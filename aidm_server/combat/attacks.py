"""Engine attack, save and healing resolution shared by every combat path.

The player's HUD attack, enemy abilities, off-hand strikes and opportunity
attacks all roll through :func:`resolve_attack`: the modifier engine sets the
roll mode, bonus dice and armour class; a hit rolls damage (dice doubled on a
critical), raises ``attack_hit`` so reactions may cancel or scale it, applies
resistances, and raises ``damage_taken`` for whatever damage remains.
"""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import math
import random
import re
from typing import Any, Callable, Iterable, Mapping

from aidm_server.canon_text import int_or_default, normalized_name
from aidm_server.combat.legal_actions import weapon_damage_profile
from aidm_server.combat.modifiers import (
    MODIFIER_ENGINE_SOURCE,
    ability_key,
    apply_damage_multiplier,
    armor_class_with_context,
    attack_context,
    damage_multiplier,
    extra_damage,
    heals_nothing,
    roll_inspiration,
    save_context,
)
from aidm_server.combat.positioning import ability_reach, in_melee_reach
from aidm_server.combat.reactions import REACTION_SOURCE, collect_reactions
from aidm_server.combat.state import combat_ability_is_available, combat_ability_resolution_mode
from aidm_server.combat.vitality import is_player_character
from aidm_server.combat.working_state import CONDITION_DURATION_RANGE, apply_working_changes
from aidm_server.damage_dice import (
    deterministic_roller,
    normalize_damage_dice_expression,
    parse_damage_dice_expression,
    roll_signed_dice,
)
from aidm_server.game_state.models import normalize_item_name, stable_change_id
from aidm_server.weapon_proficiency import match_weapon_proficiency


Roller = Callable[[int], int]

TEXT_DAMAGE_PATTERN = re.compile(
    r'\b(?:deals?|does|for)\s+(\d{0,2}d\d{1,3}(?:\s*[+-]\s*\d{1,4})?)\s+'
    r'(acid|cold|fire|force|lightning|necrotic|poison|psychic|radiant|thunder|bludgeoning|piercing|slashing)\s+damage\b',
    re.IGNORECASE,
)
RANGED_ABILITY_PATTERN = re.compile(r'\b(?:(?:short|long|cross)?bow|sling|dart|javelin|ranged|shot|arrow)s?\b')
PC_RANGED_WEAPON_MARKERS = ('bow', 'crossbow', 'firearm', 'gun', 'pistol', 'rifle', 'sidearm', 'sling')
PC_FINESSE_WEAPON_MARKERS = ('dagger', 'knife', 'rapier', 'scimitar', 'shortsword', 'whip')
MAIN_HAND_SLOTS = {'main hand', 'two hands'}


def _text(value: Any) -> str:
    return str(value or '').strip()


def party_scaled_damage(attacker: Mapping[str, Any] | None, amount: int) -> int:
    """An enemy's damage roll under its encounter's party scaling: rounded down, at least 1."""

    multiplier = attacker.get('partyDamageMultiplier') if isinstance(attacker, Mapping) else None
    if amount <= 0 or not isinstance(multiplier, (int, float)) or isinstance(multiplier, bool) or multiplier == 1:
        return amount
    return max(1, math.floor(amount * Fraction(multiplier).limit_denominator(100)))


def roll_die(sides: int, roller: Roller | None) -> int:
    sides = max(1, int_or_default(sides, default=1))
    raw_value = roller(sides) if roller else random.randint(1, sides)
    return max(1, min(sides, int_or_default(raw_value, default=1)))


def roll_damage_expression(dice_expression: Any, roller: Roller | None, *, reroll_below: int = 0) -> dict[str, Any]:
    """Roll a damage expression; each die at or below ``reroll_below`` is rerolled once
    (Great Weapon Fighting) and the replaced faces are kept in ``rerolled``."""

    expression = str(dice_expression or '').strip().replace(' ', '')
    parsed = parse_damage_dice_expression(expression)
    if not parsed:
        return {'dice': expression[:24], 'rolls': [], 'bonus': 0, 'total': 0}
    count = int(parsed['count'])
    sides = int(parsed['sides'])
    bonus = int(parsed['bonus'])
    rolls: list[int] = []
    rerolled: list[int] = []
    for _ in range(count if sides > 0 else 0):
        face = roll_die(sides, roller)
        if face <= reroll_below:
            rerolled.append(face)
            face = roll_die(sides, roller)
        rolls.append(face)
    result = {'dice': parsed['dice'], 'rolls': rolls, 'bonus': bonus, 'total': max(0, sum(rolls) + bonus)}
    if rerolled:
        result['rerolled'] = rerolled
    return result


def critical_damage_expression(value: Any) -> str:
    """Double the dice (never the flat bonus) of a critical hit's damage expression."""

    parsed = parse_damage_dice_expression(value)
    if not parsed or int(parsed.get('count') or 0) <= 0:
        return str(value or '').strip()
    count = min(100, int(parsed['count']) * 2)
    sides = int(parsed['sides'])
    bonus = int(parsed.get('bonus') or 0)
    return f'{count}d{sides}{bonus:+d}' if bonus else f'{count}d{sides}'


def d20_with_mode(first: int, mode: str, roller: Roller | None) -> tuple[int, list[int]]:
    """Keep the higher (advantage) or lower (disadvantage) of two d20s; ``first`` is already rolled."""

    if mode not in {'advantage', 'disadvantage'}:
        return first, [first]
    second = roll_die(20, roller)
    kept = max(first, second) if mode == 'advantage' else min(first, second)
    return kept, [first, second]


def rolled_bonus_dice(dice: list[str], roller: Roller | None) -> list[dict[str, Any]]:
    rolled: list[dict[str, Any]] = []
    for expression in dice:
        result = roll_signed_dice(expression, lambda sides: roll_die(sides, roller))
        if result:
            rolled.append(result)
    return rolled


def damage_component(
    *,
    dice: Any = None,
    flat: Any = None,
    bonus: int = 0,
    damage_type: str,
    source: str,
    target: Mapping[str, Any],
    critical: bool,
    roller: Roller | None,
    reroll_below: int = 0,
) -> dict[str, Any]:
    if dice:
        expression = critical_damage_expression(dice) if critical else str(dice)
        rolled = roll_damage_expression(expression, roller, reroll_below=reroll_below)
    else:
        expression = str(int_or_default(flat, default=0))
        rolled = {'dice': expression, 'rolls': [], 'bonus': int_or_default(flat, default=0), 'total': max(0, int_or_default(flat, default=0))}
    before = max(0, int(rolled.get('total') or 0) + bonus)
    multiplier = damage_multiplier(target, damage_type)
    component = {
        'source': source,
        'dice': rolled.get('dice') or expression,
        'rolls': rolled.get('rolls') or [],
        'bonus': int(rolled.get('bonus') or 0) + bonus,
        'type': damage_type,
        'multiplier': multiplier,
        'total': apply_damage_multiplier(before, multiplier),
    }
    if rolled.get('rerolled'):
        component['rerolled'] = rolled['rerolled']
    return component


def bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int_or_default(value, default=default)))


def target_armor_class(target: Mapping[str, Any] | None) -> int:
    if not isinstance(target, Mapping):
        return 10
    stats = target.get('stats') if isinstance(target.get('stats'), Mapping) else {}
    return bounded_int(target.get('armorClass', stats.get('armorClass')), default=10, minimum=1, maximum=40)


def attack_armor_class(target: Mapping[str, Any] | None, modifiers: Mapping[str, Any]) -> int:
    """The armour class an attack is compared against: the target's, with effects and cover."""

    return armor_class_with_context(target_armor_class(target), modifiers)


def ability_modifier(score: Any) -> int:
    return (int_or_default(score, default=10) - 10) // 2


def proficiency_bonus(participant: Mapping[str, Any] | None) -> int:
    level = int_or_default((participant or {}).get('level'), default=1) if isinstance(participant, Mapping) else 1
    return 2 + max(0, (level - 1) // 4)


def ability_is_ranged(ability: Mapping[str, Any] | None) -> bool:
    text = normalize_item_name(
        ' '.join(str((ability or {}).get(key) or '') for key in ('name', 'description', 'range'))
    )
    return bool(RANGED_ABILITY_PATTERN.search(text))


def ability_reach_profile(ability: Mapping[str, Any] | None) -> tuple[bool, frozenset[str]]:
    """``(melee, allowed_bands)`` of an enemy ability."""

    return ability_reach(ability, ranged_by_text=ability_is_ranged(ability))


def _attack_stat_modifier(enemy: Mapping[str, Any] | None, ability: Mapping[str, Any] | None) -> int:
    stats = enemy.get('stats') if isinstance(enemy, Mapping) and isinstance(enemy.get('stats'), Mapping) else {}
    strength = ability_modifier(stats.get('strength'))
    dexterity = ability_modifier(stats.get('dexterity'))
    return dexterity if ability_is_ranged(ability) else max(strength, dexterity)


def ability_attack_bonus(enemy: Mapping[str, Any] | None, ability: Mapping[str, Any] | None) -> int:
    if isinstance(ability, Mapping):
        explicit = ability.get('attackBonus', ability.get('toHitBonus'))
        if explicit is not None:
            return int_or_default(explicit, default=0)
    return proficiency_bonus(enemy) + _attack_stat_modifier(enemy, ability)


def ability_damage(ability: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ability, Mapping):
        return {}
    damage = ability.get('damage')
    if isinstance(damage, Mapping):
        normalized_dice = normalize_damage_dice_expression(damage.get('dice'))
        result = {'type': damage.get('type') or damage.get('damageType')}
        if normalized_dice:
            result['dice'] = normalized_dice
        return result
    if isinstance(damage, str):
        normalized_dice = normalize_damage_dice_expression(damage)
        return {'dice': normalized_dice} if normalized_dice else {}
    match = TEXT_DAMAGE_PATTERN.search(str(ability.get('description') or ''))
    if match:
        normalized_dice = normalize_damage_dice_expression(match.group(1).replace(' ', ''))
        if normalized_dice:
            return {'dice': normalized_dice, 'type': match.group(2).lower()}
    return {}


def ability_conditions(ability: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(ability, Mapping):
        return []
    raw = ability.get('conditionsApplied', ability.get('conditions'))
    values = raw if isinstance(raw, list) else [raw]
    conditions: list[str] = []
    for value in values:
        condition = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
        if condition and condition not in conditions:
            conditions.append(condition)
    return conditions


def condition_duration_fields(ability: Mapping[str, Any], *, cast_id: str, applied_during_turn_of: str) -> dict[str, Any]:
    """What a ``combat.condition.add`` from an enemy ability carries so the condition wears off.

    It lasts to the end of its bearer's next turn, or for the ability's
    ``durationRounds`` (1–10) of them; an ability with a save repeats it at the
    end of each of those turns (see :func:`aidm_server.combat.working_state.enemy_condition_effect`).
    """

    rounds = ability.get('durationRounds')
    low, high = CONDITION_DURATION_RANGE
    fields: dict[str, Any] = {
        'durationRounds': rounds if isinstance(rounds, int) and not isinstance(rounds, bool) and low <= rounds <= high else 1,
        'castId': cast_id,
        'sourceAbilityName': _text(ability.get('name')) or None,
        'appliedDuringTurnOf': applied_during_turn_of,
    }
    if isinstance(ability.get('save'), Mapping):
        save = ability_save(ability)
        fields['endSave'] = {'ability': save['ability'], 'dc': save['dc']}
    return fields


def saving_throw_modifier(target: Mapping[str, Any] | None, ability_name: Any) -> int:
    if not isinstance(target, Mapping):
        return 0
    ability = ability_key(ability_name)
    short = ability[:3]
    stats = target.get('stats') if isinstance(target.get('stats'), Mapping) else {}
    for source in (target.get('savingThrows'), target.get('saving_throws'), stats.get('savingThrows'), stats.get('saving_throws')):
        if not isinstance(source, Mapping):
            continue
        for key in (ability, short, f'{ability}Save', f'{ability}_save'):
            if source.get(key) is not None:
                return int_or_default(source.get(key), default=0)
    modifier = ability_modifier(stats.get(ability, target.get(ability)))
    proficiencies = {
        ability_key(value)
        for value in (target.get('savingThrowProficiencies') or stats.get('savingThrowProficiencies') or [])
    }
    if ability in proficiencies:
        modifier += proficiency_bonus(target)
    return modifier


def ability_save(ability: Mapping[str, Any] | None) -> dict[str, Any]:
    save = ability.get('save') if isinstance(ability, Mapping) and isinstance(ability.get('save'), Mapping) else {}
    return {
        'ability': str(save.get('ability') or '').strip().lower(),
        'dc': max(1, min(40, int_or_default(save.get('dc'), default=10))),
        'effectOnSuccess': str(save.get('effectOnSuccess') or save.get('effect_on_success') or 'none').strip().lower(),
    }


def resolve_save(
    target: Mapping[str, Any],
    save: Mapping[str, Any],
    combat: Mapping[str, Any],
    roller: Roller | None,
) -> dict[str, Any]:
    """Roll one saving throw for a target through the modifier engine.

    A failed save the target's Bardic Inspiration die can still change adds
    the die (``inspiration``) and spends it through ``consume``.
    """

    context = save_context(target, save['ability'], combat=combat)
    modifier = saving_throw_modifier(target, save['ability'])
    if context['autoFail']:
        return {
            'saveAbility': save['ability'],
            'saveDC': save['dc'],
            'saveRoll': None,
            'saveRolls': [],
            'saveMode': context['mode'],
            'saveModifier': modifier,
            'saveBonusDice': [],
            'saveTotal': None,
            'saveSucceeded': False,
            'saveAutoFailed': True,
            'saveReasons': context['reasons'],
            'consume': context['consume'],
        }
    natural, rolls = d20_with_mode(roll_die(20, roller), context['mode'], roller)
    bonus_dice = rolled_bonus_dice(context['bonusDice'], roller)
    total = natural + modifier + int(context['flatBonus']) + sum(int(item['total']) for item in bonus_dice)
    inspiration = None if total >= save['dc'] else roll_inspiration(target, lambda sides: roll_die(sides, roller))
    if inspiration:
        total += inspiration['roll']
        context['consume'].append(inspiration.pop('consume'))
    result = {
        'saveAbility': save['ability'],
        'saveDC': save['dc'],
        'saveRoll': natural,
        'saveRolls': rolls,
        'saveMode': context['mode'],
        'saveModifier': modifier,
        'saveBonusDice': bonus_dice,
        'saveTotal': total,
        'saveSucceeded': total >= save['dc'],
        'saveAutoFailed': False,
        'saveReasons': context['reasons'],
        'consume': context['consume'],
    }
    if inspiration:
        result['inspiration'] = inspiration
    return result


def _multiplier_product(reactions: list[dict[str, Any]]) -> float | None:
    factors = [float(reaction['damageMultiplier']) for reaction in reactions if reaction.get('damageMultiplier') is not None]
    if not factors:
        return None
    product = 1.0
    for factor in factors:
        product *= max(0.0, factor)
    return product


def resolve_attack(
    *,
    attacker: Mapping[str, Any],
    target: Mapping[str, Any],
    combat: dict[str, Any],
    state: Mapping[str, Any],
    kind: str,
    ranged: bool,
    first_d20: int,
    attack_bonus: int,
    attack_roller: Roller | None,
    damage: Mapping[str, Any],
    damage_roller: Roller | None,
    event_id: str,
    turn_id: Any,
    weapon: Mapping[str, Any] | None = None,
    ability: Any = None,
    rider: Mapping[str, Any] | None = None,
    once_per_turn_used: Iterable[Any] = (),
    armor_class: int | None = None,
) -> dict[str, Any]:
    """One attack roll with its damage and reactions.

    ``damage`` is ``{"dice", "type", "bonus", "source"}``; weapon attacks also
    roll the attacker's extra damage.  ``weapon`` (descriptor with its rules
    properties), ``ability`` (the ability the attack uses), ``rider`` (an
    attack rider declared with it) and ``once_per_turn_used`` (once-per-turn
    damage this attacker already dealt this turn) reach the modifier and
    extra-damage providers; ``oncePerTurn`` in the result lists the keys this
    hit used.  A miss that the attacker's Bardic Inspiration die turns into a
    hit records ``inspiration`` and spends it through ``consume``.  Reactions
    raised by the hit are applied to ``combat`` (the caller's working copy) and
    returned in ``reactions``.
    """

    modifiers = attack_context(attacker, target, kind=kind, ranged=ranged, combat=combat, weapon=weapon, ability=ability)
    natural_roll, attack_rolls = d20_with_mode(first_d20, modifiers['mode'], attack_roller)
    bonus_dice = rolled_bonus_dice(modifiers['attackBonusDice'], attack_roller)
    attack_total = natural_roll + attack_bonus + sum(int(item['total']) for item in bonus_dice) + int(modifiers['attackFlatBonus'])
    # ``armor_class``: the number a player's roll was already shown against (see attack_armor_class).
    target_ac = armor_class if armor_class is not None else attack_armor_class(target, modifiers)
    critical_threshold = 20 - max(0, int(modifiers['criticalRangeBonus']))
    hit = natural_roll != 1 and (natural_roll >= critical_threshold or attack_total >= target_ac)
    inspiration = None
    if not hit and natural_roll != 1:
        inspiration = roll_inspiration(attacker, lambda sides: roll_die(sides, attack_roller))
        if inspiration:
            attack_total += inspiration['roll']
            modifiers['consume'].append(inspiration.pop('consume'))
            hit = attack_total >= target_ac
    critical = hit and (natural_roll >= critical_threshold or modifiers['autoCrit'])
    damage_type = _text(damage.get('type')).lower()
    components: list[dict[str, Any]] = []
    once_per_turn: list[str] = []
    if hit:
        components.append(
            damage_component(
                dice=damage.get('dice'),
                bonus=int_or_default(damage.get('bonus'), default=0),
                damage_type=damage_type,
                source=_text(damage.get('source')) or 'attack',
                target=target,
                critical=critical,
                roller=damage_roller,
                reroll_below=int(modifiers['damageRerollBelow']) if kind == 'weapon_attack' else 0,
            )
        )
        if kind == 'weapon_attack':
            attack = {
                'mode': modifiers['mode'],
                'ranged': ranged,
                'ability': ability_key(ability) if _text(ability) else None,
                'critical': critical,
                'combat': combat,
                'rider': dict(rider) if isinstance(rider, Mapping) else None,
                'oncePerTurnUsed': [_text(key) for key in once_per_turn_used if _text(key)],
            }
            for extra in extra_damage(attacker, target, kind=kind, weapon={**(weapon or {}), 'damageType': damage_type}, attack=attack):
                component = damage_component(
                    dice=extra.get('dice'),
                    flat=extra.get('flat'),
                    damage_type=str(extra.get('type') or damage_type),
                    source=str(extra.get('source') or 'effect'),
                    target=target,
                    critical=critical,
                    roller=damage_roller,
                )
                if _text(extra.get('oncePerTurn')):
                    component['oncePerTurn'] = _text(extra['oncePerTurn'])
                    once_per_turn.append(component['oncePerTurn'])
                components.append(component)
    damage_total = party_scaled_damage(attacker, sum(int(component['total']) for component in components))
    reactions: list[dict[str, Any]] = []
    hit_cancelled_by = None
    if hit:
        reactions = collect_reactions(
            {
                'type': 'attack_hit',
                'attackerId': _text(attacker.get('id')),
                'targetId': _text(target.get('id')),
                'attackTotal': attack_total,
                'targetAc': target_ac,
                'damage': damage_total,
                'critical': critical,
                'eventId': f'{event_id}:hit',
                'turnId': turn_id,
            },
            combat,
            state,
        )
        cancelling = next((reaction for reaction in reactions if reaction['cancelsHit']), None)
        if cancelling is not None:
            hit, critical, components, damage_total = False, False, [], 0
            hit_cancelled_by = cancelling['label']
        multiplier = _multiplier_product(reactions)
        if multiplier is not None:
            damage_total = apply_damage_multiplier(damage_total, multiplier)
    if damage_total > 0:
        reactions.extend(
            collect_reactions(
                {
                    'type': 'damage_taken',
                    'targetId': _text(target.get('id')),
                    'sourceId': _text(attacker.get('id')),
                    'amount': damage_total,
                    'damageType': damage_type,
                    'eventId': f'{event_id}:damage',
                    'turnId': turn_id,
                },
                combat,
                state,
            )
        )
    base = components[0] if components else {}
    result = {
        'attackMode': modifiers['mode'],
        'attackRolls': attack_rolls,
        'modeReasons': modifiers['reasons'],
        'attackRoll': natural_roll,
        'attackBonus': attack_bonus,
        'attackBonusDice': bonus_dice,
        'attackTotal': attack_total,
        'targetArmorClass': target_ac,
        'coverBonus': modifiers['coverBonus'],
        'hit': hit,
        'critical': critical,
        'damageDice': base.get('dice') or damage.get('dice'),
        'damageRolls': base.get('rolls') or [],
        'damageBonus': int(base.get('bonus') or 0),
        'damageComponents': components,
        'damageTotal': damage_total,
        'damageType': damage_type or None,
        'consume': modifiers['consume'],
        'reactions': reactions,
    }
    if hit_cancelled_by:
        result['hitCancelledBy'] = hit_cancelled_by
    if inspiration:
        result['inspiration'] = inspiration
    if once_per_turn and hit:
        result['oncePerTurn'] = once_per_turn
    return result


def resolve_enemy_ability(
    *,
    enemy: Mapping[str, Any],
    target: Mapping[str, Any],
    ability: Mapping[str, Any],
    combat: dict[str, Any],
    state: Mapping[str, Any],
    roller: Roller | None,
    event_id: str,
    turn_id: Any,
) -> dict[str, Any] | None:
    """Resolve an enemy's attack or save ability against one target, or None when it cannot."""

    resolution_mode = combat_ability_resolution_mode(dict(ability))
    if resolution_mode not in {'attack', 'attack_with_save', 'save'}:
        return None
    damage = ability_damage(ability)
    damage_type = _text(damage.get('type') or damage.get('damageType')).lower()
    conditions = ability_conditions(ability)
    entry: dict[str, Any] = {'resolutionMode': resolution_mode}
    consumed: list[dict[str, Any]] = []
    if resolution_mode in {'attack', 'attack_with_save'}:
        if not damage.get('dice'):
            return None
        melee, _bands = ability_reach_profile(ability)
        attack = resolve_attack(
            attacker=enemy,
            target=target,
            combat=combat,
            state=state,
            kind='enemy_attack',
            ranged=not melee,
            first_d20=roll_die(20, roller),
            attack_bonus=ability_attack_bonus(enemy, ability),
            attack_roller=roller,
            damage={'dice': damage.get('dice'), 'type': damage_type, 'bonus': 0, 'source': _text(ability.get('name')) or 'attack'},
            damage_roller=roller,
            event_id=event_id,
            turn_id=turn_id,
        )
        consumed.extend(attack.pop('consume'))
        attack.pop('damageComponents')
        entry.update(attack)
        entry['conditionsApplied'] = []
        if attack['hit'] and resolution_mode == 'attack_with_save':
            save_result = resolve_save(target, ability_save(ability), combat, roller)
            consumed.extend(save_result.pop('consume'))
            entry.update(save_result)
            entry['conditionsApplied'] = [] if save_result['saveSucceeded'] else conditions
    else:
        save = ability_save(ability)
        save_result = resolve_save(target, save, combat, roller)
        consumed.extend(save_result.pop('consume'))
        save_succeeded = save_result['saveSucceeded']
        damage_roll = (
            roll_damage_expression(damage.get('dice'), roller)
            if damage.get('dice')
            else {'dice': None, 'rolls': [], 'bonus': 0, 'total': 0}
        )
        damage_total = party_scaled_damage(enemy, int_or_default(damage_roll.get('total'), default=0))
        if save_succeeded:
            damage_total = damage_total // 2 if save['effectOnSuccess'] in {'half', 'half_damage'} else 0
        damage_total = apply_damage_multiplier(damage_total, damage_multiplier(target, damage_type))
        reactions = (
            collect_reactions(
                {
                    'type': 'damage_taken',
                    'targetId': _text(target.get('id')),
                    'sourceId': _text(enemy.get('id')),
                    'amount': damage_total,
                    'damageType': damage_type,
                    'eventId': f'{event_id}:damage',
                    'turnId': turn_id,
                },
                combat,
                state,
            )
            if damage_total > 0
            else []
        )
        entry.update(save_result)
        entry.update(
            {
                'hit': damage_total > 0,
                'critical': False,
                'damageDice': damage.get('dice'),
                'damageRolls': damage_roll.get('rolls') or [],
                'damageBonus': damage_roll.get('bonus') or 0,
                'damageTotal': damage_total,
                'damageType': damage_type or None,
                'conditionsApplied': [] if save_succeeded else conditions,
                'reactions': reactions,
            }
        )
    entry['consume'] = consumed
    return entry


def resolve_enemy_healing(
    *,
    enemy: Mapping[str, Any],
    ability: Mapping[str, Any],
    combat: Mapping[str, Any],
    roller: Roller | None,
) -> dict[str, Any] | None:
    """Heal the enemy itself (``targetType`` self) or its most wounded ally."""

    target_type = _text(ability.get('targetType', ability.get('target_type'))).lower() or 'single'
    team = _text(enemy.get('team'))
    if target_type == 'self':
        candidates = [enemy]
    else:
        candidates = [
            participant
            for participant in combat.get('participants') or []
            if isinstance(participant, Mapping)
            and _text(participant.get('team')) == team
            and participant.get('isAlive') is not False
            and int_or_default((participant.get('hp') or {}).get('current'), default=0) > 0
        ]
    wounded = [
        participant
        for participant in candidates
        if not heals_nothing(participant)
        and int_or_default((participant.get('hp') or {}).get('current'), default=0)
        < int_or_default((participant.get('hp') or {}).get('max'), default=0)
    ]
    if not wounded:
        return None
    target = min(
        wounded,
        key=lambda participant: (
            int_or_default((participant.get('hp') or {}).get('current'), default=0)
            / max(1, int_or_default((participant.get('hp') or {}).get('max'), default=1)),
            _text(participant.get('id')),
        ),
    )
    healing = ability.get('healing') if isinstance(ability.get('healing'), Mapping) else {}
    rolled = roll_damage_expression(healing.get('dice'), roller)
    hp = target.get('hp') if isinstance(target.get('hp'), Mapping) else {}
    current = int_or_default(hp.get('current'), default=0)
    maximum = int_or_default(hp.get('max'), default=current)
    healed = max(0, min(int(rolled['total']), maximum - current))
    return {
        'resolutionMode': 'heal',
        'healTargetId': _text(target.get('id')),
        'healTargetName': _text(target.get('name')) or _text(target.get('id')),
        'healingDice': rolled['dice'],
        'healingRolls': rolled['rolls'],
        'healingTotal': healed,
        'resultingHp': {'current': current + healed, 'max': maximum, 'temp': int_or_default(hp.get('temp'), default=0)},
        'hit': False,
        'damageTotal': 0,
        'conditionsApplied': [],
        'consume': [],
        'reactions': [],
    }


def _actor(state: Mapping[str, Any], participant_id: str) -> Mapping[str, Any]:
    for actor in state.get('playerCharacters') or []:
        if isinstance(actor, Mapping) and _text(actor.get('id')) == participant_id:
            return actor
    return {}


def _weapon_labels(weapon: Mapping[str, Any]) -> str:
    return ' '.join(
        normalized_name(value)
        for value in (weapon.get('name'), weapon.get('subtype'), *(weapon.get('tags') or []))
        if _text(value)
    )


def pc_main_hand_melee_weapon(actor: Mapping[str, Any]) -> dict[str, Any] | None:
    inventory = actor.get('inventory') if isinstance(actor.get('inventory'), Mapping) else {}
    melee = [
        item
        for item in inventory.get('items') or []
        if isinstance(item, Mapping)
        and normalized_name(item.get('type')) == 'weapon'
        and item.get('equipped')
        and not any(marker in _weapon_labels(item) for marker in PC_RANGED_WEAPON_MARKERS)
    ]
    main_hand = [item for item in melee if normalized_name(item.get('slot') or item.get('equipmentSlot')) in MAIN_HAND_SLOTS]
    chosen = main_hand[0] if main_hand else (melee[0] if len(melee) == 1 else None)
    return dict(chosen) if chosen else None


def attack_weapon_descriptor(actor: Mapping[str, Any], weapon: Mapping[str, Any], *, ranged: bool) -> dict[str, Any]:
    """The weapon as class features read it: its names and tags (finesse, light, two-handed),
    melee or ranged, and whether the character has another weapon equipped (Dueling)."""

    weapon_id = _text(weapon.get('id'))
    inventory = actor.get('inventory') if isinstance(actor.get('inventory'), Mapping) else {}
    other_weapon = any(
        isinstance(item, Mapping)
        and normalized_name(item.get('type')) == 'weapon'
        and item.get('equipped')
        and _text(item.get('id')) != weapon_id
        for item in inventory.get('items') or []
    )
    descriptor = {key: deepcopy(weapon[key]) for key in ('id', 'name', 'subtype', 'aliases', 'tags', 'slot') if weapon.get(key)}
    return {**descriptor, 'classification': 'ranged' if ranged else 'melee', 'otherWeaponEquipped': other_weapon}


def pc_weapon_attack_profile(actor: Mapping[str, Any], participant: Mapping[str, Any], weapon: Mapping[str, Any]) -> dict[str, Any]:
    """Attack bonus, damage, ability and weapon descriptor of a player character's melee weapon."""

    stats = actor.get('stats') if isinstance(actor.get('stats'), Mapping) else participant.get('stats') or {}
    strength = ability_modifier(stats.get('strength'))
    dexterity = ability_modifier(stats.get('dexterity'))
    finesse = any(marker in _weapon_labels(weapon) for marker in PC_FINESSE_WEAPON_MARKERS)
    modifier = max(strength, dexterity) if finesse else strength
    metadata = actor.get('metadata') if isinstance(actor.get('metadata'), Mapping) else {}
    proficient, _selector = match_weapon_proficiency(metadata.get('weaponProficiencies'), dict(weapon))
    hp = participant.get('hp') if isinstance(participant.get('hp'), Mapping) else {}
    ratio = int_or_default(hp.get('current'), default=0) / max(1, int_or_default(hp.get('max'), default=1))
    wound_penalty = 4 if ratio <= 0.25 else 2 if ratio <= 0.5 else 1 if ratio <= 0.75 else 0
    profile = weapon_damage_profile(dict(weapon), 'melee')
    return {
        'attackBonus': modifier + (proficiency_bonus(participant) if proficient else 0) - wound_penalty,
        'damage': {'dice': profile['dice'], 'type': profile['type'], 'bonus': modifier, 'source': _text(weapon.get('name')) or 'weapon'},
        'ability': 'dexterity' if finesse and dexterity > strength else 'strength',
        'weapon': attack_weapon_descriptor(actor, weapon, ranged=False),
    }


def first_melee_attack_ability(enemy: Mapping[str, Any], round_number: Any) -> dict[str, Any] | None:
    """The enemy's first always-ready melee attack ability, used for opportunity attacks."""

    for ability in enemy.get('abilities') or []:
        if not isinstance(ability, dict):
            continue
        cooldown = _text(ability.get('cooldown')).lower() or 'none'
        if (
            cooldown == 'none'
            and combat_ability_is_available(ability, round_number=round_number)
            and combat_ability_resolution_mode(ability) in {'attack', 'attack_with_save'}
            and ability_reach_profile(ability)[0]
        ):
            return ability
    return None


def damage_changes(
    *,
    target: Mapping[str, Any],
    amount: int,
    damage_type: Any,
    critical: bool,
    change_id: str,
    turn_id: Any,
    source: str,
    source_id: str,
    reason: str,
) -> list[dict[str, Any]]:
    """The trusted change dealing final damage to one participant."""

    if amount <= 0:
        return []
    target_id = _text(target.get('id'))
    change: dict[str, Any] = {'id': change_id, 'turnId': turn_id, 'source': source, 'sourceId': source_id, 'reason': reason, 'visible': True}
    if is_player_character(target):
        change.update({'type': 'health.damage', 'actorId': target_id, 'amount': amount})
        if critical:
            change['critical'] = True
    else:
        hp = target.get('hp') if isinstance(target.get('hp'), Mapping) else {}
        current = max(0, int_or_default(hp.get('current'), default=0))
        temp = max(0, int_or_default(hp.get('temp'), default=0))
        absorbed = min(temp, amount)
        resulting = max(0, current - (amount - absorbed))
        change.update(
            {
                'type': 'combat.participant.update',
                'participantId': target_id,
                'hp': {'current': resulting, 'max': max(current, int_or_default(hp.get('max'), default=current)), 'temp': temp - absorbed},
                'isAlive': resulting > 0,
                'isConscious': resulting > 0,
                'damageAmount': amount,
            }
        )
    if _text(damage_type):
        change['damageType'] = _text(damage_type).lower()
    return [change]


def resolve_opportunity_attack(
    reactor: Mapping[str, Any],
    mover: Mapping[str, Any],
    *,
    combat: dict[str, Any],
    state: Mapping[str, Any],
    event: Mapping[str, Any],
) -> dict[str, Any] | None:
    """One melee strike against a participant leaving melee; applies its damage to ``combat``."""

    reactor_id = _text(reactor.get('id'))
    event_id = f"{_text(event.get('eventId'))}:opportunity:{reactor_id}"
    turn_id = event.get('turnId')
    roller = deterministic_roller(f'opportunity_attack:{turn_id}:{event_id}')
    if not in_melee_reach(reactor, mover):
        return None
    if _text(reactor.get('team')) == 'enemy':
        ability = first_melee_attack_ability(reactor, combat.get('round'))
        if ability is None:
            return None
        result = resolve_enemy_ability(
            enemy=reactor, target=mover, ability=ability, combat=combat, state=state, roller=roller, event_id=event_id, turn_id=turn_id,
        )
        if result is None:
            return None
        result['abilityId'] = _text(ability.get('id'))
        result['abilityName'] = _text(ability.get('name')) or None
    else:
        ability = None
        actor = _actor(state, reactor_id)
        weapon = pc_main_hand_melee_weapon(actor)
        if weapon is None:
            return None
        profile = pc_weapon_attack_profile(actor, reactor, weapon)
        result = resolve_attack(
            attacker=reactor,
            target=mover,
            combat=combat,
            state=state,
            kind='weapon_attack',
            ranged=False,
            first_d20=roll_die(20, roller),
            attack_bonus=profile['attackBonus'],
            attack_roller=roller,
            damage=profile['damage'],
            damage_roller=roller,
            event_id=event_id,
            turn_id=turn_id,
            weapon=profile['weapon'],
            ability=profile['ability'],
        )
        result['weaponId'] = _text(weapon.get('id')) or None
        result['weaponName'] = _text(weapon.get('name')) or None
    nested_changes = [change for reaction in result.get('reactions') or [] for change in reaction.get('changes') or []]
    own_changes = damage_changes(
        target=mover,
        amount=int_or_default(result.get('damageTotal'), default=0),
        damage_type=result.get('damageType'),
        critical=result.get('critical') is True,
        change_id=stable_change_id('opportunity_attack_damage', turn_id, event_id),
        turn_id=turn_id,
        source=REACTION_SOURCE,
        source_id=reactor_id,
        reason=f"Opportunity attack: {_text(reactor.get('name')) or reactor_id} hit {_text(mover.get('name')) or 'the mover'} for {result.get('damageTotal')} damage.",
    )
    for condition in result.get('conditionsApplied') or []:
        own_changes.append(
            {
                'id': stable_change_id('opportunity_attack_condition', turn_id, event_id, condition),
                'turnId': turn_id,
                'type': 'combat.condition.add',
                'participantId': _text(mover.get('id')),
                'condition': condition,
                'source': REACTION_SOURCE,
                'sourceEnemyId': reactor_id,
                'sourceAbilityId': _text(ability.get('id')) if ability else None,
                # Struck on its own turn, the mover carries it to the end of its next one.
                **condition_duration_fields(
                    ability or {},
                    cast_id=stable_change_id('opportunity_attack_conditions', turn_id, event_id),
                    applied_during_turn_of=_text(mover.get('id')),
                ),
                'reason': f"Opportunity attack applied {condition}.",
                'visible': True,
            }
        )
    consume = result.pop('consume', [])
    if consume:
        own_changes.append(
            {
                'id': stable_change_id('opportunity_attack_consumed', turn_id, event_id),
                'turnId': turn_id,
                'type': 'combat.effect.consume',
                'consume': deepcopy(consume),
                'source': MODIFIER_ENGINE_SOURCE,
                'reason': 'Effects spent by an engine-resolved opportunity attack.',
                'visible': False,
            }
        )
    apply_working_changes(combat, own_changes)
    result.update({'attackerId': reactor_id, 'attackerName': _text(reactor.get('name')) or reactor_id, 'targetId': _text(mover.get('id')), 'targetName': _text(mover.get('name'))})
    return {'result': result, 'changes': [*nested_changes, *own_changes]}
