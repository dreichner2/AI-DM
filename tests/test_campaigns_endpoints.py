from __future__ import annotations

import json
import threading

import aidm_server.blueprints.campaigns as campaigns_blueprint
from aidm_server.database import db
from aidm_server.models import (
    Campaign,
    CampaignSegment,
    BestiaryEntry,
    CanonJob,
    CampaignPack,
    CampaignPackCheckpointProgress,
    CampaignPackRecord,
    CampaignPackSession,
    DmCoherenceFeedback,
    DmTurn,
    InstalledCampaignPack,
    Map,
    OperatorActionAudit,
    Player,
    PlayerAction,
    Session,
    SessionLogEntry,
    SessionState,
    SessionTurnLock,
    StoryEntity,
    StoryFact,
    StoryThread,
    TurnCanonUpdate,
    TurnEvent,
    World,
)
from aidm_server.turn_coordinator import session_turn_coordinator
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


def test_example_campaign_pack_library_lists_bundled_pack_summaries(client):
    response = client.get('/api/campaigns/example-packs')

    assert response.status_code == 200
    payload = response.get_json()
    pack_ids = {pack['pack_id'] for pack in payload['packs']}
    assert {
        'middle_earth.shadow_over_the_greenway',
        'middle_earth.shadow_under_eryn_luin',
        'original_fantasy.road_of_unremembered_kings',
    }.issubset(pack_ids)
    assert 'bleakmoor_intro' not in pack_ids
    assert all(pack['source'] == 'bundled_example' for pack in payload['packs'])
    assert all('manifest' not in pack for pack in payload['packs'])
    assert payload['count'] == len(payload['packs'])

    road = next(pack for pack in payload['packs'] if pack['pack_id'] == 'original_fantasy.road_of_unremembered_kings')
    assert road['title'] == 'The Road of Unremembered Kings'
    assert road['source_filename'] == 'the_road_of_unremembered_kings_campaign.json'
    assert road['world_name'] == 'The Western Roadlands'
    assert road['length_estimate']['sessions_min'] == 4

    eryn_luin = next(pack for pack in payload['packs'] if pack['pack_id'] == 'middle_earth.shadow_under_eryn_luin')
    assert eryn_luin['length_estimate']['label'] == 'Medium campaign'
    assert eryn_luin['length_estimate']['sessions_min'] == 4
    assert eryn_luin['length_estimate']['sessions_max'] == 6


def test_import_example_campaign_pack_creates_playable_campaign(client, app):
    response = client.post('/api/campaigns/example-packs/bleakmoor_intro/import', json={})

    assert response.status_code == 201
    payload = response.get_json()
    assert payload['pack_id'] == 'bleakmoor_intro'
    assert payload['campaign_id']
    assert payload['session_id']
    assert payload['installed_campaign_pack']['source_filename'] == 'bleakmoor_intro_campaign_pack.json'

    with app.app_context():
        campaign = db.session.get(Campaign, payload['campaign_id'])
        session = db.session.get(Session, payload['session_id'])
        world = db.session.get(World, campaign.world_id)
        snapshot = json.loads(session.state_snapshot)
        assert campaign.title == 'The Lanterns of Bleakmoor'
        assert world.name == 'Bleakmoor'
        assert snapshot['campaignPack']['packId'] == 'bleakmoor_intro'
        assert CampaignPackSession.query.filter_by(
            campaign_id=payload['campaign_id'],
            session_id=payload['session_id'],
            pack_id='bleakmoor_intro',
        ).one()


def test_import_road_of_unremembered_kings_example_pack_dry_run(client, app):
    response = client.post(
        '/api/campaigns/example-packs/original_fantasy.road_of_unremembered_kings/import?dry_run=true',
        json={},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['dry_run'] is True
    assert payload['pack_id'] == 'original_fantasy_road_of_unremembered_kings'
    assert payload['preview']['title'] == 'The Road of Unremembered Kings'
    assert payload['preview']['starting_location_id'] == 'loc_lantern_post_inn'
    with app.app_context():
        assert Campaign.query.filter_by(title='The Road of Unremembered Kings').count() == 0


def test_import_example_campaign_pack_rejects_malformed_optional_json(client):
    response = client.post(
        '/api/campaigns/example-packs/bleakmoor_intro/import',
        data='{',
        content_type='application/json',
    )

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'


def test_import_example_campaign_pack_can_use_existing_world(client, app):
    with app.app_context():
        world = World(name='Shared Table World', description='Existing setting')
        db.session.add(world)
        db.session.commit()
        world_id = world.world_id

    response = client.post(
        '/api/campaigns/example-packs/bleakmoor_intro/import',
        json={'world_id': world_id},
    )

    assert response.status_code == 201
    with app.app_context():
        campaign = db.session.get(Campaign, response.get_json()['campaign_id'])
        assert campaign.world_id == world_id


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
            'sourceFilename': 'bleakmoor_intro_campaign_pack.json',
            'pack': {
                'packId': 'bleakmoor_intro',
                'title': 'The Lanterns of Bleakmoor',
                'version': '1.0.0',
                'description': 'A short authored marsh adventure.',
                'startingState': {
                    'locationId': 'bleakmoor_gate',
                    'questId': 'q_missing_caravan',
                    'checkpointId': 'cp_watchtower',
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
    assert payload['installed_campaign_pack']['pack_id'] == 'bleakmoor_intro'
    assert payload['installed_campaign_pack']['source_filename'] == 'bleakmoor_intro_campaign_pack.json'
    assert len(payload['installed_campaign_pack']['pack_hash']) == 64
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
        installed_pack = InstalledCampaignPack.query.filter_by(workspace_id='owner', pack_id='bleakmoor_intro').one()
        campaign_pack = CampaignPack.query.filter_by(workspace_id='owner', pack_id='bleakmoor_intro').one()
        pack_session = CampaignPackSession.query.filter_by(session_id=session_obj.session_id).one()
        pack_records = CampaignPackRecord.query.filter_by(campaign_pack_id=campaign_pack.campaign_pack_id).all()
        checkpoint_progress = (
            CampaignPackCheckpointProgress.query.filter_by(campaign_pack_session_id=pack_session.campaign_pack_session_id)
            .order_by(CampaignPackCheckpointProgress.sort_order.asc())
            .all()
        )

        assert campaign.title == 'The Lanterns of Bleakmoor'
        assert installed_pack.title == 'The Lanterns of Bleakmoor'
        assert installed_pack.pack_version == '1.0.0'
        assert installed_pack.schema_version == '1'
        assert installed_pack.source_filename == 'bleakmoor_intro_campaign_pack.json'
        assert json.loads(installed_pack.manifest_json)['packId'] == 'bleakmoor_intro'
        assert campaign_pack.installed_pack_id == installed_pack.installed_pack_id
        assert campaign_pack.pack_hash == installed_pack.pack_hash
        assert {record.record_type for record in pack_records} == {
            'checkpoint',
            'enemy',
            'location',
            'npc',
            'quest',
            'segment',
        }
        assert {record.record_id for record in pack_records if record.record_type == 'location'} == {
            'bleakmoor_gate',
            'old_road',
        }
        assert pack_session.campaign_pack_id == campaign_pack.campaign_pack_id
        assert pack_session.installed_pack_id == installed_pack.installed_pack_id
        assert pack_session.pack_id == 'bleakmoor_intro'
        assert pack_session.active_checkpoint_id == 'cp_watchtower'
        assert pack_session.progress_revision == 0
        assert [(row.checkpoint_id, row.status) for row in checkpoint_progress] == [
            ('cp_old_road', 'open'),
            ('cp_watchtower', 'active'),
        ]
        assert campaign.current_quest == 'Find the Missing Caravan - Ask at Bleakmoor Gate'
        assert campaign.location == 'Bleakmoor Gate'
        assert session_state.current_location == 'Bleakmoor Gate'
        assert session_state.current_quest == 'Find the Missing Caravan - Ask at Bleakmoor Gate'

        snapshot = json.loads(session_obj.state_snapshot)
        assert snapshot['campaignPack']['packId'] == 'bleakmoor_intro'
        assert snapshot['campaignPack']['schemaVersion'] == '1'
        assert snapshot['campaignPack']['activeCheckpointId'] == 'cp_watchtower'
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
        assert segment.external_id == 'seg_question_veyra'
        assert segment.source == 'campaign_pack'
        assert segment.source_pack_id == 'bleakmoor_intro'
        assert json.loads(segment.metadata_json)['packSegmentId'] == 'seg_question_veyra'
        assert trigger['type'] == 'state'
        assert trigger['packId'] == 'bleakmoor_intro'
        assert 'campaign_pack' in segment.tags
        assert 'pack:bleakmoor_intro' in segment.tags

        assert bestiary.source == 'campaign_pack'
        assert bestiary.persistence == 'campaign'
        assert 'pack:bleakmoor_intro' in json.loads(bestiary.tags_json)
        audit = OperatorActionAudit.query.filter_by(action='campaign_pack.import', campaign_id=campaign.campaign_id).one()
        assert audit.resource_id == 'bleakmoor_intro'
        details = json.loads(audit.details_json)
        assert details['packId'] == 'bleakmoor_intro'
        assert details['counts']['bestiary_entries'] == 1


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


def test_import_campaign_pack_reuses_installed_pack_source_by_hash(client, app):
    pack = {
        'packId': 'repeatable_pack',
        'title': 'Repeatable Pack',
        'version': '1.2.3',
        'locations': [{'id': 'start', 'name': 'Start'}],
        'startingState': {'locationId': 'start'},
    }

    first = client.post('/api/campaigns/import-pack', json={'sourceFilename': 'repeatable.json', 'pack': pack})
    second = client.post('/api/campaigns/import-pack', json={'sourceFilename': 'repeatable.json', 'pack': pack})

    assert first.status_code == 201
    assert second.status_code == 201
    first_source = first.get_json()['installed_campaign_pack']
    second_source = second.get_json()['installed_campaign_pack']
    assert second_source['installed_pack_id'] == first_source['installed_pack_id']
    assert second_source['pack_hash'] == first_source['pack_hash']
    with app.app_context():
        assert InstalledCampaignPack.query.filter_by(pack_id='repeatable_pack').count() == 1
        assert CampaignPack.query.filter_by(pack_id='repeatable_pack').count() == 1
        assert CampaignPackSession.query.filter_by(pack_id='repeatable_pack').count() == 2
        assert Campaign.query.filter_by(title='Repeatable Pack').count() == 2


def test_installed_campaign_pack_library_lists_details_and_imports(client, app):
    pack = {
        'packId': 'library_pack',
        'title': 'Library Pack',
        'version': '2.0.0',
        'multiSessionGroupKey': 'shared-west-marches',
        'gmNotes': {'opening': 'Keep the bell secret.'},
        'dependencies': [{'packId': 'shared_rules', 'versionRange': '^1'}],
        'mods': [{'id': 'winter_overlay', 'title': 'Winter Overlay'}],
        'locations': [{'id': 'start', 'name': 'Start'}],
        'startingState': {'locationId': 'start'},
        'checkpoints': [{'id': 'cp_start', 'title': 'Start', 'terminal': True}],
    }

    imported = client.post('/api/campaigns/import-pack', json={'sourceFilename': 'library_pack.json', 'pack': pack})
    assert imported.status_code == 201
    installed_pack_id = imported.get_json()['installed_campaign_pack']['installed_pack_id']

    list_response = client.get('/api/campaigns/installed-packs')
    assert list_response.status_code == 200
    list_payload = list_response.get_json()
    assert list_payload['count'] == 1
    assert list_payload['installed_packs'][0]['pack_id'] == 'library_pack'
    assert list_payload['installed_packs'][0]['dependencies'][0]['packId'] == 'shared_rules'
    assert list_payload['installed_packs'][0]['multi_session_group_key'] == 'shared-west-marches'

    detail_response = client.get(f'/api/campaigns/installed-packs/{installed_pack_id}')
    assert detail_response.status_code == 200
    detail_payload = detail_response.get_json()
    assert detail_payload['manifest']['gmNotes']['opening'] == 'Keep the bell secret.'
    assert detail_payload['record_count'] == 2
    assert {record['record_type'] for record in detail_payload['records']} == {'checkpoint', 'location'}

    reimport_response = client.post(
        f'/api/campaigns/installed-packs/{installed_pack_id}/import',
        json={'sessionName': 'Second Table'},
    )
    assert reimport_response.status_code == 201
    assert reimport_response.get_json()['session']['display_name'] == 'Second Table'
    with app.app_context():
        assert CampaignPackSession.query.filter_by(pack_id='library_pack').count() == 2


def test_campaign_pack_lint_endpoint_returns_authoring_issues(client):
    response = client.post(
        '/api/campaigns/pack-tools/lint',
        json={
            'packId': 'lint_endpoint_pack',
            'title': 'Lint Endpoint Pack',
            'locations': [{'id': 'start', 'name': 'Start'}],
            'startingState': {'locationId': 'start'},
            'checkpoints': [{'id': 'cp_start', 'title': 'Start', 'terminal': True}],
            'handouts': [
                {
                    'id': 'handout_secret',
                    'title': 'Secret Handout',
                    'visibleAtStart': True,
                    'hiddenToPlayers': True,
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['ok'] is False
    assert payload['summary']['packId'] == 'lint_endpoint_pack'
    assert payload['graph']['reachable'] == ['cp_start']
    assert payload['authoring_report']['checkpoints']['reachable'] == 1
    assert payload['authoring_report']['collections'][0]['collection'] == 'locations'
    assert any(issue['code'] == 'hidden_record_visible_at_start' for issue in payload['issues'])


def test_campaign_pack_forge_endpoint_returns_importable_manifest(client):
    response = client.post(
        '/api/campaigns/pack-tools/forge',
        json={
            'title': 'Lanterns Under Blackwater',
            'prompt': 'Harbor intrigue, a drowned archive, and a guide with divided loyalties.',
            'tone': 'noir mystery',
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['ok'] is True
    assert payload['sourceFilename'].endswith('.json')
    assert payload['pack']['packId'].startswith('forge_lanterns_under_blackwater_')
    assert payload['lint']['ok'] is True
    assert payload['lint']['authoring_report']['checkpoints']['reachable'] == 4
    assert payload['lint']['preview']['preview']['starting_location'] == 'The Breakwater Gate'
    assert json.loads(payload['manifestText']) == payload['payload']

    dry_run = client.post('/api/campaigns/import-pack?dry_run=true', json=payload['payload'])

    assert dry_run.status_code == 200
    dry_run_payload = dry_run.get_json()
    assert dry_run_payload['dry_run'] is True
    assert dry_run_payload['pack_id'] == payload['pack']['packId']
    assert dry_run_payload['counts']['checkpoints'] == 4


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


def test_import_campaign_pack_runs_schema_validation_with_pathful_errors(client):
    response = client.post(
        '/api/campaigns/import-pack',
        json={
            'packId': 'bad_schema_pack',
            'title': 'Bad Schema Pack',
            'quests': [
                {
                    'id': 'q_bad',
                    'title': 'Broken Quest',
                    'objectives': [
                        {
                            'id': 'obj_bad',
                            'description': 'This one has a bad status shape.',
                            'status': {'not': 'a string'},
                        }
                    ],
                }
            ],
        },
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['error_code'] == 'invalid_campaign_pack_schema'
    assert payload['error'] == 'quests[0].objectives[0].status must be a string.'


def test_import_campaign_pack_accepts_supported_schema_version_variants(client):
    for schema_version in ['1', '1.0', '1.0.0', 1]:
        response = client.post(
            '/api/campaigns/import-pack?dry_run=true',
            json={
                'schemaVersion': schema_version,
                'packId': f'schema_variant_{str(schema_version).replace(".", "_")}',
                'title': f'Schema Variant {schema_version}',
            },
        )

        assert response.status_code == 200
        assert response.get_json()['schema_version'] == '1'


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
        audits = (
            OperatorActionAudit.query.filter_by(campaign_id=ids['campaign_id'])
            .order_by(OperatorActionAudit.operator_audit_id.asc())
            .all()
        )
        assert [audit.action for audit in audits] == ['campaign.archive', 'campaign.restore']
        assert json.loads(audits[0].details_json)['archivedSessionCount'] == 1
        assert json.loads(audits[1].details_json)['restoredSessionCount'] == 1


def test_campaign_archive_waits_for_every_existing_session_lock(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        second_session = Session(campaign_id=ids['campaign_id'], name='Second Session')
        db.session.add(second_session)
        db.session.commit()
        second_session_id = second_session.session_id

    entered_lifecycle = threading.Event()
    completed = threading.Event()
    result = {}
    original_commit_operation = campaigns_blueprint.commit_campaign_lifecycle_operation

    def tracked_commit_operation(*args, **kwargs):
        entered_lifecycle.set()
        return original_commit_operation(*args, **kwargs)

    monkeypatch.setattr(
        campaigns_blueprint,
        'commit_campaign_lifecycle_operation',
        tracked_commit_operation,
    )

    def archive_request():
        response = app.test_client().post(f"/api/campaigns/{ids['campaign_id']}/archive")
        result['status_code'] = response.status_code
        result['payload'] = response.get_json()
        completed.set()

    with app.app_context():
        app.config['AIDM_TURN_COORDINATOR_STORE'] = 'memory'
        with session_turn_coordinator.serialized(second_session_id):
            thread = threading.Thread(target=archive_request)
            thread.start()
            assert entered_lifecycle.wait(timeout=1.0)
            assert completed.wait(timeout=0.1) is False

        thread.join(timeout=2.0)
        assert not thread.is_alive()

    assert result['status_code'] == 200
    assert result['payload']['campaign']['status'] == 'archived'
    with app.app_context():
        statuses = {
            session.session_id: session.status
            for session in Session.query.filter_by(campaign_id=ids['campaign_id']).all()
        }
        assert statuses == {
            ids['session_id']: 'archived',
            second_session_id: 'archived',
        }


def test_database_coordinator_campaign_archive_and_restore_fence_every_session(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        second_session = Session(campaign_id=ids['campaign_id'], name='Second Session')
        db.session.add(second_session)
        db.session.commit()
        session_ids = [ids['session_id'], second_session.session_id]
        app.config['AIDM_TURN_COORDINATOR_STORE'] = 'database'
        app.config['AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS'] = 30
        app.config['AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS'] = 10

    archive_response = client.post(f"/api/campaigns/{ids['campaign_id']}/archive")

    assert archive_response.status_code == 200
    with app.app_context():
        archived_statuses = {
            session.session_id: session.status
            for session in Session.query.filter(Session.session_id.in_(session_ids)).all()
        }
        first_tokens = {
            lock.session_id: lock.fencing_token
            for lock in SessionTurnLock.query.filter(SessionTurnLock.session_id.in_(session_ids)).all()
        }
        assert archived_statuses == {session_id: 'archived' for session_id in session_ids}
        assert first_tokens == {session_id: 1 for session_id in session_ids}

    restore_response = client.post(f"/api/campaigns/{ids['campaign_id']}/restore")

    assert restore_response.status_code == 200
    with app.app_context():
        restored_statuses = {
            session.session_id: session.status
            for session in Session.query.filter(Session.session_id.in_(session_ids)).all()
        }
        second_tokens = {
            lock.session_id: lock.fencing_token
            for lock in SessionTurnLock.query.filter(SessionTurnLock.session_id.in_(session_ids)).all()
        }
        assert restored_statuses == {session_id: 'active' for session_id in session_ids}
        assert second_tokens == {session_id: 2 for session_id in session_ids}


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
        campaign_audit = OperatorActionAudit.query.filter_by(
            action='campaign.delete_hard',
            resource_id=str(ids['campaign_id']),
        ).one()
        assert campaign_audit.workspace_id == 'owner'
        campaign_audit_details = json.loads(campaign_audit.details_json)
        assert campaign_audit_details['forceDelete'] is True
        assert campaign_audit_details['deletedSessionIds'] == [ids['session_id']]
        session_audit = OperatorActionAudit.query.filter_by(
            action='session.delete_hard',
            resource_id=str(ids['session_id']),
        ).one()
        assert session_audit.workspace_id == 'owner'


def test_database_coordinator_force_delete_cascades_all_campaign_turn_locks(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        second_session = Session(campaign_id=ids['campaign_id'], name='Second Session')
        db.session.add(second_session)
        db.session.commit()
        session_ids = [ids['session_id'], second_session.session_id]
        app.config['AIDM_TURN_COORDINATOR_STORE'] = 'database'
        app.config['AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS'] = 30
        app.config['AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS'] = 10

    response = client.delete(f"/api/campaigns/{ids['campaign_id']}?hard=true&force=true")

    assert response.status_code == 200
    assert response.get_json()['deleted_session_ids'] == session_ids
    with app.app_context():
        assert db.session.get(Campaign, ids['campaign_id']) is None
        assert Session.query.filter(Session.session_id.in_(session_ids)).count() == 0
        assert SessionTurnLock.query.filter(SessionTurnLock.session_id.in_(session_ids)).count() == 0


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
