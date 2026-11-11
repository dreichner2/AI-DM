"""What an improvised success leaves: one lasting fact about the place (W9-FREEFORM).

When. A successful improvised attempt in an adventure played from a pack: the roll turn of words the engine holds
nothing for, outside a fight, and the improvised fight action (whose ``off_balance`` stands). One fact per attempt; a
failure records nothing and is told as before.

Where the fact comes from. With a narrating model the helper (task :data:`TASK`) is asked once, before narration, for
``{"fact", "effect", "targetId", "about", "removes", "none"}``, given the words, the check, the place and room, the
things and people in the hero's room (id and name: nothing hidden, nobody unmet), the facts already true here and the
nine effects. With the scripted narrator, without the helper, or when it fails or times out, the fact is the engine's
own record of the words, ``{Hero}: "{words, at most 80 characters}" — done.``, with no effect, ``about`` or
``removes``.

What the engine accepts (:func:`fact_problem`; the reason a fact is refused is named in the turn pipeline's
``improvisedFact``, and the turn is told as before). The helper's ``fact`` is one sentence of 12–110 characters ending
in a full stop, without line breaks or digits the words did not hold (``length``, ``not_one_sentence``, ``digits``).
No fact names a hidden object or item, a room, place or clue not yet found (``names_hidden``, matched as the
hidden-thing rule matches, :func:`scene_plans.name_run`), a person of the pack the party has not met
(``names_unmet_person``) or, by full or first name, a person or hero who is not in the hero's room
(``person_not_here``); none gives or takes coin, xp, an item of the pack's that the hero does not hold and that does
not lie in the room, or a key, map, letter, secret, name or password (``gives_or_takes``); none kills, removes or turns
a person or enemy (``kills_or_turns``). A ``removes`` that is not an improvised fact of this place is
``unknown_removes``; an answer with no fact is ``invalid_answer`` and ``{"none": true}`` is ``none``. The engine's own
record is held to the same rules but for those of form.

What an accepted fact does. An ``effect`` with a ``targetId`` the table (:data:`scene_plans.TABLE`) allows for an
object in the hero's room is resolved without a second check through the plans' own path, and that path's fact is the
one kept; otherwise the fact is kept as an improvised fact of the place (:func:`scene_plans.add_improvised_fact`).
``removes`` drops the improvised fact it names. Each person in ``about`` who is in the hero's room remembers the fact
(``witnessed``).
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Callable, Iterable, Mapping

from aidm_server.contracts import ProviderRequest
from aidm_server.game_state.extraction.free_text_actions import name_words
from aidm_server.game_state.extraction.schemas import extract_json_object
from aidm_server.game_state.models import find_actor, stable_change_id
from aidm_server.game_state.orchestration import conversations
from aidm_server.game_state.orchestration.scene_plans import (
    EFFECT_CHANGE,
    EFFECT_SOURCE,
    EFFECTS,
    FROM_IMPROVISED,
    TABLE,
    helper_enabled,
    improvised_check,
    improvised_facts,
    name_run,
    object_tags,
    resolve_plan,
    scene_facts,
    table_plan,
    things,
)
from aidm_server.game_state.zones import hero_zone_id, known_zone_ids, place_zones, present_heroes
from aidm_server.llm_providers import get_helper_provider
from aidm_server.provider_priority import foreground_provider_reservation
from aidm_server.telemetry import telemetry_event

TASK = 'improvised_fact'
# The turn pipeline's record of how the fact was decided (:func:`improvised_fact`, without its changes).
PIPELINE_KEY = 'improvisedFact'
SYSTEM_MESSAGE = (
    "A player's improvised attempt in a tabletop game has succeeded. Say in one short present-tense sentence what is "
    'now lastingly true in the scene because of it, using only the things and people listed. If it plainly is one of '
    'the listed effects on one of the listed things, name the effect and the thing. If nothing lasting changed, answer '
    '{"none": true}. Never give the hero an item, coin or knowledge of hidden things; never kill or remove a person; '
    'never move the hero to another place. Return only the JSON object.'
)
PROMPT_HEAD = 'Improvised attempt by '
RAW_PREVIEW_LIMIT = 600
FALLBACK_WORDS_LIMIT = 80
FACT_LENGTH = (12, 110)
DIGITS = re.compile(r'\d+')
SENTENCE_BREAK = re.compile(r'[.!?]["”’)\]]*\s+\S')
COIN = re.compile(r'\b(gold|gp|silver|coins?|xp)\b', re.IGNORECASE)
KNOWLEDGE = re.compile(
    r'\b(now )?(has|have|holds?|carr(y|ies)|gains?|finds?|found|learns?|knows?)\b.*\b(keys?|maps?|letters?|secrets?|names?|passwords?)\b',
    re.IGNORECASE,
)
KILLS_OR_TURNS = re.compile(r'\b(dead|dies|died|killed|slain|gone for good|joins? (you|the party)|is now (your|an) ally)\b', re.IGNORECASE)
# Where a pack puts the items it places in the world (the pack's item catalog; a shop's goods are trade, not that).
ITEM_LISTS = frozenset({'contents', 'items', 'givesItems', 'carrying'})


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _catalog(state: Mapping[str, Any]) -> dict[str, Any]:
    return _dict(_dict(state.get('campaignPack')).get('catalog'))


def _scene(state: Mapping[str, Any]) -> dict[str, Any]:
    return _dict(state.get('currentScene'))


def fallback_fact(hero_name: str, words: str) -> str:
    """The engine's own record of an improvised success: ``Arden Vale: "I climb onto the stable roof." — done.``"""

    return f'{_text(hero_name) or "The hero"}: "{_text(words)[:FALLBACK_WORDS_LIMIT]}" — done.'


# What the helper is given --------------------------------------------------------------------------------------------


def _room(state: Mapping[str, Any], actor_id: str) -> list[dict[str, Any]]:
    """The things and people in the hero's room (in a fight, its zone): nothing hidden, nobody unmet."""

    return [thing for thing in things(state, actor_id) if thing['steps'] == 0]


def _room_name(state: Mapping[str, Any], actor_id: str) -> str:
    zones = place_zones(state)
    here = hero_zone_id(state, actor_id, zones)
    return next((zone['name'] for zone in zones if zone['id'] == here), '')


def _prompt(state: Mapping[str, Any], *, actor_id: str, hero: str, words: str, check: Mapping[str, Any]) -> str:
    place = _text(_scene(state).get('name')) or 'an unnamed place'
    room = _room_name(state, actor_id)
    pool = '\n'.join(f"- {thing['id']}: {thing['name']}" for thing in _room(state, actor_id)) or '- none'
    improvised = {_text(record.get('text')) for record in improvised_facts(state)}
    true_here = '\n'.join(f"- {fact}{' (improvised)' if fact in improvised else ''}" for fact in scene_facts(state)) or '- nothing yet'
    return (
        f'{PROMPT_HEAD}{hero}:\n"{_text(words)}"\n'
        f'{improvised_check(check)}: success.\n\n'
        f"Place: {place}{f', {room}' if room else ''}.\n"
        f'Things and people in the room (id: name):\n{pool}\n\n'
        f'Already true here:\n{true_here}\n\n'
        f"Effects: {', '.join(EFFECTS)}.\n\n"
        'Answer {"fact": "<one sentence>", "effect": "<one effect>" or null, "targetId": "<an id above>" or null, '
        '"about": ["<ids above it concerns>"], "removes": "<an improvised fact above that this undoes, exactly>" or null, '
        '"none": false}.'
    )


def _debug(*, model: str | None = None, raw: str | None = None, error: str | None = None, parsed: Any = None) -> dict[str, Any]:
    return {
        'source': 'improvised_fact_helper',
        'reason': TASK,
        'helperAttempted': True,
        'helperSchemaValid': parsed is not None,
        'helperModel': model,
        'helperRawText': raw,
        'helperRawPreview': raw[:RAW_PREVIEW_LIMIT] if raw else None,
        'helperParsed': parsed,
        'helperError': error,
        'fallbackRan': False,
        'fallbackReason': None,
    }


def helper_answer(
    state: Mapping[str, Any],
    *,
    actor_id: str,
    hero: str,
    words: str,
    check: Mapping[str, Any],
    before_provider_call: Callable[[], None] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The helper's answer, asked once (None when it is not a JSON object), and its debug record; ``helperError`` is
    set when the call failed or timed out."""

    with foreground_provider_reservation() as activate_provider:
        if before_provider_call:
            before_provider_call()
        try:
            activate_provider()
            response = get_helper_provider(task=TASK).generate(
                ProviderRequest(prompt=_prompt(state, actor_id=actor_id, hero=hero, words=words, check=check), system_message=SYSTEM_MESSAGE)
            )
        except Exception as exc:
            telemetry_event('state_pipeline.improvised_fact_helper.failed', payload={'error': str(exc)[:300]}, severity='warning')
            return None, _debug(error=str(exc)[:300] or type(exc).__name__)
    raw = str(response.text or '')
    payload = extract_json_object(raw)
    return payload, _debug(model=response.model, raw=raw, parsed=payload)


# What the engine accepts ---------------------------------------------------------------------------------------------


def _object_records(state: Mapping[str, Any], location: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A place's objects as they stand: the scene's here, else as last left, else as the pack wrote them."""

    location_id = _text(location.get('id'))
    if location_id == _text(_scene(state).get('locationId')):
        source: Mapping[str, Any] = _scene(state)
    else:
        source = _dict(_dict(state.get('locationSceneStates')).get(location_id)) or location
    return [entry for key in ('interactables', 'hazards') for entry in _dicts(source.get(key))]


def _hidden(record: Mapping[str, Any]) -> bool:
    return record.get('gmOnly') is True or (record.get('hidden') is True and record.get('playerKnown') is not True)


def _hidden_names(state: Mapping[str, Any]) -> list[str]:
    """The names of what the party has not found: hidden objects (here and at every place of the pack), hidden items
    here, rooms here and places not yet known, and clues not yet found."""

    current = _text(_scene(state).get('locationId'))
    names = [_text(entry.get('name')) for entry in _object_records(state, {'id': current}) if _hidden(entry)]
    names.extend(_text(item.get('name')) for item in _dicts(_scene(state).get('items')) if _hidden(item))
    known_places = {_text(location.get('id')) for location in _dicts(state.get('locations'))}
    for location in _dicts(_catalog(state).get('locations')):
        if _text(location.get('id')) not in known_places:
            names.append(_text(location.get('name')))
        if _text(location.get('id')) != current:
            names.extend(_text(entry.get('name')) for entry in _object_records(state, location) if _hidden(entry))
    zones = place_zones(state)
    known_rooms = set(known_zone_ids(state, zones))
    names.extend(zone['name'] for zone in zones if zone['id'] not in known_rooms)
    found = {_text(clue.get('id')) for clue in _dicts(state.get('clues')) if _text(clue.get('status')).lower() not in conversations.UNKNOWN_CLUE_STATUSES}
    for clue in [*_dicts(_catalog(state).get('clues')), *_dicts(state.get('clues'))]:
        if _text(clue.get('id')) not in found:
            names.append(_text(clue.get('title') or clue.get('name')))
    return [name for name in names if name]


def _seen_names(state: Mapping[str, Any], actor_id: str) -> list[set[str]]:
    """The name words of what the party can see or knows by name here: words naming one of these name that."""

    zones = place_zones(state)
    known_rooms = set(known_zone_ids(state, zones))
    names = [thing['name'] for thing in things(state, actor_id)]
    names.extend(zone['name'] for zone in zones if zone['id'] in known_rooms)
    names.extend(_text(location.get('name')) for location in _dicts(state.get('locations')))
    return [set(name_words(name)) for name in names if name_words(name)]


def _names_any(words: list[str], names: Iterable[str], seen: list[set[str]]) -> bool:
    return any(name_run(words, name_words(name), seen) for name in names if name_words(name))


def _named_person(text: str, words: list[str], name: str) -> bool:
    """The text names the person by full name, or by first name as written (three letters or more)."""

    full = name_words(name)
    if full and any(words[index:index + len(full)] == full for index in range(len(words) - len(full) + 1)):
        return True
    first = conversations.first_name(name)
    return first != _text(name) and len(first) >= 3 and re.search(rf'\b{re.escape(first)}\b', text) is not None


def _person_problem(state: Mapping[str, Any], actor_id: str, text: str, words: list[str]) -> str:
    known = {_text(npc.get('id')) for key in ('knownNpcs', 'partyNpcs') for npc in _dicts(state.get(key))}
    people = {
        _text(npc.get('id')): _text(npc.get('name'))
        for npc in [*_dicts(_catalog(state).get('npcs')), *_dicts(state.get('knownNpcs')), *_dicts(state.get('partyNpcs'))]
        if _text(npc.get('id')) and _text(npc.get('name'))
    }
    for npc_id, name in people.items():
        if not _named_person(text, words, name):
            continue
        if npc_id not in known:
            return 'names_unmet_person'
        if not conversations.in_room(dict(state), actor_id, npc_id):
            return 'person_not_here'
    zones = place_zones(state)
    here = hero_zone_id(state, actor_id, zones)
    present = {_text(hero.get('id')) for hero in present_heroes(state)}
    for hero in _dicts(state.get('playerCharacters')):
        hero_id = _text(hero.get('id'))
        if hero_id == actor_id or not _named_person(text, words, _text(hero.get('name'))):
            continue
        if hero_id not in present or hero_zone_id(state, hero_id, zones) != here:
            return 'person_not_here'
    return ''


def _pack_items(value: Any, *, listed: bool = False) -> Iterable[dict[str, Any]]:
    """Every item record the pack places in the world (``contents``, ``items``, ``givesItems``, ``carrying``)."""

    if isinstance(value, dict):
        if listed and _text(value.get('name')):
            yield value
        for key, inner in value.items():
            yield from _pack_items(inner, listed=key in ITEM_LISTS)
    elif isinstance(value, list):
        for inner in value:
            yield from _pack_items(inner, listed=listed)


def _item_problem(state: Mapping[str, Any], actor_id: str, words: list[str], seen: list[set[str]]) -> bool:
    """The text names an item of the pack's that the hero does not hold and that does not lie in the room."""

    at_hand = {
        key
        for thing in _room(state, actor_id)
        if thing['kind'] == 'item'
        for key in (thing['id'], _text(thing['record'].get('sourceItemId')), thing['name'].lower())
        if key
    }
    names = [
        _text(item['name'])
        for item in _pack_items([_catalog(state), _dicts(state.get('locations')), {'items': _scene(state).get('items')}])
        if not {_text(item.get('id')), _text(item['name']).lower()} & at_hand
    ]
    return _names_any(words, names, seen)


def fact_problem(state: Mapping[str, Any], *, actor_id: str, words: str, fact: str, own: bool = False) -> str:
    """Why the engine refuses this fact (a reason named in the module docstring); '' when it accepts it. ``own`` is the
    engine's own record of the words, held to every rule but those of form."""

    if not own:
        if '\n' in fact or '\r' in fact:
            return 'not_one_sentence'
        if not FACT_LENGTH[0] <= len(fact) <= FACT_LENGTH[1]:
            return 'length'
        if not fact.endswith('.') or SENTENCE_BREAK.search(fact):
            return 'not_one_sentence'
        if set(DIGITS.findall(fact)) - set(DIGITS.findall(words)):
            return 'digits'
    tokens = name_words(fact)
    seen = _seen_names(state, actor_id)
    if _names_any(tokens, _hidden_names(state), seen):
        return 'names_hidden'
    person = _person_problem(state, actor_id, fact, tokens)
    if person:
        return person
    if COIN.search(fact) or KNOWLEDGE.search(fact) or _item_problem(state, actor_id, tokens, seen):
        return 'gives_or_takes'
    if KILLS_OR_TURNS.search(fact):
        return 'kills_or_turns'
    return ''


# What an accepted fact does ------------------------------------------------------------------------------------------


def _effect_plan(state: Mapping[str, Any], actor_id: str, effect: str, target_id: str) -> dict[str, Any] | None:
    """The table's plan of this effect on an object in the hero's room; None when the table does not hold the pair."""

    target = next((thing for thing in _room(state, actor_id) if thing['id'] == target_id and thing['kind'] == 'object'), None)
    if effect not in TABLE or target is None:
        return None
    if not any(object_tags(target['record']) & works_on for works_on, _check in TABLE[effect]):
        return None
    return table_plan(state, actor_id, effect, object_id=target_id, subject_id=None)


def _fact_change(state: Mapping[str, Any], *, actor_id: str, hero: str, fact: str, removes: str, turn_id: Any) -> dict[str, Any]:
    return {
        'id': stable_change_id(turn_id, EFFECT_CHANGE, 'improvised', actor_id),
        'turnId': turn_id,
        'type': EFFECT_CHANGE,
        'source': EFFECT_SOURCE,
        'actorId': actor_id,
        'actorName': hero,
        'label': 'An improvised success',
        'success': True,
        'consequence': True,
        'improvised': {'text': fact, 'heroId': actor_id, 'turn': turn_id},
        **({'removesImprovised': removes} if removes else {}),
        'facts': [],
        'locationId': _text(_scene(state).get('locationId')),
        'visible': True,
    }


def _accepted_changes(
    state: Mapping[str, Any],
    *,
    actor_id: str,
    hero: str,
    fact: str,
    answer: Mapping[str, Any],
    turn_id: Any,
) -> tuple[list[dict[str, Any]], str, str]:
    """``(changes, fact kept, effect applied)`` of an accepted fact."""

    removes = _text(answer.get('removes'))
    effect = _text(answer.get('effect')).lower()
    plan = _effect_plan(state, actor_id, effect, _text(answer.get('targetId'))) if effect else None
    resolved = resolve_plan(state, actor_id=actor_id, plan=plan, turn_id=turn_id, checked=False) if plan else None
    if resolved and resolved['ok'] and _dicts(resolved['changes'][0].get('facts')):
        change, *extra = deepcopy(resolved['changes'])
        change[FROM_IMPROVISED] = True
        if removes:
            change['removesImprovised'] = removes
        changes, kept = [change, *extra], _text(change['facts'][0].get('text'))
    else:
        effect = ''
        changes, kept = [_fact_change(state, actor_id=actor_id, hero=hero, fact=fact, removes=removes, turn_id=turn_id)], fact
    about = [_text(value) for value in answer.get('about') or [] if isinstance(value, str)] if isinstance(answer.get('about'), list) else []
    for npc_id in dict.fromkeys(about):
        remembered = conversations.witness_change(dict(state), hero_id=actor_id, npc_id=npc_id, fact=kept, turn_id=turn_id)
        if remembered:
            changes.append(remembered)
    return changes, kept, effect


def improvised_fact(
    state: Mapping[str, Any],
    *,
    actor_id: str,
    attempt: Mapping[str, Any],
    turn_id: Any,
    before_provider_call: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """The fact a successful improvised attempt (``{"words", "check"}``) leaves: ``{"source"`` (``helper`` or
    ``engine``), ``"fact"`` (the one kept, '' when refused), ``"reason"`` (why it was refused), ``"effect"``,
    ``"answer"``, ``"helper"`` (the helper's debug record) and ``"changes"`` to apply ``}``."""

    hero = _text((find_actor(dict(state), actor_id) or {}).get('name'))
    words = _text(attempt.get('words'))
    check = _dict(attempt.get('check'))
    answer, debug = (None, None)
    if helper_enabled():
        answer, debug = helper_answer(state, actor_id=actor_id, hero=hero, words=words, check=check, before_provider_call=before_provider_call)
    outcome: dict[str, Any] = {'source': 'helper' if debug and not debug['helperError'] else 'engine', 'fact': '', 'reason': '', 'effect': ''}
    if debug:
        outcome.update(answer=answer, helper=debug)
    if outcome['source'] == 'engine':
        fact, answer = fallback_fact(hero, words), {}
    elif not isinstance(answer, dict) or answer.get('none') is True:
        fact = ''
        outcome['reason'] = 'none' if isinstance(answer, dict) else 'invalid_answer'
    else:
        fact = answer['fact'].strip() if isinstance(answer.get('fact'), str) else ''
        outcome['reason'] = '' if fact else 'invalid_answer'
    if fact and not outcome['reason']:
        outcome['reason'] = fact_problem(state, actor_id=actor_id, words=words, fact=fact, own=outcome['source'] == 'engine')
    removes = _text((answer or {}).get('removes'))
    if not outcome['reason'] and removes and removes not in {_text(record.get('text')) for record in improvised_facts(state)}:
        outcome['reason'] = 'unknown_removes'
    if outcome['reason']:
        telemetry_event('state_pipeline.improvised_fact.refused', payload={'reason': outcome['reason']})
        return {**outcome, 'changes': []}
    changes, kept, effect = _accepted_changes(state, actor_id=actor_id, hero=hero, fact=fact, answer=answer or {}, turn_id=turn_id)
    return {**outcome, 'fact': kept, 'effect': effect, 'changes': changes}
