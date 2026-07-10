from types import SimpleNamespace
from unittest.mock import Mock

import aidm_server.socket_turn_control as socket_turn_control_module
from aidm_server.socket_state import SocketState
from aidm_server.socket_turn_control import (
    SocketTurnControlDependencies,
    TurnControlFailure,
    TurnControlRequest,
    TurnControlUpdate,
    apply_turn_control_update,
    normalize_turn_control_request,
    register_socket_turn_control_events,
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


def _dependencies(**overrides):
    defaults = {
        'state': SocketState(),
        'logger': Mock(),
        'set_socket_context': Mock(),
        'socket_workspace_id': Mock(),
        'socket_capability_forbidden': Mock(),
        'workspace_session': Mock(),
        'workspace_player': Mock(),
        'set_turn_control': Mock(),
        'turn_control_payload': Mock(),
        'commit': Mock(),
        'rollback': Mock(),
    }
    defaults.update(overrides)
    return SocketTurnControlDependencies(**defaults)


def _event_payload(received, name):
    for event in received:
        if event['name'] == name:
            return event['args'][0] if event['args'] else {}
    return None


def test_turn_control_registration_owns_only_turn_control_event():
    registry = _SocketRegistry()

    register_socket_turn_control_events(registry, _dependencies())

    assert set(registry.handlers) == {'set_turn_control'}


def test_turn_control_request_normalizes_transport_aliases_and_defaults():
    assert normalize_turn_control_request(
        {
            'sessionId': '7',
            'playerId': '11',
            'mode': ' STRUCTURED ',
            'source': ' ADMIN ',
            'activePlayerId': '13',
        }
    ) == TurnControlRequest(
        session_id=7,
        player_id=11,
        mode='structured',
        source='admin',
        active_player_id=13,
    )
    assert normalize_turn_control_request({'session_id': 7, 'player_id': 11}) == TurnControlRequest(
        session_id=7,
        player_id=11,
        mode='free',
        source='manual',
        active_player_id=None,
    )


def test_turn_control_policy_rejects_unbound_identity_before_workspace_access():
    state = SocketState()
    state.set_connection('socket-1', {'session_id': 7, 'player_id': 11})
    workspace_session = Mock()
    workspace_player = Mock()
    commit = Mock()
    dependencies = _dependencies(
        state=state,
        workspace_session=workspace_session,
        workspace_player=workspace_player,
        commit=commit,
    )

    outcome = apply_turn_control_update(
        TurnControlRequest(7, 12, 'structured', 'manual', 12),
        sid='socket-1',
        workspace_id='owner',
        dependencies=dependencies,
    )

    assert isinstance(outcome, TurnControlFailure)
    assert outcome.error_code == 'player_identity_mismatch'
    assert outcome.telemetry_payload == {
        'session_id': 7,
        'player_id': 12,
        'bound_session_id': 7,
        'bound_player_id': 11,
    }
    workspace_session.assert_not_called()
    workspace_player.assert_not_called()
    commit.assert_not_called()


def test_turn_control_policy_persists_one_authorized_update_and_builds_room_payload():
    state = SocketState()
    state.set_connection('socket-1', {'session_id': 7, 'player_id': 11})
    session = SimpleNamespace(campaign_id=5)
    player = SimpleNamespace(campaign_id=5)
    set_turn_control = Mock(return_value={'mode': 'spotlight', 'activePlayerId': 11})
    turn_control_payload = Mock(return_value={'session_id': 7, 'turn_control': {'mode': 'spotlight'}})
    commit = Mock()
    dependencies = _dependencies(
        state=state,
        workspace_session=Mock(return_value=session),
        workspace_player=Mock(return_value=player),
        set_turn_control=set_turn_control,
        turn_control_payload=turn_control_payload,
        commit=commit,
    )

    outcome = apply_turn_control_update(
        TurnControlRequest(7, 11, 'spotlight', 'auto', None),
        sid='socket-1',
        workspace_id='owner',
        dependencies=dependencies,
    )

    assert outcome == TurnControlUpdate(
        session_id=7,
        payload={'session_id': 7, 'turn_control': {'mode': 'spotlight'}},
    )
    set_turn_control.assert_called_once_with(
        session,
        mode='spotlight',
        active_player_id=11,
        updated_by_player_id=11,
        source='auto',
    )
    commit.assert_called_once_with()
    turn_control_payload.assert_called_once_with(7, {'mode': 'spotlight', 'activePlayerId': 11})


def test_turn_control_event_keeps_camel_case_contract_and_validation_errors(app, socketio):
    ids = seed_world_campaign_player_session(app)
    client = socketio.test_client(app, flask_test_client=app.test_client())
    client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    client.get_received()

    client.emit(
        'set_turn_control',
        {
            'sessionId': ids['session_id'],
            'playerId': ids['player_id'],
            'mode': 'spotlight',
            'source': 'auto',
        },
    )
    updated = _event_payload(client.get_received(), 'turn_control_updated')

    assert updated['turn_control']['mode'] == 'spotlight'
    assert updated['turn_control']['source'] == 'auto'
    assert updated['turn_control']['activePlayerId'] == ids['player_id']

    client.emit(
        'set_turn_control',
        {
            'session_id': ids['session_id'],
            'player_id': ids['player_id'],
            'mode': 'initiative',
        },
    )
    error = _event_payload(client.get_received(), 'error')

    assert error['error'] == 'Turn mode must be free, spotlight, or structured.'
    assert error['error_code'] == 'validation_error'


def test_turn_control_event_rolls_back_and_emits_public_error_when_commit_fails(monkeypatch):
    registry = _SocketRegistry()
    state = SocketState()
    state.set_connection('socket-1', {'session_id': 7, 'player_id': 11})
    rollback = Mock()
    logger = Mock()
    emitted = Mock()
    dependencies = _dependencies(
        state=state,
        logger=logger,
        socket_workspace_id=Mock(return_value='owner'),
        socket_capability_forbidden=Mock(return_value=False),
        workspace_session=Mock(return_value=SimpleNamespace(campaign_id=5)),
        workspace_player=Mock(return_value=SimpleNamespace(campaign_id=5)),
        set_turn_control=Mock(return_value={'mode': 'spotlight', 'activePlayerId': 11}),
        turn_control_payload=Mock(return_value={'session_id': 7}),
        commit=Mock(side_effect=RuntimeError('database unavailable')),
        rollback=rollback,
    )
    monkeypatch.setattr(socket_turn_control_module, 'request', SimpleNamespace(sid='socket-1'))
    monkeypatch.setattr(socket_turn_control_module, 'emit', emitted)
    monkeypatch.setattr(socket_turn_control_module, 'telemetry_event', Mock())
    monkeypatch.setattr(socket_turn_control_module, 'telemetry_metric', Mock())
    register_socket_turn_control_events(registry, dependencies)

    registry.handlers['set_turn_control'](
        {
            'session_id': 7,
            'player_id': 11,
            'mode': 'spotlight',
            'source': 'manual',
        }
    )

    rollback.assert_called_once_with()
    logger.exception.assert_called_once()
    emitted.assert_called_once_with(
        'error',
        {
            'error': 'Failed to update turn control.',
            'error_code': 'server_error',
            'details': {},
        },
    )
