from __future__ import annotations

import importlib
import json
import os
import sys

import pytest
from flask import Flask

from aidm_server.auth import extract_socket_token, hash_secret
from aidm_server.database import db
from aidm_server.creatures.core_bestiary import core_creature
from aidm_server.models import (
    Account,
    AccountWorkspaceMembership,
    BestiaryEntry,
    Campaign,
    DmTurn,
    OperatorActionAudit,
    Player,
    Session,
    TurnEvent,
    Workspace,
    World,
    safe_json_dumps,
    safe_json_loads,
)


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


def _socket_event_payload(events: list[dict], event_name: str):
    event = next((item for item in events if item.get('name') == event_name), None)
    if not event:
        return None
    args = event.get('args') or []
    return args[0] if args else None


def test_rest_auth_required(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    health_response = client.get('/api/health')
    assert health_response.status_code == 200

    unauthorized = client.post('/api/worlds', json={'name': 'NoAuth'})
    assert unauthorized.status_code == 401

    global_operator = client.post(
        '/api/worlds',
        json={'name': 'Authorized World', 'description': 'auth ok'},
        headers={'Authorization': 'Bearer token-123'},
    )
    assert global_operator.status_code == 201

    capabilities = client.get(
        '/api/capabilities',
        headers={'Authorization': 'Bearer token-123'},
    ).get_json()['capabilities']
    assert 'dm_authoring' in capabilities
    assert 'dm_runtime_control' in capabilities
    assert 'debug_read' in capabilities
    assert 'local_operator_only' not in capabilities


def test_capabilities_endpoint_reports_account_role_capabilities(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        player_account = Account(
            username='cap-player',
            first_name='Cap',
            last_name='Player',
            password_hash='configured',
            account_token_hash=hash_secret('cap-player-token'),
        )
        admin_account = Account(
            username='cap-admin',
            first_name='Cap',
            last_name='Admin',
            password_hash='configured',
            account_token_hash=hash_secret('cap-admin-token'),
        )
        db.session.add_all([player_account, admin_account])
        db.session.flush()
        db.session.add_all(
            [
                AccountWorkspaceMembership(account_id=player_account.account_id, workspace_id='owner', role='player'),
                AccountWorkspaceMembership(account_id=admin_account.account_id, workspace_id='owner', role='admin'),
            ]
        )
        db.session.commit()

    unauthenticated = client.get('/api/capabilities')
    player = client.get(
        '/api/capabilities',
        headers={'Authorization': 'Bearer cap-player-token', 'X-AIDM-Workspace-Id': 'owner'},
    )
    admin = client.get(
        '/api/capabilities',
        headers={'Authorization': 'Bearer cap-admin-token', 'X-AIDM-Workspace-Id': 'owner'},
    )

    assert unauthenticated.status_code == 401
    assert player.status_code == 200
    player_payload = player.get_json()
    assert player_payload['capabilities'] == ['player_action', 'player_read']
    assert player_payload['is_workspace_admin'] is False
    assert 'dm_authoring' not in player_payload['capabilities']

    assert admin.status_code == 200
    admin_payload = admin.get_json()
    assert admin_payload['is_workspace_admin'] is True
    assert 'dm_authoring' in admin_payload['capabilities']
    assert 'dm_runtime_control' in admin_payload['capabilities']
    assert admin_payload['descriptions']['dm_runtime_control']


def test_workspace_bearer_token_has_player_capabilities_and_cannot_author_bestiary(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_API_AUTH_TOKENS': 'owner-token',
            'AIDM_API_AUTH_TOKEN_WORKSPACES': 'owner=owner-token',
        },
    )
    client = app.test_client()
    with app.app_context():
        world = World(name='Token Capability World', description='token capability auth')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Token Capability Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(campaign)
        db.session.commit()
        campaign_id = campaign.campaign_id

    headers = {'Authorization': 'Bearer owner-token'}
    capabilities_response = client.get('/api/capabilities', headers=headers)
    authoring_response = client.post(
        f'/api/campaigns/{campaign_id}/bestiary',
        headers=headers,
        json={'creature': core_creature('wolf')},
    )

    assert capabilities_response.status_code == 200
    capabilities_payload = capabilities_response.get_json()
    assert capabilities_payload['account_id'] is None
    assert capabilities_payload['is_workspace_admin'] is False
    assert capabilities_payload['capabilities'] == ['player_action', 'player_read']
    assert 'dm_authoring' not in capabilities_payload['capabilities']
    assert 'debug_read' not in capabilities_payload['capabilities']
    assert 'local_operator_only' not in capabilities_payload['capabilities']
    assert authoring_response.status_code == 403
    assert authoring_response.get_json()['details']['required_capability'] == 'dm_authoring'


def test_dynamic_workspace_header_token_has_player_capabilities(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        db.session.add(
            Workspace(
                workspace_id='dynamic_table',
                name='Dynamic Table',
                name_key='dynamic table',
                token_hash=hash_secret('dynamic-workspace-token'),
            )
        )
        db.session.commit()

    headers = {'X-AIDM-Workspace-Token': 'dynamic-workspace-token'}
    capabilities_response = client.get('/api/capabilities', headers=headers)
    authoring_response = client.post('/api/worlds', headers=headers, json={'name': 'Blocked dynamic world'})

    assert capabilities_response.status_code == 200
    assert capabilities_response.get_json()['capabilities'] == ['player_action', 'player_read']
    assert authoring_response.status_code == 403
    assert authoring_response.get_json()['details']['required_capability'] == 'dm_authoring'


def test_player_account_cannot_mutate_dm_resources_but_admin_can(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        player_account = Account(
            username='boundary-player',
            first_name='Boundary',
            last_name='Player',
            password_hash='configured',
            account_token_hash=hash_secret('boundary-player-token'),
        )
        admin_account = Account(
            username='boundary-admin',
            first_name='Boundary',
            last_name='Admin',
            password_hash='configured',
            account_token_hash=hash_secret('boundary-admin-token'),
        )
        db.session.add_all([player_account, admin_account])
        db.session.flush()
        db.session.add_all(
            [
                AccountWorkspaceMembership(account_id=player_account.account_id, workspace_id='owner', role='player'),
                AccountWorkspaceMembership(account_id=admin_account.account_id, workspace_id='owner', role='admin'),
            ]
        )
        world = World(name='Boundary World', description='capability boundary')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Boundary Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(campaign)
        db.session.flush()
        session = Session(campaign_id=campaign.campaign_id)
        db.session.add(session)
        db.session.commit()
        ids = {
            'world_id': world.world_id,
            'campaign_id': campaign.campaign_id,
            'session_id': session.session_id,
        }

    player_headers = {
        'Authorization': 'Bearer boundary-player-token',
        'X-AIDM-Workspace-Id': 'owner',
    }
    checks = [
        ('post', '/api/worlds', {'name': 'Player-authored world'}, 'dm_authoring'),
        ('patch', f"/api/campaigns/{ids['campaign_id']}", {'title': 'Player rename'}, 'dm_authoring'),
        ('post', '/api/sessions/start', {'campaign_id': ids['campaign_id']}, 'dm_runtime_control'),
        ('post', '/api/maps', {'title': 'Player map', 'campaign_id': ids['campaign_id']}, 'dm_authoring'),
        (
            'post',
            '/api/segments',
            {'campaign_id': ids['campaign_id'], 'title': 'Player segment'},
            'dm_authoring',
        ),
    ]
    for method, path, payload, capability in checks:
        response = getattr(client, method)(path, headers=player_headers, json=payload)
        assert response.status_code == 403, path
        assert response.get_json()['details']['required_capability'] == capability

    admin_response = client.post(
        '/api/worlds',
        headers={
            'Authorization': 'Bearer boundary-admin-token',
            'X-AIDM-Workspace-Id': 'owner',
        },
        json={'name': 'Admin-authored world'},
    )
    assert admin_response.status_code == 201
    admin_metrics = client.get(
        '/api/metrics',
        headers={
            'Authorization': 'Bearer boundary-admin-token',
            'X-AIDM-Workspace-Id': 'owner',
        },
    )
    assert admin_metrics.status_code == 200


def test_workspace_bearer_token_cannot_use_campaign_pack_or_metrics_operator_paths(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_API_AUTH_TOKENS': 'owner-token',
            'AIDM_API_AUTH_TOKEN_WORKSPACES': 'owner=owner-token',
        },
    )
    client = app.test_client()
    headers = {'Authorization': 'Bearer owner-token'}

    checks = [
        ('get', '/api/campaigns/installed-packs', None, 'dm_authoring'),
        ('post', '/api/campaigns/pack-tools/lint', {}, 'dm_authoring'),
        ('post', '/api/campaigns/example-packs/bleakmoor_intro/import', {}, 'dm_authoring'),
        ('get', '/api/metrics', None, 'debug_read'),
        ('get', '/api/metrics/prometheus', None, 'debug_read'),
        ('get', '/api/beta/summary', None, 'debug_read'),
        ('get', '/api/beta/slo', None, 'debug_read'),
    ]
    for method, path, payload, capability in checks:
        response = getattr(client, method)(path, headers=headers, json=payload)
        assert response.status_code == 403, path
        assert response.get_json()['details']['required_capability'] == capability


def test_account_login_is_rate_limited_before_password_verification(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={'AIDM_RATE_LIMIT_MAX_API_REQUESTS': '1'},
    )
    client = app.test_client()
    with app.app_context():
        account = Account(
            username='limited-login',
            first_name='Limited',
            last_name='Login',
            password_hash='configured',
            account_token_hash=hash_secret('limited-login-token'),
        )
        db.session.add(account)
        db.session.commit()

    import aidm_server.blueprints.accounts as accounts_module

    password_checks = 0

    def reject_password(_account, _password):
        nonlocal password_checks
        password_checks += 1
        return False

    monkeypatch.setattr(accounts_module, 'password_matches', reject_password)
    payload = {'intent': 'login', 'username': 'limited-login', 'password': 'wrong'}
    first = client.post('/api/accounts/login', json=payload)
    second = client.post('/api/accounts/login', json=payload)

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.get_json()['error_code'] == 'rate_limited'
    assert password_checks == 1


def test_socket_turn_control_requires_runtime_control_capability(tmp_path, monkeypatch):
    app, socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_API_AUTH_TOKENS': 'global-operator-token,workspace-player-token',
            'AIDM_API_AUTH_TOKEN_WORKSPACES': 'owner=workspace-player-token',
        },
    )
    with app.app_context():
        player_account = Account(
            username='socket-boundary-player',
            first_name='Socket',
            last_name='Player',
            password_hash='configured',
            account_token_hash=hash_secret('socket-boundary-player-token'),
        )
        admin_account = Account(
            username='socket-boundary-admin',
            first_name='Socket',
            last_name='Admin',
            password_hash='configured',
            account_token_hash=hash_secret('socket-boundary-admin-token'),
        )
        db.session.add_all([player_account, admin_account])
        db.session.flush()
        db.session.add_all(
            [
                AccountWorkspaceMembership(account_id=player_account.account_id, workspace_id='owner', role='player'),
                AccountWorkspaceMembership(account_id=admin_account.account_id, workspace_id='owner', role='admin'),
            ]
        )
        world = World(name='Socket Boundary World', description='socket capability boundary')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Socket Boundary Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(campaign)
        db.session.flush()
        player = Player(
            campaign_id=campaign.campaign_id,
            workspace_id='owner',
            account_id=player_account.account_id,
            name='Socket Player',
            character_name='Socket Hero',
        )
        session = Session(campaign_id=campaign.campaign_id)
        db.session.add_all([player, session])
        db.session.commit()
        player_id = player.player_id
        session_id = session.session_id

    player_client = socketio.test_client(
        app,
        flask_test_client=app.test_client(),
        auth={'account_token': 'socket-boundary-player-token', 'workspace_id': 'owner'},
    )
    assert player_client.is_connected()
    player_client.emit('join_session', {'session_id': session_id, 'player_id': player_id})
    player_client.get_received()
    player_client.emit(
        'set_turn_control',
        {'session_id': session_id, 'player_id': player_id, 'mode': 'structured'},
    )
    forbidden = _socket_event_payload(player_client.get_received(), 'error')
    assert forbidden['error_code'] == 'forbidden'
    assert forbidden['details']['required_capability'] == 'dm_runtime_control'

    workspace_token_client = socketio.test_client(
        app,
        flask_test_client=app.test_client(),
        auth={'token': 'workspace-player-token'},
    )
    assert workspace_token_client.is_connected()
    workspace_token_client.emit('join_session', {'session_id': session_id, 'player_id': player_id})
    workspace_token_client.get_received()
    workspace_token_client.emit(
        'set_turn_control',
        {'session_id': session_id, 'player_id': player_id, 'mode': 'structured'},
    )
    token_forbidden = _socket_event_payload(workspace_token_client.get_received(), 'error')
    assert token_forbidden['error_code'] == 'forbidden'
    assert token_forbidden['details']['required_capability'] == 'dm_runtime_control'

    global_operator_client = socketio.test_client(
        app,
        flask_test_client=app.test_client(),
        auth={'token': 'global-operator-token'},
    )
    assert global_operator_client.is_connected()
    global_operator_client.emit('join_session', {'session_id': session_id, 'player_id': player_id})
    global_operator_client.get_received()
    global_operator_client.emit(
        'set_turn_control',
        {'session_id': session_id, 'player_id': player_id, 'mode': 'spotlight'},
    )
    operator_update = _socket_event_payload(global_operator_client.get_received(), 'turn_control_updated')
    assert operator_update['turn_control']['mode'] == 'spotlight'

    admin_client = socketio.test_client(
        app,
        flask_test_client=app.test_client(),
        auth={'account_token': 'socket-boundary-admin-token', 'workspace_id': 'owner'},
    )
    assert admin_client.is_connected()
    admin_client.emit('join_session', {'session_id': session_id, 'player_id': player_id})
    admin_client.get_received()
    admin_client.emit(
        'set_turn_control',
        {'session_id': session_id, 'player_id': player_id, 'mode': 'structured'},
    )
    updated = _socket_event_payload(admin_client.get_received(), 'turn_control_updated')
    assert updated['turn_control']['mode'] == 'structured'


def test_beta_incidents_require_workspace_admin_account_but_players_can_report(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        account = Account(
            username='incident-player',
            first_name='Incident',
            last_name='Player',
            password_hash='configured',
            account_token_hash=hash_secret('incident-token'),
        )
        db.session.add(account)
        db.session.flush()
        membership = AccountWorkspaceMembership(account_id=account.account_id, workspace_id='owner', role='player')
        db.session.add(membership)
        world = World(name='Incident World', description='incident auth')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Incident Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(campaign)
        db.session.flush()
        player = Player(campaign_id=campaign.campaign_id, workspace_id='owner', name='Mira', character_name='Mira')
        db.session.add(player)
        db.session.flush()
        session = Session(campaign_id=campaign.campaign_id)
        db.session.add(session)
        db.session.flush()
        turn = DmTurn(
            session_id=session.session_id,
            campaign_id=campaign.campaign_id,
            player_id=player.player_id,
            player_input='I test the bad report.',
            dm_output='',
            status='failed',
            llm_provider='fallback',
            llm_model='deterministic-v1',
        )
        db.session.add(turn)
        other_world = World(name='Other Workspace Incident World', workspace_id='friend')
        db.session.add(other_world)
        db.session.flush()
        other_campaign = Campaign(
            title='Other Workspace Incident Campaign',
            world_id=other_world.world_id,
            workspace_id='friend',
        )
        db.session.add(other_campaign)
        db.session.flush()
        other_session = Session(campaign_id=other_campaign.campaign_id)
        db.session.add(other_session)
        db.session.flush()
        other_turn = DmTurn(
            session_id=other_session.session_id,
            campaign_id=other_campaign.campaign_id,
            player_input='This newer turn belongs to another workspace.',
            dm_output='Private other-workspace diagnostics.',
            status='completed',
            llm_provider='gemini',
            llm_model='gemini-2.5-pro',
        )
        db.session.add(other_turn)
        db.session.commit()
        account_id = account.account_id
        session_id = session.session_id
        turn_id = turn.turn_id
        other_turn_id = other_turn.turn_id

    headers = {'Authorization': 'Bearer incident-token', 'X-AIDM-Workspace-Id': 'owner'}
    report_response = client.post(
        '/api/feedback/bad-turn',
        headers=headers,
        json={'session_id': session_id, 'turn_id': turn_id, 'category': 'state'},
    )
    player_incidents = client.get('/api/beta/incidents', headers=headers)
    player_bundle = client.get(f'/api/beta/support-bundle?session_id={session_id}', headers=headers)

    assert report_response.status_code == 201
    assert report_response.get_json()['feedback']['provider'] == 'fallback'
    assert player_incidents.status_code == 403
    assert player_incidents.get_json()['details']['required_capability'] == 'debug_read'
    assert player_bundle.status_code == 403
    assert player_bundle.get_json()['details']['required_capability'] == 'debug_read'

    with app.app_context():
        membership = AccountWorkspaceMembership.query.filter_by(account_id=account_id, workspace_id='owner').one()
        membership.role = 'admin'
        db.session.commit()

    admin_incidents = client.get('/api/beta/incidents', headers=headers)
    assert admin_incidents.status_code == 200
    payload = admin_incidents.get_json()
    assert payload['summary']['bad_turn_report_count'] == 1
    assert payload['summary']['failed_turn_count'] == 1

    admin_bundle = client.get(f'/api/beta/support-bundle?session_id={session_id}', headers=headers)
    admin_llm_config = client.get('/api/llm/config', headers=headers)
    assert admin_bundle.status_code == 200
    assert admin_llm_config.status_code == 200
    bundle_payload = admin_bundle.get_json()
    assert bundle_payload['session']['session_id'] == session_id
    assert bundle_payload['incidents']['summary']['bad_turn_report_count'] == 1
    assert bundle_payload['incidents']['summary']['failed_turn_count'] == 1
    assert bundle_payload['recent_turns'][0]['turn_id'] == turn_id
    assert bundle_payload['runtime']['llm']['latest_turn']['turn_id'] == turn_id
    assert bundle_payload['runtime']['llm']['latest_turn']['turn_id'] != other_turn_id
    assert admin_llm_config.get_json()['current']['latest_turn']['turn_id'] == turn_id


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
        friend_world = World(
            workspace_id='aidan_test',
            name='Aidan Test World',
            description='Friend-only world',
        )
        db.session.add(friend_world)
        db.session.flush()
        friend_campaign = Campaign(
            title='Aidan Test Campaign',
            world_id=friend_world.world_id,
            workspace_id='aidan_test',
        )
        db.session.add_all([owner_player, owner_session, friend_campaign])
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
    assert [campaign['title'] for campaign in friend_campaigns.get_json()] == ['Aidan Test Campaign']

    friend_worlds = client.get('/api/worlds', headers=friend_headers)
    assert friend_worlds.status_code == 200
    assert [world['name'] for world in friend_worlds.get_json()] == ['Aidan Test World']
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
    assert blocked_campaign.status_code == 403
    assert blocked_campaign.get_json()['details']['required_capability'] == 'dm_authoring'

    blocked_world = client.post(
        '/api/worlds',
        headers=friend_headers,
        json={'name': 'Another Aidan Test World', 'description': 'Friend-only world'},
    )
    assert blocked_world.status_code == 403
    assert blocked_world.get_json()['details']['required_capability'] == 'dm_authoring'

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


def test_llm_config_requires_global_operator_instead_of_workspace_scoped_tokens(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_API_AUTH_TOKENS': 'global-operator-token,owner-token,tenant-token',
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
    assert tenant_patch.get_json()['error_code'] == 'forbidden'
    assert tenant_patch.get_json()['details']['required_capability'] == 'admin_workspace'
    assert app.config['AIDM_LLM_PROVIDER'] == 'gemini'
    assert os.environ['AIDM_LLM_PROVIDER'] == 'gemini'

    owner_workspace_patch = client.patch(
        '/api/llm/config',
        headers={'Authorization': 'Bearer owner-token'},
        json={'provider': 'fallback', 'model': 'deterministic-v1', 'persist': False},
    )
    assert owner_workspace_patch.status_code == 403
    assert owner_workspace_patch.get_json()['details']['required_capability'] == 'admin_workspace'

    global_operator_patch = client.patch(
        '/api/llm/config',
        headers={'Authorization': 'Bearer global-operator-token'},
        json={'provider': 'fallback', 'model': 'deterministic-v1', 'persist': False},
    )

    assert global_operator_patch.status_code == 200
    assert global_operator_patch.get_json()['current']['provider'] == 'fallback'
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
        tenant_membership = AccountWorkspaceMembership(
            account_id=account.account_id,
            workspace_id='tenant_b',
            role='admin',
        )
        db.session.add_all([membership, tenant_membership])
        db.session.commit()
        account_id = account.account_id

    account_headers = {'Authorization': 'Bearer account-token', 'X-AIDM-Workspace-Id': 'owner'}
    player_get = client.get('/api/llm/config', headers=account_headers)
    player_patch = client.patch(
        '/api/llm/config',
        headers=account_headers,
        json={'provider': 'fallback', 'model': 'deterministic-v1', 'persist': False},
    )

    assert player_get.status_code == 403
    assert player_get.get_json()['details']['required_capability'] == 'debug_read'
    assert player_patch.status_code == 403
    assert player_patch.get_json()['error_code'] == 'forbidden'
    assert player_patch.get_json()['details']['required_capability'] == 'admin_workspace'
    assert app.config['AIDM_LLM_PROVIDER'] == 'gemini'

    tenant_admin_patch = client.patch(
        '/api/llm/config',
        headers={'Authorization': 'Bearer account-token', 'X-AIDM-Workspace-Id': 'tenant_b'},
        json={'provider': 'fallback', 'model': 'deterministic-v1', 'persist': False},
    )
    assert tenant_admin_patch.status_code == 403
    assert tenant_admin_patch.get_json()['error_code'] == 'runtime_config_admin_required'
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


def test_combat_operator_endpoints_require_workspace_admin_account(tmp_path, monkeypatch):
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
        player = Player(campaign_id=campaign.campaign_id, workspace_id='owner', name='Kael', character_name='Kael')
        db.session.add(player)
        db.session.flush()
        session = Session(campaign_id=campaign.campaign_id)
        db.session.add(session)
        db.session.commit()
        account_id = account.account_id
        session_id = session.session_id

    headers = {'Authorization': 'Bearer combat-token', 'X-AIDM-Workspace-Id': 'owner'}
    player_start = client.post(
        f'/api/sessions/{session_id}/combat/start',
        headers=headers,
        json={'creature': core_creature('wolf'), 'enemyCount': 1},
    )
    player_morale = client.post(
        f'/api/sessions/{session_id}/combat/apply-morale-event',
        headers=headers,
        json={'participantId': 'enemy_wolf_1', 'event': 'took_heavy_damage'},
    )
    player_apply = client.post(
        f'/api/sessions/{session_id}/combat/apply-state-changes',
        headers=headers,
        json={'changes': []},
    )
    player_plan = client.post(f'/api/sessions/{session_id}/combat/plan-enemy-intents', headers=headers)
    player_debug = client.get(f'/api/sessions/{session_id}/combat/debug', headers=headers)

    with app.app_context():
        session = db.session.get(Session, session_id)
        session.state_snapshot = safe_json_dumps(
            {
                'combat': {
                    'status': 'active',
                    'participants': [
                        {'id': 'player_1', 'team': 'player', 'hp': {'current': 12, 'max': 12}, 'isAlive': True},
                        {'id': 'enemy_wolf_1', 'team': 'enemy', 'hp': {'current': 0, 'max': 11}, 'isAlive': False},
                    ],
                    'flags': {},
                }
            },
            {},
        )
        db.session.commit()
    player_check_end = client.post(
        f'/api/sessions/{session_id}/combat/check-end',
        headers=headers,
        json={'apply': True},
    )

    assert player_start.status_code == 403
    assert player_start.get_json()['error_code'] == 'forbidden'
    assert player_morale.status_code == 403
    assert player_morale.get_json()['error_code'] == 'forbidden'
    assert player_apply.status_code == 403
    assert player_apply.get_json()['error_code'] == 'forbidden'
    assert player_plan.status_code == 403
    assert player_plan.get_json()['error_code'] == 'forbidden'
    assert player_debug.status_code == 403
    assert player_debug.get_json()['error_code'] == 'forbidden'
    assert player_check_end.status_code == 403
    assert player_check_end.get_json()['error_code'] == 'forbidden'

    with app.app_context():
        session = db.session.get(Session, session_id)
        session.state_snapshot = safe_json_dumps({'combat': {'status': 'none', 'participants': [], 'flags': {}}}, {})
        membership = AccountWorkspaceMembership.query.filter_by(account_id=account_id, workspace_id='owner').one()
        membership.role = 'admin'
        db.session.commit()

    admin_start = client.post(
        f'/api/sessions/{session_id}/combat/start',
        headers=headers,
        json={'creature': core_creature('wolf'), 'enemyCount': 1},
    )
    enemy_id = next(
        participant['id']
        for participant in admin_start.get_json()['combat']['participants']
        if participant['team'] == 'enemy'
    )
    admin_plan = client.post(f'/api/sessions/{session_id}/combat/plan-enemy-intents', headers=headers)
    admin_morale = client.post(
        f'/api/sessions/{session_id}/combat/apply-morale-event',
        headers=headers,
        json={'participantId': enemy_id, 'event': 'took_heavy_damage'},
    )
    admin_apply = client.post(
        f'/api/sessions/{session_id}/combat/apply-state-changes',
        headers=headers,
        json={
            'changes': [
                {
                    'id': 'defeat_enemy_for_auth_test',
                    'type': 'combat.participant.update',
                    'participantId': enemy_id,
                    'hp': {'current': 0, 'max': 11},
                }
            ]
        },
    )
    admin_check_end = client.post(
        f'/api/sessions/{session_id}/combat/check-end',
        headers=headers,
        json={'apply': True},
    )
    admin_debug = client.get(f'/api/sessions/{session_id}/combat/debug', headers=headers)
    admin_debug_bad_limit = client.get(f'/api/sessions/{session_id}/combat/debug?limit=invalid', headers=headers)

    assert admin_start.status_code == 200
    assert admin_start.get_json()['combat']['status'] == 'active'
    assert admin_plan.status_code == 200
    assert admin_plan.get_json()['intentPlan']
    assert admin_morale.status_code == 200
    assert admin_morale.get_json()['validation']['rejected'] == []
    assert admin_apply.status_code == 200
    assert len(admin_apply.get_json()['appliedChanges']) == 1
    assert admin_check_end.status_code == 200
    assert admin_check_end.get_json()['endReason'] == 'all_enemies_defeated'
    assert admin_debug.status_code == 200
    assert admin_debug.get_json()['events']
    assert admin_debug_bad_limit.status_code == 200
    assert admin_debug_bad_limit.get_json()['events']


def test_bestiary_authoring_endpoints_require_workspace_admin_account(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        account = Account(
            username='bestiary-player',
            first_name='Bestiary',
            last_name='Player',
            password_hash='configured',
            account_token_hash=hash_secret('bestiary-token'),
        )
        db.session.add(account)
        db.session.flush()
        membership = AccountWorkspaceMembership(account_id=account.account_id, workspace_id='owner', role='player')
        db.session.add(membership)
        world = World(name='Bestiary Auth World', description='bestiary auth')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Bestiary Auth Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(campaign)
        db.session.flush()
        session = Session(campaign_id=campaign.campaign_id)
        db.session.add(session)
        db.session.commit()
        account_id = account.account_id
        campaign_id = campaign.campaign_id
        session_id = session.session_id

    headers = {'Authorization': 'Bearer bestiary-token', 'X-AIDM-Workspace-Id': 'owner'}
    player_create = client.post(
        f'/api/campaigns/{campaign_id}/bestiary',
        headers=headers,
        json={'creature': core_creature('wolf')},
    )
    player_generate_pack = client.post(
        f'/api/campaigns/{campaign_id}/bestiary/generate-pack',
        headers=headers,
        json={'themes': ['ash'], 'count': 1},
    )
    player_resolve_save = client.post(
        '/api/creatures/resolve',
        headers=headers,
        json={
            'campaignId': campaign_id,
            'descriptionHint': 'wolf',
            'themeTags': ['wolf'],
            'allowGeneration': False,
            'allowVariants': False,
        },
    )
    player_resolve_preview = client.post(
        '/api/creatures/resolve',
        headers=headers,
        json={
            'campaignId': campaign_id,
            'saveGenerated': 'off',
            'descriptionHint': 'wolf',
            'themeTags': ['wolf'],
            'allowGeneration': False,
            'allowVariants': False,
        },
    )
    player_evolve_save = client.post(
        '/api/creatures/evolve',
        headers=headers,
        json={
            'campaignId': campaign_id,
            'sessionId': session_id,
            'baseCreature': core_creature('goblin_skirmisher'),
            'eventContext': {'eventTags': ['fire'], 'grudgeTargetId': 'player_1'},
        },
    )
    player_evolve_preview = client.post(
        '/api/creatures/evolve',
        headers=headers,
        json={
            'campaignId': campaign_id,
            'saveGenerated': 'off',
            'baseCreature': core_creature('goblin_skirmisher'),
            'eventContext': {'eventTags': ['fire']},
        },
    )

    assert player_create.status_code == 403
    assert player_create.get_json()['error_code'] == 'forbidden'
    assert player_generate_pack.status_code == 403
    assert player_generate_pack.get_json()['error_code'] == 'forbidden'
    assert player_resolve_save.status_code == 403
    assert player_resolve_save.get_json()['error_code'] == 'forbidden'
    assert player_resolve_preview.status_code == 200
    player_resolve_preview_payload = player_resolve_preview.get_json()
    assert player_resolve_preview_payload['savedToBestiary'] is False
    assert 'debug' not in player_resolve_preview_payload
    assert player_evolve_save.status_code == 403
    assert player_evolve_save.get_json()['error_code'] == 'forbidden'
    assert player_evolve_preview.status_code == 200
    assert player_evolve_preview.get_json()['entry'] is None
    with app.app_context():
        assert BestiaryEntry.query.filter_by(campaign_id=campaign_id).count() == 0
        assert OperatorActionAudit.query.filter_by(campaign_id=campaign_id).count() == 0

    player_audits = client.get('/api/beta/audits', headers=headers)
    assert player_audits.status_code == 403
    assert player_audits.get_json()['error_code'] == 'forbidden'

    with app.app_context():
        membership = AccountWorkspaceMembership.query.filter_by(account_id=account_id, workspace_id='owner').one()
        membership.role = 'admin'
        db.session.commit()

    admin_create = client.post(
        f'/api/campaigns/{campaign_id}/bestiary',
        headers=headers,
        json={'creature': core_creature('wolf')},
    )
    admin_generate_pack = client.post(
        f'/api/campaigns/{campaign_id}/bestiary/generate-pack',
        headers=headers,
        json={'themes': ['ash'], 'count': 1},
    )
    admin_resolve_save = client.post(
        '/api/creatures/resolve',
        headers=headers,
        json={
            'campaignId': campaign_id,
            'descriptionHint': 'wolf',
            'themeTags': ['wolf'],
            'allowGeneration': False,
            'allowVariants': False,
        },
    )
    admin_evolve_save = client.post(
        '/api/creatures/evolve',
        headers=headers,
        json={
            'campaignId': campaign_id,
            'sessionId': session_id,
            'baseCreature': core_creature('goblin_skirmisher'),
            'eventContext': {'eventTags': ['fire'], 'grudgeTargetId': 'player_1'},
        },
    )

    assert admin_create.status_code == 201
    assert admin_create.get_json()['entry']['creature']['id'] == 'wolf'
    assert admin_generate_pack.status_code == 200
    assert len(admin_generate_pack.get_json()['entries']) == 3
    assert admin_resolve_save.status_code == 200
    admin_resolve_save_payload = admin_resolve_save.get_json()
    assert admin_resolve_save_payload['savedToBestiary'] is False
    assert admin_resolve_save_payload['debug']['rankings']
    assert admin_evolve_save.status_code == 200
    assert admin_evolve_save.get_json()['entry']['source'] == 'evolved'
    with app.app_context():
        audits = (
            OperatorActionAudit.query.filter_by(campaign_id=campaign_id)
            .order_by(OperatorActionAudit.operator_audit_id.asc())
            .all()
        )
        assert [audit.action for audit in audits] == [
            'bestiary.create',
            'bestiary.generate_pack',
            'bestiary.evolve_save',
        ]
        assert {audit.actor_role for audit in audits} == {'admin'}
        assert {audit.actor_account_id for audit in audits} == {account_id}
        generate_details = safe_json_loads(audits[1].details_json, {})
        assert generate_details['savedCount'] == 3
        evolve_details = safe_json_loads(audits[2].details_json, {})
        assert evolve_details['scope'] == 'session'
    admin_audits = client.get('/api/beta/audits?limit=5', headers=headers)
    assert admin_audits.status_code == 200
    audit_payload = admin_audits.get_json()
    assert audit_payload['summary']['operator_action_count'] == 3
    assert [row['action'] for row in reversed(audit_payload['operator_actions'])] == [
        'bestiary.create',
        'bestiary.generate_pack',
        'bestiary.evolve_save',
    ]


def test_generate_pack_string_save_off_generates_without_persisting(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        account = Account(
            username='bestiary-preview-admin',
            first_name='Bestiary',
            last_name='Preview',
            password_hash='configured',
            account_token_hash=hash_secret('bestiary-preview-token'),
        )
        db.session.add(account)
        db.session.flush()
        db.session.add(AccountWorkspaceMembership(account_id=account.account_id, workspace_id='owner', role='admin'))
        world = World(name='Bestiary Preview World', description='bestiary preview')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Bestiary Preview Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(campaign)
        db.session.commit()
        campaign_id = campaign.campaign_id

    response = client.post(
        f'/api/campaigns/{campaign_id}/bestiary/generate-pack',
        headers={'Authorization': 'Bearer bestiary-preview-token', 'X-AIDM-Workspace-Id': 'owner'},
        json={'themes': ['ash'], 'count': 3, 'save': 'off'},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload['creatures']) == 3
    assert payload['entries'] == []
    with app.app_context():
        assert BestiaryEntry.query.filter_by(campaign_id=campaign_id).count() == 0
        audits = OperatorActionAudit.query.filter_by(campaign_id=campaign_id).all()
        assert len(audits) == 1
        details = safe_json_loads(audits[0].details_json, {})
        assert details['generatedCount'] == 3
        assert details['savedCount'] == 0


def test_evolve_save_forbidden_preflights_before_evolution_work(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        account = Account(
            username='evolve-preflight-player',
            first_name='Evolve',
            last_name='Player',
            password_hash='configured',
            account_token_hash=hash_secret('evolve-preflight-token'),
        )
        db.session.add(account)
        db.session.flush()
        db.session.add(AccountWorkspaceMembership(account_id=account.account_id, workspace_id='owner', role='player'))
        world = World(name='Evolve Preflight World', description='evolve auth preflight')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Evolve Preflight Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(campaign)
        db.session.flush()
        session = Session(campaign_id=campaign.campaign_id)
        db.session.add(session)
        db.session.commit()
        campaign_id = campaign.campaign_id
        session_id = session.session_id

    import aidm_server.blueprints.creatures as creatures_module

    def fail_evolution(*_args, **_kwargs):
        raise AssertionError('evolve_creature should not run for unauthorized save requests')

    monkeypatch.setattr(creatures_module, 'evolve_creature', fail_evolution)
    response = client.post(
        '/api/creatures/evolve',
        headers={'Authorization': 'Bearer evolve-preflight-token', 'X-AIDM-Workspace-Id': 'owner'},
        json={
            'campaignId': campaign_id,
            'sessionId': session_id,
            'baseCreature': core_creature('goblin_skirmisher'),
            'eventContext': {'eventTags': ['fire'], 'grudgeTargetId': 'player_1'},
        },
    )

    assert response.status_code == 403
    assert response.get_json()['error_code'] == 'forbidden'
    with app.app_context():
        assert BestiaryEntry.query.filter_by(campaign_id=campaign_id).count() == 0
        assert OperatorActionAudit.query.filter_by(campaign_id=campaign_id).count() == 0


def test_example_campaign_pack_import_requires_workspace_admin_account(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        account = Account(
            username='example-import-player',
            first_name='Example',
            last_name='Importer',
            password_hash='configured',
            account_token_hash=hash_secret('example-import-token'),
        )
        db.session.add(account)
        db.session.flush()
        membership = AccountWorkspaceMembership(account_id=account.account_id, workspace_id='owner', role='player')
        db.session.add(membership)
        db.session.commit()
        account_id = account.account_id

    headers = {'Authorization': 'Bearer example-import-token', 'X-AIDM-Workspace-Id': 'owner'}
    player_import = client.post('/api/campaigns/example-packs/bleakmoor_intro/import', headers=headers, json={})

    assert player_import.status_code == 403
    assert player_import.get_json()['error_code'] == 'forbidden'
    with app.app_context():
        assert Campaign.query.filter_by(workspace_id='owner', title='The Lanterns of Bleakmoor').count() == 0

        membership = AccountWorkspaceMembership.query.filter_by(account_id=account_id, workspace_id='owner').one()
        membership.role = 'admin'
        db.session.commit()

    admin_import = client.post('/api/campaigns/example-packs/bleakmoor_intro/import', headers=headers, json={})

    assert admin_import.status_code == 201
    payload = admin_import.get_json()
    assert payload['pack_id'] == 'bleakmoor_intro'
    assert payload['session']['state_snapshot']['campaignPack']['packId'] == 'bleakmoor_intro'


def test_session_import_strips_campaign_pack_state_for_non_admin_account(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        world = World(name='Import Auth World', description='auth import')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Import Auth Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(campaign)
        account = Account(
            username='session-import-player',
            first_name='Session',
            last_name='Importer',
            password_hash='configured',
            account_token_hash=hash_secret('session-import-token'),
        )
        db.session.add(account)
        db.session.flush()
        db.session.add(AccountWorkspaceMembership(account_id=account.account_id, workspace_id='owner', role='player'))
        db.session.commit()
        campaign_id = campaign.campaign_id

    crafted_snapshot = {
        'currentScene': {'locationId': 'evil_gate', 'name': 'Evil Gate'},
        'flags': {
            'campaignPackActiveCheckpointId': 'cp_evil',
            'campaignPackProgressRevision': 999,
            'ordinaryFlag': 'kept',
        },
        'campaignPack': {
            'packId': 'evil_pack',
            'activeCheckpointId': 'cp_evil',
            'directorRules': {'mainQuestGeneration': 'pack_only'},
            'checkpoints': [{'id': 'cp_evil', 'title': 'Injected', 'encounterIds': ['enc_evil']}],
            'catalog': {
                'encounters': [
                    {'id': 'enc_evil', 'enemyGroups': [{'enemyId': 'enemy_evil', 'count': 20_000}]}
                ],
                'enemies': [{'id': 'enemy_evil', 'name': 'Injected Enemy'}],
            },
        },
    }
    headers = {'Authorization': 'Bearer session-import-token', 'X-AIDM-Workspace-Id': 'owner'}
    response = client.post(
        '/api/sessions/import',
        headers=headers,
        json={
            'campaign_id': campaign_id,
            'selectedSession': {'state_snapshot': crafted_snapshot},
            'turnEvents': [
                {
                    'event_type': 'campaign_pack.progress.changed',
                    'payload': {'packId': 'evil_pack', 'toCheckpointId': 'cp_evil'},
                }
            ],
        },
    )

    assert response.status_code == 201
    imported_session_id = response.get_json()['session_id']
    with app.app_context():
        imported_session = db.session.get(Session, imported_session_id)
        snapshot = json.loads(imported_session.state_snapshot)
        imported_pack_events = TurnEvent.query.filter_by(
            session_id=imported_session_id,
            event_type='campaign_pack.progress.changed',
        ).count()

    assert 'campaignPack' not in snapshot
    assert snapshot['flags'] == {'ordinaryFlag': 'kept'}
    assert snapshot['importMetadata']['campaignPackStateStripped'] is True
    assert imported_pack_events == 0
    assert 'campaignPack' not in response.get_json()['session']['state_snapshot']


def test_socket_token_extraction_ignores_query_and_event_payloads(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)

    with app.test_request_context('/socket.io/?token=token-123'):
        assert extract_socket_token(data_payload={'token': 'token-123'}) is None

    with app.test_request_context('/socket.io/', headers={'Authorization': 'Bearer token-123'}):
        assert extract_socket_token(data_payload={'token': 'ignored'}) == 'token-123'

    with app.test_request_context('/socket.io/'):
        assert extract_socket_token(auth_payload={'token': 'token-123'}) == 'token-123'


def test_socket_token_extraction_accepts_http_only_account_cookie(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={'AIDM_ACCOUNT_COOKIE_AUTH_ENABLED': 'true'},
    )
    with app.app_context():
        db.session.add(
            Account(
                username='socket-cookie',
                first_name='Socket',
                last_name='Cookie',
                account_token_hash=hash_secret('socket-cookie-token'),
            )
        )
        db.session.commit()

    with app.test_request_context('/socket.io/', headers={'Cookie': 'aidm_account_session=socket-cookie-token'}):
        assert extract_socket_token() == 'socket-cookie-token'


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
            'AIDM_API_AUTH_TOKENS': 'token-123,friend-token,owner-player-token',
            'AIDM_API_AUTH_TOKEN_WORKSPACES': 'aidan_test=friend-token,owner=owner-player-token',
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

    owner_workspace_token = client.get('/admin/', headers={'Authorization': 'Bearer owner-player-token'})
    assert owner_workspace_token.status_code == 401

    without_bearer_after_success = client.get('/admin/')
    assert without_bearer_after_success.status_code == 401


def test_admin_allows_owner_workspace_admin_account_but_not_player_account(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={'AIDM_ADMIN_ENABLED': 'true'},
    )
    client = app.test_client()
    with app.app_context():
        player_account = Account(
            username='admin-ui-player',
            first_name='Admin UI',
            last_name='Player',
            password_hash='configured',
            account_token_hash=hash_secret('admin-ui-player-token'),
        )
        admin_account = Account(
            username='admin-ui-admin',
            first_name='Admin UI',
            last_name='Admin',
            password_hash='configured',
            account_token_hash=hash_secret('admin-ui-admin-token'),
        )
        db.session.add_all([player_account, admin_account])
        db.session.flush()
        db.session.add_all(
            [
                AccountWorkspaceMembership(
                    account_id=player_account.account_id,
                    workspace_id='owner',
                    role='player',
                ),
                AccountWorkspaceMembership(
                    account_id=admin_account.account_id,
                    workspace_id='owner',
                    role='admin',
                ),
            ]
        )
        db.session.commit()

    player_headers = {
        'Authorization': 'Bearer admin-ui-player-token',
        'X-AIDM-Workspace-Id': 'owner',
    }
    admin_headers = {
        'Authorization': 'Bearer admin-ui-admin-token',
        'X-AIDM-Workspace-Id': 'owner',
    }

    player_index = client.get('/admin/', headers=player_headers)
    player_create = client.post(
        '/admin/world/new/',
        headers=player_headers,
        data={'name': 'Player Escalation World', 'description': 'must not be created'},
    )
    admin_index = client.get('/admin/', headers=admin_headers)

    assert player_index.status_code == 401
    assert player_create.status_code == 401
    assert admin_index.status_code == 200
    with app.app_context():
        assert World.query.filter_by(name='Player Escalation World').count() == 0


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

    first = client.get('/api/capabilities', headers={**headers, 'X-Forwarded-For': '1.1.1.1'})
    second = client.get('/api/capabilities', headers={**headers, 'X-Forwarded-For': '2.2.2.2'})

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

    first = client.get('/api/capabilities', headers=headers)
    second = client.get('/api/capabilities', headers=headers)

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
