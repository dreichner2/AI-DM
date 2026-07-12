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
DEFAULT_PROOF_OUTPUT_DIR = REPO_ROOT / 'tmp' / 'release'
PROOF_ARTIFACT_FILENAMES = (
    'security-forbidden-evidence.md',
    'export-import-evidence.md',
    'beta-slo-baseline.md',
)
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
    private_marker: str


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

    def post(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict | None = None,
        json_payload: dict | None = None,
    ):
        payload = json if json is not None else json_payload
        return RequestsResponseAdapter(
            self.session.post(self._url(path), headers=headers or {}, json=payload or {}, timeout=self.timeout_seconds)
        )

    def get(self, path: str, *, headers: dict[str, str] | None = None):
        return RequestsResponseAdapter(self.session.get(self._url(path), headers=headers or {}, timeout=self.timeout_seconds))

    def patch(self, path: str, *, headers: dict[str, str] | None = None, json: dict | None = None):
        return RequestsResponseAdapter(
            self.session.patch(self._url(path), headers=headers or {}, json=json or {}, timeout=self.timeout_seconds)
        )

    def delete(self, path: str, *, headers: dict[str, str] | None = None):
        return RequestsResponseAdapter(
            self.session.delete(self._url(path), headers=headers or {}, timeout=self.timeout_seconds)
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json_payload: dict | None,
    ):
        return RequestsResponseAdapter(
            self.session.request(
                method,
                self._url(path),
                headers=headers,
                json=json_payload,
                timeout=self.timeout_seconds,
            )
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
    parser.add_argument(
        '--release-proof-suite',
        action='store_true',
        help=(
            'Use the throwaway owner and player cookie sessions to also write hosted non-admin, '
            'session export/import, and beta SLO evidence without bearer-token inputs.'
        ),
    )
    parser.add_argument(
        '--proof-output-dir',
        type=pathlib.Path,
        default=DEFAULT_PROOF_OUTPUT_DIR,
        help=f'Directory for release-proof artifacts. Default: {DEFAULT_PROOF_OUTPUT_DIR}.',
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
    proof_artifacts: dict[str, str] | None = None,
) -> dict:
    payload = {
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
    if proof_artifacts:
        payload['release_proof_suite'] = {
            'status': 'passed',
            'artifacts': proof_artifacts,
        }
    return payload


def render_evidence_markdown(payload: dict) -> str:
    rows = ['| Check | Status |', '| --- | --- |']
    for check in payload.get('checks') or []:
        rows.append(f"| {check.get('label')} | {check.get('status')} |")
    lines = [
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
    proof_suite = payload.get('release_proof_suite')
    if isinstance(proof_suite, dict):
        lines.extend(['## Release Proof Suite', '', f"- Status: {proof_suite.get('status') or ''}"])
        artifacts = proof_suite.get('artifacts')
        if isinstance(artifacts, dict):
            lines.extend(f'- {label}: `{path}`' for label, path in sorted(artifacts.items()))
        lines.append('')
    return '\n'.join(lines)


def write_evidence_report(path: pathlib.Path, payload: dict) -> pathlib.Path:
    output_path = path if path.is_absolute() else REPO_ROOT / path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == '.json':
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    else:
        output_path.write_text(render_evidence_markdown(payload), encoding='utf-8')
    return output_path


def _resolved_output_dir(path: pathlib.Path) -> pathlib.Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _artifact_display_path(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _client_cookie_values(http) -> list[str]:
    values: list[str] = []
    session = getattr(http, 'session', None)
    cookies = getattr(session, 'cookies', None)
    if cookies is not None:
        values.extend(str(cookie.value) for cookie in cookies if getattr(cookie, 'value', ''))
    get_cookie = getattr(http, 'get_cookie', None)
    if callable(get_cookie):
        for name in (ACCOUNT_COOKIE_NAME, CSRF_COOKIE_NAME):
            cookie = get_cookie(name)
            value = getattr(cookie, 'value', '') if cookie is not None else ''
            if value:
                values.append(str(value))
    return values


def _redact_known_values(markdown: str, sensitive_values: list[str]) -> str:
    redacted = markdown
    for value in sorted({str(value) for value in sensitive_values if len(str(value)) >= 8}, key=len, reverse=True):
        redacted = redacted.replace(value, '<redacted>')
    return redacted


def _write_redacted_markdown(path: pathlib.Path, markdown: str, *, sensitive_values: list[str]) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_redact_known_values(markdown, sensitive_values), encoding='utf-8')
    return path


def _seed_export_import_source(
    http,
    *,
    seeded: SeededHostedAuthRuntime,
    csrf_headers: dict[str, str],
) -> int:
    payload = _post(
        http,
        '/api/sessions/import',
        {
            'campaign_id': seeded.campaign_id,
            'name': 'Hosted Cookie Release Proof Source',
            'selectedIds': {
                'campaignId': seeded.campaign_id,
                'sessionId': seeded.session_id,
                'playerId': seeded.player_id,
            },
            'selectedSession': {
                'session_id': seeded.session_id,
                'display_name': 'Hosted Cookie Release Proof Source',
            },
            'turnEvents': [
                {
                    'event_id': 1,
                    'turn_id': 1,
                    'player_id': seeded.player_id,
                    'event_type': 'player_message',
                    'payload': {
                        'speaker': 'Cookie Sentinel',
                        'message': 'I verify the hosted export gate.',
                    },
                    'created_at': _iso_now(),
                },
                {
                    'event_id': 2,
                    'turn_id': 1,
                    'player_id': seeded.player_id,
                    'event_type': 'dm_response',
                    'payload': {'message': 'The hosted export gate records a stable answer.'},
                    'created_at': _iso_now(),
                },
            ],
        },
        headers=_workspace_headers(seeded.workspace_id, csrf_headers),
        expected=201,
    )
    session_id = int(payload.get('session_id') or 0)
    if session_id <= 0:
        raise AssertionError(f'Session proof source import omitted session_id: {payload}')
    return session_id


def _run_release_proof_suite(
    owner_http,
    peer_http,
    *,
    seeded: SeededHostedAuthRuntime,
    owner_csrf_headers: dict[str, str],
    peer_csrf_headers: dict[str, str],
    target_url: str,
    mode: str,
    output_dir: pathlib.Path,
    sensitive_values: list[str],
) -> dict[str, str]:
    from scripts import render_beta_slo_baseline, security_forbidden_smoke, session_export_import_smoke

    resolved_output_dir = _resolved_output_dir(output_dir)
    for filename in PROOF_ARTIFACT_FILENAMES:
        (resolved_output_dir / filename).unlink(missing_ok=True)
    artifacts: dict[str, str] = {}
    errors: list[str] = []
    generated_at = _iso_now()

    try:
        security_http = (
            peer_http
            if isinstance(peer_http, RequestsHttpClient)
            else security_forbidden_smoke.FlaskTestHttpClient(peer_http)
        )
        results = security_forbidden_smoke.run_forbidden_checks(
            security_http,
            workspace_id=seeded.workspace_id,
            campaign_id=seeded.campaign_id,
            session_id=seeded.session_id,
            headers=_workspace_headers(seeded.workspace_id, peer_csrf_headers),
        )
        security_payload = security_forbidden_smoke.evidence_payload(
            mode=mode,
            target_url=target_url,
            workspace_id=seeded.workspace_id,
            campaign_id=seeded.campaign_id,
            session_id=seeded.session_id,
            generated_at=generated_at,
            results=results,
        )
        security_path = resolved_output_dir / 'security-forbidden-evidence.md'
        _write_redacted_markdown(
            security_path,
            security_forbidden_smoke.render_evidence_markdown(security_payload),
            sensitive_values=sensitive_values,
        )
        artifacts['Security forbidden evidence'] = _artifact_display_path(security_path)
        if security_payload['status'] != 'passed':
            raise AssertionError('One or more capability checks did not return the required forbidden response.')
    except Exception as exc:
        errors.append(f'security-forbidden: {_redact_known_values(str(exc), sensitive_values)}')

    source_session_id: int | None = None
    source_cleanup_error = ''
    try:
        source_session_id = _seed_export_import_source(
            owner_http,
            seeded=seeded,
            csrf_headers=owner_csrf_headers,
        )
        session_http = (
            owner_http
            if isinstance(owner_http, RequestsHttpClient)
            else session_export_import_smoke.FlaskTestHttpClient(owner_http)
        )
        _export_payload, round_trip = session_export_import_smoke.run_round_trip(
            session_http,
            headers=_workspace_headers(seeded.workspace_id, owner_csrf_headers),
            session_id=source_session_id,
            player_id=seeded.player_id,
        )
        export_payload = session_export_import_smoke.evidence_payload(
            mode=mode,
            target_url=target_url,
            workspace_id=seeded.workspace_id,
            generated_at=generated_at,
            result=round_trip,
        )
        export_path = resolved_output_dir / 'export-import-evidence.md'
        _write_redacted_markdown(
            export_path,
            session_export_import_smoke.render_evidence_markdown(export_payload),
            sensitive_values=sensitive_values,
        )
        artifacts['Session export/import evidence'] = _artifact_display_path(export_path)
        if export_payload['status'] != 'passed':
            raise AssertionError('Session export/import result did not satisfy the duplication and cleanup checks.')
    except Exception as exc:
        errors.append(f'export-import: {_redact_known_values(str(exc), sensitive_values)}')
    finally:
        if source_session_id is not None:
            try:
                response = owner_http.delete(
                    f'/api/sessions/{source_session_id}?hard=true',
                    headers=_workspace_headers(seeded.workspace_id, owner_csrf_headers),
                )
                if response.status_code != 200:
                    source_cleanup_error = f'proof source cleanup returned HTTP {response.status_code}'
            except Exception as exc:  # pragma: no cover - transport failures are environment-specific
                source_cleanup_error = f'proof source cleanup failed with {type(exc).__name__}'
        if source_cleanup_error:
            errors.append(f'export-import-cleanup: {source_cleanup_error}')

    try:
        headers = _workspace_headers(seeded.workspace_id)
        slo = _get(owner_http, '/api/beta/slo', headers=headers)
        incidents = _get(owner_http, '/api/beta/incidents?limit=25', headers=headers)
        baseline = render_beta_slo_baseline.render_baseline(
            slo=slo,
            incidents=incidents,
            generated_at=generated_at,
            release='hosted-cookie-release-proof',
            commit_sha=os.getenv('GITHUB_SHA', ''),
            environment='hosted-target' if mode == 'live-target' else 'isolated',
            target_url=target_url,
            socketio_worker_model='',
            database='',
            llm_provider_model='',
            observability_provider='',
            alert_owner='',
            evidence_report='hosted-cookie-auth-evidence.md',
        )
        baseline_path = resolved_output_dir / 'beta-slo-baseline.md'
        _write_redacted_markdown(baseline_path, baseline, sensitive_values=sensitive_values)
        artifacts['Beta SLO baseline'] = _artifact_display_path(baseline_path)
    except Exception as exc:
        errors.append(f'beta-slo: {_redact_known_values(str(exc), sensitive_values)}')

    if errors:
        raise AssertionError('Release proof suite failed: ' + '; '.join(errors))

    print(
        'Hosted cookie release-proof suite passed: non-admin capability denials, '
        'session export/import cleanup, and beta SLO/incidents evidence were generated.'
    )
    return artifacts


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


def _patch(client, path: str, payload: dict, *, headers: dict[str, str] | None = None, expected: int = 200) -> dict:
    response = client.patch(path, headers=headers or {}, json=payload)
    return _assert_status(response, expected, path=f'PATCH {path}')


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
) -> tuple[str, dict[str, str], str]:
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
    workspace_token = str(workspace_payload.get('workspace_token') or '').strip()
    if not workspace_token:
        raise AssertionError('Token workspace creation did not return its one-time join token.')
    return workspace_id, csrf_headers, workspace_token


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
    private_marker = 'HOSTED_COOKIE_OWNER_PRIVATE_ITEM'
    player = _post(
        http,
        f"/api/players/campaigns/{campaign['campaign_id']}/players",
        {
            'name': 'Hosted Cookie',
            'character_name': 'Cookie Sentinel',
            'char_class': 'Ranger',
            'level': 2,
            'inventory': [{'id': 'hosted-cookie-private-item', 'name': private_marker, 'quantity': 1}],
            'character_sheet': {'privateNotes': private_marker},
        },
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
        private_marker=private_marker,
    )


def _signup_and_join_second_account(
    http,
    *,
    workspace_id: str,
    workspace_token: str,
    username: str,
    password: str,
    require_secure_cookie: bool,
) -> dict[str, str]:
    login_response = http.post(
        '/api/accounts/login',
        json={
            'username': username,
            'first_name': 'Hosted',
            'last_name': 'Peer',
            'password': password,
            'intent': 'signup',
        },
    )
    login_payload = _assert_status(login_response, 201, path='POST /api/accounts/login for second account')
    csrf_headers = _assert_cookie_transport(
        login_response,
        login_payload,
        require_secure_cookie=require_secure_cookie,
    )
    join_payload = _post(
        http,
        '/api/accounts/workspace',
        {'workspace_token': workspace_token},
        headers=csrf_headers,
        expected=200,
    )
    if join_payload.get('workspace_id') != workspace_id or join_payload.get('workspace_role') != 'player':
        raise AssertionError(f'Second account did not join as a workspace player: {join_payload}')
    return csrf_headers


def _seed_second_account_player(
    http,
    *,
    seeded: SeededHostedAuthRuntime,
    csrf_headers: dict[str, str],
) -> int:
    player = _post(
        http,
        f'/api/players/campaigns/{seeded.campaign_id}/players',
        {
            'name': 'Hosted Peer',
            'character_name': 'Privacy Warden',
            'char_class': 'Wizard',
            'level': 2,
            'inventory': [
                {
                    'id': 'hosted-peer-private-item',
                    'name': 'HOSTED_COOKIE_PEER_PRIVATE_ITEM',
                    'quantity': 1,
                }
            ],
        },
        headers=_workspace_headers(seeded.workspace_id, csrf_headers),
        expected=201,
    )
    return int(player['player_id'])


def _assert_two_account_rest_privacy(
    http,
    *,
    seeded: SeededHostedAuthRuntime,
    own_player_id: int,
    csrf_headers: dict[str, str],
) -> None:
    headers = _workspace_headers(seeded.workspace_id)
    unsafe_headers = _workspace_headers(seeded.workspace_id, csrf_headers)

    _get(http, f'/api/players/{seeded.player_id}', headers=headers, expected=404)
    own_detail = _get(http, f'/api/players/{own_player_id}', headers=headers, expected=200)
    if own_detail.get('player_id') != own_player_id or 'inventory' not in own_detail:
        raise AssertionError(f'Second account did not receive its own private player detail: {own_detail}')
    _patch(
        http,
        f'/api/players/{seeded.player_id}',
        {'level': 20},
        headers=unsafe_headers,
        expected=404,
    )

    party_response = http.get(
        f'/api/players/campaigns/{seeded.campaign_id}/players',
        headers=headers,
    )
    party_payload = party_response.get_json(silent=True)
    if party_response.status_code != 200 or not isinstance(party_payload, list):
        raise AssertionError(f'Party summary failed for second account: {party_payload}')
    party_ids = {int(player.get('player_id')) for player in party_payload if isinstance(player, dict)}
    if own_player_id not in party_ids:
        raise AssertionError(f'Party summary omitted the second account player: {party_payload}')
    party_text = json.dumps(party_payload, sort_keys=True)
    if seeded.private_marker in party_text or 'inventory' in party_text or 'character_sheet' in party_text:
        raise AssertionError(f'Party summary leaked private player fields: {party_text[:500]}')

    _get(
        http,
        f'/api/sessions/{seeded.session_id}/export?player_id={seeded.player_id}',
        headers=headers,
        expected=404,
    )
    own_export = _get(
        http,
        f'/api/sessions/{seeded.session_id}/export?player_id={own_player_id}',
        headers=headers,
        expected=200,
    )
    if own_export.get('selectedPlayer', {}).get('player_id') != own_player_id:
        raise AssertionError(f'Second account export did not select its own player: {own_export}')
    if seeded.private_marker in json.dumps(own_export, sort_keys=True):
        raise AssertionError('Second account export leaked the owner player private marker.')

    session_state = _get(
        http,
        f'/api/sessions/{seeded.session_id}/state',
        headers=headers,
        expected=200,
    )
    if seeded.private_marker in json.dumps(session_state, sort_keys=True):
        raise AssertionError('Second account session state leaked the owner player private marker.')


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


def _assert_socket_player_ownership(
    socketio,
    app,
    http,
    seeded: SeededHostedAuthRuntime,
    *,
    own_player_id: int,
) -> None:
    socket_client = socketio.test_client(
        app,
        flask_test_client=http,
        auth={'workspace_id': seeded.workspace_id},
    )
    if not socket_client.is_connected():
        raise AssertionError('Second account Socket.IO client failed to connect.')

    socket_client.emit(
        'join_session',
        {
            'workspace_id': seeded.workspace_id,
            'session_id': seeded.session_id,
            'player_id': seeded.player_id,
        },
    )
    guessed_events = socket_client.get_received()
    guessed_errors = [event['args'][0] for event in guessed_events if event.get('name') == 'error']
    if not any(isinstance(error, dict) and error.get('error_code') == 'invalid_player' for error in guessed_errors):
        socket_client.disconnect()
        raise AssertionError(f'Second account Socket.IO guessed-player join was not rejected: {guessed_events}')

    socket_client.emit(
        'join_session',
        {
            'workspace_id': seeded.workspace_id,
            'session_id': seeded.session_id,
            'player_id': own_player_id,
        },
    )
    own_events = socket_client.get_received()
    own_errors = [event for event in own_events if event.get('name') == 'error']
    active_payloads = [event['args'][0] for event in own_events if event.get('name') == 'active_players']
    socket_client.disconnect()
    if own_errors:
        raise AssertionError(f'Second account own-player Socket.IO join emitted errors: {own_errors}')
    if not any(
        isinstance(payload, list)
        and any(isinstance(player, dict) and player.get('id') == own_player_id for player in payload)
        for payload in active_payloads
    ):
        raise AssertionError(f'Second account own-player Socket.IO join lacked presence proof: {own_events}')


def _assert_live_socket_player_ownership(
    http: RequestsHttpClient,
    seeded: SeededHostedAuthRuntime,
    *,
    own_player_id: int,
    socketio_path: str,
    timeout_seconds: float,
) -> None:
    errors: list[object] = []
    active_payloads: list[object] = []
    sio = HostedSocketClient(
        reconnection=False,
        request_timeout=timeout_seconds,
        http_session=http.session,
    )

    @sio.on('error')
    def on_error(data):
        errors.append(data)

    @sio.on('active_players')
    def on_active_players(data):
        active_payloads.append(data)

    sio.connect(
        http.base_url,
        auth={'workspace_id': seeded.workspace_id},
        transports=['websocket'],
        socketio_path=socketio_path,
        wait_timeout=timeout_seconds,
    )
    try:
        sio.emit(
            'join_session',
            {
                'workspace_id': seeded.workspace_id,
                'session_id': seeded.session_id,
                'player_id': seeded.player_id,
            },
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and not errors:
            sio.sleep(0.05)
        if not any(isinstance(error, dict) and error.get('error_code') == 'invalid_player' for error in errors):
            raise AssertionError(f'Live second-account guessed-player join was not rejected: {errors}')

        errors.clear()
        active_payloads.clear()
        sio.emit(
            'join_session',
            {
                'workspace_id': seeded.workspace_id,
                'session_id': seeded.session_id,
                'player_id': own_player_id,
            },
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and not active_payloads and not errors:
            sio.sleep(0.05)
        if errors:
            raise AssertionError(f'Live second-account own-player join emitted errors: {errors}')
        if not any(
            isinstance(payload, list)
            and any(isinstance(player, dict) and player.get('id') == own_player_id for player in payload)
            for payload in active_payloads
        ):
            raise AssertionError(f'Live second-account own-player join lacked presence proof: {active_payloads}')
    finally:
        if sio.connected:
            sio.disconnect()


def _assert_role_downgrade(app, http, seeded: SeededHostedAuthRuntime) -> None:
    from aidm_server.database import db
    from aidm_server.models import AccountWorkspaceMembership

    with app.app_context():
        membership = AccountWorkspaceMembership.query.filter_by(workspace_id=seeded.workspace_id, role='admin').one()
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
    release_proof_suite: bool = False,
    proof_output_dir: pathlib.Path = DEFAULT_PROOF_OUTPUT_DIR,
) -> dict:
    if account_intent == 'login' and (not username or not password):
        raise SystemExit('--username and --password are required when --account-intent=login.')
    suffix = secrets.token_hex(4)
    username = username or f'HostedCookieSmoke_{suffix}'
    password = password or f'hosted-cookie-secret-{suffix}'
    workspace_name = f'{workspace_name} {suffix}' if account_intent == 'signup' else workspace_name
    require_secure_cookie = target_url.lower().startswith('https://')
    http = RequestsHttpClient(target_url, timeout_seconds=timeout_seconds)
    peer_http: RequestsHttpClient | None = None
    workspace_id = ''
    workspace_token = ''
    csrf_headers: dict[str, str] = {}
    peer_csrf_headers: dict[str, str] = {}
    seeded: SeededHostedAuthRuntime | None = None
    peer_player_id: int | None = None
    proof_artifacts: dict[str, str] = {}
    cleanup_completed = False
    logout_completed = False
    try:
        workspace_id, csrf_headers, workspace_token = _create_account_and_workspace(
            http,
            username=username,
            password=password,
            account_intent=account_intent,
            workspace_name=workspace_name,
            require_secure_cookie=require_secure_cookie,
        )
        seeded = _seed_play_runtime(http, workspace_id=workspace_id, csrf_headers=csrf_headers)
        peer_http = RequestsHttpClient(target_url, timeout_seconds=timeout_seconds)
        peer_csrf_headers = _signup_and_join_second_account(
            peer_http,
            workspace_id=workspace_id,
            workspace_token=workspace_token,
            username=f'HostedCookiePeer_{suffix}',
            password=f'hosted-cookie-peer-secret-{suffix}',
            require_secure_cookie=require_secure_cookie,
        )
        peer_player_id = _seed_second_account_player(
            peer_http,
            seeded=seeded,
            csrf_headers=peer_csrf_headers,
        )
        _assert_two_account_rest_privacy(
            peer_http,
            seeded=seeded,
            own_player_id=peer_player_id,
            csrf_headers=peer_csrf_headers,
        )
        _assert_live_socket_cookie_auth(
            http,
            seeded,
            socketio_path=socketio_path,
            timeout_seconds=timeout_seconds,
        )
        _assert_live_socket_player_ownership(
            peer_http,
            seeded,
            own_player_id=peer_player_id,
            socketio_path=socketio_path,
            timeout_seconds=timeout_seconds,
        )
        if release_proof_suite:
            sensitive_values = [
                workspace_token,
                *csrf_headers.values(),
                *peer_csrf_headers.values(),
                *_client_cookie_values(http),
                *_client_cookie_values(peer_http),
            ]
            proof_artifacts = _run_release_proof_suite(
                http,
                peer_http,
                seeded=seeded,
                owner_csrf_headers=csrf_headers,
                peer_csrf_headers=peer_csrf_headers,
                target_url=target_url,
                mode='live-target',
                output_dir=proof_output_dir,
                sensitive_values=sensitive_values,
            )
    finally:
        had_prior_error = sys.exc_info()[0] is not None
        sensitive_values = [
            workspace_token,
            *csrf_headers.values(),
            *peer_csrf_headers.values(),
            *_client_cookie_values(http),
            *(_client_cookie_values(peer_http) if peer_http is not None else []),
        ]
        cleanup_errors: list[str] = []
        if seeded is not None:
            try:
                _delete(
                    http,
                    f'/api/sessions/{seeded.session_id}?hard=true',
                    headers=_workspace_headers(workspace_id, csrf_headers),
                    expected=200,
                )
            except Exception as exc:
                cleanup_errors.append(f'session cleanup: {_redact_known_values(str(exc), sensitive_values)}')
        if workspace_id and csrf_headers:
            try:
                _delete(
                    http,
                    f'/api/accounts/workspaces/{workspace_id}',
                    headers=_workspace_headers(workspace_id, csrf_headers),
                    expected=200,
                )
            except Exception as exc:
                cleanup_errors.append(f'workspace cleanup: {_redact_known_values(str(exc), sensitive_values)}')
        cleanup_completed = not cleanup_errors

        logout_errors: list[str] = []
        if csrf_headers:
            try:
                if seeded is not None:
                    _assert_live_logout_clears_session(
                        http,
                        seeded,
                        csrf_headers,
                        socketio_path=socketio_path,
                        timeout_seconds=timeout_seconds,
                    )
                else:
                    _assert_status(
                        http.delete('/api/accounts/session', headers=csrf_headers),
                        200,
                        path='DELETE /api/accounts/session',
                    )
            except Exception as exc:
                logout_errors.append(f'owner logout: {_redact_known_values(str(exc), sensitive_values)}')
        if peer_http is not None and peer_csrf_headers:
            try:
                if seeded is not None:
                    peer_seeded = SeededHostedAuthRuntime(
                        workspace_id=seeded.workspace_id,
                        world_id=seeded.world_id,
                        campaign_id=seeded.campaign_id,
                        session_id=seeded.session_id,
                        player_id=peer_player_id or seeded.player_id,
                        private_marker='HOSTED_COOKIE_PEER_PRIVATE_ITEM',
                    )
                    _assert_live_logout_clears_session(
                        peer_http,
                        peer_seeded,
                        peer_csrf_headers,
                        socketio_path=socketio_path,
                        timeout_seconds=timeout_seconds,
                    )
                else:
                    _assert_status(
                        peer_http.delete('/api/accounts/session', headers=peer_csrf_headers),
                        200,
                        path='DELETE /api/accounts/session for peer',
                    )
            except Exception as exc:
                logout_errors.append(f'peer logout: {_redact_known_values(str(exc), sensitive_values)}')
        logout_completed = not logout_errors

        finalization_errors = cleanup_errors + logout_errors
        if finalization_errors:
            message = 'Live target finalization failed: ' + '; '.join(finalization_errors)
            if had_prior_error:
                print(f'[hosted-cookie-auth-smoke][warning] {message}', file=sys.stderr)
            else:
                raise AssertionError(message)

    assert seeded is not None
    assert peer_player_id is not None
    if not cleanup_completed or not logout_completed:
        raise AssertionError('Live target cleanup or logout did not complete.')

    print(
        'Hosted cookie auth live-target smoke passed: cookie-only login, CSRF, '
        'two-account REST/Socket.IO privacy, workspace cleanup, and logout cleanup verified.'
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
            'A second account could read its own private character detail but not the owner character detail',
            'Party, session-state, and export projections did not leak the owner private marker to the second account',
            'A second account could bind Socket.IO only as its owned player, not a guessed peer player',
            'Smoke-created session and workspace cleanup completed',
            'Both account logouts cleared account and CSRF cookies and rejected later API/socket access',
            *(
                [
                    'Player cookie auth denied every operator capability check with the expected capability',
                    'Owner cookie auth completed an export/import round trip and removed both proof sessions',
                    'Owner cookie auth generated hosted beta SLO and incident baseline evidence',
                ]
                if release_proof_suite
                else []
            ),
        ],
        proof_artifacts=proof_artifacts,
    )


def run_smoke(
    *,
    database_uri: str,
    release_proof_suite: bool = False,
    proof_output_dir: pathlib.Path = DEFAULT_PROOF_OUTPUT_DIR,
) -> dict:
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

        workspace_id, csrf_headers, workspace_token = _create_account_and_workspace(http)
        seeded = _seed_play_runtime(http, workspace_id=workspace_id, csrf_headers=csrf_headers)
        peer_http = app.test_client()
        peer_csrf_headers = _signup_and_join_second_account(
            peer_http,
            workspace_id=workspace_id,
            workspace_token=workspace_token,
            username='HostedCookiePeer',
            password='hosted-cookie-test-key-peer',
            require_secure_cookie=False,
        )
        peer_player_id = _seed_second_account_player(
            peer_http,
            seeded=seeded,
            csrf_headers=peer_csrf_headers,
        )
        _assert_two_account_rest_privacy(
            peer_http,
            seeded=seeded,
            own_player_id=peer_player_id,
            csrf_headers=peer_csrf_headers,
        )
        _assert_socket_cookie_auth(socketio, app, http, seeded)
        _assert_socket_player_ownership(
            socketio,
            app,
            peer_http,
            seeded,
            own_player_id=peer_player_id,
        )
        proof_artifacts: dict[str, str] = {}
        if release_proof_suite:
            sensitive_values = [
                workspace_token,
                *csrf_headers.values(),
                *peer_csrf_headers.values(),
                *_client_cookie_values(http),
                *_client_cookie_values(peer_http),
            ]
            proof_artifacts = _run_release_proof_suite(
                http,
                peer_http,
                seeded=seeded,
                owner_csrf_headers=csrf_headers,
                peer_csrf_headers=peer_csrf_headers,
                target_url='',
                mode='isolated',
                output_dir=proof_output_dir,
                sensitive_values=sensitive_values,
            )
        _assert_role_downgrade(app, http, seeded)
        _assert_socket_cookie_auth(socketio, app, http, seeded)
        _assert_logout_clears_session(http, socketio, app, seeded, csrf_headers)
        peer_seeded = SeededHostedAuthRuntime(
            workspace_id=seeded.workspace_id,
            world_id=seeded.world_id,
            campaign_id=seeded.campaign_id,
            session_id=seeded.session_id,
            player_id=peer_player_id,
            private_marker='HOSTED_COOKIE_PEER_PRIVATE_ITEM',
        )
        _assert_logout_clears_session(
            peer_http,
            socketio,
            app,
            peer_seeded,
            peer_csrf_headers,
        )

        print(
            'Hosted cookie auth smoke passed: cookie-only account login, CSRF, '
            'two-account REST/Socket.IO privacy, fresh role downgrade, and logout cleanup verified.'
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
                'A second account could read its own private character detail but not the owner character detail',
                'Party, session-state, and export projections did not leak the owner private marker to the second account',
                'A second account could bind Socket.IO only as its owned player, not a guessed peer player',
                'Role downgrade removed admin/debug capabilities and support-bundle access',
                'Socket.IO still allowed normal player session join after role downgrade',
                'Both account logouts cleared account and CSRF cookies and rejected later API/socket access',
                *(
                    [
                        'Player cookie auth denied every operator capability check with the expected capability',
                        'Owner cookie auth completed an export/import round trip and removed both proof sessions',
                        'Owner cookie auth generated isolated beta SLO and incident baseline evidence',
                    ]
                    if release_proof_suite
                    else []
                ),
            ],
            proof_artifacts=proof_artifacts,
        )
    finally:
        _restore_runtime_env(env_snapshot)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.target_url and args.database_uri:
        parser.error('--database-uri cannot be combined with --target-url.')
    if args.release_proof_suite:
        resolved_output_dir = _resolved_output_dir(args.proof_output_dir)
        for filename in PROOF_ARTIFACT_FILENAMES:
            (resolved_output_dir / filename).unlink(missing_ok=True)
        if args.evidence_report is not None:
            evidence_path = args.evidence_report if args.evidence_report.is_absolute() else REPO_ROOT / args.evidence_report
            evidence_path.unlink(missing_ok=True)
    if args.target_url:
        payload = run_live_target_smoke(
            target_url=args.target_url,
            username=args.username,
            password=args.password,
            account_intent=args.account_intent,
            workspace_name=args.workspace_name,
            socketio_path=args.socketio_path,
            timeout_seconds=args.timeout_seconds,
            release_proof_suite=args.release_proof_suite,
            proof_output_dir=args.proof_output_dir,
        )
        if args.evidence_report is not None and payload:
            output_path = write_evidence_report(args.evidence_report, payload)
            print(f'[hosted-cookie-auth-smoke] Evidence report written to {output_path}.')
        return 0
    if args.database_uri:
        payload = run_smoke(
            database_uri=args.database_uri,
            release_proof_suite=args.release_proof_suite,
            proof_output_dir=args.proof_output_dir,
        )
        if args.evidence_report is not None:
            output_path = write_evidence_report(args.evidence_report, payload)
            print(f'[hosted-cookie-auth-smoke] Evidence report written to {output_path}.')
        return 0

    with tempfile.TemporaryDirectory(prefix='aidm-hosted-cookie-auth-') as tmp:
        db_path = pathlib.Path(tmp) / 'hosted-cookie-auth.sqlite'
        payload = run_smoke(
            database_uri=f'sqlite:///{db_path}',
            release_proof_suite=args.release_proof_suite,
            proof_output_dir=args.proof_output_dir,
        )
    if args.evidence_report is not None:
        output_path = write_evidence_report(args.evidence_report, payload)
        print(f'[hosted-cookie-auth-smoke] Evidence report written to {output_path}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
