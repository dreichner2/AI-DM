"""What the Story screen reads: the player's-eyes view, the Continue card's line and a fresh Play Now."""

from __future__ import annotations

import importlib
import json

import pytest

from aidm_server.auth import generate_account_token, hash_secret
from aidm_server.database import db
from aidm_server.models import (
    Account,
    AccountWorkspaceMembership,
    Campaign,
    DmTurn,
    Player,
    Session,
    SessionState,
    World,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.response_dtos import session_state_payload
from aidm_server.services.campaign_pack_progress import PROGRESS_CHANGED_EVENT
from aidm_server.services.play_now import PlayNowOnboardingError, ensure_play_now_adventure, play_now_fresh_flag
from aidm_server.turn_events import DM_RESPONSE_EVENT, record_turn_event


def _play_now(client, **body) -> dict:
    response = client.post('/api/onboarding/play-now', json=body)
    assert response.status_code in (200, 201), response.get_json()
    return response.get_json()


def _narrate(app, ids: dict, message: str, *, player_id: int | None = None, metadata: dict | None = None) -> None:
    """One Dungeon Master turn, as the turn engine records it."""

    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=player_id or ids['player_id'],
            player_input='I look around.',
            dm_output=message,
            status='completed',
            outcome_status='resolved',
            llm_provider='fallback',
            llm_model='deterministic-v1',
        )
        db.session.add(turn)
        db.session.flush()
        record_turn_event(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            turn_id=turn.turn_id,
            player_id=turn.player_id,
            event_type=DM_RESPONSE_EVENT,
            payload={'message': message, 'metadata': {'turn_id': turn.turn_id, **(metadata or {})}},
        )
        db.session.commit()


def _edit_snapshot(app, session_id: int, edit) -> None:
    with app.app_context():
        session_obj = db.session.get(Session, session_id)
        snapshot = safe_json_loads(session_obj.state_snapshot, {})
        edit(snapshot)
        session_obj.state_snapshot = safe_json_dumps(snapshot, {})
        db.session.commit()


def _add_enemy(snapshot: dict) -> None:
    hero = snapshot['playerCharacters'][0]
    snapshot['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {'id': hero['id'], 'playerId': hero['playerId'], 'name': hero['name'], 'team': 'player', 'hp': {'current': 8, 'max': 8}},
            {'id': 'enemy_cutthroat', 'name': 'Ashmarked Cutthroat', 'team': 'enemy', 'hp': {'current': 5, 'max': 11}},
        ],
        'battlefield': {},
        'flags': {},
    }


def test_an_owner_reads_the_story_through_one_heros_eyes(client, app):
    ids = _play_now(client, character_id='liora-quill')
    with app.app_context():
        companion = Player(campaign_id=ids['campaign_id'], workspace_id=ids['workspace_id'], name='Sam', character_name='Brann')
        db.session.add(companion)
        db.session.commit()
        companion_id = companion.player_id
    _edit_snapshot(app, ids['session_id'], _add_enemy)
    _narrate(app, ids, 'The ledger is ruined.', metadata={'state_pipeline': {'trace': 'OWN_PIPELINE'}})
    _narrate(app, ids, 'Brann waits.', player_id=companion_id, metadata={'state_pipeline': {'trace': 'DM_ONLY_PIPELINE'}})
    with app.app_context():
        record_turn_event(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            event_type=PROGRESS_CHANGED_EVENT,
            payload={'type': PROGRESS_CHANGED_EVENT, 'reason': 'DM_ONLY_PROGRESS_REASON'},
        )
        db.session.commit()
    query = f"view=player&player_id={ids['player_id']}"

    state = client.get(f"/api/sessions/{ids['session_id']}/state?{query}").get_json()
    log = client.get(f"/api/sessions/{ids['session_id']}/log?{query}").get_json()
    events = client.get(f"/api/sessions/{ids['session_id']}/events?{query}").get_json()

    snapshot = state['state_snapshot']
    text = json.dumps(state)
    assert 'inn_wagon_strongbox' not in text
    assert '"hidden": true' not in text
    assert '"secret"' not in text and 'silver button' not in text
    assert 'gmNotes' not in text
    enemy = next(participant for participant in snapshot['combat']['participants'] if participant['team'] == 'enemy')
    assert 'hp' not in enemy and enemy['health'] == 'Wounded'
    hero = next(record for record in snapshot['playerCharacters'] if record['playerId'] == ids['player_id'])
    assert hero['inventory']['items'] and hero['spellResources']
    assert [bundle['playerId'] for bundle in snapshot['combat']['legalActions']] == [ids['player_id']]
    # Rules still judged the enemy by its real hit points: it is standing and in the turn order.
    assert 'enemy_cutthroat' in {entry['id'] for entry in snapshot['combat']['turnOrder']}
    pipelines = {entry['message']: entry['metadata'].get('state_pipeline') for entry in log['entries'] if entry['entry_type'] == 'dm'}
    assert pipelines == {'DM: The ledger is ruined.': {'trace': 'OWN_PIPELINE'}, 'DM: Brann waits.': None}
    assert PROGRESS_CHANGED_EVENT not in {event['event_type'] for event in events['events']}

    full_log = client.get(f"/api/sessions/{ids['session_id']}/log").get_json()
    full_events = client.get(f"/api/sessions/{ids['session_id']}/events").get_json()
    assert 'DM_ONLY_PIPELINE' in json.dumps(full_log)
    assert PROGRESS_CHANGED_EVENT in {event['event_type'] for event in full_events['events']}


def test_without_a_view_the_owner_still_reads_the_full_state(client, app):
    ids = _play_now(client)

    response = client.get(f"/api/sessions/{ids['session_id']}/state")

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        expected = session_state_payload(
            session_obj,
            SessionState.query.filter_by(session_id=ids['session_id']).first(),
            include_hidden_state=True,
        )
    assert response.get_json() == json.loads(json.dumps(expected))
    scene = response.get_json()['state_snapshot']['currentScene']
    assert 'inn_wagon_strongbox' in {entry['id'] for entry in scene['interactables']}


def test_an_unknown_view_or_hero_is_refused(client, app):
    ids = _play_now(client)
    base = f"/api/sessions/{ids['session_id']}/state"

    assert client.get(f'{base}?view=table').status_code == 400
    assert client.get(f'{base}?view=player&player_id=first').status_code == 400
    stranger = client.get(f'{base}?view=player&player_id=9999')
    assert (stranger.status_code, stranger.get_json()['error_code']) == (403, 'forbidden')
    assert client.get(f"/api/campaigns/{ids['campaign_id']}/workspace?view=player&player_id=9999").status_code == 403


def _auth_app(tmp_path, monkeypatch):
    monkeypatch.setenv('AIDM_DATABASE_URI', f"sqlite:///{tmp_path / 'story_auth.db'}")
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'true')
    monkeypatch.setenv('AIDM_AUTH_REQUIRED', 'true')
    monkeypatch.setenv('AIDM_ENV', 'test')
    monkeypatch.setenv('AIDM_SOCKETIO_ASYNC_MODE', 'threading')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')
    monkeypatch.setenv('AIDM_RATE_LIMIT_MAX_API_REQUESTS', '1000')
    import aidm_server.main as main_module

    app = importlib.reload(main_module).create_app()
    with app.app_context():
        db.create_all()
    return app


def _account(app, username: str, role: str) -> tuple[int, dict]:
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
        return account.account_id, {'Authorization': f'Bearer {token}', 'X-AIDM-Workspace-Id': 'owner'}


def test_a_player_may_view_only_as_a_hero_their_account_controls(tmp_path, monkeypatch):
    app = _auth_app(tmp_path, monkeypatch)
    client = app.test_client()
    mine_account, mine = _account(app, 'ada', 'player')
    other_account, _ = _account(app, 'bo', 'player')
    _admin_account, admin = _account(app, 'cy', 'admin')
    with app.app_context():
        world = World(workspace_id='owner', name='World', description='world')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(workspace_id='owner', title='Campaign', world_id=world.world_id)
        db.session.add(campaign)
        db.session.flush()
        heroes = [
            Player(workspace_id='owner', campaign_id=campaign.campaign_id, account_id=account_id, name=name, character_name=name)
            for account_id, name in ((mine_account, 'Ada'), (other_account, 'Bo'))
        ]
        session_obj = Session(campaign_id=campaign.campaign_id, state_snapshot='{}')
        db.session.add_all([*heroes, session_obj])
        db.session.commit()
        my_hero, their_hero, session_id = heroes[0].player_id, heroes[1].player_id, session_obj.session_id
    state = f'/api/sessions/{session_id}/state?view=player&player_id='

    assert client.get(f'{state}{my_hero}', headers=mine).status_code == 200
    refused = client.get(f'{state}{their_hero}', headers=mine)
    assert (refused.status_code, refused.get_json()['error_code']) == (403, 'forbidden')
    assert client.get(f'/api/sessions/{session_id}/log?view=player&player_id={their_hero}', headers=mine).status_code == 403
    assert client.get(f'{state}{their_hero}', headers=admin).status_code == 200


def test_the_continue_line_is_the_place_and_the_first_sentence_of_the_latest_narration(client, app):
    ids = _play_now(client)

    def continue_line() -> str | None:
        return client.get(f"/api/campaigns/{ids['campaign_id']}/workspace").get_json()['sessions'][0]['continue_line']

    assert continue_line() is None
    _narrate(app, ids, 'Rain hammers the shutters as the ledger falls open. Every name is gone.')
    assert continue_line() == 'The Lantern Post Inn — Rain hammers the shutters as the ledger falls open.'

    _narrate(app, ids, 'That can’t be done with the Wagon Bell.', metadata={'rules_rejection': {'reason': 'x'}})
    assert continue_line() == 'The Lantern Post Inn — Rain hammers the shutters as the ledger falls open.'

    _narrate(app, ids, 'The wagons creak ' + 'and groan in the wind ' * 12 + 'until dawn. The road waits.')
    line = continue_line()
    assert len(line) <= 160 and line.endswith('…')
    assert line.startswith('The Lantern Post Inn — The wagons creak and groan')
    with app.app_context():
        rolling_summary = SessionState.query.filter_by(session_id=ids['session_id']).first().rolling_summary
    assert rolling_summary not in line


def test_a_fresh_play_now_always_starts_a_new_adventure_but_a_repeat_does_not_make_two(client, app):
    first = _play_now(client)
    with app.app_context():
        fresh = ensure_play_now_adventure(fresh=True)
        db.session.commit()
        repeat = ensure_play_now_adventure(fresh=True)
        db.session.commit()

    assert fresh.status_code == 201
    assert fresh.payload['campaign_id'] != first['campaign_id']
    assert (repeat.status_code, repeat.payload['campaign_id']) == (200, fresh.payload['campaign_id'])

    _narrate(app, {key: fresh.payload[key] for key in ('session_id', 'campaign_id', 'player_id')}, 'You set out.')
    with app.app_context():
        after_play = ensure_play_now_adventure(fresh=True)
        db.session.commit()
        other_hero = ensure_play_now_adventure(fresh=True, character_id='mara-fen')
        db.session.commit()
    assert after_play.status_code == 201
    assert len({first['campaign_id'], fresh.payload['campaign_id'], after_play.payload['campaign_id']}) == 3
    assert other_hero.payload['campaign_id'] not in {fresh.payload['campaign_id'], after_play.payload['campaign_id']}
    assert _play_now(client)['campaign_id'] == first['campaign_id']


@pytest.mark.parametrize(('body', 'fresh'), [({}, False), ({'fresh': False}, False), ({'fresh': True}, True)])
def test_the_fresh_flag_is_a_boolean(body, fresh):
    assert play_now_fresh_flag(body) is fresh


def test_a_fresh_flag_that_is_not_a_boolean_is_refused():
    with pytest.raises(PlayNowOnboardingError, match='fresh must be true or false.'):
        play_now_fresh_flag({'fresh': 'yes'})


def test_the_play_now_endpoint_passes_the_fresh_flag_through(client, app):
    first = client.post('/api/onboarding/play-now', json={})
    again = client.post('/api/onboarding/play-now', json={})
    fresh = client.post('/api/onboarding/play-now', json={'fresh': True})
    refused = client.post('/api/onboarding/play-now', json={'fresh': 'yes'})

    assert first.status_code == 201
    assert again.get_json()['campaign_id'] == first.get_json()['campaign_id']
    assert fresh.status_code == 201
    assert fresh.get_json()['campaign_id'] != first.get_json()['campaign_id']
    assert refused.status_code == 400
    assert refused.get_json()['error'] == 'fresh must be true or false.'
