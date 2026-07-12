from __future__ import annotations

from dataclasses import dataclass
import logging

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import IntegrityError

from aidm_server.action_intent import ACTION_ID_RE
from aidm_server.capabilities import current_actor_has_capability
from aidm_server.database import db, release_clean_scoped_session
from aidm_server.errors import error_response
from aidm_server.llm import query_gpt
from aidm_server.models import (
    Campaign,
    DmTurn,
    Player,
    Session,
    SessionLogEntry,
    SessionState,
    TurnEvent,
    get_or_create_session_state,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.response_dtos import (
    account_player_ids_by_campaign,
    campaign_payload,
    isoformat,
    party_player_payload,
    session_log_entry_payload,
    session_payload,
    session_state_payload,
    turn_event_payload,
)
from aidm_server.provider_priority import foreground_provider_reservation
from aidm_server.services.session_lifecycle import (
    SessionLifecycleTargetNotFoundError,
    archive_session_record,
    commit_session_lifecycle_operation,
    delete_session_record,
    restore_session_record,
)
from aidm_server.services.session_recovery import (
    OPERATOR_NOTE_MAX_LENGTH,
    TURN_RECOVERY_RESOLUTIONS,
    SessionRecoveryConflictError,
    resolve_turn_recovery_gate,
)
from aidm_server.services.campaign_pack_progress import (
    CampaignPackProgressError,
    PROGRESS_CHANGED_EVENT,
    campaign_pack_progress_payload,
    control_campaign_pack_progress,
)
from aidm_server.services.campaign_commentary import campaign_pack_commentary_payload, session_recap_payload
from aidm_server.services.chronicle_export import chronicle_html_response, export_session_chronicle_html
from aidm_server.services.content_settings import (
    apply_session_content_settings,
    content_settings_payload,
    session_content_settings,
)
from aidm_server.services.session_import import SessionImportError, import_session_export
from aidm_server.services.workspace import list_campaign_session_payloads
from aidm_server.socket_contracts import session_recovery_resolved_payload
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.time_utils import utc_now
from aidm_server.turn_coordinator import session_turn_coordinator
from aidm_server.turn_events import SESSION_ENDED_EVENT, SESSION_RECAP_EVENT, SESSION_STARTED_EVENT, record_turn_event
from aidm_server.validation import coerce_int, missing_fields, optional_text, parse_json_body, positive_int, required_text
from aidm_server.workspace_access import (
    current_account_id,
    current_workspace_id,
    get_campaign as workspace_campaign,
    get_session as workspace_session,
)


logger = logging.getLogger(__name__)
sessions_bp = Blueprint('sessions', __name__)
RECAP_RECENT_ENTRY_LIMIT = 80
RECAP_SOURCE_CHAR_BUDGET = 12_000
SESSION_IDEMPOTENCY_KEY_MAX_LENGTH = 80
SESSION_EXPORT_MAX_LOG_ENTRIES = 1000
SESSION_EXPORT_MAX_TURN_EVENTS = 1000


@dataclass(frozen=True)
class _SessionRecapContext:
    session_id: int
    campaign_id: int
    session_status: str | None
    state_snapshot: str | None
    session_updated_at: object
    session_deleted_at: object
    campaign_workspace_id: str | None
    campaign_location: str | None
    campaign_current_quest: str | None
    campaign_updated_at: object
    session_state_id: int | None
    rolling_summary: str | None
    current_location: str | None
    current_quest: str | None
    active_segments: str | None
    memory_snippets: str | None
    session_state_updated_at: object
    recap_source: str


def _state_snapshot_dict(raw_snapshot) -> dict:
    snapshot = safe_json_loads(raw_snapshot, {})
    return snapshot if isinstance(snapshot, dict) else {}


def _merge_state_snapshot(raw_snapshot, updates: dict) -> dict:
    snapshot = _state_snapshot_dict(raw_snapshot)
    snapshot.update(updates)
    return snapshot


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
    return current_actor_has_capability('dm_runtime_control')


def _campaign_pack_progress_actor() -> str:
    if current_actor_has_capability('local_operator_only'):
        return 'operator'
    account_id = current_account_id()
    if account_id is not None:
        return f'account:{account_id}:admin'
    if current_actor_has_capability('dm_runtime_control'):
        return 'operator'
    return 'unknown-actor'


def _turn_player_ids_for_log_entries(entries: list[SessionLogEntry]) -> dict[int, int | None]:
    turn_ids: set[int] = set()
    for entry in entries:
        metadata = safe_json_loads(entry.metadata_json, {})
        if not isinstance(metadata, dict):
            continue
        turn_id = coerce_int(metadata.get('turn_id'))
        if turn_id is not None:
            turn_ids.add(turn_id)
    if not turn_ids:
        return {}
    return {
        int(turn_id): (int(player_id) if player_id is not None else None)
        for turn_id, player_id in (
            db.session.query(DmTurn.turn_id, DmTurn.player_id)
            .filter(DmTurn.turn_id.in_(turn_ids))
            .all()
        )
    }


def _session_export_log_entries(
    session_id: int,
    *,
    include_private: bool,
    private_player_ids: frozenset[int],
) -> tuple[list[dict], bool]:
    rows = (
        SessionLogEntry.query.filter_by(session_id=session_id)
        .order_by(SessionLogEntry.timestamp.asc(), SessionLogEntry.id.asc())
        .limit(SESSION_EXPORT_MAX_LOG_ENTRIES + 1)
        .all()
    )
    truncated = len(rows) > SESSION_EXPORT_MAX_LOG_ENTRIES
    if truncated:
        rows = rows[:SESSION_EXPORT_MAX_LOG_ENTRIES]
    turn_player_ids = _turn_player_ids_for_log_entries(rows)
    return (
        [
            session_log_entry_payload(
                entry,
                include_private=include_private,
                private_player_ids=private_player_ids,
                turn_player_ids=turn_player_ids,
            )
            for entry in rows
        ],
        truncated,
    )


def _session_export_turn_events(
    session_id: int,
    *,
    include_private: bool,
    private_player_ids: frozenset[int],
) -> tuple[list[dict], bool]:
    query = TurnEvent.query.filter_by(session_id=session_id)
    if not _campaign_pack_operator_view():
        query = query.filter(TurnEvent.event_type != PROGRESS_CHANGED_EVENT)
    rows = query.order_by(TurnEvent.created_at.asc(), TurnEvent.event_id.asc()).limit(SESSION_EXPORT_MAX_TURN_EVENTS + 1).all()
    truncated = len(rows) > SESSION_EXPORT_MAX_TURN_EVENTS
    if truncated:
        rows = rows[:SESSION_EXPORT_MAX_TURN_EVENTS]
    return [
        turn_event_payload(
            event,
            include_private=include_private,
            private_player_ids=private_player_ids,
        )
        for event in rows
    ], truncated


def _session_export_payload(
    session_obj: Session,
    *,
    selected_player_id: int | None = None,
    include_hidden_state: bool,
    viewer_account_id: int | None,
) -> dict:
    campaign = session_obj.campaign
    players = (
        Player.query.filter_by(campaign_id=campaign.campaign_id)
        .order_by(Player.player_id.asc())
        .all()
    )
    private_player_ids = frozenset(
        player.player_id
        for player in players
        if include_hidden_state or (viewer_account_id is not None and player.account_id == viewer_account_id)
    )
    selectable_players = players if include_hidden_state else [
        player for player in players if player.player_id in private_player_ids
    ]
    selected_player = None
    if selected_player_id is not None:
        selected_player = next(
            (player for player in selectable_players if player.player_id == selected_player_id),
            None,
        )
    if selected_player is None and selectable_players:
        selected_player = selectable_players[0]
    session_state = SessionState.query.filter_by(session_id=session_obj.session_id).first()
    log_entries, log_truncated = _session_export_log_entries(
        session_obj.session_id,
        include_private=include_hidden_state,
        private_player_ids=private_player_ids,
    )
    turn_events, turn_events_truncated = _session_export_turn_events(
        session_obj.session_id,
        include_private=include_hidden_state,
        private_player_ids=private_player_ids,
    )
    warnings = []
    if log_truncated:
        warnings.append(f'log entries truncated to {SESSION_EXPORT_MAX_LOG_ENTRIES}')
    if turn_events_truncated:
        warnings.append(f'turn events truncated to {SESSION_EXPORT_MAX_TURN_EVENTS}')
    return {
        'exportedAt': utc_now().isoformat(),
        'selectedIds': {
            'campaignId': campaign.campaign_id,
            'sessionId': session_obj.session_id,
            'playerId': selected_player.player_id if selected_player else None,
        },
        'campaign': campaign_payload(campaign),
        'selectedSession': session_payload(
            session_obj,
            include_hidden_state=include_hidden_state,
            viewer_account_id=viewer_account_id,
            private_player_ids=private_player_ids,
        ),
        'players': [
            party_player_payload(
                player,
                include_private=(include_hidden_state or player.player_id in private_player_ids),
            )
            for player in players
        ],
        'selectedPlayer': (
            party_player_payload(selected_player, include_private=True)
            if selected_player
            else None
        ),
        'sessionState': session_state_payload(
            session_obj,
            session_state,
            include_hidden_state=include_hidden_state,
            viewer_account_id=viewer_account_id,
            private_player_ids=private_player_ids,
        ),
        'logEntries': log_entries,
        'turnEvents': turn_events,
        'warnings': warnings,
    }


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


def _session_recap_context(
    session_obj: Session,
    session_state: SessionState | None,
    *,
    recap_source: str,
) -> _SessionRecapContext:
    campaign = session_obj.campaign
    return _SessionRecapContext(
        session_id=session_obj.session_id,
        campaign_id=session_obj.campaign_id,
        session_status=session_obj.status,
        state_snapshot=session_obj.state_snapshot,
        session_updated_at=session_obj.updated_at,
        session_deleted_at=session_obj.deleted_at,
        campaign_workspace_id=campaign.workspace_id,
        campaign_location=campaign.location,
        campaign_current_quest=campaign.current_quest,
        campaign_updated_at=campaign.updated_at,
        session_state_id=session_state.state_id if session_state else None,
        rolling_summary=session_state.rolling_summary if session_state else None,
        current_location=session_state.current_location if session_state else None,
        current_quest=session_state.current_quest if session_state else None,
        active_segments=session_state.active_segments if session_state else None,
        memory_snippets=session_state.memory_snippets if session_state else None,
        session_state_updated_at=session_state.updated_at if session_state else None,
        recap_source=recap_source,
    )


def _session_recap_conflict(captured: _SessionRecapContext, current: _SessionRecapContext | None):
    return error_response(
        'session_context_conflict',
        'Session changed while the recap was being prepared. Refresh and retry.',
        409,
        {
            'session_id': captured.session_id,
            'expected_updated_at': isoformat(captured.session_updated_at),
            'actual_updated_at': isoformat(current.session_updated_at) if current else None,
        },
    )


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

    try:
        # Campaign lifecycle mutations row-lock the same record. Refresh under
        # that lock so a stale preflight cannot create or replay an active
        # session after the campaign has been archived.
        campaign = db.session.get(
            Campaign,
            campaign.campaign_id,
            populate_existing=True,
            with_for_update=True,
        )
        if campaign is None:
            db.session.rollback()
            return error_response('campaign_not_found', 'Campaign not found.', 404)
        if str(campaign.status or 'active').strip().lower() != 'active':
            db.session.rollback()
            telemetry_event(
                'sessions.start.campaign_archived',
                payload={'campaign_id': campaign_id},
                severity='warning',
            )
            return error_response(
                'campaign_archived',
                'This campaign is archived. Restore it before starting a session.',
                409,
            )

        if client_session_id:
            existing_session = _find_idempotent_session(campaign.campaign_id, client_session_id)
            if existing_session:
                db.session.rollback()
                telemetry_metric('sessions.start.idempotent_replay_total', 1)
                return jsonify({'session_id': existing_session.session_id, 'idempotent_replay': True}), 200

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
        campaign = db.session.get(Campaign, campaign_id, populate_existing=True)
        if campaign is None:
            return error_response('campaign_not_found', 'Campaign not found.', 404)
        if str(campaign.status or 'active').strip().lower() != 'active':
            return error_response(
                'campaign_archived',
                'This campaign is archived. Restore it before starting a session.',
                409,
            )
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
    with session_turn_coordinator.serialized(session_id):
        session_obj = workspace_session(session_id)
        if not session_obj:
            telemetry_event('sessions.end.session_not_found', payload={'session_id': session_id}, severity='warning')
            return error_response('session_not_found', 'Session not found.', 404)

        session_state = SessionState.query.filter_by(session_id=session_id).first()
        recap_source = _bounded_session_recap_source(session_id, session_state)
        captured_context = _session_recap_context(
            session_obj,
            session_state,
            recap_source=recap_source,
        )
        recap_prompt = (
            'Please provide a concise summary of this D&D session, highlighting key events, '
            'important decisions, and significant character developments. Use the existing '
            'rolling summary for continuity and the bounded recent log for latest events:\n\n'
            f'{recap_source}'
        )

        with foreground_provider_reservation() as activate_provider:
            release_clean_scoped_session(boundary='session recap provider')
            activate_provider()
            recap = query_gpt(prompt=recap_prompt, system_message='You are a D&D session summarizer.')

        try:
            session_obj = workspace_session(session_id)
            if not session_obj:
                db.session.rollback()
                telemetry_metric('sessions.end.context_conflict_total', 1)
                return _session_recap_conflict(captured_context, None)

            session_state = SessionState.query.filter_by(session_id=session_id).first()
            current_recap_source = _bounded_session_recap_source(session_id, session_state)
            current_context = _session_recap_context(
                session_obj,
                session_state,
                recap_source=current_recap_source,
            )
            if current_context != captured_context:
                db.session.rollback()
                telemetry_metric('sessions.end.context_conflict_total', 1)
                return _session_recap_conflict(captured_context, current_context)

            if session_state is None:
                session_state = get_or_create_session_state(session_id, session_obj.campaign)

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


@sessions_bp.route('/<int:session_id>/recap', methods=['GET'])
def get_session_recap(session_id):
    telemetry_metric('sessions.recap.requests_total', 1)
    session_obj = workspace_session(session_id)
    if not session_obj:
        telemetry_event('sessions.recap.session_not_found', payload={'session_id': session_id}, severity='warning')
        return error_response('session_not_found', 'Session not found.', 404)

    payload = session_recap_payload(session_obj)
    telemetry_metric('sessions.recap.success_total', 1)
    return jsonify(payload)


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
            viewer_account_id=current_account_id(),
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
        operator_view = _campaign_pack_operator_view()
        account_id = current_account_id()
        result = import_session_export(
            payload,
            workspace_id=current_workspace_id(),
            include_hidden_state=operator_view,
            allow_campaign_pack_state=operator_view,
            required_player_account_id=(account_id if account_id is not None and not operator_view else None),
        )
        db.session.commit()
        telemetry_metric('sessions.import.success_total', 1)
        return jsonify(result.payload), 201
    except SessionImportError as exc:
        db.session.rollback()
        telemetry_event(
            'sessions.import.validation_error',
            payload={'error_code': exc.error_code, 'message': exc.public_message},
            severity='warning',
        )
        return error_response(exc.error_code, exc.public_message, exc.status_code)
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to import session: %s', str(exc))
        telemetry_event('sessions.import.failed', payload={'error': str(exc)}, severity='error')
        return error_response('session_import_failed', 'Failed to import session.', 400)


@sessions_bp.route('/<int:session_id>/export', methods=['GET'])
def export_session(session_id):
    telemetry_metric('sessions.export.requests_total', 1)
    session_obj = workspace_session(session_id)
    if not session_obj:
        telemetry_event('sessions.export.session_not_found', payload={'session_id': session_id}, severity='warning')
        return error_response('session_not_found', 'Session not found.', 404)

    selected_player_id = coerce_int(request.args.get('player_id'))
    include_hidden_state = _campaign_pack_operator_view()
    viewer_account_id = current_account_id()
    if selected_player_id is not None and not include_hidden_state:
        selected_player = (
            Player.query.filter_by(
                player_id=selected_player_id,
                campaign_id=session_obj.campaign_id,
                account_id=viewer_account_id,
            ).first()
            if viewer_account_id is not None
            else None
        )
        if selected_player is None:
            return error_response('player_not_found', 'Player not found.', 404)
    payload = _session_export_payload(
        session_obj,
        selected_player_id=selected_player_id,
        include_hidden_state=include_hidden_state,
        viewer_account_id=viewer_account_id,
    )
    telemetry_metric('sessions.export.success_total', 1)
    return jsonify(payload)


@sessions_bp.route('/<int:session_id>/chronicle', methods=['GET'])
def export_session_chronicle(session_id):
    session_obj = workspace_session(session_id)
    if not session_obj:
        return error_response('session_not_found', 'Session not found.', 404)

    export = export_session_chronicle_html(
        session_obj,
        include_director_metadata=current_actor_has_capability('debug_read'),
    )
    return chronicle_html_response(export)


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
        include_hidden_state = _campaign_pack_operator_view()
        payload = commit_session_lifecycle_operation(
            session_id,
            workspace_id=current_workspace_id(),
            operation=lambda refreshed: archive_session_record(
                refreshed,
                include_hidden_state=include_hidden_state,
            ),
        )
        telemetry_metric('sessions.archive.success_total', 1)
        return jsonify({'archived': True, 'session': payload})
    except SessionLifecycleTargetNotFoundError:
        db.session.rollback()
        telemetry_event('sessions.archive.session_not_found', payload={'session_id': session_id}, severity='warning')
        return error_response('session_not_found', 'Session not found.', 404)
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
        include_hidden_state = _campaign_pack_operator_view()
        payload = commit_session_lifecycle_operation(
            session_id,
            workspace_id=current_workspace_id(),
            operation=lambda refreshed: restore_session_record(
                refreshed,
                include_hidden_state=include_hidden_state,
            ),
        )
        telemetry_metric('sessions.restore.success_total', 1)
        return jsonify({'restored': True, 'session': payload})
    except SessionLifecycleTargetNotFoundError:
        db.session.rollback()
        telemetry_event('sessions.restore.session_not_found', payload={'session_id': session_id}, severity='warning')
        return error_response('session_not_found', 'Session not found.', 404)
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
        include_hidden_state = _campaign_pack_operator_view()
        result = commit_session_lifecycle_operation(
            session_id,
            workspace_id=current_workspace_id(),
            operation=lambda refreshed: delete_session_record(
                refreshed,
                hard_delete=hard_delete,
                include_hidden_state=include_hidden_state,
            ),
        )
        telemetry_metric(
            'sessions.delete.success_total' if result.hard_deleted else 'sessions.delete.archived_total',
            1,
        )
        return jsonify(result.payload)
    except SessionLifecycleTargetNotFoundError:
        db.session.rollback()
        telemetry_event('sessions.delete.session_not_found', payload={'session_id': session_id}, severity='warning')
        return error_response('session_not_found', 'Session not found.', 404)
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to delete session: %s', str(exc))
        telemetry_event('sessions.delete.failed', payload={'session_id': session_id, 'error': str(exc)}, severity='error')
        return error_response('session_delete_failed', 'Failed to delete session.', 400)


@sessions_bp.route('/<int:session_id>/recovery/resolve', methods=['POST'])
def resolve_session_recovery(session_id):
    if not _campaign_pack_operator_view():
        return error_response(
            'forbidden',
            'Only workspace admins can resolve a failed-turn recovery gate.',
            403,
            {'required_capability': 'dm_runtime_control'},
        )
    session_obj = workspace_session(session_id)
    if not session_obj:
        return error_response('session_not_found', 'Session not found.', 404)

    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    raw_turn_id = payload.get('turn_id') if 'turn_id' in payload else payload.get('turnId')
    turn_id, turn_id_error = positive_int(raw_turn_id, field='turn_id', required=True)
    if turn_id_error:
        return error_response('validation_error', turn_id_error, 400)

    resolution = str(payload.get('resolution') or '').strip()
    if resolution not in TURN_RECOVERY_RESOLUTIONS:
        return error_response(
            'validation_error',
            'resolution must be state_corrected or no_mechanical_change_required.',
            400,
        )

    raw_operator_note = (
        payload.get('operator_note')
        if 'operator_note' in payload
        else payload.get('operatorNote')
    )
    operator_note, operator_note_error = required_text(
        raw_operator_note,
        max_length=OPERATOR_NOTE_MAX_LENGTH,
        field='operator_note',
    )
    if operator_note_error:
        return error_response('validation_error', operator_note_error, 400)

    try:
        result = resolve_turn_recovery_gate(
            session_id=session_id,
            turn_id=turn_id,
            resolution=resolution,
            operator_note=operator_note,
        )
        telemetry_event(
            'sessions.recovery.resolved',
            payload={
                'session_id': session_id,
                'turn_id': turn_id,
                'resolution': resolution,
                'idempotent_replay': result.idempotent_replay,
            },
        )
        if not result.idempotent_replay:
            socketio = current_app.extensions.get('socketio')
            if socketio is not None:
                try:
                    socketio.emit(
                        'session_recovery_resolved',
                        session_recovery_resolved_payload(
                            session_id=result.session_id,
                            turn_id=result.turn_id,
                            state_revision=result.state_revision,
                        ),
                        room=str(result.session_id),
                    )
                except Exception as exc:
                    # The authoritative recovery commit already succeeded. A
                    # transient notification failure must not turn that durable
                    # success into an ambiguous HTTP error or replay the audit.
                    error_type = type(exc).__name__
                    logger.warning(
                        'Failed to notify recovery resolution (error_type=%s, session_id=%s, turn_id=%s).',
                        error_type,
                        result.session_id,
                        result.turn_id,
                    )
                    telemetry_event(
                        'sessions.recovery.notification_failed',
                        payload={
                            'session_id': result.session_id,
                            'turn_id': result.turn_id,
                            'error_type': error_type,
                        },
                        severity='warning',
                    )
        return jsonify(
            {
                'resolved': True,
                'idempotent_replay': result.idempotent_replay,
                'session_id': result.session_id,
                'turn_id': result.turn_id,
                'resolution': result.resolution,
                'state_revision': result.state_revision,
            }
        )
    except SessionRecoveryConflictError as exc:
        db.session.rollback()
        status_code = 404 if exc.error_code == 'session_not_found' else 409
        return error_response(
            exc.error_code,
            exc.public_message,
            status_code,
            exc.details or None,
        )
    except Exception:
        db.session.rollback()
        logger.exception(
            'Failed to resolve session recovery (session_id=%s, turn_id=%s).',
            session_id,
            turn_id,
        )
        return error_response(
            'session_recovery_resolve_failed',
            'Failed to resolve session recovery.',
            500,
        )


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
    include_private = _campaign_pack_operator_view()
    private_player_ids = account_player_ids_by_campaign(
        [session_obj.campaign_id],
        current_account_id(),
    ).get(session_obj.campaign_id, frozenset())
    turn_player_ids = _turn_player_ids_for_log_entries(entries)
    return jsonify(
        {
            'session_id': session_id,
            'limit': limit,
            'has_more': has_more,
            'next_cursor': entries[0].id if has_more and entries else None,
            'entries': [
                session_log_entry_payload(
                    entry,
                    include_private=include_private,
                    private_player_ids=private_player_ids,
                    turn_player_ids=turn_player_ids,
                )
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
    include_private = _campaign_pack_operator_view()
    private_player_ids = account_player_ids_by_campaign(
        [session_obj.campaign_id],
        current_account_id(),
    ).get(session_obj.campaign_id, frozenset())
    return jsonify(
        {
            'session_id': session_id,
            'limit': limit,
            'has_more': has_more,
            'next_cursor': events[0].event_id if has_more and events else None,
            'events': [
                turn_event_payload(
                    event,
                    include_private=include_private,
                    private_player_ids=private_player_ids,
                )
                for event in events
            ],
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
            viewer_account_id=current_account_id(),
        )
    )


@sessions_bp.route('/<int:session_id>/content-settings', methods=['GET'])
def get_session_content_settings(session_id):
    session_obj = workspace_session(session_id)
    if not session_obj:
        return error_response('session_not_found', 'Session not found.', 404)
    return jsonify(
        {
            'session_id': session_id,
            'settings': content_settings_payload(session_content_settings(session_obj)),
        }
    )


@sessions_bp.route('/<int:session_id>/content-settings', methods=['PATCH', 'POST'])
def update_session_content_settings(session_id):
    session_obj = workspace_session(session_id)
    if not session_obj:
        return error_response('session_not_found', 'Session not found.', 404)
    if not _campaign_pack_operator_view():
        return error_response(
            'forbidden',
            'Only workspace admins can change session content settings.',
            403,
        )
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    content_rating = payload.get('content_rating') if 'content_rating' in payload else payload.get('contentRating')
    tone_tags = payload.get('tone_tags') if 'tone_tags' in payload else payload.get('toneTags')
    try:
        settings = apply_session_content_settings(
            session_obj,
            content_rating=content_rating,
            tone_tags=tone_tags,
        )
        session_state = SessionState.query.filter_by(session_id=session_id).first()
        db.session.commit()
        return jsonify(
            {
                'session_id': session_id,
                'settings': content_settings_payload(settings),
                'session': session_payload(session_obj, include_hidden_state=_campaign_pack_operator_view()),
                'state': session_state_payload(
                    session_obj,
                    session_state,
                    include_hidden_state=_campaign_pack_operator_view(),
                ),
            }
        )
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to update session content settings: %s', str(exc))
        return error_response('content_settings_update_failed', 'Failed to update content settings.', 400)


@sessions_bp.route('/<int:session_id>/campaign-pack/progress', methods=['GET'])
def get_session_campaign_pack_progress(session_id):
    session_obj = workspace_session(session_id)
    if not session_obj:
        return error_response('session_not_found', 'Session not found.', 404)
    try:
        return jsonify(campaign_pack_progress_payload(session_id=session_id, include_hidden=_campaign_pack_operator_view()))
    except CampaignPackProgressError as exc:
        return error_response(exc.error_code, exc.public_message, exc.status_code)


@sessions_bp.route('/<int:session_id>/campaign-pack/commentary', methods=['GET'])
def get_session_campaign_pack_commentary(session_id):
    session_obj = workspace_session(session_id)
    if not session_obj:
        return error_response('session_not_found', 'Session not found.', 404)
    if not _campaign_pack_operator_view():
        return error_response(
            'forbidden',
            'Only workspace admins can inspect director commentary.',
            403,
        )

    payload = campaign_pack_commentary_payload(session_obj)
    if not payload.get('enabled'):
        return error_response('campaign_pack_not_found', 'Session does not have an imported campaign pack.', 404)
    return jsonify(payload)


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
        return error_response(exc.error_code, exc.public_message, exc.status_code)
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to update campaign pack progress: %s', str(exc))
        return error_response('campaign_pack_progress_failed', 'Failed to update campaign pack progress.', 400)
