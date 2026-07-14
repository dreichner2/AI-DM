"""Small authoritative catalog of fully usable class capabilities.

The game deliberately starts with a few complete mechanics instead of exposing
every class feature as decorative text.  Capability state is JSON-safe and can
live in the existing player stats/session snapshot without a schema migration.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from aidm_server.canon_text import int_or_default
from aidm_server.character_progression import class_archetype


Roller = Callable[[int], int]


CLASS_CAPABILITY_CATALOG: dict[str, tuple[dict[str, Any], ...]] = {
    'fighter': (
        {
            'id': 'second_wind',
            'name': 'Second Wind',
            'minimumLevel': 1,
            'actionEconomy': 'bonus_action',
            'targetPolicy': 'self',
            'refreshesOn': 'short_rest',
            'maxUses': 1,
            'effect': {'type': 'heal', 'dice': {'count': 1, 'sides': 10}, 'levelBonus': True},
            'description': 'Recover 1d10 + fighter level hit points as a bonus action.',
        },
        {
            'id': 'action_surge',
            'name': 'Action Surge',
            'minimumLevel': 2,
            'actionEconomy': 'free',
            'targetPolicy': 'self',
            'refreshesOn': 'short_rest',
            'maxUses': 1,
            'effect': {'type': 'restore_action', 'amount': 1},
            'description': 'Regain the action for the current combat turn.',
        },
    ),
    'paladin': (
        {
            'id': 'lay_on_hands',
            'name': 'Lay on Hands',
            'minimumLevel': 1,
            'actionEconomy': 'action',
            'targetPolicy': 'self_or_ally',
            'refreshesOn': 'long_rest',
            'poolPerLevel': 5,
            'effect': {'type': 'healing_pool'},
            'description': 'Spend points from a 5-per-level pool to restore hit points.',
        },
    ),
}


def _text(value: Any) -> str:
    return str(value or '').strip()


def _bounded_level(value: Any) -> int:
    return max(1, min(20, int_or_default(value, default=1)))


def capabilities_for_class(class_name: Any, level: Any) -> list[dict[str, Any]]:
    archetype = class_archetype(class_name) or ''
    character_level = _bounded_level(level)
    capabilities: list[dict[str, Any]] = []
    for definition in CLASS_CAPABILITY_CATALOG.get(archetype, ()):
        minimum_level = max(1, int_or_default(definition.get('minimumLevel'), default=1))
        if character_level < minimum_level:
            continue
        capability = deepcopy(definition)
        if capability.get('poolPerLevel') is not None:
            capability['maxUses'] = max(
                1,
                int_or_default(capability.get('poolPerLevel'), default=1) * character_level,
            )
        capabilities.append(capability)
    return capabilities


def normalize_class_feature_state(
    raw_state: Any,
    *,
    class_name: Any,
    level: Any,
) -> dict[str, dict[str, Any]]:
    source = raw_state if isinstance(raw_state, dict) else {}
    normalized: dict[str, dict[str, Any]] = {}
    for capability in capabilities_for_class(class_name, level):
        capability_id = _text(capability.get('id'))
        maximum = max(1, int_or_default(capability.get('maxUses'), default=1))
        existing = source.get(capability_id) if isinstance(source.get(capability_id), dict) else {}
        current = int_or_default(
            existing.get('current', existing.get('remaining', maximum)),
            default=maximum,
        )
        normalized[capability_id] = {
            'current': max(0, min(maximum, current)),
            'max': maximum,
            'refreshesOn': capability.get('refreshesOn') or 'long_rest',
        }
        if existing.get('usedAtTurn') is not None:
            normalized[capability_id]['usedAtTurn'] = max(
                0,
                int_or_default(existing.get('usedAtTurn'), default=0),
            )
    return normalized


def capability_by_id(class_name: Any, level: Any, capability_id: Any) -> dict[str, Any] | None:
    requested = _text(capability_id)
    return next(
        (
            capability
            for capability in capabilities_for_class(class_name, level)
            if _text(capability.get('id')) == requested
        ),
        None,
    )


def _health(actor: dict[str, Any]) -> tuple[int, int]:
    health = actor.get('health') if isinstance(actor.get('health'), dict) else {}
    maximum = max(0, int_or_default(health.get('maxHp', health.get('max')), default=0))
    current = max(0, min(maximum, int_or_default(health.get('currentHp', health.get('current')), default=maximum)))
    return current, maximum


def resolve_capability_use(
    *,
    actor: dict[str, Any],
    capability_id: Any,
    target: dict[str, Any] | None = None,
    requested_amount: Any = None,
    turn_economy: dict[str, Any] | None = None,
    in_combat: bool = False,
    roller: Roller | None = None,
) -> dict[str, Any]:
    """Validate and resolve one capability without mutating input state."""

    capability = capability_by_id(actor.get('class'), actor.get('level'), capability_id)
    if not capability:
        return {'ok': False, 'reason': 'That class capability is not available at this level.'}

    state = normalize_class_feature_state(
        actor.get('classFeatureState'),
        class_name=actor.get('class'),
        level=actor.get('level'),
    )
    capability_state = state.get(_text(capability.get('id')), {})
    remaining = max(0, int_or_default(capability_state.get('current'), default=0))
    if remaining <= 0:
        return {'ok': False, 'reason': f"{capability.get('name')} has no uses remaining."}

    economy = turn_economy if isinstance(turn_economy, dict) else {}
    economy_kind = _text(capability.get('actionEconomy')).lower()
    if economy_kind == 'action' and in_combat and int_or_default(economy.get('actionRemaining'), default=0) <= 0:
        return {'ok': False, 'reason': 'The action for this turn is already spent.'}
    if economy_kind == 'bonus_action' and in_combat and int_or_default(economy.get('bonusActionRemaining'), default=0) <= 0:
        return {'ok': False, 'reason': 'The bonus action for this turn is already spent.'}

    target_policy = _text(capability.get('targetPolicy')).lower()
    resolved_target = actor if target_policy == 'self' else target
    if not isinstance(resolved_target, dict):
        return {'ok': False, 'reason': f"{capability.get('name')} requires an exact target."}
    if target_policy == 'self_or_ally' and in_combat:
        actor_team = _text(actor.get('team') or 'player')
        target_team = _text(resolved_target.get('team') or 'player')
        if actor_team and target_team and actor_team != target_team:
            return {'ok': False, 'reason': f"{capability.get('name')} can target only self or an ally."}

    effect = capability.get('effect') if isinstance(capability.get('effect'), dict) else {}
    effect_type = _text(effect.get('type')).lower()
    result: dict[str, Any] = {
        'ok': True,
        'capability': capability,
        'targetId': resolved_target.get('id'),
        'resourceCost': 1,
        'actionEconomy': economy_kind,
    }
    if effect_type == 'heal':
        current_hp, maximum_hp = _health(resolved_target)
        if maximum_hp <= 0 or current_hp <= 0:
            return {'ok': False, 'reason': 'An unconscious or dead target cannot use this recovery feature.'}
        if current_hp >= maximum_hp:
            return {'ok': False, 'reason': 'The target is already at full hit points.'}
        dice = effect.get('dice') if isinstance(effect.get('dice'), dict) else {}
        count = max(1, int_or_default(dice.get('count'), default=1))
        sides = max(2, int_or_default(dice.get('sides'), default=10))
        roll = roller or (lambda die_sides: die_sides)
        rolls = [max(1, min(sides, int_or_default(roll(sides), default=1))) for _ in range(count)]
        level_bonus = _bounded_level(actor.get('level')) if effect.get('levelBonus') else 0
        result.update({'effectType': 'heal', 'amount': sum(rolls) + level_bonus, 'rolls': rolls})
    elif effect_type == 'healing_pool':
        current_hp, maximum_hp = _health(resolved_target)
        if maximum_hp <= 0 or current_hp <= 0:
            return {'ok': False, 'reason': 'Lay on Hands cannot restore a dead target.'}
        missing_hp = max(0, maximum_hp - current_hp)
        requested = max(1, int_or_default(requested_amount, default=1))
        amount = min(requested, remaining, missing_hp)
        if amount <= 0:
            return {'ok': False, 'reason': 'The target has no missing hit points.'}
        result.update({'effectType': 'heal', 'amount': amount, 'resourceCost': amount})
    elif effect_type == 'restore_action':
        if not in_combat:
            return {'ok': False, 'reason': 'Action Surge is available only during active combat.'}
        if int_or_default(economy.get('actionRemaining'), default=0) > 0:
            return {'ok': False, 'reason': 'The action for this turn is still available.'}
        result.update({'effectType': 'restore_action', 'amount': 1})
    else:
        return {'ok': False, 'reason': 'That capability does not yet have an authoritative effect.'}
    return result


def spend_capability(
    raw_state: Any,
    *,
    class_name: Any,
    level: Any,
    capability_id: Any,
    amount: Any,
    turn_id: Any,
) -> dict[str, dict[str, Any]] | None:
    state = normalize_class_feature_state(raw_state, class_name=class_name, level=level)
    requested = _text(capability_id)
    entry = state.get(requested)
    cost = max(1, int_or_default(amount, default=1))
    if not entry or int_or_default(entry.get('current'), default=0) < cost:
        return None
    entry['current'] = int_or_default(entry.get('current'), default=0) - cost
    entry['usedAtTurn'] = max(0, int_or_default(turn_id, default=0))
    return state


def restore_class_capabilities(
    raw_state: Any,
    *,
    class_name: Any,
    level: Any,
    rest_type: Any,
) -> dict[str, dict[str, Any]]:
    state = normalize_class_feature_state(raw_state, class_name=class_name, level=level)
    refreshes = {'short_rest', 'long_rest'} if _text(rest_type).lower() == 'long_rest' else {'short_rest'}
    for entry in state.values():
        if _text(entry.get('refreshesOn')).lower() not in refreshes:
            continue
        entry['current'] = entry['max']
        entry.pop('usedAtTurn', None)
    return state
