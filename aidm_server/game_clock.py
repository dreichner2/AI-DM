"""The party's day clock, hit dice, and when a rest is allowed.

``snapshot.clock = {"day", "phase", "shortRestsSinceLong", "lastLongRestDay"?}``
(default day 1, morning).  A short rest and structured travel advance one
phase; a long rest advances to the next morning.  At most two short rests count
between long rests and one long rest is allowed per day.

Hit dice live on the actor as ``hitDice = {"current", "max", "die"}`` (max = level,
die from class) and persist in ``Player.stats['hit_dice']`` as the current count.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from aidm_server.canon_text import int_or_default
from aidm_server.character_progression import hit_die_for_class
from aidm_server.combat.state import normalize_combat_state


CLOCK_PHASES = ('morning', 'afternoon', 'evening', 'night')
DEFAULT_CLOCK = {'day': 1, 'phase': 'morning', 'shortRestsSinceLong': 0}
MAX_SHORT_RESTS_BETWEEN_LONG = 2
REST_TYPES = ('short_rest', 'long_rest')
REST_POLICIES = ('safe', 'risky', 'none')
RISKY_DANGER_LEVEL = 3
NO_REST_DANGER_LEVEL = 6

COMBAT_REST_REASON = 'Rest is unavailable while combat is active.'
DOWNED_REST_REASON = 'A dead or dying character cannot complete a rest.'
NO_REST_REASON = 'It is too dangerous to rest here.'
RISKY_LONG_REST_REASON = "This place is too dangerous for a full night's rest."
SHORT_REST_LIMIT_REASON = 'You need a long rest before another short rest helps.'
LONG_REST_LIMIT_REASON = 'You have already rested today.'


def _text(value: Any) -> str:
    return str(value or '').strip()


def normalize_clock(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    phase = _text(raw.get('phase')).lower()
    clock: dict[str, Any] = {
        'day': max(1, int_or_default(raw.get('day'), default=1)),
        'phase': phase if phase in CLOCK_PHASES else DEFAULT_CLOCK['phase'],
        'shortRestsSinceLong': max(0, int_or_default(raw.get('shortRestsSinceLong'), default=0)),
    }
    if raw.get('lastLongRestDay') is not None:
        clock['lastLongRestDay'] = max(1, int_or_default(raw.get('lastLongRestDay'), default=1))
    return clock


def state_clock(state: Mapping[str, Any]) -> dict[str, Any]:
    return normalize_clock(state.get('clock') if isinstance(state, Mapping) else None)


def clock_label(clock: Any) -> str:
    normalized = normalize_clock(clock)
    return f"Day {normalized['day']} · {normalized['phase'].capitalize()}"


def public_clock(clock: Any) -> dict[str, Any]:
    """The clock as players and the DM see it."""

    return {**normalize_clock(clock), 'label': clock_label(clock)}


def advance_phases(clock: Any, phases: int = 1) -> dict[str, Any]:
    normalized = normalize_clock(clock)
    index = CLOCK_PHASES.index(normalized['phase']) + max(0, int(phases))
    normalized['day'] += index // len(CLOCK_PHASES)
    normalized['phase'] = CLOCK_PHASES[index % len(CLOCK_PHASES)]
    return normalized


def advance_state_clock(state: dict[str, Any], phases: int = 1) -> dict[str, Any]:
    state['clock'] = advance_phases(state.get('clock'), phases)
    return state['clock']


def record_rest(state: dict[str, Any], rest_type: str) -> dict[str, Any]:
    """Advance the clock for a completed rest and update the rest counters."""

    if rest_type == 'long_rest':
        clock = normalize_clock(state.get('clock'))
        clock.update({'day': clock['day'] + 1, 'phase': 'morning', 'shortRestsSinceLong': 0})
        clock['lastLongRestDay'] = clock['day']
    else:
        clock = advance_phases(state.get('clock'), 1)
        clock['shortRestsSinceLong'] += 1
    state['clock'] = clock
    return clock


def scene_rest_policy(scene: Any) -> str:
    """The authored ``restPolicy`` of the current scene, else one derived from its danger level."""

    record = scene if isinstance(scene, Mapping) else {}
    authored = _text(record.get('restPolicy')).lower()
    if authored in REST_POLICIES:
        return authored
    danger = int_or_default(record.get('dangerLevel'), default=0)
    if danger >= NO_REST_DANGER_LEVEL:
        return 'none'
    if danger >= RISKY_DANGER_LEVEL:
        return 'risky'
    return 'safe'


def _combat_blocks_rest(state: Mapping[str, Any]) -> bool:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    combat = normalize_combat_state(state.get('combat'), scene)
    return (
        _text(combat.get('status')).lower() in {'starting', 'active'}
        or _text(scene.get('combatState')).lower() in {'pending', 'active'}
    )


def _actor_is_downed(actor: Mapping[str, Any]) -> bool:
    health = actor.get('health') if isinstance(actor.get('health'), Mapping) else {}
    conditions = {_text(value).lower() for value in health.get('conditions') or []}
    # A stable character at 0 HP may rest; the dying and the dead may not.
    return bool(
        conditions.intersection({'dead', 'dying'})
        or (int_or_default(health.get('currentHp'), default=1) <= 0 and 'stable' not in conditions)
    )


def rest_block_reason(state: Mapping[str, Any], actor: Mapping[str, Any], rest_type: str) -> str | None:
    """Why this actor cannot complete this rest now, or ``None`` when it may."""

    if _combat_blocks_rest(state):
        return COMBAT_REST_REASON
    if _actor_is_downed(actor):
        return DOWNED_REST_REASON
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), Mapping) else {}
    policy = scene_rest_policy(scene)
    if policy == 'none':
        return NO_REST_REASON
    clock = state_clock(state)
    if rest_type == 'long_rest':
        if policy == 'risky':
            return RISKY_LONG_REST_REASON
        if clock.get('lastLongRestDay') is not None and clock['day'] <= clock['lastLongRestDay']:
            return LONG_REST_LIMIT_REASON
    elif clock['shortRestsSinceLong'] >= MAX_SHORT_RESTS_BETWEEN_LONG:
        return SHORT_REST_LIMIT_REASON
    return None


def hit_dice_for(
    *,
    class_name: Any,
    level: Any,
    stored: Any = None,
) -> dict[str, Any]:
    maximum = max(1, int_or_default(level, default=1))
    current = maximum if stored is None else max(0, min(maximum, int_or_default(stored, default=maximum)))
    return {'current': current, 'max': maximum, 'die': f'd{hit_die_for_class(class_name)}'}


def actor_hit_dice(actor: Mapping[str, Any]) -> dict[str, Any]:
    stored = actor.get('hitDice') if isinstance(actor.get('hitDice'), Mapping) else {}
    return hit_dice_for(class_name=actor.get('class'), level=actor.get('level'), stored=stored.get('current'))


def _constitution_modifier(actor: Mapping[str, Any]) -> int:
    stats = actor.get('stats') if isinstance(actor.get('stats'), Mapping) else {}
    score = int_or_default(stats.get('constitution', stats.get('con')), default=10)
    return (score - 10) // 2


def resolve_short_rest_hit_dice(
    actor: Mapping[str, Any],
    requested: int | None,
    roller: Callable[[int], int],
) -> dict[str, Any]:
    """Spend hit dice on a short rest: each heals one die + CON modifier (minimum 1).

    ``requested`` ``None`` spends dice until hit points are full or none remain.
    """

    dice = actor_hit_dice(actor)
    health = actor.get('health') if isinstance(actor.get('health'), Mapping) else {}
    maximum = max(0, int_or_default(health.get('maxHp'), default=0))
    # A stable character wakes at 1 HP before spending dice.
    current = max(1, int_or_default(health.get('currentHp'), default=0)) if maximum else 0
    sides = int(dice['die'][1:])
    modifier = _constitution_modifier(actor)
    budget = dice['current'] if requested is None else min(dice['current'], max(0, int(requested)))
    rolls: list[int] = []
    healed = 0
    while len(rolls) < budget and (requested is not None or current + healed < maximum):
        roll = max(1, min(sides, int(roller(sides))))
        rolls.append(roll)
        healed += max(1, roll + modifier)
    return {
        'die': dice['die'],
        'requested': requested,
        'spent': len(rolls),
        'rolls': rolls,
        'conModifier': modifier,
        'healing': healed,
        'remaining': dice['current'] - len(rolls),
    }


def long_rest_hit_dice_regained(actor: Mapping[str, Any]) -> int:
    dice = actor_hit_dice(actor)
    return min(dice['max'] - dice['current'], max(1, dice['max'] // 2))
