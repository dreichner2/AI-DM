from __future__ import annotations

import json

from scripts.check_external_proof_values import build_report, main, render_markdown


def _external_inputs() -> dict:
    return {
        'status': 'action-required',
        'github_actions': {
            'repository': 'dreichner2/AIDM-main',
        },
        'fields': [
            {
                'key': 'signed_off_commit_sha',
                'status': 'required',
                'required_for': ['clean_signed_off_worktree'],
                'notes': 'Final signed-off commit.',
            },
            {
                'key': 'clean_worktree_rc_evidence',
                'status': 'required',
                'required_for': ['clean_signed_off_worktree'],
                'notes': 'Clean-worktree RC evidence.',
            },
            {
                'key': 'closed_beta_rc_run_url',
                'status': 'required',
                'required_for': ['github_actions_closed_beta_rc'],
                'notes': 'Successful manual Closed Beta RC workflow run.',
            },
            {
                'key': 'operator_signoff_manifest_evidence',
                'status': 'required',
                'required_for': ['final_operator_signoff'],
                'notes': 'Final operator signoff status.',
            },
            {
                'key': 'target_url',
                'status': 'required',
                'required_for': ['hosted_deployment_readiness'],
                'notes': 'Real hosted target.',
            },
            {
                'key': 'operator_auth_token',
                'status': 'required',
                'required_for': ['hosted_deployment_readiness'],
                'sensitive': True,
                'notes': 'Pass only via command flags.',
            },
            {
                'key': 'frontend_npm_ci_evidence',
                'status': 'provided-context',
                'current_value': 'tmp/release/frontend-npm-ci-evidence.md',
                'required_for': ['frontend_npm_ci'],
            },
            {
                'key': 'socketio_staging_proof',
                'status': 'conditional',
                'required_for': ['multi_worker_socketio_staging'],
                'notes': 'Required only for sticky/message_queue.',
            },
        ],
    }


def _complete_values() -> dict:
    return {
        'release': 'RC1',
        'commit': 'abc123',
        'target_url': 'https://aidm.closedbeta.dev',
        'signed_by': 'operator',
        'signed_at': '2026-06-19T00:00:00+00:00',
        'values': {
            'signed_off_commit_sha': 'abc123',
            'clean_worktree_rc_evidence': 'rc-evidence.md',
            'operator_signoff_manifest_evidence': 'operator-signoff-status.md',
            'closed_beta_rc_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/222',
            'target_url': 'https://aidm.closedbeta.dev',
            'socketio_worker_model': 'single',
        },
    }


def _write_rc_evidence(path, *, status: str = 'passed', commit: str = 'abc123', worktree: str = 'clean') -> None:
    path.write_text(
        '\n'.join(
            [
                '# Closed Beta RC Evidence',
                '',
                f'- Status: {status}',
                f'- Commit: {commit}',
                f'- Worktree: {worktree}',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _write_operator_signoff_status(
    path,
    *,
    status: str = 'passed',
    complete: int = 19,
    required: int = 19,
    pending: int = 0,
) -> None:
    path.write_text(
        '\n'.join(
            [
                '# RC Operator Sign-Off Status',
                '',
                f'- Status: {status}',
                f'- Required complete: {complete}/{required}',
                f'- Missing or invalid required items: {pending}',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _write_hosted_evidence(path, *, status: str = 'passed', target_url: str = 'https://aidm.closedbeta.dev') -> None:
    path.write_text(
        '\n'.join(
            [
                '# Hosted Evidence',
                '',
                f'- Status: {status}',
                f'- Target URL: `{target_url}`',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _external_inputs_with_hosted_field(key: str) -> dict:
    external_inputs = _external_inputs()
    external_inputs['fields'].append(
        {
            'key': key,
            'status': 'required',
            'required_for': ['hosted_target'],
            'notes': 'Hosted target evidence.',
        }
    )
    return external_inputs


def _external_inputs_with_source_archive_attachment() -> dict:
    external_inputs = _external_inputs()
    external_inputs['source_archive'] = {
        'status': 'passed',
        'path': 'tmp/release/aidm-source.tar.gz',
        'sha256': 'a' * 64,
    }
    external_inputs['fields'].append(
        {
            'key': 'source_archive_attachment_evidence',
            'status': 'required',
            'required_for': ['source_archive_attachment'],
            'notes': 'Source archive attachment URL plus checksum.',
        }
    )
    return external_inputs


def test_missing_values_file_renders_incomplete_preview(tmp_path):
    report = build_report(
        external_inputs=_external_inputs(),
        values_payload={},
        values_present=False,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )
    markdown = render_markdown(report)

    assert report['status'] == 'incomplete'
    assert report['values_present'] is False
    assert report['required_complete'] == '1/6'
    assert report['missing_required_fields'] == [
        'signed_off_commit_sha',
        'clean_worktree_rc_evidence',
        'closed_beta_rc_run_url',
        'operator_signoff_manifest_evidence',
        'target_url',
    ]
    assert report['command_only_fields'] == ['operator_auth_token']
    assert '## Command-Only Fields' in markdown


def test_required_current_context_counts_complete_without_values_file(tmp_path):
    external_inputs = _external_inputs()
    external_inputs['fields'].insert(
        0,
        {
            'key': 'aidm_ci_run_url',
            'status': 'required',
            'current_value': 'https://github.com/dreichner2/AIDM-main/actions/runs/111',
            'required_for': ['github_actions_aidm_ci'],
            'notes': 'Successful AIDM CI workflow run.',
        },
    )

    report = build_report(
        external_inputs=external_inputs,
        values_payload={},
        values_present=False,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )
    fields = {field['key']: field for field in report['fields']}

    assert fields['aidm_ci_run_url']['status'] == 'provided-context'
    assert fields['aidm_ci_run_url']['complete'] is True
    assert report['required_complete'] == '2/7'
    assert report['missing_required_fields'] == [
        'signed_off_commit_sha',
        'clean_worktree_rc_evidence',
        'closed_beta_rc_run_url',
        'operator_signoff_manifest_evidence',
        'target_url',
    ]


def test_complete_values_pass_without_persisted_tokens(tmp_path):
    _write_rc_evidence(tmp_path / 'rc-evidence.md')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')
    report = build_report(
        external_inputs=_external_inputs(),
        values_payload=_complete_values(),
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'passed'
    assert report['missing_required_fields'] == []
    assert report['metadata_errors'] == []
    assert report['invalid_errors'] == []


def test_complete_values_accept_github_actions_run_url_with_query(tmp_path):
    values = _complete_values()
    values['values']['closed_beta_rc_run_url'] = 'https://github.com/dreichner2/AIDM-main/actions/runs/222?check_suite_focus=true'
    _write_rc_evidence(tmp_path / 'rc-evidence.md')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')

    report = build_report(
        external_inputs=_external_inputs(),
        values_payload=values,
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'passed'
    assert report['invalid_errors'] == []


def test_complete_values_reject_malformed_github_actions_run_url(tmp_path):
    values = _complete_values()
    values['values']['closed_beta_rc_run_url'] = 'https://github.com/dreichner2/AIDM-main/pulls/222'
    _write_rc_evidence(tmp_path / 'rc-evidence.md')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')

    report = build_report(
        external_inputs=_external_inputs(),
        values_payload=values,
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'invalid'
    assert any(
        'closed_beta_rc_run_url' in error and 'must look like https://github.com/<owner>/<repo>/actions/runs/<run-id>' in error
        for error in report['invalid_errors']
    )


def test_complete_values_reject_wrong_repository_github_actions_run_url(tmp_path):
    values = _complete_values()
    values['values']['closed_beta_rc_run_url'] = 'https://github.com/other/AIDM-main/actions/runs/222'
    _write_rc_evidence(tmp_path / 'rc-evidence.md')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')

    report = build_report(
        external_inputs=_external_inputs(),
        values_payload=values,
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'invalid'
    assert any(
        'closed_beta_rc_run_url' in error and 'repository other/AIDM-main does not match dreichner2/AIDM-main' in error
        for error in report['invalid_errors']
    )


def test_complete_values_accept_real_target_local_hosted_evidence(tmp_path):
    values = _complete_values()
    values['values']['hosted_cookie_auth_evidence'] = 'hosted-cookie-auth-evidence.md'
    _write_rc_evidence(tmp_path / 'rc-evidence.md')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')
    _write_hosted_evidence(tmp_path / 'hosted-cookie-auth-evidence.md')

    report = build_report(
        external_inputs=_external_inputs_with_hosted_field('hosted_cookie_auth_evidence'),
        values_payload=values,
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'passed'
    assert report['invalid_errors'] == []


def test_complete_values_reject_isolated_local_hosted_evidence(tmp_path):
    values = _complete_values()
    values['values']['hosted_cookie_auth_evidence'] = 'hosted-cookie-auth-evidence.md'
    _write_rc_evidence(tmp_path / 'rc-evidence.md')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')
    _write_hosted_evidence(
        tmp_path / 'hosted-cookie-auth-evidence.md',
        target_url='isolated local runtime',
    )

    report = build_report(
        external_inputs=_external_inputs_with_hosted_field('hosted_cookie_auth_evidence'),
        values_payload=values,
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'invalid'
    assert any(
        'hosted_cookie_auth_evidence' in error and 'not a real hosted/staging target' in error
        for error in report['invalid_errors']
    )


def test_complete_values_reject_local_hosted_evidence_target_mismatch(tmp_path):
    values = _complete_values()
    values['values']['hosted_cookie_auth_evidence'] = 'hosted-cookie-auth-evidence.md'
    _write_rc_evidence(tmp_path / 'rc-evidence.md')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')
    _write_hosted_evidence(
        tmp_path / 'hosted-cookie-auth-evidence.md',
        target_url='https://other.closedbeta.dev',
    )

    report = build_report(
        external_inputs=_external_inputs_with_hosted_field('hosted_cookie_auth_evidence'),
        values_payload=values,
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'invalid'
    assert any(
        'hosted_cookie_auth_evidence' in error
        and 'does not match target_url https://aidm.closedbeta.dev' in error
        for error in report['invalid_errors']
    )


def test_complete_values_reject_local_hosted_evidence_without_passed_status(tmp_path):
    values = _complete_values()
    values['values']['hosted_non_admin_forbidden_evidence'] = 'security-forbidden-evidence.md'
    _write_rc_evidence(tmp_path / 'rc-evidence.md')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')
    _write_hosted_evidence(
        tmp_path / 'security-forbidden-evidence.md',
        status='failed',
    )

    report = build_report(
        external_inputs=_external_inputs_with_hosted_field('hosted_non_admin_forbidden_evidence'),
        values_payload=values,
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'invalid'
    assert any(
        'hosted_non_admin_forbidden_evidence' in error and 'status is not passed: failed' in error
        for error in report['invalid_errors']
    )


def test_complete_values_reject_local_beta_slo_with_isolated_target(tmp_path):
    values = _complete_values()
    values['values']['hosted_beta_slo_baseline_evidence'] = 'beta-slo-baseline.md'
    _write_rc_evidence(tmp_path / 'rc-evidence.md')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')
    (tmp_path / 'beta-slo-baseline.md').write_text(
        '\n'.join(
            [
                '# Beta SLO Baseline',
                '',
                '## Release Context',
                '',
                '- Target URL: isolated local runtime',
                '',
            ]
        ),
        encoding='utf-8',
    )

    report = build_report(
        external_inputs=_external_inputs_with_hosted_field('hosted_beta_slo_baseline_evidence'),
        values_payload=values,
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'invalid'
    assert any(
        'hosted_beta_slo_baseline_evidence' in error and 'not a real hosted/staging target' in error
        for error in report['invalid_errors']
    )


def test_complete_values_reject_missing_clean_worktree_evidence_path(tmp_path):
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')
    report = build_report(
        external_inputs=_external_inputs(),
        values_payload=_complete_values(),
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'invalid'
    assert any('clean_worktree_rc_evidence' in error and 'path does not exist' in error for error in report['invalid_errors'])


def test_complete_values_reject_missing_operator_signoff_evidence_path(tmp_path):
    _write_rc_evidence(tmp_path / 'rc-evidence.md')

    report = build_report(
        external_inputs=_external_inputs(),
        values_payload=_complete_values(),
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'invalid'
    assert any(
        'operator_signoff_manifest_evidence' in error and 'path does not exist' in error
        for error in report['invalid_errors']
    )


def test_complete_values_reject_incomplete_operator_signoff_evidence(tmp_path):
    _write_rc_evidence(tmp_path / 'rc-evidence.md')
    _write_operator_signoff_status(
        tmp_path / 'operator-signoff-status.md',
        status='incomplete',
        complete=18,
        required=19,
        pending=1,
    )

    report = build_report(
        external_inputs=_external_inputs(),
        values_payload=_complete_values(),
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'invalid'
    assert any('operator signoff status is not passed' in error for error in report['invalid_errors'])
    assert any('required complete is not full: 18/19' in error for error in report['invalid_errors'])


def test_complete_values_reject_dirty_clean_worktree_evidence(tmp_path):
    _write_rc_evidence(tmp_path / 'rc-evidence.md', worktree='dirty (2 changed/untracked paths)')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')

    report = build_report(
        external_inputs=_external_inputs(),
        values_payload=_complete_values(),
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'invalid'
    assert any('worktree is not clean' in error for error in report['invalid_errors'])


def test_complete_values_reject_clean_worktree_evidence_commit_mismatch(tmp_path):
    _write_rc_evidence(tmp_path / 'rc-evidence.md', commit='def456')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')

    report = build_report(
        external_inputs=_external_inputs(),
        values_payload=_complete_values(),
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'invalid'
    assert any('does not match signed_off_commit_sha abc123' in error for error in report['invalid_errors'])


def test_non_single_socketio_models_are_invalid_even_with_staging_proof(tmp_path):
    for worker_model in ('sticky', 'message_queue', 'future_model'):
        values = _complete_values()
        values['values']['socketio_worker_model'] = worker_model
        values['values']['socketio_staging_proof'] = 'https://platform.aidm.closedbeta.dev/socketio-proof'
        _write_rc_evidence(tmp_path / 'rc-evidence.md')
        _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')

        report = build_report(
            external_inputs=_external_inputs(),
            values_payload=values,
            values_present=True,
            values_path=tmp_path / 'external-proof-values.json',
            external_inputs_path=tmp_path / 'external-proof-inputs.json',
            generated_at='2026-06-19T00:00:00+00:00',
        )

        assert report['status'] == 'invalid'
        assert report['missing_required_fields'] == []
        assert any(
            f'{worker_model} is unsupported even when Socket.IO staging proof is provided' in error
            for error in report['invalid_errors']
        )


def test_main_rejects_persisted_sensitive_value(tmp_path):
    external_inputs = tmp_path / 'external-proof-inputs.json'
    values_path = tmp_path / 'external-proof-values.json'
    output = tmp_path / 'external-proof-values-status.md'
    json_output = tmp_path / 'external-proof-values-status.json'
    values = _complete_values()
    values['values']['operator_auth_token'] = 'secret-token'
    external_inputs.write_text(json.dumps(_external_inputs()), encoding='utf-8')
    values_path.write_text(json.dumps(values), encoding='utf-8')

    exit_code = main(
        [
            '--external-inputs-json',
            str(external_inputs),
            '--values',
            str(values_path),
            '--output',
            str(output),
            '--json-output',
            str(json_output),
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
        ]
    )

    payload = json.loads(json_output.read_text(encoding='utf-8'))
    assert exit_code == 2
    assert payload['status'] == 'invalid'
    assert 'values.operator_auth_token' in payload['invalid_errors'][0]


def test_main_rejects_secret_like_alias_in_values(tmp_path):
    external_inputs = tmp_path / 'external-proof-inputs.json'
    values_path = tmp_path / 'external-proof-values.json'
    output = tmp_path / 'external-proof-values-status.md'
    json_output = tmp_path / 'external-proof-values-status.json'
    values = _complete_values()
    values['values']['hosted_api_key'] = 'secret-token'
    external_inputs.write_text(json.dumps(_external_inputs()), encoding='utf-8')
    values_path.write_text(json.dumps(values), encoding='utf-8')

    exit_code = main(
        [
            '--external-inputs-json',
            str(external_inputs),
            '--values',
            str(values_path),
            '--output',
            str(output),
            '--json-output',
            str(json_output),
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
        ]
    )

    payload = json.loads(json_output.read_text(encoding='utf-8'))
    assert exit_code == 2
    assert payload['status'] == 'invalid'
    assert any('values.hosted_api_key' in error for error in payload['invalid_errors'])


def test_main_rejects_top_level_sensitive_value(tmp_path):
    external_inputs = tmp_path / 'external-proof-inputs.json'
    values_path = tmp_path / 'external-proof-values.json'
    output = tmp_path / 'external-proof-values-status.md'
    json_output = tmp_path / 'external-proof-values-status.json'
    values = _complete_values()
    values['operator_auth_token'] = 'secret-token'
    external_inputs.write_text(json.dumps(_external_inputs()), encoding='utf-8')
    values_path.write_text(json.dumps(values), encoding='utf-8')

    exit_code = main(
        [
            '--external-inputs-json',
            str(external_inputs),
            '--values',
            str(values_path),
            '--output',
            str(output),
            '--json-output',
            str(json_output),
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
        ]
    )

    payload = json.loads(json_output.read_text(encoding='utf-8'))
    assert exit_code == 2
    assert payload['status'] == 'invalid'
    assert any('operator_auth_token' in error for error in payload['invalid_errors'])


def test_complete_values_accept_source_archive_attachment_with_current_checksum(tmp_path):
    values = _complete_values()
    values['values']['source_archive_attachment_evidence'] = (
        'https://github.com/dreichner2/AIDM-main/releases/tag/rc1 sha256:' + 'a' * 64
    )
    _write_rc_evidence(tmp_path / 'rc-evidence.md')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')

    report = build_report(
        external_inputs=_external_inputs_with_source_archive_attachment(),
        values_payload=values,
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'passed'
    assert report['invalid_errors'] == []


def test_complete_values_reject_source_archive_attachment_without_checksum(tmp_path):
    values = _complete_values()
    values['values']['source_archive_attachment_evidence'] = (
        'https://github.com/dreichner2/AIDM-main/releases/tag/rc1'
    )
    _write_rc_evidence(tmp_path / 'rc-evidence.md')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')

    report = build_report(
        external_inputs=_external_inputs_with_source_archive_attachment(),
        values_payload=values,
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'invalid'
    assert any(
        'source_archive_attachment_evidence' in error
        and 'must include current source archive sha256' in error
        for error in report['invalid_errors']
    )


def test_complete_values_reject_source_archive_attachment_wrong_checksum(tmp_path):
    values = _complete_values()
    values['values']['source_archive_attachment_evidence'] = (
        'https://github.com/dreichner2/AIDM-main/releases/tag/rc1 sha256:' + 'b' * 64
    )
    _write_rc_evidence(tmp_path / 'rc-evidence.md')
    _write_operator_signoff_status(tmp_path / 'operator-signoff-status.md')

    report = build_report(
        external_inputs=_external_inputs_with_source_archive_attachment(),
        values_payload=values,
        values_present=True,
        values_path=tmp_path / 'external-proof-values.json',
        external_inputs_path=tmp_path / 'external-proof-inputs.json',
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'invalid'
    assert any('sha256 ' + 'a' * 64 in error for error in report['invalid_errors'])


def test_main_require_complete_fails_for_incomplete_values(tmp_path):
    external_inputs = tmp_path / 'external-proof-inputs.json'
    values_path = tmp_path / 'missing-external-proof-values.json'
    output = tmp_path / 'external-proof-values-status.md'
    json_output = tmp_path / 'external-proof-values-status.json'
    external_inputs.write_text(json.dumps(_external_inputs()), encoding='utf-8')

    exit_code = main(
        [
            '--external-inputs-json',
            str(external_inputs),
            '--values',
            str(values_path),
            '--output',
            str(output),
            '--json-output',
            str(json_output),
            '--require-complete',
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
        ]
    )

    assert exit_code == 1
    assert json.loads(json_output.read_text(encoding='utf-8'))['status'] == 'incomplete'
