from unittest.mock import Mock

from aidm_server.socket_music import (
    MUSIC_MAX_POSITION_SECONDS,
    SocketMusicDependencies,
    coerce_music_position,
    music_state_for_emit,
    register_socket_music_events,
)
from tests.helpers import seed_world_campaign_player_session


class _SocketRegistry:
    def __init__(self):
        self.handlers = {}

    def on(self, event_name):
        def register(handler):
            self.handlers[event_name] = handler
            return handler

        return register


def _event_payload(received, name):
    for event in received:
        if event['name'] == name:
            return event['args'][0] if event['args'] else {}
    return None


def test_music_registration_owns_only_music_control():
    registry = _SocketRegistry()
    dependencies = SocketMusicDependencies(
        state=Mock(),
        set_socket_context=Mock(),
        socket_workspace_id=Mock(),
        socket_capability_forbidden=Mock(),
        workspace_session=Mock(),
        workspace_player=Mock(),
        track_id_is_valid=Mock(),
        now_ms=Mock(),
        coerce_position=Mock(),
        state_for_emit=Mock(),
    )

    register_socket_music_events(registry, dependencies)

    assert set(registry.handlers) == {'music_control'}


def test_music_position_and_emit_state_normalization_are_deterministic():
    assert coerce_music_position('12.5') == 12.5
    assert coerce_music_position(-5) == 0.0
    assert coerce_music_position(MUSIC_MAX_POSITION_SECONDS + 1) == MUSIC_MAX_POSITION_SECONDS
    assert coerce_music_position(float('nan')) is None
    assert coerce_music_position(float('inf')) is None
    assert coerce_music_position('not-a-position') is None

    playing = music_state_for_emit(
        {
            'session_id': '7',
            'track_id': 'forest-road',
            'status': 'PLAYING',
            'position': 12.5,
            'updated_at_ms': 1_000,
            'updated_by_player_id': '11',
        },
        now_ms=3_500,
    )
    paused = music_state_for_emit(
        {
            'session_id': 7,
            'track_id': 'forest-road',
            'status': 'paused',
            'position': 12.5,
            'updated_at_ms': 1_000,
            'updated_by_player_id': 11,
        },
        now_ms=3_500,
    )

    assert playing == {
        'session_id': 7,
        'track_id': 'forest-road',
        'status': 'playing',
        'position': 15.0,
        'updated_at_ms': 3_500,
        'updated_by_player_id': 11,
    }
    assert paused['position'] == 12.5
    assert paused['updated_at_ms'] == 1_000


def test_music_control_rejects_invalid_track_and_mismatched_player_without_storing_state(
    app,
    socketio,
    app_runtime,
):
    socketio_module = app_runtime['modules']['socketio_events']
    ids = seed_world_campaign_player_session(app)
    client = socketio.test_client(app, flask_test_client=app.test_client())
    client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    client.get_received()

    base_payload = {
        'session_id': ids['session_id'],
        'player_id': ids['player_id'],
        'track_id': 'forest-road',
        'status': 'playing',
        'position': 12.5,
    }
    client.emit('music_control', {**base_payload, 'track_id': '../forest-road'})
    invalid_track = _event_payload(client.get_received(), 'error')

    assert invalid_track['error_code'] == 'validation_error'
    assert socketio_module.socket_state.music_state(ids['session_id']) is None

    client.emit('music_control', {**base_payload, 'player_id': ids['player_id'] + 1})
    identity_mismatch = _event_payload(client.get_received(), 'error')

    assert identity_mismatch['error_code'] == 'player_identity_mismatch'
    assert socketio_module.socket_state.music_state(ids['session_id']) is None
