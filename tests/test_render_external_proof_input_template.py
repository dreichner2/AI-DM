from __future__ import annotations

import json

from scripts.render_external_proof_input_template import build_template, main, render_markdown


def _packet() -> dict:
    return {
        'overall_status': 'local-ready-with-external-exceptions',
        'signed_off_worktree': {
            'status': 'dirty',
            'worktree': 'dirty (3 changed/untracked paths)',
            'commit': 'abc123',
        },
        'source_archive': {
            'status': 'passed',
            'path': '/tmp/aidm-source.tar.gz',
            'sha256': 'abc123sha',
            'bytes': 123,
        },
        'github_actions': {
            'status': 'incomplete',
            'freshness': 'stale',
            'repository': 'example/AIDM',
            'aidm_ci_run_url': 'https://github.com/example/AIDM/actions/runs/111',
            'closed_beta_rc_run_url': 'missing',
            'missing': 'Closed Beta RC run URL',
            'missing_details': {'Closed Beta RC run URL': 'No recent Closed Beta RC runs were returned by gh run list.'},
            'next_actions': ['Run the manual Closed Beta RC workflow for commit abc123.'],
        },
        'hosted_rc_evidence': {
            'status': 'planned',
            'target_url': 'https://closed-beta.example.test',
            'manual_required_count': 3,
            'manual_required': [
                'Hosted database backup/restore proof',
                'Hosted Socket.IO worker process proof',
                'Source archive attached to RC issue or release',
            ],
            'manual_evidence': [],
        },
        'operator_signoff': {
            'status': 'missing',
            'required_complete': '0/19',
            'missing_or_invalid': '19',
        },
        'frontend_npm_ci': {
            'status': 'passed',
            'path': '/tmp/frontend-npm-ci-evidence.md',
        },
        'packaging_cleanup': {
            'status': 'passed',
            'path': '/tmp/packaging-cleanup-evidence.md',
        },
    }


def _action_plan() -> dict:
    return {
        'status': 'action-required',
        'release': 'RC1',
        'commit': 'abc123',
        'target_url': 'https://<hosted-staging-target>',
        'complete_count': 2,
        'pending_count': 3,
        'actions': [
            {
                'key': 'clean_signed_off_worktree',
                'issues': '#3',
                'category': 'Release candidate freeze',
                'next_action': 'Commit the RC changes and rerun RC evidence from a clean worktree.',
                'evidence_to_record': 'Clean-worktree RC evidence report.',
                'required_inputs': ['<signed-off-commit-sha>'],
            },
            {
                'key': 'github_actions_aidm_ci',
                'issues': '#3',
                'category': 'GitHub Actions',
                'next_action': 'Record a successful AIDM CI run URL.',
                'evidence_to_record': 'AIDM CI workflow run URL.',
                'required_inputs': [],
                'context_evidence': 'https://github.com/example/AIDM/actions/runs/111',
            },
            {
                'key': 'github_actions_closed_beta_rc',
                'issues': '#3',
                'category': 'GitHub Actions',
                'next_action': 'Run the manual Closed Beta RC workflow.',
                'evidence_to_record': 'Closed Beta RC workflow run URL.',
                'required_inputs': [],
                'prerequisite': 'clean_signed_off_worktree',
            },
            {
                'key': 'hosted_deployment_readiness',
                'issues': '#3 #5 #8',
                'category': 'Hosted target',
                'next_action': 'Run deployment readiness against the hosted target.',
                'evidence_to_record': 'tmp/release/deployment-readiness-evidence.md',
                'required_inputs': ['<target-env>', '<target-url>', '<token>'],
            },
            {
                'key': 'source_archive_attachment',
                'issues': '#9',
                'category': 'Manual release proof',
                'next_action': 'Attach the generated source archive.',
                'evidence_to_record': 'Attachment URL/path plus checksum.',
                'required_inputs': [],
            },
        ],
        'complete_items': [
            {'key': 'frontend_npm_ci'},
        ],
    }


def test_build_template_lists_required_fields_and_command_templates():
    template = build_template(
        packet=_packet(),
        action_plan=_action_plan(),
        recommendation_matrix={
            'status': 'local-ready-with-external-exceptions',
            'recommendations': [
                {'key': 'github_actions_gate', 'status': 'external-required'},
                {'key': 'source_archive', 'status': 'implemented'},
            ],
        },
        generated_at='2026-06-19T00:00:00+00:00',
    )
    fields = {field['key']: field for field in template['fields']}
    commands = {command['key']: command for command in template['command_templates']}
    markdown = render_markdown(template)

    assert template['status'] == 'action-required'
    assert template['source_archive']['sha256'] == 'abc123sha'
    assert template['github_actions']['repository'] == 'example/AIDM'
    assert template['github_actions']['missing_details'] == {
        'Closed Beta RC run URL': 'No recent Closed Beta RC runs were returned by gh run list.'
    }
    assert fields['aidm_ci_run_url']['status'] == 'required'
    assert fields['aidm_ci_run_url']['current_value'] == ''
    assert fields['clean_worktree_rc_evidence']['status'] == 'required'
    assert fields['closed_beta_rc_run_url']['status'] == 'required'
    assert fields['target_url']['status'] == 'required'
    assert fields['operator_auth_token']['sensitive'] is True
    assert fields['non_admin_token']['sensitive'] is True
    assert fields['source_archive_attachment_evidence']['status'] == 'required'
    assert fields['frontend_npm_ci_evidence']['status'] == 'provided-context'
    assert fields['make_clean_evidence']['status'] == 'provided-context'
    assert fields['make_clean_deps_evidence']['status'] == 'provided-context'
    assert '--include-gh-details' in commands['github_actions_evidence']['command']
    assert '--output tmp/release/beta-slo-baseline.md' in commands['beta_slo_baseline']['command']
    assert 'make hosted-rc-evidence' in commands['hosted_rc_evidence']['command']
    assert '--hosted-backup-restore-evidence <link-or-path>' in commands['hosted_rc_evidence']['command']
    assert '--external-telemetry-receipt <link-or-path>' in commands['hosted_rc_evidence']['command']
    assert commands['external_proof_values_merge']['command'] == 'make external-proof-values-merge'
    assert 'existing operator-filled external proof values file' in commands['external_proof_values_merge']['description']
    assert template['external_recommendation_keys'] == ['github_actions_gate']
    assert '# External Proof Inputs' in markdown
    assert '| Signoff key | Issues | Category | Current context | Prerequisite | Next action | Evidence to record | Inputs |' in markdown
    assert 'https://github.com/example/AIDM/actions/runs/111' in markdown
    assert 'clean_signed_off_worktree' in markdown
    assert '`closed_beta_rc_run_url`' in markdown
    assert 'GitHub Actions missing: Closed Beta RC run URL' in markdown
    assert (
        'Freeze and push a clean signed-off candidate first; then run the manual Closed Beta RC workflow '
        'for the signed-off commit.'
    ) in markdown
    assert 'command-only sensitive value' in markdown
    assert '## Command Templates' in markdown


def test_render_markdown_keeps_commit_specific_github_action_for_clean_worktree():
    packet = _packet()
    packet['signed_off_worktree'] = {
        'status': 'passed',
        'worktree': 'clean',
        'commit': 'abc123',
    }
    template = build_template(
        packet=packet,
        action_plan=_action_plan(),
        recommendation_matrix={'status': 'local-ready-with-external-exceptions', 'recommendations': []},
        generated_at='2026-06-19T00:00:00+00:00',
    )

    markdown = render_markdown(template)

    assert '| GitHub Actions next action 1 | Run the manual Closed Beta RC workflow for commit abc123. |' in markdown


def test_render_markdown_does_not_double_contextualize_dirty_github_action():
    packet = _packet()
    packet['github_actions']['next_actions'] = [
        'Freeze and push a clean signed-off candidate first; then run the manual Closed Beta RC workflow '
        'for the signed-off commit, then rerun make github-actions-evidence.'
    ]
    template = build_template(
        packet=packet,
        action_plan=_action_plan(),
        recommendation_matrix={'status': 'local-ready-with-external-exceptions', 'recommendations': []},
        generated_at='2026-06-19T00:00:00+00:00',
    )

    markdown = render_markdown(template)

    assert markdown.count('Freeze and push a clean signed-off candidate first;') == 1
    assert 'then freeze and push' not in markdown


def test_build_template_uses_hosted_rc_aggregate_check_context():
    packet = _packet()
    packet['hosted_rc_evidence'] = {
        'status': 'manual-evidence-required',
        'target_url': 'https://aidm.closedbeta.dev',
        'generator_freshness': 'current',
        'manual_required_count': 4,
        'manual_required': ['Hosted database backup/restore proof'],
        'manual_evidence': [
            {
                'label': 'External telemetry receipt proof',
                'status': 'provided',
                'evidence': 'https://telemetry.closedbeta.dev/events/123',
            }
        ],
        'checks': {
            label: {
                'status': 'passed',
                'evidence_path': path,
                'evidence_target_url': 'https://aidm.closedbeta.dev',
                'missing_inputs': [],
                'validation_errors': [],
            }
            for label, path in {
                'Hosted deployment readiness': 'tmp/release/deployment-readiness-evidence.md',
                'Hosted cookie auth smoke': 'tmp/release/hosted-cookie-auth-evidence.md',
                'Hosted non-admin forbidden smoke': 'tmp/release/security-forbidden-evidence.md',
                'Hosted session export/import smoke': 'tmp/release/export-import-evidence.md',
                'Hosted beta SLO baseline': 'tmp/release/beta-slo-baseline.md',
            }.items()
        },
    }
    action_plan = {
        **_action_plan(),
        'target_url': 'https://aidm.closedbeta.dev',
        'actions': [
            {'key': key}
            for key in (
                'hosted_deployment_readiness',
                'hosted_cookie_auth',
                'hosted_non_admin_forbidden',
                'hosted_export_import',
                'hosted_beta_slo_baseline',
                'hosted_external_telemetry',
            )
        ],
        'complete_items': [],
    }

    template = build_template(
        packet=packet,
        action_plan=action_plan,
        recommendation_matrix={'status': 'local-ready-with-external-exceptions', 'recommendations': []},
        generated_at='2026-06-19T00:00:00+00:00',
    )
    fields = {field['key']: field for field in template['fields']}

    assert fields['target_url']['status'] == 'provided-context'
    assert fields['deployment_readiness_evidence']['current_value'] == 'tmp/release/deployment-readiness-evidence.md'
    assert fields['hosted_cookie_auth_evidence']['current_value'] == 'tmp/release/hosted-cookie-auth-evidence.md'
    assert fields['hosted_non_admin_forbidden_evidence']['current_value'] == 'tmp/release/security-forbidden-evidence.md'
    assert fields['hosted_export_import_evidence']['current_value'] == 'tmp/release/export-import-evidence.md'
    assert fields['hosted_beta_slo_baseline_evidence']['current_value'] == 'tmp/release/beta-slo-baseline.md'
    assert fields['external_telemetry_receipt']['current_value'] == 'https://telemetry.closedbeta.dev/events/123'
    assert fields['deployment_readiness_evidence']['status'] == 'provided-context'
    assert fields['hosted_cookie_auth_evidence']['status'] == 'provided-context'


def test_build_template_uses_current_passed_github_actions_context():
    packet = _packet()
    packet['signed_off_worktree'] = {
        'status': 'passed',
        'worktree': 'clean',
        'commit': 'abc123',
    }
    packet['github_actions'] = {
        'status': 'passed',
        'freshness': 'current',
        'aidm_ci_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/111',
        'closed_beta_rc_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/222',
        'closed_beta_rc_artifact_status': 'passed',
        'closed_beta_rc_artifact_content_status': 'passed',
        'closed_beta_rc_artifact_url': 'https://api.github.com/repos/dreichner2/AIDM-main/actions/artifacts/333',
    }

    template = build_template(
        packet=packet,
        action_plan=_action_plan(),
        recommendation_matrix={'status': 'local-ready-with-external-exceptions', 'recommendations': []},
        generated_at='2026-06-19T00:00:00+00:00',
    )
    fields = {field['key']: field for field in template['fields']}

    assert fields['aidm_ci_run_url']['status'] == 'provided-context'
    assert fields['aidm_ci_run_url']['current_value'] == 'https://github.com/dreichner2/AIDM-main/actions/runs/111'
    assert fields['closed_beta_rc_run_url']['status'] == 'provided-context'
    assert fields['closed_beta_rc_run_url']['current_value'] == 'https://github.com/dreichner2/AIDM-main/actions/runs/222'
    assert fields['closed_beta_rc_artifact_reference']['status'] == 'provided-context'
    assert (
        fields['closed_beta_rc_artifact_reference']['current_value']
        == 'https://api.github.com/repos/dreichner2/AIDM-main/actions/artifacts/333'
    )


def test_build_template_does_not_use_current_github_actions_context_until_signed_off():
    packet = _packet()
    packet['github_actions'] = {
        'status': 'incomplete',
        'freshness': 'current',
        'aidm_ci_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/111',
        'closed_beta_rc_run_url': 'missing',
    }

    template = build_template(
        packet=packet,
        action_plan=_action_plan(),
        recommendation_matrix={'status': 'local-ready-with-external-exceptions', 'recommendations': []},
        generated_at='2026-06-19T00:00:00+00:00',
    )
    fields = {field['key']: field for field in template['fields']}

    assert fields['aidm_ci_run_url']['status'] == 'required'
    assert fields['aidm_ci_run_url']['current_value'] == ''
    assert fields['closed_beta_rc_run_url']['status'] == 'required'
    assert fields['closed_beta_rc_run_url']['current_value'] == ''


def test_build_template_ignores_unverified_github_actions_artifact_context():
    packet = _packet()
    packet['signed_off_worktree'] = {
        'status': 'passed',
        'worktree': 'clean',
        'commit': 'abc123',
    }
    packet['github_actions'] = {
        'status': 'passed',
        'freshness': 'current',
        'closed_beta_rc_artifact_status': 'passed',
        'closed_beta_rc_artifact_content_status': 'not-checked',
        'closed_beta_rc_artifact_url': 'https://api.github.com/repos/dreichner2/AIDM-main/actions/artifacts/333',
    }
    action_plan = {
        **_action_plan(),
        'actions': [{'key': 'github_actions_rc_artifact'}],
        'complete_items': [],
    }

    template = build_template(
        packet=packet,
        action_plan=action_plan,
        recommendation_matrix={'status': 'local-ready-with-external-exceptions', 'recommendations': []},
        generated_at='2026-06-19T00:00:00+00:00',
    )
    fields = {field['key']: field for field in template['fields']}

    assert fields['closed_beta_rc_artifact_reference']['status'] == 'required'
    assert fields['closed_beta_rc_artifact_reference']['current_value'] == ''


def test_build_template_uses_partial_current_github_actions_context_after_signed_off():
    packet = _packet()
    packet['signed_off_worktree'] = {
        'status': 'passed',
        'worktree': 'clean',
        'commit': 'abc123',
    }
    packet['github_actions'] = {
        'status': 'incomplete',
        'freshness': 'current',
        'aidm_ci_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/111',
        'closed_beta_rc_run_url': 'missing',
    }

    template = build_template(
        packet=packet,
        action_plan=_action_plan(),
        recommendation_matrix={'status': 'local-ready-with-external-exceptions', 'recommendations': []},
        generated_at='2026-06-19T00:00:00+00:00',
    )
    fields = {field['key']: field for field in template['fields']}

    assert fields['aidm_ci_run_url']['status'] == 'provided-context'
    assert fields['aidm_ci_run_url']['current_value'] == 'https://github.com/dreichner2/AIDM-main/actions/runs/111'
    assert fields['closed_beta_rc_run_url']['status'] == 'required'
    assert fields['closed_beta_rc_run_url']['current_value'] == ''


def test_build_template_ignores_invalid_hosted_rc_aggregate_check_context():
    packet = _packet()
    packet['hosted_rc_evidence'] = {
        'status': 'manual-evidence-required',
        'target_url': 'https://aidm.closedbeta.dev',
        'generator_freshness': 'current',
        'checks': {
            'Hosted deployment readiness': {
                'status': 'passed',
                'evidence_path': 'tmp/release/deployment-readiness-evidence.md',
                'evidence_target_url': 'https://aidm.closedbeta.dev',
                'missing_inputs': [],
                'validation_errors': ['stale target'],
            }
        },
    }
    action_plan = {
        **_action_plan(),
        'target_url': 'https://aidm.closedbeta.dev',
        'actions': [{'key': 'hosted_deployment_readiness'}],
        'complete_items': [],
    }

    template = build_template(
        packet=packet,
        action_plan=action_plan,
        recommendation_matrix={'status': 'local-ready-with-external-exceptions', 'recommendations': []},
        generated_at='2026-06-19T00:00:00+00:00',
    )
    fields = {field['key']: field for field in template['fields']}

    assert fields['deployment_readiness_evidence']['status'] == 'required'
    assert fields['deployment_readiness_evidence']['current_value'] == ''


def test_main_writes_markdown_and_json(tmp_path):
    packet = tmp_path / 'release-evidence-packet.json'
    action_plan = tmp_path / 'operator-signoff-action-plan.json'
    matrix = tmp_path / 'rc-recommendation-matrix.json'
    output = tmp_path / 'external-proof-inputs.md'
    json_output = tmp_path / 'external-proof-inputs.json'
    packet.write_text(json.dumps(_packet()), encoding='utf-8')
    action_plan.write_text(json.dumps(_action_plan()), encoding='utf-8')
    matrix.write_text(
        json.dumps({'status': 'local-ready-with-external-exceptions', 'recommendations': []}),
        encoding='utf-8',
    )

    exit_code = main(
        [
            '--packet-json',
            str(packet),
            '--action-plan-json',
            str(action_plan),
            '--recommendation-matrix-json',
            str(matrix),
            '--output',
            str(output),
            '--json-output',
            str(json_output),
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
        ]
    )

    assert exit_code == 0
    assert '# External Proof Inputs' in output.read_text(encoding='utf-8')
    payload = json.loads(json_output.read_text(encoding='utf-8'))
    assert payload['generated_at'] == '2026-06-19T00:00:00+00:00'
    assert payload['field_counts']['required'] >= 3
