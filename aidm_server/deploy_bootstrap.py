"""Deployment bootstrap runner for AI-DM.

This module runs preflight checks before starting the Socket.IO server:
1) `flask db upgrade`
2) `/api/health` + `/api/metrics` sanity checks
3) Socket auth + rate-limit configuration checks
4) Optional server startup
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import pathlib
import subprocess
import sys
import stat
from typing import Iterable

from sqlalchemy import inspect

from aidm_server.config import (
    SUPPORTED_TURN_COORDINATOR_STORES,
    TURN_COORDINATOR_STORE_DATABASE,
    TURN_COORDINATOR_STORE_MEMORY,
)
from aidm_server.rate_limiter import RATE_LIMIT_STORE_DATABASE, RATE_LIMIT_STORE_MEMORY, SUPPORTED_RATE_LIMIT_STORES


@dataclass
class BootstrapReport:
    warnings: list[str]


class BootstrapError(RuntimeError):
    pass


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def _load_runtime_factories():
    from aidm_server.main import build_runtime, create_app, create_socketio
    from aidm_server.blueprints.socketio_events import register_socketio_events

    return build_runtime, create_app, create_socketio, register_socketio_events


def _schema_mismatch_details() -> list[str]:
    previous_auto_create = os.environ.get('AIDM_AUTO_CREATE_SCHEMA')
    os.environ['AIDM_AUTO_CREATE_SCHEMA'] = 'false'
    try:
        _build_runtime, create_app, _create_socketio, _register_socketio_events = _load_runtime_factories()
        app = create_app()
    finally:
        if previous_auto_create is None:
            os.environ.pop('AIDM_AUTO_CREATE_SCHEMA', None)
        else:
            os.environ['AIDM_AUTO_CREATE_SCHEMA'] = previous_auto_create

    with app.app_context():
        from aidm_server.database import db

        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())
        expected_tables = set(db.metadata.tables.keys())
        mismatches: list[str] = []

        missing_tables = sorted(expected_tables - existing_tables)
        if missing_tables:
            mismatches.append(f"missing tables: {', '.join(missing_tables)}")

        for table_name in sorted(expected_tables & existing_tables):
            expected_columns = {column.name for column in db.metadata.tables[table_name].columns}
            existing_columns = {column['name'] for column in inspector.get_columns(table_name)}
            missing_columns = sorted(expected_columns - existing_columns)
            if missing_columns:
                mismatches.append(f"{table_name} missing columns: {', '.join(missing_columns)}")

        return mismatches


def run_migrations(repo_root: pathlib.Path):
    env = os.environ.copy()
    env.setdefault('FLASK_APP', 'aidm_server.main:create_app')
    # Migration commands should be the only schema mutator.
    env['AIDM_AUTO_CREATE_SCHEMA'] = 'false'
    existing_pythonpath = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = f"{repo_root}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(repo_root)

    def _run_db_command(args: list[str], print_output: bool = True):
        cmd = [sys.executable, '-m', 'flask', 'db', *args]
        result = subprocess.run(
            cmd,
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
        )
        if print_output:
            if result.stdout.strip():
                print(result.stdout.strip())
            if result.stderr.strip():
                print(result.stderr.strip())
        return result

    result = _run_db_command(['upgrade'], print_output=False)
    if result.returncode == 0:
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())
        return

    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    if 'already exists' in combined_output or 'duplicate column name' in combined_output:
        mismatches = _schema_mismatch_details()
        if mismatches:
            details = '; '.join(mismatches)
            raise BootstrapError(
                'Migration step failed: existing schema does not match current runtime metadata '
                f'({details}).'
            )
        print('[bootstrap][warning] Existing schema detected without Alembic state. Stamping head revision.')
        stamp_result = _run_db_command(['stamp', 'head'])
        if stamp_result.returncode == 0:
            return

    raise BootstrapError('Migration step failed: flask db upgrade returned non-zero exit status.')


def _validate_rate_limits(app):
    api_limit = app.config.get('AIDM_RATE_LIMIT_MAX_API_REQUESTS', 0)
    socket_limit = app.config.get('AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES', 0)
    window = app.config.get('AIDM_RATE_LIMIT_WINDOW_SECONDS', 0)
    store = str(app.config.get('AIDM_RATE_LIMIT_STORE', RATE_LIMIT_STORE_MEMORY) or '').strip().lower()
    turn_store = str(
        app.config.get('AIDM_TURN_COORDINATOR_STORE', TURN_COORDINATOR_STORE_MEMORY) or ''
    ).strip().lower()
    turn_lock_ttl = app.config.get('AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS', 0)
    turn_poll_interval_ms = app.config.get('AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS', 0)

    if not isinstance(api_limit, int) or api_limit < 1:
        raise BootstrapError('Invalid AIDM_RATE_LIMIT_MAX_API_REQUESTS; expected integer >= 1.')
    if not isinstance(socket_limit, int) or socket_limit < 1:
        raise BootstrapError('Invalid AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES; expected integer >= 1.')
    if not isinstance(window, int) or window < 1:
        raise BootstrapError('Invalid AIDM_RATE_LIMIT_WINDOW_SECONDS; expected integer >= 1.')
    if store not in SUPPORTED_RATE_LIMIT_STORES:
        expected = ', '.join(sorted(SUPPORTED_RATE_LIMIT_STORES))
        raise BootstrapError(f'Invalid AIDM_RATE_LIMIT_STORE; expected one of: {expected}.')
    if turn_store not in SUPPORTED_TURN_COORDINATOR_STORES:
        expected = ', '.join(sorted(SUPPORTED_TURN_COORDINATOR_STORES))
        raise BootstrapError(f'Invalid AIDM_TURN_COORDINATOR_STORE; expected one of: {expected}.')
    if app.config.get('AIDM_ENV') == 'production' and store != RATE_LIMIT_STORE_DATABASE:
        raise BootstrapError('AIDM_ENV=production requires AIDM_RATE_LIMIT_STORE=database.')
    if app.config.get('AIDM_ENV') == 'production' and turn_store != TURN_COORDINATOR_STORE_DATABASE:
        raise BootstrapError('AIDM_ENV=production requires AIDM_TURN_COORDINATOR_STORE=database.')
    if not isinstance(turn_lock_ttl, int) or turn_lock_ttl < 30:
        raise BootstrapError('Invalid AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS; expected integer >= 30.')
    if not isinstance(turn_poll_interval_ms, int) or turn_poll_interval_ms < 10:
        raise BootstrapError('Invalid AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS; expected integer >= 10.')


def _validate_auth_config(app, report: BootstrapReport):
    env = app.config.get('AIDM_ENV', 'development')
    auth_required = bool(app.config.get('AIDM_AUTH_REQUIRED', False))
    tokens = _configured_auth_tokens(app)

    if auth_required and not tokens:
        raise BootstrapError(
            'AIDM_AUTH_REQUIRED=true but no AIDM_API_AUTH_TOKENS are configured, '
            'and no AIDM_API_AUTH_TOKEN_WORKSPACES are configured.'
        )

    if env == 'production' and not auth_required:
        raise BootstrapError('AIDM_ENV=production requires AIDM_AUTH_REQUIRED=true for deployment bootstrap.')

    if not auth_required:
        report.warnings.append('Auth is disabled; suitable only for trusted/private deployment contexts.')


def _harden_env_local_permissions(repo_root: pathlib.Path, report: BootstrapReport):
    env_local = repo_root / '.env.local'
    if not env_local.exists():
        report.warnings.append('.env.local is not present; local provider/runtime overrides will not be loaded.')
        return
    if not env_local.is_file():
        raise BootstrapError('.env.local exists but is not a regular file.')

    current_mode = stat.S_IMODE(env_local.stat().st_mode)
    if current_mode != 0o600:
        env_local.chmod(0o600)
        report.warnings.append('.env.local permissions were tightened to 0600.')


def _harden_local_data_permissions(app, report: BootstrapReport):
    from aidm_server.database import harden_sqlite_permissions

    changed = harden_sqlite_permissions(
        str(app.config.get('SQLALCHEMY_DATABASE_URI', '')),
        app.root_path,
    )
    if changed:
        report.warnings.append(f'Tightened local SQLite permissions: {", ".join(changed)}')


def _validate_network_exposure(app, host: str, report: BootstrapReport):
    env = app.config.get('AIDM_ENV', 'development')
    auth_required = bool(app.config.get('AIDM_AUTH_REQUIRED', False))
    public_host = host in {'0.0.0.0', '::', ''}

    if public_host and not auth_required:
        raise BootstrapError(
            f'Server host {host or "<all>"} exposes the backend on the network while auth is disabled; '
            'set AIDM_AUTH_REQUIRED=true with API tokens or bind --host to a loopback interface.'
        )

    cors_allowlist = app.config.get('AIDM_CORS_ALLOWLIST', [])
    socket_allowlist = app.config.get('AIDM_SOCKET_CORS_ALLOWLIST', [])
    if env == 'production':
        if '*' in cors_allowlist or '*' in socket_allowlist:
            raise BootstrapError('AIDM_ENV=production does not allow wildcard CORS allowlists.')
        if not cors_allowlist or not socket_allowlist:
            report.warnings.append('Production CORS allowlists are empty; confirm same-origin deployment or set explicit origins.')


def _check_endpoint(client, path: str):
    response = client.get(path)
    if response.status_code != 200:
        raise BootstrapError(f'Sanity check failed for {path}: expected 200, got {response.status_code}.')
    return response.get_json(silent=True) or {}


def _configured_auth_tokens(app) -> list[str]:
    tokens = [token for token in app.config.get('AIDM_API_AUTH_TOKENS', []) if token]
    token_workspaces = app.config.get('AIDM_API_AUTH_TOKEN_WORKSPACES', {})
    if isinstance(token_workspaces, dict):
        tokens.extend(token for token in token_workspaces.keys() if token)
    return list(dict.fromkeys(tokens))


def _auth_headers_for_app(app) -> dict:
    if not bool(app.config.get('AIDM_AUTH_REQUIRED', False)):
        return {}
    tokens = _configured_auth_tokens(app)
    if not tokens:
        raise BootstrapError('Auth is required but no API tokens are configured for endpoint checks.')
    return {'Authorization': f'Bearer {tokens[0]}'}


def _validate_endpoints(app):
    client = app.test_client()
    auth_headers = _auth_headers_for_app(app)

    health_payload = _check_endpoint(client, '/api/health')
    if health_payload.get('status') != 'ok':
        raise BootstrapError('Health endpoint returned unexpected status payload.')

    response = client.get('/api/metrics', headers=auth_headers)
    if response.status_code != 200:
        raise BootstrapError(f'Sanity check failed for /api/metrics: expected 200, got {response.status_code}.')
    metrics_payload = response.get_json(silent=True) or {}
    if 'counters' not in metrics_payload or 'timings' not in metrics_payload:
        raise BootstrapError('Metrics endpoint payload missing required fields: counters/timings.')


def _validate_socket_auth_behavior(app, socketio):
    auth_required = bool(app.config.get('AIDM_AUTH_REQUIRED', False))
    tokens = _configured_auth_tokens(app)

    if auth_required:
        unauthorized_client = socketio.test_client(app, flask_test_client=app.test_client())
        unauthorized_connected = unauthorized_client.is_connected()
        if unauthorized_connected:
            unauthorized_client.disconnect()
            raise BootstrapError('Socket auth check failed: unauthenticated client connected while auth is required.')

        valid_token = tokens[0]
        authorized_client = socketio.test_client(
            app,
            flask_test_client=app.test_client(),
            auth={'token': valid_token},
        )
        if not authorized_client.is_connected():
            raise BootstrapError('Socket auth check failed: authenticated client could not connect.')
        authorized_client.disconnect()
        return

    open_client = socketio.test_client(app, flask_test_client=app.test_client())
    if not open_client.is_connected():
        raise BootstrapError('Socket check failed: client could not connect in non-auth mode.')
    open_client.disconnect()


def _build_runtime():
    build_runtime, _create_app, _create_socketio, _register_socketio_events = _load_runtime_factories()
    return build_runtime(ensure_schema_created=False)


def _validate_server_start_allowed(app):
    if app.config.get('AIDM_ENV') == 'production':
        raise BootstrapError(
            'Do not start the production server with deploy_bootstrap/Werkzeug. '
            'Run deploy_bootstrap --check-only, then start AIDM with a production Socket.IO server.'
        )


def bootstrap(check_only: bool, host: str, port: int):
    repo_root = _repo_root()
    report = BootstrapReport(warnings=[])

    print('[bootstrap] Running migrations...')
    run_migrations(repo_root)

    print('[bootstrap] Building runtime and validating config...')
    check_app, check_socketio = _build_runtime()
    _validate_rate_limits(check_app)
    _validate_auth_config(check_app, report)
    _harden_env_local_permissions(repo_root, report)
    _harden_local_data_permissions(check_app, report)
    _validate_network_exposure(check_app, host, report)

    print('[bootstrap] Running endpoint sanity checks...')
    _validate_endpoints(check_app)

    print('[bootstrap] Verifying socket auth behavior...')
    _validate_socket_auth_behavior(check_app, check_socketio)

    for warning in report.warnings:
        print(f'[bootstrap][warning] {warning}')

    print('[bootstrap] Bootstrap checks passed.')

    if check_only:
        print('[bootstrap] Check-only mode enabled; server will not start.')
        return 0

    # Build a fresh runtime after preflight checks so test probes do not affect
    # the long-running server state.
    app, socketio = _build_runtime()
    _validate_server_start_allowed(app)
    print(f'[bootstrap] Starting server on {host}:{port}...')
    socketio.run(
        app,
        host=host,
        port=port,
        debug=bool(app.config.get('DEBUG', False)),
        allow_unsafe_werkzeug=True,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='AI-DM deployment bootstrap')
    parser.add_argument('--check-only', action='store_true', help='Run preflight checks without starting server')
    parser.add_argument('--host', default=os.getenv('HOST', '127.0.0.1'), help='Host interface for server startup')
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT', '5000')), help='Port for server startup')
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        return bootstrap(check_only=args.check_only, host=args.host, port=args.port)
    except BootstrapError as exc:
        print(f'[bootstrap][error] {str(exc)}')
        return 1
    except Exception as exc:
        print(f'[bootstrap][error] {str(exc)}')
        return 1
    except KeyboardInterrupt:
        print('[bootstrap] Interrupted by user.')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
