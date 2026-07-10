#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tarfile
from datetime import UTC, datetime
from typing import Any

try:
    from scripts.render_rc_issue_evidence import (
        FORBIDDEN_ARCHIVE_PARTS,
        FORBIDDEN_ARCHIVE_PATHS,
        FORBIDDEN_ARCHIVE_SUFFIXES,
        inspect_source_archive,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised when run as a script path
    from render_rc_issue_evidence import (  # type: ignore[no-redef]
        FORBIDDEN_ARCHIVE_PARTS,
        FORBIDDEN_ARCHIVE_PATHS,
        FORBIDDEN_ARCHIVE_SUFFIXES,
        inspect_source_archive,
    )


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CLEANUP_SCRIPT = REPO_ROOT / 'scripts' / 'cleanup_artifacts.sh'
DEFAULT_MAKEFILE = REPO_ROOT / 'Makefile'
DEFAULT_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'packaging-cleanup-evidence.md'
DEFAULT_JSON_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'packaging-cleanup-evidence.json'

CLEAN_SCRIPT_NEEDLES: tuple[tuple[str, str], ...] = (
    ('git metadata preserved', '$ROOT_DIR/.git'),
    ('pytest cache', '$ROOT_DIR/.pytest_cache'),
    ('runtime tmp directory', '$ROOT_DIR/tmp'),
    ('release evidence preserved', '! -name "release"'),
    ('backend memory artifact', '$ROOT_DIR/aidm_server/:memory:'),
    ('frontend vite cache', '$ROOT_DIR/aidm_frontend/.vite'),
    ('frontend dist build output', '$ROOT_DIR/aidm_frontend/dist'),
    ('python bytecode caches', '__pycache__'),
    ('macOS Finder metadata', '.DS_Store'),
)

CLEAN_DEPS_NEEDLES: tuple[tuple[str, str], ...] = (
    ('clean-deps depends on clean', 'clean-deps: clean'),
    ('python virtualenv removal', '.venv'),
    ('frontend node_modules removal', '$(FRONTEND_DIR)/node_modules'),
)
ALLOWED_ARCHIVE_TEMPLATE_FILES = ('.env.local.example', '.env.production.example')


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


def _latest_source_archive() -> pathlib.Path | None:
    release_dir = REPO_ROOT / 'tmp' / 'release'
    archives = sorted(release_dir.glob('aidm-source-*.tar.gz'), key=lambda path: path.stat().st_mtime)
    return archives[-1] if archives else None


def _check_needles(text: str, needles: tuple[tuple[str, str], ...]) -> list[dict[str, Any]]:
    return [
        {
            'label': label,
            'needle': needle,
            'present': needle in text,
        }
        for label, needle in needles
    ]


def _archive_policy(source_archive: pathlib.Path | None) -> dict[str, Any]:
    policy: dict[str, Any] = {
        'forbidden_parts': sorted(FORBIDDEN_ARCHIVE_PARTS),
        'forbidden_paths': sorted('/'.join(path) for path in FORBIDDEN_ARCHIVE_PATHS),
        'forbidden_suffixes': list(FORBIDDEN_ARCHIVE_SUFFIXES),
        'allowed_template_files': list(ALLOWED_ARCHIVE_TEMPLATE_FILES),
        'allowed_template_members_found': [],
        'member_count': 0,
        'error': '',
    }
    if source_archive is None or not source_archive.exists():
        return policy
    try:
        with tarfile.open(source_archive, mode='r:*') as archive:
            for member in archive.getmembers():
                policy['member_count'] += 1
                name = member.name.strip('/')
                if pathlib.PurePosixPath(name).name in ALLOWED_ARCHIVE_TEMPLATE_FILES:
                    policy['allowed_template_members_found'].append(name)
    except (tarfile.TarError, EOFError) as exc:
        policy['error'] = str(exc)
    return policy


def build_evidence(
    *,
    cleanup_script: pathlib.Path,
    makefile: pathlib.Path,
    source_archive: pathlib.Path | None,
    generated_at: str,
) -> dict[str, Any]:
    cleanup_path = _resolve_repo_path(cleanup_script)
    makefile_path = _resolve_repo_path(makefile)
    archive_path = _resolve_repo_path(source_archive) if source_archive is not None else _latest_source_archive()

    cleanup_text = cleanup_path.read_text(encoding='utf-8') if cleanup_path.exists() else ''
    makefile_text = makefile_path.read_text(encoding='utf-8') if makefile_path.exists() else ''
    clean_checks = _check_needles(cleanup_text, CLEAN_SCRIPT_NEEDLES)
    clean_deps_checks = _check_needles(makefile_text, CLEAN_DEPS_NEEDLES)
    archive_result = inspect_source_archive(archive_path)
    archive_policy = _archive_policy(archive_path)
    failures: list[str] = []
    if not cleanup_path.exists():
        failures.append(f'missing cleanup script: {_relative_or_absolute(cleanup_path)}')
    if not makefile_path.exists():
        failures.append(f'missing Makefile: {_relative_or_absolute(makefile_path)}')
    failures.extend(f"missing clean coverage: {check['label']}" for check in clean_checks if not check['present'])
    failures.extend(f"missing clean-deps coverage: {check['label']}" for check in clean_deps_checks if not check['present'])
    if archive_result.get('status') != 'passed':
        failures.append(f"source archive status is {archive_result.get('status')}")
    if archive_result.get('forbidden'):
        failures.append('source archive contains forbidden paths')
    if archive_result.get('large_untracked'):
        failures.append('source archive contains large files not tracked by Git LFS')

    return {
        'generated_at': generated_at,
        'status': 'passed' if not failures else 'failed',
        'cleanup_script': str(cleanup_path),
        'makefile': str(makefile_path),
        'clean_checks': clean_checks,
        'clean_deps_checks': clean_deps_checks,
        'source_archive': archive_result,
        'archive_policy': archive_policy,
        'failures': failures,
    }


def render_markdown(evidence: dict[str, Any]) -> str:
    clean_rows = ['| Check | Needle | Present |', '| --- | --- | --- |']
    for check in evidence.get('clean_checks') or []:
        clean_rows.append(f"| {check.get('label')} | `{check.get('needle')}` | {check.get('present')} |")

    clean_deps_rows = ['| Check | Needle | Present |', '| --- | --- | --- |']
    for check in evidence.get('clean_deps_checks') or []:
        clean_deps_rows.append(f"| {check.get('label')} | `{check.get('needle')}` | {check.get('present')} |")

    archive = evidence.get('source_archive') or {}
    archive_policy = evidence.get('archive_policy') or {}
    large_members = archive.get('large_members') or []
    failure_rows = ['| Failure |', '| --- |']
    for failure in evidence.get('failures') or []:
        failure_rows.append(f'| {failure} |')
    if len(failure_rows) == 2:
        failure_rows.append('| None |')

    policy_rows = ['| Category | Values |', '| --- | --- |']
    policy_rows.extend(
        [
            f"| Forbidden path parts | `{', '.join(archive_policy.get('forbidden_parts') or [])}` |",
            f"| Forbidden path pairs | `{', '.join(archive_policy.get('forbidden_paths') or []) or 'none'}` |",
            f"| Forbidden suffixes | `{', '.join(archive_policy.get('forbidden_suffixes') or [])}` |",
            f"| Allowed env templates | `{', '.join(archive_policy.get('allowed_template_files') or [])}` |",
            (
                f"| Allowed templates found | "
                f"`{', '.join(archive_policy.get('allowed_template_members_found') or []) or 'none'}` |"
            ),
            f"| Archive members inspected | {archive_policy.get('member_count') or 0} |",
        ]
    )
    if archive_policy.get('error'):
        policy_rows.append(f"| Policy inspection error | `{archive_policy.get('error')}` |")

    large_rows = ['| Path | Bytes | LFS tracked |', '| --- | ---: | --- |']
    for member in large_members:
        large_rows.append(
            f"| `{member.get('path')}` | {member.get('bytes') or 0} | {member.get('lfs_tracked')} |"
        )
    if len(large_rows) == 2:
        large_rows.append('| None | 0 | n/a |')

    return '\n'.join(
        [
            '# Packaging Cleanup Evidence',
            '',
            f"- Generated: {evidence.get('generated_at')}",
            f"- Status: {evidence.get('status')}",
            f"- Cleanup script: `{_relative_or_absolute(evidence.get('cleanup_script') or '')}`",
            f"- Makefile: `{_relative_or_absolute(evidence.get('makefile') or '')}`",
            f"- Source archive: `{_relative_or_absolute(archive.get('path') or '')}`",
            f"- Source archive status: {archive.get('status') or 'missing'}",
            f"- Source archive forbidden paths: {len(archive.get('forbidden') or [])}",
            f"- Source archive large files: {archive.get('large_member_count') or 0}",
            f"- Source archive large files not LFS-tracked: {len(archive.get('large_untracked') or [])}",
            '',
            '## make clean Coverage',
            '',
            *clean_rows,
            '',
            '## make clean-deps Coverage',
            '',
            *clean_deps_rows,
            '',
            '## Source Archive Exclusion Policy',
            '',
            *policy_rows,
            '',
            '## Large Archive Members',
            '',
            f"- Threshold bytes: {archive.get('large_member_threshold_bytes') or 0}",
            f"- Git LFS patterns: `{', '.join(archive.get('lfs_patterns') or []) or 'none'}`",
            '',
            *large_rows,
            '',
            '## Failures',
            '',
            *failure_rows,
            '',
        ]
    )


def write_evidence(evidence: dict[str, Any], *, output: pathlib.Path, json_output: pathlib.Path | None) -> None:
    output_path = _resolve_repo_path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(evidence), encoding='utf-8')
    if json_output is not None:
        json_path = _resolve_repo_path(json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Render non-destructive packaging cleanup evidence.')
    parser.add_argument('--cleanup-script', type=pathlib.Path, default=DEFAULT_CLEANUP_SCRIPT)
    parser.add_argument('--makefile', type=pathlib.Path, default=DEFAULT_MAKEFILE)
    parser.add_argument('--source-archive', type=pathlib.Path, default=None)
    parser.add_argument('--output', type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--json-output', type=pathlib.Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument('--generated-at', default='', help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generated_at = args.generated_at or _iso_now()
    evidence = build_evidence(
        cleanup_script=args.cleanup_script,
        makefile=args.makefile,
        source_archive=args.source_archive,
        generated_at=generated_at,
    )
    write_evidence(evidence, output=args.output, json_output=args.json_output)
    print(f'[packaging-cleanup-evidence] Wrote {_relative_or_absolute(_resolve_repo_path(args.output))}.')
    if args.json_output is not None:
        print(f'[packaging-cleanup-evidence] Wrote {_relative_or_absolute(_resolve_repo_path(args.json_output))}.')
    return 0 if evidence.get('status') == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
