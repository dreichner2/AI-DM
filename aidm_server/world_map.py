"""World map data: a deterministic layout and the player projection of places.

Pack authors may place locations themselves (``mapPosition`` on a 0-100 grid);
every location without a position is placed by :func:`layout`, a seeded
force-directed relaxation over the adjacency graph. The importer stores the
computed positions, so a campaign keeps the same map for its whole life.
:func:`project_world_map` turns a session snapshot into the player-safe
``worldMap`` the client draws.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import lru_cache
import hashlib
import math
import random
from typing import Any


MAP_MIN = 8
MAP_MAX = 92
MIN_SPACING = 14
TERRAINS = ('moor', 'forest', 'mountain', 'coast', 'plains', 'marsh')
DEFAULT_TERRAIN = 'plains'
MAP_ICONS = (
    'inn',
    'town',
    'road',
    'bridge',
    'ruin',
    'tower',
    'forest',
    'cave',
    'camp',
    'shrine',
    'cairn',
    'valley',
    'gate',
    'marsh',
    'peak',
    'unknown',
)
ROUTE_STYLES = ('road', 'trail', 'hidden')
DEFAULT_ROUTE_STYLE = 'road'
RUMOURED_NAME = '?'
_UNREACHABLE_STATUSES = frozenset({'hidden', 'inaccessible'})


def _text(value: Any) -> str:
    return str(value or '').strip()


def _location_id(record: Any) -> str:
    return _text(record.get('id')) if isinstance(record, dict) else ''


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]


def map_position(value: Any) -> tuple[float, float] | None:
    """An authored ``{"x", "y"}`` position inside the 0-100 grid, or None."""

    if not isinstance(value, dict):
        return None
    coordinates = []
    for key in ('x', 'y'):
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not 0 <= raw <= 100:
            return None
        coordinates.append(float(raw))
    return coordinates[0], coordinates[1]


def adjacency(locations: Iterable[dict[str, Any]]) -> list[tuple[str, str]]:
    """Undirected, sorted adjacency pairs between the given locations."""

    records = [record for record in locations if _location_id(record)]
    known = {_location_id(record) for record in records}
    pairs: set[tuple[str, str]] = set()
    for record in records:
        source = _location_id(record)
        for target in _string_list(record.get('connectedLocationIds')):
            if target in known and target != source:
                pairs.add((min(source, target), max(source, target)))
    return sorted(pairs)


def numeric_seed(value: Any) -> int:
    """An integer seed: authored integers as given, anything else hashed."""

    if isinstance(value, int) and not isinstance(value, bool):
        return value
    digest = hashlib.sha256(_text(value).encode('utf-8')).digest()
    return int.from_bytes(digest[:4], 'big')


def layout(locations: Iterable[dict[str, Any]], seed: Any) -> dict[str, dict[str, int]]:
    """Return ``{location_id: {"x", "y"}}`` for every location.

    Authored ``mapPosition`` values are kept. The others come from a seeded
    force-directed relaxation over ``connectedLocationIds``; when nothing is
    authored the result is normalised to 8-92 on both axes. Nodes are then
    nudged to the nearest free point so no two are closer than 14 units
    (best effort once the grid is too crowded for that to be possible).
    """

    records = []
    seen: set[str] = set()
    for record in locations:
        location_id = _location_id(record)
        if location_id and location_id not in seen:
            seen.add(location_id)
            records.append(record)
    ids = tuple(_location_id(record) for record in records)
    pinned = tuple(
        (location_id, position)
        for location_id, record in zip(ids, records)
        if (position := map_position(record.get('mapPosition'))) is not None
    )
    return {
        location_id: {'x': x, 'y': y}
        for location_id, (x, y) in _layout(ids, tuple(adjacency(records)), pinned, numeric_seed(seed)).items()
    }


@lru_cache(maxsize=128)
def _layout(
    ids: tuple[str, ...],
    edges: tuple[tuple[str, str], ...],
    pinned: tuple[tuple[str, tuple[float, float]], ...],
    seed: int,
) -> dict[str, tuple[int, int]]:
    if not ids:
        return {}
    fixed = dict(pinned)
    free = [location_id for location_id in ids if location_id not in fixed]
    rng = random.Random(seed)
    positions = {
        location_id: list(fixed[location_id])
        if location_id in fixed
        else [rng.uniform(MAP_MIN, MAP_MAX), rng.uniform(MAP_MIN, MAP_MAX)]
        for location_id in ids
    }
    if free:
        _relax(ids, edges, positions, set(fixed))
        if not fixed:
            _normalise(positions)
    rounded = {
        location_id: (round(positions[location_id][0]), round(positions[location_id][1]))
        for location_id in ids
    }
    return _spread(ids, rounded, set(fixed))


def _relax(
    ids: tuple[str, ...],
    edges: tuple[tuple[str, str], ...],
    positions: dict[str, list[float]],
    fixed: set[str],
) -> None:
    span = MAP_MAX - MAP_MIN
    ideal = math.sqrt(span * span / len(ids))
    iterations = max(40, min(200, 6000 // len(ids)))
    temperature = span / 4
    cooling = temperature / iterations
    for _ in range(iterations):
        displacement = {location_id: [0.0, 0.0] for location_id in ids}
        for index, first in enumerate(ids):
            first_position = positions[first]
            for second in ids[index + 1:]:
                dx = first_position[0] - positions[second][0]
                dy = first_position[1] - positions[second][1]
                distance = max(0.01, math.hypot(dx, dy))
                force = ideal * ideal / distance
                displacement[first][0] += dx / distance * force
                displacement[first][1] += dy / distance * force
                displacement[second][0] -= dx / distance * force
                displacement[second][1] -= dy / distance * force
        for first, second in edges:
            dx = positions[first][0] - positions[second][0]
            dy = positions[first][1] - positions[second][1]
            distance = max(0.01, math.hypot(dx, dy))
            force = distance * distance / ideal
            displacement[first][0] -= dx / distance * force
            displacement[first][1] -= dy / distance * force
            displacement[second][0] += dx / distance * force
            displacement[second][1] += dy / distance * force
        for location_id in ids:
            if location_id in fixed:
                continue
            dx, dy = displacement[location_id]
            length = max(0.01, math.hypot(dx, dy))
            step = min(length, temperature)
            position = positions[location_id]
            position[0] = min(MAP_MAX, max(MAP_MIN, position[0] + dx / length * step))
            position[1] = min(MAP_MAX, max(MAP_MIN, position[1] + dy / length * step))
        temperature = max(0.5, temperature - cooling)


def _normalise(positions: dict[str, list[float]]) -> None:
    for axis in (0, 1):
        values = [position[axis] for position in positions.values()]
        low, high = min(values), max(values)
        for position in positions.values():
            position[axis] = (
                (MAP_MIN + MAP_MAX) / 2
                if high - low < 1e-9
                else MAP_MIN + (position[axis] - low) / (high - low) * (MAP_MAX - MAP_MIN)
            )


def _far_enough(point: tuple[int, int], placed: Iterable[tuple[int, int]]) -> bool:
    return all(math.hypot(point[0] - other[0], point[1] - other[1]) >= MIN_SPACING for other in placed)


def _spread(
    ids: tuple[str, ...],
    positions: dict[str, tuple[int, int]],
    fixed: set[str],
) -> dict[str, tuple[int, int]]:
    placed = {location_id: positions[location_id] for location_id in ids if location_id in fixed}
    for location_id in ids:
        if location_id in fixed:
            continue
        target = positions[location_id]
        placed[location_id] = (
            target if _far_enough(target, placed.values()) else _nearest_free_point(target, list(placed.values()))
        )
    return {location_id: placed[location_id] for location_id in ids}


def _nearest_free_point(target: tuple[int, int], placed: list[tuple[int, int]]) -> tuple[int, int]:
    for radius in range(1, MAP_MAX - MAP_MIN + 1):
        candidates = [
            (x, y)
            for x in range(target[0] - radius, target[0] + radius + 1)
            for y in (target[1] - radius, target[1] + radius)
        ] + [
            (x, y)
            for x in (target[0] - radius, target[0] + radius)
            for y in range(target[1] - radius + 1, target[1] + radius)
        ]
        valid = [
            point
            for point in candidates
            if MAP_MIN <= point[0] <= MAP_MAX and MAP_MIN <= point[1] <= MAP_MAX and _far_enough(point, placed)
        ]
        if valid:
            return min(valid, key=lambda point: (math.hypot(point[0] - target[0], point[1] - target[1]), point))
    return target


def route_styles(routes: Any) -> dict[tuple[str, str], str]:
    """Authored route styles keyed by the undirected location pair."""

    styles: dict[tuple[str, str], str] = {}
    for route in routes if isinstance(routes, list) else []:
        if not isinstance(route, dict):
            continue
        first, second = _text(route.get('from')), _text(route.get('to'))
        style = _text(route.get('style')).lower()
        if first and second and first != second and style in ROUTE_STYLES:
            styles[(min(first, second), max(first, second))] = style
    return styles


def map_icon(record: dict[str, Any]) -> str:
    """The authored icon, else the location type when it names an icon."""

    for value in (record.get('mapIcon'), record.get('type')):
        icon = _text(value).lower()
        if icon in MAP_ICONS:
            return icon
    return 'unknown'


def project_world_map(
    snapshot: dict[str, Any],
    *,
    record_visible: Callable[[dict[str, Any]], bool],
) -> dict[str, Any] | None:
    """The player-safe ``worldMap`` for one snapshot, or None without places.

    ``current`` is the party's location; ``visited`` places the party has
    been; ``known`` places discovered but not visited; ``rumoured`` is an
    undiscovered catalog neighbour of a visited place, shown only as ``?``.
    Nothing else appears. Hidden routes appear only between discovered places.
    """

    pack = snapshot.get('campaignPack') if isinstance(snapshot.get('campaignPack'), dict) else {}
    catalog = pack.get('catalog') if isinstance(pack.get('catalog'), dict) else {}
    catalog_locations = [
        record
        for record in (catalog.get('locations') if isinstance(catalog.get('locations'), list) else [])
        if _location_id(record) and record_visible(record)
    ]
    known_locations = [
        record
        for record in (snapshot.get('locations') if isinstance(snapshot.get('locations'), list) else [])
        if _location_id(record) and record_visible(record) and _text(record.get('status')).lower() != 'hidden'
    ]
    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    flags = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}
    current_id = _text(scene.get('locationId'))
    known_by_id = {_location_id(record): record for record in known_locations}
    catalog_by_id = {_location_id(record): record for record in catalog_locations}
    visited = {
        location_id
        for location_id, record in known_by_id.items()
        if _text(record.get('status')).lower() == 'visited' or record.get('lastVisitedTurn') is not None
    }
    visited.update(location_id for location_id in _string_list(flags.get('locationEnteredIds')) if location_id in known_by_id)
    if current_id in known_by_id:
        visited.add(current_id)
    styles = route_styles(pack.get('routes'))
    connections: dict[str, set[str]] = {}
    for record in [*catalog_locations, *known_locations]:
        connections.setdefault(_location_id(record), set()).update(_string_list(record.get('connectedLocationIds')))
    edges = sorted(
        {
            (min(first, second), max(first, second))
            for first, targets in connections.items()
            for second in targets
            if second in connections and second != first
        }
        | {pair for pair in styles if pair[0] in connections and pair[1] in connections}
    )
    rumoured = {
        neighbour
        for first, second in edges
        if styles.get((first, second), DEFAULT_ROUTE_STYLE) != 'hidden'
        for source, neighbour in ((first, second), (second, first))
        if source in visited and neighbour not in known_by_id and neighbour in catalog_by_id
    }
    node_ids = [
        *(location_id for location_id in known_by_id),
        *(location_id for location_id in catalog_by_id if location_id in rumoured and location_id not in known_by_id),
    ]
    if not node_ids:
        return None
    map_settings = pack.get('map') if isinstance(pack.get('map'), dict) else {}
    seed = map_settings.get('seed') if 'seed' in map_settings else numeric_seed(pack.get('packId') or snapshot.get('campaignId'))
    layout_records = [
        {
            'id': location_id,
            'mapPosition': (catalog_by_id.get(location_id) or known_by_id.get(location_id) or {}).get('mapPosition'),
            'connectedLocationIds': [
                second if first == location_id else first
                for first, second in edges
                if location_id in (first, second)
            ],
        }
        for location_id in node_ids
    ]
    positions = layout(layout_records, seed)
    nodes = []
    for location_id in node_ids:
        record = known_by_id.get(location_id) or catalog_by_id.get(location_id) or {}
        authored = catalog_by_id.get(location_id) or record
        if location_id in rumoured and location_id not in known_by_id:
            state, name, icon = 'rumoured', RUMOURED_NAME, 'unknown'
        else:
            state = 'current' if location_id == current_id else 'visited' if location_id in visited else 'known'
            name, icon = _text(record.get('name')) or location_id, map_icon(authored)
        nodes.append({'id': location_id, 'name': name, 'icon': icon, **positions[location_id], 'state': state})
    node_states = {node['id']: node['state'] for node in nodes}
    discovered = {location_id for location_id, state in node_states.items() if state != 'rumoured'}
    routes = []
    for first, second in edges:
        if first not in node_states or second not in node_states:
            continue
        style = styles.get((first, second), DEFAULT_ROUTE_STYLE)
        both_discovered = first in discovered and second in discovered
        if style == 'hidden' and not both_discovered:
            continue
        travelable = both_discovered and all(
            _text(known_by_id[location_id].get('status')).lower() not in _UNREACHABLE_STATUSES
            for location_id in (first, second)
        ) and (
            second in _string_list(known_by_id[first].get('connectedLocationIds'))
            or first in _string_list(known_by_id[second].get('connectedLocationIds'))
        )
        routes.append({'from': first, 'to': second, 'style': style, 'travelable': travelable})
    terrain = _text(map_settings.get('terrain')).lower()
    return {
        'title': _text(map_settings.get('title')) or _text(pack.get('title')),
        'terrain': terrain if terrain in TERRAINS else DEFAULT_TERRAIN,
        'seed': numeric_seed(seed),
        'nodes': nodes,
        'routes': routes,
    }
