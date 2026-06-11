from __future__ import annotations

import os
from typing import Any

from flask import current_app, has_app_context

from aidm_server.contracts import ProviderRequest
from aidm_server.game_state.extraction.schemas import extract_json_object
from aidm_server.llm_providers import get_helper_provider
from aidm_server.services.runtime_config import provider_configured
from aidm_server.telemetry import telemetry_event, telemetry_metric


BOSS_TACTICS_TASK = 'boss_tactics'
BOSS_TACTICS_SYSTEM_MESSAGE = (
    'You recommend one structured EnemyIntent for a complex boss in an AI tabletop RPG. '
    'Return JSON only. Do not narrate.'
)
COMPLEX_INTELLIGENCE = {'trained', 'tactical', 'genius', 'alien'}


def _config_value(name: str) -> str:
    if has_app_context():
        value = current_app.config.get(name)
        if value not in (None, ''):
            return str(value)
    return os.getenv(name, '')


def _helper_provider_name() -> str:
    return str(_config_value('AIDM_BOSS_TACTICS_HELPER_LLM_PROVIDER') or _config_value('AIDM_HELPER_LLM_PROVIDER') or 'deepseek').strip().lower()


def boss_tactics_helper_enabled() -> bool:
    if has_app_context() and current_app.config.get('TESTING') and not current_app.config.get('AIDM_BOSS_TACTICS_HELPER_IN_TESTS'):
        return False
    setting = str(_config_value('AIDM_BOSS_TACTICS_HELPER_ENABLED') or 'auto').strip().lower()
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
        return bool(os.getenv('AIDM_BOSS_TACTICS_HELPER_DEEPSEEK_API_KEY') or os.getenv('AIDM_HELPER_DEEPSEEK_API_KEY') or os.getenv('AIDM_DEEPSEEK_API_KEY'))
    if provider in {'nvidia', 'kimi'}:
        return bool(os.getenv('AIDM_BOSS_TACTICS_HELPER_NVIDIA_API_KEY') or os.getenv('AIDM_HELPER_NVIDIA_API_KEY') or os.getenv('AIDM_NVIDIA_API_KEY'))
    if provider == 'gemini':
        return bool(os.getenv('AIDM_GEMINI_API_KEY'))
    return False


def should_use_boss_tactics_helper(enemy: dict[str, Any], combat: dict[str, Any], settings: dict[str, Any]) -> bool:
    if not settings.get('allowBossTacticsHelper', True):
        return False
    behavior = enemy.get('behavior') if isinstance(enemy.get('behavior'), dict) else {}
    if enemy.get('kind') == 'boss' or behavior.get('combatRole') == 'boss' or enemy.get('challengeTier') == 'boss':
        return True
    if behavior.get('intelligenceProfile') in COMPLEX_INTELLIGENCE and len(enemy.get('abilities') or []) >= 3:
        return True
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), dict) else {}
    return bool(behavior.get('primaryGoal') in {'complete_ritual', 'protect_location'} and (battlefield.get('hazards') or battlefield.get('interactables')))


def _players_summary(combat: dict[str, Any]) -> list[str]:
    result = []
    for participant in combat.get('participants') or []:
        if not isinstance(participant, dict) or participant.get('team') != 'player':
            continue
        hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
        result.append(f"{participant.get('name')}: {hp.get('current')}/{hp.get('max')} HP, {participant.get('position', {}).get('rangeBand', 'near')}")
    return result[:8]


def _available_abilities(enemy: dict[str, Any]) -> list[str]:
    return [
        str(ability.get('id') or ability.get('name'))
        for ability in enemy.get('abilities') or []
        if isinstance(ability, dict) and (ability.get('id') or ability.get('name'))
    ][:10]


def build_boss_tactics_prompt(enemy: dict[str, Any], combat: dict[str, Any]) -> str:
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), dict) else {}
    hp = enemy.get('hp') if isinstance(enemy.get('hp'), dict) else {}
    behavior = enemy.get('behavior') if isinstance(enemy.get('behavior'), dict) else {}
    return (
        'Return one JSON object with recommendedIntent. Use only available ability ids.\n'
        'recommendedIntent fields: intentType, abilityId, targetId, movementGoal, reason, confidence, visibleTelegraph, suggestedSpeech.\n'
        f"Boss: {enemy.get('name')}\n"
        f"Boss HP: {hp.get('current')}/{hp.get('max')}\n"
        f"Boss goals: {[behavior.get('primaryGoal'), *(behavior.get('secondaryGoals') or [])]}\n"
        f"Boss behavior: {behavior}\n"
        f"Available abilities: {_available_abilities(enemy)}\n"
        f"Party state: {_players_summary(combat)}\n"
        f"Battlefield: {battlefield}\n"
    )


def _first_special_ability(enemy: dict[str, Any]) -> dict[str, Any] | None:
    abilities = [ability for ability in enemy.get('abilities') or [] if isinstance(ability, dict)]
    for ability in abilities:
        if ability.get('type') in {'legendary', 'lair', 'spell', 'special'}:
            return ability
    return abilities[0] if abilities else None


def deterministic_boss_tactic(enemy: dict[str, Any], combat: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    ability = _first_special_ability(enemy)
    players = [participant for participant in combat.get('participants') or [] if isinstance(participant, dict) and participant.get('team') == 'player']
    wounded = sorted(
        players,
        key=lambda participant: (
            ((participant.get('hp') or {}).get('current') or 999) / max(1, ((participant.get('hp') or {}).get('max') or 1)),
            participant.get('name') or '',
        ),
    )
    target = wounded[0] if wounded else None
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), dict) else {}
    if settings.get('allowEnvironmentalHazards') and battlefield.get('hazards'):
        return {
            'intentType': 'use_environment',
            'targetId': target.get('id') if target else None,
            'movementGoal': 'force a player toward the strongest battlefield hazard',
            'reason': f"{enemy.get('name')} uses the battlefield instead of trading simple attacks.",
            'confidence': 0.82,
            'visibleTelegraph': f"{enemy.get('name')} shifts attention toward the dangerous terrain.",
            'suggestedSpeech': 'The battlefield itself answers to me.',
        }
    if ability and ability.get('id'):
        return {
            'intentType': 'use_ability',
            'abilityId': ability.get('id'),
            'targetId': target.get('id') if target else None,
            'reason': f"{enemy.get('name')} uses {ability.get('name') or ability.get('id')} to press a meaningful advantage.",
            'confidence': 0.8,
            'visibleTelegraph': f"{enemy.get('name')} prepares {ability.get('name') or 'a decisive technique'}.",
            'suggestedSpeech': None,
        }
    return {
        'intentType': 'attack',
        'targetId': target.get('id') if target else None,
        'reason': f"{enemy.get('name')} keeps pressure on the most vulnerable target.",
        'confidence': 0.68,
        'visibleTelegraph': f"{enemy.get('name')} studies the weakest opening.",
    }


def _validated_helper_intent(enemy: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    intent = payload.get('recommendedIntent') if isinstance(payload.get('recommendedIntent'), dict) else payload
    if not isinstance(intent, dict):
        return None
    ability_ids = {str(ability.get('id')) for ability in enemy.get('abilities') or [] if isinstance(ability, dict) and ability.get('id')}
    ability_id = str(intent.get('abilityId') or '').strip()
    if ability_id and ability_id not in ability_ids:
        intent.pop('abilityId', None)
    intent_type = str(intent.get('intentType') or '').strip()
    if not intent_type:
        return None
    return {
        'intentType': intent_type,
        'abilityId': intent.get('abilityId'),
        'targetId': intent.get('targetId'),
        'movementGoal': intent.get('movementGoal'),
        'reason': str(intent.get('reason') or 'Boss tactics helper selected this plan.'),
        'confidence': float(intent.get('confidence') or 0.75),
        'visibleTelegraph': intent.get('visibleTelegraph'),
        'suggestedSpeech': intent.get('suggestedSpeech'),
    }


def plan_boss_tactic(enemy: dict[str, Any], combat: dict[str, Any], settings: dict[str, Any]) -> tuple[dict[str, Any], str]:
    fallback = deterministic_boss_tactic(enemy, combat, settings)
    if not boss_tactics_helper_enabled():
        return fallback, 'deterministic'
    try:
        response = get_helper_provider(task=BOSS_TACTICS_TASK).generate(
            ProviderRequest(
                prompt=build_boss_tactics_prompt(enemy, combat),
                system_message=BOSS_TACTICS_SYSTEM_MESSAGE,
            )
        )
        payload = extract_json_object(response.text)
        if not payload:
            raise ValueError('boss tactics helper returned invalid JSON')
        intent = _validated_helper_intent(enemy, payload)
        if not intent:
            raise ValueError('boss tactics helper returned invalid intent')
        telemetry_metric('combat.boss_tactics.success_total', 1, tags={'model': response.model})
        return intent, response.model
    except Exception as exc:
        telemetry_event('combat.boss_tactics.failed', payload={'error': str(exc)[:300]}, severity='warning')
        return fallback, 'deterministic_fallback'
