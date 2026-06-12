from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.creatures.schemas import DAMAGE_TYPES, normalize_creature_definition
from aidm_server.game_state.models import stable_slug


COMBAT_STATUSES = {'none', 'starting', 'active', 'ended'}
PARTICIPANT_TEAMS = {'player', 'ally', 'enemy', 'neutral'}
PARTICIPANT_KINDS = {'player_character', 'npc', 'creature', 'boss', 'minion'}
RANGE_BANDS = {'melee', 'near', 'far', 'distant'}
LIGHTING_VALUES = {'bright', 'dim', 'dark'}
VISIBILITY_VALUES = {'clear', 'fog', 'smoke', 'rain', 'magical_darkness'}
COVER_TYPES = {'half', 'three_quarters', 'full'}
ENVIRONMENT_TYPES = {
    'open_field',
    'forest',
    'dungeon_room',
    'cavern',
    'tavern',
    'city_street',
    'bridge',
    'ship',
    'boss_lair',
    'custom',
}


def _text(value: Any, default: str = '') -> str:
    text = str(value or '').strip()
    return text or default


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or '').strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {'0', 'false', 'no', 'off', 'unblocked'}


def _enum(value: Any, allowed: set[str], default: str) -> str:
    normalized = _text(value, default).lower().replace(' ', '_').replace('-', '_')
    return normalized if normalized in allowed else default


def _object_id(raw: dict[str, Any], fallback: str) -> str:
    return stable_slug(_text(raw.get('id') or raw.get('name'), fallback))


def _normalize_zone(value: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    zone_id = _object_id(value, f'zone_{index + 1}')
    return {
        'id': zone_id,
        'name': _text(value.get('name'), zone_id.replace('_', ' ').title())[:100],
        'description': _text(value.get('description'))[:500],
    }


def _normalize_damage(value: Any) -> dict[str, Any] | None:
    raw = value if isinstance(value, dict) else {}
    dice = _text(raw.get('dice'))
    if not dice:
        return None
    return {
        'dice': dice[:40],
        'type': _enum(raw.get('type'), DAMAGE_TYPES, 'bludgeoning'),
    }


def _normalize_hazard(value: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    hazard_id = _object_id(value, f'hazard_{index + 1}')
    hazard = {
        'id': hazard_id,
        'name': _text(value.get('name'), hazard_id.replace('_', ' ').title())[:100],
        'description': _text(value.get('description'))[:500],
        'effect': _text(value.get('effect'), 'hazardous terrain')[:160],
    }
    damage = _normalize_damage(value.get('damage'))
    if damage:
        hazard['damage'] = damage
    return hazard


def _normalize_cover(value: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    cover_id = _object_id(value, f'cover_{index + 1}')
    cover = {
        'id': cover_id,
        'name': _text(value.get('name'), cover_id.replace('_', ' ').title())[:100],
        'coverType': _enum(value.get('coverType', value.get('cover_type')), COVER_TYPES, 'half'),
    }
    if value.get('zoneId') or value.get('zone_id'):
        cover['zoneId'] = _text(value.get('zoneId') or value.get('zone_id'))[:100]
    return cover


def _normalize_exit(value: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    exit_id = _object_id(value, f'exit_{index + 1}')
    exit_item = {
        'id': exit_id,
        'name': _text(value.get('name'), exit_id.replace('_', ' ').title())[:100],
        'blocked': _bool(value.get('blocked'), default=False),
    }
    destination = _text(value.get('destinationLocationId', value.get('destination_location_id')))
    if destination:
        exit_item['destinationLocationId'] = destination[:120]
    return exit_item


def _normalize_interactable(value: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    interactable_id = _object_id(value, f'interactable_{index + 1}')
    return {
        'id': interactable_id,
        'name': _text(value.get('name'), interactable_id.replace('_', ' ').title())[:100],
        'description': _text(value.get('description'))[:500],
        'possibleUses': _string_list(value.get('possibleUses', value.get('possible_uses')))[:10],
    }


def _normalize_items(values: Any, normalizer) -> list[dict[str, Any]]:
    result = []
    for index, item in enumerate(values if isinstance(values, list) else []):
        normalized = normalizer(item, index)
        if normalized:
            result.append(normalized)
    return result


def default_battlefield(scene: dict[str, Any] | None = None) -> dict[str, Any]:
    scene = scene if isinstance(scene, dict) else {}
    scene_type = _text(scene.get('sceneType')).lower()
    name = _text(scene.get('name'))
    if scene_type == 'dungeon':
        environment = 'dungeon_room'
    elif scene_type == 'social' and 'tavern' in name.lower():
        environment = 'tavern'
    elif scene_type == 'travel':
        environment = 'open_field'
    elif 'forest' in name.lower() or 'woods' in name.lower():
        environment = 'forest'
    elif 'cave' in name.lower() or 'cavern' in name.lower():
        environment = 'cavern'
    else:
        environment = 'custom'
    return {
        'environmentType': environment,
        'zones': [],
        'hazards': [],
        'cover': [],
        'exits': [],
        'interactables': [],
        'lighting': 'dim' if scene_type in {'dungeon', 'mystery'} else 'bright',
        'visibility': 'clear',
    }


def normalize_battlefield(value: Any, scene: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    fallback = default_battlefield(scene)
    return {
        'environmentType': _enum(raw.get('environmentType', raw.get('environment_type')), ENVIRONMENT_TYPES, fallback['environmentType']),
        'zones': _normalize_items(raw.get('zones'), _normalize_zone),
        'hazards': _normalize_items(raw.get('hazards'), _normalize_hazard),
        'cover': _normalize_items(raw.get('cover'), _normalize_cover),
        'exits': _normalize_items(raw.get('exits'), _normalize_exit),
        'interactables': _normalize_items(raw.get('interactables'), _normalize_interactable),
        'lighting': _enum(raw.get('lighting'), LIGHTING_VALUES, fallback['lighting']),
        'visibility': _enum(raw.get('visibility'), VISIBILITY_VALUES, fallback['visibility']),
    }


def normalize_position(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    position = {'rangeBand': _enum(raw.get('rangeBand', raw.get('range_band')), RANGE_BANDS, 'near')}
    if raw.get('zoneId') or raw.get('zone_id'):
        position['zoneId'] = _text(raw.get('zoneId') or raw.get('zone_id'))
    if raw.get('coverId') or raw.get('cover_id'):
        position['coverId'] = _text(raw.get('coverId') or raw.get('cover_id'))
    if raw.get('isHidden') is not None or raw.get('is_hidden') is not None:
        position['isHidden'] = bool(raw.get('isHidden', raw.get('is_hidden')))
    return position


def player_combat_participant(player_actor: dict[str, Any]) -> dict[str, Any]:
    health = player_actor.get('health') if isinstance(player_actor.get('health'), dict) else {}
    stats = player_actor.get('stats') if isinstance(player_actor.get('stats'), dict) else {}
    return {
        'id': _text(player_actor.get('id')) or f"player_{player_actor.get('playerId') or 'unknown'}",
        'name': _text(player_actor.get('name') or player_actor.get('characterName'), 'Player'),
        'team': 'player',
        'kind': 'player_character',
        'level': max(1, int_or_default(player_actor.get('level'), default=1)),
        'hp': {
            'current': max(0, int_or_default(health.get('currentHp'), default=0)),
            'max': max(0, int_or_default(health.get('maxHp'), default=0)),
            'temp': max(0, int_or_default(health.get('tempHp'), default=0)),
        },
        'armorClass': int_or_default(stats.get('armorClass', stats.get('ac')), default=10),
        'stats': stats,
        'conditions': _string_list(health.get('conditions')),
        'position': normalize_position({'rangeBand': 'near'}),
        'abilities': [],
        'morale': 100,
        'isAlive': int_or_default(health.get('currentHp'), default=1) > 0,
        'isConscious': int_or_default(health.get('currentHp'), default=1) > 0,
    }


def instantiate_creature(
    definition: dict[str, Any],
    *,
    instance_id: str | None = None,
    team: str = 'enemy',
    position: dict[str, Any] | None = None,
    current_turn: int | None = None,
) -> dict[str, Any]:
    memory_seed = definition.get('combatMemorySeed') if isinstance(definition, dict) and isinstance(definition.get('combatMemorySeed'), dict) else {}
    creature = normalize_creature_definition(definition, source=definition.get('source') if isinstance(definition, dict) else None)
    participant_id = instance_id or f"enemy_{stable_slug(creature['name'])}_01"
    behavior = creature.get('behavior') if isinstance(creature.get('behavior'), dict) else {}
    return {
        'id': participant_id,
        'name': creature['name'],
        'team': _enum(team, PARTICIPANT_TEAMS, 'enemy'),
        'kind': 'boss' if creature.get('challengeTier') == 'boss' else 'creature',
        'creatureType': creature.get('creatureType'),
        'creatureTypeName': creature.get('creatureTypeName'),
        'definitionId': creature['id'],
        'aliases': deepcopy(creature.get('aliases') or []),
        'npcBinding': deepcopy(creature.get('npcBinding') or {}),
        'level': creature.get('level'),
        'challengeTier': creature.get('challengeTier'),
        'xpReward': creature.get('xpReward'),
        'hp': {
            'current': creature['stats']['maxHp'],
            'max': creature['stats']['maxHp'],
            'temp': 0,
        },
        'armorClass': creature['stats']['armorClass'],
        'stats': deepcopy(creature['stats']),
        'conditions': [],
        'position': normalize_position(position),
        'senses': deepcopy(creature.get('senses') or {}),
        'movement': deepcopy(creature.get('movement') or {}),
        'abilities': deepcopy(creature.get('abilities') or []),
        'behavior': deepcopy(behavior),
        'currentIntent': None,
        'memory': deepcopy(memory_seed),
        'morale': int_or_default(behavior.get('morale'), default=50),
        'isAlive': True,
        'isConscious': True,
        'createdAtTurn': current_turn,
        'source': creature.get('source'),
    }


def normalize_participant(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    hp = value.get('hp') if isinstance(value.get('hp'), dict) else {}
    participant_id = _text(value.get('id'))
    if not participant_id:
        return None
    current_hp = max(0, int_or_default(hp.get('current', hp.get('currentHp')), default=0))
    max_hp = max(current_hp, int_or_default(hp.get('max', hp.get('maxHp')), default=current_hp))
    return {
        **value,
        'id': participant_id,
        'name': _text(value.get('name'), participant_id),
        'team': _enum(value.get('team'), PARTICIPANT_TEAMS, 'enemy'),
        'kind': _enum(value.get('kind'), PARTICIPANT_KINDS, 'creature'),
        'creatureType': _text(value.get('creatureType', value.get('creature_type'))),
        'hp': {'current': current_hp, 'max': max_hp, 'temp': max(0, int_or_default(hp.get('temp'), default=0))},
        'conditions': _string_list(value.get('conditions')),
        'position': normalize_position(value.get('position')),
        'abilities': [item for item in (value.get('abilities') or []) if isinstance(item, dict)],
        'morale': max(0, min(100, int_or_default(value.get('morale'), default=50))),
        'isAlive': bool(value.get('isAlive', current_hp > 0)) and current_hp > 0,
        'isConscious': bool(value.get('isConscious', current_hp > 0)) and current_hp > 0,
    }


def normalize_combat_state(value: Any, scene: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    participants = [
        participant
        for item in (raw.get('participants') or [])
        if (participant := normalize_participant(item)) is not None
    ]
    status = _enum(raw.get('status'), COMBAT_STATUSES, 'none')
    if participants and status == 'none':
        status = 'active'
    return {
        'status': status,
        'round': max(1, int_or_default(raw.get('round'), default=1)),
        'turnIndex': int_or_default(raw.get('turnIndex', raw.get('turn_index')), default=0) if raw.get('turnIndex', raw.get('turn_index')) is not None else None,
        'participants': participants,
        'battlefield': normalize_battlefield(raw.get('battlefield'), scene),
        'encounterGoal': raw.get('encounterGoal', raw.get('encounter_goal')) if isinstance(raw.get('encounterGoal', raw.get('encounter_goal')), dict) else None,
        'initiative': [item for item in (raw.get('initiative') or []) if isinstance(item, dict)],
        'lastRoundSummary': _text(raw.get('lastRoundSummary', raw.get('last_round_summary'))),
        'flags': raw.get('flags') if isinstance(raw.get('flags'), dict) else {},
    }


def ensure_combat_state(state: dict[str, Any]) -> dict[str, Any]:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    combat = normalize_combat_state(state.get('combat'), scene)
    state['combat'] = combat
    return combat


def _participant_can_take_turn(participant: dict[str, Any]) -> bool:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    current_hp = int_or_default(hp.get('current'), default=1)
    return (
        participant.get('isAlive') is not False
        and participant.get('isConscious') is not False
        and current_hp > 0
        and participant.get('team') in {'player', 'ally', 'enemy'}
    )


def _turn_order_entry(participant: dict[str, Any], order_index: int) -> dict[str, Any]:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    return {
        'id': participant.get('id'),
        'name': participant.get('name') or participant.get('id'),
        'team': participant.get('team'),
        'kind': participant.get('kind'),
        'order': order_index,
        'hp': {
            'current': hp.get('current'),
            'max': hp.get('max'),
        },
    }


def combat_turn_order(combat: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = normalize_combat_state(combat)
    participants = [
        participant
        for participant in normalized.get('participants') or []
        if isinstance(participant, dict) and participant.get('id') and _participant_can_take_turn(participant)
    ]
    ordered = [
        *[participant for participant in participants if participant.get('team') in {'player', 'ally'}],
        *[participant for participant in participants if participant.get('team') == 'enemy'],
    ]
    return [_turn_order_entry(participant, index) for index, participant in enumerate(ordered)]


def _turn_index_for_actor(order: list[dict[str, Any]], actor_id: str | None) -> int | None:
    actor_id = str(actor_id or '').strip()
    if not actor_id:
        return None
    for index, entry in enumerate(order):
        if str(entry.get('id') or '') == actor_id:
            return index
    return None


def _turn_index_from_combat(combat: dict[str, Any], order: list[dict[str, Any]]) -> int | None:
    if not order:
        return None
    raw_index = combat.get('turnIndex')
    if raw_index is None:
        flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
        actor_index = _turn_index_for_actor(order, flags.get('activeActorId'))
        if actor_index is not None:
            return actor_index
    return int_or_default(raw_index, default=0) % len(order)


def combat_turn_context(combat: dict[str, Any], active_actor_id: str | None = None) -> dict[str, Any]:
    normalized = normalize_combat_state(combat)
    order = combat_turn_order(normalized)
    if not order:
        return {
            'mode': 'players_then_enemies',
            'turnOrder': [],
            'turnOrderIds': [],
            'turnOrderText': '',
            'turnIndex': None,
            'currentActor': None,
            'immediateNextActor': None,
            'enemyTurnBlock': [],
            'handoffActor': None,
            'nextTurnIndex': None,
            'nextRound': normalized.get('round') or 1,
            'turnInstruction': 'No eligible combat participants can take a turn.',
        }

    actor_index = _turn_index_for_actor(order, active_actor_id)
    current_index = actor_index if actor_index is not None else _turn_index_from_combat(normalized, order)
    if current_index is None:
        current_index = 0

    count = len(order)
    current_actor = order[current_index]
    immediate_next_index = (current_index + 1) % count
    immediate_next_actor = order[immediate_next_index]
    enemy_turn_block: list[dict[str, Any]] = []
    handoff_index = immediate_next_index

    if immediate_next_actor.get('team') == 'enemy':
        cursor = immediate_next_index
        visited = 0
        while visited < count and order[cursor].get('team') == 'enemy':
            enemy_turn_block.append(order[cursor])
            cursor = (cursor + 1) % count
            visited += 1
        if visited < count:
            handoff_index = cursor

    handoff_actor = order[handoff_index]
    steps_to_handoff = (handoff_index - current_index) % count
    if steps_to_handoff == 0:
        steps_to_handoff = count
    next_round = max(1, int_or_default(normalized.get('round'), default=1))
    if current_index + steps_to_handoff >= count:
        next_round += 1

    order_text = ' -> '.join(str(entry.get('name') or entry.get('id')) for entry in order)
    if enemy_turn_block:
        enemy_text = ', '.join(str(entry.get('name') or entry.get('id')) for entry in enemy_turn_block)
        turn_instruction = (
            f"Resolve only {current_actor.get('name')}'s submitted action first. Then resolve enemy turns in order: "
            f"{enemy_text}. After those enemy turns, hand the next player turn to {handoff_actor.get('name')}."
        )
    else:
        turn_instruction = (
            f"Resolve only {current_actor.get('name')}'s submitted action. Do not take enemy turns yet. "
            f"Hand the next combat turn to {handoff_actor.get('name')}."
        )

    return {
        'mode': 'players_then_enemies',
        'turnOrder': order,
        'turnOrderIds': [entry.get('id') for entry in order],
        'turnOrderText': order_text,
        'turnIndex': current_index,
        'currentActor': current_actor,
        'immediateNextActor': immediate_next_actor,
        'enemyTurnBlock': enemy_turn_block,
        'handoffActor': handoff_actor,
        'nextTurnIndex': handoff_index,
        'nextRound': next_round,
        'turnInstruction': turn_instruction,
    }


def combat_summary_for_dm(combat: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_combat_state(combat)
    turn_context = combat_turn_context(normalized)
    participants_by_id = {
        str(participant.get('id')): participant
        for participant in normalized.get('participants') or []
        if isinstance(participant, dict) and participant.get('id')
    }
    participants_summary = []
    telegraphs = []
    for participant in normalized.get('participants') or []:
        hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
        intent = participant.get('currentIntent') if isinstance(participant.get('currentIntent'), dict) else {}
        if participant.get('team') == 'enemy':
            position = participant.get('position') if isinstance(participant.get('position'), dict) else {}
            zone = f", zone {position.get('zoneId')}" if position.get('zoneId') else ''
            participants_summary.append(
                f"{participant.get('name')}: {hp.get('current')}/{hp.get('max')} HP, morale {participant.get('morale')}, {position.get('rangeBand', 'near')}{zone}"
            )
            if intent.get('visibleTelegraph'):
                telegraphs.append(str(intent.get('visibleTelegraph')))
        elif participant.get('team') == 'player':
            participants_summary.append(f"{participant.get('name')}: {hp.get('current')}/{hp.get('max')} HP")
    battlefield = normalized.get('battlefield') or {}
    intent_summaries = []
    required_actions = []
    for participant in normalized.get('participants') or []:
        if participant.get('team') != 'enemy' or not isinstance(participant.get('currentIntent'), dict):
            continue
        intent = participant.get('currentIntent') or {}
        target = participants_by_id.get(str(intent.get('targetId') or ''))
        target_name = target.get('name') if isinstance(target, dict) else None
        target_text = f" targeting {target_name}" if target_name else ''
        intent_summary = f"{participant.get('name')} -> {intent.get('intentType')}{target_text}: {intent.get('reason')}"
        intent_summaries.append(intent_summary)
        required_actions.append(
            {
                'enemyId': participant.get('id'),
                'enemyName': participant.get('name'),
                'intentType': intent.get('intentType'),
                'targetId': intent.get('targetId'),
                'targetName': target_name,
                'reason': intent.get('reason'),
                'telegraph': intent.get('visibleTelegraph'),
                'brainSource': intent.get('brainSource'),
                'selectionMethod': intent.get('selectionMethod'),
            }
        )
    return {
        'status': normalized.get('status'),
        'round': normalized.get('round'),
        'battlefield': f"{battlefield.get('lighting', 'bright')} {battlefield.get('environmentType', 'custom')} with {battlefield.get('visibility', 'clear')} visibility",
        'participantsSummary': participants_summary[:12],
        'enemyIntentSummary': ' '.join(intent_summaries[:6]),
        'enemyRequiredActions': required_actions[:6],
        'enemyTelegraphs': telegraphs[:6],
        'encounterGoal': normalized.get('encounterGoal'),
        'turnOrderMode': turn_context.get('mode'),
        'turnOrder': turn_context.get('turnOrder'),
        'turnOrderText': turn_context.get('turnOrderText'),
        'currentTurn': turn_context.get('currentActor'),
        'nextActor': turn_context.get('immediateNextActor'),
        'enemyTurnBlock': turn_context.get('enemyTurnBlock'),
        'handoffActor': turn_context.get('handoffActor'),
        'turnInstruction': turn_context.get('turnInstruction'),
    }
