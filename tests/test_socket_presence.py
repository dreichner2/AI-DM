from types import SimpleNamespace
from unittest.mock import Mock

from aidm_server.socket_presence import (
    SocketPresenceDependencies,
    connection_binding_ids,
    player_presence_payload,
    register_socket_presence_events,
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


def test_presence_registration_owns_only_lifecycle_events():
    registry = _SocketRegistry()
    dependencies = SocketPresenceDependencies(
        runtime=Mock(),
        state=Mock(),
        logger=Mock(),
        set_socket_context=Mock(),
        socket_workspace_id=Mock(),
        socket_capability_forbidden=Mock(),
        workspace_session=Mock(),
        workspace_player=Mock(),
        active_player_payloads=Mock(),
        track_active_player=Mock(),
        release_active_player=Mock(),
        player_is_typing=Mock(),
        music_state_for_emit=Mock(),
    )

    register_socket_presence_events(registry, dependencies)

    assert set(registry.handlers) == {'connect', 'join_session', 'leave_session', 'disconnect'}


def test_presence_helpers_normalize_bindings_and_keep_legacy_class_alias():
    assert connection_binding_ids(None) == (None, None)
    assert connection_binding_ids({'session_id': '7', 'player_id': '11'}) == (7, 11)

    payload = player_presence_payload(
        SimpleNamespace(
            player_id=11,
            character_name='Seraphina',
            name='Alice',
            race='Elf',
            sex='male',
            class_='Ranger',
        )
    )

    assert payload['id'] == 11
    assert payload['class_'] == 'Ranger'
    assert payload['char_class'] == 'Ranger'
    assert payload['profile_image'].endswith('elf_male.png')


def test_leave_session_rejects_mismatched_binding_then_clears_valid_presence(
    app,
    socketio,
    app_runtime,
):
    socketio_module = app_runtime['modules']['socketio_events']
    ids = seed_world_campaign_player_session(app)
    client = socketio.test_client(app, flask_test_client=app.test_client())
    client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    client.get_received()

    connection_record = next(
        record
        for record in socketio_module.socketio_connections.values()
        if record.get('player_id') == ids['player_id']
    )
    assert ids['player_id'] in socketio_module.active_players[ids['session_id']]

    client.emit(
        'leave_session',
        {'session_id': ids['session_id'], 'player_id': ids['player_id'] + 1},
    )
    mismatch = _event_payload(client.get_received(), 'error')

    assert mismatch['error_code'] == 'player_identity_mismatch'
    assert connection_binding_ids(connection_record) == (ids['session_id'], ids['player_id'])
    assert ids['player_id'] in socketio_module.active_players[ids['session_id']]

    client.emit(
        'leave_session',
        {'session_id': ids['session_id'], 'player_id': ids['player_id']},
    )

    assert connection_binding_ids(connection_record) == (None, None)
    assert ids['session_id'] not in socketio_module.active_players or (
        ids['player_id'] not in socketio_module.active_players[ids['session_id']]
    )
