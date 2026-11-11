from __future__ import annotations

import json
from pathlib import Path

import pytest

from aidm_server.database import db
from aidm_server.models import Session, safe_json_loads
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_file, lint_campaign_pack_manifest


PLAY_NOW_PACK = Path(__file__).resolve().parents[1] / 'docs' / 'examples' / 'the_road_of_unremembered_kings_campaign.json'


def _pack(**inn_fields) -> dict:
    return {
        'packId': 'atmosphere_pack',
        'title': 'Atmosphere Pack',
        'locations': [
            {'id': 'inn', 'name': 'Inn', 'mapIcon': 'inn', 'connectedLocationIds': ['road'], **inn_fields},
            {'id': 'road', 'name': 'Road', 'connectedLocationIds': ['inn']},
        ],
        'startingState': {'locationId': 'inn'},
        'checkpoints': [{'id': 'cp_start', 'title': 'Start', 'terminal': True, 'playerTitle': 'Start'}],
    }


def test_ambience_and_weather_survive_import_on_the_catalog_and_the_party_location(client, app):
    response = client.post('/api/campaigns/import-pack', json={'pack': _pack(ambience='hearth', weather='rain')})
    assert response.status_code == 201, response.get_json()

    with app.app_context():
        snapshot = safe_json_loads(db.session.get(Session, response.get_json()['session_id']).state_snapshot, {})
    catalog_inn = snapshot['campaignPack']['catalog']['locations'][0]
    runtime_inn = next(location for location in snapshot['locations'] if location['id'] == 'inn')
    assert (catalog_inn['ambience'], catalog_inn['weather']) == ('hearth', 'rain')
    assert (runtime_inn['ambience'], runtime_inn['weather']) == ('hearth', 'rain')


@pytest.mark.parametrize(
    ('field', 'value', 'allowed'),
    [
        ('ambience', 'thunder', 'rain, wind, hearth, night, marsh, cave, hush'),
        ('weather', 'storm', 'rain, fog, snow, clear'),
        ('weather', 'Rain', 'rain, fog, snow, clear'),
    ],
)
def test_lint_rejects_an_unknown_ambience_or_weather_with_its_path(app, field, value, allowed):
    with app.app_context():
        result = lint_campaign_pack_manifest(_pack(**{field: value}), workspace_id='owner')

    assert result['ok'] is False
    assert {
        'severity': 'error',
        'code': 'invalid_campaign_pack_schema',
        'path': 'campaign pack',
        'message': f'locations[0].{field} must be one of: {allowed}.',
    } in result['issues']


def test_every_ambience_and_weather_value_lints_clean(app):
    with app.app_context():
        results = [
            lint_campaign_pack_manifest(_pack(ambience=ambience, weather=weather), workspace_id='owner', strict=True)
            for ambience in ('rain', 'wind', 'hearth', 'night', 'marsh', 'cave', 'hush')
            for weather in ('rain', 'fog', 'snow', 'clear')
        ]

    assert [issue for result in results for issue in result['issues']] == []


def test_play_now_pack_rains_at_the_inn_and_road_and_is_foggy_at_the_cairns_and_ridge(app):
    with app.app_context():
        result = lint_campaign_pack_file(PLAY_NOW_PACK, workspace_id='owner', strict=True)

    assert result['issues'] == []
    weather = {
        location['id']: location['weather']
        for location in json.loads(PLAY_NOW_PACK.read_text(encoding='utf-8'))['locations']
        if 'weather' in location
    }
    assert weather == {
        'loc_lantern_post_inn': 'rain',
        'loc_old_north_road': 'rain',
        'loc_cairns_roadwardens': 'fog',
        'loc_ridge_and_culvert_paths': 'fog',
    }
