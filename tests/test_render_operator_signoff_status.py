from __future__ import annotations

import json

from scripts.render_operator_signoff_status import (
    ITEM_SPECS,
    build_action_plan,
    build_report,
    draft_manifest_from_packet,
    example_manifest,
    main,
    render_action_plan_markdown,
    render_markdown,
)


def test_missing_manifest_renders_missing_without_failing_by_default(tmp_path):
    manifest = tmp_path / 'operator-signoff.json'

    report = build_report(manifest_path=manifest, generated_at='2026-06-19T00:00:00+00:00')

    assert report['status'] == 'missing'
    assert report['manifest_present'] is False
    assert report['complete_count'] == 0
    assert report['pending_count'] == len(ITEM_SPECS)
    multi_worker = next(item for item in report['items'] if item['key'] == 'multi_worker_socketio_staging')
    assert multi_worker['status'] == 'pending'
    assert multi_worker['complete'] is False
    markdown = render_markdown(report)
    assert '# RC Operator Sign-Off Status' in markdown
    assert '- Status: missing' in markdown
    assert '`github_actions_aidm_ci`' in markdown


def test_complete_manifest_passes(tmp_path):
    manifest = example_manifest()
    manifest['commit'] = 'abc123'
    manifest['target_url'] = 'https://aidm.closedbeta.dev'
    manifest['signed_by'] = 'operator'
    manifest['signed_at'] = '2026-06-19T00:00:00+00:00'
    for key, item in manifest['items'].items():
        if key == 'multi_worker_socketio_staging':
            item['status'] = 'not_applicable'
            item['evidence'] = 'https://platform.aidm.closedbeta.dev/processes'
            item['notes'] = 'RC1 uses exactly one backend worker.'
            continue
        item['status'] = 'provided'
        item['evidence'] = f'https://evidence.aidm.closedbeta.dev/{key}'
        if key == 'source_archive_attachment':
            item['evidence'] += ' sha256:' + 'a' * 64
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(
        manifest_path=manifest_path,
        generated_at='2026-06-19T00:00:00+00:00',
        packet={'source_archive': {'sha256': 'a' * 64}},
    )

    assert report['status'] == 'passed'
    assert report['complete_count'] == len(ITEM_SPECS)
    assert report['pending_count'] == 0
    assert report['errors'] == []


def test_complete_items_do_not_pass_with_placeholder_metadata(tmp_path):
    manifest = example_manifest()
    for key, item in manifest['items'].items():
        if key == 'multi_worker_socketio_staging':
            item['status'] = 'not_applicable'
            item['evidence'] = 'https://platform.aidm.closedbeta.dev/processes'
            item['notes'] = 'RC1 uses exactly one backend worker.'
            continue
        item['status'] = 'provided'
        item['evidence'] = f'https://evidence.aidm.closedbeta.dev/{key}'
        if key == 'source_archive_attachment':
            item['evidence'] += ' sha256:' + 'a' * 64
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:00:00+00:00')

    assert report['status'] == 'invalid'
    assert report['complete_count'] == len(ITEM_SPECS)
    assert any('target_url must be a real hosted/staging URL' in error for error in report['errors'])
    assert any('commit must be the signed-off commit SHA' in error for error in report['errors'])


def test_invalid_manifest_requires_evidence_for_provided_items(tmp_path):
    manifest = example_manifest()
    manifest['items']['github_actions_aidm_ci'] = {'status': 'provided', 'evidence': ''}
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:00:00+00:00')

    assert report['status'] == 'invalid'
    assert any('github_actions_aidm_ci' in error for error in report['errors'])


def test_invalid_manifest_rejects_placeholder_or_example_evidence(tmp_path):
    manifest = example_manifest()
    manifest['commit'] = 'abc123'
    manifest['target_url'] = 'https://aidm.closedbeta.dev'
    manifest['signed_by'] = 'operator'
    manifest['signed_at'] = '2026-06-19T00:00:00+00:00'
    manifest['items']['github_actions_aidm_ci'] = {
        'status': 'provided',
        'evidence': 'https://github.com/example/AIDM/actions/runs/111',
    }
    manifest['items']['hosted_cookie_auth'] = {
        'status': 'provided',
        'evidence': 'http://localhost:5050/tmp/release/hosted-cookie-auth-evidence.md',
    }
    manifest['items']['source_archive_attachment'] = {
        'status': 'provided',
        'evidence': '<release-artifact-url>',
    }
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:00:00+00:00')

    assert report['status'] == 'invalid'
    assert any('github_actions_aidm_ci' in error and 'example' in error for error in report['errors'])
    assert any('hosted_cookie_auth' in error and 'localhost' in error for error in report['errors'])
    assert any('source_archive_attachment' in error and 'placeholder' in error for error in report['errors'])


def test_invalid_manifest_rejects_source_archive_attachment_without_checksum(tmp_path):
    manifest = example_manifest()
    manifest['commit'] = 'abc123'
    manifest['target_url'] = 'https://aidm.closedbeta.dev'
    manifest['signed_by'] = 'operator'
    manifest['signed_at'] = '2026-06-19T00:00:00+00:00'
    manifest['items']['source_archive_attachment'] = {
        'status': 'provided',
        'evidence': 'https://github.com/dreichner2/AIDM-main/releases/tag/rc1',
    }
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:00:00+00:00')

    assert report['status'] == 'invalid'
    assert any(
        'source_archive_attachment' in error and 'SHA256 checksum' in error
        for error in report['errors']
    )


def test_invalid_manifest_rejects_source_archive_attachment_with_wrong_packet_checksum(tmp_path):
    expected_sha256 = 'a' * 64
    manifest = example_manifest()
    manifest['commit'] = 'abc123'
    manifest['target_url'] = 'https://aidm.closedbeta.dev'
    manifest['signed_by'] = 'operator'
    manifest['signed_at'] = '2026-06-19T00:00:00+00:00'
    manifest['items']['source_archive_attachment'] = {
        'status': 'provided',
        'evidence': 'https://github.com/dreichner2/AIDM-main/releases/tag/rc1 sha256:' + 'b' * 64,
    }
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(
        manifest_path=manifest_path,
        generated_at='2026-06-19T00:00:00+00:00',
        packet={'source_archive': {'sha256': expected_sha256}},
    )

    assert report['status'] == 'invalid'
    assert report['source_archive_sha256'] == expected_sha256
    assert any(
        'source_archive_attachment' in error
        and 'current source archive sha256' in error
        and expected_sha256 in error
        for error in report['errors']
    )


def test_main_validates_source_archive_attachment_against_packet_checksum(tmp_path):
    expected_sha256 = 'a' * 64
    manifest = example_manifest()
    manifest['commit'] = 'abc123'
    manifest['target_url'] = 'https://aidm.closedbeta.dev'
    manifest['signed_by'] = 'operator'
    manifest['signed_at'] = '2026-06-19T00:00:00+00:00'
    manifest['items']['source_archive_attachment'] = {
        'status': 'provided',
        'evidence': 'https://github.com/dreichner2/AIDM-main/releases/tag/rc1 sha256:' + 'b' * 64,
    }
    manifest_path = tmp_path / 'operator-signoff.json'
    packet_path = tmp_path / 'release-evidence-packet.json'
    output = tmp_path / 'operator-signoff-status.md'
    json_output = tmp_path / 'operator-signoff-status.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    packet_path.write_text(json.dumps({'source_archive': {'sha256': expected_sha256}}), encoding='utf-8')

    exit_code = main(
        [
            '--manifest',
            str(manifest_path),
            '--packet-json',
            str(packet_path),
            '--output',
            str(output),
            '--json-output',
            str(json_output),
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
        ]
    )

    assert exit_code == 1
    assert f'- Source archive SHA256: `{expected_sha256}`' in output.read_text(encoding='utf-8')
    payload = json.loads(json_output.read_text(encoding='utf-8'))
    assert payload['status'] == 'invalid'
    assert payload['source_archive_sha256'] == expected_sha256


def test_invalid_manifest_requires_worker_evidence_for_multi_worker_not_applicable(tmp_path):
    manifest = example_manifest()
    manifest['commit'] = 'abc123'
    manifest['target_url'] = 'https://aidm.closedbeta.dev'
    manifest['signed_by'] = 'operator'
    manifest['signed_at'] = '2026-06-19T00:00:00+00:00'
    manifest['items']['multi_worker_socketio_staging'] = {
        'status': 'not_applicable',
        'evidence': '',
        'notes': 'RC1 uses exactly one backend worker.',
    }
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:00:00+00:00')

    assert report['status'] == 'invalid'
    assert any(
        'multi_worker_socketio_staging' in error and 'hosted worker-process evidence' in error
        for error in report['errors']
    )


def test_invalid_manifest_rejects_dirty_clean_worktree_evidence(tmp_path):
    evidence_path = tmp_path / 'rc-evidence.md'
    evidence_path.write_text(
        '\n'.join(
            [
                '# Closed Beta RC Evidence',
                '',
                '- Status: passed',
                '- Commit: abc123',
                '- Worktree: dirty (3 changed/untracked paths)',
                '',
            ]
        ),
        encoding='utf-8',
    )
    manifest = example_manifest()
    manifest['commit'] = 'abc123'
    manifest['target_url'] = 'https://aidm.closedbeta.dev'
    manifest['signed_by'] = 'operator'
    manifest['signed_at'] = '2026-06-19T00:00:00+00:00'
    manifest['items']['clean_signed_off_worktree'] = {
        'status': 'provided',
        'evidence': str(evidence_path),
    }
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:01:00+00:00')

    assert report['status'] == 'invalid'
    assert any('clean_signed_off_worktree' in error and 'worktree is not clean' in error for error in report['errors'])


def test_invalid_manifest_rejects_local_evidence_for_wrong_target_url(tmp_path):
    evidence_path = tmp_path / 'hosted-cookie-auth-evidence.md'
    evidence_path.write_text(
        '\n'.join(
            [
                '# Hosted Cookie Auth Evidence',
                '',
                '- Status: passed',
                '- Mode: live-target',
                '- Target URL: `https://other.closedbeta.dev`',
                '',
            ]
        ),
        encoding='utf-8',
    )
    manifest = example_manifest()
    manifest['commit'] = 'abc123'
    manifest['target_url'] = 'https://aidm.closedbeta.dev'
    manifest['signed_by'] = 'operator'
    manifest['signed_at'] = '2026-06-19T00:00:00+00:00'
    manifest['items']['hosted_cookie_auth'] = {
        'status': 'provided',
        'evidence': str(evidence_path),
    }
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:00:00+00:00')

    assert report['status'] == 'invalid'
    assert any('hosted_cookie_auth' in error and 'does not match manifest target_url' in error for error in report['errors'])


def test_invalid_manifest_rejects_missing_local_hosted_evidence_path(tmp_path):
    manifest = example_manifest()
    manifest['commit'] = 'abc123'
    manifest['target_url'] = 'https://aidm.closedbeta.dev'
    manifest['signed_by'] = 'operator'
    manifest['signed_at'] = '2026-06-19T00:00:00+00:00'
    manifest['items']['hosted_cookie_auth'] = {
        'status': 'provided',
        'evidence': 'tmp/release/hosted-cookie-auth-evidence.md',
    }
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:00:00+00:00')

    assert report['status'] == 'invalid'
    assert any('hosted_cookie_auth' in error and 'path does not exist' in error for error in report['errors'])


def test_invalid_manifest_rejects_local_hosted_evidence_without_target_url(tmp_path):
    evidence_path = tmp_path / 'deployment-readiness-evidence.md'
    evidence_path.write_text('- Status: passed\n', encoding='utf-8')
    manifest = example_manifest()
    manifest['commit'] = 'abc123'
    manifest['target_url'] = 'https://aidm.closedbeta.dev'
    manifest['signed_by'] = 'operator'
    manifest['signed_at'] = '2026-06-19T00:00:00+00:00'
    manifest['items']['hosted_deployment_readiness'] = {
        'status': 'provided',
        'evidence': str(evidence_path),
    }
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:00:00+00:00')

    assert report['status'] == 'invalid'
    assert any('hosted_deployment_readiness' in error and 'missing target_url' in error for error in report['errors'])


def test_invalid_manifest_rejects_local_evidence_for_local_target_url(tmp_path):
    evidence_path = tmp_path / 'deployment-readiness-evidence.json'
    evidence_path.write_text(
        json.dumps({'status': 'passed', 'options': {'target_url': 'http://127.0.0.1:5050'}}),
        encoding='utf-8',
    )
    manifest = example_manifest()
    manifest['commit'] = 'abc123'
    manifest['target_url'] = 'https://aidm.closedbeta.dev'
    manifest['signed_by'] = 'operator'
    manifest['signed_at'] = '2026-06-19T00:00:00+00:00'
    manifest['items']['hosted_deployment_readiness'] = {
        'status': 'provided',
        'evidence': str(evidence_path),
    }
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:00:00+00:00')

    assert report['status'] == 'invalid'
    assert any('hosted_deployment_readiness' in error and 'not hosted/staging' in error for error in report['errors'])


def test_invalid_manifest_rejects_local_evidence_with_failed_status(tmp_path):
    evidence_path = tmp_path / 'deployment-readiness-evidence.md'
    evidence_path.write_text(
        '\n'.join(
            [
                '# Deployment Readiness Evidence',
                '',
                '- Status: failed',
                '- Target URL: `https://aidm.closedbeta.dev`',
                '',
            ]
        ),
        encoding='utf-8',
    )
    manifest = example_manifest()
    manifest['commit'] = 'abc123'
    manifest['target_url'] = 'https://aidm.closedbeta.dev'
    manifest['signed_by'] = 'operator'
    manifest['signed_at'] = '2026-06-19T00:00:00+00:00'
    manifest['items']['hosted_deployment_readiness'] = {
        'status': 'provided',
        'evidence': str(evidence_path),
    }
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:00:00+00:00')

    assert report['status'] == 'invalid'
    assert any('hosted_deployment_readiness' in error and 'status is not passed' in error for error in report['errors'])


def test_invalid_manifest_rejects_local_smoke_evidence_without_live_target_mode(tmp_path):
    evidence_path = tmp_path / 'hosted-cookie-auth-evidence.md'
    evidence_path.write_text(
        '\n'.join(
            [
                '# Hosted Cookie Auth Evidence',
                '',
                '- Status: passed',
                '- Mode: isolated',
                '- Target URL: `https://aidm.closedbeta.dev`',
                '',
            ]
        ),
        encoding='utf-8',
    )
    manifest = example_manifest()
    manifest['commit'] = 'abc123'
    manifest['target_url'] = 'https://aidm.closedbeta.dev'
    manifest['signed_by'] = 'operator'
    manifest['signed_at'] = '2026-06-19T00:00:00+00:00'
    manifest['items']['hosted_cookie_auth'] = {
        'status': 'provided',
        'evidence': str(evidence_path),
    }
    manifest_path = tmp_path / 'operator-signoff.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = build_report(manifest_path=manifest_path, generated_at='2026-06-19T00:00:00+00:00')

    assert report['status'] == 'invalid'
    assert any('hosted_cookie_auth' in error and 'mode is not live-target' in error for error in report['errors'])


def test_main_writes_report_json_and_honors_require_complete(tmp_path):
    manifest_path = tmp_path / 'operator-signoff.json'
    output = tmp_path / 'operator-signoff-status.md'
    json_output = tmp_path / 'operator-signoff-status.json'

    exit_code = main(
        [
            '--manifest',
            str(manifest_path),
            '--output',
            str(output),
            '--json-output',
            str(json_output),
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
        ]
    )

    assert exit_code == 0
    assert '- Status: missing' in output.read_text(encoding='utf-8')
    payload = json.loads(json_output.read_text(encoding='utf-8'))
    assert payload['status'] == 'missing'

    strict_exit = main(
        [
            '--manifest',
            str(manifest_path),
            '--output',
            str(output),
            '--json-output',
            str(json_output),
            '--require-complete',
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
        ]
    )

    assert strict_exit == 1


def test_main_writes_template(tmp_path):
    template = tmp_path / 'operator-signoff.example.json'

    exit_code = main(['--write-template', str(template)])

    assert exit_code == 0
    payload = json.loads(template.read_text(encoding='utf-8'))
    assert set(payload['items']) == {spec.key for spec in ITEM_SPECS}
    assert payload['items']['multi_worker_socketio_staging']['status'] == 'not_applicable'


def test_draft_manifest_does_not_seed_incomplete_github_actions_as_provided():
    packet = {
        'rc_evidence': {'commit': 'abc123'},
        'signed_off_worktree': {'commit': 'abc123', 'status': 'dirty'},
        'github_actions': {
            'status': 'incomplete',
            'freshness': 'stale',
            'aidm_ci_run_url': 'https://github.com/example/AIDM/actions/runs/111',
            'closed_beta_rc_run_url': 'missing',
        },
        'frontend_npm_ci': {
            'status': 'passed',
            'freshness': 'current',
            'path': '/tmp/frontend-npm-ci-evidence.md',
        },
        'packaging_cleanup': {
            'status': 'passed',
            'freshness': 'current',
            'path': '/tmp/packaging-cleanup-evidence.md',
        },
        'deployment_readiness': {
            'status': 'env-only',
            'target_url': 'not checked',
            'path': '/tmp/deployment-readiness-evidence.md',
        },
        'hosted_cookie_auth': {
            'status': 'passed',
            'mode': 'isolated',
            'target_url': 'isolated local runtime',
            'path': '/tmp/hosted-cookie-auth-evidence.md',
        },
        'security_forbidden': {
            'status': 'passed',
            'mode': 'isolated',
            'target_url': 'isolated local runtime',
            'path': '/tmp/security-forbidden-evidence.md',
        },
        'export_import': {
            'status': 'passed',
            'mode': 'isolated',
            'target_url': 'isolated local runtime',
            'path': '/tmp/export-import-evidence.md',
        },
        'beta_slo_baseline': {
            'status': 'local-only',
            'target_url': 'isolated local runtime',
            'path': '/tmp/beta-slo-baseline.md',
        },
        'hosted_rc_evidence': {
            'status': 'planned',
            'target_url': 'https://closed-beta.example.test',
            'metadata': {'release': 'RC1', 'socket_io_worker_model': 'single'},
            'manual_evidence': [],
        },
    }

    draft = draft_manifest_from_packet(packet, generated_at='2026-06-19T00:00:00+00:00')
    items = draft['items']

    assert draft['commit'] == 'abc123'
    assert draft['target_url'] == 'https://<hosted-staging-target>'
    assert items['clean_signed_off_worktree']['status'] == 'pending'
    assert items['github_actions_aidm_ci']['status'] == 'pending'
    assert items['frontend_npm_ci']['status'] == 'provided'
    assert items['frontend_npm_ci']['evidence'] == '/tmp/frontend-npm-ci-evidence.md'
    assert items['make_clean']['status'] == 'provided'
    assert items['make_clean']['evidence'] == '/tmp/packaging-cleanup-evidence.md'
    assert items['make_clean_deps']['status'] == 'provided'
    assert items['make_clean_deps']['evidence'] == '/tmp/packaging-cleanup-evidence.md'
    assert items['github_actions_closed_beta_rc']['status'] == 'pending'
    assert items['hosted_cookie_auth']['status'] == 'pending'
    assert items['hosted_non_admin_forbidden']['status'] == 'pending'
    assert items['hosted_export_import']['status'] == 'pending'
    assert items['hosted_beta_slo_baseline']['status'] == 'pending'
    assert items['source_archive_attachment']['status'] == 'pending'
    assert items['multi_worker_socketio_staging']['status'] == 'pending'


def test_draft_manifest_does_not_seed_github_actions_from_dirty_worktree():
    packet = {
        'rc_evidence': {'commit': 'abc123'},
        'signed_off_worktree': {'commit': 'abc123', 'status': 'dirty', 'worktree': 'dirty (3 files)'},
        'github_actions': {
            'status': 'passed',
            'freshness': 'current',
            'aidm_ci_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/111',
            'closed_beta_rc_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/222',
            'closed_beta_rc_artifact_status': 'passed',
            'closed_beta_rc_artifact_content_status': 'passed',
            'closed_beta_rc_artifact_url': 'https://api.github.com/repos/dreichner2/AIDM-main/actions/artifacts/333',
        },
    }

    draft = draft_manifest_from_packet(packet, generated_at='2026-06-19T00:00:00+00:00')
    items = draft['items']

    assert draft['commit'] == 'abc123'
    assert items['clean_signed_off_worktree']['status'] == 'pending'
    assert items['github_actions_aidm_ci']['status'] == 'pending'
    assert items['github_actions_closed_beta_rc']['status'] == 'pending'
    assert items['github_actions_rc_artifact']['status'] == 'pending'


def test_draft_manifest_seeds_clean_signed_off_worktree_evidence():
    packet = {
        'rc_evidence': {
            'commit': 'abc123',
            'status': 'passed',
            'path': '/tmp/rc-evidence.md',
        },
        'signed_off_worktree': {'commit': 'abc123', 'status': 'passed', 'worktree': 'clean'},
    }

    draft = draft_manifest_from_packet(packet, generated_at='2026-06-19T00:00:00+00:00')
    item = draft['items']['clean_signed_off_worktree']

    assert draft['commit'] == 'abc123'
    assert item['status'] == 'provided'
    assert item['evidence'] == '/tmp/rc-evidence.md'
    assert 'clean signed-off worktree' in item['notes']


def test_draft_manifest_seeds_live_hosted_and_manual_evidence():
    target_url = 'https://aidm.closedbeta.dev'
    packet = {
        'rc_evidence': {'commit': 'abc123'},
        'github_actions': {
            'status': 'passed',
            'freshness': 'current',
            'aidm_ci_run_url': 'https://github.com/example/AIDM/actions/runs/111',
            'closed_beta_rc_run_url': 'https://github.com/example/AIDM/actions/runs/222',
            'closed_beta_rc_artifact_status': 'passed',
            'closed_beta_rc_artifact_content_status': 'passed',
            'closed_beta_rc_artifact_url': 'https://api.github.com/repos/dreichner2/AIDM-main/actions/artifacts/333',
        },
        'deployment_readiness': {
            'status': 'passed',
            'target_url': target_url,
            'path': '/tmp/deployment-readiness-evidence.md',
        },
        'hosted_cookie_auth': {
            'status': 'passed',
            'mode': 'live-target',
            'target_url': target_url,
            'path': '/tmp/hosted-cookie-auth-evidence.md',
        },
        'security_forbidden': {
            'status': 'passed',
            'mode': 'live-target',
            'target_url': target_url,
            'path': '/tmp/security-forbidden-evidence.md',
        },
        'export_import': {
            'status': 'passed',
            'mode': 'live-target',
            'target_url': target_url,
            'path': '/tmp/export-import-evidence.md',
        },
        'beta_slo_baseline': {
            'status': 'present',
            'target_url': target_url,
            'path': '/tmp/beta-slo-baseline.md',
        },
        'hosted_rc_evidence': {
            'status': 'passed',
            'target_url': target_url,
            'metadata': {'release': 'RC1', 'socket_io_worker_model': 'single'},
            'manual_evidence': [
                {
                    'label': 'Hosted database backup/restore proof',
                    'status': 'provided',
                    'evidence': 'backup-link',
                },
                {
                    'label': 'Hosted Socket.IO worker process proof',
                    'status': 'provided',
                    'evidence': 'worker-link',
                },
                {
                    'label': 'Source archive attached to RC issue or release',
                    'status': 'provided',
                    'evidence': 'archive-link',
                },
            ],
        },
    }

    draft = draft_manifest_from_packet(packet, generated_at='2026-06-19T00:00:00+00:00')
    items = draft['items']

    assert draft['target_url'] == target_url
    assert items['github_actions_closed_beta_rc']['status'] == 'provided'
    assert items['hosted_env_config']['status'] == 'provided'
    assert items['hosted_deployment_readiness']['status'] == 'provided'
    assert items['hosted_cookie_auth']['status'] == 'provided'
    assert items['hosted_non_admin_forbidden']['status'] == 'provided'
    assert items['hosted_export_import']['status'] == 'provided'
    assert items['hosted_beta_slo_baseline']['status'] == 'provided'
    assert items['hosted_backup_restore']['evidence'] == 'backup-link'
    assert items['hosted_socketio_worker_process']['evidence'] == 'worker-link'
    assert items['source_archive_attachment']['evidence'] == 'archive-link'
    assert items['multi_worker_socketio_staging']['status'] == 'not_applicable'
    assert items['multi_worker_socketio_staging']['evidence'] == 'worker-link'
    assert items['github_actions_rc_artifact']['status'] == 'provided'
    assert (
        items['github_actions_rc_artifact']['evidence']
        == 'https://api.github.com/repos/dreichner2/AIDM-main/actions/artifacts/333'
    )
    assert items['hosted_external_telemetry']['status'] == 'pending'
    assert items['rc_issue_closure_review']['status'] == 'pending'


def test_draft_manifest_does_not_seed_unverified_rc_artifact():
    packet = {
        'rc_evidence': {'commit': 'abc123'},
        'signed_off_worktree': {'commit': 'abc123', 'status': 'passed', 'worktree': 'clean'},
        'github_actions': {
            'status': 'passed',
            'freshness': 'current',
            'closed_beta_rc_artifact_status': 'passed',
            'closed_beta_rc_artifact_content_status': 'not-checked',
            'closed_beta_rc_artifact_url': 'https://api.github.com/repos/dreichner2/AIDM-main/actions/artifacts/333',
        },
    }

    draft = draft_manifest_from_packet(packet, generated_at='2026-06-19T00:00:00+00:00')

    assert draft['items']['github_actions_rc_artifact']['status'] == 'pending'


def test_main_writes_draft_from_packet(tmp_path):
    packet = tmp_path / 'release-evidence-packet.json'
    draft_path = tmp_path / 'operator-signoff.draft.json'
    packet.write_text(
        json.dumps(
            {
                'rc_evidence': {'commit': 'abc123'},
                'github_actions': {
                    'status': 'passed',
                    'aidm_ci_run_url': 'https://github.com/example/AIDM/actions/runs/111',
                },
            }
        ),
        encoding='utf-8',
    )

    exit_code = main(
        [
            '--write-draft-from-packet',
            str(packet),
            '--draft-output',
            str(draft_path),
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
        ]
    )

    assert exit_code == 0
    payload = json.loads(draft_path.read_text(encoding='utf-8'))
    assert payload['draft_generated_at'] == '2026-06-19T00:00:00+00:00'
    assert payload['items']['github_actions_aidm_ci']['status'] == 'provided'


def test_action_plan_lists_pending_signoff_work_and_source_archive_context(tmp_path):
    packet = {
        'rc_evidence': {'commit': 'abc123'},
        'signed_off_worktree': {'commit': 'abc123', 'status': 'dirty', 'worktree': 'dirty (3 files)'},
        'github_actions': {
            'status': 'passed',
            'freshness': 'current',
            'aidm_ci_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/111',
        },
        'source_archive': {
            'path': '/tmp/aidm-source.tar.gz',
            'sha256': 'abc123sha',
        },
    }
    manifest = draft_manifest_from_packet(packet, generated_at='2026-06-19T00:00:00+00:00')

    plan = build_action_plan(
        manifest=manifest,
        manifest_path=tmp_path / 'operator-signoff.draft.json',
        packet=packet,
        generated_at='2026-06-19T00:01:00+00:00',
    )
    markdown = render_action_plan_markdown(plan)

    assert plan['status'] == 'action-required'
    assert plan['complete_count'] == 0
    assert plan['pending_count'] == len(ITEM_SPECS)
    assert plan['signed_off_worktree'] == {
        'status': 'dirty',
        'worktree': 'dirty (3 files)',
        'commit': 'abc123',
    }
    pending_keys = {item['key'] for item in plan['actions']}
    complete_keys = {item['key'] for item in plan['complete_items']}
    assert complete_keys == set()
    assert 'clean_signed_off_worktree' in pending_keys
    assert 'github_actions_aidm_ci' in pending_keys
    assert 'frontend_npm_ci' in pending_keys
    assert 'hosted_deployment_readiness' in pending_keys
    aidm_ci = next(item for item in plan['actions'] if item['key'] == 'github_actions_aidm_ci')
    assert aidm_ci['context_evidence'] == 'https://github.com/dreichner2/AIDM-main/actions/runs/111'
    assert 'final signoff still requires a successful AIDM CI run' in aidm_ci['next_action']
    assert 'context: https://github.com/dreichner2/AIDM-main/actions/runs/111' in markdown
    closed_beta_rc = next(item for item in plan['actions'] if item['key'] == 'github_actions_closed_beta_rc')
    assert closed_beta_rc['prerequisite'] == 'clean_signed_off_worktree'
    assert closed_beta_rc['next_action'].startswith(
        'Freeze and push a clean signed-off candidate before this GitHub Actions proof; then run the manual'
    )
    assert '--include-gh-details' in closed_beta_rc['command']
    rc_artifact = next(item for item in plan['actions'] if item['key'] == 'github_actions_rc_artifact')
    assert rc_artifact['prerequisite'] == 'clean_signed_off_worktree'
    assert 'prerequisite: clean_signed_off_worktree' in markdown
    deployment = next(item for item in plan['actions'] if item['key'] == 'hosted_deployment_readiness')
    assert 'make deployment-readiness' in deployment['command']
    assert '<target-url>' in deployment['required_inputs']
    archive = next(item for item in plan['actions'] if item['key'] == 'source_archive_attachment')
    assert 'sha256:abc123sha' in archive['next_action']
    assert '# RC Operator Sign-Off Action Plan' in markdown
    assert '- Signed-off worktree: dirty; dirty (3 files)' in markdown
    assert '`clean_signed_off_worktree`' in markdown
    assert '`hosted_deployment_readiness`' in markdown


def test_main_writes_action_plan_from_draft_and_packet(tmp_path):
    packet_path = tmp_path / 'release-evidence-packet.json'
    draft_path = tmp_path / 'operator-signoff.draft.json'
    output = tmp_path / 'operator-signoff-action-plan.md'
    json_output = tmp_path / 'operator-signoff-action-plan.json'
    packet = {
        'rc_evidence': {'commit': 'abc123'},
        'github_actions': {
            'status': 'passed',
            'freshness': 'current',
            'aidm_ci_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/111',
        },
    }
    draft = draft_manifest_from_packet(packet, generated_at='2026-06-19T00:00:00+00:00')
    packet_path.write_text(json.dumps(packet), encoding='utf-8')
    draft_path.write_text(json.dumps(draft), encoding='utf-8')

    exit_code = main(
        [
            '--write-action-plan',
            '--action-plan-manifest',
            str(draft_path),
            '--action-plan-packet',
            str(packet_path),
            '--action-plan-output',
            str(output),
            '--action-plan-json-output',
            str(json_output),
            '--generated-at',
            '2026-06-19T00:01:00+00:00',
        ]
    )

    assert exit_code == 0
    assert '# RC Operator Sign-Off Action Plan' in output.read_text(encoding='utf-8')
    payload = json.loads(json_output.read_text(encoding='utf-8'))
    assert payload['complete_count'] == 2
    assert payload['pending_count'] == len(ITEM_SPECS) - 2
