from __future__ import annotations

import importlib
import os

from aidm_server.config import default_sqlite_uri
from aidm_server.database import _resolve_sqlite_uri
from aidm_server.database import engine_options_for_database_uri, ensure_schema


def test_health_endpoint_available_without_llm_key(client):
    response = client.get('/api/health')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['status'] == 'ok'
    assert 'auth_required' in payload
    assert 'latest_turn' not in payload['llm']


def test_non_ai_crud_works_without_llm_key(client):
    response = client.post('/api/worlds', json={'name': 'Faerun', 'description': 'High fantasy'})
    assert response.status_code == 201
    payload = response.get_json()
    assert payload['world_id'] > 0


def test_worlds_can_be_listed_for_campaign_creation_lookup(client):
    first = client.post('/api/worlds', json={'name': 'First World', 'description': 'Older'})
    second = client.post('/api/worlds', json={'name': 'Second World', 'description': 'Newer'})
    assert first.status_code == 201
    assert second.status_code == 201

    response = client.get('/api/worlds')

    assert response.status_code == 200
    payload = response.get_json()
    names = [world['name'] for world in payload]
    assert 'First World' in names
    assert 'Second World' in names
    assert {'world_id', 'name', 'description', 'created_at'} <= set(payload[0])


def test_http_response_includes_request_correlation_id(client):
    response = client.get('/api/health', headers={'X-Request-ID': 'test-correlation-id'})
    assert response.status_code == 200
    assert response.headers.get('X-Request-ID') == 'test-correlation-id'


def test_http_response_includes_security_headers(client):
    response = client.get('/api/health')

    assert response.status_code == 200
    assert response.headers.get('X-Content-Type-Options') == 'nosniff'
    assert response.headers.get('X-Frame-Options') == 'DENY'
    assert response.headers.get('Referrer-Policy') == 'no-referrer'
    assert response.headers.get('Permissions-Policy') == 'camera=(), microphone=(), geolocation=(), payment=()'
    csp = response.headers.get('Content-Security-Policy', '')
    assert "default-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com" in csp
    assert "font-src 'self' data: https://fonts.gstatic.com" in csp
    assert "frame-ancestors 'none'" in csp


def test_security_headers_can_be_disabled(tmp_path, monkeypatch):
    db_path = tmp_path / 'security_headers_disabled.db'
    monkeypatch.setenv('AIDM_DATABASE_URI', f'sqlite:///{db_path}')
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'true')
    monkeypatch.setenv('AIDM_ENV', 'test')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('AIDM_SECURITY_HEADERS_ENABLED', 'false')
    monkeypatch.setenv('AIDM_CORS_ALLOWLIST', 'http://localhost')
    monkeypatch.setenv('AIDM_SOCKET_CORS_ALLOWLIST', 'http://localhost')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')

    import aidm_server.main as main_module

    main_module = importlib.reload(main_module)
    app = main_module.create_app()
    ensure_schema(app)
    test_client = app.test_client()

    response = test_client.get('/api/health')

    assert response.status_code == 200
    assert 'Content-Security-Policy' not in response.headers
    assert 'X-Frame-Options' not in response.headers


def test_root_reports_backend_metadata(client):
    response = client.get('/')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {
        'service': 'aidm_backend',
        'status': 'ok',
        'frontend': 'React app served separately from aidm_frontend',
    }


def test_legacy_codex_frontend_route_is_removed(client):
    response = client.get('/codex')

    assert response.status_code == 404


def test_frontend_static_mode_serves_spa_and_assets(tmp_path, monkeypatch):
    db_path = tmp_path / 'frontend_static.db'
    dist_dir = tmp_path / 'dist'
    assets_dir = dist_dir / 'assets'
    assets_dir.mkdir(parents=True)
    (dist_dir / 'index.html').write_text('<div id="root">AIDM Frontend</div>', encoding='utf-8')
    (assets_dir / 'index.js').write_text('console.log("aidm")', encoding='utf-8')

    monkeypatch.setenv('AIDM_DATABASE_URI', f'sqlite:///{db_path}')
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'true')
    monkeypatch.setenv('AIDM_ENV', 'test')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('AIDM_SERVE_FRONTEND', 'true')
    monkeypatch.setenv('AIDM_FRONTEND_DIST_DIR', str(dist_dir))
    monkeypatch.setenv('AIDM_CORS_ALLOWLIST', 'http://localhost')
    monkeypatch.setenv('AIDM_SOCKET_CORS_ALLOWLIST', 'http://localhost')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')

    import aidm_server.main as main_module

    main_module = importlib.reload(main_module)
    app = main_module.create_app()
    test_client = app.test_client()

    root_response = test_client.get('/')
    assert root_response.status_code == 200
    assert b'AIDM Frontend' in root_response.data

    asset_response = test_client.get('/assets/index.js')
    assert asset_response.status_code == 200
    assert b'console.log("aidm")' in asset_response.data

    spa_response = test_client.get('/campaigns/10/sessions/20')
    assert spa_response.status_code == 200
    assert b'AIDM Frontend' in spa_response.data

    api_response = test_client.get('/api/not-a-real-route')
    assert api_response.status_code == 404
    assert b'AIDM Frontend' not in api_response.data


def test_frontend_static_mode_reports_missing_build(tmp_path, monkeypatch):
    db_path = tmp_path / 'frontend_missing.db'
    missing_dist = tmp_path / 'missing-dist'

    monkeypatch.setenv('AIDM_DATABASE_URI', f'sqlite:///{db_path}')
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'true')
    monkeypatch.setenv('AIDM_ENV', 'test')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('AIDM_SERVE_FRONTEND', 'true')
    monkeypatch.setenv('AIDM_FRONTEND_DIST_DIR', str(missing_dist))
    monkeypatch.setenv('AIDM_CORS_ALLOWLIST', 'http://localhost')
    monkeypatch.setenv('AIDM_SOCKET_CORS_ALLOWLIST', 'http://localhost')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')

    import aidm_server.main as main_module

    main_module = importlib.reload(main_module)
    app = main_module.create_app()

    response = app.test_client().get('/')

    assert response.status_code == 503
    assert response.get_json()['error_code'] == 'frontend_not_built'


def test_admin_ui_is_not_registered_when_disabled(client):
    response = client.get('/admin/')

    assert response.status_code == 404


def test_admin_ui_requires_auth_when_enabled_for_local_development(tmp_path, monkeypatch):
    db_path = tmp_path / 'admin_enabled.db'
    monkeypatch.setenv('AIDM_DATABASE_URI', f'sqlite:///{db_path}')
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'true')
    monkeypatch.setenv('AIDM_ENV', 'local')
    monkeypatch.setenv('AIDM_ADMIN_ENABLED', 'true')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('AIDM_CORS_ALLOWLIST', 'http://localhost')
    monkeypatch.setenv('AIDM_SOCKET_CORS_ALLOWLIST', 'http://localhost')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')

    import aidm_server.main as main_module

    main_module = importlib.reload(main_module)
    app = main_module.create_app()
    ensure_schema(app)

    response = app.test_client().get('/admin/')

    assert response.status_code == 403


def test_llm_config_update_requires_json_body(client):
    response = client.post('/api/llm/config', data='not-json', content_type='text/plain')

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'


def test_llm_config_exposes_provider_capabilities(client, monkeypatch, tmp_path):
    monkeypatch.setenv('AIDM_NVIDIA_API_KEY', 'nvapi-test')
    codex_executable = tmp_path / 'codex'
    codex_executable.write_text('#!/bin/sh\n', encoding='utf-8')
    codex_executable.chmod(0o755)
    monkeypatch.setenv('AIDM_CODEX_EXECUTABLE', str(codex_executable))
    monkeypatch.delenv('AIDM_DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)

    response = client.get('/api/llm/config')

    assert response.status_code == 200
    payload = response.get_json()
    providers = {provider['id']: provider for provider in payload['providers']}
    codex_models = providers['codex_cli']['models']
    assert providers['deepseek']['capabilities']['openai_compatible'] is True
    assert providers['deepseek']['capabilities']['thinking_control'] is True
    assert providers['deepseek']['configured'] is False
    assert providers['codex_cli']['label'] == 'Codex'
    assert providers['codex_cli']['default_model'] == 'gpt-5.5-medium'
    assert [model['id'] for model in codex_models] == [
        'gpt-5.5-low',
        'gpt-5.5-medium',
        'gpt-5.5-high',
        'gpt-5.5-xhigh',
    ]
    assert [model['reasoning_effort'] for model in codex_models] == ['low', 'medium', 'high', 'xhigh']
    assert providers['codex_cli']['configured'] is True
    assert providers['codex_cli']['capabilities']['streaming'] is True
    assert providers['codex_cli']['capabilities']['oauth_cli'] is True
    assert providers['nvidia']['configured'] is True
    assert providers['nvidia']['capabilities']['default_timeout_seconds'] >= 1
    assert payload['runtime_scope'] == 'process'
    assert payload['restart_required_for_other_workers'] is True


def test_llm_config_marks_codex_configured_from_mac_app_bundle(client, monkeypatch, tmp_path):
    from aidm_server import codex_runtime

    app_executable = tmp_path / 'Codex.app' / 'Contents' / 'Resources' / 'codex'
    app_executable.parent.mkdir(parents=True)
    app_executable.write_text('#!/bin/sh\n', encoding='utf-8')
    app_executable.chmod(0o755)
    monkeypatch.delenv('AIDM_CODEX_EXECUTABLE', raising=False)
    monkeypatch.setattr(codex_runtime.shutil, 'which', lambda executable: None)
    monkeypatch.setattr(codex_runtime, 'DEFAULT_CODEX_APP_EXECUTABLES', (app_executable,))

    response = client.get('/api/llm/config')

    assert response.status_code == 200
    payload = response.get_json()
    providers = {provider['id']: provider for provider in payload['providers']}
    assert providers['codex_cli']['configured'] is True


def test_llm_config_update_accepts_codex_reasoning_effort_model(client, monkeypatch, tmp_path):
    codex_executable = tmp_path / 'codex'
    codex_executable.write_text('#!/bin/sh\n', encoding='utf-8')
    codex_executable.chmod(0o755)
    monkeypatch.setenv('AIDM_CODEX_EXECUTABLE', str(codex_executable))
    monkeypatch.delenv('AIDM_CODEX_REASONING_EFFORT', raising=False)
    monkeypatch.delenv('AIDM_CODEX_TIMEOUT_SECONDS', raising=False)

    response = client.patch(
        '/api/llm/config',
        json={'provider': 'codex_cli', 'model': 'gpt-5.5-xhigh', 'persist': False},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['current']['provider'] == 'codex_cli'
    assert payload['current']['model'] == 'gpt-5.5-xhigh'
    assert payload['persisted'] is False
    assert os.environ['AIDM_CODEX_REASONING_EFFORT'] == 'xhigh'
    assert os.environ['AIDM_CODEX_TIMEOUT_SECONDS'] == '240'


def test_llm_config_update_normalizes_legacy_codex_model(client, monkeypatch, tmp_path):
    codex_executable = tmp_path / 'codex'
    codex_executable.write_text('#!/bin/sh\n', encoding='utf-8')
    codex_executable.chmod(0o755)
    monkeypatch.setenv('AIDM_CODEX_EXECUTABLE', str(codex_executable))

    response = client.patch(
        '/api/llm/config',
        json={'provider': 'codex_cli', 'model': 'gpt-5.5', 'persist': False},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['current']['provider'] == 'codex_cli'
    assert payload['current']['model'] == 'gpt-5.5-medium'
    assert os.environ['AIDM_CODEX_REASONING_EFFORT'] == 'medium'


def test_llm_config_persist_writes_active_env_file(client, monkeypatch, tmp_path):
    active_env = tmp_path / 'desktop-env.local'
    active_env.write_text(
        'AIDM_LLM_PROVIDER=deepseek\n'
        'AIDM_LLM_MODEL=deepseek-v4-pro\n'
        'AIDM_LLM_FALLBACK_MODELS=\n',
        encoding='utf-8',
    )
    monkeypatch.setenv('AIDM_ENV_FILE', str(active_env))
    monkeypatch.setenv('AIDM_SKIP_REPO_ENV_LOCAL', '1')

    response = client.patch(
        '/api/llm/config',
        json={'provider': 'fallback', 'model': 'deterministic-v1', 'persist': True},
    )

    assert response.status_code == 200
    persisted = active_env.read_text(encoding='utf-8')
    assert 'AIDM_LLM_PROVIDER=fallback\n' in persisted
    assert 'AIDM_LLM_MODEL=deterministic-v1\n' in persisted


def test_llm_config_route_is_owned_by_runtime_config_blueprint(app):
    assert app.view_functions['runtime_config.llm_config']
    assert app.view_functions['runtime_config.update_llm_config']
    assert 'system.llm_config' not in app.view_functions
    assert 'system.update_llm_config' not in app.view_functions


def test_in_memory_sqlite_uri_is_not_rewritten():
    assert _resolve_sqlite_uri('sqlite:///:memory:', '/tmp/app-root') == 'sqlite:///:memory:'


def test_default_sqlite_uri_uses_local_data_dir(tmp_path, monkeypatch):
    local_data_dir = tmp_path / 'aidm-data'
    monkeypatch.setenv('AIDM_LOCAL_DATA_DIR', str(local_data_dir))

    assert default_sqlite_uri() == f"sqlite:///{local_data_dir / 'dnd_ai_dm.db'}"


def test_database_engine_options_are_sqlite_specific():
    sqlite_options = engine_options_for_database_uri('sqlite:///local.db')
    postgres_options = engine_options_for_database_uri('postgresql://user:pass@example.test/db')

    assert sqlite_options['connect_args']['check_same_thread'] is False
    assert sqlite_options['connect_args']['timeout'] == 30
    assert postgres_options == {}


def test_main_module_stays_factory_only():
    import aidm_server.main as main_module

    main_module = importlib.reload(main_module)

    assert not hasattr(main_module, 'app')
    assert not hasattr(main_module, 'socketio')


def test_production_auto_create_schema_defaults_off_and_cannot_be_forced(tmp_path, monkeypatch):
    db_path = tmp_path / 'prod_schema.db'
    monkeypatch.setenv('AIDM_DATABASE_URI', f'sqlite:///{db_path}')
    monkeypatch.setenv('AIDM_ENV', 'production')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('FLASK_SECRET_KEY', 'prod-secret-for-test')
    monkeypatch.setenv('AIDM_AUTH_REQUIRED', 'true')
    monkeypatch.setenv('AIDM_API_AUTH_TOKENS', 'token-123')
    monkeypatch.setenv('AIDM_CORS_ALLOWLIST', 'https://example.com')
    monkeypatch.setenv('AIDM_SOCKET_CORS_ALLOWLIST', 'https://example.com')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')
    monkeypatch.delenv('AIDM_AUTO_CREATE_SCHEMA', raising=False)

    import aidm_server.main as main_module

    main_module = importlib.reload(main_module)
    app = main_module.create_app()
    assert app.config['AIDM_AUTO_CREATE_SCHEMA'] is False

    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'true')
    main_module = importlib.reload(main_module)
    try:
        main_module.build_runtime()
    except RuntimeError as exc:
        assert 'AIDM_AUTO_CREATE_SCHEMA must be false in production' in str(exc)
    else:
        raise AssertionError('Production runtime should reject auto schema creation.')
