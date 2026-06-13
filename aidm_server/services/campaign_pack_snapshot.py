from __future__ import annotations

from copy import deepcopy
from typing import Any


CAMPAIGN_PACK_SNAPSHOT_SCHEMA_VERSION = 1
CAMPAIGN_PACK_PROGRESS_SCHEMA_VERSION = 1
CAMPAIGN_PACK_PROGRESS_EVENTS_VERSION = 1


def migrate_campaign_pack_snapshot(snapshot: Any) -> tuple[Any, list[str]]:
    if not isinstance(snapshot, dict):
        return snapshot, []
    pack = snapshot.get('campaignPack') if isinstance(snapshot.get('campaignPack'), dict) else None
    if pack is None:
        return snapshot, []

    migrated = deepcopy(snapshot)
    pack = migrated.get('campaignPack')
    flags = migrated.get('flags') if isinstance(migrated.get('flags'), dict) else {}
    migrated['flags'] = flags
    applied: list[str] = []

    if _positive_int(pack.get('snapshotSchemaVersion')) != CAMPAIGN_PACK_SNAPSHOT_SCHEMA_VERSION:
        pack['snapshotSchemaVersion'] = CAMPAIGN_PACK_SNAPSHOT_SCHEMA_VERSION
        applied.append('campaign_pack.snapshot_schema_v1')
    if _positive_int(pack.get('progressSchemaVersion')) != CAMPAIGN_PACK_PROGRESS_SCHEMA_VERSION:
        pack['progressSchemaVersion'] = CAMPAIGN_PACK_PROGRESS_SCHEMA_VERSION
        applied.append('campaign_pack.progress_schema_v1')
    if _positive_int(pack.get('progressEventsVersion')) != CAMPAIGN_PACK_PROGRESS_EVENTS_VERSION:
        pack['progressEventsVersion'] = CAMPAIGN_PACK_PROGRESS_EVENTS_VERSION
        applied.append('campaign_pack.progress_events_v1')

    active_id = _text(
        _first(pack, 'activeCheckpointId', 'active_checkpoint_id', 'currentCheckpointId', 'current_checkpoint_id')
        or _first(flags, 'campaignPackActiveCheckpointId', 'activeCheckpointId')
    )
    completed_ids = _ids_from(pack, 'completedCheckpointIds', 'completed_checkpoint_ids') or _ids_from(
        flags,
        'campaignPackCompletedCheckpointIds',
        'completedCheckpointIds',
    )
    skipped_ids = _ids_from(pack, 'skippedCheckpointIds', 'skipped_checkpoint_ids') or _ids_from(
        flags,
        'campaignPackSkippedCheckpointIds',
        'skippedCheckpointIds',
    )
    failed_ids = _ids_from(pack, 'failedCheckpointIds', 'failed_checkpoint_ids') or _ids_from(
        flags,
        'campaignPackFailedCheckpointIds',
        'failedCheckpointIds',
    )
    revision = _progress_revision(pack, flags)

    applied.extend(_sync_value(pack, 'activeCheckpointId', active_id or None, aliases=('currentCheckpointId',)))
    applied.extend(_sync_value(flags, 'campaignPackActiveCheckpointId', active_id or None))
    applied.extend(_sync_value(pack, 'completedCheckpointIds', completed_ids))
    applied.extend(_sync_value(flags, 'campaignPackCompletedCheckpointIds', completed_ids))
    applied.extend(_sync_value(pack, 'skippedCheckpointIds', skipped_ids))
    applied.extend(_sync_value(flags, 'campaignPackSkippedCheckpointIds', skipped_ids))
    applied.extend(_sync_value(pack, 'failedCheckpointIds', failed_ids))
    applied.extend(_sync_value(flags, 'campaignPackFailedCheckpointIds', failed_ids))
    applied.extend(_sync_value(pack, 'progressRevision', revision))
    applied.extend(_sync_value(flags, 'campaignPackProgressRevision', revision))

    catalog = pack.get('catalog') if isinstance(pack.get('catalog'), dict) else {}
    if not isinstance(pack.get('catalog'), dict):
        pack['catalog'] = catalog
        applied.append('campaign_pack.catalog_object')
    for key in ('locations', 'npcs', 'quests', 'enemies', 'encounters'):
        if key not in catalog and isinstance(pack.get(key), list):
            catalog[key] = pack.get(key)
            applied.append(f'campaign_pack.catalog_{key}')

    existing_migrations = _string_list(pack.get('migrationsApplied'))
    merged_migrations = _unique_ids([*existing_migrations, *applied])
    if merged_migrations != existing_migrations:
        pack['migrationsApplied'] = merged_migrations

    migrated['campaignPack'] = pack
    return migrated, _unique_ids(applied)


def _sync_value(record: dict, key: str, value: Any, *, aliases: tuple[str, ...] = ()) -> list[str]:
    changed = []
    for alias in aliases:
        if alias in record:
            record.pop(alias, None)
            changed.append(f'campaign_pack.remove_{alias}')
    if record.get(key) != value:
        record[key] = value
        changed.append(f'campaign_pack.{key}')
    return changed


def _progress_revision(pack: dict, flags: dict) -> int:
    for value in (
        _first(pack, 'progressRevision', 'progress_revision'),
        _first(flags, 'campaignPackProgressRevision', 'progressRevision', 'progress_revision'),
    ):
        revision = _positive_int(value)
        if revision is not None:
            return revision
    return 0


def _ids_from(record: dict, *keys: str) -> list[str]:
    if not isinstance(record, dict):
        return []
    values: list[Any] = []
    for key in keys:
        if key not in record:
            continue
        value = record.get(key)
        if isinstance(value, str):
            values.extend(item.strip() for item in value.replace(';', ',').split(','))
        elif isinstance(value, list):
            values.extend(value)
        elif value not in (None, ''):
            values.append(value)
    return _unique_ids([_text(value) for value in values if _text(value)])


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.replace(';', ',').split(',')
    if not isinstance(value, list):
        return []
    return _unique_ids([_text(item) for item in value if _text(item)])


def _unique_ids(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _first(record: dict | None, *keys: str) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        if key in record:
            return record.get(key)
    return None


def _text(value: Any) -> str:
    return str(value or '').strip()
