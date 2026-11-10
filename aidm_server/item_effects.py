"""Consumable items whose rules effects the server resolves before narration.

An inventory item has an effect when ``item.metadata.effectId`` names an entry of
:data:`ITEM_EFFECTS`, when its normalised name or an alias matches one, or (for
spell scrolls) when it is named ``Scroll of <Spell>`` or carries
``metadata.spellId``.  :func:`resolve_item_use` is pure over a state snapshot and
draws every die from the roller it is given, so a recorded roll list replays to
the same result.  In combat the same options are offered as ``item.use.<itemId>``
legal actions through the combat action-provider registry.
"""

from __future__ import annotations

import re
import secrets
from copy import deepcopy
from typing import Any, Callable, Mapping

from aidm_server.canon_text import int_or_default
from aidm_server.character_progression import proficiency_bonus_for_level
from aidm_server.character_state import SPELLCASTING_ABILITY_BY_CLASS
from aidm_server.combat import vitality
from aidm_server.combat.legal_actions import register_action_provider, register_action_resolver
from aidm_server.combat.modifiers import (
    armor_class_with_context,
    attack_block_reason,
    attack_context,
    can_act,
    condition_key,
    damage_defense,
    is_hidden,
)
from aidm_server.combat.state import (
    combat_turn_context,
    combat_turn_economy,
    default_turn_economy,
    normalize_combat_state,
    participant_is_present,
    participant_is_targetable,
)
from aidm_server.damage_dice import parse_damage_dice_expression, roll_signed_dice
from aidm_server.game_state.models import actor_items, actor_name, display_actor_id, find_actor, normalize_item_name
from aidm_server.price_list import price_key
from aidm_server.spell_effects import resolve_targeted_spell
from aidm_server.spellbook import (
    CLASS_LEVEL_UNLOCKS,
    CLASS_STARTING_SPELLS,
    authoritative_spell_effect,
    class_spell_archetype,
    spell_payload,
)


Roller = Callable[[int], int]

ITEM_USE_ACTION_PREFIX = 'item.use.'
ITEM_USE_SOURCE = 'pre_dm_item_action'
ITEM_EFFECT_AUTHORITY = 'item_effect_engine'
RANGE_BAND_ORDER = ('melee', 'near', 'far', 'distant')
SCROLL_NAME_PATTERN = re.compile(r'^scroll of (?P<spell>.+)$')
COST_LABELS = {'action': 'action', 'bonus_action': 'bonus action'}
FRIENDLY_TEAMS = frozenset({'player', 'ally'})

ITEM_EFFECTS: dict[str, dict[str, Any]] = {
    'potion_healing': {
        'name': 'Potion of Healing',
        'matches': ('potion of healing', 'healing potion'),
        'kind': 'healing',
        'dice': '2d4+2',
    },
    'potion_greater_healing': {
        'name': 'Potion of Greater Healing',
        'matches': ('potion of greater healing',),
        'kind': 'healing',
        'dice': '4d4+4',
    },
    'potion_superior_healing': {
        'name': 'Potion of Superior Healing',
        'matches': ('potion of superior healing',),
        'kind': 'healing',
        'dice': '8d4+8',
    },
    'antitoxin': {'name': 'Antitoxin', 'matches': ('antitoxin',), 'kind': 'antitoxin'},
    'healers_kit': {'name': "Healer's Kit", 'matches': ('healers kit',), 'kind': 'stabilize', 'uses': 10},
    'holy_water': {
        'name': 'Holy Water',
        'matches': ('holy water',),
        'kind': 'thrown',
        'damage': {'dice': '2d6', 'type': 'radiant'},
        'harmsCreatureTypes': ('undead', 'fiend'),
    },
    'alchemists_fire': {
        'name': "Alchemist's Fire",
        'matches': ('alchemists fire',),
        'kind': 'thrown',
        'damage': {'dice': '1d4', 'type': 'fire'},
        'burning': {'dice': '1d4', 'type': 'fire', 'turns': 2},
    },
    'acid_vial': {
        'name': 'Acid',
        'matches': ('acid', 'vial of acid'),
        'kind': 'thrown',
        'damage': {'dice': '2d6', 'type': 'acid'},
    },
    'spell_scroll': {'name': 'Spell Scroll', 'matches': (), 'kind': 'scroll'},
}
_EFFECT_BY_NAME = {
    price_key(name): effect_id
    for effect_id, effect in ITEM_EFFECTS.items()
    for name in effect['matches']
}


def _text(value: Any) -> str:
    return str(value or '').strip()


# Catalog matching -----------------------------------------------------------


def _metadata(item: Mapping[str, Any]) -> Mapping[str, Any]:
    return item.get('metadata') if isinstance(item.get('metadata'), Mapping) else {}


def _item_names(item: Mapping[str, Any]) -> list[str]:
    return [_text(item.get('name')), *[_text(alias) for alias in item.get('aliases') or []]]


def _class_spell_levels(archetype: str | None) -> dict[str, int]:
    specs = [*CLASS_STARTING_SPELLS.get(archetype or '', [])]
    for unlocked in CLASS_LEVEL_UNLOCKS.get(archetype or '', {}).values():
        specs.extend(unlocked)
    return {price_key(name): int(level) for name, level, _description in specs}


def _all_spell_levels() -> dict[str, int]:
    levels: dict[str, int] = {}
    for archetype in CLASS_STARTING_SPELLS:
        levels.update(_class_spell_levels(archetype))
    return levels


def scroll_spell(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """``{"name", "key", "level"}`` of the spell a scroll holds, if the item is a scroll."""

    spell_id = _text(_metadata(item).get('spellId'))
    if spell_id:
        name = spell_id.removeprefix('spell_').replace('_', ' ')
    else:
        match = next(
            (match for value in _item_names(item) if (match := SCROLL_NAME_PATTERN.match(normalize_item_name(value)))),
            None,
        )
        if match is None:
            return None
        name = match.group('spell')
    key = price_key(name)
    if not key:
        return None
    level = _all_spell_levels().get(key)
    return {'name': name.title(), 'key': key, 'level': level}


def item_effect_id(item: Mapping[str, Any] | None) -> str | None:
    """The catalog effect id of an inventory item, or ``None`` for DM-adjudicated items."""

    if not isinstance(item, Mapping):
        return None
    explicit = _text(_metadata(item).get('effectId'))
    if explicit in ITEM_EFFECTS:
        return explicit
    if scroll_spell(item) is not None:
        return 'spell_scroll'
    return next((_EFFECT_BY_NAME[key] for value in _item_names(item) if (key := price_key(value)) in _EFFECT_BY_NAME), None)


def item_name_has_effect(item_name: Any) -> bool:
    return item_effect_id({'name': item_name}) is not None


def uses_remaining(item: Mapping[str, Any]) -> int | None:
    effect = ITEM_EFFECTS.get(item_effect_id(item) or '') or {}
    if not effect.get('uses'):
        return None
    return max(0, int_or_default(_metadata(item).get('usesRemaining'), default=int(effect['uses'])))


def _scroll_reader_problem(spell: Mapping[str, Any] | None, class_name: Any) -> str:
    if not spell or authoritative_spell_effect(spell['key']) is None:
        return 'That scroll holds no spell the rules engine can cast.'
    if spell['key'] not in _class_spell_levels(class_spell_archetype(_text(class_name))):
        return f"{spell['name']} is not on your class's spell list."
    return ''


def _scroll_definition(spell: Mapping[str, Any], class_name: Any) -> dict[str, Any]:
    definition = spell_payload(spell['name'], level=int(spell.get('level') or 0))
    delivery = definition.get('delivery') if isinstance(definition.get('delivery'), dict) else {}
    casting_ability = SPELLCASTING_ABILITY_BY_CLASS.get(class_spell_archetype(_text(class_name)) or '')
    if casting_ability and _text(delivery.get('type')).lower() in {'attack', 'save'}:
        definition['delivery'] = {**delivery, 'castingAbility': casting_ability}
    return definition


def _summary(effect_id: str, item: Mapping[str, Any]) -> str:
    effect = ITEM_EFFECTS[effect_id]
    kind = effect['kind']
    if kind == 'healing':
        return f"Restores {effect['dice']} hit points: a bonus action to drink, an action to give to an ally nearby."
    if kind == 'antitoxin':
        return 'Ends poisoned and grants advantage on Constitution saves until the encounter ends (out of combat, until your next rest).'
    if kind == 'stabilize':
        return f'Stabilises a dying ally nearby ({uses_remaining(item)} uses left).'
    if kind == 'thrown':
        damage = effect['damage']
        text = f"Thrown: {damage['dice']} {damage['type']} damage"
        if effect.get('harmsCreatureTypes'):
            text += f" to {' or '.join(effect['harmsCreatureTypes'])} only"
        if effect.get('burning'):
            burning = effect['burning']
            text += f", then {burning['dice']} {burning['type']} at the start of its next {burning['turns']} turns"
        return f'{text}.'
    spell = scroll_spell(item) or {'name': 'a spell'}
    return f"Casts {spell['name']} once at its base level without a spell slot."


def _self_cost(effect_id: str, item: Mapping[str, Any]) -> str:
    if ITEM_EFFECTS[effect_id]['kind'] == 'healing':
        return 'bonus_action'
    if effect_id == 'spell_scroll':
        spell = scroll_spell(item)
        definition = spell_payload(spell['name']) if spell else {}
        return 'bonus_action' if _text(definition.get('castingTime')).lower() == 'bonus_action' else 'action'
    return 'action'


def _target_rule(effect_id: str, item: Mapping[str, Any]) -> str:
    kind = ITEM_EFFECTS[effect_id]['kind']
    if kind == 'healing':
        return 'self_or_ally'
    if kind == 'antitoxin':
        return 'self'
    if kind == 'stabilize':
        return 'dying_ally'
    if kind == 'thrown':
        return 'enemy'
    spell = scroll_spell(item)
    target = (authoritative_spell_effect(spell['key']) or {}).get('target', {}) if spell else {}
    return f"spell_{_text(target.get('relation')).lower() or 'enemy'}"


def item_effect_descriptor(item: Mapping[str, Any], *, class_name: Any = None) -> dict[str, Any] | None:
    """What a client needs to offer an item: summary, costs, target rule, uses."""

    effect_id = item_effect_id(item)
    if effect_id is None:
        return None
    descriptor: dict[str, Any] = {
        'id': effect_id,
        'summary': _summary(effect_id, item),
        'cost': _self_cost(effect_id, item),
        'allyCost': 'action' if ITEM_EFFECTS[effect_id]['kind'] == 'healing' else None,
        'target': _target_rule(effect_id, item),
    }
    remaining = uses_remaining(item)
    if remaining is not None:
        descriptor['usesRemaining'] = remaining
    if effect_id == 'spell_scroll':
        spell = scroll_spell(item)
        descriptor['spell'] = spell
        problem = _scroll_reader_problem(spell, class_name)
        if problem:
            descriptor['unusableReason'] = problem
    return descriptor


# Targets and legality ------------------------------------------------------


def _band(record: Mapping[str, Any]) -> str:
    position = record.get('position') if isinstance(record.get('position'), Mapping) else {}
    band = _text(position.get('rangeBand') or position.get('range_band')).lower()
    return band if band in RANGE_BAND_ORDER else 'near'


def _bands_apart(first: Mapping[str, Any], second: Mapping[str, Any]) -> int:
    return abs(RANGE_BAND_ORDER.index(_band(first)) - RANGE_BAND_ORDER.index(_band(second)))


def _active_combat(state: Mapping[str, Any]) -> dict[str, Any] | None:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    combat = normalize_combat_state(state.get('combat'), scene)
    return combat if _text(combat.get('status')).lower() in {'starting', 'active'} else None


def _participant(combat: Mapping[str, Any], participant_id: Any) -> dict[str, Any] | None:
    wanted = _text(participant_id)
    return next(
        (
            participant
            for participant in combat.get('participants') or []
            if isinstance(participant, dict) and _text(participant.get('id')) == wanted
        ),
        None,
    )


def _option(record: Mapping[str, Any], *, cost: str | None, reason: str = '') -> dict[str, Any]:
    return {
        'id': _text(record.get('id')),
        'name': _text(record.get('name')) or _text(record.get('id')),
        'rangeBand': _band(record),
        'cost': cost,
        'available': not reason,
        'reason': reason,
    }


def _present_party(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    active_ids = {int_or_default(value, default=0) for value in state.get('activePlayerIds') or []}
    return [
        actor
        for actor in state.get('playerCharacters') or []
        if isinstance(actor, dict) and (not active_ids or actor.get('playerId') in active_ids)
    ]


def _economy_reason(economy: Mapping[str, Any], cost: str | None) -> str:
    if cost == 'action' and int_or_default(economy.get('actionRemaining'), default=0) < 1:
        return "This turn's action is already spent."
    if cost == 'bonus_action' and int_or_default(economy.get('bonusActionRemaining'), default=0) < 1:
        return "This turn's bonus action is already spent."
    return ''


def _ally_reason(rule: str, actor: Mapping[str, Any], target: Mapping[str, Any], *, in_combat: bool) -> str:
    if rule == 'self_or_ally' and vitality.is_dead(target):
        return 'Healing cannot restore the dead.'
    if rule == 'dying_ally' and not vitality.is_dying(target):
        return 'Not dying.'
    if in_combat and _text(target.get('id')) != _text(actor.get('id')) and _bands_apart(actor, target) > 1:
        return 'Too far away; an ally must be in the same or an adjacent range band.'
    return ''


def _has_full_cover(combat: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    position = target.get('position') if isinstance(target.get('position'), Mapping) else {}
    cover_id = _text(position.get('coverId') or position.get('cover_id'))
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), Mapping) else {}
    return bool(cover_id) and any(
        isinstance(entry, Mapping)
        and _text(entry.get('id')) == cover_id
        and condition_key(entry.get('coverType') or entry.get('cover_type')) == 'full'
        for entry in battlefield.get('cover') or []
    )


def _enemy_reason(combat: Mapping[str, Any], actor: Mapping[str, Any], target: Mapping[str, Any]) -> str:
    if not participant_is_targetable(dict(target)):
        return 'Target is already down.'
    if is_hidden(target):
        return 'Target is hidden.'
    if _has_full_cover(combat, target):
        return 'Target has full cover.'
    return attack_block_reason(actor, target)


def target_options(
    state: Mapping[str, Any],
    actor: Mapping[str, Any],
    item: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Every candidate target of one item use with its cost and availability."""

    effect_id = item_effect_id(item)
    if effect_id is None:
        return []
    rule = _target_rule(effect_id, item)
    self_cost = _self_cost(effect_id, item)
    combat = _active_combat(state)
    if combat is None:
        if rule in {'self', 'spell_self'}:
            candidates = [actor]
        elif rule in {'self_or_ally', 'spell_ally'}:
            candidates = _present_party(state)
        elif rule == 'dying_ally':
            candidates = [entry for entry in _present_party(state) if _text(entry.get('id')) != _text(actor.get('id'))]
        else:
            return []
        return [
            _option(candidate, cost=None, reason=_ally_reason(rule, actor, candidate, in_combat=False))
            for candidate in candidates
        ]
    participant = _participant(combat, actor.get('id'))
    if participant is None:
        return []
    economy = combat_turn_economy(combat, actor_id=participant.get('id')) or default_turn_economy(
        participant.get('id'),
        combat.get('round'),
    )
    options: list[dict[str, Any]] = []
    for candidate in combat.get('participants') or []:
        if not isinstance(candidate, dict) or not participant_is_present(candidate):
            continue
        is_self = _text(candidate.get('id')) == _text(participant.get('id'))
        friendly = _text(candidate.get('team')).lower() in FRIENDLY_TEAMS
        if rule in {'self', 'spell_self'} and not is_self:
            continue
        if rule in {'self_or_ally', 'dying_ally', 'spell_ally'} and not friendly:
            continue
        if rule == 'dying_ally' and is_self:
            continue
        if rule in {'enemy', 'spell_enemy'} and (friendly or _text(candidate.get('team')).lower() == 'neutral'):
            continue
        cost = self_cost if is_self or rule != 'self_or_ally' else 'action'
        if rule in {'enemy', 'spell_enemy'}:
            reason = _enemy_reason(combat, participant, candidate)
        elif rule.startswith('spell_'):
            # The spell engine applies the spell's own range and target rules when cast.
            reason = ''
        else:
            reason = _ally_reason(rule, participant, candidate, in_combat=True)
        options.append(_option(candidate, cost=cost, reason=reason or _economy_reason(economy, cost)))
    return options


def _use_blocker(state: Mapping[str, Any], actor: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    effect_id = item_effect_id(item)
    if effect_id is None:
        return f"{_text(item.get('name')) or 'That item'} has no rules effect."
    if int_or_default(item.get('quantity'), default=1) < 1 or uses_remaining(item) == 0:
        return f"{item.get('name')} is used up."
    if effect_id == 'spell_scroll':
        problem = _scroll_reader_problem(scroll_spell(item), actor.get('class'))
        if problem:
            return problem
    combat = _active_combat(state)
    if combat is None:
        if _target_rule(effect_id, item) in {'enemy', 'spell_enemy'}:
            return 'Requires an active encounter.'
        return ''
    participant = _participant(combat, actor.get('id'))
    if participant is None:
        return f'{actor_name(actor)} is not part of this encounter.'
    if not can_act(participant):
        return f'{actor_name(actor)} cannot act.'
    current = combat_turn_context(combat).get('currentActor')
    if not isinstance(current, dict):
        return 'Combat turn order is not established.'
    if _text(current.get('id')) != _text(participant.get('id')):
        return f"{_text(current.get('name')) or 'Another combatant'} is acting now."
    return ''


# Resolution ------------------------------------------------------------------


def item_roller(replay: list[int] | None = None) -> tuple[Roller, list[int]]:
    """A server roller that records each die; ``replay`` feeds recorded dice back in order."""

    recorded: list[int] = []

    def roll(sides: int) -> int:
        size = max(1, int(sides))
        if replay is None:
            value = secrets.randbelow(size) + 1
        else:
            value = int_or_default(replay[len(recorded)], default=1) if len(recorded) < len(replay) else 1
        value = max(1, min(size, value))
        recorded.append(value)
        return value

    return roll, recorded


def _roll(expression: str, roller: Roller, *, critical: bool = False) -> dict[str, Any]:
    parsed = parse_damage_dice_expression(expression) or {'count': 0, 'sides': 0, 'bonus': 0, 'dice': expression}
    count = int(parsed['count']) * (2 if critical else 1)
    rolls = [roller(int(parsed['sides'])) for _ in range(count)] if parsed['sides'] else []
    bonus = int(parsed['bonus'])
    return {'dice': str(parsed['dice']), 'rolls': rolls, 'bonus': bonus, 'total': max(0, sum(rolls) + bonus)}


def _d20(mode: str, roller: Roller) -> tuple[int, list[int]]:
    first = roller(20)
    if mode not in {'advantage', 'disadvantage'}:
        return first, [first]
    second = roller(20)
    return (max if mode == 'advantage' else min)(first, second), [first, second]


def _resolve_thrown(
    effect: Mapping[str, Any],
    participant: Mapping[str, Any],
    target: Mapping[str, Any],
    combat: Mapping[str, Any],
    roller: Roller,
) -> dict[str, Any]:
    context = attack_context(participant, target, kind='weapon_attack', ranged=True, combat=combat)
    natural, d20_rolls = _d20(context['mode'], roller)
    stats = participant.get('stats') if isinstance(participant.get('stats'), Mapping) else {}
    dexterity = (int_or_default(stats.get('dexterity', stats.get('dex')), default=10) - 10) // 2
    bonus = dexterity + proficiency_bonus_for_level(participant.get('level'))
    bonus_dice = [rolled for expression in context['attackBonusDice'] if (rolled := roll_signed_dice(expression, roller))]
    total = natural + bonus + sum(int(rolled['total']) for rolled in bonus_dice)
    armor_class = armor_class_with_context(int_or_default(target.get('armorClass'), default=10), context)
    critical = natural == 20
    hit = natural != 1 and (critical or total >= armor_class)
    attack = {
        'mode': context['mode'],
        'modeReasons': context['reasons'],
        'd20': d20_rolls,
        'natural': natural,
        'bonus': bonus,
        'bonusDice': bonus_dice,
        'total': total,
        'targetArmorClass': armor_class,
        'hit': hit,
        'critical': critical and hit,
    }
    result: dict[str, Any] = {'attack': attack, 'consume': deepcopy(context['consume'])}
    if not hit:
        return result
    harms = effect.get('harmsCreatureTypes')
    creature_type = condition_key(target.get('creatureType') or target.get('creatureTypeName'))
    if harms and creature_type not in harms:
        result['damage'] = {'type': effect['damage']['type'], 'total': 0, 'unaffected': True}
        return result
    rolled = _roll(effect['damage']['dice'], roller, critical=critical)
    multiplier, defense = damage_defense(target, effect['damage']['type'])
    result['damage'] = {
        **rolled,
        'type': effect['damage']['type'],
        'multiplier': multiplier,
        'defense': defense,
        'total': max(0, int(rolled['total'] * multiplier)),
    }
    if effect.get('burning'):
        result['burning'] = dict(effect['burning'])
    return result


def _participant_with_actor_stats(participant: Mapping[str, Any], actor: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(dict(participant))
    if not isinstance(merged.get('stats'), dict) or not merged['stats']:
        merged['stats'] = deepcopy(actor.get('stats') or {})
    merged.setdefault('level', actor.get('level'))
    return merged


def resolve_item_use(
    state: Mapping[str, Any],
    *,
    actor_id: Any,
    item_id: Any,
    target_id: Any,
    use_id: str,
    current_turn: int,
    roller: Roller,
) -> dict[str, Any]:
    """Validate one item use and resolve its effect: ``{"ok", "reason", "resolution"}``.

    The resolution records every die in ``rolls``; with the same state and those
    rolls the result is identical, which is how a submitted result is verified.
    """

    actor = find_actor(dict(state), actor_id)
    if actor is None:
        return {'ok': False, 'reason': 'Actor not found.'}
    item = next((entry for entry in actor_items(actor) if _text(entry.get('id')) == _text(item_id)), None)
    if item is None:
        return {'ok': False, 'reason': f'{actor_name(actor)} does not have that item.'}
    blocker = _use_blocker(state, actor, item)
    if blocker:
        return {'ok': False, 'reason': blocker}
    effect_id = item_effect_id(item) or ''
    effect = ITEM_EFFECTS[effect_id]
    options = target_options(state, actor, item)
    rule = _target_rule(effect_id, item)
    requested_target = _text(target_id) or (_text(actor.get('id')) if rule in {'self', 'self_or_ally'} else '')
    if not requested_target:
        return {'ok': False, 'reason': f"Choose a target for {item.get('name')}."}
    option = next((entry for entry in options if entry['id'] == requested_target), None)
    if option is None:
        return {'ok': False, 'reason': f"That is not a legal target for {item.get('name')}."}
    if not option['available']:
        return {'ok': False, 'reason': option['reason']}

    combat = _active_combat(state)
    resolution: dict[str, Any] = {
        'effectId': effect_id,
        'kind': effect['kind'],
        'itemId': _text(item.get('id')),
        'itemName': _text(item.get('name')),
        'targetId': option['id'],
        'targetName': option['name'],
        'cost': option['cost'] if combat is not None else None,
        'consumes': 'use' if uses_remaining(item) is not None else 'quantity',
    }
    user = actor_name(actor)
    item_name = _text(item.get('name'))
    on_self = option['id'] == _text(actor.get('id'))
    if effect['kind'] == 'healing':
        rolled = _roll(effect['dice'], roller)
        resolution['healing'] = rolled
        resolution['summary'] = (
            f"{user} drinks {item_name} and regains up to {rolled['total']} hit points ({rolled['dice']}: {rolled['rolls']})."
            if on_self
            else f"{user} gives {item_name} to {option['name']}, who regains up to {rolled['total']} hit points ({rolled['dice']}: {rolled['rolls']})."
        )
    elif effect['kind'] == 'antitoxin':
        resolution['removesConditions'] = ['poisoned']
        resolution['saveAdvantage'] = 'encounter' if combat is not None else 'until_rest'
        resolution['summary'] = f'{user} drinks {item_name}: no longer poisoned, and has advantage on Constitution saves.'
    elif effect['kind'] == 'stabilize':
        resolution['stabilizes'] = True
        resolution['summary'] = f"{user} uses {item_name} on {option['name']}, who is now stable."
    elif effect['kind'] == 'thrown':
        assert combat is not None
        participant = _participant_with_actor_stats(_participant(combat, actor.get('id')) or {}, actor)
        target = _participant(combat, option['id']) or {}
        resolution.update(_resolve_thrown(effect, participant, target, combat, roller))
        attack = resolution['attack']
        outcome = 'misses'
        if attack['hit']:
            damage = resolution['damage']
            outcome = (
                f"hits but {option['name']} is unharmed"
                if damage.get('unaffected')
                else f"hits for {damage['total']} {damage['type']} damage"
            )
        resolution['summary'] = (
            f"{user} throws {item_name} at {option['name']} (attack {attack['total']} vs AC {attack['targetArmorClass']}) and {outcome}."
        )
    else:
        spell = scroll_spell(item) or {}
        definition = _scroll_definition(spell, actor.get('class'))
        spell_result = resolve_targeted_spell(
            combat or (state.get('combat') if isinstance(state.get('combat'), Mapping) else {}),
            caster_id=_text(actor.get('id')),
            spell=definition,
            target_ids=[option['id']],
            cast_id=use_id,
            roller=roller,
            caster_resources=actor.get('spellResources') if isinstance(actor.get('spellResources'), dict) else {},
            current_turn=current_turn,
        )
        if not spell_result.get('ok'):
            return {'ok': False, 'reason': _text(spell_result.get('reason')) or 'That spell cannot be cast now.'}
        resolution['spell'] = {'name': spell.get('name'), 'level': spell.get('level'), 'resolution': spell_result.get('resolution')}
        resolution['summary'] = f"{user} reads {item_name} and casts {spell.get('name')} at {option['name']}."
        return {
            'ok': True,
            'reason': resolution['summary'],
            'resolution': resolution,
            'resolvedCombat': spell_result.get('combat'),
            'casterResources': spell_result.get('casterResources'),
        }
    return {'ok': True, 'reason': resolution['summary'], 'resolution': resolution}


# Combat legal actions --------------------------------------------------------


def _economy_cost(cost: str | None) -> dict[str, Any]:
    return {
        'action': 1 if cost == 'action' else 0,
        'bonusAction': 1 if cost == 'bonus_action' else 0,
        'reaction': 0,
        'movement': 'unused',
        'movementCost': 0,
        'endsTurn': False,
    }


def item_use_actions(snapshot: Mapping[str, Any], participant: Mapping[str, Any], combat: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Action-provider: one ``item.use.<itemId>`` action per usable inventory item."""

    actor = find_actor(dict(snapshot), participant.get('id'))
    if actor is None:
        return []
    state = {**snapshot, 'combat': combat}
    actions: list[dict[str, Any]] = []
    for item in actor_items(actor):
        descriptor = item_effect_descriptor(item, class_name=actor.get('class'))
        item_id = _text(item.get('id'))
        if descriptor is None or not item_id:
            continue
        targets = target_options(state, actor, item)
        available_targets = [target for target in targets if target['available']]
        blocker = _use_blocker(state, actor, item)
        cheapest = min(
            (target['cost'] for target in available_targets),
            key=lambda cost: 0 if cost == 'bonus_action' else 1,
            default=descriptor['cost'],
        )
        reason = blocker or ('' if available_targets else 'No legal target is available.')
        actions.append(
            {
                'id': f'{ITEM_USE_ACTION_PREFIX}{item_id}',
                'type': 'item',
                'label': f"Use {item.get('name')}",
                'description': descriptor['summary'],
                'message': '',
                'available': not reason,
                'reason': reason,
                'economy': _economy_cost(cheapest),
                'requiresTarget': True,
                'targets': targets,
                'item': {
                    'id': item_id,
                    'name': _text(item.get('name')),
                    'quantity': int_or_default(item.get('quantity'), default=1),
                    'effectId': descriptor['id'],
                },
            }
        )
    return actions


def resolve_item_use_action(
    snapshot: dict[str, Any],
    player: Any,
    action: dict[str, Any],
    target_id: str,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Action-resolver for ``item.use.*``: canonicalise the chosen target.

    The ``item.use`` state change charges the turn economy itself, so the
    canonical action carries no economy cost for the combat pipeline to charge.
    """

    target = next((entry for entry in action.get('targets') or [] if entry.get('id') == _text(target_id)), None)
    if target is None:
        return None, 'combat_target_invalid', 'Choose a current target for that item.'
    if not target.get('available'):
        return None, 'combat_target_unavailable', _text(target.get('reason')) or 'That target is not available.'
    item = action.get('item') if isinstance(action.get('item'), dict) else {}
    user = _text(player.character_name) or 'I'
    message = (
        f"{user} uses {item.get('name')}."
        if target['id'] == display_actor_id(player.player_id)
        else f"{user} uses {item.get('name')} on {target['name']}."
    )
    return (
        {
            'action_id': action['id'],
            'action_type': 'item',
            'economy': _economy_cost(None),
            'authoritative': True,
            'item_id': _text(item.get('id')),
            'item_name': _text(item.get('name')),
            'target_id': target['id'],
            'target_name': target['name'],
            'message': message,
        },
        None,
        None,
    )


register_action_provider(item_use_actions)
register_action_resolver(ITEM_USE_ACTION_PREFIX, resolve_item_use_action)
