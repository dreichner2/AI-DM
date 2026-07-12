from __future__ import annotations

import importlib
import json
import os
import sys

import pytest
from flask import Flask
from sqlalchemy import event

from aidm_server.auth import extract_socket_token, hash_secret
from aidm_server.database import db
from aidm_server.creatures.core_bestiary import core_creature
from aidm_server.models import (
    Account,
    AccountWorkspaceMembership,
    BestiaryEntry,
    Campaign,
    CampaignPackCheckpointProgress,
    CampaignPackSession,
    CampaignSegment,
    DmTurn,
    Map,
    OperatorActionAudit,
    Player,
    PlayerAction,
    Session,
    SessionLogEntry,
    SessionState,
    StoryEntity,
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


def test_api_guard_resolves_account_and_membership_once_without_noop_commit(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        account = Account(
            username='query-count-player',
            first_name='Query',
            last_name='Player',
            password_hash='configured',
            account_token_hash=hash_secret('query-count-token'),
        )
        db.session.add(account)
        db.session.flush()
        db.session.add(
            AccountWorkspaceMembership(
                account_id=account.account_id,
                workspace_id='owner',
                role='player',
            )
        )
        db.session.commit()
        engine = db.engine

    statements: list[str] = []
    commits: list[bool] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(' '.join(str(statement).lower().split()))

    def record_commit(_conn):
        commits.append(True)

    event.listen(engine, 'before_cursor_execute', record_statement)
    event.listen(engine, 'commit', record_commit)
    try:
        response = client.get(
            '/api/capabilities',
            headers={
                'Authorization': 'Bearer query-count-token',
                'X-AIDM-Workspace-Id': 'owner',
            },
        )
    finally:
        event.remove(engine, 'before_cursor_execute', record_statement)
        event.remove(engine, 'commit', record_commit)

    select_statements = [statement for statement in statements if statement.startswith('select ')]
    assert response.status_code == 200
    assert response.get_json()['capabilities'] == ['player_action', 'player_read']
    assert sum(' from accounts ' in statement for statement in select_statements) == 1
    assert sum(' from account_workspace_memberships ' in statement for statement in select_statements) == 1
    assert commits == []


def test_workspace_token_membership_cache_is_request_local_and_preserves_role_changes(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        account = Account(
            username='token-role-player',
            first_name='Token',
            last_name='Role',
            password_hash='configured',
            account_token_hash=hash_secret('token-role-account-token'),
        )
        workspace = Workspace(
            workspace_id='token_role_table',
            name='Token Role Table',
            name_key='token role table',
            token_hash=hash_secret('test-key-token-role-workspace'),
        )
        db.session.add_all([account, workspace])
        db.session.commit()
        account_id = account.account_id
        engine = db.engine

    headers = {
        'Authorization': 'Bearer token-role-account-token',
        'X-AIDM-Workspace-Token': 'test-key-token-role-workspace',
    }
    initial = client.get('/api/capabilities', headers=headers)

    assert initial.status_code == 200
    assert initial.get_json()['capabilities'] == ['player_action', 'player_read']
    with app.app_context():
        membership = AccountWorkspaceMembership.query.filter_by(
            account_id=account_id,
            workspace_id='token_role_table',
        ).one()
        membership.role = 'admin'
        db.session.commit()

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(' '.join(str(statement).lower().split()))

    event.listen(engine, 'before_cursor_execute', record_statement)
    try:
        updated = client.get('/api/capabilities', headers=headers)
    finally:
        event.remove(engine, 'before_cursor_execute', record_statement)

    select_statements = [statement for statement in statements if statement.startswith('select ')]
    assert updated.status_code == 200
    assert updated.get_json()['is_workspace_admin'] is True
    assert 'dm_authoring' in updated.get_json()['capabilities']
    assert sum(' from accounts ' in statement for statement in select_statements) == 1
    assert sum(' from workspaces ' in statement for statement in select_statements) == 1
    assert sum(' from account_workspace_memberships ' in statement for statement in select_statements) == 1


def test_api_guard_still_commits_a_matching_legacy_player_claim(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        account = Account(
            username='legacy-guard',
            first_name='Legacy',
            last_name='Guard',
            password_hash='configured',
            account_token_hash=hash_secret('legacy-guard-token'),
        )
        db.session.add(account)
        db.session.flush()
        db.session.add(
            AccountWorkspaceMembership(
                account_id=account.account_id,
                workspace_id='owner',
                role='player',
            )
        )
        legacy_player = Player(
            workspace_id='owner',
            name=' Legacy Guard ',
            character_name='Recovered Hero',
        )
        db.session.add(legacy_player)
        db.session.commit()
        account_id = account.account_id
        legacy_player_id = legacy_player.player_id

    response = client.get(
        '/api/capabilities',
        headers={
            'Authorization': 'Bearer legacy-guard-token',
            'X-AIDM-Workspace-Id': 'owner',
        },
    )

    assert response.status_code == 200
    with app.app_context():
        claimed_player = db.session.get(Player, legacy_player_id)
        assert claimed_player.account_id == account_id
        assert claimed_player.name == 'Legacy Guard'


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


def test_player_cannot_read_raw_segments_and_workspace_only_exposes_triggered_public_segments(
    tmp_path,
    monkeypatch,
):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        player_account = Account(
            username='segment-player',
            first_name='Segment',
            last_name='Player',
            password_hash='configured',
            account_token_hash=hash_secret('segment-player-token'),
        )
        admin_account = Account(
            username='segment-admin',
            first_name='Segment',
            last_name='Admin',
            password_hash='configured',
            account_token_hash=hash_secret('segment-admin-token'),
        )
        db.session.add_all([player_account, admin_account])
        db.session.flush()
        db.session.add_all(
            [
                AccountWorkspaceMembership(account_id=player_account.account_id, workspace_id='owner', role='player'),
                AccountWorkspaceMembership(account_id=admin_account.account_id, workspace_id='owner', role='admin'),
            ]
        )
        world = World(name='Segment Privacy World', description='segment privacy', workspace_id='owner')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Segment Privacy Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(campaign)
        db.session.flush()
        player = Player(
            campaign_id=campaign.campaign_id,
            workspace_id='owner',
            account_id=player_account.account_id,
            name='Segment Player',
            character_name='Segment Hero',
        )
        revealed = CampaignSegment(
            campaign_id=campaign.campaign_id,
            title='Revealed Segment',
            description='The bridge has already collapsed.',
            trigger_condition='DM_ONLY_REVEALED_TRIGGER',
            tags='DM_ONLY_REVEALED_TAGS',
            metadata_json=safe_json_dumps({'directorNote': 'DM_ONLY_REVEALED_METADATA'}, {}),
            is_triggered=True,
        )
        hidden = CampaignSegment(
            campaign_id=campaign.campaign_id,
            title='DM_ONLY_HIDDEN_TITLE',
            description='DM_ONLY_HIDDEN_DESCRIPTION',
            trigger_condition='DM_ONLY_HIDDEN_TRIGGER',
            tags='DM_ONLY_HIDDEN_TAGS',
            metadata_json=safe_json_dumps({'directorNote': 'DM_ONLY_HIDDEN_METADATA'}, {}),
            is_triggered=False,
        )
        db.session.add_all([player, revealed, hidden])
        db.session.commit()
        campaign_id = campaign.campaign_id
        revealed_id = revealed.segment_id

    player_headers = {
        'Authorization': 'Bearer segment-player-token',
        'X-AIDM-Workspace-Id': 'owner',
    }
    admin_headers = {
        'Authorization': 'Bearer segment-admin-token',
        'X-AIDM-Workspace-Id': 'owner',
    }

    raw_list = client.get(f'/api/segments?campaign_id={campaign_id}', headers=player_headers)
    raw_detail = client.get(f'/api/segments/{revealed_id}', headers=player_headers)
    player_workspace = client.get(f'/api/campaigns/{campaign_id}/workspace', headers=player_headers)
    admin_list = client.get(f'/api/segments?campaign_id={campaign_id}', headers=admin_headers)
    admin_workspace = client.get(f'/api/campaigns/{campaign_id}/workspace', headers=admin_headers)

    assert raw_list.status_code == 403
    assert raw_list.get_json()['details']['required_capability'] == 'dm_authoring'
    assert raw_detail.status_code == 403
    assert raw_detail.get_json()['details']['required_capability'] == 'dm_authoring'

    assert player_workspace.status_code == 200
    player_payload = player_workspace.get_json()
    assert player_payload['summary']['segment_count'] == 1
    assert len(player_payload['segments']) == 1
    assert player_payload['segments'][0] == {
        'segment_id': revealed_id,
        'campaign_id': campaign_id,
        'title': 'Revealed Segment',
        'description': 'The bridge has already collapsed.',
        'is_triggered': True,
    }
    assert 'DM_ONLY_' not in json.dumps(player_payload)

    assert admin_list.status_code == 200
    assert len(admin_list.get_json()) == 2
    assert admin_workspace.status_code == 200
    admin_payload = admin_workspace.get_json()
    assert admin_payload['summary']['segment_count'] == 2
    assert 'DM_ONLY_HIDDEN_TITLE' in json.dumps(admin_payload)
    assert 'DM_ONLY_REVEALED_TRIGGER' in json.dumps(admin_payload)


def test_authored_map_visibility_filters_player_rest_workspace_export_socket_and_supports_reveal(
    tmp_path,
    monkeypatch,
):
    app, socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        player_account = Account(
            username='map-player',
            first_name='Map',
            last_name='Player',
            password_hash='configured',
            account_token_hash=hash_secret('map-player-token'),
        )
        admin_account = Account(
            username='map-admin',
            first_name='Map',
            last_name='Admin',
            password_hash='configured',
            account_token_hash=hash_secret('map-admin-token'),
        )
        db.session.add_all([player_account, admin_account])
        db.session.flush()
        db.session.add_all(
            [
                AccountWorkspaceMembership(account_id=player_account.account_id, workspace_id='owner', role='player'),
                AccountWorkspaceMembership(account_id=admin_account.account_id, workspace_id='owner', role='admin'),
            ]
        )
        world = World(name='Map Privacy World', description='map privacy', workspace_id='owner')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Map Privacy Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add(campaign)
        db.session.flush()
        player = Player(
            campaign_id=campaign.campaign_id,
            workspace_id='owner',
            account_id=player_account.account_id,
            name='Map Player',
            character_name='Map Hero',
        )
        session = Session(campaign_id=campaign.campaign_id)
        revealed = Map(
            world_id=world.world_id,
            campaign_id=campaign.campaign_id,
            title='Revealed Crossing',
            description='A crossing the party has discovered.',
            map_data=safe_json_dumps({'marker': 'PLAYER_MAP_MARKER'}, {}),
            visibility='player',
        )
        hidden = Map(
            world_id=world.world_id,
            campaign_id=campaign.campaign_id,
            title='DM_ONLY_MAP_TITLE',
            description='DM_ONLY_MAP_DESCRIPTION',
            map_data=safe_json_dumps({'marker': 'DM_ONLY_MAP_DATA'}, {}),
            visibility='dm',
        )
        db.session.add_all([player, session, revealed, hidden])
        db.session.commit()
        campaign_id = campaign.campaign_id
        session_id = session.session_id
        player_id = player.player_id
        revealed_id = revealed.map_id
        hidden_id = hidden.map_id

    player_headers = {
        'Authorization': 'Bearer map-player-token',
        'X-AIDM-Workspace-Id': 'owner',
    }
    admin_headers = {
        'Authorization': 'Bearer map-admin-token',
        'X-AIDM-Workspace-Id': 'owner',
    }

    player_list = client.get(f'/api/maps?campaign_id={campaign_id}', headers=player_headers)
    player_workspace = client.get(f'/api/campaigns/{campaign_id}/workspace', headers=player_headers)
    player_revealed = client.get(f'/api/maps/{revealed_id}', headers=player_headers)
    player_hidden = client.get(f'/api/maps/{hidden_id}', headers=player_headers)
    player_export = client.get(f'/api/sessions/{session_id}/export', headers=player_headers)

    assert player_list.status_code == 200
    assert [item['map_id'] for item in player_list.get_json()] == [revealed_id]
    assert player_revealed.status_code == 200
    assert player_revealed.get_json()['visibility'] == 'player'
    assert player_hidden.status_code == 404
    assert player_hidden.get_json()['error_code'] == 'map_not_found'
    assert player_workspace.status_code == 200
    player_workspace_payload = player_workspace.get_json()
    assert player_workspace_payload['summary']['map_count'] == 1
    assert [item['map_id'] for item in player_workspace_payload['maps']] == [revealed_id]
    assert player_export.status_code == 200
    assert 'DM_ONLY_MAP_' not in json.dumps(player_export.get_json())
    assert 'DM_ONLY_MAP_' not in json.dumps(player_workspace_payload)

    player_socket = socketio.test_client(
        app,
        flask_test_client=app.test_client(),
        auth={'account_token': 'map-player-token', 'workspace_id': 'owner'},
    )
    assert player_socket.is_connected()
    player_socket.emit('join_session', {'session_id': session_id, 'player_id': player_id})
    assert 'DM_ONLY_MAP_' not in json.dumps(player_socket.get_received())
    player_socket.disconnect()

    admin_list = client.get(f'/api/maps?campaign_id={campaign_id}', headers=admin_headers)
    admin_workspace = client.get(f'/api/campaigns/{campaign_id}/workspace', headers=admin_headers)
    admin_hidden = client.get(f'/api/maps/{hidden_id}', headers=admin_headers)
    assert admin_list.status_code == 200
    assert {item['map_id'] for item in admin_list.get_json()} == {revealed_id, hidden_id}
    assert admin_workspace.status_code == 200
    assert admin_workspace.get_json()['summary']['map_count'] == 2
    assert admin_hidden.status_code == 200
    assert admin_hidden.get_json()['map_data']['marker'] == 'DM_ONLY_MAP_DATA'

    reveal = client.patch(
        f'/api/maps/{hidden_id}',
        headers=admin_headers,
        json={'visibility': 'player'},
    )
    assert reveal.status_code == 200
    newly_revealed = client.get(f'/api/maps/{hidden_id}', headers=player_headers)
    assert newly_revealed.status_code == 200
    assert newly_revealed.get_json()['map_data']['marker'] == 'DM_ONLY_MAP_DATA'

    hide_again = client.patch(
        f'/api/maps/{hidden_id}',
        headers=admin_headers,
        json={'visibility': 'dm'},
    )
    assert hide_again.status_code == 200
    assert client.get(f'/api/maps/{hidden_id}', headers=player_headers).status_code == 404


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

    import aidm_server.blueprints.socketio_events as socketio_events_module

    assert socketio_events_module.socketio_connections == {}

    auth_client = socketio.test_client(
        app,
        flask_test_client=app.test_client(),
        auth={'token': 'token-123'},
    )
    assert auth_client.is_connected()

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


def _seed_session_player_privacy_fixture(app):
    with app.app_context():
        attacker = Account(
            username='session-privacy-attacker',
            first_name='Session',
            last_name='Attacker',
            password_hash='configured',
            account_token_hash=hash_secret('session-privacy-attacker-token'),
        )
        victim = Account(
            username='session-privacy-victim',
            first_name='Session',
            last_name='Victim',
            password_hash='configured',
            account_token_hash=hash_secret('session-privacy-victim-token'),
        )
        admin = Account(
            username='session-privacy-admin',
            first_name='Session',
            last_name='Admin',
            password_hash='configured',
            account_token_hash=hash_secret('session-privacy-admin-token'),
        )
        db.session.add_all([attacker, victim, admin])
        db.session.flush()
        db.session.add_all(
            [
                AccountWorkspaceMembership(account_id=attacker.account_id, workspace_id='owner', role='player'),
                AccountWorkspaceMembership(account_id=victim.account_id, workspace_id='owner', role='player'),
                AccountWorkspaceMembership(account_id=admin.account_id, workspace_id='owner', role='admin'),
            ]
        )
        world = World(name='Session Privacy World', description='authorization regression')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(
            title='Session Privacy Campaign',
            world_id=world.world_id,
            workspace_id='owner',
        )
        db.session.add(campaign)
        db.session.flush()
        attacker_player = Player(
            campaign_id=campaign.campaign_id,
            workspace_id='owner',
            account_id=attacker.account_id,
            name='Attacker Account',
            character_name='Attacker Hero',
            race='Human',
            class_='Rogue',
            level=3,
            stats=safe_json_dumps({'dexterity': 16, 'privateResource': 'ATTACKER_RESOURCE'}, {}),
            inventory=safe_json_dumps([{'id': 'attacker_item', 'name': 'ATTACKER_PRIVATE_ITEM'}], []),
            character_sheet=safe_json_dumps(
                {'privateNotes': 'ATTACKER_PRIVATE_SHEET', 'knownSpells': ['ATTACKER_PRIVATE_SPELL']},
                {},
            ),
        )
        victim_player = Player(
            campaign_id=campaign.campaign_id,
            workspace_id='owner',
            account_id=victim.account_id,
            name='Victim Account',
            character_name='Victim Hero',
            race='Elf',
            class_='Wizard',
            level=4,
            stats=safe_json_dumps({'intelligence': 18, 'privateResource': 'VICTIM_RESOURCE'}, {}),
            inventory=safe_json_dumps([{'id': 'victim_item', 'name': 'VICTIM_PRIVATE_ITEM'}], []),
            character_sheet=safe_json_dumps(
                {'privateNotes': 'VICTIM_PRIVATE_SHEET', 'knownSpells': ['VICTIM_PRIVATE_SPELL']},
                {},
            ),
        )
        db.session.add_all([attacker_player, victim_player])
        db.session.flush()

        def snapshot_player(player, *, item: str, spell: str, resource: str):
            return {
                'id': f'player_{player.player_id}',
                'playerId': player.player_id,
                'name': player.character_name,
                'race': player.race,
                'class': player.class_,
                'level': player.level,
                'health': {'currentHp': 17, 'maxHp': 21},
                'stats': {'ability': 18, 'privateResource': resource},
                'inventory': {'items': [{'id': f'item_{player.player_id}', 'name': item}]},
                'character_sheet': {'privateNotes': f'{resource}_PRIVATE_SHEET'},
                'spellbook': {'knownSpells': [{'id': f'spell_{player.player_id}', 'name': spell}]},
                'spells': [spell],
                'resources': {'spellSlots': {'1': 3}},
                'metadata': {'privateNotes': f'{player.character_name} private metadata'},
            }

        snapshot = {
            'schemaVersion': 1,
            'playerCharacters': [
                snapshot_player(
                    attacker_player,
                    item='ATTACKER_PRIVATE_ITEM',
                    spell='ATTACKER_PRIVATE_SPELL',
                    resource='ATTACKER_RESOURCE',
                ),
                snapshot_player(
                    victim_player,
                    item='VICTIM_PRIVATE_ITEM',
                    spell='VICTIM_PRIVATE_SPELL',
                    resource='VICTIM_RESOURCE',
                ),
            ],
            'combat': {
                'status': 'active',
                'participants': [
                    {
                        'id': f'player_{attacker_player.player_id}',
                        'name': attacker_player.character_name,
                        'team': 'player',
                        'kind': 'player_character',
                        'class': attacker_player.class_,
                        'level': attacker_player.level,
                        'hp': {'current': 17, 'max': 21},
                        'stats': {'privateResource': 'ATTACKER_COMBAT_RESOURCE'},
                        'abilities': [{'name': 'ATTACKER_COMBAT_ABILITY'}],
                        'armorClass': 15,
                        'conditions': [],
                        'isAlive': True,
                        'isConscious': True,
                    },
                    {
                        'id': f'player_{victim_player.player_id}',
                        'name': victim_player.character_name,
                        'team': 'player',
                        'kind': 'player_character',
                        'class': victim_player.class_,
                        'level': victim_player.level,
                        'hp': {'current': 12, 'max': 24},
                        'stats': {'privateResource': 'VICTIM_COMBAT_RESOURCE'},
                        'abilities': [{'name': 'VICTIM_COMBAT_ABILITY'}],
                        'armorClass': 13,
                        'conditions': ['concentrating'],
                        'isAlive': True,
                        'isConscious': True,
                    },
                ],
                'flags': {},
            },
            'flags': {
                'campaignPackActiveCheckpointId': 'cp_start',
                'campaignPackProgressRevision': 2,
                'dmOnlyRuntimeFlag': 'DM_ONLY_FLAG',
            },
            'stateChangeLedger': [{'message': 'DM_ONLY_LEDGER'}],
            'campaignPack': {
                'packId': 'privacy_pack',
                'title': 'Privacy Pack',
                'activeCheckpointId': 'cp_start',
                'checkpoints': [
                    {'id': 'cp_start', 'title': 'Visible Start'},
                    {'id': 'cp_secret', 'title': 'DM_ONLY_CHECKPOINT'},
                ],
                'catalog': {'locations': [{'id': 'secret_lair', 'name': 'DM_ONLY_LOCATION'}]},
                'directorRules': {'offTrackPolicy': 'DM_ONLY_POLICY'},
            },
        }
        session = Session(
            campaign_id=campaign.campaign_id,
            state_snapshot=safe_json_dumps(snapshot, {}),
        )
        db.session.add(session)
        db.session.flush()
        db.session.add(
            SessionState(
                session_id=session.session_id,
                current_location='Visible Start',
                current_quest='Protect private character state',
                active_segments=safe_json_dumps(
                    [
                        {
                            'segment_id': 44,
                            'title': 'Revealed Story Beat',
                            'description': 'The party has reached the bell tower.',
                            'reason': 'DM_ONLY_TRIGGER_REASON',
                            'trigger_spec': {
                                'trigger_type': 'keywords',
                                'raw': {'keywords': ['DM_ONLY_TRIGGER_KEYWORD']},
                            },
                        }
                    ],
                    [],
                ),
                memory_snippets='[]',
            )
        )
        db.session.commit()
        return {
            'campaign_id': campaign.campaign_id,
            'session_id': session.session_id,
            'attacker_player_id': attacker_player.player_id,
            'victim_player_id': victim_player.player_id,
        }


def _private_roll_event_payload(*, client_message_id: str, score: int, skill: str, wound_penalty: int) -> dict:
    roll = {
        'rule_type': 'social',
        'die': 'd20',
        'mode': 'normal',
        'rolls': [12],
        'kept': 12,
        'modifier': 4,
        'total': 16,
        'reason': 'persuade the guard',
        'result_visibility': 'visible',
        'dc_hint': f'DM_ONLY_DC_HINT_{client_message_id}',
        'ability': {'key': 'charisma', 'label': 'CHA', 'score': score, 'modifier': 4},
        'proficiency': {'bonus': 2, 'skills': [skill]},
        'modifier_breakdown': {
            'ability_modifier': 4,
            'proficiency_bonus': 2,
            'wound_penalty': wound_penalty,
            'total': 4,
        },
    }
    return {
        'pending_turn_id': None,
        'roll_value': 16,
        'roll': roll,
        'metadata': {
            'client_message_id': client_message_id,
            'dc_hint': f'DM_ONLY_METADATA_HINT_{client_message_id}',
            'action_intent': {'kind': 'roll', 'roll': roll},
            'authoritative_roll': roll,
            'roll_gate': {'scope': 'single_player', 'roll_spec': {**roll, 'task_dc': 14}},
        },
    }


def test_non_admin_session_state_keeps_own_detail_and_redacts_other_players(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    ids = _seed_session_player_privacy_fixture(app)
    attacker_headers = {
        'Authorization': 'Bearer session-privacy-attacker-token',
        'X-AIDM-Workspace-Id': 'owner',
    }

    victim_detail = client.get(f"/api/players/{ids['victim_player_id']}", headers=attacker_headers)
    state_response = client.get(f"/api/sessions/{ids['session_id']}/state", headers=attacker_headers)
    session_list_response = client.get(
        f"/api/sessions/campaigns/{ids['campaign_id']}/sessions",
        headers=attacker_headers,
    )
    workspace_response = client.get(
        f"/api/campaigns/{ids['campaign_id']}/workspace",
        headers=attacker_headers,
    )

    assert victim_detail.status_code == 404
    assert state_response.status_code == 200
    state_payload = state_response.get_json()
    snapshot = state_payload['state_snapshot']
    assert state_payload['active_segments'] == [
        {
            'segment_id': 44,
            'title': 'Revealed Story Beat',
            'description': 'The party has reached the bell tower.',
        }
    ]
    assert 'DM_ONLY_TRIGGER' not in json.dumps(state_payload)
    assert [
        bundle['playerId']
        for bundle in snapshot['combat'].get('legalActions', [])
    ] == [ids['attacker_player_id']]
    actors = {actor['playerId']: actor for actor in snapshot['playerCharacters']}
    assert actors[ids['attacker_player_id']]['inventory']['items'][0]['name'] == 'ATTACKER_PRIVATE_ITEM'
    assert actors[ids['attacker_player_id']]['spellbook']['knownSpells'][0]['name'] == 'ATTACKER_PRIVATE_SPELL'
    assert set(actors[ids['victim_player_id']]) == {'id', 'playerId', 'name', 'race', 'class', 'level'}
    assert actors[ids['victim_player_id']]['name'] == 'Victim Hero'
    victim_combat = next(
        participant
        for participant in snapshot['combat']['participants']
        if participant['id'] == f"player_{ids['victim_player_id']}"
    )
    assert set(victim_combat) == {
        'id',
        'name',
        'team',
        'kind',
        'class',
        'level',
        'hp',
        'conditions',
        'isAlive',
        'isConscious',
    }
    assert victim_combat['hp'] == {'current': 12, 'max': 24}
    assert 'stateChangeLedger' not in snapshot
    assert snapshot['flags'] == {
        'campaignPackActiveCheckpointId': 'cp_start',
        'campaignPackCompletedCheckpointIds': [],
        'campaignPackSkippedCheckpointIds': [],
        'campaignPackFailedCheckpointIds': [],
        'campaignPackProgressRevision': 2,
    }
    assert 'catalog' not in snapshot['campaignPack']
    assert 'directorRules' not in snapshot['campaignPack']
    assert 'VICTIM_PRIVATE' not in json.dumps(snapshot)
    assert 'VICTIM_RESOURCE' not in json.dumps(snapshot)
    assert session_list_response.status_code == 200
    assert 'ATTACKER_PRIVATE_ITEM' in json.dumps(session_list_response.get_json())
    assert 'VICTIM_PRIVATE' not in json.dumps(session_list_response.get_json())
    assert workspace_response.status_code == 200
    assert 'ATTACKER_PRIVATE_ITEM' in json.dumps(workspace_response.get_json())
    assert 'VICTIM_PRIVATE' not in json.dumps(workspace_response.get_json())


def test_session_export_scopes_player_detail_and_selection_but_admin_remains_complete(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_API_AUTH_TOKENS': 'token-123,session-privacy-table-test-key',
            'AIDM_API_AUTH_TOKEN_WORKSPACES': 'owner=session-privacy-table-test-key',
        },
    )
    client = app.test_client()
    ids = _seed_session_player_privacy_fixture(app)
    attacker_headers = {
        'Authorization': 'Bearer session-privacy-attacker-token',
        'X-AIDM-Workspace-Id': 'owner',
    }
    admin_headers = {
        'Authorization': 'Bearer session-privacy-admin-token',
        'X-AIDM-Workspace-Id': 'owner',
    }
    table_token_headers = {
        'Authorization': 'Bearer session-privacy-table-test-key',
        'X-AIDM-Workspace-Id': 'owner',
    }

    attacker_export = client.get(f"/api/sessions/{ids['session_id']}/export", headers=attacker_headers)
    victim_selection = client.get(
        f"/api/sessions/{ids['session_id']}/export?player_id={ids['victim_player_id']}",
        headers=attacker_headers,
    )
    own_selection = client.get(
        f"/api/sessions/{ids['session_id']}/export?player_id={ids['attacker_player_id']}",
        headers=attacker_headers,
    )
    admin_export = client.get(
        f"/api/sessions/{ids['session_id']}/export?player_id={ids['victim_player_id']}",
        headers=admin_headers,
    )
    table_token_selection = client.get(
        f"/api/sessions/{ids['session_id']}/export?player_id={ids['victim_player_id']}",
        headers=table_token_headers,
    )

    assert attacker_export.status_code == 200
    attacker_payload = attacker_export.get_json()
    assert attacker_payload['selectedPlayer']['player_id'] == ids['attacker_player_id']
    assert attacker_payload['selectedPlayer']['inventory'][0]['name'] == 'ATTACKER_PRIVATE_ITEM'
    attacker_players = {player['player_id']: player for player in attacker_payload['players']}
    assert attacker_players[ids['attacker_player_id']]['character_sheet']['privateNotes'] == 'ATTACKER_PRIVATE_SHEET'
    assert set(attacker_players[ids['victim_player_id']]) == {
        'player_id',
        'campaign_id',
        'name',
        'character_name',
        'race',
        'sex',
        'profile_image',
        'class_',
        'char_class',
        'level',
    }
    assert 'VICTIM_PRIVATE' not in json.dumps(attacker_payload)
    assert 'VICTIM_RESOURCE' not in json.dumps(attacker_payload)
    assert 'DM_ONLY_TRIGGER' not in json.dumps(attacker_payload)

    assert victim_selection.status_code == 404
    assert victim_selection.get_json()['error_code'] == 'player_not_found'
    assert own_selection.status_code == 200
    assert own_selection.get_json()['selectedPlayer']['player_id'] == ids['attacker_player_id']
    assert table_token_selection.status_code == 404
    assert table_token_selection.get_json()['error_code'] == 'player_not_found'

    assert admin_export.status_code == 200
    admin_payload = admin_export.get_json()
    assert admin_payload['selectedPlayer']['player_id'] == ids['victim_player_id']
    assert admin_payload['selectedPlayer']['inventory'][0]['name'] == 'VICTIM_PRIVATE_ITEM'
    assert admin_payload['selectedPlayer']['character_sheet']['privateNotes'] == 'VICTIM_PRIVATE_SHEET'
    admin_players = {player['player_id']: player for player in admin_payload['players']}
    assert admin_players[ids['victim_player_id']]['stats']['privateResource'] == 'VICTIM_RESOURCE'
    assert admin_players[ids['attacker_player_id']]['inventory'][0]['name'] == 'ATTACKER_PRIVATE_ITEM'
    assert 'DM_ONLY_LEDGER' in json.dumps(admin_payload['sessionState']['state_snapshot'])
    assert 'DM_ONLY_TRIGGER_REASON' in json.dumps(admin_payload['sessionState']['active_segments'])


def test_accountless_table_token_cannot_read_mutate_or_socket_bind_guessed_player(
    tmp_path,
    monkeypatch,
):
    app, socketio = _build_auth_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_API_AUTH_TOKENS': 'token-123,session-privacy-table-test-key',
            'AIDM_API_AUTH_TOKEN_WORKSPACES': 'owner=session-privacy-table-test-key',
        },
    )
    client = app.test_client()
    ids = _seed_session_player_privacy_fixture(app)
    table_token_headers = {
        'Authorization': 'Bearer session-privacy-table-test-key',
        'X-AIDM-Workspace-Id': 'owner',
    }
    admin_headers = {
        'Authorization': 'Bearer session-privacy-admin-token',
        'X-AIDM-Workspace-Id': 'owner',
    }

    party_response = client.get(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        headers=table_token_headers,
    )
    guessed_detail = client.get(
        f"/api/players/{ids['victim_player_id']}",
        headers=table_token_headers,
    )
    guessed_update = client.patch(
        f"/api/players/{ids['victim_player_id']}",
        headers=table_token_headers,
        json={'level': 20},
    )
    admin_detail = client.get(
        f"/api/players/{ids['victim_player_id']}",
        headers=admin_headers,
    )

    assert party_response.status_code == 200
    party_payload = party_response.get_json()
    assert {player['player_id'] for player in party_payload} == {
        ids['attacker_player_id'],
        ids['victim_player_id'],
    }
    assert 'VICTIM_PRIVATE' not in json.dumps(party_payload)
    assert 'VICTIM_RESOURCE' not in json.dumps(party_payload)
    assert guessed_detail.status_code == 404
    assert guessed_detail.get_json()['error_code'] == 'player_not_found'
    assert guessed_update.status_code == 404
    assert guessed_update.get_json()['error_code'] == 'player_not_found'
    assert admin_detail.status_code == 200
    assert admin_detail.get_json()['level'] == 4
    assert admin_detail.get_json()['stats']['privateResource'] == 'VICTIM_RESOURCE'

    table_socket = socketio.test_client(
        app,
        flask_test_client=app.test_client(),
        auth={'token': 'session-privacy-table-test-key'},
    )
    assert table_socket.is_connected()
    table_socket.emit(
        'join_session',
        {'session_id': ids['session_id'], 'player_id': ids['victim_player_id']},
    )
    join_events = table_socket.get_received()
    join_error = _socket_event_payload(join_events, 'error')
    assert join_error['error_code'] == 'invalid_player'
    assert _socket_event_payload(join_events, 'player_joined') is None


def test_player_cannot_read_raw_campaign_canon_but_admin_can(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    ids = _seed_session_player_privacy_fixture(app)
    with app.app_context():
        db.session.add(
            StoryEntity(
                campaign_id=ids['campaign_id'],
                session_id=ids['session_id'],
                entity_type='npc',
                name='Canon Keeper',
                summary='DM_ONLY_CANON_SUMMARY',
                metadata_json=safe_json_dumps({'directorNote': 'DM_ONLY_CANON_METADATA'}, {}),
            )
        )
        db.session.commit()

    player_headers = {
        'Authorization': 'Bearer session-privacy-attacker-token',
        'X-AIDM-Workspace-Id': 'owner',
    }
    admin_headers = {
        'Authorization': 'Bearer session-privacy-admin-token',
        'X-AIDM-Workspace-Id': 'owner',
    }
    player_response = client.get(
        f"/api/campaigns/{ids['campaign_id']}/canon",
        headers=player_headers,
    )
    admin_response = client.get(
        f"/api/campaigns/{ids['campaign_id']}/canon",
        headers=admin_headers,
    )

    assert player_response.status_code == 403
    assert player_response.get_json()['details']['required_capability'] == 'debug_read'
    assert admin_response.status_code == 200
    assert 'DM_ONLY_CANON_SUMMARY' in json.dumps(admin_response.get_json())
    assert 'DM_ONLY_CANON_METADATA' in json.dumps(admin_response.get_json())


def test_chronicle_exports_redact_director_metadata_for_players_but_preserve_admin_view(
    tmp_path,
    monkeypatch,
):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    ids = _seed_session_player_privacy_fixture(app)
    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['attacker_player_id'],
            player_input='PUBLIC_PLAYER_ACTION',
            dm_output='PUBLIC_DM_NARRATION',
            status='completed',
            outcome_status='resolved',
            llm_provider='DM_ONLY_PROVIDER',
            llm_model='DM_ONLY_MODEL',
            metadata_json=safe_json_dumps(
                {'state_pipeline': {'directorTrace': 'DM_ONLY_STATE_PIPELINE'}},
                {},
            ),
        )
        db.session.add(turn)
        db.session.flush()
        pack_session = CampaignPackSession(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            workspace_id='owner',
            pack_id='privacy_pack',
            pack_title='Privacy Pack',
            active_checkpoint_id='cp_revealed_chapter',
            progress_revision=7,
        )
        db.session.add(pack_session)
        db.session.flush()
        db.session.add(
            CampaignPackCheckpointProgress(
                campaign_pack_session_id=pack_session.campaign_pack_session_id,
                checkpoint_id='cp_revealed_chapter',
                title='Revealed Chapter',
                status='active',
                sort_order=1,
                progress_revision=7,
            )
        )
        progress_event = TurnEvent(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            turn_id=turn.turn_id,
            event_type='campaign_pack.progress.changed',
            payload_json=safe_json_dumps(
                {
                    'type': 'campaign_pack.progress.changed',
                    'action': 'DM_ONLY_ADVANCE_ACTION',
                    'fromCheckpointId': 'cp_previous',
                    'toCheckpointId': 'cp_revealed_chapter',
                    'progressRevision': 7,
                    'reason': 'DM_ONLY_PROGRESS_REASON',
                },
                {},
            ),
        )
        db.session.add(progress_event)
        db.session.commit()
        progress_event_id = progress_event.event_id
        turn_id = turn.turn_id

    player_headers = {
        'Authorization': 'Bearer session-privacy-attacker-token',
        'X-AIDM-Workspace-Id': 'owner',
    }
    admin_headers = {
        'Authorization': 'Bearer session-privacy-admin-token',
        'X-AIDM-Workspace-Id': 'owner',
    }
    paths = [
        f"/api/campaigns/{ids['campaign_id']}/chronicle",
        f"/api/sessions/{ids['session_id']}/chronicle",
    ]
    for path in paths:
        player_response = client.get(path, headers=player_headers)
        admin_response = client.get(path, headers=admin_headers)

        assert player_response.status_code == 200
        player_html = player_response.get_data(as_text=True)
        assert 'PUBLIC_PLAYER_ACTION' in player_html
        assert 'PUBLIC_DM_NARRATION' in player_html
        assert 'Revealed Chapter' in player_html
        assert 'DM_ONLY_ADVANCE_ACTION' not in player_html
        assert 'Dm Only Advance Action' not in player_html
        assert 'DM_ONLY_PROGRESS_REASON' not in player_html
        assert 'revision 7' not in player_html
        assert f'turn event {progress_event_id}' not in player_html
        assert 'DM_ONLY_PROVIDER/DM_ONLY_MODEL' not in player_html
        assert 'recorded state-pipeline metadata' not in player_html
        assert "Director's Commentary" not in player_html
        assert 'data-chapter-source="campaign-pack-progress"' not in player_html
        assert f'Turn {turn_id} |' not in player_html

        assert admin_response.status_code == 200
        admin_html = admin_response.get_data(as_text=True)
        assert 'PUBLIC_DM_NARRATION' in admin_html
        assert 'Revealed Chapter' in admin_html
        assert 'Dm Only Advance Action' in admin_html
        assert 'DM_ONLY_PROGRESS_REASON' in admin_html
        assert 'revision 7' in admin_html
        assert f'turn event {progress_event_id}' in admin_html
        assert 'DM_ONLY_PROVIDER/DM_ONLY_MODEL' in admin_html
        assert 'recorded state-pipeline metadata' in admin_html
        assert "Director's Commentary" in admin_html
        assert 'data-chapter-source="campaign-pack-progress"' in admin_html
        assert f'Turn {turn_id} |' in admin_html


def test_player_read_logs_events_and_exports_redact_peer_roll_provenance_but_keep_own_and_admin_detail(
    tmp_path,
    monkeypatch,
):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    ids = _seed_session_player_privacy_fixture(app)

    with app.app_context():
        own_turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['attacker_player_id'],
            player_input='Own roll',
            status='completed',
            client_message_id='own-private-roll',
        )
        peer_turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['victim_player_id'],
            player_input='Peer roll',
            status='completed',
            client_message_id='peer-private-roll',
        )
        db.session.add_all([own_turn, peer_turn])
        db.session.flush()
        own_payload = _private_roll_event_payload(
            client_message_id='own-private-roll',
            score=17,
            skill='OWN_PRIVATE_SKILL',
            wound_penalty=1,
        )
        peer_payload = _private_roll_event_payload(
            client_message_id='peer-private-roll',
            score=19,
            skill='PEER_PRIVATE_SKILL',
            wound_penalty=3,
        )
        peer_payload['metadata']['state_pipeline'] = {
            'clarificationRequest': {
                'originalAction': {'itemName': 'PEER_PRIVATE_CLARIFICATION_ITEM'},
                'options': [
                    {'id': 'private-item', 'label': 'PEER_PRIVATE_CLARIFICATION_ITEM'},
                ],
            },
            'preDmValidation': {
                'validatedActions': [
                    {'resolvedItem': {'itemName': 'PEER_PRIVATE_CLARIFICATION_ITEM'}},
                ],
            },
        }
        own_payload['metadata']['turn_id'] = own_turn.turn_id
        peer_payload['metadata']['turn_id'] = peer_turn.turn_id
        db.session.add_all(
            [
                TurnEvent(
                    session_id=ids['session_id'],
                    campaign_id=ids['campaign_id'],
                    turn_id=own_turn.turn_id,
                    player_id=ids['attacker_player_id'],
                    event_type='roll_resolved',
                    payload_json=safe_json_dumps(own_payload, {}),
                ),
                TurnEvent(
                    session_id=ids['session_id'],
                    campaign_id=ids['campaign_id'],
                    turn_id=peer_turn.turn_id,
                    player_id=ids['victim_player_id'],
                    event_type='roll_resolved',
                    payload_json=safe_json_dumps(peer_payload, {}),
                ),
                SessionLogEntry(
                    session_id=ids['session_id'],
                    message='Own authoritative roll resolved.',
                    entry_type='dm',
                    metadata_json=safe_json_dumps(own_payload['metadata'], {}),
                ),
                SessionLogEntry(
                    session_id=ids['session_id'],
                    message='Peer authoritative roll resolved.',
                    entry_type='dm',
                    metadata_json=safe_json_dumps(peer_payload['metadata'], {}),
                ),
            ]
        )
        db.session.commit()

    attacker_headers = {
        'Authorization': 'Bearer session-privacy-attacker-token',
        'X-AIDM-Workspace-Id': 'owner',
    }
    admin_headers = {
        'Authorization': 'Bearer session-privacy-admin-token',
        'X-AIDM-Workspace-Id': 'owner',
    }

    player_events = client.get(f"/api/sessions/{ids['session_id']}/events", headers=attacker_headers)
    player_log = client.get(f"/api/sessions/{ids['session_id']}/log", headers=attacker_headers)
    player_export = client.get(f"/api/sessions/{ids['session_id']}/export", headers=attacker_headers)
    admin_events = client.get(f"/api/sessions/{ids['session_id']}/events", headers=admin_headers)

    assert player_events.status_code == 200
    events_by_player = {
        event['player_id']: event['payload']
        for event in player_events.get_json()['events']
        if event['event_type'] == 'roll_resolved'
    }
    own_event = events_by_player[ids['attacker_player_id']]
    peer_event = events_by_player[ids['victim_player_id']]
    assert own_event['roll']['ability']['score'] == 17
    assert own_event['roll']['proficiency']['skills'] == ['OWN_PRIVATE_SKILL']
    assert own_event['roll']['modifier_breakdown']['wound_penalty'] == 1
    assert 'ability' not in peer_event['roll']
    assert 'proficiency' not in peer_event['roll']
    assert 'modifier_breakdown' not in peer_event['roll']
    assert 'dc_hint' not in peer_event['metadata']
    assert 'ability' not in peer_event['metadata']['authoritative_roll']
    assert 'proficiency' not in peer_event['metadata']['action_intent']['roll']
    assert 'modifier_breakdown' not in peer_event['metadata']['roll_gate']['roll_spec']
    assert 'PEER_PRIVATE_SKILL' not in json.dumps(player_events.get_json())
    assert 'PEER_PRIVATE_CLARIFICATION_ITEM' not in json.dumps(player_events.get_json())

    assert player_log.status_code == 200
    log_by_client_id = {
        entry['metadata'].get('client_message_id'): entry['metadata']
        for entry in player_log.get_json()['entries']
    }
    assert log_by_client_id['own-private-roll']['authoritative_roll']['ability']['score'] == 17
    assert 'ability' not in log_by_client_id['peer-private-roll']['authoritative_roll']
    assert 'dc_hint' not in log_by_client_id['peer-private-roll']
    assert 'PEER_PRIVATE_SKILL' not in json.dumps(player_log.get_json())
    assert 'PEER_PRIVATE_CLARIFICATION_ITEM' not in json.dumps(player_log.get_json())

    assert player_export.status_code == 200
    export_payload = player_export.get_json()
    assert 'OWN_PRIVATE_SKILL' in json.dumps(export_payload['turnEvents'])
    assert 'OWN_PRIVATE_SKILL' in json.dumps(export_payload['logEntries'])
    assert 'PEER_PRIVATE_SKILL' not in json.dumps(export_payload['turnEvents'])
    assert 'PEER_PRIVATE_SKILL' not in json.dumps(export_payload['logEntries'])
    assert 'PEER_PRIVATE_CLARIFICATION_ITEM' not in json.dumps(export_payload['turnEvents'])
    assert 'PEER_PRIVATE_CLARIFICATION_ITEM' not in json.dumps(export_payload['logEntries'])

    assert admin_events.status_code == 200
    assert 'PEER_PRIVATE_SKILL' in json.dumps(admin_events.get_json())
    assert 'DM_ONLY_METADATA_HINT_peer-private-roll' in json.dumps(admin_events.get_json())
    assert 'PEER_PRIVATE_CLARIFICATION_ITEM' in json.dumps(admin_events.get_json())


def test_socket_room_roll_events_redact_peer_provenance_and_keep_sender_receipt(
    tmp_path,
    monkeypatch,
):
    app, socketio = _build_auth_runtime(tmp_path, monkeypatch)
    ids = _seed_session_player_privacy_fixture(app)
    with app.app_context():
        victim = db.session.get(Player, ids['victim_player_id'])
        victim.stats = safe_json_dumps(
            {
                'charisma': 18,
                'skill_proficiencies': ['Persuasion'],
                'current_hp': 5,
                'hp_current': 5,
                'max_hp': 20,
                'hp_max': 20,
            },
            {},
        )
        db.session.commit()

    import aidm_server.blueprints.socketio_events as socketio_events_module
    import aidm_server.player_rolls as player_rolls_module

    def fake_stream(user_input, context, speaking_player=None, rules_hint=None):
        del user_input, context, speaking_player, rules_hint
        yield 'The guard weighs the argument.'

    monkeypatch.setattr(socketio_events_module, 'query_dm_function_stream', fake_stream)
    monkeypatch.setattr(player_rolls_module.secrets, 'randbelow', lambda _sides: 11)

    attacker_client = socketio.test_client(
        app,
        flask_test_client=app.test_client(),
        auth={'account_token': 'session-privacy-attacker-token', 'workspace_id': 'owner'},
    )
    victim_client = socketio.test_client(
        app,
        flask_test_client=app.test_client(),
        auth={'account_token': 'session-privacy-victim-token', 'workspace_id': 'owner'},
    )
    assert attacker_client.is_connected()
    assert victim_client.is_connected()
    attacker_client.emit(
        'join_session',
        {'session_id': ids['session_id'], 'player_id': ids['attacker_player_id']},
    )
    victim_client.emit(
        'join_session',
        {'session_id': ids['session_id'], 'player_id': ids['victim_player_id']},
    )
    attacker_client.get_received()
    victim_client.get_received()

    victim_client.emit(
        'send_message',
        {
            'session_id': ids['session_id'],
            'campaign_id': ids['campaign_id'],
            'player_id': ids['victim_player_id'],
            'message': 'I persuade the guard to let us pass.',
            'client_message_id': 'victim-private-socket-roll',
            'action_intent': {
                'kind': 'roll',
                'source': 'dice_roller',
                'text': 'I persuade the guard to let us pass.',
                'client_message_id': 'victim-private-socket-roll',
                'ability': {'key': 'charisma', 'label': 'CHA'},
                'roll': {
                    'die': 'd20',
                    'mode': 'normal',
                    'reason': 'persuade the guard',
                    'result_visibility': 'visible',
                },
            },
        },
    )
    sender_events = victim_client.get_received()
    peer_events = attacker_client.get_received()

    sender_roll = _socket_event_payload(sender_events, 'roll_resolved')
    peer_roll = _socket_event_payload(peer_events, 'roll_resolved')
    assert sender_roll['ability']['score'] == 18
    assert sender_roll['proficiency']['skills'] == ['persuasion']
    assert sender_roll['modifier_breakdown']['wound_penalty'] == 4
    assert peer_roll['total'] == sender_roll['total']
    assert 'ability' not in peer_roll
    assert 'proficiency' not in peer_roll
    assert 'modifier_breakdown' not in peer_roll

    sender_message = _socket_event_payload(sender_events, 'new_message')
    peer_message = _socket_event_payload(peer_events, 'new_message')
    assert sender_message['action_intent']['ability']['score'] == 18
    assert sender_message['rules_hint']['roll_spec']['ability']['score'] == 18
    assert 'dc_hint' in sender_message['rules_hint']
    assert peer_message['action_intent']['ability'] == {'key': 'charisma', 'label': 'CHA'}
    assert 'dc_hint' not in peer_message['rules_hint']
    assert 'ability' not in peer_message['rules_hint']['roll_spec']
    assert 'proficiency' not in peer_message['rules_hint']['roll_spec']
    assert 'modifier_breakdown' not in peer_message['rules_hint']['roll_spec']
    assert 'task_dc' not in peer_message['rules_hint']['roll_spec']
    assert 'ability' not in peer_message['rules_hint']['authoritative_roll']

    for socket_event in peer_events:
        if socket_event['name'] not in {'dm_response_start', 'dm_chunk', 'dm_response_end'}:
            continue
        payload = socket_event['args'][0]
        rules_hint = payload['rules_hint']
        assert 'dc_hint' not in rules_hint
        if isinstance(rules_hint.get('roll_spec'), dict):
            assert 'ability' not in rules_hint['roll_spec']
            assert 'proficiency' not in rules_hint['roll_spec']
            assert 'modifier_breakdown' not in rules_hint['roll_spec']
            assert 'task_dc' not in rules_hint['roll_spec']
        if isinstance(rules_hint.get('authoritative_roll'), dict):
            assert 'ability' not in rules_hint['authoritative_roll']
            assert 'proficiency' not in rules_hint['authoritative_roll']
            assert 'modifier_breakdown' not in rules_hint['authoritative_roll']

    attacker_client.disconnect()
    victim_client.disconnect()


def test_session_import_non_admin_cannot_attribute_actions_to_other_players(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    client = app.test_client()
    with app.app_context():
        attacker = Account(
            username='session-import-attacker',
            first_name='Session',
            last_name='Attacker',
            password_hash='configured',
            account_token_hash=hash_secret('session-import-attacker-token'),
        )
        victim = Account(
            username='session-import-victim',
            first_name='Session',
            last_name='Victim',
            password_hash='configured',
            account_token_hash=hash_secret('session-import-victim-token'),
        )
        db.session.add_all([attacker, victim])
        db.session.flush()
        db.session.add_all(
            [
                AccountWorkspaceMembership(account_id=attacker.account_id, workspace_id='owner', role='player'),
                AccountWorkspaceMembership(account_id=victim.account_id, workspace_id='owner', role='player'),
            ]
        )
        world = World(name='Session Import Attribution World', description='auth import attribution')
        db.session.add(world)
        db.session.flush()
        target_campaign = Campaign(
            title='Session Import Attribution Target',
            world_id=world.world_id,
            workspace_id='owner',
        )
        other_campaign = Campaign(
            title='Session Import Attribution Other',
            world_id=world.world_id,
            workspace_id='owner',
        )
        db.session.add_all([target_campaign, other_campaign])
        db.session.flush()
        attacker_player = Player(
            campaign_id=target_campaign.campaign_id,
            workspace_id='owner',
            account_id=attacker.account_id,
            name='Session Attacker',
            character_name='Attacker Hero',
        )
        victim_same_campaign = Player(
            campaign_id=target_campaign.campaign_id,
            workspace_id='owner',
            account_id=victim.account_id,
            name='Session Victim',
            character_name='Victim Same Hero',
        )
        victim_other_campaign = Player(
            campaign_id=other_campaign.campaign_id,
            workspace_id='owner',
            account_id=victim.account_id,
            name='Session Victim',
            character_name='Victim Other Hero',
        )
        db.session.add_all([attacker_player, victim_same_campaign, victim_other_campaign])
        db.session.commit()
        ids = {
            'world_id': world.world_id,
            'target_campaign_id': target_campaign.campaign_id,
            'other_campaign_id': other_campaign.campaign_id,
            'attacker_player_id': attacker_player.player_id,
            'victim_same_campaign_id': victim_same_campaign.player_id,
            'victim_other_campaign_id': victim_other_campaign.player_id,
        }

    headers = {
        'Authorization': 'Bearer session-import-attacker-token',
        'X-AIDM-Workspace-Id': 'owner',
    }

    def import_player_event(label: str, player_id: int, *, request_headers: dict[str, str] | None = None):
        return client.post(
            '/api/sessions/import',
            headers=request_headers or headers,
            json={
                'campaign_id': ids['target_campaign_id'],
                'name': f'Session attribution {label}',
                'turnEvents': [
                    {
                        'event_type': 'player_message',
                        'player_id': player_id,
                        'payload': {'speaker': label, 'message': f'ATTRIBUTION_MARKER_{label}'},
                        'created_at': '2099-01-01T00:00:00+00:00',
                    }
                ],
            },
        )

    own_response = import_player_event('OWN', ids['attacker_player_id'])
    same_campaign_response = import_player_event('SAME_CAMPAIGN_VICTIM', ids['victim_same_campaign_id'])
    other_campaign_response = import_player_event('OTHER_CAMPAIGN_VICTIM', ids['victim_other_campaign_id'])

    assert own_response.status_code == 201
    assert same_campaign_response.status_code == 201
    assert other_campaign_response.status_code == 201
    own_session_id = own_response.get_json()['session_id']
    same_campaign_session_id = same_campaign_response.get_json()['session_id']
    other_campaign_session_id = other_campaign_response.get_json()['session_id']

    with app.app_context():
        own_event = TurnEvent.query.filter_by(session_id=own_session_id).one()
        same_campaign_event = TurnEvent.query.filter_by(session_id=same_campaign_session_id).one()
        other_campaign_event = TurnEvent.query.filter_by(session_id=other_campaign_session_id).one()

        assert own_event.player_id == ids['attacker_player_id']
        assert PlayerAction.query.filter_by(
            session_id=own_session_id,
            player_id=ids['attacker_player_id'],
        ).count() == 1
        assert same_campaign_event.player_id is None
        assert other_campaign_event.player_id is None
        assert PlayerAction.query.filter(
            PlayerAction.session_id.in_([same_campaign_session_id, other_campaign_session_id])
        ).count() == 0

        from aidm_server.llm_context import build_dm_context

        same_campaign_context = json.loads(
            build_dm_context(
                ids['world_id'],
                ids['target_campaign_id'],
                active_player_ids=[ids['victim_same_campaign_id']],
                current_player_id=ids['victim_same_campaign_id'],
            )
        )
        other_campaign_context = json.loads(
            build_dm_context(
                ids['world_id'],
                ids['other_campaign_id'],
                active_player_ids=[ids['victim_other_campaign_id']],
                current_player_id=ids['victim_other_campaign_id'],
            )
        )
        same_victim = same_campaign_context['active_players'][0]
        other_victim = other_campaign_context['active_players'][0]

    assert 'ATTRIBUTION_MARKER_SAME_CAMPAIGN_VICTIM' not in same_victim['recent_actions']
    assert 'ATTRIBUTION_MARKER_OTHER_CAMPAIGN_VICTIM' not in other_victim['recent_actions']

    operator_headers = {'Authorization': 'Bearer token-123'}
    operator_same_campaign_response = import_player_event(
        'OPERATOR_SAME_CAMPAIGN',
        ids['victim_same_campaign_id'],
        request_headers=operator_headers,
    )
    operator_other_campaign_response = import_player_event(
        'OPERATOR_OTHER_CAMPAIGN',
        ids['victim_other_campaign_id'],
        request_headers=operator_headers,
    )

    assert operator_same_campaign_response.status_code == 201
    assert operator_other_campaign_response.status_code == 201
    operator_same_session_id = operator_same_campaign_response.get_json()['session_id']
    operator_other_session_id = operator_other_campaign_response.get_json()['session_id']
    with app.app_context():
        operator_same_event = TurnEvent.query.filter_by(session_id=operator_same_session_id).one()
        operator_other_event = TurnEvent.query.filter_by(session_id=operator_other_session_id).one()

        assert operator_same_event.player_id == ids['victim_same_campaign_id']
        assert PlayerAction.query.filter_by(
            session_id=operator_same_session_id,
            player_id=ids['victim_same_campaign_id'],
        ).count() == 1
        assert operator_other_event.player_id is None
        assert PlayerAction.query.filter_by(session_id=operator_other_session_id).count() == 0


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
