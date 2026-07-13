from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

from scripts import render_github_actions_evidence
from scripts.render_github_actions_evidence import build_evidence, main, render_markdown


def test_build_evidence_uses_github_environment_for_closed_beta_url():
    evidence = build_evidence(
        ci_run_url='https://github.com/example/AIDM/actions/runs/111',
        env={
            'GITHUB_SERVER_URL': 'https://github.com',
            'GITHUB_REPOSITORY': 'example/AIDM',
            'GITHUB_RUN_ID': '222',
            'GITHUB_SHA': 'abcdef1234567890',
        },
        generated_at='2026-06-19T00:05:00+00:00',
    )

    assert evidence['status'] == 'passed'
    assert evidence['commit'] == 'abcdef123456'
    assert evidence['closed_beta_rc_run_url'] == 'https://github.com/example/AIDM/actions/runs/222'
    assert evidence['missing'] == []


def test_render_markdown_marks_missing_urls_incomplete():
    evidence = build_evidence(
        env={},
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
    )
    markdown = render_markdown(evidence)

    assert evidence['status'] == 'incomplete'
    assert '- Status: incomplete' in markdown
    assert '- AIDM CI run URL: `missing`' in markdown
    assert '- Closed Beta RC run URL: `missing`' in markdown
    assert '- Auto gh discovery: False' in markdown
    assert '- Validation errors: None.' in markdown


def test_build_evidence_rejects_invalid_run_url_shape():
    evidence = build_evidence(
        ci_run_url='https://github.com/example/AIDM/actions/runs/111',
        closed_beta_rc_run_url='https://github.com/example/AIDM/pulls/222',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
    )
    markdown = render_markdown(evidence)

    assert evidence['status'] == 'invalid'
    assert 'Closed Beta RC run URL must look like' in evidence['validation_errors'][0]
    assert '- Validation errors: Closed Beta RC run URL must look like' in markdown


def test_build_evidence_rejects_wrong_repository_run_url():
    evidence = build_evidence(
        ci_run_url='https://github.com/other/AIDM/actions/runs/111',
        closed_beta_rc_run_url='https://github.com/example/AIDM/actions/runs/222',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
    )

    assert evidence['status'] == 'invalid'
    assert evidence['validation_errors'] == ['AIDM CI run URL repository other/AIDM does not match example/AIDM']


def test_build_evidence_infers_repository_from_git_remote(monkeypatch):
    def fake_run(args, **kwargs):
        if tuple(args) == ('git', 'remote', 'get-url', 'origin'):
            return SimpleNamespace(returncode=0, stdout='git@github.com:example/AIDM.git\n')
        return SimpleNamespace(returncode=1, stdout='', stderr='unexpected call')

    monkeypatch.setattr(render_github_actions_evidence.subprocess, 'run', fake_run)

    evidence = build_evidence(
        ci_run_url='https://github.com/other/AIDM/actions/runs/111',
        closed_beta_rc_run_url='https://github.com/example/AIDM/actions/runs/222',
        commit='abc123',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
    )

    assert evidence['repository'] == 'example/AIDM'
    assert evidence['status'] == 'invalid'
    assert evidence['validation_errors'] == ['AIDM CI run URL repository other/AIDM does not match example/AIDM']


def test_build_evidence_auto_discovers_missing_urls_with_gh(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        workflow = args[args.index('--workflow') + 1]
        if workflow == 'AIDM CI':
            return SimpleNamespace(returncode=0, stdout='https://github.com/example/AIDM/actions/runs/111\n')
        if workflow == 'Closed Beta RC':
            return SimpleNamespace(returncode=0, stdout='https://github.com/example/AIDM/actions/runs/222\n')
        return SimpleNamespace(returncode=1, stdout='')

    monkeypatch.setattr(render_github_actions_evidence.subprocess, 'run', fake_run)

    evidence = build_evidence(
        auto_gh=True,
        gh_executable='gh-test',
        commit='abcdef1234567890',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
    )

    assert evidence['status'] == 'passed'
    assert evidence['auto_gh'] is True
    assert evidence['aidm_ci_run_url'].endswith('/111')
    assert evidence['closed_beta_rc_run_url'].endswith('/222')
    assert [call[0] for call in calls] == ['gh-test', 'gh-test']
    assert all('--commit' in call and 'abcdef1234567890' in call for call in calls)


def test_build_evidence_includes_read_only_workflow_details(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        if args[1:3] == ('workflow', 'list'):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {'name': 'AIDM CI', 'state': 'active', 'path': '.github/workflows/ci.yml'},
                        {'name': 'Closed Beta RC', 'state': 'active', 'path': '.github/workflows/closed-beta-rc.yml'},
                    ]
                ),
            )
        if args[1:3] == ('run', 'list'):
            workflow = args[args.index('--workflow') + 1]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            'databaseId': 123,
                            'displayTitle': workflow,
                            'status': 'completed',
                            'conclusion': 'success',
                            'event': 'workflow_dispatch',
                            'headSha': 'abc123',
                            'updatedAt': '2026-06-19T00:00:00Z',
                            'url': f'https://github.com/example/AIDM/actions/runs/{123 if workflow == "AIDM CI" else 456}',
                        }
                    ]
                ),
            )
        return SimpleNamespace(returncode=1, stderr='unexpected call', stdout='')

    monkeypatch.setattr(render_github_actions_evidence.subprocess, 'run', fake_run)

    evidence = build_evidence(
        include_gh_details=True,
        gh_executable='gh-test',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
    )
    markdown = render_markdown(evidence)

    assert evidence['include_gh_details'] is True
    assert evidence['workflow_details']['AIDM CI']['state'] == 'active'
    assert evidence['latest_runs']['Closed Beta RC'][0]['url'].endswith('/456')
    assert evidence['gh_errors'] == []
    assert '| AIDM CI | True | active | .github/workflows/ci.yml |' in markdown
    assert '| Closed Beta RC | completed | success | abc123 | 2026-06-19T00:00:00Z |' in markdown
    assert [call[:3] for call in calls] == [
        ('gh-test', 'workflow', 'list'),
        ('gh-test', 'run', 'list'),
        ('gh-test', 'run', 'list'),
    ]


def test_build_evidence_records_closed_beta_artifact_details(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        if args[1:3] == ('workflow', 'list'):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {'name': 'AIDM CI', 'state': 'active', 'path': '.github/workflows/ci.yml'},
                        {'name': 'Closed Beta RC', 'state': 'active', 'path': '.github/workflows/closed-beta-rc.yml'},
                    ]
                ),
            )
        if args[1:3] == ('run', 'list'):
            workflow = args[args.index('--workflow') + 1]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            'databaseId': 111 if workflow == 'AIDM CI' else 222,
                            'displayTitle': workflow,
                            'status': 'completed',
                            'conclusion': 'success',
                            'event': 'workflow_dispatch',
                            'headSha': 'abc123',
                            'updatedAt': '2026-06-19T00:00:00Z',
                            'url': f'https://github.com/example/AIDM/actions/runs/{111 if workflow == "AIDM CI" else 222}',
                        }
                    ]
                ),
            )
        if args[1:3] == ('run', 'view'):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        'artifacts': [
                            {
                                'name': 'closed-beta-rc-evidence',
                                'url': 'https://github.com/example/AIDM/actions/runs/222/artifacts/333',
                                'sizeInBytes': 12345,
                                'expired': False,
                            }
                        ]
                    }
                ),
            )
        return SimpleNamespace(returncode=1, stderr='unexpected call', stdout='')

    monkeypatch.setattr(render_github_actions_evidence.subprocess, 'run', fake_run)

    evidence = build_evidence(
        ci_run_url='https://github.com/example/AIDM/actions/runs/111',
        closed_beta_rc_run_url='https://github.com/example/AIDM/actions/runs/222',
        include_gh_details=True,
        gh_executable='gh-test',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
    )
    markdown = render_markdown(evidence)

    assert evidence['status'] == 'passed'
    assert evidence['closed_beta_rc_artifact_status'] == 'passed'
    assert evidence['closed_beta_rc_artifact_content_status'] == 'not-checked'
    assert evidence['closed_beta_rc_artifact']['name'] == 'closed-beta-rc-evidence'
    assert evidence['closed_beta_rc_artifact']['url'].endswith('/artifacts/333')
    assert any('--verify-closed-beta-rc-artifact-contents' in action for action in evidence['next_actions'])
    assert '- Closed Beta RC artifact status: passed' in markdown
    assert '- Closed Beta RC artifact content status: not-checked' in markdown
    assert '| Expected name | closed-beta-rc-evidence |' in markdown
    assert ('gh-test', 'run', 'view', '222', '--json', 'artifacts') in calls


def test_build_evidence_falls_back_to_actions_artifacts_api(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        if args[1:3] == ('workflow', 'list'):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {'name': 'AIDM CI', 'state': 'active', 'path': '.github/workflows/ci.yml'},
                        {'name': 'Closed Beta RC', 'state': 'active', 'path': '.github/workflows/closed-beta-rc.yml'},
                    ]
                ),
            )
        if args[1:3] == ('run', 'list'):
            workflow = args[args.index('--workflow') + 1]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            'databaseId': 111 if workflow == 'AIDM CI' else 222,
                            'displayTitle': workflow,
                            'status': 'completed',
                            'conclusion': 'success',
                            'event': 'workflow_dispatch',
                            'headSha': 'abc123',
                            'updatedAt': '2026-06-19T00:00:00Z',
                            'url': f'https://github.com/example/AIDM/actions/runs/{111 if workflow == "AIDM CI" else 222}',
                        }
                    ]
                ),
            )
        if args[1:3] == ('run', 'view'):
            return SimpleNamespace(
                returncode=1,
                stderr='Unknown JSON field: "artifacts"',
                stdout='',
            )
        if args[1] == 'api':
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        'artifacts': [
                            {
                                'name': 'closed-beta-rc-evidence',
                                'archive_download_url': 'https://api.github.com/repos/example/AIDM/actions/artifacts/333/zip',
                                'size_in_bytes': 12345,
                                'expired': False,
                            }
                        ]
                    }
                ),
            )
        return SimpleNamespace(returncode=1, stderr='unexpected call', stdout='')

    monkeypatch.setattr(render_github_actions_evidence.subprocess, 'run', fake_run)

    evidence = build_evidence(
        ci_run_url='https://github.com/example/AIDM/actions/runs/111',
        closed_beta_rc_run_url='https://github.com/example/AIDM/actions/runs/222',
        include_gh_details=True,
        gh_executable='gh-test',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
    )

    assert evidence['status'] == 'passed'
    assert evidence['closed_beta_rc_artifact_status'] == 'passed'
    assert evidence['closed_beta_rc_artifact']['name'] == 'closed-beta-rc-evidence'
    assert evidence['closed_beta_rc_artifact']['size_in_bytes'] == 12345
    assert ('gh-test', 'api', 'repos/example/AIDM/actions/runs/222/artifacts', '--paginate') in calls


def test_build_evidence_verifies_closed_beta_artifact_contents(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        if args[1:3] == ('workflow', 'list'):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {'name': 'AIDM CI', 'state': 'active', 'path': '.github/workflows/ci.yml'},
                        {'name': 'Closed Beta RC', 'state': 'active', 'path': '.github/workflows/closed-beta-rc.yml'},
                    ]
                ),
            )
        if args[1:3] == ('run', 'list'):
            workflow = args[args.index('--workflow') + 1]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            'databaseId': 111 if workflow == 'AIDM CI' else 222,
                            'displayTitle': workflow,
                            'status': 'completed',
                            'conclusion': 'success',
                            'event': 'workflow_dispatch',
                            'headSha': 'abc123',
                            'updatedAt': '2026-06-19T00:00:00Z',
                            'url': f'https://github.com/example/AIDM/actions/runs/{111 if workflow == "AIDM CI" else 222}',
                        }
                    ]
                ),
            )
        if args[1:3] == ('run', 'view'):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        'artifacts': [
                            {
                                'name': 'closed-beta-rc-evidence',
                                'url': 'https://github.com/example/AIDM/actions/runs/222/artifacts/333',
                                'sizeInBytes': 12345,
                                'expired': False,
                            }
                        ]
                    }
                ),
            )
        if args[1:3] == ('run', 'download'):
            output_dir = pathlib.Path(args[args.index('--dir') + 1])
            for relative_path in (
                'tmp/release/rc-evidence.md',
                'tmp/release/hosted-cookie-auth-evidence.md',
                'tmp/release/security-forbidden-evidence.md',
                'tmp/release/export-import-evidence.md',
                'tmp/release/visual-smoke-review.md',
                'tmp/release/visual-smoke-review.json',
                'tmp/release/github-actions-rc-run-plan.md',
                'tmp/release/github-actions-rc-run-plan.json',
                'tmp/release/github-actions-evidence.md',
                'tmp/release/github-actions-evidence.json',
                'tmp/release/packaging-cleanup-evidence.md',
                'tmp/release/packaging-cleanup-evidence.json',
                'tmp/release/release-evidence-packet.md',
                'tmp/release/release-evidence-packet.json',
                'tmp/release/issue-evidence/issue-03-preflight.md',
                'tmp/release/aidm-source-test.tar.gz',
                'tmp/release/aidm-source-test.tar.gz.sha256',
                'tmp/verification_artifacts/visual-smoke/2026-06-19T00-00-00Z/desktop-shell.png',
                'tmp/verification_artifacts/visual-smoke/2026-06-19T00-00-00Z/laptop-shell.png',
                'tmp/verification_artifacts/visual-smoke/2026-06-19T00-00-00Z/mobile-full.png',
                'tmp/verification_artifacts/visual-smoke/2026-06-19T00-00-00Z/mobile-narrow.png',
                'tmp/verification_artifacts/visual-smoke/2026-06-19T00-00-00Z/tablet-landscape.png',
                'tmp/verification_artifacts/visual-smoke/2026-06-19T00-00-00Z/tablet-portrait.png',
            ):
                path = output_dir / relative_path.removeprefix('tmp/')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('ok', encoding='utf-8')
            return SimpleNamespace(returncode=0, stdout='')
        return SimpleNamespace(returncode=1, stderr='unexpected call', stdout='')

    monkeypatch.setattr(render_github_actions_evidence.subprocess, 'run', fake_run)

    evidence = build_evidence(
        ci_run_url='https://github.com/example/AIDM/actions/runs/111',
        closed_beta_rc_run_url='https://github.com/example/AIDM/actions/runs/222',
        include_gh_details=True,
        verify_closed_beta_rc_artifact_contents=True,
        gh_executable='gh-test',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
    )
    markdown = render_markdown(evidence)

    assert evidence['status'] == 'passed'
    assert evidence['closed_beta_rc_artifact_status'] == 'passed'
    assert evidence['closed_beta_rc_artifact_content_status'] == 'passed'
    assert evidence['closed_beta_rc_artifact']['content_missing_globs'] == []
    assert 'tmp/release/aidm-source-test.tar.gz' in evidence['closed_beta_rc_artifact']['content_matched_paths']
    assert (
        'tmp/verification_artifacts/visual-smoke/2026-06-19T00-00-00Z/mobile-full.png'
        in evidence['closed_beta_rc_artifact']['content_matched_paths']
    )
    assert '- Verify Closed Beta RC artifact contents: True' in markdown
    assert '| Content status | passed |' in markdown
    assert ('gh-test', 'run', 'download', '222', '--name', 'closed-beta-rc-evidence') == calls[-1][:6]


def test_build_evidence_marks_missing_closed_beta_artifact_contents_incomplete(monkeypatch):
    def fake_run(args, **kwargs):
        if args[1:3] == ('workflow', 'list'):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {'name': 'AIDM CI', 'state': 'active', 'path': '.github/workflows/ci.yml'},
                        {'name': 'Closed Beta RC', 'state': 'active', 'path': '.github/workflows/closed-beta-rc.yml'},
                    ]
                ),
            )
        if args[1:3] == ('run', 'list'):
            workflow = args[args.index('--workflow') + 1]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            'databaseId': 111 if workflow == 'AIDM CI' else 222,
                            'displayTitle': workflow,
                            'status': 'completed',
                            'conclusion': 'success',
                            'event': 'workflow_dispatch',
                            'headSha': 'abc123',
                            'updatedAt': '2026-06-19T00:00:00Z',
                            'url': f'https://github.com/example/AIDM/actions/runs/{111 if workflow == "AIDM CI" else 222}',
                        }
                    ]
                ),
            )
        if args[1:3] == ('run', 'view'):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        'artifacts': [
                            {
                                'name': 'closed-beta-rc-evidence',
                                'url': 'https://github.com/example/AIDM/actions/runs/222/artifacts/333',
                                'sizeInBytes': 12345,
                                'expired': False,
                            }
                        ]
                    }
                ),
            )
        if args[1:3] == ('run', 'download'):
            output_dir = pathlib.Path(args[args.index('--dir') + 1])
            path = output_dir / 'tmp/release/rc-evidence.md'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('ok', encoding='utf-8')
            return SimpleNamespace(returncode=0, stdout='')
        return SimpleNamespace(returncode=1, stderr='unexpected call', stdout='')

    monkeypatch.setattr(render_github_actions_evidence.subprocess, 'run', fake_run)

    evidence = build_evidence(
        ci_run_url='https://github.com/example/AIDM/actions/runs/111',
        closed_beta_rc_run_url='https://github.com/example/AIDM/actions/runs/222',
        include_gh_details=True,
        verify_closed_beta_rc_artifact_contents=True,
        gh_executable='gh-test',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
    )
    markdown = render_markdown(evidence)

    assert evidence['status'] == 'incomplete'
    assert evidence['closed_beta_rc_artifact_status'] == 'passed'
    assert evidence['closed_beta_rc_artifact_content_status'] == 'missing'
    assert 'tmp/release/aidm-source-*.tar.gz' in evidence['closed_beta_rc_artifact']['content_missing_globs']
    assert 'tmp/release/security-forbidden-evidence.md' in evidence['closed_beta_rc_artifact']['content_missing_globs']
    assert (
        'tmp/verification_artifacts/visual-smoke/*/desktop-shell.png'
        in evidence['closed_beta_rc_artifact']['content_missing_globs']
    )
    assert any('artifact contents' in action for action in evidence['next_actions'])
    assert '| Content status | missing |' in markdown


def test_build_evidence_defers_current_workflow_artifact_check(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        if args[1:3] == ('workflow', 'list'):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {'name': 'AIDM CI', 'state': 'active', 'path': '.github/workflows/ci.yml'},
                        {'name': 'Closed Beta RC', 'state': 'active', 'path': '.github/workflows/closed-beta-rc.yml'},
                    ]
                ),
            )
        if args[1:3] == ('run', 'list'):
            workflow = args[args.index('--workflow') + 1]
            is_closed_beta = workflow == 'Closed Beta RC'
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            'databaseId': 222 if is_closed_beta else 111,
                            'displayTitle': workflow,
                            'status': 'in_progress' if is_closed_beta else 'completed',
                            'conclusion': '' if is_closed_beta else 'success',
                            'event': 'workflow_dispatch',
                            'headSha': 'abc123',
                            'updatedAt': '2026-06-19T00:00:00Z',
                            'url': f'https://github.com/example/AIDM/actions/runs/{222 if is_closed_beta else 111}',
                        }
                    ]
                ),
            )
        return SimpleNamespace(returncode=1, stderr='unexpected call', stdout='')

    monkeypatch.setattr(render_github_actions_evidence.subprocess, 'run', fake_run)

    evidence = build_evidence(
        ci_run_url='https://github.com/example/AIDM/actions/runs/111',
        closed_beta_rc_run_url='https://github.com/example/AIDM/actions/runs/222',
        include_gh_details=True,
        gh_executable='gh-test',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={'GITHUB_ACTIONS': 'true', 'GITHUB_RUN_ID': '222'},
    )
    markdown = render_markdown(evidence)

    assert evidence['status'] == 'passed'
    assert evidence['closed_beta_rc_artifact_status'] == 'deferred'
    assert evidence['closed_beta_rc_artifact']['run_id'] == '222'
    assert evidence['defer_current_run_artifact_check'] is True
    assert '| Status | deferred |' in markdown
    assert not any(call[:3] == ('gh-test', 'run', 'view') for call in calls)


def test_build_evidence_marks_missing_closed_beta_artifact_incomplete(monkeypatch):
    def fake_run(args, **kwargs):
        if args[1:3] == ('workflow', 'list'):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {'name': 'AIDM CI', 'state': 'active', 'path': '.github/workflows/ci.yml'},
                        {'name': 'Closed Beta RC', 'state': 'active', 'path': '.github/workflows/closed-beta-rc.yml'},
                    ]
                ),
            )
        if args[1:3] == ('run', 'list'):
            workflow = args[args.index('--workflow') + 1]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            'databaseId': 111 if workflow == 'AIDM CI' else 222,
                            'displayTitle': workflow,
                            'status': 'completed',
                            'conclusion': 'success',
                            'event': 'workflow_dispatch',
                            'headSha': 'abc123',
                            'updatedAt': '2026-06-19T00:00:00Z',
                            'url': f'https://github.com/example/AIDM/actions/runs/{111 if workflow == "AIDM CI" else 222}',
                        }
                    ]
                ),
            )
        if args[1:3] == ('run', 'view'):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({'artifacts': [{'name': 'browser-smoke', 'url': 'https://example.test/artifacts/1'}]}),
            )
        return SimpleNamespace(returncode=1, stderr='unexpected call', stdout='')

    monkeypatch.setattr(render_github_actions_evidence.subprocess, 'run', fake_run)

    evidence = build_evidence(
        ci_run_url='https://github.com/example/AIDM/actions/runs/111',
        closed_beta_rc_run_url='https://github.com/example/AIDM/actions/runs/222',
        include_gh_details=True,
        gh_executable='gh-test',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
    )
    markdown = render_markdown(evidence)

    assert evidence['status'] == 'incomplete'
    assert evidence['closed_beta_rc_artifact_status'] == 'missing'
    assert evidence['closed_beta_rc_artifact']['available_names'] == ['browser-smoke']
    assert any('uploaded the closed-beta-rc-evidence artifact' in action for action in evidence['next_actions'])
    assert '| Available artifact names | browser-smoke |' in markdown


def test_build_evidence_explains_missing_active_closed_beta_run(monkeypatch):
    def fake_run(args, **kwargs):
        if args[1:3] == ('workflow', 'list'):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {'name': 'AIDM CI', 'state': 'active', 'path': '.github/workflows/ci.yml'},
                        {'name': 'Closed Beta RC', 'state': 'active', 'path': '.github/workflows/closed-beta-rc.yml'},
                    ]
                ),
            )
        if args[1:3] == ('run', 'list'):
            workflow = args[args.index('--workflow') + 1]
            if workflow == 'Closed Beta RC':
                return SimpleNamespace(returncode=0, stdout='[]')
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            'databaseId': 123,
                            'displayTitle': workflow,
                            'status': 'completed',
                            'conclusion': 'success',
                            'event': 'push',
                            'headSha': 'abc123',
                            'updatedAt': '2026-06-19T00:00:00Z',
                            'url': 'https://github.com/example/AIDM/actions/runs/123',
                        }
                    ]
                ),
            )
        return SimpleNamespace(returncode=1, stderr='unexpected call', stdout='')

    monkeypatch.setattr(render_github_actions_evidence.subprocess, 'run', fake_run)

    evidence = build_evidence(
        ci_run_url='https://github.com/example/AIDM/actions/runs/123',
        include_gh_details=True,
        gh_executable='gh-test',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
    )
    markdown = render_markdown(evidence)

    assert evidence['status'] == 'incomplete'
    assert evidence['missing'] == ['Closed Beta RC run URL']
    assert evidence['missing_details']['Closed Beta RC run URL'] == 'No recent Closed Beta RC runs were returned by gh run list.'
    assert evidence['next_actions'] == [
        'Run the manual Closed Beta RC workflow for commit abc123, then rerun make github-actions-evidence.'
    ]
    assert '## Missing Proof Details' in markdown
    assert 'No recent Closed Beta RC runs were returned by gh run list.' in markdown


def test_build_evidence_contextualizes_next_action_for_dirty_worktree():
    evidence = build_evidence(
        ci_run_url='https://github.com/example/AIDM/actions/runs/123',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
        worktree={'state': 'dirty', 'summary': 'dirty (3 changed/untracked paths)'},
    )
    markdown = render_markdown(evidence)

    assert evidence['next_actions'] == [
        'Freeze and push a clean signed-off candidate first; then run the manual Closed Beta RC workflow '
        'for the signed-off commit, then rerun make github-actions-evidence.'
    ]
    assert '- Worktree: dirty; dirty (3 changed/untracked paths)' in markdown


def test_build_evidence_contextualizes_next_action_for_unknown_worktree():
    evidence = build_evidence(
        ci_run_url='https://github.com/example/AIDM/actions/runs/123',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
        worktree={'state': 'unknown', 'summary': 'git status failed'},
    )

    assert evidence['next_actions'] == [
        'Confirm the signed-off candidate is clean first; then run the manual Closed Beta RC workflow '
        'for the signed-off commit, then rerun make github-actions-evidence.'
    ]


def test_build_evidence_keeps_commit_specific_next_action_for_clean_worktree():
    evidence = build_evidence(
        ci_run_url='https://github.com/example/AIDM/actions/runs/123',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
        worktree={'state': 'clean', 'summary': 'clean'},
    )

    assert evidence['next_actions'] == [
        'Run the manual Closed Beta RC workflow for commit abc123, then rerun make github-actions-evidence.'
    ]


def test_build_evidence_rejects_run_url_not_found_in_gh_details(monkeypatch):
    def fake_run(args, **kwargs):
        if args[1:3] == ('workflow', 'list'):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {'name': 'AIDM CI', 'state': 'active', 'path': '.github/workflows/ci.yml'},
                        {'name': 'Closed Beta RC', 'state': 'active', 'path': '.github/workflows/closed-beta-rc.yml'},
                    ]
                ),
            )
        if args[1:3] == ('run', 'list'):
            workflow = args[args.index('--workflow') + 1]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            'databaseId': 999,
                            'displayTitle': workflow,
                            'status': 'completed',
                            'conclusion': 'success',
                            'event': 'workflow_dispatch',
                            'headSha': 'abc123',
                            'updatedAt': '2026-06-19T00:00:00Z',
                            'url': f'https://github.com/example/AIDM/actions/runs/{999 if workflow == "AIDM CI" else 888}',
                        }
                    ]
                ),
            )
        return SimpleNamespace(returncode=1, stderr='unexpected call', stdout='')

    monkeypatch.setattr(render_github_actions_evidence.subprocess, 'run', fake_run)

    evidence = build_evidence(
        ci_run_url='https://github.com/example/AIDM/actions/runs/111',
        closed_beta_rc_run_url='https://github.com/example/AIDM/actions/runs/222',
        include_gh_details=True,
        gh_executable='gh-test',
        commit='abc123',
        repository='example/AIDM',
        generated_at='2026-06-19T00:05:00+00:00',
        env={},
    )

    assert evidence['status'] == 'invalid'
    assert evidence['validation_errors'] == [
        'AIDM CI run URL was not found in latest AIDM CI runs for this repository',
        'Closed Beta RC run URL was not found in latest Closed Beta RC runs for this repository',
    ]


def test_main_writes_markdown_and_json(tmp_path):
    output_path = tmp_path / 'github-actions-evidence.md'
    json_path = tmp_path / 'github-actions-evidence.json'

    exit_code = main(
        [
            '--ci-run-url',
            'https://github.com/example/AIDM/actions/runs/111',
            '--closed-beta-rc-run-url',
            'https://github.com/example/AIDM/actions/runs/222',
            '--commit',
            'abc123',
            '--repository',
            'example/AIDM',
            '--auto-gh',
            '--evidence-report',
            str(output_path),
            '--json-output',
            str(json_path),
            '--generated-at',
            '2026-06-19T00:05:00+00:00',
        ]
    )

    assert exit_code == 0
    assert '- Status: passed' in output_path.read_text(encoding='utf-8')
    payload = json.loads(json_path.read_text(encoding='utf-8'))
    assert payload['auto_gh'] is True
    assert payload['aidm_ci_run_url'].endswith('/111')
    assert payload['closed_beta_rc_run_url'].endswith('/222')
