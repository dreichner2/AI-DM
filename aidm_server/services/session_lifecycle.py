from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

from sqlalchemy import or_

from aidm_server.auth import DEFAULT_WORKSPACE_ID
from aidm_server.database import db
from aidm_server.models import (
    Campaign,
    CanonJob,
    DmCoherenceFeedback,
    DmTurn,
    PlayerAction,
    Session,
    SessionLogEntry,
    SessionState,
    StoryEntity,
    StoryFact,
    StoryThread,
    TurnCanonUpdate,
    TurnEvent,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.response_dtos import session_payload
from aidm_server.operator_audit import record_operator_action
from aidm_server.time_utils import utc_now
from aidm_server.turn_coordinator import SessionTurnTargetMissingError, session_turn_coordinator

ACTIVE_STATUS = 'active'
ARCHIVED_STATUS = 'archived'
DELETED_STATUS = 'deleted'
LifecycleResult = TypeVar('LifecycleResult')


@dataclass(frozen=True)
class SessionDeletionResult:
    hard_deleted: bool
    payload: dict


class SessionLifecycleTargetNotFoundError(LookupError):
    """Raised when a lifecycle target disappears or leaves the caller's workspace."""


def commit_session_lifecycle_operation(
    session_id: int,
    *,
    workspace_id: str,
    operation: Callable[[Session], LifecycleResult],
) -> LifecycleResult:
    """Serialize, refresh, mutate, and commit one session as one fenced operation."""

    try:
        with session_turn_coordinator.serialized(session_id):
            try:
                session_obj = db.session.get(
                    Session,
                    session_id,
                    populate_existing=True,
                    with_for_update=True,
                )
                if session_obj is None:
                    raise SessionLifecycleTargetNotFoundError(session_id)

                campaign = db.session.get(
                    Campaign,
                    session_obj.campaign_id,
                    populate_existing=True,
                    with_for_update=True,
                )
                target_workspace_id = workspace_id or DEFAULT_WORKSPACE_ID
                campaign_workspace_id = (
                    campaign.workspace_id or DEFAULT_WORKSPACE_ID
                    if campaign is not None
                    else None
                )
                if campaign is None or campaign_workspace_id != target_workspace_id:
                    raise SessionLifecycleTargetNotFoundError(session_id)

                result = operation(session_obj)
                # Database-backed coordinator leases are fenced by the
                # before_commit hook while this context is still active.
                db.session.commit()
                return result
            except Exception:
                db.session.rollback()
                raise
    except SessionTurnTargetMissingError as exc:
        db.session.rollback()
        raise SessionLifecycleTargetNotFoundError(session_id) from exc


def session_playability_error(
    session_obj: Session,
    *,
    campaign_obj=None,
) -> tuple[str, str] | None:
    """Return a safe public error when a session cannot accept live play."""

    session_status = str(getattr(session_obj, 'status', None) or ACTIVE_STATUS).strip().lower()
    if session_status == ARCHIVED_STATUS:
        return 'session_archived', 'This session is archived. Restore it before playing.'
    if session_status == DELETED_STATUS:
        return 'session_deleted', 'This session has been deleted and is unavailable.'

    campaign = campaign_obj if campaign_obj is not None else getattr(session_obj, 'campaign', None)
    campaign_status = str(getattr(campaign, 'status', None) or ACTIVE_STATUS).strip().lower()
    if campaign_status == ARCHIVED_STATUS:
        return 'campaign_archived', 'This campaign is archived. Restore it before playing.'
    if campaign_status == DELETED_STATUS:
        return 'campaign_deleted', 'This campaign has been deleted and is unavailable.'
    return None


def _state_snapshot_dict(raw_snapshot) -> dict:
    snapshot = safe_json_loads(raw_snapshot, {})
    return snapshot if isinstance(snapshot, dict) else {}


def metadata_cleaned_snapshot(raw_snapshot) -> dict:
    snapshot = _state_snapshot_dict(raw_snapshot)
    for key in ('name', 'title', 'updated_at', 'is_archived', 'archived'):
        snapshot.pop(key, None)
    return snapshot


def archive_session_record(session_obj: Session, *, include_hidden_state: bool = True) -> dict:
    session_turn_coordinator.discard_session(session_obj.session_id)
    now = utc_now()
    session_obj.status = ARCHIVED_STATUS
    session_obj.deleted_at = now
    session_obj.updated_at = now
    session_obj.archived_by_campaign_id = None
    session_obj.state_snapshot = safe_json_dumps(metadata_cleaned_snapshot(session_obj.state_snapshot), {})
    record_operator_action(
        action='session.archive',
        resource_type='session',
        workspace_id=(session_obj.campaign.workspace_id if session_obj.campaign else 'owner'),
        campaign_id=session_obj.campaign_id,
        session_id=session_obj.session_id,
        resource_id=session_obj.session_id,
    )
    return session_payload(session_obj, include_hidden_state=include_hidden_state)


def restore_session_record(session_obj: Session, *, include_hidden_state: bool = True) -> dict:
    now = utc_now()
    session_obj.status = ACTIVE_STATUS
    session_obj.deleted_at = None
    session_obj.updated_at = now
    session_obj.archived_by_campaign_id = None
    session_obj.state_snapshot = safe_json_dumps(metadata_cleaned_snapshot(session_obj.state_snapshot), {})
    record_operator_action(
        action='session.restore',
        resource_type='session',
        workspace_id=(session_obj.campaign.workspace_id if session_obj.campaign else 'owner'),
        campaign_id=session_obj.campaign_id,
        session_id=session_obj.session_id,
        resource_id=session_obj.session_id,
    )
    return session_payload(session_obj, include_hidden_state=include_hidden_state)


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

        fact_ids_to_delete = []
        thread_delete_filters = []
        if turn_ids:
            fact_ids_to_delete = [
                row[0]
                for row in db.session.query(StoryFact.fact_id)
                .filter(StoryFact.source_turn_id.in_(turn_ids))
                .all()
            ]
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
            StoryEntity.query.filter(StoryEntity.first_seen_turn_id.in_(turn_ids)).update(
                {StoryEntity.first_seen_turn_id: None},
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

        if fact_ids_to_delete:
            StoryFact.query.filter(StoryFact.supersedes_fact_id.in_(fact_ids_to_delete)).update(
                {StoryFact.supersedes_fact_id: None},
                synchronize_session=False,
            )
            StoryFact.query.filter(StoryFact.fact_id.in_(fact_ids_to_delete)).delete(synchronize_session=False)
        if thread_delete_filters:
            StoryThread.query.filter(or_(*thread_delete_filters)).delete(synchronize_session=False)

        entity_ids_to_delete: list[int] = []
        if session_entity_ids:
            referenced_entity_ids = {
                row[0]
                for row in db.session.query(StoryFact.subject_entity_id)
                .filter(StoryFact.subject_entity_id.in_(session_entity_ids))
                .distinct()
                .all()
                if row[0] is not None
            }
            referenced_entity_ids.update(
                row[0]
                for row in db.session.query(StoryFact.object_entity_id)
                .filter(StoryFact.object_entity_id.in_(session_entity_ids))
                .distinct()
                .all()
                if row[0] is not None
            )
            query = StoryEntity.query.filter(StoryEntity.entity_id.in_(session_entity_ids))
            for entity in query.all():
                if entity.entity_id in referenced_entity_ids:
                    continue
                if entity.last_seen_turn_id is not None and entity.last_seen_turn_id not in turn_ids:
                    continue
                entity_ids_to_delete.append(entity.entity_id)
            if entity_ids_to_delete:
                StoryEntity.query.filter(StoryEntity.entity_id.in_(entity_ids_to_delete)).delete(synchronize_session=False)
            StoryEntity.query.filter(StoryEntity.entity_id.in_(session_entity_ids)).update(
                {StoryEntity.session_id: None},
                synchronize_session=False,
            )

        PlayerAction.query.filter_by(session_id=session_id).delete(synchronize_session=False)
        SessionLogEntry.query.filter_by(session_id=session_id).delete(synchronize_session=False)
        SessionState.query.filter_by(session_id=session_id).delete(synchronize_session=False)
        if turn_ids:
            DmTurn.query.filter(DmTurn.turn_id.in_(turn_ids)).delete(synchronize_session=False)

        db.session.delete(session_obj)
    return {'deleted': True, 'session_id': session_id}


def delete_session_record(session_obj: Session, *, hard_delete: bool, include_hidden_state: bool = True) -> SessionDeletionResult:
    session_id = session_obj.session_id
    if hard_delete:
        session_turn_coordinator.discard_session(session_id)
        record_operator_action(
            action='session.delete_hard',
            resource_type='session',
            workspace_id=(session_obj.campaign.workspace_id if session_obj.campaign else 'owner'),
            campaign_id=session_obj.campaign_id,
            resource_id=session_id,
        )
        payload = hard_delete_session_record(session_obj)
        return SessionDeletionResult(hard_deleted=True, payload=payload)

    session_payload_data = archive_session_record(session_obj, include_hidden_state=include_hidden_state)
    return SessionDeletionResult(
        hard_deleted=False,
        payload={
            'deleted': True,
            'archived': True,
            'session_id': session_id,
            'session': session_payload_data,
        },
    )
