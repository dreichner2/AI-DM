"""The director: at most one note a turn, and the clocks that give pressure a cause.

The note (:func:`director_note`). Each turn that is not wholly inside a fight carries at most one, the first of:

1. ``callback`` — a person's callback fired this turn (:mod:`aidm_server.game_state.orchestration.conversations`):
   its text, the reply's opening paragraph. A callback is only ever told here.
2. ``clock`` — a clock of the place moved this turn for its stated cause (:func:`advance_clocks`): the step's text,
   the reply's last paragraph.
3. ``clarify`` — the acting hero's message and their one before it were both refused or reached nothing. A narrating
   model is told ``Director — clarify:`` and :data:`CLARIFY`; the scripted narrator ends the reply with one sentence
   naming two things on offer (:func:`clarify_sentence`).
4. ``opportunity`` — this turn and the three before it at this place brought no beat but hints, and an authored
   ``nudge`` of the place is unused (on a scene object not yet touched, one of its affordances, or an available topic
   of someone present): that nudge, the reply's last paragraph, once.
5. ``breathe`` — the turn before won a fight, uncovered a secret, made or kept a promise or brought a callback. A
   narrating model is told ``Director — breathe:`` and :data:`BREATHE`; the scripted narrator adds nothing.

A note that is authored text (callback, clock, opportunity) is told as written by the engine with any narrator; only
``clarify`` and ``breathe`` reach a model, as instructions. A turn that starts and ends inside a fight carries none;
one that ends a fight may carry a callback, one that starts one a callback or a clock.

Clocks (:func:`advance_clocks`). Pack locations and encounters may carry ``clocks``: ``{"id", "advanceOn":
{"flags"?: [keys], "objectActions"?: ["<objectId>:<action>"], "everyTurnsAfterStart"?: n}, "steps": [{"text",
"effects"?}], "startWhen"?: {"flags"?, "encounterResolvedIds"?}}``. The clocks of the place the party is at are live:
the location's, and those of its encounters that have not begun. A live clock starts once its ``startWhen`` holds
(at once without one) or an affordance started it (its id under the state's ``clocksStarted``). Started, it gathers
causes: each listed flag the first time it is set, each listed object action the first time it succeeds, and every
``everyTurnsAfterStart`` turns taken there outside a fight since it started or last moved. On a turn outside a fight
with no callback, the first live clock with a cause moves one step: its text is the note and its ``effects``
(``setsFlags``, ``discoversLocationIds``, ``startsEncounterId`` — started as a triggered encounter starts, unless it
has begun — and ``affects``) apply. A cause left over waits for the next such turn, so every step is told; a clock
never moves without one. The state's ``clocks`` keeps each clock's ``step``, ``turns``, ``pending`` and ``spent``
causes.

What each turn left for the turns after it is its mark in the turn's metadata (:data:`MARK_KEY`): the hero, the
place, whether the turn was in a fight, a ``miss`` (refused, or words that reached nothing), whether something
``landed`` (a fight won, a secret, a promise, a callback) and its note's kind.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from aidm_server.game_state.campaign_pack_encounters import (
    COMPLETED_ENCOUNTERS_FLAG,
    RESOLVED_ENCOUNTERS_FLAG,
    TRIGGERED_ENCOUNTERS_FLAG,
    catalog_record,
    triggered_encounter_start,
    values_match,
)
from aidm_server.game_state.models import stable_change_id, stable_slug
from aidm_server.game_state.orchestration import conversations
from aidm_server.game_state.orchestration.hero_threads import THREAD_CHANGE
from aidm_server.game_state.zones import fight_running, hero_zone_id, npc_zone_id, object_zone_id, place_zones, present_npcs
from aidm_server.models import DmTurn, safe_json_loads

NOTE_KEY = 'director'
MARK_KEY = 'director'
DIRECTOR_KEY = 'director'
CLOCKS_KEY = 'clocks'
# W9-SCENE: the ids of the clocks an affordance started.
CLOCKS_STARTED_KEY = 'clocksStarted'
NUDGES_USED_KEY = 'nudgesUsed'
DIRECTOR_SOURCE = 'director'
CLARIFY = 'Say plainly, in the fiction, what is here and within reach, and what stands in the way.'
BREATHE = 'Let this moment land. Add no new pressure this turn.'
OPENING = 'opening'
CLOSING = 'closing'
# This turn and the three before it.
OPPORTUNITY_TURNS = 4
WON = frozenset({'all_enemies_defeated', 'enemies_surrendered', 'negotiated_resolution', 'enemies_fled'})
# How the scripted narrator says a scene object is still untried, by its first action.
UNTRIED = {'search': 'unsearched', 'inspect': 'unexamined', 'open': 'unopened', 'unlock': 'locked', 'read': 'unread'}
UNKNOWN_CLUE_STATUSES = frozenset({'hidden', 'undiscovered', 'unknown'})


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    return [_text(item) for item in value if _text(item)] if isinstance(value, list) else []


def _flags(state: dict[str, Any]) -> dict[str, Any]:
    return _dict(state.get('flags'))


def _location_id(state: dict[str, Any]) -> str:
    return _text(_dict(state.get('currentScene')).get('locationId'))


def _scene_objects(state: dict[str, Any]) -> list[dict[str, Any]]:
    scene = _dict(state.get('currentScene'))
    return [entry for key in ('interactables', 'hazards') for entry in _dicts(scene.get(key))]


# Clocks ---------------------------------------------------------------------------------------------------------------


def _begun_encounters(state: dict[str, Any]) -> set[str]:
    flags = _flags(state)
    return {*_strings(flags.get(TRIGGERED_ENCOUNTERS_FLAG)), *_strings(flags.get(RESOLVED_ENCOUNTERS_FLAG)), *_strings(flags.get(COMPLETED_ENCOUNTERS_FLAG))}


def place_clocks(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The live clocks: those of the location the party is at, then those of its encounters that have not begun."""

    location_id = _location_id(state)
    if not location_id:
        return []
    clocks = [clock for clock in _dicts(catalog_record(state, 'locations', location_id).get('clocks')) if _text(clock.get('id'))]
    catalog = _dict(_dict(state.get('campaignPack')).get('catalog'))
    begun = _begun_encounters(state)
    for encounter in _dicts(catalog.get('encounters')):
        places = {*_strings(encounter.get('locationIds')), _text(encounter.get('locationId'))}
        if location_id in places and _text(encounter.get('id')) not in begun:
            clocks.extend(clock for clock in _dicts(encounter.get('clocks')) if _text(clock.get('id')))
    return clocks


def clock_records(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    value = state.get(CLOCKS_KEY)
    return {key: entry for key, entry in value.items() if isinstance(entry, dict)} if isinstance(value, dict) else {}


def _flag_holds(flags: dict[str, Any], key: Any, expected: Any) -> bool:
    slug = stable_slug(key)
    return values_match(flags[slug], expected) if slug in flags else expected is False


def _start_holds(state: dict[str, Any], clock: dict[str, Any]) -> bool:
    if _text(clock.get('id')) in _strings(state.get(CLOCKS_STARTED_KEY)):
        return True
    when = clock.get('startWhen')
    if not isinstance(when, dict):
        return True
    flags = _flags(state)
    return all(_flag_holds(flags, key, expected) for key, expected in _dict(when.get('flags')).items()) and set(
        _strings(flags.get(COMPLETED_ENCOUNTERS_FLAG))
    ).issuperset(_strings(when.get('encounterResolvedIds')))


def _object_succeeded(change: dict[str, Any]) -> bool:
    event = _dict(change.get('event'))
    return change.get('type') == 'scene.interactable.action' and not _text(event.get('type')).endswith('.check_failed')


def _turn_causes(clock: dict[str, Any], record: dict[str, Any], applied: Iterable[dict[str, Any]], flags_before: dict[str, Any]) -> list[str]:
    """The causes this turn's changes gave the clock (each listed flag first set, each listed object action done)."""

    advance = _dict(clock.get('advanceOn'))
    flag_keys = {stable_slug(key) for key in _strings(advance.get('flags'))}
    object_actions = {_text(entry).lower() for entry in _strings(advance.get('objectActions'))}
    known = {*_strings(record.get('spent')), *_strings(record.get('pending'))}
    causes: list[str] = []
    for change in applied:
        if change.get('type') == 'flag.set':
            key = stable_slug(change.get('flagKey'))
            cause = f'flag:{key}'
            if key in flag_keys and change.get('flagValue') not in (None, False, '', 0) and not flags_before.get(key) and cause not in known:
                causes.append(cause)
                known.add(cause)
        elif _object_succeeded(change):
            done = f"{_text(change.get('targetId'))}:{_text(change.get('objectAction'))}".lower()
            cause = f'object:{done}'
            if done in object_actions and cause not in known:
                causes.append(cause)
                known.add(cause)
    return causes


def _step_changes(state: dict[str, Any], clock_id: str, index: int, effects: dict[str, Any], *, turn_id: Any, actor_id: str) -> list[dict[str, Any]]:
    """The changes a clock step's ``effects`` make."""

    base = {'turnId': turn_id, 'source': DIRECTOR_SOURCE, 'reason': f'Clock {clock_id}, step {index + 1}.'}
    changes = [
        {**base, 'id': stable_change_id(turn_id, 'clock', clock_id, index, 'flag', key), 'type': 'flag.set', 'flagKey': _text(key), 'flagValue': deepcopy(value), 'visible': False}
        for key, value in _dict(effects.get('setsFlags')).items()
    ]
    changes.extend(
        {**base, 'id': stable_change_id(turn_id, 'clock', clock_id, index, 'location', location_id), 'type': 'location.discover', 'locationId': location_id, 'status': 'discovered', 'visible': True}
        for location_id in _strings(effects.get('discoversLocationIds'))
    )
    changes.extend(
        conversations.affect_changes(state, _dicts(effects.get('affects')), turn_id=turn_id, parent_id=stable_change_id(turn_id, 'clock', clock_id, index), hero_id=actor_id)
    )
    encounter_id = _text(effects.get('startsEncounterId'))
    encounter = catalog_record(state, 'encounters', encounter_id) if encounter_id else {}
    if encounter and encounter_id not in _begun_encounters(state) and not fight_running(state):
        start = triggered_encounter_start(state, encounter, turn_id=turn_id, cause_id=f'clock:{clock_id}:{index}')
        if start is not None:
            changes.append(start)
    return changes


def advance_clocks(
    state_before: dict[str, Any],
    state: dict[str, Any],
    applied: list[dict[str, Any]],
    *,
    turn_id: Any,
    actor_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Gather this turn's causes into the live clocks' records (written into ``state``) and, on a turn outside a fight
    with no callback, move the first clock with a cause one step: ``(the step's changes, the clock note)``."""

    records = clock_records(state)
    flags_before = _flags(state_before)
    quiet = not fight_running(state_before) and not fight_running(state)
    held = not quiet or any(change.get('type') == conversations.TALK_CHANGE and change.get('operation') == 'callback' for change in applied)
    moved: tuple[dict[str, Any], dict[str, Any]] | None = None
    for clock in place_clocks(state):
        clock_id = _text(clock.get('id'))
        steps = _dicts(clock.get('steps'))
        record = records.get(clock_id)
        starting = record is None
        if starting:
            if not _start_holds(state, clock):
                continue
            record = records[clock_id] = {'step': 0, 'turns': 0, 'pending': [], 'spent': [], 'startedTurn': turn_id}
        if int(record.get('step') or 0) >= len(steps):
            continue
        record['pending'] = [*_strings(record.get('pending')), *_turn_causes(clock, record, applied, flags_before)]
        every = _dict(clock.get('advanceOn')).get('everyTurnsAfterStart')
        if isinstance(every, int) and every > 0 and quiet and not starting:
            record['turns'] = int(record.get('turns') or 0) + 1
            if record['turns'] >= every and 'turns' not in record['pending']:
                record['pending'].append('turns')
        if moved is None and not held and record['pending']:
            moved = (clock, record)
    state[CLOCKS_KEY] = records
    if moved is None:
        return [], None
    clock, record = moved
    clock_id = _text(clock.get('id'))
    index = int(record.get('step') or 0)
    step = _dicts(clock.get('steps'))[index]
    cause = record['pending'].pop(0)
    record.update(step=index + 1, turns=0, spent=[*_strings(record.get('spent')), cause])
    record['pending'] = [entry for entry in record['pending'] if entry != 'turns']
    changes = _step_changes(state, clock_id, index, _dict(step.get('effects')), turn_id=turn_id, actor_id=actor_id)
    note = {'kind': 'clock', 'text': _text(step.get('text')), 'place': CLOSING, 'clockId': clock_id, 'step': index + 1, 'cause': cause}
    return changes, note if note['text'] else None


# Nudges ---------------------------------------------------------------------------------------------------------------


def _nudges_used(state: dict[str, Any]) -> set[str]:
    return set(_strings(_dict(state.get(DIRECTOR_KEY)).get(NUDGES_USED_KEY)))


def place_nudges(state: dict[str, Any], actor_id: str) -> list[tuple[str, str]]:
    """The place's unused nudges, ``(key, text)`` in order: each untouched visible object's own, then its affordances',
    then the available topics of the people present."""

    used = _nudges_used(state)
    found: list[tuple[str, str]] = []
    for entry in _scene_objects(state):
        object_id = _text(entry.get('id'))
        if entry.get('hidden') is True or _text(entry.get('lastInteractionAction')) or not object_id:
            continue
        found.append((f'object:{object_id}', _text(entry.get('nudge'))))
        found.extend((f"affordance:{object_id}:{_text(affordance.get('id'))}", _text(affordance.get('nudge'))) for affordance in _dicts(entry.get('affordances')))
    for npc in present_npcs(state):
        npc_id = _text(npc.get('id'))
        found.extend(
            (f"topic:{npc_id}:{_text(topic.get('id'))}", _text(topic.get('nudge')))
            for topic in conversations.available_topics(state, actor_id, npc_id)
            if not topic.get('generated')
        )
    return [(key, text) for key, text in found if text and key not in used]


def use_nudge(state: dict[str, Any], key: str) -> None:
    director = state.setdefault(DIRECTOR_KEY, {})
    director[NUDGES_USED_KEY] = [*_strings(director.get(NUDGES_USED_KEY)), key]


# Clarify --------------------------------------------------------------------------------------------------------------


def _the(name: str) -> str:
    return f'the {name[4:]}' if name.lower().startswith('the ') else f'the {name}'


def _untried(entry: dict[str, Any]) -> str:
    name = _text(entry.get('name'))
    last = name.split()[-1] if name.split() else ''
    plural = last.lower().endswith('s') and not last.lower().endswith('ss')
    actions = _strings(entry.get('allowedActions'))
    state = UNTRIED.get(actions[0].lower(), 'untouched') if actions else 'untouched'
    return f"{_the(name)} {'are' if plural else 'is'} still {state}"


def _unspoken(person: dict[str, Any]) -> bool:
    talk = _dict(person.get('talk'))
    return not (talk.get('greeted') or _strings(talk.get('told')) or talk.get('knowsTold') or _dicts(person.get('remembers')))


def clarify_sentence(state: dict[str, Any], actor_id: str) -> str:
    """``The Lead Wagon is still unsearched, and Tovin has not been spoken to.``: two things on offer, an untried
    object first and someone not yet spoken to, from the hero's room before the rest of the place; '' with nothing."""

    zones = place_zones(state)
    here = hero_zone_id(state, actor_id, zones)
    objects = [
        entry
        for entry in _scene_objects(state)
        if entry.get('hidden') is not True and not _text(entry.get('lastInteractionAction')) and _text(entry.get('name')) and _strings(entry.get('allowedActions'))
    ]
    people = [npc for npc in present_npcs(state) if _unspoken(conversations.person_record(state, npc.get('id')) or npc)]
    objects.sort(key=lambda entry: object_zone_id(state, entry.get('id'), zones) != here)
    people.sort(key=lambda npc: npc_zone_id(state, npc.get('id'), zones) != here)
    untried = [_untried(entry) for entry in objects]
    unspoken = [f"{conversations.first_name(npc.get('name'))} has not been spoken to" for npc in people]
    # An object and a person when both are on offer; else the first two of either.
    things = [*untried[:1], *unspoken[:1]] if untried and unspoken else [*untried, *unspoken][:2]
    if not things:
        return ''
    sentence = ', and '.join(things)
    return f'{sentence[:1].upper()}{sentence[1:]}.'


# What a turn left behind ------------------------------------------------------------------------------------------


def _landed(state_before: dict[str, Any], state_after: dict[str, Any], applied: Iterable[dict[str, Any]]) -> bool:
    """The turn won a fight, uncovered a secret (a topic with a check or a disposition floor, a hidden object, a tie
    line, a secret clue), made or kept a promise, or brought a callback."""

    for change in applied:
        change_type = change.get('type')
        if change_type == 'combat.end' and _text(change.get('endReason')).lower() in WON:
            return True
        if change_type == THREAD_CHANGE:
            return True
        if change_type == conversations.TALK_CHANGE and (
            change.get('operation') == 'callback'
            or change.get('promiseMade')
            or change.get('promiseKept')
            or conversations.SECRET_UNCOVERED in _strings(change.get('inspiration'))
        ):
            return True
    hidden_before = {_text(entry.get('id')) for entry in _scene_objects(state_before) if entry.get('hidden') is True}
    if any(_text(entry.get('id')) in hidden_before and entry.get('hidden') is not True for entry in _scene_objects(state_after)):
        return True
    secret_clues = {_text(clue.get('id')) for clue in _dicts(_dict(_dict(state_after.get('campaignPack')).get('catalog')).get('clues')) if clue.get('secret') is True}
    known_before = {_text(clue.get('id')) for clue in _dicts(state_before.get('clues')) if _text(clue.get('status')).lower() not in UNKNOWN_CLUE_STATUSES}
    return any(
        _text(clue.get('id')) in secret_clues - known_before and _text(clue.get('status')).lower() not in UNKNOWN_CLUE_STATUSES
        for clue in _dicts(state_after.get('clues'))
    )


def recent_marks(turn: DmTurn) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """The marks and beats of the session's turns before this one (newest first, as many as the opportunity needs)
    and the mark of this hero's own turn before it."""

    rows = (
        DmTurn.query.filter(DmTurn.session_id == turn.session_id, DmTurn.turn_id < turn.turn_id)
        .order_by(DmTurn.turn_id.desc())
        .limit(OPPORTUNITY_TURNS - 1)
        .all()
    )
    session_turns = []
    for row in rows:
        metadata = _dict(safe_json_loads(row.metadata_json, {}))
        session_turns.append({'mark': _dict(metadata.get(MARK_KEY)), 'beats': _dicts(metadata.get('storyBeats'))})
    own = next((entry['mark'] for entry, row in zip(session_turns, rows) if row.player_id == turn.player_id), None)
    if own is None:
        row = (
            DmTurn.query.filter(DmTurn.session_id == turn.session_id, DmTurn.turn_id < turn.turn_id, DmTurn.player_id == turn.player_id)
            .order_by(DmTurn.turn_id.desc())
            .first()
        )
        own = _dict(_dict(safe_json_loads(row.metadata_json, {})).get(MARK_KEY)) if row is not None else None
    return session_turns, own


def _quiet(entry: dict[str, Any], location_id: str) -> bool:
    mark = entry.get('mark') or {}
    return (
        bool(mark)
        and _text(mark.get('locationId')) == location_id
        and mark.get('fight') is not True
        and mark.get('note') != 'opportunity'
        and all(_text(beat.get('kind')) == 'hint' for beat in entry.get('beats') or [])
    )


def refusal_mark(state: dict[str, Any], actor_id: str, *, miss: bool) -> dict[str, Any]:
    """The mark of a turn refused before anything ran (the hero down, or reaching out of their reach)."""

    return {'heroId': actor_id, 'locationId': _location_id(state), 'fight': fight_running(state), 'miss': miss, 'landed': False}


def director_note(
    *,
    state_before: dict[str, Any],
    state_after: dict[str, Any],
    applied: list[dict[str, Any]],
    actor_id: str,
    narration: dict[str, Any],
    clock_note: dict[str, Any] | None,
    miss: bool,
    session_turns: list[dict[str, Any]],
    own_previous: dict[str, Any] | None,
    beats: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """This turn's note (or None) and its mark (see the module docstring). ``beats`` is called, only when the
    opportunity needs them, for this turn's story beats."""

    fight = fight_running(state_before) or fight_running(state_after)
    location_id = _location_id(state_after)
    mark = {
        'heroId': actor_id,
        'locationId': location_id,
        'fight': fight,
        'miss': miss,
        'landed': _landed(state_before, state_after, applied),
    }
    note: dict[str, Any] | None = None
    callbacks = [callback for callback in _dicts(narration.get('callbacks')) if _text(callback.get('text'))]
    if fight_running(state_before) and fight_running(state_after):
        note = None
    elif callbacks:
        first = callbacks[0]
        note = {'kind': 'callback', 'text': _text(first.get('text')), 'place': OPENING, 'npcId': _text(first.get('npcId')), 'name': _text(first.get('name'))}
    elif clock_note is not None:
        note = clock_note
    elif fight:
        note = None
    elif miss and (own_previous or {}).get('miss') is True and (own_previous or {}).get('note') != 'clarify':
        note = {'kind': 'clarify', 'instruction': CLARIFY, 'scripted': clarify_sentence(state_after, actor_id)}
    elif (
        len(session_turns) >= OPPORTUNITY_TURNS - 1
        and all(_quiet(entry, location_id) for entry in session_turns[: OPPORTUNITY_TURNS - 1])
        and (nudges := place_nudges(state_after, actor_id))
        and all(_text(beat.get('kind')) == 'hint' for beat in beats())
    ):
        key, text = nudges[0]
        note = {'kind': 'opportunity', 'text': text, 'place': CLOSING, 'nudge': key}
    elif session_turns and (_dict(session_turns[0].get('mark')).get('landed') is True):
        note = {'kind': 'breathe', 'instruction': BREATHE}
    if note is not None:
        mark['note'] = note['kind']
    return note, mark


# Telling it -----------------------------------------------------------------------------------------------------------


def told_by_engine(narration: Any, place: str) -> str:
    """The authored note's text the engine tells at the reply's ``opening`` or ``closing``; ''."""

    note = _dict(_dict(narration).get(NOTE_KEY))
    return _text(note.get('text')) if _text(note.get('place')) == place else ''


def scripted_closing(narration: Any) -> str:
    """What the scripted narrator ends the reply with: an authored note's text, or the clarify sentence; ''."""

    note = _dict(_dict(narration).get(NOTE_KEY))
    return told_by_engine(narration, CLOSING) or (_text(note.get('scripted')) if note.get('kind') == 'clarify' else '')


def engine_paragraphs(rules_hint: Any) -> tuple[str, str]:
    """``(opening, closing)``: the authored note's text the engine adds around a narrating model's reply."""

    narration = _dict(_dict(_dict(rules_hint).get('state_pipeline')).get('narration'))
    return told_by_engine(narration, OPENING), told_by_engine(narration, CLOSING)
