from __future__ import annotations

import json

import pytest
from sqlalchemy.exc import IntegrityError

import aidm_server.blueprints.sessions as sessions_blueprint
from aidm_server.database import db
from aidm_server.models import (
    Campaign,
    DmTurn,
    OperatorActionAudit,
    Session,
    SessionLogEntry,
    SessionState,
    StoryEntity,
    StoryFact,
    StoryThread,
    TurnEvent,
    TurnCanonUpdate,
    World,
)
from aidm_server.services.campaign_pack_progress import PROGRESS_CHANGED_EVENT
from aidm_server.turn_events import (
    DM_RESPONSE_EVENT,
    PLAYER_MESSAGE_EVENT,
    SESSION_ENDED_EVENT,
    SESSION_RECAP_EVENT,
    SESSION_STARTED_EVENT,
    record_turn_event,
)
from tests.helpers import seed_world_campaign_player_session


def test_session_state_and_log_endpoints(client, app):
    ids = seed_world_campaign_player_session(app)
    scene_snapshot = {
        'currentScene': {'locationId': 'blackwake_tavern', 'name': 'Blackwake Tavern'},
        'locations': [{'id': 'blackwake_tavern', 'name': 'Blackwake Tavern'}],
    }

    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        assert session is not None
        session.state_snapshot = json.dumps(scene_snapshot)
        db.session.commit()

    state_response = client.get(f"/api/sessions/{ids['session_id']}/state")
    assert state_response.status_code == 200
    state_payload = state_response.get_json()
    assert state_payload['session_id'] == ids['session_id']
    assert 'rolling_summary' in state_payload
    assert state_payload['current_location'] == 'Old Ruins'
    assert state_payload['current_quest'] == 'Find the relic'
    assert state_payload['state_snapshot'] == scene_snapshot

    with app.app_context():
        assert SessionState.query.filter_by(session_id=ids['session_id']).first() is None

    log_response = client.get(f"/api/sessions/{ids['session_id']}/log")
    assert log_response.status_code == 200
    log_payload = log_response.get_json()
    assert log_payload['session_id'] == ids['session_id']
    assert isinstance(log_payload['entries'], list)


def test_end_session_recap_persists_snapshot(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        assert session is not None
        session.state_snapshot = json.dumps({'client_session_id': 'legacy-id', 'custom': {'keep': True}})
        db.session.commit()

    response = client.post(f"/api/sessions/{ids['session_id']}/end")
    assert response.status_code == 200
    payload = response.get_json()
    assert 'recap' in payload

    sessions_response = client.get(f"/api/sessions/campaigns/{ids['campaign_id']}/sessions")
    assert sessions_response.status_code == 200
    sessions_payload = sessions_response.get_json()
    assert isinstance(sessions_payload[0]['state_snapshot'], dict)
    assert sessions_payload[0]['state_snapshot']['recap'] == payload['recap']
    assert sessions_payload[0]['state_snapshot']['custom'] == {'keep': True}

    with app.app_context():
        events = (
            TurnEvent.query.filter_by(session_id=ids['session_id'])
            .order_by(TurnEvent.event_id.asc())
            .all()
        )
        assert [event.event_type for event in events] == [SESSION_ENDED_EVENT, SESSION_RECAP_EVENT]
        recap_event = events[-1]
        recap_payload = json.loads(recap_event.payload_json)
        assert recap_payload['recap'] == payload['recap']

        log_entries = (
            SessionLogEntry.query.filter_by(session_id=ids['session_id'])
            .order_by(SessionLogEntry.id.asc())
            .all()
        )
        assert [entry.entry_type for entry in log_entries] == ['system', 'system']
        assert 'Session ended' in log_entries[0].message
        assert 'Session Recap' in log_entries[1].message
        assert payload['recap'] in log_entries[1].message


def test_end_session_recap_uses_bounded_recent_log_and_summary(client, app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    captured = {}

    with app.app_context():
        db.session.add(
            SessionState(
                session_id=ids['session_id'],
                rolling_summary='Campaign summary before the bounded batch.',
            )
        )
        db.session.add_all(
            [
                SessionLogEntry(
                    session_id=ids['session_id'],
                    message=f'ancient-log-{index}',
                    entry_type='dm',
                )
                for index in range(20)
            ]
        )
        db.session.add_all(
            [
                SessionLogEntry(
                    session_id=ids['session_id'],
                    message=f'recent-log-{index}',
                    entry_type='dm',
                )
                for index in range(80)
            ]
        )
        db.session.commit()

    def fake_query_gpt(prompt, system_message=None):
        captured['prompt'] = prompt
        captured['system_message'] = system_message
        return 'bounded recap'

    monkeypatch.setattr(sessions_blueprint, 'query_gpt', fake_query_gpt)

    response = client.post(f"/api/sessions/{ids['session_id']}/end")

    assert response.status_code == 200
    assert response.get_json()['recap'] == 'bounded recap'
    assert captured['system_message'] == 'You are a D&D session summarizer.'
    assert 'Campaign summary before the bounded batch.' in captured['prompt']
    assert 'recent-log-79' in captured['prompt']
    assert 'ancient-log-0' not in captured['prompt']


def test_start_session_adds_welcome_log(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id']})
    assert response.status_code == 201
    session_id = response.get_json()['session_id']

    log_response = client.get(f'/api/sessions/{session_id}/log')
    assert log_response.status_code == 200
    entries = log_response.get_json()['entries']

    assert entries[0]['entry_type'] == 'system'
    assert 'Welcome to the table' in entries[0]['message']

    with app.app_context():
        event = TurnEvent.query.filter_by(session_id=session_id).one()
        assert event.event_type == SESSION_STARTED_EVENT
        payload = json.loads(event.payload_json)
        assert payload['metadata']['kind'] == 'session_welcome'


def test_start_session_requires_json_and_valid_campaign_id(client):
    non_json_response = client.post('/api/sessions/start', data='not-json', content_type='text/plain')
    assert non_json_response.status_code == 400
    assert non_json_response.get_json()['error_code'] == 'validation_error'

    invalid_campaign_response = client.post('/api/sessions/start', json={'campaign_id': 'not-an-id'})
    assert invalid_campaign_response.status_code == 400
    assert invalid_campaign_response.get_json()['error_code'] == 'validation_error'


def test_start_session_reuses_client_session_id(client, app):
    ids = seed_world_campaign_player_session(app)
    payload = {'campaign_id': ids['campaign_id'], 'client_session_id': 'session-start-1'}

    first_response = client.post('/api/sessions/start', json=payload)
    replay_response = client.post('/api/sessions/start', json=payload)

    assert first_response.status_code == 201
    assert replay_response.status_code == 200
    assert replay_response.get_json()['session_id'] == first_response.get_json()['session_id']
    assert replay_response.get_json()['idempotent_replay'] is True

    with app.app_context():
        created_count = Session.query.filter_by(campaign_id=ids['campaign_id']).count()
        assert created_count == 2
        created = Session.query.filter_by(client_session_id='session-start-1').one()
        assert created.session_id == first_response.get_json()['session_id']


def test_session_client_session_id_is_unique_per_campaign(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        db.session.add_all(
            [
                Session(campaign_id=ids['campaign_id'], client_session_id='duplicate-start'),
                Session(campaign_id=ids['campaign_id'], client_session_id='duplicate-start'),
            ]
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_start_session_rejects_invalid_client_session_id(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        '/api/sessions/start',
        json={'campaign_id': ids['campaign_id'], 'client_session_id': 'bad key with spaces'},
    )

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'


def test_start_session_rejects_overlong_client_session_id(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        '/api/sessions/start',
        json={'campaign_id': ids['campaign_id'], 'client_session_id': 'a' * 81},
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['error_code'] == 'validation_error'
    assert '80 characters or fewer' in payload['error']


def test_import_session_from_export_restores_state_events_and_projected_log(client, app):
    ids = seed_world_campaign_player_session(app)
    export_payload = {
        'exportedAt': '2026-06-06T10:30:00+00:00',
        'selectedIds': {
            'campaignId': ids['campaign_id'],
            'sessionId': ids['session_id'],
            'playerId': ids['player_id'],
        },
        'selectedSession': {
            'session_id': ids['session_id'],
            'display_name': 'Ash Gate Alpha',
            'state_snapshot': {'recap': 'Old recap'},
        },
        'sessionState': {
            'current_location': 'Restored Gate',
            'current_quest': 'Open the restored door',
            'rolling_summary': 'The party found the gate in an exported file.',
            'active_segments': [{'title': 'Gate'}],
            'memory_snippets': [{'summary': 'Exported memory'}],
            'updated_at': '2026-06-06T10:31:00+00:00',
        },
        'logEntries': [
            {
                'message': 'This should not be duplicated when events are present.',
                'entry_type': 'system',
                'metadata': {},
                'timestamp': '2026-06-06T10:29:00+00:00',
            }
        ],
        'turnEvents': [
            {
                'event_id': 101,
                'turn_id': 77,
                'player_id': ids['player_id'],
                'event_type': PLAYER_MESSAGE_EVENT,
                'payload': {'speaker': 'Ember', 'message': 'I test the restored gate.'},
                'created_at': '2026-06-06T10:32:00+00:00',
            },
            {
                'event_id': 102,
                'turn_id': 77,
                'player_id': ids['player_id'],
                'event_type': DM_RESPONSE_EVENT,
                'payload': {'message': 'The restored gate opens.'},
                'created_at': '2026-06-06T10:33:00+00:00',
            },
        ],
    }

    response = client.post('/api/sessions/import', json=export_payload)

    assert response.status_code == 201
    payload = response.get_json()
    imported_session_id = payload['session_id']
    assert imported_session_id != ids['session_id']
    assert payload['session']['display_name'] == 'Ash Gate Alpha'
    assert payload['counts'] == {
        'turn_events': 2,
        'projected_log_entries': 2,
        'log_entries': 0,
        'session_state': 1,
    }

    with app.app_context():
        imported_session = db.session.get(Session, imported_session_id)
        assert imported_session is not None
        assert imported_session.campaign_id == ids['campaign_id']
        assert imported_session.name == 'Ash Gate Alpha'
        imported_snapshot = json.loads(imported_session.state_snapshot)
        assert imported_snapshot['recap'] == 'Old recap'
        assert imported_snapshot['imported'] is True
        assert imported_snapshot['source_session_id'] == ids['session_id']
        assert imported_snapshot['sessionId'] == imported_session_id
        assert imported_snapshot['campaignId'] == ids['campaign_id']

        imported_state = SessionState.query.filter_by(session_id=imported_session_id).one()
        assert imported_state.current_location == 'Restored Gate'
        assert imported_state.rolling_summary == 'The party found the gate in an exported file.'
        assert json.loads(imported_state.active_segments) == [{'title': 'Gate'}]
        assert json.loads(imported_state.memory_snippets) == [{'summary': 'Exported memory'}]
        audit = OperatorActionAudit.query.filter_by(action='session.import', session_id=imported_session_id).one()
        assert audit.resource_id == str(imported_session_id)
        audit_details = json.loads(audit.details_json)
        assert audit_details['turnEventsImported'] == 2
        assert audit_details['projectedLogEntries'] == 2

        events = TurnEvent.query.filter_by(session_id=imported_session_id).order_by(TurnEvent.event_id.asc()).all()
        assert [event.event_type for event in events] == [PLAYER_MESSAGE_EVENT, DM_RESPONSE_EVENT]
        assert [event.player_id for event in events] == [ids['player_id'], ids['player_id']]
        player_event_payload = json.loads(events[0].payload_json)
        assert player_event_payload['metadata']['imported_from_turn_id'] == 77
        assert player_event_payload['metadata']['imported_from_event_id'] == 101

        log_entries = (
            SessionLogEntry.query.filter_by(session_id=imported_session_id)
            .order_by(SessionLogEntry.id.asc())
            .all()
        )
        assert [entry.entry_type for entry in log_entries] == ['player', 'dm']
        assert 'I test the restored gate.' in log_entries[0].message
        assert 'The restored gate opens.' in log_entries[1].message
        assert 'This should not be duplicated' not in '\n'.join(entry.message for entry in log_entries)


def test_export_session_returns_importable_shape_and_round_trips_without_log_duplication(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        assert session is not None
        session.name = 'Exportable Ash Gate'
        db.session.add(
            SessionState(
                session_id=ids['session_id'],
                current_location='Ash Gate',
                current_quest='Prove export/import',
                rolling_summary='The party prepared an export proof.',
                active_segments=json.dumps([{'title': 'Ash Gate'}]),
                memory_snippets=json.dumps([{'summary': 'Export memory'}]),
            )
        )
        db.session.add(
            SessionLogEntry(
                session_id=ids['session_id'],
                message='This source-only log should not be duplicated on import.',
                entry_type='system',
                metadata_json=json.dumps({'source': 'export_route_test'}),
            )
        )
        record_turn_event(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            event_type=PLAYER_MESSAGE_EVENT,
            payload={'speaker': 'Seraphina', 'message': 'I test the export gate.'},
        )
        record_turn_event(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            event_type=DM_RESPONSE_EVENT,
            payload={'message': 'The export gate answers.'},
        )
        db.session.commit()

    export_response = client.get(f"/api/sessions/{ids['session_id']}/export?player_id={ids['player_id']}")

    assert export_response.status_code == 200
    export_payload = export_response.get_json()
    assert export_payload['selectedIds'] == {
        'campaignId': ids['campaign_id'],
        'sessionId': ids['session_id'],
        'playerId': ids['player_id'],
    }
    assert export_payload['campaign']['campaign_id'] == ids['campaign_id']
    assert export_payload['selectedSession']['display_name'] == 'Exportable Ash Gate'
    assert export_payload['selectedPlayer']['player_id'] == ids['player_id']
    assert [player['player_id'] for player in export_payload['players']] == [ids['player_id']]
    assert export_payload['sessionState']['current_location'] == 'Ash Gate'
    assert 'This source-only log should not be duplicated' in '\n'.join(
        entry['message'] for entry in export_payload['logEntries']
    )
    assert [event['event_type'] for event in export_payload['turnEvents']] == [
        PLAYER_MESSAGE_EVENT,
        DM_RESPONSE_EVENT,
    ]

    import_response = client.post('/api/sessions/import', json=export_payload)

    assert import_response.status_code == 201
    import_payload = import_response.get_json()
    assert import_payload['counts'] == {
        'turn_events': 2,
        'projected_log_entries': 2,
        'log_entries': 0,
        'session_state': 1,
    }
    imported_session_id = import_payload['session_id']
    imported_log_response = client.get(f'/api/sessions/{imported_session_id}/log?limit=100')
    assert imported_log_response.status_code == 200
    imported_messages = '\n'.join(entry['message'] for entry in imported_log_response.get_json()['entries'])
    assert 'I test the export gate.' in imported_messages
    assert 'The export gate answers.' in imported_messages
    assert 'This source-only log should not be duplicated' not in imported_messages


def test_import_session_preserves_campaign_pack_state_progress_events_and_archive_restore(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        session.state_snapshot = json.dumps(
            {
                'schemaVersion': 1,
                'sessionId': ids['session_id'],
                'campaignId': ids['campaign_id'],
                'currentScene': {
                    'locationId': 'old_road',
                    'name': 'Old Road',
                    'activeQuestIds': ['q_missing_caravan'],
                    'activeNpcIds': ['npc_captain_veyra'],
                },
                'locations': [
                    {'id': 'bleakmoor_gate', 'name': 'Bleakmoor Gate', 'source': 'campaign_pack', 'packId': 'bleakmoor_intro'},
                    {'id': 'old_road', 'name': 'Old Road', 'source': 'campaign_pack', 'packId': 'bleakmoor_intro'},
                ],
                'knownNpcs': [
                    {'id': 'npc_captain_veyra', 'name': 'Captain Veyra', 'source': 'campaign_pack', 'packId': 'bleakmoor_intro'}
                ],
                'quests': [
                    {'id': 'q_missing_caravan', 'title': 'Find the Missing Caravan', 'status': 'active', 'source': 'campaign_pack', 'packId': 'bleakmoor_intro'}
                ],
                'flags': {
                    'campaignPackActiveCheckpointId': 'cp_old_road',
                    'campaignPackCompletedCheckpointIds': ['cp_gate'],
                    'campaignPackSkippedCheckpointIds': [],
                    'campaignPackFailedCheckpointIds': [],
                    'campaignPackProgressRevision': 3,
                },
                'campaignPack': {
                    'packId': 'bleakmoor_intro',
                    'title': 'The Lanterns of Bleakmoor',
                    'schemaVersion': '1',
                    'version': '1.0.0',
                    'source': 'campaign_pack',
                    'snapshotSchemaVersion': 1,
                    'progressSchemaVersion': 1,
                    'progressRevision': 3,
                    'progressEventsVersion': 1,
                    'activeCheckpointId': 'cp_old_road',
                    'completedCheckpointIds': ['cp_gate'],
                    'skippedCheckpointIds': [],
                    'failedCheckpointIds': [],
                    'checkpoints': [
                        {'id': 'cp_gate', 'title': 'Question the gate captain', 'nextCheckpointIds': ['cp_old_road']},
                        {'id': 'cp_old_road', 'title': 'Find the old road', 'locationIds': ['old_road']},
                    ],
                    'catalog': {
                        'locations': [
                            {'id': 'bleakmoor_gate', 'name': 'Bleakmoor Gate'},
                            {'id': 'old_road', 'name': 'Old Road'},
                        ],
                        'npcs': [{'id': 'npc_lantern_keeper', 'name': 'Lantern Keeper', 'hiddenToPlayers': True}],
                        'quests': [{'id': 'q_hidden_witness', 'title': 'Find the Old Road Witness'}],
                        'encounters': [{'id': 'enc_lantern_wraith', 'title': 'Lantern Wraith'}],
                    },
                    'directorRules': {'mainQuestGeneration': 'pack_only'},
                },
            }
        )
        db.session.add(
            SessionState(
                session_id=ids['session_id'],
                current_location='Old Road',
                current_quest='Find the Missing Caravan',
                rolling_summary='The party reached the old road.',
                active_segments=json.dumps([]),
                memory_snippets=json.dumps([]),
            )
        )
        db.session.add(
            TurnEvent(
                session_id=ids['session_id'],
                campaign_id=ids['campaign_id'],
                event_type=PROGRESS_CHANGED_EVENT,
                payload_json=json.dumps(
                    {
                        'type': PROGRESS_CHANGED_EVENT,
                        'packId': 'bleakmoor_intro',
                        'action': 'advance',
                        'fromCheckpointId': 'cp_gate',
                        'toCheckpointId': 'cp_old_road',
                        'progressRevision': 3,
                    }
                ),
            )
        )
        db.session.commit()
        source_snapshot = json.loads(session.state_snapshot)
        progress_event = TurnEvent.query.filter_by(session_id=ids['session_id'], event_type=PROGRESS_CHANGED_EVENT).one()
        progress_event_id = progress_event.event_id
        progress_event_created_at = progress_event.created_at.isoformat()
        progress_event_payload = json.loads(progress_event.payload_json)

    response = client.post(
        '/api/sessions/import',
        json={
            'exportedAt': '2026-06-06T10:30:00+00:00',
            'selectedIds': {'campaignId': ids['campaign_id'], 'sessionId': ids['session_id']},
            'selectedSession': {
                'session_id': ids['session_id'],
                'display_name': 'Bleakmoor Reimport',
                'state_snapshot': source_snapshot,
            },
            'sessionState': {
                'current_location': 'Old Road',
                'current_quest': 'Find the Missing Caravan',
                'rolling_summary': 'The party reached the old road.',
            },
            'turnEvents': [
                {
                    'event_id': progress_event_id,
                    'event_type': PROGRESS_CHANGED_EVENT,
                    'payload': progress_event_payload,
                    'created_at': progress_event_created_at,
                }
            ],
        },
    )

    assert response.status_code == 201
    imported_session_id = response.get_json()['session_id']
    assert imported_session_id != ids['session_id']
    archive_response = client.post(f'/api/sessions/{imported_session_id}/archive')
    restore_response = client.post(f'/api/sessions/{imported_session_id}/restore')
    state_response = client.get(f'/api/sessions/{imported_session_id}/state')

    assert archive_response.status_code == 200
    assert restore_response.status_code == 200
    assert state_response.status_code == 200
    restored_snapshot = state_response.get_json()['state_snapshot']
    assert restored_snapshot['sessionId'] == imported_session_id
    assert restored_snapshot['campaignId'] == ids['campaign_id']
    assert restored_snapshot['source_session_id'] == ids['session_id']
    assert restored_snapshot['currentScene']['locationId'] == 'old_road'
    assert restored_snapshot['campaignPack']['activeCheckpointId'] == 'cp_old_road'
    assert restored_snapshot['campaignPack']['completedCheckpointIds'] == ['cp_gate']
    assert restored_snapshot['campaignPack']['progressRevision'] == 3
    assert restored_snapshot['campaignPack']['catalog']['npcs'][0]['id'] == 'npc_lantern_keeper'
    assert restored_snapshot['campaignPack']['directorRules']['mainQuestGeneration'] == 'pack_only'

    with app.app_context():
        imported_events = TurnEvent.query.filter_by(
            session_id=imported_session_id,
            event_type=PROGRESS_CHANGED_EVENT,
        ).all()
        imported_event_payload = json.loads(imported_events[0].payload_json)
    assert len(imported_events) == 1
    assert imported_event_payload['toCheckpointId'] == 'cp_old_road'
    assert imported_event_payload['metadata']['imported_from_event_id'] == progress_event_id


def test_import_session_falls_back_to_log_entries_when_events_are_absent(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        '/api/sessions/import',
        json={
            'campaign_id': ids['campaign_id'],
            'name': 'Legacy Snapshot',
            'logEntries': [
                {
                    'message': 'Legacy player log',
                    'entry_type': 'player',
                    'metadata': {'source': 'legacy'},
                    'timestamp': '2026-06-06T10:34:00+00:00',
                },
                {
                    'message': 'Legacy DM log',
                    'entry_type': 'dm',
                    'metadata': {},
                    'timestamp': '2026-06-06T10:35:00+00:00',
                },
            ],
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    imported_session_id = payload['session_id']
    assert payload['session']['display_name'] == 'Legacy Snapshot'
    assert payload['counts']['turn_events'] == 0
    assert payload['counts']['log_entries'] == 2

    log_response = client.get(f"/api/sessions/{imported_session_id}/log")
    assert log_response.status_code == 200
    assert [entry['message'] for entry in log_response.get_json()['entries']] == [
        'Legacy player log',
        'Legacy DM log',
    ]


def test_import_session_bounds_nested_state_lists(client, app):
    ids = seed_world_campaign_player_session(app)
    huge_text = 'x' * 3000

    response = client.post(
        '/api/sessions/import',
        json={
            'campaign_id': ids['campaign_id'],
            'sessionState': {
                'active_segments': [{'title': huge_text} for _ in range(110)],
                'memory_snippets': [{'summary': huge_text, 'nested': [huge_text for _ in range(80)]}],
            },
        },
    )

    assert response.status_code == 201
    imported_session_id = response.get_json()['session_id']
    with app.app_context():
        imported_state = SessionState.query.filter_by(session_id=imported_session_id).one()
        active_segments = json.loads(imported_state.active_segments)
        memory_snippets = json.loads(imported_state.memory_snippets)

    assert len(active_segments) == 100
    assert len(active_segments[0]['title']) == 2000
    assert len(memory_snippets[0]['summary']) == 2000
    assert len(memory_snippets[0]['nested']) == 50


def test_import_session_rejects_missing_or_unknown_campaign(client):
    missing_campaign_response = client.post('/api/sessions/import', json={'logEntries': []})
    assert missing_campaign_response.status_code == 400
    assert missing_campaign_response.get_json()['error_code'] == 'validation_error'

    unknown_campaign_response = client.post('/api/sessions/import', json={'campaign_id': 99999})
    assert unknown_campaign_response.status_code == 404
    assert unknown_campaign_response.get_json()['error_code'] == 'campaign_not_found'


def test_update_session_persists_name_in_metadata_columns(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.patch(f"/api/sessions/{ids['session_id']}", json={'name': 'Pyres at Dawn'})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['state_snapshot'] == {}
    assert payload['display_name'] == 'Pyres at Dawn'
    assert payload['updated_at']
    assert payload['status'] == 'active'

    sessions_response = client.get(f"/api/sessions/campaigns/{ids['campaign_id']}/sessions")
    assert sessions_response.status_code == 200
    sessions_payload = sessions_response.get_json()
    renamed = next(item for item in sessions_payload if item['session_id'] == ids['session_id'])
    assert renamed['state_snapshot'] == {}
    assert renamed['display_name'] == 'Pyres at Dawn'
    assert renamed['latest_activity_at']
    assert renamed['turn_count'] == 0
    assert renamed['latest_summary'] == ''


def test_update_session_rejects_stale_expected_updated_at(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.patch(
        f"/api/sessions/{ids['session_id']}",
        json={'name': 'Old Name', 'expected_updated_at': '1999-01-01T00:00:00'},
    )

    assert response.status_code == 409
    assert response.get_json()['error_code'] == 'stale_update'


def test_list_campaign_sessions_includes_display_metadata(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I inspect the relic.',
            dm_output='It glows blue.',
            status='completed',
            outcome_status='resolved',
        )
        db.session.add(turn)
        db.session.flush()
        db.session.add(SessionLogEntry(session_id=ids['session_id'], message='It glows blue.', entry_type='dm'))
        db.session.add(SessionState(session_id=ids['session_id'], rolling_summary='The relic started glowing.'))
        db.session.commit()

    response = client.get(f"/api/sessions/campaigns/{ids['campaign_id']}/sessions")

    assert response.status_code == 200
    payload = response.get_json()[0]
    assert payload['session_id'] == ids['session_id']
    assert payload['display_name'] == 'Session 1'
    assert payload['turn_count'] == 1
    assert payload['latest_summary'] == 'The relic started glowing.'
    assert payload['latest_activity_at']
    assert payload['updated_at']
    assert payload['is_archived'] is False
    assert payload['status'] == 'active'
    assert payload['deleted_at'] is None


def test_unnamed_sessions_display_campaign_relative_numbers(client, app):
    with app.app_context():
        world = World(name='Numbered World', description='Campaign-relative sessions')
        db.session.add(world)
        db.session.flush()
        first_campaign = Campaign(title='First Campaign', world_id=world.world_id)
        second_campaign = Campaign(title='Second Campaign', world_id=world.world_id)
        db.session.add_all([first_campaign, second_campaign])
        db.session.flush()
        first_session = Session(campaign_id=first_campaign.campaign_id)
        second_session = Session(campaign_id=first_campaign.campaign_id)
        other_campaign_session = Session(campaign_id=second_campaign.campaign_id)
        db.session.add_all([first_session, second_session, other_campaign_session])
        db.session.commit()
        first_campaign_id = first_campaign.campaign_id
        second_campaign_id = second_campaign.campaign_id
        first_session_id = first_session.session_id
        second_session_id = second_session.session_id
        other_session_id = other_campaign_session.session_id

    first_response = client.get(f'/api/sessions/campaigns/{first_campaign_id}/sessions')
    second_response = client.get(f'/api/sessions/campaigns/{second_campaign_id}/sessions')

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first_payload = {session['session_id']: session for session in first_response.get_json()}
    second_payload = {session['session_id']: session for session in second_response.get_json()}
    assert first_payload[first_session_id]['display_name'] == 'Session 1'
    assert first_payload[second_session_id]['display_name'] == 'Session 2'
    assert second_payload[other_session_id]['display_name'] == 'Session 1'


def test_update_session_rejects_empty_and_overlong_names(client, app):
    ids = seed_world_campaign_player_session(app)

    empty_response = client.patch(f"/api/sessions/{ids['session_id']}", json={'name': '   '})
    assert empty_response.status_code == 400
    assert empty_response.get_json()['error_code'] == 'validation_error'

    overlong_response = client.patch(f"/api/sessions/{ids['session_id']}", json={'name': 'x' * 81})
    assert overlong_response.status_code == 400
    assert overlong_response.get_json()['error_code'] == 'validation_error'


def test_update_missing_session_returns_404(client):
    response = client.patch('/api/sessions/99999', json={'name': 'Missing Session'})

    assert response.status_code == 404
    assert response.get_json()['error_code'] == 'session_not_found'


def test_delete_session_removes_session_owned_rows(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I listen.',
            dm_output='Ash falls.',
            status='completed',
            outcome_status='resolved',
        )
        db.session.add(turn)
        db.session.flush()
        db.session.add(TurnCanonUpdate(turn_id=turn.turn_id, campaign_id=ids['campaign_id']))
        db.session.add(SessionLogEntry(session_id=ids['session_id'], message='log', entry_type='dm'))
        db.session.add(SessionState(session_id=ids['session_id'], rolling_summary='summary'))
        db.session.commit()
        turn_id = turn.turn_id

    response = client.delete(f"/api/sessions/{ids['session_id']}?hard=true")
    assert response.status_code == 200
    assert response.get_json()['deleted'] is True

    with app.app_context():
        assert db.session.get(Session, ids['session_id']) is None
        assert DmTurn.query.filter_by(session_id=ids['session_id']).count() == 0
        assert SessionLogEntry.query.filter_by(session_id=ids['session_id']).count() == 0
        assert SessionState.query.filter_by(session_id=ids['session_id']).count() == 0
        assert TurnCanonUpdate.query.filter_by(turn_id=turn_id).count() == 0


def test_hard_delete_session_removes_session_origin_canon(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I study the sigil.',
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
            source_turn_id=turn.turn_id,
        )
        thread = StoryThread(
            campaign_id=ids['campaign_id'],
            title='Open the Amber Gate',
            origin_turn_id=turn.turn_id,
            last_touched_turn_id=turn.turn_id,
            resolved_turn_id=turn.turn_id,
        )
        db.session.add_all([fact, thread])
        db.session.commit()
        entity_id = entity.entity_id
        fact_id = fact.fact_id
        thread_id = thread.thread_id

    response = client.delete(f"/api/sessions/{ids['session_id']}?hard=true")
    assert response.status_code == 200

    with app.app_context():
        assert db.session.get(StoryEntity, entity_id) is None
        assert db.session.get(StoryFact, fact_id) is None
        assert db.session.get(StoryThread, thread_id) is None


def test_delete_session_archives_and_restore_resurfaces_in_lists(client, app):
    ids = seed_world_campaign_player_session(app)

    first_response = client.delete(f"/api/sessions/{ids['session_id']}")
    assert first_response.status_code == 200
    first_payload = first_response.get_json()
    assert first_payload['deleted'] is True
    assert first_payload['archived'] is True
    assert first_payload['session']['is_archived'] is True
    assert first_payload['session']['deleted_at']

    default_list = client.get(f"/api/sessions/campaigns/{ids['campaign_id']}/sessions")
    assert default_list.status_code == 200
    assert default_list.get_json() == []

    archived_list = client.get(f"/api/sessions/campaigns/{ids['campaign_id']}/sessions?include_archived=true")
    assert archived_list.status_code == 200
    archived_payload = archived_list.get_json()[0]
    assert archived_payload['session_id'] == ids['session_id']
    assert archived_payload['status'] == 'archived'

    restore_response = client.post(f"/api/sessions/{ids['session_id']}/restore")
    assert restore_response.status_code == 200
    restore_payload = restore_response.get_json()
    assert restore_payload['restored'] is True
    assert restore_payload['session']['status'] == 'active'
    assert restore_payload['session']['deleted_at'] is None
    with app.app_context():
        audits = (
            OperatorActionAudit.query.filter_by(session_id=ids['session_id'])
            .order_by(OperatorActionAudit.operator_audit_id.asc())
            .all()
        )
        assert [audit.action for audit in audits] == ['session.archive', 'session.restore']


def test_delete_missing_and_repeated_hard_delete_returns_404(client, app):
    ids = seed_world_campaign_player_session(app)

    missing_response = client.delete('/api/sessions/99999')
    assert missing_response.status_code == 404
    assert missing_response.get_json()['error_code'] == 'session_not_found'

    first_response = client.delete(f"/api/sessions/{ids['session_id']}?hard=true")
    assert first_response.status_code == 200
    with app.app_context():
        audit = OperatorActionAudit.query.filter_by(action='session.delete_hard', resource_id=str(ids['session_id'])).one()
        assert audit.campaign_id == ids['campaign_id']

    repeated_response = client.delete(f"/api/sessions/{ids['session_id']}?hard=true")
    assert repeated_response.status_code == 404
    assert repeated_response.get_json()['error_code'] == 'session_not_found'


def test_session_log_endpoint_returns_most_recent_entries_in_order(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        db.session.add_all(
            [
                SessionLogEntry(session_id=ids['session_id'], message='first', entry_type='dm'),
                SessionLogEntry(session_id=ids['session_id'], message='second', entry_type='dm'),
                SessionLogEntry(session_id=ids['session_id'], message='third', entry_type='dm'),
            ]
        )
        db.session.commit()

    response = client.get(f"/api/sessions/{ids['session_id']}/log?limit=2")
    assert response.status_code == 200
    payload = response.get_json()

    assert [entry['message'] for entry in payload['entries']] == ['second', 'third']
    assert payload['has_more'] is True
    assert payload['next_cursor'] == payload['entries'][0]['id']

    previous_response = client.get(
        f"/api/sessions/{ids['session_id']}/log?limit=2&before_id={payload['next_cursor']}"
    )
    assert previous_response.status_code == 200
    previous_payload = previous_response.get_json()
    assert [entry['message'] for entry in previous_payload['entries']] == ['first']
    assert previous_payload['has_more'] is False


def test_session_events_endpoint_returns_most_recent_events_in_order(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        db.session.add_all(
            [
                TurnEvent(
                    session_id=ids['session_id'],
                    campaign_id=ids['campaign_id'],
                    player_id=ids['player_id'],
                    event_type='player_message',
                    payload_json=json.dumps({'message': 'first'}),
                ),
                TurnEvent(
                    session_id=ids['session_id'],
                    campaign_id=ids['campaign_id'],
                    player_id=ids['player_id'],
                    event_type='dm_response',
                    payload_json=json.dumps({'message': 'second'}),
                ),
                TurnEvent(
                    session_id=ids['session_id'],
                    campaign_id=ids['campaign_id'],
                    event_type='canon_applied',
                    payload_json=json.dumps({'status': 'applied'}),
                ),
            ]
        )
        db.session.commit()

    response = client.get(f"/api/sessions/{ids['session_id']}/events?limit=2")
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['session_id'] == ids['session_id']
    assert [event['event_type'] for event in payload['events']] == ['dm_response', 'canon_applied']
    assert payload['events'][0]['payload']['message'] == 'second'
    assert payload['events'][1]['payload']['status'] == 'applied'
    assert payload['has_more'] is True
    assert payload['next_cursor'] == payload['events'][0]['event_id']

    previous_response = client.get(
        f"/api/sessions/{ids['session_id']}/events?limit=2&before_id={payload['next_cursor']}"
    )
    assert previous_response.status_code == 200
    previous_payload = previous_response.get_json()
    assert [event['event_type'] for event in previous_payload['events']] == ['player_message']
    assert previous_payload['has_more'] is False


def test_list_campaign_sessions_returns_404_for_missing_campaign(client):
    response = client.get('/api/sessions/campaigns/99999/sessions')

    assert response.status_code == 404
    assert response.get_json()['error_code'] == 'campaign_not_found'


def test_session_events_returns_404_for_missing_session(client):
    response = client.get('/api/sessions/99999/events')

    assert response.status_code == 404
    assert response.get_json()['error_code'] == 'session_not_found'
