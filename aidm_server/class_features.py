"""Class features at levels 1-5: the capability catalog, class pools and passive rules.

Only the primary class grants features on a multiclass sheet ("Fighter / Wizard"
plays as a fighter).  The character sheet and armour-class code import this
module, so it depends on nothing heavier than the modifier engine; resolving a
capability use and the combat-registry glue live in
:mod:`aidm_server.class_capabilities`.

Pools live in ``classFeatureState`` as ``{poolId: {"current", "max",
"refreshesOn"}}``.  A capability names the pool it spends in ``cost`` and may
offer options; the id a player submits is ``<capabilityId>`` or
``<capabilityId>:<option>`` (``step_of_the_wind:dash``).

The passive rules are providers registered with the modifier engine.  They are
pure and read the participant and the provider context only, so some of them
act only once the attack resolver supplies these context keys:

* extra-damage context: ``weapon`` (the HUD weapon descriptor ``id``, ``name``,
  ``classification`` plus ``otherWeaponEquipped``), ``attack`` (``mode``,
  ``ranged``, ``ability``, ``critical``, ``combat``, the declared ``rider`` and
  ``oncePerTurnUsed``: the ``oncePerTurn`` keys of components this attacker
  already dealt this turn) and, for spell attacks, ``spellId``;
* attack-roll context: ``weapon`` and ``ability`` (the ability the attack
  uses); and the result keys ``criticalRangeBonus`` (the natural roll needed
  for a critical drops by this much) and ``damageRerollBelow`` (weapon damage
  dice at or below it are rerolled once);
* ``participant['fightingStyle']`` for the fighting styles.
"""

from __future__ import annotations

from copy import deepcopy
from itertools import combinations_with_replacement
import re
from typing import Any, Mapping

from aidm_server.canon_text import int_or_default
from aidm_server.character_progression import class_archetype, proficiency_bonus_for_level
from aidm_server.combat.modifiers import (
    INCAPACITATING_CONDITIONS,
    active_effects,
    can_act,
    condition_keys,
    engaged_with,
    register_attacks_per_action_provider,
    register_extra_damage_provider,
    register_modifier_provider,
)
from aidm_server.weapon_proficiency import weapon_properties


EXTRA_ATTACK_CLASSES = frozenset({'barbarian', 'fighter', 'monk', 'paladin', 'ranger'})
FIGHTING_STYLES = {
    'fighter': ('defense', 'dueling', 'great_weapon', 'archery'),
    'paladin': ('defense', 'dueling', 'great_weapon'),
    'ranger': ('archery', 'defense', 'dueling'),
}
FIGHTING_STYLE_LEVEL = {'fighter': 1, 'paladin': 2, 'ranger': 2}
RAGE_RESISTANCES = ('bludgeoning', 'piercing', 'slashing')
SORCERY_POINTS_FOR_SLOT = {1: 2, 2: 3, 3: 5}
ARCANE_RECOVERY_MAX_SLOT = 3
COMBAT_ONLY_REASON = 'Available only during active combat.'
OUT_OF_COMBAT_ONLY_REASON = 'Available only outside combat.'
ROLE_REASONS = {
    'combat_action': 'Choose it from the combat actions on your turn.',
    'attack_rider': 'Choose it with a melee weapon attack in combat; it is spent only on a hit.',
    'reaction': 'Used automatically as your reaction when an attack hits you.',
}

CLASS_POOLS: dict[str, tuple[dict[str, Any], ...]] = {
    'barbarian': ({'id': 'rage', 'name': 'Rage', 'minimumLevel': 1, 'poolByLevel': {1: 2, 3: 3}, 'refreshesOn': 'long_rest'},),
    'bard': (
        {
            'id': 'bardic_inspiration',
            'name': 'Bardic Inspiration',
            'minimumLevel': 1,
            'poolAbility': 'charisma',
            'refreshesOn': 'long_rest',
            'shortRestFromLevel': 5,
        },
    ),
    'cleric': ({'id': 'channel_divinity', 'name': 'Channel Divinity', 'minimumLevel': 2, 'poolByLevel': {2: 1}, 'refreshesOn': 'short_rest'},),
    'fighter': (
        {'id': 'second_wind', 'name': 'Second Wind', 'minimumLevel': 1, 'poolByLevel': {1: 1}, 'refreshesOn': 'short_rest'},
        {'id': 'action_surge', 'name': 'Action Surge', 'minimumLevel': 2, 'poolByLevel': {2: 1}, 'refreshesOn': 'short_rest'},
    ),
    'monk': ({'id': 'ki', 'name': 'Ki', 'minimumLevel': 2, 'poolPerLevel': 1, 'refreshesOn': 'short_rest'},),
    'paladin': ({'id': 'lay_on_hands', 'name': 'Lay on Hands', 'minimumLevel': 1, 'poolPerLevel': 5, 'refreshesOn': 'long_rest'},),
    'sorcerer': ({'id': 'sorcery_points', 'name': 'Sorcery points', 'minimumLevel': 2, 'poolPerLevel': 1, 'refreshesOn': 'long_rest'},),
    'wizard': ({'id': 'arcane_recovery', 'name': 'Arcane Recovery', 'minimumLevel': 1, 'poolByLevel': {1: 1}, 'refreshesOn': 'long_rest'},),
}

# ``role`` says how a player uses the capability: ``panel`` (a typed capability
# use, also offered as a combat action), ``combat_action`` (only a combat HUD
# action), ``attack_rider`` (a variant of a melee weapon attack) or ``reaction``.
CLASS_CAPABILITY_CATALOG: dict[str, tuple[dict[str, Any], ...]] = {
    'barbarian': (
        {
            'id': 'rage',
            'name': 'Rage',
            'minimumLevel': 1,
            'actionEconomy': 'bonus_action',
            'targetPolicy': 'self',
            'cost': {'pool': 'rage', 'amount': 1},
            'combatOnly': True,
            'effect': {'type': 'apply_modifier'},
            'description': (
                'For 10 rounds: +2 damage on Strength melee attacks, resistance to bludgeoning, piercing and slashing '
                'damage, advantage on Strength checks and saves; you cannot cast or concentrate on spells.'
            ),
        },
        {
            'id': 'reckless_attack',
            'name': 'Reckless Attack',
            'minimumLevel': 2,
            'actionEconomy': 'free',
            'targetPolicy': 'self',
            'combatOnly': True,
            'effect': {'type': 'apply_modifier'},
            'description': (
                'Before your first attack this turn: your melee Strength attacks have advantage this turn, and attacks '
                'against you have advantage until your next turn.'
            ),
        },
    ),
    'bard': (
        {
            'id': 'bardic_inspiration',
            'name': 'Bardic Inspiration',
            'minimumLevel': 1,
            'actionEconomy': 'bonus_action',
            'targetPolicy': 'ally',
            'cost': {'pool': 'bardic_inspiration', 'amount': 1},
            'effect': {'type': 'apply_modifier'},
            'description': (
                "Give an ally a d6 (d8 from level 5), added to their next attack roll, ability check or saving throw "
                'that would otherwise fail, within 100 rounds.'
            ),
        },
    ),
    'cleric': (
        {
            'id': 'turn_undead',
            'name': 'Turn Undead',
            'minimumLevel': 2,
            'actionEconomy': 'action',
            'targetPolicy': 'self',
            'cost': {'pool': 'channel_divinity', 'amount': 1},
            'combatOnly': True,
            'effect': {'type': 'turn_undead'},
            'description': (
                'Each undead enemy at melee, near or far range makes a Wisdom save against your spell DC or is turned '
                'for 10 rounds. From level 5, turned undead of challenge 1/2 or lower are destroyed.'
            ),
        },
    ),
    'fighter': (
        {
            'id': 'second_wind',
            'name': 'Second Wind',
            'minimumLevel': 1,
            'actionEconomy': 'bonus_action',
            'targetPolicy': 'self',
            'cost': {'pool': 'second_wind', 'amount': 1},
            'effect': {'type': 'heal', 'dice': {'count': 1, 'sides': 10}, 'levelBonus': True},
            'description': 'Recover 1d10 + fighter level hit points as a bonus action.',
        },
        {
            'id': 'action_surge',
            'name': 'Action Surge',
            'minimumLevel': 2,
            'actionEconomy': 'free',
            'targetPolicy': 'self',
            'cost': {'pool': 'action_surge', 'amount': 1},
            'effect': {'type': 'restore_action', 'amount': 1},
            'description': 'Regain the action for the current combat turn.',
        },
    ),
    'monk': (
        {
            'id': 'martial_arts_strike',
            'name': 'Martial Arts Strike',
            'minimumLevel': 1,
            'actionEconomy': 'bonus_action',
            'targetPolicy': 'self',
            'role': 'combat_action',
            'combatOnly': True,
            'effect': {'type': 'grant_action', 'action': 'unarmed_strike'},
            'description': 'After you take the Attack action, make one unarmed strike as a bonus action.',
        },
        {
            'id': 'flurry_of_blows',
            'name': 'Flurry of Blows',
            'minimumLevel': 2,
            'actionEconomy': 'bonus_action',
            'targetPolicy': 'self',
            'cost': {'pool': 'ki', 'amount': 1},
            'combatOnly': True,
            'effect': {'type': 'grant_action', 'action': 'flurry_strikes'},
            'description': 'After you take the Attack action, spend 1 ki to make two unarmed strikes as a bonus action.',
        },
        {
            'id': 'patient_defense',
            'name': 'Patient Defense',
            'minimumLevel': 2,
            'actionEconomy': 'bonus_action',
            'targetPolicy': 'self',
            'cost': {'pool': 'ki', 'amount': 1},
            'combatOnly': True,
            'effect': {'type': 'grant_action', 'action': 'dodge'},
            'description': 'Spend 1 ki to take the Dodge action as a bonus action.',
        },
        {
            'id': 'step_of_the_wind',
            'name': 'Step of the Wind',
            'minimumLevel': 2,
            'actionEconomy': 'bonus_action',
            'targetPolicy': 'self',
            'cost': {'pool': 'ki', 'amount': 1},
            'combatOnly': True,
            'options': ('dash', 'disengage'),
            'effect': {'type': 'grant_action'},
            'description': 'Spend 1 ki to Dash or Disengage as a bonus action.',
        },
        {
            'id': 'stunning_strike',
            'name': 'Stunning Strike',
            'minimumLevel': 5,
            'actionEconomy': 'free',
            'targetPolicy': 'enemy',
            'role': 'attack_rider',
            'cost': {'pool': 'ki', 'amount': 1},
            'combatOnly': True,
            'effect': {'type': 'attack_rider'},
            'description': (
                'Declared with a melee weapon attack; on a hit spend 1 ki: the target makes a Constitution save or is '
                'stunned until the end of your next turn.'
            ),
        },
    ),
    'paladin': (
        {
            'id': 'lay_on_hands',
            'name': 'Lay on Hands',
            'minimumLevel': 1,
            'actionEconomy': 'action',
            'targetPolicy': 'self_or_ally',
            'cost': {'pool': 'lay_on_hands', 'amount': 'requested'},
            'options': ('cure_poison',),
            'effect': {'type': 'healing_pool'},
            'description': 'Spend points from a 5-per-level pool to restore hit points, or 5 points to cure poison.',
        },
        {
            'id': 'divine_smite',
            'name': 'Divine Smite',
            'minimumLevel': 2,
            'actionEconomy': 'free',
            'targetPolicy': 'enemy',
            'role': 'attack_rider',
            'cost': {'spellSlot': 'chosen'},
            'combatOnly': True,
            'effect': {'type': 'attack_rider'},
            'description': (
                'Declared with a melee weapon attack; on a hit spend a spell slot for +2d8 radiant damage, +1d8 per '
                'slot level above 1st (at most 5d8), +1d8 against undead or fiends.'
            ),
        },
    ),
    'rogue': (
        {
            'id': 'cunning_action',
            'name': 'Cunning Action',
            'minimumLevel': 2,
            'actionEconomy': 'bonus_action',
            'targetPolicy': 'self',
            'role': 'combat_action',
            'combatOnly': True,
            'options': ('dash', 'disengage', 'hide'),
            'effect': {'type': 'grant_action'},
            'description': 'Dash, Disengage or Hide as a bonus action.',
        },
        {
            'id': 'uncanny_dodge',
            'name': 'Uncanny Dodge',
            'minimumLevel': 5,
            'actionEconomy': 'reaction',
            'targetPolicy': 'self',
            'role': 'reaction',
            'combatOnly': True,
            'effect': {'type': 'reaction'},
            'description': 'When an attack hits you, your reaction halves its damage (automatic unless reactions are off).',
        },
    ),
    'sorcerer': (
        {
            'id': 'flexible_casting',
            'name': 'Flexible Casting',
            'minimumLevel': 2,
            'actionEconomy': 'bonus_action',
            'targetPolicy': 'self',
            'cost': {'pool': 'sorcery_points', 'amount': 'by_option'},
            'effect': {'type': 'resource_convert'},
            'description': 'Turn a spell slot into sorcery points equal to its level, or 2/3/5 points into a missing 1st/2nd/3rd-level slot.',
        },
        {
            'id': 'quickened_spell',
            'name': 'Quickened Spell',
            'minimumLevel': 3,
            'actionEconomy': 'free',
            'targetPolicy': 'self',
            'cost': {'pool': 'sorcery_points', 'amount': 2},
            'combatOnly': True,
            'effect': {'type': 'apply_modifier', 'metamagic': 'quickened'},
            'description': 'Spend 2 sorcery points: the next spell you cast this turn that costs an action costs a bonus action.',
        },
        {
            'id': 'twinned_spell',
            'name': 'Twinned Spell',
            'minimumLevel': 3,
            'actionEconomy': 'free',
            'targetPolicy': 'self',
            'cost': {'pool': 'sorcery_points', 'amount': 'by_option'},
            'combatOnly': True,
            'effect': {'type': 'apply_modifier', 'metamagic': 'twinned'},
            'description': (
                'Spend sorcery points equal to the spell level (1 for a cantrip): the next single-target spell of that '
                'level you cast this turn also affects a second legal target, with its own rolls.'
            ),
        },
    ),
    'wizard': (
        {
            'id': 'arcane_recovery',
            'name': 'Arcane Recovery',
            'minimumLevel': 1,
            'actionEconomy': 'free',
            'targetPolicy': 'self',
            'cost': {'pool': 'arcane_recovery', 'amount': 1},
            'outOfCombatOnly': True,
            'effect': {'type': 'restore_slots'},
            'description': (
                'Once per long rest, after a short rest: recover spell slots totalling up to half your wizard level '
                '(rounded up), none above 3rd level. Lowest missing slots first unless you choose.'
            ),
        },
    ),
}

def _text(value: Any) -> str:
    return str(value or '').strip()


def _key(value: Any) -> str:
    return re.sub(r'[^a-z0-9]+', '_', _text(value).lower()).strip('_')


def bounded_level(value: Any) -> int:
    return max(1, min(20, int_or_default(value, default=1)))


def primary_class(value: Any) -> str | None:
    """Resolve only the first class on a multiclass label."""

    label = re.split(r'[/+,&]|\band\b', str(value or ''), maxsplit=1, flags=re.IGNORECASE)[0]
    return class_archetype(label)


def subclass_name(value: Any) -> str:
    """The subclass part of a primary class label such as ``Fighter - Champion``."""

    label = re.split(r'[/+,&]|\band\b', str(value or ''), maxsplit=1, flags=re.IGNORECASE)[0]
    return label.split('-', 1)[1].strip() if '-' in label else ''


def ability_score_modifier(scores: Mapping[str, Any] | None, ability: str) -> int:
    source = scores if isinstance(scores, Mapping) else {}
    return (int_or_default(source.get(ability, source.get(ability[:3])), default=10) - 10) // 2


def _participant_class(participant: Mapping[str, Any]) -> tuple[str | None, int]:
    return primary_class(participant.get('class')), bounded_level(participant.get('level'))


def _stats(participant: Mapping[str, Any]) -> Mapping[str, Any]:
    stats = participant.get('stats')
    return stats if isinstance(stats, Mapping) else {}


def spell_save_dc(participant: Mapping[str, Any], ability: str) -> int:
    return 8 + proficiency_bonus_for_level(participant.get('level')) + ability_score_modifier(_stats(participant), ability)


# Pools ------------------------------------------------------------------------


def _pool_size(pool: Mapping[str, Any], level: int, scores: Mapping[str, Any] | None, stored: Any) -> int:
    ability = _text(pool.get('poolAbility'))
    if ability:
        # Without scores the ability-sized pool keeps its stored size rather
        # than shrinking and losing uses.
        if not isinstance(scores, Mapping) or not scores:
            return max(1, int_or_default(stored, default=1))
        return max(1, ability_score_modifier(scores, ability))
    if pool.get('poolPerLevel') is not None:
        return max(1, int_or_default(pool.get('poolPerLevel'), default=1) * level)
    table = pool.get('poolByLevel') or {}
    reached = [threshold for threshold in table if level >= int(threshold)]
    return max(1, int(table[max(reached)])) if reached else 1


def pool_refreshes_on(pool: Mapping[str, Any], level: int) -> str:
    from_level = int_or_default(pool.get('shortRestFromLevel'), default=0)
    if from_level and level >= from_level:
        return 'short_rest'
    return _text(pool.get('refreshesOn')) or 'long_rest'


def class_pools(class_name: Any, level: Any) -> list[dict[str, Any]]:
    character_level = bounded_level(level)
    return [
        deepcopy(pool)
        for pool in CLASS_POOLS.get(primary_class(class_name) or '', ())
        if character_level >= int_or_default(pool.get('minimumLevel'), default=1)
    ]


def pool_display_name(class_name: Any, pool_id: Any) -> str:
    """The player-facing name of one of the class's pools ("Ki", "Sorcery points")."""

    return next(
        (pool['name'] for pool in CLASS_POOLS.get(primary_class(class_name) or '', ()) if pool['id'] == _text(pool_id)),
        _text(pool_id).replace('_', ' ').capitalize(),
    )


def normalize_class_feature_state(
    raw_state: Any,
    *,
    class_name: Any,
    level: Any,
    scores: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Every pool the class has at this level.

    A pool that grew or shrank since it was stored gains or loses only the
    difference, so a level-up never refills spent uses.
    """

    source = raw_state if isinstance(raw_state, dict) else {}
    character_level = bounded_level(level)
    normalized: dict[str, dict[str, Any]] = {}
    for pool in class_pools(class_name, character_level):
        pool_id = _text(pool.get('id'))
        existing = source.get(pool_id) if isinstance(source.get(pool_id), dict) else {}
        maximum = _pool_size(pool, character_level, scores, existing.get('max'))
        stored_max = int_or_default(existing.get('max'), default=maximum)
        current = int_or_default(existing.get('current', existing.get('remaining', stored_max)), default=stored_max)
        if stored_max != maximum:
            current += maximum - stored_max
        normalized[pool_id] = {
            'current': max(0, min(maximum, current)),
            'max': maximum,
            'refreshesOn': pool_refreshes_on(pool, character_level),
        }
        if existing.get('usedAtTurn') is not None:
            normalized[pool_id]['usedAtTurn'] = max(0, int_or_default(existing.get('usedAtTurn'), default=0))
    return normalized


def adjust_class_pool(state: dict[str, dict[str, Any]], pool_id: Any, delta: int, *, turn_id: Any = None) -> bool:
    """Spend (negative) or regain (positive) pool points in place; False when that is impossible."""

    entry = state.get(_text(pool_id))
    if not entry:
        return False
    current = int_or_default(entry.get('current'), default=0) + int(delta)
    if current < 0 or current > int_or_default(entry.get('max'), default=0):
        return False
    entry['current'] = current
    if delta < 0:
        entry['usedAtTurn'] = max(0, int_or_default(turn_id, default=0))
    return True


def spend_capability(
    raw_state: Any,
    *,
    class_name: Any,
    level: Any,
    capability_id: Any,
    amount: Any,
    turn_id: Any,
    scores: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]] | None:
    """The state after the capability's pool pays ``amount``, or None when it cannot."""

    capability = capability_by_id(class_name, level, capability_id)
    state = normalize_class_feature_state(raw_state, class_name=class_name, level=level, scores=scores)
    pool_id = _text((capability or {}).get('pool'))
    if not pool_id or not adjust_class_pool(state, pool_id, -max(1, int_or_default(amount, default=1)), turn_id=turn_id):
        return None
    return state


def restore_class_capabilities(
    raw_state: Any,
    *,
    class_name: Any,
    level: Any,
    rest_type: Any,
    scores: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    state = normalize_class_feature_state(raw_state, class_name=class_name, level=level, scores=scores)
    refreshes = {'short_rest', 'long_rest'} if _text(rest_type).lower() == 'long_rest' else {'short_rest'}
    for entry in state.values():
        if _text(entry.get('refreshesOn')).lower() not in refreshes:
            continue
        entry['current'] = entry['max']
        entry.pop('usedAtTurn', None)
    return state


# Catalog ------------------------------------------------------------------------


def split_capability_id(value: Any) -> tuple[str, str]:
    """``("step_of_the_wind", "dash")`` for ``step_of_the_wind:dash``."""

    base, _, option = _text(value).partition(':')
    return base, option


def capabilities_for_class(class_name: Any, level: Any) -> list[dict[str, Any]]:
    """The class's active capabilities unlocked at this level, each with its pool's size and refresh."""

    character_level = bounded_level(level)
    pools = {pool['id']: pool for pool in class_pools(class_name, character_level)}
    capabilities: list[dict[str, Any]] = []
    for definition in CLASS_CAPABILITY_CATALOG.get(primary_class(class_name) or '', ()):
        if character_level < max(1, int_or_default(definition.get('minimumLevel'), default=1)):
            continue
        capability = deepcopy(definition)
        cost = capability.get('cost') if isinstance(capability.get('cost'), dict) else {}
        pool = pools.get(_text(cost.get('pool')))
        if pool is not None:
            capability['pool'] = pool['id']
            capability['poolName'] = pool['name']
            capability['refreshesOn'] = pool_refreshes_on(pool, character_level)
        capabilities.append(capability)
    return capabilities


def capability_by_id(class_name: Any, level: Any, capability_id: Any) -> dict[str, Any] | None:
    requested, _option = split_capability_id(capability_id)
    return next(
        (capability for capability in capabilities_for_class(class_name, level) if capability['id'] == requested),
        None,
    )


# Options: what a player may choose and what it costs ---------------------------------


def _slot_table(spell_resources: Any) -> dict[int, tuple[int, int]]:
    """``{slotLevel: (current, max)}`` for the standard slots a caster has."""

    resources = spell_resources if isinstance(spell_resources, Mapping) else {}
    slots = resources.get('slots') if isinstance(resources.get('slots'), Mapping) else {}
    table: dict[int, tuple[int, int]] = {}
    for raw_level, entry in slots.items():
        level = int_or_default(raw_level, default=0)
        if level <= 0 or not isinstance(entry, Mapping):
            continue
        maximum = max(0, int_or_default(entry.get('max'), default=0))
        if maximum:
            table[level] = (max(0, min(maximum, int_or_default(entry.get('current'), default=0))), maximum)
    return table


def _ordinal(level: int) -> str:
    return {1: '1st', 2: '2nd', 3: '3rd'}.get(level, f'{level}th')


def arcane_recovery_budget(level: Any) -> int:
    return (bounded_level(level) + 1) // 2


def _arcane_recovery_choices(level: int, slots: Mapping[int, tuple[int, int]]) -> list[tuple[int, ...]]:
    budget = arcane_recovery_budget(level)
    levels = sorted(slot_level for slot_level in slots if slot_level <= ARCANE_RECOVERY_MAX_SLOT)
    choices: list[tuple[int, ...]] = []
    for count in range(1, budget + 1):
        for choice in combinations_with_replacement(levels, count):
            if sum(choice) <= budget:
                choices.append(choice)
    return choices


def default_arcane_recovery(level: Any, slots: Mapping[int, tuple[int, int]]) -> tuple[int, ...]:
    """Lowest missing slots first within the level budget."""

    remaining = arcane_recovery_budget(level)
    chosen: list[int] = []
    for slot_level in sorted(slot_level for slot_level in slots if slot_level <= ARCANE_RECOVERY_MAX_SLOT):
        current, maximum = slots[slot_level]
        for _ in range(maximum - current):
            if slot_level > remaining:
                break
            chosen.append(slot_level)
            remaining -= slot_level
    return tuple(chosen)


def capability_variants(
    capability: Mapping[str, Any],
    *,
    level: Any,
    spell_resources: Any = None,
) -> list[dict[str, Any]]:
    """Every way the capability can be used: ``{"option", "name", "cost"}``.

    ``cost`` is ``{"pool", "amount"}``, ``{"spellSlot": n}`` or ``{}``; flexible
    casting variants also carry ``gain``.
    """

    capability_id = _text(capability.get('id'))
    name = _text(capability.get('name'))
    character_level = bounded_level(level)
    base_cost = deepcopy(capability.get('cost')) if isinstance(capability.get('cost'), Mapping) else {}
    slots = _slot_table(spell_resources)
    if capability_id in {'step_of_the_wind', 'cunning_action'}:
        return [
            {'option': option, 'name': f'{name}: {option.capitalize()}', 'cost': base_cost}
            for option in capability.get('options') or ()
        ]
    if capability_id == 'lay_on_hands':
        return [
            {'option': '', 'name': name, 'cost': base_cost},
            {'option': 'cure_poison', 'name': f'{name}: Cure Poison', 'cost': {'pool': 'lay_on_hands', 'amount': 5}},
        ]
    if capability_id == 'divine_smite':
        return [
            {'option': str(slot_level), 'name': f'{name} ({_ordinal(slot_level)}-level slot)', 'cost': {'spellSlot': slot_level}}
            for slot_level in sorted(slots)
        ]
    if capability_id == 'flexible_casting':
        variants = [
            {
                'option': f'slot_to_points.{slot_level}',
                'name': f'{name}: {_ordinal(slot_level)}-level slot to {slot_level} sorcery point{"s" if slot_level > 1 else ""}',
                'cost': {'spellSlot': slot_level},
                'gain': {'pool': 'sorcery_points', 'amount': slot_level},
            }
            for slot_level in sorted(slots)
        ]
        variants.extend(
            {
                'option': f'points_to_slot.{slot_level}',
                'name': f'{name}: {points} sorcery points to a {_ordinal(slot_level)}-level slot',
                'cost': {'pool': 'sorcery_points', 'amount': points},
                'gain': {'spellSlot': slot_level},
            }
            for slot_level, points in SORCERY_POINTS_FOR_SLOT.items()
            if slot_level in slots
        )
        return variants
    if capability_id == 'twinned_spell':
        spell_levels = [0, *sorted(slot_level for slot_level in slots if slot_level <= 3)]
        return [
            {
                'option': str(spell_level),
                'name': f"{name} ({'cantrip' if spell_level == 0 else _ordinal(spell_level) + '-level spell'})",
                'cost': {'pool': 'sorcery_points', 'amount': max(1, spell_level)},
            }
            for spell_level in spell_levels
        ]
    if capability_id == 'arcane_recovery':
        return [
            {'option': '', 'name': f'{name} (lowest missing slots first)', 'cost': base_cost},
            *(
                {
                    'option': '.'.join(str(slot_level) for slot_level in choice),
                    'name': f"{name}: {' + '.join(_ordinal(slot_level) for slot_level in choice)}-level slot{'s' if len(choice) > 1 else ''}",
                    'cost': base_cost,
                }
                for choice in _arcane_recovery_choices(character_level, slots)
            ),
        ]
    return [{'option': '', 'name': name, 'cost': base_cost}]


def variant_for(capability: Mapping[str, Any], option: str, *, level: Any, spell_resources: Any = None) -> dict[str, Any] | None:
    return next(
        (variant for variant in capability_variants(capability, level=level, spell_resources=spell_resources) if variant['option'] == option),
        None,
    )


def resource_reason(
    capability: Mapping[str, Any],
    variant: Mapping[str, Any],
    *,
    feature_state: Mapping[str, Any],
    spell_resources: Any,
    level: Any,
) -> str:
    """Why the pool or spell slots cannot pay for this variant now ('' when they can)."""

    cost = variant.get('cost') if isinstance(variant.get('cost'), Mapping) else {}
    slots = _slot_table(spell_resources)
    pool_id = _text(cost.get('pool'))
    if pool_id:
        entry = feature_state.get(pool_id) if isinstance(feature_state.get(pool_id), Mapping) else {}
        current = int_or_default(entry.get('current'), default=0)
        amount = cost.get('amount')
        needed = 1 if amount == 'requested' else max(1, int_or_default(amount, default=1))
        if current <= 0:
            return f"{capability.get('name')} has no uses remaining." if pool_id == capability.get('id') else f'No {_pool_label(pool_id)} remaining.'
        if current < needed:
            return f'Needs {needed} {_pool_label(pool_id)}; {current} left.'
    slot_level = int_or_default(cost.get('spellSlot'), default=0)
    if slot_level and slots.get(slot_level, (0, 0))[0] <= 0:
        return f'No {_ordinal(slot_level)}-level spell slot left.'
    gain = variant.get('gain') if isinstance(variant.get('gain'), Mapping) else {}
    if gain.get('pool'):
        entry = feature_state.get(_text(gain['pool'])) if isinstance(feature_state.get(_text(gain['pool'])), Mapping) else {}
        if int_or_default(entry.get('current'), default=0) + int_or_default(gain.get('amount'), default=0) > int_or_default(entry.get('max'), default=0):
            return 'That would exceed your sorcery point maximum.'
    if gain.get('spellSlot'):
        current, maximum = slots.get(int(gain['spellSlot']), (0, 0))
        if current >= maximum:
            return f"No expended {_ordinal(int(gain['spellSlot']))}-level slot to restore."
    if _text(capability.get('id')) == 'arcane_recovery':
        chosen = arcane_recovery_choice(variant.get('option'), level=level, spell_resources=spell_resources)
        if not chosen:
            return 'No expended spell slot can be recovered.'
    return ''


def arcane_recovery_choice(option: Any, *, level: Any, spell_resources: Any) -> tuple[int, ...]:
    """The slot levels a recovery restores, or () when the choice is not possible now."""

    slots = _slot_table(spell_resources)
    text = _text(option)
    if not text:
        return default_arcane_recovery(level, slots)
    chosen = tuple(sorted(int_or_default(part, default=0) for part in text.split('.')))
    if chosen not in _arcane_recovery_choices(bounded_level(level), slots):
        return ()
    for slot_level in set(chosen):
        current, maximum = slots[slot_level]
        if maximum - current < chosen.count(slot_level):
            return ()
    return chosen


def _pool_label(pool_id: str) -> str:
    return {
        'ki': 'ki points',
        'sorcery_points': 'sorcery points',
        'channel_divinity': 'Channel Divinity uses',
        'bardic_inspiration': 'Bardic Inspiration uses',
        'rage': 'rages',
        'lay_on_hands': 'Lay on Hands points',
    }.get(pool_id, f"{pool_id.replace('_', ' ')} uses")


def class_feature_descriptors(
    class_name: Any,
    level: Any,
    *,
    scores: Mapping[str, Any] | None = None,
    feature_state: Any = None,
    spell_resources: Any = None,
) -> list[dict[str, Any]]:
    """One player-facing entry per capability variant, with its pool, cost and any resource blocker.

    Situational reasons (turn, economy, targets) are added by the combat
    action provider and the client.
    """

    character_level = bounded_level(level)
    state = normalize_class_feature_state(feature_state, class_name=class_name, level=character_level, scores=scores)
    descriptors: list[dict[str, Any]] = []
    for capability in capabilities_for_class(class_name, character_level):
        for variant in capability_variants(capability, level=character_level, spell_resources=spell_resources):
            descriptor = {key: deepcopy(value) for key, value in capability.items() if key != 'options'}
            descriptor.update(
                {
                    'id': f"{capability['id']}:{variant['option']}" if variant['option'] else capability['id'],
                    'baseId': capability['id'],
                    'name': variant['name'],
                    'cost': deepcopy(variant['cost']),
                    'role': capability.get('role') or 'panel',
                    'reason': ROLE_REASONS.get(_text(capability.get('role')))
                    or resource_reason(capability, variant, feature_state=state, spell_resources=spell_resources, level=character_level),
                }
            )
            gain = variant.get('gain') if isinstance(variant.get('gain'), Mapping) else {}
            pool_id = _text(variant['cost'].get('pool') or gain.get('pool') or capability.get('pool'))
            if pool_id in state:
                descriptor['pool'] = pool_id
                descriptor['poolName'] = pool_display_name(class_name, pool_id)
                descriptor['maxUses'] = state[pool_id]['max']
                descriptor['refreshesOn'] = state[pool_id]['refreshesOn']
            descriptors.append(descriptor)
    return descriptors


# Fighting styles, Expertise, Jack of All Trades -------------------------------------


def _equipped(items: Any) -> list[Mapping[str, Any]]:
    return [
        item
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, Mapping) and item.get('equipped') and int_or_default(item.get('quantity'), default=1) > 0
    ]


def _slot(item: Mapping[str, Any]) -> str:
    return _key(item.get('slot') or item.get('equipmentSlot'))


def default_fighting_style(class_name: Any, items: Any) -> str:
    """Shield or a single one-handed melee weapon: dueling; two-handed melee: great weapon;
    ranged main weapon: archery; otherwise defense (skipping styles the class lacks)."""

    allowed = FIGHTING_STYLES.get(primary_class(class_name) or '', ())
    equipped = _equipped(items)
    weapons = [item for item in equipped if _key(item.get('type')) == 'weapon']
    shield = any(_key(item.get('type')) == 'armor' and 'shield' in _key(f"{item.get('subtype')} {item.get('name')}") for item in equipped)
    main = next((item for item in weapons if _slot(item) in {'main_hand', 'two_hands'}), weapons[0] if weapons else None)
    melee = [item for item in weapons if not weapon_properties(item)['ranged']]
    candidates = []
    if shield or (len(weapons) == 1 and melee and not weapon_properties(melee[0])['two_handed']):
        candidates.append('dueling')
    if main is not None and not weapon_properties(main)['ranged'] and weapon_properties(main)['two_handed']:
        candidates.append('great_weapon')
    if main is not None and weapon_properties(main)['ranged']:
        candidates.append('archery')
    candidates.append('defense')
    return next(style for style in candidates if style in allowed)


def fighting_style_for(class_name: Any, level: Any, *, chosen: Any = None, items: Any = None) -> str | None:
    """The stored ``character_sheet.fightingStyle`` when the class may take it, else the equipment default."""

    archetype = primary_class(class_name) or ''
    if archetype not in FIGHTING_STYLES or bounded_level(level) < FIGHTING_STYLE_LEVEL[archetype]:
        return None
    style = _key(chosen)
    style = 'great_weapon' if style == 'great_weapon_fighting' else style
    return style if style in FIGHTING_STYLES[archetype] else default_fighting_style(class_name, items)


def default_expertise(class_name: Any, skill_proficiencies: list[str]) -> list[str]:
    """A rogue without stored expertise doubles Stealth and their first other proficient skill."""

    if primary_class(class_name) != 'rogue':
        return []
    other = next((skill for skill in skill_proficiencies if skill != 'stealth'), None)
    return ['stealth', other] if other else ['stealth']


def jack_of_all_trades_bonus(class_name: Any, level: Any) -> int:
    if primary_class(class_name) != 'bard' or bounded_level(level) < 2:
        return 0
    return proficiency_bonus_for_level(level) // 2


# Combat helpers shared with the capability engine -------------------------------------


def has_effect(participant: Mapping[str, Any] | None, effect_id: str) -> bool:
    return any(effect['id'] == effect_id for effect in active_effects(participant))


def is_raging(participant: Mapping[str, Any] | None) -> bool:
    return isinstance(participant, Mapping) and has_effect(participant, f"rage:{_text(participant.get('id'))}")


def unarmed_damage_dice(class_name: Any, level: Any) -> str:
    if primary_class(class_name) != 'monk':
        return '1'
    return '1d6' if bounded_level(level) >= 5 else '1d4'


def challenge_at_most_half(participant: Mapping[str, Any]) -> bool:
    """Challenge 1/2 or lower, read from the creature's XP award (100 XP is challenge 1/2)."""

    reward = participant.get('xpReward')
    return reward is not None and int_or_default(reward, default=101) <= 100


# Modifier-engine providers ---------------------------------------------------------


def extra_attack_provider(participant: Mapping[str, Any], _combat: Mapping[str, Any] | None) -> int | None:
    archetype, level = _participant_class(participant)
    return 2 if archetype in EXTRA_ATTACK_CLASSES and level >= 5 else None


def danger_sense_provider(participant: Mapping[str, Any], role: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    archetype, level = _participant_class(participant)
    if (
        role != 'saver'
        or context.get('ability') != 'dexterity'
        or archetype != 'barbarian'
        or level < 2
        or condition_keys(participant) & (INCAPACITATING_CONDITIONS | {'blinded', 'deafened', 'dead'})
    ):
        return None
    return {'mode': 'advantage', 'reasons': ['Danger Sense']}


def rage_provider(participant: Mapping[str, Any], role: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    """Advantage on Strength checks and saves while raging and conscious."""

    if role not in {'checker', 'saver'} or context.get('ability') != 'strength':
        return None
    if not is_raging(participant) or not can_act(participant):
        return None
    return {'mode': 'advantage', 'reasons': ['Rage']}


def reckless_attack_provider(participant: Mapping[str, Any], role: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    """Advantage on melee weapon attacks using Strength this turn."""

    if role != 'attacker' or context.get('kind') != 'weapon_attack' or context.get('ranged') or context.get('ability') != 'strength':
        return None
    if not has_effect(participant, f"reckless_attack:{_text(participant.get('id'))}"):
        return None
    return {'mode': 'advantage', 'reasons': ['Reckless Attack']}


def fighting_style_provider(participant: Mapping[str, Any], role: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    style = _text(participant.get('fightingStyle'))
    if role == 'attacker' and style == 'archery' and context.get('kind') == 'weapon_attack' and context.get('ranged'):
        return {'attackFlatBonus': 2, 'reasons': ['Archery fighting style']}
    if role == 'target' and style == 'defense':
        breakdown = participant.get('armorClassBreakdown') if isinstance(participant.get('armorClassBreakdown'), Mapping) else {}
        if _text(breakdown.get('source')) == 'armor':
            return {'targetAcBonus': 1, 'reasons': ['Defense fighting style']}
    return None


def improved_critical_provider(participant: Mapping[str, Any], role: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    archetype, level = _participant_class(participant)
    if (
        role != 'attacker'
        or context.get('kind') != 'weapon_attack'
        or archetype != 'fighter'
        or level < 3
        or 'champion' not in subclass_name(participant.get('class')).lower()
    ):
        return None
    return {'criticalRangeBonus': 1}


def great_weapon_fighting_provider(participant: Mapping[str, Any], role: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    weapon = context.get('weapon') if isinstance(context.get('weapon'), Mapping) else {}
    if role != 'attacker' or _text(participant.get('fightingStyle')) != 'great_weapon' or not weapon:
        return None
    properties = weapon_properties(weapon)
    if properties['ranged'] or not properties['two_handed']:
        return None
    return {'damageRerollBelow': 2, 'reasons': ['Great Weapon Fighting']}


def _weapon_context(context: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    weapon = context.get('weapon') if isinstance(context.get('weapon'), Mapping) else {}
    attack = context.get('attack') if isinstance(context.get('attack'), Mapping) else {}
    return weapon, attack


def _used_this_turn(attack: Mapping[str, Any], key: str) -> bool:
    return key in {_text(value) for value in attack.get('oncePerTurnUsed') or []}


def sneak_attack_dice(level: Any) -> str:
    return f'{(bounded_level(level) + 1) // 2}d6'


def _ally_engaged_with(target: Mapping[str, Any], attacker: Mapping[str, Any], combat: Mapping[str, Any]) -> bool:
    attacker_id = _text(attacker.get('id'))
    for participant in combat.get('participants') or []:
        if (
            isinstance(participant, Mapping)
            and _text(participant.get('id')) != attacker_id
            and _text(participant.get('team')).lower() in {'player', 'ally'}
            and can_act(participant)
            and (_text(target.get('id')) in engaged_with(participant) or _text(participant.get('id')) in engaged_with(target))
        ):
            return True
    return False


def sneak_attack_provider(attacker: Mapping[str, Any], target: Mapping[str, Any], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    archetype, level = _participant_class(attacker)
    weapon, attack = _weapon_context(context)
    if archetype != 'rogue' or context.get('kind') != 'weapon_attack' or not weapon or not attack:
        return []
    properties = weapon_properties(weapon)
    if not (properties['finesse'] or properties['ranged']) or _used_this_turn(attack, 'sneak_attack'):
        return []
    mode = _text(attack.get('mode'))
    combat = attack.get('combat') if isinstance(attack.get('combat'), Mapping) else {}
    if mode != 'advantage' and (mode == 'disadvantage' or not _ally_engaged_with(target, attacker, combat)):
        return []
    return [{'dice': sneak_attack_dice(level), 'source': 'Sneak Attack', 'oncePerTurn': 'sneak_attack'}]


def colossus_slayer_provider(attacker: Mapping[str, Any], target: Mapping[str, Any], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    archetype, level = _participant_class(attacker)
    weapon, attack = _weapon_context(context)
    if (
        archetype != 'ranger'
        or level < 3
        or 'hunter' not in subclass_name(attacker.get('class')).lower()
        or context.get('kind') != 'weapon_attack'
        or not attack
        or _used_this_turn(attack, 'colossus_slayer')
    ):
        return []
    hp = target.get('hp') if isinstance(target.get('hp'), Mapping) else {}
    if int_or_default(hp.get('current'), default=0) >= int_or_default(hp.get('max'), default=0):
        return []
    return [{'dice': '1d8', 'source': 'Colossus Slayer', 'oncePerTurn': 'colossus_slayer'}]


def dueling_provider(attacker: Mapping[str, Any], _target: Mapping[str, Any], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    weapon, attack = _weapon_context(context)
    if _text(attacker.get('fightingStyle')) != 'dueling' or context.get('kind') != 'weapon_attack' or not weapon or not attack:
        return []
    properties = weapon_properties(weapon)
    if properties['ranged'] or properties['two_handed'] or weapon.get('otherWeaponEquipped') is not False:
        return []
    return [{'flat': 2, 'source': 'Dueling fighting style'}]


def agonizing_blast_provider(attacker: Mapping[str, Any], _target: Mapping[str, Any], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    archetype, level = _participant_class(attacker)
    if archetype != 'warlock' or level < 2 or context.get('kind') != 'spell_attack' or _text(context.get('spellId')) not in {'eldritch_blast', 'spell_eldritch_blast'}:
        return []
    bonus = ability_score_modifier(_stats(attacker), 'charisma')
    return [{'flat': bonus, 'type': 'force', 'source': 'Agonizing Blast'}] if bonus > 0 else []


def divine_smite_dice(slot_level: int, target: Mapping[str, Any]) -> str:
    count = min(5, 2 + max(0, slot_level - 1))
    if _key(target.get('creatureType') or target.get('creatureTypeName')) in {'undead', 'fiend'}:
        count += 1
    return f'{count}d8'


def attack_rider_provider(attacker: Mapping[str, Any], target: Mapping[str, Any], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Divine Smite's radiant dice when the attack declared it (the resolver adds them only on a hit)."""

    _weapon, attack = _weapon_context(context)
    rider = attack.get('rider') if isinstance(attack.get('rider'), Mapping) else {}
    if _text(rider.get('capabilityId')) != 'divine_smite' or primary_class(attacker.get('class')) != 'paladin':
        return []
    return [{'dice': divine_smite_dice(int_or_default(rider.get('slotLevel'), default=1), target), 'type': 'radiant', 'source': 'Divine Smite'}]


def register_class_feature_providers() -> None:
    """Register the providers once; registries ignore repeated registration."""

    register_attacks_per_action_provider(extra_attack_provider)
    for provider in (danger_sense_provider, rage_provider, reckless_attack_provider, fighting_style_provider,
                     improved_critical_provider, great_weapon_fighting_provider):
        register_modifier_provider(provider)
    for provider in (sneak_attack_provider, colossus_slayer_provider, dueling_provider, agonizing_blast_provider,
                     attack_rider_provider):
        register_extra_damage_provider(provider)


register_class_feature_providers()
