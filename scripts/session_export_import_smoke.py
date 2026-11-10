from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin

import requests


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_EVIDENCE_REPORT = REPO_ROOT / 'tmp' / 'release' / 'export-import-evidence.md'


@dataclass(frozen=True)
class SeededExportImportRuntime:
    campaign_id: int
    session_id: int
    player_id: int


@dataclass(frozen=True)
class ExportImportSmokeResult:
    source_session_id: int
    imported_session_id: int
    save_format: str
    exported_log_entries: int
    imported_log_entries: int
    imported_log_entry_count: int
    imported_log_matches: bool
    archive_status: str
    archive_is_current: bool
    selected_player_exported: bool
    cleanup_status_code: int | None
    ok: bool


class RequestsHttpClient:
    def __init__(self, base_url: str, *, timeout_seconds: float):
        self.base_url = base_url.rstrip('/') + '/'
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip('/'))

    def get(self, path: str, *, headers: dict[str, str]):
        return self.session.get(self._url(path), headers=headers, timeout=self.timeout_seconds)

    def post(self, path: str, *, headers: dict[str, str], json_payload: dict):
        return self.session.post(self._url(path), headers=headers, json=json_payload, timeout=self.timeout_seconds)

    def delete(self, path: str, *, headers: dict[str, str]):
        return self.session.delete(self._url(path), headers=headers, timeout=self.timeout_seconds)


class FlaskTestHttpClient:
    def __init__(self, client):
        self.client = client

    def get(self, path: str, *, headers: dict[str, str]):
        return self.client.get(path, headers=headers)

    def post(self, path: str, *, headers: dict[str, str], json_payload: dict):
        return self.client.post(path, headers=headers, json=json_payload)

    def delete(self, path: str, *, headers: dict[str, str]):
        return self.client.delete(path, headers=headers)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Verify session export/import round-trip behavior.')
    parser.add_argument('--database-uri', default='', help='Optional database URI for isolated mode.')
    parser.add_argument('--target-url', default='', help='Hosted/staging base URL. Omit for isolated local mode.')
    parser.add_argument('--auth-token', default=os.getenv('AIDM_EXPORT_IMPORT_AUTH_TOKEN') or os.getenv('AIDM_API_AUTH_TOKEN') or '')
    parser.add_argument('--workspace-id', default=os.getenv('AIDM_EXPORT_IMPORT_WORKSPACE_ID') or os.getenv('AIDM_WORKSPACE_ID') or '')
    parser.add_argument('--session-id', type=int, default=int(os.getenv('AIDM_EXPORT_IMPORT_SESSION_ID') or 0))
    parser.add_argument(
        '--player-id',
        type=int,
        default=int(os.getenv('AIDM_EXPORT_IMPORT_PLAYER_ID') or 0),
        help='Require this player\'s character in the exported save.',
    )
    parser.add_argument('--timeout-seconds', type=float, default=10.0)
    parser.add_argument(
        '--keep-imported-session',
        action='store_true',
        help='Do not delete the imported session after the smoke. Useful for hosted forensic review.',
    )
    parser.add_argument(
        '--evidence-report',
        nargs='?',
        const=DEFAULT_EVIDENCE_REPORT,
        default=None,
        type=pathlib.Path,
        help='Write Markdown or JSON export/import smoke evidence.',
    )
    return parser


def configure_runtime(database_uri: str) -> None:
    os.environ.update(
        {
            'AIDM_ENV': 'test',
            'AIDM_DATABASE_URI': database_uri,
            'AIDM_AUTO_CREATE_SCHEMA': 'true',
            'AIDM_AUTH_REQUIRED': 'false',
            'AIDM_LLM_PROVIDER': 'fallback',
            'AIDM_LLM_MODEL': 'session-export-import-smoke-v1',
            'AIDM_LLM_FALLBACK_MODELS': '',
            'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
            'AIDM_TELEMETRY_ENABLED': 'false',
            'AIDM_RATE_LIMIT_MAX_API_REQUESTS': '1000',
            'AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES': '1000',
        }
    )


def _auth_headers(*, auth_token: str, workspace_id: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    if auth_token:
        headers['Authorization'] = auth_token if auth_token.lower().startswith('bearer ') else f'Bearer {auth_token}'
    if workspace_id:
        headers['X-AIDM-Workspace-Id'] = workspace_id
    return headers


def _json(response, *, label: str) -> dict:
    if hasattr(response, 'get_json'):
        payload = response.get_json(silent=True)
        body = response.get_data(as_text=True)
    else:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        body = getattr(response, 'text', '')
    if not isinstance(payload, dict):
        raise AssertionError(f'{label} returned non-object JSON: {body[:500]}')
    return payload


def _assert_status(response, expected: int, *, label: str) -> dict:
    payload = _json(response, label=label)
    if response.status_code != expected:
        raise AssertionError(f'{label} expected HTTP {expected}, got {response.status_code}: {payload}')
    return payload


def _seed_isolated_runtime(database_uri: str) -> tuple[object, SeededExportImportRuntime]:
    configure_runtime(database_uri)

    from aidm_server.database import db, ensure_schema
    from aidm_server.main import create_app
    from aidm_server.models import Campaign, Player, Session, SessionLogEntry, SessionState, World, safe_json_dumps
    from aidm_server.turn_events import DM_RESPONSE_EVENT, PLAYER_MESSAGE_EVENT, record_turn_event

    app = create_app()
    ensure_schema(app)
    with app.app_context():
        world = World(name='Export Import Smoke World', description='export/import smoke')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(
            title='Export Import Smoke Campaign',
            description='export/import smoke campaign',
            world_id=world.world_id,
            workspace_id='owner',
            current_quest='Prove the round trip',
            location='Smoke Gate',
        )
        db.session.add(campaign)
        db.session.flush()
        player = Player(
            campaign_id=campaign.campaign_id,
            workspace_id='owner',
            name='Export Player',
            character_name='Export Sentinel',
            class_='Ranger',
            level=2,
        )
        db.session.add(player)
        db.session.flush()
        session = Session(campaign_id=campaign.campaign_id, name='Export Import Source', status='active')
        db.session.add(session)
        db.session.flush()
        db.session.add(
            SessionState(
                session_id=session.session_id,
                current_location='Smoke Gate',
                current_quest='Prove the round trip',
                rolling_summary='The party prepared a clean export/import proof.',
                active_segments=safe_json_dumps([{'title': 'Export Gate'}], []),
                memory_snippets=safe_json_dumps([{'summary': 'Export smoke memory'}], []),
            )
        )
        db.session.add(
            SessionLogEntry(
                session_id=session.session_id,
                message='The smoke table gathered at the export gate.',
                entry_type='system',
                metadata_json=safe_json_dumps({'source': 'export_import_smoke'}, {}),
            )
        )
        record_turn_event(
            session_id=session.session_id,
            campaign_id=campaign.campaign_id,
            player_id=player.player_id,
            event_type=PLAYER_MESSAGE_EVENT,
            payload={'speaker': 'Export Sentinel', 'message': 'I mark the export gate.'},
        )
        record_turn_event(
            session_id=session.session_id,
            campaign_id=campaign.campaign_id,
            player_id=player.player_id,
            event_type=DM_RESPONSE_EVENT,
            payload={'message': 'The export gate glows with a stable record.'},
        )
        db.session.commit()
        seeded = SeededExportImportRuntime(
            campaign_id=int(campaign.campaign_id),
            session_id=int(session.session_id),
            player_id=int(player.player_id),
        )
    return app, seeded


def run_round_trip(
    http,
    *,
    headers: dict[str, str],
    session_id: int,
    player_id: int | None = None,
    keep_imported_session: bool = False,
) -> tuple[dict, ExportImportSmokeResult]:
    """Export a session's save document and import it back as a read-only archive."""

    export_path = f'/api/sessions/{session_id}/export'
    document = _assert_status(http.get(export_path, headers=headers), 200, label=f'GET {export_path}')
    log_entries = document.get('log') if isinstance(document.get('log'), list) else []
    characters = document.get('characters') if isinstance(document.get('characters'), list) else []
    if not log_entries:
        raise AssertionError('Exported save has no log entries; choose a session with a recorded story.')

    imported_session_id: int | None = None
    cleanup_status_code = None
    try:
        import_payload = _assert_status(
            http.post('/api/sessions/import', headers=headers, json_payload={**document, 'mode': 'archive'}),
            201,
            label='POST /api/sessions/import',
        )
        imported_session_id = int(import_payload['session_id'])
        counts = import_payload.get('counts') if isinstance(import_payload.get('counts'), dict) else {}
        archive = import_payload.get('session') if isinstance(import_payload.get('session'), dict) else {}
        imported_log_payload = _assert_status(
            http.get(f'/api/sessions/{imported_session_id}/log?limit=200', headers=headers),
            200,
            label=f'GET /api/sessions/{imported_session_id}/log',
        )
        imported_logs = imported_log_payload.get('entries') if isinstance(imported_log_payload.get('entries'), list) else []
    finally:
        had_prior_error = sys.exc_info()[0] is not None
        cleanup_error = ''
        if imported_session_id is not None and not keep_imported_session:
            try:
                cleanup_response = http.delete(f'/api/sessions/{imported_session_id}?hard=true', headers=headers)
                cleanup_status_code = int(cleanup_response.status_code)
                if cleanup_status_code != 200:
                    cleanup_error = f'DELETE imported session expected HTTP 200, got {cleanup_status_code}.'
            except Exception as exc:  # pragma: no cover - transport failures are environment-specific
                cleanup_error = f'Imported session cleanup request failed: {type(exc).__name__}.'
        if cleanup_error:
            if had_prior_error:
                print(f'[session-export-import-smoke][warning] {cleanup_error}', file=sys.stderr)
            else:
                raise AssertionError(cleanup_error)

    assert imported_session_id is not None

    exported_messages = [str(entry.get('message') or '') for entry in log_entries if isinstance(entry, dict)]
    imported_messages = [str(entry.get('message') or '') for entry in imported_logs if isinstance(entry, dict)]
    imported_log_matches = bool(imported_messages) and imported_messages == exported_messages[-len(imported_messages):]
    character_ids = {character.get('player_id') for character in characters if isinstance(character, dict)}
    save_format = f"{document.get('format')}/{document.get('version')}"
    result = ExportImportSmokeResult(
        source_session_id=session_id,
        imported_session_id=imported_session_id,
        save_format=save_format,
        exported_log_entries=len(log_entries),
        imported_log_entries=int(counts.get('log_entries') or 0),
        imported_log_entry_count=len(imported_logs),
        imported_log_matches=imported_log_matches,
        archive_status=str(archive.get('status') or ''),
        archive_is_current=archive.get('is_current') is True,
        selected_player_exported=player_id is None or player_id in character_ids,
        cleanup_status_code=cleanup_status_code,
        ok=(
            save_format == 'aidm.save/2'
            and int(counts.get('log_entries') or 0) == len(log_entries)
            and imported_log_matches
            and archive.get('status') == 'ended'
            and archive.get('is_current') is False
            and (player_id is None or player_id in character_ids)
            and (keep_imported_session or cleanup_status_code == 200)
        ),
    )
    return document, result


def evidence_payload(
    *,
    mode: str,
    target_url: str,
    workspace_id: str,
    generated_at: str,
    result: ExportImportSmokeResult,
) -> dict:
    return {
        'status': 'passed' if result.ok else 'failed',
        'generated_at': generated_at,
        'mode': mode,
        'target_url': target_url,
        'workspace_id': workspace_id,
        'result': asdict(result),
    }


def render_evidence_markdown(payload: dict) -> str:
    result = payload['result']
    return '\n'.join(
        [
            '# Session Export/Import Evidence',
            '',
            f"- Status: {payload['status']}",
            f"- Generated: {payload['generated_at']}",
            f"- Mode: {payload['mode']}",
            f"- Target URL: `{payload['target_url'] or 'isolated local runtime'}`",
            f"- Workspace ID: `{payload['workspace_id'] or 'owner'}`",
            f"- Source session ID: {result['source_session_id']}",
            f"- Imported session ID: {result['imported_session_id']}",
            f"- Save format: {result['save_format']}",
            f"- Exported log entries: {result['exported_log_entries']}",
            f"- Imported log entries: {result['imported_log_entries']}",
            f"- Imported log entries read back: {result['imported_log_entry_count']}",
            f"- Imported log matches the save: {result['imported_log_matches']}",
            f"- Archive status: {result['archive_status']}",
            f"- Archive is the current chapter: {result['archive_is_current']}",
            f"- Selected player exported: {result['selected_player_exported']}",
            f"- Cleanup status code: {result['cleanup_status_code']}",
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


def run_isolated_smoke(*, database_uri: str) -> dict:
    app, seeded = _seed_isolated_runtime(database_uri)
    http = FlaskTestHttpClient(app.test_client())
    _export_payload, result = run_round_trip(
        http,
        headers={},
        session_id=seeded.session_id,
        player_id=seeded.player_id,
    )
    return evidence_payload(
        mode='isolated',
        target_url='',
        workspace_id='owner',
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        result=result,
    )


def run_live_target_smoke(
    *,
    target_url: str,
    auth_token: str,
    workspace_id: str,
    session_id: int,
    player_id: int | None,
    timeout_seconds: float,
    keep_imported_session: bool,
) -> dict:
    http = RequestsHttpClient(target_url, timeout_seconds=timeout_seconds)
    headers = _auth_headers(auth_token=auth_token, workspace_id=workspace_id)
    _export_payload, result = run_round_trip(
        http,
        headers=headers,
        session_id=session_id,
        player_id=player_id,
        keep_imported_session=keep_imported_session,
    )
    return evidence_payload(
        mode='live-target',
        target_url=target_url,
        workspace_id=workspace_id,
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        result=result,
    )


def _print_summary(payload: dict) -> None:
    result = payload['result']
    if payload['status'] == 'passed':
        print(
            'Session export/import smoke passed: the save document imported as a read-only archive '
            'with its log intact, and cleanup completed.'
        )
        return
    print('[session-export-import-smoke][error] Export/import smoke failed:', file=sys.stderr)
    print(json.dumps(result, indent=2, sort_keys=True), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.target_url and args.database_uri:
        parser.error('--database-uri cannot be combined with --target-url.')
    if args.target_url:
        missing = []
        if not args.auth_token:
            missing.append('--auth-token')
        if not args.workspace_id:
            missing.append('--workspace-id')
        if args.session_id <= 0:
            missing.append('--session-id')
        if missing:
            parser.error('live target mode requires ' + ', '.join(missing))
        payload = run_live_target_smoke(
            target_url=args.target_url,
            auth_token=args.auth_token,
            workspace_id=args.workspace_id,
            session_id=args.session_id,
            player_id=args.player_id or None,
            timeout_seconds=args.timeout_seconds,
            keep_imported_session=args.keep_imported_session,
        )
    elif args.database_uri:
        payload = run_isolated_smoke(database_uri=args.database_uri)
    else:
        with tempfile.TemporaryDirectory(prefix='aidm-session-export-import-') as tmp:
            db_path = pathlib.Path(tmp) / 'session-export-import.sqlite'
            payload = run_isolated_smoke(database_uri=f'sqlite:///{db_path}')

    _print_summary(payload)
    if args.evidence_report is not None:
        output_path = write_evidence_report(args.evidence_report, payload)
        print(f'[session-export-import-smoke] Evidence report written to {output_path}.')
    return 0 if payload['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
