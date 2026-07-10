#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from datetime import UTC, datetime
from typing import Any

try:
    from scripts.render_rc_issue_evidence import (
        DEFAULT_EVIDENCE_REPORT,
        DEFAULT_OUTPUT_DIR,
        DEFAULT_BETA_TESTER_ONBOARDING,
        DEFAULT_EXPORT_IMPORT_EVIDENCE,
        DEFAULT_GITHUB_ACTIONS_EVIDENCE,
        DEFAULT_VISUAL_SMOKE_REVIEW,
        ISSUE_SPECS,
        inspect_beta_tester_onboarding,
        inspect_export_import_evidence,
        inspect_github_actions_evidence,
        inspect_source_archive,
        inspect_visual_smoke,
        inspect_visual_smoke_review,
        load_evidence,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised when run as a script path
    from render_rc_issue_evidence import (  # type: ignore[no-redef]
        DEFAULT_EVIDENCE_REPORT,
        DEFAULT_OUTPUT_DIR,
        DEFAULT_BETA_TESTER_ONBOARDING,
        DEFAULT_EXPORT_IMPORT_EVIDENCE,
        DEFAULT_GITHUB_ACTIONS_EVIDENCE,
        DEFAULT_VISUAL_SMOKE_REVIEW,
        ISSUE_SPECS,
        inspect_beta_tester_onboarding,
        inspect_export_import_evidence,
        inspect_github_actions_evidence,
        inspect_source_archive,
        inspect_visual_smoke,
        inspect_visual_smoke_review,
        load_evidence,
    )


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DEPLOYMENT_READINESS_EVIDENCE = REPO_ROOT / 'tmp' / 'release' / 'deployment-readiness-evidence.md'
DEFAULT_BETA_SLO_BASELINE = REPO_ROOT / 'tmp' / 'release' / 'beta-slo-baseline.md'
DEFAULT_FRONTEND_NPM_CI_EVIDENCE = REPO_ROOT / 'tmp' / 'release' / 'frontend-npm-ci-evidence.md'
DEFAULT_PACKAGING_CLEANUP_EVIDENCE = REPO_ROOT / 'tmp' / 'release' / 'packaging-cleanup-evidence.md'
DEFAULT_RC_ISSUE_CLOSURE_EVIDENCE = REPO_ROOT / 'tmp' / 'release' / 'rc-issue-closure-evidence.md'
DEFAULT_HOSTED_COOKIE_AUTH_EVIDENCE = REPO_ROOT / 'tmp' / 'release' / 'hosted-cookie-auth-evidence.md'
DEFAULT_SECURITY_FORBIDDEN_EVIDENCE = REPO_ROOT / 'tmp' / 'release' / 'security-forbidden-evidence.md'
DEFAULT_HOSTED_RC_EVIDENCE = REPO_ROOT / 'tmp' / 'release' / 'hosted-rc-evidence.md'
DEFAULT_OPERATOR_SIGNOFF_STATUS = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff-status.md'
DEFAULT_OPERATOR_SIGNOFF_DRAFT = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff.draft.json'
DEFAULT_OPERATOR_SIGNOFF_ACTION_PLAN = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff-action-plan.md'
DEFAULT_OPERATOR_SIGNOFF_FROM_INPUTS_STATUS = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff.from-inputs-status.md'
DEFAULT_RECOMMENDATION_MATRIX = REPO_ROOT / 'tmp' / 'release' / 'rc-recommendation-matrix.md'
DEFAULT_EXTERNAL_PROOF_INPUTS = REPO_ROOT / 'tmp' / 'release' / 'external-proof-inputs.md'
DEFAULT_EXTERNAL_PROOF_EXECUTION_PLAN = REPO_ROOT / 'tmp' / 'release' / 'external-proof-execution-plan.md'
DEFAULT_EXTERNAL_PROOF_VALUES_TEMPLATE = REPO_ROOT / 'tmp' / 'release' / 'external-proof-values.example.json'
DEFAULT_EXTERNAL_PROOF_VALUES_STATUS = REPO_ROOT / 'tmp' / 'release' / 'external-proof-values-status.md'
DEFAULT_RELEASE_ARTIFACT_CONSISTENCY = REPO_ROOT / 'tmp' / 'release' / 'release-artifact-consistency.md'
DEFAULT_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'release-evidence-packet.md'


def _resolve_repo_path(path: pathlib.Path) -> pathlib.Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _relative_or_absolute(path: str) -> str:
    if not path:
        return ''
    candidate = pathlib.Path(path)
    try:
        return str(candidate.relative_to(REPO_ROOT))
    except ValueError:
        return str(candidate)


def _strip_ticks(value: str) -> str:
    value = value.strip()
    if value.startswith('`') and value.endswith('`'):
        return value[1:-1]
    return value


def _file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
    except OSError:
        return ''
    return digest.hexdigest()


def _path_mtime(path: pathlib.Path | None) -> float | None:
    if path is None:
        return None
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _artifact_path_from_status(artifact: dict[str, Any], fallback: pathlib.Path | None) -> pathlib.Path | None:
    path = str(artifact.get('path') or '').strip()
    return pathlib.Path(path) if path else fallback


def _with_freshness_against_rc(
    artifact: dict[str, Any],
    *,
    artifact_path: pathlib.Path | None,
    rc_evidence_path: pathlib.Path,
    label: str,
) -> dict[str, Any]:
    result = dict(artifact)
    result['freshness_reference'] = str(rc_evidence_path)
    result['freshness'] = 'unknown'
    artifact_mtime = _path_mtime(artifact_path)
    rc_mtime = _path_mtime(rc_evidence_path)
    if artifact_mtime is None or rc_mtime is None:
        return result
    result['mtime'] = artifact_mtime
    result['rc_evidence_mtime'] = rc_mtime
    if artifact_mtime < rc_mtime:
        previous_status = str(result.get('status') or '')
        result['freshness'] = 'stale'
        result['previous_status'] = previous_status
        if previous_status not in {'missing', 'incomplete', 'failed', 'invalid'}:
            result['status'] = 'stale'
        result['stale_reason'] = f'{label} is older than the RC evidence report'
    else:
        result['freshness'] = 'current'
    return result


def _metadata_key(label: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', label.strip().lower()).strip('_')


def parse_markdown_metadata(path: pathlib.Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if not path.exists():
        return metadata
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line.startswith('- ') or ':' not in line:
            continue
        label, value = line[2:].split(':', 1)
        metadata[_metadata_key(label)] = _strip_ticks(value.strip())
    return metadata


def _safe_load_rc_evidence(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {'status': 'missing', 'commands': []}
    try:
        return load_evidence(path)
    except Exception as exc:
        return {'status': 'invalid', 'commands': [], 'error': str(exc)}


def _status_from_rc(evidence: dict[str, Any]) -> str:
    if evidence.get('status') in {'passed', 'failed', 'missing', 'invalid'}:
        return str(evidence['status'])
    commands = evidence.get('commands') or []
    if not commands:
        return 'missing'
    failed = [command for command in commands if command.get('status') != 'passed']
    return 'failed' if failed else 'passed'


def _inspect_signed_off_worktree(rc_evidence: dict[str, Any]) -> dict[str, Any]:
    git_worktree = rc_evidence.get('git_worktree') or {}
    worktree_label = str(
        rc_evidence.get('worktree') or (git_worktree.get('state') if isinstance(git_worktree, dict) else '') or ''
    )
    dirty = git_worktree.get('dirty') if isinstance(git_worktree, dict) else None
    state = str(git_worktree.get('state') or '') if isinstance(git_worktree, dict) else ''
    if dirty is False or state == 'clean' or worktree_label == 'clean':
        status = 'passed'
    elif dirty is True or state == 'dirty' or worktree_label.startswith('dirty'):
        status = 'dirty'
    else:
        status = 'unknown'
    return {
        'status': status,
        'worktree': worktree_label or state or 'unknown',
        'commit': rc_evidence.get('commit') or '',
    }


def _inspect_issue_evidence(issue_dir: pathlib.Path) -> dict[str, Any]:
    expected = len(ISSUE_SPECS)
    files = sorted(issue_dir.glob('issue-*.md')) if issue_dir.exists() else []
    exceptions: list[dict[str, str]] = []
    for path in files:
        title = path.stem
        issue = path.stem
        first_exception_line = ''
        for raw_line in path.read_text(encoding='utf-8').splitlines():
            line = raw_line.strip()
            if line.startswith('# '):
                title = line.removeprefix('# ').strip()
            elif line.startswith('- Issue:'):
                issue = line.removeprefix('- Issue:').strip()
            elif line.startswith('- Remaining exceptions:') and not first_exception_line:
                first_exception_line = line.removeprefix('- Remaining exceptions:').strip()
        if first_exception_line and first_exception_line != 'None.':
            for exception in (part.strip() for part in first_exception_line.split(';')):
                if exception:
                    exceptions.append({'issue': issue, 'title': title, 'exception': exception})

    if len(files) < expected:
        status = 'incomplete'
    elif exceptions:
        status = 'passed with external exceptions'
    else:
        status = 'passed'
    return {
        'status': status,
        'path': str(issue_dir),
        'file_count': len(files),
        'expected_file_count': expected,
        'external_exceptions': exceptions,
    }


def _inspect_rc_issue_closure_evidence(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {'status': 'missing', 'path': str(path), 'complete': '', 'open_issues': ''}
    metadata = parse_markdown_metadata(path)
    return {
        'status': metadata.get('status') or 'present',
        'path': str(path),
        'complete': metadata.get('issues_complete') or '',
        'open_issues': metadata.get('open_issues') or '',
        'matching_comments': metadata.get('matching_evidence_comments') or '',
        'remaining_exceptions': metadata.get('local_snippets_with_remaining_exceptions') or '',
        'metadata': metadata,
    }


def _inspect_deployment_readiness(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {'status': 'missing', 'path': str(path), 'target_url': ''}
    metadata = parse_markdown_metadata(path)
    target_url = metadata.get('target_url') or ''
    status = metadata.get('status') or 'present'
    if status == 'passed' and target_url == 'not checked':
        status = 'env-only'
    return {'status': status, 'path': str(path), 'target_url': target_url, 'metadata': metadata}


def _inspect_beta_slo_baseline(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {'status': 'missing', 'path': str(path), 'target_url': ''}
    metadata = parse_markdown_metadata(path)
    target_url = metadata.get('target_url') or ''
    if target_url == 'isolated local runtime':
        status = 'local-only'
    else:
        status = 'present' if target_url and '<target-url>' not in target_url else 'incomplete'
    return {'status': status, 'path': str(path), 'target_url': target_url, 'metadata': metadata}


def _inspect_security_forbidden_evidence(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {'status': 'missing', 'path': str(path), 'mode': '', 'target_url': ''}
    metadata = parse_markdown_metadata(path)
    return {
        'status': metadata.get('status') or 'present',
        'path': str(path),
        'mode': metadata.get('mode') or '',
        'target_url': metadata.get('target_url') or '',
        'metadata': metadata,
    }


def _inspect_frontend_npm_ci_evidence(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {'status': 'missing', 'path': str(path), 'command': '', 'return_code': ''}
    metadata = parse_markdown_metadata(path)
    return {
        'status': metadata.get('status') or 'present',
        'path': str(path),
        'command': metadata.get('command') or '',
        'return_code': metadata.get('return_code') or '',
        'duration_seconds': metadata.get('duration_seconds') or '',
        'metadata': metadata,
    }


def _inspect_packaging_cleanup_evidence(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {
            'status': 'missing',
            'path': str(path),
            'source_archive_status': '',
            'forbidden_paths': '',
            'large_files': '',
            'large_files_not_lfs_tracked': '',
        }
    metadata = parse_markdown_metadata(path)
    return {
        'status': metadata.get('status') or 'present',
        'path': str(path),
        'source_archive_status': metadata.get('source_archive_status') or '',
        'forbidden_paths': metadata.get('source_archive_forbidden_paths') or '',
        'large_files': metadata.get('source_archive_large_files') or '',
        'large_files_not_lfs_tracked': metadata.get('source_archive_large_files_not_lfs_tracked') or '',
        'metadata': metadata,
    }


def _inspect_hosted_cookie_auth_evidence(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {'status': 'missing', 'path': str(path), 'mode': '', 'target_url': ''}
    metadata = parse_markdown_metadata(path)
    return {
        'status': metadata.get('status') or 'present',
        'path': str(path),
        'mode': metadata.get('mode') or '',
        'target_url': metadata.get('target_url') or '',
        'metadata': metadata,
    }


def _inspect_hosted_rc_evidence(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {
            'status': 'missing',
            'path': str(path),
            'json_path': str(path.with_suffix('.json')),
            'generator_freshness': 'missing',
            'generator_sha256': '',
            'current_generator_sha256': _file_sha256(REPO_ROOT / 'scripts' / 'hosted_rc_evidence_check.py'),
            'command_plan_sha256': '',
            'target_url': '',
            'check_count': 0,
            'manual_required_count': 0,
            'manual_provided_count': 0,
            'manual_required': [],
            'manual_evidence': [],
            'checks': {},
        }
    metadata = parse_markdown_metadata(path)
    json_path = path.with_suffix('.json')
    json_payload: dict[str, Any] = {}
    json_error = ''
    if json_path.exists():
        try:
            loaded_json = json.loads(json_path.read_text(encoding='utf-8'))
            if isinstance(loaded_json, dict):
                json_payload = loaded_json
            else:
                json_error = 'JSON sidecar must contain an object'
        except json.JSONDecodeError as exc:
            json_error = str(exc)
    generator = json_payload.get('generator') if isinstance(json_payload.get('generator'), dict) else {}
    generator_sha256 = (
        str(generator.get('sha256') or '').strip()
        or metadata.get('generator_sha256')
        or str(json_payload.get('generator_sha256') or '').strip()
    )
    current_generator_sha256 = _file_sha256(REPO_ROOT / 'scripts' / 'hosted_rc_evidence_check.py')
    if json_error:
        generator_freshness = 'invalid'
    elif generator_sha256 and current_generator_sha256:
        generator_freshness = 'current' if generator_sha256 == current_generator_sha256 else 'stale'
    elif generator_sha256:
        generator_freshness = 'unknown'
    else:
        generator_freshness = 'missing'
    status = metadata.get('status') or 'present'
    if generator_freshness == 'stale':
        status = 'stale'
    elif generator_freshness == 'invalid':
        status = 'invalid'
    check_count = 0
    manual_required_count = 0
    manual_provided_count = 0
    manual_required: list[str] = []
    manual_evidence: list[dict[str, str]] = []
    checks: dict[str, dict[str, Any]] = {}
    raw_json_checks = json_payload.get('checks') if isinstance(json_payload.get('checks'), list) else []
    for item in raw_json_checks:
        if not isinstance(item, dict):
            continue
        label = str(item.get('label') or '').strip()
        if not label:
            continue
        checks[label] = {
            'status': str(item.get('status') or '').strip(),
            'exit': '' if item.get('returncode') is None else str(item.get('returncode')),
            'evidence_path': str(item.get('evidence_path') or '').strip(),
            'evidence_target_url': str(item.get('evidence_target_url') or '').strip(),
            'missing_inputs': [str(value) for value in item.get('missing_inputs') or []],
            'validation_errors': [str(value) for value in item.get('validation_errors') or []],
        }
    in_automated_checks = False
    in_manual_evidence = False
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if line == '## Automated Checks':
            in_automated_checks = True
            in_manual_evidence = False
            continue
        if line == '## Manual Evidence Still Required':
            in_automated_checks = False
            in_manual_evidence = True
            continue
        if in_automated_checks and line.startswith('## '):
            in_automated_checks = False
        if in_manual_evidence and line.startswith('## '):
            in_manual_evidence = False
        if in_automated_checks and line.startswith('| Hosted ') and '|' in line and '| ---' not in line:
            check_count += 1
            columns = [column.strip() for column in line.strip('|').split('|')]
            if len(columns) >= 5:
                label, check_status, exit_code, evidence_path = columns[:4]
                evidence_target_url = ''
                missing_inputs = columns[4]
                validation_errors = ''
                if len(columns) >= 7:
                    evidence_target_url = columns[4]
                    missing_inputs = columns[5]
                    validation_errors = columns[6]
                checks.setdefault(
                    label,
                    {
                        'status': check_status,
                        'exit': exit_code,
                        'evidence_path': _strip_ticks(evidence_path),
                        'evidence_target_url': _strip_ticks(evidence_target_url),
                        'missing_inputs': [item.strip() for item in missing_inputs.split(',') if item.strip()],
                        'validation_errors': [item.strip() for item in validation_errors.split(';') if item.strip()],
                    },
                )
        if in_manual_evidence and line.startswith('|') and '| ---' not in line and '| Evidence |' not in line:
            columns = [column.strip() for column in line.strip('|').split('|')]
            label = columns[0] if columns else ''
            manual_status = columns[1] if len(columns) > 1 else ''
            evidence = columns[2] if len(columns) > 2 else ''
            manual_evidence.append({'label': label, 'status': manual_status, 'evidence': evidence})
            if manual_status == 'required':
                manual_required_count += 1
                if label:
                    manual_required.append(label)
            elif manual_status == 'provided':
                manual_provided_count += 1
    if any(
        check.get('status') == 'invalid-evidence' or check.get('validation_errors')
        for check in checks.values()
    ):
        status = 'invalid-evidence'
    elif any(check.get('status') in {'failed', 'invalid'} for check in checks.values()):
        status = 'failed'
    return {
        'status': status,
        'path': str(path),
        'json_path': str(json_path),
        'json_status': 'invalid' if json_error else ('present' if json_path.exists() else 'missing'),
        'json_error': json_error,
        'generator_freshness': generator_freshness,
        'generator_sha256': generator_sha256,
        'current_generator_sha256': current_generator_sha256,
        'command_plan_sha256': (
            str(json_payload.get('command_plan_sha256') or '').strip() or metadata.get('command_plan_sha256') or ''
        ),
        'target_url': metadata.get('target_url') or '',
        'check_count': check_count or len(checks),
        'checks': checks,
        'manual_required_count': manual_required_count,
        'manual_provided_count': manual_provided_count,
        'manual_required': manual_required,
        'manual_evidence': manual_evidence,
        'metadata': metadata,
    }


def _inspect_operator_signoff_status(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {
            'status': 'missing',
            'path': str(path),
            'manifest': '',
            'required_complete': '',
            'missing_or_invalid': '',
            'source_archive_sha256': '',
        }
    metadata = parse_markdown_metadata(path)
    return {
        'status': metadata.get('status') or 'present',
        'path': str(path),
        'manifest': metadata.get('manifest') or '',
        'required_complete': metadata.get('required_complete') or '',
        'missing_or_invalid': metadata.get('missing_or_invalid_required_items') or '',
        'source_archive_sha256': metadata.get('source_archive_sha256') or '',
        'metadata': metadata,
    }


def _inspect_operator_signoff_draft(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return {
            'status': 'not checked',
            'path': '',
            'provided_count': 0,
            'pending_count': 0,
            'not_applicable_count': 0,
        }
    if not path.exists():
        return {
            'status': 'missing',
            'path': str(path),
            'provided_count': 0,
            'pending_count': 0,
            'not_applicable_count': 0,
        }
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return {
            'status': 'invalid',
            'path': str(path),
            'provided_count': 0,
            'pending_count': 0,
            'not_applicable_count': 0,
            'error': str(exc),
        }
    items = payload.get('items') if isinstance(payload, dict) else None
    if not isinstance(items, dict):
        return {
            'status': 'invalid',
            'path': str(path),
            'provided_count': 0,
            'pending_count': 0,
            'not_applicable_count': 0,
            'error': 'items must be a JSON object',
        }
    statuses = [str(item.get('status') or 'pending') for item in items.values() if isinstance(item, dict)]
    return {
        'status': 'present',
        'path': str(path),
        'provided_count': statuses.count('provided'),
        'pending_count': statuses.count('pending'),
        'not_applicable_count': statuses.count('not_applicable'),
        'generated': payload.get('draft_generated_at') or '',
        'commit': payload.get('commit') or '',
        'target_url': payload.get('target_url') or '',
    }


def _inspect_operator_signoff_action_plan(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return {'status': 'not checked', 'path': '', 'pending_actions': ''}
    if not path.exists():
        return {'status': 'missing', 'path': str(path), 'pending_actions': ''}
    metadata = parse_markdown_metadata(path)
    return {
        'status': metadata.get('status') or 'present',
        'path': str(path),
        'pending_actions': metadata.get('pending_actions') or '',
        'required_complete': metadata.get('required_complete') or '',
        'manifest_source': metadata.get('manifest_source') or '',
        'target_url': metadata.get('target_url') or '',
        'metadata': metadata,
    }


def _inspect_recommendation_matrix(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return {'status': 'not checked', 'path': '', 'summary': ''}
    if not path.exists():
        return {'status': 'missing', 'path': str(path), 'summary': ''}
    metadata = parse_markdown_metadata(path)
    summary: list[str] = []
    in_summary = False
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if line == '## Summary':
            in_summary = True
            continue
        if in_summary and line.startswith('## '):
            break
        if in_summary and line.startswith('|') and '| ---' not in line and '| Status |' not in line:
            columns = [column.strip() for column in line.strip('|').split('|')]
            if len(columns) >= 2:
                summary.append(f'{columns[0]}: {columns[1]}')
    return {
        'status': metadata.get('status') or 'present',
        'path': str(path),
        'summary': ', '.join(summary),
        'metadata': metadata,
    }


def _inspect_external_proof_inputs(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return {'status': 'not checked', 'path': '', 'required_fields': '', 'conditional_fields': ''}
    if not path.exists():
        return {'status': 'missing', 'path': str(path), 'required_fields': '', 'conditional_fields': ''}
    metadata = parse_markdown_metadata(path)
    return {
        'status': metadata.get('status') or 'present',
        'path': str(path),
        'required_fields': metadata.get('required_fields') or '',
        'conditional_fields': metadata.get('conditional_fields') or '',
        'provided_context_fields': metadata.get('provided_context_fields') or '',
        'external_recommendation_keys': metadata.get('external_recommendation_keys') or '',
        'metadata': metadata,
    }


def _inspect_external_proof_execution_plan(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return {
            'status': 'not checked',
            'path': '',
            'pending_actions': '',
            'required_fields': '',
            'external_checklist_rows': '',
        }
    if not path.exists():
        return {
            'status': 'missing',
            'path': str(path),
            'pending_actions': '',
            'required_fields': '',
            'external_checklist_rows': '',
        }
    metadata = parse_markdown_metadata(path)
    return {
        'status': metadata.get('status') or 'present',
        'path': str(path),
        'pending_actions': metadata.get('pending_actions') or '',
        'required_fields': metadata.get('required_fields') or '',
        'conditional_fields': metadata.get('conditional_fields') or '',
        'external_checklist_rows': metadata.get('external_checklist_rows') or '',
        'metadata': metadata,
    }


def _inspect_external_proof_values_template(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return {'status': 'not checked', 'path': '', 'field_count': 0}
    if not path.exists():
        return {'status': 'missing', 'path': str(path), 'field_count': 0}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return {'status': 'invalid', 'path': str(path), 'field_count': 0, 'error': str(exc)}
    values = payload.get('values') if isinstance(payload, dict) and isinstance(payload.get('values'), dict) else {}
    return {'status': 'present', 'path': str(path), 'field_count': len(values)}


def _inspect_external_proof_values_status(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return {
            'status': 'not checked',
            'path': '',
            'values_file_present': '',
            'required_complete': '',
            'missing_required_fields': '',
            'metadata_errors': '',
            'invalid_errors': '',
        }
    if not path.exists():
        return {
            'status': 'missing',
            'path': str(path),
            'values_file_present': '',
            'required_complete': '',
            'missing_required_fields': '',
            'metadata_errors': '',
            'invalid_errors': '',
        }
    metadata = parse_markdown_metadata(path)
    return {
        'status': metadata.get('status') or 'present',
        'path': str(path),
        'values_file': metadata.get('values_file') or '',
        'values_file_present': metadata.get('values_file_present') or '',
        'external_inputs': metadata.get('external_inputs') or '',
        'external_inputs_status': metadata.get('external_inputs_status') or '',
        'required_complete': metadata.get('required_complete') or '',
        'missing_required_fields': metadata.get('missing_required_fields') or '',
        'metadata_errors': metadata.get('metadata_errors') or '',
        'invalid_errors': metadata.get('invalid_errors') or '',
        'metadata': metadata,
    }


def _inspect_release_artifact_consistency(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return {
            'status': 'not checked',
            'path': '',
            'json_path': '',
            'source_archive_sha256': '',
            'check_count': 0,
            'error_count': 0,
        }
    if not path.exists():
        return {
            'status': 'missing',
            'path': str(path),
            'json_path': str(path.with_suffix('.json')),
            'source_archive_sha256': '',
            'check_count': 0,
            'error_count': 0,
        }
    metadata = parse_markdown_metadata(path)
    json_path = path.with_suffix('.json')
    check_count = 0
    error_count = 0
    json_status = 'missing'
    json_error = ''
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            json_status = 'invalid'
            json_error = str(exc)
        else:
            if isinstance(payload, dict):
                json_status = 'present'
                checks = payload.get('checks') if isinstance(payload.get('checks'), list) else []
                errors = payload.get('errors') if isinstance(payload.get('errors'), list) else []
                check_count = len(checks)
                error_count = len(errors)
            else:
                json_status = 'invalid'
                json_error = 'JSON sidecar must contain an object'
    status = metadata.get('status') or 'present'
    if json_status == 'invalid':
        status = 'invalid'
    elif error_count:
        status = 'failed'
    return {
        'status': status,
        'path': str(path),
        'json_path': str(json_path),
        'json_status': json_status,
        'json_error': json_error,
        'source_archive_sha256': metadata.get('source_archive_sha256') or '',
        'check_count': check_count,
        'error_count': error_count,
        'metadata': metadata,
    }


def _artifact_evidence_path(path: str) -> str:
    relative = _relative_or_absolute(path)
    return f'`{relative}`' if relative else ''


def _hosted_signoff_checklist(packet: dict[str, Any]) -> list[dict[str, str]]:
    source_archive_path = _relative_or_absolute(packet.get('source_archive', {}).get('path') or '')
    visual_smoke_path = _relative_or_absolute(packet.get('visual_smoke', {}).get('path') or '')
    visual_review_path = _relative_or_absolute(packet.get('visual_smoke_review', {}).get('path') or '')
    github_actions_path = _relative_or_absolute(packet.get('github_actions', {}).get('path') or '')
    return [
        {
            'issues': '#3',
            'evidence': 'GitHub Actions CI and Closed Beta RC run URLs',
            'command': 'Run GitHub Actions `AIDM CI` and manual `Closed Beta RC` without skips.',
            'artifact': f'`{github_actions_path}` and `closed-beta-rc-evidence` artifact' if github_actions_path else '`closed-beta-rc-evidence` artifact and run URLs',
        },
        {
            'issues': '#3 #5 #8',
            'evidence': 'Hosted/staging deployment readiness',
            'command': (
                'make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> '
                '--target-url <target-url> --auth-token <token> '
                '--evidence-report tmp/release/deployment-readiness-evidence.md"'
            ),
            'artifact': '`tmp/release/deployment-readiness-evidence.md`',
        },
        {
            'issues': '#4',
            'evidence': 'Visual-smoke screenshot review',
            'command': 'Run `make visual-smoke-review` after visual smoke and inspect any failed screenshot checks.',
            'artifact': (
                f'`{visual_review_path}` and `{visual_smoke_path}`'
                if visual_review_path and visual_smoke_path
                else '`tmp/release/visual-smoke-review.md` and `tmp/verification_artifacts/visual-smoke/<run>/`'
            ),
        },
        {
            'issues': '#5 #7',
            'evidence': 'Hosted cookie-auth proof',
            'command': (
                'make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_SMOKE_ARGS="--target-url <target-url> '
                '--account-intent signup --evidence-report tmp/release/hosted-cookie-auth-evidence.md"'
            ),
            'artifact': '`tmp/release/hosted-cookie-auth-evidence.md`',
        },
        {
            'issues': '#5',
            'evidence': 'Hosted non-admin forbidden-response proof',
            'command': (
                'make security-forbidden-smoke SECURITY_FORBIDDEN_SMOKE_ARGS="--target-url <target-url> '
                '--account-token <non-admin-account-token> --workspace-id <workspace-id> '
                '--campaign-id <campaign-id> --session-id <session-id> '
                '--evidence-report tmp/release/security-forbidden-evidence.md"'
            ),
            'artifact': '`tmp/release/security-forbidden-evidence.md`',
        },
        {
            'issues': '#6',
            'evidence': 'Hosted database backup/restore proof',
            'command': 'Run the provider-specific staging backup/restore drill and attach restore logs or managed-provider evidence.',
            'artifact': 'provider restore artifact or runbook link',
        },
        {
            'issues': '#6',
            'evidence': 'Hosted export/import smoke',
            'command': (
                'make session-export-import-smoke SESSION_EXPORT_IMPORT_SMOKE_ARGS="--target-url <target-url> '
                '--auth-token <token> --workspace-id <workspace-id> --session-id <session-id> '
                '--player-id <player-id> --evidence-report tmp/release/export-import-evidence.md"'
            ),
            'artifact': '`tmp/release/export-import-evidence.md`',
        },
        {
            'issues': '#7',
            'evidence': 'Hosted Socket.IO worker-model process proof',
            'command': 'Attach platform process/config evidence showing the documented worker model and exactly one backend worker for RC1 single-worker mode.',
            'artifact': 'platform process screenshot/log/config link',
        },
        {
            'issues': '#7',
            'evidence': 'Single-worker-only Socket.IO topology enforcement',
            'command': (
                'Confirm `AIDM_SOCKETIO_WORKER_MODEL=single`, `WEB_CONCURRENCY=1`, and exactly one '
                'backend process/replica. Multi-worker staging proof is future-only and cannot satisfy RC1.'
            ),
            'artifact': 'platform process screenshot/log/config link',
        },
        {
            'issues': '#8',
            'evidence': 'Hosted beta SLO baseline',
            'command': (
                'make beta-slo-baseline BETA_SLO_BASELINE_ARGS="--target-url <target-url> '
                '--auth-token <token> --workspace-id <workspace-id> --release RC1 --environment staging '
                '--output tmp/release/beta-slo-baseline.md"'
            ),
            'artifact': '`tmp/release/beta-slo-baseline.md` and raw target JSON if saved',
        },
        {
            'issues': '#9',
            'evidence': 'Source archive attached to RC issue or release',
            'command': 'Attach the source archive produced by `make source-archive`.',
            'artifact': f'`{source_archive_path}`' if source_archive_path else '`tmp/release/aidm-source-*.tar.gz`',
        },
    ]


def _has_status(items: list[str | None], statuses: set[str]) -> bool:
    return any((status or '') in statuses for status in items)


def _included_label(value: Any) -> str:
    if value is True:
        return 'included'
    if value is False:
        return 'skipped'
    return 'unknown'


def build_packet(
    *,
    generated_at: str,
    rc_evidence_path: pathlib.Path,
    issue_evidence_dir: pathlib.Path,
    source_archive_path: pathlib.Path | None,
    visual_smoke_dir: pathlib.Path | None,
    visual_smoke_review_path: pathlib.Path | None,
    security_forbidden_evidence_path: pathlib.Path,
    export_import_evidence_path: pathlib.Path,
    deployment_readiness_evidence_path: pathlib.Path,
    beta_slo_baseline_path: pathlib.Path,
    rc_issue_closure_evidence_path: pathlib.Path | None = None,
    frontend_npm_ci_evidence_path: pathlib.Path | None = None,
    packaging_cleanup_evidence_path: pathlib.Path | None = None,
    github_actions_evidence_path: pathlib.Path | None = None,
    hosted_cookie_auth_evidence_path: pathlib.Path | None = None,
    hosted_rc_evidence_path: pathlib.Path | None = None,
    operator_signoff_status_path: pathlib.Path | None = None,
    operator_signoff_draft_path: pathlib.Path | None = None,
    operator_signoff_action_plan_path: pathlib.Path | None = None,
    operator_signoff_from_inputs_status_path: pathlib.Path | None = None,
    recommendation_matrix_path: pathlib.Path | None = None,
    external_proof_inputs_path: pathlib.Path | None = None,
    external_proof_execution_plan_path: pathlib.Path | None = None,
    external_proof_values_template_path: pathlib.Path | None = None,
    external_proof_values_status_path: pathlib.Path | None = None,
    release_artifact_consistency_path: pathlib.Path | None = None,
    beta_tester_onboarding_path: pathlib.Path | None = None,
) -> dict[str, Any]:
    rc_evidence_path = _resolve_repo_path(rc_evidence_path)
    issue_evidence_dir = _resolve_repo_path(issue_evidence_dir)
    hosted_cookie_auth_evidence_path = _resolve_repo_path(
        hosted_cookie_auth_evidence_path or DEFAULT_HOSTED_COOKIE_AUTH_EVIDENCE
    )
    hosted_rc_evidence_path = _resolve_repo_path(hosted_rc_evidence_path or DEFAULT_HOSTED_RC_EVIDENCE)
    operator_signoff_status_path = _resolve_repo_path(operator_signoff_status_path or DEFAULT_OPERATOR_SIGNOFF_STATUS)
    if operator_signoff_draft_path is not None:
        operator_signoff_draft_path = _resolve_repo_path(operator_signoff_draft_path)
    if operator_signoff_action_plan_path is not None:
        operator_signoff_action_plan_path = _resolve_repo_path(operator_signoff_action_plan_path)
    if operator_signoff_from_inputs_status_path is not None:
        operator_signoff_from_inputs_status_path = _resolve_repo_path(operator_signoff_from_inputs_status_path)
    if recommendation_matrix_path is not None:
        recommendation_matrix_path = _resolve_repo_path(recommendation_matrix_path)
    if external_proof_inputs_path is not None:
        external_proof_inputs_path = _resolve_repo_path(external_proof_inputs_path)
    if external_proof_execution_plan_path is not None:
        external_proof_execution_plan_path = _resolve_repo_path(external_proof_execution_plan_path)
    if external_proof_values_template_path is not None:
        external_proof_values_template_path = _resolve_repo_path(external_proof_values_template_path)
    if external_proof_values_status_path is not None:
        external_proof_values_status_path = _resolve_repo_path(external_proof_values_status_path)
    if release_artifact_consistency_path is not None:
        release_artifact_consistency_path = _resolve_repo_path(release_artifact_consistency_path)
    security_forbidden_evidence_path = _resolve_repo_path(security_forbidden_evidence_path)
    export_import_evidence_path = _resolve_repo_path(export_import_evidence_path)
    deployment_readiness_evidence_path = _resolve_repo_path(deployment_readiness_evidence_path)
    beta_slo_baseline_path = _resolve_repo_path(beta_slo_baseline_path)
    rc_issue_closure_evidence_path = _resolve_repo_path(rc_issue_closure_evidence_path or DEFAULT_RC_ISSUE_CLOSURE_EVIDENCE)
    frontend_npm_ci_evidence_path = _resolve_repo_path(frontend_npm_ci_evidence_path or DEFAULT_FRONTEND_NPM_CI_EVIDENCE)
    packaging_cleanup_evidence_path = _resolve_repo_path(packaging_cleanup_evidence_path or DEFAULT_PACKAGING_CLEANUP_EVIDENCE)

    rc_evidence = _safe_load_rc_evidence(rc_evidence_path)
    signed_off_worktree = _inspect_signed_off_worktree(rc_evidence)
    source_archive = inspect_source_archive(source_archive_path)
    source_archive = _with_freshness_against_rc(
        source_archive,
        artifact_path=_artifact_path_from_status(source_archive, source_archive_path),
        rc_evidence_path=rc_evidence_path,
        label='source archive',
    )
    visual_smoke = inspect_visual_smoke(visual_smoke_dir)
    visual_smoke_review = inspect_visual_smoke_review(visual_smoke_review_path or DEFAULT_VISUAL_SMOKE_REVIEW)
    github_actions = inspect_github_actions_evidence(github_actions_evidence_path or DEFAULT_GITHUB_ACTIONS_EVIDENCE)
    github_actions = _with_freshness_against_rc(
        github_actions,
        artifact_path=_artifact_path_from_status(
            github_actions,
            github_actions_evidence_path or DEFAULT_GITHUB_ACTIONS_EVIDENCE,
        ),
        rc_evidence_path=rc_evidence_path,
        label='GitHub Actions evidence',
    )
    issue_evidence = _inspect_issue_evidence(issue_evidence_dir)
    rc_issue_closure_evidence = _inspect_rc_issue_closure_evidence(rc_issue_closure_evidence_path)
    hosted_rc_evidence = _inspect_hosted_rc_evidence(hosted_rc_evidence_path)
    operator_signoff = _inspect_operator_signoff_status(operator_signoff_status_path)
    operator_signoff_draft = _inspect_operator_signoff_draft(operator_signoff_draft_path)
    operator_signoff_action_plan = _inspect_operator_signoff_action_plan(operator_signoff_action_plan_path)
    operator_signoff_from_inputs = _inspect_operator_signoff_status(
        operator_signoff_from_inputs_status_path or DEFAULT_OPERATOR_SIGNOFF_FROM_INPUTS_STATUS
    )
    recommendation_matrix = _inspect_recommendation_matrix(recommendation_matrix_path)
    external_proof_inputs = _inspect_external_proof_inputs(external_proof_inputs_path)
    external_proof_execution_plan = _inspect_external_proof_execution_plan(external_proof_execution_plan_path)
    external_proof_values_template = _inspect_external_proof_values_template(external_proof_values_template_path)
    external_proof_values_status = _inspect_external_proof_values_status(
        external_proof_values_status_path or DEFAULT_EXTERNAL_PROOF_VALUES_STATUS
    )
    release_artifact_consistency = _inspect_release_artifact_consistency(
        release_artifact_consistency_path or DEFAULT_RELEASE_ARTIFACT_CONSISTENCY
    )
    hosted_cookie_auth = _inspect_hosted_cookie_auth_evidence(hosted_cookie_auth_evidence_path)
    security_forbidden = _inspect_security_forbidden_evidence(security_forbidden_evidence_path)
    export_import = inspect_export_import_evidence(export_import_evidence_path)
    deployment_readiness = _inspect_deployment_readiness(deployment_readiness_evidence_path)
    beta_slo_baseline = _inspect_beta_slo_baseline(beta_slo_baseline_path)
    frontend_npm_ci = _inspect_frontend_npm_ci_evidence(frontend_npm_ci_evidence_path)
    frontend_npm_ci = _with_freshness_against_rc(
        frontend_npm_ci,
        artifact_path=frontend_npm_ci_evidence_path,
        rc_evidence_path=rc_evidence_path,
        label='frontend npm ci evidence',
    )
    packaging_cleanup = _inspect_packaging_cleanup_evidence(packaging_cleanup_evidence_path)
    packaging_cleanup = _with_freshness_against_rc(
        packaging_cleanup,
        artifact_path=packaging_cleanup_evidence_path,
        rc_evidence_path=rc_evidence_path,
        label='packaging cleanup evidence',
    )
    beta_tester_onboarding = inspect_beta_tester_onboarding(beta_tester_onboarding_path or DEFAULT_BETA_TESTER_ONBOARDING)

    commands = rc_evidence.get('commands') or []
    failed_commands = [command for command in commands if command.get('status') != 'passed']
    local_artifact_statuses = [
        _status_from_rc(rc_evidence),
        source_archive.get('status'),
        visual_smoke.get('status'),
        visual_smoke_review.get('status'),
        hosted_cookie_auth.get('status'),
        security_forbidden.get('status'),
        export_import.get('status'),
        frontend_npm_ci.get('status'),
        packaging_cleanup.get('status'),
        release_artifact_consistency.get('status'),
        beta_tester_onboarding.get('status'),
        issue_evidence.get('status'),
    ]
    external_evidence_statuses = [
        github_actions.get('status'),
        hosted_rc_evidence.get('status'),
        operator_signoff.get('status'),
        signed_off_worktree.get('status'),
        deployment_readiness.get('status'),
        beta_slo_baseline.get('status'),
    ]
    if (
        _has_status(local_artifact_statuses + external_evidence_statuses, {'failed', 'invalid', 'invalid-evidence'})
        or failed_commands
    ):
        overall_status = 'failed'
    elif _has_status(local_artifact_statuses, {'missing', 'incomplete', 'stale'}):
        overall_status = 'incomplete'
    elif issue_evidence['external_exceptions'] or _has_status(
        external_evidence_statuses,
        {'missing', 'incomplete', 'planned', 'missing-input', 'manual-evidence-required', 'empty', 'env-only', 'local-only'},
    ) or _has_status(
        [hosted_rc_evidence.get('status'), hosted_rc_evidence.get('generator_freshness')],
        {'stale', 'invalid'},
    ) or _has_status(
        external_evidence_statuses,
        {'stale'},
    ) or signed_off_worktree.get('status') in {'dirty', 'unknown'} or int(hosted_rc_evidence.get('manual_required_count') or 0) > 0:
        overall_status = 'local-ready-with-external-exceptions'
    else:
        overall_status = 'ready-for-issue-closure'

    packet = {
        'generated_at': generated_at,
        'repo_root': str(REPO_ROOT),
        'overall_status': overall_status,
        'rc_evidence': {
            'status': _status_from_rc(rc_evidence),
            'path': str(rc_evidence_path),
            'commit': rc_evidence.get('commit') or '',
            'worktree': rc_evidence.get('worktree') or (rc_evidence.get('git_worktree') or {}).get('state') or '',
            'git_worktree': rc_evidence.get('git_worktree') or {},
            'finished_at': rc_evidence.get('finished_at') or '',
            'include_browser_smoke': rc_evidence.get('include_browser_smoke'),
            'include_dependency_audits': rc_evidence.get('include_dependency_audits'),
            'gate_count': len(commands),
            'failed_gate_count': len(failed_commands),
            'commands': commands,
            'error': rc_evidence.get('error') or '',
        },
        'signed_off_worktree': signed_off_worktree,
        'issue_evidence': issue_evidence,
        'rc_issue_closure_evidence': rc_issue_closure_evidence,
        'source_archive': source_archive,
        'visual_smoke': visual_smoke,
        'visual_smoke_review': visual_smoke_review,
        'github_actions': github_actions,
        'hosted_rc_evidence': hosted_rc_evidence,
        'operator_signoff': operator_signoff,
        'operator_signoff_draft': operator_signoff_draft,
        'operator_signoff_action_plan': operator_signoff_action_plan,
        'operator_signoff_from_inputs': operator_signoff_from_inputs,
        'recommendation_matrix': recommendation_matrix,
        'external_proof_inputs': external_proof_inputs,
        'external_proof_execution_plan': external_proof_execution_plan,
        'external_proof_values_template': external_proof_values_template,
        'external_proof_values_status': external_proof_values_status,
        'release_artifact_consistency': release_artifact_consistency,
        'hosted_cookie_auth': hosted_cookie_auth,
        'security_forbidden': security_forbidden,
        'export_import': export_import,
        'deployment_readiness': deployment_readiness,
        'beta_slo_baseline': beta_slo_baseline,
        'frontend_npm_ci': frontend_npm_ci,
        'packaging_cleanup': packaging_cleanup,
        'beta_tester_onboarding': beta_tester_onboarding,
    }
    packet['hosted_signoff_checklist'] = _hosted_signoff_checklist(packet)
    return packet


def render_packet(packet: dict[str, Any]) -> str:
    rc = packet['rc_evidence']
    issue = packet['issue_evidence']
    issue_closure = packet.get('rc_issue_closure_evidence') or {}
    signed_off_worktree = packet['signed_off_worktree']
    source = packet['source_archive']
    visual = packet['visual_smoke']
    visual_review = packet['visual_smoke_review']
    github_actions = packet['github_actions']
    github_actions_artifact = github_actions.get('closed_beta_rc_artifact')
    if not isinstance(github_actions_artifact, dict):
        github_actions_artifact = {}
    hosted_rc = packet['hosted_rc_evidence']
    operator_signoff = packet['operator_signoff']
    operator_signoff_draft = packet.get('operator_signoff_draft') or {}
    operator_signoff_action_plan = packet.get('operator_signoff_action_plan') or {}
    operator_signoff_from_inputs = packet.get('operator_signoff_from_inputs') or {}
    recommendation_matrix = packet.get('recommendation_matrix') or {}
    external_proof_inputs = packet.get('external_proof_inputs') or {}
    external_proof_execution_plan = packet.get('external_proof_execution_plan') or {}
    external_proof_values_template = packet.get('external_proof_values_template') or {}
    external_proof_values_status = packet.get('external_proof_values_status') or {}
    release_artifact_consistency = packet.get('release_artifact_consistency') or {}
    hosted_cookie_auth = packet['hosted_cookie_auth']
    security = packet['security_forbidden']
    export_import = packet['export_import']
    readiness = packet['deployment_readiness']
    slo = packet['beta_slo_baseline']
    frontend_npm_ci = packet.get('frontend_npm_ci') or {}
    packaging_cleanup = packet.get('packaging_cleanup') or {}
    onboarding = packet['beta_tester_onboarding']
    operator_source_archive_sha256 = operator_signoff.get('source_archive_sha256') or ''
    operator_source_archive_note = (
        f'; source archive sha256: {operator_source_archive_sha256}' if operator_source_archive_sha256 else ''
    )

    artifact_rows = [
        '| Artifact | Status | Evidence | Notes |',
        '| --- | --- | --- | --- |',
        (
            f"| Local RC evidence | {rc['status']} | {_artifact_evidence_path(rc['path'])} | "
            f"{rc['gate_count']} gates, {rc['failed_gate_count']} failed; "
            f"browser smoke: {_included_label(rc.get('include_browser_smoke'))}; "
            f"dependency audits: {_included_label(rc.get('include_dependency_audits'))} |"
        ),
        (
            f"| Clean signed-off worktree | {signed_off_worktree.get('status')} | "
            f"{signed_off_worktree.get('commit') or 'unknown'} | "
            f"worktree: {signed_off_worktree.get('worktree') or 'unknown'} |"
        ),
        (
            f"| Issue evidence snippets | {issue['status']} | {_artifact_evidence_path(issue['path'])} | "
            f"{issue['file_count']}/{issue['expected_file_count']} files, {len(issue['external_exceptions'])} external exceptions |"
        ),
        (
            f"| RC issue closure evidence | {issue_closure.get('status')} | "
            f"{_artifact_evidence_path(issue_closure.get('path') or '')} | "
            f"complete: {issue_closure.get('complete') or 'missing'}, "
            f"open issues: {issue_closure.get('open_issues') or 'missing'}, "
            f"matching comments: {issue_closure.get('matching_comments') or 'missing'} |"
        ),
        (
            f"| Source archive | {source.get('status')} | {_artifact_evidence_path(source.get('path') or '')} | "
            f"{len(source.get('forbidden') or [])} forbidden paths"
            f"; large files: {source.get('large_member_count') or 0}"
            f" ({len(source.get('large_untracked') or [])} not LFS-tracked)"
            f"{'; sha256: ' + source.get('sha256') if source.get('sha256') else ''}"
            f"{'; bytes: ' + str(source.get('bytes')) if source.get('bytes') else ''} |"
        ),
        (
            f"| Visual smoke screenshots | {visual.get('status')} | {_artifact_evidence_path(visual.get('path') or '')} | "
            f"{', '.join(visual.get('screenshots') or []) or 'none'} |"
        ),
        (
            f"| Visual smoke review | {visual_review.get('status')} | "
            f"{_artifact_evidence_path(visual_review.get('path') or '')} | "
            f"screenshots: {visual_review.get('screenshots') or 'missing'}, "
            f"failures: {visual_review.get('failures') or 'missing'} |"
        ),
        (
            f"| GitHub Actions evidence | {github_actions.get('status')} | "
            f"{_artifact_evidence_path(github_actions.get('path') or '')} | "
            f"AIDM CI: {github_actions.get('aidm_ci_run_url') or 'missing'}, "
            f"Closed Beta RC: {github_actions.get('closed_beta_rc_run_url') or 'missing'}, "
            f"artifact: {github_actions_artifact.get('status') or github_actions.get('closed_beta_rc_artifact_status') or 'not-checked'}, "
            f"artifact content: {github_actions_artifact.get('content_status') or github_actions.get('closed_beta_rc_artifact_content_status') or 'not-checked'}, "
            f"artifact URL: {github_actions_artifact.get('url') or github_actions.get('closed_beta_rc_artifact_url') or 'missing'}, "
            f"worktree: {github_actions.get('worktree') or 'unknown'} |"
        ),
        (
            f"| Hosted RC evidence | {hosted_rc.get('status')} | "
            f"{_artifact_evidence_path(hosted_rc.get('path') or '')} | "
            f"target URL: {hosted_rc.get('target_url') or 'missing'}, "
            f"checks: {hosted_rc.get('check_count') or 0}, "
            f"manual required: {hosted_rc.get('manual_required_count') or 0}, "
            f"plan freshness: {hosted_rc.get('generator_freshness') or 'missing'} |"
        ),
        (
            f"| Operator sign-off status | {operator_signoff.get('status')} | "
            f"{_artifact_evidence_path(operator_signoff.get('path') or '')} | "
            f"required complete: {operator_signoff.get('required_complete') or 'missing'}, "
            f"missing/invalid: {operator_signoff.get('missing_or_invalid') or 'missing'}"
            f"{operator_source_archive_note} |"
        ),
        (
            f"| Operator sign-off draft | {operator_signoff_draft.get('status')} | "
            f"{_artifact_evidence_path(operator_signoff_draft.get('path') or '')} | "
            f"provided: {operator_signoff_draft.get('provided_count') or 0}, "
            f"pending: {operator_signoff_draft.get('pending_count') or 0}, "
            f"not applicable: {operator_signoff_draft.get('not_applicable_count') or 0} |"
        ),
        (
            f"| Operator sign-off action plan | {operator_signoff_action_plan.get('status')} | "
            f"{_artifact_evidence_path(operator_signoff_action_plan.get('path') or '')} | "
            f"pending actions: {operator_signoff_action_plan.get('pending_actions') or 'missing'} |"
        ),
        (
            f"| Operator sign-off from inputs preview | {operator_signoff_from_inputs.get('status')} | "
            f"{_artifact_evidence_path(operator_signoff_from_inputs.get('path') or '')} | "
            f"required complete: {operator_signoff_from_inputs.get('required_complete') or 'missing'}, "
            f"missing/invalid: {operator_signoff_from_inputs.get('missing_or_invalid') or 'missing'} |"
        ),
        (
            f"| RC recommendation matrix | {recommendation_matrix.get('status')} | "
            f"{_artifact_evidence_path(recommendation_matrix.get('path') or '')} | "
            f"{recommendation_matrix.get('summary') or 'summary missing'} |"
        ),
        (
            f"| External proof inputs | {external_proof_inputs.get('status')} | "
            f"{_artifact_evidence_path(external_proof_inputs.get('path') or '')} | "
            f"required fields: {external_proof_inputs.get('required_fields') or 'missing'}, "
            f"conditional fields: {external_proof_inputs.get('conditional_fields') or 'missing'} |"
        ),
        (
            f"| External proof execution plan | {external_proof_execution_plan.get('status')} | "
            f"{_artifact_evidence_path(external_proof_execution_plan.get('path') or '')} | "
            f"pending actions: {external_proof_execution_plan.get('pending_actions') or 'missing'}, "
            f"required fields: {external_proof_execution_plan.get('required_fields') or 'missing'}, "
            f"external checklist rows: {external_proof_execution_plan.get('external_checklist_rows') or 'missing'} |"
        ),
        (
            f"| External proof values template | {external_proof_values_template.get('status')} | "
            f"{_artifact_evidence_path(external_proof_values_template.get('path') or '')} | "
            f"fields: {external_proof_values_template.get('field_count') or 0} |"
        ),
        (
            f"| External proof values status | {external_proof_values_status.get('status')} | "
            f"{_artifact_evidence_path(external_proof_values_status.get('path') or '')} | "
            f"required complete: {external_proof_values_status.get('required_complete') or 'missing'}, "
            f"missing required: {external_proof_values_status.get('missing_required_fields') or 'missing'}, "
            f"invalid: {external_proof_values_status.get('invalid_errors') or 'missing'} |"
        ),
        (
            f"| Release artifact consistency | {release_artifact_consistency.get('status')} | "
            f"{_artifact_evidence_path(release_artifact_consistency.get('path') or '')} | "
            f"checks: {release_artifact_consistency.get('check_count') or 0}, "
            f"errors: {release_artifact_consistency.get('error_count') or 0}, "
            f"source archive sha256: {release_artifact_consistency.get('source_archive_sha256') or 'missing'} |"
        ),
        (
            f"| Hosted cookie-auth evidence | {hosted_cookie_auth.get('status')} | "
            f"{_artifact_evidence_path(hosted_cookie_auth.get('path') or '')} | "
            f"mode: {hosted_cookie_auth.get('mode') or 'missing'}, target URL: {hosted_cookie_auth.get('target_url') or 'missing'} |"
        ),
        (
            f"| Security forbidden evidence | {security.get('status')} | {_artifact_evidence_path(security.get('path') or '')} | "
            f"mode: {security.get('mode') or 'missing'}, target URL: {security.get('target_url') or 'missing'} |"
        ),
        (
            f"| Session export/import evidence | {export_import.get('status')} | {_artifact_evidence_path(export_import.get('path') or '')} | "
            f"mode: {export_import.get('mode') or 'missing'}, target URL: {export_import.get('target_url') or 'missing'} |"
        ),
        (
            f"| Deployment readiness | {readiness.get('status')} | {_artifact_evidence_path(readiness.get('path') or '')} | "
            f"target URL: {readiness.get('target_url') or 'missing'} |"
        ),
        (
            f"| Beta SLO baseline | {slo.get('status')} | {_artifact_evidence_path(slo.get('path') or '')} | "
            f"target URL: {slo.get('target_url') or 'missing'} |"
        ),
        (
            f"| Frontend npm ci evidence | {frontend_npm_ci.get('status')} | "
            f"{_artifact_evidence_path(frontend_npm_ci.get('path') or '')} | "
            f"command: {frontend_npm_ci.get('command') or 'missing'}, "
            f"return code: {frontend_npm_ci.get('return_code') or 'missing'} |"
        ),
        (
            f"| Packaging cleanup evidence | {packaging_cleanup.get('status')} | "
            f"{_artifact_evidence_path(packaging_cleanup.get('path') or '')} | "
            f"source archive: {packaging_cleanup.get('source_archive_status') or 'missing'}, "
            f"forbidden paths: {packaging_cleanup.get('forbidden_paths') or 'missing'}, "
            f"large files: {packaging_cleanup.get('large_files') or 'missing'}, "
            f"large files not LFS-tracked: {packaging_cleanup.get('large_files_not_lfs_tracked') or 'missing'} |"
        ),
        (
            f"| Beta tester onboarding | {onboarding.get('status')} | "
            f"{_artifact_evidence_path(onboarding.get('path') or '')} | "
            f"linked from: {', '.join(onboarding.get('linked_from') or []) or 'none'} |"
        ),
    ]

    exception_rows = ['| Issue | Gate | Remaining external exception |', '| --- | --- | --- |']
    for exception in issue['external_exceptions']:
        exception_rows.append(f"| {exception['issue']} | {exception['title']} | {exception['exception']} |")
    if not issue['external_exceptions']:
        exception_rows.append('| None | None | None |')

    hosted_rows = ['| Issues | Evidence needed | Command or source | Artifact or field |', '| --- | --- | --- | --- |']
    for item in packet.get('hosted_signoff_checklist') or []:
        hosted_rows.append(
            f"| {item.get('issues')} | {item.get('evidence')} | {item.get('command')} | {item.get('artifact')} |"
        )
    if not packet.get('hosted_signoff_checklist'):
        hosted_rows.append('| None | None | None | None |')

    gate_rows = ['| Gate | Status | Exit | Seconds |', '| --- | --- | ---: | ---: |']
    for command in rc.get('commands') or []:
        gate_rows.append(
            f"| {command.get('label')} | {command.get('status')} | {command.get('returncode')} | "
            f"{command.get('duration_seconds')} |"
        )
    if not rc.get('commands'):
        gate_rows.append('| None | missing |  |  |')

    return '\n'.join(
        [
            '# Release Evidence Packet',
            '',
            f"- Generated: {packet['generated_at']}",
            f"- Repo: `{packet['repo_root']}`",
            f"- Overall status: {packet['overall_status']}",
            f"- RC commit: {rc.get('commit') or 'unknown'}",
            f"- RC worktree: {rc.get('worktree') or 'unknown'}",
            f"- RC finished: {rc.get('finished_at') or 'unknown'}",
            '',
            '## Artifact Summary',
            '',
            *artifact_rows,
            '',
            '## Remaining External Exceptions',
            '',
            *exception_rows,
            '',
            '## Hosted Sign-Off Checklist',
            '',
            *hosted_rows,
            '',
            '## Local RC Gates',
            '',
            *gate_rows,
            '',
        ]
    )


def write_packet(packet: dict[str, Any], output_path: pathlib.Path, json_output_path: pathlib.Path | None) -> None:
    output_path = _resolve_repo_path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_packet(packet), encoding='utf-8')
    if json_output_path is not None:
        json_output_path = _resolve_repo_path(json_output_path)
        json_output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Render a release evidence packet from existing RC artifacts.')
    parser.add_argument('--rc-evidence', type=pathlib.Path, default=DEFAULT_EVIDENCE_REPORT)
    parser.add_argument('--issue-evidence-dir', type=pathlib.Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument('--rc-issue-closure-evidence', type=pathlib.Path, default=DEFAULT_RC_ISSUE_CLOSURE_EVIDENCE)
    parser.add_argument('--source-archive', type=pathlib.Path, default=None)
    parser.add_argument('--visual-smoke-dir', type=pathlib.Path, default=None)
    parser.add_argument('--visual-smoke-review', type=pathlib.Path, default=DEFAULT_VISUAL_SMOKE_REVIEW)
    parser.add_argument('--github-actions-evidence', type=pathlib.Path, default=DEFAULT_GITHUB_ACTIONS_EVIDENCE)
    parser.add_argument('--hosted-cookie-auth-evidence', type=pathlib.Path, default=DEFAULT_HOSTED_COOKIE_AUTH_EVIDENCE)
    parser.add_argument('--hosted-rc-evidence', type=pathlib.Path, default=DEFAULT_HOSTED_RC_EVIDENCE)
    parser.add_argument('--operator-signoff-status', type=pathlib.Path, default=DEFAULT_OPERATOR_SIGNOFF_STATUS)
    parser.add_argument('--operator-signoff-draft', type=pathlib.Path, default=DEFAULT_OPERATOR_SIGNOFF_DRAFT)
    parser.add_argument('--operator-signoff-action-plan', type=pathlib.Path, default=DEFAULT_OPERATOR_SIGNOFF_ACTION_PLAN)
    parser.add_argument('--operator-signoff-from-inputs-status', type=pathlib.Path, default=DEFAULT_OPERATOR_SIGNOFF_FROM_INPUTS_STATUS)
    parser.add_argument('--recommendation-matrix', type=pathlib.Path, default=DEFAULT_RECOMMENDATION_MATRIX)
    parser.add_argument('--external-proof-inputs', type=pathlib.Path, default=DEFAULT_EXTERNAL_PROOF_INPUTS)
    parser.add_argument('--external-proof-execution-plan', type=pathlib.Path, default=DEFAULT_EXTERNAL_PROOF_EXECUTION_PLAN)
    parser.add_argument('--external-proof-values-template', type=pathlib.Path, default=DEFAULT_EXTERNAL_PROOF_VALUES_TEMPLATE)
    parser.add_argument('--external-proof-values-status', type=pathlib.Path, default=DEFAULT_EXTERNAL_PROOF_VALUES_STATUS)
    parser.add_argument('--release-artifact-consistency', type=pathlib.Path, default=DEFAULT_RELEASE_ARTIFACT_CONSISTENCY)
    parser.add_argument('--security-forbidden-evidence', type=pathlib.Path, default=DEFAULT_SECURITY_FORBIDDEN_EVIDENCE)
    parser.add_argument('--export-import-evidence', type=pathlib.Path, default=DEFAULT_EXPORT_IMPORT_EVIDENCE)
    parser.add_argument('--deployment-readiness-evidence', type=pathlib.Path, default=DEFAULT_DEPLOYMENT_READINESS_EVIDENCE)
    parser.add_argument('--beta-slo-baseline', type=pathlib.Path, default=DEFAULT_BETA_SLO_BASELINE)
    parser.add_argument('--frontend-npm-ci-evidence', type=pathlib.Path, default=DEFAULT_FRONTEND_NPM_CI_EVIDENCE)
    parser.add_argument('--packaging-cleanup-evidence', type=pathlib.Path, default=DEFAULT_PACKAGING_CLEANUP_EVIDENCE)
    parser.add_argument('--beta-tester-onboarding', type=pathlib.Path, default=DEFAULT_BETA_TESTER_ONBOARDING)
    parser.add_argument('--output', type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--json-output', type=pathlib.Path, default=None)
    parser.add_argument('--generated-at', default='', help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generated_at = args.generated_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    packet = build_packet(
        generated_at=generated_at,
        rc_evidence_path=args.rc_evidence,
        issue_evidence_dir=args.issue_evidence_dir,
        rc_issue_closure_evidence_path=args.rc_issue_closure_evidence,
        source_archive_path=args.source_archive,
        visual_smoke_dir=args.visual_smoke_dir,
        visual_smoke_review_path=args.visual_smoke_review,
        github_actions_evidence_path=args.github_actions_evidence,
        hosted_cookie_auth_evidence_path=args.hosted_cookie_auth_evidence,
        hosted_rc_evidence_path=args.hosted_rc_evidence,
        operator_signoff_status_path=args.operator_signoff_status,
        operator_signoff_draft_path=args.operator_signoff_draft,
        operator_signoff_action_plan_path=args.operator_signoff_action_plan,
        operator_signoff_from_inputs_status_path=args.operator_signoff_from_inputs_status,
        recommendation_matrix_path=args.recommendation_matrix,
        external_proof_inputs_path=args.external_proof_inputs,
        external_proof_execution_plan_path=args.external_proof_execution_plan,
        external_proof_values_template_path=args.external_proof_values_template,
        external_proof_values_status_path=args.external_proof_values_status,
        release_artifact_consistency_path=args.release_artifact_consistency,
        security_forbidden_evidence_path=args.security_forbidden_evidence,
        export_import_evidence_path=args.export_import_evidence,
        deployment_readiness_evidence_path=args.deployment_readiness_evidence,
        beta_slo_baseline_path=args.beta_slo_baseline,
        frontend_npm_ci_evidence_path=args.frontend_npm_ci_evidence,
        packaging_cleanup_evidence_path=args.packaging_cleanup_evidence,
        beta_tester_onboarding_path=args.beta_tester_onboarding,
    )
    write_packet(packet, args.output, args.json_output)
    print(f"[release-evidence-packet] Wrote {_relative_or_absolute(str(_resolve_repo_path(args.output)))}.")
    if args.json_output is not None:
        print(f"[release-evidence-packet] Wrote {_relative_or_absolute(str(_resolve_repo_path(args.json_output)))}.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
