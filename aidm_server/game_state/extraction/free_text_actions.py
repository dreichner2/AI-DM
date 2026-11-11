"""Typed words that name one action become the same typed intent a button sends.

``free_text_action`` reads a free-text message against what the acting hero can
see and returns the typed intents it names (a pickup, an object action, travel
or a rest), the refusal for a pickup of something that is not there, or a
narration-only kind (looking around, talking to someone present). Only the
first action is taken; ``more`` says the message named another.

Naming rule: case-insensitive, leading articles and possessive ``'s`` ignored;
the phrase must be the whole name or a run of whole words from it (at least one
word of three or more letters) that matches exactly one visible candidate of
that kind. Two or more candidates, or none, map nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping

from aidm_server.interactables import available_actions_for_target, interactable_actor_context
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player

ARTICLES = frozenset({'the', 'a', 'an', 'some', 'my', 'your', 'our'})
DEFAULT_LABEL_VERBS = {
    'inspect': 'Examine',
    'search': 'Search',
    'open': 'Open',
    'close': 'Close',
    'unlock': 'Unlock',
    'lock': 'Lock',
    'use': 'Use',
    'break': 'Break',
    'trigger': 'Trigger',
    'disarm': 'Disarm',
    'reset': 'Reset',
}
LEADING_SUBJECT = re.compile(r"^\s*(?:i|we)\s+", re.IGNORECASE)
CLAUSE_BREAK = re.compile(r'\s*(?:[.;!?]+|,\s*(?:and\s+then|and|then)?|\band\s+then\b|\bthen\b|\band\b)\s*', re.IGNORECASE)
REST_PHRASES = (
    ('short_rest', re.compile(r'\b(?:short rest|take a breather|rest for an hour)\b')),
    ('long_rest', re.compile(r'\b(?:long rest|make camp|sleep for the night|rest for the night|rest until morning)\b')),
)
LOOK_AROUND = re.compile(r'^(?:look around|survey|take stock)\b')
TAKE_ALL = re.compile(r'^(?:take everything|take all|take it all|loot it all)\b')
PICKUP = re.compile(r'^(?:pick up|take|grab|pocket|collect|gather|retrieve|loot)\s+(?P<phrase>.+)$')
COINS = re.compile(r'^(?:the\s+)?(?:coins|gold|money)$')
# Words after "take" that make it something other than a pickup ("take a look", "take cover").
NOT_A_PICKUP = frozenset(
    {
        'look', 'breath', 'breather', 'moment', 'step', 'steps', 'seat', 'cover', 'aim', 'stock', 'off', 'care',
        'heart', 'time', 'turn', 'turns', 'leave', 'hold', 'charge', 'shelter', 'rest', 'nap', 'chance', 'guess',
        'swing', 'shot', 'stand', 'note', 'notice', 'position', 'point', 'risk', 'action', 'up', 'out', 'down',
    }
)
# Verb phrases (longest first) and the object action each names; "force" is decided per object.
OBJECT_VERBS: tuple[tuple[str, str], ...] = (
    ('look inside', 'search'),
    ('look under', 'search'),
    ('look over', 'inspect'),
    ('look in', 'search'),
    ('look at', 'inspect'),
    ('rummage', 'search'),
    ('examine', 'inspect'),
    ('inspect', 'inspect'),
    ('search', 'search'),
    ('unlock', 'unlock'),
    ('smash', 'break'),
    ('study', 'inspect'),
    ('break', 'break'),
    ('close', 'close'),
    ('force', 'force'),
    ('light', 'use'),
    ('press', 'use'),
    ('touch', 'use'),
    ('read', 'inspect'),
    ('open', 'open'),
    ('shut', 'close'),
    ('lock', 'lock'),
    ('ring', 'use'),
    ('pull', 'use'),
    ('push', 'use'),
    ('sift', 'search'),
    ('use', 'use'),
)
OBJECT_PREPOSITIONS = frozenset({'through', 'in', 'into', 'inside', 'under', 'beneath', 'at', 'on', 'over', 'around'})
TRAVEL = re.compile(
    r'^(?:go back to|go to|travel to|head back to|head to|head for|walk back to|walk to|return to|make for|'
    r'set out for|leave for)\s+(?P<phrase>.+)$'
)
TALK = re.compile(r'^(?:(?:talk|speak)\s+(?:to|with)|ask)\s+(?P<phrase>.+)$')
ACTION_START = re.compile(
    r'^(?:pick up|take|grab|pocket|collect|gather|retrieve|loot|go|travel|head|walk|return|make for|set out|leave for|'
    r'talk|speak|ask|look|survey|rest|sleep|make camp|attack|cast|drink|use|' + '|'.join(verb for verb, _ in OBJECT_VERBS) + r')\b'
)


@dataclass(frozen=True)
class FreeTextAction:
    """What one free-text message names, for the turn that carries it."""

    kind: str
    intents: tuple[dict[str, Any], ...] = ()
    refusal: str | None = None
    npc: dict[str, Any] | None = None
    more: bool = False


def _text(value: Any) -> str:
    return str(value or '').strip()


def name_words(text: Any) -> list[str]:
    """Lower-case words of a name or phrase, without articles, possessive ``'s`` or punctuation."""

    lowered = re.sub(r"['’]s\b", '', _text(text).lower())
    return [word for word in re.findall(r'[a-z0-9]+', lowered) if word not in ARTICLES]


def _phrase_names(phrase_words: list[str], candidate_words: list[str]) -> bool:
    if not phrase_words or not any(len(word) >= 3 for word in phrase_words):
        return False
    size = len(phrase_words)
    return any(candidate_words[index:index + size] == phrase_words for index in range(len(candidate_words) - size + 1))


def _match(phrase: str, candidates: list[Any], name_of: Callable[[Any], str]) -> tuple[Any | None, bool]:
    """``(candidate, matched_anything)``: the one candidate the longest leading run of the phrase names.

    A prefix that names two or more candidates stops the search (ambiguous: nothing is mapped).
    """

    words = name_words(phrase)
    for size in range(len(words), 0, -1):
        prefix = words[:size]
        named = [candidate for candidate in candidates if _phrase_names(prefix, name_words(name_of(candidate)))]
        if len(named) == 1:
            return named[0], True
        if named:
            return None, True
    return None, False


def _clauses(message: str) -> list[str]:
    body = LEADING_SUBJECT.sub('', _text(message).lower())
    return [LEADING_SUBJECT.sub('', clause).strip() for clause in CLAUSE_BREAK.split(body) if clause and clause.strip()]


def _names_another_action(clauses: list[str]) -> bool:
    return any(ACTION_START.match(clause) for clause in clauses[1:])


def _hero(state: Mapping[str, Any], actor_id: str) -> dict[str, Any]:
    return next(
        (
            actor
            for actor in state.get('playerCharacters') or []
            if isinstance(actor, Mapping) and _text(actor.get('id')) == actor_id
        ),
        {},
    )


def _visible_scene(state: Mapping[str, Any], player_id: int | None) -> dict[str, Any]:
    projected = filter_session_snapshot_for_player(dict(state), private_player_ids=frozenset({player_id} if player_id else ()))
    scene = projected.get('currentScene') if isinstance(projected, dict) else None
    return scene if isinstance(scene, dict) else {}


def _offered(
    state: Mapping[str, Any],
    hero: dict[str, Any],
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    """The object's offered actions for this hero: ``action``, ``label``, ``legal``, ``check``."""

    location_id = _text((state.get('currentScene') or {}).get('locationId'))
    options = available_actions_for_target(
        state, location_id=location_id, target_id=_text(entry.get('id')), actor=interactable_actor_context(state, hero)
    ).get('actions') or []
    projected_labels = {
        _text(offer.get('action')): _text(offer.get('label'))
        for offer in entry.get('offeredActions') or []
        if isinstance(offer, Mapping)
    }
    authored = entry.get('actionLabels') if isinstance(entry.get('actionLabels'), Mapping) else {}
    offered = []
    for option in options:
        action = _text(option.get('action'))
        if action not in DEFAULT_LABEL_VERBS:
            continue
        label = projected_labels.get(action) or _text(authored.get(action)) or f"{DEFAULT_LABEL_VERBS[action]} {_text(entry.get('name'))}"
        offered.append(
            {
                'action': action,
                'label': label,
                'legal': option.get('legal') is True or option.get('requiresCheck') is True,
                'needsRoll': option.get('requiresCheck') is True or bool(option.get('check')),
            }
        )
    return offered


def _object_intent(entry: dict[str, Any], action: str, message: str) -> dict[str, Any]:
    return {
        'kind': 'object',
        'source': 'free_text',
        'text': message,
        'object': {'id': _text(entry.get('id')), 'action': action, 'revision': entry.get('revision')},
    }


def _item_intent(item: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        'kind': 'item',
        'source': 'free_text',
        'text': message,
        'inventory_action': 'pick_up',
        'item': {'id': _text(item.get('id')), 'name': _text(item.get('name')), 'quantity': int(item.get('quantity') or 1)},
    }


def _is_coin_pile(item: Mapping[str, Any]) -> bool:
    return _text(item.get('type')).lower() == 'currency' or isinstance(item.get('currency'), Mapping)


def _by_label(message: str, objects: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The object and offer whose label the message equals or begins with (the longest such label)."""

    words = name_words(LEADING_SUBJECT.sub('', _text(message)))
    matches = [
        (entry, offer, len(label_words))
        for entry, offers in objects
        for offer in offers
        if (label_words := name_words(offer['label'])) and words[: len(label_words)] == label_words
    ]
    if not matches:
        return None
    longest = max(size for _entry, _offer, size in matches)
    best = [(entry, offer) for entry, offer, size in matches if size == longest]
    return best[0] if len(best) == 1 else None


def _object_by_verb(clause: str, objects: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> tuple[dict[str, Any], str] | None:
    for verb, action in OBJECT_VERBS:
        if not re.match(rf'^{re.escape(verb)}\b', clause):
            continue
        rest = clause[len(verb):].split()
        while rest and rest[0] in OBJECT_PREPOSITIONS:
            rest = rest[1:]
        entry_offers, _matched = _match(' '.join(rest), objects, lambda pair: pair[0].get('name'))
        if entry_offers is None:
            return None
        entry, offers = entry_offers
        by_action = {offer['action']: offer for offer in offers}
        if action == 'force':
            action = 'unlock' if by_action.get('unlock', {}).get('needsRoll') else 'break'
        return (entry, action) if action in by_action else None
    return None


def _travel_destinations(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    location_id = _text((state.get('currentScene') or {}).get('locationId'))
    locations = [location for location in state.get('locations') or [] if isinstance(location, Mapping)]
    current = next((location for location in locations if _text(location.get('id')) == location_id), {})
    connected = {_text(value) for value in current.get('connectedLocationIds') or []}
    return [
        dict(location)
        for location in locations
        if _text(location.get('id')) in connected
        and location.get('hidden') is not True
        and _text(location.get('status')).lower() not in {'hidden', 'undiscovered', 'unknown'}
    ]


def _present_npcs(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), Mapping) else {}
    present = {_text(value) for value in scene.get('activeNpcIds') or []}
    return [
        dict(npc)
        for key in ('knownNpcs', 'partyNpcs')
        for npc in state.get(key) or []
        if isinstance(npc, Mapping) and _text(npc.get('id')) in present and _text(npc.get('name'))
    ]


def free_text_action(
    state: Mapping[str, Any],
    *,
    actor_id: str,
    player_id: int | None,
    message: str,
) -> FreeTextAction | None:
    """The action a free-text message clearly names, or None when it names none."""

    clauses = _clauses(message)
    if not clauses:
        return None
    first = clauses[0]
    more = _names_another_action(clauses)
    for rest_type, pattern in REST_PHRASES:
        if pattern.search(first):
            return FreeTextAction(
                kind='rest',
                intents=({'kind': 'rest', 'source': 'free_text', 'text': message, 'rest_type': rest_type},),
                more=more,
            )
    if LOOK_AROUND.match(first):
        return FreeTextAction(kind='look', more=more)

    hero = _hero(state, actor_id)
    scene = _visible_scene(state, player_id)
    objects = [
        (entry, offers)
        for collection in ('interactables', 'hazards')
        for entry in scene.get(collection) or []
        if isinstance(entry, dict) and (offers := _offered(state, hero, entry))
    ]
    labelled = _by_label(message, objects)
    if labelled:
        entry, offer = labelled
        return FreeTextAction(kind='object', intents=(_object_intent(entry, offer['action'], message),), more=more)

    items = [item for item in scene.get('items') or [] if isinstance(item, dict) and _text(item.get('id'))]
    if TAKE_ALL.match(first):
        if not items:
            return FreeTextAction(kind='pickup', refusal='There is nothing here to take.', more=more)
        return FreeTextAction(kind='pickup', intents=tuple(_item_intent(item, message) for item in items), more=more)
    pickup = PICKUP.match(first)
    if pickup:
        phrase_words = [word for word in pickup.group('phrase').split() if word not in ARTICLES]
        if phrase_words and phrase_words[0] not in NOT_A_PICKUP and len(phrase_words) <= 5:
            phrase = ' '.join(phrase_words)
            if COINS.match(pickup.group('phrase')):
                piles = [item for item in items if _is_coin_pile(item)]
                if piles:
                    return FreeTextAction(kind='pickup', intents=tuple(_item_intent(item, message) for item in piles), more=more)
                return FreeTextAction(kind='pickup', refusal=f'There is no {phrase} here to take.', more=more)
            item, matched = _match(phrase, [item for item in items if not _is_coin_pile(item)], lambda item: item.get('name'))
            if item is not None:
                return FreeTextAction(kind='pickup', intents=(_item_intent(item, message),), more=more)
            if not matched:
                return FreeTextAction(kind='pickup', refusal=f'There is no {phrase} here to take.', more=more)
            return None

    object_action = _object_by_verb(first, objects)
    if object_action:
        entry, action = object_action
        return FreeTextAction(kind='object', intents=(_object_intent(entry, action, message),), more=more)

    travel = TRAVEL.match(first)
    if travel:
        location, _matched = _match(travel.group('phrase'), _travel_destinations(state), lambda location: location.get('name'))
        if location is not None:
            return FreeTextAction(
                kind='travel',
                intents=(
                    {
                        'kind': 'travel',
                        'source': 'free_text',
                        'text': message,
                        'location': {'id': _text(location.get('id')), 'name': _text(location.get('name'))},
                    },
                ),
                more=more,
            )

    talk = TALK.match(first)
    if talk:
        npc, _matched = _match(talk.group('phrase'), _present_npcs(state), lambda npc: npc.get('name'))
        if npc is not None:
            return FreeTextAction(kind='talk', npc=npc, more=more)
    return None
