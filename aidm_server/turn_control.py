from __future__ import annotations

from datetime import timezone
import json
import re
from typing import Any

from flask import current_app, has_app_context

from aidm_server.contracts import ProviderRequest
from aidm_server.database import db
from aidm_server.game_state.extraction.schemas import extract_json_object
from aidm_server.llm_providers import get_helper_provider
from aidm_server.models import Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.time_utils import utc_now


TURN_CONTROL_MODES = {'free', 'spotlight', 'structured'}
TURN_CONTROL_SOURCES = {'auto', 'ai', 'manual', 'admin', 'system'}
TURN_CONDUCTOR_DECISIONS = {
    'allow',
    'queue',
    'add_participant',
    'switch_to_free',
    'switch_to_spotlight',
    'switch_to_structured',
}
DEFAULT_TURN_CONTROL = {
    'mode': 'free',
    'source': 'auto',
    'focusType': None,
    'activePlayerId': None,
    'activePlayerName': None,
    'participantPlayerIds': [],
    'participantPlayerNames': [],
    'pendingJoinRequests': [],
    'reason': None,
    'confidence': None,
    'updatedByPlayerId': None,
    'updatedAt': None,
}

_SOCIAL_ACTION_RE = re.compile(
    r'\b(?:ask|say|says|speak|talk|tell|reply|respond|answer|whisper|persuade|convince|negotiate|'
    r'bargain|deceive|intimidate|charm|greet|question|call out)\b',
    re.IGNORECASE,
)
_JOIN_SPOTLIGHT_RE = re.compile(
    r'\b(?:join|step in|step beside|walk over|move beside|back (?:him|her|them|you) up|chime in|'
    r'add|support|help (?:him|her|them|you)|stand beside|stand with)\b',
    re.IGNORECASE,
)
_STRUCTURED_ACTION_RE = re.compile(
    r'\b(?:attack|stab|slash|shoot|fire|punch|strike|kill|grapple|cast|fireball|chase|flee|'
    r'run ahead|charge|tackle|dodge|disarm|steal|snatch|kick open|break down)\b',
    re.IGNORECASE,
)

TURN_CONDUCTOR_SYSTEM_MESSAGE = (
    'You are AI-DM turn conductor. Decide table flow only; do not narrate. '
    'Return JSON only with decision, mode, activePlayerId, participantPlayerIds, '
    'focusType, reason, and confidence. Prefer spotlight for shared conversations, '
    'free for low-pressure play, and structured only for active combat, chases, traps, explicit interrupts, or concrete timing-critical hazards. '
    'Do not use structured just because an action is magical, unusual, or might hypothetically need a check. '
    'A player joining the same conversation should usually be add_participant, not queued.'
)


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


def _clean_source(value: Any) -> str:
    source = (_clean_string(value) or 'auto').lower()
    return source if source in TURN_CONTROL_SOURCES else 'auto'


def _clean_confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return parsed


def _positive_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        parsed = _positive_int(item)
        if parsed and parsed not in result:
            result.append(parsed)
    return result


def player_display_name(player_id: int | None) -> str | None:
    if not player_id:
        return None
    player = db.session.get(Player, player_id)
    if not player:
        return None
    return player.character_name or player.name or f'Player {player_id}'


def player_display_names(player_ids: list[int]) -> list[str]:
    return [player_display_name(player_id) or f'Player {player_id}' for player_id in player_ids]


def _active_player_records(player_ids: list[int]) -> list[dict]:
    records: list[dict] = []
    for player_id in player_ids:
        records.append({'playerId': player_id, 'name': player_display_name(player_id) or f'Player {player_id}'})
    return records


def _pending_join_requests(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    requests: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        player_id = _positive_int(item.get('playerId') or item.get('player_id'))
        if not player_id:
            continue
        requests.append(
            {
                'playerId': player_id,
                'playerName': _clean_string(item.get('playerName') or item.get('player_name')) or player_display_name(player_id),
                'reason': _clean_string(item.get('reason')),
                'requestedAt': _clean_string(item.get('requestedAt') or item.get('requested_at')),
            }
        )
    return requests


def normalize_turn_control(raw_value: Any) -> dict:
    raw = raw_value if isinstance(raw_value, dict) else {}
    mode = _clean_string(raw.get('mode')) or 'free'
    mode = mode if mode in TURN_CONTROL_MODES else 'free'
    source = _clean_source(raw.get('source'))
    focus_type = _clean_string(raw.get('focusType') or raw.get('focus_type'))
    active_player_id = _positive_int(raw.get('activePlayerId') or raw.get('active_player_id'))
    active_player_name = _clean_string(raw.get('activePlayerName') or raw.get('active_player_name'))
    participant_player_ids = _positive_int_list(raw.get('participantPlayerIds') or raw.get('participant_player_ids'))
    updated_by_player_id = _positive_int(raw.get('updatedByPlayerId') or raw.get('updated_by_player_id'))
    updated_at = _clean_string(raw.get('updatedAt') or raw.get('updated_at'))
    reason = _clean_string(raw.get('reason'))
    confidence = _clean_confidence(raw.get('confidence'))
    pending_join_requests = _pending_join_requests(raw.get('pendingJoinRequests') or raw.get('pending_join_requests'))

    if mode == 'free':
        active_player_id = None
        active_player_name = None
        participant_player_ids = []
        focus_type = None
        pending_join_requests = []
    elif active_player_id and active_player_id not in participant_player_ids:
        participant_player_ids = [active_player_id, *participant_player_ids]
    elif mode == 'structured' and not active_player_id and participant_player_ids:
        active_player_id = participant_player_ids[0]

    if active_player_id and not active_player_name:
        active_player_name = player_display_name(active_player_id)

    return {
        'mode': mode,
        'source': source,
        'focusType': focus_type,
        'activePlayerId': active_player_id,
        'activePlayerName': active_player_name,
        'participantPlayerIds': participant_player_ids,
        'participantPlayerNames': player_display_names(participant_player_ids),
        'pendingJoinRequests': pending_join_requests,
        'reason': reason,
        'confidence': confidence,
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
    participant_player_ids: list[int] | None = None,
    source: str = 'manual',
    focus_type: str | None = None,
    reason: str | None = None,
    confidence: float | None = None,
) -> dict:
    normalized_mode = mode if mode in TURN_CONTROL_MODES else 'free'
    next_active_player_id = active_player_id if normalized_mode != 'free' else None
    participants = _positive_int_list(participant_player_ids or [])
    if normalized_mode != 'free' and next_active_player_id and next_active_player_id not in participants:
        participants = [next_active_player_id, *participants]
    return save_turn_control(
        session_obj,
        {
            'mode': normalized_mode,
            'source': source,
            'focusType': focus_type,
            'activePlayerId': next_active_player_id,
            'activePlayerName': player_display_name(next_active_player_id),
            'participantPlayerIds': participants,
            'pendingJoinRequests': [],
            'reason': reason,
            'confidence': confidence,
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


def _turn_conductor_helper_enabled() -> bool:
    if has_app_context() and current_app.config.get('AIDM_ENV') == 'test':
        return bool(current_app.config.get('AIDM_TURN_CONDUCTOR_HELPER_IN_TESTS', False))
    if has_app_context():
        return bool(current_app.config.get('AIDM_TURN_CONDUCTOR_HELPER_ENABLED', True))
    return False


def _build_turn_conductor_prompt(
    *,
    turn_control: dict,
    player_id: int,
    message: str,
    action_intent: dict | None,
    active_player_ids: list[int],
) -> str:
    payload = {
        'currentTurnControl': turn_control,
        'actingPlayerId': player_id,
        'actingPlayerName': player_display_name(player_id) or f'Player {player_id}',
        'activePlayers': _active_player_records(active_player_ids),
        'playerMessage': message,
        'actionIntent': action_intent or {},
        'validDecisions': sorted(TURN_CONDUCTOR_DECISIONS),
        'rules': [
            'Do not choose a player id outside activePlayers or actingPlayerId.',
            'Use add_participant when an outside player naturally joins the same spotlight conversation.',
            'Use switch_to_structured only for violent, interrupting, chase, trap, combat, or concrete timing-critical hazard actions.',
            'Use queue when an outside player tries to take unrelated focus during spotlight.',
            'Use allow when current flow already permits the action.',
        ],
    }
    return f'TURN_CONDUCTOR_INPUT:\n{json.dumps(payload, separators=(",", ":"))}\n'


def _normalize_conductor_decision(raw_value: Any, *, player_id: int, active_player_ids: list[int]) -> dict | None:
    raw = raw_value if isinstance(raw_value, dict) else {}
    decision = (_clean_string(raw.get('decision')) or '').lower()
    if decision not in TURN_CONDUCTOR_DECISIONS:
        return None

    mode = (_clean_string(raw.get('mode')) or '').lower()
    if mode not in TURN_CONTROL_MODES:
        if decision == 'switch_to_free':
            mode = 'free'
        elif decision == 'switch_to_spotlight' or decision == 'add_participant':
            mode = 'spotlight'
        elif decision == 'switch_to_structured':
            mode = 'structured'

    allowed_ids = set(_positive_int_list(active_player_ids))
    allowed_ids.add(player_id)
    raw_active_player_id = _positive_int(raw.get('activePlayerId') or raw.get('active_player_id'))
    active_player_id = raw_active_player_id if raw_active_player_id in allowed_ids else None
    participant_player_ids = [
        candidate for candidate in _positive_int_list(raw.get('participantPlayerIds') or raw.get('participant_player_ids')) if candidate in allowed_ids
    ]
    if player_id and decision in {'add_participant', 'switch_to_spotlight', 'switch_to_structured'} and player_id not in participant_player_ids:
        participant_player_ids.append(player_id)
    if active_player_id and active_player_id not in participant_player_ids and mode != 'free':
        participant_player_ids = [active_player_id, *participant_player_ids]

    return {
        'decision': decision,
        'mode': mode or None,
        'activePlayerId': active_player_id,
        'participantPlayerIds': participant_player_ids,
        'focusType': _clean_string(raw.get('focusType') or raw.get('focus_type')),
        'reason': _clean_string(raw.get('reason')) or 'AI conductor adjusted table flow.',
        'confidence': _clean_confidence(raw.get('confidence')) or 0.5,
    }


def _ai_turn_conductor_decision(
    *,
    turn_control: dict,
    player_id: int,
    message: str,
    action_intent: dict | None,
    active_player_ids: list[int],
) -> dict | None:
    if not _turn_conductor_helper_enabled():
        return None
    try:
        response = get_helper_provider().generate(
            ProviderRequest(
                prompt=_build_turn_conductor_prompt(
                    turn_control=turn_control,
                    player_id=player_id,
                    message=message,
                    action_intent=action_intent,
                    active_player_ids=active_player_ids,
                ),
                system_message=TURN_CONDUCTOR_SYSTEM_MESSAGE,
            )
        )
        telemetry_metric('socket.turn_conductor.helper_total', 1)
        payload = extract_json_object(response.text)
        decision = _normalize_conductor_decision(payload, player_id=player_id, active_player_ids=active_player_ids)
        if decision:
            decision['provider'] = response.provider
            decision['model'] = response.model
            return decision
        telemetry_event(
            'socket.turn_conductor.helper_invalid',
            payload={'raw_preview': str(response.text or '')[:500]},
            severity='warning',
        )
    except Exception as exc:
        telemetry_event('socket.turn_conductor.helper_failed', payload={'error': str(exc)}, severity='warning')
    return None


def _queue_result(session_obj: Session, *, player_id: int, action_intent: dict | None, has_pending_roll: bool) -> tuple[bool, str | None, dict, bool, dict]:
    allowed, reason, turn_control = turn_submission_result(
        session_obj,
        player_id=player_id,
        action_intent=action_intent,
        has_pending_roll=has_pending_roll,
    )
    return allowed, reason, turn_control, False, {'decision': 'queue'}


def _apply_ai_conductor_decision(
    session_obj: Session,
    *,
    turn_control: dict,
    decision: dict,
    player_id: int,
    message: str,
    action_intent: dict | None,
    has_pending_roll: bool,
    active_player_ids: list[int],
) -> tuple[bool, str | None, dict, bool, dict] | None:
    current_mode = turn_control['mode']
    current_participants = turn_control.get('participantPlayerIds') or []
    current_active_player_id = turn_control.get('activePlayerId')
    decision_name = decision.get('decision')

    if current_mode == 'structured' and current_active_player_id and current_active_player_id != player_id:
        return _queue_result(session_obj, player_id=player_id, action_intent=action_intent, has_pending_roll=has_pending_roll)

    if decision_name == 'allow':
        if current_mode == 'free' or player_id == current_active_player_id or player_id in current_participants:
            return True, None, turn_control, False, decision
        return None

    if decision_name == 'queue':
        if current_mode == 'free':
            return None
        return _queue_result(session_obj, player_id=player_id, action_intent=action_intent, has_pending_roll=has_pending_roll)

    if decision_name == 'switch_to_free':
        updated = set_session_turn_control(
            session_obj,
            mode='free',
            active_player_id=None,
            participant_player_ids=[],
            updated_by_player_id=player_id,
            source='ai',
            reason=decision.get('reason'),
            confidence=decision.get('confidence'),
        )
        return True, None, updated, True, decision

    if decision_name in {'add_participant', 'switch_to_spotlight'}:
        participants = decision.get('participantPlayerIds') or [*current_participants, player_id]
        active_player_id = decision.get('activePlayerId') or current_active_player_id or player_id
        if decision_name == 'add_participant':
            participants = [*current_participants, *participants, player_id]
        updated = set_session_turn_control(
            session_obj,
            mode='spotlight',
            active_player_id=active_player_id,
            participant_player_ids=participants,
            updated_by_player_id=player_id,
            source='ai',
            focus_type=decision.get('focusType') or turn_control.get('focusType') or 'conversation',
            reason=decision.get('reason'),
            confidence=decision.get('confidence'),
        )
        return True, None, updated, True, decision

    if decision_name == 'switch_to_structured':
        if not _allows_structured_switch(message, action_intent, has_pending_roll=has_pending_roll):
            return None
        participants = decision.get('participantPlayerIds') or active_player_ids or [player_id]
        active_player_id = decision.get('activePlayerId') or player_id
        updated = set_session_turn_control(
            session_obj,
            mode='structured',
            active_player_id=active_player_id,
            participant_player_ids=participants,
            updated_by_player_id=player_id,
            source='ai',
            focus_type=decision.get('focusType') or 'high_stakes',
            reason=decision.get('reason'),
            confidence=decision.get('confidence'),
        )
        return True, None, updated, True, decision

    return None


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
    participant_player_ids = turn_control.get('participantPlayerIds') or []
    if not active_player_id or active_player_id == player_id:
        return True, None, turn_control
    if turn_control['mode'] == 'spotlight' and player_id in participant_player_ids:
        return True, None, turn_control

    active_name = turn_control.get('activePlayerName') or f'Player {active_player_id}'
    mode_label = 'spotlight' if turn_control['mode'] == 'spotlight' else 'structured turn'
    return False, f'{active_name} has the {mode_label}. Your action is queued until your turn opens.', turn_control


def _action_kind(action_intent: dict | None) -> str | None:
    return _clean_string(action_intent.get('kind')) if isinstance(action_intent, dict) else None


def _interaction_type(action_intent: dict | None) -> str | None:
    if not isinstance(action_intent, dict):
        return None
    interaction = action_intent.get('interaction')
    if not isinstance(interaction, dict):
        return None
    return _clean_string(interaction.get('type'))


def _targets_current_scene_interaction(message: str, action_intent: dict | None) -> bool:
    kind = _action_kind(action_intent)
    if kind in {'ooc', 'admin', 'roll'}:
        return False
    if kind == 'interact' and _interaction_type(action_intent) == 'speak_to':
        return True
    return bool(_SOCIAL_ACTION_RE.search(message or ''))


def _requests_spotlight_join(message: str, action_intent: dict | None) -> bool:
    return _targets_current_scene_interaction(message, action_intent) or bool(_JOIN_SPOTLIGHT_RE.search(message or ''))


def _requires_structured_flow(message: str, action_intent: dict | None) -> bool:
    kind = _action_kind(action_intent)
    if kind in {'roll', 'admin', 'ooc'}:
        return False
    if kind == 'interact' and _interaction_type(action_intent) in {'act_on', 'take_from'}:
        return True
    if kind == 'item':
        inventory_action = _clean_string(action_intent.get('inventory_action')) if isinstance(action_intent, dict) else None
        return inventory_action in {'use', 'drop'}
    return bool(_STRUCTURED_ACTION_RE.search(message or ''))


def _allows_structured_switch(message: str, action_intent: dict | None, *, has_pending_roll: bool) -> bool:
    if has_pending_roll:
        return True
    return _requires_structured_flow(message, action_intent)


def _structured_context_still_active(session_obj: Session) -> bool:
    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    combat = snapshot.get('combat') if isinstance(snapshot.get('combat'), dict) else {}
    if str(combat.get('status') or '').strip().lower() in {'starting', 'active'}:
        return True
    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    if str(scene.get('combatState') or '').strip().lower() in {'pending', 'active'}:
        return True
    if str(scene.get('sceneType') or '').strip().lower() == 'combat':
        return True
    try:
        return int(scene.get('dangerLevel') or 0) >= 7
    except (TypeError, ValueError):
        return False


def conduct_turn_submission(
    session_obj: Session,
    *,
    player_id: int,
    message: str,
    action_intent: dict | None,
    has_pending_roll: bool = False,
    active_player_ids: list[int] | None = None,
) -> tuple[bool, str | None, dict, bool, dict]:
    turn_control = turn_control_from_session(session_obj)
    kind = _action_kind(action_intent)
    if kind == 'admin':
        return True, None, turn_control, False, {'decision': 'allow_admin'}
    if kind == 'roll' and has_pending_roll:
        return True, None, turn_control, False, {'decision': 'allow_pending_roll'}
    if turn_control['mode'] == 'structured':
        active_player_id = turn_control.get('activePlayerId')
        if active_player_id and active_player_id != player_id:
            return _queue_result(
                session_obj,
                player_id=player_id,
                action_intent=action_intent,
                has_pending_roll=has_pending_roll,
            )

    active_ids = _positive_int_list(active_player_ids or [])
    if player_id and player_id not in active_ids:
        active_ids.append(player_id)
    if not active_ids and player_id:
        active_ids = [player_id]

    ai_decision = _ai_turn_conductor_decision(
        turn_control=turn_control,
        player_id=player_id,
        message=message,
        action_intent=action_intent,
        active_player_ids=active_ids,
    )
    if ai_decision:
        ai_result = _apply_ai_conductor_decision(
            session_obj,
            turn_control=turn_control,
            decision=ai_decision,
            player_id=player_id,
            action_intent=action_intent,
            message=message,
            has_pending_roll=has_pending_roll,
            active_player_ids=active_ids,
        )
        if ai_result:
            return ai_result

    mode = turn_control['mode']
    if mode == 'free':
        if _targets_current_scene_interaction(message, action_intent):
            updated = set_session_turn_control(
                session_obj,
                mode='spotlight',
                active_player_id=player_id,
                participant_player_ids=[player_id],
                updated_by_player_id=player_id,
                source='auto',
                focus_type='conversation',
                reason='Auto flow focused the scene on an active conversation.',
                confidence=0.78,
            )
            return True, None, updated, True, {'decision': 'switch_to_spotlight'}
        return True, None, turn_control, False, {'decision': 'allow_free'}

    if mode == 'spotlight':
        participants = turn_control.get('participantPlayerIds') or []
        active_player_id = turn_control.get('activePlayerId')
        if not participants or player_id in participants or player_id == active_player_id:
            return True, None, turn_control, False, {'decision': 'allow_spotlight_participant'}
        if _requires_structured_flow(message, action_intent):
            updated = set_session_turn_control(
                session_obj,
                mode='structured',
                active_player_id=player_id,
                participant_player_ids=active_ids,
                updated_by_player_id=player_id,
                source='auto',
                focus_type='interrupt',
                reason='Auto flow switched to structured timing for an interrupting high-impact action.',
                confidence=0.84,
            )
            return True, None, updated, True, {'decision': 'switch_to_structured'}
        if _requests_spotlight_join(message, action_intent):
            updated = set_session_turn_control(
                session_obj,
                mode='spotlight',
                active_player_id=active_player_id or player_id,
                participant_player_ids=[*participants, player_id],
                updated_by_player_id=player_id,
                source='auto',
                focus_type=turn_control.get('focusType') or 'conversation',
                reason='Auto flow added a player to the focused scene.',
                confidence=0.8,
            )
            return True, None, updated, True, {'decision': 'add_participant'}

    return (*turn_submission_result(session_obj, player_id=player_id, action_intent=action_intent, has_pending_roll=has_pending_roll), False, {'decision': 'queue'})


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

    if turn_control.get('source') in {'ai', 'auto'} and not _structured_context_still_active(session_obj):
        if len(unique_active_ids) <= 1:
            return set_session_turn_control(
                session_obj,
                mode='free',
                active_player_id=None,
                participant_player_ids=[],
                updated_by_player_id=current_player_id,
                source='auto',
                focus_type=None,
                reason='Auto flow returned to free play after structured timing resolved.',
                confidence=0.82,
            )
        return set_session_turn_control(
            session_obj,
            mode='spotlight',
            active_player_id=current_player_id or active_player_id or unique_active_ids[0],
            participant_player_ids=unique_active_ids,
            updated_by_player_id=current_player_id,
            source='auto',
            focus_type='conversation',
            reason='Auto flow returned to spotlight after structured timing resolved.',
            confidence=0.82,
        )

    next_player_id = unique_active_ids[0]
    if current_player_id in unique_active_ids:
        current_index = unique_active_ids.index(current_player_id)
        next_player_id = unique_active_ids[(current_index + 1) % len(unique_active_ids)]

    return set_session_turn_control(
        session_obj,
        mode='structured',
        active_player_id=next_player_id,
        participant_player_ids=unique_active_ids,
        updated_by_player_id=current_player_id,
        source='auto',
        focus_type=turn_control.get('focusType') or 'turn_order',
        reason='Auto flow advanced the structured turn order.',
        confidence=0.9,
    )
