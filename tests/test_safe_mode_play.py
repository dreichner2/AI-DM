"""Socket-level Safe Mode play on the Play Now adventure: scripted narration, no network models."""

from __future__ import annotations

import re

import pytest
import requests

from aidm_server.llm_providers import DeterministicFallbackProvider
from aidm_server.models import DmTurn, safe_json_loads
from aidm_server.scripted_narrator import requests_roll


ROLL_REQUEST_RE = re.compile(r'Make an? (?P<label>[A-Za-z ()\']+) check, DC (?P<dc>\d+)\.$')


def _payload(received, name):
    return next((event['args'][0] for event in received if event['name'] == name and event['args']), None)


def _payloads(received, name):
    return [event['args'][0] for event in received if event['name'] == name and event['args']]


@pytest.fixture()
def safe_mode_session(app, client, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    monkeypatch.setenv('AIDM_HELPER_LLM_PROVIDER', 'deepseek')
    monkeypatch.setenv('AIDM_DEEPSEEK_API_KEY', 'deepseek-test-key')
    app.config.update(
        AIDM_LLM_PROVIDER='fallback',
        AIDM_LLM_MODEL='deterministic-v1',
        AIDM_STATE_PIPELINE_HELPER_IN_TESTS=True,
    )
    generate_calls: list[str] = []
    original_generate = DeterministicFallbackProvider.generate

    def recording_generate(self, request):
        generate_calls.append(self.model_name)
        return original_generate(self, request)

    monkeypatch.setattr(DeterministicFallbackProvider, 'generate', recording_generate)

    response = client.post('/api/onboarding/play-now', json={})
    assert response.status_code in (200, 201), response.get_data(as_text=True)
    play_now = response.get_json()
    ids = {key: play_now[key] for key in ('session_id', 'campaign_id', 'player_id', 'world_id', 'workspace_id')}
    return ids, generate_calls


def _send(socket_client, ids, message, **extra):
    socket_client.emit(
        'send_message',
        {
            'session_id': ids['session_id'],
            'campaign_id': ids['campaign_id'],
            'player_id': ids['player_id'],
            'message': message,
            **extra,
        },
    )
    return socket_client.get_received()


def _latest_turn(app, session_id):
    with app.app_context():
        turn = DmTurn.query.filter_by(session_id=session_id).order_by(DmTurn.turn_id.desc()).first()
        metadata = safe_json_loads(turn.metadata_json, {})
        return {
            'turn_id': turn.turn_id,
            'outcome_status': turn.outcome_status,
            'rule_type': turn.rule_type,
            'llm_model': turn.llm_model,
            'roll_gate': metadata.get('roll_gate'),
            'rules_hint': safe_json_loads(turn.rules_hint, {}),
            'state_log': (metadata.get('state_pipeline') or {}).get('stateLog') or {},
        }


def test_safe_mode_plays_the_play_now_opening_actions_and_rolls_without_network(app, socketio, safe_mode_session):
    ids, generate_calls = safe_mode_session
    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit(
        'join_session',
        {'session_id': ids['session_id'], 'player_id': ids['player_id'], 'workspace_id': ids['workspace_id']},
    )
    socket_client.get_received()

    opening = _send(
        socket_client,
        ids,
        'Please narrate the opening scene for this campaign. Campaign: The Road of Unremembered Kings.',
        message_kind='opening',
        client_message_id='opening-1',
    )
    opening_text = _payload(opening, 'dm_response_end')['text']
    opening_turn = _latest_turn(app, ids['session_id'])

    assert 'empty caravan' in opening_text
    assert 'Within earshot are Mara Reedwake, Tovin Bell, and Osric Hale.' in opening_text
    assert 'witness' not in opening_text
    assert opening_text.endswith('What do you do?')
    # "its horses are calm" proposes a mood-only scene update; it must not reach the player as a change line.
    assert [line for line in opening_turn['state_log'].get('lines', []) if line.get('changeType') == 'scene.update'] == []
    assert requests_roll(opening_text) is False
    assert opening_turn['roll_gate'] is None
    assert opening_turn['outcome_status'] != 'deferred'
    assert opening_turn['llm_model'] == 'deterministic-v1'
    assert all(set(chunk) == {'session_id', 'turn_id', 'chunk', 'index'} for chunk in _payloads(opening, 'dm_chunk'))

    plain = _send(socket_client, ids, 'I walk over to the wagons.', client_message_id='turn-2')
    plain_text = _payload(plain, 'dm_response_end')['text']

    assert _payloads(plain, 'roll_required') == []
    assert not [error for error in _payloads(plain, 'error') if error.get('error_code') == 'roll_required']
    assert 'I walk over to the wagons' not in plain_text
    assert plain_text.startswith(('You set to it.', 'You take your time over it.', 'Nothing stops you.', 'You do just that.'))
    assert 'Mara Reedwake' in plain_text.split('\n\n')[-1]
    assert _latest_turn(app, ids['session_id'])['roll_gate'] is None

    sneak = _send(socket_client, ids, 'I sneak around behind the last wagon.', client_message_id='turn-3')
    sneak_text = _payload(sneak, 'dm_response_end')['text']
    request_match = ROLL_REQUEST_RE.search(sneak_text)
    sneak_turn = _latest_turn(app, ids['session_id'])

    assert request_match is not None, sneak_text
    assert request_match.group('label') == 'Dexterity (Stealth)'
    stated_dc = int(request_match.group('dc'))
    assert sneak_turn['outcome_status'] == 'deferred'
    assert sneak_turn['roll_gate']['rule_type'] == 'stealth'
    assert sneak_turn['roll_gate']['dc_hint'] == f'DC {stated_dc}'
    assert sneak_turn['rules_hint']['dc_hint'] == f'DC {stated_dc}'
    assert 'Please roll' not in sneak_text

    resolved = _send(
        socket_client,
        ids,
        'I roll.',
        client_message_id='turn-4',
        action_intent={
            'kind': 'roll',
            'source': 'composer',
            'text': 'I roll.',
            'roll': {'die': 'd20', 'mode': 'normal', 'result_visibility': 'hidden_until_landed', 'reason': 'stealth'},
        },
    )
    resolved_text = _payload(resolved, 'dm_response_end')['text']

    assert re.match(rf'Dexterity \(Stealth\): -?\d+ against DC {stated_dc} — (success|failure)\.', resolved_text), resolved_text
    assert 'Make a' not in resolved_text
    narrated_turns = 4
    assert generate_calls.count('deterministic-v1') == narrated_turns
    assert set(generate_calls) <= {'deterministic-v1', 'state-helper-fallback-v1'}


def test_safe_mode_attack_with_no_opponent_asks_for_no_roll(app, socketio, safe_mode_session):
    ids, _generate_calls = safe_mode_session
    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit(
        'join_session',
        {'session_id': ids['session_id'], 'player_id': ids['player_id'], 'workspace_id': ids['workspace_id']},
    )
    socket_client.get_received()
    _send(socket_client, ids, 'Please narrate the opening scene.', message_kind='opening', client_message_id='opening-1')

    swing = _send(socket_client, ids, 'I hit the empty wagon bench with the flat of my blade.', client_message_id='turn-2')
    swing_text = _payload(swing, 'dm_response_end')['text']
    swing_turn = _latest_turn(app, ids['session_id'])

    assert _payloads(swing, 'roll_required') == []
    assert swing_text.startswith(('There is no one here to fight.', 'Your blade finds only air; no enemy stands before you.'))
    assert 'Mara Reedwake' in swing_text
    assert requests_roll(swing_text) is False
    assert swing_turn['outcome_status'] != 'deferred'
    assert swing_turn['roll_gate'] is None
    assert swing_turn['rules_hint']['requires_roll'] is False
    assert swing_turn['rules_hint']['no_target'] is True
    assert swing_turn['rules_hint']['reason'] == 'No opponent is present.'
    assert swing_turn['rules_hint']['roll_requirement_cleared']['original_rule_type'] == 'attack'
