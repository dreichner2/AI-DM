from __future__ import annotations

import json

from scripts.render_rc_recommendation_matrix import build_matrix, main, render_markdown


def _write(path, text='ok'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _seed_recommendation_files(root):
    _write(root / 'docs' / 'roadmap.md', 'AIDM is in beta-hardening territory.')
    _write(root / 'docs' / 'socketio_worker_model.md')
    _write(root / 'docs' / 'state_snapshot_writer_inventory.md')
    _write(root / 'docs' / 'beta_tester_onboarding.md')
    _write(root / 'docs' / 'campaign_packs.md', 'authoring report preview')
    _write(root / 'docs' / 'beta_runbook.md', 'scripts/run_production_server.sh')
    _write(root / 'docs' / 'auth_modes.md')
    _write(root / 'docs' / 'release_checklist.md', 'verifies required security headers and CSP')
    _write(root / '.github' / 'dependabot.yml')
    _write(root / 'scripts' / 'check_socketio_worker_model_decision.py')
    _write(root / 'scripts' / 'check_state_snapshot_writers.py')
    _write(root / 'scripts' / 'render_release_evidence_packet.py')
    _write(root / 'scripts' / 'render_release_checklist_status.py')
    _write(root / 'scripts' / 'export_support_bundle.py')
    _write(root / 'scripts' / 'run_production_server.sh')
    _write(root / 'Makefile', 'rc-handoff-artifacts')
    _write(
        root / 'aidm_frontend' / 'src' / 'BetaRuntimeNotesPanel.tsx',
        '\n'.join(
            [
                'Beta information',
                'Current player-impacting problems appear as alerts.',
                'Its current availability appears in the narration controls.',
            ]
        ),
    )
    _write(
        root / 'aidm_frontend' / 'src' / 'App.test.tsx',
        "\n".join(
            [
                "it('closes the character delete confirmation with Escape without deleting', () => {})",
                "it('traps modal focus and returns focus to the opener when closed', () => {})",
                "findByRole('dialog', { name: 'Create New Campaign' })",
                "fireEvent.keyDown(document, { key: 'Escape' })",
                "fireEvent.keyDown(document, { key: 'Tab' })",
                'expect(closeButton).toHaveFocus()',
                'expect(dialog).toHaveAccessibleDescription(/permanently removes/)',
                "if (method === 'DELETE') return {}",
            ]
        ),
    )
    _write(
        root / 'aidm_frontend' / 'src' / 'App.runtimeAccess.test.tsx',
        '\n'.join(
            [
                'Beta information',
                'keeps beta information available from the account menu without stale operator guidance',
                'keeps unavailable TTS in its accessible control instead of the global alert bar',
                'hides process-local instructions for a single worker',
                'shows process-local synchronization only to workspace admins when multiple workers need it',
                'hides process-local synchronization from non-admin players',
            ]
        ),
    )
    _write(root / 'aidm_frontend' / 'src' / 'BetaIncidentPanel.tsx', 'Selected session quality\nExport workspace support bundle')
    _write(root / 'aidm_frontend' / 'src' / 'CampaignPackImportDialog.tsx', 'authoring_report')
    _write(root / 'aidm_frontend' / 'src' / 'SessionBoard.tsx', 'Beta turn feedback')
    _write(
        root / 'aidm_frontend' / 'src' / 'App.tsx',
        "\n".join(
            [
                'fun_score',
                'Beta runtime notices',
                'Fallback DM active. Ask the table operator to restore the live provider.',
                'Live DM is unavailable. Ask the table operator to configure the selected provider.',
                'Narration unavailable; Deepgram is not configured',
                'showProcessLocalDiagnostic',
                'Process-local · restart workers',
            ]
        ),
    )
    _write(root / 'aidm_frontend' / 'scripts' / 'browser-smoke.cjs', 'assertCspDirectives')
    _write(root / 'tests' / 'test_beta_summary.py', '/api/beta/session-quality\n/api/feedback/coherence')


def _packet(*, hosted_ready=False, clean=False, signoff=False):
    target = 'https://aidm.closedbeta.dev' if hosted_ready else 'isolated local runtime'
    return {
        'rc_evidence': {
            'status': 'passed',
            'gate_count': 26,
            'commands': [
                {'label': 'Socket.IO worker model decision', 'status': 'passed'},
                {'label': 'State snapshot writer inventory', 'status': 'passed'},
                {'label': 'API type drift check', 'status': 'passed'},
                {'label': 'Migration chain drill', 'status': 'passed'},
                {'label': 'Socket concurrency smoke', 'status': 'passed'},
                {'label': 'Security forbidden smoke', 'status': 'passed'},
            ],
        },
        'github_actions': {
            'status': 'passed' if hosted_ready else 'incomplete',
            'closed_beta_rc_run_url': 'https://github.example/actions/runs/1' if hosted_ready else 'missing',
        },
        'issue_evidence': {'status': 'passed with external exceptions'},
        'rc_issue_closure_evidence': {
            'status': 'passed' if hosted_ready and signoff else 'external-required',
            'open_issues': '0' if hosted_ready and signoff else '7',
            'matching_comments': '7' if hosted_ready and signoff else '0',
        },
        'deployment_readiness': {
            'status': 'passed' if hosted_ready else 'env-only',
            'target_url': 'https://aidm.closedbeta.dev' if hosted_ready else 'not checked',
        },
        'hosted_cookie_auth': {
            'status': 'passed',
            'mode': 'live-target' if hosted_ready else 'isolated',
            'target_url': target,
        },
        'beta_slo_baseline': {
            'status': 'present' if hosted_ready else 'local-only',
            'target_url': target,
        },
        'source_archive': {
            'status': 'passed',
            'path': '/tmp/aidm-source.tar.gz',
            'sha256': 'abc123',
        },
        'signed_off_worktree': {
            'status': 'passed' if clean else 'dirty',
            'worktree': 'clean' if clean else 'dirty',
        },
        'operator_signoff': {
            'status': 'passed' if signoff else 'missing',
            'required_complete': '19/19' if signoff else '0/19',
        },
    }


def test_build_matrix_separates_local_implementation_from_external_proof(tmp_path):
    _seed_recommendation_files(tmp_path)

    matrix = build_matrix(
        repo_root=tmp_path,
        packet=_packet(hosted_ready=False, clean=False, signoff=False),
        checklist={'items': []},
        generated_at='2026-06-19T00:00:00+00:00',
    )

    by_key = {item['key']: item for item in matrix['recommendations']}
    assert matrix['status'] == 'local-ready-with-external-exceptions'
    assert by_key['local_rc_gate']['status'] == 'implemented'
    assert by_key['github_actions_gate']['status'] == 'external-required'
    assert by_key['issue_evidence_closure']['status'] == 'external-required'
    assert by_key['hosted_cookie_auth_proof']['status'] == 'external-required'
    assert by_key['beta_feedback_prompt']['status'] == 'implemented'
    assert by_key['beta_runtime_notices']['status'] == 'implemented'
    assert by_key['campaign_pack_authoring_feedback']['status'] == 'implemented'
    assert by_key['modal_accessibility_regressions']['status'] == 'implemented'
    assert by_key['clean_signed_off_worktree']['status'] == 'external-required'
    assert by_key['final_operator_signoff']['status'] == 'external-required'
    assert matrix['counts']['implemented'] > matrix['counts']['external-required']

    markdown = render_markdown(matrix)
    assert '# RC Recommendation Matrix' in markdown
    assert '`hosted_cookie_auth_proof`' in markdown


def test_main_writes_recommendation_matrix(tmp_path):
    packet_path = tmp_path / 'packet.json'
    checklist_path = tmp_path / 'checklist.json'
    output = tmp_path / 'matrix.md'
    json_output = tmp_path / 'matrix.json'
    packet_path.write_text(json.dumps(_packet(hosted_ready=True, clean=True, signoff=True)), encoding='utf-8')
    checklist_path.write_text(json.dumps({'items': []}), encoding='utf-8')

    exit_code = main(
        [
            '--packet-json',
            str(packet_path),
            '--checklist-json',
            str(checklist_path),
            '--output',
            str(output),
            '--json-output',
            str(json_output),
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
        ]
    )

    assert exit_code == 0
    assert '# RC Recommendation Matrix' in output.read_text(encoding='utf-8')
    payload = json.loads(json_output.read_text(encoding='utf-8'))
    assert payload['generated_at'] == '2026-06-19T00:00:00+00:00'
    assert any(item['key'] == 'source_archive' for item in payload['recommendations'])
