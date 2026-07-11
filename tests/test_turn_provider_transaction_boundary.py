from __future__ import annotations

import threading
import time

from sqlalchemy import event, text

from aidm_server.contracts import ProviderResponse
from aidm_server.database import db
import aidm_server.game_state.extraction.post_dm_outcome_extractor as post_extractor_module
import aidm_server.game_state.extraction.pre_dm_action_extractor as pre_extractor_module
import aidm_server.turn_control as turn_control_module
from aidm_server.models import DmTurn, Session, safe_json_dumps, safe_json_loads
from aidm_server.rules import RuleHint
from tests.helpers import seed_world_campaign_player_session


def _pool_checkout_probe(engine):
    lock = threading.Lock()
    active = 0

    def checkout(*_args):
        nonlocal active
        with lock:
            active += 1

    def checkin(*_args):
        nonlocal active
        with lock:
            active -= 1

    def snapshot() -> int:
        with lock:
            return active

    event.listen(engine, 'checkout', checkout)
    event.listen(engine, 'checkin', checkin)
    return snapshot, checkout, checkin


def _assert_provider_has_no_database_lease(snapshot) -> None:
    assert db.session().in_transaction() is False
    assert snapshot() == 0
    time.sleep(0.01)
    assert db.session().in_transaction() is False
    assert snapshot() == 0


def test_all_foreground_provider_waits_release_database_connection_and_persist_success(
    app,
    socketio,
    app_runtime,
    monkeypatch,
):
    socketio_module = app_runtime['modules']['socketio_events']
    turn_engine_module = app_runtime['modules']['turn_engine']
    observed: list[str] = []

    with app.app_context():
        engine = db.engine
    snapshot, checkout, checkin = _pool_checkout_probe(engine)

    class PreProvider:
        def generate(self, _request):
            _assert_provider_has_no_database_lease(snapshot)
            observed.append('pre_helper')
            return ProviderResponse(
                text='{"declaredActions":[],"notes":[]}',
                provider='fake',
                model='fake-pre',
            )

    class ConductorProvider:
        def generate(self, _request):
            _assert_provider_has_no_database_lease(snapshot)
            observed.append('turn_conductor')
            return ProviderResponse(
                text='{"decision":"allow","mode":"free","confidence":0.9}',
                provider='fake',
                model='fake-conductor',
            )

    class PostProvider:
        def generate(self, _request):
            _assert_provider_has_no_database_lease(snapshot)
            observed.append('post_helper')
            return ProviderResponse(
                text='{"proposedChanges":[],"uncertainChanges":[]}',
                provider='fake',
                model='fake-post',
            )

    def fake_stream(user_input, context, speaking_player=None, rules_hint=None):
        del user_input, context, speaking_player, rules_hint
        _assert_provider_has_no_database_lease(snapshot)
        observed.append('narration')
        yield 'The sentry lowers its blade and answers.'

    monkeypatch.setattr(pre_extractor_module, 'get_helper_provider', lambda: PreProvider())
    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: PostProvider())
    monkeypatch.setattr(turn_control_module, 'get_helper_provider', lambda: ConductorProvider())
    monkeypatch.setattr(socketio_module, 'query_dm_function_stream', fake_stream)
    monkeypatch.setattr(
        turn_engine_module,
        'classify_player_action',
        lambda _message: RuleHint(False, None, None, 'No check needed.', 0.9),
    )

    try:
        ids = seed_world_campaign_player_session(app)
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        app.config['AIDM_TURN_CONDUCTOR_HELPER_IN_TESTS'] = True
        client = socketio.test_client(app, flask_test_client=app.test_client())
        client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
        client.get_received()
        client.emit(
            'send_message',
            {
                'session_id': ids['session_id'],
                'campaign_id': ids['campaign_id'],
                'world_id': ids['world_id'],
                'player_id': ids['player_id'],
                'message': 'I attack, then stop and ask the sentry to surrender.',
            },
        )

        with app.app_context():
            turn = DmTurn.query.filter_by(session_id=ids['session_id']).one()
            assert turn.status == 'completed'
            assert turn.dm_output == 'The sentry lowers its blade and answers.'
            assert 'post_turn_error' not in safe_json_loads(turn.metadata_json, {})
        assert observed == ['turn_conductor', 'pre_helper', 'narration', 'post_helper']
    finally:
        event.remove(engine, 'checkout', checkout)
        event.remove(engine, 'checkin', checkin)


def test_failed_narration_reloads_turn_after_session_release_and_persists_failure(
    app,
    socketio,
    app_runtime,
    monkeypatch,
):
    socketio_module = app_runtime['modules']['socketio_events']

    with app.app_context():
        engine = db.engine
    snapshot, checkout, checkin = _pool_checkout_probe(engine)

    def failed_stream(user_input, context, speaking_player=None, rules_hint=None):
        del user_input, context, speaking_player, rules_hint
        _assert_provider_has_no_database_lease(snapshot)
        raise RuntimeError('provider unavailable')
        yield ''

    monkeypatch.setattr(socketio_module, 'query_dm_function_stream', failed_stream)

    try:
        ids = seed_world_campaign_player_session(app)
        client = socketio.test_client(app, flask_test_client=app.test_client())
        client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
        client.get_received()
        client.emit(
            'send_message',
            {
                'session_id': ids['session_id'],
                'campaign_id': ids['campaign_id'],
                'world_id': ids['world_id'],
                'player_id': ids['player_id'],
                'message': 'I listen at the sealed door.',
            },
        )

        with app.app_context():
            turn = DmTurn.query.filter_by(session_id=ids['session_id']).one()
            metadata = safe_json_loads(turn.metadata_json, {})
            assert turn.status == 'failed'
            assert metadata['error'] == 'The DM response could not be generated. Please retry.'
            assert 'post_turn_error' not in metadata
    finally:
        event.remove(engine, 'checkout', checkout)
        event.remove(engine, 'checkin', checkin)


def test_context_build_failure_persists_terminal_turn(
    app,
    socketio,
    app_runtime,
    monkeypatch,
):
    turn_engine_module = app_runtime['modules']['turn_engine']

    def failed_context(*args, **kwargs):
        del args, kwargs
        raise RuntimeError('context store unavailable')

    monkeypatch.setattr(turn_engine_module, 'build_dm_context', failed_context)

    ids = seed_world_campaign_player_session(app)
    client = socketio.test_client(app, flask_test_client=app.test_client())
    client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    client.get_received()
    client.emit(
        'send_message',
        {
            'session_id': ids['session_id'],
            'campaign_id': ids['campaign_id'],
            'world_id': ids['world_id'],
            'player_id': ids['player_id'],
            'message': 'I listen for movement beyond the door.',
        },
    )
    received = client.get_received()

    errors = [event['args'][0] for event in received if event['name'] == 'error']
    assert any(error['error_code'] == 'dm_context_failed' for error in errors)
    with app.app_context():
        turn = DmTurn.query.filter_by(session_id=ids['session_id']).one()
        metadata = safe_json_loads(turn.metadata_json, {})
        assert turn.status == 'failed'
        assert turn.dm_output is None
        assert turn.completed_at is not None
        assert metadata['error'] == 'The DM response could not be generated. Please retry.'


def test_narration_does_not_overwrite_turn_changed_while_provider_is_running(
    app,
    socketio,
    app_runtime,
    monkeypatch,
):
    socketio_module = app_runtime['modules']['socketio_events']

    def concurrent_update_stream(user_input, context, speaking_player=None, rules_hint=None):
        del user_input, context, speaking_player, rules_hint
        assert db.session().in_transaction() is False
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE dm_turns SET status = 'cancelled' "
                    'WHERE turn_id = (SELECT MAX(turn_id) FROM dm_turns)'
                )
            )
        yield 'This response must not overwrite the newer turn state.'

    monkeypatch.setattr(socketio_module, 'query_dm_function_stream', concurrent_update_stream)

    ids = seed_world_campaign_player_session(app)
    client = socketio.test_client(app, flask_test_client=app.test_client())
    client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    client.get_received()
    client.emit(
        'send_message',
        {
            'session_id': ids['session_id'],
            'campaign_id': ids['campaign_id'],
            'world_id': ids['world_id'],
            'player_id': ids['player_id'],
            'message': 'I wait for the door to open.',
        },
    )
    received = client.get_received()

    errors = [event['args'][0] for event in received if event['name'] == 'error']
    assert any(error['error_code'] == 'turn_persist_failed' for error in errors)
    with app.app_context():
        turn = DmTurn.query.filter_by(session_id=ids['session_id']).one()
        metadata = safe_json_loads(turn.metadata_json, {})
        assert turn.status == 'cancelled'
        assert turn.dm_output is None
        assert metadata['post_turn_error'] == 'The DM response could not be fully saved. Please retry.'


def test_helper_provider_failures_reload_models_before_heuristic_fallback(
    app,
    socketio,
    app_runtime,
    monkeypatch,
):
    socketio_module = app_runtime['modules']['socketio_events']
    turn_engine_module = app_runtime['modules']['turn_engine']
    calls: list[str] = []

    class FailedPreProvider:
        def generate(self, _request):
            assert db.session().in_transaction() is False
            calls.append('pre')
            raise RuntimeError('pre helper unavailable')

    class FailedPostProvider:
        def generate(self, _request):
            assert db.session().in_transaction() is False
            calls.append('post')
            raise RuntimeError('post helper unavailable')

    def successful_stream(user_input, context, speaking_player=None, rules_hint=None):
        del user_input, context, speaking_player, rules_hint
        assert db.session().in_transaction() is False
        yield 'The character uses the torch and continues.'

    monkeypatch.setattr(pre_extractor_module, 'get_helper_provider', lambda: FailedPreProvider())
    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FailedPostProvider())
    monkeypatch.setattr(socketio_module, 'query_dm_function_stream', successful_stream)
    monkeypatch.setattr(
        turn_engine_module,
        'classify_player_action',
        lambda _message: RuleHint(False, None, None, 'No check needed.', 0.9),
    )

    ids = seed_world_campaign_player_session(app)
    app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
    client = socketio.test_client(app, flask_test_client=app.test_client())
    client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    client.get_received()
    client.emit(
        'send_message',
        {
            'session_id': ids['session_id'],
            'campaign_id': ids['campaign_id'],
            'world_id': ids['world_id'],
            'player_id': ids['player_id'],
            'message': 'I use the torch to inspect the passage.',
        },
    )

    with app.app_context():
        turn = DmTurn.query.filter_by(session_id=ids['session_id']).one()
        metadata = safe_json_loads(turn.metadata_json, {})
        assert turn.status == 'completed'
        assert turn.dm_output == 'The character uses the torch and continues.'
        assert 'post_turn_error' not in metadata
    assert calls == ['pre', 'post']


def test_reload_failure_preserves_visible_narration_and_clears_processing_status(
    app,
    socketio,
    app_runtime,
    monkeypatch,
):
    socketio_module = app_runtime['modules']['socketio_events']
    turn_engine_module = app_runtime['modules']['turn_engine']

    def successful_stream(user_input, context, speaking_player=None, rules_hint=None):
        del user_input, context, speaking_player, rules_hint
        yield 'The visible narration survives the persistence reload failure.'

    def failed_reload(_token):
        raise RuntimeError('simulated reload failure')

    monkeypatch.setattr(socketio_module, 'query_dm_function_stream', successful_stream)
    monkeypatch.setattr(
        turn_engine_module.TurnEngine,
        '_reload_persistence_context',
        staticmethod(failed_reload),
    )

    ids = seed_world_campaign_player_session(app)
    client = socketio.test_client(app, flask_test_client=app.test_client())
    client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    client.get_received()
    client.emit(
        'send_message',
        {
            'session_id': ids['session_id'],
            'campaign_id': ids['campaign_id'],
            'world_id': ids['world_id'],
            'player_id': ids['player_id'],
            'message': 'I wait for the answer.',
        },
    )

    with app.app_context():
        turn = DmTurn.query.filter_by(session_id=ids['session_id']).one()
        metadata = safe_json_loads(turn.metadata_json, {})
        assert turn.status == 'completed'
        assert turn.dm_output == 'The visible narration survives the persistence reload failure.'
        assert turn.completed_at is not None
        assert metadata['post_turn_error'] == 'The DM response could not be fully saved. Please retry.'


def test_turn_conductor_discards_ai_decision_when_session_changes_during_provider(
    app,
    app_runtime,
    monkeypatch,
):
    turn_engine_module = app_runtime['modules']['turn_engine']
    ids = seed_world_campaign_player_session(app)
    app.config['AIDM_TURN_CONDUCTOR_HELPER_IN_TESTS'] = True
    changed_snapshot = safe_json_dumps(
        {
            'turnControl': {
                'mode': 'structured',
                'source': 'manual',
                'activePlayerId': ids['player_id'],
                'participantPlayerIds': [ids['player_id']],
            }
        },
        {},
    )

    class StaleConductorProvider:
        def generate(self, _request):
            assert db.session().in_transaction() is False
            with db.engine.begin() as connection:
                connection.execute(
                    text('UPDATE sessions SET state_snapshot = :snapshot WHERE session_id = :session_id'),
                    {'snapshot': changed_snapshot, 'session_id': ids['session_id']},
                )
            return ProviderResponse(
                text='{"decision":"switch_to_free","mode":"free","confidence":0.99}',
                provider='fake',
                model='stale-conductor',
            )

    monkeypatch.setattr(turn_control_module, 'get_helper_provider', lambda: StaleConductorProvider())

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        allowed, reason, turn_control, changed, decision = turn_control_module.conduct_turn_submission(
            session_obj,
            player_id=ids['player_id'],
            message='I wait for my opening.',
            action_intent={'kind': 'message'},
            active_player_ids=[ids['player_id']],
            before_helper_call=turn_engine_module.TurnEngine._release_clean_provider_session,
            reload_session_after_helper=lambda: db.session.get(Session, ids['session_id']),
        )

    assert allowed is True
    assert reason is None
    assert turn_control['mode'] == 'structured'
    assert changed is False
    assert decision['decision'] != 'switch_to_free'
