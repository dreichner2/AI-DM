from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.render_operator_signoff_from_external_inputs import (
    build_manifest_from_values,
    build_values_template,
    main,
)
from scripts.render_operator_signoff_status import build_report, example_manifest


def _complete_values_payload() -> dict:
    return {
        'release': 'RC1',
        'commit': 'abc123',
        'target_url': 'https://aidm.closedbeta.dev',
        'signed_by': 'operator',
        'signed_at': '2026-06-19T00:00:00+00:00',
        'values': {
            'clean_worktree_rc_evidence': 'tmp/release/rc-evidence.md',
            'aidm_ci_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/111',
            'closed_beta_rc_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/222',
            'closed_beta_rc_artifact_reference': 'https://github.com/dreichner2/AIDM-main/actions/runs/222/artifacts/closed-beta-rc-evidence',
            'deployment_readiness_evidence': 'tmp/release/deployment-readiness-evidence.md',
            'hosted_cookie_auth_evidence': 'tmp/release/hosted-cookie-auth-evidence.md',
            'hosted_non_admin_forbidden_evidence': 'tmp/release/security-forbidden-evidence.md',
            'hosted_export_import_evidence': 'tmp/release/export-import-evidence.md',
            'hosted_backup_restore_evidence': 'https://provider.aidm.closedbeta.dev/backup-restore-log',
            'hosted_worker_process_evidence': 'https://platform.aidm.closedbeta.dev/processes',
            'socketio_worker_model': 'single',
            'hosted_beta_slo_baseline_evidence': 'tmp/release/beta-slo-baseline.md',
            'external_telemetry_receipt': 'https://telemetry.aidm.closedbeta.dev/events/123',
            'source_archive_attachment_evidence': (
                'https://github.com/dreichner2/AIDM-main/releases/tag/rc1 sha256:' + 'a' * 64
            ),
            'rc_issue_closure_review': 'https://github.com/dreichner2/AIDM-main/issues/3#issuecomment-1',
            'make_clean_evidence': 'make clean output reviewed',
            'make_clean_deps_evidence': 'make clean-deps output reviewed',
        },
    }


def _write_hosted_target_reports(root: Path, *, target_url: str = 'https://aidm.closedbeta.dev') -> None:
    base = root / 'tmp' / 'release'
    base.mkdir(parents=True, exist_ok=True)
    (base / 'rc-evidence.md').write_text(
        '- Status: passed\n- Commit: abc123\n- Worktree: clean\n',
        encoding='utf-8',
    )
    (base / 'deployment-readiness-evidence.md').write_text(
        f'- Status: passed\n- Target URL: `{target_url}`\n',
        encoding='utf-8',
    )
    for name in ('hosted-cookie-auth-evidence.md', 'security-forbidden-evidence.md', 'export-import-evidence.md'):
        (base / name).write_text(
            f'- Status: passed\n- Mode: live-target\n- Target URL: `{target_url}`\n',
            encoding='utf-8',
        )
    (base / 'beta-slo-baseline.md').write_text(
        f'- Status: passed\n- Target URL: `{target_url}`\n',
        encoding='utf-8',
    )


def test_build_values_template_carries_current_context_without_live_tokens():
    template = build_values_template(
        external_inputs={
            'fields': [
                {
                    'key': 'aidm_ci_run_url',
                    'current_value': 'https://github.com/dreichner2/AIDM-main/actions/runs/111',
                    'notes': 'CI URL',
                },
                {
                    'key': 'operator_auth_token',
                    'current_value': '',
                    'sensitive': True,
                    'notes': 'Do not store this token.',
                },
                {
                    'key': 'non_admin_token',
                    'current_value': '',
                    'sensitive': True,
                    'notes': 'Do not store this token.',
                },
            ]
        },
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert template['values']['aidm_ci_run_url'] == 'https://github.com/dreichner2/AIDM-main/actions/runs/111'
    assert 'operator_auth_token' not in template['values']
    assert 'non_admin_token' not in template['values']
    assert template['sensitive_fields_omitted'] == ['non_admin_token', 'operator_auth_token']
    assert template['values']['deployment_readiness_evidence'] == ''
    assert template['values']['socketio_worker_model'] == 'single'
    assert 'Sensitive command-only values are intentionally omitted' in template['instructions']


def test_build_manifest_from_values_can_complete_signoff(tmp_path):
    manifest = build_manifest_from_values(
        draft_manifest=example_manifest(),
        values_payload=_complete_values_payload(),
        generated_at='2026-06-19T00:01:00+00:00',
    )
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    _write_hosted_target_reports(tmp_path)
    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:02:00+00:00')

    assert manifest['commit'] == 'abc123'
    assert manifest['items']['clean_signed_off_worktree']['status'] == 'provided'
    assert manifest['items']['hosted_deployment_readiness']['status'] == 'provided'
    assert manifest['items']['frontend_npm_ci']['evidence'] == 'https://github.com/dreichner2/AIDM-main/actions/runs/111'
    assert manifest['items']['multi_worker_socketio_staging']['status'] == 'not_applicable'
    assert manifest['items']['multi_worker_socketio_staging']['evidence'] == 'https://platform.aidm.closedbeta.dev/processes'
    assert report['status'] == 'passed'
    assert report['complete_count'] == report['required_count']


def test_build_manifest_rejects_persisted_sensitive_values():
    payload = _complete_values_payload()
    payload['values']['operator_auth_token'] = 'secret-token'

    with pytest.raises(ValueError, match='values.operator_auth_token'):
        build_manifest_from_values(
            draft_manifest=example_manifest(),
            values_payload=payload,
            generated_at='2026-06-19T00:01:00+00:00',
        )


def test_build_manifest_rejects_secret_like_alias_values():
    payload = _complete_values_payload()
    payload['values']['hosted_api_key'] = 'secret-token'

    with pytest.raises(ValueError, match='values.hosted_api_key'):
        build_manifest_from_values(
            draft_manifest=example_manifest(),
            values_payload=payload,
            generated_at='2026-06-19T00:01:00+00:00',
        )


def test_build_manifest_rejects_top_level_sensitive_values():
    payload = _complete_values_payload()
    payload['non_admin_token'] = 'secret-token'

    with pytest.raises(ValueError, match='non_admin_token'):
        build_manifest_from_values(
            draft_manifest=example_manifest(),
            values_payload=payload,
            generated_at='2026-06-19T00:01:00+00:00',
        )


def test_build_manifest_leaves_source_archive_attachment_pending_without_checksum(tmp_path):
    payload = _complete_values_payload()
    payload['values']['source_archive_attachment_evidence'] = 'https://github.com/dreichner2/AIDM-main/releases/tag/rc1'

    manifest = build_manifest_from_values(
        draft_manifest=example_manifest(),
        values_payload=payload,
        generated_at='2026-06-19T00:01:00+00:00',
    )
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    _write_hosted_target_reports(tmp_path)
    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:02:00+00:00')

    assert report['status'] == 'invalid'
    assert any(
        'source_archive_attachment' in error and 'SHA256 checksum' in error
        for error in report['errors']
    )


def test_build_manifest_requires_worker_evidence_before_multi_worker_not_applicable(tmp_path):
    payload = _complete_values_payload()
    payload['values'].pop('hosted_worker_process_evidence')

    manifest = build_manifest_from_values(
        draft_manifest=example_manifest(),
        values_payload=payload,
        generated_at='2026-06-19T00:01:00+00:00',
    )
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    _write_hosted_target_reports(tmp_path)
    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:02:00+00:00')

    assert manifest['items']['multi_worker_socketio_staging']['status'] == 'pending'
    assert 'Single-worker mode still needs hosted worker-process evidence' in manifest['items']['multi_worker_socketio_staging']['notes']
    assert report['status'] == 'incomplete'
    assert any(item['key'] == 'multi_worker_socketio_staging' and not item['complete'] for item in report['items'])


def test_build_manifest_rejects_sticky_model_without_staging_proof(tmp_path):
    payload = _complete_values_payload()
    payload['values']['socketio_worker_model'] = 'sticky'
    payload['values']['multi_worker_socketio_staging_not_applicable'] = 'true'
    payload['values'].pop('socketio_staging_proof', None)

    with pytest.raises(ValueError, match='socketio_worker_model must be single.*sticky is unsupported'):
        build_manifest_from_values(
            draft_manifest=example_manifest(),
            values_payload=payload,
            generated_at='2026-06-19T00:01:00+00:00',
        )


def test_build_manifest_rejects_message_queue_model_even_with_staging_proof():
    payload = _complete_values_payload()
    payload['values']['socketio_worker_model'] = 'message_queue'
    payload['values']['multi_worker_socketio_staging_not_applicable'] = 'true'
    payload['values']['socketio_staging_proof'] = 'https://platform.aidm.closedbeta.dev/socketio-message-queue-proof'

    with pytest.raises(ValueError, match='socketio_worker_model must be single.*message_queue is unsupported'):
        build_manifest_from_values(
            draft_manifest=example_manifest(),
            values_payload=payload,
            generated_at='2026-06-19T00:01:00+00:00',
        )


def test_build_manifest_does_not_treat_not_applicable_as_single_worker_proof():
    payload = _complete_values_payload()
    payload['values'].pop('socketio_worker_model')
    payload['values']['multi_worker_socketio_staging_not_applicable'] = 'true'

    manifest = build_manifest_from_values(
        draft_manifest=example_manifest(),
        values_payload=payload,
        generated_at='2026-06-19T00:01:00+00:00',
    )
    item = manifest['items']['multi_worker_socketio_staging']

    assert item['status'] == 'pending'
    assert item['evidence'] == ''
    assert 'Set socketio_worker_model=single' in item['notes']


def test_main_writes_values_template_and_signoff_preview(tmp_path):
    external_inputs = tmp_path / 'external-proof-inputs.json'
    values_template = tmp_path / 'external-proof-values.example.json'
    values = tmp_path / 'external-proof-values.json'
    packet = tmp_path / 'release-evidence-packet.json'
    output = tmp_path / 'operator-signoff.from-inputs.json'
    status_output = tmp_path / 'operator-signoff.from-inputs-status.md'
    status_json = tmp_path / 'operator-signoff.from-inputs-status.json'
    external_inputs.write_text(
        json.dumps(
            {
                'fields': [
                    {'key': 'aidm_ci_run_url', 'current_value': 'https://github.com/dreichner2/AIDM-main/actions/runs/111'},
                    {'key': 'deployment_readiness_evidence', 'current_value': ''},
                ]
            }
        ),
        encoding='utf-8',
    )

    template_exit = main(
        [
            '--external-inputs-json',
            str(external_inputs),
            '--write-values-template',
            str(values_template),
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
        ]
    )
    assert template_exit == 0
    assert json.loads(values_template.read_text(encoding='utf-8'))['values']['aidm_ci_run_url']

    values.write_text(json.dumps(_complete_values_payload()), encoding='utf-8')
    packet.write_text(json.dumps({'source_archive': {'sha256': 'a' * 64}}), encoding='utf-8')
    _write_hosted_target_reports(tmp_path)
    render_exit = main(
        [
            '--values',
            str(values),
            '--packet-json',
            str(packet),
            '--output',
            str(output),
            '--status-output',
            str(status_output),
            '--status-json-output',
            str(status_json),
            '--generated-at',
            '2026-06-19T00:01:00+00:00',
        ]
    )

    assert render_exit == 0
    assert json.loads(output.read_text(encoding='utf-8'))['items']['hosted_cookie_auth']['status'] == 'provided'
    assert '# RC Operator Sign-Off Status' in status_output.read_text(encoding='utf-8')
    assert json.loads(status_json.read_text(encoding='utf-8'))['status'] == 'passed'


def test_main_writes_self_evidence_to_values_after_passed_signoff(tmp_path):
    values = tmp_path / 'external-proof-values.json'
    packet = tmp_path / 'release-evidence-packet.json'
    output = tmp_path / 'operator-signoff.json'
    status_output = tmp_path / 'operator-signoff-status.md'
    status_json = tmp_path / 'operator-signoff-status.json'
    values.write_text(json.dumps(_complete_values_payload()), encoding='utf-8')
    packet.write_text(json.dumps({'source_archive': {'sha256': 'a' * 64}}), encoding='utf-8')
    _write_hosted_target_reports(tmp_path)

    exit_code = main(
        [
            '--values',
            str(values),
            '--packet-json',
            str(packet),
            '--output',
            str(output),
            '--status-output',
            str(status_output),
            '--status-json-output',
            str(status_json),
            '--write-self-evidence-to-values',
            '--require-complete',
            '--generated-at',
            '2026-06-19T00:01:00+00:00',
        ]
    )

    stored_values = json.loads(values.read_text(encoding='utf-8'))
    assert exit_code == 0
    assert json.loads(status_json.read_text(encoding='utf-8'))['status'] == 'passed'
    assert stored_values['values']['operator_signoff_manifest_evidence'] == str(status_output)


def test_main_does_not_write_self_evidence_after_invalid_signoff(tmp_path):
    values = tmp_path / 'external-proof-values.json'
    packet = tmp_path / 'release-evidence-packet.json'
    output = tmp_path / 'operator-signoff.json'
    status_output = tmp_path / 'operator-signoff-status.md'
    status_json = tmp_path / 'operator-signoff-status.json'
    values.write_text(json.dumps(_complete_values_payload()), encoding='utf-8')
    packet.write_text(json.dumps({'source_archive': {'sha256': 'b' * 64}}), encoding='utf-8')
    _write_hosted_target_reports(tmp_path)

    exit_code = main(
        [
            '--values',
            str(values),
            '--packet-json',
            str(packet),
            '--output',
            str(output),
            '--status-output',
            str(status_output),
            '--status-json-output',
            str(status_json),
            '--write-self-evidence-to-values',
            '--require-complete',
            '--generated-at',
            '2026-06-19T00:01:00+00:00',
        ]
    )

    stored_values = json.loads(values.read_text(encoding='utf-8'))
    assert exit_code == 1
    assert json.loads(status_json.read_text(encoding='utf-8'))['status'] == 'invalid'
    assert 'operator_signoff_manifest_evidence' not in stored_values['values']


def test_main_validates_external_input_source_archive_against_packet_checksum(tmp_path):
    values = tmp_path / 'external-proof-values.json'
    packet = tmp_path / 'release-evidence-packet.json'
    output = tmp_path / 'operator-signoff.from-inputs.json'
    status_output = tmp_path / 'operator-signoff.from-inputs-status.md'
    status_json = tmp_path / 'operator-signoff.from-inputs-status.json'
    values.write_text(json.dumps(_complete_values_payload()), encoding='utf-8')
    packet.write_text(json.dumps({'source_archive': {'sha256': 'b' * 64}}), encoding='utf-8')
    _write_hosted_target_reports(tmp_path)

    exit_code = main(
        [
            '--values',
            str(values),
            '--packet-json',
            str(packet),
            '--output',
            str(output),
            '--status-output',
            str(status_output),
            '--status-json-output',
            str(status_json),
            '--generated-at',
            '2026-06-19T00:01:00+00:00',
        ]
    )

    payload = json.loads(status_json.read_text(encoding='utf-8'))
    assert exit_code == 0
    assert payload['status'] == 'invalid'
    assert any(
        'source_archive_attachment' in error and 'current source archive sha256' in error
        for error in payload['errors']
    )


def test_main_missing_values_file_writes_incomplete_preview(tmp_path):
    values = tmp_path / 'missing-external-proof-values.json'
    packet = tmp_path / 'release-evidence-packet.json'
    draft = tmp_path / 'operator-signoff.draft.json'
    output = tmp_path / 'operator-signoff.from-inputs.json'
    status_output = tmp_path / 'operator-signoff.from-inputs-status.md'
    status_json = tmp_path / 'operator-signoff.from-inputs-status.json'
    packet.write_text('{}', encoding='utf-8')
    draft.write_text(json.dumps(example_manifest()), encoding='utf-8')

    exit_code = main(
        [
            '--values',
            str(values),
            '--packet-json',
            str(packet),
            '--draft',
            str(draft),
            '--output',
            str(output),
            '--status-output',
            str(status_output),
            '--status-json-output',
            str(status_json),
            '--generated-at',
            '2026-06-19T00:01:00+00:00',
        ]
    )

    payload = json.loads(status_json.read_text(encoding='utf-8'))
    assert exit_code == 0
    assert payload['status'] == 'incomplete'
    assert payload['preview_mode'] is True
    assert 'external-proof-values.json is missing' in payload['preview_reason']
    assert '- Status: incomplete' in status_output.read_text(encoding='utf-8')


def test_main_uses_current_packet_context_for_default_draft(tmp_path):
    packet = tmp_path / 'release-evidence-packet.json'
    values = tmp_path / 'missing-external-proof-values.json'
    output = tmp_path / 'operator-signoff.from-inputs.json'
    status_json = tmp_path / 'operator-signoff.from-inputs-status.json'
    packet.write_text(
        json.dumps(
            {
                'frontend_npm_ci': {
                    'status': 'passed',
                    'freshness': 'current',
                    'path': '/tmp/aidm/frontend-npm-ci-evidence.md',
                },
                'packaging_cleanup': {
                    'status': 'passed',
                    'freshness': 'current',
                    'path': '/tmp/aidm/packaging-cleanup-evidence.md',
                },
            }
        ),
        encoding='utf-8',
    )

    exit_code = main(
        [
            '--packet-json',
            str(packet),
            '--values',
            str(values),
            '--output',
            str(output),
            '--status-json-output',
            str(status_json),
            '--generated-at',
            '2026-06-19T00:01:00+00:00',
        ]
    )

    manifest = json.loads(output.read_text(encoding='utf-8'))
    assert exit_code == 0
    assert manifest['items']['frontend_npm_ci']['status'] == 'provided'
    assert manifest['items']['make_clean']['status'] == 'provided'
    assert manifest['items']['make_clean_deps']['status'] == 'provided'
    assert manifest['items']['make_clean']['evidence'] == '/tmp/aidm/packaging-cleanup-evidence.md'


def test_main_existing_incomplete_values_file_stays_invalid(tmp_path):
    values = tmp_path / 'external-proof-values.json'
    packet = tmp_path / 'release-evidence-packet.json'
    output = tmp_path / 'operator-signoff.from-inputs.json'
    status_json = tmp_path / 'operator-signoff.from-inputs-status.json'
    packet.write_text('{}', encoding='utf-8')
    values.write_text(
        json.dumps({'values': {'aidm_ci_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/111'}}),
        encoding='utf-8',
    )

    exit_code = main(
        [
            '--values',
            str(values),
            '--packet-json',
            str(packet),
            '--output',
            str(output),
            '--status-json-output',
            str(status_json),
            '--generated-at',
            '2026-06-19T00:01:00+00:00',
        ]
    )

    payload = json.loads(status_json.read_text(encoding='utf-8'))
    assert exit_code == 0
    assert payload['status'] == 'invalid'
    assert 'preview_mode' not in payload
    assert any('target_url must be a real hosted/staging URL' in error for error in payload['errors'])


def test_main_rejects_values_file_with_persisted_sensitive_values(tmp_path):
    values = tmp_path / 'external-proof-values.json'
    output = tmp_path / 'operator-signoff.from-inputs.json'
    payload = _complete_values_payload()
    payload['values']['non_admin_token'] = 'secret-token'
    values.write_text(json.dumps(payload), encoding='utf-8')

    exit_code = main(
        [
            '--values',
            str(values),
            '--output',
            str(output),
            '--generated-at',
            '2026-06-19T00:01:00+00:00',
        ]
    )

    assert exit_code == 2
    assert not output.exists()
