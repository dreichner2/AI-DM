from __future__ import annotations

from aidm_server.database import db
from aidm_server.models import DmTurn, Session, SessionState, safe_json_dumps
from tests.helpers import seed_world_campaign_player_session


def test_session_recap_get_returns_stored_snapshot_recap(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        session.state_snapshot = safe_json_dumps({'recap': 'The party recovered the moon key.'}, {})
        db.session.add(
            SessionState(
                session_id=ids['session_id'],
                rolling_summary='Older rolling summary.',
                current_location='Moon Gate',
                current_quest='Open the vault',
            )
        )
        db.session.commit()

    response = client.get(f"/api/sessions/{ids['session_id']}/recap")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['session_id'] == ids['session_id']
    assert payload['campaign_id'] == ids['campaign_id']
    assert payload['recap'] == 'The party recovered the moon key.'
    assert payload['source'] == 'state_snapshot'
    assert payload['generated'] is False
    assert payload['state']['current_location'] == 'Moon Gate'


def test_session_recap_get_falls_back_to_recent_turns_without_llm(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        db.session.add(
            DmTurn(
                session_id=ids['session_id'],
                campaign_id=ids['campaign_id'],
                player_id=ids['player_id'],
                player_input='I inspect the rain-slick sigil.',
                dm_output='The sigil glows blue and points toward the old bridge.',
                status='completed',
                outcome_status='resolved',
            )
        )
        db.session.commit()

    response = client.get(f"/api/sessions/{ids['session_id']}/recap")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['source'] == 'recent_turns'
    assert payload['generated'] is False
    assert 'I inspect the rain-slick sigil.' in payload['recap']
    assert 'The sigil glows blue' in payload['recap']


def test_campaign_pack_commentary_reports_route_branches_and_undiscovered_records(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        session.state_snapshot = safe_json_dumps(_branching_pack_snapshot(ids), {})
        db.session.commit()

    response = client.get(f"/api/sessions/{ids['session_id']}/campaign-pack/commentary")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['enabled'] is True
    assert payload['pack']['packId'] == 'branching_pack'
    assert [item['checkpointId'] for item in payload['routeTaken']] == ['cp_gate', 'cp_old_road']
    assert payload['routeTaken'][0]['status'] == 'completed'
    assert payload['routeTaken'][1]['status'] == 'active'

    roads_not_taken = {item['checkpointId']: item for item in payload['roadsNotTaken']}
    assert roads_not_taken['cp_watchtower']['edgeType'] == 'alternate'
    assert roads_not_taken['cp_watchtower']['fromCheckpointId'] == 'cp_gate'

    undiscovered_locations = {item['id'] for item in payload['undiscoveredRecords']['locations']}
    undiscovered_npcs = {item['id'] for item in payload['undiscoveredRecords']['npcs']}
    undiscovered_clues = {item['id'] for item in payload['undiscoveredRecords']['clues']}
    undiscovered_enemies = {item['id'] for item in payload['undiscoveredRecords']['enemies']}
    assert {'loc_watchtower', 'loc_final_bridge'}.issubset(undiscovered_locations)
    assert 'npc_watch_captain' in undiscovered_npcs
    assert 'clue_road_runes' not in undiscovered_clues
    assert 'clue_watchtower_signal' in undiscovered_clues
    assert 'enemy_gate_thief' not in undiscovered_enemies
    assert 'enemy_watch_guard' in undiscovered_enemies
    assert payload['summary']['roadsNotTakenCount'] == 1
    assert any('Roads not taken' in note for note in payload['commentary'])


def test_campaign_pack_commentary_requires_pack_state(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.get(f"/api/sessions/{ids['session_id']}/campaign-pack/commentary")

    assert response.status_code == 404
    assert response.get_json()['error_code'] == 'campaign_pack_not_found'


def _branching_pack_snapshot(ids: dict[str, int]) -> dict:
    checkpoints = [
        {
            'id': 'cp_gate',
            'title': 'Rain Gate',
            'locationIds': ['loc_gate'],
            'npcIds': ['npc_gate_scout'],
            'questIds': ['quest_main'],
            'encounterIds': ['enc_gate_theft'],
            'nextCheckpointIds': ['cp_old_road'],
            'alternateCheckpointIds': ['cp_watchtower'],
        },
        {
            'id': 'cp_old_road',
            'title': 'Old Road',
            'locationIds': ['loc_old_road'],
            'npcIds': ['npc_road_warden'],
            'questIds': ['quest_main'],
            'clueIds': ['clue_road_runes'],
            'nextCheckpointIds': ['cp_final_bridge'],
        },
        {
            'id': 'cp_watchtower',
            'title': 'Abandoned Watchtower',
            'locationIds': ['loc_watchtower'],
            'npcIds': ['npc_watch_captain'],
            'clueIds': ['clue_watchtower_signal'],
            'encounterIds': ['enc_watch_guard'],
            'nextCheckpointIds': ['cp_final_bridge'],
        },
        {
            'id': 'cp_final_bridge',
            'title': 'Final Bridge',
            'locationIds': ['loc_final_bridge'],
            'terminal': True,
        },
    ]
    catalog = {
        'locations': [
            {'id': 'loc_gate', 'name': 'Rain Gate', 'visibleAtStart': True},
            {'id': 'loc_old_road', 'name': 'Old Road'},
            {'id': 'loc_watchtower', 'name': 'Abandoned Watchtower', 'hiddenToPlayers': True},
            {'id': 'loc_final_bridge', 'name': 'Final Bridge', 'hiddenToPlayers': True},
        ],
        'npcs': [
            {'id': 'npc_gate_scout', 'name': 'Gate Scout', 'visibleAtStart': True},
            {'id': 'npc_road_warden', 'name': 'Road Warden'},
            {'id': 'npc_watch_captain', 'name': 'Watch Captain', 'hiddenToPlayers': True},
        ],
        'quests': [{'id': 'quest_main', 'title': 'Find the Bridge', 'visibleAtStart': True}],
        'clues': [
            {'id': 'clue_road_runes', 'name': 'Road Runes', 'checkpointIds': ['cp_old_road']},
            {'id': 'clue_watchtower_signal', 'name': 'Watchtower Signal', 'checkpointIds': ['cp_watchtower']},
        ],
        'encounters': [
            {
                'id': 'enc_gate_theft',
                'title': 'Gate Theft',
                'checkpointIds': ['cp_gate'],
                'enemyIds': ['enemy_gate_thief'],
            },
            {
                'id': 'enc_watch_guard',
                'title': 'Watch Guard',
                'checkpointIds': ['cp_watchtower'],
                'enemyIds': ['enemy_watch_guard'],
            },
        ],
        'enemies': [
            {'id': 'enemy_gate_thief', 'name': 'Gate Thief'},
            {'id': 'enemy_watch_guard', 'name': 'Watch Guard'},
        ],
    }
    return {
        'schemaVersion': 1,
        'sessionId': ids['session_id'],
        'campaignId': ids['campaign_id'],
        'currentScene': {
            'locationId': 'loc_old_road',
            'name': 'Old Road',
            'activeNpcIds': ['npc_road_warden'],
            'activeQuestIds': ['quest_main'],
        },
        'locations': [
            {'id': 'loc_gate', 'name': 'Rain Gate'},
            {'id': 'loc_old_road', 'name': 'Old Road'},
        ],
        'knownNpcs': [
            {'id': 'npc_gate_scout', 'name': 'Gate Scout'},
            {'id': 'npc_road_warden', 'name': 'Road Warden'},
        ],
        'quests': [{'id': 'quest_main', 'title': 'Find the Bridge', 'status': 'active'}],
        'flags': {
            'campaignPackActiveCheckpointId': 'cp_old_road',
            'campaignPackCompletedCheckpointIds': ['cp_gate'],
            'campaignPackSkippedCheckpointIds': [],
            'campaignPackFailedCheckpointIds': [],
            'campaignPackCompletedEncounterIds': ['enc_gate_theft'],
            'campaignPackProgressRevision': 2,
        },
        'campaignPack': {
            'packId': 'branching_pack',
            'title': 'The Branching Pack',
            'schemaVersion': '1',
            'version': '1.0.0',
            'activeCheckpointId': 'cp_old_road',
            'completedCheckpointIds': ['cp_gate'],
            'skippedCheckpointIds': [],
            'failedCheckpointIds': [],
            'progressRevision': 2,
            'checkpoints': checkpoints,
            'catalog': catalog,
        },
    }
