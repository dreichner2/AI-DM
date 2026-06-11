from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.creatures.schemas import normalize_creature_definition
from aidm_server.game_state.models import stable_slug


COMBAT_STATUSES = {'none', 'starting', 'active', 'ended'}
PARTICIPANT_TEAMS = {'player', 'ally', 'enemy', 'neutral'}
PARTICIPANT_KINDS = {'player_character', 'npc', 'creature', 'boss', 'minion'}
RANGE_BANDS = {'melee', 'near', 'far', 'distant'}
LIGHTING_VALUES = {'bright', 'dim', 'dark'}
VISIBILITY_VALUES = {'clear', 'fog', 'smoke', 'rain', 'magical_darkness'}
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


def _enum(value: Any, allowed: set[str], default: str) -> str:
    normalized = _text(value, default).lower().replace(' ', '_').replace('-', '_')
    return normalized if normalized in allowed else default


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
        'zones': [item for item in (raw.get('zones') or []) if isinstance(item, dict)],
        'hazards': [item for item in (raw.get('hazards') or []) if isinstance(item, dict)],
        'cover': [item for item in (raw.get('cover') or []) if isinstance(item, dict)],
        'exits': [item for item in (raw.get('exits') or []) if isinstance(item, dict)],
        'interactables': [item for item in (raw.get('interactables') or []) if isinstance(item, dict)],
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
    creature = normalize_creature_definition(definition, source=definition.get('source') if isinstance(definition, dict) else None)
    participant_id = instance_id or f"enemy_{stable_slug(creature['name'])}_01"
    behavior = creature.get('behavior') if isinstance(creature.get('behavior'), dict) else {}
    return {
        'id': participant_id,
        'name': creature['name'],
        'team': _enum(team, PARTICIPANT_TEAMS, 'enemy'),
        'kind': 'boss' if creature.get('challengeTier') == 'boss' else 'creature',
        'creatureType': creature.get('creatureType'),
        'definitionId': creature['id'],
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
        'memory': {},
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


def combat_summary_for_dm(combat: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_combat_state(combat)
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
    }
