from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_

from aidm_server.database import db
from aidm_server.models import (
    CanonJob,
    DmCoherenceFeedback,
    DmTurn,
    PlayerAction,
    Session,
    SessionLogEntry,
    SessionState,
    SessionTurnLock,
    StoryEntity,
    StoryFact,
    StoryThread,
    TurnCanonUpdate,
    TurnEvent,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.response_dtos import session_payload
from aidm_server.time_utils import utc_now
from aidm_server.turn_coordinator import session_turn_coordinator

ACTIVE_STATUS = 'active'
ARCHIVED_STATUS = 'archived'


@dataclass(frozen=True)
class SessionDeletionResult:
    hard_deleted: bool
    payload: dict


def _state_snapshot_dict(raw_snapshot) -> dict:
    snapshot = safe_json_loads(raw_snapshot, {})
    return snapshot if isinstance(snapshot, dict) else {}


def metadata_cleaned_snapshot(raw_snapshot) -> dict:
    snapshot = _state_snapshot_dict(raw_snapshot)
    for key in ('name', 'title', 'updated_at', 'is_archived', 'archived'):
        snapshot.pop(key, None)
    return snapshot


def archive_session_record(session_obj: Session) -> dict:
    now = utc_now()
    session_obj.status = ARCHIVED_STATUS
    session_obj.deleted_at = now
    session_obj.updated_at = now
    session_obj.archived_by_campaign_id = None
    session_obj.state_snapshot = safe_json_dumps(metadata_cleaned_snapshot(session_obj.state_snapshot), {})
    session_turn_coordinator.discard_session(session_obj.session_id)
    return session_payload(session_obj)


def restore_session_record(session_obj: Session) -> dict:
    now = utc_now()
    session_obj.status = ACTIVE_STATUS
    session_obj.deleted_at = None
    session_obj.updated_at = now
    session_obj.archived_by_campaign_id = None
    session_obj.state_snapshot = safe_json_dumps(metadata_cleaned_snapshot(session_obj.state_snapshot), {})
    return session_payload(session_obj)


def hard_delete_session_record(session_obj: Session) -> dict:
    session_id = session_obj.session_id
    turn_ids = [
        row[0]
        for row in db.session.query(DmTurn.turn_id)
        .filter(DmTurn.session_id == session_id)
        .all()
    ]

    with db.session.no_autoflush:
        entity_filters = [StoryEntity.session_id == session_id]
        if turn_ids:
            entity_filters.append(StoryEntity.first_seen_turn_id.in_(turn_ids))
        session_entity_ids = [
            row[0]
            for row in db.session.query(StoryEntity.entity_id)
            .filter(or_(*entity_filters))
            .all()
        ]

        fact_delete_filters = []
        thread_delete_filters = []
        if turn_ids:
            fact_delete_filters.append(StoryFact.source_turn_id.in_(turn_ids))
            thread_delete_filters.append(StoryThread.origin_turn_id.in_(turn_ids))
            TurnCanonUpdate.query.filter(TurnCanonUpdate.turn_id.in_(turn_ids)).delete(
                synchronize_session=False,
            )
            CanonJob.query.filter(
                or_(CanonJob.session_id == session_id, CanonJob.turn_id.in_(turn_ids)),
            ).delete(synchronize_session=False)
            DmCoherenceFeedback.query.filter(
                or_(
                    DmCoherenceFeedback.session_id == session_id,
                    DmCoherenceFeedback.turn_id.in_(turn_ids),
                ),
            ).delete(synchronize_session=False)
            TurnEvent.query.filter(
                or_(TurnEvent.session_id == session_id, TurnEvent.turn_id.in_(turn_ids)),
            ).delete(synchronize_session=False)
            StoryEntity.query.filter(StoryEntity.last_seen_turn_id.in_(turn_ids)).update(
                {StoryEntity.last_seen_turn_id: None},
                synchronize_session=False,
            )
            StoryThread.query.filter(StoryThread.last_touched_turn_id.in_(turn_ids)).update(
                {StoryThread.last_touched_turn_id: None},
                synchronize_session=False,
            )
            StoryThread.query.filter(StoryThread.resolved_turn_id.in_(turn_ids)).update(
                {StoryThread.resolved_turn_id: None},
                synchronize_session=False,
            )
        else:
            CanonJob.query.filter_by(session_id=session_id).delete(synchronize_session=False)
            DmCoherenceFeedback.query.filter_by(session_id=session_id).delete(synchronize_session=False)
            TurnEvent.query.filter_by(session_id=session_id).delete(synchronize_session=False)

        if session_entity_ids:
            fact_delete_filters.append(StoryFact.subject_entity_id.in_(session_entity_ids))
            fact_delete_filters.append(StoryFact.object_entity_id.in_(session_entity_ids))
        if fact_delete_filters:
            StoryFact.query.filter(or_(*fact_delete_filters)).delete(synchronize_session=False)
        if thread_delete_filters:
            StoryThread.query.filter(or_(*thread_delete_filters)).delete(synchronize_session=False)
        if session_entity_ids:
            StoryEntity.query.filter(StoryEntity.entity_id.in_(session_entity_ids)).delete(synchronize_session=False)

        PlayerAction.query.filter_by(session_id=session_id).delete(synchronize_session=False)
        SessionLogEntry.query.filter_by(session_id=session_id).delete(synchronize_session=False)
        SessionState.query.filter_by(session_id=session_id).delete(synchronize_session=False)
        SessionTurnLock.query.filter_by(session_id=session_id).delete(synchronize_session=False)
        if turn_ids:
            DmTurn.query.filter(DmTurn.turn_id.in_(turn_ids)).delete(synchronize_session=False)

        db.session.delete(session_obj)
    return {'deleted': True, 'session_id': session_id}


def delete_session_record(session_obj: Session, *, hard_delete: bool) -> SessionDeletionResult:
    session_id = session_obj.session_id
    if hard_delete:
        payload = hard_delete_session_record(session_obj)
        session_turn_coordinator.discard_session(session_id)
        return SessionDeletionResult(hard_deleted=True, payload=payload)

    session_payload_data = archive_session_record(session_obj)
    return SessionDeletionResult(
        hard_deleted=False,
        payload={
            'deleted': True,
            'archived': True,
            'session_id': session_id,
            'session': session_payload_data,
        },
    )
