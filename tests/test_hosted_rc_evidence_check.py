from __future__ import annotations

import json

import pytest

from scripts import hosted_rc_evidence_check
from scripts.hosted_rc_evidence_check import (
    HostedCheck,
    HostedCheckResult,
    _redacted_command_args,
    build_command_plan,
    build_parser,
    finalize_status,
    main,
    run_check,
    run_plan,
)


def _parse_args(*args: str):
    return build_parser().parse_args(list(args))


def test_parser_rejects_unsupported_multi_worker_model():
    with pytest.raises(SystemExit):
        _parse_args('--socketio-worker-model', 'message_queue')


def test_redacted_command_args_hide_sensitive_flag_values():
    assert _redacted_command_args(
        [
            'python',
            'script.py',
            '--auth-token',
            'operator-token',
            '--account-token=player-token',
            '--workspace-token',
            'workspace-secret',
            '--target-url',
            'https://aidm.closedbeta.dev',
        ]
    ) == (
        'python',
        'script.py',
        '--auth-token',
        '<redacted>',
        '--account-token=<redacted>',
        '--workspace-token',
        '<redacted>',
        '--target-url',
        'https://aidm.closedbeta.dev',
    )


def test_command_plan_builds_hosted_checks_with_expected_artifacts():
    args = _parse_args(
        '--python',
        'python-test',
        '--target-url',
        'https://aidm.closedbeta.dev',
        '--env-file',
        'target.env',
        '--auth-token',
        'operator-token',
        '--socketio-origin',
        'https://play.closedbeta.dev',
        '--workspace-id',
        'workspace-1',
        '--non-admin-token',
        'player-token',
        '--campaign-id',
        '7',
        '--session-id',
        '9',
        '--player-id',
        '11',
        '--commit-sha',
        'abc1234',
        '--invite-more-testers',
        'yes',
    )

    plan = build_command_plan(args)

    assert [check.label for check in plan] == [
        'Hosted deployment readiness',
        'Hosted cookie auth smoke',
        'Hosted non-admin forbidden smoke',
        'Hosted session export/import smoke',
        'Hosted beta SLO baseline',
    ]
    readiness = plan[0]
    assert readiness.required == ()
    assert readiness.args[:2] == ('python-test', 'scripts/deployment_readiness_check.py')
    assert '--target-url' in readiness.args
    assert readiness.args[readiness.args.index('--socketio-origin') + 1] == 'https://play.closedbeta.dev'
    assert 'tmp/release/deployment-readiness-evidence.md' in readiness.args

    forbidden = next(check for check in plan if check.label == 'Hosted non-admin forbidden smoke')
    assert forbidden.required == ()
    assert '--account-token' in forbidden.args
    assert 'player-token' in forbidden.args

    export_import = next(check for check in plan if check.label == 'Hosted session export/import smoke')
    assert '--player-id' in export_import.args
    assert '11' in export_import.args

    beta_slo = next(check for check in plan if check.label == 'Hosted beta SLO baseline')
    assert '--output' in beta_slo.args
    assert beta_slo.args[beta_slo.args.index('--commit-sha') + 1] == 'abc1234'
    assert beta_slo.args[beta_slo.args.index('--invite-more-testers') + 1] == 'yes'
    assert 'tmp/release/beta-slo-baseline.md' in beta_slo.args
    assert '--evidence-report' not in beta_slo.args
    assert 'tmp/release/deployment-readiness-evidence.md' not in beta_slo.args


def test_missing_inputs_are_reported_without_running_commands():
    args = _parse_args(
        '--target-url',
        'https://aidm.closedbeta.dev',
        '--skip-cookie-auth',
        '--skip-deployment-readiness',
        '--skip-export-import',
        '--skip-beta-slo',
    )
    plan = build_command_plan(args)

    status, results = run_plan(plan, dry_run=False)

    assert status == 'missing-input'
    assert len(results) == 1
    assert results[0].label == 'Hosted non-admin forbidden smoke'
    assert results[0].status == 'missing-input'
    assert '--non-admin-token' in results[0].missing_inputs
    assert '--workspace-id' in results[0].missing_inputs
    assert '--campaign-id' in results[0].missing_inputs
    assert '--session-id' in results[0].missing_inputs


def test_run_check_rejects_passed_command_with_wrong_evidence_target(monkeypatch, tmp_path):
    evidence_path = tmp_path / 'deployment-readiness-evidence.md'
    evidence_path.write_text(
        '\n'.join(
            [
                '# Deployment Readiness',
                '',
                '- Status: passed',
                '- Target URL: `https://stale.closedbeta.dev`',
                '',
            ]
        ),
        encoding='utf-8',
    )

    class Result:
        returncode = 0

    monkeypatch.setattr(hosted_rc_evidence_check.subprocess, 'run', lambda *args, **kwargs: Result())

    result = run_check(
        HostedCheck(
            label='Hosted deployment readiness',
            args=('python-test', 'scripts/deployment_readiness_check.py'),
            evidence_path=evidence_path,
            expected_target_url='https://aidm.closedbeta.dev',
        ),
        dry_run=False,
    )

    assert result.status == 'invalid-evidence'
    assert result.evidence_target_url == 'https://stale.closedbeta.dev'
    assert result.validation_errors == (
        'evidence target URL https://stale.closedbeta.dev does not match requested target URL https://aidm.closedbeta.dev',
    )


def test_run_check_rejects_passed_command_without_evidence_report(monkeypatch, tmp_path):
    class Result:
        returncode = 0

    monkeypatch.setattr(hosted_rc_evidence_check.subprocess, 'run', lambda *args, **kwargs: Result())

    result = run_check(
        HostedCheck(
            label='Hosted cookie auth smoke',
            args=('python-test', 'scripts/hosted_cookie_auth_smoke.py'),
            evidence_path=tmp_path / 'missing-evidence.md',
            expected_target_url='https://aidm.closedbeta.dev',
        ),
        dry_run=False,
    )

    assert result.status == 'invalid-evidence'
    assert result.evidence_target_url == ''
    assert result.validation_errors == (f'evidence report was not written: {tmp_path / "missing-evidence.md"}',)


def test_finalize_status_requires_manual_evidence_after_automated_pass():
    manual_items = [
        {'label': 'Hosted database backup/restore proof', 'status': 'provided', 'evidence': 'restore-log', 'error': ''},
        {'label': 'Hosted Socket.IO worker process proof', 'status': 'required', 'evidence': '', 'error': ''},
    ]

    assert (
        finalize_status(automated_status='passed', manual_items=manual_items, dry_run=False)
        == 'manual-evidence-required'
    )
    assert finalize_status(automated_status='passed', manual_items=manual_items, dry_run=True) == 'planned'
    assert (
        finalize_status(
            automated_status='passed',
            manual_items=[{**item, 'status': 'provided', 'evidence': 'proof', 'error': ''} for item in manual_items],
            dry_run=False,
        )
        == 'passed'
    )


def test_finalize_status_rejects_invalid_manual_evidence():
    manual_items = [
        {
            'label': 'Hosted database backup/restore proof',
            'status': 'invalid',
            'evidence': 'https://example.test/backup',
            'error': 'manual evidence must not use example references',
        },
        {'label': 'Hosted Socket.IO worker process proof', 'status': 'provided', 'evidence': 'worker-proof', 'error': ''},
    ]

    assert finalize_status(automated_status='passed', manual_items=manual_items, dry_run=False) == 'invalid'


def test_main_dry_run_writes_markdown_and_json(tmp_path):
    output_path = tmp_path / 'hosted-rc-evidence.md'
    json_path = tmp_path / 'hosted-rc-evidence.json'
    values_path = tmp_path / 'external-proof-values.hosted-rc.json'

    exit_code = main(
        [
            '--python',
            'python-test',
            '--dry-run',
            '--target-url',
            'https://aidm.closedbeta.dev',
            '--auth-token',
            'operator-token',
            '--workspace-id',
            'workspace-1',
            '--non-admin-token',
            'player-token',
            '--campaign-id',
            '7',
            '--session-id',
            '9',
            '--player-id',
            '11',
            '--evidence-report',
            str(output_path),
            '--json-output',
            str(json_path),
            '--values-output',
            str(values_path),
        ]
    )

    assert exit_code == 0
    markdown = output_path.read_text(encoding='utf-8')
    assert '# Hosted RC Evidence' in markdown
    assert '- Status: planned' in markdown
    assert '| Hosted deployment readiness | planned |' in markdown
    assert 'python-test scripts/deployment_readiness_check.py' in markdown
    assert 'operator-token' not in markdown
    assert 'player-token' not in markdown
    assert '<redacted>' in markdown
    payload = json.loads(json_path.read_text(encoding='utf-8'))
    assert payload['status'] == 'planned'
    assert payload['automated_status'] == 'planned'
    assert payload['target_url'] == 'https://aidm.closedbeta.dev'
    assert len(payload['checks']) == 5
    assert all('operator-token' not in check['command'] for check in payload['checks'])
    assert all('player-token' not in check['command'] for check in payload['checks'])
    assert payload['schema_version'] == 1
    assert payload['generator']['path'] == 'scripts/hosted_rc_evidence_check.py'
    assert len(payload['generator']['sha256']) == 64
    assert len(payload['command_plan_sha256']) == 64
    assert '- Generator SHA256: `' in markdown
    assert '- Command plan SHA256: `' in markdown
    values_payload = json.loads(values_path.read_text(encoding='utf-8'))
    assert values_payload['status'] == 'planned'
    assert values_payload['usable_for_signoff'] is False
    assert values_payload['values']['target_url'] == 'https://aidm.closedbeta.dev'
    assert 'operator_auth_token' not in values_payload['values']
    assert 'non_admin_token' not in values_payload['values']


def test_main_preserves_existing_real_evidence_when_requested(monkeypatch, tmp_path):
    output_path = tmp_path / 'hosted-rc-evidence.md'
    json_path = tmp_path / 'hosted-rc-evidence.json'
    values_path = tmp_path / 'external-proof-values.hosted-rc.json'
    output_path.write_text(
        '\n'.join(
            [
                '# Hosted RC Evidence',
                '',
                '- Status: passed',
                '- Dry run: False',
                '',
            ]
        ),
        encoding='utf-8',
    )
    json_path.write_text(json.dumps({'status': 'passed', 'dry_run': False, 'sentinel': 'keep'}), encoding='utf-8')
    values_path.write_text(json.dumps({'status': 'passed', 'sentinel': 'keep-values'}), encoding='utf-8')

    def fail_if_called(*args, **kwargs):
        raise AssertionError('real hosted evidence should have been preserved')

    monkeypatch.setattr(hosted_rc_evidence_check, 'run_plan', fail_if_called)

    exit_code = main(
        [
            '--dry-run',
            '--preserve-existing-real-evidence',
            '--target-url',
            'https://aidm.closedbeta.dev',
            '--auth-token',
            'operator-token',
            '--workspace-id',
            'workspace-1',
            '--non-admin-token',
            'player-token',
            '--campaign-id',
            '7',
            '--session-id',
            '9',
            '--player-id',
            '11',
            '--evidence-report',
            str(output_path),
            '--json-output',
            str(json_path),
            '--values-output',
            str(values_path),
        ]
    )

    assert exit_code == 0
    assert json.loads(json_path.read_text(encoding='utf-8')) == {
        'status': 'passed',
        'dry_run': False,
        'sentinel': 'keep',
    }
    assert json.loads(values_path.read_text(encoding='utf-8')) == {'status': 'passed', 'sentinel': 'keep-values'}


def test_main_returns_manual_evidence_required_when_automated_checks_pass(monkeypatch, tmp_path):
    output_path = tmp_path / 'hosted-rc-evidence.md'
    json_path = tmp_path / 'hosted-rc-evidence.json'
    values_path = tmp_path / 'external-proof-values.hosted-rc.json'

    monkeypatch.setattr(hosted_rc_evidence_check, 'run_plan', lambda plan, dry_run: ('passed', []))

    exit_code = main(
        [
            '--target-url',
            'https://aidm.closedbeta.dev',
            '--auth-token',
            'operator-token',
            '--workspace-id',
            'workspace-1',
            '--non-admin-token',
            'player-token',
            '--campaign-id',
            '7',
            '--session-id',
            '9',
            '--player-id',
            '11',
            '--evidence-report',
            str(output_path),
            '--json-output',
            str(json_path),
            '--values-output',
            str(values_path),
        ]
    )

    assert exit_code == 1
    markdown = output_path.read_text(encoding='utf-8')
    assert '- Status: manual-evidence-required' in markdown
    assert '- Automated status: passed' in markdown
    payload = json.loads(json_path.read_text(encoding='utf-8'))
    assert payload['status'] == 'manual-evidence-required'
    assert payload['automated_status'] == 'passed'
    assert [item['status'] for item in payload['manual_evidence']] == ['required', 'required', 'required', 'required']


def test_main_rejects_placeholder_manual_evidence(monkeypatch, tmp_path):
    output_path = tmp_path / 'hosted-rc-evidence.md'
    json_path = tmp_path / 'hosted-rc-evidence.json'
    values_path = tmp_path / 'external-proof-values.hosted-rc.json'
    monkeypatch.setattr(hosted_rc_evidence_check, 'run_plan', lambda plan, dry_run: ('passed', []))

    exit_code = main(
        [
            '--target-url',
            'https://aidm.closedbeta.dev',
            '--auth-token',
            'operator-token',
            '--workspace-id',
            'workspace-1',
            '--non-admin-token',
            'player-token',
            '--campaign-id',
            '7',
            '--session-id',
            '9',
            '--player-id',
            '11',
            '--hosted-backup-restore-evidence',
            'https://example.test/backup',
            '--hosted-worker-process-evidence',
            'platform-process-log',
            '--source-archive-attachment-evidence',
            '<release-artifact-url>',
            '--external-telemetry-receipt',
            'https://telemetry.closedbeta.dev/events/123',
            '--evidence-report',
            str(output_path),
            '--json-output',
            str(json_path),
            '--values-output',
            str(values_path),
        ]
    )

    assert exit_code == 1
    markdown = output_path.read_text(encoding='utf-8')
    assert '- Status: invalid' in markdown
    assert 'manual evidence must not use example, localhost, or isolated-runtime references' in markdown
    assert 'manual evidence must be a real proof link/path/details value, not a placeholder' in markdown
    payload = json.loads(json_path.read_text(encoding='utf-8'))
    assert payload['status'] == 'invalid'
    assert [item['status'] for item in payload['manual_evidence']] == ['invalid', 'provided', 'invalid', 'provided']


def test_main_rejects_source_archive_attachment_without_checksum(monkeypatch, tmp_path):
    output_path = tmp_path / 'hosted-rc-evidence.md'
    json_path = tmp_path / 'hosted-rc-evidence.json'
    values_path = tmp_path / 'external-proof-values.hosted-rc.json'
    monkeypatch.setattr(hosted_rc_evidence_check, 'run_plan', lambda plan, dry_run: ('passed', []))

    exit_code = main(
        [
            '--target-url',
            'https://aidm.closedbeta.dev',
            '--auth-token',
            'operator-token',
            '--workspace-id',
            'workspace-1',
            '--non-admin-token',
            'player-token',
            '--campaign-id',
            '7',
            '--session-id',
            '9',
            '--player-id',
            '11',
            '--hosted-backup-restore-evidence',
            'https://ops.closedbeta.dev/backup/restore-1',
            '--hosted-worker-process-evidence',
            'https://ops.closedbeta.dev/processes/worker-single',
            '--source-archive-attachment-evidence',
            'https://github.com/dreichner2/AIDM-main/releases/tag/rc1',
            '--external-telemetry-receipt',
            'https://telemetry.closedbeta.dev/events/123',
            '--evidence-report',
            str(output_path),
            '--json-output',
            str(json_path),
            '--values-output',
            str(values_path),
        ]
    )

    assert exit_code == 1
    payload = json.loads(json_path.read_text(encoding='utf-8'))
    assert payload['status'] == 'invalid'
    assert [item['status'] for item in payload['manual_evidence']] == [
        'provided',
        'provided',
        'invalid',
        'provided',
    ]
    assert any(
        item['label'] == 'Source archive attached to RC issue or release'
        and 'SHA256 checksum' in item['error']
        for item in payload['manual_evidence']
    )
    values_payload = json.loads(values_path.read_text(encoding='utf-8'))
    assert values_payload['usable_for_signoff'] is False
    assert 'source_archive_attachment_evidence' not in values_payload['values']


def test_main_writes_signoff_values_fragment_when_hosted_evidence_passes(monkeypatch, tmp_path):
    output_path = tmp_path / 'hosted-rc-evidence.md'
    json_path = tmp_path / 'hosted-rc-evidence.json'
    values_path = tmp_path / 'external-proof-values.hosted-rc.json'
    env_path = tmp_path / 'hosted.env'
    env_path.write_text('AIDM_ENV=production\n', encoding='utf-8')
    source_archive_evidence = 'https://github.com/dreichner2/AIDM-main/releases/tag/rc1 sha256:' + 'a' * 64

    results = [
        HostedCheckResult(
            label='Hosted deployment readiness',
            status='passed',
            returncode=0,
            duration_seconds=1.0,
            command='python scripts/deployment_readiness_check.py --auth-token <redacted>',
            evidence_path='tmp/release/deployment-readiness-evidence.md',
            evidence_target_url='https://aidm.closedbeta.dev',
        ),
        HostedCheckResult(
            label='Hosted cookie auth smoke',
            status='passed',
            returncode=0,
            duration_seconds=1.0,
            command='python scripts/hosted_cookie_auth_smoke.py',
            evidence_path='tmp/release/hosted-cookie-auth-evidence.md',
            evidence_target_url='https://aidm.closedbeta.dev',
        ),
        HostedCheckResult(
            label='Hosted non-admin forbidden smoke',
            status='passed',
            returncode=0,
            duration_seconds=1.0,
            command='python scripts/security_forbidden_smoke.py --account-token <redacted>',
            evidence_path='tmp/release/security-forbidden-evidence.md',
            evidence_target_url='https://aidm.closedbeta.dev',
        ),
        HostedCheckResult(
            label='Hosted session export/import smoke',
            status='passed',
            returncode=0,
            duration_seconds=1.0,
            command='python scripts/session_export_import_smoke.py --auth-token <redacted>',
            evidence_path='tmp/release/export-import-evidence.md',
            evidence_target_url='https://aidm.closedbeta.dev',
        ),
        HostedCheckResult(
            label='Hosted beta SLO baseline',
            status='passed',
            returncode=0,
            duration_seconds=1.0,
            command='python scripts/render_beta_slo_baseline.py --auth-token <redacted>',
            evidence_path='tmp/release/beta-slo-baseline.md',
            evidence_target_url='https://aidm.closedbeta.dev',
        ),
    ]
    monkeypatch.setattr(hosted_rc_evidence_check, 'run_plan', lambda plan, dry_run: ('passed', results))

    exit_code = main(
        [
            '--target-url',
            'https://aidm.closedbeta.dev',
            '--env-file',
            str(env_path),
            '--auth-token',
            'operator-token',
            '--workspace-id',
            'workspace-1',
            '--non-admin-token',
            'player-token',
            '--campaign-id',
            '7',
            '--session-id',
            '9',
            '--player-id',
            '11',
            '--commit-sha',
            'abc1234',
            '--socketio-worker-model',
            'single',
            '--hosted-backup-restore-evidence',
            'https://ops.closedbeta.dev/backup/restore-1',
            '--hosted-worker-process-evidence',
            'https://ops.closedbeta.dev/processes/worker-single',
            '--source-archive-attachment-evidence',
            source_archive_evidence,
            '--external-telemetry-receipt',
            'https://telemetry.closedbeta.dev/events/123',
            '--evidence-report',
            str(output_path),
            '--json-output',
            str(json_path),
            '--values-output',
            str(values_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(json_path.read_text(encoding='utf-8'))
    assert payload['status'] == 'passed'
    assert [item['status'] for item in payload['manual_evidence']] == ['provided', 'provided', 'provided', 'provided']

    values_payload = json.loads(values_path.read_text(encoding='utf-8'))
    assert values_payload['status'] == 'passed'
    assert values_payload['usable_for_signoff'] is True
    values = values_payload['values']
    assert values['target_url'] == 'https://aidm.closedbeta.dev'
    assert values['target_env_file'] == str(env_path)
    assert values['workspace_id'] == 'workspace-1'
    assert values['campaign_id'] == '7'
    assert values['session_id'] == '9'
    assert values['player_id'] == '11'
    assert values['signed_off_commit_sha'] == 'abc1234'
    assert values['deployment_readiness_evidence'] == 'tmp/release/deployment-readiness-evidence.md'
    assert values['hosted_cookie_auth_evidence'] == 'tmp/release/hosted-cookie-auth-evidence.md'
    assert values['hosted_non_admin_forbidden_evidence'] == 'tmp/release/security-forbidden-evidence.md'
    assert values['hosted_export_import_evidence'] == 'tmp/release/export-import-evidence.md'
    assert values['hosted_beta_slo_baseline_evidence'] == 'tmp/release/beta-slo-baseline.md'
    assert values['hosted_backup_restore_evidence'] == 'https://ops.closedbeta.dev/backup/restore-1'
    assert values['hosted_worker_process_evidence'] == 'https://ops.closedbeta.dev/processes/worker-single'
    assert values['source_archive_attachment_evidence'] == source_archive_evidence
    assert values['external_telemetry_receipt'] == 'https://telemetry.closedbeta.dev/events/123'
    assert values['socketio_worker_model'] == 'single'
    assert 'operator_auth_token' not in values
    assert 'non_admin_token' not in values
