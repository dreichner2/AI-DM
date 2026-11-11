from __future__ import annotations

import importlib
import json

from aidm_server.auth import generate_account_token, hash_secret
from aidm_server.database import db
from aidm_server.models import (
    Account,
    AccountWorkspaceMembership,
    Campaign,
    DmTurn,
    OperatorActionAudit,
    Player,
    Session,
    SessionLogEntry,
    SessionState,
    World,
)
from aidm_server.services.session_import import RESTORED_MID_SCENE_MESSAGE
from aidm_server.services.session_lifecycle import session_playability_error
from tests.helpers import seed_world_campaign_player_session

SAVED_WORLD = {
    'schemaVersion': 1,
    'currentScene': {'locationId': 'ash_gate', 'name': 'Ash Gate', 'combatState': 'none'},
    'flags': {'gate_opened': True},
    'quests': [{'id': 'q_relic', 'title': 'Find the relic', 'status': 'active'}],
    'knownNpcs': [{'id': 'npc_bram', 'name': 'Bram', 'disposition': 'friendly'}],
    'combat': {'status': 'none', 'round': 1, 'participants': [], 'battlefield': {}, 'flags': {}},
    'playerCharacters': [{'id': 'player_1', 'inventory': {'items': [], 'currency': {'gp': 100}}}],
}
LATER_WORLD = {
    'schemaVersion': 1,
    'currentScene': {'locationId': 'black_mire', 'name': 'Black Mire', 'combatState': 'none'},
    'flags': {'gate_opened': True, 'mire_flooded': True},
    'quests': [{'id': 'q_relic', 'title': 'Find the relic', 'status': 'failed'}],
}


def _seed_saved_chapter(app) -> dict:
    """Chapter 1 with the saved world; the player's row holds 10 gold."""

    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        session_obj.state_snapshot = json.dumps(SAVED_WORLD)
        db.session.add(
            SessionState(
                session_id=ids['session_id'],
                current_location='Ash Gate',
                current_quest='Find the relic',
                rolling_summary='The party opened the Ash Gate.',
            )
        )
        db.session.add(SessionLogEntry(session_id=ids['session_id'], message='The gate opens.', entry_type='dm'))
        player = db.session.get(Player, ids['player_id'])
        player.stats = json.dumps({'gold': 10, 'current_hp': 4, 'max_hp': 20, 'xp': 300})
        player.inventory = json.dumps([{'name': 'Rope', 'quantity': 1}])
        db.session.commit()
    return ids


def _saved_document(client, app, ids) -> dict:
    """Export chapter 1, then give the save the 100 gold of the R2 reproduction."""

    document = client.get(f"/api/sessions/{ids['session_id']}/export").get_json()
    character = document['characters'][0]
    character['stats'] = {**character['stats'], 'gold': 100, 'current_hp': 18, 'xp': 900}
    character['level'] = 5
    character['inventory'] = [{'name': 'Relic Blade', 'quantity': 1}]
    return document


def _player_stats(app, player_id: int) -> dict:
    with app.app_context():
        return json.loads(db.session.get(Player, player_id).stats)


def _play_on_to_the_later_world(app, ids) -> None:
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        session_obj.state_snapshot = json.dumps(LATER_WORLD)
        db.session.commit()


def test_archive_import_is_ended_unplayable_and_touches_no_character(client, app):
    ids = _seed_saved_chapter(app)
    document = _saved_document(client, app, ids)

    response = client.post('/api/sessions/import', json={**document, 'mode': 'archive'})

    assert response.status_code == 201
    payload = response.get_json()
    archive = payload['session']
    assert payload['mode'] == 'archive'
    assert archive['status'] == 'ended'
    assert archive['imported'] is True
    assert archive['is_current'] is False
    assert archive['display_name'].startswith('Archive — ')
    assert payload['restored_player_ids'] == []
    assert _player_stats(app, ids['player_id'])['gold'] == 10

    state = client.get(f"/api/sessions/{payload['session_id']}/state").get_json()
    log = client.get(f"/api/sessions/{payload['session_id']}/log").get_json()['entries']
    assert state['state_snapshot']['playerCharacters'][0]['inventory']['currency']['gp'] == 100
    # The narrator's memory is imported but never read back to the table.
    assert state['rolling_summary'] == ''
    assert [entry['message'] for entry in log] == ['The gate opens.']

    with app.app_context():
        assert SessionState.query.filter_by(session_id=payload['session_id']).one().rolling_summary == (
            'The party opened the Ash Gate.'
        )
        archive_row = db.session.get(Session, payload['session_id'])
        assert session_playability_error(archive_row).error_code == 'session_ended'
    blocked = client.patch(
        f"/api/sessions/{payload['session_id']}/content-settings",
        json={'content_rating': 'mature'},
    )
    assert blocked.status_code == 409
    assert blocked.get_json()['error_code'] == 'session_ended'
    workspace = client.get(f"/api/campaigns/{ids['campaign_id']}/workspace").get_json()
    current = [item['session_id'] for item in workspace['sessions'] if item['is_current']]
    assert current == [ids['session_id']]


def test_import_mode_defaults_to_archive_and_rejects_unknown_values(client, app):
    ids = _seed_saved_chapter(app)
    document = client.get(f"/api/sessions/{ids['session_id']}/export").get_json()

    default_mode = client.post('/api/sessions/import', json=document)
    unknown_mode = client.post('/api/sessions/import', json={**document, 'mode': 'merge'})
    bad_flag = client.post('/api/sessions/import', json={**document, 'restore_characters': 'yes'})
    future_save = client.post('/api/sessions/import', json={**document, 'version': 3})

    assert default_mode.status_code == 201
    assert default_mode.get_json()['mode'] == 'archive'
    assert unknown_mode.status_code == 400
    assert bad_flag.status_code == 400
    assert future_save.status_code == 400


def test_restore_starts_a_new_current_chapter_from_the_saved_world(client, app):
    ids = _seed_saved_chapter(app)
    document = _saved_document(client, app, ids)
    _play_on_to_the_later_world(app, ids)

    response = client.post('/api/sessions/import', json={**document, 'mode': 'restore'})

    assert response.status_code == 201
    payload = response.get_json()
    restored = payload['session']
    assert payload['mode'] == 'restore'
    assert restored['status'] == 'active'
    assert restored['is_current'] is True
    assert restored['chapter_number'] == 2
    assert restored['display_name'] == 'Chapter 2'
    assert restored['imported'] is False
    assert restored['scene_title'] == 'Ash Gate'

    state = client.get(f"/api/sessions/{payload['session_id']}/state").get_json()
    snapshot = state['state_snapshot']
    assert snapshot['flags'] == {'gate_opened': True}
    assert snapshot['quests'][0]['status'] == 'active'
    assert snapshot['knownNpcs'][0]['name'] == 'Bram'
    assert snapshot['chapter']['previousSessionId'] == ids['session_id']
    assert snapshot['chapter']['restoredFrom']['sessionId'] == ids['session_id']
    assert 'imported' not in snapshot
    assert 'playerCharacters' not in snapshot
    assert state['rolling_summary'] == ''
    assert state['current_location'] == 'Ash Gate'
    with app.app_context():
        assert SessionState.query.filter_by(session_id=payload['session_id']).one().rolling_summary == (
            'The party opened the Ash Gate.'
        )

    previous = client.get(f"/api/sessions/{ids['session_id']}/state").get_json()
    assert previous['status'] == 'ended'
    log = client.get(f"/api/sessions/{payload['session_id']}/log").get_json()['entries']
    assert log[0]['message'].startswith('**Chapter 2 begins.**')
    assert RESTORED_MID_SCENE_MESSAGE not in [entry['message'] for entry in log]


def test_a_restored_chapter_recalls_the_save_in_prose_never_its_rolling_summary(client, app):
    ids = _seed_saved_chapter(app)
    document = _saved_document(client, app, ids)
    assert (document['session']['continue_line'], document['state_snapshot'].get('recap')) == (None, None)

    def restored_welcome(save: dict) -> str:
        response = client.post('/api/sessions/import', json={**save, 'mode': 'restore'})
        assert response.status_code == 201, response.get_json()
        return client.get(f"/api/sessions/{response.get_json()['session_id']}/log").get_json()['entries'][0]['message']

    # Neither a saved recap nor narration: no Previously line, though the save holds a rolling summary.
    assert document['session_state']['rolling_summary'] == 'The party opened the Ash Gate.'
    assert restored_welcome(document) == '**Chapter 2 begins.**'
    narrated = {**document, 'session': {**document['session'], 'continue_line': 'Ash Gate — The gate opens.'}}
    assert restored_welcome(narrated) == '**Chapter 3 begins.**\n\nPreviously: Ash Gate — The gate opens.'
    recapped = {**narrated, 'state_snapshot': {**narrated['state_snapshot'], 'recap': 'The party forced the Ash Gate.'}}
    assert restored_welcome(recapped) == '**Chapter 4 begins.**\n\nPreviously: The party forced the Ash Gate.'


def test_restore_keeps_characters_unless_asked_and_then_restores_their_saved_state(client, app):
    ids = _seed_saved_chapter(app)
    document = _saved_document(client, app, ids)

    kept = client.post('/api/sessions/import', json={**document, 'mode': 'restore'})
    assert kept.status_code == 201
    assert kept.get_json()['restored_player_ids'] == []
    assert _player_stats(app, ids['player_id'])['gold'] == 10

    restored = client.post(
        '/api/sessions/import',
        json={**document, 'mode': 'restore', 'restore_characters': True},
    )

    assert restored.status_code == 201
    assert restored.get_json()['restored_player_ids'] == [ids['player_id']]
    stats = _player_stats(app, ids['player_id'])
    assert stats['gold'] == 100
    assert stats['current_hp'] == 18
    assert stats['xp'] == 900
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert player.level == 5
        assert [item['name'] for item in json.loads(player.inventory)] == ['Relic Blade']
        audit = OperatorActionAudit.query.filter_by(action='session.restore_characters').one()
        assert json.loads(audit.details_json)['playerIds'] == [ids['player_id']]


def test_restore_matches_characters_by_id_then_by_name(client, app):
    ids = _seed_saved_chapter(app)
    document = _saved_document(client, app, ids)
    with app.app_context():
        replacement = Player(
            campaign_id=ids['campaign_id'],
            name='Bob',
            character_name='Mira Vale',
            race='Human',
            class_='Fighter',
            level=1,
            stats=json.dumps({'gold': 1}),
        )
        db.session.add(replacement)
        db.session.commit()
        replacement_id = replacement.player_id
    by_name = {**document['characters'][0], 'player_id': 98765, 'character_name': '  mira  VALE ', 'stats': {'gold': 55}}
    document['characters'].append(by_name)

    response = client.post('/api/sessions/import', json={**document, 'mode': 'restore', 'restore_characters': True})

    assert response.status_code == 201
    assert response.get_json()['restored_player_ids'] == sorted([ids['player_id'], replacement_id])
    assert _player_stats(app, ids['player_id'])['gold'] == 100
    assert _player_stats(app, replacement_id)['gold'] == 55


def test_restore_is_refused_for_another_campaign_and_for_legacy_files(client, app):
    ids = _seed_saved_chapter(app)
    document = client.get(f"/api/sessions/{ids['session_id']}/export").get_json()
    with app.app_context():
        other = Campaign(title='Other Campaign', world_id=db.session.get(Campaign, ids['campaign_id']).world_id)
        db.session.add(other)
        db.session.commit()
        other_id = other.campaign_id

    other_campaign = client.post(
        '/api/sessions/import',
        json={**document, 'mode': 'restore', 'campaign_id': other_id},
    )
    legacy = client.post(
        '/api/sessions/import',
        json={'campaign_id': ids['campaign_id'], 'selectedSession': {'state_snapshot': SAVED_WORLD}, 'mode': 'restore'},
    )

    for response in (other_campaign, legacy):
        assert response.status_code == 409
        assert response.get_json()['error_code'] == 'save_not_restorable'
        assert response.get_json()['error']
    assert 'different campaign' in other_campaign.get_json()['error']
    with app.app_context():
        assert Session.query.filter_by(campaign_id=ids['campaign_id']).count() == 1


def test_restore_adds_the_mid_scene_notice_and_drops_the_interrupted_fight(client, app):
    ids = _seed_saved_chapter(app)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        session_obj.state_snapshot = json.dumps(
            {
                **SAVED_WORLD,
                'currentScene': {**SAVED_WORLD['currentScene'], 'combatState': 'active'},
                'combat': {**SAVED_WORLD['combat'], 'status': 'active', 'participants': [{'id': 'enemy_wolf'}]},
            }
        )
        db.session.add(
            DmTurn(
                session_id=ids['session_id'],
                campaign_id=ids['campaign_id'],
                player_id=ids['player_id'],
                player_input='I leap the gap.',
                status='completed',
                outcome_status='deferred',
            )
        )
        db.session.commit()
    document = client.get(f"/api/sessions/{ids['session_id']}/export").get_json()
    assert document['unsettled'] == {'pending_roll': True, 'active_combat': True}

    unsettled = client.post('/api/sessions/import', json={**document, 'mode': 'restore'})
    assert unsettled.status_code == 409
    assert unsettled.get_json()['error_code'] == 'chapter_unsettled'

    with app.app_context():
        DmTurn.query.filter_by(session_id=ids['session_id']).update({'outcome_status': 'resolved'})
        db.session.commit()
    _play_on_to_the_later_world(app, ids)
    response = client.post('/api/sessions/import', json={**document, 'mode': 'restore'})

    assert response.status_code == 201
    new_session_id = response.get_json()['session_id']
    snapshot = client.get(f'/api/sessions/{new_session_id}/state').get_json()['state_snapshot']
    log = client.get(f'/api/sessions/{new_session_id}/log').get_json()['entries']
    assert snapshot['combat']['status'] == 'none'
    assert snapshot['combat']['participants'] == []
    assert snapshot['currentScene']['combatState'] == 'none'
    assert [entry['message'] for entry in log][-1] == RESTORED_MID_SCENE_MESSAGE
    assert log[-1]['entry_type'] == 'system'
    with app.app_context():
        assert DmTurn.query.filter_by(session_id=new_session_id).count() == 0


def _build_auth_app(tmp_path, monkeypatch):
    monkeypatch.setenv('AIDM_DATABASE_URI', f"sqlite:///{tmp_path / 'saves_auth.db'}")
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'true')
    monkeypatch.setenv('AIDM_AUTH_REQUIRED', 'true')
    monkeypatch.setenv('AIDM_ENV', 'test')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('AIDM_SOCKETIO_ASYNC_MODE', 'threading')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')
    monkeypatch.setenv('AIDM_RATE_LIMIT_MAX_API_REQUESTS', '1000')
    monkeypatch.setenv('AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES', '1000')

    import aidm_server.main as main_module

    app = importlib.reload(main_module).create_app()
    with app.app_context():
        db.create_all()
    return app


def _account(app, *, username: str, role: str) -> tuple[int, dict]:
    token = generate_account_token()
    with app.app_context():
        account = Account(
            username=username,
            first_name=username.title(),
            last_name='Tester',
            password_hash='test-password-hash',
            account_token_hash=hash_secret(token),
        )
        db.session.add(account)
        db.session.flush()
        db.session.add(AccountWorkspaceMembership(account_id=account.account_id, workspace_id='owner', role=role))
        db.session.commit()
        account_id = account.account_id
    return account_id, {'Authorization': f'Bearer {token}', 'X-AIDM-Workspace-Id': 'owner'}


def test_export_gives_players_their_own_character_and_the_projected_snapshot(tmp_path, monkeypatch):
    app = _build_auth_app(tmp_path, monkeypatch)
    client = app.test_client()
    player_account_id, player_headers = _account(app, username='save_player', role='player')
    _admin_account_id, admin_headers = _account(app, username='save_admin', role='admin')
    with app.app_context():
        world = World(name='Save World')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Save Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(campaign)
        db.session.flush()
        own = Player(
            campaign_id=campaign.campaign_id,
            workspace_id='owner',
            account_id=player_account_id,
            name='Own',
            character_name='Own Hero',
            level=2,
            inventory=json.dumps([{'name': 'OWN_ITEM', 'quantity': 1}]),
        )
        peer = Player(
            campaign_id=campaign.campaign_id,
            workspace_id='owner',
            name='Peer',
            character_name='Peer Hero',
            level=2,
            inventory=json.dumps([{'name': 'PEER_PRIVATE_ITEM', 'quantity': 1}]),
        )
        db.session.add_all([own, peer])
        db.session.flush()
        session_obj = Session(
            campaign_id=campaign.campaign_id,
            state_snapshot=json.dumps(
                {
                    **SAVED_WORLD,
                    'stateChangeLedger': [{'id': 'DM_ONLY_LEDGER'}],
                    'campaignPack': {'packId': 'save_pack', 'catalog': {'npcs': [{'id': 'DM_ONLY_NPC'}]}},
                }
            ),
        )
        db.session.add(session_obj)
        db.session.flush()
        db.session.add(SessionState(session_id=session_obj.session_id, rolling_summary='T4 | P1: NARRATOR_MEMORY'))
        db.session.commit()
        ids = {
            'campaign_id': campaign.campaign_id,
            'session_id': session_obj.session_id,
            'own_id': own.player_id,
            'peer_id': peer.player_id,
        }

    player_export = client.get(f"/api/sessions/{ids['session_id']}/export", headers=player_headers)
    own_selected_export = client.get(
        f"/api/sessions/{ids['session_id']}/export?player_id={ids['own_id']}", headers=player_headers
    )
    peer_selected_export = client.get(
        f"/api/sessions/{ids['session_id']}/export?player_id={ids['peer_id']}", headers=player_headers
    )
    player_state = client.get(f"/api/sessions/{ids['session_id']}/state", headers=player_headers)
    admin_export = client.get(f"/api/sessions/{ids['session_id']}/export", headers=admin_headers)
    player_restore = client.post(
        '/api/sessions/import',
        headers=player_headers,
        json={**player_export.get_json(), 'mode': 'restore'},
    )

    assert player_export.status_code == 200
    assert own_selected_export.status_code == 200
    assert peer_selected_export.status_code == 404
    assert peer_selected_export.get_json()['error_code'] == 'player_not_found'
    document = player_export.get_json()
    assert set(document) == {
        'format',
        'version',
        'exported_at',
        'campaign',
        'session',
        'state_snapshot',
        'session_state',
        'log',
        'characters',
        'unsettled',
    }
    assert document['campaign'] == {'id': ids['campaign_id'], 'title': 'Save Campaign', 'pack_id': 'save_pack'}
    assert [character['player_id'] for character in document['characters']] == [ids['own_id']]
    assert document['characters'][0]['inventory'][0]['name'] == 'OWN_ITEM'
    assert document['state_snapshot'] == player_state.get_json()['state_snapshot']
    assert 'state_snapshot' not in document['session']
    assert 'PEER_PRIVATE_ITEM' not in json.dumps(document)
    assert 'DM_ONLY_LEDGER' not in json.dumps(document)
    assert 'DM_ONLY_NPC' not in json.dumps(document)
    # The narrator's memory stays out of a player's save and state; only an operator's save keeps it.
    assert (document['session_state']['rolling_summary'], player_state.get_json()['rolling_summary']) == ('', '')
    assert 'NARRATOR_MEMORY' not in json.dumps(document)

    assert admin_export.status_code == 200
    admin_document = admin_export.get_json()
    assert [character['player_id'] for character in admin_document['characters']] == [ids['own_id'], ids['peer_id']]
    assert 'DM_ONLY_LEDGER' in json.dumps(admin_document['state_snapshot'])
    assert 'DM_ONLY_NPC' in json.dumps(admin_document['state_snapshot'])
    assert admin_document['session_state']['rolling_summary'] == 'T4 | P1: NARRATOR_MEMORY'

    assert player_restore.status_code == 409
    assert player_restore.get_json()['error_code'] == 'save_not_restorable'
