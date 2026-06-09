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
    find_actor_by_name,
    normalize_item_name,
    stable_change_id,
    state_applied_change_ids,
)
from aidm_server.game_state.validation.inventory_validator import resolve_inventory_item_reference


CONSUMABLE_TYPES = {'consumable', 'potion', 'food'}
UNRESOLVED_TARGET_LABELS = {'', 'target', 'someone', 'somebody', 'an npc', 'a npc', 'npc'}
GENERIC_EXTRACTED_REASON = 'Extracted from DM response.'


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
        if change_type not in PHASE_1_STATE_CHANGE_TYPES:
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
        elif change_type in {'currency.add', 'currency.remove'}:
            status, reason, normalized = _validate_currency_change(state, change)
        elif change_type in {'health.heal', 'health.damage'}:
            status, reason, normalized = _validate_health_change(state, change)
        elif change_type in {'xp.add', 'xp.remove'}:
            status, reason, normalized = _validate_xp_change(state, change)
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
