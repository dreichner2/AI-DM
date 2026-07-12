"""Viewer-safe projections for persisted and realtime roll metadata."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PUBLIC_ROLL_KEYS = (
    'rule_type',
    'die',
    'mode',
    'rolls',
    'kept',
    'modifier',
    'total',
    'reason',
    'result_visibility',
    'pending_turn_id',
)

PUBLIC_ROLL_SPEC_KEYS = (
    'die',
    'mode',
    'rule_type',
    'reason',
    'result_visibility',
)

PRIVATE_TURN_PIPELINE_KEYS = (
    'state_pipeline',
    'statePipeline',
    'clarificationRequest',
    'clarification_request',
    'clarificationResume',
    'clarification_resume',
)


def public_roll_payload(roll: Any) -> dict[str, Any]:
    """Return public outcome fields without character-sheet provenance."""

    if not isinstance(roll, dict):
        return {}
    return {key: deepcopy(roll[key]) for key in PUBLIC_ROLL_KEYS if key in roll}


def public_roll_spec_payload(spec: Any) -> dict[str, Any]:
    """Return request/presentation fields without scores, DCs, or modifiers."""

    if not isinstance(spec, dict):
        return {}
    return {key: deepcopy(spec[key]) for key in PUBLIC_ROLL_SPEC_KEYS if key in spec}


def player_roll_spec_payload(spec: Any) -> dict[str, Any]:
    """Project roll guidance for the acting player without numeric provenance."""

    payload = public_roll_spec_payload(spec)
    if not isinstance(spec, dict):
        return payload
    ability = spec.get('ability')
    if isinstance(ability, dict):
        public_ability = {
            key: deepcopy(ability[key])
            for key in ('key', 'label')
            if key in ability
        }
        if public_ability:
            payload['ability'] = public_ability
    return payload


def public_action_intent_payload(action_intent: Any) -> Any:
    if not isinstance(action_intent, dict):
        return deepcopy(action_intent)
    payload = deepcopy(action_intent)
    ability = payload.get('ability')
    if isinstance(ability, dict):
        payload['ability'] = {
            key: deepcopy(ability[key])
            for key in ('key', 'label')
            if key in ability
        }
    if isinstance(payload.get('roll'), dict):
        payload['roll'] = public_roll_payload(payload['roll'])
    if isinstance(payload.get('roll_spec'), dict):
        payload['roll_spec'] = public_roll_spec_payload(payload['roll_spec'])
    for private_key in (
        'attack',
        'dc_hint',
        'modifier',
        'modifier_breakdown',
        'proficiency',
        'task_dc',
    ):
        payload.pop(private_key, None)
    return payload


def public_roll_gate_payload(roll_gate: Any) -> Any:
    if not isinstance(roll_gate, dict):
        return deepcopy(roll_gate)
    payload = deepcopy(roll_gate)
    if isinstance(payload.get('roll_spec'), dict):
        payload['roll_spec'] = public_roll_spec_payload(payload['roll_spec'])
    payload.pop('dc_hint', None)
    return payload


def public_rules_hint_payload(rules_hint: Any) -> dict[str, Any]:
    """Redact viewer-specific roll mechanics from a room-wide rules hint."""

    if not isinstance(rules_hint, dict):
        return {}
    payload = deepcopy(rules_hint)
    payload.pop('dc_hint', None)
    if isinstance(payload.get('roll_spec'), dict):
        payload['roll_spec'] = public_roll_spec_payload(payload['roll_spec'])
    if isinstance(payload.get('authoritative_roll'), dict):
        payload['authoritative_roll'] = public_roll_payload(payload['authoritative_roll'])
    if isinstance(payload.get('roll_gate'), dict):
        payload['roll_gate'] = public_roll_gate_payload(payload['roll_gate'])
    return payload


def public_turn_metadata_payload(metadata: Any) -> Any:
    if not isinstance(metadata, dict):
        return deepcopy(metadata)
    payload = deepcopy(metadata)
    for private_key in PRIVATE_TURN_PIPELINE_KEYS:
        payload.pop(private_key, None)
    payload.pop('dc_hint', None)
    if isinstance(payload.get('action_intent'), dict):
        payload['action_intent'] = public_action_intent_payload(payload['action_intent'])
    if isinstance(payload.get('authoritative_roll'), dict):
        payload['authoritative_roll'] = public_roll_payload(payload['authoritative_roll'])
    if isinstance(payload.get('roll_spec'), dict):
        payload['roll_spec'] = public_roll_spec_payload(payload['roll_spec'])
    if isinstance(payload.get('roll_gate'), dict):
        payload['roll_gate'] = public_roll_gate_payload(payload['roll_gate'])
    if isinstance(payload.get('rules_hint'), dict):
        payload['rules_hint'] = public_rules_hint_payload(payload['rules_hint'])
    return payload


def public_turn_event_payload(payload: Any) -> Any:
    """Project one stored turn-event body for a non-owning player viewer."""

    if not isinstance(payload, dict):
        return deepcopy(payload)
    projected = deepcopy(payload)
    for private_key in PRIVATE_TURN_PIPELINE_KEYS:
        projected.pop(private_key, None)
    if isinstance(projected.get('roll'), dict):
        projected['roll'] = public_roll_payload(projected['roll'])
    if isinstance(projected.get('action_intent'), dict):
        projected['action_intent'] = public_action_intent_payload(projected['action_intent'])
    if isinstance(projected.get('rules_hint'), dict):
        projected['rules_hint'] = public_rules_hint_payload(projected['rules_hint'])
    if isinstance(projected.get('metadata'), dict):
        projected['metadata'] = public_turn_metadata_payload(projected['metadata'])
    return projected


def public_segment_triggered_payload(payload: Any) -> dict[str, Any]:
    """Expose the revealed story beat without its private trigger recipe."""

    if not isinstance(payload, dict):
        return {}
    return {
        key: deepcopy(payload[key])
        for key in ('segment_id', 'title', 'description')
        if key in payload
    }
