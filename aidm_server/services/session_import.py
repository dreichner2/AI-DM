from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from typing import Any

from aidm_server.database import db
from aidm_server.models import Campaign, Player, Session, SessionLogEntry, SessionState, TurnEvent, safe_json_dumps
from aidm_server.response_dtos import session_payload
from aidm_server.services.campaign_pack_snapshot import migrate_campaign_pack_snapshot
from aidm_server.time_utils import utc_now
from aidm_server.turn_events import project_turn_event
from aidm_server.validation import coerce_int

MAX_IMPORTED_EVENTS = 1000
MAX_IMPORTED_LOG_ENTRIES = 1000
MAX_IMPORTED_TEXT_LENGTH = 20_000
MAX_IMPORTED_NAME_LENGTH = 80
MAX_IMPORTED_STATE_LIST_ITEMS = 100
MAX_IMPORTED_STATE_NESTED_ITEMS = 50
MAX_IMPORTED_STATE_NESTED_TEXT_LENGTH = 2_000
MAX_IMPORTED_STATE_NESTED_DEPTH = 4
VALID_LOG_ENTRY_TYPES = {'dm', 'player', 'system'}


class SessionImportError(ValueError):
    def __init__(self, message: str, *, error_code: str = 'validation_error', status_code: int = 400):
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


@dataclass(frozen=True)
class SessionImportResult:
    payload: dict


def import_session_export(
    payload: dict[str, Any],
    *,
    workspace_id: str | None = None,
    include_hidden_state: bool = True,
) -> SessionImportResult:
    if not isinstance(payload, dict):
        raise SessionImportError('Expected JSON request body.')

    campaign_id = _campaign_id(payload)
    if campaign_id is None:
        raise SessionImportError('Import file must include a campaign id.')

    campaign = db.session.get(Campaign, campaign_id)
    if not campaign:
        raise SessionImportError('Campaign not found.', error_code='campaign_not_found', status_code=404)
    if workspace_id is not None and (campaign.workspace_id or 'owner') != workspace_id:
        raise SessionImportError('Campaign not found.', error_code='campaign_not_found', status_code=404)

    now = utc_now()
    source_session = _record(payload.get('selectedSession'))
    exported_at = _optional_text(payload.get('exportedAt'), max_length=80)
    source_snapshot = _record(source_session.get('state_snapshot'))

    session_obj = Session(
        campaign_id=campaign.campaign_id,
        name=_session_name(payload, source_session),
        status='active',
        state_snapshot=safe_json_dumps({}, {}),
        created_at=now,
        updated_at=now,
    )
    db.session.add(session_obj)
    db.session.flush()
    session_obj.state_snapshot = safe_json_dumps(
        _imported_state_snapshot(
            source_snapshot=source_snapshot,
            session_obj=session_obj,
            campaign=campaign,
            exported_at=exported_at,
            source_session_id=_coerce_positive(source_session.get('session_id')),
            imported_at=now,
        ),
        {},
    )

    state_imported = _import_session_state(session_obj, campaign, payload.get('sessionState'), now)
    events_imported, projected_log_entries = _import_turn_events(
        session_obj,
        campaign,
        _list(payload.get('turnEvents'))[:MAX_IMPORTED_EVENTS],
    )
    log_entries_imported = 0
    if events_imported == 0:
        log_entries_imported = _import_log_entries(
            session_obj,
            _list(payload.get('logEntries'))[:MAX_IMPORTED_LOG_ENTRIES],
        )

    result_payload = {
        'imported': True,
        'session_id': session_obj.session_id,
        'session': session_payload(session_obj, include_hidden_state=include_hidden_state),
        'counts': {
            'turn_events': events_imported,
            'projected_log_entries': projected_log_entries,
            'log_entries': log_entries_imported,
            'session_state': 1 if state_imported else 0,
        },
    }
    return SessionImportResult(payload=result_payload)


def _imported_state_snapshot(
    *,
    source_snapshot: dict[str, Any],
    session_obj: Session,
    campaign: Campaign,
    exported_at: str,
    source_session_id: int | None,
    imported_at: datetime,
) -> dict[str, Any]:
    snapshot = deepcopy(source_snapshot) if source_snapshot else {}
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
    snapshot['importMetadata'] = metadata
    snapshot, _migrations_applied = migrate_campaign_pack_snapshot(snapshot)
    return snapshot


def _campaign_id(payload: dict[str, Any]) -> int | None:
    selected_ids = _record(payload.get('selectedIds'))
    campaign = _record(payload.get('campaign'))
    for value in (
        payload.get('campaign_id'),
        payload.get('campaignId'),
        selected_ids.get('campaignId'),
        selected_ids.get('campaign_id'),
        campaign.get('campaign_id'),
    ):
        campaign_id = _coerce_positive(value)
        if campaign_id is not None:
            return campaign_id
    return None


def _session_name(payload: dict[str, Any], source_session: dict[str, Any]) -> str:
    for value in (
        payload.get('name'),
        payload.get('sessionName'),
        source_session.get('display_name'),
        source_session.get('name'),
        source_session.get('title'),
    ):
        text = _optional_text(value, max_length=MAX_IMPORTED_NAME_LENGTH)
        if text:
            return text
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


def _import_turn_events(session_obj: Session, campaign: Campaign, events: list[Any]) -> tuple[int, int]:
    imported = 0
    projected_log_entries = 0
    for raw_event in events:
        event_record = _record(raw_event)
        event_type = _optional_text(event_record.get('event_type'), max_length=80)
        if not event_type:
            continue
        payload = _record(event_record.get('payload'))
        payload = _with_import_metadata(payload, event_record)
        created_at = _parse_datetime(event_record.get('created_at')) or utc_now()
        event = TurnEvent(
            session_id=session_obj.session_id,
            campaign_id=campaign.campaign_id,
            turn_id=None,
            player_id=_campaign_player_id(event_record.get('player_id'), campaign.campaign_id),
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


def _campaign_player_id(value: Any, campaign_id: int) -> int | None:
    player_id = _coerce_positive(value)
    if player_id is None:
        return None
    player = db.session.get(Player, player_id)
    campaign = db.session.get(Campaign, campaign_id)
    if not player or not campaign:
        return None
    if player.workspace_id:
        return player.player_id if player.workspace_id == campaign.workspace_id else None
    if player.campaign_id != campaign_id:
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
