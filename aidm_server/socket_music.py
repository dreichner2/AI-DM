"""Socket.IO scene-music state policy and event registration."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from flask import request
from flask_socketio import emit

from aidm_server.logging_context import clear_logging_context, set_logging_context
from aidm_server.services.session_lifecycle import session_playability_error
from aidm_server.socket_contracts import socket_error_payload as socket_error
from aidm_server.socket_state import SocketState
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.validation import coerce_int


MUSIC_TRACK_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,120}$')
MUSIC_MAX_POSITION_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class SocketMusicDependencies:
    state: SocketState
    set_socket_context: Callable[..., None]
    socket_workspace_id: Callable[..., str | None]
    socket_capability_forbidden: Callable[[str], bool]
    workspace_session: Callable[[int, str], Any]
    workspace_player: Callable[[int, str], Any]
    track_id_is_valid: Callable[[str], bool]
    now_ms: Callable[[], int]
    coerce_position: Callable[[Any], float | None]
    state_for_emit: Callable[[dict | None], dict | None]


def music_now_ms() -> int:
    return int(time.time() * 1000)


def coerce_music_position(
    value,
    *,
    max_position_seconds: float = MUSIC_MAX_POSITION_SECONDS,
) -> float | None:
    try:
        position = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(position):
        return None
    return min(max_position_seconds, max(0.0, position))


def music_state_for_emit(
    state: dict | None,
    *,
    now_ms: int | None = None,
    coerce_position: Callable[[Any], float | None] = coerce_music_position,
    max_position_seconds: float = MUSIC_MAX_POSITION_SECONDS,
) -> dict | None:
    if not state:
        return None

    current_time_ms = music_now_ms() if now_ms is None else now_ms
    position = coerce_position(state.get('position')) or 0.0
    updated_at_ms = coerce_int(state.get('updated_at_ms')) or current_time_ms
    status = str(state.get('status') or '').strip().lower()
    if status == 'playing':
        position = min(
            max_position_seconds,
            position + max(0, current_time_ms - updated_at_ms) / 1000,
        )
        updated_at_ms = current_time_ms

    return {
        'session_id': coerce_int(state.get('session_id')),
        'track_id': str(state.get('track_id') or ''),
        'status': status,
        'position': round(position, 3),
        'updated_at_ms': updated_at_ms,
        'updated_by_player_id': coerce_int(state.get('updated_by_player_id')),
    }


def register_socket_music_events(socketio, dependencies: SocketMusicDependencies) -> None:
    @socketio.on('music_control')
    def handle_music_control(data):
        dependencies.set_socket_context('music_control', data if isinstance(data, dict) else None)
        try:
            if not isinstance(data, dict):
                emit('error', socket_error('validation_error', 'Expected object payload for music_control.'))
                telemetry_event('socket.music.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            workspace_id = dependencies.socket_workspace_id(data_payload=data)
            if not workspace_id:
                emit('error', socket_error('unauthorized', 'Missing or invalid workspace token.'))
                telemetry_event('socket.music.unauthorized', payload={'sid': request.sid}, severity='warning')
                return

            if dependencies.socket_capability_forbidden('music_control'):
                return

            session_id = coerce_int(data.get('session_id') or data.get('sessionId'))
            player_id = coerce_int(data.get('player_id') or data.get('playerId'))
            track_id = str(data.get('track_id') or data.get('trackId') or '').strip()
            status = str(data.get('status') or '').strip().lower()
            position = dependencies.coerce_position(data.get('position'))
            set_logging_context(session_id=session_id)

            if not session_id or not player_id:
                emit('error', socket_error('validation_error', 'session_id and player_id are required.'))
                telemetry_event('socket.music.validation_error', payload={'sid': request.sid}, severity='warning')
                return

            if not dependencies.track_id_is_valid(track_id):
                emit('error', socket_error('validation_error', 'Music track ID is invalid.'))
                telemetry_event(
                    'socket.music.invalid_track',
                    payload={'sid': request.sid, 'session_id': session_id},
                    severity='warning',
                )
                return
            if status not in {'playing', 'paused'} or position is None:
                emit('error', socket_error('validation_error', 'Music status and position are required.'))
                telemetry_event(
                    'socket.music.invalid_state',
                    payload={'sid': request.sid, 'session_id': session_id, 'status': status},
                    severity='warning',
                )
                return

            connection_record = dependencies.state.connection(request.sid)
            bound_session_id = coerce_int(connection_record.get('session_id')) if connection_record else None
            bound_player_id = coerce_int(connection_record.get('player_id')) if connection_record else None
            if bound_session_id != session_id or bound_player_id != player_id:
                emit(
                    'error',
                    socket_error(
                        'player_identity_mismatch',
                        'This socket can only update music for the player and session it joined with.',
                    ),
                )
                telemetry_event(
                    'socket.music.player_identity_mismatch',
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

            session_obj = dependencies.workspace_session(session_id, workspace_id)
            if not session_obj:
                emit('error', socket_error('session_not_found', 'Session not found.'))
                telemetry_event(
                    'socket.music.session_not_found',
                    payload={'sid': request.sid, 'session_id': session_id},
                    severity='warning',
                )
                return

            playability_error = session_playability_error(session_obj)
            if playability_error:
                error_code, message = playability_error
                emit('error', socket_error(error_code, message))
                telemetry_event(
                    f'socket.music.{error_code}',
                    payload={'sid': request.sid, 'session_id': session_id},
                    severity='warning',
                )
                return

            player = dependencies.workspace_player(player_id, workspace_id)
            if not player or player.campaign_id != session_obj.campaign_id:
                emit('error', socket_error('invalid_player', 'Invalid player ID.'))
                telemetry_event(
                    'socket.music.invalid_player',
                    payload={'sid': request.sid, 'session_id': session_id, 'player_id': player_id},
                    severity='warning',
                )
                return

            state = dependencies.state.set_music_state(
                session_id,
                {
                    'track_id': track_id,
                    'status': status,
                    'position': position,
                    'updated_at_ms': dependencies.now_ms(),
                    'updated_by_player_id': player_id,
                },
            )
            emit('music_state', dependencies.state_for_emit(state), room=str(session_id))
            telemetry_metric('socket.music_control_total', 1)
        finally:
            clear_logging_context()
