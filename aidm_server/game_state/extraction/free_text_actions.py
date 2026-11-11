"""Typed words that name actions become the same typed intents the buttons send.

``free_text_action`` reads the first two sentences of a free-text message
against what the acting hero can see. Each sentence is split into clauses on
"and", "then", ", then" and ";", and each clause is mapped on its own: its verb
may follow a lead-in of up to eight words ("Carefully, I open…") or sit in a
purpose phrase ("climb into the lead wagon to search it"), and "it", "them" or
"that" after the verb is the thing named last before it in the message, else the
single scene object or item the previous turn acted on. The first mapped clause
is taken: its typed intents (a pickup, an object action, travel or a rest), the
refusal for a pickup of something that is not there, or a narration-only kind
(looking around, talking to someone present). Every later mapped action is
returned as a deferred action, by its offered label, and is not taken.

A clause maps nothing when it is negated or hypothetical, its sentence is a
question, or its verb sits inside quotation marks (speech); nor when its
lead-in names someone present or has the hero direct or watch someone else
("tell", "let", "order", "watch"), since the verb is then theirs.

Naming rule: case-insensitive, leading articles and possessive ``'s`` ignored;
the phrase must be the whole name or a run of whole words from it (at least one
word of three or more letters) that matches exactly one visible candidate of
that kind. Two or more candidates, or none, map nothing. ``mentions`` applies
the same rule to every noun phrase of a text: the words after an article,
determiner or preposition.

A captive the party holds (``currentScene.captives``) is questioned like a
person present: "question", "interrogate", "ask" or "talk to" it.

In a fight, :func:`fight_words` decides instead (the hero's legal-action bundle
says whose turn it is): speech is free on any turn; off turn, anything else is
refused (``It is {Name}'s turn.``); on the hero's turn the first clause that
names a fight action by its verb (:data:`FIGHT_VERBS`, any tense) or its label
takes it ("close in and tackle him" is the grapple that closes in first), an
action that is not legal is refused with its reason. Words that name no fight
action are speech when their first verb talks (:data:`FIGHT_SPEECH_VERBS`:
"question the cutthroat"), a look when it looks (:data:`FIGHT_LOOK_VERBS`, or
"search for"), both free, and otherwise an improvised action. The target is the enemy named; with "him",
"her", "it", "them" or "the last one", the only one there is; else the hero's
last target, else the only enemy standing. A blow aimed at a thing ("I cut the
rope") names no fight action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable, Iterable, Mapping

from aidm_server.combat.legal_actions import IMPROVISE_ACTION_ID, MOVE_FIRST_TYPES
from aidm_server.combat.pipeline import OBJECT_TARGET_WORDS
from aidm_server.combat.state import participant_is_targetable
from aidm_server.interactables import available_actions_for_target, interactable_actor_context, take_label
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player

ARTICLES = frozenset({'the', 'a', 'an', 'some', 'my', 'your', 'our'})
DETERMINERS = ARTICLES | {'that', 'this', 'these', 'those', 'its', 'his', 'her', 'their'}
PREPOSITIONS = frozenset(
    {
        'to', 'toward', 'towards', 'into', 'onto', 'inside', 'in', 'under', 'beneath', 'behind', 'beside', 'near',
        'at', 'on', 'over', 'through', 'across', 'along', 'up', 'down', 'by', 'with', 'from', 'for', 'of', 'around',
        'past',
    }
)
NOUN_LEADS = DETERMINERS | PREPOSITIONS
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
MAX_SENTENCES = 2
MAX_LEAD_IN = 8
MAX_PHRASE_WORDS = 6
QUOTED = re.compile(r'"[^"]*"?|“[^”]*”?')
SENTENCE = re.compile(r'(?P<body>[^.!?]+)(?P<end>[.!?]*)')
CLAUSE_SPLIT = re.compile(r'\s+and\s+|\s*,\s*then\s+|\s+then\s+|\s*;\s*')
WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)*")
HYPOTHETICAL = re.compile(r"\b(?:not|never|without|would)\b|n't\b|\b(?:before|if|should|could|can)\s+i\b")
# Words in a lead-in that make the verb after them someone else's ("I tell Mara to open it").
OTHERS_ACT = frozenset({'tell', 'tells', 'let', 'lets', 'order', 'orders', 'watch', 'watches'})
REST_PHRASES = (
    ('short_rest', re.compile(r'\b(?:short rest|take a breather|rest for an hour)\b')),
    ('long_rest', re.compile(r'\b(?:long rest|make camp|sleep for the night|rest for the night|rest until morning)\b')),
)
REST_LABELS = {'short_rest': 'a short rest', 'long_rest': 'a long rest'}
LOOK_AROUND = re.compile(r'^(?:look around|survey|take stock)\b')
TAKE_ALL = re.compile(r'^(?:take everything|take all|take it all|loot it all)\b')
PICKUP = re.compile(
    r'^(?:pick up|scoop up|gather up|help myself to|take|grab|pocket|collect|gather|retrieve|loot)\s+(?P<phrase>.+)$'
)
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
TRAVEL_VERBS = (
    'make my way', 'leave for', 'make for', 'set out', 'set off', 'go back', 'travel', 'return', 'follow', 'march',
    'start', 'head', 'walk', 'ride', 'hike', 'take', 'go',
)
TRAVEL_PREPOSITIONS = (
    'out along', 'out on', 'back to', 'towards', 'toward', 'through', 'along', 'onto', 'into', 'down', 'for', 'to', 'up',
)
TRAVEL = re.compile(rf"^(?:{'|'.join(TRAVEL_VERBS)})(?:\s+(?:{'|'.join(TRAVEL_PREPOSITIONS)}))?\s+(?P<phrase>.+)$")
TALK = re.compile(r'^(?:(?:talk|speak)\s+(?:to|with)|ask|question|interrogate)\s+(?P<phrase>.+)$')
PRONOUNS = frozenset({'it', 'them'})
# Fight actions by the verbs that name them, any tense; a particle may follow an object ("hold him down").
_OBJECT = r"(?: [a-z']+){0,3}"
FIGHT_VERBS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (action_type, re.compile(rf'^(?:{pattern})\b'))
    for action_type, pattern in (
        ('end_turn', r"end(?:s|ed|ing)?(?: (?:my|the|our|his|her|their))? turn"),
        ('advance', r"close(?:s|d)? in|closing in|advanc(?:e|es|ed|ing)"),
        ('reposition', r"f(?:a|e)ll(?:s|ing)? back|fallen back"),
        ('subdue', rf"knock(?:s|ed|ing)?{_OBJECT} out|(?:take|takes|took|taking){_OBJECT} alive|bind(?:s|ing)?|bound|tie(?:s|d)?|tying|truss(?:es|ed|ing)?|cuff(?:s|ed|ing)?|subdu(?:e|es|ed|ing)|captur(?:e|es|ed|ing)"),
        ('shove', rf"knock(?:s|ed|ing)?{_OBJECT} (?:down|over)|(?:throw|throws|threw|thrown|throwing){_OBJECT} down|shov(?:e|es|ed|ing)|push(?:es|ed|ing)?|trip(?:s|ped|ping)?|sweep(?:s|ing)?|swept"),
        ('grapple', rf"(?:hold|holds|held|holding){_OBJECT} down|jump(?:s|ed|ing)? on|grab(?:s|bed|bing)?|grappl(?:e|es|ed|ing)|tackl(?:e|es|ed|ing)|wrestl(?:e|es|ed|ing)|pin(?:s|ned|ning)?|seiz(?:e|es|ed|ing)|restrain(?:s|ed|ing)?"),
        ('demand_surrender', r"surrender(?:s|ed|ing)?|yield(?:s|ed|ing)?|(?:give|gives|gave|giving) up|(?:stand|stands|stood|standing) down|drop(?:s|ped)? it|(?:lay|lays|laid|laying) down"),
        ('attack', r"attack(?:s|ed|ing)?|hit(?:s|ting)?|strike(?:s)?|struck|striking|stab(?:s|bed|bing)?|slash(?:es|ed|ing)?|shoot(?:s|ing)?|shot|(?:fire|fires|fired|firing) at|(?:swing|swings|swung|swinging) at|cut(?:s|ting)?"),
        ('disengage', r"disengag(?:e|es|ed|ing)"),
        ('dodge', r"dodg(?:e|es|ed|ing)"),
        ('dash', r"dash(?:es|ed|ing)?"),
        ('hide', r"hid(?:e|es|ing|den)"),
        ('stabilize', r"stabili[sz](?:e|es|ed|ing)"),
        ('help', r"help(?:s|ed|ing)?"),
    )
)
RANGED_ATTACK_VERBS = re.compile(r'^(?:shoot|shoots|shooting|shot|fire|fires|fired|firing)\b')
MELEE_ATTACK_VERBS = re.compile(r'^(?:stab|stabs|stabbed|stabbing|slash|slashes|slashed|slashing|cut|cuts|cutting|swing|swings|swung|swinging)\b')
SPEECH_VERBS = frozenset(
    f'{stem}{ending}'
    for stem in ('say', 'ask', 'tell', 'shout', 'yell', 'call', 'whisper', 'taunt')
    for ending in ('', 's', 'ed', 'ing')
) | {'said', 'told'}
SPEECH_LEADS = frozenset({'i', 'we', 'then', 'and', 'so', 'now'})
TARGET_PRONOUNS = frozenset({'him', 'her', 'it', 'them'})
# Irregular forms of the first verbs below ("spoke", "swore", "eyeing").
IRREGULAR_FORMS = {
    'speak': {'spoke', 'spoken'},
    'swear': {'swore', 'sworn'},
    'beg': {'begged', 'begging'},
    'eye': {'eyed', 'eyeing'},
    'read': set(),
}


def _forms(*verbs: str) -> frozenset[str]:
    """Each verb in any tense: ``question``, ``questions``, ``questioned``, ``questioning``."""

    forms: set[str] = set()
    for verb in verbs:
        stem = verb[:-1] if verb.endswith('e') else verb
        forms |= {verb, f'{verb}s', f'{stem}ed', f'{stem}ing', *IRREGULAR_FORMS.get(verb, ())}
        if verb.endswith(('sh', 'ch', 'x')):
            forms.add(f'{verb}es')
        if verb.endswith('y') and verb[-2:-1] not in set('aeiou'):
            forms |= {f'{verb[:-1]}ies', f'{verb[:-1]}ied'}
    return frozenset(forms)


# In a fight, words naming no fight action whose first verb is one of these are speech (free).
FIGHT_SPEECH_VERBS = _forms(
    'question', 'interrogate', 'demand', 'threaten', 'warn', 'beg', 'plead', 'answer', 'reply', 'talk', 'speak',
    'greet', 'curse', 'swear', 'laugh', 'thank', 'insult', 'mock', 'taunt',
)
# ... and these only look (free): the narrator answers with where everyone stands.
FIGHT_LOOK_VERBS = _forms('look', 'watch', 'study', 'examine', 'read', 'listen', 'check', 'eye', 'glance')


@dataclass(frozen=True)
class FreeTextAction:
    """What one free-text message names, for the turn that carries it.

    ``deferred`` holds the later actions the message named and the turn does
    not take: ``{"kind", "label", "target", "verb"}``, where ``label`` is the
    action's offered label and ``target``/``verb`` say it in prose ("the lead
    wagon", "search").
    """

    kind: str
    intents: tuple[dict[str, Any], ...] = ()
    refusal: str | None = None
    npc: dict[str, Any] | None = None
    deferred: tuple[dict[str, str], ...] = ()

    @property
    def more(self) -> bool:
        return bool(self.deferred)


@dataclass(frozen=True)
class _Clause:
    words: tuple[str, ...]
    start: int
    sentence: int
    guarded: bool


@dataclass(frozen=True)
class _Mapped:
    action: FreeTextAction
    labels: tuple[dict[str, str], ...] = ()


@dataclass
class _Scene:
    """What the acting hero can see and act on, read once per message."""

    objects: list[tuple[dict[str, Any], list[dict[str, Any]]]]
    items: list[dict[str, Any]]
    destinations: list[dict[str, Any]]
    npcs: list[dict[str, Any]]
    captives: list[dict[str, Any]] = field(default_factory=list)
    previous: tuple[str, dict[str, Any]] | None = None
    spans: list[tuple[int, int, tuple[str, Any]]] = field(default_factory=list)


def _text(value: Any) -> str:
    return str(value or '').strip()


def name_words(text: Any) -> list[str]:
    """Lower-case words of a name or phrase, without articles, possessive ``'s`` or punctuation."""

    lowered = re.sub(r"['’]s\b", '', _text(text).lower())
    return [word for word in re.findall(r'[a-z0-9]+', lowered) if word not in ARTICLES]


def phrase_names(phrase_words: list[str], candidate_words: list[str]) -> bool:
    """The phrase is the whole name or a run of its whole words, one of three or more letters."""

    if not phrase_words or not any(len(word) >= 3 for word in phrase_words):
        return False
    size = len(phrase_words)
    return any(candidate_words[index:index + size] == phrase_words for index in range(len(candidate_words) - size + 1))


def _match(phrase: str, candidates: list[Any], name_of: Callable[[Any], str]) -> tuple[Any | None, bool]:
    """``(candidate, matched_anything)``: the one candidate the longest leading run of the phrase names.

    A prefix that names two or more candidates stops the search (ambiguous: nothing is mapped).
    """

    words = name_words(phrase)
    while words and words[0] in DETERMINERS:
        words = words[1:]
    for size in range(len(words), 0, -1):
        prefix = words[:size]
        named = [candidate for candidate in candidates if phrase_names(prefix, name_words(name_of(candidate)))]
        if len(named) == 1:
            return named[0], True
        if named:
            return None, True
    return None, False


def _clauses(message: str) -> list[_Clause]:
    """Every clause of the message, quoted speech taken out, with its place among the message's words."""

    text = QUOTED.sub(' ; ', _text(message).lower().replace('’', "'").replace('‘', "'"))
    clauses: list[_Clause] = []
    position = 0
    sentence = 0
    for match in SENTENCE.finditer(text):
        question = '?' in match.group('end')
        parts = [(part, WORD.findall(part)) for part in CLAUSE_SPLIT.split(match.group('body'))]
        if not any(words for _part, words in parts):
            continue
        for part, words in parts:
            if words:
                clauses.append(_Clause(tuple(words), position, sentence, question or bool(HYPOTHETICAL.search(part))))
                position += len(words)
        sentence += 1
    return clauses


def _spans(words: tuple[str, ...] | list[str], candidates: list[Any], name_of: Callable[[Any], str]) -> list[tuple[int, int, Any]]:
    """``(start, end, candidate)`` for each noun phrase that names exactly one candidate."""

    spans = []
    index = 1
    while index < len(words):
        if words[index] in NOUN_LEADS or words[index - 1] not in NOUN_LEADS:
            index += 1
            continue
        found = None
        for size in range(min(MAX_PHRASE_WORDS, len(words) - index), 0, -1):
            run = name_words(' '.join(words[index:index + size]))
            named = [candidate for candidate in candidates if phrase_names(run, name_words(name_of(candidate)))]
            if len(named) == 1:
                found = (index, index + size, named[0])
                break
            if named:
                break
        if found:
            spans.append(found)
            index = found[1]
        else:
            index += 1
    return spans


def mentions(
    text: str,
    candidates: Iterable[Any],
    name_of: Callable[[Any], str],
    *,
    guarded: bool = True,
) -> list[Any]:
    """The candidates the text names, in order, each once; ``guarded`` skips negated, hypothetical and quoted words."""

    pool = list(candidates)
    named: list[Any] = []
    for clause in _clauses(text):
        if guarded and clause.guarded:
            continue
        for _start, _end, candidate in _spans(clause.words, pool, name_of):
            if not any(candidate is seen for seen in named):
                named.append(candidate)
    return named


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
    """The object's offered actions for this hero: ``action``, ``label``, ``legal``, ``needsRoll``, ``retry``."""

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
                'retry': option.get('retry') is True,
            }
        )
    return offered


def _the(name: str) -> str:
    """``the Old North Road`` for "The Old North Road", ``the Lead Wagon`` for "Lead Wagon"."""

    return f'the {name[4:]}' if name.lower().startswith('the ') else f'the {name}'


def _object_parts(label: str, name: str) -> tuple[str, str]:
    """``(target, verb)`` of an object label: "Search the lead wagon" → ("the lead wagon", "search")."""

    words = label.split()
    article = next((index for index, word in enumerate(words) if index and word.lower() in {'the', 'a', 'an'}), None)
    if article is not None:
        target = ' '.join(words[article:])
        if phrase_names(name_words(target), name_words(name)):
            return target, ' '.join(words[:article]).lower()
    return _the(name), (words[0].lower() if words else 'use')


def _object_intent(entry: dict[str, Any], offer: dict[str, Any], message: str) -> dict[str, Any]:
    """The typed object intent; words naming a failed check again take time over it."""

    return {
        'kind': 'object',
        'source': 'free_text',
        'text': message,
        'object': {'id': _text(entry.get('id')), 'action': offer['action'], 'revision': entry.get('revision')},
        **({'take_time': True} if offer.get('retry') else {}),
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


def ways_onward(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The places connected to the current one that the party knows of."""

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


def _scene(state: Mapping[str, Any], *, actor_id: str, player_id: int | None, previous_target: tuple[str, str] | None) -> _Scene:
    hero = _hero(state, actor_id)
    visible = _visible_scene(state, player_id)
    objects = [
        (entry, offers)
        for collection in ('interactables', 'hazards')
        for entry in visible.get(collection) or []
        if isinstance(entry, dict) and (offers := _offered(state, hero, entry))
    ]
    items = [item for item in visible.get('items') or [] if isinstance(item, dict) and _text(item.get('id'))]
    captives = [
        {**captive, 'captive': True}
        for captive in (state.get('currentScene') or {}).get('captives') or []
        if isinstance(captive, Mapping) and _text(captive.get('name'))
    ]
    scene = _Scene(objects, items, ways_onward(state), _present_npcs(state), captives)
    if previous_target:
        kind, target_id = previous_target
        pool = [entry for entry, _offers in objects] if kind == 'object' else items
        found = next((record for record in pool if _text(record.get('id')) == target_id), None)
        scene.previous = (kind, found) if found is not None else None
    return scene


def _everything(scene: _Scene) -> list[tuple[str, Any]]:
    return [
        *(('object', pair) for pair in scene.objects),
        *(('item', item) for item in scene.items),
        *(('place', location) for location in scene.destinations),
    ]


def _entity_name(entity: tuple[str, Any]) -> str:
    kind, record = entity
    return _text((record[0] if kind == 'object' else record).get('name'))


def _antecedent(scene: _Scene, position: int, kind: str) -> Any | None:
    """What "it" at ``position`` refers to, when that is a ``kind``: the thing named last before it, else the previous turn's."""

    named = [entity for _start, end, entity in scene.spans if end <= position]
    if named:
        found_kind, record = named[-1]
        return record if found_kind == kind else None
    if scene.previous and scene.previous[0] == kind:
        record = scene.previous[1]
        if kind == 'object':
            return next((pair for pair in scene.objects if pair[0] is record), None)
        return record
    return None


def _others_act(words: tuple[str, ...], verb_index: int, scene: _Scene) -> bool:
    """The lead-in before ``verb_index`` makes the verb someone else's: it directs or watches them, or names them."""

    if any(
        word in OTHERS_ACT and not (word in {'let', 'lets'} and words[index + 1:index + 2] in (('me',), ('us',)))
        for index, word in enumerate(words[:verb_index])
    ):
        return True
    lead_in = name_words(' '.join(words[:verb_index]))
    for npc in scene.npcs:
        name = name_words(npc.get('name'))
        candidates = [name, name[:1]] if name and len(name[0]) >= 3 else [name]
        if any(
            candidate and lead_in[index:index + len(candidate)] == candidate
            for candidate in candidates
            for index in range(len(lead_in))
        ):
            return True
    return False


def _object_labels(entry: dict[str, Any], offer: dict[str, Any]) -> tuple[dict[str, str], ...]:
    target, verb = _object_parts(offer['label'], _text(entry.get('name')))
    return ({'kind': 'object', 'label': offer['label'], 'target': target, 'verb': verb},)


def _pickup_labels(items: Iterable[dict[str, Any]]) -> tuple[dict[str, str], ...]:
    labels = []
    for item in items:
        label = take_label(item)
        labels.append({'kind': 'pickup', 'label': label, 'target': label[len('Take '):], 'verb': 'take'})
    return tuple(labels)


def _object_action(entry: dict[str, Any], offers: list[dict[str, Any]], action: str, message: str) -> _Mapped | None:
    by_action = {offer['action']: offer for offer in offers}
    if action == 'force':
        unlock = by_action.get('unlock', {})
        action = 'unlock' if unlock.get('needsRoll') or unlock.get('retry') else 'break'
    if action not in by_action:
        return None
    offer = by_action[action]
    return _Mapped(FreeTextAction(kind='object', intents=(_object_intent(entry, offer, message),)), _object_labels(entry, offer))


def _by_label(words: list[str], scene: _Scene, message: str) -> _Mapped | None:
    """The object action whose label the words equal or begin with (the longest such label)."""

    spoken = name_words(' '.join(words))
    matches = [
        (entry, offer, len(label_words))
        for entry, offers in scene.objects
        for offer in offers
        if (label_words := name_words(offer['label'])) and spoken[: len(label_words)] == label_words
    ]
    if not matches:
        return None
    longest = max(size for _entry, _offer, size in matches)
    best = [(entry, offer) for entry, offer, size in matches if size == longest]
    if len(best) != 1:
        return None
    entry, offer = best[0]
    return _Mapped(FreeTextAction(kind='object', intents=(_object_intent(entry, offer, message),)), _object_labels(entry, offer))


def _travel(text: str, scene: _Scene, message: str) -> _Mapped | None:
    travel = TRAVEL.match(text)
    if not travel:
        return None
    location, _matched = _match(travel.group('phrase'), scene.destinations, lambda location: location.get('name'))
    if location is None:
        return None
    name = _text(location.get('name'))
    return _Mapped(
        FreeTextAction(
            kind='travel',
            intents=(
                {
                    'kind': 'travel',
                    'source': 'free_text',
                    'text': message,
                    'location': {'id': _text(location.get('id')), 'name': name},
                },
            ),
        ),
        ({'kind': 'travel', 'label': f'Go to {name}', 'target': _the(name), 'verb': 'travel to'},),
    )


def _pickup(text: str, position: int, scene: _Scene, message: str) -> _Mapped | None:
    pickup = PICKUP.match(text)
    if not pickup:
        return None
    raw_phrase = pickup.group('phrase').split()
    phrase_words = [word for word in raw_phrase if word not in ARTICLES]
    if not phrase_words or phrase_words[0] in NOT_A_PICKUP or len(phrase_words) > 5:
        return None
    if phrase_words[0] in PRONOUNS:
        verb_words = len(text.split()) - len(raw_phrase)
        item = _antecedent(scene, position + verb_words + raw_phrase.index(phrase_words[0]), 'item')
        if item is None:
            return None
        return _Mapped(FreeTextAction(kind='pickup', intents=(_item_intent(item, message),)), _pickup_labels([item]))
    phrase = ' '.join(phrase_words)
    if COINS.match(pickup.group('phrase')):
        piles = [item for item in scene.items if _is_coin_pile(item)]
        if piles:
            return _Mapped(FreeTextAction(kind='pickup', intents=tuple(_item_intent(item, message) for item in piles)), _pickup_labels(piles))
        return _Mapped(FreeTextAction(kind='pickup', refusal=f'There is no {phrase} here to take.'))
    item, matched = _match(phrase, [item for item in scene.items if not _is_coin_pile(item)], lambda item: item.get('name'))
    if item is not None:
        return _Mapped(FreeTextAction(kind='pickup', intents=(_item_intent(item, message),)), _pickup_labels([item]))
    if matched:
        return None
    # "take the old north road" is travel, not a pickup.
    travel = _travel(text, scene, message)
    if travel is not None:
        return travel
    return _Mapped(FreeTextAction(kind='pickup', refusal=f'There is no {phrase} here to take.'))


def _object_by_verb(words: list[str], position: int, scene: _Scene, message: str) -> _Mapped | None:
    text = ' '.join(words)
    for verb, action in OBJECT_VERBS:
        if not re.match(rf'^{re.escape(verb)}\b', text):
            continue
        skipped = len(verb.split())
        rest = words[skipped:]
        while rest and rest[0] in OBJECT_PREPOSITIONS:
            rest = rest[1:]
            skipped += 1
        # "that" is a pronoun unless a name follows it ("open that strongbox").
        pair, _matched = _match(' '.join(rest), scene.objects, lambda pair: pair[0].get('name'))
        if pair is None and rest and rest[0] in PRONOUNS | {'that'}:
            pair = _antecedent(scene, position + skipped, 'object')
        if pair is None:
            return None
        entry, offers = pair
        return _object_action(entry, offers, action, message)
    return None


def _talk(text: str, scene: _Scene) -> _Mapped | None:
    talk = TALK.match(text)
    if not talk:
        return None
    npc, _matched = _match(talk.group('phrase'), [*scene.npcs, *scene.captives], lambda npc: npc.get('name'))
    if npc is None:
        return None
    name = _text(npc.get('name'))
    if npc.get('captive') is True:
        return _Mapped(FreeTextAction(kind='talk', npc=npc), ({'kind': 'talk', 'label': f'Question {name}', 'target': name, 'verb': 'question'},))
    return _Mapped(FreeTextAction(kind='talk', npc=npc), ({'kind': 'talk', 'label': f'Talk to {name}', 'target': name, 'verb': 'talk to'},))


def _map_at(words: list[str], position: int, scene: _Scene, message: str) -> _Mapped | None:
    """The action the words name when their first word is the verb."""

    text = ' '.join(words)
    if LOOK_AROUND.match(text):
        return _Mapped(FreeTextAction(kind='look'))
    labelled = _by_label(words, scene, message)
    if labelled:
        return labelled
    if TAKE_ALL.match(text):
        if not scene.items:
            return _Mapped(FreeTextAction(kind='pickup', refusal='There is nothing here to take.'))
        return _Mapped(
            FreeTextAction(kind='pickup', intents=tuple(_item_intent(item, message) for item in scene.items)),
            _pickup_labels(scene.items),
        )
    return (
        _pickup(text, position, scene, message)
        or _object_by_verb(words, position, scene, message)
        or _travel(text, scene, message)
        or _talk(text, scene)
    )


def _map_clause(clause: _Clause, scene: _Scene, message: str) -> _Mapped | None:
    if clause.guarded:
        return None
    text = ' '.join(clause.words)
    for rest_type, pattern in REST_PHRASES:
        if pattern.search(text):
            return _Mapped(
                FreeTextAction(kind='rest', intents=({'kind': 'rest', 'source': 'free_text', 'text': message, 'rest_type': rest_type},)),
                ({'kind': 'rest', 'label': f'Take {REST_LABELS[rest_type]}', 'target': REST_LABELS[rest_type], 'verb': 'take'},),
            )
    for index in range(min(MAX_LEAD_IN + 1, len(clause.words))):
        if index and _others_act(clause.words, index, scene):
            return None
        mapped = _map_at(list(clause.words[index:]), clause.start + index, scene, message)
        if mapped:
            return mapped
    return None


def free_text_action(
    state: Mapping[str, Any],
    *,
    actor_id: str,
    player_id: int | None,
    message: str,
    previous_target: tuple[str, str] | None = None,
) -> FreeTextAction | None:
    """The first action a free-text message clearly names, with the later ones deferred; None when it names none.

    ``previous_target`` is ``("object" | "item", id)`` for the single thing the previous turn acted on.
    """

    clauses = _clauses(message)
    if not any(clause.sentence < MAX_SENTENCES for clause in clauses):
        return None
    scene = _scene(state, actor_id=actor_id, player_id=player_id, previous_target=previous_target)
    everything = _everything(scene)
    scene.spans = [
        (clause.start + start, clause.start + end, entity)
        for clause in clauses
        for start, end, entity in _spans(clause.words, everything, _entity_name)
    ]
    mapped = [
        result
        for clause in clauses
        if clause.sentence < MAX_SENTENCES and (result := _map_clause(clause, scene, message)) is not None
    ]
    if not mapped:
        return None
    first, later = mapped[0], mapped[1:]
    taken = {label['label'] for label in first.labels}
    deferred: list[dict[str, str]] = []
    for result in later:
        if result.action.kind == 'look' or result.action.refusal:
            continue
        for label in result.labels:
            if label['label'] not in taken and all(label['label'] != seen['label'] for seen in deferred):
                deferred.append(label)
    first_action = first.action
    return FreeTextAction(
        kind=first_action.kind,
        intents=first_action.intents,
        refusal=first_action.refusal,
        npc=first_action.npc,
        deferred=tuple(deferred),
    )


@dataclass(frozen=True)
class FightWords:
    """How typed words are taken on a fight turn.

    ``kind`` is ``speech`` (free: nothing is spent and the turn goes on), ``look`` (free like speech; the
    narrator tells where everyone stands), ``action`` (``action_id`` and ``target_id`` of a listed fight
    action), ``improvised`` (``action_id`` is the improvised action, ``target_id`` the enemy named or the
    only one) or ``refused`` (with ``refusal``).
    """

    kind: str
    action_id: str | None = None
    target_id: str | None = None
    refusal: str | None = None


def _unquoted_words(message: str) -> list[str]:
    return WORD.findall(QUOTED.sub(' ', _text(message).lower().replace('’', "'")))


def _first_words(message: str, *, hero_name: str = '') -> list[str]:
    """The unquoted words from the first verb on: leads ("I", "then", the hero's name) and adverbs dropped."""

    leads = SPEECH_LEADS | set(name_words(hero_name))
    words = _unquoted_words(message)
    while words and (words[0] in leads or words[0].endswith('ly')):
        words = words[1:]
    return words


def is_speech(message: str, *, hero_name: str = '') -> bool:
    """Words the hero says: they begin with a quotation mark, end with "?", or their first verb is a
    speech verb (say, ask, tell, shout, yell, call, whisper, taunt)."""

    text = _text(message)
    if not text:
        return False
    if text[0] in '"“\'‘' or text.rstrip('"”\'’ ').endswith('?'):
        return True
    words = _first_words(text, hero_name=hero_name)
    return bool(words) and words[0] in SPEECH_VERBS


def _free_fight_words(message: str, *, hero_name: str) -> str | None:
    """``speech`` or ``look`` for words naming no fight action whose first verb talks or looks; None."""

    words = _first_words(message, hero_name=hero_name)
    if not words:
        return None
    if words[0] in FIGHT_SPEECH_VERBS:
        return 'speech'
    if words[0] in FIGHT_LOOK_VERBS or words[:2] in (['search', 'for'], ['searches', 'for'], ['searching', 'for']):
        return 'look'
    return None


def attacked_person(state: Mapping[str, Any], message: str, *, hero_name: str = '') -> dict[str, Any] | None:
    """The one person present in the scene (``activeNpcIds``) the words attack: their first verb is an
    attack verb (:data:`FIGHT_VERBS`) aimed at that person ("I attack Osric"); None otherwise."""

    text = ' '.join(_first_words(message, hero_name=hero_name))
    attack = next(pattern for action_type, pattern in FIGHT_VERBS if action_type == 'attack')
    match = attack.match(text)
    if not match:
        return None
    person, _matched = _match(text[match.end():], _present_npcs(state), lambda npc: npc.get('name'))
    return person


def _fight_clauses(message: str) -> list[tuple[str, list[str], str]]:
    """``(action_type, words after the verb, the verb as typed)`` for each clause naming a fight action."""

    mapped = []
    for clause in _clauses(message):
        if clause.sentence >= MAX_SENTENCES or clause.guarded:
            continue
        for index in range(min(MAX_LEAD_IN + 1, len(clause.words))):
            if any(word in OTHERS_ACT for word in clause.words[:index]):
                break
            text = ' '.join(clause.words[index:])
            found = next(((action_type, match) for action_type, pattern in FIGHT_VERBS if (match := pattern.match(text))), None)
            if found:
                action_type, match = found
                mapped.append((action_type, text[match.end():].split(), match.group(0)))
                break
    return mapped


def _named_target(words: list[str], targets: list[dict[str, Any]]) -> dict[str, Any] | None:
    named, _matched = _match(' '.join(words), targets, lambda target: target.get('name'))
    return named


def _target_for(
    words: list[str],
    targets: list[dict[str, Any]],
    *,
    standing: list[dict[str, Any]],
    last_target_id: str,
) -> dict[str, Any] | None:
    """The enemy the words name; with a pronoun or "the last one", the only one there is; else the hero's
    last target; else the only enemy standing."""

    named = _named_target(words, targets)
    if named is not None:
        return named
    rest = [word for word in words if word not in DETERMINERS]
    pointed = bool(rest) and (rest[0] in TARGET_PRONOUNS or rest[:2] == ['last', 'one'])
    if pointed and len(targets) == 1:
        return targets[0]
    last = next((target for target in targets if _text(target.get('id')) == last_target_id), None)
    if last is not None:
        return last
    in_targets = [target for target in targets if _text(target.get('id')) in {_text(enemy.get('id')) for enemy in standing}]
    return in_targets[0] if len(standing) == 1 and in_targets else None


def _aims_at_a_thing(words: list[str], targets: list[dict[str, Any]]) -> bool:
    """The words after the verb name an object, not anyone in the fight ("the rope")."""

    if _named_target(words, targets) is not None:
        return False
    rest = [word for word in words if word not in DETERMINERS]
    return bool(rest) and rest[0] in OBJECT_TARGET_WORDS


def _attack_action(actions: list[dict[str, Any]], verb: str, target: dict[str, Any] | None) -> dict[str, Any] | None:
    attacks = [action for action in actions if _text(action.get('type')) == 'attack' and action.get('offhand') is not True]
    if RANGED_ATTACK_VERBS.match(verb):
        attacks.sort(key=lambda action: (action.get('range') or {}).get('classification') != 'ranged')
    elif MELEE_ATTACK_VERBS.match(verb):
        attacks.sort(key=lambda action: (action.get('range') or {}).get('classification') == 'ranged')
    if target is not None:
        reaching = [
            action
            for action in attacks
            if any(option.get('id') == target.get('id') and option.get('available') for option in action.get('targets') or [])
        ]
        attacks = reaching or attacks
    return attacks[0] if attacks else None


def fight_words(
    state: Mapping[str, Any],
    bundle: Mapping[str, Any],
    *,
    message: str,
    last_target_id: str = '',
) -> FightWords:
    """How a typed message is taken on a fight turn (rules in the module docstring); ``bundle`` is the
    hero's legal-action bundle."""

    hero_name = _text(bundle.get('actorName'))
    if not bundle.get('isCurrentActor'):
        if is_speech(message, hero_name=hero_name):
            return FightWords('speech')
        current = _text(bundle.get('currentActorName'))
        return FightWords('refused', refusal=f"It is {current}'s turn." if current else 'It is not your turn.')
    if is_speech(message, hero_name=hero_name):
        return FightWords('speech')
    actions = [action for action in bundle.get('actions') or [] if isinstance(action, Mapping)]
    combat = state.get('combat') if isinstance(state.get('combat'), Mapping) else {}
    standing = [
        participant
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and _text(participant.get('team')).lower() == 'enemy' and participant_is_targetable(participant)
    ]
    mapped = _fight_clauses(message)
    if mapped and mapped[0][0] == 'advance':
        # "Close in and tackle him": the maneuver or attack that closes in first.
        joined = next((entry for entry in mapped[1:] if entry[0] in MOVE_FIRST_TYPES), None)
        if joined is not None:
            mapped = [(joined[0], joined[1] or mapped[0][1], joined[2])]
    for action_type, words, verb in mapped:
        if action_type == 'attack':
            enemies = [{'id': _text(enemy.get('id')), 'name': _text(enemy.get('name'))} for enemy in standing]
            if _aims_at_a_thing(words, enemies):
                continue
            target = _target_for(words, enemies, standing=standing, last_target_id=last_target_id)
            action = _attack_action(actions, verb, target)
        else:
            action = next((candidate for candidate in actions if _text(candidate.get('type')) == action_type), None)
            target = None
            if action is not None and action.get('requiresTarget'):
                target = _target_for(words, list(action.get('targets') or []), standing=standing, last_target_id=last_target_id)
        if action is None:
            return FightWords('refused', refusal='That is not something you can do in this fight.')
        if action.get('requiresTarget') and target is not None:
            option = next((option for option in action.get('targets') or [] if option.get('id') == target.get('id')), None)
            if option is None or not option.get('available'):
                return FightWords('refused', refusal=_text((option or {}).get('reason')) or _text(action.get('reason')) or 'That target is not available.')
        if not action.get('available'):
            return FightWords('refused', refusal=_text(action.get('reason')) or 'That is not available now.')
        return FightWords('action', action_id=_text(action.get('id')), target_id=_text((target or {}).get('id')) or None)
    free = _free_fight_words(message, hero_name=hero_name)
    if free:
        return FightWords(free)
    enemies = [{'id': _text(enemy.get('id')), 'name': _text(enemy.get('name'))} for enemy in standing]
    named = mentions(message, enemies, lambda enemy: enemy['name'])
    target = named[0] if len(named) == 1 else enemies[0] if len(enemies) == 1 else None
    return FightWords('improvised', action_id=IMPROVISE_ACTION_ID, target_id=(target or {}).get('id'))
