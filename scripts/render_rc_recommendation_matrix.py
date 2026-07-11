#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PACKET_JSON = REPO_ROOT / 'tmp' / 'release' / 'release-evidence-packet.json'
DEFAULT_CHECKLIST_JSON = REPO_ROOT / 'tmp' / 'release' / 'release-checklist-status.json'
DEFAULT_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'rc-recommendation-matrix.md'
DEFAULT_JSON_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'rc-recommendation-matrix.json'


@dataclass(frozen=True)
class RecommendationRow:
    key: str
    category: str
    status: str
    recommendation: str
    evidence: str
    next_action: str


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


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    resolved = _resolve_repo_path(path)
    if not resolved.exists():
        return {}
    parsed = json.loads(resolved.read_text(encoding='utf-8'))
    return parsed if isinstance(parsed, dict) else {}


def _path_exists(repo_root: pathlib.Path, relative_path: str) -> bool:
    return (repo_root / relative_path).exists()


def _file_contains(repo_root: pathlib.Path, relative_path: str, needle: str) -> bool:
    path = repo_root / relative_path
    if not path.exists():
        return False
    return needle in path.read_text(encoding='utf-8', errors='ignore')


def _packet_section(packet: dict[str, Any], key: str) -> dict[str, Any]:
    section = packet.get(key) or {}
    return section if isinstance(section, dict) else {}


def _packet_status(packet: dict[str, Any], key: str) -> str:
    return str(_packet_section(packet, key).get('status') or 'missing')


def _command_passed(packet: dict[str, Any], label: str) -> bool:
    for command in (_packet_section(packet, 'rc_evidence').get('commands') or []):
        if isinstance(command, dict) and command.get('label') == label:
            return command.get('status') == 'passed'
    return False


def _target_url(section: dict[str, Any]) -> str:
    return str(section.get('target_url') or '')


def _is_hosted_target(target_url: str) -> bool:
    lowered = target_url.lower().strip()
    if not lowered or lowered in {'missing', 'not checked', 'isolated local runtime'}:
        return False
    if '<' in lowered or '>' in lowered or '.example.' in lowered:
        return False
    return not lowered.startswith(('http://127.', 'http://localhost', 'https://127.', 'https://localhost'))


def _hosted_passed(packet: dict[str, Any], key: str) -> bool:
    section = _packet_section(packet, key)
    return section.get('status') in {'passed', 'present'} and _is_hosted_target(_target_url(section))


def _checklist_item(checklist: dict[str, Any], needle: str) -> dict[str, Any]:
    for item in checklist.get('items') or []:
        if isinstance(item, dict) and needle.lower() in str(item.get('item') or '').lower():
            return item
    return {}


def _checklist_status(checklist: dict[str, Any], needle: str) -> str:
    return str(_checklist_item(checklist, needle).get('status') or 'missing')


def _bool_status(value: bool) -> str:
    return 'implemented' if value else 'missing'


def _file_contains_all(repo_root: pathlib.Path, relative_path: str, needles: tuple[str, ...]) -> bool:
    path = repo_root / relative_path
    if not path.exists():
        return False
    contents = path.read_text(encoding='utf-8', errors='ignore')
    return all(needle in contents for needle in needles)


def _row(
    key: str,
    category: str,
    status: str,
    recommendation: str,
    evidence: str,
    next_action: str = '',
) -> RecommendationRow:
    return RecommendationRow(
        key=key,
        category=category,
        status=status,
        recommendation=recommendation,
        evidence=evidence,
        next_action=next_action,
    )


def build_matrix(*, repo_root: pathlib.Path, packet: dict[str, Any], checklist: dict[str, Any], generated_at: str) -> dict[str, Any]:
    docs_roadmap_hardening = _file_contains(repo_root, 'docs/roadmap.md', 'beta-hardening')
    local_rc_passed = _packet_section(packet, 'rc_evidence').get('status') == 'passed'
    source_archive = _packet_section(packet, 'source_archive')
    github_actions = _packet_section(packet, 'github_actions')
    issue_closure = _packet_section(packet, 'rc_issue_closure_evidence')
    hosted_cookie = _packet_section(packet, 'hosted_cookie_auth')
    beta_slo = _packet_section(packet, 'beta_slo_baseline')
    signed_worktree = _packet_section(packet, 'signed_off_worktree')
    operator_signoff = _packet_section(packet, 'operator_signoff')

    rows = [
        _row(
            'scope_freeze',
            'Release discipline',
            _bool_status(docs_roadmap_hardening),
            'Freeze new gameplay scope until RC1 security, runtime, docs, and release evidence are boring.',
            'docs/roadmap.md states the project is in beta-hardening territory.'
            if docs_roadmap_hardening
            else 'No beta-hardening scope-freeze statement found.',
            'Keep gameplay feature expansion out of RC1 unless it fixes a gate failure.',
        ),
        _row(
            'local_rc_gate',
            'Release evidence',
            'implemented' if local_rc_passed else 'missing',
            'Run the full local closed-beta RC gate and save evidence.',
            f"RC evidence status: {_packet_section(packet, 'rc_evidence').get('status') or 'missing'}; "
            f"gates: {_packet_section(packet, 'rc_evidence').get('gate_count') or 0}.",
            'Run make closed-beta-rc and make rc-handoff-artifacts.' if not local_rc_passed else '',
        ),
        _row(
            'github_actions_gate',
            'Release evidence',
            'implemented' if github_actions.get('status') == 'passed' else 'external-required',
            'Run AIDM CI and manual Closed Beta RC GitHub Actions for the signed-off commit.',
            f"GitHub Actions evidence status: {github_actions.get('status') or 'missing'}; "
            f"Closed Beta RC URL: {github_actions.get('closed_beta_rc_run_url') or 'missing'}.",
            'Run the manual Closed Beta RC workflow and rerun make github-actions-evidence.',
        ),
        _row(
            'issue_evidence_closure',
            'Release evidence',
            'implemented' if issue_closure.get('status') == 'passed' else 'external-required',
            'Close RC1 gate issues with evidence snippets, not code summaries.',
            f"Issue evidence status: {_packet_status(packet, 'issue_evidence')}; "
            f"closure status: {issue_closure.get('status') or 'missing'}; "
            f"open issues: {issue_closure.get('open_issues') or 'missing'}; "
            f"matching evidence comments: {issue_closure.get('matching_comments') or 'missing'}.",
            'Review/post issue snippets and close issues #3-#9 only after external proof is attached.',
        ),
        _row(
            'hosted_deployment_readiness',
            'Hosted proof',
            'implemented' if _hosted_passed(packet, 'deployment_readiness') else 'external-required',
            'Validate deployment readiness against a real hosted/staging target.',
            f"Deployment readiness status: {_packet_status(packet, 'deployment_readiness')}; "
            f"target: {_target_url(_packet_section(packet, 'deployment_readiness')) or 'missing'}.",
            'Run make deployment-readiness against the hosted/staging URL.',
        ),
        _row(
            'socketio_worker_model',
            'Runtime quality',
            _bool_status(
                _path_exists(repo_root, 'docs/socketio_worker_model.md')
                and _path_exists(repo_root, 'scripts/check_socketio_worker_model_decision.py')
                and _command_passed(packet, 'Socket.IO worker model decision')
            ),
            'Decide and document the Socket.IO deployment model.',
            'docs/socketio_worker_model.md plus the RC worker-model decision gate are present.'
            if _path_exists(repo_root, 'docs/socketio_worker_model.md')
            else 'Socket.IO worker model doc/check missing.',
            'Keep hosted RC1 single-worker proof attached in final signoff.',
        ),
        _row(
            'state_boundary_inventory',
            'Data integrity',
            _bool_status(
                _path_exists(repo_root, 'docs/state_snapshot_writer_inventory.md')
                and _path_exists(repo_root, 'scripts/check_state_snapshot_writers.py')
                and _command_passed(packet, 'State snapshot writer inventory')
            ),
            'Track and enforce the Session.state_snapshot writer inventory.',
            'State writer inventory doc and policy checker are present and pass in local RC evidence.',
            'Keep every direct state_snapshot writer categorized before merging new writers.',
        ),
        _row(
            'hosted_cookie_auth_proof',
            'Hosted proof',
            'implemented' if _hosted_passed(packet, 'hosted_cookie_auth') else 'external-required',
            'Prove hosted browser auth, cookie-only mode, CSRF, logout cleanup, role refresh, and socket auth.',
            f"Hosted cookie-auth evidence status: {hosted_cookie.get('status') or 'missing'}; "
            f"mode: {hosted_cookie.get('mode') or 'missing'}; target: {hosted_cookie.get('target_url') or 'missing'}.",
            'Run hosted-cookie-auth-smoke against the real hosted/staging target.',
        ),
        _row(
            'beta_slo_visibility',
            'Observability',
            'implemented' if _hosted_passed(packet, 'beta_slo_baseline') else 'external-required',
            'Record beta SLO visibility before inviting more testers.',
            f"Beta SLO baseline status: {beta_slo.get('status') or 'missing'}; target: {beta_slo.get('target_url') or 'missing'}.",
            'Render beta SLO baseline from hosted/staging metrics.',
        ),
        _row(
            'source_archive',
            'Packaging',
            'implemented' if source_archive.get('status') == 'passed' else 'missing',
            'Package a clean source-only RC archive.',
            f"Source archive status: {source_archive.get('status') or 'missing'}; "
            f"path: {_relative_or_absolute(source_archive.get('path') or '')}; sha256: {source_archive.get('sha256') or 'missing'}.",
            'Attach the archive/checksum to the RC issue or release.',
        ),
        _row(
            'tester_onboarding',
            'Tester operations',
            _bool_status(_path_exists(repo_root, 'docs/beta_tester_onboarding.md')),
            'Add tester onboarding guidance.',
            'docs/beta_tester_onboarding.md exists.',
            '',
        ),
        _row(
            'known_limitations_ui',
            'Tester operations',
            _bool_status(
                _file_contains_all(
                    repo_root,
                    'aidm_frontend/src/BetaRuntimeNotesPanel.tsx',
                    (
                        'Beta information',
                        'Current player-impacting problems appear as alerts.',
                        'Its current availability appears in the narration controls.',
                    ),
                )
                and _file_contains_all(
                    repo_root,
                    'aidm_frontend/src/App.runtimeAccess.test.tsx',
                    (
                        'keeps beta information available from the account menu without stale operator guidance',
                        'Beta information',
                    ),
                )
            ),
            'Keep compact Beta information available from the account menu.',
            'Beta information routes player-impacting outages to alerts and optional narration status to its control, with frontend coverage.',
            '',
        ),
        _row(
            'beta_runtime_notices',
            'Tester operations',
            _bool_status(
                _file_contains_all(
                    repo_root,
                    'aidm_frontend/src/App.tsx',
                    (
                        'Beta runtime notices',
                        'Fallback DM active. Ask the table operator to restore the live provider.',
                        'Live DM is unavailable. Ask the table operator to configure the selected provider.',
                        'Narration unavailable; Deepgram is not configured',
                        'showProcessLocalDiagnostic',
                        'Process-local · restart workers',
                    ),
                )
                and _file_contains_all(
                    repo_root,
                    'aidm_frontend/src/App.runtimeAccess.test.tsx',
                    (
                        'keeps unavailable TTS in its accessible control instead of the global alert bar',
                        'hides process-local instructions for a single worker',
                        'shows process-local synchronization only to workspace admins when multiple workers need it',
                        'hides process-local synchronization from non-admin players',
                    ),
                )
            ),
            'Expose actionable player alerts and keep optional/operator state in the relevant controls.',
            'Global notices cover player-impacting provider outages; narration availability and multi-worker synchronization are control-local with role and worker-count coverage.',
            '',
        ),
        _row(
            'session_quality_summary',
            'Observability',
            _bool_status(
                _file_contains(repo_root, 'tests/test_beta_summary.py', '/api/beta/session-quality')
                and _file_contains(repo_root, 'aidm_frontend/src/BetaIncidentPanel.tsx', 'Selected session quality')
            ),
            'Add selected-session quality summaries for operators.',
            '/api/beta/session-quality tests and Ops-tab session quality UI are present.',
            '',
        ),
        _row(
            'campaign_pack_authoring_feedback',
            'Campaign packs',
            _bool_status(
                _file_contains(repo_root, 'aidm_frontend/src/CampaignPackImportDialog.tsx', 'authoring_report')
                and _file_contains(repo_root, 'docs/campaign_packs.md', 'authoring report preview')
            ),
            'Improve campaign-pack authoring feedback with preview, lint, graph, and report surfaces.',
            'Frontend import dialog, pack lint endpoint, docs, and tests expose authoring feedback.',
            '',
        ),
        _row(
            'beta_feedback_prompt',
            'Tester operations',
            _bool_status(
                _file_contains(repo_root, 'aidm_frontend/src/SessionBoard.tsx', 'Beta turn feedback')
                and _file_contains(repo_root, 'aidm_frontend/src/App.tsx', 'fun_score')
                and _file_contains(repo_root, 'tests/test_beta_summary.py', '/api/feedback/coherence')
            ),
            'Add beta feedback prompts for coherence, fun, and rules scores.',
            'Turn quality prompt, backend feedback API, and beta summary tests are present.',
            '',
        ),
        _row(
            'api_contract_strictness',
            'Maintenance',
            'implemented' if _command_passed(packet, 'API type drift check') else 'missing',
            'Keep generated API contracts strict.',
            'API type drift check passes in local RC evidence.',
            'Run make api-types after backend contract changes.',
        ),
        _row(
            'release_evidence_automation',
            'Release evidence',
            _bool_status(
                _path_exists(repo_root, 'scripts/render_release_evidence_packet.py')
                and _path_exists(repo_root, 'scripts/render_release_checklist_status.py')
                and _file_contains(repo_root, 'Makefile', 'rc-handoff-artifacts')
            ),
            'Automate release evidence packet generation.',
            'Release packet, checklist status, signoff draft/action plan, and rc-handoff-artifacts are present.',
            '',
        ),
        _row(
            'dependency_automation',
            'Maintenance',
            _bool_status(_path_exists(repo_root, '.github/dependabot.yml')),
            'Add dependency update automation.',
            '.github/dependabot.yml exists.',
            'Review and merge dependency PRs only after audits pass.',
        ),
        _row(
            'migration_drill',
            'Data integrity',
            'implemented' if _command_passed(packet, 'Migration chain drill') else 'missing',
            'Add a staged migration drill.',
            'Migration chain drill passes in local RC evidence.',
            '',
        ),
        _row(
            'socket_concurrency_smoke',
            'Runtime quality',
            'implemented' if _command_passed(packet, 'Socket concurrency smoke') else 'missing',
            'Add load/concurrency smoke for Socket.IO turn locks.',
            'Socket concurrency smoke passes in local RC evidence.',
            '',
        ),
        _row(
            'operator_incident_export',
            'Observability',
            _bool_status(
                _path_exists(repo_root, 'scripts/export_support_bundle.py')
                and _file_contains(repo_root, 'aidm_frontend/src/BetaIncidentPanel.tsx', 'Export workspace support bundle')
            ),
            'Add operator incident/support-bundle export.',
            'Support bundle endpoint/export script and Ops-tab export UI are present.',
            '',
        ),
        _row(
            'production_server_command',
            'Hosted proof',
            _bool_status(
                _path_exists(repo_root, 'scripts/run_production_server.sh')
                and _file_contains(repo_root, 'docs/beta_runbook.md', 'scripts/run_production_server.sh')
            ),
            'Document the exact production server command.',
            'scripts/run_production_server.sh is documented in the beta runbook.',
            '',
        ),
        _row(
            'auth_mode_matrix',
            'Security',
            _bool_status(_path_exists(repo_root, 'docs/auth_modes.md')),
            'Document local, private, and hosted auth modes.',
            'docs/auth_modes.md exists.',
            '',
        ),
        _row(
            'csp_browser_smoke',
            'Security',
            _bool_status(
                _file_contains(repo_root, 'aidm_frontend/scripts/browser-smoke.cjs', 'assertCspDirectives')
                and _file_contains(repo_root, 'docs/release_checklist.md', 'verifies required security headers and CSP')
            ),
            'Add CSP/security-header validation to browser smoke.',
            'Browser smoke asserts security headers and CSP directives for the built single-origin UI.',
            '',
        ),
        _row(
            'modal_accessibility_regressions',
            'Frontend',
            _bool_status(
                _file_contains_all(
                    repo_root,
                    'aidm_frontend/src/App.test.tsx',
                    (
                        'closes the character delete confirmation with Escape without deleting',
                        'traps modal focus and returns focus to the opener when closed',
                        "findByRole('dialog', { name: 'Create New Campaign' })",
                        "key: 'Escape'",
                        "key: 'Tab'",
                        'toHaveFocus()',
                        'toHaveAccessibleDescription',
                        "method === 'DELETE'",
                    ),
                )
            ),
            'Keep modal accessibility regressions explicit for RC frontend proof.',
            'Frontend tests cover focus placement, Escape close, focus trapping, focus return, dialog descriptions, and danger confirmation cancellation.',
            '',
        ),
        _row(
            'admin_capability_hardening',
            'Security',
            'implemented' if _command_passed(packet, 'Security forbidden smoke') else 'missing',
            'Keep admin/operator capabilities narrow.',
            'Security forbidden smoke passes locally for operator/admin-only endpoints.',
            'Run the same forbidden smoke against hosted/staging before security signoff.',
        ),
        _row(
            'clean_signed_off_worktree',
            'Release evidence',
            'implemented' if signed_worktree.get('status') == 'passed' else 'external-required',
            'Generate final evidence from a clean signed-off worktree.',
            f"Signed-off worktree status: {signed_worktree.get('status') or 'missing'}; {signed_worktree.get('worktree') or 'missing'}.",
            'Commit the RC changes and rerun RC evidence from the clean signed-off commit.',
        ),
        _row(
            'final_operator_signoff',
            'Release evidence',
            'implemented' if operator_signoff.get('status') == 'passed' else 'external-required',
            'Fill final operator signoff manifest and require it before issue closure.',
            f"Operator signoff status: {operator_signoff.get('status') or 'missing'}; "
            f"required complete: {operator_signoff.get('required_complete') or 'missing'}.",
            'Fill tmp/release/operator-signoff.json and run operator-signoff-status --require-complete.',
        ),
    ]

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    if counts.get('missing'):
        status = 'incomplete'
    elif counts.get('external-required'):
        status = 'local-ready-with-external-exceptions'
    else:
        status = 'ready-for-issue-closure'

    return {
        'generated_at': generated_at,
        'status': status,
        'counts': dict(sorted(counts.items())),
        'recommendations': [asdict(row) for row in rows],
    }


def render_markdown(matrix: dict[str, Any]) -> str:
    summary_rows = ['| Status | Count |', '| --- | ---: |']
    for status, count in (matrix.get('counts') or {}).items():
        summary_rows.append(f'| {status} | {count} |')

    recommendation_rows = [
        '| Key | Category | Status | Recommendation | Evidence | Next action |',
        '| --- | --- | --- | --- | --- | --- |',
    ]
    for item in matrix.get('recommendations') or []:
        recommendation_rows.append(
            f"| `{item.get('key')}` | {item.get('category')} | {item.get('status')} | "
            f"{item.get('recommendation')} | {item.get('evidence')} | {item.get('next_action') or ''} |"
        )

    return '\n'.join(
        [
            '# RC Recommendation Matrix',
            '',
            f"- Generated: {matrix.get('generated_at')}",
            f"- Status: {matrix.get('status')}",
            '',
            '## Summary',
            '',
            *summary_rows,
            '',
            '## Recommendations',
            '',
            *recommendation_rows,
            '',
        ]
    )


def write_matrix(matrix: dict[str, Any], *, output: pathlib.Path, json_output: pathlib.Path | None) -> None:
    output_path = _resolve_repo_path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(matrix), encoding='utf-8')
    if json_output is not None:
        json_path = _resolve_repo_path(json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(matrix, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Render an RC recommendation matrix from current evidence.')
    parser.add_argument('--packet-json', type=pathlib.Path, default=DEFAULT_PACKET_JSON)
    parser.add_argument('--checklist-json', type=pathlib.Path, default=DEFAULT_CHECKLIST_JSON)
    parser.add_argument('--output', type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--json-output', type=pathlib.Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument('--generated-at', default='', help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    packet = _load_json(args.packet_json)
    checklist = _load_json(args.checklist_json)
    matrix = build_matrix(
        repo_root=REPO_ROOT,
        packet=packet,
        checklist=checklist,
        generated_at=args.generated_at or _iso_now(),
    )
    write_matrix(matrix, output=args.output, json_output=args.json_output)
    print(f"[rc-recommendation-matrix] Wrote {_relative_or_absolute(_resolve_repo_path(args.output))}.")
    if args.json_output is not None:
        print(f"[rc-recommendation-matrix] Wrote {_relative_or_absolute(_resolve_repo_path(args.json_output))}.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
