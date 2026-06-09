from __future__ import annotations

import logging
import secrets

from flask import current_app, request
from flask_socketio import emit, join_room, leave_room

from aidm_server.llm import CONTEXT_VERSION, query_dm_function_stream
from aidm_server.logging_context import clear_logging_context, set_logging_context
from aidm_server.database import db
from aidm_server.models import DmTurn, safe_json_loads
from aidm_server.profile_icons import profile_icon_src_for_character
from aidm_server.socket_contracts import socket_error_payload as socket_error, validate_send_message_payload
from aidm_server.socket_runtime import SocketRuntime
from aidm_server.socket_state import SocketState
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.turn_control import (
    set_session_turn_control,
    turn_control_from_session,
    turn_control_update_payload,
    turn_submission_result,
)
from aidm_server.turn_engine import TurnCommand, TurnEngine
from aidm_server.turn_rules import latest_pending_turn
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


def _player_is_typing(session_id: int, player_id: int) -> bool:
    return socket_runtime.player_is_typing(session_id, player_id)


def _set_player_typing(session_id: int, player_id: int, sid: str, is_typing: bool) -> bool:
    return socket_runtime.set_player_typing(session_id, player_id, sid, is_typing)


def _socket_account_context() -> tuple[int | None, bool]:
    return socket_runtime.connection_account_context(request.sid)


def _workspace_player_for_socket(player_id: int, workspace_id: str):
    account_id, is_admin = _socket_account_context()
    return workspace_player(player_id, workspace_id, account_id=account_id, is_admin=is_admin)


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

                account = socket_runtime.account_for_auth(auth_payload=auth)
                membership = socket_runtime.membership_for_auth(auth_payload=auth, workspace_id=workspace_id) if account else None
                if membership:
                    db.session.commit()

                if sid:
                    socket_state.set_connection(sid, {
                        'authorized': True,
                        'workspace_id': workspace_id,
                        'account_id': account.account_id if account else None,
                        'workspace_role': membership.role if membership else None,
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
                emit('error', socket_error('unauthorized', 'Missing or invalid workspace token.'))
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
                player = _workspace_player_for_socket(player_id, workspace_id)
                if not player:
                    emit('error', socket_error('invalid_player', 'Invalid player ID.'))
                    telemetry_event(
                        'socket.join.invalid_player',
                        payload={'sid': request.sid, 'session_id': session_id, 'player_id': player_id},
                        severity='warning',
                    )
                    return
                if player.campaign_id != session_obj.campaign_id:
                    emit('error', socket_error('campaign_mismatch', 'Player does not belong to this session campaign.'))
                    telemetry_event(
                        'socket.join.campaign_mismatch',
                        payload={
                            'sid': request.sid,
                            'session_id': session_id,
                            'player_id': player_id,
                            'campaign_id': session_obj.campaign_id,
                        },
                        severity='warning',
                    )
                    return
                player_data = {
                    'id': player.player_id,
                    'character_name': player.character_name,
                    'name': player.name,
                    'race': player.race,
                    'sex': player.sex,
                    'profile_image': profile_icon_src_for_character(player.race, player.sex),
                    'class_': player.class_,
                    'char_class': player.class_,
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
            emit('turn_control_updated', turn_control_update_payload(session_id, turn_control_from_session(session_obj)))
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

    @socketio.on('set_turn_control')
    def handle_set_turn_control(data):
        _set_socket_context('set_turn_control', data if isinstance(data, dict) else None)
        try:
            workspace_id = _socket_workspace_id(data_payload=data)
            if not workspace_id:
                emit('error', socket_error('unauthorized', 'Missing or invalid workspace token.'))
                telemetry_event('socket.turn_control.unauthorized', payload={'sid': request.sid}, severity='warning')
                return
            if not isinstance(data, dict):
                emit('error', socket_error('validation_error', 'Expected object payload for set_turn_control.'))
                telemetry_event('socket.turn_control.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            session_id = coerce_int(data.get('session_id') or data.get('sessionId'))
            player_id = coerce_int(data.get('player_id') or data.get('playerId'))
            mode = str(data.get('mode') or 'free').strip().lower()
            active_player_id = coerce_int(data.get('active_player_id') or data.get('activePlayerId'))
            set_logging_context(session_id=session_id)

            if not session_id or not player_id:
                emit('error', socket_error('validation_error', 'session_id and player_id are required.'))
                telemetry_event('socket.turn_control.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            connection_record = socket_state.connection(request.sid)
            bound_session_id = coerce_int(connection_record.get('session_id')) if connection_record else None
            bound_player_id = coerce_int(connection_record.get('player_id')) if connection_record else None
            if bound_session_id != session_id or bound_player_id != player_id:
                emit(
                    'error',
                    socket_error(
                        'player_identity_mismatch',
                        'This socket can only change turn control for the player and session it joined with.',
                    ),
                )
                telemetry_event(
                    'socket.turn_control.player_identity_mismatch',
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
            if not session_obj:
                emit('error', socket_error('session_not_found', 'Session not found.'))
                telemetry_event(
                    'socket.turn_control.session_not_found',
                    payload={'sid': request.sid, 'session_id': session_id},
                    severity='warning',
                )
                return

            player = _workspace_player_for_socket(player_id, workspace_id)
            if not player or player.campaign_id != session_obj.campaign_id:
                emit('error', socket_error('invalid_player', 'Invalid player ID.'))
                telemetry_event(
                    'socket.turn_control.invalid_player',
                    payload={'sid': request.sid, 'session_id': session_id, 'player_id': player_id},
                    severity='warning',
                )
                return

            if mode not in {'free', 'spotlight', 'structured'}:
                emit('error', socket_error('validation_error', 'Turn mode must be free, spotlight, or structured.'))
                telemetry_event(
                    'socket.turn_control.invalid_mode',
                    payload={'sid': request.sid, 'session_id': session_id, 'mode': mode},
                    severity='warning',
                )
                return

            if mode != 'free':
                active_player_id = active_player_id or player_id
                active_player = _workspace_player_for_socket(active_player_id, workspace_id)
                if not active_player or active_player.campaign_id != session_obj.campaign_id:
                    emit('error', socket_error('invalid_player', 'Active turn player does not belong to this campaign.'))
                    telemetry_event(
                        'socket.turn_control.invalid_active_player',
                        payload={
                            'sid': request.sid,
                            'session_id': session_id,
                            'active_player_id': active_player_id,
                        },
                        severity='warning',
                    )
                    return

            turn_control = set_session_turn_control(
                session_obj,
                mode=mode,
                active_player_id=active_player_id,
                updated_by_player_id=player_id,
            )
            db.session.commit()
            emit('turn_control_updated', turn_control_update_payload(session_id, turn_control), room=str(session_id))
            telemetry_metric('socket.turn_control.updated_total', 1)
        except Exception as exc:
            db.session.rollback()
            logger.exception('Turn control update failed: %s', str(exc))
            emit('error', socket_error('server_error', 'Failed to update turn control.'))
            telemetry_event('socket.turn_control.error', payload={'sid': request.sid, 'error': str(exc)}, severity='error')
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

            was_typing = _player_is_typing(session_id, player_id)
            removed_player = _release_active_player(session_id, player_id, request.sid)
            if removed_player:
                emit('player_left', {'id': player_id}, room=str(session_id))
                emit('active_players', _active_player_payloads(session_id), room=str(session_id))
            elif was_typing != _player_is_typing(session_id, player_id):
                emit('active_players', _active_player_payloads(session_id), room=str(session_id))

            if connection_record is not None:
                connection_record['session_id'] = None
                connection_record['player_id'] = None
            telemetry_metric('socket.leave.success_total', 1)
        finally:
            clear_logging_context()

    @socketio.on('typing_status')
    def handle_typing_status(data):
        _set_socket_context('typing_status', data if isinstance(data, dict) else None)
        try:
            if not isinstance(data, dict):
                emit('error', socket_error('validation_error', 'Expected object payload for typing_status.'))
                telemetry_event('socket.typing.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            workspace_id = _socket_workspace_id(data_payload=data)
            if not workspace_id:
                emit('error', socket_error('unauthorized', 'Missing or invalid workspace token.'))
                telemetry_event('socket.typing.unauthorized', payload={'sid': request.sid}, severity='warning')
                return

            session_id = coerce_int(data.get('session_id'))
            player_id = coerce_int(data.get('player_id'))
            set_logging_context(session_id=session_id)
            if not session_id or not player_id:
                emit('error', socket_error('validation_error', 'session_id and player_id are required'))
                telemetry_event('socket.typing.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            connection_record = socket_state.connection(request.sid)
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
            if _set_player_typing(session_id, player_id, request.sid, is_typing):
                emit('active_players', _active_player_payloads(session_id), room=str(session_id))
            telemetry_metric('socket.typing_status_total', 1)
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
                emit('error', socket_error('unauthorized', 'Missing or invalid workspace token.'))
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

            if _set_player_typing(session_id, player_id, request.sid, False):
                emit('active_players', _active_player_payloads(session_id), room=str(session_id))

            session_obj = workspace_session(session_id, workspace_id)
            if not session_obj or session_obj.campaign_id != campaign_id:
                emit('error', socket_error('session_not_found', 'Session not found.'))
                telemetry_event(
                    'socket.send_message.session_not_found',
                    payload={'sid': request.sid, 'session_id': session_id, 'campaign_id': campaign_id},
                    severity='warning',
                )
                return

            player = _workspace_player_for_socket(player_id, workspace_id)
            if not player:
                emit('error', socket_error('invalid_player', 'Invalid player ID'))
                telemetry_event(
                    'socket.send_message.invalid_player',
                    payload={'sid': request.sid, 'player_id': player_id, 'campaign_id': campaign_id},
                    severity='warning',
                )
                return
            if player.campaign_id != campaign_id:
                emit('error', socket_error('campaign_mismatch', 'Player does not belong to this campaign.'))
                telemetry_event(
                    'socket.send_message.campaign_mismatch',
                    payload={'sid': request.sid, 'player_id': player_id, 'campaign_id': campaign_id},
                    severity='warning',
                )
                return

            action_kind = (
                str(message_payload.action_intent.get('kind')).strip()
                if isinstance(message_payload.action_intent, dict) and message_payload.action_intent.get('kind') is not None
                else ''
            )
            has_pending_roll = action_kind == 'roll' and latest_pending_turn(session_id, player_id) is not None
            turn_allowed, turn_block_reason, turn_control = turn_submission_result(
                session_obj,
                player_id=player_id,
                action_intent=message_payload.action_intent,
                has_pending_roll=has_pending_roll,
            )
            if not turn_allowed:
                emit(
                    'error',
                    socket_error(
                        'turn_out_of_order',
                        turn_block_reason or 'It is not your turn to act.',
                        {'turn_control': turn_control},
                    ),
                )
                telemetry_event(
                    'socket.send_message.turn_out_of_order',
                    payload={
                        'sid': request.sid,
                        'session_id': session_id,
                        'player_id': player_id,
                        'turn_control': turn_control,
                    },
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

    @socketio.on('resolve_clarification')
    def handle_resolve_clarification(data):
        _set_socket_context('resolve_clarification', data if isinstance(data, dict) else None)
        try:
            workspace_id = _socket_workspace_id(data_payload=data)
            if not workspace_id:
                emit('error', socket_error('unauthorized', 'Missing or invalid workspace token.'))
                telemetry_event('socket.resolve_clarification.unauthorized', payload={'sid': request.sid}, severity='warning')
                return
            if not isinstance(data, dict):
                emit('error', socket_error('validation_error', 'Expected object payload for resolve_clarification.'))
                return

            session_id = coerce_int(data.get('session_id') or data.get('sessionId'))
            player_id = coerce_int(data.get('player_id') or data.get('playerId'))
            turn_id = coerce_int(data.get('turn_id') or data.get('turnId'))
            selected_item_id = str(data.get('selected_item_id') or data.get('selectedItemId') or '').strip()
            if not session_id or not player_id or not turn_id or not selected_item_id:
                emit(
                    'error',
                    socket_error(
                        'validation_error',
                        'session_id, player_id, turn_id, and selected_item_id are required.',
                    ),
                )
                return

            connection_record = socket_state.connection(request.sid)
            bound_session_id = coerce_int(connection_record.get('session_id')) if connection_record else None
            bound_player_id = coerce_int(connection_record.get('player_id')) if connection_record else None
            if bound_session_id != session_id or bound_player_id != player_id:
                emit(
                    'error',
                    socket_error(
                        'player_identity_mismatch',
                        'This socket can only resolve clarification for the player and session it joined with.',
                    ),
                )
                return

            session_obj = workspace_session(session_id, workspace_id)
            player = _workspace_player_for_socket(player_id, workspace_id)
            turn = db.session.get(DmTurn, turn_id)
            if not session_obj or not player or not turn or turn.session_id != session_id or turn.player_id != player_id:
                emit('error', socket_error('clarification_not_found', 'Clarification turn not found.'))
                return

            metadata = safe_json_loads(turn.metadata_json, {})
            pipeline = metadata.get('state_pipeline') if isinstance(metadata, dict) and isinstance(metadata.get('state_pipeline'), dict) else {}
            request_payload = pipeline.get('clarificationRequest') if isinstance(pipeline.get('clarificationRequest'), dict) else {}
            original_action = request_payload.get('originalAction') if isinstance(request_payload.get('originalAction'), dict) else None
            if not original_action:
                emit('error', socket_error('clarification_not_found', 'Clarification request metadata is missing.'))
                return
            valid_option_ids = {
                str(option.get('itemId'))
                for option in request_payload.get('options') or []
                if isinstance(option, dict) and option.get('itemId')
            }
            if selected_item_id not in valid_option_ids:
                emit('error', socket_error('clarification_invalid_selection', 'Selected item is not one of the clarification options.'))
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
                    campaign_id=session_obj.campaign_id,
                    world_id=session_obj.campaign.world_id if session_obj.campaign else 0,
                    player_id=player_id,
                    user_input=turn.player_input,
                    manual_segment_ids=set(),
                    action_intent=metadata.get('action_intent') if isinstance(metadata, dict) else None,
                    client_message_id=f'clarification-{turn_id}-{selected_item_id}',
                    state_pipeline_override={
                        'declaredActions': [original_action],
                        'selectedItemIds': {str(original_action.get('id')): selected_item_id},
                        'resolvedClarificationTurnId': turn_id,
                    },
                )
            )
        finally:
            clear_logging_context()
