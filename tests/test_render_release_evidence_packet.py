from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path

from scripts.render_rc_issue_evidence import ISSUE_SPECS
from scripts.render_release_evidence_packet import (
    _inspect_beta_slo_baseline,
    _inspect_hosted_rc_evidence,
    build_packet,
    main,
    render_packet,
)


def _write_tar(path: Path, members: list[str]) -> None:
    with tarfile.open(path, mode='w:gz') as archive:
        for member_name in members:
            data = b'example'
            info = tarfile.TarInfo(member_name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def _write_visual_smoke(path: Path) -> None:
    path.mkdir()
    for name in (
        'desktop-shell.png',
        'laptop-shell.png',
        'mobile-full.png',
        'mobile-narrow.png',
        'tablet-landscape.png',
        'tablet-portrait.png',
    ):
        (path / name).write_bytes(b'png')


def _write_visual_review(path: Path, visual_smoke_dir: Path) -> None:
    path.write_text(
        '\n'.join(
            [
                '# Visual Smoke Review Evidence',
                '',
                '- Status: passed',
                '- Reviewed: 2026-06-19T00:05:00+00:00',
                f'- Artifact dir: `{visual_smoke_dir}`',
                '- Screenshots: 6/6',
                '- Failures: None.',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _write_beta_slo_baseline(
    path: Path,
    *,
    sample_count: int = 3,
    provider_turn_count: int = 3,
    invite_more_testers: str = 'yes',
    target_url: str = 'https://aidm.example.test',
    commit_sha: str = 'abc1234',
) -> None:
    path.write_text(
        '\n'.join(
            [
                '# Beta SLO Baseline',
                '',
                '## Release Context',
                '',
                f'- Target URL: {target_url}',
                f'- Commit SHA: {commit_sha}',
                '',
                '## Baseline Metrics',
                '',
                '| Metric | Value | Source | Decision |',
                '| --- | ---: | --- | --- |',
                f'| DM response sample count | {sample_count} | `/api/beta/slo` |  |',
                '',
                '## Provider/Model Turn Mix',
                '',
                '| Provider | Model | Turns |',
                '| --- | --- | ---: |',
                f'| gemini | gemini-test | {provider_turn_count} |',
                '',
                '## Gate Decision',
                '',
                f'- Invite more testers: {invite_more_testers}',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _write_github_actions(path: Path) -> None:
    path.write_text(
        '\n'.join(
            [
                '# GitHub Actions Evidence',
                '',
                '- Status: passed',
                '- AIDM CI run URL: `https://github.com/example/AIDM/actions/runs/111`',
                '- Closed Beta RC run URL: `https://github.com/example/AIDM/actions/runs/222`',
                '- Worktree: clean',
                '- Missing: None.',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _write_hosted_rc(path: Path, *, status: str = 'passed', manual_status: str = 'required') -> None:
    manual_evidence = 'evidence-link' if manual_status == 'provided' else ''
    path.write_text(
        '\n'.join(
            [
                '# Hosted RC Evidence',
                '',
                f'- Status: {status}',
                '- Target URL: `https://aidm.example.test`',
                '- Workspace ID: `workspace-1`',
                f"- Dry run: {'True' if status == 'planned' else 'False'}",
                '',
                '## Automated Checks',
                '',
                '| Check | Status | Exit | Evidence | Evidence target | Missing inputs | Validation errors |',
                '| --- | --- | ---: | --- | --- | --- | --- |',
                '| Hosted deployment readiness | passed | 0 | `tmp/release/deployment-readiness-evidence.md` | `https://aidm.example.test` |  |  |',
                '| Hosted beta SLO baseline | passed | 0 | `tmp/release/beta-slo-baseline.md` | `https://aidm.example.test` |  |  |',
                '',
                '## Manual Evidence Still Required',
                '',
                '| Evidence | Status | Link/path/details |',
                '| --- | --- | --- |',
                f'| Hosted database backup/restore proof | {manual_status} | {manual_evidence} |',
                f'| Hosted Socket.IO worker process proof | {manual_status} | {manual_evidence} |',
                f'| Source archive attached to RC issue or release | {manual_status} | {manual_evidence} |',
                f'| External telemetry receipt proof | {manual_status} | {manual_evidence} |',
                '',
            ]
        ),
        encoding='utf-8',
    )


def test_inspect_hosted_rc_evidence_marks_stale_generator_sidecar(tmp_path):
    hosted_rc_path = tmp_path / 'hosted-rc-evidence.md'
    _write_hosted_rc(hosted_rc_path)
    hosted_rc_path.with_suffix('.json').write_text(
        json.dumps(
            {
                'generator': {'path': 'scripts/hosted_rc_evidence_check.py', 'sha256': '0' * 64},
                'command_plan_sha256': '1' * 64,
            }
        ),
        encoding='utf-8',
    )

    inspected = _inspect_hosted_rc_evidence(hosted_rc_path)

    assert inspected['status'] == 'stale'
    assert inspected['generator_freshness'] == 'stale'
    assert inspected['generator_sha256'] == '0' * 64
    assert len(inspected['current_generator_sha256']) == 64
    assert inspected['command_plan_sha256'] == '1' * 64


def test_inspect_hosted_rc_evidence_parses_check_statuses(tmp_path):
    hosted_rc_path = tmp_path / 'hosted-rc-evidence.md'
    _write_hosted_rc(hosted_rc_path)

    inspected = _inspect_hosted_rc_evidence(hosted_rc_path)

    assert inspected['check_count'] == 2
    assert inspected['checks']['Hosted deployment readiness'] == {
        'status': 'passed',
        'exit': '0',
        'evidence_path': 'tmp/release/deployment-readiness-evidence.md',
        'evidence_target_url': 'https://aidm.example.test',
        'missing_inputs': [],
        'validation_errors': [],
    }
    assert inspected['checks']['Hosted beta SLO baseline']['status'] == 'passed'


def test_inspect_beta_slo_baseline_requires_samples_provider_turns_and_decision(tmp_path):
    path = tmp_path / 'beta-slo-baseline.md'
    _write_beta_slo_baseline(path, sample_count=0, provider_turn_count=0, invite_more_testers='yes/no')

    inspected = _inspect_beta_slo_baseline(path)

    assert inspected['status'] == 'incomplete'
    assert inspected['dm_response_sample_count'] == 0
    assert inspected['provider_turn_count'] == 0
    assert inspected['invite_more_testers'] == 'yes/no'
    assert inspected['validation_errors'] == [
        'DM response sample count must be greater than zero',
        'provider/model turn mix must include at least one positive real row',
        'Invite more testers must be explicitly set to yes or no',
    ]


def test_inspect_beta_slo_baseline_passes_only_semantically_complete_yes_decision(tmp_path):
    path = tmp_path / 'beta-slo-baseline.md'
    _write_beta_slo_baseline(path)

    inspected = _inspect_beta_slo_baseline(path)

    assert inspected['status'] == 'passed'
    assert inspected['dm_response_sample_count'] == 3
    assert inspected['provider_turn_count'] == 3
    assert inspected['invite_more_testers'] == 'yes'
    assert inspected['commit_sha'] == 'abc1234'
    assert inspected['validation_errors'] == []


def test_inspect_beta_slo_baseline_fails_explicit_no_expansion_decision(tmp_path):
    path = tmp_path / 'beta-slo-baseline.md'
    _write_beta_slo_baseline(path, invite_more_testers='no')

    inspected = _inspect_beta_slo_baseline(path)

    assert inspected['status'] == 'failed'
    assert inspected['invite_more_testers'] == 'no'
    assert inspected['validation_errors'] == []


def test_inspect_beta_slo_baseline_rejects_non_http_target(tmp_path):
    path = tmp_path / 'beta-slo-baseline.md'
    _write_beta_slo_baseline(path, target_url='staging')

    inspected = _inspect_beta_slo_baseline(path)

    assert inspected['status'] == 'incomplete'
    assert inspected['validation_errors'] == ['Target URL must be an absolute http(s) URL']


def test_inspect_beta_slo_baseline_rejects_stale_rc_commit(tmp_path):
    path = tmp_path / 'beta-slo-baseline.md'
    _write_beta_slo_baseline(path, commit_sha='deadbee')

    inspected = _inspect_beta_slo_baseline(path, expected_commit_sha='abc1234')

    assert inspected['status'] == 'incomplete'
    assert inspected['commit_matches_rc'] is False
    assert inspected['validation_errors'] == ['Commit SHA deadbee does not match current RC commit abc1234']


def test_inspect_hosted_rc_evidence_rejects_invalid_check_rows(tmp_path):
    hosted_rc_path = tmp_path / 'hosted-rc-evidence.md'
    _write_hosted_rc(hosted_rc_path)
    text = hosted_rc_path.read_text(encoding='utf-8')
    hosted_rc_path.write_text(
        text.replace(
            '| Hosted deployment readiness | passed | 0 | `tmp/release/deployment-readiness-evidence.md` | `https://aidm.example.test` |  |  |',
            '| Hosted deployment readiness | invalid-evidence | 0 | `tmp/release/deployment-readiness-evidence.md` | `https://stale.example.test` |  | evidence target URL does not match requested target URL |',
        ),
        encoding='utf-8',
    )

    inspected = _inspect_hosted_rc_evidence(hosted_rc_path)

    assert inspected['status'] == 'invalid-evidence'
    assert inspected['checks']['Hosted deployment readiness']['status'] == 'invalid-evidence'
    assert inspected['checks']['Hosted deployment readiness']['validation_errors'] == [
        'evidence target URL does not match requested target URL'
    ]


def test_build_packet_preserves_markdown_rc_gate_mode_flags(tmp_path):
    rc_evidence_path = tmp_path / 'rc-evidence.md'
    rc_evidence_path.write_text(
        '\n'.join(
            [
                '# Closed Beta RC Evidence',
                '',
                '- Status: passed',
                '- Started: 2026-06-19T00:00:00+00:00',
                '- Finished: 2026-06-19T00:03:00+00:00',
                '- Commit: abc123',
                '- Worktree: clean',
                '- Repo: `/tmp/repo`',
                '- Python: `.venv/bin/python`',
                '- Browser smoke: included',
                '- Dependency audits: included',
                '',
                '## Gates',
                '',
                '| Gate | Status | Exit | Seconds | Command |',
                '| --- | --- | ---: | ---: | --- |',
                '| Browser smoke (single-origin build) | passed | 0 | 1.00 | `npm run smoke:browser` |',
                '| Frontend production dependency audit | passed | 0 | 1.00 | `npm audit --omit=dev` |',
                '',
            ]
        ),
        encoding='utf-8',
    )

    packet = build_packet(
        generated_at='2026-06-19T00:05:00+00:00',
        rc_evidence_path=rc_evidence_path,
        issue_evidence_dir=tmp_path / 'issue-evidence',
        source_archive_path=None,
        visual_smoke_dir=None,
        visual_smoke_review_path=None,
        hosted_cookie_auth_evidence_path=tmp_path / 'hosted-cookie-auth-evidence.md',
        security_forbidden_evidence_path=tmp_path / 'security-forbidden-evidence.md',
        export_import_evidence_path=tmp_path / 'export-import-evidence.md',
        deployment_readiness_evidence_path=tmp_path / 'deployment-readiness-evidence.md',
        beta_slo_baseline_path=tmp_path / 'beta-slo-baseline.md',
        frontend_npm_ci_evidence_path=tmp_path / 'frontend-npm-ci-evidence.md',
        packaging_cleanup_evidence_path=tmp_path / 'packaging-cleanup-evidence.md',
    )
    markdown = render_packet(packet)

    assert packet['rc_evidence']['include_browser_smoke'] is True
    assert packet['rc_evidence']['include_dependency_audits'] is True
    assert 'browser smoke: included; dependency audits: included' in markdown


def _write_operator_signoff(
    path: Path,
    *,
    status: str = 'missing',
    complete: str = '0/18',
    missing: str = '18',
    source_archive_sha256: str = '',
) -> None:
    lines = [
        '# RC Operator Sign-Off Status',
        '',
        f'- Status: {status}',
        '- Manifest: `tmp/release/operator-signoff.json`',
    ]
    if source_archive_sha256:
        lines.append(f'- Source archive SHA256: `{source_archive_sha256}`')
    lines.extend(
        [
            f'- Required complete: {complete}',
            f'- Missing or invalid required items: {missing}',
            '',
        ]
    )
    path.write_text('\n'.join(lines), encoding='utf-8')


def _write_operator_signoff_draft(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                'draft_generated_at': '2026-06-19T00:04:00+00:00',
                'commit': 'abc123',
                'target_url': 'https://<hosted-staging-target>',
                'items': {
                    'github_actions_aidm_ci': {'status': 'provided'},
                    'frontend_npm_ci': {'status': 'provided'},
                    'hosted_cookie_auth': {'status': 'pending'},
                    'multi_worker_socketio_staging': {'status': 'pending'},
                },
            }
        ),
        encoding='utf-8',
    )


def _write_operator_signoff_action_plan(path: Path, *, pending: int = 14) -> None:
    path.write_text(
        '\n'.join(
            [
                '# RC Operator Sign-Off Action Plan',
                '',
                '- Generated: 2026-06-19T00:04:30+00:00',
                '- Status: action-required',
                '- Manifest source: `tmp/release/operator-signoff.draft.json`',
                '- Release: RC1',
                '- Commit: abc123',
                '- Target URL: `https://<hosted-staging-target>`',
                '- Required complete: 4/18',
                f'- Pending actions: {pending}',
                '- Source archive: `tmp/release/aidm-source.tar.gz sha256:abc123sha`',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _write_recommendation_matrix(path: Path) -> None:
    path.write_text(
        '\n'.join(
            [
                '# RC Recommendation Matrix',
                '',
                '- Generated: 2026-06-19T00:04:45+00:00',
                '- Status: local-ready-with-external-exceptions',
                '',
                '## Summary',
                '',
                '| Status | Count |',
                '| --- | ---: |',
                '| implemented | 20 |',
                '| external-required | 6 |',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _write_external_proof_inputs(path: Path) -> None:
    path.write_text(
        '\n'.join(
            [
                '# External Proof Inputs',
                '',
                '- Generated: 2026-06-19T00:04:50+00:00',
                '- Status: action-required',
                '- Required fields: 12',
                '- Conditional fields: 1',
                '- Provided context fields: 3',
                '- External recommendation keys: github_actions_gate, hosted_deployment_readiness',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _write_external_proof_execution_plan(path: Path) -> None:
    path.write_text(
        '\n'.join(
            [
                '# External Proof Execution Plan',
                '',
                '- Generated: 2026-06-19T00:04:52+00:00',
                '- Status: action-required',
                '- Pending actions: 16',
                '- Required fields: 23',
                '- Conditional fields: 1',
                '- External checklist rows: 26',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _write_external_proof_values_template(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                'generated_at': '2026-06-19T00:04:55+00:00',
                'values': {
                    'aidm_ci_run_url': '',
                    'closed_beta_rc_run_url': '',
                    'deployment_readiness_evidence': '',
                },
            }
        ),
        encoding='utf-8',
    )


def _write_external_proof_values_status(
    path: Path,
    *,
    status: str = 'incomplete',
    complete: str = '0/22',
    missing: str = '22',
    invalid: str = '0',
) -> None:
    path.write_text(
        '\n'.join(
            [
                '# External Proof Values Check',
                '',
                '- Generated: 2026-06-19T00:04:56+00:00',
                f'- Status: {status}',
                '- Values file: `tmp/release/external-proof-values.json`',
                '- Values file present: False',
                '- External inputs: `tmp/release/external-proof-inputs.json`',
                '- External inputs status: action-required',
                f'- Required complete: {complete}',
                f'- Missing required fields: {missing}',
                '- Metadata errors: 0',
                f'- Invalid errors: {invalid}',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _write_release_artifact_consistency(path: Path, *, sha256: str = 'd' * 64, status: str = 'passed') -> None:
    path.write_text(
        '\n'.join(
            [
                '# Release Artifact Consistency',
                '',
                '- Generated: 2026-06-19T00:04:57+00:00',
                f'- Status: {status}',
                '- Packet JSON: `tmp/release/release-evidence-packet.json`',
                '- Source archive: `tmp/release/aidm-source.tar.gz`',
                f'- Source archive SHA256: `{sha256}`',
                '',
            ]
        ),
        encoding='utf-8',
    )
    path.with_suffix('.json').write_text(
        json.dumps(
            {
                'status': status,
                'source_archive_sha256': sha256,
                'checks': [{'key': 'source_archive_sha256_actual', 'status': status, 'detail': sha256}],
                'errors': [] if status == 'passed' else ['stale archive checksum'],
            }
        ),
        encoding='utf-8',
    )


def _write_frontend_npm_ci_evidence(path: Path, *, status: str = 'passed') -> None:
    path.write_text(
        '\n'.join(
            [
                '# Frontend npm ci Evidence',
                '',
                '- Generated: 2026-06-19T00:04:58+00:00',
                f'- Status: {status}',
                '- Frontend dir: `aidm_frontend`',
                '- Command: `npm ci`',
                '- Return code: 0',
                '- Duration seconds: 1.5',
                '- package.json present: True',
                '- package-lock.json present: True',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _write_packaging_cleanup_evidence(path: Path, *, status: str = 'passed') -> None:
    path.write_text(
        '\n'.join(
            [
                '# Packaging Cleanup Evidence',
                '',
                '- Generated: 2026-06-19T00:04:59+00:00',
                f'- Status: {status}',
                '- Cleanup script: `scripts/cleanup_artifacts.sh`',
                '- Makefile: `Makefile`',
                '- Source archive: `tmp/release/aidm-source.tar.gz`',
                '- Source archive status: passed',
                '- Source archive forbidden paths: 0',
                '- Source archive large files: 1',
                '- Source archive large files not LFS-tracked: 0',
                '- Source archive unresolved Git LFS pointers: 0',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _write_github_actions_incomplete(path: Path) -> None:
    path.write_text(
        '\n'.join(
            [
                '# GitHub Actions Evidence',
                '',
                '- Status: incomplete',
                '- AIDM CI run URL: `missing`',
                '- Closed Beta RC run URL: `missing`',
                '- Missing: AIDM CI run URL, Closed Beta RC run URL',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _write_issue_evidence(path: Path) -> None:
    path.mkdir()
    for spec in ISSUE_SPECS:
        exceptions = (
            'Attach GitHub Actions `AIDM CI` and `Closed Beta RC` run URLs before closing.'
            if spec.issue_number == 3
            else 'None.'
        )
        (path / f'issue-{spec.issue_number:02d}-{spec.slug}.md').write_text(
            '\n'.join(
                [
                    f'# {spec.title}',
                    '',
                    f'- Issue: #{spec.issue_number}',
                    f'- Remaining exceptions: {exceptions}',
                    '',
                ]
            ),
            encoding='utf-8',
        )


def _write_issue_evidence_without_exceptions(path: Path) -> None:
    path.mkdir()
    for spec in ISSUE_SPECS:
        (path / f'issue-{spec.issue_number:02d}-{spec.slug}.md').write_text(
            '\n'.join(
                [
                    f'# {spec.title}',
                    '',
                    f'- Issue: #{spec.issue_number}',
                    '- Remaining exceptions: None.',
                    '',
                ]
            ),
            encoding='utf-8',
        )


def _write_issue_closure_evidence(
    path: Path,
    *,
    status: str = 'external-required',
    complete: str = '0/7',
    open_issues: str = '7',
    matching_comments: str = '0',
    remaining_exceptions: str = '1',
) -> None:
    path.write_text(
        '\n'.join(
            [
                '# RC Issue Closure Evidence',
                '',
                '- Generated: 2026-06-19T00:05:00+00:00',
                f'- Status: {status}',
                '- Issue dir: `tmp/release/issue-evidence`',
                '- Checked GitHub: True',
                f'- Issues complete: {complete}',
                f'- Open issues: {open_issues}',
                f'- Matching evidence comments: {matching_comments}',
                f'- Local snippets with remaining exceptions: {remaining_exceptions}',
                '',
            ]
        ),
        encoding='utf-8',
    )


def _mark_older_than(path: Path, reference: Path) -> None:
    old_mtime = reference.stat().st_mtime - 60
    os.utime(path, (old_mtime, old_mtime))


def test_build_packet_marks_local_ready_with_external_exceptions(tmp_path):
    rc_evidence_path = tmp_path / 'rc-evidence.json'
    issue_dir = tmp_path / 'issue-evidence'
    issue_closure_path = tmp_path / 'rc-issue-closure-evidence.md'
    archive_path = tmp_path / 'aidm-source-clean.tar.gz'
    visual_smoke_dir = tmp_path / 'visual-smoke-run'
    visual_review_path = tmp_path / 'visual-smoke-review.md'
    github_actions_path = tmp_path / 'github-actions-evidence.md'
    hosted_rc_path = tmp_path / 'hosted-rc-evidence.md'
    operator_signoff_path = tmp_path / 'operator-signoff-status.md'
    operator_signoff_draft_path = tmp_path / 'operator-signoff.draft.json'
    operator_signoff_action_plan_path = tmp_path / 'operator-signoff-action-plan.md'
    operator_signoff_from_inputs_status_path = tmp_path / 'operator-signoff.from-inputs-status.md'
    recommendation_matrix_path = tmp_path / 'rc-recommendation-matrix.md'
    external_proof_inputs_path = tmp_path / 'external-proof-inputs.md'
    external_proof_execution_plan_path = tmp_path / 'external-proof-execution-plan.md'
    external_proof_values_template_path = tmp_path / 'external-proof-values.example.json'
    external_proof_values_status_path = tmp_path / 'external-proof-values-status.md'
    release_artifact_consistency_path = tmp_path / 'release-artifact-consistency.md'
    hosted_auth_path = tmp_path / 'hosted-cookie-auth-evidence.md'
    security_path = tmp_path / 'security-forbidden-evidence.md'
    export_import_path = tmp_path / 'export-import-evidence.md'
    readiness_path = tmp_path / 'deployment-readiness-evidence.md'
    slo_path = tmp_path / 'beta-slo-baseline.md'
    frontend_npm_ci_path = tmp_path / 'frontend-npm-ci-evidence.md'
    packaging_cleanup_path = tmp_path / 'packaging-cleanup-evidence.md'
    rc_evidence_path.write_text(
        json.dumps(
            {
                'status': 'passed',
                'commit': 'abc123',
                'worktree': 'dirty (4 changed/untracked paths; 3 tracked, 1 untracked)',
                'finished_at': '2026-06-19T00:02:00+00:00',
                'include_browser_smoke': True,
                'include_dependency_audits': True,
                'commands': [
                    {
                        'label': 'Backend tests',
                        'status': 'passed',
                        'returncode': 0,
                        'duration_seconds': 1.2,
                        'command': 'pytest',
                    }
                ],
            }
        ),
        encoding='utf-8',
    )
    _write_issue_evidence(issue_dir)
    _write_issue_closure_evidence(issue_closure_path)
    _write_tar(archive_path, ['AIDM-main/README.md'])
    _write_visual_smoke(visual_smoke_dir)
    _write_visual_review(visual_review_path, visual_smoke_dir)
    _write_github_actions(github_actions_path)
    _write_hosted_rc(hosted_rc_path)
    signoff_source_sha = 'a' * 64
    _write_operator_signoff(
        operator_signoff_path,
        status='incomplete',
        complete='4/18',
        missing='14',
        source_archive_sha256=signoff_source_sha,
    )
    _write_operator_signoff_draft(operator_signoff_draft_path)
    _write_operator_signoff_action_plan(operator_signoff_action_plan_path)
    _write_operator_signoff(operator_signoff_from_inputs_status_path, status='incomplete', complete='4/18', missing='14')
    _write_recommendation_matrix(recommendation_matrix_path)
    _write_external_proof_inputs(external_proof_inputs_path)
    _write_external_proof_execution_plan(external_proof_execution_plan_path)
    _write_external_proof_values_template(external_proof_values_template_path)
    _write_external_proof_values_status(external_proof_values_status_path)
    _write_release_artifact_consistency(release_artifact_consistency_path)
    _write_frontend_npm_ci_evidence(frontend_npm_ci_path)
    _write_packaging_cleanup_evidence(packaging_cleanup_path)
    hosted_auth_path.write_text(
        '\n'.join(
            [
                '# Hosted Cookie Auth Evidence',
                '',
                '- Status: passed',
                '- Mode: isolated',
                '- Target URL: `isolated local runtime`',
                '',
            ]
        ),
        encoding='utf-8',
    )
    security_path.write_text(
        '\n'.join(
            [
                '# Security Forbidden Evidence',
                '',
                '- Status: passed',
                '- Mode: isolated',
                '- Target URL: `isolated local runtime`',
                '',
            ]
        ),
        encoding='utf-8',
    )
    export_import_path.write_text(
        '\n'.join(
            [
                '# Session Export/Import Evidence',
                '',
                '- Status: passed',
                '- Mode: isolated',
                '- Target URL: `isolated local runtime`',
                '',
            ]
        ),
        encoding='utf-8',
    )
    readiness_path.write_text(
        '\n'.join(
            [
                '# Deployment Readiness Evidence',
                '',
                '- Status: passed',
                '- Target URL: `not checked`',
                '',
            ]
        ),
        encoding='utf-8',
    )

    packet = build_packet(
        generated_at='2026-06-19T00:05:00+00:00',
        rc_evidence_path=rc_evidence_path,
        issue_evidence_dir=issue_dir,
        rc_issue_closure_evidence_path=issue_closure_path,
        source_archive_path=archive_path,
        visual_smoke_dir=visual_smoke_dir,
        visual_smoke_review_path=visual_review_path,
        github_actions_evidence_path=github_actions_path,
        hosted_rc_evidence_path=hosted_rc_path,
        operator_signoff_status_path=operator_signoff_path,
        operator_signoff_draft_path=operator_signoff_draft_path,
        operator_signoff_action_plan_path=operator_signoff_action_plan_path,
        operator_signoff_from_inputs_status_path=operator_signoff_from_inputs_status_path,
        recommendation_matrix_path=recommendation_matrix_path,
        external_proof_inputs_path=external_proof_inputs_path,
        external_proof_execution_plan_path=external_proof_execution_plan_path,
        external_proof_values_template_path=external_proof_values_template_path,
        external_proof_values_status_path=external_proof_values_status_path,
        release_artifact_consistency_path=release_artifact_consistency_path,
        hosted_cookie_auth_evidence_path=hosted_auth_path,
        security_forbidden_evidence_path=security_path,
        export_import_evidence_path=export_import_path,
        deployment_readiness_evidence_path=readiness_path,
        beta_slo_baseline_path=slo_path,
        frontend_npm_ci_evidence_path=frontend_npm_ci_path,
        packaging_cleanup_evidence_path=packaging_cleanup_path,
    )
    markdown = render_packet(packet)

    assert packet['overall_status'] == 'local-ready-with-external-exceptions'
    assert packet['rc_evidence']['worktree'] == 'dirty (4 changed/untracked paths; 3 tracked, 1 untracked)'
    assert packet['rc_evidence']['include_browser_smoke'] is True
    assert packet['rc_evidence']['include_dependency_audits'] is True
    assert packet['signed_off_worktree']['status'] == 'dirty'
    assert packet['deployment_readiness']['status'] == 'env-only'
    assert packet['frontend_npm_ci']['status'] == 'passed'
    assert packet['frontend_npm_ci']['command'] == 'npm ci'
    assert packet['packaging_cleanup']['status'] == 'passed'
    assert packet['packaging_cleanup']['forbidden_paths'] == '0'
    assert packet['packaging_cleanup']['large_files'] == '1'
    assert packet['packaging_cleanup']['large_files_not_lfs_tracked'] == '0'
    assert packet['packaging_cleanup']['unresolved_lfs_pointers'] == '0'
    assert packet['rc_issue_closure_evidence']['status'] == 'external-required'
    assert packet['rc_issue_closure_evidence']['open_issues'] == '7'
    assert packet['visual_smoke_review']['status'] == 'passed'
    assert packet['github_actions']['status'] == 'passed'
    assert packet['github_actions']['worktree'] == 'clean'
    assert packet['hosted_rc_evidence']['status'] == 'passed'
    assert packet['operator_signoff']['status'] == 'incomplete'
    assert packet['operator_signoff']['required_complete'] == '4/18'
    assert packet['operator_signoff']['source_archive_sha256'] == signoff_source_sha
    assert packet['operator_signoff_draft']['status'] == 'present'
    assert packet['operator_signoff_draft']['provided_count'] == 2
    assert packet['operator_signoff_draft']['pending_count'] == 2
    assert packet['operator_signoff_action_plan']['status'] == 'action-required'
    assert packet['operator_signoff_action_plan']['pending_actions'] == '14'
    assert packet['operator_signoff_from_inputs']['status'] == 'incomplete'
    assert packet['operator_signoff_from_inputs']['required_complete'] == '4/18'
    assert packet['recommendation_matrix']['status'] == 'local-ready-with-external-exceptions'
    assert packet['recommendation_matrix']['summary'] == 'implemented: 20, external-required: 6'
    assert packet['external_proof_inputs']['status'] == 'action-required'
    assert packet['external_proof_inputs']['required_fields'] == '12'
    assert packet['external_proof_execution_plan']['status'] == 'action-required'
    assert packet['external_proof_execution_plan']['pending_actions'] == '16'
    assert packet['external_proof_execution_plan']['external_checklist_rows'] == '26'
    assert packet['external_proof_values_template']['status'] == 'present'
    assert packet['external_proof_values_template']['field_count'] == 3
    assert packet['external_proof_values_status']['status'] == 'incomplete'
    assert packet['external_proof_values_status']['required_complete'] == '0/22'
    assert packet['external_proof_values_status']['missing_required_fields'] == '22'
    assert packet['release_artifact_consistency']['status'] == 'passed'
    assert packet['release_artifact_consistency']['check_count'] == 1
    assert packet['release_artifact_consistency']['source_archive_sha256'] == 'd' * 64
    assert packet['hosted_rc_evidence']['check_count'] == 2
    assert packet['hosted_rc_evidence']['checks']['Hosted deployment readiness']['status'] == 'passed'
    assert (
        packet['hosted_rc_evidence']['checks']['Hosted deployment readiness']['evidence_target_url']
        == 'https://aidm.example.test'
    )
    assert packet['hosted_rc_evidence']['manual_required_count'] == 4
    assert packet['hosted_rc_evidence']['manual_evidence'][0] == {
        'label': 'Hosted database backup/restore proof',
        'status': 'required',
        'evidence': '',
    }
    assert packet['hosted_cookie_auth']['status'] == 'passed'
    assert packet['security_forbidden']['status'] == 'passed'
    assert packet['export_import']['status'] == 'passed'
    assert packet['beta_slo_baseline']['status'] == 'missing'
    assert packet['beta_tester_onboarding']['status'] == 'passed'
    assert packet['hosted_signoff_checklist'][0]['evidence'] == 'GitHub Actions CI and Closed Beta RC run URLs'
    assert '| Source archive | passed |' in markdown
    assert 'large files: 0 (0 not LFS-tracked)' in markdown
    assert 'sha256:' in markdown
    assert 'browser smoke: included; dependency audits: included' in markdown
    assert '- RC worktree: dirty (4 changed/untracked paths; 3 tracked, 1 untracked)' in markdown
    assert '| Clean signed-off worktree | dirty |' in markdown
    assert '| Visual smoke screenshots | passed |' in markdown
    assert '| Visual smoke review | passed |' in markdown
    assert '| GitHub Actions evidence | passed |' in markdown
    assert 'worktree: clean' in markdown
    assert '| Hosted RC evidence | passed |' in markdown
    assert '| Operator sign-off status | incomplete |' in markdown
    assert f'source archive sha256: {signoff_source_sha}' in markdown
    assert '| Operator sign-off draft | present |' in markdown
    assert '| Operator sign-off action plan | action-required |' in markdown
    assert '| Operator sign-off from inputs preview | incomplete |' in markdown
    assert '| RC recommendation matrix | local-ready-with-external-exceptions |' in markdown
    assert '| External proof inputs | action-required |' in markdown
    assert '| External proof execution plan | action-required |' in markdown
    assert '| External proof values template | present |' in markdown
    assert '| External proof values status | incomplete |' in markdown
    assert '| Release artifact consistency | passed |' in markdown
    assert f"source archive sha256: {'d' * 64}" in markdown
    assert 'checks: 2, manual required: 4' in markdown
    assert '| Hosted cookie-auth evidence | passed |' in markdown
    assert '| Security forbidden evidence | passed |' in markdown
    assert '| Session export/import evidence | passed |' in markdown
    assert '| Frontend npm ci evidence | passed |' in markdown
    assert '| Packaging cleanup evidence | passed |' in markdown
    assert 'large files not LFS-tracked: 0' in markdown
    assert '| RC issue closure evidence | external-required |' in markdown
    assert '| Beta tester onboarding | passed |' in markdown
    assert '| #3 | Closed Beta RC1: Preflight gates | Attach GitHub Actions' in markdown
    assert '## Hosted Sign-Off Checklist' in markdown
    assert '| #3 #5 #8 | Hosted/staging deployment readiness |' in markdown
    assert '| #4 | Visual-smoke screenshot review |' in markdown
    assert '| #8 | Hosted beta SLO baseline |' in markdown
    slo_check = next(item for item in packet['hosted_signoff_checklist'] if item['evidence'] == 'Hosted beta SLO baseline')
    assert '--commit-sha <signed-off-commit-sha>' in slo_check['command']
    assert '--invite-more-testers <yes-or-no>' in slo_check['command']
    assert '--output tmp/release/beta-slo-baseline.md' in slo_check['command']
    assert '--evidence-report' not in slo_check['command']
    assert 'deployment-readiness-evidence.md' not in slo_check['command']


def test_build_packet_marks_local_handoff_artifacts_stale_when_older_than_rc_evidence(tmp_path):
    rc_evidence_path = tmp_path / 'rc-evidence.json'
    issue_dir = tmp_path / 'issue-evidence'
    archive_path = tmp_path / 'aidm-source-clean.tar.gz'
    visual_smoke_dir = tmp_path / 'visual-smoke-run'
    visual_review_path = tmp_path / 'visual-smoke-review.md'
    hosted_auth_path = tmp_path / 'hosted-cookie-auth-evidence.md'
    security_path = tmp_path / 'security-forbidden-evidence.md'
    export_import_path = tmp_path / 'export-import-evidence.md'
    readiness_path = tmp_path / 'deployment-readiness-evidence.md'
    slo_path = tmp_path / 'beta-slo-baseline.md'
    frontend_npm_ci_path = tmp_path / 'frontend-npm-ci-evidence.md'
    packaging_cleanup_path = tmp_path / 'packaging-cleanup-evidence.md'
    release_artifact_consistency_path = tmp_path / 'release-artifact-consistency.md'

    _write_issue_evidence_without_exceptions(issue_dir)
    _write_tar(archive_path, ['AIDM-main/README.md'])
    _write_visual_smoke(visual_smoke_dir)
    _write_visual_review(visual_review_path, visual_smoke_dir)
    _write_frontend_npm_ci_evidence(frontend_npm_ci_path)
    _write_packaging_cleanup_evidence(packaging_cleanup_path)
    _write_release_artifact_consistency(release_artifact_consistency_path)
    _write_beta_slo_baseline(slo_path)
    hosted_auth_path.write_text('- Status: passed\n- Mode: isolated\n- Target URL: `isolated local runtime`\n', encoding='utf-8')
    security_path.write_text('- Status: passed\n- Mode: isolated\n- Target URL: `isolated local runtime`\n', encoding='utf-8')
    export_import_path.write_text('- Status: passed\n- Mode: isolated\n- Target URL: `isolated local runtime`\n', encoding='utf-8')
    rc_evidence_path.write_text(
        json.dumps(
            {
                'status': 'passed',
                'commit': 'abc1234',
                'commands': [],
                'git_worktree': {'state': 'clean', 'dirty': False},
            }
        ),
        encoding='utf-8',
    )
    for stale_path in (archive_path, frontend_npm_ci_path, packaging_cleanup_path, slo_path):
        _mark_older_than(stale_path, rc_evidence_path)

    packet = build_packet(
        generated_at='2026-06-19T00:05:00+00:00',
        rc_evidence_path=rc_evidence_path,
        issue_evidence_dir=issue_dir,
        source_archive_path=archive_path,
        visual_smoke_dir=visual_smoke_dir,
        visual_smoke_review_path=visual_review_path,
        hosted_cookie_auth_evidence_path=hosted_auth_path,
        security_forbidden_evidence_path=security_path,
        export_import_evidence_path=export_import_path,
        deployment_readiness_evidence_path=readiness_path,
        beta_slo_baseline_path=slo_path,
        frontend_npm_ci_evidence_path=frontend_npm_ci_path,
        packaging_cleanup_evidence_path=packaging_cleanup_path,
        release_artifact_consistency_path=release_artifact_consistency_path,
    )

    assert packet['overall_status'] == 'incomplete'
    assert packet['source_archive']['status'] == 'stale'
    assert packet['source_archive']['previous_status'] == 'passed'
    assert packet['source_archive']['freshness'] == 'stale'
    assert packet['frontend_npm_ci']['status'] == 'stale'
    assert packet['frontend_npm_ci']['previous_status'] == 'passed'
    assert packet['packaging_cleanup']['status'] == 'stale'
    assert packet['packaging_cleanup']['previous_status'] == 'passed'
    assert packet['beta_slo_baseline']['status'] == 'stale'
    assert packet['beta_slo_baseline']['previous_status'] == 'passed'
    assert packet['beta_slo_baseline']['freshness'] == 'stale'


def test_build_packet_marks_missing_release_artifact_consistency_incomplete(tmp_path):
    rc_evidence_path = tmp_path / 'rc-evidence.json'
    issue_dir = tmp_path / 'issue-evidence'
    archive_path = tmp_path / 'aidm-source-clean.tar.gz'
    visual_smoke_dir = tmp_path / 'visual-smoke-run'
    visual_review_path = tmp_path / 'visual-smoke-review.md'
    hosted_auth_path = tmp_path / 'hosted-cookie-auth-evidence.md'
    security_path = tmp_path / 'security-forbidden-evidence.md'
    export_import_path = tmp_path / 'export-import-evidence.md'
    readiness_path = tmp_path / 'deployment-readiness-evidence.md'
    slo_path = tmp_path / 'beta-slo-baseline.md'
    frontend_npm_ci_path = tmp_path / 'frontend-npm-ci-evidence.md'
    packaging_cleanup_path = tmp_path / 'packaging-cleanup-evidence.md'
    missing_consistency_path = tmp_path / 'missing-release-artifact-consistency.md'

    rc_evidence_path.write_text(
        json.dumps({'status': 'passed', 'commands': [], 'git_worktree': {'state': 'clean', 'dirty': False}}),
        encoding='utf-8',
    )
    _write_issue_evidence_without_exceptions(issue_dir)
    _write_tar(archive_path, ['AIDM-main/README.md'])
    _write_visual_smoke(visual_smoke_dir)
    _write_visual_review(visual_review_path, visual_smoke_dir)
    _write_frontend_npm_ci_evidence(frontend_npm_ci_path)
    _write_packaging_cleanup_evidence(packaging_cleanup_path)
    hosted_auth_path.write_text('- Status: passed\n- Mode: isolated\n- Target URL: `isolated local runtime`\n', encoding='utf-8')
    security_path.write_text('- Status: passed\n- Mode: isolated\n- Target URL: `isolated local runtime`\n', encoding='utf-8')
    export_import_path.write_text('- Status: passed\n- Mode: isolated\n- Target URL: `isolated local runtime`\n', encoding='utf-8')

    packet = build_packet(
        generated_at='2026-06-19T00:05:00+00:00',
        rc_evidence_path=rc_evidence_path,
        issue_evidence_dir=issue_dir,
        source_archive_path=archive_path,
        visual_smoke_dir=visual_smoke_dir,
        visual_smoke_review_path=visual_review_path,
        hosted_cookie_auth_evidence_path=hosted_auth_path,
        security_forbidden_evidence_path=security_path,
        export_import_evidence_path=export_import_path,
        deployment_readiness_evidence_path=readiness_path,
        beta_slo_baseline_path=slo_path,
        frontend_npm_ci_evidence_path=frontend_npm_ci_path,
        packaging_cleanup_evidence_path=packaging_cleanup_path,
        release_artifact_consistency_path=missing_consistency_path,
    )

    assert packet['release_artifact_consistency']['status'] == 'missing'
    assert packet['overall_status'] == 'incomplete'


def test_build_packet_marks_github_actions_evidence_stale_when_older_than_rc_evidence(tmp_path):
    rc_evidence_path = tmp_path / 'rc-evidence.json'
    issue_dir = tmp_path / 'issue-evidence'
    archive_path = tmp_path / 'aidm-source-clean.tar.gz'
    visual_smoke_dir = tmp_path / 'visual-smoke-run'
    visual_review_path = tmp_path / 'visual-smoke-review.md'
    github_actions_path = tmp_path / 'github-actions-evidence.md'
    hosted_auth_path = tmp_path / 'hosted-cookie-auth-evidence.md'
    security_path = tmp_path / 'security-forbidden-evidence.md'
    export_import_path = tmp_path / 'export-import-evidence.md'
    readiness_path = tmp_path / 'deployment-readiness-evidence.md'
    slo_path = tmp_path / 'beta-slo-baseline.md'
    frontend_npm_ci_path = tmp_path / 'frontend-npm-ci-evidence.md'
    packaging_cleanup_path = tmp_path / 'packaging-cleanup-evidence.md'
    release_artifact_consistency_path = tmp_path / 'release-artifact-consistency.md'

    _write_github_actions(github_actions_path)
    rc_evidence_path.write_text(
        json.dumps({'status': 'passed', 'commands': [], 'git_worktree': {'state': 'clean', 'dirty': False}}),
        encoding='utf-8',
    )
    _mark_older_than(github_actions_path, rc_evidence_path)
    _write_issue_evidence_without_exceptions(issue_dir)
    _write_tar(archive_path, ['AIDM-main/README.md'])
    _write_visual_smoke(visual_smoke_dir)
    _write_visual_review(visual_review_path, visual_smoke_dir)
    _write_frontend_npm_ci_evidence(frontend_npm_ci_path)
    _write_packaging_cleanup_evidence(packaging_cleanup_path)
    _write_release_artifact_consistency(release_artifact_consistency_path)
    hosted_auth_path.write_text('- Status: passed\n- Mode: isolated\n- Target URL: `isolated local runtime`\n', encoding='utf-8')
    security_path.write_text('- Status: passed\n- Mode: isolated\n- Target URL: `isolated local runtime`\n', encoding='utf-8')
    export_import_path.write_text('- Status: passed\n- Mode: isolated\n- Target URL: `isolated local runtime`\n', encoding='utf-8')

    packet = build_packet(
        generated_at='2026-06-19T00:05:00+00:00',
        rc_evidence_path=rc_evidence_path,
        issue_evidence_dir=issue_dir,
        source_archive_path=archive_path,
        visual_smoke_dir=visual_smoke_dir,
        visual_smoke_review_path=visual_review_path,
        github_actions_evidence_path=github_actions_path,
        hosted_cookie_auth_evidence_path=hosted_auth_path,
        security_forbidden_evidence_path=security_path,
        export_import_evidence_path=export_import_path,
        deployment_readiness_evidence_path=readiness_path,
        beta_slo_baseline_path=slo_path,
        frontend_npm_ci_evidence_path=frontend_npm_ci_path,
        packaging_cleanup_evidence_path=packaging_cleanup_path,
        release_artifact_consistency_path=release_artifact_consistency_path,
    )

    assert packet['github_actions']['status'] == 'stale'
    assert packet['github_actions']['previous_status'] == 'passed'
    assert packet['github_actions']['freshness'] == 'stale'
    assert packet['overall_status'] == 'local-ready-with-external-exceptions'


def test_build_packet_preserves_incomplete_github_actions_status_when_stale(tmp_path):
    rc_evidence_path = tmp_path / 'rc-evidence.json'
    issue_dir = tmp_path / 'issue-evidence'
    archive_path = tmp_path / 'aidm-source-clean.tar.gz'
    visual_smoke_dir = tmp_path / 'visual-smoke-run'
    visual_review_path = tmp_path / 'visual-smoke-review.md'
    github_actions_path = tmp_path / 'github-actions-evidence.md'
    hosted_auth_path = tmp_path / 'hosted-cookie-auth-evidence.md'
    security_path = tmp_path / 'security-forbidden-evidence.md'
    export_import_path = tmp_path / 'export-import-evidence.md'
    readiness_path = tmp_path / 'deployment-readiness-evidence.md'
    slo_path = tmp_path / 'beta-slo-baseline.md'
    frontend_npm_ci_path = tmp_path / 'frontend-npm-ci-evidence.md'
    packaging_cleanup_path = tmp_path / 'packaging-cleanup-evidence.md'

    _write_github_actions_incomplete(github_actions_path)
    rc_evidence_path.write_text(
        json.dumps({'status': 'passed', 'commands': [], 'git_worktree': {'state': 'clean', 'dirty': False}}),
        encoding='utf-8',
    )
    _mark_older_than(github_actions_path, rc_evidence_path)
    _write_issue_evidence_without_exceptions(issue_dir)
    _write_tar(archive_path, ['AIDM-main/README.md'])
    _write_visual_smoke(visual_smoke_dir)
    _write_visual_review(visual_review_path, visual_smoke_dir)
    _write_frontend_npm_ci_evidence(frontend_npm_ci_path)
    _write_packaging_cleanup_evidence(packaging_cleanup_path)
    hosted_auth_path.write_text('- Status: passed\n- Mode: isolated\n- Target URL: `isolated local runtime`\n', encoding='utf-8')
    security_path.write_text('- Status: passed\n- Mode: isolated\n- Target URL: `isolated local runtime`\n', encoding='utf-8')
    export_import_path.write_text('- Status: passed\n- Mode: isolated\n- Target URL: `isolated local runtime`\n', encoding='utf-8')

    packet = build_packet(
        generated_at='2026-06-19T00:05:00+00:00',
        rc_evidence_path=rc_evidence_path,
        issue_evidence_dir=issue_dir,
        source_archive_path=archive_path,
        visual_smoke_dir=visual_smoke_dir,
        visual_smoke_review_path=visual_review_path,
        github_actions_evidence_path=github_actions_path,
        hosted_cookie_auth_evidence_path=hosted_auth_path,
        security_forbidden_evidence_path=security_path,
        export_import_evidence_path=export_import_path,
        deployment_readiness_evidence_path=readiness_path,
        beta_slo_baseline_path=slo_path,
        frontend_npm_ci_evidence_path=frontend_npm_ci_path,
        packaging_cleanup_evidence_path=packaging_cleanup_path,
    )

    assert packet['github_actions']['status'] == 'incomplete'
    assert packet['github_actions']['freshness'] == 'stale'
    assert packet['github_actions']['previous_status'] == 'incomplete'
    assert packet['github_actions']['missing'] == 'AIDM CI run URL, Closed Beta RC run URL'


def test_main_writes_markdown_and_json_packet(tmp_path):
    rc_evidence_path = tmp_path / 'rc-evidence.json'
    issue_dir = tmp_path / 'issue-evidence'
    issue_closure_path = tmp_path / 'rc-issue-closure-evidence.md'
    archive_path = tmp_path / 'aidm-source-clean.tar.gz'
    visual_smoke_dir = tmp_path / 'visual-smoke-run'
    visual_review_path = tmp_path / 'visual-smoke-review.md'
    github_actions_path = tmp_path / 'github-actions-evidence.md'
    hosted_rc_path = tmp_path / 'hosted-rc-evidence.md'
    operator_signoff_path = tmp_path / 'operator-signoff-status.md'
    operator_signoff_draft_path = tmp_path / 'operator-signoff.draft.json'
    operator_signoff_action_plan_path = tmp_path / 'operator-signoff-action-plan.md'
    operator_signoff_from_inputs_status_path = tmp_path / 'operator-signoff.from-inputs-status.md'
    recommendation_matrix_path = tmp_path / 'rc-recommendation-matrix.md'
    external_proof_inputs_path = tmp_path / 'external-proof-inputs.md'
    external_proof_execution_plan_path = tmp_path / 'external-proof-execution-plan.md'
    external_proof_values_template_path = tmp_path / 'external-proof-values.example.json'
    external_proof_values_status_path = tmp_path / 'external-proof-values-status.md'
    hosted_auth_path = tmp_path / 'hosted-cookie-auth-evidence.md'
    security_path = tmp_path / 'security-forbidden-evidence.md'
    export_import_path = tmp_path / 'export-import-evidence.md'
    readiness_path = tmp_path / 'deployment-readiness-evidence.md'
    slo_path = tmp_path / 'beta-slo-baseline.md'
    frontend_npm_ci_path = tmp_path / 'frontend-npm-ci-evidence.md'
    packaging_cleanup_path = tmp_path / 'packaging-cleanup-evidence.md'
    output_path = tmp_path / 'release-evidence-packet.md'
    json_output_path = tmp_path / 'release-evidence-packet.json'

    rc_evidence_path.write_text(
        json.dumps(
            {
                'status': 'passed',
                'commit': 'abc1234',
                'commands': [],
                'git_worktree': {'state': 'clean', 'dirty': False},
            }
        ),
        encoding='utf-8',
    )
    _write_issue_evidence(issue_dir)
    _write_issue_closure_evidence(
        issue_closure_path,
        status='passed',
        complete='7/7',
        open_issues='0',
        matching_comments='7',
        remaining_exceptions='0',
    )
    _write_tar(archive_path, ['AIDM-main/README.md'])
    _write_visual_smoke(visual_smoke_dir)
    _write_visual_review(visual_review_path, visual_smoke_dir)
    _write_github_actions(github_actions_path)
    _write_hosted_rc(hosted_rc_path)
    signoff_source_sha = 'b' * 64
    _write_operator_signoff(
        operator_signoff_path,
        status='passed',
        complete='18/18',
        missing='0',
        source_archive_sha256=signoff_source_sha,
    )
    _write_operator_signoff_draft(operator_signoff_draft_path)
    _write_operator_signoff_action_plan(operator_signoff_action_plan_path, pending=0)
    _write_operator_signoff(operator_signoff_from_inputs_status_path, status='passed', complete='18/18', missing='0')
    _write_recommendation_matrix(recommendation_matrix_path)
    _write_external_proof_inputs(external_proof_inputs_path)
    _write_external_proof_execution_plan(external_proof_execution_plan_path)
    _write_external_proof_values_template(external_proof_values_template_path)
    _write_external_proof_values_status(external_proof_values_status_path, status='passed', complete='22/22', missing='0')
    _write_frontend_npm_ci_evidence(frontend_npm_ci_path)
    _write_packaging_cleanup_evidence(packaging_cleanup_path)
    hosted_auth_path.write_text('- Status: passed\n- Mode: live-target\n- Target URL: `https://aidm.example.test`\n', encoding='utf-8')
    security_path.write_text('- Status: passed\n- Mode: live-target\n- Target URL: `https://aidm.example.test`\n', encoding='utf-8')
    export_import_path.write_text('- Status: passed\n- Mode: live-target\n- Target URL: `https://aidm.example.test`\n', encoding='utf-8')
    readiness_path.write_text('- Status: passed\n- Target URL: `https://aidm.example.test`\n', encoding='utf-8')
    _write_beta_slo_baseline(slo_path)

    exit_code = main(
        [
            '--rc-evidence',
            str(rc_evidence_path),
            '--issue-evidence-dir',
            str(issue_dir),
            '--rc-issue-closure-evidence',
            str(issue_closure_path),
            '--source-archive',
            str(archive_path),
            '--visual-smoke-dir',
            str(visual_smoke_dir),
            '--visual-smoke-review',
            str(visual_review_path),
            '--github-actions-evidence',
            str(github_actions_path),
            '--hosted-rc-evidence',
            str(hosted_rc_path),
            '--operator-signoff-status',
            str(operator_signoff_path),
            '--operator-signoff-draft',
            str(operator_signoff_draft_path),
            '--operator-signoff-action-plan',
            str(operator_signoff_action_plan_path),
            '--operator-signoff-from-inputs-status',
            str(operator_signoff_from_inputs_status_path),
            '--recommendation-matrix',
            str(recommendation_matrix_path),
            '--external-proof-inputs',
            str(external_proof_inputs_path),
            '--external-proof-execution-plan',
            str(external_proof_execution_plan_path),
            '--external-proof-values-template',
            str(external_proof_values_template_path),
            '--external-proof-values-status',
            str(external_proof_values_status_path),
            '--hosted-cookie-auth-evidence',
            str(hosted_auth_path),
            '--security-forbidden-evidence',
            str(security_path),
            '--export-import-evidence',
            str(export_import_path),
            '--deployment-readiness-evidence',
            str(readiness_path),
            '--beta-slo-baseline',
            str(slo_path),
            '--frontend-npm-ci-evidence',
            str(frontend_npm_ci_path),
            '--packaging-cleanup-evidence',
            str(packaging_cleanup_path),
            '--beta-tester-onboarding',
            'docs/beta_tester_onboarding.md',
            '--output',
            str(output_path),
            '--json-output',
            str(json_output_path),
            '--generated-at',
            '2026-06-19T00:05:00+00:00',
        ]
    )

    assert exit_code == 0
    assert '# Release Evidence Packet' in output_path.read_text(encoding='utf-8')
    payload = json.loads(json_output_path.read_text(encoding='utf-8'))
    assert payload['generated_at'] == '2026-06-19T00:05:00+00:00'
    assert payload['source_archive']['status'] == 'passed'
    assert payload['rc_issue_closure_evidence']['status'] == 'passed'
    assert payload['visual_smoke_review']['status'] == 'passed'
    assert payload['github_actions']['status'] == 'passed'
    assert payload['hosted_rc_evidence']['status'] == 'passed'
    assert payload['hosted_rc_evidence']['manual_evidence'][0]['label'] == 'Hosted database backup/restore proof'
    assert payload['signed_off_worktree']['status'] == 'passed'
    assert payload['operator_signoff']['status'] == 'passed'
    assert payload['operator_signoff']['source_archive_sha256'] == signoff_source_sha
    assert payload['operator_signoff_draft']['status'] == 'present'
    assert payload['operator_signoff_action_plan']['pending_actions'] == '0'
    assert payload['operator_signoff_from_inputs']['status'] == 'passed'
    assert payload['recommendation_matrix']['status'] == 'local-ready-with-external-exceptions'
    assert payload['external_proof_inputs']['status'] == 'action-required'
    assert payload['external_proof_execution_plan']['status'] == 'action-required'
    assert payload['external_proof_execution_plan']['pending_actions'] == '16'
    assert payload['external_proof_values_template']['status'] == 'present'
    assert payload['external_proof_values_status']['status'] == 'passed'
    assert payload['external_proof_values_status']['required_complete'] == '22/22'
    assert payload['hosted_cookie_auth']['mode'] == 'live-target'
    assert payload['security_forbidden']['mode'] == 'live-target'
    assert payload['export_import']['mode'] == 'live-target'
    assert payload['beta_slo_baseline']['status'] == 'passed'
    assert payload['beta_slo_baseline']['dm_response_sample_count'] == 3
    assert payload['beta_slo_baseline']['provider_turn_count'] == 3
    assert payload['beta_slo_baseline']['invite_more_testers'] == 'yes'
    assert payload['beta_slo_baseline']['commit_matches_rc'] is True
    assert payload['beta_slo_baseline']['freshness'] == 'current'
    assert payload['frontend_npm_ci']['status'] == 'passed'
    assert payload['packaging_cleanup']['status'] == 'passed'
    assert payload['beta_tester_onboarding']['status'] == 'passed'
    assert len(payload['hosted_signoff_checklist']) == 11
    assert any(item['evidence'] == 'Source archive attached to RC issue or release' for item in payload['hosted_signoff_checklist'])
    forbidden_item = next(item for item in payload['hosted_signoff_checklist'] if item['evidence'] == 'Hosted non-admin forbidden-response proof')
    assert '--account-token <non-admin-account-token>' in forbidden_item['command']
    assert '--campaign-id <campaign-id>' in forbidden_item['command']
    assert '--session-id <session-id>' in forbidden_item['command']
    export_item = next(item for item in payload['hosted_signoff_checklist'] if item['evidence'] == 'Hosted export/import smoke')
    assert '--player-id <player-id>' in export_item['command']


def test_build_packet_marks_isolated_slo_baseline_local_only(tmp_path):
    rc_evidence_path = tmp_path / 'rc-evidence.json'
    issue_dir = tmp_path / 'issue-evidence'
    archive_path = tmp_path / 'aidm-source-clean.tar.gz'
    visual_smoke_dir = tmp_path / 'visual-smoke-run'
    visual_review_path = tmp_path / 'visual-smoke-review.md'
    github_actions_path = tmp_path / 'github-actions-evidence.md'
    hosted_rc_path = tmp_path / 'hosted-rc-evidence.md'
    operator_signoff_path = tmp_path / 'operator-signoff-status.md'
    hosted_auth_path = tmp_path / 'hosted-cookie-auth-evidence.md'
    security_path = tmp_path / 'security-forbidden-evidence.md'
    export_import_path = tmp_path / 'export-import-evidence.md'
    readiness_path = tmp_path / 'deployment-readiness-evidence.md'
    slo_path = tmp_path / 'beta-slo-baseline.md'
    frontend_npm_ci_path = tmp_path / 'frontend-npm-ci-evidence.md'
    packaging_cleanup_path = tmp_path / 'packaging-cleanup-evidence.md'
    release_artifact_consistency_path = tmp_path / 'release-artifact-consistency.md'

    rc_evidence_path.write_text(
        json.dumps(
            {
                'status': 'passed',
                'commit': 'abc1234',
                'commands': [],
                'git_worktree': {'state': 'clean', 'dirty': False},
            }
        ),
        encoding='utf-8',
    )
    issue_dir.mkdir()
    for spec in ISSUE_SPECS:
        (issue_dir / f'issue-{spec.issue_number:02d}-{spec.slug}.md').write_text(
            f'# {spec.title}\n\n- Issue: #{spec.issue_number}\n- Remaining exceptions: None.\n',
            encoding='utf-8',
        )
    _write_tar(archive_path, ['AIDM-main/README.md'])
    _write_visual_smoke(visual_smoke_dir)
    _write_visual_review(visual_review_path, visual_smoke_dir)
    _write_github_actions(github_actions_path)
    _write_hosted_rc(hosted_rc_path)
    _write_operator_signoff(operator_signoff_path, status='passed', complete='18/18', missing='0')
    hosted_auth_path.write_text('- Status: passed\n- Mode: live-target\n- Target URL: `https://aidm.example.test`\n', encoding='utf-8')
    security_path.write_text('- Status: passed\n- Mode: live-target\n- Target URL: `https://aidm.example.test`\n', encoding='utf-8')
    export_import_path.write_text('- Status: passed\n- Mode: live-target\n- Target URL: `https://aidm.example.test`\n', encoding='utf-8')
    readiness_path.write_text('- Status: passed\n- Target URL: `https://aidm.example.test`\n', encoding='utf-8')
    slo_path.write_text('- Target URL: isolated local runtime\n', encoding='utf-8')
    _write_frontend_npm_ci_evidence(frontend_npm_ci_path)
    _write_packaging_cleanup_evidence(packaging_cleanup_path)
    _write_release_artifact_consistency(release_artifact_consistency_path)

    packet = build_packet(
        generated_at='2026-06-19T00:05:00+00:00',
        rc_evidence_path=rc_evidence_path,
        issue_evidence_dir=issue_dir,
        source_archive_path=archive_path,
        visual_smoke_dir=visual_smoke_dir,
        visual_smoke_review_path=visual_review_path,
        github_actions_evidence_path=github_actions_path,
        hosted_rc_evidence_path=hosted_rc_path,
        operator_signoff_status_path=operator_signoff_path,
        hosted_cookie_auth_evidence_path=hosted_auth_path,
        security_forbidden_evidence_path=security_path,
        export_import_evidence_path=export_import_path,
        deployment_readiness_evidence_path=readiness_path,
        beta_slo_baseline_path=slo_path,
        frontend_npm_ci_evidence_path=frontend_npm_ci_path,
        packaging_cleanup_evidence_path=packaging_cleanup_path,
        release_artifact_consistency_path=release_artifact_consistency_path,
    )

    assert packet['beta_slo_baseline']['status'] == 'local-only'
    assert packet['overall_status'] == 'local-ready-with-external-exceptions'


def test_build_packet_overall_status_depends_on_external_evidence_statuses(tmp_path):
    rc_evidence_path = tmp_path / 'rc-evidence.json'
    issue_dir = tmp_path / 'issue-evidence'
    archive_path = tmp_path / 'aidm-source-clean.tar.gz'
    visual_smoke_dir = tmp_path / 'visual-smoke-run'
    visual_review_path = tmp_path / 'visual-smoke-review.md'
    github_actions_path = tmp_path / 'github-actions-evidence.md'
    hosted_rc_path = tmp_path / 'hosted-rc-evidence.md'
    operator_signoff_path = tmp_path / 'operator-signoff-status.md'
    hosted_auth_path = tmp_path / 'hosted-cookie-auth-evidence.md'
    security_path = tmp_path / 'security-forbidden-evidence.md'
    export_import_path = tmp_path / 'export-import-evidence.md'
    readiness_path = tmp_path / 'deployment-readiness-evidence.md'
    slo_path = tmp_path / 'beta-slo-baseline.md'
    frontend_npm_ci_path = tmp_path / 'frontend-npm-ci-evidence.md'
    packaging_cleanup_path = tmp_path / 'packaging-cleanup-evidence.md'
    release_artifact_consistency_path = tmp_path / 'release-artifact-consistency.md'

    rc_evidence_path.write_text(
        json.dumps(
            {
                'status': 'passed',
                'commit': 'abc1234',
                'commands': [],
                'git_worktree': {'state': 'clean', 'dirty': False},
            }
        ),
        encoding='utf-8',
    )
    _write_issue_evidence_without_exceptions(issue_dir)
    _write_tar(archive_path, ['AIDM-main/README.md'])
    _write_visual_smoke(visual_smoke_dir)
    _write_visual_review(visual_review_path, visual_smoke_dir)
    _write_github_actions_incomplete(github_actions_path)
    _write_hosted_rc(hosted_rc_path, status='planned')
    _write_operator_signoff(operator_signoff_path, status='incomplete', complete='4/18', missing='14')
    hosted_auth_path.write_text('- Status: passed\n- Mode: live-target\n- Target URL: `https://aidm.example.test`\n', encoding='utf-8')
    security_path.write_text('- Status: passed\n- Mode: live-target\n- Target URL: `https://aidm.example.test`\n', encoding='utf-8')
    export_import_path.write_text('- Status: passed\n- Mode: live-target\n- Target URL: `https://aidm.example.test`\n', encoding='utf-8')
    readiness_path.write_text('- Status: passed\n- Target URL: `https://aidm.example.test`\n', encoding='utf-8')
    _write_beta_slo_baseline(slo_path)
    _write_frontend_npm_ci_evidence(frontend_npm_ci_path)
    _write_packaging_cleanup_evidence(packaging_cleanup_path)
    _write_release_artifact_consistency(release_artifact_consistency_path)

    def build() -> dict:
        return build_packet(
            generated_at='2026-06-19T00:05:00+00:00',
            rc_evidence_path=rc_evidence_path,
            issue_evidence_dir=issue_dir,
            source_archive_path=archive_path,
            visual_smoke_dir=visual_smoke_dir,
            visual_smoke_review_path=visual_review_path,
            github_actions_evidence_path=github_actions_path,
            hosted_rc_evidence_path=hosted_rc_path,
            operator_signoff_status_path=operator_signoff_path,
            hosted_cookie_auth_evidence_path=hosted_auth_path,
            security_forbidden_evidence_path=security_path,
            export_import_evidence_path=export_import_path,
            deployment_readiness_evidence_path=readiness_path,
            beta_slo_baseline_path=slo_path,
            frontend_npm_ci_evidence_path=frontend_npm_ci_path,
            packaging_cleanup_evidence_path=packaging_cleanup_path,
            release_artifact_consistency_path=release_artifact_consistency_path,
        )

    packet = build()
    assert packet['issue_evidence']['external_exceptions'] == []
    assert packet['github_actions']['status'] == 'incomplete'
    assert packet['hosted_rc_evidence']['status'] == 'planned'
    assert packet['operator_signoff']['status'] == 'incomplete'
    assert packet['overall_status'] == 'local-ready-with-external-exceptions'

    _write_github_actions(github_actions_path)
    _write_hosted_rc(hosted_rc_path, status='passed', manual_status='required')
    _write_operator_signoff(operator_signoff_path, status='incomplete', complete='12/18', missing='6')
    manual_missing_packet = build()
    assert manual_missing_packet['github_actions']['status'] == 'passed'
    assert manual_missing_packet['hosted_rc_evidence']['status'] == 'passed'
    assert manual_missing_packet['hosted_rc_evidence']['manual_required_count'] == 4
    assert manual_missing_packet['overall_status'] == 'local-ready-with-external-exceptions'

    _write_hosted_rc(hosted_rc_path, status='passed', manual_status='provided')
    _write_operator_signoff(operator_signoff_path, status='passed', complete='18/18', missing='0')
    ready_packet = build()
    assert ready_packet['hosted_rc_evidence']['manual_required_count'] == 0
    assert ready_packet['hosted_rc_evidence']['manual_provided_count'] == 4
    assert ready_packet['hosted_rc_evidence']['manual_evidence'][0]['evidence'] == 'evidence-link'
    assert ready_packet['overall_status'] == 'ready-for-issue-closure'

    _write_hosted_rc(hosted_rc_path, status='invalid-evidence', manual_status='provided')
    invalid_hosted_packet = build()
    assert invalid_hosted_packet['hosted_rc_evidence']['status'] == 'invalid-evidence'
    assert invalid_hosted_packet['overall_status'] == 'failed'

    _write_hosted_rc(hosted_rc_path, status='passed', manual_status='provided')
    readiness_path.write_text('- Status: failed\n- Target URL: `https://aidm.example.test`\n', encoding='utf-8')
    failed_packet = build()
    assert failed_packet['deployment_readiness']['status'] == 'failed'
    assert failed_packet['overall_status'] == 'failed'
