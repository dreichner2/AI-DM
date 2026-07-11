from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from flask import current_app
from sqlalchemy.exc import IntegrityError

from aidm_server.action_intent import apply_action_intent_to_rule_hint
from aidm_server.canon_jobs import (
    enqueue_canon_job,
    process_canon_job,
    wake_canon_job_worker,
)
from aidm_server.character_state import (
    apply_character_dc_adjustment,
    character_state_for_player,
    inventory_contains,
    requested_gold_spend,
)
from aidm_server.canon_inventory import OWNED_ITEM_ACTIONS
from aidm_server.database import commit_with_retry, db, release_clean_scoped_session, run_with_commit_retry
from aidm_server.emergent_memory import apply_immediate_state_changes
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY
from aidm_server.game_state.change_types import (
    COMBAT_STATE_CHANGE_TYPES,
    PLAYER_SNAPSHOT_CHANGE_TYPES,
    SNAPSHOT_REFRESH_CHANGE_TYPES,
    WORLD_STATE_CHANGE_TYPES,
)
from aidm_server.game_state.orchestration.turn_pipeline import (
    augment_rules_hint_with_state_packet,
    post_dm_pipeline,
    pre_dm_pipeline,
)
from aidm_server.llm import CONTEXT_VERSION, build_dm_context
from aidm_server.logging_context import set_logging_context
from aidm_server.models import Campaign, CampaignSegment, DmTurn, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.rules import RuleHint, classify_player_action
from aidm_server.services.campaign_pack_progress import update_campaign_pack_progress
from aidm_server.services.scene_state import scene_state_for_session
from aidm_server.socket_contracts import (
    new_message_payload,
    roll_required_payload,
    scene_state_payload,
    session_log_update_payload,
    socket_error_payload as socket_error,
    turn_duplicate_payload,
    turn_status_payload,
)
from aidm_server.telemetry import telemetry_event, telemetry_metric, telemetry_timing
from aidm_server.text_sanitization import strip_reasoning_blocks
from aidm_server.time_utils import utc_now
from aidm_server.turn_action_policy import TurnActionPolicy
from aidm_server.turn_control import (
    advance_structured_turn,
    conduct_turn_submission,
    turn_control_from_session,
    turn_control_update_payload,
)
from aidm_server.turn_coordinator import session_turn_coordinator
from aidm_server.turn_events import (
    DM_RESPONSE_EVENT,
    PLAYER_MESSAGE_EVENT,
    ROLL_RESOLVED_EVENT,
    record_turn_event,
)
from aidm_server.turn_narration import (
    DM_GENERATION_FAILED_MESSAGE as DM_GENERATION_FAILED_MESSAGE,
    NarrationRequest,
    NarrationResult,
    TurnNarrationDependencies,
    TurnNarrationService,
)
from aidm_server.turn_rules import (
    apply_pending_resolution_hint as default_apply_pending_resolution_hint,
    build_roll_prompt as default_build_roll_prompt,
    dc_hint_from_turn as default_dc_hint_from_turn,
    latest_pending_turn as default_latest_pending_turn,
    pending_turn_remaining_player_ids,
    pending_turn_by_id as default_pending_turn_by_id,
    pending_turn_required_player_ids,
    pending_turn_resolved_player_ids,
    response_mentions_roll_request as default_response_mentions_roll_request,
)
from aidm_server.turn_roll_policy import TurnRollPolicy
from aidm_server.turn_segments import SegmentEvaluationRequest, TurnSegmentService, default_turn_segment_service


logger = logging.getLogger(__name__)
POST_TURN_PERSIST_FAILED_MESSAGE = 'The DM response could not be fully saved. Please retry.'


def _coerce_player_id(value) -> int | None:
    try:
        player_id = int(value)
    except (TypeError, ValueError):
        return None
    return player_id if player_id > 0 else None


def _affected_player_ids_from_state_summary(
    inventory_changes: list[dict],
    character_state_changes: list[dict],
    *,
    fallback_player_id: int | None,
) -> list[int]:
    affected: set[int] = set()
    for change in [*inventory_changes, *character_state_changes]:
        if not isinstance(change, dict):
            continue
        player_id = _coerce_player_id(change.get('player_id') or change.get('playerId'))
        if player_id:
            affected.add(player_id)
    if not affected and (inventory_changes or character_state_changes):
        fallback = _coerce_player_id(fallback_player_id)
        if fallback:
            affected.add(fallback)
    return sorted(affected)


def _snapshot_refresh_flags_from_applied_changes(applied_changes: list[dict]) -> dict:
    change_types = {
        str(change.get('type') or '').strip()
        for change in applied_changes
        if isinstance(change, dict)
    }
    flags: dict[str, bool] = {}
    if change_types & WORLD_STATE_CHANGE_TYPES:
        flags['world_state_changed'] = True
    if change_types & COMBAT_STATE_CHANGE_TYPES:
        flags['combat_state_changed'] = True
    if change_types & PLAYER_SNAPSHOT_CHANGE_TYPES:
        flags['player_state_changed'] = True
    if change_types & SNAPSHOT_REFRESH_CHANGE_TYPES:
        flags['snapshot_changed'] = True
    return flags


def _snapshot_refresh_needed_from_applied_changes(applied_changes: list[dict]) -> bool:
    return bool(_snapshot_refresh_flags_from_applied_changes(applied_changes).get('snapshot_changed'))


def _state_application_event_details(
    *,
    stage: str,
    player_id: int | None,
    affected_player_ids: list[int],
    inventory_changes_applied: list[dict],
    character_state_changes_applied: list[dict],
    state_log: dict,
    applied_changes: list[dict],
    state_applied: bool | None = None,
    campaign_pack_progress_changed: bool = False,
) -> dict:
    details = {
        'stage': stage,
        'player_id': player_id,
        'affected_player_ids': affected_player_ids,
        'inventory_changes_applied': inventory_changes_applied,
        'character_state_changes_applied': character_state_changes_applied,
        'state_log': state_log,
    }
    if state_applied is not None:
        details['state_applied'] = state_applied
    details.update(_snapshot_refresh_flags_from_applied_changes(applied_changes))
    if campaign_pack_progress_changed:
        details['campaign_pack_progress_changed'] = True
        details['snapshot_changed'] = True
    return details


def _pre_narration_effects(pre_pipeline_result: dict, triggered_segments: list[dict]) -> dict:
    applied_changes = [
        change
        for key in ('immediateAppliedChanges', 'combatAppliedChanges')
        for change in (pre_pipeline_result.get(key) or [])
        if isinstance(change, dict)
    ]
    change_ids = [str(change['id']) for change in applied_changes if change.get('id') is not None]
    change_types = [
        str(change['type'])
        for change in applied_changes
        if str(change.get('type') or '').strip()
    ]
    segment_ids = [
        int(segment['segment_id'])
        for segment in triggered_segments
        if isinstance(segment, dict) and _coerce_player_id(segment.get('segment_id')) is not None
    ]
    return {
        'state_change_count': len(applied_changes),
        'state_change_ids': change_ids,
        'state_change_types': change_types,
        'triggered_segment_count': len(segment_ids),
        'triggered_segment_ids': segment_ids,
    }


@dataclass
class TurnCommand:
    sid: str
    session_id: int
    campaign_id: int
    world_id: int
    player_id: int
    user_input: str
    manual_segment_ids: set[int]
    action_intent: dict | None = None
    client_message_id: str | None = None
    state_pipeline_override: dict | None = None


@dataclass(frozen=True)
class TurnPersistenceToken:
    turn_id: int
    session_id: int
    campaign_id: int
    player_id: int | None
    client_message_id: str | None
    expected_status: str


class TurnEngine:
    def __init__(
        self,
        *,
        socketio,
        emit_fn: Callable,
        stream_fn: Callable,
        latest_pending_turn_fn: Callable[[int, int | None], DmTurn | None] | None = None,
        pending_turn_by_id_fn: Callable[[int, int, int | None], DmTurn | None] | None = None,
        dc_hint_from_turn_fn: Callable[[DmTurn | None], str | None] | None = None,
        apply_pending_resolution_hint_fn: Callable[[int, int, RuleHint, int | None], tuple[DmTurn | None, int | None]] | None = None,
        build_roll_prompt_fn: Callable[[RuleHint, int | None], str] | None = None,
        response_mentions_roll_request_fn: Callable[[str], bool] | None = None,
        active_player_ids_fn: Callable[[int], list[int]] | None = None,
        segment_service: TurnSegmentService | None = None,
    ):
        self.socketio = socketio
        self.emit = emit_fn
        self.stream_fn = stream_fn
        self.latest_pending_turn = latest_pending_turn_fn or default_latest_pending_turn
        self.pending_turn_by_id = pending_turn_by_id_fn or default_pending_turn_by_id
        self.dc_hint_from_turn = dc_hint_from_turn_fn or default_dc_hint_from_turn
        self.apply_pending_resolution_hint = apply_pending_resolution_hint_fn or default_apply_pending_resolution_hint
        self.build_roll_prompt = build_roll_prompt_fn or default_build_roll_prompt
        self.response_mentions_roll_request = response_mentions_roll_request_fn or default_response_mentions_roll_request
        self.active_player_ids = active_player_ids_fn
        self.segment_service = segment_service or default_turn_segment_service(logger=logger)

    @staticmethod
    def _release_clean_provider_session() -> None:
        """Remove a read-only scoped session before waiting on a provider."""

        release_clean_scoped_session(boundary='provider')

    @staticmethod
    def _reload_core_models(
        command: TurnCommand, turn_id: int
    ) -> tuple[DmTurn, Session, Campaign, Player]:
        turn = db.session.get(DmTurn, turn_id)
        session_obj = db.session.get(Session, command.session_id)
        campaign = db.session.get(Campaign, command.campaign_id)
        player = db.session.get(Player, command.player_id)
        if not all((turn, session_obj, campaign, player)):
            raise RuntimeError('Turn persistence context is no longer available.')
        if (
            turn.session_id != command.session_id
            or turn.campaign_id != command.campaign_id
            or turn.player_id != command.player_id
            or session_obj.campaign_id != command.campaign_id
        ):
            raise RuntimeError('Turn persistence context changed unexpectedly.')
        return turn, session_obj, campaign, player

    @staticmethod
    def _reload_submission_models(
        command: TurnCommand,
    ) -> tuple[Session, Campaign, Player]:
        session_obj = db.session.get(Session, command.session_id)
        campaign = db.session.get(Campaign, command.campaign_id)
        player = db.session.get(Player, command.player_id)
        if not all((session_obj, campaign, player)):
            raise RuntimeError('Turn submission context is no longer available.')
        if session_obj.campaign_id != command.campaign_id:
            raise RuntimeError('Turn submission context changed unexpectedly.')
        return session_obj, campaign, player

    @staticmethod
    def _persistence_token(
        *,
        turn: DmTurn,
    ) -> TurnPersistenceToken:
        return TurnPersistenceToken(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            campaign_id=turn.campaign_id,
            player_id=turn.player_id,
            client_message_id=turn.client_message_id,
            expected_status=str(turn.status or 'processing'),
        )

    @staticmethod
    def _reload_persistence_context(
        token: TurnPersistenceToken,
    ) -> tuple[DmTurn, Campaign]:
        turn = db.session.get(DmTurn, token.turn_id)
        session_obj = db.session.get(Session, token.session_id)
        campaign = db.session.get(Campaign, token.campaign_id)
        if not all((turn, session_obj, campaign)):
            raise RuntimeError(
                'Turn state changed while narration was being generated.'
            )
        if (
            turn.session_id != token.session_id
            or turn.campaign_id != token.campaign_id
            or turn.player_id != token.player_id
            or turn.client_message_id != token.client_message_id
            or session_obj.campaign_id != token.campaign_id
            or turn.status != token.expected_status
            or turn.dm_output is not None
            or turn.completed_at is not None
        ):
            raise RuntimeError(
                'Turn state changed while narration was being generated.'
            )
        return turn, campaign

    @staticmethod
    def _find_duplicate_turn(command: TurnCommand) -> DmTurn | None:
        if not command.client_message_id:
            return None

        duplicate_turn = (
            DmTurn.query.filter(
                DmTurn.session_id == command.session_id,
                DmTurn.player_id == command.player_id,
                DmTurn.client_message_id == command.client_message_id,
            )
            .order_by(DmTurn.turn_id.desc())
            .first()
        )
        if duplicate_turn:
            return duplicate_turn

        # Rows created before migration 0027 only carry the idempotency key in
        # metadata. Keep that compatibility path scoped to rows without the
        # dedicated column; all new writes are protected by the unique index.
        legacy_candidates = (
            DmTurn.query.filter(
                DmTurn.session_id == command.session_id,
                DmTurn.player_id == command.player_id,
                DmTurn.client_message_id.is_(None),
                DmTurn.metadata_json.contains(command.client_message_id),
            )
            .order_by(DmTurn.turn_id.desc())
            .all()
        )
        for candidate in legacy_candidates:
            metadata = safe_json_loads(candidate.metadata_json, {})
            if isinstance(metadata, dict) and metadata.get('client_message_id') == command.client_message_id:
                return candidate
        return None

    def _emit_duplicate_turn(self, command: TurnCommand, duplicate_turn: DmTurn, *, detected_by: str) -> None:
        if not command.client_message_id:
            return
        self.emit(
            'turn_duplicate',
            turn_duplicate_payload(
                command.session_id,
                duplicate_turn.turn_id,
                command.client_message_id,
            ),
        )
        self.emit(
            'session_log_update',
            session_log_update_payload(command.session_id, duplicate_turn.turn_id),
            room=str(command.session_id),
        )
        telemetry_event(
            'socket.send_message.duplicate_ignored',
            payload={
                'sid': command.sid,
                'session_id': command.session_id,
                'player_id': command.player_id,
                'client_message_id': command.client_message_id,
                'detected_by': detected_by,
            },
        )

    def _conduct_turn_submission(self, command: TurnCommand, session_obj: Session) -> bool:
        action_kind = (
            str(command.action_intent.get('kind')).strip()
            if isinstance(command.action_intent, dict) and command.action_intent.get('kind') is not None
            else ''
        )
        has_pending_roll = (
            action_kind == 'roll'
            and self.latest_pending_turn(command.session_id, command.player_id) is not None
        )
        active_player_ids = []
        if self.active_player_ids:
            active_player_ids = [
                int(player_id)
                for player_id in self.active_player_ids(command.session_id)
                if player_id
            ]
        allowed, block_reason, turn_control, changed, decision = (
            conduct_turn_submission(
                session_obj,
                player_id=command.player_id,
                message=command.user_input,
                action_intent=command.action_intent,
                has_pending_roll=has_pending_roll,
                active_player_ids=active_player_ids,
                before_helper_call=self._release_clean_provider_session,
                reload_session_after_helper=lambda: db.session.get(
                    Session, command.session_id
                ),
            )
        )
        if not allowed:
            self.emit(
                'error',
                socket_error(
                    'turn_out_of_order',
                    block_reason or 'It is not your turn to act.',
                    {'turn_control': turn_control},
                ),
            )
            telemetry_event(
                'socket.send_message.turn_out_of_order',
                payload={
                    'sid': command.sid,
                    'session_id': command.session_id,
                    'player_id': command.player_id,
                    'turn_control': turn_control,
                },
                severity='warning',
            )
            return False
        if changed:
            commit_with_retry(label='turn conductor decision')
            self.emit(
                'turn_control_updated',
                turn_control_update_payload(command.session_id, turn_control),
                room=str(command.session_id),
            )
            telemetry_event(
                'socket.turn_conductor.decision_applied',
                payload={
                    'sid': command.sid,
                    'session_id': command.session_id,
                    'player_id': command.player_id,
                    'decision': decision,
                    'turn_control': turn_control,
                },
            )
        return True

    def _active_player_ids_for_session(self, session_id: int) -> set[int]:
        if not self.active_player_ids:
            return set()
        active_ids: set[int] = set()
        for player_id in self.active_player_ids(session_id):
            try:
                parsed = int(player_id)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                active_ids.add(parsed)
        return active_ids

    @staticmethod
    def _session_turn_number(session_id: int) -> int:
        return int(DmTurn.query.filter_by(session_id=session_id).count() or 0) + 1

    @staticmethod
    def _clarification_resume_turn_id(command: TurnCommand) -> int | None:
        override = command.state_pipeline_override if isinstance(command.state_pipeline_override, dict) else {}
        try:
            turn_id = int(override.get('resolvedClarificationTurnId') or 0)
        except (TypeError, ValueError):
            return None
        return turn_id if turn_id > 0 else None

    @staticmethod
    def _clarification_selected_item_ids(command: TurnCommand) -> dict:
        override = command.state_pipeline_override if isinstance(command.state_pipeline_override, dict) else {}
        selected = override.get('selectedItemIds')
        return selected if isinstance(selected, dict) else {}

    @staticmethod
    def _dm_response_sentences(text: str) -> list[str]:
        return TurnRollPolicy.response_sentences(text)

    @staticmethod
    def _dm_response_explains_no_roll_needed(text: str) -> bool:
        return TurnRollPolicy.response_explains_no_roll_needed(text)

    @staticmethod
    def _dm_response_requests_group_roll(text: str) -> bool:
        return TurnRollPolicy.response_requests_group_roll(text)

    def _dm_response_requests_roll(self, text: str) -> bool:
        return self.response_mentions_roll_request(text) or self._dm_response_requests_group_roll(text)

    @staticmethod
    def _roll_type_from_dm_response(text: str, fallback: str | None = None) -> str:
        return TurnRollPolicy.roll_type_from_response(text, fallback)

    def _candidate_roll_gate_player_ids(self, session_id: int, campaign: Campaign, fallback_player_id: int | None) -> list[int]:
        active_ids = []
        if self.active_player_ids:
            active_ids = [player_id for player_id in self.active_player_ids(session_id) if player_id]
        if active_ids:
            return list(dict.fromkeys(active_ids))

        query = Player.query.filter_by(workspace_id=campaign.workspace_id)
        players = query.order_by(Player.created_at.asc(), Player.player_id.asc()).limit(12).all()
        player_ids = [
            player.player_id
            for player in players
            if TurnActionPolicy.player_is_available_for_campaign(player, campaign)
        ]
        if player_ids:
            return list(dict.fromkeys(player_ids))
        return [fallback_player_id] if fallback_player_id else []

    def _roll_gate_for_turn(self, turn: DmTurn, campaign: Campaign, dm_response_text: str) -> dict | None:
        dm_requested_roll = self._dm_response_requests_roll(dm_response_text)
        group_player_ids = (
            self._candidate_roll_gate_player_ids(turn.session_id, campaign, turn.player_id)
            if self._dm_response_requests_group_roll(dm_response_text)
            else []
        )
        return TurnRollPolicy.build_roll_gate(
            turn=turn,
            dm_response_text=dm_response_text,
            response_requests_roll=dm_requested_roll,
            group_player_ids=group_player_ids,
        )

    @staticmethod
    def _player_names_by_id(player_ids: list[int]) -> dict[int, str]:
        if not player_ids:
            return {}
        players = Player.query.filter(Player.player_id.in_(player_ids)).all()
        return {player.player_id: player.character_name or player.name or f'Player {player.player_id}' for player in players}

    def _pending_roll_context(self, pending_turn: DmTurn) -> dict:
        required_player_ids = pending_turn_required_player_ids(pending_turn)
        remaining_player_ids = pending_turn_remaining_player_ids(pending_turn)
        relevant_player_ids = list(dict.fromkeys([
            *(required_player_ids or []),
            *(remaining_player_ids or []),
            *([pending_turn.player_id] if pending_turn.player_id else []),
        ]))
        player_names = self._player_names_by_id(relevant_player_ids)
        pending_summary = (pending_turn.player_input or '').strip().replace('\n', ' ')
        if len(pending_summary) > 140:
            pending_summary = f'{pending_summary[:137]}...'
        pending_player_name = player_names.get(
            pending_turn.player_id,
            f'Player {pending_turn.player_id}' if pending_turn.player_id else 'Another player',
        )
        remaining_player_names = [
            player_names.get(player_id, f'Player {player_id}')
            for player_id in remaining_player_ids
        ]
        return {
            'pending_turn_id': pending_turn.turn_id,
            'pending_player_id': pending_turn.player_id,
            'pending_player_name': pending_player_name,
            'pending_rule_type': pending_turn.rule_type or 'check',
            'pending_turn_summary': pending_summary,
            'remaining_player_ids': remaining_player_ids,
            'remaining_player_names': remaining_player_names,
            'required_player_ids': required_player_ids,
        }

    @staticmethod
    def _current_scene_npc_target(session_obj: Session, target: dict) -> dict | None:
        return TurnActionPolicy.current_scene_npc_target(session_obj, target)

    @classmethod
    def _current_scene_npc_target_from_text(cls, session_obj: Session, text: str) -> dict | None:
        return TurnActionPolicy.current_scene_npc_target_from_text(session_obj, text)

    def _prepare_interaction_target(self, command: TurnCommand, campaign: Campaign, session_obj: Session) -> bool:
        action_intent = command.action_intent
        if not isinstance(action_intent, dict) or action_intent.get('kind') != 'interact':
            return True
        target = action_intent.get('target')
        if not isinstance(target, dict):
            return True
        target_kind = str(target.get('kind') or '').strip().lower()
        target_npc_id = str(target.get('npc_id') or target.get('npcId') or '').strip()
        if target_kind == 'npc' or target_npc_id:
            npc_target = self._current_scene_npc_target(session_obj, target)
            if not npc_target:
                self.emit(
                    'error',
                    socket_error(
                        'interaction_target_invalid',
                        'Target NPC is not active in the current scene.',
                        {'target_npc_id': target_npc_id},
                    ),
                )
                telemetry_event(
                    'socket.send_message.interaction_target_invalid',
                    payload={
                        'sid': command.sid,
                        'session_id': command.session_id,
                        'player_id': command.player_id,
                        'target_npc_id': target_npc_id,
                        'campaign_id': campaign.campaign_id,
                    },
                    severity='warning',
                )
                return False
            target['kind'] = 'npc'
            target['npc_id'] = npc_target['npc_id']
            target['character_name'] = npc_target['character_name']
            target['player_name'] = npc_target['player_name']
            target.pop('player_id', None)
            return True

        text_npc_target = self._current_scene_npc_target_from_text(session_obj, command.user_input)
        if text_npc_target:
            target['kind'] = 'npc'
            target['npc_id'] = text_npc_target['npc_id']
            target['character_name'] = text_npc_target['character_name']
            target['player_name'] = text_npc_target['player_name']
            target.pop('player_id', None)
            telemetry_event(
                'socket.send_message.interaction_target_reconciled_to_npc',
                payload={
                    'sid': command.sid,
                    'session_id': command.session_id,
                    'player_id': command.player_id,
                    'npc_id': text_npc_target['npc_id'],
                    'campaign_id': campaign.campaign_id,
                },
            )
            return True

        target_player_id = target.get('player_id') if isinstance(target, dict) else None
        target_player = db.session.get(Player, target_player_id) if isinstance(target_player_id, int) else None
        if not TurnActionPolicy.player_is_available_for_campaign(target_player, campaign):
            self.emit(
                'error',
                socket_error(
                    'interaction_target_invalid',
                    'Target player is not available in this workspace.',
                    {'target_player_id': target_player_id},
                ),
            )
            telemetry_event(
                'socket.send_message.interaction_target_invalid',
                payload={
                    'sid': command.sid,
                    'session_id': command.session_id,
                    'player_id': command.player_id,
                    'target_player_id': target_player_id,
                    'campaign_id': campaign.campaign_id,
                },
                severity='warning',
            )
            return False
        active_ids = self._active_player_ids_for_session(command.session_id)
        if active_ids and target_player.player_id not in active_ids:
            self.emit(
                'error',
                socket_error(
                    'interaction_target_invalid',
                    'Target player is not active in this session.',
                    {'target_player_id': target_player_id},
                ),
            )
            telemetry_event(
                'socket.send_message.interaction_target_inactive',
                payload={
                    'sid': command.sid,
                    'session_id': command.session_id,
                    'player_id': command.player_id,
                    'target_player_id': target_player_id,
                    'campaign_id': campaign.campaign_id,
                },
                severity='warning',
            )
            return False
        target['kind'] = 'player'
        target['character_name'] = target_player.character_name
        target['player_name'] = target_player.name
        return True

    def _validate_character_limits(self, command: TurnCommand, player: Player) -> bool:
        action_intent = command.action_intent if isinstance(command.action_intent, dict) else {}
        item_cost_gold = 0
        if action_intent.get('kind') == 'item':
            item = action_intent.get('item') if isinstance(action_intent.get('item'), dict) else {}
            item_name = str(item.get('name') or '').strip()
            quantity = int(item.get('quantity') or 1)
            inventory_action = str(action_intent.get('inventory_action') or 'use').strip().lower()
            item_cost_gold = int(action_intent.get('cost_gold') or 0)
            if inventory_action in OWNED_ITEM_ACTIONS and not inventory_contains(player, item_name, quantity):
                self.emit(
                    'error',
                    socket_error(
                        'item_not_available',
                        f'You do not have {item_name or "that item"}.',
                        {'item_name': item_name, 'quantity': quantity},
                    ),
                )
                telemetry_event(
                    'socket.send_message.item_not_available',
                    payload={
                        'sid': command.sid,
                        'session_id': command.session_id,
                        'player_id': command.player_id,
                        'item_name': item_name,
                    },
                    severity='warning',
                )
                return False

        spend = max(item_cost_gold if action_intent.get('inventory_action') == 'buy' else 0, requested_gold_spend(command.user_input))
        if spend:
            state = character_state_for_player(player)
            gold = int(state.get('gold') or 0)
            if spend > gold:
                self.emit(
                    'error',
                    socket_error(
                        'insufficient_gold',
                        f'{player.character_name} has {gold} gold and cannot spend {spend}.',
                        {'gold': gold, 'attempted_spend': spend},
                    ),
                )
                telemetry_event(
                    'socket.send_message.insufficient_gold',
                    payload={
                        'sid': command.sid,
                        'session_id': command.session_id,
                        'player_id': command.player_id,
                        'gold': gold,
                        'attempted_spend': spend,
                    },
                    severity='warning',
                )
                return False
        return True

    def _harmful_pvp_target(self, command: TurnCommand, campaign: Campaign) -> Player | None:
        if TurnActionPolicy.is_admin_override(command.action_intent):
            return None
        text = str(command.user_input or '')
        if not TurnActionPolicy.contains_harmful_pvp_action(text):
            return None
        active_ids = self._active_player_ids_for_session(command.session_id)
        query = Player.query.filter_by(workspace_id=campaign.workspace_id, campaign_id=campaign.campaign_id)
        candidates = [
            player
            for player in query.order_by(Player.player_id.asc()).all()
            if player.player_id != command.player_id and (not active_ids or player.player_id in active_ids)
        ]
        action_intent = command.action_intent if isinstance(command.action_intent, dict) else {}
        target = action_intent.get('target') if isinstance(action_intent.get('target'), dict) else {}
        target_player_id = _coerce_player_id(target.get('player_id')) if isinstance(target, dict) else None
        if action_intent.get('kind') == 'interact' and target_player_id:
            target_player = next((player for player in candidates if player.player_id == target_player_id), None)
            if target_player:
                return target_player
        for player in candidates:
            if TurnActionPolicy.harmful_text_targets_player(text, player):
                return player
        return None

    @staticmethod
    def _pvp_rules_payload(target_player: Player | None) -> dict | None:
        return TurnActionPolicy.pvp_rules_payload(target_player)

    @staticmethod
    def _apply_pvp_rule_hint(rule_hint: RuleHint, pvp_payload: dict | None) -> RuleHint:
        return TurnActionPolicy.apply_pvp_rule_hint(rule_hint, pvp_payload)

    @staticmethod
    def _pre_dm_roll_veto(pre_pipeline_result: dict, rules_hint_payload: dict) -> dict | None:
        if not rules_hint_payload.get('requires_roll'):
            return None
        if rules_hint_payload.get('roll_value') is not None:
            return None
        if rules_hint_payload.get('resolved_turn_id') is not None:
            return None
        if rules_hint_payload.get('target_pending_turn_id') is not None:
            return None
        if isinstance(rules_hint_payload.get('pvp'), dict):
            return None

        pre_extraction = pre_pipeline_result.get('preExtraction')
        pre_extraction = pre_extraction if isinstance(pre_extraction, dict) else {}
        roll_requirement = pre_extraction.get('rollRequirement')
        if not isinstance(roll_requirement, dict) or roll_requirement.get('requiresRoll') is not False:
            return None

        try:
            confidence = float(roll_requirement.get('confidence') or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < 0.75:
            return None

        pre_validation = pre_pipeline_result.get('preValidation')
        pre_validation = pre_validation if isinstance(pre_validation, dict) else {}
        if pre_validation.get('pendingRolls'):
            return None
        for result in pre_validation.get('validatedActions') or []:
            if not isinstance(result, dict):
                continue
            if result.get('status') not in {'valid', 'pending'}:
                continue
            action = result.get('normalizedAction') if isinstance(result.get('normalizedAction'), dict) else {}
            original = result.get('originalAction') if isinstance(result.get('originalAction'), dict) else {}
            action_type = str(action.get('type') or original.get('type') or '')
            if action_type == 'combat.attack':
                return None

        reason = str(roll_requirement.get('reason') or '').strip() or 'Pre-DM helper marked this as no-roll context.'
        return {
            'source': 'pre_dm_helper',
            'reason': reason,
            'confidence': confidence,
            'original_rule_type': rules_hint_payload.get('roll_type'),
            'original_reason': rules_hint_payload.get('reason'),
        }

    @staticmethod
    def _clear_turn_roll_requirement_from_pre_dm_veto(
        *,
        turn: DmTurn,
        rules_hint_payload: dict,
        veto: dict,
        pre_pipeline_result: dict,
    ) -> None:
        turn.requires_roll = False
        turn.rule_type = None
        turn.outcome_status = 'resolved'

        rules_hint_payload['requires_roll'] = False
        rules_hint_payload['roll_type'] = None
        rules_hint_payload['dc_hint'] = None
        rules_hint_payload['outcome_deferred'] = False
        rules_hint_payload['reason'] = 'Pre-DM helper marked no roll needed'
        rules_hint_payload.pop('roll_gate', None)
        rules_hint_payload.pop('remaining_player_ids', None)
        rules_hint_payload['roll_requirement_cleared'] = veto

        rules_hint = safe_json_loads(turn.rules_hint, {})
        rules_hint = rules_hint if isinstance(rules_hint, dict) else {}
        rules_hint.update(
            {
                'requires_roll': False,
                'roll_type': None,
                'dc_hint': None,
                'outcome_deferred': False,
                'reason': 'Pre-DM helper marked no roll needed',
                'roll_requirement_cleared': veto,
            }
        )
        rules_hint.pop('roll_gate', None)
        rules_hint.pop('remaining_player_ids', None)
        turn.rules_hint = safe_json_dumps(rules_hint, {})

        metadata = safe_json_loads(turn.metadata_json, {})
        metadata = metadata if isinstance(metadata, dict) else {}
        metadata.pop('roll_gate', None)
        metadata['roll_requirement_cleared'] = veto
        state_pipeline = metadata.get(STATE_PIPELINE_METADATA_KEY)
        if isinstance(state_pipeline, dict):
            state_pipeline['rollRequirementCleared'] = veto
            dm_context = state_pipeline.get('dmContextPacket')
            if isinstance(dm_context, dict):
                dm_context['pendingRolls'] = []
                dm_context['rollRequirementCleared'] = veto
            pre_validation = state_pipeline.get('preDmValidation')
            if isinstance(pre_validation, dict) and pre_validation.get('pendingRolls'):
                pre_validation['pendingRolls'] = []
        turn.metadata_json = safe_json_dumps(metadata, {})

        dm_context_packet = pre_pipeline_result.get('dmContextPacket')
        if isinstance(dm_context_packet, dict):
            dm_context_packet['pendingRolls'] = []
            dm_context_packet['rollRequirementCleared'] = veto

    def process(self, command: TurnCommand):
        with session_turn_coordinator.serialized(command.session_id) as wait_ms:
            if wait_ms >= 1.0:
                telemetry_timing(
                    'socket.turn_queue_wait_ms',
                    wait_ms,
                    tags={'session_id': command.session_id, 'campaign_id': command.campaign_id},
                )
            return self._process_serialized(command)

    def _process_serialized(self, command: TurnCommand):
        session_obj = db.session.get(Session, command.session_id)
        if not session_obj:
            self.emit('error', socket_error('session_not_found', 'Session not found'))
            telemetry_event(
                'socket.send_message.session_not_found',
                payload={'sid': command.sid, 'session_id': command.session_id},
                severity='warning',
            )
            return

        if session_obj.campaign_id != command.campaign_id:
            self.emit('error', socket_error('campaign_mismatch', 'Session does not belong to this campaign'))
            telemetry_event(
                'socket.send_message.campaign_mismatch',
                payload={'sid': command.sid, 'session_id': command.session_id, 'campaign_id': command.campaign_id},
                severity='warning',
            )
            return

        campaign = db.session.get(Campaign, command.campaign_id)
        if not campaign:
            self.emit('error', socket_error('campaign_not_found', 'Campaign not found'))
            telemetry_event(
                'socket.send_message.campaign_not_found',
                payload={'sid': command.sid, 'campaign_id': command.campaign_id},
                severity='warning',
            )
            return

        player = db.session.get(Player, command.player_id)
        if not player:
            self.emit('error', socket_error('invalid_player', 'Invalid player ID'))
            telemetry_event(
                'socket.send_message.invalid_player',
                payload={'sid': command.sid, 'player_id': command.player_id},
                severity='warning',
            )
            return

        if not TurnActionPolicy.player_is_available_for_campaign(player, campaign):
            self.emit('error', socket_error('campaign_mismatch', 'Player is not available in this campaign'))
            telemetry_event(
                'socket.send_message.campaign_mismatch',
                payload={'sid': command.sid, 'player_id': command.player_id, 'campaign_id': command.campaign_id},
                severity='warning',
            )
            return

        if command.client_message_id:
            duplicate_turn = self._find_duplicate_turn(command)
            if duplicate_turn:
                self._emit_duplicate_turn(command, duplicate_turn, detected_by='preflight')
                return

        if not self._conduct_turn_submission(command, session_obj):
            return
        try:
            session_obj, campaign, player = self._reload_submission_models(command)
        except RuntimeError:
            self.emit(
                'error',
                socket_error(
                    'turn_persist_failed',
                    'The session changed while preparing the turn. Please retry.',
                ),
            )
            return

        if not self._prepare_interaction_target(command, campaign, session_obj):
            return
        if not self._validate_character_limits(command, player):
            return
        pvp_target = self._harmful_pvp_target(command, campaign)
        pvp_payload = self._pvp_rules_payload(pvp_target)

        player_label = player.character_name
        is_admin_override = TurnActionPolicy.is_admin_override(command.action_intent)
        rules_engine_enabled = bool(current_app.config.get('AIDM_RULES_ENGINE_ENABLED', True))
        rule_hint: RuleHint = (
            classify_player_action(command.user_input)
            if rules_engine_enabled
            else RuleHint(
                requires_roll=False,
                roll_type=None,
                dc_hint=None,
                reason='Rules engine disabled',
                confidence=1.0,
                roll_value=None,
                outcome_deferred=False,
            )
        )
        rule_hint = apply_action_intent_to_rule_hint(command.action_intent, rule_hint)
        rule_hint = apply_character_dc_adjustment(rule_hint, player)
        rule_hint = self._apply_pvp_rule_hint(rule_hint, pvp_payload)

        roll_target_pending_turn_id = None
        if command.action_intent and command.action_intent.get('kind') == 'roll':
            raw_roll = command.action_intent.get('roll')
            if isinstance(raw_roll, dict):
                roll_target_pending_turn_id = raw_roll.get('target_pending_turn_id')

        pending_turn_before = None
        if not is_admin_override:
            pending_turn_before = (
                self.pending_turn_by_id(command.session_id, command.player_id, roll_target_pending_turn_id)
                if roll_target_pending_turn_id is not None
                else self.latest_pending_turn(command.session_id, command.player_id)
            )
        any_pending_turn = None if is_admin_override else self.latest_pending_turn(command.session_id, None)
        if roll_target_pending_turn_id is not None and rule_hint.roll_value is not None and not pending_turn_before:
            self.emit(
                'error',
                socket_error(
                    'pending_roll_target_not_found',
                    'The selected pending check is no longer available. Refresh and choose another target.',
                    {
                        'session_id': command.session_id,
                        'pending_turn_id': roll_target_pending_turn_id,
                    },
                ),
            )
            telemetry_event(
                'socket.send_message.pending_roll_target_not_found',
                payload={
                    'sid': command.sid,
                    'session_id': command.session_id,
                    'player_id': command.player_id,
                    'pending_turn_id': roll_target_pending_turn_id,
                },
                severity='warning',
            )
            return

        if pending_turn_before and rule_hint.roll_value is None:
            pending_rule_type = pending_turn_before.rule_type or 'check'
            pending_dc_hint = self.dc_hint_from_turn(pending_turn_before)
            roll_required = roll_required_payload(
                session_id=command.session_id,
                pending_turn_id=pending_turn_before.turn_id,
                rule_type=pending_rule_type,
                dc_hint=pending_dc_hint,
                prompt=self.build_roll_prompt(
                    RuleHint(
                        requires_roll=True,
                        roll_type=pending_rule_type,
                        dc_hint=pending_dc_hint,
                        reason='Pending roll required',
                        confidence=1.0,
                        roll_value=None,
                        outcome_deferred=True,
                    ),
                    pending_turn_id=pending_turn_before.turn_id,
                ),
            )
            self.emit('roll_required', roll_required)
            self.emit(
                'error',
                socket_error(
                    'roll_required',
                    'Resolve the pending check before taking a new action.',
                    roll_required,
                ),
            )
            telemetry_event(
                'socket.send_message.roll_required',
                payload={
                    'sid': command.sid,
                    'session_id': command.session_id,
                    'pending_turn_id': pending_turn_before.turn_id,
                    'rule_type': pending_rule_type,
                },
                severity='warning',
            )
            return

        if not is_admin_override and any_pending_turn is not None and not pending_turn_before:
            if rule_hint.roll_value is None:
                remaining_player_ids = pending_turn_remaining_player_ids(any_pending_turn)
                if len(pending_turn_required_player_ids(any_pending_turn)) > 1:
                    pending_context = self._pending_roll_context(any_pending_turn)
                    remaining_names = pending_context.get('remaining_player_names') or []
                    waiting_on = ', '.join(remaining_names) if remaining_names else 'all requested players'
                    self.emit(
                        'error',
                        socket_error(
                            'pending_rolls_block_story',
                            f'The story is waiting on {waiting_on} to roll before it can move forward.',
                            {
                                'session_id': command.session_id,
                                **pending_context,
                                'remaining_player_ids': remaining_player_ids,
                            },
                        ),
                    )
                    telemetry_event(
                        'socket.send_message.pending_rolls_block_story',
                        payload={
                            'sid': command.sid,
                            'session_id': command.session_id,
                            'player_id': command.player_id,
                            'pending_turn_id': any_pending_turn.turn_id,
                            'remaining_player_ids': remaining_player_ids,
                        },
                        severity='warning',
                    )
                    return

            if rule_hint.roll_value is not None and any_pending_turn.player_id != command.player_id:
                pending_context = self._pending_roll_context(any_pending_turn)
                pending_name = pending_context.get('pending_player_name') or 'Another player'
                pending_rule = pending_context.get('pending_rule_type') or 'check'
                pending_summary = pending_context.get('pending_turn_summary') or 'their last action'
                message = (
                    f'{pending_name} has an unresolved {pending_rule} from turn '
                    f'{any_pending_turn.turn_id}: "{pending_summary}". Your roll cannot resolve it.'
                )
                self.emit(
                    'error',
                    socket_error(
                        'pending_roll_not_owned',
                        message,
                        {
                            'session_id': command.session_id,
                            **pending_context,
                        },
                    ),
                )
                telemetry_event(
                    'socket.send_message.pending_roll_not_owned',
                    payload={
                        'sid': command.sid,
                        'session_id': command.session_id,
                        'player_id': command.player_id,
                        'pending_turn_id': any_pending_turn.turn_id,
                        'pending_player_id': any_pending_turn.player_id,
                    },
                    severity='warning',
                )
                return

        pending_turn_to_resolve, resolved_turn_id = self.apply_pending_resolution_hint(
            command.session_id,
            command.player_id,
            rule_hint,
            roll_target_pending_turn_id,
        )
        resolved_clarification_turn_id = self._clarification_resume_turn_id(command)
        session_turn_number = self._session_turn_number(command.session_id)
        turn_control_payload = turn_control_from_session(session_obj)
        rules_hint_payload = {
            'requires_roll': rule_hint.requires_roll,
            'roll_type': rule_hint.roll_type,
            'dc_hint': rule_hint.dc_hint,
            'reason': rule_hint.reason,
            'confidence': rule_hint.confidence,
            'roll_value': rule_hint.roll_value,
            'outcome_deferred': rule_hint.outcome_deferred,
            'resolved_turn_id': resolved_turn_id,
            'target_pending_turn_id': roll_target_pending_turn_id,
            'resolved_clarification_turn_id': resolved_clarification_turn_id,
            'turn_number': session_turn_number,
            'turn_control': turn_control_payload,
        }
        if pvp_payload:
            rules_hint_payload['pvp'] = pvp_payload
        if resolved_clarification_turn_id:
            rules_hint_payload['clarification_resume'] = {
                'resolved_turn_id': resolved_clarification_turn_id,
                'selected_item_ids': self._clarification_selected_item_ids(command),
            }

        turn = DmTurn(
            session_id=command.session_id,
            campaign_id=command.campaign_id,
            player_id=command.player_id,
            player_input=command.user_input,
            requires_roll=rule_hint.requires_roll,
            rule_type=rule_hint.roll_type,
            confidence=rule_hint.confidence,
            roll_value=rule_hint.roll_value,
            outcome_status='deferred' if rule_hint.outcome_deferred else 'resolved',
            rules_hint=safe_json_dumps(rules_hint_payload, {}),
            context_version=CONTEXT_VERSION,
            status='processing',
            client_message_id=command.client_message_id,
            metadata_json=safe_json_dumps(
                {
                    'speaker': player_label,
                    'resolved_turn_id': resolved_turn_id,
                    'turn_number': session_turn_number,
                    'action_intent': command.action_intent,
                    'client_message_id': command.client_message_id,
                    'turn_control': turn_control_payload,
                    'pvp': pvp_payload,
                    'resolved_clarification_turn_id': resolved_clarification_turn_id,
                    'clarification_resume': (
                        {
                            'resolved_turn_id': resolved_clarification_turn_id,
                            'selected_item_ids': self._clarification_selected_item_ids(command),
                        }
                        if resolved_clarification_turn_id
                        else None
                    ),
                },
                {},
            ),
        )

        start_time = time.perf_counter()
        incoming_save_started = time.perf_counter()
        incoming_result = self._persist_incoming_turn(
            turn,
            player_label,
            command,
            rule_hint,
            pending_turn_to_resolve,
            resolved_turn_id,
            session_turn_number,
        )
        if not incoming_result.get('ok'):
            return
        turn_id = int(turn.turn_id)
        self._record_phase_timing(
            'incoming_db_save',
            incoming_save_started,
            campaign_id=command.campaign_id,
            session_id=command.session_id,
        )
        self._emit_turn_status(command.session_id, turn.turn_id, 'received')

        self.emit(
            'new_message',
            new_message_payload(
                message=command.user_input,
                speaker=player_label,
                turn_id=turn.turn_id,
                requires_roll=rule_hint.requires_roll,
                rules_hint=rules_hint_payload,
                context_version=CONTEXT_VERSION,
                action_intent=command.action_intent,
                client_message_id=command.client_message_id,
                turn_number=session_turn_number,
            ),
            room=str(command.session_id),
        )

        if incoming_result.get('waiting_for_rolls'):
            self._record_phase_timing(
                'incoming_db_save',
                incoming_save_started,
                campaign_id=command.campaign_id,
                session_id=command.session_id,
            )
            self._emit_roll_gate_waiting(
                turn=turn,
                campaign=campaign,
                command=command,
                remaining_player_ids=incoming_result.get('remaining_player_ids') or [],
                session_turn_number=session_turn_number,
            )
            return

        pre_pipeline_result: dict = {}
        state_pipeline_started = time.perf_counter()
        try:
            active_player_ids = []
            if self.active_player_ids:
                active_player_ids = [player_id for player_id in self.active_player_ids(command.session_id) if player_id]
            pre_pipeline_result = pre_dm_pipeline(
                turn=turn,
                session_obj=session_obj,
                campaign=campaign,
                player=player,
                player_message=command.user_input,
                action_intent=command.action_intent,
                selected_item_ids=(
                    command.state_pipeline_override.get('selectedItemIds')
                    if isinstance(command.state_pipeline_override, dict)
                    else None
                ),
                declared_actions_override=(
                    command.state_pipeline_override.get('declaredActions')
                    if isinstance(command.state_pipeline_override, dict)
                    else None
                ),
                active_player_ids=active_player_ids,
                before_helper_call=self._release_clean_provider_session,
            )
            turn, session_obj, campaign, player = self._reload_core_models(
                command, turn_id
            )
            commit_with_retry(label='pre-DM state pipeline')
            self._record_phase_timing(
                'state_pre_dm',
                state_pipeline_started,
                campaign_id=command.campaign_id,
                session_id=command.session_id,
            )
        except Exception as exc:
            db.session.rollback()
            try:
                turn, session_obj, campaign, player = self._reload_core_models(
                    command, turn_id
                )
            except RuntimeError:
                self.emit(
                    'error',
                    socket_error(
                        'turn_persist_failed', 'Failed to reload player action state.'
                    ),
                )
                telemetry_event(
                    'socket.send_message.turn_reload_failed',
                    payload={'session_id': command.session_id, 'turn_id': turn_id},
                    severity='error',
                )
                return
            logger.warning('Pre-DM state pipeline failed: %s', str(exc))
            telemetry_event(
                'socket.state_pipeline.pre_dm_failed',
                payload={
                    'session_id': command.session_id,
                    'turn_id': turn_id,
                    'error': str(exc),
                },
                severity='warning',
            )
            rules_hint_payload['state_pipeline_warning'] = 'State validation failed; avoid committing inventory/HP/currency changes.'
        else:
            clarification_requests = pre_pipeline_result.get('clarificationRequests') or []
            if clarification_requests:
                self._emit_clarification_request(
                    session_id=command.session_id,
                    turn_id=turn.turn_id,
                    player_id=command.player_id,
                    player_message=command.user_input,
                    clarification_requests=clarification_requests,
                )
                return
            pre_dm_roll_veto = self._pre_dm_roll_veto(pre_pipeline_result, rules_hint_payload)
            if pre_dm_roll_veto:
                self._clear_turn_roll_requirement_from_pre_dm_veto(
                    turn=turn,
                    rules_hint_payload=rules_hint_payload,
                    veto=pre_dm_roll_veto,
                    pre_pipeline_result=pre_pipeline_result,
                )
            rules_hint_payload = augment_rules_hint_with_state_packet(
                rules_hint_payload,
                pre_pipeline_result.get('dmContextPacket') or {},
            )
            turn.rules_hint = safe_json_dumps(rules_hint_payload, {})
            commit_with_retry(label='pre-narration rules state')

        triggered_segments = self._evaluate_segments(
            turn=turn,
            campaign=campaign,
            command=command,
            allowed_trigger_types={'keywords'},
            include_manual=False,
        )
        for segment_payload in triggered_segments:
            self.emit('segment_triggered', segment_payload, room=str(command.session_id))

        # The segment service commits when it evaluates. This explicit boundary
        # also covers the disabled/no-op path before context construction.
        commit_with_retry(label='pre-narration boundary')
        turn, session_obj, campaign, player = self._reload_core_models(command, turn_id)
        narration_request = NarrationRequest(
            session_id=turn.session_id,
            campaign_id=campaign.campaign_id,
            turn_id=turn.turn_id,
            player_id=turn.player_id,
            requires_roll=turn.requires_roll,
            roll_value=turn.roll_value,
            rule_type=turn.rule_type,
            confidence=turn.confidence,
            serialized_rules_hint=turn.rules_hint,
            player_label=player_label,
            world_id=campaign.world_id,
            user_input=command.user_input,
            model_user_input=TurnActionPolicy.model_input_for_action(
                command.user_input,
                command.action_intent,
                player_label,
                pvp_target,
            ),
            rules_hint_payload=rules_hint_payload,
            resolved_turn_id=resolved_turn_id,
            pre_narration_effects=_pre_narration_effects(pre_pipeline_result, triggered_segments),
        )
        persistence_token = self._persistence_token(
            turn=turn,
        )
        narration_result = self._narrate_turn(narration_request)

        # Keep the per-session coordinator locked until the DM response has a
        # durable DmTurn row and timeline event. Canon extraction can continue
        # asynchronously after that saved boundary.
        self._emit_turn_status(command.session_id, turn_id, 'saving')
        post_turn_segments = self._persist_turn_outcome(
            persistence_token=persistence_token,
            command=command,
            player_label=player_label,
            rules_hint_payload=rules_hint_payload,
            dm_response_text=narration_result.text,
            stream_error=narration_result.stream_error,
            narration_provider=narration_result.provider,
            narration_model=narration_result.model,
            emergency_fallback=narration_result.emergency_fallback,
            triggered_segments=triggered_segments,
            start_time=start_time,
        )
        for segment_payload in post_turn_segments:
            self.socketio.emit('segment_triggered', segment_payload, room=str(command.session_id))

        self.socketio.emit(
            'session_log_update',
            session_log_update_payload(command.session_id, turn_id),
            room=str(command.session_id),
        )

    def _emit_turn_status(
        self,
        session_id: int,
        turn_id: int | None,
        status: str,
        details: dict | None = None,
    ):
        self.socketio.emit(
            'turn_status',
            turn_status_payload(session_id, turn_id, status, details),
            room=str(session_id),
        )

    def _emit_scene_state(self, session_id: int, *, acting_player_id: int | None = None) -> None:
        try:
            state = scene_state_for_session(session_id, acting_player_id=acting_player_id)
            if state:
                self.socketio.emit('scene_state', scene_state_payload(state), room=str(session_id))
        except Exception as exc:
            logger.warning('Scene-state emit failed: %s', str(exc))
            telemetry_event(
                'socket.scene_state.emit_failed',
                payload={'session_id': session_id, 'error': str(exc)},
                severity='warning',
            )

    def _emit_clarification_request(
        self,
        *,
        session_id: int,
        turn_id: int,
        player_id: int,
        player_message: str,
        clarification_requests: list[dict],
    ) -> None:
        request_payload = {
            'id': f'clarify_{turn_id}_001',
            'turnId': turn_id,
            'sessionId': session_id,
            'playerId': player_id,
            'type': 'item_resolution',
            'prompt': clarification_requests[0].get('prompt') if clarification_requests else 'Which item do you use?',
            'originalPlayerMessage': player_message,
            'originalAction': clarification_requests[0].get('originalAction') if clarification_requests else {},
            'options': clarification_requests[0].get('options') if clarification_requests else [],
        }
        turn_obj = db.session.get(DmTurn, turn_id)
        if turn_obj:
            metadata = safe_json_loads(turn_obj.metadata_json, {})
            metadata = metadata if isinstance(metadata, dict) else {}
            pipeline = metadata.get('state_pipeline') if isinstance(metadata.get('state_pipeline'), dict) else {}
            pipeline['clarificationRequest'] = request_payload
            metadata['state_pipeline'] = pipeline
            turn_obj.metadata_json = safe_json_dumps(metadata, {})
            turn_obj.status = 'awaiting_clarification'
            turn_obj.outcome_status = 'resolved'
            commit_with_retry(label='clarification request')
        self.socketio.emit('clarification_required', request_payload, room=str(session_id))
        self._emit_turn_status(session_id, turn_id, 'clarification_required', request_payload)
        self.socketio.emit('session_log_update', session_log_update_payload(session_id, turn_id), room=str(session_id))

    def _mark_clarification_resume_completed(self, *, command: TurnCommand, resumed_turn: DmTurn) -> None:
        paused_turn_id = self._clarification_resume_turn_id(command)
        if not paused_turn_id or paused_turn_id == resumed_turn.turn_id:
            return
        paused_turn = db.session.get(DmTurn, paused_turn_id)
        if (
            not paused_turn
            or paused_turn.session_id != command.session_id
            or paused_turn.player_id != command.player_id
            or paused_turn.status != 'awaiting_clarification'
        ):
            return

        metadata = safe_json_loads(paused_turn.metadata_json, {})
        metadata = metadata if isinstance(metadata, dict) else {}
        pipeline = metadata.get('state_pipeline') if isinstance(metadata.get('state_pipeline'), dict) else {}
        pipeline['clarificationResume'] = {
            'status': 'resolved',
            'resolvedByTurnId': resumed_turn.turn_id,
            'selectedItemIds': self._clarification_selected_item_ids(command),
            'resolvedAt': utc_now().isoformat(),
        }
        metadata['state_pipeline'] = pipeline
        metadata['resolved_by_turn_id'] = resumed_turn.turn_id
        paused_turn.metadata_json = safe_json_dumps(metadata, {})
        paused_turn.status = 'clarification_resolved'
        paused_turn.outcome_status = 'resolved'

        self._emit_turn_status(
            command.session_id,
            paused_turn.turn_id,
            'clarification_resolved',
            {'resolved_by_turn_id': resumed_turn.turn_id},
        )

    @staticmethod
    def _record_phase_timing(
        phase: str,
        started_at: float,
        *,
        campaign_id: int,
        session_id: int,
    ) -> None:
        telemetry_timing(
            'socket.turn_phase_latency_ms',
            float((time.perf_counter() - started_at) * 1000),
            tags={'campaign_id': campaign_id, 'phase': phase, 'session_id': session_id},
        )

    def _emit_roll_gate_waiting(
        self,
        *,
        turn: DmTurn,
        campaign: Campaign,
        command: TurnCommand,
        remaining_player_ids: list[int],
        session_turn_number: int,
    ) -> None:
        names_by_id = self._player_names_by_id(remaining_player_ids)
        remaining_names = [names_by_id.get(player_id, f'Player {player_id}') for player_id in remaining_player_ids]
        waiting_label = ', '.join(remaining_names) if remaining_names else 'the remaining players'
        message = f'**Roll recorded. Waiting for {waiting_label} before resolving the outcome.**'
        try:
            record_turn_event(
                session_id=turn.session_id,
                campaign_id=campaign.campaign_id,
                turn_id=turn.turn_id,
                player_id=turn.player_id,
                event_type=DM_RESPONSE_EVENT,
                payload={
                    'message': message,
                    'metadata': {
                        'turn_id': turn.turn_id,
                        'turn_number': session_turn_number,
                        'roll_gate_waiting': True,
                        'remaining_player_ids': remaining_player_ids,
                    },
                },
            )
            commit_with_retry(label='roll gate waiting message')
        except Exception as exc:
            db.session.rollback()
            logger.error('Failed to persist roll gate waiting message: %s', str(exc))

        self._emit_turn_status(
            command.session_id,
            turn.turn_id,
            'saved',
            {'stage': 'roll_gate_waiting', 'remaining_player_ids': remaining_player_ids},
        )
        self.socketio.emit(
            'session_log_update',
            session_log_update_payload(command.session_id, turn.turn_id),
            room=str(command.session_id),
        )

    @staticmethod
    def _complete_group_roll_waiting_rows(
        *,
        session_id: int,
        pending_turn_id: int,
        completed_by_turn_id: int,
    ) -> None:
        waiting_turns = DmTurn.query.filter_by(
            session_id=session_id,
            status='waiting_for_group_roll',
            outcome_status='resolved',
        ).all()
        completed_at = utc_now().isoformat()
        for waiting_turn in waiting_turns:
            if waiting_turn.turn_id == completed_by_turn_id:
                continue
            metadata = safe_json_loads(waiting_turn.metadata_json, {})
            metadata = metadata if isinstance(metadata, dict) else {}
            if int(metadata.get('resolved_turn_id') or 0) != pending_turn_id:
                continue
            metadata['group_roll_completed_by_turn_id'] = completed_by_turn_id
            metadata['group_roll_completed_at'] = completed_at
            waiting_turn.metadata_json = safe_json_dumps(metadata, {})
            waiting_turn.status = 'completed'
            waiting_turn.outcome_status = 'resolved'

    def _persist_incoming_turn(
        self,
        turn: DmTurn,
        player_label: str,
        command: TurnCommand,
        rule_hint: RuleHint,
        pending_turn_to_resolve: DmTurn | None,
        resolved_turn_id: int | None,
        session_turn_number: int,
    ) -> dict:
        remaining_player_ids: list[int] = []
        try:
            def _save_incoming_turn():
                nonlocal remaining_player_ids
                remaining_player_ids = []
                db.session.add(turn)
                db.session.flush()
                set_logging_context(turn_id=turn.turn_id)

                if pending_turn_to_resolve:
                    pending_metadata = safe_json_loads(pending_turn_to_resolve.metadata_json, {})
                    pending_metadata = pending_metadata if isinstance(pending_metadata, dict) else {}
                    gate = pending_metadata.get('roll_gate') if isinstance(pending_metadata.get('roll_gate'), dict) else {}
                    if gate:
                        resolved_player_ids = list(
                            dict.fromkeys([*pending_turn_resolved_player_ids(pending_turn_to_resolve), command.player_id])
                        )
                        required_player_ids = pending_turn_required_player_ids(pending_turn_to_resolve)
                        remaining_player_ids = [
                            player_id for player_id in required_player_ids if player_id not in set(resolved_player_ids)
                        ]
                        gate['resolved_player_ids'] = resolved_player_ids
                        gate['remaining_player_ids'] = remaining_player_ids
                        pending_metadata['roll_gate'] = gate
                        pending_turn_to_resolve.outcome_status = 'deferred' if remaining_player_ids else 'resolved'
                        if remaining_player_ids:
                            turn.status = 'waiting_for_group_roll'
                            turn.outcome_status = 'resolved'
                        else:
                            self._complete_group_roll_waiting_rows(
                                session_id=command.session_id,
                                pending_turn_id=pending_turn_to_resolve.turn_id,
                                completed_by_turn_id=turn.turn_id,
                            )
                    else:
                        pending_turn_to_resolve.outcome_status = 'resolved'
                    pending_metadata['resolved_by_turn_id'] = turn.turn_id
                    pending_metadata['resolved_at'] = utc_now().isoformat()
                    pending_turn_to_resolve.metadata_json = safe_json_dumps(pending_metadata, {})
                    record_turn_event(
                        session_id=command.session_id,
                        campaign_id=command.campaign_id,
                        turn_id=turn.turn_id,
                        player_id=command.player_id,
                        event_type=ROLL_RESOLVED_EVENT,
                        payload={
                            'pending_turn_id': pending_turn_to_resolve.turn_id,
                            'roll_value': rule_hint.roll_value,
                            'metadata': {
                                'turn_id': turn.turn_id,
                                'turn_number': session_turn_number,
                                'resolved_turn_id': pending_turn_to_resolve.turn_id,
                                'roll_value': rule_hint.roll_value,
                                'rule_type': rule_hint.roll_type,
                                'roll_gate': pending_metadata.get('roll_gate'),
                                'remaining_player_ids': remaining_player_ids,
                                'action_intent': command.action_intent,
                                'client_message_id': command.client_message_id,
                            },
                        },
                    )

                record_turn_event(
                    session_id=command.session_id,
                    campaign_id=command.campaign_id,
                    turn_id=turn.turn_id,
                    player_id=command.player_id,
                    event_type=PLAYER_MESSAGE_EVENT,
                    payload={
                        'message': command.user_input,
                        'speaker': player_label,
                        'metadata': {
                            'turn_id': turn.turn_id,
                            'turn_number': session_turn_number,
                            'confidence': rule_hint.confidence,
                            'outcome_status': turn.outcome_status,
                            'resolved_turn_id': resolved_turn_id,
                            'action_intent': command.action_intent,
                            'client_message_id': command.client_message_id,
                        },
                    },
                )
                return {
                    'ok': True,
                    'waiting_for_rolls': bool(pending_turn_to_resolve and remaining_player_ids),
                    'remaining_player_ids': remaining_player_ids,
                }

            return run_with_commit_retry(_save_incoming_turn, label='incoming player turn')
        except IntegrityError as exc:
            db.session.rollback()
            duplicate_turn = self._find_duplicate_turn(command)
            if duplicate_turn:
                logger.info(
                    'Database idempotency constraint rejected duplicate client message %s for session %s.',
                    command.client_message_id,
                    command.session_id,
                )
                self._emit_duplicate_turn(command, duplicate_turn, detected_by='unique_constraint')
                return {'ok': False, 'duplicate': True, 'turn_id': duplicate_turn.turn_id}
            logger.error('Failed to persist incoming player turn due to an integrity error: %s', str(exc))
            self.emit('error', socket_error('turn_persist_failed', 'Failed to persist player action.'))
            telemetry_event(
                'socket.send_message.turn_persist_failed',
                payload={'sid': command.sid, 'session_id': command.session_id, 'error_type': 'integrity_error'},
                severity='error',
            )
            return {'ok': False}
        except Exception as exc:
            db.session.rollback()
            logger.error('Failed to persist incoming player turn: %s', str(exc))
            self.emit('error', socket_error('turn_persist_failed', 'Failed to persist player action.'))
            telemetry_event(
                'socket.send_message.turn_persist_failed',
                payload={'sid': command.sid, 'session_id': command.session_id},
                severity='error',
            )
            return {'ok': False}

    def _segment_state_payload(self, session_id: int, campaign: Campaign) -> tuple[dict, dict]:
        return self.segment_service.segment_state_payload(session_id, campaign)

    def _activate_segments(
        self,
        *,
        turn: DmTurn,
        session_id: int,
        segments_to_activate: list[tuple[CampaignSegment, dict]],
    ) -> list[dict]:
        return self.segment_service.activate_segments(
            turn=turn,
            session_id=session_id,
            segments_to_activate=segments_to_activate,
        )

    def _evaluate_segments(
        self,
        turn: DmTurn,
        campaign: Campaign,
        command: TurnCommand,
        *,
        allowed_trigger_types: set[str] | None,
        include_manual: bool,
    ) -> list[dict]:
        return self.segment_service.evaluate_segments(
            turn=turn,
            campaign=campaign,
            request=SegmentEvaluationRequest(
                session_id=command.session_id,
                campaign_id=command.campaign_id,
                player_message=command.user_input,
                manual_segment_ids=frozenset(command.manual_segment_ids),
            ),
            allowed_trigger_types=allowed_trigger_types,
            include_manual=include_manual,
            state_payload_fn=self._segment_state_payload,
            activate_segments_fn=self._activate_segments,
        )

    def _narrate_turn(self, request: NarrationRequest) -> NarrationResult:
        service = TurnNarrationService(
            TurnNarrationDependencies(
                emit=self.emit,
                sleep=self.socketio.sleep,
                stream=self.stream_fn,
                build_context=build_dm_context,
                release_session=self._release_clean_provider_session,
                active_player_ids=self.active_player_ids,
                record_phase_timing=self._record_phase_timing,
                emit_turn_status=self._emit_turn_status,
                build_roll_prompt=self.build_roll_prompt,
                response_requests_roll=self._dm_response_requests_roll,
                response_explains_no_roll_needed=self._dm_response_explains_no_roll_needed,
                telemetry_event=telemetry_event,
                telemetry_metric=telemetry_metric,
                config_get=lambda key: current_app.config.get(key),
                logger=logger,
            )
        )
        return service.narrate(request)

    @staticmethod
    def _turn_has_unresolved_roll_gate(turn_obj: DmTurn) -> bool:
        if turn_obj.outcome_status == 'deferred':
            return True
        metadata = safe_json_loads(turn_obj.metadata_json, {})
        metadata = metadata if isinstance(metadata, dict) else {}
        gate = metadata.get('roll_gate')
        if not isinstance(gate, dict):
            return False
        remaining_player_ids = gate.get('remaining_player_ids')
        return isinstance(remaining_player_ids, list) and bool(remaining_player_ids)

    def _advance_structured_turn_if_ready(self, *, turn_obj: DmTurn, action_intent: dict | None) -> None:
        if TurnActionPolicy.is_admin_override(action_intent):
            return
        if self._turn_has_unresolved_roll_gate(turn_obj):
            return
        if not self.active_player_ids:
            return

        try:
            session_obj = db.session.get(Session, turn_obj.session_id)
            if not session_obj:
                return
            active_ids = [player_id for player_id in self.active_player_ids(turn_obj.session_id) if player_id]
            turn_control = advance_structured_turn(
                session_obj,
                current_player_id=turn_obj.player_id,
                active_player_ids=active_ids,
            )
            if not turn_control:
                return
            commit_with_retry(label='structured turn advance')
            self.socketio.emit(
                'turn_control_updated',
                turn_control_update_payload(turn_obj.session_id, turn_control),
                room=str(turn_obj.session_id),
            )
        except Exception as exc:
            db.session.rollback()
            logger.warning('Structured turn advance failed: %s', str(exc))
            telemetry_event(
                'socket.turn_control.advance_failed',
                payload={'session_id': turn_obj.session_id, 'turn_id': turn_obj.turn_id, 'error': str(exc)},
                severity='warning',
            )

    def _persist_turn_outcome(
        self,
        *,
        persistence_token: TurnPersistenceToken,
        command: TurnCommand,
        player_label: str,
        rules_hint_payload: dict,
        dm_response_text: str,
        stream_error: str | None,
        narration_provider: str | None,
        narration_model: str | None,
        emergency_fallback: dict | None,
        triggered_segments: list[dict],
        start_time: float,
    ) -> list[dict]:
        post_turn_segments: list[dict] = []
        dm_response_text = strip_reasoning_blocks(dm_response_text).strip()
        try:
            turn, campaign = self._reload_persistence_context(persistence_token)
            db_save_started = time.perf_counter()
            dm_succeeded = (
                bool(dm_response_text)
                and stream_error is None
                and emergency_fallback is None
            )
            turn_obj = turn
            if turn_obj:
                turn_obj.completed_at = utc_now()
                turn_obj.latency_ms = int((time.perf_counter() - start_time) * 1000)
                turn_obj.llm_provider = narration_provider
                turn_obj.llm_model = narration_model
                set_logging_context(turn_id=turn.turn_id)

                if dm_response_text:
                    turn_obj.dm_output = dm_response_text
                    turn_obj.status = 'failed' if stream_error else ('degraded' if emergency_fallback else 'completed')
                else:
                    turn_obj.status = 'failed' if stream_error else 'completed'
                metadata_payload = safe_json_loads(turn_obj.metadata_json, {})
                metadata_payload = metadata_payload if isinstance(metadata_payload, dict) else {}
                if stream_error:
                    metadata_payload['error'] = stream_error
                if emergency_fallback:
                    metadata_payload['llm_fallback'] = emergency_fallback
                turn_obj.metadata_json = safe_json_dumps(metadata_payload, {})

                dm_explains_no_roll = (
                    stream_error is None
                    and emergency_fallback is None
                    and turn_obj.roll_value is None
                    and self._dm_response_explains_no_roll_needed(dm_response_text)
                    and not self._dm_response_requests_roll(dm_response_text)
                )
                roll_gate_payload = (
                    None
                    if dm_explains_no_roll or emergency_fallback
                    else self._roll_gate_for_turn(turn_obj, campaign, dm_response_text)
                )
                if dm_explains_no_roll and turn_obj.requires_roll:
                    turn_obj.requires_roll = False
                    turn_obj.rule_type = None
                    turn_obj.outcome_status = 'resolved'
                    turn.requires_roll = False
                    turn.rule_type = None
                    turn.outcome_status = 'resolved'
                    metadata_payload = safe_json_loads(turn_obj.metadata_json, {})
                    metadata_payload = metadata_payload if isinstance(metadata_payload, dict) else {}
                    metadata_payload.pop('roll_gate', None)
                    metadata_payload['roll_requirement_cleared'] = {
                        'reason': 'dm_explained_no_roll_needed',
                        'original_rule_type': rules_hint_payload.get('roll_type'),
                        'original_reason': rules_hint_payload.get('reason'),
                    }
                    turn_obj.metadata_json = safe_json_dumps(metadata_payload, {})
                    rules_hint_payload['requires_roll'] = False
                    rules_hint_payload['roll_type'] = None
                    rules_hint_payload['dc_hint'] = None
                    rules_hint_payload['outcome_deferred'] = False
                    rules_hint_payload['reason'] = 'DM explained no roll was needed'
                    rules_hint_payload.pop('roll_gate', None)
                    rules_hint_payload.pop('remaining_player_ids', None)
                    rules_hint = safe_json_loads(turn_obj.rules_hint, {})
                    rules_hint = rules_hint if isinstance(rules_hint, dict) else {}
                    rules_hint.update(
                        {
                            'requires_roll': False,
                            'roll_type': None,
                            'dc_hint': None,
                            'outcome_deferred': False,
                            'reason': 'DM explained no roll was needed',
                        }
                    )
                    rules_hint.pop('roll_gate', None)
                    rules_hint.pop('remaining_player_ids', None)
                    turn_obj.rules_hint = safe_json_dumps(rules_hint, {})
                elif roll_gate_payload:
                    rule_type = roll_gate_payload.get('rule_type') or turn_obj.rule_type or 'check'
                    turn_obj.requires_roll = True
                    turn_obj.rule_type = rule_type
                    turn_obj.outcome_status = 'deferred'
                    turn.requires_roll = True
                    turn.rule_type = rule_type
                    turn.outcome_status = 'deferred'
                    metadata_payload = safe_json_loads(turn_obj.metadata_json, {})
                    metadata_payload = metadata_payload if isinstance(metadata_payload, dict) else {}
                    metadata_payload['roll_gate'] = roll_gate_payload
                    turn_obj.metadata_json = safe_json_dumps(metadata_payload, {})
                    rules_hint_payload['requires_roll'] = True
                    rules_hint_payload['roll_type'] = rule_type
                    rules_hint_payload['outcome_deferred'] = True
                    rules_hint_payload['roll_gate'] = roll_gate_payload
                    rules_hint_payload['remaining_player_ids'] = roll_gate_payload.get('remaining_player_ids', [])
                    rules_hint = safe_json_loads(turn_obj.rules_hint, {})
                    rules_hint = rules_hint if isinstance(rules_hint, dict) else {}
                    rules_hint.update(
                        {
                            'requires_roll': True,
                            'roll_type': rule_type,
                            'outcome_deferred': True,
                            'roll_gate': roll_gate_payload,
                            'remaining_player_ids': roll_gate_payload.get('remaining_player_ids', []),
                        }
                    )
                    turn_obj.rules_hint = safe_json_dumps(rules_hint, {})

            if dm_response_text:
                record_turn_event(
                    session_id=turn.session_id,
                    campaign_id=campaign.campaign_id,
                    turn_id=turn.turn_id,
                    player_id=turn.player_id,
                    event_type=DM_RESPONSE_EVENT,
                    payload={
                        'message': dm_response_text,
                        'metadata': {
                            'turn_id': turn.turn_id,
                            'turn_number': rules_hint_payload.get('turn_number'),
                            'requires_roll': turn.requires_roll,
                            'rule_type': turn.rule_type,
                            'dc_hint': rules_hint_payload.get('dc_hint'),
                            'confidence': turn.confidence,
                            'outcome_status': 'deferred' if turn.outcome_status == 'deferred' else 'resolved',
                            'roll_gate': rules_hint_payload.get('roll_gate'),
                            'remaining_player_ids': rules_hint_payload.get('remaining_player_ids'),
                            'action_intent': command.action_intent,
                            'client_message_id': command.client_message_id,
                            'llm_provider': narration_provider,
                            'llm_model': narration_model,
                            'llm_fallback': emergency_fallback,
                        },
                    },
                )

            commit_with_retry(label='DM response save')
            self._record_phase_timing(
                'db_save',
                db_save_started,
                campaign_id=campaign.campaign_id,
                session_id=turn.session_id,
            )
            self._emit_turn_status(turn.session_id, turn.turn_id, 'saved', {'stage': 'dm_response'})

            immediate_state_summary: dict = {}
            state_log: dict = {}
            post_pipeline_result: dict = {}
            campaign_pack_progress_changed = False
            if turn_obj and dm_succeeded:
                try:
                    player_obj = db.session.get(Player, turn.player_id) if turn.player_id else None
                    if not player_obj:
                        raise RuntimeError('Turn player not found for state pipeline.')
                    session_for_pipeline = db.session.get(Session, turn.session_id)
                    if session_for_pipeline is None:
                        raise RuntimeError('Turn session not found for state pipeline.')
                    active_player_ids = []
                    if self.active_player_ids:
                        active_player_ids = [player_id for player_id in self.active_player_ids(turn.session_id) if player_id]
                    post_pipeline_result = post_dm_pipeline(
                        turn=turn_obj,
                        session_obj=session_for_pipeline,
                        campaign=campaign,
                        player=player_obj,
                        dm_response_text=dm_response_text,
                        active_player_ids=active_player_ids,
                        before_helper_call=self._release_clean_provider_session,
                    )
                    turn_obj, _session_obj, campaign, _player_obj = (
                        self._reload_core_models(
                            command,
                            persistence_token.turn_id,
                        )
                    )
                    turn = turn_obj
                    immediate_state_summary = (
                        post_pipeline_result.get('legacyImmediateSummary') or {}
                    )
                    state_log = post_pipeline_result.get('stateLog') or {}
                    progress_result = update_campaign_pack_progress(
                        session_id=turn.session_id,
                        campaign_id=campaign.campaign_id,
                        triggered_segments=triggered_segments,
                    )
                    campaign_pack_progress_changed = bool(progress_result.changed)
                    commit_with_retry(label='post-DM state pipeline')
                    self._emit_scene_state(turn.session_id, acting_player_id=turn.player_id)
                except Exception as exc:
                    db.session.rollback()
                    logger.warning('State pipeline post-DM application failed: %s', str(exc))
                    telemetry_event(
                        'socket.state_pipeline.post_dm_failed',
                        payload={
                            'session_id': persistence_token.session_id,
                            'turn_id': persistence_token.turn_id,
                            'error': str(exc),
                        },
                        severity='warning',
                    )
                    try:
                        turn_obj, _session_obj, campaign, _player_obj = (
                            self._reload_core_models(
                                command,
                                persistence_token.turn_id,
                            )
                        )
                        turn = turn_obj
                        if turn_obj:
                            immediate_state_summary = apply_immediate_state_changes(turn_obj, campaign, dm_response_text)
                            commit_with_retry(label='legacy immediate state fallback')
                            self._emit_scene_state(turn.session_id, acting_player_id=turn.player_id)
                    except Exception as fallback_exc:
                        db.session.rollback()
                        logger.warning('Immediate character state application failed: %s', str(fallback_exc))
                        telemetry_event(
                            'socket.immediate_state_apply_failed',
                            payload={'session_id': turn.session_id, 'turn_id': turn.turn_id, 'error': str(fallback_exc)},
                            severity='warning',
                        )

                inventory_changes = immediate_state_summary.get('inventory_changes_applied') or []
                character_state_changes = immediate_state_summary.get('character_state_changes_applied') or []
                state_log_lines = state_log.get('lines') if isinstance(state_log, dict) else []
                metadata_payload = safe_json_loads(turn_obj.metadata_json, {}) if turn_obj else {}
                metadata_payload = metadata_payload if isinstance(metadata_payload, dict) else {}
                pipeline_metadata = metadata_payload.get(STATE_PIPELINE_METADATA_KEY)
                pipeline_metadata = pipeline_metadata if isinstance(pipeline_metadata, dict) else {}
                applied_changes_for_status = [
                    *(pipeline_metadata.get('immediateAppliedChanges') or []),
                    *(post_pipeline_result.get('postAppliedChanges') or []),
                ]
                if (
                    inventory_changes
                    or character_state_changes
                    or state_log_lines
                    or _snapshot_refresh_needed_from_applied_changes(applied_changes_for_status)
                ):
                    affected_player_ids = _affected_player_ids_from_state_summary(
                        inventory_changes,
                        character_state_changes,
                        fallback_player_id=turn.player_id,
                    )
                    already_applied_inventory = [
                        {**change, 'already_applied': True}
                        for change in inventory_changes
                        if isinstance(change, dict)
                    ]
                    already_applied_character_state = [
                        {**change, 'already_applied': True}
                        for change in character_state_changes
                        if isinstance(change, dict)
                    ]
                    self._emit_turn_status(
                        turn.session_id,
                        turn.turn_id,
                        'state_applied',
                        _state_application_event_details(
                            stage='dm_response',
                            player_id=turn.player_id,
                            affected_player_ids=affected_player_ids,
                            inventory_changes_applied=inventory_changes,
                            character_state_changes_applied=character_state_changes,
                            state_log=state_log,
                            applied_changes=applied_changes_for_status,
                            campaign_pack_progress_changed=campaign_pack_progress_changed,
                        ),
                    )
                    self._emit_turn_status(
                        turn.session_id,
                        turn.turn_id,
                        'canon_applied',
                        _state_application_event_details(
                            stage='state_applied',
                            player_id=turn.player_id,
                            affected_player_ids=affected_player_ids,
                            inventory_changes_applied=already_applied_inventory,
                            character_state_changes_applied=already_applied_character_state,
                            state_log=state_log,
                            applied_changes=applied_changes_for_status,
                            state_applied=True,
                            campaign_pack_progress_changed=campaign_pack_progress_changed,
                        ),
                    )

            if turn_obj and dm_succeeded:
                self._advance_structured_turn_if_ready(turn_obj=turn_obj, action_intent=command.action_intent)

            if turn_obj and dm_succeeded:
                self._mark_clarification_resume_completed(command=command, resumed_turn=turn_obj)

            if turn_obj and dm_succeeded:
                canon_job = enqueue_canon_job(
                    turn=turn_obj,
                    campaign=campaign,
                    speaking_player_name=player_label,
                    triggered_segments=triggered_segments,
                )
                commit_with_retry(label='canon job enqueue')
                self._emit_turn_status(
                    turn.session_id,
                    turn.turn_id,
                    'canon_pending',
                    {'job_id': canon_job.job_id},
                )
                app = current_app._get_current_object()  # type: ignore[attr-defined]
                if current_app.config.get('TESTING') or current_app.config.get('AIDM_ENV') == 'test':
                    process_canon_job(
                        canon_job.job_id,
                        emit_turn_status=self._emit_turn_status,
                        emit_segment_triggered=lambda session_id, payload: self.socketio.emit(
                            'segment_triggered',
                            payload,
                            room=str(session_id),
                        ),
                        record_phase_timing=self._record_phase_timing,
                    )
                else:
                    wake_canon_job_worker(app)

            commit_with_retry(label='post-turn final save')
            self._emit_turn_status(turn.session_id, turn.turn_id, 'saved', {'stage': 'post_turn'})

            if dm_succeeded:
                telemetry_metric('socket.send_message.success_total', 1)
                telemetry_timing(
                    'socket.turn_latency_ms',
                    float((time.perf_counter() - start_time) * 1000),
                    tags={'campaign_id': campaign.campaign_id, 'session_id': turn.session_id},
                )
            elif emergency_fallback:
                telemetry_metric(
                    'socket.send_message.degraded_total',
                    1,
                    tags={'provider': narration_provider or 'unknown', 'model': narration_model or 'unknown'},
                )
            elif stream_error:
                telemetry_event(
                    'socket.turn_failed',
                    payload={'session_id': turn.session_id, 'turn_id': turn.turn_id, 'error': stream_error},
                    severity='error',
                )
            return post_turn_segments
        except Exception as exc:
            db.session.rollback()
            logger.exception('Failed to persist DM response state')
            failed_turn = db.session.get(DmTurn, persistence_token.turn_id)
            if failed_turn:
                metadata_payload = safe_json_loads(failed_turn.metadata_json, {})
                metadata_payload = (
                    metadata_payload if isinstance(metadata_payload, dict) else {}
                )
                metadata_payload['post_turn_error'] = POST_TURN_PERSIST_FAILED_MESSAGE
                metadata_payload['canon_status'] = 'failed'
                if stream_error:
                    metadata_payload['error'] = stream_error
                if emergency_fallback:
                    metadata_payload['llm_fallback'] = emergency_fallback
                failed_turn.metadata_json = safe_json_dumps(metadata_payload, {})
                if failed_turn.status == persistence_token.expected_status:
                    failed_turn.completed_at = utc_now()
                    failed_turn.latency_ms = int(
                        (time.perf_counter() - start_time) * 1000
                    )
                    failed_turn.llm_provider = narration_provider
                    failed_turn.llm_model = narration_model
                    if dm_response_text:
                        failed_turn.dm_output = dm_response_text
                        failed_turn.status = (
                            'failed'
                            if stream_error
                            else ('degraded' if emergency_fallback else 'completed')
                        )
                    else:
                        failed_turn.status = 'failed'
                elif failed_turn.dm_output:
                    failed_turn.status = (
                        'degraded'
                        if metadata_payload.get('llm_fallback')
                        else 'completed'
                    )
                commit_with_retry(label='post-turn failure metadata')
            self._emit_turn_status(
                persistence_token.session_id,
                persistence_token.turn_id,
                'failed',
                {'stage': 'post_turn', 'error': POST_TURN_PERSIST_FAILED_MESSAGE},
            )
            self.socketio.emit(
                'error',
                socket_error(
                    'turn_persist_failed',
                    'The DM response was generated but could not be saved. Please retry; continuity may be affected.',
                    {
                        'session_id': persistence_token.session_id,
                        'turn_id': persistence_token.turn_id,
                    },
                ),
                room=str(persistence_token.session_id),
            )
            telemetry_event(
                'socket.dm_persist_failed',
                payload={
                    'session_id': persistence_token.session_id,
                    'turn_id': persistence_token.turn_id,
                    'error_type': type(exc).__name__,
                },
                severity='error',
            )
            return []
