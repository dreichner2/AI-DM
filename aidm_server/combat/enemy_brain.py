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
ENEMY_TACTICS_PLANNER_TASK = 'enemy_tactics_planner'
ENEMY_TACTICS_COMPILER_TASK = 'enemy_tactics_compiler'
SENTIENT_ENEMY_BRAIN_SYSTEM_MESSAGE = (
    'You are a strict combat candidate selector for one sentient tabletop RPG enemy. '
    'The combat engine already generated legal candidate actions. '
    'Return JSON only with selected_candidate_id, backup_candidate_ids, reasoning_summary, and confidence. '
    'Do not output target IDs, ability IDs, movement, rolls, damage, effects, or resolver fields.'
)
ENEMY_TACTICS_PLANNER_SYSTEM_MESSAGE = (
    'You are a tactical advisor for one intelligent tabletop RPG enemy. '
    'Study the raw combat, scene, enemy, and visible player information and recommend the best next tactic. '
    'You may suggest attacks, movement, cover, terrain use, bargaining, surrender, retreat, warnings, or special abilities. '
    'Do not write executable game-state changes, rolls, damage, or final JSON for the engine.'
)
ENEMY_TACTICS_COMPILER_SYSTEM_MESSAGE = (
    'You convert a tactical recommendation into one strict engine intent JSON object. '
    'Use only known target IDs, known ability IDs, and the allowed intent types from the prompt. '
    'Do not output damage, rolls, DCs, HP changes, conditions, state patches, resolver fields, or prose outside JSON.'
)
NON_SENTIENT_INTELLIGENCE = {'mindless', 'animal'}
NON_SENTIENT_TYPES = {'beast', 'ooze', 'swarm', 'plant'}
INTELLIGENT_INTELLIGENCE = {'low_cunning', 'average', 'trained', 'tactical', 'genius', 'alien'}
HUMANLIKE_CREATURE_TYPES = {'humanoid', 'fey', 'fiend', 'celestial', 'dragon', 'giant', 'aberration', 'monstrosity', 'custom'}
_SELECTOR_ALLOWED_KEYS = {'selected_candidate_id', 'backup_candidate_ids', 'reasoning_summary', 'confidence'}
_COMPILED_INTENT_ALLOWED_KEYS = {
    'intent_type',
    'target_id',
    'ability_id',
    'movement_goal',
    'reason',
    'confidence',
    'visible_telegraph',
    'suggested_speech',
}
_COMPILED_INTENT_TYPES = {
    'attack',
    'retreat',
    'flee',
    'surrender',
    'negotiate',
    'call_reinforcements',
    'use_environment',
    'protect_ally',
    'complete_objective',
    'delay',
    'hide',
    'defend',
    'use_ability',
    'reposition',
    'wait',
}
_HOSTILE_TARGET_INTENTS = {'attack', 'use_ability', 'use_environment', 'delay'}
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


def _participant_tactics_view(participant: dict[str, Any]) -> dict[str, Any]:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    return {
        'id': participant.get('id'),
        'name': participant.get('name'),
        'team': participant.get('team'),
        'kind': participant.get('kind'),
        'level': participant.get('level'),
        'hp': f"{hp.get('current')}/{hp.get('max')}",
        'armor_class': participant.get('armorClass') or participant.get('armor_class'),
        'stats': participant.get('stats') if isinstance(participant.get('stats'), dict) else {},
        'position': participant.get('position') if isinstance(participant.get('position'), dict) else {},
        'conditions': participant.get('conditions') if isinstance(participant.get('conditions'), list) else [],
        'class': participant.get('class') or participant.get('class_'),
        'role': participant.get('role'),
        'visible_equipment': participant.get('equipment') or participant.get('visibleEquipment') or participant.get('visible_equipment') or [],
    }


def _scene_tactics_view(combat: dict[str, Any]) -> dict[str, Any]:
    scene = {}
    for key in ('currentScene', 'current_scene', 'scene', 'sceneContext', 'scene_context'):
        value = combat.get(key)
        if isinstance(value, dict):
            scene[key] = value
    for key in ('sceneDescription', 'scene_description', 'recentNarration', 'recent_narration', 'locationDescription', 'location_description'):
        value = combat.get(key)
        if isinstance(value, str) and value.strip():
            scene[key] = value.strip()[:1200]
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    for key in ('lastPlayerAction', 'last_player_action', 'lastDmOutput', 'last_dm_output'):
        value = flags.get(key)
        if isinstance(value, str) and value.strip():
            scene[key] = value.strip()[:800]
    return scene


def _ability_tactics_view(enemy: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for ability in enemy.get('abilities') or []:
        if not isinstance(ability, dict):
            continue
        result.append(
            {
                'id': ability.get('id'),
                'name': ability.get('name'),
                'type': ability.get('type'),
                'action_cost': ability.get('actionCost') or ability.get('action_cost'),
                'range': ability.get('range'),
                'target_type': ability.get('targetType') or ability.get('target_type'),
                'cooldown': ability.get('cooldown'),
                'damage': ability.get('damage') if isinstance(ability.get('damage'), dict) else None,
                'description': ability.get('description'),
                'ai_use_when': ability.get('aiUseWhen') or ability.get('ai_use_when') or [],
            }
        )
    return result[:12]


def _tactics_context_payload(
    enemy: dict[str, Any],
    combat: dict[str, Any],
    settings: dict[str, Any],
    *,
    allowed_target_ids: set[str],
    fallback_intent: dict[str, Any],
    candidates: list[dict[str, Any]],
    facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    behavior = _behavior(enemy)
    hp = enemy.get('hp') if isinstance(enemy.get('hp'), dict) else {}
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), dict) else {}
    participants = [item for item in combat.get('participants') or [] if isinstance(item, dict)]
    return {
        'actor': {
            'actor_id': enemy.get('id'),
            'name': enemy.get('name'),
            'type': enemy.get('creatureType') or enemy.get('kind'),
            'hp': f"{hp.get('current')}/{hp.get('max')}",
            'morale': enemy.get('morale'),
            'position': enemy.get('position') if isinstance(enemy.get('position'), dict) else {},
            'behavior': behavior,
            'abilities': _ability_tactics_view(enemy),
            'recent_behavior': _recent_behavior_summary(enemy),
        },
        'visible_participants': [_participant_tactics_view(participant) for participant in participants[:12]],
        'visible_player_ids': sorted(allowed_target_ids),
        'battle_context': {
            'round': combat.get('round', 1),
            'battlefield': battlefield,
            'scene': _scene_tactics_view(combat),
            'facts': facts or {},
            'settings': settings,
        },
        'deterministic_baseline': {
            'intent_type': fallback_intent.get('intentType'),
            'target_id': fallback_intent.get('targetId'),
            'ability_id': fallback_intent.get('abilityId'),
            'summary': fallback_intent.get('llmSummary') or fallback_intent.get('reason'),
            'candidate_id': fallback_intent.get('candidateId'),
        },
        'engine_candidates_for_reference_only': [_selector_candidate_view(candidate) for candidate in candidates[:8]],
    }


def build_enemy_tactics_planner_prompt(
    enemy: dict[str, Any],
    combat: dict[str, Any],
    settings: dict[str, Any],
    *,
    allowed_target_ids: set[str],
    fallback_intent: dict[str, Any],
    candidates: list[dict[str, Any]],
    facts: dict[str, Any] | None = None,
) -> str:
    planner_input = _tactics_context_payload(
        enemy,
        combat,
        settings,
        allowed_target_ids=allowed_target_ids,
        fallback_intent=fallback_intent,
        candidates=candidates,
        facts=facts,
    )
    return (
        'Recommend the best next tactic for this enemy. You are not limited to the engine candidates.\n'
        'Use the enemy behavior, visible player state, battlefield, inferred terrain, morale, and recent actions.\n'
        'Prefer tactics that make the enemy feel intelligent but still fair and mechanically plausible.\n'
        'Do not invent damage, rolls, HP changes, new abilities, or hidden player knowledge.\n'
        'Return concise JSON with keys: tactical_goal, intended_action, target_preference, ability_preference, '
        'movement_or_positioning, terrain_use, speech_or_telegraph, backup_plan, reasoning_summary.\n\n'
        f"ENEMY_TACTICS_PLANNER_INPUT:\n{json.dumps(planner_input, sort_keys=True)}"
    )


def build_enemy_tactics_compiler_prompt(
    enemy: dict[str, Any],
    combat: dict[str, Any],
    settings: dict[str, Any],
    *,
    allowed_target_ids: set[str],
    fallback_intent: dict[str, Any],
    candidates: list[dict[str, Any]],
    planner_output: str,
    facts: dict[str, Any] | None = None,
) -> str:
    compiler_input = {
        **_tactics_context_payload(
            enemy,
            combat,
            settings,
            allowed_target_ids=allowed_target_ids,
            fallback_intent=fallback_intent,
            candidates=candidates,
            facts=facts,
        ),
        'planner_output': planner_output[:5000],
        'allowed_intent_types': sorted(_COMPILED_INTENT_TYPES),
        'schema': {
            'required': ['intent_type', 'reason', 'confidence'],
            'optional': ['target_id', 'ability_id', 'movement_goal', 'visible_telegraph', 'suggested_speech'],
            'additionalProperties': False,
            'known_target_ids': [participant.get('id') for participant in combat.get('participants') or [] if isinstance(participant, dict) and participant.get('id')],
            'visible_player_target_ids': sorted(allowed_target_ids),
            'known_ability_ids': [ability.get('id') for ability in enemy.get('abilities') or [] if isinstance(ability, dict) and ability.get('id')],
        },
    }
    return (
        'Compile the tactical recommendation into exactly one strict engine intent JSON object.\n'
        'Use snake_case keys only. Use target_id only when it is one of known_target_ids. '
        'Use ability_id only when it is one of known_ability_ids. '
        'For attacks or hostile abilities, target a visible_player_target_id. '
        'For cover, positioning, escape, warning, bargaining, or self-preservation, prefer movement_goal and a non-damage intent. '
        'If the recommendation cannot be represented safely, compile the deterministic baseline instead.\n'
        'Return JSON only with no markdown.\n\n'
        f"ENEMY_TACTICS_COMPILER_INPUT:\n{json.dumps(compiler_input, sort_keys=True)}"
    )


def _first_attack_ability_id(enemy: dict[str, Any]) -> str | None:
    for ability in enemy.get('abilities') or []:
        if isinstance(ability, dict) and ability.get('id') and ability.get('damage'):
            return str(ability['id'])
    for ability in enemy.get('abilities') or []:
        if isinstance(ability, dict) and ability.get('id') and ability.get('type') == 'attack':
            return str(ability['id'])
    return None


def _validated_compiled_tactic(
    payload: dict[str, Any] | None,
    *,
    enemy: dict[str, Any],
    combat: dict[str, Any],
    allowed_target_ids: set[str],
    fallback_intent: dict[str, Any],
    planner_output: str,
    planner_model: str,
    compiler_model: str,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if set(payload.keys()) - _COMPILED_INTENT_ALLOWED_KEYS:
        return None
    intent_type = str(payload.get('intent_type') or '').strip().lower()
    if intent_type == 'flee':
        intent_type = 'retreat'
    if intent_type not in _COMPILED_INTENT_TYPES:
        return None
    participant_ids = {
        str(participant.get('id'))
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and participant.get('id')
    }
    target_id = str(payload.get('target_id') or '').strip() or None
    if target_id and target_id not in participant_ids:
        return None
    if intent_type in _HOSTILE_TARGET_INTENTS and target_id and target_id not in allowed_target_ids:
        return None
    ability_ids = {
        str(ability.get('id'))
        for ability in enemy.get('abilities') or []
        if isinstance(ability, dict) and ability.get('id')
    }
    ability_id = str(payload.get('ability_id') or '').strip() or None
    if ability_id and ability_id not in ability_ids:
        return None
    if intent_type == 'attack' and not ability_id:
        ability_id = str(fallback_intent.get('abilityId') or _first_attack_ability_id(enemy) or '').strip() or None
    if intent_type == 'use_ability' and not ability_id:
        return None
    try:
        confidence = float(payload.get('confidence'))
    except (TypeError, ValueError):
        return None
    confidence = max(0.0, min(1.0, confidence))
    reason = str(payload.get('reason') or '').strip()
    if not reason:
        return None
    intent = {
        'enemyId': enemy.get('id'),
        'intentType': intent_type,
        'reason': reason[:320],
        'confidence': confidence,
        'tacticsCompilation': {
            'plannerModel': planner_model,
            'compilerModel': compiler_model,
            'plannerSummary': planner_output[:900],
            'compiledReason': reason[:320],
        },
        'brainSource': f'{planner_model} -> {compiler_model}',
    }
    if target_id:
        intent['targetId'] = target_id
    if ability_id:
        intent['abilityId'] = ability_id
    movement_goal = str(payload.get('movement_goal') or '').strip()
    if movement_goal:
        intent['movementGoal'] = movement_goal[:220]
    visible_telegraph = str(payload.get('visible_telegraph') or '').strip()
    if visible_telegraph:
        intent['visibleTelegraph'] = visible_telegraph[:220]
    suggested_speech = str(payload.get('suggested_speech') or '').strip()
    if suggested_speech:
        intent['suggestedSpeech'] = suggested_speech[:180]
    return intent


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


def freeform_enemy_tactics_enabled(settings: dict[str, Any]) -> bool:
    if not settings.get('allowFreeformEnemyTactics', True):
        return False
    return sentient_enemy_brain_enabled()


def plan_freeform_enemy_tactic(
    enemy: dict[str, Any],
    combat: dict[str, Any],
    settings: dict[str, Any],
    *,
    allowed_target_ids: set[str],
    fallback_intent: dict[str, Any],
    candidates: list[dict[str, Any]],
    facts: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    if not freeform_enemy_tactics_enabled(settings):
        return None, 'disabled'
    try:
        planner_response = get_helper_provider(task=ENEMY_TACTICS_PLANNER_TASK).generate(
            ProviderRequest(
                prompt=build_enemy_tactics_planner_prompt(
                    enemy,
                    combat,
                    settings,
                    allowed_target_ids=allowed_target_ids,
                    fallback_intent=fallback_intent,
                    candidates=candidates,
                    facts=facts,
                ),
                system_message=ENEMY_TACTICS_PLANNER_SYSTEM_MESSAGE,
            )
        )
        planner_output = str(planner_response.text or '').strip()
        if not planner_output:
            raise ValueError('enemy tactics planner returned empty output')
        compiler_response = get_helper_provider(task=ENEMY_TACTICS_COMPILER_TASK).generate(
            ProviderRequest(
                prompt=build_enemy_tactics_compiler_prompt(
                    enemy,
                    combat,
                    settings,
                    allowed_target_ids=allowed_target_ids,
                    fallback_intent=fallback_intent,
                    candidates=candidates,
                    planner_output=planner_output,
                    facts=facts,
                ),
                system_message=ENEMY_TACTICS_COMPILER_SYSTEM_MESSAGE,
            )
        )
        payload = extract_json_object(compiler_response.text)
        intent = _validated_compiled_tactic(
            payload,
            enemy=enemy,
            combat=combat,
            allowed_target_ids=allowed_target_ids,
            fallback_intent=fallback_intent,
            planner_output=planner_output,
            planner_model=planner_response.model,
            compiler_model=compiler_response.model,
        )
        if not intent:
            raise ValueError('enemy tactics compiler returned invalid intent payload')
        telemetry_metric('combat.enemy_tactics_pipeline.success_total', 1, tags={'planner': planner_response.model, 'compiler': compiler_response.model})
        telemetry_event(
            'combat.enemy_tactics_pipeline.compiled',
            payload={
                'enemyId': enemy.get('id'),
                'intentType': intent.get('intentType'),
                'targetId': intent.get('targetId'),
                'abilityId': intent.get('abilityId'),
                'plannerModel': planner_response.model,
                'compilerModel': compiler_response.model,
            },
        )
        return intent, f'{planner_response.model} -> {compiler_response.model}'
    except Exception as exc:
        telemetry_event('combat.enemy_tactics_pipeline.failed', payload={'enemyId': enemy.get('id'), 'error': str(exc)[:300]}, severity='warning')
        return None, 'deterministic_fallback'


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
