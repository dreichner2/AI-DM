#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff.json'
DEFAULT_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff-status.md'
DEFAULT_JSON_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff-status.json'
DEFAULT_PACKET_JSON = REPO_ROOT / 'tmp' / 'release' / 'release-evidence-packet.json'
DEFAULT_DRAFT_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff.draft.json'
DEFAULT_ACTION_PLAN_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff-action-plan.md'
DEFAULT_ACTION_PLAN_JSON_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff-action-plan.json'


@dataclass(frozen=True)
class SignoffItemSpec:
    key: str
    title: str
    issues: str
    details: str
    allow_not_applicable: bool = False


@dataclass(frozen=True)
class SignoffItemStatus:
    key: str
    title: str
    issues: str
    status: str
    evidence: str
    notes: str
    complete: bool
    errors: tuple[str, ...]


@dataclass(frozen=True)
class SignoffActionSpec:
    key: str
    category: str
    next_action: str
    command: str
    evidence_to_record: str
    required_inputs: tuple[str, ...] = ()


ITEM_SPECS: tuple[SignoffItemSpec, ...] = (
    SignoffItemSpec(
        'clean_signed_off_worktree',
        'Clean signed-off worktree RC evidence',
        '#3',
        'RC evidence report generated from the final signed-off commit with a clean worktree.',
    ),
    SignoffItemSpec(
        'github_actions_aidm_ci',
        'GitHub Actions AIDM CI run URL',
        '#3',
        'Successful AIDM CI run URL for the signed-off commit.',
    ),
    SignoffItemSpec(
        'github_actions_closed_beta_rc',
        'GitHub Actions Closed Beta RC run URL',
        '#3',
        'Successful manual Closed Beta RC workflow run URL for the signed-off commit.',
    ),
    SignoffItemSpec(
        'github_actions_rc_artifact',
        'Closed Beta RC evidence artifact',
        '#3 #9',
        (
            'Artifact URL or run artifact reference containing the RC report, issue snippets, '
            'release packet, source archive, security/export-import evidence, GitHub Actions '
            'evidence, and visual-smoke artifacts.'
        ),
    ),
    SignoffItemSpec(
        'hosted_env_config',
        'Hosted environment configuration proof',
        '#3 #5 #7 #8',
        'Evidence for auth-required mode, strong token, explicit CORS, cookie settings, worker model, observability provider, and alert owner.',
    ),
    SignoffItemSpec(
        'hosted_deployment_readiness',
        'Hosted deployment readiness evidence',
        '#3 #5 #8',
        'Passing hosted/staging deployment-readiness report for health, metrics, headers, auth, CORS, and provider checks.',
    ),
    SignoffItemSpec(
        'hosted_cookie_auth',
        'Hosted cookie-auth smoke evidence',
        '#5 #7',
        'Passing hosted browser cookie-auth, CSRF, logout, role refresh, and Socket.IO auth smoke evidence.',
    ),
    SignoffItemSpec(
        'hosted_non_admin_forbidden',
        'Hosted non-admin forbidden smoke evidence',
        '#5',
        'Passing hosted non-admin forbidden-response evidence for combat, bestiary, and beta operator endpoints.',
    ),
    SignoffItemSpec(
        'hosted_export_import',
        'Hosted session export/import smoke evidence',
        '#6',
        'Passing target-environment session export/import smoke evidence.',
    ),
    SignoffItemSpec(
        'hosted_backup_restore',
        'Hosted database backup/restore proof',
        '#6',
        'Provider-specific hosted/staging backup and restore drill evidence.',
    ),
    SignoffItemSpec(
        'hosted_socketio_worker_process',
        'Hosted Socket.IO worker process proof',
        '#7',
        'Platform process/config evidence showing the documented RC worker model and worker count.',
    ),
    SignoffItemSpec(
        'multi_worker_socketio_staging',
        'Multi-worker Socket.IO staging proof',
        '#7',
        'Sticky-session or message-queue staging proof when the target is not RC1 single-worker mode.',
        allow_not_applicable=True,
    ),
    SignoffItemSpec(
        'hosted_beta_slo_baseline',
        'Hosted beta SLO baseline',
        '#8',
        'Target-environment beta SLO baseline generated before tester expansion.',
    ),
    SignoffItemSpec(
        'hosted_external_telemetry',
        'External telemetry receipt proof',
        '#8',
        'Proof that the configured external telemetry endpoint receives events when enabled.',
    ),
    SignoffItemSpec(
        'source_archive_attachment',
        'Source archive attached to RC issue or release',
        '#9',
        'Link/path showing the generated source archive and checksum were attached for reviewer download.',
    ),
    SignoffItemSpec(
        'rc_issue_closure_review',
        'RC gate issue closure review',
        '#3 #4 #5 #6 #7 #8 #9',
        'Evidence that RC gate issues were closed with generated issue snippets or template entries.',
    ),
    SignoffItemSpec(
        'frontend_npm_ci',
        'Frontend npm ci lockfile install',
        '#4',
        'Command output or workflow evidence that frontend dependencies installed from package-lock.json.',
    ),
    SignoffItemSpec(
        'make_clean',
        'Packaging clean command review',
        '#9',
        'Evidence that make clean was run or reviewed before source packaging.',
    ),
    SignoffItemSpec(
        'make_clean_deps',
        'Packaging clean-deps command review',
        '#9',
        'Evidence that dependency folders were removed or intentionally excluded before source handoff.',
    ),
)

ACTION_SPECS: dict[str, SignoffActionSpec] = {
    'clean_signed_off_worktree': SignoffActionSpec(
        key='clean_signed_off_worktree',
        category='Release candidate freeze',
        next_action='Commit the RC changes, rerun the full local RC gate, and regenerate handoff artifacts from a clean signed-off worktree.',
        command='git status --short && make closed-beta-rc && make rc-handoff-artifacts',
        evidence_to_record='tmp/release/rc-evidence.md showing Status: passed and Worktree: clean for the signed-off commit.',
        required_inputs=('<signed-off-commit-sha>', 'tmp/release/rc-evidence.md'),
    ),
    'github_actions_aidm_ci': SignoffActionSpec(
        key='github_actions_aidm_ci',
        category='GitHub Actions',
        next_action='Record a successful AIDM CI run URL for the final signed-off commit.',
        command=(
            'make github-actions-evidence GITHUB_ACTIONS_EVIDENCE_ARGS='
            '"--auto-gh --include-gh-details --verify-closed-beta-rc-artifact-contents"'
        ),
        evidence_to_record='AIDM CI run URL.',
    ),
    'github_actions_closed_beta_rc': SignoffActionSpec(
        key='github_actions_closed_beta_rc',
        category='GitHub Actions',
        next_action='Run the manual Closed Beta RC workflow and record its successful run URL.',
        command=(
            'GitHub Actions > Closed Beta RC > Run workflow; then rerun '
            'make github-actions-evidence GITHUB_ACTIONS_EVIDENCE_ARGS='
            '"--auto-gh --include-gh-details --verify-closed-beta-rc-artifact-contents".'
        ),
        evidence_to_record='Closed Beta RC workflow run URL.',
    ),
    'github_actions_rc_artifact': SignoffActionSpec(
        key='github_actions_rc_artifact',
        category='GitHub Actions',
        next_action=(
            'Verify the Closed Beta RC workflow artifact contains the required RC evidence bundle, '
            'including security/export-import evidence and visual-smoke artifacts.'
        ),
        command=(
            'make github-actions-evidence GITHUB_ACTIONS_EVIDENCE_ARGS='
            '"--auto-gh --include-gh-details --verify-closed-beta-rc-artifact-contents"'
        ),
        evidence_to_record='tmp/release/github-actions-evidence.md with artifact content status: passed.',
    ),
    'hosted_env_config': SignoffActionSpec(
        key='hosted_env_config',
        category='Hosted target',
        next_action='Verify hosted/staging env flags for auth, CORS, cookies, worker model, and observability.',
        command=(
            'make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> '
            '--target-url <target-url> --auth-token <token> '
            '--evidence-report tmp/release/deployment-readiness-evidence.md"'
        ),
        evidence_to_record='Hosted deployment-readiness report path or URL.',
        required_inputs=('<target-env>', '<target-url>', '<token>'),
    ),
    'hosted_deployment_readiness': SignoffActionSpec(
        key='hosted_deployment_readiness',
        category='Hosted target',
        next_action='Run deployment readiness against the actual hosted/staging target.',
        command=(
            'make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> '
            '--target-url <target-url> --auth-token <token> '
            '--evidence-report tmp/release/deployment-readiness-evidence.md"'
        ),
        evidence_to_record='tmp/release/deployment-readiness-evidence.md from the hosted/staging run.',
        required_inputs=('<target-env>', '<target-url>', '<token>'),
    ),
    'hosted_cookie_auth': SignoffActionSpec(
        key='hosted_cookie_auth',
        category='Hosted target',
        next_action='Run the hosted cookie-auth browser smoke against the target.',
        command=(
            'make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_SMOKE_ARGS="--target-url <target-url> '
            '--account-intent signup --evidence-report tmp/release/hosted-cookie-auth-evidence.md"'
        ),
        evidence_to_record='tmp/release/hosted-cookie-auth-evidence.md from live-target mode.',
        required_inputs=('<target-url>',),
    ),
    'hosted_non_admin_forbidden': SignoffActionSpec(
        key='hosted_non_admin_forbidden',
        category='Hosted target',
        next_action='Run non-admin forbidden-response smoke against hosted/staging.',
        command=(
            'make security-forbidden-smoke SECURITY_FORBIDDEN_SMOKE_ARGS="--target-url <target-url> '
            '--account-token <non-admin-token> --workspace-id <workspace-id> --campaign-id <campaign-id> '
            '--session-id <session-id> --evidence-report tmp/release/security-forbidden-evidence.md"'
        ),
        evidence_to_record='tmp/release/security-forbidden-evidence.md from live-target mode.',
        required_inputs=('<target-url>', '<non-admin-token>', '<workspace-id>', '<campaign-id>', '<session-id>'),
    ),
    'hosted_export_import': SignoffActionSpec(
        key='hosted_export_import',
        category='Hosted target',
        next_action='Run session export/import smoke against hosted/staging.',
        command=(
            'make session-export-import-smoke SESSION_EXPORT_IMPORT_SMOKE_ARGS="--target-url <target-url> '
            '--auth-token <token> --workspace-id <workspace-id> --session-id <session-id> '
            '--player-id <player-id> --evidence-report tmp/release/export-import-evidence.md"'
        ),
        evidence_to_record='tmp/release/export-import-evidence.md from live-target mode.',
        required_inputs=('<target-url>', '<token>', '<workspace-id>', '<session-id>', '<player-id>'),
    ),
    'hosted_backup_restore': SignoffActionSpec(
        key='hosted_backup_restore',
        category='Manual hosted proof',
        next_action='Attach provider-specific hosted/staging backup and restore drill evidence.',
        command='Run the provider backup/restore drill, then pass --hosted-backup-restore-evidence <link-or-path> to hosted RC evidence.',
        evidence_to_record='Backup/restore log, provider snapshot/restore proof, or runbook link.',
        required_inputs=('<link-or-path>',),
    ),
    'hosted_socketio_worker_process': SignoffActionSpec(
        key='hosted_socketio_worker_process',
        category='Manual hosted proof',
        next_action='Attach platform process/config proof for the documented Socket.IO worker model.',
        command='Pass --hosted-worker-process-evidence <link-or-path> to hosted RC evidence.',
        evidence_to_record='Platform config/log/screenshot proving worker model and worker count.',
        required_inputs=('<link-or-path>',),
    ),
    'multi_worker_socketio_staging': SignoffActionSpec(
        key='multi_worker_socketio_staging',
        category='Manual hosted proof',
        next_action='Mark not applicable only after worker-process proof confirms RC1 single-worker mode; otherwise attach sticky/message-queue staging proof.',
        command='For multi-worker targets, rerun deployment readiness with --socketio-staging-proof <link-or-path>.',
        evidence_to_record='Single-worker proof or sticky/message-queue staging proof.',
        required_inputs=('<link-or-path>',),
    ),
    'hosted_beta_slo_baseline': SignoffActionSpec(
        key='hosted_beta_slo_baseline',
        category='Hosted target',
        next_action='Render beta SLO baseline from the hosted/staging target.',
        command=(
            'make beta-slo-baseline BETA_SLO_BASELINE_ARGS="--target-url <target-url> '
            '--auth-token <token> --workspace-id <workspace-id> --release RC1 --environment staging"'
        ),
        evidence_to_record='tmp/release/beta-slo-baseline.md from hosted/staging metrics.',
        required_inputs=('<target-url>', '<token>', '<workspace-id>'),
    ),
    'hosted_external_telemetry': SignoffActionSpec(
        key='hosted_external_telemetry',
        category='Manual hosted proof',
        next_action='Verify the configured external telemetry endpoint receives events.',
        command='Enable the hosted telemetry endpoint, then pass --external-telemetry-receipt <link-or-path> to hosted RC evidence.',
        evidence_to_record='Telemetry provider receipt, log, dashboard link, or event sample reference.',
        required_inputs=('<link-or-path>',),
    ),
    'source_archive_attachment': SignoffActionSpec(
        key='source_archive_attachment',
        category='Manual release proof',
        next_action='Attach the generated source archive and checksum to the RC issue, workflow artifact, or GitHub Release.',
        command='Attach tmp/release/aidm-source-*.tar.gz and matching .sha256 sidecar.',
        evidence_to_record='Attachment URL/path plus checksum.',
    ),
    'rc_issue_closure_review': SignoffActionSpec(
        key='rc_issue_closure_review',
        category='Manual release proof',
        next_action='Review/post generated issue evidence snippets before closing RC gate issues.',
        command='make post-rc-issue-evidence; add POST_RC_ISSUE_EVIDENCE_ARGS="--post" only after review.',
        evidence_to_record='Links to reviewed/posted issue comments for #3 through #9.',
    ),
    'frontend_npm_ci': SignoffActionSpec(
        key='frontend_npm_ci',
        category='Local packaging proof',
        next_action='Record evidence that frontend dependencies install from package-lock.json.',
        command='cd aidm_frontend && npm ci',
        evidence_to_record='Command output or CI run proving npm ci succeeded.',
    ),
    'make_clean': SignoffActionSpec(
        key='make_clean',
        category='Local packaging proof',
        next_action='Run or review make clean before final source packaging.',
        command='make clean',
        evidence_to_record='Command output or reviewed artifact-cleanliness note.',
    ),
    'make_clean_deps': SignoffActionSpec(
        key='make_clean_deps',
        category='Local packaging proof',
        next_action='Run or review clean dependency removal before source-only handoff.',
        command='make clean-deps',
        evidence_to_record='Command output or documented decision not to remove local dependency folders before commit.',
    ),
}

VALID_STATUSES = {'pending', 'provided', 'not_applicable'}
MISSING_VALUES = {'', 'missing', 'none', 'not checked', 'placeholder', 'tbd', 'todo', 'unknown'}
LOCAL_TARGETS = {'isolated local runtime', 'not checked'}
PLACEHOLDER_EVIDENCE_MARKERS = (
    '.example.',
    'example.com',
    'example.test',
    'github.com/example',
    'localhost',
    '127.0.0.1',
    'isolated local runtime',
)
TARGET_ALIGNED_SIGNOFF_ITEMS = {
    'hosted_env_config',
    'hosted_deployment_readiness',
    'hosted_cookie_auth',
    'hosted_non_admin_forbidden',
    'hosted_export_import',
    'hosted_beta_slo_baseline',
}
PASSED_LOCAL_REPORT_SIGNOFF_ITEMS = {
    'hosted_env_config',
    'hosted_deployment_readiness',
    'hosted_cookie_auth',
    'hosted_non_admin_forbidden',
    'hosted_export_import',
}
LIVE_TARGET_LOCAL_REPORT_SIGNOFF_ITEMS = {
    'hosted_cookie_auth',
    'hosted_non_admin_forbidden',
    'hosted_export_import',
}
SHA256_RE = re.compile(r'\b[a-fA-F0-9]{64}\b')


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


def _normalize_status(value: Any) -> str:
    if value is None:
        return 'pending'
    status = str(value).strip().lower().replace('-', '_').replace(' ', '_')
    return status or 'pending'


def _text(value: Any) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _real_text(value: Any) -> str:
    text = _text(value)
    return '' if text.lower() in MISSING_VALUES else text


def _has_placeholder(value: Any) -> bool:
    text = _real_text(value)
    return not text or '<' in text or '>' in text


def _is_real_url(value: Any) -> bool:
    text = _real_text(value)
    if not text or '<' in text or '>' in text:
        return False
    return text.lower().startswith(('http://', 'https://'))


def _is_hosted_target(value: Any) -> bool:
    text = _real_text(value)
    lowered = text.lower()
    if not _is_real_url(text):
        return False
    if lowered in LOCAL_TARGETS or '.example.' in lowered:
        return False
    return not lowered.startswith(('http://127.', 'http://localhost', 'https://127.', 'https://localhost'))


def _normalize_target_url(value: Any) -> str:
    return _real_text(value).rstrip('/')


def _metadata_key(label: str) -> str:
    return label.strip().lower().replace(' ', '_').replace('-', '_').replace('/', '_')


def _markdown_report_metadata(path: pathlib.Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line.startswith('- ') or ':' not in line:
            continue
        label, value = line[2:].split(':', 1)
        key = _metadata_key(label)
        if key in {'status', 'target_url', 'mode', 'worktree', 'commit'}:
            metadata[key] = value.strip().strip('`')
    return metadata


def _json_report_metadata(path: pathlib.Path) -> dict[str, str]:
    parsed = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(parsed, dict):
        return {}
    metadata = {
        'status': _real_text(parsed.get('status')),
        'target_url': _real_text(parsed.get('target_url')),
        'mode': _real_text(parsed.get('mode')),
        'worktree': _real_text(parsed.get('worktree')),
        'commit': _real_text(parsed.get('commit')),
    }
    options = parsed.get('options')
    if isinstance(options, dict) and not metadata['target_url']:
        metadata['target_url'] = _real_text(options.get('target_url'))
    return {key: value for key, value in metadata.items() if value}


def _local_evidence_path(evidence: str, *, manifest_path: pathlib.Path) -> pathlib.Path | None:
    text = _real_text(evidence)
    if not text or _is_real_url(text):
        return None
    candidate = pathlib.Path(text)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    manifest_in_repo = False
    try:
        manifest_path.relative_to(REPO_ROOT)
        manifest_in_repo = True
    except ValueError:
        manifest_in_repo = False
    candidates = [manifest_path.parent / candidate]
    if manifest_in_repo:
        candidates.append(REPO_ROOT / candidate)
    return next((path for path in candidates if path.exists()), None)


def _evidence_report_target_url(evidence: str, *, manifest_path: pathlib.Path) -> str:
    return _evidence_report_metadata(evidence, manifest_path=manifest_path).get('target_url', '')


def _evidence_report_metadata(evidence: str, *, manifest_path: pathlib.Path) -> dict[str, str]:
    path = _local_evidence_path(evidence, manifest_path=manifest_path)
    if path is None:
        return {}
    try:
        if path.suffix.lower() == '.json':
            return _json_report_metadata(path)
        return _markdown_report_metadata(path)
    except (OSError, json.JSONDecodeError):
        return {}


def _section(packet: dict[str, Any], key: str) -> dict[str, Any]:
    value = packet.get(key) or {}
    return value if isinstance(value, dict) else {}


def _metadata(section: dict[str, Any]) -> dict[str, Any]:
    value = section.get('metadata') or {}
    return value if isinstance(value, dict) else {}


def _evidence_path(section: dict[str, Any]) -> str:
    return _real_text(section.get('path'))


def _set_item(manifest: dict[str, Any], key: str, *, status: str, evidence: str = '', notes: str = '') -> None:
    items = manifest.setdefault('items', {})
    item = items.setdefault(key, {})
    item['status'] = status
    item['evidence'] = evidence
    item['notes'] = notes or item.get('notes') or ''


def _set_provided(manifest: dict[str, Any], key: str, *, evidence: str, notes: str) -> None:
    if evidence:
        _set_item(manifest, key, status='provided', evidence=evidence, notes=notes)


def _automated_hosted_passed(section: dict[str, Any]) -> bool:
    return section.get('status') == 'passed' and _is_hosted_target(section.get('target_url'))


def _live_hosted_smoke_passed(section: dict[str, Any]) -> bool:
    return (
        section.get('status') == 'passed'
        and section.get('mode') == 'live-target'
        and _is_hosted_target(section.get('target_url'))
    )


def _github_actions_usable(section: dict[str, Any]) -> bool:
    return section.get('status') == 'passed' and section.get('freshness') != 'stale'


def _github_actions_allowed_for_signoff(section: dict[str, Any], signed_off_worktree: dict[str, Any]) -> bool:
    status = _text(signed_off_worktree.get('status')).lower()
    return _github_actions_usable(section) and (not status or status == 'passed')


def _github_actions_rc_artifact_reference(section: dict[str, Any]) -> str:
    artifact = section.get('closed_beta_rc_artifact')
    if not isinstance(artifact, dict):
        artifact = {}
    artifact_status = _text(section.get('closed_beta_rc_artifact_status') or artifact.get('status')).lower()
    content_status = _text(
        section.get('closed_beta_rc_artifact_content_status') or artifact.get('content_status')
    ).lower()
    artifact_url = _real_text(section.get('closed_beta_rc_artifact_url') or artifact.get('url'))
    if artifact_status == 'passed' and content_status == 'passed' and _is_real_url(artifact_url):
        return artifact_url
    return ''


def _local_artifact_passed(section: dict[str, Any]) -> bool:
    return section.get('status') == 'passed' and section.get('freshness') != 'stale'


def _manual_evidence_lookup(hosted_rc: dict[str, Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    manual = hosted_rc.get('manual_evidence') or hosted_rc.get('manual_items') or []
    if not isinstance(manual, list):
        return lookup
    for item in manual:
        if not isinstance(item, dict) or item.get('status') != 'provided':
            continue
        label = _text(item.get('label')).lower()
        evidence = _real_text(item.get('evidence'))
        if label and evidence:
            lookup[label] = evidence
    return lookup


def _load_manifest(path: pathlib.Path) -> tuple[dict[str, Any], str]:
    manifest_path = _resolve_repo_path(path)
    if not manifest_path.exists():
        return {}, ''
    try:
        parsed = json.loads(manifest_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return {}, f'invalid JSON: {exc}'
    if not isinstance(parsed, dict):
        return {}, 'manifest root must be a JSON object'
    return parsed, ''


def _load_json_object(path: pathlib.Path) -> dict[str, Any]:
    packet_path = _resolve_repo_path(path)
    try:
        parsed = json.loads(packet_path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise SystemExit(f'[operator-signoff] Packet not found: {_relative_or_absolute(packet_path)}') from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f'[operator-signoff] Invalid packet JSON: {exc}') from exc
    if not isinstance(parsed, dict):
        raise SystemExit('[operator-signoff] Packet root must be a JSON object.')
    return parsed


def _raw_items(manifest: dict[str, Any]) -> tuple[dict[str, Any], str]:
    raw = manifest.get('items') or {}
    if not isinstance(raw, dict):
        return {}, 'items must be a JSON object keyed by signoff item id'
    return raw, ''


def _manifest_metadata_errors(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if _has_placeholder(manifest.get('release')):
        errors.append('release must be filled')
    if _has_placeholder(manifest.get('commit')):
        errors.append('commit must be the signed-off commit SHA')
    if not _is_hosted_target(manifest.get('target_url')):
        errors.append('target_url must be a real hosted/staging URL, not localhost, example, or placeholder')
    if _has_placeholder(manifest.get('signed_by') or manifest.get('operator')):
        errors.append('signed_by must identify the operator signing off')
    if _has_placeholder(manifest.get('signed_at')):
        errors.append('signed_at must be filled with an ISO-8601 timestamp')
    return errors


def _evidence_errors(evidence: str) -> list[str]:
    errors: list[str] = []
    if _has_placeholder(evidence):
        errors.append('provided evidence must not be a placeholder')
        return errors
    lowered = evidence.lower()
    if any(marker in lowered for marker in PLACEHOLDER_EVIDENCE_MARKERS):
        errors.append('provided evidence must not use example, localhost, or isolated-runtime references')
    return errors


def _target_alignment_errors(
    spec: SignoffItemSpec,
    evidence: str,
    *,
    target_url: str,
    manifest_path: pathlib.Path,
) -> list[str]:
    if spec.key not in TARGET_ALIGNED_SIGNOFF_ITEMS:
        return []
    local_path = _local_evidence_path(evidence, manifest_path=manifest_path) if not _is_real_url(evidence) else None
    if not _is_real_url(evidence) and local_path is None:
        return [f'provided local evidence path does not exist: {evidence}']
    evidence_metadata = _evidence_report_metadata(evidence, manifest_path=manifest_path)
    evidence_target = evidence_metadata.get('target_url', '')
    if not evidence_target:
        if local_path is not None:
            return ['provided local evidence report is missing target_url']
        return []
    if not _is_hosted_target(evidence_target):
        return [f'provided evidence target_url is not hosted/staging: {evidence_target}']
    expected = _normalize_target_url(target_url)
    actual = _normalize_target_url(evidence_target)
    if expected and actual != expected:
        return [f'provided evidence target_url {actual} does not match manifest target_url {expected}']
    evidence_status = (evidence_metadata.get('status') or '').strip().lower()
    if spec.key in PASSED_LOCAL_REPORT_SIGNOFF_ITEMS and evidence_status != 'passed':
        return [f'provided evidence status is not passed: {evidence_status or "missing"}']
    evidence_mode = (evidence_metadata.get('mode') or '').strip().lower()
    if spec.key in LIVE_TARGET_LOCAL_REPORT_SIGNOFF_ITEMS and evidence_mode != 'live-target':
        return [f'provided evidence mode is not live-target: {evidence_mode or "missing"}']
    return []


def _clean_signed_off_evidence_errors(evidence: str, *, manifest_path: pathlib.Path) -> list[str]:
    if _is_real_url(evidence):
        return []
    local_path = _local_evidence_path(evidence, manifest_path=manifest_path)
    if local_path is None:
        return [f'provided local evidence path does not exist: {evidence}']
    metadata = _evidence_report_metadata(evidence, manifest_path=manifest_path)
    status = (metadata.get('status') or '').strip().lower()
    if status != 'passed':
        return [f'provided RC evidence status is not passed: {status or "missing"}']
    worktree = (metadata.get('worktree') or '').strip().lower()
    if worktree != 'clean':
        return [f'provided RC evidence worktree is not clean: {worktree or "missing"}']
    return []


def _source_archive_sha256(packet: dict[str, Any] | None) -> str:
    source_archive = _section(packet or {}, 'source_archive')
    sha256 = _real_text(source_archive.get('sha256')).lower()
    return sha256 if SHA256_RE.fullmatch(sha256) else ''


def _source_archive_attachment_errors(evidence: str, *, expected_sha256: str = '') -> list[str]:
    expected = expected_sha256.lower()
    if expected:
        if expected in evidence.lower():
            return []
        return [f'source archive attachment evidence must include current source archive sha256 {expected_sha256}']
    if SHA256_RE.search(evidence):
        return []
    return ['source archive attachment evidence must include a SHA256 checksum']


def _item_status(
    spec: SignoffItemSpec,
    raw_item: Any,
    *,
    target_url: str = '',
    manifest_path: pathlib.Path | None = None,
    source_archive_sha256: str = '',
) -> SignoffItemStatus:
    errors: list[str] = []
    if raw_item is None:
        raw: dict[str, Any] = {}
    elif isinstance(raw_item, str):
        raw = {'status': 'provided', 'evidence': raw_item}
    elif isinstance(raw_item, dict):
        raw = raw_item
    else:
        raw = {}
        errors.append('item value must be an object or evidence string')

    status = _normalize_status(raw.get('status'))
    evidence = _text(raw.get('evidence') or raw.get('url') or raw.get('path'))
    notes = _text(raw.get('notes') or raw.get('reason'))

    if status not in VALID_STATUSES:
        errors.append(f"status must be one of {', '.join(sorted(VALID_STATUSES))}")
    if status == 'provided' and not evidence:
        errors.append('provided status requires evidence')
    elif status == 'provided':
        errors.extend(_evidence_errors(evidence))
        if manifest_path is not None:
            if spec.key == 'clean_signed_off_worktree':
                errors.extend(_clean_signed_off_evidence_errors(evidence, manifest_path=manifest_path))
            errors.extend(_target_alignment_errors(spec, evidence, target_url=target_url, manifest_path=manifest_path))
        if spec.key == 'source_archive_attachment':
            errors.extend(_source_archive_attachment_errors(evidence, expected_sha256=source_archive_sha256))
    if status == 'not_applicable':
        if not spec.allow_not_applicable:
            errors.append('not_applicable is not allowed for this item')
        if spec.key == 'multi_worker_socketio_staging' and not evidence:
            errors.append('not_applicable status requires hosted worker-process evidence')
        elif not evidence and not notes:
            errors.append('not_applicable status requires evidence or notes')

    complete = status == 'provided' or (status == 'not_applicable' and spec.allow_not_applicable)
    if errors:
        complete = False

    return SignoffItemStatus(
        key=spec.key,
        title=spec.title,
        issues=spec.issues,
        status=status,
        evidence=evidence,
        notes=notes,
        complete=complete,
        errors=tuple(errors),
    )


def build_report(
    *,
    manifest_path: pathlib.Path,
    generated_at: str,
    packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_manifest = _resolve_repo_path(manifest_path)
    manifest, load_error = _load_manifest(resolved_manifest)
    raw_items, items_error = _raw_items(manifest) if not load_error else ({}, '')
    manifest_target_url = _text(manifest.get('target_url'))
    source_archive_sha256 = _source_archive_sha256(packet)
    statuses = [
        _item_status(
            spec,
            raw_items.get(spec.key),
            target_url=manifest_target_url,
            manifest_path=resolved_manifest,
            source_archive_sha256=source_archive_sha256,
        )
        for spec in ITEM_SPECS
    ]
    item_errors = [f"{item.key}: {error}" for item in statuses for error in item.errors]
    metadata_errors = _manifest_metadata_errors(manifest) if resolved_manifest.exists() and not load_error else []
    extra_items = sorted(str(key) for key in raw_items if key not in {spec.key for spec in ITEM_SPECS})
    errors = [error for error in (load_error, items_error) if error] + metadata_errors + item_errors

    if load_error:
        status = 'invalid'
    elif not resolved_manifest.exists():
        status = 'missing'
    elif errors:
        status = 'invalid'
    elif all(item.complete for item in statuses):
        status = 'passed'
    else:
        status = 'incomplete'

    complete_count = sum(1 for item in statuses if item.complete)
    pending_count = sum(1 for item in statuses if not item.complete)
    return {
        'generated_at': generated_at,
        'status': status,
        'manifest_path': str(resolved_manifest),
        'manifest_present': resolved_manifest.exists(),
        'release': _text(manifest.get('release')),
        'commit': _text(manifest.get('commit')),
        'target_url': _text(manifest.get('target_url')),
        'signed_by': _text(manifest.get('signed_by') or manifest.get('operator')),
        'signed_at': _text(manifest.get('signed_at')),
        'source_archive_sha256': source_archive_sha256,
        'required_count': len(ITEM_SPECS),
        'complete_count': complete_count,
        'pending_count': pending_count,
        'errors': errors,
        'extra_items': extra_items,
        'items': [asdict(item) for item in statuses],
    }


def render_markdown(report: dict[str, Any]) -> str:
    rows = ['| Key | Issues | Status | Evidence | Notes/errors |', '| --- | --- | --- | --- | --- |']
    for item in report.get('items') or []:
        notes = item.get('notes') or ''
        errors = '; '.join(item.get('errors') or [])
        if errors:
            notes = f'{notes}; {errors}' if notes else errors
        rows.append(
            f"| `{item.get('key')}` | {item.get('issues')} | {item.get('status')} | "
            f"{item.get('evidence') or ''} | {notes} |"
        )

    error_rows = ['| Error |', '| --- |']
    for error in report.get('errors') or []:
        error_rows.append(f'| {error} |')
    if len(error_rows) == 2:
        error_rows.append('| None |')

    extra_rows = ['| Extra item key |', '| --- |']
    for key in report.get('extra_items') or []:
        extra_rows.append(f'| `{key}` |')
    if len(extra_rows) == 2:
        extra_rows.append('| None |')

    return '\n'.join(
        [
            '# RC Operator Sign-Off Status',
            '',
            f"- Generated: {report.get('generated_at')}",
            f"- Status: {report.get('status')}",
            f"- Manifest: `{_relative_or_absolute(report.get('manifest_path') or '')}`",
            f"- Manifest present: {report.get('manifest_present')}",
            f"- Release: {report.get('release') or 'missing'}",
            f"- Commit: {report.get('commit') or 'missing'}",
            f"- Target URL: `{report.get('target_url') or 'missing'}`",
            f"- Signed by: {report.get('signed_by') or 'missing'}",
            f"- Signed at: {report.get('signed_at') or 'missing'}",
            f"- Source archive SHA256: `{report.get('source_archive_sha256') or 'missing'}`",
            f"- Required complete: {report.get('complete_count')}/{report.get('required_count')}",
            f"- Missing or invalid required items: {report.get('pending_count')}",
            '',
            '## Required Sign-Off Items',
            '',
            *rows,
            '',
            '## Errors',
            '',
            *error_rows,
            '',
            '## Extra Manifest Items',
            '',
            *extra_rows,
            '',
        ]
    )


def example_manifest() -> dict[str, Any]:
    return {
        'release': 'RC1',
        'commit': '<signed-off-commit-sha>',
        'target_url': 'https://<hosted-staging-target>',
        'signed_by': '<operator-name>',
        'signed_at': '<iso-8601-timestamp>',
        'items': {
            spec.key: {
                'status': 'not_applicable' if spec.allow_not_applicable else 'pending',
                'evidence': '',
                'notes': (
                    'Set to provided with sticky/message-queue proof if the target is not single-worker.'
                    if spec.allow_not_applicable
                    else spec.details
                ),
            }
            for spec in ITEM_SPECS
        },
    }


def draft_manifest_from_packet(
    packet: dict[str, Any],
    *,
    packet_path: pathlib.Path | None = None,
    generated_at: str,
) -> dict[str, Any]:
    manifest = example_manifest()
    manifest['draft_generated_at'] = generated_at
    manifest['draft_notes'] = (
        'Generated from the release evidence packet. Review before copying to '
        'tmp/release/operator-signoff.json; pending items still need external or manual proof.'
    )
    if packet_path is not None:
        manifest['generated_from_packet'] = _relative_or_absolute(_resolve_repo_path(packet_path))

    rc_evidence = _section(packet, 'rc_evidence')
    signed_off_worktree = _section(packet, 'signed_off_worktree')
    commit = _real_text(signed_off_worktree.get('commit') or rc_evidence.get('commit'))
    if commit:
        manifest['commit'] = commit

    hosted_rc = _section(packet, 'hosted_rc_evidence')
    hosted_rc_metadata = _metadata(hosted_rc)
    release = _real_text(hosted_rc_metadata.get('release') or hosted_rc.get('release'))
    if release:
        manifest['release'] = release
    target_url = _real_text(hosted_rc.get('target_url') or hosted_rc_metadata.get('target_url'))
    if _is_hosted_target(target_url):
        manifest['target_url'] = target_url

    _set_item(
        manifest,
        'multi_worker_socketio_staging',
        status='pending',
        notes=(
            'Pending until hosted Socket.IO worker-process proof confirms RC1 single-worker mode '
            'or supplies sticky/message-queue staging evidence.'
        ),
    )

    github_actions = _section(packet, 'github_actions')
    frontend_npm_ci = _section(packet, 'frontend_npm_ci')
    packaging_cleanup = _section(packet, 'packaging_cleanup')
    if (
        _text(signed_off_worktree.get('status')).lower() == 'passed'
        and rc_evidence.get('status') == 'passed'
        and _evidence_path(rc_evidence)
    ):
        _set_provided(
            manifest,
            'clean_signed_off_worktree',
            evidence=_evidence_path(rc_evidence),
            notes='Seeded from RC evidence generated after the latest clean signed-off worktree check.',
        )
    github_actions_usable = _github_actions_allowed_for_signoff(github_actions, signed_off_worktree)
    aidm_ci_url = _real_text(github_actions.get('aidm_ci_run_url'))
    closed_beta_rc_url = _real_text(github_actions.get('closed_beta_rc_run_url'))
    closed_beta_rc_artifact = _github_actions_rc_artifact_reference(github_actions)
    if github_actions_usable and _is_real_url(aidm_ci_url):
        _set_provided(
            manifest,
            'github_actions_aidm_ci',
            evidence=aidm_ci_url,
            notes='Seeded from GitHub Actions evidence; verify the run matches the final signed-off commit.',
        )
    if _local_artifact_passed(frontend_npm_ci) and _evidence_path(frontend_npm_ci):
        _set_provided(
            manifest,
            'frontend_npm_ci',
            evidence=_evidence_path(frontend_npm_ci),
            notes='Seeded from local frontend npm ci evidence generated after the latest RC evidence run.',
        )
    elif github_actions_usable and _is_real_url(aidm_ci_url):
        _set_provided(
            manifest,
            'frontend_npm_ci',
            evidence=aidm_ci_url,
            notes='Seeded from AIDM CI evidence; verify the run includes frontend lockfile install.',
        )
    if _local_artifact_passed(packaging_cleanup) and _evidence_path(packaging_cleanup):
        packaging_evidence = _evidence_path(packaging_cleanup)
        _set_provided(
            manifest,
            'make_clean',
            evidence=packaging_evidence,
            notes='Seeded from local packaging cleanup evidence generated after the latest RC evidence run.',
        )
        _set_provided(
            manifest,
            'make_clean_deps',
            evidence=packaging_evidence,
            notes='Seeded from local packaging cleanup evidence generated after the latest RC evidence run.',
        )
    if github_actions_usable and _is_real_url(closed_beta_rc_url):
        _set_provided(
            manifest,
            'github_actions_closed_beta_rc',
            evidence=closed_beta_rc_url,
            notes='Seeded from GitHub Actions evidence; verify the run matches the final signed-off commit.',
        )
    if github_actions_usable and closed_beta_rc_artifact:
        _set_provided(
            manifest,
            'github_actions_rc_artifact',
            evidence=closed_beta_rc_artifact,
            notes='Seeded from GitHub Actions artifact verification; content status is passed.',
        )

    deployment_readiness = _section(packet, 'deployment_readiness')
    if _automated_hosted_passed(deployment_readiness):
        readiness_path = _evidence_path(deployment_readiness)
        _set_provided(
            manifest,
            'hosted_deployment_readiness',
            evidence=readiness_path,
            notes='Hosted/staging deployment readiness passed against a non-local target.',
        )
        _set_provided(
            manifest,
            'hosted_env_config',
            evidence=readiness_path,
            notes='Deployment readiness evidence covers hosted auth, CORS, cookies, worker model, and observability flags.',
        )

    hosted_cookie_auth = _section(packet, 'hosted_cookie_auth')
    if _live_hosted_smoke_passed(hosted_cookie_auth):
        _set_provided(
            manifest,
            'hosted_cookie_auth',
            evidence=_evidence_path(hosted_cookie_auth),
            notes='Hosted cookie-auth smoke passed against a live target.',
        )

    security_forbidden = _section(packet, 'security_forbidden')
    if _live_hosted_smoke_passed(security_forbidden):
        _set_provided(
            manifest,
            'hosted_non_admin_forbidden',
            evidence=_evidence_path(security_forbidden),
            notes='Hosted non-admin forbidden smoke passed against a live target.',
        )

    export_import = _section(packet, 'export_import')
    if _live_hosted_smoke_passed(export_import):
        _set_provided(
            manifest,
            'hosted_export_import',
            evidence=_evidence_path(export_import),
            notes='Hosted session export/import smoke passed against a live target.',
        )

    beta_slo_baseline = _section(packet, 'beta_slo_baseline')
    if beta_slo_baseline.get('status') in {'passed', 'present'} and _is_hosted_target(
        beta_slo_baseline.get('target_url')
    ):
        _set_provided(
            manifest,
            'hosted_beta_slo_baseline',
            evidence=_evidence_path(beta_slo_baseline),
            notes='Hosted beta SLO baseline was generated for a non-local target.',
        )

    manual_evidence = _manual_evidence_lookup(hosted_rc)
    backup_restore = manual_evidence.get('hosted database backup/restore proof', '')
    worker_process = manual_evidence.get('hosted socket.io worker process proof', '')
    source_archive_attachment = manual_evidence.get('source archive attached to rc issue or release', '')

    _set_provided(
        manifest,
        'hosted_backup_restore',
        evidence=backup_restore,
        notes='Seeded from hosted RC manual evidence.',
    )
    _set_provided(
        manifest,
        'hosted_socketio_worker_process',
        evidence=worker_process,
        notes='Seeded from hosted RC manual evidence.',
    )
    _set_provided(
        manifest,
        'source_archive_attachment',
        evidence=source_archive_attachment,
        notes='Seeded from hosted RC manual evidence.',
    )

    worker_model = _real_text(hosted_rc_metadata.get('socket_io_worker_model') or hosted_rc.get('socketio_worker_model'))
    if worker_process and worker_model == 'single':
        _set_item(
            manifest,
            'multi_worker_socketio_staging',
            status='not_applicable',
            evidence=worker_process,
            notes='Hosted worker-process proof confirms RC1 single-worker mode.',
        )
    elif worker_process and _real_text(hosted_rc_metadata.get('socket_io_staging_proof')):
        _set_item(
            manifest,
            'multi_worker_socketio_staging',
            status='provided',
            evidence=_real_text(hosted_rc_metadata.get('socket_io_staging_proof')),
            notes='Hosted worker proof supplied sticky/message-queue staging evidence.',
        )

    return manifest


def write_draft_manifest(manifest: dict[str, Any], *, output: pathlib.Path) -> None:
    output_path = _resolve_repo_path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _action_spec_for(key: str) -> SignoffActionSpec:
    return ACTION_SPECS.get(
        key,
        SignoffActionSpec(
            key=key,
            category='Manual review',
            next_action='Provide evidence for this operator signoff item.',
            command='Review docs/rc_operator_signoff_manifest.example.json and fill this item.',
            evidence_to_record='Evidence link, path, or operator note.',
        ),
    )


def _source_archive_label(packet: dict[str, Any] | None) -> str:
    source = _section(packet or {}, 'source_archive')
    path = _real_text(source.get('path'))
    checksum = _real_text(source.get('sha256'))
    if path and checksum:
        return f'{_relative_or_absolute(path)} sha256:{checksum}'
    if path:
        return _relative_or_absolute(path)
    return ''


def _signed_off_worktree_context(packet: dict[str, Any] | None) -> dict[str, str]:
    signed_off = _section(packet or {}, 'signed_off_worktree')
    return {
        'status': _text(signed_off.get('status')) or 'missing',
        'worktree': _text(signed_off.get('worktree')) or 'missing',
        'commit': _text(signed_off.get('commit')) or '',
    }


def _github_context_url(packet: dict[str, Any] | None, key: str) -> str:
    github_actions = _section(packet or {}, 'github_actions')
    if github_actions.get('freshness') == 'stale' or github_actions.get('status') in {'failed', 'invalid'}:
        return ''
    url = _real_text(github_actions.get(key))
    return url if _is_real_url(url) else ''


def _needs_clean_signed_off_worktree(packet: dict[str, Any] | None) -> bool:
    signed_off = _section(packet or {}, 'signed_off_worktree')
    return _text(signed_off.get('status')).lower() != 'passed'


def build_action_plan(
    *,
    manifest: dict[str, Any],
    manifest_path: pathlib.Path,
    packet: dict[str, Any] | None,
    generated_at: str,
) -> dict[str, Any]:
    raw_items, items_error = _raw_items(manifest)
    statuses = (
        [
            _item_status(
                spec,
                raw_items.get(spec.key),
                target_url=_text(manifest.get('target_url')),
                manifest_path=_resolve_repo_path(manifest_path),
            )
            for spec in ITEM_SPECS
        ]
        if not items_error
        else []
    )
    source_archive = _source_archive_label(packet)
    signed_off_worktree = _signed_off_worktree_context(packet)
    action_items: list[dict[str, Any]] = []
    complete_items: list[dict[str, Any]] = []
    for status in statuses:
        action = _action_spec_for(status.key)
        payload = {
            **asdict(status),
            'category': action.category,
            'next_action': action.next_action,
            'command': action.command,
            'evidence_to_record': action.evidence_to_record,
            'required_inputs': list(action.required_inputs),
        }
        if status.key == 'source_archive_attachment' and source_archive:
            payload['source_archive'] = source_archive
            payload['next_action'] = f"{payload['next_action']} Current archive: {source_archive}."
        if not status.complete and status.key == 'github_actions_aidm_ci':
            current_ci_url = _github_context_url(packet, 'aidm_ci_run_url')
            if current_ci_url:
                payload['context_evidence'] = current_ci_url
                payload['next_action'] = (
                    f'Current AIDM CI evidence is recorded at {current_ci_url}, but final signoff still '
                    'requires a successful AIDM CI run for the clean signed-off commit.'
                )
        if (
            not status.complete
            and status.key in {'github_actions_closed_beta_rc', 'github_actions_rc_artifact'}
            and _needs_clean_signed_off_worktree(packet)
        ):
            payload['prerequisite'] = 'clean_signed_off_worktree'
            payload['next_action'] = (
                'Freeze and push a clean signed-off candidate before this GitHub Actions proof; then '
                f"{str(payload['next_action'])[:1].lower()}{str(payload['next_action'])[1:]}"
            )
        if status.complete:
            complete_items.append(payload)
        else:
            action_items.append(payload)

    return {
        'generated_at': generated_at,
        'status': 'passed' if not action_items and not items_error else 'action-required',
        'manifest_path': str(_resolve_repo_path(manifest_path)),
        'release': _text(manifest.get('release')),
        'commit': _text(manifest.get('commit')),
        'target_url': _text(manifest.get('target_url')),
        'source_archive': source_archive,
        'signed_off_worktree': signed_off_worktree,
        'required_count': len(ITEM_SPECS),
        'complete_count': len(complete_items),
        'pending_count': len(action_items),
        'errors': [items_error] if items_error else [],
        'actions': action_items,
        'complete_items': complete_items,
    }


def render_action_plan_markdown(plan: dict[str, Any]) -> str:
    action_rows = [
        '| Key | Issues | Category | Current | Next action | Command/source | Evidence to record | Inputs |',
        '| --- | --- | --- | --- | --- | --- | --- | --- |',
    ]
    for item in plan.get('actions') or []:
        current = str(item.get('status') or '')
        if item.get('context_evidence'):
            current = f"{current}; context: {item.get('context_evidence')}"
        if item.get('prerequisite'):
            current = f"{current}; prerequisite: {item.get('prerequisite')}"
        action_rows.append(
            f"| `{item.get('key')}` | {item.get('issues')} | {item.get('category')} | "
            f"{current} | {item.get('next_action')} | `{item.get('command') or ''}` | "
            f"{item.get('evidence_to_record') or ''} | {', '.join(item.get('required_inputs') or [])} |"
        )
    if len(action_rows) == 2:
        action_rows.append('| None | None | None | None | None | None | None | None |')

    complete_rows = ['| Key | Issues | Status | Evidence |', '| --- | --- | --- | --- |']
    for item in plan.get('complete_items') or []:
        complete_rows.append(
            f"| `{item.get('key')}` | {item.get('issues')} | {item.get('status')} | {item.get('evidence') or ''} |"
        )
    if len(complete_rows) == 2:
        complete_rows.append('| None | None | None | None |')

    error_rows = ['| Error |', '| --- |']
    for error in plan.get('errors') or []:
        error_rows.append(f'| {error} |')
    if len(error_rows) == 2:
        error_rows.append('| None |')

    return '\n'.join(
        [
            '# RC Operator Sign-Off Action Plan',
            '',
            f"- Generated: {plan.get('generated_at')}",
            f"- Status: {plan.get('status')}",
            f"- Manifest source: `{_relative_or_absolute(plan.get('manifest_path') or '')}`",
            f"- Release: {plan.get('release') or 'missing'}",
            f"- Current packet commit: {plan.get('commit') or 'missing'}",
            (
                f"- Signed-off worktree: {plan.get('signed_off_worktree', {}).get('status') or 'missing'}; "
                f"{plan.get('signed_off_worktree', {}).get('worktree') or 'missing'}"
            ),
            f"- Target URL: `{plan.get('target_url') or 'missing'}`",
            f"- Required complete: {plan.get('complete_count')}/{plan.get('required_count')}",
            f"- Pending actions: {plan.get('pending_count')}",
            f"- Source archive: `{plan.get('source_archive') or 'missing'}`",
            '',
            '## Pending Actions',
            '',
            *action_rows,
            '',
            '## Already Complete In Draft/Manifest',
            '',
            *complete_rows,
            '',
            '## Errors',
            '',
            *error_rows,
            '',
        ]
    )


def write_action_plan(plan: dict[str, Any], *, output: pathlib.Path, json_output: pathlib.Path | None) -> None:
    output_path = _resolve_repo_path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_action_plan_markdown(plan), encoding='utf-8')
    if json_output is not None:
        json_path = _resolve_repo_path(json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def write_report(report: dict[str, Any], *, output: pathlib.Path, json_output: pathlib.Path | None) -> None:
    output_path = _resolve_repo_path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(report), encoding='utf-8')
    if json_output is not None:
        json_path = _resolve_repo_path(json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Render final RC operator sign-off status from a JSON manifest.')
    parser.add_argument('--manifest', type=pathlib.Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--output', type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--json-output', type=pathlib.Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument('--packet-json', type=pathlib.Path, default=DEFAULT_PACKET_JSON)
    parser.add_argument('--require-complete', action='store_true')
    parser.add_argument('--write-template', type=pathlib.Path, default=None)
    parser.add_argument(
        '--write-draft-from-packet',
        type=pathlib.Path,
        nargs='?',
        const=DEFAULT_PACKET_JSON,
        default=None,
    )
    parser.add_argument('--draft-output', type=pathlib.Path, default=DEFAULT_DRAFT_OUTPUT)
    parser.add_argument('--write-action-plan', action='store_true')
    parser.add_argument('--action-plan-manifest', type=pathlib.Path, default=DEFAULT_DRAFT_OUTPUT)
    parser.add_argument('--action-plan-packet', type=pathlib.Path, default=DEFAULT_PACKET_JSON)
    parser.add_argument('--action-plan-output', type=pathlib.Path, default=DEFAULT_ACTION_PLAN_OUTPUT)
    parser.add_argument('--action-plan-json-output', type=pathlib.Path, default=DEFAULT_ACTION_PLAN_JSON_OUTPUT)
    parser.add_argument('--generated-at', default='', help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.write_template is not None:
        template_path = _resolve_repo_path(args.write_template)
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text(json.dumps(example_manifest(), indent=2, sort_keys=True) + '\n', encoding='utf-8')
        print(f'[operator-signoff] Wrote template to {_relative_or_absolute(template_path)}.')
        return 0

    generated_at = args.generated_at or _iso_now()
    if args.write_draft_from_packet is not None:
        packet_path = _resolve_repo_path(args.write_draft_from_packet)
        packet = _load_json_object(packet_path)
        draft = draft_manifest_from_packet(packet, packet_path=packet_path, generated_at=generated_at)
        write_draft_manifest(draft, output=args.draft_output)
        print(f'[operator-signoff] Wrote draft to {_relative_or_absolute(_resolve_repo_path(args.draft_output))}.')
        return 0

    if args.write_action_plan:
        packet_path = _resolve_repo_path(args.action_plan_packet)
        packet = _load_json_object(packet_path) if packet_path.exists() else {}
        manifest_path = _resolve_repo_path(args.action_plan_manifest)
        manifest, error = _load_manifest(manifest_path)
        if error:
            print(f'[operator-signoff] Invalid action-plan manifest: {error}', file=sys.stderr)
            return 1
        if not manifest:
            manifest = (
                draft_manifest_from_packet(packet, packet_path=packet_path, generated_at=generated_at)
                if packet
                else example_manifest()
            )
        plan = build_action_plan(
            manifest=manifest,
            manifest_path=manifest_path,
            packet=packet,
            generated_at=generated_at,
        )
        write_action_plan(plan, output=args.action_plan_output, json_output=args.action_plan_json_output)
        print(
            '[operator-signoff] Wrote action plan to '
            f'{_relative_or_absolute(_resolve_repo_path(args.action_plan_output))}.'
        )
        if args.action_plan_json_output is not None:
            print(
                '[operator-signoff] Wrote action plan JSON to '
                f'{_relative_or_absolute(_resolve_repo_path(args.action_plan_json_output))}.'
            )
        return 0

    packet_path = _resolve_repo_path(args.packet_json)
    packet = _load_json_object(packet_path) if packet_path.exists() else {}
    report = build_report(manifest_path=args.manifest, generated_at=generated_at, packet=packet)
    write_report(report, output=args.output, json_output=args.json_output)
    print(f"[operator-signoff] Wrote {_relative_or_absolute(_resolve_repo_path(args.output))}.")
    if args.json_output is not None:
        print(f"[operator-signoff] Wrote {_relative_or_absolute(_resolve_repo_path(args.json_output))}.")

    if report.get('status') == 'invalid':
        return 1
    if args.require_complete and report.get('status') != 'passed':
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
