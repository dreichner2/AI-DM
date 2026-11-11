"""A hero who is down cannot act: the turn is refused before narration and changes nothing."""

from __future__ import annotations

import pytest
import requests

from aidm_server.database import db
from aidm_server.models import DmTurn, Player, Session, safe_json_dumps, safe_json_loads


REFUSAL = 'You are down and cannot act.'


@pytest.fixture()
def downed(app, client, socketio, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    response = client.post('/api/onboarding/play-now', json={})
    ids = {key: response.get_json()[key] for key in ('session_id', 'campaign_id', 'player_id', 'workspace_id')}

    def knock_down(*conditions: str) -> None:
        with app.app_context():
            player = db.session.get(Player, ids['player_id'])
            stats = safe_json_loads(player.stats, {})
            stats.update(current_hp=0, hp_current=0, conditions=list(conditions), death_saves={'successes': 0, 'failures': 0})
            player.stats = safe_json_dumps(stats, {})
            db.session.commit()

    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id'], 'workspace_id': ids['workspace_id']})
    socket_client.get_received()

    def send(message: str, intent: dict | None = None) -> list[dict]:
        socket_client.emit(
            'send_message',
            {
                'session_id': ids['session_id'],
                'campaign_id': ids['campaign_id'],
                'player_id': ids['player_id'],
                'message': message,
                **({'action_intent': {**intent, 'text': message}} if intent else {}),
            },
        )
        return socket_client.get_received()

    def snapshot() -> dict:
        with app.app_context():
            return safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})

    def last_turn() -> DmTurn:
        with app.app_context():
            turn = DmTurn.query.filter_by(session_id=ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
            db.session.expunge(turn)
            return turn

    yield knock_down, send, snapshot, last_turn
    socket_client.disconnect()


def _refusals(received: list[dict]) -> list[str]:
    return [event['args'][0]['error'] for event in received if event['name'] == 'error']


def _game_state(snapshot: dict) -> dict:
    return {key: value for key, value in snapshot.items() if key not in {'lastUpdatedAt', 'stateChangeLedger'}}


@pytest.mark.parametrize(
    ('message', 'intent'),
    [
        ('I get up and walk back to the inn.', None),
        ('go to the old north road', None),
        ('Read the caravan ledger.', {'kind': 'object', 'source': 'scene_panel', 'object': {'id': 'inn_caravan_ledger', 'action': 'inspect'}}),
        ('The party travels to The Old North Road.', {'kind': 'travel', 'source': 'scene_panel', 'location': {'id': 'loc_old_north_road', 'name': 'The Old North Road'}}),
    ],
    ids=['free-words', 'mapped-words', 'object', 'travel'],
)
def test_a_downed_hero_is_refused_before_narration_and_nothing_changes(downed, message, intent):
    knock_down, send, snapshot, last_turn = downed
    knock_down('unconscious', 'dying')
    before = snapshot()

    received = send(message, intent)
    turn = last_turn()

    assert _refusals(received) == [REFUSAL]
    assert not any(event['name'] == 'dm_response_end' for event in received)
    assert turn.dm_output == REFUSAL and turn.llm_provider == 'rules_engine'
    assert _game_state(snapshot()) == _game_state(before)


def test_an_out_of_character_message_still_goes_through(downed):
    knock_down, send, _snapshot, _last_turn = downed
    knock_down('unconscious', 'stable')

    received = send('brb, getting tea', {'kind': 'ooc', 'source': 'composer'})

    assert _refusals(received) == []
    assert any(event['name'] == 'dm_response_end' for event in received)


def test_a_stable_hero_may_rest_outside_a_fight_and_wakes(downed):
    knock_down, send, snapshot, _last_turn = downed
    knock_down('unconscious', 'stable')

    received = send('I take a short rest.')
    health = snapshot()['playerCharacters'][0]['health']

    assert _refusals(received) == []
    assert health['currentHp'] >= 1 and health['conditions'] == []


def test_a_dying_hero_cannot_rest(downed):
    knock_down, send, _snapshot, _last_turn = downed
    knock_down('unconscious', 'dying')

    assert _refusals(send('I take a short rest.')) == [REFUSAL]
