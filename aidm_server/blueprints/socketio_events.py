from __future__ import annotations

import logging

from flask import current_app, request
from flask_socketio import emit

from aidm_server.capabilities import required_socket_capability
from aidm_server.llm import query_dm_function_stream
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
from aidm_server.socket_message import SocketMessageDependencies, register_socket_message_events
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
from aidm_server.socket_turn_control import (
    SocketTurnControlDependencies,
    register_socket_turn_control_events,
)
from aidm_server.socket_typing import SocketTypingDependencies, register_socket_typing_events
from aidm_server.telemetry import telemetry_event
from aidm_server.turn_control import (
    set_session_turn_control,
    turn_control_update_payload,
)
from aidm_server.turn_engine import TurnEngine
from aidm_server.turn_coordinator import session_turn_coordinator
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
    return socket_runtime.set_context(event_name, data, turn_id)


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

    register_socket_turn_control_events(
        socketio,
        SocketTurnControlDependencies(
            state=socket_state,
            logger=logger,
            set_socket_context=lambda *args, **kwargs: _set_socket_context(*args, **kwargs),
            socket_workspace_id=lambda *args, **kwargs: _socket_workspace_id(*args, **kwargs),
            socket_capability_forbidden=lambda event_name: _socket_capability_forbidden(event_name),
            workspace_session=lambda session_id, workspace_id: workspace_session(session_id, workspace_id),
            workspace_player=lambda player_id, workspace_id: _workspace_player_for_socket(player_id, workspace_id),
            serialize_session=lambda session_id: session_turn_coordinator.serialized(session_id),
            refresh_session=lambda: db.session.expire_all(),
            set_turn_control=set_session_turn_control,
            turn_control_payload=turn_control_update_payload,
            commit=lambda: db.session.commit(),
            rollback=lambda: db.session.rollback(),
        ),
    )

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
            workspace_session=lambda session_id, workspace_id: workspace_session(session_id, workspace_id),
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

    register_socket_message_events(
        socketio,
        SocketMessageDependencies(
            state=socket_state,
            set_socket_context=lambda *args, **kwargs: _set_socket_context(*args, **kwargs),
            socket_workspace_id=lambda *args, **kwargs: _socket_workspace_id(*args, **kwargs),
            socket_capability_forbidden=lambda event_name: _socket_capability_forbidden(event_name),
            validate_payload=validate_send_message_payload,
            set_player_typing=lambda session_id, player_id, sid, is_typing: _set_player_typing(
                session_id,
                player_id,
                sid,
                is_typing,
            ),
            emit_active_players=lambda session_id: emit(
                'active_players',
                _active_player_payloads(session_id),
                room=str(session_id),
            ),
            workspace_session=lambda session_id, workspace_id: workspace_session(session_id, workspace_id),
            workspace_player=lambda player_id, workspace_id: _workspace_player_for_socket(player_id, workspace_id),
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
            configured_admin_passcode=lambda: current_app.config.get('AIDM_ADMIN_PASSCODE'),
            authorize_admin_action=authorize_admin_socket_action,
            passcode_validator=lambda data: _admin_passcode_is_valid(data),
            process_turn=lambda command: _new_turn_engine(socketio).process(command),
        ),
    )

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
