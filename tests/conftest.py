from __future__ import annotations

import importlib

import pytest

from aidm_server.database import ensure_schema


@pytest.fixture(autouse=True)
def disable_external_codex_helpers_by_default(monkeypatch):
    monkeypatch.setenv('AIDM_CODEX_EXECUTABLE', '/nonexistent/aidm-test-codex')


@pytest.fixture()
def app_runtime(tmp_path, monkeypatch):
    db_path = tmp_path / 'test.db'

    monkeypatch.setenv('AIDM_DATABASE_URI', f'sqlite:///{db_path}')
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'true')
    monkeypatch.setenv('AIDM_ENV', 'test')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('AIDM_CORS_ALLOWLIST', 'http://localhost')
    monkeypatch.setenv('AIDM_SOCKET_CORS_ALLOWLIST', 'http://localhost')
    monkeypatch.setenv('AIDM_SOCKETIO_ASYNC_MODE', 'threading')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')
    monkeypatch.setenv('AIDM_RATE_LIMIT_MAX_API_REQUESTS', '1000')
    monkeypatch.setenv('AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES', '1000')
    monkeypatch.setenv('GOOGLE_GENAI_API_KEY', '')
    monkeypatch.setenv('AIDM_NVIDIA_API_KEY', '')
    monkeypatch.setenv('NVIDIA_API_KEY', '')

    import aidm_server.main as main_module
    main_module = importlib.reload(main_module)

    import aidm_server.turn_engine as turn_engine_module
    turn_engine_module = importlib.reload(turn_engine_module)
    import aidm_server.blueprints.socketio_events as socketio_events_module
    socketio_events_module = importlib.reload(socketio_events_module)

    socketio_events_module.active_players.clear()
    socketio_events_module.socketio_connections.clear()

    app = main_module.create_app()
    ensure_schema(app)
    socketio = main_module.create_socketio(app)
    socketio_events_module.register_socketio_events(socketio)

    yield {
        'app': app,
        'socketio': socketio,
        'modules': {
            'main': main_module,
            'socketio_events': socketio_events_module,
            'turn_engine': turn_engine_module,
        },
    }


@pytest.fixture()
def app(app_runtime):
    return app_runtime['app']


@pytest.fixture()
def socketio(app_runtime):
    return app_runtime['socketio']


@pytest.fixture()
def client(app):
    return app.test_client()
