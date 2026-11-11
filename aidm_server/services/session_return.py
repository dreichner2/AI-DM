"""Reasons to come back: the players' own plans, where a chapter ends, and what greets a party that returns.

Plans. The state's ``plans`` hold the players' own notes of what they mean to do: ``{"id", "text", "playerId",
"heroId", "source", "createdAt"}``, ``source`` ``player`` (``/plan`` or ``Note a plan``) or ``helper`` (an intention
a narrating model's pre-narration helper read in a hero's words, at most one a sitting). A plan stays until a player
marks it done.

A sitting is a run of turns with no gap longer than :data:`SITTING_GAP`.

Coming back (:func:`coming_back`). When a party opens an adventure whose last turn is more than
:data:`SITTING_GAP` old, the viewer is shown ``WHERE YOU STAND`` — the place (and room) and the active objective in
one sentence, and up to two people whose memory of the viewer's heroes, or whose regard, changed in the last sitting
— and ``YOU MEANT TO``, the plans.

The end of a chapter (:func:`chapter_end`). A checkpoint with ``"chapterBreak": true`` closes a chapter when it
completes: ``END OF CHAPTER n · {its playerTitle}``, then ``WHAT YOU DECIDED`` — every one of its ``decisionLines``
whose ``when`` holds, at most five — then ``STILL OPEN``: promises left open, leads not followed and the plans; then
its ``teaser``. A decision line's ``when`` may test ``flags``, ``stakes`` (``{"<encounterId>.<stakeId>":
"<state>"}``, read from the state's ``stakes`` written by the fights) and ``remembers`` (``{"npcId", "key"}``, or a
list of them, each a person who remembers that of some hero). Leads (``leads``: ``[{"text", "followed"?}]``) and
stakes are read here only, and are nothing when absent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
from typing import Any, Iterable

from aidm_server.game_state.campaign_pack_encounters import values_match
from aidm_server.game_state.models import display_actor_id, stable_slug
from aidm_server.game_state.orchestration import conversations
from aidm_server.game_state.zones import hero_zone_id, place_zones, zone_by_id, zone_phrase
from aidm_server.models import DmTurn, safe_json_loads

PLANS_KEY = 'plans'
STAKES_KEY = 'stakes'
LEADS_KEY = 'leads'
PLAN_TEXT_LIMIT = 160
PLAN_LIMIT = 20
PLAN_SOURCES = ('player', 'helper')
SITTING_GAP = timedelta(minutes=30)
DECISION_LIMIT = 5
COMING_BACK_PEOPLE = 2
# How the coming-back block says what a person remembers of the viewer's hero, by memory key.
REMEMBERED = {
    'claimed_authority': '{name} believes you act with authority.',
    'claimed_identity': '{name} knows who you say you are.',
    'caught_lying': '{name} knows you lied to them.',
    'threatened': '{name} has not forgotten your threat.',
    'promised': '{name} is holding you to your word.',
    'protected': '{name} remembers you stood over them.',
    'owned_the_lie': '{name} remembers you owned the lie.',
    'shown': '{name} remembers what you showed them.',
}
REGARD = {True: '{name} has warmed to you.', False: '{name} has cooled toward you.'}
SITTING_TURN_LIMIT = 400


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    return [_text(item) for item in value if _text(item)] if isinstance(value, list) else []


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


# Plans ----------------------------------------------------------------------------------------------------------------


def plans(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [plan for plan in _dicts(state.get(PLANS_KEY)) if _text(plan.get('id')) and _text(plan.get('text'))]


def plan_text(value: Any) -> str:
    """A plan's words as kept: one line, at most :data:`PLAN_TEXT_LIMIT` characters."""

    return ' '.join(_text(value).split())[:PLAN_TEXT_LIMIT].strip()


def note_plan(state: dict[str, Any], text: Any, *, player_id: int | None, source: str, now: datetime) -> dict[str, Any] | None:
    """Add a plan to the state; the plan, or None for no words (or a full list)."""

    words = plan_text(text)
    if not words or len(plans(state)) >= PLAN_LIMIT:
        return None
    plan = {
        'id': f'plan_{secrets.token_hex(6)}',
        'text': words,
        **({'playerId': player_id, 'heroId': display_actor_id(player_id)} if player_id is not None else {}),
        'source': source if source in PLAN_SOURCES else 'player',
        'createdAt': now.isoformat(),
    }
    state[PLANS_KEY] = [*plans(state), plan]
    return plan


def remove_plan(state: dict[str, Any], plan_id: Any) -> bool:
    """Mark a plan done (it leaves the list); False when there is no such plan."""

    kept = [plan for plan in plans(state) if _text(plan.get('id')) != _text(plan_id)]
    if len(kept) == len(plans(state)):
        return False
    state[PLANS_KEY] = kept
    return True


def plans_for_view(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The plans as players see them: ``{"id", "text", "playerId"?, "createdAt"}``."""

    return [
        {'id': _text(plan['id']), 'text': _text(plan['text']), **({'playerId': plan['playerId']} if isinstance(plan.get('playerId'), int) else {}), 'createdAt': _text(plan.get('createdAt'))}
        for plan in plans(state)
    ]


# Sittings -------------------------------------------------------------------------------------------------------------


def _turn_times(session_id: int, *, before_turn_id: int | None = None) -> list[tuple[int, datetime]]:
    query = DmTurn.query.with_entities(DmTurn.turn_id, DmTurn.created_at).filter(DmTurn.session_id == session_id)
    if before_turn_id is not None:
        query = query.filter(DmTurn.turn_id < before_turn_id)
    return [(turn_id, _aware(created)) for turn_id, created in query.order_by(DmTurn.turn_id.desc()).limit(SITTING_TURN_LIMIT).all() if created]


def last_sitting(session_id: int) -> tuple[list[int], datetime | None]:
    """The turn ids of the session's latest sitting, newest first, and when its last turn was taken."""

    times = _turn_times(session_id)
    sitting: list[int] = []
    previous: datetime | None = None
    for turn_id, created in times:
        if previous is not None and previous - created > SITTING_GAP:
            break
        sitting.append(turn_id)
        previous = created
    return sitting, times[0][1] if times else None


def sitting_began(session_id: int, *, turn_id: int, now: datetime) -> datetime:
    """When the sitting this turn belongs to began."""

    began = now
    for _turn_id, created in _turn_times(session_id, before_turn_id=turn_id):
        if began - created > SITTING_GAP:
            break
        began = created
    return began


def helper_plan_allowed(state: dict[str, Any], session_id: int, *, turn_id: int, now: datetime) -> bool:
    """No plan the helper read has been noted in this sitting (the one this turn belongs to)."""

    began = sitting_began(session_id, turn_id=turn_id, now=now)
    return not any(
        plan.get('source') == 'helper' and (created := _parse_time(plan.get('createdAt'))) is not None and created >= began
        for plan in plans(state)
    )


def _parse_time(value: Any) -> datetime | None:
    try:
        return _aware(datetime.fromisoformat(_text(value)))
    except ValueError:
        return None


# Coming back ----------------------------------------------------------------------------------------------------------


def _place_name(state: dict[str, Any]) -> str:
    scene = _dict(state.get('currentScene'))
    location = next((record for record in _dicts(state.get('locations')) if _text(record.get('id')) == _text(scene.get('locationId'))), {})
    return _text(location.get('name')) or _text(scene.get('name'))


def active_objective(state: dict[str, Any]) -> str:
    """The title of the first open objective of the active checkpoint; ''."""

    pack = _dict(state.get('campaignPack'))
    active = next((checkpoint for checkpoint in _dicts(pack.get('checkpoints')) if _text(checkpoint.get('id')) == _text(pack.get('activeCheckpointId'))), {})
    wanted = _strings(active.get('objectiveIds'))
    for quest in _dicts(state.get('quests')):
        for objective in _dicts(quest.get('objectives')):
            if _text(objective.get('id')) in wanted and _text(objective.get('status')).lower() not in {'completed', 'failed'}:
                return _text(objective.get('title')) or _text(objective.get('description'))
    return ''


def where_you_stand(state: dict[str, Any], hero_id: str) -> str:
    """``You are in the yard at the Lantern Post Inn, set to investigate the empty caravan.``"""

    place = _place_name(state)
    if not place:
        return ''
    zones = place_zones(state)
    room = zone_phrase(zone_by_id(zones, hero_zone_id(state, hero_id, zones))) if zones else ''
    objective = active_objective(state)
    if objective and len(objective) > 1 and objective[1].islower():
        objective = f'{objective[0].lower()}{objective[1:]}'
    place = f'the {place[4:]}' if place.startswith('The ') else place
    # "The Road" of the Old North Road says nothing the place's own name does not.
    if room and room.removeprefix('the ').lower() in place.lower():
        room = ''
    where = f'in {room} at {place}' if room else f'at {place}'
    return f"You are {where}{', set to ' + objective.rstrip('.') if objective else ''}."


def _people_changed(state: dict[str, Any], hero_ids: set[str], turn_ids: set[int], bonds: dict[str, bool]) -> list[str]:
    """Up to two people whose memory of the viewer's heroes, or whose regard, changed in the last sitting."""

    lines: list[tuple[int, str]] = []
    for person in [*_dicts(state.get('knownNpcs')), *_dicts(state.get('partyNpcs'))]:
        name = _text(person.get('name'))
        npc_id = _text(person.get('id'))
        recent = [
            entry
            for entry in conversations.memories(person)
            if _text(entry.get('heroId')) in hero_ids and isinstance(entry.get('turn'), int) and entry['turn'] in turn_ids
        ]
        if recent:
            entry = recent[-1]
            key = _text(entry.get('key'))
            line = REMEMBERED[key].format(name=name) if key in REMEMBERED else f'{name} remembers {conversations.memory_line(entry)}.'
            lines.append((int(entry['turn']), line))
        elif npc_id in bonds:
            lines.append((-1, REGARD[bonds[npc_id]].format(name=name)))
    lines.sort(key=lambda pair: pair[0], reverse=True)
    return [line for _turn, line in lines[:COMING_BACK_PEOPLE]]


def coming_back(session_id: int, state: dict[str, Any], *, player_ids: Iterable[int], now: datetime) -> dict[str, Any] | None:
    """The coming-back block for a viewer, or None when the last turn is no more than :data:`SITTING_GAP` old."""

    turn_ids, last_turn_at = last_sitting(session_id)
    if not turn_ids or last_turn_at is None or now - last_turn_at <= SITTING_GAP:
        return None
    owned = [player_id for player_id in player_ids if isinstance(player_id, int)]
    hero_ids = {display_actor_id(player_id) for player_id in owned}
    bonds: dict[str, bool] = {}
    for row in DmTurn.query.filter(DmTurn.turn_id.in_(turn_ids)).order_by(DmTurn.turn_id.asc()).all():
        for beat in _dicts(_dict(safe_json_loads(row.metadata_json, {})).get('storyBeats')):
            if beat.get('kind') == 'bond' and _text(beat.get('refId')):
                bonds[_text(beat['refId'])] = beat.get('down') is not True
    return {
        'where': where_you_stand(state, display_actor_id(owned[0])) if owned else '',
        'people': _people_changed(state, hero_ids, set(turn_ids), bonds),
        'plans': [plan['text'] for plan in plans_for_view(state)],
    }


# The end of a chapter -------------------------------------------------------------------------------------------------


def chapter_break_reached(session_id: int) -> bool:
    """A turn of this session ended its chapter at a chapter break (its beats carry the chapter-end entry)."""

    return DmTurn.query.filter(DmTurn.session_id == session_id, DmTurn.metadata_json.contains('"chapter_end"')).first() is not None


def stake_states(state: dict[str, Any]) -> dict[str, str]:
    """``{"<encounterId>.<stakeId>": "<state>"}`` for every stake of a finished fight; {} when there are none."""

    return {
        f'{_text(encounter_id)}.{_text(stake_id)}': _text(value)
        for encounter_id, stakes in _dict(state.get(STAKES_KEY)).items()
        for stake_id, value in _dict(stakes).items()
        if _text(value)
    }


def open_leads(state: dict[str, Any]) -> list[str]:
    """The leads not followed; [] when there are none."""

    return [_text(lead.get('text')) for lead in _dicts(state.get(LEADS_KEY)) if _text(lead.get('text')) and lead.get('followed') is not True]


def decision_holds(state: dict[str, Any], when: Any) -> bool:
    """Whether a decision line's ``when`` holds (``flags``, ``stakes``, ``remembers``); one without it always does."""

    if not isinstance(when, dict):
        return True
    flags = _dict(state.get('flags'))
    stakes = stake_states(state)
    remembers = when.get('remembers')
    remembered = [remembers] if isinstance(remembers, dict) else _dicts(remembers)
    return (
        all(
            values_match(flags[stable_slug(key)], expected) if stable_slug(key) in flags else expected is False
            for key, expected in _dict(when.get('flags')).items()
        )
        and all(stakes.get(_text(key)) == _text(expected) for key, expected in _dict(when.get('stakes')).items())
        and all(
            any(_text(entry.get('key')) == _text(wanted.get('key')) for entry in conversations.memories(conversations.person_record(state, wanted.get('npcId')) or {}))
            for wanted in remembered
        )
    )


def chapter_end(state: dict[str, Any], *, completed_before: Iterable[str], number: int) -> dict[str, Any] | None:
    """The chapter-end entry when a checkpoint with ``chapterBreak`` completed since ``completed_before``; None."""

    pack = _dict(state.get('campaignPack'))
    before = set(completed_before)
    completed = [checkpoint_id for checkpoint_id in _strings(pack.get('completedCheckpointIds')) if checkpoint_id not in before]
    checkpoint = next(
        (
            record
            for record in _dicts(pack.get('checkpoints'))
            if _text(record.get('id')) in completed and record.get('chapterBreak') is True
        ),
        None,
    )
    if checkpoint is None:
        return None
    decided = [_text(line.get('text')) for line in _dicts(checkpoint.get('decisionLines')) if _text(line.get('text')) and decision_holds(state, line.get('when'))]
    word = [row['line'] for row in conversations.word_lines(state, {_text(promise.get('heroId')) for promise in conversations.promises(state)}) if row['status'] == 'left open']
    return {
        'number': number,
        'title': _text(checkpoint.get('playerTitle')) or _text(checkpoint.get('title')),
        'checkpointId': _text(checkpoint.get('id')),
        'decided': decided[:DECISION_LIMIT],
        'open': [*word, *open_leads(state), *(plan['text'] for plan in plans_for_view(state))],
        **({'teaser': _text(checkpoint['teaser'])} if _text(checkpoint.get('teaser')) else {}),
    }
