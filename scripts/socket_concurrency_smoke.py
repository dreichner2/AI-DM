from __future__ import annotations

import argparse
import os
import pathlib
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass(frozen=True)
class SeededRuntime:
    world_id: int
    campaign_id: int
    session_one_id: int
    session_two_id: int
    player_one_id: int
    player_two_id: int
    player_three_id: int


@dataclass
class SocketResult:
    label: str
    events: list[dict[str, Any]]
    error: BaseException | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run a local Socket.IO concurrency smoke.')
    parser.add_argument(
        '--database-uri',
        default='',
        help='Optional SQLAlchemy database URI. Defaults to an isolated temporary SQLite database.',
    )
    parser.add_argument(
        '--stream-sleep-seconds',
        type=float,
        default=0.2,
        help='Artificial DM stream delay used to make same-session lock contention observable.',
    )
    return parser


def configure_runtime(database_uri: str):
    os.environ.update(
        {
            'AIDM_ENV': 'test',
            'AIDM_DATABASE_URI': database_uri,
            'AIDM_AUTO_CREATE_SCHEMA': 'true',
            'AIDM_LLM_PROVIDER': 'fallback',
            'AIDM_LLM_MODEL': 'socket-concurrency-smoke-v1',
            'AIDM_LLM_FALLBACK_MODELS': '',
            'AIDM_AUTH_REQUIRED': 'false',
            'AIDM_TELEMETRY_ENABLED': 'false',
            'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
            'AIDM_TURN_COORDINATOR_STORE': 'database',
            'AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS': '10',
            'AIDM_RATE_LIMIT_STORE': 'database',
            'AIDM_RATE_LIMIT_MAX_API_REQUESTS': '1000',
            'AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES': '1000',
        }
    )


def _post(client, path: str, payload: dict) -> dict:
    response = client.post(path, json=payload)
    if response.status_code >= 400:
        raise AssertionError(f'POST {path} failed: {response.status_code} {response.get_data(as_text=True)}')
    return response.get_json()


def _seed_runtime(http) -> SeededRuntime:
    creation_stats = {
        'ability_scores': {
            'strength': 15,
            'dexterity': 14,
            'constitution': 13,
            'intelligence': 12,
            'wisdom': 8,
            'charisma': 8,
        }
    }
    world = _post(http, '/api/worlds', {'name': 'Concurrency Smoke World', 'description': 'Socket smoke realm'})
    campaign = _post(
        http,
        '/api/campaigns',
        {
            'title': 'Concurrency Smoke Campaign',
            'description': 'Validates concurrent socket turns.',
            'world_id': world['world_id'],
        },
    )
    campaign_id = int(campaign['campaign_id'])
    player_one = _post(
        http,
        f'/api/players/campaigns/{campaign_id}/players',
        {'name': 'Aria', 'character_name': 'Aria', 'char_class': 'Wizard', 'level': 2, 'stats': creation_stats},
    )
    player_two = _post(
        http,
        f'/api/players/campaigns/{campaign_id}/players',
        {'name': 'Borin', 'character_name': 'Borin', 'char_class': 'Cleric', 'level': 2, 'stats': creation_stats},
    )
    player_three = _post(
        http,
        f'/api/players/campaigns/{campaign_id}/players',
        {'name': 'Cala', 'character_name': 'Cala', 'char_class': 'Ranger', 'level': 2, 'stats': creation_stats},
    )
    session_one = _post(http, '/api/sessions/start', {'campaign_id': campaign_id})
    session_two = _post(http, '/api/sessions/start', {'campaign_id': campaign_id})
    return SeededRuntime(
        world_id=int(world['world_id']),
        campaign_id=campaign_id,
        session_one_id=int(session_one['session_id']),
        session_two_id=int(session_two['session_id']),
        player_one_id=int(player_one['player_id']),
        player_two_id=int(player_two['player_id']),
        player_three_id=int(player_three['player_id']),
    )


def _connect_client(socketio, app, *, session_id: int, player_id: int):
    client = socketio.test_client(app, flask_test_client=app.test_client())
    if not client.is_connected():
        raise AssertionError('Socket client failed to connect.')
    client.emit('join_session', {'session_id': session_id, 'player_id': player_id})
    events = client.get_received()
    errors = [event for event in events if event.get('name') == 'error']
    if errors:
        raise AssertionError(f'join_session emitted errors: {errors}')
    return client


def _send_turn(
    *,
    barrier: threading.Barrier,
    client,
    label: str,
    payload: dict,
    result: SocketResult,
) -> None:
    try:
        barrier.wait(timeout=5)
        client.emit('send_message', payload)
        result.events = client.get_received()
    except BaseException as exc:  # noqa: BLE001 - preserve thread failure for the main assertion.
        result.error = exc


def _assert_socket_result(result: SocketResult) -> None:
    if result.error:
        raise AssertionError(f'{result.label} failed: {result.error}') from result.error
    errors = [event for event in result.events if event.get('name') == 'error']
    if errors:
        raise AssertionError(f'{result.label} emitted errors: {errors}')
    names = [event.get('name') for event in result.events]
    for expected in ('dm_response_start', 'dm_chunk', 'dm_response_end'):
        if expected not in names:
            raise AssertionError(f'{result.label} did not emit {expected}: {names}')


def _assert_persisted_turns(app, seeded: SeededRuntime) -> None:
    from aidm_server.models import DmTurn

    with app.app_context():
        session_one_turns = DmTurn.query.filter_by(session_id=seeded.session_one_id).order_by(DmTurn.turn_id.asc()).all()
        session_two_turns = DmTurn.query.filter_by(session_id=seeded.session_two_id).order_by(DmTurn.turn_id.asc()).all()
        if len(session_one_turns) != 2:
            raise AssertionError(f'Expected 2 same-session turns, found {len(session_one_turns)}.')
        if len(session_two_turns) != 1:
            raise AssertionError(f'Expected 1 different-session turn, found {len(session_two_turns)}.')
        session_one_players = {turn.player_id for turn in session_one_turns}
        if session_one_players != {seeded.player_one_id, seeded.player_two_id}:
            raise AssertionError(f'Same-session turn players mismatch: {session_one_players}')
        if session_two_turns[0].player_id != seeded.player_three_id:
            raise AssertionError(f'Different-session turn player mismatch: {session_two_turns[0].player_id}')


def _assert_queue_wait_metric(http, seeded: SeededRuntime) -> None:
    metrics_response = http.get('/api/metrics')
    if metrics_response.status_code != 200:
        raise AssertionError(f'/api/metrics failed: {metrics_response.status_code}')
    timings = (metrics_response.get_json() or {}).get('timings') or {}
    matching = [
        timing
        for key, timing in timings.items()
        if key.startswith('socket.turn_queue_wait_ms|')
        and f'session_id={seeded.session_one_id}' in key
        and f'campaign_id={seeded.campaign_id}' in key
    ]
    if not matching:
        raise AssertionError(f'No socket.turn_queue_wait_ms timing found for session {seeded.session_one_id}.')
    if not any(float(timing.get('max_ms') or 0) >= 1.0 for timing in matching):
        raise AssertionError(f'Same-session queue wait timing was too small: {matching}')


def run_smoke(*, database_uri: str, stream_sleep_seconds: float) -> None:
    configure_runtime(database_uri)

    from aidm_server.blueprints.socketio_events import register_socketio_events
    from aidm_server.database import ensure_schema
    from aidm_server.main import create_app, create_socketio
    import aidm_server.blueprints.socketio_events as socketio_events_module

    app = create_app()
    ensure_schema(app)
    socketio = create_socketio(app)
    register_socketio_events(socketio)
    http = app.test_client()
    seeded = _seed_runtime(http)

    def delayed_stream(user_input, context, speaking_player=None, rules_hint=None):
        del context, speaking_player, rules_hint
        time.sleep(max(0.0, stream_sleep_seconds))
        yield f'The concurrency smoke resolves the action: {str(user_input).strip()}'

    original_stream = socketio_events_module.query_dm_function_stream
    socketio_events_module.query_dm_function_stream = delayed_stream
    try:
        client_one = _connect_client(socketio, app, session_id=seeded.session_one_id, player_id=seeded.player_one_id)
        client_two = _connect_client(socketio, app, session_id=seeded.session_one_id, player_id=seeded.player_two_id)
        client_three = _connect_client(socketio, app, session_id=seeded.session_two_id, player_id=seeded.player_three_id)

        payloads = [
            (
                'same-session-one',
                client_one,
                {
                    'session_id': seeded.session_one_id,
                    'campaign_id': seeded.campaign_id,
                    'world_id': seeded.world_id,
                    'player_id': seeded.player_one_id,
                    'message': 'I inspect the locked gate.',
                    'client_message_id': 'concurrency-smoke-same-one',
                },
            ),
            (
                'same-session-two',
                client_two,
                {
                    'session_id': seeded.session_one_id,
                    'campaign_id': seeded.campaign_id,
                    'world_id': seeded.world_id,
                    'player_id': seeded.player_two_id,
                    'message': 'I watch the corridor for trouble.',
                    'client_message_id': 'concurrency-smoke-same-two',
                },
            ),
            (
                'different-session',
                client_three,
                {
                    'session_id': seeded.session_two_id,
                    'campaign_id': seeded.campaign_id,
                    'world_id': seeded.world_id,
                    'player_id': seeded.player_three_id,
                    'message': 'I study the second chamber.',
                    'client_message_id': 'concurrency-smoke-different',
                },
            ),
        ]
        barrier = threading.Barrier(len(payloads))
        results = [SocketResult(label=label, events=[]) for label, _client, _payload in payloads]
        threads = [
            threading.Thread(
                target=_send_turn,
                kwargs={'barrier': barrier, 'client': client, 'label': label, 'payload': payload, 'result': result},
                name=f'aidm-{label}',
            )
            for result, (label, client, payload) in zip(results, payloads, strict=True)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        for thread in threads:
            if thread.is_alive():
                raise AssertionError(f'{thread.name} did not finish.')
        for result in results:
            _assert_socket_result(result)
        _assert_persisted_turns(app, seeded)
        _assert_queue_wait_metric(http, seeded)
    finally:
        socketio_events_module.query_dm_function_stream = original_stream


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.database_uri:
        run_smoke(database_uri=args.database_uri, stream_sleep_seconds=args.stream_sleep_seconds)
    else:
        with tempfile.TemporaryDirectory(prefix='aidm-socket-concurrency-') as tmp:
            db_path = pathlib.Path(tmp) / 'socket-concurrency.sqlite'
            run_smoke(database_uri=f'sqlite:///{db_path}', stream_sleep_seconds=args.stream_sleep_seconds)
    print('Socket concurrency smoke passed: same-session queue lock and different-session socket turns persisted.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
