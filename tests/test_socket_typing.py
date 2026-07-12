from unittest.mock import Mock

from aidm_server.socket_typing import SocketTypingDependencies, register_socket_typing_events
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


def test_typing_registration_owns_only_typing_status():
    registry = _SocketRegistry()
    dependencies = SocketTypingDependencies(
        state=Mock(),
        set_socket_context=Mock(),
        socket_workspace_id=Mock(),
        socket_capability_forbidden=Mock(),
        workspace_session=Mock(),
        set_player_typing=Mock(),
        active_player_payloads=Mock(),
        rate_key=Mock(),
        allow_rate_key=Mock(),
        emit_rate_limited=Mock(),
    )

    register_socket_typing_events(registry, dependencies)

    assert set(registry.handlers) == {'typing_status'}


def test_typing_rejects_mismatched_identity_then_accepts_legacy_typing_alias(
    app,
    socketio,
    app_runtime,
):
    socketio_module = app_runtime['modules']['socketio_events']
    ids = seed_world_campaign_player_session(app)
    client = socketio.test_client(app, flask_test_client=app.test_client())
    client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    client.get_received()

    client.emit(
        'typing_status',
        {'session_id': ids['session_id'], 'player_id': ids['player_id'] + 1, 'typing': True},
    )
    mismatch = _event_payload(client.get_received(), 'error')

    assert mismatch['error_code'] == 'player_identity_mismatch'
    assert socketio_module.socket_state.player_is_typing(ids['session_id'], ids['player_id']) is False

    client.emit(
        'typing_status',
        {'session_id': ids['session_id'], 'player_id': ids['player_id'], 'typing': True},
    )
    roster = _event_payload(client.get_received(), 'active_players')

    player_payload = next(player for player in roster if player['id'] == ids['player_id'])
    assert player_payload['is_typing'] is True
    assert socketio_module.socket_state.player_is_typing(ids['session_id'], ids['player_id']) is True
