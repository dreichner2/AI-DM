#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from datetime import UTC, datetime
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PACKET_JSON = REPO_ROOT / 'tmp' / 'release' / 'release-evidence-packet.json'
DEFAULT_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'release-artifact-consistency.md'
DEFAULT_JSON_OUTPUT = REPO_ROOT / 'tmp' / 'release' / 'release-artifact-consistency.json'
SHA256_LEN = 64


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


def _load_json_object(path: pathlib.Path) -> tuple[dict[str, Any], str]:
    resolved = _resolve_repo_path(path)
    if not resolved.exists():
        return {}, f'packet JSON does not exist: {_relative_or_absolute(resolved)}'
    try:
        parsed = json.loads(resolved.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return {}, f'packet JSON is invalid: {exc}'
    if not isinstance(parsed, dict):
        return {}, 'packet JSON root must be an object'
    return parsed, ''


def _section(packet: dict[str, Any], key: str) -> dict[str, Any]:
    value = packet.get(key) or {}
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _looks_like_sha256(value: str) -> bool:
    return len(value) == SHA256_LEN and all(char in '0123456789abcdefABCDEF' for char in value)


def _file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _add_check(checks: list[dict[str, str]], key: str, status: str, detail: str) -> None:
    checks.append({'key': key, 'status': status, 'detail': detail})


def _sidecar_values(sidecar_path: pathlib.Path) -> tuple[str, str]:
    try:
        text = sidecar_path.read_text(encoding='utf-8').strip()
    except OSError:
        return '', ''
    parts = text.split(maxsplit=1)
    if not parts:
        return '', ''
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else '')


def _resolved_sidecar_target(raw_path: str, *, sidecar_path: pathlib.Path) -> pathlib.Path:
    candidate = pathlib.Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return sidecar_path.parent / candidate


def _check_source_archive(packet: dict[str, Any], checks: list[dict[str, str]]) -> tuple[pathlib.Path | None, str]:
    source = _section(packet, 'source_archive')
    path_text = _text(source.get('path'))
    expected_sha = _text(source.get('sha256')).lower()
    status = _text(source.get('status'))
    if status != 'passed':
        _add_check(checks, 'source_archive_status', 'failed', f'source_archive.status is {status or "missing"}')
    else:
        _add_check(checks, 'source_archive_status', 'passed', 'source archive status is passed')
    if not path_text:
        _add_check(checks, 'source_archive_path', 'failed', 'source_archive.path is missing')
        return None, expected_sha
    archive_path = _resolve_repo_path(pathlib.Path(path_text))
    if not archive_path.exists():
        _add_check(checks, 'source_archive_path', 'failed', f'source archive does not exist: {_relative_or_absolute(archive_path)}')
        return archive_path, expected_sha
    _add_check(checks, 'source_archive_path', 'passed', _relative_or_absolute(archive_path))
    if not _looks_like_sha256(expected_sha):
        _add_check(checks, 'source_archive_sha256_format', 'failed', 'source_archive.sha256 is missing or malformed')
        return archive_path, expected_sha
    _add_check(checks, 'source_archive_sha256_format', 'passed', expected_sha)
    actual_sha = _file_sha256(archive_path)
    if actual_sha != expected_sha:
        _add_check(checks, 'source_archive_sha256_actual', 'failed', f'actual archive sha256 {actual_sha} does not match packet {expected_sha}')
    else:
        _add_check(checks, 'source_archive_sha256_actual', 'passed', actual_sha)
    expected_bytes = source.get('bytes')
    if expected_bytes not in (None, ''):
        try:
            expected_size = int(expected_bytes)
        except (TypeError, ValueError):
            _add_check(checks, 'source_archive_bytes', 'failed', f'source_archive.bytes is not an integer: {expected_bytes}')
        else:
            actual_size = archive_path.stat().st_size
            if actual_size != expected_size:
                _add_check(checks, 'source_archive_bytes', 'failed', f'actual archive bytes {actual_size} do not match packet {expected_size}')
            else:
                _add_check(checks, 'source_archive_bytes', 'passed', str(actual_size))
    sidecar_path = pathlib.Path(str(archive_path) + '.sha256')
    if not sidecar_path.exists():
        _add_check(checks, 'source_archive_sidecar', 'failed', f'sidecar is missing: {_relative_or_absolute(sidecar_path)}')
        return archive_path, expected_sha
    sidecar_sha, sidecar_target = _sidecar_values(sidecar_path)
    if sidecar_sha.lower() != expected_sha:
        _add_check(checks, 'source_archive_sidecar_sha256', 'failed', f'sidecar sha256 {sidecar_sha or "missing"} does not match packet {expected_sha}')
    else:
        _add_check(checks, 'source_archive_sidecar_sha256', 'passed', _relative_or_absolute(sidecar_path))
    if sidecar_target:
        if pathlib.Path(sidecar_target).is_absolute():
            _add_check(
                checks,
                'source_archive_sidecar_path',
                'failed',
                f'sidecar target must be portable and relative, not absolute: {sidecar_target}',
            )
        elif _resolved_sidecar_target(sidecar_target, sidecar_path=sidecar_path).resolve() != archive_path.resolve():
            _add_check(
                checks,
                'source_archive_sidecar_path',
                'failed',
                f'sidecar target {sidecar_target} does not match archive {_relative_or_absolute(archive_path)}',
            )
        else:
            _add_check(checks, 'source_archive_sidecar_path', 'passed', sidecar_target)
    return archive_path, expected_sha


def _check_section_sha(packet: dict[str, Any], checks: list[dict[str, str]], section_key: str, expected_sha: str) -> None:
    section = _section(packet, section_key)
    if not section:
        _add_check(checks, f'{section_key}_source_archive_sha256', 'failed', f'{section_key} section is missing')
        return
    observed = _text(section.get('source_archive_sha256')).lower()
    if not observed:
        _add_check(checks, f'{section_key}_source_archive_sha256', 'failed', f'{section_key}.source_archive_sha256 is missing')
    elif observed != expected_sha:
        _add_check(
            checks,
            f'{section_key}_source_archive_sha256',
            'failed',
            f'{section_key}.source_archive_sha256 {observed} does not match source archive {expected_sha}',
        )
    else:
        _add_check(checks, f'{section_key}_source_archive_sha256', 'passed', observed)


def _artifact_path(packet: dict[str, Any], section_key: str, default: pathlib.Path | None = None) -> pathlib.Path | None:
    section = _section(packet, section_key)
    path_text = _text(section.get('path'))
    if path_text:
        return _resolve_repo_path(pathlib.Path(path_text))
    return default


def _check_file_mentions_sha(
    checks: list[dict[str, str]],
    *,
    key: str,
    path: pathlib.Path | None,
    expected_sha: str,
    required: bool = True,
) -> None:
    if path is None:
        if required:
            _add_check(checks, f'{key}_mentions_source_archive_sha256', 'failed', f'{key} path is missing')
        return
    if not path.exists():
        if required:
            _add_check(checks, f'{key}_mentions_source_archive_sha256', 'failed', f'{_relative_or_absolute(path)} does not exist')
        return
    try:
        text = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        _add_check(checks, f'{key}_mentions_source_archive_sha256', 'failed', f'{_relative_or_absolute(path)} is not UTF-8 text')
        return
    if expected_sha not in text:
        _add_check(checks, f'{key}_mentions_source_archive_sha256', 'failed', f'{_relative_or_absolute(path)} does not mention current source archive sha256 {expected_sha}')
    else:
        _add_check(checks, f'{key}_mentions_source_archive_sha256', 'passed', _relative_or_absolute(path))


def build_report(
    *,
    packet_json: pathlib.Path,
    generated_at: str,
    release_packet_markdown: pathlib.Path | None = None,
) -> dict[str, Any]:
    packet_path = _resolve_repo_path(packet_json)
    packet, load_error = _load_json_object(packet_path)
    checks: list[dict[str, str]] = []
    if load_error:
        _add_check(checks, 'release_evidence_packet_json', 'failed', load_error)
        return {
            'generated_at': generated_at,
            'status': 'failed',
            'packet_json': str(packet_path),
            'checks': checks,
            'errors': [load_error],
        }
    _add_check(checks, 'release_evidence_packet_json', 'passed', _relative_or_absolute(packet_path))
    archive_path, expected_sha = _check_source_archive(packet, checks)
    if expected_sha:
        _check_section_sha(packet, checks, 'operator_signoff', expected_sha)
        _check_section_sha(packet, checks, 'operator_signoff_from_inputs', expected_sha)
        _check_file_mentions_sha(
            checks,
            key='release_evidence_packet_markdown',
            path=_resolve_repo_path(release_packet_markdown) if release_packet_markdown else packet_path.with_suffix('.md'),
            expected_sha=expected_sha,
        )
        for section_key in (
            'operator_signoff',
            'operator_signoff_from_inputs',
            'operator_signoff_action_plan',
            'external_proof_inputs',
            'external_proof_execution_plan',
            'recommendation_matrix',
        ):
            _check_file_mentions_sha(
                checks,
                key=section_key,
                path=_artifact_path(packet, section_key),
                expected_sha=expected_sha,
                required=section_key in {'operator_signoff', 'operator_signoff_from_inputs'},
            )
    errors = [check['detail'] for check in checks if check['status'] == 'failed']
    return {
        'generated_at': generated_at,
        'status': 'failed' if errors else 'passed',
        'packet_json': str(packet_path),
        'source_archive_path': str(archive_path) if archive_path is not None else '',
        'source_archive_sha256': expected_sha,
        'checks': checks,
        'errors': errors,
    }


def render_markdown(report: dict[str, Any]) -> str:
    rows = ['| Check | Status | Detail |', '| --- | --- | --- |']
    for check in report.get('checks') or []:
        rows.append(f"| `{check.get('key')}` | {check.get('status')} | {check.get('detail') or ''} |")
    errors = report.get('errors') or []
    error_rows = ['| Error |', '| --- |']
    if errors:
        error_rows.extend(f'| {error} |' for error in errors)
    else:
        error_rows.append('| None |')
    return '\n'.join(
        [
            '# Release Artifact Consistency',
            '',
            f"- Generated: {report.get('generated_at')}",
            f"- Status: {report.get('status')}",
            f"- Packet JSON: `{_relative_or_absolute(report.get('packet_json') or '')}`",
            f"- Source archive: `{_relative_or_absolute(report.get('source_archive_path') or '') or 'missing'}`",
            f"- Source archive SHA256: `{report.get('source_archive_sha256') or 'missing'}`",
            '',
            '## Checks',
            '',
            *rows,
            '',
            '## Errors',
            '',
            *error_rows,
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
    parser = argparse.ArgumentParser(description='Check generated RC release artifacts for stale or inconsistent source archive evidence.')
    parser.add_argument('--packet-json', type=pathlib.Path, default=DEFAULT_PACKET_JSON)
    parser.add_argument('--release-packet-markdown', type=pathlib.Path, default=None)
    parser.add_argument('--output', type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--json-output', type=pathlib.Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument('--generated-at', default='', help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(
        packet_json=args.packet_json,
        release_packet_markdown=args.release_packet_markdown,
        generated_at=args.generated_at or _iso_now(),
    )
    write_report(report, output=args.output, json_output=args.json_output)
    print(f"[release-artifact-consistency] Wrote {_relative_or_absolute(_resolve_repo_path(args.output))}.")
    if args.json_output is not None:
        print(f"[release-artifact-consistency] Wrote {_relative_or_absolute(_resolve_repo_path(args.json_output))}.")
    return 0 if report.get('status') == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
