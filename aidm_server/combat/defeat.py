"""Heroes are beaten, never killed: whoever is down wakes once the fight is over.

When a combat ends with every present player character down (``objective_failed``),
the clock advances one phase, every hero wakes with 1 HP (no longer dying,
stable, out of the fight, unconscious or prone; death saves reset), the enemies
leave the scene, and the pack encounter is re-armed so its trigger can fire
again. An encounter's ``onDefeat`` (W9-SCENE) replaces the re-arm: the party wakes
in its ``wakeZoneId``, its ``text`` is told, its ``setsFlags`` and ``stakes`` apply,
and the encounter is complete and does not happen again. The encounter's ``defeat`` outcome is recorded (and pays nothing) by the
ordinary combat-end reward transaction, which does not use up the encounter's
once-only success rewards. ``lethalDefeat`` on the combat or its encounter
changes none of this: it only lets the defeat fail the checkpoint that names
the encounter, so the story takes that checkpoint's failure route
(``campaign_pack_progress``).

Outside a fight (:func:`party_wake`): when a fight has ended, for any reason,
with at least one hero standing, every hero who is down (dying, stable or out
of the fight) comes round with 1 HP at once, with no time passing; this is
settled once per fight end. A hero still out of the fight with no fight on (an
old save) comes round the same way. When every present hero is down, whatever
put them down, each one comes to with 1 HP a phase later.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.combat.state import participant_is_present
from aidm_server.combat.vitality import hit_points, is_down, is_out_of_fight, is_player_character, revive
from aidm_server.game_clock import advance_state_clock
from aidm_server.game_state.campaign_pack_encounters import COMPLETED_ENCOUNTERS_FLAG, TRIGGERED_ENCOUNTERS_FLAG, catalog_record
from aidm_server.game_state.models import append_change_ledger, stable_change_id

DEFEAT_RECOVERY_CHANGE = 'combat.defeat_recovery'
DEFEAT_RECOVERY_SOURCE = 'engine_defeat_recovery'
DEFEAT_END_REASON = 'objective_failed'
PARTY_WAKE_CHANGE = 'party.wake'
PARTY_COME_ROUND_CHANGE = 'party.come_round'
# Set on an ended combat once its downed heroes have been woken (or found standing).
COME_ROUND_FLAG = 'comeRound'
ACTIVE_COMBAT_STATUSES = frozenset({'starting', 'active'})
ENDED_COMBAT_STATUSES = frozenset({'ended', 'resolved'})
WAKE_CHANGE_TYPES = frozenset({DEFEAT_RECOVERY_CHANGE, PARTY_WAKE_CHANGE, PARTY_COME_ROUND_CHANGE})


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
        _text(combat.get('status')).lower() in ENDED_COMBAT_STATUSES
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


def _participant(combat: dict[str, Any], hero_id: Any) -> dict[str, Any] | None:
    return next(
        (
            candidate
            for candidate in combat.get('participants') or []
            if isinstance(candidate, dict) and _text(candidate.get('id')) == _text(hero_id)
        ),
        None,
    )


def _wake(*records: dict[str, Any] | None) -> None:
    """One hero, as its state actor and its combat participant: 1 HP, no longer down or prone."""

    for record in records:
        if record is not None:
            revive(record, 1)
            _without_prone(record)


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
        _wake(hero, actor)
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
    authored = _on_defeat(state, encounter_id)
    triggered = state_flags.get(TRIGGERED_ENCOUNTERS_FLAG)
    if authored is not None:
        _apply_on_defeat(state, encounter_id, authored)
    elif encounter_id and isinstance(triggered, list):
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
    if authored is not None:
        change['onDefeat'] = {key: deepcopy(authored[key]) for key in ('text', 'wakeZoneId') if authored.get(key)}
    append_change_ledger(state, change)
    return change


def _on_defeat(state: dict[str, Any], encounter_id: str) -> dict[str, Any] | None:
    from aidm_server.game_state.orchestration.fight_stakes import on_defeat

    return on_defeat(catalog_record(state, 'encounters', encounter_id)) if encounter_id else None


def _apply_on_defeat(state: dict[str, Any], encounter_id: str, authored: dict[str, Any]) -> None:
    """The encounter's ``onDefeat``: complete for good, its flags and stake states, the party woken in its zone."""

    from aidm_server.game_state.zones import gather_in_zone

    state_flags = state.setdefault('flags', {})
    completed = [value for value in state_flags.get(COMPLETED_ENCOUNTERS_FLAG) or [] if _text(value)]
    state_flags[COMPLETED_ENCOUNTERS_FLAG] = list(dict.fromkeys([*completed, encounter_id]))
    for key, value in (authored.get('setsFlags') or {}).items() if isinstance(authored.get('setsFlags'), dict) else []:
        state_flags[_text(key)] = deepcopy(value)
    if isinstance(authored.get('stakes'), dict) and authored['stakes']:
        stored = state.get('stakes') if isinstance(state.get('stakes'), dict) else {}
        stored[encounter_id] = {**(stored.get(encounter_id) or {}), **{_text(key): _text(value) for key, value in authored['stakes'].items()}}
        state['stakes'] = stored
    if _text(authored.get('wakeZoneId')):
        gather_in_zone(state, _text(authored['wakeZoneId']))


def _present_actors(state: dict[str, Any]) -> list[dict[str, Any]]:
    active_ids = {value for value in state.get('activePlayerIds') or [] if isinstance(value, int) and not isinstance(value, bool)}
    return [
        actor
        for actor in state.get('playerCharacters') or []
        if isinstance(actor, dict) and (not active_ids or actor.get('playerId') in active_ids)
    ]


def _come_round(state: dict[str, Any], combat: dict[str, Any], *, turn_id: Any) -> dict[str, Any] | None:
    """After a fight that ended with a hero standing, every downed hero comes round with 1 HP; None otherwise."""

    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    heroes = [actor for actor in state.get('playerCharacters') or [] if isinstance(actor, dict)]
    fight_ended = (
        _text(combat.get('status')).lower() in ENDED_COMBAT_STATUSES
        and bool(_present_heroes(combat))
        and not isinstance(flags.get(COME_ROUND_FLAG), dict)
    )
    if not fight_ended and not any(is_out_of_fight(hero) for hero in heroes):
        return None
    if fight_ended:
        combat.setdefault('flags', {})[COME_ROUND_FLAG] = {'turnId': turn_id}
    pairs = [(hero, _participant(combat, hero.get('id'))) for hero in heroes if hit_points(hero)['max'] > 0]
    down = [(hero, participant) for hero, participant in pairs if is_down(hero) or (participant is not None and is_down(participant))]
    if not down or len(down) == len(pairs):
        return None
    for hero, participant in down:
        _wake(hero, participant)
    woken = [_text(hero.get('id')) for hero, _record in down]
    if fight_ended:
        combat['flags'][COME_ROUND_FLAG]['wokenActorIds'] = woken
    change = {
        'id': stable_change_id(turn_id, PARTY_COME_ROUND_CHANGE, *woken),
        'turnId': turn_id,
        'type': PARTY_COME_ROUND_CHANGE,
        'source': DEFEAT_RECOVERY_SOURCE,
        'wokenActorIds': woken,
        'reason': 'The fight is over and a hero is still standing; every downed hero comes round with 1 HP.',
        'visible': True,
    }
    append_change_ledger(state, change)
    return change


def party_wake(state: dict[str, Any], *, turn_id: Any) -> dict[str, Any] | None:
    """Out of a fight, wake the heroes who are down (``state`` is mutated); None when nobody wakes.

    After a fight that ended with a hero standing, the downed come round at once; when every present
    hero is down, they all come to a phase later.
    """

    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    if _text(combat.get('status')).lower() in ACTIVE_COMBAT_STATUSES:
        return None
    come_round = _come_round(state, combat, turn_id=turn_id)
    if come_round:
        return come_round
    heroes = _present_actors(state)
    if (
        not heroes
        # A hero without hit points on record is not known to be down.
        or not all(is_down(hero) and hit_points(hero)['max'] > 0 for hero in heroes)
    ):
        return None
    woken: list[str] = []
    for hero in heroes:
        _wake(hero, _participant(combat, hero.get('id')))
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
