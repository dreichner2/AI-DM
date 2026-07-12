from __future__ import annotations

import logging
from typing import Any

from aidm_server.database import db
from aidm_server.models import PlayerAction, SessionLogEntry, TurnEvent, safe_json_dumps, safe_json_loads

logger = logging.getLogger(__name__)


PLAYER_MESSAGE_EVENT = 'player_message'
ROLL_RESOLVED_EVENT = 'roll_resolved'
DM_RESPONSE_EVENT = 'dm_response'
SEGMENT_TRIGGERED_EVENT = 'segment_triggered'
CANON_APPLIED_EVENT = 'canon_applied'
STATE_UPDATE_EVENT = 'state_update'
SESSION_STARTED_EVENT = 'session_started'
SESSION_ENDED_EVENT = 'session_ended'
SESSION_RECAP_EVENT = 'session_recap'


def record_turn_event(
    *,
    session_id: int,
    campaign_id: int,
    event_type: str,
    payload: dict[str, Any],
    turn_id: int | None = None,
    player_id: int | None = None,
    project_legacy: bool = True,
) -> TurnEvent:
    event = TurnEvent(
        session_id=session_id,
        campaign_id=campaign_id,
        turn_id=turn_id,
        player_id=player_id,
        event_type=event_type,
        payload_json=safe_json_dumps(payload, {}),
    )
    db.session.add(event)
    db.session.flush()

    if project_legacy:
        try:
            with db.session.begin_nested():
                _project_turn_event(event, payload)
                db.session.flush()
        except Exception as exc:
            logger.warning(
                'Failed to project legacy turn event %s for session %s turn %s: %s',
                event.event_type,
                event.session_id,
                event.turn_id,
                exc,
            )
    return event


def project_turn_event(event: TurnEvent, payload: dict[str, Any] | None = None, *, timestamp=None) -> dict[str, int]:
    payload = payload if isinstance(payload, dict) else safe_json_loads(event.payload_json, {})
    payload = payload if isinstance(payload, dict) else {}
    return _project_turn_event(event, payload, timestamp=timestamp)


def _player_action(event: TurnEvent, message: str, *, timestamp=None) -> PlayerAction:
    action = PlayerAction(
        player_id=event.player_id,
        session_id=event.session_id,
        action_text=message,
    )
    if timestamp is not None:
        action.timestamp = timestamp
    return action


def _log_entry(event: TurnEvent, message: str, entry_type: str, metadata: dict | None = None, *, timestamp=None) -> SessionLogEntry:
    entry = SessionLogEntry(
        session_id=event.session_id,
        message=message,
        entry_type=entry_type,
        metadata_json=safe_json_dumps(metadata or {}, {}),
    )
    if timestamp is not None:
        entry.timestamp = timestamp
    return entry


def _project_turn_event(event: TurnEvent, payload: dict[str, Any], *, timestamp=None) -> dict[str, int]:
    counts = {'player_actions': 0, 'session_log_entries': 0}
    if event.event_type == PLAYER_MESSAGE_EVENT:
        message = str(payload.get('message') or '').strip()
        speaker = str(payload.get('speaker') or '').strip()
        if not message:
            return counts
        if event.player_id is not None:
            db.session.add(_player_action(event, message, timestamp=timestamp))
            counts['player_actions'] += 1
        db.session.add(
            _log_entry(
                event,
                f'{speaker}: {message}' if speaker else message,
                'player',
                payload.get('metadata', {}),
                timestamp=timestamp,
            )
        )
        counts['session_log_entries'] += 1
        return counts

    if event.event_type == ROLL_RESOLVED_EVENT:
        pending_turn_id = payload.get('pending_turn_id')
        roll_value = payload.get('roll_value')
        message = (
            f'**Check Resolved**: turn {pending_turn_id} resolved with roll {roll_value}.'
            if pending_turn_id is not None
            else f'**Roll Resolved**: authoritative result {roll_value}.'
        )
        db.session.add(
            _log_entry(
                event,
                message,
                'dm',
                payload.get('metadata', {}),
                timestamp=timestamp,
            )
        )
        counts['session_log_entries'] += 1
        return counts

    if event.event_type == DM_RESPONSE_EVENT:
        message = str(payload.get('message') or '').strip()
        if not message:
            return counts
        db.session.add(
            _log_entry(
                event,
                f'DM: {message}',
                'dm',
                payload.get('metadata', {}),
                timestamp=timestamp,
            )
        )
        counts['session_log_entries'] += 1
        return counts

    if event.event_type == SEGMENT_TRIGGERED_EVENT:
        title = str(payload.get('title') or '').strip()
        if not title:
            return counts
        db.session.add(
            _log_entry(
                event,
                f'**Segment Triggered**: {title}',
                'dm',
                payload.get('metadata', {}),
                timestamp=timestamp,
            )
        )
        counts['session_log_entries'] += 1
        return counts

    if event.event_type == STATE_UPDATE_EVENT:
        message = str(payload.get('message') or '').strip()
        if not message:
            return counts
        db.session.add(
            _log_entry(
                event,
                message,
                'system',
                payload.get('metadata', {}),
                timestamp=timestamp,
            )
        )
        counts['session_log_entries'] += 1
        return counts

    if event.event_type == SESSION_STARTED_EVENT:
        message = str(payload.get('message') or '').strip()
        if not message:
            return counts
        db.session.add(
            _log_entry(
                event,
                message,
                'system',
                payload.get('metadata', {}),
                timestamp=timestamp,
            )
        )
        counts['session_log_entries'] += 1
        return counts

    if event.event_type == SESSION_ENDED_EVENT:
        message = str(payload.get('message') or '**Session ended.**').strip()
        if not message:
            return counts
        db.session.add(
            _log_entry(
                event,
                message,
                'system',
                payload.get('metadata', {}),
                timestamp=timestamp,
            )
        )
        counts['session_log_entries'] += 1
        return counts

    if event.event_type == SESSION_RECAP_EVENT:
        recap = str(payload.get('recap') or '').strip()
        message = str(payload.get('message') or '').strip()
        if not message and recap:
            message = f'**Session Recap**\n\n{recap}'
        if not message:
            return counts
        db.session.add(
            _log_entry(
                event,
                message,
                'system',
                payload.get('metadata', {}),
                timestamp=timestamp,
            )
        )
        counts['session_log_entries'] += 1
        return counts

    return counts
