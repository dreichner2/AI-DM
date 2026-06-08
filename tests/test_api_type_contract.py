from __future__ import annotations

from aidm_server.api_type_contract import API_TYPE_CONTRACT_BY_NAME
from aidm_server.database import db
from aidm_server.models import CampaignSegment, Map
from scripts.generate_api_types import OUTPUT, render_types
from tests.helpers import seed_world_campaign_player_session


def _contract_keys(type_name: str) -> set[str]:
    return {field.name for field in API_TYPE_CONTRACT_BY_NAME[type_name].fields}


def _assert_contract_payload(type_name: str, payload: dict) -> None:
    assert set(payload) == _contract_keys(type_name)


def _contract_field_type(type_name: str, field_name: str) -> str:
    for field in API_TYPE_CONTRACT_BY_NAME[type_name].fields:
        if field.name == field_name:
            return field.ts_type
    raise AssertionError(f'{type_name}.{field_name} is missing from API contract')


def test_generated_frontend_api_contract_is_current():
    assert OUTPUT.read_text(encoding='utf-8') == render_types()


def test_frontend_api_contract_marks_nullable_backend_strings():
    nullable_fields = {
        ('World', 'description'),
        ('Campaign', 'description'),
        ('Player', 'race'),
        ('Player', 'sex'),
        ('Player', 'class_'),
        ('Player', 'char_class'),
        ('PlayerDetail', 'race'),
        ('PlayerDetail', 'sex'),
        ('PlayerDetail', 'class_'),
        ('PlayerDetail', 'char_class'),
        ('MapItem', 'description'),
        ('CampaignSegment', 'description'),
        ('CampaignSegment', 'trigger_condition'),
        ('CampaignSegment', 'tags'),
    }

    for type_name, field_name in nullable_fields:
        assert _contract_field_type(type_name, field_name) == 'string | null'


def test_frontend_api_contract_matches_backend_endpoint_payloads(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        map_obj = Map(
            world_id=ids['world_id'],
            campaign_id=ids['campaign_id'],
            title='Contract Map',
            description='Shared API type map.',
            map_data='{"tiles": []}',
        )
        segment = CampaignSegment(
            campaign_id=ids['campaign_id'],
            title='Contract Segment',
            description='Shared API type segment.',
            trigger_condition='when contracts run',
            tags='contract',
            is_triggered=False,
        )
        db.session.add_all([map_obj, segment])
        db.session.commit()
        map_id = map_obj.map_id
        segment_id = segment.segment_id

    _assert_contract_payload('World', client.get(f"/api/worlds/{ids['world_id']}").get_json())
    _assert_contract_payload('Campaign', client.get(f"/api/campaigns/{ids['campaign_id']}").get_json())
    _assert_contract_payload('SessionState', client.get(f"/api/sessions/{ids['session_id']}/state").get_json())
    _assert_contract_payload('PlayerDetail', client.get(f"/api/players/{ids['player_id']}").get_json())
    _assert_contract_payload('MapItem', client.get(f'/api/maps/{map_id}').get_json())
    _assert_contract_payload('CampaignSegment', client.get(f'/api/segments/{segment_id}').get_json())

    session_list = client.get(f"/api/sessions/campaigns/{ids['campaign_id']}/sessions").get_json()
    _assert_contract_payload('SessionSummary', session_list[0])

    workspace = client.get(f"/api/campaigns/{ids['campaign_id']}/workspace").get_json()
    _assert_contract_payload('CampaignWorkspace', workspace)
    _assert_contract_payload('Campaign', workspace['campaign'])
    _assert_contract_payload('SessionSummary', workspace['sessions'][0])
    _assert_contract_payload('Player', workspace['players'][0])
    _assert_contract_payload('MapItem', next(item for item in workspace['maps'] if item['map_id'] == map_id))
    _assert_contract_payload(
        'CampaignSegment',
        next(item for item in workspace['segments'] if item['segment_id'] == segment_id),
    )
