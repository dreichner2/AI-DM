from types import SimpleNamespace
from unittest.mock import Mock

from aidm_server.rate_limiter import RateLimitResult
from aidm_server.socket_access import AdminSocketAuthorization
from aidm_server.socket_contracts import SendMessagePayload, SocketContractError
from aidm_server.socket_message import (
    SocketMessageDependencies,
    SocketMessageDispatch,
    SocketMessageFailure,
    SocketMessageRateLimit,
    normalize_socket_message,
    prepare_socket_message,
    register_socket_message_events,
)
from aidm_server.socket_state import SocketState


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
        'set_socket_context': Mock(),
        'socket_workspace_id': Mock(),
        'socket_capability_forbidden': Mock(),
        'validate_payload': Mock(),
        'set_player_typing': Mock(return_value=False),
        'emit_active_players': Mock(),
        'workspace_session': Mock(),
        'workspace_player': Mock(),
        'rate_key': Mock(return_value='message-bucket'),
        'allow_rate_key': Mock(return_value=RateLimitResult(True, 9, 30)),
        'emit_rate_limited': Mock(),
        'configured_admin_passcode': Mock(return_value=None),
        'authorize_admin_action': Mock(),
        'passcode_validator': Mock(),
        'process_turn': Mock(),
    }
    defaults.update(overrides)
    return SocketMessageDependencies(**defaults)


def _message_payload(*, action_intent=None):
    return SendMessagePayload(
        session_id=7,
        campaign_id=5,
        world_id=3,
        player_id=11,
        user_input='I search the ruined gate.',
        manual_segment_ids=set(),
        action_intent=action_intent,
        client_message_id='message-1',
    )


def test_socket_message_registration_owns_only_send_message_event():
    registry = _SocketRegistry()

    register_socket_message_events(registry, _dependencies())

    assert set(registry.handlers) == {'send_message'}


def test_socket_message_normalization_preserves_contract_error_metadata():
    contract_error = SocketContractError(
        error_code='validation_error',
        message='Malformed action.',
        details={'field': 'action_intent'},
        telemetry_suffix='invalid_action',
        telemetry_payload={'field': 'action_intent', 'reason': 'malformed'},
    )
    dependencies = _dependencies(validate_payload=Mock(return_value=(None, contract_error)))

    outcome = normalize_socket_message({'action_intent': 'bad'}, dependencies)

    assert outcome == SocketMessageFailure(
        error_code='validation_error',
        message='Malformed action.',
        details={'field': 'action_intent'},
        telemetry_suffix='invalid_action',
        telemetry_payload={'field': 'action_intent', 'reason': 'malformed'},
    )


def test_socket_message_preflight_rejects_unbound_identity_before_side_effects():
    state = SocketState()
    state.set_connection('socket-1', {'session_id': 7, 'player_id': 12})
    set_player_typing = Mock()
    workspace_session = Mock()
    allow_rate_key = Mock()
    dependencies = _dependencies(
        state=state,
        set_player_typing=set_player_typing,
        workspace_session=workspace_session,
        allow_rate_key=allow_rate_key,
    )

    outcome = prepare_socket_message(
        _message_payload(),
        raw_data={},
        sid='socket-1',
        workspace_id='owner',
        remote_address='127.0.0.1',
        dependencies=dependencies,
    )

    assert isinstance(outcome, SocketMessageFailure)
    assert outcome.error_code == 'player_identity_mismatch'
    assert outcome.details == {'bound_session_id': 7, 'bound_player_id': 12}
    set_player_typing.assert_not_called()
    workspace_session.assert_not_called()
    allow_rate_key.assert_not_called()


def test_socket_message_preflight_clears_typing_and_builds_authorized_turn_command():
    state = SocketState()
    state.set_connection('socket-1', {'session_id': 7, 'player_id': 11})
    set_player_typing = Mock(return_value=True)
    emit_active_players = Mock()
    rate_key = Mock(return_value='owner:7:11')
    allow_rate_key = Mock(return_value=RateLimitResult(True, 9, 30))
    authorize_admin_action = Mock()
    dependencies = _dependencies(
        state=state,
        set_player_typing=set_player_typing,
        emit_active_players=emit_active_players,
        workspace_session=Mock(return_value=SimpleNamespace(campaign_id=5)),
        workspace_player=Mock(return_value=SimpleNamespace(campaign_id=5)),
        rate_key=rate_key,
        allow_rate_key=allow_rate_key,
        authorize_admin_action=authorize_admin_action,
    )

    outcome = prepare_socket_message(
        _message_payload(),
        raw_data={},
        sid='socket-1',
        workspace_id='owner',
        remote_address='127.0.0.1',
        dependencies=dependencies,
    )

    assert isinstance(outcome, SocketMessageDispatch)
    assert outcome.command.sid == 'socket-1'
    assert outcome.command.session_id == 7
    assert outcome.command.campaign_id == 5
    assert outcome.command.world_id == 3
    assert outcome.command.player_id == 11
    assert outcome.command.user_input == 'I search the ruined gate.'
    assert outcome.command.client_message_id == 'message-1'
    set_player_typing.assert_called_once_with(7, 11, 'socket-1', False)
    emit_active_players.assert_called_once_with(7)
    rate_key.assert_called_once_with('owner', 7, 11)
    allow_rate_key.assert_called_once_with('owner:7:11')
    authorize_admin_action.assert_not_called()


def test_socket_message_preflight_preserves_admin_rate_limit_boundary():
    state = SocketState()
    state.set_connection('socket-1', {'session_id': 7, 'player_id': 11})
    passcode_validator = Mock(return_value=False)
    authorize_admin_action = Mock(
        return_value=AdminSocketAuthorization(
            allowed=False,
            error_code='rate_limited',
            reset_in_seconds=17,
        )
    )
    dependencies = _dependencies(
        state=state,
        workspace_session=Mock(return_value=SimpleNamespace(campaign_id=5)),
        workspace_player=Mock(return_value=SimpleNamespace(campaign_id=5)),
        configured_admin_passcode=Mock(return_value='letmein'),
        authorize_admin_action=authorize_admin_action,
        passcode_validator=passcode_validator,
    )
    action_intent = {'kind': 'admin', 'text': 'Open the vault.'}
    raw_data = {'admin_passcode': 'wrong'}

    outcome = prepare_socket_message(
        _message_payload(action_intent=action_intent),
        raw_data=raw_data,
        sid='socket-1',
        workspace_id='owner',
        remote_address='127.0.0.1',
        dependencies=dependencies,
    )

    assert outcome == SocketMessageRateLimit(
        telemetry_prefix='socket.send_message.admin_passcode',
        session_id=7,
        reset_in_seconds=17,
    )
    authorize_admin_action.assert_called_once_with(
        configured_passcode='letmein',
        data=raw_data,
        workspace_id='owner',
        remote_address='127.0.0.1',
        allow_rate_key=dependencies.allow_rate_key,
        passcode_validator=passcode_validator,
    )
