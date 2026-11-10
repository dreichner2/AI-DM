"""Range bands, melee engagement and reach, shared by the player HUD and the enemy engine.

Every participant stands in one band: ``melee``, ``near``, ``far`` or ``distant``.
The melee band is the press of close combat.  A participant in it is *engaged*
with the enemies it closed on or that closed on it (``position.engagedWith``,
always kept symmetric).  Closing on a target puts both participants in the melee
band and engages them; any move out of melee ends every engagement of the mover.
A target can be closed on from ``near`` or from inside melee when it stands in
``melee`` or ``near``.

Reach, the same for player weapons and enemy abilities:

- melee: the target is engaged with the attacker or both stand in melee; weapons
  tagged ``reach`` may instead target the ``melee`` and ``near`` bands;
- ranged: any band up to the weapon's or ability's range (weapons reach all four);
- a target that is down, hidden or behind full cover cannot be targeted at all.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from aidm_server.combat.modifiers import (
    attack_block_reason,
    condition_key,
    engaged_with,
    is_hidden,
    range_band,
)
from aidm_server.combat.state import participant_is_targetable


RANGE_BANDS = ('melee', 'near', 'far', 'distant')
CLOSABLE_BANDS = frozenset({'melee', 'near'})
REACH_WEAPON_BANDS = frozenset({'melee', 'near'})
MELEE_ABILITY_RANGES = frozenset({'melee', 'touch'})


def _text(value: Any) -> str:
    return str(value or '').strip()


def _id(participant: Mapping[str, Any] | None) -> str:
    return _text(participant.get('id')) if isinstance(participant, Mapping) else ''


def _position(participant: Mapping[str, Any] | None) -> Mapping[str, Any]:
    value = participant.get('position') if isinstance(participant, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def band_index(band: Any) -> int:
    key = condition_key(band)
    return RANGE_BANDS.index(key) if key in RANGE_BANDS else 1


def band_inward(band: Any) -> str:
    return RANGE_BANDS[max(0, band_index(band) - 1)]


def band_outward(band: Any) -> str:
    return RANGE_BANDS[min(len(RANGE_BANDS) - 1, band_index(band) + 1)]


def bands_up_to(band: Any) -> frozenset[str]:
    return frozenset(RANGE_BANDS[: band_index(band) + 1])


def adjacent_or_same_band(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    return abs(band_index(range_band(first)) - band_index(range_band(second))) <= 1


def participant_by_id(combat: Mapping[str, Any] | None, participant_id: Any) -> dict[str, Any] | None:
    wanted = _text(participant_id)
    if not wanted or not isinstance(combat, Mapping):
        return None
    for participant in combat.get('participants') or []:
        if isinstance(participant, dict) and _id(participant) == wanted:
            return participant
    return None


def are_engaged(first: Mapping[str, Any] | None, second: Mapping[str, Any] | None) -> bool:
    first_id, second_id = _id(first), _id(second)
    return bool(first_id and second_id) and (second_id in engaged_with(first) or first_id in engaged_with(second))


def is_hostile(first: Mapping[str, Any] | None, second: Mapping[str, Any] | None) -> bool:
    sides = {condition_key((record or {}).get('team')) == 'enemy' for record in (first, second)}
    return len(sides) == 2


def in_melee_reach(attacker: Mapping[str, Any], target: Mapping[str, Any], *, reach: bool = False) -> bool:
    if are_engaged(attacker, target):
        return True
    if range_band(attacker) == 'melee' and range_band(target) == 'melee':
        return True
    return reach and range_band(target) in REACH_WEAPON_BANDS


def full_cover_ids(combat: Mapping[str, Any] | None) -> frozenset[str]:
    battlefield = combat.get('battlefield') if isinstance(combat, Mapping) and isinstance(combat.get('battlefield'), Mapping) else {}
    return frozenset(
        _text(entry.get('id'))
        for entry in battlefield.get('cover') or []
        if isinstance(entry, Mapping)
        and condition_key(entry.get('coverType', entry.get('cover_type'))) == 'full'
        and _text(entry.get('id'))
    )


def has_cover(combat: Mapping[str, Any] | None, participant: Mapping[str, Any] | None) -> bool:
    cover_id = _text(_position(participant).get('coverId') or _position(participant).get('cover_id'))
    battlefield = combat.get('battlefield') if isinstance(combat, Mapping) and isinstance(combat.get('battlefield'), Mapping) else {}
    return bool(cover_id) and any(
        isinstance(entry, Mapping) and _text(entry.get('id')) == cover_id for entry in battlefield.get('cover') or []
    )


def _zone(participant: Mapping[str, Any]) -> str:
    return _text(_position(participant).get('zoneId') or _position(participant).get('zone_id'))


def separated_by_zone(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    return bool(_zone(first) and _zone(second) and _zone(first) != _zone(second))


def targeting_block_reason(attacker: Mapping[str, Any], target: Mapping[str, Any], combat: Mapping[str, Any] | None) -> str:
    """Why a hostile attack or ability may not pick this target at all, whatever the range."""

    if not participant_is_targetable(dict(target)):
        return 'Target is already down.'
    if is_hidden(target):
        return 'Target is hidden.'
    if _text(_position(target).get('coverId') or _position(target).get('cover_id')) in full_cover_ids(combat):
        return 'Target has full cover.'
    return attack_block_reason(attacker, target)


def reach_block_reason(
    attacker: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    melee: bool,
    allowed_bands: Iterable[str] = RANGE_BANDS,
    reach: bool = False,
) -> str:
    """Why an attack cannot reach this target from where both stand now ('' when it can)."""

    band = range_band(target)
    if not melee:
        return '' if band in set(allowed_bands) else f'Target is at {band} range.'
    in_reach = in_melee_reach(attacker, target, reach=reach)
    if not in_reach and band != 'melee':
        return f'Target is at {band} range.'
    if separated_by_zone(attacker, target):
        return 'Target is in another battlefield zone.'
    return '' if in_reach else 'Advance into melee first.'


def close_block_reason(mover: Mapping[str, Any], target: Mapping[str, Any], combat: Mapping[str, Any] | None) -> str:
    """Why the mover cannot close on this enemy with one movement ('' when it can)."""

    blocked = targeting_block_reason(mover, target, combat)
    if blocked:
        return blocked
    if separated_by_zone(mover, target):
        return 'Target is in another battlefield zone.'
    if in_melee_reach(mover, target):
        return 'Already within reach.'
    if range_band(mover) not in CLOSABLE_BANDS:
        return 'Too far away to close in one move.'
    if range_band(target) not in CLOSABLE_BANDS:
        return f'Target is at {range_band(target)} range.'
    return ''


def _set_engaged(participant: dict[str, Any], ids: list[str]) -> None:
    position = dict(_position(participant))
    if ids:
        position['engagedWith'] = list(dict.fromkeys(ids))
    else:
        position.pop('engagedWith', None)
    participant['position'] = position


def release_engagements(combat: Mapping[str, Any], participant_id: Any) -> list[str]:
    """End every engagement of one participant, on both sides; returns who it was engaged with."""

    mover_id = _text(participant_id)
    released: list[str] = []
    for participant in combat.get('participants') or []:
        if not isinstance(participant, dict):
            continue
        if _id(participant) == mover_id:
            released.extend(engaged_with(participant))
            _set_engaged(participant, [])
        elif mover_id in engaged_with(participant):
            released.append(_id(participant))
            _set_engaged(participant, [value for value in engaged_with(participant) if value != mover_id])
    return list(dict.fromkeys(released))


def move_participant(
    combat: Mapping[str, Any],
    participant_id: Any,
    to_band: Any,
    *,
    engage_with: Iterable[Any] = (),
) -> dict[str, Any] | None:
    """Move one participant to a band and engage it with the given targets.

    Leaving melee ends the mover's engagements; each target it engages is pulled
    into melee with it.
    """

    mover = participant_by_id(combat, participant_id)
    destination = condition_key(to_band)
    if mover is None or destination not in RANGE_BANDS:
        return None
    if destination != 'melee':
        release_engagements(combat, participant_id)
    mover['position'] = {**dict(_position(mover)), 'rangeBand': destination}
    for target_id in engage_with:
        target = participant_by_id(combat, target_id)
        if target is None or target is mover or destination != 'melee':
            continue
        target['position'] = {**dict(_position(target)), 'rangeBand': 'melee'}
        _set_engaged(target, [*engaged_with(target), _id(mover)])
        _set_engaged(mover, [*engaged_with(mover), _id(target)])
    return mover


def ability_reach(ability: Mapping[str, Any] | None, *, ranged_by_text: bool) -> tuple[bool, frozenset[str]]:
    """``(melee, allowed_bands)`` for an enemy ability from its declared range.

    Attacks without a range are melee unless their text names a ranged weapon;
    other abilities without a range reach every band.
    """

    declared = condition_key((ability or {}).get('range'))
    if declared in MELEE_ABILITY_RANGES:
        return True, frozenset({'melee'})
    if declared in RANGE_BANDS:
        return False, bands_up_to(declared)
    is_attack = condition_key((ability or {}).get('type')) == 'attack' or (ability or {}).get('attackBonus') is not None
    if is_attack and not ranged_by_text and not declared:
        return True, frozenset({'melee'})
    return False, frozenset(RANGE_BANDS)


def enemy_approach(
    enemy: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    melee: bool,
    allowed_bands: Iterable[str],
    combat: Mapping[str, Any] | None,
    can_move: bool,
) -> dict[str, Any]:
    """How an enemy reaches a target this turn.

    Returns ``{"reachable": bool, "steps": [{"toRangeBand", "engageWith"}...],
    "attack": bool, "reason": str}``.  A melee enemy moves inward one band and
    attacks if that brings the target into reach, or spends its action on a
    second move otherwise; a ranged enemy never moves.
    """

    blocked = targeting_block_reason(enemy, target, combat)
    if blocked:
        return {'reachable': False, 'steps': [], 'attack': False, 'reason': blocked}
    if not melee:
        reason = reach_block_reason(enemy, target, melee=False, allowed_bands=allowed_bands)
        return {'reachable': not reason, 'steps': [], 'attack': not reason, 'reason': reason}
    if separated_by_zone(enemy, target):
        return {'reachable': False, 'steps': [], 'attack': False, 'reason': 'Target is in another battlefield zone.'}
    if in_melee_reach(enemy, target):
        return {'reachable': True, 'steps': [], 'attack': True, 'reason': ''}
    if range_band(target) not in CLOSABLE_BANDS:
        return {'reachable': False, 'steps': [], 'attack': False, 'reason': f'Target is at {range_band(target)} range.'}
    if not can_move:
        return {'reachable': True, 'steps': [], 'attack': False, 'reason': 'Cannot move into reach this turn.'}
    steps: list[dict[str, Any]] = []
    band = range_band(enemy)
    while band not in CLOSABLE_BANDS and len(steps) < 2:
        band = band_inward(band)
        steps.append({'toRangeBand': band, 'engageWith': []})
    if band in CLOSABLE_BANDS and len(steps) < 2:
        steps.append({'toRangeBand': 'melee', 'engageWith': [_id(target)]})
    return {'reachable': True, 'steps': steps, 'attack': len(steps) == 1 and bool(steps[0]['engageWith']), 'reason': ''}
