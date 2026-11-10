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


SAVED_LOG = [
    {'message': 'The table gathers.', 'entry_type': 'system'},
    {'message': 'DM: The gate opens.', 'entry_type': 'dm'},
]


class _FakeHttp:
    def __init__(self, *, imported_messages: list[str] | None = None, archive_status: str = 'ended'):
        self.calls = []
        self.imported_messages = (
            imported_messages if imported_messages is not None else [entry['message'] for entry in SAVED_LOG]
        )
        self.archive_status = archive_status

    def get(self, path: str, *, headers: dict[str, str]):
        self.calls.append(('GET', path, headers, None))
        if path == '/api/sessions/12/export':
            return _FakeResponse(
                200,
                {
                    'format': 'aidm.save',
                    'version': 2,
                    'log': SAVED_LOG,
                    'characters': [{'player_id': 56}],
                },
            )
        if path == '/api/sessions/34/log?limit=200':
            return _FakeResponse(200, {'entries': [{'message': message} for message in self.imported_messages]})
        return _FakeResponse(404, {'error_code': 'not_found'})

    def post(self, path: str, *, headers: dict[str, str], json_payload: dict):
        self.calls.append(('POST', path, headers, json_payload))
        return _FakeResponse(
            201,
            {
                'session_id': 34,
                'mode': 'archive',
                'session': {'status': self.archive_status, 'is_current': False},
                'counts': {
                    'turn_events': 0,
                    'projected_log_entries': 0,
                    'log_entries': len(SAVED_LOG),
                    'session_state': 0,
                },
            },
        )

    def delete(self, path: str, *, headers: dict[str, str]):
        self.calls.append(('DELETE', path, headers, None))
        return _FakeResponse(200, {'deleted': True})


def test_run_round_trip_imports_the_save_as_a_read_only_archive():
    http = _FakeHttp()

    _payload, result = session_export_import_smoke.run_round_trip(
        http,
        headers={'Authorization': 'Bearer token'},
        session_id=12,
        player_id=56,
    )

    assert result.ok is True
    assert result.save_format == 'aidm.save/2'
    assert result.imported_log_entries == 2
    assert result.imported_log_matches is True
    assert result.archive_status == 'ended'
    assert result.selected_player_exported is True
    assert http.calls[0] == ('GET', '/api/sessions/12/export', {'Authorization': 'Bearer token'}, None)
    assert http.calls[1][3]['mode'] == 'archive'
    assert http.calls[-1][0] == 'DELETE'


def test_run_round_trip_flags_a_lost_log_line_a_playable_archive_and_a_missing_player():
    lost_line = session_export_import_smoke.run_round_trip(
        _FakeHttp(imported_messages=['DM: Something else.']),
        headers={},
        session_id=12,
    )[1]
    playable = session_export_import_smoke.run_round_trip(
        _FakeHttp(archive_status='active'),
        headers={},
        session_id=12,
    )[1]
    missing_player = session_export_import_smoke.run_round_trip(
        _FakeHttp(),
        headers={},
        session_id=12,
        player_id=99,
    )[1]

    assert lost_line.ok is False
    assert lost_line.imported_log_matches is False
    assert playable.ok is False
    assert missing_player.ok is False
    assert missing_player.selected_player_exported is False


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
                save_format='aidm.save/2',
                exported_log_entries=1,
                imported_log_entries=1,
                imported_log_entry_count=1,
                imported_log_matches=True,
                archive_status='ended',
                archive_is_current=False,
                selected_player_exported=True,
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
