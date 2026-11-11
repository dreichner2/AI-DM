from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Callable

from aidm_server.canon_inventory import append_drop_all_inventory_changes_from_text, inventory_change_from_intent_outcome
from aidm_server.canon_text import int_or_default
from aidm_server.combat.end_conditions import check_combat_end, combat_end_change
from aidm_server.combat.modifiers import MODIFIER_ENGINE_SOURCE
from aidm_server.combat.pipeline import (
    combat_turn_advance_change,
    engine_controlled_turn_is_up,
    engine_turn_advance_change,
    finalize_combat_prepare,
    prepare_combat_for_turn,
    prepare_combat_from_dm_response,
    record_combat_debug_from_outcome,
    record_combat_debug_from_prepare,
    sync_combat_encounter_record,
    turn_end_preview,
)
from aidm_server.combat.defeat import enemies_phrase, party_defeat_recovery, party_is_beaten, party_wake
from aidm_server.combat.state import (
    combat_summary_for_dm,
    combat_turn_context,
    consume_combat_turn_economy,
    ensure_combat_turn_economy,
    normalize_combat_state,
)
from aidm_server.combat.vitality import VITALITY_CONDITIONS, is_dead, is_stable
from aidm_server.database import db
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY, STATE_PIPELINE_VERSION
from aidm_server.game_state.application.applier import (
    apply_state_changes,
    legacy_immediate_summary_from_applied,
    persist_state_to_database,
)
from aidm_server.game_state.campaign_pack_encounters import catalog_record
from aidm_server.game_state.extraction.free_text_actions import FreeTextAction, free_text_action, name_words
from aidm_server.game_state.extraction.post_dm_outcome_extractor import extract_post_dm_outcomes
from aidm_server.game_state.extraction.pre_dm_action_extractor import extract_pre_dm_actions
from aidm_server.game_state.logging.state_log_builder import build_state_log, state_log_message
from aidm_server.game_state.orchestration.combat_resolution import (
    build_dm_combat_context,
    combat_participant_update_signature,
    derive_trusted_damage_changes,
    resolve_authoritative_player_attack,
    settle_engine_controlled_turns,
    without_trusted_damage_overlaps,
)
from aidm_server.game_state.orchestration.engine_brief import engine_brief
from aidm_server.game_state.orchestration.narration_facts import (
    PICKUP_SOURCE,
    TAKE_TIME_LINE,
    enemy_names,
    fight_start,
    next_actor_line,
    object_facts,
    plain_results,
)
from aidm_server.game_state.orchestration.story_beats import BEAT_BASIS_KEY, beat_basis
from aidm_server.game_state.models import (
    compact_state_for_extraction,
    display_actor_id,
    find_actor,
    normalize_item_name,
    player_character_from_model,
    recent_timeline_for_session,
    stable_change_id,
    state_snapshot_for_session,
)
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.models import Campaign, DmTurn, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.price_list import catalog_item, normalize_price
from aidm_server.telemetry import telemetry_metric
from aidm_server.turn_events import record_turn_event


STATE_UPDATE_EVENT = 'state_update'
MANAGED_STATE_DOMAINS = ['inventory', 'currency', 'health', 'xp', 'spells', 'scene', 'quests', 'locations', 'npcs', 'flags', 'combat']
SAFE_PRE_DM_IMMEDIATE_CHANGE_TYPES = {
    'inventory.mark_used',
    'inventory.equip',
    'inventory.unequip',
    'inventory.add',
    'inventory.remove',
    'currency.add',
    'scene.item.add',
    'scene.item.remove',
    'scene.move_location',
    'scene.interactable.action',
    'spell.cast',
    'class_feature.use',
    'rest.complete',
}
AUTHORITATIVE_PRE_NARRATION_CHANGE_TYPES = {
    'scene.interactable.action',
    'spell.cast',
    'class_feature.use',
    'item.use',
    'shop.buy',
    'shop.sell',
}
CONFIRMATION_DENIAL_PATTERN = re.compile(
    r"\b(?:do not|don't|does not|doesn't|did not|cannot|can't|fail|fails|failed|before you can|instead)\b",
    re.IGNORECASE,
)
INVENTORY_REMOVE_CONFIRMATION_PATTERN = re.compile(
    r'\b(?:drink|drinks|drank|consume|consumes|consumed|quaff|quaffs|quaffed|swallow|swallows|swallowed|'
    r'eat|eats|ate|use up|uses up|used up|drop|drops|dropped|give|gives|gave|hand over|hands over|'
    r'sell|sells|sold|remove|removes|removed)\b',
    re.IGNORECASE,
)
INVENTORY_TRANSFER_CONFIRMATION_PATTERN = re.compile(
    r'\b(?:give|gives|gave|hand|hands|handed|pass|passes|passed|offer|offers|offered)\b',
    re.IGNORECASE,
)
CURRENCY_TRANSFER_CONFIRMATION_PATTERN = re.compile(
    r'\b(?:give|gives|gave|pay|pays|paid|hand over|hands over|handed over)\b',
    re.IGNORECASE,
)
DOWNED_ACTOR_REFUSAL = 'You are down and cannot act.'
# How many of the hero's earlier rolled turns are searched for their previous attack roll.
PREVIOUS_ATTACK_LOOKBACK = 40


def _sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in re.split(r'(?<=[.!?])\s+|\n+', text or '') if sentence.strip()]


def _players_for_campaign(campaign: Campaign, fallback_player: Player) -> list[Player]:
    players = (
        Player.query.filter_by(workspace_id=campaign.workspace_id, campaign_id=campaign.campaign_id)
        .order_by(Player.player_id.asc())
        .all()
    )
    available = [
        player
        for player in players
        if player.workspace_id == campaign.workspace_id and player.campaign_id == campaign.campaign_id
    ]
    if not any(player.player_id == fallback_player.player_id for player in available):
        available.append(fallback_player)
    return available


def _pipeline_orm_token(
    *,
    turn: DmTurn,
    session_obj: Session,
    campaign: Campaign,
    player: Player,
    players: list[Player],
) -> dict[str, Any]:
    """Capture the rows whose state a helper response is allowed to mutate.

    The models do not currently expose SQLAlchemy version columns. This token
    therefore provides an optimistic version boundary from their mutable
    columns so a helper result cannot overwrite edits committed while its
    external provider call was in flight.
    """

    return {
        'turn_id': turn.turn_id,
        'session_id': session_obj.session_id,
        'campaign_id': campaign.campaign_id,
        'player_id': player.player_id,
        'turn': (
            turn.session_id,
            turn.campaign_id,
            turn.player_id,
            turn.status,
            turn.dm_output,
            turn.requires_roll,
            turn.roll_value,
            turn.rule_type,
            turn.outcome_status,
            turn.rules_hint,
            turn.metadata_json,
            turn.completed_at,
        ),
        'session': (
            session_obj.campaign_id,
            session_obj.status,
            session_obj.state_snapshot,
            session_obj.updated_at,
            session_obj.deleted_at,
        ),
        'campaign': (
            campaign.workspace_id,
            campaign.world_id,
            campaign.status,
            campaign.current_quest,
            campaign.plot_points,
            campaign.active_npcs,
            campaign.location,
            campaign.updated_at,
        ),
        'players': tuple(
            (
                player_obj.player_id,
                player_obj.workspace_id,
                player_obj.campaign_id,
                player_obj.level,
                player_obj.stats,
                player_obj.inventory,
                player_obj.character_sheet,
                player_obj.updated_at,
            )
            for player_obj in players
        ),
    }


def _reload_pipeline_orm(token: dict[str, Any]) -> tuple[DmTurn, Session, Campaign, Player, list[Player]]:
    turn = db.session.get(DmTurn, token['turn_id'])
    session_obj = db.session.get(Session, token['session_id'])
    campaign = db.session.get(Campaign, token['campaign_id'])
    player = db.session.get(Player, token['player_id'])
    if not all((turn, session_obj, campaign, player)):
        raise RuntimeError('Turn state changed while the helper provider was running.')

    players = _players_for_campaign(campaign, player)
    current = _pipeline_orm_token(
        turn=turn,
        session_obj=session_obj,
        campaign=campaign,
        player=player,
        players=players,
    )
    if current != token:
        raise RuntimeError('Turn state changed while the helper provider was running.')
    return turn, session_obj, campaign, player, players


def _metadata(turn: DmTurn) -> dict[str, Any]:
    payload = safe_json_loads(turn.metadata_json, {})
    return payload if isinstance(payload, dict) else {}


def _set_metadata(turn: DmTurn, payload: dict[str, Any]) -> None:
    turn.metadata_json = safe_json_dumps(payload, {})


def _recent_context_strings(recent_timeline: list[dict[str, Any]]) -> list[str]:
    values = []
    for entry in recent_timeline:
        if not isinstance(entry, dict):
            continue
        if entry.get('playerMessage'):
            values.append(str(entry.get('playerMessage')))
        if entry.get('dmResponse'):
            values.append(str(entry.get('dmResponse')))
    return values


def _safe_pre_dm_immediate_change(change: dict[str, Any]) -> bool:
    change_type = str(change.get('type') or '').strip()
    # Server-owned resolutions must be committed before the DM narrates them.
    # ``visible`` controls whether the applied result appears in the DM packet;
    # it must not demote a validated rules result into narration-confirmed state.
    if change_type in AUTHORITATIVE_PRE_NARRATION_CHANGE_TYPES:
        return True
    return change_type in SAFE_PRE_DM_IMMEDIATE_CHANGE_TYPES and not bool(change.get('visible', True))


def _spell_casting_time(state: dict[str, Any], change: dict[str, Any]) -> str:
    """The cast's ``castingTime``: the rules engine's resolution when it resolved the cast
    (Quickened Spell makes an action a bonus action), else the caster's spell definition
    (``action`` by default)."""

    resolution = change.get('resolution') if isinstance(change.get('resolution'), dict) else {}
    if resolution.get('castingTime') in {'action', 'bonus_action'}:
        return resolution['castingTime']
    actor = find_actor(state, change.get('actorId') or change.get('actor_id')) or {}
    spellbook = actor.get('spellbook') if isinstance(actor.get('spellbook'), dict) else {}
    wanted_id = str(change.get('spellId') or '').strip()
    wanted_name = normalize_item_name(change.get('spellName'))
    for spell in spellbook.get('knownSpells') or []:
        if not isinstance(spell, dict):
            continue
        if (wanted_id and str(spell.get('id') or '') == wanted_id) or (
            not wanted_id and wanted_name and normalize_item_name(spell.get('name')) == wanted_name
        ):
            casting_time = str(spell.get('castingTime') or '').strip().lower()
            return 'bonus_action' if casting_time == 'bonus_action' else 'action'
    return 'action'


def _gate_pre_dm_combat_spells(
    *,
    state: dict[str, Any],
    changes: list[dict[str, Any]],
    actor_id: str,
    turn_id: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reserve the persisted combat action (or bonus action, per the spell's
    ``castingTime``) for each accepted spell cast."""

    working_state = deepcopy(state)
    allowed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for change in changes:
        if str(change.get('type') or '').strip() != 'spell.cast':
            allowed.append(change)
            continue
        scene = working_state.get('currentScene') if isinstance(working_state.get('currentScene'), dict) else {}
        combat = normalize_combat_state(working_state.get('combat'), scene)
        if str(combat.get('status') or '').strip().lower() not in {'starting', 'active'}:
            allowed.append(change)
            continue
        change_actor_id = str(change.get('actorId') or change.get('actor_id') or '').strip()
        turn_context = combat_turn_context(combat)
        current_actor = turn_context.get('currentActor') if isinstance(turn_context.get('currentActor'), dict) else {}
        current_actor_id = str(current_actor.get('id') or '').strip()
        if combat.get('turnIndex') is None or not current_actor_id:
            rejected.append(
                {
                    'change': change,
                    'reason': 'Spell casting is unavailable until combat turn order is established.',
                }
            )
            continue
        if change_actor_id != actor_id or change_actor_id != current_actor_id:
            current_name = str(current_actor.get('name') or 'Another combatant').strip()
            rejected.append(
                {
                    'change': change,
                    'reason': f'{current_name} is acting now; this spell cannot be cast off-turn.',
                }
            )
            continue
        bonus_action = _spell_casting_time(working_state, change) == 'bonus_action'
        prior_economy = ensure_combat_turn_economy(combat, actor_id=change_actor_id)
        # Spiritual Weapon attacking again is not a new casting.
        levelled = (
            int_or_default(change.get('castLevel', change.get('cast_level')), default=0) > 0
            and change.get('repeatAttack') is not True
        )
        repeated = str(change.get('id') or '') in (prior_economy.get('spentActionIds') or [])
        remaining_key = 'bonusActionRemaining' if bonus_action else 'actionRemaining'
        if levelled and prior_economy.get('leveledSpellCast') is True and not repeated and prior_economy.get(remaining_key, 0) > 0:
            rejected.append({'change': change, 'reason': 'Only one levelled spell can be cast per turn.'})
            continue
        consumed, reason, economy = consume_combat_turn_economy(
            combat,
            actor_id=change_actor_id,
            action_id=change.get('id'),
            action_cost=0 if bonus_action else 1,
            bonus_action_cost=1 if bonus_action else 0,
        )
        if not consumed:
            rejected.append({'change': change, 'reason': reason or "This turn's action is already spent."})
            continue
        if levelled:
            economy['leveledSpellCast'] = True
            combat['flags']['turnEconomy'] = economy
        working_state['combat'] = combat
        allowed.extend(
            [
                change,
                {
                    'id': stable_change_id(
                        turn_id,
                        'combat.spell.action',
                        change_actor_id,
                        change.get('id'),
                    ),
                    'turnId': turn_id,
                    'type': 'combat.update',
                    'round': combat.get('round') or 1,
                    'turnIndex': combat.get('turnIndex'),
                    'flags': {'turnEconomy': deepcopy(economy)},
                    'reason': (
                        f"Casting {change.get('spellName') or 'a spell'} consumed the combat "
                        f"{'bonus action' if bonus_action else 'action'}."
                    ),
                    'visible': False,
                },
            ]
        )
    return allowed, rejected


def _gate_pre_dm_combat_object_actions(
    *,
    state: dict[str, Any],
    changes: list[dict[str, Any]],
    actor_id: str,
    turn_id: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """In a fight, an object action (one of the object's ``usableInCombat``) is taken
    on the hero's own turn and spends the action."""

    working_state = deepcopy(state)
    allowed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for change in changes:
        scene = working_state.get('currentScene') if isinstance(working_state.get('currentScene'), dict) else {}
        combat = normalize_combat_state(working_state.get('combat'), scene)
        if (
            str(change.get('type') or '').strip() != 'scene.interactable.action'
            or str(combat.get('status') or '').strip().lower() not in {'starting', 'active'}
        ):
            allowed.append(change)
            continue
        current_actor = combat_turn_context(combat).get('currentActor')
        current_actor = current_actor if isinstance(current_actor, dict) else {}
        if str(change.get('actorId') or '').strip() != actor_id or str(current_actor.get('id') or '') != actor_id:
            rejected.append({'change': change, 'reason': f"{str(current_actor.get('name') or 'Another combatant').strip()} is acting now."})
            continue
        consumed, reason, economy = consume_combat_turn_economy(
            combat,
            actor_id=actor_id,
            action_id=change.get('id'),
            action_cost=1,
        )
        if not consumed:
            rejected.append({'change': change, 'reason': reason or "This turn's action is already spent."})
            continue
        working_state['combat'] = combat
        allowed.extend(
            [
                change,
                {
                    'id': stable_change_id(turn_id, 'combat.object.action', actor_id, change.get('id')),
                    'turnId': turn_id,
                    'type': 'combat.update',
                    'round': combat.get('round') or 1,
                    'turnIndex': combat.get('turnIndex'),
                    'flags': {'turnEconomy': deepcopy(economy)},
                    'reason': 'Acting on an object consumed the combat action.',
                    'visible': False,
                },
            ]
        )
    return allowed, rejected


def _with_rejected_pre_dm_changes(
    pre_validation: dict[str, Any],
    rejected_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    if not rejected_changes:
        return pre_validation
    payload = deepcopy(pre_validation)
    rejected_by_type_actor: dict[tuple[str, str], str] = {}
    rejected_ids: set[str] = set()
    for rejection in rejected_changes:
        change = rejection.get('change') if isinstance(rejection.get('change'), dict) else {}
        change_id = str(change.get('id') or '').strip()
        if change_id:
            rejected_ids.add(change_id)
        rejected_by_type_actor[
            (
                str(change.get('type') or '').strip(),
                str(change.get('actorId') or change.get('actor_id') or '').strip(),
            )
        ] = str(rejection.get('reason') or "That can't be done right now.")
    payload['immediateChanges'] = [
        change
        for change in (payload.get('immediateChanges') or [])
        if isinstance(change, dict) and str(change.get('id') or '').strip() not in rejected_ids
    ]
    for result in payload.get('validatedActions') or []:
        if not isinstance(result, dict):
            continue
        original = result.get('originalAction') if isinstance(result.get('originalAction'), dict) else {}
        normalized = result.get('normalizedAction') if isinstance(result.get('normalizedAction'), dict) else {}
        key = (
            str(normalized.get('type') or original.get('type') or '').strip(),
            str(normalized.get('actorId') or original.get('actorId') or '').strip(),
        )
        if key in rejected_by_type_actor:
            result['status'] = 'invalid'
            result['reason'] = rejected_by_type_actor[key]
    return payload


def _merge_validation_results(*validations: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {'accepted': [], 'modified': [], 'rejected': []}
    for validation in validations:
        if not isinstance(validation, dict):
            continue
        for key in ('accepted', 'modified', 'rejected'):
            merged[key].extend([item for item in (validation.get(key) or []) if isinstance(item, dict)])
    return merged


def _item_reference_terms(item_name: Any) -> set[str]:
    normalized = normalize_item_name(item_name)
    terms = {normalized} if normalized else set()
    tokens = {token for token in normalized.split() if len(token) > 2}
    if tokens:
        terms.add(normalized.split()[-1])
        terms.update(token for token in tokens if token in {'potion', 'ration', 'food', 'elixir', 'vial', 'flask'})
    return {term for term in terms if term}


def _sentence_mentions_item(sentence: str, item_name: Any) -> bool:
    normalized_sentence = normalize_item_name(sentence)
    if not normalized_sentence:
        return False
    for term in _item_reference_terms(item_name):
        if ' ' in term and term in normalized_sentence:
            return True
        if re.search(rf'\b{re.escape(term)}\b', normalized_sentence):
            return True
    return False


def _dm_confirms_inventory_remove(change: dict[str, Any], dm_response_text: str) -> bool:
    item = change.get('item') if isinstance(change.get('item'), dict) else {}
    item_name = change.get('itemName') or change.get('item_name') or item.get('name')
    if not item_name:
        return False
    for sentence in _sentences(dm_response_text):
        if CONFIRMATION_DENIAL_PATTERN.search(sentence):
            continue
        if INVENTORY_REMOVE_CONFIRMATION_PATTERN.search(sentence) and _sentence_mentions_item(sentence, item_name):
            return True
    return False


def _dm_confirms_inventory_transfer(action: dict[str, Any], dm_response_text: str) -> bool:
    item_name = action.get('itemName') or action.get('item_name')
    target_name = action.get('toActorName') or action.get('to_actor_name')
    for sentence in _sentences(dm_response_text):
        if CONFIRMATION_DENIAL_PATTERN.search(sentence):
            continue
        if not INVENTORY_TRANSFER_CONFIRMATION_PATTERN.search(sentence):
            continue
        if item_name and not _sentence_mentions_item(sentence, item_name):
            continue
        if target_name and normalize_item_name(target_name) not in normalize_item_name(sentence):
            continue
        return True
    return False


def _dm_confirms_currency_transfer(action: dict[str, Any], dm_response_text: str) -> bool:
    amount = int_or_default(action.get('amount'), default=0)
    currency = str(action.get('currency') or '').strip().lower()
    target_name = action.get('toActorName') or action.get('to_actor_name')
    if amount <= 0 or not currency:
        return False
    for sentence in _sentences(dm_response_text):
        normalized = normalize_item_name(sentence)
        if CONFIRMATION_DENIAL_PATTERN.search(sentence):
            continue
        if not CURRENCY_TRANSFER_CONFIRMATION_PATTERN.search(sentence):
            continue
        if str(amount) not in normalized:
            continue
        if currency not in normalized and {
            'pp': 'platinum',
            'gp': 'gold',
            'ep': 'electrum',
            'sp': 'silver',
            'cp': 'copper',
        }.get(currency, currency) not in normalized:
            continue
        if target_name and normalize_item_name(target_name) not in normalized:
            continue
        return True
    return False


def _confirmed_pre_dm_changes(
    *,
    turn: DmTurn,
    pre_validation: dict[str, Any],
    pending_immediate_changes: list[dict[str, Any]],
    dm_response_text: str,
) -> list[dict[str, Any]]:
    confirmed: list[dict[str, Any]] = []
    for change in pending_immediate_changes:
        if not isinstance(change, dict):
            continue
        if str(change.get('type') or '') == 'inventory.remove' and _dm_confirms_inventory_remove(change, dm_response_text):
            next_change = deepcopy(change)
            next_change['source'] = 'post_dm_confirmed'
            next_change['reason'] = next_change.get('reason') or 'DM confirmed the pre-validated inventory removal.'
            confirmed.append(next_change)

    for result in pre_validation.get('validatedActions') or []:
        if not isinstance(result, dict) or result.get('status') not in {'valid', 'pending'}:
            continue
        original = result.get('originalAction') if isinstance(result.get('originalAction'), dict) else {}
        normalized = result.get('normalizedAction') if isinstance(result.get('normalizedAction'), dict) else {}
        action = {**original, **normalized}
        action_type = str(original.get('type') or normalized.get('type') or '').strip()
        action_id = str(original.get('id') or normalized.get('id') or result.get('actionId') or '').strip()
        actor_id = str(action.get('fromActorId') or action.get('actorId') or '').strip()
        if action.get('untrackedTarget') and not action.get('toActorId'):
            continue

        if action_type == 'inventory.transfer' and _dm_confirms_inventory_transfer(action, dm_response_text):
            confirmed.append(
                {
                    'id': stable_change_id(turn.turn_id, 'post_dm_confirmed', action_id, 'inventory.transfer'),
                    'turnId': turn.turn_id,
                    'type': 'inventory.transfer',
                    'source': 'post_dm_confirmed',
                    'actorId': actor_id,
                    'fromActorId': actor_id,
                    'toActorId': action.get('toActorId'),
                    'toActorName': action.get('toActorName'),
                    'itemId': action.get('itemId'),
                    'itemName': action.get('itemName'),
                    'quantity': max(1, int_or_default(action.get('quantity'), default=1)),
                    'reason': f"DM confirmed transfer of {action.get('itemName') or 'item'}.",
                    'visible': True,
                }
            )
        elif action_type == 'currency.transfer' and _dm_confirms_currency_transfer(action, dm_response_text):
            confirmed.append(
                {
                    'id': stable_change_id(turn.turn_id, 'post_dm_confirmed', action_id, 'currency.transfer'),
                    'turnId': turn.turn_id,
                    'type': 'currency.transfer',
                    'source': 'post_dm_confirmed',
                    'actorId': actor_id,
                    'fromActorId': actor_id,
                    'toActorId': action.get('toActorId'),
                    'toActorName': action.get('toActorName'),
                    'amount': max(1, int_or_default(action.get('amount'), default=1)),
                    'currency': str(action.get('currency') or '').lower(),
                    'reason': f"DM confirmed transfer of {action.get('amount')} {action.get('currency')}.",
                    'visible': True,
                }
            )
    return _merge_state_changes(confirmed)


def _turn_resolves_player_roll(turn: DmTurn) -> bool:
    if getattr(turn, 'roll_value', None) is not None:
        return True
    rules_hint = safe_json_loads(turn.rules_hint, {})
    if not isinstance(rules_hint, dict):
        return False
    return rules_hint.get('roll_value') is not None and not bool(rules_hint.get('outcome_deferred'))


def _turn_awaits_player_roll(turn: DmTurn) -> bool:
    return bool(turn.requires_roll and getattr(turn, 'roll_value', None) is None)


def _turn_level_pending_roll(turn: DmTurn, *, actor_id: str) -> dict[str, Any]:
    rules_hint = safe_json_loads(turn.rules_hint, {})
    rules_hint = rules_hint if isinstance(rules_hint, dict) else {}
    roll_type = str(turn.rule_type or rules_hint.get('roll_type') or 'check').strip() or 'check'
    return {
        'type': f'{roll_type}_roll',
        'actorId': actor_id,
        'source': 'turn_rules',
        'dcHint': rules_hint.get('dc_hint'),
        'reason': rules_hint.get('reason') or 'Player roll required to resolve the current action.',
    }


def _resolved_player_roll_should_defer_enemy(turn: DmTurn) -> bool:
    if not _turn_resolves_player_roll(turn):
        return False
    rules_hint = safe_json_loads(turn.rules_hint, {})
    rules_hint = rules_hint if isinstance(rules_hint, dict) else {}
    roll_type = str(turn.rule_type or rules_hint.get('roll_type') or '').strip().lower()
    if roll_type == 'attack':
        return False
    return True


def _turn_combat_action(turn: DmTurn) -> dict[str, Any] | None:
    metadata = _metadata(turn)
    action_intent = metadata.get('action_intent') if isinstance(metadata.get('action_intent'), dict) else None
    if not isinstance(action_intent, dict):
        return None
    combat_action = action_intent.get('combat')
    if not isinstance(combat_action, dict) or combat_action.get('authoritative') is not True:
        return None
    return combat_action


def _opens_with_enemy_turns(state: dict[str, Any], applied_changes: list[dict[str, Any]]) -> bool:
    """A fight started this turn, however it started, whose first turns belong to
    participants the engine plays (enemies, or a non-player ally).

    They act in this same turn, up to the first player character, so no player
    is left waiting on "X is acting now".
    """

    if not any(isinstance(change, dict) and change.get('type') == 'combat.start' for change in applied_changes):
        return False
    return engine_controlled_turn_is_up(state.get('combat') if isinstance(state.get('combat'), dict) else {})


def _settle_engine_turns(
    *,
    turn: DmTurn,
    state: dict[str, Any],
    already_applied: list[dict[str, Any]],
    applied_post: list[dict[str, Any]],
    post_validation: dict[str, Any],
    post_extraction: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Turn processing never ends with initiative on a participant the engine plays.

    A fight narration started, or one left waiting on an enemy or a non-player
    ally, has those turns resolved now as trusted changes, until a player
    character can act or the combat ends.
    """

    settled = settle_engine_controlled_turns(
        state,
        turn_id=turn.turn_id,
        already_applied_changes=[*already_applied, *applied_post],
    )
    if not settled.changes:
        return state, applied_post, post_validation, post_extraction
    post_extraction = deepcopy(post_extraction)
    post_extraction['proposedChanges'] = [*(post_extraction.get('proposedChanges') or []), *settled.changes]
    notes = list(post_extraction.get('notes') or [])
    if 'engine_controlled_turns_resolved' not in notes:
        notes.append('engine_controlled_turns_resolved')
    post_extraction['notes'] = notes
    return (
        settled.state,
        [*applied_post, *settled.applied_changes],
        _merge_validation_results(post_validation, settled.validation),
        post_extraction,
    )


def _combat_enemy_deferred_reason(turn: DmTurn) -> str | None:
    combat_action = _turn_combat_action(turn)
    if not isinstance(combat_action, dict):
        return 'player_turn_in_progress'
    economy = combat_action.get('economy') if isinstance(combat_action.get('economy'), dict) else {}
    return None if economy.get('endsTurn') is True else 'player_turn_in_progress'


def _player_roll_consume_change(turn: DmTurn, state: dict[str, Any]) -> dict[str, Any] | None:
    """The trusted change spending the combat effects a server-rolled player roll used
    (Guidance on a check, a Bardic Inspiration die)."""

    roll = _metadata(turn).get('authoritative_roll')
    entries = [
        {key: str(entry[key]) for key in ('participantId', 'effectId') if entry.get(key)}
        for entry in (roll.get('consume') if isinstance(roll, dict) and isinstance(roll.get('consume'), list) else [])
        if isinstance(entry, dict) and entry.get('participantId') and entry.get('effectId')
    ]
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    if not entries or str(combat.get('status') or '').strip().lower() not in {'starting', 'active'}:
        return None
    return {
        'id': stable_change_id(turn.turn_id, 'player_roll_effects_consumed'),
        'turnId': turn.turn_id,
        'type': 'combat.effect.consume',
        'consume': entries,
        'source': MODIFIER_ENGINE_SOURCE,
        'reason': 'Effects spent by the server-rolled player roll.',
        'visible': False,
    }


def _resolved_player_attack_for_turn(
    *,
    turn: DmTurn,
    state: dict[str, Any],
    actor_id: str,
) -> dict[str, Any] | None:
    metadata = _metadata(turn)
    action_intent = metadata.get('action_intent') if isinstance(metadata.get('action_intent'), dict) else None
    authoritative_roll = (
        metadata.get('authoritative_roll')
        if isinstance(metadata.get('authoritative_roll'), dict)
        else None
    )
    if authoritative_roll is None:
        rules_hint = safe_json_loads(turn.rules_hint, {})
        if isinstance(rules_hint, dict) and isinstance(rules_hint.get('authoritative_roll'), dict):
            authoritative_roll = rules_hint['authoritative_roll']
    return resolve_authoritative_player_attack(
        state=state,
        actor_id=actor_id,
        turn_id=turn.turn_id,
        action_intent=action_intent,
        authoritative_roll=authoritative_roll,
    )


def _state_change_signature(change: dict[str, Any]) -> tuple[Any, ...] | None:
    change_type = str(change.get('type') or '').strip()
    actor_id = str(change.get('actorId') or change.get('actor_id') or '')
    if change_type in {'inventory.add', 'inventory.remove', 'inventory.equip', 'inventory.unequip'}:
        item = change.get('item') if isinstance(change.get('item'), dict) else {}
        item_id = change.get('itemId') or change.get('item_id') or item.get('id') or item.get('itemId')
        item_name = change.get('itemName') or change.get('item_name') or item.get('name')
        return (
            change_type,
            actor_id,
            str(item_id or ''),
            normalize_item_name(item_name),
            int_or_default(change.get('quantity', item.get('quantity')), default=1),
            normalize_item_name(change.get('slot')),
        )
    if change_type == 'inventory.transfer':
        item = change.get('item') if isinstance(change.get('item'), dict) else {}
        item_id = change.get('itemId') or change.get('item_id') or item.get('id') or item.get('itemId')
        item_name = change.get('itemName') or change.get('item_name')
        to_actor = str(change.get('toActorId') or change.get('to_actor_id') or change.get('toActorName') or change.get('to_actor_name') or '')
        return (
            change_type,
            actor_id,
            to_actor.lower(),
            str(item_id or ''),
            normalize_item_name(item_name),
            int_or_default(change.get('quantity'), default=1),
        )
    if change_type in {'currency.add', 'currency.remove'}:
        return (change_type, actor_id, str(change.get('currency') or '').lower(), int_or_default(change.get('amount'), default=0))
    if change_type == 'currency.transfer':
        to_actor = str(change.get('toActorId') or change.get('to_actor_id') or change.get('toActorName') or change.get('to_actor_name') or '')
        return (
            change_type,
            actor_id,
            to_actor.lower(),
            str(change.get('currency') or '').lower(),
            int_or_default(change.get('amount'), default=0),
        )
    if change_type in {'health.heal', 'health.damage'}:
        return (change_type, actor_id, int_or_default(change.get('amount'), default=0))
    if change_type == 'health.max.set':
        return (change_type, actor_id, int_or_default(change.get('maxHp', change.get('amount')), default=0))
    if change_type in {'xp.add', 'xp.remove'}:
        return (change_type, actor_id, int_or_default(change.get('amount'), default=0))
    if change_type == 'spell.learn':
        spell = change.get('spell') if isinstance(change.get('spell'), dict) else {}
        return (change_type, actor_id, normalize_item_name(change.get('spellName') or spell.get('name')))
    if change_type == 'spell.cast':
        return (
            change_type,
            actor_id,
            normalize_item_name(change.get('spellId') or change.get('spellName')),
            int_or_default(change.get('castLevel'), default=0),
            normalize_item_name(change.get('resourcePool')),
        )
    if change_type == 'rest.complete':
        return (change_type, actor_id, normalize_item_name(change.get('restType')))
    if change_type in {'scene.item.add', 'scene.item.remove'}:
        item = change.get('item') if isinstance(change.get('item'), dict) else {}
        return (
            change_type,
            normalize_item_name(change.get('locationId')),
            str(change.get('itemId') or item.get('id') or '').strip(),
            normalize_item_name(change.get('itemName') or item.get('name')),
            int_or_default(change.get('quantity', item.get('quantity')), default=1),
        )
    if change_type in {'scene.update', 'scene.move_location'}:
        return (
            change_type,
            normalize_item_name(change.get('locationId') or change.get('name')),
            normalize_item_name(change.get('sceneType') or change.get('mood') or change.get('combatState')),
        )
    if change_type == 'combat.end':
        return (
            change_type,
            normalize_item_name(change.get('status') or 'ended'),
            normalize_item_name(change.get('endReason') or change.get('end_reason')),
        )
    if change_type == 'combat.participant.update':
        return (
            change_type,
            normalize_item_name(change.get('participantId') or change.get('enemyId')),
            combat_participant_update_signature(change),
        )
    if change_type == 'combat.round.advance':
        return (change_type, int_or_default(change.get('round'), default=0))
    if change_type.startswith('location.'):
        return (
            change_type,
            normalize_item_name(change.get('locationId') or change.get('name')),
            normalize_item_name(change.get('connectedLocationId') or change.get('connectedLocationName')),
        )
    if change_type.startswith('quest.'):
        return (
            change_type,
            normalize_item_name(change.get('questId') or change.get('title') or change.get('name')),
            normalize_item_name(change.get('objectiveId') or change.get('stage')),
        )
    if change_type.startswith('npc.'):
        return (
            change_type,
            normalize_item_name(change.get('npcId') or change.get('name')),
            normalize_item_name(change.get('locationId') or change.get('disposition') or change.get('status')),
        )
    if change_type.startswith('flag.'):
        return (change_type, normalize_item_name(change.get('flagKey')))
    if change_type.startswith('combat.'):
        return (
            change_type,
            normalize_item_name(change.get('participantId') or change.get('enemyId') or change.get('combatId')),
            normalize_item_name(change.get('intentType') or change.get('status') or change.get('round')),
        )
    return None


def _inventory_change_quantity(change: dict[str, Any]) -> int:
    item = change.get('item') if isinstance(change.get('item'), dict) else {}
    return int_or_default(change.get('quantity', item.get('quantity')), default=1)


def _inventory_changes_semantically_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_type = str(left.get('type') or '').strip()
    right_type = str(right.get('type') or '').strip()
    inventory_types = {
        'inventory.add',
        'inventory.remove',
        'inventory.equip',
        'inventory.unequip',
        'inventory.transfer',
    }
    if left_type != right_type or left_type not in inventory_types:
        return False
    if _change_actor_id(left) != _change_actor_id(right):
        return False
    if _inventory_change_quantity(left) != _inventory_change_quantity(right):
        return False
    if normalize_item_name(left.get('slot')) != normalize_item_name(right.get('slot')):
        return False
    if left_type == 'inventory.transfer' and (
        _transfer_target_actor_id(left) != _transfer_target_actor_id(right)
    ):
        return False
    left_item = left.get('item') if isinstance(left.get('item'), dict) else {}
    right_item = right.get('item') if isinstance(right.get('item'), dict) else {}
    left_id = str(left.get('itemId') or left.get('item_id') or left_item.get('id') or '').strip()
    right_id = str(right.get('itemId') or right.get('item_id') or right_item.get('id') or '').strip()
    if bool(left_id) != bool(right_id):
        left_name = normalize_item_name(left.get('itemName') or left.get('item_name') or left_item.get('name'))
        right_name = normalize_item_name(right.get('itemName') or right.get('item_name') or right_item.get('name'))
        if not left_name or left_name != right_name:
            return False
        correlation_id = str(left.get('_semanticCorrelationId') or '').strip()
        return bool(correlation_id and correlation_id == str(right.get('_semanticCorrelationId') or '').strip())
    return _item_reference_matches(left, right)


def _correlate_inventory_intent_changes(
    proposed_changes: list[dict[str, Any]],
    intent_changes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    proposed = [deepcopy(change) for change in proposed_changes if isinstance(change, dict)]
    intents = [deepcopy(change) for change in intent_changes if isinstance(change, dict)]
    for intent_index, intent in enumerate(intents):
        intent_type = str(intent.get('type') or '').strip()
        if intent_type not in {'inventory.add', 'inventory.remove', 'inventory.equip', 'inventory.unequip'}:
            continue
        candidates = [
            index
            for index, proposed_change in enumerate(proposed)
            if _inventory_changes_semantically_match(
                {**intent, '_semanticCorrelationId': 'candidate'},
                {**proposed_change, '_semanticCorrelationId': 'candidate'},
            )
        ]
        if len(candidates) != 1:
            continue
        proposed_index = candidates[0]
        intent_item = intent.get('item') if isinstance(intent.get('item'), dict) else {}
        correlation_id = stable_change_id(
            intent.get('turnId'),
            'confirmed_inventory_action',
            intent_type,
            _change_actor_id(intent),
            normalize_item_name(intent.get('itemName') or intent_item.get('name')),
            _inventory_change_quantity(intent),
        )
        intents[intent_index]['_semanticCorrelationId'] = correlation_id
        proposed[proposed_index]['_semanticCorrelationId'] = correlation_id
    return proposed, intents


def _merge_state_changes(
    *change_lists: list[dict[str, Any]],
    seed_changes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    semantically_seen: list[dict[str, Any]] = []
    for change in seed_changes or []:
        if isinstance(change, dict):
            semantically_seen.append(change)
            signature = _state_change_signature(change)
            if signature:
                seen.add(signature)
    for changes in change_lists:
        for change in changes or []:
            if not isinstance(change, dict):
                continue
            signature = _state_change_signature(change)
            if signature and signature in seen:
                continue
            if any(
                _inventory_changes_semantically_match(change, existing)
                for existing in semantically_seen
            ):
                continue
            if signature:
                seen.add(signature)
            merged.append({key: value for key, value in change.items() if key != '_semanticCorrelationId'})
            semantically_seen.append(change)
    return merged


def _change_actor_id(change: dict[str, Any]) -> str:
    return str(change.get('actorId') or change.get('actor_id') or '').strip()


def _transfer_source_actor_id(change: dict[str, Any]) -> str:
    return str(change.get('fromActorId') or change.get('from_actor_id') or change.get('actorId') or change.get('actor_id') or '').strip()


def _transfer_target_actor_id(change: dict[str, Any]) -> str:
    return str(change.get('toActorId') or change.get('to_actor_id') or '').strip()


def _item_reference_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_item = left.get('item') if isinstance(left.get('item'), dict) else {}
    right_item = right.get('item') if isinstance(right.get('item'), dict) else {}
    left_id = str(left.get('itemId') or left.get('item_id') or left_item.get('id') or left_item.get('itemId') or '').strip()
    right_id = str(right.get('itemId') or right.get('item_id') or right_item.get('id') or right_item.get('itemId') or '').strip()
    if left_id and right_id:
        return left_id == right_id
    if left_id or right_id:
        return False
    left_name = normalize_item_name(left.get('itemName') or left.get('item_name') or left_item.get('name'))
    right_name = normalize_item_name(right.get('itemName') or right.get('item_name') or right_item.get('name'))
    return bool(left_name and right_name and left_name == right_name)


def _confirmed_item_reference_matches(change: dict[str, Any], confirmed: dict[str, Any]) -> bool:
    change_item = change.get('item') if isinstance(change.get('item'), dict) else {}
    confirmed_item = confirmed.get('item') if isinstance(confirmed.get('item'), dict) else {}
    change_id = str(change.get('itemId') or change.get('item_id') or change_item.get('id') or '').strip()
    confirmed_id = str(confirmed.get('itemId') or confirmed.get('item_id') or confirmed_item.get('id') or '').strip()
    if change_id and confirmed_id:
        return change_id == confirmed_id
    change_name = normalize_item_name(change.get('itemName') or change.get('item_name') or change_item.get('name'))
    confirmed_name = normalize_item_name(
        confirmed.get('itemName') or confirmed.get('item_name') or confirmed_item.get('name')
    )
    return bool(change_name and change_name == confirmed_name)


def _same_positive_amount(left: dict[str, Any], right: dict[str, Any], key: str) -> bool:
    return int_or_default(left.get(key), default=0) > 0 and int_or_default(left.get(key), default=0) == int_or_default(right.get(key), default=0)


def _change_overlaps_confirmed_transfer(change: dict[str, Any], confirmed_transfers: list[dict[str, Any]]) -> bool:
    change_type = str(change.get('type') or '').strip()
    actor_id = _change_actor_id(change)
    for transfer in confirmed_transfers:
        transfer_type = str(transfer.get('type') or '').strip()
        if transfer_type == 'inventory.transfer':
            if change_type == 'inventory.remove' and actor_id == _transfer_source_actor_id(transfer):
                if _same_positive_amount(change, transfer, 'quantity') and _confirmed_item_reference_matches(change, transfer):
                    return True
            if change_type == 'inventory.add' and actor_id == _transfer_target_actor_id(transfer):
                if _same_positive_amount(change, transfer, 'quantity') and _confirmed_item_reference_matches(change, transfer):
                    return True
        elif transfer_type == 'currency.transfer':
            currency = str(change.get('currency') or '').strip().lower()
            transfer_currency = str(transfer.get('currency') or '').strip().lower()
            if currency != transfer_currency:
                continue
            if change_type == 'currency.remove' and actor_id == _transfer_source_actor_id(transfer):
                if _same_positive_amount(change, transfer, 'amount'):
                    return True
            if change_type == 'currency.add' and actor_id == _transfer_target_actor_id(transfer):
                if _same_positive_amount(change, transfer, 'amount'):
                    return True
    return False


def _without_confirmed_transfer_overlaps(
    changes: list[dict[str, Any]],
    confirmed_transfers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not confirmed_transfers:
        return changes
    return [
        change
        for change in changes or []
        if isinstance(change, dict) and not _change_overlaps_confirmed_transfer(change, confirmed_transfers)
    ]


def _without_confirmed_inventory_overlaps(
    changes: list[dict[str, Any]],
    confirmed_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    confirmed_inventory = [
        change
        for change in confirmed_changes
        if isinstance(change, dict)
        and str(change.get('type') or '').strip()
        in {'inventory.add', 'inventory.remove', 'inventory.equip', 'inventory.unequip'}
    ]
    if not confirmed_inventory:
        return changes
    filtered: list[dict[str, Any]] = []
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        overlaps = any(
            str(change.get('type') or '').strip() == str(confirmed.get('type') or '').strip()
            and _change_actor_id(change) == _change_actor_id(confirmed)
            and _inventory_change_quantity(change) == _inventory_change_quantity(confirmed)
            and _confirmed_item_reference_matches(change, confirmed)
            for confirmed in confirmed_inventory
        )
        if not overlaps:
            filtered.append(change)
    return filtered


def _names_revealed_item(change: dict[str, Any], revealed: dict[str, Any]) -> bool:
    item = change.get('item') if isinstance(change.get('item'), dict) else {}
    change_id = str(change.get('itemId') or item.get('id') or '').strip()
    if change_id and change_id == str(revealed.get('id') or '').strip():
        return True
    return bool(normalize_item_name(_narrated_item_name(change)) == normalize_item_name(revealed.get('name')) != '')


def _narrated_item_name(change: dict[str, Any]) -> str:
    item = change.get('item') if isinstance(change.get('item'), dict) else {}
    return str(change.get('itemName') or item.get('name') or '').strip()


def _without_applied_immediate_overlaps(
    changes: list[dict[str, Any]],
    applied_immediate: list[dict[str, Any]],
    *,
    action_intent: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Keep narration from re-applying or contradicting pre-DM authoritative work."""

    applied = [change for change in applied_immediate if isinstance(change, dict)]
    applied_items = [change for change in applied if change.get('type') == 'item.use']
    applied_adds = [change for change in applied if change.get('type') == 'inventory.add']
    applied_trades = [change for change in applied if change.get('type') in {'shop.buy', 'shop.sell'}]
    intent_kind = str((action_intent or {}).get('kind') or '').strip().lower()
    inventory_action = str((action_intent or {}).get('inventory_action') or '').strip().lower()
    raw_intent_item = (
        action_intent.get('item')
        if isinstance(action_intent, dict) and isinstance(action_intent.get('item'), dict)
        else {}
    )
    intent_item = {
        'itemId': raw_intent_item.get('id') or raw_intent_item.get('itemId') or raw_intent_item.get('item_id'),
        'itemName': raw_intent_item.get('name'),
        'item': raw_intent_item,
    }
    authoritative_move = any(str(change.get('type') or '').strip() == 'scene.move_location' for change in applied)
    applied_actor_actions = {
        (str(change.get('type') or '').strip(), _change_actor_id(change))
        for change in applied
        if str(change.get('type') or '').strip() in {'spell.cast', 'class_feature.use', 'rest.complete'}
    }
    applied_scene_item_signatures = {
        signature
        for change in applied
        if str(change.get('type') or '').strip() in {'scene.item.add', 'scene.item.remove'}
        for signature in [_state_change_signature(change)]
        if signature
    }
    grounded_scene_removals = [
        candidate
        for candidate in changes or []
        if isinstance(candidate, dict) and str(candidate.get('type') or '').strip() == 'scene.item.remove'
    ]
    # Contents an object action revealed this turn stay in the scene until a
    # typed pickup takes them: narrating the reveal neither carries an item
    # off nor pockets a pile of coins.
    revealed_items = [
        item
        for change in applied
        if change.get('type') == 'scene.interactable.action' and isinstance(change.get('event'), dict)
        for item in (change['event'].get('revealedItems') or [])
        if isinstance(item, dict)
    ]
    revealed_coins = {
        (code, amount) for item in revealed_items for code, amount in normalize_price(item.get('currency')).items()
    }
    filtered: list[dict[str, Any]] = []
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        change_type = str(change.get('type') or '').strip()
        # These typed actions are adjudicated before narration. This applies
        # whether validation succeeded (the change is already present) or
        # failed (narration cannot create a bypass to success).
        if intent_kind == 'travel' and change_type == 'scene.move_location':
            continue
        if intent_kind == 'spell' and change_type == 'spell.cast':
            continue
        if intent_kind == 'capability' and change_type == 'class_feature.use':
            continue
        if intent_kind == 'object' and change_type == 'scene.interactable.action':
            continue
        if intent_kind == 'rest' and change_type == 'rest.complete':
            continue
        if change_type == 'inventory.remove' and any(
            _change_actor_id(change) == _change_actor_id(item)
            and _confirmed_item_reference_matches(change, item)
            for item in applied_items
        ):
            continue
        if change_type in {'health.heal', 'health.damage'} and any(
            _change_actor_id(change) == str(item.get('targetId') or _change_actor_id(item)).strip()
            and isinstance(item.get('resolution'), dict)
            and ('healing' if change_type == 'health.heal' else 'damage') in item['resolution']
            for item in applied_items
        ):
            continue
        if any(
            _change_actor_id(change) == _change_actor_id(trade)
            and (
                (change_type in {'currency.add', 'currency.remove'})
                or (
                    change_type == ('inventory.add' if trade.get('type') == 'shop.buy' else 'inventory.remove')
                    and _confirmed_item_reference_matches(change, trade)
                )
            )
            for trade in applied_trades
        ):
            continue
        if change_type in {'inventory.add', 'scene.item.remove'} and any(
            _names_revealed_item(change, item) for item in revealed_items
        ):
            continue
        if change_type == 'inventory.add' and any(
            _change_actor_id(change) == _change_actor_id(added) and _confirmed_item_reference_matches(change, added)
            for added in applied_adds
        ):
            continue
        if change_type == 'currency.add' and (
            str(change.get('currency') or '').strip().lower(),
            int_or_default(change.get('amount'), default=0),
        ) in revealed_coins:
            continue
        # A free-form DM response is not an item source. Inventory gains come
        # from a validated typed transaction (pickup, purchase, reward, object
        # contents); narration may only confirm the typed item request or name
        # an item it lifts from the scene or one the item catalog knows.
        # Otherwise "the quest takes a step forward" mints a "step forward".
        if (
            change_type == 'inventory.add'
            and str(change.get('source') or '') == 'post_dm'
            and not (intent_kind == 'item' and _item_reference_matches(change, intent_item))
            and not any(_item_reference_matches(change, removal) for removal in grounded_scene_removals)
            and catalog_item(_narrated_item_name(change)) is None
        ):
            continue
        if intent_kind == 'item' and inventory_action == 'pick_up':
            if change_type in {'inventory.add', 'scene.item.remove'} and _item_reference_matches(change, intent_item):
                continue
        if intent_kind == 'item' and inventory_action == 'drop':
            if change_type in {'inventory.remove', 'scene.item.add'} and _item_reference_matches(change, intent_item):
                continue
        if authoritative_move and change_type == 'scene.move_location':
            continue
        if change_type in {'spell.cast', 'class_feature.use', 'rest.complete'}:
            change_actor_id = _change_actor_id(change)
            if any(
                applied_type == change_type
                and (not change_actor_id or change_actor_id == applied_actor_id)
                for applied_type, applied_actor_id in applied_actor_actions
            ):
                continue
        signature = _state_change_signature(change)
        if signature and signature in applied_scene_item_signatures:
            continue
        filtered.append(change)
    return filtered


def _intent_confirmed_post_changes(
    *,
    turn: DmTurn,
    dm_response_text: str,
    actor_id: str,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    inventory_change = inventory_change_from_intent_outcome(turn, dm_response_text)
    if inventory_change:
        action = str(inventory_change.get('action') or '')
        change_type = 'inventory.add' if action == 'acquire' else 'inventory.remove' if action == 'lose' else ''
        item_name = str(inventory_change.get('item_name') or '').strip()
        item_id = str(inventory_change.get('item_id') or '').strip()
        quantity = max(1, int_or_default(inventory_change.get('quantity'), default=1))
        if change_type and item_name:
            change: dict[str, Any] = {
                'id': stable_change_id(turn.turn_id, 'post_dm_intent', change_type, actor_id, item_id or item_name, quantity),
                'turnId': turn.turn_id,
                'type': change_type,
                'source': 'post_dm',
                'actorId': actor_id,
                'itemName': item_name,
                **({'itemId': item_id} if item_id else {}),
                'quantity': quantity,
                'reason': f"DM confirmed requested inventory action for {item_name}.",
                'visible': True,
            }
            if change_type == 'inventory.add':
                change['item'] = {
                    **({'id': item_id} if item_id else {}),
                    'name': item_name,
                    'quantity': quantity,
                    'type': 'misc',
                }
            changes.append(change)

    drop_all_patch = {'inventory_changes': [], 'entities': []}
    append_drop_all_inventory_changes_from_text(turn, dm_response_text, drop_all_patch)
    for change in drop_all_patch.get('inventory_changes') or []:
        if not isinstance(change, dict) or change.get('action') != 'lose':
            continue
        item_name = str(change.get('item_name') or '').strip()
        quantity = max(1, int_or_default(change.get('quantity'), default=1))
        if not item_name:
            continue
        changes.append(
            {
                'id': stable_change_id(turn.turn_id, 'post_dm_drop_all', 'inventory.remove', actor_id, item_name, quantity),
                'turnId': turn.turn_id,
                'type': 'inventory.remove',
                'source': 'post_dm',
                'actorId': actor_id,
                'itemName': item_name,
                'quantity': quantity,
                'reason': f"DM confirmed dropping {item_name}.",
                'visible': True,
            }
        )
    return _merge_state_changes(changes)


def _dm_context_packet(
    *,
    state: dict[str, Any],
    player_message: str,
    pre_validation: dict[str, Any],
    applied_changes: list[dict[str, Any]],
    combat_context: dict[str, Any] | None = None,
    resolved_player_roll: bool = False,
    player_resolved_action: dict[str, Any] | None = None,
    enemy_deferred_reason: str | None = None,
    opening_enemy_turns: bool = False,
    enemy_roller: Callable[[int], int] | None = None,
    turn_id: int | None = None,
) -> dict[str, Any]:
    compact = compact_state_for_extraction(state)
    raw_pending_rolls = pre_validation.get('pendingRolls')
    pending_rolls = [roll for roll in raw_pending_rolls if isinstance(roll, dict)] if isinstance(raw_pending_rolls, list) else []
    dm_combat = build_dm_combat_context(
        state=state,
        combat_context=combat_context,
        pending_rolls=pending_rolls,
        resolved_player_roll=resolved_player_roll or bool(enemy_deferred_reason),
        enemy_roller=enemy_roller,
        turn_id=turn_id,
    )
    if isinstance(dm_combat, dict) and enemy_deferred_reason:
        dm_combat['enemyActionDeferredReason'] = enemy_deferred_reason
    if isinstance(dm_combat, dict) and isinstance(player_resolved_action, dict):
        dm_combat['playerResolvedAction'] = deepcopy(player_resolved_action)
        dm_combat['trustedDamageEvents'] = [deepcopy(player_resolved_action)]
    validated_actions = []
    valid_actions = []
    invalid_actions = []
    pending_actions = []
    needs_clarification = []
    for result in pre_validation.get('validatedActions') or []:
        if not isinstance(result, dict):
            continue
        original = result.get('originalAction') if isinstance(result.get('originalAction'), dict) else {}
        normalized = result.get('normalizedAction') if isinstance(result.get('normalizedAction'), dict) else {}
        resolution = normalized.get('resolution') if isinstance(normalized.get('resolution'), dict) else None
        action_label = normalized.get('summary') or original.get('summary') or original.get('sourceText')
        reason = result.get('reason') or result.get('status')
        summary = action_label if action_label else reason
        if action_label and reason and reason != action_label:
            summary = f"{action_label} ({reason})"
        action_entry = {
            'status': result.get('status'),
            'summary': summary,
            'type': original.get('type'),
            'resolvedItem': (
                {
                    'itemId': resolution.get('itemId'),
                    'itemName': resolution.get('itemName'),
                    'resolutionMethod': resolution.get('resolutionMethod'),
                }
                if resolution and resolution.get('status') == 'resolved'
                else None
            ),
            'reason': result.get('reason'),
        }
        validated_actions.append(action_entry)
        if result.get('status') == 'valid':
            valid_actions.append(action_entry)
        elif result.get('status') == 'pending':
            pending_actions.append(action_entry)
        elif result.get('status') == 'invalid':
            invalid_actions.append(action_entry)
        elif result.get('status') == 'needs_clarification':
            needs_clarification.append(
                {
                    **action_entry,
                    'clarificationRequest': result.get('clarificationRequest'),
                }
            )
    instructions = [
        'Narrate valid actions as possible.',
        'Anchor narration to the latest playerMessage and validatedActions.',
        'Do not substitute a different known object for the object named or described in the latest playerMessage.',
        'Do not narrate invalid actions as successful.',
        'If an action is invalid, explain it naturally in-world.',
        'Do not output JSON.',
        'Do not claim state changes that contradict validatedActions.',
    ]
    if combat_context:
        instructions.extend(
            [
                'Enemy rolls are engine-owned. Never ask the player to roll enemy attacks, enemy saving throws, enemy checks, or enemy damage.',
                'If combatState.enemyResolvedActions is present, narrate those exact enemy results, including attack totals, hit or miss, damage totals, and targets.',
                'If combatState.enemyResolvedActions is absent, do not invent enemy roll results and do not request enemy rolls from the player.',
                'Only ask the player for rolls listed in pendingRolls.',
                'Do not make fleeing, surrendering, or negotiating enemies fight to the death unless blocked or forced.',
                'Use enemy morale, survival instincts, and objectives when describing choices.',
                'Do not directly mutate game state; narrate concrete outcomes clearly for extraction and validation.',
            ]
        )
    if opening_enemy_turns:
        instructions.extend(
            [
                'Combat has just begun and those ahead of the player characters in initiative acted first: '
                'narrate combatState.enemyResolvedActions as the opening of the fight.',
                'The action that started the fight is not resolved as an attack now and needs no roll; '
                'each player character attacks on their own turn.',
            ]
        )
    if isinstance(dm_combat, dict) and dm_combat.get('deathSaves'):
        instructions.append(
            'combatState.deathSaves are engine-rolled death saving throws of downed characters; '
            'narrate each exact roll, result and outcome (still dying, stable, dead, or back on their feet at 1 HP).'
        )
    if isinstance(player_resolved_action, dict):
        instructions.extend(
            [
                'The player attack in combatState.playerResolvedAction is engine-owned.',
                'Narrate its exact target, hit or miss, attack total, and damage total; do not invent or revise the result.',
            ]
        )
    if any(
        isinstance(change, dict)
        and change.get('type') == 'spell.cast'
        and isinstance(change.get('spellResolution'), dict)
        for change in applied_changes
    ):
        instructions.extend(
            [
                'A targeted spell in stateChangesAlreadyApplied was fully resolved by the rules engine.',
                'Narrate its exact targets, attack or save rolls, damage, healing, conditions, and concentration; do not revise or duplicate them.',
            ]
        )
    scene_summary = compact.get('currentScene') if isinstance(compact.get('currentScene'), dict) else {}
    if scene_summary.get('interactables') or scene_summary.get('hazards'):
        instructions.extend(
            [
                'Scene interactables and hazards are authoritative persistent objects with exact IDs and state.',
                'Never narrate an object transition that is absent from validatedActions/stateChangesAlreadyApplied, and never reveal entries marked hidden or not player-known.',
            ]
        )
    return {
        'currentStateSummary': compact,
        'playerMessage': player_message,
        'validatedActions': validated_actions,
        'validActions': valid_actions,
        'invalidActions': invalid_actions,
        'pendingActions': pending_actions,
        'needsClarification': needs_clarification,
        'pendingRolls': pending_rolls,
        'stateChangesAlreadyApplied': [
            {
                'type': change.get('type'),
                'locationId': change.get('locationId'),
                'locationName': change.get('locationName') or change.get('name'),
                'questId': change.get('questId'),
                'questTitle': change.get('questTitle') or change.get('title'),
                'npcId': change.get('npcId'),
                'npcName': change.get('npcName') or change.get('name'),
                'flagKey': change.get('flagKey'),
                'itemName': change.get('itemName'),
                'spellName': change.get('spellName'),
                'spellResolution': change.get('spellResolution'),
                'capabilityName': change.get('capabilityName'),
                'effectType': change.get('effectType'),
                'objectAction': change.get('objectAction'),
                'targetId': change.get('targetId'),
                'targetName': change.get('targetName'),
                'eventType': change.get('eventType'),
                'slot': change.get('slot'),
                'amount': change.get('actualAmount', change.get('amount')),
                'currency': change.get('currency'),
                'xp': change.get('actualAmount', change.get('amount')) if str(change.get('type') or '').startswith('xp.') else None,
                'combatStatus': change.get('combatStatus'),
                'participantName': change.get('participantName'),
                'intentType': change.get('intentType'),
            }
            for change in applied_changes
            if isinstance(change, dict) and change.get('visible', True)
        ],
        'combatState': dm_combat,
        'dmInstructions': instructions,
    }


def augment_rules_hint_with_state_packet(rules_hint_payload: dict[str, Any], dm_context_packet: dict[str, Any]) -> dict[str, Any]:
    updated = dict(rules_hint_payload)
    updated['state_pipeline'] = dm_context_packet
    return updated


def _is_free_text(action_intent: dict[str, Any] | None, player_message: str) -> bool:
    kind = str((action_intent or {}).get('kind') or '').strip().lower() if isinstance(action_intent, dict) else ''
    return bool(str(player_message or '').strip()) and kind in {'', 'message'}


def _previous_target(turn: DmTurn) -> tuple[str, str] | None:
    """``("object" | "item", id)`` for the single scene object or item the session's previous turn acted on."""

    previous = (
        DmTurn.query.filter(DmTurn.session_id == turn.session_id, DmTurn.turn_id < turn.turn_id)
        .order_by(DmTurn.turn_id.desc())
        .first()
    )
    pipeline = _metadata(previous).get(STATE_PIPELINE_METADATA_KEY) if previous is not None else None
    if not isinstance(pipeline, dict):
        return None
    targets = set()
    for change in [*(pipeline.get('immediateAppliedChanges') or []), *(pipeline.get('combatAppliedChanges') or [])]:
        if not isinstance(change, dict):
            continue
        if change.get('type') == 'scene.interactable.action' and str(change.get('targetId') or ''):
            targets.add(('object', str(change['targetId'])))
        item = change.get('item') if isinstance(change.get('item'), dict) else {}
        if change.get('type') == 'scene.item.remove' and change.get('source') == PICKUP_SOURCE and str(item.get('id') or ''):
            targets.add(('item', str(item['id'])))
    return next(iter(targets)) if len(targets) == 1 else None


def _combat_is_running(state: dict[str, Any]) -> bool:
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    return str(combat.get('status') or '').strip().lower() in {'starting', 'active'}


def _actor_down_refusal(
    state: dict[str, Any],
    *,
    actor_id: str,
    action_intent: dict[str, Any] | None,
    free_text: FreeTextAction | None,
    player_message: str,
) -> str | None:
    """The refusal for a hero at 0 HP who is dying, stable, unconscious or dead; None when they may act.

    An out-of-character message always goes through, and a stable hero may rest
    outside a fight. The opening narration is not the hero's action.
    """

    kind = str((action_intent or {}).get('kind') or '').strip().lower() if isinstance(action_intent, dict) else ''
    if kind in {'ooc', 'admin'} or (not kind and not str(player_message or '').strip()):
        return None
    actor = find_actor(state, actor_id) or {}
    health = actor.get('health') if isinstance(actor.get('health'), dict) else {}
    conditions = {str(condition or '').strip().lower() for condition in health.get('conditions') or []}
    down = is_dead(actor) or (
        int_or_default(health.get('currentHp'), default=1) <= 0 and bool(conditions & set(VITALITY_CONDITIONS))
    )
    if not down:
        return None
    resting = kind == 'rest' or (free_text is not None and free_text.kind == 'rest')
    if resting and is_stable(actor) and not _combat_is_running(state):
        return None
    return DOWNED_ACTOR_REFUSAL


def _spoken(text: Any) -> str:
    return ' '.join(re.sub(r'[\'"“”‘’.,;:!?]', ' ', str(text or '').lower()).split())


def _present_npc_named(state: dict[str, Any], message: str) -> dict[str, Any] | None:
    """The one present NPC the message names by full or first name."""

    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    present = {str(value) for value in scene.get('activeNpcIds') or []}
    words = name_words(message)
    named = []
    for key in ('knownNpcs', 'partyNpcs'):
        for npc in state.get(key) or []:
            if not isinstance(npc, dict) or str(npc.get('id') or '') not in present:
                continue
            name = name_words(npc.get('name'))
            candidates = [name, name[:1]] if name and len(name[0]) >= 3 else [name]
            if any(
                candidate and words[index:index + len(candidate)] == candidate
                for candidate in candidates
                for index in range(len(words))
            ):
                named.append(npc)
    return named[0] if len(named) == 1 else None


def _talk_facts(*, session_obj: Session, turn: DmTurn, state: dict[str, Any], npc: dict[str, Any]) -> dict[str, Any]:
    """The person talked to and the next thing they know that has not been told in this session."""

    record = catalog_record(state, 'npcs', npc.get('id')) or npc
    knows = [str(fact).strip() for fact in record.get('knows') or [] if str(fact or '').strip()]
    told = ' '.join(
        _spoken(text)
        for (text,) in DmTurn.query.with_entities(DmTurn.dm_output).filter(
            DmTurn.session_id == session_obj.session_id,
            DmTurn.turn_id != turn.turn_id,
        )
    )
    return {
        'npcId': str(npc.get('id') or ''),
        'name': str(npc.get('name') or record.get('name') or '').strip(),
        'fact': next((fact for fact in knows if _spoken(fact) not in told), None),
    }


def _foreseen_fight_end(
    *,
    state: dict[str, Any],
    dm_context: dict[str, Any],
    actor_id: str,
    turn_id: int,
    already_applied: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    """How the engine-resolved enemy turns in ``dm_context`` end the fight once applied (its end reason),
    and the party defeat that brings, if it does."""

    if not _combat_is_running(state):
        return None, None
    trusted = derive_trusted_damage_changes(
        state=state,
        dm_context_packet=dm_context,
        actor_id=actor_id,
        turn_id=turn_id,
        already_applied_changes=already_applied,
    ).all_changes
    if not trusted:
        return None, None
    projected = apply_state_changes(
        state, validated_changes_for_application(validate_state_changes(state=state, changes=trusted))
    )['nextState']
    reason = check_combat_end(projected.get('combat') if isinstance(projected.get('combat'), dict) else {})
    if reason != 'objective_failed':
        return reason, None
    ended = apply_state_changes(
        projected,
        validated_changes_for_application(
            validate_state_changes(state=projected, changes=[combat_end_change(turn_id, reason)])
        ),
    )['nextState']
    if not party_is_beaten(ended):
        return reason, None
    phrase, several = enemies_phrase(state)
    return reason, {'enemies': phrase, 'plural': several}


def _narration_packet(
    *,
    session_obj: Session,
    turn: DmTurn,
    state_before: dict[str, Any],
    state_after: dict[str, Any],
    applied_changes: list[dict[str, Any]],
    actor_id: str,
    player_message: str,
    action_intent: dict[str, Any] | None,
    free_text: FreeTextAction | None,
    declared_actions: list[dict[str, Any]],
    turn_ends: bool,
) -> dict[str, Any]:
    """What exactly happened this turn, for a narrator that only tells what happened."""

    packet: dict[str, Any] = {
        'objects': object_facts(
            state_before=state_before,
            state_after=state_after,
            applied_changes=applied_changes,
            actor_id=actor_id,
        ),
        'enemies': enemy_names(state_after),
        **plain_results(state_after, applied_changes, actor_id=actor_id),
    }
    free = _is_free_text(action_intent, player_message)
    talk_npc = free_text.npc if free_text is not None and free_text.kind == 'talk' else None
    target = (action_intent or {}).get('target') if isinstance((action_intent or {}).get('target'), dict) else {}
    if (action_intent or {}).get('kind') == 'interact' and target.get('kind') == 'npc' and target.get('npc_id'):
        talk_npc = next(
            (
                npc
                for key in ('knownNpcs', 'partyNpcs')
                for npc in state_before.get(key) or []
                if isinstance(npc, dict) and str(npc.get('id') or '') == str(target.get('npc_id'))
            ),
            None,
        )
    if free and free_text is None and not declared_actions:
        talk_npc = _present_npc_named(state_before, player_message)
    if talk_npc:
        packet['talk'] = _talk_facts(session_obj=session_obj, turn=turn, state=state_before, npc=talk_npc)
    if free_text is not None:
        packet['freeText'] = {'kind': free_text.kind, 'more': free_text.more, 'deferred': deepcopy(list(free_text.deferred))}
    elif free and not declared_actions and not talk_npc and not turn.requires_roll:
        packet['freeText'] = {'kind': None, 'unmatched': True, 'more': False}
    started = fight_start(state_after, applied_changes)
    if started:
        packet['fightStart'] = started
    shaken_off = _conditions_shaken_off(state_after, turn_id=turn.turn_id) if turn_ends else []
    if shaken_off:
        packet['shakenOff'] = shaken_off
    line = next_actor_line(state_after, actor_id=actor_id, turn_ends=turn_ends)
    if line:
        packet['nextActorLine'] = line
    recovery = next(
        (change for change in applied_changes if isinstance(change, dict) and change.get('type') == 'combat.defeat_recovery'),
        None,
    )
    if recovery:
        packet['partyDefeat'] = {'enemies': recovery.get('departedEnemies'), 'plural': recovery.get('departedEnemiesPlural') is True}
    modifiers = _attack_modifiers_to_tell(turn)
    if modifiers:
        packet['attackModifiers'] = modifiers
    return packet


def _conditions_shaken_off(state: dict[str, Any], *, turn_id: Any) -> list[dict[str, Any]]:
    """The enemy abilities whose conditions end as the acting hero's turn ends: ``[{"name", "source"}]``."""

    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    names = {
        str(participant.get('id') or ''): str(participant.get('name') or '').strip()
        for participant in combat.get('participants') or []
        if isinstance(participant, dict)
    }
    return [
        {'name': names.get(str(entry.get('participantId') or '')) or '', 'source': str(entry['sourceName']).strip()}
        for entry in turn_end_preview(state, turn_id=turn_id)
        if str(entry.get('sourceName') or '').strip()
    ]


def _attack_modifiers_to_tell(turn: DmTurn) -> list[dict[str, Any]]:
    """The rules moving this turn's attack roll (``Wounded −2``) when the hero's previous attack roll
    had others, so the narration names a change once instead of on every swing."""

    roll = _metadata(turn).get('authoritative_roll')
    if not isinstance(roll, dict) or roll.get('rule_type') != 'attack' or not roll.get('modifiers'):
        return []
    earlier = (
        DmTurn.query.filter(
            DmTurn.session_id == turn.session_id,
            DmTurn.player_id == turn.player_id,
            DmTurn.turn_id < turn.turn_id,
            DmTurn.metadata_json.contains('authoritative_roll'),
        )
        .order_by(DmTurn.turn_id.desc())
        .limit(PREVIOUS_ATTACK_LOOKBACK)
    )
    for earlier_turn in earlier:
        previous = _metadata(earlier_turn).get('authoritative_roll')
        if isinstance(previous, dict) and previous.get('rule_type') == 'attack':
            return [] if previous.get('modifiers') == roll['modifiers'] else deepcopy(roll['modifiers'])
    return deepcopy(roll['modifiers'])


def _downed_actor_result(
    *,
    turn: DmTurn,
    state: dict[str, Any],
    actor_id: str,
    players_by_id: dict[int, Player],
    refusal: str,
) -> dict[str, Any]:
    """A turn refused before narration because its hero is down: nothing is extracted or applied."""

    pre_extraction = {'declaredActions': [], 'notes': ['actor_down']}
    pre_validation = {
        'validatedActions': [
            {'status': 'invalid', 'reason': refusal, 'originalAction': {'type': 'actor.down', 'actorId': actor_id}}
        ],
        'pendingRolls': [],
        'immediateChanges': [],
        'clarificationRequests': [],
    }
    empty_validation: dict[str, Any] = {'accepted': [], 'modified': [], 'rejected': []}
    dm_context = _dm_context_packet(state=state, player_message='', pre_validation=pre_validation, applied_changes=[])
    state_log = build_state_log(turn_id=turn.turn_id, pre_validation=pre_validation, immediate_validation=empty_validation)
    actor_down = {'reason': refusal}
    metadata = _metadata(turn)
    metadata[STATE_PIPELINE_METADATA_KEY] = {
        'version': STATE_PIPELINE_VERSION,
        'actorId': actor_id,
        'stateBeforePreDm': compact_state_for_extraction(state),
        'stateBeforeDm': state,
        'preDmExtraction': pre_extraction,
        'preDmValidation': pre_validation,
        'immediateValidation': empty_validation,
        'pendingImmediateChanges': [],
        'pendingImmediateValidation': empty_validation,
        'immediateAppliedChanges': [],
        'combatPreDmChanges': [],
        'combatPreDmValidation': empty_validation,
        'combatAppliedChanges': [],
        'combatDebug': {},
        'dmContextPacket': dm_context,
        'openingEnemyTurns': False,
        'actorDown': actor_down,
        'stateLog': state_log,
        'managedDomains': MANAGED_STATE_DOMAINS,
    }
    _set_metadata(turn, metadata)
    db.session.flush()
    return {
        'stateBeforeDm': state,
        'playersById': players_by_id,
        'preExtraction': pre_extraction,
        'preValidation': pre_validation,
        'immediateValidation': empty_validation,
        'pendingImmediateValidation': empty_validation,
        'pendingImmediateChanges': [],
        'immediateAppliedChanges': [],
        'combatValidation': empty_validation,
        'combatAppliedChanges': [],
        'combatDebug': {},
        'dmContextPacket': dm_context,
        'openingEnemyTurns': False,
        'actorDown': actor_down,
        'stateLog': state_log,
        'clarificationRequests': [],
    }


def pre_dm_pipeline(
    *,
    turn: DmTurn,
    session_obj: Session,
    campaign: Campaign,
    player: Player,
    player_message: str,
    action_intent: dict[str, Any] | None = None,
    selected_item_ids: dict[str, str] | None = None,
    declared_actions_override: list[dict[str, Any]] | None = None,
    active_player_ids: list[int] | None = None,
    before_helper_call: Callable[[], None] | None = None,
) -> dict[str, Any]:
    players = _players_for_campaign(campaign, player)
    players_by_id = {player_obj.player_id: player_obj for player_obj in players}
    orm_token = _pipeline_orm_token(
        turn=turn,
        session_obj=session_obj,
        campaign=campaign,
        player=player,
        players=players,
    )
    state = state_snapshot_for_session(
        session_obj=session_obj,
        campaign=campaign,
        players=players,
        active_player_ids=active_player_ids,
    )
    recent_timeline = recent_timeline_for_session(session_obj.session_id, limit=5)
    actor_id = display_actor_id(player.player_id)
    # Heroes left down outside a fight come to before anything else happens.
    wake = party_wake(state, turn_id=turn.turn_id)
    # Words that clearly name one action take exactly the path of that action's button.
    free_text = (
        free_text_action(
            state,
            actor_id=actor_id,
            player_id=player.player_id,
            message=player_message,
            previous_target=_previous_target(turn),
        )
        if not declared_actions_override and _is_free_text(action_intent, player_message)
        else None
    )
    down_refusal = _actor_down_refusal(
        state,
        actor_id=actor_id,
        action_intent=action_intent,
        free_text=free_text,
        player_message=player_message,
    )
    if down_refusal:
        return _downed_actor_result(turn=turn, state=state, actor_id=actor_id, players_by_id=players_by_id, refusal=down_refusal)

    if declared_actions_override:
        pre_extraction = {
            'declaredActions': declared_actions_override,
            'notes': ['clarification_resume'],
        }
    else:
        helper_session_released = False

        def _before_provider_call() -> None:
            nonlocal helper_session_released
            if before_helper_call:
                before_helper_call()
                helper_session_released = True

        pre_extraction = extract_pre_dm_actions(
            current_state=compact_state_for_extraction(state),
            player_message=player_message,
            recent_timeline=recent_timeline,
            actor_id=actor_id,
            action_intent=action_intent,
            free_text_intents=list(free_text.intents) if free_text is not None else None,
            force_helper=_turn_awaits_player_roll(turn),
            before_provider_call=_before_provider_call if before_helper_call else None,
        )
        if helper_session_released:
            turn, session_obj, campaign, player, players = _reload_pipeline_orm(orm_token)
            players_by_id = {player_obj.player_id: player_obj for player_obj in players}
    pre_validation = validate_declared_actions(
        state=state,
        declared_actions=pre_extraction.get('declaredActions') or [],
        current_turn=turn.turn_id,
        recent_context=_recent_context_strings(recent_timeline),
        selected_item_ids=selected_item_ids,
        expected_actor_id=actor_id,
    )
    pre_immediate_changes = [
        change
        for change in (pre_validation.get('immediateChanges') or [])
        if isinstance(change, dict)
    ]
    safe_immediate_candidates = [change for change in pre_immediate_changes if _safe_pre_dm_immediate_change(change)]
    pending_immediate_changes = [change for change in pre_immediate_changes if not _safe_pre_dm_immediate_change(change)]
    safe_immediate_changes, combat_gate_rejections = _gate_pre_dm_combat_spells(
        state=state,
        changes=safe_immediate_candidates,
        actor_id=actor_id,
        turn_id=turn.turn_id,
    )
    safe_immediate_changes, combat_object_rejections = _gate_pre_dm_combat_object_actions(
        state=state,
        changes=safe_immediate_changes,
        actor_id=actor_id,
        turn_id=turn.turn_id,
    )
    combat_gate_rejections = [*combat_gate_rejections, *combat_object_rejections]
    pre_validation = _with_rejected_pre_dm_changes(pre_validation, combat_gate_rejections)
    immediate_validation = _merge_validation_results(
        validate_state_changes(state=state, changes=safe_immediate_changes, expected_actor_id=actor_id),
        {'accepted': [], 'modified': [], 'rejected': combat_gate_rejections},
    )
    pending_immediate_validation = validate_state_changes(state=state, changes=pending_immediate_changes, expected_actor_id=actor_id)
    immediate_changes = validated_changes_for_application(immediate_validation)
    apply_result = apply_state_changes(state, immediate_changes)
    state_after_immediate = apply_result['nextState']
    applied_immediate = apply_result['appliedChanges']
    immediate_combat = (
        state_after_immediate.get('combat')
        if isinstance(state_after_immediate.get('combat'), dict)
        else {}
    )
    immediate_end_reason = (
        check_combat_end(immediate_combat)
        if any(
            isinstance(change, dict)
            and change.get('type') == 'spell.cast'
            and change.get('spellResolution')
            for change in applied_immediate
        )
        else None
    )
    if immediate_end_reason:
        immediate_end_change = combat_end_change(turn.turn_id, immediate_end_reason)
        immediate_end_validation = validate_state_changes(
            state=state_after_immediate,
            changes=[immediate_end_change],
        )
        immediate_end_apply = apply_state_changes(
            state_after_immediate,
            validated_changes_for_application(immediate_end_validation),
        )
        state_after_immediate = immediate_end_apply['nextState']
        applied_immediate = [*applied_immediate, *immediate_end_apply['appliedChanges']]
        immediate_validation = _merge_validation_results(
            immediate_validation,
            immediate_end_validation,
        )
    combat_session_released = False

    def _before_combat_provider_call() -> None:
        nonlocal combat_session_released
        if before_helper_call:
            before_helper_call()
            combat_session_released = True

    combat_prepare = prepare_combat_for_turn(
        state=state_after_immediate,
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        player_message=player_message,
        workspace_id=campaign.workspace_id,
        before_intent_provider_call=(
            _before_combat_provider_call if before_helper_call else None
        ),
        before_creature_provider_call=(
            _before_combat_provider_call if before_helper_call else None
        ),
    )
    if combat_session_released:
        turn, session_obj, campaign, player, players = _reload_pipeline_orm(orm_token)
        players_by_id = {player_obj.player_id: player_obj for player_obj in players}
    combat_changes = [
        change
        for change in (combat_prepare.get('changes') or [])
        if isinstance(change, dict)
    ]
    roll_consume_change = _player_roll_consume_change(turn, state_after_immediate)
    if roll_consume_change:
        combat_changes.append(roll_consume_change)
    combat_validation = validate_state_changes(state=state_after_immediate, changes=combat_changes)
    applied_combat_changes = validated_changes_for_application(combat_validation)
    combat_apply = apply_state_changes(state_after_immediate, applied_combat_changes)
    state_before_dm = combat_apply['nextState']
    applied_combat = combat_apply['appliedChanges']
    combat_context_for_dm = (
        combat_prepare.get('combatContext')
        if isinstance(combat_prepare.get('combatContext'), dict)
        else None
    )
    player_resolved_action = _resolved_player_attack_for_turn(
        turn=turn,
        state=state_before_dm,
        actor_id=actor_id,
    )
    trusted_player_changes: list[dict[str, Any]] = []
    if isinstance(player_resolved_action, dict):
        trusted_player_changes = derive_trusted_damage_changes(
            state=state_before_dm,
            dm_context_packet={
                'combatState': {
                    'playerResolvedAction': player_resolved_action,
                    'trustedDamageEvents': [player_resolved_action],
                }
            },
            actor_id=actor_id,
            turn_id=turn.turn_id,
            already_applied_changes=[*applied_immediate, *applied_combat],
        ).resolved
        if trusted_player_changes:
            trusted_player_validation = validate_state_changes(
                state=state_before_dm,
                changes=trusted_player_changes,
            )
            trusted_player_apply = apply_state_changes(
                state_before_dm,
                validated_changes_for_application(trusted_player_validation),
            )
            state_before_dm = trusted_player_apply['nextState']
            applied_combat = [*applied_combat, *trusted_player_apply['appliedChanges']]
            combat_changes = [*combat_changes, *trusted_player_changes]
            combat_validation = _merge_validation_results(combat_validation, trusted_player_validation)
            combat_after_damage = (
                state_before_dm.get('combat')
                if isinstance(state_before_dm.get('combat'), dict)
                else {}
            )
            engine_end_reason = check_combat_end(combat_after_damage)
            if engine_end_reason:
                engine_end_change = combat_end_change(turn.turn_id, engine_end_reason)
                engine_end_validation = validate_state_changes(
                    state=state_before_dm,
                    changes=[engine_end_change],
                )
                engine_end_apply = apply_state_changes(
                    state_before_dm,
                    validated_changes_for_application(engine_end_validation),
                )
                if engine_end_apply['appliedChanges']:
                    state_before_dm = engine_end_apply['nextState']
                    applied_combat = [*applied_combat, *engine_end_apply['appliedChanges']]
                    combat_changes = [*combat_changes, engine_end_change]
                    trusted_player_changes = [*trusted_player_changes, *engine_end_apply['appliedChanges']]
                    combat_context_for_dm = combat_summary_for_dm(state_before_dm['combat'])
                combat_validation = _merge_validation_results(combat_validation, engine_end_validation)
    defeat_recovery = party_defeat_recovery(state_before_dm, turn_id=turn.turn_id)
    applied_combat = [*([wake] if wake else []), *applied_combat, *([defeat_recovery] if defeat_recovery else [])]
    finalize_combat_prepare(
        session_obj=session_obj,
        campaign=campaign,
        prepare_result=combat_prepare,
        applied_changes=applied_combat,
        final_state=state_before_dm,
    )
    if applied_immediate or applied_combat:
        persist_state_to_database(session_obj=session_obj, state=state_before_dm, players_by_id=players_by_id)
    else:
        session_obj.state_snapshot = safe_json_dumps(state_before_dm, {})
    record_combat_debug_from_prepare(
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        prepare_result=combat_prepare,
    )

    opening_enemy_turns = _opens_with_enemy_turns(state_before_dm, [*applied_immediate, *applied_combat])
    enemy_deferred_reason = None if opening_enemy_turns else _combat_enemy_deferred_reason(turn)
    if opening_enemy_turns:
        # The action that started the fight is not resolved out of initiative
        # order, so no roll of this turn holds back the opening turns.
        dm_pre_validation = {**pre_validation, 'pendingRolls': []}
    elif _turn_awaits_player_roll(turn) and not (pre_validation.get('pendingRolls') or []):
        dm_pre_validation = {**pre_validation, 'pendingRolls': [_turn_level_pending_roll(turn, actor_id=actor_id)]}
    else:
        dm_pre_validation = pre_validation
    dm_context = _dm_context_packet(
        state=state_before_dm,
        player_message=player_message,
        pre_validation=dm_pre_validation,
        applied_changes=[*applied_immediate, *applied_combat],
        combat_context=combat_context_for_dm,
        resolved_player_roll=not opening_enemy_turns
        and (_turn_awaits_player_roll(turn) or _resolved_player_roll_should_defer_enemy(turn)),
        player_resolved_action=player_resolved_action,
        enemy_deferred_reason=enemy_deferred_reason,
        opening_enemy_turns=opening_enemy_turns,
        turn_id=turn.turn_id,
    )
    if trusted_player_changes:
        dm_context['trustedStateChanges'] = deepcopy(trusted_player_changes)
    dm_context['narration'] = _narration_packet(
        session_obj=session_obj,
        turn=turn,
        state_before=state,
        state_after=state_before_dm,
        applied_changes=[*applied_immediate, *applied_combat],
        actor_id=actor_id,
        player_message=player_message,
        action_intent=action_intent,
        free_text=free_text,
        declared_actions=pre_extraction.get('declaredActions') or [],
        turn_ends=opening_enemy_turns or enemy_deferred_reason is None,
    )
    if free_text is not None and free_text.refusal:
        # Words that name nothing here change nothing; only the narrator says so.
        dm_context['invalidActions'].append(
            {'status': 'invalid', 'summary': player_message, 'type': 'scene.item.pickup', 'reason': free_text.refusal}
        )
    started = dm_context['narration'].get('fightStart') or {}
    if started.get('setup'):
        dm_context['dmInstructions'].append(
            f'The fight begins. Before the first blows, convey this read-aloud text: "{started["setup"]}"'
        )
    for line in started.get('adjustments') or []:
        dm_context['dmInstructions'].append(f'This line must appear in the narration before the first blows: "{line}"')
    if any(fact.get('takeTime') is True for fact in dm_context['narration'].get('objects') or []):
        dm_context['dmInstructions'].append(f'Before telling the result of the check, say exactly: "{TAKE_TIME_LINE}"')
    foreseen_end, foreseen_defeat = (None, None) if defeat_recovery else _foreseen_fight_end(
        state=state_before_dm,
        dm_context=dm_context,
        actor_id=actor_id,
        turn_id=turn.turn_id,
        already_applied=[*applied_immediate, *applied_combat],
    )
    if foreseen_end:
        # A fight that ends this turn hands no turn on, so no turn ends lift a condition either.
        dm_context['narration']['fightEnds'] = foreseen_end
        dm_context['narration'].pop('nextActorLine', None)
        dm_context['narration'].pop('shakenOff', None)
    if foreseen_defeat:
        dm_context['narration']['partyDefeat'] = foreseen_defeat
        dm_context['narration'].pop('nextActorLine', None)
        dm_context['dmInstructions'].append(
            'The enemy turns resolved here put down every hero: after narrating them, tell that darkness '
            'takes the party and they wake later, alive but barely, with the enemies gone.'
        )
    dm_context['engineBrief'] = engine_brief(
        dm_context=dm_context,
        state_before=state,
        state_after=state_before_dm,
        applied_changes=[*applied_immediate, *applied_combat],
        free_text=free_text,
        player_message=player_message,
        actor_id=actor_id,
        player_id=player.player_id,
    )
    state_log = build_state_log(
        turn_id=turn.turn_id,
        pre_validation=pre_validation,
        immediate_validation=immediate_validation,
    )
    metadata = _metadata(turn)
    metadata[BEAT_BASIS_KEY] = beat_basis(state, actor_id=actor_id)
    if free_text is not None and free_text.intents:
        # The turn records the mapped intent, so it is handled exactly as the button's turn.
        metadata['action_intent'] = deepcopy(free_text.intents[0])
    if free_text is not None:
        metadata['free_text_action'] = {
            'kind': free_text.kind,
            'intents': deepcopy(list(free_text.intents)),
            'more': free_text.more,
            'deferred': deepcopy(list(free_text.deferred)),
            **({'refusal': free_text.refusal} if free_text.refusal else {}),
        }
    metadata[STATE_PIPELINE_METADATA_KEY] = {
        'version': STATE_PIPELINE_VERSION,
        'actorId': actor_id,
        'stateBeforePreDm': compact_state_for_extraction(state),
        'stateBeforeDm': state_before_dm,
        'preDmExtraction': pre_extraction,
        'preDmValidation': pre_validation,
        'immediateValidation': immediate_validation,
        'pendingImmediateChanges': validated_changes_for_application(pending_immediate_validation),
        'pendingImmediateValidation': pending_immediate_validation,
        'immediateAppliedChanges': applied_immediate,
        'combatPreDmChanges': combat_changes,
        'combatPreDmValidation': combat_validation,
        'combatAppliedChanges': applied_combat,
        'combatDebug': combat_prepare.get('debug') if isinstance(combat_prepare.get('debug'), dict) else {},
        'dmContextPacket': dm_context,
        'openingEnemyTurns': opening_enemy_turns,
        'stateLog': state_log,
        'managedDomains': MANAGED_STATE_DOMAINS,
    }
    _set_metadata(turn, metadata)
    db.session.flush()

    return {
        'stateBeforeDm': state_before_dm,
        'playersById': players_by_id,
        'preExtraction': pre_extraction,
        'preValidation': pre_validation,
        'immediateValidation': immediate_validation,
        'pendingImmediateValidation': pending_immediate_validation,
        'pendingImmediateChanges': validated_changes_for_application(pending_immediate_validation),
        'immediateAppliedChanges': applied_immediate,
        'combatValidation': combat_validation,
        'combatAppliedChanges': applied_combat,
        'combatDebug': combat_prepare.get('debug') if isinstance(combat_prepare.get('debug'), dict) else {},
        'dmContextPacket': dm_context,
        'openingEnemyTurns': opening_enemy_turns,
        'stateLog': state_log,
        'clarificationRequests': pre_validation.get('clarificationRequests') or [],
    }


def _without_authored_text(dm_response_text: str, dm_context_packet: dict[str, Any]) -> str:
    """The narration without the pack's authored passages it was given to tell (an object's action
    text and description, a fight's setup and adjustment lines), so wording such as "You strike the
    bell" is never read as a new fight."""

    narration = dm_context_packet.get('narration') if isinstance(dm_context_packet.get('narration'), dict) else {}
    started = narration.get('fightStart') if isinstance(narration.get('fightStart'), dict) else {}
    passages = [
        *(
            str(fact.get(key) or '').strip()
            for fact in narration.get('objects') or []
            if isinstance(fact, dict)
            for key in ('actionText', 'description')
        ),
        str(started.get('setup') or '').strip(),
        *(str(line or '').strip() for line in started.get('adjustments') or []),
    ]
    text = str(dm_response_text or '')
    for passage in sorted({passage for passage in passages if passage}, key=len, reverse=True):
        text = text.replace(passage, '')
    return text


def _reload_post_dm_orm(token: dict[str, Any]) -> tuple[DmTurn, Session, Campaign, Player]:
    """Reload after a post-DM provider wait, tolerating character edits.

    Characters are rebuilt from fresh Player rows before post-DM changes are
    validated, so only turn, session or campaign changes invalidate the result.
    """

    turn = db.session.get(DmTurn, token['turn_id'])
    session_obj = db.session.get(Session, token['session_id'])
    campaign = db.session.get(Campaign, token['campaign_id'])
    player = db.session.get(Player, token['player_id'])
    if not all((turn, session_obj, campaign, player)):
        raise RuntimeError('Turn state changed while the helper provider was running.')
    current = _pipeline_orm_token(
        turn=turn,
        session_obj=session_obj,
        campaign=campaign,
        player=player,
        players=[],
    )
    if any(current[key] != token[key] for key in current if key != 'players'):
        raise RuntimeError('Turn state changed while the helper provider was running.')
    return turn, session_obj, campaign, player


def _lock_campaign_players(campaign: Campaign, fallback_player: Player) -> list[Player]:
    """Re-read the campaign's Player rows, row-locked where the dialect supports it."""

    players = (
        Player.query.filter_by(workspace_id=campaign.workspace_id, campaign_id=campaign.campaign_id)
        .order_by(Player.player_id.asc())
        .populate_existing()
        .with_for_update()
        .all()
    )
    if not any(player_obj.player_id == fallback_player.player_id for player_obj in players):
        locked_fallback = db.session.get(
            Player,
            fallback_player.player_id,
            populate_existing=True,
            with_for_update=True,
        )
        players.append(locked_fallback or fallback_player)
    return players


def _ledger_ids(state: dict[str, Any]) -> set[str]:
    ledger = state.get('stateChangeLedger')
    return {
        str(entry.get('id'))
        for entry in (ledger if isinstance(ledger, list) else [])
        if isinstance(entry, dict)
    }


def _with_fresh_player_characters(
    state: dict[str, Any],
    players: list[Player],
    session_obj: Session,
) -> dict[str, Any]:
    """Return the state with every character rebuilt from its Player row.

    Combat participants keep their stored state; the applier keeps syncing
    actors from participants as before. If the session's ledger already holds
    changes the captured state lacks (the step is being retried, or the session
    was changed after the capture), the rest of the state is taken from the
    session as well, so those changes are neither applied twice nor undone.
    """

    current_snapshot = safe_json_loads(session_obj.state_snapshot, {})
    session_advanced = isinstance(current_snapshot, dict) and bool(
        _ledger_ids(current_snapshot) - _ledger_ids(state)
    )
    fresh_state = deepcopy(current_snapshot if session_advanced else state)
    if session_advanced and 'activePlayerIds' in state:
        fresh_state['activePlayerIds'] = deepcopy(state['activePlayerIds'])
    fresh_state['playerCharacters'] = [player_character_from_model(player_obj) for player_obj in players]
    return fresh_state


def _engine_resolved_turn(pipeline: dict[str, Any], state_before_dm: dict[str, Any], *, pending_roll: bool) -> bool:
    """Whether the engine resolved everything the turn does before narration.

    Every declared action was named without the pre-narration helper or a
    clarification, needed no Dungeon Master resolution and validated, every
    change it made was applied, and no fight is running or was started. The
    narration of such a turn describes what the engine did, so it is not read
    for state. A turn waiting on a roll or opening with enemy turns is never one.
    """

    if pending_roll or pipeline.get('openingEnemyTurns') is True:
        return False
    pre_extraction = pipeline.get('preDmExtraction') if isinstance(pipeline.get('preDmExtraction'), dict) else {}
    helper_debug = pre_extraction.get('debug') if isinstance(pre_extraction.get('debug'), dict) else {}
    declared_actions = pre_extraction.get('declaredActions') if isinstance(pre_extraction.get('declaredActions'), list) else []
    if (
        helper_debug.get('helperAttempted') is not False
        or 'clarification_resume' in (pre_extraction.get('notes') or [])
        or not declared_actions
        or not all(isinstance(action, dict) and action.get('requiresDMResolution') is False for action in declared_actions)
    ):
        return False
    pre_validation = pipeline.get('preDmValidation') if isinstance(pipeline.get('preDmValidation'), dict) else {}
    if not all(isinstance(result, dict) and result.get('status') == 'valid' for result in pre_validation.get('validatedActions') or []):
        return False
    if pipeline.get('pendingImmediateChanges') or not pipeline.get('immediateAppliedChanges'):
        return False
    combat_debug = pipeline.get('combatDebug') if isinstance(pipeline.get('combatDebug'), dict) else {}
    return not _combat_is_running(state_before_dm) and combat_debug.get('triggered') is not True


def _skipped_post_extraction(reason: str) -> dict[str, Any]:
    """Count a turn whose narration is not read for state and return its empty post-narration extraction."""

    telemetry_metric('state_pipeline.post_dm.skipped_total', 1, tags={'reason': reason})
    return {
        'proposedChanges': [],
        'uncertainChanges': [],
        'notes': [f'post_dm_skipped_{reason}'],
        'debug': {
            'source': 'skipped',
            'reason': reason,
            'helperAttempted': False,
            'helperSchemaValid': False,
            'helperModel': None,
            'helperRawText': None,
            'helperRawPreview': None,
            'helperParsed': None,
            'helperError': None,
            'fallbackRan': False,
            'fallbackReason': None,
        },
    }


def post_dm_pipeline(
    *,
    turn: DmTurn,
    session_obj: Session,
    campaign: Campaign,
    player: Player,
    dm_response_text: str,
    active_player_ids: list[int] | None = None,
    before_helper_call: Callable[[], None] | None = None,
) -> dict[str, Any]:
    metadata = _metadata(turn)
    pipeline = metadata.get(STATE_PIPELINE_METADATA_KEY) if isinstance(metadata.get(STATE_PIPELINE_METADATA_KEY), dict) else {}
    players = _players_for_campaign(campaign, player)
    players_by_id = {player_obj.player_id: player_obj for player_obj in players}
    orm_token = _pipeline_orm_token(
        turn=turn,
        session_obj=session_obj,
        campaign=campaign,
        player=player,
        players=players,
    )
    state_before_dm = pipeline.get('stateBeforeDm')
    if not isinstance(state_before_dm, dict):
        state_before_dm = state_snapshot_for_session(
            session_obj=session_obj,
            campaign=campaign,
            players=players,
            active_player_ids=active_player_ids,
        )
    elif active_player_ids is not None:
        state_before_dm = deepcopy(state_before_dm)
        active_ids: list[int] = []
        for raw_id in active_player_ids:
            parsed = int_or_default(raw_id, default=0)
            if parsed > 0 and parsed not in active_ids:
                active_ids.append(parsed)
        state_before_dm['activePlayerIds'] = active_ids

    actor_id = str(pipeline.get('actorId') or display_actor_id(player.player_id))
    recent_timeline = recent_timeline_for_session(session_obj.session_id, limit=5)
    already_applied = [*(pipeline.get('immediateAppliedChanges') or []), *(pipeline.get('combatAppliedChanges') or [])]
    pending_immediate_changes = [
        change
        for change in (pipeline.get('pendingImmediateChanges') or [])
        if isinstance(change, dict)
    ]
    post_combat_prepare: dict[str, Any] = {'debug': {}}
    opening_enemy_turns = pipeline.get('openingEnemyTurns') is True
    # Narrated opening turns land even when the narration asked for a roll
    # ("Roll initiative!"); otherwise a pending roll defers the outcome.
    skip_post_extraction = not opening_enemy_turns and bool(
        turn.requires_roll and turn.roll_value is None and str(turn.outcome_status or '').lower() == 'deferred'
    )
    engine_resolved = _engine_resolved_turn(pipeline, state_before_dm, pending_roll=skip_post_extraction)
    if skip_post_extraction:
        post_extraction = _skipped_post_extraction('pending_roll')
        post_validation = {'accepted': [], 'rejected': [], 'modified': []}
        players = _lock_campaign_players(campaign, player)
        players_by_id = {player_obj.player_id: player_obj for player_obj in players}
        state_before_dm = _with_fresh_player_characters(state_before_dm, players, session_obj)
        final_state, applied_post, post_validation, post_extraction = _settle_engine_turns(
            turn=turn,
            state=deepcopy(state_before_dm),
            already_applied=already_applied,
            applied_post=[],
            post_validation=post_validation,
            post_extraction=post_extraction,
        )
        recovered = [
            change
            for change in (
                party_defeat_recovery(final_state, turn_id=turn.turn_id),
                party_wake(final_state, turn_id=turn.turn_id),
            )
            if change
        ]
        applied_post = [*applied_post, *recovered]
        if applied_post:
            persist_state_to_database(session_obj=session_obj, state=final_state, players_by_id=players_by_id)
            sync_combat_encounter_record(
                session_obj=session_obj,
                campaign=campaign,
                combat=final_state.get('combat') if isinstance(final_state.get('combat'), dict) else {},
            )
        else:
            session_obj.state_snapshot = safe_json_dumps(final_state, {})
    else:
        if engine_resolved:
            # The narration only describes what the engine already did: no model reads it for state or a fight.
            post_extraction = _skipped_post_extraction('engine_resolved')
        else:
            helper_session_released = False

            def _before_provider_call() -> None:
                nonlocal helper_session_released
                if before_helper_call:
                    before_helper_call()
                    helper_session_released = True

            # Authored read-aloud text the narration carries is content, not a claim of new state.
            narrated_claims = _without_authored_text(
                dm_response_text,
                pipeline.get('dmContextPacket') if isinstance(pipeline.get('dmContextPacket'), dict) else {},
            )
            post_extraction = extract_post_dm_outcomes(
                state_before_dm=compact_state_for_extraction(state_before_dm),
                player_message=turn.player_input,
                validated_actions=pipeline.get('preDmValidation') if isinstance(pipeline.get('preDmValidation'), dict) else {},
                already_applied_changes=already_applied,
                dm_response=narrated_claims,
                recent_timeline=recent_timeline,
                actor_id=actor_id,
                turn_id=turn.turn_id,
                before_provider_call=_before_provider_call if before_helper_call else None,
            )
            if helper_session_released:
                turn, session_obj, campaign, player = _reload_post_dm_orm(orm_token)
            combat_session_released = False

            def _before_combat_provider_call() -> None:
                nonlocal combat_session_released
                if before_helper_call:
                    before_helper_call()
                    combat_session_released = True

            post_combat_prepare = prepare_combat_from_dm_response(
                state=state_before_dm,
                session_obj=session_obj,
                campaign=campaign,
                turn=turn,
                player_message=turn.player_input or '',
                dm_response=narrated_claims,
                workspace_id=campaign.workspace_id,
                before_intent_provider_call=(
                    _before_combat_provider_call if before_helper_call else None
                ),
                before_creature_provider_call=(
                    _before_combat_provider_call if before_helper_call else None
                ),
            )
            if combat_session_released:
                turn, session_obj, campaign, player = _reload_post_dm_orm(orm_token)
        # Character edits committed while narration or the helpers ran (another
        # session, a sheet edit) must survive: apply post-DM changes onto the
        # current rows, never onto the pre-narration actor copies.
        players = _lock_campaign_players(campaign, player)
        players_by_id = {player_obj.player_id: player_obj for player_obj in players}
        state_before_dm = _with_fresh_player_characters(state_before_dm, players, session_obj)
        post_combat_changes = [
            change
            for change in (post_combat_prepare.get('changes') or [])
            if isinstance(change, dict)
        ]
        if post_combat_changes:
            post_extraction = deepcopy(post_extraction)
            post_extraction['proposedChanges'] = _merge_state_changes(
                post_combat_changes,
                post_extraction.get('proposedChanges') or [],
                seed_changes=already_applied,
            )
            notes = list(post_extraction.get('notes') or [])
            if 'post_dm_combat_adjudicator' not in notes:
                notes.append('post_dm_combat_adjudicator')
            post_extraction['notes'] = notes
        intent_changes = _intent_confirmed_post_changes(
            turn=turn,
            dm_response_text=dm_response_text,
            actor_id=actor_id,
        )
        confirmed_pre_dm_changes = _confirmed_pre_dm_changes(
            turn=turn,
            pre_validation=pipeline.get('preDmValidation') if isinstance(pipeline.get('preDmValidation'), dict) else {},
            pending_immediate_changes=pending_immediate_changes,
            dm_response_text=dm_response_text,
        )
        if intent_changes or confirmed_pre_dm_changes:
            post_extraction = deepcopy(post_extraction)
            confirmed_transfers = [
                change
                for change in confirmed_pre_dm_changes
                if isinstance(change, dict) and str(change.get('type') or '').strip() in {'inventory.transfer', 'currency.transfer'}
            ]
            proposed_inventory_changes, correlated_intent_changes = _correlate_inventory_intent_changes(
                _without_confirmed_inventory_overlaps(
                    _without_confirmed_transfer_overlaps(
                        post_extraction.get('proposedChanges') or [],
                        confirmed_transfers,
                    ),
                    confirmed_pre_dm_changes,
                ),
                _without_confirmed_inventory_overlaps(
                    _without_confirmed_transfer_overlaps(intent_changes, confirmed_transfers),
                    confirmed_pre_dm_changes,
                ),
            )
            post_extraction['proposedChanges'] = _merge_state_changes(
                proposed_inventory_changes,
                correlated_intent_changes,
                confirmed_pre_dm_changes,
                seed_changes=already_applied,
            )
            notes = list(post_extraction.get('notes') or [])
            if intent_changes and 'intent_confirmed_post_dm' not in notes:
                notes.append('intent_confirmed_post_dm')
            if confirmed_pre_dm_changes and 'pre_dm_confirmed_post_dm' not in notes:
                notes.append('pre_dm_confirmed_post_dm')
            post_extraction['notes'] = notes
        proposed_before_authority_filter = [
            change
            for change in (post_extraction.get('proposedChanges') or [])
            if isinstance(change, dict)
        ]
        proposed_before_dedupe = _without_applied_immediate_overlaps(
            proposed_before_authority_filter,
            [
                change
                for change in (pipeline.get('immediateAppliedChanges') or [])
                if isinstance(change, dict)
            ],
            action_intent=(metadata.get('action_intent') if isinstance(metadata.get('action_intent'), dict) else None),
        )
        if len(proposed_before_dedupe) != len(proposed_before_authority_filter):
            post_extraction = deepcopy(post_extraction)
            post_extraction['proposedChanges'] = proposed_before_dedupe
            notes = list(post_extraction.get('notes') or [])
            if 'post_dm_authoritative_immediate_filter' not in notes:
                notes.append('post_dm_authoritative_immediate_filter')
            post_extraction['notes'] = notes
        proposed_after_dedupe = _merge_state_changes(proposed_before_dedupe, seed_changes=already_applied)
        if len(proposed_after_dedupe) != len(proposed_before_dedupe):
            post_extraction = deepcopy(post_extraction)
            post_extraction['proposedChanges'] = proposed_after_dedupe
            notes = list(post_extraction.get('notes') or [])
            if 'post_dm_semantic_dedupe' not in notes:
                notes.append('post_dm_semantic_dedupe')
            post_extraction['notes'] = notes
        dm_context_packet = pipeline.get('dmContextPacket') if isinstance(pipeline.get('dmContextPacket'), dict) else {}
        trusted_damage = derive_trusted_damage_changes(
            state=state_before_dm,
            dm_context_packet=dm_context_packet,
            actor_id=actor_id,
            turn_id=turn.turn_id,
            already_applied_changes=already_applied,
        )
        trusted_enemy_damage_changes = trusted_damage.enemy
        trusted_resolved_damage_changes = trusted_damage.resolved
        trusted_damage_changes = trusted_damage.all_changes
        protected_post_changes = without_trusted_damage_overlaps(
            post_extraction.get('proposedChanges') or [],
            trusted_damage_changes,
            dm_context_packet=dm_context_packet,
        )
        has_engine_player_resolution = bool(
            isinstance(dm_context_packet.get('combatState'), dict)
            and isinstance(dm_context_packet['combatState'].get('playerResolvedAction'), dict)
        )
        if trusted_damage_changes or has_engine_player_resolution:
            post_extraction = deepcopy(post_extraction)
            post_extraction['proposedChanges'] = [
                *trusted_damage_changes,
                *protected_post_changes,
            ]
            notes = list(post_extraction.get('notes') or [])
            if trusted_enemy_damage_changes and 'trusted_enemy_resolved_damage' not in notes:
                notes.append('trusted_enemy_resolved_damage')
            if trusted_resolved_damage_changes and 'trusted_resolved_damage' not in notes:
                notes.append('trusted_resolved_damage')
            if has_engine_player_resolution and 'engine_player_attack_authority' not in notes:
                notes.append('engine_player_attack_authority')
            post_extraction['notes'] = notes

        trusted_damage_ids = {
            str(change.get('id') or '').strip()
            for change in trusted_damage_changes
            if isinstance(change, dict) and str(change.get('id') or '').strip()
        }
        proposed_changes = [
            change
            for change in (post_extraction.get('proposedChanges') or [])
            if isinstance(change, dict)
        ]
        trusted_changes = [
            change
            for change in proposed_changes
            if str(change.get('id') or '').strip() in trusted_damage_ids
        ]
        untrusted_changes = [
            change
            for change in proposed_changes
            if str(change.get('id') or '').strip() not in trusted_damage_ids
        ]
        if trusted_changes:
            trusted_validation = validate_state_changes(state=state_before_dm, changes=trusted_changes)
            untrusted_validation = validate_state_changes(
                state=state_before_dm,
                changes=untrusted_changes,
                expected_actor_id=actor_id,
                authorized_cross_actor_change_ids=post_extraction.get('authorizedCrossActorChangeIds') or [],
            )
            post_validation = _merge_validation_results(trusted_validation, untrusted_validation)
        else:
            post_validation = validate_state_changes(
                state=state_before_dm,
                changes=untrusted_changes,
                expected_actor_id=actor_id,
                authorized_cross_actor_change_ids=post_extraction.get('authorizedCrossActorChangeIds') or [],
            )
        post_changes = validated_changes_for_application(post_validation)
        post_apply = apply_state_changes(state_before_dm, post_changes)
        final_state = post_apply['nextState']
        applied_post = post_apply['appliedChanges']
        final_combat = final_state.get('combat') if isinstance(final_state.get('combat'), dict) else {}
        engine_end_reason = (
            check_combat_end(final_combat)
            if trusted_damage_changes or has_engine_player_resolution
            else None
        )
        if engine_end_reason:
            engine_end_change = combat_end_change(turn.turn_id, engine_end_reason)
            engine_end_validation = validate_state_changes(state=final_state, changes=[engine_end_change])
            engine_end_apply = apply_state_changes(
                final_state,
                validated_changes_for_application(engine_end_validation),
            )
            final_state = engine_end_apply['nextState']
            applied_post = [*applied_post, *engine_end_apply['appliedChanges']]
            post_validation = _merge_validation_results(post_validation, engine_end_validation)
            if engine_end_apply['appliedChanges']:
                post_extraction = deepcopy(post_extraction)
                post_extraction['proposedChanges'] = [
                    *(post_extraction.get('proposedChanges') or []),
                    engine_end_change,
                ]
                notes = list(post_extraction.get('notes') or [])
                if 'engine_combat_end' not in notes:
                    notes.append('engine_combat_end')
                post_extraction['notes'] = notes
        # The opening block narration was given ends with initiative handed on;
        # otherwise only the active player's turn-ending action advances it.
        turn_advance_change = (
            engine_turn_advance_change(final_state, turn_id=turn.turn_id)
            if opening_enemy_turns
            else combat_turn_advance_change(state=final_state, turn=turn, actor_id=actor_id)
        )
        if turn_advance_change:
            advance_validation = validate_state_changes(state=final_state, changes=[turn_advance_change], expected_actor_id=actor_id)
            advance_changes = validated_changes_for_application(advance_validation)
            advance_apply = apply_state_changes(final_state, advance_changes)
            final_state = advance_apply['nextState']
            applied_post = [*applied_post, *advance_apply['appliedChanges']]
            post_validation = _merge_validation_results(post_validation, advance_validation)
            post_extraction = deepcopy(post_extraction)
            post_extraction['proposedChanges'] = [
                *(post_extraction.get('proposedChanges') or []),
                turn_advance_change,
            ]
            notes = list(post_extraction.get('notes') or [])
            if 'combat_turn_roster_advanced' not in notes:
                notes.append('combat_turn_roster_advanced')
            post_extraction['notes'] = notes
            # Turn-start effects can defeat the last combatant after the first
            # end check. End the encounter here as well so its ordinary reward
            # transaction runs once on the same turn.
            advanced_combat = final_state.get('combat') if isinstance(final_state.get('combat'), dict) else {}
            advance_end_reason = check_combat_end(advanced_combat) if advance_apply['appliedChanges'] else None
            if advance_end_reason:
                advance_end_change = combat_end_change(turn.turn_id, advance_end_reason)
                advance_end_validation = validate_state_changes(state=final_state, changes=[advance_end_change])
                advance_end_apply = apply_state_changes(
                    final_state, validated_changes_for_application(advance_end_validation),
                )
                final_state = advance_end_apply['nextState']
                applied_post = [*applied_post, *advance_end_apply['appliedChanges']]
                post_validation = _merge_validation_results(post_validation, advance_end_validation)
                if advance_end_apply['appliedChanges']:
                    post_extraction['proposedChanges'].append(advance_end_change)
                    if 'engine_combat_end' not in post_extraction['notes']:
                        post_extraction['notes'].append('engine_combat_end')
        final_state, applied_post, post_validation, post_extraction = _settle_engine_turns(
            turn=turn,
            state=final_state,
            already_applied=already_applied,
            applied_post=applied_post,
            post_validation=post_validation,
            post_extraction=post_extraction,
        )
        recovered = [
            change
            for change in (
                party_defeat_recovery(final_state, turn_id=turn.turn_id),
                party_wake(final_state, turn_id=turn.turn_id),
            )
            if change
        ]
        applied_post = [*applied_post, *recovered]
        finalized_encounter = finalize_combat_prepare(
            session_obj=session_obj,
            campaign=campaign,
            prepare_result=post_combat_prepare,
            applied_changes=applied_post,
            final_state=final_state,
        )
        if applied_post:
            persist_state_to_database(session_obj=session_obj, state=final_state, players_by_id=players_by_id)
        else:
            session_obj.state_snapshot = safe_json_dumps(final_state, {})
        if finalized_encounter is None and any(
            str(change.get('type') or '').startswith('combat.')
            for change in applied_post
            if isinstance(change, dict)
        ):
            sync_combat_encounter_record(
                session_obj=session_obj,
                campaign=campaign,
                combat=(
                    final_state.get('combat')
                    if isinstance(final_state.get('combat'), dict)
                    else {}
                ),
            )

    state_log = build_state_log(
        turn_id=turn.turn_id,
        pre_validation=pipeline.get('preDmValidation') if isinstance(pipeline.get('preDmValidation'), dict) else None,
        immediate_validation=pipeline.get('immediateValidation') if isinstance(pipeline.get('immediateValidation'), dict) else None,
        post_validation=post_validation,
    )
    all_applied = [*already_applied, *applied_post]
    legacy_summary = legacy_immediate_summary_from_applied(
        all_applied,
        rejected=[
            *(post_validation.get('rejected') or []),
            *((pipeline.get('immediateValidation') or {}).get('rejected') if isinstance(pipeline.get('immediateValidation'), dict) else []),
        ],
    )
    record_combat_debug_from_outcome(
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        prepare_result=post_combat_prepare,
        post_validation=post_validation,
        applied_changes=applied_post,
        state_log=state_log,
    )

    pipeline.update(
        {
            'stateBeforeDm': state_before_dm,
            'postDmExtraction': post_extraction,
            'postDmCombatDebug': post_combat_prepare.get('debug') if isinstance(post_combat_prepare.get('debug'), dict) else {},
            'postDmValidation': post_validation,
            'postAppliedChanges': applied_post,
            'finalStateSummary': compact_state_for_extraction(final_state),
            'stateLog': state_log,
            'managedDomains': MANAGED_STATE_DOMAINS,
        }
    )
    metadata[STATE_PIPELINE_METADATA_KEY] = pipeline
    metadata['immediate_state_changes_applied'] = legacy_summary
    turn.metadata_json = safe_json_dumps(metadata, {})
    db.session.flush()

    message = state_log_message(state_log)
    if message:
        record_turn_event(
            session_id=turn.session_id,
            campaign_id=campaign.campaign_id,
            turn_id=turn.turn_id,
            player_id=turn.player_id,
            event_type=STATE_UPDATE_EVENT,
            payload={
                'message': message,
                'stateLog': state_log,
                'metadata': {
                    'turn_id': turn.turn_id,
                    'state_log': state_log,
                    'state_pipeline_version': STATE_PIPELINE_VERSION,
                },
            },
        )

    return {
        'postExtraction': post_extraction,
        'postValidation': post_validation,
        'postAppliedChanges': applied_post,
        'stateLog': state_log,
        'stateLogMessage': message,
        'legacyImmediateSummary': legacy_summary,
        'finalState': deepcopy(final_state),
    }
