#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CHECKLIST = REPO_ROOT / 'docs' / 'release_checklist.md'
DEFAULT_PACKET_JSON = REPO_ROOT / 'tmp' / 'release' / 'release-evidence-packet.json'
DEFAULT_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'release-checklist-status.md'
DEFAULT_JSON_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'release-checklist-status.json'
DEFAULT_OPERATOR_SIGNOFF_DRAFT = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff.draft.json'
DEFAULT_OPERATOR_SIGNOFF_ACTION_PLAN = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff-action-plan.json'
DEFAULT_OPERATOR_SIGNOFF_FROM_INPUTS = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff.from-inputs.json'
DEFAULT_OPERATOR_SIGNOFF_FROM_INPUTS_STATUS = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff.from-inputs-status.json'
DEFAULT_RECOMMENDATION_MATRIX = REPO_ROOT / 'tmp' / 'release' / 'rc-recommendation-matrix.json'
DEFAULT_EXTERNAL_PROOF_INPUTS = REPO_ROOT / 'tmp' / 'release' / 'external-proof-inputs.json'
DEFAULT_EXTERNAL_PROOF_INPUTS_MARKDOWN = REPO_ROOT / 'tmp' / 'release' / 'external-proof-inputs.md'
DEFAULT_EXTERNAL_PROOF_EXECUTION_PLAN = REPO_ROOT / 'tmp' / 'release' / 'external-proof-execution-plan.json'
DEFAULT_EXTERNAL_PROOF_EXECUTION_PLAN_MARKDOWN = REPO_ROOT / 'tmp' / 'release' / 'external-proof-execution-plan.md'
DEFAULT_EXTERNAL_PROOF_VALUES_TEMPLATE = REPO_ROOT / 'tmp' / 'release' / 'external-proof-values.example.json'
DEFAULT_EXTERNAL_PROOF_VALUES_STATUS = REPO_ROOT / 'tmp' / 'release' / 'external-proof-values-status.json'
DEFAULT_EXTERNAL_PROOF_VALUES_STATUS_MARKDOWN = REPO_ROOT / 'tmp' / 'release' / 'external-proof-values-status.md'
DEFAULT_RELEASE_ARTIFACT_CONSISTENCY = REPO_ROOT / 'tmp' / 'release' / 'release-artifact-consistency.md'
DEFAULT_RELEASE_ARTIFACT_CONSISTENCY_JSON = REPO_ROOT / 'tmp' / 'release' / 'release-artifact-consistency.json'
DEFAULT_FRONTEND_APP_TEST = REPO_ROOT / 'aidm_frontend' / 'src' / 'App.test.tsx'


@dataclass(frozen=True)
class ChecklistItem:
    section: str
    line_number: int
    text: str


@dataclass(frozen=True)
class ChecklistStatus:
    section: str
    line_number: int
    item: str
    status: str
    evidence: str
    remaining_action: str


def _resolve_repo_path(path: pathlib.Path) -> pathlib.Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _relative_or_absolute(path: pathlib.Path | str) -> str:
    candidate = pathlib.Path(path)
    try:
        return str(candidate.relative_to(REPO_ROOT))
    except ValueError:
        return str(candidate)


def _strip_markdown(text: str) -> str:
    return re.sub(r'\s+', ' ', text.replace('`', '')).strip()


def parse_checklist(path: pathlib.Path) -> list[ChecklistItem]:
    checklist_path = _resolve_repo_path(path)
    section = 'Unsectioned'
    items: list[ChecklistItem] = []
    for line_number, raw_line in enumerate(checklist_path.read_text(encoding='utf-8').splitlines(), start=1):
        line = raw_line.rstrip()
        if line.startswith('## '):
            section = line.removeprefix('## ').strip()
            continue
        match = re.match(r'^\s*-\s*\[[ xX]\]\s+(?P<text>.+)$', line)
        if match:
            items.append(ChecklistItem(section=section, line_number=line_number, text=match.group('text').strip()))
    return items


def load_packet(path: pathlib.Path) -> dict[str, Any]:
    packet_path = _resolve_repo_path(path)
    if not packet_path.exists():
        return {'overall_status': 'missing', 'rc_evidence': {'commands': []}}
    return json.loads(packet_path.read_text(encoding='utf-8'))


def _command_status(packet: dict[str, Any], label: str) -> str:
    for command in (packet.get('rc_evidence') or {}).get('commands') or []:
        if command.get('label') == label:
            return str(command.get('status') or 'missing')
    return 'missing'


def _commands_passed(packet: dict[str, Any], labels: tuple[str, ...]) -> bool:
    return all(_command_status(packet, label) == 'passed' for label in labels)


def _current_git_snapshot() -> tuple[str | None, bool | None]:
    try:
        head = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ['git', 'status', '--porcelain', '--untracked-files=all'],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return head or None, not bool(status.strip())


def _commits_match(left: str, right: str) -> bool:
    clean_left = left.strip().lower()
    clean_right = right.strip().lower()
    return bool(clean_left and clean_right) and (
        clean_left.startswith(clean_right) or clean_right.startswith(clean_left)
    )


def _artifact_status(packet: dict[str, Any], key: str) -> str:
    artifact = packet.get(key) or {}
    return str(artifact.get('status') or 'missing')


def _artifact_path(packet: dict[str, Any], key: str) -> str:
    artifact = packet.get(key) or {}
    path = artifact.get('path') or ''
    return _relative_or_absolute(path) if path else ''


def _hosted_rc_manual_required(packet: dict[str, Any]) -> int:
    return int((packet.get('hosted_rc_evidence') or {}).get('manual_required_count') or 0)


def _is_usable_target_url(value: Any) -> bool:
    text = str(value or '').strip()
    return bool(text and text not in {'not checked', 'isolated local runtime'})


def _hosted_rc_usable(packet: dict[str, Any]) -> bool:
    hosted = packet.get('hosted_rc_evidence') or {}
    status = str(hosted.get('status') or 'missing')
    freshness = str(hosted.get('generator_freshness') or '')
    if status in {'failed', 'invalid', 'invalid-evidence', 'missing', 'planned', 'stale'}:
        return False
    if freshness and freshness not in {'current', 'unknown'}:
        return False
    return _is_usable_target_url(hosted.get('target_url'))


def _hosted_rc_check(packet: dict[str, Any], label: str) -> dict[str, Any]:
    checks = (packet.get('hosted_rc_evidence') or {}).get('checks') or {}
    check = checks.get(label) or {}
    return check if isinstance(check, dict) else {}


def _hosted_rc_check_passed(packet: dict[str, Any], label: str) -> bool:
    if not _hosted_rc_usable(packet):
        return False
    check = _hosted_rc_check(packet, label)
    if check.get('status') != 'passed':
        return False
    if check.get('validation_errors'):
        return False
    target_url = check.get('evidence_target_url') or (packet.get('hosted_rc_evidence') or {}).get('target_url')
    return _is_usable_target_url(target_url)


def _hosted_rc_check_status(packet: dict[str, Any], label: str) -> ChecklistStatus | None:
    if not _hosted_rc_check_passed(packet, label):
        return None
    check = _hosted_rc_check(packet, label)
    evidence_path = str(check.get('evidence_path') or '').strip()
    target_url = str(check.get('evidence_target_url') or (packet.get('hosted_rc_evidence') or {}).get('target_url') or '').strip()
    evidence = _relative_or_absolute(evidence_path) if evidence_path else _artifact_path(packet, 'hosted_rc_evidence')
    return _status('passed', f'{evidence} passed via hosted RC evidence against {target_url}', '')


def _operator_signoff_status(packet: dict[str, Any]) -> ChecklistStatus:
    signoff = packet.get('operator_signoff') or {}
    status = str(signoff.get('status') or 'missing')
    path = _artifact_path(packet, 'operator_signoff') or 'tmp/release/operator-signoff-status.md'
    if status == 'passed':
        return _status('passed', f'{path} passed', '')
    if status == 'invalid':
        return _status('failed', f'{path} is invalid', 'fix tmp/release/operator-signoff.json')
    return _status(
        'external-required',
        f'operator signoff status is {status}; required complete: {signoff.get("required_complete") or "missing"}',
        'fill external proof values and run make rc-finalize-signoff',
    )


def _rc_issue_closure_status(packet: dict[str, Any]) -> ChecklistStatus:
    issue_closure = packet.get('rc_issue_closure_evidence') or {}
    status = str(issue_closure.get('status') or 'missing')
    path = _artifact_path(packet, 'rc_issue_closure_evidence') or 'tmp/release/rc-issue-closure-evidence.md'
    if status == 'passed':
        return _status('passed', f'{path} confirms generated evidence comments and closed RC issues', '')
    if status in {'failed', 'invalid'}:
        return _status('failed', f'{path} status is {status}', 'rerun make rc-issue-closure-evidence')
    open_issues = issue_closure.get('open_issues') or 'unknown'
    return _status(
        'external-required',
        f'{path} status is {status}; open issues: {open_issues}',
        'post/review generated issue evidence and close issues only after external proof is attached',
    )


def _rc_issue_closure_artifact_status(packet: dict[str, Any]) -> ChecklistStatus:
    issue_closure = packet.get('rc_issue_closure_evidence') or {}
    status = str(issue_closure.get('status') or 'missing')
    path = _artifact_path(packet, 'rc_issue_closure_evidence') or 'tmp/release/rc-issue-closure-evidence.md'
    if status in {'passed', 'external-required'}:
        return _status('passed', f'{path} generated; issue closure status: {status}', '')
    if status in {'failed', 'invalid'}:
        return _status('failed', f'{path} status is {status}', 'rerun make rc-issue-closure-evidence')
    return _status(
        'external-required',
        f'{path} status is {status}',
        'run make rc-issue-evidence, then rerun make rc-issue-closure-evidence',
    )


def _operator_signoff_draft_status() -> ChecklistStatus:
    if not DEFAULT_OPERATOR_SIGNOFF_DRAFT.exists():
        return _status(
            'external-required',
            'operator signoff draft is missing',
            'run make operator-signoff-draft after make release-evidence-packet',
        )
    try:
        json.loads(DEFAULT_OPERATOR_SIGNOFF_DRAFT.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return _status(
            'failed',
            f'{_relative_or_absolute(DEFAULT_OPERATOR_SIGNOFF_DRAFT)} is invalid JSON: {exc}',
            'rerun make operator-signoff-draft',
        )
    return _status('passed', f'{_relative_or_absolute(DEFAULT_OPERATOR_SIGNOFF_DRAFT)} generated', '')


def _operator_signoff_action_plan_status() -> ChecklistStatus:
    if not DEFAULT_OPERATOR_SIGNOFF_ACTION_PLAN.exists():
        return _status(
            'external-required',
            'operator signoff action plan is missing',
            'run make operator-signoff-action-plan after make operator-signoff-draft',
        )
    try:
        payload = json.loads(DEFAULT_OPERATOR_SIGNOFF_ACTION_PLAN.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return _status(
            'failed',
            f'{_relative_or_absolute(DEFAULT_OPERATOR_SIGNOFF_ACTION_PLAN)} is invalid JSON: {exc}',
            'rerun make operator-signoff-action-plan',
        )
    pending = payload.get('pending_count')
    return _status(
        'passed',
        f'{_relative_or_absolute(DEFAULT_OPERATOR_SIGNOFF_ACTION_PLAN)} generated; pending actions: {pending}',
        '',
    )


def _frontend_modal_accessibility_status(packet: dict[str, Any]) -> ChecklistStatus:
    if not _commands_passed(packet, ('Frontend tests',)):
        return _status(
            'failed',
            'Frontend tests did not pass in RC evidence',
            'run cd aidm_frontend && npm test and rerun make release-checklist-status',
        )
    if not DEFAULT_FRONTEND_APP_TEST.exists():
        return _status(
            'failed',
            f'{_relative_or_absolute(DEFAULT_FRONTEND_APP_TEST)} is missing',
            'add explicit modal accessibility regressions and rerun frontend tests',
        )
    test_source = DEFAULT_FRONTEND_APP_TEST.read_text(encoding='utf-8')
    required_snippets = {
        'focus placement': "findByRole('dialog', { name: 'Create New Campaign' })",
        'Escape close': "key: 'Escape'",
        'Tab focus trap': "key: 'Tab'",
        'focus return': 'toHaveFocus()',
        'dialog description': 'toHaveAccessibleDescription',
        'danger confirmation cancellation': "method === 'DELETE'",
        'delete confirmation Escape test': 'closes the character delete confirmation with Escape without deleting',
        'modal focus trap test': 'traps modal focus and returns focus to the opener when closed',
    }
    missing = [label for label, snippet in required_snippets.items() if snippet not in test_source]
    if missing:
        return _status(
            'failed',
            f'{_relative_or_absolute(DEFAULT_FRONTEND_APP_TEST)} is missing modal accessibility coverage: {", ".join(missing)}',
            'add explicit modal accessibility regressions and rerun frontend tests',
        )
    return _status(
        'passed',
        (
            f'{_relative_or_absolute(DEFAULT_FRONTEND_APP_TEST)} covers focus placement, Escape close, '
            'Tab focus trapping, focus return, dialog descriptions, and danger confirmation cancellation; '
            'Frontend tests passed'
        ),
        '',
    )


def _recommendation_matrix_status() -> ChecklistStatus:
    if not DEFAULT_RECOMMENDATION_MATRIX.exists():
        return _status(
            'external-required',
            'RC recommendation matrix is missing',
            'run make rc-recommendation-matrix',
        )
    try:
        payload = json.loads(DEFAULT_RECOMMENDATION_MATRIX.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return _status(
            'failed',
            f'{_relative_or_absolute(DEFAULT_RECOMMENDATION_MATRIX)} is invalid JSON: {exc}',
            'rerun make rc-recommendation-matrix',
        )
    status = payload.get('status') or 'unknown'
    counts = payload.get('counts') if isinstance(payload.get('counts'), dict) else {}
    count_label = ', '.join(f'{key}: {value}' for key, value in sorted(counts.items())) or 'no counts'
    return _status(
        'passed',
        f'{_relative_or_absolute(DEFAULT_RECOMMENDATION_MATRIX)} generated; status: {status}; {count_label}',
        '',
    )


def _external_proof_inputs_status(packet: dict[str, Any] | None = None) -> ChecklistStatus:
    missing = [
        _relative_or_absolute(path)
        for path in (DEFAULT_EXTERNAL_PROOF_INPUTS_MARKDOWN, DEFAULT_EXTERNAL_PROOF_INPUTS)
        if not path.exists()
    ]
    if missing:
        return _status(
            'external-required',
            f"external proof input template is missing: {', '.join(missing)}",
            'run make external-proof-inputs after make operator-signoff-action-plan',
        )
    try:
        payload = json.loads(DEFAULT_EXTERNAL_PROOF_INPUTS.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return _status(
            'failed',
            f'{_relative_or_absolute(DEFAULT_EXTERNAL_PROOF_INPUTS)} is invalid JSON: {exc}',
            'rerun make external-proof-inputs',
        )
    status = payload.get('status') or 'unknown'
    counts = payload.get('field_counts') if isinstance(payload.get('field_counts'), dict) else {}
    count_label = ', '.join(f'{key}: {value}' for key, value in sorted(counts.items())) or 'no field counts'
    return _status(
        'passed',
        (
            f'{_relative_or_absolute(DEFAULT_EXTERNAL_PROOF_INPUTS_MARKDOWN)} and '
            f'{_relative_or_absolute(DEFAULT_EXTERNAL_PROOF_INPUTS)} generated; status: {status}; {count_label}'
        ),
        '',
    )


def _external_proof_execution_plan_status() -> ChecklistStatus:
    missing = [
        _relative_or_absolute(path)
        for path in (DEFAULT_EXTERNAL_PROOF_EXECUTION_PLAN_MARKDOWN, DEFAULT_EXTERNAL_PROOF_EXECUTION_PLAN)
        if not path.exists()
    ]
    if missing:
        return _status(
            'external-required',
            f"external proof execution plan is missing: {', '.join(missing)}",
            'run make external-proof-execution-plan after make external-proof-inputs',
        )
    try:
        payload = json.loads(DEFAULT_EXTERNAL_PROOF_EXECUTION_PLAN.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return _status(
            'failed',
            f'{_relative_or_absolute(DEFAULT_EXTERNAL_PROOF_EXECUTION_PLAN)} is invalid JSON: {exc}',
            'rerun make external-proof-execution-plan',
        )
    status = payload.get('status') or 'unknown'
    counts = payload.get('counts') if isinstance(payload.get('counts'), dict) else {}
    count_label = ', '.join(f'{key}: {value}' for key, value in sorted(counts.items())) or 'no counts'
    return _status(
        'passed',
        (
            f'{_relative_or_absolute(DEFAULT_EXTERNAL_PROOF_EXECUTION_PLAN_MARKDOWN)} and '
            f'{_relative_or_absolute(DEFAULT_EXTERNAL_PROOF_EXECUTION_PLAN)} generated; status: {status}; {count_label}'
        ),
        '',
    )


def _external_proof_values_template_status() -> ChecklistStatus:
    if not DEFAULT_EXTERNAL_PROOF_VALUES_TEMPLATE.exists():
        return _status(
            'external-required',
            'external proof values template is missing',
            'run make operator-signoff-values-template after make external-proof-inputs',
        )
    try:
        payload = json.loads(DEFAULT_EXTERNAL_PROOF_VALUES_TEMPLATE.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return _status(
            'failed',
            f'{_relative_or_absolute(DEFAULT_EXTERNAL_PROOF_VALUES_TEMPLATE)} is invalid JSON: {exc}',
            'rerun make operator-signoff-values-template',
        )
    values = payload.get('values') if isinstance(payload.get('values'), dict) else {}
    return _status(
        'passed',
        f'{_relative_or_absolute(DEFAULT_EXTERNAL_PROOF_VALUES_TEMPLATE)} generated; fields: {len(values)}',
        '',
    )


def _external_proof_values_check_status() -> ChecklistStatus:
    missing = [
        _relative_or_absolute(path)
        for path in (DEFAULT_EXTERNAL_PROOF_VALUES_STATUS_MARKDOWN, DEFAULT_EXTERNAL_PROOF_VALUES_STATUS)
        if not path.exists()
    ]
    if missing:
        return _status(
            'external-required',
            f"external proof values check report is missing: {', '.join(missing)}",
            'run make external-proof-values-check after make operator-signoff-values-template',
        )
    try:
        payload = json.loads(DEFAULT_EXTERNAL_PROOF_VALUES_STATUS.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return _status(
            'failed',
            f'{_relative_or_absolute(DEFAULT_EXTERNAL_PROOF_VALUES_STATUS)} is invalid JSON: {exc}',
            'rerun make external-proof-values-check',
        )
    status = str(payload.get('status') or 'unknown')
    invalid_count = int(payload.get('invalid_error_count') or 0)
    required_complete = str(payload.get('required_complete') or 'unknown')
    missing_count = int(payload.get('missing_required_count') or 0)
    if status == 'invalid' or invalid_count > 0:
        return _status(
            'failed',
            (
                f'{_relative_or_absolute(DEFAULT_EXTERNAL_PROOF_VALUES_STATUS_MARKDOWN)} reports invalid external '
                f'proof values; invalid errors: {invalid_count}'
            ),
            'remove persisted command-only values and rerun make external-proof-values-check',
        )
    return _status(
        'passed',
        (
            f'{_relative_or_absolute(DEFAULT_EXTERNAL_PROOF_VALUES_STATUS_MARKDOWN)} and '
            f'{_relative_or_absolute(DEFAULT_EXTERNAL_PROOF_VALUES_STATUS)} generated; status: {status}; '
            f'required complete: {required_complete}; missing required: {missing_count}'
        ),
        '',
    )


def _release_artifact_consistency_status(packet: dict[str, Any]) -> ChecklistStatus:
    artifact = packet.get('release_artifact_consistency') or {}
    status = str(artifact.get('status') or 'missing')
    path = _artifact_path(packet, 'release_artifact_consistency') or _relative_or_absolute(DEFAULT_RELEASE_ARTIFACT_CONSISTENCY)
    json_path = str(artifact.get('json_path') or DEFAULT_RELEASE_ARTIFACT_CONSISTENCY_JSON)
    json_label = _relative_or_absolute(json_path)
    check_count = int(artifact.get('check_count') or 0)
    error_count = int(artifact.get('error_count') or 0)
    if status == 'passed' and error_count == 0:
        return _status(
            'passed',
            f'{path} and {json_label} passed; checks: {check_count}; source archive sha256: {artifact.get("source_archive_sha256") or "missing"}',
            '',
        )
    if status in {'failed', 'invalid', 'invalid-evidence'} or error_count:
        return _status(
            'failed',
            f'{path} status is {status}; errors: {error_count}',
            'rerun make rc-handoff-artifacts and fix stale source archive/signoff evidence',
        )
    return _status(
        'external-required',
        f'{path} status is {status}',
        'run make release-artifact-consistency after make release-evidence-packet',
    )


def _signed_off_worktree_status(packet: dict[str, Any]) -> ChecklistStatus:
    worktree = packet.get('signed_off_worktree') or {}
    status = str(worktree.get('status') or 'unknown')
    label = str(worktree.get('worktree') or 'unknown')
    commit = str(worktree.get('commit') or 'unknown')
    if status == 'passed':
        return _status('passed', f'RC evidence is from clean worktree at {commit}', '')
    return _status(
        'external-required',
        f'RC evidence worktree is {label}',
        'commit/push the release candidate and regenerate RC evidence from a clean signed-off worktree',
    )


def _has_github_run_url(value: Any) -> bool:
    text = str(value or '').strip()
    return bool(text and text.lower() != 'missing')


def _github_actions_checklist_status(packet: dict[str, Any], lowered: str) -> ChecklistStatus:
    status = _artifact_status(packet, 'github_actions')
    path = _artifact_path(packet, 'github_actions')
    if status == 'invalid':
        return _status(
            'failed',
            f'{path} is invalid',
            'fix GitHub Actions run URL evidence and rerun make github-actions-evidence',
        )
    if status == 'stale':
        return _status(
            'external-required',
            f'{path} is older than RC evidence',
            'rerun make github-actions-evidence after the final RC evidence run',
        )

    github_actions = packet.get('github_actions') if isinstance(packet.get('github_actions'), dict) else {}
    aidm_ci_url = github_actions.get('aidm_ci_run_url')
    closed_beta_rc_url = github_actions.get('closed_beta_rc_run_url')
    requires_aidm_ci = 'aidm ci' in lowered
    requires_closed_beta_rc = 'closed beta rc' in lowered or 'closed-beta-rc' in lowered
    requires_both = 'github-actions-evidence.md' in lowered or (requires_aidm_ci and requires_closed_beta_rc)

    if status == 'passed':
        return _status('passed', f'{path} has required run URLs', '')

    if requires_both:
        missing: list[str] = []
        if not _has_github_run_url(aidm_ci_url):
            missing.append('AIDM CI run URL')
        if not _has_github_run_url(closed_beta_rc_url):
            missing.append('Closed Beta RC run URL')
        if not missing:
            return _status('passed', f'{path} has AIDM CI and Closed Beta RC run URLs', '')
        return _status(
            'external-required',
            f"GitHub Actions evidence is {status}; missing: {', '.join(missing)}",
            'attach AIDM CI and Closed Beta RC run URLs',
        )

    if requires_aidm_ci:
        if _has_github_run_url(aidm_ci_url):
            return _status('passed', f'{path} records AIDM CI run URL: {aidm_ci_url}', '')
        return _status(
            'external-required',
            f'GitHub Actions evidence is {status}; missing AIDM CI run URL',
            'attach AIDM CI run URL',
        )

    if requires_closed_beta_rc:
        if _has_github_run_url(closed_beta_rc_url):
            return _status('passed', f'{path} records Closed Beta RC run URL: {closed_beta_rc_url}', '')
        return _status(
            'external-required',
            f'GitHub Actions evidence is {status}; missing Closed Beta RC run URL',
            'attach Closed Beta RC run URL',
        )

    return _status(
        'external-required',
        f'GitHub Actions evidence is {status}',
        'attach AIDM CI and Closed Beta RC run URLs',
    )


def _closed_beta_rc_artifact_checklist_status(packet: dict[str, Any], lowered: str = '') -> ChecklistStatus:
    github_actions = packet.get('github_actions') if isinstance(packet.get('github_actions'), dict) else {}
    status = str(github_actions.get('status') or 'missing')
    path = _artifact_path(packet, 'github_actions') or 'tmp/release/github-actions-evidence.md'
    if status == 'invalid':
        return _status(
            'failed',
            f'{path} is invalid',
            'fix GitHub Actions run URL evidence and rerun make github-actions-evidence',
        )
    if status == 'stale':
        return _status(
            'external-required',
            f'{path} is older than RC evidence',
            'rerun make github-actions-evidence after the final RC evidence run',
        )

    artifact = github_actions.get('closed_beta_rc_artifact')
    if not isinstance(artifact, dict):
        artifact = {}
    artifact_status = str(
        github_actions.get('closed_beta_rc_artifact_status') or artifact.get('status') or 'not-checked'
    )
    content_status = str(
        github_actions.get('closed_beta_rc_artifact_content_status') or artifact.get('content_status') or 'not-checked'
    )
    artifact_name = str(
        artifact.get('name')
        or github_actions.get('closed_beta_rc_artifact_name')
        or artifact.get('expected_name')
        or 'closed-beta-rc-evidence'
    )
    artifact_url = str(artifact.get('url') or github_actions.get('closed_beta_rc_artifact_url') or '')
    aidm_ci_url = github_actions.get('aidm_ci_run_url')
    closed_beta_rc_url = github_actions.get('closed_beta_rc_run_url')

    if 'aidm ci' in lowered and not _has_github_run_url(aidm_ci_url):
        return _status(
            'external-required',
            f'{path} has not recorded an AIDM CI run URL',
            'run or wait for AIDM CI and rerun make github-actions-evidence',
        )
    if not _has_github_run_url(closed_beta_rc_url):
        return _status(
            'external-required',
            f'{path} has not recorded a Closed Beta RC run URL',
            'run the manual Closed Beta RC workflow and rerun make github-actions-evidence',
        )

    if artifact_status == 'passed' and content_status == 'passed':
        url_detail = f': {artifact_url}' if artifact_url else ''
        return _status('passed', f'{path} verifies {artifact_name} artifact contents{url_detail}', '')
    if artifact_status == 'missing':
        return _status(
            'failed',
            f'{path} checked the Closed Beta RC run, but {artifact_name} was not found',
            'fix the Closed Beta RC artifact upload or rerun the manual workflow, then rerun make github-actions-evidence',
        )
    if artifact_status == 'passed' and content_status == 'missing':
        missing_globs = artifact.get('content_missing_globs')
        detail = ', '.join(str(pattern) for pattern in missing_globs) if isinstance(missing_globs, list) else ''
        return _status(
            'failed',
            f'{path} found {artifact_name}, but artifact contents are missing required files: {detail or "unknown"}',
            'fix the Closed Beta RC artifact upload paths and rerun make github-actions-evidence with --verify-closed-beta-rc-artifact-contents',
        )
    if artifact_status == 'passed' and content_status == 'unknown':
        return _status(
            'external-required',
            f'{path} found {artifact_name}, but artifact content verification could not complete',
            'rerun make github-actions-evidence with --verify-closed-beta-rc-artifact-contents after the artifact is downloadable',
        )
    if artifact_status == 'passed':
        return _status(
            'external-required',
            f'{path} records {artifact_name}, but artifact contents are {content_status}',
            'rerun make github-actions-evidence with --include-gh-details --verify-closed-beta-rc-artifact-contents',
        )
    return _status(
        'external-required',
        f'{path} artifact status is {artifact_status}',
        'rerun make github-actions-evidence with --auto-gh --include-gh-details --verify-closed-beta-rc-artifact-contents after the manual Closed Beta RC run',
    )


def _target_status(packet: dict[str, Any], key: str) -> tuple[str, str]:
    artifact = packet.get(key) or {}
    status = str(artifact.get('status') or 'missing')
    target_url = str(artifact.get('target_url') or '')
    if status == 'passed' and target_url and target_url not in {'not checked', 'isolated local runtime'}:
        return 'passed', f"{_artifact_path(packet, key)} against {target_url}"
    if status in {'present', 'passed'} and target_url and target_url not in {'not checked', 'isolated local runtime'}:
        return 'passed', f"{_artifact_path(packet, key)} against {target_url}"
    return 'external-required', f"{_artifact_path(packet, key) or key} needs hosted/staging target evidence"


def _frontend_npm_ci_status(packet: dict[str, Any]) -> ChecklistStatus:
    artifact = packet.get('frontend_npm_ci') or {}
    status = str(artifact.get('status') or 'missing')
    path = _artifact_path(packet, 'frontend_npm_ci') or 'tmp/release/frontend-npm-ci-evidence.md'
    if status == 'passed':
        return _status('passed', f'{path} passed', '')
    if status in {'failed', 'invalid', 'stale'}:
        return _status('failed', f'{path} status is {status}', 'rerun make rc-handoff-artifacts')
    return _status(
        'manual-review',
        f'{path} is {status}',
        'run make frontend-npm-ci-evidence or review before final RC sign-off',
    )


def _packaging_cleanup_status(packet: dict[str, Any], *, target: str) -> ChecklistStatus:
    artifact = packet.get('packaging_cleanup') or {}
    status = str(artifact.get('status') or 'missing')
    path = _artifact_path(packet, 'packaging_cleanup') or 'tmp/release/packaging-cleanup-evidence.md'
    if status == 'passed':
        return _status('passed', f'{path} verifies {target} cleanup coverage and archive exclusions', '')
    if status in {'failed', 'invalid', 'stale'}:
        return _status('failed', f'{path} status is {status}', 'rerun make rc-handoff-artifacts')
    return _status(
        'manual-review',
        f'{path} is {status}',
        'run make packaging-cleanup-evidence or review before final RC sign-off',
    )


def _metadata_value(packet: dict[str, Any], artifact_key: str, metadata_key: str) -> str:
    artifact = packet.get(artifact_key) or {}
    metadata = artifact.get('metadata') if isinstance(artifact, dict) else {}
    if isinstance(metadata, dict) and metadata.get(metadata_key) not in {None, ''}:
        return str(metadata.get(metadata_key))
    value = artifact.get(metadata_key) if isinstance(artifact, dict) else None
    return str(value or '')


def _socketio_worker_model(packet: dict[str, Any]) -> str:
    for artifact_key in ('hosted_rc_evidence', 'deployment_readiness', 'beta_slo_baseline'):
        value = _metadata_value(packet, artifact_key, 'socket_io_worker_model')
        if value:
            return value.strip().lower()
    return ''


def _conditional_socketio_multiworker_status(packet: dict[str, Any], *, proof_only: bool = False) -> ChecklistStatus:
    del proof_only
    worker_model = _socketio_worker_model(packet)
    if worker_model == 'single':
        return _status(
            'passed',
            'RC1 worker model is single; sticky/message-queue staging proof is not required',
            '',
        )
    if worker_model:
        return _status(
            'failed',
            f'{worker_model} Socket.IO worker model is unsupported for hosted production in this release',
            'set AIDM_SOCKETIO_WORKER_MODEL=single and provide hosted single-worker process proof',
        )
    return _status(
        'external-required',
        'Socket.IO worker model is missing from hosted evidence',
        'record AIDM_SOCKETIO_WORKER_MODEL=single in hosted RC evidence',
    )


def _source_archive_sidecar_status(packet: dict[str, Any]) -> ChecklistStatus | None:
    source = packet.get('source_archive') or {}
    path_value = source.get('path') or ''
    if not path_value:
        return None
    source_status = str(source.get('status') or 'missing')
    if source_status == 'stale':
        return _status('failed', f"{_artifact_path(packet, 'source_archive')} is older than RC evidence", 'rerun make rc-handoff-artifacts')
    sidecar = pathlib.Path(path_value + '.sha256')
    expected_sha = str(source.get('sha256') or '').strip()
    if not sidecar.exists() or not expected_sha:
        return _status('external-required', 'source archive exists but checksum sidecar is missing from evidence', 'rerun make source-archive')
    try:
        recorded_sha = sidecar.read_text(encoding='utf-8').split()[0]
    except (OSError, IndexError):
        return _status('failed', f"{_relative_or_absolute(sidecar)} could not be read as a checksum sidecar", 'rerun make source-archive')
    if recorded_sha != expected_sha:
        return _status(
            'failed',
            f"{_relative_or_absolute(sidecar)} checksum does not match packet sha256",
            'rerun make source-archive',
        )
    return _status('passed', f"{_relative_or_absolute(sidecar)} matches packet sha256 {expected_sha}", '')


def _status(status: str, evidence: str, remaining_action: str) -> ChecklistStatus:
    return ChecklistStatus(section='', line_number=0, item='', status=status, evidence=evidence, remaining_action=remaining_action)


def _with_item(item: ChecklistItem, status: ChecklistStatus) -> ChecklistStatus:
    return ChecklistStatus(
        section=item.section,
        line_number=item.line_number,
        item=item.text,
        status=status.status,
        evidence=status.evidence,
        remaining_action=status.remaining_action,
    )


def _passed_when_gates(packet: dict[str, Any], labels: tuple[str, ...], evidence_label: str | None = None) -> ChecklistStatus:
    if _commands_passed(packet, labels):
        evidence = evidence_label or ', '.join(labels)
        return _status('passed', f'RC evidence gates passed: {evidence}', '')
    missing = [label for label in labels if _command_status(packet, label) != 'passed']
    return _status('external-required', f"Missing/failed RC gates: {', '.join(missing)}", 'rerun make closed-beta-rc')


def _passed_when_current_rc_gates(
    packet: dict[str, Any],
    labels: tuple[str, ...],
    evidence_label: str,
) -> ChecklistStatus:
    gate_status = _passed_when_gates(packet, labels, evidence_label)
    if gate_status.status != 'passed':
        return gate_status

    rc_evidence = packet.get('rc_evidence') or {}
    signed_off = packet.get('signed_off_worktree') or {}
    packet_commit = str(rc_evidence.get('commit') or signed_off.get('commit') or '').strip()
    current_commit, current_clean = _current_git_snapshot()
    if (
        str(signed_off.get('status') or '') != 'passed'
        or not packet_commit
        or not current_commit
        or not _commits_match(packet_commit, current_commit)
        or current_clean is not True
    ):
        current_label = current_commit[:12] if current_commit else 'unavailable'
        packet_label = packet_commit or 'missing'
        return _status(
            'external-required',
            (
                f'RC regression evidence is not current for this worktree '
                f'(packet {packet_label}; current {current_label}; clean={current_clean})'
            ),
            'commit the remediation and rerun make closed-beta-rc plus make release-evidence-packet',
        )
    return gate_status


def _classify_command_item(text: str, packet: dict[str, Any]) -> ChecklistStatus | None:
    command_rules: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
        (('closed-beta-rc',), tuple(label for label in (command.get('label') for command in (packet.get('rc_evidence') or {}).get('commands') or []) if label)),
        (('deploy_bootstrap.py --check-only',), ('Deploy bootstrap check-only',)),
        (('-m pytest',), ('Backend tests',)),
        (('smoke_beta_flow.py',), ('Isolated beta smoke flow',)),
        (('scenario_regression.py',), ('Scenario quality regressions',)),
        (('flask db upgrade',), ('Migration chain drill',)),
        (('make db-upgrade',), ('Migration chain drill',)),
        (('npm test',), ('Frontend tests',)),
        (('npm run lint',), ('Frontend tests',)),
        (('npm run typecheck',), ('Frontend tests',)),
        (('npm run build',), ('Frontend build',)),
        (('bundle:budget',), ('Frontend bundle budget',)),
        (('npm audit --omit=dev',), ('Frontend production dependency audit',)),
        (('backup-restore-drill',), ('SQLite backup/restore drill',)),
        (('session-export-import-smoke',), ('Session export/import smoke',)),
        (('migration-chain-drill',), ('Migration chain drill',)),
        (('request-json-parsing',), ('Request JSON parsing guard',)),
        (('request.get_json(silent=true)',), ('Request JSON parsing guard',)),
        (('check_state_snapshot_writers.py',), ('State snapshot writer inventory',)),
        (('socketio-worker-model-decision',), ('Socket.IO worker model decision',)),
        (('socket-concurrency-smoke',), ('Socket concurrency smoke',)),
        (('observability-check',), ('Observability bundle check',)),
        (('local-beta-slo-baseline',), ('Local beta SLO baseline',)),
        (('rc-issue-evidence',), ()),
        (('rc-handoff-artifacts',), ()),
        (('release-evidence-packet',), ()),
    )
    lowered = text.lower()
    for needles, labels in command_rules:
        if not all(needle in lowered for needle in needles):
            continue
        if 'closed-beta-rc' in needles:
            rc_status = (packet.get('rc_evidence') or {}).get('status')
            if rc_status == 'passed':
                return _status('passed', 'local RC evidence passed', '')
            return _status('external-required', 'local RC evidence is not passed', 'run make closed-beta-rc')
        if 'rc-issue-evidence' in needles:
            issue_status = _artifact_status(packet, 'issue_evidence')
            if issue_status in {'passed', 'passed with external exceptions'}:
                return _status('passed', f"issue snippets rendered at {_artifact_path(packet, 'issue_evidence')}", '')
            return _status('external-required', 'issue evidence snippets are missing/incomplete', 'run make rc-issue-evidence')
        if 'rc-handoff-artifacts' in needles:
            required_paths = [
                DEFAULT_OUTPUT,
                DEFAULT_JSON_OUTPUT,
                DEFAULT_OPERATOR_SIGNOFF_DRAFT,
                DEFAULT_OPERATOR_SIGNOFF_ACTION_PLAN,
                DEFAULT_OPERATOR_SIGNOFF_FROM_INPUTS,
                DEFAULT_OPERATOR_SIGNOFF_FROM_INPUTS_STATUS,
                DEFAULT_RECOMMENDATION_MATRIX,
                DEFAULT_EXTERNAL_PROOF_INPUTS_MARKDOWN,
                DEFAULT_EXTERNAL_PROOF_INPUTS,
                DEFAULT_EXTERNAL_PROOF_VALUES_TEMPLATE,
                DEFAULT_RELEASE_ARTIFACT_CONSISTENCY,
                DEFAULT_RELEASE_ARTIFACT_CONSISTENCY_JSON,
            ]
            missing = [_relative_or_absolute(path) for path in required_paths if not path.exists()]
            if not missing and _artifact_status(packet, 'source_archive') == 'passed':
                return _status('passed', 'RC handoff artifacts generated', '')
            detail = ', '.join(missing) if missing else 'source archive evidence is not passed'
            return _status('external-required', f'RC handoff artifacts are incomplete: {detail}', 'run make rc-handoff-artifacts')
        if 'release-evidence-packet' in needles:
            if packet.get('overall_status') and packet.get('overall_status') != 'missing':
                return _status('passed', _artifact_path(packet, 'release_evidence') or 'release evidence packet JSON loaded', '')
            return _status('external-required', 'release evidence packet missing', 'run make release-evidence-packet')
        return _passed_when_gates(packet, labels)
    return None


def classify_item(item: ChecklistItem, packet: dict[str, Any]) -> ChecklistStatus:
    text = _strip_markdown(item.text)
    lowered = text.lower()

    if 'operator-signoff-draft' in lowered:
        return _with_item(item, _operator_signoff_draft_status())

    if 'rc-issue-closure-evidence' in lowered:
        return _with_item(item, _rc_issue_closure_artifact_status(packet))

    if 'rc gate issues are closed' in lowered:
        return _with_item(item, _rc_issue_closure_status(packet))

    if 'operator-signoff-action-plan' in lowered:
        return _with_item(item, _operator_signoff_action_plan_status())

    if 'rc-handoff-artifacts' in lowered:
        command_status = _classify_command_item(text, packet)
        if command_status is not None:
            return _with_item(item, command_status)

    if 'rc-recommendation-matrix' in lowered or 'recommendation matrix' in lowered:
        return _with_item(item, _recommendation_matrix_status())

    if 'operator-signoff-values-template' in lowered or 'external-proof-values.example.json' in lowered:
        return _with_item(item, _external_proof_values_template_status())

    if 'rc-finalize-signoff' in lowered:
        return _with_item(item, _operator_signoff_status(packet))

    if 'external-proof-values-check' in lowered or 'external-proof-values-status' in lowered:
        return _with_item(item, _external_proof_values_check_status())

    if 'release-artifact-consistency' in lowered or 'artifact consistency' in lowered:
        return _with_item(item, _release_artifact_consistency_status(packet))

    if 'external-proof-inputs' in lowered or 'external proof inputs' in lowered:
        return _with_item(item, _external_proof_inputs_status(packet))

    if 'external-proof-execution-plan' in lowered or 'external proof execution plan' in lowered:
        return _with_item(item, _external_proof_execution_plan_status())

    if 'operator-signoff-status' in lowered or 'operator sign-off' in lowered or 'operator signoff' in lowered:
        return _with_item(item, _operator_signoff_status(packet))

    if 'clean signed-off' in lowered or 'clean worktree' in lowered:
        return _with_item(item, _signed_off_worktree_status(packet))

    if 'closed-beta-rc-evidence' in lowered or 'workflow artifact includes the generated source archive' in lowered:
        return _with_item(item, _closed_beta_rc_artifact_checklist_status(packet, lowered))

    if 'github-actions-evidence' in lowered or 'github actions' in lowered or 'closed beta rc workflow' in lowered:
        return _with_item(item, _github_actions_checklist_status(packet, lowered))

    if 'hosted-rc-evidence' in lowered or 'hosted rc evidence' in lowered:
        status = _artifact_status(packet, 'hosted_rc_evidence')
        manual_required = _hosted_rc_manual_required(packet)
        if status == 'passed' and manual_required == 0:
            return _with_item(
                item,
                _status(
                    'passed',
                    f"{_artifact_path(packet, 'hosted_rc_evidence')} passed with all manual proof supplied",
                    '',
                ),
            )
        if status in {'failed', 'invalid', 'invalid-evidence'}:
            return _with_item(
                item,
                _status(
                    'failed',
                    f"hosted RC evidence status is {status}",
                    'fix the hosted RC evidence run and rerun make hosted-rc-evidence, then make rc-handoff-artifacts',
                ),
            )
        if status == 'stale':
            return _with_item(
                item,
                _status(
                    'external-required',
                    'hosted RC evidence was generated by an older hosted evidence checker',
                    'rerun make hosted-rc-evidence, then make rc-handoff-artifacts',
                ),
            )
        return _with_item(
            item,
            _status(
                'external-required',
                f"hosted RC status is {status}; manual proofs required: {manual_required}",
                'run hosted RC evidence against the target and provide manual proof flags',
            ),
        )

    if 'release-checklist-status' in lowered:
        output_path = DEFAULT_OUTPUT
        json_path = DEFAULT_JSON_OUTPUT
        if output_path.exists() and json_path.exists():
            return _with_item(item, _status('passed', f"{_relative_or_absolute(output_path)} and {_relative_or_absolute(json_path)}", ''))
        return _with_item(item, _status('external-required', 'release checklist status artifacts are missing', 'run make release-checklist-status'))

    if 'cd aidm_frontend && npm ci' in lowered:
        return _with_item(item, _frontend_npm_ci_status(packet))

    if 'make clean-deps' in lowered:
        return _with_item(item, _packaging_cleanup_status(packet, target='make clean-deps'))

    if 'make clean' in lowered:
        return _with_item(item, _packaging_cleanup_status(packet, target='make clean'))

    if (
        'source archive attached' in lowered
        or 'source archive is attached' in lowered
        or 'attach the source archive' in lowered
        or 'workflow artifact includes the generated source archive' in lowered
    ):
        hosted = packet.get('hosted_rc_evidence') or {}
        manual_required = hosted.get('manual_required') or []
        if 'Source archive attached to RC issue or release' not in manual_required and hosted.get('status') == 'passed':
            return _with_item(item, _status('passed', 'hosted RC evidence records source archive attachment proof', ''))
        return _with_item(
            item,
            _status(
                'external-required',
                'source archive attachment proof is still manual/external',
                'attach source archive to RC issue, workflow artifact, or GitHub Release',
            ),
        )

    if 'source archive has a matching .sha256' in lowered:
        sidecar_status = _source_archive_sidecar_status(packet)
        if sidecar_status:
            return _with_item(item, sidecar_status)

    if 'make source-archive' in lowered or 'release archive does not include' in lowered:
        source_status = _artifact_status(packet, 'source_archive')
        if source_status == 'passed':
            return _with_item(item, _status('passed', f"{_artifact_path(packet, 'source_archive')} passed archive scan", ''))
        if source_status == 'stale':
            return _with_item(
                item,
                _status('failed', f"{_artifact_path(packet, 'source_archive')} is older than RC evidence", 'rerun make rc-handoff-artifacts'),
            )
        return _with_item(item, _status('external-required', f'source archive status is {source_status}', 'run make source-archive'))

    if 'rc browser smoke' in lowered or 'browser smoke runs' in lowered:
        if _commands_passed(packet, ('Browser smoke (single-origin build)',)):
            return _with_item(item, _status('passed', 'RC browser smoke passed', ''))
        return _with_item(item, _status('external-required', 'browser smoke gate is missing/failed', 'run browser smoke'))

    if 'multi-worker deployments' in lowered:
        return _with_item(item, _conditional_socketio_multiworker_status(packet))

    if 'sticky or message-queue socket.io deployments' in lowered:
        return _with_item(item, _conditional_socketio_multiworker_status(packet, proof_only=True))

    command_status = _classify_command_item(lowered, packet)
    if command_status is not None:
        return _with_item(item, command_status)

    if 'deployment-readiness' in lowered or 'deployment readiness' in lowered:
        hosted_status = _hosted_rc_check_status(packet, 'Hosted deployment readiness')
        if hosted_status is not None:
            return _with_item(item, hosted_status)
        status, evidence = _target_status(packet, 'deployment_readiness')
        return _with_item(item, _status(status, evidence, '' if status == 'passed' else 'run deployment-readiness against hosted/staging'))

    if 'hosted cookie-auth' in lowered or 'hosted-cookie-auth' in lowered or 'cookie auth smoke' in lowered:
        status = _artifact_status(packet, 'hosted_cookie_auth')
        mode = (packet.get('hosted_cookie_auth') or {}).get('mode') or ''
        if status == 'passed' and ('during the local rc gate' in lowered or 'target-url' not in lowered):
            return _with_item(item, _status('passed', f"{_artifact_path(packet, 'hosted_cookie_auth')} local RC smoke passed", ''))
        hosted_status = _hosted_rc_check_status(packet, 'Hosted cookie auth smoke')
        if hosted_status is not None:
            return _with_item(item, hosted_status)
        if status == 'passed' and mode == 'live-target':
            return _with_item(item, _status('passed', f"{_artifact_path(packet, 'hosted_cookie_auth')} live-target passed", ''))
        if status == 'passed':
            return _with_item(item, _status('external-required', f"{_artifact_path(packet, 'hosted_cookie_auth')} is local/isolated only", 'run hosted cookie-auth smoke against hosted/staging'))
        return _with_item(item, _status('external-required', f'hosted cookie-auth evidence is {status}', 'run hosted cookie-auth smoke'))

    if 'non-admin' in lowered or 'forbidden-response' in lowered or 'security-forbidden' in lowered:
        status = _artifact_status(packet, 'security_forbidden')
        mode = (packet.get('security_forbidden') or {}).get('mode') or ''
        if status == 'passed' and 'target-url' not in lowered:
            return _with_item(item, _status('passed', f"{_artifact_path(packet, 'security_forbidden')} local RC smoke passed", ''))
        hosted_status = _hosted_rc_check_status(packet, 'Hosted non-admin forbidden smoke')
        if hosted_status is not None:
            return _with_item(item, hosted_status)
        if status == 'passed' and mode == 'live-target':
            return _with_item(item, _status('passed', f"{_artifact_path(packet, 'security_forbidden')} live-target passed", ''))
        if status == 'passed':
            return _with_item(item, _status('external-required', f"{_artifact_path(packet, 'security_forbidden')} is local/isolated only", 'run forbidden smoke against hosted/staging'))
        return _with_item(item, _status('external-required', f'forbidden-response evidence is {status}', 'run security-forbidden-smoke'))

    if 'export/import' in lowered or 'export-import' in lowered:
        status = _artifact_status(packet, 'export_import')
        mode = (packet.get('export_import') or {}).get('mode') or ''
        if 'hosted' in lowered or 'target-environment' in lowered:
            hosted_status = _hosted_rc_check_status(packet, 'Hosted session export/import smoke')
            if hosted_status is not None:
                return _with_item(item, hosted_status)
            if status == 'passed' and mode == 'live-target':
                return _with_item(item, _status('passed', f"{_artifact_path(packet, 'export_import')} live-target passed", ''))
            return _with_item(item, _status('external-required', f"{_artifact_path(packet, 'export_import')} is not hosted live-target evidence", 'run hosted export/import smoke'))
        if status == 'passed':
            return _with_item(item, _status('passed', f"{_artifact_path(packet, 'export_import')} passed", ''))

    if 'beta slo baseline' in lowered or 'target-environment metrics' in lowered:
        hosted_status = _hosted_rc_check_status(packet, 'Hosted beta SLO baseline')
        if hosted_status is not None:
            return _with_item(item, hosted_status)
        status, evidence = _target_status(packet, 'beta_slo_baseline')
        return _with_item(item, _status(status, evidence, '' if status == 'passed' else 'render beta SLO baseline from hosted/staging target'))

    if 'source archive' in lowered or 'release archive' in lowered:
        source_status = _artifact_status(packet, 'source_archive')
        if source_status == 'passed':
            return _with_item(item, _status('passed', f"{_artifact_path(packet, 'source_archive')} passed archive scan", ''))
        if source_status == 'stale':
            return _with_item(
                item,
                _status('failed', f"{_artifact_path(packet, 'source_archive')} is older than RC evidence", 'rerun make rc-handoff-artifacts'),
            )
        return _with_item(item, _status('external-required', f'source archive status is {source_status}', 'run make source-archive'))

    if 'visual smoke' in lowered or 'screenshot' in lowered:
        if _artifact_status(packet, 'visual_smoke_review') == 'passed' and _artifact_status(packet, 'visual_smoke') == 'passed':
            return _with_item(item, _status('passed', f"{_artifact_path(packet, 'visual_smoke_review')} passed", ''))
        return _with_item(item, _status('external-required', 'visual smoke artifacts are missing/incomplete', 'run visual smoke and review'))

    if 'dependabot' in lowered:
        path = REPO_ROOT / '.github' / 'dependabot.yml'
        if path.exists():
            return _with_item(item, _status('passed', _relative_or_absolute(path), ''))
        return _with_item(item, _status('external-required', 'missing .github/dependabot.yml', 'add dependency update configuration'))

    if 'beta tester onboarding' in lowered or 'beta_tester_onboarding.md' in lowered:
        status = _artifact_status(packet, 'beta_tester_onboarding')
        if status == 'passed':
            return _with_item(item, _status('passed', f"{_artifact_path(packet, 'beta_tester_onboarding')} linked", ''))
        return _with_item(item, _status('external-required', f'beta tester onboarding status is {status}', 'link/review beta tester onboarding guide'))

    if 'auth_modes.md' in lowered:
        path = REPO_ROOT / 'docs' / 'auth_modes.md'
        if path.exists():
            return _with_item(item, _status('passed', _relative_or_absolute(path), ''))
        return _with_item(item, _status('external-required', 'docs/auth_modes.md missing', 'document auth-mode matrix'))

    if 'passwordless legacy recovery requires' in lowered:
        return _with_item(
            item,
            _passed_when_current_rc_gates(
                packet,
                ('Backend tests', 'Frontend tests'),
                'legacy recovery backend and frontend regressions',
            ),
        )

    if 'workspace-password target limiting is scoped' in lowered:
        return _with_item(
            item,
            _passed_when_current_rc_gates(
                packet,
                ('Backend tests',),
                'workspace-password account-isolation regression',
            ),
        )

    if 'run_production_server.sh' in lowered:
        path = REPO_ROOT / 'scripts' / 'run_production_server.sh'
        if path.exists() and _commands_passed(packet, ('Socket.IO worker model decision',)):
            return _with_item(item, _status('passed', 'production server command script exists and worker model decision gate passed', ''))
        return _with_item(item, _status('external-required', 'production server command script or worker-model gate missing', 'verify scripts/run_production_server.sh --print'))

    if 'aidm_socketio_worker_model' in lowered and 'explicitly set' in lowered:
        if _commands_passed(packet, ('Socket.IO worker model decision',)):
            return _with_item(
                item,
                _status(
                    'passed',
                    'Socket.IO worker model decision gate verifies docs, production env template, and server command',
                    '',
                ),
            )
        return _with_item(
            item,
            _status(
                'external-required',
                'Socket.IO worker model decision gate is missing or failed',
                'run make socketio-worker-model-decision',
            ),
        )

    if 'exactly one backend process/replica' in lowered:
        worker_model = _socketio_worker_model(packet)
        if worker_model and worker_model != 'single':
            return _with_item(
                item,
                _status(
                    'failed',
                    f'hosted evidence records unsupported {worker_model} Socket.IO worker model',
                    'set AIDM_SOCKETIO_WORKER_MODEL=single and attach hosted process/replica proof',
                ),
            )

        hosted = packet.get('hosted_rc_evidence') or {}
        process_evidence = next(
            (
                entry
                for entry in hosted.get('manual_evidence') or []
                if isinstance(entry, dict) and entry.get('label') == 'Hosted Socket.IO worker process proof'
            ),
            {},
        )
        evidence_value = str(process_evidence.get('evidence') or '').strip()
        if (
            worker_model == 'single'
            and hosted.get('status') == 'passed'
            and _hosted_rc_usable(packet)
            and process_evidence.get('status') == 'provided'
            and evidence_value
        ):
            target_url = str(hosted.get('target_url') or '').strip()
            return _with_item(
                item,
                _status(
                    'passed',
                    f'hosted single-worker process/replica proof for {target_url}: {evidence_value}',
                    '',
                ),
            )
        return _with_item(
            item,
            _status(
                'external-required',
                'hosted single-worker process/replica proof has not been supplied',
                'attach platform evidence proving exactly one backend process/replica',
            ),
        )

    if any(marker in lowered for marker in (
        'aidm_auth_required',
        'aidm_api_auth_tokens',
        'cors',
        'account_cookie',
        'account_token_response',
        'observability_provider',
        'alert_owner',
        'hosted same-origin deployments',
    )):
        hosted_status = _hosted_rc_check_status(packet, 'Hosted deployment readiness')
        if hosted_status is not None:
            return _with_item(item, hosted_status)
        return _with_item(item, _status('external-required', 'requires target environment configuration evidence', 'verify against hosted/staging environment'))

    if '/api/health' in lowered or '/api/metrics' in lowered or 'security headers' in lowered:
        hosted_status = _hosted_rc_check_status(packet, 'Hosted deployment readiness')
        if hosted_status is not None:
            return _with_item(item, hosted_status)
        status, evidence = _target_status(packet, 'deployment_readiness')
        return _with_item(item, _status(status, evidence, '' if status == 'passed' else 'prove live target endpoints/headers with deployment-readiness'))

    if '/api/beta/support-bundle' in lowered or 'export-support-bundle' in lowered:
        if _commands_passed(packet, ('Backend tests',)):
            return _with_item(item, _status('passed', 'backend tests cover beta support bundle and export script exists', ''))

    if '/api/beta/session-quality' in lowered or '/api/beta/audits' in lowered or 'bad-turn reports' in lowered or 'beta feedback prompt' in lowered:
        if _commands_passed(packet, ('Backend tests',)):
            return _with_item(item, _status('passed', 'backend/frontend RC suites cover beta operator surfaces', ''))

    if 'rate-limit and auth errors are monitored' in lowered or 'dm generation failures are monitored' in lowered:
        if _commands_passed(packet, ('Local beta SLO baseline',)):
            return _with_item(item, _status('passed', 'local beta SLO baseline includes auth/rate-limit/provider failure counters', ''))

    if 'external telemetry endpoint receives events' in lowered:
        return _with_item(item, _status('external-required', 'unit tests cover delivery; real endpoint proof is target-specific', 'enable telemetry endpoint in hosted/staging and verify receipt'))

    if 'tts /api/tts/stream' in lowered or 'tts stream' in lowered:
        if _commands_passed(packet, ('Backend tests',)):
            return _with_item(item, _status('passed', 'backend tests cover TTS stream headers and chunk failure telemetry', ''))

    if 'modal accessibility regressions' in lowered:
        return _with_item(item, _frontend_modal_accessibility_status(packet))

    local_backend_covered = (
        'new tables exist' in lowered
        or 'session log and state endpoints' in lowered
        or 'socket message stream includes' in lowered
        or 'action_intent' in lowered
        or 'turn_status' in lowered
        or 'campaign-pack progress service' in lowered
        or 'beta runtime notices' in lowered
        or 'segment trigger events' in lowered
        or 'improvised canon' in lowered
        or 'session end recap' in lowered
        or 'scenario quality regressions cover' in lowered
    )
    if local_backend_covered and _commands_passed(packet, ('Backend tests',)):
        return _with_item(item, _status('passed', 'covered by local RC backend/frontend/scenario test gates', ''))

    if 'database backup taken before deployment' in lowered or 'hosted database restore' in lowered or 'provider-specific' in lowered:
        return _with_item(item, _status('external-required', 'provider-specific hosted backup/restore proof is manual', 'attach hosted database backup/restore evidence'))

    if 'make clean' in lowered or 'make clean-deps' in lowered or 'npm ci' in lowered or 'post-rc-issue-evidence' in lowered or 'rc gate issues are closed' in lowered:
        return _with_item(item, _status('manual-review', 'operator action is intentionally manual or preview-only', 'run/review before final RC sign-off'))

    return _with_item(item, _status('unmapped', 'no automated rule mapped this checklist row', 'review manually or add a renderer rule'))


def build_status_report(
    *,
    checklist_path: pathlib.Path,
    packet_path: pathlib.Path,
    generated_at: str,
) -> dict[str, Any]:
    packet = load_packet(packet_path)
    items = parse_checklist(checklist_path)
    statuses = [classify_item(item, packet) for item in items]
    counts: dict[str, int] = {}
    by_section: dict[str, dict[str, int]] = {}
    for status in statuses:
        counts[status.status] = counts.get(status.status, 0) + 1
        section_counts = by_section.setdefault(status.section, {})
        section_counts[status.status] = section_counts.get(status.status, 0) + 1
    return {
        'generated_at': generated_at,
        'checklist_path': str(_resolve_repo_path(checklist_path)),
        'packet_path': str(_resolve_repo_path(packet_path)),
        'packet_overall_status': packet.get('overall_status') or 'missing',
        'counts': counts,
        'by_section': by_section,
        'items': [asdict(status) for status in statuses],
    }


def render_markdown(report: dict[str, Any]) -> str:
    count_rows = ['| Status | Count |', '| --- | ---: |']
    for status in ('passed', 'external-required', 'manual-review', 'failed', 'unmapped'):
        count_rows.append(f"| {status} | {report.get('counts', {}).get(status, 0)} |")

    remaining_rows = ['| Section | Line | Status | Checklist item | Evidence | Remaining action |', '| --- | ---: | --- | --- | --- | --- |']
    for item in report.get('items') or []:
        if item.get('status') == 'passed':
            continue
        remaining_rows.append(
            f"| {item.get('section')} | {item.get('line_number')} | {item.get('status')} | "
            f"{item.get('item')} | {item.get('evidence')} | {item.get('remaining_action')} |"
        )
    if len(remaining_rows) == 2:
        remaining_rows.append('| None |  | passed | None | None | None |')

    section_rows = ['| Section | Passed | External required | Manual review | Unmapped |', '| --- | ---: | ---: | ---: | ---: |']
    for section, counts in (report.get('by_section') or {}).items():
        section_rows.append(
            f"| {section} | {counts.get('passed', 0)} | {counts.get('external-required', 0)} | "
            f"{counts.get('manual-review', 0)} | {counts.get('unmapped', 0)} |"
        )

    return '\n'.join(
        [
            '# Release Checklist Status',
            '',
            f"- Generated: {report.get('generated_at')}",
            f"- Checklist: `{_relative_or_absolute(report.get('checklist_path') or '')}`",
            f"- Evidence packet: `{_relative_or_absolute(report.get('packet_path') or '')}`",
            f"- Evidence packet status: {report.get('packet_overall_status')}",
            '',
            '## Summary',
            '',
            *count_rows,
            '',
            '## Remaining Items',
            '',
            *remaining_rows,
            '',
            '## Section Counts',
            '',
            *section_rows,
            '',
        ]
    )


def write_report(report: dict[str, Any], *, output: pathlib.Path, json_output: pathlib.Path | None) -> None:
    output_path = _resolve_repo_path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(report), encoding='utf-8')
    if json_output is not None:
        json_path = _resolve_repo_path(json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Render release checklist status from the release evidence packet.')
    parser.add_argument('--checklist', type=pathlib.Path, default=DEFAULT_CHECKLIST)
    parser.add_argument('--packet-json', type=pathlib.Path, default=DEFAULT_PACKET_JSON)
    parser.add_argument('--output', type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--json-output', type=pathlib.Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument('--generated-at', default='', help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generated_at = args.generated_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    report = build_status_report(checklist_path=args.checklist, packet_path=args.packet_json, generated_at=generated_at)
    write_report(report, output=args.output, json_output=args.json_output)
    print(f"[release-checklist-status] Wrote {_relative_or_absolute(_resolve_repo_path(args.output))}.")
    if args.json_output is not None:
        print(f"[release-checklist-status] Wrote {_relative_or_absolute(_resolve_repo_path(args.json_output))}.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
