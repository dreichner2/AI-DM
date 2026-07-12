from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.services.campaign_pack_snapshot import migrate_campaign_pack_snapshot


PLAYER_PROGRESS_FLAG_KEYS = {
    'campaignPackActiveCheckpointId',
    'campaignPackCompletedCheckpointIds',
    'campaignPackSkippedCheckpointIds',
    'campaignPackFailedCheckpointIds',
    'campaignPackProgressRevision',
}

PUBLIC_PLAYER_CHARACTER_KEYS = (
    'id',
    'playerId',
    'player_id',
    'name',
    'characterName',
    'character_name',
    'race',
    'class',
    'class_',
    'level',
    'sex',
    'profileImage',
    'profile_image',
)

PUBLIC_PLAYER_COMBAT_KEYS = (
    'id',
    'playerId',
    'player_id',
    'name',
    'team',
    'kind',
    'class',
    'class_',
    'level',
    'conditions',
    'isAlive',
    'isConscious',
)


def filter_session_snapshot_for_player(
    snapshot: Any,
    *,
    private_player_ids: set[int] | frozenset[int] | None = None,
) -> Any:
    if not isinstance(snapshot, dict):
        return snapshot

    migrated, _migrations_applied = migrate_campaign_pack_snapshot(snapshot)
    filtered = deepcopy(migrated)
    owned_player_ids = _normalized_player_ids(private_player_ids)
    filtered.pop('stateChangeLedger', None)

    flags = filtered.get('flags') if isinstance(filtered.get('flags'), dict) else {}
    pack = filtered.get('campaignPack') if isinstance(filtered.get('campaignPack'), dict) else {}
    if pack:
        filtered['campaignPack'] = _player_pack_snapshot(pack, flags)
    if flags:
        filtered['flags'] = _player_flags(flags)

    for key in ('locations', 'knownNpcs', 'partyNpcs', 'quests'):
        if isinstance(filtered.get(key), list):
            filtered[key] = [record for record in filtered[key] if _record_player_visible(record)]

    if isinstance(filtered.get('playerCharacters'), list):
        filtered['playerCharacters'] = [
            _player_character_for_viewer(record, owned_player_ids)
            for record in filtered['playerCharacters']
            if isinstance(record, dict)
        ]

    combat = filtered.get('combat') if isinstance(filtered.get('combat'), dict) else None
    if combat is not None:
        # Legal actions are response-time, viewer-scoped projections. Never
        # trust or relay an imported/persisted copy across viewers.
        combat.pop('legalActions', None)
        combat.pop('legalActionsSchemaVersion', None)
        if isinstance(combat.get('participants'), list):
            combat['participants'] = [
                _combat_participant_for_viewer(record, owned_player_ids)
                for record in combat['participants']
                if isinstance(record, dict)
            ]

    return filtered


def _normalized_player_ids(values: set[int] | frozenset[int] | None) -> frozenset[int]:
    normalized = {
        parsed
        for value in values or ()
        if (parsed := _positive_int(value)) is not None and parsed > 0
    }
    return frozenset(normalized)


def _record_player_id(record: dict) -> int | None:
    direct = _positive_int(_first(record, 'playerId', 'player_id'))
    if direct is not None and direct > 0:
        return direct

    actor_id = _text(record.get('id')).lower()
    for prefix in ('player_', 'player-'):
        if actor_id.startswith(prefix):
            parsed = _positive_int(actor_id[len(prefix):])
            return parsed if parsed is not None and parsed > 0 else None
    return None


def _public_fields(record: dict, keys: tuple[str, ...]) -> dict:
    return {key: record[key] for key in keys if key in record}


def _player_character_for_viewer(record: dict, private_player_ids: frozenset[int]) -> dict:
    player_id = _record_player_id(record)
    if player_id is not None and player_id in private_player_ids:
        return record
    return _public_fields(record, PUBLIC_PLAYER_CHARACTER_KEYS)


def _combat_participant_for_viewer(record: dict, private_player_ids: frozenset[int]) -> dict:
    player_id = _record_player_id(record)
    team = _text(record.get('team')).lower()
    kind = _text(record.get('kind')).lower()
    is_player = player_id is not None or team == 'player' or kind in {'player', 'player_character'}
    if not is_player or (player_id is not None and player_id in private_player_ids):
        return record
    payload = _public_fields(record, PUBLIC_PLAYER_COMBAT_KEYS)
    hp = record.get('hp') if isinstance(record.get('hp'), dict) else {}
    public_hp = {
        key: hp[key]
        for key in ('current', 'max', 'currentHp', 'maxHp')
        if key in hp
    }
    if public_hp:
        payload['hp'] = public_hp
    return payload


def _player_pack_snapshot(pack: dict, flags: dict) -> dict:
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
    checkpoint_statuses = _checkpoint_statuses(
        [checkpoint for checkpoint in (pack.get('checkpoints') or []) if isinstance(checkpoint, dict)],
        active_id=active_id,
        completed_ids=completed_ids,
        skipped_ids=skipped_ids,
        failed_ids=failed_ids,
    )

    result = {
        'packId': _text(_first(pack, 'packId', 'pack_id')),
        'title': _text(_first(pack, 'title', 'name')),
        'schemaVersion': _text(_first(pack, 'schemaVersion', 'schema_version')) or '1',
        'version': _text(_first(pack, 'version')),
        'source': _text(_first(pack, 'source')) or 'campaign_pack',
        'visibility': 'player',
        'progressSchemaVersion': _positive_int(_first(pack, 'progressSchemaVersion', 'progress_schema_version')) or 1,
        'progressRevision': _progress_revision(pack, flags),
        'activeCheckpointId': active_id or None,
        'completedCheckpointIds': completed_ids,
        'skippedCheckpointIds': skipped_ids,
        'failedCheckpointIds': failed_ids,
        'checkpointStatuses': checkpoint_statuses,
        'checkpoints': _player_visible_checkpoints(pack.get('checkpoints'), checkpoint_statuses),
    }
    return {key: value for key, value in result.items() if value not in ('', None)}


def _player_visible_checkpoints(value: Any, checkpoint_statuses: dict[str, str]) -> list[dict]:
    checkpoints = [checkpoint for checkpoint in (value or []) if isinstance(checkpoint, dict)]
    visible: list[dict] = []
    for checkpoint in checkpoints:
        checkpoint_id = _checkpoint_id(checkpoint)
        if not checkpoint_id:
            continue
        status = checkpoint_statuses.get(checkpoint_id) or 'open'
        if status not in {'active', 'completed', 'skipped', 'failed'} and not _truthy(
            _first(
                checkpoint,
                'visibleToPlayers',
                'visible_to_players',
                'knownToPlayers',
                'known_to_players',
                'playerVisible',
                'player_visible',
            )
        ):
            continue

        payload = {
            'id': checkpoint_id,
            'status': status,
        }
        title = _text(
            _first(checkpoint, 'playerTitle', 'player_title', 'publicTitle', 'public_title')
            or _first(checkpoint, 'title', 'name')
        )
        if title:
            payload['title'] = title
        summary = _text(
            _first(checkpoint, 'playerSummary', 'player_summary', 'publicSummary', 'public_summary')
            or (_first(checkpoint, 'summary', 'description') if status in {'active', 'completed', 'skipped', 'failed'} else '')
        )
        if summary:
            payload['summary'] = summary
        if _truthy(_first(checkpoint, 'optional', 'isOptional', 'is_optional')):
            payload['optional'] = True
        visible.append(payload)
    return visible


def _player_flags(flags: dict) -> dict:
    return {key: flags[key] for key in PLAYER_PROGRESS_FLAG_KEYS if key in flags}


def _record_player_visible(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    metadata = record.get('metadata') if isinstance(record.get('metadata'), dict) else {}
    for source in (record, metadata):
        if _truthy(
            _first(
                source,
                'hiddenToPlayers',
                'hidden_to_players',
                'dmOnly',
                'dm_only',
                'secret',
            )
        ):
            return False
        explicit_visibility = _first(
            source,
            'knownToPlayers',
            'known_to_players',
            'visibleToPlayers',
            'visible_to_players',
            'playerVisible',
            'player_visible',
        )
        if explicit_visibility is not None and not _truthy(explicit_visibility):
            return False
    return True


def _checkpoint_statuses(
    checkpoints: list[dict],
    *,
    active_id: str,
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
) -> dict[str, str]:
    active_key = _id_key(active_id)
    completed_keys = {_id_key(value) for value in completed_ids}
    skipped_keys = {_id_key(value) for value in skipped_ids}
    failed_keys = {_id_key(value) for value in failed_ids}
    statuses: dict[str, str] = {}
    for checkpoint in checkpoints:
        checkpoint_id = _checkpoint_id(checkpoint)
        if not checkpoint_id:
            continue
        key = _id_key(checkpoint_id)
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


def _progress_revision(pack: dict, flags: dict) -> int:
    for value in (
        _first(pack, 'progressRevision', 'progress_revision'),
        _first(flags, 'campaignPackProgressRevision', 'progressRevision', 'progress_revision'),
    ):
        revision = _positive_int(value)
        if revision is not None:
            return revision
    return 0


def _checkpoint_id(checkpoint: dict) -> str:
    return _text(_first(checkpoint, 'id', 'checkpointId', 'checkpoint_id'))


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


def _unique_ids(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if not text:
            continue
        key = _id_key(text)
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


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower().replace(' ', '_') in {'1', 'true', 'yes', 'y', 'on', 'known', 'visible'}


def _text(value: Any) -> str:
    return str(value or '').strip()


def _id_key(value: Any) -> str:
    return _text(value).lower()
