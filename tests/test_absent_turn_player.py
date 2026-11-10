from __future__ import annotations

import pytest

import aidm_server.socket_runtime as socket_runtime_module
import aidm_server.turn_control as turn_control_module
from aidm_server.config import load_config
from aidm_server.database import db
from aidm_server.models import DmTurn, Player, Session, SessionLogEntry, safe_json_loads
from aidm_server.rules import RuleHint
from aidm_server.socket_runtime import SocketRuntime
from aidm_server.socket_state import SocketState
from aidm_server.turn_control import set_session_turn_control
from tests.helpers import seed_world_campaign_player_session


def _payloads(received, name):
    return [event['args'][0] for event in received if event['name'] == name]


@pytest.fixture()
def table(app, socketio, app_runtime, monkeypatch):
    """Two joined players; Seraphina (the seeded player) holds a structured turn."""

    clock = {'now': 1_000.0}
    monkeypatch.setattr(socket_runtime_module, 'epoch_seconds', lambda: clock['now'])
    monkeypatch.setattr(turn_control_module, '_epoch_seconds', lambda: clock['now'])
    monkeypatch.setattr(
        app_runtime['modules']['socketio_events'],
        'query_dm_function_stream',
        lambda *args, **kwargs: iter(['Borin scouts ahead while the trail stays quiet.']),
    )
    monkeypatch.setattr(
        app_runtime['modules']['turn_engine'],
        'classify_player_action',
        lambda _message: RuleHint(False, None, None, 'No check needed.', 0.9),
    )
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        second = Player(
            campaign_id=ids['campaign_id'],
            name='Bob',
            character_name='Borin',
            race='Dwarf',
            class_='Fighter',
            level=1,
        )
        db.session.add(second)
        db.session.flush()
        second_id = second.player_id
        set_session_turn_control(
            db.session.get(Session, ids['session_id']),
            mode='structured',
            active_player_id=ids['player_id'],
            participant_player_ids=[ids['player_id'], second_id],
            updated_by_player_id=ids['player_id'],
        )
        db.session.commit()
    holder = socketio.test_client(app, flask_test_client=app.test_client())
    submitter = socketio.test_client(app, flask_test_client=app.test_client())
    holder.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    submitter.emit('join_session', {'session_id': ids['session_id'], 'player_id': second_id})
    holder.get_received()
    submitter.get_received()
    return {'ids': ids, 'second_id': second_id, 'holder': holder, 'submitter': submitter, 'clock': clock}


def _submit(table, client_message_id):
    ids = table['ids']
    table['submitter'].emit(
        'send_message',
        {
            **ids,
            'player_id': table['second_id'],
            'message': 'I look around the trail.',
            'client_message_id': client_message_id,
        },
    )
    return table['submitter'].get_received()


def test_connected_holder_rejects_without_queueing(app, table):
    received = _submit(table, 'absent-connected')

    errors = _payloads(received, 'error')
    assert errors[0]['error_code'] == 'turn_out_of_order'
    assert errors[0]['error'] == 'Seraphina has the turn. Your action was not sent; your draft is kept.'
    assert 'active_player_absent' not in errors[0]['details']
    assert 'queued' not in errors[0]['error']
    with app.app_context():
        assert DmTurn.query.filter_by(client_message_id='absent-connected').count() == 0


def test_absent_holder_within_grace_reports_skip_countdown(app, table):
    table['holder'].disconnect()
    table['clock']['now'] += 30
    received = _submit(table, 'absent-within-grace')

    errors = _payloads(received, 'error')
    assert errors[0]['error_code'] == 'turn_out_of_order'
    assert errors[0]['error'] == (
        'Seraphina has the turn but is away. Their turn is skipped in 30s if they do not return. '
        'Your action was not sent; your draft is kept.'
    )
    assert errors[0]['details']['active_player_absent'] is True
    assert errors[0]['details']['skip_in_seconds'] == 30
    assert 'queued' not in errors[0]['error']
    with app.app_context():
        assert DmTurn.query.filter_by(client_message_id='absent-within-grace').count() == 0


def test_absent_holder_after_grace_is_skipped_and_action_runs(app, table):
    ids = table['ids']
    table['holder'].disconnect()
    table['clock']['now'] += 60
    received = _submit(table, 'absent-after-grace')

    assert _payloads(received, 'error') == []
    turn_updates = _payloads(received, 'turn_control_updated')
    assert turn_updates[0]['turn_control']['mode'] == 'structured'
    assert turn_updates[0]['turn_control']['activePlayerId'] == table['second_id']
    with app.app_context():
        turn = DmTurn.query.filter_by(client_message_id='absent-after-grace').one()
        assert turn.player_id == table['second_id']
        assert turn.status == 'completed'
        skip_entries = SessionLogEntry.query.filter_by(
            session_id=ids['session_id'],
            message='Seraphina is away; their turn was skipped.',
        ).all()
        assert [entry.entry_type for entry in skip_entries] == ['system']
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
        assert snapshot['turnControl']['activePlayerId'] != ids['player_id']


def test_rejoined_holder_is_connected_again(app, socketio, table):
    ids = table['ids']
    table['holder'].disconnect()
    table['clock']['now'] += 600
    returning = socketio.test_client(app, flask_test_client=app.test_client())
    returning.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})

    received = _submit(table, 'absent-rejoined')

    errors = _payloads(received, 'error')
    assert errors[0]['error'] == 'Seraphina has the turn. Your action was not sent; your draft is kept.'


def test_departed_structured_player_no_longer_blocks_the_table(app, table):
    """Inverted review reproduction (c): nothing is queued and the table moves on."""

    table['holder'].disconnect()
    blocked = _payloads(_submit(table, 'review-departed-player'), 'error')
    table['clock']['now'] += 61
    _submit(table, 'review-departed-player')

    assert blocked[0]['error_code'] == 'turn_out_of_order'
    assert 'queued' not in blocked[0]['error']
    with app.app_context():
        assert DmTurn.query.filter_by(client_message_id='review-departed-player').count() == 1


def test_spotlight_rejection_does_not_claim_queueing(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        set_session_turn_control(
            session_obj,
            mode='spotlight',
            active_player_id=ids['player_id'],
            participant_player_ids=[ids['player_id']],
            updated_by_player_id=ids['player_id'],
        )
        allowed, reason, _control = turn_control_module.turn_submission_result(
            session_obj,
            player_id=ids['player_id'] + 1,
            action_intent=None,
        )

    assert allowed is False
    assert reason == 'Seraphina has the spotlight. Your action was not sent; your draft is kept.'


def test_socket_runtime_records_departure_once_last_socket_leaves(monkeypatch):
    clock = {'now': 50.0}
    monkeypatch.setattr(socket_runtime_module, 'epoch_seconds', lambda: clock['now'])
    state = SocketState()
    runtime = SocketRuntime(state)
    player = {'id': 7, 'character_name': 'Ember', 'name': 'Danny'}
    for sid in ('sid-a', 'sid-b'):
        state.set_connection(sid, {'authorized': True, 'session_id': 3, 'player_id': 7})
        state.track_active_player(3, player, sid)

    runtime.release_disconnect('sid-a', emit_fn=lambda *args, **kwargs: None)
    clock['now'] = 80.0
    assert runtime.player_absent_since(3, 9) == 80.0
    clock['now'] = 90.0
    runtime.release_disconnect('sid-b', emit_fn=lambda *args, **kwargs: None)
    clock['now'] = 200.0

    assert runtime.player_absent_since(3, 7) == 90.0
    assert runtime.player_absent_since(3, 9) == 80.0
    runtime.clear_departure(3, 7)
    assert runtime.player_absent_since(3, 7) == 200.0


def test_absent_turn_grace_setting_has_a_floor(monkeypatch):
    monkeypatch.delenv('AIDM_ABSENT_TURN_GRACE_SECONDS', raising=False)
    assert load_config().absent_turn_grace_seconds == 60
    monkeypatch.setenv('AIDM_ABSENT_TURN_GRACE_SECONDS', '3')
    assert load_config().absent_turn_grace_seconds == 10
    monkeypatch.setenv('AIDM_ABSENT_TURN_GRACE_SECONDS', '45')
    assert load_config().absent_turn_grace_seconds == 45
