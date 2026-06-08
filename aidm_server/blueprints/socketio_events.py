from __future__ import annotations

import logging
import secrets

from flask import current_app, request
from flask_socketio import emit, join_room, leave_room

from aidm_server.llm import CONTEXT_VERSION, query_dm_function_stream
from aidm_server.logging_context import clear_logging_context, set_logging_context
from aidm_server.socket_contracts import socket_error_payload as socket_error, validate_send_message_payload
from aidm_server.socket_runtime import SocketRuntime
from aidm_server.socket_state import SocketState
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.turn_engine import TurnCommand, TurnEngine
from aidm_server.validation import coerce_int
from aidm_server.workspace_access import get_player as workspace_player, get_session as workspace_session


logger = logging.getLogger(__name__)

socket_state = SocketState()
# Compatibility aliases for tests and diagnostics. Runtime code should use socket_state.
active_players = socket_state.active_players
socketio_connections = socket_state.connections
socket_runtime = SocketRuntime(socket_state)


def _set_socket_context(event_name: str, data: dict | None = None, turn_id: int | None = None):
    socket_runtime.set_context(event_name, data, turn_id)


def _socket_rate_limiter():
    return socket_runtime.rate_limiter()


def _socket_auth_required() -> bool:
    return socket_runtime.auth_required()


def _is_socket_authorized(auth_payload: dict | None = None, data_payload: dict | None = None) -> bool:
    return socket_runtime.is_authorized(auth_payload=auth_payload, data_payload=data_payload)


def _socket_workspace_id(auth_payload: dict | None = None, data_payload: dict | None = None) -> str | None:
    return socket_runtime.workspace_id_for_auth(auth_payload=auth_payload, data_payload=data_payload)


def _active_player_payloads(session_id: int) -> list[dict]:
    return socket_runtime.active_player_payloads(session_id)


def _track_active_player(session_id: int, player_data: dict, sid: str) -> bool:
    return socket_runtime.track_active_player(session_id, player_data, sid)


def _release_active_player(session_id: int, player_id: int, sid: str) -> bool:
    return socket_runtime.release_active_player(session_id, player_id, sid)


def _admin_passcode_is_valid(data: dict | None) -> bool:
    configured = str(current_app.config.get('AIDM_ADMIN_PASSCODE') or '').strip()
    supplied = str((data or {}).get('admin_passcode') or '').strip()
    if not configured or not supplied:
        return False
    return secrets.compare_digest(supplied, configured)


def register_socketio_events(socketio):
    def _clear_connection_binding(sid: str, *, leave_bound_room: bool):
        socket_runtime.clear_connection_binding(
            sid,
            leave_bound_room=leave_bound_room,
            leave_room_fn=leave_room,
            emit_fn=emit,
        )

    @socketio.on('connect')
    def handle_connect(auth=None):
        _set_socket_context('connect', auth if isinstance(auth, dict) else None)
        try:
            try:
                sid = getattr(request, 'sid', None)
                remote_addr = getattr(request, 'remote_addr', None)
                workspace_id = _socket_workspace_id(auth_payload=auth)
                authorized = bool(workspace_id)

                if not authorized:
                    logger.warning('Socket auth rejected sid=%s', sid)
                    telemetry_event(
                        'socket.connect.unauthorized',
                        payload={'sid': sid, 'remote_addr': remote_addr},
                        severity='warning',
                    )
                    return False

                if sid:
                    socket_state.set_connection(sid, {
                        'authorized': True,
                        'workspace_id': workspace_id,
                        'session_id': None,
                        'player_id': None,
                        'correlation_id': (socket_state.connection(sid) or {}).get('correlation_id'),
                    })
                telemetry_metric('socket.connect.success_total', 1)
                return None
            except Exception as exc:
                logger.exception('Socket connect handler failed: %s', str(exc))
                telemetry_event(
                    'socket.connect.error',
                    payload={'error': str(exc)},
                    severity='error',
                )
                return False
        finally:
            clear_logging_context()

    @socketio.on('join_session')
    def handle_join_session(data):
        _set_socket_context('join_session', data if isinstance(data, dict) else None)
        try:
            if not isinstance(data, dict):
                emit('error', socket_error('validation_error', 'Expected object payload for join_session.'))
                telemetry_event('socket.join.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            workspace_id = _socket_workspace_id(data_payload=data)
            if not workspace_id:
                emit('error', socket_error('unauthorized', 'Missing or invalid auth token.'))
                telemetry_event('socket.join.unauthorized', payload={'sid': request.sid}, severity='warning')
                return

            session_id = coerce_int(data.get('session_id'))
            player_id = coerce_int(data.get('player_id'))
            set_logging_context(session_id=session_id)

            if not session_id:
                emit('error', socket_error('validation_error', 'Session ID is required to join.'))
                telemetry_event('socket.join.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            session_obj = workspace_session(session_id, workspace_id)
            if not session_obj:
                emit('error', socket_error('session_not_found', 'Session not found.'))
                telemetry_event(
                    'socket.join.session_not_found',
                    payload={'sid': request.sid, 'session_id': session_id},
                    severity='warning',
                )
                return

            player_data = None
            if player_id:
                player = workspace_player(player_id, workspace_id)
                if not player:
                    emit('error', socket_error('invalid_player', 'Invalid player ID.'))
                    telemetry_event(
                        'socket.join.invalid_player',
                        payload={'sid': request.sid, 'session_id': session_id, 'player_id': player_id},
                        severity='warning',
                    )
                    return
                player_data = {
                    'id': player.player_id,
                    'character_name': player.character_name,
                    'name': player.name,
                }

            connection_record = socket_state.ensure_connection(
                request.sid,
                {'authorized': True, 'workspace_id': workspace_id},
            )
            existing_session_id = coerce_int(connection_record.get('session_id'))
            existing_player_id = coerce_int(connection_record.get('player_id'))
            if existing_session_id != session_id or existing_player_id != player_id:
                _clear_connection_binding(request.sid, leave_bound_room=True)

            join_room(str(session_id))
            connection_record['workspace_id'] = workspace_id
            connection_record['session_id'] = session_id
            connection_record['player_id'] = player_id

            socket_state.ensure_session(session_id)

            if player_id:
                if player_data:
                    joined_fresh = _track_active_player(session_id, player_data, request.sid)
                    if joined_fresh:
                        emit('player_joined', player_data, room=str(session_id))
            emit('active_players', _active_player_payloads(session_id), room=str(session_id))
            emit(
                'new_message',
                {
                    'message': f'A new player joined session {session_id}!',
                    'context_version': CONTEXT_VERSION,
                },
                room=str(session_id),
            )
            telemetry_metric('socket.join.success_total', 1)
        finally:
            clear_logging_context()

    @socketio.on('leave_session')
    def handle_leave_session(data):
        _set_socket_context('leave_session', data if isinstance(data, dict) else None)
        try:
            if not isinstance(data, dict):
                emit('error', socket_error('validation_error', 'Expected object payload for leave_session.'))
                telemetry_event('socket.leave.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            session_id = coerce_int(data.get('session_id'))
            player_id = coerce_int(data.get('player_id'))
            set_logging_context(session_id=session_id)

            if not session_id or not player_id:
                emit('error', socket_error('validation_error', 'session_id and player_id are required'))
                telemetry_event('socket.leave.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            connection_record = socket_state.connection(request.sid)
            bound_session_id = coerce_int(connection_record.get('session_id')) if connection_record else None
            bound_player_id = coerce_int(connection_record.get('player_id')) if connection_record else None
            if bound_session_id != session_id or bound_player_id != player_id:
                emit(
                    'error',
                    socket_error(
                        'player_identity_mismatch',
                        'This socket can only leave the session/player binding it joined with.',
                    ),
                )
                telemetry_event(
                    'socket.leave.player_identity_mismatch',
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

            leave_room(str(session_id))

            removed_player = _release_active_player(session_id, player_id, request.sid)
            if removed_player:
                emit('player_left', {'id': player_id}, room=str(session_id))
                emit('active_players', _active_player_payloads(session_id), room=str(session_id))

            if connection_record is not None:
                connection_record['session_id'] = None
                connection_record['player_id'] = None
            telemetry_metric('socket.leave.success_total', 1)
        finally:
            clear_logging_context()

    @socketio.on('disconnect')
    def handle_disconnect():
        _set_socket_context('disconnect')
        try:
            connection_info = socket_runtime.release_disconnect(request.sid, emit_fn=emit)
            if not connection_info:
                return
            telemetry_metric('socket.disconnect_total', 1)
        finally:
            clear_logging_context()

    @socketio.on('send_message')
    def handle_send_message(data):
        _set_socket_context('send_message', data if isinstance(data, dict) else None)
        try:
            telemetry_metric('socket.messages_total', 1)
            workspace_id = _socket_workspace_id(data_payload=data)
            if not workspace_id:
                emit('error', socket_error('unauthorized', 'Missing or invalid auth token.'))
                telemetry_event('socket.send_message.unauthorized', payload={'sid': request.sid}, severity='warning')
                return

            message_payload, contract_error = validate_send_message_payload(data)
            if contract_error:
                emit(
                    'error',
                    socket_error(
                        contract_error.error_code,
                        contract_error.message,
                        contract_error.details,
                    ),
                )
                telemetry_event(
                    f'socket.send_message.{contract_error.telemetry_suffix}',
                    payload={'sid': request.sid, **contract_error.telemetry_payload},
                    severity='warning',
                )
                return
            if message_payload is None:
                emit('error', socket_error('validation_error', 'Invalid message payload types.'))
                telemetry_event('socket.send_message.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            if message_payload.action_intent and message_payload.action_intent.get('kind') == 'admin':
                if not current_app.config.get('AIDM_ADMIN_PASSCODE'):
                    emit('error', socket_error('admin_not_configured', 'Admin mode is not configured on this backend.'))
                    telemetry_event(
                        'socket.send_message.admin_not_configured',
                        payload={'sid': request.sid},
                        severity='warning',
                    )
                    return
                if not _admin_passcode_is_valid(data):
                    emit('error', socket_error('admin_unauthorized', 'Invalid admin passcode.'))
                    telemetry_event(
                        'socket.send_message.admin_unauthorized',
                        payload={'sid': request.sid},
                        severity='warning',
                    )
                    return

            session_id = message_payload.session_id
            campaign_id = message_payload.campaign_id
            player_id = message_payload.player_id
            set_logging_context(session_id=session_id)

            connection_record = socket_state.connection(request.sid)
            bound_session_id = coerce_int(connection_record.get('session_id')) if connection_record else None
            bound_player_id = coerce_int(connection_record.get('player_id')) if connection_record else None
            if bound_session_id != session_id or bound_player_id != player_id:
                emit(
                    'error',
                    socket_error(
                        'player_identity_mismatch',
                        'This socket can only submit turns for the player and session it joined with.',
                        {
                            'bound_session_id': bound_session_id,
                            'bound_player_id': bound_player_id,
                        },
                    ),
                )
                telemetry_event(
                    'socket.send_message.player_identity_mismatch',
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

            session_obj = workspace_session(session_id, workspace_id)
            if not session_obj or session_obj.campaign_id != campaign_id:
                emit('error', socket_error('session_not_found', 'Session not found.'))
                telemetry_event(
                    'socket.send_message.session_not_found',
                    payload={'sid': request.sid, 'session_id': session_id, 'campaign_id': campaign_id},
                    severity='warning',
                )
                return

            player = workspace_player(player_id, workspace_id)
            if not player:
                emit('error', socket_error('invalid_player', 'Invalid player ID'))
                telemetry_event(
                    'socket.send_message.invalid_player',
                    payload={'sid': request.sid, 'player_id': player_id, 'campaign_id': campaign_id},
                    severity='warning',
                )
                return

            rate_key = f"{request.sid}:{session_id}"
            limit_result = _socket_rate_limiter().allow(rate_key)
            if not limit_result.allowed:
                emit(
                    'error',
                    socket_error(
                        'rate_limited',
                        'Too many socket messages; please wait before sending more.',
                        {'reset_in_seconds': limit_result.reset_in_seconds},
                    ),
                )
                telemetry_event(
                    'socket.send_message.rate_limited',
                    payload={'sid': request.sid, 'session_id': session_id, 'reset_in_seconds': limit_result.reset_in_seconds},
                    severity='warning',
                )
                return

            engine = TurnEngine(
                socketio=socketio,
                emit_fn=emit,
                stream_fn=query_dm_function_stream,
                active_player_ids_fn=lambda session_id: [
                    int(player['id']) for player in _active_player_payloads(session_id) if player.get('id')
                ],
            )
            engine.process(
                TurnCommand(
                    sid=request.sid,
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
        finally:
            clear_logging_context()
