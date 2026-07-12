"""Shared-session coordination for campaign-pack progress transactions."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import or_

from aidm_server.database import (
    db,
    release_clean_scoped_session,
    scoped_session_has_pending_writes,
)
from aidm_server.models import CampaignPackSession, Session, safe_json_loads
from aidm_server.turn_coordinator import SessionTurnTargetMissingError, session_turn_coordinator


class CampaignPackProgressLockSetChanged(RuntimeError):
    """Raised when shared-progress membership exceeds the held lock set."""


@dataclass(frozen=True)
class CampaignPackProgressLockBoundary:
    """Fresh, revalidated shared-progress leases held by the current caller."""

    session_ids: tuple[int, ...]
    waits: dict[int, float]


def require_campaign_pack_progress_locks(session_ids) -> None:
    normalized = sorted({int(session_id) for session_id in session_ids})
    if not session_turn_coordinator.holds_all(normalized):
        raise CampaignPackProgressLockSetChanged(
            f'Campaign-pack shared progress lock set changed: {normalized}'
        )


def campaign_pack_progress_lock_session_ids(session_id: int) -> list[int]:
    """Return the active shared-progress session set without flushing callers."""

    with db.session.no_autoflush:
        return _campaign_pack_progress_lock_session_ids(session_id)


def _campaign_pack_progress_lock_session_ids(session_id: int) -> list[int]:
    """Read the active shared-progress session set in deterministic order."""

    session = db.session.get(Session, session_id)
    if session is None:
        return [session_id]

    snapshot = safe_json_loads(session.state_snapshot, {})
    pack = (
        snapshot.get('campaignPack')
        if isinstance(snapshot, dict) and isinstance(snapshot.get('campaignPack'), dict)
        else {}
    )
    group_key = _text(_first(pack, 'multiSessionGroupKey', 'multi_session_group_key'))
    pack_id = _text(_first(pack, 'packId', 'pack_id'))
    if not group_key or not pack_id:
        return [session_id]

    current_progress = CampaignPackSession.query.filter_by(session_id=session_id).first()
    if current_progress is None:
        return [session_id]

    sibling_ids = [
        int(row[0])
        for row in db.session.query(CampaignPackSession.session_id)
        .join(Session, Session.session_id == CampaignPackSession.session_id)
        .filter(
            CampaignPackSession.workspace_id == current_progress.workspace_id,
            CampaignPackSession.pack_id == pack_id,
            CampaignPackSession.multi_session_group_key == group_key,
            CampaignPackSession.session_id != session_id,
            or_(Session.status.is_(None), Session.status.notin_(['archived', 'deleted'])),
        )
        .order_by(CampaignPackSession.session_id.asc())
        .all()
    ]
    return sorted({int(session_id), *sibling_ids})


@contextmanager
def serialized_campaign_pack_progress(
    session_id: int,
    *,
    include_shared_progress: bool = True,
):
    """Acquire a fresh and complete lock set without retaining pre-wait rows.

    Lock discovery necessarily reads the live session snapshot. Keeping those
    ORM identities while waiting would let a later mutation overwrite a turn
    that committed before the waiter acquired its leases. This boundary drops
    the clean discovery session, acquires the deterministic lock set, then
    discovers membership again in a fresh scoped session. Membership changes
    are retried before any caller mutation runs.

    Clean callers release their discovery identities before waiting. Callers
    with unrelated pending writes retain those writes; only clean Session and
    CampaignPackSession identities used for membership are refreshed after the
    locks are held. The boundary never rolls back caller-owned dirty state.
    """

    primary_session_id = int(session_id)
    preserve_pending_writes = scoped_session_has_pending_writes()
    release_discovery_session = include_shared_progress and not preserve_pending_writes
    if release_discovery_session:
        release_clean_scoped_session(boundary='campaign-pack lock discovery')
    lock_session_ids = (
        campaign_pack_progress_lock_session_ids(primary_session_id)
        if include_shared_progress
        else [primary_session_id]
    )
    if release_discovery_session:
        release_clean_scoped_session(boundary='campaign-pack lock wait')

    while True:
        yielded = False
        try:
            with session_turn_coordinator.serialized_many(lock_session_ids) as waits:
                if not release_discovery_session:
                    _refresh_campaign_pack_membership_rows(primary_session_id)
                if include_shared_progress:
                    current_lock_session_ids = campaign_pack_progress_lock_session_ids(
                        primary_session_id
                    )
                    if current_lock_session_ids != lock_session_ids:
                        if release_discovery_session:
                            release_clean_scoped_session(
                                boundary='campaign-pack lock membership retry'
                            )
                        lock_session_ids = current_lock_session_ids
                        continue
                yielded = True
                yield CampaignPackProgressLockBoundary(
                    session_ids=tuple(lock_session_ids),
                    waits=waits,
                )
                return
        except SessionTurnTargetMissingError as exc:
            if yielded:
                raise
            if release_discovery_session:
                release_clean_scoped_session(boundary='campaign-pack missing lock retry')
            if exc.session_id == primary_session_id:
                raise
            lock_session_ids = (
                campaign_pack_progress_lock_session_ids(primary_session_id)
                if include_shared_progress
                else [primary_session_id]
            )
            if release_discovery_session:
                release_clean_scoped_session(boundary='campaign-pack missing lock wait')


def _refresh_campaign_pack_membership_rows(session_id: int) -> None:
    """Refresh lock-discovery rows without touching unrelated pending writes."""

    scoped_session = db.session()
    membership_rows = [
        value
        for value in (
            *scoped_session.new,
            *scoped_session.dirty,
            *scoped_session.deleted,
        )
        if (
            isinstance(value, Session)
            and int(value.session_id or 0) == session_id
        )
        or (
            isinstance(value, CampaignPackSession)
            and int(value.session_id or 0) == session_id
        )
    ]
    if membership_rows:
        raise RuntimeError(
            'Campaign-pack lock discovery cannot refresh pending session membership writes.'
        )

    with scoped_session.no_autoflush:
        (
            Session.query.filter_by(session_id=session_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        (
            CampaignPackSession.query.filter_by(session_id=session_id)
            .populate_existing()
            .with_for_update()
            .first()
        )

def _first(record: dict | None, *keys: str):
    if not isinstance(record, dict):
        return None
    for key in keys:
        if key in record:
            return record.get(key)
    return None


def _text(value) -> str:
    return str(value or '').strip()
