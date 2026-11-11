"""Importing saves: a read-only archive by default, or a restored chapter.

An ``aidm.save`` version 2 document comes from ``GET /api/sessions/<id>/export``.
The older client-assembled export shape is still accepted as an archive.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from typing import Any

from aidm_server.database import db
from aidm_server.game_state.models import CURRENCY_STAT_KEYS, dump_inventory_items, load_inventory_items
from aidm_server.models import (
    Campaign,
    Player,
    Session,
    SessionLogEntry,
    SessionState,
    TurnEvent,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.operator_audit import record_operator_action
from aidm_server.response_dtos import ARCHIVE_NAME_PREFIX, ENDED_STATUS, session_is_open, session_payload
from aidm_server.services import session_chapters
from aidm_server.services.campaign_pack_snapshot import migrate_campaign_pack_snapshot
from aidm_server.spellbook import character_sheet_record
from aidm_server.time_utils import utc_now
from aidm_server.turn_coordinator import SessionTurnTargetMissingError, TurnLeaseLostError, session_turn_coordinator
from aidm_server.turn_events import STATE_UPDATE_EVENT, project_turn_event, record_turn_event
from aidm_server.validation import coerce_int

SAVE_FORMAT = 'aidm.save'
SAVE_VERSION = 2
IMPORT_ARCHIVE = 'archive'
IMPORT_RESTORE = 'restore'
IMPORT_MODES = (IMPORT_ARCHIVE, IMPORT_RESTORE)
RESTORED_MID_SCENE_MESSAGE = (
    'Restored from a save taken mid-scene: the interrupted fight or roll was not carried over.'
)

MAX_IMPORTED_EVENTS = 1000
MAX_IMPORTED_LOG_ENTRIES = 1000
MAX_IMPORTED_TEXT_LENGTH = 20_000
MAX_IMPORTED_NAME_LENGTH = 80
MAX_IMPORTED_STATE_LIST_ITEMS = 100
MAX_IMPORTED_STATE_NESTED_ITEMS = 50
MAX_IMPORTED_STATE_NESTED_TEXT_LENGTH = 2_000
MAX_IMPORTED_STATE_NESTED_DEPTH = 4
VALID_LOG_ENTRY_TYPES = {'dm', 'player', 'system'}
CAMPAIGN_PACK_SNAPSHOT_KEYS = {'campaignPack', 'campaign_pack'}
CAMPAIGN_PACK_METADATA_FLAG = 'campaignPackStateStripped'

# Mechanical Player fields a restore may overwrite: hit points, XP, currency,
# class feature uses, conditions (Player.stats); spell and class resources
# (Player.character_sheet); level and inventory are columns.
RESTORED_STAT_KEYS = (
    'current_hp',
    'hp_current',
    'max_hp',
    'hp_max',
    'temp_hp',
    'xp',
    'experience',
    *CURRENCY_STAT_KEYS.values(),
    'class_feature_state',
    'conditions',
    'death_saves',
)
RESTORED_SHEET_KEYS = ('spellResources', 'classFeatureState')


class SessionImportError(ValueError):
    def __init__(self, message: str, *, error_code: str = 'validation_error', status_code: int = 400):
        super().__init__(message)
        self.public_message = message
        self.error_code = error_code
        self.status_code = status_code


@dataclass(frozen=True)
class SessionImportResult:
    payload: dict


def _not_restorable(reason: str) -> SessionImportError:
    return SessionImportError(reason, error_code='save_not_restorable', status_code=409)


def save_document(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return ``payload`` when it is a version 2 save, None for the legacy export shape."""

    if payload.get('format') != SAVE_FORMAT:
        return None
    if payload.get('version') != SAVE_VERSION:
        raise SessionImportError('This save was written by a different version of AI-DM and cannot be imported.')
    return payload


def import_session_export(
    payload: dict[str, Any],
    *,
    workspace_id: str | None = None,
    allow_campaign_pack_state: bool = True,
    required_player_account_id: int | None = None,
) -> SessionImportResult:
    """Import a save as a read-only archive: an ended session that never becomes current.

    Character rows are never touched.
    """

    if not isinstance(payload, dict):
        raise SessionImportError('Expected JSON request body.')
    document = save_document(payload)
    campaign = _workspace_campaign(_campaign_id(payload), workspace_id)

    now = utc_now()
    if document is not None:
        source_session = _record(document.get('session'))
        exported_at = _optional_text(document.get('exported_at'), max_length=80)
        source_snapshot = _record(document.get('state_snapshot'))
        raw_session_state = document.get('session_state')
    else:
        source_session = _record(payload.get('selectedSession'))
        exported_at = _optional_text(payload.get('exportedAt'), max_length=80)
        source_snapshot = _record(source_session.get('state_snapshot'))
        raw_session_state = payload.get('sessionState')

    session_obj = Session(
        campaign_id=campaign.campaign_id,
        name=_session_name(payload, source_session),
        status=ENDED_STATUS,
        state_snapshot=safe_json_dumps({}, {}),
        created_at=now,
        updated_at=now,
    )
    db.session.add(session_obj)
    db.session.flush()
    snapshot = _imported_state_snapshot(
        source_snapshot=source_snapshot,
        session_obj=session_obj,
        campaign=campaign,
        exported_at=exported_at,
        source_session_id=_coerce_positive(source_session.get('session_id')),
        imported_at=now,
        allow_campaign_pack_state=allow_campaign_pack_state,
    )
    if not isinstance(snapshot.get('ended_at'), str):
        snapshot['ended_at'] = now.isoformat()
    session_obj.state_snapshot = safe_json_dumps(snapshot, {})

    state_imported = _import_session_state(session_obj, campaign, raw_session_state, now)
    events_imported = projected_log_entries = log_entries_imported = 0
    if document is not None:
        log_entries_imported = _import_log_entries(session_obj, _list(document.get('log'))[:MAX_IMPORTED_LOG_ENTRIES])
    else:
        events_imported, projected_log_entries = _import_turn_events(
            session_obj,
            campaign,
            _list(payload.get('turnEvents'))[:MAX_IMPORTED_EVENTS],
            allow_campaign_pack_events=allow_campaign_pack_state,
            required_player_account_id=required_player_account_id,
        )
        if events_imported == 0:
            log_entries_imported = _import_log_entries(
                session_obj,
                _list(payload.get('logEntries'))[:MAX_IMPORTED_LOG_ENTRIES],
            )

    counts = {
        'turn_events': events_imported,
        'projected_log_entries': projected_log_entries,
        'log_entries': log_entries_imported,
        'session_state': 1 if state_imported else 0,
    }
    record_operator_action(
        action='session.import',
        resource_type='session',
        workspace_id=campaign.workspace_id or 'owner',
        campaign_id=campaign.campaign_id,
        session_id=session_obj.session_id,
        resource_id=session_obj.session_id,
        details={
            'mode': IMPORT_ARCHIVE,
            'saveVersion': SAVE_VERSION if document is not None else None,
            'turnEventsImported': events_imported,
            'projectedLogEntries': projected_log_entries,
            'logEntriesImported': log_entries_imported,
            'sessionStateImported': bool(state_imported),
            'campaignPackStateAllowed': allow_campaign_pack_state,
        },
    )
    return SessionImportResult(payload=_import_payload(session_obj, IMPORT_ARCHIVE, counts, restored_player_ids=[]))


def restore_session_save(
    payload: dict[str, Any],
    *,
    workspace_id: str | None,
    operator: bool,
    restore_characters: bool,
) -> SessionImportResult:
    """Start a new chapter of the save's own campaign from the saved world, and commit it.

    The chapter starts through ``session_chapters`` exactly as a continued
    chapter does (same carried keys, the current chapter closes, transient
    combat and pending rolls stay behind) and is fenced the same way.
    """

    if not isinstance(payload, dict):
        raise SessionImportError('Expected JSON request body.')
    document = save_document(payload)
    if document is None:
        raise _not_restorable('Only a save exported by this version of AI-DM can be restored as a chapter.')
    if not operator:
        raise _not_restorable("Only the campaign's operators can restore a save as a chapter.")
    saved_campaign_id = _coerce_positive(_record(document.get('campaign')).get('id'))
    target_campaign_id = _coerce_positive(payload.get('campaign_id') or payload.get('campaignId')) or saved_campaign_id
    if saved_campaign_id is None or saved_campaign_id != target_campaign_id:
        raise _not_restorable(
            'This save belongs to a different campaign. Import it as a read-only archive instead.'
        )
    campaign_id = _workspace_campaign(saved_campaign_id, workspace_id).campaign_id

    try:
        session_chapters._locked_active_campaign(campaign_id, workspace_id)
        fence_session_ids = session_chapters._chapter_fence_session_ids(campaign_id)
    finally:
        db.session.rollback()

    try:
        with session_turn_coordinator.try_serialized_many(fence_session_ids) as held:
            if held is None:
                raise session_chapters._session_busy_error()
            try:
                campaign = session_chapters._locked_active_campaign(campaign_id, workspace_id)
                if session_chapters._chapter_fence_session_ids(campaign_id) != fence_session_ids:
                    raise session_chapters._session_busy_error()
                session_obj = _restore_chapter_locked(campaign, document)
                restored_player_ids = (
                    _restore_characters(campaign, session_obj, _list(document.get('characters')))
                    if restore_characters
                    else []
                )
                record_operator_action(
                    action='session.import',
                    resource_type='session',
                    workspace_id=campaign.workspace_id or 'owner',
                    campaign_id=campaign.campaign_id,
                    session_id=session_obj.session_id,
                    resource_id=session_obj.session_id,
                    details={
                        'mode': IMPORT_RESTORE,
                        'saveVersion': SAVE_VERSION,
                        'sourceSessionId': _coerce_positive(_record(document.get('session')).get('session_id')),
                        'restoredPlayerIds': restored_player_ids,
                    },
                )
                result = SessionImportResult(
                    payload=_import_payload(
                        session_obj,
                        IMPORT_RESTORE,
                        {'turn_events': 0, 'projected_log_entries': 0, 'log_entries': 0, 'session_state': 1},
                        restored_player_ids=restored_player_ids,
                    )
                )
                # Database-backed coordinator leases fence this commit.
                db.session.commit()
                return result
            except Exception:
                db.session.rollback()
                raise
    except (SessionTurnTargetMissingError, TurnLeaseLostError):
        db.session.rollback()
        raise session_chapters._session_busy_error() from None


def _restore_chapter_locked(campaign: Campaign, document: dict[str, Any]) -> Session:
    source = session_chapters.chapter_source_session(campaign.campaign_id)
    if source is not None and session_is_open(source.status):
        reason = session_chapters.chapter_unsettled_reason(source)
        if reason:
            raise session_chapters.ChapterStartError(
                'chapter_unsettled',
                session_chapters.CHAPTER_UNSETTLED_MESSAGE,
                409,
                {'session_id': source.session_id, 'reason': reason},
            )

    now = utc_now()
    now_iso = now.isoformat()
    chapter_number = session_chapters._next_chapter_number(campaign.campaign_id)
    saved_snapshot, _migrations_applied = migrate_campaign_pack_snapshot(
        deepcopy(_record(document.get('state_snapshot')))
    )
    saved_state = _record(document.get('session_state'))
    recap_text = session_chapters.chapter_recap_text(
        saved_snapshot,
        _optional_text(_record(document.get('session')).get('continue_line'), max_length=MAX_IMPORTED_TEXT_LENGTH),
    )
    snapshot = session_chapters.chapter_snapshot_from(
        saved_snapshot,
        new_client_session_id=None,
        chapter_number=chapter_number,
        previous_session_id=source.session_id if source is not None else None,
        recap_text=recap_text,
        now_iso=now_iso,
    )
    snapshot['chapter']['restoredFrom'] = {
        'sessionId': _coerce_positive(_record(document.get('session')).get('session_id')),
        'exportedAt': _optional_text(document.get('exported_at'), max_length=80) or None,
    }

    session_chapters._close_open_chapters(campaign.campaign_id, now=now)
    session_obj = session_chapters._create_chapter_session(
        campaign,
        name=f'Chapter {chapter_number}',
        client_session_id=None,
        snapshot=snapshot,
        welcome_message=session_chapters.chapter_welcome_message(chapter_number, recap_text),
        welcome_metadata={'chapter_number': chapter_number, 'continuity': IMPORT_RESTORE},
    )
    # The restored chapter's summary state starts now, from the saved values.
    _import_session_state(session_obj, campaign, {**saved_state, 'updated_at': None}, now)
    if source is not None:
        session_chapters._carry_campaign_pack_progress(source, session_obj)
    unsettled = _record(document.get('unsettled'))
    if unsettled.get('pending_roll') is True or unsettled.get('active_combat') is True:
        record_turn_event(
            session_id=session_obj.session_id,
            campaign_id=campaign.campaign_id,
            event_type=STATE_UPDATE_EVENT,
            payload={'message': RESTORED_MID_SCENE_MESSAGE, 'metadata': {'kind': 'restored_mid_scene'}},
        )
    return session_obj


def _restore_characters(campaign: Campaign, session_obj: Session, characters: list[Any]) -> list[int]:
    """Overwrite matching Player rows' mechanical fields with the saved characters.

    A saved character matches the campaign's row with its ``player_id`` first,
    then (for the rows still unmatched) the row with its character name.
    """

    players = {
        player.player_id: player
        for player in Player.query.filter_by(campaign_id=campaign.campaign_id)
        .order_by(Player.player_id.asc())
        .all()
    }
    saved_records = [_record(character) for character in characters if isinstance(character, dict)]
    matches: list[tuple[Player, dict[str, Any]]] = []
    unmatched_records = []
    for record in saved_records:
        player = players.pop(_coerce_positive(record.get('player_id')) or 0, None)
        if player is not None:
            matches.append((player, record))
        else:
            unmatched_records.append(record)
    for record in unmatched_records:
        name_key = _name_key(record.get('character_name'))
        player = next(
            (row for row in players.values() if name_key and _name_key(row.character_name) == name_key),
            None,
        )
        if player is not None:
            players.pop(player.player_id)
            matches.append((player, record))

    for player, record in matches:
        _apply_saved_character(player, record)
    restored_player_ids = sorted(player.player_id for player, _record_value in matches)
    if restored_player_ids:
        record_operator_action(
            action='session.restore_characters',
            resource_type='player',
            workspace_id=campaign.workspace_id or 'owner',
            campaign_id=campaign.campaign_id,
            session_id=session_obj.session_id,
            resource_id=','.join(str(player_id) for player_id in restored_player_ids),
            details={
                'playerIds': restored_player_ids,
                'statFields': list(RESTORED_STAT_KEYS),
                'sheetFields': list(RESTORED_SHEET_KEYS),
                'columns': ['level', 'inventory'],
            },
        )
    return restored_player_ids


def _apply_saved_character(player: Player, record: dict[str, Any]) -> None:
    saved_stats = _record(record.get('stats'))
    stats = safe_json_loads(player.stats, {})
    stats = stats if isinstance(stats, dict) else {}
    _replace_keys(stats, saved_stats, RESTORED_STAT_KEYS)
    player.stats = safe_json_dumps(stats, {})

    sheet = character_sheet_record(player.character_sheet)
    _replace_keys(sheet, _record(record.get('character_sheet')), RESTORED_SHEET_KEYS)
    player.character_sheet = safe_json_dumps(sheet, {})

    level = record.get('level')
    if isinstance(level, int) and not isinstance(level, bool) and level >= 1:
        player.level = level
    player.inventory = dump_inventory_items(load_inventory_items(record.get('inventory')))


def _replace_keys(target: dict[str, Any], saved: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if key in saved:
            target[key] = deepcopy(saved[key])
        else:
            target.pop(key, None)


def _name_key(value: Any) -> str:
    return ' '.join(str(value or '').split()).casefold()


def _import_payload(session_obj: Session, mode: str, counts: dict, *, restored_player_ids: list[int]) -> dict:
    return {
        'imported': True,
        'mode': mode,
        'session_id': session_obj.session_id,
        'session': session_payload(session_obj),
        'counts': counts,
        'restored_player_ids': restored_player_ids,
    }


def _workspace_campaign(campaign_id: int | None, workspace_id: str | None) -> Campaign:
    if campaign_id is None:
        raise SessionImportError('Import file must include a campaign id.')
    campaign = db.session.get(Campaign, campaign_id)
    if not campaign or (workspace_id is not None and (campaign.workspace_id or 'owner') != workspace_id):
        raise SessionImportError('Campaign not found.', error_code='campaign_not_found', status_code=404)
    return campaign


def _imported_state_snapshot(
    *,
    source_snapshot: dict[str, Any],
    session_obj: Session,
    campaign: Campaign,
    exported_at: str,
    source_session_id: int | None,
    imported_at: datetime,
    allow_campaign_pack_state: bool,
) -> dict[str, Any]:
    snapshot = deepcopy(source_snapshot) if source_snapshot else {}
    campaign_pack_state_stripped = False
    if not allow_campaign_pack_state:
        campaign_pack_state_stripped = _strip_untrusted_campaign_pack_state(snapshot)
    snapshot['imported'] = True
    snapshot['imported_at'] = imported_at.isoformat()
    snapshot['source_exported_at'] = exported_at
    snapshot['source_session_id'] = source_session_id
    snapshot['sessionId'] = session_obj.session_id
    snapshot['campaignId'] = campaign.campaign_id
    metadata = _record(snapshot.get('importMetadata')).copy()
    metadata.update(
        {
            'importedAt': imported_at.isoformat(),
            'sourceExportedAt': exported_at,
            'sourceSessionId': source_session_id,
        }
    )
    if campaign_pack_state_stripped:
        metadata[CAMPAIGN_PACK_METADATA_FLAG] = True
    snapshot['importMetadata'] = metadata
    snapshot, _migrations_applied = migrate_campaign_pack_snapshot(snapshot)
    return snapshot


def _strip_untrusted_campaign_pack_state(snapshot: dict[str, Any]) -> bool:
    stripped = False
    for key in CAMPAIGN_PACK_SNAPSHOT_KEYS:
        if key in snapshot:
            snapshot.pop(key, None)
            stripped = True

    flags = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}
    if flags:
        filtered_flags = {
            key: value
            for key, value in flags.items()
            if not str(key).startswith('campaignPack')
        }
        if filtered_flags != flags:
            stripped = True
            if filtered_flags:
                snapshot['flags'] = filtered_flags
            else:
                snapshot.pop('flags', None)
    return stripped


def _campaign_id(payload: dict[str, Any]) -> int | None:
    selected_ids = _record(payload.get('selectedIds'))
    campaign = _record(payload.get('campaign'))
    for value in (
        payload.get('campaign_id'),
        payload.get('campaignId'),
        selected_ids.get('campaignId'),
        selected_ids.get('campaign_id'),
        campaign.get('campaign_id'),
        campaign.get('id'),
    ):
        campaign_id = _coerce_positive(value)
        if campaign_id is not None:
            return campaign_id
    return None


def _session_name(payload: dict[str, Any], source_session: dict[str, Any]) -> str:
    for value in (
        payload.get('name'),
        payload.get('sessionName'),
        source_session.get('name'),
        source_session.get('display_name'),
        source_session.get('title'),
    ):
        text = _optional_text(value, max_length=MAX_IMPORTED_TEXT_LENGTH)
        # An archive's display name carries the prefix; the stored name does not.
        if text.startswith(ARCHIVE_NAME_PREFIX):
            text = text[len(ARCHIVE_NAME_PREFIX):].strip()
        if text:
            return text[:MAX_IMPORTED_NAME_LENGTH]
    return 'Imported Session'


def _import_session_state(session_obj: Session, campaign: Campaign, raw_state: Any, now: datetime) -> bool:
    state = _record(raw_state)
    if not state:
        return False
    session_state = SessionState(
        session_id=session_obj.session_id,
        current_location=_optional_text(state.get('current_location'), max_length=MAX_IMPORTED_TEXT_LENGTH)
        or campaign.location,
        current_quest=_optional_text(state.get('current_quest'), max_length=MAX_IMPORTED_TEXT_LENGTH)
        or campaign.current_quest,
        rolling_summary=_optional_text(state.get('rolling_summary'), max_length=MAX_IMPORTED_TEXT_LENGTH) or '',
        active_segments=safe_json_dumps(_bounded_state_list(state.get('active_segments')), []),
        memory_snippets=safe_json_dumps(_bounded_state_list(state.get('memory_snippets')), []),
        updated_at=_parse_datetime(state.get('updated_at')) or now,
    )
    db.session.add(session_state)
    return True


def _import_turn_events(
    session_obj: Session,
    campaign: Campaign,
    events: list[Any],
    *,
    allow_campaign_pack_events: bool = True,
    required_player_account_id: int | None = None,
) -> tuple[int, int]:
    imported = 0
    projected_log_entries = 0
    for raw_event in events:
        event_record = _record(raw_event)
        event_type = _optional_text(event_record.get('event_type'), max_length=80)
        if not event_type:
            continue
        if not allow_campaign_pack_events and event_type.startswith('campaign_pack.'):
            continue
        payload = _record(event_record.get('payload'))
        payload = _with_import_metadata(payload, event_record)
        created_at = _parse_datetime(event_record.get('created_at')) or utc_now()
        event = TurnEvent(
            session_id=session_obj.session_id,
            campaign_id=campaign.campaign_id,
            turn_id=None,
            player_id=_campaign_player_id(
                event_record.get('player_id'),
                campaign,
                required_player_account_id=required_player_account_id,
            ),
            event_type=event_type,
            payload_json=safe_json_dumps(payload, {}),
            created_at=created_at,
        )
        db.session.add(event)
        db.session.flush()
        counts = project_turn_event(event, payload, timestamp=created_at)
        projected_log_entries += counts.get('session_log_entries', 0)
        imported += 1
    return imported, projected_log_entries


def _with_import_metadata(payload: dict[str, Any], event_record: dict[str, Any]) -> dict[str, Any]:
    source_turn_id = _coerce_positive(event_record.get('turn_id'))
    source_event_id = _coerce_positive(event_record.get('event_id'))
    if source_turn_id is None and source_event_id is None:
        return payload
    metadata = _record(payload.get('metadata')).copy()
    if source_turn_id is not None:
        metadata.setdefault('imported_from_turn_id', source_turn_id)
    if source_event_id is not None:
        metadata.setdefault('imported_from_event_id', source_event_id)
    return {**payload, 'metadata': metadata}


def _import_log_entries(session_obj: Session, entries: list[Any]) -> int:
    imported = 0
    for raw_entry in entries:
        entry_record = _record(raw_entry)
        message = _optional_text(entry_record.get('message'), max_length=MAX_IMPORTED_TEXT_LENGTH)
        if not message:
            continue
        entry_type = _optional_text(entry_record.get('entry_type'), max_length=40) or 'system'
        if entry_type not in VALID_LOG_ENTRY_TYPES:
            entry_type = 'system'
        entry = SessionLogEntry(
            session_id=session_obj.session_id,
            message=message,
            entry_type=entry_type,
            metadata_json=safe_json_dumps(_record(entry_record.get('metadata')), {}),
            timestamp=_parse_datetime(entry_record.get('timestamp')) or utc_now(),
        )
        db.session.add(entry)
        imported += 1
    return imported


def _campaign_player_id(
    value: Any,
    campaign: Campaign,
    *,
    required_player_account_id: int | None = None,
) -> int | None:
    player_id = _coerce_positive(value)
    if player_id is None:
        return None
    player = db.session.get(Player, player_id)
    if not player:
        return None
    if player.workspace_id != campaign.workspace_id or player.campaign_id != campaign.campaign_id:
        return None
    if required_player_account_id is not None and player.account_id != required_player_account_id:
        return None
    return player.player_id


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bounded_state_list(value: Any) -> list[Any]:
    return [
        _bounded_state_value(item, depth=0)
        for item in _list(value)[:MAX_IMPORTED_STATE_LIST_ITEMS]
    ]


def _bounded_state_value(value: Any, *, depth: int) -> Any:
    if isinstance(value, str):
        return value[:MAX_IMPORTED_STATE_NESTED_TEXT_LENGTH]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= MAX_IMPORTED_STATE_NESTED_DEPTH:
        return str(value)[:MAX_IMPORTED_STATE_NESTED_TEXT_LENGTH]
    if isinstance(value, list):
        return [
            _bounded_state_value(item, depth=depth + 1)
            for item in value[:MAX_IMPORTED_STATE_NESTED_ITEMS]
        ]
    if isinstance(value, dict):
        bounded = {}
        for key, item in islice(value.items(), MAX_IMPORTED_STATE_NESTED_ITEMS):
            bounded[str(key)[:128]] = _bounded_state_value(item, depth=depth + 1)
        return bounded
    return str(value)[:MAX_IMPORTED_STATE_NESTED_TEXT_LENGTH]


def _coerce_positive(value: Any) -> int | None:
    coerced = coerce_int(value)
    return coerced if coerced and coerced > 0 else None


def _optional_text(value: Any, *, max_length: int) -> str:
    if value is None:
        return ''
    if not isinstance(value, str):
        value = str(value)
    return value.strip()[:max_length]


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith('Z'):
        normalized = f'{normalized[:-1]}+00:00'
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
