from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.game_state.models import (
    CURRENCY_CODES,
    CURRENCY_STAT_KEYS,
    actor_currency,
    actor_items,
    append_change_ledger,
    dump_inventory_items,
    find_actor,
    normalize_item_name,
    parse_actor_player_id,
    stable_item_id,
    stats_with_currency,
)
from aidm_server.models import Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.time_utils import utc_now


def _change_value(change: dict[str, Any], camel_key: str, snake_key: str | None = None, default=None):
    if camel_key in change:
        return change.get(camel_key)
    if snake_key and snake_key in change:
        return change.get(snake_key)
    return default


def _find_item(items: list[dict[str, Any]], *, item_id: str | None = None, item_name: str | None = None) -> dict[str, Any] | None:
    if item_id:
        exact = next((item for item in items if str(item.get('id')) == str(item_id)), None)
        if exact:
            return exact
    requested = normalize_item_name(item_name)
    if requested:
        return next((item for item in items if normalize_item_name(item.get('name')) == requested), None)
    return None


def _item_payload(change: dict[str, Any]) -> dict[str, Any]:
    raw_item = change.get('item') if isinstance(change.get('item'), dict) else {}
    name = str(raw_item.get('name') or change.get('itemName') or change.get('item_name') or '').strip()
    quantity = max(1, int_or_default(raw_item.get('quantity', change.get('quantity')), default=1))
    item_id = str(raw_item.get('id') or raw_item.get('itemId') or change.get('itemId') or stable_item_id(name)).strip()
    return {
        **raw_item,
        'id': item_id,
        'name': name,
        'quantity': quantity,
        'type': raw_item.get('type') or change.get('itemType') or change.get('item_type') or 'misc',
    }


def _merge_item(items: list[dict[str, Any]], incoming: dict[str, Any]) -> dict[str, Any]:
    existing = _find_item(items, item_id=str(incoming.get('id')), item_name=str(incoming.get('name')))
    if existing:
        existing['quantity'] = max(0, int_or_default(existing.get('quantity'), default=0)) + max(
            1,
            int_or_default(incoming.get('quantity'), default=1),
        )
        for key, value in incoming.items():
            if key not in {'quantity'} and value not in (None, '', [], {}):
                existing.setdefault(key, value)
        return existing
    items.append(incoming)
    return incoming


def _remove_item(items: list[dict[str, Any]], change: dict[str, Any]) -> dict[str, Any] | None:
    item = _find_item(
        items,
        item_id=_change_value(change, 'itemId', 'item_id'),
        item_name=_change_value(change, 'itemName', 'item_name'),
    )
    if not item:
        return None
    quantity = max(1, int_or_default(change.get('quantity'), default=1))
    item['quantity'] = max(0, int_or_default(item.get('quantity'), default=1) - quantity)
    if item['quantity'] <= 0:
        items.remove(item)
    return item


def _apply_currency(actor: dict[str, Any], change: dict[str, Any], direction: int) -> int:
    currency_code = str(change.get('currency') or '').strip().lower()
    if currency_code not in CURRENCY_CODES:
        return 0
    amount = max(0, int_or_default(change.get('amount'), default=0))
    inventory = actor.setdefault('inventory', {})
    currency = inventory.setdefault('currency', {})
    current = max(0, int_or_default(currency.get(currency_code), default=0))
    if direction < 0:
        actual = min(current, amount)
        currency[currency_code] = current - actual
        return -actual
    currency[currency_code] = current + amount
    return amount


def _apply_health_heal(actor: dict[str, Any], change: dict[str, Any]) -> int:
    amount = max(0, int_or_default(change.get('amount'), default=0))
    health = actor.setdefault('health', {})
    current_hp = max(0, int_or_default(health.get('currentHp'), default=0))
    max_hp = max(0, int_or_default(health.get('maxHp'), default=0))
    if max_hp:
        actual = max(0, min(amount, max_hp - current_hp))
        health['currentHp'] = min(max_hp, current_hp + amount)
    else:
        actual = amount
        health['currentHp'] = current_hp + amount
    return actual


def _apply_health_damage(actor: dict[str, Any], change: dict[str, Any]) -> dict[str, int]:
    amount = max(0, int_or_default(change.get('amount'), default=0))
    health = actor.setdefault('health', {})
    current_hp = max(0, int_or_default(health.get('currentHp'), default=0))
    temp_hp = max(0, int_or_default(health.get('tempHp'), default=0))
    temp_damage = min(temp_hp, amount)
    remaining = amount - temp_damage
    hp_damage = min(current_hp, remaining)
    health['tempHp'] = temp_hp - temp_damage
    health['currentHp'] = current_hp - hp_damage
    return {'amount': temp_damage + hp_damage, 'tempHpDamage': temp_damage, 'hpDamage': hp_damage}


def apply_state_changes(previous_state: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    next_state = deepcopy(previous_state)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_ids = {str(entry.get('id')) for entry in next_state.get('stateChangeLedger', []) if isinstance(entry, dict)}

    for raw_change in changes:
        if not isinstance(raw_change, dict):
            continue
        change = deepcopy(raw_change)
        change_id = str(change.get('id') or '').strip()
        if change_id and change_id in seen_ids:
            skipped.append({'change': change, 'reason': 'State change was already applied.'})
            continue

        change_type = str(change.get('type') or '').strip()
        actor_id = _change_value(change, 'actorId', 'actor_id')
        actor = find_actor(next_state, actor_id) if actor_id is not None else None
        applied_change = deepcopy(change)
        applied_change['actualAmount'] = None

        if change_type == 'inventory.add' and actor:
            inventory = actor.setdefault('inventory', {})
            items = inventory.setdefault('items', [])
            item = _merge_item(items, _item_payload(change))
            applied_change['itemId'] = item.get('id')
            applied_change['itemName'] = item.get('name')
            applied_change['actualAmount'] = max(1, int_or_default(change.get('quantity', item.get('quantity')), default=1))
        elif change_type == 'inventory.remove' and actor:
            removed = _remove_item(actor_items(actor), change)
            applied_change['itemId'] = _change_value(change, 'itemId', 'item_id') or (removed or {}).get('id')
            applied_change['itemName'] = _change_value(change, 'itemName', 'item_name') or (removed or {}).get('name')
            applied_change['actualAmount'] = max(1, int_or_default(change.get('quantity'), default=1))
        elif change_type == 'inventory.mark_used' and actor:
            item = _find_item(actor_items(actor), item_id=_change_value(change, 'itemId', 'item_id'))
            if item:
                item['lastUsedAtTurn'] = change.get('turnId') or change.get('turn_id') or item.get('lastUsedAtTurn')
                applied_change['itemName'] = item.get('name')
        elif change_type == 'currency.add' and actor:
            applied_change['actualAmount'] = _apply_currency(actor, change, 1)
        elif change_type == 'currency.remove' and actor:
            applied_change['actualAmount'] = abs(_apply_currency(actor, change, -1))
        elif change_type == 'health.heal' and actor:
            applied_change['actualAmount'] = _apply_health_heal(actor, change)
        elif change_type == 'health.damage' and actor:
            result = _apply_health_damage(actor, change)
            applied_change.update(result)
            applied_change['actualAmount'] = result['amount']
        else:
            skipped.append({'change': change, 'reason': 'Unsupported change or actor missing during application.'})
            continue

        applied.append(applied_change)
        if change_id:
            seen_ids.add(change_id)
            append_change_ledger(next_state, applied_change)

    next_state['lastUpdatedAt'] = utc_now().isoformat()
    return {'nextState': next_state, 'appliedChanges': applied, 'skippedChanges': skipped}


def persist_state_to_database(
    *,
    session_obj: Session,
    state: dict[str, Any],
    players_by_id: dict[int, Player],
) -> None:
    for actor in state.get('playerCharacters') or []:
        if not isinstance(actor, dict):
            continue
        player_id = parse_actor_player_id(actor.get('id')) or actor.get('playerId')
        player = players_by_id.get(int(player_id)) if player_id else None
        if not player:
            continue

        inventory = actor.get('inventory') if isinstance(actor.get('inventory'), dict) else {}
        items = inventory.get('items') if isinstance(inventory.get('items'), list) else []
        player.inventory = dump_inventory_items(items)

        stats = safe_json_loads(player.stats, {})
        stats = stats if isinstance(stats, dict) else {}
        currency = actor_currency(actor)
        stats = stats_with_currency(stats, currency)
        health = actor.get('health') if isinstance(actor.get('health'), dict) else {}
        if health:
            current_hp = max(0, int_or_default(health.get('currentHp'), default=0))
            max_hp = max(0, int_or_default(health.get('maxHp'), default=0))
            temp_hp = max(0, int_or_default(health.get('tempHp'), default=0))
            stats['current_hp'] = current_hp
            stats['hp_current'] = current_hp
            if max_hp:
                stats['max_hp'] = max_hp
                stats['hp_max'] = max_hp
            stats['temp_hp'] = temp_hp
        xp = actor.get('xp') if isinstance(actor.get('xp'), dict) else {}
        if xp:
            current_xp = max(0, int_or_default(xp.get('current'), default=0))
            stats['xp'] = current_xp
            stats['experience'] = current_xp
        player.stats = safe_json_dumps(stats, {})

    session_obj.state_snapshot = safe_json_dumps(state, {})


def legacy_immediate_summary_from_applied(applied_changes: list[dict[str, Any]], rejected: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    inventory_changes: list[dict[str, Any]] = []
    character_changes: list[dict[str, Any]] = []
    currency_names = {
        'pp': 'platinum',
        'gp': 'gold',
        'ep': 'electrum',
        'sp': 'silver',
        'cp': 'copper',
    }
    for change in applied_changes:
        change_type = str(change.get('type') or '')
        if change_type in {'inventory.add', 'inventory.remove'}:
            inventory_changes.append(
                {
                    'action': 'acquire' if change_type == 'inventory.add' else 'lose',
                    'item_name': change.get('itemName') or change.get('item_name') or change.get('item', {}).get('name'),
                    'quantity': max(1, int_or_default(change.get('quantity'), default=1)),
                    'source': change.get('source') or 'state_pipeline',
                    'state_change_id': change.get('id'),
                }
            )
        elif change_type in {'health.heal', 'health.damage', 'currency.add', 'currency.remove'}:
            amount = int_or_default(change.get('actualAmount', change.get('amount')), default=0)
            signed_amount = -amount if change_type in {'health.damage', 'currency.remove'} else amount
            character_change = {
                'player_id': parse_actor_player_id(change.get('actorId') or change.get('actor_id')),
                'change_type': change_type,
                'amount': amount,
                'currency': change.get('currency'),
                'state_change_id': change.get('id'),
                'already_applied': True,
            }
            if change_type in {'health.heal', 'health.damage'}:
                character_change['hp_delta'] = signed_amount
            if change_type in {'currency.add', 'currency.remove'}:
                currency_code = str(change.get('currency') or '').lower()
                if currency_code == 'gp':
                    character_change['gold_delta'] = signed_amount
                elif currency_code in currency_names:
                    character_change['gold_delta'] = 0
                    character_change['currency_delta'] = {currency_names[currency_code]: signed_amount}
            character_changes.append(character_change)
    return {
        'inventory_changes_applied': inventory_changes,
        'character_state_changes_applied': character_changes,
        'rejections': rejected or [],
        'source': 'state_pipeline',
    }
