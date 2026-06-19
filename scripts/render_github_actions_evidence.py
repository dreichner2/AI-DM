#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'github-actions-evidence.md'
DEFAULT_JSON_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'github-actions-evidence.json'
DEFAULT_CI_WORKFLOW = 'AIDM CI'
DEFAULT_CLOSED_BETA_RC_WORKFLOW = 'Closed Beta RC'
DEFAULT_CLOSED_BETA_RC_ARTIFACT = 'closed-beta-rc-evidence'
DEFAULT_CLOSED_BETA_RC_ARTIFACT_CONTENT_GLOBS = (
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
    'tmp/release/issue-evidence/*.md',
    'tmp/release/aidm-source-*.tar.gz',
    'tmp/release/aidm-source-*.tar.gz.sha256',
    'tmp/verification_artifacts/visual-smoke/*/desktop-shell.png',
    'tmp/verification_artifacts/visual-smoke/*/mobile-full.png',
    'tmp/verification_artifacts/visual-smoke/*/short-height-composer.png',
)
GITHUB_RUN_URL_RE = re.compile(r'^https://github\.com/([^/\s]+)/([^/\s]+)/actions/runs/(\d+)(?:[/?#].*)?$')
GITHUB_REMOTE_RE = re.compile(r'(?:github\.com[:/])([^/\s]+)/([^/\s]+?)(?:\.git)?/?$')


def _resolve_repo_path(path: pathlib.Path) -> pathlib.Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _relative_or_absolute(path: pathlib.Path | str) -> str:
    candidate = pathlib.Path(path)
    try:
        return str(candidate.relative_to(REPO_ROOT))
    except ValueError:
        return str(candidate)


def _git_commit(*, short: bool = True) -> str:
    args = ('git', 'rev-parse', '--short', 'HEAD') if short else ('git', 'rev-parse', 'HEAD')
    result = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 'unknown'
    return result.stdout.strip() or 'unknown'


def _repository_from_remote_url(url: str) -> str:
    match = GITHUB_REMOTE_RE.search(url.strip())
    if not match:
        return ''
    owner, repo = match.groups()
    return f'{owner}/{repo}'


def _git_repository() -> str:
    result = subprocess.run(
        ('git', 'remote', 'get-url', 'origin'),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ''
    return _repository_from_remote_url(result.stdout)


def _git_worktree_state() -> dict[str, Any]:
    result = subprocess.run(
        ('git', 'status', '--short'),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {
            'state': 'unknown',
            'dirty': None,
            'summary': result.stderr.strip() or result.stdout.strip() or 'git status failed',
        }
    paths = [line for line in result.stdout.splitlines() if line.strip()]
    if not paths:
        return {'state': 'clean', 'dirty': False, 'summary': 'clean', 'changed_paths': 0}
    return {
        'state': 'dirty',
        'dirty': True,
        'summary': f'dirty ({len(paths)} changed/untracked paths)',
        'changed_paths': len(paths),
    }


def _closed_beta_rc_url_from_env(env: dict[str, str]) -> str:
    server_url = env.get('GITHUB_SERVER_URL') or ''
    repository = env.get('GITHUB_REPOSITORY') or ''
    run_id = env.get('GITHUB_RUN_ID') or ''
    if server_url and repository and run_id:
        return f'{server_url.rstrip("/")}/{repository}/actions/runs/{run_id}'
    return ''


def _github_run_id(url: str) -> str:
    match = GITHUB_RUN_URL_RE.match(url.strip())
    if not match:
        return ''
    return match.group(3)


def _repository_from_run_url(url: str) -> str:
    match = GITHUB_RUN_URL_RE.match(url.strip())
    if not match:
        return ''
    owner, repo, _run_id = match.groups()
    return f'{owner}/{repo}'


def _gh_run_url(
    *,
    workflow: str,
    commit: str,
    gh_executable: str,
    status: str = 'success',
) -> str:
    if not workflow.strip() or not commit.strip():
        return ''
    try:
        result = subprocess.run(
            (
                gh_executable,
                'run',
                'list',
                '--workflow',
                workflow,
                '--commit',
                commit,
                '--status',
                status,
                '--limit',
                '1',
                '--json',
                'url',
                '--jq',
                '.[0].url // ""',
            ),
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return ''
    if result.returncode != 0:
        return ''
    return result.stdout.strip()


def _gh_json(
    args: tuple[str, ...],
    *,
    gh_executable: str,
) -> tuple[Any, str]:
    try:
        result = subprocess.run(
            (gh_executable, *args),
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None, f'{gh_executable} not found'
    if result.returncode != 0:
        return None, result.stderr.strip() or result.stdout.strip() or f'{gh_executable} exited {result.returncode}'
    try:
        return json.loads(result.stdout or 'null'), ''
    except json.JSONDecodeError as exc:
        return None, f'invalid gh JSON: {exc}'


def _workflow_detail(workflows: Any, workflow_name: str) -> dict[str, Any]:
    if not isinstance(workflows, list):
        return {'name': workflow_name, 'found': False, 'state': '', 'path': ''}
    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        if workflow.get('name') == workflow_name:
            return {
                'name': workflow_name,
                'found': True,
                'state': workflow.get('state') or '',
                'path': workflow.get('path') or '',
            }
    return {'name': workflow_name, 'found': False, 'state': '', 'path': ''}


def _artifact_content_fields(
    *,
    status: str = 'not-checked',
    required_globs: tuple[str, ...] = DEFAULT_CLOSED_BETA_RC_ARTIFACT_CONTENT_GLOBS,
    missing_globs: list[str] | None = None,
    matched_paths: list[str] | None = None,
    file_count: int | None = None,
    error: str = '',
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        'content_status': status,
        'content_checked': status not in {'not-checked', 'deferred'},
        'content_required_globs': list(required_globs),
        'content_missing_globs': missing_globs or [],
        'content_matched_paths': matched_paths or [],
        'content_file_count': file_count,
    }
    if error:
        fields['content_error'] = error
    return fields


def _artifact_payload(artifact: dict[str, Any], *, expected_name: str, run_id: str) -> dict[str, Any]:
    return {
        'status': 'passed',
        'expected_name': expected_name,
        'name': str(artifact.get('name') or ''),
        'url': str(artifact.get('url') or artifact.get('archive_download_url') or ''),
        'size_in_bytes': artifact.get('sizeInBytes') or artifact.get('size_in_bytes'),
        'expired': artifact.get('expired'),
        'run_id': run_id,
        'checked': True,
        'available_names': [],
        **_artifact_content_fields(),
    }


def _missing_artifact_payload(
    *,
    expected_name: str,
    run_id: str,
    available_names: list[str],
) -> dict[str, Any]:
    return {
        'status': 'missing',
        'expected_name': expected_name,
        'name': '',
        'url': '',
        'size_in_bytes': None,
        'expired': None,
        'run_id': run_id,
        'checked': True,
        'available_names': available_names,
        **_artifact_content_fields(status='missing'),
    }


def _unchecked_artifact_payload(*, expected_name: str, status: str = 'not-checked', run_id: str = '', error: str = '') -> dict[str, Any]:
    payload = {
        'status': status,
        'expected_name': expected_name,
        'name': '',
        'url': '',
        'size_in_bytes': None,
        'expired': None,
        'run_id': run_id,
        'checked': False,
        'available_names': [],
        **_artifact_content_fields(status=status if status == 'deferred' else 'not-checked'),
    }
    if error:
        payload['error'] = error
    return payload


def _deferred_artifact_payload(*, expected_name: str, run_id: str) -> dict[str, Any]:
    payload = _unchecked_artifact_payload(expected_name=expected_name, status='deferred', run_id=run_id)
    payload.update(_artifact_content_fields(status='deferred'))
    payload['deferred_reason'] = 'current GitHub Actions run uploads this artifact after evidence rendering'
    return payload


def _download_artifact_contents(
    *,
    gh_executable: str,
    run_id: str,
    artifact_name: str,
    required_globs: tuple[str, ...],
) -> tuple[dict[str, Any], str]:
    with tempfile.TemporaryDirectory(prefix='aidm-gh-artifact-') as tmp_dir:
        try:
            result = subprocess.run(
                (
                    gh_executable,
                    'run',
                    'download',
                    run_id,
                    '--name',
                    artifact_name,
                    '--dir',
                    tmp_dir,
                ),
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            error = f'{gh_executable} not found'
            return _artifact_content_fields(status='unknown', required_globs=required_globs, error=error), error
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip() or f'{gh_executable} exited {result.returncode}'
            return _artifact_content_fields(status='unknown', required_globs=required_globs, error=error), error
        root = pathlib.Path(tmp_dir)
        artifact_paths = sorted(
            str(path.relative_to(root)).replace(os.sep, '/')
            for path in root.rglob('*')
            if path.is_file()
        )
        artifact_path_candidates = set(artifact_paths)
        for artifact_path in artifact_paths:
            if artifact_path.startswith(('release/', 'verification_artifacts/')):
                artifact_path_candidates.add(f'tmp/{artifact_path}')
        artifact_paths_for_matching = sorted(artifact_path_candidates)

    matches_by_glob: dict[str, list[str]] = {
        pattern: [candidate for candidate in artifact_paths_for_matching if fnmatch.fnmatch(candidate, pattern)]
        for pattern in required_globs
    }
    missing_globs = [pattern for pattern, matches in matches_by_glob.items() if not matches]
    matched_paths = sorted({match for matches in matches_by_glob.values() for match in matches})
    content_status = 'missing' if missing_globs else 'passed'
    return (
        _artifact_content_fields(
            status=content_status,
            required_globs=required_globs,
            missing_globs=missing_globs,
            matched_paths=matched_paths,
            file_count=len(artifact_paths),
        ),
        '',
    )


def _artifacts_from_api_payload(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, dict):
        artifacts = payload.get('artifacts')
    elif isinstance(payload, list):
        artifacts = payload
    else:
        return None
    if not isinstance(artifacts, list):
        return None
    return [artifact for artifact in artifacts if isinstance(artifact, dict)]


def _list_run_artifacts(
    *,
    gh_executable: str,
    run_url: str,
    run_id: str,
) -> tuple[list[dict[str, Any]] | None, str]:
    payload, error = _gh_json(
        ('run', 'view', run_id, '--json', 'artifacts'),
        gh_executable=gh_executable,
    )
    if not error:
        artifacts = _artifacts_from_api_payload(payload)
        if artifacts is not None:
            return artifacts, ''
        error = 'gh run view returned no artifacts list'

    repository = _repository_from_run_url(run_url) or _git_repository()
    if not repository:
        return None, error or 'could not determine repository for artifact lookup'

    api_payload, api_error = _gh_json(
        ('api', f'repos/{repository}/actions/runs/{run_id}/artifacts', '--paginate'),
        gh_executable=gh_executable,
    )
    if api_error:
        combined = f'{error}; gh api artifacts: {api_error}' if error else f'gh api artifacts: {api_error}'
        return None, combined
    artifacts = _artifacts_from_api_payload(api_payload)
    if artifacts is None:
        return None, 'gh api artifacts returned no artifacts list'
    return artifacts, ''


def _closed_beta_rc_artifact_details(
    *,
    gh_executable: str,
    closed_beta_rc_run_url: str,
    expected_name: str,
    defer_current_run_check: bool,
    verify_contents: bool,
    required_content_globs: tuple[str, ...],
) -> tuple[dict[str, Any], str]:
    run_id = _github_run_id(closed_beta_rc_run_url)
    if not run_id:
        return _unchecked_artifact_payload(expected_name=expected_name), ''
    if defer_current_run_check:
        return _deferred_artifact_payload(expected_name=expected_name, run_id=run_id), ''
    artifacts, error = _list_run_artifacts(
        gh_executable=gh_executable,
        run_url=closed_beta_rc_run_url,
        run_id=run_id,
    )
    if error:
        return (
            _unchecked_artifact_payload(expected_name=expected_name, status='unknown', run_id=run_id, error=error),
            f'Closed Beta RC artifacts: {error}',
        )
    if not isinstance(artifacts, list):
        return (
            _unchecked_artifact_payload(
                expected_name=expected_name,
                status='unknown',
                run_id=run_id,
                error='gh run view returned no artifacts list',
            ),
            'Closed Beta RC artifacts: gh run view returned no artifacts list',
        )
    available_names = [str(artifact.get('name') or '') for artifact in artifacts if isinstance(artifact, dict)]
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        if str(artifact.get('name') or '').strip().lower() == expected_name.lower():
            artifact_payload = _artifact_payload(artifact, expected_name=expected_name, run_id=run_id)
            if verify_contents:
                content_fields, content_error = _download_artifact_contents(
                    gh_executable=gh_executable,
                    run_id=run_id,
                    artifact_name=expected_name,
                    required_globs=required_content_globs,
                )
                artifact_payload.update(content_fields)
                if content_error:
                    return artifact_payload, f'Closed Beta RC artifact content: {content_error}'
            return artifact_payload, ''
    return _missing_artifact_payload(expected_name=expected_name, run_id=run_id, available_names=available_names), ''


def _gh_details(
    *,
    gh_executable: str,
    ci_workflow: str,
    closed_beta_rc_workflow: str,
    closed_beta_rc_run_url: str,
    closed_beta_rc_artifact_name: str,
    defer_current_run_artifact_check: bool,
    verify_closed_beta_rc_artifact_contents: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    workflows, workflows_error = _gh_json(
        ('workflow', 'list', '--json', 'name,state,path'),
        gh_executable=gh_executable,
    )
    if workflows_error:
        errors.append(f'workflow list: {workflows_error}')
        workflows = []
    workflow_details = {
        ci_workflow: _workflow_detail(workflows, ci_workflow),
        closed_beta_rc_workflow: _workflow_detail(workflows, closed_beta_rc_workflow),
    }
    latest_runs: dict[str, list[dict[str, Any]]] = {}
    for workflow_name in (ci_workflow, closed_beta_rc_workflow):
        runs, runs_error = _gh_json(
            (
                'run',
                'list',
                '--workflow',
                workflow_name,
                '--limit',
                '5',
                '--json',
                'databaseId,displayTitle,status,conclusion,event,headSha,createdAt,updatedAt,url',
            ),
            gh_executable=gh_executable,
        )
        if runs_error:
            errors.append(f'{workflow_name} runs: {runs_error}')
            runs = []
        latest_runs[workflow_name] = runs if isinstance(runs, list) else []
    closed_beta_rc_artifact, artifact_error = _closed_beta_rc_artifact_details(
        gh_executable=gh_executable,
        closed_beta_rc_run_url=closed_beta_rc_run_url,
        expected_name=closed_beta_rc_artifact_name,
        defer_current_run_check=defer_current_run_artifact_check,
        verify_contents=verify_closed_beta_rc_artifact_contents,
        required_content_globs=DEFAULT_CLOSED_BETA_RC_ARTIFACT_CONTENT_GLOBS,
    )
    if artifact_error:
        errors.append(artifact_error)
    return {
        'workflow_details': workflow_details,
        'latest_runs': latest_runs,
        'closed_beta_rc_artifact': closed_beta_rc_artifact,
        'closed_beta_rc_artifact_status': closed_beta_rc_artifact.get('status') or 'not-checked',
        'gh_errors': errors,
    }


def _github_run_url_error(*, label: str, url: str, repository: str) -> str:
    if not url.strip():
        return ''
    match = GITHUB_RUN_URL_RE.match(url.strip())
    if not match:
        return f'{label} run URL must look like https://github.com/<owner>/<repo>/actions/runs/<id>'
    if repository and repository != 'local':
        owner, repo, _run_id = match.groups()
        actual_repository = f'{owner}/{repo}'
        if actual_repository.lower() != repository.lower():
            return f'{label} run URL repository {actual_repository} does not match {repository}'
    return ''


def _run_matches_commit(run: dict[str, Any], commit: str) -> bool:
    expected = commit.strip().lower()
    if not expected:
        return True
    actual = str(run.get('headSha') or '').strip().lower()
    return bool(actual and (actual == expected or actual.startswith(expected) or expected.startswith(actual)))


def _workflow_run_url_error(
    *,
    label: str,
    workflow: str,
    url: str,
    commit: str,
    latest_runs: dict[str, Any],
) -> str:
    if not url.strip() or workflow not in latest_runs:
        return ''
    runs = latest_runs.get(workflow)
    if not isinstance(runs, list):
        return ''
    for run in runs:
        if not isinstance(run, dict) or run.get('url') != url:
            continue
        if run.get('status') != 'completed' or run.get('conclusion') != 'success':
            return f'{label} run URL is not a completed successful {workflow} run'
        if not _run_matches_commit(run, commit):
            return f'{label} run URL does not match commit {commit}'
        return ''
    return f'{label} run URL was not found in latest {workflow} runs for this repository'


def _validation_errors(
    *,
    ci_run_url: str,
    closed_beta_rc_run_url: str,
    repository: str,
    commit: str,
    include_gh_details: bool,
    latest_runs: dict[str, Any],
    ci_workflow: str,
    closed_beta_rc_workflow: str,
    defer_current_closed_beta_rc_run_check: bool = False,
) -> list[str]:
    errors = [
        error
        for error in (
            _github_run_url_error(label='AIDM CI', url=ci_run_url, repository=repository),
            _github_run_url_error(label='Closed Beta RC', url=closed_beta_rc_run_url, repository=repository),
        )
        if error
    ]
    if include_gh_details:
        errors.extend(
            error
            for error in (
                _workflow_run_url_error(
                    label='AIDM CI',
                    workflow=ci_workflow,
                    url=ci_run_url,
                    commit=commit,
                    latest_runs=latest_runs,
                ),
                ''
                if defer_current_closed_beta_rc_run_check
                else _workflow_run_url_error(
                    label='Closed Beta RC',
                    workflow=closed_beta_rc_workflow,
                    url=closed_beta_rc_run_url,
                    commit=commit,
                    latest_runs=latest_runs,
                ),
            )
            if error
        )
    return errors


def _latest_run_summary(run: dict[str, Any]) -> str:
    return (
        f"latest status={run.get('status') or 'unknown'} "
        f"conclusion={run.get('conclusion') or 'unknown'} "
        f"head={run.get('headSha') or 'unknown'} "
        f"updated={run.get('updatedAt') or run.get('createdAt') or 'unknown'} "
        f"url={run.get('url') or 'missing'}"
    )


def _missing_run_reason(
    *,
    label: str,
    workflow: str,
    commit: str,
    include_gh_details: bool,
    workflow_details: dict[str, Any],
    latest_runs: dict[str, Any],
) -> str:
    if not include_gh_details:
        return f'{label} run URL was not provided and gh details were not requested.'
    detail = workflow_details.get(workflow) if isinstance(workflow_details.get(workflow), dict) else {}
    if detail and not detail.get('found'):
        return f'{workflow} workflow was not found by gh workflow list.'
    state = str(detail.get('state') or '').strip()
    if state and state != 'active':
        return f'{workflow} workflow exists but is {state}.'

    runs = latest_runs.get(workflow)
    if not isinstance(runs, list) or not runs:
        return f'No recent {workflow} runs were returned by gh run list.'

    successful_commit_runs = [
        run
        for run in runs
        if isinstance(run, dict)
        and run.get('status') == 'completed'
        and run.get('conclusion') == 'success'
        and _run_matches_commit(run, commit)
    ]
    if successful_commit_runs:
        return f'{workflow} has a successful run for commit {commit}, but no run URL was selected; pass it explicitly.'
    return f'No completed successful {workflow} run for commit {commit} was found among the latest runs; {_latest_run_summary(runs[0])}.'


def _missing_details(
    *,
    missing: list[str],
    commit: str,
    include_gh_details: bool,
    workflow_details: dict[str, Any],
    latest_runs: dict[str, Any],
    ci_workflow: str,
    closed_beta_rc_workflow: str,
) -> dict[str, str]:
    details: dict[str, str] = {}
    if 'AIDM CI run URL' in missing:
        details['AIDM CI run URL'] = _missing_run_reason(
            label='AIDM CI',
            workflow=ci_workflow,
            commit=commit,
            include_gh_details=include_gh_details,
            workflow_details=workflow_details,
            latest_runs=latest_runs,
        )
    if 'Closed Beta RC run URL' in missing:
        details['Closed Beta RC run URL'] = _missing_run_reason(
            label='Closed Beta RC',
            workflow=closed_beta_rc_workflow,
            commit=commit,
            include_gh_details=include_gh_details,
            workflow_details=workflow_details,
            latest_runs=latest_runs,
        )
    return details


def _next_actions(
    *,
    missing: list[str],
    validation_errors: list[str],
    commit: str,
    worktree: dict[str, Any],
    closed_beta_rc_artifact: dict[str, Any],
    verify_closed_beta_rc_artifact_contents: bool,
) -> list[str]:
    state = str(worktree.get('state') or '').strip().lower()
    prefix = ''
    commit_label = f'commit {commit}'
    if state == 'dirty':
        prefix = 'Freeze and push a clean signed-off candidate first; then '
        commit_label = 'the signed-off commit'
    elif state == 'unknown':
        prefix = 'Confirm the signed-off candidate is clean first; then '
        commit_label = 'the signed-off commit'

    def action_text(text: str) -> str:
        if prefix:
            return prefix + text
        return text[:1].upper() + text[1:]

    actions: list[str] = []
    if 'AIDM CI run URL' in missing:
        actions.append(
            action_text(f'run or wait for AIDM CI to pass on {commit_label}, then rerun make github-actions-evidence.')
        )
    if 'Closed Beta RC run URL' in missing:
        actions.append(
            action_text(
                f'run the manual Closed Beta RC workflow for {commit_label}, then rerun make github-actions-evidence.'
            )
        )
    artifact_status = str(closed_beta_rc_artifact.get('status') or 'not-checked')
    if artifact_status in {'missing', 'unknown'} and 'Closed Beta RC run URL' not in missing:
        actions.append(
            action_text(
                'confirm the manual Closed Beta RC workflow uploaded the '
                f'{closed_beta_rc_artifact.get("expected_name") or DEFAULT_CLOSED_BETA_RC_ARTIFACT} artifact, '
                'then rerun make github-actions-evidence with --include-gh-details.'
            )
        )
    content_status = str(closed_beta_rc_artifact.get('content_status') or 'not-checked')
    if artifact_status == 'passed' and content_status == 'not-checked':
        actions.append(
            action_text(
                'rerun make github-actions-evidence with --include-gh-details '
                '--verify-closed-beta-rc-artifact-contents to prove the artifact contains the required RC evidence files.'
            )
        )
    if artifact_status == 'passed' and content_status in {'missing', 'unknown'}:
        actions.append(
            action_text(
                'fix the closed-beta-rc-evidence artifact contents, then rerun make github-actions-evidence '
                'with --verify-closed-beta-rc-artifact-contents.'
            )
        )
    if validation_errors:
        actions.append('Fix GitHub Actions evidence validation errors before using these URLs for RC signoff.')
    return actions


def build_evidence(
    *,
    ci_run_url: str = '',
    closed_beta_rc_run_url: str = '',
    commit: str = '',
    repository: str = '',
    generated_at: str = '',
    auto_gh: bool = False,
    include_gh_details: bool = False,
    gh_executable: str = 'gh',
    ci_workflow: str = DEFAULT_CI_WORKFLOW,
    closed_beta_rc_workflow: str = DEFAULT_CLOSED_BETA_RC_WORKFLOW,
    closed_beta_rc_artifact_name: str = DEFAULT_CLOSED_BETA_RC_ARTIFACT,
    verify_closed_beta_rc_artifact_contents: bool = False,
    env: dict[str, str] | None = None,
    worktree: dict[str, Any] | None = None,
) -> dict[str, Any]:
    env = os.environ if env is None else env
    ci_run_url = ci_run_url.strip()
    closed_beta_rc_run_url = closed_beta_rc_run_url.strip() or _closed_beta_rc_url_from_env(env)
    discovery_commit = commit.strip() or env.get('GITHUB_SHA', '') or _git_commit(short=False)
    commit = commit.strip() or env.get('GITHUB_SHA', '')[:12] or _git_commit()
    repository = repository.strip() or env.get('GITHUB_REPOSITORY', '') or _git_repository() or 'local'
    generated_at = generated_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    if auto_gh and not ci_run_url:
        ci_run_url = _gh_run_url(
            workflow=ci_workflow,
            commit=discovery_commit,
            gh_executable=gh_executable,
        )
    if auto_gh and not closed_beta_rc_run_url:
        closed_beta_rc_run_url = _gh_run_url(
            workflow=closed_beta_rc_workflow,
            commit=discovery_commit,
            gh_executable=gh_executable,
        )

    closed_beta_rc_run_id = _github_run_id(closed_beta_rc_run_url)
    defer_current_run_artifact_check = bool(
        include_gh_details
        and env.get('GITHUB_ACTIONS') == 'true'
        and closed_beta_rc_run_id
        and closed_beta_rc_run_id == env.get('GITHUB_RUN_ID')
    )
    gh_details: dict[str, Any] = {}
    if include_gh_details:
        gh_details = _gh_details(
            gh_executable=gh_executable,
            ci_workflow=ci_workflow,
            closed_beta_rc_workflow=closed_beta_rc_workflow,
            closed_beta_rc_run_url=closed_beta_rc_run_url,
            closed_beta_rc_artifact_name=closed_beta_rc_artifact_name,
            defer_current_run_artifact_check=defer_current_run_artifact_check,
            verify_closed_beta_rc_artifact_contents=verify_closed_beta_rc_artifact_contents,
        )
    closed_beta_rc_artifact = gh_details.get('closed_beta_rc_artifact')
    if not isinstance(closed_beta_rc_artifact, dict):
        closed_beta_rc_artifact = _unchecked_artifact_payload(expected_name=closed_beta_rc_artifact_name)

    missing: list[str] = []
    if not ci_run_url:
        missing.append('AIDM CI run URL')
    if not closed_beta_rc_run_url:
        missing.append('Closed Beta RC run URL')
    validation_errors = _validation_errors(
        ci_run_url=ci_run_url,
        closed_beta_rc_run_url=closed_beta_rc_run_url,
        repository=repository,
        commit=commit,
        include_gh_details=include_gh_details,
        latest_runs=gh_details.get('latest_runs') if isinstance(gh_details.get('latest_runs'), dict) else {},
        ci_workflow=ci_workflow,
        closed_beta_rc_workflow=closed_beta_rc_workflow,
        defer_current_closed_beta_rc_run_check=defer_current_run_artifact_check,
    )
    latest_runs = gh_details.get('latest_runs') if isinstance(gh_details.get('latest_runs'), dict) else {}
    workflow_details = gh_details.get('workflow_details') if isinstance(gh_details.get('workflow_details'), dict) else {}
    worktree = worktree if isinstance(worktree, dict) else {}
    artifact_status = str(closed_beta_rc_artifact.get('status') or 'not-checked')
    artifact_content_status = str(closed_beta_rc_artifact.get('content_status') or 'not-checked')
    artifact_incomplete = bool(
        include_gh_details
        and closed_beta_rc_run_url
        and (
            artifact_status in {'missing', 'unknown'}
            or (
                verify_closed_beta_rc_artifact_contents
                and artifact_content_status in {'missing', 'unknown'}
            )
        )
    )

    evidence = {
        'status': 'invalid' if validation_errors else ('passed' if not missing and not artifact_incomplete else 'incomplete'),
        'generated_at': generated_at,
        'repository': repository,
        'commit': commit,
        'aidm_ci_run_url': ci_run_url,
        'closed_beta_rc_run_url': closed_beta_rc_run_url,
        'closed_beta_rc_artifact': closed_beta_rc_artifact,
        'closed_beta_rc_artifact_status': artifact_status,
        'closed_beta_rc_artifact_content_status': artifact_content_status,
        'missing': missing,
        'missing_details': _missing_details(
            missing=missing,
            commit=commit,
            include_gh_details=include_gh_details,
            workflow_details=workflow_details,
            latest_runs=latest_runs,
            ci_workflow=ci_workflow,
            closed_beta_rc_workflow=closed_beta_rc_workflow,
        ),
        'validation_errors': validation_errors,
        'next_actions': _next_actions(
            missing=missing,
            validation_errors=validation_errors,
            commit=commit,
            worktree=worktree,
            closed_beta_rc_artifact=closed_beta_rc_artifact,
            verify_closed_beta_rc_artifact_contents=verify_closed_beta_rc_artifact_contents,
        ),
        'worktree': worktree,
        'auto_gh': auto_gh,
        'include_gh_details': include_gh_details,
        'ci_workflow': ci_workflow,
        'closed_beta_rc_workflow': closed_beta_rc_workflow,
        'closed_beta_rc_artifact_name': closed_beta_rc_artifact_name,
        'defer_current_run_artifact_check': defer_current_run_artifact_check,
        'verify_closed_beta_rc_artifact_contents': verify_closed_beta_rc_artifact_contents,
        'closed_beta_rc_artifact_content_globs': list(DEFAULT_CLOSED_BETA_RC_ARTIFACT_CONTENT_GLOBS),
    }
    if include_gh_details:
        evidence.update(gh_details)
    return evidence


def render_markdown(evidence: dict[str, Any]) -> str:
    missing = evidence.get('missing') or []
    missing_details = evidence.get('missing_details') if isinstance(evidence.get('missing_details'), dict) else {}
    closed_beta_rc_artifact = evidence.get('closed_beta_rc_artifact')
    if not isinstance(closed_beta_rc_artifact, dict):
        closed_beta_rc_artifact = {}
    workflow_rows = ['| Workflow | Found | State | Path |', '| --- | --- | --- | --- |']
    workflow_details = evidence.get('workflow_details') if isinstance(evidence.get('workflow_details'), dict) else {}
    for workflow_name in (evidence.get('ci_workflow') or DEFAULT_CI_WORKFLOW, evidence.get('closed_beta_rc_workflow') or DEFAULT_CLOSED_BETA_RC_WORKFLOW):
        detail = workflow_details.get(workflow_name) if isinstance(workflow_details.get(workflow_name), dict) else {}
        workflow_rows.append(
            f"| {workflow_name} | {detail.get('found', '')} | {detail.get('state') or ''} | {detail.get('path') or ''} |"
        )
    if len(workflow_rows) == 2:
        workflow_rows.append('| Not checked |  |  |  |')

    run_rows = ['| Workflow | Status | Conclusion | Head SHA | Updated | URL |', '| --- | --- | --- | --- | --- | --- |']
    latest_runs = evidence.get('latest_runs') if isinstance(evidence.get('latest_runs'), dict) else {}
    for workflow_name, runs in latest_runs.items():
        if not isinstance(runs, list):
            continue
        for run in runs[:5]:
            if not isinstance(run, dict):
                continue
            run_rows.append(
                f"| {workflow_name} | {run.get('status') or ''} | {run.get('conclusion') or ''} | "
                f"{run.get('headSha') or ''} | {run.get('updatedAt') or ''} | {run.get('url') or ''} |"
            )
    if len(run_rows) == 2:
        run_rows.append('| Not checked |  |  |  |  |  |')

    error_rows = ['| Error |', '| --- |']
    for error in evidence.get('gh_errors') or []:
        error_rows.append(f'| {error} |')
    if len(error_rows) == 2:
        error_rows.append('| None |')

    validation_rows = ['| Validation error |', '| --- |']
    for error in evidence.get('validation_errors') or []:
        validation_rows.append(f'| {error} |')
    if len(validation_rows) == 2:
        validation_rows.append('| None |')

    missing_rows = ['| Missing proof | Reason |', '| --- | --- |']
    for label in missing:
        missing_rows.append(f"| {label} | {missing_details.get(label) or 'Run URL was not provided/discovered.'} |")
    if len(missing_rows) == 2:
        missing_rows.append('| None |  |')

    action_rows = ['| Next action |', '| --- |']
    for action in evidence.get('next_actions') or []:
        action_rows.append(f'| {action} |')
    if len(action_rows) == 2:
        action_rows.append('| None |')

    artifact_rows = ['| Field | Value |', '| --- | --- |']
    available_names = closed_beta_rc_artifact.get('available_names')
    if not isinstance(available_names, list):
        available_names = []
    artifact_rows.extend(
        [
            f"| Status | {closed_beta_rc_artifact.get('status') or 'not-checked'} |",
            f"| Content status | {closed_beta_rc_artifact.get('content_status') or 'not-checked'} |",
            f"| Expected name | {closed_beta_rc_artifact.get('expected_name') or DEFAULT_CLOSED_BETA_RC_ARTIFACT} |",
            f"| Found name | {closed_beta_rc_artifact.get('name') or ''} |",
            f"| URL | {closed_beta_rc_artifact.get('url') or ''} |",
            f"| Run ID | {closed_beta_rc_artifact.get('run_id') or ''} |",
            f"| Size in bytes | {closed_beta_rc_artifact.get('size_in_bytes') or ''} |",
            f"| Expired | {closed_beta_rc_artifact.get('expired') if closed_beta_rc_artifact.get('expired') is not None else ''} |",
            f"| Available artifact names | {', '.join(str(name) for name in available_names if str(name).strip())} |",
            f"| Required content globs | {', '.join(str(pattern) for pattern in closed_beta_rc_artifact.get('content_required_globs') or [])} |",
            f"| Missing content globs | {', '.join(str(pattern) for pattern in closed_beta_rc_artifact.get('content_missing_globs') or [])} |",
            f"| Matched content paths | {', '.join(str(path) for path in closed_beta_rc_artifact.get('content_matched_paths') or [])} |",
            f"| Content file count | {closed_beta_rc_artifact.get('content_file_count') or ''} |",
            f"| Error | {closed_beta_rc_artifact.get('error') or ''} |",
            f"| Content error | {closed_beta_rc_artifact.get('content_error') or ''} |",
            f"| Deferred reason | {closed_beta_rc_artifact.get('deferred_reason') or ''} |",
        ]
    )

    return '\n'.join(
        [
            '# GitHub Actions Evidence',
            '',
            f"- Status: {evidence.get('status') or 'unknown'}",
            f"- Generated: {evidence.get('generated_at') or 'unknown'}",
            f"- Repository: {evidence.get('repository') or 'unknown'}",
            f"- Commit: {evidence.get('commit') or 'unknown'}",
            (
                f"- Worktree: {(evidence.get('worktree') or {}).get('state') or 'not checked'}; "
                f"{(evidence.get('worktree') or {}).get('summary') or 'not checked'}"
            ),
            f"- Auto gh discovery: {evidence.get('auto_gh')}",
            f"- Include gh details: {evidence.get('include_gh_details')}",
            f"- AIDM CI workflow: `{evidence.get('ci_workflow') or DEFAULT_CI_WORKFLOW}`",
            f"- Closed Beta RC workflow: `{evidence.get('closed_beta_rc_workflow') or DEFAULT_CLOSED_BETA_RC_WORKFLOW}`",
            f"- AIDM CI run URL: `{evidence.get('aidm_ci_run_url') or 'missing'}`",
            f"- Closed Beta RC run URL: `{evidence.get('closed_beta_rc_run_url') or 'missing'}`",
            f"- Closed Beta RC artifact status: {closed_beta_rc_artifact.get('status') or 'not-checked'}",
            f"- Closed Beta RC artifact content status: {closed_beta_rc_artifact.get('content_status') or 'not-checked'}",
            f"- Closed Beta RC artifact expected name: `{closed_beta_rc_artifact.get('expected_name') or DEFAULT_CLOSED_BETA_RC_ARTIFACT}`",
            f"- Closed Beta RC artifact name: `{closed_beta_rc_artifact.get('name') or 'missing'}`",
            f"- Closed Beta RC artifact URL: `{closed_beta_rc_artifact.get('url') or 'missing'}`",
            f"- Deferred current-run artifact check: {evidence.get('defer_current_run_artifact_check')}",
            f"- Verify Closed Beta RC artifact contents: {evidence.get('verify_closed_beta_rc_artifact_contents')}",
            '- Missing: ' + (', '.join(missing) if missing else 'None.'),
            '- Validation errors: ' + (', '.join(evidence.get('validation_errors') or []) if evidence.get('validation_errors') else 'None.'),
            '',
            '## Missing Proof Details',
            '',
            *missing_rows,
            '',
            '## Next Actions',
            '',
            *action_rows,
            '',
            '## Workflow Details',
            '',
            *workflow_rows,
            '',
            '## Latest Runs',
            '',
            *run_rows,
            '',
            '## Closed Beta RC Artifact',
            '',
            *artifact_rows,
            '',
            '## GitHub CLI Errors',
            '',
            *error_rows,
            '',
            '## Validation Errors',
            '',
            *validation_rows,
            '',
        ]
    )


def write_reports(
    evidence: dict[str, Any],
    *,
    evidence_report: pathlib.Path,
    json_output: pathlib.Path | None = None,
) -> None:
    report_path = _resolve_repo_path(evidence_report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown(evidence), encoding='utf-8')
    if json_output is not None:
        json_path = _resolve_repo_path(json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Render GitHub Actions run URL evidence for RC issue sign-off.')
    parser.add_argument('--ci-run-url', default='', help='Successful AIDM CI run URL for this commit.')
    parser.add_argument('--closed-beta-rc-run-url', default='', help='Closed Beta RC workflow run URL.')
    parser.add_argument('--commit', default='', help='Commit SHA or short SHA. Defaults to GitHub env or local HEAD.')
    parser.add_argument('--repository', default='', help='GitHub owner/repo. Defaults to GitHub env when present.')
    parser.add_argument('--auto-gh', action='store_true', help='Use gh to discover missing run URLs for the selected commit.')
    parser.add_argument('--include-gh-details', action='store_true', help='Use read-only gh commands to record workflow state and latest runs.')
    parser.add_argument('--gh-executable', default='gh')
    parser.add_argument('--ci-workflow', default=DEFAULT_CI_WORKFLOW)
    parser.add_argument('--closed-beta-rc-workflow', default=DEFAULT_CLOSED_BETA_RC_WORKFLOW)
    parser.add_argument('--closed-beta-rc-artifact-name', default=DEFAULT_CLOSED_BETA_RC_ARTIFACT)
    parser.add_argument(
        '--verify-closed-beta-rc-artifact-contents',
        action='store_true',
        help='Download the Closed Beta RC artifact with gh and verify it contains the required RC evidence files.',
    )
    parser.add_argument('--evidence-report', type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--json-output', type=pathlib.Path, default=None)
    parser.add_argument('--generated-at', default='', help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    evidence = build_evidence(
        ci_run_url=args.ci_run_url,
        closed_beta_rc_run_url=args.closed_beta_rc_run_url,
        commit=args.commit,
        repository=args.repository,
        generated_at=args.generated_at,
        auto_gh=args.auto_gh,
        include_gh_details=args.include_gh_details,
        gh_executable=args.gh_executable,
        ci_workflow=args.ci_workflow,
        closed_beta_rc_workflow=args.closed_beta_rc_workflow,
        closed_beta_rc_artifact_name=args.closed_beta_rc_artifact_name,
        verify_closed_beta_rc_artifact_contents=args.verify_closed_beta_rc_artifact_contents,
        worktree=_git_worktree_state(),
    )
    write_reports(evidence, evidence_report=args.evidence_report, json_output=args.json_output)
    print(
        '[github-actions-evidence] '
        f"{evidence['status']}; evidence written to "
        f"{_relative_or_absolute(_resolve_repo_path(args.evidence_report))}."
    )
    if args.json_output is not None:
        print(f'[github-actions-evidence] JSON written to {_relative_or_absolute(_resolve_repo_path(args.json_output))}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
