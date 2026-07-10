#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_FRONTEND_DIR = REPO_ROOT / 'aidm_frontend'
DEFAULT_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'frontend-npm-ci-evidence.md'
DEFAULT_JSON_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'frontend-npm-ci-evidence.json'
EXPECTED_NODE_VERSION = '24.18.0'
EXPECTED_NPM_VERSION = '12.0.0'


def _resolve_repo_path(path: pathlib.Path) -> pathlib.Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _relative_or_absolute(path: pathlib.Path | str) -> str:
    candidate = pathlib.Path(path)
    try:
        return str(candidate.relative_to(REPO_ROOT))
    except ValueError:
        return str(candidate)


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _tail(text: str, limit: int = 6000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def build_evidence(
    *,
    frontend_dir: pathlib.Path,
    command: list[str],
    generated_at: str,
    run: bool,
) -> dict[str, Any]:
    resolved_frontend = _resolve_repo_path(frontend_dir)
    package_json = resolved_frontend / 'package.json'
    package_lock = resolved_frontend / 'package-lock.json'
    base: dict[str, Any] = {
        'generated_at': generated_at,
        'frontend_dir': str(resolved_frontend),
        'command': command,
        'command_label': shlex.join(command),
        'package_json': str(package_json),
        'package_json_present': package_json.exists(),
        'package_lock': str(package_lock),
        'package_lock_present': package_lock.exists(),
        'status': 'missing-input',
        'returncode': None,
        'duration_seconds': None,
        'stdout_tail': '',
        'stderr_tail': '',
        'node_version': None,
        'npm_version': None,
        'expected_node_version': EXPECTED_NODE_VERSION,
        'expected_npm_version': EXPECTED_NPM_VERSION,
    }
    if not resolved_frontend.exists():
        base['error'] = 'frontend directory does not exist'
        return base
    if not package_json.exists() or not package_lock.exists():
        missing = []
        if not package_json.exists():
            missing.append('package.json')
        if not package_lock.exists():
            missing.append('package-lock.json')
        base['error'] = f"missing required frontend file(s): {', '.join(missing)}"
        return base
    if not run:
        base['status'] = 'planned'
        return base

    if command and pathlib.Path(command[0]).name == 'npm':
        try:
            node_version = subprocess.run(
                ['node', '--version'],
                cwd=resolved_frontend,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip().removeprefix('v')
            npm_version = subprocess.run(
                ['npm', '--version'],
                cwd=resolved_frontend,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            base.update({'status': 'failed', 'error': f'could not verify Node.js/npm: {exc}'})
            return base
        base.update({'node_version': node_version, 'npm_version': npm_version})
        if node_version != EXPECTED_NODE_VERSION or npm_version != EXPECTED_NPM_VERSION:
            base.update(
                {
                    'status': 'failed',
                    'error': (
                        f'expected Node.js {EXPECTED_NODE_VERSION} and npm {EXPECTED_NPM_VERSION}; '
                        f'found Node.js {node_version} and npm {npm_version}'
                    ),
                }
            )
            return base

    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=resolved_frontend,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    duration = round(time.monotonic() - started, 2)
    base.update(
        {
            'status': 'passed' if completed.returncode == 0 else 'failed',
            'returncode': completed.returncode,
            'duration_seconds': duration,
            'stdout_tail': _tail(completed.stdout),
            'stderr_tail': _tail(completed.stderr),
        }
    )
    return base


def render_markdown(evidence: dict[str, Any]) -> str:
    stdout = evidence.get('stdout_tail') or ''
    stderr = evidence.get('stderr_tail') or ''
    lines = [
        '# Frontend npm ci Evidence',
        '',
        f"- Generated: {evidence.get('generated_at')}",
        f"- Status: {evidence.get('status')}",
        f"- Frontend dir: `{_relative_or_absolute(evidence.get('frontend_dir') or '')}`",
        f"- Command: `{evidence.get('command_label') or ''}`",
        f"- Return code: {evidence.get('returncode') if evidence.get('returncode') is not None else 'not run'}",
        f"- Duration seconds: {evidence.get('duration_seconds') if evidence.get('duration_seconds') is not None else 'not run'}",
        f"- package.json present: {evidence.get('package_json_present')}",
        f"- package-lock.json present: {evidence.get('package_lock_present')}",
        f"- Node.js: {evidence.get('node_version') or 'not checked'} (expected {evidence.get('expected_node_version')})",
        f"- npm: {evidence.get('npm_version') or 'not checked'} (expected {evidence.get('expected_npm_version')})",
        '',
        '## stdout tail',
        '',
        '```text',
        stdout,
        '```',
        '',
        '## stderr tail',
        '',
        '```text',
        stderr,
        '```',
        '',
    ]
    if evidence.get('error'):
        lines[9:9] = [f"- Error: {evidence.get('error')}"]
    return '\n'.join(lines)


def write_evidence(evidence: dict[str, Any], *, output: pathlib.Path, json_output: pathlib.Path | None) -> None:
    output_path = _resolve_repo_path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(evidence), encoding='utf-8')
    if json_output is not None:
        json_path = _resolve_repo_path(json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run or record frontend npm ci lockfile-install evidence.')
    parser.add_argument('--frontend-dir', type=pathlib.Path, default=DEFAULT_FRONTEND_DIR)
    parser.add_argument('--output', type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--json-output', type=pathlib.Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument('--no-run', action='store_true', help='Only inspect required files; do not run npm ci.')
    parser.add_argument('--generated-at', default='', help=argparse.SUPPRESS)
    parser.add_argument('--command', nargs=argparse.REMAINDER, default=None, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generated_at = args.generated_at or _iso_now()
    evidence = build_evidence(
        frontend_dir=args.frontend_dir,
        command=list(args.command or ['npm', 'ci']),
        generated_at=generated_at,
        run=not args.no_run,
    )
    write_evidence(evidence, output=args.output, json_output=args.json_output)
    print(f'[frontend-npm-ci-evidence] Wrote {_relative_or_absolute(_resolve_repo_path(args.output))}.')
    if args.json_output is not None:
        print(f'[frontend-npm-ci-evidence] Wrote {_relative_or_absolute(_resolve_repo_path(args.json_output))}.')
    return 0 if evidence.get('status') in {'passed', 'planned'} else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
