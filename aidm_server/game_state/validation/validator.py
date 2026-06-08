from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.game_state.action_types import PRE_DM_ACTION_TYPES
from aidm_server.game_state.change_types import CURRENCY_TYPES, PHASE_1_STATE_CHANGE_TYPES
from aidm_server.game_state.models import (
    actor_currency,
    actor_items,
    actor_name,
    find_actor,
    normalize_item_name,
    stable_change_id,
    state_applied_change_ids,
)
from aidm_server.game_state.validation.inventory_validator import resolve_inventory_item_reference


CONSUMABLE_TYPES = {'consumable', 'potion', 'food'}


def _action_value(action: dict[str, Any], camel_key: str, snake_key: str | None = None, default=None):
    if camel_key in action:
        return action.get(camel_key)
    if snake_key and snake_key in action:
        return action.get(snake_key)
    return default


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


def _validate_currency_transfer(action: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    actor = find_actor(state, _action_value(action, 'actorId', 'actor_id') or _action_value(action, 'fromActorId', 'from_actor_id'))
    if not actor:
        return _invalid(action, 'Actor not found.')
    currency = str(action.get('currency') or '').strip().lower()
    amount = max(0, int_or_default(action.get('amount'), default=0))
    if currency not in CURRENCY_TYPES or amount <= 0:
        return _invalid(action, 'Currency transfer requires a positive amount and valid denomination.')
    available = actor_currency(actor).get(currency, 0)
    if amount > available:
        return _invalid(action, f"{actor_name(actor)} has {available} {currency} and cannot transfer {amount}.")
    return _pending(action, f"{actor_name(actor)} has {available} {currency}.")


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
        elif action_type in {'inventory.use', 'inventory.transfer'}:
            result = _validate_use_or_transfer_item(
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
        label = original.get('sourceText') or original.get('summary') or original.get('type')
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
        if change_type not in PHASE_1_STATE_CHANGE_TYPES:
            rejected.append(_rejected(change, f"Unsupported state change type '{change_type}'."))
            continue
        if change_id and (change_id in applied_ids or change_id in seen_ids):
            rejected.append(_rejected(change, 'State change was already applied.'))
            continue
        if change_id:
            seen_ids.add(change_id)

        if change_type in {'inventory.add', 'inventory.remove'}:
            status, reason, normalized = _validate_inventory_change(state, change)
        elif change_type in {'currency.add', 'currency.remove'}:
            status, reason, normalized = _validate_currency_change(state, change)
        elif change_type in {'health.heal', 'health.damage'}:
            status, reason, normalized = _validate_health_change(state, change)
        elif change_type == 'inventory.mark_used':
            status, reason, normalized = 'accepted', 'Inventory use marker is valid.', None
        else:
            status, reason, normalized = 'rejected', 'Phase 1 does not apply this change directly.', None

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

