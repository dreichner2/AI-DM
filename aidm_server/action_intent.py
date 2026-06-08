"""Typed player action intent validation and rules integration."""

from __future__ import annotations

import re
from typing import Any

from aidm_server.canon_inventory import INVENTORY_ACTIONS, clean_inventory_item_name, looks_like_inventory_item
from aidm_server.rules import DC_HINTS, RuleHint


VALID_ACTION_KINDS = {'message', 'roll', 'ability', 'item', 'interact', 'emote', 'ooc', 'admin'}
VALID_DICE = {'d4', 'd6', 'd8', 'd10', 'd12', 'd20', 'd100'}
VALID_ROLL_MODES = {'normal', 'advantage', 'disadvantage'}
VALID_RESULT_VISIBILITY = {'hidden_until_landed', 'visible'}
VALID_ABILITIES = {'strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma'}
VALID_INTERACTION_TYPES = {'speak_to', 'act_on', 'give_to', 'take_from'}
ACTION_TEXT_MAX_LENGTH = 2000
ACTION_REASON_MAX_LENGTH = 240
ACTION_ITEM_MAX_LENGTH = 120
ACTION_ID_MAX_LENGTH = 80
ACTION_NAME_MAX_LENGTH = 120
ACTION_ID_RE = re.compile(r'^[A-Za-z0-9._:-]+$')
RESERVED_ADMIN_PREFIX_RE = re.compile(
    r'^\s*(?:\[\s*admin\s*\]|\(\s*admin\s*\)|/\s*admin\s*/|/admin(?:\s+|$))',
    re.IGNORECASE,
)


def has_reserved_admin_prefix(value: Any) -> bool:
    """Return True when player text starts with an admin-only command marker."""

    return bool(RESERVED_ADMIN_PREFIX_RE.match(str(value or '')))


def strip_reserved_admin_prefix(value: Any) -> str:
    """Remove one reserved admin marker after admin auth has already succeeded."""

    text = str(value or '').strip()
    return RESERVED_ADMIN_PREFIX_RE.sub('', text, count=1).strip()


def _clean_text(value: Any, *, max_length: int) -> str:
    text = str(value or '').strip()
    return text[:max_length]


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _coerce_int_list(value: Any, *, min_value: int | None = None, max_value: int | None = None, max_count: int = 2) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value[:max_count]:
        parsed = _coerce_int(item)
        if parsed is None:
            continue
        if min_value is not None and parsed < min_value:
            continue
        if max_value is not None and parsed > max_value:
            continue
        result.append(parsed)
    return result


def _validate_roll(raw_roll: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(raw_roll, dict):
        return None, 'roll action metadata must include a roll object.'

    die = _clean_text(raw_roll.get('die'), max_length=8).lower() or 'd20'
    if die not in VALID_DICE:
        return None, f'roll.die must be one of {sorted(VALID_DICE)}.'

    sides = int(die[1:])
    mode = _clean_text(raw_roll.get('mode'), max_length=24).lower() or 'normal'
    if mode not in VALID_ROLL_MODES:
        return None, f'roll.mode must be one of {sorted(VALID_ROLL_MODES)}.'

    modifier = _coerce_int(raw_roll.get('modifier'))
    if modifier is None:
        modifier = 0
    if modifier < -99 or modifier > 99:
        return None, 'roll.modifier must be between -99 and 99.'

    rolls = _coerce_int_list(raw_roll.get('rolls'), min_value=1, max_value=sides, max_count=2)
    expected_roll_count = 2 if mode in {'advantage', 'disadvantage'} else 1
    if len(rolls) != expected_roll_count:
        return None, f'roll.rolls must include {expected_roll_count} value(s) for {mode}.'

    kept = _coerce_int(raw_roll.get('kept'))
    if kept is None:
        kept = max(rolls) if mode == 'advantage' else min(rolls) if mode == 'disadvantage' else rolls[0]
    if kept not in rolls:
        return None, 'roll.kept must match one of roll.rolls.'

    total = _coerce_int(raw_roll.get('total'))
    expected_total = kept + modifier
    if total is None:
        total = expected_total
    if total != expected_total:
        return None, 'roll.total must equal roll.kept plus roll.modifier.'

    visibility = _clean_text(raw_roll.get('result_visibility'), max_length=32).lower() or 'hidden_until_landed'
    if visibility not in VALID_RESULT_VISIBILITY:
        return None, f'roll.result_visibility must be one of {sorted(VALID_RESULT_VISIBILITY)}.'

    target_pending_turn_id = _coerce_int(raw_roll.get('target_pending_turn_id'))
    if raw_roll.get('target_pending_turn_id') not in (None, '') and (target_pending_turn_id is None or target_pending_turn_id < 1):
        return None, 'roll.target_pending_turn_id must be a positive integer.'

    normalized = {
        'die': die,
        'mode': mode,
        'modifier': modifier,
        'rolls': rolls,
        'kept': kept,
        'total': total,
        'result_visibility': visibility,
        'reason': _clean_text(raw_roll.get('reason'), max_length=ACTION_REASON_MAX_LENGTH),
    }
    if target_pending_turn_id is not None:
        normalized['target_pending_turn_id'] = target_pending_turn_id
    return normalized, None


def _validate_ability_payload(raw_ability: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(raw_ability, dict):
        return None, 'ability action metadata must include an ability object.'
    key = _clean_text(raw_ability.get('key'), max_length=32).lower()
    if key not in VALID_ABILITIES:
        return None, f'ability.key must be one of {sorted(VALID_ABILITIES)}.'
    modifier = _coerce_int(raw_ability.get('modifier'))
    return {
        'key': key,
        'label': _clean_text(raw_ability.get('label'), max_length=40) or key.title(),
        'modifier': modifier if modifier is not None else 0,
    }, None


def _coerce_non_negative_int(value: Any) -> int:
    parsed = _coerce_int(value)
    if parsed is None:
        return 0
    return max(0, parsed)


def validate_action_intent(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Return normalized action intent or a validation error message."""

    if value is None:
        return None, None
    if not isinstance(value, dict):
        return None, 'action_intent must be an object.'

    kind = _clean_text(value.get('kind'), max_length=24).lower() or 'message'
    if kind not in VALID_ACTION_KINDS:
        return None, f'action_intent.kind must be one of {sorted(VALID_ACTION_KINDS)}.'

    normalized: dict[str, Any] = {
        'kind': kind,
        'text': _clean_text(value.get('text'), max_length=ACTION_TEXT_MAX_LENGTH),
        'source': _clean_text(value.get('source'), max_length=40) or 'composer',
    }

    client_message_id = _clean_text(value.get('client_message_id'), max_length=ACTION_ID_MAX_LENGTH)
    if client_message_id:
        if not ACTION_ID_RE.fullmatch(client_message_id):
            return None, 'action_intent.client_message_id contains unsupported characters.'
        normalized['client_message_id'] = client_message_id

    if kind == 'roll':
        roll, error = _validate_roll(value.get('roll'))
        if error:
            return None, error
        normalized['roll'] = roll
        if value.get('ability') not in (None, ''):
            ability, ability_error = _validate_ability_payload(value.get('ability'))
            if ability_error:
                return None, ability_error
            normalized['ability'] = ability

    if kind == 'ability':
        ability, ability_error = _validate_ability_payload(value.get('ability'))
        if ability_error:
            return None, ability_error
        normalized['ability'] = ability

    if kind == 'item':
        inventory_action = _clean_text(value.get('inventory_action'), max_length=32).lower() or 'use'
        if inventory_action not in INVENTORY_ACTIONS:
            return None, f'inventory_action must be one of {sorted(INVENTORY_ACTIONS)}.'

        item = value.get('item')
        if not isinstance(item, dict):
            return None, 'item action metadata must include an item object.'
        name = clean_inventory_item_name(_clean_text(item.get('name'), max_length=ACTION_ITEM_MAX_LENGTH))
        if not name:
            return None, 'item.name is required.'
        if not looks_like_inventory_item(name):
            return None, 'item.name must be a tangible inventory item.'
        quantity = _coerce_int(item.get('quantity'))
        cost_gold = _coerce_non_negative_int(value.get('cost_gold', value.get('price_gold')))
        normalized['item'] = {
            'name': name,
            'quantity': quantity if quantity is not None and quantity > 0 else 1,
        }
        normalized['inventory_action'] = inventory_action
        normalized['cost_gold'] = cost_gold

    if kind == 'interact':
        interaction = value.get('interaction')
        if not isinstance(interaction, dict):
            return None, 'interact action metadata must include an interaction object.'
        interaction_type = _clean_text(interaction.get('type'), max_length=32).lower()
        if interaction_type not in VALID_INTERACTION_TYPES:
            return None, f'interaction.type must be one of {sorted(VALID_INTERACTION_TYPES)}.'

        target = value.get('target')
        if not isinstance(target, dict):
            return None, 'interact action metadata must include a target object.'
        target_player_id = _coerce_int(target.get('player_id'))
        if target_player_id is None or target_player_id < 1:
            return None, 'target.player_id must be a positive integer.'
        target_character_name = _clean_text(target.get('character_name'), max_length=ACTION_NAME_MAX_LENGTH)
        if not target_character_name:
            return None, 'target.character_name is required.'

        normalized['interaction'] = {
            'type': interaction_type,
            'label': _clean_text(interaction.get('label'), max_length=40) or interaction_type.replace('_', ' ').title(),
        }
        normalized['target'] = {
            'player_id': target_player_id,
            'character_name': target_character_name,
            'player_name': _clean_text(
                target.get('player_name') or target.get('name'),
                max_length=ACTION_NAME_MAX_LENGTH,
            ),
        }

    return normalized, None


def apply_action_intent_to_rule_hint(intent: dict[str, Any] | None, hint: RuleHint) -> RuleHint:
    """Let typed action metadata override brittle natural-language roll parsing."""

    if not intent:
        return hint

    kind = intent.get('kind')
    if kind == 'roll':
        roll = intent.get('roll') if isinstance(intent.get('roll'), dict) else {}
        ability = intent.get('ability') if isinstance(intent.get('ability'), dict) else {}
        ability_key = _clean_text(ability.get('key'), max_length=32).lower()
        total = _coerce_int(roll.get('total'))
        reason = _clean_text(roll.get('reason'), max_length=ACTION_REASON_MAX_LENGTH)
        hint.requires_roll = True
        hint.roll_type = hint.roll_type or ability_key or 'check'
        hint.dc_hint = hint.dc_hint or DC_HINTS['check']
        hint.reason = reason or (f'Typed {ability_key} ability check' if ability_key else 'Typed roll action')
        hint.confidence = max(hint.confidence or 0.0, 0.99)
        hint.roll_value = total
        hint.outcome_deferred = False
        return hint

    if kind == 'ability':
        ability = intent.get('ability') if isinstance(intent.get('ability'), dict) else {}
        ability_key = _clean_text(ability.get('key'), max_length=32).lower() or 'check'
        hint.requires_roll = True
        hint.roll_type = ability_key
        hint.dc_hint = hint.dc_hint or DC_HINTS['check']
        hint.reason = f'Typed {ability_key} ability check'
        hint.confidence = max(hint.confidence or 0.0, 0.96)
        hint.outcome_deferred = hint.roll_value is None
        return hint

    if kind in {'ooc', 'emote', 'item', 'admin'}:
        hint.requires_roll = False
        hint.roll_type = None
        hint.dc_hint = None
        hint.outcome_deferred = False
        if kind == 'admin':
            hint.reason = 'Authenticated admin override'
            hint.confidence = 1.0

    return hint
