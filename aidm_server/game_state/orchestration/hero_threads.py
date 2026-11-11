"""Your hero in the story: the adventure tells their own threads, and what they do earns Inspiration.

Tie lines (:mod:`aidm_server.services.tie_lines`). Before narration, each of the pack's lines whose moment
came this turn (an object action that succeeded, a first arrival, an encounter starting, a long rest) and
that has not fired in this adventure fires for one present hero with that tie (``seeking``: an attachment
who is a person; ``burden``; ``desire`` of the line's kind): the acting hero when they have it, else the one
with the fewest lines fired, the earliest hero first. A line no present hero can carry stays unfired. A fired
line is a ``hero.thread`` change recorded in ``campaignPack.threads`` (``lineId``, ``heroId``, ``kind``,
``moment``, ``text`` filled, ``title``, ``values``); the narration packet's ``threads`` carries its text, told
as its own paragraph by the scripted narrator and given to a narrating model as :data:`PERSONAL_THREAD`. An
``ending`` line is never fired: the player view appends it to that ending's text for each hero whose
seeking line fired (:func:`aidm_server.services.tie_lines.ending_lines`).

Inspiration. A hero holds at most one (``inspiration`` on the hero, kept on the character row). It is
granted by :func:`grant_inspiration`, which returns the ``hero.inspiration`` change to apply (``operation``
``grant``) or None when the hero already holds one. Grants wired here: a tie line of theirs firing
(:data:`THREAD_REASON`); ending a fight without killing — a surrender or negotiation on their own turn — or
sparing at the finishing blow (:data:`MERCY_REASON`); an object action the pack marks ``inspires``
(``{"<action>": "<reason>"}``). ``Take heart (Inspiration)`` spends it (``operation`` ``spend``): free, in a
fight (a ``take_heart`` fight action) or out of one (the ``inspiration`` intent). The hero gains the active
effect ``inspired``: their next attack roll, check or saving throw — theirs or one the engine makes for
their action — is made with advantage, and spends it (the modifier engine's ``d20`` consume event; an object
check or save, or a conversation's check, spends it here, ``operation`` ``clear``).
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Iterable

from aidm_server.character_ties import attachment_is_person
from aidm_server.combat.legal_actions import register_action_provider, register_action_resolver
from aidm_server.combat.modifiers import D20_CONSUME_EVENT
from aidm_server.game_state.models import find_actor, stable_change_id
from aidm_server.services.tie_lines import (
    AT_ENCOUNTER,
    AT_LOCATION,
    AT_LONG_REST,
    AT_OBJECT,
    THREADS_KEY,
    TIE_LINES_KEY,
    fill_tie_line,
)

THREAD_CHANGE = 'hero.thread'
INSPIRATION_CHANGE = 'hero.inspiration'
HERO_CHANGE_TYPES = frozenset({THREAD_CHANGE, INSPIRATION_CHANGE})
# Only the engine makes these changes; one any other stage proposes is refused.
HERO_CHANGE_SOURCE = 'hero_threads'
INSPIRED_EFFECT_ID = 'inspired'
THREAD_REASON = 'a thread of your own'
MERCY_REASON = 'mercy'
THREAD_TITLE = 'A thread of your own'
TAKE_HEART_LABEL = 'Take heart (Inspiration)'
TAKE_HEART_ACTION_PREFIX = 'inspiration.'
TAKE_HEART_ACTION_ID = 'inspiration.take_heart'
TAKE_HEART_TYPE = 'take_heart'
TAKE_HEART_DECLARED = 'inspiration.take_heart'
TAKE_HEART_DESCRIPTION = 'Free: your next d20 roll is made with advantage.'
NO_INSPIRATION = 'You hold no Inspiration.'
ALREADY_INSPIRED = 'You have already taken heart; your next roll has advantage.'
PERSONAL_THREAD = 'Personal thread — tell it in one or two sentences, facts unchanged:'
MERCY_END_REASONS = frozenset({'enemies_surrendered', 'negotiated_resolution'})
MERCY_OUTCOMES = frozenset({'surrender', 'negotiation'})
FIRING_MOMENTS = frozenset({AT_OBJECT, AT_LOCATION, AT_ENCOUNTER, AT_LONG_REST})
# Changes whose engine-made check may spend the advantage of a hero who took heart: an object's, a conversation's.
CHECKED_CHANGES = frozenset({'scene.interactable.action', 'npc.talk'})
FREE_ECONOMY = {
    'action': 0,
    'bonusAction': 0,
    'reaction': 0,
    'movement': 'unused',
    'movementCost': 0,
    'endsTurn': False,
}


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _pack(state: dict[str, Any]) -> dict[str, Any]:
    return state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}


def _ties(hero: dict[str, Any]) -> dict[str, Any]:
    return hero.get('ties') if isinstance(hero.get('ties'), dict) else {}


def present_heroes(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The heroes at the table: those in ``activePlayerIds`` when it names any, else every hero."""

    active = {value for value in state.get('activePlayerIds') or [] if isinstance(value, int) and not isinstance(value, bool)}
    return [hero for hero in _dicts(state.get('playerCharacters')) if not active or hero.get('playerId') in active]


def holds_inspiration(hero: dict[str, Any] | None) -> bool:
    return isinstance(hero, dict) and hero.get('inspiration') is True


def is_inspired(record: dict[str, Any] | None) -> bool:
    """The hero (or their fight record) has taken heart and not yet rolled."""

    return isinstance(record, dict) and any(_text(effect.get('id')) == INSPIRED_EFFECT_ID for effect in _dicts(record.get('activeEffects')))


def inspired_effect(hero_id: str) -> dict[str, Any]:
    return {
        'id': INSPIRED_EFFECT_ID,
        'sourceId': 'inspiration',
        'sourceName': 'Inspiration',
        'casterId': hero_id,
        'modifiers': {'attackMode': 'advantage', 'checkMode': 'advantage', 'saveMode': {'mode': 'advantage'}},
        'duration': None,
        'concentration': False,
        'consumeOn': D20_CONSUME_EVENT,
    }


def grant_inspiration(state: dict[str, Any], hero_id: str, reason: str, *, turn_id: Any = None) -> dict[str, Any] | None:
    """The change that gives the hero Inspiration for ``reason``; None when they already hold one."""

    hero = find_actor(state, hero_id)
    if hero is None or holds_inspiration(hero) or not _text(reason):
        return None
    return {
        'id': stable_change_id(turn_id, INSPIRATION_CHANGE, 'grant', hero.get('id')),
        'turnId': turn_id,
        'type': INSPIRATION_CHANGE,
        'source': HERO_CHANGE_SOURCE,
        'operation': 'grant',
        'actorId': _text(hero.get('id')),
        'reason': _text(reason),
        'visible': False,
    }


def take_heart_refusal(state: dict[str, Any], hero_id: str) -> str:
    """Why the hero cannot take heart now; '' when they can."""

    hero = find_actor(state, hero_id)
    if not holds_inspiration(hero):
        return NO_INSPIRATION
    return ALREADY_INSPIRED if is_inspired(hero) else ''


def take_heart_change(state: dict[str, Any], hero_id: str, *, turn_id: Any) -> dict[str, Any]:
    hero = find_actor(state, hero_id) or {}
    return {
        'id': stable_change_id(turn_id, INSPIRATION_CHANGE, 'spend', hero.get('id')),
        'turnId': turn_id,
        'type': INSPIRATION_CHANGE,
        'source': HERO_CHANGE_SOURCE,
        'operation': 'spend',
        'actorId': _text(hero.get('id')),
        'reason': 'Take heart (Inspiration).',
        'visible': False,
    }


# Tie lines ------------------------------------------------------------------------------------------------------------


def tie_line_values(hero: dict[str, Any]) -> dict[str, str]:
    """What a hero's ties fill a line's tokens with."""

    ties = _ties(hero)
    attachment = ties.get('attachment') if isinstance(ties.get('attachment'), dict) else {}
    values = {'hero': _text(hero.get('name'))}
    if _text(attachment.get('name')):
        values.update(name=_text(attachment['name']), relation=_text(attachment.get('relation')))
    if _text(ties.get('burden')):
        values['burden'] = _text(ties['burden'])
    return values


def has_tie(hero: dict[str, Any], line: dict[str, Any]) -> bool:
    ties = _ties(hero)
    kind = _text(line.get('kind'))
    if kind == 'seeking':
        return attachment_is_person(ties)
    if kind == 'burden':
        return bool(_text(ties.get('burden')))
    desire = ties.get('desire') if isinstance(ties.get('desire'), dict) else {}
    return kind == 'desire' and _text(desire.get('kind')) == _text(line.get('desire'))


def thread_title(line: dict[str, Any], values: dict[str, str]) -> str:
    """``A thread of your own — Edda Vale`` for a seeking line; ``A thread of your own`` for any other."""

    return f"{THREAD_TITLE} — {values['name']}" if _text(line.get('kind')) == 'seeking' and values.get('name') else THREAD_TITLE


def _object_action_succeeded(change: dict[str, Any]) -> bool:
    event = change.get('event') if isinstance(change.get('event'), dict) else {}
    return change.get('type') == 'scene.interactable.action' and not _text(event.get('type')).endswith('.check_failed')


def _line_due(line: dict[str, Any], *, state_before: dict[str, Any], applied: list[dict[str, Any]]) -> bool:
    at = line.get('at') if isinstance(line.get('at'), dict) else {}
    moment = _text(line.get('moment'))
    if moment == AT_OBJECT:
        return any(
            _object_action_succeeded(change)
            and _text(change.get('targetId')) == _text(at.get('objectId'))
            and _text(change.get('objectAction')).lower() == _text(at.get('action'))
            for change in applied
        )
    if moment == AT_LOCATION:
        entered = (state_before.get('flags') or {}).get('locationEnteredIds') if isinstance(state_before.get('flags'), dict) else []
        return _text(at.get('locationId')) not in {_text(value) for value in entered or []} and any(
            change.get('type') == 'scene.move_location' and _text(change.get('locationId')) == _text(at.get('locationId'))
            for change in applied
        )
    if moment == AT_ENCOUNTER:
        return any(
            change.get('type') == 'combat.start'
            and _text(change.get('campaignPackEncounterId') or ((change.get('combat') or {}).get('flags') or {}).get('campaignPackEncounterId'))
            == _text(at.get('encounterId'))
            for change in applied
        )
    if moment == AT_LONG_REST:
        return any(change.get('type') == 'rest.complete' and _text(change.get('restType')) == 'long_rest' for change in applied)
    return False


def fired_threads(state: dict[str, Any]) -> list[dict[str, Any]]:
    return _dicts(_pack(state).get(THREADS_KEY))


def _carrier(candidates: list[dict[str, Any]], actor_id: str, counts: Counter) -> dict[str, Any]:
    acting = next((hero for hero in candidates if _text(hero.get('id')) == actor_id), None)
    if acting is not None:
        return acting
    return min(candidates, key=lambda hero: (counts[_text(hero.get('id'))], int(hero.get('playerId') or 0)))


def tie_line_changes(
    *,
    state_before: dict[str, Any],
    state: dict[str, Any],
    applied: list[dict[str, Any]],
    actor_id: str,
    turn_id: Any,
) -> list[dict[str, Any]]:
    """The ``hero.thread`` changes of the lines this turn's results bring due (see the module docstring)."""

    fired = fired_threads(state)
    fired_ids = {_text(record.get('lineId')) for record in fired}
    counts = Counter(_text(record.get('heroId')) for record in fired)
    heroes = present_heroes(state)
    changes = []
    for line in _dicts(_pack(state).get(TIE_LINES_KEY)):
        if (
            _text(line.get('moment')) not in FIRING_MOMENTS
            or _text(line.get('id')) in fired_ids
            or not _line_due(line, state_before=state_before, applied=applied)
        ):
            continue
        candidates = [hero for hero in heroes if has_tie(hero, line)]
        if not candidates:
            continue
        hero = _carrier(candidates, actor_id, counts)
        hero_id = _text(hero.get('id'))
        counts[hero_id] += 1
        values = tie_line_values(hero)
        changes.append(
            {
                'id': stable_change_id(turn_id, THREAD_CHANGE, line.get('id')),
                'turnId': turn_id,
                'type': THREAD_CHANGE,
                'source': HERO_CHANGE_SOURCE,
                'actorId': hero_id,
                'lineId': _text(line.get('id')),
                'kind': _text(line.get('kind')),
                'moment': _text(line.get('moment')),
                'text': fill_tie_line(_text(line.get('text')), values),
                'title': thread_title(line, values),
                'values': values,
                'visible': False,
            }
        )
    return changes


# Inspiration earned this turn ------------------------------------------------------------------------------------


def _mercy(applied: list[dict[str, Any]], action_intent: dict[str, Any] | None, finisher: dict[str, Any]) -> bool:
    """The acting hero ended the fight without killing, or spared the foe at the finishing blow."""

    if finisher.get('spared') is True:
        return True
    intent = action_intent if isinstance(action_intent, dict) else {}
    combat = intent.get('combat') if isinstance(intent.get('combat'), dict) else {}
    if _text(combat.get('action_id')) == 'combat.end_turn':
        return False
    return any(
        (change.get('type') == 'combat.end' and _text(change.get('endReason')).lower() in MERCY_END_REASONS)
        or (change.get('type') == 'encounter.resolve' and _text(change.get('outcome')).lower() in MERCY_OUTCOMES)
        for change in applied
    )


def _scene_object(state: dict[str, Any], target_id: Any) -> dict[str, Any]:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    return next(
        (entry for key in ('interactables', 'hazards') for entry in _dicts(scene.get(key)) if _text(entry.get('id')) == _text(target_id)),
        {},
    )


def _inspiring_reasons(state_before: dict[str, Any], applied: list[dict[str, Any]], actor_id: str) -> list[str]:
    """The reasons of the object actions the hero did this turn that the pack marks ``inspires``."""

    reasons = []
    for change in applied:
        if not _object_action_succeeded(change) or _text(change.get('actorId')) != actor_id:
            continue
        inspires = _scene_object(state_before, change.get('targetId')).get('inspires')
        reason = inspires.get(_text(change.get('objectAction')).lower()) if isinstance(inspires, dict) else None
        if _text(reason):
            reasons.append(_text(reason))
    return reasons


def _spent_by(value: Any) -> set[str]:
    """The ids of the heroes whose ``inspired`` effect a roll recorded in ``value`` spent."""

    if isinstance(value, list):
        return {hero_id for item in value for hero_id in _spent_by(item)}
    if not isinstance(value, dict):
        return set()
    spent = {
        _text(entry.get('participantId'))
        for entry in _dicts(value.get('consume'))
        if _text(entry.get('effectId')) == INSPIRED_EFFECT_ID
    }
    return spent | {hero_id for key, item in value.items() if key != 'consume' for hero_id in _spent_by(item)}


def _clear_change(hero_id: str, turn_id: Any) -> dict[str, Any]:
    return {
        'id': stable_change_id(turn_id, INSPIRATION_CHANGE, 'clear', hero_id),
        'turnId': turn_id,
        'type': INSPIRATION_CHANGE,
        'source': HERO_CHANGE_SOURCE,
        'operation': 'clear',
        'actorId': hero_id,
        'reason': 'The roll was made with advantage.',
        'visible': False,
    }


def hero_turn_changes(
    *,
    state_before: dict[str, Any],
    state: dict[str, Any],
    applied: Iterable[dict[str, Any]],
    actor_id: str,
    turn_id: Any,
    action_intent: dict[str, Any] | None,
    finisher: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """The turn's tie lines, the Inspiration they and the hero's deeds earn, and the advantage an object roll spent.

    ``state_before`` is the state as the turn began, ``state`` after its results (``applied``).
    """

    applied = [change for change in applied if isinstance(change, dict)]
    threads = tie_line_changes(state_before=state_before, state=state, applied=applied, actor_id=actor_id, turn_id=turn_id)
    earned = [(_text(change['actorId']), THREAD_REASON) for change in threads]
    if _mercy(applied, action_intent, finisher or {}):
        earned.append((actor_id, MERCY_REASON))
    earned.extend((actor_id, reason) for reason in _inspiring_reasons(state_before, applied, actor_id))
    # W9-SCENE: another hero's plan this hero's success relied on, in the same round.
    from aidm_server.game_state.orchestration.scene_plans import teamwork_reasons

    earned.extend(teamwork_reasons(state_before, state, applied, actor_id))
    grants: dict[str, dict[str, Any]] = {}
    for hero_id, reason in earned:
        if hero_id not in grants and (grant := grant_inspiration(state, hero_id, reason, turn_id=turn_id)):
            grants[hero_id] = grant
    spent = {
        hero_id
        for change in applied
        if change.get('type') in CHECKED_CHANGES
        for hero_id in _spent_by(change.get('event') or change.get('check'))
        if is_inspired(find_actor(state, hero_id))
    }
    return [*threads, *grants.values(), *(_clear_change(hero_id, turn_id) for hero_id in sorted(spent))]


def _hero_name(state: dict[str, Any], hero_id: Any) -> str:
    return _text((find_actor(state, hero_id) or {}).get('name')) or 'The hero'


def hero_turn_facts(state: dict[str, Any], applied: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """The narration packet's ``threads`` (each fired line's ``heroId``, ``hero``, ``moment`` and ``text``) and
    ``takeHeart`` (``heroId``, ``hero``) from the turn's applied changes."""

    facts: dict[str, Any] = {}
    changes = [change for change in applied if isinstance(change, dict)]
    threads = [
        {'heroId': _text(change.get('actorId')), 'hero': _hero_name(state, change.get('actorId')), 'moment': _text(change.get('moment')), 'text': _text(change.get('text'))}
        for change in changes
        if change.get('type') == THREAD_CHANGE and _text(change.get('text'))
    ]
    if threads:
        facts['threads'] = threads
    spent = next((change for change in changes if change.get('type') == INSPIRATION_CHANGE and change.get('operation') == 'spend'), None)
    if spent:
        facts['takeHeart'] = {'heroId': _text(spent.get('actorId')), 'hero': _hero_name(state, spent.get('actorId'))}
    return facts


def hero_line(state: dict[str, Any], actor_id: str) -> str:
    """``Arden Vale wants to keep a duty and would not abandon Edda Vale (sister).``; '' without either tie."""

    hero = find_actor(state, actor_id) or {}
    ties = _ties(hero)
    desire = ties.get('desire') if isinstance(ties.get('desire'), dict) else {}
    attachment = ties.get('attachment') if isinstance(ties.get('attachment'), dict) else {}
    parts = []
    if _text(desire.get('words')):
        parts.append(f"wants {_text(desire['words'])}")
    if _text(attachment.get('name')):
        parts.append(f"would not abandon {_text(attachment['name'])} ({_text(attachment.get('relation'))})")
    return f"{_text(hero.get('name')) or 'The hero'} {' and '.join(parts)}." if parts else ''


# Changes: validation and application --------------------------------------------------------------------------------


def hero_change_problem(state: dict[str, Any], change: dict[str, Any]) -> str:
    """Why a ``hero.thread`` or ``hero.inspiration`` change cannot apply; '' when it can."""

    if _text(change.get('source')) != HERO_CHANGE_SOURCE:
        return 'Only the engine tells a thread or moves Inspiration.'
    hero = find_actor(state, change.get('actorId'))
    if hero is None:
        return 'Hero not found.'
    if change.get('type') == THREAD_CHANGE:
        if not _text(change.get('lineId')) or not _text(change.get('text')):
            return 'A thread names its line and its text.'
        if any(_text(record.get('lineId')) == _text(change.get('lineId')) for record in fired_threads(state)):
            return 'That line has already been told in this adventure.'
        return ''
    operation = _text(change.get('operation'))
    if operation == 'grant':
        return 'The hero already holds Inspiration.' if holds_inspiration(hero) else ''
    if operation == 'spend':
        return take_heart_refusal(state, _text(hero.get('id')))
    return '' if operation == 'clear' else 'Unknown Inspiration operation.'


def _participant(state: dict[str, Any], hero_id: str) -> dict[str, Any] | None:
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    if _text(combat.get('status')).lower() not in {'starting', 'active'}:
        return None
    return next((entry for entry in _dicts(combat.get('participants')) if _text(entry.get('id')) == hero_id), None)


def _without_inspired(record: dict[str, Any]) -> None:
    if isinstance(record.get('activeEffects'), list):
        record['activeEffects'] = [effect for effect in record['activeEffects'] if not (isinstance(effect, dict) and _text(effect.get('id')) == INSPIRED_EFFECT_ID)]


def apply_hero_change(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    """Apply a ``hero.thread`` or ``hero.inspiration`` change to ``state``; the applied change's extra fields,
    or None when it no longer applies."""

    if hero_change_problem(state, change):
        return None
    hero = find_actor(state, change.get('actorId')) or {}
    hero_id = _text(hero.get('id'))
    if change.get('type') == THREAD_CHANGE:
        pack = state.setdefault('campaignPack', {})
        record = {key: deepcopy(change.get(key)) for key in ('lineId', 'kind', 'moment', 'text', 'title', 'values', 'turnId')}
        pack[THREADS_KEY] = [*fired_threads(state), {**record, 'heroId': hero_id}]
        return {'heroName': _text(hero.get('name'))}
    operation = _text(change.get('operation'))
    participant = _participant(state, hero_id)
    if operation == 'grant':
        hero['inspiration'] = True
    elif operation == 'spend':
        hero['inspiration'] = False
        for record in (hero, participant):
            if record is not None:
                _without_inspired(record)
                record['activeEffects'] = [*_dicts(record.get('activeEffects')), inspired_effect(hero_id)]
    else:
        for record in (hero, participant):
            if record is not None:
                _without_inspired(record)
    return {'heroName': _text(hero.get('name'))}


# Take heart in a fight ----------------------------------------------------------------------------------------------


def take_heart_actions(snapshot: dict[str, Any], participant: dict[str, Any], _combat: dict[str, Any]) -> list[dict[str, Any]]:
    """Action-provider: ``Take heart (Inspiration)`` for a hero who holds Inspiration and has not taken heart."""

    hero = find_actor(dict(snapshot), participant.get('id'))
    if not holds_inspiration(hero) or is_inspired(hero) or is_inspired(participant):
        return []
    return [
        {
            'id': TAKE_HEART_ACTION_ID,
            'type': TAKE_HEART_TYPE,
            'label': TAKE_HEART_LABEL,
            'description': TAKE_HEART_DESCRIPTION,
            'message': f"{_text(hero.get('name')) or 'The hero'} takes heart.",
            'economy': dict(FREE_ECONOMY),
        }
    ]


def resolve_take_heart_action(
    _snapshot: dict[str, Any],
    player: Any,
    action: dict[str, Any],
    target_id: str,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    if target_id:
        return None, 'combat_target_invalid', 'That combat action does not accept a target.'
    return (
        {
            'action_id': action['id'],
            'action_type': TAKE_HEART_TYPE,
            'economy': dict(FREE_ECONOMY),
            'authoritative': True,
            'message': _text(action.get('message')) or f'{_text(getattr(player, "character_name", "")) or "The hero"} takes heart.',
        },
        None,
        None,
    )


register_action_provider(take_heart_actions)
register_action_resolver(TAKE_HEART_ACTION_PREFIX, resolve_take_heart_action)
