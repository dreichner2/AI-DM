from __future__ import annotations

from itertools import combinations
import math

from aidm_server.database import db
from aidm_server.models import Session, safe_json_loads
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from aidm_server.world_map import MAP_MAX, MAP_MIN, MIN_SPACING, layout


def _chain(count: int) -> list[dict]:
    return [
        {
            'id': f'loc_{index}',
            'connectedLocationIds': [f'loc_{index + 1}'] if index + 1 < count else [],
        }
        for index in range(count)
    ]


def _distances(positions: dict[str, dict[str, int]]) -> list[float]:
    return [
        math.hypot(first['x'] - second['x'], first['y'] - second['y'])
        for first, second in combinations(positions.values(), 2)
    ]


def test_layout_is_deterministic_for_a_seed():
    first = layout(_chain(9), 42)
    second = layout([dict(record) for record in _chain(9)], 42)

    assert first == second
    assert layout(_chain(9), 43) != first


def test_layout_is_normalised_to_the_map_bounds_and_keeps_minimum_spacing():
    for count in (2, 5, 12, 25):
        positions = layout(_chain(count), 7)

        assert set(positions) == {f'loc_{index}' for index in range(count)}
        for position in positions.values():
            assert MAP_MIN <= position['x'] <= MAP_MAX
            assert MAP_MIN <= position['y'] <= MAP_MAX
        assert min(_distances(positions)) >= MIN_SPACING
        xs = [position['x'] for position in positions.values()]
        ys = [position['y'] for position in positions.values()]
        assert (min(xs), max(xs), min(ys), max(ys)) == (MAP_MIN, MAP_MAX, MAP_MIN, MAP_MAX)


def test_layout_keeps_authored_positions_and_places_the_rest_around_them():
    records = _chain(4)
    records[0]['mapPosition'] = {'x': 50, 'y': 50}

    positions = layout(records, 3)

    assert positions['loc_0'] == {'x': 50, 'y': 50}
    assert min(_distances(positions)) >= MIN_SPACING


def _pack_snapshot() -> dict:
    catalog_locations = [
        {'id': 'inn', 'name': 'Lantern Post Inn', 'mapIcon': 'inn', 'mapPosition': {'x': 20, 'y': 50},
         'connectedLocationIds': ['road', 'cellar']},
        {'id': 'road', 'name': 'Old North Road', 'type': 'road', 'mapPosition': {'x': 50, 'y': 50},
         'connectedLocationIds': ['inn', 'ridge', 'bridge']},
        {'id': 'ridge', 'name': 'Ridge', 'mapIcon': 'peak', 'mapPosition': {'x': 80, 'y': 20},
         'connectedLocationIds': ['road']},
        {'id': 'bridge', 'name': 'Bridge', 'mapIcon': 'bridge', 'mapPosition': {'x': 80, 'y': 80},
         'connectedLocationIds': ['road']},
        {'id': 'cellar', 'name': 'Hidden Cellar', 'mapIcon': 'cave', 'mapPosition': {'x': 20, 'y': 85},
         'connectedLocationIds': ['inn']},
        {'id': 'vault', 'name': 'Vault', 'mapPosition': {'x': 50, 'y': 15}, 'connectedLocationIds': ['ridge']},
    ]
    return {
        'campaignId': 1,
        'currentScene': {'locationId': 'road'},
        'flags': {'locationEnteredIds': ['inn', 'road']},
        'locations': [
            {**catalog_locations[0], 'status': 'visited'},
            {**catalog_locations[1], 'status': 'visited'},
            {**catalog_locations[3], 'status': 'discovered'},
        ],
        'campaignPack': {
            'packId': 'roadlands',
            'title': 'Roadlands',
            'map': {'title': 'The Western Roadlands', 'terrain': 'moor', 'seed': 9},
            'routes': [
                {'from': 'inn', 'to': 'cellar', 'style': 'hidden'},
                {'from': 'road', 'to': 'ridge', 'style': 'trail'},
            ],
            'catalog': {'locations': catalog_locations},
        },
    }


def test_player_projection_marks_current_visited_known_and_rumoured_places():
    world_map = filter_session_snapshot_for_player(_pack_snapshot())['worldMap']

    states = {node['id']: (node['state'], node['name']) for node in world_map['nodes']}
    assert states == {
        'inn': ('visited', 'Lantern Post Inn'),
        'road': ('current', 'Old North Road'),
        'bridge': ('known', 'Bridge'),
        'ridge': ('rumoured', '?'),
    }
    assert world_map['title'] == 'The Western Roadlands'
    assert world_map['terrain'] == 'moor'
    assert world_map['seed'] == 9
    ridge = next(node for node in world_map['nodes'] if node['id'] == 'ridge')
    assert (ridge['icon'], ridge['x'], ridge['y']) == ('unknown', 80, 20)
    assert next(node for node in world_map['nodes'] if node['id'] == 'road')['icon'] == 'road'
    assert 'Hidden Cellar' not in str(world_map)
    assert 'vault' not in str(world_map)


def test_routes_carry_styles_and_hidden_routes_need_both_ends_discovered():
    snapshot = _pack_snapshot()
    routes = {
        (route['from'], route['to']): route
        for route in filter_session_snapshot_for_player(snapshot)['worldMap']['routes']
    }
    assert routes == {
        ('inn', 'road'): {'from': 'inn', 'to': 'road', 'style': 'road', 'travelable': True},
        ('bridge', 'road'): {'from': 'bridge', 'to': 'road', 'style': 'road', 'travelable': True},
        ('ridge', 'road'): {'from': 'ridge', 'to': 'road', 'style': 'trail', 'travelable': False},
    }

    snapshot['locations'].append({**snapshot['campaignPack']['catalog']['locations'][4], 'status': 'discovered'})
    routes = filter_session_snapshot_for_player(snapshot)['worldMap']['routes']
    assert {'from': 'cellar', 'to': 'inn', 'style': 'hidden', 'travelable': True} in routes


def test_campaign_without_a_pack_maps_its_known_locations_with_a_layout():
    snapshot = {
        'campaignId': 4,
        'currentScene': {'locationId': 'tavern'},
        'locations': [
            {'id': 'tavern', 'name': 'Tavern', 'type': 'tavern', 'status': 'visited', 'connectedLocationIds': ['market']},
            {'id': 'market', 'name': 'Market', 'status': 'discovered', 'connectedLocationIds': ['tavern']},
        ],
    }

    world_map = filter_session_snapshot_for_player(snapshot)['worldMap']

    assert [(node['id'], node['state']) for node in world_map['nodes']] == [('tavern', 'current'), ('market', 'known')]
    assert all(MAP_MIN <= node['x'] <= MAP_MAX and MAP_MIN <= node['y'] <= MAP_MAX for node in world_map['nodes'])
    assert world_map['routes'] == [{'from': 'market', 'to': 'tavern', 'style': 'road', 'travelable': True}]
    assert filter_session_snapshot_for_player(snapshot)['worldMap'] == world_map
    assert 'worldMap' not in filter_session_snapshot_for_player({'currentScene': {}, 'locations': []})


def test_import_stores_computed_positions_and_map_settings(client, app):
    pack = {
        'packId': 'map_pack',
        'title': 'Map Pack',
        'map': {'title': 'The Marches', 'terrain': 'forest', 'seed': 11},
        'routes': [{'from': 'camp', 'to': 'ford', 'style': 'trail'}],
        'startingState': {'locationId': 'camp', 'knownLocationIds': ['camp', 'ford']},
        'locations': [
            {'id': 'camp', 'name': 'Camp', 'mapIcon': 'camp', 'connectedLocationIds': ['ford']},
            {'id': 'ford', 'name': 'Ford', 'mapPosition': {'x': 70, 'y': 30}, 'connectedLocationIds': ['camp', 'tower']},
            {'id': 'tower', 'name': 'Tower', 'connectedLocationIds': ['ford']},
        ],
    }
    response = client.post('/api/campaigns/import-pack', json={'pack': pack})
    assert response.status_code == 201, response.get_json()
    with app.app_context():
        snapshot = safe_json_loads(db.session.get(Session, response.get_json()['session_id']).state_snapshot, {})

    catalog = {location['id']: location for location in snapshot['campaignPack']['catalog']['locations']}
    assert catalog['ford']['mapPosition'] == {'x': 70, 'y': 30}
    assert all(MAP_MIN <= catalog[location_id]['mapPosition']['x'] <= MAP_MAX for location_id in ('camp', 'tower'))
    assert snapshot['campaignPack']['map'] == {'title': 'The Marches', 'terrain': 'forest', 'seed': 11}
    assert snapshot['campaignPack']['routes'] == [{'from': 'camp', 'to': 'ford', 'style': 'trail'}]

    world_map = filter_session_snapshot_for_player(snapshot)['worldMap']
    assert [(node['id'], node['state']) for node in world_map['nodes']] == [
        ('camp', 'current'),
        ('ford', 'known'),
    ]
    assert world_map['routes'] == [{'from': 'camp', 'to': 'ford', 'style': 'trail', 'travelable': True}]
