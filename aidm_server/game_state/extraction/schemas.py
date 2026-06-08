from __future__ import annotations

import json
import re
from typing import Any

from aidm_server.game_state.action_types import PRE_DM_ACTION_TYPES
from aidm_server.game_state.change_types import PHASE_1_STATE_CHANGE_TYPES

GENERIC_INTENT_SAFE_FIELDS = {'summary', 'intentDescription', 'intent_description', 'description', 'sourceText', 'source_text'}
POSITIVE_INT_FIELDS = {'quantity', 'amount'}
WEIGHT_FIELDS = ('weight', 'itemWeight', 'item_weight', 'weightLbs', 'weight_lbs')
_MISSING = object()


def extract_json_object(text: str | None) -> dict[str, Any] | None:
    candidate = str(text or '').strip()
    if not candidate:
        return None
    try:
        loaded = json.loads(candidate)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{.*\}', candidate, re.DOTALL)
    if not match:
        return None
    try:
        loaded = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _value(payload: dict[str, Any], camel_key: str, snake_key: str | None = None, default=None):
    if camel_key in payload:
        return payload.get(camel_key)
    if snake_key and snake_key in payload:
        return payload.get(snake_key)
    return default


def _first_present(payload: dict[str, Any], keys: tuple[str, ...]):
    for key in keys:
        if key in payload:
            return payload.get(key)
    return _MISSING


def _positive_number(value: Any) -> float | int | None:
    if isinstance(value, str):
        match = re.search(r'-?\d+(?:\.\d+)?', value)
        if not match:
            return None
        value = match.group(0)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return int(number) if number.is_integer() else number


def normalize_declared_action(raw_action: Any, *, fallback_actor_id: str, fallback_id: str) -> dict[str, Any] | None:
    if not isinstance(raw_action, dict):
        return None
    action_type = str(raw_action.get('type') or '').strip()
    if action_type not in PRE_DM_ACTION_TYPES:
        if not any(str(raw_action.get(key) or '').strip() for key in GENERIC_INTENT_SAFE_FIELDS):
            return None
        action_type = 'generic.intent'
    for field in POSITIVE_INT_FIELDS:
        if field in raw_action:
            try:
                if int(raw_action.get(field)) <= 0:
                    return None
            except (TypeError, ValueError):
                return None
    action = {
        'id': str(raw_action.get('id') or fallback_id),
        'type': action_type,
        'actorId': str(_value(raw_action, 'actorId', 'actor_id', fallback_actor_id) or fallback_actor_id),
        'confidence': max(0.0, min(1.0, float(raw_action.get('confidence') or 0.5))),
        'sourceText': str(_value(raw_action, 'sourceText', 'source_text', '') or ''),
        'requiresDMResolution': bool(_value(raw_action, 'requiresDMResolution', 'requires_dm_resolution', True)),
    }
    for key in (
        'itemName',
        'item_name',
        'targetId',
        'target_id',
        'intendedUse',
        'intended_use',
        'targetName',
        'target_name',
        'weaponName',
        'weapon_name',
        'attackStyle',
        'attack_style',
        'fromActorId',
        'from_actor_id',
        'toActorId',
        'to_actor_id',
        'toActorName',
        'to_actor_name',
        'summary',
        'currency',
    ):
        if key in raw_action:
            camel = ''.join([key.split('_')[0], *[part[:1].upper() + part[1:] for part in key.split('_')[1:]]])
            action[camel] = raw_action[key]
    if 'quantity' in raw_action:
        try:
            action['quantity'] = max(1, int(raw_action.get('quantity') or 1))
        except (TypeError, ValueError):
            action['quantity'] = 1
    if 'amount' in raw_action:
        try:
            action['amount'] = max(1, int(raw_action.get('amount') or 1))
        except (TypeError, ValueError):
            action['amount'] = 1
    if not action.get('summary'):
        summary = (
            raw_action.get('summary')
            or raw_action.get('intentDescription')
            or raw_action.get('intent_description')
            or raw_action.get('description')
        )
        if summary:
            action['summary'] = str(summary)
    if not _declared_action_has_required_fields(action):
        return None
    return action


def _declared_action_has_required_fields(action: dict[str, Any]) -> bool:
    if not action.get('id') or not action.get('actorId') or not action.get('sourceText'):
        return False
    action_type = str(action.get('type') or '')
    if action_type in {'inventory.consume', 'inventory.use', 'inventory.transfer'}:
        return bool(str(action.get('itemName') or '').strip()) and 'quantity' in action
    if action_type == 'currency.transfer':
        return bool(str(action.get('currency') or '').strip() and action.get('amount'))
    if action_type == 'combat.attack':
        return bool(str(action.get('weaponName') or '').strip())
    if action_type == 'generic.intent':
        return bool(str(action.get('summary') or action.get('sourceText') or '').strip())
    return False


def normalize_pre_extraction(raw_payload: dict[str, Any] | None, *, fallback_actor_id: str) -> dict[str, Any]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    raw_actions = payload.get('declaredActions') or payload.get('declared_actions') or []
    actions: list[dict[str, Any]] = []
    if isinstance(raw_actions, list):
        for index, raw_action in enumerate(raw_actions, start=1):
            action = normalize_declared_action(
                raw_action,
                fallback_actor_id=fallback_actor_id,
                fallback_id=f'act_{index:03d}',
            )
            if action:
                actions.append(action)
    notes = _normalize_notes(payload.get('notes'))
    return {'declaredActions': actions, 'notes': notes}


def _normalize_notes(raw_notes: Any) -> list[str]:
    if isinstance(raw_notes, list):
        return [str(note) for note in raw_notes if str(note).strip()]
    if isinstance(raw_notes, str) and raw_notes.strip():
        return [raw_notes.strip()]
    return []


def normalize_state_change(raw_change: Any, *, fallback_actor_id: str, fallback_id: str, source: str) -> dict[str, Any] | None:
    if not isinstance(raw_change, dict):
        return None
    change_type = str(raw_change.get('type') or '').strip()
    if change_type not in PHASE_1_STATE_CHANGE_TYPES:
        return None
    for field in POSITIVE_INT_FIELDS:
        if field in raw_change:
            try:
                if int(raw_change.get(field)) <= 0:
                    return None
            except (TypeError, ValueError):
                return None
    change = dict(raw_change)
    change['id'] = str(raw_change.get('id') or fallback_id)
    change['type'] = change_type
    change['source'] = str(raw_change.get('source') or source)
    change['actorId'] = str(
        _value(raw_change, 'actorId', 'actor_id', None)
        or raw_change.get('target')
        or raw_change.get('targetId')
        or raw_change.get('target_id')
        or fallback_actor_id
    )
    change['visible'] = bool(raw_change.get('visible', True))
    change['reason'] = str(raw_change.get('reason') or 'Extracted from DM response.')
    if 'item_name' in change and 'itemName' not in change:
        change['itemName'] = change.pop('item_name')
    if 'item_id' in change and 'itemId' not in change:
        change['itemId'] = change.pop('item_id')
    if 'from_actor_id' in change and 'fromActorId' not in change:
        change['fromActorId'] = change.pop('from_actor_id')
    if 'to_actor_id' in change and 'toActorId' not in change:
        change['toActorId'] = change.pop('to_actor_id')
    if 'to_actor_name' in change and 'toActorName' not in change:
        change['toActorName'] = change.pop('to_actor_name')
    if 'amount' in change:
        try:
            change['amount'] = max(1, int(change.get('amount') or 1))
        except (TypeError, ValueError):
            return None
    if 'quantity' in change:
        try:
            change['quantity'] = max(1, int(change.get('quantity') or 1))
        except (TypeError, ValueError):
            return None
    raw_weight = _first_present(change, WEIGHT_FIELDS)
    if raw_weight is not _MISSING:
        weight = _positive_number(raw_weight)
        if weight is None:
            return None
        change['weight'] = weight
    if change_type == 'inventory.add':
        raw_item = change.get('item')
        if isinstance(raw_item, dict):
            item = dict(raw_item)
        elif isinstance(raw_item, str) and raw_item.strip():
            item = {'name': raw_item.strip()}
        else:
            item = {}
        if not item and change.get('itemName'):
            item = {'name': change.get('itemName')}
        if item:
            raw_item_quantity = item.get('quantity', _MISSING)
            if raw_item_quantity is not _MISSING:
                try:
                    item['quantity'] = max(1, int(raw_item_quantity or 1))
                except (TypeError, ValueError):
                    return None
                if 'quantity' not in change:
                    change['quantity'] = item['quantity']
            elif 'quantity' in change:
                item['quantity'] = change['quantity']

            raw_item_weight = _first_present(item, WEIGHT_FIELDS)
            if raw_item_weight is not _MISSING:
                weight = _positive_number(raw_item_weight)
                if weight is None:
                    return None
                item['weight'] = weight
            elif change.get('weight') is not None:
                item['weight'] = change['weight']
        change['item'] = item
        if item.get('name') and not change.get('itemName'):
            change['itemName'] = item.get('name')
    if not _state_change_has_required_fields(change):
        return None
    return change


def _state_change_has_required_fields(change: dict[str, Any]) -> bool:
    change_type = str(change.get('type') or '').strip()
    if not change.get('id') or not change.get('actorId'):
        return False
    if change_type == 'inventory.add':
        item = change.get('item') if isinstance(change.get('item'), dict) else {}
        return bool(str(change.get('itemName') or item.get('name') or '').strip()) and 'quantity' in change
    if change_type == 'inventory.remove':
        return bool(str(change.get('itemId') or change.get('itemName') or '').strip()) and 'quantity' in change
    if change_type == 'inventory.transfer':
        return (
            bool(str(change.get('itemId') or change.get('itemName') or '').strip())
            and 'quantity' in change
            and bool(str(change.get('toActorId') or change.get('toActorName') or '').strip())
        )
    if change_type in {'currency.add', 'currency.remove'}:
        return bool(str(change.get('currency') or '').strip()) and 'amount' in change
    if change_type == 'currency.transfer':
        return (
            bool(str(change.get('currency') or '').strip())
            and 'amount' in change
            and bool(str(change.get('toActorId') or change.get('toActorName') or '').strip())
        )
    if change_type in {'health.heal', 'health.damage'}:
        return 'amount' in change
    if change_type == 'inventory.mark_used':
        return bool(str(change.get('itemId') or '').strip())
    return False


def normalize_post_extraction(raw_payload: dict[str, Any] | None, *, fallback_actor_id: str) -> dict[str, Any]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    raw_changes = payload.get('proposedChanges') or payload.get('proposed_changes') or []
    changes: list[dict[str, Any]] = []
    if isinstance(raw_changes, list):
        for index, raw_change in enumerate(raw_changes, start=1):
            change = normalize_state_change(
                raw_change,
                fallback_actor_id=fallback_actor_id,
                fallback_id=f'post_chg_{index:03d}',
                source='post_dm',
            )
            if change:
                changes.append(change)
    uncertain = payload.get('uncertainChanges') or payload.get('uncertain_changes') or []
    notes = _normalize_notes(payload.get('notes'))
    return {
        'proposedChanges': changes,
        'uncertainChanges': uncertain if isinstance(uncertain, list) else [],
        'notes': notes,
    }
