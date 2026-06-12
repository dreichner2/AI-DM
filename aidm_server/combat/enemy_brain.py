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


SENTIENT_ENEMY_BRAIN_TASK = 'sentient_enemy_brain'
SENTIENT_ENEMY_BRAIN_SYSTEM_MESSAGE = (
    'You are a strict combat candidate selector for one sentient tabletop RPG enemy. '
    'The combat engine already generated legal candidate actions. '
    'Return JSON only with selected_candidate_id, backup_candidate_ids, reasoning_summary, and confidence. '
    'Do not output target IDs, ability IDs, movement, rolls, damage, effects, or resolver fields.'
)
NON_SENTIENT_INTELLIGENCE = {'mindless', 'animal'}
NON_SENTIENT_TYPES = {'beast', 'ooze', 'swarm', 'plant'}
INTELLIGENT_INTELLIGENCE = {'low_cunning', 'average', 'trained', 'tactical', 'genius', 'alien'}
HUMANLIKE_CREATURE_TYPES = {'humanoid', 'fey', 'fiend', 'celestial', 'dragon', 'giant', 'aberration', 'monstrosity', 'custom'}
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
    'action_intent',
}


def _config_value(name: str) -> str:
    if has_app_context():
        value = current_app.config.get(name)
        if value not in (None, ''):
            return str(value)
    return os.getenv(name, '')


def _helper_provider_name() -> str:
    return helper_provider_name(SENTIENT_ENEMY_BRAIN_TASK)


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
    if provider in {'codex', 'codex_cli'}:
        return provider_configured(provider)
    return False


def _behavior(enemy: dict[str, Any]) -> dict[str, Any]:
    return enemy.get('behavior') if isinstance(enemy.get('behavior'), dict) else {}


def is_sentient_enemy(enemy: dict[str, Any]) -> bool:
    behavior = _behavior(enemy)
    intelligence = str(behavior.get('intelligenceProfile') or '').strip().lower()
    creature_type = str(enemy.get('creatureType') or enemy.get('creature_type') or '').strip().lower()
    if intelligence in NON_SENTIENT_INTELLIGENCE:
        return False
    if creature_type in NON_SENTIENT_TYPES and intelligence not in INTELLIGENT_INTELLIGENCE:
        return False
    if enemy.get('kind') == 'boss' or enemy.get('challengeTier') == 'boss' or behavior.get('combatRole') == 'boss':
        return True
    if creature_type in HUMANLIKE_CREATURE_TYPES:
        return True
    return intelligence in INTELLIGENT_INTELLIGENCE


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


def _selector_candidate_view(candidate: dict[str, Any]) -> dict[str, Any]:
    tags = candidate.get('tags') if isinstance(candidate.get('tags'), dict) else {}
    return {
        'candidate_id': candidate.get('candidateId'),
        'summary': candidate.get('llmSummary') or candidate.get('reason'),
        'intent_tags': tags.get('intent') or [],
        'targeting_tags': tags.get('targeting') or [],
        'ability_tags': tags.get('abilityProfile') or [],
        'positioning_tags': tags.get('positioning') or [],
        'risk_posture': tags.get('riskPosture'),
        'objective_tags': tags.get('objective') or [],
        'deterministic_rank': candidate.get('deterministicRank'),
        'deterministic_score': candidate.get('deterministicScore'),
        'is_fallback_candidate': bool(candidate.get('isFallbackCandidate')),
    }


def _recent_behavior_summary(enemy: dict[str, Any]) -> list[str]:
    memory = enemy.get('memory') if isinstance(enemy.get('memory'), dict) else {}
    recent = []
    for item in memory.get('recentIntents') or memory.get('recent_intents') or []:
        if isinstance(item, str) and item.strip():
            recent.append(item.strip()[:160])
        elif isinstance(item, dict):
            intent_type = item.get('intentType') or item.get('intent_type') or item.get('type')
            target = item.get('targetId') or item.get('target_id')
            ability = item.get('abilityId') or item.get('ability_id')
            pieces = [str(value) for value in (intent_type, target, ability) if value]
            if pieces:
                recent.append(' / '.join(pieces)[:160])
    return recent[:4]


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
    selector_input = {
        'actor': {
            'actor_id': enemy.get('id'),
            'name': enemy.get('name'),
            'type': enemy.get('creatureType') or enemy.get('kind'),
            'hp': f"{hp.get('current')}/{hp.get('max')}",
            'morale': enemy.get('morale'),
            'position': _position_summary(enemy),
            'behavior': behavior,
        },
        'battle_context': {
            'round': combat.get('round', 1),
            'allowed_target_ids': sorted(allowed_target_ids),
            'party_state': _players_summary(combat, allowed_target_ids),
            'battlefield': battlefield,
            'settings': settings,
        },
        'deterministic_baseline': {
            'fallback_candidate_id': fallback_intent.get('candidateId'),
            'top_candidate_id': fallback_intent.get('candidateId'),
            'top_candidate_summary': fallback_intent.get('llmSummary') or fallback_intent.get('reason'),
        },
        'recent_behavior': _recent_behavior_summary(enemy),
        'anti_repetition_hint': 'Avoid repeating the same intent unless it is clearly the best legal candidate.',
        'legal_candidates': [_selector_candidate_view(candidate) for candidate in candidates[:8]],
        'schema': {
            'required': ['selected_candidate_id', 'backup_candidate_ids', 'reasoning_summary', 'confidence'],
            'additionalProperties': False,
            'forbidden_executable_fields': sorted(_FORBIDDEN_EXECUTABLE_KEYS),
        },
    }
    return (
        'Select exactly one already-legal candidate for this enemy turn.\n'
        'You are not writing a combat action. You may only choose candidate IDs from legal_candidates.\n'
        'If no non-fallback candidate clearly fits, choose fallback_candidate_id.\n'
        'Return JSON only using the schema exactly.\n\n'
        f"LEGAL_CANDIDATE_SELECTION_INPUT:\n{json.dumps(selector_input, sort_keys=True)}"
    )


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
        'reasoningSummary': str(payload.get('reasoning_summary') or '')[:240],
        'confidence': confidence,
        'fallbackCandidateId': fallback_candidate_id,
        'selectedCandidate': selected,
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
    fallback_candidate_id = str(fallback_intent.get('candidateId') or '').strip() or None
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
        selection = _validated_candidate_selection(
            payload,
            candidates=candidates,
            fallback_candidate_id=fallback_candidate_id,
        )
        if not selection:
            raise ValueError('sentient enemy brain returned invalid candidate selection')
        selected_candidate = selection['selectedCandidate']
        selected_intent = selected_candidate.get('intent') if isinstance(selected_candidate.get('intent'), dict) else None
        if not selected_intent:
            raise ValueError('sentient enemy brain selected candidate without an executable intent')
        intent = {
            **selected_intent,
            'selectionScore': selected_candidate.get('score'),
            'selectionMethod': 'sentient_enemy_brain_candidate_selector',
            'brainSource': response.model,
            'candidateSelection': {
                'selectedCandidateId': selection['selectedCandidateId'],
                'backupCandidateIds': selection['backupCandidateIds'],
                'fallbackCandidateId': selection['fallbackCandidateId'],
                'reasoningSummary': selection['reasoningSummary'],
                'confidence': selection['confidence'],
                'changedDeterministicBaseline': selection['selectedCandidateId'] != fallback_candidate_id,
            },
            'reason': selection['reasoningSummary'] or selected_intent.get('reason') or fallback_intent.get('reason'),
            'confidence': selection['confidence'],
        }
        telemetry_metric('combat.sentient_enemy_brain.success_total', 1, tags={'model': response.model})
        telemetry_event(
            'combat.sentient_enemy_brain.selected',
            payload={
                'enemyId': enemy.get('id'),
                'selectedCandidateId': selection['selectedCandidateId'],
                'fallbackCandidateId': selection['fallbackCandidateId'],
                'changedDeterministicBaseline': selection['selectedCandidateId'] != fallback_candidate_id,
                'confidence': selection['confidence'],
                'model': response.model,
            },
        )
        return intent, response.model
    except Exception as exc:
        telemetry_event('combat.sentient_enemy_brain.failed', payload={'error': str(exc)[:300]}, severity='warning')
        return fallback, 'deterministic_fallback'
