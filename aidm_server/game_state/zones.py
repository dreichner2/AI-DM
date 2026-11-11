"""Rooms and areas of a place: where each hero stands, what they can reach from there, and moving between them.

A pack location may divide into ``zones`` — ``{"id", "name", "description"?, "adjacentTo": [...], "exit"?, "rest"?,
"moveLabel"?}`` — with a ``startZoneId``. Adjacency goes both ways whichever zone lists it. A place without zones is
one area and nothing here applies to it.

Each hero stands in one zone of the current place (``currentScene.playerZones``, by hero id; a hero with no entry
stands in the start zone, where arrival puts everyone). People (``npcs[].zoneId``), scene objects (``zoneId``),
captives and loose items in a container stand where the pack (or the fight) put them, else in the start zone; a
merchant is wherever its person is, and a companion stands with the first hero. ``currentScene.visitedZoneIds``
records where any hero has stood; a zone is *known* once any hero has stood in it or next to it.

What a hero can reach (:func:`zone_steps`): their own zone (0 steps), an adjacent one (1, outside a fight the move is
made for them before the action) or one further off (2 or more: refused, ``Mara Reedwake is in the common room. Go
there first.``). A move (:func:`zone_move_change`, change type :data:`ZONE_MOVE_CHANGE`) takes a turn but no clock
time; travel leaves from zones marked ``exit`` (every zone when none is) and rest happens in zones marked ``rest``
(every zone when none is).
"""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable, Mapping

from aidm_server.game_state.campaign_pack_encounters import catalog_record
from aidm_server.game_state.models import stable_change_id

ZONE_MOVE_CHANGE = 'scene.zone_move'
VISITED_ZONES_KEY = 'visitedZoneIds'
# A fight's zone, kept in its combat flags.
FIGHT_ZONE_FLAG = 'zoneId'
# Heroes who ran in from another zone when a fight started: ``[{"id", "name", "from"}]`` in its combat flags.
CAME_RUNNING_FLAG = 'cameRunning'
GO_THERE_FIRST = 'Go there first.'
ACTIVE_COMBAT_STATUSES = frozenset({'starting', 'active'})


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _scene(state: Mapping[str, Any]) -> dict[str, Any]:
    scene = state.get('currentScene') if isinstance(state, Mapping) else None
    return scene if isinstance(scene, dict) else {}


def location_zones(location: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """A location's zones as ``{"id", "name", "description", "adjacentTo", "exit", "rest", "moveLabel"}``, adjacency
    made symmetric and limited to zones that exist; [] for a place that is one area."""

    raw = [zone for zone in _dicts((location or {}).get('zones')) if _text(zone.get('id')) and _text(zone.get('name'))]
    ids = {_text(zone['id']) for zone in raw}
    adjacent: dict[str, list[str]] = {_text(zone['id']): [] for zone in raw}
    for zone in raw:
        zone_id = _text(zone['id'])
        for other in zone.get('adjacentTo') or []:
            other_id = _text(other)
            if other_id in ids and other_id != zone_id:
                adjacent[zone_id] = list(dict.fromkeys([*adjacent[zone_id], other_id]))
                adjacent[other_id] = list(dict.fromkeys([*adjacent[other_id], zone_id]))
    return [
        {
            'id': _text(zone['id']),
            'name': _text(zone['name']),
            'description': _text(zone.get('description')),
            'adjacentTo': adjacent[_text(zone['id'])],
            'exit': zone.get('exit') is True,
            'rest': zone.get('rest') is True,
            'moveLabel': _text(zone.get('moveLabel')),
        }
        for zone in raw
    ]


def place_location(state: Mapping[str, Any]) -> dict[str, Any]:
    """The pack catalog record of the party's place."""

    return catalog_record(dict(state), 'locations', _scene(state).get('locationId')) if _scene(state).get('locationId') else {}


def place_zones(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    return location_zones(place_location(state))


def zoned(state: Mapping[str, Any]) -> bool:
    return bool(place_zones(state))


def start_zone_id(state: Mapping[str, Any], zones: list[dict[str, Any]] | None = None) -> str:
    zones = place_zones(state) if zones is None else zones
    if not zones:
        return ''
    wanted = _text(place_location(state).get('startZoneId'))
    return wanted if any(zone['id'] == wanted for zone in zones) else zones[0]['id']


def zone_by_id(zones: Iterable[dict[str, Any]], zone_id: Any) -> dict[str, Any] | None:
    return next((zone for zone in zones if zone['id'] == _text(zone_id)), None)


def _in_place(zones: list[dict[str, Any]], zone_id: Any, fallback: str) -> str:
    return _text(zone_id) if zone_by_id(zones, zone_id) else fallback


def hero_zone_id(state: Mapping[str, Any], hero_id: Any, zones: list[dict[str, Any]] | None = None) -> str:
    zones = place_zones(state) if zones is None else zones
    if not zones:
        return ''
    stood = _scene(state).get('playerZones') if isinstance(_scene(state).get('playerZones'), dict) else {}
    return _in_place(zones, stood.get(_text(hero_id)), start_zone_id(state, zones))


def zone_steps(zones: list[dict[str, Any]], from_id: Any, to_id: Any) -> int | None:
    """How many moves lie between two zones of a place (0 for the same one); None when there is no way."""

    start, goal = _text(from_id), _text(to_id)
    if not zone_by_id(zones, start) or not zone_by_id(zones, goal):
        return None
    seen = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current == goal:
            return seen[current]
        for other in (zone_by_id(zones, current) or {}).get('adjacentTo') or []:
            if other not in seen:
                seen[other] = seen[current] + 1
                queue.append(other)
    return None


def first_step(zones: list[dict[str, Any]], from_id: Any, to_id: Any) -> str:
    """The zone next to ``from_id`` on the shortest way to ``to_id``."""

    steps = zone_steps(zones, from_id, to_id)
    zone = zone_by_id(zones, from_id) or {}
    return next(
        (other for other in zone.get('adjacentTo') or [] if steps is not None and zone_steps(zones, other, to_id) == steps - 1),
        '',
    )


def zone_phrase(zone: Mapping[str, Any] | None) -> str:
    """``the common room`` for "The Common Room"; a name without a leading article is kept as it is."""

    name = _text((zone or {}).get('name'))
    return name.lower() if name.lower().startswith('the ') else name


def move_label(zone: Mapping[str, Any]) -> str:
    """``Go into the common room`` when the pack gives it, else ``Go to the stable``."""

    return _text(zone.get('moveLabel')) or f'Go to {zone_phrase(zone)}'


def _capitalized(text: str) -> str:
    return f'{text[:1].upper()}{text[1:]}'


def npc_zone_id(state: Mapping[str, Any], npc_id: Any, zones: list[dict[str, Any]] | None = None) -> str:
    zones = place_zones(state) if zones is None else zones
    if not zones:
        return ''
    if any(_text(npc.get('id')) == _text(npc_id) for npc in _dicts(state.get('partyNpcs'))):
        heroes = present_heroes(state)
        return hero_zone_id(state, heroes[0].get('id'), zones) if heroes else start_zone_id(state, zones)
    record = catalog_record(dict(state), 'npcs', npc_id) or next(
        (npc for npc in _dicts(state.get('knownNpcs')) if _text(npc.get('id')) == _text(npc_id)), {}
    )
    return _in_place(zones, record.get('zoneId'), start_zone_id(state, zones))


def _scene_object(state: Mapping[str, Any], object_id: Any) -> dict[str, Any]:
    scene = _scene(state)
    return next(
        (entry for collection in ('interactables', 'hazards') for entry in _dicts(scene.get(collection)) if _text(entry.get('id')) == _text(object_id)),
        {},
    )


def object_zone_id(state: Mapping[str, Any], object_id: Any, zones: list[dict[str, Any]] | None = None) -> str:
    zones = place_zones(state) if zones is None else zones
    if not zones:
        return ''
    return _in_place(zones, _scene_object(state, object_id).get('zoneId'), start_zone_id(state, zones))


def item_zone_id(state: Mapping[str, Any], item: Mapping[str, Any], zones: list[dict[str, Any]] | None = None) -> str:
    """Where a loose item lies: in its container's zone, or its own; '' when nothing places it (reachable anywhere)."""

    zones = place_zones(state) if zones is None else zones
    if not zones:
        return ''
    if _text(item.get('containerId')) and _scene_object(state, item.get('containerId')):
        return object_zone_id(state, item.get('containerId'), zones)
    return _in_place(zones, item.get('zoneId'), '')


def merchant_zone_id(state: Mapping[str, Any], merchant: Mapping[str, Any], zones: list[dict[str, Any]] | None = None) -> str:
    zones = place_zones(state) if zones is None else zones
    if not zones:
        return ''
    if _text(merchant.get('npcId')):
        return npc_zone_id(state, merchant.get('npcId'), zones)
    return _in_place(zones, merchant.get('zoneId'), start_zone_id(state, zones))


def captive_zone_id(state: Mapping[str, Any], captive: Mapping[str, Any], zones: list[dict[str, Any]] | None = None) -> str:
    zones = place_zones(state) if zones is None else zones
    return _in_place(zones, captive.get('zoneId'), start_zone_id(state, zones)) if zones else ''


def present_heroes(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The heroes at the table (every hero when none is marked present)."""

    heroes = [hero for hero in _dicts(state.get('playerCharacters')) if _text(hero.get('id'))]
    active = {_text(value) for value in state.get('activePlayerIds') or []}
    present = [hero for hero in heroes if not active or _text(hero.get('playerId')) in active]
    return present or heroes


def visited_zone_ids(state: Mapping[str, Any], zones: list[dict[str, Any]] | None = None) -> list[str]:
    zones = place_zones(state) if zones is None else zones
    stored = [_text(value) for value in _scene(state).get(VISITED_ZONES_KEY) or [] if zone_by_id(zones, value)]
    stood = [hero_zone_id(state, hero.get('id'), zones) for hero in present_heroes(state)]
    return list(dict.fromkeys([*stored, *stood, *([start_zone_id(state, zones)] if zones else [])]))


def known_zone_ids(state: Mapping[str, Any], zones: list[dict[str, Any]] | None = None) -> list[str]:
    """Zones a hero has stood in or next to, in the pack's order."""

    zones = place_zones(state) if zones is None else zones
    visited = set(visited_zone_ids(state, zones))
    near = {other for zone_id in visited for other in (zone_by_id(zones, zone_id) or {}).get('adjacentTo') or []}
    return [zone['id'] for zone in zones if zone['id'] in visited | near]


def fight_running(state: Mapping[str, Any]) -> bool:
    combat = state.get('combat') if isinstance(state.get('combat'), Mapping) else {}
    return _text(combat.get('status')).lower() in ACTIVE_COMBAT_STATUSES


def present_npcs(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The known people and companions present at the place."""

    present = {_text(value) for value in _scene(state).get('activeNpcIds') or []}
    party = [npc for npc in _dicts(state.get('partyNpcs')) if _text(npc.get('name'))]
    party_ids = {_text(npc.get('id')) for npc in party}
    known = [
        npc
        for npc in _dicts(state.get('knownNpcs'))
        if _text(npc.get('id')) in present and _text(npc.get('id')) not in party_ids and _text(npc.get('name'))
    ]
    return [*known, *party]


def people_in_zone(state: Mapping[str, Any], zone_id: str, zones: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Who stands in a zone: ``[{"id", "name", "kind"}]`` — heroes, then known people, then companions."""

    zones = place_zones(state) if zones is None else zones
    party_ids = {_text(npc.get('id')) for npc in _dicts(state.get('partyNpcs'))}
    heroes = [
        {'id': _text(hero.get('id')), 'name': _text(hero.get('name')), 'kind': 'hero'}
        for hero in present_heroes(state)
        if _text(hero.get('name')) and hero_zone_id(state, hero.get('id'), zones) == zone_id
    ]
    people = [
        {'id': _text(npc.get('id')), 'name': _text(npc.get('name')), 'kind': 'companion' if _text(npc.get('id')) in party_ids else 'person'}
        for npc in present_npcs(state)
        if npc_zone_id(state, npc.get('id'), zones) == zone_id
    ]
    return [*heroes, *sorted(people, key=lambda person: person['kind'] == 'companion')]


def zone_move_change(state: Mapping[str, Any], *, actor_id: str, zone_id: str, turn_id: Any) -> dict[str, Any]:
    """The change that moves a hero into a zone of the place (a turn, no clock time)."""

    zones = place_zones(state)
    zone = zone_by_id(zones, zone_id) or {}
    return {
        'id': stable_change_id(turn_id, ZONE_MOVE_CHANGE, actor_id, zone_id),
        'turnId': turn_id,
        'type': ZONE_MOVE_CHANGE,
        'source': 'zone_move',
        'actorId': actor_id,
        'zoneId': _text(zone_id),
        'zoneName': _text(zone.get('name')),
        'fromZoneId': hero_zone_id(state, actor_id, zones),
        'reason': f"{_capitalized(zone_phrase(zone)) or 'Another part of the place'} is next door.",
        'visible': True,
    }


def zone_move_refusal(state: Mapping[str, Any], *, actor_id: str, zone_id: str) -> str | None:
    """Why the hero cannot move into this zone now; None when they can."""

    zones = place_zones(state)
    zone = zone_by_id(zones, zone_id)
    if zone is None:
        return 'There is no such place here.'
    if fight_running(state):
        return 'Not while the fight is on.'
    here = hero_zone_id(state, actor_id, zones)
    steps = zone_steps(zones, here, zone['id'])
    if steps == 0:
        return f'You are already in {zone_phrase(zone)}.'
    if steps is None:
        return f'There is no way into {zone_phrase(zone)} from here.'
    if steps > 1:
        between = zone_by_id(zones, first_step(zones, here, zone['id'])) or {}
        return f'{_capitalized(zone_phrase(zone))} is beyond {zone_phrase(between)}. {GO_THERE_FIRST}'
    return None


def apply_zone_move(state: dict[str, Any], change: Mapping[str, Any]) -> dict[str, Any] | None:
    """Stand the hero in the zone; returns what the applied change records (``firstVisit``), or None."""

    zones = place_zones(state)
    zone = zone_by_id(zones, change.get('zoneId'))
    actor_id = _text(change.get('actorId'))
    scene = _scene(state)
    if zone is None or not actor_id or not scene:
        return None
    visited = visited_zone_ids(state, zones)
    stood = scene.get('playerZones') if isinstance(scene.get('playerZones'), dict) else {}
    scene['playerZones'] = {**stood, actor_id: zone['id']}
    scene[VISITED_ZONES_KEY] = list(dict.fromkeys([*visited, zone['id']]))
    return {'zoneId': zone['id'], 'zoneName': zone['name'], 'firstVisit': zone['id'] not in visited}


def arrive_in_start_zone(state: dict[str, Any]) -> None:
    """Arriving at a place stands every hero in its start zone."""

    zones = place_zones(state)
    scene = _scene(state)
    if not zones or not scene:
        return
    start = start_zone_id(state, zones)
    scene['playerZones'] = {_text(hero.get('id')): start for hero in _dicts(state.get('playerCharacters')) if _text(hero.get('id'))}
    stored = [_text(value) for value in scene.get(VISITED_ZONES_KEY) or [] if zone_by_id(zones, value)]
    scene[VISITED_ZONES_KEY] = list(dict.fromkeys([*stored, start]))


def gather_in_zone(state: dict[str, Any], zone_id: str) -> None:
    """Stand every hero in one zone of the place (the fight's, once it starts and when it ends)."""

    zones = place_zones(state)
    scene = _scene(state)
    if not zone_by_id(zones, zone_id) or not scene:
        return
    scene['playerZones'] = {_text(hero.get('id')): zone_id for hero in _dicts(state.get('playerCharacters')) if _text(hero.get('id'))}
    scene[VISITED_ZONES_KEY] = list(dict.fromkeys([*visited_zone_ids(state, zones), zone_id]))


def encounter_zone_id(state: Mapping[str, Any], encounter: Mapping[str, Any]) -> str:
    """Where a pack encounter's fight happens: its ``zoneId`` when it is a zone of the place, else the zone of the
    object whose action triggered it, else the start zone; '' for a place without zones."""

    zones = place_zones(state)
    if not zones:
        return ''
    if zone_by_id(zones, encounter.get('zoneId')):
        return _text(encounter.get('zoneId'))
    trigger = encounter.get('trigger') if isinstance(encounter.get('trigger'), Mapping) else {}
    if _text(trigger.get('type')).lower() == 'on_interactable' and _scene_object(state, trigger.get('targetId')):
        return object_zone_id(state, trigger.get('targetId'), zones)
    return start_zone_id(state, zones)


def zones_for_viewer(state: Mapping[str, Any], hero_id: Any) -> list[dict[str, Any]]:
    """The place's known zones for one hero's view: ``{"id", "name", "current", "adjacent", "exit", "here",
    "moveLabel"?}`` with ``here`` the heroes, known people and companions there by name."""

    zones = place_zones(state)
    if not zones:
        return []
    here = hero_zone_id(state, hero_id, zones)
    known = set(known_zone_ids(state, zones))
    exits_marked = any(zone['exit'] for zone in zones)
    view = []
    for zone in zones:
        if zone['id'] not in known:
            continue
        adjacent = zone_steps(zones, here, zone['id']) == 1
        entry: dict[str, Any] = {
            'id': zone['id'],
            'name': zone['name'],
            'current': zone['id'] == here,
            'adjacent': adjacent,
            'exit': zone['exit'] or not exits_marked,
            'here': people_in_zone(state, zone['id'], zones),
        }
        if adjacent:
            entry['moveLabel'] = move_label(zone)
        view.append(entry)
    return view


def _names(people: list[dict[str, Any]]) -> str:
    names = [person['name'] for person in people]
    if len(names) <= 2:
        return ' and '.join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def where_line(state: Mapping[str, Any], hero_id: Any) -> str:
    """``Thalion is in the yard with Osric Hale and Tovin Bell. Next door, in the common room: Mara Reedwake, Corin
    Ashby.``; '' for a place without zones."""

    zones = place_zones(state)
    if not zones:
        return ''
    here = zone_by_id(zones, hero_zone_id(state, hero_id, zones)) or {}
    hero = next((record for record in _dicts(state.get('playerCharacters')) if _text(record.get('id')) == _text(hero_id)), {})
    others = [person for person in people_in_zone(state, here.get('id', ''), zones) if person['id'] != _text(hero_id)]
    parts = [f"{_text(hero.get('name')) or 'The hero'} is in {zone_phrase(here)}{' with ' + _names(others) if others else ''}."]
    for zone_id in here.get('adjacentTo') or []:
        people = people_in_zone(state, zone_id, zones)
        if people:
            parts.append(f"Next door, in {zone_phrase(zone_by_id(zones, zone_id))}: {', '.join(person['name'] for person in people)}.")
    return ' '.join(parts)


def names_here(state: Mapping[str, Any], hero_id: Any) -> list[str] | None:
    """The known people and companions in the hero's zone, by name; None for a place without zones."""

    zones = place_zones(state)
    if not zones:
        return None
    here = hero_zone_id(state, hero_id, zones)
    return [person['name'] for person in people_in_zone(state, here, zones) if person['kind'] != 'hero']


def zone_reach(state: Mapping[str, Any], *, actor_id: str, zone_id: str, subject: str) -> tuple[str, str]:
    """``(refusal, move_to)`` for an action on something in ``zone_id``: nothing when it is in the hero's zone; the
    move first when it is next door and no fight is on; else ``{Subject} is in {zone}. Go there first.``"""

    zones = place_zones(state)
    if not zones or not zone_id:
        return '', ''
    here = hero_zone_id(state, actor_id, zones)
    steps = zone_steps(zones, here, zone_id)
    if steps == 0:
        return '', ''
    if steps == 1 and not fight_running(state):
        return '', zone_id
    return f'{_capitalized(subject)} is in {zone_phrase(zone_by_id(zones, zone_id))}. {GO_THERE_FIRST}', ''


def rest_refusal(state: Mapping[str, Any], *, actor_id: str) -> str | None:
    """Why the hero cannot rest where they stand: resting happens in the zones marked ``rest``."""

    zones = place_zones(state)
    rests = [zone for zone in zones if zone['rest']]
    if not rests:
        return None
    here = hero_zone_id(state, actor_id, zones)
    if any(zone['id'] == here for zone in rests):
        return None
    return f"You can rest in {' or '.join(zone_phrase(zone) for zone in rests)}. {GO_THERE_FIRST}"


def the_name(name: Any) -> str:
    """``the Wagon Strongbox`` for an object, as a refusal names it."""

    text = _text(name)
    return text if text.lower().startswith('the ') else f'the {text}'
