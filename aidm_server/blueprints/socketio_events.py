from __future__ import annotations

import logging

from flask import current_app, request
from flask_socketio import emit

from aidm_server.capabilities import required_socket_capability
from aidm_server.llm import query_dm_function_stream
from aidm_server.logging_context import clear_logging_context, set_logging_context
from aidm_server.database import db
from aidm_server.models import DmTurn
from aidm_server.socket_access import (
    admin_passcode_is_valid,
    authorize_admin_socket_action,
    socket_message_rate_key,
)
from aidm_server.socket_clarification import (
    SocketClarificationDependencies,
    register_socket_clarification_events,
)
from aidm_server.socket_contracts import socket_error_payload as socket_error, validate_send_message_payload
from aidm_server.socket_music import (
    MUSIC_MAX_POSITION_SECONDS,
    MUSIC_TRACK_ID_RE,
    SocketMusicDependencies,
    coerce_music_position,
    music_now_ms,
    music_state_for_emit,
    register_socket_music_events,
)
from aidm_server.socket_presence import SocketPresenceDependencies, register_socket_presence_events
from aidm_server.socket_runtime import SocketRuntime
from aidm_server.socket_state import SocketState
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.socket_typing import SocketTypingDependencies, register_socket_typing_events
from aidm_server.turn_control import (
    TURN_CONTROL_MODES,
    TURN_CONTROL_SOURCES,
    set_session_turn_control,
    turn_control_update_payload,
)
from aidm_server.turn_engine import TurnCommand, TurnEngine
from aidm_server.validation import coerce_int
from aidm_server.workspace_access import get_player as workspace_player, get_session as workspace_session


logger = logging.getLogger(__name__)

_MUSIC_TRACK_ID_RE = MUSIC_TRACK_ID_RE
_MUSIC_MAX_POSITION_SECONDS = MUSIC_MAX_POSITION_SECONDS

socket_state = SocketState()
# Compatibility aliases for tests and diagnostics. Runtime code should use socket_state.
active_players = socket_state.active_players
socketio_connections = socket_state.connections
socket_runtime = SocketRuntime(socket_state)


def _set_socket_context(event_name: str, data: dict | None = None, turn_id: int | None = None):
    socket_runtime.set_context(event_name, data, turn_id)


def _socket_rate_limiter():
    return socket_runtime.rate_limiter()


def _socket_rate_key(workspace_id: str, session_id: int, player_id: int) -> str:
    return socket_message_rate_key(workspace_id, session_id, player_id)


def _emit_socket_rate_limited(telemetry_prefix: str, session_id: int, reset_in_seconds: int) -> None:
    emit(
        'error',
        socket_error(
            'rate_limited',
            'Too many socket messages; please wait before sending more.',
            {'reset_in_seconds': reset_in_seconds},
        ),
    )
    telemetry_event(
        f'{telemetry_prefix}.rate_limited',
        payload={'sid': request.sid, 'session_id': session_id, 'reset_in_seconds': reset_in_seconds},
        severity='warning',
    )


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


def _music_now_ms() -> int:
    return music_now_ms()


def _coerce_music_position(value) -> float | None:
    return coerce_music_position(value, max_position_seconds=_MUSIC_MAX_POSITION_SECONDS)


def _music_state_for_emit(state: dict | None) -> dict | None:
    if not state:
        return None
    return music_state_for_emit(
        state,
        now_ms=_music_now_ms(),
        coerce_position=_coerce_music_position,
        max_position_seconds=_MUSIC_MAX_POSITION_SECONDS,
    )


def _admin_passcode_is_valid(data: dict | None) -> bool:
    return admin_passcode_is_valid(current_app.config.get('AIDM_ADMIN_PASSCODE'), data)


def _socket_capability_forbidden(event_name: str) -> bool:
    required_capability = required_socket_capability(event_name)
    if required_capability is None or socket_runtime.connection_has_capability(request.sid, required_capability):
        return False
    emit(
        'error',
        socket_error(
            'forbidden',
            f'Missing required capability: {required_capability}.',
            {'required_capability': required_capability},
        ),
    )
    telemetry_event(
        'socket.capability_forbidden',
        payload={
            'sid': request.sid,
            'event': event_name,
            'required_capability': required_capability,
        },
        severity='warning',
    )
    return True


def _new_turn_engine(socketio) -> TurnEngine:
    return TurnEngine(
        socketio=socketio,
        emit_fn=emit,
        stream_fn=query_dm_function_stream,
        active_player_ids_fn=lambda session_id: [
            int(player['id']) for player in _active_player_payloads(session_id) if player.get('id')
        ],
    )


def register_socketio_events(socketio):
    register_socket_presence_events(
        socketio,
        SocketPresenceDependencies(
            runtime=socket_runtime,
            state=socket_state,
            logger=logger,
            set_socket_context=lambda *args, **kwargs: _set_socket_context(*args, **kwargs),
            socket_workspace_id=lambda *args, **kwargs: _socket_workspace_id(*args, **kwargs),
            socket_capability_forbidden=lambda event_name: _socket_capability_forbidden(event_name),
            workspace_session=lambda session_id, workspace_id: workspace_session(session_id, workspace_id),
            workspace_player=lambda player_id, workspace_id: _workspace_player_for_socket(player_id, workspace_id),
            active_player_payloads=lambda session_id: _active_player_payloads(session_id),
            track_active_player=lambda session_id, player_data, sid: _track_active_player(session_id, player_data, sid),
            release_active_player=lambda session_id, player_id, sid: _release_active_player(session_id, player_id, sid),
            player_is_typing=lambda session_id, player_id: _player_is_typing(session_id, player_id),
            music_state_for_emit=lambda state: _music_state_for_emit(state),
        ),
    )

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

            if _socket_capability_forbidden('set_turn_control'):
                return

            session_id = coerce_int(data.get('session_id') or data.get('sessionId'))
            player_id = coerce_int(data.get('player_id') or data.get('playerId'))
            mode = str(data.get('mode') or 'free').strip().lower()
            source = str(data.get('source') or 'manual').strip().lower()
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

            if mode not in TURN_CONTROL_MODES:
                emit('error', socket_error('validation_error', 'Turn mode must be free, spotlight, or structured.'))
                telemetry_event(
                    'socket.turn_control.invalid_mode',
                    payload={'sid': request.sid, 'session_id': session_id, 'mode': mode},
                    severity='warning',
                )
                return

            if source not in TURN_CONTROL_SOURCES or source in {'ai', 'system'}:
                emit('error', socket_error('validation_error', 'Turn control source must be auto, manual, or admin.'))
                telemetry_event(
                    'socket.turn_control.invalid_source',
                    payload={'sid': request.sid, 'session_id': session_id, 'source': source},
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
                source=source,
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

    register_socket_music_events(
        socketio,
        SocketMusicDependencies(
            state=socket_state,
            set_socket_context=lambda *args, **kwargs: _set_socket_context(*args, **kwargs),
            socket_workspace_id=lambda *args, **kwargs: _socket_workspace_id(*args, **kwargs),
            socket_capability_forbidden=lambda event_name: _socket_capability_forbidden(event_name),
            workspace_session=lambda session_id, workspace_id: workspace_session(session_id, workspace_id),
            workspace_player=lambda player_id, workspace_id: _workspace_player_for_socket(player_id, workspace_id),
            track_id_is_valid=lambda track_id: bool(_MUSIC_TRACK_ID_RE.match(track_id)),
            now_ms=lambda: _music_now_ms(),
            coerce_position=lambda value: _coerce_music_position(value),
            state_for_emit=lambda state: _music_state_for_emit(state),
        ),
    )

    register_socket_typing_events(
        socketio,
        SocketTypingDependencies(
            state=socket_state,
            set_socket_context=lambda *args, **kwargs: _set_socket_context(*args, **kwargs),
            socket_workspace_id=lambda *args, **kwargs: _socket_workspace_id(*args, **kwargs),
            socket_capability_forbidden=lambda event_name: _socket_capability_forbidden(event_name),
            set_player_typing=lambda session_id, player_id, sid, is_typing: _set_player_typing(
                session_id,
                player_id,
                sid,
                is_typing,
            ),
            active_player_payloads=lambda session_id: _active_player_payloads(session_id),
            rate_key=lambda workspace_id, session_id, player_id: _socket_rate_key(
                workspace_id,
                session_id,
                player_id,
            ),
            allow_rate_key=lambda key: _socket_rate_limiter().allow(key),
            emit_rate_limited=lambda telemetry_prefix, session_id, reset_in_seconds: _emit_socket_rate_limited(
                telemetry_prefix,
                session_id,
                reset_in_seconds,
            ),
        ),
    )

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

            if _socket_capability_forbidden('send_message'):
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

            limit_result = _socket_rate_limiter().allow(_socket_rate_key(workspace_id, session_id, player_id))
            if not limit_result.allowed:
                _emit_socket_rate_limited('socket.send_message', session_id, limit_result.reset_in_seconds)
                return

            if message_payload.action_intent and message_payload.action_intent.get('kind') == 'admin':
                admin_authorization = authorize_admin_socket_action(
                    configured_passcode=current_app.config.get('AIDM_ADMIN_PASSCODE'),
                    data=data,
                    workspace_id=workspace_id,
                    remote_address=request.remote_addr or request.environ.get('REMOTE_ADDR') or 'unknown',
                    allow_rate_key=_socket_rate_limiter().allow,
                    passcode_validator=_admin_passcode_is_valid,
                )
                if admin_authorization.error_code == 'rate_limited':
                    _emit_socket_rate_limited(
                        'socket.send_message.admin_passcode',
                        session_id,
                        admin_authorization.reset_in_seconds or 0,
                    )
                    return
                if not admin_authorization.allowed:
                    emit('error', socket_error(admin_authorization.error_code, admin_authorization.message))
                    telemetry_event(
                        f'socket.send_message.{admin_authorization.error_code}',
                        payload={'sid': request.sid},
                        severity='warning',
                    )
                    return

            engine = _new_turn_engine(socketio)
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

    register_socket_clarification_events(
        socketio,
        SocketClarificationDependencies(
            state=socket_state,
            set_socket_context=lambda *args, **kwargs: _set_socket_context(*args, **kwargs),
            socket_workspace_id=lambda *args, **kwargs: _socket_workspace_id(*args, **kwargs),
            socket_capability_forbidden=lambda event_name: _socket_capability_forbidden(event_name),
            workspace_session=lambda session_id, workspace_id: workspace_session(session_id, workspace_id),
            workspace_player=lambda player_id, workspace_id: _workspace_player_for_socket(player_id, workspace_id),
            get_turn=lambda turn_id: db.session.get(DmTurn, turn_id),
            process_turn=lambda command: _new_turn_engine(socketio).process(command),
        ),
    )
