#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from datetime import UTC, datetime
from typing import Any

try:
    from scripts.render_operator_signoff_status import (
        DEFAULT_DRAFT_OUTPUT,
        ITEM_SPECS,
        build_report,
        draft_manifest_from_packet,
        example_manifest,
        write_report,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised when run as a script path
    from render_operator_signoff_status import (  # type: ignore[no-redef]
        DEFAULT_DRAFT_OUTPUT,
        ITEM_SPECS,
        build_report,
        draft_manifest_from_packet,
        example_manifest,
        write_report,
    )


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PACKET_JSON = REPO_ROOT / 'tmp' / 'release' / 'release-evidence-packet.json'
DEFAULT_EXTERNAL_INPUTS_JSON = REPO_ROOT / 'tmp' / 'release' / 'external-proof-inputs.json'
DEFAULT_VALUES = REPO_ROOT / 'tmp' / 'release' / 'external-proof-values.json'
DEFAULT_VALUES_TEMPLATE = REPO_ROOT / 'tmp' / 'release' / 'external-proof-values.example.json'
DEFAULT_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff.from-inputs.json'
DEFAULT_STATUS_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff.from-inputs-status.md'
DEFAULT_STATUS_JSON_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'operator-signoff.from-inputs-status.json'

MISSING_VALUES = {'', 'missing', 'none', 'not checked', 'unknown', 'isolated local runtime'}
SENSITIVE_VALUE_KEYS = {'operator_auth_token', 'non_admin_token'}
SENSITIVE_KEY_MARKERS = (
    'api_key',
    'apikey',
    'auth_token',
    'client_secret',
    'password',
    'private_key',
    'secret',
    'token',
)
IGNORED_SENSITIVE_SCAN_KEYS = {
    'field_notes',
    'instructions',
    'sensitive_fields_omitted',
    'values',
}
PREVIEW_METADATA_ERROR_PREFIXES = (
    'release must be filled',
    'commit must be the signed-off commit SHA',
    'target_url must be a real hosted/staging URL',
    'signed_by must identify the operator signing off',
    'signed_at must be filled',
)

SIGNOFF_VALUE_MAP: dict[str, tuple[str, ...]] = {
    'clean_signed_off_worktree': ('clean_worktree_rc_evidence',),
    'github_actions_aidm_ci': ('aidm_ci_run_url',),
    'github_actions_closed_beta_rc': ('closed_beta_rc_run_url',),
    'github_actions_rc_artifact': ('closed_beta_rc_artifact_reference',),
    'hosted_env_config': ('deployment_readiness_evidence', 'hosted_env_config_evidence'),
    'hosted_deployment_readiness': ('deployment_readiness_evidence',),
    'hosted_cookie_auth': ('hosted_cookie_auth_evidence',),
    'hosted_non_admin_forbidden': ('hosted_non_admin_forbidden_evidence', 'security_forbidden_evidence'),
    'hosted_export_import': ('hosted_export_import_evidence', 'export_import_evidence'),
    'hosted_backup_restore': ('hosted_backup_restore_evidence',),
    'hosted_socketio_worker_process': ('hosted_worker_process_evidence',),
    'hosted_beta_slo_baseline': ('hosted_beta_slo_baseline_evidence', 'beta_slo_baseline_evidence'),
    'hosted_external_telemetry': ('external_telemetry_receipt',),
    'source_archive_attachment': ('source_archive_attachment_evidence',),
    'rc_issue_closure_review': ('rc_issue_closure_review',),
    'frontend_npm_ci': ('frontend_npm_ci_evidence', 'aidm_ci_run_url'),
    'make_clean': ('make_clean_evidence',),
    'make_clean_deps': ('make_clean_deps_evidence',),
}


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


def _real_text(value: Any) -> str:
    text = _text(value)
    if not text or '<' in text or '>' in text:
        return ''
    return '' if text.lower() in MISSING_VALUES else text


def _load_json_object(path: pathlib.Path, *, missing_ok: bool = False) -> dict[str, Any]:
    resolved = _resolve_repo_path(path)
    if not resolved.exists():
        if missing_ok:
            return {}
        raise SystemExit(f'[operator-signoff-from-inputs] Missing JSON file: {_relative_or_absolute(resolved)}')
    try:
        parsed = json.loads(resolved.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise SystemExit(f'[operator-signoff-from-inputs] Invalid JSON in {_relative_or_absolute(resolved)}: {exc}') from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f'[operator-signoff-from-inputs] JSON root must be an object: {_relative_or_absolute(resolved)}')
    return parsed


def _write_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    resolved = _resolve_repo_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def write_self_evidence_to_values(*, values_path: pathlib.Path, status_output: pathlib.Path) -> str:
    resolved_values = _resolve_repo_path(values_path)
    if not resolved_values.exists():
        raise ValueError(
            f'external proof values file is missing: {_relative_or_absolute(resolved_values)}'
        )
    payload = _load_json_object(values_path)
    evidence = _relative_or_absolute(_resolve_repo_path(status_output))
    if isinstance(payload.get('values'), dict):
        payload['values']['operator_signoff_manifest_evidence'] = evidence
    else:
        payload['operator_signoff_manifest_evidence'] = evidence
    _write_json(values_path, payload)
    return evidence


def _values_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    raw_values = payload.get('values') if isinstance(payload.get('values'), dict) else payload
    values: dict[str, str] = {}
    for key, value in raw_values.items():
        text = _real_text(value)
        if text:
            values[str(key)] = text
    return values


def _key_looks_sensitive(key: str) -> bool:
    normalized = re.sub(r'[^a-z0-9]+', '_', key.strip().lower()).strip('_')
    if normalized in SENSITIVE_VALUE_KEYS:
        return True
    return any(marker in normalized for marker in SENSITIVE_KEY_MARKERS)


def _sensitive_values_in_payload(payload: dict[str, Any]) -> list[str]:
    raw_values = payload.get('values') if isinstance(payload.get('values'), dict) else payload
    raw_is_nested = isinstance(payload.get('values'), dict)
    paths: set[str] = set()
    for key, value in raw_values.items():
        key_text = str(key)
        if _real_text(value) and _key_looks_sensitive(key_text):
            paths.add(f'values.{key_text}' if raw_is_nested else key_text)
    for key, value in payload.items():
        key_text = str(key)
        if key_text in IGNORED_SENSITIVE_SCAN_KEYS:
            continue
        if _real_text(value) and _key_looks_sensitive(key_text):
            paths.add(key_text)
    return sorted(paths)


def _first_value(values: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _real_text(values.get(key))
        if value:
            return value
    return ''


def _set_item(manifest: dict[str, Any], key: str, *, status: str, evidence: str = '', notes: str = '') -> None:
    items = manifest.setdefault('items', {})
    item = items.setdefault(key, {})
    item['status'] = status
    item['evidence'] = evidence
    item['notes'] = notes


def _apply_top_level_values(manifest: dict[str, Any], values_payload: dict[str, Any], values: dict[str, str], generated_at: str) -> None:
    manifest['generated_from_external_values_at'] = generated_at
    manifest['release'] = _real_text(values_payload.get('release')) or values.get('release') or manifest.get('release') or 'RC1'
    manifest['commit'] = (
        _real_text(values_payload.get('commit'))
        or values.get('signed_off_commit_sha')
        or values.get('commit')
        or manifest.get('commit')
        or '<signed-off-commit-sha>'
    )
    manifest['target_url'] = (
        _real_text(values_payload.get('target_url'))
        or values.get('target_url')
        or manifest.get('target_url')
        or 'https://<hosted-staging-target>'
    )
    manifest['signed_by'] = (
        _real_text(values_payload.get('signed_by'))
        or values.get('signed_by')
        or manifest.get('signed_by')
        or '<operator-name>'
    )
    manifest['signed_at'] = _real_text(values_payload.get('signed_at')) or values.get('signed_at') or manifest.get('signed_at') or generated_at
    manifest['source_values'] = 'tmp/release/external-proof-values.json'


def _multi_worker_status(values: dict[str, str]) -> tuple[str, str, str]:
    worker_model = values.get('socketio_worker_model', '').lower()
    worker_evidence = values.get('hosted_worker_process_evidence', '')
    single_worker_evidence = worker_evidence or values.get('multi_worker_socketio_staging_evidence', '')
    if worker_model and worker_model != 'single':
        return (
            'pending',
            '',
            f'{worker_model} Socket.IO mode is unsupported for hosted production in this release; use single.',
        )
    if worker_model == 'single':
        if single_worker_evidence:
            notes = 'RC1 target uses single-worker mode; sticky/message-queue staging proof is not applicable.'
            return 'not_applicable', single_worker_evidence, notes
        return (
            'pending',
            '',
            'Single-worker mode still needs hosted worker-process evidence before multi-worker proof is not applicable.',
        )
    return 'pending', '', 'Set socketio_worker_model=single and provide hosted worker-process evidence.'


def build_manifest_from_values(
    *,
    draft_manifest: dict[str, Any],
    values_payload: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    sensitive_keys = _sensitive_values_in_payload(values_payload)
    if sensitive_keys:
        raise ValueError(
            'external proof values contains command-only sensitive fields: '
            + ', '.join(sensitive_keys)
            + '. Pass live auth tokens through command arguments or a secret manager, not tmp/release/external-proof-values.json.'
        )
    manifest = json.loads(json.dumps(draft_manifest or example_manifest()))
    values = _values_from_payload(values_payload)
    worker_model = values.get('socketio_worker_model', '').strip().lower()
    if worker_model and worker_model != 'single':
        raise ValueError(
            'socketio_worker_model must be single for hosted production in this release; '
            f'{worker_model} is unsupported even when Socket.IO staging proof is supplied.'
        )
    _apply_top_level_values(manifest, values_payload, values, generated_at)

    for spec in ITEM_SPECS:
        if spec.key == 'multi_worker_socketio_staging':
            status, evidence, notes = _multi_worker_status(values)
            _set_item(manifest, spec.key, status=status, evidence=evidence, notes=notes)
            continue
        evidence = _first_value(values, SIGNOFF_VALUE_MAP.get(spec.key, ()))
        if evidence:
            _set_item(
                manifest,
                spec.key,
                status='provided',
                evidence=evidence,
                notes=f'Generated from external proof values for {spec.title}.',
            )

    return manifest


def build_values_template(*, external_inputs: dict[str, Any], generated_at: str) -> dict[str, Any]:
    fields = [field for field in external_inputs.get('fields') or [] if isinstance(field, dict)]
    values: dict[str, str] = {}
    notes: dict[str, str] = {}
    sensitive_fields: list[str] = []
    for field in fields:
        key = _text(field.get('key'))
        if not key:
            continue
        if field.get('sensitive') or key in SENSITIVE_VALUE_KEYS:
            sensitive_fields.append(key)
            continue
        values[key] = _real_text(field.get('current_value'))
        notes[key] = _text(field.get('notes'))

    values.setdefault('deployment_readiness_evidence', '')
    values.setdefault('hosted_cookie_auth_evidence', '')
    values.setdefault('hosted_non_admin_forbidden_evidence', '')
    values.setdefault('hosted_export_import_evidence', '')
    values.setdefault('hosted_beta_slo_baseline_evidence', '')
    values.setdefault('socketio_worker_model', 'single')
    values.setdefault('multi_worker_socketio_staging_not_applicable', 'true')
    return {
        'generated_at': generated_at,
        'instructions': (
            'Copy this file to tmp/release/external-proof-values.json, fill only proof links/paths and '
            'non-secret identifiers needed for signoff, then run make operator-signoff-from-inputs. '
            'Sensitive command-only values are intentionally omitted; pass live auth tokens through command '
            'arguments or a secret manager, never this file.'
        ),
        'sensitive_fields_omitted': sorted(set(sensitive_fields)),
        'release': 'RC1',
        'commit': values.get('signed_off_commit_sha') or '<signed-off-commit-sha>',
        'target_url': values.get('target_url') or 'https://<hosted-staging-target>',
        'signed_by': '<operator-name>',
        'signed_at': '<iso-8601-timestamp>',
        'values': values,
        'field_notes': notes,
    }


def _metadata_only_preview_errors(report: dict[str, Any]) -> bool:
    errors = [str(error) for error in report.get('errors') or []]
    return bool(errors) and all(
        any(error.startswith(prefix) for prefix in PREVIEW_METADATA_ERROR_PREFIXES) for error in errors
    )


def _downgrade_missing_values_preview(report: dict[str, Any], *, values_path: pathlib.Path) -> dict[str, Any]:
    if _resolve_repo_path(values_path).exists():
        return report
    if report.get('status') != 'invalid' or not _metadata_only_preview_errors(report):
        return report
    preview = dict(report)
    preview['status'] = 'incomplete'
    preview['preview_mode'] = True
    preview['preview_reason'] = (
        'tmp/release/external-proof-values.json is missing; this is an incomplete preview generated from '
        'local packet context, not a final operator signoff.'
    )
    return preview


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Build an operator signoff manifest from filled external proof values.')
    parser.add_argument('--draft', type=pathlib.Path, default=DEFAULT_DRAFT_OUTPUT)
    parser.add_argument('--packet-json', type=pathlib.Path, default=DEFAULT_PACKET_JSON)
    parser.add_argument('--values', type=pathlib.Path, default=DEFAULT_VALUES)
    parser.add_argument('--external-inputs-json', type=pathlib.Path, default=DEFAULT_EXTERNAL_INPUTS_JSON)
    parser.add_argument('--output', type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--status-output', type=pathlib.Path, default=DEFAULT_STATUS_OUTPUT)
    parser.add_argument('--status-json-output', type=pathlib.Path, default=DEFAULT_STATUS_JSON_OUTPUT)
    parser.add_argument('--write-values-template', type=pathlib.Path, nargs='?', const=DEFAULT_VALUES_TEMPLATE, default=None)
    parser.add_argument(
        '--write-self-evidence-to-values',
        action='store_true',
        help=(
            'After a passed signoff render, write operator_signoff_manifest_evidence back to '
            'the external proof values file using --status-output.'
        ),
    )
    parser.add_argument('--generated-at', default='', help=argparse.SUPPRESS)
    parser.add_argument('--require-complete', action='store_true')
    return parser


def _default_draft_manifest(
    *,
    draft_path: pathlib.Path,
    packet_path: pathlib.Path,
    generated_at: str,
    draft_explicit: bool,
) -> dict[str, Any]:
    packet = _load_json_object(packet_path, missing_ok=True)
    if not draft_explicit and packet:
        return draft_manifest_from_packet(packet, packet_path=_resolve_repo_path(packet_path), generated_at=generated_at)

    draft = _load_json_object(draft_path, missing_ok=True)
    if draft:
        return draft
    if packet:
        return draft_manifest_from_packet(packet, packet_path=_resolve_repo_path(packet_path), generated_at=generated_at)
    return example_manifest()


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw_argv)
    generated_at = args.generated_at or _iso_now()
    if args.write_values_template is not None:
        external_inputs = _load_json_object(args.external_inputs_json)
        template = build_values_template(external_inputs=external_inputs, generated_at=generated_at)
        _write_json(args.write_values_template, template)
        print(
            '[operator-signoff-from-inputs] Wrote values template to '
            f'{_relative_or_absolute(_resolve_repo_path(args.write_values_template))}.'
        )
        return 0

    packet = _load_json_object(args.packet_json, missing_ok=True)
    draft = _default_draft_manifest(
        draft_path=args.draft,
        packet_path=args.packet_json,
        generated_at=generated_at,
        draft_explicit='--draft' in raw_argv,
    )
    values_payload = _load_json_object(args.values, missing_ok=True)
    try:
        manifest = build_manifest_from_values(
            draft_manifest=draft,
            values_payload=values_payload,
            generated_at=generated_at,
        )
    except ValueError as exc:
        print(f'[operator-signoff-from-inputs] {exc}', file=sys.stderr)
        return 2
    _write_json(args.output, manifest)
    report = build_report(manifest_path=_resolve_repo_path(args.output), generated_at=generated_at, packet=packet)
    report = _downgrade_missing_values_preview(report, values_path=args.values)
    write_report(report, output=args.status_output, json_output=args.status_json_output)
    print(f'[operator-signoff-from-inputs] Wrote manifest to {_relative_or_absolute(_resolve_repo_path(args.output))}.')
    print(f'[operator-signoff-from-inputs] Wrote status to {_relative_or_absolute(_resolve_repo_path(args.status_output))}.')
    if args.status_json_output is not None:
        print(
            '[operator-signoff-from-inputs] Wrote status JSON to '
            f'{_relative_or_absolute(_resolve_repo_path(args.status_json_output))}.'
        )
    if args.write_self_evidence_to_values and report.get('status') == 'passed':
        try:
            evidence = write_self_evidence_to_values(values_path=args.values, status_output=args.status_output)
        except ValueError as exc:
            print(f'[operator-signoff-from-inputs] {exc}', file=sys.stderr)
            return 2
        print(
            '[operator-signoff-from-inputs] Wrote operator_signoff_manifest_evidence to '
            f'{_relative_or_absolute(_resolve_repo_path(args.values))}: {evidence}'
        )
    elif args.write_self_evidence_to_values:
        print(
            '[operator-signoff-from-inputs] Skipped operator_signoff_manifest_evidence update '
            f'because signoff status is {report.get("status") or "unknown"}.'
        )
    if args.require_complete and report.get('status') != 'passed':
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
