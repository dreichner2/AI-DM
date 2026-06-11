from __future__ import annotations

import json
import re
from typing import Any

from aidm_server.game_state.action_types import PRE_DM_ACTION_TYPES
from aidm_server.game_state.change_types import COMBAT_STATE_CHANGE_TYPES, STATE_CHANGE_TYPES, WORLD_STATE_CHANGE_TYPES
from aidm_server.game_state.models import stable_slug

GENERIC_INTENT_SAFE_FIELDS = {'summary', 'intentDescription', 'intent_description', 'description', 'sourceText', 'source_text'}
POSITIVE_INT_FIELDS = {'quantity', 'amount'}
WEIGHT_FIELDS = ('weight', 'itemWeight', 'item_weight', 'weightLbs', 'weight_lbs')
CURRENCY_FIELDS = ('currency', 'currencyType', 'currency_type', 'currencyName', 'currency_name', 'coinType', 'coin_type')
WORLD_FIELD_ALIASES = {
    'actor_id': 'actorId',
    'turn_id': 'turnId',
    'change_id': 'changeId',
    'location_id': 'locationId',
    'location_name': 'locationName',
    'scene_type': 'sceneType',
    'danger_level': 'dangerLevel',
    'combat_state': 'combatState',
    'active_npc_ids': 'activeNpcIds',
    'active_quest_ids': 'activeQuestIds',
    'player_positions': 'playerPositions',
    'player_zones': 'playerZones',
    'character_positions': 'characterPositions',
    'character_zones': 'characterZones',
    'music_tag': 'musicTag',
    'updated_at_turn': 'updatedAtTurn',
    'parent_location_id': 'parentLocationId',
    'connected_location_id': 'connectedLocationId',
    'connected_location_ids': 'connectedLocationIds',
    'from_location_id': 'fromLocationId',
    'to_location_id': 'toLocationId',
    'npc_ids': 'npcIds',
    'quest_ids': 'questIds',
    'first_discovered_turn': 'firstDiscoveredTurn',
    'last_visited_turn': 'lastVisitedTurn',
    'quest_id': 'questId',
    'related_npc_ids': 'relatedNpcIds',
    'related_location_ids': 'relatedLocationIds',
    'important_item_ids': 'importantItemIds',
    'created_at_turn': 'createdAtTurn',
    'completed_at_turn': 'completedAtTurn',
    'objective_id': 'objectiveId',
    'npc_id': 'npcId',
    'last_seen_turn': 'lastSeenTurn',
    'first_met_turn': 'firstMetTurn',
    'score_delta': 'scoreDelta',
    'relationship_score': 'relationshipScore',
    'relationship_label': 'relationshipLabel',
    'flag_key': 'flagKey',
    'flag_value': 'flagValue',
}
COMBAT_FIELD_ALIASES = {
    'participant_id': 'participantId',
    'enemy_id': 'enemyId',
    'combat_id': 'combatId',
    'turn_id': 'turnId',
    'intent_type': 'intentType',
    'target_id': 'targetId',
    'ability_id': 'abilityId',
    'movement_goal': 'movementGoal',
    'to_range_band': 'toRangeBand',
    'from_range_band': 'fromRangeBand',
    'visible_telegraph': 'visibleTelegraph',
    'suggested_speech': 'suggestedSpeech',
    'condition_name': 'condition',
    'morale_event': 'event',
    'end_reason': 'endReason',
    'is_alive': 'isAlive',
    'is_conscious': 'isConscious',
    'current_hp': 'currentHp',
    'max_hp': 'maxHp',
}
CURRENCY_ALIASES = {
    'pp': 'pp',
    'platinum': 'pp',
    'platinum piece': 'pp',
    'platinum pieces': 'pp',
    'gp': 'gp',
    'gold': 'gp',
    'gold coin': 'gp',
    'gold coins': 'gp',
    'gold piece': 'gp',
    'gold pieces': 'gp',
    'ep': 'ep',
    'electrum': 'ep',
    'electrum piece': 'ep',
    'electrum pieces': 'ep',
    'sp': 'sp',
    'silver': 'sp',
    'silver coin': 'sp',
    'silver coins': 'sp',
    'silver piece': 'sp',
    'silver pieces': 'sp',
    'cp': 'cp',
    'copper': 'cp',
    'copper coin': 'cp',
    'copper coins': 'cp',
    'copper piece': 'cp',
    'copper pieces': 'cp',
}
_MISSING = object()


def _copy_aliases(payload: dict[str, Any], aliases: dict[str, str]) -> None:
    for source_key, target_key in aliases.items():
        if source_key in payload and target_key not in payload:
            payload[target_key] = payload.pop(source_key)


def _as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or '').strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _stable_record_id(*values: Any) -> str:
    for value in values:
        text = str(value or '').strip()
        if text:
            return stable_slug(text)
    return ''


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


def _currency_code(payload: dict[str, Any]) -> str:
    raw_value = _first_present(payload, CURRENCY_FIELDS)
    if raw_value is _MISSING:
        return ''
    normalized = re.sub(r'\s+', ' ', str(raw_value or '').strip().lower())
    return CURRENCY_ALIASES.get(normalized, normalized)


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
        'slot',
        'equipmentSlot',
        'equipment_slot',
        'attackStyle',
        'attack_style',
        'fromActorId',
        'from_actor_id',
        'toActorId',
        'to_actor_id',
        'toActorName',
        'to_actor_name',
        'summary',
    ):
        if key in raw_action:
            camel = ''.join([key.split('_')[0], *[part[:1].upper() + part[1:] for part in key.split('_')[1:]]])
            action[camel] = raw_action[key]
    currency = _currency_code(raw_action)
    if currency:
        action['currency'] = currency
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
    if action_type in {'inventory.equip', 'inventory.unequip'}:
        return bool(str(action.get('itemName') or '').strip())
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


def _normalize_world_change_ids(change: dict[str, Any], raw_id: Any) -> None:
    change_type = str(change.get('type') or '').strip()
    _copy_aliases(change, WORLD_FIELD_ALIASES)
    raw_record_id = str(raw_id or '').strip()

    location = _as_record(change.get('location'))
    quest = _as_record(change.get('quest'))
    npc = _as_record(change.get('npc'))
    objective = _as_record(change.get('objective'))

    if change_type in {'scene.move_location', 'location.discover', 'location.update'}:
        if 'locationName' in change and 'name' not in change:
            change['name'] = change.get('locationName')
        change['locationId'] = _stable_record_id(
            change.get('locationId'),
            location.get('id'),
            location.get('locationId'),
            raw_record_id if change_type.startswith('location.') else None,
            change.get('name'),
            location.get('name'),
        )
        if not change.get('name') and location.get('name'):
            change['name'] = location.get('name')

    if change_type == 'location.connect':
        if change.get('locationName') and not change.get('name'):
            change['name'] = change.get('locationName')
        change['locationId'] = _stable_record_id(change.get('locationId'), change.get('fromLocationId'), raw_record_id, change.get('name'))
        change['connectedLocationId'] = _stable_record_id(
            change.get('connectedLocationId'),
            change.get('toLocationId'),
            change.get('connectedLocationName'),
            change.get('toLocationName'),
        )
        if change.get('toLocationName') and not change.get('connectedLocationName'):
            change['connectedLocationName'] = change.get('toLocationName')

    if change_type.startswith('quest.'):
        if 'name' in change and 'title' not in change:
            change['title'] = change.get('name')
        change['questId'] = _stable_record_id(
            change.get('questId'),
            quest.get('id'),
            quest.get('questId'),
            raw_record_id,
            change.get('title'),
            quest.get('title'),
        )
        if not change.get('title') and quest.get('title'):
            change['title'] = quest.get('title')
        if isinstance(change.get('objectives'), list):
            for item in change['objectives']:
                if isinstance(item, dict):
                    _copy_aliases(item, WORLD_FIELD_ALIASES)
                    if not item.get('id'):
                        item['id'] = _stable_record_id(item.get('objectiveId'), item.get('description'))
        elif objective:
            _copy_aliases(objective, WORLD_FIELD_ALIASES)
            if not objective.get('id'):
                objective['id'] = _stable_record_id(objective.get('objectiveId'), objective.get('description'))
            change['objective'] = objective
        if not change.get('objectiveId') and objective.get('id'):
            change['objectiveId'] = objective.get('id')

    if change_type.startswith('npc.'):
        if 'npcName' in change and 'name' not in change:
            change['name'] = change.get('npcName')
        change['npcId'] = _stable_record_id(
            change.get('npcId'),
            npc.get('id'),
            npc.get('npcId'),
            raw_record_id,
            change.get('name'),
            npc.get('name'),
        )
        if not change.get('name') and npc.get('name'):
            change['name'] = npc.get('name')

    if change_type.startswith('flag.'):
        if change.get('key') and not change.get('flagKey'):
            change['flagKey'] = change.get('key')
        if 'value' in change and 'flagValue' not in change:
            change['flagValue'] = change.get('value')
        if change.get('flagKey'):
            change['flagKey'] = stable_slug(change.get('flagKey'))

    for key in (
        'activeNpcIds',
        'activeQuestIds',
        'connectedLocationIds',
        'npcIds',
        'questIds',
        'tags',
        'relatedNpcIds',
        'relatedLocationIds',
        'importantItemIds',
    ):
        if key in change:
            change[key] = _as_string_list(change.get(key))


def normalize_state_change(raw_change: Any, *, fallback_actor_id: str, fallback_id: str, source: str) -> dict[str, Any] | None:
    if not isinstance(raw_change, dict):
        return None
    change_type = str(raw_change.get('type') or '').strip()
    if change_type not in STATE_CHANGE_TYPES:
        return None
    is_world_change = change_type in WORLD_STATE_CHANGE_TYPES
    is_combat_change = change_type in COMBAT_STATE_CHANGE_TYPES
    for field in POSITIVE_INT_FIELDS:
        if field in raw_change:
            try:
                if int(raw_change.get(field)) <= 0:
                    return None
            except (TypeError, ValueError):
                return None
    change = dict(raw_change)
    raw_id = raw_change.get('id')
    change_id = raw_change.get('changeId') or raw_change.get('change_id') or (None if is_world_change else raw_id) or fallback_id
    change['id'] = str(change_id)
    change['type'] = change_type
    change['source'] = str(raw_change.get('source') or source)
    if not is_combat_change:
        change['actorId'] = str(
            _value(raw_change, 'actorId', 'actor_id', None)
            or raw_change.get('target')
            or raw_change.get('targetId')
            or raw_change.get('target_id')
            or fallback_actor_id
        )
    elif raw_change.get('actorId') or raw_change.get('actor_id'):
        change['actorId'] = str(_value(raw_change, 'actorId', 'actor_id', fallback_actor_id))
    change['visible'] = bool(raw_change.get('visible', True))
    change['reason'] = str(raw_change.get('reason') or 'Extracted from DM response.')
    if is_world_change:
        _normalize_world_change_ids(change, raw_id)
    if is_combat_change:
        _copy_aliases(change, COMBAT_FIELD_ALIASES)
        if change.get('enemyId') and not change.get('participantId'):
            change['participantId'] = change.get('enemyId')
        if change_type == 'combat.intent.set':
            intent = change.get('intent') if isinstance(change.get('intent'), dict) else {}
            _copy_aliases(intent, COMBAT_FIELD_ALIASES)
            if change.get('intentType') and not intent.get('intentType'):
                intent['intentType'] = change.get('intentType')
            if change.get('participantId') and not intent.get('enemyId'):
                intent['enemyId'] = change.get('participantId')
            change['intent'] = intent
        if change_type == 'combat.participant.update':
            hp = change.get('hp') if isinstance(change.get('hp'), dict) else {}
            if change.get('currentHp') is not None and 'current' not in hp:
                hp['current'] = change.get('currentHp')
            if change.get('maxHp') is not None and 'max' not in hp:
                hp['max'] = change.get('maxHp')
            if hp:
                change['hp'] = hp
    currency = _currency_code(raw_change)
    if currency:
        change['currency'] = currency
    if 'item_name' in change and 'itemName' not in change:
        change['itemName'] = change.pop('item_name')
    if 'item_id' in change and 'itemId' not in change:
        change['itemId'] = change.pop('item_id')
    if 'equipment_slot' in change and 'slot' not in change:
        change['slot'] = change.pop('equipment_slot')
    if 'equipmentSlot' in change and 'slot' not in change:
        change['slot'] = change.get('equipmentSlot')
    if 'from_actor_id' in change and 'fromActorId' not in change:
        change['fromActorId'] = change.pop('from_actor_id')
    if 'to_actor_id' in change and 'toActorId' not in change:
        change['toActorId'] = change.pop('to_actor_id')
    if 'to_actor_name' in change and 'toActorName' not in change:
        change['toActorName'] = change.pop('to_actor_name')
    if 'spell_name' in change and 'spellName' not in change:
        change['spellName'] = change.pop('spell_name')
    if 'spell_level' in change and 'spellLevel' not in change:
        change['spellLevel'] = change.pop('spell_level')
    if 'learned_from' in change and 'learnedFrom' not in change:
        change['learnedFrom'] = change.pop('learned_from')
    if change_type == 'spell.learn':
        raw_spell = change.get('spell')
        if isinstance(raw_spell, dict):
            if raw_spell.get('name') and not change.get('spellName'):
                change['spellName'] = raw_spell.get('name')
            if raw_spell.get('level') is not None and change.get('spellLevel') is None:
                change['spellLevel'] = raw_spell.get('level')
        elif isinstance(raw_spell, str) and raw_spell.strip() and not change.get('spellName'):
            change['spellName'] = raw_spell.strip()
        if change.get('spellLevel') is not None:
            try:
                change['spellLevel'] = max(0, min(9, int(change.get('spellLevel'))))
            except (TypeError, ValueError):
                return None
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
    if not change.get('id'):
        return False
    if change_type in WORLD_STATE_CHANGE_TYPES:
        if change_type == 'scene.update':
            return any(
                key in change
                for key in (
                    'locationId',
                    'name',
                    'sceneType',
                    'dangerLevel',
                    'mood',
                    'combatState',
                    'description',
                    'activeNpcIds',
                    'activeQuestIds',
                    'playerPositions',
                    'playerZones',
                    'characterPositions',
                    'characterZones',
                    'musicTag',
                )
            )
        if change_type == 'scene.move_location':
            return bool(str(change.get('locationId') or change.get('name') or '').strip())
        if change_type in {'location.discover', 'location.update'}:
            return bool(str(change.get('locationId') or change.get('name') or '').strip())
        if change_type == 'location.connect':
            return bool(str(change.get('locationId') or '').strip() and str(change.get('connectedLocationId') or '').strip())
        if change_type == 'quest.add':
            return bool(str(change.get('questId') or change.get('title') or '').strip())
        if change_type in {'quest.update', 'quest.complete', 'quest.fail'}:
            return bool(str(change.get('questId') or change.get('title') or '').strip())
        if change_type in {'quest.objective.add', 'quest.objective.update'}:
            objective = change.get('objective') if isinstance(change.get('objective'), dict) else {}
            return bool(
                str(change.get('questId') or change.get('title') or '').strip()
                and str(change.get('objectiveId') or objective.get('id') or objective.get('description') or '').strip()
            )
        if change_type in {'npc.discover', 'npc.update', 'npc.move', 'npc.relationship.update'}:
            return bool(str(change.get('npcId') or change.get('name') or '').strip())
        if change_type in {'flag.set', 'flag.unset'}:
            return bool(str(change.get('flagKey') or '').strip())
        return False
    if change_type in COMBAT_STATE_CHANGE_TYPES:
        if change_type == 'combat.start':
            combat = change.get('combat') if isinstance(change.get('combat'), dict) else change
            return bool(isinstance(combat.get('participants'), list) and combat.get('participants'))
        if change_type in {'combat.update', 'combat.round.advance', 'combat.battlefield.update', 'combat.end'}:
            return True
        if change_type in {
            'combat.participant.update',
            'combat.intent.set',
            'combat.morale.update',
            'combat.morale.event',
            'combat.move',
            'combat.condition.add',
            'combat.condition.remove',
            'combat.ability.mark_used',
        }:
            return bool(str(change.get('participantId') or change.get('enemyId') or '').strip())
        return False
    if not change.get('actorId'):
        return False
    if change_type == 'inventory.add':
        item = change.get('item') if isinstance(change.get('item'), dict) else {}
        return bool(str(change.get('itemName') or item.get('name') or '').strip()) and 'quantity' in change
    if change_type == 'inventory.remove':
        return bool(str(change.get('itemId') or change.get('itemName') or '').strip()) and 'quantity' in change
    if change_type in {'inventory.equip', 'inventory.unequip'}:
        return bool(str(change.get('itemId') or change.get('itemName') or '').strip())
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
    if change_type in {'xp.add', 'xp.remove'}:
        return 'amount' in change
    if change_type == 'spell.learn':
        raw_spell = change.get('spell') if isinstance(change.get('spell'), dict) else {}
        return bool(str(change.get('spellName') or raw_spell.get('name') or '').strip())
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
