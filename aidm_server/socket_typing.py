"""Socket.IO typing-presence event registration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from flask import request
from flask_socketio import emit

from aidm_server.logging_context import clear_logging_context, set_logging_context
from aidm_server.rate_limiter import RateLimitResult
from aidm_server.socket_contracts import socket_error_payload as socket_error
from aidm_server.socket_state import SocketState
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.validation import coerce_int


@dataclass(frozen=True)
class SocketTypingDependencies:
    state: SocketState
    set_socket_context: Callable[..., None]
    socket_workspace_id: Callable[..., str | None]
    socket_capability_forbidden: Callable[[str], bool]
    set_player_typing: Callable[[int, int, str, bool], bool]
    active_player_payloads: Callable[[int], list[dict]]
    rate_key: Callable[[str, int, int], str]
    allow_rate_key: Callable[[str], RateLimitResult]
    emit_rate_limited: Callable[[str, int, int], None]


def register_socket_typing_events(socketio, dependencies: SocketTypingDependencies) -> None:
    @socketio.on('typing_status')
    def handle_typing_status(data):
        dependencies.set_socket_context('typing_status', data if isinstance(data, dict) else None)
        try:
            if not isinstance(data, dict):
                emit('error', socket_error('validation_error', 'Expected object payload for typing_status.'))
                telemetry_event('socket.typing.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            workspace_id = dependencies.socket_workspace_id(data_payload=data)
            if not workspace_id:
                emit('error', socket_error('unauthorized', 'Missing or invalid workspace token.'))
                telemetry_event('socket.typing.unauthorized', payload={'sid': request.sid}, severity='warning')
                return

            if dependencies.socket_capability_forbidden('typing_status'):
                return

            session_id = coerce_int(data.get('session_id'))
            player_id = coerce_int(data.get('player_id'))
            set_logging_context(session_id=session_id)
            if not session_id or not player_id:
                emit('error', socket_error('validation_error', 'session_id and player_id are required'))
                telemetry_event('socket.typing.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            connection_record = dependencies.state.connection(request.sid)
            bound_session_id = coerce_int(connection_record.get('session_id')) if connection_record else None
            bound_player_id = coerce_int(connection_record.get('player_id')) if connection_record else None
            if bound_session_id != session_id or bound_player_id != player_id:
                emit(
                    'error',
                    socket_error(
                        'player_identity_mismatch',
                        'This socket can only update typing for the player and session it joined with.',
                    ),
                )
                telemetry_event(
                    'socket.typing.player_identity_mismatch',
                    payload={
                        'sid': request.sid,
                        'session_id': session_id,
                        'player_id': player_id,
                        'bound_session_id': bound_session_id,
                        'bound_player_id': bound_player_id,
                    },
                    severity='warning',
                )
                return

            is_typing = data.get('is_typing') is True or data.get('typing') is True
            typing_state_changed = dependencies.set_player_typing(session_id, player_id, request.sid, is_typing)
            if not typing_state_changed:
                telemetry_metric('socket.typing_status_total', 1)
                return

            limit_result = dependencies.allow_rate_key(
                dependencies.rate_key(workspace_id, session_id, player_id)
            )
            if not limit_result.allowed:
                if is_typing:
                    dependencies.set_player_typing(session_id, player_id, request.sid, False)
                else:
                    emit('active_players', dependencies.active_player_payloads(session_id), room=str(session_id))
                dependencies.emit_rate_limited('socket.typing', session_id, limit_result.reset_in_seconds)
                return

            emit('active_players', dependencies.active_player_payloads(session_id), room=str(session_id))
            telemetry_metric('socket.typing_status_total', 1)
        finally:
            clear_logging_context()
