from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.deployment_readiness_check import (
    REQUIRED_SECURITY_HEADERS,
    ReadinessReport,
    main,
    merged_env,
    parse_env_file,
    validate_database_connectivity,
    validate_environment,
    validate_live_target,
    validate_websocket_transport,
)


def _ready_env(**overrides: str) -> dict[str, str]:
    env = {
        'AIDM_ENV': 'production',
        'AIDM_DEBUG': 'false',
        'FLASK_SECRET_KEY': 'a' * 40,
        'AIDM_DATABASE_URI': 'postgresql+psycopg://aidm:secret@db.example.test:5432/aidm',
        'AIDM_AUTH_REQUIRED': 'true',
        'AIDM_API_AUTH_TOKENS': 'closed-beta-token',
        'AIDM_AUTO_CREATE_SCHEMA': 'false',
        'AIDM_RATE_LIMIT_STORE': 'database',
        'AIDM_TURN_COORDINATOR_STORE': 'database',
        'AIDM_CORS_ALLOWLIST': 'https://aidm.example.test',
        'AIDM_SOCKET_CORS_ALLOWLIST': 'https://aidm.example.test',
        'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
        'AIDM_SOCKETIO_WORKER_MODEL': 'single',
        'AIDM_GUNICORN_THREADS': '100',
        'WEB_CONCURRENCY': '1',
        'AIDM_OBSERVABILITY_PROVIDER': 'managed-prometheus',
        'AIDM_ALERT_OWNER': 'beta-oncall',
        'AIDM_TELEMETRY_ENABLED': 'true',
        'AIDM_TELEMETRY_ENDPOINT': 'https://telemetry.example.test/ingest',
        'AIDM_SECURITY_HEADERS_ENABLED': 'true',
        'AIDM_ACCOUNT_COOKIE_AUTH_ENABLED': 'true',
        'AIDM_ACCOUNT_COOKIE_SECURE': 'true',
        'AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED': 'false',
        'AIDM_LLM_PROVIDER': 'gemini',
        'GOOGLE_GENAI_API_KEY': 'test-provider-key',
    }
    env.update(overrides)
    return env


def _write_ready_env_file(path: Path, **overrides: str) -> None:
    path.write_text(
        '\n'.join(f'{key}={value}' for key, value in _ready_env(**overrides).items()) + '\n',
        encoding='utf-8',
    )


def test_parse_env_file_supports_comments_export_and_quotes(tmp_path: Path):
    env_file = tmp_path / '.env.production'
    env_file.write_text(
        """
        # hosted beta
        export AIDM_ENV=production
        FLASK_SECRET_KEY="quoted secret"
        AIDM_ALERT_OWNER='beta-oncall'
        """,
        encoding='utf-8',
    )

    assert parse_env_file(env_file) == {
        'AIDM_ENV': 'production',
        'FLASK_SECRET_KEY': 'quoted secret',
        'AIDM_ALERT_OWNER': 'beta-oncall',
    }


def test_merged_env_file_overrides_base_env(tmp_path: Path):
    env_file = tmp_path / '.env.production'
    env_file.write_text('AIDM_ENV=production\n', encoding='utf-8')

    assert merged_env(env_file, base_env={'AIDM_ENV': 'development'})['AIDM_ENV'] == 'production'


def test_validate_environment_accepts_hosted_closed_beta_config():
    report = validate_environment(_ready_env())

    assert report.ok
    assert report.warnings == []


def test_validate_environment_rejects_placeholders_and_wildcard_cors():
    report = validate_environment(
        _ready_env(
            FLASK_SECRET_KEY='replace-with-secret',
            AIDM_CORS_ALLOWLIST='*',
            AIDM_SOCKET_CORS_ALLOWLIST='*',
            AIDM_OBSERVABILITY_PROVIDER='replace-with-provider-name',
        )
    )

    assert not report.ok
    assert any('FLASK_SECRET_KEY still looks like a placeholder' in error for error in report.errors)
    assert any('Wildcard CORS' in error for error in report.errors)
    assert any('AIDM_OBSERVABILITY_PROVIDER still looks like a placeholder' in error for error in report.errors)


def test_validate_environment_requires_supported_hosted_database():
    missing_database = _ready_env()
    missing_database.pop('AIDM_DATABASE_URI')

    missing_report = validate_environment(missing_database)
    sqlite_report = validate_environment(_ready_env(AIDM_DATABASE_URI='sqlite:////tmp/aidm.db'))
    legacy_driver_report = validate_environment(
        _ready_env(AIDM_DATABASE_URI='postgresql://aidm:secret@db.example.test:5432/aidm')
    )

    assert any('AIDM_DATABASE_URI is required' in error for error in missing_report.errors)
    assert any('must use postgresql+psycopg' in error for error in sqlite_report.errors)
    assert any('must use postgresql+psycopg' in error for error in legacy_driver_report.errors)


def test_validate_environment_rejects_malformed_workspace_tokens_debug_and_async_mode():
    report = validate_environment(
        _ready_env(
            AIDM_API_AUTH_TOKENS='',
            AIDM_API_AUTH_TOKEN_WORKSPACES='malformed-entry',
            AIDM_DEBUG='true',
            AIDM_SOCKETIO_ASYNC_MODE='eventlet',
            AIDM_GUNICORN_THREADS='1',
            WEB_CONCURRENCY='2',
        )
    )

    assert not report.ok
    assert any('workspace=token' in error for error in report.errors)
    assert any('AIDM_DEBUG must be false' in error for error in report.errors)
    assert any('AIDM_SOCKETIO_ASYNC_MODE must be threading' in error for error in report.errors)
    assert any('AIDM_GUNICORN_THREADS must be an integer >= 16' in error for error in report.errors)
    assert any('AIDM_SOCKETIO_WORKER_MODEL=single requires WEB_CONCURRENCY=1' in error for error in report.errors)


@pytest.mark.parametrize(
    ('provider', 'overrides', 'credential_name'),
    [
        ('gemini', {'GOOGLE_GENAI_API_KEY': ''}, 'GOOGLE_GENAI_API_KEY'),
        (
            'deepseek',
            {'AIDM_DEEPSEEK_API_KEY': '', 'DEEPSEEK_API_KEY': ''},
            'AIDM_DEEPSEEK_API_KEY',
        ),
        (
            'nvidia',
            {'AIDM_NVIDIA_API_KEY': '', 'NVIDIA_API_KEY': ''},
            'AIDM_NVIDIA_API_KEY',
        ),
        (
            'kimi',
            {'AIDM_NVIDIA_API_KEY': '', 'NVIDIA_API_KEY': ''},
            'AIDM_NVIDIA_API_KEY',
        ),
    ],
)
def test_validate_environment_requires_selected_provider_credentials(provider, overrides, credential_name):
    report = validate_environment(_ready_env(AIDM_LLM_PROVIDER=provider, **overrides))

    assert not report.ok
    assert any(credential_name in error for error in report.errors)


def test_validate_environment_requires_codex_runtime_and_dedicated_auth(tmp_path):
    codex_executable = tmp_path / 'codex'
    codex_executable.write_text('#!/bin/sh\n', encoding='utf-8')
    codex_executable.chmod(0o755)
    codex_home = tmp_path / 'aidm-codex'
    codex_home.mkdir()
    (codex_home / 'auth.json').write_text('{"auth":"test"}', encoding='utf-8')

    ready_report = validate_environment(
        _ready_env(
            AIDM_LLM_PROVIDER='codex_cli',
            AIDM_CODEX_EXECUTABLE=str(codex_executable),
            AIDM_CODEX_HOME=str(codex_home),
        )
    )
    missing_home_report = validate_environment(
        _ready_env(
            AIDM_LLM_PROVIDER='codex_cli',
            AIDM_CODEX_EXECUTABLE=str(codex_executable),
            AIDM_CODEX_HOME='',
        )
    )
    relative_home_report = validate_environment(
        _ready_env(
            AIDM_LLM_PROVIDER='codex_cli',
            AIDM_CODEX_EXECUTABLE=str(codex_executable),
            AIDM_CODEX_HOME='relative/codex-home',
        )
    )
    access_token_report = validate_environment(
        _ready_env(
            AIDM_LLM_PROVIDER='codex_cli',
            AIDM_CODEX_EXECUTABLE=str(codex_executable),
            AIDM_CODEX_ACCESS_TOKEN='dedicated-test-token',
        )
    )

    assert ready_report.ok
    assert any('dedicated persistent AIDM_CODEX_HOME' in error for error in missing_home_report.errors)
    assert any('must be an absolute path' in error for error in relative_home_report.errors)
    assert access_token_report.ok


def test_validate_environment_requires_cookie_auth_or_documented_exception():
    missing_cookie_report = validate_environment(
        _ready_env(
            AIDM_ACCOUNT_COOKIE_AUTH_ENABLED='false',
            AIDM_ACCOUNT_COOKIE_SECURE='false',
            AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED='true',
        )
    )
    exception_report = validate_environment(
        _ready_env(
            AIDM_ACCOUNT_COOKIE_AUTH_ENABLED='false',
            AIDM_ACCOUNT_COOKIE_SECURE='false',
            AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED='true',
        ),
        auth_storage_exception='Native API clients use bearer tokens only.',
    )

    assert not missing_cookie_report.ok
    assert any('AIDM_ACCOUNT_COOKIE_AUTH_ENABLED=true' in error for error in missing_cookie_report.errors)
    assert exception_report.ok


def test_validate_environment_rejects_deferred_multi_worker_models():
    sticky_report = validate_environment(_ready_env(AIDM_SOCKETIO_WORKER_MODEL='sticky'))
    queue_report = validate_environment(
        _ready_env(
            AIDM_SOCKETIO_WORKER_MODEL='message_queue',
            AIDM_SOCKETIO_MESSAGE_QUEUE='redis://redis.example.test:6379/0',
        ),
        socketio_staging_proof='staging browser-smoke run 123',
    )

    assert not sticky_report.ok
    assert not queue_report.ok
    assert any('currently supports only AIDM_SOCKETIO_WORKER_MODEL=single' in error for error in sticky_report.errors)
    assert any('currently supports only AIDM_SOCKETIO_WORKER_MODEL=single' in error for error in queue_report.errors)


class _FakeResponse:
    def __init__(self, *, payload=None, text='', headers=None):
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeDatabaseResult:
    def scalar_one(self):
        return 1


class _FakeDatabaseConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback

    def exec_driver_sql(self, statement):
        assert statement == 'SELECT 1'
        return _FakeDatabaseResult()


class _FakeDatabaseEngine:
    def __init__(self):
        self.disposed = False

    def connect(self):
        return _FakeDatabaseConnection()

    def dispose(self):
        self.disposed = True


def test_validate_database_connectivity_runs_round_trip_and_disposes(monkeypatch):
    engine = _FakeDatabaseEngine()

    def fake_create_engine(uri, **kwargs):
        assert uri == _ready_env()['AIDM_DATABASE_URI']
        assert kwargs == {'pool_pre_ping': True, 'connect_args': {'connect_timeout': 3}}
        return engine

    monkeypatch.setattr('scripts.deployment_readiness_check.create_engine', fake_create_engine)

    report = validate_database_connectivity(_ready_env(), timeout_seconds=3)

    assert report.ok
    assert engine.disposed is True


def test_validate_database_connectivity_redacts_connection_details(monkeypatch):
    class FailingEngine:
        disposed = False

        def connect(self):
            raise RuntimeError('could not connect using aidm:secret@db.example.test')

        def dispose(self):
            self.disposed = True

    engine = FailingEngine()
    monkeypatch.setattr('scripts.deployment_readiness_check.create_engine', lambda *_args, **_kwargs: engine)

    report = validate_database_connectivity(_ready_env())

    assert not report.ok
    assert report.errors == ['Database connectivity check failed (RuntimeError).']
    assert 'secret' not in ' '.join(report.errors)
    assert engine.disposed is True


def _stub_database_connectivity(monkeypatch):
    monkeypatch.setattr(
        'scripts.deployment_readiness_check.validate_database_connectivity',
        lambda env, *, timeout_seconds: ReadinessReport(),
    )


def _stub_websocket_transport(monkeypatch):
    monkeypatch.setattr(
        'scripts.deployment_readiness_check.validate_websocket_transport',
        lambda target_url, *, auth_token, origin, timeout_seconds: ReadinessReport(),
    )


def test_validate_live_target_checks_health_metrics_prometheus_and_headers(monkeypatch):
    _stub_websocket_transport(monkeypatch)
    security_headers = {header: 'set' for header in REQUIRED_SECURITY_HEADERS}

    def fake_get(url, headers, timeout):
        assert headers == {'Authorization': 'Bearer live-token'}
        assert timeout == 4
        if url.endswith('/api/health'):
            return _FakeResponse(
                payload={
                    'status': 'ok',
                    'env': 'production',
                    'auth_required': True,
                    'llm': {'provider': 'gemini', 'configured': True},
                },
                headers=security_headers,
            )
        if url.endswith('/api/metrics'):
            return _FakeResponse(payload={'counters': {}, 'timings': {}, 'beta': {}})
        if url.endswith('/api/metrics/prometheus'):
            return _FakeResponse(
                text='# TYPE aidm_telemetry_enabled gauge\naidm_telemetry_enabled 1\naidm_beta_bad_turn_reports 0\n',
                headers={'Content-Type': 'text/plain; version=0.0.4'},
            )
        raise AssertionError(url)

    monkeypatch.setattr('scripts.deployment_readiness_check.requests.get', fake_get)

    report = validate_live_target('https://aidm.example.test', auth_token='live-token', timeout_seconds=4)

    assert report.ok


def test_validate_live_target_rejects_unconfigured_selected_provider(monkeypatch):
    _stub_websocket_transport(monkeypatch)
    security_headers = {header: 'set' for header in REQUIRED_SECURITY_HEADERS}

    def fake_get(url, headers, timeout):
        del headers, timeout
        if url.endswith('/api/health'):
            return _FakeResponse(
                payload={
                    'status': 'ok',
                    'env': 'production',
                    'auth_required': True,
                    'llm': {'provider': 'gemini', 'configured': False},
                },
                headers=security_headers,
            )
        if url.endswith('/api/metrics'):
            return _FakeResponse(payload={'counters': {}, 'timings': {}, 'beta': {}})
        if url.endswith('/api/metrics/prometheus'):
            return _FakeResponse(
                text='# TYPE aidm_telemetry_enabled gauge\naidm_telemetry_enabled 1\naidm_beta_bad_turn_reports 0\n',
                headers={'Content-Type': 'text/plain; version=0.0.4'},
            )
        raise AssertionError(url)

    monkeypatch.setattr('scripts.deployment_readiness_check.requests.get', fake_get)

    report = validate_live_target('https://aidm.example.test')

    assert not report.ok
    assert any('selected LLM provider is not configured' in error for error in report.errors)


def test_validate_live_target_rejects_missing_security_headers(monkeypatch):
    _stub_websocket_transport(monkeypatch)
    def fake_get(url, headers, timeout):
        del headers, timeout
        if url.endswith('/api/health'):
            return _FakeResponse(
                payload={
                    'status': 'ok',
                    'env': 'production',
                    'auth_required': True,
                    'llm': {'provider': 'gemini', 'configured': True},
                },
                headers={},
            )
        if url.endswith('/api/metrics'):
            return _FakeResponse(payload={'counters': {}, 'timings': {}, 'beta': {}})
        if url.endswith('/api/metrics/prometheus'):
            return _FakeResponse(
                text='# TYPE aidm_telemetry_enabled gauge\naidm_telemetry_enabled 1\naidm_beta_bad_turn_reports 0\n',
                headers={'Content-Type': 'text/plain'},
            )
        raise AssertionError(url)

    monkeypatch.setattr('scripts.deployment_readiness_check.requests.get', fake_get)

    report = validate_live_target('https://aidm.example.test')

    assert not report.ok
    assert any('missing security headers' in error for error in report.errors)


def test_validate_websocket_transport_forces_authenticated_upgrade_with_origin(monkeypatch):
    calls = {}

    class FakeSocketClient:
        def __init__(self, **kwargs):
            calls['init'] = kwargs

        def connect(self, target_url, **kwargs):
            calls['connect'] = (target_url, kwargs)

        def transport(self):
            return 'websocket'

        def disconnect(self):
            calls['disconnected'] = True

    monkeypatch.setattr('scripts.deployment_readiness_check.socketio.Client', FakeSocketClient)

    report = validate_websocket_transport(
        'https://api.aidm.example.test/base/',
        auth_token='live-token',
        origin='https://play.aidm.example.test',
        timeout_seconds=4,
    )

    assert report.ok
    assert calls['init'] == {
        'reconnection': False,
        'request_timeout': 4,
        'websocket_extra_options': {'origin': 'https://play.aidm.example.test'},
    }
    assert calls['connect'] == (
        'https://api.aidm.example.test/base',
        {
            'headers': {'Authorization': 'Bearer live-token'},
            'auth': {'workspace_token': 'live-token'},
            'transports': ['websocket'],
            'wait_timeout': 4,
        },
    )
    assert calls['disconnected'] is True


def test_validate_websocket_transport_reports_redacted_failure(monkeypatch):
    class FailingSocketClient:
        def __init__(self, **_kwargs):
            pass

        def connect(self, *_args, **_kwargs):
            raise RuntimeError('secret live-token')

    monkeypatch.setattr('scripts.deployment_readiness_check.socketio.Client', FailingSocketClient)

    report = validate_websocket_transport('https://aidm.example.test', auth_token='live-token')

    assert report.errors == ['Socket.IO WebSocket probe failed (RuntimeError).']
    assert 'live-token' not in ' '.join(report.errors)


def test_parse_env_file_rejects_invalid_lines(tmp_path: Path):
    env_file = tmp_path / '.env.production'
    env_file.write_text('AIDM_ENV production\n', encoding='utf-8')

    with pytest.raises(ValueError, match='expected KEY=value'):
        parse_env_file(env_file)


def test_main_writes_markdown_evidence_report_for_env_check(tmp_path: Path, monkeypatch):
    env_file = tmp_path / '.env.production'
    report_path = tmp_path / 'deployment-readiness.md'
    _write_ready_env_file(env_file)
    _stub_database_connectivity(monkeypatch)

    exit_code = main(['--env-file', str(env_file), '--evidence-report', str(report_path)])

    assert exit_code == 0
    report = report_path.read_text(encoding='utf-8')
    assert '# Deployment Readiness Evidence' in report
    assert '- Status: passed' in report
    assert f'- Env file: `{env_file}`' in report
    assert '| Environment configuration | passed | 0 | 0 |' in report


def test_main_writes_default_evidence_report_path(tmp_path: Path, monkeypatch):
    env_file = tmp_path / '.env.production'
    _write_ready_env_file(env_file)
    monkeypatch.setattr('scripts.deployment_readiness_check.REPO_ROOT', tmp_path)
    _stub_database_connectivity(monkeypatch)

    exit_code = main(['--env-file', str(env_file), '--evidence-report'])

    report_path = tmp_path / 'tmp/release/deployment-readiness-evidence.md'
    assert exit_code == 0
    assert report_path.exists()
    assert '# Deployment Readiness Evidence' in report_path.read_text(encoding='utf-8')


def test_main_writes_json_evidence_report_for_live_target(tmp_path: Path, monkeypatch):
    env_file = tmp_path / '.env.production'
    report_path = tmp_path / 'deployment-readiness.json'
    _write_ready_env_file(env_file)
    _stub_database_connectivity(monkeypatch)
    _stub_websocket_transport(monkeypatch)
    security_headers = {header: 'set' for header in REQUIRED_SECURITY_HEADERS}

    def fake_get(url, headers, timeout):
        assert headers == {'Authorization': 'Bearer live-token'}
        assert timeout == 3
        if url.endswith('/api/health'):
            return _FakeResponse(
                payload={
                    'status': 'ok',
                    'env': 'production',
                    'auth_required': True,
                    'llm': {'provider': 'gemini', 'configured': True},
                },
                headers=security_headers,
            )
        if url.endswith('/api/metrics'):
            return _FakeResponse(payload={'counters': {}, 'timings': {}, 'beta': {}})
        if url.endswith('/api/metrics/prometheus'):
            return _FakeResponse(
                text='# TYPE aidm_telemetry_enabled gauge\naidm_telemetry_enabled 1\naidm_beta_bad_turn_reports 0\n',
                headers={'Content-Type': 'text/plain'},
            )
        raise AssertionError(url)

    monkeypatch.setattr('scripts.deployment_readiness_check.requests.get', fake_get)

    exit_code = main(
        [
            '--env-file',
            str(env_file),
            '--target-url',
            'https://aidm.example.test',
            '--auth-token',
            'live-token',
            '--timeout-seconds',
            '3',
            '--evidence-report',
            str(report_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding='utf-8'))
    assert payload['status'] == 'passed'
    assert payload['options']['auth_token_provided'] is True
    assert payload['options']['target_url'] == 'https://aidm.example.test'
    assert payload['sections'][0]['label'] == 'Environment configuration'
    assert payload['sections'][1]['label'] == 'Database connectivity'
    assert payload['sections'][2]['label'] == 'Live target checks'
    assert payload['sections'][2]['status'] == 'passed'


def test_main_writes_evidence_report_when_env_file_is_invalid(tmp_path: Path):
    env_file = tmp_path / '.env.production'
    report_path = tmp_path / 'deployment-readiness.md'
    env_file.write_text('AIDM_ENV production\n', encoding='utf-8')

    exit_code = main(['--env-file', str(env_file), '--evidence-report', str(report_path)])

    assert exit_code == 1
    report = report_path.read_text(encoding='utf-8')
    assert '- Status: failed' in report
    assert 'Environment file load' in report
    assert 'expected KEY=value' in report
