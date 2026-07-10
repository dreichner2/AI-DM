from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from aidm_server.canon_jobs import (
    CANON_JOB_FAILED_MESSAGE,
    enqueue_canon_job,
    process_canon_job,
    reset_stale_canon_jobs,
    retry_canon_job,
)
from aidm_server.database import db
from aidm_server.models import (
    CanonJob,
    Campaign,
    DmTurn,
    SessionState,
    TurnCanonUpdate,
    TurnEvent,
    safe_json_loads,
)
from aidm_server.time_utils import utc_now
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
            emit_turn_status=lambda session_id, turn_id, status, details=None: emitted_statuses.append(
                {'session_id': session_id, 'turn_id': turn_id, 'status': status, 'details': details or {}}
            ),
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

    payload = client.get(f"/api/campaigns/{ids['campaign_id']}/canon").get_json()
    assert payload['summary']['canon_job_counts'] == {'succeeded': 1}


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
