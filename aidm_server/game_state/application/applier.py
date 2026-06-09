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
    stable_slug,
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


def _apply_xp(actor: dict[str, Any], change: dict[str, Any], direction: int) -> int:
    amount = max(0, int_or_default(change.get('amount'), default=0))
    xp = actor.setdefault('xp', {})
    current = max(0, int_or_default(xp.get('current'), default=0))
    if direction < 0:
        actual = min(current, amount)
        xp['current'] = current - actual
        return actual
    xp['current'] = current + amount
    return amount


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
        _set_if_present(record, key, payload.get(key))
    _merge_rich_text(record, 'description', payload.get('description'))
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
            if not removed:
                skipped.append({'change': change, 'reason': 'Item missing during inventory removal.'})
                continue
            applied_change['itemId'] = _change_value(change, 'itemId', 'item_id') or (removed or {}).get('id')
            applied_change['itemName'] = _change_value(change, 'itemName', 'item_name') or (removed or {}).get('name')
            applied_change['actualAmount'] = max(1, int_or_default(change.get('quantity'), default=1))
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
        elif change_type == 'health.damage' and actor:
            result = _apply_health_damage(actor, change)
            applied_change.update(result)
            applied_change['actualAmount'] = result['amount']
        elif change_type == 'xp.add' and actor:
            applied_change['actualAmount'] = _apply_xp(actor, change, 1)
        elif change_type == 'xp.remove' and actor:
            applied_change['actualAmount'] = _apply_xp(actor, change, -1)
        elif change_type == 'scene.update':
            scene = _ensure_scene(next_state)
            _apply_scene_fields(scene, change)
            applied_change['sceneName'] = scene.get('name')
        elif change_type == 'scene.move_location':
            location = _apply_scene_move(next_state, change)
            applied_change['locationId'] = location.get('id')
            applied_change['locationName'] = location.get('name')
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
                    'player_id': parse_actor_player_id(change.get('actorId') or change.get('actor_id')),
                    'action': 'acquire' if change_type == 'inventory.add' else 'lose',
                    'item_name': change.get('itemName') or change.get('item_name') or change.get('item', {}).get('name'),
                    'quantity': max(1, int_or_default(change.get('quantity'), default=1)),
                    'source': change.get('source') or 'state_pipeline',
                    'state_change_id': change.get('id'),
                }
            )
        elif change_type in {'health.heal', 'health.damage', 'currency.add', 'currency.remove', 'xp.add', 'xp.remove'}:
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
    return {
        'inventory_changes_applied': inventory_changes,
        'character_state_changes_applied': character_changes,
        'rejections': rejected or [],
        'source': 'state_pipeline',
    }
