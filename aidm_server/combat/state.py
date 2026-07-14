from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from aidm_server.armor_class import armor_class_details
from aidm_server.canon_text import int_or_default
from aidm_server.creatures.schemas import DAMAGE_TYPES, normalize_creature_definition
from aidm_server.damage_dice import normalize_damage_dice_expression
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
TURN_EXCLUDING_CONDITIONS = {
    'dead',
    'fled',
    'escaped',
    'retreated',
    'withdrawn',
    'surrendered',
    'yielded',
    'unconscious',
    'incapacitated',
    'paralyzed',
    'stunned',
    'absent',
}
TARGET_EXCLUDING_CONDITIONS = {
    'dead',
    'fled',
    'escaped',
    'retreated',
    'withdrawn',
    'surrendered',
    'yielded',
    'unconscious',
    'absent',
}
LIMITED_USE_COOLDOWNS = {'once_per_combat', 'short_rest', 'long_rest'}
TURN_ECONOMY_VERSION = 1


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
    dice = normalize_damage_dice_expression(raw.get('dice'))
    if not dice:
        return None
    return {
        'dice': dice,
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
    stats = dict(player_actor.get('stats') if isinstance(player_actor.get('stats'), dict) else {})
    inventory = player_actor.get('inventory') if isinstance(player_actor.get('inventory'), dict) else {}
    inventory_items = inventory.get('items') if isinstance(inventory.get('items'), list) else []
    ac_details = armor_class_details(stats, inventory_items)
    stats['armorClass'] = ac_details['armorClass']
    stats['armor_class'] = ac_details['armorClass']
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
        'armorClass': ac_details['armorClass'],
        'stats': stats,
        'savingThrowProficiencies': _string_list(
            player_actor.get('savingThrowProficiencies')
            or player_actor.get('saving_throw_proficiencies')
        ),
        'armorClassBreakdown': ac_details,
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
        'resistances': deepcopy(creature.get('resistances') or []),
        'vulnerabilities': deepcopy(creature.get('vulnerabilities') or []),
        'immunities': deepcopy(creature.get('immunities') or []),
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


def _initiative_modifier(participant: dict[str, Any]) -> int:
    stats = participant.get('stats') if isinstance(participant.get('stats'), dict) else {}
    explicit = stats.get('initiative', participant.get('initiativeModifier'))
    if explicit is not None:
        return max(-20, min(20, int_or_default(explicit, default=0)))
    dexterity = int_or_default(
        stats.get('dexterity', stats.get('dex', participant.get('dexterity'))),
        default=10,
    )
    return max(-20, min(20, (dexterity - 10) // 2))


def _deterministic_initiative_roll(seed: str, participant_id: str) -> int:
    digest = hashlib.sha256(f'{seed}:{participant_id}'.encode('utf-8')).digest()
    return int.from_bytes(digest[:4], 'big') % 20 + 1


def build_combat_initiative(
    participants: list[dict[str, Any]],
    *,
    seed: Any,
    source: str = 'server_deterministic',
) -> list[dict[str, Any]]:
    """Create one stable, explicit initiative result for every participant."""

    normalized_seed = _text(seed, 'combat')
    entries: list[dict[str, Any]] = []
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        participant_id = _text(participant.get('id'))
        if not participant_id:
            continue
        modifier = _initiative_modifier(participant)
        roll = _deterministic_initiative_roll(normalized_seed, participant_id)
        entries.append(
            {
                'participantId': participant_id,
                'name': _text(participant.get('name'), participant_id),
                'roll': roll,
                'modifier': modifier,
                'total': roll + modifier,
                'source': source,
            }
        )
    entries.sort(
        key=lambda entry: (
            -int(entry['total']),
            -int(entry['modifier']),
            str(entry['participantId']),
        )
    )
    for order, entry in enumerate(entries):
        entry['order'] = order
    return entries


def _legacy_combat_initiative(participants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Upgrade old snapshots without changing their historical roster order."""

    entries: list[dict[str, Any]] = []
    for order, participant in enumerate(participants):
        participant_id = _text(participant.get('id'))
        if not participant_id:
            continue
        roll = max(1, 20 - order)
        entries.append(
            {
                'participantId': participant_id,
                'name': _text(participant.get('name'), participant_id),
                'roll': roll,
                'modifier': 0,
                'total': roll,
                'order': order,
                'source': 'legacy_roster_order',
            }
        )
    return entries


def _normalize_combat_initiative(
    value: Any,
    participants: list[dict[str, Any]],
    *,
    seed: Any = None,
    generate: bool = False,
) -> list[dict[str, Any]]:
    participants_by_id = {
        _text(participant.get('id')): participant
        for participant in participants
        if isinstance(participant, dict) and _text(participant.get('id'))
    }
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        participant_id = _text(raw.get('participantId', raw.get('participant_id', raw.get('id'))))
        if participant_id not in participants_by_id or participant_id in seen:
            continue
        participant = participants_by_id[participant_id]
        modifier = max(-20, min(20, int_or_default(raw.get('modifier'), default=_initiative_modifier(participant))))
        roll = max(1, min(20, int_or_default(raw.get('roll', raw.get('initiative')), default=10)))
        total = max(-20, min(60, int_or_default(raw.get('total'), default=roll + modifier)))
        normalized.append(
            {
                'participantId': participant_id,
                'name': _text(raw.get('name'), _text(participant.get('name'), participant_id)),
                'roll': roll,
                'modifier': modifier,
                'total': total,
                'order': max(0, int_or_default(raw.get('order'), default=len(normalized))),
                'source': _text(raw.get('source'), 'persisted'),
            }
        )
        seen.add(participant_id)

    if not normalized:
        if generate:
            return build_combat_initiative(participants, seed=seed or 'combat')
        return _legacy_combat_initiative(participants)

    if len(seen) != len(participants_by_id):
        generated = build_combat_initiative(
            [participant for participant_id, participant in participants_by_id.items() if participant_id not in seen],
            seed=seed or 'combat_reinforcement',
        )
        next_order = max((int(entry.get('order', 0)) for entry in normalized), default=-1) + 1
        for offset, entry in enumerate(generated):
            entry['order'] = next_order + offset
        normalized.extend(generated)
    normalized.sort(key=lambda entry: (int(entry.get('order', 0)), -int(entry.get('total', 0)), str(entry.get('participantId'))))
    for order, entry in enumerate(normalized):
        entry['order'] = order
    return normalized


def default_turn_economy(actor_id: Any, round_number: Any) -> dict[str, Any]:
    return {
        'version': TURN_ECONOMY_VERSION,
        'actorId': _text(actor_id),
        'round': max(1, int_or_default(round_number, default=1)),
        'actionRemaining': 1,
        'bonusActionRemaining': 1,
        'reactionRemaining': 1,
        'movementRemaining': 1,
        'spentActionIds': [],
        'spentActionClaims': {},
    }


def _normalized_turn_economy(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    actor_id = _text(value.get('actorId', value.get('actor_id')))
    if not actor_id:
        return None
    spent_action_ids = []
    for raw_id in value.get('spentActionIds', value.get('spent_action_ids')) or []:
        action_id = _text(raw_id)
        if action_id and action_id not in spent_action_ids:
            spent_action_ids.append(action_id)
    raw_claims = value.get('spentActionClaims', value.get('spent_action_claims'))
    spent_action_claims = {
        _text(action_id): _text(claim)
        for action_id, claim in (raw_claims.items() if isinstance(raw_claims, dict) else [])
        if _text(action_id) and _text(claim)
    }
    return {
        'version': TURN_ECONOMY_VERSION,
        'actorId': actor_id,
        'round': max(1, int_or_default(value.get('round'), default=1)),
        'actionRemaining': max(0, min(1, int_or_default(value.get('actionRemaining'), default=1))),
        'bonusActionRemaining': max(0, min(1, int_or_default(value.get('bonusActionRemaining'), default=1))),
        'reactionRemaining': max(0, min(1, int_or_default(value.get('reactionRemaining'), default=1))),
        'movementRemaining': max(0, min(1, int_or_default(value.get('movementRemaining'), default=1))),
        'spentActionIds': spent_action_ids[:20],
        'spentActionClaims': {
            action_id: spent_action_claims[action_id]
            for action_id in spent_action_ids[:20]
            if action_id in spent_action_claims
        },
    }


def ensure_combat_turn_economy(
    combat: dict[str, Any],
    *,
    actor_id: Any,
    round_number: Any = None,
    force_reset: bool = False,
) -> dict[str, Any]:
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    combat['flags'] = flags
    current = _normalized_turn_economy(flags.get('turnEconomy'))
    expected_actor_id = _text(actor_id)
    expected_round = max(1, int_or_default(round_number, default=int_or_default(combat.get('round'), default=1)))
    if (
        force_reset
        or current is None
        or current.get('actorId') != expected_actor_id
        or int(current.get('round') or 1) != expected_round
    ):
        current = default_turn_economy(expected_actor_id, expected_round)
    flags['turnEconomy'] = current
    return current


def combat_turn_economy(combat: dict[str, Any], *, actor_id: Any = None) -> dict[str, Any] | None:
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    economy = _normalized_turn_economy(flags.get('turnEconomy'))
    if economy is None:
        return None
    expected_actor_id = _text(actor_id)
    if expected_actor_id and economy.get('actorId') != expected_actor_id:
        return None
    return economy


def consume_combat_turn_economy(
    combat: dict[str, Any],
    *,
    actor_id: Any,
    action_id: Any,
    action_claim: Any = None,
    action_cost: int = 0,
    bonus_action_cost: int = 0,
    reaction_cost: int = 0,
    movement_cost: int = 0,
) -> tuple[bool, str, dict[str, Any]]:
    economy = ensure_combat_turn_economy(combat, actor_id=actor_id)
    normalized_action_id = _text(action_id)
    normalized_action_claim = _text(action_claim)
    # A retried request for the same server-issued action is a replay, not a
    # second action.  The state-change ledger remains responsible for making
    # the action's downstream effects idempotent.
    if normalized_action_id and normalized_action_id in economy['spentActionIds']:
        prior_claim = _text(economy.get('spentActionClaims', {}).get(normalized_action_id))
        if prior_claim and normalized_action_claim and prior_claim != normalized_action_claim:
            return False, 'This action id is already bound to a different combat action.', economy
        return True, '', economy
    costs = {
        'actionRemaining': max(0, min(1, int_or_default(action_cost, default=0))),
        'bonusActionRemaining': max(0, min(1, int_or_default(bonus_action_cost, default=0))),
        'reactionRemaining': max(0, min(1, int_or_default(reaction_cost, default=0))),
        'movementRemaining': max(0, min(1, int_or_default(movement_cost, default=0))),
    }
    labels = {
        'actionRemaining': 'action',
        'bonusActionRemaining': 'bonus action',
        'reactionRemaining': 'reaction',
        'movementRemaining': 'movement',
    }
    for key, cost in costs.items():
        if cost and int(economy.get(key) or 0) < cost:
            return False, f"This turn's {labels[key]} is already spent.", economy
    for key, cost in costs.items():
        if cost:
            economy[key] = max(0, int(economy.get(key) or 0) - cost)
    if normalized_action_id and any(costs.values()) and normalized_action_id not in economy['spentActionIds']:
        economy['spentActionIds'].append(normalized_action_id)
        if normalized_action_claim:
            economy['spentActionClaims'][normalized_action_id] = normalized_action_claim
    combat['flags']['turnEconomy'] = economy
    return True, '', economy


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
    flags = dict(raw.get('flags')) if isinstance(raw.get('flags'), dict) else {}
    initiative_seed = flags.get('initiativeSeed', flags.get('initiative_seed'))
    return {
        'status': status,
        'round': max(1, int_or_default(raw.get('round'), default=1)),
        'turnIndex': int_or_default(raw.get('turnIndex', raw.get('turn_index')), default=0) if raw.get('turnIndex', raw.get('turn_index')) is not None else None,
        'participants': participants,
        'battlefield': normalize_battlefield(raw.get('battlefield'), scene),
        'encounterGoal': raw.get('encounterGoal', raw.get('encounter_goal')) if isinstance(raw.get('encounterGoal', raw.get('encounter_goal')), dict) else None,
        'initiative': _normalize_combat_initiative(
            raw.get('initiative'),
            participants,
            seed=initiative_seed,
            generate=bool(initiative_seed),
        ),
        'lastRoundSummary': _text(raw.get('lastRoundSummary', raw.get('last_round_summary'))),
        'flags': flags,
    }


def ensure_combat_state(state: dict[str, Any]) -> dict[str, Any]:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    combat = normalize_combat_state(state.get('combat'), scene)
    state['combat'] = combat
    return combat


def participant_condition_keys(participant: dict[str, Any]) -> set[str]:
    return {
        _text(condition).lower().replace(' ', '_').replace('-', '_')
        for condition in participant.get('conditions') or []
        if _text(condition)
    }


def participant_is_present(participant: dict[str, Any]) -> bool:
    return (
        participant.get('isPresent', participant.get('present', True)) is not False
        and 'absent' not in participant_condition_keys(participant)
    )


def participant_is_targetable(participant: dict[str, Any]) -> bool:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    current_hp = int_or_default(hp.get('current'), default=1)
    return (
        participant.get('isAlive') is not False
        and participant.get('isConscious') is not False
        and participant_is_present(participant)
        and current_hp > 0
        and not participant_condition_keys(participant).intersection(TARGET_EXCLUDING_CONDITIONS)
    )


def participant_can_take_turn(participant: dict[str, Any]) -> bool:
    return (
        participant_is_targetable(participant)
        and participant.get('team') in {'player', 'ally', 'enemy'}
        and not participant_condition_keys(participant).intersection(TURN_EXCLUDING_CONDITIONS)
    )


def combat_ability_is_available(ability: dict[str, Any], *, round_number: Any = None) -> bool:
    if not isinstance(ability, dict) or ability.get('available') is False:
        return False
    if ability.get('usesRemaining') is not None and int_or_default(ability.get('usesRemaining'), default=0) <= 0:
        return False
    cooldown = _text(ability.get('cooldown')).lower().replace(' ', '_').replace('-', '_')
    if cooldown in LIMITED_USE_COOLDOWNS and ability.get('used') is True:
        return False
    if cooldown == 'turn' and round_number is not None:
        return int_or_default(ability.get('lastUsedRound'), default=-1) != int_or_default(round_number, default=0)
    return True


def combat_ability_resolution_mode(ability: dict[str, Any]) -> str:
    """Describe the small enemy-ability subset the authoritative engine resolves.

    Multi-target and underspecified narrative abilities fail closed instead of
    being converted into fabricated weapon damage.
    """

    if not isinstance(ability, dict):
        return ''
    target_type = _text(ability.get('targetType', ability.get('target_type')), 'single').lower().replace(' ', '_')
    if target_type not in {'single', 'one', 'creature'}:
        return ''
    damage = ability.get('damage')
    declares_damage = bool(
        _text(damage)
        if not isinstance(damage, dict)
        else _text(damage.get('dice') or damage.get('amount'))
    )
    conditions = ability.get('conditionsApplied', ability.get('conditions'))
    declares_condition = bool(
        [condition for condition in (conditions or []) if _text(condition)]
        if isinstance(conditions, list)
        else _text(conditions)
    )
    save = ability.get('save') if isinstance(ability.get('save'), dict) else None
    ability_type = _text(ability.get('type')).lower().replace(' ', '_')
    explicit_attack = ability_type == 'attack' or ability.get('attackBonus', ability.get('toHitBonus')) is not None
    # Explicit attack abilities may declare bounded damage in their prose; the
    # resolver performs that strict parse and still fails closed if absent.
    if explicit_attack:
        return 'attack_with_save' if save and declares_condition else 'attack'
    if save and (declares_damage or declares_condition):
        return 'save'
    return ''


def _turn_order_entry(
    participant: dict[str, Any],
    order_index: int,
    initiative: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    return {
        'id': participant.get('id'),
        'name': participant.get('name') or participant.get('id'),
        'team': participant.get('team'),
        'kind': participant.get('kind'),
        'order': order_index,
        'initiative': deepcopy(initiative) if isinstance(initiative, dict) else None,
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
        if isinstance(participant, dict) and participant.get('id') and participant_can_take_turn(participant)
    ]
    initiative_by_id = {
        _text(entry.get('participantId', entry.get('id'))): entry
        for entry in normalized.get('initiative') or []
        if isinstance(entry, dict) and _text(entry.get('participantId', entry.get('id')))
    }
    participants.sort(
        key=lambda participant: (
            int((initiative_by_id.get(_text(participant.get('id'))) or {}).get('order', 9999)),
            _text(participant.get('id')),
        )
    )
    return [
        _turn_order_entry(participant, index, initiative_by_id.get(_text(participant.get('id'))))
        for index, participant in enumerate(participants)
    ]


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
    # turnIndex is positional, so it becomes stale whenever the eligible roster
    # is compacted by defeat, fleeing, surrender, absence, etc.  In that case
    # activeActorId is the stable identity for an in-progress turn.  When no
    # compaction occurred, retain the historical turnIndex authority so a stale
    # or contradictory legacy flag cannot silently switch actors.
    participant_count = sum(
        1
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and participant.get('id')
    )
    if len(order) < participant_count:
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
            'mode': 'initiative_order_with_enemy_blocks',
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

    if current_actor.get('team') == 'enemy':
        cursor = current_index
        visited = 0
        while visited < count and order[cursor].get('team') == 'enemy':
            enemy_turn_block.append(order[cursor])
            cursor = (cursor + 1) % count
            visited += 1
        if visited < count:
            handoff_index = cursor
    elif immediate_next_actor.get('team') == 'enemy':
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
    if current_actor.get('team') == 'enemy' and enemy_turn_block:
        enemy_text = ', '.join(str(entry.get('name') or entry.get('id')) for entry in enemy_turn_block)
        turn_instruction = (
            f"Resolve enemy turns in initiative order: {enemy_text}. "
            f"After those enemy turns, hand the next player turn to {handoff_actor.get('name')}."
        )
    elif enemy_turn_block:
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
        'mode': 'initiative_order_with_enemy_blocks',
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
                'abilityId': intent.get('abilityId'),
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
