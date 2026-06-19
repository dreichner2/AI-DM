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
DEFAULT_PACKET_JSON = REPO_ROOT / 'tmp' / 'release' / 'release-evidence-packet.json'
DEFAULT_ACTION_PLAN_JSON = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff-action-plan.json'
DEFAULT_RECOMMENDATION_MATRIX_JSON = REPO_ROOT / 'tmp' / 'release' / 'rc-recommendation-matrix.json'
DEFAULT_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'external-proof-inputs.md'
DEFAULT_JSON_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'external-proof-inputs.json'

MISSING_VALUES = {'', 'missing', 'none', 'not checked', 'unknown', 'isolated local runtime'}


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    placeholder: str
    required_for: tuple[str, ...]
    notes: str
    conditional: bool = False
    sensitive: bool = False


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        key='aidm_ci_run_url',
        label='AIDM CI run URL',
        placeholder='https://github.com/<owner>/<repo>/actions/runs/<run-id>',
        required_for=('github_actions_aidm_ci',),
        notes='Successful AIDM CI run for the signed-off commit.',
    ),
    FieldSpec(
        key='closed_beta_rc_run_url',
        label='Closed Beta RC workflow run URL',
        placeholder='https://github.com/<owner>/<repo>/actions/runs/<run-id>',
        required_for=('github_actions_closed_beta_rc',),
        notes='Successful manual Closed Beta RC workflow run for the signed-off commit.',
    ),
    FieldSpec(
        key='closed_beta_rc_artifact_reference',
        label='Closed Beta RC evidence artifact reference',
        placeholder='closed-beta-rc-evidence artifact URL or run artifact reference',
        required_for=('github_actions_rc_artifact',),
        notes=(
            'Artifact must contain the RC report, issue snippets, release packet, source archive, '
            'security/export-import evidence, GitHub Actions evidence, and visual-smoke artifacts.'
        ),
    ),
    FieldSpec(
        key='target_env_file',
        label='Hosted/staging env file',
        placeholder='/absolute/path/to/hosted-rc.env',
        required_for=('hosted_env_config', 'hosted_deployment_readiness'),
        notes='Use the target env values used by the hosted/staging deployment.',
    ),
    FieldSpec(
        key='deployment_readiness_evidence',
        label='Hosted deployment-readiness evidence',
        placeholder='tmp/release/deployment-readiness-evidence.md from the hosted/staging run',
        required_for=('hosted_env_config', 'hosted_deployment_readiness'),
        notes='Passing deployment-readiness report against the real hosted/staging target.',
    ),
    FieldSpec(
        key='signed_off_commit_sha',
        label='Signed-off commit SHA',
        placeholder='<signed-off-commit-sha>',
        required_for=('clean_signed_off_worktree',),
        notes='Final RC commit used for local RC evidence, GitHub Actions, and operator signoff.',
    ),
    FieldSpec(
        key='clean_worktree_rc_evidence',
        label='Clean-worktree RC evidence',
        placeholder='tmp/release/rc-evidence.md regenerated from a clean signed-off worktree',
        required_for=('clean_signed_off_worktree',),
        notes='Proof that final RC evidence was produced after committing the release candidate.',
    ),
    FieldSpec(
        key='target_url',
        label='Hosted/staging target URL',
        placeholder='https://closed-beta.example.com',
        required_for=(
            'hosted_env_config',
            'hosted_deployment_readiness',
            'hosted_cookie_auth',
            'hosted_non_admin_forbidden',
            'hosted_export_import',
            'hosted_beta_slo_baseline',
        ),
        notes='Must be the real hosted/staging target, not localhost or an example domain.',
    ),
    FieldSpec(
        key='hosted_cookie_auth_evidence',
        label='Hosted cookie-auth evidence',
        placeholder='tmp/release/hosted-cookie-auth-evidence.md from live-target mode',
        required_for=('hosted_cookie_auth',),
        notes='Live-target hosted cookie-auth smoke report.',
    ),
    FieldSpec(
        key='operator_auth_token',
        label='Operator auth token',
        placeholder='<operator-token>',
        required_for=('hosted_env_config', 'hosted_deployment_readiness', 'hosted_export_import', 'hosted_beta_slo_baseline'),
        notes='Token must have access to the hosted workspace evidence endpoints. Pass it to commands only; do not store it in tmp/release/external-proof-values.json.',
        sensitive=True,
    ),
    FieldSpec(
        key='workspace_id',
        label='Hosted workspace ID',
        placeholder='<workspace-id>',
        required_for=('hosted_non_admin_forbidden', 'hosted_export_import', 'hosted_beta_slo_baseline'),
        notes='Workspace used for hosted proof runs.',
    ),
    FieldSpec(
        key='non_admin_token',
        label='Hosted non-admin account token',
        placeholder='<non-admin-token>',
        required_for=('hosted_non_admin_forbidden',),
        notes='Must belong to an account that should be rejected by operator/admin endpoints. Pass it to commands only; do not store it in tmp/release/external-proof-values.json.',
        sensitive=True,
    ),
    FieldSpec(
        key='hosted_non_admin_forbidden_evidence',
        label='Hosted non-admin forbidden evidence',
        placeholder='tmp/release/security-forbidden-evidence.md from live-target mode',
        required_for=('hosted_non_admin_forbidden',),
        notes='Live-target hosted forbidden-response smoke report.',
    ),
    FieldSpec(
        key='campaign_id',
        label='Hosted campaign ID',
        placeholder='<campaign-id>',
        required_for=('hosted_non_admin_forbidden',),
        notes='Campaign reachable by the hosted forbidden-response smoke.',
    ),
    FieldSpec(
        key='session_id',
        label='Hosted session ID',
        placeholder='<session-id>',
        required_for=('hosted_non_admin_forbidden', 'hosted_export_import'),
        notes='Session used by hosted forbidden-response and export/import smokes.',
    ),
    FieldSpec(
        key='player_id',
        label='Hosted player ID',
        placeholder='<player-id>',
        required_for=('hosted_export_import',),
        notes='Player used by hosted export/import smoke.',
    ),
    FieldSpec(
        key='hosted_export_import_evidence',
        label='Hosted export/import evidence',
        placeholder='tmp/release/export-import-evidence.md from live-target mode',
        required_for=('hosted_export_import',),
        notes='Live-target hosted session export/import smoke report.',
    ),
    FieldSpec(
        key='hosted_backup_restore_evidence',
        label='Hosted backup/restore evidence',
        placeholder='provider snapshot/restore log URL or runbook evidence path',
        required_for=('hosted_backup_restore',),
        notes='Provider-specific proof that a hosted backup was restored successfully.',
    ),
    FieldSpec(
        key='hosted_worker_process_evidence',
        label='Hosted Socket.IO worker process evidence',
        placeholder='platform config/log/screenshot URL or path',
        required_for=('hosted_socketio_worker_process', 'multi_worker_socketio_staging'),
        notes='For RC1 single-worker mode, prove exactly one backend worker and the documented worker model.',
    ),
    FieldSpec(
        key='socketio_staging_proof',
        label='Socket.IO sticky/message-queue staging proof',
        placeholder='sticky-session or message-queue staging proof link',
        required_for=('multi_worker_socketio_staging',),
        notes='Only required when the hosted target does not use RC1 single-worker mode.',
        conditional=True,
    ),
    FieldSpec(
        key='source_archive_attachment_evidence',
        label='Source archive attachment evidence',
        placeholder='RC issue, workflow artifact, or release URL plus checksum',
        required_for=('source_archive_attachment',),
        notes='Attach the generated source archive and matching .sha256 sidecar.',
    ),
    FieldSpec(
        key='external_telemetry_receipt',
        label='External telemetry receipt evidence',
        placeholder='telemetry dashboard/log/event sample URL or path',
        required_for=('hosted_external_telemetry',),
        notes='Proof that the configured external telemetry destination received hosted events.',
    ),
    FieldSpec(
        key='hosted_beta_slo_baseline_evidence',
        label='Hosted beta SLO baseline evidence',
        placeholder='tmp/release/beta-slo-baseline.md from hosted/staging metrics',
        required_for=('hosted_beta_slo_baseline',),
        notes='Target-environment beta SLO baseline generated before tester expansion.',
    ),
    FieldSpec(
        key='operator_signoff_manifest_evidence',
        label='Final operator signoff evidence',
        placeholder='tmp/release/operator-signoff.json and operator-signoff-status.md showing 19/19',
        required_for=('final_operator_signoff',),
        notes='Final signoff manifest and status output after all required evidence is filled.',
    ),
    FieldSpec(
        key='rc_issue_closure_review',
        label='RC issue closure evidence links',
        placeholder='links to reviewed/posted issue comments for #3 through #9',
        required_for=('rc_issue_closure_review',),
        notes='Close issues only after generated snippets and external proof are attached.',
    ),
    FieldSpec(
        key='frontend_npm_ci_evidence',
        label='Frontend npm ci evidence',
        placeholder='CI run URL or local command output reference',
        required_for=('frontend_npm_ci',),
        notes='Proof that frontend dependencies installed from package-lock.json.',
    ),
    FieldSpec(
        key='make_clean_evidence',
        label='make clean evidence',
        placeholder='command output or reviewed artifact-cleanliness note',
        required_for=('make_clean',),
        notes='Packaging cleanup evidence before final source handoff.',
    ),
    FieldSpec(
        key='make_clean_deps_evidence',
        label='make clean-deps evidence',
        placeholder='command output or documented decision not to remove dependency folders',
        required_for=('make_clean_deps',),
        notes='Dependency-folder cleanup evidence before source-only handoff.',
    ),
)


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


def _text(value: Any) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _contextual_github_next_actions(actions: list[Any], worktree: dict[str, Any]) -> list[str]:
    clean_actions = [str(action) for action in actions if str(action).strip()]
    status = str(worktree.get('status') or '').strip().lower()
    if status not in {'dirty', 'unknown'}:
        return clean_actions

    prefix = (
        'Freeze and push a clean signed-off candidate first'
        if status == 'dirty'
        else 'Confirm the signed-off candidate is clean first'
    )
    contextualized: list[str] = []
    for action in clean_actions:
        if action.startswith(('Freeze and push a clean signed-off candidate first;', 'Confirm the signed-off candidate is clean first;')):
            contextualized.append(action)
            continue
        action_text = re.sub(r'commit [^,.;]+', 'the signed-off commit', action)
        if action_text:
            action_text = action_text[:1].lower() + action_text[1:]
        contextualized.append(f'{prefix}; then {action_text}')
    return contextualized


def _real_text(value: Any) -> str:
    text = _text(value)
    if not text or '<' in text or '>' in text:
        return ''
    return '' if text.lower() in MISSING_VALUES else text


def _is_real_url(value: Any) -> bool:
    text = _real_text(value)
    return text.startswith(('http://', 'https://'))


def _is_hosted_target(value: Any) -> bool:
    text = _real_text(value)
    lowered = text.lower()
    if not _is_real_url(text):
        return False
    if '.example.' in lowered or lowered.endswith('.example.test') or lowered.endswith('.example.com'):
        return False
    return not lowered.startswith(('http://127.', 'http://localhost', 'https://127.', 'https://localhost'))


def _load_json_object(path: pathlib.Path) -> dict[str, Any]:
    resolved = _resolve_repo_path(path)
    if not resolved.exists():
        return {}
    try:
        parsed = json.loads(resolved.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise SystemExit(f'[external-proof-inputs] Invalid JSON in {_relative_or_absolute(resolved)}: {exc}') from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f'[external-proof-inputs] JSON root must be an object: {_relative_or_absolute(resolved)}')
    return parsed


def _section(packet: dict[str, Any], key: str) -> dict[str, Any]:
    value = packet.get(key) or {}
    return value if isinstance(value, dict) else {}


def _metadata(section: dict[str, Any]) -> dict[str, Any]:
    value = section.get('metadata') or {}
    return value if isinstance(value, dict) else {}


def _manual_evidence_lookup(packet: dict[str, Any]) -> dict[str, str]:
    hosted_rc = _section(packet, 'hosted_rc_evidence')
    manual = hosted_rc.get('manual_evidence') or []
    lookup: dict[str, str] = {}
    if not isinstance(manual, list):
        return lookup
    for item in manual:
        if not isinstance(item, dict):
            continue
        label = _text(item.get('label')).lower()
        evidence = _real_text(item.get('evidence'))
        if label and item.get('status') == 'provided' and evidence:
            lookup[label] = evidence
    return lookup


def _evidence_path_if_hosted(section: dict[str, Any], *, require_live_mode: bool = False) -> str:
    if section.get('status') not in {'passed', 'present'}:
        return ''
    if require_live_mode and section.get('mode') != 'live-target':
        return ''
    if not _is_hosted_target(section.get('target_url')):
        return ''
    return _real_text(section.get('path'))


def _hosted_rc_usable(hosted_rc: dict[str, Any]) -> bool:
    status = _real_text(hosted_rc.get('status'))
    freshness = _real_text(hosted_rc.get('generator_freshness'))
    if status not in {'passed', 'manual-evidence-required'}:
        return False
    if freshness and freshness not in {'current', 'unknown'}:
        return False
    return _is_hosted_target(hosted_rc.get('target_url'))


def _hosted_rc_check(hosted_rc: dict[str, Any], label: str) -> dict[str, Any]:
    checks = hosted_rc.get('checks') if isinstance(hosted_rc.get('checks'), dict) else {}
    check = checks.get(label) if isinstance(checks, dict) else {}
    return check if isinstance(check, dict) else {}


def _hosted_rc_check_evidence(hosted_rc: dict[str, Any], label: str) -> str:
    if not _hosted_rc_usable(hosted_rc):
        return ''
    check = _hosted_rc_check(hosted_rc, label)
    if check.get('status') != 'passed' or check.get('validation_errors'):
        return ''
    target_url = check.get('evidence_target_url') or hosted_rc.get('target_url')
    if not _is_hosted_target(target_url):
        return ''
    return _real_text(check.get('evidence_path'))


def _github_actions_url(github_actions: dict[str, Any], signed_off: dict[str, Any], key: str) -> str:
    if signed_off.get('status') != 'passed':
        return ''
    if github_actions.get('freshness') == 'stale' or github_actions.get('status') in {'failed', 'invalid'}:
        return ''
    return _real_text(github_actions.get(key))


def _github_actions_rc_artifact_reference(github_actions: dict[str, Any], signed_off: dict[str, Any]) -> str:
    if signed_off.get('status') != 'passed':
        return ''
    if github_actions.get('freshness') == 'stale' or github_actions.get('status') in {'failed', 'invalid'}:
        return ''
    artifact = github_actions.get('closed_beta_rc_artifact')
    if not isinstance(artifact, dict):
        artifact = {}
    artifact_status = _text(
        github_actions.get('closed_beta_rc_artifact_status') or artifact.get('status')
    ).lower()
    content_status = _text(
        github_actions.get('closed_beta_rc_artifact_content_status') or artifact.get('content_status')
    ).lower()
    artifact_url = _real_text(github_actions.get('closed_beta_rc_artifact_url') or artifact.get('url'))
    if artifact_status == 'passed' and content_status == 'passed' and _is_real_url(artifact_url):
        return artifact_url
    return ''


def _source_archive_artifact_attachment_evidence(source_archive: dict[str, Any], artifact_url: str) -> str:
    if not artifact_url:
        return ''
    path = _real_text(source_archive.get('path'))
    sha256 = _real_text(source_archive.get('sha256')).lower()
    if not path or not re.fullmatch(r'[a-f0-9]{64}', sha256):
        return ''
    return f'{artifact_url} includes {_relative_or_absolute(path)} sha256:{sha256}'


def _field_current_value(key: str, packet: dict[str, Any], action_plan: dict[str, Any]) -> str:
    github_actions = _section(packet, 'github_actions')
    hosted_rc = _section(packet, 'hosted_rc_evidence')
    hosted_metadata = _metadata(hosted_rc)
    readiness = _section(packet, 'deployment_readiness')
    cookie_auth = _section(packet, 'hosted_cookie_auth')
    forbidden = _section(packet, 'security_forbidden')
    export_import = _section(packet, 'export_import')
    slo = _section(packet, 'beta_slo_baseline')
    source_archive = _section(packet, 'source_archive')
    frontend_npm_ci = _section(packet, 'frontend_npm_ci')
    packaging_cleanup = _section(packet, 'packaging_cleanup')
    signed_off = _section(packet, 'signed_off_worktree')
    rc_evidence = _section(packet, 'rc_evidence')
    operator_signoff = _section(packet, 'operator_signoff')
    manual = _manual_evidence_lookup(packet)

    target_url = (
        action_plan.get('target_url')
        or hosted_rc.get('target_url')
        or readiness.get('target_url')
        or cookie_auth.get('target_url')
        or forbidden.get('target_url')
        or export_import.get('target_url')
        or slo.get('target_url')
    )
    closed_beta_rc_artifact_reference = _github_actions_rc_artifact_reference(github_actions, signed_off)
    values = {
        'aidm_ci_run_url': _github_actions_url(github_actions, signed_off, 'aidm_ci_run_url'),
        'closed_beta_rc_run_url': _github_actions_url(github_actions, signed_off, 'closed_beta_rc_run_url'),
        'closed_beta_rc_artifact_reference': closed_beta_rc_artifact_reference,
        'deployment_readiness_evidence': _evidence_path_if_hosted(readiness)
        or _hosted_rc_check_evidence(hosted_rc, 'Hosted deployment readiness'),
        'target_url': target_url if _is_hosted_target(target_url) else '',
        'hosted_cookie_auth_evidence': _evidence_path_if_hosted(cookie_auth, require_live_mode=True)
        or _hosted_rc_check_evidence(hosted_rc, 'Hosted cookie auth smoke'),
        'hosted_non_admin_forbidden_evidence': _evidence_path_if_hosted(forbidden, require_live_mode=True)
        or _hosted_rc_check_evidence(hosted_rc, 'Hosted non-admin forbidden smoke'),
        'hosted_export_import_evidence': _evidence_path_if_hosted(export_import, require_live_mode=True)
        or _hosted_rc_check_evidence(hosted_rc, 'Hosted session export/import smoke'),
        'source_archive_attachment_evidence': manual.get('source archive attached to rc issue or release')
        or _source_archive_artifact_attachment_evidence(source_archive, closed_beta_rc_artifact_reference),
        'hosted_backup_restore_evidence': manual.get('hosted database backup/restore proof'),
        'hosted_worker_process_evidence': manual.get('hosted socket.io worker process proof'),
        'external_telemetry_receipt': manual.get('external telemetry receipt proof'),
        'socketio_staging_proof': hosted_metadata.get('socket_io_staging_proof'),
        'signed_off_commit_sha': signed_off.get('commit') if signed_off.get('status') == 'passed' else '',
        'clean_worktree_rc_evidence': rc_evidence.get('path') if signed_off.get('status') == 'passed' else '',
        'operator_signoff_manifest_evidence': (
            operator_signoff.get('path') if operator_signoff.get('status') == 'passed' else ''
        ),
        'hosted_beta_slo_baseline_evidence': _evidence_path_if_hosted(slo)
        or _hosted_rc_check_evidence(hosted_rc, 'Hosted beta SLO baseline'),
        'frontend_npm_ci_evidence': (
            _real_text(frontend_npm_ci.get('path')) if frontend_npm_ci.get('status') == 'passed' else ''
        ),
        'make_clean_evidence': (
            _real_text(packaging_cleanup.get('path')) if packaging_cleanup.get('status') == 'passed' else ''
        ),
        'make_clean_deps_evidence': (
            _real_text(packaging_cleanup.get('path')) if packaging_cleanup.get('status') == 'passed' else ''
        ),
        'source_archive': (
            f"{_relative_or_absolute(source_archive.get('path') or '')} sha256:{source_archive.get('sha256')}"
            if source_archive.get('path') and source_archive.get('sha256')
            else source_archive.get('path')
        ),
    }
    value = values.get(key)
    return _real_text(value)


def _field_status(spec: FieldSpec, pending_keys: set[str], complete_keys: set[str], current_value: str) -> str:
    if current_value:
        return 'provided-context'
    if any(key in pending_keys for key in spec.required_for):
        return 'conditional' if spec.conditional else 'required'
    if any(key in complete_keys for key in spec.required_for):
        return 'already-complete'
    return 'not-currently-required'


def _source_archive_context(packet: dict[str, Any]) -> dict[str, Any]:
    source = _section(packet, 'source_archive')
    return {
        'status': source.get('status') or 'missing',
        'path': _relative_or_absolute(source.get('path') or '') if source.get('path') else '',
        'sha256': source.get('sha256') or '',
        'bytes': source.get('bytes') or 0,
    }


def _recommendation_external_keys(recommendation_matrix: dict[str, Any]) -> list[str]:
    recommendations = recommendation_matrix.get('recommendations') or []
    if not isinstance(recommendations, list):
        return []
    return [
        str(item.get('key'))
        for item in recommendations
        if isinstance(item, dict) and item.get('status') == 'external-required' and item.get('key')
    ]


def _command_templates(source_archive: dict[str, Any]) -> list[dict[str, str]]:
    source_label = source_archive.get('path') or 'tmp/release/aidm-source-*.tar.gz'
    return [
        {
            'key': 'github_actions_rc_plan',
            'description': 'Check local GitHub Actions readiness and optionally dispatch the manual Closed Beta RC workflow.',
            'command': 'make github-actions-rc-plan',
        },
        {
            'key': 'github_actions_evidence',
            'description': 'Refresh GitHub Actions run URL evidence after CI and Closed Beta RC complete.',
            'command': (
                'make github-actions-evidence GITHUB_ACTIONS_EVIDENCE_ARGS='
                '"--auto-gh --include-gh-details --verify-closed-beta-rc-artifact-contents"'
            ),
        },
        {
            'key': 'deployment_readiness',
            'description': 'Validate the hosted/staging deployment and live health/metrics/security headers.',
            'command': (
                'make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> '
                '--target-url <target-url> --auth-token <operator-token> '
                '--evidence-report tmp/release/deployment-readiness-evidence.md"'
            ),
        },
        {
            'key': 'hosted_cookie_auth_smoke',
            'description': 'Prove hosted cookie-only auth, CSRF, logout cleanup, role refresh, and socket auth.',
            'command': (
                'make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_SMOKE_ARGS="--target-url <target-url> '
                '--account-intent signup --evidence-report tmp/release/hosted-cookie-auth-evidence.md"'
            ),
        },
        {
            'key': 'security_forbidden_smoke',
            'description': 'Prove non-admin accounts cannot mutate operator/admin resources on hosted/staging.',
            'command': (
                'make security-forbidden-smoke SECURITY_FORBIDDEN_SMOKE_ARGS="--target-url <target-url> '
                '--account-token <non-admin-token> --workspace-id <workspace-id> '
                '--campaign-id <campaign-id> --session-id <session-id> '
                '--evidence-report tmp/release/security-forbidden-evidence.md"'
            ),
        },
        {
            'key': 'session_export_import_smoke',
            'description': 'Prove hosted session export/import works against the target workspace/session.',
            'command': (
                'make session-export-import-smoke SESSION_EXPORT_IMPORT_SMOKE_ARGS="--target-url <target-url> '
                '--auth-token <operator-token> --workspace-id <workspace-id> '
                '--session-id <session-id> --player-id <player-id> '
                '--evidence-report tmp/release/export-import-evidence.md"'
            ),
        },
        {
            'key': 'beta_slo_baseline',
            'description': 'Record beta SLO visibility from hosted/staging metrics.',
            'command': (
                'make beta-slo-baseline BETA_SLO_BASELINE_ARGS="--target-url <target-url> '
                '--auth-token <operator-token> --workspace-id <workspace-id> '
                '--release RC1 --environment staging --output tmp/release/beta-slo-baseline.md"'
            ),
        },
        {
            'key': 'hosted_rc_evidence',
            'description': 'Bundle hosted proof and manual provider evidence into the RC evidence artifact.',
            'command': (
                'make hosted-rc-evidence HOSTED_RC_EVIDENCE_ARGS="--target-url <target-url> '
                '--auth-token <operator-token> --workspace-id <workspace-id> '
                '--non-admin-token <non-admin-token> --campaign-id <campaign-id> '
                '--session-id <session-id> --player-id <player-id> --env-file <target-env> '
                '--hosted-backup-restore-evidence <link-or-path> '
                '--hosted-worker-process-evidence <link-or-path> '
                '--source-archive-attachment-evidence <link-or-path> '
                '--external-telemetry-receipt <link-or-path>"'
            ),
        },
        {
            'key': 'external_proof_values_merge',
            'description': (
                'Merge the passed hosted RC values fragment into an existing operator-filled external proof values file.'
            ),
            'command': 'make external-proof-values-merge',
        },
        {
            'key': 'operator_signoff',
            'description': (
                'Generate final operator signoff, stamp its evidence into external proof values, '
                'and refresh the packet/checklist.'
            ),
            'command': 'make rc-finalize-signoff',
        },
        {
            'key': 'clean_signed_off_handoff',
            'description': 'Regenerate RC evidence and handoff artifacts after committing the release candidate.',
            'command': 'git status --short && make closed-beta-rc && make rc-handoff-artifacts',
        },
        {
            'key': 'source_archive_attachment',
            'description': 'Attach the current source archive and checksum to the RC issue or release.',
            'command': f'Attach {source_label} and {source_label}.sha256 to the RC issue, workflow artifact, or release.',
        },
    ]


def build_template(
    *,
    packet: dict[str, Any],
    action_plan: dict[str, Any],
    recommendation_matrix: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    pending_actions = [item for item in action_plan.get('actions') or [] if isinstance(item, dict)]
    complete_items = [item for item in action_plan.get('complete_items') or [] if isinstance(item, dict)]
    pending_keys = {str(item.get('key')) for item in pending_actions if item.get('key')}
    complete_keys = {str(item.get('key')) for item in complete_items if item.get('key')}
    external_recommendation_keys = set(_recommendation_external_keys(recommendation_matrix))
    required_keys = pending_keys | external_recommendation_keys
    source_archive = _source_archive_context(packet)

    fields: list[dict[str, Any]] = []
    for spec in FIELD_SPECS:
        current_value = '' if spec.sensitive else _field_current_value(spec.key, packet, action_plan)
        fields.append(
            {
                **asdict(spec),
                'required_for': list(spec.required_for),
                'current_value': current_value,
                'status': _field_status(spec, required_keys, complete_keys, current_value),
            }
        )

    required_fields = [field for field in fields if field['status'] == 'required']
    conditional_fields = [field for field in fields if field['status'] == 'conditional']
    provided_context_fields = [field for field in fields if field['status'] == 'provided-context']
    status = 'ready' if not required_fields and not pending_actions else 'action-required'

    packet_github = _section(packet, 'github_actions')
    operator_signoff = _section(packet, 'operator_signoff')
    hosted_rc = _section(packet, 'hosted_rc_evidence')
    signed_off_worktree = _section(packet, 'signed_off_worktree')
    return {
        'generated_at': generated_at,
        'status': status,
        'packet_overall_status': packet.get('overall_status') or 'missing',
        'action_plan_status': action_plan.get('status') or 'missing',
        'recommendation_matrix_status': recommendation_matrix.get('status') or 'missing',
        'source_archive': source_archive,
        'github_actions': {
            'status': packet_github.get('status') or 'missing',
            'repository': packet_github.get('repository') or 'missing',
            'aidm_ci_run_url': packet_github.get('aidm_ci_run_url') or 'missing',
            'closed_beta_rc_run_url': packet_github.get('closed_beta_rc_run_url') or 'missing',
            'missing': packet_github.get('missing') or '',
            'missing_details': packet_github.get('missing_details') if isinstance(packet_github.get('missing_details'), dict) else {},
            'next_actions': packet_github.get('next_actions') if isinstance(packet_github.get('next_actions'), list) else [],
        },
        'hosted_rc_evidence': {
            'status': hosted_rc.get('status') or 'missing',
            'target_url': hosted_rc.get('target_url') or 'missing',
            'manual_required': hosted_rc.get('manual_required') or [],
            'manual_required_count': hosted_rc.get('manual_required_count') or 0,
        },
        'operator_signoff': {
            'status': operator_signoff.get('status') or 'missing',
            'required_complete': operator_signoff.get('required_complete') or 'missing',
            'missing_or_invalid': operator_signoff.get('missing_or_invalid') or 'missing',
        },
        'signed_off_worktree': {
            'status': signed_off_worktree.get('status') or 'missing',
            'worktree': signed_off_worktree.get('worktree') or 'missing',
            'commit': signed_off_worktree.get('commit') or action_plan.get('commit') or 'missing',
        },
        'external_recommendation_keys': sorted(external_recommendation_keys),
        'field_counts': {
            'required': len(required_fields),
            'conditional': len(conditional_fields),
            'provided_context': len(provided_context_fields),
            'total': len(fields),
        },
        'fields': fields,
        'pending_actions': pending_actions,
        'complete_items': complete_items,
        'command_templates': _command_templates(source_archive),
    }


def render_markdown(template: dict[str, Any]) -> str:
    github_actions = template.get('github_actions') if isinstance(template.get('github_actions'), dict) else {}
    github_missing_details = github_actions.get('missing_details') if isinstance(github_actions.get('missing_details'), dict) else {}
    github_next_actions = github_actions.get('next_actions') if isinstance(github_actions.get('next_actions'), list) else []
    signed_off_worktree = (
        template.get('signed_off_worktree') if isinstance(template.get('signed_off_worktree'), dict) else {}
    )
    contextual_github_next_actions = _contextual_github_next_actions(github_next_actions, signed_off_worktree)
    context_rows = [
        '| Context | Value |',
        '| --- | --- |',
        f"| Release packet status | {template.get('packet_overall_status')} |",
        f"| Action plan status | {template.get('action_plan_status')} |",
        f"| Recommendation matrix status | {template.get('recommendation_matrix_status')} |",
        (
            f"| Source archive | {template.get('source_archive', {}).get('path') or 'missing'} "
            f"sha256:{template.get('source_archive', {}).get('sha256') or 'missing'} |"
        ),
        (
            f"| GitHub Actions | {template.get('github_actions', {}).get('status')}; "
            f"AIDM CI: {template.get('github_actions', {}).get('aidm_ci_run_url')}; "
            f"Closed Beta RC: {template.get('github_actions', {}).get('closed_beta_rc_run_url')} |"
        ),
        (
            f"| Hosted RC evidence | {template.get('hosted_rc_evidence', {}).get('status')}; "
            f"target: {template.get('hosted_rc_evidence', {}).get('target_url')}; "
            f"manual required: {template.get('hosted_rc_evidence', {}).get('manual_required_count')} |"
        ),
        (
            f"| Operator signoff | {template.get('operator_signoff', {}).get('status')}; "
            f"required complete: {template.get('operator_signoff', {}).get('required_complete')} |"
        ),
        (
            f"| Worktree | {template.get('signed_off_worktree', {}).get('status')}; "
            f"{template.get('signed_off_worktree', {}).get('worktree')} |"
        ),
    ]
    for label, reason in github_missing_details.items():
        context_rows.append(f'| GitHub Actions missing: {label} | {reason} |')
    for index, action in enumerate(contextual_github_next_actions, start=1):
        context_rows.append(f'| GitHub Actions next action {index} | {action} |')

    field_rows = [
        '| Field | Status | Handling | Placeholder/current value | Required for | Notes |',
        '| --- | --- | --- | --- | --- | --- |',
    ]
    for field in template.get('fields') or []:
        value = field.get('current_value') or field.get('placeholder') or ''
        handling = 'command-only sensitive value' if field.get('sensitive') else 'persistable proof value'
        field_rows.append(
            f"| `{field.get('key')}` | {field.get('status')} | {handling} | {value} | "
            f"{', '.join(field.get('required_for') or [])} | {field.get('notes') or ''} |"
        )

    action_rows = [
        '| Signoff key | Issues | Category | Current context | Prerequisite | Next action | Evidence to record | Inputs |',
        '| --- | --- | --- | --- | --- | --- | --- | --- |',
    ]
    for item in template.get('pending_actions') or []:
        action_rows.append(
            f"| `{item.get('key')}` | {item.get('issues') or ''} | {item.get('category') or ''} | "
            f"{item.get('context_evidence') or ''} | {item.get('prerequisite') or ''} | "
            f"{item.get('next_action') or ''} | {item.get('evidence_to_record') or ''} | "
            f"{', '.join(item.get('required_inputs') or [])} |"
        )
    if len(action_rows) == 2:
        action_rows.append('| None | None | None | None | None | None | None | None |')

    command_rows = ['| Key | Description | Command |', '| --- | --- | --- |']
    for command in template.get('command_templates') or []:
        command_rows.append(
            f"| `{command.get('key')}` | {command.get('description')} | `{command.get('command')}` |"
        )

    external_keys = ', '.join(template.get('external_recommendation_keys') or []) or 'None'
    counts = template.get('field_counts') or {}
    return '\n'.join(
        [
            '# External Proof Inputs',
            '',
            f"- Generated: {template.get('generated_at')}",
            f"- Status: {template.get('status')}",
            f"- Required fields: {counts.get('required', 0)}",
            f"- Conditional fields: {counts.get('conditional', 0)}",
            f"- Provided context fields: {counts.get('provided_context', 0)}",
            f"- External recommendation keys: {external_keys}",
            '',
            '## Known Context',
            '',
            *context_rows,
            '',
            '## Fillable Inputs',
            '',
            *field_rows,
            '',
            '## Pending Sign-Off Actions',
            '',
            *action_rows,
            '',
            '## Command Templates',
            '',
            *command_rows,
            '',
        ]
    )


def write_template(template: dict[str, Any], *, output: pathlib.Path, json_output: pathlib.Path | None) -> None:
    output_path = _resolve_repo_path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(template), encoding='utf-8')
    if json_output is not None:
        json_path = _resolve_repo_path(json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(template, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Render a fillable template for the external RC proof inputs.')
    parser.add_argument('--packet-json', type=pathlib.Path, default=DEFAULT_PACKET_JSON)
    parser.add_argument('--action-plan-json', type=pathlib.Path, default=DEFAULT_ACTION_PLAN_JSON)
    parser.add_argument('--recommendation-matrix-json', type=pathlib.Path, default=DEFAULT_RECOMMENDATION_MATRIX_JSON)
    parser.add_argument('--output', type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--json-output', type=pathlib.Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument('--generated-at', default='', help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generated_at = args.generated_at or _iso_now()
    packet = _load_json_object(args.packet_json)
    action_plan = _load_json_object(args.action_plan_json)
    recommendation_matrix = _load_json_object(args.recommendation_matrix_json)
    template = build_template(
        packet=packet,
        action_plan=action_plan,
        recommendation_matrix=recommendation_matrix,
        generated_at=generated_at,
    )
    write_template(template, output=args.output, json_output=args.json_output)
    print(f'[external-proof-inputs] Wrote {_relative_or_absolute(_resolve_repo_path(args.output))}.')
    if args.json_output is not None:
        print(f'[external-proof-inputs] Wrote {_relative_or_absolute(_resolve_repo_path(args.json_output))}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
