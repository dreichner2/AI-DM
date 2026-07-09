"""Socket.IO connection and session-presence event registration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable

from flask import request
from flask_socketio import emit, join_room, leave_room

from aidm_server.database import db
from aidm_server.llm import CONTEXT_VERSION
from aidm_server.logging_context import clear_logging_context, set_logging_context
from aidm_server.profile_icons import profile_icon_src_for_character
from aidm_server.services.scene_state import scene_state_for_session
from aidm_server.socket_contracts import scene_state_payload, socket_error_payload as socket_error
from aidm_server.socket_runtime import SocketRuntime
from aidm_server.socket_state import SocketState
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.turn_control import turn_control_from_session, turn_control_update_payload
from aidm_server.validation import coerce_int


@dataclass(frozen=True)
class SocketPresenceDependencies:
    runtime: SocketRuntime
    state: SocketState
    logger: logging.Logger
    set_socket_context: Callable[..., str]
    socket_workspace_id: Callable[..., str | None]
    socket_capability_forbidden: Callable[[str], bool]
    workspace_session: Callable[[int, str], Any]
    workspace_player: Callable[[int, str], Any]
    active_player_payloads: Callable[[int], list[dict]]
    track_active_player: Callable[[int, dict, str], bool]
    release_active_player: Callable[[int, int, str], bool]
    player_is_typing: Callable[[int, int], bool]
    music_state_for_emit: Callable[[dict | None], dict | None]


def connection_binding_ids(connection_record: dict | None) -> tuple[int | None, int | None]:
    if not connection_record:
        return None, None
    return coerce_int(connection_record.get('session_id')), coerce_int(connection_record.get('player_id'))


def player_presence_payload(player) -> dict:
    return {
        'id': player.player_id,
        'character_name': player.character_name,
        'name': player.name,
        'race': player.race,
        'sex': player.sex,
        'profile_image': profile_icon_src_for_character(player.race, player.sex),
        'class_': player.class_,
        'char_class': player.class_,
    }


def register_socket_presence_events(socketio, dependencies: SocketPresenceDependencies) -> None:
    """Register connection, join, leave, and disconnect lifecycle handlers."""

    def serialize_connection_lifecycle(handler):
        @wraps(handler)
        def serialized(*args, **kwargs):
            sid = getattr(request, 'sid', '') or ''
            with dependencies.state.connection_lifecycle(sid):
                return handler(*args, **kwargs)

        return serialized

    def clear_connection_binding(sid: str, *, leave_bound_room: bool):
        dependencies.runtime.clear_connection_binding(
            sid,
            leave_bound_room=leave_bound_room,
            leave_room_fn=leave_room,
            emit_fn=emit,
        )

    @socketio.on('connect')
    @serialize_connection_lifecycle
    def handle_connect(auth=None):
        correlation_id = dependencies.set_socket_context('connect', auth if isinstance(auth, dict) else None)
        try:
            try:
                sid = getattr(request, 'sid', None)
                remote_addr = getattr(request, 'remote_addr', None)
                workspace_id = dependencies.socket_workspace_id(auth_payload=auth)

                if not workspace_id:
                    dependencies.logger.warning('Socket auth rejected sid=%s', sid)
                    telemetry_event(
                        'socket.connect.unauthorized',
                        payload={'sid': sid, 'remote_addr': remote_addr},
                        severity='warning',
                    )
                    return False

                account = dependencies.runtime.account_for_auth(auth_payload=auth)
                membership = (
                    dependencies.runtime.membership_for_auth(auth_payload=auth, workspace_id=workspace_id)
                    if account
                    else None
                )
                if membership:
                    db.session.commit()

                if sid:
                    dependencies.state.set_connection(
                        sid,
                        {
                            'authorized': True,
                            'workspace_id': workspace_id,
                            'account_id': account.account_id if account else None,
                            'workspace_role': membership.role if membership else None,
                            'credential_present': dependencies.runtime.credential_present_for_auth(auth_payload=auth),
                            'global_operator': dependencies.runtime.global_operator_for_auth(auth_payload=auth),
                            'session_id': None,
                            'player_id': None,
                            'correlation_id': correlation_id,
                        },
                    )
                telemetry_metric('socket.connect.success_total', 1)
                return None
            except Exception as exc:
                dependencies.logger.exception('Socket connect handler failed: %s', str(exc))
                telemetry_event(
                    'socket.connect.error',
                    payload={'error': str(exc)},
                    severity='error',
                )
                return False
        finally:
            clear_logging_context()

    @socketio.on('join_session')
    @serialize_connection_lifecycle
    def handle_join_session(data):
        dependencies.set_socket_context('join_session', data if isinstance(data, dict) else None)
        try:
            if not isinstance(data, dict):
                emit('error', socket_error('validation_error', 'Expected object payload for join_session.'))
                telemetry_event('socket.join.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            if dependencies.socket_capability_forbidden('join_session'):
                return

            workspace_id = dependencies.socket_workspace_id(data_payload=data)
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

            session_obj = dependencies.workspace_session(session_id, workspace_id)
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
                player = dependencies.workspace_player(player_id, workspace_id)
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
                player_data = player_presence_payload(player)

            connection_record = dependencies.state.connection(request.sid)
            if not connection_record or not connection_record.get('authorized'):
                emit('error', socket_error('unauthorized', 'Socket connection is no longer active.'))
                return
            existing_session_id, existing_player_id = connection_binding_ids(connection_record)
            if existing_session_id != session_id or existing_player_id != player_id:
                clear_connection_binding(request.sid, leave_bound_room=True)

            join_room(str(session_id))
            updated_connection = dependencies.state.update_connection_if_present(
                request.sid,
                workspace_id=workspace_id,
                session_id=session_id,
                player_id=player_id,
            )
            if updated_connection is None:
                leave_room(str(session_id))
                emit('error', socket_error('unauthorized', 'Socket connection is no longer active.'))
                return

            dependencies.state.ensure_session(session_id)

            if player_id and player_data:
                joined_fresh = dependencies.track_active_player(session_id, player_data, request.sid)
                if joined_fresh:
                    emit('player_joined', player_data, room=str(session_id))
            emit('active_players', dependencies.active_player_payloads(session_id), room=str(session_id))
            emit('turn_control_updated', turn_control_update_payload(session_id, turn_control_from_session(session_obj)))
            current_music_state = dependencies.music_state_for_emit(dependencies.state.music_state(session_id))
            if current_music_state:
                emit('music_state', current_music_state)
            current_scene_state = scene_state_for_session(session_id)
            if current_scene_state:
                emit('scene_state', scene_state_payload(current_scene_state))
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
    @serialize_connection_lifecycle
    def handle_leave_session(data):
        dependencies.set_socket_context('leave_session', data if isinstance(data, dict) else None)
        try:
            if not isinstance(data, dict):
                emit('error', socket_error('validation_error', 'Expected object payload for leave_session.'))
                telemetry_event('socket.leave.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            if dependencies.socket_capability_forbidden('leave_session'):
                return

            session_id = coerce_int(data.get('session_id'))
            player_id = coerce_int(data.get('player_id'))
            set_logging_context(session_id=session_id)

            if not session_id or not player_id:
                emit('error', socket_error('validation_error', 'session_id and player_id are required'))
                telemetry_event('socket.leave.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            connection_record = dependencies.state.connection(request.sid)
            bound_session_id, bound_player_id = connection_binding_ids(connection_record)
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

            unbound_connection = dependencies.state.unbind_connection(
                request.sid,
                expected_session_id=session_id,
                expected_player_id=player_id,
            )
            if unbound_connection is None:
                emit(
                    'error',
                    socket_error(
                        'player_identity_mismatch',
                        'The socket binding changed before the leave operation completed.',
                    ),
                )
                return

            leave_room(str(session_id))

            was_typing = dependencies.player_is_typing(session_id, player_id)
            removed_player = dependencies.release_active_player(session_id, player_id, request.sid)
            if removed_player:
                emit('player_left', {'id': player_id}, room=str(session_id))
                emit('active_players', dependencies.active_player_payloads(session_id), room=str(session_id))
            elif was_typing != dependencies.player_is_typing(session_id, player_id):
                emit('active_players', dependencies.active_player_payloads(session_id), room=str(session_id))

            telemetry_metric('socket.leave.success_total', 1)
        finally:
            clear_logging_context()

    @socketio.on('disconnect')
    @serialize_connection_lifecycle
    def handle_disconnect():
        dependencies.set_socket_context('disconnect')
        try:
            connection_info = dependencies.runtime.release_disconnect(request.sid, emit_fn=emit)
            if not connection_info:
                return
            telemetry_metric('socket.disconnect_total', 1)
        finally:
            clear_logging_context()
