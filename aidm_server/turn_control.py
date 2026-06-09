from __future__ import annotations

from datetime import timezone
from typing import Any

from aidm_server.database import db
from aidm_server.models import Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.time_utils import utc_now


TURN_CONTROL_MODES = {'free', 'spotlight', 'structured'}
DEFAULT_TURN_CONTROL = {
    'mode': 'free',
    'activePlayerId': None,
    'activePlayerName': None,
    'updatedByPlayerId': None,
    'updatedAt': None,
}


def _utc_iso() -> str:
    return utc_now().replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def player_display_name(player_id: int | None) -> str | None:
    if not player_id:
        return None
    player = db.session.get(Player, player_id)
    if not player:
        return None
    return player.character_name or player.name or f'Player {player_id}'


def normalize_turn_control(raw_value: Any) -> dict:
    raw = raw_value if isinstance(raw_value, dict) else {}
    mode = _clean_string(raw.get('mode')) or 'free'
    mode = mode if mode in TURN_CONTROL_MODES else 'free'
    active_player_id = _positive_int(raw.get('activePlayerId') or raw.get('active_player_id'))
    active_player_name = _clean_string(raw.get('activePlayerName') or raw.get('active_player_name'))
    updated_by_player_id = _positive_int(raw.get('updatedByPlayerId') or raw.get('updated_by_player_id'))
    updated_at = _clean_string(raw.get('updatedAt') or raw.get('updated_at'))

    if mode == 'free':
        active_player_id = None
        active_player_name = None

    return {
        'mode': mode,
        'activePlayerId': active_player_id,
        'activePlayerName': active_player_name,
        'updatedByPlayerId': updated_by_player_id,
        'updatedAt': updated_at,
    }


def turn_control_from_session(session_obj: Session | None) -> dict:
    if not session_obj:
        return dict(DEFAULT_TURN_CONTROL)
    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    return normalize_turn_control(snapshot.get('turnControl') or snapshot.get('turn_control'))


def save_turn_control(session_obj: Session, turn_control: dict) -> dict:
    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    normalized = normalize_turn_control(turn_control)
    snapshot['turnControl'] = normalized
    session_obj.state_snapshot = safe_json_dumps(snapshot, {})
    session_obj.updated_at = utc_now()
    return normalized


def set_session_turn_control(
    session_obj: Session,
    *,
    mode: str,
    active_player_id: int | None,
    updated_by_player_id: int | None,
) -> dict:
    normalized_mode = mode if mode in TURN_CONTROL_MODES else 'free'
    next_active_player_id = active_player_id if normalized_mode != 'free' else None
    return save_turn_control(
        session_obj,
        {
            'mode': normalized_mode,
            'activePlayerId': next_active_player_id,
            'activePlayerName': player_display_name(next_active_player_id),
            'updatedByPlayerId': updated_by_player_id,
            'updatedAt': _utc_iso(),
        },
    )


def turn_control_update_payload(session_id: int, turn_control: dict) -> dict:
    normalized = normalize_turn_control(turn_control)
    return {
        'session_id': session_id,
        'turn_control': normalized,
        'turnControl': normalized,
    }


def turn_submission_result(
    session_obj: Session,
    *,
    player_id: int,
    action_intent: dict | None,
    has_pending_roll: bool = False,
) -> tuple[bool, str | None, dict]:
    turn_control = turn_control_from_session(session_obj)
    kind = _clean_string(action_intent.get('kind')) if isinstance(action_intent, dict) else None

    if kind == 'admin':
        return True, None, turn_control
    if kind == 'roll' and has_pending_roll:
        return True, None, turn_control
    if turn_control['mode'] == 'free':
        return True, None, turn_control

    active_player_id = turn_control.get('activePlayerId')
    if not active_player_id or active_player_id == player_id:
        return True, None, turn_control

    active_name = turn_control.get('activePlayerName') or f'Player {active_player_id}'
    mode_label = 'spotlight' if turn_control['mode'] == 'spotlight' else 'structured turn'
    return False, f'{active_name} has the {mode_label}. Your action is queued until your turn opens.', turn_control


def advance_structured_turn(session_obj: Session, *, current_player_id: int | None, active_player_ids: list[int]) -> dict | None:
    turn_control = turn_control_from_session(session_obj)
    if turn_control['mode'] != 'structured':
        return None

    unique_active_ids: list[int] = []
    for player_id in active_player_ids:
        parsed = _positive_int(player_id)
        if parsed and parsed not in unique_active_ids:
            unique_active_ids.append(parsed)

    if not unique_active_ids:
        return None

    active_player_id = turn_control.get('activePlayerId')
    if active_player_id and current_player_id and active_player_id != current_player_id:
        return None

    next_player_id = unique_active_ids[0]
    if current_player_id in unique_active_ids:
        current_index = unique_active_ids.index(current_player_id)
        next_player_id = unique_active_ids[(current_index + 1) % len(unique_active_ids)]

    return set_session_turn_control(
        session_obj,
        mode='structured',
        active_player_id=next_player_id,
        updated_by_player_id=current_player_id,
    )
