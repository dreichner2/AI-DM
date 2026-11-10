"""Apply engine-produced combat changes to a working copy of the combat.

Resolution runs several rolls in sequence (an enemy block, a chain of
reactions) before any change is validated and persisted.  Later rolls must see
what earlier ones did — a spent reaction, a Shield bonus, a move, a fall to
0 HP — so resolvers apply their own changes to a scratch copy with these
helpers.  The persisting applier uses the same participant-level helpers.
"""

from __future__ import annotations

from typing import Any, Mapping

from aidm_server.canon_text import int_or_default
from aidm_server.combat import vitality
from aidm_server.combat.modifiers import condition_key, consume_modifiers, normalize_active_effect
from aidm_server.combat.positioning import move_participant, participant_by_id


def _text(value: Any) -> str:
    return str(value or '').strip()


def spend_reaction(combat: Mapping[str, Any], participant: dict[str, Any]) -> None:
    participant['reactionRemaining'] = 0
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    economy = flags.get('turnEconomy') if isinstance(flags.get('turnEconomy'), dict) else None
    if economy is not None and _text(economy.get('actorId')) == _text(participant.get('id')):
        economy['reactionRemaining'] = 0


def add_active_effect(participant: dict[str, Any], raw_effect: Any) -> dict[str, Any] | None:
    """Add (or replace by id) one active effect, maintaining its condition."""

    effect = normalize_active_effect(raw_effect)
    if effect is None:
        return None
    effects = [
        existing
        for existing in participant.get('activeEffects') or []
        if isinstance(existing, dict) and _text(existing.get('id')) != effect['id']
    ]
    participant['activeEffects'] = [*effects, effect]
    if effect.get('condition'):
        conditions = [condition_key(value) for value in participant.get('conditions') or [] if _text(value)]
        if effect['condition'] not in conditions:
            participant['conditions'] = [*conditions, effect['condition']]
    return effect


def _set_condition(participant: dict[str, Any], condition: str, *, add: bool) -> None:
    conditions = [condition_key(value) for value in participant.get('conditions') or [] if _text(value)]
    if add and condition not in conditions:
        conditions.append(condition)
    if not add:
        conditions = [value for value in conditions if value != condition]
    participant['conditions'] = conditions
    if condition == 'hidden' and isinstance(participant.get('position'), dict):
        participant['position']['isHidden'] = add


def _damage(participant: dict[str, Any], amount: int, *, critical: bool) -> None:
    if vitality.is_player_character(participant):
        vitality.take_damage(participant, amount, critical=critical, in_combat=True)
        return
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    temp = max(0, int_or_default(hp.get('temp'), default=0))
    absorbed = min(temp, amount)
    current = max(0, int_or_default(hp.get('current'), default=0) - (amount - absorbed))
    participant['hp'] = {**hp, 'current': current, 'temp': temp - absorbed}
    participant['isAlive'] = current > 0
    participant['isConscious'] = current > 0


def _set_hit_points(participant: dict[str, Any], hp: Mapping[str, Any]) -> None:
    current = max(0, int_or_default(hp.get('current'), default=0))
    if vitality.is_player_character(participant):
        vitality.set_hit_points(participant, current, in_combat=True)
        return
    participant['hp'] = {**(participant.get('hp') if isinstance(participant.get('hp'), dict) else {}), **dict(hp)}
    participant['isAlive'] = current > 0
    participant['isConscious'] = current > 0


def apply_working_changes(combat: dict[str, Any], changes: list[Mapping[str, Any]]) -> None:
    """Apply the combat side of engine changes to ``combat`` in place."""

    for change in changes:
        change_type = _text(change.get('type'))
        participant = participant_by_id(combat, change.get('participantId') or change.get('actorId'))
        if change_type == 'combat.move':
            move_participant(combat, change.get('participantId'), change.get('toRangeBand'), engage_with=change.get('engageWith') or [])
        elif change_type == 'combat.effect.consume':
            consume_modifiers(combat, change.get('consume') or [])
        elif participant is None:
            continue
        elif change_type == 'combat.reaction.use':
            spend_reaction(combat, participant)
        elif change_type == 'combat.effect.add':
            add_active_effect(participant, change.get('effect'))
        elif change_type in {'combat.condition.add', 'combat.condition.remove'}:
            _set_condition(participant, condition_key(change.get('condition')), add=change_type.endswith('.add'))
        elif change_type == 'combat.stabilize':
            vitality.stabilize(participant)
        elif change_type == 'health.damage':
            _damage(participant, max(0, int_or_default(change.get('amount'), default=0)), critical=change.get('critical') is True)
        elif change_type == 'combat.participant.update' and isinstance(change.get('hp'), Mapping):
            _set_hit_points(participant, change['hp'])
