from __future__ import annotations

from typing import Callable, TypeVar

from sqlalchemy import func, or_

from aidm_server.auth import DEFAULT_WORKSPACE_ID
from aidm_server.database import db
from aidm_server.models import (
    BestiaryEntry,
    Campaign,
    CampaignSegment,
    CanonJob,
    CombatDebugEvent,
    CombatEncounter,
    DmCoherenceFeedback,
    DmTurn,
    Map,
    Player,
    Session,
    StoryEntity,
    StoryEvent,
    StoryFact,
    StoryThread,
    TurnCanonUpdate,
    TurnEvent,
)
from aidm_server.response_dtos import campaign_payload
from aidm_server.operator_audit import record_operator_action
from aidm_server.services.session_lifecycle import delete_session_record
from aidm_server.time_utils import utc_now
from aidm_server.turn_coordinator import SessionTurnTargetMissingError, session_turn_coordinator

ACTIVE_STATUS = 'active'
ARCHIVED_STATUS = 'archived'
LifecycleResult = TypeVar('LifecycleResult')


class CampaignHasSessionsError(RuntimeError):
    def __init__(self, session_count: int):
        super().__init__('Campaign has sessions.')
        self.session_count = session_count


class CampaignLifecycleTargetNotFoundError(LookupError):
    """Raised when a lifecycle target disappears or leaves the caller's workspace."""


def _campaign_session_ids(campaign_id: int) -> list[int]:
    return [
        int(row[0])
        for row in db.session.query(Session.session_id)
        .filter(Session.campaign_id == campaign_id)
        .order_by(Session.session_id.asc())
        .all()
    ]


def commit_campaign_lifecycle_operation(
    campaign_id: int,
    *,
    workspace_id: str,
    operation: Callable[[Campaign], LifecycleResult],
) -> LifecycleResult:
    """Fence every campaign session and commit the lifecycle mutation in-lock."""

    candidate_session_ids = _campaign_session_ids(campaign_id)
    while True:
        try:
            with session_turn_coordinator.serialized_many(candidate_session_ids):
                try:
                    campaign = db.session.get(
                        Campaign,
                        campaign_id,
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
                        raise CampaignLifecycleTargetNotFoundError(campaign_id)

                    # Refresh and row-lock after acquiring coordinator leases.
                    # If a session appeared while leases were being acquired,
                    # release and retry so every mutation uses one deterministic
                    # ascending lock order.
                    session_rows = (
                        Session.query.filter_by(campaign_id=campaign_id)
                        .order_by(Session.session_id.asc())
                        .populate_existing()
                        .with_for_update()
                        .all()
                    )
                    current_session_ids = [int(session.session_id) for session in session_rows]
                    if current_session_ids != candidate_session_ids:
                        db.session.rollback()
                        candidate_session_ids = current_session_ids
                        continue

                    result = operation(campaign)
                    # The database coordinator's before_commit hook fences all
                    # held leases before deletes cascade their lock rows.
                    db.session.commit()
                    return result
                except Exception:
                    db.session.rollback()
                    raise
        except SessionTurnTargetMissingError:
            # A session was hard-deleted while this operation waited. Refresh
            # the campaign/session set and retry, or fail closed if the campaign
            # itself disappeared.
            db.session.rollback()
            campaign = db.session.get(Campaign, campaign_id, populate_existing=True)
            campaign_workspace_id = (
                campaign.workspace_id or DEFAULT_WORKSPACE_ID
                if campaign is not None
                else None
            )
            if campaign is None or campaign_workspace_id != (workspace_id or DEFAULT_WORKSPACE_ID):
                raise CampaignLifecycleTargetNotFoundError(campaign_id) from None
            candidate_session_ids = _campaign_session_ids(campaign_id)


def archive_campaign_record(campaign: Campaign) -> dict:
    campaign_id = campaign.campaign_id
    campaign.status = ARCHIVED_STATUS
    campaign.updated_at = utc_now()
    archived_sessions = Session.query.filter(
        Session.campaign_id == campaign_id,
        or_(Session.status.is_(None), Session.status != ARCHIVED_STATUS),
    ).update(
        {
            Session.status: ARCHIVED_STATUS,
            Session.deleted_at: campaign.updated_at,
            Session.updated_at: campaign.updated_at,
            Session.archived_by_campaign_id: campaign_id,
        },
        synchronize_session=False,
    )
    record_operator_action(
        action='campaign.archive',
        resource_type='campaign',
        workspace_id=campaign.workspace_id or 'owner',
        campaign_id=campaign_id,
        resource_id=campaign_id,
        details={'archivedSessionCount': archived_sessions},
    )
    return campaign_payload(campaign)


def restore_campaign_record(campaign: Campaign) -> dict:
    campaign_id = campaign.campaign_id
    campaign.status = ACTIVE_STATUS
    campaign.updated_at = utc_now()
    restored_sessions = Session.query.filter_by(campaign_id=campaign_id, archived_by_campaign_id=campaign_id).update(
        {
            Session.status: ACTIVE_STATUS,
            Session.deleted_at: None,
            Session.updated_at: campaign.updated_at,
            Session.archived_by_campaign_id: None,
        },
        synchronize_session=False,
    )
    record_operator_action(
        action='campaign.restore',
        resource_type='campaign',
        workspace_id=campaign.workspace_id or 'owner',
        campaign_id=campaign_id,
        resource_id=campaign_id,
        details={'restoredSessionCount': restored_sessions},
    )
    return campaign_payload(campaign)


def _detach_campaign_players(campaign_id: int) -> list[int]:
    detached_player_ids = [
        player.player_id for player in Player.query.filter_by(campaign_id=campaign_id).all()
    ]
    Player.query.filter_by(campaign_id=campaign_id).update(
        {Player.campaign_id: None},
        synchronize_session=False,
    )
    return detached_player_ids


def _delete_campaign_runtime_rows(campaign_id: int) -> None:
    CombatDebugEvent.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    CombatEncounter.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    BestiaryEntry.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)


def _force_delete_campaign(campaign: Campaign) -> dict:
    campaign_id = campaign.campaign_id
    workspace_id = campaign.workspace_id or 'owner'
    session_rows = Session.query.filter_by(campaign_id=campaign_id).all()
    session_ids = [session.session_id for session in session_rows]
    # Keep Session deletes pending until commit begins. The database coordinator
    # fences every held lease in before_commit; an autoflush here would delete
    # the parent sessions and cascade their lock rows too early.
    with db.session.no_autoflush:
        detached_player_ids = _detach_campaign_players(campaign_id)
        for session_obj in session_rows:
            delete_session_record(session_obj, hard_delete=True)

        CanonJob.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
        TurnCanonUpdate.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
        TurnEvent.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
        _delete_campaign_runtime_rows(campaign_id)
        DmCoherenceFeedback.query.filter(
            DmCoherenceFeedback.turn_id.in_(
                db.session.query(DmTurn.turn_id).filter_by(campaign_id=campaign_id),
            )
        ).delete(synchronize_session=False)
        DmTurn.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
        StoryFact.query.filter_by(campaign_id=campaign_id).update(
            {StoryFact.supersedes_fact_id: None},
            synchronize_session=False,
        )
        StoryFact.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
        StoryThread.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
        StoryEntity.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
        StoryEvent.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
        CampaignSegment.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
        Map.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
        record_operator_action(
            action='campaign.delete_hard',
            resource_type='campaign',
            workspace_id=workspace_id,
            resource_id=campaign_id,
            details={
                'forceDelete': True,
                'deletedSessionIds': session_ids,
                'detachedPlayerIds': detached_player_ids,
            },
        )
        db.session.delete(campaign)
    return {
        'deleted': True,
        'campaign_id': campaign_id,
        'archived': False,
        'hard_deleted': True,
        'deleted_session_ids': session_ids,
        'detached_player_ids': detached_player_ids,
    }


def _hard_delete_campaign_without_sessions(campaign: Campaign) -> dict:
    campaign_id = campaign.campaign_id
    workspace_id = campaign.workspace_id or 'owner'
    detached_player_ids = _detach_campaign_players(campaign_id)
    _delete_campaign_runtime_rows(campaign_id)
    record_operator_action(
        action='campaign.delete_hard',
        resource_type='campaign',
        workspace_id=workspace_id,
        resource_id=campaign_id,
        details={
            'forceDelete': False,
            'deletedSessionIds': [],
            'detachedPlayerIds': detached_player_ids,
        },
    )
    db.session.delete(campaign)
    return {
        'deleted': True,
        'campaign_id': campaign_id,
        'archived': False,
        'hard_deleted': True,
        'deleted_session_ids': [],
        'detached_player_ids': detached_player_ids,
    }


def delete_campaign_record(campaign: Campaign, *, hard_delete: bool, force_delete: bool) -> dict:
    if not hard_delete:
        return {
            'archived': True,
            'campaign': archive_campaign_record(campaign),
        }

    campaign_id = campaign.campaign_id
    session_count = (
        db.session.query(func.count(Session.session_id)).filter_by(campaign_id=campaign_id).scalar()
        or 0
    )
    if session_count and not force_delete:
        raise CampaignHasSessionsError(int(session_count))

    if force_delete:
        return _force_delete_campaign(campaign)
    return _hard_delete_campaign_without_sessions(campaign)
