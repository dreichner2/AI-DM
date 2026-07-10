#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Iterable


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_REPORT = REPO_ROOT / 'tmp' / 'release' / 'hosted-rc-evidence.md'
DEFAULT_JSON_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'hosted-rc-evidence.json'
DEFAULT_VALUES_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'external-proof-values.hosted-rc.json'
MISSING_VALUES = {'', 'missing', 'none', 'not checked', 'placeholder', 'tbd', 'todo', 'unknown'}
PLACEHOLDER_EVIDENCE_MARKERS = (
    '.example.',
    'example.com',
    'example.test',
    'github.com/example',
    'localhost',
    '127.0.0.1',
    'isolated local runtime',
)
SCHEMA_VERSION = 1
VALUES_SCHEMA_VERSION = 1
SENSITIVE_COMMAND_FLAGS = frozenset({'--account-token', '--auth-token', '--password', '--workspace-token'})
REDACTED_VALUE = '<redacted>'
SHA256_RE = re.compile(r'\b[a-fA-F0-9]{64}\b')

HOSTED_BACKUP_RESTORE_LABEL = 'Hosted database backup/restore proof'
HOSTED_WORKER_PROCESS_LABEL = 'Hosted Socket.IO worker process proof'
SOURCE_ARCHIVE_ATTACHMENT_LABEL = 'Source archive attached to RC issue or release'
EXTERNAL_TELEMETRY_RECEIPT_LABEL = 'External telemetry receipt proof'

EVIDENCE_VALUE_KEYS_BY_CHECK = {
    'Hosted deployment readiness': 'deployment_readiness_evidence',
    'Hosted cookie auth smoke': 'hosted_cookie_auth_evidence',
    'Hosted non-admin forbidden smoke': 'hosted_non_admin_forbidden_evidence',
    'Hosted session export/import smoke': 'hosted_export_import_evidence',
    'Hosted beta SLO baseline': 'hosted_beta_slo_baseline_evidence',
}

MANUAL_VALUE_KEYS_BY_LABEL = {
    HOSTED_BACKUP_RESTORE_LABEL: 'hosted_backup_restore_evidence',
    HOSTED_WORKER_PROCESS_LABEL: 'hosted_worker_process_evidence',
    SOURCE_ARCHIVE_ATTACHMENT_LABEL: 'source_archive_attachment_evidence',
    EXTERNAL_TELEMETRY_RECEIPT_LABEL: 'external_telemetry_receipt',
}


@dataclass(frozen=True)
class HostedCheck:
    label: str
    args: tuple[str, ...]
    evidence_path: pathlib.Path
    required: tuple[str, ...] = ()
    expected_target_url: str = ''
    cwd: pathlib.Path = REPO_ROOT


@dataclass(frozen=True)
class HostedCheckResult:
    label: str
    status: str
    returncode: int | None
    duration_seconds: float | None
    command: str
    evidence_path: str
    evidence_target_url: str = ''
    validation_errors: tuple[str, ...] = ()
    missing_inputs: tuple[str, ...] = ()


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


def _file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: object) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _real_text(value: object) -> str:
    text = _text(value)
    return '' if text.lower() in MISSING_VALUES else text


def _manual_evidence_error(value: object, *, label: str = '') -> str:
    text = _real_text(value)
    if not text or '<' in text or '>' in text:
        return 'manual evidence must be a real proof link/path/details value, not a placeholder'
    lowered = text.lower()
    if any(marker in lowered for marker in PLACEHOLDER_EVIDENCE_MARKERS):
        return 'manual evidence must not use example, localhost, or isolated-runtime references'
    if label == SOURCE_ARCHIVE_ATTACHMENT_LABEL and not SHA256_RE.search(text):
        return 'source archive attachment evidence must include a SHA256 checksum'
    return ''


def _normalize_target_url(value: object) -> str:
    return _real_text(value).rstrip('/')


def _evidence_target_from_json(path: pathlib.Path) -> str:
    parsed = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(parsed, dict):
        return ''
    target_url = _real_text(parsed.get('target_url'))
    if target_url:
        return target_url
    options = parsed.get('options')
    if isinstance(options, dict):
        return _real_text(options.get('target_url'))
    return ''


def _evidence_target_from_markdown(path: pathlib.Path) -> str:
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line.startswith('- ') or ':' not in line:
            continue
        label, value = line[2:].split(':', 1)
        if label.strip().lower() == 'target url':
            return value.strip().strip('`')
    return ''


def _evidence_report_target_url(path: pathlib.Path) -> str:
    try:
        if path.suffix.lower() == '.json':
            return _evidence_target_from_json(path)
        return _evidence_target_from_markdown(path)
    except (OSError, json.JSONDecodeError):
        return ''


def _validate_evidence_report(check: HostedCheck) -> tuple[str, tuple[str, ...]]:
    errors: list[str] = []
    evidence_path = _resolve_repo_path(check.evidence_path)
    if not evidence_path.exists():
        return '', (f'evidence report was not written: {_relative_or_absolute(evidence_path)}',)

    evidence_target = _evidence_report_target_url(evidence_path)
    if not evidence_target:
        errors.append('evidence report does not include a target URL')
        return '', tuple(errors)

    expected = _normalize_target_url(check.expected_target_url)
    actual = _normalize_target_url(evidence_target)
    if expected and actual != expected:
        errors.append(f'evidence target URL {actual} does not match requested target URL {expected}')
    return evidence_target, tuple(errors)


def _append_if(args: list[str], flag: str, value: str | int | float | pathlib.Path | None) -> None:
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    args.extend([flag, str(value)])


def _append_flag_if(args: list[str], flag: str, enabled: bool) -> None:
    if enabled:
        args.append(flag)


def _redacted_command_args(args: Iterable[str]) -> tuple[str, ...]:
    redacted: list[str] = []
    redact_next = False
    for arg in args:
        if redact_next:
            redacted.append(REDACTED_VALUE)
            redact_next = False
            continue
        matched_equals_flag = False
        for flag in SENSITIVE_COMMAND_FLAGS:
            if arg.startswith(f'{flag}='):
                redacted.append(f'{flag}={REDACTED_VALUE}')
                matched_equals_flag = True
                break
        if matched_equals_flag:
            continue
        redacted.append(arg)
        if arg in SENSITIVE_COMMAND_FLAGS:
            redact_next = True
    return tuple(redacted)


def _command_text(check: HostedCheck) -> str:
    return f'(cd {_relative_or_absolute(check.cwd)} && {shlex.join(_redacted_command_args(check.args))})'


def _required_missing(*items: tuple[str, object]) -> tuple[str, ...]:
    missing: list[str] = []
    for label, value in items:
        if value is None:
            missing.append(label)
        elif isinstance(value, str) and not value.strip():
            missing.append(label)
        elif isinstance(value, int) and value <= 0:
            missing.append(label)
    return tuple(missing)


def _command_plan_sha256(results: Iterable[HostedCheckResult]) -> str:
    entries = [
        {
            'label': result.label,
            'command': result.command,
            'evidence_path': result.evidence_path,
        }
        for result in results
    ]
    encoded = json.dumps(entries, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def build_command_plan(args: argparse.Namespace) -> list[HostedCheck]:
    python = args.python
    target_missing = _required_missing(('--target-url', args.target_url))
    plan: list[HostedCheck] = []

    if not args.skip_deployment_readiness:
        command = [python, 'scripts/deployment_readiness_check.py']
        _append_if(command, '--env-file', args.env_file)
        _append_if(command, '--target-url', args.target_url)
        _append_if(command, '--auth-token', args.auth_token)
        _append_if(command, '--socketio-origin', args.socketio_origin)
        _append_if(command, '--timeout-seconds', args.timeout_seconds)
        _append_if(command, '--auth-storage-exception', args.auth_storage_exception)
        _append_if(command, '--socketio-staging-proof', args.socketio_staging_proof)
        _append_flag_if(command, '--same-origin-deployment', args.same_origin_deployment)
        _append_flag_if(command, '--allow-fallback-provider', args.allow_fallback_provider)
        _append_flag_if(command, '--allow-non-production-target', args.allow_non_production_target)
        command.extend(['--evidence-report', 'tmp/release/deployment-readiness-evidence.md'])
        plan.append(
            HostedCheck(
                label='Hosted deployment readiness',
                args=tuple(command),
                evidence_path=REPO_ROOT / 'tmp' / 'release' / 'deployment-readiness-evidence.md',
                required=target_missing,
                expected_target_url=args.target_url,
            )
        )

    if not args.skip_cookie_auth:
        command = [python, 'scripts/hosted_cookie_auth_smoke.py']
        _append_if(command, '--target-url', args.target_url)
        command.extend(['--account-intent', args.cookie_account_intent])
        _append_if(command, '--username', args.cookie_username)
        _append_if(command, '--password', args.cookie_password)
        _append_if(command, '--workspace-name', args.cookie_workspace_name)
        _append_if(command, '--socketio-path', args.socketio_path)
        _append_if(command, '--timeout-seconds', args.timeout_seconds)
        command.extend(['--evidence-report', 'tmp/release/hosted-cookie-auth-evidence.md'])
        required = list(target_missing)
        if args.cookie_account_intent == 'login':
            required.extend(_required_missing(('--cookie-username', args.cookie_username), ('--cookie-password', args.cookie_password)))
        plan.append(
            HostedCheck(
                label='Hosted cookie auth smoke',
                args=tuple(command),
                evidence_path=REPO_ROOT / 'tmp' / 'release' / 'hosted-cookie-auth-evidence.md',
                required=tuple(required),
                expected_target_url=args.target_url,
            )
        )

    if not args.skip_security_forbidden:
        command = [python, 'scripts/security_forbidden_smoke.py']
        _append_if(command, '--target-url', args.target_url)
        _append_if(command, '--account-token', args.non_admin_token)
        _append_if(command, '--workspace-id', args.workspace_id)
        _append_if(command, '--campaign-id', args.campaign_id)
        _append_if(command, '--session-id', args.session_id)
        _append_if(command, '--timeout-seconds', args.timeout_seconds)
        command.extend(['--evidence-report', 'tmp/release/security-forbidden-evidence.md'])
        plan.append(
            HostedCheck(
                label='Hosted non-admin forbidden smoke',
                args=tuple(command),
                evidence_path=REPO_ROOT / 'tmp' / 'release' / 'security-forbidden-evidence.md',
                required=target_missing
                + _required_missing(
                    ('--non-admin-token', args.non_admin_token),
                    ('--workspace-id', args.workspace_id),
                    ('--campaign-id', args.campaign_id),
                    ('--session-id', args.session_id),
                ),
                expected_target_url=args.target_url,
            )
        )

    if not args.skip_export_import:
        command = [python, 'scripts/session_export_import_smoke.py']
        _append_if(command, '--target-url', args.target_url)
        _append_if(command, '--auth-token', args.auth_token)
        _append_if(command, '--workspace-id', args.workspace_id)
        _append_if(command, '--session-id', args.session_id)
        _append_if(command, '--player-id', args.player_id)
        _append_if(command, '--timeout-seconds', args.timeout_seconds)
        _append_flag_if(command, '--keep-imported-session', args.keep_imported_session)
        command.extend(['--evidence-report', 'tmp/release/export-import-evidence.md'])
        plan.append(
            HostedCheck(
                label='Hosted session export/import smoke',
                args=tuple(command),
                evidence_path=REPO_ROOT / 'tmp' / 'release' / 'export-import-evidence.md',
                required=target_missing
                + _required_missing(
                    ('--auth-token', args.auth_token),
                    ('--workspace-id', args.workspace_id),
                    ('--session-id', args.session_id),
                ),
                expected_target_url=args.target_url,
            )
        )

    if not args.skip_beta_slo:
        command = [python, 'scripts/render_beta_slo_baseline.py']
        _append_if(command, '--target-url', args.target_url)
        _append_if(command, '--auth-token', args.auth_token)
        _append_if(command, '--workspace-id', args.workspace_id)
        _append_if(command, '--workspace-token', args.workspace_token)
        _append_if(command, '--timeout-seconds', args.timeout_seconds)
        _append_if(command, '--release', args.release)
        _append_if(command, '--commit-sha', args.commit_sha)
        _append_if(command, '--environment', args.environment)
        _append_if(command, '--socketio-worker-model', args.socketio_worker_model)
        _append_if(command, '--database', args.database)
        _append_if(command, '--llm-provider-model', args.llm_provider_model)
        _append_if(command, '--observability-provider', args.observability_provider)
        _append_if(command, '--alert-owner', args.alert_owner)
        command.extend(['--output', 'tmp/release/beta-slo-baseline.md'])
        required = list(target_missing)
        if not args.workspace_token:
            required.extend(_required_missing(('--auth-token', args.auth_token), ('--workspace-id', args.workspace_id)))
        plan.append(
            HostedCheck(
                label='Hosted beta SLO baseline',
                args=tuple(command),
                evidence_path=REPO_ROOT / 'tmp' / 'release' / 'beta-slo-baseline.md',
                required=tuple(required),
                expected_target_url=args.target_url,
            )
        )

    return plan


def run_check(check: HostedCheck, *, dry_run: bool) -> HostedCheckResult:
    command_text = _command_text(check)
    evidence_path = _relative_or_absolute(check.evidence_path)
    if check.required:
        print(f"[hosted-rc][missing-input] {check.label}: {', '.join(check.required)}")
        return HostedCheckResult(
            label=check.label,
            status='missing-input',
            returncode=None,
            duration_seconds=None,
            command=command_text,
            evidence_path=evidence_path,
            missing_inputs=check.required,
        )
    if dry_run:
        print(f'[hosted-rc][dry-run] {check.label}: {command_text}')
        return HostedCheckResult(
            label=check.label,
            status='planned',
            returncode=None,
            duration_seconds=None,
            command=command_text,
            evidence_path=evidence_path,
        )
    print(f'[hosted-rc] {check.label}...')
    started = time.monotonic()
    result = subprocess.run(check.args, cwd=str(check.cwd), text=True)
    duration = time.monotonic() - started
    status = 'passed' if result.returncode == 0 else 'failed'
    if result.returncode != 0:
        print(f'[hosted-rc][failed] {check.label} exited with {result.returncode}.')
    evidence_target_url = ''
    validation_errors: tuple[str, ...] = ()
    if result.returncode == 0:
        evidence_target_url, validation_errors = _validate_evidence_report(check)
        if validation_errors:
            status = 'invalid-evidence'
            print(f'[hosted-rc][invalid-evidence] {check.label}: {"; ".join(validation_errors)}')
    return HostedCheckResult(
        label=check.label,
        status=status,
        returncode=result.returncode,
        duration_seconds=duration,
        command=command_text,
        evidence_path=evidence_path,
        evidence_target_url=evidence_target_url,
        validation_errors=validation_errors,
    )


def run_plan(plan: Iterable[HostedCheck], *, dry_run: bool) -> tuple[str, list[HostedCheckResult]]:
    results = [run_check(check, dry_run=dry_run) for check in plan]
    statuses = {result.status for result in results}
    if not results:
        status = 'empty'
    elif 'failed' in statuses:
        status = 'failed'
    elif 'invalid-evidence' in statuses:
        status = 'invalid-evidence'
    elif 'missing-input' in statuses:
        status = 'missing-input'
    elif dry_run:
        status = 'planned'
    else:
        status = 'passed'
    return status, results


def _manual_evidence_item(label: str, evidence: str) -> dict[str, str]:
    real_evidence = _real_text(evidence)
    if not real_evidence:
        return {'label': label, 'status': 'required', 'evidence': '', 'error': ''}
    error = _manual_evidence_error(real_evidence, label=label)
    return {
        'label': label,
        'status': 'invalid' if error else 'provided',
        'evidence': real_evidence,
        'error': error,
    }


def _manual_evidence_items(args: argparse.Namespace) -> list[dict[str, str]]:
    return [
        _manual_evidence_item(HOSTED_BACKUP_RESTORE_LABEL, args.hosted_backup_restore_evidence),
        _manual_evidence_item(HOSTED_WORKER_PROCESS_LABEL, args.hosted_worker_process_evidence),
        _manual_evidence_item(SOURCE_ARCHIVE_ATTACHMENT_LABEL, args.source_archive_attachment_evidence),
        _manual_evidence_item(EXTERNAL_TELEMETRY_RECEIPT_LABEL, args.external_telemetry_receipt),
    ]


def _markdown_metadata(path: pathlib.Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return metadata
    for raw_line in lines:
        line = raw_line.strip()
        if not line.startswith('- ') or ':' not in line:
            continue
        label, value = line[2:].split(':', 1)
        metadata[label.strip().lower()] = value.strip().strip('`')
    return metadata


def _payload_is_planned_dry_run(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and str(payload.get('status') or '').lower() == 'planned'
        and payload.get('dry_run') is True
    )


def _markdown_report_is_planned_dry_run(path: pathlib.Path) -> bool:
    metadata = _markdown_metadata(path)
    return metadata.get('status', '').lower() == 'planned' and metadata.get('dry run', '').lower() == 'true'


def _should_preserve_existing_real_evidence(evidence_report: pathlib.Path, json_output: pathlib.Path | None) -> bool:
    if json_output is not None:
        json_path = _resolve_repo_path(json_output)
        if json_path.exists():
            try:
                payload = json.loads(json_path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                return True
            return not _payload_is_planned_dry_run(payload)

    report_path = _resolve_repo_path(evidence_report)
    if not report_path.exists():
        return False
    return not _markdown_report_is_planned_dry_run(report_path)


def finalize_status(*, automated_status: str, manual_items: list[dict[str, str]], dry_run: bool) -> str:
    if automated_status != 'passed':
        return automated_status
    if dry_run:
        return 'planned'
    if any(item.get('status') == 'invalid' for item in manual_items):
        return 'invalid'
    if any(item.get('status') == 'required' for item in manual_items):
        return 'manual-evidence-required'
    return 'passed'


def build_payload(
    *,
    status: str,
    results: list[HostedCheckResult],
    args: argparse.Namespace,
    started_at: str,
    finished_at: str,
) -> dict:
    manual_items = _manual_evidence_items(args)
    final_status = finalize_status(automated_status=status, manual_items=manual_items, dry_run=args.dry_run)
    generator_path = pathlib.Path(__file__).resolve()
    return {
        'schema_version': SCHEMA_VERSION,
        'status': final_status,
        'automated_status': status,
        'started_at': started_at,
        'finished_at': finished_at,
        'generator': {
            'path': _relative_or_absolute(generator_path),
            'sha256': _file_sha256(generator_path),
        },
        'command_plan_sha256': _command_plan_sha256(results),
        'target_url': args.target_url,
        'target_env_file': _relative_or_absolute(args.env_file) if args.env_file else '',
        'workspace_id': args.workspace_id,
        'campaign_id': str(args.campaign_id or ''),
        'session_id': str(args.session_id or ''),
        'player_id': str(args.player_id or ''),
        'release': args.release,
        'commit_sha': args.commit_sha,
        'environment': args.environment,
        'socketio_worker_model': args.socketio_worker_model,
        'socketio_staging_proof': args.socketio_staging_proof,
        'database': args.database,
        'llm_provider_model': args.llm_provider_model,
        'observability_provider': args.observability_provider,
        'alert_owner': args.alert_owner,
        'dry_run': args.dry_run,
        'checks': [asdict(result) for result in results],
        'manual_evidence': manual_items,
    }


def _set_value(values: dict[str, str], key: str, value: object) -> None:
    text = _real_text(value)
    if text:
        values[key] = text


def build_values_payload(payload: dict) -> dict:
    values: dict[str, str] = {}
    _set_value(values, 'target_url', payload.get('target_url'))
    _set_value(values, 'target_env_file', payload.get('target_env_file'))
    _set_value(values, 'workspace_id', payload.get('workspace_id'))
    _set_value(values, 'campaign_id', payload.get('campaign_id'))
    _set_value(values, 'session_id', payload.get('session_id'))
    _set_value(values, 'player_id', payload.get('player_id'))
    _set_value(values, 'socketio_worker_model', payload.get('socketio_worker_model'))
    _set_value(values, 'socketio_staging_proof', payload.get('socketio_staging_proof'))
    _set_value(values, 'signed_off_commit_sha', payload.get('commit_sha'))

    for check in payload.get('checks') or []:
        if not isinstance(check, dict) or check.get('status') != 'passed' or check.get('validation_errors'):
            continue
        key = EVIDENCE_VALUE_KEYS_BY_CHECK.get(str(check.get('label') or ''))
        if key:
            _set_value(values, key, check.get('evidence_path'))

    for item in payload.get('manual_evidence') or []:
        if not isinstance(item, dict) or item.get('status') != 'provided':
            continue
        key = MANUAL_VALUE_KEYS_BY_LABEL.get(str(item.get('label') or ''))
        if key:
            _set_value(values, key, item.get('evidence'))

    return {
        'schema_version': VALUES_SCHEMA_VERSION,
        'generated_at': payload.get('finished_at') or _iso_now(),
        'source': 'hosted_rc_evidence_check',
        'source_evidence': _relative_or_absolute(DEFAULT_EVIDENCE_REPORT),
        'status': payload.get('status') or 'unknown',
        'usable_for_signoff': payload.get('status') == 'passed',
        'values': values,
        'sensitive_omitted': sorted(SENSITIVE_COMMAND_FLAGS),
    }


def render_markdown(payload: dict) -> str:
    rows = [
        '| Check | Status | Exit | Evidence | Evidence target | Missing inputs | Validation errors |',
        '| --- | --- | ---: | --- | --- | --- | --- |',
    ]
    for check in payload.get('checks') or []:
        exit_code = '' if check.get('returncode') is None else str(check.get('returncode'))
        rows.append(
            f"| {check.get('label')} | {check.get('status')} | {exit_code} | "
            f"`{check.get('evidence_path') or ''}` | `{check.get('evidence_target_url') or ''}` | "
            f"{', '.join(check.get('missing_inputs') or []) or ''} | "
            f"{'; '.join(check.get('validation_errors') or []) or ''} |"
        )

    manual_rows = ['| Evidence | Status | Link/path/details | Error |', '| --- | --- | --- | --- |']
    for item in payload.get('manual_evidence') or []:
        manual_rows.append(
            f"| {item.get('label')} | {item.get('status')} | {item.get('evidence') or ''} | "
            f"{item.get('error') or ''} |"
        )

    command_rows = ['| Check | Command |', '| --- | --- |']
    for check in payload.get('checks') or []:
        command_rows.append(f"| {check.get('label')} | `{check.get('command') or ''}` |")

    return '\n'.join(
        [
            '# Hosted RC Evidence',
            '',
            f"- Status: {payload.get('status') or 'unknown'}",
            f"- Automated status: {payload.get('automated_status') or 'unknown'}",
            f"- Evidence schema version: {payload.get('schema_version') or 'unknown'}",
            f"- Generator: `{(payload.get('generator') or {}).get('path') or 'unknown'}`",
            f"- Generator SHA256: `{(payload.get('generator') or {}).get('sha256') or 'unknown'}`",
            f"- Command plan SHA256: `{payload.get('command_plan_sha256') or 'unknown'}`",
            f"- Started: {payload.get('started_at') or 'unknown'}",
            f"- Finished: {payload.get('finished_at') or 'unknown'}",
            f"- Target URL: `{payload.get('target_url') or 'missing'}`",
            f"- Workspace ID: `{payload.get('workspace_id') or 'missing'}`",
            f"- Release: {payload.get('release') or 'unknown'}",
            f"- Environment: {payload.get('environment') or 'unknown'}",
            f"- Socket.IO worker model: {payload.get('socketio_worker_model') or 'missing'}",
            f"- Socket.IO staging proof: `{payload.get('socketio_staging_proof') or 'missing'}`",
            f"- Database: {payload.get('database') or 'missing'}",
            f"- LLM provider/model: {payload.get('llm_provider_model') or 'missing'}",
            f"- Observability provider: {payload.get('observability_provider') or 'missing'}",
            f"- Alert owner: {payload.get('alert_owner') or 'missing'}",
            f"- Dry run: {payload.get('dry_run')}",
            '',
            '## Automated Checks',
            '',
            *rows,
            '',
            '## Manual Evidence Still Required',
            '',
            *manual_rows,
            '',
            '## Command Plan',
            '',
            *command_rows,
            '',
        ]
    )


def write_reports(
    payload: dict,
    *,
    evidence_report: pathlib.Path,
    json_output: pathlib.Path | None,
    values_output: pathlib.Path | None,
) -> None:
    report_path = _resolve_repo_path(evidence_report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown(payload), encoding='utf-8')
    if json_output is not None:
        json_path = _resolve_repo_path(json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    if values_output is not None:
        values_path = _resolve_repo_path(values_output)
        values_path.parent.mkdir(parents=True, exist_ok=True)
        values_payload = build_values_payload(payload)
        values_payload['source_evidence'] = _relative_or_absolute(report_path)
        values_path.write_text(json.dumps(values_payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run hosted/staging RC evidence checks as one command.')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--target-url', default='')
    parser.add_argument('--env-file', type=pathlib.Path, default=None)
    parser.add_argument('--auth-token', default='')
    parser.add_argument('--workspace-id', default='')
    parser.add_argument('--workspace-token', default='')
    parser.add_argument('--non-admin-token', default='')
    parser.add_argument('--campaign-id', type=int, default=0)
    parser.add_argument('--session-id', type=int, default=0)
    parser.add_argument('--player-id', type=int, default=0)
    parser.add_argument('--timeout-seconds', type=float, default=15.0)
    parser.add_argument('--cookie-account-intent', choices=('signup', 'login'), default='signup')
    parser.add_argument('--cookie-username', default='')
    parser.add_argument('--cookie-password', default='')
    parser.add_argument('--cookie-workspace-name', default='Hosted RC Cookie Smoke')
    parser.add_argument('--socketio-path', default='socket.io')
    parser.add_argument(
        '--socketio-origin',
        default='',
        help='Origin header for the deployment-readiness forced WebSocket probe.',
    )
    parser.add_argument('--keep-imported-session', action='store_true')
    parser.add_argument('--same-origin-deployment', action='store_true')
    parser.add_argument('--auth-storage-exception', default='')
    parser.add_argument(
        '--socketio-staging-proof',
        default='',
        help='Future-only research context; cannot satisfy hosted RC1 single-worker sign-off.',
    )
    parser.add_argument('--allow-fallback-provider', action='store_true')
    parser.add_argument('--allow-non-production-target', action='store_true')
    parser.add_argument('--release', default='RC1')
    parser.add_argument('--commit-sha', default='')
    parser.add_argument('--environment', default='staging')
    parser.add_argument('--socketio-worker-model', choices=('single',), default='')
    parser.add_argument('--database', default='')
    parser.add_argument('--llm-provider-model', default='')
    parser.add_argument('--observability-provider', default='')
    parser.add_argument('--alert-owner', default='')
    parser.add_argument('--hosted-backup-restore-evidence', default='')
    parser.add_argument('--hosted-worker-process-evidence', default='')
    parser.add_argument('--source-archive-attachment-evidence', default='')
    parser.add_argument('--external-telemetry-receipt', default='')
    parser.add_argument('--skip-deployment-readiness', action='store_true')
    parser.add_argument('--skip-cookie-auth', action='store_true')
    parser.add_argument('--skip-security-forbidden', action='store_true')
    parser.add_argument('--skip-export-import', action='store_true')
    parser.add_argument('--skip-beta-slo', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--evidence-report', type=pathlib.Path, default=DEFAULT_EVIDENCE_REPORT)
    parser.add_argument('--json-output', type=pathlib.Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument('--values-output', type=pathlib.Path, default=DEFAULT_VALUES_OUTPUT)
    parser.add_argument('--no-values-output', action='store_true')
    parser.add_argument(
        '--preserve-existing-real-evidence',
        action='store_true',
        help='Do not overwrite an existing non-planned hosted RC evidence report.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.preserve_existing_real_evidence and _should_preserve_existing_real_evidence(
        args.evidence_report, args.json_output
    ):
        print(
            '[hosted-rc] Existing hosted RC evidence appears to be a real run; '
            f'preserving {_relative_or_absolute(_resolve_repo_path(args.evidence_report))}.'
        )
        return 0
    plan = build_command_plan(args)
    if args.list:
        for check in plan:
            missing = f" missing: {', '.join(check.required)}" if check.required else ''
            print(f'{check.label}: {_command_text(check)}{missing}')
        return 0
    started_at = _iso_now()
    status, results = run_plan(plan, dry_run=args.dry_run)
    finished_at = _iso_now()
    payload = build_payload(status=status, results=results, args=args, started_at=started_at, finished_at=finished_at)
    values_output = None if args.no_values_output else args.values_output
    write_reports(payload, evidence_report=args.evidence_report, json_output=args.json_output, values_output=values_output)
    print(f'[hosted-rc] Evidence report written to {_relative_or_absolute(_resolve_repo_path(args.evidence_report))}.')
    print(f'[hosted-rc] JSON written to {_relative_or_absolute(_resolve_repo_path(args.json_output))}.')
    if values_output is not None:
        print(f'[hosted-rc] External proof values fragment written to {_relative_or_absolute(_resolve_repo_path(values_output))}.')
    return 0 if payload.get('status') in {'passed', 'planned'} else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
