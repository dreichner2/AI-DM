"""A turn in a place with rooms: what the hero's action needs from where they stand, and what the narration tells.

:func:`zone_plan` reads the turn's action — a clicked one (its intent) or the typed words' mapped intents, a person
talked to, or words the hero says — against the zones of :mod:`aidm_server.game_state.zones`:

- a zone move (``zone_move`` intent, clicked or typed): the move alone, refused when it is not next door or a fight
  is on;
- an action on something or someone in the hero's own zone (words to a person included): nothing to do;
- in a zone next door, outside a fight: the move first, then the action, in one turn;
- further off, or next door during a fight: refused before narration (``Mara Reedwake is in the common room. Go
  there first.``);
- travel away from a zone that is not an exit, and a rest outside the rest zones: refused;
- words the hero says (not a talk action) that name someone next door call out to them: one line, answered in at
  most one sentence; naming someone further off is refused.

A place without zones plans nothing. :func:`zone_move_facts` and the plan's ``call_out`` are what the narration is
told: the scripted narrator says the arrival line (``You step into the common room. Mara Reedwake is here.``), with
the zone's description the first time, and a narrating model is given the same in its brief.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from aidm_server.game_state.extraction.free_text_actions import FreeTextAction, is_speech, name_words
from aidm_server.game_state.zones import (
    ZONE_MOVE_CHANGE,
    captive_zone_id,
    fight_running,
    hero_zone_id,
    item_zone_id,
    merchant_zone_id,
    npc_zone_id,
    object_zone_id,
    people_in_zone,
    place_zones,
    present_npcs,
    rest_refusal,
    the_name,
    zone_by_id,
    zone_move_refusal,
    zone_phrase,
    zone_reach,
    zone_steps,
)

ZONE_MOVE_KIND = 'zone_move'


@dataclass(frozen=True)
class ZonePlan:
    """``refusal`` (the turn is refused before narration), ``move_to`` (the zone the hero moves into first),
    ``only_move`` (the move is the whole turn) and ``call_out`` (``{"npcId", "name", "zone"}``)."""

    refusal: str = ''
    move_to: str = ''
    only_move: bool = False
    call_out: dict[str, str] | None = None


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _scene(state: Mapping[str, Any]) -> dict[str, Any]:
    return _dict(state.get('currentScene'))


def _scene_record(state: Mapping[str, Any], collection: str, record_id: Any) -> dict[str, Any]:
    return next((record for record in _scene(state).get(collection) or [] if isinstance(record, dict) and _text(record.get('id')) == _text(record_id)), {})


def _merchant(state: Mapping[str, Any], merchant_id: Any) -> dict[str, Any]:
    merchants = state.get('merchants') if isinstance(state.get('merchants'), dict) else {}
    record = merchants.get(_text(merchant_id))
    return {'id': _text(merchant_id), **record} if isinstance(record, dict) else {}


def _npc_name(state: Mapping[str, Any], npc_id: Any) -> str:
    return next((_text(npc.get('name')) for npc in present_npcs(state) if _text(npc.get('id')) == _text(npc_id)), '')


def _targets(state: Mapping[str, Any], intents: list[dict[str, Any]], free_text: FreeTextAction | None) -> list[tuple[str, str]]:
    """``(zone_id, subject)`` for each thing or person the turn's action is aimed at."""

    targets: list[tuple[str, str]] = []
    if free_text is not None and free_text.kind == 'talk' and free_text.npc:
        npc = free_text.npc
        name = _text(npc.get('name'))
        zone = captive_zone_id(state, npc) if npc.get('captive') is True else npc_zone_id(state, npc.get('id'))
        targets.append((zone, name))
    for intent in intents:
        kind = _text(intent.get('kind'))
        if kind == 'object':
            target = _dict(intent.get('object'))
            entry = next(
                (record for collection in ('interactables', 'hazards') if (record := _scene_record(state, collection, target.get('id')))),
                {},
            )
            targets.append((object_zone_id(state, target.get('id')), the_name(entry.get('name') or target.get('id'))))
        elif kind == 'item' and _text(intent.get('inventory_action')) == 'pick_up':
            item = _scene_record(state, 'items', _dict(intent.get('item')).get('id'))
            if item:
                targets.append((item_zone_id(state, item), the_name(item.get('name'))))
        elif kind == 'shop':
            merchant = _merchant(state, _dict(intent.get('shop')).get('merchant_id'))
            if merchant:
                person = _npc_name(state, merchant.get('npcId')) or _text(merchant.get('name'))
                targets.append((merchant_zone_id(state, merchant), person))
        elif kind == 'talk':
            npc_id = _dict(intent.get('talk')).get('npc_id')
            if _npc_name(state, npc_id):
                targets.append((npc_zone_id(state, npc_id), _npc_name(state, npc_id)))
        elif kind == 'interact':
            target = _dict(intent.get('target'))
            if _text(target.get('kind')) == 'npc' and _text(target.get('npc_id')):
                targets.append((npc_zone_id(state, target.get('npc_id')), _npc_name(state, target.get('npc_id')) or 'They'))
    return [(zone_id, subject) for zone_id, subject in targets if zone_id]


def _named_people(state: Mapping[str, Any], message: str) -> list[dict[str, Any]]:
    """The people present the words name by full or first name."""

    words = name_words(message)
    named = []
    for npc in present_npcs(state):
        name = name_words(npc.get('name'))
        candidates = [name, name[:1]] if name and len(name[0]) >= 3 else [name]
        if any(candidate and words[index:index + len(candidate)] == candidate for candidate in candidates for index in range(len(words))):
            named.append(npc)
    return named


def _speech_plan(state: Mapping[str, Any], *, actor_id: str, message: str, hero_name: str) -> ZonePlan:
    """Words the hero says to the people they name: to someone here as ever, a call to someone next door, refused
    for anyone further off."""

    if not is_speech(message, hero_name=hero_name):
        return ZonePlan()
    zones = place_zones(state)
    here = hero_zone_id(state, actor_id, zones)
    named = [(npc, npc_zone_id(state, npc.get('id'), zones)) for npc in _named_people(state, message)]
    if not named or any(zone_id == here for _npc, zone_id in named):
        return ZonePlan()
    npc, zone_id = named[0]
    if zone_steps(zones, here, zone_id) == 1 and not fight_running(state):
        return ZonePlan(call_out={'npcId': _text(npc.get('id')), 'name': _text(npc.get('name')), 'zone': zone_phrase(zone_by_id(zones, zone_id))})
    refusal, _move = zone_reach(state, actor_id=actor_id, zone_id=zone_id, subject=_text(npc.get('name')))
    return ZonePlan(refusal=refusal)


def zone_plan(
    state: Mapping[str, Any],
    *,
    actor_id: str,
    action_intent: dict[str, Any] | None,
    free_text: FreeTextAction | None,
    player_message: str,
) -> ZonePlan:
    """What the turn's action needs from where the hero stands (rules in the module docstring)."""

    if not place_zones(state):
        return ZonePlan()
    intent = _dict(action_intent)
    intents = [dict(entry) for entry in free_text.intents] if free_text is not None else ([intent] if intent else [])
    move = next((entry for entry in intents if _text(entry.get('kind')) == ZONE_MOVE_KIND), None)
    if move is not None:
        zone_id = _text(_dict(move.get('zone')).get('id'))
        refusal = zone_move_refusal(state, actor_id=actor_id, zone_id=zone_id)
        return ZonePlan(refusal=refusal) if refusal else ZonePlan(move_to=zone_id, only_move=True)
    kinds = {_text(entry.get('kind')) for entry in intents}
    if 'rest' in kinds and (refusal := rest_refusal(state, actor_id=actor_id)):
        return ZonePlan(refusal=refusal)
    move_to = ''
    for zone_id, subject in _targets(state, intents, free_text):
        refusal, step = zone_reach(state, actor_id=actor_id, zone_id=zone_id, subject=subject)
        if refusal or (step and move_to and step != move_to):
            return ZonePlan(refusal=refusal or f'{subject} is in {zone_phrase(zone_by_id(place_zones(state), zone_id))}. Go there first.')
        move_to = step or move_to
    if move_to:
        return ZonePlan(move_to=move_to)
    if free_text is None and _text(intent.get('kind')) in {'', 'message'}:
        hero = next((record for record in state.get('playerCharacters') or [] if isinstance(record, dict) and _text(record.get('id')) == actor_id), {})
        return _speech_plan(state, actor_id=actor_id, message=player_message, hero_name=_text(hero.get('name')))
    return ZonePlan()


def without_zone_moves(intents: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The intents the engine's extraction reads: a zone move is made by the plan, not extracted."""

    return [intent for intent in intents if _text(intent.get('kind')) != ZONE_MOVE_KIND]


def zone_move_facts(state_after: Mapping[str, Any], applied_changes: list[dict[str, Any]], *, actor_id: str, only: bool) -> dict[str, Any] | None:
    """What the narration says of the hero's zone move this turn: ``{"zoneId", "name", "phrase", "description"
    (the first time only), "present" (who is there besides the hero), "only"}``; None without one."""

    move = next((change for change in applied_changes if isinstance(change, dict) and change.get('type') == ZONE_MOVE_CHANGE), None)
    if move is None:
        return None
    zones = place_zones(state_after)
    zone = zone_by_id(zones, move.get('zoneId')) or {}
    present = [person['name'] for person in people_in_zone(state_after, zone.get('id', ''), zones) if person['id'] != actor_id]
    return {
        'zoneId': _text(zone.get('id')),
        'name': _text(zone.get('name')),
        'phrase': zone_phrase(zone),
        'description': _text(zone.get('description')) if move.get('firstVisit') is True else '',
        'present': present,
        'only': only,
    }


def zone_move_only(context_packet: Any) -> bool:
    """The turn was a zone move and nothing else (its narration facts say so)."""

    narration = _dict(_dict(context_packet).get('narration'))
    return _dict(narration.get('zoneMove')).get('only') is True
