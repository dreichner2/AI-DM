"""What a hero's own words to a person are: a topic asked about, or one of six other speech acts.

Something shown (below) decides first. Then a topic is chosen when the words hold its label (leading article dropped)
as whole words (:func:`matched_topic`). Otherwise the words are read for an act by its cue words (:data:`CUES`, whole words, case-insensitive; with several,
the first act of :data:`ACT_ORDER`): ``promise``, ``show`` (only with an item the hero holds or a clue the party knows
named in the words: that is its ``about``), ``threaten``, ``offer`` (coin, an item, a favour), ``claim`` (``about``
``authority`` when the words say by whose authority, else ``identity``) and ``apologise``. Failing a cue, or before
a bare claim of who the hero is, a topic's ``keywords`` choose it. With an AI narrator, words that hold no cue are put once to the pre-narration helper
(:func:`helper_act`), which may only name one of the seven acts, or none, and what it is about; the engine applies
the act and the helper never decides the person's answer. Words that are none of these are free speech; when the
helper reads in them an intention for later (``"plan"``), it becomes the players' plan
(:mod:`aidm_server.services.session_return`).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from flask import current_app, has_app_context

from aidm_server.contracts import ProviderRequest
from aidm_server.game_state.extraction.free_text_actions import attacked_person, free_text_action, name_words
from aidm_server.game_state.extraction.schemas import extract_json_object
from aidm_server.game_state.orchestration import conversations
from aidm_server.game_state.zones import fight_running, hero_zone_id, npc_zone_id, place_zones, present_npcs, zone_steps
from aidm_server.llm_providers import get_helper_provider, safe_mode_active
from aidm_server.provider_priority import foreground_provider_reservation
from aidm_server.telemetry import telemetry_event

ACTS = ('ask', 'promise', 'show', 'threaten', 'offer', 'claim', 'apologise')
ACT_ORDER = ('apologise', 'threaten', 'offer', 'promise', 'show', 'claim')
NUMBER_WORDS = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
    'ten': 10, 'twenty': 20, 'fifty': 50, 'hundred': 100,
}
COIN = (
    r'(?P<amount>\d+|one|two|three|four|five|six|seven|eight|nine|ten|twenty|fifty|hundred)\s+'
    r'(?P<unit>gp|sp|cp|gold|silver|copper|coins?)'
)
COIN_AMOUNT = re.compile(rf'\b{COIN}\b')
COIN_VALUE = {'gp': 1.0, 'gold': 1.0, 'coin': 1.0, 'coins': 1.0, 'sp': 0.1, 'silver': 0.1, 'cp': 0.01, 'copper': 0.01}
# "I am" and "we are" claim who the hero is only before a name or a noun phrase, not "I'm sorry" or "I am looking".
IDENTITY = re.compile(
    r"\b(?i:i am|i'm|we are|we're)\s+(?!(?i:a bit|a little|a lot|not|just|so|very|only|still|sure|afraid|glad|sorry)\b)"
    r"(?:(?i:an?|the|one of|from|of|your|his|her|their)\b|[A-Z])"
)
AUTHORITY = re.compile(r"\b(?:sent by|on behalf of|by order of|by the order of|i serve|we serve|in the name of)\b")
CUES: dict[str, re.Pattern[str]] = {
    'apologise': re.compile(r"\b(?:sorry|apologi[sz]e[sd]?|apologi[sz]ing|apolog(?:y|ies)|forgive|i was wrong)\b"),
    'threaten': re.compile(r"\b(?:or else|or i(?:'ll| will)|last chance|threaten(?:s|ed|ing)?|i(?:'ll| will) (?:hurt|arrest|burn)|talk or)\b"),
    'offer': re.compile(rf"\b(?:i(?:'ll| will) pay|i offer|in exchange|i can give)\b|\b{COIN}\b"),
    'promise': re.compile(r"\b(?:promise[sd]?|swear|i swore|give (?:you )?my word|you have my word|i(?:'ll| will) (?:bring|return|find))\b"),
    'show': re.compile(r"\b(?:show|hand|present|here is|here's|look at this)\b"),
}
HELPER_SYSTEM_MESSAGE = (
    'You read what a player character says to a person in a tabletop game and name the one speech act it is. '
    'Answer with JSON only.'
)
HELPER_RAW_PREVIEW_LIMIT = 600
# A plan the helper reads, at most this many characters.
PLAN_LIMIT = 160


def _text(value: Any) -> str:
    return str(value or '').strip()


def _lowered(words: str) -> str:
    return ' '.join(_text(words).lower().replace('’', "'").replace('‘', "'").split())


def _contains(text_words: list[str], phrase_words: list[str]) -> bool:
    size = len(phrase_words)
    return bool(size) and any(text_words[index:index + size] == phrase_words for index in range(len(text_words) - size + 1))


def matched_topic(words: str, topics: Iterable[dict[str, Any]], *, keywords: bool = True) -> dict[str, Any] | None:
    """The first topic whose label (or, with ``keywords``, any keyword) the words hold as whole words; None."""

    text_words = name_words(words)
    for topic in topics:
        phrases = [topic.get('label'), *((topic.get('keywords') or []) if keywords else [])]
        if any(_contains(text_words, name_words(phrase)) for phrase in phrases if _text(phrase)):
            return topic
    return None


def named_thing(words: str, things: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """The thing (``{"id", "name"}``) the words name by its whole name or a run of its words (one of three or more
    letters), the longest such run first; None."""

    text_words = name_words(words)
    best: tuple[int, dict[str, Any]] | None = None
    for thing in things:
        name = name_words(thing.get('name'))
        for size in range(len(name), 0, -1):
            runs = [name[index:index + size] for index in range(len(name) - size + 1)]
            if any(any(len(word) >= 3 for word in run) and _contains(text_words, run) for run in runs):
                if best is None or size > best[0]:
                    best = (size, thing)
                break
    return best[1] if best else None


def offered_coin(words: str) -> float:
    """The coin the words offer, in gold pieces (``ten silver`` is 1); 0 when they name none."""

    match = COIN_AMOUNT.search(_lowered(words))
    if not match:
        return 0
    amount = match.group('amount')
    return (int(amount) if amount.isdigit() else NUMBER_WORDS[amount]) * COIN_VALUE[match.group('unit')]


def cue_act(words: str, *, things: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """``{"act", "about"?}`` for the words by their cue words (rules in the module docstring); None."""

    text = _lowered(words)
    held = list(things)
    for act in ACT_ORDER:
        if act == 'claim':
            if AUTHORITY.search(text):
                return {'act': 'claim', 'about': 'authority'}
            if IDENTITY.search(_text(words).replace('’', "'")):
                return {'act': 'claim', 'about': 'identity'}
            continue
        if not CUES[act].search(text):
            continue
        if act == 'show':
            thing = named_thing(words, held)
            if thing is None:
                continue
            return {'act': 'show', 'about': _text(thing.get('id'))}
        if act == 'offer':
            thing = named_thing(words, held)
            return {'act': 'offer', **({'about': _text(thing.get('id'))} if thing and not offered_coin(words) else {})}
        return {'act': act}
    return None


def speech_helper_enabled() -> bool:
    """The pre-narration helper may read a hero's words: an AI narrator is set up and the state helper is on."""

    if safe_mode_active():
        return False
    if has_app_context() and current_app.config.get('AIDM_ENV') == 'test':
        return bool(current_app.config.get('AIDM_STATE_PIPELINE_HELPER_IN_TESTS', False))
    if has_app_context():
        return bool(current_app.config.get('AIDM_STATE_PIPELINE_HELPER_ENABLED', True))
    return True


def _helper_prompt(words: str, *, person: str, topics: list[dict[str, Any]], things: list[dict[str, Any]]) -> str:
    topic_lines = '\n'.join(f"- {_text(topic.get('id'))}: {_text(topic.get('label'))}" for topic in topics) or '- none'
    thing_lines = '\n'.join(f"- {_text(thing.get('id'))}: {_text(thing.get('name'))}" for thing in things) or '- none'
    return (
        f'The hero says to {person}:\n"{_text(words)}"\n\n'
        'Which ONE speech act is it?\n'
        '- ask: asks about one of the topics below (about = its topic id)\n'
        '- promise: gives their word to do something\n'
        '- show: shows one of the things below (about = its id)\n'
        '- threaten: threatens or intimidates\n'
        '- offer: offers coin, an item (about = its id) or a favour in exchange\n'
        '- claim: says who they are (about = "identity") or by whose authority they act (about = "authority")\n'
        '- apologise: says sorry or asks forgiveness\n'
        '- none: anything else\n\n'
        f'Topics:\n{topic_lines}\n\nThings the hero has:\n{thing_lines}\n\n'
        'Answer {"act": "...", "about": "...", "text": "<the words that carry the act>"}. Never answer for the person.\n'
        'When the act is none and the words say what the hero means to do later, something that cannot be done here '
        'and now, add "plan": "<that intention in at most twelve words>".'
    )


def _helper_debug(*, attempted: bool, model: str | None = None, raw: str | None = None, error: str | None = None, parsed: Any = None) -> dict[str, Any]:
    return {
        'source': 'speech_act_helper' if attempted else 'skipped',
        'reason': 'speech_act',
        'helperAttempted': attempted,
        'helperSchemaValid': parsed is not None,
        'helperModel': model,
        'helperRawText': raw,
        'helperRawPreview': raw[:HELPER_RAW_PREVIEW_LIMIT] if raw else None,
        'helperParsed': parsed,
        'helperError': error,
        'fallbackRan': False,
        'fallbackReason': None,
    }


def helper_act(
    words: str,
    *,
    person: str,
    topics: list[dict[str, Any]],
    things: list[dict[str, Any]],
    before_provider_call: Callable[[], None] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The act the pre-narration helper reads in the words (``{"act", "about"?, "text"?}``), or None for none or an
    answer outside the acts and the ids it was given; ``{"act": "none", "plan"}`` when it reads no act but an
    intention the hero means to carry out later; and the helper's debug record."""

    with foreground_provider_reservation() as activate_provider:
        if before_provider_call:
            before_provider_call()
        try:
            activate_provider()
            response = get_helper_provider().generate(
                ProviderRequest(prompt=_helper_prompt(words, person=person, topics=topics, things=things), system_message=HELPER_SYSTEM_MESSAGE)
            )
        except Exception as exc:
            telemetry_event('state_pipeline.speech_act_helper.failed', payload={'error': str(exc)[:300]}, severity='warning')
            return None, _helper_debug(attempted=True, error=str(exc)[:300])
    raw = str(response.text or '')
    payload = extract_json_object(raw)
    act = _text((payload or {}).get('act')).lower()
    about = _text((payload or {}).get('about'))
    allowed = {
        'ask': {_text(topic.get('id')) for topic in topics},
        'show': {_text(thing.get('id')) for thing in things},
        'offer': {'', *(_text(thing.get('id')) for thing in things)},
        'claim': {'', 'authority', 'identity'},
    }
    debug = _helper_debug(attempted=True, model=response.model, raw=raw, parsed=payload)
    plan = ' '.join(_text((payload or {}).get('plan')).split())[:PLAN_LIMIT]
    if act == 'none' and plan:
        return {'act': 'none', 'plan': plan}, debug
    if act not in ACTS or (act in allowed and about not in allowed[act]):
        return None, debug
    if act == 'claim':
        return {'act': act, 'about': about or 'identity'}, debug
    return {'act': act, **({'about': about} if about and act in allowed else {})}, debug


def unasked_debug() -> dict[str, Any]:
    """The debug record of words read without the helper."""

    return _helper_debug(attempted=False)


# A conversation turn's words ----------------------------------------------------------------------------------------


def things_at_hand(state: dict[str, Any], hero_id: Any) -> list[dict[str, Any]]:
    """What the hero can show or offer: the items they hold and the clues the party knows (``{"id", "name"}``)."""

    items = [{'id': _text(item.get('id')), 'name': _text(item.get('name'))} for item in conversations.hero_items(state, hero_id)]
    clues = [
        {'id': _text(clue.get('id')), 'name': _text(clue.get('title') or clue.get('name'))}
        for clue in state.get('clues') or []
        if isinstance(clue, dict) and _text(clue.get('status')).lower() not in conversations.UNKNOWN_CLUE_STATUSES
    ]
    return [thing for thing in [*items, *clues] if thing['id'] and thing['name']]


def _named_here(state: dict[str, Any], actor_id: str, message: str) -> dict[str, Any] | None:
    """The one person in the hero's room the words name by full or first name; None."""

    words = name_words(message)
    named = []
    for npc in present_npcs(state):
        name = name_words(npc.get('name'))
        candidates = [name, name[:1]] if name and len(name[0]) >= 3 else [name]
        if conversations.in_room(state, actor_id, npc.get('id')) and any(_contains(words, candidate) for candidate in candidates):
            named.append(npc)
    return named[0] if len(named) == 1 else None


ADDRESSING = frozenset({'ask', 'asks', 'asked', 'tell', 'tells', 'told', 'question', 'questions', 'to', 'say', 'says'})


def _next_door(state: dict[str, Any], hero_id: Any, npc_id: Any) -> bool:
    """The person is known, at the place, and one room away from the hero."""

    zones = place_zones(state)
    return (
        bool(zones)
        and conversations.is_present(state, npc_id)
        and zone_steps(zones, hero_zone_id(state, hero_id, zones), npc_zone_id(state, npc_id, zones)) == 1
    )


def _addressed_here(
    state: dict[str, Any],
    actor_id: str,
    message: str,
    *,
    within: Callable[[dict[str, Any], Any, Any], bool] = conversations.in_room,
) -> dict[str, Any] | None:
    """The person in the hero's room the words speak to by name: the name first (after the hero's own), or right
    after ``ask``, ``tell``, ``question``, ``say to``; None. ``within`` widens it to the room next door, where the
    hero steps in first as any action aimed there does."""

    hero = next((record for record in state.get('playerCharacters') or [] if isinstance(record, dict) and _text(record.get('id')) == actor_id), {})
    words = name_words(message)
    own = name_words(hero.get('name'))
    if own and words[:len(own)] == own:
        words = words[len(own):]
    starts = [0, *(index + 1 for index, word in enumerate(words) if word in ADDRESSING)]
    for npc in present_npcs(state):
        name = name_words(npc.get('name'))
        candidates = [name, name[:1]] if name and len(name[0]) >= 3 else [name]
        if within(state, actor_id, npc.get('id')) and any(
            candidate and words[start:start + len(candidate)] == candidate for candidate in candidates for start in starts
        ):
            return npc
    return None


def conversation_words(state: dict[str, Any], *, actor_id: str, player_id: int | None, message: str) -> dict[str, Any] | None:
    """The ``talk`` payload a hero's typed words become outside a fight, or None.

    Words that talk to a person (``I talk to Mara``) open a conversation with them; words that name another
    action, an attack on someone included, are that action (and end any conversation); other words are said to the
    person the hero is talking with unless they speak to someone else in the room by name, or, outside a
    conversation, to the one person in the hero's room they name.
    """

    hero = next((record for record in state.get('playerCharacters') or [] if isinstance(record, dict) and _text(record.get('id')) == actor_id), {})
    if fight_running(state) or not _text(message) or attacked_person(state, message, hero_name=_text(hero.get('name'))):
        return None
    mapped = free_text_action(state, actor_id=actor_id, player_id=player_id, message=message)
    if mapped is not None:
        npc = mapped.npc if mapped.kind == 'talk' else None
        if npc and npc.get('captive') is not True:
            return {'action': 'open', 'npc_id': _text(npc.get('id'))}
        return None
    conversation = conversations.conversation_of(state, actor_id)
    # "Tovin, it's all right" said from the next room is said to Tovin: the hero steps out to him first.
    addressed = _addressed_here(state, actor_id, message) or _addressed_here(state, actor_id, message, within=_next_door)
    if conversation is not None:
        return {'action': 'words', 'npc_id': _text((addressed or {}).get('id') or conversation.get('npcId'))}
    named = _named_here(state, actor_id, message) or addressed
    return {'action': 'words', 'npc_id': _text(named.get('id'))} if named else None


def talk_extraction(
    state: dict[str, Any],
    *,
    actor_id: str,
    talk: dict[str, Any],
    words: str,
    before_provider_call: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """The pre-narration extraction of a conversation turn: one declared ``npc.talk`` action carrying what the words
    are (a ``topicId``, or an ``act`` with its ``about`` and offered ``coin``), and the helper's debug record."""

    action = _text(talk.get('action'))
    npc_id = _text(talk.get('npc_id'))
    person = conversations.person_record(state, npc_id) or {}
    declared: dict[str, Any] = {
        'id': 'act_001',
        'type': conversations.TALK_DECLARED,
        'actorId': actor_id,
        'confidence': 1.0,
        'sourceText': words,
        'requiresDMResolution': False,
        'operation': action,
        'npcId': npc_id,
        'npcName': _text(person.get('name')),
    }
    debug = unasked_debug()
    if action == 'topic':
        declared['topicId'] = _text(talk.get('topic_id'))
    elif action in {'open', 'words'} and _text(words):
        talking = _text((conversations.conversation_of(state, actor_id) or {}).get('npcId')) == npc_id
        projected = conversations.projected_conversation(state, actor_id, npc_id, opening=action == 'open' or not talking)
        topics = conversations.available_topics(projected, actor_id, npc_id)
        things = things_at_hand(state, actor_id)
        # Something shown decides first, then a topic's label, then an act's cue words; a topic's keyword comes only
        # before a bare claim of who the hero is ("I'm a stranger here: what of the caravan?" asks about the caravan).
        act = cue_act(words, things=things)
        topic = None if act and act['act'] == 'show' else matched_topic(words, topics, keywords=False)
        if topic is not None:
            act = None
        elif act is None or act == {'act': 'claim', 'about': 'identity'}:
            topic = matched_topic(words, topics)
            act = None if topic else act
        if topic is None and act is None and action == 'words' and speech_helper_enabled():
            act, debug = helper_act(words, person=_text(person.get('name')), topics=topics, things=things, before_provider_call=before_provider_call)
            if act and act['act'] == 'none':
                # No act, but an intention for later: the director notes it as the players' plan.
                declared['plan'] = act['plan']
                act = None
            if act and act['act'] == 'ask':
                topic, act = next((entry for entry in topics if _text(entry.get('id')) == act.get('about')), None), None
        if topic is not None:
            declared['topicId'] = _text(topic.get('id'))
        elif act is not None:
            declared.update(act=act['act'], **({'about': act['about']} if act.get('about') else {}))
            if act['act'] == 'offer':
                declared['coin'] = offered_coin(words)
    return {'declaredActions': [declared], 'notes': ['conversation'], 'debug': debug}
