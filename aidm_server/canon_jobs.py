"""Durable canon extraction job queue."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from threading import Event, Lock

from flask import current_app
from sqlalchemy import case, func, or_, update
from sqlalchemy.exc import IntegrityError

from aidm_server.database import commit_with_retry, db, release_clean_scoped_session
from aidm_server.emergent_memory import (
    append_session_memory,
    apply_canon_patch,
    extract_canon_patch,
    refresh_session_projection,
    validate_canon_patch,
)
from aidm_server.models import CanonJob, Campaign, CampaignSegment, DmTurn, safe_json_dumps, safe_json_loads
from aidm_server.provider_priority import provider_priority_gate
from aidm_server.segment_state import build_segment_state_payload
from aidm_server.segment_triggers import evaluate_segment_trigger, parse_trigger_spec
from aidm_server.services.campaign_pack_progress import update_campaign_pack_progress
from aidm_server.services.campaign_pack_coordination import serialized_campaign_pack_progress
from aidm_server.socket_contracts import segment_triggered_payload, turn_status_payload
from aidm_server.telemetry import telemetry_event, telemetry_metric, telemetry_timing
from aidm_server.time_utils import utc_now
from aidm_server.turn_events import (
    CANON_APPLIED_EVENT,
    SEGMENT_TRIGGERED_EVENT,
    record_turn_event,
)


logger = logging.getLogger(__name__)

CANON_JOB_RUNNABLE_STATUSES = {'queued'}
CANON_JOB_TERMINAL_STATUSES = {'succeeded', 'failed', 'cancelled'}
DEFAULT_CANON_JOB_MAX_ATTEMPTS = 1
DEFAULT_CANON_JOB_RETRY_DELAY_SECONDS = 30
DEFAULT_CANON_JOB_STALE_LOCK_SECONDS = 15 * 60
DEFAULT_CANON_JOB_WORKER_INTERVAL_SECONDS = 5
DEFAULT_CANON_JOB_WORKER_BATCH_LIMIT = 3
DEFAULT_CANON_JOB_STARVATION_OBSERVATION_SECONDS = 60
CANON_JOB_FAILED_MESSAGE = 'Canon processing failed. The DM response remains saved.'
CANON_JOB_WORKER_EXTENSION = 'aidm_canon_job_worker'

TurnStatusEmitter = Callable[[int, int | None, str, dict | None], None]
SegmentEmitter = Callable[[int, dict], None]
PhaseRecorder = Callable[..., None]


@dataclass
class CanonJobWorkerState:
    """One process-local wakeable worker for the durable queue."""

    owner_pid: int = field(default_factory=os.getpid)
    wake_event: Event = field(default_factory=Event)
    stop_event: Event = field(default_factory=Event)
    task: object | None = None

    def wake(self) -> None:
        self.wake_event.set()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()


_CANON_JOB_WORKER_START_LOCK = Lock()


@dataclass(frozen=True)
class CanonJobQueueSnapshot:
    """Current durable queue gauges, computed in one aggregate query."""

    queued_count: int
    running_count: int
    failed_count: int
    oldest_queued_age_seconds: float


def canon_job_queue_snapshot() -> CanonJobQueueSnapshot:
    """Return exact current queue gauges without job- or tenant-level labels."""

    queued_at = case(
        (CanonJob.status == 'queued', CanonJob.updated_at),
        else_=None,
    )
    row = (
        db.session.query(
            func.sum(case((CanonJob.status == 'queued', 1), else_=0)),
            func.sum(case((CanonJob.status == 'running', 1), else_=0)),
            func.sum(case((CanonJob.status == 'failed', 1), else_=0)),
            func.min(queued_at),
        )
        # Successful/cancelled history grows indefinitely and does not
        # contribute to these gauges. Let the status index exclude it before
        # the aggregate instead of rescanning the full queue table every cycle.
        .filter(CanonJob.status.in_(('queued', 'running', 'failed')))
        .one()
    )
    oldest_queued_at = row[3]
    oldest_age_seconds = 0.0
    if oldest_queued_at is not None:
        oldest_age_seconds = max(
            0.0,
            float((_job_timestamp() - oldest_queued_at).total_seconds()),
        )
    return CanonJobQueueSnapshot(
        queued_count=int(row[0] or 0),
        running_count=int(row[1] or 0),
        failed_count=int(row[2] or 0),
        oldest_queued_age_seconds=oldest_age_seconds,
    )


def _safe_triggered_segments(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _job_timestamp():
    return utc_now().replace(tzinfo=None)


def _foreground_pressure_state() -> str:
    snapshot = provider_priority_gate.snapshot()
    if snapshot.active_foreground and snapshot.waiting_foreground:
        return 'active_and_waiting'
    if snapshot.waiting_foreground:
        return 'waiting'
    if snapshot.active_foreground:
        return 'active'
    return 'none'


def _record_queue_observation(snapshot: CanonJobQueueSnapshot) -> None:
    """Record bounded queue-pressure events; exact gauges come from the snapshot."""

    if snapshot.queued_count <= 0:
        return
    telemetry_metric('memory.canon_job.queue_nonempty_cycles_total', 1)
    pressure = _foreground_pressure_state()
    if pressure != 'none':
        telemetry_metric(
            'memory.canon_job.foreground_pressure_cycles_total',
            1,
            tags={'state': pressure},
        )
    if (
        snapshot.oldest_queued_age_seconds
        >= DEFAULT_CANON_JOB_STARVATION_OBSERVATION_SECONDS
    ):
        telemetry_metric(
            'memory.canon_job.starvation_observations_total',
            1,
            tags={'foreground_pressure': pressure},
        )


def _record_enqueue_attempt(result: str) -> None:
    telemetry_metric(
        'memory.canon_job.enqueue_attempts_total',
        1,
        tags={'result': result},
    )


def _set_turn_canon_metadata(
    turn: DmTurn | None,
    *,
    status: str,
    job: CanonJob | None,
    error: str | None = None,
) -> None:
    if not turn:
        return
    metadata = safe_json_loads(turn.metadata_json, {})
    metadata['canon_status'] = status
    if job is not None:
        metadata['canon_job_id'] = job.job_id
        metadata['canon_job_attempts'] = job.attempts
    if error:
        metadata['canon_error'] = error
    elif 'canon_error' in metadata:
        metadata.pop('canon_error', None)
    metadata['canon_status_updated_at'] = utc_now().isoformat()
    turn.metadata_json = safe_json_dumps(metadata, {})


def _emit_status(
    emit_turn_status: TurnStatusEmitter | None,
    session_id: int,
    turn_id: int | None,
    status: str,
    details: dict | None = None,
) -> None:
    if emit_turn_status:
        emit_turn_status(session_id, turn_id, status, details)


def _record_phase(
    record_phase_timing: PhaseRecorder | None,
    phase: str,
    started_at: float,
    *,
    campaign_id: int,
    session_id: int,
) -> None:
    if record_phase_timing:
        record_phase_timing(phase, started_at, campaign_id=campaign_id, session_id=session_id)


def enqueue_canon_job(
    *,
    turn: DmTurn,
    campaign: Campaign,
    speaking_player_name: str,
    triggered_segments: list[dict] | None = None,
    max_attempts: int = DEFAULT_CANON_JOB_MAX_ATTEMPTS,
) -> CanonJob:
    existing = CanonJob.query.filter_by(turn_id=turn.turn_id).first()
    if existing:
        if existing.status not in CANON_JOB_TERMINAL_STATUSES:
            existing.speaking_player_name = speaking_player_name
            existing.triggered_segments_json = safe_json_dumps(triggered_segments or [], [])
            existing.updated_at = _job_timestamp()
        _set_turn_canon_metadata(turn, status=existing.status, job=existing)
        db.session.flush()
        _record_enqueue_attempt(
            'existing_terminal'
            if existing.status in CANON_JOB_TERMINAL_STATUSES
            else 'existing_active'
        )
        return existing

    job = CanonJob(
        turn_id=turn.turn_id,
        campaign_id=campaign.campaign_id,
        session_id=turn.session_id,
        status='queued',
        attempts=0,
        max_attempts=max(1, int(max_attempts)),
        speaking_player_name=speaking_player_name,
        triggered_segments_json=safe_json_dumps(triggered_segments or [], []),
        next_run_at=_job_timestamp(),
    )
    try:
        # The turn id is unique. A savepoint keeps a concurrent enqueue race
        # from rolling back unrelated post-turn work in the caller's outer
        # transaction.
        with db.session.begin_nested():
            db.session.add(job)
            db.session.flush()
    except IntegrityError:
        existing = CanonJob.query.filter_by(turn_id=turn.turn_id).first()
        if not existing:
            raise
        if existing.status not in CANON_JOB_TERMINAL_STATUSES:
            existing.speaking_player_name = speaking_player_name
            existing.triggered_segments_json = safe_json_dumps(
                triggered_segments or [], []
            )
            existing.updated_at = _job_timestamp()
        _set_turn_canon_metadata(turn, status=existing.status, job=existing)
        db.session.flush()
        _record_enqueue_attempt('race_reconciled')
        return existing
    _set_turn_canon_metadata(turn, status='queued', job=job)
    _record_enqueue_attempt('created')
    return job


def retry_canon_job(job_id: int) -> CanonJob | None:
    job = db.session.get(CanonJob, job_id)
    if not job:
        return None
    if job.status != 'failed':
        return job
    job.status = 'queued'
    job.error_text = None
    job.locked_at = None
    job.completed_at = None
    job.next_run_at = _job_timestamp()
    job.updated_at = _job_timestamp()
    _set_turn_canon_metadata(job.turn, status='queued', job=job)
    commit_with_retry(label='canon job retry')
    wake_canon_job_worker(current_app._get_current_object())  # type: ignore[attr-defined]
    return job


def reset_stale_canon_jobs(*, stale_after_seconds: int = DEFAULT_CANON_JOB_STALE_LOCK_SECONDS) -> int:
    cutoff = _job_timestamp() - timedelta(seconds=max(1, int(stale_after_seconds)))
    stale_jobs = CanonJob.query.filter(
        CanonJob.status == 'running',
        CanonJob.locked_at.isnot(None),
        CanonJob.locked_at < cutoff,
    ).all()
    for job in stale_jobs:
        job.status = 'queued'
        job.error_text = 'Reset after stale running lock.'
        job.locked_at = None
        job.next_run_at = _job_timestamp()
        job.updated_at = _job_timestamp()
        _set_turn_canon_metadata(job.turn, status='queued', job=job)
    if stale_jobs:
        commit_with_retry(label='stale canon job reset')
    return len(stale_jobs)


def _claim_canon_job(job_id: int) -> CanonJob | None:
    now = _job_timestamp()
    result = db.session.execute(
        update(CanonJob)
        .where(
            CanonJob.job_id == job_id,
            CanonJob.status.in_(CANON_JOB_RUNNABLE_STATUSES),
            or_(CanonJob.next_run_at.is_(None), CanonJob.next_run_at <= now),
        )
        .values(
            status='running',
            attempts=CanonJob.attempts + 1,
            locked_at=now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        db.session.rollback()
        job = db.session.get(CanonJob, job_id)
        return job if job and job.status in CANON_JOB_TERMINAL_STATUSES else None

    commit_with_retry(label='canon job claim')
    job = db.session.get(CanonJob, job_id)
    if not job:
        return None
    _set_turn_canon_metadata(job.turn, status='running', job=job)
    commit_with_retry(label='canon job running metadata')
    return job


def _segment_state_payload(session_id: int, campaign: Campaign) -> tuple[dict, dict]:
    return build_segment_state_payload(session_id, campaign)


def _activate_state_segments(turn: DmTurn, segments_to_activate: list[tuple[CampaignSegment, dict]]) -> list[dict]:
    triggered_segments: list[dict] = []
    for segment, payload in segments_to_activate:
        segment.is_triggered = True
        triggered_segments.append(payload)
        record_turn_event(
            session_id=turn.session_id,
            campaign_id=turn.campaign_id,
            turn_id=turn.turn_id,
            player_id=turn.player_id,
            event_type=SEGMENT_TRIGGERED_EVENT,
            payload={
                'title': segment.title,
                'reason': payload.get('reason'),
                'segment_id': segment.segment_id,
                'metadata': {'turn_id': turn.turn_id, 'reason': payload.get('reason')},
            },
        )
    return triggered_segments


def _evaluate_state_segments_after_turn(
    turn: DmTurn,
    campaign: Campaign,
    *,
    progress_commit: bool = True,
) -> list[dict]:
    if not current_app.config.get('AIDM_SEGMENT_EVALUATOR_ENABLED', True):
        return []

    try:
        session_state_payload, campaign_state = _segment_state_payload(turn.session_id, campaign)
        segments_to_activate: list[tuple[CampaignSegment, dict]] = []
        untriggered_segments = CampaignSegment.query.filter_by(
            campaign_id=campaign.campaign_id,
            is_triggered=False,
        ).all()

        for segment in untriggered_segments:
            trigger_type = parse_trigger_spec(segment.trigger_condition).trigger_type
            if trigger_type != 'state':
                continue

            matched, reason, trigger_spec = evaluate_segment_trigger(
                trigger_condition=segment.trigger_condition,
                player_message=turn.player_input,
                session_state=session_state_payload,
                campaign_state=campaign_state,
            )
            if not matched:
                continue

            payload = segment_triggered_payload(
                segment_id=segment.segment_id,
                title=segment.title,
                description=segment.description,
                reason=reason,
                trigger_spec=trigger_spec,
            )
            segments_to_activate.append((segment, payload))

        triggered_segments = _activate_state_segments(turn, segments_to_activate)
        update_campaign_pack_progress(
            session_id=turn.session_id,
            campaign_id=campaign.campaign_id,
            triggered_segments=triggered_segments,
            commit=progress_commit,
        )
        return triggered_segments
    except Exception as exc:
        logger.error('Post-turn state segment evaluation failed: %s', str(exc))
        telemetry_event(
            'socket.segment_state_evaluation_failed',
            payload={'session_id': turn.session_id, 'campaign_id': campaign.campaign_id, 'error': str(exc)},
            severity='error',
        )
        return []


def _segment_thread_patch(triggered_segments: list[dict]) -> dict:
    return {
        'entities': [],
        'facts': [],
        'threads': [
            {
                'title': str(segment_payload.get('title') or '').strip(),
                'summary': f'Authored story thread activated: {str(segment_payload.get("title") or "").strip()}.',
                'status': 'open',
                'priority': 2,
                'source': 'segment',
                'metadata': {
                    'segment_id': segment_payload.get('segment_id'),
                    'reason': segment_payload.get('reason'),
                },
            }
            for segment_payload in triggered_segments
            if str(segment_payload.get('title') or '').strip()
        ],
        'inventory_changes': [],
        'projection': {},
    }


def _mark_job_failed(
    job_id: int,
    error: str,
    *,
    expected_attempt: int,
    emit_turn_status: TurnStatusEmitter | None = None,
) -> CanonJob | None:
    db.session.rollback()
    now = _job_timestamp()
    result = db.session.execute(
        update(CanonJob)
        .where(
            CanonJob.job_id == job_id,
            CanonJob.status == 'running',
            CanonJob.attempts == expected_attempt,
        )
        .values(
            status='failed',
            error_text=error,
            completed_at=now,
            updated_at=now,
            locked_at=None,
        )
    )
    if result.rowcount != 1:
        db.session.rollback()
        return db.session.get(CanonJob, job_id)

    job = db.session.get(CanonJob, job_id)
    if not job:
        db.session.rollback()
        return None
    _set_turn_canon_metadata(job.turn, status='failed', job=job, error=error)
    commit_with_retry(label='canon job failure')
    _emit_status(
        emit_turn_status,
        job.session_id,
        job.turn_id,
        'failed',
        {'stage': 'canon_job', 'error': error},
    )
    telemetry_event(
        'memory.canon_job_failed',
        payload={'job_id': job.job_id, 'turn_id': job.turn_id, 'campaign_id': job.campaign_id, 'error': error},
        severity='error',
    )
    return job


def _apply_canon_job_result(
    job_id: int,
    *,
    claimed_attempt: int,
    dm_output: str,
    triggered_segments: list[dict],
    patch: dict,
    extractor_model: str,
    emit_turn_status: TurnStatusEmitter | None,
    emit_segment_triggered: SegmentEmitter | None,
    record_phase_timing: PhaseRecorder | None,
) -> CanonJob | None:
    """Reload, validate, and commit one extracted result under the turn lock."""

    job = db.session.get(CanonJob, job_id)
    if not job:
        return None
    if job.status != 'running' or int(job.attempts) != claimed_attempt:
        telemetry_event(
            'memory.canon_job_stale_result_discarded',
            payload={
                'job_id': job_id,
                'claimed_attempt': claimed_attempt,
                'current_attempt': int(job.attempts),
                'current_status': job.status,
            },
            severity='warning',
        )
        return job

    turn = db.session.get(DmTurn, job.turn_id)
    campaign = db.session.get(Campaign, job.campaign_id)
    if not turn or not campaign:
        return _mark_job_failed(
            job_id,
            'Canon job turn or campaign is missing.',
            expected_attempt=claimed_attempt,
            emit_turn_status=emit_turn_status,
        )
    if dm_output:
        append_session_memory(turn)
    applied_status_details: dict = {}

    canon_validation_started = time.perf_counter()
    validated_patch, rejections = validate_canon_patch(
        turn=turn, campaign=campaign, patch=patch
    )
    _record_phase(
        record_phase_timing,
        'canon_validation',
        canon_validation_started,
        campaign_id=campaign.campaign_id,
        session_id=turn.session_id,
    )
    if rejections:
        telemetry_metric('memory.validation.rejections_total', len(rejections))
        telemetry_event(
            'memory.validation.rejections',
            payload={
                'campaign_id': campaign.campaign_id,
                'turn_id': turn.turn_id,
                'rejections': rejections,
            },
            severity='warning',
        )

    canon_apply_started = time.perf_counter()
    applied_summary = apply_canon_patch(
        turn=turn,
        campaign=campaign,
        patch=validated_patch,
        extractor_model=extractor_model,
        rejections=rejections,
    )
    _record_phase(
        record_phase_timing,
        'canon_apply',
        canon_apply_started,
        campaign_id=campaign.campaign_id,
        session_id=turn.session_id,
    )
    record_turn_event(
        session_id=turn.session_id,
        campaign_id=campaign.campaign_id,
        turn_id=turn.turn_id,
        player_id=turn.player_id,
        event_type=CANON_APPLIED_EVENT,
        payload={
            'extractor_model': extractor_model,
            'rejection_count': len(rejections),
            'thread_count': len(validated_patch.get('threads', [])),
            'entity_count': len(validated_patch.get('entities', [])),
            'fact_count': len(validated_patch.get('facts', [])),
            'canon_job_id': job.job_id,
        },
        project_legacy=False,
    )
    applied_status_details = {
        'extractor_model': extractor_model,
        'rejection_count': len(rejections),
        'job_id': job.job_id,
        'player_id': turn.player_id,
        'inventory_changes_applied': applied_summary.get(
            'inventory_changes_applied', []
        ),
        'character_state_changes_applied': applied_summary.get(
            'character_state_changes_applied', []
        ),
    }

    projection_started = time.perf_counter()
    refresh_session_projection(
        session_id=turn.session_id,
        campaign=campaign,
        triggered_segments=triggered_segments,
    )
    post_turn_segments = _evaluate_state_segments_after_turn(
        turn,
        campaign,
        progress_commit=False,
    )
    if post_turn_segments:
        segment_patch = _segment_thread_patch(post_turn_segments)
        validated_segment_patch, segment_rejections = validate_canon_patch(
            turn=turn,
            campaign=campaign,
            patch=segment_patch,
        )
        if segment_rejections:
            telemetry_metric(
                'memory.validation.rejections_total', len(segment_rejections)
            )
            telemetry_event(
                'memory.validation.rejections',
                payload={
                    'campaign_id': campaign.campaign_id,
                    'turn_id': turn.turn_id,
                    'rejections': segment_rejections,
                },
                severity='warning',
            )
        apply_canon_patch(
            turn=turn,
            campaign=campaign,
            patch=validated_segment_patch,
            extractor_model='segment-state-v1',
            rejections=segment_rejections,
        )
        refresh_session_projection(
            session_id=turn.session_id,
            campaign=campaign,
            triggered_segments=post_turn_segments,
        )
    _record_phase(
        record_phase_timing,
        'projection_refresh',
        projection_started,
        campaign_id=campaign.campaign_id,
        session_id=turn.session_id,
    )

    now = _job_timestamp()
    job.status = 'succeeded'
    job.error_text = None
    job.completed_at = now
    job.locked_at = None
    job.updated_at = now
    _set_turn_canon_metadata(turn, status='applied', job=job)
    commit_with_retry(label='canon job success')
    telemetry_metric('memory.canon_job.succeeded_total', 1)

    # Realtime notifications describe durable state. Emit only after the
    # success transaction commits, and never turn an emitter failure into a
    # failed durable canon job.
    try:
        _emit_status(
            emit_turn_status,
            turn.session_id,
            turn.turn_id,
            'canon_applied',
            applied_status_details,
        )
    except Exception as exc:  # pragma: no cover - transport defensive guard.
        logger.warning('Committed canon status emit failed: %s', str(exc))
        telemetry_event(
            'memory.canon_job.emit_failed',
            payload={'job_id': job.job_id, 'event': 'canon_applied'},
            severity='warning',
        )
    if emit_segment_triggered:
        for segment_payload in post_turn_segments:
            try:
                emit_segment_triggered(turn.session_id, segment_payload)
            except Exception as exc:  # pragma: no cover - transport defensive guard.
                logger.warning('Committed segment emit failed: %s', str(exc))
                telemetry_event(
                    'memory.canon_job.emit_failed',
                    payload={'job_id': job.job_id, 'event': 'segment_triggered'},
                    severity='warning',
                )
    if post_turn_segments:
        try:
            _emit_status(
                emit_turn_status,
                turn.session_id,
                turn.turn_id,
                'canon_applied',
                {
                    'stage': 'segment_evaluation',
                    'snapshot_changed': True,
                    'campaign_pack_progress_changed': True,
                },
            )
        except Exception as exc:  # pragma: no cover - transport defensive guard.
            logger.warning('Committed segment status emit failed: %s', str(exc))
            telemetry_event(
                'memory.canon_job.emit_failed',
                payload={'job_id': job.job_id, 'event': 'segment_evaluation'},
                severity='warning',
            )
    return job


def process_canon_job(
    job_id: int,
    *,
    emit_turn_status: TurnStatusEmitter | None = None,
    emit_segment_triggered: SegmentEmitter | None = None,
    record_phase_timing: PhaseRecorder | None = None,
) -> CanonJob | None:
    claimed = _claim_canon_job(job_id)
    if not claimed or claimed.status in CANON_JOB_TERMINAL_STATUSES:
        return claimed

    job = db.session.get(CanonJob, job_id)
    if not job:
        return None
    claimed_attempt = int(job.attempts)
    turn = db.session.get(DmTurn, job.turn_id)
    campaign = db.session.get(Campaign, job.campaign_id)
    if not turn or not campaign:
        return _mark_job_failed(
            job_id,
            'Canon job turn or campaign is missing.',
            expected_attempt=claimed_attempt,
            emit_turn_status=emit_turn_status,
        )

    campaign_id = int(campaign.campaign_id)
    session_id = int(turn.session_id)
    pending_status = {
        'session_id': int(job.session_id),
        'turn_id': int(job.turn_id),
        'job_id': int(job.job_id),
        'attempts': claimed_attempt,
    }
    # A Socket.IO message queue can make even this status notification a
    # network wait. Publish it only after returning the read transaction used
    # to materialize the durable job context.
    release_clean_scoped_session(boundary='canon pending status emit')
    try:
        _emit_status(
            emit_turn_status,
            pending_status['session_id'],
            pending_status['turn_id'],
            'canon_pending',
            {
                'job_id': pending_status['job_id'],
                'attempts': pending_status['attempts'],
            },
        )
    except Exception as exc:  # pragma: no cover - transport defensive guard.
        logger.warning('Canon pending status emit failed: %s', str(exc))
        telemetry_event(
            'memory.canon_job.emit_failed',
            payload={'job_id': pending_status['job_id'], 'event': 'canon_pending'},
            severity='warning',
        )

    # Emitters are caller-owned and may use the scoped session themselves.
    # Reload the authoritative rows before extraction and reject a superseded
    # attempt before spending provider capacity.
    job = db.session.get(CanonJob, job_id)
    if (
        job is None
        or job.status != 'running'
        or int(job.attempts) != claimed_attempt
    ):
        return job
    turn = db.session.get(DmTurn, job.turn_id)
    campaign = db.session.get(Campaign, job.campaign_id)
    if not turn or not campaign:
        return _mark_job_failed(
            job_id,
            'Canon job turn or campaign is missing.',
            expected_attempt=claimed_attempt,
            emit_turn_status=emit_turn_status,
        )
    triggered_segments = _safe_triggered_segments(
        safe_json_loads(job.triggered_segments_json, [])
    )
    dm_output = turn.dm_output or ''

    try:
        pressure = _foreground_pressure_state()
        if pressure != 'none':
            telemetry_metric(
                'memory.canon_job.foreground_pressure_jobs_total',
                1,
                tags={'state': pressure},
            )
        canon_extract_started = time.perf_counter()
        patch, extractor_model = extract_canon_patch(
            turn=turn,
            campaign=campaign,
            dm_output=dm_output,
            speaking_player_name=job.speaking_player_name or '',
            triggered_segments=triggered_segments,
        )
        release_clean_scoped_session(boundary='canon apply')
        _record_phase(
            record_phase_timing,
            'canon_extraction',
            canon_extract_started,
            campaign_id=campaign_id,
            session_id=session_id,
        )

        # Provider I/O is complete and the retrieval session has been removed.
        # Serialize only the reload/validate/apply phase with foreground turns.
        with serialized_campaign_pack_progress(session_id):
            return _apply_canon_job_result(
                job_id,
                claimed_attempt=claimed_attempt,
                dm_output=dm_output,
                triggered_segments=triggered_segments,
                patch=patch,
                extractor_model=extractor_model,
                emit_turn_status=emit_turn_status,
                emit_segment_triggered=emit_segment_triggered,
                record_phase_timing=record_phase_timing,
            )
    except Exception as exc:
        logger.exception('Canon job failed')
        telemetry_event(
            'memory.canon_job_internal_error',
            payload={'job_id': job_id, 'error_type': type(exc).__name__},
            severity='error',
        )
        return _mark_job_failed(
            job_id,
            CANON_JOB_FAILED_MESSAGE,
            expected_attempt=claimed_attempt,
            emit_turn_status=emit_turn_status,
        )


def process_due_canon_jobs(
    *,
    limit: int = 10,
    emit_turn_status: TurnStatusEmitter | None = None,
    emit_segment_triggered: SegmentEmitter | None = None,
    record_phase_timing: PhaseRecorder | None = None,
) -> int:
    now = _job_timestamp()
    jobs = (
        CanonJob.query.filter(
            CanonJob.status.in_(CANON_JOB_RUNNABLE_STATUSES),
            CanonJob.next_run_at <= now,
        )
        .order_by(CanonJob.next_run_at.asc(), CanonJob.job_id.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    job_ids = [int(job.job_id) for job in jobs]
    processed = 0
    for job_id in job_ids:
        job_started = time.perf_counter()
        outcome = 'error'
        try:
            result = process_canon_job(
                job_id,
                emit_turn_status=emit_turn_status,
                emit_segment_triggered=emit_segment_triggered,
                record_phase_timing=record_phase_timing,
            )
            if result:
                processed += 1
                status = str(result.status or '')
                outcome = (
                    status
                    if status in CANON_JOB_TERMINAL_STATUSES
                    else 'nonterminal'
                )
            else:
                outcome = 'not_claimed'
        finally:
            telemetry_timing(
                'memory.canon_job.runtime_ms',
                float((time.perf_counter() - job_started) * 1000),
                tags={'outcome': outcome},
            )
    return processed


def _worker_emit_turn_status(
    socketio, session_id: int, turn_id: int | None, status: str, details=None
) -> None:
    socketio.emit(
        'turn_status',
        turn_status_payload(session_id, turn_id, status, details),
        room=str(session_id),
    )


def _worker_emit_segment_triggered(socketio, session_id: int, payload: dict) -> None:
    socketio.emit('segment_triggered', payload, room=str(session_id))


def _worker_record_phase_timing(
    phase: str,
    started_at: float,
    *,
    campaign_id: int,
    session_id: int,
) -> None:
    del campaign_id, session_id
    telemetry_timing(
        'socket.turn_phase_latency_ms',
        float((time.perf_counter() - started_at) * 1000),
        tags={'phase': phase},
    )


def _canon_job_worker_loop(
    app,
    socketio,
    state: CanonJobWorkerState,
    interval_seconds: int,
    batch_limit: int,
):
    try:
        interval = max(1, int(interval_seconds))
        limit = max(1, int(batch_limit))
        stale_reset_pending = True
        while not state.stop_event.is_set():
            state.wake_event.wait(timeout=interval)
            state.wake_event.clear()
            if state.stop_event.is_set():
                break

            processed = 0
            cycle_outcome = 'error'
            cycle_started = time.perf_counter()
            with app.app_context():
                if stale_reset_pending:
                    try:
                        reset_count = reset_stale_canon_jobs()
                        stale_reset_pending = False
                        telemetry_metric(
                            'memory.canon_job.stale_reset_jobs_total',
                            reset_count,
                        )
                        telemetry_metric(
                            'memory.canon_job.stale_reset_attempts_total',
                            1,
                            tags={'result': 'succeeded'},
                        )
                    except Exception as exc:  # pragma: no cover - startup resilience.
                        db.session.rollback()
                        logger.error('Canon job stale-lock reset failed: %s', str(exc))
                        telemetry_metric(
                            'memory.canon_job.stale_reset_attempts_total',
                            1,
                            tags={'result': 'failed'},
                        )
                        telemetry_event(
                            'memory.canon_job.stale_reset_failed',
                            payload={'error_type': type(exc).__name__},
                            severity='error',
                        )

                try:
                    _record_queue_observation(canon_job_queue_snapshot())
                    processed = process_due_canon_jobs(
                        limit=limit,
                        emit_turn_status=lambda session_id, turn_id, status, details=None: (
                            _worker_emit_turn_status(
                                socketio,
                                session_id,
                                turn_id,
                                status,
                                details,
                            )
                        ),
                        emit_segment_triggered=lambda session_id, payload: (
                            _worker_emit_segment_triggered(
                                socketio,
                                session_id,
                                payload,
                            )
                        ),
                        record_phase_timing=_worker_record_phase_timing,
                    )
                    if processed:
                        telemetry_metric(
                            'memory.canon_job.worker_processed_total', processed
                        )
                    cycle_outcome = (
                        'batch_full'
                        if processed >= limit
                        else ('processed' if processed else 'idle')
                    )
                except Exception as exc:  # pragma: no cover - long-running guard.
                    db.session.rollback()
                    logger.error('Canon job worker failed: %s', str(exc))
                    telemetry_event(
                        'memory.canon_job.worker_failed',
                        payload={'error_type': type(exc).__name__},
                        severity='error',
                    )
                finally:
                    telemetry_metric(
                        'memory.canon_job.worker_cycles_total',
                        1,
                        tags={'result': cycle_outcome},
                    )
                    telemetry_timing(
                        'memory.canon_job.worker_cycle_runtime_ms',
                        float((time.perf_counter() - cycle_started) * 1000),
                        tags={'result': cycle_outcome},
                    )

            # A full batch likely means more durable work remains. Re-wake this
            # same worker rather than creating one background task per job.
            if processed >= limit:
                state.wake()
            socketio.sleep(0)
    finally:
        # A stopped or unexpectedly terminated worker must not permanently
        # poison this process's extension state. The identity check prevents
        # an inherited/future worker from being removed by an older task.
        with _CANON_JOB_WORKER_START_LOCK:
            if app.extensions.get(CANON_JOB_WORKER_EXTENSION) is state:
                app.extensions.pop(CANON_JOB_WORKER_EXTENSION, None)


def _current_process_worker_state(app) -> CanonJobWorkerState | None:
    state = app.extensions.get(CANON_JOB_WORKER_EXTENSION)
    if not isinstance(state, CanonJobWorkerState):
        return None
    if state.owner_pid != os.getpid():
        return None
    return state


def wake_canon_job_worker(app) -> bool:
    state = _current_process_worker_state(app)
    if state is None or state.stop_event.is_set():
        return False
    state.wake()
    return True


def stop_canon_job_worker(app) -> bool:
    state = _current_process_worker_state(app)
    if state is None:
        return False
    state.stop()
    return True


def start_canon_job_worker(
    app,
    socketio,
    *,
    interval_seconds: int = DEFAULT_CANON_JOB_WORKER_INTERVAL_SECONDS,
    batch_limit: int = DEFAULT_CANON_JOB_WORKER_BATCH_LIMIT,
) -> bool:
    if app.config.get('TESTING') or app.config.get('AIDM_ENV') == 'test':
        return False
    worker_model = str(
        app.config.get('AIDM_SOCKETIO_WORKER_MODEL', 'single') or 'single'
    ).strip().lower().replace('-', '_')
    if worker_model != 'single':
        raise RuntimeError(
            'The process-local canon worker requires AIDM_SOCKETIO_WORKER_MODEL=single.'
        )
    with _CANON_JOB_WORKER_START_LOCK:
        if _current_process_worker_state(app) is not None:
            return False
        state = CanonJobWorkerState()
        app.extensions[CANON_JOB_WORKER_EXTENSION] = state
        try:
            state.task = socketio.start_background_task(
                _canon_job_worker_loop,
                app,
                socketio,
                state,
                interval_seconds,
                batch_limit,
            )
        except Exception:
            if app.extensions.get(CANON_JOB_WORKER_EXTENSION) is state:
                app.extensions.pop(CANON_JOB_WORKER_EXTENSION, None)
            raise
    state.wake()
    return True


def canon_job_status_counts(campaign_id: int) -> dict[str, int]:
    rows = (
        db.session.query(CanonJob.status, func.count(CanonJob.job_id))
        .filter(CanonJob.campaign_id == campaign_id)
        .group_by(CanonJob.status)
        .all()
    )
    return {str(status): int(count) for status, count in rows}
