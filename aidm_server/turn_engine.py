from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

from flask import current_app

from aidm_server.action_intent import apply_action_intent_to_rule_hint, strip_reserved_admin_prefix
from aidm_server.canon_jobs import enqueue_canon_job, process_canon_job
from aidm_server.character_state import (
    apply_character_dc_adjustment,
    character_state_for_player,
    inventory_contains,
    requested_gold_spend,
)
from aidm_server.canon_inventory import OWNED_ITEM_ACTIONS
from aidm_server.database import commit_with_retry, db, run_with_commit_retry
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
from aidm_server.segment_triggers import evaluate_segment_trigger, parse_trigger_spec
from aidm_server.segment_state import build_segment_state_payload
from aidm_server.services.campaign_pack_progress import update_campaign_pack_progress
from aidm_server.socket_contracts import (
    dm_chunk_payload,
    dm_response_end_payload,
    dm_response_start_payload,
    new_message_payload,
    roll_required_payload,
    segment_triggered_payload,
    session_log_update_payload,
    socket_error_payload as socket_error,
    turn_duplicate_payload,
    turn_status_payload,
)
from aidm_server.telemetry import telemetry_event, telemetry_metric, telemetry_timing
from aidm_server.text_sanitization import ReasoningBlockFilter, strip_reasoning_blocks
from aidm_server.time_utils import utc_now
from aidm_server.turn_control import advance_structured_turn, turn_control_from_session, turn_control_update_payload
from aidm_server.turn_coordinator import session_turn_coordinator
from aidm_server.turn_events import (
    DM_RESPONSE_EVENT,
    PLAYER_MESSAGE_EVENT,
    ROLL_RESOLVED_EVENT,
    SEGMENT_TRIGGERED_EVENT,
    record_turn_event,
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


logger = logging.getLogger(__name__)

_HARMFUL_PVP_RE = re.compile(
    r'\b(?:attack|attacks|attacked|behead\w*|choke\w*|cut|cuts|decapitat\w*|execute\w*|'
    r'hit|hits|kick\w*|kill\w*|maim\w*|murder\w*|punch\w*|shoot\w*|slash\w*|slice\w*|'
    r'slit|smite\w*|stab\w*|strike\w*)\b|\bhead\s+off\b',
    re.IGNORECASE,
)
_GENERIC_PLAYER_RACE_LABELS = {'human', 'elf', 'dwarf', 'gnome', 'halfling'}
_INTERACTION_TARGET_CUE_RE = re.compile(
    r'\b(?:to|at|toward|towards|with|from)\s+(?:the\s+)?(?P<target>[A-Za-z][A-Za-z0-9\'\-\s]{1,80}?)(?:\s*:|[.!?]|$)',
    re.IGNORECASE,
)


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

    @staticmethod
    def _is_admin_override(action_intent: dict | None) -> bool:
        return isinstance(action_intent, dict) and action_intent.get('kind') == 'admin'

    @staticmethod
    def _admin_model_input(user_input: str) -> str:
        clean = strip_reserved_admin_prefix(user_input)
        return (
            'ADMIN OVERRIDE (authenticated):\n'
            f'{clean}\n\n'
            'This is an out-of-character table administrator directive. Make it happen in the next DM response. '
            'Do not ask for a roll, do not defer the outcome, and do not refuse due to normal story uncertainty. '
            'If the directive changes established state, make the change true and give a concise in-world explanation.'
        )

    @staticmethod
    def _interaction_model_input(user_input: str, action_intent: dict | None, actor_label: str) -> str:
        if not isinstance(action_intent, dict) or action_intent.get('kind') != 'interact':
            return user_input
        interaction = action_intent.get('interaction') if isinstance(action_intent.get('interaction'), dict) else {}
        target = action_intent.get('target') if isinstance(action_intent.get('target'), dict) else {}
        target_character = str(target.get('character_name') or 'another player character').strip()
        target_player = str(target.get('player_name') or '').strip()
        target_kind = str(target.get('kind') or 'player').strip().lower()
        interaction_type = str(interaction.get('type') or 'act_on').strip()
        interaction_labels = {
            'speak_to': 'speak to the target',
            'act_on': 'take an action directed at the target',
            'give_to': 'give something to the target',
            'take_from': 'try to take something from the target',
        }
        clean_input = str(user_input or '').strip()
        target_player_line = f'\nTarget account/profile label (not a character): {target_player}' if target_player else ''
        if target_kind == 'npc':
            return (
                'PLAYER-TO-NPC INTERACTION:\n'
                f'Acting character: {actor_label}\n'
                f'Target NPC: {target_character}'
                f'{target_player_line}\n'
                f'Interaction intent: {interaction_labels.get(interaction_type, "interact with the target")}\n\n'
                'Player message:\n'
                f'{clean_input}\n\n'
                'DM handling: Resolve this as an interaction with a current-scene NPC. Ask for a roll when the '
                'action needs one, and only apply inventory, relationship, health, or scene changes when the outcome is clear.'
            )
        return (
            'PLAYER-TO-PLAYER INTERACTION:\n'
            f'Acting character: {actor_label}\n'
            f'Target character: {target_character}'
            f'{target_player_line}\n'
            f'Interaction intent: {interaction_labels.get(interaction_type, "interact with the target")}\n\n'
            'Player message:\n'
            f'{clean_input}\n\n'
            'DM handling: Treat the target as a player character in this campaign, even if they have not spoken in '
            'the current chat log yet. Keep the acting character and target character distinct. Resolve the speech '
            'or action as directed at the target, ask for a roll when the action needs one, and do not narrate the '
            "target player's voluntary response for them."
        )

    @staticmethod
    def _item_model_input(user_input: str, action_intent: dict | None, actor_label: str) -> str:
        if not isinstance(action_intent, dict) or action_intent.get('kind') != 'item':
            return user_input
        item = action_intent.get('item') if isinstance(action_intent.get('item'), dict) else {}
        item_name = str(item.get('name') or 'item').strip()
        quantity = item.get('quantity') or 1
        inventory_action = str(action_intent.get('inventory_action') or 'use').strip()
        cost_gold = action_intent.get('cost_gold')
        cost_line = f'\nKnown price/value: {cost_gold} gold' if cost_gold else ''
        action_labels = {
            'pick_up': 'pick up',
            'buy': 'buy',
            'use': 'use',
            'drop': 'drop',
            'give': 'give',
            'sell': 'sell',
            'equip': 'equip',
            'unequip': 'unequip',
        }
        return (
            'PLAYER INVENTORY INTENT:\n'
            f'Acting character: {actor_label}\n'
            f'Attempted action: {action_labels.get(inventory_action, inventory_action)}\n'
            f'Item: {item_name} x{quantity}'
            f'{cost_line}\n\n'
            'Player message:\n'
            f'{str(user_input or "").strip()}\n\n'
            'DM handling: Treat this as an attempted inventory action, not an automatic state change. '
            'Narrate whether it actually succeeds. If it succeeds, explicitly say the character picks up, buys, '
            'drops, gives, sells, consumes, uses up, equips, or unequips the named item so the state pipeline can update inventory. '
            'If it fails, explicitly say why it fails.'
        )

    @staticmethod
    def _pvp_model_input(user_input: str, actor_label: str, target_player: Player) -> str:
        target_label = target_player.character_name or target_player.name or f'Player {target_player.player_id}'
        return (
            'PLAYER-VS-PLAYER ACTION (ALLOWED):\n'
            f'Acting character: {actor_label}\n'
            f'Target player character: {target_label}\n\n'
            'Player message:\n'
            f'{str(user_input or "").strip()}\n\n'
            'DM handling: Allow PvP as an attempted action. Do not reject the attempt just because it targets '
            'another player character. Do not narrate final injury, death, incapacitation, theft, forced movement, '
            'or loss of agency yet. Ask for the appropriate attack roll, opposed check, saving throw, or contested '
            'rolls from the involved players, then defer the final outcome until the required rolls are recorded.'
        )

    @classmethod
    def _model_input_for_action(
        cls,
        user_input: str,
        action_intent: dict | None,
        actor_label: str,
        pvp_target: Player | None = None,
    ) -> str:
        if pvp_target:
            return cls._pvp_model_input(user_input, actor_label, pvp_target)
        if cls._is_admin_override(action_intent):
            return cls._admin_model_input(user_input)
        if isinstance(action_intent, dict) and action_intent.get('kind') == 'item':
            return cls._item_model_input(user_input, action_intent, actor_label)
        if isinstance(action_intent, dict) and action_intent.get('kind') == 'interact':
            return cls._interaction_model_input(user_input, action_intent, actor_label)
        return user_input

    @staticmethod
    def _player_is_available_for_campaign(player: Player | None, campaign: Campaign) -> bool:
        return bool(
            player
            and player.workspace_id == campaign.workspace_id
            and player.campaign_id == campaign.campaign_id
        )

    @staticmethod
    def _target_label_regex(label: str) -> str:
        words = [re.escape(part) for part in re.findall(r'[a-z0-9]+', str(label or '').lower())]
        if not words:
            return ''
        return r'\b' + r'[\W_]+'.join(words) + r"(?:'s|s)?\b"

    @classmethod
    def _player_target_labels(cls, player: Player) -> list[str]:
        labels: list[str] = []
        for value in (player.character_name, player.name):
            text = str(value or '').strip()
            if text and text.lower() not in {label.lower() for label in labels}:
                labels.append(text)
        race = str(player.race or '').strip().lower()
        if race and (race not in _GENERIC_PLAYER_RACE_LABELS or race == 'orc'):
            labels.extend([race, f'the {race}'])
        return labels

    @classmethod
    def _harmful_text_targets_player(cls, text: str, player: Player) -> bool:
        if not _HARMFUL_PVP_RE.search(text or ''):
            return False
        normalized = str(text or '').lower()
        harmful_pattern = f'(?:{_HARMFUL_PVP_RE.pattern})'
        for label in cls._player_target_labels(player):
            label_pattern = cls._target_label_regex(label)
            if not label_pattern:
                continue
            harm_then_label = re.compile(
                rf'{harmful_pattern}(?:\W+\w+){{0,8}}\W+{label_pattern}',
                re.IGNORECASE,
            )
            label_then_harm = re.compile(
                rf'{label_pattern}(?:\W+\w+){{0,8}}\W+{harmful_pattern}',
                re.IGNORECASE,
            )
            if harm_then_label.search(normalized) or label_then_harm.search(normalized):
                return True
        return False

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
    def _dm_response_requests_group_roll(text: str) -> bool:
        candidate = (text or '').lower()
        group_markers = (
            'both of you',
            'you both',
            'all of you',
            'everyone',
            'each of you',
            'every player',
            'all players',
            'the party',
        )
        if not any(marker in candidate for marker in group_markers):
            return False
        return 'roll' in candidate or 'check' in candidate or 'saving throw' in candidate

    def _dm_response_requests_roll(self, text: str) -> bool:
        return self.response_mentions_roll_request(text) or self._dm_response_requests_group_roll(text)

    @staticmethod
    def _roll_type_from_dm_response(text: str, fallback: str | None = None) -> str:
        candidate = (text or '').lower()
        if re.search(r'\binitiative\b', candidate):
            return 'initiative'
        if re.search(
            r'\bspell\b|\bmagic\b|\bcast\b|\bconjure\b|\bsummon\b|\bsorcery\b|'
            r'\bsorcerous\b|\btelekinesis\b|\blevitat\w*\b|\bwild magic\b',
            candidate,
        ):
            return 'spell'
        if re.search(r'\battack\b|\bweapon\b', candidate):
            return 'attack'
        if re.search(r'\bstealth\b|\bsneak\b|\bhide\b', candidate):
            return 'stealth'
        if re.search(r'\bpersuasion\b|\bdeception\b|\bintimidation\b|\bcharisma\b', candidate):
            return 'social'
        if re.search(r'\binvestigation\b|\barcana\b|\bhistory\b|\bintelligence\b', candidate):
            return 'lore'
        if re.search(r'\bathletics\b|\bstrength\b', candidate):
            return 'athletics'
        if re.search(r'\bacrobatics\b|\bdexterity\b', candidate):
            return 'mobility'
        return fallback or 'check'

    def _candidate_roll_gate_player_ids(self, session_id: int, campaign: Campaign, fallback_player_id: int | None) -> list[int]:
        active_ids = []
        if self.active_player_ids:
            active_ids = [player_id for player_id in self.active_player_ids(session_id) if player_id]
        if active_ids:
            return list(dict.fromkeys(active_ids))

        query = Player.query.filter_by(workspace_id=campaign.workspace_id)
        players = query.order_by(Player.created_at.asc(), Player.player_id.asc()).limit(12).all()
        player_ids = [player.player_id for player in players if self._player_is_available_for_campaign(player, campaign)]
        if player_ids:
            return list(dict.fromkeys(player_ids))
        return [fallback_player_id] if fallback_player_id else []

    def _roll_gate_for_turn(self, turn: DmTurn, campaign: Campaign, dm_response_text: str) -> dict | None:
        dm_requested_roll = self._dm_response_requests_roll(dm_response_text)
        if not ((turn.requires_roll and turn.outcome_status == 'deferred') or dm_requested_roll):
            return None
        if turn.roll_value is not None:
            return None
        roll_type = self._roll_type_from_dm_response(dm_response_text, turn.rule_type)
        rules_hint = safe_json_loads(turn.rules_hint, {})
        rules_hint = rules_hint if isinstance(rules_hint, dict) else {}
        pvp_payload = rules_hint.get('pvp') if isinstance(rules_hint.get('pvp'), dict) else {}
        pvp_target_player_id = _coerce_player_id(pvp_payload.get('target_player_id')) if pvp_payload else None
        if pvp_target_player_id:
            required_player_ids = list(dict.fromkeys([player_id for player_id in [turn.player_id, pvp_target_player_id] if player_id]))
            resolved_player_ids = [turn.player_id] if turn.roll_value is not None and turn.player_id else []
            remaining_player_ids = [player_id for player_id in required_player_ids if player_id not in set(resolved_player_ids)]
            return {
                'scope': 'pvp_contest',
                'rule_type': roll_type,
                'required_player_ids': required_player_ids,
                'resolved_player_ids': resolved_player_ids,
                'remaining_player_ids': remaining_player_ids,
                'target_player_id': pvp_target_player_id,
            }
        required_player_ids = [turn.player_id] if turn.player_id else []
        scope = 'single_player'
        if self._dm_response_requests_group_roll(dm_response_text):
            group_ids = self._candidate_roll_gate_player_ids(turn.session_id, campaign, turn.player_id)
            if len(group_ids) > 1:
                required_player_ids = group_ids
                scope = 'group'
        if not required_player_ids:
            return None
        return {
            'scope': scope,
            'rule_type': roll_type,
            'required_player_ids': required_player_ids,
            'resolved_player_ids': [],
            'remaining_player_ids': required_player_ids,
        }

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
        snapshot = safe_json_loads(session_obj.state_snapshot, {})
        if not isinstance(snapshot, dict):
            return None
        scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
        active_npc_ids = {
            str(value).strip()
            for value in scene.get('activeNpcIds', [])
            if str(value or '').strip()
        } if isinstance(scene.get('activeNpcIds'), list) else set()
        scene_location_id = str(scene.get('locationId') or '').strip()
        target_npc_id = str(target.get('npc_id') or target.get('npcId') or '').strip()
        target_name = str(target.get('character_name') or target.get('name') or '').strip().lower()
        npc_records = []
        for key in ('knownNpcs', 'partyNpcs'):
            value = snapshot.get(key)
            if isinstance(value, list):
                npc_records.extend([record for record in value if isinstance(record, dict)])
        for npc in npc_records:
            npc_id = str(npc.get('id') or npc.get('npcId') or '').strip()
            npc_name = str(npc.get('name') or '').strip()
            if target_npc_id and npc_id != target_npc_id:
                continue
            if not target_npc_id and target_name and npc_name.lower() != target_name:
                continue
            if not npc_id and not npc_name:
                continue
            if active_npc_ids and npc_id not in active_npc_ids:
                continue
            npc_location_id = str(npc.get('locationId') or '').strip()
            if not active_npc_ids and npc_location_id and scene_location_id and npc_location_id != scene_location_id:
                continue
            return {
                'npc_id': npc_id or target_npc_id,
                'character_name': npc_name or target.get('character_name') or 'Scene NPC',
                'player_name': str(npc.get('role') or npc.get('disposition') or 'Current scene NPC').strip(),
            }
        return None

    @classmethod
    def _current_scene_npc_target_from_text(cls, session_obj: Session, text: str) -> dict | None:
        snapshot = safe_json_loads(session_obj.state_snapshot, {})
        if not isinstance(snapshot, dict):
            return None
        scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
        active_npc_ids = {
            str(value).strip()
            for value in scene.get('activeNpcIds', [])
            if str(value or '').strip()
        } if isinstance(scene.get('activeNpcIds'), list) else set()
        scene_location_id = str(scene.get('locationId') or '').strip()
        npc_records: list[dict] = []
        for key in ('knownNpcs', 'partyNpcs'):
            value = snapshot.get(key)
            if isinstance(value, list):
                npc_records.extend([record for record in value if isinstance(record, dict)])
        if not npc_records:
            return None

        normalized_text = str(text or '').lower()
        explicit_target_terms = {normalized_text}
        for match in _INTERACTION_TARGET_CUE_RE.finditer(text or ''):
            target = str(match.group('target') or '').strip().lower()
            if target:
                explicit_target_terms.add(target)

        matches: list[tuple[int, dict]] = []
        for npc in npc_records:
            npc_id = str(npc.get('id') or npc.get('npcId') or '').strip()
            npc_name = str(npc.get('name') or '').strip()
            if not npc_id or not npc_name:
                continue
            if active_npc_ids and npc_id not in active_npc_ids:
                continue
            npc_location_id = str(npc.get('locationId') or '').strip()
            if not active_npc_ids and npc_location_id and scene_location_id and npc_location_id != scene_location_id:
                continue
            labels = [npc_name, npc_id.replace('_', ' ')]
            aliases = npc.get('aliases') if isinstance(npc.get('aliases'), list) else []
            labels.extend(str(alias) for alias in aliases if str(alias or '').strip())
            best_score = 0
            for label in labels:
                label_text = str(label or '').strip().lower()
                if not label_text:
                    continue
                label_pattern = cls._target_label_regex(label_text)
                if label_pattern and re.search(label_pattern, normalized_text, re.IGNORECASE):
                    best_score = max(best_score, 100 + len(label_text))
                    continue
                if any(label_text == term or label_text in term or term in label_text for term in explicit_target_terms):
                    best_score = max(best_score, 50 + len(label_text))
            if best_score:
                matches.append((best_score, npc))

        if not matches:
            return None
        matches.sort(key=lambda item: item[0], reverse=True)
        npc = matches[0][1]
        return {
            'npc_id': str(npc.get('id') or npc.get('npcId') or '').strip(),
            'character_name': str(npc.get('name') or 'Scene NPC').strip(),
            'player_name': str(npc.get('role') or npc.get('disposition') or 'Current scene NPC').strip(),
        }

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
        if not self._player_is_available_for_campaign(target_player, campaign):
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
        if self._is_admin_override(command.action_intent):
            return None
        text = str(command.user_input or '')
        if not _HARMFUL_PVP_RE.search(text):
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
            if self._harmful_text_targets_player(text, player):
                return player
        return None

    @staticmethod
    def _pvp_rules_payload(target_player: Player | None) -> dict | None:
        if not target_player:
            return None
        return {
            'allowed': True,
            'requires_contested_resolution': True,
            'target_player_id': target_player.player_id,
            'target_character_name': target_player.character_name or target_player.name or f'Player {target_player.player_id}',
        }

    @staticmethod
    def _apply_pvp_rule_hint(rule_hint: RuleHint, pvp_payload: dict | None) -> RuleHint:
        if not pvp_payload:
            return rule_hint
        rule_hint.requires_roll = True
        if not rule_hint.roll_type or rule_hint.roll_type == 'check':
            rule_hint.roll_type = 'attack'
        rule_hint.dc_hint = rule_hint.dc_hint or 'contested by target player or DM-set defense'
        rule_hint.reason = f"Harmful PvP action targeting {pvp_payload['target_character_name']}; contested resolution required"
        rule_hint.confidence = max(rule_hint.confidence or 0.0, 0.97)
        rule_hint.outcome_deferred = True
        return rule_hint

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

        if not self._player_is_available_for_campaign(player, campaign):
            self.emit('error', socket_error('campaign_mismatch', 'Player is not available in this campaign'))
            telemetry_event(
                'socket.send_message.campaign_mismatch',
                payload={'sid': command.sid, 'player_id': command.player_id, 'campaign_id': command.campaign_id},
                severity='warning',
            )
            return

        if not self._prepare_interaction_target(command, campaign, session_obj):
            return
        if not self._validate_character_limits(command, player):
            return
        pvp_target = self._harmful_pvp_target(command, campaign)
        pvp_payload = self._pvp_rules_payload(pvp_target)

        player_label = player.character_name
        is_admin_override = self._is_admin_override(command.action_intent)
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

        if command.client_message_id:
            duplicate_turn = (
                DmTurn.query.filter(
                    DmTurn.session_id == command.session_id,
                    DmTurn.player_id == command.player_id,
                    DmTurn.metadata_json.contains(f'"client_message_id": "{command.client_message_id}"'),
                )
                .order_by(DmTurn.turn_id.desc())
                .first()
            )
            if duplicate_turn:
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
                    },
                )
                return

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
            logger.warning('Pre-DM state pipeline failed: %s', str(exc))
            telemetry_event(
                'socket.state_pipeline.pre_dm_failed',
                payload={'session_id': command.session_id, 'turn_id': turn.turn_id, 'error': str(exc)},
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
            rules_hint_payload = augment_rules_hint_with_state_packet(
                rules_hint_payload,
                pre_pipeline_result.get('dmContextPacket') or {},
            )
            turn.rules_hint = safe_json_dumps(rules_hint_payload, {})
            db.session.flush()

        triggered_segments = self._evaluate_segments(
            turn=turn,
            campaign=campaign,
            command=command,
            allowed_trigger_types={'keywords'},
            include_manual=False,
        )
        for segment_payload in triggered_segments:
            self.emit('segment_triggered', segment_payload, room=str(command.session_id))

        dm_response_text, stream_error = self._narrate_turn(
            turn=turn,
            campaign=campaign,
            player_label=player_label,
            world_id=campaign.world_id,
            user_input=command.user_input,
            model_user_input=self._model_input_for_action(command.user_input, command.action_intent, player_label, pvp_target),
            rules_hint_payload=rules_hint_payload,
            resolved_turn_id=resolved_turn_id,
        )

        # Keep the per-session coordinator locked until the DM response has a
        # durable DmTurn row and timeline event. Canon extraction can continue
        # asynchronously after that saved boundary.
        self._emit_turn_status(command.session_id, turn.turn_id, 'saving')
        post_turn_segments = self._persist_turn_outcome(
            turn=turn,
            campaign=campaign,
            command=command,
            player_label=player_label,
            rules_hint_payload=rules_hint_payload,
            dm_response_text=dm_response_text,
            stream_error=stream_error,
            triggered_segments=triggered_segments,
            start_time=start_time,
        )
        for segment_payload in post_turn_segments:
            self.socketio.emit('segment_triggered', segment_payload, room=str(command.session_id))

        self.socketio.emit(
            'session_log_update',
            session_log_update_payload(command.session_id, turn.turn_id),
            room=str(command.session_id),
        )

    def _background_post_turn(
        self,
        app,
        *,
        turn: DmTurn,
        campaign: Campaign,
        command: TurnCommand,
        player_label: str,
        rules_hint_payload: dict,
        dm_response_text: str,
        stream_error: str | None,
        triggered_segments: list[dict],
        start_time: float,
    ):
        with app.app_context():
            # Re-attach ORM objects to the new session so lazy loads work.
            turn = db.session.merge(turn)
            campaign = db.session.merge(campaign)
            self._emit_turn_status(command.session_id, turn.turn_id, 'saving')

            post_turn_segments = self._persist_turn_outcome(
                turn=turn,
                campaign=campaign,
                command=command,
                player_label=player_label,
                rules_hint_payload=rules_hint_payload,
                dm_response_text=dm_response_text,
                stream_error=stream_error,
                triggered_segments=triggered_segments,
                start_time=start_time,
            )
            for segment_payload in post_turn_segments:
                self.socketio.emit('segment_triggered', segment_payload, room=str(command.session_id))

            self.socketio.emit(
                'session_log_update',
                session_log_update_payload(command.session_id, turn.turn_id),
                room=str(command.session_id),
            )

    def _emit_turn_status(self, session_id: int, turn_id: int | None, status: str, details: dict | None = None):
        self.socketio.emit('turn_status', turn_status_payload(session_id, turn_id, status, details), room=str(session_id))

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
        return build_segment_state_payload(session_id, campaign)

    def _activate_segments(
        self,
        *,
        turn: DmTurn,
        session_id: int,
        segments_to_activate: list[tuple[CampaignSegment, dict]],
    ) -> list[dict]:
        triggered_segments: list[dict] = []
        for seg, payload in segments_to_activate:
            seg.is_triggered = True
            triggered_segments.append(payload)
            record_turn_event(
                session_id=session_id,
                campaign_id=turn.campaign_id,
                turn_id=turn.turn_id,
                player_id=turn.player_id,
                event_type=SEGMENT_TRIGGERED_EVENT,
                payload={
                    'title': seg.title,
                    'reason': payload.get('reason'),
                    'segment_id': seg.segment_id,
                    'metadata': {'turn_id': turn.turn_id, 'reason': payload.get('reason')},
                },
            )
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
        triggered_segments: list[dict] = []
        automatic_enabled = bool(current_app.config.get('AIDM_SEGMENT_EVALUATOR_ENABLED', True))
        if not (automatic_enabled or (include_manual and command.manual_segment_ids)):
            return triggered_segments

        try:
            segments_to_activate: list[tuple[CampaignSegment, dict]] = []
            if automatic_enabled:
                session_state_payload, campaign_state = self._segment_state_payload(command.session_id, campaign)
                untriggered_segments = CampaignSegment.query.filter_by(
                    campaign_id=command.campaign_id,
                    is_triggered=False,
                ).all()

                for seg in untriggered_segments:
                    trigger_type = parse_trigger_spec(seg.trigger_condition).trigger_type
                    if trigger_type == 'manual':
                        continue
                    if allowed_trigger_types is not None and trigger_type not in allowed_trigger_types:
                        continue
                    matched, reason, trigger_spec = evaluate_segment_trigger(
                        trigger_condition=seg.trigger_condition,
                        player_message=command.user_input,
                        session_state=session_state_payload,
                        campaign_state=campaign_state,
                    )
                    if not matched:
                        continue

                    payload = segment_triggered_payload(
                        segment_id=seg.segment_id,
                        title=seg.title,
                        description=seg.description,
                        reason=reason,
                        trigger_spec=trigger_spec,
                    )
                    segments_to_activate.append((seg, payload))

            if include_manual and command.manual_segment_ids:
                manual_segments = (
                    CampaignSegment.query.filter(
                        CampaignSegment.campaign_id == command.campaign_id,
                        CampaignSegment.segment_id.in_(command.manual_segment_ids),
                        CampaignSegment.is_triggered.is_(False),
                    ).all()
                )
                for seg in manual_segments:
                    payload = segment_triggered_payload(
                        segment_id=seg.segment_id,
                        title=seg.title,
                        description=seg.description,
                        reason='manual_override',
                        trigger_spec={'trigger_type': 'manual', 'raw': {'source': 'client_override'}},
                    )
                    if not any(existing.segment_id == seg.segment_id for existing, _payload in segments_to_activate):
                        segments_to_activate.append((seg, payload))

            triggered_segments = self._activate_segments(
                turn=turn,
                session_id=command.session_id,
                segments_to_activate=segments_to_activate,
            )
            update_campaign_pack_progress(
                session_id=command.session_id,
                campaign_id=command.campaign_id,
                triggered_segments=triggered_segments,
            )
            commit_with_retry(label='segment evaluation')
            if triggered_segments:
                telemetry_metric('socket.segment_triggered_total', len(triggered_segments))
        except Exception as exc:
            db.session.rollback()
            logger.error('Segment evaluation failed: %s', str(exc))
            telemetry_event(
                'socket.segment_evaluation_failed',
                payload={'session_id': command.session_id, 'campaign_id': command.campaign_id, 'error': str(exc)},
                severity='error',
            )
            return []

        return triggered_segments

    def _narrate_turn(
        self,
        *,
        turn: DmTurn,
        campaign: Campaign,
        player_label: str,
        world_id: int,
        user_input: str,
        model_user_input: str,
        rules_hint_payload: dict,
        resolved_turn_id: int | None,
    ) -> tuple[str, str | None]:
        context_started = time.perf_counter()
        active_player_ids = []
        if self.active_player_ids:
            active_player_ids = [player_id for player_id in self.active_player_ids(turn.session_id) if player_id]
        context = build_dm_context(
            world_id,
            campaign.campaign_id,
            turn.session_id,
            query_text=user_input,
            active_player_ids=active_player_ids,
            current_player_id=turn.player_id,
        )
        self._record_phase_timing(
            'context_build',
            context_started,
            campaign_id=campaign.campaign_id,
            session_id=turn.session_id,
        )
        self._emit_turn_status(turn.session_id, turn.turn_id, 'narrating')
        telemetry_event(
            'socket.dm_stream_started',
            payload={
                'session_id': turn.session_id,
                'campaign_id': campaign.campaign_id,
                'turn_id': turn.turn_id,
                'provider': current_app.config.get('AIDM_LLM_PROVIDER'),
                'model': current_app.config.get('AIDM_LLM_MODEL'),
                'context_version': CONTEXT_VERSION,
            },
        )
        self.emit(
            'dm_response_start',
            dm_response_start_payload(
                session_id=turn.session_id,
                turn_id=turn.turn_id,
                requires_roll=turn.requires_roll,
                rules_hint=rules_hint_payload,
                context_version=CONTEXT_VERSION,
                turn_number=rules_hint_payload.get('turn_number'),
            ),
            room=str(turn.session_id),
        )

        dm_response_text = ''
        stream_error = None
        reasoning_filter = ReasoningBlockFilter()
        provider_started = time.perf_counter()
        first_token_recorded = False
        try:
            for chunk in self.stream_fn(
                model_user_input,
                context,
                speaking_player={'character_name': player_label, 'player_id': str(turn.player_id)},
                rules_hint=rules_hint_payload,
            ):
                if not chunk:
                    continue
                if not first_token_recorded:
                    self._record_phase_timing(
                        'provider_time_to_first_token',
                        provider_started,
                        campaign_id=campaign.campaign_id,
                        session_id=turn.session_id,
                    )
                    first_token_recorded = True
                chunk = reasoning_filter.filter(chunk)
                if not chunk:
                    continue
                self.emit(
                    'dm_chunk',
                    dm_chunk_payload(
                        chunk=chunk,
                        session_id=turn.session_id,
                        turn_id=turn.turn_id,
                        requires_roll=turn.requires_roll,
                        rules_hint=rules_hint_payload,
                        context_version=CONTEXT_VERSION,
                        turn_number=rules_hint_payload.get('turn_number'),
                    ),
                    room=str(turn.session_id),
                )
                self.socketio.sleep(0)
                dm_response_text += chunk
            final_chunk = reasoning_filter.finish()
            if final_chunk:
                self.emit(
                    'dm_chunk',
                    dm_chunk_payload(
                        chunk=final_chunk,
                        session_id=turn.session_id,
                        turn_id=turn.turn_id,
                        requires_roll=turn.requires_roll,
                        rules_hint=rules_hint_payload,
                        context_version=CONTEXT_VERSION,
                        turn_number=rules_hint_payload.get('turn_number'),
                    ),
                    room=str(turn.session_id),
                )
                self.socketio.sleep(0)
                dm_response_text += final_chunk
        except Exception as exc:
            stream_error = str(exc)
            logger.error('Error generating streamed DM response: %s', stream_error)
            self.emit('error', socket_error('dm_generation_failed', 'Error generating DM response', {'detail': stream_error}))
            telemetry_event(
                'socket.dm_generation_failed',
                payload={'session_id': turn.session_id, 'turn_id': turn.turn_id, 'error': stream_error},
                severity='error',
            )
        finally:
            self._record_phase_timing(
                'provider_total',
                provider_started,
                campaign_id=campaign.campaign_id,
                session_id=turn.session_id,
            )

        if turn.requires_roll and turn.roll_value is None and not self.response_mentions_roll_request(dm_response_text):
            injected_prompt = self.build_roll_prompt(
                RuleHint(
                    requires_roll=True,
                    roll_type=turn.rule_type,
                    dc_hint=safe_json_loads(turn.rules_hint, {}).get('dc_hint'),
                    reason='Roll prompt injected',
                    confidence=turn.confidence or 1.0,
                    roll_value=None,
                    outcome_deferred=True,
                ),
                pending_turn_id=resolved_turn_id,
            )
            injected_chunk = f'\n\n{injected_prompt}' if dm_response_text.strip() else injected_prompt
            self.emit(
                'dm_chunk',
                dm_chunk_payload(
                    chunk=injected_chunk,
                    session_id=turn.session_id,
                    turn_id=turn.turn_id,
                    requires_roll=turn.requires_roll,
                    rules_hint=rules_hint_payload,
                    context_version=CONTEXT_VERSION,
                    turn_number=rules_hint_payload.get('turn_number'),
                ),
                room=str(turn.session_id),
            )
            self.socketio.sleep(0)
            dm_response_text += injected_chunk
            telemetry_metric('socket.roll_prompt_injected_total', 1)

        response_emit_started = time.perf_counter()
        self.emit(
            'dm_response_end',
            dm_response_end_payload(
                session_id=turn.session_id,
                turn_id=turn.turn_id,
                requires_roll=turn.requires_roll,
                rules_hint=rules_hint_payload,
                context_version=CONTEXT_VERSION,
                ok=stream_error is None,
                error=stream_error[:500] if stream_error else None,
                turn_number=rules_hint_payload.get('turn_number'),
            ),
            room=str(turn.session_id),
        )
        self._record_phase_timing(
            'dm_response_emit',
            response_emit_started,
            campaign_id=campaign.campaign_id,
            session_id=turn.session_id,
        )
        self._emit_turn_status(turn.session_id, turn.turn_id, 'response_complete', {'ok': stream_error is None})
        # Yield so the dm_response_end event is flushed to clients immediately,
        # before the heavy post-turn processing (DB writes, canon extraction,
        # session projection) that can take 30-120+ seconds.
        self.socketio.sleep(0)
        return dm_response_text, stream_error

    def _background_canon_job(self, app, job_id: int):
        with app.app_context():
            process_canon_job(
                job_id,
                emit_turn_status=self._emit_turn_status,
                emit_segment_triggered=lambda session_id, payload: self.socketio.emit(
                    'segment_triggered',
                    payload,
                    room=str(session_id),
                ),
                record_phase_timing=self._record_phase_timing,
            )

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
        if self._is_admin_override(action_intent):
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
        turn: DmTurn,
        campaign: Campaign,
        command: TurnCommand,
        player_label: str,
        rules_hint_payload: dict,
        dm_response_text: str,
        stream_error: str | None,
        triggered_segments: list[dict],
        start_time: float,
    ) -> list[dict]:
        post_turn_segments: list[dict] = []
        try:
            db_save_started = time.perf_counter()
            dm_response_text = strip_reasoning_blocks(dm_response_text).strip()
            dm_succeeded = bool(dm_response_text) and stream_error is None
            turn_obj = db.session.get(DmTurn, turn.turn_id)
            if turn_obj:
                turn_obj.completed_at = utc_now()
                turn_obj.latency_ms = int((time.perf_counter() - start_time) * 1000)
                turn_obj.llm_provider = current_app.config.get('AIDM_LLM_PROVIDER')
                turn_obj.llm_model = current_app.config.get('AIDM_LLM_MODEL')
                set_logging_context(turn_id=turn.turn_id)

                if dm_response_text:
                    turn_obj.dm_output = dm_response_text
                    turn_obj.status = 'failed' if stream_error else 'completed'
                else:
                    turn_obj.status = 'failed' if stream_error else 'completed'
                    metadata_payload = safe_json_loads(turn_obj.metadata_json, {})
                    if stream_error:
                        metadata_payload['error'] = stream_error
                    turn_obj.metadata_json = safe_json_dumps(metadata_payload, {})

                roll_gate_payload = self._roll_gate_for_turn(turn_obj, campaign, dm_response_text)
                if roll_gate_payload:
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
                    )
                    immediate_state_summary = post_pipeline_result.get('legacyImmediateSummary') or {}
                    state_log = post_pipeline_result.get('stateLog') or {}
                    progress_result = update_campaign_pack_progress(
                        session_id=turn.session_id,
                        campaign_id=campaign.campaign_id,
                        triggered_segments=triggered_segments,
                    )
                    campaign_pack_progress_changed = bool(progress_result.changed)
                    commit_with_retry(label='post-DM state pipeline')
                except Exception as exc:
                    db.session.rollback()
                    logger.warning('State pipeline post-DM application failed: %s', str(exc))
                    telemetry_event(
                        'socket.state_pipeline.post_dm_failed',
                        payload={'session_id': turn.session_id, 'turn_id': turn.turn_id, 'error': str(exc)},
                        severity='warning',
                    )
                    try:
                        turn_obj = db.session.get(DmTurn, turn.turn_id)
                        if turn_obj:
                            immediate_state_summary = apply_immediate_state_changes(turn_obj, campaign, dm_response_text)
                            commit_with_retry(label='legacy immediate state fallback')
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
                    self.socketio.start_background_task(
                        self._background_canon_job,
                        app,
                        canon_job.job_id,
                    )

            commit_with_retry(label='post-turn final save')
            self._emit_turn_status(turn.session_id, turn.turn_id, 'saved', {'stage': 'post_turn'})

            if dm_response_text:
                telemetry_metric('socket.send_message.success_total', 1)
                telemetry_timing(
                    'socket.turn_latency_ms',
                    float((time.perf_counter() - start_time) * 1000),
                    tags={'campaign_id': campaign.campaign_id, 'session_id': turn.session_id},
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
            logger.error('Failed to persist DM response state: %s', str(exc))
            failed_turn = db.session.get(DmTurn, turn.turn_id)
            if failed_turn:
                metadata_payload = safe_json_loads(failed_turn.metadata_json, {})
                metadata_payload['post_turn_error'] = str(exc)
                metadata_payload['canon_status'] = 'failed'
                failed_turn.metadata_json = safe_json_dumps(metadata_payload, {})
                if failed_turn.dm_output:
                    failed_turn.status = 'completed'
                commit_with_retry(label='post-turn failure metadata')
            self._emit_turn_status(turn.session_id, turn.turn_id, 'failed', {'stage': 'post_turn', 'error': str(exc)})
            self.socketio.emit(
                'error',
                socket_error(
                    'turn_persist_failed',
                    'The DM response was generated but could not be saved. Please retry; continuity may be affected.',
                    {'session_id': turn.session_id, 'turn_id': turn.turn_id},
                ),
                room=str(turn.session_id),
            )
            telemetry_event(
                'socket.dm_persist_failed',
                payload={'session_id': turn.session_id, 'turn_id': turn.turn_id, 'error': str(exc)},
                severity='error',
            )
            return []
