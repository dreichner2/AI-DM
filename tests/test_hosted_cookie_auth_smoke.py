from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from scripts import hosted_cookie_auth_smoke


def test_hosted_cookie_auth_smoke_uses_isolated_database_by_default(tmp_path):
    external_db_path = tmp_path / 'should-not-be-created.sqlite'
    env = {
        **os.environ,
        'AIDM_DATABASE_URI': f'sqlite:///{external_db_path}',
    }

    result = subprocess.run(
        [sys.executable, 'scripts/hosted_cookie_auth_smoke.py'],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert 'Hosted cookie auth smoke passed' in result.stdout
    assert not external_db_path.exists()


def test_hosted_cookie_auth_smoke_writes_evidence_report(tmp_path):
    evidence_path = tmp_path / 'hosted-cookie-auth-evidence.md'

    exit_code = hosted_cookie_auth_smoke.main(['--evidence-report', str(evidence_path)])

    assert exit_code == 0
    markdown = evidence_path.read_text(encoding='utf-8')
    assert '# Hosted Cookie Auth Evidence' in markdown
    assert '- Status: passed' in markdown
    assert '- Mode: isolated' in markdown
    assert 'Cookie-only login used an HttpOnly account cookie' in markdown
    assert 'Role downgrade removed admin/debug capabilities' in markdown


def test_hosted_cookie_auth_smoke_writes_json_evidence_report(tmp_path):
    evidence_path = tmp_path / 'hosted-cookie-auth-evidence.json'

    exit_code = hosted_cookie_auth_smoke.main(['--evidence-report', str(evidence_path)])

    assert exit_code == 0
    payload = json.loads(evidence_path.read_text(encoding='utf-8'))
    assert payload['status'] == 'passed'
    assert payload['mode'] == 'isolated'
    assert payload['target_url'] == ''
    assert len(payload['checks']) >= 6


def test_hosted_cookie_auth_smoke_dispatches_live_target_mode(monkeypatch):
    captured = {}

    def fake_run_live_target_smoke(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(hosted_cookie_auth_smoke, 'run_live_target_smoke', fake_run_live_target_smoke)

    exit_code = hosted_cookie_auth_smoke.main(
        [
            '--target-url',
            'https://aidm.example.test',
            '--username',
            'tester',
            '--password',
            'secret',
            '--account-intent',
            'login',
            '--workspace-name',
            'Smoke Workspace',
            '--socketio-path',
            'custom-socket.io',
            '--timeout-seconds',
            '3',
        ]
    )

    assert exit_code == 0
    assert captured == {
        'target_url': 'https://aidm.example.test',
        'username': 'tester',
        'password': 'secret',
        'account_intent': 'login',
        'workspace_name': 'Smoke Workspace',
        'socketio_path': 'custom-socket.io',
        'timeout_seconds': 3.0,
    }


def test_hosted_cookie_auth_smoke_rejects_database_uri_with_target_url():
    with pytest.raises(SystemExit) as exc_info:
        hosted_cookie_auth_smoke.main(
            [
                '--target-url',
                'https://aidm.example.test',
                '--database-uri',
                'sqlite:///should-not-be-used.sqlite',
            ]
        )

    assert exc_info.value.code == 2


def test_live_socket_cookie_auth_reuses_http_session_and_forces_websocket(monkeypatch):
    captured = {}

    class FakeSocketClient:
        connected = True

        def __init__(self, **kwargs):
            captured['client_kwargs'] = kwargs

        def on(self, _event_name):
            return lambda callback: callback

        def connect(self, url, **kwargs):
            captured['connect_url'] = url
            captured['connect_kwargs'] = kwargs

        def emit(self, event_name, payload):
            captured['emit'] = (event_name, payload)

        def sleep(self, seconds):
            captured['sleep'] = seconds

        def disconnect(self):
            self.connected = False

    class FakeHttp:
        base_url = 'https://aidm.example.test/'
        session = object()

        @staticmethod
        def cookie_header():
            return 'aidm_account_session=cookie-value; aidm_csrf_token=csrf-value'

    seeded = hosted_cookie_auth_smoke.SeededHostedAuthRuntime(
        workspace_id='workspace-one',
        world_id=1,
        campaign_id=2,
        session_id=3,
        player_id=4,
    )
    monkeypatch.setattr(hosted_cookie_auth_smoke, 'HostedSocketClient', FakeSocketClient)

    hosted_cookie_auth_smoke._assert_live_socket_cookie_auth(
        FakeHttp(),
        seeded,
        socketio_path='socket.io',
        timeout_seconds=5,
    )

    assert captured['client_kwargs']['http_session'] is FakeHttp.session
    assert captured['connect_url'] == FakeHttp.base_url
    assert captured['connect_kwargs']['transports'] == ['websocket']
    assert 'headers' not in captured['connect_kwargs']
    assert captured['emit'][0] == 'join_session'


def test_hosted_socket_client_waits_before_namespace_connect(monkeypatch):
    calls = []

    monkeypatch.setattr(
        hosted_cookie_auth_smoke.time,
        'sleep',
        lambda seconds: calls.append(('sleep', seconds)),
    )
    monkeypatch.setattr(
        hosted_cookie_auth_smoke.socketio.Client,
        '_handle_eio_connect',
        lambda _client: calls.append(('connect', None)),
    )
    client = object.__new__(hosted_cookie_auth_smoke.HostedSocketClient)

    client._handle_eio_connect()

    assert calls == [
        ('sleep', hosted_cookie_auth_smoke.LIVE_SOCKET_NAMESPACE_SETTLE_SECONDS),
        ('connect', None),
    ]
