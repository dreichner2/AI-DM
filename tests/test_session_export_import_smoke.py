from __future__ import annotations

import os
import subprocess
import sys

import pytest

from scripts import session_export_import_smoke


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeHttp:
    def __init__(self, *, log_message: str = 'Projected cleanly.'):
        self.calls = []
        self.log_message = log_message

    def get(self, path: str, *, headers: dict[str, str]):
        self.calls.append(('GET', path, headers, None))
        if path.startswith('/api/sessions/12/export'):
            return _FakeResponse(
                200,
                {
                    'turnEvents': [
                        {
                            'event_id': 1,
                            'turn_id': 2,
                            'event_type': 'player_message',
                            'payload': {'message': 'hello'},
                            'created_at': '2026-06-19T00:00:00+00:00',
                        }
                    ],
                    'logEntries': [
                        {
                            'message': 'This stale source log should not be duplicated when turn events are present.',
                            'entry_type': 'system',
                        }
                    ],
                },
            )
        if path == '/api/sessions/34/log?limit=200':
            return _FakeResponse(200, {'entries': [{'message': self.log_message}]})
        return _FakeResponse(404, {'error_code': 'not_found'})

    def post(self, path: str, *, headers: dict[str, str], json_payload: dict):
        self.calls.append(('POST', path, headers, json_payload))
        return _FakeResponse(
            201,
            {
                'session_id': 34,
                'counts': {
                    'turn_events': 1,
                    'projected_log_entries': 1,
                    'log_entries': 0,
                    'session_state': 0,
                },
            },
        )

    def delete(self, path: str, *, headers: dict[str, str]):
        self.calls.append(('DELETE', path, headers, None))
        return _FakeResponse(200, {'deleted': True})


def test_run_round_trip_proves_events_project_without_raw_log_duplication():
    http = _FakeHttp()

    _payload, result = session_export_import_smoke.run_round_trip(
        http,
        headers={'Authorization': 'Bearer token'},
        session_id=12,
        player_id=56,
    )

    assert result.ok is True
    assert result.imported_turn_events == 1
    assert result.projected_log_entries == 1
    assert result.imported_log_entries == 0
    assert result.duplicate_marker_found is False
    assert http.calls[0] == ('GET', '/api/sessions/12/export?player_id=56', {'Authorization': 'Bearer token'}, None)
    assert http.calls[-1][0] == 'DELETE'


def test_run_round_trip_flags_duplicate_source_log_marker():
    http = _FakeHttp(log_message='This stale source log should not be duplicated when turn events are present.')

    _payload, result = session_export_import_smoke.run_round_trip(http, headers={}, session_id=12)

    assert result.ok is False
    assert result.duplicate_marker_found is True


def test_run_round_trip_cleans_imported_session_when_post_import_validation_fails():
    class FailingImportedLogHttp(_FakeHttp):
        def get(self, path: str, *, headers: dict[str, str]):
            if path == '/api/sessions/34/log?limit=200':
                self.calls.append(('GET', path, headers, None))
                return _FakeResponse(500, {'error_code': 'temporary_failure'})
            return super().get(path, headers=headers)

    http = FailingImportedLogHttp()

    with pytest.raises(AssertionError, match='expected HTTP 200, got 500'):
        session_export_import_smoke.run_round_trip(http, headers={}, session_id=12)

    assert http.calls[-1] == ('DELETE', '/api/sessions/34?hard=true', {}, None)


def test_session_export_import_smoke_uses_isolated_database_by_default(tmp_path):
    external_db_path = tmp_path / 'should-not-be-created.sqlite'
    env = {
        **os.environ,
        'AIDM_DATABASE_URI': f'sqlite:///{external_db_path}',
    }

    result = subprocess.run(
        [sys.executable, 'scripts/session_export_import_smoke.py'],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert 'Session export/import smoke passed' in result.stdout
    assert not external_db_path.exists()


def test_session_export_import_smoke_dispatches_live_target_mode(monkeypatch):
    captured = {}

    def fake_run_live_target_smoke(**kwargs):
        captured.update(kwargs)
        return session_export_import_smoke.evidence_payload(
            mode='live-target',
            target_url=kwargs['target_url'],
            workspace_id=kwargs['workspace_id'],
            generated_at='2026-06-19T00:00:00+00:00',
            result=session_export_import_smoke.ExportImportSmokeResult(
                source_session_id=kwargs['session_id'],
                imported_session_id=999,
                exported_turn_events=1,
                exported_log_entries=1,
                imported_turn_events=1,
                projected_log_entries=1,
                imported_log_entries=0,
                imported_log_entry_count=1,
                duplicate_marker_found=False,
                cleanup_status_code=200,
                ok=True,
            ),
        )

    monkeypatch.setattr(session_export_import_smoke, 'run_live_target_smoke', fake_run_live_target_smoke)

    exit_code = session_export_import_smoke.main(
        [
            '--target-url',
            'https://aidm.example.test',
            '--auth-token',
            'operator-token',
            '--workspace-id',
            'owner',
            '--session-id',
            '12',
            '--player-id',
            '56',
            '--timeout-seconds',
            '3',
            '--keep-imported-session',
        ]
    )

    assert exit_code == 0
    assert captured == {
        'target_url': 'https://aidm.example.test',
        'auth_token': 'operator-token',
        'workspace_id': 'owner',
        'session_id': 12,
        'player_id': 56,
        'timeout_seconds': 3.0,
        'keep_imported_session': True,
    }


def test_session_export_import_smoke_requires_target_context():
    with pytest.raises(SystemExit) as exc_info:
        session_export_import_smoke.main(['--target-url', 'https://aidm.example.test'])

    assert exc_info.value.code == 2
