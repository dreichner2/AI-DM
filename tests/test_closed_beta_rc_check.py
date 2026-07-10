from __future__ import annotations

import json
import pathlib
import sys

from scripts.closed_beta_rc_check import RcCommand, build_command_plan, run_plan


def test_closed_beta_rc_plan_includes_release_gate_commands(tmp_path):
    plan = build_command_plan(
        python_executable='python-test',
        include_browser_smoke=True,
        include_dependency_audits=True,
        tmp_dir=tmp_path,
    )
    labels = [command.label for command in plan]

    assert labels == [
        'Deploy bootstrap check-only',
        'SQLite backup/restore drill',
        'Migration chain drill',
        'Python correctness lint',
        'Secret scan',
        'Python dependency audit',
        'Request JSON parsing guard',
        'Backend tests',
        'Isolated beta smoke flow',
        'Scenario quality regressions',
        'Socket concurrency smoke',
        'Hosted cookie auth smoke',
        'Security forbidden smoke',
        'Session export/import smoke',
        'Observability bundle check',
        'Local beta SLO baseline',
        'State snapshot writer inventory',
        'Socket.IO worker model decision',
        'API type drift check',
        'Frontend npm ci evidence',
        'Frontend toolchain preflight',
        'Frontend tests',
        'Frontend build',
        'Frontend bundle budget',
        'Frontend production dependency audit',
        'Browser smoke (single-origin build)',
        'Visual smoke screenshots',
        'Visual smoke artifact review',
    ]
    bootstrap = plan[0]
    assert bootstrap.args == ('python-test', 'scripts/deploy_bootstrap.py', '--check-only')
    assert bootstrap.env is not None
    assert bootstrap.env['AIDM_AUTH_REQUIRED'] == 'true'
    assert bootstrap.env['AIDM_AUTO_CREATE_SCHEMA'] == 'false'
    assert bootstrap.env['AIDM_DATABASE_URI'].startswith(f'sqlite:///{tmp_path}')

    backup_drill = plan[1]
    assert backup_drill.args[:2] == ('python-test', 'scripts/backup_restore_drill.py')
    assert backup_drill.env is not None
    assert backup_drill.env['AIDM_DATABASE_URI'] == bootstrap.env['AIDM_DATABASE_URI']

    migration_drill = plan[2]
    assert migration_drill.args[:2] == ('python-test', 'scripts/migration_chain_drill.py')
    assert '--python' in migration_drill.args

    hosted_cookie_auth = next(command for command in plan if command.label == 'Hosted cookie auth smoke')
    assert hosted_cookie_auth.args == (
        'python-test',
        'scripts/hosted_cookie_auth_smoke.py',
        '--evidence-report',
        'tmp/release/hosted-cookie-auth-evidence.md',
    )


def test_closed_beta_rc_plan_supports_fast_local_iteration(tmp_path):
    plan = build_command_plan(
        python_executable='python-test',
        include_browser_smoke=False,
        include_dependency_audits=False,
        tmp_dir=tmp_path,
    )
    labels = [command.label for command in plan]

    assert 'Browser smoke (single-origin build)' not in labels
    assert 'Visual smoke screenshots' not in labels
    assert 'Visual smoke artifact review' not in labels
    assert 'Python dependency audit' not in labels
    assert 'Frontend npm ci evidence' not in labels
    assert 'Frontend production dependency audit' not in labels
    assert 'Backend tests' in labels
    assert 'Request JSON parsing guard' in labels
    assert 'Socket concurrency smoke' in labels
    assert 'Hosted cookie auth smoke' in labels
    assert 'Security forbidden smoke' in labels
    assert 'Session export/import smoke' in labels
    assert 'Observability bundle check' in labels
    assert 'Local beta SLO baseline' in labels
    assert 'State snapshot writer inventory' in labels
    assert 'Socket.IO worker model decision' in labels
    assert 'SQLite backup/restore drill' in labels
    assert 'Migration chain drill' in labels
    assert 'Frontend bundle budget' in labels


def test_closed_beta_rc_frontend_commands_run_from_frontend_dir(tmp_path):
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    plan = build_command_plan(
        python_executable='python-test',
        include_browser_smoke=True,
        include_dependency_audits=True,
        tmp_dir=tmp_path,
    )

    frontend_commands = {
        command.label: pathlib.Path(command.cwd).name
        for command in plan
        if (
            command.label.startswith('Frontend') and command.label != 'Frontend npm ci evidence'
            or command.label == 'Browser smoke (single-origin build)'
            or command.label == 'Visual smoke screenshots'
        )
    }

    assert set(frontend_commands.values()) == {'aidm_frontend'}

    visual_review = next(command for command in plan if command.label == 'Visual smoke artifact review')
    assert pathlib.Path(visual_review.cwd) == repo_root
    assert visual_review.args[:2] == ('python-test', 'scripts/review_visual_smoke_artifacts.py')

    npm_ci_evidence = next(command for command in plan if command.label == 'Frontend npm ci evidence')
    assert pathlib.Path(npm_ci_evidence.cwd) == repo_root
    assert npm_ci_evidence.args == ('python-test', 'scripts/render_frontend_npm_ci_evidence.py')


def test_closed_beta_rc_browser_smoke_uses_single_origin_build(tmp_path):
    plan = build_command_plan(
        python_executable='python-test',
        include_browser_smoke=True,
        include_dependency_audits=False,
        tmp_dir=tmp_path,
    )

    browser_smoke = next(
        command
        for command in plan
        if command.label == 'Browser smoke (single-origin build)'
    )

    assert browser_smoke.env is not None
    assert browser_smoke.env['AIDM_BROWSER_SMOKE_SINGLE_ORIGIN'] == 'true'


def test_closed_beta_rc_evidence_report_records_failure_and_skips(tmp_path):
    report_path = tmp_path / 'rc-evidence.md'
    exit_code = run_plan(
        [
            RcCommand('Passing gate', (sys.executable, '-c', 'pass')),
            RcCommand('Failing gate', (sys.executable, '-c', 'import sys; sys.exit(3)')),
            RcCommand('Later gate', (sys.executable, '-c', 'pass')),
        ],
        evidence_report=report_path,
        include_browser_smoke=False,
        include_dependency_audits=False,
        python_executable=sys.executable,
    )

    assert exit_code == 3
    report = report_path.read_text(encoding='utf-8')
    assert '- Status: failed' in report
    assert '- Browser smoke: skipped' in report
    assert '- Dependency audits: skipped' in report
    assert '- Worktree:' in report
    assert '| Passing gate | passed | 0 |' in report
    assert '| Failing gate | failed | 3 |' in report
    assert '| Later gate | skipped |  |  |' in report


def test_closed_beta_rc_evidence_report_supports_json(tmp_path):
    report_path = tmp_path / 'rc-evidence.json'
    exit_code = run_plan(
        [RcCommand('Passing gate', (sys.executable, '-c', 'pass'))],
        evidence_report=report_path,
        python_executable=sys.executable,
    )

    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding='utf-8'))
    assert payload['status'] == 'passed'
    assert 'git_worktree' in payload
    assert payload['git_worktree']['state'] in {'clean', 'dirty', 'unknown'}
    assert payload['commands'][0]['label'] == 'Passing gate'
    assert payload['commands'][0]['status'] == 'passed'
    assert payload['commands'][0]['returncode'] == 0
