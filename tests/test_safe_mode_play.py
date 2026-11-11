"""Socket-level Safe Mode play on the Play Now adventure: scripted narration, no network models."""

from __future__ import annotations

import re

import pytest
import requests

from aidm_server.game_state.orchestration.story_beats import HINT_TEXT
from aidm_server.llm_providers import DeterministicFallbackProvider
from aidm_server.models import DmTurn, safe_json_loads
from aidm_server.scripted_narrator import TEMPLATES, requests_roll


ROLL_REQUEST_RE = re.compile(r'Make an? (?P<label>[A-Za-z ()\']+) check, DC (?P<dc>\d+)\.$')


def _payload(received, name):
    return next((event['args'][0] for event in received if event['name'] == name and event['args']), None)


def _payloads(received, name):
    return [event['args'][0] for event in received if event['name'] == name and event['args']]


NOTHING_LINES = TEMPLATES['nothing_happens']


def _story_beats(received):
    """The beats of the turn, from its completion event."""

    completed = next(
        payload
        for payload in _payloads(received, 'turn_status')
        if payload['status'] == 'saved' and payload['details'].get('stage') == 'post_turn'
    )
    return completed['details']['storyBeats']


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
    # Words that name no action get one short line, and once per session a quiet hint.
    assert plain_text in NOTHING_LINES
    assert _story_beats(plain) == [{'kind': 'hint', 'text': HINT_TEXT}]
    assert _latest_turn(app, ids['session_id'])['roll_gate'] is None
    again = _send(socket_client, ids, 'I wait by the wagons.', client_message_id='turn-2b')
    assert _payload(again, 'dm_response_end')['text'] in NOTHING_LINES
    assert _story_beats(again) == []

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

    # The roll is shown as its card; the narration tells what came of it, with no numbers.
    assert resolved_text and not re.search(r'\d|DC', resolved_text), resolved_text
    assert 'Make a' not in resolved_text
    narrated_turns = 5
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
    # Striking an object is an ordinary action, not a fight with no one in it.
    assert not swing_text.startswith(('There is no one here to fight.', 'Your blade finds only air; no enemy stands before you.'))
    assert swing_text in NOTHING_LINES
    assert requests_roll(swing_text) is False
    assert swing_turn['outcome_status'] != 'deferred'
    assert swing_turn['roll_gate'] is None
    assert swing_turn['rules_hint']['requires_roll'] is False
    assert 'no_target' not in swing_turn['rules_hint']
    assert swing_turn['rules_hint']['reason'] == 'No opponent is present.'
    assert swing_turn['rules_hint']['roll_requirement_cleared']['original_rule_type'] == 'attack'


def _roll_intent(text: str, **extra) -> dict:
    return {
        'kind': 'roll',
        'source': 'dice_tray',
        'text': text,
        'roll': {'die': 'd20', 'mode': 'normal', 'result_visibility': 'hidden_until_landed'},
        **extra,
    }


@pytest.fixture()
def table(app, socketio, safe_mode_session, monkeypatch):
    ids, _generate_calls = safe_mode_session
    # The hero's d20 lands on 10.
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 9 if sides == 20 else 0)
    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit(
        'join_session',
        {'session_id': ids['session_id'], 'player_id': ids['player_id'], 'workspace_id': ids['workspace_id']},
    )
    socket_client.get_received()
    _send(socket_client, ids, 'Please narrate the opening scene.', message_kind='opening', client_message_id='opening-1')
    return ids, socket_client


def test_a_roll_with_no_check_pending_is_just_a_roll(app, table):
    ids, socket_client = table

    bare = _send(socket_client, ids, 'I roll a d20.', client_message_id='roll-1', action_intent=_roll_intent('I roll a d20.'))
    bare_turn = _latest_turn(app, ids['session_id'])
    strength = _send(
        socket_client,
        ids,
        'I roll for Strength.',
        client_message_id='roll-2',
        action_intent=_roll_intent('I roll for Strength.', ability={'key': 'strength', 'label': 'STR'}),
    )
    strength_turn = _latest_turn(app, ids['session_id'])
    roll = strength_turn['rules_hint']['authoritative_roll']
    modifier = roll['modifier']

    # The roll's card shows the number.
    assert _payload(bare, 'dm_response_end')['text'] == 'You roll the die.'
    assert _story_beats(bare) == [{'kind': 'check', 'text': 'd20: 10'}]
    assert (bare_turn['rules_hint']['authoritative_roll']['dc'], bare_turn['rules_hint']['authoritative_roll']['outcome']) == (None, None)
    assert bare_turn['rules_hint']['dc_hint'] is None
    assert (bare_turn['roll_gate'], bare_turn['outcome_status']) == (None, 'resolved')
    assert _payloads(bare, 'roll_required') == []
    assert (roll['total'], roll['dc'], roll['outcome'], roll['free']) == (10 + modifier, None, None, True)
    sign = f'+ {modifier}' if modifier >= 0 else f'− {-modifier}'
    assert _payload(strength, 'dm_response_end')['text'] == 'You roll for Strength.'
    assert _story_beats(strength) == [{'kind': 'check', 'text': f'Strength: {10 + modifier} (d20 10 {sign})'}]


def test_a_roll_that_answers_a_pending_check_is_that_check(app, table):
    ids, socket_client = table
    sneak = _send(socket_client, ids, 'I sneak around behind the last wagon.', client_message_id='turn-2')
    stated_dc = int(ROLL_REQUEST_RE.search(_payload(sneak, 'dm_response_end')['text']).group('dc'))
    pending_turn_id = _latest_turn(app, ids['session_id'])['turn_id']

    resolved = _send(
        socket_client,
        ids,
        'I roll.',
        client_message_id='roll-1',
        action_intent=_roll_intent('I roll.', roll={'die': 'd20', 'mode': 'normal', 'result_visibility': 'hidden_until_landed', 'target_pending_turn_id': pending_turn_id}),
    )
    roll = _latest_turn(app, ids['session_id'])['rules_hint']['authoritative_roll']
    outcome = 'success' if roll['total'] >= stated_dc else 'failure'

    assert (roll['dc'], roll['outcome'], roll.get('free')) == (stated_dc, outcome, None)
    assert _story_beats(resolved) == [{'kind': 'check', 'text': f"Stealth check: {roll['total']} — {outcome} (DC {stated_dc})"}]
    # The beat keeps the numbers; the narration, beside the roll's card, tells only what came of it.
    told = _payload(resolved, 'dm_response_end')['text']
    assert told and not re.search(r'\d|DC', told)
