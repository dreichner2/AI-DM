from __future__ import annotations

import argparse
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aidm_server.env_loader import load_runtime_env  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description='Run the local AI-DM beta smoke flow.')
    parser.add_argument(
        '--use-local-env',
        action='store_true',
        help='Load .env/.env.local and use the configured database/provider instead of isolated fallback mode.',
    )
    return parser.parse_args()


def configure_runtime(*, use_local_env: bool):
    if use_local_env:
        load_runtime_env(REPO_ROOT)
        os.environ.setdefault('AIDM_ENV', 'test')
        return

    os.environ.update(
        {
            'AIDM_ENV': 'test',
            'AIDM_DATABASE_URI': 'sqlite:///:memory:',
            'AIDM_AUTO_CREATE_SCHEMA': 'true',
            'AIDM_LLM_PROVIDER': 'fallback',
            'AIDM_LLM_MODEL': 'deterministic-v1',
            'AIDM_LLM_FALLBACK_MODELS': '',
            'AIDM_AUTH_REQUIRED': 'false',
            'AIDM_TELEMETRY_ENABLED': 'false',
            'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
        }
    )


def main():
    args = parse_args()
    configure_runtime(use_local_env=args.use_local_env)

    from aidm_server.blueprints.socketio_events import register_socketio_events
    from aidm_server.database import ensure_schema
    from aidm_server.main import create_app, create_socketio

    app = create_app()
    ensure_schema(app)
    socketio = create_socketio(app)
    register_socketio_events(socketio)

    http = app.test_client()

    world_resp = http.post('/api/worlds', json={'name': 'Smoke World', 'description': 'Smoke test realm'})
    assert world_resp.status_code == 201, world_resp.get_data(as_text=True)
    world_id = world_resp.get_json()['world_id']

    camp_resp = http.post(
        '/api/campaigns',
        json={'title': 'Smoke Campaign', 'description': 'Smoke campaign', 'world_id': world_id},
    )
    assert camp_resp.status_code == 201, camp_resp.get_data(as_text=True)
    campaign_id = camp_resp.get_json()['campaign_id']

    player_resp = http.post(
        f'/api/players/campaigns/{campaign_id}/players',
        json={'name': 'Smoke Player', 'character_name': 'Ember', 'char_class': 'Wizard', 'level': 2},
    )
    assert player_resp.status_code == 201, player_resp.get_data(as_text=True)
    player_id = player_resp.get_json()['player_id']

    session_resp = http.post('/api/sessions/start', json={'campaign_id': campaign_id})
    assert session_resp.status_code == 201, session_resp.get_data(as_text=True)
    session_id = session_resp.get_json()['session_id']

    sio = socketio.test_client(app, flask_test_client=app.test_client())
    assert sio.is_connected()

    sio.emit('join_session', {'session_id': session_id, 'player_id': player_id})
    sio.get_received()

    sio.emit(
        'send_message',
        {
            'session_id': session_id,
            'campaign_id': campaign_id,
            'world_id': world_id,
            'player_id': player_id,
            'message': 'I inspect the ancient gate and push it open.',
        },
    )
    events = sio.get_received()

    event_names = [event['name'] for event in events]
    assert 'dm_response_start' in event_names
    assert 'dm_chunk' in event_names
    assert 'dm_response_end' in event_names

    log_resp = http.get(f'/api/sessions/{session_id}/log')
    assert log_resp.status_code == 200, log_resp.get_data(as_text=True)

    state_resp = http.get(f'/api/sessions/{session_id}/state')
    assert state_resp.status_code == 200, state_resp.get_data(as_text=True)

    end_resp = http.post(f'/api/sessions/{session_id}/end')
    assert end_resp.status_code == 200, end_resp.get_data(as_text=True)

    print('Smoke flow passed: world->campaign->player->session->message->state/log->recap')


if __name__ == '__main__':
    main()
