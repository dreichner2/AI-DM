from __future__ import annotations

import importlib

from aidm_server.database import db
from aidm_server.models import AccountWorkspaceMembership, Campaign, Player, World


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
):
    payload = {
        'username': username,
        'first_name': first_name,
        'last_name': last_name,
        'password': password,
    }
    if workspace_token is not None:
        payload['workspace_token'] = workspace_token
    return client.post(
        '/api/accounts/login',
        json=payload,
    )


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
