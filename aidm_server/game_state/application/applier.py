from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from aidm_server.armor_class import sync_actor_armor_class
from aidm_server.canon_text import int_or_default
from aidm_server.combat.state import ensure_combat_state, normalize_battlefield, normalize_combat_state, normalize_participant, normalize_position
from aidm_server.game_state.equipment import conflict_items, equipment_slot_label, infer_equipment_slot
from aidm_server.game_state.models import (
    CURRENCY_CODES,
    actor_currency,
    actor_items,
    append_change_ledger,
    dump_inventory_items,
    find_actor,
    normalize_item_name,
    parse_actor_player_id,
    stable_slug,
    stable_item_id,
    stats_with_currency,
)
from aidm_server.game_state.leveling import sync_actor_level_for_xp, sync_stats_for_level
from aidm_server.models import Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.spellbook import (
    character_sheet_record,
    ensure_character_sheet_spellbook,
    known_spell_names,
    merge_spellbooks,
    normalize_spellbook,
    spell_from_change,
)
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
    payload = {
        **raw_item,
        'id': item_id,
        'name': name,
        'quantity': quantity,
        'type': raw_item.get('type') or change.get('itemType') or change.get('item_type') or 'misc',
    }
    source_actor_id = str(change.get('sourceActorId') or change.get('fromActorId') or raw_item.get('sourceActorId') or '').strip()
    if source_actor_id:
        payload['sourceActorId'] = source_actor_id
    return payload


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


def _merge_scene_item(items: list[dict[str, Any]], incoming: dict[str, Any]) -> dict[str, Any]:
    item = _merge_item(items, incoming)
    if incoming.get('sourceActorId') and not item.get('sourceActorId'):
        item['sourceActorId'] = incoming.get('sourceActorId')
    return item


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


def _equip_item(items: list[dict[str, Any]], change: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    item = _find_item(
        items,
        item_id=_change_value(change, 'itemId', 'item_id'),
        item_name=_change_value(change, 'itemName', 'item_name'),
    )
    if not item:
        return None, [], None
    slot = infer_equipment_slot(item, requested_slot=_change_value(change, 'slot', 'equipment_slot'), equipped_items=items)
    if not slot:
        return item, [], None
    conflicts = conflict_items(items, item, slot)
    for conflict in conflicts:
        conflict['equipped'] = False
        conflict['slot'] = conflict.get('slot') or infer_equipment_slot(conflict) or 'none'
    item['equipped'] = True
    item['slot'] = slot
    if change.get('turnId') or change.get('turn_id'):
        item['lastEquippedAtTurn'] = change.get('turnId') or change.get('turn_id')
    return item, conflicts, slot


def _unequip_item(items: list[dict[str, Any]], change: dict[str, Any]) -> dict[str, Any] | None:
    item = _find_item(
        items,
        item_id=_change_value(change, 'itemId', 'item_id'),
        item_name=_change_value(change, 'itemName', 'item_name'),
    )
    if not item:
        return None
    item['equipped'] = False
    item['slot'] = item.get('slot') or infer_equipment_slot(item) or 'none'
    return item


def _sync_actor_and_combat_armor_class(state: dict[str, Any], actor: dict[str, Any] | None) -> int:
    armor_class = sync_actor_armor_class(actor)
    if not isinstance(actor, dict):
        return armor_class
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    participants = combat.get('participants') if isinstance(combat.get('participants'), list) else []
    actor_id = str(actor.get('id') or '')
    player_id = parse_actor_player_id(actor_id) or actor.get('playerId')
    participant_ids = {actor_id}
    if player_id:
        participant_ids.add(f'player_{player_id}')
    breakdown = (actor.get('metadata') or {}).get('armorClassBreakdown') if isinstance(actor.get('metadata'), dict) else None
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        participant_id = str(participant.get('id') or '')
        participant_player_id = participant.get('playerId') or parse_actor_player_id(participant_id)
        if participant_id not in participant_ids and (not player_id or str(participant_player_id) != str(player_id)):
            continue
        participant['armorClass'] = armor_class
        stats = participant.setdefault('stats', {})
        if not isinstance(stats, dict):
            stats = {}
            participant['stats'] = stats
        stats['armorClass'] = armor_class
        stats['armor_class'] = armor_class
        if breakdown:
            participant['armorClassBreakdown'] = breakdown
    return armor_class


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


def _apply_health_max_set(actor: dict[str, Any], change: dict[str, Any]) -> dict[str, int]:
    health = actor.setdefault('health', {})
    old_max = max(0, int_or_default(health.get('maxHp'), default=0))
    old_current = max(0, int_or_default(health.get('currentHp'), default=0))
    new_max = max(1, int_or_default(change.get('maxHp', change.get('amount')), default=old_max or 1))
    if change.get('currentHp') is not None:
        new_current = max(0, min(new_max, int_or_default(change.get('currentHp'), default=old_current)))
    elif change.get('healToMax') or change.get('setCurrentToMax'):
        new_current = new_max
    else:
        new_current = min(old_current, new_max)
    health['maxHp'] = new_max
    health['currentHp'] = new_current
    return {
        'oldMaxHp': old_max,
        'newMaxHp': new_max,
        'oldCurrentHp': old_current,
        'newCurrentHp': new_current,
        'maxHpDelta': new_max - old_max,
        'currentHpDelta': new_current - old_current,
    }


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


def _apply_xp(actor: dict[str, Any], change: dict[str, Any], direction: int) -> int:
    amount = max(0, int_or_default(change.get('amount'), default=0))
    xp = actor.setdefault('xp', {})
    current = max(0, int_or_default(xp.get('current'), default=0))
    if direction < 0:
        actual = min(current, amount)
        xp['current'] = current - actual
        sync_actor_level_for_xp(actor)
        return actual
    xp['current'] = current + amount
    sync_actor_level_for_xp(actor)
    return amount


def _apply_spell_learn(actor: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    spell = spell_from_change(change)
    if not spell:
        return None
    existing = normalize_spellbook(actor.get('spellbook') if isinstance(actor.get('spellbook'), dict) else {})
    known_before = {normalize_item_name(candidate.get('name')) for candidate in existing.get('knownSpells', []) if isinstance(candidate, dict)}
    merged = merge_spellbooks(existing, {'knownSpells': [spell]})
    actor['spellbook'] = merged
    actor['spells'] = known_spell_names(merged)
    return {
        'spellId': spell.get('id'),
        'spellName': spell.get('name'),
        'spellLevel': spell.get('level'),
        'alreadyKnown': normalize_item_name(spell.get('name')) in known_before,
    }


def _text(value: Any) -> str:
    return str(value or '').strip()


def _world_id(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return stable_slug(text)
    return ''


def _ensure_list(container: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = container.get(key)
    if isinstance(value, list):
        return value
    container[key] = []
    return container[key]


def _ensure_dict(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    if isinstance(value, dict):
        return value
    container[key] = {}
    return container[key]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _merge_unique(existing: Any, incoming: Any) -> list[str]:
    merged: list[str] = []
    for value in [*_string_list(existing), *_string_list(incoming)]:
        if value not in merged:
            merged.append(value)
    return merged


def _remove_active_quest_id(state: dict[str, Any], quest_id: Any) -> None:
    quest_id_text = _text(quest_id)
    if not quest_id_text:
        return
    scene = state.get('currentScene')
    if not isinstance(scene, dict):
        return
    active_quest_ids = scene.get('activeQuestIds')
    if not isinstance(active_quest_ids, list):
        return
    scene['activeQuestIds'] = [item for item in active_quest_ids if _text(item) != quest_id_text]


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


def _location_record(state: dict[str, Any], *, location_id: Any = None, name: Any = None) -> dict[str, Any] | None:
    return _find_record(_ensure_list(state, 'locations'), record_id=location_id, name=name)


def _quest_record(state: dict[str, Any], *, quest_id: Any = None, title: Any = None) -> dict[str, Any] | None:
    return _find_record(_ensure_list(state, 'quests'), record_id=quest_id, title=title)


def _npc_record(state: dict[str, Any], *, npc_id: Any = None, name: Any = None) -> dict[str, Any] | None:
    return _find_record(
        [*_ensure_list(state, 'knownNpcs'), *_ensure_list(state, 'partyNpcs')],
        record_id=npc_id,
        name=name,
    )


def _turn_id(change: dict[str, Any]) -> int | None:
    if change.get('turnId') is None and change.get('turn_id') is None:
        return None
    value = int_or_default(change.get('turnId', change.get('turn_id')), default=0)
    return value if value > 0 else None


def _merge_rich_text(record: dict[str, Any], key: str, value: Any) -> None:
    if value == '':
        record[key] = ''
        return
    incoming = _text(value)
    if not incoming:
        return
    existing = _text(record.get(key))
    if not existing or len(incoming) >= len(existing):
        record[key] = incoming


def _set_if_present(record: dict[str, Any], key: str, value: Any) -> None:
    if value not in (None, '', [], {}):
        record[key] = value


def _merge_metadata(record: dict[str, Any], incoming: Any) -> None:
    if not isinstance(incoming, dict):
        return
    metadata = record.setdefault('metadata', {})
    if not isinstance(metadata, dict):
        metadata = {}
        record['metadata'] = metadata
    for key, value in incoming.items():
        if value not in (None, '', [], {}):
            metadata[key] = value


def _ensure_scene(state: dict[str, Any]) -> dict[str, Any]:
    scene = state.get('currentScene')
    if not isinstance(scene, dict):
        scene = {}
        state['currentScene'] = scene
    scene.setdefault('sceneType', 'exploration')
    scene.setdefault('dangerLevel', 0)
    scene.setdefault('combatState', 'none')
    scene.setdefault('activeNpcIds', [])
    scene.setdefault('activeQuestIds', [])
    return scene


def _scene_items(scene: dict[str, Any]) -> list[dict[str, Any]]:
    items = scene.get('items')
    if isinstance(items, list):
        return items
    scene['items'] = []
    return scene['items']


def _apply_scene_fields(scene: dict[str, Any], change: dict[str, Any]) -> None:
    for key in ('locationId', 'name', 'sceneType', 'mood', 'combatState', 'musicTag'):
        _set_if_present(scene, key, change.get(key))
    if 'dangerLevel' in change:
        scene['dangerLevel'] = max(0, min(10, int_or_default(change.get('dangerLevel'), default=0)))
    if 'description' in change:
        _merge_rich_text(scene, 'description', change.get('description'))
    if 'activeNpcIds' in change:
        scene['activeNpcIds'] = _string_list(change.get('activeNpcIds'))
    if 'activeQuestIds' in change:
        scene['activeQuestIds'] = _merge_unique(scene.get('activeQuestIds'), change.get('activeQuestIds'))
    for key in ('playerPositions', 'playerZones', 'characterPositions', 'characterZones'):
        if isinstance(change.get(key), dict):
            current = scene.get(key) if isinstance(scene.get(key), dict) else {}
            scene[key] = {**current, **{str(k): v for k, v in change[key].items() if v not in (None, '', [], {})}}
    turn_id = _turn_id(change)
    if turn_id is not None:
        scene['updatedAtTurn'] = turn_id


def _location_payload(change: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    location = change.get('location') if isinstance(change.get('location'), dict) else {}
    location_id = _world_id(
        change.get('locationId'),
        location.get('id'),
        location.get('locationId'),
        change.get('name') or change.get('locationName'),
        location.get('name'),
    )
    name = _text(change.get('name') or change.get('locationName') or location.get('name') or location_id)
    turn_id = _turn_id(change)
    payload: dict[str, Any] = {
        **location,
        'id': location_id,
        'name': name,
        'type': change.get('type') if str(change.get('type') or '').startswith('location_type.') else location.get('type'),
        'description': change.get('description') or location.get('description'),
        'status': status or change.get('status') or location.get('status') or 'discovered',
        'parentLocationId': change.get('parentLocationId') or location.get('parentLocationId'),
        'connectedLocationIds': _merge_unique(location.get('connectedLocationIds'), change.get('connectedLocationIds')),
        'npcIds': _merge_unique(location.get('npcIds'), change.get('npcIds')),
        'questIds': _merge_unique(location.get('questIds'), change.get('questIds')),
        'tags': _merge_unique(location.get('tags'), change.get('tags')),
        'metadata': location.get('metadata') if isinstance(location.get('metadata'), dict) else {},
    }
    location_type = change.get('locationType') or change.get('type') or location.get('type')
    if location_type and str(location_type).startswith('location.'):
        location_type = None
    if location_type:
        payload['type'] = location_type
    if turn_id is not None:
        if status in {'visited', 'discovered'} or payload.get('status') in {'visited', 'discovered'}:
            payload['firstDiscoveredTurn'] = location.get('firstDiscoveredTurn') or change.get('firstDiscoveredTurn') or turn_id
        if status == 'visited' or payload.get('status') == 'visited':
            payload['lastVisitedTurn'] = turn_id
    if change.get('metadata'):
        payload['metadata'] = {**payload.get('metadata', {}), **(change.get('metadata') if isinstance(change.get('metadata'), dict) else {})}
    return payload


def _merge_location(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    locations = _ensure_list(state, 'locations')
    record = _find_record(locations, record_id=payload.get('id'), name=payload.get('name'))
    if not record:
        record = {
            'id': payload.get('id'),
            'name': payload.get('name'),
            'type': payload.get('type') or 'other',
            'description': _text(payload.get('description')),
            'status': payload.get('status') or 'discovered',
            'parentLocationId': payload.get('parentLocationId'),
            'connectedLocationIds': _string_list(payload.get('connectedLocationIds')),
            'npcIds': _string_list(payload.get('npcIds')),
            'questIds': _string_list(payload.get('questIds')),
            'tags': _string_list(payload.get('tags')),
            'firstDiscoveredTurn': payload.get('firstDiscoveredTurn'),
            'lastVisitedTurn': payload.get('lastVisitedTurn'),
            'metadata': payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {},
        }
        locations.append(record)
        return record
    for key in ('name', 'type', 'status', 'parentLocationId', 'firstDiscoveredTurn', 'lastVisitedTurn'):
        if key == 'firstDiscoveredTurn' and record.get(key):
            continue
        _set_if_present(record, key, payload.get(key))
    _merge_rich_text(record, 'description', payload.get('description'))
    for key in ('connectedLocationIds', 'npcIds', 'questIds', 'tags'):
        record[key] = _merge_unique(record.get(key), payload.get(key))
    _merge_metadata(record, payload.get('metadata'))
    return record


def _apply_scene_move(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    scene = _ensure_scene(state)
    location_payload = _location_payload(change, status='visited')
    location = _merge_location(state, location_payload)
    move_change = {
        'sceneType': 'exploration',
        'dangerLevel': 0,
        'combatState': 'none',
        'description': '',
        'activeNpcIds': [],
        **change,
        'locationId': location.get('id'),
        'name': location.get('name'),
    }
    if 'mood' not in change:
        scene.pop('mood', None)
    if 'musicTag' not in change:
        scene.pop('musicTag', None)
    _apply_scene_fields(scene, move_change)
    return location


def _normalize_objective(raw: dict[str, Any]) -> dict[str, Any]:
    description = _text(raw.get('description'))
    objective_id = _world_id(raw.get('id'), raw.get('objectiveId'), description)
    return {
        **raw,
        'id': objective_id,
        'description': description,
        'status': raw.get('status') or 'open',
    }


def _merge_objectives(existing: Any, incoming: Any) -> list[dict[str, Any]]:
    objectives = [dict(item) for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
    incoming_items = incoming if isinstance(incoming, list) else []
    for raw_objective in incoming_items:
        if not isinstance(raw_objective, dict):
            continue
        objective = _normalize_objective(raw_objective)
        current = _find_record(objectives, record_id=objective.get('id'), name=objective.get('description'))
        if not current:
            objectives.append(objective)
            continue
        _merge_rich_text(current, 'description', objective.get('description'))
        _set_if_present(current, 'status', objective.get('status'))
    return objectives


def _quest_payload(change: dict[str, Any]) -> dict[str, Any]:
    quest = change.get('quest') if isinstance(change.get('quest'), dict) else {}
    title = _text(change.get('title') or change.get('name') or quest.get('title') or quest.get('name'))
    quest_id = _world_id(change.get('questId'), quest.get('id'), quest.get('questId'), title)
    turn_id = _turn_id(change)
    payload: dict[str, Any] = {
        **quest,
        'id': quest_id,
        'title': title or quest_id,
        'status': change.get('status') or quest.get('status') or ('active' if str(change.get('type')) == 'quest.add' else None),
        'summary': change.get('summary') or quest.get('summary'),
        'stage': change.get('stage') or quest.get('stage'),
        'objectives': change.get('objectives') if isinstance(change.get('objectives'), list) else quest.get('objectives') if isinstance(quest.get('objectives'), list) else [],
        'relatedNpcIds': _merge_unique(quest.get('relatedNpcIds'), change.get('relatedNpcIds')),
        'relatedLocationIds': _merge_unique(quest.get('relatedLocationIds'), change.get('relatedLocationIds')),
        'importantItemIds': _merge_unique(quest.get('importantItemIds'), change.get('importantItemIds')),
        'flags': quest.get('flags') if isinstance(quest.get('flags'), dict) else {},
        'metadata': quest.get('metadata') if isinstance(quest.get('metadata'), dict) else {},
    }
    if isinstance(change.get('flags'), dict):
        payload['flags'] = {**payload['flags'], **change['flags']}
    if isinstance(change.get('metadata'), dict):
        payload['metadata'] = {**payload['metadata'], **change['metadata']}
    if turn_id is not None:
        payload['createdAtTurn'] = quest.get('createdAtTurn') or change.get('createdAtTurn') or turn_id
        payload['updatedAtTurn'] = turn_id
    return payload


def _merge_quest(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    quests = _ensure_list(state, 'quests')
    record = _find_record(quests, record_id=payload.get('id'), title=payload.get('title'))
    if not record:
        record = {
            'id': payload.get('id'),
            'title': payload.get('title'),
            'status': payload.get('status') or 'active',
            'summary': _text(payload.get('summary')),
            'stage': _text(payload.get('stage')),
            'objectives': _merge_objectives([], payload.get('objectives')),
            'relatedNpcIds': _string_list(payload.get('relatedNpcIds')),
            'relatedLocationIds': _string_list(payload.get('relatedLocationIds')),
            'importantItemIds': _string_list(payload.get('importantItemIds')),
            'flags': payload.get('flags') if isinstance(payload.get('flags'), dict) else {},
            'createdAtTurn': payload.get('createdAtTurn'),
            'updatedAtTurn': payload.get('updatedAtTurn'),
            'completedAtTurn': payload.get('completedAtTurn'),
            'metadata': payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {},
        }
        quests.append(record)
        return record
    for key in ('title', 'status', 'stage', 'createdAtTurn', 'updatedAtTurn', 'completedAtTurn'):
        if key == 'createdAtTurn' and record.get(key):
            continue
        _set_if_present(record, key, payload.get(key))
    _merge_rich_text(record, 'summary', payload.get('summary'))
    record['objectives'] = _merge_objectives(record.get('objectives'), payload.get('objectives'))
    for key in ('relatedNpcIds', 'relatedLocationIds', 'importantItemIds'):
        record[key] = _merge_unique(record.get(key), payload.get(key))
    flags = record.setdefault('flags', {})
    if isinstance(flags, dict) and isinstance(payload.get('flags'), dict):
        flags.update(payload['flags'])
    _merge_metadata(record, payload.get('metadata'))
    return record


def _apply_objective_change(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    quest = _quest_record(state, quest_id=change.get('questId'), title=change.get('title'))
    if not quest:
        return None
    objective = change.get('objective') if isinstance(change.get('objective'), dict) else {}
    objective = {
        **objective,
        'id': change.get('objectiveId') or objective.get('id') or objective.get('objectiveId'),
        'description': change.get('description') or objective.get('description'),
        'status': change.get('status') or change.get('objectiveStatus') or objective.get('status') or 'open',
    }
    quest['objectives'] = _merge_objectives(quest.get('objectives'), [objective])
    turn_id = _turn_id(change)
    if turn_id is not None:
        quest['updatedAtTurn'] = turn_id
    return quest


def _npc_payload(change: dict[str, Any]) -> dict[str, Any]:
    npc = change.get('npc') if isinstance(change.get('npc'), dict) else {}
    name = _text(change.get('name') or change.get('npcName') or npc.get('name'))
    npc_id = _world_id(change.get('npcId'), npc.get('id'), npc.get('npcId'), name)
    turn_id = _turn_id(change)
    race = (
        change.get('race')
        or change.get('species')
        or change.get('ancestry')
        or npc.get('race')
        or npc.get('species')
        or npc.get('ancestry')
    )
    payload: dict[str, Any] = {
        **npc,
        'id': npc_id,
        'name': name or npc_id,
        'race': race,
        'role': change.get('role') or npc.get('role'),
        'description': change.get('description') or npc.get('description'),
        'disposition': change.get('disposition') or npc.get('disposition') or 'unknown',
        'relationship': npc.get('relationship') if isinstance(npc.get('relationship'), dict) else {},
        'locationId': _world_id(change.get('locationId'), npc.get('locationId')) if (change.get('locationId') or npc.get('locationId')) else None,
        'status': change.get('status') or npc.get('status') or 'known',
        'faction': change.get('faction') or npc.get('faction'),
        'aliases': _merge_unique(npc.get('aliases'), change.get('aliases')),
        'questIds': _merge_unique(npc.get('questIds'), change.get('questIds')),
        'memory': _merge_unique(npc.get('memory'), change.get('memory')),
        'metadata': npc.get('metadata') if isinstance(npc.get('metadata'), dict) else {},
    }
    if isinstance(change.get('relationship'), dict):
        payload['relationship'] = {**payload['relationship'], **change['relationship']}
    if isinstance(change.get('metadata'), dict):
        payload['metadata'] = {**payload['metadata'], **change['metadata']}
    if turn_id is not None:
        if str(change.get('type')) == 'npc.discover':
            payload['firstMetTurn'] = npc.get('firstMetTurn') or change.get('firstMetTurn') or turn_id
        payload['lastSeenTurn'] = turn_id
    return payload


def _merge_npc(state: dict[str, Any], payload: dict[str, Any], *, party: bool = False) -> dict[str, Any]:
    collection_key = 'partyNpcs' if party else 'knownNpcs'
    collection = _ensure_list(state, collection_key)
    record = _npc_record(state, npc_id=payload.get('id'), name=payload.get('name'))
    if not record:
        record = {
            'id': payload.get('id'),
            'name': payload.get('name'),
            'race': payload.get('race'),
            'role': payload.get('role'),
            'description': _text(payload.get('description')),
            'disposition': payload.get('disposition') or 'unknown',
            'relationship': payload.get('relationship') if isinstance(payload.get('relationship'), dict) else {'score': 0, 'label': 'neutral'},
            'locationId': payload.get('locationId'),
            'status': payload.get('status') or 'known',
            'faction': payload.get('faction'),
            'aliases': _string_list(payload.get('aliases')),
            'questIds': _string_list(payload.get('questIds')),
            'memory': _string_list(payload.get('memory')),
            'firstMetTurn': payload.get('firstMetTurn'),
            'lastSeenTurn': payload.get('lastSeenTurn'),
            'metadata': payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {},
        }
        collection.append(record)
        return record
    for key in ('name', 'race', 'role', 'disposition', 'locationId', 'status', 'faction', 'firstMetTurn', 'lastSeenTurn'):
        if key == 'firstMetTurn' and record.get(key):
            continue
        if key == 'name' and payload.get('name') == payload.get('id') and record.get('name'):
            continue
        _set_if_present(record, key, payload.get(key))
    _merge_rich_text(record, 'description', payload.get('description'))
    record['aliases'] = _merge_unique(record.get('aliases'), payload.get('aliases'))
    record['questIds'] = _merge_unique(record.get('questIds'), payload.get('questIds'))
    record['memory'] = _merge_unique(record.get('memory'), payload.get('memory'))
    relationship = record.setdefault('relationship', {})
    if not isinstance(relationship, dict):
        relationship = {}
        record['relationship'] = relationship
    if isinstance(payload.get('relationship'), dict):
        relationship.update({key: value for key, value in payload['relationship'].items() if value not in (None, '')})
    relationship.setdefault('score', 0)
    relationship.setdefault('label', 'neutral')
    _merge_metadata(record, payload.get('metadata'))
    return record


def _link_npc_and_quest_refs(state: dict[str, Any], npc: dict[str, Any]) -> None:
    npc_id = _text(npc.get('id'))
    if not npc_id:
        return
    location = _location_record(state, location_id=npc.get('locationId'))
    if location:
        location['npcIds'] = _merge_unique(location.get('npcIds'), [npc_id])
    for quest_id in _string_list(npc.get('questIds')):
        quest = _quest_record(state, quest_id=quest_id)
        if quest:
            quest['relatedNpcIds'] = _merge_unique(quest.get('relatedNpcIds'), [npc_id])


def _apply_relationship_update(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    npc = _npc_record(state, npc_id=change.get('npcId'), name=change.get('name'))
    if not npc:
        return None
    relationship = npc.setdefault('relationship', {})
    if not isinstance(relationship, dict):
        relationship = {}
        npc['relationship'] = relationship
    current = int_or_default(relationship.get('score'), default=0)
    if change.get('scoreDelta') is not None:
        relationship['score'] = max(-100, min(100, current + int_or_default(change.get('scoreDelta'), default=0)))
    elif change.get('relationshipScore') is not None:
        relationship['score'] = max(-100, min(100, int_or_default(change.get('relationshipScore'), default=current)))
    elif isinstance(change.get('relationship'), dict) and change['relationship'].get('score') is not None:
        relationship['score'] = max(-100, min(100, int_or_default(change['relationship'].get('score'), default=current)))
    if change.get('relationshipLabel'):
        relationship['label'] = _text(change.get('relationshipLabel'))
    elif isinstance(change.get('relationship'), dict) and change['relationship'].get('label'):
        relationship['label'] = _text(change['relationship'].get('label'))
    relationship.setdefault('score', 0)
    relationship.setdefault('label', 'neutral')
    return npc


def _combat_participant(combat: dict[str, Any], participant_id: Any) -> dict[str, Any] | None:
    requested = _text(participant_id)
    if not requested:
        return None
    for participant in combat.get('participants') or []:
        if isinstance(participant, dict) and _text(participant.get('id')) == requested:
            return participant
    requested_keys = _combat_reference_keys(requested)
    matches = [
        participant
        for participant in combat.get('participants') or []
        if isinstance(participant, dict)
        and requested_keys.intersection(_combat_participant_reference_keys(participant))
    ]
    unique_ids = {_text(participant.get('id')) for participant in matches if _text(participant.get('id'))}
    if len(unique_ids) == 1:
        resolved_id = next(iter(unique_ids))
        return next(participant for participant in matches if _text(participant.get('id')) == resolved_id)
    return None


def _combat_reference_keys(value: Any) -> set[str]:
    text = _text(value)
    if not text:
        return set()
    normalized = normalize_item_name(text)
    cleaned = re.sub(r'[^a-z0-9]+', ' ', normalized).strip()
    candidates = {normalized, cleaned, stable_slug(text)}
    for candidate in list(candidates):
        if not candidate:
            continue
        for article in ('the ', 'a ', 'an '):
            if candidate.startswith(article):
                candidates.add(candidate[len(article) :].strip())
        for marker in (' the ', ' a ', ' an '):
            if marker in candidate:
                candidates.add(candidate.rsplit(marker, 1)[-1].strip())
    return {candidate for candidate in candidates if candidate}


def _combat_participant_reference_keys(participant: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for value in (
        participant.get('id'),
        participant.get('name'),
        participant.get('definitionId'),
        participant.get('creatureType'),
    ):
        keys.update(_combat_reference_keys(value))
    for alias in participant.get('aliases') or []:
        keys.update(_combat_reference_keys(alias))
    return keys


def _sync_actor_health_to_combat_participant(state: dict[str, Any], actor: dict[str, Any]) -> None:
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else None
    if not combat:
        return
    participant = _combat_participant(combat, actor.get('id'))
    if not participant:
        return
    health = actor.get('health') if isinstance(actor.get('health'), dict) else {}
    hp = participant.setdefault('hp', {})
    current = max(0, int_or_default(health.get('currentHp'), default=hp.get('current') or 0))
    maximum = max(current, int_or_default(health.get('maxHp'), default=hp.get('max') or current))
    hp['current'] = current
    hp['max'] = maximum
    hp['temp'] = max(0, int_or_default(health.get('tempHp'), default=hp.get('temp') or 0))
    if participant.get('team') == 'player':
        participant['isAlive'] = participant.get('isAlive', True)
        participant['isConscious'] = current > 0


def _sync_player_actor_from_combat_participant(state: dict[str, Any], participant: dict[str, Any]) -> None:
    if participant.get('team') != 'player':
        return
    actor = find_actor(state, participant.get('id'))
    if not actor:
        return

    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    if hp:
        health = actor.setdefault('health', {})
        current = max(0, int_or_default(hp.get('current', hp.get('currentHp')), default=health.get('currentHp') or 0))
        maximum = max(current, int_or_default(hp.get('max', hp.get('maxHp')), default=health.get('maxHp') or current))
        health['currentHp'] = current
        health['maxHp'] = maximum
        health['tempHp'] = max(0, int_or_default(hp.get('temp', hp.get('tempHp')), default=health.get('tempHp') or 0))
    if 'conditions' in participant:
        actor.setdefault('health', {})['conditions'] = _string_list(participant.get('conditions'))


def _sync_actor_level_to_combat_participant(state: dict[str, Any], actor: dict[str, Any]) -> None:
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else None
    if not combat:
        return
    participant = _combat_participant(combat, actor.get('id'))
    if participant:
        participant['level'] = actor.get('level')


def _sync_scene_combat_state(state: dict[str, Any], combat: dict[str, Any]) -> None:
    scene = _ensure_scene(state)
    status = _text(combat.get('status'))
    if status in {'starting', 'active'}:
        scene['sceneType'] = 'combat'
        scene['combatState'] = 'active'
        scene['dangerLevel'] = max(8, int_or_default(scene.get('dangerLevel'), default=0))
    elif status == 'ended':
        scene['combatState'] = 'resolved'
        scene['dangerLevel'] = min(int_or_default(scene.get('dangerLevel'), default=0), 4)
        if scene.get('sceneType') == 'combat':
            scene['sceneType'] = 'exploration'
        if scene.get('mood') == 'dangerous':
            scene['mood'] = 'calm'
    elif status == 'none':
        scene['combatState'] = 'none'


def _apply_combat_start(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    combat_payload = change.get('combat') if isinstance(change.get('combat'), dict) else change
    combat = normalize_combat_state(
        {
            **combat_payload,
            'status': combat_payload.get('status') or 'active',
            'round': combat_payload.get('round') or 1,
        },
        state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {},
    )
    state['combat'] = combat
    _sync_scene_combat_state(state, combat)
    return combat


def _apply_combat_update(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    combat = ensure_combat_state(state)
    for key in ('status', 'round', 'turnIndex', 'lastRoundSummary', 'encounterGoal'):
        _set_if_present(combat, key, change.get(key))
    if isinstance(change.get('flags'), dict):
        flags = combat.setdefault('flags', {})
        if not isinstance(flags, dict):
            flags = {}
            combat['flags'] = flags
        flags.update(change['flags'])
    _sync_scene_combat_state(state, combat)
    return combat


def _apply_combat_participant_update(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    combat = ensure_combat_state(state)
    participant = _combat_participant(combat, change.get('participantId'))
    if not participant:
        return None
    replacement = normalize_participant(change.get('participant')) if isinstance(change.get('participant'), dict) else None
    if replacement:
        participant.update(replacement)
    hp = change.get('hp') if isinstance(change.get('hp'), dict) else {}
    if hp:
        current = max(0, int_or_default(hp.get('current', hp.get('currentHp', participant.get('hp', {}).get('current'))), default=0))
        maximum = max(current, int_or_default(hp.get('max', hp.get('maxHp', participant.get('hp', {}).get('max'))), default=current))
        participant['hp'] = {
            'current': current,
            'max': maximum,
            'temp': max(0, int_or_default(hp.get('temp', participant.get('hp', {}).get('temp')), default=0)),
        }
        participant['isAlive'] = current > 0
        participant['isConscious'] = current > 0 and bool(change.get('isConscious', participant.get('isConscious', True)))
    if 'conditions' in change:
        participant['conditions'] = _string_list(change.get('conditions'))
    if isinstance(change.get('position'), dict):
        participant['position'] = change['position']
    for key in ('isAlive', 'isConscious'):
        if key in change:
            participant[key] = bool(change[key])
    _sync_player_actor_from_combat_participant(state, participant)
    return participant


def _apply_combat_intent(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    combat = ensure_combat_state(state)
    participant = _combat_participant(combat, change.get('participantId'))
    if not participant:
        return None
    participant['currentIntent'] = change.get('intent') if isinstance(change.get('intent'), dict) else None
    return participant


def _apply_combat_morale(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    combat = ensure_combat_state(state)
    participant = _combat_participant(combat, change.get('participantId'))
    if not participant:
        return None
    participant['morale'] = max(0, min(100, int_or_default(change.get('morale'), default=participant.get('morale') or 50)))
    if change.get('event'):
        participant['moraleEvents'] = _merge_unique(participant.get('moraleEvents'), [change.get('event')])
    return participant


def _apply_combat_move(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    combat = ensure_combat_state(state)
    participant = _combat_participant(combat, change.get('participantId'))
    if not participant:
        return None
    position = normalize_position({**(participant.get('position') if isinstance(participant.get('position'), dict) else {}), 'rangeBand': change.get('toRangeBand')})
    for key in ('zoneId', 'coverId', 'isHidden'):
        if key in change:
            position[key] = change[key]
    participant['position'] = position
    return participant


def _apply_combat_condition(state: dict[str, Any], change: dict[str, Any], *, add: bool) -> dict[str, Any] | None:
    combat = ensure_combat_state(state)
    participant = _combat_participant(combat, change.get('participantId'))
    if not participant:
        return None
    condition = _text(change.get('condition')).lower().replace(' ', '_')
    conditions = [str(item).strip().lower().replace(' ', '_') for item in participant.get('conditions') or [] if str(item or '').strip()]
    if add and condition and condition not in conditions:
        conditions.append(condition)
    if not add:
        conditions = [item for item in conditions if item != condition]
    participant['conditions'] = conditions
    if condition in {'fled', 'escaped'} and add:
        participant['isConscious'] = False
    if condition == 'surrendered' and add:
        participant['currentIntent'] = None
    return participant


def _apply_combat_ability_mark_used(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    combat = ensure_combat_state(state)
    participant = _combat_participant(combat, change.get('participantId'))
    if not participant:
        return None
    ability_id = _text(change.get('abilityId'))
    for ability in participant.get('abilities') or []:
        if not isinstance(ability, dict) or _text(ability.get('id')) != ability_id:
            continue
        if ability.get('usesRemaining') is not None:
            ability['usesRemaining'] = max(0, int_or_default(ability.get('usesRemaining'), default=1) - 1)
        ability['lastUsedRound'] = (combat.get('round') or 1)
        if ability.get('cooldown') in {'once_per_combat', 'short_rest', 'long_rest'}:
            ability['used'] = True
        return participant
    return participant


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
            _sync_actor_and_combat_armor_class(next_state, actor)
            applied_change['itemId'] = item.get('id')
            applied_change['itemName'] = item.get('name')
            applied_change['actualAmount'] = max(1, int_or_default(change.get('quantity', item.get('quantity')), default=1))
        elif change_type == 'inventory.remove' and actor:
            removed = _remove_item(actor_items(actor), change)
            if not removed:
                skipped.append({'change': change, 'reason': 'Item missing during inventory removal.'})
                continue
            _sync_actor_and_combat_armor_class(next_state, actor)
            applied_change['itemId'] = _change_value(change, 'itemId', 'item_id') or (removed or {}).get('id')
            applied_change['itemName'] = _change_value(change, 'itemName', 'item_name') or (removed or {}).get('name')
            applied_change['actualAmount'] = max(1, int_or_default(change.get('quantity'), default=1))
        elif change_type == 'inventory.equip' and actor:
            item, conflicts, slot = _equip_item(actor_items(actor), change)
            if not item or not slot:
                skipped.append({'change': change, 'reason': 'Item missing or not equippable during equip.'})
                continue
            _sync_actor_and_combat_armor_class(next_state, actor)
            applied_change['itemId'] = item.get('id')
            applied_change['itemName'] = item.get('name')
            applied_change['slot'] = slot
            applied_change['slotLabel'] = equipment_slot_label(slot)
            applied_change['conflictItemIds'] = [conflict.get('id') for conflict in conflicts if conflict.get('id')]
            applied_change['conflictItemNames'] = [conflict.get('name') for conflict in conflicts if conflict.get('name')]
        elif change_type == 'inventory.unequip' and actor:
            item = _unequip_item(actor_items(actor), change)
            if not item:
                skipped.append({'change': change, 'reason': 'Item missing during unequip.'})
                continue
            _sync_actor_and_combat_armor_class(next_state, actor)
            applied_change['itemId'] = item.get('id')
            applied_change['itemName'] = item.get('name')
            applied_change['slot'] = item.get('slot')
            applied_change['slotLabel'] = equipment_slot_label(item.get('slot'))
        elif change_type == 'inventory.mark_used' and actor:
            item = _find_item(actor_items(actor), item_id=_change_value(change, 'itemId', 'item_id'))
            if item:
                item['lastUsedAtTurn'] = change.get('turnId') or change.get('turn_id') or item.get('lastUsedAtTurn')
                applied_change['itemName'] = item.get('name')
        elif change_type == 'race_ability.mark_used' and actor:
            ability_id = _text(_change_value(change, 'abilityId', 'ability_id'))
            if not ability_id:
                skipped.append({'change': change, 'reason': 'Race ability id missing during mark used.'})
                continue
            ability_state = actor.setdefault('raceAbilityState', {})
            ability_state[ability_id] = {
                'available': False,
                'usedAtTurn': change.get('turnId') or change.get('turn_id'),
                'refreshesOn': _text(_change_value(change, 'refreshesOn', 'refreshes_on')) or 'short_rest',
            }
            applied_change['abilityId'] = ability_id
        elif change_type == 'race_ability.refresh' and actor:
            ability_id = _text(_change_value(change, 'abilityId', 'ability_id'))
            if not ability_id:
                skipped.append({'change': change, 'reason': 'Race ability id missing during refresh.'})
                continue
            ability_state = actor.setdefault('raceAbilityState', {})
            current = ability_state.setdefault(ability_id, {})
            current['available'] = True
            current.pop('usedAtTurn', None)
            applied_change['abilityId'] = ability_id
        elif change_type == 'currency.add' and actor:
            applied_change['actualAmount'] = _apply_currency(actor, change, 1)
        elif change_type == 'currency.remove' and actor:
            currency_code = str(change.get('currency') or '').strip().lower()
            requested_amount = max(0, int_or_default(change.get('amount'), default=0))
            if actor_currency(actor).get(currency_code, 0) < requested_amount:
                skipped.append({'change': change, 'reason': 'Insufficient currency during removal.'})
                continue
            applied_change['actualAmount'] = abs(_apply_currency(actor, change, -1))
        elif change_type == 'health.heal' and actor:
            applied_change['actualAmount'] = _apply_health_heal(actor, change)
            _sync_actor_health_to_combat_participant(next_state, actor)
        elif change_type == 'health.max.set' and actor:
            applied_change.update(_apply_health_max_set(actor, change))
            applied_change['actualAmount'] = applied_change.get('maxHpDelta', 0)
            _sync_actor_health_to_combat_participant(next_state, actor)
        elif change_type == 'health.damage' and actor:
            result = _apply_health_damage(actor, change)
            _sync_actor_health_to_combat_participant(next_state, actor)
            applied_change.update(result)
            applied_change['actualAmount'] = result['amount']
        elif change_type == 'xp.add' and actor:
            applied_change['actualAmount'] = _apply_xp(actor, change, 1)
            _sync_actor_level_to_combat_participant(next_state, actor)
        elif change_type == 'xp.remove' and actor:
            applied_change['actualAmount'] = _apply_xp(actor, change, -1)
            _sync_actor_level_to_combat_participant(next_state, actor)
        elif change_type == 'spell.learn' and actor:
            result = _apply_spell_learn(actor, change)
            if not result:
                skipped.append({'change': change, 'reason': 'Spell missing during learn application.'})
                continue
            applied_change.update(result)
            applied_change['actualAmount'] = 0 if result.get('alreadyKnown') else 1
        elif change_type == 'combat.start':
            combat = _apply_combat_start(next_state, change)
            applied_change['combatStatus'] = combat.get('status')
            applied_change['participantCount'] = len(combat.get('participants') or [])
        elif change_type == 'combat.update':
            combat = _apply_combat_update(next_state, change)
            applied_change['combatStatus'] = combat.get('status')
            applied_change['round'] = combat.get('round')
        elif change_type == 'combat.round.advance':
            combat = ensure_combat_state(next_state)
            combat['round'] = max(1, int_or_default(change.get('round'), default=int_or_default(combat.get('round'), default=1) + 1))
            combat['turnIndex'] = 0
            if change.get('summary'):
                combat['lastRoundSummary'] = change.get('summary')
            applied_change['combatStatus'] = combat.get('status')
            applied_change['round'] = combat.get('round')
        elif change_type == 'combat.battlefield.update':
            combat = ensure_combat_state(next_state)
            combat['battlefield'] = normalize_battlefield(
                change.get('battlefield'),
                next_state.get('currentScene') if isinstance(next_state.get('currentScene'), dict) else {},
            )
            applied_change['battlefieldType'] = combat['battlefield'].get('environmentType')
        elif change_type == 'combat.participant.update':
            participant = _apply_combat_participant_update(next_state, change)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during update.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
        elif change_type == 'combat.move':
            participant = _apply_combat_move(next_state, change)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during movement.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['toRangeBand'] = (participant.get('position') or {}).get('rangeBand')
        elif change_type == 'combat.condition.add':
            participant = _apply_combat_condition(next_state, change, add=True)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during condition add.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['condition'] = change.get('condition')
        elif change_type == 'combat.condition.remove':
            participant = _apply_combat_condition(next_state, change, add=False)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during condition remove.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['condition'] = change.get('condition')
        elif change_type == 'combat.ability.mark_used':
            participant = _apply_combat_ability_mark_used(next_state, change)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during ability mark used.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['abilityId'] = change.get('abilityId')
        elif change_type == 'combat.intent.set':
            participant = _apply_combat_intent(next_state, change)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during intent update.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['intentType'] = (participant.get('currentIntent') or {}).get('intentType') if isinstance(participant.get('currentIntent'), dict) else None
        elif change_type == 'combat.morale.update':
            participant = _apply_combat_morale(next_state, change)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during morale update.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['morale'] = participant.get('morale')
        elif change_type == 'combat.morale.event':
            participant = _apply_combat_morale(next_state, change)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during morale event.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['morale'] = participant.get('morale')
            applied_change['event'] = change.get('event')
        elif change_type == 'combat.end':
            combat = ensure_combat_state(next_state)
            combat['status'] = change.get('status') or 'ended'
            combat['lastRoundSummary'] = change.get('summary') or change.get('reason') or combat.get('lastRoundSummary')
            if change.get('endReason'):
                combat.setdefault('flags', {})['endReason'] = change.get('endReason')
            for participant in combat.get('participants') or []:
                if not isinstance(participant, dict):
                    continue
                if participant.get('team') == 'enemy':
                    participant['currentIntent'] = None
                    if 'morale' in participant:
                        participant['morale'] = min(int_or_default(participant.get('morale'), default=0), 10)
            _sync_scene_combat_state(next_state, combat)
            applied_change['combatStatus'] = combat.get('status')
        elif change_type == 'scene.update':
            scene = _ensure_scene(next_state)
            _apply_scene_fields(scene, change)
            applied_change['sceneName'] = scene.get('name')
        elif change_type == 'scene.move_location':
            location = _apply_scene_move(next_state, change)
            applied_change['locationId'] = location.get('id')
            applied_change['locationName'] = location.get('name')
        elif change_type == 'scene.item.add':
            scene = _ensure_scene(next_state)
            item = _merge_scene_item(_scene_items(scene), _item_payload(change))
            applied_change['itemId'] = item.get('id')
            applied_change['itemName'] = item.get('name')
            applied_change['actualAmount'] = max(1, int_or_default(change.get('quantity', item.get('quantity')), default=1))
            if change.get('sourceActorId') or change.get('fromActorId'):
                applied_change['sourceActorId'] = change.get('sourceActorId') or change.get('fromActorId')
        elif change_type == 'scene.item.remove':
            scene = _ensure_scene(next_state)
            removed = _remove_item(_scene_items(scene), change)
            if not removed:
                skipped.append({'change': change, 'reason': 'Scene item missing during removal.'})
                continue
            applied_change['itemId'] = _change_value(change, 'itemId', 'item_id') or removed.get('id')
            applied_change['itemName'] = _change_value(change, 'itemName', 'item_name') or removed.get('name')
            applied_change['actualAmount'] = max(1, int_or_default(change.get('quantity'), default=1))
        elif change_type in {'location.discover', 'location.update'}:
            payload = _location_payload(change, status='discovered' if change_type == 'location.discover' else None)
            location = _merge_location(next_state, payload)
            applied_change['locationId'] = location.get('id')
            applied_change['locationName'] = location.get('name')
        elif change_type == 'location.connect':
            first_payload = _location_payload(
                {**change, 'locationId': change.get('locationId'), 'name': change.get('name')},
                status='discovered',
            )
            second_payload = _location_payload(
                {
                    **change,
                    'locationId': change.get('connectedLocationId'),
                    'name': change.get('connectedLocationName') or change.get('toLocationName'),
                    'connectedLocationIds': [change.get('locationId')],
                },
                status='discovered',
            )
            first = _merge_location(next_state, first_payload)
            second = _merge_location(next_state, second_payload)
            first['connectedLocationIds'] = _merge_unique(first.get('connectedLocationIds'), [second.get('id')])
            second['connectedLocationIds'] = _merge_unique(second.get('connectedLocationIds'), [first.get('id')])
            applied_change['locationId'] = first.get('id')
            applied_change['connectedLocationId'] = second.get('id')
        elif change_type in {'quest.add', 'quest.update'}:
            quest = _merge_quest(next_state, _quest_payload(change))
            applied_change['questId'] = quest.get('id')
            applied_change['questTitle'] = quest.get('title')
            if quest.get('status') == 'active':
                scene = _ensure_scene(next_state)
                scene['activeQuestIds'] = _merge_unique(scene.get('activeQuestIds'), [quest.get('id')])
        elif change_type in {'quest.objective.add', 'quest.objective.update'}:
            quest = _apply_objective_change(next_state, change)
            if not quest:
                skipped.append({'change': change, 'reason': 'Quest missing during objective update.'})
                continue
            applied_change['questId'] = quest.get('id')
            applied_change['questTitle'] = quest.get('title')
        elif change_type in {'quest.complete', 'quest.fail'}:
            quest = _quest_record(next_state, quest_id=change.get('questId'), title=change.get('title'))
            if not quest:
                skipped.append({'change': change, 'reason': 'Quest missing during status update.'})
                continue
            quest['status'] = 'completed' if change_type == 'quest.complete' else 'failed'
            turn_id = _turn_id(change)
            if turn_id is not None:
                quest['updatedAtTurn'] = turn_id
                if change_type == 'quest.complete':
                    quest['completedAtTurn'] = quest.get('completedAtTurn') or turn_id
            _remove_active_quest_id(next_state, quest.get('id'))
            applied_change['questId'] = quest.get('id')
            applied_change['questTitle'] = quest.get('title')
        elif change_type in {'npc.discover', 'npc.update'}:
            npc = _merge_npc(
                next_state,
                _npc_payload(change),
                party=bool(change.get('party') or change.get('partyNpc') or change.get('inParty')),
            )
            _link_npc_and_quest_refs(next_state, npc)
            applied_change['npcId'] = npc.get('id')
            applied_change['npcName'] = npc.get('name')
        elif change_type == 'npc.move':
            npc = _npc_record(next_state, npc_id=change.get('npcId'), name=change.get('name'))
            if not npc:
                skipped.append({'change': change, 'reason': 'NPC missing during movement.'})
                continue
            npc['locationId'] = _world_id(change.get('locationId'))
            turn_id = _turn_id(change)
            if turn_id is not None:
                npc['lastSeenTurn'] = turn_id
            _link_npc_and_quest_refs(next_state, npc)
            applied_change['npcId'] = npc.get('id')
            applied_change['npcName'] = npc.get('name')
        elif change_type == 'npc.relationship.update':
            npc = _apply_relationship_update(next_state, change)
            if not npc:
                skipped.append({'change': change, 'reason': 'NPC missing during relationship update.'})
                continue
            applied_change['npcId'] = npc.get('id')
            applied_change['npcName'] = npc.get('name')
        elif change_type == 'flag.set':
            flags = _ensure_dict(next_state, 'flags')
            flags[_world_id(change.get('flagKey'))] = change.get('flagValue')
            applied_change['flagKey'] = _world_id(change.get('flagKey'))
        elif change_type == 'flag.unset':
            flags = _ensure_dict(next_state, 'flags')
            flags.pop(_world_id(change.get('flagKey')), None)
            applied_change['flagKey'] = _world_id(change.get('flagKey'))
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
            sync_actor_level_for_xp(actor)
        if int_or_default(actor.get('level'), default=0) > int_or_default(player.level, default=1):
            player.level = int(actor['level'])
        sync_stats_for_level(stats, player.level or 1)
        health = actor.setdefault('health', {})
        current_hp = max(0, int_or_default(stats.get('current_hp', stats.get('hp_current')), default=0))
        max_hp = max(current_hp, int_or_default(stats.get('max_hp', stats.get('hp_max')), default=current_hp))
        if max_hp:
            health['currentHp'] = current_hp
            health['maxHp'] = max_hp
            health['tempHp'] = max(0, int_or_default(stats.get('temp_hp', stats.get('tempHp')), default=0))
            _sync_actor_health_to_combat_participant(state, actor)
        actor['level'] = int_or_default(player.level, default=1)
        actor_xp = actor.setdefault('xp', {})
        actor_xp['current'] = max(0, int_or_default(stats.get('xp', stats.get('experience')), default=0))
        sync_actor_level_for_xp(actor)
        _sync_actor_level_to_combat_participant(state, actor)
        player.stats = safe_json_dumps(stats, {})
        spellbook = actor.get('spellbook') if isinstance(actor.get('spellbook'), dict) else {}
        sheet_source = player.character_sheet
        if spellbook.get('knownSpells'):
            sheet = character_sheet_record(sheet_source)
            sheet['spellbook'] = normalize_spellbook(spellbook)
            sheet['spells'] = known_spell_names(sheet['spellbook'])
            sheet_source = safe_json_dumps(sheet, {})
        sheet, changed = ensure_character_sheet_spellbook(
            sheet_source,
            class_name=player.class_,
            race_name=player.race,
            race_selection=player.race_selection,
            level=player.level or 1,
        )
        if changed or spellbook.get('knownSpells'):
            player.character_sheet = safe_json_dumps(sheet, {})
            synced_spellbook = normalize_spellbook(sheet.get('spellbook'))
            if synced_spellbook.get('knownSpells'):
                actor['spellbook'] = synced_spellbook
                actor['spells'] = known_spell_names(synced_spellbook)

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
                    'player_id': parse_actor_player_id(change.get('actorId') or change.get('actor_id')),
                    'action': 'acquire' if change_type == 'inventory.add' else 'lose',
                    'item_name': change.get('itemName') or change.get('item_name') or change.get('item', {}).get('name'),
                    'quantity': max(1, int_or_default(change.get('quantity'), default=1)),
                    'source': change.get('source') or 'state_pipeline',
                    'state_change_id': change.get('id'),
                }
            )
        elif change_type in {'inventory.equip', 'inventory.unequip'}:
            inventory_changes.append(
                {
                    'player_id': parse_actor_player_id(change.get('actorId') or change.get('actor_id')),
                    'action': 'equip' if change_type == 'inventory.equip' else 'unequip',
                    'item_name': change.get('itemName') or change.get('item_name'),
                    'quantity': 1,
                    'slot': change.get('slot'),
                    'conflict_item_names': change.get('conflictItemNames') or [],
                    'source': change.get('source') or 'state_pipeline',
                    'state_change_id': change.get('id'),
                }
            )
        elif change_type in {'health.heal', 'health.damage', 'health.max.set', 'currency.add', 'currency.remove', 'xp.add', 'xp.remove'}:
            amount = int_or_default(change.get('actualAmount', change.get('amount')), default=0)
            signed_amount = -amount if change_type in {'health.damage', 'currency.remove', 'xp.remove'} else amount
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
            if change_type == 'health.max.set':
                character_change['max_hp_delta'] = int_or_default(change.get('maxHpDelta'), default=0)
                character_change['hp_delta'] = int_or_default(change.get('currentHpDelta'), default=0)
            if change_type in {'xp.add', 'xp.remove'}:
                character_change['xp_delta'] = signed_amount
            if change_type in {'currency.add', 'currency.remove'}:
                currency_code = str(change.get('currency') or '').lower()
                if currency_code == 'gp':
                    character_change['gold_delta'] = signed_amount
                elif currency_code in currency_names:
                    character_change['gold_delta'] = 0
                    character_change['currency_delta'] = {currency_names[currency_code]: signed_amount}
            character_changes.append(character_change)
        elif change_type == 'spell.learn':
            character_changes.append(
                {
                    'player_id': parse_actor_player_id(change.get('actorId') or change.get('actor_id')),
                    'change_type': change_type,
                    'spell_name': change.get('spellName') or change.get('spell_name'),
                    'spell_level': change.get('spellLevel') if change.get('spellLevel') is not None else change.get('spell_level'),
                    'state_change_id': change.get('id'),
                    'already_applied': True,
                }
            )
    return {
        'inventory_changes_applied': inventory_changes,
        'character_state_changes_applied': character_changes,
        'rejections': rejected or [],
        'source': 'state_pipeline',
    }
