from __future__ import annotations

import importlib
import os
import sys

import pytest
from flask import Flask

from aidm_server.auth import extract_socket_token, hash_secret
from aidm_server.database import db
from aidm_server.models import Account, AccountWorkspaceMembership, Campaign, Player, Session, World, safe_json_dumps


def _build_auth_runtime(tmp_path, monkeypatch, extra_env: dict[str, str] | None = None):
    db_path = tmp_path / 'auth.db'

    monkeypatch.setenv('AIDM_DATABASE_URI', f'sqlite:///{db_path}')
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'true')
    monkeypatch.setenv('AIDM_AUTH_REQUIRED', 'true')
    monkeypatch.setenv('AIDM_API_AUTH_TOKENS', 'token-123')
    monkeypatch.setenv('AIDM_ENV', 'test')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('AIDM_SOCKETIO_ASYNC_MODE', 'threading')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')
    monkeypatch.setenv('AIDM_RATE_LIMIT_MAX_API_REQUESTS', '1000')
    monkeypatch.setenv('AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES', '1000')
    if extra_env:
        for key, value in extra_env.items():
            monkeypatch.setenv(key, value)

    import aidm_server.main as main_module
    main_module = importlib.reload(main_module)

    import aidm_server.blueprints.socketio_events as socketio_events_module
    socketio_events_module = importlib.reload(socketio_events_module)

    socketio_events_module.active_players.clear()
    socketio_events_module.socketio_connections.clear()

    app = main_module.create_app()
    socketio = main_module.create_socketio(app)
    socketio_events_module.register_socketio_events(socketio)

    with app.app_context():
        db.create_all()

    return app, socketio


def test_rest_auth_required(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    health_response = client.get('/api/health')
    assert health_response.status_code == 200

    unauthorized = client.post('/api/worlds', json={'name': 'NoAuth'})
    assert unauthorized.status_code == 401

    authorized = client.post(
        '/api/worlds',
        json={'name': 'Authorized World', 'description': 'auth ok'},
        headers={'Authorization': 'Bearer token-123'},
    )
    assert authorized.status_code == 201


def test_auth_required_allows_cors_preflight_without_token(tmp_path, monkeypatch):
    origin = 'http://127.0.0.1:5173'
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={'AIDM_CORS_ALLOWLIST': origin},
    )
    client = app.test_client()

    response = client.options(
        '/api/campaigns',
        headers={
            'Origin': origin,
            'Access-Control-Request-Method': 'GET',
            'Access-Control-Request-Headers': 'Authorization, X-AIDM-Workspace-Id',
        },
    )

    assert response.status_code == 200
    assert response.headers.get('Access-Control-Allow-Origin') == origin


def test_auth_required_for_mutating_api_endpoints_and_tts(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    with app.app_context():
        world = World(name='Auth World', description='auth coverage')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Auth Campaign', world_id=world.world_id)
        db.session.add(campaign)
        db.session.flush()
        player = Player(campaign_id=campaign.campaign_id, name='Alice', character_name='Seraphina')
        db.session.add(player)
        db.session.flush()
        session = Session(campaign_id=campaign.campaign_id)
        db.session.add(session)
        db.session.commit()

        ids = {
            'world_id': world.world_id,
            'campaign_id': campaign.campaign_id,
            'player_id': player.player_id,
            'session_id': session.session_id,
        }

    mutating_requests = [
        ('post', '/api/campaigns', {'title': 'Blocked Campaign', 'world_id': ids['world_id']}),
        ('post', '/api/sessions/start', {'campaign_id': ids['campaign_id']}),
        ('post', '/api/maps', {'title': 'Blocked Map', 'campaign_id': ids['campaign_id']}),
        ('post', '/api/segments', {'campaign_id': ids['campaign_id'], 'title': 'Blocked Segment'}),
        (
            'post',
            f"/api/players/campaigns/{ids['campaign_id']}/players",
            {'name': 'Bob', 'character_name': 'Borin'},
        ),
        ('patch', f"/api/players/{ids['player_id']}", {'level': 4}),
        ('patch', f"/api/campaigns/{ids['campaign_id']}", {'title': 'Blocked Campaign Rename'}),
        ('delete', f"/api/campaigns/{ids['campaign_id']}", {}),
        ('post', f"/api/campaigns/{ids['campaign_id']}/archive", {}),
        ('post', f"/api/campaigns/{ids['campaign_id']}/restore", {}),
        ('patch', f"/api/sessions/{ids['session_id']}", {'name': 'Blocked Session Rename'}),
        ('delete', f"/api/sessions/{ids['session_id']}", {}),
        ('post', f"/api/sessions/{ids['session_id']}/archive", {}),
        ('post', f"/api/sessions/{ids['session_id']}/restore", {}),
        ('post', '/api/tts/speak', {'text': 'The torches flicker.'}),
    ]

    for method, path, payload in mutating_requests:
        response = getattr(client, method)(path, json=payload)
        assert response.status_code == 401, path
        assert response.get_json()['error_code'] == 'unauthorized'


def test_socket_auth_required(tmp_path, monkeypatch):
    app, socketio = _build_auth_runtime(tmp_path, monkeypatch)

    no_auth_client = socketio.test_client(app, flask_test_client=app.test_client())
    assert not no_auth_client.is_connected()

    query_auth_client = socketio.test_client(
        app,
        flask_test_client=app.test_client(),
        query_string='token=token-123',
    )
    assert not query_auth_client.is_connected()

    auth_client = socketio.test_client(
        app,
        flask_test_client=app.test_client(),
        auth={'token': 'token-123'},
    )
    assert auth_client.is_connected()

    import aidm_server.blueprints.socketio_events as socketio_events_module

    assert socketio_events_module.socketio_connections
    assert all('token' not in connection for connection in socketio_events_module.socketio_connections.values())
    auth_client.disconnect()


def test_auth_tokens_are_scoped_to_campaign_workspaces(tmp_path, monkeypatch):
    app, socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_API_AUTH_TOKENS': 'owner-token,friend-token',
            'AIDM_API_AUTH_TOKEN_WORKSPACES': 'owner=owner-token,aidan_test=friend-token',
        },
    )
    client = app.test_client()
    owner_headers = {'Authorization': 'Bearer owner-token'}
    friend_headers = {'Authorization': 'Bearer friend-token'}

    with app.app_context():
        world = World(name='Shared World', description='Reusable test world')
        db.session.add(world)
        db.session.flush()
        owner_campaign = Campaign(title='Owner Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(owner_campaign)
        db.session.flush()
        owner_player = Player(
            campaign_id=owner_campaign.campaign_id,
            name='Danny',
            character_name='Owner Hero',
        )
        owner_session = Session(campaign_id=owner_campaign.campaign_id)
        db.session.add_all([owner_player, owner_session])
        db.session.commit()
        ids = {
            'world_id': world.world_id,
            'campaign_id': owner_campaign.campaign_id,
            'player_id': owner_player.player_id,
            'session_id': owner_session.session_id,
        }

    owner_campaigns = client.get('/api/campaigns', headers=owner_headers)
    assert owner_campaigns.status_code == 200
    assert [campaign['title'] for campaign in owner_campaigns.get_json()] == ['Owner Campaign']

    owner_worlds = client.get('/api/worlds', headers=owner_headers)
    assert owner_worlds.status_code == 200
    assert [world['name'] for world in owner_worlds.get_json()] == ['Shared World']

    friend_campaigns = client.get('/api/campaigns', headers=friend_headers)
    assert friend_campaigns.status_code == 200
    assert friend_campaigns.get_json() == []

    friend_worlds = client.get('/api/worlds', headers=friend_headers)
    assert friend_worlds.status_code == 200
    assert friend_worlds.get_json() == []
    assert client.get(f"/api/worlds/{ids['world_id']}", headers=friend_headers).status_code == 404

    hidden_paths = [
        f"/api/campaigns/{ids['campaign_id']}",
        f"/api/campaigns/{ids['campaign_id']}/workspace",
        f"/api/players/{ids['player_id']}",
        f"/api/sessions/{ids['session_id']}/log",
        f"/api/sessions/{ids['session_id']}/state",
    ]
    for path in hidden_paths:
        assert client.get(path, headers=friend_headers).status_code == 404

    blocked_campaign = client.post(
        '/api/campaigns',
        headers=friend_headers,
        json={'title': 'Aidan Test Campaign', 'world_id': ids['world_id']},
    )
    assert blocked_campaign.status_code == 404
    assert blocked_campaign.get_json()['error_code'] == 'world_not_found'

    friend_world = client.post(
        '/api/worlds',
        headers=friend_headers,
        json={'name': 'Aidan Test World', 'description': 'Friend-only world'},
    )
    assert friend_world.status_code == 201
    friend_world_id = friend_world.get_json()['world_id']

    created = client.post(
        '/api/campaigns',
        headers=friend_headers,
        json={'title': 'Aidan Test Campaign', 'world_id': friend_world_id},
    )
    assert created.status_code == 201
    friend_campaign_id = created.get_json()['campaign_id']

    with app.app_context():
        friend_world_obj = db.session.get(World, friend_world_id)
        assert friend_world_obj is not None
        assert friend_world_obj.workspace_id == 'aidan_test'
        friend_campaign = db.session.get(Campaign, friend_campaign_id)
        assert friend_campaign is not None
        assert friend_campaign.workspace_id == 'aidan_test'

    friend_campaigns = client.get('/api/campaigns', headers=friend_headers)
    assert [campaign['title'] for campaign in friend_campaigns.get_json()] == ['Aidan Test Campaign']

    friend_socket = socketio.test_client(
        app,
        flask_test_client=app.test_client(),
        auth={'token': 'friend-token'},
    )
    assert friend_socket.is_connected()
    friend_socket.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    received = friend_socket.get_received()
    errors = [event['args'][0] for event in received if event['name'] == 'error']
    assert errors and errors[0]['error_code'] == 'session_not_found'
    friend_socket.disconnect()


def test_llm_config_update_requires_owner_workspace_token(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_API_AUTH_TOKENS': 'owner-token,tenant-token',
            'AIDM_API_AUTH_TOKEN_WORKSPACES': 'owner=owner-token,tenant_b=tenant-token',
            'AIDM_LLM_PROVIDER': 'gemini',
            'AIDM_LLM_MODEL': 'gemini-2.5-pro',
        },
    )
    client = app.test_client()
    app.config['AIDM_LLM_PROVIDER'] = 'gemini'
    app.config['AIDM_LLM_MODEL'] = 'gemini-2.5-pro'

    tenant_patch = client.patch(
        '/api/llm/config',
        headers={'Authorization': 'Bearer tenant-token'},
        json={'provider': 'fallback', 'model': 'deterministic-v1', 'persist': False},
    )

    assert tenant_patch.status_code == 403
    assert tenant_patch.get_json()['error_code'] == 'runtime_config_admin_required'
    assert app.config['AIDM_LLM_PROVIDER'] == 'gemini'
    assert os.environ['AIDM_LLM_PROVIDER'] == 'gemini'

    owner_patch = client.patch(
        '/api/llm/config',
        headers={'Authorization': 'Bearer owner-token'},
        json={'provider': 'fallback', 'model': 'deterministic-v1', 'persist': False},
    )

    assert owner_patch.status_code == 200
    assert owner_patch.get_json()['current']['provider'] == 'fallback'
    assert app.config['AIDM_LLM_PROVIDER'] == 'fallback'
    assert os.environ['AIDM_LLM_PROVIDER'] == 'fallback'


def test_llm_config_update_requires_owner_account_admin_role(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_API_AUTH_TOKENS': 'owner-token',
            'AIDM_API_AUTH_TOKEN_WORKSPACES': 'owner=owner-token',
            'AIDM_LLM_PROVIDER': 'gemini',
            'AIDM_LLM_MODEL': 'gemini-2.5-pro',
        },
    )
    client = app.test_client()
    app.config['AIDM_LLM_PROVIDER'] = 'gemini'
    app.config['AIDM_LLM_MODEL'] = 'gemini-2.5-pro'
    with app.app_context():
        account = Account(
            username='maya',
            first_name='Maya',
            last_name='Tester',
            password_hash='configured',
            account_token_hash=hash_secret('account-token'),
        )
        db.session.add(account)
        db.session.flush()
        membership = AccountWorkspaceMembership(account_id=account.account_id, workspace_id='owner', role='player')
        db.session.add(membership)
        db.session.commit()
        account_id = account.account_id

    account_headers = {'Authorization': 'Bearer account-token', 'X-AIDM-Workspace-Id': 'owner'}
    player_patch = client.patch(
        '/api/llm/config',
        headers=account_headers,
        json={'provider': 'fallback', 'model': 'deterministic-v1', 'persist': False},
    )

    assert player_patch.status_code == 403
    assert player_patch.get_json()['error_code'] == 'runtime_config_admin_required'
    assert app.config['AIDM_LLM_PROVIDER'] == 'gemini'

    with app.app_context():
        membership = AccountWorkspaceMembership.query.filter_by(account_id=account_id, workspace_id='owner').one()
        membership.role = 'admin'
        db.session.commit()

    admin_patch = client.patch(
        '/api/llm/config',
        headers=account_headers,
        json={'provider': 'fallback', 'model': 'deterministic-v1', 'persist': False},
    )

    assert admin_patch.status_code == 200
    assert admin_patch.get_json()['current']['provider'] == 'fallback'
    assert app.config['AIDM_LLM_PROVIDER'] == 'fallback'


def test_combat_state_and_debug_endpoints_require_workspace_admin_account(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        account = Account(
            username='combat-player',
            first_name='Combat',
            last_name='Player',
            password_hash='configured',
            account_token_hash=hash_secret('combat-token'),
        )
        db.session.add(account)
        db.session.flush()
        membership = AccountWorkspaceMembership(account_id=account.account_id, workspace_id='owner', role='player')
        db.session.add(membership)
        world = World(name='Combat Auth World', description='combat auth')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Combat Auth Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(campaign)
        db.session.flush()
        session = Session(
            campaign_id=campaign.campaign_id,
            state_snapshot=safe_json_dumps({'combat': {'status': 'none', 'participants': [], 'flags': {}}}, {}),
        )
        db.session.add(session)
        db.session.commit()
        account_id = account.account_id
        session_id = session.session_id

    headers = {'Authorization': 'Bearer combat-token', 'X-AIDM-Workspace-Id': 'owner'}
    player_apply = client.post(
        f'/api/sessions/{session_id}/combat/apply-state-changes',
        headers=headers,
        json={'changes': []},
    )
    player_debug = client.get(f'/api/sessions/{session_id}/combat/debug', headers=headers)

    assert player_apply.status_code == 403
    assert player_apply.get_json()['error_code'] == 'forbidden'
    assert player_debug.status_code == 403
    assert player_debug.get_json()['error_code'] == 'forbidden'

    with app.app_context():
        membership = AccountWorkspaceMembership.query.filter_by(account_id=account_id, workspace_id='owner').one()
        membership.role = 'admin'
        db.session.commit()

    admin_apply = client.post(
        f'/api/sessions/{session_id}/combat/apply-state-changes',
        headers=headers,
        json={'changes': []},
    )
    admin_debug = client.get(f'/api/sessions/{session_id}/combat/debug', headers=headers)
    admin_debug_bad_limit = client.get(f'/api/sessions/{session_id}/combat/debug?limit=invalid', headers=headers)

    assert admin_apply.status_code == 200
    assert admin_apply.get_json()['appliedChanges'] == []
    assert admin_debug.status_code == 200
    assert admin_debug.get_json()['events'] == []
    assert admin_debug_bad_limit.status_code == 200
    assert admin_debug_bad_limit.get_json()['events'] == []


def test_socket_token_extraction_ignores_query_and_event_payloads(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)

    with app.test_request_context('/socket.io/?token=token-123'):
        assert extract_socket_token(data_payload={'token': 'token-123'}) is None

    with app.test_request_context('/socket.io/', headers={'Authorization': 'Bearer token-123'}):
        assert extract_socket_token(data_payload={'token': 'ignored'}) == 'token-123'

    with app.test_request_context('/socket.io/'):
        assert extract_socket_token(auth_payload={'token': 'token-123'}) == 'token-123'


def test_admin_denies_access_when_auth_is_disabled(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_AUTH_REQUIRED': 'false',
            'AIDM_ADMIN_ENABLED': 'true',
            'AIDM_API_AUTH_TOKENS': '',
        },
    )
    client = app.test_client()

    response = client.get('/admin/')

    assert response.status_code == 403


def test_admin_denies_model_view_writes_when_auth_is_disabled(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_AUTH_REQUIRED': 'false',
            'AIDM_ADMIN_ENABLED': 'true',
            'AIDM_API_AUTH_TOKENS': '',
        },
    )
    client = app.test_client()

    response = client.post(
        '/admin/world/new/',
        data={'name': 'pwned-world', 'description': 'created without authentication'},
    )

    assert response.status_code == 403
    with app.app_context():
        assert World.query.filter_by(name='pwned-world').count() == 0


def test_admin_requires_auth_when_enabled(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_ADMIN_ENABLED': 'true',
            'AIDM_API_AUTH_TOKENS': 'token-123,friend-token',
            'AIDM_API_AUTH_TOKEN_WORKSPACES': 'aidan_test=friend-token',
        },
    )
    client = app.test_client()

    unauthorized = client.get('/admin/')
    assert unauthorized.status_code == 401

    query_token = client.get('/admin/?token=token-123')
    assert query_token.status_code == 401

    authorized = client.get('/admin/', headers={'Authorization': 'Bearer token-123'})
    assert authorized.status_code == 200

    friend_token = client.get('/admin/', headers={'Authorization': 'Bearer friend-token'})
    assert friend_token.status_code == 401

    without_bearer_after_success = client.get('/admin/')
    assert without_bearer_after_success.status_code == 401


def test_admin_rejects_cookie_signed_with_old_default_secret(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={'AIDM_ADMIN_ENABLED': 'true'},
    )
    client = app.test_client()

    forged_app = Flask(__name__)
    forged_app.secret_key = 'dev-secret-change-me'
    serializer = forged_app.session_interface.get_signing_serializer(forged_app)
    assert serializer is not None
    forged_cookie = serializer.dumps({'aidm_admin_authorized': True})

    client.set_cookie(app.config.get('SESSION_COOKIE_NAME', 'session'), forged_cookie)
    response = client.get('/admin/')

    assert response.status_code == 401


def test_production_requires_explicit_secret_key(tmp_path, monkeypatch):
    db_path = tmp_path / 'prod_auth.db'
    monkeypatch.setenv('AIDM_DATABASE_URI', f'sqlite:///{db_path}')
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'false')
    monkeypatch.setenv('AIDM_ENV', 'production')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('AIDM_AUTH_REQUIRED', 'true')
    monkeypatch.setenv('AIDM_API_AUTH_TOKENS', 'token-123')
    monkeypatch.setenv('AIDM_SOCKETIO_ASYNC_MODE', 'threading')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')
    monkeypatch.delenv('FLASK_SECRET_KEY', raising=False)

    with pytest.raises(ValueError, match='FLASK_SECRET_KEY'):
        if 'aidm_server.main' in sys.modules:
            main_module = importlib.reload(sys.modules['aidm_server.main'])
        else:
            main_module = importlib.import_module('aidm_server.main')
        main_module.create_app()


def test_api_rate_limit_ignores_spoofed_forwarded_for(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={'AIDM_RATE_LIMIT_MAX_API_REQUESTS': '1'},
    )
    client = app.test_client()
    headers = {'Authorization': 'Bearer token-123'}

    first = client.get('/api/metrics', headers={**headers, 'X-Forwarded-For': '1.1.1.1'})
    second = client.get('/api/metrics', headers={**headers, 'X-Forwarded-For': '2.2.2.2'})

    assert first.status_code == 200
    assert second.status_code == 429


def test_api_rate_limit_can_use_database_store(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_RATE_LIMIT_MAX_API_REQUESTS': '1',
            'AIDM_RATE_LIMIT_STORE': 'database',
        },
    )
    client = app.test_client()
    headers = {'Authorization': 'Bearer token-123'}

    first = client.get('/api/metrics', headers=headers)
    second = client.get('/api/metrics', headers=headers)

    assert app.config['AIDM_RATE_LIMIT_STORE'] == 'database'
    assert first.status_code == 200
    assert second.status_code == 429


def test_api_rate_limit_uses_route_template_instead_of_raw_ids(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={'AIDM_RATE_LIMIT_MAX_API_REQUESTS': '1'},
    )
    client = app.test_client()
    headers = {'Authorization': 'Bearer token-123'}

    first = client.get('/api/sessions/111/log', headers=headers)
    second = client.get('/api/sessions/222/log', headers=headers)

    assert first.status_code == 404
    assert second.status_code == 429
