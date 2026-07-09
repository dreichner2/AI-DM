from __future__ import annotations

import io
import json
import tarfile

from scripts import render_rc_issue_evidence
from scripts.render_rc_issue_evidence import (
    ISSUE_SPECS,
    inspect_github_actions_evidence,
    inspect_source_archive,
    inspect_visual_smoke,
    inspect_visual_smoke_review,
    load_evidence,
    render_all_issue_evidence,
)


def _gate_labels() -> list[str]:
    labels: set[str] = set()
    for spec in ISSUE_SPECS:
        for _, criterion_labels in spec.criteria:
                labels.update(
                    label
                    for label in criterion_labels
                    if label not in {'Source archive clean', 'Beta tester onboarding linked'}
                )
    return sorted(labels)


def _write_tar(path, members: list[str]) -> None:
    with tarfile.open(path, mode='w:gz') as archive:
        for member_name in members:
            data = b'example'
            info = tarfile.TarInfo(member_name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def _write_link_tar(path, member_name: str, linkname: str) -> None:
    with tarfile.open(path, mode='w:gz') as archive:
        info = tarfile.TarInfo(member_name)
        info.type = tarfile.SYMTYPE
        info.linkname = linkname
        archive.addfile(info)


def _write_hosted_rc_evidence(
    path,
    *,
    status: str = 'passed',
    manual_status: str = 'provided',
    worker_model: str = 'single',
) -> None:
    manual_evidence = 'https://github.com/example/AIDM/issues/9#evidence' if manual_status == 'provided' else ''
    path.write_text(
        '\n'.join(
            [
                '# Hosted RC Evidence',
                '',
                f'- Status: {status}',
                '- Automated status: passed',
                '- Target URL: `https://aidm.example.test`',
                '- Workspace ID: `workspace-1`',
                '- Release: RC1',
                '- Environment: staging',
                f'- Socket.IO worker model: {worker_model}',
                '- Socket.IO staging proof: `missing`',
                '- Database: hosted-db',
                '- LLM provider/model: openai/gpt-4.1',
                '- Observability provider: managed',
                '- Alert owner: aidm-operator',
                '- Dry run: False',
                '',
                '## Automated Checks',
                '',
                '| Check | Status | Exit | Evidence | Missing inputs |',
                '| --- | --- | ---: | --- | --- |',
                '| Hosted deployment readiness | passed | 0 | `tmp/release/deployment-readiness-evidence.md` |  |',
                '| Hosted cookie auth smoke | passed | 0 | `tmp/release/hosted-cookie-auth-evidence.md` |  |',
                '| Hosted non-admin forbidden smoke | passed | 0 | `tmp/release/security-forbidden-evidence.md` |  |',
                '| Hosted session export/import smoke | passed | 0 | `tmp/release/export-import-evidence.md` |  |',
                '| Hosted beta SLO baseline | passed | 0 | `tmp/release/beta-slo-baseline.md` |  |',
                '',
                '## Manual Evidence Still Required',
                '',
                '| Evidence | Status | Link/path/details |',
                '| --- | --- | --- |',
                f'| Hosted database backup/restore proof | {manual_status} | {manual_evidence} |',
                f'| Hosted Socket.IO worker process proof | {manual_status} | {manual_evidence} |',
                f'| Source archive attached to RC issue or release | {manual_status} | {manual_evidence} |',
                '',
            ]
        ),
        encoding='utf-8',
    )


def test_load_evidence_parses_markdown_report(tmp_path):
    report_path = tmp_path / 'rc-evidence.md'
    report_path.write_text(
        '\n'.join(
            [
                '# Closed Beta RC Evidence',
                '',
                '- Status: passed',
                '- Started: 2026-06-19T00:00:00+00:00',
                '- Finished: 2026-06-19T00:02:00+00:00',
                '- Commit: abc123',
                '- Worktree: dirty (2 changed/untracked paths; 1 tracked, 1 untracked)',
                '- Repo: `/repo`',
                '- Python: `.venv/bin/python`',
                '- Browser smoke: included',
                '- Dependency audits: included',
                '',
                '| Gate | Status | Exit | Seconds | Command |',
                '| --- | --- | ---: | ---: | --- |',
                '| Backend tests | passed | 0 | 1.23 | `(cd . && pytest)` |',
                '| Later gate | skipped |  |  | `(cd . && later)` |',
                '',
            ]
        ),
        encoding='utf-8',
    )

    payload = load_evidence(report_path)

    assert payload['status'] == 'passed'
    assert payload['commit'] == 'abc123'
    assert payload['worktree'] == 'dirty (2 changed/untracked paths; 1 tracked, 1 untracked)'
    assert payload['include_browser_smoke'] is True
    assert payload['commands'][0]['label'] == 'Backend tests'
    assert payload['commands'][0]['returncode'] == 0
    assert payload['commands'][1]['status'] == 'skipped'
    assert payload['commands'][1]['returncode'] is None


def test_inspect_github_actions_evidence_marks_validation_errors_invalid(tmp_path):
    report_path = tmp_path / 'github-actions-evidence.md'
    report_path.write_text(
        '\n'.join(
            [
                '# GitHub Actions Evidence',
                '',
                '- Status: passed',
                '- AIDM CI run URL: `https://github.com/example/AIDM/actions/runs/111`',
                '- Closed Beta RC run URL: `https://github.com/example/AIDM/actions/runs/222`',
                '- Missing: None.',
                '- Validation errors: Closed Beta RC run URL was not found in latest Closed Beta RC runs for this repository',
                '',
            ]
        ),
        encoding='utf-8',
    )

    evidence = inspect_github_actions_evidence(report_path)

    assert evidence['status'] == 'invalid'
    assert 'Closed Beta RC run URL was not found' in evidence['validation_errors']


def test_inspect_source_archive_reports_clean_and_forbidden_members(tmp_path):
    clean_archive = tmp_path / 'aidm-source-clean.tar.gz'
    dirty_archive = tmp_path / 'aidm-source-dirty.tar.gz'
    corrupt_archive = tmp_path / 'aidm-source-corrupt.tar.gz'
    _write_tar(clean_archive, ['AIDM-main/README.md', 'AIDM-main/docs/release_checklist.md'])
    _write_tar(dirty_archive, ['AIDM-main/README.md', 'AIDM-main/aidm_frontend/node_modules/pkg/index.js'])
    corrupt_archive.write_bytes(b'\x1f\x8b')

    clean_result = inspect_source_archive(clean_archive)
    assert clean_result['status'] == 'passed'
    assert len(clean_result['sha256']) == 64
    assert clean_result['bytes'] > 0
    dirty_result = inspect_source_archive(dirty_archive)
    assert dirty_result['status'] == 'failed'
    assert dirty_result['forbidden'] == ['AIDM-main/aidm_frontend/node_modules/pkg/index.js']
    corrupt_result = inspect_source_archive(corrupt_archive)
    assert corrupt_result['status'] == 'invalid'
    assert corrupt_result['forbidden']


def test_inspect_source_archive_flags_forbidden_link_targets(tmp_path):
    archive_path = tmp_path / 'aidm-source-link.tar.gz'
    _write_link_tar(
        archive_path,
        'AIDM-main/docs/current-database',
        '../tmp/dnd_ai_dm.db',
    )

    result = inspect_source_archive(archive_path)

    assert result['status'] == 'failed'
    assert result['forbidden'] == ['AIDM-main/docs/current-database -> ../tmp/dnd_ai_dm.db']


def test_inspect_source_archive_tracks_large_lfs_members(tmp_path, monkeypatch):
    monkeypatch.setattr(render_rc_issue_evidence, 'LARGE_ARCHIVE_MEMBER_THRESHOLD_BYTES', 1)
    archive_path = tmp_path / 'aidm-source-large-lfs.tar.gz'
    _write_tar(archive_path, ['AIDM-main/aidm_frontend/public/music/theme.mp3'])

    result = inspect_source_archive(archive_path)

    assert result['status'] == 'passed'
    assert result['large_member_count'] == 1
    assert result['large_untracked'] == []
    assert result['large_members'][0]['lfs_tracked'] is True


def test_inspect_source_archive_fails_large_non_lfs_members(tmp_path, monkeypatch):
    monkeypatch.setattr(render_rc_issue_evidence, 'LARGE_ARCHIVE_MEMBER_THRESHOLD_BYTES', 1)
    archive_path = tmp_path / 'aidm-source-large-untracked.tar.gz'
    _write_tar(archive_path, ['AIDM-main/docs/large-reference.txt'])

    result = inspect_source_archive(archive_path)

    assert result['status'] == 'failed'
    assert result['large_untracked'] == ['AIDM-main/docs/large-reference.txt']
    assert result['large_members'][0]['lfs_tracked'] is False


def test_inspect_visual_smoke_reports_expected_screenshots(tmp_path):
    smoke_dir = tmp_path / 'visual-smoke-run'
    smoke_dir.mkdir()
    for name in ('desktop-shell.png', 'mobile-full.png', 'short-height-composer.png'):
        (smoke_dir / name).write_bytes(b'png')

    result = inspect_visual_smoke(smoke_dir)

    assert result['status'] == 'passed'
    assert result['screenshots'] == ['desktop-shell.png', 'mobile-full.png', 'short-height-composer.png']
    assert result['missing'] == []


def test_inspect_visual_smoke_review_parses_report(tmp_path):
    report_path = tmp_path / 'visual-smoke-review.md'
    report_path.write_text(
        '\n'.join(
            [
                '# Visual Smoke Review Evidence',
                '',
                '- Status: passed',
                '- Artifact dir: `tmp/verification_artifacts/visual-smoke/run`',
                '- Screenshots: 3/3',
                '- Failures: None.',
                '',
            ]
        ),
        encoding='utf-8',
    )

    result = inspect_visual_smoke_review(report_path)

    assert result['status'] == 'passed'
    assert result['screenshots'] == '3/3'
    assert result['failures'] == 'None.'


def test_inspect_github_actions_evidence_parses_report(tmp_path):
    report_path = tmp_path / 'github-actions-evidence.md'
    report_path.write_text(
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

    result = inspect_github_actions_evidence(report_path)

    assert result['status'] == 'passed'
    assert result['aidm_ci_run_url'].endswith('/111')
    assert result['closed_beta_rc_run_url'].endswith('/222')
    assert result['worktree'] == 'clean'


def test_inspect_github_actions_evidence_carries_json_sidecar_details(tmp_path):
    report_path = tmp_path / 'github-actions-evidence.md'
    report_path.write_text(
        '\n'.join(
            [
                '# GitHub Actions Evidence',
                '',
                '- Status: incomplete',
                '- AIDM CI run URL: `https://github.com/example/AIDM/actions/runs/111`',
                '- Closed Beta RC run URL: `missing`',
                '- Missing: Closed Beta RC run URL',
                '',
            ]
        ),
        encoding='utf-8',
    )
    report_path.with_suffix('.json').write_text(
        json.dumps(
            {
                'status': 'incomplete',
                'aidm_ci_run_url': 'https://github.com/example/AIDM/actions/runs/111',
                'closed_beta_rc_run_url': '',
                'missing': ['Closed Beta RC run URL'],
                'missing_details': {
                    'Closed Beta RC run URL': 'No recent Closed Beta RC runs were returned by gh run list.'
                },
                'next_actions': ['Run the manual Closed Beta RC workflow for commit abc123.'],
                'closed_beta_rc_artifact': {
                    'status': 'passed',
                    'content_status': 'passed',
                    'expected_name': 'closed-beta-rc-evidence',
                    'name': 'closed-beta-rc-evidence',
                    'url': 'https://github.com/example/AIDM/actions/runs/222/artifacts/333',
                },
                'closed_beta_rc_artifact_status': 'passed',
                'closed_beta_rc_artifact_content_status': 'passed',
                'worktree': {
                    'state': 'dirty',
                    'dirty': True,
                    'summary': 'dirty (2 changed/untracked paths)',
                    'changed_paths': 2,
                },
                'validation_errors': [],
            }
        ),
        encoding='utf-8',
    )

    result = inspect_github_actions_evidence(report_path)

    assert result['status'] == 'incomplete'
    assert result['missing'] == 'Closed Beta RC run URL'
    assert result['missing_details'] == {
        'Closed Beta RC run URL': 'No recent Closed Beta RC runs were returned by gh run list.'
    }
    assert result['next_actions'] == ['Run the manual Closed Beta RC workflow for commit abc123.']
    assert result['closed_beta_rc_artifact_status'] == 'passed'
    assert result['closed_beta_rc_artifact_content_status'] == 'passed'
    assert result['closed_beta_rc_artifact_name'] == 'closed-beta-rc-evidence'
    assert result['closed_beta_rc_artifact_url'].endswith('/artifacts/333')
    assert result['worktree'] == 'dirty (2 changed/untracked paths)'
    assert result['git_worktree']['state'] == 'dirty'


def test_render_issue_evidence_writes_issue_ready_markdown(tmp_path):
    evidence_path = tmp_path / 'rc-evidence.json'
    archive_path = tmp_path / 'aidm-source-clean.tar.gz'
    visual_smoke_dir = tmp_path / 'visual-smoke-run'
    visual_smoke_review_path = tmp_path / 'visual-smoke-review.md'
    github_actions_evidence_path = tmp_path / 'github-actions-evidence.md'
    hosted_rc_path = tmp_path / 'hosted-rc-evidence.md'
    security_report_path = tmp_path / 'security-forbidden-evidence.md'
    export_import_report_path = tmp_path / 'export-import-evidence.md'
    output_dir = tmp_path / 'issue-evidence'
    _write_tar(archive_path, ['AIDM-main/README.md'])
    _write_hosted_rc_evidence(hosted_rc_path)
    visual_smoke_dir.mkdir()
    for name in ('desktop-shell.png', 'mobile-full.png', 'short-height-composer.png'):
        (visual_smoke_dir / name).write_bytes(b'png')
    visual_smoke_review_path.write_text(
        '\n'.join(
            [
                '# Visual Smoke Review Evidence',
                '',
                '- Status: passed',
                f'- Artifact dir: `{visual_smoke_dir}`',
                '- Screenshots: 3/3',
                '- Failures: None.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    github_actions_evidence_path.write_text(
        '\n'.join(
            [
                '# GitHub Actions Evidence',
                '',
                '- Status: passed',
                '- AIDM CI run URL: `https://github.com/example/AIDM/actions/runs/111`',
                '- Closed Beta RC run URL: `https://github.com/example/AIDM/actions/runs/222`',
                '- Missing: None.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    security_report_path.write_text(
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
    export_import_report_path.write_text(
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
    evidence_path.write_text(
        json.dumps(
            {
                'status': 'passed',
                'started_at': '2026-06-19T00:00:00+00:00',
                'finished_at': '2026-06-19T00:02:00+00:00',
                'commit': 'abc123',
                'worktree': 'dirty (3 changed/untracked paths; 2 tracked, 1 untracked)',
                'repo_root': '/repo',
                'python': '.venv/bin/python',
                'commands': [
                    {
                        'label': label,
                        'status': 'passed',
                        'returncode': 0,
                        'duration_seconds': 1.0,
                        'command': f'run {label}',
                    }
                    for label in _gate_labels()
                ],
            },
            indent=2,
        ),
        encoding='utf-8',
    )

    written = render_all_issue_evidence(
        evidence_report_path=evidence_path,
        output_dir=output_dir,
        source_archive_path=archive_path,
        visual_smoke_dir=visual_smoke_dir,
        visual_smoke_review_path=visual_smoke_review_path,
        github_actions_evidence_path=github_actions_evidence_path,
        hosted_rc_evidence_path=hosted_rc_path,
        security_forbidden_evidence_path=security_report_path,
        export_import_evidence_path=export_import_report_path,
    )

    assert len(written) == 7
    preflight = (output_dir / 'issue-03-preflight.md').read_text(encoding='utf-8')
    assert '# Closed Beta RC1: Preflight gates' in preflight
    assert '- Result: passed' in preflight
    assert '- Worktree: dirty (3 changed/untracked paths; 2 tracked, 1 untracked)' in preflight
    assert 'sha256:' in preflight
    assert '- GitHub Actions evidence:' in preflight
    assert 'Attach GitHub Actions `AIDM CI` and `Closed Beta RC` run URLs before closing.' not in preflight
    assert 'Attach live hosted/staging `/api/health` evidence when this issue is used for hosted RC sign-off.' not in preflight
    assert '- Remaining exceptions: None.' in preflight
    assert '| Backend tests pass | Backend tests | passed |' in preflight

    frontend = (output_dir / 'issue-04-frontend.md').read_text(encoding='utf-8')
    assert '- Visual smoke artifacts:' in frontend
    assert '- Visual smoke review:' in frontend
    assert '(passed; screenshots: 3/3; failures: None.)' in frontend
    assert 'desktop-shell.png, mobile-full.png, short-height-composer.png' in frontend

    security = (output_dir / 'issue-05-security.md').read_text(encoding='utf-8')
    assert '- Security forbidden evidence:' in security
    assert '(passed; mode: isolated)' in security

    data_integrity = (output_dir / 'issue-06-data-integrity.md').read_text(encoding='utf-8')
    assert '- Export/import evidence:' in data_integrity
    assert '(passed; mode: isolated)' in data_integrity
    assert '| Session export/import smoke passes | Session export/import smoke | passed |' in data_integrity

    packaging = (output_dir / 'issue-09-packaging.md').read_text(encoding='utf-8')
    assert '- Source archive:' in packaging
    assert '- Beta tester onboarding:' in packaging
    assert '| Source archive exists and excludes generated/runtime artifacts | Source archive clean | passed |' in packaging
    assert '| Beta tester onboarding guide exists and is linked | Beta tester onboarding linked | passed |' in packaging


def test_render_issue_evidence_clears_hosted_exceptions_when_hosted_rc_proof_is_complete(tmp_path):
    evidence_path = tmp_path / 'rc-evidence.json'
    archive_path = tmp_path / 'aidm-source-clean.tar.gz'
    hosted_rc_path = tmp_path / 'hosted-rc-evidence.md'
    github_actions_path = tmp_path / 'github-actions-evidence.md'
    output_dir = tmp_path / 'issue-evidence'

    _write_tar(archive_path, ['AIDM-main/README.md'])
    _write_hosted_rc_evidence(hosted_rc_path)
    github_actions_path.write_text(
        '\n'.join(
            [
                '# GitHub Actions Evidence',
                '',
                '- Status: passed',
                '- AIDM CI run URL: `https://github.com/example/AIDM/actions/runs/111`',
                '- Closed Beta RC run URL: `https://github.com/example/AIDM/actions/runs/222`',
                '- Missing: None.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    evidence_path.write_text(
        json.dumps(
            {
                'status': 'passed',
                'started_at': '2026-06-19T00:00:00+00:00',
                'finished_at': '2026-06-19T00:02:00+00:00',
                'commit': 'abc123',
                'worktree': 'clean',
                'repo_root': '/repo',
                'python': '.venv/bin/python',
                'commands': [
                    {
                        'label': label,
                        'status': 'passed',
                        'returncode': 0,
                        'duration_seconds': 1.0,
                        'command': f'run {label}',
                    }
                    for label in _gate_labels()
                ],
            },
            indent=2,
        ),
        encoding='utf-8',
    )

    render_all_issue_evidence(
        evidence_report_path=evidence_path,
        output_dir=output_dir,
        source_archive_path=archive_path,
        github_actions_evidence_path=github_actions_path,
        hosted_rc_evidence_path=hosted_rc_path,
    )

    for issue_number, slug in (
        (3, 'preflight'),
        (5, 'security'),
        (6, 'data-integrity'),
        (7, 'runtime-quality'),
        (8, 'observability'),
        (9, 'packaging'),
    ):
        rendered = (output_dir / f'issue-{issue_number:02d}-{slug}.md').read_text(encoding='utf-8')
        assert '- Hosted RC evidence:' in rendered
        assert '- Remaining exceptions: None.' in rendered
        assert '- Decision: Local RC evidence satisfies this gate.' in rendered


def test_render_issue_evidence_does_not_clear_exceptions_from_invalid_hosted_rc_proof(tmp_path):
    evidence_path = tmp_path / 'rc-evidence.json'
    archive_path = tmp_path / 'aidm-source-clean.tar.gz'
    hosted_rc_path = tmp_path / 'hosted-rc-evidence.md'
    github_actions_path = tmp_path / 'github-actions-evidence.md'
    output_dir = tmp_path / 'issue-evidence'

    _write_tar(archive_path, ['AIDM-main/README.md'])
    _write_hosted_rc_evidence(hosted_rc_path, status='invalid-evidence')
    github_actions_path.write_text(
        '\n'.join(
            [
                '# GitHub Actions Evidence',
                '',
                '- Status: passed',
                '- AIDM CI run URL: `https://github.com/example/AIDM/actions/runs/111`',
                '- Closed Beta RC run URL: `https://github.com/example/AIDM/actions/runs/222`',
                '- Missing: None.',
                '',
            ]
        ),
        encoding='utf-8',
    )
    evidence_path.write_text(
        json.dumps(
            {
                'status': 'passed',
                'started_at': '2026-06-19T00:00:00+00:00',
                'finished_at': '2026-06-19T00:02:00+00:00',
                'commit': 'abc123',
                'worktree': 'clean',
                'repo_root': '/repo',
                'python': '.venv/bin/python',
                'commands': [
                    {
                        'label': label,
                        'status': 'passed',
                        'returncode': 0,
                        'duration_seconds': 1.0,
                        'command': f'run {label}',
                    }
                    for label in _gate_labels()
                ],
            },
            indent=2,
        ),
        encoding='utf-8',
    )

    render_all_issue_evidence(
        evidence_report_path=evidence_path,
        output_dir=output_dir,
        source_archive_path=archive_path,
        github_actions_evidence_path=github_actions_path,
        hosted_rc_evidence_path=hosted_rc_path,
    )

    preflight = (output_dir / 'issue-03-preflight.md').read_text(encoding='utf-8')
    assert '- Hosted RC evidence:' in preflight
    assert '(invalid-evidence;' in preflight
    assert 'Attach live hosted/staging `/api/health` evidence' in preflight
    assert '- Remaining exceptions: None.' not in preflight


def test_multi_worker_staging_proof_cannot_clear_rc1_single_worker_policy_exception():
    spec = next(candidate for candidate in ISSUE_SPECS if candidate.issue_number == 7)

    for worker_model in ('sticky', 'message_queue'):
        remaining = list(spec.external_exceptions)
        render_rc_issue_evidence._apply_hosted_rc_exception_evidence(
            spec,
            remaining,
            {
                'status': 'passed',
                'socketio_worker_model': worker_model,
                'socketio_staging_proof': 'https://evidence.example.test/socketio',
                'manual_evidence': {
                    'Hosted Socket.IO worker process proof': {
                        'status': 'provided',
                        'evidence': 'https://evidence.example.test/workers',
                    }
                },
            },
        )

        assert render_rc_issue_evidence.HOSTED_SINGLE_WORKER_POLICY_EXCEPTION in remaining
