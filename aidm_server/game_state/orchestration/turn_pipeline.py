from __future__ import annotations

from copy import deepcopy
import random
import re
from typing import Any, Callable

from aidm_server.canon_inventory import append_drop_all_inventory_changes_from_text, inventory_change_from_intent_outcome
from aidm_server.canon_text import int_or_default
from aidm_server.combat.pipeline import (
    combat_turn_advance_change,
    prepare_combat_for_turn,
    prepare_combat_from_dm_response,
    record_combat_debug_from_outcome,
    record_combat_debug_from_prepare,
)
from aidm_server.database import db
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY, STATE_PIPELINE_VERSION
from aidm_server.game_state.application.applier import (
    apply_state_changes,
    legacy_immediate_summary_from_applied,
    persist_state_to_database,
)
from aidm_server.game_state.extraction.post_dm_outcome_extractor import extract_post_dm_outcomes
from aidm_server.game_state.extraction.pre_dm_action_extractor import extract_pre_dm_actions
from aidm_server.game_state.logging.state_log_builder import build_state_log, state_log_message
from aidm_server.game_state.models import (
    compact_state_for_extraction,
    display_actor_id,
    normalize_item_name,
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
from aidm_server.turn_events import record_turn_event


STATE_UPDATE_EVENT = 'state_update'
MANAGED_STATE_DOMAINS = ['inventory', 'currency', 'health', 'xp', 'spells', 'scene', 'quests', 'locations', 'npcs', 'flags', 'combat']
SAFE_PRE_DM_IMMEDIATE_CHANGE_TYPES = {'inventory.mark_used', 'inventory.equip', 'inventory.unequip'}
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
DAMAGE_DICE_PATTERN = re.compile(r'^\s*(?:(\d*)d(\d+))?\s*([+-]\s*\d+)?\s*$', re.IGNORECASE)


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
    return change_type in SAFE_PRE_DM_IMMEDIATE_CHANGE_TYPES and not bool(change.get('visible', True))


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


def _state_change_signature(change: dict[str, Any]) -> tuple[Any, ...] | None:
    change_type = str(change.get('type') or '').strip()
    actor_id = str(change.get('actorId') or change.get('actor_id') or '')
    if change_type in {'inventory.add', 'inventory.remove', 'inventory.equip', 'inventory.unequip'}:
        item = change.get('item') if isinstance(change.get('item'), dict) else {}
        item_name = change.get('itemName') or change.get('item_name') or item.get('name')
        return (change_type, actor_id, normalize_item_name(item_name), normalize_item_name(change.get('slot')))
    if change_type == 'inventory.transfer':
        item_name = change.get('itemName') or change.get('item_name')
        to_actor = str(change.get('toActorId') or change.get('to_actor_id') or change.get('toActorName') or change.get('to_actor_name') or '')
        return (
            change_type,
            actor_id,
            to_actor.lower(),
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
    if change_type in {'scene.update', 'scene.move_location'}:
        return (
            change_type,
            normalize_item_name(change.get('locationId') or change.get('name')),
            normalize_item_name(change.get('sceneType') or change.get('mood') or change.get('combatState')),
        )
    if change_type == 'combat.end':
        return (change_type,)
    if change_type == 'combat.participant.update':
        return (
            change_type,
            normalize_item_name(change.get('participantId') or change.get('enemyId')),
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


def _merge_state_changes(
    *change_lists: list[dict[str, Any]],
    seed_changes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for change in seed_changes or []:
        if isinstance(change, dict):
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
            if signature:
                seen.add(signature)
            merged.append(change)
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
    left_name = normalize_item_name(left.get('itemName') or left.get('item_name') or left_item.get('name'))
    right_name = normalize_item_name(right.get('itemName') or right.get('item_name') or right_item.get('name'))
    return bool(left_name and right_name and left_name == right_name)


def _same_positive_amount(left: dict[str, Any], right: dict[str, Any], key: str) -> bool:
    return int_or_default(left.get(key), default=0) > 0 and int_or_default(left.get(key), default=0) == int_or_default(right.get(key), default=0)


def _change_overlaps_confirmed_transfer(change: dict[str, Any], confirmed_transfers: list[dict[str, Any]]) -> bool:
    change_type = str(change.get('type') or '').strip()
    actor_id = _change_actor_id(change)
    for transfer in confirmed_transfers:
        transfer_type = str(transfer.get('type') or '').strip()
        if transfer_type == 'inventory.transfer':
            if change_type == 'inventory.remove' and actor_id == _transfer_source_actor_id(transfer):
                if _same_positive_amount(change, transfer, 'quantity') and _item_reference_matches(change, transfer):
                    return True
            if change_type == 'inventory.add' and actor_id == _transfer_target_actor_id(transfer):
                if _same_positive_amount(change, transfer, 'quantity') and _item_reference_matches(change, transfer):
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
        quantity = max(1, int_or_default(inventory_change.get('quantity'), default=1))
        if change_type and item_name:
            change: dict[str, Any] = {
                'id': stable_change_id(turn.turn_id, 'post_dm_intent', change_type, actor_id, item_name, quantity),
                'turnId': turn.turn_id,
                'type': change_type,
                'source': 'post_dm',
                'actorId': actor_id,
                'itemName': item_name,
                'quantity': quantity,
                'reason': f"DM confirmed requested inventory action for {item_name}.",
                'visible': True,
            }
            if change_type == 'inventory.add':
                change['item'] = {'name': item_name, 'quantity': quantity, 'type': 'misc'}
            changes.append(change)

    metadata = _metadata(turn)
    action_intent = metadata.get('action_intent') if isinstance(metadata.get('action_intent'), dict) else None
    if isinstance(action_intent, dict) and inventory_change:
        inventory_action = str(action_intent.get('inventory_action') or '').strip().lower()
        cost_gold = max(0, int_or_default(action_intent.get('cost_gold'), default=0))
        if cost_gold and inventory_action in {'buy', 'sell'}:
            change_type = 'currency.remove' if inventory_action == 'buy' else 'currency.add'
            changes.append(
                {
                    'id': stable_change_id(turn.turn_id, 'post_dm_intent', change_type, actor_id, 'gp', cost_gold),
                    'turnId': turn.turn_id,
                    'type': change_type,
                    'source': 'post_dm',
                    'actorId': actor_id,
                    'amount': cost_gold,
                    'currency': 'gp',
                    'reason': f"DM confirmed {inventory_action} action with known price/value.",
                    'visible': True,
                }
            )

    drop_all_patch = {'inventory_changes': []}
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


def _participant_id(participant: dict[str, Any]) -> str:
    return str(participant.get('id') or participant.get('participantId') or participant.get('actorId') or '').strip()


def _participant_name(participant: dict[str, Any] | None, fallback: str) -> str:
    if not isinstance(participant, dict):
        return fallback
    return str(participant.get('name') or participant.get('displayName') or fallback).strip() or fallback


def _combat_participants(state: dict[str, Any]) -> list[dict[str, Any]]:
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    participants = combat.get('participants') if isinstance(combat.get('participants'), list) else []
    return [participant for participant in participants if isinstance(participant, dict)]


def _participant_by_id(participants: list[dict[str, Any]], participant_id: Any) -> dict[str, Any] | None:
    wanted = str(participant_id or '').strip()
    if not wanted:
        return None
    for participant in participants:
        if _participant_id(participant) == wanted:
            return participant
    return None


def _ability_by_id(participant: dict[str, Any] | None, ability_id: Any) -> dict[str, Any] | None:
    if not isinstance(participant, dict):
        return None
    wanted = str(ability_id or '').strip()
    abilities = participant.get('abilities') if isinstance(participant.get('abilities'), list) else []
    if wanted:
        for ability in abilities:
            if isinstance(ability, dict) and str(ability.get('id') or ability.get('abilityId') or '').strip() == wanted:
                return ability
    for ability in abilities:
        if isinstance(ability, dict) and str(ability.get('type') or '').strip().lower() == 'attack':
            return ability
    return abilities[0] if abilities and isinstance(abilities[0], dict) else None


def _roll_die(sides: int, roller: Callable[[int], int] | None) -> int:
    sides = max(1, int_or_default(sides, default=1))
    raw_value = roller(sides) if roller else random.randint(1, sides)
    value = int_or_default(raw_value, default=1)
    return max(1, min(sides, value))


def _roll_damage_expression(dice_expression: Any, roller: Callable[[int], int] | None) -> dict[str, Any]:
    expression = str(dice_expression or '').strip().replace(' ', '')
    if re.fullmatch(r'[+-]?\d+', expression):
        total = int_or_default(expression, default=0)
        return {'dice': expression, 'rolls': [], 'bonus': total, 'total': max(0, total)}

    match = DAMAGE_DICE_PATTERN.match(expression)
    if not match:
        return {'dice': expression, 'rolls': [], 'bonus': 0, 'total': 0}

    count = int_or_default(match.group(1) or 1, default=1)
    sides = int_or_default(match.group(2), default=0)
    bonus = int_or_default((match.group(3) or '0').replace(' ', ''), default=0)
    rolls = [_roll_die(sides, roller) for _ in range(max(0, count))] if sides > 0 else []
    return {'dice': expression, 'rolls': rolls, 'bonus': bonus, 'total': max(0, sum(rolls) + bonus)}


def _target_armor_class(target: dict[str, Any] | None) -> int:
    if not isinstance(target, dict):
        return 10
    stats = target.get('stats') if isinstance(target.get('stats'), dict) else {}
    return _bounded_int(target.get('armorClass', stats.get('armorClass')), default=10, minimum=1, maximum=40)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    parsed = int_or_default(value, default=default)
    return max(minimum, min(maximum, parsed))


def _ability_damage(ability: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ability, dict):
        return {}
    damage = ability.get('damage')
    if isinstance(damage, dict):
        return damage
    if isinstance(damage, str):
        return {'dice': damage}
    return {}


def _resolve_enemy_required_actions(
    *,
    state: dict[str, Any],
    combat_context: dict[str, Any] | None,
    roller: Callable[[int], int] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(combat_context, dict):
        return []
    actions = combat_context.get('enemyRequiredActions') if isinstance(combat_context.get('enemyRequiredActions'), list) else []
    if not actions:
        return []

    participants = _combat_participants(state)
    resolved: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        intent_type = str(action.get('intentType') or action.get('type') or '').strip().lower()
        enemy_id = str(action.get('enemyId') or action.get('actorId') or action.get('participantId') or '').strip()
        target_id = str(action.get('targetId') or action.get('targetActorId') or '').strip()
        ability_id = str(action.get('abilityId') or action.get('ability_id') or '').strip()
        enemy = _participant_by_id(participants, enemy_id)
        target = _participant_by_id(participants, target_id)
        ability = _ability_by_id(enemy, ability_id)
        ability_name = str((ability or {}).get('name') or action.get('abilityName') or ability_id or '').strip()
        entry: dict[str, Any] = {
            'enemyId': enemy_id,
            'enemyName': _participant_name(enemy, enemy_id or 'Enemy'),
            'targetId': target_id,
            'targetName': _participant_name(target, target_id or 'target'),
            'intentType': intent_type,
            'abilityId': ability_id or ((ability or {}).get('id') if isinstance(ability, dict) else None),
            'abilityName': ability_name or None,
            'sourceIntent': action,
            'instruction': 'Narrate this enemy result as already resolved by the engine; do not ask the player to roll it.',
        }

        if intent_type in {'attack', 'use_ability'} and ability:
            attack_bonus = int_or_default((ability or {}).get('attackBonus', (ability or {}).get('toHitBonus')), default=0)
            attack_roll = _roll_die(20, roller)
            attack_total = attack_roll + attack_bonus
            target_ac = _target_armor_class(target)
            hit = attack_roll != 1 and (attack_roll == 20 or attack_total >= target_ac)
            damage = _ability_damage(ability)
            damage_roll = (
                _roll_damage_expression(damage.get('dice'), roller)
                if hit
                else {'dice': damage.get('dice'), 'rolls': [], 'bonus': 0, 'total': 0}
            )
            entry.update(
                {
                    'attackRoll': attack_roll,
                    'attackBonus': attack_bonus,
                    'attackTotal': attack_total,
                    'targetArmorClass': target_ac,
                    'hit': hit,
                    'critical': attack_roll == 20,
                    'damageDice': damage.get('dice'),
                    'damageRolls': damage_roll.get('rolls') or [],
                    'damageBonus': damage_roll.get('bonus') or 0,
                    'damageTotal': damage_roll.get('total') or 0,
                    'damageType': damage.get('type') or damage.get('damageType'),
                }
            )
        else:
            entry['resolvedWithoutRoll'] = True
        resolved.append(entry)
    return resolved


def _dm_combat_context(
    *,
    state: dict[str, Any],
    combat_context: dict[str, Any] | None,
    pending_rolls: list[dict[str, Any]],
    resolved_player_roll: bool,
    enemy_roller: Callable[[int], int] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(combat_context, dict):
        return None
    context = deepcopy(combat_context)
    if pending_rolls or resolved_player_roll:
        context['enemyRequiredActions'] = []
        context['enemyIntentSummary'] = ''
        context['enemyTelegraphs'] = []
        context['enemyResolvedActions'] = []
        context['enemyActionDeferredReason'] = 'pending_player_roll' if pending_rolls else 'player_roll_resolution'
        return context
    resolved_actions = _resolve_enemy_required_actions(state=state, combat_context=context, roller=enemy_roller)
    if resolved_actions:
        context['enemyResolvedActions'] = resolved_actions
    return context


def _dm_context_packet(
    *,
    state: dict[str, Any],
    player_message: str,
    pre_validation: dict[str, Any],
    applied_changes: list[dict[str, Any]],
    combat_context: dict[str, Any] | None = None,
    resolved_player_roll: bool = False,
    enemy_roller: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    compact = compact_state_for_extraction(state)
    raw_pending_rolls = pre_validation.get('pendingRolls')
    pending_rolls = [roll for roll in raw_pending_rolls if isinstance(roll, dict)] if isinstance(raw_pending_rolls, list) else []
    dm_combat = _dm_combat_context(
        state=state,
        combat_context=combat_context,
        pending_rolls=pending_rolls,
        resolved_player_roll=resolved_player_roll,
        enemy_roller=enemy_roller,
    )
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
) -> dict[str, Any]:
    players = _players_for_campaign(campaign, player)
    players_by_id = {player_obj.player_id: player_obj for player_obj in players}
    state = state_snapshot_for_session(
        session_obj=session_obj,
        campaign=campaign,
        players=players,
        active_player_ids=active_player_ids,
    )
    recent_timeline = recent_timeline_for_session(session_obj.session_id, limit=5)
    actor_id = display_actor_id(player.player_id)

    if declared_actions_override:
        pre_extraction = {
            'declaredActions': declared_actions_override,
            'notes': ['clarification_resume'],
        }
    else:
        pre_extraction = extract_pre_dm_actions(
            current_state=compact_state_for_extraction(state),
            player_message=player_message,
            recent_timeline=recent_timeline,
            actor_id=actor_id,
            action_intent=action_intent,
        )
    pre_validation = validate_declared_actions(
        state=state,
        declared_actions=pre_extraction.get('declaredActions') or [],
        current_turn=turn.turn_id,
        recent_context=_recent_context_strings(recent_timeline),
        selected_item_ids=selected_item_ids,
    )
    pre_immediate_changes = [
        change
        for change in (pre_validation.get('immediateChanges') or [])
        if isinstance(change, dict)
    ]
    safe_immediate_changes = [change for change in pre_immediate_changes if _safe_pre_dm_immediate_change(change)]
    pending_immediate_changes = [change for change in pre_immediate_changes if not _safe_pre_dm_immediate_change(change)]
    immediate_validation = validate_state_changes(state=state, changes=safe_immediate_changes)
    pending_immediate_validation = validate_state_changes(state=state, changes=pending_immediate_changes)
    immediate_changes = validated_changes_for_application(immediate_validation)
    apply_result = apply_state_changes(state, immediate_changes)
    state_after_immediate = apply_result['nextState']
    applied_immediate = apply_result['appliedChanges']
    combat_prepare = prepare_combat_for_turn(
        state=state_after_immediate,
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        player_message=player_message,
        workspace_id=campaign.workspace_id,
    )
    combat_changes = [
        change
        for change in (combat_prepare.get('changes') or [])
        if isinstance(change, dict)
    ]
    combat_validation = validate_state_changes(state=state_after_immediate, changes=combat_changes)
    applied_combat_changes = validated_changes_for_application(combat_validation)
    combat_apply = apply_state_changes(state_after_immediate, applied_combat_changes)
    state_before_dm = combat_apply['nextState']
    applied_combat = combat_apply['appliedChanges']
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

    dm_context = _dm_context_packet(
        state=state_before_dm,
        player_message=player_message,
        pre_validation=pre_validation,
        applied_changes=[*applied_immediate, *applied_combat],
        combat_context=combat_prepare.get('combatContext') if isinstance(combat_prepare.get('combatContext'), dict) else None,
        resolved_player_roll=_turn_resolves_player_roll(turn),
    )
    state_log = build_state_log(
        turn_id=turn.turn_id,
        pre_validation=pre_validation,
        immediate_validation=immediate_validation,
    )
    metadata = _metadata(turn)
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
        'stateLog': state_log,
        'clarificationRequests': pre_validation.get('clarificationRequests') or [],
    }


def post_dm_pipeline(
    *,
    turn: DmTurn,
    session_obj: Session,
    campaign: Campaign,
    player: Player,
    dm_response_text: str,
    active_player_ids: list[int] | None = None,
) -> dict[str, Any]:
    metadata = _metadata(turn)
    pipeline = metadata.get(STATE_PIPELINE_METADATA_KEY) if isinstance(metadata.get(STATE_PIPELINE_METADATA_KEY), dict) else {}
    players = _players_for_campaign(campaign, player)
    players_by_id = {player_obj.player_id: player_obj for player_obj in players}
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
    skip_post_extraction = bool(turn.requires_roll and turn.roll_value is None and str(turn.outcome_status or '').lower() == 'deferred')
    if skip_post_extraction:
        post_extraction = {
            'proposedChanges': [],
            'uncertainChanges': [],
            'notes': ['post_dm_skipped_pending_roll'],
            'debug': {
                'source': 'skipped',
                'reason': 'pending_roll',
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
        post_validation = {'accepted': [], 'rejected': [], 'modified': []}
        final_state = deepcopy(state_before_dm)
        applied_post: list[dict[str, Any]] = []
        session_obj.state_snapshot = safe_json_dumps(final_state, {})
    else:
        post_extraction = extract_post_dm_outcomes(
            state_before_dm=compact_state_for_extraction(state_before_dm),
            player_message=turn.player_input,
            validated_actions=pipeline.get('preDmValidation') if isinstance(pipeline.get('preDmValidation'), dict) else {},
            already_applied_changes=already_applied,
            dm_response=dm_response_text,
            recent_timeline=recent_timeline,
            actor_id=actor_id,
            turn_id=turn.turn_id,
        )
        post_combat_prepare = prepare_combat_from_dm_response(
            state=state_before_dm,
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            player_message=turn.player_input or '',
            dm_response=dm_response_text,
            workspace_id=campaign.workspace_id,
        )
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
            post_extraction['proposedChanges'] = _merge_state_changes(
                _without_confirmed_transfer_overlaps(post_extraction.get('proposedChanges') or [], confirmed_transfers),
                _without_confirmed_transfer_overlaps(intent_changes, confirmed_transfers),
                confirmed_pre_dm_changes,
                seed_changes=already_applied,
            )
            notes = list(post_extraction.get('notes') or [])
            if intent_changes and 'intent_confirmed_post_dm' not in notes:
                notes.append('intent_confirmed_post_dm')
            if confirmed_pre_dm_changes and 'pre_dm_confirmed_post_dm' not in notes:
                notes.append('pre_dm_confirmed_post_dm')
            post_extraction['notes'] = notes
        proposed_before_dedupe = [
            change
            for change in (post_extraction.get('proposedChanges') or [])
            if isinstance(change, dict)
        ]
        proposed_after_dedupe = _merge_state_changes(proposed_before_dedupe, seed_changes=already_applied)
        if len(proposed_after_dedupe) != len(proposed_before_dedupe):
            post_extraction = deepcopy(post_extraction)
            post_extraction['proposedChanges'] = proposed_after_dedupe
            notes = list(post_extraction.get('notes') or [])
            if 'post_dm_semantic_dedupe' not in notes:
                notes.append('post_dm_semantic_dedupe')
            post_extraction['notes'] = notes
        post_validation = validate_state_changes(state=state_before_dm, changes=post_extraction.get('proposedChanges') or [])
        post_changes = validated_changes_for_application(post_validation)
        post_apply = apply_state_changes(state_before_dm, post_changes)
        final_state = post_apply['nextState']
        applied_post = post_apply['appliedChanges']
        turn_advance_change = combat_turn_advance_change(state=final_state, turn=turn, actor_id=actor_id)
        if turn_advance_change:
            advance_validation = validate_state_changes(state=final_state, changes=[turn_advance_change])
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
        if applied_post:
            persist_state_to_database(session_obj=session_obj, state=final_state, players_by_id=players_by_id)
        else:
            session_obj.state_snapshot = safe_json_dumps(final_state, {})

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
