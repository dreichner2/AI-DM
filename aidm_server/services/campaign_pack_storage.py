from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import or_

from aidm_server.database import db
from aidm_server.models import (
    Campaign,
    CampaignPack,
    CampaignPackCheckpointProgress,
    CampaignPackProgressEvent,
    CampaignPackRecord,
    CampaignPackSession,
    InstalledCampaignPack,
    Session,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.services.campaign_pack_coordination import (
    campaign_pack_progress_lock_session_ids as _campaign_pack_progress_lock_session_ids,
    require_campaign_pack_progress_locks,
)
from aidm_server.services.session_state_mutation import record_session_state_mutation_audit
from aidm_server.time_utils import utc_now


def campaign_pack_progress_lock_session_ids(session_id: int) -> list[int]:
    """Compatibility export for callers that used the storage module."""

    return _campaign_pack_progress_lock_session_ids(session_id)


def upsert_campaign_pack_definition(
    *,
    workspace_id: str,
    installed_pack: InstalledCampaignPack | None,
    pack_id: str,
    title: str,
    version: str,
    schema_version: str,
    pack_hash: str,
    manifest: dict[str, Any],
    records_by_type: dict[str, list[dict[str, Any]]],
    validated_at,
) -> CampaignPack:
    pack_row = CampaignPack.query.filter_by(workspace_id=workspace_id, pack_hash=pack_hash).first()
    if pack_row is None:
        pack_row = CampaignPack(
            workspace_id=workspace_id,
            pack_hash=pack_hash,
            created_at=validated_at,
        )
        db.session.add(pack_row)

    pack_row.installed_pack_id = installed_pack.installed_pack_id if installed_pack else None
    pack_row.pack_id = pack_id
    pack_row.title = title
    pack_row.pack_version = version
    pack_row.schema_version = schema_version
    pack_row.manifest_json = safe_json_dumps(manifest, {})
    pack_row.updated_at = validated_at
    db.session.flush()

    CampaignPackRecord.query.filter_by(campaign_pack_id=pack_row.campaign_pack_id).delete()
    for record_type, records in records_by_type.items():
        for sort_order, record in enumerate(records):
            record_id = _record_id(record, fallback=f'{record_type}_{sort_order + 1}')
            db.session.add(
                CampaignPackRecord(
                    campaign_pack_id=pack_row.campaign_pack_id,
                    workspace_id=workspace_id,
                    pack_id=pack_id,
                    record_type=record_type,
                    record_id=record_id,
                    title=_record_title(record),
                    visibility=_record_visibility(record),
                    sort_order=sort_order,
                    record_json=safe_json_dumps(record, {}),
                    created_at=validated_at,
                    updated_at=validated_at,
                )
            )
    db.session.flush()
    return pack_row


def sync_campaign_pack_progress(
    *,
    session: Session,
    pack: dict[str, Any],
    checkpoints: list[dict[str, Any]],
    active_checkpoint_id: str | None,
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
    progress_revision: int,
    campaign_pack: CampaignPack | None = None,
    installed_pack: InstalledCampaignPack | None = None,
) -> CampaignPackSession:
    campaign = session.campaign or db.session.get(Campaign, session.campaign_id)
    workspace_id = campaign.workspace_id if campaign else 'owner'
    pack_id = _text(_first(pack, 'packId', 'pack_id')) or 'unknown_pack'
    now = utc_now()
    progress_row = CampaignPackSession.query.filter_by(session_id=session.session_id).first()
    if progress_row is None:
        progress_row = CampaignPackSession(
            session_id=session.session_id,
            campaign_id=session.campaign_id,
            workspace_id=workspace_id,
            pack_id=pack_id,
            created_at=now,
        )
        db.session.add(progress_row)

    progress_row.campaign_pack_id = campaign_pack.campaign_pack_id if campaign_pack else progress_row.campaign_pack_id
    progress_row.installed_pack_id = installed_pack.installed_pack_id if installed_pack else progress_row.installed_pack_id
    progress_row.campaign_id = session.campaign_id
    progress_row.workspace_id = workspace_id
    progress_row.pack_id = pack_id
    progress_row.pack_title = _text(_first(pack, 'title', 'name')) or None
    progress_row.pack_version = _text(_first(pack, 'version', 'packVersion', 'pack_version')) or None
    progress_row.active_checkpoint_id = active_checkpoint_id
    progress_row.progress_revision = max(0, int(progress_revision or 0))
    progress_row.snapshot_schema_version = _positive_int(_first(pack, 'snapshotSchemaVersion', 'snapshot_schema_version')) or 1
    progress_row.progress_schema_version = _positive_int(_first(pack, 'progressSchemaVersion', 'progress_schema_version')) or 1
    progress_row.progress_events_version = _positive_int(_first(pack, 'progressEventsVersion', 'progress_events_version')) or 1
    progress_row.status = session.status or 'active'
    progress_row.multi_session_group_key = _text(_first(pack, 'multiSessionGroupKey', 'multi_session_group_key')) or None
    gm_notes = _first(pack, 'gmNotes', 'gm_notes', 'hiddenNotes', 'hidden_notes', 'hiddenSceneNotes', 'hidden_scene_notes')
    progress_row.gm_notes_json = safe_json_dumps(gm_notes, None) if gm_notes not in (None, '') else None
    progress_row.updated_at = now
    db.session.flush()

    _sync_checkpoint_rows(
        progress_row=progress_row,
        checkpoints=checkpoints,
        active_checkpoint_id=active_checkpoint_id,
        completed_ids=completed_ids,
        skipped_ids=skipped_ids,
        failed_ids=failed_ids,
        progress_revision=progress_row.progress_revision,
        now=now,
    )
    return progress_row


def record_campaign_pack_progress_event(
    *,
    session: Session,
    turn_event_id: int | None,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
) -> int | None:
    snapshot = safe_json_loads(session.state_snapshot, {})
    if not isinstance(snapshot, dict):
        return None
    pack = snapshot.get('campaignPack') if isinstance(snapshot.get('campaignPack'), dict) else {}
    checkpoints = [checkpoint for checkpoint in (pack.get('checkpoints') or []) if isinstance(checkpoint, dict)]
    if not pack or not checkpoints:
        return None

    progress_revision = _positive_int(payload.get('progressRevision')) or 0
    progress_row = sync_campaign_pack_progress(
        session=session,
        pack=pack,
        checkpoints=checkpoints,
        active_checkpoint_id=_text(payload.get('toCheckpointId')) or None,
        completed_ids=_string_list(payload.get('completedCheckpointIds')),
        skipped_ids=_string_list(payload.get('skippedCheckpointIds')),
        failed_ids=_string_list(payload.get('failedCheckpointIds')),
        progress_revision=progress_revision,
    )
    if idempotency_key:
        existing = CampaignPackProgressEvent.query.filter_by(
            campaign_pack_session_id=progress_row.campaign_pack_session_id,
            idempotency_key=idempotency_key,
        ).first()
        if existing:
            return existing.progress_event_id

    now = utc_now()
    progress_event = CampaignPackProgressEvent(
        campaign_pack_session_id=progress_row.campaign_pack_session_id,
        session_id=session.session_id,
        campaign_id=session.campaign_id,
        turn_id=_positive_int(payload.get('turnId')) or None,
        turn_event_id=turn_event_id,
        event_type=_text(payload.get('type')) or 'campaign_pack.progress.changed',
        action=_text(payload.get('action')) or 'progress_changed',
        actor=_text(payload.get('actor')) or None,
        from_checkpoint_id=_text(payload.get('fromCheckpointId')) or None,
        to_checkpoint_id=_text(payload.get('toCheckpointId')) or None,
        reason=_text(payload.get('reason')) or None,
        progress_revision=progress_revision,
        idempotency_key=idempotency_key,
        payload_json=safe_json_dumps(payload, {}),
        created_at=now,
    )
    db.session.add(progress_event)
    db.session.flush()
    return progress_event.progress_event_id


def propagate_shared_campaign_pack_progress(
    *,
    session: Session,
    pack: dict[str, Any],
    active_checkpoint_id: str | None,
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
    progress_revision: int,
    reason: str | None,
    actor: str | None,
) -> int:
    group_key = _text(_first(pack, 'multiSessionGroupKey', 'multi_session_group_key'))
    pack_id = _text(_first(pack, 'packId', 'pack_id'))
    if not group_key or not pack_id:
        return 0

    current_progress = CampaignPackSession.query.filter_by(session_id=session.session_id).first()
    if current_progress is None:
        return 0

    siblings = (
        CampaignPackSession.query.join(
            Session,
            Session.session_id == CampaignPackSession.session_id,
        ).filter(
            CampaignPackSession.workspace_id == current_progress.workspace_id,
            CampaignPackSession.pack_id == pack_id,
            CampaignPackSession.multi_session_group_key == group_key,
            CampaignPackSession.session_id != session.session_id,
            or_(Session.status.is_(None), Session.status.notin_(['archived', 'deleted'])),
        )
        .order_by(CampaignPackSession.campaign_pack_session_id.asc())
        .all()
    )
    if not siblings:
        return 0
    require_campaign_pack_progress_locks(
        sibling_progress.session_id for sibling_progress in siblings
    )

    now = utc_now().isoformat()
    propagated = 0
    for sibling_progress in siblings:
        sibling_session = db.session.get(
            Session,
            sibling_progress.session_id,
            populate_existing=True,
            with_for_update=True,
        )
        if not sibling_session or sibling_session.status in {'archived', 'deleted'}:
            continue
        snapshot = safe_json_loads(sibling_session.state_snapshot, {})
        if not isinstance(snapshot, dict):
            continue
        sibling_pack = snapshot.get('campaignPack') if isinstance(snapshot.get('campaignPack'), dict) else {}
        sibling_group = _text(_first(sibling_pack, 'multiSessionGroupKey', 'multi_session_group_key'))
        sibling_pack_id = _text(_first(sibling_pack, 'packId', 'pack_id'))
        if sibling_pack_id != pack_id or sibling_group != group_key:
            continue
        checkpoints = [checkpoint for checkpoint in (sibling_pack.get('checkpoints') or []) if isinstance(checkpoint, dict)]
        if not checkpoints:
            continue
        flags = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}
        previous_revision = _progress_revision(sibling_pack, flags)
        if previous_revision > progress_revision:
            continue
        before_snapshot = deepcopy(snapshot)
        flags['campaignPackActiveCheckpointId'] = active_checkpoint_id
        flags['campaignPackCompletedCheckpointIds'] = completed_ids
        flags['campaignPackSkippedCheckpointIds'] = skipped_ids
        flags['campaignPackFailedCheckpointIds'] = failed_ids
        flags['campaignPackProgressRevision'] = progress_revision
        flags['campaignPackSharedProgressSourceSessionId'] = session.session_id
        flags['campaignPackSharedProgressAt'] = now
        flags['campaignPackSharedProgressActor'] = actor
        flags['campaignPackSharedProgressReason'] = reason
        sibling_pack['activeCheckpointId'] = active_checkpoint_id
        sibling_pack['completedCheckpointIds'] = completed_ids
        sibling_pack['skippedCheckpointIds'] = skipped_ids
        sibling_pack['failedCheckpointIds'] = failed_ids
        sibling_pack['progressRevision'] = progress_revision
        sibling_pack['lastSharedProgressSourceSessionId'] = session.session_id
        sibling_pack['lastSharedProgressAt'] = now
        sibling_pack['lastProgressReason'] = reason
        snapshot['flags'] = flags
        snapshot['campaignPack'] = sibling_pack
        sibling_session.state_snapshot = safe_json_dumps(snapshot, {})
        sync_campaign_pack_progress(
            session=sibling_session,
            pack=sibling_pack,
            checkpoints=checkpoints,
            active_checkpoint_id=active_checkpoint_id,
            completed_ids=completed_ids,
            skipped_ids=skipped_ids,
            failed_ids=failed_ids,
            progress_revision=progress_revision,
            campaign_pack=sibling_progress.campaign_pack,
            installed_pack=sibling_progress.installed_pack,
        )
        record_session_state_mutation_audit(
            session_obj=sibling_session,
            before_state=before_snapshot,
            after_state=snapshot,
            source='system.campaign_pack.shared_progress',
            actor=actor or 'system',
            previous_revision=previous_revision,
            state_revision=progress_revision,
            applied_changes=[
                {
                    'id': f'campaign_pack.shared_progress.{pack_id}.{progress_revision}',
                    'type': 'campaign_pack.progress.shared',
                }
            ],
            rejected_count=0,
            metadata={
                'packId': pack_id,
                'sourceSessionId': session.session_id,
                'reason': reason,
            },
        )
        propagated += 1
    return propagated


def _progress_revision(pack: dict[str, Any], flags: dict[str, Any]) -> int:
    for value in (
        _first(pack, 'progressRevision', 'progress_revision'),
        _first(flags, 'campaignPackProgressRevision', 'progressRevision', 'progress_revision'),
    ):
        revision = _positive_int(value)
        if revision is not None:
            return revision
    return 0


def _sync_checkpoint_rows(
    *,
    progress_row: CampaignPackSession,
    checkpoints: list[dict[str, Any]],
    active_checkpoint_id: str | None,
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
    progress_revision: int,
    now,
) -> None:
    existing_rows = {
        _id_key(row.checkpoint_id): row
        for row in CampaignPackCheckpointProgress.query.filter_by(
            campaign_pack_session_id=progress_row.campaign_pack_session_id
        ).all()
    }
    statuses = _checkpoint_statuses(
        checkpoints=checkpoints,
        active_checkpoint_id=active_checkpoint_id,
        completed_ids=completed_ids,
        skipped_ids=skipped_ids,
        failed_ids=failed_ids,
    )
    for sort_order, checkpoint in enumerate(checkpoints):
        checkpoint_id = _record_id(checkpoint, fallback=f'checkpoint_{sort_order + 1}')
        row = existing_rows.get(_id_key(checkpoint_id))
        if row is None:
            row = CampaignPackCheckpointProgress(
                campaign_pack_session_id=progress_row.campaign_pack_session_id,
                checkpoint_id=checkpoint_id,
                created_at=now,
            )
            db.session.add(row)
        previous_status = row.status
        next_status = statuses.get(checkpoint_id) or 'open'
        row.title = _record_title(checkpoint)
        row.status = next_status
        row.sort_order = sort_order
        row.progress_revision = progress_revision
        row.metadata_json = safe_json_dumps(_checkpoint_metadata(checkpoint), {})
        row.updated_at = now
        if next_status == 'active' and previous_status != 'active' and row.activated_at is None:
            row.activated_at = now
        if next_status == 'completed' and previous_status != 'completed' and row.completed_at is None:
            row.completed_at = now
        if next_status == 'skipped' and previous_status != 'skipped' and row.skipped_at is None:
            row.skipped_at = now
        if next_status == 'failed' and previous_status != 'failed' and row.failed_at is None:
            row.failed_at = now
    db.session.flush()


def _checkpoint_statuses(
    *,
    checkpoints: list[dict[str, Any]],
    active_checkpoint_id: str | None,
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
) -> dict[str, str]:
    active_key = _id_key(active_checkpoint_id)
    completed_keys = {_id_key(value) for value in completed_ids}
    skipped_keys = {_id_key(value) for value in skipped_ids}
    failed_keys = {_id_key(value) for value in failed_ids}
    statuses: dict[str, str] = {}
    for checkpoint in checkpoints:
        checkpoint_id = _record_id(checkpoint, fallback='')
        key = _id_key(checkpoint_id)
        if not checkpoint_id:
            continue
        if key == active_key:
            statuses[checkpoint_id] = 'active'
        elif key in failed_keys:
            statuses[checkpoint_id] = 'failed'
        elif key in skipped_keys:
            statuses[checkpoint_id] = 'skipped'
        elif key in completed_keys:
            statuses[checkpoint_id] = 'completed'
        elif _truthy(_first(checkpoint, 'optional', 'isOptional', 'is_optional')):
            statuses[checkpoint_id] = 'optional'
        else:
            statuses[checkpoint_id] = 'open'
    return statuses


def _checkpoint_metadata(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        key: checkpoint.get(key)
        for key in (
            'locationIds',
            'questIds',
            'npcIds',
            'encounterIds',
            'objectiveIds',
            'segmentIds',
            'nextCheckpointIds',
            'alternateCheckpointIds',
            'prerequisiteCheckpointIds',
            'prerequisitePolicy',
            'failureCheckpointIds',
            'optional',
            'terminal',
            'chapter',
            'act',
            'priority',
            'gate',
            'canCompleteOutOfOrder',
            'rejoinTargetCheckpointId',
            'playerTitle',
            'playerSummary',
        )
        if key in checkpoint
    }


def _record_id(record: dict[str, Any], *, fallback: str) -> str:
    return _text(_first(record, 'id', 'checkpointId', 'checkpoint_id', 'recordId', 'record_id')) or fallback


def _record_title(record: dict[str, Any]) -> str | None:
    return _text(_first(record, 'title', 'name', 'label')) or None


def _record_visibility(record: dict[str, Any]) -> str:
    if _truthy(_first(record, 'visibleAtStart', 'visible_at_start', 'knownToPlayers', 'known_to_players')):
        return 'player'
    if _truthy(_first(record, 'playerVisible', 'player_visible', 'visibleToPlayers', 'visible_to_players')):
        return 'player'
    return 'dm'


def _first(record: dict[str, Any], *keys: str) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        if key in record:
            return record.get(key)
    return None


def _text(value: Any) -> str:
    return str(value or '').strip()


def _id_key(value: Any) -> str:
    return _text(value).lower()


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_values = [item.strip() for item in value.replace(';', ',').split(',')]
    elif isinstance(value, list):
        raw_values = value
    elif value in (None, ''):
        raw_values = []
    else:
        raw_values = [value]
    result: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        text = _text(raw_value)
        key = _id_key(text)
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {'1', 'true', 'yes', 'y', 'on', 'known', 'visible', 'public'}
