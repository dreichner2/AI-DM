"""Plans that change the scene: nine effects, tagged scene objects, authored affordances and scene facts.

Effects. Each has one engine meaning:

- ``obstruct`` a way out or a path (an object tagged ``exit``/``barrier``, or a ``heavy``/``vehicle`` one that
  ``guards`` another): the way is ``blocked``; anyone who needs it spends an action clearing it (Strength or
  Dexterity against DC 12) before passing.
- ``distract`` the people or enemies in the zone (with a ``noise``/``fire``/``light`` thing, an ``animal`` or a
  ``crowd``, or words to one person): ``distracted`` until the end of the next round — no reactions, the next attack
  against each has advantage; outside a fight a distracted person does not notice the next action taken there.
- ``expose`` what is hidden in the zone (a ``light``/``fire`` thing raised or thrown, which is then spent; or a
  ``cover``/``container`` searched): hidden objects, items and enemies are revealed and concealment ends there.
- ``conceal`` a hero (the existing ``hidden`` condition) or an item (``concealed``: an enemy must spend an action
  searching before it can take it) in a ``cover``, ``container``, ``crowd`` or ``vehicle``.
- ``separate`` one enemy or person from the rest (with an ``animal``, ``vehicle``, ``crowd`` or ``barrier``):
  ``cut_off`` until the end of the next round; a person cut off may be told what is ``private``.
- ``restrain``: W7's grapple or bind, or (authored) ``restrained`` by an object until it breaks free.
- ``destroy`` a ``fragile``/``light``/``rope``/``barrier`` object: ``broken`` for good; a light leaves its zone
  ``dark`` (ranged attacks have disadvantage, hiding needs no cover); a rope leaves what it held ``loose`` (an
  ``animal`` or ``vehicle`` it held gives a free ``separate``); a barrier leaves the way open for good.
- ``protect`` a person or item: the hero is ``guarding`` it until their next turn starts.
- ``take`` an item in reach that no conscious enemy holds.

Scene objects carry ``tags`` and may author ``affordances`` (:func:`object_affordances`). Without one, an effect on a
tagged thing in reach resolves by :data:`TABLE` (DC 12 unless stated); an impossible pair falls through to the
improvised floor. Every resolved effect records a scene fact (``currentScene.facts``, sentences, persisted with the
place; ``factKeys`` says which state holds each one so it goes when that state changes back), a ``change`` beat, and
a ``Did`` line for a narrating model (``Effect — obstruct: the yard gate is barred (Athletics 14 vs DC 10)``).

How words become a plan (:func:`plan_from_words`, after offered actions, conversation topics and fight actions had
their chance): words that name a still hidden object first (amendment 5); then an affordance's ``keywords`` (one with
the object's name, or two); then the verb families of :data:`VERB_FAMILIES` with a thing in reach named in the
clause. With an AI narrator, words still unplaced (and not speech) go once to the pre-narration helper
(:func:`helper_plan`), which may only name one of the nine effects and the ids of the things in reach.

A plan is resolved by :func:`resolve_plan` into one ``scene.effect`` change (plus the combat effects it lays on
participants); the applier calls :func:`apply_effect_change` and :func:`plan_followup_changes` adds its flags,
dispositions, memories, Inspiration and any encounter it starts.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Callable, Iterable, Mapping

from flask import current_app, has_app_context

from aidm_server.contracts import ProviderRequest
from aidm_server.game_state.extraction.schemas import extract_json_object
from aidm_server.game_state.models import find_actor, stable_change_id
from aidm_server.game_state.zones import (
    FIGHT_ZONE_FLAG,
    hero_zone_id,
    known_zone_ids,
    item_zone_id,
    npc_zone_id,
    object_zone_id,
    place_zones,
    present_npcs,
    zone_reach,
    zone_steps,
)
from aidm_server.game_state.zones import the_name as named_as_offered
from aidm_server.interactables import object_rolls
from aidm_server.llm_providers import get_helper_provider, safe_mode_active
from aidm_server.provider_priority import foreground_provider_reservation
from aidm_server.telemetry import telemetry_event

EFFECTS = ('obstruct', 'distract', 'expose', 'conceal', 'separate', 'restrain', 'destroy', 'protect', 'take')
OBJECT_TAGS = frozenset(
    {'exit', 'barrier', 'light', 'fire', 'cover', 'heavy', 'fragile', 'rope', 'animal', 'crowd', 'vehicle', 'container', 'noise', 'high_ground'}
)
AFFORDANCE_COSTS = ('action', 'bonus_action', 'free')
AFFORDANCE_TARGETS = ('enemy', 'zone')
# The typed intent of a plan (clicked or typed), the declared action it becomes, and the change it makes.
PLAN_KIND = 'plan'
PLAN_DECLARED = 'scene.effect'
EFFECT_CHANGE = 'scene.effect'
EFFECT_SOURCE = 'scene_plans'
# The combat changes a plan makes (effects and conditions on participants) are the combat engine's.
COMBAT_ENGINE_SOURCE = 'combat_action_engine'
# Fight actions: an authored affordance is ``plan.<objectId>.<affordanceId>``; typed words carry their plan.
PLAN_ACTION_PREFIX = 'plan.'
PLAN_WORDS_ACTION_ID = 'plan.words'
PLAN_ACTION_TYPE = 'plan'
FACTS_KEY = 'facts'
FACT_KEYS_KEY = 'factKeys'
ZONE_CONDITIONS_KEY = 'zoneConditions'
CUT_OFF_KEY = 'cutOff'
DISTRACTED_KEY = 'distracted'
GUARDS_KEY = 'guards'
CLOCKS_STARTED_KEY = 'clocksStarted'
# The key of a place without rooms in ``zoneConditions``.
WHOLE_PLACE = '*'
DEFAULT_DC = 12
CLEAR_DC = 12
RESTRAIN_ESCAPE_DC = 12
WELL_PLAYED = 'well played together'
SECRET_UNCOVERED = 'a secret uncovered'
# Combat effects an effect lays on participants.
DISTRACTED_SOURCE = 'scene_distracted'
CUT_OFF_SOURCE = 'scene_cut_off'
GUARDING_SOURCE = 'scene_guarding'
RESTRAINED_SOURCE = 'scene_restrained'
TRANSIENT_FACT = 't:'
ACTIVE_COMBAT_STATUSES = frozenset({'starting', 'active'})
# Effect -> ((tags it works on, check: None | skill | (skill, skill) the better), ...).
TABLE: dict[str, tuple[tuple[frozenset[str], Any], ...]] = {
    'obstruct': ((frozenset({'barrier', 'exit', 'heavy', 'vehicle'}), 'athletics'),),
    'distract': (
        (frozenset({'noise', 'fire', 'light'}), None),
        (frozenset({'animal'}), 'animal_handling'),
        (frozenset({'crowd'}), 'deception'),
    ),
    'expose': ((frozenset({'light', 'fire'}), None), (frozenset({'cover', 'container'}), 'investigation')),
    'conceal': ((frozenset({'cover', 'container', 'crowd', 'vehicle'}), 'stealth'),),
    'separate': (
        (frozenset({'animal', 'vehicle'}), ('animal_handling', 'athletics')),
        (frozenset({'crowd'}), 'persuasion'),
        (frozenset({'barrier'}), 'athletics'),
    ),
    'destroy': ((frozenset({'fragile', 'light', 'rope'}), None), (frozenset({'barrier'}), 'athletics')),
}
# Effects whose object is the thing acted on; the others act on a person, enemy, hero or item ("subject").
OBJECT_EFFECTS = frozenset({'obstruct', 'expose', 'destroy'})
# Verb families of the first pass (whole words, any tense), longest phrase first within each family.
VERB_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('separate', ('cut off', 'cut loose', 'drive off', 'stampede', 'scatter', 'split')),
    ('destroy', ('put out', 'smash', 'break', 'snuff', 'douse', 'cut')),
    ('protect', ('stand over', 'shield', 'guard')),
    ('take', ('pick up', 'grab', 'snatch', 'seize')),
    ('expose', ('light up', 'shine', 'reveal', 'uncover', 'throw', 'toss', 'hurl')),
    ('obstruct', ('barricade', 'block', 'bar', 'shut', 'close', 'jam', 'wedge')),
    ('distract', ('shout at', 'distract', 'ring', 'bang', 'wave', 'spook', 'startle')),
    ('conceal', ('hide', 'stash', 'tuck', 'cover')),
)
IRREGULAR = {
    'cut': {'cut', 'cuts', 'cutting'},
    'put': {'put', 'puts', 'putting'},
    'shut': {'shut', 'shuts', 'shutting'},
    'split': {'split', 'splits', 'splitting'},
    'hide': {'hide', 'hides', 'hid', 'hidden', 'hiding'},
    'break': {'break', 'breaks', 'broke', 'broken', 'breaking'},
    'throw': {'throw', 'throws', 'threw', 'thrown', 'throwing'},
    'ring': {'ring', 'rings', 'rang', 'rung', 'ringing'},
    'drive': {'drive', 'drives', 'drove', 'driven', 'driving'},
    'stand': {'stand', 'stands', 'stood', 'standing'},
    'shine': {'shine', 'shines', 'shone', 'shined', 'shining'},
    'light': {'light', 'lights', 'lit', 'lighted', 'lighting'},
    'seize': {'seize', 'seizes', 'seized', 'seizing'},
}
DOUBLED = {'bar', 'jam', 'grab', 'wedge', 'snuff', 'stash', 'tuck', 'bang'}
MAX_LEAD_IN = 8
MAX_NAME_WORDS = 6
HELPER_SYSTEM_MESSAGE = (
    'You read what a tabletop hero tries to do and name the one effect it is, using only the ids you are given. '
    'Return strict JSON only. Never decide whether it works.'
)
HELPER_RAW_PREVIEW_LIMIT = 2000
WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)*")
QUOTED = re.compile(r'"[^"]*"?|“[^”]*”?')
NEGATED = re.compile(r"\b(?:not|never|without|would)\b|n't\b|\b(?:before|if|should|could|can)\s+i\b")
ARTICLES = frozenset({'the', 'a', 'an', 'some', 'my', 'your', 'our', 'his', 'her', 'their', 'its', 'that', 'this'})


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_text(value)] if _text(value) else []
    return [_text(item) for item in value if _text(item)] if isinstance(value, (list, tuple)) else []


def _scene(state: Mapping[str, Any]) -> dict[str, Any]:
    scene = state.get('currentScene') if isinstance(state, Mapping) else None
    return scene if isinstance(scene, dict) else {}


def _combat(state: Mapping[str, Any]) -> dict[str, Any]:
    combat = state.get('combat') if isinstance(state, Mapping) else None
    return combat if isinstance(combat, dict) else {}


def fighting(state: Mapping[str, Any]) -> bool:
    return _text(_combat(state).get('status')).lower() in ACTIVE_COMBAT_STATUSES


def capitalized(text: str) -> str:
    return f'{text[:1].upper()}{text[1:]}'


def the_name(record: Mapping[str, Any]) -> str:
    """``the yard gate`` for "Yard Gate"; a proper name stands alone; "The Last Stone" keeps its article in lower case."""

    name = _text(record.get('name')) or _text(record.get('id')).replace('_', ' ')
    if record.get('proper') is True or record.get('kind') in {'person', 'hero'}:
        return name
    if name.lower().startswith('the '):
        return f'the {name[4:].lower()}' if record.get('kind') in {'object', 'item'} else f'the {name[4:]}'
    return f'the {name.lower()}' if record.get('kind') in {'object', 'item', 'enemy'} else f'the {name}'


def _verb_forms(verb: str) -> frozenset[str]:
    if verb in IRREGULAR:
        return frozenset(IRREGULAR[verb])
    stem = verb[:-1] if verb.endswith('e') else verb
    forms = {verb, f'{verb}s', f'{stem}ed', f'{stem}ing'}
    if verb.endswith(('sh', 'ch', 'x', 's')):
        forms.add(f'{verb}es')
    if verb in DOUBLED and not verb.endswith('e') and verb[-1] not in 'aeiou' and verb[-2:-1] in set('aeiou'):
        forms |= {f'{verb}{verb[-1]}ed', f'{verb}{verb[-1]}ing'}
    return frozenset(forms)


# Records ----------------------------------------------------------------------------------------------------------


def object_tags(entry: Mapping[str, Any]) -> set[str]:
    return {tag.lower() for tag in _strings(entry.get('tags')) if tag.lower() in OBJECT_TAGS}


def normalize_affordance(raw: Any) -> dict[str, Any] | None:
    """One authored affordance with its defaults; None when it has no id, label or known effect."""

    if not isinstance(raw, dict) or not _text(raw.get('id')) or not _text(raw.get('label')):
        return None
    effect = _text(raw.get('effect')).lower()
    if effect not in EFFECTS:
        return None
    check = raw.get('check') if isinstance(raw.get('check'), dict) else None
    cost = _text(raw.get('cost')).lower() or 'action'
    return {
        'id': _text(raw['id']),
        'effect': effect,
        'label': _text(raw['label']),
        'keywords': [word.lower() for word in _strings(raw.get('keywords'))],
        'check': (
            {'skill': _text(check.get('skill')).lower() or None, 'ability': _text(check.get('ability')).lower() or None, 'dc': int(check.get('dc') or DEFAULT_DC)}
            if check
            else None
        ),
        'cost': cost if cost in AFFORDANCE_COSTS else 'action',
        'inFight': raw.get('inFight') is not False,
        'outsideFight': raw.get('outsideFight') is not False,
        'once': raw.get('once') is True,
        'requires': deepcopy(raw.get('requires')) if isinstance(raw.get('requires'), dict) else {},
        'target': _text(raw.get('target')) or None,
        'result': deepcopy(raw.get('result')) if isinstance(raw.get('result'), dict) else {},
        'failResult': deepcopy(raw.get('failResult')) if isinstance(raw.get('failResult'), dict) else {},
        'onFail': deepcopy(raw.get('onFail')) if isinstance(raw.get('onFail'), dict) else None,
        'inspires': _text(raw.get('inspires')) or None,
    }


def object_affordances(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [affordance for raw in entry.get('affordances') or [] if (affordance := normalize_affordance(raw))]


def _objects(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    scene = _scene(state)
    return [entry for key in ('interactables', 'hazards') for entry in _dicts(scene.get(key)) if _text(entry.get('id'))]


def _object(state: Mapping[str, Any], object_id: Any) -> dict[str, Any]:
    return next((entry for entry in _objects(state) if _text(entry.get('id')) == _text(object_id)), {})


def _visible(entry: Mapping[str, Any]) -> bool:
    return entry.get('gmOnly') is not True and not (entry.get('hidden') is True and entry.get('playerKnown') is not True)


# Scene facts and zone conditions ----------------------------------------------------------------------------------


def scene_facts(state: Mapping[str, Any]) -> list[str]:
    """What has changed here, as sentences (``currentScene.facts``)."""

    return _strings(_scene(state).get(FACTS_KEY))


def zone_key(state: Mapping[str, Any], zone_id: Any) -> str:
    return _text(zone_id) if place_zones(state) else WHOLE_PLACE


def zone_conditions(state: Mapping[str, Any], zone_id: Any) -> list[str]:
    conditions = _dict(_scene(state).get(ZONE_CONDITIONS_KEY))
    return _strings(conditions.get(zone_key(state, zone_id)))


def fight_zone(state: Mapping[str, Any]) -> str:
    return _text(_dict(_combat(state).get('flags')).get(FIGHT_ZONE_FLAG))


def set_fact(scene: dict[str, Any], key: str, text: str) -> None:
    """Record a fact under ``key``, replacing the one that key held."""

    keys = _dict(scene.get(FACT_KEYS_KEY))
    facts = _strings(scene.get(FACTS_KEY))
    old = keys.get(key)
    if old:
        facts = [fact for fact in facts if fact != old]
    if text:
        facts = [fact for fact in facts if fact != text] + [text]
        keys[key] = text
    else:
        keys.pop(key, None)
    scene[FACTS_KEY] = facts
    scene[FACT_KEYS_KEY] = keys


def clear_transient_facts(scene: dict[str, Any]) -> None:
    """A fight's passing effects (distracted, cut off, guarding, restrained) end with it."""

    for key in [key for key in _dict(scene.get(FACT_KEYS_KEY)) if key.startswith(TRANSIENT_FACT)]:
        set_fact(scene, key, '')
    for key in (CUT_OFF_KEY, DISTRACTED_KEY, GUARDS_KEY):
        scene.pop(key, None)


def is_cut_off(state: Mapping[str, Any], npc_id: Any) -> bool:
    """A person a ``separate`` drew apart from the others (private topics may be told among them)."""

    return _text(npc_id) in _strings(_scene(state).get(CUT_OFF_KEY))


def guards(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Outside a fight: who each hero stands guard over, ``{heroId: {"targetId", "kind", "name"}}``."""

    return {key: value for key, value in _dict(_scene(state).get(GUARDS_KEY)).items() if isinstance(value, dict)}


# What is in reach -------------------------------------------------------------------------------------------------


def _hero_items(state: Mapping[str, Any], hero_id: Any) -> list[dict[str, Any]]:
    hero = find_actor(dict(state), hero_id) or {}
    return _dicts(_dict(hero.get('inventory')).get('items'))


def _participants(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _dicts(_combat(state).get('participants'))


def _standing(participant: Mapping[str, Any]) -> bool:
    from aidm_server.combat.state import participant_is_targetable

    return participant_is_targetable(participant)


def things(state: Mapping[str, Any], actor_id: str) -> list[dict[str, Any]]:
    """Every thing the acting hero can see here: ``{"kind", "id", "name", "zoneId", "record", "steps"}`` with ``kind``
    ``object``, ``item`` (lying here, or ``held`` by the hero), ``person``, ``enemy`` or ``hero``; ``steps`` is how
    many rooms away it is (0 here, 1 next door) — a fight's things are in its zone."""

    zones = place_zones(state)
    here = fight_zone(state) or hero_zone_id(state, actor_id, zones)

    def steps(zone_id: str) -> int:
        if not zones or not zone_id:
            return 0
        found = zone_steps(zones, here, zone_id)
        return 99 if found is None else found

    found: list[dict[str, Any]] = []
    for entry in _objects(state):
        if _visible(entry):
            zone_id = object_zone_id(state, entry.get('id'), zones)
            found.append({'kind': 'object', 'id': _text(entry['id']), 'name': _text(entry.get('name')), 'zoneId': zone_id, 'record': entry, 'steps': steps(zone_id)})
    for item in _dicts(_scene(state).get('items')):
        if _text(item.get('id')) and _text(item.get('name')) and not (item.get('hidden') is True and item.get('playerKnown') is not True):
            zone_id = item_zone_id(state, item, zones)
            found.append({'kind': 'item', 'id': _text(item['id']), 'name': _text(item['name']), 'zoneId': zone_id, 'record': item, 'steps': steps(zone_id)})
    for item in _hero_items(state, actor_id):
        if _text(item.get('id')) and _text(item.get('name')):
            found.append({'kind': 'item', 'id': _text(item['id']), 'name': _text(item['name']), 'zoneId': here, 'record': item, 'steps': 0, 'held': True})
    for npc in present_npcs(state):
        if npc.get('outOfPlay') is True:
            continue
        zone_id = npc_zone_id(state, npc.get('id'), zones)
        found.append({'kind': 'person', 'id': _text(npc.get('id')), 'name': _text(npc.get('name')), 'zoneId': zone_id, 'record': npc, 'steps': steps(zone_id)})
    if fighting(state):
        for participant in _participants(state):
            if _text(participant.get('team')).lower() == 'enemy' and _standing(participant) and _text(participant.get('name')):
                found.append({'kind': 'enemy', 'id': _text(participant['id']), 'name': _text(participant['name']), 'zoneId': here, 'record': participant, 'steps': 0})
    for hero in _dicts(state.get('playerCharacters')):
        if _text(hero.get('id')) and _text(hero.get('name')):
            zone_id = hero_zone_id(state, hero['id'], zones)
            found.append({'kind': 'hero', 'id': _text(hero['id']), 'name': _text(hero['name']), 'zoneId': zone_id, 'record': hero, 'steps': steps(zone_id)})
    if fighting(state):
        return [thing for thing in found if thing['steps'] == 0]
    # Outside a fight a thing in a room the party knows may be named: next door the hero steps in first, further off
    # the plan is refused with where it is (:mod:`aidm_server.game_state.orchestration.zone_turns`).
    known = set(known_zone_ids(state, zones))
    return [thing for thing in found if thing['steps'] <= 1 or (thing['steps'] < 99 and thing['zoneId'] in known)]


def _thing(state: Mapping[str, Any], actor_id: str, thing_id: Any) -> dict[str, Any] | None:
    return next((thing for thing in things(state, actor_id) if thing['id'] == _text(thing_id)), None)


def _carried_by_enemy(state: Mapping[str, Any], item_id: str) -> dict[str, Any] | None:
    return next(
        (participant for participant in _participants(state) if item_id in {_text(item.get('id')) for item in _dicts(participant.get('carrying'))}),
        None,
    )


# Validity: affordances and the table ------------------------------------------------------------------------------


def _flags_hold(state: Mapping[str, Any], wanted: Any) -> bool:
    flags = state.get('flags') if isinstance(state.get('flags'), dict) else {}
    if not isinstance(wanted, dict):
        return True
    return all((flags.get(key) if key in flags else False) == value for key, value in wanted.items())


def affordance_refusal(state: Mapping[str, Any], entry: Mapping[str, Any], affordance: Mapping[str, Any]) -> str:
    """Why this affordance cannot be taken now; '' when it can (reach and turn are judged elsewhere)."""

    if affordance['once'] and affordance['id'] in _strings(entry.get('affordancesUsed')):
        return 'That has been done already.'
    if fighting(state) and not affordance['inFight']:
        return 'Not while the fight is on.'
    if not fighting(state) and not affordance['outsideFight']:
        return 'Only in a fight.'
    if not _flags_hold(state, _dict(affordance['requires']).get('flags')):
        return "That can't be done yet."
    return ''


def _table_check(effect: str, tags: set[str]) -> tuple[bool, Any]:
    """``(possible, check)`` of an effect on an object with these tags (``check``: None, a skill or two)."""

    for works_on, check in TABLE.get(effect, ()):
        if tags & works_on:
            return True, check
    return False, None


def _plan_subject_default(state: Mapping[str, Any], actor_id: str, effect: str) -> dict[str, Any] | None:
    """The one enemy (in a fight) a ``separate`` or ``distract`` aims at when none is named."""

    if effect != 'separate':
        return None
    enemies = [thing for thing in things(state, actor_id) if thing['kind'] == 'enemy']
    people = [thing for thing in things(state, actor_id) if thing['kind'] == 'person' and thing['steps'] == 0]
    pool = enemies if fighting(state) else people
    return pool[0] if len(pool) == 1 else None


def table_plan(
    state: Mapping[str, Any],
    actor_id: str,
    effect: str,
    *,
    object_id: str | None,
    subject_id: str | None,
) -> dict[str, Any] | None:
    """The plan an effect on these things makes by the table, or None when the pair is impossible."""

    obj = _thing(state, actor_id, object_id) if object_id else None
    subject = _thing(state, actor_id, subject_id) if subject_id else None
    if obj is not None and obj['kind'] != 'object':
        obj, subject = (None, obj) if subject is None else (None, None)
    if subject is not None and subject['kind'] == 'object' and obj is None:
        obj, subject = subject, None
    if effect == 'take':
        if subject is None or subject['kind'] != 'item' or subject.get('held'):
            return None
        carrier = _carried_by_enemy(state, subject['id'])
        if carrier is not None and _standing(carrier):
            return None
        return {'effect': 'take', 'subjectId': subject['id'], 'subjectKind': 'item'}
    if effect == 'protect':
        if subject is None or subject['kind'] not in {'person', 'item', 'hero'} or subject['id'] == actor_id:
            return None
        return {'effect': 'protect', 'subjectId': subject['id'], 'subjectKind': subject['kind']}
    if effect == 'distract' and obj is None:
        if subject is None or subject['kind'] != 'person':
            return None
        return {'effect': 'distract', 'subjectId': subject['id'], 'subjectKind': 'person', 'check': 'deception'}
    if effect not in TABLE or obj is None:
        return None
    possible, check = _table_check(effect, object_tags(obj['record']))
    if not possible or obj['record'].get('broken') is True:
        return None
    plan: dict[str, Any] = {'effect': effect, 'objectId': obj['id']}
    if check:
        plan['check'] = check
    if effect == 'conceal':
        subject = subject or _thing(state, actor_id, actor_id)
        if subject is None or subject['kind'] not in {'hero', 'item'}:
            return None
    elif effect == 'separate':
        subject = subject or _plan_subject_default(state, actor_id, effect)
        if subject is None or subject['kind'] not in {'enemy', 'person'}:
            return None
    elif effect == 'distract' and subject is not None and subject['kind'] not in {'enemy', 'person'}:
        subject = None
    elif effect in OBJECT_EFFECTS:
        subject = None
    if subject is not None:
        plan.update(subjectId=subject['id'], subjectKind=subject['kind'])
    return plan


def affordance_plan(state: Mapping[str, Any], object_id: str, affordance_id: str) -> dict[str, Any] | None:
    entry = _object(state, object_id)
    affordance = next((item for item in object_affordances(entry) if item['id'] == affordance_id), None)
    if not entry or affordance is None:
        return None
    return {'effect': affordance['effect'], 'objectId': _text(entry['id']), 'affordanceId': affordance['id']}


# Offered affordances ----------------------------------------------------------------------------------------------


def affordance_offers(state: Mapping[str, Any], hero: Mapping[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """The authored affordances offered to the hero, by object id (``offeredActions`` entries of action ``plan``).

    An affordance on an object the hero can see, in reach or next door outside a fight, whose ``requires`` hold and
    that is not used up (``once``). At most two are marked ``suggested`` at a time, the rest wait under ``More…``.
    """

    hero_id = _text((hero or {}).get('id'))
    if not hero_id:
        return {}
    in_reach = {thing['id']: thing for thing in things(state, hero_id) if thing['kind'] == 'object' and thing['steps'] <= 1}
    offers: dict[str, list[dict[str, Any]]] = {}
    suggested = 0
    for entry in _objects(state):
        thing = in_reach.get(_text(entry.get('id')))
        if thing is None:
            continue
        for affordance in object_affordances(entry):
            refusal = affordance_refusal(state, entry, affordance)
            if refusal == 'That has been done already.' or (fighting(state) and not affordance['inFight']):
                continue
            offer: dict[str, Any] = {
                'action': PLAN_ACTION_TYPE,
                'planId': affordance['id'],
                'label': affordance['label'],
                'authored': True,
                'legal': not refusal,
                'done': False,
                'effect': affordance['effect'],
            }
            if affordance['check']:
                offer['check'] = {key: value for key, value in affordance['check'].items() if key in {'skill', 'ability'} and value}
            if refusal:
                offer['reason'] = refusal
            elif suggested < 2:
                offer['suggested'] = True
                suggested += 1
            offers.setdefault(_text(entry['id']), []).append(offer)
    return offers


# Words: the first pass --------------------------------------------------------------------------------------------


def _clauses(message: str) -> list[list[str]]:
    text = QUOTED.sub(' ; ', _text(message).lower().replace('’', "'"))
    sentences = re.split(r'[.!?]+', text)
    clauses = []
    for sentence in sentences[:2]:
        if '?' in sentence:
            continue
        for part in re.split(r'\s+and\s+|\s*,\s*then\s+|\s+then\s+|\s*;\s*', sentence):
            if NEGATED.search(part):
                continue
            words = WORD.findall(part)
            if words:
                clauses.append(words)
    return clauses


def _name_words(name: str) -> list[str]:
    lowered = re.sub(r"['’]s\b", '', name.lower())
    return [word for word in re.findall(r'[a-z0-9]+', lowered) if word not in {'the', 'a', 'an'}]


def _named(words: list[str], pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The things the words name, in order, each once: a run of whole words of a thing's name (one of three or more
    letters) that names exactly that thing."""

    found: list[dict[str, Any]] = []
    index = 0
    while index < len(words):
        if words[index] in ARTICLES:
            index += 1
            continue
        match = None
        for size in range(min(MAX_NAME_WORDS, len(words) - index), 0, -1):
            run = [word for word in words[index:index + size]]
            if not any(len(word) >= 3 for word in run):
                continue
            named = [
                thing
                for thing in pool
                if any(_name_words(thing['name'])[start:start + size] == run for start in range(len(_name_words(thing['name'])) - size + 1))
            ]
            if len(named) == 1:
                match = (size, named[0])
                break
            if named:
                break
        if match:
            size, thing = match
            if all(thing is not seen for seen in found):
                found.append(thing)
            index += size
        else:
            index += 1
    return found


def named_things(state: Mapping[str, Any], words: Iterable[str], *, actor_id: str) -> list[dict[str, Any]]:
    """The things in the hero's reach the words name (see :func:`_named`)."""

    return _named(list(words), things(state, actor_id))


def _verb_at(words: list[str], index: int) -> tuple[str, str, int] | None:
    """``(effect, verb phrase, words it took)`` for a verb family starting at ``index``."""

    for effect, phrases in VERB_FAMILIES:
        for phrase in phrases:
            head, _space, particle = phrase.partition(' ')
            if words[index] not in _verb_forms(head):
                continue
            if not particle:
                if head == 'cut' and 'loose' in words[index + 1:] or head == 'cut' and 'off' in words[index + 1:]:
                    continue
                return effect, phrase, 1
            rest = words[index + 1:]
            if rest[:1] == [particle]:
                return effect, phrase, 2
            if particle in rest[:6] and particle not in {'at', 'over'}:
                return effect, phrase, 1
    return None


def hidden_named(state: Mapping[str, Any], actor_id: str, message: str) -> dict[str, Any] | None:
    """Amendment 5: the still hidden object the words name (its whole name, or two of its name words), with the words
    as typed (``{"object", "typed"}``); None when they name none."""

    text = QUOTED.sub(' ', _text(message).lower())
    words = WORD.findall(text)
    # Words that name a thing in sight ("the mail wagon") name that, even when a hidden thing shares them.
    seen = [set(_name_words(_text(entry.get('name')))) for entry in _objects(state) if _visible(entry)]
    for entry in _objects(state):
        if _visible(entry) or entry.get('gmOnly') is True:
            continue
        name = _name_words(_text(entry.get('name')))
        if not name:
            continue
        for start in range(len(words)):
            run = []
            index = start
            while index < len(words) and words[index] in name and len(run) < len(name):
                run.append(words[index])
                index += 1
            if (run == name or len(set(run)) >= 2) and not any(set(run) <= visible for visible in seen):
                return {'object': entry, 'typed': ' '.join(run)}
    return None


def reveal_action(state: Mapping[str, Any], actor_id: str, hidden: Mapping[str, Any]) -> dict[str, Any] | None:
    """The offered action on a visible object in reach or next door whose authored ``reveals`` link uncovers this
    hidden object: ``{"object", "action"}``; None when there is none."""

    from aidm_server.interactables import available_actions_for_target, interactable_actor_context

    hidden_id = _text(hidden.get('id'))
    hero = find_actor(dict(state), actor_id) or {}
    location_id = _text(_scene(state).get('locationId'))
    for thing in things(state, actor_id):
        if thing['kind'] != 'object':
            continue
        reveals = thing['record'].get('reveals')
        # A plain list is what a search of the thing uncovers (as ``interactables._action_reveals`` reads it).
        by_action = reveals if isinstance(reveals, dict) else {'search': reveals} if isinstance(reveals, list) else {}
        for action, ids in by_action.items():
            if hidden_id not in _strings(ids):
                continue
            options = available_actions_for_target(
                state, location_id=location_id, target_id=thing['id'], actor=interactable_actor_context(state, hero)
            ).get('actions') or []
            if any(option.get('action') == action and (option.get('legal') or option.get('requiresCheck')) for option in options):
                return {'object': thing['record'], 'action': action}
    return None


def _keyword_plan(state: Mapping[str, Any], actor_id: str, clause: list[str]) -> dict[str, Any] | None:
    """An affordance whose keywords the clause holds: one keyword and the object's name, or two keywords."""

    scored = []
    words = set(clause)
    for thing in things(state, actor_id):
        if thing['kind'] != 'object':
            continue
        named = bool(_named(clause, [thing]))
        for affordance in object_affordances(thing['record']):
            if affordance_refusal(state, thing['record'], affordance):
                continue
            hits = sum(1 for keyword in affordance['keywords'] if keyword in words)
            if hits >= 2 or (hits >= 1 and named):
                scored.append((hits + (1 if named else 0), thing, affordance))
    if not scored:
        return None
    best = max(score for score, _thing_found, _affordance in scored)
    top = [(thing, affordance) for score, thing, affordance in scored if score == best]
    if len(top) != 1:
        return None
    thing, affordance = top[0]
    return {'effect': affordance['effect'], 'objectId': thing['id'], 'affordanceId': affordance['id']}


def _verb_plan(state: Mapping[str, Any], actor_id: str, clause: list[str]) -> dict[str, Any] | None:
    pool = things(state, actor_id)
    for index in range(min(MAX_LEAD_IN + 1, len(clause))):
        found = _verb_at(clause, index)
        if not found:
            continue
        effect, phrase, taken = found
        # What the clause names after the verb, then (a means named first: "with the drovers I…") before it.
        named = _named(clause[index + taken:], pool)
        named.extend(thing for thing in _named(clause[:index], pool) if thing['kind'] == 'object' and all(thing is not seen for seen in named))
        objects = [thing for thing in named if thing['kind'] == 'object']
        others = [thing for thing in named if thing['kind'] != 'object']
        if phrase == 'cover' and any(thing['kind'] in {'person', 'hero'} and thing['id'] != actor_id for thing in others):
            effect = 'protect'
        if effect == 'expose' and phrase in {'throw', 'toss', 'hurl'} and not any(object_tags(thing['record']) & {'light', 'fire'} for thing in objects):
            return None
        if effect == 'destroy' and phrase == 'cut' and objects and 'rope' not in object_tags(objects[0]['record']):
            return None
        if effect == 'take' and not others:
            return None
        obj = objects[0] if objects else None
        subject = others[0] if others else None
        if effect == 'expose' and obj and len(objects) > 1 and not object_tags(obj['record']) & {'light', 'fire'}:
            obj = objects[1]
        plan = table_plan(state, actor_id, effect, object_id=obj['id'] if obj else None, subject_id=subject['id'] if subject else None)
        if plan is not None:
            return plan
        if not named:
            continue
        return None
    return None


def plan_from_words(state: Mapping[str, Any], *, actor_id: str, message: str) -> dict[str, Any] | None:
    """What the first pass makes of typed words (the module docstring's order): ``{"hidden": {...}, "reveal": ...}``
    for a hidden thing named, ``{"plan": {...}}`` for a plan, or None."""

    hidden = hidden_named(state, actor_id, message)
    if hidden is not None:
        return {'hidden': hidden, 'reveal': reveal_action(state, actor_id, hidden['object'])}
    for clause in _clauses(message):
        plan = _keyword_plan(state, actor_id, clause) or _verb_plan(state, actor_id, clause)
        if plan is not None:
            return {'plan': plan}
    return None


def plan_label(state: Mapping[str, Any], plan: Mapping[str, Any]) -> str:
    """``Shut and bar the yard gate`` for an affordance; by the table, the effect in plain words (``Put out the yard
    lantern``, ``Hide the road-stone fragment in the feed crate``, ``Guard Wenna Pike``)."""

    if _text(plan.get('affordanceId')):
        entry = _object(state, plan.get('objectId'))
        affordance = next((item for item in object_affordances(entry) if item['id'] == _text(plan['affordanceId'])), None)
        if affordance:
            return affordance['label']
    effect = _text(plan.get('effect'))
    entry = _object(state, plan.get('objectId')) if _text(plan.get('objectId')) else {}
    tags = object_tags(entry)
    thing = the_name({**entry, 'kind': 'object'}) if entry else ''
    kind = _text(plan.get('subjectKind'))
    subject = the_name({**_subject_record(state, plan.get('subjectId'), kind), 'kind': kind}) if _text(plan.get('subjectId')) else ''
    if effect == 'obstruct':
        return f"{'Bar' if tags & {'barrier', 'exit'} else 'Block the way with'} {thing}"
    if effect == 'destroy':
        return f"{'Put out' if 'light' in tags else 'Cut' if 'rope' in tags else 'Break'} {thing}"
    if effect == 'expose':
        return f'Light the place up with {thing}' if tags & {'light', 'fire'} else f'Search {thing} for what is hidden'
    if effect == 'conceal':
        where = 'in' if tags & {'container', 'vehicle'} else 'behind'
        return f'Hide {subject} {where} {thing}' if kind == 'item' else f'Hide {where} {thing}'
    if effect == 'separate':
        return f'Cut {subject} off with {thing}'
    if effect == 'distract':
        return f'Distract {subject}' if subject and not thing else f'Make a distraction with {thing}'
    if effect == 'restrain':
        return f'Restrain {subject} with {thing}'
    if effect == 'protect':
        return f'Guard {subject}'
    return f'Take {subject}'


def plan_intent(state: Mapping[str, Any], plan: Mapping[str, Any], *, message: str, source: str) -> dict[str, Any]:
    return {'kind': PLAN_KIND, 'source': source, 'text': message, 'plan': {key: value for key, value in plan.items() if value not in (None, '')}}


def plan_targets(state: Mapping[str, Any], plan: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(zone_id, subject)`` of what the plan is aimed at, for the step next door (``zone_turns``)."""

    targets = []
    zones = place_zones(state)
    if _text(plan.get('objectId')):
        entry = _object(state, plan['objectId'])
        targets.append((object_zone_id(state, plan['objectId'], zones), named_as_offered(entry.get('name') or plan['objectId'])))
    kind = _text(plan.get('subjectKind'))
    if kind == 'person':
        record = _subject_record(state, plan.get('subjectId'), kind)
        targets.append((npc_zone_id(state, plan.get('subjectId'), zones), _text(record.get('name'))))
    elif kind == 'item':
        item = next((item for item in _dicts(_scene(state).get('items')) if _text(item.get('id')) == _text(plan.get('subjectId'))), None)
        if item is not None:
            targets.append((item_zone_id(state, item, zones), named_as_offered(item.get('name'))))
    return [(zone_id, subject) for zone_id, subject in targets if zone_id]


# Words: the pre-narration helper ----------------------------------------------------------------------------------


def helper_enabled() -> bool:
    """The pre-narration helper may read a hero's words: an AI narrator is set up and the state helper is on."""

    if safe_mode_active():
        return False
    if has_app_context() and current_app.config.get('AIDM_ENV') == 'test':
        return bool(current_app.config.get('AIDM_STATE_PIPELINE_HELPER_IN_TESTS', False))
    if has_app_context():
        return bool(current_app.config.get('AIDM_STATE_PIPELINE_HELPER_ENABLED', True))
    return True


def plans_possible_here(state: Mapping[str, Any], actor_id: str) -> bool:
    """The place offers something to plan with: an object in reach with tags or affordances."""

    return any(
        thing['kind'] == 'object' and (object_tags(thing['record']) or object_affordances(thing['record']))
        for thing in things(state, actor_id)
    )


def _helper_prompt(words: str, pool: list[dict[str, Any]]) -> str:
    lines = '\n'.join(
        f"- {thing['id']}: {thing['name']} ({thing['kind']}{', tags ' + ', '.join(sorted(object_tags(thing['record']))) if thing['kind'] == 'object' and object_tags(thing['record']) else ''})"
        for thing in pool
    )
    return (
        f'The hero tries:\n"{_text(words)}"\n\n'
        f"Which ONE effect is it? One of: {', '.join(EFFECTS)}, or none.\n"
        'targetId is the thing acted on; withId (optional) the thing used to do it. Use only these ids:\n'
        f'{lines or "- none"}\n\n'
        'Answer {"effect": "...", "targetId": "...", "withId": "...", "summary": "<a few words>"}. Never say whether it works.'
    )


def _helper_debug(*, attempted: bool, model: str | None = None, raw: str | None = None, error: str | None = None, parsed: Any = None) -> dict[str, Any]:
    return {
        'source': 'plan_helper' if attempted else 'skipped',
        'reason': 'plan',
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


def helper_plan(
    state: Mapping[str, Any],
    *,
    actor_id: str,
    words: str,
    before_provider_call: Callable[[], None] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The plan the pre-narration helper reads in the words, once; None for none or an answer outside the nine effects
    and the ids it was given (and the plan the table then refuses). Returns the helper's debug record too."""

    # The helper is given only what stands in the hero's own room: a plan it names never walks the hero anywhere.
    pool = [thing for thing in things(state, actor_id) if thing['steps'] == 0]
    with foreground_provider_reservation() as activate_provider:
        if before_provider_call:
            before_provider_call()
        try:
            activate_provider()
            response = get_helper_provider().generate(ProviderRequest(prompt=_helper_prompt(words, pool), system_message=HELPER_SYSTEM_MESSAGE))
        except Exception as exc:
            telemetry_event('state_pipeline.plan_helper.failed', payload={'error': str(exc)[:300]}, severity='warning')
            return None, _helper_debug(attempted=True, error=str(exc)[:300])
    raw = str(response.text or '')
    payload = extract_json_object(raw)
    debug = _helper_debug(attempted=True, model=response.model, raw=raw, parsed=payload)
    ids = {thing['id'] for thing in pool}
    effect = _text((payload or {}).get('effect')).lower()
    target_id = _text((payload or {}).get('targetId'))
    with_id = _text((payload or {}).get('withId'))
    if effect not in EFFECTS or target_id not in ids or (with_id and with_id not in ids):
        return None, debug
    target = next(thing for thing in pool if thing['id'] == target_id)
    if target['kind'] == 'object':
        affordance = next((item for item in object_affordances(target['record']) if item['effect'] == effect and not affordance_refusal(state, target['record'], item)), None)
        if affordance is not None:
            return {'effect': effect, 'objectId': target_id, 'affordanceId': affordance['id'], 'summary': _text((payload or {}).get('summary'))[:120]}, debug
    plan = table_plan(state, actor_id, effect, object_id=with_id or (target_id if target['kind'] == 'object' else None), subject_id=target_id if target['kind'] != 'object' else (None if not with_id else target_id))
    if plan is None:
        return None, debug
    return {**plan, 'summary': _text((payload or {}).get('summary'))[:120]}, debug


# Resolution -------------------------------------------------------------------------------------------------------


def _subject_record(state: Mapping[str, Any], subject_id: Any, kind: Any) -> dict[str, Any]:
    subject_id = _text(subject_id)
    if kind == 'item':
        return next(
            (item for item in [*_dicts(_scene(state).get('items')), *(item for hero in _dicts(state.get('playerCharacters')) for item in _dicts(_dict(hero.get('inventory')).get('items')))] if _text(item.get('id')) == subject_id),
            {},
        )
    if kind == 'person':
        return next((npc for npc in [*_dicts(state.get('knownNpcs')), *_dicts(state.get('partyNpcs'))] if _text(npc.get('id')) == subject_id), {})
    if kind == 'enemy':
        return next((participant for participant in _participants(state) if _text(participant.get('id')) == subject_id), {})
    if kind == 'hero':
        return find_actor(dict(state), subject_id) or {}
    return {}


def _roll(state: Mapping[str, Any], actor_id: str, check: Any, *, dc: int, seed: str) -> dict[str, Any]:
    """The engine's roll of a plan's check (as an object check is rolled); with two skills, the better."""

    rolls = object_rolls(state)['check']
    skills = list(check) if isinstance(check, (list, tuple)) else [check]
    results = [rolls(actor_id, {'skill': skill, 'dc': dc, 'id': seed}, seed) for skill in skills if skill]
    return max(results, key=lambda result: result['total'])


def _check_spec(plan: Mapping[str, Any], affordance: Mapping[str, Any] | None) -> tuple[Any, int] | None:
    if affordance is not None:
        check = affordance.get('check')
        if not check:
            return None
        return (check.get('skill') or check.get('ability') or 'athletics'), int(check.get('dc') or DEFAULT_DC)
    check = plan.get('check')
    return (check, DEFAULT_DC) if check else None


def check_words(check: Mapping[str, Any] | None) -> str:
    """``Athletics 14 vs DC 10``."""

    if not isinstance(check, Mapping) or not isinstance(check.get('total'), int):
        return ''
    skill = _text(check.get('skill') or check.get('ability')).replace('_', ' ').title() or 'Check'
    return f"{skill} {check['total']} vs DC {check.get('dc')}"


def _fact_for(state: Mapping[str, Any], effect: str, *, obj: Mapping[str, Any], subject: Mapping[str, Any], actor: Mapping[str, Any], zone_name: str) -> str:
    """The built scene fact of a resolved effect (``The yard gate is barred.``)."""

    thing = capitalized(the_name({**obj, 'kind': 'object'})) if obj else ''
    who = capitalized(the_name(subject)) if subject else ''
    hero = _text(actor.get('name')) or 'The hero'
    if effect == 'obstruct':
        guarded = _text(obj.get('guards'))
        if guarded:
            return f"{capitalized(the_name({**_object(state, guarded), 'kind': 'object'}))} is blocked by {the_name({**obj, 'kind': 'object'})}."
        return f'{thing} is {"barred" if object_tags(obj) & {"exit", "barrier"} else "blocked"}.'
    if effect == 'destroy':
        tags = object_tags(obj)
        if 'light' in tags:
            return f"{thing} is out; {zone_name or 'it'} is dark."
        if 'rope' in tags:
            return f'{thing} is loose.'
        return f'{thing} is broken.'
    if effect == 'expose':
        return f'{thing} lights up {zone_name or "the place"}: nothing here is hidden now.' if object_tags(obj) & {'light', 'fire'} else 'Nothing here is hidden now.'
    if effect == 'conceal':
        return f"{who} is hidden {'in' if object_tags(obj) & {'container', 'vehicle'} else 'behind'} {the_name({**obj, 'kind': 'object'})}."
    if effect == 'separate':
        return f'{who} is cut off from the others.'
    if effect == 'distract':
        return f'{who} is looking the wrong way.' if subject else f"Everyone near {the_name({**obj, 'kind': 'object'})} is looking the wrong way."
    if effect == 'protect':
        return f'{who} is behind {hero}.'
    if effect == 'take':
        return f"{hero} has {the_name(subject)}."
    if effect == 'restrain':
        return f"{who} is caught in {the_name({**obj, 'kind': 'object'})}."
    return ''


def _effect_payload(participant_id: str, source: str, *, hero_id: str, hero_name: str, condition: str, turn_id: Any, **extra: Any) -> dict[str, Any]:
    names = {DISTRACTED_SOURCE: 'Distracted', CUT_OFF_SOURCE: 'Cut off', GUARDING_SOURCE: 'Guarding', RESTRAINED_SOURCE: 'Restrained'}
    modifiers = {DISTRACTED_SOURCE: {'attackedMode': 'advantage', 'cannotReact': True}, RESTRAINED_SOURCE: {'speedZero': True, 'attackedMode': 'advantage'}}
    durations = {
        DISTRACTED_SOURCE: {'rounds': 1, 'tickOn': 'target_turn_end'},
        CUT_OFF_SOURCE: {'rounds': 1, 'tickOn': 'target_turn_end'},
        GUARDING_SOURCE: {'rounds': 1, 'tickOn': 'caster_turn_start'},
    }
    effect = {
        'id': f'{source}:{turn_id}:{hero_id}:{participant_id}',
        'sourceId': source,
        'sourceName': f'{names[source]} ({hero_name})' if hero_name else names[source],
        'casterId': hero_id,
        'condition': condition,
        'modifiers': modifiers.get(source, {}),
        **({'duration': durations[source]} if source in durations else {}),
        **({'consumeOn': 'attacked'} if source == DISTRACTED_SOURCE else {}),
        **extra,
    }
    return effect


def _combat_effect_change(turn_id: Any, change_id: str, participant_id: str, effect: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        'id': stable_change_id(change_id, 'combat.effect.add', participant_id, effect['sourceId']),
        'turnId': turn_id,
        'type': 'combat.effect.add',
        'source': COMBAT_ENGINE_SOURCE,
        'participantId': participant_id,
        'effect': effect,
        'reason': reason,
        'visible': True,
    }


def _zone_name(state: Mapping[str, Any], zone_id: str) -> str:
    zone = next((zone for zone in place_zones(state) if zone['id'] == zone_id), None)
    if zone:
        name = zone['name']
        return f'the {name[4:].lower()}' if name.lower().startswith('the ') else name.lower()
    location = next((location for location in _dicts(state.get('locations')) if _text(location.get('id')) == _text(_scene(state).get('locationId'))), {})
    name = _text(location.get('name')) or 'the place'
    return f'the {name[4:]}' if name.lower().startswith('the ') else name


def _distracted_people(state: Mapping[str, Any], zone_id: str) -> list[dict[str, Any]]:
    zones = place_zones(state)
    return [npc for npc in present_npcs(state) if not zones or npc_zone_id(state, npc.get('id'), zones) == zone_id]


def resolve_plan(state: Mapping[str, Any], *, actor_id: str, plan: Mapping[str, Any], turn_id: Any) -> dict[str, Any]:
    """Resolve one plan for the hero: ``{"ok", "reason", "changes", "result"}``.

    ``changes`` are the ``scene.effect`` change and the combat effects it lays on participants; ``result`` is what
    the narration tells (``effect``, ``label``, ``success``, ``did``, ``text``, ``fact``). A plan that is not
    possible here is refused with its reason and changes nothing.
    """

    actor = find_actor(dict(state), actor_id) or {}
    if not actor:
        return _refused('Actor not found.')
    effect = _text(plan.get('effect')).lower()
    entry = _object(state, plan.get('objectId')) if _text(plan.get('objectId')) else {}
    affordance = next((item for item in object_affordances(entry) if item['id'] == _text(plan.get('affordanceId'))), None) if entry else None
    if _text(plan.get('affordanceId')) and affordance is None:
        return _refused("That can't be done here.")
    if affordance is not None:
        refusal = affordance_refusal(state, entry, affordance)
        if refusal:
            return _refused(refusal)
        effect = affordance['effect']
    elif effect not in EFFECTS:
        return _refused("That can't be done here.")
    zones = place_zones(state)
    if entry:
        # Next door the hero has stepped in first (the turn's zone plan); anywhere else is out of reach.
        reach, move = zone_reach(state, actor_id=actor_id, zone_id=object_zone_id(state, entry.get('id'), zones), subject=the_name({**entry, 'kind': 'object'}))
        if move:
            reach = f"{capitalized(the_name({**entry, 'kind': 'object'}))} is next door. Go there first."
        if reach or not _visible(entry) or _thing(state, actor_id, entry.get('id')) is None:
            return _refused(reach or 'There is nothing like that here.')
    subject_kind = _text(plan.get('subjectKind'))
    subject = _subject_record(state, plan.get('subjectId'), subject_kind) if _text(plan.get('subjectId')) else {}
    if _text(plan.get('subjectId')) and not subject:
        return _refused('There is nothing like that here.')
    seen = _thing(state, actor_id, plan.get('subjectId')) if subject else None
    if seen is not None and seen['steps'] != 0:
        return _refused(f"{capitalized(the_name({**subject, 'kind': subject_kind}))} is not here. Go there first.")
    if affordance is None:
        checked = table_plan(state, actor_id, effect, object_id=_text(plan.get('objectId')) or None, subject_id=_text(plan.get('subjectId')) or None)
        if checked is None:
            return _refused("That can't be done here.")
        plan = {**plan, **checked}
        subject_kind = _text(plan.get('subjectKind'))
        subject = _subject_record(state, plan.get('subjectId'), subject_kind) if _text(plan.get('subjectId')) else {}
    elif affordance['target'] == 'enemy' and not subject:
        enemies = [thing for thing in things(state, actor_id) if thing['kind'] == 'enemy']
        if not enemies:
            return _refused('There is no enemy to aim it at.')
        subject_kind, subject = 'enemy', enemies[0]['record']
    zone_id = fight_zone(state) or (object_zone_id(state, entry.get('id'), zones) if entry else hero_zone_id(state, actor_id, zones))
    seed = f"plan:{state.get('sessionId')}:{turn_id}:{actor_id}:{effect}:{_text(plan.get('objectId'))}:{_text(plan.get('subjectId'))}"
    check_spec = _check_spec(plan, affordance)
    rolled = _roll(state, actor_id, check_spec[0], dc=check_spec[1], seed=seed) if check_spec else None
    success = rolled is None or rolled.get('passed') is True
    change_id = stable_change_id(turn_id, EFFECT_CHANGE, actor_id, effect, _text(plan.get('objectId')), _text(plan.get('subjectId')), _text(plan.get('affordanceId')))
    subject_view = {**subject, 'kind': subject_kind} if subject else {}
    change: dict[str, Any] = {
        'id': change_id,
        'turnId': turn_id,
        'type': EFFECT_CHANGE,
        'source': EFFECT_SOURCE,
        'actorId': actor_id,
        'actorName': _text(actor.get('name')),
        'effect': effect,
        'label': affordance['label'] if affordance else plan_label(state, plan),
        'zoneId': zone_id,
        'locationId': _text(_scene(state).get('locationId')),
        'success': success,
        **({'objectId': _text(entry['id']), 'objectName': _text(entry.get('name'))} if entry else {}),
        **({'subjectId': _text(subject.get('id')), 'subjectKind': subject_kind, 'subjectName': _text(subject.get('name'))} if subject else {}),
        **({'affordanceId': affordance['id']} if affordance else {}),
        **({'check': rolled} if rolled else {}),
        'visible': True,
    }
    extra: list[dict[str, Any]] = []
    if success:
        _resolve_success(state, change, entry=entry, subject=subject_view, actor=actor, affordance=affordance, zone_id=zone_id, extra=extra, turn_id=turn_id)
    else:
        _resolve_failure(change, entry=entry, affordance=affordance)
    change['reason'] = did_line(change)
    result = {
        'effect': effect,
        'label': change['label'],
        'success': success,
        'did': change['reason'],
        **({'text': change['text']} if _text(change.get('text')) else {}),
        **({'fact': change['facts'][0]['text']} if change.get('facts') else {}),
    }
    return {'ok': True, 'reason': '', 'changes': [change, *extra], 'result': result}


def _refused(reason: str) -> dict[str, Any]:
    return {'ok': False, 'reason': reason, 'changes': [], 'result': None}


def _resolve_success(
    state: Mapping[str, Any],
    change: dict[str, Any],
    *,
    entry: Mapping[str, Any],
    subject: Mapping[str, Any],
    actor: Mapping[str, Any],
    affordance: Mapping[str, Any] | None,
    zone_id: str,
    extra: list[dict[str, Any]],
    turn_id: Any,
) -> None:
    """What a plan that worked does (the module docstring's meanings), written onto ``change``."""

    effect = change['effect']
    authored = _dict((affordance or {}).get('result'))
    patches: list[dict[str, Any]] = []
    facts: list[dict[str, str]] = []
    hero_id, hero_name = _text(actor.get('id')), _text(actor.get('name'))
    object_id = _text(entry.get('id')) if entry else ''
    in_fight = fighting(state)
    zone_name = _zone_name(state, zone_id)
    if affordance is not None and affordance['once']:
        patches.append({'id': object_id, 'patch': {'affordancesUsed': [*_strings(entry.get('affordancesUsed')), affordance['id']]}})
    if effect == 'obstruct':
        way = _text(entry.get('guards')) or object_id
        patches.append({'id': way, 'patch': {'blocked': True}})
        change['wayId'] = way
    elif effect == 'destroy':
        tags = object_tags(entry)
        patch: dict[str, Any] = {'broken': True}
        if 'rope' in tags:
            patch['loose'] = True
        if 'barrier' in tags:
            patch['blocked'] = False
            change['wayId'] = object_id
        patches.append({'id': object_id, 'patch': patch})
        if 'light' in tags and not affordance:
            change['zoneCondition'] = {'zoneId': zone_key(state, zone_id), 'add': 'dark'}
        if tags & {'animal', 'vehicle'} and 'rope' in tags:
            loose = _plan_subject_default(state, hero_id, 'separate')
            if loose is not None:
                change['freeSeparate'] = {'id': loose['id'], 'kind': loose['kind'], 'name': loose['name']}
                _lay_effect(state, change, loose['record'], loose['kind'], CUT_OFF_SOURCE, 'cut_off', hero_id, hero_name, turn_id, extra)
    elif effect == 'expose':
        revealed = _reveal_in_zone(state, zone_id)
        change.update({key: value for key, value in revealed.items() if value})
        if object_tags(entry) & {'light', 'fire'} and not affordance:
            patches.append({'id': object_id, 'patch': {'spent': True}})
        for participant_id in revealed.get('revealEnemyIds') or []:
            extra.append(
                {
                    'id': stable_change_id(change['id'], 'combat.condition.remove', participant_id),
                    'turnId': turn_id,
                    'type': 'combat.condition.remove',
                    'source': COMBAT_ENGINE_SOURCE,
                    'participantId': participant_id,
                    'condition': 'hidden',
                    'reason': 'Exposed.',
                    'visible': True,
                }
            )
    elif effect == 'conceal':
        if subject.get('kind') == 'item':
            change['itemMove'] = {'itemId': _text(subject.get('id')), 'to': 'scene', 'containerId': object_id, 'concealed': True, 'fromHeroId': hero_id if _held_by(state, hero_id, subject.get('id')) else ''}
        elif in_fight:
            extra.append(
                {
                    'id': stable_change_id(change['id'], 'combat.condition.add', subject.get('id'), 'hidden'),
                    'turnId': turn_id,
                    'type': 'combat.condition.add',
                    'source': COMBAT_ENGINE_SOURCE,
                    'participantId': _text(subject.get('id')),
                    'condition': 'hidden',
                    'reason': f"{_text(subject.get('name'))} is hidden.",
                    'visible': True,
                }
            )
    elif effect == 'separate':
        _lay_effect(state, change, subject, subject.get('kind'), CUT_OFF_SOURCE, 'cut_off', hero_id, hero_name, turn_id, extra)
    elif effect == 'distract':
        if subject:
            _lay_effect(state, change, subject, subject.get('kind'), DISTRACTED_SOURCE, 'distracted', hero_id, hero_name, turn_id, extra)
        elif in_fight:
            for participant in _participants(state):
                if _text(participant.get('team')).lower() == 'enemy' and _standing(participant):
                    _lay_effect(state, change, {**participant, 'kind': 'enemy'}, 'enemy', DISTRACTED_SOURCE, 'distracted', hero_id, hero_name, turn_id, extra)
        else:
            change['distractedNpcIds'] = [_text(npc.get('id')) for npc in _distracted_people(state, zone_id)]
    elif effect == 'restrain':
        _lay_effect(state, change, subject, subject.get('kind'), RESTRAINED_SOURCE, 'restrained', hero_id, hero_name, turn_id, extra)
    elif effect == 'protect':
        change['guard'] = {'targetId': _text(subject.get('id')), 'kind': subject.get('kind'), 'name': _text(subject.get('name'))}
        if in_fight:
            effect_record = _effect_payload(hero_id, GUARDING_SOURCE, hero_id=hero_id, hero_name=hero_name, condition='guarding', turn_id=turn_id, guardingId=_text(subject.get('id')), guardingKind=subject.get('kind'))
            extra.append(_combat_effect_change(turn_id, change['id'], hero_id, effect_record, f"{hero_name} guards {_text(subject.get('name'))}."))
    elif effect == 'take':
        change['itemMove'] = {'itemId': _text(subject.get('id')), 'to': 'hero', 'heroId': hero_id, 'fromEnemyId': _text((_carried_by_enemy(state, _text(subject.get('id'))) or {}).get('id'))}
    if patches:
        change['objectPatches'] = patches
    fact = _text(authored.get('fact')) or _fact_for(state, effect, obj=entry, subject=subject, actor=actor, zone_name=zone_name)
    if fact:
        facts.append({'key': _fact_key(effect, change), 'text': fact})
    if _text(authored.get('text')):
        change['text'] = _text(authored['text'])
    for key in ('setsFlags', 'stakes'):
        if isinstance(authored.get(key), dict) and authored[key]:
            change[key] = deepcopy(authored[key])
    if _dicts(authored.get('affects')):
        change['affects'] = deepcopy(authored['affects'])
    for condition in _strings(authored.get('conditions')):
        if subject.get('kind') == 'enemy' and in_fight:
            extra.append(
                {
                    'id': stable_change_id(change['id'], 'combat.condition.add', subject.get('id'), condition),
                    'turnId': turn_id,
                    'type': 'combat.condition.add',
                    'source': COMBAT_ENGINE_SOURCE,
                    'participantId': _text(subject.get('id')),
                    'condition': condition,
                    'rounds': 1,
                    'reason': f"{_text(subject.get('name'))} is {condition.replace('_', ' ')}.",
                    'visible': True,
                }
            )
    if _text(authored.get('startsClockId')):
        change['startsClockId'] = _text(authored['startsClockId'])
    if affordance is not None and affordance['inspires']:
        change['inspires'] = affordance['inspires']
    change['facts'] = facts


def _fact_key(effect: str, change: Mapping[str, Any]) -> str:
    if effect in {'distract', 'separate', 'protect', 'restrain'}:
        return f"{TRANSIENT_FACT}{effect}:{change.get('subjectId') or change.get('objectId') or change.get('actorId')}"
    if effect in {'take', 'conceal'} and change.get('subjectKind') == 'item':
        return f"item:{change.get('subjectId')}"
    if effect == 'conceal':
        return f"{TRANSIENT_FACT}hidden:{change.get('subjectId')}"
    return f"object:{change.get('wayId') or change.get('objectId')}"


def _lay_effect(
    state: Mapping[str, Any],
    change: dict[str, Any],
    subject: Mapping[str, Any],
    kind: Any,
    source: str,
    condition: str,
    hero_id: str,
    hero_name: str,
    turn_id: Any,
    extra: list[dict[str, Any]],
) -> None:
    """A passing effect on an enemy (a combat effect) or a person (marked in the scene)."""

    subject_id = _text(subject.get('id'))
    if kind == 'enemy' and fighting(state):
        payload = _effect_payload(
            subject_id,
            source,
            hero_id=hero_id,
            hero_name=hero_name,
            condition=condition,
            turn_id=turn_id,
            round=int(_combat(state).get('round') or 1),
            **({'escapeDc': RESTRAIN_ESCAPE_DC} if source == RESTRAINED_SOURCE else {}),
        )
        extra.append(_combat_effect_change(turn_id, change['id'], subject_id, payload, f"{_text(subject.get('name'))} is {condition.replace('_', ' ')}."))
    elif kind == 'person':
        key = {CUT_OFF_SOURCE: 'cutOffNpcIds', DISTRACTED_SOURCE: 'distractedNpcIds'}.get(source)
        if key:
            change[key] = list(dict.fromkeys([*_strings(change.get(key)), subject_id]))


def _held_by(state: Mapping[str, Any], hero_id: str, item_id: Any) -> bool:
    return any(_text(item_id) in {_text(item.get('id')), _text(item.get('sourceItemId'))} for item in _hero_items(state, hero_id))


def _reveal_in_zone(state: Mapping[str, Any], zone_id: str) -> dict[str, list[str]]:
    """What an ``expose`` uncovers in the zone: hidden objects, hidden or concealed items, hidden enemies."""

    zones = place_zones(state)

    def here(found_zone: str) -> bool:
        return not zones or not found_zone or found_zone == zone_id

    objects = [
        _text(entry['id'])
        for entry in _objects(state)
        if (entry.get('hidden') is True and entry.get('playerKnown') is not True or _text(entry.get('exposedBy')) == 'expose') and entry.get('gmOnly') is not True
        and here(object_zone_id(state, entry.get('id'), zones))
    ]
    items = [
        _text(item.get('id'))
        for item in _dicts(_scene(state).get('items'))
        if (item.get('hidden') is True and item.get('playerKnown') is not True or item.get('concealed') is True or _text(item.get('exposedBy')) == 'expose')
        and here(item_zone_id(state, item, zones))
    ]
    enemies = [
        _text(participant.get('id'))
        for participant in _participants(state)
        if _text(participant.get('team')).lower() == 'enemy' and 'hidden' in {_text(condition).lower() for condition in participant.get('conditions') or []}
    ] if fighting(state) else []
    return {'revealObjectIds': objects, 'revealItemIds': items, 'revealEnemyIds': enemies}


def _resolve_failure(change: dict[str, Any], *, entry: Mapping[str, Any], affordance: Mapping[str, Any] | None) -> None:
    failed = _dict((affordance or {}).get('failResult'))
    on_fail = _dict((affordance or {}).get('onFail'))
    text = _text(failed.get('text')) or _text(on_fail.get('text'))
    if text:
        change['text'] = text
    fact = _text(failed.get('fact')) or _text(on_fail.get('fact'))
    change['facts'] = [{'key': f"object:{change.get('objectId')}:failed", 'text': fact}] if fact else []
    if on_fail:
        change['onFail'] = deepcopy(on_fail)


def did_line(change: Mapping[str, Any]) -> str:
    """``Effect — obstruct: the yard gate is barred (Athletics 14 vs DC 10)``; a failure says nothing changed."""

    rolled = check_words(change.get('check'))
    suffix = f' ({rolled})' if rolled else ''
    label = _text(change.get('label'))
    if change.get('success') is not True:
        return f"Effect — {change.get('effect')}: {label[:1].lower()}{label[1:]} fails; nothing changes{suffix}"
    facts = _dicts(change.get('facts'))
    what = _text(facts[0].get('text')).rstrip('.') if facts else f'{label[:1].lower()}{label[1:]}'
    # A fact opening on an article reads on after the colon in lower case; a name keeps its capital.
    if what.split(' ', 1)[0] in {'The', 'A', 'An'}:
        what = f'{what[:1].lower()}{what[1:]}'
    return f"Effect — {change.get('effect')}: {what}{suffix}"


# Applying ---------------------------------------------------------------------------------------------------------


def effect_change_problem(state: Mapping[str, Any], change: Mapping[str, Any]) -> str:
    """Why a ``scene.effect`` change cannot apply; '' when it can."""

    if _text(change.get('source')) != EFFECT_SOURCE:
        return 'Only the engine resolves plans.'
    if _text(change.get('effect')) not in EFFECTS:
        return 'Unknown effect.'
    if _text(change.get('locationId')) and _text(change.get('locationId')) != _text(_scene(state).get('locationId')):
        return 'The plan was made somewhere else.'
    return ''


def sync_location(state: dict[str, Any]) -> None:
    """Mirror the scene's objects into its location's ``sceneState`` so travel restores them."""

    scene = _scene(state)
    for location in _dicts(state.get('locations')):
        if _text(location.get('id')) == _text(scene.get('locationId')):
            scene_state = location.setdefault('sceneState', {})
            if isinstance(scene_state, dict):
                for key in ('interactables', 'hazards'):
                    if isinstance(scene.get(key), list):
                        scene_state[key] = deepcopy(scene[key])


def move_item(state: dict[str, Any], move: Mapping[str, Any]) -> bool:
    item_id = _text(move.get('itemId'))
    scene = _scene(state)
    item: dict[str, Any] | None = None
    items = _dicts(scene.get('items'))
    for index, record in enumerate(items):
        if _text(record.get('id')) == item_id:
            item = items.pop(index)
            break
    if item is None:
        for participant in _participants(state):
            carried = _dicts(participant.get('carrying'))
            for index, record in enumerate(carried):
                if _text(record.get('id')) == item_id:
                    item = carried.pop(index)
                    participant['carrying'] = carried
                    break
            if item is not None:
                break
    if item is None:
        for hero in _dicts(state.get('playerCharacters')):
            held = _dicts(_dict(hero.get('inventory')).get('items'))
            for index, record in enumerate(held):
                if item_id in {_text(record.get('id')), _text(record.get('sourceItemId'))}:
                    item = held.pop(index)
                    hero['inventory']['items'] = held
                    break
            if item is not None:
                break
    if item is None:
        return False
    scene['items'] = items
    item = {key: value for key, value in item.items() if key not in {'containerId', 'concealed', 'equipped'}}
    if move.get('to') == 'hero':
        hero = find_actor(state, move.get('heroId'))
        if hero is None:
            return False
        inventory = hero.setdefault('inventory', {})
        inventory['items'] = [*_dicts(inventory.get('items')), {**item, 'quantity': int(item.get('quantity') or 1)}]
    elif move.get('to') == 'enemy':
        participant = next((record for record in _participants(state) if _text(record.get('id')) == _text(move.get('enemyId'))), None)
        if participant is None:
            return False
        participant['carrying'] = [*_dicts(participant.get('carrying')), item]
    else:
        placed = {**item}
        if _text(move.get('containerId')):
            placed['containerId'] = _text(move['containerId'])
        if move.get('concealed') is True:
            placed['concealed'] = True
        if _text(move.get('zoneId')):
            placed['zoneId'] = _text(move['zoneId'])
        scene['items'] = [*_dicts(scene.get('items')), placed]
    return True


def apply_effect_change(state: dict[str, Any], change: Mapping[str, Any]) -> dict[str, Any] | None:
    """Apply a ``scene.effect`` change to ``state``; the applied change's extra fields, or None when it no longer
    applies."""

    if effect_change_problem(state, change):
        return None
    scene = _scene(state)
    if not scene:
        return None
    objects = {_text(entry.get('id')): entry for entry in _objects(state)}
    for patch in _dicts(change.get('objectPatches')):
        entry = objects.get(_text(patch.get('id')))
        if entry is not None:
            entry.update(deepcopy(_dict(patch.get('patch'))))
            entry['revision'] = int(entry.get('revision') or 0) + 1
    for object_id in _strings(change.get('revealObjectIds')):
        entry = objects.get(object_id)
        if entry is not None:
            entry.update({'playerKnown': True, 'hidden': False})
    for item in _dicts(scene.get('items')):
        if _text(item.get('id')) in _strings(change.get('revealItemIds')):
            item.pop('concealed', None)
            item.update({'playerKnown': True, 'hidden': False})
    if isinstance(change.get('itemMove'), dict) and not move_item(state, change['itemMove']):
        return None
    condition = _dict(change.get('zoneCondition'))
    if condition:
        conditions = _dict(scene.get(ZONE_CONDITIONS_KEY))
        conditions[_text(condition.get('zoneId'))] = list(dict.fromkeys([*_strings(conditions.get(_text(condition.get('zoneId')))), _text(condition.get('add'))]))
        scene[ZONE_CONDITIONS_KEY] = conditions
        if fighting(state) and _text(condition.get('add')) == 'dark':
            _combat(state).setdefault('flags', {})['dark'] = True
    for key, scene_key in (('cutOffNpcIds', CUT_OFF_KEY), ('distractedNpcIds', DISTRACTED_KEY)):
        if _strings(change.get(key)):
            scene[scene_key] = list(dict.fromkeys([*_strings(scene.get(scene_key)), *_strings(change.get(key))]))
    guard = _dict(change.get('guard'))
    if guard and not fighting(state):
        scene[GUARDS_KEY] = {**guards(state), _text(change.get('actorId')): deepcopy(guard)}
    for fact in _dicts(change.get('facts')):
        set_fact(scene, _text(fact.get('key')), _text(fact.get('text')))
    if _text(change.get('startsClockId')):
        state[CLOCKS_STARTED_KEY] = list(dict.fromkeys([*_strings(state.get(CLOCKS_STARTED_KEY)), _text(change['startsClockId'])]))
    sync_location(state)
    from aidm_server.game_state.orchestration.fight_stakes import sync_stakes

    sync_stakes(state, turn_id=change.get('turnId'), forced=_dict(change.get('stakes')))
    return {}


# Follow-ups -------------------------------------------------------------------------------------------------------


def _flag_changes(parent: Mapping[str, Any], flags: Mapping[str, Any], reason: str) -> list[dict[str, Any]]:
    return [
        {
            'id': stable_change_id(parent.get('id'), 'flag', key),
            'turnId': parent.get('turnId'),
            'type': 'flag.set',
            'source': EFFECT_SOURCE,
            'flagKey': _text(key),
            'flagValue': deepcopy(value),
            'visible': False,
            'reason': reason,
        }
        for key, value in flags.items()
    ]


def consequence_changes(state: Mapping[str, Any], parent: Mapping[str, Any], spec: Mapping[str, Any], *, hero_id: str, reason: str) -> list[dict[str, Any]]:
    """The flags, dispositions, revealed objects and encounter an authored result or ``onFail`` brings."""

    from aidm_server.game_state.orchestration.conversations import affect_change

    changes = _flag_changes(parent, _dict(spec.get('setsFlags')), reason)
    for index, entry in enumerate(_dicts(spec.get('affects'))):
        change = affect_change(dict(state), parent=dict(parent), entry=entry, index=index, hero_id=hero_id)
        if change:
            changes.append(change)
    reveals = _strings(spec.get('reveals'))
    if reveals:
        changes.append(
            {
                'id': stable_change_id(parent.get('id'), EFFECT_CHANGE, 'reveals'),
                'turnId': parent.get('turnId'),
                'type': EFFECT_CHANGE,
                'source': EFFECT_SOURCE,
                'actorId': hero_id,
                'effect': 'expose',
                'label': reason,
                'success': True,
                'consequence': True,
                'revealObjectIds': reveals,
                'facts': [],
                'locationId': _text(_scene(state).get('locationId')),
                'visible': True,
            }
        )
    encounter_id = _text(spec.get('startsEncounterId'))
    if encounter_id and not fighting(state):
        from aidm_server.game_state.campaign_pack_encounters import catalog_record, triggered_encounter_start

        encounter = catalog_record(dict(state), 'encounters', encounter_id)
        start = triggered_encounter_start(dict(state), encounter, turn_id=parent.get('turnId'), cause_id=parent.get('id')) if encounter else None
        if start:
            changes.append(start)
    return changes


def plan_followup_changes(state: dict[str, Any], applied: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What follows one applied change: a plan's flags, dispositions, memory and Inspiration; an object action's
    ``onFail``."""

    change_type = _text(applied.get('type'))
    if change_type == 'scene.interactable.action':
        return _object_on_fail_changes(state, applied)
    if change_type != EFFECT_CHANGE or applied.get('consequence') is True:
        return []
    from aidm_server.game_state.orchestration.conversations import memory_change
    from aidm_server.game_state.orchestration.hero_threads import grant_inspiration

    hero_id = _text(applied.get('actorId'))
    reason = f"{_text(applied.get('label')) or 'A plan'}."
    spec = applied if applied.get('success') is True else _dict(applied.get('onFail'))
    changes = consequence_changes(state, applied, spec, hero_id=hero_id, reason=reason)
    guard = _dict(applied.get('guard'))
    if applied.get('success') is True and guard.get('kind') == 'person':
        remembered = memory_change(state, hero_id=hero_id, npc_id=guard.get('targetId'), key='protected', turn_id=applied.get('turnId'))
        if remembered:
            changes.append(remembered)
    reasons = []
    if applied.get('success') is True and (_strings(applied.get('revealObjectIds')) or _strings(applied.get('revealEnemyIds'))) and applied.get('effect') == 'expose':
        reasons.append(SECRET_UNCOVERED)
    if applied.get('success') is True and _text(applied.get('inspires')):
        reasons.append(_text(applied['inspires']))
    for reason_text in reasons:
        grant = grant_inspiration(state, hero_id, reason_text, turn_id=applied.get('turnId'))
        if grant:
            changes.append(grant)
            break
    return changes


def teamwork_reasons(state_before: Mapping[str, Any], state: Mapping[str, Any], applied: Iterable[Mapping[str, Any]], actor_id: str) -> list[tuple[str, str]]:
    """``well played together``: the hero whose effect (distracted, cut off, restrained) another hero's success against
    that enemy relied on in the same round (``(heroId, reason)`` for hero threads)."""

    if not fighting(state_before):
        return []
    round_now = int(_combat(state).get('round') or _combat(state_before).get('round') or 1)
    before = {_text(participant.get('id')): participant for participant in _participants(state_before)}
    earned = []
    for change in applied:
        change_type = _text(change.get('type'))
        target = before.get(_text(change.get('participantId')))
        if target is None or _text(target.get('team')).lower() != 'enemy':
            continue
        struck = change_type == 'combat.participant.update' and _text(change.get('sourceId')) == actor_id
        held = change_type in {'combat.effect.add', 'combat.condition.add'} and _text(change.get('source')) == 'combat_action_engine'
        if not (struck or held):
            continue
        from aidm_server.combat.modifiers import active_effects

        for effect in active_effects(target):
            caster = _text(effect.get('casterId'))
            if (
                effect['sourceId'] in {DISTRACTED_SOURCE, CUT_OFF_SOURCE, RESTRAINED_SOURCE}
                and caster
                and caster != actor_id
                and int(effect.get('round') or round_now) == round_now
                and all(caster != hero for hero, _reason in earned)
            ):
                earned.append((caster, WELL_PLAYED))
    return earned


def object_on_fail(entry: Mapping[str, Any], action: str) -> dict[str, Any] | None:
    """An object action's authored ``onFail`` (``{"<action>": {...}}``), or None."""

    spec = entry.get('onFail')
    value = spec.get(action) if isinstance(spec, dict) else None
    return value if isinstance(value, dict) else None


def _object_on_fail_changes(state: dict[str, Any], applied: Mapping[str, Any]) -> list[dict[str, Any]]:
    event = _dict(applied.get('event'))
    if not _text(event.get('type')).endswith('.check_failed'):
        return []
    spec = object_on_fail(_object(state, applied.get('targetId')), _text(applied.get('objectAction')).lower())
    if spec is None:
        return []
    hero_id = _text(applied.get('actorId'))
    changes = consequence_changes(state, applied, spec, hero_id=hero_id, reason=f"{_text(event.get('targetName')) or 'An object'}: failed.")
    if _text(spec.get('fact')):
        changes.append(
            {
                'id': stable_change_id(applied.get('id'), EFFECT_CHANGE, 'onFail'),
                'turnId': applied.get('turnId'),
                'type': EFFECT_CHANGE,
                'source': EFFECT_SOURCE,
                'actorId': hero_id,
                'effect': 'expose',
                'label': _text(event.get('targetName')),
                'success': True,
                'consequence': True,
                'facts': [{'key': f"object:{applied.get('targetId')}:failed", 'text': _text(spec['fact'])}],
                'locationId': _text(_scene(state).get('locationId')),
                'visible': True,
            }
        )
    return changes


# Narration --------------------------------------------------------------------------------------------------------


def effect_facts(applied_changes: Iterable[Mapping[str, Any]], *, actor_id: str) -> list[dict[str, Any]]:
    """The narration packet's ``effects``: each plan the hero resolved this turn (``effect``, ``label``, ``success``,
    ``did``, ``text``, ``fact``, ``check``)."""

    facts = []
    for change in applied_changes:
        if not isinstance(change, Mapping) or _text(change.get('type')) != EFFECT_CHANGE or change.get('consequence') is True:
            continue
        facts.append(
            {
                'effect': _text(change.get('effect')),
                'label': _text(change.get('label')),
                'actorId': _text(change.get('actorId')),
                'actor': _text(change.get('actorName')),
                'success': change.get('success') is True,
                'did': _text(change.get('reason')) or did_line(change),
                **({'text': _text(change['text'])} if _text(change.get('text')) else {}),
                **({'fact': _text(_dicts(change.get('facts'))[0].get('text'))} if _dicts(change.get('facts')) else {}),
                **({'check': deepcopy(change['check'])} if isinstance(change.get('check'), dict) else {}),
                **({'mine': True} if _text(change.get('actorId')) == actor_id else {}),
            }
        )
    return facts


def unseen_line(typed: str, *, hero: str = '') -> str:
    """``You see no wagon strongbox here.``; in party play ``Arden Vale sees no wagon strongbox here.``"""

    return f'{hero} sees no {typed} here.' if hero else f'You see no {typed} here.'


UNSEEN_DID = 'nothing — the hero finds no such thing in sight.'
IMPROVISED_SCRIPTED = {
    (False, False): 'You try it. It comes to nothing.',
    (False, True): 'You manage it, for what it is worth.',
    (True, False): '{hero} tries it. It comes to nothing.',
    (True, True): '{hero} manages it, for what it is worth.',
}


def improvised_did(words: str, roll: Mapping[str, Any]) -> str:
    """``Improvised — "<words>": Athletics 14 vs DC 12, success. The engine changed nothing.``"""

    skill = _text(roll.get('rule_type') or roll.get('skill')).replace('_', ' ').title() or 'Check'
    if skill.lower() in {'check', 'ability check'}:
        skill = _text(_dict(roll.get('ability')).get('key')).title() or 'Check'
    outcome = 'success' if _text(roll.get('outcome')).lower() == 'success' else 'failure'
    dc = roll.get('dc') if isinstance(roll.get('dc'), int) else roll.get('task_dc')
    against = f' vs DC {dc}' if isinstance(dc, int) else ''
    return f'Improvised — "{_text(words)[:120]}": {skill} {roll.get("total")}{against}, {outcome}. The engine changed nothing.'


def improvised_line(*, success: bool, hero: str = '') -> str:
    return IMPROVISED_SCRIPTED[(bool(hero), success)].format(hero=hero)


def scripted_effect_lines(effect: Mapping[str, Any], *, hero_does: Callable[[str], str]) -> list[str]:
    """How the scripted narrator tells one plan: its authored text as written, else the hero doing it and what it
    changed (``You shut and bar the yard gate. The yard gate is barred.``), or that it did not work."""

    if _text(effect.get('text')):
        return [_text(effect['text'])]
    label = _text(effect.get('label'))
    phrase = f'{label[:1].lower()}{label[1:]}'
    if effect.get('success') is not True:
        return [hero_does(f'try to {phrase}'), 'It does not work.']
    return [hero_does(phrase), *([_text(effect['fact'])] if _text(effect.get('fact')) else [])]


def told_by_engine(narration: Mapping[str, Any]) -> list[str]:
    """The authored passages of this turn's plans, told as written by the engine (amendment 9)."""

    return [_text(effect.get('text')) for effect in _dicts(narration.get('effects')) if _text(effect.get('text'))]


def scene_paragraphs(rules_hint: Any) -> tuple[str, str]:
    """``(opening, closing)``: the authored passages the engine tells around a narrating model's reply."""

    told = _dict(_dict(_dict(_dict(rules_hint).get('state_pipeline')).get('narration')).get('sceneTold'))
    return '\n\n'.join(_strings(told.get('opening'))), '\n\n'.join(_strings(told.get('closing')))


def scene_view(state: Mapping[str, Any]) -> dict[str, Any]:
    """The player view's ``facts`` (``WHAT HAS CHANGED``) and each zone's ``conditions`` (``dark``)."""

    return {'facts': scene_facts(state), 'zoneConditions': deepcopy(_dict(_scene(state).get(ZONE_CONDITIONS_KEY)))}


# In a fight -------------------------------------------------------------------------------------------------------


def _economy(cost: str) -> dict[str, Any]:
    return {
        'action': 1 if cost == 'action' else 0,
        'bonusAction': 1 if cost == 'bonus_action' else 0,
        'reaction': 0,
        'movement': 'unused',
        'movementCost': 0,
        'endsTurn': False,
        'tracking': 'persisted_turn_economy',
        'reactionTracked': True,
        'subTurnCountersTracked': True,
    }


def plan_fight_actions(snapshot: dict[str, Any], participant: dict[str, Any], combat: dict[str, Any]) -> list[dict[str, Any]]:
    """The authored affordances usable in the fight, for the combat HUD: ``plan.<objectId>.<affordanceId>``."""

    hero = find_actor(snapshot, participant.get('id'))
    if not hero:
        return []
    state = {**snapshot, 'combat': combat}
    actions = []
    for object_id, offers in affordance_offers(state, hero).items():
        entry = _object(state, object_id)
        for offer in offers:
            if not offer['legal']:
                continue
            affordance = next(item for item in object_affordances(entry) if item['id'] == offer['planId'])
            label = offer['label']
            actions.append(
                {
                    'id': f"{PLAN_ACTION_PREFIX}{object_id}.{affordance['id']}",
                    'type': PLAN_ACTION_TYPE,
                    'label': label,
                    'description': {'action': 'Uses your action.', 'bonus_action': 'Uses your bonus action.', 'free': 'Free.'}[affordance['cost']]
                    + (' Needs a check.' if affordance['check'] else ''),
                    'message': f"{_text(participant.get('name')) or 'I'}: {label[:1].lower()}{label[1:]}.",
                    'available': True,
                    'reason': '',
                    'economy': _economy(affordance['cost']),
                    'plan': {'effect': affordance['effect'], 'objectId': object_id, 'affordanceId': affordance['id']},
                    **({'suggested': True} if offer.get('suggested') is True else {}),
                }
            )
    return actions


def resolve_plan_fight_action(snapshot: dict[str, Any], player: Any, action: dict[str, Any], target_id: str) -> tuple[dict[str, Any] | None, str | None, str | None]:
    if target_id:
        return None, 'combat_target_invalid', 'That combat action does not accept a target.'
    return (
        {
            'action_id': action['id'],
            'action_type': PLAN_ACTION_TYPE,
            'economy': deepcopy(action['economy']),
            'authoritative': True,
            'plan': deepcopy(action['plan']),
            'message': _text(action.get('message')),
        },
        None,
        None,
    )


def plan_words_action(bundle: dict[str, Any], player: Any, snapshot: dict[str, Any], plan: Any) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Typed words that named a plan in the fight: they cost the hero's action (as the improvised action does)."""

    end_turn = next((action for action in bundle['actions'] if action['id'] == 'combat.end_turn'), {})
    if not end_turn.get('available'):
        return None, 'combat_action_unavailable', _text(end_turn.get('reason')) or 'That combat action is not available now.'
    if not isinstance(plan, dict) or not plan:
        return None, 'combat_action_invalid', "That can't be done here."
    affordance = next(
        (item for item in object_affordances(_object(snapshot, plan.get('objectId'))) if item['id'] == _text(plan.get('affordanceId'))),
        None,
    )
    cost = affordance['cost'] if affordance else 'action'
    if cost == 'action' and not bundle['economy']['actionAvailable']:
        return None, 'combat_action_unavailable', "This turn's action is already spent."
    if cost == 'bonus_action' and not bundle['economy']['bonusActionAvailable']:
        return None, 'combat_action_unavailable', "This turn's bonus action is already spent."
    return (
        {
            'action_id': PLAN_WORDS_ACTION_ID,
            'action_type': PLAN_ACTION_TYPE,
            'economy': _economy(cost),
            'authoritative': True,
            'plan': deepcopy(plan),
            'message': f"{_text(getattr(player, 'character_name', '')) or 'The hero'} tries a plan.",
        },
        None,
        None,
    )


def resolve_plan_in_fight(context: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    """The fight's resolution of a plan (``actions.resolve_combat_action``): the plan's changes and its ``Did``."""

    resolved = resolve_plan({**state, 'combat': state.get('combat')}, actor_id=context['actorId'], plan=context.get('plan') or {}, turn_id=context['turnId'])
    if not resolved['ok']:
        return {'ok': False, 'reason': resolved['reason'], 'changes': [], 'result': None}
    result = resolved['result']
    return {'changes': resolved['changes'], 'outcome': result['did'], 'success': result['success'], 'effect': result['effect']}


def dark_modifiers(participant: Mapping[str, Any], role: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    """In a fight whose zone is dark, ranged attacks are made with disadvantage."""

    combat = context.get('combat') if isinstance(context.get('combat'), Mapping) else {}
    if role != 'attacker' or context.get('ranged') is not True or _dict(combat.get('flags')).get('dark') is not True:
        return None
    return {'mode': 'disadvantage', 'reasons': ['It is dark.']}


def _register() -> None:
    from aidm_server.combat.actions import register_action_type_resolver
    from aidm_server.combat.legal_actions import register_action_provider, register_action_resolver, register_unlisted_action
    from aidm_server.combat.modifiers import register_modifier_provider

    register_action_provider(plan_fight_actions)
    register_action_resolver(PLAN_ACTION_PREFIX, resolve_plan_fight_action)
    register_unlisted_action(PLAN_WORDS_ACTION_ID, plan_words_action)
    register_action_type_resolver(PLAN_ACTION_TYPE, resolve_plan_in_fight)
    register_modifier_provider(dark_modifiers)


_register()
