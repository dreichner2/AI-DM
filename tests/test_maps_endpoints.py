from __future__ import annotations

from aidm_server.database import db
from aidm_server.models import Map
from tests.helpers import seed_world_campaign_player_session


def test_create_map_rejects_campaign_world_mismatch(client, app):
    ids = seed_world_campaign_player_session(app)

    other_world_response = client.post('/api/worlds', json={'name': 'Other World', 'description': 'Secondary world'})
    assert other_world_response.status_code == 201
    other_world_id = other_world_response.get_json()['world_id']

    response = client.post(
        '/api/maps',
        json={
            'title': 'Conflicting Map',
            'world_id': other_world_id,
            'campaign_id': ids['campaign_id'],
            'map_data': {'nodes': []},
        },
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['error_code'] == 'campaign_world_mismatch'


def test_create_map_validates_request_body_and_fields(client, app):
    ids = seed_world_campaign_player_session(app)

    non_json_response = client.post('/api/maps', data='not-json', content_type='text/plain')
    assert non_json_response.status_code == 400
    assert non_json_response.get_json()['error_code'] == 'validation_error'

    empty_title_response = client.post('/api/maps', json={'title': '   '})
    assert empty_title_response.status_code == 400
    assert empty_title_response.get_json()['error_code'] == 'validation_error'

    numeric_title_response = client.post('/api/maps', json={'title': 123})
    assert numeric_title_response.status_code == 400
    assert numeric_title_response.get_json()['error_code'] == 'validation_error'

    long_description_response = client.post('/api/maps', json={'title': 'Valid', 'description': 'x' * 2001})
    assert long_description_response.status_code == 400
    assert long_description_response.get_json()['error_code'] == 'validation_error'

    invalid_id_response = client.post('/api/maps', json={'title': 'Valid', 'world_id': 'not-an-id'})
    assert invalid_id_response.status_code == 400
    assert invalid_id_response.get_json()['error_code'] == 'validation_error'

    invalid_map_data_response = client.post(
        '/api/maps',
        json={
            'title': 'Valid',
            'world_id': ids['world_id'],
            'campaign_id': ids['campaign_id'],
            'map_data': ['not', 'an', 'object'],
        },
    )
    assert invalid_map_data_response.status_code == 400
    assert invalid_map_data_response.get_json()['error_code'] == 'validation_error'

    invalid_visibility_response = client.post(
        '/api/maps',
        json={
            'title': 'Valid',
            'world_id': ids['world_id'],
            'visibility': 'everyone_and_the_dm',
        },
    )
    assert invalid_visibility_response.status_code == 400
    assert invalid_visibility_response.get_json()['error_code'] == 'validation_error'

    orphan_response = client.post('/api/maps', json={'title': 'Ownerless Map', 'map_data': {}})
    assert orphan_response.status_code == 400
    assert orphan_response.get_json()['error_code'] == 'validation_error'


def test_update_map_validates_mutable_fields(client, app):
    ids = seed_world_campaign_player_session(app)
    create_response = client.post(
        '/api/maps',
        json={
            'title': 'Valid Map',
            'world_id': ids['world_id'],
            'campaign_id': ids['campaign_id'],
            'map_data': {'nodes': []},
        },
    )
    assert create_response.status_code == 201
    map_id = create_response.get_json()['map_id']

    non_json_response = client.patch(f'/api/maps/{map_id}', data='not-json', content_type='text/plain')
    assert non_json_response.status_code == 400
    assert non_json_response.get_json()['error_code'] == 'validation_error'

    empty_title_response = client.patch(f'/api/maps/{map_id}', json={'title': '   '})
    assert empty_title_response.status_code == 400
    assert empty_title_response.get_json()['error_code'] == 'validation_error'

    invalid_map_data_response = client.patch(f'/api/maps/{map_id}', json={'map_data': 'not-object'})
    assert invalid_map_data_response.status_code == 400
    assert invalid_map_data_response.get_json()['error_code'] == 'validation_error'

    invalid_visibility_response = client.patch(f'/api/maps/{map_id}', json={'visibility': 123})
    assert invalid_visibility_response.status_code == 400
    assert invalid_visibility_response.get_json()['error_code'] == 'validation_error'

    detail_response = client.get(f'/api/maps/{map_id}')
    assert detail_response.status_code == 200
    assert detail_response.get_json()['visibility'] == 'player'

    hide_response = client.patch(f'/api/maps/{map_id}', json={'visibility': 'dm_only'})
    assert hide_response.status_code == 200
    assert client.get(f'/api/maps/{map_id}').get_json()['visibility'] == 'dm'

    reveal_response = client.patch(f'/api/maps/{map_id}', json={'visibility': 'revealed'})
    assert reveal_response.status_code == 200
    assert client.get(f'/api/maps/{map_id}').get_json()['visibility'] == 'player'


def test_list_maps_returns_404_for_missing_campaign(client):
    response = client.get('/api/maps?campaign_id=99999')

    assert response.status_code == 404
    assert response.get_json()['error_code'] == 'campaign_not_found'


def test_map_endpoints_tolerate_corrupt_map_json(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        map_obj = Map(
            world_id=ids['world_id'],
            campaign_id=ids['campaign_id'],
            title='Corrupt Map',
            map_data='{not json',
        )
        db.session.add(map_obj)
        db.session.commit()
        map_id = map_obj.map_id

    list_response = client.get(f"/api/maps?campaign_id={ids['campaign_id']}")
    assert list_response.status_code == 200
    list_payload = list_response.get_json()
    listed = next(item for item in list_payload if item['map_id'] == map_id)
    assert listed['map_data'] == {}

    detail_response = client.get(f'/api/maps/{map_id}')
    assert detail_response.status_code == 200
    assert detail_response.get_json()['map_data'] == {}
