"""Durable canon extraction job queue."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import timedelta

from flask import current_app
from sqlalchemy import func, or_, update

from aidm_server.database import db
from aidm_server.emergent_memory import (
    append_session_memory,
    apply_canon_patch,
    extract_canon_patch,
    refresh_session_projection,
    validate_canon_patch,
)
from aidm_server.models import CanonJob, Campaign, CampaignSegment, DmTurn, safe_json_dumps, safe_json_loads
from aidm_server.segment_triggers import evaluate_segment_trigger, parse_trigger_spec
from aidm_server.socket_contracts import segment_triggered_payload
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.time_utils import utc_now
from aidm_server.turn_events import CANON_APPLIED_EVENT, SEGMENT_TRIGGERED_EVENT, record_turn_event


logger = logging.getLogger(__name__)

CANON_JOB_RUNNABLE_STATUSES = {'queued'}
CANON_JOB_TERMINAL_STATUSES = {'succeeded', 'failed', 'cancelled'}
DEFAULT_CANON_JOB_MAX_ATTEMPTS = 1
DEFAULT_CANON_JOB_RETRY_DELAY_SECONDS = 30
DEFAULT_CANON_JOB_STALE_LOCK_SECONDS = 15 * 60

TurnStatusEmitter = Callable[[int, int | None, str, dict | None], None]
SegmentEmitter = Callable[[int, dict], None]
PhaseRecorder = Callable[..., None]


def _safe_triggered_segments(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _job_timestamp():
    return utc_now().replace(tzinfo=None)


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
    db.session.add(job)
    db.session.flush()
    _set_turn_canon_metadata(turn, status='queued', job=job)
    return job


def retry_canon_job(job_id: int) -> CanonJob | None:
    job = db.session.get(CanonJob, job_id)
    if not job:
        return None
    job.status = 'queued'
    job.error_text = None
    job.locked_at = None
    job.completed_at = None
    job.next_run_at = _job_timestamp()
    job.updated_at = _job_timestamp()
    _set_turn_canon_metadata(job.turn, status='queued', job=job)
    db.session.commit()
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
        db.session.commit()
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

    db.session.commit()
    job = db.session.get(CanonJob, job_id)
    if not job:
        return None
    _set_turn_canon_metadata(job.turn, status='running', job=job)
    db.session.commit()
    return job


def _segment_state_payload(session_id: int, campaign: Campaign) -> tuple[dict, dict]:
    session_state = refresh_session_projection(session_id, campaign)
    session_state_payload = {
        'current_location': session_state.current_location,
        'current_quest': session_state.current_quest,
    }
    campaign_state = {
        'location': campaign.location,
        'current_quest': campaign.current_quest,
    }
    return session_state_payload, campaign_state


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


def _evaluate_state_segments_after_turn(turn: DmTurn, campaign: Campaign) -> list[dict]:
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

        return _activate_state_segments(turn, segments_to_activate)
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
    emit_turn_status: TurnStatusEmitter | None = None,
) -> CanonJob | None:
    db.session.rollback()
    job = db.session.get(CanonJob, job_id)
    if not job:
        return None
    now = _job_timestamp()
    job.status = 'failed'
    job.error_text = error
    job.completed_at = now
    job.updated_at = now
    job.locked_at = None
    _set_turn_canon_metadata(job.turn, status='failed', job=job, error=error)
    db.session.commit()
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
    turn = db.session.get(DmTurn, job.turn_id)
    campaign = db.session.get(Campaign, job.campaign_id)
    if not turn or not campaign:
        return _mark_job_failed(job_id, 'Canon job turn or campaign is missing.', emit_turn_status=emit_turn_status)

    _emit_status(
        emit_turn_status,
        job.session_id,
        job.turn_id,
        'canon_pending',
        {'job_id': job.job_id, 'attempts': job.attempts},
    )
    triggered_segments = _safe_triggered_segments(safe_json_loads(job.triggered_segments_json, []))
    dm_output = turn.dm_output or ''

    try:
        if dm_output:
            append_session_memory(turn)

        canon_extract_started = time.perf_counter()
        patch, extractor_model = extract_canon_patch(
            turn=turn,
            campaign=campaign,
            dm_output=dm_output,
            speaking_player_name=job.speaking_player_name or '',
            triggered_segments=triggered_segments,
        )
        _record_phase(
            record_phase_timing,
            'canon_extraction',
            canon_extract_started,
            campaign_id=campaign.campaign_id,
            session_id=turn.session_id,
        )

        canon_validation_started = time.perf_counter()
        validated_patch, rejections = validate_canon_patch(turn=turn, campaign=campaign, patch=patch)
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
                payload={'campaign_id': campaign.campaign_id, 'turn_id': turn.turn_id, 'rejections': rejections},
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
        _emit_status(
            emit_turn_status,
            turn.session_id,
            turn.turn_id,
            'canon_applied',
            {
                'extractor_model': extractor_model,
                'rejection_count': len(rejections),
                'job_id': job.job_id,
                'player_id': turn.player_id,
                'inventory_changes_applied': applied_summary.get('inventory_changes_applied', []),
                'character_state_changes_applied': applied_summary.get('character_state_changes_applied', []),
            },
        )

        projection_started = time.perf_counter()
        refresh_session_projection(session_id=turn.session_id, campaign=campaign, triggered_segments=triggered_segments)
        post_turn_segments = _evaluate_state_segments_after_turn(turn, campaign)
        if emit_segment_triggered:
            for segment_payload in post_turn_segments:
                emit_segment_triggered(turn.session_id, segment_payload)
        if post_turn_segments:
            segment_patch = _segment_thread_patch(post_turn_segments)
            validated_segment_patch, segment_rejections = validate_canon_patch(
                turn=turn,
                campaign=campaign,
                patch=segment_patch,
            )
            if segment_rejections:
                telemetry_metric('memory.validation.rejections_total', len(segment_rejections))
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
        db.session.commit()
        telemetry_metric('memory.canon_job.succeeded_total', 1)
        return job
    except Exception as exc:
        logger.error('Canon job failed: %s', str(exc))
        return _mark_job_failed(job_id, str(exc), emit_turn_status=emit_turn_status)


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
    processed = 0
    for job in jobs:
        if process_canon_job(
            job.job_id,
            emit_turn_status=emit_turn_status,
            emit_segment_triggered=emit_segment_triggered,
            record_phase_timing=record_phase_timing,
        ):
            processed += 1
    return processed


def _canon_job_worker_loop(app, socketio, interval_seconds: int):
    with app.app_context():
        reset_stale_canon_jobs()

    while True:
        with app.app_context():
            try:
                processed = process_due_canon_jobs(limit=3)
                if processed:
                    telemetry_metric('memory.canon_job.worker_processed_total', processed)
            except Exception as exc:  # pragma: no cover - defensive long-running worker guard.
                logger.error('Canon job worker failed: %s', str(exc))
                telemetry_event(
                    'memory.canon_job.worker_failed',
                    payload={'error': str(exc)},
                    severity='error',
                )
        socketio.sleep(max(1, int(interval_seconds)))


def start_canon_job_worker(app, socketio, *, interval_seconds: int = 5) -> bool:
    if app.config.get('TESTING') or app.config.get('AIDM_ENV') == 'test':
        return False
    if app.extensions.get('aidm_canon_job_worker_started'):
        return False
    app.extensions['aidm_canon_job_worker_started'] = True
    socketio.start_background_task(_canon_job_worker_loop, app, socketio, interval_seconds)
    return True


def canon_job_status_counts(campaign_id: int) -> dict[str, int]:
    rows = (
        db.session.query(CanonJob.status, func.count(CanonJob.job_id))
        .filter(CanonJob.campaign_id == campaign_id)
        .group_by(CanonJob.status)
        .all()
    )
    return {str(status): int(count) for status, count in rows}
