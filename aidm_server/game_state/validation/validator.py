from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.game_state.equipment import (
    conflict_items,
    equipment_slot_label,
    infer_equipment_slot,
    is_equippable,
)
from aidm_server.game_state.action_types import PRE_DM_ACTION_TYPES
from aidm_server.game_state.change_types import COMBAT_STATE_CHANGE_TYPES, CURRENCY_TYPES, STATE_CHANGE_TYPES, WORLD_STATE_CHANGE_TYPES
from aidm_server.game_state.models import (
    actor_currency,
    actor_items,
    actor_name,
    find_actor,
    find_actor_by_name,
    normalize_item_name,
    stable_slug,
    stable_change_id,
    state_applied_change_ids,
)
from aidm_server.combat.morale import MORALE_EVENTS, apply_morale_event
from aidm_server.combat.state import RANGE_BANDS, normalize_battlefield, normalize_combat_state, normalize_participant
from aidm_server.game_state.validation.inventory_validator import resolve_inventory_item_reference
from aidm_server.spellbook import normalize_spellbook, spell_from_change


CONSUMABLE_TYPES = {'consumable', 'potion', 'food'}
UNRESOLVED_TARGET_LABELS = {'', 'target', 'someone', 'somebody', 'an npc', 'a npc', 'npc'}
GENERIC_EXTRACTED_REASON = 'Extracted from DM response.'
SCENE_TYPES = {'social', 'exploration', 'travel', 'combat', 'dungeon', 'rest', 'mystery', 'shopping', 'dialogue'}
SCENE_MOODS = {'calm', 'tense', 'eerie', 'heroic', 'sad', 'mysterious', 'dangerous'}
COMBAT_STATES = {'none', 'pending', 'active', 'resolved'}
LOCATION_TYPES = {'tavern', 'town', 'dungeon', 'forest', 'road', 'shop', 'castle', 'ruins', 'cave', 'wilderness', 'other'}
LOCATION_STATUSES = {'known', 'discovered', 'visited', 'hidden', 'inaccessible'}
QUEST_STATUSES = {'available', 'active', 'completed', 'failed', 'abandoned', 'hidden'}
OBJECTIVE_STATUSES = {'open', 'completed', 'failed', 'optional'}
NPC_DISPOSITIONS = {'friendly', 'neutral', 'hostile', 'suspicious', 'afraid', 'loyal', 'unknown'}
NPC_STATUSES = {'known', 'met', 'allied', 'hostile', 'dead', 'missing', 'unknown'}


def _action_value(action: dict[str, Any], camel_key: str, snake_key: str | None = None, default=None):
    if camel_key in action:
        return action.get(camel_key)
    if snake_key and snake_key in action:
        return action.get(snake_key)
    return default


def _target_actor_from_payload(state: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    target_id = (
        _action_value(payload, 'toActorId', 'to_actor_id')
        or _action_value(payload, 'targetActorId', 'target_actor_id')
        or _action_value(payload, 'targetId', 'target_id')
    )
    if target_id:
        target = find_actor(state, target_id)
        if target:
            return target, ''
        return None, f"Target actor '{target_id}' was not found."

    target_name = (
        _action_value(payload, 'toActorName', 'to_actor_name')
        or _action_value(payload, 'targetActorName', 'target_actor_name')
        or _action_value(payload, 'targetName', 'target_name')
    )
    normalized_target_name = normalize_item_name(target_name)
    if normalized_target_name in UNRESOLVED_TARGET_LABELS:
        return None, 'Transfer target is missing.'
    target = find_actor_by_name(state, target_name)
    if target:
        return target, ''
    return None, f"Target actor '{target_name}' was not found."


def _target_actor_name_from_payload(payload: dict[str, Any]) -> str:
    target_name = (
        _action_value(payload, 'toActorName', 'to_actor_name')
        or _action_value(payload, 'targetActorName', 'target_actor_name')
        or _action_value(payload, 'targetName', 'target_name')
    )
    return str(target_name or '').strip()


def _has_named_untracked_target(payload: dict[str, Any]) -> bool:
    target_name = _target_actor_name_from_payload(payload)
    return bool(target_name and normalize_item_name(target_name) not in UNRESOLVED_TARGET_LABELS)


def _invalid(action: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        'actionId': action.get('id'),
        'status': 'invalid',
        'originalAction': action,
        'reason': reason,
    }


def _valid(
    action: dict[str, Any],
    reason: str,
    *,
    normalized_action: dict[str, Any] | None = None,
    immediate_changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        'actionId': action.get('id'),
        'status': 'valid',
        'originalAction': action,
        'normalizedAction': normalized_action or {},
        'reason': reason,
        'immediateChanges': immediate_changes or [],
    }


def _pending(
    action: dict[str, Any],
    reason: str,
    *,
    normalized_action: dict[str, Any] | None = None,
    required_rolls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        'actionId': action.get('id'),
        'status': 'pending',
        'originalAction': action,
        'normalizedAction': normalized_action or {},
        'reason': reason,
        'requiredRolls': required_rolls or [],
    }


def _clarification(action: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
    return {
        'actionId': action.get('id'),
        'status': 'needs_clarification',
        'originalAction': action,
        'reason': resolution.get('reason') or 'Item reference is ambiguous.',
        'clarificationRequest': {
            'type': 'item_resolution',
            'prompt': resolution.get('query') or 'Which item do you use?',
            'originalAction': action,
            'options': resolution.get('options') or [],
        },
    }


def _resolve_action_item(
    *,
    action: dict[str, Any],
    state: dict[str, Any],
    item_name: str,
    requested_type: str | None = None,
    requested_subtype: str | None = None,
    current_turn: int = 0,
    recent_context: list[str] | None = None,
    selected_item_id: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    actor = find_actor(state, _action_value(action, 'actorId', 'actor_id'))
    if not actor:
        return None, None, {'status': 'missing', 'reason': 'Actor not found.', 'searchedName': item_name}
    metadata = actor.get('metadata') if isinstance(actor.get('metadata'), dict) else {}
    resolution = resolve_inventory_item_reference(
        actor_inventory=actor_items(actor),
        requested_name=item_name,
        requested_type=requested_type,
        requested_subtype=requested_subtype,
        current_turn=current_turn,
        recent_context=recent_context or [],
        default_item_id=metadata.get('defaultWeaponId') or metadata.get('default_weapon_id'),
        selected_item_id=selected_item_id,
    )
    item = None
    if resolution.get('status') == 'resolved':
        item = next((candidate for candidate in actor_items(actor) if candidate.get('id') == resolution.get('itemId')), None)
    return actor, item, resolution


def _validate_consume_item(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
    recent_context: list[str],
    selected_item_id: str | None,
) -> dict[str, Any]:
    item_name = str(_action_value(action, 'itemName', 'item_name') or '').strip()
    quantity = max(1, int_or_default(action.get('quantity'), default=1))
    actor, item, resolution = _resolve_action_item(
        action=action,
        state=state,
        item_name=item_name,
        current_turn=current_turn,
        recent_context=recent_context,
        selected_item_id=selected_item_id,
    )
    if not actor:
        return _invalid(action, resolution.get('reason') or 'Actor not found.')
    if resolution.get('status') == 'needs_clarification':
        return _clarification(action, resolution)
    if resolution.get('status') == 'missing' or not item:
        return _invalid(action, f"{actor_name(actor)} does not have {item_name or 'that item'}.")
    if int_or_default(item.get('quantity'), default=1) < quantity:
        return _invalid(action, f"Not enough {item.get('name')}. Available: {item.get('quantity')}.")
    item_type = normalize_item_name(item.get('type'))
    item_subtype = normalize_item_name(item.get('subtype'))
    item_labels = {item_type, item_subtype, *[normalize_item_name(tag) for tag in item.get('tags') or []]}
    if not (item_labels & CONSUMABLE_TYPES) and 'potion' not in normalize_item_name(item.get('name')):
        return _invalid(action, f"{item.get('name')} is not consumable.")

    change = {
        'id': stable_change_id(current_turn, 'pre_dm', action.get('id'), 'inventory.remove', item.get('id'), quantity),
        'turnId': current_turn,
        'type': 'inventory.remove',
        'source': 'pre_dm',
        'actorId': actor.get('id'),
        'itemId': item.get('id'),
        'itemName': item.get('name'),
        'quantity': quantity,
        'reason': f"{item.get('name')} consumed.",
        'visible': True,
    }
    return _valid(
        action,
        f"{actor_name(actor)} has {item.get('name')} x{item.get('quantity')}.",
        normalized_action={
            **action,
            'itemId': item.get('id'),
            'itemName': item.get('name'),
            'resolution': resolution,
        },
        immediate_changes=[change],
    )


def _validate_use_or_transfer_item(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
    recent_context: list[str],
    selected_item_id: str | None,
) -> dict[str, Any]:
    item_name = str(_action_value(action, 'itemName', 'item_name') or '').strip()
    quantity = max(1, int_or_default(action.get('quantity'), default=1))
    actor, item, resolution = _resolve_action_item(
        action=action,
        state=state,
        item_name=item_name,
        current_turn=current_turn,
        recent_context=recent_context,
        selected_item_id=selected_item_id,
    )
    if not actor:
        return _invalid(action, resolution.get('reason') or 'Actor not found.')
    if resolution.get('status') == 'needs_clarification':
        return _clarification(action, resolution)
    if resolution.get('status') == 'missing' or not item:
        return _invalid(action, f"{actor_name(actor)} does not have {item_name or 'that item'}.")
    if int_or_default(item.get('quantity'), default=1) < quantity:
        return _invalid(action, f"Not enough {item.get('name')}. Available: {item.get('quantity')}.")
    return _pending(
        action,
        f"{actor_name(actor)} has {item.get('name')} x{item.get('quantity')}.",
        normalized_action={
            **action,
            'itemId': item.get('id'),
            'itemName': item.get('name'),
            'resolution': resolution,
        },
    )


def _validate_inventory_transfer(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
    recent_context: list[str],
    selected_item_id: str | None,
) -> dict[str, Any]:
    item_name = str(_action_value(action, 'itemName', 'item_name') or '').strip()
    quantity = max(1, int_or_default(action.get('quantity'), default=1))
    actor, item, resolution = _resolve_action_item(
        action=action,
        state=state,
        item_name=item_name,
        current_turn=current_turn,
        recent_context=recent_context,
        selected_item_id=selected_item_id,
    )
    if not actor:
        return _invalid(action, resolution.get('reason') or 'Actor not found.')
    if resolution.get('status') == 'needs_clarification':
        return _clarification(action, resolution)
    if resolution.get('status') == 'missing' or not item:
        return _invalid(action, f"{actor_name(actor)} does not have {item_name or 'that item'}.")
    if int_or_default(item.get('quantity'), default=1) < quantity:
        return _invalid(action, f"Not enough {item.get('name')}. Available: {item.get('quantity')}.")

    target, target_error = _target_actor_from_payload(state, action)
    if not target:
        if _has_named_untracked_target(action):
            target_name = _target_actor_name_from_payload(action)
            return _pending(
                action,
                f"{actor_name(actor)} can offer {item.get('name')} x{quantity} to {target_name}; target is not tracked, so DM must resolve the exchange.",
                normalized_action={
                    **action,
                    'fromActorId': actor.get('id'),
                    'toActorName': target_name,
                    'itemId': item.get('id'),
                    'itemName': item.get('name'),
                    'quantity': quantity,
                    'resolution': resolution,
                    'untrackedTarget': True,
                },
            )
        return _invalid(action, target_error or 'Transfer target was not found.')
    if str(target.get('id')) == str(actor.get('id')):
        return _invalid(action, 'Transfer target must be different from the source actor.')

    return _pending(
        action,
        f"{actor_name(actor)} can give {item.get('name')} x{quantity} to {actor_name(target)}.",
        normalized_action={
            **action,
            'fromActorId': actor.get('id'),
            'toActorId': target.get('id'),
            'toActorName': actor_name(target),
            'itemId': item.get('id'),
            'itemName': item.get('name'),
            'quantity': quantity,
            'resolution': resolution,
        },
    )


def _validate_equipment_action(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
    recent_context: list[str],
    selected_item_id: str | None,
    equip: bool,
) -> dict[str, Any]:
    item_name = str(_action_value(action, 'itemName', 'item_name') or '').strip()
    actor, item, resolution = _resolve_action_item(
        action=action,
        state=state,
        item_name=item_name,
        current_turn=current_turn,
        recent_context=recent_context,
        selected_item_id=selected_item_id,
    )
    if not actor:
        return _invalid(action, resolution.get('reason') or 'Actor not found.')
    if resolution.get('status') == 'needs_clarification':
        return _clarification(action, resolution)
    if resolution.get('status') == 'missing' or not item:
        return _invalid(action, f"{actor_name(actor)} does not have {item_name or 'that item'}.")

    if not equip:
        if not item.get('equipped'):
            return _valid(
                action,
                f"{item.get('name')} is already unequipped.",
                normalized_action={**action, 'itemId': item.get('id'), 'itemName': item.get('name'), 'resolution': resolution},
            )
        change = {
            'id': stable_change_id(current_turn, 'pre_dm', action.get('id'), 'inventory.unequip', item.get('id')),
            'turnId': current_turn,
            'type': 'inventory.unequip',
            'source': 'pre_dm',
            'actorId': actor.get('id'),
            'itemId': item.get('id'),
            'itemName': item.get('name'),
            'reason': f"{actor_name(actor)} unequipped {item.get('name')}.",
            'visible': False,
        }
        return _valid(
            action,
            f"{actor_name(actor)} can unequip {item.get('name')}.",
            normalized_action={**action, 'itemId': item.get('id'), 'itemName': item.get('name'), 'resolution': resolution},
            immediate_changes=[change],
        )

    if not is_equippable(item):
        return _invalid(action, f"{item.get('name')} is not equippable.")
    slot = infer_equipment_slot(
        item,
        requested_slot=_action_value(action, 'slot', 'equipment_slot'),
        equipped_items=actor_items(actor),
    )
    if not slot:
        return _invalid(action, f"{item.get('name')} does not have an equipment slot.")
    if item.get('equipped') and str(item.get('slot') or '') == slot:
        return _valid(
            action,
            f"{item.get('name')} is already equipped in {equipment_slot_label(slot)}.",
            normalized_action={
                **action,
                'itemId': item.get('id'),
                'itemName': item.get('name'),
                'slot': slot,
                'resolution': resolution,
            },
        )
    conflicts = conflict_items(actor_items(actor), item, slot)
    change = {
        'id': stable_change_id(current_turn, 'pre_dm', action.get('id'), 'inventory.equip', item.get('id'), slot),
        'turnId': current_turn,
        'type': 'inventory.equip',
        'source': 'pre_dm',
        'actorId': actor.get('id'),
        'itemId': item.get('id'),
        'itemName': item.get('name'),
        'slot': slot,
        'conflictItemIds': [conflict.get('id') for conflict in conflicts if conflict.get('id')],
        'conflictItemNames': [conflict.get('name') for conflict in conflicts if conflict.get('name')],
        'reason': f"{actor_name(actor)} equipped {item.get('name')} in {equipment_slot_label(slot)}.",
        'visible': False,
    }
    return _valid(
        action,
        f"{actor_name(actor)} can equip {item.get('name')} in {equipment_slot_label(slot)}.",
        normalized_action={
            **action,
            'itemId': item.get('id'),
            'itemName': item.get('name'),
            'slot': slot,
            'resolution': resolution,
        },
        immediate_changes=[change],
    )


def _validate_currency_transfer(action: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    actor = find_actor(state, _action_value(action, 'fromActorId', 'from_actor_id') or _action_value(action, 'actorId', 'actor_id'))
    if not actor:
        return _invalid(action, 'Actor not found.')
    currency = str(action.get('currency') or '').strip().lower()
    amount = max(0, int_or_default(action.get('amount'), default=0))
    if currency not in CURRENCY_TYPES or amount <= 0:
        return _invalid(action, 'Currency transfer requires a positive amount and valid denomination.')
    available = actor_currency(actor).get(currency, 0)
    if amount > available:
        return _invalid(action, f"{actor_name(actor)} has {available} {currency} and cannot transfer {amount}.")
    target, target_error = _target_actor_from_payload(state, action)
    if not target:
        if _has_named_untracked_target(action):
            target_name = _target_actor_name_from_payload(action)
            return _pending(
                action,
                f"{actor_name(actor)} can offer {amount} {currency} to {target_name}; target is not tracked, so DM must resolve the exchange.",
                normalized_action={
                    **action,
                    'fromActorId': actor.get('id'),
                    'toActorName': target_name,
                    'amount': amount,
                    'currency': currency,
                    'untrackedTarget': True,
                },
            )
        return _invalid(action, target_error or 'Transfer target was not found.')
    if str(target.get('id')) == str(actor.get('id')):
        return _invalid(action, 'Transfer target must be different from the source actor.')
    return _pending(
        action,
        f"{actor_name(actor)} can give {amount} {currency} to {actor_name(target)}.",
        normalized_action={
            **action,
            'fromActorId': actor.get('id'),
            'toActorId': target.get('id'),
            'toActorName': actor_name(target),
            'amount': amount,
            'currency': currency,
        },
    )


def _validate_attack(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
    recent_context: list[str],
    selected_item_id: str | None,
) -> dict[str, Any]:
    weapon_name = str(_action_value(action, 'weaponName', 'weapon_name') or '').strip()
    if not weapon_name:
        return _pending(action, 'Attack requires DM resolution.')
    actor, item, resolution = _resolve_action_item(
        action=action,
        state=state,
        item_name=weapon_name,
        requested_type='weapon',
        current_turn=current_turn,
        recent_context=recent_context,
        selected_item_id=selected_item_id,
    )
    if not actor:
        return _invalid(action, resolution.get('reason') or 'Actor not found.')
    if resolution.get('status') == 'needs_clarification':
        return _clarification(action, resolution)
    if resolution.get('status') == 'missing' or not item:
        return _invalid(action, f"{actor_name(actor)} does not have {weapon_name}.")
    mark_used = {
        'id': stable_change_id(current_turn, 'pre_dm', action.get('id'), 'inventory.mark_used', item.get('id')),
        'turnId': current_turn,
        'type': 'inventory.mark_used',
        'source': 'pre_dm',
        'actorId': actor.get('id'),
        'itemId': item.get('id'),
        'reason': f"{actor_name(actor)} used {item.get('name')} for an attack.",
        'visible': False,
    }
    return _pending(
        action,
        f"{actor_name(actor)} can attack with {item.get('name')}.",
        normalized_action={
            **action,
            'weaponId': item.get('id'),
            'weaponName': item.get('name'),
            'resolution': resolution,
        },
        required_rolls=[
            {
                'type': 'attack_roll',
                'actorId': actor.get('id'),
                'targetName': action.get('targetName') or action.get('target_name'),
                'weaponId': item.get('id'),
            }
        ],
    ) | {'immediateChanges': [mark_used]}


def validate_declared_actions(
    *,
    state: dict[str, Any],
    declared_actions: list[dict[str, Any]],
    current_turn: int,
    recent_context: list[str] | None = None,
    selected_item_ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    selected_item_ids = selected_item_ids or {}
    validated: list[dict[str, Any]] = []
    clarification_requests: list[dict[str, Any]] = []
    for action in declared_actions:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get('type') or '').strip()
        if action_type not in PRE_DM_ACTION_TYPES:
            validated.append(_invalid(action, f"Unsupported declared action type '{action_type}'."))
            continue
        selected_item_id = selected_item_ids.get(str(action.get('id')))
        if action_type == 'inventory.consume':
            result = _validate_consume_item(
                action,
                state,
                current_turn=current_turn,
                recent_context=recent_context or [],
                selected_item_id=selected_item_id,
            )
        elif action_type == 'inventory.use':
            result = _validate_use_or_transfer_item(
                action,
                state,
                current_turn=current_turn,
                recent_context=recent_context or [],
                selected_item_id=selected_item_id,
            )
        elif action_type == 'inventory.equip':
            result = _validate_equipment_action(
                action,
                state,
                current_turn=current_turn,
                recent_context=recent_context or [],
                selected_item_id=selected_item_id,
                equip=True,
            )
        elif action_type == 'inventory.unequip':
            result = _validate_equipment_action(
                action,
                state,
                current_turn=current_turn,
                recent_context=recent_context or [],
                selected_item_id=selected_item_id,
                equip=False,
            )
        elif action_type == 'inventory.transfer':
            result = _validate_inventory_transfer(
                action,
                state,
                current_turn=current_turn,
                recent_context=recent_context or [],
                selected_item_id=selected_item_id,
            )
        elif action_type == 'currency.transfer':
            result = _validate_currency_transfer(action, state)
        elif action_type == 'combat.attack':
            result = _validate_attack(
                action,
                state,
                current_turn=current_turn,
                recent_context=recent_context or [],
                selected_item_id=selected_item_id,
            )
        else:
            result = _pending(action, action.get('summary') or 'Player intent needs DM narration.')
        if result.get('status') == 'needs_clarification' and isinstance(result.get('clarificationRequest'), dict):
            clarification_requests.append(result['clarificationRequest'])
        validated.append(result)

    valid_summaries = []
    invalid_summaries = []
    pending_rolls = []
    immediate_changes = []
    for result in validated:
        status = result.get('status')
        reason = result.get('reason')
        original = result.get('originalAction') if isinstance(result.get('originalAction'), dict) else {}
        label = original.get('summary') or original.get('sourceText') or original.get('type')
        if status in {'valid', 'pending'}:
            valid_summaries.append(f"{label}: {reason}")
        elif status == 'invalid':
            invalid_summaries.append(f"{label}: {reason}")
        for roll in result.get('requiredRolls') or []:
            if isinstance(roll, dict):
                pending_rolls.append(roll)
        for change in result.get('immediateChanges') or []:
            if isinstance(change, dict):
                immediate_changes.append(change)

    summary_parts = []
    if valid_summaries:
        summary_parts.append('Allowed or pending: ' + '; '.join(valid_summaries))
    if invalid_summaries:
        summary_parts.append('Invalid: ' + '; '.join(invalid_summaries))

    return {
        'validatedActions': validated,
        'dmContextSummary': ' '.join(summary_parts).strip(),
        'pendingRolls': pending_rolls,
        'immediateChanges': immediate_changes,
        'clarificationRequests': clarification_requests,
    }


def _accepted(change: dict[str, Any], reason: str) -> dict[str, Any]:
    return {'change': change, 'reason': reason}


def _rejected(change: dict[str, Any], reason: str) -> dict[str, Any]:
    return {'change': change, 'reason': reason}


def _modified(original: dict[str, Any], modified: dict[str, Any], reason: str) -> dict[str, Any]:
    return {'originalChange': original, 'modifiedChange': modified, 'reason': reason}


def _validate_inventory_change(state: dict[str, Any], change: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Actor not found.', None
    quantity = max(0, int_or_default(change.get('quantity'), default=0))
    if quantity <= 0:
        return 'rejected', 'Inventory change quantity must be positive.', None
    if change.get('type') == 'inventory.add':
        item = change.get('item') if isinstance(change.get('item'), dict) else {}
        item_name = str(item.get('name') or change.get('itemName') or '').strip()
        if not item_name:
            return 'rejected', 'Inventory add requires an item name.', None
        return 'accepted', 'Inventory add is valid.', None
    item_id = _action_value(change, 'itemId', 'item_id')
    item_name = _action_value(change, 'itemName', 'item_name')
    item = None
    for candidate in actor_items(actor):
        if item_id and str(candidate.get('id')) == str(item_id):
            item = candidate
            break
        if item_name and normalize_item_name(candidate.get('name')) == normalize_item_name(item_name):
            item = candidate
            break
    if not item:
        return 'rejected', 'Item not found in inventory.', None
    if int_or_default(item.get('quantity'), default=1) < quantity:
        return 'rejected', f"Insufficient quantity. Available: {item.get('quantity')}.", None
    normalized = deepcopy(change)
    normalized['itemId'] = item.get('id')
    normalized['itemName'] = item.get('name')
    return 'accepted', 'Inventory remove is valid.', normalized


def _validate_equipment_change(state: dict[str, Any], change: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Actor not found.', None
    item_id = _action_value(change, 'itemId', 'item_id')
    item_name = _action_value(change, 'itemName', 'item_name')
    item = None
    for candidate in actor_items(actor):
        if item_id and str(candidate.get('id')) == str(item_id):
            item = candidate
            break
        if item_name and normalize_item_name(candidate.get('name')) == normalize_item_name(item_name):
            item = candidate
            break
    if not item:
        return 'rejected', 'Item not found in inventory.', None

    normalized = deepcopy(change)
    normalized['itemId'] = item.get('id')
    normalized['itemName'] = item.get('name')
    if str(change.get('type') or '') == 'inventory.unequip':
        return 'accepted', 'Equipment unequip is valid.', normalized

    if not is_equippable(item):
        return 'rejected', 'Item is not equippable.', None
    slot = infer_equipment_slot(
        item,
        requested_slot=_action_value(change, 'slot', 'equipment_slot'),
        equipped_items=actor_items(actor),
    )
    if not slot:
        return 'rejected', 'Equippable item does not have a valid slot.', None
    conflicts = conflict_items(actor_items(actor), item, slot)
    normalized['slot'] = slot
    normalized['conflictItemIds'] = [conflict.get('id') for conflict in conflicts if conflict.get('id')]
    normalized['conflictItemNames'] = [conflict.get('name') for conflict in conflicts if conflict.get('name')]
    return 'accepted', 'Equipment equip is valid.', normalized


def _validate_currency_change(state: dict[str, Any], change: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Actor not found.', None
    currency = str(change.get('currency') or '').strip().lower()
    amount = max(0, int_or_default(change.get('amount'), default=0))
    if currency not in CURRENCY_TYPES or amount <= 0:
        return 'rejected', 'Currency change requires a positive amount and valid denomination.', None
    if change.get('type') == 'currency.remove' and amount > actor_currency(actor).get(currency, 0):
        return 'rejected', f"Insufficient {currency}. Available: {actor_currency(actor).get(currency, 0)}.", None
    return 'accepted', 'Currency change is valid.', None


def _validate_health_change(state: dict[str, Any], change: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Actor not found.', None
    amount = max(0, int_or_default(change.get('amount'), default=0))
    if amount <= 0:
        return 'rejected', 'Health change amount must be positive.', None
    if change.get('type') == 'health.heal':
        health = actor.get('health') if isinstance(actor.get('health'), dict) else {}
        current_hp = max(0, int_or_default(health.get('currentHp'), default=0))
        max_hp = max(0, int_or_default(health.get('maxHp'), default=0))
        if max_hp and current_hp + amount > max_hp:
            modified = deepcopy(change)
            modified['amount'] = max(0, max_hp - current_hp)
            if modified['amount'] <= 0:
                return 'rejected', 'Healing has no effect because HP is already at maximum.', None
            return 'modified', 'Healing capped at max HP.', modified
    return 'accepted', 'Health change is valid.', None


def _validate_xp_change(state: dict[str, Any], change: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Actor not found.', None
    amount = max(0, int_or_default(change.get('amount'), default=0))
    if amount <= 0:
        return 'rejected', 'XP change amount must be positive.', None
    if change.get('type') == 'xp.remove':
        xp = actor.get('xp') if isinstance(actor.get('xp'), dict) else {}
        current_xp = max(0, int_or_default(xp.get('current'), default=0))
        if amount > current_xp:
            modified = deepcopy(change)
            modified['amount'] = current_xp
            if modified['amount'] <= 0:
                return 'rejected', 'XP loss has no effect because XP is already zero.', None
            return 'modified', 'XP loss capped at current XP.', modified
    return 'accepted', 'XP change is valid.', None


def _validate_spell_learn_change(state: dict[str, Any], change: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Actor not found.', None
    spell = spell_from_change(change)
    if not spell:
        return 'rejected', 'Spell learn requires a spell name.', None
    normalized = deepcopy(change)
    normalized['actorId'] = actor.get('id')
    normalized['spell'] = spell
    normalized['spellName'] = spell.get('name')
    normalized['spellLevel'] = spell.get('level')
    spellbook = normalize_spellbook(actor.get('spellbook') if isinstance(actor.get('spellbook'), dict) else {})
    known = {normalize_item_name(candidate.get('name')) for candidate in spellbook.get('knownSpells', []) if isinstance(candidate, dict)}
    if normalize_item_name(spell.get('name')) in known:
        normalized['alreadyKnown'] = True
        return 'accepted', 'Spell is already known.', normalized
    return 'accepted', 'Spell learn is valid.', normalized


def _text(value: Any) -> str:
    return str(value or '').strip()


def _stable_id(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return stable_slug(text)
    return ''


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _records(state: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [record for record in _list(state.get(key)) if isinstance(record, dict)]


def _find_record(records: list[dict[str, Any]], *, record_id: Any = None, name: Any = None, title: Any = None) -> dict[str, Any] | None:
    requested_id = _text(record_id)
    requested_name = normalize_item_name(name or title)
    for record in records:
        if requested_id and _text(record.get('id')) == requested_id:
            return record
    if requested_name:
        for record in records:
            record_name = normalize_item_name(record.get('name') or record.get('title'))
            if record_name == requested_name:
                return record
    return None


def _find_location(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    return _find_record(
        _records(state, 'locations'),
        record_id=change.get('locationId'),
        name=change.get('name') or change.get('locationName'),
    )


def _find_quest(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    return _find_record(_records(state, 'quests'), record_id=change.get('questId'), title=change.get('title') or change.get('name'))


def _find_npc(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    return _find_record(
        [*_records(state, 'knownNpcs'), *_records(state, 'partyNpcs')],
        record_id=change.get('npcId'),
        name=change.get('name') or change.get('npcName'),
    )


def _player_character_collision_label(state: dict[str, Any], change: dict[str, Any]) -> str | None:
    requested_id = _text(change.get('npcId') or change.get('id'))
    requested_name = normalize_item_name(change.get('name') or change.get('npcName'))
    for actor in _records(state, 'playerCharacters'):
        actor_name = _text(actor.get('name') or actor.get('characterName'))
        actor_player_id = _text(actor.get('playerId') or actor.get('player_id'))
        actor_ids = {
            _text(actor.get('id')),
            actor_player_id,
            f'player_{actor_player_id}' if actor_player_id else '',
            stable_slug(actor_name) if actor_name else '',
        }
        if requested_id and requested_id in {value for value in actor_ids if value}:
            return actor_name or requested_id
        if requested_name and actor_name and requested_name == normalize_item_name(actor_name):
            return actor_name
    return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _bounded_danger(value: Any) -> int | None:
    if value is None:
        return None
    parsed = int_or_default(value, default=-1)
    if parsed < 0:
        return None
    return max(0, min(10, parsed))


def _turn_value(change: dict[str, Any]) -> int | None:
    if change.get('turnId') is None and change.get('turn_id') is None:
        return None
    value = int_or_default(change.get('turnId', change.get('turn_id')), default=0)
    return value if value > 0 else None


def _normalize_world_change(change: dict[str, Any], state: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    change_type = str(change.get('type') or '').strip()
    normalized = deepcopy(change)
    turn_id = _turn_value(normalized)
    if turn_id is not None:
        normalized['turnId'] = turn_id

    if change_type == 'scene.update':
        scene_type = _text(normalized.get('sceneType'))
        mood = _text(normalized.get('mood'))
        combat_state = _text(normalized.get('combatState'))
        if scene_type and scene_type not in SCENE_TYPES:
            return 'rejected', f"Unsupported scene type '{scene_type}'.", None
        if mood and mood not in SCENE_MOODS:
            return 'rejected', f"Unsupported scene mood '{mood}'.", None
        if combat_state and combat_state not in COMBAT_STATES:
            return 'rejected', f"Unsupported combat state '{combat_state}'.", None
        if 'dangerLevel' in normalized:
            danger_level = _bounded_danger(normalized.get('dangerLevel'))
            if danger_level is None:
                return 'rejected', 'Scene dangerLevel must be a non-negative number.', None
            normalized['dangerLevel'] = danger_level
        if normalized.get('locationId') or normalized.get('name'):
            normalized['locationId'] = _stable_id(normalized.get('locationId'), normalized.get('name'))
        normalized['activeNpcIds'] = _string_list(normalized.get('activeNpcIds'))
        normalized['activeQuestIds'] = _string_list(normalized.get('activeQuestIds'))
        for key in ('playerPositions', 'playerZones', 'characterPositions', 'characterZones'):
            if key in normalized and not isinstance(normalized.get(key), dict):
                return 'rejected', f'Scene {key} must be an object.', None
        return 'accepted', 'Scene update is valid.', normalized

    if change_type == 'scene.move_location':
        location_id = _stable_id(normalized.get('locationId'), normalized.get('name') or normalized.get('locationName'))
        if not location_id:
            return 'rejected', 'Scene movement requires a location id or name.', None
        normalized['locationId'] = location_id
        if normalized.get('sceneType') and _text(normalized.get('sceneType')) not in SCENE_TYPES:
            return 'rejected', f"Unsupported scene type '{normalized.get('sceneType')}'.", None
        if normalized.get('mood') and _text(normalized.get('mood')) not in SCENE_MOODS:
            return 'rejected', f"Unsupported scene mood '{normalized.get('mood')}'.", None
        if normalized.get('combatState') and _text(normalized.get('combatState')) not in COMBAT_STATES:
            return 'rejected', f"Unsupported combat state '{normalized.get('combatState')}'.", None
        if 'dangerLevel' in normalized:
            danger_level = _bounded_danger(normalized.get('dangerLevel'))
            if danger_level is None:
                return 'rejected', 'Scene movement dangerLevel must be a non-negative number.', None
            normalized['dangerLevel'] = danger_level
        return 'accepted', 'Scene movement is valid.', normalized

    if change_type in {'location.discover', 'location.update'}:
        location_id = _stable_id(normalized.get('locationId'), normalized.get('name') or normalized.get('locationName'))
        if not location_id:
            return 'rejected', 'Location change requires a location id or name.', None
        normalized['locationId'] = location_id
        location = normalized.get('location') if isinstance(normalized.get('location'), dict) else {}
        location_type = _text(normalized.get('locationType') or location.get('type'))
        status = _text(normalized.get('status'))
        if location_type and location_type not in LOCATION_TYPES:
            return 'rejected', f"Unsupported location type '{location_type}'.", None
        if location_type:
            normalized['locationType'] = location_type
        if status and status not in LOCATION_STATUSES:
            return 'rejected', f"Unsupported location status '{status}'.", None
        if change_type == 'location.update' and not _find_location(state, normalized):
            return 'rejected', 'Location update target was not found.', None
        normalized['connectedLocationIds'] = [_stable_id(value) for value in _string_list(normalized.get('connectedLocationIds'))]
        normalized['npcIds'] = _string_list(normalized.get('npcIds'))
        normalized['questIds'] = _string_list(normalized.get('questIds'))
        normalized['tags'] = _string_list(normalized.get('tags'))
        return 'accepted', 'Location change is valid.', normalized

    if change_type == 'location.connect':
        normalized['locationId'] = _stable_id(normalized.get('locationId') or normalized.get('fromLocationId'), normalized.get('name'))
        normalized['connectedLocationId'] = _stable_id(
            normalized.get('connectedLocationId') or normalized.get('toLocationId'),
            normalized.get('connectedLocationName') or normalized.get('toLocationName'),
        )
        if not normalized['locationId'] or not normalized['connectedLocationId']:
            return 'rejected', 'Location connection requires two location ids or names.', None
        if normalized['locationId'] == normalized['connectedLocationId']:
            return 'rejected', 'Location connection targets must be different.', None
        return 'accepted', 'Location connection is valid.', normalized

    if change_type.startswith('quest.'):
        normalized['questId'] = _stable_id(normalized.get('questId'), normalized.get('title') or normalized.get('name'))
        if not normalized['questId']:
            return 'rejected', 'Quest change requires a quest id or title.', None
        status = _text(normalized.get('status'))
        if status and status not in QUEST_STATUSES:
            return 'rejected', f"Unsupported quest status '{status}'.", None
        if change_type != 'quest.add' and not _find_quest(state, normalized):
            return 'rejected', 'Quest update target was not found.', None
        if isinstance(normalized.get('objectives'), list):
            objectives = []
            for objective in normalized.get('objectives') or []:
                if not isinstance(objective, dict):
                    continue
                objective_status = _text(objective.get('status'))
                if objective_status and objective_status not in OBJECTIVE_STATUSES:
                    return 'rejected', f"Unsupported quest objective status '{objective_status}'.", None
                objective_id = _stable_id(objective.get('id') or objective.get('objectiveId'), objective.get('description'))
                if not objective_id and change_type in {'quest.add', 'quest.update'}:
                    return 'rejected', 'Quest objective requires an id or description.', None
                objectives.append({**objective, 'id': objective_id or objective.get('id')})
            normalized['objectives'] = objectives
        if change_type in {'quest.objective.add', 'quest.objective.update'}:
            objective = normalized.get('objective') if isinstance(normalized.get('objective'), dict) else {}
            objective_status = _text(objective.get('status') or normalized.get('objectiveStatus'))
            if objective_status and objective_status not in OBJECTIVE_STATUSES:
                return 'rejected', f"Unsupported quest objective status '{objective_status}'.", None
            objective_id = _stable_id(normalized.get('objectiveId'), objective.get('id') or objective.get('description'))
            if not objective_id:
                return 'rejected', 'Quest objective change requires an objective id or description.', None
            normalized['objectiveId'] = objective_id
        normalized['relatedNpcIds'] = _string_list(normalized.get('relatedNpcIds'))
        normalized['relatedLocationIds'] = [_stable_id(value) for value in _string_list(normalized.get('relatedLocationIds'))]
        normalized['importantItemIds'] = _string_list(normalized.get('importantItemIds'))
        return 'accepted', 'Quest change is valid.', normalized

    if change_type.startswith('npc.'):
        normalized['npcId'] = _stable_id(normalized.get('npcId'), normalized.get('name') or normalized.get('npcName'))
        if not normalized['npcId']:
            return 'rejected', 'NPC change requires an npc id or name.', None
        player_collision = _player_character_collision_label(state, normalized)
        if player_collision:
            return 'rejected', f"NPC change targets player character '{player_collision}'.", None
        disposition = _text(normalized.get('disposition'))
        status = _text(normalized.get('status'))
        if disposition and disposition not in NPC_DISPOSITIONS:
            return 'rejected', f"Unsupported NPC disposition '{disposition}'.", None
        if status and status not in NPC_STATUSES:
            return 'rejected', f"Unsupported NPC status '{status}'.", None
        if change_type != 'npc.discover' and not _find_npc(state, normalized):
            if change_type == 'npc.update' and _text(normalized.get('name') or normalized.get('npcName')):
                change_type = 'npc.discover'
                normalized['type'] = change_type
                return 'accepted', 'NPC update target was missing, so it will be applied as a discovery.', normalized
            return 'rejected', 'NPC update target was not found.', None
        if normalized.get('locationId'):
            normalized['locationId'] = _stable_id(normalized.get('locationId'))
        normalized['questIds'] = _string_list(normalized.get('questIds'))
        if change_type == 'npc.relationship.update':
            relationship = normalized.get('relationship') if isinstance(normalized.get('relationship'), dict) else {}
            score_value = normalized.get('relationshipScore', relationship.get('score'))
            if score_value is not None:
                try:
                    normalized['relationshipScore'] = max(-100, min(100, int(score_value)))
                except (TypeError, ValueError):
                    return 'rejected', 'NPC relationship score must be numeric.', None
            delta_value = normalized.get('scoreDelta')
            if delta_value is not None:
                try:
                    normalized['scoreDelta'] = max(-100, min(100, int(delta_value)))
                except (TypeError, ValueError):
                    return 'rejected', 'NPC relationship scoreDelta must be numeric.', None
            if normalized.get('relationshipLabel') is None and relationship.get('label') is not None:
                normalized['relationshipLabel'] = relationship.get('label')
        return 'accepted', 'NPC change is valid.', normalized

    if change_type == 'flag.set':
        if not _text(normalized.get('flagKey')):
            return 'rejected', 'Flag set requires flagKey.', None
        normalized['flagKey'] = stable_slug(normalized.get('flagKey'))
        return 'accepted', 'Flag set is valid.', normalized
    if change_type == 'flag.unset':
        if not _text(normalized.get('flagKey')):
            return 'rejected', 'Flag unset requires flagKey.', None
        normalized['flagKey'] = stable_slug(normalized.get('flagKey'))
        return 'accepted', 'Flag unset is valid.', normalized

    return 'rejected', 'Unsupported world state change.', None


def _combat_participant_ids(state: dict[str, Any]) -> set[str]:
    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    return {
        _text(participant.get('id'))
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and _text(participant.get('id'))
    }


def _combat_participant(state: dict[str, Any], participant_id: str) -> dict[str, Any] | None:
    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    for participant in combat.get('participants') or []:
        if isinstance(participant, dict) and _text(participant.get('id')) == participant_id:
            return participant
    return None


def _combat_start_reopens_resolved_enemy(state: dict[str, Any], combat: dict[str, Any], normalized: dict[str, Any]) -> str | None:
    if normalized.get('allowResolvedEncounterRestart') or normalized.get('allow_resolved_encounter_restart'):
        return None
    existing = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    if str(existing.get('status') or '') not in {'ended', 'none'} and str(scene.get('combatState') or '') not in {'resolved', 'none'}:
        return None
    resolved_signatures: set[str] = set()
    for participant in existing.get('participants') or []:
        if not isinstance(participant, dict) or participant.get('team') != 'enemy':
            continue
        conditions = {str(item or '').lower() for item in participant.get('conditions') or []}
        if not conditions.intersection({'surrendered', 'fled', 'defeated'}) and participant.get('isAlive') is not False:
            continue
        for value in (participant.get('definitionId'), participant.get('name'), participant.get('id')):
            text = stable_slug(value)
            if text:
                resolved_signatures.add(text)
    if not resolved_signatures:
        return None
    for participant in combat.get('participants') or []:
        if not isinstance(participant, dict) or participant.get('team') != 'enemy':
            continue
        for value in (participant.get('definitionId'), participant.get('name'), participant.get('id')):
            if stable_slug(value) in resolved_signatures:
                return str(participant.get('name') or participant.get('id') or 'enemy')
    return None


def _normalize_combat_change(change: dict[str, Any], state: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    change_type = _text(change.get('type'))
    normalized = deepcopy(change)
    turn_id = _turn_value(normalized)
    if turn_id is not None:
        normalized['turnId'] = turn_id

    if change_type == 'combat.start':
        combat_payload = normalized.get('combat') if isinstance(normalized.get('combat'), dict) else normalized
        combat = normalize_combat_state(
            {
                **combat_payload,
                'status': combat_payload.get('status') or 'active',
                'round': combat_payload.get('round') or 1,
            },
            state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {},
        )
        if not combat.get('participants'):
            return 'rejected', 'Combat start requires participants.', None
        if not any(participant.get('team') == 'enemy' for participant in combat['participants']):
            return 'rejected', 'Combat start requires at least one enemy participant.', None
        reopened_enemy = _combat_start_reopens_resolved_enemy(state, combat, normalized)
        if reopened_enemy:
            return 'rejected', f"Combat start would reopen resolved enemy '{reopened_enemy}'.", None
        normalized['combat'] = combat
        return 'accepted', 'Combat start is valid.', normalized

    if change_type == 'combat.update':
        status = _text(normalized.get('status'))
        if status and status not in {'none', 'starting', 'active', 'ended'}:
            return 'rejected', f"Unsupported combat status '{status}'.", None
        if normalized.get('round') is not None:
            round_number = int_or_default(normalized.get('round'), default=0)
            if round_number < 1:
                return 'rejected', 'Combat round must be positive.', None
            normalized['round'] = min(999, round_number)
        if normalized.get('flags') is not None and not isinstance(normalized.get('flags'), dict):
            return 'rejected', 'Combat flags must be an object.', None
        return 'accepted', 'Combat update is valid.', normalized

    if change_type == 'combat.round.advance':
        combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
        normalized['round'] = max(1, int_or_default(normalized.get('round'), default=int(combat.get('round') or 1) + 1))
        return 'accepted', 'Combat round advance is valid.', normalized

    if change_type == 'combat.battlefield.update':
        normalized['battlefield'] = normalize_battlefield(
            normalized.get('battlefield'),
            state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {},
        )
        return 'accepted', 'Combat battlefield update is valid.', normalized

    if change_type == 'combat.end':
        status = _text(normalized.get('status') or 'ended')
        if status not in {'ended', 'none'}:
            return 'rejected', 'Combat end status must be ended or none.', None
        normalized['status'] = status
        return 'accepted', 'Combat end is valid.', normalized

    participant_id = _text(normalized.get('participantId') or normalized.get('participant_id') or normalized.get('enemyId') or normalized.get('enemy_id'))
    if not participant_id:
        return 'rejected', 'Combat participant change requires participantId.', None
    if participant_id not in _combat_participant_ids(state):
        return 'rejected', f"Combat participant '{participant_id}' was not found.", None
    normalized['participantId'] = participant_id

    if change_type == 'combat.participant.update':
        if normalized.get('hp') is not None and not isinstance(normalized.get('hp'), dict):
            return 'rejected', 'Combat participant hp must be an object.', None
        if normalized.get('conditions') is not None:
            normalized['conditions'] = _string_list(normalized.get('conditions'))
        if normalized.get('position') is not None and not isinstance(normalized.get('position'), dict):
            return 'rejected', 'Combat participant position must be an object.', None
        if normalized.get('participant') is not None:
            participant = normalize_participant(normalized.get('participant'))
            if not participant:
                return 'rejected', 'Combat participant payload is invalid.', None
            normalized['participant'] = participant
        return 'accepted', 'Combat participant update is valid.', normalized

    if change_type == 'combat.move':
        to_range = _text(normalized.get('toRangeBand') or normalized.get('to_range_band') or normalized.get('rangeBand') or normalized.get('range_band')).lower().replace(' ', '_')
        if to_range not in RANGE_BANDS:
            return 'rejected', 'Combat movement requires a valid toRangeBand.', None
        normalized['toRangeBand'] = to_range
        from_range = _text(normalized.get('fromRangeBand') or normalized.get('from_range_band')).lower().replace(' ', '_')
        if from_range and from_range not in RANGE_BANDS:
            return 'rejected', 'Combat movement fromRangeBand is invalid.', None
        if from_range:
            normalized['fromRangeBand'] = from_range
        return 'accepted', 'Combat movement is valid.', normalized

    if change_type in {'combat.condition.add', 'combat.condition.remove'}:
        condition = _text(normalized.get('condition') or normalized.get('conditionName') or normalized.get('condition_name')).lower().replace(' ', '_')
        if not condition:
            return 'rejected', 'Combat condition change requires condition.', None
        normalized['condition'] = condition
        return 'accepted', 'Combat condition change is valid.', normalized

    if change_type == 'combat.ability.mark_used':
        ability_id = _text(normalized.get('abilityId') or normalized.get('ability_id'))
        if not ability_id:
            return 'rejected', 'Combat ability mark used requires abilityId.', None
        normalized['abilityId'] = ability_id
        return 'accepted', 'Combat ability mark used is valid.', normalized

    if change_type == 'combat.intent.set':
        intent = normalized.get('intent') if isinstance(normalized.get('intent'), dict) else {}
        intent_type = _text(intent.get('intentType') or normalized.get('intentType'))
        if not intent_type:
            return 'rejected', 'Combat intent requires intentType.', None
        try:
            confidence = float(intent.get('confidence', normalized.get('confidence', 0.5)) or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        normalized['intent'] = {
            **intent,
            'enemyId': participant_id,
            'intentType': intent_type,
            'reason': _text(intent.get('reason') or normalized.get('reason')) or 'Backend selected enemy intent.',
            'confidence': max(0.0, min(1.0, confidence)),
        }
        return 'accepted', 'Combat intent update is valid.', normalized

    if change_type == 'combat.morale.update':
        morale = int_or_default(normalized.get('morale'), default=-1)
        if morale < 0:
            return 'rejected', 'Combat morale update requires morale.', None
        normalized['morale'] = max(0, min(100, morale))
        return 'accepted', 'Combat morale update is valid.', normalized

    if change_type == 'combat.morale.event':
        event = _text(normalized.get('event') or normalized.get('moraleEvent') or normalized.get('morale_event')).lower()
        if event not in MORALE_EVENTS:
            return 'rejected', 'Combat morale event is unsupported.', None
        participant = _combat_participant(state, participant_id)
        if not participant:
            return 'rejected', f"Combat participant '{participant_id}' was not found.", None
        normalized['event'] = event
        normalized['morale'] = apply_morale_event(participant, event)
        return 'accepted', 'Combat morale event is valid.', normalized

    return 'rejected', 'Unsupported combat state change.', None


def _atomic_change_id(parent_id: str, suffix: str, *parts: Any) -> str:
    if parent_id:
        return f'{parent_id}:{suffix}'
    return stable_change_id('transfer', suffix, *parts)


def _transfer_source_actor(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    return find_actor(state, _action_value(change, 'fromActorId', 'from_actor_id') or _action_value(change, 'actorId', 'actor_id'))


def _change_id_already_seen(change: dict[str, Any], applied_ids: set[str], seen_ids: set[str]) -> bool:
    change_id = str(change.get('id') or '').strip()
    return bool(change_id and (change_id in applied_ids or change_id in seen_ids))


def _transfer_reason(change: dict[str, Any], fallback: str) -> str:
    reason = str(change.get('reason') or '').strip()
    if not reason or reason == GENERIC_EXTRACTED_REASON:
        return fallback
    return reason


def _validate_inventory_transfer_change(
    state: dict[str, Any],
    change: dict[str, Any],
    *,
    applied_ids: set[str],
    seen_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = _transfer_source_actor(state, change)
    if not source:
        return [], [_rejected(change, 'Transfer source actor not found.')]
    target, target_error = _target_actor_from_payload(state, change)
    if not target:
        return [], [_rejected(change, target_error or 'Transfer target actor not found.')]
    if str(source.get('id')) == str(target.get('id')):
        return [], [_rejected(change, 'Transfer target must be different from the source actor.')]

    item_id = _action_value(change, 'itemId', 'item_id')
    item_name = _action_value(change, 'itemName', 'item_name')
    source_item = None
    for candidate in actor_items(source):
        if item_id and str(candidate.get('id')) == str(item_id):
            source_item = candidate
            break
        if item_name and normalize_item_name(candidate.get('name')) == normalize_item_name(item_name):
            source_item = candidate
            break
    if not source_item:
        return [], [_rejected(change, 'Item not found in source inventory.')]

    quantity = max(0, int_or_default(change.get('quantity'), default=0))
    if quantity <= 0:
        return [], [_rejected(change, 'Inventory transfer quantity must be positive.')]

    parent_id = str(change.get('id') or '').strip()
    source_name = actor_name(source)
    target_name = actor_name(target)
    remove_change = {
        **change,
        'id': _atomic_change_id(parent_id, 'remove', source.get('id'), source_item.get('id'), quantity),
        'type': 'inventory.remove',
        'actorId': source.get('id'),
        'fromActorName': source_name,
        'toActorName': target_name,
        'itemId': source_item.get('id'),
        'itemName': source_item.get('name'),
        'quantity': quantity,
        'reason': _transfer_reason(change, f"{source_name} gave {source_item.get('name')} x{quantity} to {target_name}."),
        'transferId': parent_id or None,
        'transferDirection': 'source',
    }
    item_payload = deepcopy(source_item)
    item_payload['quantity'] = quantity
    add_change = {
        **change,
        'id': _atomic_change_id(parent_id, 'add', target.get('id'), source_item.get('id'), quantity),
        'type': 'inventory.add',
        'actorId': target.get('id'),
        'fromActorName': source_name,
        'toActorName': target_name,
        'itemId': source_item.get('id'),
        'itemName': source_item.get('name'),
        'quantity': quantity,
        'item': item_payload,
        'reason': _transfer_reason(change, f"{target_name} received {source_item.get('name')} x{quantity} from {source_name}."),
        'transferId': parent_id or None,
        'transferDirection': 'target',
    }
    if _change_id_already_seen(remove_change, applied_ids, seen_ids) or _change_id_already_seen(add_change, applied_ids, seen_ids):
        return [], [_rejected(change, 'State transfer was already applied.')]

    remove_status, remove_reason, normalized_remove = _validate_inventory_change(state, remove_change)
    add_status, add_reason, normalized_add = _validate_inventory_change(state, add_change)
    if remove_status != 'accepted' or add_status != 'accepted':
        reasons = [reason for status, reason in ((remove_status, remove_reason), (add_status, add_reason)) if status != 'accepted']
        return [], [_rejected(change, '; '.join(reasons) or 'Inventory transfer validation failed.')]

    accepted = [
        _accepted(normalized_remove or remove_change, 'Inventory transfer source removal is valid.'),
        _accepted(normalized_add or add_change, 'Inventory transfer target add is valid.'),
    ]
    for entry in accepted:
        atomic_id = str(entry['change'].get('id') or '').strip()
        if atomic_id:
            seen_ids.add(atomic_id)
    return accepted, []


def _validate_currency_transfer_change(
    state: dict[str, Any],
    change: dict[str, Any],
    *,
    applied_ids: set[str],
    seen_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = _transfer_source_actor(state, change)
    if not source:
        return [], [_rejected(change, 'Transfer source actor not found.')]
    target, target_error = _target_actor_from_payload(state, change)
    if not target:
        return [], [_rejected(change, target_error or 'Transfer target actor not found.')]
    if str(source.get('id')) == str(target.get('id')):
        return [], [_rejected(change, 'Transfer target must be different from the source actor.')]

    currency = str(change.get('currency') or '').strip().lower()
    amount = max(0, int_or_default(change.get('amount'), default=0))
    parent_id = str(change.get('id') or '').strip()
    source_name = actor_name(source)
    target_name = actor_name(target)
    remove_change = {
        **change,
        'id': _atomic_change_id(parent_id, 'remove', source.get('id'), currency, amount),
        'type': 'currency.remove',
        'actorId': source.get('id'),
        'fromActorName': source_name,
        'toActorName': target_name,
        'currency': currency,
        'amount': amount,
        'reason': _transfer_reason(change, f"{source_name} gave {amount} {currency} to {target_name}."),
        'transferId': parent_id or None,
        'transferDirection': 'source',
    }
    add_change = {
        **change,
        'id': _atomic_change_id(parent_id, 'add', target.get('id'), currency, amount),
        'type': 'currency.add',
        'actorId': target.get('id'),
        'fromActorName': source_name,
        'toActorName': target_name,
        'currency': currency,
        'amount': amount,
        'reason': _transfer_reason(change, f"{target_name} received {amount} {currency} from {source_name}."),
        'transferId': parent_id or None,
        'transferDirection': 'target',
    }
    if _change_id_already_seen(remove_change, applied_ids, seen_ids) or _change_id_already_seen(add_change, applied_ids, seen_ids):
        return [], [_rejected(change, 'State transfer was already applied.')]

    remove_status, remove_reason, normalized_remove = _validate_currency_change(state, remove_change)
    add_status, add_reason, normalized_add = _validate_currency_change(state, add_change)
    if remove_status != 'accepted' or add_status != 'accepted':
        reasons = [reason for status, reason in ((remove_status, remove_reason), (add_status, add_reason)) if status != 'accepted']
        return [], [_rejected(change, '; '.join(reasons) or 'Currency transfer validation failed.')]

    accepted = [
        _accepted(normalized_remove or remove_change, 'Currency transfer source removal is valid.'),
        _accepted(normalized_add or add_change, 'Currency transfer target add is valid.'),
    ]
    for entry in accepted:
        atomic_id = str(entry['change'].get('id') or '').strip()
        if atomic_id:
            seen_ids.add(atomic_id)
    return accepted, []


def validate_state_changes(*, state: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    modified: list[dict[str, Any]] = []
    applied_ids = state_applied_change_ids(state)
    seen_ids: set[str] = set()

    for raw_change in changes:
        if not isinstance(raw_change, dict):
            continue
        change = deepcopy(raw_change)
        change_type = str(change.get('type') or '').strip()
        change_id = str(change.get('id') or '').strip()
        if change_type not in STATE_CHANGE_TYPES:
            rejected.append(_rejected(change, f"Unsupported state change type '{change_type}'."))
            continue
        if change_type == 'inventory.transfer':
            transfer_accepted, transfer_rejected = _validate_inventory_transfer_change(
                state,
                change,
                applied_ids=applied_ids,
                seen_ids=seen_ids,
            )
            accepted.extend(transfer_accepted)
            rejected.extend(transfer_rejected)
            continue
        if change_type == 'currency.transfer':
            transfer_accepted, transfer_rejected = _validate_currency_transfer_change(
                state,
                change,
                applied_ids=applied_ids,
                seen_ids=seen_ids,
            )
            accepted.extend(transfer_accepted)
            rejected.extend(transfer_rejected)
            continue
        if change_id and (change_id in applied_ids or change_id in seen_ids):
            rejected.append(_rejected(change, 'State change was already applied.'))
            continue
        if change_id:
            seen_ids.add(change_id)

        if change_type in {'inventory.add', 'inventory.remove'}:
            status, reason, normalized = _validate_inventory_change(state, change)
        elif change_type in {'inventory.equip', 'inventory.unequip'}:
            status, reason, normalized = _validate_equipment_change(state, change)
        elif change_type in {'currency.add', 'currency.remove'}:
            status, reason, normalized = _validate_currency_change(state, change)
        elif change_type in {'health.heal', 'health.damage'}:
            status, reason, normalized = _validate_health_change(state, change)
        elif change_type in {'xp.add', 'xp.remove'}:
            status, reason, normalized = _validate_xp_change(state, change)
        elif change_type == 'spell.learn':
            status, reason, normalized = _validate_spell_learn_change(state, change)
        elif change_type == 'inventory.mark_used':
            status, reason, normalized = 'accepted', 'Inventory use marker is valid.', None
        elif change_type == 'race_ability.mark_used':
            ability_id = str(change.get('abilityId') or change.get('ability_id') or '').strip()
            refreshes_on = str(change.get('refreshesOn') or change.get('refreshes_on') or '').strip()
            if not change.get('actorId') and not change.get('actor_id'):
                status, reason, normalized = 'rejected', 'Race ability use requires actorId.'
            elif not ability_id:
                status, reason, normalized = 'rejected', 'Race ability use requires abilityId.'
            elif refreshes_on not in {'short_rest', 'long_rest', 'session'}:
                status, reason, normalized = 'rejected', 'Race ability use requires a supported refreshesOn value.'
            else:
                status, reason, normalized = 'accepted', 'Race ability use marker is valid.', None
        elif change_type == 'race_ability.refresh':
            ability_id = str(change.get('abilityId') or change.get('ability_id') or '').strip()
            if not change.get('actorId') and not change.get('actor_id'):
                status, reason, normalized = 'rejected', 'Race ability refresh requires actorId.'
            elif not ability_id:
                status, reason, normalized = 'rejected', 'Race ability refresh requires abilityId.'
            else:
                status, reason, normalized = 'accepted', 'Race ability refresh marker is valid.', None
        elif change_type in COMBAT_STATE_CHANGE_TYPES:
            status, reason, normalized = _normalize_combat_change(change, state)
        elif change_type in WORLD_STATE_CHANGE_TYPES:
            status, reason, normalized = _normalize_world_change(change, state)
        else:
            status, reason, normalized = 'rejected', 'State pipeline does not apply this change directly.', None

        if status == 'accepted':
            accepted.append(_accepted(normalized or change, reason))
        elif status == 'modified' and normalized:
            modified.append(_modified(change, normalized, reason))
        else:
            rejected.append(_rejected(change, reason))

    return {'accepted': accepted, 'rejected': rejected, 'modified': modified}


def validated_changes_for_application(validation_result: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for entry in validation_result.get('accepted') or []:
        if isinstance(entry, dict) and isinstance(entry.get('change'), dict):
            changes.append(entry['change'])
    for entry in validation_result.get('modified') or []:
        if isinstance(entry, dict) and isinstance(entry.get('modifiedChange'), dict):
            changes.append(entry['modifiedChange'])
    return changes
