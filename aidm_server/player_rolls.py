"""Server-authoritative player dice and canonical roll presentation."""

from __future__ import annotations

from copy import deepcopy
import re
import secrets
from typing import Any, Callable

from aidm_server.action_intent import VALID_ABILITIES, VALID_DICE, VALID_RESULT_VISIBILITY, VALID_ROLL_MODES
from aidm_server.character_state import character_roll_spec
from aidm_server.models import DmTurn, Player, safe_json_loads
from aidm_server.player_roll_claims import find_legacy_roll_claim


Roller = Callable[[int], int]


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any, *, max_length: int) -> str:
    return str(value or '').strip()[:max_length]


def _pending_rules_hint(pending_turn: DmTurn | None) -> dict[str, Any]:
    if pending_turn is None:
        return {}
    payload = safe_json_loads(pending_turn.rules_hint, {})
    return payload if isinstance(payload, dict) else {}


def pending_roll_spec(pending_turn: DmTurn | None) -> dict[str, Any]:
    rules_hint = _pending_rules_hint(pending_turn)
    spec = rules_hint.get('roll_spec')
    if isinstance(spec, dict):
        return spec
    metadata = safe_json_loads(pending_turn.metadata_json, {}) if pending_turn is not None else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    gate = metadata.get('roll_gate') if isinstance(metadata.get('roll_gate'), dict) else {}
    spec = gate.get('roll_spec')
    return spec if isinstance(spec, dict) else {}


def _ability_key(value: Any) -> str | None:
    key = _clean_text(_record(value).get('key'), max_length=32).lower()
    return key if key in VALID_ABILITIES else None


def requested_ability_key(
    action_intent: dict[str, Any] | None,
    pending_turn: DmTurn | None = None,
) -> str | None:
    spec = pending_roll_spec(pending_turn)
    spec_ability = spec.get('ability') if isinstance(spec.get('ability'), dict) else {}
    key = _ability_key(spec_ability)
    if key:
        return key

    if pending_turn is not None:
        metadata = safe_json_loads(pending_turn.metadata_json, {})
        metadata = metadata if isinstance(metadata, dict) else {}
        pending_intent = metadata.get('action_intent') if isinstance(metadata.get('action_intent'), dict) else {}
        key = _ability_key(pending_intent.get('ability'))
        if key:
            return key

    intent = action_intent if isinstance(action_intent, dict) else {}
    return _ability_key(intent.get('ability'))


def _requested_roll(action_intent: dict[str, Any] | None) -> dict[str, Any]:
    intent = action_intent if isinstance(action_intent, dict) else {}
    roll = intent.get('roll')
    return roll if isinstance(roll, dict) else {}


def _roll_setting(
    pending_spec: dict[str, Any],
    requested_roll: dict[str, Any],
    key: str,
    allowed: set[str],
    default: str,
) -> str:
    pending_value = _clean_text(pending_spec.get(key), max_length=32).lower()
    if pending_value in allowed:
        return pending_value
    requested_value = _clean_text(requested_roll.get(key), max_length=32).lower()
    return requested_value if requested_value in allowed else default


def _authoritative_roll_setting(
    pending_turn: DmTurn | None,
    pending_spec: dict[str, Any],
    requested_roll: dict[str, Any],
    key: str,
    allowed: set[str],
    default: str,
) -> str:
    """Resolve a setting without letting a pending check inherit client state."""

    if pending_turn is not None:
        pending_value = _clean_text(pending_spec.get(key), max_length=32).lower()
        return pending_value if pending_value in allowed else default
    return _roll_setting(pending_spec, requested_roll, key, allowed, default)


def _secure_die(sides: int, roller: Roller | None) -> int:
    value = roller(sides) if roller else secrets.randbelow(sides) + 1
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError('Player roll source returned a non-integer result.') from None
    if parsed < 1 or parsed > sides:
        raise ValueError('Player roll source returned a result outside the die bounds.')
    return parsed


def resolve_authoritative_player_roll(
    *,
    player: Player,
    rule_type: str | None,
    dc_hint: str | None,
    action_intent: dict[str, Any] | None,
    pending_turn: DmTurn | None = None,
    attack_context: dict[str, Any] | None = None,
    roller: Roller | None = None,
) -> dict[str, Any]:
    """Generate one canonical player result without trusting client outcomes."""

    requested_roll = _requested_roll(action_intent)
    stored_spec = pending_roll_spec(pending_turn)
    die = _authoritative_roll_setting(
        pending_turn, stored_spec, requested_roll, 'die', VALID_DICE, 'd20'
    )
    mode = _authoritative_roll_setting(
        pending_turn, stored_spec, requested_roll, 'mode', VALID_ROLL_MODES, 'normal'
    )
    visibility = _authoritative_roll_setting(
        pending_turn,
        stored_spec,
        requested_roll,
        'result_visibility',
        VALID_RESULT_VISIBILITY,
        'hidden_until_landed',
    )
    ability_key = requested_ability_key(action_intent, pending_turn)
    stored_attack_context = stored_spec.get('attack') if isinstance(stored_spec.get('attack'), dict) else None
    effective_attack_context = stored_attack_context or attack_context
    spec = character_roll_spec(
        player,
        roll_type=rule_type,
        requested_ability_key=ability_key,
        dc_hint=dc_hint,
        attack_context=effective_attack_context,
    )
    sides = int(die[1:])
    rolls = [_secure_die(sides, roller)]
    if mode in {'advantage', 'disadvantage'}:
        rolls.append(_secure_die(sides, roller))
    kept = max(rolls) if mode == 'advantage' else min(rolls) if mode == 'disadvantage' else rolls[0]
    modifier = int(spec.get('modifier') or 0)
    reason = _clean_text(requested_roll.get('reason'), max_length=240)
    if not reason:
        reason = str(rule_type or 'check').replace('_', ' ')

    return {
        'rule_type': str(rule_type or 'check').strip().lower() or 'check',
        'die': die,
        'mode': mode,
        'rolls': rolls,
        'kept': kept,
        'modifier': modifier,
        'total': kept + modifier,
        'reason': reason,
        'result_visibility': visibility,
        'ability': deepcopy(spec.get('ability')) if isinstance(spec.get('ability'), dict) else None,
        'proficiency': deepcopy(spec.get('proficiency')) if isinstance(spec.get('proficiency'), dict) else {'bonus': 0, 'skills': []},
        'modifier_breakdown': (
            deepcopy(spec.get('modifier_breakdown'))
            if isinstance(spec.get('modifier_breakdown'), dict)
            else {'ability_modifier': 0, 'proficiency_bonus': 0, 'wound_penalty': 0, 'total': modifier}
        ),
        'task_dc': spec.get('task_dc'),
        'attack': deepcopy(spec.get('attack')) if isinstance(spec.get('attack'), dict) else None,
        'authoritative': True,
    }


def canonical_roll_sentence(roll: dict[str, Any]) -> str:
    modifier = int(roll.get('modifier') or 0)
    modifier_text = f'{modifier:+d}' if modifier else ''
    reason = _clean_text(roll.get('reason'), max_length=240)
    reason_text = f' for {reason}' if reason else ''
    faces = [int(value) for value in (roll.get('rolls') or [])]
    kept = int(roll.get('kept') or 0)
    total = int(roll.get('total') or kept + modifier)
    mode = str(roll.get('mode') or 'normal')
    detail = str(kept)
    if mode in {'advantage', 'disadvantage'}:
        detail = f'{kept} ({mode}; rolls {", ".join(str(value) for value in faces)})'
    return f"I roll a {roll.get('die') or 'd20'}{modifier_text}{reason_text}: {detail} = {total}"


def _clean_action_prefix(value: str) -> str:
    prefix = value.strip()
    while prefix:
        cleaned = re.sub(
            r'\s*(?:[,;:—–-]\s*)?\b(?:and|then)\b\s*$',
            '',
            prefix,
            flags=re.IGNORECASE,
        ).strip()
        if cleaned == prefix:
            break
        prefix = cleaned
    return re.sub(r'[,;:—–-]+\s*$', '', prefix).strip()


def _preserved_action_text(text: str, *, claim_start: int, claim_end: int) -> str:
    prefix = _clean_action_prefix(text[:claim_start])
    suffix = re.sub(r'^[\s,;:.!?—–-]+', '', text[claim_end:]).strip()
    if prefix and suffix:
        separator = ', ' if re.match(r'^(?:and|then)\b', suffix, flags=re.IGNORECASE) else ' '
        return f'{prefix}{separator}{suffix}'
    if prefix:
        return prefix
    if suffix.lower().startswith('to '):
        return f'I attempt {suffix}'
    return suffix


def canonicalize_roll_text(original_text: str, roll: dict[str, Any]) -> str:
    """Remove a client-claimed outcome while preserving surrounding action text."""

    text = str(original_text or '').strip()
    sentence = canonical_roll_sentence(roll)
    claim = find_legacy_roll_claim(text)
    if claim is None:
        return f'{text}\n{sentence}'.strip()

    action_text = _preserved_action_text(text, claim_start=claim.start, claim_end=claim.end)
    if not action_text:
        return sentence
    return f'{action_text}\n{sentence}'


def canonicalize_roll_action_intent(
    action_intent: dict[str, Any] | None,
    *,
    canonical_text: str,
    client_message_id: str | None,
    roll: dict[str, Any],
    pending_turn_id: int | None,
) -> dict[str, Any]:
    intent = deepcopy(action_intent) if isinstance(action_intent, dict) else {}
    intent.update(
        {
            'kind': 'roll',
            'source': str(intent.get('source') or 'server_legacy_text'),
            'text': canonical_text,
        }
    )
    if client_message_id:
        intent['client_message_id'] = client_message_id

    canonical_roll = {
        key: deepcopy(roll.get(key))
        for key in (
            'die',
            'mode',
            'rolls',
            'kept',
            'modifier',
            'total',
            'reason',
            'result_visibility',
            'modifier_breakdown',
            'authoritative',
        )
    }
    if pending_turn_id:
        canonical_roll['target_pending_turn_id'] = pending_turn_id
    intent['roll'] = canonical_roll
    if isinstance(roll.get('ability'), dict):
        intent['ability'] = deepcopy(roll['ability'])
    else:
        intent.pop('ability', None)
    return intent
