"""Socket.IO clarification-resolution event registration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import request
from flask_socketio import emit

from aidm_server.logging_context import clear_logging_context
from aidm_server.models import safe_json_loads
from aidm_server.socket_contracts import socket_error_payload as socket_error
from aidm_server.socket_state import SocketState
from aidm_server.telemetry import telemetry_event
from aidm_server.turn_engine import TurnCommand
from aidm_server.validation import coerce_int


@dataclass(frozen=True)
class SocketClarificationDependencies:
    state: SocketState
    set_socket_context: Callable[..., None]
    socket_workspace_id: Callable[..., str | None]
    socket_capability_forbidden: Callable[[str], bool]
    workspace_session: Callable[[int, str], Any]
    workspace_player: Callable[[int, str], Any]
    get_turn: Callable[[int], Any]
    process_turn: Callable[[TurnCommand], None]


def clarification_action_and_option_ids(metadata: dict) -> tuple[dict | None, set[str]]:
    pipeline = (
        metadata.get('state_pipeline')
        if isinstance(metadata, dict) and isinstance(metadata.get('state_pipeline'), dict)
        else {}
    )
    request_payload = (
        pipeline.get('clarificationRequest')
        if isinstance(pipeline.get('clarificationRequest'), dict)
        else {}
    )
    original_action = (
        request_payload.get('originalAction')
        if isinstance(request_payload.get('originalAction'), dict)
        else None
    )
    option_ids = {
        str(option.get('itemId'))
        for option in request_payload.get('options') or []
        if isinstance(option, dict) and option.get('itemId')
    }
    return original_action, option_ids


def register_socket_clarification_events(
    socketio,
    dependencies: SocketClarificationDependencies,
) -> None:
    @socketio.on('resolve_clarification')
    def handle_resolve_clarification(data):
        dependencies.set_socket_context('resolve_clarification', data if isinstance(data, dict) else None)
        try:
            workspace_id = dependencies.socket_workspace_id(data_payload=data)
            if not workspace_id:
                emit('error', socket_error('unauthorized', 'Missing or invalid workspace token.'))
                telemetry_event(
                    'socket.resolve_clarification.unauthorized',
                    payload={'sid': request.sid},
                    severity='warning',
                )
                return
            if dependencies.socket_capability_forbidden('resolve_clarification'):
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

            connection_record = dependencies.state.connection(request.sid)
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

            session_obj = dependencies.workspace_session(session_id, workspace_id)
            player = dependencies.workspace_player(player_id, workspace_id)
            turn = dependencies.get_turn(turn_id)
            if not session_obj or not player or not turn or turn.session_id != session_id or turn.player_id != player_id:
                emit('error', socket_error('clarification_not_found', 'Clarification turn not found.'))
                return

            metadata = safe_json_loads(turn.metadata_json, {})
            original_action, valid_option_ids = clarification_action_and_option_ids(metadata)
            if not original_action:
                emit('error', socket_error('clarification_not_found', 'Clarification request metadata is missing.'))
                return
            if selected_item_id not in valid_option_ids:
                emit(
                    'error',
                    socket_error(
                        'clarification_invalid_selection',
                        'Selected item is not one of the clarification options.',
                    ),
                )
                return

            dependencies.process_turn(
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
