from __future__ import annotations

import os
import pathlib
import sqlite3
import stat
import subprocess
import sys

import pytest

from aidm_server.deploy_bootstrap import (
    BootstrapError,
    BootstrapReport,
    _harden_env_local_permissions,
    _validate_endpoints,
    _validate_auth_config,
    _validate_observability_config,
    _validate_rate_limits,
    _validate_network_exposure,
    _validate_security_headers_config,
    _validate_server_start_allowed,
    _validate_socketio_deployment_config,
    build_parser,
)
from aidm_server.database import harden_sqlite_permissions


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = REPO_ROOT / 'scripts' / 'deploy_bootstrap.py'


def _run_bootstrap(env_overrides: dict[str, str]):
    env = os.environ.copy()
    env.update(env_overrides)
    env['PYTHONPATH'] = str(REPO_ROOT)
    env['PYTHON_DOTENV_DISABLED'] = '1'

    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP_SCRIPT), '--check-only'],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    return result


def test_deploy_bootstrap_check_only_success(tmp_path):
    db_path = tmp_path / 'bootstrap_success.db'

    result = _run_bootstrap(
        {
            'AIDM_DATABASE_URI': f'sqlite:///{db_path}',
            'AIDM_AUTO_CREATE_SCHEMA': 'false',
            'AIDM_ENV': 'test',
            'AIDM_DEBUG': 'false',
            'AIDM_AUTH_REQUIRED': 'true',
            'AIDM_API_AUTH_TOKENS': 'bootstrap-token',
            'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
            'AIDM_TELEMETRY_ENABLED': 'false',
            'AIDM_RATE_LIMIT_WINDOW_SECONDS': '30',
            'AIDM_RATE_LIMIT_MAX_API_REQUESTS': '120',
            'AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES': '40',
        }
    )

    assert result.returncode == 0, f'STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}'
    assert 'Bootstrap checks passed' in result.stdout


def test_deploy_bootstrap_check_only_accepts_workspace_scoped_local_tokens(tmp_path):
    db_path = tmp_path / 'bootstrap_workspace_tokens.db'

    result = _run_bootstrap(
        {
            'AIDM_DATABASE_URI': f'sqlite:///{db_path}',
            'AIDM_AUTO_CREATE_SCHEMA': 'false',
            'AIDM_ENV': 'test',
            'AIDM_DEBUG': 'false',
            'AIDM_AUTH_REQUIRED': 'true',
            'AIDM_API_AUTH_TOKENS': 'owner-token,friend-token',
            'AIDM_API_AUTH_TOKEN_WORKSPACES': 'owner=owner-token,friend=friend-token',
            'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
            'AIDM_TELEMETRY_ENABLED': 'false',
            'AIDM_RATE_LIMIT_WINDOW_SECONDS': '30',
            'AIDM_RATE_LIMIT_MAX_API_REQUESTS': '120',
            'AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES': '40',
        }
    )

    assert result.returncode == 0, f'STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}'
    assert 'Bootstrap checks passed' in result.stdout
    assert 'local preflight used a temporary in-process operator credential' in result.stdout


def test_validate_endpoints_keeps_workspace_tokens_scoped(app):
    original_tokens = ['owner-token', 'friend-token']
    app.config.update(
        AIDM_ENV='test',
        AIDM_AUTH_REQUIRED=True,
        AIDM_API_AUTH_TOKENS=original_tokens,
        AIDM_API_AUTH_TOKEN_WORKSPACES={
            'owner-token': 'owner',
            'friend-token': 'friend',
        },
    )

    assert _validate_endpoints(app) is True
    assert app.config['AIDM_API_AUTH_TOKENS'] is original_tokens
    response = app.test_client().get(
        '/api/metrics',
        headers={'Authorization': 'Bearer owner-token'},
    )
    assert response.status_code == 403
    assert response.get_json()['details']['required_capability'] == 'debug_read'


def test_validate_endpoints_requires_real_operator_token_in_production(app):
    app.config.update(
        AIDM_ENV='production',
        AIDM_AUTH_REQUIRED=True,
        AIDM_API_AUTH_TOKENS=['owner-token'],
        AIDM_API_AUTH_TOKEN_WORKSPACES={'owner-token': 'owner'},
    )

    with pytest.raises(BootstrapError, match='unscoped global operator token'):
        _validate_endpoints(app)


def test_deploy_bootstrap_fails_when_auth_required_without_tokens(tmp_path):
    db_path = tmp_path / 'bootstrap_fail.db'

    result = _run_bootstrap(
        {
            'AIDM_DATABASE_URI': f'sqlite:///{db_path}',
            'AIDM_AUTO_CREATE_SCHEMA': 'false',
            'AIDM_ENV': 'test',
            'AIDM_DEBUG': 'false',
            'AIDM_AUTH_REQUIRED': 'true',
            'AIDM_API_AUTH_TOKENS': '',
            'AIDM_API_AUTH_TOKEN_WORKSPACES': '',
            'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
            'AIDM_TELEMETRY_ENABLED': 'false',
            'AIDM_RATE_LIMIT_WINDOW_SECONDS': '30',
            'AIDM_RATE_LIMIT_MAX_API_REQUESTS': '120',
            'AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES': '40',
        }
    )

    assert result.returncode == 1
    assert 'AIDM_AUTH_REQUIRED=true but no AIDM_API_AUTH_TOKENS are configured' in result.stdout


def test_deploy_bootstrap_rejects_invalid_production_config_before_migrations(tmp_path):
    db_path = tmp_path / 'must-not-be-created.db'

    result = _run_bootstrap(
        {
            'AIDM_ENV': 'production',
            'AIDM_DEBUG': 'false',
            'AIDM_DATABASE_URI': f'sqlite:///{db_path}',
            'AIDM_AUTO_CREATE_SCHEMA': 'false',
            'FLASK_SECRET_KEY': 's' * 40,
            'AIDM_AUTH_REQUIRED': 'true',
            'AIDM_API_AUTH_TOKENS': 'operator-token',
            'AIDM_RATE_LIMIT_STORE': 'database',
            'AIDM_TURN_COORDINATOR_STORE': 'database',
            'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
            'AIDM_SOCKETIO_WORKER_MODEL': 'single',
            'AIDM_CORS_ALLOWLIST': 'https://aidm.example.test',
            'AIDM_SOCKET_CORS_ALLOWLIST': 'https://aidm.example.test',
            'AIDM_SECURITY_HEADERS_ENABLED': 'true',
            'AIDM_OBSERVABILITY_PROVIDER': 'managed-prometheus',
            'AIDM_ALERT_OWNER': 'beta-oncall',
            'AIDM_LLM_PROVIDER': 'fallback',
        }
    )

    assert result.returncode == 1
    assert 'must use postgresql+psycopg in production' in result.stdout
    assert '[bootstrap] Running migrations...' not in result.stdout
    assert not db_path.exists()


def test_deploy_bootstrap_fails_when_existing_schema_is_partial(tmp_path):
    db_path = tmp_path / 'bootstrap_partial.db'

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE worlds (
                world_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR NOT NULL,
                description VARCHAR,
                created_at DATETIME
            )
            """
        )
        conn.commit()

    result = _run_bootstrap(
        {
            'AIDM_DATABASE_URI': f'sqlite:///{db_path}',
            'AIDM_AUTO_CREATE_SCHEMA': 'false',
            'AIDM_ENV': 'test',
            'AIDM_DEBUG': 'false',
            'AIDM_AUTH_REQUIRED': 'false',
            'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
            'AIDM_TELEMETRY_ENABLED': 'false',
            'AIDM_RATE_LIMIT_WINDOW_SECONDS': '30',
            'AIDM_RATE_LIMIT_MAX_API_REQUESTS': '120',
            'AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES': '40',
        }
    )

    assert result.returncode == 1
    assert 'existing schema does not match current runtime metadata' in result.stdout


def test_harden_sqlite_permissions_locks_down_instance_files(tmp_path):
    instance_dir = tmp_path / 'instance'
    instance_dir.mkdir()
    db_path = instance_dir / 'runtime.db'
    backup_path = instance_dir / 'backup.db'
    db_path.write_bytes(b'sqlite')
    backup_path.write_bytes(b'backup')
    instance_dir.chmod(0o755)
    db_path.chmod(0o644)
    backup_path.chmod(0o644)

    changed = harden_sqlite_permissions(f'sqlite:///{db_path}')

    assert str(instance_dir) in changed
    assert str(db_path) in changed
    assert str(backup_path) in changed
    assert stat.S_IMODE(instance_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600


def test_harden_sqlite_permissions_locks_down_default_local_data_dir(tmp_path):
    local_data_dir = tmp_path / '.aidm'
    local_data_dir.mkdir()
    db_path = local_data_dir / 'dnd_ai_dm.db'
    backup_path = local_data_dir / 'dnd_ai_dm.backup.db'
    db_path.write_bytes(b'sqlite')
    backup_path.write_bytes(b'backup')
    local_data_dir.chmod(0o755)
    db_path.chmod(0o644)
    backup_path.chmod(0o644)

    changed = harden_sqlite_permissions(f'sqlite:///{db_path}')

    assert str(local_data_dir) in changed
    assert str(db_path) in changed
    assert str(backup_path) in changed
    assert stat.S_IMODE(local_data_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600


def test_harden_env_local_permissions_locks_down_file(tmp_path):
    env_local = tmp_path / '.env.local'
    env_local.write_text('AIDM_LLM_PROVIDER=fallback\n', encoding='utf-8')
    env_local.chmod(0o644)
    report = BootstrapReport(warnings=[])

    _harden_env_local_permissions(tmp_path, report)

    assert stat.S_IMODE(env_local.stat().st_mode) == 0o600
    assert '.env.local permissions were tightened to 0600.' in report.warnings


def test_validate_network_exposure_rejects_open_host_without_auth(app):
    app.config['AIDM_ENV'] = 'development'
    app.config['AIDM_AUTH_REQUIRED'] = False
    report = BootstrapReport(warnings=[])

    with pytest.raises(BootstrapError, match='auth is disabled'):
        _validate_network_exposure(app, '0.0.0.0', report)


def test_deploy_bootstrap_defaults_to_loopback_host(monkeypatch):
    monkeypatch.delenv('HOST', raising=False)

    args = build_parser().parse_args([])

    assert args.host == '127.0.0.1'


def test_validate_network_exposure_rejects_production_wildcard_cors(app):
    app.config['AIDM_ENV'] = 'production'
    app.config['AIDM_AUTH_REQUIRED'] = True
    app.config['AIDM_CORS_ALLOWLIST'] = ['https://client.example']
    app.config['AIDM_SOCKET_CORS_ALLOWLIST'] = ['*']
    report = BootstrapReport(warnings=[])

    with pytest.raises(BootstrapError, match='wildcard CORS'):
        _validate_network_exposure(app, '127.0.0.1', report)


def test_validate_rate_limits_rejects_unknown_store(app):
    app.config['AIDM_RATE_LIMIT_STORE'] = 'sidecar'

    with pytest.raises(BootstrapError, match='AIDM_RATE_LIMIT_STORE'):
        _validate_rate_limits(app)


def test_validate_rate_limits_rejects_unknown_turn_coordinator_store(app):
    app.config['AIDM_TURN_COORDINATOR_STORE'] = 'sidecar'

    with pytest.raises(BootstrapError, match='AIDM_TURN_COORDINATOR_STORE'):
        _validate_rate_limits(app)


def test_validate_rate_limits_requires_database_stores_in_production(app):
    app.config['AIDM_ENV'] = 'production'
    app.config['AIDM_RATE_LIMIT_STORE'] = 'memory'
    app.config['AIDM_TURN_COORDINATOR_STORE'] = 'database'

    with pytest.raises(BootstrapError, match='AIDM_RATE_LIMIT_STORE=database'):
        _validate_rate_limits(app)

    app.config['AIDM_RATE_LIMIT_STORE'] = 'database'
    app.config['AIDM_TURN_COORDINATOR_STORE'] = 'memory'

    with pytest.raises(BootstrapError, match='AIDM_TURN_COORDINATOR_STORE=database'):
        _validate_rate_limits(app)


def test_validate_rate_limits_rejects_invalid_turn_lock_settings(app):
    app.config['AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS'] = 5

    with pytest.raises(BootstrapError, match='AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS'):
        _validate_rate_limits(app)

    app.config['AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS'] = 30
    app.config['AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS'] = 5

    with pytest.raises(BootstrapError, match='AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS'):
        _validate_rate_limits(app)


def test_validate_socketio_deployment_config_requires_explicit_worker_model_in_production(app):
    app.config['AIDM_ENV'] = 'production'
    app.config['AIDM_SOCKETIO_WORKER_MODEL'] = 'single'
    app.config['AIDM_SOCKETIO_WORKER_MODEL_EXPLICIT'] = False
    report = BootstrapReport(warnings=[])

    with pytest.raises(BootstrapError, match='AIDM_SOCKETIO_WORKER_MODEL'):
        _validate_socketio_deployment_config(app, report)


def test_validate_socketio_deployment_config_accepts_production_single_worker(app):
    app.config['AIDM_ENV'] = 'production'
    app.config['AIDM_SOCKETIO_WORKER_MODEL'] = 'single'
    app.config['AIDM_SOCKETIO_WORKER_MODEL_EXPLICIT'] = True
    report = BootstrapReport(warnings=[])

    _validate_socketio_deployment_config(app, report)

    assert 'run exactly one backend worker' in report.warnings[0]


def test_validate_socketio_deployment_config_rejects_multi_worker_in_production(app):
    app.config['AIDM_ENV'] = 'production'
    app.config['AIDM_SOCKETIO_WORKER_MODEL'] = 'message_queue'
    app.config['AIDM_SOCKETIO_WORKER_MODEL_EXPLICIT'] = True
    app.config['AIDM_SOCKETIO_MESSAGE_QUEUE'] = 'redis://redis.example:6379/0'
    report = BootstrapReport(warnings=[])

    with pytest.raises(BootstrapError, match='currently supports only AIDM_SOCKETIO_WORKER_MODEL=single'):
        _validate_socketio_deployment_config(app, report)


def test_validate_socketio_deployment_config_requires_queue_for_nonproduction_multi_worker(app):
    app.config['AIDM_ENV'] = 'development'
    app.config['AIDM_SOCKETIO_WORKER_MODEL'] = 'message_queue'
    app.config['AIDM_SOCKETIO_MESSAGE_QUEUE'] = ''
    report = BootstrapReport(warnings=[])

    with pytest.raises(BootstrapError, match='AIDM_SOCKETIO_MESSAGE_QUEUE'):
        _validate_socketio_deployment_config(app, report)

    app.config['AIDM_SOCKETIO_MESSAGE_QUEUE'] = 'redis://redis.example:6379/0'
    _validate_socketio_deployment_config(app, report)


def test_validate_observability_config_requires_provider_and_owner_in_production(app):
    app.config['AIDM_ENV'] = 'production'
    app.config['AIDM_OBSERVABILITY_PROVIDER'] = ''
    app.config['AIDM_ALERT_OWNER'] = ''
    app.config['AIDM_TELEMETRY_ENABLED'] = False
    report = BootstrapReport(warnings=[])

    with pytest.raises(BootstrapError, match='AIDM_OBSERVABILITY_PROVIDER'):
        _validate_observability_config(app, report)

    app.config['AIDM_OBSERVABILITY_PROVIDER'] = 'grafana-cloud'
    with pytest.raises(BootstrapError, match='AIDM_ALERT_OWNER'):
        _validate_observability_config(app, report)


def test_validate_observability_config_checks_enabled_telemetry_endpoint(app):
    app.config['AIDM_ENV'] = 'test'
    app.config['AIDM_OBSERVABILITY_PROVIDER'] = ''
    app.config['AIDM_ALERT_OWNER'] = ''
    app.config['AIDM_TELEMETRY_ENABLED'] = True
    app.config['AIDM_TELEMETRY_ENDPOINT'] = ''
    report = BootstrapReport(warnings=[])

    with pytest.raises(BootstrapError, match='AIDM_TELEMETRY_ENDPOINT'):
        _validate_observability_config(app, report)

    app.config['AIDM_TELEMETRY_ENDPOINT'] = 'https://example.test/telemetry'
    _validate_observability_config(app, report)


def test_validate_security_headers_config_requires_headers_in_production(app):
    app.config['AIDM_ENV'] = 'production'
    app.config['AIDM_SECURITY_HEADERS_ENABLED'] = False

    with pytest.raises(BootstrapError, match='AIDM_SECURITY_HEADERS_ENABLED=true'):
        _validate_security_headers_config(app)

    app.config['AIDM_SECURITY_HEADERS_ENABLED'] = True
    app.config['AIDM_CONTENT_SECURITY_POLICY'] = ''

    with pytest.raises(BootstrapError, match='AIDM_CONTENT_SECURITY_POLICY'):
        _validate_security_headers_config(app)

    app.config['AIDM_CONTENT_SECURITY_POLICY'] = "default-src 'self'"
    _validate_security_headers_config(app)


def test_validate_auth_config_rejects_insecure_production_cookie_auth(app):
    app.config['AIDM_ENV'] = 'production'
    app.config['AIDM_AUTH_REQUIRED'] = True
    app.config['AIDM_API_AUTH_TOKENS'] = ['token-123']
    app.config['AIDM_API_AUTH_TOKEN_WORKSPACES'] = {}
    app.config['AIDM_ACCOUNT_COOKIE_AUTH_ENABLED'] = True
    app.config['AIDM_ACCOUNT_COOKIE_SECURE'] = False
    app.config['AIDM_ACCOUNT_COOKIE_SAMESITE'] = 'Lax'
    report = BootstrapReport(warnings=[])

    with pytest.raises(BootstrapError, match='AIDM_ACCOUNT_COOKIE_SECURE=true'):
        _validate_auth_config(app, report)


def test_validate_auth_config_warns_for_production_bearer_and_cookie_token_echo(app):
    app.config['AIDM_ENV'] = 'production'
    app.config['AIDM_AUTH_REQUIRED'] = True
    app.config['AIDM_API_AUTH_TOKENS'] = ['token-123']
    app.config['AIDM_API_AUTH_TOKEN_WORKSPACES'] = {}
    app.config['AIDM_ACCOUNT_COOKIE_AUTH_ENABLED'] = False
    report = BootstrapReport(warnings=[])

    _validate_auth_config(app, report)

    assert any('HTTP-only account cookie auth is disabled' in warning for warning in report.warnings)

    app.config['AIDM_ACCOUNT_COOKIE_AUTH_ENABLED'] = True
    app.config['AIDM_ACCOUNT_COOKIE_SECURE'] = True
    app.config['AIDM_ACCOUNT_COOKIE_SAMESITE'] = 'Lax'
    app.config['AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED'] = True
    report = BootstrapReport(warnings=[])

    _validate_auth_config(app, report)

    assert any('Account tokens are still returned in JSON' in warning for warning in report.warnings)


def test_validate_server_start_allowed_rejects_production_werkzeug(app):
    app.config['AIDM_ENV'] = 'production'

    with pytest.raises(BootstrapError, match='production server'):
        _validate_server_start_allowed(app)

    app.config['AIDM_ENV'] = 'test'
    _validate_server_start_allowed(app)
