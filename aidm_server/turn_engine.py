from __future__ import annotations

import logging
import time
from copy import copy
from dataclasses import dataclass, field
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
    character_roll_spec,
    character_state_for_player,
    inventory_contains,
    requested_gold_spend,
    server_attack_roll_context,
)
from aidm_server.canon_inventory import OWNED_ITEM_ACTIONS
from aidm_server.combat.legal_actions import (
    combat_snapshot_from_session,
    legal_combat_actions_for_player,
    resolve_combat_legal_action,
)
from aidm_server.combat.modifiers import can_act
from aidm_server.combat.pipeline import has_campaign_pack, hostile_action_targets_creature
from aidm_server.database import commit_with_retry, db, release_clean_scoped_session, run_with_commit_retry
from aidm_server.emergent_memory import apply_immediate_state_changes
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY
from aidm_server.game_state.change_types import (
    COMBAT_STATE_CHANGE_TYPES,
    PLAYER_SNAPSHOT_CHANGE_TYPES,
    SNAPSHOT_REFRESH_CHANGE_TYPES,
    WORLD_STATE_CHANGE_TYPES,
)
from aidm_server.game_state.extraction.free_text_actions import fight_words
from aidm_server.game_state.orchestration.conversations import TALK_KIND, TALK_WORDS_SOURCE
from aidm_server.game_state.orchestration.speech_acts import conversation_words
from aidm_server.game_state.models import display_actor_id
from aidm_server.interactables import NOT_WHILE_FIGHTING
from aidm_server.game_state.orchestration.combat_resolution import player_attack_target
from aidm_server.game_state.orchestration.finishing_blow import DESCRIBE_ACTION_ID, STORY_ACTION_ID, lets_the_story_tell_it, pending_finisher
from aidm_server.game_state.orchestration.hero_threads import TAKE_HEART_DECLARED
from aidm_server.game_state.orchestration.scene_plans import PLAN_DECLARED, PLAN_WORDS_ACTION_ID
from aidm_server.game_state.orchestration.story_beats import SCRIPTED_NARRATOR_PROVIDER, record_turn_story_beats
from aidm_server.game_state.orchestration.story_rolls import record_turn_story_rolls, turn_story_rolls
from aidm_server.game_state.orchestration.turn_flow import (
    AUTO_END_TURN_REQUEST_KEY,
    SCRIPTED_NARRATOR_MODEL,
    TIMINGS_KEY,
    TOLD_BY_NARRATOR,
    TOLD_SCRIPTED,
    TOLD_TURN_LINE,
    TURN_FLOW_KEY,
    scripted_stream,
    telling,
    turn_kind,
    turn_timings,
)
from aidm_server.game_state.orchestration.turn_pipeline import (
    augment_rules_hint_with_state_packet,
    post_dm_pipeline,
    pre_dm_pipeline,
)
from aidm_server.llm import CONTEXT_VERSION, build_dm_context
from aidm_server.logging_context import set_logging_context
from aidm_server.models import Campaign, CampaignSegment, DmTurn, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.player_rolls import (
    canonicalize_roll_action_intent,
    canonicalize_roll_text,
    pending_roll_spec,
    requested_ability_key,
    resolve_authoritative_player_roll,
    roll_against_armor_class,
)
from aidm_server.player_roll_claims import find_legacy_roll_claim
from aidm_server.roll_visibility import public_segment_triggered_payload
from aidm_server.rules import RuleHint, classify_player_action, player_message_reports_roll_result
from aidm_server.services.campaign_pack_progress import update_campaign_pack_progress
from aidm_server.services.scene_state import scene_state_for_session
from aidm_server.services.session_lifecycle import session_playability_error
from aidm_server.services.session_recovery import (
    LIFECYCLE_ACCEPTED,
    LIFECYCLE_EFFECTS_APPLIED,
    LIFECYCLE_FINISHED,
    LIFECYCLE_NARRATION_SAVED,
    LIFECYCLE_RULES_APPLIED,
    MAX_TURN_RESUME_ATTEMPTS,
    activate_turn_recovery_gate,
    active_turn_recovery_gate,
    finish_turn_lifecycle,
    mark_turn_recovery_required,
    recovery_gate_from_failed_turn,
    recovery_required_details,
    set_turn_lifecycle,
    turn_is_unfinished,
    turn_lifecycle,
    turn_lifecycle_phase,
    unfinished_session_turns,
    unresolved_turn_recovery_turn,
)
from aidm_server.services.session_state_mutation import serialized_campaign_turn
from aidm_server.socket_contracts import (
    MESSAGE_KIND_OPENING,
    new_message_payload,
    roll_resolved_payload,
    roll_required_payload,
    scene_state_payload,
    session_log_update_payload,
    socket_error_payload as socket_error,
    turn_duplicate_payload,
    turn_rolls_payload,
    turn_status_payload,
)
from aidm_server.socket_runtime import bound_presence
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
from aidm_server.turn_coordinator import SessionTurnTargetMissingError
from aidm_server.turn_events import (
    DM_RESPONSE_EVENT,
    PLAYER_MESSAGE_EVENT,
    ROLL_RESOLVED_EVENT,
    STATE_UPDATE_EVENT,
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
    FIGHT_LOOK,
    FIGHT_WORDS_SOURCE,
    FINISHER_DESCRIBE,
    FINISHER_SOURCE,
    FINISHER_STORY,
    fight_words_rule_hint,
    finisher_rule_hint,
    finishing,
    typed_in_fight,
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
# In a fight these come as fight actions (Take heart included), never as their own intents.
COMBAT_BLOCKED_ACTION_KINDS = frozenset({'ability', 'item', 'interact', 'travel', 'rest', 'inspiration', 'plan'})
AUTHORITATIVE_TYPED_ACTION_KINDS = frozenset({'capability', 'inspiration', 'item', 'object', 'plan', 'rest', 'shop', 'spell', 'travel'})


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
    message_kind: str | None = None
    # The hero's ``End my turn for me`` choice, sent with the action.
    auto_end_turn: bool = False
    # When the server received the message (``time.perf_counter``): a turn's time to act starts here.
    submitted_at: float = field(default_factory=time.perf_counter)

    @property
    def is_opening(self) -> bool:
        return self.message_kind == MESSAGE_KIND_OPENING


@dataclass(frozen=True)
class TurnPersistenceToken:
    turn_id: int
    session_id: int
    campaign_id: int
    player_id: int | None
    client_message_id: str | None
    expected_status: str


@dataclass(frozen=True)
class TurnClock:
    """When a turn's engine results were sent and its narration ended (``time.perf_counter``), and the narration's
    own length (first to last chunk) when a model was called."""

    submitted_at: float
    engine_done_at: float
    narration_ended_at: float
    narration_ms: int
    model_called: bool


@dataclass
class PostTurnOutcome:
    """Narration outcome and progress of the steps that follow the DM response save."""

    turn_id: int
    session_id: int
    command: TurnCommand
    player_label: str
    rules_hint_payload: dict
    dm_response_text: str
    stream_error: str | None
    narration_provider: str | None
    narration_model: str | None
    emergency_fallback: dict | None
    triggered_segments: list[dict]
    start_time: float
    # Status the DM response save expects to replace; None when resuming a turn
    # whose narration is already saved.
    expected_status: str | None = None
    state_application_failed: bool = False
    # The narration's roll request was for something the engine rolls itself and was taken out.
    engine_rolls_removed: bool = False
    # Who told the turn (``turn_flow.telling``) and, for a turn played through here, its clock.
    told: str = TOLD_BY_NARRATOR
    clock: TurnClock | None = None


def _turn_metadata(turn: DmTurn) -> dict:
    metadata = safe_json_loads(turn.metadata_json, {})
    return metadata if isinstance(metadata, dict) else {}


def _turn_triggered_segments(turn: DmTurn) -> list[dict]:
    segments = _turn_metadata(turn).get('triggered_segments')
    return [segment for segment in segments if isinstance(segment, dict)] if isinstance(segments, list) else []


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

    def _emit_duplicate_turn(self, command: TurnCommand, duplicate_turn_id: int, *, detected_by: str) -> None:
        if not command.client_message_id:
            return
        self.emit(
            'turn_duplicate',
            turn_duplicate_payload(
                command.session_id,
                duplicate_turn_id,
                command.client_message_id,
            ),
        )
        self.emit(
            'session_log_update',
            session_log_update_payload(command.session_id, duplicate_turn_id),
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
        legacy_roll_submission = player_message_reports_roll_result(command.user_input)
        has_pending_roll = (
            (action_kind == 'roll' or legacy_roll_submission)
            and self.latest_pending_turn(command.session_id, command.player_id) is not None
        )
        conductor_intent = (
            {'kind': 'roll'}
            if legacy_roll_submission and has_pending_roll
            else command.action_intent if isinstance(command.action_intent, dict) else None
        )
        active_player_ids = []
        if self.active_player_ids:
            active_player_ids = [
                int(player_id)
                for player_id in self.active_player_ids(command.session_id)
                if player_id
            ]
        presence = bound_presence()
        allowed, block_reason, turn_control, changed, decision = (
            conduct_turn_submission(
                session_obj,
                player_id=command.player_id,
                message=command.user_input,
                action_intent=conductor_intent,
                has_pending_roll=has_pending_roll,
                active_player_ids=active_player_ids,
                before_helper_call=self._release_clean_provider_session,
                reload_session_after_helper=lambda: db.session.get(
                    Session, command.session_id
                ),
                absent_since=(
                    (lambda player_id: presence.player_absent_since(command.session_id, player_id))
                    if presence is not None and self.active_player_ids
                    else None
                ),
                # An offered action says what it is: no model is asked who may act.
                allow_helper=not command.is_opening and action_kind in {'', 'message'},
                engine_runs_table=has_campaign_pack(safe_json_loads(session_obj.state_snapshot, {}) or {}),
            )
        )
        if changed:
            skipped_player_name = decision.get('skipped_player_name')
            if skipped_player_name:
                record_turn_event(
                    session_id=command.session_id,
                    campaign_id=command.campaign_id,
                    player_id=decision.get('skipped_player_id'),
                    event_type=STATE_UPDATE_EVENT,
                    payload={
                        'message': f'{skipped_player_name} is away; their turn was skipped.',
                        'metadata': {
                            'turn_control_skip': {
                                'skipped_player_id': decision.get('skipped_player_id'),
                                'reason': 'player_absent',
                            },
                        },
                    },
                )
            commit_with_retry(label='turn conductor decision')
            self.emit(
                'turn_control_updated',
                turn_control_update_payload(command.session_id, turn_control),
                room=str(command.session_id),
            )
            if skipped_player_name:
                self.emit(
                    'session_log_update',
                    session_log_update_payload(command.session_id),
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
        if not allowed:
            self.emit(
                'error',
                socket_error(
                    'turn_out_of_order',
                    block_reason or 'It is not your turn to act.',
                    {'turn_control': turn_control, **(decision.get('rejection_details') or {})},
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

    @staticmethod
    def _last_combat_target(session_id: int, player_id: int) -> str:
        """The enemy the player's most recent fight action was aimed at."""

        turns = (
            DmTurn.query.filter(DmTurn.session_id == session_id, DmTurn.player_id == player_id, DmTurn.metadata_json.contains('"target_id"'))
            .order_by(DmTurn.turn_id.desc())
            .limit(20)
        )
        for turn in turns:
            intent = safe_json_loads(turn.metadata_json, {}).get('action_intent')
            combat = intent.get('combat') if isinstance(intent, dict) and isinstance(intent.get('combat'), dict) else {}
            if str(combat.get('target_id') or '').strip():
                return str(combat['target_id']).strip()
        return ''

    @staticmethod
    def _route_finisher_words(command: TurnCommand, player: Player, session_obj: Session) -> None:
        """While a hero's finishing blow is pending, every message they type is it (or, typed or sent as the
        offered action, "Let the story tell it"): never another action, never rolled. The offered actions
        sent from the fight's action list (``finisher.describe`` with the hero's words, ``finisher.story``)
        are taken the same way."""

        action_intent = command.action_intent if isinstance(command.action_intent, dict) else {}
        message = str(command.user_input or '').strip()
        snapshot = safe_json_loads(session_obj.state_snapshot, {})
        pending = pending_finisher(snapshot if isinstance(snapshot, dict) else {})
        kind = str(action_intent.get('kind') or '').strip().lower()
        offered = str((action_intent.get('combat') or {}).get('action_id') or '') if isinstance(action_intent.get('combat'), dict) else ''
        if (
            pending is None
            or str(pending.get('heroId') or '') != display_actor_id(player.player_id)
            or not (kind in {'', 'message'} or (kind == 'combat' and offered in {DESCRIBE_ACTION_ID, STORY_ACTION_ID}))
            or not message
            or command.is_opening
            or player_message_reports_roll_result(message)
            or find_legacy_roll_claim(message) is not None
        ):
            return
        story = offered == STORY_ACTION_ID or (offered != DESCRIBE_ACTION_ID and lets_the_story_tell_it(message))
        command.action_intent = {
            **({'client_message_id': action_intent['client_message_id']} if action_intent.get('client_message_id') else {}),
            'kind': 'message',
            'source': FINISHER_SOURCE,
            'text': message,
            'finisher': FINISHER_STORY if story else FINISHER_DESCRIBE,
        }

    def _route_fight_words(self, command: TurnCommand, player: Player, session_obj: Session) -> bool:
        """Typed words of a hero who can act in a fight: free speech or a look, a listed fight action or an
        improvised one (each then takes the path of the button that would send it), or refused before
        narration. A hero's finishing blow is never read as fight words."""

        action_intent = command.action_intent if isinstance(command.action_intent, dict) else {}
        message = str(command.user_input or '').strip()
        if (
            finishing(action_intent)
            or str(action_intent.get('kind') or '').strip().lower() not in {'', 'message'}
            or not message
            or command.is_opening
            or player_message_reports_roll_result(message)
            or find_legacy_roll_claim(message) is not None
        ):
            return True
        snapshot = combat_snapshot_from_session(session_obj.state_snapshot)
        bundle = legal_combat_actions_for_player(snapshot, player)
        combat = snapshot.get('combat') if isinstance(snapshot.get('combat'), dict) else {}
        actor = next(
            (
                participant
                for participant in combat.get('participants') or []
                if isinstance(participant, dict) and bundle and str(participant.get('id') or '') == bundle['actorId']
            ),
            None,
        )
        if bundle is None or not can_act(actor):
            # A hero who is down is answered by the pipeline's own refusal.
            return True
        words = fight_words(
            snapshot,
            bundle,
            message=message,
            last_target_id=self._last_combat_target(command.session_id, command.player_id),
        )
        if words.kind == 'refused':
            self.emit(
                'error',
                socket_error(
                    'combat_action_unavailable',
                    words.refusal,
                    {'session_id': command.session_id, 'player_id': command.player_id},
                ),
            )
            telemetry_event(
                'socket.send_message.fight_words_refused',
                payload={'sid': command.sid, 'session_id': command.session_id, 'player_id': command.player_id},
                severity='warning',
            )
            return False
        routed = {
            'source': FIGHT_WORDS_SOURCE,
            'text': message,
            **({'client_message_id': action_intent['client_message_id']} if action_intent.get('client_message_id') else {}),
        }
        if words.kind in {'speech', 'look'}:
            command.action_intent = {**routed, 'kind': 'message', **({'fight_words': FIGHT_LOOK} if words.kind == 'look' else {})}
            return True
        if words.kind == 'plan':
            # A plan the words named (W9-SCENE): an unlisted fight action carrying the plan.
            command.action_intent = {**routed, 'kind': 'combat', 'combat': {'action_id': PLAN_WORDS_ACTION_ID, 'plan': words.plan}}
            return True
        combat_request = {'action_id': words.action_id, **({'target_id': words.target_id} if words.target_id else {})}
        if words.kind == 'improvised':
            combat_request['attempt'] = message
        command.action_intent = {**routed, 'kind': 'combat', 'combat': combat_request}
        return True

    def _route_conversation_words(self, command: TurnCommand, player: Player, session_obj: Session) -> None:
        """Typed words outside a fight that talk to someone, or are said to the person the hero is talking with or
        names in their room, become a ``talk`` intent: the engine holds the conversation, and the words are never read
        for a roll (:mod:`aidm_server.game_state.orchestration.speech_acts`)."""

        action_intent = command.action_intent if isinstance(command.action_intent, dict) else {}
        message = str(command.user_input or '').strip()
        if (
            finishing(action_intent)
            or typed_in_fight(action_intent)
            or str(action_intent.get('kind') or '').strip().lower() not in {'', 'message'}
            or not message
            or command.is_opening
            or player_message_reports_roll_result(message)
            or find_legacy_roll_claim(message) is not None
        ):
            return
        talk = conversation_words(
            combat_snapshot_from_session(session_obj.state_snapshot),
            actor_id=display_actor_id(player.player_id),
            player_id=player.player_id,
            message=message,
        )
        if talk is None:
            return
        command.action_intent = {
            'kind': TALK_KIND,
            'source': TALK_WORDS_SOURCE,
            'text': message,
            'talk': talk,
            **({'client_message_id': action_intent['client_message_id']} if action_intent.get('client_message_id') else {}),
        }

    def _prepare_combat_action(self, command: TurnCommand, player: Player, session_obj: Session) -> bool:
        action_intent = command.action_intent
        snapshot = combat_snapshot_from_session(session_obj.state_snapshot)
        combat = snapshot.get('combat') if isinstance(snapshot.get('combat'), dict) else {}
        combat_active = str(combat.get('status') or '').strip().lower() in {'starting', 'active'}
        action_kind = str(action_intent.get('kind') or '').strip().lower() if isinstance(action_intent, dict) else ''
        if combat_active and action_kind in COMBAT_BLOCKED_ACTION_KINDS:
            picking_up = action_kind == 'item' and str(action_intent.get('inventory_action') or '').strip().lower() == 'pick_up'
            self.emit(
                'error',
                socket_error(
                    'combat_action_required',
                    NOT_WHILE_FIGHTING
                    if picking_up
                    else 'Use an available combat action for this turn. That gameplay action is not legal during active combat.',
                    {
                        'session_id': command.session_id,
                        'player_id': command.player_id,
                        'action_kind': action_kind,
                    },
                ),
            )
            telemetry_event(
                'socket.send_message.combat_non_action_rejected',
                payload={
                    'sid': command.sid,
                    'session_id': command.session_id,
                    'player_id': command.player_id,
                    'action_kind': action_kind,
                },
                severity='warning',
            )
            return False
        if not isinstance(action_intent, dict) or action_kind != 'combat':
            return True
        requested = action_intent.get('combat') if isinstance(action_intent.get('combat'), dict) else {}
        resolved, error_code, error_message = resolve_combat_legal_action(
            snapshot,
            player,
            action_id=requested.get('action_id'),
            target_id=requested.get('target_id'),
            attempt=requested.get('attempt'),
            payload=requested.get('plan'),
        )
        if resolved is None:
            self.emit(
                'error',
                socket_error(
                    error_code or 'combat_action_invalid',
                    error_message or 'That combat action is no longer available.',
                    {
                        'session_id': command.session_id,
                        'player_id': command.player_id,
                        'action_id': requested.get('action_id'),
                    },
                ),
            )
            telemetry_event(
                'socket.send_message.combat_action_rejected',
                payload={
                    'sid': command.sid,
                    'session_id': command.session_id,
                    'player_id': command.player_id,
                    'action_id': requested.get('action_id'),
                    'error_code': error_code,
                },
                severity='warning',
            )
            return False
        action_intent['combat'] = resolved
        if not typed_in_fight(action_intent):
            # Typed words keep what the player wrote; a button's turn reads as its action.
            action_intent['text'] = resolved['message']
            command.user_input = resolved['message']
        return True

    def _validate_character_limits(self, command: TurnCommand, player: Player) -> bool:
        action_intent = command.action_intent if isinstance(command.action_intent, dict) else {}
        if action_intent.get('kind') == 'item':
            item = action_intent.get('item') if isinstance(action_intent.get('item'), dict) else {}
            item_name = str(item.get('name') or '').strip()
            quantity = int(item.get('quantity') or 1)
            inventory_action = str(action_intent.get('inventory_action') or 'use').strip().lower()
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

        # Shop prices are checked against the whole purse by the commerce engine;
        # this only guards gold amounts the player names in free text.
        spend = requested_gold_spend(command.user_input)
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
    def _combat_opening_roll_veto(pre_pipeline_result: dict, rules_hint_payload: dict) -> dict | None:
        """Clear the roll of an action that started a fight others open.

        When enemies (or a non-player ally) come first in initiative, their
        opening turns resolve this turn and the action that started the fight
        is not resolved out of initiative order: the character attacks on
        their own turn.
        """
        if pre_pipeline_result.get('openingEnemyTurns') is not True:
            return None
        if not rules_hint_payload.get('requires_roll') or rules_hint_payload.get('roll_value') is not None:
            return None
        if rules_hint_payload.get('resolved_turn_id') is not None:
            return None
        if rules_hint_payload.get('target_pending_turn_id') is not None:
            return None
        return {
            'source': 'combat_opening',
            'reason': 'Combat began with others ahead in initiative; the character acts on their own turn.',
            'instruction': (
                'Combat has begun and others act first in initiative: do not request a roll; '
                'the character acts on their own turn.'
            ),
            'original_rule_type': rules_hint_payload.get('roll_type'),
            'original_reason': rules_hint_payload.get('reason'),
        }

    @staticmethod
    def _no_opponent_roll_veto(pre_pipeline_result: dict, rules_hint_payload: dict, player_message: str) -> dict | None:
        """Clear a classifier attack roll when nobody is there to fight.

        Applies when no combat is active in the state the narrator sees, which
        already includes a combat the pre-DM pipeline started this turn.  Only
        a hostile action aimed at a creature is answered as having no one to
        fight (``no_target``); striking an object is an ordinary action.
        """
        if not rules_hint_payload.get('requires_roll') or rules_hint_payload.get('roll_type') != 'attack':
            return None
        if rules_hint_payload.get('roll_value') is not None:
            return None
        if rules_hint_payload.get('resolved_turn_id') is not None:
            return None
        if rules_hint_payload.get('target_pending_turn_id') is not None:
            return None
        if isinstance(rules_hint_payload.get('pvp'), dict):
            return None
        state = pre_pipeline_result.get('stateBeforeDm')
        if not isinstance(state, dict):
            return None
        combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
        if str(combat.get('status') or '').strip().lower() in {'starting', 'active'}:
            return None
        no_target = hostile_action_targets_creature(player_message)
        return {
            'source': 'no_opponent',
            'reason': 'No opponent is present.',
            'instruction': (
                'No opponent is present: do not request a roll; say there is no one here to fight.'
                if no_target
                else 'No opponent is present: do not request a roll; narrate this as an ordinary action.'
            ),
            'no_target': no_target,
            'original_rule_type': rules_hint_payload.get('roll_type'),
            'original_reason': rules_hint_payload.get('reason'),
        }

    @staticmethod
    def _typed_action_rejection(command: TurnCommand, pre_pipeline_result: dict) -> dict | None:
        intent = command.action_intent if isinstance(command.action_intent, dict) else {}
        intent_kind = str(intent.get('kind') or '').strip().lower()
        actor_down = pre_pipeline_result.get('actorDown')
        if isinstance(actor_down, dict):
            # A hero who is down cannot act, whatever was typed or clicked.
            return {
                'intent_kind': intent_kind or 'message',
                'action_id': None,
                'action_type': 'actor.down',
                'reason': str(actor_down.get('reason') or '').strip(),
            }
        zone_refusal = pre_pipeline_result.get('zoneRefusal')
        if isinstance(zone_refusal, dict):
            # What the hero reaches for is in another room of the place, beyond the next one.
            return {
                'intent_kind': intent_kind or 'message',
                'action_id': None,
                'action_type': 'zone.out_of_reach',
                'reason': str(zone_refusal.get('reason') or '').strip(),
            }
        pre_validation = pre_pipeline_result.get('preValidation')
        pre_validation = pre_validation if isinstance(pre_validation, dict) else {}
        if intent_kind in {'', 'message'} and any(
            isinstance(result, dict)
            and isinstance(result.get('originalAction'), dict)
            and result['originalAction'].get('type') == 'spell.cast'
            for result in pre_validation.get('validatedActions') or []
        ):
            intent_kind = 'spell'
        if intent_kind not in AUTHORITATIVE_TYPED_ACTION_KINDS:
            return None
        relevant_results: list[dict] = []
        for result in pre_validation.get('validatedActions') or []:
            if not isinstance(result, dict):
                continue
            original = result.get('originalAction') if isinstance(result.get('originalAction'), dict) else {}
            normalized = result.get('normalizedAction') if isinstance(result.get('normalizedAction'), dict) else {}
            action_type = str(normalized.get('type') or original.get('type') or '').strip()
            matches_intent = (
                (intent_kind == 'spell' and action_type == 'spell.cast')
                or (intent_kind == 'capability' and action_type == 'class_feature.use')
                or (intent_kind == 'object' and action_type == 'scene.interactable.action')
                or (intent_kind == 'plan' and action_type == PLAN_DECLARED)
                or (intent_kind == 'rest' and action_type == 'rest.complete')
                or (intent_kind == 'travel' and action_type == 'world.travel')
                or (intent_kind == 'shop' and action_type.startswith('shop.'))
                or (intent_kind == 'inspiration' and action_type == TAKE_HEART_DECLARED)
                or (
                    intent_kind == 'item'
                    and action_type.startswith(('currency.', 'inventory.', 'scene.item.', 'shop.'))
                )
            )
            if matches_intent:
                relevant_results.append(result)

        if not relevant_results or any(result.get('status') != 'invalid' for result in relevant_results):
            return None

        rejected = relevant_results[0]
        original = rejected.get('originalAction') if isinstance(rejected.get('originalAction'), dict) else {}
        normalized = rejected.get('normalizedAction') if isinstance(rejected.get('normalizedAction'), dict) else {}
        return {
            'intent_kind': intent_kind,
            'action_id': rejected.get('actionId') or normalized.get('id') or original.get('id'),
            'action_type': normalized.get('type') or original.get('type'),
            'reason': str(rejected.get('reason') or "That can't be done right now.").strip(),
        }

    @staticmethod
    def _clear_turn_roll_requirement_from_pre_dm_veto(
        *,
        turn: DmTurn,
        rules_hint_payload: dict,
        veto: dict,
        pre_pipeline_result: dict,
        reason: str,
    ) -> None:
        turn.requires_roll = False
        turn.rule_type = None
        turn.outcome_status = 'resolved'

        rules_hint_payload['requires_roll'] = False
        rules_hint_payload['roll_type'] = None
        rules_hint_payload['dc_hint'] = None
        rules_hint_payload['outcome_deferred'] = False
        rules_hint_payload['reason'] = reason
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
                'reason': reason,
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

    @staticmethod
    def _persisted_pre_pipeline_result(turn: DmTurn) -> dict | None:
        metadata = safe_json_loads(turn.metadata_json, {})
        metadata = metadata if isinstance(metadata, dict) else {}
        pipeline = metadata.get(STATE_PIPELINE_METADATA_KEY)
        if not isinstance(pipeline, dict) or not isinstance(pipeline.get('dmContextPacket'), dict):
            return None
        pre_validation = pipeline.get('preDmValidation')
        pre_validation = pre_validation if isinstance(pre_validation, dict) else {}
        return {
            'stateBeforeDm': pipeline.get('stateBeforeDm'),
            'preExtraction': pipeline.get('preDmExtraction') or {},
            'preValidation': pre_validation,
            'immediateValidation': pipeline.get('immediateValidation') or {},
            'pendingImmediateValidation': pipeline.get('pendingImmediateValidation') or {},
            'pendingImmediateChanges': pipeline.get('pendingImmediateChanges') or [],
            'immediateAppliedChanges': pipeline.get('immediateAppliedChanges') or [],
            'combatValidation': pipeline.get('combatPreDmValidation') or {},
            'combatAppliedChanges': pipeline.get('combatAppliedChanges') or [],
            'combatDebug': pipeline.get('combatDebug') or {},
            'dmContextPacket': pipeline.get('dmContextPacket') or {},
            'openingEnemyTurns': pipeline.get('openingEnemyTurns') is True,
            'actorDown': pipeline.get('actorDown'),
            'stateLog': pipeline.get('stateLog') or {},
            'clarificationRequests': pre_validation.get('clarificationRequests') or [],
        }

    def recover_abandoned_turns(self, session_id: int) -> bool:
        """Resume a session's unfinished turns unless a turn holds its locks.

        Returns whether a recovery ran. Never waits for the turn lock: a held
        lock means the turn is still in flight, not abandoned.
        """

        with serialized_campaign_turn(session_id, blocking=False) as lock_boundary:
            if lock_boundary is None:
                return False
            session_obj = db.session.get(Session, session_id, populate_existing=True)
            if (
                session_obj is None
                or session_playability_error(session_obj) is not None
                or active_turn_recovery_gate(session_obj) is not None
            ):
                return False
            return self._resume_abandoned_turns(session_id)

    def _resume_abandoned_turns(self, session_id: int) -> bool:
        """Finish turns a stopped process left unfinished, oldest first.

        The caller holds the session's turn locks, so no other worker can still
        be processing them. Returns whether any turn was found.
        """

        turn_ids = [turn.turn_id for turn in unfinished_session_turns(session_id)]
        if not turn_ids:
            return False
        room_engine = self._session_room_engine(session_id)
        for turn_id in turn_ids:
            room_engine._resume_abandoned_turn(turn_id)
        return True

    def _session_room_engine(self, session_id: int) -> 'TurnEngine':
        """Return a copy whose requester-addressed events go to the session room."""

        engine = copy(self)
        session_room = str(session_id)

        def emit_to_session_room(event, *args, room=None, **kwargs):
            self.socketio.emit(event, *args, room=room or session_room, **kwargs)

        engine.emit = emit_to_session_room
        return engine

    def _resume_abandoned_turn(self, turn_id: int) -> None:
        turn = db.session.get(DmTurn, turn_id, populate_existing=True)
        if turn is None or not turn_is_unfinished(turn):
            return
        session_obj = db.session.get(Session, turn.session_id)
        campaign = db.session.get(Campaign, turn.campaign_id)
        player = db.session.get(Player, turn.player_id) if turn.player_id else None
        if not all((session_obj, campaign, player)):
            self._settle_resume_attempt(turn_id)
            return
        command = TurnCommand(
            sid=str(turn.session_id),
            session_id=turn.session_id,
            campaign_id=turn.campaign_id,
            world_id=campaign.world_id,
            player_id=turn.player_id,
            user_input=str(turn.player_input or ''),
            manual_segment_ids=set(),
            client_message_id=turn.client_message_id,
        )
        self._resume_turn(
            command=command,
            turn=turn,
            session_obj=session_obj,
            campaign=campaign,
            player=player,
            private_receipt=False,
        )

    def _resume_turn(
        self,
        *,
        command: TurnCommand,
        turn: DmTurn,
        session_obj: Session,
        campaign: Campaign,
        player: Player,
        private_receipt: bool,
    ) -> None:
        """Resume an unfinished turn and account for a resume that did not finish it."""

        turn_id = int(turn.turn_id)
        try:
            self._resume_processing_turn(
                command=command,
                turn=turn,
                session_obj=session_obj,
                campaign=campaign,
                player=player,
                private_receipt=private_receipt,
            )
        except Exception as exc:
            db.session.rollback()
            logger.warning('Turn resume failed (error_type=%s).', type(exc).__name__)
            telemetry_event(
                'socket.turn_resume.failed',
                payload={
                    'session_id': command.session_id,
                    'turn_id': turn_id,
                    'error_type': type(exc).__name__,
                },
                severity='error',
            )
            self.emit(
                'error',
                socket_error(
                    'turn_persist_failed',
                    'The interrupted turn could not be finished yet. It will be retried.',
                    {'session_id': command.session_id, 'turn_id': turn_id},
                ),
            )
        self._settle_resume_attempt(turn_id)

    def _settle_resume_attempt(self, turn_id: int) -> None:
        """Count a resume that left the turn unfinished; gate it after the last attempt."""

        turn = db.session.get(DmTurn, turn_id, populate_existing=True)
        if turn is None or not turn_is_unfinished(turn):
            return
        attempts = int(turn_lifecycle(turn).get('resumeAttempts') or 0) + 1
        session_id = int(turn.session_id)
        set_turn_lifecycle(turn, resumeAttempts=attempts)
        commit_with_retry(label='turn resume attempt')
        telemetry_event(
            'socket.turn_resume.unfinished',
            payload={'session_id': session_id, 'turn_id': turn_id, 'resume_attempts': attempts},
            severity='warning',
        )
        if attempts >= MAX_TURN_RESUME_ATTEMPTS:
            try:
                activate_turn_recovery_gate(session_id=session_id, turn_id=turn_id)
            except Exception as exc:
                db.session.rollback()
                telemetry_event(
                    'socket.turn_resume.recovery_gate_failed',
                    payload={
                        'session_id': session_id,
                        'turn_id': turn_id,
                        'error_type': type(exc).__name__,
                    },
                    severity='error',
                )

    def _resume_processing_turn(
        self,
        *,
        command: TurnCommand,
        turn: DmTurn,
        session_obj: Session,
        campaign: Campaign,
        player: Player,
        private_receipt: bool = True,
    ) -> None:
        metadata = safe_json_loads(turn.metadata_json, {})
        metadata = metadata if isinstance(metadata, dict) else {}
        rules_hint_payload = safe_json_loads(turn.rules_hint, {})
        rules_hint_payload = rules_hint_payload if isinstance(rules_hint_payload, dict) else {}

        persisted_intent = metadata.get('action_intent')
        command.user_input = str(turn.player_input or '')
        command.action_intent = persisted_intent if isinstance(persisted_intent, dict) else None
        command.client_message_id = turn.client_message_id or command.client_message_id
        command.message_kind = metadata.get('message_kind') or None
        persisted_override = metadata.get('state_pipeline_override')
        command.state_pipeline_override = (
            persisted_override if isinstance(persisted_override, dict) else None
        )
        manual_segment_ids = metadata.get('manual_segment_ids')
        command.manual_segment_ids = {
            int(segment_id)
            for segment_id in (manual_segment_ids if isinstance(manual_segment_ids, list) else [])
            if isinstance(segment_id, int) and segment_id > 0
        }

        player_label = str(metadata.get('speaker') or player.character_name or player.name)
        resolved_turn_id = _coerce_player_id(
            metadata.get('resolved_turn_id', rules_hint_payload.get('resolved_turn_id'))
        )
        try:
            session_turn_number = int(
                metadata.get('turn_number', rules_hint_payload.get('turn_number')) or 0
            )
        except (TypeError, ValueError):
            session_turn_number = 0

        pvp_payload = metadata.get('pvp')
        if not isinstance(pvp_payload, dict):
            pvp_payload = rules_hint_payload.get('pvp')
        pvp_payload = pvp_payload if isinstance(pvp_payload, dict) else {}
        pvp_target_id = _coerce_player_id(pvp_payload.get('target_player_id'))
        pvp_target = db.session.get(Player, pvp_target_id) if pvp_target_id else None
        if not TurnActionPolicy.player_is_available_for_campaign(pvp_target, campaign):
            pvp_target = None

        persisted_roll = metadata.get('authoritative_roll')
        if not isinstance(persisted_roll, dict):
            persisted_roll = rules_hint_payload.get('authoritative_roll')
        if (
            private_receipt
            and isinstance(persisted_roll, dict)
            and persisted_roll.get('authoritative') is True
        ):
            self.emit(
                'roll_resolved',
                roll_resolved_payload(
                    session_id=command.session_id,
                    turn_id=turn.turn_id,
                    player_id=command.player_id,
                    client_message_id=command.client_message_id,
                    pending_turn_id=resolved_turn_id,
                    roll=persisted_roll,
                    include_private_provenance=True,
                ),
                room=command.sid,
            )

        phase = turn_lifecycle_phase(turn)
        telemetry_event(
            'socket.send_message.duplicate_resumed',
            payload={
                'sid': command.sid,
                'session_id': command.session_id,
                'player_id': command.player_id,
                'turn_id': turn.turn_id,
                'client_message_id': command.client_message_id,
                'pre_dm_already_persisted': self._persisted_pre_pipeline_result(turn) is not None,
                'lifecycle_phase': phase,
            },
        )
        self._emit_turn_status(
            command.session_id,
            turn.turn_id,
            'resuming',
            {'stage': 'idempotent_retry', 'lifecycle_phase': phase},
        )
        if phase in (LIFECYCLE_NARRATION_SAVED, LIFECYCLE_EFFECTS_APPLIED):
            self._resume_saved_narration(
                command=command,
                turn=turn,
                player_label=player_label,
                rules_hint_payload=rules_hint_payload,
            )
            return
        self._continue_persisted_turn(
            turn=turn,
            command=command,
            session_obj=session_obj,
            campaign=campaign,
            player=player,
            player_label=player_label,
            rules_hint_payload=rules_hint_payload,
            resolved_turn_id=resolved_turn_id,
            session_turn_number=session_turn_number,
            pvp_target=pvp_target,
            start_time=time.perf_counter(),
        )

    def _continue_persisted_turn(
        self,
        *,
        turn: DmTurn,
        command: TurnCommand,
        session_obj: Session,
        campaign: Campaign,
        player: Player,
        player_label: str,
        rules_hint_payload: dict,
        resolved_turn_id: int | None,
        session_turn_number: int,
        pvp_target: Player | None,
        start_time: float,
    ) -> None:
        turn_id = int(turn.turn_id)
        pre_pipeline_result = self._persisted_pre_pipeline_result(turn) or {}
        pre_pipeline_succeeded = bool(pre_pipeline_result)
        if not pre_pipeline_succeeded:
            state_pipeline_started = time.perf_counter()
            try:
                active_player_ids = []
                if self.active_player_ids:
                    active_player_ids = [
                        player_id
                        for player_id in self.active_player_ids(command.session_id)
                        if player_id
                    ]
                # An opening request declares no player action, so the pre-DM
                # extraction (and its helper) gets nothing to extract.
                pre_pipeline_result = pre_dm_pipeline(
                    turn=turn,
                    session_obj=session_obj,
                    campaign=campaign,
                    player=player,
                    player_message='' if command.is_opening else command.user_input,
                    action_intent=None if command.is_opening else command.action_intent,
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
                    command,
                    turn_id,
                )
                set_turn_lifecycle(turn, LIFECYCLE_RULES_APPLIED)
                commit_with_retry(label='pre-DM state pipeline')
                self._record_phase_timing(
                    'state_pre_dm',
                    state_pipeline_started,
                    campaign_id=command.campaign_id,
                    session_id=command.session_id,
                )
                pre_pipeline_succeeded = True
            except Exception as exc:
                db.session.rollback()
                try:
                    turn, session_obj, campaign, player = self._reload_core_models(
                        command,
                        turn_id,
                    )
                except RuntimeError:
                    self.emit(
                        'error',
                        socket_error(
                            'turn_persist_failed',
                            'Failed to reload player action state.',
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
                rules_hint_payload['state_pipeline_warning'] = (
                    'State validation failed; avoid committing inventory/HP/currency changes.'
                )

        if pre_pipeline_succeeded:
            clarification_requests = pre_pipeline_result.get('clarificationRequests') or []
            if clarification_requests:
                self._emit_clarification_request(
                    session_id=command.session_id,
                    turn_id=turn.turn_id,
                    player_id=command.player_id,
                    request_sid=command.sid,
                    player_message=command.user_input,
                    clarification_requests=clarification_requests,
                )
                return
            typed_action_rejection = self._typed_action_rejection(
                command,
                pre_pipeline_result,
            )
            if typed_action_rejection:
                self._reject_typed_action_before_narration(
                    command=command,
                    turn=turn,
                    rejection=typed_action_rejection,
                    start_time=start_time,
                )
                return
            combat_opening_veto = self._combat_opening_roll_veto(pre_pipeline_result, rules_hint_payload)
            if combat_opening_veto:
                self._clear_turn_roll_requirement_from_pre_dm_veto(
                    turn=turn,
                    rules_hint_payload=rules_hint_payload,
                    veto=combat_opening_veto,
                    pre_pipeline_result=pre_pipeline_result,
                    reason=combat_opening_veto['reason'],
                )
                rules_hint_payload['instruction'] = combat_opening_veto['instruction']
            pre_dm_roll_veto = self._pre_dm_roll_veto(
                pre_pipeline_result,
                rules_hint_payload,
            )
            if pre_dm_roll_veto:
                self._clear_turn_roll_requirement_from_pre_dm_veto(
                    turn=turn,
                    rules_hint_payload=rules_hint_payload,
                    veto=pre_dm_roll_veto,
                    pre_pipeline_result=pre_pipeline_result,
                    reason='Pre-DM helper marked no roll needed',
                )
            no_opponent_veto = self._no_opponent_roll_veto(
                pre_pipeline_result,
                rules_hint_payload,
                command.user_input,
            )
            if no_opponent_veto:
                self._clear_turn_roll_requirement_from_pre_dm_veto(
                    turn=turn,
                    rules_hint_payload=rules_hint_payload,
                    veto=no_opponent_veto,
                    pre_pipeline_result=pre_pipeline_result,
                    reason=no_opponent_veto['reason'],
                )
                if no_opponent_veto['no_target']:
                    rules_hint_payload['no_target'] = True
                rules_hint_payload['instruction'] = no_opponent_veto['instruction']
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
            self.emit(
                'segment_triggered',
                public_segment_triggered_payload(segment_payload),
                room=str(command.session_id),
            )

        commit_with_retry(label='pre-narration boundary')
        turn, session_obj, campaign, player = self._reload_core_models(command, turn_id)
        # Includes segments a previous, interrupted attempt already triggered.
        triggered_segments = _turn_triggered_segments(turn)
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
            pre_narration_effects=_pre_narration_effects(
                pre_pipeline_result,
                triggered_segments,
            ),
        )
        persistence_token = self._persistence_token(turn=turn)
        self._emit_turn_rolls(turn)
        engine_done_at = time.perf_counter()
        told, turn_line = telling(
            rules_hint_payload,
            command.action_intent,
            player_input=narration_request.model_user_input,
            speaker=player_label,
        )
        if told == TOLD_TURN_LINE:
            # Nothing to tell beyond whose turn it is: no reply is streamed and no narrator is asked.
            narration_result = NarrationResult(
                text=turn_line, stream_error=None, provider=SCRIPTED_NARRATOR_PROVIDER, model=SCRIPTED_NARRATOR_MODEL
            )
        elif told == TOLD_SCRIPTED:
            narration_result = self._narrate_turn(narration_request, scripted=True)
        else:
            narration_result = self._narrate_turn(narration_request)
        model_called = told == TOLD_BY_NARRATOR and str(current_app.config.get('AIDM_LLM_PROVIDER') or '') != SCRIPTED_NARRATOR_PROVIDER

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
            engine_rolls_removed=narration_result.engine_rolls_removed,
            told=told,
            clock=TurnClock(
                submitted_at=command.submitted_at,
                engine_done_at=engine_done_at,
                narration_ended_at=time.perf_counter(),
                narration_ms=narration_result.narration_ms if model_called else 0,
                model_called=model_called,
            ),
        )
        for segment_payload in post_turn_segments:
            self.socketio.emit(
                'segment_triggered',
                public_segment_triggered_payload(segment_payload),
                room=str(command.session_id),
            )

        self.socketio.emit(
            'session_log_update',
            session_log_update_payload(command.session_id, turn_id),
            room=str(command.session_id),
        )

    def _resume_saved_narration(
        self,
        *,
        command: TurnCommand,
        turn: DmTurn,
        player_label: str,
        rules_hint_payload: dict,
    ) -> None:
        """Finish a turn whose narration is saved without calling the narrator again."""

        turn_id = int(turn.turn_id)
        metadata = _turn_metadata(turn)
        outcome = PostTurnOutcome(
            turn_id=turn_id,
            session_id=int(turn.session_id),
            command=command,
            player_label=player_label,
            rules_hint_payload=rules_hint_payload,
            dm_response_text=str(turn.dm_output or ''),
            stream_error=metadata.get('error'),
            narration_provider=turn.llm_provider,
            narration_model=turn.llm_model,
            emergency_fallback=metadata.get('llm_fallback') if isinstance(metadata.get('llm_fallback'), dict) else None,
            triggered_segments=_turn_triggered_segments(turn),
            start_time=time.perf_counter(),
        )
        try:
            turn, _session_obj, campaign, _player = self._reload_core_models(command, turn_id)
            post_turn_segments = self._complete_saved_turn(outcome, turn=turn, campaign=campaign)
        except Exception as exc:
            post_turn_segments = self._handle_post_turn_failure(outcome, exc)
        for segment_payload in post_turn_segments:
            self.socketio.emit(
                'segment_triggered',
                public_segment_triggered_payload(segment_payload),
                room=str(command.session_id),
            )
        self.socketio.emit(
            'session_log_update',
            session_log_update_payload(command.session_id, turn_id),
            room=str(command.session_id),
        )

    def process(self, command: TurnCommand):
        try:
            with serialized_campaign_turn(command.session_id) as lock_boundary:
                wait_ms = lock_boundary.waits.get(command.session_id, 0.0)
                if wait_ms >= 1.0:
                    telemetry_timing(
                        'socket.turn_queue_wait_ms',
                        wait_ms,
                        tags={'session_id': command.session_id, 'campaign_id': command.campaign_id},
                    )
                return self._process_serialized(command)
        except SessionTurnTargetMissingError:
            self.emit('error', socket_error('session_not_found', 'Session not found'))
            telemetry_event(
                'socket.turn.session_not_found',
                payload={'sid': command.sid, 'session_id': command.session_id},
                severity='warning',
            )
            return None

    def _process_serialized(self, command: TurnCommand):
        # Socket preflights can race an archive request, and the scoped
        # SQLAlchemy session may still hold the object loaded by that preflight.
        # Force a fresh read while the per-session turn coordinator is held.
        session_obj = db.session.get(Session, command.session_id, populate_existing=True)
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

        campaign = db.session.get(Campaign, command.campaign_id, populate_existing=True)
        if not campaign:
            self.emit('error', socket_error('campaign_not_found', 'Campaign not found'))
            telemetry_event(
                'socket.send_message.campaign_not_found',
                payload={'sid': command.sid, 'campaign_id': command.campaign_id},
                severity='warning',
            )
            return

        playability_error = session_playability_error(session_obj, campaign_obj=campaign)
        if playability_error:
            error_code = playability_error.error_code
            self.emit('error', socket_error(error_code, playability_error.message, playability_error.details))
            telemetry_event(
                f'socket.turn.{error_code}',
                payload={
                    'sid': command.sid,
                    'session_id': command.session_id,
                    'campaign_id': command.campaign_id,
                },
                severity='warning',
            )
            return

        recovery_gate = active_turn_recovery_gate(session_obj)
        if recovery_gate is None:
            # The failed turn row is the fail-closed source of truth. Repair the
            # redundant snapshot gate when possible, but still block this action
            # if snapshot persistence remains unavailable. Mechanics are never
            # retried from this path.
            unresolved_turn = unresolved_turn_recovery_turn(command.session_id)
            recovery_gate = (
                recovery_gate_from_failed_turn(unresolved_turn)
                if unresolved_turn is not None
                else None
            )
            if recovery_gate is not None and unresolved_turn is not None:
                try:
                    recovery_mutation = activate_turn_recovery_gate(
                        session_id=command.session_id,
                        turn_id=unresolved_turn.turn_id,
                    )
                    repaired_gate = active_turn_recovery_gate(recovery_mutation.state)
                    if repaired_gate is not None:
                        recovery_gate = repaired_gate
                except Exception as exc:
                    db.session.rollback()
                    telemetry_event(
                        'socket.turn_recovery.snapshot_repair_failed',
                        payload={
                            'session_id': command.session_id,
                            'turn_id': unresolved_turn.turn_id,
                            'error_type': type(exc).__name__,
                        },
                        severity='error',
                    )
        if recovery_gate is not None:
            self._emit_recovery_required(command, recovery_gate)
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
                duplicate_turn_id = int(duplicate_turn.turn_id)
                if turn_is_unfinished(duplicate_turn):
                    narration_was_saved = turn_lifecycle_phase(duplicate_turn) in (
                        LIFECYCLE_NARRATION_SAVED,
                        LIFECYCLE_EFFECTS_APPLIED,
                    )
                    self._resume_turn(
                        command=command,
                        turn=duplicate_turn,
                        session_obj=session_obj,
                        campaign=campaign,
                        player=player,
                        private_receipt=True,
                    )
                    if narration_was_saved:
                        self._emit_duplicate_turn(command, duplicate_turn_id, detected_by='resumed')
                    return
                self._emit_duplicate_turn(command, duplicate_turn_id, detected_by='preflight')
                return

        # Holding the turn lock means any unfinished turn of this session was
        # abandoned by a stopped process: finish it before the new message.
        if self._resume_abandoned_turns(command.session_id):
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
            recovery_gate = active_turn_recovery_gate(session_obj)
            if recovery_gate is not None:
                self._emit_recovery_required(command, recovery_gate)
                return

        self._route_finisher_words(command, player, session_obj)
        if not self._route_fight_words(command, player, session_obj):
            return
        self._route_conversation_words(command, player, session_obj)
        if not self._prepare_combat_action(command, player, session_obj):
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
        action_kind = (
            str(command.action_intent.get('kind')).strip().lower()
            if isinstance(command.action_intent, dict)
            else ''
        )
        roll_submission_requested = (
            action_kind == 'roll'
            or player_message_reports_roll_result(command.user_input)
        )
        combat_roll_requested = bool(
            action_kind == 'combat'
            and isinstance(command.action_intent, dict)
            and isinstance(command.action_intent.get('combat'), dict)
            and command.action_intent['combat'].get('action_type') == 'attack'
        )
        roll_submission_requested = roll_submission_requested or combat_roll_requested
        legacy_roll_claim = find_legacy_roll_claim(command.user_input)
        if legacy_roll_claim is not None and action_kind in {'', 'message'}:
            command.action_intent = {
                'kind': 'roll',
                'source': 'server_legacy_text',
                'text': command.user_input,
                'roll': {
                    'die': legacy_roll_claim.die,
                    'mode': 'normal',
                    'result_visibility': 'hidden_until_landed',
                    'reason': legacy_roll_claim.reason or '',
                },
            }
            action_kind = 'roll'
        rules_engine_enabled = bool(current_app.config.get('AIDM_RULES_ENGINE_ENABLED', True))
        rule_hint: RuleHint = (
            fight_words_rule_hint()
            if typed_in_fight(command.action_intent)
            else finisher_rule_hint()
            if finishing(command.action_intent)
            else classify_player_action(command.user_input)
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
        # The check the player's own words name, if any ("I persuade the guard"); "I roll a d20" names none.
        worded_check = rule_hint.roll_type if rule_hint.roll_type not in (None, 'check') else None
        rule_hint = apply_action_intent_to_rule_hint(command.action_intent, rule_hint)
        rule_hint = TurnActionPolicy.apply_spell_rule_hint(rule_hint, command.action_intent, player)
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
        if pending_turn_before is not None and combat_roll_requested:
            # A fresh HUD attack must never be mistaken for a response to an
            # earlier unresolved check. The player must resolve that check first.
            roll_submission_requested = False
        any_pending_turn = None if is_admin_override else self.latest_pending_turn(command.session_id, None)
        if roll_target_pending_turn_id is not None and roll_submission_requested and not pending_turn_before:
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

        if pending_turn_before and not roll_submission_requested:
            pending_rule_type = pending_turn_before.rule_type or 'check'
            pending_dc_hint = self.dc_hint_from_turn(pending_turn_before)
            pending_spec = pending_roll_spec(pending_turn_before) or {
                'die': 'd20',
                'mode': 'normal',
                'rule_type': pending_rule_type,
                'reason': pending_rule_type.replace('_', ' '),
                'result_visibility': 'hidden_until_landed',
            }
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
                remaining_player_ids=pending_turn_remaining_player_ids(pending_turn_before),
                roll_spec=pending_spec,
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
            if not roll_submission_requested:
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

            if roll_submission_requested and any_pending_turn.player_id != command.player_id:
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

        if pending_turn_before and roll_submission_requested:
            pending_rule_type = pending_turn_before.rule_type or 'check'
            pending_dc_hint = self.dc_hint_from_turn(pending_turn_before)
            rule_hint.requires_roll = True
            rule_hint.roll_type = pending_rule_type
            rule_hint.dc_hint = pending_dc_hint or rule_hint.dc_hint
            rule_hint.reason = f'Resolving pending {pending_rule_type} from turn {pending_turn_before.turn_id}'
            rule_hint.outcome_deferred = True

        attack_context = None
        if str(rule_hint.roll_type or '').strip().lower() == 'attack':
            stored_roll_spec = pending_roll_spec(pending_turn_before)
            stored_attack_context = (
                stored_roll_spec.get('attack')
                if isinstance(stored_roll_spec.get('attack'), dict)
                else None
            )
            attack_source_text = (
                pending_turn_before.player_input
                if pending_turn_before is not None
                else command.user_input
            )
            attack_context = stored_attack_context or server_attack_roll_context(
                player,
                attack_source_text,
            )
            persisted_ability = (
                stored_roll_spec.get('ability')
                if isinstance(stored_roll_spec.get('ability'), dict)
                else {}
            )
            persisted_ability_key = str(persisted_ability.get('key') or '').strip().lower()
            if pending_turn_before is not None and persisted_ability_key in {'strength', 'dexterity'}:
                attack_context = {**attack_context, 'ability_key': persisted_ability_key}
            ability_key = str(attack_context.get('ability_key') or 'strength')
        else:
            ability_key = requested_ability_key(command.action_intent, pending_turn_before)
        rule_hint = apply_character_dc_adjustment(
            rule_hint,
            player,
            requested_ability_key=ability_key,
            attack_context=attack_context,
        )
        roll_spec_payload = (
            character_roll_spec(
                player,
                roll_type=rule_hint.roll_type,
                requested_ability_key=ability_key,
                dc_hint=rule_hint.dc_hint,
                attack_context=attack_context,
            )
            if rule_hint.requires_roll
            else None
        )
        authoritative_roll = None
        if roll_submission_requested:
            # A roll made on its own (no pending check, gate or contest, and words that name no check)
            # is not a check: no DC, no outcome.
            free_roll = pending_turn_before is None and not combat_roll_requested and not pvp_payload and worded_check is None
            authoritative_roll = resolve_authoritative_player_roll(
                player=player,
                rule_type=rule_hint.roll_type,
                dc_hint=rule_hint.dc_hint,
                action_intent=command.action_intent,
                pending_turn=pending_turn_before,
                attack_context=attack_context,
                free=free_roll,
            )
            if free_roll:
                rule_hint.dc_hint = None
                roll_spec_payload = None
            attack_target = (
                player_attack_target(
                    combat_snapshot_from_session(session_obj.state_snapshot),
                    actor_id=display_actor_id(player.player_id),
                    combat_action=command.action_intent.get('combat'),
                )
                if combat_roll_requested
                else None
            )
            if attack_target is not None:
                authoritative_roll = roll_against_armor_class(authoritative_roll, attack_target)
            roll_spec_payload = {
                **(roll_spec_payload or {}),
                'die': authoritative_roll['die'],
                'mode': authoritative_roll['mode'],
                'result_visibility': authoritative_roll['result_visibility'],
                'reason': authoritative_roll['reason'],
            }
            rule_hint.requires_roll = True
            rule_hint.roll_value = int(authoritative_roll['total'])
            rule_hint.outcome_deferred = False
            canonical_text = canonicalize_roll_text(command.user_input, authoritative_roll)
            command.user_input = canonical_text
            command.action_intent = canonicalize_roll_action_intent(
                command.action_intent,
                canonical_text=canonical_text,
                client_message_id=command.client_message_id,
                roll=authoritative_roll,
                pending_turn_id=(pending_turn_before.turn_id if pending_turn_before else None),
            )

        if command.is_opening:
            # Opening narration never asks for a roll.
            rule_hint.requires_roll = False
            rule_hint.roll_type = None
            rule_hint.dc_hint = None
            rule_hint.outcome_deferred = False
            roll_spec_payload = None

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
        if roll_spec_payload:
            rules_hint_payload['roll_spec'] = roll_spec_payload
        if authoritative_roll:
            rules_hint_payload['authoritative_roll'] = authoritative_roll
        if pvp_payload:
            rules_hint_payload['pvp'] = pvp_payload
        if resolved_clarification_turn_id:
            rules_hint_payload['clarification_resume'] = {
                'resolved_turn_id': resolved_clarification_turn_id,
                'selected_item_ids': self._clarification_selected_item_ids(command),
            }
        if command.message_kind:
            rules_hint_payload['message_kind'] = command.message_kind

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
                    'authoritative_roll': authoritative_roll,
                    'client_message_id': command.client_message_id,
                    'manual_segment_ids': sorted(command.manual_segment_ids),
                    'state_pipeline_override': command.state_pipeline_override,
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
                    **({'message_kind': command.message_kind} if command.message_kind else {}),
                    **({AUTO_END_TURN_REQUEST_KEY: True} if command.auto_end_turn else {}),
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
            authoritative_roll,
        )
        if not incoming_result.get('ok'):
            return
        turn_id = int(turn.turn_id)
        if authoritative_roll:
            resolved_payload_args = {
                'session_id': command.session_id,
                'turn_id': turn_id,
                'player_id': command.player_id,
                'client_message_id': command.client_message_id,
                'pending_turn_id': (
                    pending_turn_to_resolve.turn_id
                    if pending_turn_to_resolve
                    else None
                ),
                'roll': authoritative_roll,
            }
            self.emit(
                'roll_resolved',
                roll_resolved_payload(
                    **resolved_payload_args,
                    include_private_provenance=False,
                ),
                room=str(command.session_id),
                skip_sid=command.sid,
            )
            self.emit(
                'roll_resolved',
                roll_resolved_payload(
                    **resolved_payload_args,
                    include_private_provenance=True,
                ),
                room=command.sid,
            )
        self._record_phase_timing(
            'incoming_db_save',
            incoming_save_started,
            campaign_id=command.campaign_id,
            session_id=command.session_id,
        )
        self._emit_turn_status(command.session_id, turn.turn_id, 'received')

        message_payload_args = {
            'message': command.user_input,
            'speaker': player_label,
            'turn_id': turn.turn_id,
            'requires_roll': rule_hint.requires_roll,
            'rules_hint': rules_hint_payload,
            'context_version': CONTEXT_VERSION,
            'action_intent': command.action_intent,
            'client_message_id': command.client_message_id,
            'turn_number': session_turn_number,
            'message_kind': command.message_kind,
        }
        self.emit(
            'new_message',
            new_message_payload(
                **message_payload_args,
                include_private_provenance=False,
            ),
            room=str(command.session_id),
            skip_sid=command.sid,
        )
        self.emit(
            'new_message',
            new_message_payload(
                **message_payload_args,
                include_private_provenance=True,
            ),
            room=command.sid,
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

        self._continue_persisted_turn(
            turn=turn,
            command=command,
            session_obj=session_obj,
            campaign=campaign,
            player=player,
            player_label=player_label,
            rules_hint_payload=rules_hint_payload,
            resolved_turn_id=resolved_turn_id,
            session_turn_number=session_turn_number,
            pvp_target=pvp_target,
            start_time=start_time,
        )

    def _emit_recovery_required(self, command: TurnCommand, recovery_gate: dict) -> None:
        recovery_details = {
            'session_id': command.session_id,
            **recovery_required_details(recovery_gate),
        }
        recovery_message = (
            'This session is paused while the Dungeon Master reviews a partially applied turn. '
            'Some authoritative pre-narration mechanics remain applied and must not be repeated.'
            if recovery_gate['mechanicsStatus'] == 'partial'
            else (
                'This session is paused while the Dungeon Master reviews a turn whose '
                'narration was saved without authoritative mechanics.'
            )
        )
        self.emit(
            'error',
            socket_error(
                'session_recovery_required',
                recovery_message,
                recovery_details,
            ),
        )
        telemetry_event(
            'socket.send_message.session_recovery_required',
            payload={
                'sid': command.sid,
                'session_id': command.session_id,
                'campaign_id': command.campaign_id,
                'turn_id': recovery_details['turn_id'],
            },
            severity='warning',
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

    def _emit_turn_rolls(self, turn: DmTurn) -> None:
        """The rolls the engine resolved before narration, sent before the narrator starts."""

        rolls = turn_story_rolls(turn)
        if rolls:
            self.socketio.emit('turn_rolls', turn_rolls_payload(int(turn.session_id), int(turn.turn_id), rolls), room=str(turn.session_id))

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

    def _reject_typed_action_before_narration(
        self,
        *,
        command: TurnCommand,
        turn: DmTurn,
        rejection: dict,
        start_time: float,
    ) -> None:
        reason = str(rejection.get('reason') or "That can't be done right now.").strip()
        response = reason
        metadata = safe_json_loads(turn.metadata_json, {})
        metadata = metadata if isinstance(metadata, dict) else {}
        metadata['rules_rejection'] = {
            **rejection,
            'stage': 'pre_narration',
        }
        turn.metadata_json = safe_json_dumps(metadata, {})
        turn.dm_output = response
        turn.status = 'completed'
        turn.outcome_status = 'resolved'
        turn.completed_at = utc_now()
        turn.latency_ms = int((time.perf_counter() - start_time) * 1000)
        turn.llm_provider = 'rules_engine'
        turn.llm_model = 'authoritative-validator-v1'
        record_turn_event(
            session_id=turn.session_id,
            campaign_id=turn.campaign_id,
            turn_id=turn.turn_id,
            player_id=turn.player_id,
            event_type=DM_RESPONSE_EVENT,
            payload={
                'message': response,
                'metadata': {
                    'turn_id': turn.turn_id,
                    'rules_rejection': rejection,
                    'action_intent': command.action_intent,
                    'client_message_id': command.client_message_id,
                    'llm_provider': 'rules_engine',
                    'llm_model': 'authoritative-validator-v1',
                },
            },
        )
        finish_turn_lifecycle(turn)
        commit_with_retry(label='typed action rules rejection')

        details = {
            'turn_id': turn.turn_id,
            'action_type': rejection.get('action_type'),
            'action_id': rejection.get('action_id'),
        }
        self.emit(
            'error',
            socket_error('gameplay_action_invalid', response, details),
            room=command.sid,
        )
        self._emit_turn_status(
            command.session_id,
            turn.turn_id,
            'rejected',
            {'stage': 'pre_narration', **details, 'reason': reason},
        )
        self.socketio.emit(
            'session_log_update',
            session_log_update_payload(command.session_id, turn.turn_id),
            room=str(command.session_id),
        )
        telemetry_event(
            'socket.send_message.gameplay_action_invalid',
            payload={
                'session_id': command.session_id,
                'campaign_id': command.campaign_id,
                'player_id': command.player_id,
                **details,
            },
            severity='info',
        )

    def _emit_clarification_request(
        self,
        *,
        session_id: int,
        turn_id: int,
        player_id: int,
        request_sid: str,
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
            finish_turn_lifecycle(turn_obj)
            commit_with_retry(label='clarification request')
        self.socketio.emit('clarification_required', request_payload, room=request_sid)
        self._emit_turn_status(
            session_id,
            turn_id,
            'clarification_required',
            {
                'stage': 'awaiting_clarification',
                'player_id': player_id,
                'waiting_for_player_id': player_id,
            },
        )
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
        authoritative_roll: dict | None,
    ) -> dict:
        remaining_player_ids: list[int] = []
        try:
            def _save_incoming_turn():
                nonlocal remaining_player_ids
                remaining_player_ids = []
                db.session.add(turn)
                db.session.flush()
                set_logging_context(turn_id=turn.turn_id)
                pending_metadata: dict = {}

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

                # A roll recorded while others still owe theirs ends here; the
                # last roller's turn carries the narration.
                if turn.status == 'waiting_for_group_roll':
                    finish_turn_lifecycle(turn)
                else:
                    set_turn_lifecycle(turn, LIFECYCLE_ACCEPTED)

                if authoritative_roll:
                    pending_turn_id = (
                        pending_turn_to_resolve.turn_id
                        if pending_turn_to_resolve
                        else None
                    )
                    record_turn_event(
                        session_id=command.session_id,
                        campaign_id=command.campaign_id,
                        turn_id=turn.turn_id,
                        player_id=command.player_id,
                        event_type=ROLL_RESOLVED_EVENT,
                        payload={
                            'pending_turn_id': pending_turn_id,
                            'roll_value': rule_hint.roll_value,
                            'roll': authoritative_roll,
                            'metadata': {
                                'turn_id': turn.turn_id,
                                'turn_number': session_turn_number,
                                'resolved_turn_id': pending_turn_id,
                                'roll_value': rule_hint.roll_value,
                                'rule_type': rule_hint.roll_type,
                                'roll_gate': pending_metadata.get('roll_gate'),
                                'remaining_player_ids': remaining_player_ids,
                                'action_intent': command.action_intent,
                                'authoritative_roll': authoritative_roll,
                                'client_message_id': command.client_message_id,
                            },
                        },
                    )

                message_kind_fields = (
                    {'message_kind': command.message_kind} if command.message_kind else {}
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
                        **message_kind_fields,
                        'metadata': {
                            'turn_id': turn.turn_id,
                            'turn_number': session_turn_number,
                            'confidence': rule_hint.confidence,
                            'outcome_status': turn.outcome_status,
                            'resolved_turn_id': resolved_turn_id,
                            'action_intent': command.action_intent,
                            'client_message_id': command.client_message_id,
                            **message_kind_fields,
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
                self._emit_duplicate_turn(command, duplicate_turn.turn_id, detected_by='unique_constraint')
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
        triggered_segments = self.segment_service.activate_segments(
            turn=turn,
            session_id=session_id,
            segments_to_activate=segments_to_activate,
        )
        if triggered_segments:
            # Committed with the activation so a resumed turn still feeds these
            # segments to pack progress and canon after a stop.
            metadata = _turn_metadata(turn)
            metadata['triggered_segments'] = [*_turn_triggered_segments(turn), *triggered_segments]
            turn.metadata_json = safe_json_dumps(metadata, {})
        return triggered_segments

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

    def _narrate_turn(self, request: NarrationRequest, *, scripted: bool = False) -> NarrationResult:
        """Stream the turn's narration: from the configured narrator, or (``scripted``) from the scripted
        narrator exactly as Safe Mode tells it, whatever narrator is configured."""

        scripted_config = {'AIDM_LLM_PROVIDER': SCRIPTED_NARRATOR_PROVIDER, 'AIDM_LLM_MODEL': SCRIPTED_NARRATOR_MODEL}
        service = TurnNarrationService(
            TurnNarrationDependencies(
                emit=self.emit,
                sleep=self.socketio.sleep,
                stream=scripted_stream if scripted else self.stream_fn,
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
                config_get=lambda key: (scripted_config if scripted and key in scripted_config else current_app.config).get(key),
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
        if turn_lifecycle(turn_obj).get('structuredAdvanceDone'):
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
            set_turn_lifecycle(turn_obj, structuredAdvanceDone=True)
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
        engine_rolls_removed: bool = False,
        told: str = TOLD_BY_NARRATOR,
        clock: TurnClock | None = None,
    ) -> list[dict]:
        outcome = PostTurnOutcome(
            turn_id=persistence_token.turn_id,
            session_id=persistence_token.session_id,
            command=command,
            player_label=player_label,
            rules_hint_payload=rules_hint_payload,
            dm_response_text=strip_reasoning_blocks(dm_response_text).strip(),
            stream_error=stream_error,
            narration_provider=narration_provider,
            narration_model=narration_model,
            emergency_fallback=emergency_fallback,
            triggered_segments=triggered_segments,
            start_time=start_time,
            expected_status=persistence_token.expected_status,
            engine_rolls_removed=engine_rolls_removed,
            told=told,
            clock=clock,
        )
        try:
            turn, campaign = self._reload_persistence_context(persistence_token)
            db_save_started = time.perf_counter()
            self._apply_dm_response(turn=turn, campaign=campaign, outcome=outcome)
            commit_with_retry(label='DM response save')
            self._record_phase_timing(
                'db_save',
                db_save_started,
                campaign_id=campaign.campaign_id,
                session_id=turn.session_id,
            )
            self._emit_turn_status(turn.session_id, turn.turn_id, 'saved', {'stage': 'dm_response'})
            return self._complete_saved_turn(outcome, turn=turn, campaign=campaign)
        except Exception as exc:
            return self._handle_post_turn_failure(outcome, exc)

    def _apply_dm_response(self, *, turn: DmTurn, campaign: Campaign, outcome: PostTurnOutcome) -> None:
        """Write the narration outcome onto the turn; the caller commits it."""

        command = outcome.command
        rules_hint_payload = outcome.rules_hint_payload
        dm_response_text = outcome.dm_response_text
        stream_error = outcome.stream_error
        emergency_fallback = outcome.emergency_fallback
        turn.completed_at = utc_now()
        turn.latency_ms = int((time.perf_counter() - outcome.start_time) * 1000)
        turn.llm_provider = outcome.narration_provider
        turn.llm_model = outcome.narration_model
        set_logging_context(turn_id=turn.turn_id)

        if dm_response_text:
            turn.dm_output = dm_response_text
            turn.status = 'failed' if stream_error else ('degraded' if emergency_fallback else 'completed')
        else:
            turn.status = 'failed' if stream_error else 'completed'
        metadata_payload = _turn_metadata(turn)
        if stream_error:
            metadata_payload['error'] = stream_error
        if emergency_fallback:
            metadata_payload['llm_fallback'] = emergency_fallback
        if outcome.told != TOLD_BY_NARRATOR:
            metadata_payload[TURN_FLOW_KEY] = {'told': outcome.told}
        turn.metadata_json = safe_json_dumps(metadata_payload, {})

        dm_explains_no_roll = (
            stream_error is None
            and emergency_fallback is None
            and turn.roll_value is None
            and (outcome.engine_rolls_removed or self._dm_response_explains_no_roll_needed(dm_response_text))
            and not self._dm_response_requests_roll(dm_response_text)
        )
        no_roll_reason = 'engine_rolls_it' if outcome.engine_rolls_removed else 'dm_explained_no_roll_needed'
        roll_gate_payload = (
            None
            if dm_explains_no_roll or emergency_fallback or command.is_opening
            else self._roll_gate_for_turn(turn, campaign, dm_response_text)
        )
        if dm_explains_no_roll and turn.requires_roll:
            turn.requires_roll = False
            turn.rule_type = None
            turn.outcome_status = 'resolved'
            metadata_payload = _turn_metadata(turn)
            metadata_payload.pop('roll_gate', None)
            metadata_payload['roll_requirement_cleared'] = {
                'reason': no_roll_reason,
                'original_rule_type': rules_hint_payload.get('roll_type'),
                'original_reason': rules_hint_payload.get('reason'),
            }
            turn.metadata_json = safe_json_dumps(metadata_payload, {})
            rules_hint_payload['requires_roll'] = False
            rules_hint_payload['roll_type'] = None
            rules_hint_payload['dc_hint'] = None
            rules_hint_payload['outcome_deferred'] = False
            rules_hint_payload['reason'] = 'DM explained no roll was needed'
            rules_hint_payload.pop('roll_gate', None)
            rules_hint_payload.pop('remaining_player_ids', None)
            rules_hint = safe_json_loads(turn.rules_hint, {})
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
            turn.rules_hint = safe_json_dumps(rules_hint, {})
        elif roll_gate_payload:
            rule_type = roll_gate_payload.get('rule_type') or turn.rule_type or 'check'
            turn.requires_roll = True
            turn.rule_type = rule_type
            turn.outcome_status = 'deferred'
            metadata_payload = _turn_metadata(turn)
            metadata_payload['roll_gate'] = roll_gate_payload
            turn.metadata_json = safe_json_dumps(metadata_payload, {})
            rules_hint_payload['requires_roll'] = True
            rules_hint_payload['roll_type'] = rule_type
            rules_hint_payload['outcome_deferred'] = True
            rules_hint_payload['roll_gate'] = roll_gate_payload
            rules_hint_payload['remaining_player_ids'] = roll_gate_payload.get('remaining_player_ids', [])
            rules_hint = safe_json_loads(turn.rules_hint, {})
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
            turn.rules_hint = safe_json_dumps(rules_hint, {})

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
                        'llm_provider': outcome.narration_provider,
                        'llm_model': outcome.narration_model,
                        'llm_fallback': emergency_fallback,
                    },
                },
            )
        if turn.status == 'failed':
            finish_turn_lifecycle(turn)
        else:
            set_turn_lifecycle(turn, LIFECYCLE_NARRATION_SAVED)

    def _complete_saved_turn(self, outcome: PostTurnOutcome, *, turn: DmTurn, campaign: Campaign) -> list[dict]:
        """Run every step after the saved narration that has not durably run yet."""

        command = outcome.command
        post_turn_segments: list[dict] = []
        dm_succeeded = turn.status == 'completed' and bool(turn.dm_output)
        immediate_state_summary: dict = {}
        state_log: dict = {}
        post_pipeline_result: dict = {}
        campaign_pack_progress_changed = False
        story_beats: list[dict] = []
        state_failure_details: dict | None = None
        if dm_succeeded and turn_lifecycle_phase(turn) == LIFECYCLE_NARRATION_SAVED:
            dm_response_text = str(turn.dm_output or '')
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
                    turn=turn,
                    session_obj=session_for_pipeline,
                    campaign=campaign,
                    player=player_obj,
                    dm_response_text=dm_response_text,
                    active_player_ids=active_player_ids,
                    before_helper_call=self._release_clean_provider_session,
                )
                turn, _session_obj, campaign, _player_obj = self._reload_core_models(command, outcome.turn_id)
                immediate_state_summary = post_pipeline_result.get('legacyImmediateSummary') or {}
                state_log = post_pipeline_result.get('stateLog') or {}
                progress_result = update_campaign_pack_progress(
                    session_id=turn.session_id,
                    campaign_id=campaign.campaign_id,
                    triggered_segments=outcome.triggered_segments,
                    commit=False,
                )
                campaign_pack_progress_changed = bool(progress_result.changed)
                story_beats = record_turn_story_beats(turn, _session_obj)
                record_turn_story_rolls(turn, _session_obj)
                # Effects, snapshot, pack progress and the marker commit together,
                # so a resumed turn never applies the effects twice.
                set_turn_lifecycle(turn, LIFECYCLE_EFFECTS_APPLIED)
                commit_with_retry(label='post-DM state pipeline')
                self._emit_scene_state(turn.session_id, acting_player_id=turn.player_id)
            except Exception as exc:
                db.session.rollback()
                error_type = type(exc).__name__
                logger.warning(
                    'State pipeline post-DM application failed (error_type=%s).',
                    error_type,
                )
                telemetry_event(
                    'socket.state_pipeline.post_dm_failed',
                    payload={
                        'session_id': outcome.session_id,
                        'turn_id': outcome.turn_id,
                        'error_type': error_type,
                    },
                    severity='warning',
                )
                try:
                    turn, _session_obj, campaign, _player_obj = self._reload_core_models(command, outcome.turn_id)
                    immediate_state_summary = apply_immediate_state_changes(turn, campaign, dm_response_text)
                    metadata_payload = _turn_metadata(turn)
                    metadata_payload['post_dm_state'] = {
                        'status': 'legacy_fallback_applied',
                        'primary_pipeline_failed': True,
                        'recovery_required': False,
                    }
                    turn.metadata_json = safe_json_dumps(metadata_payload, {})
                    set_turn_lifecycle(turn, LIFECYCLE_EFFECTS_APPLIED)
                    commit_with_retry(label='legacy immediate state fallback')
                    self._emit_scene_state(turn.session_id, acting_player_id=turn.player_id)
                except Exception as fallback_exc:
                    db.session.rollback()
                    outcome.state_application_failed = True
                    fallback_error_type = type(fallback_exc).__name__
                    logger.warning(
                        'Immediate character state application failed (error_type=%s).',
                        fallback_error_type,
                    )
                    telemetry_event(
                        'socket.immediate_state_apply_failed',
                        payload={
                            'session_id': outcome.session_id,
                            'turn_id': outcome.turn_id,
                            'error_type': fallback_error_type,
                        },
                        severity='warning',
                    )
                    recovery_mutation = activate_turn_recovery_gate(
                        session_id=outcome.session_id,
                        turn_id=outcome.turn_id,
                    )
                    turn, _session_obj, campaign, _player_obj = self._reload_core_models(command, outcome.turn_id)
                    recovery_gate = active_turn_recovery_gate(recovery_mutation.state)
                    if recovery_gate is None:
                        raise RuntimeError('Turn recovery gate was not persisted.')
                    recovery_details = recovery_required_details(recovery_gate)
                    state_failure_details = {
                        'stage': 'state_application',
                        **{
                            key: value
                            for key, value in recovery_details.items()
                            if key != 'turn_id'
                        },
                    }
                    self.socketio.emit(
                        'error',
                        socket_error(
                            'turn_state_apply_failed',
                            self._state_apply_failure_message(recovery_gate),
                            {
                                'session_id': outcome.session_id,
                                **recovery_details,
                            },
                        ),
                        room=str(outcome.session_id),
                    )

            inventory_changes = immediate_state_summary.get('inventory_changes_applied') or []
            character_state_changes = immediate_state_summary.get('character_state_changes_applied') or []
            state_log_lines = state_log.get('lines') if isinstance(state_log, dict) else []
            pipeline_metadata = _turn_metadata(turn).get(STATE_PIPELINE_METADATA_KEY)
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
                    outcome.session_id,
                    outcome.turn_id,
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
                    outcome.session_id,
                    outcome.turn_id,
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

        turn_completed_safely = dm_succeeded and not outcome.state_application_failed
        if turn_completed_safely:
            self._advance_structured_turn_if_ready(turn_obj=turn, action_intent=command.action_intent)
            self._mark_clarification_resume_completed(command=command, resumed_turn=turn)
            canon_job = enqueue_canon_job(
                turn=turn,
                campaign=campaign,
                speaking_player_name=outcome.player_label,
                triggered_segments=outcome.triggered_segments,
            )
            self._record_turn_timings(turn, outcome)
            # The canon worker writes canon metadata on this row outside the
            # turn lock, so the turn finishes before the job can start.
            finish_turn_lifecycle(turn)
            commit_with_retry(label='canon job enqueue')
            canon_job_id = canon_job.job_id
            self._emit_turn_status(
                outcome.session_id,
                outcome.turn_id,
                'canon_pending',
                {'job_id': canon_job_id},
            )
            app = current_app._get_current_object()  # type: ignore[attr-defined]
            if current_app.config.get('TESTING') or current_app.config.get('AIDM_ENV') == 'test':
                process_canon_job(
                    canon_job_id,
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

        final_turn = db.session.get(DmTurn, outcome.turn_id)
        if final_turn is not None and turn_lifecycle_phase(final_turn) != LIFECYCLE_FINISHED:
            finish_turn_lifecycle(final_turn)
        commit_with_retry(label='post-turn final save')
        if outcome.state_application_failed:
            self._emit_turn_status(
                outcome.session_id,
                outcome.turn_id,
                'failed',
                state_failure_details or {'stage': 'state_application'},
            )
        else:
            self._emit_turn_status(
                outcome.session_id,
                outcome.turn_id,
                'saved',
                {
                    'stage': 'post_turn',
                    'storyBeats': story_beats,
                    'storyRolls': turn_story_rolls(final_turn) if final_turn is not None else [],
                },
            )

        if turn_completed_safely:
            telemetry_metric('socket.send_message.success_total', 1)
            telemetry_timing(
                'socket.turn_latency_ms',
                float((time.perf_counter() - outcome.start_time) * 1000),
                tags={'campaign_id': command.campaign_id, 'session_id': outcome.session_id},
            )
        elif outcome.state_application_failed:
            telemetry_metric('socket.send_message.state_apply_failed_total', 1)
        elif outcome.emergency_fallback:
            telemetry_metric(
                'socket.send_message.degraded_total',
                1,
                tags={
                    'provider': outcome.narration_provider or 'unknown',
                    'model': outcome.narration_model or 'unknown',
                },
            )
        elif outcome.stream_error:
            telemetry_event(
                'socket.turn_failed',
                payload={
                    'session_id': outcome.session_id,
                    'turn_id': outcome.turn_id,
                    'error': outcome.stream_error,
                },
                severity='error',
            )
        return post_turn_segments

    @staticmethod
    def _record_turn_timings(turn: DmTurn, outcome: PostTurnOutcome) -> None:
        """Write the finished turn's ``timings`` on it and add its time to act to telemetry, by turn kind."""

        clock = outcome.clock
        if clock is None:
            return
        metadata = _turn_metadata(turn)
        timings = turn_timings(
            submitted_at=clock.submitted_at,
            engine_done_at=clock.engine_done_at,
            narration_ms=clock.narration_ms,
            model_called=clock.model_called,
            narration_ended_at=clock.narration_ended_at,
            finished_at=time.perf_counter(),
            kind=turn_kind(metadata),
        )
        metadata[TIMINGS_KEY] = timings
        turn.metadata_json = safe_json_dumps(metadata, {})
        telemetry_timing('turn.time_to_act_ms', float(timings['time_to_act_ms']), tags={'kind': timings['kind']})

    @staticmethod
    def _state_apply_failure_message(recovery_gate: dict) -> str:
        if recovery_gate['mechanicsStatus'] == 'partial':
            return (
                'The narration was saved after some authoritative pre-narration mechanics '
                'were applied, but post-narration game-state changes failed. Those earlier '
                'changes remain applied; ask the Dungeon Master to review the turn without '
                'repeating them.'
            )
        return (
            'The narration was saved, but authoritative game-state changes could not '
            'be applied. Ask the Dungeon Master to review this turn before continuing.'
        )

    def _handle_post_turn_failure(self, outcome: PostTurnOutcome, exc: Exception) -> list[dict]:
        db.session.rollback()
        if outcome.state_application_failed:
            logger.error(
                'Failed to persist the failed-turn recovery gate (error_type=%s).',
                type(exc).__name__,
            )
        else:
            logger.exception('Failed to persist DM response state')
        failed_state_recovery_gate = None
        failed_turn = db.session.get(DmTurn, outcome.turn_id)
        if failed_turn:
            if outcome.state_application_failed:
                failed_state_recovery_gate = mark_turn_recovery_required(failed_turn)
                failed_turn.completed_at = utc_now()
                failed_turn.latency_ms = int(
                    (time.perf_counter() - outcome.start_time) * 1000
                )
                failed_turn.llm_provider = outcome.narration_provider
                failed_turn.llm_model = outcome.narration_model
                if outcome.dm_response_text:
                    failed_turn.dm_output = outcome.dm_response_text
                # Re-run after assigning the preserved narration so the
                # metadata accurately states that narration was saved.
                failed_state_recovery_gate = mark_turn_recovery_required(
                    failed_turn
                )
            else:
                metadata_payload = _turn_metadata(failed_turn)
                metadata_payload['post_turn_error'] = POST_TURN_PERSIST_FAILED_MESSAGE
                metadata_payload['canon_status'] = 'failed'
                if outcome.stream_error:
                    metadata_payload['error'] = outcome.stream_error
                if outcome.emergency_fallback:
                    metadata_payload['llm_fallback'] = outcome.emergency_fallback
                failed_turn.metadata_json = safe_json_dumps(metadata_payload, {})
                if failed_turn.status == outcome.expected_status:
                    if outcome.dm_response_text:
                        # The DM response save never committed. Save the
                        # narration as unfinished so the retry the player is
                        # asked to make resumes at the post-DM step.
                        self._apply_dm_response(
                            turn=failed_turn,
                            campaign=db.session.get(Campaign, failed_turn.campaign_id),
                            outcome=outcome,
                        )
                    else:
                        failed_turn.completed_at = utc_now()
                        failed_turn.latency_ms = int(
                            (time.perf_counter() - outcome.start_time) * 1000
                        )
                        failed_turn.llm_provider = outcome.narration_provider
                        failed_turn.llm_model = outcome.narration_model
                        failed_turn.status = 'failed'
                        finish_turn_lifecycle(failed_turn)
                elif failed_turn.dm_output:
                    failed_turn.status = (
                        'degraded'
                        if metadata_payload.get('llm_fallback')
                        else 'completed'
                    )
            commit_with_retry(label='post-turn failure metadata')
        if outcome.state_application_failed:
            recovery_gate = failed_state_recovery_gate or {
                'turnId': outcome.turn_id,
                'mechanicsStatus': 'none',
                'mechanicsApplied': False,
                'preDmMechanicsApplied': False,
                'preDmAppliedChangeCount': 0,
                'postDmMechanicsApplied': False,
            }
            recovery_details = recovery_required_details(recovery_gate)
            self._emit_turn_status(
                outcome.session_id,
                outcome.turn_id,
                'failed',
                {
                    'stage': 'state_application',
                    **{
                        key: value
                        for key, value in recovery_details.items()
                        if key != 'turn_id'
                    },
                },
            )
            self.socketio.emit(
                'error',
                socket_error(
                    'turn_state_apply_failed',
                    self._state_apply_failure_message(recovery_gate),
                    {
                        'session_id': outcome.session_id,
                        **recovery_details,
                    },
                ),
                room=str(outcome.session_id),
            )
            telemetry_event(
                'socket.turn_recovery.activation_failed',
                payload={
                    'session_id': outcome.session_id,
                    'turn_id': outcome.turn_id,
                    'error_type': type(exc).__name__,
                },
                severity='error',
            )
            return []
        self._emit_turn_status(
            outcome.session_id,
            outcome.turn_id,
            'failed',
            {'stage': 'post_turn', 'error': POST_TURN_PERSIST_FAILED_MESSAGE},
        )
        self.socketio.emit(
            'error',
            socket_error(
                'turn_persist_failed',
                'The DM response was generated but could not be saved. Please retry; continuity may be affected.',
                {
                    'session_id': outcome.session_id,
                    'turn_id': outcome.turn_id,
                },
            ),
            room=str(outcome.session_id),
        )
        telemetry_event(
            'socket.dm_persist_failed',
            payload={
                'session_id': outcome.session_id,
                'turn_id': outcome.turn_id,
                'error_type': type(exc).__name__,
            },
            severity='error',
        )
        return []
