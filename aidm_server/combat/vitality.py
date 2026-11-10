"""Going down, death saving throws and recovery for player characters.

Player characters reduced to 0 HP fall ``unconscious`` and ``dying`` (in
combat) or ``stable`` (out of combat) instead of leaving the game; only the
``dead`` condition makes one not alive.  Enemies and NPC allies keep the
simpler rule that 0 HP means defeated.

Every function accepts either a combat participant (``hp``, ``conditions``,
``deathSaves``, ``isAlive``, ``isConscious``) or a state actor (``health`` with
``currentHp``, ``maxHp``, ``tempHp``, ``conditions``, ``deathSaves``) and
mutates it in place.
"""

from __future__ import annotations

from typing import Any, Mapping

from aidm_server.canon_text import int_or_default
from aidm_server.combat.modifiers import condition_key, heals_nothing
from aidm_server.damage_dice import deterministic_roller
from aidm_server.game_state.models import stable_change_id


VITALITY_CONDITIONS = ('unconscious', 'dying', 'stable', 'dead')
DEATH_SAVE_DC = 10
DEATH_SAVE_LIMIT = 3
DEATH_SAVE_SOURCE = 'engine_death_save'


def _is_actor(record: Mapping[str, Any]) -> bool:
    return isinstance(record.get('health'), dict) and not isinstance(record.get('hp'), dict)


def is_player_character(record: Mapping[str, Any] | None) -> bool:
    if not isinstance(record, Mapping):
        return False
    return condition_key(record.get('kind')) == 'player_character' or condition_key(record.get('team')) == 'player'


def _conditions(record: Mapping[str, Any]) -> list[str]:
    source = record['health'] if _is_actor(record) else record
    values = source.get('conditions') if isinstance(source.get('conditions'), list) else []
    return list(dict.fromkeys(condition_key(value) for value in values if condition_key(value)))


def normalize_death_saves(value: Any) -> dict[str, int]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        'successes': max(0, min(DEATH_SAVE_LIMIT, int_or_default(raw.get('successes'), default=0))),
        'failures': max(0, min(DEATH_SAVE_LIMIT, int_or_default(raw.get('failures'), default=0))),
    }


def death_saves(record: Mapping[str, Any]) -> dict[str, int]:
    source = record['health'] if _is_actor(record) else record
    return normalize_death_saves(source.get('deathSaves'))


def hit_points(record: Mapping[str, Any]) -> dict[str, int]:
    if _is_actor(record):
        health = record['health']
        current = max(0, int_or_default(health.get('currentHp'), default=0))
        maximum = max(0, int_or_default(health.get('maxHp'), default=0))
        temp = max(0, int_or_default(health.get('tempHp'), default=0))
    else:
        hp = record.get('hp') if isinstance(record.get('hp'), Mapping) else {}
        current = max(0, int_or_default(hp.get('current', hp.get('currentHp')), default=0))
        maximum = max(current, int_or_default(hp.get('max', hp.get('maxHp')), default=current))
        temp = max(0, int_or_default(hp.get('temp', hp.get('tempHp')), default=0))
    return {'current': current, 'max': maximum, 'temp': temp}


def _write(
    record: dict[str, Any],
    *,
    hp: dict[str, int] | None = None,
    conditions: list[str] | None = None,
    saves: dict[str, int] | None = None,
    wake: bool = False,
) -> None:
    if _is_actor(record):
        health = record['health']
        if hp is not None:
            health['currentHp'] = hp['current']
            health['tempHp'] = hp['temp']
        target = health
    else:
        if hp is not None:
            record['hp'] = {**(record.get('hp') if isinstance(record.get('hp'), dict) else {}), **hp}
        target = record
    if conditions is not None:
        target['conditions'] = list(dict.fromkeys(conditions))
    if saves is not None:
        target['deathSaves'] = dict(saves)
    if not _is_actor(record):
        current_conditions = _conditions(record)
        record['isAlive'] = 'dead' not in current_conditions
        if hit_points(record)['current'] <= 0 or not record['isAlive']:
            record['isConscious'] = False
        elif wake:
            record['isConscious'] = True


def _with(conditions: list[str], *added: str, without: tuple[str, ...] = ()) -> list[str]:
    kept = [condition for condition in conditions if condition not in without and condition not in added]
    return [*kept, *added]


def is_dead(record: Mapping[str, Any]) -> bool:
    return 'dead' in _conditions(record)


def is_dying(record: Mapping[str, Any]) -> bool:
    return 'dying' in _conditions(record) and not is_dead(record)


def is_stable(record: Mapping[str, Any]) -> bool:
    return 'stable' in _conditions(record) and not is_dead(record)


def is_down(record: Mapping[str, Any]) -> bool:
    return is_dead(record) or hit_points(record)['current'] <= 0


def _zero_saves() -> dict[str, int]:
    return {'successes': 0, 'failures': 0}


def _die(record: dict[str, Any], saves: dict[str, int]) -> None:
    _write(
        record,
        conditions=_with(_conditions(record), 'dead', without=('dying', 'stable', 'unconscious')),
        saves=saves,
    )


def take_damage(
    record: dict[str, Any],
    amount: int,
    *,
    critical: bool = False,
    in_combat: bool = True,
) -> dict[str, Any]:
    """Apply final damage (temporary HP first) to a player character and settle its vitality."""

    hp = hit_points(record)
    amount = max(0, int(amount))
    result = {'amount': 0, 'tempHpDamage': 0, 'hpDamage': 0, 'overflow': 0, 'vitality': None}
    if is_dead(record) or amount <= 0:
        return result
    temp_damage = min(hp['temp'], amount)
    remaining = amount - temp_damage
    was_at_zero = hp['current'] <= 0
    hp_damage = min(hp['current'], remaining)
    overflow = remaining - hp_damage
    next_hp = {**hp, 'current': hp['current'] - hp_damage, 'temp': hp['temp'] - temp_damage}
    _write(record, hp=next_hp)
    result.update({'amount': temp_damage + hp_damage, 'tempHpDamage': temp_damage, 'hpDamage': hp_damage, 'overflow': overflow})
    if remaining <= 0 or next_hp['current'] > 0 or hp['max'] <= 0:
        return result

    saves = death_saves(record)
    if hp['max'] > 0 and overflow >= hp['max']:
        _die(record, saves)
        result['vitality'] = 'dead_massive_damage'
        return result
    conditions = _conditions(record)
    if not was_at_zero:
        if in_combat:
            _write(record, conditions=_with(conditions, 'unconscious', 'dying', without=('stable',)), saves=_zero_saves())
            result['vitality'] = 'dying'
        else:
            _write(record, conditions=_with(conditions, 'unconscious', 'stable', without=('dying',)), saves=_zero_saves())
            result['vitality'] = 'stable'
        return result

    saves['failures'] = min(DEATH_SAVE_LIMIT, saves['failures'] + (2 if critical else 1))
    if saves['failures'] >= DEATH_SAVE_LIMIT:
        _die(record, saves)
        result['vitality'] = 'dead'
        return result
    if in_combat:
        conditions = _with(conditions, 'unconscious', 'dying', without=('stable',))
    _write(record, conditions=conditions, saves=saves)
    result['vitality'] = 'dying' if 'dying' in conditions else 'stable'
    return result


def set_hit_points(record: dict[str, Any], current: int, *, in_combat: bool) -> None:
    """Set HP from an explicit state update, then go down or wake up accordingly."""

    hp = hit_points(record)
    current = max(0, min(int(current), hp['max']) if hp['max'] else int(current))
    _write(record, hp={**hp, 'current': current})
    if is_dead(record):
        return
    if current <= 0 < hp['current']:
        added = ('unconscious', 'dying') if in_combat else ('unconscious', 'stable')
        _write(record, conditions=_with(_conditions(record), *added, without=('dying', 'stable')), saves=_zero_saves())
    elif current > 0 >= hp['current']:
        _write(
            record,
            conditions=_with(_conditions(record), without=('dying', 'stable', 'unconscious')),
            saves=_zero_saves(),
            wake=True,
        )


def heal(record: dict[str, Any], amount: int) -> int:
    """Restore HP; a player character at 0 HP that is not dead wakes and stops dying."""

    if is_dead(record) or heals_nothing(record if not _is_actor(record) else {}):
        return 0
    hp = hit_points(record)
    applied = min(max(0, int(amount)), max(0, hp['max'] - hp['current'])) if hp['max'] else max(0, int(amount))
    if applied <= 0:
        return 0
    _write(record, hp={**hp, 'current': hp['current'] + applied})
    if hp['current'] <= 0:
        _write(
            record,
            conditions=_with(_conditions(record), without=('dying', 'stable', 'unconscious')),
            saves=_zero_saves(),
            wake=True,
        )
    return applied


def death_save_roll(turn_id: Any, participant_id: Any, round_number: Any) -> int:
    """The engine's deterministic d20 for one participant's death save in one round."""

    return deterministic_roller(f'death_save:{turn_id}:{participant_id}:{round_number}')(20)


def death_save_change_id(turn_id: Any, participant_id: Any, round_number: Any) -> str:
    return stable_change_id('combat.death_save', turn_id, participant_id, round_number)


def apply_death_save(record: dict[str, Any], roll: int) -> dict[str, Any]:
    """Record one d20 death saving throw for a dying player character."""

    natural = max(1, min(20, int(roll)))
    saves = death_saves(record)
    if not is_dying(record):
        return {'applied': False, 'roll': natural, **saves, 'outcome': None, 'result': None}
    if natural == 20:
        hp = hit_points(record)
        _write(
            record,
            hp={**hp, 'current': max(1, hp['current'])},
            conditions=_with(_conditions(record), without=('dying', 'stable', 'unconscious')),
            saves=_zero_saves(),
            wake=True,
        )
        return {'applied': True, 'roll': natural, 'result': 'critical_success', **saves, 'outcome': 'revived'}
    if natural == 1:
        result = 'critical_failure'
        saves['failures'] = min(DEATH_SAVE_LIMIT, saves['failures'] + 2)
    elif natural >= DEATH_SAVE_DC:
        result = 'success'
        saves['successes'] = min(DEATH_SAVE_LIMIT, saves['successes'] + 1)
    else:
        result = 'failure'
        saves['failures'] = min(DEATH_SAVE_LIMIT, saves['failures'] + 1)
    if saves['failures'] >= DEATH_SAVE_LIMIT:
        _die(record, saves)
        outcome = 'dead'
    elif saves['successes'] >= DEATH_SAVE_LIMIT:
        stabilize(record)
        outcome = 'stable'
    else:
        _write(record, saves=saves)
        outcome = 'dying'
    return {'applied': True, 'roll': natural, 'result': result, **saves, 'outcome': outcome}


def stabilize(record: dict[str, Any]) -> bool:
    """A dying character becomes stable: still unconscious at 0 HP, no more death saves."""

    if not is_dying(record):
        return False
    _write(
        record,
        conditions=_with(_conditions(record), 'unconscious', 'stable', without=('dying',)),
        saves=_zero_saves(),
    )
    return True


def revive(record: dict[str, Any], hp: int = 1) -> None:
    """Return a dead (or downed) character to life with ``hp`` hit points."""

    current = hit_points(record)
    restored = max(1, min(int(hp), current['max'] or int(hp)))
    _write(
        record,
        hp={**current, 'current': max(current['current'], restored)},
        conditions=_with(_conditions(record), without=VITALITY_CONDITIONS),
        saves=_zero_saves(),
        wake=True,
    )


def recover_on_rest(record: dict[str, Any], rest_type: str) -> int:
    """A rest wakes a stable character: a short rest to 1 HP; returns HP regained here."""

    conditions = _conditions(record)
    if 'stable' not in conditions or is_dead(record):
        return 0
    hp = hit_points(record)
    regained = 1 if rest_type == 'short_rest' and hp['current'] <= 0 else 0
    _write(
        record,
        hp={**hp, 'current': hp['current'] + regained},
        conditions=_with(conditions, without=('stable', 'unconscious', 'dying')),
        saves=_zero_saves(),
        wake=True,
    )
    return regained


def vitality_snapshot(record: Mapping[str, Any]) -> dict[str, Any]:
    """The vitality fields a player-character record round-trips through storage."""

    return {
        'conditions': [condition for condition in _conditions(record) if condition in VITALITY_CONDITIONS],
        'deathSaves': death_saves(record),
    }
