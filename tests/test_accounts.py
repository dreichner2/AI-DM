from __future__ import annotations

import importlib
from unittest.mock import Mock

from sqlalchemy import event

from aidm_server.auth import (
    generate_account_token,
    hash_secret,
    issue_legacy_recovery_token,
    normalize_username,
    password_hash_matches,
)
from aidm_server.blueprints.accounts import LEGACY_PASSWORD_SETUP_MESSAGE
from aidm_server.database import db
from aidm_server.models import (
    Account,
    AccountWorkspaceMembership,
    Campaign,
    CampaignPack,
    CampaignPackCheckpointProgress,
    CampaignPackProgressEvent,
    CampaignPackRecord,
    CampaignPackSession,
    InstalledCampaignPack,
    OperatorActionAudit,
    Player,
    RateLimitEvent,
    Session,
    Workspace,
    World,
)


def _build_account_runtime(tmp_path, monkeypatch, extra_env: dict[str, str] | None = None):
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
    monkeypatch.setenv('AIDM_PREAUTH_RATE_LIMIT_MAX_IP_TARGET_ATTEMPTS', '1000')
    monkeypatch.setenv('AIDM_PREAUTH_RATE_LIMIT_MAX_IP_ATTEMPTS', '1000')
    monkeypatch.setenv('AIDM_PREAUTH_RATE_LIMIT_MAX_TARGET_ATTEMPTS', '1000')
    for key, value in (extra_env or {}).items():
        monkeypatch.setenv(key, value)

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


def _strict_preauth_env(*, trusted_proxy_count: int = 0) -> dict[str, str]:
    return {
        'AIDM_PREAUTH_RATE_LIMIT_WINDOW_SECONDS': '60',
        'AIDM_PREAUTH_RATE_LIMIT_MAX_IP_TARGET_ATTEMPTS': '1',
        'AIDM_PREAUTH_RATE_LIMIT_MAX_IP_ATTEMPTS': '20',
        'AIDM_PREAUTH_RATE_LIMIT_MAX_TARGET_ATTEMPTS': '20',
        'AIDM_TRUSTED_PROXY_COUNT': str(trusted_proxy_count),
    }


def _csrf_header_from_response(response) -> dict[str, str]:
    csrf_cookie = next(
        value
        for value in response.headers.getlist('Set-Cookie')
        if value.startswith('aidm_csrf_token=')
    )
    return {'X-AIDM-CSRF-Token': csrf_cookie.split(';', 1)[0].split('=', 1)[1]}


def test_account_login_is_rate_limited_before_second_password_verification(
    tmp_path,
    monkeypatch,
):
    app = _build_account_runtime(
        tmp_path,
        monkeypatch,
        extra_env=_strict_preauth_env(),
    )
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

    verifier = Mock(return_value=False)
    monkeypatch.setattr('aidm_server.blueprints.accounts.password_matches', verifier)
    first = client.post(
        '/api/accounts/login',
        headers={'X-Forwarded-For': '198.51.100.10'},
        json={'username': 'Danny', 'password': 'wrong', 'intent': 'login'},
    )
    second = client.post(
        '/api/accounts/login',
        headers={'X-Forwarded-For': '203.0.113.20'},
        json={'username': ' danny ', 'password': 'wrong-again', 'intent': 'login'},
    )

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.get_json()['error_code'] == 'rate_limited'
    verifier.assert_called_once()


def test_unknown_account_login_is_rate_limited_before_second_lookup(tmp_path, monkeypatch):
    app = _build_account_runtime(
        tmp_path,
        monkeypatch,
        extra_env=_strict_preauth_env(),
    )
    client = app.test_client()
    account_selects: list[str] = []

    def capture_account_select(_conn, _cursor, statement, _parameters, _context, _executemany):
        if 'from accounts' in statement.casefold():
            account_selects.append(statement)

    with app.app_context():
        event.listen(db.engine, 'before_cursor_execute', capture_account_select)

    try:
        first = _login(
            client,
            username='Missing_User',
            first_name='',
            last_name='',
            password='wrong',
            intent='login',
        )
        second = _login(
            client,
            username=' missing_user ',
            first_name='',
            last_name='',
            password='wrong-again',
            intent='login',
        )
    finally:
        with app.app_context():
            event.remove(db.engine, 'before_cursor_execute', capture_account_select)

    assert first.status_code == 404
    assert first.get_json()['error_code'] == 'username_not_found'
    assert second.status_code == 429
    assert second.get_json()['error_code'] == 'rate_limited'
    assert len(account_selects) == 1


def test_account_login_uses_trusted_proxy_ip_for_preauth_buckets(tmp_path, monkeypatch):
    app = _build_account_runtime(
        tmp_path,
        monkeypatch,
        extra_env=_strict_preauth_env(trusted_proxy_count=1),
    )
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

    verifier = Mock(return_value=False)
    monkeypatch.setattr('aidm_server.blueprints.accounts.password_matches', verifier)
    first = client.post(
        '/api/accounts/login',
        headers={'X-Forwarded-For': '198.51.100.10'},
        json={'username': 'Danny', 'password': 'wrong', 'intent': 'login'},
    )
    second = client.post(
        '/api/accounts/login',
        headers={'X-Forwarded-For': '203.0.113.20'},
        json={'username': 'Danny', 'password': 'wrong', 'intent': 'login'},
    )
    repeated_ip = client.post(
        '/api/accounts/login',
        headers={'X-Forwarded-For': '203.0.113.20'},
        json={'username': 'Danny', 'password': 'wrong', 'intent': 'login'},
    )

    assert first.status_code == 401
    assert second.status_code == 401
    assert repeated_ip.status_code == 429
    assert verifier.call_count == 2


def test_account_login_ip_bucket_blocks_username_spraying(tmp_path, monkeypatch):
    app = _build_account_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            **_strict_preauth_env(),
            'AIDM_PREAUTH_RATE_LIMIT_MAX_IP_TARGET_ATTEMPTS': '20',
            'AIDM_PREAUTH_RATE_LIMIT_MAX_IP_ATTEMPTS': '1',
        },
    )
    client = app.test_client()

    first = _login(
        client,
        username='Missing_One',
        first_name='',
        last_name='',
        password='wrong',
        intent='login',
    )
    rotated_target = _login(
        client,
        username='Missing_Two',
        first_name='',
        last_name='',
        password='wrong',
        intent='login',
    )

    assert first.status_code == 404
    assert rotated_target.status_code == 429


def test_account_login_target_bucket_blocks_distributed_attempts(tmp_path, monkeypatch):
    app = _build_account_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            **_strict_preauth_env(trusted_proxy_count=1),
            'AIDM_PREAUTH_RATE_LIMIT_MAX_IP_TARGET_ATTEMPTS': '20',
            'AIDM_PREAUTH_RATE_LIMIT_MAX_TARGET_ATTEMPTS': '1',
        },
    )
    client = app.test_client()
    payload = {
        'username': 'Missing_User',
        'first_name': '',
        'last_name': '',
        'password': 'wrong',
        'intent': 'login',
    }

    first = client.post(
        '/api/accounts/login',
        headers={'X-Forwarded-For': '198.51.100.10'},
        json=payload,
    )
    rotated_ip = client.post(
        '/api/accounts/login',
        headers={'X-Forwarded-For': '203.0.113.20'},
        json=payload,
    )

    assert first.status_code == 404
    assert rotated_ip.status_code == 429


def test_name_only_legacy_claim_is_not_account_recovery_proof(tmp_path, monkeypatch):
    app = _build_account_runtime(
        tmp_path,
        monkeypatch,
        extra_env=_strict_preauth_env(trusted_proxy_count=1),
    )
    client = app.test_client()
    _create_legacy_passwordless_account(
        app,
        username='Maya',
        first_name='Maya',
        last_name='Stone',
    )

    response = client.post(
        '/api/accounts/login',
        headers={'X-Forwarded-For': '198.51.100.10'},
        json={
            'username': 'Maya',
            'first_name': 'Maya',
            'last_name': 'Stone',
            'password': 'new-secret',
            'intent': 'signup',
            'legacy_claim': True,
        },
    )

    assert response.status_code == 401
    assert response.get_json()['error_code'] == 'legacy_password_setup_required'
    with app.app_context():
        account = Account.query.filter_by(username='maya').one()
        assert account.password_hash is None


def test_operator_issued_legacy_recovery_bypasses_saturated_weak_claim_target(
    tmp_path,
    monkeypatch,
):
    app = _build_account_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            **_strict_preauth_env(trusted_proxy_count=1),
            'AIDM_PREAUTH_RATE_LIMIT_MAX_IP_TARGET_ATTEMPTS': '5',
        },
    )
    client = app.test_client()
    stale_maya_token = _create_legacy_passwordless_account(
        app,
        username='Maya',
        first_name='Maya',
        last_name='Stone',
    )
    with app.app_context():
        account = Account.query.filter_by(username='maya').one()
        recovery_code = issue_legacy_recovery_token(account)
        db.session.commit()
    telemetry = Mock()
    monkeypatch.setattr('aidm_server.blueprints.accounts.telemetry_event', telemetry)

    weak_claim = {
        'username': 'Maya',
        'first_name': 'Mallory',
        'last_name': 'Stone',
        'password': 'dummy-attacker-selected-password',
        'intent': 'signup',
        'legacy_claim': True,
    }
    attacker_responses = []
    for source_index in range(1, 5):
        attacker_responses.extend(
            client.post(
                '/api/accounts/login',
                headers={'X-Forwarded-For': f'198.51.100.{source_index}'},
                json=weak_claim,
            )
            for _ in range(5)
        )
    assert [response.status_code for response in attacker_responses] == [401] * 20

    blocked_name_claim = client.post(
        '/api/accounts/login',
        headers={'X-Forwarded-For': '203.0.113.1'},
        json={
            **weak_claim,
            'first_name': 'Maya',
            'password': 'owner-new-password',
        },
    )
    assert blocked_name_claim.status_code == 429
    assert blocked_name_claim.get_json()['error_code'] == 'rate_limited'

    recovery = client.post(
        '/api/accounts/login',
        headers={
            'Authorization': f'Bearer {recovery_code}',
            'X-Forwarded-For': '203.0.113.2',
        },
        json={
            'username': 'Maya',
            'password': 'owner-new-password',
            'intent': 'signup',
            'legacy_recovery': True,
        },
    )
    assert recovery.status_code == 200
    replacement_token = recovery.get_json()['account_token']
    assert replacement_token
    assert replacement_token not in {recovery_code, stale_maya_token}
    with app.app_context():
        account = Account.query.filter_by(username='maya').one()
        assert account.password_hash is not None
        assert account.account_token_hash == hash_secret(replacement_token)

    assert telemetry.call_count == 1
    for telemetry_call in telemetry.call_args_list:
        assert telemetry_call.args == ('auth.preauth_rate_limited',)
        assert telemetry_call.kwargs['severity'] == 'warning'
        payload = telemetry_call.kwargs['payload']
        assert set(payload) == {'action', 'dimension', 'reset_in_seconds'}
        assert payload['action'] == 'account-legacy-claim'
        assert payload['dimension'] == 'target'
        assert 1 <= payload['reset_in_seconds'] <= 60
    rendered_telemetry = repr(telemetry.call_args_list)
    for raw_value in ('Maya', 'maya', recovery_code, stale_maya_token, replacement_token):
        assert raw_value not in rendered_telemetry

    replay = client.post(
        '/api/accounts/login',
        headers={'Authorization': f'Bearer {recovery_code}'},
        json={'username': 'Maya', 'password': 'wrong-password', 'intent': 'login'},
    )
    assert replay.status_code == 401


def test_workspace_password_is_rate_limited_before_second_hash_verification(
    tmp_path,
    monkeypatch,
):
    app = _build_account_runtime(
        tmp_path,
        monkeypatch,
        extra_env=_strict_preauth_env(),
    )
    client = app.test_client()
    signup = _login(
        client,
        username='Maya',
        first_name='Maya',
        last_name='Stone',
        password='secret',
        intent='signup',
    )
    assert signup.status_code == 201
    account_token = signup.get_json()['account_token']
    headers = {'Authorization': f'Bearer {account_token}'}
    create_workspace = client.post(
        '/api/accounts/workspaces',
        headers=headers,
        json={
            'table_name': 'Friday Night',
            'table_password': 'table-secret',
            'access_mode': 'password',
        },
    )
    assert create_workspace.status_code == 201

    verifier = Mock(return_value=False)
    monkeypatch.setattr('aidm_server.blueprints.accounts.password_hash_matches', verifier)
    first = client.post(
        '/api/accounts/workspace',
        headers=headers,
        json={'table_name': 'Friday Night', 'table_password': 'wrong'},
    )
    second = client.post(
        '/api/accounts/workspace',
        headers=headers,
        json={'table_name': 'Friday_Night', 'table_password': 'wrong-again'},
    )

    assert first.status_code == 401
    assert second.status_code == 429
    verifier.assert_called_once()


def test_workspace_password_target_limit_is_isolated_per_account(tmp_path, monkeypatch):
    app = _build_account_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_PREAUTH_RATE_LIMIT_WINDOW_SECONDS': '60',
            'AIDM_PREAUTH_RATE_LIMIT_MAX_IP_TARGET_ATTEMPTS': '5',
            'AIDM_PREAUTH_RATE_LIMIT_MAX_IP_ATTEMPTS': '20',
            'AIDM_PREAUTH_RATE_LIMIT_MAX_TARGET_ATTEMPTS': '20',
            'AIDM_RATE_LIMIT_STORE': 'database',
            'AIDM_TRUSTED_PROXY_COUNT': '1',
        },
    )
    client = app.test_client()
    owner = _login(
        client,
        username='Owner',
        first_name='Table',
        last_name='Owner',
        password='owner-secret',
        intent='signup',
    )
    attacker = _login(
        client,
        username='Attacker',
        first_name='Table',
        last_name='Attacker',
        password='attacker-secret',
        intent='signup',
    )
    victim = _login(
        client,
        username='Victim',
        first_name='Table',
        last_name='Victim',
        password='victim-secret',
        intent='signup',
    )
    assert {owner.status_code, attacker.status_code, victim.status_code} == {201}

    create_workspace = client.post(
        '/api/accounts/workspaces',
        headers={'Authorization': f"Bearer {owner.get_json()['account_token']}"},
        json={
            'table_name': 'Friday Night',
            'table_password': 'table-secret',
            'access_mode': 'password',
        },
    )
    assert create_workspace.status_code == 201
    workspace_id = create_workspace.get_json()['workspace_id']
    attacker_headers = {'Authorization': f"Bearer {attacker.get_json()['account_token']}"}
    victim_headers = {'Authorization': f"Bearer {victim.get_json()['account_token']}"}

    verifier = Mock(side_effect=password_hash_matches)
    monkeypatch.setattr('aidm_server.blueprints.accounts.password_hash_matches', verifier)
    attacker_responses = []
    for source_index in range(1, 5):
        for attempt_index in range(5):
            attacker_responses.append(
                client.post(
                    '/api/accounts/workspace',
                    headers={
                        **attacker_headers,
                        'X-Forwarded-For': f'198.51.100.{source_index}',
                    },
                    json={
                        'table_name': 'Friday Night' if attempt_index % 2 == 0 else workspace_id,
                        'table_password': 'wrong-password',
                    },
                )
            )
    assert [response.status_code for response in attacker_responses] == [401] * 20
    assert verifier.call_count == 20

    attacker_blocked = client.post(
        '/api/accounts/workspace',
        headers={**attacker_headers, 'X-Forwarded-For': '203.0.113.10'},
        json={'table_name': workspace_id, 'table_password': 'still-wrong'},
    )
    assert attacker_blocked.status_code == 429
    assert verifier.call_count == 20

    victim_join = client.post(
        '/api/accounts/workspace',
        headers={**victim_headers, 'X-Forwarded-For': '203.0.113.20'},
        json={'table_name': 'Friday Night', 'table_password': 'table-secret'},
    )
    assert victim_join.status_code == 200
    assert verifier.call_count == 21
    with app.app_context():
        victim_account = Account.query.filter_by(username='victim').one()
        assert AccountWorkspaceMembership.query.filter_by(
            account_id=victim_account.account_id,
            workspace_id=workspace_id,
        ).one_or_none() is not None
        bucket_keys = {
            row.bucket_key
            for row in RateLimitEvent.query.all()
            if row.bucket_key.startswith('preauth:')
        }
    rendered_keys = repr(bucket_keys)
    for raw_value in (
        'Friday Night',
        workspace_id,
        'wrong-password',
        'table-secret',
        attacker.get_json()['account_token'],
        victim.get_json()['account_token'],
        '198.51.100.1',
        '203.0.113.20',
    ):
        assert raw_value not in rendered_keys


def test_invalid_workspace_token_is_rate_limited_before_second_lookup(
    tmp_path,
    monkeypatch,
):
    app = _build_account_runtime(
        tmp_path,
        monkeypatch,
        extra_env=_strict_preauth_env(),
    )
    client = app.test_client()
    signup = _login(
        client,
        username='Aidan',
        first_name='Aidan',
        last_name='Fernandez',
        password='secret',
        intent='signup',
    )
    assert signup.status_code == 201
    headers = {'Authorization': f"Bearer {signup.get_json()['account_token']}"}

    verifier = Mock(return_value=None)
    monkeypatch.setattr('aidm_server.blueprints.accounts._validate_workspace_token', verifier)
    first = client.post(
        '/api/accounts/workspace',
        headers=headers,
        json={'workspace_token': 'raw-invalid-workspace-token'},
    )
    second = client.post(
        '/api/accounts/workspace',
        headers=headers,
        json={'workspace_token': 'raw-invalid-workspace-token'},
    )

    assert first.status_code == 401
    assert second.status_code == 429
    verifier.assert_called_once()
    bucket_keys = {
        key
        for extension_name in (
            'aidm_preauth_ip_target_limiter',
            'aidm_preauth_ip_limiter',
            'aidm_preauth_target_limiter',
        )
        for key in app.extensions[extension_name]._events
    }
    assert bucket_keys
    assert all('raw-invalid-workspace-token' not in key for key in bucket_keys)
    assert all('127.0.0.1' not in key for key in bucket_keys)


def test_preauth_limiters_use_the_configured_database_store(tmp_path, monkeypatch):
    from aidm_server.rate_limiter import DatabaseRateLimitStore

    app = _build_account_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            **_strict_preauth_env(),
            'AIDM_RATE_LIMIT_STORE': 'database',
        },
    )

    for extension_name in (
        'aidm_preauth_ip_target_limiter',
        'aidm_preauth_ip_limiter',
        'aidm_preauth_target_limiter',
    ):
        assert isinstance(app.extensions[extension_name].store, DatabaseRateLimitStore)

    client = app.test_client()
    first = _login(
        client,
        username='Missing_User',
        first_name='',
        last_name='',
        password='wrong',
        intent='login',
    )
    second = _login(
        client,
        username='Missing_User',
        first_name='',
        last_name='',
        password='wrong-again',
        intent='login',
    )
    assert first.status_code == 404
    assert second.status_code == 429


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
    assert worlds_response.status_code == 403
    assert worlds_response.get_json()['details']['required_capability'] == 'dm_authoring'

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


def test_cookie_auth_can_run_account_and_workspace_flow_without_token_response(tmp_path, monkeypatch):
    app = _build_account_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_ACCOUNT_COOKIE_AUTH_ENABLED': 'true',
            'AIDM_ACCOUNT_COOKIE_SECURE': 'false',
            'AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED': 'false',
        },
    )
    client = app.test_client()

    login = _login(
        client,
        username='CookiePlayer',
        first_name='Cookie',
        last_name='Player',
        password='secret',
        intent='signup',
    )

    assert login.status_code == 201
    login_payload = login.get_json()
    assert login_payload['account_token'] == ''
    assert login_payload['account_token_transport'] == 'http_only_cookie'
    assert 'aidm_account_session=' in login.headers.get('Set-Cookie', '')
    assert 'HttpOnly' in login.headers.get('Set-Cookie', '')
    assert any(value.startswith('aidm_csrf_token=') for value in login.headers.getlist('Set-Cookie'))
    csrf_headers = _csrf_header_from_response(login)

    missing_csrf = client.post('/api/accounts/workspace', json={'workspace_token': 'owner-token'})
    assert missing_csrf.status_code == 403
    assert missing_csrf.get_json()['error_code'] == 'csrf_required'

    join_owner = client.post('/api/accounts/workspace', headers=csrf_headers, json={'workspace_token': 'owner-token'})
    assert join_owner.status_code == 200
    join_payload = join_owner.get_json()
    assert join_payload['account_token'] == ''
    assert join_payload['workspace_id'] == 'owner'

    worlds_response = client.post(
        '/api/worlds',
        headers={**csrf_headers, 'X-AIDM-Workspace-Token': 'owner-token'},
        json={'name': 'Cookie Auth World'},
    )
    assert worlds_response.status_code == 403
    assert worlds_response.get_json()['details']['required_capability'] == 'dm_authoring'

    logout = client.delete('/api/accounts/session', headers=csrf_headers)
    assert logout.status_code == 200
    assert 'aidm_account_session=;' in logout.headers.get('Set-Cookie', '')

    after_logout = client.get('/api/accounts/me')
    assert after_logout.status_code == 401


def test_operator_recovery_code_becomes_rotated_http_only_cookie_session(tmp_path, monkeypatch):
    app = _build_account_runtime(
        tmp_path,
        monkeypatch,
        extra_env={
            'AIDM_ACCOUNT_COOKIE_AUTH_ENABLED': 'true',
            'AIDM_ACCOUNT_COOKIE_SECURE': 'false',
            'AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED': 'false',
        },
    )
    client = app.test_client()
    _create_legacy_passwordless_account(
        app,
        username='Maya',
        first_name='Maya',
        last_name='Stone',
    )
    with app.app_context():
        account = Account.query.filter_by(username='maya').one()
        recovery_code = issue_legacy_recovery_token(account)
        db.session.commit()

    recovery = client.post(
        '/api/accounts/login',
        headers={'Authorization': f'Bearer {recovery_code}'},
        json={
            'username': 'Maya',
            'password': 'new-secret',
            'intent': 'signup',
            'legacy_recovery': True,
        },
    )
    assert recovery.status_code == 200
    assert recovery.get_json()['account_token'] == ''
    assert recovery.get_json()['account_token_transport'] == 'http_only_cookie'
    assert 'aidm_account_session=' in recovery.headers.get('Set-Cookie', '')
    assert 'HttpOnly' in recovery.headers.get('Set-Cookie', '')
    assert client.get('/api/accounts/me').status_code == 200

    replay_client = app.test_client()
    replay = replay_client.post(
        '/api/accounts/login',
        headers={'Authorization': f'Bearer {recovery_code}'},
        json={'username': 'Maya', 'password': 'wrong-password', 'intent': 'login'},
    )
    assert replay.status_code == 401


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
        table_session = Session(campaign_id=table_campaign.campaign_id, state_snapshot='{}')
        db.session.add(table_session)
        installed_pack = InstalledCampaignPack(
            workspace_id='Friday_Night',
            pack_id='friday-pack',
            title='Friday Pack',
            pack_version='1.0.0',
            schema_version='1',
            pack_hash='a' * 64,
            manifest_json='{}',
        )
        db.session.add(installed_pack)
        db.session.flush()
        campaign_pack = CampaignPack(
            workspace_id='Friday_Night',
            installed_pack_id=installed_pack.installed_pack_id,
            pack_id='friday-pack',
            title='Friday Pack',
            pack_version='1.0.0',
            schema_version='1',
            pack_hash='a' * 64,
            manifest_json='{}',
        )
        db.session.add(campaign_pack)
        db.session.flush()
        db.session.add(
            CampaignPackRecord(
                campaign_pack_id=campaign_pack.campaign_pack_id,
                workspace_id='Friday_Night',
                pack_id='friday-pack',
                record_type='location',
                record_id='friday-inn',
                record_json='{}',
            )
        )
        campaign_pack_session = CampaignPackSession(
            campaign_pack_id=campaign_pack.campaign_pack_id,
            installed_pack_id=installed_pack.installed_pack_id,
            session_id=table_session.session_id,
            campaign_id=table_campaign.campaign_id,
            workspace_id='Friday_Night',
            pack_id='friday-pack',
        )
        db.session.add(campaign_pack_session)
        db.session.flush()
        db.session.add(
            CampaignPackCheckpointProgress(
                campaign_pack_session_id=campaign_pack_session.campaign_pack_session_id,
                checkpoint_id='arrival',
            )
        )
        db.session.add(
            CampaignPackProgressEvent(
                campaign_pack_session_id=campaign_pack_session.campaign_pack_session_id,
                session_id=table_session.session_id,
                campaign_id=table_campaign.campaign_id,
                event_type='checkpoint',
                action='activate',
                payload_json='{}',
            )
        )
        db.session.add(
            OperatorActionAudit(
                workspace_id='Friday_Night',
                action='legacy.private_action',
                resource_type='workspace',
                resource_id='Friday_Night',
                actor='deleted-workspace-admin',
                actor_role='admin',
                details_json='{"private": "old workspace data"}',
            )
        )
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
        assert InstalledCampaignPack.query.filter_by(workspace_id='Friday_Night').count() == 0
        assert CampaignPack.query.filter_by(workspace_id='Friday_Night').count() == 0
        assert CampaignPackRecord.query.filter_by(workspace_id='Friday_Night').count() == 0
        assert CampaignPackSession.query.filter_by(workspace_id='Friday_Night').count() == 0
        assert CampaignPackCheckpointProgress.query.count() == 0
        assert CampaignPackProgressEvent.query.count() == 0
        assert OperatorActionAudit.query.filter_by(workspace_id='Friday_Night').count() == 0

    recreate_table = client.post(
        '/api/accounts/workspaces',
        headers={'Authorization': f'Bearer {owner_token}'},
        json={
            'table_name': 'Friday Night',
            'access_mode': 'password',
            'table_password': 'replacement-secret',
        },
    )
    assert recreate_table.status_code == 201
    reused_workspace_audits = client.get(
        '/api/beta/audits',
        headers={
            'Authorization': f'Bearer {owner_token}',
            'X-AIDM-Workspace-Id': 'Friday_Night',
        },
    )
    assert reused_workspace_audits.status_code == 200
    assert reused_workspace_audits.get_json()['summary']['operator_action_count'] == 0
    assert reused_workspace_audits.get_json()['operator_actions'] == []


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

    stale_name_login = _login(
        client,
        username='Danny',
        first_name='Test',
        last_name='Test',
        password='secret',
        intent='login',
    )
    assert stale_name_login.status_code == 200
    assert stale_name_login.get_json()['account']['display_name'] == 'Danny Reichner'
    with app.app_context():
        account = Account.query.filter_by(username='danny').one()
        assert account.first_name == 'Danny'
        assert account.last_name == 'Reichner'


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


def test_signup_sets_password_for_legacy_passwordless_account_with_saved_token(tmp_path, monkeypatch):
    app = _build_account_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    saved_token = _create_legacy_passwordless_account(
        app,
        username='Maya',
        first_name='Maya',
        last_name='Stone',
    )

    missing_identity_signup = _login(
        client,
        username='Maya',
        first_name='',
        last_name='',
        password='new-secret',
        intent='signup',
    )
    assert missing_identity_signup.status_code == 401
    assert missing_identity_signup.get_json()['error_code'] == 'legacy_password_setup_required'
    assert missing_identity_signup.get_json()['error'] == LEGACY_PASSWORD_SETUP_MESSAGE

    mismatch_signup = _login(
        client,
        username='Maya',
        first_name='Mara',
        last_name='Stone',
        password='new-secret',
        intent='signup',
    )
    assert mismatch_signup.status_code == 401
    assert mismatch_signup.get_json()['error_code'] == 'legacy_password_setup_required'
    with app.app_context():
        account = Account.query.filter_by(username='maya').one()
        assert account.password_hash is None

    signup = client.post(
        '/api/accounts/login',
        headers={'Authorization': f'Bearer {saved_token}'},
        json={
            'username': 'Maya',
            'password': 'new-secret',
            'intent': 'signup',
        },
    )
    assert signup.status_code == 200
    signup_token = signup.get_json()['account_token']
    assert signup_token == saved_token

    with app.app_context():
        account = Account.query.filter_by(username='maya').one()
        assert account.password_hash

    password_login = _login(
        client,
        username='Maya',
        first_name='',
        last_name='',
        password='new-secret',
        intent='login',
    )
    assert password_login.status_code == 200


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
    assert stale_login.get_json()['error'] == LEGACY_PASSWORD_SETUP_MESSAGE

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


def test_operator_recovery_token_rotates_without_relying_on_client_flag(tmp_path, monkeypatch):
    app = _build_account_runtime(tmp_path, monkeypatch)
    client = app.test_client()

    _create_legacy_passwordless_account(
        app,
        username='Maya',
        first_name='Maya',
        last_name='Stone',
    )
    with app.app_context():
        account = Account.query.filter_by(username='maya').one()
        recovery_code = issue_legacy_recovery_token(account)
        db.session.commit()

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

    missing_identity_claim = client.post(
        '/api/accounts/login',
        json={
            'username': 'Maya',
            'password': 'new-secret',
            'legacy_claim': True,
        },
    )
    assert missing_identity_claim.status_code == 401
    assert missing_identity_claim.get_json()['error_code'] == 'legacy_password_setup_required'
    with app.app_context():
        account = Account.query.filter_by(username='maya').one()
        assert account.password_hash is None

    partial_identity_claim = client.post(
        '/api/accounts/login',
        json={
            'username': 'Maya',
            'first_name': 'Maya',
            'password': 'new-secret',
            'legacy_claim': True,
        },
    )
    assert partial_identity_claim.status_code == 401
    assert partial_identity_claim.get_json()['error_code'] == 'legacy_password_setup_required'
    with app.app_context():
        account = Account.query.filter_by(username='maya').one()
        assert account.password_hash is None

    claim = client.post(
        '/api/accounts/login',
        headers={'Authorization': f'Bearer {recovery_code}'},
        json={
            'username': 'Maya',
            'password': 'new-secret',
            'intent': 'signup',
        },
    )
    assert claim.status_code == 200
    claim_token = claim.get_json()['account_token']
    assert claim_token and claim_token != recovery_code

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
        json={
            'character_name': 'Mira',
            'race': 'Human',
            'char_class': 'Fighter',
            'stats': {
                'strength': 15,
                'dexterity': 14,
                'constitution': 13,
                'intelligence': 12,
                'wisdom': 8,
                'charisma': 8,
            },
        },
    )
    assert create_maya.status_code == 201
    maya_player_id = create_maya.get_json()['player_id']

    create_other_maya = client.post(
        f'/api/players/campaigns/{other_campaign_id}/players',
        headers=normal_headers,
        json={
            'character_name': 'Mira Elsewhere',
            'race': 'Human',
            'char_class': 'Fighter',
            'stats': {
                'strength': 15,
                'dexterity': 14,
                'constitution': 13,
                'intelligence': 12,
                'wisdom': 8,
                'charisma': 8,
            },
        },
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
