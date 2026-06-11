from __future__ import annotations

import importlib

from aidm_server.auth import generate_account_token, hash_secret, normalize_username
from aidm_server.database import db
from aidm_server.models import Account, AccountWorkspaceMembership, Campaign, Player, Workspace, World


def _build_account_runtime(tmp_path, monkeypatch):
    db_path = tmp_path / 'accounts.db'
    monkeypatch.setenv('AIDM_DATABASE_URI', f'sqlite:///{db_path}')
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'true')
    monkeypatch.setenv('AIDM_AUTH_REQUIRED', 'true')
    monkeypatch.setenv('AIDM_API_AUTH_TOKENS', 'owner-token,friend-token')
    monkeypatch.setenv('AIDM_API_AUTH_TOKEN_WORKSPACES', 'owner=owner-token,friend=friend-token')
    monkeypatch.setenv('AIDM_ENV', 'test')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('AIDM_SOCKETIO_ASYNC_MODE', 'threading')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')
    monkeypatch.setenv('AIDM_RATE_LIMIT_MAX_API_REQUESTS', '1000')
    monkeypatch.setenv('AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES', '1000')

    import aidm_server.main as main_module
    main_module = importlib.reload(main_module)
    app = main_module.create_app()
    with app.app_context():
        db.create_all()
    return app


def _login(
    client,
    *,
    username: str,
    first_name: str,
    last_name: str,
    workspace_token: str | None = None,
    password: str = '',
    intent: str | None = None,
):
    payload = {
        'username': username,
        'first_name': first_name,
        'last_name': last_name,
        'password': password,
    }
    if workspace_token is not None:
        payload['workspace_token'] = workspace_token
    if intent is not None:
        payload['intent'] = intent
    return client.post(
        '/api/accounts/login',
        json=payload,
    )


def _create_legacy_passwordless_account(app, *, username: str, first_name: str, last_name: str) -> str:
    token = generate_account_token()
    with app.app_context():
        account = Account(
            username=normalize_username(username),
            first_name=first_name,
            last_name=last_name,
            password_hash=None,
            account_token_hash=hash_secret(token),
        )
        db.session.add(account)
        db.session.commit()
    return token


def test_account_login_issues_session_token_and_uses_password_plus_workspace_token(tmp_path, monkeypatch):
    app = _build_account_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    login = _login(
        client,
        username='Danny',
        first_name='Danny',
        last_name='Reichner',
        password='secret',
    )

    assert login.status_code == 201
    payload = login.get_json()
    assert payload['account']['username'] == 'danny'
    session_token = payload['account_token']
    assert session_token
    assert payload['workspace_id'] is None
    assert payload['is_workspace_admin'] is False

    join_owner = client.post(
        '/api/accounts/workspace',
        headers={'Authorization': f"Bearer {session_token}"},
        json={'workspace_token': 'owner-token'},
    )
    assert join_owner.status_code == 200
    workspace_payload = join_owner.get_json()
    assert workspace_payload['workspace_id'] == 'owner'
    assert workspace_payload['is_workspace_admin'] is False

    account_headers = {
        'Authorization': f"Bearer {session_token}",
        'X-AIDM-Workspace-Token': 'owner-token',
    }
    worlds_response = client.post('/api/worlds', headers=account_headers, json={'name': 'Account World'})
    assert worlds_response.status_code == 201

    missing_workspace = client.get('/api/campaigns', headers={'Authorization': f"Bearer {session_token}"})
    assert missing_workspace.status_code == 401
    assert missing_workspace.get_json()['error_code'] == 'unauthorized'

    friend_login = _login(
        client,
        username='Danny',
        first_name='Danny',
        last_name='Reichner',
        password='secret',
    )
    assert friend_login.status_code == 200
    friend_token = friend_login.get_json()['account_token']
    join_friend = client.post(
        '/api/accounts/workspace',
        headers={'Authorization': f"Bearer {friend_token}"},
        json={'workspace_token': 'friend-token'},
    )
    assert join_friend.status_code == 200
    assert join_friend.get_json()['workspace_id'] == 'friend'

    saved_workspaces = client.get(
        '/api/accounts/workspaces',
        headers={'Authorization': f"Bearer {friend_token}"},
    )
    assert saved_workspaces.status_code == 200
    assert {
        workspace['workspace_id']
        for workspace in saved_workspaces.get_json()['workspaces']
    } == {'owner', 'friend'}

    select_owner = client.post(
        '/api/accounts/workspace/select',
        headers={'Authorization': f"Bearer {friend_token}"},
        json={'workspace_id': 'owner'},
    )
    assert select_owner.status_code == 200
    assert select_owner.get_json()['workspace_id'] == 'owner'

    saved_workspace_headers = {
        'Authorization': f"Bearer {friend_token}",
        'X-AIDM-Workspace-Id': 'owner',
    }
    assert client.get('/api/campaigns', headers=saved_workspace_headers).status_code == 200

    missing_workspace = client.post(
        '/api/accounts/workspace/select',
        headers={'Authorization': f"Bearer {friend_token}"},
        json={'workspace_id': 'unknown'},
    )
    assert missing_workspace.status_code == 403


def test_account_can_create_password_table_and_join_by_name_password(tmp_path, monkeypatch):
    app = _build_account_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    owner_login = _login(
        client,
        username='Danny',
        first_name='Danny',
        last_name='Reichner',
        password='secret',
        intent='signup',
    )
    assert owner_login.status_code == 201
    owner_token = owner_login.get_json()['account_token']

    create_table = client.post(
        '/api/accounts/workspaces',
        headers={'Authorization': f'Bearer {owner_token}'},
        json={
            'table_name': 'Friday Night',
            'access_mode': 'password',
            'table_password': 'table-secret',
        },
    )
    assert create_table.status_code == 201
    create_payload = create_table.get_json()
    assert create_payload['workspace_id'] == 'Friday_Night'
    assert create_payload['workspace_role'] == 'admin'
    assert create_payload['is_workspace_admin'] is True
    assert 'workspace_token' not in create_payload
    assert create_payload['workspaces'][0]['workspace_name'] == 'Friday Night'
    assert create_payload['workspaces'][0]['table_name'] == 'Friday Night'
    assert create_payload['workspaces'][0]['access_mode'] == 'password'

    duplicate = client.post(
        '/api/accounts/workspaces',
        headers={'Authorization': f'Bearer {owner_token}'},
        json={
            'table_name': 'friday night',
            'access_mode': 'password',
            'table_password': 'different-secret',
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.get_json()['error'] == 'table/ workspace name already in use'

    joiner_login = _login(
        client,
        username='Maya',
        first_name='Maya',
        last_name='Stone',
        password='maya-secret',
        intent='signup',
    )
    assert joiner_login.status_code == 201
    joiner_token = joiner_login.get_json()['account_token']

    wrong_password = client.post(
        '/api/accounts/workspace',
        headers={'Authorization': f'Bearer {joiner_token}'},
        json={
            'table_name': 'Friday Night',
            'table_password': 'wrong-secret',
        },
    )
    assert wrong_password.status_code == 401

    join_table = client.post(
        '/api/accounts/workspace',
        headers={'Authorization': f'Bearer {joiner_token}'},
        json={
            'table_name': 'Friday Night',
            'table_password': 'table-secret',
        },
    )
    assert join_table.status_code == 200
    join_payload = join_table.get_json()
    assert join_payload['workspace_id'] == 'Friday_Night'
    assert join_payload['workspace_role'] == 'player'
    assert join_payload['workspaces'][0]['workspace_name'] == 'Friday Night'

    saved_workspace_headers = {
        'Authorization': f'Bearer {joiner_token}',
        'X-AIDM-Workspace-Id': 'Friday_Night',
    }
    assert client.get('/api/campaigns', headers=saved_workspace_headers).status_code == 200

    with app.app_context():
        table_world = World(name='Friday World', workspace_id='Friday_Night')
        db.session.add(table_world)
        db.session.flush()
        table_campaign = Campaign(
            title='Friday Campaign',
            world_id=table_world.world_id,
            workspace_id='Friday_Night',
        )
        db.session.add(table_campaign)
        db.session.flush()
        table_player = Player(
            workspace_id='Friday_Night',
            campaign_id=table_campaign.campaign_id,
            name='Maya Stone',
            character_name='Maya',
        )
        db.session.add(table_player)
        db.session.commit()

    remove_saved_table = client.delete(
        '/api/accounts/workspaces/Friday_Night',
        headers={'Authorization': f'Bearer {joiner_token}'},
    )
    assert remove_saved_table.status_code == 200
    assert remove_saved_table.get_json()['workspace_action'] == 'removed'
    with app.app_context():
        assert Workspace.query.filter_by(workspace_id='Friday_Night').one()
        joiner = Account.query.filter_by(username='maya').one()
        assert AccountWorkspaceMembership.query.filter_by(
            account_id=joiner.account_id,
            workspace_id='Friday_Night',
        ).first() is None
        assert Campaign.query.filter_by(workspace_id='Friday_Night').count() == 1

    delete_table = client.delete(
        '/api/accounts/workspaces/Friday_Night',
        headers={'Authorization': f'Bearer {owner_token}'},
    )
    assert delete_table.status_code == 200
    assert delete_table.get_json()['workspace_action'] == 'deleted'
    with app.app_context():
        assert Workspace.query.filter_by(workspace_id='Friday_Night').first() is None
        assert AccountWorkspaceMembership.query.filter_by(workspace_id='Friday_Night').count() == 0
        assert World.query.filter_by(workspace_id='Friday_Night').count() == 0
        assert Campaign.query.filter_by(workspace_id='Friday_Night').count() == 0
        assert Player.query.filter_by(workspace_id='Friday_Night').count() == 0


def test_account_can_create_generated_token_table_and_token_is_one_time(tmp_path, monkeypatch):
    app = _build_account_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    owner_login = _login(
        client,
        username='Danny',
        first_name='Danny',
        last_name='Reichner',
        password='secret',
        intent='signup',
    )
    assert owner_login.status_code == 201
    owner_token = owner_login.get_json()['account_token']

    create_table = client.post(
        '/api/accounts/workspaces',
        headers={'Authorization': f'Bearer {owner_token}'},
        json={
            'table_name': 'Token Table',
            'access_mode': 'token',
        },
    )
    assert create_table.status_code == 201
    create_payload = create_table.get_json()
    table_token = create_payload['workspace_token']
    assert table_token
    assert create_payload['workspace_id'] == 'Token_Table'
    assert create_payload['workspaces'][0]['access_mode'] == 'token'

    with app.app_context():
        workspace = Workspace.query.filter_by(workspace_id='Token_Table').one()
        assert workspace.token_hash == hash_secret(table_token)
        assert workspace.password_hash is None
        assert workspace.token_hash != table_token

    account_snapshot = client.get(
        '/api/accounts/me',
        headers={'Authorization': f'Bearer {owner_token}'},
    )
    assert account_snapshot.status_code == 200
    assert 'workspace_token' not in account_snapshot.get_data(as_text=True)

    joiner_login = _login(
        client,
        username='Aidan',
        first_name='Aidan',
        last_name='Fernandez',
        password='aidan-secret',
        intent='signup',
    )
    assert joiner_login.status_code == 201
    joiner_token = joiner_login.get_json()['account_token']

    join_table = client.post(
        '/api/accounts/workspace',
        headers={'Authorization': f'Bearer {joiner_token}'},
        json={'workspace_token': table_token},
    )
    assert join_table.status_code == 200
    assert join_table.get_json()['workspace_id'] == 'Token_Table'

    token_headers = {
        'Authorization': f'Bearer {joiner_token}',
        'X-AIDM-Workspace-Token': table_token,
    }
    assert client.get('/api/campaigns', headers=token_headers).status_code == 200


def test_login_and_signup_intents_return_specific_username_errors(tmp_path, monkeypatch):
    app = _build_account_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    missing_login = _login(
        client,
        username='Missing',
        first_name='',
        last_name='',
        password='secret',
        intent='login',
    )
    assert missing_login.status_code == 404
    assert missing_login.get_json()['error_code'] == 'username_not_found'
    assert missing_login.get_json()['error'] == 'Username not found. Please sign up.'

    blank_password_signup = _login(
        client,
        username='Danny',
        first_name='Danny',
        last_name='Reichner',
        password='',
        intent='signup',
    )
    assert blank_password_signup.status_code == 400
    assert blank_password_signup.get_json()['error_code'] == 'validation_error'
    assert blank_password_signup.get_json()['error'] == 'Password is required.'

    signup = _login(
        client,
        username='Danny',
        first_name='Danny',
        last_name='Reichner',
        password='secret',
        intent='signup',
    )
    assert signup.status_code == 201

    taken_signup = _login(
        client,
        username='Danny',
        first_name='Daniel',
        last_name='Reichner',
        password='another-secret',
        intent='signup',
    )
    assert taken_signup.status_code == 409
    assert taken_signup.get_json()['error_code'] == 'username_taken'
    assert taken_signup.get_json()['error'] == 'Username is already taken. Please sign in.'

    existing_login = _login(
        client,
        username='Danny',
        first_name='',
        last_name='',
        password='secret',
        intent='login',
    )
    assert existing_login.status_code == 200


def test_existing_password_account_requires_password_even_with_saved_session(tmp_path, monkeypatch):
    app = _build_account_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    signup = _login(
        client,
        username='Danny',
        first_name='Danny',
        last_name='Reichner',
        password='secret',
        intent='signup',
    )
    assert signup.status_code == 201
    session_token = signup.get_json()['account_token']

    saved_token_without_password = client.post(
        '/api/accounts/login',
        headers={'Authorization': f'Bearer {session_token}'},
        json={
            'username': 'Danny',
            'first_name': '',
            'last_name': '',
            'password': '',
            'intent': 'login',
        },
    )
    assert saved_token_without_password.status_code == 401
    assert saved_token_without_password.get_json()['error_code'] == 'unauthorized'
    assert saved_token_without_password.get_json()['error'] == 'Invalid account password.'

    saved_token_with_password = client.post(
        '/api/accounts/login',
        headers={'Authorization': f'Bearer {session_token}'},
        json={
            'username': 'Danny',
            'first_name': '',
            'last_name': '',
            'password': 'secret',
            'intent': 'login',
        },
    )
    assert saved_token_with_password.status_code == 200
    assert saved_token_with_password.get_json()['account_token'] == session_token


def test_passwordless_account_requires_saved_session_or_explicit_password_setup(tmp_path, monkeypatch):
    app = _build_account_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    session_token = _create_legacy_passwordless_account(
        app,
        username='Danny',
        first_name='Danny',
        last_name='Reichner',
    )

    stale_token = 'stale-account-token'
    stale_login = client.post(
        '/api/accounts/login',
        headers={'Authorization': f'Bearer {stale_token}'},
        json={
            'username': 'Danny',
            'first_name': 'Danny',
            'last_name': 'Reichner',
            'password': '',
        },
    )
    assert stale_login.status_code == 401
    assert stale_login.get_json()['error_code'] == 'legacy_password_setup_required'
    assert stale_login.get_json()['error'] == 'Passwords are required now. Please set one now.'

    saved_token_without_password = client.post(
        '/api/accounts/login',
        headers={'Authorization': f'Bearer {session_token}'},
        json={
            'username': 'Danny',
            'first_name': 'Danny',
            'last_name': 'Reichner',
            'password': '',
        },
    )
    assert saved_token_without_password.status_code == 401
    assert saved_token_without_password.get_json()['error_code'] == 'legacy_password_setup_required'

    saved_session_login = client.post(
        '/api/accounts/login',
        headers={'Authorization': f'Bearer {session_token}'},
        json={
            'username': 'Danny',
            'first_name': 'Danny',
            'last_name': 'Reichner',
            'password': 'secret',
        },
    )
    assert saved_session_login.status_code == 200
    assert saved_session_login.get_json()['account_token'] == session_token

    wrong_password_login = _login(
        client,
        username='Danny',
        first_name='Danny',
        last_name='Reichner',
        password='wrong',
    )
    assert wrong_password_login.status_code == 401

    password_login = _login(
        client,
        username='Danny',
        first_name='Danny',
        last_name='Reichner',
        password='secret',
    )
    assert password_login.status_code == 200
    replacement_token = password_login.get_json()['account_token']
    assert replacement_token
    assert replacement_token != session_token

    join_owner = client.post(
        '/api/accounts/workspace',
        headers={'Authorization': f'Bearer {replacement_token}'},
        json={'workspace_token': 'owner-token'},
    )
    assert join_owner.status_code == 200
    assert join_owner.get_json()['workspace_id'] == 'owner'


def test_passwordless_saved_account_cannot_join_workspace_or_use_api(tmp_path, monkeypatch):
    app = _build_account_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    session_token = _create_legacy_passwordless_account(
        app,
        username='Aidan',
        first_name='Aidan',
        last_name='Fernandez',
    )
    with app.app_context():
        account = Account.query.filter_by(username='aidan').one()
        db.session.add(AccountWorkspaceMembership(account_id=account.account_id, workspace_id='owner', role='player'))
        db.session.commit()

    account_headers = {
        'Authorization': f'Bearer {session_token}',
        'X-AIDM-Workspace-Id': 'owner',
    }
    account_snapshot = client.get('/api/accounts/me', headers=account_headers)
    assert account_snapshot.status_code == 200
    assert account_snapshot.get_json()['requires_password_setup'] is True

    saved_workspaces = client.get(
        '/api/accounts/workspaces',
        headers={'Authorization': f'Bearer {session_token}'},
    )
    assert saved_workspaces.status_code == 401
    assert saved_workspaces.get_json()['error_code'] == 'legacy_password_setup_required'

    join_owner = client.post(
        '/api/accounts/workspace',
        headers={'Authorization': f'Bearer {session_token}'},
        json={'workspace_token': 'owner-token'},
    )
    assert join_owner.status_code == 401
    assert join_owner.get_json()['error_code'] == 'legacy_password_setup_required'

    select_owner = client.post(
        '/api/accounts/workspace/select',
        headers={'Authorization': f'Bearer {session_token}'},
        json={'workspace_id': 'owner'},
    )
    assert select_owner.status_code == 401
    assert select_owner.get_json()['error_code'] == 'legacy_password_setup_required'

    campaigns = client.get('/api/campaigns', headers=account_headers)
    assert campaigns.status_code == 401
    assert campaigns.get_json()['error_code'] == 'legacy_password_setup_required'


def test_legacy_claim_sets_password_once_for_passwordless_account(tmp_path, monkeypatch):
    app = _build_account_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    _create_legacy_passwordless_account(
        app,
        username='Maya',
        first_name='Maya',
        last_name='Stone',
    )

    mismatch_claim = client.post(
        '/api/accounts/login',
        json={
            'username': 'Maya',
            'first_name': 'Mara',
            'last_name': 'Stone',
            'password': 'new-secret',
            'legacy_claim': True,
        },
    )
    assert mismatch_claim.status_code == 401

    claim = client.post(
        '/api/accounts/login',
        json={
            'username': 'Maya',
            'password': 'new-secret',
            'legacy_claim': True,
        },
    )
    assert claim.status_code == 200
    claim_token = claim.get_json()['account_token']
    assert claim_token

    with app.app_context():
        account = Account.query.filter_by(username='maya').one()
        assert account.password_hash

    password_login = _login(
        client,
        username='Maya',
        first_name='Maya',
        last_name='Stone',
        password='new-secret',
    )
    assert password_login.status_code == 200
    assert password_login.get_json()['account_token'] != claim_token


def test_account_character_visibility_and_legacy_claim(tmp_path, monkeypatch):
    app = _build_account_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    with app.app_context():
        world = World(name='Owner World', workspace_id='owner')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Owner Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(campaign)
        db.session.flush()
        other_campaign = Campaign(title='Other Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(other_campaign)
        db.session.flush()
        legacy = Player(
            workspace_id='owner',
            campaign_id=campaign.campaign_id,
            name='Danny Reichner',
            character_name='Ember',
        )
        db.session.add(legacy)
        db.session.commit()
        campaign_id = campaign.campaign_id
        other_campaign_id = other_campaign.campaign_id
        legacy_player_id = legacy.player_id

    admin_login = _login(
        client,
        username='danny',
        first_name='Danny',
        last_name='Reichner',
        workspace_token='owner-token',
        password='secret',
    )
    assert admin_login.status_code == 201
    admin_payload = admin_login.get_json()
    assert admin_payload['claimed_player_ids'] == [legacy_player_id]
    with app.app_context():
        membership = AccountWorkspaceMembership.query.filter_by(
            account_id=admin_payload['account']['account_id'],
            workspace_id='owner',
        ).first()
        assert membership is not None
        membership.role = 'admin'
        db.session.commit()
    admin_headers = {
        'Authorization': f"Bearer {admin_payload['account_token']}",
        'X-AIDM-Workspace-Token': 'owner-token',
    }

    normal_login = _login(
        client,
        username='maya',
        first_name='Maya',
        last_name='Stone',
        workspace_token='owner-token',
        password='maya-secret',
    )
    assert normal_login.status_code == 201
    normal_payload = normal_login.get_json()
    assert normal_payload['is_workspace_admin'] is False
    normal_headers = {
        'Authorization': f"Bearer {normal_payload['account_token']}",
        'X-AIDM-Workspace-Token': 'owner-token',
    }

    create_maya = client.post(
        f'/api/players/campaigns/{campaign_id}/players',
        headers=normal_headers,
        json={'character_name': 'Mira', 'race': 'Human'},
    )
    assert create_maya.status_code == 201
    maya_player_id = create_maya.get_json()['player_id']

    create_other_maya = client.post(
        f'/api/players/campaigns/{other_campaign_id}/players',
        headers=normal_headers,
        json={'character_name': 'Mira Elsewhere', 'race': 'Human'},
    )
    assert create_other_maya.status_code == 201
    other_maya_player_id = create_other_maya.get_json()['player_id']

    normal_players = client.get(f'/api/players/campaigns/{campaign_id}/players', headers=normal_headers).get_json()
    assert [player['player_id'] for player in normal_players] == [maya_player_id]
    assert normal_players[0]['name'] == 'Maya Stone'
    assert normal_players[0]['username'] == 'maya'

    normal_other_players = client.get(
        f'/api/players/campaigns/{other_campaign_id}/players',
        headers=normal_headers,
    ).get_json()
    assert [player['player_id'] for player in normal_other_players] == [other_maya_player_id]

    admin_players = client.get(f'/api/players/campaigns/{campaign_id}/players', headers=admin_headers).get_json()
    assert {player['player_id'] for player in admin_players} == {legacy_player_id, maya_player_id}
    assert other_maya_player_id not in {player['player_id'] for player in admin_players}

    normal_workspace = client.get(f'/api/campaigns/{campaign_id}/workspace', headers=normal_headers).get_json()
    assert [player['player_id'] for player in normal_workspace['players']] == [maya_player_id]

    admin_workspace = client.get(f'/api/campaigns/{campaign_id}/workspace', headers=admin_headers).get_json()
    assert {player['player_id'] for player in admin_workspace['players']} == {legacy_player_id, maya_player_id}
    assert other_maya_player_id not in {player['player_id'] for player in admin_workspace['players']}

    with app.app_context():
        legacy_player = db.session.get(Player, legacy_player_id)
        maya_player = db.session.get(Player, maya_player_id)
        assert legacy_player is not None
        assert maya_player is not None
        assert legacy_player.account_id is not None
        assert maya_player.account_id is not None
        assert AccountWorkspaceMembership.query.filter_by(workspace_id='owner', role='admin').count() == 1
