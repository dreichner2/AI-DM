#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_EXTERNAL_INPUTS_JSON = REPO_ROOT / 'tmp' / 'release' / 'external-proof-inputs.json'
DEFAULT_VALUES = REPO_ROOT / 'tmp' / 'release' / 'external-proof-values.json'
DEFAULT_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'external-proof-values-status.md'
DEFAULT_JSON_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'external-proof-values-status.json'

MISSING_VALUES = {'', 'missing', 'none', 'not checked', 'placeholder', 'tbd', 'todo', 'unknown', 'isolated local runtime'}
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
HOSTED_LOCAL_EVIDENCE_FIELDS = {
    'deployment_readiness_evidence',
    'hosted_env_config_evidence',
    'hosted_cookie_auth_evidence',
    'hosted_non_admin_forbidden_evidence',
    'security_forbidden_evidence',
    'hosted_export_import_evidence',
    'export_import_evidence',
    'hosted_beta_slo_baseline_evidence',
    'beta_slo_baseline_evidence',
}
LOCAL_EVIDENCE_WITHOUT_STATUS = {'hosted_beta_slo_baseline_evidence', 'beta_slo_baseline_evidence'}
SHA256_RE = re.compile(r'\b[a-fA-F0-9]{64}\b')
GITHUB_RUN_URL_RE = re.compile(r'^https://github\.com/([^/\s]+)/([^/\s]+)/actions/runs/\d+(?:[/?#].*)?$')
GITHUB_RUN_URL_KEYS = ('aidm_ci_run_url', 'closed_beta_rc_run_url')
GITHUB_REMOTE_RE = re.compile(r'(?:github\.com[:/])([^/\s]+)/([^/\s]+?)(?:\.git)?/?$')


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


def _normalize_target(value: Any) -> str:
    return _real_text(value).rstrip('/')


def _metadata_key(label: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', label.strip().lower()).strip('_')


def _markdown_metadata(path: pathlib.Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line.startswith('- ') or ':' not in line:
            continue
        label, value = line[2:].split(':', 1)
        metadata[_metadata_key(label)] = value.strip().strip('`')
    return metadata


def _json_metadata(path: pathlib.Path) -> dict[str, str]:
    parsed = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(parsed, dict):
        return {}
    return {
        key: text
        for key in (
            'status',
            'commit',
            'worktree',
            'target_url',
            'mode',
            'complete_count',
            'required_count',
            'pending_count',
        )
        if (text := _real_text(parsed.get(key)))
    }


def _local_evidence_path(evidence: str, *, values_path: pathlib.Path) -> pathlib.Path | None:
    text = _real_text(evidence)
    if not text or _is_real_url(text):
        return None
    candidate = pathlib.Path(text)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    resolved_values_path = _resolve_repo_path(values_path)
    candidates = [resolved_values_path.parent / candidate, REPO_ROOT / candidate]
    return next((path for path in candidates if path.exists()), None)


def _local_evidence_metadata(evidence: str, *, values_path: pathlib.Path) -> tuple[dict[str, str], str]:
    text = _real_text(evidence)
    if not text or _is_real_url(text):
        return {}, ''
    path = _local_evidence_path(text, values_path=values_path)
    if path is None:
        return {}, f'provided local evidence path does not exist: {text}'
    try:
        if path.suffix.lower() == '.json':
            return _json_metadata(path), ''
        return _markdown_metadata(path), ''
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f'could not read local evidence {text}: {exc}'


def _load_json_object(path: pathlib.Path, *, missing_ok: bool = False) -> tuple[dict[str, Any], str]:
    resolved = _resolve_repo_path(path)
    if not resolved.exists():
        if missing_ok:
            return {}, ''
        return {}, f'missing JSON file: {_relative_or_absolute(resolved)}'
    try:
        parsed = json.loads(resolved.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return {}, f'invalid JSON in {_relative_or_absolute(resolved)}: {exc}'
    if not isinstance(parsed, dict):
        return {}, f'JSON root must be an object: {_relative_or_absolute(resolved)}'
    return parsed, ''


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


def _raw_values(payload: dict[str, Any]) -> dict[str, Any]:
    values = payload.get('values')
    return values if isinstance(values, dict) else payload


def _values_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    return {
        str(key): text
        for key, value in _raw_values(payload).items()
        if (text := _real_text(value))
    }


def _key_looks_sensitive(key: str) -> bool:
    normalized = re.sub(r'[^a-z0-9]+', '_', key.strip().lower()).strip('_')
    if normalized in SENSITIVE_VALUE_KEYS:
        return True
    return any(marker in normalized for marker in SENSITIVE_KEY_MARKERS)


def _sensitive_values(payload: dict[str, Any]) -> list[str]:
    raw = _raw_values(payload)
    paths: set[str] = set()
    raw_is_nested = isinstance(payload.get('values'), dict)
    for key, value in raw.items():
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


def _field_key(field: dict[str, Any]) -> str:
    return _text(field.get('key'))


def _field_current_value(field: dict[str, Any]) -> str:
    return _real_text(field.get('current_value')) if not field.get('sensitive') else ''


def _field_is_required(field: dict[str, Any]) -> bool:
    return field.get('status') == 'required'


def _field_counts_toward_required(field: dict[str, Any], current_value: str, values: dict[str, str]) -> bool:
    if _field_is_required(field) or _conditional_field_required(field, values):
        return True
    return bool(current_value) and field.get('status') == 'provided-context'


def _conditional_field_required(field: dict[str, Any], values: dict[str, str]) -> bool:
    del field, values
    # Historical multi-worker proof fields remain readable, but no amount of
    # staging proof can make a non-single worker model valid for this release.
    return False


def _socketio_worker_model_errors(values: dict[str, str]) -> list[str]:
    worker_model = values.get('socketio_worker_model', '').strip().lower()
    if not worker_model or worker_model == 'single':
        return []
    return [
        'socketio_worker_model: hosted production currently supports only single; '
        f'{worker_model} is unsupported even when Socket.IO staging proof is provided'
    ]


def _metadata_value(payload: dict[str, Any], values: dict[str, str], key: str) -> str:
    if key == 'commit':
        return _real_text(payload.get('commit')) or values.get('signed_off_commit_sha', '') or values.get('commit', '')
    return _real_text(payload.get(key)) or values.get(key, '')


def _signed_at_valid(value: str) -> bool:
    if not value:
        return False
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    return True


def _metadata_errors(payload: dict[str, Any], values: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if not _metadata_value(payload, values, 'release'):
        errors.append('release must be filled')
    if not _metadata_value(payload, values, 'commit'):
        errors.append('commit or signed_off_commit_sha must be filled')
    if not _is_hosted_target(_metadata_value(payload, values, 'target_url')):
        errors.append('target_url must be a real hosted/staging URL, not localhost, example, or placeholder')
    if not _metadata_value(payload, values, 'signed_by'):
        errors.append('signed_by must identify the operator signing off')
    signed_at = _metadata_value(payload, values, 'signed_at')
    if not _signed_at_valid(signed_at):
        errors.append('signed_at must be an ISO-8601 timestamp')
    return errors


def _clean_worktree_evidence_errors(values: dict[str, str], *, values_path: pathlib.Path) -> list[str]:
    evidence = values.get('clean_worktree_rc_evidence', '')
    if not evidence or _is_real_url(evidence):
        return []
    metadata, metadata_error = _local_evidence_metadata(evidence, values_path=values_path)
    if metadata_error:
        return [f'clean_worktree_rc_evidence: {metadata_error}']
    errors: list[str] = []
    status = (metadata.get('status') or '').strip().lower()
    if status != 'passed':
        errors.append(f'clean_worktree_rc_evidence: RC evidence status is not passed: {status or "missing"}')
    worktree = (metadata.get('worktree') or '').strip().lower()
    if worktree != 'clean':
        errors.append(f'clean_worktree_rc_evidence: RC evidence worktree is not clean: {worktree or "missing"}')
    expected_commit = values.get('signed_off_commit_sha', '') or values.get('commit', '')
    evidence_commit = metadata.get('commit') or ''
    if expected_commit and evidence_commit and expected_commit != evidence_commit:
        errors.append(
            'clean_worktree_rc_evidence: RC evidence commit '
            f'{evidence_commit} does not match signed_off_commit_sha {expected_commit}'
        )
    return errors


def _operator_signoff_evidence_errors(values: dict[str, str], *, values_path: pathlib.Path) -> list[str]:
    evidence = values.get('operator_signoff_manifest_evidence', '')
    if not evidence or _is_real_url(evidence):
        return []
    metadata, metadata_error = _local_evidence_metadata(evidence, values_path=values_path)
    if metadata_error:
        return [f'operator_signoff_manifest_evidence: {metadata_error}']
    errors: list[str] = []
    status = (metadata.get('status') or '').strip().lower()
    if status != 'passed':
        errors.append(f'operator_signoff_manifest_evidence: operator signoff status is not passed: {status or "missing"}')
    required_complete = (metadata.get('required_complete') or '').strip()
    if required_complete:
        parts = [part.strip() for part in required_complete.split('/', 1)]
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit() or parts[0] != parts[1]:
            errors.append(
                'operator_signoff_manifest_evidence: required complete is not full: '
                f'{required_complete}'
            )
        return errors
    complete_count = metadata.get('complete_count') or ''
    required_count = metadata.get('required_count') or ''
    pending_count = metadata.get('pending_count') or ''
    if not complete_count or not required_count:
        errors.append('operator_signoff_manifest_evidence: missing required completion metadata')
    elif complete_count != required_count:
        errors.append(
            'operator_signoff_manifest_evidence: complete_count '
            f'{complete_count} does not match required_count {required_count}'
        )
    if pending_count and pending_count != '0':
        errors.append(f'operator_signoff_manifest_evidence: pending_count is not zero: {pending_count}')
    return errors


def _hosted_local_evidence_errors(values: dict[str, str], *, values_path: pathlib.Path) -> list[str]:
    expected_target = _normalize_target(values.get('target_url', ''))
    errors: list[str] = []
    for key in sorted(HOSTED_LOCAL_EVIDENCE_FIELDS):
        evidence = values.get(key, '')
        if not evidence or _is_real_url(evidence):
            continue
        metadata, metadata_error = _local_evidence_metadata(evidence, values_path=values_path)
        if metadata_error:
            errors.append(f'{key}: {metadata_error}')
            continue
        status = (metadata.get('status') or '').strip().lower()
        if key not in LOCAL_EVIDENCE_WITHOUT_STATUS and status != 'passed':
            errors.append(f'{key}: local evidence status is not passed: {status or "missing"}')
        evidence_target = _normalize_target(metadata.get('target_url', ''))
        if not _is_hosted_target(evidence_target):
            errors.append(
                f'{key}: local evidence target URL is not a real hosted/staging target: '
                f'{evidence_target or "missing"}'
            )
            continue
        if expected_target and evidence_target != expected_target:
            errors.append(
                f'{key}: local evidence target URL {evidence_target} does not match target_url {expected_target}'
            )
    return errors


def _source_archive_sha256(external_inputs: dict[str, Any]) -> str:
    source_archive = external_inputs.get('source_archive')
    if not isinstance(source_archive, dict):
        return ''
    return _real_text(source_archive.get('sha256'))


def _source_archive_attachment_errors(values: dict[str, str], external_inputs: dict[str, Any]) -> list[str]:
    evidence = values.get('source_archive_attachment_evidence', '')
    if not evidence:
        return []
    expected_sha256 = _source_archive_sha256(external_inputs)
    if expected_sha256:
        if expected_sha256.lower() not in evidence.lower():
            return [
                'source_archive_attachment_evidence: evidence must include current source archive '
                f'sha256 {expected_sha256}'
            ]
        return []
    if not SHA256_RE.search(evidence):
        return ['source_archive_attachment_evidence: evidence must include a SHA256 checksum']
    return []


def _expected_github_repository(external_inputs: dict[str, Any]) -> str:
    github_actions = external_inputs.get('github_actions')
    if isinstance(github_actions, dict):
        repository = _real_text(github_actions.get('repository'))
        if repository and repository != 'local':
            return repository
    return _git_repository()


def _github_actions_url_errors(values: dict[str, str], external_inputs: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_repository = _expected_github_repository(external_inputs)
    for key in GITHUB_RUN_URL_KEYS:
        value = values.get(key, '')
        if not value:
            continue
        match = GITHUB_RUN_URL_RE.match(value)
        if not match:
            errors.append(f'{key}: value must look like https://github.com/<owner>/<repo>/actions/runs/<run-id>')
            continue
        if expected_repository:
            owner, repo = match.groups()
            actual_repository = f'{owner}/{repo}'
            if actual_repository.lower() != expected_repository.lower():
                errors.append(f'{key}: run URL repository {actual_repository} does not match {expected_repository}')
    return errors


def build_report(
    *,
    external_inputs: dict[str, Any],
    values_payload: dict[str, Any],
    values_present: bool,
    values_path: pathlib.Path,
    external_inputs_path: pathlib.Path,
    generated_at: str,
) -> dict[str, Any]:
    values = _values_from_payload(values_payload)
    invalid_errors = [
        f'command-only or secret-like field persisted in values file: {key}'
        for key in _sensitive_values(values_payload)
    ]
    invalid_errors.extend(_socketio_worker_model_errors(values))
    if values_present:
        invalid_errors.extend(_clean_worktree_evidence_errors(values, values_path=values_path))
        invalid_errors.extend(_operator_signoff_evidence_errors(values, values_path=values_path))
        invalid_errors.extend(_hosted_local_evidence_errors(values, values_path=values_path))
        invalid_errors.extend(_source_archive_attachment_errors(values, external_inputs))
        invalid_errors.extend(_github_actions_url_errors(values, external_inputs))
    fields = [field for field in external_inputs.get('fields') or [] if isinstance(field, dict)]
    field_rows: list[dict[str, Any]] = []
    missing_required: list[str] = []
    command_only: list[str] = []

    for field in fields:
        key = _field_key(field)
        if not key:
            continue
        sensitive = bool(field.get('sensitive')) or key in SENSITIVE_VALUE_KEYS
        current_value = _field_current_value(field)
        required = _field_counts_toward_required(field, current_value, values)
        provided_value = values.get(key, '')
        if sensitive:
            if required:
                command_only.append(key)
            state = 'command-only'
            complete = True
        elif current_value:
            state = 'provided-context'
            complete = True
        elif provided_value:
            state = 'provided'
            complete = True
        elif required:
            state = 'missing'
            complete = False
            missing_required.append(key)
        else:
            state = 'not-required'
            complete = True

        field_rows.append(
            {
                'key': key,
                'status': state,
                'required': required,
                'sensitive': sensitive,
                'current_value': current_value,
                'value_present': bool(provided_value),
                'required_for': field.get('required_for') or [],
                'notes': field.get('notes') or '',
                'complete': complete,
            }
        )

    metadata_errors = [] if not values_present else _metadata_errors(values_payload, values)
    if invalid_errors:
        status = 'invalid'
    elif not values_present or missing_required or metadata_errors:
        status = 'incomplete'
    else:
        status = 'passed'

    required_count = sum(1 for row in field_rows if row['required'] and not row['sensitive'])
    required_complete = sum(1 for row in field_rows if row['required'] and not row['sensitive'] and row['complete'])
    return {
        'generated_at': generated_at,
        'status': status,
        'values_path': str(_resolve_repo_path(values_path)),
        'values_present': values_present,
        'external_inputs_path': str(_resolve_repo_path(external_inputs_path)),
        'external_inputs_status': external_inputs.get('status') or 'missing',
        'required_complete': f'{required_complete}/{required_count}',
        'missing_required_count': len(missing_required),
        'metadata_error_count': len(metadata_errors),
        'invalid_error_count': len(invalid_errors),
        'command_only_fields': command_only,
        'missing_required_fields': missing_required,
        'metadata_errors': metadata_errors,
        'invalid_errors': invalid_errors,
        'fields': field_rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    field_rows = [
        '| Field | Status | Required | Value present | Notes |',
        '| --- | --- | --- | --- | --- |',
    ]
    for field in report.get('fields') or []:
        field_rows.append(
            f"| `{field.get('key')}` | {field.get('status')} | {field.get('required')} | "
            f"{field.get('value_present')} | {field.get('notes') or ''} |"
        )
    if len(field_rows) == 2:
        field_rows.append('| None |  |  |  |  |')

    def bullet_rows(values: list[str]) -> list[str]:
        return [f'- {value}' for value in values] if values else ['- None']

    return '\n'.join(
        [
            '# External Proof Values Check',
            '',
            f"- Generated: {report.get('generated_at')}",
            f"- Status: {report.get('status')}",
            f"- Values file: `{_relative_or_absolute(report.get('values_path') or '')}`",
            f"- Values file present: {report.get('values_present')}",
            f"- External inputs: `{_relative_or_absolute(report.get('external_inputs_path') or '')}`",
            f"- External inputs status: {report.get('external_inputs_status')}",
            f"- Required complete: {report.get('required_complete')}",
            f"- Missing required fields: {report.get('missing_required_count')}",
            f"- Metadata errors: {report.get('metadata_error_count')}",
            f"- Invalid errors: {report.get('invalid_error_count')}",
            '',
            '## Missing Required Fields',
            '',
            *bullet_rows(report.get('missing_required_fields') or []),
            '',
            '## Metadata Errors',
            '',
            *bullet_rows(report.get('metadata_errors') or []),
            '',
            '## Invalid Errors',
            '',
            *bullet_rows(report.get('invalid_errors') or []),
            '',
            '## Command-Only Fields',
            '',
            *bullet_rows(report.get('command_only_fields') or []),
            '',
            '## Field Status',
            '',
            *field_rows,
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
    parser = argparse.ArgumentParser(description='Check filled external proof values before final operator signoff.')
    parser.add_argument('--external-inputs-json', type=pathlib.Path, default=DEFAULT_EXTERNAL_INPUTS_JSON)
    parser.add_argument('--values', type=pathlib.Path, default=DEFAULT_VALUES)
    parser.add_argument('--output', type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--json-output', type=pathlib.Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument('--require-complete', action='store_true')
    parser.add_argument('--generated-at', default='', help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generated_at = args.generated_at or _iso_now()
    external_inputs, external_inputs_error = _load_json_object(args.external_inputs_json)
    values_path = _resolve_repo_path(args.values)
    values_present = values_path.exists()
    values_payload, values_error = _load_json_object(args.values, missing_ok=True)
    if external_inputs_error:
        print(f'[external-proof-values-check] {external_inputs_error}', file=sys.stderr)
        return 2
    if values_error:
        print(f'[external-proof-values-check] {values_error}', file=sys.stderr)
        return 2

    report = build_report(
        external_inputs=external_inputs,
        values_payload=values_payload,
        values_present=values_present,
        values_path=args.values,
        external_inputs_path=args.external_inputs_json,
        generated_at=generated_at,
    )
    write_report(report, output=args.output, json_output=args.json_output)
    print(
        '[external-proof-values-check] '
        f"{report['status']}; report written to {_relative_or_absolute(_resolve_repo_path(args.output))}."
    )
    if report.get('invalid_errors'):
        return 2
    if args.require_complete and report.get('status') != 'passed':
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
