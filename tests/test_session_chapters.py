from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import re
import threading

from sqlalchemy import update

import aidm_server.blueprints.sessions as sessions_blueprint
import aidm_server.services.session_chapters as session_chapters
from aidm_server.combat.state import normalize_combat_state
from aidm_server.creatures.core_bestiary import core_creature
from aidm_server.database import db
from aidm_server.models import (
    Campaign,
    CampaignPackCheckpointProgress,
    CampaignPackProgressEvent,
    CampaignPackSession,
    CombatEncounter,
    DmTurn,
    Session,
    SessionLogEntry,
    SessionState,
    SessionTurnLock,
    TurnEvent,
    World,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.services.session_chapters import (
    CHAPTER_CARRIED_KEYS,
    CHAPTER_RECAP_MAX_LENGTH,
    chapter_recap_text,
    chapter_snapshot_from,
)
from aidm_server.time_utils import utc_now
from aidm_server.turn_coordinator import session_turn_coordinator
from aidm_server.turn_events import DM_RESPONSE_EVENT, SESSION_ENDED_EVENT, SESSION_STARTED_EVENT, record_turn_event
from tests.helpers import seed_world_campaign_player_session
from tests.test_flagship_adventure import table  # noqa: F401 - the Play Now table fixture

NOW_ISO = '2026-11-10T12:00:00+00:00'
TRANSIENT_KEYS = (
    'sessionId',
    'playerCharacters',
    'activePlayerIds',
    'turnControl',
    'turnRecoveryGate',
    'stateRevision',
    'lastMutation',
    'stateMutationAudit',
    'recap',
    'ended_at',
    'client_session_id',
    'imported',
    'imported_at',
    'source_exported_at',
    'source_session_id',
    'importMetadata',
    'name',
    'title',
    'summary',
    'updated_at',
    'is_archived',
    'archived',
    'lastUpdatedAt',
)
CHAPTER_CLOSED = 'Chapter closed. The adventure continues in the next chapter.'
# A turn line of the narrator's rolling summary: ``T12 | P3: <action> | DM: <outcome>``.
ROLLING_SUMMARY_TURN_LINE = re.compile(r'T\d+ \| P\d+: ')
CHAPTER_UNSETTLED = 'Finish the current fight or roll before starting a new chapter.'


def _world_snapshot(ids: dict) -> dict:
    return {
        'schemaVersion': 1,
        'sessionId': ids['session_id'],
        'campaignId': ids['campaign_id'],
        'currentScene': {
            'locationId': 'sunken_archive',
            'name': 'Sunken Archive',
            'sceneType': 'dungeon',
            'combatState': 'none',
            'activeNpcIds': ['npc_archivist'],
            'activeQuestIds': ['q_lost_index'],
            'playerPositions': {str(ids['player_id']): 'near'},
            'playerZones': {str(ids['player_id']): 'stacks'},
            'characterPositions': {},
            'characterZones': {},
            'interactables': [{'id': 'vault_door', 'name': 'Vault Door', 'state': 'open'}],
            'hazards': [],
        },
        'locations': [{'id': 'sunken_archive', 'name': 'Sunken Archive', 'visited': True}],
        'locationSceneStates': {'old_ruins': {'interactables': [{'id': 'rubble', 'state': 'cleared'}]}},
        'knownNpcs': [{'id': 'npc_archivist', 'name': 'Archivist Mora', 'disposition': 'friendly'}],
        'partyNpcs': [],
        'quests': [
            {'id': 'q_relic', 'title': 'Find the relic', 'status': 'completed'},
            {'id': 'q_lost_index', 'title': 'Recover the lost index', 'status': 'active'},
        ],
        'flags': {'vaultOpened': True, 'archiveFlooded': False},
        'interactableActionLedger': {'open-vault': {'fingerprint': 'vault_door:open', 'eventIds': ['evt-1']}},
        'stateChangeLedger': [{'id': 'chg-vault', 'type': 'interactable', 'source': 'dm', 'appliedAt': NOW_ISO}],
        'combat': {'status': 'none', 'round': 1, 'participants': [], 'battlefield': {}, 'flags': {}},
        'playerCharacters': [{'playerId': ids['player_id'], 'name': 'Seraphina'}],
        'activePlayerIds': [ids['player_id']],
        'turnControl': {'mode': 'free'},
        'stateRevision': 7,
        'recap': 'The party drained the archive. They opened the vault.',
    }


def _seed_chapter_one(app, *, customize=None, rolling_summary: str = 'They reached the archive.'):
    ids = seed_world_campaign_player_session(app)
    snapshot = _world_snapshot(ids)
    if customize is not None:
        customize(snapshot)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        session_obj.state_snapshot = safe_json_dumps(snapshot, {})
        db.session.add(
            SessionState(
                session_id=ids['session_id'],
                current_location='Sunken Archive',
                current_quest='Recover the lost index',
                rolling_summary=rolling_summary,
                active_segments=safe_json_dumps([{'segment_id': 4, 'title': 'Flooded stacks'}], []),
                memory_snippets=safe_json_dumps(['The archivist owes the party a favour.'], []),
            )
        )
        db.session.commit()
    return ids


def _snapshot(app, session_id: int) -> dict:
    with app.app_context():
        return safe_json_loads(db.session.get(Session, session_id).state_snapshot, {})


def _session_status(app, session_id: int) -> str | None:
    with app.app_context():
        return db.session.get(Session, session_id).status


def _session_count(app, campaign_id: int) -> int:
    with app.app_context():
        return Session.query.filter_by(campaign_id=campaign_id).count()


def _log_messages(app, session_id: int) -> list[str]:
    with app.app_context():
        return [
            entry.message
            for entry in SessionLogEntry.query.filter_by(session_id=session_id).order_by(SessionLogEntry.id.asc())
        ]


def _event_payload(received, name):
    for event in received:
        if event['name'] == name:
            return event['args'][0] if event['args'] else {}
    return None


def _import_bleakmoor(client) -> dict:
    response = client.post('/api/campaigns/example-packs/bleakmoor_intro/import', json={})
    assert response.status_code == 201
    return response.get_json()


def _post_in_thread(app, path: str, payload: dict) -> dict:
    result = {}

    def request_start():
        response = app.test_client().post(path, json=payload)
        result['status_code'] = response.status_code
        result['payload'] = response.get_json()

    thread = threading.Thread(target=request_start)
    thread.start()
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    return result


def test_chapter_snapshot_from_copies_exactly_the_world_allow_list():
    previous = {key: {'marker': key} for key in CHAPTER_CARRIED_KEYS}
    previous['currentScene'] = {'locationId': 'gate', 'name': 'Gate'}
    previous.update({key: f'transient {key}' for key in TRANSIENT_KEYS})
    previous['combat'] = {'status': 'active', 'participants': [{'id': 'wolf'}]}
    previous['customDebugKey'] = {'keep': False}

    snapshot = chapter_snapshot_from(
        previous,
        new_client_session_id='chapter-2',
        chapter_number=2,
        previous_session_id=11,
        recap_text='They opened the vault.',
        now_iso=NOW_ISO,
    )

    assert set(snapshot) == {*CHAPTER_CARRIED_KEYS, 'combat', 'client_session_id', 'lastUpdatedAt', 'chapter'}
    for key in CHAPTER_CARRIED_KEYS:
        if key != 'currentScene':
            assert snapshot[key] == previous[key]
    assert snapshot['client_session_id'] == 'chapter-2'
    assert snapshot['lastUpdatedAt'] == NOW_ISO
    assert snapshot['chapter'] == {
        'number': 2,
        'previousSessionId': 11,
        'startedAt': NOW_ISO,
        'recap': 'They opened the vault.',
    }
    assert 'customDebugKey' not in snapshot
    assert all(key not in snapshot for key in set(TRANSIENT_KEYS) - {'client_session_id', 'lastUpdatedAt'})


def test_chapter_snapshot_from_resets_combat_and_deep_copies_the_world():
    previous = {
        'currentScene': {
            'locationId': 'ridge',
            'name': 'Wolf Ridge',
            'combatState': 'active',
            'playerPositions': {'1': 'melee'},
            'playerZones': {'1': 'ridge_top'},
            'characterPositions': {'wolf': 'near'},
            'characterZones': {'wolf': 'ridge_top'},
            'interactables': [{'id': 'cairn', 'state': 'toppled'}],
        },
        'flags': {'wolvesAngered': True},
        'quests': [{'id': 'q_wolves', 'status': 'active', 'objectives': [{'id': 'o1', 'done': False}]}],
        'combat': {'status': 'active', 'round': 3, 'participants': [{'id': 'wolf', 'name': 'Wolf'}]},
    }
    original = deepcopy(previous)

    snapshot = chapter_snapshot_from(
        previous,
        new_client_session_id=None,
        chapter_number=3,
        previous_session_id=None,
        recap_text='',
        now_iso=NOW_ISO,
    )

    scene = snapshot['currentScene']
    assert scene['combatState'] == 'none'
    assert scene['playerPositions'] == {}
    assert scene['playerZones'] == {}
    assert scene['characterPositions'] == {}
    assert scene['characterZones'] == {}
    assert scene['interactables'] == [{'id': 'cairn', 'state': 'toppled'}]
    assert snapshot['combat'] == normalize_combat_state(None, scene)
    assert snapshot['combat']['status'] == 'none'
    assert 'client_session_id' not in snapshot

    snapshot['flags']['wolvesAngered'] = False
    snapshot['quests'][0]['objectives'][0]['done'] = True
    scene['interactables'][0]['state'] = 'rebuilt'
    assert previous == original


def test_chapter_recap_prefers_snapshot_recap_and_trims_on_a_sentence_boundary():
    continue_line = 'Sunken Archive — They reached the archive.'
    assert chapter_recap_text({'recap': '  The vault opened.  '}, continue_line) == 'The vault opened.'
    assert chapter_recap_text({'recap': '   '}, continue_line) == continue_line
    assert chapter_recap_text({}, None) == ''

    sentence = 'The party crossed the drowned stacks and argued with the archivist. '
    long_recap = sentence * 40
    trimmed = chapter_recap_text({'recap': long_recap}, None)

    assert len(trimmed) <= CHAPTER_RECAP_MAX_LENGTH
    assert trimmed.endswith('archivist.')
    assert long_recap.startswith(trimmed)
    assert len(trimmed) > CHAPTER_RECAP_MAX_LENGTH - len(sentence)


def test_first_chapter_keeps_todays_welcome_and_empty_world(client, app):
    with app.app_context():
        world = World(name='Empty World', description='No sessions yet')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Fresh Campaign', world_id=world.world_id, location='Old Ruins')
        db.session.add(campaign)
        db.session.commit()
        campaign_id = campaign.campaign_id

    response = client.post('/api/sessions/start', json={'campaign_id': campaign_id, 'client_session_id': 'first-1'})

    assert response.status_code == 201
    payload = response.get_json()
    assert payload['chapter_number'] == 1
    assert payload['continued_from_session_id'] is None
    assert payload['is_current'] is True
    assert payload['status'] == 'active'
    assert _snapshot(app, payload['session_id']) == {'client_session_id': 'first-1'}
    assert _log_messages(app, payload['session_id']) == [
        '**Welcome to the table. Choose your opening move when you are ready.**'
    ]
    with app.app_context():
        assert db.session.get(Session, payload['session_id']).name is None


def test_continue_carries_the_world_into_the_next_chapter(client, app):
    ids = _seed_chapter_one(app)

    response = client.post(
        '/api/sessions/start',
        json={'campaign_id': ids['campaign_id'], 'client_session_id': 'chapter-2'},
    )

    assert response.status_code == 201
    payload = response.get_json()
    chapter_two_id = payload['session_id']
    recap = 'The party drained the archive. They opened the vault.'
    assert payload == {
        'session_id': chapter_two_id,
        'idempotent_replay': False,
        'status': 'active',
        'is_current': True,
        'chapter_number': 2,
        'ended_at': None,
        'chapter_recap': recap,
        'continued_from_session_id': ids['session_id'],
    }

    snapshot = _snapshot(app, chapter_two_id)
    assert snapshot['flags'] == {'vaultOpened': True, 'archiveFlooded': False}
    assert snapshot['quests'][0] == {'id': 'q_relic', 'title': 'Find the relic', 'status': 'completed'}
    assert snapshot['knownNpcs'][0]['disposition'] == 'friendly'
    assert snapshot['currentScene']['locationId'] == 'sunken_archive'
    assert snapshot['currentScene']['interactables'] == [{'id': 'vault_door', 'name': 'Vault Door', 'state': 'open'}]
    assert snapshot['currentScene']['playerPositions'] == {}
    assert snapshot['locationSceneStates']['old_ruins']['interactables'][0]['state'] == 'cleared'
    assert snapshot['interactableActionLedger'] == {'open-vault': {'fingerprint': 'vault_door:open', 'eventIds': ['evt-1']}}
    assert snapshot['combat']['status'] == 'none'
    assert snapshot['chapter']['number'] == 2
    assert snapshot['chapter']['recap'] == recap
    assert snapshot['chapter']['previousSessionId'] == ids['session_id']
    assert snapshot['client_session_id'] == 'chapter-2'
    assert 'playerCharacters' not in snapshot
    assert 'stateRevision' not in snapshot

    previous = _snapshot(app, ids['session_id'])
    assert _session_status(app, ids['session_id']) == 'ended'
    assert previous['ended_at']
    assert previous['recap'] == recap
    assert previous['flags'] == {'vaultOpened': True, 'archiveFlooded': False}
    assert _log_messages(app, ids['session_id']) == [CHAPTER_CLOSED]
    assert _log_messages(app, chapter_two_id) == [f'**Chapter 2 begins.**\n\nPreviously: {recap}']

    with app.app_context():
        chapter_two = db.session.get(Session, chapter_two_id)
        assert chapter_two.name == 'Chapter 2'
        state = SessionState.query.filter_by(session_id=chapter_two_id).one()
        assert state.current_location == 'Sunken Archive'
        assert state.current_quest == 'Recover the lost index'
        assert state.rolling_summary == 'They reached the archive.'
        assert safe_json_loads(state.active_segments, []) == [{'segment_id': 4, 'title': 'Flooded stacks'}]
        assert safe_json_loads(state.memory_snippets, []) == ['The archivist owes the party a favour.']
        closed_event = TurnEvent.query.filter_by(session_id=ids['session_id'], event_type=SESSION_ENDED_EVENT).one()
        assert safe_json_loads(closed_event.payload_json, {})['metadata']['kind'] == 'chapter_closed'


def test_without_a_recap_the_next_chapter_recalls_the_place_and_the_last_narration(client, app):
    ids = _seed_chapter_one(
        app,
        customize=lambda snapshot: snapshot.pop('recap'),
        rolling_summary='T7 | P1: I bargain | DM: The archivist relents.',
    )
    with app.app_context():
        record_turn_event(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            event_type=DM_RESPONSE_EVENT,
            payload={'message': '*The archivist* sets down her lamp. "Ask, then."'},
        )
        db.session.commit()

    response = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id'], 'name': 'The Flooded Stacks'})

    assert response.status_code == 201
    payload = response.get_json()
    previously = 'Sunken Archive — The archivist sets down her lamp.'
    assert payload['chapter_recap'] == previously
    assert _log_messages(app, payload['session_id']) == [f'**Chapter 2 begins.**\n\nPreviously: {previously}']
    with app.app_context():
        assert db.session.get(Session, payload['session_id']).name == 'The Flooded Stacks'


def test_without_a_recap_or_narration_the_next_chapter_has_no_previously_line(client, app):
    ids = _seed_chapter_one(app, customize=lambda snapshot: snapshot.pop('recap'), rolling_summary='T7 | P1: I wait')

    response = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id']})

    assert response.status_code == 201
    assert response.get_json()['chapter_recap'] == ''
    assert _log_messages(app, response.get_json()['session_id']) == ['**Chapter 2 begins.**']


def test_continue_carries_campaign_pack_progress_rows(client, app):
    imported = _import_bleakmoor(client)
    chapter_one_id = imported['session_id']
    assert client.post(
        f'/api/sessions/{chapter_one_id}/campaign-pack/progress',
        json={'action': 'advance'},
    ).status_code == 200
    assert client.post(
        f'/api/sessions/{chapter_one_id}/campaign-pack/progress',
        json={'action': 'skip', 'reason': 'The party took the marsh path.'},
    ).status_code == 200
    before = client.get(f'/api/sessions/{chapter_one_id}/campaign-pack/progress').get_json()

    response = client.post('/api/sessions/start', json={'campaign_id': imported['campaign_id']})

    assert response.status_code == 201
    chapter_two_id = response.get_json()['session_id']
    after = client.get(f'/api/sessions/{chapter_two_id}/campaign-pack/progress').get_json()
    for key in (
        'activeCheckpointId',
        'completedCheckpointIds',
        'skippedCheckpointIds',
        'failedCheckpointIds',
        'progressRevision',
    ):
        assert after[key] == before[key]
    assert before['progressRevision'] == 2
    assert before['completedCheckpointIds'] == ['cp_gate', 'cp_old_road']
    assert before['skippedCheckpointIds'] == ['cp_old_road']

    with app.app_context():
        previous_row = CampaignPackSession.query.filter_by(session_id=chapter_one_id).one()
        new_row = CampaignPackSession.query.filter_by(session_id=chapter_two_id).one()
        assert new_row.active_checkpoint_id == previous_row.active_checkpoint_id == before['activeCheckpointId']
        assert new_row.progress_revision == previous_row.progress_revision == 2
        assert new_row.campaign_pack_id == previous_row.campaign_pack_id
        assert new_row.installed_pack_id == previous_row.installed_pack_id

        def statuses(row):
            return {
                progress.checkpoint_id: progress.status
                for progress in CampaignPackCheckpointProgress.query.filter_by(
                    campaign_pack_session_id=row.campaign_pack_session_id
                )
            }

        assert statuses(new_row) == statuses(previous_row)
        assert CampaignPackProgressEvent.query.filter_by(session_id=chapter_two_id).count() == 0
        assert CampaignPackProgressEvent.query.filter_by(session_id=chapter_one_id).count() == 2

    ended_control = client.post(
        f'/api/sessions/{chapter_one_id}/campaign-pack/progress',
        json={'action': 'advance'},
    )
    assert ended_control.status_code == 409
    assert ended_control.get_json()['error_code'] == 'session_ended'


def test_ended_chapter_rejects_play_but_keeps_reads(client, app, socketio):
    ids = _seed_chapter_one(app)
    chapter_two_id = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id']}).get_json()['session_id']

    mutation = client.patch(
        f"/api/sessions/{ids['session_id']}/content-settings",
        json={'content_rating': 'mature'},
    )

    assert mutation.status_code == 409
    assert mutation.get_json() == {
        'error': 'This chapter has ended. Continue in the current chapter.',
        'error_code': 'session_ended',
        'details': {'current_session_id': chapter_two_id},
    }
    for path in ('state', 'log', 'events', 'export', 'chronicle', 'recap'):
        assert client.get(f"/api/sessions/{ids['session_id']}/{path}").status_code == 200, path
    assert client.patch(
        f'/api/sessions/{chapter_two_id}/content-settings',
        json={'content_rating': 'mature'},
    ).status_code == 200

    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    received = socket_client.get_received()
    error = _event_payload(received, 'error')
    assert error['error_code'] == 'session_ended'
    assert error['error'] == 'This chapter has ended. Continue in the current chapter.'
    assert error['details'] == {'current_session_id': chapter_two_id}
    assert _event_payload(received, 'player_joined') is None
    assert str(ids['session_id']) not in socketio.server.manager.rooms.get('/', {})

    socket_client.emit('join_session', {'session_id': chapter_two_id, 'player_id': ids['player_id']})
    assert _event_payload(socket_client.get_received(), 'error') is None


def test_message_to_a_chapter_that_ended_after_join_names_the_current_chapter(client, app, socketio):
    ids = _seed_chapter_one(app)
    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    assert _event_payload(socket_client.get_received(), 'error') is None
    chapter_two_id = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id']}).get_json()['session_id']

    socket_client.emit(
        'send_message',
        {
            'session_id': ids['session_id'],
            'campaign_id': ids['campaign_id'],
            'player_id': ids['player_id'],
            'message': 'I open the vault door.',
        },
    )

    error = _event_payload(socket_client.get_received(), 'error')
    assert error['error_code'] == 'session_ended'
    assert error['details'] == {'current_session_id': chapter_two_id}


def test_combat_start_against_an_ended_chapter_returns_session_ended(client, app):
    ids = _seed_chapter_one(app)
    chapter_two_id = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id']}).get_json()['session_id']
    ended_snapshot = _snapshot(app, ids['session_id'])

    response = client.post(
        f"/api/sessions/{ids['session_id']}/combat/start",
        json={'creature': core_creature('wolf'), 'enemyCount': 1},
    )

    assert response.status_code == 409
    assert response.get_json() == {
        'error': 'This chapter has ended. Continue in the current chapter.',
        'error_code': 'session_ended',
        'details': {'current_session_id': chapter_two_id},
    }
    assert _snapshot(app, ids['session_id']) == ended_snapshot
    with app.app_context():
        assert CombatEncounter.query.filter_by(session_id=ids['session_id']).count() == 0


def test_unsettled_chapter_blocks_continuation_unless_an_operator_forces_it(client, app):
    ids = _seed_chapter_one(app)
    with app.app_context():
        db.session.add(
            DmTurn(
                session_id=ids['session_id'],
                campaign_id=ids['campaign_id'],
                player_id=ids['player_id'],
                player_input='I pick the lock.',
                dm_output='Roll Dexterity.',
                requires_roll=True,
                outcome_status='deferred',
                status='completed',
            )
        )
        db.session.commit()

    blocked = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id']})

    assert blocked.status_code == 409
    assert blocked.get_json()['error_code'] == 'chapter_unsettled'
    assert blocked.get_json()['error'] == CHAPTER_UNSETTLED
    assert _session_status(app, ids['session_id']) == 'active'
    assert _session_count(app, ids['campaign_id']) == 1

    forced = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id'], 'force': True})

    assert forced.status_code == 201
    assert _session_status(app, ids['session_id']) == 'ended'
    assert _snapshot(app, forced.get_json()['session_id'])['combat']['status'] == 'none'


def test_active_combat_and_inflight_turn_block_continuation(client, app):
    def start_fight(snapshot):
        snapshot['combat'] = {'status': 'active', 'round': 2, 'participants': [{'id': 'wolf', 'name': 'Wolf'}]}

    combat_ids = _seed_chapter_one(app, customize=start_fight)

    combat_response = client.post('/api/sessions/start', json={'campaign_id': combat_ids['campaign_id']})

    assert combat_response.status_code == 409
    assert combat_response.get_json()['error_code'] == 'chapter_unsettled'
    assert combat_response.get_json()['details']['reason'] == 'active_combat'

    turn_ids = _seed_chapter_one(app)
    with app.app_context():
        db.session.add(
            DmTurn(
                session_id=turn_ids['session_id'],
                campaign_id=turn_ids['campaign_id'],
                player_id=turn_ids['player_id'],
                player_input='I search the stacks.',
                status='processing',
            )
        )
        db.session.commit()

    turn_response = client.post('/api/sessions/start', json={'campaign_id': turn_ids['campaign_id']})

    assert turn_response.status_code == 409
    assert turn_response.get_json()['details']['reason'] == 'turn_in_progress'
    assert _session_status(app, turn_ids['session_id']) == 'active'


def test_force_requires_operator_capability_and_inputs_are_validated(client, app, monkeypatch):
    ids = _seed_chapter_one(app)
    real_capability_check = sessions_blueprint.current_actor_has_capability
    monkeypatch.setattr(
        sessions_blueprint,
        'current_actor_has_capability',
        lambda capability: capability != 'admin_workspace' and real_capability_check(capability),
    )

    forced = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id'], 'force': True})
    bad_continuity = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id'], 'continuity': 'rewind'})
    bad_force = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id'], 'force': 'yes'})

    assert forced.status_code == 403
    assert forced.get_json()['details'] == {'required_capability': 'admin_workspace'}
    assert bad_continuity.status_code == 400
    assert bad_continuity.get_json()['error_code'] == 'validation_error'
    assert bad_force.status_code == 400
    assert _session_count(app, ids['campaign_id']) == 1
    assert _session_status(app, ids['session_id']) == 'active'


def test_concurrent_turn_rejects_start_with_session_busy(app):
    ids = _seed_chapter_one(app)

    with app.app_context():
        app.config['AIDM_TURN_COORDINATOR_STORE'] = 'memory'
        with session_turn_coordinator.serialized(ids['session_id']):
            result = _post_in_thread(app, '/api/sessions/start', {'campaign_id': ids['campaign_id']})

    assert result['status_code'] == 409
    assert result['payload']['error_code'] == 'session_busy'
    assert _session_status(app, ids['session_id']) == 'active'
    assert _session_count(app, ids['campaign_id']) == 1


def test_lost_turn_lease_at_commit_rejects_start_with_session_busy(client, app, monkeypatch):
    ids = _seed_chapter_one(app)
    with app.app_context():
        app.config['AIDM_TURN_COORDINATOR_STORE'] = 'database'
        app.config['AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS'] = 30
        app.config['AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS'] = 10
    real_close = session_chapters._close_open_chapters

    def close_after_lease_is_taken(campaign_id, *, now):
        with db.engine.begin() as connection:
            connection.execute(
                update(SessionTurnLock.__table__)
                .where(SessionTurnLock.session_id == ids['session_id'])
                .values(
                    fencing_token=SessionTurnLock.fencing_token + 1,
                    expires_at=utc_now() + timedelta(minutes=1),
                )
            )
        real_close(campaign_id, now=now)

    monkeypatch.setattr(session_chapters, '_close_open_chapters', close_after_lease_is_taken)

    response = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id']})

    assert response.status_code == 409
    assert response.get_json()['error_code'] == 'session_busy'
    assert _session_status(app, ids['session_id']) == 'active'
    assert _session_count(app, ids['campaign_id']) == 1
    assert _log_messages(app, ids['session_id']) == []


def test_idempotent_replay_returns_the_same_chapter_without_closing_or_copying_again(client, app):
    ids = _seed_chapter_one(app)
    request_payload = {'campaign_id': ids['campaign_id'], 'client_session_id': 'chapter-2-retry'}

    first = client.post('/api/sessions/start', json=request_payload)
    chapter_two_id = first.get_json()['session_id']
    snapshot_after_first = _snapshot(app, chapter_two_id)
    with app.app_context():
        app.config['AIDM_TURN_COORDINATOR_STORE'] = 'memory'
        with session_turn_coordinator.serialized(chapter_two_id):
            replay = _post_in_thread(app, '/api/sessions/start', request_payload)

    assert first.status_code == 201
    assert replay['status_code'] == 200
    assert replay['payload']['session_id'] == chapter_two_id
    assert replay['payload']['idempotent_replay'] is True
    assert replay['payload']['continued_from_session_id'] == ids['session_id']
    assert replay['payload']['chapter_number'] == 2
    assert _session_count(app, ids['campaign_id']) == 2
    assert _snapshot(app, chapter_two_id) == snapshot_after_first
    assert _log_messages(app, ids['session_id']) == [CHAPTER_CLOSED]
    with app.app_context():
        assert TurnEvent.query.filter_by(session_id=chapter_two_id, event_type=SESSION_STARTED_EVENT).count() == 1


def test_fresh_on_a_pack_campaign_reseeds_the_opening_and_starting_checkpoint(client, app):
    imported = _import_bleakmoor(client)
    chapter_one_id = imported['session_id']
    opening = _snapshot(app, chapter_one_id)
    assert client.post(
        f'/api/sessions/{chapter_one_id}/campaign-pack/progress',
        json={'action': 'advance'},
    ).status_code == 200

    response = client.post(
        '/api/sessions/start',
        json={'campaign_id': imported['campaign_id'], 'continuity': 'fresh', 'client_session_id': 'fresh-2'},
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload['continued_from_session_id'] is None
    assert payload['chapter_recap'] == ''
    assert payload['chapter_number'] == 2
    snapshot = _snapshot(app, payload['session_id'])
    assert snapshot['chapter']['fresh'] is True
    assert snapshot['chapter']['recap'] == ''
    assert snapshot['chapter']['previousSessionId'] == chapter_one_id
    assert snapshot['client_session_id'] == 'fresh-2'

    def comparable(value: dict) -> dict:
        value = deepcopy(value)
        for key in ('sessionId', 'client_session_id', 'chapter', 'lastUpdatedAt'):
            value.pop(key, None)
        value['campaignPack'].pop('importedAt')
        return value

    assert comparable(snapshot) == comparable(opening)
    progress = client.get(f"/api/sessions/{payload['session_id']}/campaign-pack/progress").get_json()
    assert progress['activeCheckpointId'] == 'cp_gate'
    assert progress['completedCheckpointIds'] == []
    assert progress['progressRevision'] == 0
    with app.app_context():
        row = CampaignPackSession.query.filter_by(session_id=payload['session_id']).one()
        assert row.active_checkpoint_id == 'cp_gate'
        assert row.progress_revision == 0
        assert row.campaign_pack_id is not None
    assert _session_status(app, chapter_one_id) == 'ended'
    assert _log_messages(app, payload['session_id']) == ['**Chapter 2 begins.**']


def test_fresh_on_a_plain_campaign_starts_todays_empty_world(client, app):
    ids = _seed_chapter_one(app)

    response = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id'], 'continuity': 'fresh'})

    assert response.status_code == 201
    payload = response.get_json()
    snapshot = _snapshot(app, payload['session_id'])
    assert set(snapshot) == {'chapter'}
    assert snapshot['chapter']['fresh'] is True
    state = client.get(f"/api/sessions/{payload['session_id']}/state").get_json()
    assert state['current_location'] == 'Old Ruins'
    assert state['current_quest'] == 'Find the relic'
    assert state['rolling_summary'] == ''
    assert _session_status(app, ids['session_id']) == 'ended'
    assert _log_messages(app, ids['session_id']) == [CHAPTER_CLOSED]


def test_archive_and_restore_keep_an_ended_chapter_ended(client, app):
    ids = _seed_chapter_one(app)
    chapter_two_id = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id']}).get_json()['session_id']

    assert client.post(f"/api/sessions/{ids['session_id']}/archive").status_code == 200
    restored = client.post(f"/api/sessions/{ids['session_id']}/restore")

    assert restored.status_code == 200
    assert restored.get_json()['session']['status'] == 'ended'
    assert _session_status(app, ids['session_id']) == 'ended'

    assert client.post(f'/api/sessions/{chapter_two_id}/archive').status_code == 200
    assert client.post(f"/api/sessions/{ids['session_id']}/archive").status_code == 200
    reopened = client.post(f"/api/sessions/{ids['session_id']}/restore")

    assert reopened.get_json()['session']['status'] == 'active'
    assert reopened.get_json()['session']['is_current'] is True


def test_campaign_archive_and_restore_keep_an_ended_chapter_ended(client, app):
    ids = _seed_chapter_one(app)
    chapter_two_id = client.post('/api/sessions/start', json={'campaign_id': ids['campaign_id']}).get_json()['session_id']

    assert client.post(f"/api/campaigns/{ids['campaign_id']}/archive").status_code == 200
    assert _session_status(app, ids['session_id']) == 'archived'
    assert _session_status(app, chapter_two_id) == 'archived'
    assert client.post(f"/api/campaigns/{ids['campaign_id']}/restore").status_code == 200

    assert _session_status(app, ids['session_id']) == 'ended'
    assert _session_status(app, chapter_two_id) == 'active'
    assert client.patch(
        f"/api/sessions/{ids['session_id']}/content-settings",
        json={'content_rating': 'mature'},
    ).get_json()['details'] == {'current_session_id': chapter_two_id}


def test_play_now_replay_opens_the_current_chapter(client, app):
    first = client.post('/api/onboarding/play-now', json={'character_id': 'mara-fen'})
    assert first.status_code == 201
    chapter_one_id = first.get_json()['session_id']
    campaign_id = first.get_json()['campaign_id']
    chapter_two_id = client.post('/api/sessions/start', json={'campaign_id': campaign_id}).get_json()['session_id']

    replay = client.post('/api/onboarding/play-now', json={'character_id': 'mara-fen'})

    assert replay.status_code == 200
    assert replay.get_json()['session_id'] == chapter_two_id
    assert replay.get_json()['idempotent_replay'] is True
    with app.app_context():
        chapter_one = db.session.get(Session, chapter_one_id)
        chapter_two = db.session.get(Session, chapter_two_id)
        assert chapter_one.client_session_id.startswith('play-now-')
        assert chapter_two.client_session_id is None

    assert client.post(f'/api/sessions/{chapter_two_id}/archive').status_code == 200
    resumed = client.post('/api/onboarding/play-now', json={'character_id': 'mara-fen'})

    assert resumed.status_code == 201
    chapter_three_id = resumed.get_json()['session_id']
    assert chapter_three_id not in {chapter_one_id, chapter_two_id}
    assert resumed.get_json()['session']['is_current'] is True
    assert resumed.get_json()['session']['chapter_number'] == 3
    assert _snapshot(app, chapter_three_id)['chapter']['previousSessionId'] == chapter_one_id


def _player_view(table, session_id: int) -> dict:  # noqa: F811
    response = table.client.get(f"/api/sessions/{session_id}/state?view=player&player_id={table.ids['player_id']}")
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _start_next_chapter(table, continuity: str = 'continue') -> int:  # noqa: F811
    response = table.client.post('/api/sessions/start', json={'campaign_id': table.ids['campaign_id'], 'continuity': continuity})
    assert response.status_code == 201, response.get_json()
    return response.get_json()['session_id']


def test_a_continued_play_now_chapter_keeps_the_day_and_the_shops(table):  # noqa: F811
    kit = table.buy('merchant_mara_counter', 'merchant_mara_counter_1', "a Healer's Kit")
    assert (kit.gp, kit.items["Healer's Kit"]) == (-5, 1)
    table.send(
        'Arden Vale takes a short rest.',
        {'kind': 'rest', 'source': 'scene_panel', 'rest_type': 'short_rest', 'hit_dice': 0},
    )
    before = _player_view(table, table.ids['session_id'])['state_snapshot']
    assert before['clock']['label'] == 'Day 1 · Night'

    chapter_two_id = _start_next_chapter(table)

    after = _player_view(table, chapter_two_id)['state_snapshot']
    assert after['clock'] == before['clock']
    trade = {merchant['id']: merchant for merchant in after['currentScene']['merchants']}
    assert trade == {merchant['id']: merchant for merchant in before['currentScene']['merchants']}
    stock = {entry['stockId']: (entry['name'], entry['quantity']) for entry in trade['merchant_mara_counter']['stock']}
    assert stock['merchant_mara_counter_0'] == ('Potion of Healing', 3)
    assert stock['merchant_mara_counter_1'] == ("Healer's Kit", 0)


def test_a_fresh_play_now_chapter_starts_the_day_and_the_shops_again(table):  # noqa: F811
    table.buy('merchant_mara_counter', 'merchant_mara_counter_1', "a Healer's Kit")
    table.send(
        'Arden Vale takes a short rest.',
        {'kind': 'rest', 'source': 'scene_panel', 'rest_type': 'short_rest', 'hit_dice': 0},
    )

    fresh = _player_view(table, _start_next_chapter(table, 'fresh'))['state_snapshot']

    assert fresh['clock']['label'] == 'Day 1 · Evening'
    trade = {merchant['id']: merchant for merchant in fresh['currentScene']['merchants']}
    stock = {entry['stockId']: entry['quantity'] for entry in trade['merchant_mara_counter']['stock']}
    assert stock['merchant_mara_counter_1'] == 1


def test_the_next_play_now_chapter_opens_with_prose_and_never_the_rolling_summary(table):  # noqa: F811
    table.act('inn_caravan_ledger', 'inspect', 'Caravan Ledger')
    workspace = table.client.get(f"/api/campaigns/{table.ids['campaign_id']}/workspace").get_json()
    chapter_one = next(item for item in workspace['sessions'] if item['session_id'] == table.ids['session_id'])
    assert chapter_one['continue_line'].startswith('The Lantern Post Inn — ')
    assert chapter_one['summary_excerpt'] == chapter_one['continue_line']

    chapter_two_id = _start_next_chapter(table)

    log = table.client.get(f'/api/sessions/{chapter_two_id}/log').get_json()['entries']
    assert log[0]['message'] == f"**Chapter 2 begins.**\n\nPreviously: {chapter_one['continue_line']}"
    with table.app.app_context():
        rolling_summary = SessionState.query.filter_by(session_id=chapter_two_id).one().rolling_summary
    first_line, *turn_lines = rolling_summary.splitlines()
    assert ROLLING_SUMMARY_TURN_LINE.match(turn_lines[0])
    # No player-facing read of either chapter carries the narrator's rolling summary.
    reads = [
        log,
        table.client.get(f"/api/campaigns/{table.ids['campaign_id']}/workspace").get_json(),
        _player_view(table, chapter_two_id),
        _player_view(table, table.ids['session_id']),
        table.client.get(f"/api/sessions/{chapter_two_id}/state").get_json(),
        table.client.get(f"/api/sessions/{table.ids['session_id']}/recap").get_json(),
        table.client.get(f"/api/sessions/{table.ids['session_id']}/chronicle").get_data(as_text=True),
    ]
    for read in map(str, reads):
        assert first_line not in read
        assert ROLLING_SUMMARY_TURN_LINE.search(read) is None, read
