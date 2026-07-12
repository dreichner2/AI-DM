from __future__ import annotations

import re
from typing import Any, Callable

from flask import current_app, has_app_context

from aidm_server.contracts import ProviderRequest
from aidm_server.game_state.extraction.prompts import PRE_DM_SYSTEM_MESSAGE, build_pre_dm_prompt
from aidm_server.game_state.extraction.schemas import extract_json_object, normalize_pre_extraction
from aidm_server.game_state.models import normalize_item_name
from aidm_server.llm_providers import get_helper_provider
from aidm_server.provider_priority import foreground_provider_reservation
from aidm_server.telemetry import telemetry_event, telemetry_metric


HELPER_RAW_PREVIEW_LIMIT = 2000
ACTIONABLE_PATTERN = re.compile(
    r'\b(?:use|drink|consume|quaff|swallow|eat|attack|shoot|strike|swing|slash|stab|cast|move|go to|take|pick|grab|collect|gather|retrieve|pocket|loot|buy|sell|give|pay|spend|equip|unequip|wield|wear|don|doff|stow|sheathe|drop|heal)\b',
    re.IGNORECASE,
)
EQUIP_PATTERN = re.compile(
    r'\b(?:equip|wield|ready|wear|don|put\s+on|strap\s+on|draw)\s+'
    r'(?:my|the|a|an|your)?\s*(?P<item>[a-z][a-z0-9\' -]{1,60}?)(?=\s+(?:and|then|while|before|after|in|on)\b|[.!?,;]|$)',
    re.IGNORECASE,
)
UNEQUIP_PATTERN = re.compile(
    r'\b(?:unequip|doff|stow|sheathe|put\s+away|take\s+off|remove)\s+'
    r'(?:my|the|a|an|your)?\s*(?P<item>[a-z][a-z0-9\' -]{1,60}?)(?=\s+(?:and|then|while|before|after|from)\b|[.!?,;]|$)',
    re.IGNORECASE,
)
CONSUME_PATTERN = re.compile(
    r'\b(?P<verb>drink|consume|quaff|swallow|eat)\s+(?:my|the|a|an|one|your)?\s*'
    r'(?P<item>[a-z][a-z0-9\' -]{1,60}?)(?=\s+(?:and|then|while|before|after)\b|[.!?,;]|$)',
    re.IGNORECASE,
)
ATTACK_WITH_PATTERN = re.compile(
    r'\b(?P<verb>shoot|fire|loose|attack|strike|swing|slash|stab)\b'
    r'(?P<body>[^.!?]{0,120}?)\bwith\s+(?:my|the|a|an)?\s*(?P<weapon>[a-z][a-z0-9\' -]{1,40}?)(?=\s+(?:and|then)\b|[.!?,;]|$)',
    re.IGNORECASE,
)
IMPLIED_WEAPON_ATTACK_PATTERN = re.compile(
    r'\b(?P<verb>stab|slash|slice|cut|shoot|fire|loose)\b(?P<body>[^.!?]{0,120})',
    re.IGNORECASE,
)
USE_WEAPON_PATTERN = re.compile(
    r'\buse\s+(?:my|the|a|an)?\s*(?P<weapon>[a-z][a-z0-9\' -]{1,40}?)\s+to\s+'
    r'(?P<verb>swing|attack|strike|slash|stab|shoot|fire)\b(?P<body>[^.!?]{0,100})',
    re.IGNORECASE,
)
CURRENCY_TRANSFER_PATTERN = re.compile(
    r'\b(?:give|pay|hand over)\s+(?P<amount>\d{1,5})\s+'
    r'(?P<currency>pp|gp|ep|sp|cp|platinum|gold|electrum|silver|copper)\b'
    r'(?:\s+(?:pieces?|coins?))?\s+to\s+(?P<target>[A-Z][a-zA-Z0-9 _-]{1,40})',
    re.IGNORECASE,
)
ITEM_TRANSFER_PATTERN = re.compile(
    r'\b(?:give|hand|pass)\s+(?:the|a|an|my)?\s*(?P<item>[a-z][a-z0-9\' -]{1,50}?)\s+'
    r'(?:to|over to)\s+(?P<target>[A-Z][a-zA-Z0-9 _-]{1,40})(?=[.!?,;]|$)',
    re.IGNORECASE,
)
PICKUP_PATTERN = re.compile(
    r'\b(?:pick\s+up|pick|grab|collect|gather|retrieve|pocket|take)\s+'
    r'(?:my|the|a|an|some|one|your)?\s*(?P<item>[a-z][a-z0-9\' -]{1,60}?)'
    r'(?:\s+up)?(?=\s+(?:and|then|while|before|after|from)\b|[.!?,;]|$)',
    re.IGNORECASE,
)

CURRENCY_WORDS = {
    'platinum': 'pp',
    'gold': 'gp',
    'electrum': 'ep',
    'silver': 'sp',
    'copper': 'cp',
}


def _helper_enabled() -> bool:
    if has_app_context() and current_app.config.get('AIDM_ENV') == 'test':
        return bool(current_app.config.get('AIDM_STATE_PIPELINE_HELPER_IN_TESTS', False))
    if has_app_context():
        return bool(current_app.config.get('AIDM_STATE_PIPELINE_HELPER_ENABLED', True))
    return True


def _pre_payload_schema_valid(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    known_keys = {
        'declaredActions',
        'declared_actions',
        'notes',
        'rollRequirement',
        'roll_requirement',
        'rollRequired',
        'roll_required',
        'requiresRoll',
        'requires_roll',
    }
    if not any(key in payload for key in known_keys):
        return False
    for key in ('declaredActions', 'declared_actions'):
        if key in payload and not isinstance(payload.get(key), list):
            return False
    if 'notes' in payload and not isinstance(payload.get('notes'), (list, str)):
        return False
    return True


def _attach_debug(payload: dict[str, Any], debug: dict[str, Any]) -> dict[str, Any]:
    payload['debug'] = debug
    return payload


def _empty_helper_debug(*, source: str, reason: str) -> dict[str, Any]:
    return {
        'source': source,
        'reason': reason,
        'helperAttempted': False,
        'helperSchemaValid': False,
        'helperModel': None,
        'helperRawText': None,
        'helperRawPreview': None,
        'helperParsed': None,
        'helperError': None,
        'fallbackRan': False,
        'fallbackReason': None,
    }


def _clean_item_phrase(value: str) -> str:
    text = normalize_item_name(value)
    text = re.sub(r'\b(?:my|the|a|an|one|your)\b', '', text).strip()
    text = re.sub(r'\s+', ' ', text)
    return text


def _next_id(actions: list[dict[str, Any]]) -> str:
    return f'act_{len(actions) + 1:03d}'


def _target_from_attack_body(body: str) -> str | None:
    match = re.search(r'\b(?:at|the)\s+(?P<target>[a-z][a-z0-9\' -]{1,40})', body or '', re.IGNORECASE)
    return match.group('target').strip() if match else None


def _implied_weapon_for_attack(verb: str) -> str:
    normalized = normalize_item_name(verb)
    if normalized in {'shoot', 'fire', 'loose'}:
        return 'ranged weapon'
    return 'blade'


def _heuristic_extract(player_message: str, *, actor_id: str) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    for pattern, action_type in ((EQUIP_PATTERN, 'inventory.equip'), (UNEQUIP_PATTERN, 'inventory.unequip')):
        for match in pattern.finditer(player_message or ''):
            item_name = _clean_item_phrase(match.group('item'))
            if not item_name:
                continue
            actions.append(
                {
                    'id': _next_id(actions),
                    'type': action_type,
                    'actorId': actor_id,
                    'confidence': 0.86,
                    'sourceText': match.group(0).strip(),
                    'requiresDMResolution': False,
                    'itemName': item_name,
                }
            )

    for match in CONSUME_PATTERN.finditer(player_message or ''):
        item_name = _clean_item_phrase(match.group('item'))
        if not item_name:
            continue
        actions.append(
            {
                'id': _next_id(actions),
                'type': 'inventory.consume',
                'actorId': actor_id,
                'confidence': 0.86,
                'sourceText': match.group(0).strip(),
                'requiresDMResolution': False,
                'itemName': item_name,
                'quantity': 1,
            }
        )

    for match in USE_WEAPON_PATTERN.finditer(player_message or ''):
        weapon_name = _clean_item_phrase(match.group('weapon'))
        if not weapon_name:
            continue
        actions.append(
            {
                'id': _next_id(actions),
                'type': 'combat.attack',
                'actorId': actor_id,
                'confidence': 0.84,
                'sourceText': match.group(0).strip(),
                'requiresDMResolution': True,
                'targetName': _target_from_attack_body(match.group('body')),
                'weaponName': weapon_name,
                'attackStyle': 'ranged' if 'shoot' in match.group('verb').lower() or 'fire' in match.group('verb').lower() else 'melee',
            }
        )

    for match in ATTACK_WITH_PATTERN.finditer(player_message or ''):
        weapon_name = _clean_item_phrase(match.group('weapon'))
        if not weapon_name:
            continue
        source_text = match.group(0).strip()
        if any(action.get('sourceText') == source_text for action in actions):
            continue
        verb = match.group('verb').lower()
        actions.append(
            {
                'id': _next_id(actions),
                'type': 'combat.attack',
                'actorId': actor_id,
                'confidence': 0.88,
                'sourceText': source_text,
                'requiresDMResolution': True,
                'targetName': _target_from_attack_body(match.group('body')),
                'weaponName': weapon_name,
                'attackStyle': 'ranged' if verb in {'shoot', 'fire', 'loose'} else 'melee',
            }
        )

    for match in IMPLIED_WEAPON_ATTACK_PATTERN.finditer(player_message or ''):
        source_text = match.group(0).strip()
        body = match.group('body') or ''
        if re.search(r'\bwith\b', body, re.IGNORECASE):
            continue
        if any(action.get('sourceText') == source_text for action in actions):
            continue
        verb = match.group('verb').lower()
        actions.append(
            {
                'id': _next_id(actions),
                'type': 'combat.attack',
                'actorId': actor_id,
                'confidence': 0.8,
                'sourceText': source_text,
                'requiresDMResolution': True,
                'targetName': _target_from_attack_body(body),
                'weaponName': _implied_weapon_for_attack(verb),
                'attackStyle': 'ranged' if verb in {'shoot', 'fire', 'loose'} else 'melee',
                'summary': f"Player attempts to {verb} using an implied {_implied_weapon_for_attack(verb)}.",
            }
        )

    for match in CURRENCY_TRANSFER_PATTERN.finditer(player_message or ''):
        currency = match.group('currency').lower()
        actions.append(
            {
                'id': _next_id(actions),
                'type': 'currency.transfer',
                'actorId': actor_id,
                'fromActorId': actor_id,
                'confidence': 0.82,
                'sourceText': match.group(0).strip(),
                'requiresDMResolution': True,
                'toActorName': (match.group('target') or 'target').strip(),
                'amount': int(match.group('amount')),
                'currency': CURRENCY_WORDS.get(currency, currency),
            }
        )

    for match in ITEM_TRANSFER_PATTERN.finditer(player_message or ''):
        item_name = _clean_item_phrase(match.group('item'))
        if not item_name or re.search(r'\b(?:gp|gold|sp|silver|cp|copper|pp|platinum|ep|electrum)\b', item_name):
            continue
        actions.append(
            {
                'id': _next_id(actions),
                'type': 'inventory.transfer',
                'actorId': actor_id,
                'fromActorId': actor_id,
                'confidence': 0.78,
                'sourceText': match.group(0).strip(),
                'requiresDMResolution': True,
                'toActorName': match.group('target').strip(),
                'itemName': item_name,
                'quantity': 1,
            }
        )

    for match in PICKUP_PATTERN.finditer(player_message or ''):
        item_name = _clean_item_phrase(match.group('item'))
        if not item_name or re.search(r'\b(?:gp|gold|sp|silver|cp|copper|pp|platinum|ep|electrum)\b', item_name):
            continue
        source_text = match.group(0).strip()
        if any(action.get('sourceText') == source_text for action in actions):
            continue
        actions.append(
            {
                'id': _next_id(actions),
                'type': 'generic.intent',
                'actorId': actor_id,
                'confidence': 0.82,
                'sourceText': source_text,
                'requiresDMResolution': True,
                'summary': f"Player attempts to pick up {item_name}.",
            }
        )

    return {'declaredActions': actions, 'notes': ['heuristic_pre_dm'] if actions else []}


def _extract_from_action_intent(action_intent: dict[str, Any] | None, *, actor_id: str, player_message: str) -> dict[str, Any] | None:
    if not isinstance(action_intent, dict) or action_intent.get('kind') != 'item':
        return None
    item = action_intent.get('item') if isinstance(action_intent.get('item'), dict) else {}
    item_name = str(item.get('name') or '').strip()
    item_id = str(item.get('id') or item.get('itemId') or '').strip()
    if not item_name:
        return None
    inventory_action = str(action_intent.get('inventory_action') or 'use').strip().lower()
    if inventory_action in {'pick_up', 'buy'}:
        summary = f"Player attempts to {inventory_action.replace('_', ' ')} {item_name}."
        cost_gold = action_intent.get('cost_gold')
        if inventory_action == 'buy' and cost_gold:
            summary = f"{summary} Known price: {cost_gold} gold."
        return {
            'declaredActions': [
                {
                    'id': 'act_001',
                    'type': 'generic.intent',
                    'actorId': actor_id,
                    'confidence': 0.94,
                    'sourceText': player_message,
                    'requiresDMResolution': True,
                    'summary': summary,
                }
            ],
            'notes': ['action_intent_pre_dm'],
        }
    if inventory_action in {'equip', 'unequip'}:
        action_type = f'inventory.{inventory_action}'
        return {
            'declaredActions': [
                {
                    'id': 'act_001',
                    'type': action_type,
                    'actorId': actor_id,
                    'confidence': 0.96,
                    'sourceText': player_message,
                    'requiresDMResolution': False,
                    'itemName': item_name,
                    **({'itemId': item_id} if item_id else {}),
                }
            ],
            'notes': ['action_intent_pre_dm'],
        }

    if inventory_action in {'drop', 'give', 'sell'}:
        summary = f"Player attempts to {inventory_action} {item_name}."
        if inventory_action == 'sell' and action_intent.get('cost_gold'):
            summary = f"{summary} Asking price: {action_intent.get('cost_gold')} gold."
        return {
            'declaredActions': [
                {
                    'id': 'act_001',
                    'type': 'generic.intent',
                    'actorId': actor_id,
                    'confidence': 0.92,
                    'sourceText': player_message,
                    'requiresDMResolution': True,
                    'summary': summary,
                }
            ],
            'notes': ['action_intent_pre_dm'],
        }

    action_type = 'inventory.consume' if inventory_action == 'use' and 'potion' in normalize_item_name(item_name) else 'inventory.use'
    return {
        'declaredActions': [
            {
                'id': 'act_001',
                'type': action_type,
                'actorId': actor_id,
                'confidence': 0.96,
                'sourceText': player_message,
                'requiresDMResolution': action_type != 'inventory.consume',
                'itemName': item_name,
                **({'itemId': item_id} if item_id else {}),
                'quantity': int(item.get('quantity') or 1),
            }
        ],
        'notes': ['action_intent_pre_dm'],
    }


def extract_pre_dm_actions(
    *,
    current_state: dict[str, Any],
    player_message: str,
    recent_timeline: list[dict[str, Any]],
    actor_id: str,
    action_intent: dict[str, Any] | None = None,
    force_helper: bool = False,
    before_provider_call: Callable[[], None] | None = None,
) -> dict[str, Any]:
    intent_payload = _extract_from_action_intent(action_intent, actor_id=actor_id, player_message=player_message)
    if intent_payload:
        return _attach_debug(
            normalize_pre_extraction(intent_payload, fallback_actor_id=actor_id),
            _empty_helper_debug(source='action_intent', reason='client_action_intent'),
        )

    helper_enabled = _helper_enabled()
    if not ACTIONABLE_PATTERN.search(player_message or '') and not (force_helper and helper_enabled):
        return _attach_debug(
            {'declaredActions': [], 'notes': ['no_actionable_intent']},
            _empty_helper_debug(source='skipped', reason='no_actionable_intent'),
        )

    helper_payload: dict[str, Any] | None = None
    helper_attempted = False
    helper_schema_valid = False
    helper_model: str | None = None
    helper_raw_text: str | None = None
    helper_raw_preview: str | None = None
    helper_error: str | None = None
    fallback_reason = 'helper_disabled' if not helper_enabled else 'helper_empty_actions'
    if helper_enabled:
        helper_attempted = True
        with foreground_provider_reservation() as activate_provider:
            if before_provider_call:
                before_provider_call()
            try:
                prompt = build_pre_dm_prompt(
                    current_state=current_state,
                    player_message=player_message,
                    recent_timeline=recent_timeline,
                )
                activate_provider()
                response = get_helper_provider().generate(
                    ProviderRequest(prompt=prompt, system_message=PRE_DM_SYSTEM_MESSAGE)
                )
                helper_model = response.model
                helper_raw_text = str(response.text or '')
                helper_raw_preview = helper_raw_text[:HELPER_RAW_PREVIEW_LIMIT]
                helper_payload = extract_json_object(response.text)
                helper_schema_valid = _pre_payload_schema_valid(helper_payload)
                if helper_schema_valid:
                    telemetry_metric('state_pipeline.pre_dm_helper.success_total', 1, tags={'model': response.model})
                else:
                    fallback_reason = 'helper_json_invalid' if helper_payload is None else 'helper_schema_invalid'
                    telemetry_event(
                        'state_pipeline.pre_dm_helper.invalid_json',
                        payload={'model': response.model, 'reason': fallback_reason},
                        severity='warning',
                    )
            except Exception as exc:
                fallback_reason = 'helper_error'
                helper_error = str(exc)[:300]
                telemetry_event(
                    'state_pipeline.pre_dm_helper.failed',
                    payload={'error': helper_error},
                    severity='warning',
                )

    helper_debug = {
        'source': 'helper' if helper_schema_valid else 'heuristic',
        'reason': None,
        'helperAttempted': helper_attempted,
        'helperSchemaValid': helper_schema_valid,
        'helperModel': helper_model,
        'helperRawText': helper_raw_text,
        'helperRawPreview': helper_raw_preview,
        'helperParsed': helper_payload if helper_schema_valid else None,
        'helperError': helper_error,
        'fallbackRan': False,
        'fallbackReason': None,
    }
    normalized = normalize_pre_extraction(helper_payload, fallback_actor_id=actor_id)
    roll_requirement = normalized.get('rollRequirement') if isinstance(normalized.get('rollRequirement'), dict) else None
    if helper_schema_valid and (
        normalized['declaredActions'] or (roll_requirement and roll_requirement.get('requiresRoll') is False)
    ):
        return _attach_debug(normalized, helper_debug)

    fallback = _heuristic_extract(player_message, actor_id=actor_id)
    normalized_fallback = normalize_pre_extraction(fallback, fallback_actor_id=actor_id)
    helper_debug['source'] = 'heuristic' if normalized_fallback['declaredActions'] else 'none'
    helper_debug['fallbackRan'] = True
    helper_debug['fallbackReason'] = fallback_reason
    return _attach_debug(normalized_fallback, helper_debug)
