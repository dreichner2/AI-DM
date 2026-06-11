from __future__ import annotations

import os
from typing import Any

from flask import current_app, has_app_context

from aidm_server.canon_text import int_or_default
from aidm_server.contracts import ProviderRequest
from aidm_server.game_state.extraction.schemas import extract_json_object
from aidm_server.llm_providers import get_helper_provider
from aidm_server.services.runtime_config import provider_configured
from aidm_server.telemetry import telemetry_event, telemetry_metric


SENTIENT_ENEMY_BRAIN_TASK = 'sentient_enemy_brain'
SENTIENT_ENEMY_BRAIN_SYSTEM_MESSAGE = (
    'You are the tactical brain for one sentient tabletop RPG enemy. '
    'Return JSON only. Do not narrate. Do not mutate game state.'
)
NON_SENTIENT_INTELLIGENCE = {'mindless', 'animal'}
NON_SENTIENT_TYPES = {'beast', 'ooze', 'swarm', 'plant'}


def _config_value(name: str) -> str:
    if has_app_context():
        value = current_app.config.get(name)
        if value not in (None, ''):
            return str(value)
    return os.getenv(name, '')


def _helper_provider_name() -> str:
    return str(_config_value('AIDM_SENTIENT_ENEMY_BRAIN_HELPER_LLM_PROVIDER') or 'deepseek').strip().lower()


def sentient_enemy_brain_enabled() -> bool:
    if has_app_context() and current_app.config.get('TESTING') and not current_app.config.get('AIDM_SENTIENT_ENEMY_BRAIN_HELPER_IN_TESTS'):
        return False
    setting = str(_config_value('AIDM_SENTIENT_ENEMY_BRAIN_HELPER_ENABLED') or 'auto').strip().lower()
    if setting in {'0', 'false', 'no', 'off', 'disabled'}:
        return False
    if setting in {'1', 'true', 'yes', 'on', 'enabled'}:
        return True
    provider = _helper_provider_name()
    if provider == 'fallback':
        return True
    if has_app_context():
        return provider_configured(provider)
    if provider == 'deepseek':
        return bool(os.getenv('AIDM_SENTIENT_ENEMY_BRAIN_DEEPSEEK_API_KEY') or os.getenv('AIDM_DEEPSEEK_API_KEY') or os.getenv('DEEPSEEK_API_KEY'))
    if provider in {'nvidia', 'kimi'}:
        return bool(os.getenv('AIDM_SENTIENT_ENEMY_BRAIN_NVIDIA_API_KEY') or os.getenv('AIDM_NVIDIA_API_KEY') or os.getenv('NVIDIA_API_KEY'))
    if provider == 'gemini':
        return bool(os.getenv('GOOGLE_GENAI_API_KEY'))
    return False


def _behavior(enemy: dict[str, Any]) -> dict[str, Any]:
    return enemy.get('behavior') if isinstance(enemy.get('behavior'), dict) else {}


def is_sentient_enemy(enemy: dict[str, Any]) -> bool:
    behavior = _behavior(enemy)
    intelligence = str(behavior.get('intelligenceProfile') or '').strip().lower()
    creature_type = str(enemy.get('creatureType') or enemy.get('creature_type') or '').strip().lower()
    if intelligence in NON_SENTIENT_INTELLIGENCE:
        return False
    if creature_type in NON_SENTIENT_TYPES and intelligence not in {'low_cunning', 'average', 'trained', 'tactical', 'genius', 'alien'}:
        return False
    if enemy.get('kind') == 'boss' or enemy.get('challengeTier') == 'boss':
        return True
    if creature_type in {'humanoid', 'fey', 'fiend', 'celestial', 'dragon', 'giant', 'aberration', 'monstrosity', 'custom'}:
        return True
    return intelligence in {'low_cunning', 'average', 'trained', 'tactical', 'genius', 'alien'}


def should_use_sentient_enemy_brain(enemy: dict[str, Any], settings: dict[str, Any]) -> bool:
    if not settings.get('allowSentientEnemyBrain', True):
        return False
    return is_sentient_enemy(enemy)


def _hp_summary(participant: dict[str, Any]) -> str:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    return f"{hp.get('current')}/{hp.get('max')}"


def _position_summary(participant: dict[str, Any]) -> str:
    position = participant.get('position') if isinstance(participant.get('position'), dict) else {}
    zone = position.get('zoneId') or 'unknown_zone'
    return f"{position.get('rangeBand') or 'near'} in {zone}"


def _players_summary(combat: dict[str, Any], allowed_target_ids: set[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for participant in combat.get('participants') or []:
        if not isinstance(participant, dict) or participant.get('team') != 'player':
            continue
        result.append(
            {
                'id': participant.get('id'),
                'name': participant.get('name'),
                'hp': _hp_summary(participant),
                'position': participant.get('position') if isinstance(participant.get('position'), dict) else {},
                'targetableNow': str(participant.get('id') or '') in allowed_target_ids,
                'conditions': participant.get('conditions') if isinstance(participant.get('conditions'), list) else [],
            }
        )
    return result[:8]


def _available_abilities(enemy: dict[str, Any]) -> list[dict[str, Any]]:
    abilities = []
    for ability in enemy.get('abilities') or []:
        if not isinstance(ability, dict):
            continue
        abilities.append(
            {
                'id': ability.get('id'),
                'name': ability.get('name'),
                'type': ability.get('type'),
                'range': ability.get('range'),
                'targetType': ability.get('targetType'),
                'cooldown': ability.get('cooldown'),
                'description': ability.get('description'),
            }
        )
    return abilities[:10]


def build_sentient_enemy_brain_prompt(
    enemy: dict[str, Any],
    combat: dict[str, Any],
    settings: dict[str, Any],
    *,
    allowed_target_ids: set[str],
    fallback_intent: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> str:
    behavior = _behavior(enemy)
    hp = enemy.get('hp') if isinstance(enemy.get('hp'), dict) else {}
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), dict) else {}
    return (
        'Return one JSON object with this shape:\n'
        '{"enemy_id": "...", "goal": "...", "current_emotion": "...", "morale": 0, '
        '"target": {"id": "...", "reason": "..."}, '
        '"movement_intent": {"goal": "...", "zoneId": "...", "rangeBand": "..."}, '
        '"action_intent": {"intentType": "...", "abilityId": "...", "requiresRoll": true, "preferredRoll": "..."}, '
        '"reasoning_summary": "...", "requires_roll": true, "preferred_roll": "...", "fallback_if_blocked": "..."}\n'
        'Use only allowed target ids and available ability ids. If no target is reachable, choose movement/positioning instead of inventing reach. '
        'Prefer surrender, retreat, negotiation, or repositioning when morale/self-preservation makes that more alive than fighting to the death.\n\n'
        f"Enemy: {enemy.get('name')} ({enemy.get('id')})\n"
        f"Enemy type: {enemy.get('creatureType') or enemy.get('kind')}\n"
        f"Enemy HP: {hp.get('current')}/{hp.get('max')}; morale {enemy.get('morale')}\n"
        f"Enemy position: {_position_summary(enemy)}\n"
        f"Enemy behavior: {behavior}\n"
        f"Available abilities: {_available_abilities(enemy)}\n"
        f"Allowed target ids now: {sorted(allowed_target_ids)}\n"
        f"Party state: {_players_summary(combat, allowed_target_ids)}\n"
        f"Battlefield: {battlefield}\n"
        f"Combat settings: {settings}\n"
        f"Deterministic fallback intent: {fallback_intent}\n"
        f"Deterministic candidates: {candidates[:6]}\n"
    )


def _validated_brain_payload(
    enemy: dict[str, Any],
    payload: dict[str, Any] | None,
    *,
    allowed_target_ids: set[str],
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    action = payload.get('action_intent') if isinstance(payload.get('action_intent'), dict) else payload
    intent_type = str(action.get('intentType') or action.get('intent_type') or action.get('type') or '').strip()
    if not intent_type:
        return None
    ability_ids = {
        str(ability.get('id'))
        for ability in enemy.get('abilities') or []
        if isinstance(ability, dict) and ability.get('id')
    }
    ability_id = str(action.get('abilityId') or action.get('ability_id') or '').strip()
    if ability_id and ability_id not in ability_ids:
        ability_id = ''
    target = payload.get('target') if isinstance(payload.get('target'), dict) else {}
    target_id = str(target.get('id') or action.get('targetId') or action.get('target_id') or '').strip()
    if target_id and target_id not in allowed_target_ids:
        target_id = ''
    morale = int_or_default(payload.get('morale'), default=int_or_default(enemy.get('morale'), default=50))
    return {
        'intentType': intent_type,
        'abilityId': ability_id or None,
        'targetId': target_id or None,
        'movementGoal': (
            (payload.get('movement_intent') or {}).get('goal')
            if isinstance(payload.get('movement_intent'), dict)
            else action.get('movementGoal') or action.get('movement_goal')
        ),
        'reason': str(payload.get('reasoning_summary') or action.get('reason') or 'Sentient enemy brain selected this tactic.'),
        'confidence': max(0.0, min(1.0, float(action.get('confidence') or payload.get('confidence') or 0.78))),
        'visibleTelegraph': action.get('visibleTelegraph') or action.get('visible_telegraph'),
        'suggestedSpeech': action.get('suggestedSpeech') or action.get('suggested_speech'),
        'requiredRolls': [payload.get('preferred_roll') or action.get('preferredRoll') or action.get('preferred_roll')] if (payload.get('requires_roll') or action.get('requiresRoll')) else [],
        'brain': {
            'enemy_id': payload.get('enemy_id') or enemy.get('id'),
            'goal': payload.get('goal'),
            'current_emotion': payload.get('current_emotion'),
            'morale': max(0, min(100, morale)),
            'target': {'id': target_id or None, 'reason': target.get('reason')},
            'movement_intent': payload.get('movement_intent') if isinstance(payload.get('movement_intent'), dict) else {},
            'action_intent': action,
            'reasoning_summary': payload.get('reasoning_summary'),
            'requires_roll': bool(payload.get('requires_roll') or action.get('requiresRoll')),
            'preferred_roll': payload.get('preferred_roll') or action.get('preferredRoll') or action.get('preferred_roll'),
            'fallback_if_blocked': payload.get('fallback_if_blocked'),
        },
    }


def plan_sentient_enemy_intent(
    enemy: dict[str, Any],
    combat: dict[str, Any],
    settings: dict[str, Any],
    *,
    allowed_target_ids: set[str],
    fallback_intent: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    fallback = {
        **fallback_intent,
        'selectionMethod': fallback_intent.get('selectionMethod') or 'deterministic_sentient_fallback',
        'brainSource': 'deterministic_fallback',
    }
    if not sentient_enemy_brain_enabled():
        return fallback, 'deterministic_fallback'
    try:
        response = get_helper_provider(task=SENTIENT_ENEMY_BRAIN_TASK).generate(
            ProviderRequest(
                prompt=build_sentient_enemy_brain_prompt(
                    enemy,
                    combat,
                    settings,
                    allowed_target_ids=allowed_target_ids,
                    fallback_intent=fallback_intent,
                    candidates=candidates,
                ),
                system_message=SENTIENT_ENEMY_BRAIN_SYSTEM_MESSAGE,
            )
        )
        payload = extract_json_object(response.text)
        intent = _validated_brain_payload(enemy, payload, allowed_target_ids=allowed_target_ids)
        if not intent:
            raise ValueError('sentient enemy brain returned invalid intent')
        intent = {
            **fallback_intent,
            **{key: value for key, value in intent.items() if value not in (None, [], {})},
            'enemyId': enemy.get('id'),
            'selectionMethod': 'sentient_enemy_brain',
            'brainSource': response.model,
        }
        telemetry_metric('combat.sentient_enemy_brain.success_total', 1, tags={'model': response.model})
        return intent, response.model
    except Exception as exc:
        telemetry_event('combat.sentient_enemy_brain.failed', payload={'error': str(exc)[:300]}, severity='warning')
        return fallback, 'deterministic_fallback'
