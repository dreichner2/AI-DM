from __future__ import annotations

import json
import os
from typing import Any

from flask import current_app, has_app_context

from aidm_server.contracts import ProviderRequest
from aidm_server.game_state.extraction.schemas import extract_json_object
from aidm_server.llm_providers import get_helper_provider, helper_provider_name
from aidm_server.services.runtime_config import provider_configured
from aidm_server.telemetry import telemetry_event, telemetry_metric


BOSS_TACTICS_TASK = 'boss_tactics'
BOSS_TACTICS_PLANNER_TASK = 'boss_tactics_planner'
BOSS_TACTICS_SYSTEM_MESSAGE = (
    'You are a strict boss combat candidate selector for an AI tabletop RPG engine. '
    'The engine already generated legal boss candidate actions. '
    'Return JSON only with selected_candidate_id, backup_candidate_ids, reasoning_summary, and confidence. '
    'Do not output target IDs, ability IDs, movement, rolls, damage, effects, or resolver fields.'
)
BOSS_TACTICS_PLANNER_SYSTEM_MESSAGE = (
    'You are an advisory boss tactics planner for an AI tabletop RPG engine. '
    'Return JSON only with tactical goals and candidate-matching tags. '
    'Do not output target IDs, ability IDs, movement, rolls, damage, effects, or resolver fields.'
)
COMPLEX_INTELLIGENCE = {'trained', 'tactical', 'genius', 'alien'}
_SELECTOR_ALLOWED_KEYS = {'selected_candidate_id', 'backup_candidate_ids', 'reasoning_summary', 'confidence'}
_FORBIDDEN_EXECUTABLE_KEYS = {
    'target_id',
    'targetId',
    'ability_id',
    'abilityId',
    'movement',
    'movementGoal',
    'destination_id',
    'destinationId',
    'roll',
    'damage',
    'save_dc',
    'saveDc',
    'environment_id',
    'environmentId',
    'action_bundle',
    'actionBundle',
    'resolver',
    'intent',
    'intentType',
    'recommendedIntent',
}


def _config_value(name: str) -> str:
    if has_app_context():
        value = current_app.config.get(name)
        if value not in (None, ''):
            return str(value)
    return os.getenv(name, '')


def _helper_provider_name() -> str:
    return helper_provider_name(BOSS_TACTICS_TASK)


def _planner_provider_name() -> str:
    return helper_provider_name(BOSS_TACTICS_PLANNER_TASK)


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
    if provider in {'codex', 'codex_cli'}:
        return provider_configured(provider)
    return False


def boss_tactics_planner_enabled() -> bool:
    if has_app_context() and current_app.config.get('TESTING') and not current_app.config.get('AIDM_BOSS_TACTICS_PLANNER_IN_TESTS'):
        return False
    setting = str(_config_value('AIDM_BOSS_TACTICS_PLANNER_ENABLED') or 'auto').strip().lower()
    if setting in {'0', 'false', 'no', 'off', 'disabled'}:
        return False
    if setting in {'1', 'true', 'yes', 'on', 'enabled'}:
        return True
    provider = _planner_provider_name()
    if provider == 'fallback':
        return True
    if has_app_context():
        return provider_configured(provider)
    if provider == 'deepseek':
        return bool(os.getenv('AIDM_BOSS_TACTICS_PLANNER_HELPER_DEEPSEEK_API_KEY') or os.getenv('AIDM_BOSS_TACTICS_HELPER_DEEPSEEK_API_KEY') or os.getenv('AIDM_DEEPSEEK_API_KEY'))
    if provider in {'nvidia', 'kimi'}:
        return bool(os.getenv('AIDM_BOSS_TACTICS_PLANNER_HELPER_NVIDIA_API_KEY') or os.getenv('AIDM_BOSS_TACTICS_HELPER_NVIDIA_API_KEY') or os.getenv('AIDM_NVIDIA_API_KEY'))
    if provider == 'gemini':
        return bool(os.getenv('AIDM_GEMINI_API_KEY'))
    if provider in {'codex', 'codex_cli'}:
        return provider_configured(provider)
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
    return bool(
        behavior.get('primaryGoal') in {'complete_ritual', 'protect_location'}
        and behavior.get('intelligenceProfile') in COMPLEX_INTELLIGENCE
        and len(enemy.get('abilities') or []) >= 3
        and (battlefield.get('hazards') or battlefield.get('interactables'))
    )


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


def _selector_candidate_view(candidate: dict[str, Any]) -> dict[str, Any]:
    tags = candidate.get('tags') if isinstance(candidate.get('tags'), dict) else {}
    return {
        'candidate_id': candidate.get('candidateId'),
        'summary': candidate.get('llmSummary') or candidate.get('reason'),
        'intent_tags': tags.get('intent') or [],
        'targeting_tags': tags.get('targeting') or [],
        'ability_tags': tags.get('abilityProfile') or [],
        'positioning_tags': tags.get('positioning') or [],
        'objective_tags': tags.get('objective') or [],
        'dramatic_role': tags.get('dramaticRole') or [],
        'risk_posture': tags.get('riskPosture'),
        'deterministic_rank': candidate.get('deterministicRank'),
        'deterministic_score': candidate.get('deterministicScore'),
        'is_fallback_candidate': bool(candidate.get('isFallbackCandidate')),
    }


def _planner_candidate_view(candidate: dict[str, Any]) -> dict[str, Any]:
    view = _selector_candidate_view(candidate)
    view.pop('deterministic_rank', None)
    view.pop('deterministic_score', None)
    return view


def build_boss_planner_prompt(
    enemy: dict[str, Any],
    combat: dict[str, Any],
    *,
    candidates: list[dict[str, Any]],
) -> str:
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), dict) else {}
    hp = enemy.get('hp') if isinstance(enemy.get('hp'), dict) else {}
    behavior = enemy.get('behavior') if isinstance(enemy.get('behavior'), dict) else {}
    planner_input = {
        'boss': {
            'boss_id': enemy.get('id'),
            'name': enemy.get('name'),
            'hp': f"{hp.get('current')}/{hp.get('max')}",
            'goals': [behavior.get('primaryGoal'), *(behavior.get('secondaryGoals') or [])],
            'behavior': behavior,
        },
        'encounter_context': {
            'round': combat.get('round', 1),
            'party_state': _players_summary(combat),
            'battlefield': battlefield,
            'objective': combat.get('encounterGoal') or (combat.get('flags') or {}).get('objectiveStatus') if isinstance(combat.get('flags'), dict) else combat.get('encounterGoal'),
        },
        'candidate_tag_options': [_planner_candidate_view(candidate) for candidate in candidates[:10]],
        'schema': {
            'required': [
                'tactical_goal',
                'desired_intent_type',
                'preferred_tags',
                'risk_posture',
                'objective_priority',
                'reasoning_summary',
            ],
            'additionalProperties': False,
            'forbidden_executable_fields': sorted(_FORBIDDEN_EXECUTABLE_KEYS),
        },
    }
    return (
        'Recommend advisory boss tactics for candidate matching.\n'
        'Do not choose a candidate ID and do not write an executable action.\n'
        'Use preferred_tags to describe which existing candidate tags should be favored.\n'
        'Return JSON only using the schema exactly.\n\n'
        f"BOSS_ADVISORY_PLANNER_INPUT:\n{json.dumps(planner_input, sort_keys=True)}"
    )


def build_boss_tactics_prompt(
    enemy: dict[str, Any],
    combat: dict[str, Any],
    *,
    fallback_intent: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> str:
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), dict) else {}
    hp = enemy.get('hp') if isinstance(enemy.get('hp'), dict) else {}
    behavior = enemy.get('behavior') if isinstance(enemy.get('behavior'), dict) else {}
    selector_input = {
        'boss': {
            'boss_id': enemy.get('id'),
            'name': enemy.get('name'),
            'hp': f"{hp.get('current')}/{hp.get('max')}",
            'goals': [behavior.get('primaryGoal'), *(behavior.get('secondaryGoals') or [])],
            'behavior': behavior,
            'available_abilities': _available_abilities(enemy),
        },
        'encounter_context': {
            'round': combat.get('round', 1),
            'party_state': _players_summary(combat),
            'battlefield': battlefield,
            'objective': combat.get('encounterGoal') or (combat.get('flags') or {}).get('objectiveStatus') if isinstance(combat.get('flags'), dict) else combat.get('encounterGoal'),
        },
        'deterministic_baseline': {
            'fallback_candidate_id': fallback_intent.get('candidateId'),
            'top_candidate_id': fallback_intent.get('candidateId'),
            'top_candidate_summary': fallback_intent.get('llmSummary') or fallback_intent.get('reason'),
        },
        'legal_candidates': [_selector_candidate_view(candidate) for candidate in candidates[:10]],
        'schema': {
            'required': ['selected_candidate_id', 'backup_candidate_ids', 'reasoning_summary', 'confidence'],
            'additionalProperties': False,
            'forbidden_executable_fields': sorted(_FORBIDDEN_EXECUTABLE_KEYS),
        },
    }
    return (
        'Select exactly one already-legal boss candidate for this decision point.\n'
        'You are not writing a combat action. You may only choose candidate IDs from legal_candidates.\n'
        'Prefer objective, phase, minion, environment, and dramatic boss-fit candidates when they are legal and useful.\n'
        'If no non-fallback candidate clearly fits, choose fallback_candidate_id.\n'
        'Return JSON only using the schema exactly.\n\n'
        f"BOSS_CANDIDATE_SELECTION_INPUT:\n{json.dumps(selector_input, sort_keys=True)}"
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


def _string_list(value: Any, *, limit: int = 8, item_limit: int = 80) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or '').strip()
        if text:
            result.append(text[:item_limit])
        if len(result) >= limit:
            break
    return result


def _validated_planner_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if any(key in payload for key in _FORBIDDEN_EXECUTABLE_KEYS):
        return None
    allowed_keys = {
        'tactical_goal',
        'desired_intent_type',
        'target_preference',
        'ability_preference',
        'position_preference',
        'preferred_tags',
        'avoid',
        'risk_posture',
        'objective_priority',
        'reasoning_summary',
        'expires_after_turns',
    }
    if set(payload.keys()) - allowed_keys:
        return None
    preferred_tags = _string_list(payload.get('preferred_tags'), limit=10, item_limit=48)
    if not preferred_tags:
        return None
    try:
        expires_after_turns = max(1, min(3, int(payload.get('expires_after_turns') or 1)))
    except (TypeError, ValueError):
        expires_after_turns = 1
    return {
        'tacticalGoal': str(payload.get('tactical_goal') or '')[:220],
        'desiredIntentType': str(payload.get('desired_intent_type') or '')[:80],
        'targetPreference': str(payload.get('target_preference') or '')[:120],
        'abilityPreference': str(payload.get('ability_preference') or '')[:120],
        'positionPreference': str(payload.get('position_preference') or '')[:120],
        'preferredTags': preferred_tags,
        'avoid': _string_list(payload.get('avoid'), limit=5, item_limit=80),
        'riskPosture': str(payload.get('risk_posture') or '')[:40],
        'objectivePriority': str(payload.get('objective_priority') or '')[:80],
        'reasoningSummary': str(payload.get('reasoning_summary') or '')[:300],
        'expiresAfterTurns': expires_after_turns,
    }


def plan_boss_advisory(
    enemy: dict[str, Any],
    combat: dict[str, Any],
    settings: dict[str, Any],
    *,
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    if not settings.get('allowBossWarmPlanner', True):
        return None, 'disabled'
    if not boss_tactics_planner_enabled():
        return None, 'deterministic'
    try:
        response = get_helper_provider(task=BOSS_TACTICS_PLANNER_TASK).generate(
            ProviderRequest(
                prompt=build_boss_planner_prompt(enemy, combat, candidates=candidates),
                system_message=BOSS_TACTICS_PLANNER_SYSTEM_MESSAGE,
            )
        )
        payload = extract_json_object(response.text)
        planner = _validated_planner_payload(payload)
        if not planner:
            raise ValueError('boss tactics planner returned invalid advisory payload')
        planner['source'] = response.model
        telemetry_metric('combat.boss_tactics_planner.success_total', 1, tags={'model': response.model})
        telemetry_event(
            'combat.boss_tactics_planner.selected',
            payload={
                'enemyId': enemy.get('id'),
                'preferredTags': planner['preferredTags'],
                'desiredIntentType': planner['desiredIntentType'],
                'model': response.model,
            },
        )
        return planner, response.model
    except Exception as exc:
        telemetry_event('combat.boss_tactics_planner.failed', payload={'error': str(exc)[:300]}, severity='warning')
        return None, 'deterministic_fallback'


def _validated_candidate_selection(
    payload: dict[str, Any] | None,
    *,
    candidates: list[dict[str, Any]],
    fallback_candidate_id: str | None,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if any(key in payload for key in _FORBIDDEN_EXECUTABLE_KEYS):
        return None
    if set(payload.keys()) - _SELECTOR_ALLOWED_KEYS:
        return None
    selected_id = str(payload.get('selected_candidate_id') or '').strip()
    candidate_by_id = {
        str(candidate.get('candidateId')): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get('candidateId')
    }
    if selected_id not in candidate_by_id:
        return None
    selected = candidate_by_id[selected_id]
    if selected.get('legalAtGeneration') is False:
        return None
    dry_run = selected.get('dryRun') if isinstance(selected.get('dryRun'), dict) else {}
    if dry_run and dry_run.get('canResolveNow') is False:
        return None
    backup_ids = []
    raw_backup_ids = payload.get('backup_candidate_ids')
    if raw_backup_ids is None:
        raw_backup_ids = []
    if not isinstance(raw_backup_ids, list):
        return None
    for backup_id in raw_backup_ids[:3]:
        backup_id = str(backup_id or '').strip()
        if not backup_id:
            continue
        if backup_id not in candidate_by_id:
            return None
        backup = candidate_by_id[backup_id]
        if backup.get('legalAtGeneration') is False:
            return None
        backup_ids.append(backup_id)
    try:
        confidence = float(payload.get('confidence'))
    except (TypeError, ValueError):
        return None
    confidence = max(0.0, min(1.0, confidence))
    return {
        'selectedCandidateId': selected_id,
        'backupCandidateIds': backup_ids,
        'reasoningSummary': str(payload.get('reasoning_summary') or '')[:300],
        'confidence': confidence,
        'fallbackCandidateId': fallback_candidate_id,
        'selectedCandidate': selected,
    }


def plan_boss_tactic(enemy: dict[str, Any], combat: dict[str, Any], settings: dict[str, Any]) -> tuple[dict[str, Any], str]:
    return deterministic_boss_tactic(enemy, combat, settings), 'deterministic'


def plan_boss_candidate_tactic(
    enemy: dict[str, Any],
    combat: dict[str, Any],
    settings: dict[str, Any],
    *,
    fallback_intent: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    fallback = {
        **fallback_intent,
        'selectionMethod': fallback_intent.get('selectionMethod') or 'deterministic_boss_fallback',
        'tacticSource': fallback_intent.get('tacticSource') or 'deterministic',
    }
    if not boss_tactics_helper_enabled():
        return fallback, 'deterministic'
    try:
        response = get_helper_provider(task=BOSS_TACTICS_TASK).generate(
            ProviderRequest(
                prompt=build_boss_tactics_prompt(
                    enemy,
                    combat,
                    fallback_intent=fallback_intent,
                    candidates=candidates,
                ),
                system_message=BOSS_TACTICS_SYSTEM_MESSAGE,
            )
        )
        payload = extract_json_object(response.text)
        if not payload:
            raise ValueError('boss tactics helper returned invalid JSON')
        selection = _validated_candidate_selection(
            payload,
            candidates=candidates,
            fallback_candidate_id=str(fallback_intent.get('candidateId') or '').strip() or None,
        )
        if not selection:
            raise ValueError('boss tactics helper returned invalid candidate selection')
        selected_candidate = selection['selectedCandidate']
        selected_intent = selected_candidate.get('intent') if isinstance(selected_candidate.get('intent'), dict) else None
        if not selected_intent:
            raise ValueError('boss tactics helper selected candidate without executable intent')
        intent = {
            **selected_intent,
            'selectionScore': selected_candidate.get('score'),
            'selectionMethod': 'boss_tactics_candidate_selector',
            'tacticSource': response.model,
            'bossTacticsSelection': {
                'selectedCandidateId': selection['selectedCandidateId'],
                'backupCandidateIds': selection['backupCandidateIds'],
                'fallbackCandidateId': selection['fallbackCandidateId'],
                'reasoningSummary': selection['reasoningSummary'],
                'confidence': selection['confidence'],
                'changedDeterministicBaseline': selection['selectedCandidateId'] != selection['fallbackCandidateId'],
            },
            'reason': selection['reasoningSummary'] or selected_intent.get('reason') or fallback_intent.get('reason'),
            'confidence': selection['confidence'],
        }
        telemetry_metric('combat.boss_tactics.success_total', 1, tags={'model': response.model})
        telemetry_event(
            'combat.boss_tactics.selected',
            payload={
                'enemyId': enemy.get('id'),
                'selectedCandidateId': selection['selectedCandidateId'],
                'fallbackCandidateId': selection['fallbackCandidateId'],
                'changedDeterministicBaseline': selection['selectedCandidateId'] != selection['fallbackCandidateId'],
                'confidence': selection['confidence'],
                'model': response.model,
            },
        )
        return intent, response.model
    except Exception as exc:
        telemetry_event('combat.boss_tactics.failed', payload={'error': str(exc)[:300]}, severity='warning')
        return {**fallback, 'tacticSource': 'deterministic_fallback'}, 'deterministic_fallback'
