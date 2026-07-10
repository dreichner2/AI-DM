"""Socket.IO turn-control request policy and event registration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from flask import request
from flask_socketio import emit

from aidm_server.logging_context import clear_logging_context, set_logging_context
from aidm_server.socket_contracts import socket_error_payload as socket_error
from aidm_server.socket_state import SocketState
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.turn_control import TURN_CONTROL_MODES, TURN_CONTROL_SOURCES
from aidm_server.validation import coerce_int


@dataclass(frozen=True)
class SocketTurnControlDependencies:
    state: SocketState
    logger: logging.Logger
    set_socket_context: Callable[..., None]
    socket_workspace_id: Callable[..., str | None]
    socket_capability_forbidden: Callable[[str], bool]
    workspace_session: Callable[[int, str], Any]
    workspace_player: Callable[[int, str], Any]
    set_turn_control: Callable[..., dict]
    turn_control_payload: Callable[[int, dict], dict]
    commit: Callable[[], None]
    rollback: Callable[[], None]


@dataclass(frozen=True)
class TurnControlRequest:
    session_id: int | None
    player_id: int | None
    mode: str
    source: str
    active_player_id: int | None


@dataclass(frozen=True)
class TurnControlFailure:
    error_code: str
    message: str
    telemetry_suffix: str
    telemetry_payload: dict[str, Any]


@dataclass(frozen=True)
class TurnControlUpdate:
    session_id: int
    payload: dict


def normalize_turn_control_request(data: dict) -> TurnControlRequest:
    """Normalize snake/camel-case transport fields without applying access policy."""

    return TurnControlRequest(
        session_id=coerce_int(data.get('session_id') or data.get('sessionId')),
        player_id=coerce_int(data.get('player_id') or data.get('playerId')),
        mode=str(data.get('mode') or 'free').strip().lower(),
        source=str(data.get('source') or 'manual').strip().lower(),
        active_player_id=coerce_int(data.get('active_player_id') or data.get('activePlayerId')),
    )


def apply_turn_control_update(
    turn_request: TurnControlRequest,
    *,
    sid: str,
    workspace_id: str,
    dependencies: SocketTurnControlDependencies,
) -> TurnControlUpdate | TurnControlFailure:
    """Authorize, validate, and persist a normalized turn-control update."""

    session_id = turn_request.session_id
    player_id = turn_request.player_id
    if not session_id or not player_id:
        return TurnControlFailure(
            'validation_error',
            'session_id and player_id are required.',
            'validation_error',
            {},
        )

    connection_record = dependencies.state.connection(sid)
    bound_session_id = coerce_int(connection_record.get('session_id')) if connection_record else None
    bound_player_id = coerce_int(connection_record.get('player_id')) if connection_record else None
    if bound_session_id != session_id or bound_player_id != player_id:
        return TurnControlFailure(
            'player_identity_mismatch',
            'This socket can only change turn control for the player and session it joined with.',
            'player_identity_mismatch',
            {
                'session_id': session_id,
                'player_id': player_id,
                'bound_session_id': bound_session_id,
                'bound_player_id': bound_player_id,
            },
        )

    session_obj = dependencies.workspace_session(session_id, workspace_id)
    if not session_obj:
        return TurnControlFailure(
            'session_not_found',
            'Session not found.',
            'session_not_found',
            {'session_id': session_id},
        )

    player = dependencies.workspace_player(player_id, workspace_id)
    if not player or player.campaign_id != session_obj.campaign_id:
        return TurnControlFailure(
            'invalid_player',
            'Invalid player ID.',
            'invalid_player',
            {'session_id': session_id, 'player_id': player_id},
        )

    if turn_request.mode not in TURN_CONTROL_MODES:
        return TurnControlFailure(
            'validation_error',
            'Turn mode must be free, spotlight, or structured.',
            'invalid_mode',
            {'session_id': session_id, 'mode': turn_request.mode},
        )

    if turn_request.source not in TURN_CONTROL_SOURCES or turn_request.source in {'ai', 'system'}:
        return TurnControlFailure(
            'validation_error',
            'Turn control source must be auto, manual, or admin.',
            'invalid_source',
            {'session_id': session_id, 'source': turn_request.source},
        )

    active_player_id = turn_request.active_player_id
    if turn_request.mode != 'free':
        active_player_id = active_player_id or player_id
        active_player = dependencies.workspace_player(active_player_id, workspace_id)
        if not active_player or active_player.campaign_id != session_obj.campaign_id:
            return TurnControlFailure(
                'invalid_player',
                'Active turn player does not belong to this campaign.',
                'invalid_active_player',
                {'session_id': session_id, 'active_player_id': active_player_id},
            )

    turn_control = dependencies.set_turn_control(
        session_obj,
        mode=turn_request.mode,
        active_player_id=active_player_id,
        updated_by_player_id=player_id,
        source=turn_request.source,
    )
    dependencies.commit()
    return TurnControlUpdate(
        session_id=session_id,
        payload=dependencies.turn_control_payload(session_id, turn_control),
    )


def register_socket_turn_control_events(
    socketio,
    dependencies: SocketTurnControlDependencies,
) -> None:
    @socketio.on('set_turn_control')
    def handle_set_turn_control(data):
        dependencies.set_socket_context('set_turn_control', data if isinstance(data, dict) else None)
        try:
            workspace_id = dependencies.socket_workspace_id(data_payload=data)
            if not workspace_id:
                emit('error', socket_error('unauthorized', 'Missing or invalid workspace token.'))
                telemetry_event('socket.turn_control.unauthorized', payload={'sid': request.sid}, severity='warning')
                return
            if not isinstance(data, dict):
                emit('error', socket_error('validation_error', 'Expected object payload for set_turn_control.'))
                telemetry_event(
                    'socket.turn_control.validation_error',
                    payload={'sid': request.sid},
                    severity='warning',
                )
                return

            if dependencies.socket_capability_forbidden('set_turn_control'):
                return

            turn_request = normalize_turn_control_request(data)
            set_logging_context(session_id=turn_request.session_id)
            outcome = apply_turn_control_update(
                turn_request,
                sid=request.sid,
                workspace_id=workspace_id,
                dependencies=dependencies,
            )
            if isinstance(outcome, TurnControlFailure):
                emit('error', socket_error(outcome.error_code, outcome.message))
                telemetry_event(
                    f'socket.turn_control.{outcome.telemetry_suffix}',
                    payload={'sid': request.sid, **outcome.telemetry_payload},
                    severity='warning',
                )
                return

            emit('turn_control_updated', outcome.payload, room=str(outcome.session_id))
            telemetry_metric('socket.turn_control.updated_total', 1)
        except Exception as exc:
            dependencies.rollback()
            dependencies.logger.exception('Turn control update failed: %s', str(exc))
            emit('error', socket_error('server_error', 'Failed to update turn control.'))
            telemetry_event(
                'socket.turn_control.error',
                payload={'sid': request.sid, 'error': str(exc)},
                severity='error',
            )
        finally:
            clear_logging_context()
