"""A party that is beaten wakes later instead of dying, whether or not the fight is lethal.

When a combat ends with every present player character down (``objective_failed``),
the clock advances one phase, every hero who is not dead wakes with 1 HP (no
longer dying, stable, unconscious or prone; death saves reset), the enemies
leave the scene, and the pack encounter is re-armed so its trigger can fire
again. The encounter's ``defeat`` outcome is recorded (and pays nothing) by the
ordinary combat-end reward transaction, which does not use up the encounter's
once-only success rewards. ``lethalDefeat`` on the combat or its encounter
changes none of this: it only lets the defeat fail the checkpoint that names
the encounter, so the story takes that checkpoint's failure route
(``campaign_pack_progress``).

Outside a fight the same waking applies at once, whatever put the heroes down:
when no combat is active and every present hero is down, each one who is not
dead comes to with 1 HP a phase later (:func:`party_wake`).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.combat.state import participant_is_present
from aidm_server.combat.vitality import hit_points, is_dead, is_down, is_player_character, revive
from aidm_server.game_clock import advance_state_clock
from aidm_server.game_state.campaign_pack_encounters import TRIGGERED_ENCOUNTERS_FLAG, catalog_record
from aidm_server.game_state.models import append_change_ledger, stable_change_id

DEFEAT_RECOVERY_CHANGE = 'combat.defeat_recovery'
DEFEAT_RECOVERY_SOURCE = 'engine_defeat_recovery'
DEFEAT_END_REASON = 'objective_failed'
PARTY_WAKE_CHANGE = 'party.wake'
ACTIVE_COMBAT_STATUSES = frozenset({'starting', 'active'})


def _text(value: Any) -> str:
    return str(value or '').strip()


def _without_prone(record: dict[str, Any]) -> None:
    holder = record['health'] if isinstance(record.get('health'), dict) and not isinstance(record.get('hp'), dict) else record
    holder['conditions'] = [condition for condition in holder.get('conditions') or [] if _text(condition).lower() != 'prone']


def _present_heroes(combat: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        participant
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and is_player_character(participant) and participant_is_present(participant)
    ]


def party_is_beaten(state: dict[str, Any]) -> bool:
    """The combat has ended with every present hero down and the party has not woken yet."""

    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    heroes = _present_heroes(combat)
    return (
        _text(combat.get('status')).lower() in {'ended', 'resolved'}
        and _text(flags.get('endReason')).lower() == DEFEAT_END_REASON
        and not isinstance(flags.get('defeatRecovery'), dict)
        and bool(heroes)
        and all(is_down(hero) for hero in heroes)
    )


def enemies_phrase(state: dict[str, Any]) -> tuple[str, bool]:
    """``("the wardbound beast and the pale lantern", True)``: the fight's enemies as prose, and whether they are several."""

    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    groups: dict[str, list[dict[str, Any]]] = {}
    for participant in combat.get('participants') or []:
        if isinstance(participant, dict) and participant.get('team') == 'enemy':
            groups.setdefault(_text(participant.get('campaignPackEnemyId')) or _text(participant.get('name')), []).append(participant)
    phrases = []
    for key, members in groups.items():
        record = catalog_record(state, 'enemies', key) if _text(members[0].get('campaignPackEnemyId')) else {}
        many = len(members) > 1
        name = (
            _text(record.get('name')) if many and record else _text(record.get('singularName')) or _text(members[0].get('name'))
        ) or _text(members[0].get('name'))
        phrases.append(_named(name, proper=members[0].get('proper') is True or record.get('proper') is True))
    plural = len(phrases) > 1 or any(len(members) > 1 for members in groups.values())
    if len(phrases) <= 1:
        return ''.join(phrases) or 'the enemies', plural or not phrases
    return f"{', '.join(phrases[:-1])} and {phrases[-1]}", plural


def _named(name: str, *, proper: bool) -> str:
    if name.lower().startswith('the '):
        return f'the {name[4:]}'
    return name if proper else f'the {name.lower()}'


def party_defeat_recovery(state: dict[str, Any], *, turn_id: Any) -> dict[str, Any] | None:
    """Wake a beaten party in ``state`` (mutated) and return the applied change, or None."""

    if not party_is_beaten(state):
        return None
    combat = state['combat']
    flags = combat.setdefault('flags', {})
    encounter_id = _text(flags.get('campaignPackEncounterId'))
    departed, several = enemies_phrase(state)
    woken: list[str] = []
    for hero in _present_heroes(combat):
        actor = next(
            (
                candidate
                for candidate in state.get('playerCharacters') or []
                if isinstance(candidate, dict) and _text(candidate.get('id')) == _text(hero.get('id'))
            ),
            None,
        )
        if is_dead(hero) or (actor is not None and is_dead(actor)):
            continue
        for record in (hero, actor):
            if record is not None:
                revive(record, 1)
                _without_prone(record)
        woken.append(_text(hero.get('id')))
    enemy_ids = {
        _text(participant.get('id'))
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and participant.get('team') == 'enemy'
    }
    combat['participants'] = [
        participant for participant in combat.get('participants') or [] if _text((participant or {}).get('id')) not in enemy_ids
    ]
    combat['initiative'] = [
        entry for entry in combat.get('initiative') or [] if _text((entry or {}).get('participantId')) not in enemy_ids
    ]
    clock = deepcopy(advance_state_clock(state, 1))
    state_flags = state.setdefault('flags', {})
    triggered = state_flags.get(TRIGGERED_ENCOUNTERS_FLAG)
    if encounter_id and isinstance(triggered, list):
        state_flags[TRIGGERED_ENCOUNTERS_FLAG] = [value for value in triggered if _text(value) != encounter_id]
    flags['defeatRecovery'] = {'turnId': turn_id, 'wokenActorIds': woken}
    change = {
        'id': stable_change_id(turn_id, DEFEAT_RECOVERY_CHANGE, encounter_id or 'combat'),
        'turnId': turn_id,
        'type': DEFEAT_RECOVERY_CHANGE,
        'source': DEFEAT_RECOVERY_SOURCE,
        'encounterId': encounter_id or None,
        'wokenActorIds': woken,
        'departedEnemies': departed,
        'departedEnemiesPlural': several,
        'clock': clock,
        'reason': f"The party was beaten and wakes later with 1 HP; {departed} {'are' if several else 'is'} gone.",
        'visible': True,
    }
    append_change_ledger(state, change)
    return change


def _present_actors(state: dict[str, Any]) -> list[dict[str, Any]]:
    active_ids = {value for value in state.get('activePlayerIds') or [] if isinstance(value, int) and not isinstance(value, bool)}
    return [
        actor
        for actor in state.get('playerCharacters') or []
        if isinstance(actor, dict) and (not active_ids or actor.get('playerId') in active_ids)
    ]


def party_wake(state: dict[str, Any], *, turn_id: Any) -> dict[str, Any] | None:
    """Out of a fight, wake a party whose every present hero is down (``state`` is mutated); None otherwise."""

    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    heroes = _present_actors(state)
    if (
        _text(combat.get('status')).lower() in ACTIVE_COMBAT_STATUSES
        or not heroes
        # A hero without hit points on record is not known to be down.
        or not all(is_down(hero) and hit_points(hero)['max'] > 0 for hero in heroes)
        or all(is_dead(hero) for hero in heroes)
    ):
        return None
    woken: list[str] = []
    for hero in heroes:
        if is_dead(hero):
            continue
        participant = next(
            (
                candidate
                for candidate in combat.get('participants') or []
                if isinstance(candidate, dict) and _text(candidate.get('id')) == _text(hero.get('id'))
            ),
            None,
        )
        for record in (hero, participant):
            if record is not None:
                revive(record, 1)
                _without_prone(record)
        woken.append(_text(hero.get('id')))
    clock = deepcopy(advance_state_clock(state, 1))
    change = {
        'id': stable_change_id(turn_id, PARTY_WAKE_CHANGE, *woken),
        'turnId': turn_id,
        'type': PARTY_WAKE_CHANGE,
        'source': DEFEAT_RECOVERY_SOURCE,
        'wokenActorIds': woken,
        'clock': clock,
        'reason': 'Every hero was down outside a fight; they come to later with 1 HP.',
        'visible': True,
    }
    append_change_ledger(state, change)
    return change
