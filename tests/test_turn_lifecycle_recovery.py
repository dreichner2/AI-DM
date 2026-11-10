from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import text

from aidm_server.config import load_config
from aidm_server.contracts import ProviderResponse
from aidm_server.database import db
import aidm_server.game_state.extraction.post_dm_outcome_extractor as post_extractor_module
import aidm_server.game_state.extraction.pre_dm_action_extractor as pre_extractor_module
import aidm_server.game_state.orchestration.turn_pipeline as turn_pipeline_module
from aidm_server.models import (
    CanonJob,
    DmTurn,
    Player,
    Session,
    SessionLogEntry,
    TurnEvent,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.rules import RuleHint
from aidm_server.services.session_recovery import active_turn_recovery_gate
from aidm_server.services.session_state_mutation import campaign_turn_lock_session_ids
import aidm_server.turn_control as turn_control_module
from aidm_server.turn_control import set_session_turn_control
from aidm_server.turn_coordinator import session_turn_coordinator
from tests.helpers import seed_world_campaign_player_session


class SimulatedProcessStop(BaseException):
    """Stands in for the process dying at an exact point of a turn."""


class RecordingHelper:
    def __init__(self, name: str, calls: list[str], text: str):
        self.name = name
        self.calls = calls
        self.text = text

    def generate(self, _request):
        self.calls.append(self.name)
        return ProviderResponse(text=self.text, provider='fake', model=f'fake-{self.name}')


def _names(received):
    return [event['name'] for event in received]


def _payloads(received, name):
    return [event['args'][0] for event in received if event['name'] == name]


def _metadata(turn):
    return safe_json_loads(turn.metadata_json, {})


def _lifecycle(turn):
    return _metadata(turn).get('lifecycle') or {}


def _gold(player_id):
    return safe_json_loads(db.session.get(Player, player_id).stats, {}).get('gold')


def _no_roll(monkeypatch, app_runtime):
    monkeypatch.setattr(
        app_runtime['modules']['turn_engine'],
        'classify_player_action',
        lambda _message: RuleHint(False, None, None, 'No check needed.', 0.9),
    )


def _gold_reward(player_id, *, when=None):
    def fake_extract(**kwargs):
        if when is not None and when not in kwargs['dm_response']:
            return {'proposedChanges': [], 'uncertainChanges': [], 'notes': []}
        return {
            'proposedChanges': [
                {
                    'id': f"gold-{kwargs['turn_id']}",
                    'type': 'currency.add',
                    'actorId': f'player_{player_id}',
                    'currency': 'gp',
                    'amount': 10,
                    'visible': True,
                }
            ],
            'uncertainChanges': [],
            'notes': [],
        }

    return fake_extract


def _counting_stream(calls, text_for=lambda _input: 'The sentry thanks you and hands you 10 gold.'):
    def fake_stream(user_input, *args, **kwargs):
        calls.append(user_input)
        yield text_for(user_input)

    return fake_stream


def _seed_gold(app, player_id, stats=None):
    with app.app_context():
        db.session.get(Player, player_id).stats = safe_json_dumps(stats or {'gold': 100}, {})
        db.session.commit()


def _seed_second_player(app, campaign_id):
    with app.app_context():
        player = Player(
            campaign_id=campaign_id,
            name='Bob',
            character_name='Borin',
            race='Dwarf',
            class_='Fighter',
            level=3,
        )
        db.session.add(player)
        db.session.commit()
        return player.player_id


def _joined_client(socketio, app, session_id, player_id):
    client = socketio.test_client(app, flask_test_client=app.test_client())
    client.emit('join_session', {'session_id': session_id, 'player_id': player_id})
    client.get_received()
    return client


def _stop_once(module, name, calls):
    original = getattr(module, name)

    def stop(*args, **kwargs):
        if not calls:
            calls.append('stopped')
            raise SimulatedProcessStop()
        calls.append('resumed')
        return original(*args, **kwargs)

    return stop


@pytest.mark.parametrize(
    ('target', 'phase_after_stop', 'gold_after_stop', 'jobs_after_stop'),
    [
        ('post_dm_pipeline', 'narration_saved', 100, 0),
        ('update_campaign_pack_progress', 'narration_saved', 100, 0),
        ('enqueue_canon_job', 'effects_applied', 110, 0),
        ('process_canon_job', 'finished', 110, 1),
    ],
)
def test_process_stop_after_narration_save_finishes_turn_exactly_once(
    app, socketio, app_runtime, monkeypatch, target, phase_after_stop, gold_after_stop, jobs_after_stop
):
    turn_engine_module = app_runtime['modules']['turn_engine']
    stream_calls: list[str] = []
    stop_calls: list[str] = []
    monkeypatch.setattr(app_runtime['modules']['socketio_events'], 'query_dm_function_stream', _counting_stream(stream_calls))
    _no_roll(monkeypatch, app_runtime)
    ids = seed_world_campaign_player_session(app)
    _seed_gold(app, ids['player_id'])
    monkeypatch.setattr(turn_pipeline_module, 'extract_post_dm_outcomes', _gold_reward(ids['player_id']))
    monkeypatch.setattr(turn_engine_module, target, _stop_once(turn_engine_module, target, stop_calls))
    client = _joined_client(socketio, app, ids['session_id'], ids['player_id'])
    payload = {**ids, 'message': 'I thank the sentry.', 'client_message_id': f'stop-{target}'}

    with pytest.raises(SimulatedProcessStop):
        client.emit('send_message', payload)
    with app.app_context():
        turn = DmTurn.query.one()
        assert turn.status == 'completed'
        assert _lifecycle(turn)['phase'] == phase_after_stop
        assert _gold(ids['player_id']) == gold_after_stop
        assert CanonJob.query.count() == jobs_after_stop
        assert active_turn_recovery_gate(db.session.get(Session, ids['session_id'])) is None

    client.get_received()
    client.emit('send_message', payload)
    retry_received = client.get_received()
    retry_events = _names(retry_received)
    retry_statuses = [payload['status'] for payload in _payloads(retry_received, 'turn_status')]

    assert retry_events[-2:] == ['turn_duplicate', 'session_log_update']
    assert len(stream_calls) == 1
    with app.app_context():
        turn = DmTurn.query.one()
        assert _lifecycle(turn)['phase'] == 'finished'
        assert _gold(ids['player_id']) == 110
        assert CanonJob.query.filter_by(turn_id=turn.turn_id).count() == 1
        applied = _metadata(turn)['state_pipeline']['postAppliedChanges']
        assert [change['type'] for change in applied] == ['currency.add']
    if phase_after_stop == 'finished':
        assert 'resuming' not in retry_statuses
    else:
        assert retry_statuses[0] == 'resuming'
        assert retry_statuses[-1] == 'saved'
    if target in ('post_dm_pipeline', 'update_campaign_pack_progress'):
        assert stop_calls == ['stopped', 'resumed']


def test_new_submission_resumes_turn_abandoned_after_pre_dm_mechanics(app, socketio, app_runtime, monkeypatch):
    socketio_module = app_runtime['modules']['socketio_events']
    stream_calls: list[str] = []
    narrate_calls: list[str] = []
    original_narrate = socketio_module.TurnEngine._narrate_turn

    def stop_before_first_narration(self, request):
        narrate_calls.append(request.turn_id)
        if len(narrate_calls) == 1:
            raise SimulatedProcessStop()
        return original_narrate(self, request)

    def narration_for(user_input):
        if 'Second Wind' in user_input:
            return 'You catch your breath as the wound closes.'
        return 'Borin keeps watch over the camp.'

    monkeypatch.setattr(socketio_module, 'query_dm_function_stream', _counting_stream(stream_calls, narration_for))
    monkeypatch.setattr(socketio_module.TurnEngine, '_narrate_turn', stop_before_first_narration)
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        fighter = db.session.get(Player, ids['player_id'])
        fighter.class_ = 'Fighter'
        fighter.level = 3
        fighter.stats = safe_json_dumps(
            {'gold': 100, 'strength': 16, 'current_hp': 8, 'hp_current': 8, 'max_hp': 20, 'hp_max': 20},
            {},
        )
        db.session.commit()
    monkeypatch.setattr(
        turn_pipeline_module,
        'extract_post_dm_outcomes',
        _gold_reward(ids['player_id'], when='catch your breath'),
    )
    second_id = _seed_second_player(app, ids['campaign_id'])
    fighter_client = _joined_client(socketio, app, ids['session_id'], ids['player_id'])
    other_client = _joined_client(socketio, app, ids['session_id'], second_id)
    fighter_client.get_received()

    with pytest.raises(SimulatedProcessStop):
        fighter_client.emit(
            'send_message',
            {
                **ids,
                'message': 'I use Second Wind.',
                'client_message_id': 'abandoned-second-wind',
                'action_intent': {
                    'kind': 'capability',
                    'source': 'scene_panel',
                    'capability': {'id': 'second_wind', 'target_id': f"player_{ids['player_id']}"},
                },
            },
        )
    with app.app_context():
        abandoned = DmTurn.query.filter_by(client_message_id='abandoned-second-wind').one()
        abandoned_id = abandoned.turn_id
        assert abandoned.status == 'processing'
        assert _lifecycle(abandoned)['phase'] == 'rules_applied'
        pre_applied = _metadata(abandoned)['state_pipeline']['immediateAppliedChanges']
        assert [change['type'] for change in pre_applied] == ['class_feature.use']
        uses_after_pre_dm = safe_json_loads(db.session.get(Player, ids['player_id']).stats, {})[
            'class_feature_state'
        ]['second_wind']['current']

    other_client.get_received()
    other_client.emit(
        'send_message',
        {**ids, 'player_id': second_id, 'message': 'I keep watch.', 'client_message_id': 'after-abandoned'},
    )
    received = other_client.get_received()

    assert len(stream_calls) == 2
    with app.app_context():
        resumed = db.session.get(DmTurn, abandoned_id)
        follow_up = DmTurn.query.filter_by(client_message_id='after-abandoned').one()
        # The abandoned narration streams to the room before the new message is handled.
        assert [payload['turn_id'] for payload in _payloads(received, 'dm_response_end')] == [
            abandoned_id,
            follow_up.turn_id,
        ]
        assert resumed.status == 'completed'
        assert _lifecycle(resumed)['phase'] == 'finished'
        assert follow_up.status == 'completed'
        assert follow_up.turn_id > abandoned_id
        stats = safe_json_loads(db.session.get(Player, ids['player_id']).stats, {})
        assert stats['gold'] == 110
        assert stats['class_feature_state']['second_wind']['current'] == uses_after_pre_dm
        assert [change['type'] for change in _metadata(resumed)['state_pipeline']['immediateAppliedChanges']] == [
            'class_feature.use'
        ]
        assert TurnEvent.query.filter_by(turn_id=abandoned_id, event_type='dm_response').count() == 1


def test_caught_failure_at_dm_response_save_is_resumed_by_the_retry(app, socketio, app_runtime, monkeypatch):
    turn_engine_module = app_runtime['modules']['turn_engine']
    original_commit = turn_engine_module.commit_with_retry
    failed = []
    post_calls = []
    original_post = turn_engine_module.post_dm_pipeline

    def failing_commit(*args, **kwargs):
        if kwargs.get('label') == 'DM response save' and not failed:
            failed.append(True)
            raise RuntimeError('simulated DM save failure')
        return original_commit(*args, **kwargs)

    def counted_post(*args, **kwargs):
        post_calls.append(True)
        return original_post(*args, **kwargs)

    stream_calls: list[str] = []
    monkeypatch.setattr(turn_engine_module, 'commit_with_retry', failing_commit)
    monkeypatch.setattr(turn_engine_module, 'post_dm_pipeline', counted_post)
    monkeypatch.setattr(app_runtime['modules']['socketio_events'], 'query_dm_function_stream', _counting_stream(stream_calls))
    _no_roll(monkeypatch, app_runtime)
    ids = seed_world_campaign_player_session(app)
    _seed_gold(app, ids['player_id'])
    monkeypatch.setattr(turn_pipeline_module, 'extract_post_dm_outcomes', _gold_reward(ids['player_id']))
    client = _joined_client(socketio, app, ids['session_id'], ids['player_id'])
    payload = {**ids, 'message': 'I nod back.', 'client_message_id': 'dm-save-fail'}

    client.emit('send_message', payload)
    first_errors = [error['error_code'] for error in _payloads(client.get_received(), 'error')]
    with app.app_context():
        turn = DmTurn.query.one()
        assert turn.status == 'completed'
        assert turn.dm_output == 'The sentry thanks you and hands you 10 gold.'
        assert _lifecycle(turn)['phase'] == 'narration_saved'
        assert _metadata(turn)['post_turn_error']
        assert TurnEvent.query.filter_by(turn_id=turn.turn_id, event_type='dm_response').count() == 1
        assert _gold(ids['player_id']) == 100

    client.emit('send_message', payload)
    retry_events = _names(client.get_received())

    assert first_errors == ['turn_persist_failed']
    assert post_calls == [True]
    assert len(stream_calls) == 1
    assert retry_events[0] != 'turn_duplicate'
    assert retry_events[-2:] == ['turn_duplicate', 'session_log_update']
    with app.app_context():
        turn = DmTurn.query.one()
        assert _lifecycle(turn)['phase'] == 'finished'
        assert _gold(ids['player_id']) == 110
        assert CanonJob.query.filter_by(turn_id=turn.turn_id).count() == 1


def test_two_campaign_sessions_sharing_a_character_keep_both_changes(app, socketio, app_runtime, monkeypatch):
    """Inverted review reproduction (b)."""

    turn_engine_module = app_runtime['modules']['turn_engine']
    ids = seed_world_campaign_player_session(app)
    _seed_gold(app, ids['player_id'])
    with app.app_context():
        other_session = Session(campaign_id=ids['campaign_id'], status='active')
        db.session.add(other_session)
        db.session.commit()
        other_id = other_session.session_id
        assert [session.status for session in Session.query.order_by(Session.session_id).all()] == ['active', 'active']
    entered, resumed, b_committed = threading.Event(), threading.Event(), threading.Event()
    original_commit = turn_engine_module.commit_with_retry

    def signal_commit(*args, **kwargs):
        result = original_commit(*args, **kwargs)
        if kwargs.get('label') == 'post-DM state pipeline' and threading.current_thread().name == 'session-b':
            b_committed.set()
        return result

    def fake_stream(user_input, *args, **kwargs):
        if user_input == 'I wait for the signal.':
            entered.set()
            if not resumed.wait(8):
                raise RuntimeError('review barrier timeout')
            yield 'The signal appears.'
        else:
            yield 'You receive ten gold.'

    def fake_extract(**kwargs):
        if kwargs['dm_response'] == 'The signal appears.':
            changes = [{'id': f"signal-{kwargs['turn_id']}", 'type': 'flag.set', 'flagKey': 'signal_seen', 'flagValue': True, 'visible': True}]
        else:
            changes = [{'id': f"gold-{kwargs['turn_id']}", 'type': 'currency.add', 'actorId': f"player_{ids['player_id']}", 'currency': 'gp', 'amount': 10, 'visible': True}]
        return {'proposedChanges': changes, 'uncertainChanges': [], 'notes': []}

    monkeypatch.setattr(turn_engine_module, 'commit_with_retry', signal_commit)
    monkeypatch.setattr(app_runtime['modules']['socketio_events'], 'query_dm_function_stream', fake_stream)
    _no_roll(monkeypatch, app_runtime)
    monkeypatch.setattr(turn_pipeline_module, 'extract_post_dm_outcomes', fake_extract)
    clients = [_joined_client(socketio, app, session_id, ids['player_id']) for session_id in (ids['session_id'], other_id)]
    errors = []

    def send(client, payload):
        try:
            client.emit('send_message', payload)
        except BaseException as exc:
            errors.append(repr(exc))

    thread_a = threading.Thread(
        target=send,
        args=(clients[0], {**ids, 'message': 'I wait for the signal.', 'client_message_id': 'review-A'}),
    )
    thread_b = threading.Thread(
        target=send,
        args=(clients[1], {**ids, 'session_id': other_id, 'message': 'I accept the payment.', 'client_message_id': 'review-B'}),
        name='session-b',
    )
    thread_a.start()
    assert entered.wait(8)
    thread_b.start()
    try:
        # B waits for A's campaign-wide turn lock instead of racing it.
        assert not b_committed.wait(0.5)
    finally:
        resumed.set()
        thread_a.join(8)
        thread_b.join(8)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert errors == []
    assert b_committed.is_set()
    with app.app_context():
        assert _gold(ids['player_id']) == 110
        snapshot_a = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
        snapshot_b = safe_json_loads(db.session.get(Session, other_id).state_snapshot, {})
        assert snapshot_a['flags']['signal_seen'] is True
        assert snapshot_b['playerCharacters'][0]['inventory']['currency']['gp'] == 110
        assert all(turn.status == 'completed' for turn in DmTurn.query.all())


def test_character_edit_during_narration_survives_post_dm_write(app, socketio, app_runtime, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    _seed_gold(app, ids['player_id'])

    def editing_stream(*args, **kwargs):
        with db.engine.begin() as connection:
            connection.execute(
                text('UPDATE players SET stats = :stats WHERE player_id = :player_id'),
                {'stats': safe_json_dumps({'gold': 100, 'silver': 7}, {}), 'player_id': ids['player_id']},
            )
        yield 'The sentry hands you 10 gold.'

    monkeypatch.setattr(app_runtime['modules']['socketio_events'], 'query_dm_function_stream', editing_stream)
    _no_roll(monkeypatch, app_runtime)
    monkeypatch.setattr(turn_pipeline_module, 'extract_post_dm_outcomes', _gold_reward(ids['player_id']))
    client = _joined_client(socketio, app, ids['session_id'], ids['player_id'])

    client.emit('send_message', {**ids, 'message': 'I thank the sentry.', 'client_message_id': 'edit-in-flight'})

    with app.app_context():
        stats = safe_json_loads(db.session.get(Player, ids['player_id']).stats, {})
        assert stats['gold'] == 110
        assert stats['silver'] == 7


def test_post_dm_helper_result_survives_a_character_only_edit(app, socketio, app_runtime, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    _seed_gold(app, ids['player_id'])

    class EditingPostHelper:
        def generate(self, _request):
            with db.engine.begin() as connection:
                connection.execute(
                    text('UPDATE players SET stats = :stats WHERE player_id = :player_id'),
                    {'stats': safe_json_dumps({'gold': 100, 'silver': 7}, {}), 'player_id': ids['player_id']},
                )
            return ProviderResponse(
                text=(
                    '{"proposedChanges":[{"id":"helper-gold","type":"currency.add","actorId":"player_%d",'
                    '"currency":"gp","amount":10,"visible":true}],"uncertainChanges":[]}' % ids['player_id']
                ),
                provider='fake',
                model='fake-post',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: EditingPostHelper())
    monkeypatch.setattr(
        app_runtime['modules']['socketio_events'],
        'query_dm_function_stream',
        lambda *args, **kwargs: iter(['The sentry hands you 10 gold.']),
    )
    _no_roll(monkeypatch, app_runtime)
    app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
    client = _joined_client(socketio, app, ids['session_id'], ids['player_id'])

    client.emit('send_message', {**ids, 'message': 'I thank the sentry.', 'client_message_id': 'helper-edit'})

    with app.app_context():
        turn = DmTurn.query.one()
        assert 'post_dm_state' not in _metadata(turn)
        stats = safe_json_loads(db.session.get(Player, ids['player_id']).stats, {})
        assert stats['gold'] == 110
        assert stats['silver'] == 7


def test_structured_advance_is_not_repeated_on_resume(app, socketio, app_runtime, monkeypatch):
    turn_engine_module = app_runtime['modules']['turn_engine']
    original_advance = turn_engine_module.advance_structured_turn
    advances = []
    stop_calls: list[str] = []

    def counted_advance(*args, **kwargs):
        advances.append(True)
        return original_advance(*args, **kwargs)

    monkeypatch.setattr(turn_engine_module, 'advance_structured_turn', counted_advance)
    monkeypatch.setattr(turn_engine_module, 'enqueue_canon_job', _stop_once(turn_engine_module, 'enqueue_canon_job', stop_calls))
    monkeypatch.setattr(
        app_runtime['modules']['socketio_events'],
        'query_dm_function_stream',
        lambda *args, **kwargs: iter(['The party holds its breath.']),
    )
    _no_roll(monkeypatch, app_runtime)
    ids = seed_world_campaign_player_session(app)
    second_id = _seed_second_player(app, ids['campaign_id'])
    with app.app_context():
        set_session_turn_control(
            db.session.get(Session, ids['session_id']),
            mode='structured',
            active_player_id=ids['player_id'],
            participant_player_ids=[ids['player_id'], second_id],
            updated_by_player_id=ids['player_id'],
        )
        db.session.commit()
    client = _joined_client(socketio, app, ids['session_id'], ids['player_id'])
    _joined_client(socketio, app, ids['session_id'], second_id)
    payload = {**ids, 'message': 'I hold my ground.', 'client_message_id': 'structured-stop'}

    with pytest.raises(SimulatedProcessStop):
        client.emit('send_message', payload)
    with app.app_context():
        turn = DmTurn.query.one()
        assert _lifecycle(turn)['phase'] == 'effects_applied'
        assert _lifecycle(turn)['structuredAdvanceDone'] is True
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
        assert snapshot['turnControl']['activePlayerId'] == second_id

    client.emit('send_message', payload)

    assert advances == [True]
    with app.app_context():
        turn = DmTurn.query.one()
        assert _lifecycle(turn)['phase'] == 'finished'
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
        assert snapshot['turnControl']['activePlayerId'] == second_id


def test_failed_resumes_are_counted_then_gated(app, socketio, app_runtime, monkeypatch):
    socketio_module = app_runtime['modules']['socketio_events']
    turn_engine_module = app_runtime['modules']['turn_engine']
    stop_calls: list[str] = []
    monkeypatch.setattr(turn_engine_module, 'post_dm_pipeline', _stop_once(turn_engine_module, 'post_dm_pipeline', stop_calls))
    monkeypatch.setattr(
        socketio_module,
        'query_dm_function_stream',
        lambda *args, **kwargs: iter(['The lantern gutters.']),
    )
    _no_roll(monkeypatch, app_runtime)
    ids = seed_world_campaign_player_session(app)
    second_id = _seed_second_player(app, ids['campaign_id'])
    stopped_client = _joined_client(socketio, app, ids['session_id'], ids['player_id'])
    other_client = _joined_client(socketio, app, ids['session_id'], second_id)
    with pytest.raises(SimulatedProcessStop):
        stopped_client.emit('send_message', {**ids, 'message': 'I trim the lantern.', 'client_message_id': 'stuck'})
    with app.app_context():
        stuck_id = DmTurn.query.filter_by(client_message_id='stuck').one().turn_id

    original_complete = socketio_module.TurnEngine._complete_saved_turn

    def failing_complete(self, outcome, **kwargs):
        if outcome.turn_id == stuck_id:
            raise RuntimeError('resume keeps failing')
        return original_complete(self, outcome, **kwargs)

    monkeypatch.setattr(socketio_module.TurnEngine, '_complete_saved_turn', failing_complete)
    outcomes = []
    for attempt in range(1, 4):
        other_client.get_received()
        other_client.emit(
            'send_message',
            {**ids, 'player_id': second_id, 'message': f'I wait, take {attempt}.', 'client_message_id': f'wait-{attempt}'},
        )
        errors = [error['error_code'] for error in _payloads(other_client.get_received(), 'error')]
        with app.app_context():
            stuck = db.session.get(DmTurn, stuck_id)
            outcomes.append(
                (
                    _lifecycle(stuck).get('resumeAttempts'),
                    stuck.status,
                    DmTurn.query.filter_by(client_message_id=f'wait-{attempt}').count(),
                    'session_recovery_required' in errors,
                )
            )

    assert outcomes == [
        (1, 'completed', 1, False),
        (2, 'completed', 1, False),
        (3, 'failed', 0, True),
    ]
    with app.app_context():
        stuck = db.session.get(DmTurn, stuck_id)
        assert _lifecycle(stuck)['phase'] == 'finished'
        assert active_turn_recovery_gate(db.session.get(Session, ids['session_id']))['turnId'] == stuck_id


def test_finished_turns_drop_state_snapshots_and_others_keep_them(app, socketio, app_runtime, monkeypatch):
    turn_engine_module = app_runtime['modules']['turn_engine']
    stop_calls: list[str] = []

    def narration(user_input, *args, **kwargs):
        if 'stumble' in user_input:
            raise RuntimeError('provider unavailable')
        yield 'The road bends north.'

    monkeypatch.setattr(app_runtime['modules']['socketio_events'], 'query_dm_function_stream', narration)
    _no_roll(monkeypatch, app_runtime)
    ids = seed_world_campaign_player_session(app)
    client = _joined_client(socketio, app, ids['session_id'], ids['player_id'])
    client.emit('send_message', {**ids, 'message': 'I walk on.', 'client_message_id': 'prune-done'})
    client.emit('send_message', {**ids, 'message': 'I stumble.', 'client_message_id': 'prune-failed'})
    monkeypatch.setattr(turn_engine_module, 'post_dm_pipeline', _stop_once(turn_engine_module, 'post_dm_pipeline', stop_calls))
    with pytest.raises(SimulatedProcessStop):
        client.emit('send_message', {**ids, 'message': 'I rest.', 'client_message_id': 'prune-open'})

    with app.app_context():
        pipelines = {}
        for turn in DmTurn.query.all():
            pipelines[turn.client_message_id] = (turn.status, _lifecycle(turn)['phase'], _metadata(turn)['state_pipeline'])
    status, phase, pipeline = pipelines['prune-done']
    assert (status, phase, pipeline.get('snapshotsPruned')) == ('completed', 'finished', True)
    assert 'stateBeforeDm' not in pipeline and 'stateBeforePreDm' not in pipeline
    status, phase, pipeline = pipelines['prune-failed']
    assert (status, phase) == ('failed', 'finished')
    assert 'stateBeforeDm' in pipeline and 'snapshotsPruned' not in pipeline
    status, phase, pipeline = pipelines['prune-open']
    assert (status, phase) == ('completed', 'narration_saved')
    assert 'stateBeforeDm' in pipeline and 'snapshotsPruned' not in pipeline


def test_turns_without_a_lifecycle_marker_count_as_finished(app, socketio, app_runtime, monkeypatch):
    stream_calls: list[str] = []
    monkeypatch.setattr(app_runtime['modules']['socketio_events'], 'query_dm_function_stream', _counting_stream(stream_calls))
    _no_roll(monkeypatch, app_runtime)
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        db.session.add(
            DmTurn(
                session_id=ids['session_id'],
                campaign_id=ids['campaign_id'],
                player_id=ids['player_id'],
                player_input='An old in-flight action.',
                status='processing',
                client_message_id='legacy-processing',
                metadata_json=safe_json_dumps({'speaker': 'Seraphina'}, {}),
            )
        )
        db.session.commit()
    client = _joined_client(socketio, app, ids['session_id'], ids['player_id'])

    client.emit('send_message', {**ids, 'message': 'An old in-flight action.', 'client_message_id': 'legacy-processing'})
    duplicate_events = _names(client.get_received())
    client.emit('send_message', {**ids, 'message': 'I look around.', 'client_message_id': 'after-legacy'})

    assert duplicate_events[0] == 'turn_duplicate'
    assert stream_calls == ['I look around.']
    with app.app_context():
        legacy = DmTurn.query.filter_by(client_message_id='legacy-processing').one()
        assert legacy.status == 'processing'
        assert 'lifecycle' not in _metadata(legacy)


def test_joining_a_session_resumes_its_abandoned_turn_in_the_background(app, socketio, app_runtime, monkeypatch):
    turn_engine_module = app_runtime['modules']['turn_engine']
    stream_calls: list[str] = []
    stop_calls: list[str] = []
    monkeypatch.setattr(app_runtime['modules']['socketio_events'], 'query_dm_function_stream', _counting_stream(stream_calls))
    _no_roll(monkeypatch, app_runtime)
    ids = seed_world_campaign_player_session(app)
    _seed_gold(app, ids['player_id'])
    monkeypatch.setattr(turn_pipeline_module, 'extract_post_dm_outcomes', _gold_reward(ids['player_id']))
    monkeypatch.setattr(turn_engine_module, 'post_dm_pipeline', _stop_once(turn_engine_module, 'post_dm_pipeline', stop_calls))
    client = _joined_client(socketio, app, ids['session_id'], ids['player_id'])
    with pytest.raises(SimulatedProcessStop):
        client.emit('send_message', {**ids, 'message': 'I thank the sentry.', 'client_message_id': 'join-recovery'})

    _joined_client(socketio, app, ids['session_id'], ids['player_id'])

    deadline = time.monotonic() + 5
    phase = None
    while time.monotonic() < deadline:
        with app.app_context():
            phase = _lifecycle(DmTurn.query.one()).get('phase')
        if phase == 'finished':
            break
        time.sleep(0.05)
    assert phase == 'finished'
    assert stop_calls == ['stopped', 'resumed']
    assert len(stream_calls) == 1
    with app.app_context():
        assert _gold(ids['player_id']) == 110


def test_background_recovery_never_waits_for_a_held_turn_lock(app, socketio, app_runtime, monkeypatch):
    turn_engine_module = app_runtime['modules']['turn_engine']
    stop_calls: list[str] = []
    monkeypatch.setattr(app_runtime['modules']['socketio_events'], 'query_dm_function_stream', _counting_stream([]))
    _no_roll(monkeypatch, app_runtime)
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr(turn_engine_module, 'post_dm_pipeline', _stop_once(turn_engine_module, 'post_dm_pipeline', stop_calls))
    client = _joined_client(socketio, app, ids['session_id'], ids['player_id'])
    with pytest.raises(SimulatedProcessStop):
        client.emit('send_message', {**ids, 'message': 'I thank the sentry.', 'client_message_id': 'held-lock'})
    engine = app_runtime['modules']['socketio_events']._new_turn_engine(socketio)
    holding, release = threading.Event(), threading.Event()

    def hold_turn_lock():
        with app.app_context():
            with session_turn_coordinator.serialized(ids['session_id']):
                holding.set()
                release.wait(5)

    holder = threading.Thread(target=hold_turn_lock)
    holder.start()
    assert holding.wait(5)
    try:
        with app.app_context():
            started = time.monotonic()
            assert engine.recover_abandoned_turns(ids['session_id']) is False
            assert time.monotonic() - started < 1
            assert _lifecycle(DmTurn.query.one())['phase'] == 'narration_saved'
    finally:
        release.set()
        holder.join(5)
    with app.app_context():
        assert engine.recover_abandoned_turns(ids['session_id']) is True
        assert _lifecycle(DmTurn.query.one())['phase'] == 'finished'


def test_campaign_turn_lock_set_covers_live_sessions_of_the_campaign(app):
    ids = seed_world_campaign_player_session(app)
    other_campaign_session = seed_world_campaign_player_session(app)['session_id']
    with app.app_context():
        live_null = Session(campaign_id=ids['campaign_id'], status=None)
        ended = Session(campaign_id=ids['campaign_id'], status='ended')
        archived = Session(campaign_id=ids['campaign_id'], status='archived')
        db.session.add_all([live_null, ended, archived])
        db.session.commit()
        live_null_id = live_null.session_id
        db.session.execute(text('UPDATE sessions SET status = NULL WHERE session_id = :id'), {'id': live_null_id})
        db.session.commit()

        lock_ids = campaign_turn_lock_session_ids(ids['session_id'])

    assert lock_ids == sorted([ids['session_id'], live_null_id])
    assert other_campaign_session not in lock_ids


def test_turn_holds_every_live_campaign_session_lock(app, app_runtime, monkeypatch):
    from unittest.mock import Mock

    turn_engine_module = app_runtime['modules']['turn_engine']
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        sibling = Session(campaign_id=ids['campaign_id'])
        db.session.add(sibling)
        db.session.commit()
        sibling_id = sibling.session_id
        engine = turn_engine_module.TurnEngine(socketio=Mock(), emit_fn=Mock(), stream_fn=Mock())
        observed = []
        monkeypatch.setattr(engine, '_process_serialized', lambda _command: observed.append(session_turn_coordinator.held_session_ids()))
        engine.process(
            turn_engine_module.TurnCommand(
                sid='socket-1',
                session_id=ids['session_id'],
                campaign_id=ids['campaign_id'],
                world_id=ids['world_id'],
                player_id=ids['player_id'],
                user_input='I look around.',
                manual_segment_ids=set(),
            )
        )

    assert observed == [frozenset({ids['session_id'], sibling_id})]


def test_opening_turn_is_marked_cheap_and_never_rolled(app, socketio, app_runtime, monkeypatch):
    helper_calls: list[str] = []
    narration_hints = []

    def opening_stream(user_input, context, speaking_player=None, rules_hint=None):
        narration_hints.append(rules_hint)
        yield 'Rain hammers the road ahead. Roll a d20 for perception.'

    monkeypatch.setattr(turn_control_module, 'get_helper_provider', lambda: RecordingHelper('conductor', helper_calls, '{"decision":"allow"}'))
    monkeypatch.setattr(pre_extractor_module, 'get_helper_provider', lambda: RecordingHelper('pre', helper_calls, '{"declaredActions":[]}'))
    monkeypatch.setattr(
        post_extractor_module,
        'get_helper_provider',
        lambda: RecordingHelper('post', helper_calls, '{"proposedChanges":[],"uncertainChanges":[]}'),
    )
    monkeypatch.setattr(app_runtime['modules']['socketio_events'], 'query_dm_function_stream', opening_stream)
    app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
    app.config['AIDM_TURN_CONDUCTOR_HELPER_IN_TESTS'] = True
    ids = seed_world_campaign_player_session(app)
    second_id = _seed_second_player(app, ids['campaign_id'])
    client = _joined_client(socketio, app, ids['session_id'], ids['player_id'])
    _joined_client(socketio, app, ids['session_id'], second_id)

    client.emit(
        'send_message',
        {
            **ids,
            'message': 'Please narrate the opening scene for this campaign. I look around and take my bearings.',
            'client_message_id': 'opening-1',
            'message_kind': 'opening',
        },
    )
    received = client.get_received()

    assert 'conductor' not in helper_calls
    assert 'pre' not in helper_calls
    assert _payloads(received, 'roll_required') == []
    assert narration_hints[0]['message_kind'] == 'opening'
    assert narration_hints[0]['requires_roll'] is False
    with app.app_context():
        turn = DmTurn.query.one()
        metadata = _metadata(turn)
        assert metadata['message_kind'] == 'opening'
        assert 'roll_gate' not in metadata
        assert turn.requires_roll is False
        assert turn.outcome_status == 'resolved'
        assert safe_json_loads(turn.rules_hint, {})['message_kind'] == 'opening'
        player_event = TurnEvent.query.filter_by(turn_id=turn.turn_id, event_type='player_message').one()
        assert safe_json_loads(player_event.payload_json, {})['message_kind'] == 'opening'
        log_entry = SessionLogEntry.query.filter_by(session_id=ids['session_id'], entry_type='player').one()
        assert safe_json_loads(log_entry.metadata_json, {})['message_kind'] == 'opening'


def test_send_message_rejects_unknown_message_kind(app, socketio):
    ids = seed_world_campaign_player_session(app)
    client = _joined_client(socketio, app, ids['session_id'], ids['player_id'])

    client.emit('send_message', {**ids, 'message': 'Hello.', 'message_kind': 'story'})

    errors = _payloads(client.get_received(), 'error')
    assert errors[0]['error_code'] == 'validation_error'
    with app.app_context():
        assert DmTurn.query.count() == 0


def test_conductor_helper_runs_only_with_two_connected_players_and_a_real_provider(app, monkeypatch):
    calls: list[str] = []
    ids = seed_world_campaign_player_session(app)
    second_id = _seed_second_player(app, ids['campaign_id'])
    app.config['AIDM_TURN_CONDUCTOR_HELPER_IN_TESTS'] = True

    def conduct(active_player_ids):
        with app.app_context():
            session_obj = db.session.get(Session, ids['session_id'])
            return turn_control_module.conduct_turn_submission(
                session_obj,
                player_id=ids['player_id'],
                message='I wait for an opening.',
                action_intent={'kind': 'message'},
                active_player_ids=active_player_ids,
            )

    monkeypatch.setattr(
        turn_control_module,
        'get_helper_provider',
        lambda: RecordingHelper('conductor', calls, '{"decision":"allow"}'),
    )
    conduct([ids['player_id']])
    alone = list(calls)
    conduct([ids['player_id'], second_id])
    together = list(calls)
    app.config['AIDM_LLM_PROVIDER'] = 'fallback'
    conduct([ids['player_id'], second_id])

    assert alone == []
    assert together == ['conductor']
    assert calls == ['conductor']


def test_helper_switches_load_from_the_environment(monkeypatch):
    monkeypatch.delenv('AIDM_TURN_CONDUCTOR_HELPER_ENABLED', raising=False)
    monkeypatch.delenv('AIDM_STATE_PIPELINE_HELPER_ENABLED', raising=False)
    defaults = load_config()
    monkeypatch.setenv('AIDM_TURN_CONDUCTOR_HELPER_ENABLED', 'false')
    monkeypatch.setenv('AIDM_STATE_PIPELINE_HELPER_ENABLED', '0')
    disabled = load_config()

    assert (defaults.turn_conductor_helper_enabled, defaults.state_pipeline_helper_enabled) == (True, True)
    assert (disabled.turn_conductor_helper_enabled, disabled.state_pipeline_helper_enabled) == (False, False)
