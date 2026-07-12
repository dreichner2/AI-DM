from __future__ import annotations

import os
import subprocess
import sys

import pytest

from scripts import security_forbidden_smoke


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHttp:
    def __init__(self, overrides: dict[str, tuple[int, dict]] | None = None):
        self.overrides = overrides or {}
        self.calls = []

    def request(self, method: str, path: str, *, headers: dict[str, str], json_payload: dict | None):
        self.calls.append((method, path, headers, json_payload))
        if path in self.overrides:
            status_code, payload = self.overrides[path]
            return _FakeResponse(status_code, payload)
        expected = next(
            spec.expected_capability
            for spec in security_forbidden_smoke.check_specs(campaign_id=12, session_id=34)
            if spec.path_template == path
        )
        return _FakeResponse(
            403,
            {
                'error_code': 'forbidden',
                'details': {'required_capability': expected},
            },
        )


def test_run_forbidden_checks_requires_expected_capabilities():
    http = _FakeHttp()

    results = security_forbidden_smoke.run_forbidden_checks(
        http,
        account_token='player-token',
        workspace_id='owner',
        campaign_id=12,
        session_id=34,
    )

    assert all(result.ok for result in results)
    assert {result.expected_capability for result in results} == {'dm_runtime_control', 'dm_authoring', 'debug_read'}
    assert http.calls[0][2] == {
        'Authorization': 'Bearer player-token',
        'X-AIDM-Workspace-Id': 'owner',
    }


def test_run_forbidden_checks_accepts_cookie_csrf_headers_without_bearer_token():
    http = _FakeHttp()
    headers = {
        'X-AIDM-Workspace-Id': 'owner',
        'X-AIDM-CSRF-Token': 'csrf-companion-value',
    }

    results = security_forbidden_smoke.run_forbidden_checks(
        http,
        workspace_id='owner',
        campaign_id=12,
        session_id=34,
        headers=headers,
    )

    assert all(result.ok for result in results)
    assert all(call[2] == headers for call in http.calls)
    assert all('Authorization' not in call[2] for call in http.calls)


def test_run_forbidden_checks_fails_on_wrong_capability():
    http = _FakeHttp(
        {
            '/api/beta/audits': (
                403,
                {
                    'error_code': 'forbidden',
                    'details': {'required_capability': 'dm_authoring'},
                },
            )
        }
    )

    results = security_forbidden_smoke.run_forbidden_checks(
        http,
        account_token='player-token',
        workspace_id='owner',
        campaign_id=12,
        session_id=34,
    )

    failed = [result for result in results if not result.ok]
    assert len(failed) == 1
    assert failed[0].label == 'Beta audits'
    assert failed[0].expected_capability == 'debug_read'
    assert failed[0].required_capability == 'dm_authoring'


def test_run_forbidden_checks_redacts_sensitive_response_excerpts():
    http = _FakeHttp(
        {
            '/api/beta/audits': (
                500,
                {
                    'error_code': 'server_error',
                    'message': 'Authorization: Bearer leaked-token-12345',
                    'account_token': 'player-token',
                    'password': 'secret-password',
                },
            )
        }
    )

    results = security_forbidden_smoke.run_forbidden_checks(
        http,
        account_token='player-token',
        workspace_id='owner',
        campaign_id=12,
        session_id=34,
    )

    failed = next(result for result in results if result.label == 'Beta audits')
    assert failed.ok is False
    assert '<redacted>' in failed.response_excerpt
    assert 'leaked-token-12345' not in failed.response_excerpt
    assert 'player-token' not in failed.response_excerpt
    assert 'secret-password' not in failed.response_excerpt


def test_security_forbidden_smoke_uses_isolated_database_by_default(tmp_path):
    external_db_path = tmp_path / 'should-not-be-created.sqlite'
    env = {
        **os.environ,
        'AIDM_DATABASE_URI': f'sqlite:///{external_db_path}',
    }

    result = subprocess.run(
        [sys.executable, 'scripts/security_forbidden_smoke.py'],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert 'Security forbidden smoke passed' in result.stdout
    assert not external_db_path.exists()


def test_security_forbidden_smoke_dispatches_live_target_mode(monkeypatch):
    captured = {}

    def fake_run_live_target_smoke(**kwargs):
        captured.update(kwargs)
        return (
            security_forbidden_smoke.evidence_payload(
                mode='live-target',
                target_url=kwargs['target_url'],
                workspace_id=kwargs['workspace_id'],
                campaign_id=kwargs['campaign_id'],
                session_id=kwargs['session_id'],
                generated_at='2026-06-19T00:00:00+00:00',
                results=[],
            ),
            [],
        )

    monkeypatch.setattr(security_forbidden_smoke, 'run_live_target_smoke', fake_run_live_target_smoke)

    exit_code = security_forbidden_smoke.main(
        [
            '--target-url',
            'https://aidm.example.test',
            '--account-token',
            'player-token',
            '--workspace-id',
            'owner',
            '--campaign-id',
            '12',
            '--session-id',
            '34',
            '--timeout-seconds',
            '3',
        ]
    )

    assert exit_code == 0
    assert captured == {
        'target_url': 'https://aidm.example.test',
        'account_token': 'player-token',
        'workspace_id': 'owner',
        'campaign_id': 12,
        'session_id': 34,
        'timeout_seconds': 3.0,
    }


def test_security_forbidden_smoke_requires_target_context():
    with pytest.raises(SystemExit) as exc_info:
        security_forbidden_smoke.main(['--target-url', 'https://aidm.example.test'])

    assert exc_info.value.code == 2
