"""Socket.IO player-message preflight policy and event registration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from flask import request
from flask_socketio import emit

from aidm_server.logging_context import clear_logging_context, set_logging_context
from aidm_server.rate_limiter import RateLimitResult
from aidm_server.services.session_lifecycle import session_playability_error
from aidm_server.socket_access import AdminSocketAuthorization
from aidm_server.socket_contracts import (
    SendMessagePayload,
    SocketContractError,
    socket_error_payload as socket_error,
)
from aidm_server.socket_state import SocketState
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.turn_engine import TurnCommand
from aidm_server.validation import coerce_int


@dataclass(frozen=True)
class SocketMessageDependencies:
    state: SocketState
    set_socket_context: Callable[..., None]
    socket_workspace_id: Callable[..., str | None]
    socket_capability_forbidden: Callable[[str], bool]
    validate_payload: Callable[[Any], tuple[SendMessagePayload | None, SocketContractError | None]]
    set_player_typing: Callable[[int, int, str, bool], bool]
    emit_active_players: Callable[[int], None]
    workspace_session: Callable[[int, str], Any]
    workspace_player: Callable[[int, str], Any]
    rate_key: Callable[[str, int, int], str]
    allow_rate_key: Callable[[str], RateLimitResult]
    emit_rate_limited: Callable[[str, int, int], None]
    configured_admin_passcode: Callable[[], str | None]
    authorize_admin_action: Callable[..., AdminSocketAuthorization]
    passcode_validator: Callable[[dict | None], bool]
    process_turn: Callable[[TurnCommand], Any]


@dataclass(frozen=True)
class SocketMessageFailure:
    error_code: str
    message: str
    telemetry_suffix: str
    details: dict[str, Any] | None = None
    telemetry_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SocketMessageRateLimit:
    telemetry_prefix: str
    session_id: int
    reset_in_seconds: int


@dataclass(frozen=True)
class SocketMessageDispatch:
    command: TurnCommand


def normalize_socket_message(
    data: Any,
    dependencies: SocketMessageDependencies,
) -> SendMessagePayload | SocketMessageFailure:
    """Normalize the public message contract while preserving its error metadata."""

    message_payload, contract_error = dependencies.validate_payload(data)
    if contract_error:
        return SocketMessageFailure(
            error_code=contract_error.error_code,
            message=contract_error.message,
            details=contract_error.details,
            telemetry_suffix=contract_error.telemetry_suffix,
            telemetry_payload=contract_error.telemetry_payload,
        )
    if message_payload is None:
        return SocketMessageFailure(
            error_code='validation_error',
            message='Invalid message payload types.',
            telemetry_suffix='validation_error',
        )
    return message_payload


def prepare_socket_message(
    message_payload: SendMessagePayload,
    *,
    raw_data: dict,
    sid: str,
    workspace_id: str,
    remote_address: str,
    dependencies: SocketMessageDependencies,
) -> SocketMessageDispatch | SocketMessageFailure | SocketMessageRateLimit:
    """Apply connection, workspace, campaign, rate, and admin policy before dispatch."""

    session_id = message_payload.session_id
    campaign_id = message_payload.campaign_id
    player_id = message_payload.player_id

    connection_record = dependencies.state.connection(sid)
    bound_session_id = coerce_int(connection_record.get('session_id')) if connection_record else None
    bound_player_id = coerce_int(connection_record.get('player_id')) if connection_record else None
    if bound_session_id != session_id or bound_player_id != player_id:
        return SocketMessageFailure(
            error_code='player_identity_mismatch',
            message='This socket can only submit turns for the player and session it joined with.',
            details={
                'bound_session_id': bound_session_id,
                'bound_player_id': bound_player_id,
            },
            telemetry_suffix='player_identity_mismatch',
            telemetry_payload={
                'session_id': session_id,
                'player_id': player_id,
                'bound_session_id': bound_session_id,
                'bound_player_id': bound_player_id,
            },
        )

    session_obj = dependencies.workspace_session(session_id, workspace_id)
    if not session_obj or session_obj.campaign_id != campaign_id:
        return SocketMessageFailure(
            error_code='session_not_found',
            message='Session not found.',
            telemetry_suffix='session_not_found',
            telemetry_payload={'session_id': session_id, 'campaign_id': campaign_id},
        )

    playability_error = session_playability_error(session_obj)
    if playability_error:
        error_code, message = playability_error
        return SocketMessageFailure(
            error_code=error_code,
            message=message,
            telemetry_suffix=error_code,
            telemetry_payload={'session_id': session_id, 'campaign_id': campaign_id},
        )

    if dependencies.set_player_typing(session_id, player_id, sid, False):
        dependencies.emit_active_players(session_id)

    player = dependencies.workspace_player(player_id, workspace_id)
    if not player:
        return SocketMessageFailure(
            error_code='invalid_player',
            message='Invalid player ID',
            telemetry_suffix='invalid_player',
            telemetry_payload={'player_id': player_id, 'campaign_id': campaign_id},
        )
    if player.campaign_id != campaign_id:
        return SocketMessageFailure(
            error_code='campaign_mismatch',
            message='Player does not belong to this campaign.',
            telemetry_suffix='campaign_mismatch',
            telemetry_payload={'player_id': player_id, 'campaign_id': campaign_id},
        )

    limit_result = dependencies.allow_rate_key(
        dependencies.rate_key(workspace_id, session_id, player_id)
    )
    if not limit_result.allowed:
        return SocketMessageRateLimit(
            telemetry_prefix='socket.send_message',
            session_id=session_id,
            reset_in_seconds=limit_result.reset_in_seconds,
        )

    if message_payload.action_intent and message_payload.action_intent.get('kind') == 'admin':
        admin_authorization = dependencies.authorize_admin_action(
            configured_passcode=dependencies.configured_admin_passcode(),
            data=raw_data,
            workspace_id=workspace_id,
            remote_address=remote_address,
            allow_rate_key=dependencies.allow_rate_key,
            passcode_validator=dependencies.passcode_validator,
        )
        if admin_authorization.error_code == 'rate_limited':
            return SocketMessageRateLimit(
                telemetry_prefix='socket.send_message.admin_passcode',
                session_id=session_id,
                reset_in_seconds=admin_authorization.reset_in_seconds or 0,
            )
        if not admin_authorization.allowed:
            error_code = admin_authorization.error_code or 'admin_unauthorized'
            return SocketMessageFailure(
                error_code=error_code,
                message=admin_authorization.message or 'Invalid admin passcode.',
                telemetry_suffix=error_code,
            )

    return SocketMessageDispatch(
        TurnCommand(
            sid=sid,
            session_id=session_id,
            campaign_id=campaign_id,
            world_id=message_payload.world_id,
            player_id=player_id,
            user_input=message_payload.user_input,
            manual_segment_ids=message_payload.manual_segment_ids,
            action_intent=message_payload.action_intent,
            client_message_id=message_payload.client_message_id,
        )
    )


def register_socket_message_events(
    socketio,
    dependencies: SocketMessageDependencies,
) -> None:
    @socketio.on('send_message')
    def handle_send_message(data):
        dependencies.set_socket_context('send_message', data if isinstance(data, dict) else None)
        try:
            telemetry_metric('socket.messages_total', 1)
            workspace_id = dependencies.socket_workspace_id(data_payload=data)
            if not workspace_id:
                emit('error', socket_error('unauthorized', 'Missing or invalid workspace token.'))
                telemetry_event(
                    'socket.send_message.unauthorized',
                    payload={'sid': request.sid},
                    severity='warning',
                )
                return

            if dependencies.socket_capability_forbidden('send_message'):
                return

            normalized = normalize_socket_message(data, dependencies)
            if isinstance(normalized, SocketMessageFailure):
                emit('error', socket_error(normalized.error_code, normalized.message, normalized.details))
                telemetry_event(
                    f'socket.send_message.{normalized.telemetry_suffix}',
                    payload={'sid': request.sid, **normalized.telemetry_payload},
                    severity='warning',
                )
                return

            set_logging_context(session_id=normalized.session_id)
            outcome = prepare_socket_message(
                normalized,
                raw_data=data,
                sid=request.sid,
                workspace_id=workspace_id,
                remote_address=request.remote_addr or request.environ.get('REMOTE_ADDR') or 'unknown',
                dependencies=dependencies,
            )
            if isinstance(outcome, SocketMessageRateLimit):
                dependencies.emit_rate_limited(
                    outcome.telemetry_prefix,
                    outcome.session_id,
                    outcome.reset_in_seconds,
                )
                return
            if isinstance(outcome, SocketMessageFailure):
                emit('error', socket_error(outcome.error_code, outcome.message, outcome.details))
                telemetry_event(
                    f'socket.send_message.{outcome.telemetry_suffix}',
                    payload={'sid': request.sid, **outcome.telemetry_payload},
                    severity='warning',
                )
                return

            dependencies.process_turn(outcome.command)
        finally:
            clear_logging_context()
