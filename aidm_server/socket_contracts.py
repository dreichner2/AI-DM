"""Socket event payload validation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aidm_server.action_intent import ACTION_ID_RE, has_reserved_admin_prefix, validate_action_intent
from aidm_server.errors import build_error
from aidm_server.roll_visibility import (
    player_roll_spec_payload,
    public_action_intent_payload,
    public_roll_payload,
    public_rules_hint_payload,
)
from aidm_server.validation import coerce_int


SEND_MESSAGE_REQUIRED_FIELDS = ['session_id', 'campaign_id', 'message', 'player_id']


@dataclass(frozen=True)
class SocketContractError:
    error_code: str
    message: str
    details: dict[str, Any] | None = None
    telemetry_suffix: str = 'validation_error'
    telemetry_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SendMessagePayload:
    session_id: int
    campaign_id: int
    world_id: int
    player_id: int
    user_input: str
    manual_segment_ids: set[int]
    action_intent: dict[str, Any] | None = None
    client_message_id: str | None = None


def socket_error_payload(error_code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_error(code=error_code, message=message, details=details)


def turn_duplicate_payload(session_id: int, turn_id: int, client_message_id: str) -> dict[str, Any]:
    return {
        'session_id': session_id,
        'turn_id': turn_id,
        'client_message_id': client_message_id,
    }


def session_log_update_payload(session_id: int, turn_id: int | None = None) -> dict[str, Any]:
    return {
        'session_id': session_id,
        'turn_id': turn_id,
    }


def session_recovery_resolved_payload(
    *,
    session_id: int,
    turn_id: int,
    state_revision: int,
) -> dict[str, Any]:
    return {
        'session_id': session_id,
        'turn_id': turn_id,
        'state_revision': state_revision,
        'recovery_required': False,
    }


def turn_status_payload(
    session_id: int,
    turn_id: int | None,
    status: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        'session_id': session_id,
        'turn_id': turn_id,
        'status': status,
        'details': details or {},
    }


def new_message_payload(
    *,
    message: str,
    speaker: str,
    turn_id: int,
    requires_roll: bool,
    rules_hint: dict[str, Any],
    context_version: str,
    action_intent: dict[str, Any] | None,
    client_message_id: str | None,
    include_private_provenance: bool,
    turn_number: int | None = None,
) -> dict[str, Any]:
    payload = {
        'message': message,
        'speaker': speaker,
        'turn_id': turn_id,
        'requires_roll': requires_roll,
        'rules_hint': rules_hint if include_private_provenance else public_rules_hint_payload(rules_hint),
        'context_version': context_version,
        'action_intent': (
            action_intent
            if include_private_provenance
            else public_action_intent_payload(action_intent)
        ),
        'client_message_id': client_message_id,
    }
    if turn_number is not None:
        payload['turn_number'] = turn_number
    return payload


def roll_required_payload(
    *,
    session_id: int,
    pending_turn_id: int,
    rule_type: str,
    dc_hint: str | None,
    prompt: str,
    remaining_player_ids: list[int] | None = None,
    roll_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        'session_id': session_id,
        'pending_turn_id': pending_turn_id,
        'rule_type': rule_type,
        'dc_hint': dc_hint,
        'prompt': prompt,
    }
    if remaining_player_ids is not None:
        payload['remaining_player_ids'] = remaining_player_ids
    if roll_spec is not None:
        payload['roll_spec'] = player_roll_spec_payload(roll_spec)
    return payload


def roll_resolved_payload(
    *,
    session_id: int,
    turn_id: int,
    player_id: int,
    client_message_id: str | None,
    pending_turn_id: int | None,
    roll: dict[str, Any],
    include_private_provenance: bool,
) -> dict[str, Any]:
    """Build a public room result or the acting player's private receipt."""

    projected_roll = roll if include_private_provenance else public_roll_payload(roll)
    payload = {
        'session_id': session_id,
        'turn_id': turn_id,
        'player_id': player_id,
        'client_message_id': client_message_id,
        'pending_turn_id': pending_turn_id,
        'rule_type': projected_roll.get('rule_type'),
        'die': projected_roll.get('die'),
        'mode': projected_roll.get('mode'),
        'rolls': list(projected_roll.get('rolls') or []),
        'kept': projected_roll.get('kept'),
        'modifier': projected_roll.get('modifier'),
        'total': projected_roll.get('total'),
        'reason': projected_roll.get('reason'),
        'result_visibility': projected_roll.get('result_visibility'),
        'authoritative': True,
    }
    if include_private_provenance:
        payload.update(
            {
                'ability': roll.get('ability') if isinstance(roll.get('ability'), dict) else None,
                'proficiency': (
                    roll.get('proficiency')
                    if isinstance(roll.get('proficiency'), dict)
                    else {'bonus': 0, 'skills': []}
                ),
                'modifier_breakdown': (
                    roll.get('modifier_breakdown')
                    if isinstance(roll.get('modifier_breakdown'), dict)
                    else {
                        'ability_modifier': 0,
                        'proficiency_bonus': 0,
                        'wound_penalty': 0,
                        'total': roll.get('modifier') or 0,
                    }
                ),
            }
        )
    return payload


def dm_response_start_payload(
    *,
    session_id: int,
    turn_id: int,
    requires_roll: bool,
    rules_hint: dict[str, Any],
    context_version: str,
    turn_number: int | None = None,
) -> dict[str, Any]:
    payload = {
        'session_id': session_id,
        'turn_id': turn_id,
        'requires_roll': requires_roll,
        'rules_hint': public_rules_hint_payload(rules_hint),
        'context_version': context_version,
    }
    if turn_number is not None:
        payload['turn_number'] = turn_number
    return payload


def dm_chunk_payload(
    *,
    chunk: str,
    session_id: int,
    turn_id: int,
    requires_roll: bool,
    rules_hint: dict[str, Any],
    context_version: str,
    turn_number: int | None = None,
) -> dict[str, Any]:
    payload = dm_response_start_payload(
        session_id=session_id,
        turn_id=turn_id,
        requires_roll=requires_roll,
        rules_hint=rules_hint,
        context_version=context_version,
        turn_number=turn_number,
    )
    payload['chunk'] = chunk
    return payload


def dm_response_end_payload(
    *,
    session_id: int,
    turn_id: int,
    requires_roll: bool,
    rules_hint: dict[str, Any],
    context_version: str,
    ok: bool,
    text: str | None = None,
    error: str | None = None,
    turn_number: int | None = None,
    degraded: bool = False,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dm_response_start_payload(
        session_id=session_id,
        turn_id=turn_id,
        requires_roll=requires_roll,
        rules_hint=rules_hint,
        context_version=context_version,
        turn_number=turn_number,
    )
    payload['ok'] = ok
    if text is not None:
        payload['text'] = text
    if error:
        payload['error'] = error
    if degraded:
        payload['degraded'] = True
    if fallback:
        payload['fallback'] = fallback
    return payload


def scene_state_payload(scene_state: dict[str, Any]) -> dict[str, Any]:
    return {
        'session_id': scene_state.get('session_id'),
        'location_id': scene_state.get('location_id'),
        'location_name': scene_state.get('location_name') or 'Unknown location',
        'scene_type': scene_state.get('scene_type') or 'scene',
        'mood': scene_state.get('mood'),
        'danger_level': scene_state.get('danger_level') or 0,
        'combat_state': scene_state.get('combat_state') or 'none',
        'in_combat': bool(scene_state.get('in_combat')),
        'music_tag': scene_state.get('music_tag') or 'calm',
        'acting_player_id': scene_state.get('acting_player_id'),
    }


def segment_triggered_payload(
    *,
    segment_id: int,
    title: str,
    description: str | None,
    reason: str,
    trigger_spec: dict[str, Any],
) -> dict[str, Any]:
    return {
        'segment_id': segment_id,
        'title': title,
        'description': description,
        'reason': reason,
        'trigger_spec': trigger_spec,
    }


def _missing_fields(data: dict[str, Any], required_fields: list[str]) -> list[str]:
    return [field_name for field_name in required_fields if data.get(field_name) in (None, '')]


def _coerce_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    parsed: list[int] = []
    for item in value:
        parsed_item = coerce_int(item)
        if parsed_item is not None:
            parsed.append(parsed_item)
    return parsed


def _validation_error(
    message: str,
    details: dict[str, Any] | None = None,
    *,
    telemetry_payload: dict[str, Any] | None = None,
) -> SocketContractError:
    return SocketContractError(
        error_code='validation_error',
        message=message,
        details=details,
        telemetry_payload=telemetry_payload or {},
    )


def validate_send_message_payload(data: Any) -> tuple[SendMessagePayload | None, SocketContractError | None]:
    """Normalize the public Socket.IO send_message contract before turn processing."""

    if not isinstance(data, dict):
        return None, _validation_error('Expected object payload for send_message.')

    missing = _missing_fields(data, SEND_MESSAGE_REQUIRED_FIELDS)
    if missing:
        return None, _validation_error(
            'Missing required data.',
            {'required_fields': SEND_MESSAGE_REQUIRED_FIELDS, 'missing_fields': missing},
            telemetry_payload={'missing_fields': missing},
        )

    session_id = coerce_int(data.get('session_id'))
    campaign_id = coerce_int(data.get('campaign_id'))
    world_id = coerce_int(data.get('world_id'), 0)
    player_id = coerce_int(data.get('player_id'))
    user_input = str(data.get('message') or '').strip()
    manual_segment_ids = set(_coerce_int_list(data.get('manual_trigger_segment_ids')))
    action_intent, action_error = validate_action_intent(data.get('action_intent'))
    if action_error:
        return None, _validation_error(
            action_error,
            telemetry_payload={'field': 'action_intent', 'error': action_error},
        )

    client_message_id = ''
    if action_intent:
        client_message_id = str(action_intent.get('client_message_id') or '').strip()
    client_message_id = client_message_id or str(data.get('client_message_id') or '').strip()[:80]
    if client_message_id and not ACTION_ID_RE.fullmatch(client_message_id):
        return None, _validation_error(
            'client_message_id contains unsupported characters.',
            telemetry_payload={'field': 'client_message_id'},
        )

    if not session_id or not campaign_id or not player_id or not user_input:
        return None, _validation_error('Invalid message payload types.')

    if (not action_intent or action_intent.get('kind') != 'admin') and has_reserved_admin_prefix(user_input):
        return None, SocketContractError(
            error_code='admin_prefix_reserved',
            message='Admin-prefixed messages require authenticated Admin mode.',
            telemetry_suffix='admin_prefix_reserved',
            telemetry_payload={'session_id': session_id, 'player_id': player_id},
        )

    if manual_segment_ids:
        return None, SocketContractError(
            error_code='manual_segment_override_disabled',
            message='Client-driven manual segment triggering is disabled.',
            telemetry_suffix='manual_segment_override_disabled',
            telemetry_payload={'session_id': session_id, 'player_id': player_id},
        )

    return (
        SendMessagePayload(
            session_id=session_id,
            campaign_id=campaign_id,
            world_id=world_id or 0,
            player_id=player_id,
            user_input=user_input,
            manual_segment_ids=manual_segment_ids,
            action_intent=action_intent,
            client_message_id=client_message_id or None,
        ),
        None,
    )
