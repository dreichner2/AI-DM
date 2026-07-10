from __future__ import annotations

import json
from pathlib import Path

from scripts import render_release_checklist_status as checklist_status
from scripts.render_release_checklist_status import build_status_report, main, parse_checklist, render_markdown


def _write_packet(path: Path, *, hosted_ready: bool = False) -> Path:
    archive_path = path.parent / 'aidm-source-test.tar.gz'
    archive_path.write_bytes(b'archive')
    (path.parent / 'aidm-source-test.tar.gz.sha256').write_text('sha  aidm-source-test.tar.gz\n', encoding='utf-8')
    packet = {
        'overall_status': 'ready-for-issue-closure' if hosted_ready else 'local-ready-with-external-exceptions',
        'rc_evidence': {
            'status': 'passed',
            'commands': [
                {'label': 'Backend tests', 'status': 'passed'},
                {'label': 'Frontend tests', 'status': 'passed'},
                {'label': 'Frontend build', 'status': 'passed'},
                {'label': 'SQLite backup/restore drill', 'status': 'passed'},
                {'label': 'Source archive clean', 'status': 'passed'},
                {'label': 'Local beta SLO baseline', 'status': 'passed'},
                {'label': 'Socket.IO worker model decision', 'status': 'passed'},
            ],
        },
        'signed_off_worktree': {
            'status': 'passed' if hosted_ready else 'dirty',
            'worktree': 'clean' if hosted_ready else 'dirty (3 changed/untracked paths; 2 tracked, 1 untracked)',
            'commit': 'abc123',
        },
        'issue_evidence': {
            'status': 'passed' if hosted_ready else 'passed with external exceptions',
            'path': str(path.parent / 'issue-evidence'),
        },
        'rc_issue_closure_evidence': {
            'status': 'passed' if hosted_ready else 'external-required',
            'path': str(path.parent / 'rc-issue-closure-evidence.md'),
            'complete': '7/7' if hosted_ready else '0/7',
            'open_issues': '0' if hosted_ready else '7',
        },
        'source_archive': {
            'status': 'passed',
            'path': str(archive_path),
            'sha256': 'sha',
        },
        'github_actions': {
            'status': 'passed' if hosted_ready else 'incomplete',
            'path': str(path.parent / 'github-actions-evidence.md'),
            'aidm_ci_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/123'
            if hosted_ready
            else '',
            'closed_beta_rc_run_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/456'
            if hosted_ready
            else '',
            'closed_beta_rc_artifact_status': 'passed' if hosted_ready else 'not-checked',
            'closed_beta_rc_artifact_content_status': 'passed' if hosted_ready else 'not-checked',
            'closed_beta_rc_artifact_name': 'closed-beta-rc-evidence' if hosted_ready else '',
            'closed_beta_rc_artifact_url': 'https://github.com/dreichner2/AIDM-main/actions/runs/456/artifacts/789'
            if hosted_ready
            else '',
            'closed_beta_rc_artifact': {
                'status': 'passed' if hosted_ready else 'not-checked',
                'content_status': 'passed' if hosted_ready else 'not-checked',
                'expected_name': 'closed-beta-rc-evidence',
                'name': 'closed-beta-rc-evidence' if hosted_ready else '',
                'url': 'https://github.com/dreichner2/AIDM-main/actions/runs/456/artifacts/789'
                if hosted_ready
                else '',
            },
        },
        'hosted_rc_evidence': {
            'status': 'passed' if hosted_ready else 'planned',
            'path': str(path.parent / 'hosted-rc-evidence.md'),
            'manual_required_count': 0 if hosted_ready else 3,
            'manual_required': [] if hosted_ready else ['Source archive attached to RC issue or release'],
            'metadata': {
                'socket_io_worker_model': 'single',
                'socket_io_staging_proof': 'missing',
            },
            'checks': {
                'Hosted deployment readiness': {'status': 'passed' if hosted_ready else 'planned'},
                'Hosted beta SLO baseline': {'status': 'passed' if hosted_ready else 'planned'},
            },
        },
        'operator_signoff': {
            'status': 'passed' if hosted_ready else 'incomplete',
            'path': str(path.parent / 'operator-signoff-status.md'),
            'required_complete': '19/19' if hosted_ready else '4/19',
            'missing_or_invalid': '0' if hosted_ready else '15',
        },
        'deployment_readiness': {
            'status': 'passed' if hosted_ready else 'env-only',
            'path': str(path.parent / 'deployment-readiness-evidence.md'),
            'target_url': 'https://aidm.example.test' if hosted_ready else 'not checked',
            'metadata': {
                'socket_io_staging_proof_provided': 'False',
            },
        },
        'beta_slo_baseline': {
            'status': 'present' if hosted_ready else 'local-only',
            'path': str(path.parent / 'beta-slo-baseline.md'),
            'target_url': 'https://aidm.example.test' if hosted_ready else 'isolated local runtime',
        },
        'frontend_npm_ci': {
            'status': 'passed' if hosted_ready else 'missing',
            'path': str(path.parent / 'frontend-npm-ci-evidence.md'),
            'command': 'npm ci',
            'return_code': '0' if hosted_ready else '',
        },
        'packaging_cleanup': {
            'status': 'passed' if hosted_ready else 'missing',
            'path': str(path.parent / 'packaging-cleanup-evidence.md'),
            'source_archive_status': 'passed' if hosted_ready else '',
            'forbidden_paths': '0' if hosted_ready else '',
        },
        'release_artifact_consistency': {
            'status': 'passed' if hosted_ready else 'missing',
            'path': str(path.parent / 'release-artifact-consistency.md'),
            'json_path': str(path.parent / 'release-artifact-consistency.json'),
            'source_archive_sha256': 'sha' if hosted_ready else '',
            'check_count': 17 if hosted_ready else 0,
            'error_count': 0,
        },
        'hosted_cookie_auth': {
            'status': 'passed',
            'path': str(path.parent / 'hosted-cookie-auth-evidence.md'),
            'mode': 'live-target' if hosted_ready else 'isolated',
            'target_url': 'https://aidm.example.test' if hosted_ready else 'isolated local runtime',
        },
        'security_forbidden': {
            'status': 'passed',
            'path': str(path.parent / 'security-forbidden-evidence.md'),
            'mode': 'live-target' if hosted_ready else 'isolated',
            'target_url': 'https://aidm.example.test' if hosted_ready else 'isolated local runtime',
        },
        'export_import': {
            'status': 'passed',
            'path': str(path.parent / 'export-import-evidence.md'),
            'mode': 'live-target' if hosted_ready else 'isolated',
            'target_url': 'https://aidm.example.test' if hosted_ready else 'isolated local runtime',
        },
        'visual_smoke': {'status': 'passed', 'path': str(path.parent / 'visual-smoke')},
        'visual_smoke_review': {'status': 'passed', 'path': str(path.parent / 'visual-smoke-review.md')},
        'beta_tester_onboarding': {
            'status': 'passed',
            'path': str(path.parent / 'docs' / 'beta_tester_onboarding.md'),
        },
    }
    path.write_text(json.dumps(packet), encoding='utf-8')
    return path


def test_parse_checklist_extracts_sections_and_line_numbers(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '# Release\n\n## Preflight\n- [ ] Backend tests pass.\n## Packaging\n- [ ] `make source-archive` creates an archive.\n',
        encoding='utf-8',
    )

    items = parse_checklist(checklist)

    assert [item.section for item in items] == ['Preflight', 'Packaging']
    assert items[0].line_number == 4
    assert items[1].text == '`make source-archive` creates an archive.'


def test_build_status_report_separates_local_passes_from_external_proof(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '# Release',
                '',
                '## Preflight',
                '- [ ] `.venv/bin/python -m pytest` passes.',
                '- [ ] RC evidence is generated from a clean signed-off commit/worktree before final issue closure.',
                '- [ ] GitHub Actions `AIDM CI` passes.',
                '- [ ] `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--target-url <target-url>"` passes for the hosted/staging target.',
                '- [ ] `make operator-signoff-status OPERATOR_SIGNOFF_STATUS_ARGS="--require-complete"` passes before RC issue closure.',
                '## Packaging',
                '- [ ] The source archive has a matching `.sha256` sidecar.',
                '- [ ] The source archive is attached to the RC issue or release.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=False)

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    by_item = {item['item']: item for item in report['items']}
    assert by_item['`.venv/bin/python -m pytest` passes.']['status'] == 'passed'
    assert by_item['RC evidence is generated from a clean signed-off commit/worktree before final issue closure.']['status'] == 'external-required'
    assert by_item['GitHub Actions `AIDM CI` passes.']['status'] == 'external-required'
    assert by_item['`make operator-signoff-status OPERATOR_SIGNOFF_STATUS_ARGS="--require-complete"` passes before RC issue closure.']['status'] == 'external-required'
    assert by_item['The source archive has a matching `.sha256` sidecar.']['status'] == 'passed'
    assert by_item['The source archive is attached to the RC issue or release.']['status'] == 'external-required'
    assert report['counts']['external-required'] == 5

    markdown = render_markdown(report)
    assert '# Release Checklist Status' in markdown
    assert '| external-required | 5 |' in markdown
    assert '## Remaining Items' in markdown


def test_build_status_report_maps_database_upgrade_and_single_worker_process_proof(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Preflight',
                '- [ ] `make db-upgrade` applies cleanly.',
                '## Runtime Quality',
                '- [ ] Deployment evidence proves exactly one backend process/replica; process-local state remains single-worker.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=False)
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    packet['rc_evidence']['commands'].append({'label': 'Migration chain drill', 'status': 'passed'})
    packet_path.write_text(json.dumps(packet), encoding='utf-8')

    pending_report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert pending_report['items'][0]['status'] == 'passed'
    assert pending_report['items'][0]['evidence'] == 'RC evidence gates passed: Migration chain drill'
    assert pending_report['items'][1]['status'] == 'external-required'
    assert pending_report['counts'] == {'passed': 1, 'external-required': 1}

    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    packet['hosted_rc_evidence'].update(
        {
            'status': 'passed',
            'manual_evidence': [
                {
                    'label': 'Hosted Socket.IO worker process proof',
                    'status': 'provided',
                    'evidence': 'https://ops.closedbeta.dev/processes/worker-single',
                }
            ],
        }
    )
    packet_path.write_text(json.dumps(packet), encoding='utf-8')

    unbound_report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert unbound_report['items'][1]['status'] == 'external-required'

    packet['hosted_rc_evidence'].update(
        {
            'target_url': 'https://aidm.closedbeta.dev',
            'generator_freshness': 'current',
        }
    )
    packet_path.write_text(json.dumps(packet), encoding='utf-8')

    passed_report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert passed_report['counts'] == {'passed': 2}
    assert 'aidm.closedbeta.dev' in passed_report['items'][1]['evidence']
    assert 'worker-single' in passed_report['items'][1]['evidence']

    packet['hosted_rc_evidence']['generator_freshness'] = 'stale'
    packet_path.write_text(json.dumps(packet), encoding='utf-8')
    stale_report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert stale_report['items'][1]['status'] == 'external-required'

    packet['hosted_rc_evidence']['generator_freshness'] = 'current'
    packet['hosted_rc_evidence']['metadata']['socket_io_worker_model'] = 'message_queue'
    packet_path.write_text(json.dumps(packet), encoding='utf-8')
    unsupported_report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert unsupported_report['items'][1]['status'] == 'failed'


def test_build_status_report_fails_mismatched_source_archive_sidecar(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '## Packaging\n- [ ] The source archive has a matching `.sha256` sidecar.\n',
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=True)
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    sidecar_path = Path(packet['source_archive']['path'] + '.sha256')
    sidecar_path.write_text('different-sha  aidm-source-test.tar.gz\n', encoding='utf-8')

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['items'][0]['status'] == 'failed'
    assert 'checksum does not match packet sha256' in report['items'][0]['evidence']
    assert report['items'][0]['remaining_action'] == 'rerun make source-archive'


def test_build_status_report_requires_explicit_modal_accessibility_regressions(tmp_path, monkeypatch):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '## Frontend\n'
        '- [ ] Modal accessibility regressions cover focus placement, Escape close, focus trapping, '
        'focus return, dialog descriptions, and danger confirmation cancellation.\n',
        encoding='utf-8',
    )
    app_test = tmp_path / 'App.test.tsx'
    app_test.write_text(
        "\n".join(
            [
                "it('closes the character delete confirmation with Escape without deleting', () => {",
                "findByRole('dialog', { name: 'Create New Campaign' })",
                "fireEvent.keyDown(document, { key: 'Escape' })",
                "fireEvent.keyDown(document, { key: 'Tab' })",
                'expect(deleteButton).toHaveFocus()',
                'expect(dialog).toHaveAccessibleDescription(/permanently removes/)',
                "fetchCalls.some((call) => call.method === 'DELETE')",
                '})',
                "it('traps modal focus and returns focus to the opener when closed', () => {})",
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr(checklist_status, 'DEFAULT_FRONTEND_APP_TEST', app_test)
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=False)

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['items'][0]['status'] == 'passed'
    assert str(app_test) in report['items'][0]['evidence']
    assert 'danger confirmation cancellation' in report['items'][0]['evidence']


def test_build_status_report_fails_missing_modal_accessibility_regressions(tmp_path, monkeypatch):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '## Frontend\n'
        '- [ ] Modal accessibility regressions cover focus placement, Escape close, focus trapping, '
        'focus return, dialog descriptions, and danger confirmation cancellation.\n',
        encoding='utf-8',
    )
    app_test = tmp_path / 'App.test.tsx'
    app_test.write_text("it('some unrelated frontend test', () => {})\n", encoding='utf-8')
    monkeypatch.setattr(checklist_status, 'DEFAULT_FRONTEND_APP_TEST', app_test)
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=False)

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['items'][0]['status'] == 'failed'
    assert 'missing modal accessibility coverage' in report['items'][0]['evidence']
    assert 'danger confirmation cancellation' in report['items'][0]['evidence']


def test_build_status_report_marks_hosted_items_passed_when_packet_is_complete(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '# Release',
                '',
                '## Preflight',
                '- [ ] RC evidence is generated from a clean signed-off commit/worktree before final issue closure.',
                '- [ ] GitHub Actions `AIDM CI` passes.',
                '- [ ] `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--target-url <target-url>"` passes for the hosted/staging target.',
                '- [ ] `make hosted-rc-evidence HOSTED_RC_EVIDENCE_ARGS="--target-url <target-url>"` runs the hosted evidence plan.',
                '- [ ] `make operator-signoff-status OPERATOR_SIGNOFF_STATUS_ARGS="--require-complete"` passes before RC issue closure.',
                '## Observability',
                '- [ ] `make beta-slo-baseline BETA_SLO_BASELINE_ARGS="--target-url <target-url>"` writes target-environment metrics.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=True)

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['counts'] == {'passed': 6}


def test_security_recovery_and_workspace_isolation_rows_require_current_clean_rc(
    tmp_path,
    monkeypatch,
):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Security',
                '- [ ] Passwordless legacy recovery requires a valid saved or operator-issued high-entropy account token.',
                '- [ ] Workspace-password target limiting is scoped per authenticated account and canonical workspace.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet-security-regressions.json', hosted_ready=True)
    monkeypatch.setattr(checklist_status, '_current_git_snapshot', lambda: ('abc123', True))

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-07-10T00:00:00+00:00',
    )

    assert report['counts'] == {'passed': 2}
    assert all('regression' in item['evidence'] for item in report['items'])


def test_security_regression_rows_do_not_reuse_stale_rc_packet(tmp_path, monkeypatch):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Security',
                '- [ ] Passwordless legacy recovery requires a valid saved or operator-issued high-entropy account token.',
                '- [ ] Workspace-password target limiting is scoped per authenticated account and canonical workspace.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet-stale-security-regressions.json', hosted_ready=True)
    monkeypatch.setattr(checklist_status, '_current_git_snapshot', lambda: ('def456', True))

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-07-10T00:00:00+00:00',
    )

    assert report['counts'] == {'external-required': 2}
    assert all('not current' in item['evidence'] for item in report['items'])


def test_build_status_report_uses_hosted_rc_aggregate_checks(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Preflight',
                '- [ ] `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--target-url <target-url>"` passes for the hosted/staging target.',
                '- [ ] `GET /api/health` confirms expected flags.',
                '## Security',
                '- [ ] `AIDM_AUTH_REQUIRED=true` in deployed environment.',
                '- [ ] `make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_SMOKE_ARGS="--target-url <target-url>"` passes against the hosted/staging URL.',
                '- [ ] `make security-forbidden-smoke SECURITY_FORBIDDEN_SMOKE_ARGS="--target-url <target-url>"` passes against hosted/staging before closing the security gate.',
                '## Data Integrity',
                '- [ ] Hosted target-environment export/import smoke passes.',
                '## Observability',
                '- [ ] `make beta-slo-baseline BETA_SLO_BASELINE_ARGS="--target-url <target-url>"` writes target-environment metrics.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet-hosted-aggregate.json', hosted_ready=False)
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    packet['hosted_rc_evidence'].update(
        {
            'status': 'manual-evidence-required',
            'target_url': 'https://aidm.closedbeta.dev',
            'generator_freshness': 'current',
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
    )
    packet_path.write_text(json.dumps(packet), encoding='utf-8')

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['counts'] == {'passed': 7}
    assert all('via hosted RC evidence' in item['evidence'] for item in report['items'])


def test_build_status_report_uses_frontend_npm_ci_evidence(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '## Frontend\n- [ ] `make frontend-npm-ci-evidence` records that `cd aidm_frontend && npm ci` installs from lockfile.\n',
        encoding='utf-8',
    )
    missing_packet = _write_packet(tmp_path / 'packet-missing.json', hosted_ready=False)

    missing_report = build_status_report(
        checklist_path=checklist,
        packet_path=missing_packet,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert missing_report['items'][0]['status'] == 'manual-review'

    passed_packet = _write_packet(tmp_path / 'packet-passed.json', hosted_ready=True)
    passed_report = build_status_report(
        checklist_path=checklist,
        packet_path=passed_packet,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert passed_report['items'][0]['status'] == 'passed'


def test_build_status_report_uses_packaging_cleanup_evidence(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Packaging',
                '- [ ] `make packaging-cleanup-evidence` verifies `make clean` removes cache/runtime/build artifacts.',
                '- [ ] `make packaging-cleanup-evidence` verifies `make clean-deps` covers local dependency folders.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    missing_packet = _write_packet(tmp_path / 'packet-missing.json', hosted_ready=False)
    missing_report = build_status_report(
        checklist_path=checklist,
        packet_path=missing_packet,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert [item['status'] for item in missing_report['items']] == ['manual-review', 'manual-review']

    passed_packet = _write_packet(tmp_path / 'packet-passed.json', hosted_ready=True)
    passed_report = build_status_report(
        checklist_path=checklist,
        packet_path=passed_packet,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert [item['status'] for item in passed_report['items']] == ['passed', 'passed']


def test_build_status_report_uses_release_artifact_consistency(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        (
            '## Preflight\n'
            '- [ ] `make release-artifact-consistency` renders '
            '`tmp/release/release-artifact-consistency.md` and `.json`.\n'
        ),
        encoding='utf-8',
    )
    missing_packet = _write_packet(tmp_path / 'packet-missing.json', hosted_ready=False)
    missing_report = build_status_report(
        checklist_path=checklist,
        packet_path=missing_packet,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert missing_report['items'][0]['status'] == 'external-required'

    passed_packet = _write_packet(tmp_path / 'packet-passed.json', hosted_ready=True)
    passed_report = build_status_report(
        checklist_path=checklist,
        packet_path=passed_packet,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert passed_report['items'][0]['status'] == 'passed'
    assert 'checks: 17' in passed_report['items'][0]['evidence']

    failed_packet = _write_packet(tmp_path / 'packet-failed.json', hosted_ready=True)
    packet = json.loads(failed_packet.read_text(encoding='utf-8'))
    packet['release_artifact_consistency']['status'] = 'failed'
    packet['release_artifact_consistency']['error_count'] = 2
    failed_packet.write_text(json.dumps(packet), encoding='utf-8')
    failed_report = build_status_report(
        checklist_path=checklist,
        packet_path=failed_packet,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert failed_report['items'][0]['status'] == 'failed'
    assert 'stale source archive/signoff evidence' in failed_report['items'][0]['remaining_action']


def test_build_status_report_fails_stale_local_handoff_artifacts(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Release Handoff',
                '- [ ] `cd aidm_frontend && npm ci` installs from lockfile.',
                '- [ ] `make packaging-cleanup-evidence` verifies `make clean` removes cache/runtime/build artifacts.',
                '- [ ] `make source-archive` creates a clean source archive.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet-stale-local.json', hosted_ready=True)
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    packet['frontend_npm_ci']['status'] = 'stale'
    packet['packaging_cleanup']['status'] = 'stale'
    packet['source_archive']['status'] = 'stale'
    packet_path.write_text(json.dumps(packet), encoding='utf-8')

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert [item['status'] for item in report['items']] == ['failed', 'failed', 'failed']
    assert all('rerun make rc-handoff-artifacts' in item['remaining_action'] for item in report['items'])


def test_build_status_report_treats_single_worker_socketio_conditionals_as_passed(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Runtime Quality',
                '- [ ] Multi-worker deployments set `AIDM_TURN_COORDINATOR_STORE=database`, have migration `0011_session_turn_locks` applied, and prove sticky-session affinity or Socket.IO message-queue delivery in staging.',
                '- [ ] Sticky or message-queue Socket.IO deployments provide `--socketio-staging-proof` to the deployment-readiness gate.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=False)

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert [item['status'] for item in report['items']] == ['passed', 'passed']
    assert all('worker model is single' in item['evidence'] for item in report['items'])


def test_build_status_report_uses_socketio_worker_model_decision_gate(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '## Runtime Quality\n'
        '- [ ] `AIDM_SOCKETIO_WORKER_MODEL` is explicitly set to `single`, `sticky`, or `message_queue`.\n',
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=False)

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['items'][0]['status'] == 'passed'
    assert 'worker model decision gate' in report['items'][0]['evidence']


def test_build_status_report_rejects_non_single_worker_modes_even_with_proof(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Runtime Quality',
                '- [ ] Multi-worker deployments set `AIDM_TURN_COORDINATOR_STORE=database`, have migration `0011_session_turn_locks` applied, and prove sticky-session affinity or Socket.IO message-queue delivery in staging.',
                '- [ ] Sticky or message-queue Socket.IO deployments provide `--socketio-staging-proof` to the deployment-readiness gate.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    for worker_model in ('sticky', 'message_queue', 'future_model'):
        packet_path = _write_packet(tmp_path / f'packet-{worker_model}.json', hosted_ready=False)
        packet = json.loads(packet_path.read_text(encoding='utf-8'))
        packet['hosted_rc_evidence']['metadata']['socket_io_worker_model'] = worker_model

        for staging_proof_provided in ('False', 'True'):
            packet['deployment_readiness']['metadata']['socket_io_staging_proof_provided'] = staging_proof_provided
            packet_path.write_text(json.dumps(packet), encoding='utf-8')
            report = build_status_report(
                checklist_path=checklist,
                packet_path=packet_path,
                generated_at='2026-06-19T00:00:00+00:00',
            )

            assert [item['status'] for item in report['items']] == ['failed', 'failed']
            assert all('unsupported for hosted production' in item['evidence'] for item in report['items'])
            assert all('AIDM_SOCKETIO_WORKER_MODEL=single' in item['remaining_action'] for item in report['items'])


def test_build_status_report_flags_stale_hosted_rc_evidence(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Preflight',
                '- [ ] `make hosted-rc-evidence` runs the hosted evidence plan.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet-stale-hosted.json', hosted_ready=True)
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    packet['hosted_rc_evidence']['status'] = 'stale'
    packet['hosted_rc_evidence']['generator_freshness'] = 'stale'
    packet_path.write_text(json.dumps(packet), encoding='utf-8')

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['items'][0]['status'] == 'external-required'
    assert 'older hosted evidence checker' in report['items'][0]['evidence']
    assert 'rerun make hosted-rc-evidence' in report['items'][0]['remaining_action']


def test_build_status_report_fails_invalid_github_actions_evidence(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Preflight',
                '- [ ] GitHub Actions `AIDM CI` passes.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet-invalid-actions.json', hosted_ready=True)
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    packet['github_actions']['status'] = 'invalid'
    packet_path.write_text(json.dumps(packet), encoding='utf-8')

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['items'][0]['status'] == 'failed'
    assert 'invalid' in report['items'][0]['evidence']
    assert 'fix GitHub Actions run URL evidence' in report['items'][0]['remaining_action']


def test_build_status_report_flags_stale_github_actions_evidence(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Preflight',
                '- [ ] GitHub Actions `Closed Beta RC` passes before tagging an RC build.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet-stale-actions.json', hosted_ready=True)
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    packet['github_actions']['status'] = 'stale'
    packet_path.write_text(json.dumps(packet), encoding='utf-8')

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['items'][0]['status'] == 'external-required'
    assert 'older than RC evidence' in report['items'][0]['evidence']
    assert 'rerun make github-actions-evidence' in report['items'][0]['remaining_action']


def test_build_status_report_passes_aidm_ci_row_when_only_ci_url_is_present(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Preflight',
                '- [ ] GitHub Actions `AIDM CI` passes backend tests, frontend checks, bundle budget, and browser smoke.',
                '- [ ] GitHub Actions `Closed Beta RC` passes before tagging an RC build.',
                '- [ ] `make github-actions-evidence` records the successful `AIDM CI` run URL and `Closed Beta RC` run URL.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet-partial-actions.json', hosted_ready=False)
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    packet['github_actions']['status'] = 'incomplete'
    packet['github_actions']['aidm_ci_run_url'] = 'https://github.com/dreichner2/AIDM-main/actions/runs/123'
    packet['github_actions']['closed_beta_rc_run_url'] = 'missing'
    packet_path.write_text(json.dumps(packet), encoding='utf-8')

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    by_item = {item['item']: item for item in report['items']}
    assert by_item['GitHub Actions `AIDM CI` passes backend tests, frontend checks, bundle budget, and browser smoke.']['status'] == 'passed'
    assert by_item['GitHub Actions `Closed Beta RC` passes before tagging an RC build.']['status'] == 'external-required'
    assert by_item['`make github-actions-evidence` records the successful `AIDM CI` run URL and `Closed Beta RC` run URL.']['status'] == 'external-required'
    assert report['counts'] == {'external-required': 2, 'passed': 1}


def test_build_status_report_uses_closed_beta_rc_artifact_status(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Preflight',
                '- [ ] GitHub Actions `Closed Beta RC` uploads the `closed-beta-rc-evidence` artifact containing the source archive.',
                '## Packaging',
                '- [ ] The manual `Closed Beta RC` workflow artifact includes the generated source archive for reviewer download before tagging a hosted RC.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    missing_packet = _write_packet(tmp_path / 'packet-missing-artifact.json', hosted_ready=True)
    packet = json.loads(missing_packet.read_text(encoding='utf-8'))
    packet['github_actions']['status'] = 'incomplete'
    packet['github_actions']['closed_beta_rc_artifact_status'] = 'missing'
    packet['github_actions']['closed_beta_rc_artifact_content_status'] = 'missing'
    packet['github_actions']['closed_beta_rc_artifact']['status'] = 'missing'
    packet['github_actions']['closed_beta_rc_artifact']['content_status'] = 'missing'
    packet['github_actions']['closed_beta_rc_artifact']['name'] = ''
    packet['github_actions']['closed_beta_rc_artifact']['url'] = ''
    missing_packet.write_text(json.dumps(packet), encoding='utf-8')

    missing_report = build_status_report(
        checklist_path=checklist,
        packet_path=missing_packet,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert [item['status'] for item in missing_report['items']] == ['failed', 'failed']
    assert all('was not found' in item['evidence'] for item in missing_report['items'])

    passed_packet = _write_packet(tmp_path / 'packet-artifact-passed.json', hosted_ready=True)
    passed_report = build_status_report(
        checklist_path=checklist,
        packet_path=passed_packet,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert [item['status'] for item in passed_report['items']] == ['passed', 'passed']
    assert all('closed-beta-rc-evidence artifact contents' in item['evidence'] for item in passed_report['items'])

    unchecked_packet = _write_packet(tmp_path / 'packet-artifact-unchecked.json', hosted_ready=True)
    packet = json.loads(unchecked_packet.read_text(encoding='utf-8'))
    packet['github_actions']['closed_beta_rc_artifact_content_status'] = 'not-checked'
    packet['github_actions']['closed_beta_rc_artifact']['content_status'] = 'not-checked'
    unchecked_packet.write_text(json.dumps(packet), encoding='utf-8')
    unchecked_report = build_status_report(
        checklist_path=checklist,
        packet_path=unchecked_packet,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert [item['status'] for item in unchecked_report['items']] == ['external-required', 'external-required']
    assert all('contents are not-checked' in item['evidence'] for item in unchecked_report['items'])


def test_build_status_report_fails_invalid_hosted_rc_evidence(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Preflight',
                '- [ ] `make hosted-rc-evidence` runs the hosted evidence plan.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet-invalid-hosted.json', hosted_ready=True)
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    packet['hosted_rc_evidence']['status'] = 'invalid-evidence'
    packet_path.write_text(json.dumps(packet), encoding='utf-8')

    report = build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['items'][0]['status'] == 'failed'
    assert 'invalid-evidence' in report['items'][0]['evidence']
    assert 'fix the hosted RC evidence run' in report['items'][0]['remaining_action']


def test_build_status_report_uses_rc_issue_closure_evidence(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '\n'.join(
            [
                '## Preflight',
                '- [ ] `make rc-issue-closure-evidence` writes read-only closure/comment evidence for RC gate issues `#3`-`#9` before final issue closure.',
                '- [ ] RC gate issues are closed with generated `tmp/release/issue-evidence/issue-*.md` snippets.',
                '',
            ]
        ),
        encoding='utf-8',
    )

    incomplete_packet = _write_packet(tmp_path / 'packet-incomplete.json', hosted_ready=False)
    incomplete_report = build_status_report(
        checklist_path=checklist,
        packet_path=incomplete_packet,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert [item['status'] for item in incomplete_report['items']] == ['passed', 'external-required']
    assert 'open issues: 7' in incomplete_report['items'][1]['evidence']

    complete_packet = _write_packet(tmp_path / 'packet-complete.json', hosted_ready=True)
    complete_report = build_status_report(
        checklist_path=checklist,
        packet_path=complete_packet,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    assert [item['status'] for item in complete_report['items']] == ['passed', 'passed']


def test_main_writes_markdown_and_json(tmp_path):
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text('## Preflight\n- [ ] `.venv/bin/python -m pytest` passes.\n', encoding='utf-8')
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=False)
    output = tmp_path / 'status.md'
    json_output = tmp_path / 'status.json'

    exit_code = main(
        [
            '--checklist',
            str(checklist),
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

    assert exit_code == 0
    assert '# Release Checklist Status' in output.read_text(encoding='utf-8')
    payload = json.loads(json_output.read_text(encoding='utf-8'))
    assert payload['counts']['passed'] == 1


def test_build_status_report_marks_external_proof_inputs_generated(tmp_path, monkeypatch):
    external_inputs_md = tmp_path / 'external-proof-inputs.md'
    external_inputs = tmp_path / 'external-proof-inputs.json'
    external_inputs_md.write_text('# External Proof Inputs\n', encoding='utf-8')
    external_inputs.write_text(
        json.dumps(
            {
                'status': 'action-required',
                'field_counts': {'required': 6, 'conditional': 1, 'provided_context': 3, 'total': 20},
            }
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr(checklist_status, 'DEFAULT_EXTERNAL_PROOF_INPUTS_MARKDOWN', external_inputs_md)
    monkeypatch.setattr(checklist_status, 'DEFAULT_EXTERNAL_PROOF_INPUTS', external_inputs)
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '## Preflight\n- [ ] `make external-proof-inputs` renders `tmp/release/external-proof-inputs.md` and `.json`.\n',
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=False)

    report = checklist_status.build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['counts'] == {'passed': 1}
    assert str(external_inputs_md) in report['items'][0]['evidence']
    assert str(external_inputs) in report['items'][0]['evidence']


def test_build_status_report_marks_operator_signoff_values_template_generated(tmp_path, monkeypatch):
    values_template = tmp_path / 'external-proof-values.example.json'
    values_template.write_text(json.dumps({'values': {'aidm_ci_run_url': '', 'target_url': ''}}), encoding='utf-8')
    monkeypatch.setattr(checklist_status, 'DEFAULT_EXTERNAL_PROOF_VALUES_TEMPLATE', values_template)
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '## Preflight\n- [ ] `make operator-signoff-values-template` renders `tmp/release/external-proof-values.example.json`.\n',
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=False)

    report = checklist_status.build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['counts'] == {'passed': 1}
    assert str(values_template) in report['items'][0]['evidence']


def test_build_status_report_marks_external_proof_values_check_generated(tmp_path, monkeypatch):
    values_status_md = tmp_path / 'external-proof-values-status.md'
    values_status_json = tmp_path / 'external-proof-values-status.json'
    values_status_md.write_text('# External Proof Values Check\n', encoding='utf-8')
    values_status_json.write_text(
        json.dumps(
            {
                'status': 'incomplete',
                'required_complete': '0/22',
                'missing_required_count': 22,
                'invalid_error_count': 0,
            }
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr(checklist_status, 'DEFAULT_EXTERNAL_PROOF_VALUES_STATUS_MARKDOWN', values_status_md)
    monkeypatch.setattr(checklist_status, 'DEFAULT_EXTERNAL_PROOF_VALUES_STATUS', values_status_json)
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        (
            '## Preflight\n'
            '- [ ] `make external-proof-values-check` writes '
            '`tmp/release/external-proof-values-status.md` and `.json`.\n'
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=False)

    report = checklist_status.build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['counts'] == {'passed': 1}
    assert str(values_status_md) in report['items'][0]['evidence']
    assert 'required complete: 0/22' in report['items'][0]['evidence']


def test_rc_finalize_signoff_row_requires_operator_signoff_even_when_values_check_exists(tmp_path, monkeypatch):
    values_status_md = tmp_path / 'external-proof-values-status.md'
    values_status_json = tmp_path / 'external-proof-values-status.json'
    values_status_md.write_text('# External Proof Values Check\n', encoding='utf-8')
    values_status_json.write_text(
        json.dumps(
            {
                'status': 'passed',
                'required_complete': '25/25',
                'missing_required_count': 0,
                'invalid_error_count': 0,
            }
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr(checklist_status, 'DEFAULT_EXTERNAL_PROOF_VALUES_STATUS_MARKDOWN', values_status_md)
    monkeypatch.setattr(checklist_status, 'DEFAULT_EXTERNAL_PROOF_VALUES_STATUS', values_status_json)
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        (
            '## Preflight\n'
            '- [ ] `make rc-finalize-signoff` runs after external hosted proof values are filled and merged. '
            'It requires `make external-proof-values-check` to pass.\n'
        ),
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=False)

    report = checklist_status.build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['counts'] == {'external-required': 1}
    assert 'operator signoff status is incomplete' in report['items'][0]['evidence']
    assert report['items'][0]['remaining_action'] == 'fill external proof values and run make rc-finalize-signoff'


def test_build_status_report_fails_external_proof_values_check_with_invalid_values(tmp_path, monkeypatch):
    values_status_md = tmp_path / 'external-proof-values-status.md'
    values_status_json = tmp_path / 'external-proof-values-status.json'
    values_status_md.write_text('# External Proof Values Check\n', encoding='utf-8')
    values_status_json.write_text(
        json.dumps({'status': 'invalid', 'required_complete': '0/22', 'invalid_error_count': 1}),
        encoding='utf-8',
    )
    monkeypatch.setattr(checklist_status, 'DEFAULT_EXTERNAL_PROOF_VALUES_STATUS_MARKDOWN', values_status_md)
    monkeypatch.setattr(checklist_status, 'DEFAULT_EXTERNAL_PROOF_VALUES_STATUS', values_status_json)
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '## Preflight\n- [ ] `make external-proof-values-check` writes `tmp/release/external-proof-values-status.md`.\n',
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=False)

    report = checklist_status.build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['counts'] == {'failed': 1}
    assert 'invalid errors: 1' in report['items'][0]['evidence']


def test_build_status_report_marks_external_proof_execution_plan_generated(tmp_path, monkeypatch):
    plan_md = tmp_path / 'external-proof-execution-plan.md'
    plan_json = tmp_path / 'external-proof-execution-plan.json'
    plan_md.write_text('# External Proof Execution Plan\n', encoding='utf-8')
    plan_json.write_text(
        json.dumps({'status': 'action-required', 'counts': {'pending_actions': 3, 'required_fields': 4}}),
        encoding='utf-8',
    )
    monkeypatch.setattr(checklist_status, 'DEFAULT_EXTERNAL_PROOF_EXECUTION_PLAN_MARKDOWN', plan_md)
    monkeypatch.setattr(checklist_status, 'DEFAULT_EXTERNAL_PROOF_EXECUTION_PLAN', plan_json)
    checklist = tmp_path / 'release_checklist.md'
    checklist.write_text(
        '## Preflight\n- [ ] `make external-proof-execution-plan` renders `tmp/release/external-proof-execution-plan.md` and `.json`.\n',
        encoding='utf-8',
    )
    packet_path = _write_packet(tmp_path / 'packet.json', hosted_ready=False)

    report = checklist_status.build_status_report(
        checklist_path=checklist,
        packet_path=packet_path,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['counts'] == {'passed': 1}
    assert str(plan_md) in report['items'][0]['evidence']
