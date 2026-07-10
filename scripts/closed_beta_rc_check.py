from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND_DIR = REPO_ROOT / 'aidm_frontend'


@dataclass(frozen=True)
class RcCommand:
    label: str
    args: tuple[str, ...]
    cwd: pathlib.Path = REPO_ROOT
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class RcCommandResult:
    label: str
    args: tuple[str, ...]
    cwd: pathlib.Path
    status: str
    returncode: int | None
    duration_seconds: float | None


def _bootstrap_env(tmp_dir: pathlib.Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            'PYTHON_DOTENV_DISABLED': '1',
            'AIDM_DATABASE_URI': f"sqlite:///{tmp_dir / 'closed-beta-rc-bootstrap.sqlite'}",
            'AIDM_AUTO_CREATE_SCHEMA': 'false',
            'AIDM_ENV': 'test',
            'AIDM_DEBUG': 'false',
            'AIDM_AUTH_REQUIRED': 'true',
            'AIDM_API_AUTH_TOKENS': 'closed-beta-rc-token',
            'AIDM_CORS_ALLOWLIST': 'http://localhost',
            'AIDM_SOCKET_CORS_ALLOWLIST': 'http://localhost',
            'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
            'AIDM_TELEMETRY_ENABLED': 'false',
            'AIDM_RATE_LIMIT_WINDOW_SECONDS': '30',
            'AIDM_RATE_LIMIT_MAX_API_REQUESTS': '120',
            'AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES': '40',
        }
    )
    return env


def _single_origin_browser_smoke_env() -> dict[str, str]:
    env = os.environ.copy()
    env['AIDM_BROWSER_SMOKE_SINGLE_ORIGIN'] = 'true'
    return env


def build_command_plan(
    *,
    python_executable: str,
    include_browser_smoke: bool,
    include_dependency_audits: bool,
    tmp_dir: pathlib.Path,
) -> list[RcCommand]:
    commands = [
        RcCommand(
            'Deploy bootstrap check-only',
            (python_executable, 'scripts/deploy_bootstrap.py', '--check-only'),
            env=_bootstrap_env(tmp_dir),
        ),
        RcCommand(
            'SQLite backup/restore drill',
            (
                python_executable,
                'scripts/backup_restore_drill.py',
                '--output-dir',
                str(tmp_dir / 'backup-restore-drill'),
            ),
            env=_bootstrap_env(tmp_dir),
        ),
        RcCommand(
            'Migration chain drill',
            (
                python_executable,
                'scripts/migration_chain_drill.py',
                '--output-dir',
                str(tmp_dir / 'migration-chain-drill'),
                '--python',
                python_executable,
            ),
        ),
        RcCommand(
            'Python correctness lint',
            (python_executable, '-m', 'ruff', 'check', '--select', 'E9,F63,F7,F82', 'aidm_server', 'tests', 'scripts'),
        ),
        RcCommand('Secret scan', (python_executable, 'scripts/scan_secrets.py')),
        RcCommand('Request JSON parsing guard', (python_executable, 'scripts/check_request_json_parsing.py')),
        RcCommand('Backend tests', (python_executable, '-m', 'pytest')),
        RcCommand('Isolated beta smoke flow', (python_executable, 'scripts/smoke_beta_flow.py')),
        RcCommand('Scenario quality regressions', (python_executable, 'scripts/scenario_regression.py')),
        RcCommand('Socket concurrency smoke', (python_executable, 'scripts/socket_concurrency_smoke.py')),
        RcCommand(
            'Hosted cookie auth smoke',
            (
                python_executable,
                'scripts/hosted_cookie_auth_smoke.py',
                '--evidence-report',
                'tmp/release/hosted-cookie-auth-evidence.md',
            ),
        ),
        RcCommand(
            'Security forbidden smoke',
            (
                python_executable,
                'scripts/security_forbidden_smoke.py',
                '--evidence-report',
                'tmp/release/security-forbidden-evidence.md',
            ),
        ),
        RcCommand(
            'Session export/import smoke',
            (
                python_executable,
                'scripts/session_export_import_smoke.py',
                '--evidence-report',
                'tmp/release/export-import-evidence.md',
            ),
        ),
        RcCommand('Observability bundle check', (python_executable, 'scripts/check_observability_bundle.py')),
        RcCommand('Local beta SLO baseline', (python_executable, 'scripts/render_local_beta_slo_baseline.py')),
        RcCommand('State snapshot writer inventory', (python_executable, 'scripts/check_state_snapshot_writers.py')),
        RcCommand('Socket.IO worker model decision', (python_executable, 'scripts/check_socketio_worker_model_decision.py')),
        RcCommand('API type drift check', (python_executable, 'scripts/generate_api_types.py', '--check')),
        RcCommand(
            'Frontend toolchain preflight',
            (
                'node',
                '-e',
                'const {execFileSync}=require("node:child_process");'
                'const npm=execFileSync("npm",["--version"],{encoding:"utf8"}).trim();'
                'if(process.versions.node!=="24.18.0"||npm!=="12.0.0")process.exit(1);',
            ),
            cwd=FRONTEND_DIR,
        ),
        RcCommand('Frontend tests', ('npm', 'test'), cwd=FRONTEND_DIR),
        RcCommand('Frontend build', ('npm', 'run', 'build'), cwd=FRONTEND_DIR),
        RcCommand('Frontend bundle budget', ('npm', 'run', 'bundle:budget'), cwd=FRONTEND_DIR),
    ]
    if include_dependency_audits:
        secret_scan_index = next(
            index
            for index, command in enumerate(commands)
            if command.label == 'Secret scan'
        )
        commands.insert(
            secret_scan_index + 1,
            RcCommand(
                'Python dependency audit',
                (python_executable, '-m', 'pip_audit', '-r', 'requirements.runtime.lock.txt'),
            ),
        )
        api_type_index = next(
            index
            for index, command in enumerate(commands)
            if command.label == 'API type drift check'
        )
        commands.insert(
            api_type_index + 1,
            RcCommand('Frontend npm ci evidence', (python_executable, 'scripts/render_frontend_npm_ci_evidence.py')),
        )
        commands.append(RcCommand('Frontend production dependency audit', ('npm', 'audit', '--omit=dev'), cwd=FRONTEND_DIR))
    if include_browser_smoke:
        commands.append(
            RcCommand(
                'Browser smoke (single-origin build)',
                ('npm', 'run', 'smoke:browser'),
                cwd=FRONTEND_DIR,
                env=_single_origin_browser_smoke_env(),
            )
        )
        commands.append(RcCommand('Visual smoke screenshots', ('npm', 'run', 'smoke:visual'), cwd=FRONTEND_DIR))
        commands.append(
            RcCommand(
                'Visual smoke artifact review',
                (
                    python_executable,
                    'scripts/review_visual_smoke_artifacts.py',
                    '--evidence-report',
                    'tmp/release/visual-smoke-review.md',
                    '--json-output',
                    'tmp/release/visual-smoke-review.json',
                ),
            )
        )
    return commands


def _relative_cwd(path: pathlib.Path) -> pathlib.Path:
    try:
        return path.relative_to(REPO_ROOT)
    except ValueError:
        return path


def _command_text(command: RcCommand | RcCommandResult) -> str:
    relative_cwd = _relative_cwd(command.cwd)
    return f'(cd {relative_cwd} && {shlex.join(command.args)})'


def _git_commit() -> str:
    result = subprocess.run(
        ('git', 'rev-parse', '--short', 'HEAD'),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 'unknown'
    return result.stdout.strip() or 'unknown'


def _git_worktree_summary() -> dict[str, int | str | bool]:
    result = subprocess.run(
        ('git', 'status', '--porcelain=v1', '--untracked-files=all'),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {
            'state': 'unknown',
            'dirty': True,
            'changed_path_count': 0,
            'tracked_change_count': 0,
            'untracked_path_count': 0,
        }
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    untracked = [line for line in lines if line.startswith('?? ')]
    return {
        'state': 'dirty' if lines else 'clean',
        'dirty': bool(lines),
        'changed_path_count': len(lines),
        'tracked_change_count': len(lines) - len(untracked),
        'untracked_path_count': len(untracked),
    }


def _git_worktree_label(summary: dict[str, int | str | bool]) -> str:
    state = str(summary.get('state') or 'unknown')
    if state == 'clean':
        return 'clean'
    if state == 'unknown':
        return 'unknown'
    changed_path_count = int(summary.get('changed_path_count') or 0)
    tracked_change_count = int(summary.get('tracked_change_count') or 0)
    untracked_path_count = int(summary.get('untracked_path_count') or 0)
    return (
        f'dirty ({changed_path_count} changed/untracked paths; '
        f'{tracked_change_count} tracked, {untracked_path_count} untracked)'
    )


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _report_path(path: pathlib.Path) -> pathlib.Path:
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def write_evidence_report(
    path: pathlib.Path,
    *,
    results: list[RcCommandResult],
    started_at: str,
    finished_at: str,
    final_status: str,
    include_browser_smoke: bool,
    include_dependency_audits: bool,
    python_executable: str,
) -> None:
    output_path = _report_path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    commit = _git_commit()
    git_worktree = _git_worktree_summary()
    payload = {
        'status': final_status,
        'started_at': started_at,
        'finished_at': finished_at,
        'commit': commit,
        'git_worktree': git_worktree,
        'repo_root': str(REPO_ROOT),
        'python': python_executable,
        'include_browser_smoke': include_browser_smoke,
        'include_dependency_audits': include_dependency_audits,
        'commands': [
            {
                'label': result.label,
                'status': result.status,
                'returncode': result.returncode,
                'duration_seconds': result.duration_seconds,
                'cwd': str(_relative_cwd(result.cwd)),
                'args': list(result.args),
                'command': _command_text(result),
            }
            for result in results
        ],
    }
    if output_path.suffix.lower() == '.json':
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        return

    rows = [
        '| Gate | Status | Exit | Seconds | Command |',
        '| --- | --- | ---: | ---: | --- |',
    ]
    for result in results:
        exit_code = '' if result.returncode is None else str(result.returncode)
        seconds = '' if result.duration_seconds is None else f'{result.duration_seconds:.2f}'
        rows.append(
            f'| {result.label} | {result.status} | {exit_code} | {seconds} | `{_command_text(result)}` |'
        )
    output_path.write_text(
        '\n'.join(
            [
                '# Closed Beta RC Evidence',
                '',
                f'- Status: {final_status}',
                f'- Started: {started_at}',
                f'- Finished: {finished_at}',
                f'- Commit: {commit}',
                f'- Worktree: {_git_worktree_label(git_worktree)}',
                f'- Repo: `{REPO_ROOT}`',
                f'- Python: `{python_executable}`',
                f'- Browser smoke: {"included" if include_browser_smoke else "skipped"}',
                f'- Dependency audits: {"included" if include_dependency_audits else "skipped"}',
                '',
                '## Gates',
                '',
                *rows,
                '',
            ]
        ),
        encoding='utf-8',
    )


def run_command(command: RcCommand) -> RcCommandResult:
    print(f'[closed-beta-rc] {command.label}...')
    start = time.monotonic()
    result = subprocess.run(
        command.args,
        cwd=str(command.cwd),
        env=command.env,
        text=True,
    )
    duration_seconds = time.monotonic() - start
    if result.returncode != 0:
        print(f'[closed-beta-rc][failed] {command.label} exited with {result.returncode}.')
    return RcCommandResult(
        label=command.label,
        args=command.args,
        cwd=command.cwd,
        status='passed' if result.returncode == 0 else 'failed',
        returncode=result.returncode,
        duration_seconds=duration_seconds,
    )


def run_plan(
    commands: Iterable[RcCommand],
    *,
    evidence_report: pathlib.Path | None = None,
    include_browser_smoke: bool = True,
    include_dependency_audits: bool = True,
    python_executable: str = sys.executable,
) -> int:
    command_list = list(commands)
    results: list[RcCommandResult] = []
    started_at = _iso_now()
    returncode = 0
    failed = False
    for command in command_list:
        if failed:
            results.append(
                RcCommandResult(
                    label=command.label,
                    args=command.args,
                    cwd=command.cwd,
                    status='skipped',
                    returncode=None,
                    duration_seconds=None,
                )
            )
            continue
        result = run_command(command)
        results.append(result)
        if result.returncode != 0:
            returncode = result.returncode if result.returncode is not None else 1
            failed = True
    final_status = 'failed' if returncode != 0 else 'passed'
    if returncode == 0:
        print('[closed-beta-rc] All checks passed.')
    finished_at = _iso_now()
    if evidence_report is not None:
        write_evidence_report(
            evidence_report,
            results=results,
            started_at=started_at,
            finished_at=finished_at,
            final_status=final_status,
            include_browser_smoke=include_browser_smoke,
            include_dependency_audits=include_dependency_audits,
            python_executable=python_executable,
        )
        print(f'[closed-beta-rc] Evidence report written to {_report_path(evidence_report)}.')
    return returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run the closed-beta release-candidate gate.')
    parser.add_argument(
        '--skip-browser-smoke',
        action='store_true',
        help='Skip browser-dependent smoke flows. Use only when a browser is unavailable.',
    )
    parser.add_argument(
        '--skip-dependency-audits',
        action='store_true',
        help='Skip pip-audit and npm audit. Use only for offline local iteration.',
    )
    parser.add_argument(
        '--python',
        default=sys.executable,
        help='Python executable to use for child Python commands.',
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List planned checks without running them.',
    )
    parser.add_argument(
        '--evidence-report',
        nargs='?',
        const='tmp/release/rc-evidence.md',
        default=None,
        help='Write a Markdown or JSON evidence report. Defaults to tmp/release/rc-evidence.md when no path is supplied.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='aidm-closed-beta-rc-') as tmp:
        commands = build_command_plan(
            python_executable=args.python,
            include_browser_smoke=not args.skip_browser_smoke,
            include_dependency_audits=not args.skip_dependency_audits,
            tmp_dir=pathlib.Path(tmp),
        )
        if args.list:
            for command in commands:
                print(f'{command.label}: {_command_text(command)}')
            return 0
        evidence_report = pathlib.Path(args.evidence_report) if args.evidence_report else None
        return run_plan(
            commands,
            evidence_report=evidence_report,
            include_browser_smoke=not args.skip_browser_smoke,
            include_dependency_audits=not args.skip_dependency_audits,
            python_executable=args.python,
        )


if __name__ == '__main__':
    raise SystemExit(main())
