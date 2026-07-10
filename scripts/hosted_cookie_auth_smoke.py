from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
import pathlib
import secrets
import sys
import tempfile
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
import socketio


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


CSRF_COOKIE_NAME = 'aidm_csrf_token'
ACCOUNT_COOKIE_NAME = 'aidm_account_session'
DEFAULT_EVIDENCE_REPORT = REPO_ROOT / 'tmp' / 'release' / 'hosted-cookie-auth-evidence.md'
RUNTIME_ENV_OVERRIDES = {
    'AIDM_ENV': 'test',
    'AIDM_DATABASE_URI': '',
    'AIDM_AUTO_CREATE_SCHEMA': 'true',
    'AIDM_AUTH_REQUIRED': 'true',
    'AIDM_API_AUTH_TOKENS': 'hosted-cookie-smoke-operator-token',
    'AIDM_ACCOUNT_COOKIE_AUTH_ENABLED': 'true',
    'AIDM_ACCOUNT_COOKIE_SECURE': 'false',
    'AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED': 'false',
    'AIDM_LLM_PROVIDER': 'fallback',
    'AIDM_LLM_MODEL': 'hosted-cookie-auth-smoke-v1',
    'AIDM_LLM_FALLBACK_MODELS': '',
    'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
    'AIDM_TELEMETRY_ENABLED': 'false',
    'AIDM_RATE_LIMIT_MAX_API_REQUESTS': '1000',
    'AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES': '1000',
}
LIVE_SOCKET_NAMESPACE_SETTLE_SECONDS = 0.1


class HostedSocketClient(socketio.Client):
    """Avoid a hosted direct-WebSocket namespace handshake false negative."""

    def _handle_eio_connect(self):
        # python-engineio invokes this callback before its direct-WebSocket
        # read/write loops start. Live Render probes showed the namespace
        # packet can otherwise time out before the app's connect handler.
        time.sleep(LIVE_SOCKET_NAMESPACE_SETTLE_SECONDS)
        super()._handle_eio_connect()


@dataclass(frozen=True)
class SeededHostedAuthRuntime:
    workspace_id: str
    world_id: int
    campaign_id: int
    session_id: int
    player_id: int


class HeaderAdapter:
    def __init__(self, headers):
        self._headers = headers

    def getlist(self, name: str) -> list[str]:
        if hasattr(self._headers, 'getlist'):
            return list(self._headers.getlist(name))
        if hasattr(self._headers, 'get_all'):
            return list(self._headers.get_all(name))
        value = self._headers.get(name, '')
        return [value] if value else []


class RequestsResponseAdapter:
    def __init__(self, response: requests.Response):
        self._response = response
        self.status_code = response.status_code
        self.headers = HeaderAdapter(response.raw.headers)

    def get_json(self, silent: bool = False):
        try:
            return self._response.json()
        except ValueError:
            if silent:
                return None
            raise

    def get_data(self, as_text: bool = False):
        return self._response.text if as_text else self._response.content


class RequestsHttpClient:
    def __init__(self, base_url: str, *, timeout_seconds: float):
        self.base_url = base_url.rstrip('/') + '/'
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip('/'))

    def post(self, path: str, *, headers: dict[str, str] | None = None, json: dict | None = None):
        return RequestsResponseAdapter(
            self.session.post(self._url(path), headers=headers or {}, json=json or {}, timeout=self.timeout_seconds)
        )

    def get(self, path: str, *, headers: dict[str, str] | None = None):
        return RequestsResponseAdapter(self.session.get(self._url(path), headers=headers or {}, timeout=self.timeout_seconds))

    def delete(self, path: str, *, headers: dict[str, str] | None = None):
        return RequestsResponseAdapter(
            self.session.delete(self._url(path), headers=headers or {}, timeout=self.timeout_seconds)
        )

    def cookie_header(self) -> str:
        return '; '.join(f'{cookie.name}={cookie.value}' for cookie in self.session.cookies if cookie.value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run hosted cookie-auth smoke checks.')
    parser.add_argument(
        '--database-uri',
        default='',
        help='Optional SQLAlchemy database URI for isolated mode. Defaults to an isolated temporary SQLite database.',
    )
    parser.add_argument(
        '--target-url',
        default='',
        help='Run a live target smoke against this deployed base URL instead of an isolated Flask runtime.',
    )
    parser.add_argument(
        '--username',
        default='',
        help='Account username for live target mode. Defaults to a generated signup username.',
    )
    parser.add_argument(
        '--password',
        default='',
        help='Account password for live target mode. Defaults to a generated password for signup mode.',
    )
    parser.add_argument(
        '--account-intent',
        choices=('signup', 'login'),
        default='signup',
        help='Use signup for a throwaway live target account or login for an existing account.',
    )
    parser.add_argument(
        '--workspace-name',
        default='Hosted Cookie Smoke',
        help='Workspace/table name to create during live target mode.',
    )
    parser.add_argument(
        '--socketio-path',
        default='socket.io',
        help='Socket.IO path for live target mode.',
    )
    parser.add_argument(
        '--timeout-seconds',
        type=float,
        default=10.0,
        help='HTTP and Socket.IO timeout for live target mode.',
    )
    parser.add_argument(
        '--evidence-report',
        nargs='?',
        const=DEFAULT_EVIDENCE_REPORT,
        default=None,
        type=pathlib.Path,
        help='Write Markdown or JSON hosted cookie-auth smoke evidence.',
    )
    return parser


def configure_runtime(database_uri: str) -> None:
    os.environ.update({**RUNTIME_ENV_OVERRIDES, 'AIDM_DATABASE_URI': database_uri})


def _snapshot_runtime_env() -> dict[str, str | None]:
    return {key: os.environ.get(key) for key in RUNTIME_ENV_OVERRIDES}


def _restore_runtime_env(snapshot: dict[str, str | None]) -> None:
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _json(response, *, path: str) -> dict:
    payload = response.get_json(silent=True)
    if not isinstance(payload, dict):
        raise AssertionError(f'{path} returned non-object JSON: {response.get_data(as_text=True)[:500]}')
    return payload


def _assert_status(response, expected: int, *, path: str) -> dict:
    payload = _json(response, path=path)
    if response.status_code != expected:
        raise AssertionError(f'{path} expected {expected}, got {response.status_code}: {payload}')
    return payload


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _evidence_payload(
    *,
    mode: str,
    target_url: str,
    workspace_id: str,
    session_id: int | None,
    account_intent: str,
    require_secure_cookie: bool,
    checks: list[str],
) -> dict:
    return {
        'status': 'passed',
        'generated_at': _iso_now(),
        'mode': mode,
        'target_url': target_url,
        'workspace_id': workspace_id,
        'session_id': session_id,
        'account_intent': account_intent,
        'require_secure_cookie': require_secure_cookie,
        'checks': [{'label': check, 'status': 'passed'} for check in checks],
    }


def render_evidence_markdown(payload: dict) -> str:
    rows = ['| Check | Status |', '| --- | --- |']
    for check in payload.get('checks') or []:
        rows.append(f"| {check.get('label')} | {check.get('status')} |")
    return '\n'.join(
        [
            '# Hosted Cookie Auth Evidence',
            '',
            f"- Status: {payload['status']}",
            f"- Generated: {payload['generated_at']}",
            f"- Mode: {payload['mode']}",
            f"- Target URL: `{payload['target_url'] or 'isolated local runtime'}`",
            f"- Workspace ID: `{payload['workspace_id']}`",
            f"- Session ID: {payload.get('session_id') or ''}",
            f"- Account intent: {payload.get('account_intent') or ''}",
            f"- Secure cookie required: {payload.get('require_secure_cookie')}",
            '',
            '## Checks',
            '',
            *rows,
            '',
        ]
    )


def write_evidence_report(path: pathlib.Path, payload: dict) -> pathlib.Path:
    output_path = path if path.is_absolute() else REPO_ROOT / path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == '.json':
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    else:
        output_path.write_text(render_evidence_markdown(payload), encoding='utf-8')
    return output_path


def _csrf_headers(response) -> dict[str, str]:
    csrf_cookie = next(
        (value for value in response.headers.getlist('Set-Cookie') if value.startswith(f'{CSRF_COOKIE_NAME}=')),
        '',
    )
    csrf_token = csrf_cookie.split(';', 1)[0].split('=', 1)[1] if csrf_cookie else ''
    if not csrf_token:
        raise AssertionError('Login response did not set a CSRF companion cookie.')
    return {'X-AIDM-CSRF-Token': csrf_token}


def _assert_cookie_transport(login_response, login_payload: dict, *, require_secure_cookie: bool = False) -> dict[str, str]:
    if login_payload.get('account_token') != '':
        raise AssertionError('Cookie-only login leaked a raw account token in JSON.')
    if login_payload.get('account_token_transport') != 'http_only_cookie':
        raise AssertionError(f"Unexpected account token transport: {login_payload.get('account_token_transport')!r}")
    set_cookies = login_response.headers.getlist('Set-Cookie')
    account_cookie = next((value for value in set_cookies if value.startswith(f'{ACCOUNT_COOKIE_NAME}=')), '')
    if not account_cookie:
        raise AssertionError('Login response did not set the account session cookie.')
    if 'HttpOnly' not in account_cookie:
        raise AssertionError('Account session cookie is not HttpOnly.')
    if require_secure_cookie and 'Secure' not in account_cookie:
        raise AssertionError('HTTPS live target account session cookie is not Secure.')
    return _csrf_headers(login_response)


def _post(client, path: str, payload: dict, *, headers: dict[str, str] | None = None, expected: int = 200) -> dict:
    response = client.post(path, headers=headers or {}, json=payload)
    return _assert_status(response, expected, path=f'POST {path}')


def _get(client, path: str, *, headers: dict[str, str] | None = None, expected: int = 200) -> dict:
    response = client.get(path, headers=headers or {})
    return _assert_status(response, expected, path=f'GET {path}')


def _delete(client, path: str, *, headers: dict[str, str] | None = None, expected: int = 200) -> dict:
    response = client.delete(path, headers=headers or {})
    return _assert_status(response, expected, path=f'DELETE {path}')


def _assert_no_account_token(payload: dict, *, label: str) -> None:
    if payload.get('account_token') != '':
        raise AssertionError(f'{label} leaked a raw account token in JSON.')


def _workspace_headers(workspace_id: str, csrf_headers: dict[str, str] | None = None) -> dict[str, str]:
    headers = {'X-AIDM-Workspace-Id': workspace_id}
    if csrf_headers:
        headers.update(csrf_headers)
    return headers


def _create_account_and_workspace(
    http,
    *,
    username: str = 'HostedCookieSmoke',
    password: str = 'hosted-cookie-secret',
    account_intent: str = 'signup',
    workspace_name: str = 'Hosted Cookie Smoke',
    require_secure_cookie: bool = False,
) -> tuple[str, dict[str, str]]:
    login_response = http.post(
        '/api/accounts/login',
        json={
            'username': username,
            'first_name': 'Hosted',
            'last_name': 'Cookie',
            'password': password,
            'intent': account_intent,
        },
    )
    expected_status = 201 if account_intent == 'signup' else 200
    login_payload = _assert_status(login_response, expected_status, path='POST /api/accounts/login')
    csrf_headers = _assert_cookie_transport(login_response, login_payload, require_secure_cookie=require_secure_cookie)

    missing_csrf = http.post(
        '/api/accounts/workspaces',
        json={'table_name': 'Hosted Cookie Smoke', 'access_mode': 'token'},
    )
    missing_payload = _assert_status(missing_csrf, 403, path='POST /api/accounts/workspaces without CSRF')
    if missing_payload.get('error_code') != 'csrf_required':
        raise AssertionError(f'Missing CSRF did not return csrf_required: {missing_payload}')

    workspace_payload = _post(
        http,
        '/api/accounts/workspaces',
        {'table_name': workspace_name, 'access_mode': 'token'},
        headers=csrf_headers,
        expected=201,
    )
    _assert_no_account_token(workspace_payload, label='Workspace create response')
    workspace_id = str(workspace_payload.get('workspace_id') or '').strip()
    if not workspace_id:
        raise AssertionError(f'Workspace create response did not include workspace_id: {workspace_payload}')
    return workspace_id, csrf_headers


def _seed_play_runtime(http, *, workspace_id: str, csrf_headers: dict[str, str]) -> SeededHostedAuthRuntime:
    headers = _workspace_headers(workspace_id, csrf_headers)
    capabilities = _get(http, '/api/capabilities', headers=_workspace_headers(workspace_id))
    if not capabilities.get('is_workspace_admin'):
        raise AssertionError(f'New workspace owner did not resolve as workspace admin: {capabilities}')
    if 'debug_read' not in set(capabilities.get('capabilities') or []):
        raise AssertionError(f'Admin capabilities did not include debug_read: {capabilities}')

    world = _post(http, '/api/worlds', {'name': 'Hosted Cookie World'}, headers=headers, expected=201)
    campaign = _post(
        http,
        '/api/campaigns',
        {'title': 'Hosted Cookie Campaign', 'world_id': world['world_id']},
        headers=headers,
        expected=201,
    )
    player = _post(
        http,
        f"/api/players/campaigns/{campaign['campaign_id']}/players",
        {'name': 'Hosted Cookie', 'character_name': 'Cookie Sentinel', 'char_class': 'Ranger', 'level': 2},
        headers=headers,
        expected=201,
    )
    session = _post(
        http,
        '/api/sessions/start',
        {'campaign_id': campaign['campaign_id']},
        headers=headers,
        expected=201,
    )

    return SeededHostedAuthRuntime(
        workspace_id=workspace_id,
        world_id=int(world['world_id']),
        campaign_id=int(campaign['campaign_id']),
        session_id=int(session['session_id']),
        player_id=int(player['player_id']),
    )


def _assert_socket_cookie_auth(socketio, app, http, seeded: SeededHostedAuthRuntime) -> None:
    socket_client = socketio.test_client(
        app,
        flask_test_client=http,
        auth={'workspace_id': seeded.workspace_id},
    )
    if not socket_client.is_connected():
        raise AssertionError('Socket.IO client failed to connect with cookie auth and workspace_id.')
    socket_client.emit(
        'join_session',
        {
            'workspace_id': seeded.workspace_id,
            'session_id': seeded.session_id,
            'player_id': seeded.player_id,
        },
    )
    events = socket_client.get_received()
    errors = [event for event in events if event.get('name') == 'error']
    socket_client.disconnect()
    if errors:
        raise AssertionError(f'Cookie-authenticated socket join emitted errors: {errors}')


def _assert_live_socket_cookie_auth(http: RequestsHttpClient, seeded: SeededHostedAuthRuntime, *, socketio_path: str, timeout_seconds: float) -> None:
    cookie_header = http.cookie_header()
    if ACCOUNT_COOKIE_NAME not in cookie_header:
        raise AssertionError('Live target HTTP session does not have an account session cookie for Socket.IO.')
    errors: list[object] = []
    sio = HostedSocketClient(
        reconnection=False,
        request_timeout=timeout_seconds,
        http_session=http.session,
    )

    @sio.on('error')
    def on_error(data):
        errors.append(data)

    sio.connect(
        http.base_url,
        auth={'workspace_id': seeded.workspace_id},
        transports=['websocket'],
        socketio_path=socketio_path,
        wait_timeout=timeout_seconds,
    )
    if not sio.connected:
        raise AssertionError('Live target Socket.IO client failed to connect with cookie auth.')
    sio.emit(
        'join_session',
        {
            'workspace_id': seeded.workspace_id,
            'session_id': seeded.session_id,
            'player_id': seeded.player_id,
        },
    )
    sio.sleep(0.5)
    sio.disconnect()
    if errors:
        raise AssertionError(f'Live target cookie-authenticated socket join emitted errors: {errors}')


def _assert_role_downgrade(app, http, seeded: SeededHostedAuthRuntime) -> None:
    from aidm_server.database import db
    from aidm_server.models import AccountWorkspaceMembership

    with app.app_context():
        membership = AccountWorkspaceMembership.query.filter_by(workspace_id=seeded.workspace_id).one()
        membership.role = 'player'
        db.session.commit()

    downgraded = _get(http, '/api/capabilities', headers=_workspace_headers(seeded.workspace_id))
    if downgraded.get('is_workspace_admin'):
        raise AssertionError(f'Role downgrade still reports workspace admin: {downgraded}')
    downgraded_capabilities = set(downgraded.get('capabilities') or [])
    if 'debug_read' in downgraded_capabilities or 'admin_workspace' in downgraded_capabilities:
        raise AssertionError(f'Role downgrade left admin capabilities visible: {downgraded}')

    support_bundle = http.get('/api/beta/support-bundle', headers=_workspace_headers(seeded.workspace_id))
    support_payload = _assert_status(support_bundle, 403, path='GET /api/beta/support-bundle after role downgrade')
    details = support_payload.get('details') if isinstance(support_payload.get('details'), dict) else {}
    if details.get('required_capability') != 'debug_read':
        raise AssertionError(f'Role downgrade did not remove debug_read access: {support_payload}')


def _assert_logout_clears_session(http, socketio, app, seeded: SeededHostedAuthRuntime, csrf_headers: dict[str, str]) -> None:
    logout_response = http.delete('/api/accounts/session', headers=csrf_headers)
    _assert_status(logout_response, 200, path='DELETE /api/accounts/session')
    set_cookies = logout_response.headers.getlist('Set-Cookie')
    if not any(value.startswith(f'{ACCOUNT_COOKIE_NAME}=;') for value in set_cookies):
        raise AssertionError('Logout did not clear the account session cookie.')
    if not any(value.startswith(f'{CSRF_COOKIE_NAME}=;') for value in set_cookies):
        raise AssertionError('Logout did not clear the CSRF cookie.')

    _get(http, '/api/accounts/me', headers=_workspace_headers(seeded.workspace_id), expected=401)
    _post(
        http,
        '/api/worlds',
        {'name': 'Should Not Persist'},
        headers=_workspace_headers(seeded.workspace_id, csrf_headers),
        expected=401,
    )

    socket_client = socketio.test_client(
        app,
        flask_test_client=http,
        auth={'workspace_id': seeded.workspace_id},
    )
    if socket_client.is_connected():
        socket_client.disconnect()
        raise AssertionError('Socket.IO client connected after logout cleared account cookies.')


def _assert_live_logout_clears_session(
    http: RequestsHttpClient,
    seeded: SeededHostedAuthRuntime,
    csrf_headers: dict[str, str],
    *,
    socketio_path: str,
    timeout_seconds: float,
) -> None:
    logout_response = http.delete('/api/accounts/session', headers=csrf_headers)
    _assert_status(logout_response, 200, path='DELETE /api/accounts/session')
    set_cookies = logout_response.headers.getlist('Set-Cookie')
    if not any(value.startswith(f'{ACCOUNT_COOKIE_NAME}=;') for value in set_cookies):
        raise AssertionError('Live target logout did not clear the account session cookie.')
    if not any(value.startswith(f'{CSRF_COOKIE_NAME}=;') for value in set_cookies):
        raise AssertionError('Live target logout did not clear the CSRF cookie.')

    _get(http, '/api/accounts/me', headers=_workspace_headers(seeded.workspace_id), expected=401)
    _post(
        http,
        '/api/worlds',
        {'name': 'Should Not Persist'},
        headers=_workspace_headers(seeded.workspace_id, csrf_headers),
        expected=401,
    )

    sio = HostedSocketClient(
        reconnection=False,
        request_timeout=timeout_seconds,
        http_session=http.session,
    )
    try:
        sio.connect(
            http.base_url,
            auth={'workspace_id': seeded.workspace_id},
            transports=['websocket'],
            socketio_path=socketio_path,
            wait_timeout=timeout_seconds,
        )
    except Exception:
        return
    try:
        if sio.connected:
            raise AssertionError('Live target Socket.IO client connected after logout cleared account cookies.')
    finally:
        if sio.connected:
            sio.disconnect()


def run_live_target_smoke(
    *,
    target_url: str,
    username: str,
    password: str,
    account_intent: str,
    workspace_name: str,
    socketio_path: str,
    timeout_seconds: float,
) -> dict:
    if account_intent == 'login' and (not username or not password):
        raise SystemExit('--username and --password are required when --account-intent=login.')
    suffix = secrets.token_hex(4)
    username = username or f'HostedCookieSmoke_{suffix}'
    password = password or f'hosted-cookie-secret-{suffix}'
    workspace_name = f'{workspace_name} {suffix}' if account_intent == 'signup' else workspace_name
    require_secure_cookie = target_url.lower().startswith('https://')
    http = RequestsHttpClient(target_url, timeout_seconds=timeout_seconds)

    workspace_id, csrf_headers = _create_account_and_workspace(
        http,
        username=username,
        password=password,
        account_intent=account_intent,
        workspace_name=workspace_name,
        require_secure_cookie=require_secure_cookie,
    )
    seeded = _seed_play_runtime(http, workspace_id=workspace_id, csrf_headers=csrf_headers)
    _assert_live_socket_cookie_auth(
        http,
        seeded,
        socketio_path=socketio_path,
        timeout_seconds=timeout_seconds,
    )
    _delete(
        http,
        f'/api/sessions/{seeded.session_id}?hard=true',
        headers=_workspace_headers(workspace_id, csrf_headers),
        expected=200,
    )
    _delete(
        http,
        f'/api/accounts/workspaces/{workspace_id}',
        headers=_workspace_headers(workspace_id, csrf_headers),
        expected=200,
    )
    _assert_live_logout_clears_session(
        http,
        seeded,
        csrf_headers,
        socketio_path=socketio_path,
        timeout_seconds=timeout_seconds,
    )

    print(
        'Hosted cookie auth live-target smoke passed: cookie-only login, CSRF, '
        'Socket.IO cookie auth, workspace cleanup, and logout cleanup verified.'
    )
    return _evidence_payload(
        mode='live-target',
        target_url=target_url,
        workspace_id=workspace_id,
        session_id=seeded.session_id,
        account_intent=account_intent,
        require_secure_cookie=require_secure_cookie,
        checks=[
            'Cookie-only login used an HttpOnly account cookie and did not return a raw account token',
            'Unsafe workspace creation required X-AIDM-CSRF-Token',
            'Workspace owner capabilities included admin/debug access before downgrade checks',
            'Socket.IO accepted cookie-authenticated session join',
            'Smoke-created session and workspace cleanup completed',
            'Logout cleared account and CSRF cookies and rejected later API/socket access',
        ],
    )


def run_smoke(*, database_uri: str) -> dict:
    env_snapshot = _snapshot_runtime_env()
    try:
        configure_runtime(database_uri)

        from aidm_server.blueprints.socketio_events import register_socketio_events
        from aidm_server.database import ensure_schema
        from aidm_server.main import create_app, create_socketio

        app = create_app()
        ensure_schema(app)
        socketio = create_socketio(app)
        register_socketio_events(socketio)
        http = app.test_client()

        workspace_id, csrf_headers = _create_account_and_workspace(http)
        seeded = _seed_play_runtime(http, workspace_id=workspace_id, csrf_headers=csrf_headers)
        _assert_socket_cookie_auth(socketio, app, http, seeded)
        _assert_role_downgrade(app, http, seeded)
        _assert_socket_cookie_auth(socketio, app, http, seeded)
        _assert_logout_clears_session(http, socketio, app, seeded, csrf_headers)

        print(
            'Hosted cookie auth smoke passed: cookie-only account login, CSRF, '
            'fresh role downgrade, Socket.IO cookie auth, and logout cleanup verified.'
        )
        return _evidence_payload(
            mode='isolated',
            target_url='',
            workspace_id=seeded.workspace_id,
            session_id=seeded.session_id,
            account_intent='signup',
            require_secure_cookie=False,
            checks=[
                'Cookie-only login used an HttpOnly account cookie and did not return a raw account token',
                'Unsafe workspace creation required X-AIDM-CSRF-Token',
                'Workspace owner capabilities included admin/debug access before downgrade checks',
                'Socket.IO accepted cookie-authenticated session join',
                'Role downgrade removed admin/debug capabilities and support-bundle access',
                'Socket.IO still allowed normal player session join after role downgrade',
                'Logout cleared account and CSRF cookies and rejected later API/socket access',
            ],
        )
    finally:
        _restore_runtime_env(env_snapshot)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.target_url and args.database_uri:
        parser.error('--database-uri cannot be combined with --target-url.')
    if args.target_url:
        payload = run_live_target_smoke(
            target_url=args.target_url,
            username=args.username,
            password=args.password,
            account_intent=args.account_intent,
            workspace_name=args.workspace_name,
            socketio_path=args.socketio_path,
            timeout_seconds=args.timeout_seconds,
        )
        if args.evidence_report is not None and payload:
            output_path = write_evidence_report(args.evidence_report, payload)
            print(f'[hosted-cookie-auth-smoke] Evidence report written to {output_path}.')
        return 0
    if args.database_uri:
        payload = run_smoke(database_uri=args.database_uri)
        if args.evidence_report is not None:
            output_path = write_evidence_report(args.evidence_report, payload)
            print(f'[hosted-cookie-auth-smoke] Evidence report written to {output_path}.')
        return 0

    with tempfile.TemporaryDirectory(prefix='aidm-hosted-cookie-auth-') as tmp:
        db_path = pathlib.Path(tmp) / 'hosted-cookie-auth.sqlite'
        payload = run_smoke(database_uri=f'sqlite:///{db_path}')
    if args.evidence_report is not None:
        output_path = write_evidence_report(args.evidence_report, payload)
        print(f'[hosted-cookie-auth-smoke] Evidence report written to {output_path}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
