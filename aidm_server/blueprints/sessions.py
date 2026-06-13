from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from aidm_server.action_intent import ACTION_ID_RE
from aidm_server.database import db
from aidm_server.errors import error_response
from aidm_server.llm import query_gpt
from aidm_server.models import (
    Session,
    SessionLogEntry,
    SessionState,
    TurnEvent,
    get_or_create_session_state,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.response_dtos import isoformat, session_payload, session_state_payload, turn_event_payload
from aidm_server.services.session_lifecycle import (
    archive_session_record,
    delete_session_record,
    metadata_cleaned_snapshot,
    restore_session_record,
)
from aidm_server.services.campaign_pack_progress import (
    CampaignPackProgressError,
    PROGRESS_CHANGED_EVENT,
    campaign_pack_progress_payload,
    control_campaign_pack_progress,
)
from aidm_server.services.session_import import SessionImportError, import_session_export
from aidm_server.services.workspace import list_campaign_session_payloads
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.time_utils import utc_now
from aidm_server.turn_events import SESSION_ENDED_EVENT, SESSION_RECAP_EVENT, SESSION_STARTED_EVENT, record_turn_event
from aidm_server.validation import coerce_int, missing_fields, optional_text, parse_json_body, positive_int, required_text
from aidm_server.workspace_access import (
    current_account_id,
    current_account_is_workspace_admin,
    current_workspace_id,
    get_campaign as workspace_campaign,
    get_session as workspace_session,
)


logger = logging.getLogger(__name__)
sessions_bp = Blueprint('sessions', __name__)
RECAP_RECENT_ENTRY_LIMIT = 80
RECAP_SOURCE_CHAR_BUDGET = 12_000
SESSION_IDEMPOTENCY_KEY_MAX_LENGTH = 80


def _state_snapshot_dict(raw_snapshot) -> dict:
    snapshot = safe_json_loads(raw_snapshot, {})
    return snapshot if isinstance(snapshot, dict) else {}


def _merge_state_snapshot(raw_snapshot, updates: dict) -> dict:
    snapshot = _state_snapshot_dict(raw_snapshot)
    snapshot.update(updates)
    return snapshot


def _metadata_cleaned_snapshot(raw_snapshot) -> dict:
    return metadata_cleaned_snapshot(raw_snapshot)


def _client_session_id(payload: dict | None) -> tuple[str | None, str | None]:
    if not isinstance(payload, dict):
        return None, None
    raw_value = payload.get('client_session_id') or payload.get('idempotency_key')
    if raw_value in (None, ''):
        return None, None
    client_session_id = str(raw_value).strip()
    if not client_session_id:
        return None, None
    if len(client_session_id) > SESSION_IDEMPOTENCY_KEY_MAX_LENGTH:
        return None, f'client_session_id must be {SESSION_IDEMPOTENCY_KEY_MAX_LENGTH} characters or fewer.'
    if not ACTION_ID_RE.fullmatch(client_session_id):
        return None, 'client_session_id contains unsupported characters.'
    return client_session_id, None


def _find_idempotent_session(campaign_id: int, client_session_id: str) -> Session | None:
    direct_match = Session.query.filter_by(campaign_id=campaign_id, client_session_id=client_session_id).first()
    if direct_match:
        return direct_match

    for session_obj in Session.query.filter_by(campaign_id=campaign_id, client_session_id=None).all():
        snapshot = _state_snapshot_dict(session_obj.state_snapshot)
        if snapshot.get('client_session_id') == client_session_id:
            return session_obj
    return None


def _stale_update_error(payload: dict, current_updated_at) -> tuple[dict, int] | None:
    expected = payload.get('expected_updated_at')
    if expected in (None, ''):
        return None
    actual = isoformat(current_updated_at)
    if str(expected) == str(actual):
        return None
    return error_response(
        'stale_update',
        'Session was updated by another request. Refresh before saving changes.',
        409,
        {'expected_updated_at': expected, 'actual_updated_at': actual},
    )


def _include_archived() -> bool:
    return str(request.args.get('include_archived', '')).strip().lower() in {'1', 'true', 'yes', 'on'}


def _campaign_pack_operator_view() -> bool:
    return current_account_id() is None or current_account_is_workspace_admin()


def _campaign_pack_progress_actor() -> str:
    account_id = current_account_id()
    if account_id is None:
        return 'operator'
    role = 'admin' if current_account_is_workspace_admin() else 'player'
    return f'account:{account_id}:{role}'


def _bounded_session_recap_source(session_id: int, session_state: SessionState | None) -> str:
    entries = (
        SessionLogEntry.query.filter_by(session_id=session_id)
        .order_by(SessionLogEntry.timestamp.desc(), SessionLogEntry.id.desc())
        .limit(RECAP_RECENT_ENTRY_LIMIT)
        .all()
    )
    recent_log = "\n".join(entry.message for entry in reversed(entries))
    if len(recent_log) > RECAP_SOURCE_CHAR_BUDGET:
        recent_log = recent_log[-RECAP_SOURCE_CHAR_BUDGET:]

    parts = []
    rolling_summary = (session_state.rolling_summary if session_state else '') or ''
    if rolling_summary:
        parts.append(f'Existing rolling summary:\n{rolling_summary[-4000:]}')
    if recent_log:
        parts.append(
            f'Recent session log (last {len(entries)} entries, bounded to {RECAP_SOURCE_CHAR_BUDGET} chars):\n'
            f'{recent_log}'
        )
    return "\n\n".join(parts) or 'No session log entries have been recorded for this session.'


@sessions_bp.route('/start', methods=['POST'])
def start_new_session():
    telemetry_metric('sessions.start.requests_total', 1)
    payload = parse_json_body(request)
    if payload is None:
        telemetry_event('sessions.start.validation_error', payload={'field': 'body'}, severity='warning')
        return error_response('validation_error', 'Expected JSON request body.', 400)

    required = missing_fields(payload, ['campaign_id'])
    if required:
        telemetry_event('sessions.start.validation_error', payload={'missing_fields': required}, severity='warning')
        return error_response('validation_error', 'Missing required fields.', 400, {'missing_fields': required})

    client_session_id, client_session_id_error = _client_session_id(payload)
    if client_session_id_error:
        telemetry_event('sessions.start.validation_error', payload={'field': 'client_session_id'}, severity='warning')
        return error_response('validation_error', client_session_id_error, 400)

    campaign_id, campaign_id_error = positive_int(payload.get('campaign_id'), field='campaign_id', required=True)
    if campaign_id_error:
        telemetry_event('sessions.start.validation_error', payload={'field': 'campaign_id'}, severity='warning')
        return error_response('validation_error', campaign_id_error, 400)

    campaign = workspace_campaign(campaign_id)
    if not campaign:
        telemetry_event('sessions.start.campaign_not_found', payload={'campaign_id': campaign_id}, severity='warning')
        return error_response('campaign_not_found', 'Campaign not found.', 404)

    if client_session_id:
        existing_session = _find_idempotent_session(campaign.campaign_id, client_session_id)
        if existing_session:
            telemetry_metric('sessions.start.idempotent_replay_total', 1)
            return jsonify({'session_id': existing_session.session_id, 'idempotent_replay': True}), 200

    try:
        name = None
        if payload.get('name') is not None:
            name, name_error = optional_text(payload.get('name'), max_length=80, field='name', default=None)
            if name_error:
                return error_response('validation_error', name_error, 400)
            if not name:
                name = None

        snapshot = {'client_session_id': client_session_id} if client_session_id else None
        new_session = Session(
            campaign_id=campaign.campaign_id,
            name=name,
            client_session_id=client_session_id,
            state_snapshot=(safe_json_dumps(snapshot, {}) if snapshot else None),
        )
        db.session.add(new_session)
        db.session.flush()

        get_or_create_session_state(new_session.session_id, campaign)
        record_turn_event(
            session_id=new_session.session_id,
            campaign_id=campaign.campaign_id,
            event_type=SESSION_STARTED_EVENT,
            payload={
                'message': '**Welcome to the table. Choose your opening move when you are ready.**',
                'metadata': {
                    'kind': 'session_welcome',
                    'client_session_id': client_session_id,
                },
            },
        )
        db.session.commit()
        telemetry_metric('sessions.start.success_total', 1)
        return jsonify({'session_id': new_session.session_id, 'idempotent_replay': False}), 201
    except IntegrityError:
        db.session.rollback()
        if client_session_id:
            existing_session = _find_idempotent_session(campaign.campaign_id, client_session_id)
            if existing_session:
                telemetry_metric('sessions.start.idempotent_race_replay_total', 1)
                return jsonify({'session_id': existing_session.session_id, 'idempotent_replay': True}), 200
        logger.error('Failed to start session because idempotency constraint was violated.')
        telemetry_event('sessions.start.idempotency_conflict_failed', payload={'campaign_id': campaign_id}, severity='error')
        return error_response('session_start_failed', 'Failed to start session.', 400)
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to start session: %s', str(exc))
        telemetry_event('sessions.start.failed', payload={'error': str(exc)}, severity='error')
        return error_response('session_start_failed', 'Failed to start session.', 400)


@sessions_bp.route('/<int:session_id>/end', methods=['POST'])
def end_game_session(session_id):
    telemetry_metric('sessions.end.requests_total', 1)
    session_obj = workspace_session(session_id)
    if not session_obj:
        telemetry_event('sessions.end.session_not_found', payload={'session_id': session_id}, severity='warning')
        return error_response('session_not_found', 'Session not found.', 404)

    session_state = get_or_create_session_state(session_id, session_obj.campaign)
    recap_source = _bounded_session_recap_source(session_id, session_state)
    recap_prompt = (
        'Please provide a concise summary of this D&D session, highlighting key events, '
        'important decisions, and significant character developments. Use the existing '
        'rolling summary for continuity and the bounded recent log for latest events:\n\n'
        f'{recap_source}'
    )

    recap = query_gpt(prompt=recap_prompt, system_message='You are a D&D session summarizer.')

    try:
        ended_at = utc_now()
        snapshot = _merge_state_snapshot(
            session_obj.state_snapshot,
            {
                'recap': recap,
                'ended_at': ended_at.isoformat(),
            },
        )
        session_obj.state_snapshot = safe_json_dumps(snapshot, {})

        session_state.rolling_summary = recap
        session_state.current_location = (session_state.current_location or session_obj.campaign.location)
        session_state.current_quest = (session_state.current_quest or session_obj.campaign.current_quest)
        session_state.updated_at = ended_at
        record_turn_event(
            session_id=session_id,
            campaign_id=session_obj.campaign_id,
            event_type=SESSION_ENDED_EVENT,
            payload={
                'message': '**Session ended.**',
                'metadata': {
                    'kind': 'session_ended',
                    'ended_at': ended_at.isoformat(),
                },
            },
        )
        record_turn_event(
            session_id=session_id,
            campaign_id=session_obj.campaign_id,
            event_type=SESSION_RECAP_EVENT,
            payload={
                'recap': recap,
                'metadata': {
                    'kind': 'session_recap',
                    'ended_at': ended_at.isoformat(),
                },
            },
        )

        db.session.commit()
        telemetry_metric('sessions.end.success_total', 1)
        return jsonify({'recap': recap})
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to end session: %s', str(exc))
        telemetry_event(
            'sessions.end.failed',
            payload={'session_id': session_id, 'error': str(exc)},
            severity='error',
        )
        return error_response('session_end_failed', 'Failed to end session.', 400)


@sessions_bp.route('/campaigns/<int:campaign_id>/sessions', methods=['GET'])
def list_campaign_sessions(campaign_id):
    telemetry_metric('sessions.list.requests_total', 1)
    campaign = workspace_campaign(campaign_id)
    if not campaign:
        telemetry_event('sessions.list.campaign_not_found', payload={'campaign_id': campaign_id}, severity='warning')
        return error_response('campaign_not_found', 'Campaign not found.', 404)

    limit = coerce_int(request.args.get('limit'))
    return jsonify(
        list_campaign_session_payloads(
            campaign_id,
            include_archived=_include_archived(),
            limit=(max(1, min(500, limit)) if limit is not None else None),
            include_hidden_state=_campaign_pack_operator_view(),
        )
    )


@sessions_bp.route('/import', methods=['POST'])
def import_session():
    telemetry_metric('sessions.import.requests_total', 1)
    payload = parse_json_body(request)
    if payload is None:
        telemetry_event('sessions.import.validation_error', payload={'field': 'body'}, severity='warning')
        return error_response('validation_error', 'Expected JSON request body.', 400)

    try:
        result = import_session_export(
            payload,
            workspace_id=current_workspace_id(),
            include_hidden_state=_campaign_pack_operator_view(),
        )
        db.session.commit()
        telemetry_metric('sessions.import.success_total', 1)
        return jsonify(result.payload), 201
    except SessionImportError as exc:
        db.session.rollback()
        telemetry_event(
            'sessions.import.validation_error',
            payload={'error_code': exc.error_code, 'message': str(exc)},
            severity='warning',
        )
        return error_response(exc.error_code, str(exc), exc.status_code)
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to import session: %s', str(exc))
        telemetry_event('sessions.import.failed', payload={'error': str(exc)}, severity='error')
        return error_response('session_import_failed', 'Failed to import session.', 400)


@sessions_bp.route('/<int:session_id>', methods=['PATCH'])
def update_session(session_id):
    telemetry_metric('sessions.update.requests_total', 1)
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    session_obj = workspace_session(session_id)
    if not session_obj:
        telemetry_event('sessions.update.session_not_found', payload={'session_id': session_id}, severity='warning')
        return error_response('session_not_found', 'Session not found.', 404)

    stale_response = _stale_update_error(payload, session_obj.updated_at)
    if stale_response:
        return stale_response

    raw_name = payload.get('name', payload.get('title'))
    name, name_error = required_text(raw_name, max_length=80, field='Session name')
    if name_error:
        return error_response('validation_error', name_error, 400)

    try:
        session_obj.name = name
        session_obj.updated_at = utc_now()
        session_obj.state_snapshot = safe_json_dumps(_metadata_cleaned_snapshot(session_obj.state_snapshot), {})
        db.session.commit()
        telemetry_metric('sessions.update.success_total', 1)
        return jsonify(session_payload(session_obj, include_hidden_state=_campaign_pack_operator_view()))
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to update session: %s', str(exc))
        telemetry_event('sessions.update.failed', payload={'session_id': session_id, 'error': str(exc)}, severity='error')
        return error_response('session_update_failed', 'Failed to update session.', 400)


@sessions_bp.route('/<int:session_id>/archive', methods=['POST'])
def archive_session(session_id):
    telemetry_metric('sessions.archive.requests_total', 1)
    session_obj = workspace_session(session_id)
    if not session_obj:
        telemetry_event('sessions.archive.session_not_found', payload={'session_id': session_id}, severity='warning')
        return error_response('session_not_found', 'Session not found.', 404)

    try:
        payload = archive_session_record(session_obj, include_hidden_state=_campaign_pack_operator_view())
        db.session.commit()
        telemetry_metric('sessions.archive.success_total', 1)
        return jsonify({'archived': True, 'session': payload})
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to archive session: %s', str(exc))
        telemetry_event('sessions.archive.failed', payload={'session_id': session_id, 'error': str(exc)}, severity='error')
        return error_response('session_archive_failed', 'Failed to archive session.', 400)


@sessions_bp.route('/<int:session_id>/restore', methods=['POST'])
def restore_session(session_id):
    telemetry_metric('sessions.restore.requests_total', 1)
    session_obj = workspace_session(session_id)
    if not session_obj:
        telemetry_event('sessions.restore.session_not_found', payload={'session_id': session_id}, severity='warning')
        return error_response('session_not_found', 'Session not found.', 404)

    try:
        payload = restore_session_record(session_obj, include_hidden_state=_campaign_pack_operator_view())
        db.session.commit()
        telemetry_metric('sessions.restore.success_total', 1)
        return jsonify({'restored': True, 'session': payload})
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to restore session: %s', str(exc))
        telemetry_event('sessions.restore.failed', payload={'session_id': session_id, 'error': str(exc)}, severity='error')
        return error_response('session_restore_failed', 'Failed to restore session.', 400)


@sessions_bp.route('/<int:session_id>', methods=['DELETE'])
def delete_session(session_id):
    telemetry_metric('sessions.delete.requests_total', 1)
    session_obj = workspace_session(session_id)
    if not session_obj:
        telemetry_event('sessions.delete.session_not_found', payload={'session_id': session_id}, severity='warning')
        return error_response('session_not_found', 'Session not found.', 404)

    hard_delete = str(request.args.get('hard', '')).strip().lower() in {'1', 'true', 'yes', 'on'}
    try:
        result = delete_session_record(
            session_obj,
            hard_delete=hard_delete,
            include_hidden_state=_campaign_pack_operator_view(),
        )
        db.session.commit()
        telemetry_metric(
            'sessions.delete.success_total' if result.hard_deleted else 'sessions.delete.archived_total',
            1,
        )
        return jsonify(result.payload)
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to delete session: %s', str(exc))
        telemetry_event('sessions.delete.failed', payload={'session_id': session_id, 'error': str(exc)}, severity='error')
        return error_response('session_delete_failed', 'Failed to delete session.', 400)


@sessions_bp.route('/<int:session_id>/log', methods=['GET'])
def get_session_log(session_id):
    telemetry_metric('sessions.log.requests_total', 1)
    session_obj = workspace_session(session_id)
    if not session_obj:
        telemetry_event('sessions.log.session_not_found', payload={'session_id': session_id}, severity='warning')
        return error_response('session_not_found', 'Session not found.', 404)

    limit = request.args.get('limit', default=200, type=int)
    limit = max(1, min(limit, 500))
    before_id = coerce_int(request.args.get('before_id'))

    query = SessionLogEntry.query.filter_by(session_id=session_id)
    if before_id is not None:
        query = query.filter(SessionLogEntry.id < before_id)

    entries = query.order_by(SessionLogEntry.timestamp.desc(), SessionLogEntry.id.desc()).limit(limit + 1).all()
    has_more = len(entries) > limit
    if has_more:
        entries = entries[:limit]
    entries = list(reversed(entries))
    return jsonify(
        {
            'session_id': session_id,
            'limit': limit,
            'has_more': has_more,
            'next_cursor': entries[0].id if has_more and entries else None,
            'entries': [
                {
                    'id': entry.id,
                    'message': entry.message,
                    'entry_type': entry.entry_type,
                    'metadata': safe_json_loads(entry.metadata_json, {}),
                    'timestamp': entry.timestamp.isoformat() if entry.timestamp else None,
                }
                for entry in entries
            ],
        }
    )


@sessions_bp.route('/<int:session_id>/events', methods=['GET'])
def get_session_events(session_id):
    telemetry_metric('sessions.events.requests_total', 1)
    session_obj = workspace_session(session_id)
    if not session_obj:
        telemetry_event('sessions.events.session_not_found', payload={'session_id': session_id}, severity='warning')
        return error_response('session_not_found', 'Session not found.', 404)

    limit = request.args.get('limit', default=500, type=int)
    limit = max(1, min(limit, 1000))
    before_id = coerce_int(request.args.get('before_id'))

    query = TurnEvent.query.filter_by(session_id=session_id)
    if not _campaign_pack_operator_view():
        query = query.filter(TurnEvent.event_type != PROGRESS_CHANGED_EVENT)
    if before_id is not None:
        query = query.filter(TurnEvent.event_id < before_id)

    events = query.order_by(TurnEvent.created_at.desc(), TurnEvent.event_id.desc()).limit(limit + 1).all()
    has_more = len(events) > limit
    if has_more:
        events = events[:limit]
    events = list(reversed(events))
    return jsonify(
        {
            'session_id': session_id,
            'limit': limit,
            'has_more': has_more,
            'next_cursor': events[0].event_id if has_more and events else None,
            'events': [turn_event_payload(event) for event in events],
        }
    )


@sessions_bp.route('/<int:session_id>/state', methods=['GET'])
def get_session_state(session_id):
    telemetry_metric('sessions.state.requests_total', 1)
    session_obj = workspace_session(session_id)
    if not session_obj:
        telemetry_event('sessions.state.session_not_found', payload={'session_id': session_id}, severity='warning')
        return error_response('session_not_found', 'Session not found.', 404)

    session_state = SessionState.query.filter_by(session_id=session_id).first()
    return jsonify(
        session_state_payload(
            session_obj,
            session_state,
            include_hidden_state=_campaign_pack_operator_view(),
        )
    )


@sessions_bp.route('/<int:session_id>/campaign-pack/progress', methods=['GET'])
def get_session_campaign_pack_progress(session_id):
    session_obj = workspace_session(session_id)
    if not session_obj:
        return error_response('session_not_found', 'Session not found.', 404)
    try:
        return jsonify(campaign_pack_progress_payload(session_id=session_id, include_hidden=_campaign_pack_operator_view()))
    except CampaignPackProgressError as exc:
        return error_response(exc.error_code, str(exc), exc.status_code)


@sessions_bp.route('/<int:session_id>/campaign-pack/progress', methods=['POST'])
def update_session_campaign_pack_progress(session_id):
    session_obj = workspace_session(session_id)
    if not session_obj:
        return error_response('session_not_found', 'Session not found.', 404)
    if not _campaign_pack_operator_view():
        return error_response(
            'forbidden',
            'Only workspace admins can control campaign pack progress.',
            403,
        )
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    action = str(payload.get('action') or '').strip()
    checkpoint_id = payload.get('checkpointId') or payload.get('checkpoint_id')
    checkpoint_id = str(checkpoint_id).strip() if checkpoint_id not in (None, '') else None
    reason = payload.get('reason')
    reason = str(reason).strip() if reason not in (None, '') else None
    raw_expected_revision = payload.get('expectedRevision') if 'expectedRevision' in payload else payload.get('expected_revision')
    expected_revision = coerce_int(raw_expected_revision)
    try:
        result = control_campaign_pack_progress(
            session_id=session_id,
            action=action,
            checkpoint_id=checkpoint_id,
            reason=reason,
            actor=_campaign_pack_progress_actor(),
            expected_revision=expected_revision,
        )
        session_state = SessionState.query.filter_by(session_id=session_id).first()
        db.session.commit()
        return jsonify(
            {
                'changed': result.changed,
                'active_checkpoint_id': result.active_checkpoint_id,
                'completed_checkpoint_ids': result.completed_checkpoint_ids,
                'skipped_checkpoint_ids': result.skipped_checkpoint_ids,
                'failed_checkpoint_ids': result.failed_checkpoint_ids or [],
                'reason': result.reason,
                'progress_revision': result.progress_revision,
                'event_id': result.event_id,
                'state': session_state_payload(
                    session_obj,
                    session_state,
                    include_hidden_state=_campaign_pack_operator_view(),
                ),
            }
        )
    except CampaignPackProgressError as exc:
        db.session.rollback()
        return error_response(exc.error_code, str(exc), exc.status_code)
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to update campaign pack progress: %s', str(exc))
        return error_response('campaign_pack_progress_failed', 'Failed to update campaign pack progress.', 400)
