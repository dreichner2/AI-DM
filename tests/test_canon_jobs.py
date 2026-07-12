from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, Lock, Thread, current_thread

import pytest

import aidm_server.services.campaign_pack_coordination as campaign_pack_coordination_module
from aidm_server.canon_jobs import (
    CANON_JOB_WORKER_EXTENSION,
    CANON_JOB_FAILED_MESSAGE,
    enqueue_canon_job,
    process_canon_job,
    reset_stale_canon_jobs,
    retry_canon_job,
    start_canon_job_worker,
    stop_canon_job_worker,
)
from aidm_server.contracts import ProviderResponse
from aidm_server.database import db
from aidm_server.models import (
    CanonJob,
    Campaign,
    DmTurn,
    Session,
    SessionState,
    TurnCanonUpdate,
    TurnEvent,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.time_utils import utc_now
from aidm_server.turn_coordinator import session_turn_coordinator
from tests.helpers import seed_world_campaign_player_session


def _empty_patch():
    return {
        'entities': [],
        'facts': [],
        'threads': [],
        'inventory_changes': [],
        'projection': {},
    }


def _seed_completed_turn(app, ids, *, dm_output='The silver key is now canon.'):
    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I take the silver key.',
            dm_output=dm_output,
            status='completed',
        )
        db.session.add(turn)
        db.session.commit()
        return turn.turn_id


def test_canon_job_processes_and_exposes_status_counts(client, app, monkeypatch):
    import aidm_server.canon_jobs as canon_jobs_module

    ids = seed_world_campaign_player_session(app)
    turn_id = _seed_completed_turn(app, ids)
    emitted_statuses: list[dict] = []

    def fake_extract(*args, **kwargs):
        del args, kwargs
        return (
            {
                **_empty_patch(),
                'entities': [
                    {
                        'entity_type': 'item',
                        'name': 'silver key',
                        'summary': 'A key made canon by the queued worker.',
                        'status': 'active',
                    }
                ],
            },
            'queued-test',
        )

    monkeypatch.setattr(canon_jobs_module, 'extract_canon_patch', fake_extract)

    def capture_status(session_id, emitted_turn_id, status, details=None):
        if status == 'canon_pending':
            assert db.session.registry.has() is False
        durable_job = CanonJob.query.filter_by(turn_id=emitted_turn_id).one()
        emitted_statuses.append(
            {
                'session_id': session_id,
                'turn_id': emitted_turn_id,
                'status': status,
                'details': details or {},
                'durable_job_status': durable_job.status,
            }
        )

    with app.app_context():
        turn = db.session.get(DmTurn, turn_id)
        campaign = db.session.get(Campaign, ids['campaign_id'])
        job = enqueue_canon_job(
            turn=turn,
            campaign=campaign,
            speaking_player_name='Seraphina',
            triggered_segments=[],
        )
        db.session.commit()
        job_id = job.job_id

        process_canon_job(
            job_id,
            emit_turn_status=capture_status,
        )

        job = db.session.get(CanonJob, job_id)
        turn = db.session.get(DmTurn, turn_id)
        update = TurnCanonUpdate.query.filter_by(turn_id=turn_id).one()
        event = TurnEvent.query.filter_by(turn_id=turn_id, event_type='canon_applied').one()
        state = SessionState.query.filter_by(session_id=ids['session_id']).one()

        assert job.status == 'succeeded'
        assert job.attempts == 1
        assert update.extractor_model == 'queued-test'
        assert safe_json_loads(event.payload_json, {})['canon_job_id'] == job_id
        assert safe_json_loads(turn.metadata_json, {})['canon_status'] == 'applied'
        assert 'silver key' in state.rolling_summary
        assert [status['status'] for status in emitted_statuses] == ['canon_pending', 'canon_applied']
        assert [status['durable_job_status'] for status in emitted_statuses] == ['running', 'succeeded']

    payload = client.get(f"/api/campaigns/{ids['campaign_id']}/canon").get_json()
    assert payload['summary']['canon_job_counts'] == {'succeeded': 1}


def test_canon_provider_wait_has_no_active_database_transaction(app, monkeypatch):
    import aidm_server.llm as llm_module

    ids = seed_world_campaign_player_session(app)
    turn_id = _seed_completed_turn(app, ids)
    provider_transaction_states: list[bool] = []

    class InspectingProvider:
        @staticmethod
        def generate(_request):
            provider_transaction_states.append(bool(db.session().in_transaction()))
            return ProviderResponse(text='{}', provider='test', model='transaction-probe')

    monkeypatch.setattr(llm_module, 'get_provider', lambda: InspectingProvider())

    with app.app_context():
        turn = db.session.get(DmTurn, turn_id)
        campaign = db.session.get(Campaign, ids['campaign_id'])
        job = enqueue_canon_job(turn=turn, campaign=campaign, speaking_player_name='Seraphina')
        db.session.commit()

        processed = process_canon_job(job.job_id)
        assert processed.status == 'succeeded'

    assert provider_transaction_states == [False]


def test_canon_waiter_preserves_foreground_snapshot_commit(app, monkeypatch):
    import aidm_server.canon_jobs as canon_jobs_module

    ids = seed_world_campaign_player_session(app)
    turn_id = _seed_completed_turn(app, ids)
    with app.app_context():
        app.config['AIDM_TURN_COORDINATOR_STORE'] = 'memory'
        turn = db.session.get(DmTurn, turn_id)
        campaign = db.session.get(Campaign, ids['campaign_id'])
        job = enqueue_canon_job(
            turn=turn,
            campaign=campaign,
            speaking_player_name='Seraphina',
        )
        db.session.commit()
        job_id = job.job_id

    monkeypatch.setattr(
        canon_jobs_module,
        'extract_canon_patch',
        lambda *args, **kwargs: (_empty_patch(), 'snapshot-waiter-test'),
    )
    initial_discovery_finished = Event()
    worker_errors: list[Exception] = []
    worker_statuses: list[str] = []
    original_discovery = (
        campaign_pack_coordination_module.campaign_pack_progress_lock_session_ids
    )

    def observe_discovery(session_id):
        lock_ids = original_discovery(session_id)
        if current_thread().name == 'canon-snapshot-waiter':
            initial_discovery_finished.set()
        return lock_ids

    monkeypatch.setattr(
        campaign_pack_coordination_module,
        'campaign_pack_progress_lock_session_ids',
        observe_discovery,
    )

    def run_waiting_canon_job():
        with app.app_context():
            try:
                processed = process_canon_job(job_id)
                worker_statuses.append(processed.status if processed else 'missing')
            except Exception as exc:  # pragma: no cover - assertion reports worker failures.
                worker_errors.append(exc)
            finally:
                db.session.remove()

    worker = Thread(target=run_waiting_canon_job, name='canon-snapshot-waiter')
    with app.app_context(), session_turn_coordinator.serialized(ids['session_id']):
        worker.start()
        assert initial_discovery_finished.wait(timeout=1.0)
        session_obj = db.session.get(Session, ids['session_id'], populate_existing=True)
        foreground_snapshot = safe_json_loads(session_obj.state_snapshot, {})
        foreground_snapshot['foregroundCommitMarker'] = 'must-survive-canon'
        session_obj.state_snapshot = safe_json_dumps(foreground_snapshot, {})
        db.session.commit()

    worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert worker_errors == []
    assert worker_statuses == ['succeeded']
    with app.app_context():
        final_snapshot = safe_json_loads(
            db.session.get(Session, ids['session_id']).state_snapshot,
            {},
        )
    assert final_snapshot['foregroundCommitMarker'] == 'must-survive-canon'


def test_canon_job_failure_is_durable_retryable_and_does_not_expose_internal_error(app, monkeypatch):
    import aidm_server.canon_jobs as canon_jobs_module

    ids = seed_world_campaign_player_session(app)
    turn_id = _seed_completed_turn(app, ids)
    internal_detail = 'postgresql://internal-user:secret@database.internal/aidm /srv/private/state.json'
    emitted_statuses: list[dict] = []

    def fail_extract(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(internal_detail)

    monkeypatch.setattr(canon_jobs_module, 'extract_canon_patch', fail_extract)

    with app.app_context():
        turn = db.session.get(DmTurn, turn_id)
        campaign = db.session.get(Campaign, ids['campaign_id'])
        job = enqueue_canon_job(
            turn=turn,
            campaign=campaign,
            speaking_player_name='Seraphina',
            triggered_segments=[],
        )
        db.session.commit()
        job_id = job.job_id

        process_canon_job(
            job_id,
            emit_turn_status=lambda session_id, emitted_turn_id, status, details=None: emitted_statuses.append(
                {
                    'session_id': session_id,
                    'turn_id': emitted_turn_id,
                    'status': status,
                    'details': details or {},
                }
            ),
        )
        job = db.session.get(CanonJob, job_id)
        turn = db.session.get(DmTurn, turn_id)
        assert job.status == 'failed'
        assert job.error_text == CANON_JOB_FAILED_MESSAGE
        metadata = safe_json_loads(turn.metadata_json, {})
        assert metadata['canon_status'] == 'failed'
        assert metadata['canon_error'] == CANON_JOB_FAILED_MESSAGE
        assert internal_detail not in str(metadata)

    failed_status = next(status for status in emitted_statuses if status['status'] == 'failed')
    assert failed_status['details'] == {'stage': 'canon_job', 'error': CANON_JOB_FAILED_MESSAGE}
    assert internal_detail not in str(emitted_statuses)

    monkeypatch.setattr(canon_jobs_module, 'extract_canon_patch', lambda *args, **kwargs: (_empty_patch(), 'retry-ok'))

    with app.app_context():
        retry_canon_job(job_id)
        process_canon_job(job_id)
        job = db.session.get(CanonJob, job_id)
        turn = db.session.get(DmTurn, turn_id)
        assert job.status == 'succeeded'
        assert job.attempts == 2
        assert job.error_text is None
        assert safe_json_loads(turn.metadata_json, {})['canon_status'] == 'applied'


def test_canon_job_claim_is_atomic_across_concurrent_workers(app, monkeypatch):
    import aidm_server.canon_jobs as canon_jobs_module

    ids = seed_world_campaign_player_session(app)
    turn_id = _seed_completed_turn(app, ids)
    extract_calls = []

    def slow_extract(*args, **kwargs):
        del args, kwargs
        extract_calls.append('called')
        time.sleep(0.05)
        return _empty_patch(), 'concurrent-ok'

    monkeypatch.setattr(canon_jobs_module, 'extract_canon_patch', slow_extract)

    with app.app_context():
        turn = db.session.get(DmTurn, turn_id)
        campaign = db.session.get(Campaign, ids['campaign_id'])
        job = enqueue_canon_job(turn=turn, campaign=campaign, speaking_player_name='Seraphina')
        db.session.commit()
        job_id = job.job_id

    def process_once():
        with app.app_context():
            processed = process_canon_job(job_id)
            return processed.status if processed else None

    with ThreadPoolExecutor(max_workers=3) as executor:
        statuses = list(executor.map(lambda _index: process_once(), range(3)))

    assert extract_calls == ['called']
    assert statuses.count('succeeded') >= 1
    with app.app_context():
        job = db.session.get(CanonJob, job_id)
        assert job.status == 'succeeded'
        assert job.attempts == 1


def test_concurrent_enqueue_reconciles_to_one_durable_job(app):
    ids = seed_world_campaign_player_session(app)
    turn_id = _seed_completed_turn(app, ids)
    ready = Barrier(2)

    def enqueue_once():
        with app.app_context():
            turn = db.session.get(DmTurn, turn_id)
            campaign = db.session.get(Campaign, ids['campaign_id'])
            ready.wait(timeout=2)
            job = enqueue_canon_job(turn=turn, campaign=campaign, speaking_player_name='Seraphina')
            db.session.commit()
            return job.job_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        job_ids = list(executor.map(lambda _index: enqueue_once(), range(2)))

    assert job_ids[0] == job_ids[1]
    with app.app_context():
        assert CanonJob.query.filter_by(turn_id=turn_id).count() == 1


def test_stale_running_canon_job_resets_to_queued(app):
    ids = seed_world_campaign_player_session(app)
    turn_id = _seed_completed_turn(app, ids)

    with app.app_context():
        turn = db.session.get(DmTurn, turn_id)
        campaign = db.session.get(Campaign, ids['campaign_id'])
        job = enqueue_canon_job(
            turn=turn,
            campaign=campaign,
            speaking_player_name='Seraphina',
            triggered_segments=[],
        )
        job.status = 'running'
        job.locked_at = utc_now() - timedelta(minutes=30)
        db.session.commit()
        job_id = job.job_id

        assert reset_stale_canon_jobs(stale_after_seconds=60) == 1

        job = db.session.get(CanonJob, job_id)
        assert job.status == 'queued'
        assert job.locked_at is None
        assert job.error_text == 'Reset after stale running lock.'


def test_retry_does_not_requeue_live_or_succeeded_jobs(app):
    ids = seed_world_campaign_player_session(app)
    turn_id = _seed_completed_turn(app, ids)

    with app.app_context():
        turn = db.session.get(DmTurn, turn_id)
        campaign = db.session.get(Campaign, ids['campaign_id'])
        job = enqueue_canon_job(turn=turn, campaign=campaign, speaking_player_name='Seraphina')
        job.status = 'running'
        job.attempts = 1
        db.session.commit()
        job_id = job.job_id

        assert retry_canon_job(job_id).status == 'running'
        job = db.session.get(CanonJob, job_id)
        assert job.status == 'running'
        assert job.attempts == 1

        job.status = 'succeeded'
        db.session.commit()
        assert retry_canon_job(job_id).status == 'succeeded'
        assert db.session.get(CanonJob, job_id).status == 'succeeded'


def test_stale_extraction_failure_cannot_fail_a_newer_attempt(app, monkeypatch):
    import aidm_server.canon_jobs as canon_jobs_module

    ids = seed_world_campaign_player_session(app)
    turn_id = _seed_completed_turn(app, ids)

    with app.app_context():
        turn = db.session.get(DmTurn, turn_id)
        campaign = db.session.get(Campaign, ids['campaign_id'])
        job = enqueue_canon_job(turn=turn, campaign=campaign, speaking_player_name='Seraphina')
        db.session.commit()
        job_id = job.job_id

    def supersede_then_fail(*args, **kwargs):
        del args, kwargs
        job = db.session.get(CanonJob, job_id)
        job.status = 'running'
        job.attempts = 2
        db.session.commit()
        raise RuntimeError('attempt one finished late')

    monkeypatch.setattr(canon_jobs_module, 'extract_canon_patch', supersede_then_fail)

    with app.app_context():
        result = process_canon_job(job_id)
        assert result.status == 'running'
        job = db.session.get(CanonJob, job_id)
        assert job.status == 'running'
        assert job.attempts == 2
        assert job.error_text is None


def test_worker_is_single_wakeable_and_drains_burst_serially(app, socketio, monkeypatch):
    import aidm_server.canon_jobs as canon_jobs_module

    ids = seed_world_campaign_player_session(app)
    job_ids: list[int] = []
    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        for index in range(5):
            turn_id = _seed_completed_turn(app, ids, dm_output=f'Canon burst result {index}.')
            turn = db.session.get(DmTurn, turn_id)
            job = enqueue_canon_job(turn=turn, campaign=campaign, speaking_player_name='Seraphina')
            db.session.commit()
            job_ids.append(job.job_id)

    active = 0
    max_active = 0
    extraction_calls: list[int] = []
    activity_lock = Lock()

    def serial_extract(*args, **kwargs):
        nonlocal active, max_active
        del args, kwargs
        with activity_lock:
            active += 1
            max_active = max(max_active, active)
            extraction_calls.append(active)
        time.sleep(0.01)
        with activity_lock:
            active -= 1
        return _empty_patch(), 'burst-test'

    monkeypatch.setattr(canon_jobs_module, 'extract_canon_patch', serial_extract)
    original_start = socketio.start_background_task
    worker_starts = 0

    def track_start(*args, **kwargs):
        nonlocal worker_starts
        worker_starts += 1
        return original_start(*args, **kwargs)

    monkeypatch.setattr(socketio, 'start_background_task', track_start)
    app.config.update(TESTING=False, AIDM_ENV='development')

    assert start_canon_job_worker(app, socketio, interval_seconds=30, batch_limit=2) is True
    assert start_canon_job_worker(app, socketio, interval_seconds=30, batch_limit=2) is False

    deadline = time.monotonic() + 5
    statuses: list[str] = []
    while time.monotonic() < deadline:
        with app.app_context():
            statuses = [db.session.get(CanonJob, job_id).status for job_id in job_ids]
            db.session.remove()
        if statuses == ['succeeded'] * len(job_ids):
            break
        time.sleep(0.02)

    assert statuses == ['succeeded'] * len(job_ids)
    assert worker_starts == 1
    assert len(extraction_calls) == len(job_ids)
    assert max_active == 1
    assert stop_canon_job_worker(app) is True
    state = app.extensions[CANON_JOB_WORKER_EXTENSION]
    if hasattr(state.task, 'join'):
        state.task.join(timeout=2)


def test_worker_start_failure_does_not_poison_restart_state(app):
    class FailingSocket:
        @staticmethod
        def start_background_task(*args, **kwargs):
            del args, kwargs
            raise RuntimeError('thread unavailable')

    app.config.update(TESTING=False, AIDM_ENV='development')
    with pytest.raises(RuntimeError, match='thread unavailable'):
        start_canon_job_worker(app, FailingSocket())
    assert CANON_JOB_WORKER_EXTENSION not in app.extensions
