from __future__ import annotations

from aidm_server.api_type_contract import API_TYPE_CONTRACT_BY_NAME
from aidm_server.database import db
from aidm_server.models import CampaignSegment, Map, Session, SessionLogEntry, safe_json_dumps
from scripts.generate_api_types import OUTPUT, main as generate_api_types_main, render_types
from tests.helpers import seed_played_turn, seed_world_campaign_player_session


def _contract_keys(type_name: str) -> set[str]:
    return {field.name for field in API_TYPE_CONTRACT_BY_NAME[type_name].fields}


def _assert_contract_payload(type_name: str, payload: dict) -> None:
    contract = API_TYPE_CONTRACT_BY_NAME[type_name]
    payload_keys = set(payload)
    required_keys = {field.name for field in contract.fields if not field.optional}
    assert required_keys <= payload_keys
    assert payload_keys <= _contract_keys(type_name)


def _contract_field_type(type_name: str, field_name: str) -> str:
    for field in API_TYPE_CONTRACT_BY_NAME[type_name].fields:
        if field.name == field_name:
            return field.ts_type
    raise AssertionError(f'{type_name}.{field_name} is missing from API contract')


def test_generated_frontend_api_contract_is_current():
    assert OUTPUT.read_text(encoding='utf-8') == render_types()


def test_generated_frontend_api_contract_check_mode_passes():
    assert generate_api_types_main(['--check']) == 0


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
        db.session.add_all(
            [
                map_obj,
                segment,
                SessionLogEntry(session_id=ids['session_id'], message='Contract log line.', entry_type='system'),
            ]
        )
        session = db.session.get(Session, ids['session_id'])
        session.state_snapshot = safe_json_dumps(
            {
                'recap': 'The party reached the contract gate.',
                'flags': {
                    'campaignPackActiveCheckpointId': 'cp_route',
                    'campaignPackCompletedCheckpointIds': ['cp_start'],
                },
                'campaignPack': {
                    'packId': 'contract_pack',
                    'title': 'Contract Pack',
                    'version': '1.0.0',
                    'schemaVersion': '1',
                    'checkpoints': [
                        {
                            'id': 'cp_start',
                            'title': 'Contract Gate',
                            'nextCheckpointIds': ['cp_route'],
                            'alternateCheckpointIds': ['cp_branch'],
                        },
                        {'id': 'cp_route', 'title': 'Contract Route', 'terminal': True},
                        {'id': 'cp_branch', 'title': 'Contract Branch', 'terminal': True},
                    ],
                    'catalog': {
                        'lore': [
                            {
                                'id': 'lore_contract',
                                'title': 'Contract Secret',
                                'summary': 'A hidden contract record.',
                                'hiddenToPlayers': True,
                                'checkpointIds': ['cp_branch'],
                            }
                        ]
                    },
                },
            },
            {},
        )
        db.session.commit()
        map_id = map_obj.map_id
        segment_id = segment.segment_id

    _assert_contract_payload('World', client.get(f"/api/worlds/{ids['world_id']}").get_json())
    _assert_contract_payload('Campaign', client.get(f"/api/campaigns/{ids['campaign_id']}").get_json())
    _assert_contract_payload('SessionState', client.get(f"/api/sessions/{ids['session_id']}/state").get_json())
    recap = client.get(f"/api/sessions/{ids['session_id']}/recap").get_json()
    _assert_contract_payload('SessionRecapResponse', recap)
    _assert_contract_payload('SessionRecapState', recap['state'])

    commentary = client.get(f"/api/sessions/{ids['session_id']}/campaign-pack/commentary").get_json()
    _assert_contract_payload('CampaignPackCommentaryResponse', commentary)
    _assert_contract_payload('CampaignPackCommentaryPack', commentary['pack'])
    _assert_contract_payload('CampaignPackCommentaryProgress', commentary['progress'])
    _assert_contract_payload('CampaignPackCommentaryGraph', commentary['graph'])
    _assert_contract_payload('CampaignPackCommentaryGraphNode', commentary['graph']['nodes'][0])
    _assert_contract_payload('CampaignPackCommentaryGraphEdge', commentary['graph']['edges'][0])
    _assert_contract_payload('CampaignPackCommentarySummary', commentary['summary'])
    _assert_contract_payload('CampaignPackCommentaryRecord', commentary['undiscoveredRecords']['lore'][0])
    _assert_contract_payload('CampaignPackCommentaryCheckpoint', commentary['routeTaken'][0])
    _assert_contract_payload('CampaignPackCommentaryCheckpoint', commentary['roadsNotTaken'][0])
    _assert_contract_payload('CampaignPackCommentaryCheckpoint', commentary['alternateEndings'][0])
    _assert_contract_payload('PlayerDetail', client.get(f"/api/players/{ids['player_id']}").get_json())
    _assert_contract_payload('MapItem', client.get(f'/api/maps/{map_id}').get_json())
    _assert_contract_payload('CampaignSegment', client.get(f'/api/segments/{segment_id}').get_json())

    session_list = client.get(f"/api/sessions/campaigns/{ids['campaign_id']}/sessions").get_json()
    _assert_contract_payload('SessionListResponse', session_list)
    _assert_contract_payload('SessionSummary', session_list['sessions'][0])

    save = client.get(f"/api/sessions/{ids['session_id']}/export").get_json()
    _assert_contract_payload('SessionSaveDocument', save)
    _assert_contract_payload('SessionSummary', save['session'])
    _assert_contract_payload('SessionLogEntry', save['log'][0])
    _assert_contract_payload('PlayerDetail', save['characters'][0])
    imported = client.post('/api/sessions/import', json=save).get_json()
    _assert_contract_payload('SessionImportResponse', imported)
    _assert_contract_payload('SessionSummary', imported['session'])

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

    seed_played_turn(app, ids['session_id'])
    started = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id']}).get_json()
    _assert_contract_payload('SessionStartResponse', started)
    ended_session = next(
        item
        for item in client.get(f"/api/sessions/campaigns/{ids['campaign_id']}/sessions").get_json()['sessions']
        if item['session_id'] == ids['session_id']
    )
    _assert_contract_payload('SessionSummary', ended_session)
    assert ended_session['status'] == 'ended'


def test_creation_options_endpoint_matches_its_contract_and_the_creation_tables(client):
    response = client.get('/api/rules/creation-options')

    assert response.status_code == 200
    payload = response.get_json()
    _assert_contract_payload('CreationOptionsResponse', payload)
    classes = {entry['id']: entry for entry in payload['classes']}
    assert list(classes) == [
        'barbarian', 'bard', 'cleric', 'druid', 'fighter', 'monk',
        'paladin', 'ranger', 'rogue', 'sorcerer', 'warlock', 'wizard',
    ]
    for entry in classes.values():
        _assert_contract_payload('CreationClassOption', entry)
        for feature in entry['levelOneFeatures']:
            _assert_contract_payload('CreationClassFeature', feature)
        for feature in entry['laterFeatures']:
            _assert_contract_payload('CreationLaterClassFeature', feature)
    fighter, rogue, wizard = classes['fighter'], classes['rogue'], classes['wizard']
    assert (fighter['hitDie'], fighter['savingThrows'], fighter['skillChoices']['count']) == (10, ['strength', 'constitution'], 2)
    assert fighter['fightingStyles'] == {'level': 1, 'options': ['defense', 'dueling', 'great_weapon', 'archery']}
    assert classes['paladin']['fightingStyles']['level'] == 2
    assert (rogue['skillChoices']['count'], rogue['expertise']) == (4, {'count': 2})
    assert 'expertise' not in fighter and 'fightingStyles' not in wizard
    assert [feature['name'] for feature in fighter['levelOneFeatures']] == ['Second Wind', 'Fighting Style']
    assert {'level': 5, 'name': 'Extra Attack', 'summary': 'Two attacks with every Attack action.'} in fighter['laterFeatures']
    assert 'Magic Missile' in wizard['spells'] and 'Fire Bolt' in wizard['cantrips']
    assert fighter['startingKit'][:3] == ['Longsword', 'Shield', 'Chain Mail']
    backgrounds = {entry['id']: entry for entry in payload['backgrounds']}
    for entry in backgrounds.values():
        _assert_contract_payload('CreationBackgroundOption', entry)
    assert backgrounds['soldier']['skills'] == ['athletics', 'intimidation']
