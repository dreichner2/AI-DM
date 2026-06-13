from __future__ import annotations

import json

from aidm_server.database import db
from aidm_server.models import (
    Campaign,
    CampaignSegment,
    BestiaryEntry,
    CanonJob,
    DmCoherenceFeedback,
    DmTurn,
    Map,
    Player,
    PlayerAction,
    Session,
    SessionLogEntry,
    SessionState,
    StoryEntity,
    StoryFact,
    StoryThread,
    TurnCanonUpdate,
    TurnEvent,
    World,
)
from tests.helpers import seed_world_campaign_player_session


def test_create_campaign_accepts_numeric_world_id_string(client, app):
    with app.app_context():
        world = World(name='Campaign World', description='For campaign creation')
        db.session.add(world)
        db.session.commit()
        world_id = world.world_id

    response = client.post(
        '/api/campaigns',
        json={
            'title': '  Gate of Ash  ',
            'world_id': str(world_id),
            'description': 'A new table.',
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    campaign_id = payload['campaign_id']
    assert payload['world_name'] == 'Campaign World'
    assert payload['bestiary_seeded_count'] == 8

    with app.app_context():
        campaign = db.session.get(Campaign, campaign_id)
        assert campaign is not None
        assert campaign.title == 'Gate of Ash'
        assert campaign.world_id == world_id
        assert campaign.status == 'active'
        assert BestiaryEntry.query.filter_by(campaign_id=campaign_id, source='campaign_pack').count() == 8


def test_create_campaign_can_opt_out_of_bestiary_seed(client, app):
    with app.app_context():
        world = World(name='Empty Campaign World', description='For opt out')
        db.session.add(world)
        db.session.commit()
        world_id = world.world_id

    response = client.post(
        '/api/campaigns',
        json={
            'title': 'Quiet Table',
            'world_id': world_id,
            'seed_bestiary': False,
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload['bestiary_seeded_count'] == 0
    with app.app_context():
        assert BestiaryEntry.query.filter_by(campaign_id=payload['campaign_id']).count() == 0


def test_import_campaign_pack_seeds_structured_campaign_content(client, app):
    with app.app_context():
        world = World(name='Bleakmoor', description='A marshland test world')
        db.session.add(world)
        db.session.commit()
        world_id = world.world_id

    response = client.post(
        '/api/campaigns/import-pack',
        json={
            'world_id': world_id,
            'pack': {
                'packId': 'bleakmoor_intro',
                'title': 'The Lanterns of Bleakmoor',
                'version': '1.0.0',
                'description': 'A short authored marsh adventure.',
                'startingState': {
                    'locationId': 'bleakmoor_gate',
                    'questId': 'q_missing_caravan',
                    'currentScene': {
                        'mood': 'rain-darkened',
                        'description': 'The gatehouse lanterns hiss in the rain.',
                        'activeNpcIds': ['npc_captain_veyra'],
                    },
                },
                'locations': [
                    {
                        'id': 'bleakmoor_gate',
                        'name': 'Bleakmoor Gate',
                        'type': 'town',
                        'description': 'A rain-darkened gatehouse on the edge of the marsh.',
                    },
                    {
                        'id': 'old_road',
                        'name': 'Old Road',
                        'type': 'road',
                        'description': 'A drowned road that should remain hidden until discovered.',
                    }
                ],
                'npcs': [
                    {
                        'id': 'npc_captain_veyra',
                        'name': 'Captain Veyra',
                        'role': 'Gate captain',
                        'disposition': 'suspicious',
                        'locationId': 'bleakmoor_gate',
                        'questIds': ['q_missing_caravan'],
                    },
                    {
                        'id': 'npc_lantern_keeper',
                        'name': 'Lantern Keeper',
                        'role': 'Hidden witness',
                        'locationId': 'old_road',
                        'questIds': ['q_old_road_witness'],
                    }
                ],
                'quests': [
                    {
                        'id': 'q_missing_caravan',
                        'title': 'Find the Missing Caravan',
                        'status': 'active',
                        'stage': 'Ask at Bleakmoor Gate',
                        'summary': 'A supply caravan vanished near the old road.',
                        'objectives': [
                            {
                                'id': 'obj_question_veyra',
                                'description': 'Question Captain Veyra.',
                                'status': 'open',
                            }
                        ],
                    },
                    {
                        'id': 'q_old_road_witness',
                        'title': 'Find the Old Road Witness',
                        'status': 'available',
                        'summary': 'A later quest that should not appear before discovery.',
                    }
                ],
                'enemies': [
                    {
                        'id': 'lantern_wraith',
                        'name': 'Lantern Wraith',
                        'creatureType': 'undead',
                        'challengeTier': 'hard',
                        'tags': ['wraith', 'lantern'],
                    }
                ],
                'segments': [
                    {
                        'id': 'seg_question_veyra',
                        'title': 'Question Captain Veyra',
                        'description': 'Veyra reveals the caravan was last seen near the old road.',
                        'trigger': {
                            'type': 'state',
                            'location_contains': 'bleakmoor',
                            'quest_contains': 'missing caravan',
                        },
                        'tags': ['gate', 'mainline'],
                    }
                ],
                'checkpoints': [
                    {
                        'id': 'cp_old_road',
                        'title': 'Find the old road',
                        'nextCheckpointIds': ['cp_watchtower'],
                    },
                    {
                        'id': 'cp_watchtower',
                        'title': 'Reach the watchtower',
                    }
                ],
                'directorRules': {
                    'mainQuestGeneration': 'pack_only',
                    'sideQuestGeneration': 'allowed_tagged',
                },
            },
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload['imported'] is True
    assert payload['pack_id'] == 'bleakmoor_intro'
    assert payload['counts'] == {
        'locations': 2,
        'npcs': 2,
        'quests': 2,
        'segments': 1,
        'checkpoints': 2,
        'encounters': 0,
        'enemies': 1,
        'bestiary_entries': 1,
    }

    with app.app_context():
        campaign = db.session.get(Campaign, payload['campaign_id'])
        session_obj = db.session.get(Session, payload['session_id'])
        session_state = SessionState.query.filter_by(session_id=session_obj.session_id).one()
        segment = CampaignSegment.query.filter_by(campaign_id=campaign.campaign_id).one()
        bestiary = BestiaryEntry.query.filter_by(campaign_id=campaign.campaign_id).one()

        assert campaign.title == 'The Lanterns of Bleakmoor'
        assert campaign.current_quest == 'Find the Missing Caravan - Ask at Bleakmoor Gate'
        assert campaign.location == 'Bleakmoor Gate'
        assert session_state.current_location == 'Bleakmoor Gate'
        assert session_state.current_quest == 'Find the Missing Caravan - Ask at Bleakmoor Gate'

        snapshot = json.loads(session_obj.state_snapshot)
        assert snapshot['campaignPack']['packId'] == 'bleakmoor_intro'
        assert snapshot['campaignPack']['schemaVersion'] == '1'
        assert snapshot['campaignPack']['directorRules']['mainQuestGeneration'] == 'pack_only'
        assert snapshot['currentScene']['locationId'] == 'bleakmoor_gate'
        assert snapshot['currentScene']['name'] == 'Bleakmoor Gate'
        assert snapshot['currentScene']['activeNpcIds'] == ['npc_captain_veyra']
        assert snapshot['currentScene']['activeQuestIds'] == ['q_missing_caravan']
        assert [location['id'] for location in snapshot['locations']] == ['bleakmoor_gate']
        assert [npc['id'] for npc in snapshot['knownNpcs']] == ['npc_captain_veyra']
        assert [quest['id'] for quest in snapshot['quests']] == ['q_missing_caravan']
        assert snapshot['locations'][0]['source'] == 'campaign_pack'
        assert snapshot['knownNpcs'][0]['packId'] == 'bleakmoor_intro'
        assert snapshot['quests'][0]['source'] == 'campaign_pack'
        assert [location['id'] for location in snapshot['campaignPack']['catalog']['locations']] == ['bleakmoor_gate', 'old_road']
        assert [npc['id'] for npc in snapshot['campaignPack']['catalog']['npcs']] == [
            'npc_captain_veyra',
            'npc_lantern_keeper',
        ]
        assert [quest['id'] for quest in snapshot['campaignPack']['catalog']['quests']] == [
            'q_missing_caravan',
            'q_old_road_witness',
        ]

        trigger = json.loads(segment.trigger_condition)
        assert segment.title == 'Question Captain Veyra'
        assert trigger['type'] == 'state'
        assert trigger['packId'] == 'bleakmoor_intro'
        assert 'campaign_pack' in segment.tags
        assert 'pack:bleakmoor_intro' in segment.tags

        assert bestiary.source == 'campaign_pack'
        assert bestiary.persistence == 'campaign'
        assert 'pack:bleakmoor_intro' in json.loads(bestiary.tags_json)


def test_import_campaign_pack_can_create_world_from_manifest(client, app):
    response = client.post(
        '/api/campaigns/import-pack',
        json={
            'packId': 'self_contained_pack',
            'title': 'Self Contained Pack',
            'world': {
                'name': 'Pack World',
                'description': 'Created during pack import.',
            },
            'locations': [{'id': 'start', 'name': 'Start'}],
            'startingState': {'locationId': 'start'},
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    with app.app_context():
        campaign = db.session.get(Campaign, payload['campaign_id'])
        world = db.session.get(World, campaign.world_id)
        assert world.name == 'Pack World'
        assert campaign.location == 'Start'


def test_import_campaign_pack_dry_run_previews_without_creating_records(client, app):
    response = client.post(
        '/api/campaigns/import-pack?dry_run=true',
        json={
            'schemaVersion': '1.0.0',
            'packId': 'dry_run_pack',
            'title': 'Dry Run Pack',
            'world': {'name': 'Dry Run World'},
            'startingState': {'locationId': 'start', 'questId': 'q_start'},
            'locations': [{'id': 'start', 'name': 'Start', 'visibleAtStart': True}],
            'quests': [{'id': 'q_start', 'title': 'Begin', 'status': 'active', 'visibleAtStart': True}],
            'npcs': [{'id': 'npc_guide', 'name': 'Guide', 'visibleAtStart': True}],
            'enemies': [{'id': 'shade', 'name': 'Shade'}],
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['dry_run'] is True
    assert payload['imported'] is False
    assert payload['schema_version'] == '1'
    assert payload['preview']['world']['mode'] == 'create'
    assert payload['preview']['world']['name'] == 'Dry Run World'
    assert payload['preview']['starting_location'] == 'Start'
    assert payload['counts']['enemies'] == 1
    assert payload['counts']['bestiary_entries'] == 1

    with app.app_context():
        assert Campaign.query.filter_by(title='Dry Run Pack').count() == 0
        assert World.query.filter_by(name='Dry Run World').count() == 0
        assert BestiaryEntry.query.filter_by(creature_id='shade').count() == 0


def test_import_campaign_pack_rejects_unsupported_schema_version(client):
    response = client.post(
        '/api/campaigns/import-pack',
        json={
            'schemaVersion': '99',
            'packId': 'future_pack',
            'title': 'Future Pack',
        },
    )

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'unsupported_schema_version'


def test_import_campaign_pack_rejects_unknown_checkpoint_edge(client):
    response = client.post(
        '/api/campaigns/import-pack',
        json={
            'packId': 'broken_graph_pack',
            'title': 'Broken Graph Pack',
            'checkpoints': [
                {
                    'id': 'cp_start',
                    'title': 'Start',
                    'nextCheckpointIds': ['cp_missing'],
                }
            ],
        },
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['error_code'] == 'invalid_pack_reference'
    assert 'unknown checkpoint "cp_missing"' in payload['error']


def test_import_campaign_pack_rejects_unknown_branch_checkpoint_reference(client):
    response = client.post(
        '/api/campaigns/import-pack',
        json={
            'packId': 'broken_branch_pack',
            'title': 'Broken Branch Pack',
            'checkpoints': [
                {
                    'id': 'cp_start',
                    'title': 'Start',
                    'failureCheckpointIds': ['cp_missing_fallback'],
                }
            ],
        },
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['error_code'] == 'invalid_pack_reference'
    assert 'unknown checkpoint "cp_missing_fallback"' in payload['error']


def test_import_campaign_pack_rejects_checkpoint_cycles(client):
    response = client.post(
        '/api/campaigns/import-pack',
        json={
            'packId': 'cycle_pack',
            'title': 'Cycle Pack',
            'checkpoints': [
                {
                    'id': 'cp_one',
                    'title': 'One',
                    'nextCheckpointIds': ['cp_two'],
                },
                {
                    'id': 'cp_two',
                    'title': 'Two',
                    'nextCheckpointIds': ['cp_one'],
                },
            ],
        },
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['error_code'] == 'invalid_checkpoint_graph'
    assert 'nextCheckpointIds cycle' in payload['error']


def test_import_campaign_pack_validates_required_pack_fields(client, app):
    response = client.post('/api/campaigns/import-pack', json={'pack': {'title': 'No Pack ID'}})

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'
    assert response.get_json()['error'] == 'packId is required.'


def test_import_campaign_pack_rejects_unknown_starting_location(client, app):
    with app.app_context():
        world = World(name='Pack Validation World', description='For pack validation')
        db.session.add(world)
        db.session.commit()
        world_id = world.world_id

    response = client.post(
        '/api/campaigns/import-pack',
        json={
            'world_id': world_id,
            'pack': {
                'packId': 'bad_start_location',
                'title': 'Bad Start Location',
                'startingState': {'locationId': 'missing_gate'},
                'locations': [{'id': 'real_gate', 'name': 'Real Gate'}],
            },
        },
    )

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'
    assert response.get_json()['error'] == 'startingState.locationId must reference an imported location.'


def test_create_campaign_rejects_invalid_world_id(client):
    response = client.post('/api/campaigns', json={'title': 'Broken', 'world_id': 'nope'})

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'


def test_create_campaign_rejects_non_string_text_fields(client, app):
    with app.app_context():
        world = World(name='Text Validation World', description='For validation')
        db.session.add(world)
        db.session.commit()
        world_id = world.world_id

    response = client.post('/api/campaigns', json={'title': 123, 'world_id': world_id})

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'


def test_create_campaign_requires_json_body(client):
    response = client.post('/api/campaigns', data='not-json', content_type='text/plain')

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'


def test_create_campaign_rejects_overlong_text_fields(client, app):
    with app.app_context():
        world = World(name='Validation World', description='For validation')
        db.session.add(world)
        db.session.commit()
        world_id = world.world_id

    title_response = client.post(
        '/api/campaigns',
        json={'title': 'x' * 121, 'world_id': world_id},
    )
    assert title_response.status_code == 400
    assert title_response.get_json()['error_code'] == 'validation_error'

    description_response = client.post(
        '/api/campaigns',
        json={'title': 'Valid Title', 'world_id': world_id, 'description': 'x' * 2001},
    )
    assert description_response.status_code == 400
    assert description_response.get_json()['error_code'] == 'validation_error'


def test_list_campaigns_returns_compact_session_summary(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I inspect the gate.',
            dm_output='The gate hums.',
            status='completed',
            outcome_status='resolved',
        )
        db.session.add(turn)
        db.session.add(SessionLogEntry(session_id=ids['session_id'], message='The gate hums.', entry_type='dm'))
        db.session.commit()

    response = client.get('/api/campaigns')

    assert response.status_code == 200
    payload = response.get_json()
    campaign_payload = next(item for item in payload if item['campaign_id'] == ids['campaign_id'])
    assert campaign_payload['session_count'] == 1
    assert campaign_payload['latest_session_id'] == ids['session_id']
    assert campaign_payload['latest_activity_at']
    assert campaign_payload['updated_at']
    assert campaign_payload['status'] == 'active'
    assert campaign_payload['is_archived'] is False
    assert campaign_payload['world_name'] == 'Test World'


def test_update_campaign_validates_and_persists_metadata(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.patch(
        f"/api/campaigns/{ids['campaign_id']}",
        json={
            'title': '  Smoke Over Ember  ',
            'description': 'A cleaner campaign description.',
            'current_quest': 'Find the red bell',
            'location': 'Ash Market',
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['title'] == 'Smoke Over Ember'
    assert payload['description'] == 'A cleaner campaign description.'
    assert payload['current_quest'] == 'Find the red bell'
    assert payload['location'] == 'Ash Market'
    assert payload['updated_at']

    empty_title_response = client.patch(f"/api/campaigns/{ids['campaign_id']}", json={'title': '  '})
    assert empty_title_response.status_code == 400
    assert empty_title_response.get_json()['error_code'] == 'validation_error'


def test_update_campaign_rejects_stale_expected_updated_at(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.patch(
        f"/api/campaigns/{ids['campaign_id']}",
        json={'title': 'Stale Title', 'expected_updated_at': '1999-01-01T00:00:00'},
    )

    assert response.status_code == 409
    assert response.get_json()['error_code'] == 'stale_update'


def test_campaign_archive_delete_and_restore_hide_from_default_lists(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        manually_archived = Session(
            campaign_id=ids['campaign_id'],
            name='Already Archived',
            status='archived',
        )
        db.session.add(manually_archived)
        db.session.commit()
        manually_archived_id = manually_archived.session_id

    archive_response = client.delete(f"/api/campaigns/{ids['campaign_id']}")
    assert archive_response.status_code == 200
    archive_payload = archive_response.get_json()
    assert archive_payload['archived'] is True
    assert archive_payload['campaign']['status'] == 'archived'

    list_response = client.get('/api/campaigns')
    assert list_response.status_code == 200
    assert all(item['campaign_id'] != ids['campaign_id'] for item in list_response.get_json())

    workspace_response = client.get(f"/api/campaigns/{ids['campaign_id']}/workspace")
    assert workspace_response.status_code == 404
    assert workspace_response.get_json()['error_code'] == 'campaign_not_found'

    archived_workspace_response = client.get(f"/api/campaigns/{ids['campaign_id']}/workspace?include_archived=true")
    assert archived_workspace_response.status_code == 200
    assert archived_workspace_response.get_json()['campaign']['is_archived'] is True

    archived_list_response = client.get('/api/campaigns?include_archived=true')
    assert archived_list_response.status_code == 200
    archived = next(item for item in archived_list_response.get_json() if item['campaign_id'] == ids['campaign_id'])
    assert archived['is_archived'] is True

    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        assert session is not None
        assert session.status == 'archived'
        assert session.archived_by_campaign_id == ids['campaign_id']

    restore_response = client.post(f"/api/campaigns/{ids['campaign_id']}/restore")
    assert restore_response.status_code == 200
    assert restore_response.get_json()['campaign']['status'] == 'active'

    with app.app_context():
        restored_session = db.session.get(Session, ids['session_id'])
        manually_archived = db.session.get(Session, manually_archived_id)
        assert restored_session is not None
        assert restored_session.status == 'active'
        assert restored_session.archived_by_campaign_id is None
        assert manually_archived is not None
        assert manually_archived.status == 'archived'
        assert manually_archived.archived_by_campaign_id is None


def test_campaign_hard_delete_rejects_campaigns_with_sessions(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.delete(f"/api/campaigns/{ids['campaign_id']}?hard=true")

    assert response.status_code == 409
    assert response.get_json()['error_code'] == 'campaign_has_sessions'


def test_campaign_force_hard_delete_removes_campaign_workspace(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I inspect the doomed gate.',
            dm_output='The gate is ready to be deleted.',
            status='completed',
            outcome_status='resolved',
        )
        db.session.add(turn)
        db.session.flush()
        db.session.add_all(
            [
                TurnCanonUpdate(turn_id=turn.turn_id, campaign_id=ids['campaign_id']),
                CanonJob(
                    turn_id=turn.turn_id,
                    campaign_id=ids['campaign_id'],
                    session_id=ids['session_id'],
                ),
                TurnEvent(
                    session_id=ids['session_id'],
                    campaign_id=ids['campaign_id'],
                    turn_id=turn.turn_id,
                    player_id=ids['player_id'],
                    event_type='delete_test',
                    payload_json='{}',
                ),
                SessionLogEntry(session_id=ids['session_id'], message='delete log', entry_type='dm'),
                SessionState(session_id=ids['session_id'], rolling_summary='delete summary'),
                PlayerAction(
                    player_id=ids['player_id'],
                    session_id=ids['session_id'],
                    action_text='I inspect the doomed gate.',
                ),
                DmCoherenceFeedback(
                    session_id=ids['session_id'],
                    turn_id=turn.turn_id,
                    coherence_score=5,
                ),
            ]
        )
        db.session.add(Map(world_id=ids['world_id'], campaign_id=ids['campaign_id'], title='Delete Map'))
        db.session.add(CampaignSegment(campaign_id=ids['campaign_id'], title='Delete Segment'))
        db.session.add(StoryEntity(campaign_id=ids['campaign_id'], entity_type='npc', name='Delete NPC'))
        db.session.flush()
        entity = StoryEntity.query.filter_by(campaign_id=ids['campaign_id']).first()
        assert entity is not None
        db.session.add(StoryFact(campaign_id=ids['campaign_id'], subject_entity_id=entity.entity_id, predicate='knows'))
        db.session.add(StoryThread(campaign_id=ids['campaign_id'], title='Delete Thread'))
        db.session.commit()

    response = client.delete(f"/api/campaigns/{ids['campaign_id']}?hard=true&force=true")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['deleted'] is True
    assert payload['hard_deleted'] is True
    assert payload['deleted_session_ids'] == [ids['session_id']]

    assert client.get(f"/api/campaigns/{ids['campaign_id']}").status_code == 404
    with app.app_context():
        assert db.session.get(Campaign, ids['campaign_id']) is None
        assert db.session.get(Session, ids['session_id']) is None
        player = db.session.get(Player, ids['player_id'])
        assert player is not None
        assert player.campaign_id is None
        assert player.workspace_id == 'owner'
        assert Map.query.filter_by(campaign_id=ids['campaign_id']).count() == 0
        assert CampaignSegment.query.filter_by(campaign_id=ids['campaign_id']).count() == 0
        assert StoryFact.query.filter_by(campaign_id=ids['campaign_id']).count() == 0
        assert StoryEntity.query.filter_by(campaign_id=ids['campaign_id']).count() == 0
        assert DmTurn.query.filter_by(campaign_id=ids['campaign_id']).count() == 0
        assert TurnCanonUpdate.query.filter_by(campaign_id=ids['campaign_id']).count() == 0
        assert CanonJob.query.filter_by(campaign_id=ids['campaign_id']).count() == 0
        assert TurnEvent.query.filter_by(campaign_id=ids['campaign_id']).count() == 0
        assert SessionLogEntry.query.filter_by(session_id=ids['session_id']).count() == 0
        assert SessionState.query.filter_by(session_id=ids['session_id']).count() == 0
        assert PlayerAction.query.filter_by(session_id=ids['session_id']).count() == 0
        assert DmCoherenceFeedback.query.filter_by(session_id=ids['session_id']).count() == 0


def test_campaign_workspace_endpoint_returns_aggregate_payload(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        map_obj = Map(
            world_id=ids['world_id'],
            campaign_id=ids['campaign_id'],
            title='Ash Gate',
            description='A gate under black rain.',
            map_data=json.dumps({'tiles': []}),
        )
        segment = CampaignSegment(
            campaign_id=ids['campaign_id'],
            title='Hidden Chamber',
            description='The hidden chamber opens.',
            trigger_condition='sigil solved',
            tags='chamber,secret',
            is_triggered=False,
        )
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I inspect the gate.',
            dm_output='The gate hums.',
            status='completed',
            outcome_status='resolved',
        )
        db.session.add_all([map_obj, segment, turn])
        db.session.flush()
        db.session.add(SessionLogEntry(session_id=ids['session_id'], message='The gate hums.', entry_type='dm'))
        db.session.add(SessionState(session_id=ids['session_id'], rolling_summary='The party found the Ash Gate.'))
        db.session.commit()

    response = client.get(f"/api/campaigns/{ids['campaign_id']}/workspace")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['campaign']['campaign_id'] == ids['campaign_id']
    assert payload['summary']['session_count'] == 1
    assert payload['summary']['player_count'] == 1
    assert payload['summary']['map_count'] == 1
    assert payload['summary']['segment_count'] == 1
    assert payload['summary']['latest_session_id'] == ids['session_id']
    assert payload['has_more'] == {
        'sessions': False,
        'players': False,
        'maps': False,
        'segments': False,
    }
    assert payload['next_cursor'] == {
        'sessions': None,
        'players': None,
        'maps': None,
        'segments': None,
    }
    assert payload['sessions'][0]['turn_count'] == 1
    assert payload['sessions'][0]['latest_summary'] == 'The party found the Ash Gate.'
    assert payload['players'][0]['player_id'] == ids['player_id']
    assert payload['maps'][0]['map_data'] == {'tiles': []}
    assert payload['segments'][0]['title'] == 'Hidden Chamber'


def test_campaign_workspace_summary_counts_full_collection_when_lists_are_limited(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        db.session.add_all(
            [
                Session(campaign_id=ids['campaign_id']),
                Session(campaign_id=ids['campaign_id']),
                Player(
                    campaign_id=ids['campaign_id'],
                    name='Bob',
                    character_name='Mira',
                    race='Human',
                    class_='Fighter',
                    level=1,
                ),
                Map(
                    world_id=ids['world_id'],
                    campaign_id=ids['campaign_id'],
                    title='Second Map',
                    description='Another place.',
                    map_data=json.dumps({}),
                ),
                Map(
                    world_id=ids['world_id'],
                    campaign_id=ids['campaign_id'],
                    title='Third Map',
                    description='Another place.',
                    map_data=json.dumps({}),
                ),
                CampaignSegment(
                    campaign_id=ids['campaign_id'],
                    title='Second Segment',
                    description='Another segment.',
                    trigger_condition='later',
                    tags='later',
                    is_triggered=False,
                ),
                CampaignSegment(
                    campaign_id=ids['campaign_id'],
                    title='Third Segment',
                    description='Another segment.',
                    trigger_condition='later',
                    tags='later',
                    is_triggered=False,
                ),
            ]
        )
        db.session.commit()

    response = client.get(
        f"/api/campaigns/{ids['campaign_id']}/workspace"
        "?session_limit=1&player_limit=1&map_limit=1&segment_limit=1"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['summary']['session_count'] == 3
    assert payload['summary']['player_count'] == 2
    assert payload['summary']['map_count'] == 2
    assert payload['summary']['segment_count'] == 2
    assert len(payload['sessions']) == 1
    assert len(payload['players']) == 1
    assert len(payload['maps']) == 1
    assert len(payload['segments']) == 1
    assert payload['has_more'] == {
        'sessions': True,
        'players': True,
        'maps': True,
        'segments': True,
    }


def test_campaign_canon_endpoint_paginates_collections(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        entities = [
            StoryEntity(
                campaign_id=ids['campaign_id'],
                session_id=ids['session_id'],
                entity_type='npc',
                name=f'Entity {index}',
                canonical_name=f'entity-{index}',
                summary='A test entity.',
            )
            for index in range(3)
        ]
        db.session.add_all(entities)
        db.session.flush()
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I remember.',
            dm_output='A memory forms.',
            status='completed',
            outcome_status='resolved',
        )
        db.session.add(turn)
        db.session.flush()
        facts = [
            StoryFact(
                campaign_id=ids['campaign_id'],
                subject_entity_id=entities[index % len(entities)].entity_id,
                predicate='knows',
                value_text=f'Fact {index}',
                fact_status='accepted',
            )
            for index in range(3)
        ]
        threads = [
            StoryThread(
                campaign_id=ids['campaign_id'],
                title=f'Thread {index}',
                summary='A thread.',
                status='open',
            )
            for index in range(3)
        ]
        updates = [
            TurnCanonUpdate(
                turn_id=turn.turn_id,
                campaign_id=ids['campaign_id'],
                raw_patch_json='{}',
                applied_patch_json='{}',
                status='applied',
            )
            for _index in range(3)
        ]
        db.session.add_all(facts + threads + updates)
        db.session.commit()

    response = client.get(f"/api/campaigns/{ids['campaign_id']}/canon?limit=2")

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload['entities']) == 2
    assert len(payload['facts']) == 2
    assert len(payload['threads']) == 2
    assert len(payload['updates']) == 2
    assert payload['has_more']['entities'] is True
    assert payload['has_more']['facts'] is True
    assert payload['next_cursor']['facts'] is not None
    assert payload['facts'][0]['subject_name'].startswith('Entity')

    next_response = client.get(
        f"/api/campaigns/{ids['campaign_id']}/canon?limit=2"
        f"&fact_before_id={payload['next_cursor']['facts']}"
    )
    assert next_response.status_code == 200
    assert len(next_response.get_json()['facts']) == 1


def test_campaign_canon_endpoint_returns_structured_story_state(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I read the sigil.',
            dm_output='It names the Amber Gate.',
            status='completed',
            outcome_status='resolved',
        )
        db.session.add(turn)
        db.session.flush()
        entity = StoryEntity(
            campaign_id=ids['campaign_id'],
            session_id=ids['session_id'],
            entity_type='location',
            name='Amber Gate',
            canonical_name='The Amber Gate',
            summary='A sealed entrance.',
            aliases_json=json.dumps(['Gate of Amber']),
            metadata_json=json.dumps({'danger': 'high'}),
            first_seen_turn_id=turn.turn_id,
            last_seen_turn_id=turn.turn_id,
        )
        db.session.add(entity)
        db.session.flush()
        fact = StoryFact(
            campaign_id=ids['campaign_id'],
            subject_entity_id=entity.entity_id,
            predicate='is sealed by',
            value_text='a rain-worn sigil',
            value_json=json.dumps({'seal': 'sigil'}),
            confidence=0.88,
            source_turn_id=turn.turn_id,
        )
        thread = StoryThread(
            campaign_id=ids['campaign_id'],
            title='Open the Amber Gate',
            summary='The party needs the seal phrase.',
            origin_turn_id=turn.turn_id,
            last_touched_turn_id=turn.turn_id,
            metadata_json=json.dumps({'priority_reason': 'main path'}),
        )
        update = TurnCanonUpdate(
            turn_id=turn.turn_id,
            campaign_id=ids['campaign_id'],
            raw_patch_json=json.dumps({'entities': ['Amber Gate']}),
            applied_patch_json=json.dumps({'accepted': True}),
            status='applied',
            extractor_model='test-extractor',
        )
        db.session.add_all([fact, thread, update])
        db.session.commit()

    response = client.get(f"/api/campaigns/{ids['campaign_id']}/canon")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['campaign_id'] == ids['campaign_id']
    assert payload['summary'] == {
        'entity_count': 1,
        'fact_count': 1,
        'thread_count': 1,
        'update_count': 1,
        'canon_job_counts': {},
    }
    assert payload['entities'][0]['aliases'] == ['Gate of Amber']
    assert payload['entities'][0]['metadata'] == {'danger': 'high'}
    assert payload['facts'][0]['subject_name'] == 'Amber Gate'
    assert payload['facts'][0]['value_json'] == {'seal': 'sigil'}
    assert payload['threads'][0]['metadata'] == {'priority_reason': 'main path'}
    assert payload['updates'][0]['applied_patch'] == {'accepted': True}


def test_campaign_workspace_and_canon_return_404_for_missing_campaign(client):
    workspace_response = client.get('/api/campaigns/99999/workspace')
    canon_response = client.get('/api/campaigns/99999/canon')

    assert workspace_response.status_code == 404
    assert workspace_response.get_json()['error_code'] == 'campaign_not_found'
    assert canon_response.status_code == 404
    assert canon_response.get_json()['error_code'] == 'campaign_not_found'
