from concurrent.futures import ThreadPoolExecutor
import time

from flask import Flask
import pytest

from aidm_server.socket_runtime import SocketRuntime
from aidm_server.socket_state import SocketState


def test_socket_runtime_clears_bound_player_and_room():
    state = SocketState()
    runtime = SocketRuntime(state)
    emitted = []
    left_rooms = []
    player = {'id': 7, 'character_name': 'Ember', 'name': 'Danny'}

    state.set_connection('sid-a', {'authorized': True, 'session_id': 3, 'player_id': 7})
    state.track_active_player(3, player, 'sid-a')

    record = runtime.clear_connection_binding(
        'sid-a',
        leave_bound_room=True,
        leave_room_fn=left_rooms.append,
        emit_fn=lambda name, payload, **kwargs: emitted.append((name, payload, kwargs)),
    )

    assert record == {'authorized': True, 'session_id': None, 'player_id': None}
    assert left_rooms == ['3']
    assert ('player_left', {'id': 7}, {'room': '3'}) in emitted
    assert ('active_players', [], {'room': '3'}) in emitted
    assert state.active_player_payloads(3) == []


def test_socket_runtime_keeps_player_until_last_socket_disconnects():
    state = SocketState()
    runtime = SocketRuntime(state)
    emitted = []
    player = {'id': 7, 'character_name': 'Ember', 'name': 'Danny'}

    state.set_connection('sid-a', {'authorized': True, 'session_id': 3, 'player_id': 7})
    state.set_connection('sid-b', {'authorized': True, 'session_id': 3, 'player_id': 7})
    state.track_active_player(3, player, 'sid-a')
    state.track_active_player(3, player, 'sid-b')

    runtime.release_disconnect(
        'sid-a',
        emit_fn=lambda name, payload, **kwargs: emitted.append((name, payload, kwargs)),
    )
    assert state.active_player_payloads(3) == [player]
    assert emitted == []

    runtime.release_disconnect(
        'sid-b',
        emit_fn=lambda name, payload, **kwargs: emitted.append((name, payload, kwargs)),
    )
    assert state.active_player_payloads(3) == []
    assert ('player_left', {'id': 7}, {'room': '3'}) in emitted
    assert ('active_players', [], {'room': '3'}) in emitted


def test_socket_runtime_builds_one_rate_limiter_under_concurrent_first_use(monkeypatch):
    app = Flask(__name__)
    app.config.update(
        AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES=40,
        AIDM_RATE_LIMIT_WINDOW_SECONDS=30,
        AIDM_PREAUTH_RATE_LIMIT_WINDOW_SECONDS=60,
        AIDM_RATE_LIMIT_RETENTION_WINDOW_SECONDS=60,
        AIDM_RATE_LIMIT_STORE='memory',
    )
    runtime = SocketRuntime(SocketState())
    created = []

    def fake_build_rate_limiter(**kwargs):
        time.sleep(0.02)
        limiter = object()
        created.append((kwargs, limiter))
        return limiter

    monkeypatch.setattr('aidm_server.socket_runtime.build_rate_limiter', fake_build_rate_limiter)

    def get_limiter():
        with app.app_context():
            return runtime.rate_limiter()

    with ThreadPoolExecutor(max_workers=8) as executor:
        limiters = list(executor.map(lambda _index: get_limiter(), range(8)))

    assert len(created) == 1
    assert all(limiter is limiters[0] for limiter in limiters)
    assert created[0][0] == {
        'limit': 40,
        'window_seconds': 30,
        'store_name': 'memory',
        'retention_window_seconds': 60,
    }


def test_socket_runtime_propagates_and_enforces_database_retention(monkeypatch):
    app = Flask(__name__)
    app.config.update(
        AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES=40,
        AIDM_RATE_LIMIT_WINDOW_SECONDS=30,
        AIDM_PREAUTH_RATE_LIMIT_WINDOW_SECONDS=60,
        AIDM_RATE_LIMIT_RETENTION_WINDOW_SECONDS=60,
        AIDM_RATE_LIMIT_STORE='database',
    )
    runtime = SocketRuntime(SocketState())
    created = []

    def fake_build_rate_limiter(**kwargs):
        limiter = object()
        created.append((kwargs, limiter))
        return limiter

    monkeypatch.setattr('aidm_server.socket_runtime.build_rate_limiter', fake_build_rate_limiter)

    with app.app_context():
        first = runtime.rate_limiter()
        app.config['AIDM_PREAUTH_RATE_LIMIT_WINDOW_SECONDS'] = 90
        with pytest.raises(ValueError, match='retention is shorter'):
            runtime.rate_limiter()

    assert first is created[0][1]
    assert [kwargs['retention_window_seconds'] for kwargs, _limiter in created] == [60]
    assert all(kwargs['store_name'] == 'database' for kwargs, _limiter in created)
