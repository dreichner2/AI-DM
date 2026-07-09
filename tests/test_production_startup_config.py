from __future__ import annotations

import os
import subprocess
import sys

import pytest

from aidm_server.config import load_config, validate_production_startup_config


def _configure_safe_production(monkeypatch) -> None:
    values = {
        'AIDM_ENV': 'production',
        'AIDM_DEBUG': 'false',
        'AIDM_DATABASE_URI': 'postgresql+psycopg://aidm:secret@db.internal:5432/aidm',
        'AIDM_AUTO_CREATE_SCHEMA': 'false',
        'FLASK_SECRET_KEY': 's' * 40,
        'AIDM_AUTH_REQUIRED': 'true',
        'AIDM_API_AUTH_TOKENS': 'operator-token',
        'AIDM_RATE_LIMIT_STORE': 'database',
        'AIDM_TURN_COORDINATOR_STORE': 'database',
        'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
        'AIDM_SOCKETIO_WORKER_MODEL': 'single',
        'AIDM_GUNICORN_THREADS': '100',
        'WEB_CONCURRENCY': '1',
        'AIDM_CORS_ALLOWLIST': 'https://aidm.example.test',
        'AIDM_SOCKET_CORS_ALLOWLIST': 'https://aidm.example.test',
        'AIDM_SECURITY_HEADERS_ENABLED': 'true',
        'AIDM_OBSERVABILITY_PROVIDER': 'managed-prometheus',
        'AIDM_ALERT_OWNER': 'beta-oncall',
        'AIDM_LLM_PROVIDER': 'fallback',
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_production_wsgi_config_accepts_explicit_safe_settings(monkeypatch):
    _configure_safe_production(monkeypatch)

    validate_production_startup_config(load_config())


def test_production_wsgi_config_rejects_implicit_database(monkeypatch):
    _configure_safe_production(monkeypatch)
    monkeypatch.delenv('AIDM_DATABASE_URI')

    with pytest.raises(ValueError, match='AIDM_DATABASE_URI must be explicitly configured'):
        validate_production_startup_config(load_config())


def test_production_wsgi_config_rejects_non_postgres_database(monkeypatch):
    _configure_safe_production(monkeypatch)
    monkeypatch.setenv('AIDM_DATABASE_URI', 'sqlite:////tmp/explicit-production.sqlite')

    with pytest.raises(ValueError, match=r'must use postgresql\+psycopg in production'):
        validate_production_startup_config(load_config())


def test_production_wsgi_config_rejects_process_local_coordination(monkeypatch):
    _configure_safe_production(monkeypatch)
    monkeypatch.setenv('AIDM_RATE_LIMIT_STORE', 'memory')
    monkeypatch.setenv('AIDM_TURN_COORDINATOR_STORE', 'memory')

    with pytest.raises(ValueError) as exc_info:
        validate_production_startup_config(load_config())

    message = str(exc_info.value)
    assert 'AIDM_RATE_LIMIT_STORE must be database' in message
    assert 'AIDM_TURN_COORDINATOR_STORE must be database' in message


def test_production_wsgi_config_rejects_flask_admin(monkeypatch):
    _configure_safe_production(monkeypatch)
    monkeypatch.setenv('AIDM_ADMIN_ENABLED', 'true')

    with pytest.raises(ValueError, match='AIDM_ADMIN_ENABLED must be false in production'):
        validate_production_startup_config(load_config())


def test_production_wsgi_config_enforces_single_threaded_worker_capacity(monkeypatch):
    _configure_safe_production(monkeypatch)
    monkeypatch.setenv('AIDM_SOCKETIO_WORKER_MODEL', 'sticky')
    monkeypatch.setenv('AIDM_SOCKETIO_MESSAGE_QUEUE', 'redis://redis.internal:6379/0')
    monkeypatch.setenv('WEB_CONCURRENCY', '2')
    monkeypatch.setenv('AIDM_GUNICORN_THREADS', '8')

    with pytest.raises(ValueError) as exc_info:
        validate_production_startup_config(load_config())

    message = str(exc_info.value)
    assert 'production currently supports only AIDM_SOCKETIO_WORKER_MODEL=single' in message
    assert 'AIDM_GUNICORN_THREADS must be an integer >= 16' in message


def test_production_wsgi_config_rejects_unsafe_single_worker_process_sizing(monkeypatch):
    _configure_safe_production(monkeypatch)
    monkeypatch.setenv('WEB_CONCURRENCY', '2')
    monkeypatch.setenv('AIDM_GUNICORN_THREADS', '015')

    with pytest.raises(ValueError) as exc_info:
        validate_production_startup_config(load_config())

    message = str(exc_info.value)
    assert 'AIDM_SOCKETIO_WORKER_MODEL=single requires WEB_CONCURRENCY=1' in message
    assert 'AIDM_GUNICORN_THREADS must be an integer >= 16' in message


def test_non_production_wsgi_config_keeps_local_defaults(monkeypatch):
    monkeypatch.setenv('AIDM_ENV', 'development')
    monkeypatch.delenv('AIDM_DATABASE_URI', raising=False)

    validate_production_startup_config(load_config())


def test_wsgi_import_fails_before_building_unsafe_production_runtime():
    env = {
        **os.environ,
        'AIDM_SKIP_REPO_ENV_LOCAL': '1',
        'AIDM_ENV': 'production',
        'AIDM_DEBUG': 'false',
        'AIDM_DATABASE_URI': 'sqlite:////tmp/unsafe-production.sqlite',
        'AIDM_AUTO_CREATE_SCHEMA': 'false',
        'FLASK_SECRET_KEY': 's' * 40,
        'AIDM_AUTH_REQUIRED': 'false',
        'AIDM_RATE_LIMIT_STORE': 'memory',
        'AIDM_TURN_COORDINATOR_STORE': 'memory',
        'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
        'AIDM_SOCKETIO_WORKER_MODEL': 'single',
        'AIDM_OBSERVABILITY_PROVIDER': 'test-observability',
        'AIDM_ALERT_OWNER': 'test-owner',
    }

    result = subprocess.run(
        [sys.executable, '-c', 'import aidm_server.wsgi'],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert 'Unsafe production startup configuration' in output
    assert 'AIDM_AUTH_REQUIRED must be true' in output
    assert 'AIDM_RATE_LIMIT_STORE must be database' in output
    assert 'Database initialized' not in output
