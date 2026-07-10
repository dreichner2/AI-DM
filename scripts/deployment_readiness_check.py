from __future__ import annotations

import argparse
from datetime import UTC, datetime
from dataclasses import dataclass, field
from importlib.util import find_spec
import json
import os
from pathlib import Path
import sys
from typing import Mapping
from urllib.parse import urljoin, urlsplit

import requests
import socketio
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from aidm_server.codex_runtime import codex_executable_configured  # noqa: E402


SUPPORTED_SOCKETIO_WORKER_MODELS = {'single', 'sticky', 'message_queue'}
SUPPORTED_HOSTED_DATABASE_DRIVER = 'postgresql+psycopg'
SUPPORTED_READINESS_LLM_PROVIDERS = {'deepseek', 'codex_cli', 'gemini', 'nvidia', 'kimi', 'fallback'}
PROVIDER_CREDENTIAL_KEYS = {
    'deepseek': ('AIDM_DEEPSEEK_API_KEY', 'DEEPSEEK_API_KEY'),
    'gemini': ('GOOGLE_GENAI_API_KEY',),
    'nvidia': ('AIDM_NVIDIA_API_KEY', 'NVIDIA_API_KEY'),
    'kimi': ('AIDM_NVIDIA_API_KEY', 'NVIDIA_API_KEY'),
}
PLACEHOLDER_MARKERS = (
    '<',
    '>',
    'changeme',
    'example.com',
    'placeholder',
    'replace-',
    'replace_',
    'replace with',
    'replace-with',
)
REQUIRED_SECURITY_HEADERS = {
    'Content-Security-Policy',
    'X-Content-Type-Options',
    'X-Frame-Options',
    'Referrer-Policy',
    'Permissions-Policy',
}
@dataclass(frozen=True)
class ReportSection:
    label: str
    report: 'ReadinessReport'


@dataclass
class ReadinessReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export ') :].strip()
        if '=' not in line:
            raise ValueError(f'{path}:{line_number}: expected KEY=value.')
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f'{path}:{line_number}: empty environment key.')
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def merged_env(env_file: Path | None, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ)
    if env_file:
        env.update(parse_env_file(env_file))
    return env


def _normalized_bool(value: str | None) -> str:
    return str(value or '').strip().lower()


def _is_true(value: str | None) -> bool:
    return _normalized_bool(value) in {'1', 'true', 'yes', 'on'}


def _is_false(value: str | None) -> bool:
    return _normalized_bool(value) in {'0', 'false', 'no', 'off'}


def _split_list(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or '').split(',') if item.strip()]


def _looks_placeholder(value: str | None) -> bool:
    normalized = str(value or '').strip().lower()
    if not normalized:
        return True
    return any(marker in normalized for marker in PLACEHOLDER_MARKERS)


def _required_value(report: ReadinessReport, env: Mapping[str, str], key: str) -> str:
    value = str(env.get(key) or '').strip()
    if not value:
        report.error(f'{key} is required.')
    elif _looks_placeholder(value):
        report.error(f'{key} still looks like a placeholder.')
    return value


def _validate_bool(
    report: ReadinessReport,
    env: Mapping[str, str],
    key: str,
    *,
    expected: bool,
    message: str,
) -> None:
    value = env.get(key)
    if expected and not _is_true(value):
        report.error(message)
    elif not expected and not _is_false(value):
        report.error(message)


def _validate_database_configuration(report: ReadinessReport, env: Mapping[str, str]) -> None:
    database_uri = _required_value(report, env, 'AIDM_DATABASE_URI')
    if not database_uri or _looks_placeholder(database_uri):
        return

    try:
        database_url = make_url(database_uri)
    except Exception:
        report.error('AIDM_DATABASE_URI is not a valid SQLAlchemy database URL.')
        return

    if database_url.drivername != SUPPORTED_HOSTED_DATABASE_DRIVER:
        report.error(
            'AIDM_DATABASE_URI must use postgresql+psycopg for hosted closed-beta readiness.'
        )
    if not database_url.host:
        report.error('AIDM_DATABASE_URI must include a database host.')
    if not database_url.database:
        report.error('AIDM_DATABASE_URI must include a database name.')
    if find_spec('psycopg') is None:
        report.error('The psycopg runtime driver is not installed.')


def _parse_token_workspace_entries(report: ReadinessReport, value: str | None) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    malformed = False
    for item in str(value or '').split(','):
        raw_item = item.strip()
        if not raw_item:
            continue
        if '=' not in raw_item:
            malformed = True
            continue
        workspace_id, token = (part.strip() for part in raw_item.split('=', 1))
        if not workspace_id or not token:
            malformed = True
            continue
        entries.append((workspace_id, token))
    if malformed:
        report.error('AIDM_API_AUTH_TOKEN_WORKSPACES entries must use non-empty workspace=token values.')
    return entries


def _selected_provider_credential(env: Mapping[str, str], keys: tuple[str, ...]) -> tuple[str, str]:
    for key in keys:
        value = str(env.get(key) or '').strip()
        if value:
            return key, value
    return keys[0], ''


def _validate_selected_provider(
    report: ReadinessReport,
    env: Mapping[str, str],
    *,
    allow_fallback_provider: bool,
) -> None:
    provider = _required_value(report, env, 'AIDM_LLM_PROVIDER').lower()
    if not provider or _looks_placeholder(provider):
        return
    if provider not in SUPPORTED_READINESS_LLM_PROVIDERS:
        expected = ', '.join(sorted(SUPPORTED_READINESS_LLM_PROVIDERS))
        report.error(f'AIDM_LLM_PROVIDER must be one of: {expected}.')
        return
    if provider == 'fallback':
        if not allow_fallback_provider:
            report.error('AIDM_LLM_PROVIDER=fallback is safe-mode only; pass --allow-fallback-provider for intentional drills.')
        return
    if provider == 'codex_cli':
        executable = str(env.get('AIDM_CODEX_EXECUTABLE') or 'codex').strip()
        if _looks_placeholder(executable) or not codex_executable_configured(executable):
            report.error('AIDM_LLM_PROVIDER=codex_cli requires an available AIDM_CODEX_EXECUTABLE or codex on PATH.')
        return

    credential_key, credential = _selected_provider_credential(env, PROVIDER_CREDENTIAL_KEYS[provider])
    if not credential:
        expected = ' or '.join(PROVIDER_CREDENTIAL_KEYS[provider])
        report.error(f'AIDM_LLM_PROVIDER={provider} requires {expected}.')
    elif _looks_placeholder(credential):
        report.error(f'{credential_key} still looks like a placeholder.')


def validate_environment(
    env: Mapping[str, str],
    *,
    same_origin_deployment: bool = False,
    auth_storage_exception: str = '',
    socketio_staging_proof: str = '',
    allow_fallback_provider: bool = False,
) -> ReadinessReport:
    report = ReadinessReport()

    if str(env.get('AIDM_ENV') or '').strip().lower() != 'production':
        report.error('AIDM_ENV must be production for hosted closed-beta readiness.')
    _validate_bool(
        report,
        env,
        'AIDM_DEBUG',
        expected=False,
        message='AIDM_DEBUG must be false for hosted closed-beta readiness.',
    )
    _required_value(report, env, 'FLASK_SECRET_KEY')
    if len(str(env.get('FLASK_SECRET_KEY') or '')) < 32:
        report.error('FLASK_SECRET_KEY must be at least 32 characters.')
    _validate_database_configuration(report, env)

    _validate_bool(
        report,
        env,
        'AIDM_AUTH_REQUIRED',
        expected=True,
        message='AIDM_AUTH_REQUIRED must be true for hosted closed-beta readiness.',
    )
    tokens = _split_list(env.get('AIDM_API_AUTH_TOKENS'))
    token_workspaces = _parse_token_workspace_entries(report, env.get('AIDM_API_AUTH_TOKEN_WORKSPACES'))
    if not tokens and not token_workspaces:
        report.error('AIDM_API_AUTH_TOKENS or AIDM_API_AUTH_TOKEN_WORKSPACES must be configured.')
    for key in ('AIDM_API_AUTH_TOKENS', 'AIDM_API_AUTH_TOKEN_WORKSPACES'):
        value = str(env.get(key) or '').strip()
        if value and _looks_placeholder(value):
            report.error(f'{key} still looks like a placeholder.')

    _validate_bool(
        report,
        env,
        'AIDM_AUTO_CREATE_SCHEMA',
        expected=False,
        message='AIDM_AUTO_CREATE_SCHEMA must be false; migrations must own schema changes.',
    )
    if str(env.get('AIDM_RATE_LIMIT_STORE') or '').strip().lower() != 'database':
        report.error('AIDM_RATE_LIMIT_STORE must be database for hosted/multi-worker readiness.')
    if str(env.get('AIDM_TURN_COORDINATOR_STORE') or '').strip().lower() != 'database':
        report.error('AIDM_TURN_COORDINATOR_STORE must be database for hosted/multi-worker readiness.')
    if str(env.get('AIDM_SOCKETIO_ASYNC_MODE') or '').strip().lower() != 'threading':
        report.error('AIDM_SOCKETIO_ASYNC_MODE must be threading for hosted closed-beta readiness.')
    gunicorn_threads_raw = str(env.get('AIDM_GUNICORN_THREADS') or '100').strip()
    gunicorn_threads = (
        int(gunicorn_threads_raw)
        if gunicorn_threads_raw.isascii()
        and gunicorn_threads_raw.isdigit()
        and not gunicorn_threads_raw.startswith('0')
        else 0
    )
    if gunicorn_threads < 16:
        report.error('AIDM_GUNICORN_THREADS must be an integer >= 16.')

    rest_cors = _split_list(env.get('AIDM_CORS_ALLOWLIST'))
    socket_cors = _split_list(env.get('AIDM_SOCKET_CORS_ALLOWLIST'))
    if '*' in rest_cors or '*' in socket_cors:
        report.error('Wildcard CORS allowlists are not allowed for hosted closed-beta readiness.')
    if not same_origin_deployment and (not rest_cors or not socket_cors):
        report.error(
            'AIDM_CORS_ALLOWLIST and AIDM_SOCKET_CORS_ALLOWLIST must be explicit, '
            'or pass --same-origin-deployment for an intentionally same-origin target.'
        )

    worker_model = str(env.get('AIDM_SOCKETIO_WORKER_MODEL') or '').strip().lower().replace('-', '_')
    web_concurrency_raw = str(env.get('WEB_CONCURRENCY') or '1').strip()
    web_concurrency = (
        int(web_concurrency_raw)
        if web_concurrency_raw.isascii()
        and web_concurrency_raw.isdigit()
        and not web_concurrency_raw.startswith('0')
        else 0
    )
    if web_concurrency < 1:
        report.error('WEB_CONCURRENCY must be a positive integer.')
    if worker_model not in SUPPORTED_SOCKETIO_WORKER_MODELS:
        expected = ', '.join(sorted(SUPPORTED_SOCKETIO_WORKER_MODELS))
        report.error(f'AIDM_SOCKETIO_WORKER_MODEL must be one of: {expected}.')
    elif worker_model in {'sticky', 'message_queue'}:
        report.error(
            'Hosted production currently supports only AIDM_SOCKETIO_WORKER_MODEL=single; '
            'multi-worker presence and music state are not shared.'
        )
    elif worker_model == 'single':
        if web_concurrency != 1:
            report.error('AIDM_SOCKETIO_WORKER_MODEL=single requires WEB_CONCURRENCY=1.')

    _required_value(report, env, 'AIDM_OBSERVABILITY_PROVIDER')
    _required_value(report, env, 'AIDM_ALERT_OWNER')
    if _is_true(env.get('AIDM_TELEMETRY_ENABLED')):
        _required_value(report, env, 'AIDM_TELEMETRY_ENDPOINT')
    else:
        report.warn('AIDM_TELEMETRY_ENABLED is not true; confirm managed metrics scraping covers beta SLOs.')

    _validate_bool(
        report,
        env,
        'AIDM_SECURITY_HEADERS_ENABLED',
        expected=True,
        message='AIDM_SECURITY_HEADERS_ENABLED must be true.',
    )

    cookie_auth_enabled = _is_true(env.get('AIDM_ACCOUNT_COOKIE_AUTH_ENABLED'))
    if cookie_auth_enabled:
        _validate_bool(
            report,
            env,
            'AIDM_ACCOUNT_COOKIE_SECURE',
            expected=True,
            message='AIDM_ACCOUNT_COOKIE_SECURE must be true when cookie auth is enabled.',
        )
        _validate_bool(
            report,
            env,
            'AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED',
            expected=False,
            message='AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED must be false for cookie-only hosted browser auth.',
        )
    elif not auth_storage_exception.strip():
        report.error(
            'Hosted readiness requires AIDM_ACCOUNT_COOKIE_AUTH_ENABLED=true, '
            'or --auth-storage-exception documenting why bearer/session storage is acceptable.'
        )

    _validate_selected_provider(
        report,
        env,
        allow_fallback_provider=allow_fallback_provider,
    )

    return report


def validate_database_connectivity(
    env: Mapping[str, str],
    *,
    timeout_seconds: float = 5.0,
) -> ReadinessReport:
    """Open the configured hosted database and run a minimal round trip."""
    report = ReadinessReport()
    database_uri = str(env.get('AIDM_DATABASE_URI') or '').strip()
    if not database_uri:
        report.error('Database connectivity check could not run because AIDM_DATABASE_URI is missing.')
        return report

    engine = None
    try:
        database_url = make_url(database_uri)
        if database_url.drivername != SUPPORTED_HOSTED_DATABASE_DRIVER:
            report.error('Database connectivity check requires postgresql+psycopg.')
            return report
        engine = create_engine(
            database_uri,
            pool_pre_ping=True,
            connect_args={'connect_timeout': max(1, int(timeout_seconds))},
        )
        with engine.connect() as connection:
            result = connection.exec_driver_sql('SELECT 1').scalar_one()
            if result != 1:
                report.error('Database connectivity check returned an unexpected result.')
    except Exception as exc:
        report.error(f'Database connectivity check failed ({type(exc).__name__}).')
    finally:
        if engine is not None:
            engine.dispose()
    return report


def _request_json(url: str, headers: Mapping[str, str], timeout_seconds: float) -> tuple[dict, requests.Response]:
    response = requests.get(url, headers=dict(headers), timeout=timeout_seconds)
    response.raise_for_status()
    return response.json(), response


def _request_text(url: str, headers: Mapping[str, str], timeout_seconds: float) -> tuple[str, requests.Response]:
    response = requests.get(url, headers=dict(headers), timeout=timeout_seconds)
    response.raise_for_status()
    return response.text, response


def validate_websocket_transport(
    target_url: str,
    *,
    auth_token: str = '',
    origin: str = '',
    timeout_seconds: float = 10.0,
) -> ReadinessReport:
    """Prove that the deployed edge supports an authenticated WebSocket upgrade."""

    report = ReadinessReport()
    parsed_target = urlsplit(target_url)
    selected_origin = origin.strip() or f'{parsed_target.scheme}://{parsed_target.netloc}'
    if not parsed_target.scheme or not parsed_target.netloc or not selected_origin:
        report.error('Socket.IO WebSocket probe requires an absolute target URL and origin.')
        return report

    client = socketio.Client(
        reconnection=False,
        request_timeout=timeout_seconds,
        websocket_extra_options={'origin': selected_origin},
    )
    connected = False
    try:
        client.connect(
            target_url.rstrip('/'),
            headers={'Authorization': f'Bearer {auth_token}'} if auth_token else {},
            auth={'workspace_token': auth_token} if auth_token else None,
            transports=['websocket'],
            wait_timeout=timeout_seconds,
        )
        connected = True
        if client.transport() != 'websocket':
            report.error(f'Socket.IO transport is {client.transport()!r}; expected websocket.')
    except Exception as exc:  # pragma: no cover - exact client exception type is not useful to assert.
        report.error(f'Socket.IO WebSocket probe failed ({type(exc).__name__}).')
    finally:
        if connected:
            client.disconnect()
    return report


def validate_live_target(
    target_url: str,
    *,
    auth_token: str = '',
    socketio_origin: str = '',
    timeout_seconds: float = 10.0,
    allow_fallback_provider: bool = False,
    allow_non_production_target: bool = False,
) -> ReadinessReport:
    report = ReadinessReport()
    base_url = target_url.rstrip('/') + '/'
    headers = {'Authorization': f'Bearer {auth_token}'} if auth_token else {}

    try:
        health, health_response = _request_json(urljoin(base_url, 'api/health'), headers, timeout_seconds)
    except Exception as exc:  # pragma: no cover - exact requests exception type is not useful to assert.
        report.error(f'GET /api/health failed: {exc}')
        return report

    if health.get('status') != 'ok':
        report.error('GET /api/health did not return status=ok.')
    if health.get('auth_required') is not True:
        report.error('GET /api/health reports auth_required is not true.')
    if health.get('env') != 'production' and not allow_non_production_target:
        report.error('GET /api/health does not report env=production.')
    llm_payload = health.get('llm') if isinstance(health.get('llm'), dict) else {}
    if str(llm_payload.get('provider') or '').lower() == 'fallback' and not allow_fallback_provider:
        report.error('GET /api/health reports the deterministic fallback provider.')
    if llm_payload.get('configured') is not True:
        report.error('GET /api/health reports the selected LLM provider is not configured.')

    missing_headers = [header for header in sorted(REQUIRED_SECURITY_HEADERS) if not health_response.headers.get(header)]
    if missing_headers:
        report.error(f'GET /api/health is missing security headers: {", ".join(missing_headers)}.')

    try:
        metrics, _metrics_response = _request_json(urljoin(base_url, 'api/metrics'), headers, timeout_seconds)
    except Exception as exc:  # pragma: no cover - exact requests exception type is not useful to assert.
        report.error(f'GET /api/metrics failed: {exc}')
    else:
        if not isinstance(metrics.get('counters'), dict) or not isinstance(metrics.get('timings'), dict):
            report.error('GET /api/metrics payload is missing counters/timings objects.')
        if not isinstance(metrics.get('beta'), dict):
            report.error('GET /api/metrics payload is missing beta summary gauges.')

    try:
        prometheus_text, prometheus_response = _request_text(
            urljoin(base_url, 'api/metrics/prometheus'),
            headers,
            timeout_seconds,
        )
    except Exception as exc:  # pragma: no cover - exact requests exception type is not useful to assert.
        report.error(f'GET /api/metrics/prometheus failed: {exc}')
    else:
        content_type = prometheus_response.headers.get('Content-Type', '')
        if not content_type.startswith('text/plain'):
            report.error('GET /api/metrics/prometheus did not return text/plain content.')
        if 'aidm_telemetry_enabled' not in prometheus_text:
            report.error('GET /api/metrics/prometheus is missing aidm_telemetry_enabled.')
        if 'aidm_beta_' not in prometheus_text:
            report.error('GET /api/metrics/prometheus is missing beta gauges.')

    websocket_report = validate_websocket_transport(
        target_url,
        auth_token=auth_token,
        origin=socketio_origin,
        timeout_seconds=timeout_seconds,
    )
    report.errors.extend(websocket_report.errors)
    report.warnings.extend(websocket_report.warnings)

    return report


def _merge_reports(*reports: ReadinessReport) -> ReadinessReport:
    merged = ReadinessReport()
    for report in reports:
        merged.errors.extend(report.errors)
        merged.warnings.extend(report.warnings)
    return merged


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _report_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _relative_or_absolute(path: Path | None) -> str:
    if path is None:
        return ''
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _section_payload(section: ReportSection) -> dict[str, object]:
    return {
        'label': section.label,
        'status': 'passed' if section.report.ok else 'failed',
        'errors': section.report.errors,
        'warnings': section.report.warnings,
    }


def _options_payload(args: argparse.Namespace) -> dict[str, object]:
    return {
        'env_file': _relative_or_absolute(args.env_file),
        'target_url': args.target_url or '',
        'auth_token_provided': bool(args.auth_token),
        'socketio_origin': args.socketio_origin or '',
        'timeout_seconds': args.timeout_seconds,
        'database_timeout_seconds': args.database_timeout_seconds,
        'same_origin_deployment': bool(args.same_origin_deployment),
        'auth_storage_exception_provided': bool(args.auth_storage_exception.strip()),
        'socketio_staging_proof_provided': bool(args.socketio_staging_proof.strip()),
        'allow_fallback_provider': bool(args.allow_fallback_provider),
        'allow_non_production_target': bool(args.allow_non_production_target),
    }


def write_evidence_report(
    path: Path,
    *,
    sections: list[ReportSection],
    started_at: str,
    finished_at: str,
    args: argparse.Namespace,
) -> None:
    output_path = _report_path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    status = 'passed' if all(section.report.ok for section in sections) else 'failed'
    payload = {
        'status': status,
        'started_at': started_at,
        'finished_at': finished_at,
        'options': _options_payload(args),
        'sections': [_section_payload(section) for section in sections],
    }
    if output_path.suffix.lower() == '.json':
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        return

    rows = [
        '| Check | Status | Errors | Warnings |',
        '| --- | --- | ---: | ---: |',
    ]
    for section in sections:
        rows.append(
            f'| {section.label} | {"passed" if section.report.ok else "failed"} '
            f'| {len(section.report.errors)} | {len(section.report.warnings)} |'
        )

    details: list[str] = []
    for section in sections:
        details.extend(['', f'## {section.label}', '', f'- Status: {"passed" if section.report.ok else "failed"}'])
        if section.report.warnings:
            details.append('- Warnings:')
            details.extend(f'  - {warning}' for warning in section.report.warnings)
        else:
            details.append('- Warnings: none')
        if section.report.errors:
            details.append('- Errors:')
            details.extend(f'  - {error}' for error in section.report.errors)
        else:
            details.append('- Errors: none')

    options = payload['options']
    output_path.write_text(
        '\n'.join(
            [
                '# Deployment Readiness Evidence',
                '',
                f'- Status: {status}',
                f'- Started: {started_at}',
                f'- Finished: {finished_at}',
                f"- Env file: `{options['env_file'] or 'process environment'}`",
                f"- Target URL: `{options['target_url'] or 'not checked'}`",
                f"- Auth token provided: {options['auth_token_provided']}",
                f"- Database timeout seconds: {options['database_timeout_seconds']}",
                f"- Same-origin deployment: {options['same_origin_deployment']}",
                f"- Auth storage exception provided: {options['auth_storage_exception_provided']}",
                f"- Socket.IO staging proof provided: {options['socketio_staging_proof_provided']}",
                f"- Allow fallback provider: {options['allow_fallback_provider']}",
                f"- Allow non-production target: {options['allow_non_production_target']}",
                '',
                '## Checks',
                '',
                *rows,
                *details,
                '',
            ]
        ),
        encoding='utf-8',
    )


def _print_report(report: ReadinessReport) -> None:
    for warning in report.warnings:
        print(f'[deployment-readiness][warning] {warning}')
    for error in report.errors:
        print(f'[deployment-readiness][error] {error}')
    if report.ok:
        print('[deployment-readiness] All checks passed.')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Validate hosted closed-beta deployment readiness.')
    parser.add_argument('--env-file', type=Path, help='Optional production env file to validate.')
    parser.add_argument('--target-url', help='Optional deployed target base URL for live endpoint checks.')
    parser.add_argument('--auth-token', help='Bearer token for live target checks when auth is required.')
    parser.add_argument(
        '--socketio-origin',
        default='',
        help='Origin header for the forced WebSocket probe; defaults to the target URL origin.',
    )
    parser.add_argument('--timeout-seconds', type=float, default=10.0, help='HTTP timeout for live target checks.')
    parser.add_argument(
        '--database-timeout-seconds',
        type=float,
        default=5.0,
        help='Connection timeout for the required hosted database round trip.',
    )
    parser.add_argument(
        '--same-origin-deployment',
        action='store_true',
        help='Allow empty CORS allowlists because the deployment is intentionally same-origin.',
    )
    parser.add_argument(
        '--auth-storage-exception',
        default='',
        help='Document why hosted browser auth is not using HTTP-only cookies.',
    )
    parser.add_argument(
        '--socketio-staging-proof',
        default='',
        help=(
            'Reserved future-topology evidence. It does not make sticky or message_queue mode '
            'eligible for hosted RC1; production currently requires single-worker mode.'
        ),
    )
    parser.add_argument(
        '--allow-fallback-provider',
        action='store_true',
        help='Allow the deterministic fallback provider for explicit safe-mode drills.',
    )
    parser.add_argument(
        '--allow-non-production-target',
        action='store_true',
        help='Allow live endpoint checks against a staging target that does not report env=production.',
    )
    parser.add_argument(
        '--evidence-report',
        nargs='?',
        const=Path('tmp/release/deployment-readiness-evidence.md'),
        default=None,
        type=Path,
        help='Write a Markdown or JSON deployment-readiness evidence report.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started_at = _iso_now()
    try:
        env = merged_env(args.env_file)
    except Exception as exc:
        print(f'[deployment-readiness][error] {exc}')
        if args.evidence_report is not None:
            report = ReadinessReport(errors=[str(exc)])
            write_evidence_report(
                args.evidence_report,
                sections=[ReportSection('Environment file load', report)],
                started_at=started_at,
                finished_at=_iso_now(),
                args=args,
            )
            print(f'[deployment-readiness] Evidence report written to {_report_path(args.evidence_report)}.')
        return 1

    environment_report = validate_environment(
        env,
        same_origin_deployment=args.same_origin_deployment,
        auth_storage_exception=args.auth_storage_exception,
        socketio_staging_proof=args.socketio_staging_proof,
        allow_fallback_provider=args.allow_fallback_provider,
    )
    if environment_report.ok:
        database_report = validate_database_connectivity(
            env,
            timeout_seconds=args.database_timeout_seconds,
        )
    else:
        database_report = ReadinessReport(
            warnings=['Database connectivity was skipped because environment configuration is invalid.']
        )

    sections = [
        ReportSection('Environment configuration', environment_report),
        ReportSection('Database connectivity', database_report),
    ]
    if args.target_url:
        sections.append(
            ReportSection(
                'Live target checks',
                validate_live_target(
                    args.target_url,
                    auth_token=args.auth_token or '',
                    socketio_origin=args.socketio_origin,
                    timeout_seconds=args.timeout_seconds,
                    allow_fallback_provider=args.allow_fallback_provider,
                    allow_non_production_target=args.allow_non_production_target,
                ),
            )
        )

    report = _merge_reports(*(section.report for section in sections))
    _print_report(report)
    if args.evidence_report is not None:
        write_evidence_report(
            args.evidence_report,
            sections=sections,
            started_at=started_at,
            finished_at=_iso_now(),
            args=args,
        )
        print(f'[deployment-readiness] Evidence report written to {_report_path(args.evidence_report)}.')
    return 0 if report.ok else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
