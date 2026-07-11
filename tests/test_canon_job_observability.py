from __future__ import annotations

import os
import time
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import pytest

from aidm_server.canon_jobs import (
    CANON_JOB_WORKER_EXTENSION,
    CanonJobQueueSnapshot,
    CanonJobWorkerState,
    _record_queue_observation,
    _worker_record_phase_timing,
    canon_job_queue_snapshot,
    enqueue_canon_job,
    process_due_canon_jobs,
    start_canon_job_worker,
    stop_canon_job_worker,
    wake_canon_job_worker,
)
from aidm_server.database import db
from aidm_server.models import CanonJob, Campaign, DmTurn
from aidm_server.provider_priority import provider_priority_gate
from aidm_server.time_utils import utc_now
from tests.helpers import seed_world_campaign_player_session


def _completed_turn(ids: dict, *, output: str) -> DmTurn:
    turn = DmTurn(
        session_id=ids['session_id'],
        campaign_id=ids['campaign_id'],
        player_id=ids['player_id'],
        player_input='Record this outcome.',
        dm_output=output,
        status='completed',
    )
    db.session.add(turn)
    db.session.flush()
    return turn


def test_queue_snapshot_reports_exact_current_gauges(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        jobs: list[CanonJob] = []
        for index, status in enumerate(('queued', 'queued', 'running', 'failed')):
            turn = _completed_turn(ids, output=f'Canon outcome {index}.')
            job = enqueue_canon_job(
                turn=turn,
                campaign=campaign,
                speaking_player_name='Seraphina',
            )
            job.status = status
            jobs.append(job)

        jobs[0].updated_at = utc_now().replace(tzinfo=None) - timedelta(seconds=120)
        jobs[1].updated_at = utc_now().replace(tzinfo=None) - timedelta(seconds=15)
        db.session.commit()

        snapshot = canon_job_queue_snapshot()

    assert snapshot.queued_count == 2
    assert snapshot.running_count == 1
    assert snapshot.failed_count == 1
    assert 115 <= snapshot.oldest_queued_age_seconds < 135


def test_queue_pressure_metrics_are_bounded_and_contain_no_ids(app):
    with app.app_context():
        with provider_priority_gate.foreground_reservation():
            _record_queue_observation(
                CanonJobQueueSnapshot(
                    queued_count=3,
                    running_count=1,
                    failed_count=0,
                    oldest_queued_age_seconds=61,
                )
            )
        _worker_record_phase_timing(
            'canon_apply',
            time.perf_counter(),
            campaign_id=987654,
            session_id=123456,
        )
        snapshot = app.extensions['aidm_telemetry'].snapshot()

    counters = snapshot['counters']
    assert counters['memory.canon_job.queue_nonempty_cycles_total'] == 1
    assert (
        counters[
            'memory.canon_job.foreground_pressure_cycles_total|state=waiting'
        ]
        == 1
    )
    assert (
        counters[
            'memory.canon_job.starvation_observations_total|foreground_pressure=waiting'
        ]
        == 1
    )
    assert 'socket.turn_phase_latency_ms|phase=canon_apply' in snapshot['timings']
    canon_keys = [
        key
        for section in ('counters', 'timings')
        for key in snapshot[section]
        if 'canon' in key
    ]
    assert canon_keys
    assert all('campaign_id=' not in key for key in canon_keys)
    assert all('session_id=' not in key for key in canon_keys)
    assert all('job_id=' not in key for key in canon_keys)
    assert all('turn_id=' not in key for key in canon_keys)


def test_due_job_runtime_uses_a_bounded_outcome_tag(app, monkeypatch):
    import aidm_server.canon_jobs as canon_jobs_module

    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        turn = _completed_turn(ids, output='A quick canon outcome.')
        enqueue_canon_job(
            turn=turn,
            campaign=campaign,
            speaking_player_name='Seraphina',
        )
        db.session.commit()

        monkeypatch.setattr(
            canon_jobs_module,
            'process_canon_job',
            lambda *args, **kwargs: SimpleNamespace(status='succeeded'),
        )
        assert process_due_canon_jobs(limit=1) == 1
        timings = app.extensions['aidm_telemetry'].snapshot()['timings']

    assert 'memory.canon_job.runtime_ms|outcome=succeeded' in timings


def test_worker_survives_initial_reset_failure_and_can_restart(
    app, socketio, monkeypatch
):
    import aidm_server.canon_jobs as canon_jobs_module

    reset_attempted = Event()
    reset_recovered = Event()
    attempts: list[int] = []

    def flaky_reset():
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            reset_attempted.set()
            raise RuntimeError('database temporarily unavailable')
        reset_recovered.set()
        return 0

    monkeypatch.setattr(canon_jobs_module, 'reset_stale_canon_jobs', flaky_reset)
    monkeypatch.setattr(canon_jobs_module, 'process_due_canon_jobs', lambda **kwargs: 0)
    monkeypatch.setattr(
        canon_jobs_module,
        'canon_job_queue_snapshot',
        lambda: CanonJobQueueSnapshot(0, 0, 0, 0.0),
    )
    app.config.update(TESTING=False, AIDM_ENV='development')

    assert start_canon_job_worker(app, socketio, interval_seconds=30) is True
    assert reset_attempted.wait(timeout=2)
    first_state = app.extensions[CANON_JOB_WORKER_EXTENSION]
    first_state.wake()
    assert reset_recovered.wait(timeout=2)
    assert app.extensions[CANON_JOB_WORKER_EXTENSION] is first_state

    assert stop_canon_job_worker(app) is True
    if hasattr(first_state.task, 'join'):
        first_state.task.join(timeout=2)
    assert CANON_JOB_WORKER_EXTENSION not in app.extensions

    assert start_canon_job_worker(app, socketio, interval_seconds=30) is True
    second_state = app.extensions[CANON_JOB_WORKER_EXTENSION]
    assert second_state is not first_state
    assert stop_canon_job_worker(app) is True
    if hasattr(second_state.task, 'join'):
        second_state.task.join(timeout=2)
    assert CANON_JOB_WORKER_EXTENSION not in app.extensions


def test_inherited_worker_state_cannot_be_woken_and_is_replaced(app):
    class DormantSocket:
        @staticmethod
        def start_background_task(*args, **kwargs):
            del args, kwargs
            return object()

    inherited = CanonJobWorkerState(owner_pid=os.getpid() + 1)
    app.extensions[CANON_JOB_WORKER_EXTENSION] = inherited
    app.config.update(TESTING=False, AIDM_ENV='development')

    assert wake_canon_job_worker(app) is False
    assert stop_canon_job_worker(app) is False
    assert start_canon_job_worker(app, DormantSocket()) is True
    current = app.extensions[CANON_JOB_WORKER_EXTENSION]
    assert current is not inherited
    assert current.owner_pid == os.getpid()

    app.extensions.pop(CANON_JOB_WORKER_EXTENSION, None)


def test_process_local_worker_rejects_multi_worker_model(app, socketio):
    app.config.update(
        TESTING=False,
        AIDM_ENV='development',
        AIDM_SOCKETIO_WORKER_MODEL='message_queue',
    )

    with pytest.raises(RuntimeError, match='requires AIDM_SOCKETIO_WORKER_MODEL=single'):
        start_canon_job_worker(app, socketio)

    assert CANON_JOB_WORKER_EXTENSION not in app.extensions
