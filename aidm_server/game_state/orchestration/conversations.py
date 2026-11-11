"""People you can talk to: conversations, topics, what a hero's words do to a person, and what the person remembers.

Conversations. ``Talk to {Name}``, or words addressed to someone in the hero's room, opens one:
``conversations[heroId] = {"npcId", "sinceTurn", "failed"}`` (the topics whose check failed in it). It is always with
someone in the hero's room and ends on ``Leave it there``, travel, a fight starting, the hero stepping into another
room, or any other action of that hero; two heroes may talk to two people at once. Opening tells the person's
``greeting`` the first time, after that ``{Name} turns to you.``; no ``knows`` line is spent.

Topics. A person's ``topics`` are told once each. One is available to a hero when it is not told, did not fail in
this conversation, its ``requires`` hold (``flags``, an unset flag counting as false; ``clueIds`` the party knows;
``itemIds`` the hero holds; ``topicIds`` this person told; ``encounterResolvedIds``; ``dispositionAtLeast``;
``remembers``, a key this person remembers of this hero; ``anyOf``, a list of such requirements of which one is
enough), the callback that ``offers`` it (if one does) has fired, and, when it is ``private``, no other known person
stands in the hero's room. A conversation offers the first four available, ``ask`` topics (``Ask about {label}``)
before ``say`` ones (their label as written), then ``Say something else`` and ``Leave it there``. A person without
topics has one generated topic, ``Ask what {first name} knows``, telling their next ``knows`` line each time, then that
they have nothing more to add.

A topic's ``check`` is rolled by the engine; its ``dcWhen`` entries (requirements with a ``dc``, or ``noCheck``)
change it, the first that holds deciding. A hero whose threat or offer worked has the next topic with a check told
without it. Success or no check tells ``line`` with ``effects``; failure tells the check's ``failLine`` with its
``failEffects``, and the topic is offered again only in a later conversation.

Effects (topics, reactions, callbacks): ``setsFlags``, ``discoversClueIds``, ``discoversLocationIds``, ``givesItems``
(to the hero), ``disposition`` (±n), ``affects`` (other people: ``{npcId, disposition, line?, when?}``, each only
when that person is known and present), ``promise`` (sets that flag and records the promise), ``keepsPromise`` and
``breaksPromise`` (an authored promise by its id), ``remember`` and ``forget`` (a memory key of this hero).

People. Each known person keeps on their record ``disposition`` (−2…2, :data:`DISPOSITION_WORDS`; the pack's word
until it first changes, anything but one of the five words counting as neutral), ``remembers`` (``{"heroId", "key",
"words"?, "turn"}``, newest last, at most eight) and ``talk`` (``told``, ``knowsTold``, ``greeted``, ``callbacks``,
``reacts``, ``waived``, ``promisedBy``, ``apologyChapter``). Promises are the state's ``promises``.

Reactions to speech acts (recognised in :mod:`aidm_server.game_state.orchestration.speech_acts`): a person's first
``reacts`` entry for the act (and its ``about``, when given) decides, its check rolled like a topic's; a ``promise``
otherwise chooses the available topic that makes a promise, and a ``show`` the available ``say`` topic that requires
the thing shown; failing those, the defaults of :func:`default_reaction`.

Callbacks: a person's ``callbacks`` fire once each, when their event happens (``arrival``, at ``atLocationId`` when
given; ``fight_end``; ``rest``; ``topic:<id>``) while the person is known, present and remembers ``when.remembers``
of a hero (the hero it is told for) and ``when.flags`` hold; the text opens the narration and the topics it
``offers`` become available.

Changes are ``npc.talk`` with an ``operation`` (``open``, ``close``, ``topic``, ``act``, ``callback``, ``affect``),
made only by the engine (source ``conversation``). Applying one changes the conversation and the person; what it
does to the world (flags, clues, places, items, coin, Inspiration) follows as ordinary changes
(:func:`talk_followup_changes`), as do the ``affects`` of scene objects and encounter outcomes and the conversations
ended by travel, a fight or a step into another room.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from aidm_server.combat.rewards import canonical_combat_outcome
from aidm_server.game_state.campaign_pack_encounters import COMPLETED_ENCOUNTERS_FLAG, catalog_record, values_match
from aidm_server.game_state.models import find_actor, stable_change_id
from aidm_server.game_state.orchestration.hero_threads import grant_inspiration
from aidm_server.game_state.zones import ZONE_MOVE_CHANGE, fight_running, hero_zone_id, npc_zone_id, place_zones, present_npcs
from aidm_server.interactables import object_rolls

TALK_CHANGE = 'npc.talk'
TALK_DECLARED = 'npc.talk'
TALK_SOURCE = 'conversation'
TALK_KIND = 'talk'
# The source of the talk intent typed words become (``speech_acts.conversation_words``).
TALK_WORDS_SOURCE = 'conversation_words'
TALK_ACTIONS = frozenset({'open', 'topic', 'leave', 'words'})
CONVERSATIONS_KEY = 'conversations'
PROMISES_KEY = 'promises'
DISPOSITION_WORDS = ('hostile', 'wary', 'neutral', 'warm', 'loyal')
TOPIC_LIMIT = 4
MEMORY_LIMIT = 8
PROMISE_WORDS_LIMIT = 120
KNOWS_TOPIC = 'knows'
SAY_FREE = 'say_free'
LEAVE = 'leave_conversation'
SAY_SOMETHING_ELSE = 'Say something else'
LEAVE_IT_THERE = 'Leave it there'
DEFAULT_DC = 12
NEGATIVE_MEMORIES = frozenset({'threatened', 'caught_lying'})
UNKNOWN_CLUE_STATUSES = frozenset({'hidden', 'undiscovered', 'unknown'})
# Inspiration a conversation earns.
WORD_GIVEN = 'your word given'
WORD_KEPT = 'your word kept'
SECRET_UNCOVERED = 'a secret uncovered'
# How the journal says what a person remembers of a hero.
MEMORY_PHRASES = {
    'threatened': 'you threatened them',
    'caught_lying': 'you were caught lying',
    'claimed_authority': 'you claimed to act with authority',
    'claimed_identity': 'you told them who you are',
    'owned_the_lie': 'you owned the lie',
    'protected': 'you stood over them',
}
CLOSED_BY = {'scene.move_location': 'travel', 'combat.start': 'fight'}


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    return [_text(item) for item in value if _text(item)] if isinstance(value, list) else []


# People ---------------------------------------------------------------------------------------------------------------


def disposition_value(value: Any) -> int:
    """A disposition as a number from −2 (hostile) to 2 (loyal): one of the five words, else neutral."""

    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(-2, min(2, value))
    word = _text(value).lower()
    return DISPOSITION_WORDS.index(word) - 2 if word in DISPOSITION_WORDS else 0


def disposition_word(value: Any) -> str:
    return DISPOSITION_WORDS[disposition_value(value) + 2]


def shown_disposition(record: dict[str, Any]) -> str | None:
    """How a known person's disposition reads to players and the narrator: the word for a pack person or one the
    engine has moved (a number never shows); anyone else's as it was written; None without one."""

    value = record.get('disposition')
    if (isinstance(value, int) and not isinstance(value, bool)) or _text(record.get('source')) == 'campaign_pack':
        return disposition_word(value)
    return _text(value) or None


def person_record(state: dict[str, Any], npc_id: Any) -> dict[str, Any] | None:
    """The known person's record (``knownNpcs`` or ``partyNpcs``), the one the engine changes; None."""

    return next(
        (npc for key in ('knownNpcs', 'partyNpcs') for npc in _dicts(state.get(key)) if _text(npc.get('id')) == _text(npc_id)),
        None,
    )


def authored(state: dict[str, Any], npc_id: Any) -> dict[str, Any]:
    """What the pack wrote for the person: their catalog record, else their known record."""

    return catalog_record(state, 'npcs', npc_id) or person_record(state, npc_id) or {}


def _talk(person: dict[str, Any]) -> dict[str, Any]:
    return person.get('talk') if isinstance(person.get('talk'), dict) else {}


def _talk_for_update(person: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(person.get('talk'), dict):
        person['talk'] = {}
    return person['talk']


def first_name(name: Any) -> str:
    words = _text(name).split()
    return _text(name) if not words or words[0].lower() == 'the' else words[0]


def memories(person: dict[str, Any], hero_id: Any = None) -> list[dict[str, Any]]:
    entries = _dicts(person.get('remembers'))
    return entries if hero_id is None else [entry for entry in entries if _text(entry.get('heroId')) == _text(hero_id)]


def remembers(person: dict[str, Any], hero_id: Any, key: Any) -> bool:
    return any(_text(entry.get('key')) == _text(key) for entry in memories(person, hero_id))


def memory_line(entry: dict[str, Any]) -> str:
    """``you gave your word to bring word of the drivers``: how the journal says one thing a person remembers."""

    key, words = _text(entry.get('key')), _text(entry.get('words'))
    if key == 'promised':
        rest = words[len('Promise '):] if words.lower().startswith('promise ') else ''
        return f'you gave your word {rest}' if rest else f'you gave your word: "{words}"'
    if key == 'shown':
        return f'you showed them the {words}' if words else 'you showed them something'
    return MEMORY_PHRASES.get(key) or key.replace('_', ' ')


def is_present(state: dict[str, Any], npc_id: Any) -> bool:
    return any(_text(npc.get('id')) == _text(npc_id) for npc in present_npcs(state))


def in_room(state: dict[str, Any], hero_id: Any, npc_id: Any) -> bool:
    """The person is known, at the place and, in a place with rooms, in the hero's room."""

    if not is_present(state, npc_id):
        return False
    zones = place_zones(state)
    return not zones or npc_zone_id(state, npc_id, zones) == hero_zone_id(state, hero_id, zones)


def _others_in_room(state: dict[str, Any], hero_id: Any, npc_id: Any) -> bool:
    return any(_text(npc.get('id')) != _text(npc_id) and in_room(state, hero_id, npc.get('id')) for npc in present_npcs(state))


# Conversations --------------------------------------------------------------------------------------------------------


def conversations(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    value = state.get(CONVERSATIONS_KEY)
    return {key: entry for key, entry in value.items() if isinstance(entry, dict)} if isinstance(value, dict) else {}


def conversation_of(state: dict[str, Any], hero_id: Any) -> dict[str, Any] | None:
    return conversations(state).get(_text(hero_id))


def talk_intent(action_intent: Any) -> dict[str, Any] | None:
    """The ``talk`` payload of a conversation intent (``{"action", "npc_id", "topic_id"?}``); None for any other."""

    if not isinstance(action_intent, dict) or _text(action_intent.get('kind')) != TALK_KIND:
        return None
    talk = action_intent.get('talk')
    return talk if isinstance(talk, dict) else None


def leaving(action_intent: Any) -> bool:
    return _text((talk_intent(action_intent) or {}).get('action')) == 'leave'


# Topics ---------------------------------------------------------------------------------------------------------------


def _topic(raw: dict[str, Any]) -> dict[str, Any]:
    topic = deepcopy(raw)
    topic['kind'] = 'say' if _text(raw.get('kind')) == 'say' else 'ask'
    return topic


def knows_lines(state: dict[str, Any], npc_id: Any) -> list[str]:
    return _strings(authored(state, npc_id).get('knows'))


def topics(state: dict[str, Any], npc_id: Any) -> list[dict[str, Any]]:
    """The person's topics, ``ask`` before ``say`` in authored order; one generated topic for someone without any."""

    record = authored(state, npc_id)
    written = [_topic(topic) for topic in _dicts(record.get('topics')) if _text(topic.get('id')) and _text(topic.get('label'))]
    if written:
        return [*(topic for topic in written if topic['kind'] == 'ask'), *(topic for topic in written if topic['kind'] == 'say')]
    if not knows_lines(state, npc_id):
        return []
    return [{'id': KNOWS_TOPIC, 'label': f"Ask what {first_name(record.get('name'))} knows", 'kind': 'say', 'keywords': ['know', 'knows'], 'generated': True}]


def topic_by_id(state: dict[str, Any], npc_id: Any, topic_id: Any) -> dict[str, Any] | None:
    return next((topic for topic in topics(state, npc_id) if _text(topic.get('id')) == _text(topic_id)), None)


def offered_label(topic: dict[str, Any]) -> str:
    return f"Ask about {_text(topic.get('label'))}" if topic.get('kind') == 'ask' else _text(topic.get('label'))


def _clue_known(state: dict[str, Any], clue_id: str) -> bool:
    return any(
        _text(clue.get('id')) == clue_id and _text(clue.get('status')).lower() not in UNKNOWN_CLUE_STATUSES
        for clue in _dicts(state.get('clues'))
    )


def hero_items(state: dict[str, Any], hero_id: Any) -> list[dict[str, Any]]:
    hero = find_actor(state, hero_id) or {}
    inventory = hero.get('inventory') if isinstance(hero.get('inventory'), dict) else {}
    return _dicts(inventory.get('items'))


def holds(state: dict[str, Any], hero_id: Any, item_id: Any) -> bool:
    return any(_text(item_id) in {_text(item.get('id')), _text(item.get('sourceItemId'))} for item in hero_items(state, hero_id))


def _flag_holds(flags: dict[str, Any], key: str, expected: Any) -> bool:
    if key not in flags:
        return expected is False
    return values_match(flags[key], expected)


def requirement_holds(state: dict[str, Any], hero_id: Any, npc_id: Any, requires: Any) -> bool:
    """Whether a ``requires`` (or ``dcWhen`` entry) holds for this hero and person (keys in the module docstring)."""

    if not isinstance(requires, dict):
        return True
    flags = state.get('flags') if isinstance(state.get('flags'), dict) else {}
    person = person_record(state, npc_id) or {}
    completed = set(_strings(flags.get(COMPLETED_ENCOUNTERS_FLAG)))
    told = set(_strings(_talk(person).get('told')))
    floor = requires.get('dispositionAtLeast')
    return (
        all(_flag_holds(flags, _text(key), value) for key, value in (requires.get('flags') or {}).items())
        and all(_clue_known(state, clue_id) for clue_id in _strings(requires.get('clueIds')))
        and all(holds(state, hero_id, item_id) for item_id in _strings(requires.get('itemIds')))
        and told.issuperset(_strings(requires.get('topicIds')))
        and completed.issuperset(_strings(requires.get('encounterResolvedIds')))
        and (not isinstance(floor, int) or disposition_value(person.get('disposition')) >= floor)
        and (not _text(requires.get('remembers')) or remembers(person, hero_id, requires['remembers']))
        and (not isinstance(requires.get('anyOf'), list) or any(requirement_holds(state, hero_id, npc_id, entry) for entry in _dicts(requires['anyOf'])))
    )


def _offered_by_callback(state: dict[str, Any], npc_id: Any, topic_id: str) -> bool:
    """A topic a callback ``offers`` waits for that callback; any other is not held back."""

    fired = set(_strings(_talk(person_record(state, npc_id) or {}).get('callbacks')))
    openers = [_text(callback.get('id')) for callback in _dicts(authored(state, npc_id).get('callbacks')) if topic_id in _strings(callback.get('offers'))]
    return not openers or any(callback_id in fired for callback_id in openers)


def available_topics(state: dict[str, Any], hero_id: Any, npc_id: Any) -> list[dict[str, Any]]:
    """Every topic available to the hero with this person now, in the order they are offered."""

    person = person_record(state, npc_id) or {}
    talk = _talk(person)
    told = set(_strings(talk.get('told')))
    failed = set(_strings((conversation_of(state, hero_id) or {}).get('failed')))
    available = []
    for topic in topics(state, npc_id):
        topic_id = _text(topic.get('id'))
        if topic.get('generated'):
            available.append(topic)
            continue
        if topic_id in told or topic_id in failed or not _offered_by_callback(state, npc_id, topic_id):
            continue
        # W9-SCENE: a person cut off from the scene (``separate``) may hear a private topic among others.
        if topic.get('private') is True and _others_in_room(state, hero_id, npc_id):
            continue
        if requirement_holds(state, hero_id, npc_id, topic.get('requires')):
            available.append(topic)
    return available


def conversation_actions(state: dict[str, Any], hero_id: Any) -> list[dict[str, Any]]:
    """What a hero in a conversation is offered: up to four topics, ``Say something else``, ``Leave it there``."""

    conversation = conversation_of(state, hero_id)
    if conversation is None or fight_running(state):
        return []
    npc_id = _text(conversation.get('npcId'))
    offered = [
        {'kind': 'topic', 'id': _text(topic.get('id')), 'label': offered_label(topic), 'npcId': npc_id}
        for topic in available_topics(state, hero_id, npc_id)[:TOPIC_LIMIT]
    ]
    return [*offered, {'kind': SAY_FREE, 'label': SAY_SOMETHING_ELSE, 'npcId': npc_id}, {'kind': LEAVE, 'label': LEAVE_IT_THERE, 'npcId': npc_id}]


def conversation_view(state: dict[str, Any], hero_id: Any) -> dict[str, Any] | None:
    """The viewer's conversation for the Story view: who with, their journal one-liner, and the offered actions."""

    actions = conversation_actions(state, hero_id)
    if not actions:
        return None
    npc_id = actions[0]['npcId']
    person = person_record(state, npc_id) or {}
    record = authored(state, npc_id)
    return {
        'npcId': npc_id,
        'name': _text(person.get('name') or record.get('name')),
        'summary': _text(record.get('playerSummary') or person.get('playerSummary')),
        'actions': [{key: value for key, value in action.items() if key != 'npcId'} for action in actions],
    }


# Checks ---------------------------------------------------------------------------------------------------------------


def check_dc(state: dict[str, Any], hero_id: Any, npc_id: Any, check: dict[str, Any]) -> int | None:
    """The check's DC for this hero, or None when one of its ``dcWhen`` entries says no check."""

    for entry in _dicts(check.get('dcWhen')):
        if requirement_holds(state, hero_id, npc_id, entry):
            return None if entry.get('noCheck') is True else int(entry.get('dc') or DEFAULT_DC)
    return int(check.get('dc') or DEFAULT_DC)


def roll_check(state: dict[str, Any], hero_id: Any, check: dict[str, Any], dc: int, *, seed: str) -> dict[str, Any]:
    """The engine's roll of a conversation check (as an object check is rolled)."""

    spec = {'skill': check.get('skill'), 'ability': check.get('ability'), 'dc': dc}
    return object_rolls(state)['check'](hero_id, spec, seed)


def _waived(person: dict[str, Any], hero_id: Any) -> bool:
    return _text(hero_id) in _strings(_talk(person).get('waived'))


# Changes --------------------------------------------------------------------------------------------------------------


def _change(turn_id: Any, operation: str, hero_id: Any, npc_id: Any, *parts: Any, **fields: Any) -> dict[str, Any]:
    return {
        'id': stable_change_id(turn_id, TALK_CHANGE, operation, hero_id, npc_id, *parts),
        'turnId': turn_id,
        'type': TALK_CHANGE,
        'source': TALK_SOURCE,
        'operation': operation,
        **({'actorId': _text(hero_id)} if _text(hero_id) else {}),
        **({'npcId': _text(npc_id)} if _text(npc_id) else {}),
        **fields,
        'visible': False,
    }


def open_change(state: dict[str, Any], hero_id: Any, npc_id: Any, *, turn_id: Any) -> dict[str, Any]:
    person = person_record(state, npc_id) or {}
    greeting = '' if _talk(person).get('greeted') else _text(authored(state, npc_id).get('greeting'))
    return _change(turn_id, 'open', hero_id, npc_id, npcName=_text(person.get('name')), greeting=greeting)


def close_change(state: dict[str, Any], hero_id: Any, *, turn_id: Any, reason: str) -> dict[str, Any] | None:
    conversation = conversation_of(state, hero_id)
    if conversation is None:
        return None
    npc_id = _text(conversation.get('npcId'))
    name = _text((person_record(state, npc_id) or {}).get('name'))
    return _change(turn_id, 'close', hero_id, npc_id, reason, npcName=name, reason=reason)


def topic_change(state: dict[str, Any], hero_id: Any, npc_id: Any, topic: dict[str, Any], *, turn_id: Any) -> dict[str, Any]:
    """A topic chosen: its check rolled (unless waived or none), then its line and effects, or its failure."""

    person = person_record(state, npc_id) or {}
    topic_id = _text(topic.get('id'))
    base = {'npcName': _text(person.get('name')), 'topicId': topic_id, 'label': _text(topic.get('label')), 'kind': topic.get('kind')}
    if topic.get('generated'):
        index = int(_talk(person).get('knowsTold') or 0)
        lines = knows_lines(state, npc_id)
        # Once every line is told the person has nothing more to add.
        return _change(turn_id, 'topic', hero_id, npc_id, topic_id, index, **base, outcome='told', line=lines[index] if index < len(lines) else '', knows=True, effects={})
    check = topic.get('check') if isinstance(topic.get('check'), dict) else None
    dc = check_dc(state, hero_id, npc_id, check) if check else None
    waived = dc is not None and _waived(person, hero_id)
    rolled = roll_check(state, hero_id, check, dc, seed=f"talk:{state.get('sessionId')}:{turn_id}:{npc_id}:{topic_id}") if dc is not None and not waived else None
    failed = rolled is not None and rolled.get('passed') is not True
    effects = deepcopy((check or {}).get('failEffects') if failed else topic.get('effects')) or {}
    if waived:
        effects['waiveUsed'] = True
    secret = not failed and (check is not None or isinstance((topic.get('requires') or {}).get('dispositionAtLeast'), int))
    inspiration = [] if failed else [*([SECRET_UNCOVERED] if secret else []), *_strings([topic.get('inspires')])]
    return _change(
        turn_id, 'topic', hero_id, npc_id, topic_id,
        **base,
        outcome='failed' if failed else 'told',
        line=_text((check or {}).get('failLine') if failed else topic.get('line')),
        **({'check': rolled} if rolled else {}),
        **({'waived': True} if waived else {}),
        effects=effects,
        **({'inspiration': inspiration} if inspiration else {}),
    )


def act_change(state: dict[str, Any], hero_id: Any, npc_id: Any, reaction: dict[str, Any], *, turn_id: Any) -> dict[str, Any]:
    person = person_record(state, npc_id) or {}
    return _change(
        turn_id, 'act', hero_id, npc_id, reaction['act'], reaction.get('about'),
        npcName=_text(person.get('name')),
        **{key: value for key, value in reaction.items() if value not in (None, '', {}, [])},
    )


# Reactions ------------------------------------------------------------------------------------------------------------


def _reaction_from(state: dict[str, Any], hero_id: Any, npc_id: Any, spec: dict[str, Any], *, act: str, turn_id: Any, seed: str) -> dict[str, Any]:
    check = spec.get('check') if isinstance(spec.get('check'), dict) else None
    dc = check_dc(state, hero_id, npc_id, check) if check else None
    rolled = roll_check(state, hero_id, check, dc, seed=seed) if dc is not None else None
    failed = rolled is not None and rolled.get('passed') is not True
    return {
        'act': act,
        'outcome': 'failed' if failed else 'worked',
        'line': _text(spec.get('failLine') if failed else spec.get('line')),
        'check': rolled,
        'effects': deepcopy(spec.get('failEffects') if failed else spec.get('effects')) or {},
    }


def authored_reaction(state: dict[str, Any], npc_id: Any, act: str, about: Any) -> tuple[int, dict[str, Any]] | None:
    """The person's first ``reacts`` entry for the act (and its ``about``) not spent by ``once``."""

    used = set(_talk(person_record(state, npc_id) or {}).get('reacts') or [])
    for index, entry in enumerate(_dicts(authored(state, npc_id).get('reacts'))):
        if _text(entry.get('act')) != act or (entry.get('once') is True and index in used):
            continue
        if _text(entry.get('about')) and _text(entry.get('about')) != _text(about):
            continue
        return index, entry
    return None


def topic_for_act(state: dict[str, Any], hero_id: Any, npc_id: Any, act: str, about: Any) -> dict[str, Any] | None:
    """The available topic a promise or a show reaches: one that makes a promise; a ``say`` topic requiring the thing."""

    def names(requires: Any) -> set[str]:
        if not isinstance(requires, dict):
            return set()
        found = {*_strings(requires.get('clueIds')), *_strings(requires.get('itemIds'))}
        return found.union(*(names(entry) for entry in _dicts(requires.get('anyOf'))))

    for topic in available_topics(state, hero_id, npc_id):
        if topic.get('generated'):
            continue
        if act == 'promise' and _text((topic.get('effects') or {}).get('promise')):
            return topic
        if act == 'show' and topic.get('kind') == 'say' and _text(about) in names(topic.get('requires')):
            return topic
    return None


def _thing_name(state: dict[str, Any], hero_id: Any, thing_id: Any) -> str:
    item = next((item for item in hero_items(state, hero_id) if _text(thing_id) in {_text(item.get('id')), _text(item.get('sourceItemId'))}), None)
    if item:
        return _text(item.get('name'))
    clue = next((clue for clue in _dicts(state.get('clues')) if _text(clue.get('id')) == _text(thing_id)), {})
    return _text(clue.get('title') or clue.get('name'))


def _coin_gp(state: dict[str, Any], hero_id: Any) -> int:
    hero = find_actor(state, hero_id) or {}
    currency = (hero.get('inventory') or {}).get('currency') if isinstance(hero.get('inventory'), dict) else {}
    return int((currency or {}).get('gp') or 0)


def default_reaction(
    state: dict[str, Any],
    hero_id: Any,
    npc_id: Any,
    act: str,
    *,
    about: Any = None,
    words: str = '',
    coin: float = 0,
    turn_id: Any,
) -> dict[str, Any] | None:
    """What a person does with an act nobody wrote a reaction for; None when it does nothing (free speech).

    promise: remembered with the hero's words (at most 120 characters), +1 the first time this hero promises them,
    and listed under the hero's word until kept or broken. show: +1 and remembered when the person cares about it
    (``caresAbout``), else ``{Name} looks, and shrugs.``. threaten: Intimidation against 10 + 2 × disposition; on
    success the next topic with a check is told without it; either way −1 and remembered. offer: when it meets one
    of the person's ``wants`` (an item id or ``coin:<gp>``) the coin or item changes hands, +1, and the next topic with
    a check is told without it; else ``{Name} is not for sale.``. claim: Deception against 12 unless a ``believes``
    entry for the claim holds (``ifFlags``, ``ifItems``); believed or backed, +1 and remembered; else −1 and
    remembered as caught lying. apologise: forgets the most recent bad memory of this hero and restores 1, once per
    person per chapter.
    """

    person = person_record(state, npc_id) or {}
    record = authored(state, npc_id)
    disposition = disposition_value(person.get('disposition'))
    talk = _talk(person)
    seed = f"talk:{state.get('sessionId')}:{turn_id}:{npc_id}:{act}"
    if act == 'promise':
        text = _text(words)[:PROMISE_WORDS_LIMIT]
        first = _text(hero_id) not in _strings(talk.get('promisedBy'))
        return {'act': act, 'outcome': 'default', 'effects': {'remember': 'promised', 'rememberWords': text, 'promiseWords': text, **({'disposition': 1} if first else {})}}
    if act == 'show':
        name = _thing_name(state, hero_id, about)
        cares = _text(about) in _strings(record.get('caresAbout'))
        shown_before = any(_text(entry.get('key')) == 'shown' and _text(entry.get('words')) == name for entry in memories(person, hero_id))
        if cares and not shown_before:
            return {'act': act, 'about': _text(about), 'outcome': 'cares', 'effects': {'disposition': 1, 'remember': 'shown', 'rememberWords': name}}
        return {'act': act, 'about': _text(about), 'outcome': 'shrugs', 'line': f"{_text(person.get('name'))} looks, and shrugs.", 'effects': {}}
    if act == 'threaten':
        rolled = roll_check(state, hero_id, {'skill': 'intimidation', 'ability': 'charisma'}, 10 + 2 * disposition, seed=seed)
        worked = rolled.get('passed') is True
        return {'act': act, 'outcome': 'worked' if worked else 'failed', 'check': rolled, 'effects': {'disposition': -1, 'remember': 'threatened', **({'waiveNextCheck': True} if worked else {})}}
    if act == 'offer':
        wants = _strings(record.get('wants'))
        item = _text(about) if _text(about) in wants and holds(state, hero_id, about) else ''
        price = next((int(want.split(':', 1)[1]) for want in wants if want.startswith('coin:') and want.split(':', 1)[1].isdigit()), None)
        paid = price is not None and coin >= price and _coin_gp(state, hero_id) >= price
        if not item and not paid:
            return {'act': act, 'outcome': 'refused', 'line': f"{_text(person.get('name'))} is not for sale.", 'effects': {}}
        takes = {'itemId': item} if item else {'gp': price}
        return {'act': act, **({'about': item} if item else {}), 'outcome': 'accepted', 'effects': {'disposition': 1, 'waiveNextCheck': True, 'takes': takes}}
    if act == 'claim':
        about = _text(about) or 'identity'
        backed = any(
            _text(belief.get('claim')) == about
            and (not isinstance(belief.get('ifFlags'), dict) or requirement_holds(state, hero_id, npc_id, {'flags': belief['ifFlags']}))
            and all(holds(state, hero_id, item_id) for item_id in _strings(belief.get('ifItems')))
            for belief in _dicts(record.get('believes'))
        )
        rolled = None if backed else roll_check(state, hero_id, {'skill': 'deception', 'ability': 'charisma'}, DEFAULT_DC, seed=seed)
        if backed or (rolled or {}).get('passed') is True:
            return {'act': act, 'about': about, 'outcome': 'believed', 'check': rolled, 'effects': {'disposition': 1, 'remember': f'claimed_{about}'}}
        return {'act': act, 'about': about, 'outcome': 'disbelieved', 'check': rolled, 'effects': {'disposition': -1, 'remember': 'caught_lying'}}
    if act == 'apologise':
        bad = [entry for entry in memories(person, hero_id) if _text(entry.get('key')) in NEGATIVE_MEMORIES]
        if not bad or talk.get('apologyChapter') == state.get('sessionId'):
            return None
        return {'act': act, 'outcome': 'forgiven', 'effects': {'disposition': 1, 'forgetNegative': True}}
    return None


def reaction(
    state: dict[str, Any],
    hero_id: Any,
    npc_id: Any,
    act: str,
    *,
    about: Any = None,
    words: str = '',
    coin: float = 0,
    turn_id: Any,
) -> dict[str, Any] | None:
    """The act's result with this person: an authored reaction, else the defaults; None when it changes nothing."""

    found = authored_reaction(state, npc_id, act, about)
    if found is not None:
        index, spec = found
        seed = f"talk:{state.get('sessionId')}:{turn_id}:{npc_id}:react:{index}"
        return {**_reaction_from(state, hero_id, npc_id, spec, act=act, turn_id=turn_id, seed=seed), 'about': _text(about), 'reactIndex': index}
    return default_reaction(state, hero_id, npc_id, act, about=about, words=words, coin=coin, turn_id=turn_id)


# Application ----------------------------------------------------------------------------------------------------------


def talk_change_problem(state: dict[str, Any], change: dict[str, Any]) -> str:
    """Why an ``npc.talk`` change cannot apply; '' when it can."""

    if _text(change.get('source')) != TALK_SOURCE:
        return 'Only the engine holds conversations.'
    operation = _text(change.get('operation'))
    if operation in {'open', 'close', 'topic', 'act'} and find_actor(state, change.get('actorId')) is None:
        return 'Hero not found.'
    if operation != 'close' and person_record(state, change.get('npcId')) is None:
        return 'That person is not known.'
    if operation == 'close' and conversation_of(state, change.get('actorId')) is None:
        return 'There is no conversation to end.'
    if operation not in {'open', 'close', 'topic', 'act', 'callback', 'affect'}:
        return 'Unknown conversation operation.'
    return ''


def _remember(state: dict[str, Any], person: dict[str, Any], hero_id: Any, key: str, *, words: str = '', turn_id: Any = None) -> None:
    entry = {'heroId': _text(hero_id), 'key': key, **({'words': words} if words else {}), 'turn': turn_id}
    person['remembers'] = [*memories(person), entry][-MEMORY_LIMIT:]


def _forget(person: dict[str, Any], hero_id: Any, keys: Iterable[str]) -> bool:
    wanted = set(keys)
    entries = memories(person)
    for index in range(len(entries) - 1, -1, -1):
        if _text(entries[index].get('heroId')) == _text(hero_id) and _text(entries[index].get('key')) in wanted:
            person['remembers'] = [*entries[:index], *entries[index + 1:]]
            return True
    return False


def _shift(person: dict[str, Any], delta: int) -> int:
    before = disposition_value(person.get('disposition'))
    after = max(-2, min(2, before + delta))
    person['disposition'] = after
    return after - before


def _bond(person: dict[str, Any], delta: int, line: str = '') -> dict[str, Any] | None:
    moved = _shift(person, delta)
    if not moved:
        return None
    return {'npcId': _text(person.get('id')), 'name': _text(person.get('name')), 'delta': moved, **({'line': line} if line else {})}


def promises(state: dict[str, Any]) -> list[dict[str, Any]]:
    return _dicts(state.get(PROMISES_KEY))


def _affect_holds(state: dict[str, Any], entry: dict[str, Any]) -> bool:
    when = entry.get('when')
    if not isinstance(when, dict):
        return True
    flags = state.get('flags') if isinstance(state.get('flags'), dict) else {}
    return _flag_holds(flags, _text(when.get('flagKey')), when.get('equals', True))


def _apply_effects(state: dict[str, Any], person: dict[str, Any], change: dict[str, Any], effects: dict[str, Any]) -> dict[str, Any]:
    """What the effects do to this person, others and the hero's word; returns what the applied change records."""

    hero_id = _text(change.get('actorId') or change.get('heroId'))
    turn_id = change.get('turnId')
    talk = _talk_for_update(person)
    recorded: dict[str, Any] = {}
    bonds = []
    if isinstance(effects.get('disposition'), int) and (bond := _bond(person, effects['disposition'], _text(effects.get('line')))):
        bonds.append(bond)
    for entry in _dicts(effects.get('affects')):
        other = person_record(state, entry.get('npcId'))
        if other is None or not is_present(state, entry.get('npcId')) or not _affect_holds(state, entry):
            continue
        if isinstance(entry.get('disposition'), int) and (bond := _bond(other, entry['disposition'], _text(entry.get('line')))):
            bonds.append(bond)
    if bonds:
        recorded['bonds'] = bonds
    if _text(effects.get('forget')):
        _forget(person, hero_id, [_text(effects['forget'])])
    if effects.get('forgetNegative') is True:
        _forget(person, hero_id, NEGATIVE_MEMORIES)
        talk['apologyChapter'] = state.get('sessionId')
    if _text(effects.get('remember')):
        _remember(state, person, hero_id, _text(effects['remember']), words=_text(effects.get('rememberWords')), turn_id=turn_id)
    if effects.get('waiveNextCheck') is True:
        talk['waived'] = list(dict.fromkeys([*_strings(talk.get('waived')), hero_id]))
    if effects.get('waiveUsed') is True:
        talk['waived'] = [value for value in _strings(talk.get('waived')) if value != hero_id]
    made = _text(effects.get('promise'))
    words = _text(effects.get('promiseWords'))
    if made or words:
        text = _text(change.get('label')) if made else words
        first = hero_id not in _strings(talk.get('promisedBy'))
        record = {'id': made or _text(change.get('id')), 'heroId': hero_id, 'npcId': _text(person.get('id')), 'text': text, 'authored': bool(made), 'status': 'open', 'turn': turn_id}
        state[PROMISES_KEY] = [*promises(state), record]
        talk['promisedBy'] = list(dict.fromkeys([*_strings(talk.get('promisedBy')), hero_id]))
        # Inspiration for the word given: an authored promise, or the first a hero gives this person.
        recorded['promiseMade'] = {'text': text, 'authored': bool(made), **({'first': True} if made or first else {})}
        if made:
            _remember(state, person, hero_id, 'promised', words=text, turn_id=turn_id)
    for key, status in (('keepsPromise', 'kept'), ('breaksPromise', 'broken')):
        promise_id = _text(effects.get(key))
        for promise in promises(state):
            if promise_id and _text(promise.get('id')) == promise_id and promise.get('status') == 'open':
                promise['status'] = status
                recorded['promise' + status.capitalize()] = {'text': _text(promise.get('text')), 'heroId': _text(promise.get('heroId'))}
    return recorded


def apply_talk_change(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    """Apply an ``npc.talk`` change; the applied change's extra fields, or None when it no longer applies."""

    if talk_change_problem(state, change):
        return None
    operation = _text(change.get('operation'))
    hero_id = _text(change.get('actorId'))
    npc_id = _text(change.get('npcId'))
    if operation == 'close':
        state[CONVERSATIONS_KEY] = {key: value for key, value in conversations(state).items() if key != hero_id}
        return {}
    person = person_record(state, npc_id) or {}
    talk = _talk_for_update(person)
    if operation == 'open':
        state[CONVERSATIONS_KEY] = {**conversations(state), hero_id: {'npcId': npc_id, 'sinceTurn': change.get('turnId'), 'failed': []}}
        if _text(change.get('greeting')):
            talk['greeted'] = True
        return {}
    if operation == 'topic':
        if change.get('knows') is True:
            talk['knowsTold'] = min(int(talk.get('knowsTold') or 0) + 1, len(knows_lines(state, npc_id)))
        elif change.get('outcome') == 'failed':
            conversation = conversation_of(state, hero_id)
            if conversation is not None:
                conversation['failed'] = [*_strings(conversation.get('failed')), _text(change.get('topicId'))]
        else:
            talk['told'] = [*_strings(talk.get('told')), _text(change.get('topicId'))]
    elif operation == 'act' and isinstance(change.get('reactIndex'), int):
        talk['reacts'] = list(dict.fromkeys([*(talk.get('reacts') or []), change['reactIndex']]))
    elif operation == 'callback':
        talk['callbacks'] = [*_strings(talk.get('callbacks')), _text(change.get('callbackId'))]
    effects = change.get('effects') if isinstance(change.get('effects'), dict) else {}
    if operation == 'affect':
        effects = {'disposition': change.get('delta'), 'line': _text(change.get('line'))}
    return _apply_effects(state, person, change, effects)


# What follows from an applied change -----------------------------------------------------------------------------


def _world_changes(state: dict[str, Any], applied: dict[str, Any]) -> list[dict[str, Any]]:
    """The flags, clues, places, items, coin and Inspiration an applied ``npc.talk`` change brings."""

    effects = applied.get('effects') if isinstance(applied.get('effects'), dict) else {}
    hero_id = _text(applied.get('actorId') or applied.get('heroId'))
    parent = _text(applied.get('id'))
    base = {'turnId': applied.get('turnId'), 'source': TALK_SOURCE}
    reason = f"{_text(applied.get('npcName')) or 'A conversation'}: {_text(applied.get('label')) or _text(applied.get('act')) or 'talk'}."
    changes: list[dict[str, Any]] = []
    flags = dict(effects.get('setsFlags') or {})
    if _text(effects.get('promise')):
        flags[_text(effects['promise'])] = True
    for key, value in flags.items():
        changes.append({**base, 'id': stable_change_id(parent, 'flag', key), 'type': 'flag.set', 'flagKey': _text(key), 'flagValue': deepcopy(value), 'visible': False, 'reason': reason})
    for clue_id in _strings(effects.get('discoversClueIds')):
        changes.append({**base, 'id': stable_change_id(parent, 'clue', clue_id), 'type': 'clue.discover', 'clueId': clue_id, 'visible': True, 'reason': reason})
    for location_id in _strings(effects.get('discoversLocationIds')):
        changes.append({**base, 'id': stable_change_id(parent, 'location', location_id), 'type': 'location.discover', 'locationId': location_id, 'status': 'discovered', 'visible': True, 'reason': reason})
    pack = state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}
    for item in _dicts(effects.get('givesItems')) if hero_id else []:
        given = {**deepcopy(item), 'quantity': max(1, int(item.get('quantity') or 1)), 'source': 'campaign_pack', 'packId': _text(pack.get('packId'))}
        changes.append({**base, 'id': stable_change_id(parent, 'item', item.get('id')), 'type': 'inventory.add', 'actorId': hero_id, 'item': given, 'quantity': given['quantity'], 'visible': True, 'reason': reason})
    takes = effects.get('takes') if isinstance(effects.get('takes'), dict) else {}
    if hero_id and isinstance(takes.get('gp'), int):
        changes.append({**base, 'id': stable_change_id(parent, 'paid'), 'type': 'currency.remove', 'actorId': hero_id, 'currency': 'gp', 'amount': takes['gp'], 'visible': True, 'reason': reason})
    if hero_id and _text(takes.get('itemId')):
        item = next((item for item in hero_items(state, hero_id) if _text(takes['itemId']) in {_text(item.get('id')), _text(item.get('sourceItemId'))}), {})
        changes.append({**base, 'id': stable_change_id(parent, 'given'), 'type': 'inventory.remove', 'actorId': hero_id, 'itemId': _text(item.get('id')), 'itemName': _text(item.get('name')), 'quantity': 1, 'visible': True, 'reason': reason})
    reasons = [*_strings(applied.get('inspiration'))]
    if (applied.get('promiseMade') or {}).get('first') is True:
        reasons.append(WORD_GIVEN)
    if applied.get('promiseKept'):
        reasons.append(WORD_KEPT)
    for reason_text in reasons:
        if hero_id and (grant := grant_inspiration(state, hero_id, reason_text, turn_id=applied.get('turnId'))):
            changes.append(grant)
            break
    return changes


def _affect_change(state: dict[str, Any], parent: dict[str, Any], entry: dict[str, Any], index: int) -> dict[str, Any] | None:
    npc_id = _text(entry.get('npcId'))
    if person_record(state, npc_id) is None or not is_present(state, npc_id) or not _affect_holds(state, entry) or not isinstance(entry.get('disposition'), int):
        return None
    return _change(
        parent.get('turnId'), 'affect', '', npc_id, parent.get('id'), index,
        heroId=_text(parent.get('actorId')),
        delta=entry['disposition'],
        **({'line': _text(entry['line'])} if _text(entry.get('line')) else {}),
    )


def _scene_object(state: dict[str, Any], target_id: Any) -> dict[str, Any]:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    return next((entry for key in ('interactables', 'hazards') for entry in _dicts(scene.get(key)) if _text(entry.get('id')) == _text(target_id)), {})


def _affects_of(state: dict[str, Any], applied: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``affects`` an applied object action (when it succeeded) or encounter outcome carries."""

    change_type = _text(applied.get('type'))
    if change_type == 'scene.interactable.action':
        event = applied.get('event') if isinstance(applied.get('event'), dict) else {}
        if _text(event.get('type')).endswith('.check_failed'):
            return []
        affects = _scene_object(state, applied.get('targetId')).get('affects')
        return _dicts(affects.get(_text(applied.get('objectAction')).lower())) if isinstance(affects, dict) else []
    if change_type == 'combat.end':
        combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
        flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
        encounter_id, outcome = flags.get('campaignPackEncounterId'), canonical_combat_outcome(applied.get('endReason') or flags.get('endReason'))
    elif change_type == 'encounter.resolve':
        encounter_id, outcome = applied.get('encounterId'), _text(applied.get('outcome'))
    else:
        return []
    outcomes = catalog_record(state, 'encounters', encounter_id).get('outcomes') if _text(encounter_id) else None
    spec = outcomes.get(_text(outcome)) if isinstance(outcomes, dict) else None
    return _dicts(spec.get('affects')) if isinstance(spec, dict) else []


def _callback_due(state: dict[str, Any], callback: dict[str, Any], applied: dict[str, Any]) -> bool:
    when = callback.get('when') if isinstance(callback.get('when'), dict) else {}
    event = _text(when.get('on'))
    change_type = _text(applied.get('type'))
    if event == 'arrival':
        happened = change_type == 'scene.move_location' and (not _text(when.get('atLocationId')) or _text(applied.get('locationId')) == _text(when['atLocationId']))
    elif event == 'fight_end':
        happened = change_type == 'combat.end'
    elif event == 'rest':
        happened = change_type == 'rest.complete'
    elif event.startswith('topic:'):
        happened = change_type == TALK_CHANGE and applied.get('operation') == 'topic' and applied.get('outcome') == 'told' and _text(applied.get('topicId')) == event[len('topic:'):]
    else:
        happened = False
    flags = state.get('flags') if isinstance(state.get('flags'), dict) else {}
    return happened and all(_flag_holds(flags, _text(key), value) for key, value in (when.get('flags') or {}).items())


def _callback_changes(state: dict[str, Any], applied: dict[str, Any]) -> list[dict[str, Any]]:
    changes = []
    for npc in present_npcs(state):
        npc_id = _text(npc.get('id'))
        person = person_record(state, npc_id) or {}
        fired = set(_strings(_talk(person).get('callbacks')))
        for callback in _dicts(authored(state, npc_id).get('callbacks')):
            callback_id = _text(callback.get('id'))
            key = _text((callback.get('when') or {}).get('remembers')) if isinstance(callback.get('when'), dict) else ''
            entry = next((entry for entry in reversed(memories(person)) if _text(entry.get('key')) == key), None) if key else None
            if not callback_id or callback_id in fired or entry is None or not _callback_due(state, callback, applied):
                continue
            changes.append(
                _change(
                    applied.get('turnId'), 'callback', '', npc_id, callback_id,
                    heroId=_text(entry.get('heroId')),
                    npcName=_text(person.get('name')),
                    callbackId=callback_id,
                    text=_text(callback.get('text')),
                    effects=deepcopy(callback.get('effects')) if isinstance(callback.get('effects'), dict) else {},
                )
            )
    return changes


def _closed_conversations(state: dict[str, Any], applied: dict[str, Any]) -> list[dict[str, Any]]:
    """Travel and a fight end every conversation; a step into another room ends that hero's."""

    change_type = _text(applied.get('type'))
    if change_type in CLOSED_BY:
        heroes = list(conversations(state))
        reason = CLOSED_BY[change_type]
    elif change_type == ZONE_MOVE_CHANGE:
        hero_id = _text(applied.get('actorId'))
        conversation = conversation_of(state, hero_id)
        heroes = [hero_id] if conversation is not None and not in_room(state, hero_id, conversation.get('npcId')) else []
        reason = 'left'
    else:
        return []
    return [change for hero_id in heroes if (change := close_change(state, hero_id, turn_id=applied.get('turnId'), reason=reason))]


def talk_followup_changes(state: dict[str, Any], applied: dict[str, Any]) -> list[dict[str, Any]]:
    """The changes that follow one applied change (see the module docstring)."""

    changes = _world_changes(state, applied) if _text(applied.get('type')) == TALK_CHANGE else []
    changes.extend(change for index, entry in enumerate(_affects_of(state, applied)) if (change := _affect_change(state, applied, entry, index)))
    changes.extend(_closed_conversations(state, applied))
    changes.extend(_callback_changes(state, applied))
    return changes


# A conversation turn ---------------------------------------------------------------------------------------------


def projected_conversation(state: dict[str, Any], hero_id: str, npc_id: str, *, opening: bool) -> dict[str, Any]:
    """The state as it reads once the hero is talking with this person (a fresh conversation when one opens)."""

    if not opening:
        return state
    projected = deepcopy(state)
    projected[CONVERSATIONS_KEY] = {**conversations(projected), hero_id: {'npcId': npc_id, 'failed': []}}
    return projected


def validate_talk(state: dict[str, Any], action: dict[str, Any], *, turn_id: Any) -> tuple[bool, str, list[dict[str, Any]]]:
    """``(valid, reason, changes)`` for a declared ``npc.talk`` action: ``open`` (with what the words named, if
    anything), ``topic`` (clicked), ``leave``, or ``words`` (the hero's own words to the person)."""

    hero_id = _text(action.get('actorId'))
    npc_id = _text(action.get('npcId'))
    operation = _text(action.get('operation'))
    if fight_running(state):
        return False, 'Not while the fight is on.', []
    if operation == 'leave':
        change = close_change(state, hero_id, turn_id=turn_id, reason='leave')
        return (True, 'Leaves the conversation.', [change]) if change else (False, 'You are not talking with anyone.', [])
    person = person_record(state, npc_id)
    name = _text((person or {}).get('name') or authored(state, npc_id).get('name')) or 'They'
    if person is None or not in_room(state, hero_id, npc_id):
        return False, f'{name} is not here.', []
    talking = _text((conversation_of(state, hero_id) or {}).get('npcId')) == npc_id
    if operation == 'topic' and not talking:
        return False, f'You are not talking with {name}.', []
    opening = operation == 'open' or not talking
    changes = [open_change(state, hero_id, npc_id, turn_id=turn_id)] if opening else []
    projected = projected_conversation(state, hero_id, npc_id, opening=opening)
    topic_id = _text(action.get('topicId'))
    if topic_id:
        topic = next((topic for topic in available_topics(projected, hero_id, npc_id) if _text(topic.get('id')) == topic_id), None)
        if topic is None:
            return False, f'{name} will not talk about that now.', []
        return True, f'Talks with {name}: {offered_label(topic)}.', [*changes, topic_change(projected, hero_id, npc_id, topic, turn_id=turn_id)]
    act = _text(action.get('act'))
    reached = topic_for_act(projected, hero_id, npc_id, act, action.get('about')) if act and authored_reaction(projected, npc_id, act, action.get('about')) is None else None
    if reached is not None:
        return True, f'Talks with {name}: {offered_label(reached)}.', [*changes, topic_change(projected, hero_id, npc_id, reached, turn_id=turn_id)]
    result = reaction(projected, hero_id, npc_id, act, about=action.get('about'), words=_text(action.get('sourceText')), coin=float(action.get('coin') or 0), turn_id=turn_id) if act else None
    if result is not None:
        return True, f'Speaks to {name}: {act}.', [*changes, act_change(projected, hero_id, npc_id, result, turn_id=turn_id)]
    return True, f'Speaks to {name}.', changes


def conversation_facts(applied: Iterable[dict[str, Any]], *, actor_id: str, declared: list[dict[str, Any]]) -> dict[str, Any]:
    """The narration packet's ``conversation`` (this hero's: the person, ``opened`` and ``greeting``, the ``topic``
    told or failed, a ``reaction``, ``free`` speech, ``left``) and ``callbacks`` (``{"name", "heroId", "text"}``)."""

    facts: dict[str, Any] = {}
    changes = [change for change in applied if isinstance(change, dict) and change.get('type') == TALK_CHANGE]
    callbacks = [
        {'npcId': _text(change.get('npcId')), 'name': _text(change.get('npcName')), 'heroId': _text(change.get('heroId')), 'text': _text(change.get('text'))}
        for change in changes
        if change.get('operation') == 'callback' and _text(change.get('text'))
    ]
    if callbacks:
        facts['callbacks'] = callbacks
    conversation: dict[str, Any] = {}
    for change in changes:
        if _text(change.get('actorId')) != actor_id:
            continue
        operation = change.get('operation')
        if operation == 'close' and change.get('reason') != 'leave':
            continue
        if operation in {'open', 'topic', 'act', 'close'}:
            conversation.update(npcId=_text(change.get('npcId')), name=_text(change.get('npcName')))
        if operation == 'open':
            conversation.update(opened=True, greeting=_text(change.get('greeting')))
        elif operation == 'topic':
            conversation['topic'] = {
                'label': _text(change.get('label')),
                'offered': offered_label({'label': change.get('label'), 'kind': change.get('kind')}),
                'line': _text(change.get('line')),
                'failed': change.get('outcome') == 'failed',
                **({'knows': True} if change.get('knows') is True else {}),
                **({'check': deepcopy(change['check'])} if isinstance(change.get('check'), dict) else {}),
                **({'waived': True} if change.get('waived') is True else {}),
            }
        elif operation == 'act':
            conversation['reaction'] = {
                key: deepcopy(change[key]) for key in ('act', 'about', 'outcome', 'line', 'check') if change.get(key) not in (None, '', {})
            }
        elif operation == 'close':
            conversation['left'] = True
    talked = next((action for action in declared if isinstance(action, dict) and action.get('type') == TALK_DECLARED), None)
    if talked is not None and _text(talked.get('operation')) == 'words' and not ({'topic', 'reaction'} & set(conversation)):
        conversation.update(npcId=_text(talked.get('npcId')), name=_text(talked.get('npcName')) or conversation.get('name', ''), free=True)
    if conversation:
        conversation['first'] = first_name(conversation.get('name'))
        facts['conversation'] = conversation
    return facts


def conversation_brief(state: dict[str, Any], hero_id: Any) -> dict[str, Any] | None:
    """For a narrating model: the person the hero is talking with, their ``personality``, ``disposition`` word, the
    lines already ``told``, what they ``remember`` of this hero, and what they ``mayTell`` if asked (the available
    ``ask`` topics without a check; the next ``knows`` line of someone without topics). Their ``secret`` is never
    given: only a topic that tells it can."""

    conversation = conversation_of(state, hero_id)
    if conversation is None:
        return None
    npc_id = _text(conversation.get('npcId'))
    person = person_record(state, npc_id) or {}
    record = authored(state, npc_id)
    told_ids = set(_strings(_talk(person).get('told')))
    told = [_text(topic.get('line')) for topic in topics(state, npc_id) if _text(topic.get('id')) in told_ids]
    knows = knows_lines(state, npc_id)
    known = int(_talk(person).get('knowsTold') or 0)
    told.extend(knows[:known] if not _dicts(record.get('topics')) else [])
    may_tell = [
        ((knows[known] if known < len(knows) else '') if topic.get('generated') else _text(topic.get('line')))
        for topic in available_topics(state, hero_id, npc_id)
        if topic.get('kind') == 'ask' or topic.get('generated')
        if not isinstance(topic.get('check'), dict)
    ]
    return {
        'person': _text(person.get('name') or record.get('name')),
        'personality': _text(record.get('personality')),
        'disposition': disposition_word(person.get('disposition')),
        'told': [line for line in told if line],
        'remembers': [memory_line(entry) for entry in memories(person, hero_id)],
        'mayTell': [line for line in may_tell if line],
    }


# The journal ----------------------------------------------------------------------------------------------------------


def journal_person(person: dict[str, Any], hero_ids: Iterable[str]) -> dict[str, Any]:
    """A person in the viewer's journal: their ``disposition`` word and up to two lines of what they remember of the
    viewer's heroes, newest first."""

    owned = set(hero_ids)
    lines = [memory_line(entry) for entry in reversed(memories(person)) if _text(entry.get('heroId')) in owned]
    return {'disposition': disposition_word(person.get('disposition')), **({'remembers': lines[:2]} if lines else {})}


def journal_promises(state: dict[str, Any], hero_ids: Iterable[str], *, ended: bool) -> list[dict[str, Any]]:
    """The viewer's heroes' word: the open promises (``{"id", "text", "to", "status"}``); once the story has ended,
    every promise, an open one as ``left open``."""

    owned = set(hero_ids)
    rows = []
    for promise in promises(state):
        if _text(promise.get('heroId')) not in owned or (promise.get('status') != 'open' and not ended):
            continue
        person = person_record(state, promise.get('npcId')) or {}
        status = 'left open' if ended and promise.get('status') == 'open' else _text(promise.get('status'))
        rows.append({'id': _text(promise.get('id')), 'text': _text(promise.get('text')), 'to': _text(person.get('name')), 'status': status})
    return rows
