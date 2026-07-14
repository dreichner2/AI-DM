from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from typing import Any

from flask import current_app, has_app_context

from aidm_server.combat.boss_tactics import deterministic_boss_tactic, plan_boss_advisory, plan_boss_candidate_tactic, should_use_boss_tactics_helper
from aidm_server.combat.difficulty import combat_difficulty_from_state, normalize_combat_difficulty_ai
from aidm_server.combat.enemy_brain import plan_freeform_enemy_tactic, plan_sentient_enemy_intent, should_use_sentient_enemy_brain
from aidm_server.combat.morale import actionable_participants, living_participants, recalculate_morale
from aidm_server.canon_text import int_or_default
from aidm_server.combat.state import (
    combat_ability_is_available,
    combat_ability_resolution_mode,
    normalize_combat_state,
    participant_can_take_turn,
    participant_is_targetable,
)


_NON_HELPER_BOSS_TACTIC_SOURCES = {'deterministic', 'deterministic_fallback'}


def _hp_percent(participant: dict[str, Any]) -> int:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    current = max(0, int_or_default(hp.get('current'), default=0))
    maximum = max(1, int_or_default(hp.get('max'), default=1))
    return round((current / maximum) * 100)


def _living_participants(combat: dict[str, Any], team: str | None = None) -> list[dict[str, Any]]:
    return living_participants(combat, team)


def _actionable_participants(combat: dict[str, Any], team: str | None = None) -> list[dict[str, Any]]:
    return actionable_participants(combat, team)


def _config_bool(name: str, default: bool) -> bool:
    value: Any = None
    if has_app_context():
        value = current_app.config.get(name)
    if value is None:
        value = os.getenv(name)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on', 'enabled'}


def _config_int(name: str, default: int) -> int:
    value: Any = None
    if has_app_context():
        value = current_app.config.get(name)
    if value is None:
        value = os.getenv(name)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _parallel_enemy_intents_enabled() -> bool:
    return _config_bool('AIDM_ENEMY_INTENT_PARALLEL', False)


def _enemy_intent_max_workers() -> int:
    return _config_int('AIDM_ENEMY_INTENT_MAX_WORKERS', 4)


def _leader_dead(combat: dict[str, Any]) -> bool:
    for participant in combat.get('participants') or []:
        if not isinstance(participant, dict):
            continue
        behavior = participant.get('behavior') if isinstance(participant.get('behavior'), dict) else {}
        if participant.get('team') == 'enemy' and behavior.get('combatRole') == 'leader':
            return participant.get('isAlive') is False or _hp_percent(participant) <= 0
    return False


def _is_outnumbered(combat: dict[str, Any]) -> bool:
    enemies = _living_participants(combat, 'enemy')
    players = _living_participants(combat, 'player')
    return bool(enemies and len(enemies) < len(players))


def _is_alone(enemy: dict[str, Any], combat: dict[str, Any]) -> bool:
    allies = [
        participant
        for participant in _living_participants(combat, enemy.get('team') or 'enemy')
        if participant.get('id') != enemy.get('id')
    ]
    return not allies


def _target_priority_value(enemy: dict[str, Any], target: dict[str, Any], settings: dict[str, Any]) -> tuple[int, int]:
    behavior = enemy.get('behavior') if isinstance(enemy.get('behavior'), dict) else {}
    priorities = [str(item or '').strip().lower() for item in behavior.get('targetPriority') or []]
    target_hp = _hp_percent(target)
    score = 0
    active_actor_id = str(settings.get('activeActorId') or '').strip()
    if active_actor_id and str(target.get('id') or '').strip() == active_actor_id:
        score += 24
    if 'wounded' in priorities and target_hp <= 50:
        score += 20
    if 'isolated' in priorities and (target.get('position') or {}).get('rangeBand') in {'far', 'distant'}:
        score += 15
    if 'lowest_armor' in priorities:
        score += max(0, 20 - int_or_default(target.get('armorClass'), default=10))
    if 'nearest' in priorities and (target.get('position') or {}).get('rangeBand') in {'melee', 'near'}:
        score += 8
    if 'last_damaged_by' in priorities and (enemy.get('memory') or {}).get('lastDamagedBy') == target.get('id'):
        score += 18
    if 'personal_grudge_target' in priorities and (enemy.get('memory') or {}).get('personalGrudgeTargetId') == target.get('id'):
        score += 35
    target_role_blob = f"{target.get('class')} {target.get('class_')} {target.get('role')} {target.get('name')}".lower()
    if settings.get('allowTargetHealers') and 'healer' in priorities and any(word in target_role_blob for word in ('cleric', 'healer', 'medic', 'priest')):
        score += 18
    if 'spellcaster' in priorities and any(word in target_role_blob for word in ('wizard', 'sorcerer', 'warlock', 'mage', 'caster')):
        score += 16
    return (-score, int_or_default(target.get('armorClass'), default=10))


def _zone_id(participant: dict[str, Any]) -> str:
    position = participant.get('position') if isinstance(participant.get('position'), dict) else {}
    return str(position.get('zoneId') or position.get('zone_id') or '').strip()


def target_reachable_now(enemy: dict[str, Any], target: dict[str, Any]) -> bool:
    enemy_zone = _zone_id(enemy)
    target_zone = _zone_id(target)
    return not (enemy_zone and target_zone and enemy_zone != target_zone)


def reachable_players_for_enemy(enemy: dict[str, Any], players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [player for player in players if target_reachable_now(enemy, player)]


def _battlefield_items(combat: dict[str, Any], key: str) -> list[dict[str, Any]]:
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), dict) else {}
    return [item for item in (battlefield.get(key) or []) if isinstance(item, dict)]


def _item_name(item: dict[str, Any], default: str) -> str:
    return str(item.get('name') or item.get('id') or default).strip() or default


def _has_escape_route(combat: dict[str, Any]) -> bool:
    exits = _battlefield_items(combat, 'exits')
    return not exits or any(not bool(exit_item.get('blocked')) for exit_item in exits)


def _role_blob(participant: dict[str, Any]) -> str:
    return f"{participant.get('class')} {participant.get('class_')} {participant.get('role')} {participant.get('name')}".lower()


def _visible_targets(enemy: dict[str, Any], combat: dict[str, Any], players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), dict) else {}
    if battlefield.get('visibility') == 'magical_darkness' and not (enemy.get('senses') or {}).get('blindsight'):
        return []
    return reachable_players_for_enemy(enemy, players)


def build_combat_facts(enemy: dict[str, Any], combat: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = normalize_combat_difficulty_ai(settings)
    allies = [
        participant
        for participant in _living_participants(combat, enemy.get('team') or 'enemy')
        if participant.get('id') != enemy.get('id')
    ]
    players = _living_participants(combat, 'player')
    visible_targets = _visible_targets(enemy, combat, players)
    wounded_targets = [target for target in visible_targets if _hp_percent(target) <= 50]
    isolated_targets = [
        target
        for target in visible_targets
        if (target.get('position') or {}).get('rangeBand') in {'far', 'distant'} or (target.get('position') or {}).get('isHidden')
    ]
    spellcaster_targets = [target for target in visible_targets if any(word in _role_blob(target) for word in ('wizard', 'sorcerer', 'warlock', 'mage', 'caster'))]
    healer_targets = [target for target in visible_targets if any(word in _role_blob(target) for word in ('cleric', 'healer', 'medic', 'priest'))]
    return {
        'enemyId': enemy.get('id'),
        'enemyHpPercent': _hp_percent(enemy),
        'allies': allies,
        'enemies': _living_participants(combat, 'enemy'),
        'playerCharacters': players,
        'isOutnumbered': _is_outnumbered(combat),
        'leaderAlive': not _leader_dead(combat),
        'hasEscapeRoute': _has_escape_route(combat),
        'canSeeTargets': bool(visible_targets),
        'visibleTargets': visible_targets,
        'woundedTargets': wounded_targets,
        'spellcasterTargets': spellcaster_targets if settings.get('allowTargetHealers') else spellcaster_targets,
        'healerTargets': healer_targets if settings.get('allowTargetHealers') else [],
        'isolatedTargets': isolated_targets,
        'battlefieldHazards': _battlefield_items(combat, 'hazards'),
        'availableCover': _battlefield_items(combat, 'cover'),
        'availableExits': [exit_item for exit_item in _battlefield_items(combat, 'exits') if not bool(exit_item.get('blocked'))],
        'availableInteractables': _battlefield_items(combat, 'interactables'),
    }


def choose_target(enemy: dict[str, Any], players: list[dict[str, Any]], settings: dict[str, Any] | None = None) -> dict[str, Any] | None:
    reachable_players = reachable_players_for_enemy(enemy, players)
    if not reachable_players:
        return None
    raw_settings = settings if isinstance(settings, dict) else {}
    settings = normalize_combat_difficulty_ai(raw_settings)
    if raw_settings.get('activeActorId'):
        settings['activeActorId'] = str(raw_settings.get('activeActorId'))
    return sorted(reachable_players, key=lambda target: _target_priority_value(enemy, target, settings))[0]


def _best_ability(
    enemy: dict[str, Any],
    intent_type: str = 'attack',
    *,
    round_number: Any = None,
) -> dict[str, Any] | None:
    abilities = [
        ability
        for ability in (enemy.get('abilities') or [])
        if (
            isinstance(ability, dict)
            and combat_ability_is_available(ability, round_number=round_number)
            and combat_ability_resolution_mode(ability)
        )
    ]
    if intent_type == 'use_ability':
        for ability in abilities:
            if ability.get('type') in {'spell', 'special', 'legendary', 'lair'} and ability.get('cooldown') in {'none', 'turn', 'recharge_5_6', 'once_per_combat'}:
                return ability
    for ability in abilities:
        if ability.get('damage'):
            return ability
    return abilities[0] if abilities else None


def _intent(
    enemy: dict[str, Any],
    intent_type: str,
    *,
    target: dict[str, Any] | None = None,
    ability: dict[str, Any] | None = None,
    reason: str,
    confidence: float,
    movement_goal: str | None = None,
    speech: str | None = None,
    telegraph: str | None = None,
) -> dict[str, Any]:
    payload = {
        'enemyId': enemy.get('id'),
        'intentType': intent_type,
        'targetId': target.get('id') if target else None,
        'abilityId': ability.get('id') if ability else None,
        'movementGoal': movement_goal,
        'reason': reason,
        'confidence': max(0.0, min(1.0, confidence)),
        'visibleTelegraph': telegraph,
        'suggestedSpeech': speech,
        'mechanicalChanges': [],
        'requiredRolls': [],
    }
    return {key: value for key, value in payload.items() if value not in (None, [], {})}


def _safe_id_part(value: Any, default: str = 'unknown') -> str:
    text = str(value or default).strip() or default
    return ''.join(ch if ch.isalnum() or ch in {'_', '-'} else '_' for ch in text)


def _combat_state_version(combat: dict[str, Any]) -> str:
    explicit = combat.get('stateVersion') or combat.get('state_version')
    if explicit:
        return _safe_id_part(explicit, default='combat_state')
    participant_count = len([item for item in combat.get('participants') or [] if isinstance(item, dict)])
    return f"combat_round_{int_or_default(combat.get('round'), default=1)}_p{participant_count}"


def _actor_turn_id(enemy: dict[str, Any], combat: dict[str, Any]) -> str:
    round_id = int_or_default(combat.get('round'), default=1)
    return f"turn_{round_id}.{_safe_id_part(enemy.get('id'), default='enemy')}"


def _participant_lookup(combat: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(participant.get('id')): participant
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and participant.get('id')
    }


def _ability_lookup(enemy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(ability.get('id')): ability
        for ability in enemy.get('abilities') or []
        if isinstance(ability, dict) and ability.get('id')
    }


def _candidate_summary(intent: dict[str, Any]) -> str:
    parts = [str(intent.get('intentType') or 'act').replace('_', ' ')]
    if intent.get('movementGoal'):
        parts.append(str(intent['movementGoal']))
    if intent.get('abilityId'):
        ability_text = f"use {intent['abilityId']}"
        if intent.get('targetId'):
            ability_text += f" on {intent['targetId']}"
        parts.append(ability_text)
    elif intent.get('targetId'):
        parts.append(f"target {intent['targetId']}")
    reason = str(intent.get('reason') or '').strip()
    if reason:
        parts.append(reason)
    return '; '.join(parts)[:320]


def _targeting_tags(target: dict[str, Any] | None) -> list[str]:
    if not target:
        return []
    tags: set[str] = set()
    hp = _hp_percent(target)
    if hp <= 50:
        tags.add('wounded')
    role_blob = _role_blob(target)
    if any(word in role_blob for word in ('wizard', 'sorcerer', 'warlock', 'mage', 'caster')):
        tags.add('caster')
    if any(word in role_blob for word in ('cleric', 'healer', 'medic', 'priest')):
        tags.add('healer')
    position = target.get('position') if isinstance(target.get('position'), dict) else {}
    if position.get('rangeBand') in {'far', 'distant'} or not position.get('coverId'):
        tags.add('exposed_target')
    if target.get('team') == 'enemy':
        tags.add('ally')
    if target.get('team') == 'player':
        tags.add('hostile')
    return sorted(tags)


def _ability_tags(ability: dict[str, Any] | None) -> list[str]:
    if not ability:
        return []
    tags: set[str] = set()
    ability_type = str(ability.get('type') or '').strip()
    if ability_type:
        tags.add(ability_type)
    range_text = str(ability.get('range') or '').lower()
    if any(word in range_text for word in ('ranged', 'short', 'medium', 'long', 'far')):
        tags.add('ranged')
        tags.add('safe_pressure')
    if ability.get('targetType') in {'single', 'creature'} or ability.get('damage'):
        tags.add('single_target')
    if ability_type in {'spell', 'special', 'legendary', 'lair'}:
        tags.add('limited_or_special')
    for tag in ability.get('tacticalTags') or ability.get('tactical_tags') or []:
        if isinstance(tag, str) and tag.strip():
            tags.add(tag.strip())
    return sorted(tags)


def _intent_tags(intent: dict[str, Any], target: dict[str, Any] | None, behavior: dict[str, Any]) -> list[str]:
    intent_type = str(intent.get('intentType') or 'act').strip()
    tags = {intent_type}
    movement_goal = str(intent.get('movementGoal') or '').lower()
    reason = str(intent.get('reason') or '').lower()
    if 'cover' in movement_goal or 'cover' in reason or 'pillar' in movement_goal:
        tags.update({'use_cover', 'preserve_distance'})
    if intent_type in {'retreat', 'flee', 'surrender', 'negotiate'}:
        tags.add('self_preservation')
    if intent_type in {'protect_ally', 'complete_objective', 'delay', 'use_environment'}:
        tags.add('objective_support')
    if intent_type in {'attack', 'use_ability'}:
        tags.add('pressure_target')
    target_tags = set(_targeting_tags(target))
    if 'caster' in target_tags:
        tags.add('pressure_caster')
    if 'wounded' in target_tags:
        tags.add('focus_wounded')
    primary_goal = str(behavior.get('primaryGoal') or '').strip()
    if primary_goal:
        tags.add(primary_goal)
    return sorted(tags)


def _risk_posture(intent: dict[str, Any], enemy: dict[str, Any]) -> str:
    intent_type = str(intent.get('intentType') or '').strip()
    hp = _hp_percent(enemy)
    if intent_type in {'flee', 'surrender'}:
        return 'desperate'
    if intent_type in {'retreat', 'defend', 'hide', 'negotiate'}:
        return 'cautious'
    if intent_type in {'use_environment', 'use_ability', 'attack'} and hp <= 25:
        return 'desperate'
    if intent_type in {'use_environment', 'call_reinforcements', 'protect_ally'}:
        return 'controlled'
    if intent_type == 'attack':
        return 'balanced'
    return 'balanced'


def _candidate_resolver(
    intent: dict[str, Any],
    *,
    enemy: dict[str, Any],
    target: dict[str, Any] | None,
    ability: dict[str, Any] | None,
    combat: dict[str, Any],
) -> dict[str, Any]:
    action_bundle: list[dict[str, Any]] = []
    step = 1
    if intent.get('movementGoal'):
        action_bundle.append(
            {
                'step': step,
                'type': 'movement_intent',
                'movement_goal': intent.get('movementGoal'),
            }
        )
        step += 1
    if intent.get('abilityId'):
        action_bundle.append(
            {
                'step': step,
                'type': 'use_ability',
                'ability_id': intent.get('abilityId'),
                'target_id': intent.get('targetId'),
            }
        )
    else:
        action_bundle.append(
            {
                'step': step,
                'type': 'combat_intent',
                'intent_type': intent.get('intentType'),
                'target_id': intent.get('targetId'),
            }
        )
    return {
        'resolverType': 'engine_intent_bundle_v1',
        'actorId': enemy.get('id'),
        'combatStateVersion': _combat_state_version(combat),
        'actionBundle': action_bundle,
        'mechanicalSummary': _candidate_summary(intent),
    }


def _candidate_legality_report(
    intent: dict[str, Any],
    *,
    enemy: dict[str, Any],
    target: dict[str, Any] | None,
    ability: dict[str, Any] | None,
    combat: dict[str, Any],
) -> dict[str, Any]:
    actor_can_act = participant_can_take_turn(enemy)
    target_id = intent.get('targetId')
    target_valid = not target_id or (target is not None and participant_is_targetable(target))
    ability_id = intent.get('abilityId')
    ability_available = not ability_id or (
        ability is not None
        and combat_ability_is_available(ability, round_number=combat.get('round'))
        and bool(combat_ability_resolution_mode(ability))
    )
    if target and target.get('team') == 'player':
        target_visible = target_reachable_now(enemy, target)
    else:
        target_visible = target_valid
    destination_reachable = not intent.get('movementGoal') or bool(str(intent.get('movementGoal')).strip())
    report = {
        'actor_can_act': actor_can_act,
        'ability_available': ability_available,
        'target_valid': target_valid,
        'target_visible_or_known': target_visible,
        'destination_reachable': destination_reachable,
        'action_economy_valid': True,
        'resources_available': ability_available,
    }
    report['can_resolve_now'] = all(report.values())
    return report


def _candidate_tags(
    intent: dict[str, Any],
    *,
    enemy: dict[str, Any],
    target: dict[str, Any] | None,
    ability: dict[str, Any] | None,
    behavior: dict[str, Any],
) -> dict[str, Any]:
    objective_tags = []
    primary_goal = str(behavior.get('primaryGoal') or '').strip()
    if primary_goal:
        objective_tags.append(primary_goal)
    return {
        'intent': _intent_tags(intent, target, behavior),
        'targeting': _targeting_tags(target),
        'abilityProfile': _ability_tags(ability),
        'positioning': ['movement'] if intent.get('movementGoal') else [],
        'riskPosture': _risk_posture(intent, enemy),
        'moraleFit': ['low_morale'] if int_or_default(enemy.get('morale'), default=50) <= 35 else [],
        'objective': objective_tags,
        'dramaticRole': ['boss'] if enemy.get('kind') == 'boss' or enemy.get('challengeTier') == 'boss' else [],
    }


def _attach_candidate_contracts(
    candidates: list[dict[str, Any]],
    *,
    enemy: dict[str, Any],
    combat: dict[str, Any],
) -> None:
    actor_turn_id = _actor_turn_id(enemy, combat)
    combat_version = _combat_state_version(combat)
    participants = _participant_lookup(combat)
    abilities = _ability_lookup(enemy)
    behavior = enemy.get('behavior') if isinstance(enemy.get('behavior'), dict) else {}
    for index, candidate in enumerate(candidates, start=1):
        intent = candidate.get('intent') if isinstance(candidate.get('intent'), dict) else {}
        candidate_id = f"{actor_turn_id}.cand_{index:03d}"
        target = participants.get(str(intent.get('targetId') or ''))
        ability = abilities.get(str(intent.get('abilityId') or ''))
        tags = _candidate_tags(intent, enemy=enemy, target=target, ability=ability, behavior=behavior)
        resolver = _candidate_resolver(intent, enemy=enemy, target=target, ability=ability, combat=combat)
        legality = _candidate_legality_report(intent, enemy=enemy, target=target, ability=ability, combat=combat)
        deterministic_score = round(min(1.0, max(0.0, int_or_default(candidate.get('score'), default=0) / 100)), 2)
        contract = {
            'candidateId': candidate_id,
            'candidateVersion': 'candidate_v1',
            'combatStateVersion': combat_version,
            'actorId': enemy.get('id'),
            'actorTurnId': actor_turn_id,
            'kind': str(intent.get('intentType') or 'act'),
            'isFallbackCandidate': index == 1,
            'deterministicRank': index,
            'deterministicScore': deterministic_score,
            'legalAtGeneration': bool(legality.get('can_resolve_now')),
            'resolver': resolver,
            'mechanicalSummary': resolver['mechanicalSummary'],
            'llmSummary': _candidate_summary(intent),
            'tags': tags,
            'legalityReport': legality,
            'dryRun': {
                'candidateId': candidate_id,
                'canResolveNow': bool(legality.get('can_resolve_now')),
                'blockingReason': None if legality.get('can_resolve_now') else 'candidate failed generation-time legality report',
                'wouldConsume': {
                    'action': str(intent.get('intentType') or '') not in {'wait'},
                    'bonusAction': False,
                    'reaction': False,
                    'movementIntent': bool(intent.get('movementGoal')),
                    'resources': [],
                },
            },
        }
        candidate.update(contract)
        intent.update(
            {
                'candidateId': candidate_id,
                'candidateVersion': contract['candidateVersion'],
                'combatStateVersion': combat_version,
                'actorTurnId': actor_turn_id,
                'resolver': resolver,
                'mechanicalSummary': contract['mechanicalSummary'],
                'llmSummary': contract['llmSummary'],
                'tags': tags,
                'legalAtGeneration': contract['legalAtGeneration'],
                'dryRun': contract['dryRun'],
            }
        )


def _candidate_debug_view(candidate: dict[str, Any]) -> dict[str, Any]:
    intent = candidate.get('intent') if isinstance(candidate.get('intent'), dict) else {}
    return {
        'candidateId': candidate.get('candidateId'),
        'candidateVersion': candidate.get('candidateVersion'),
        'combatStateVersion': candidate.get('combatStateVersion'),
        'actorId': candidate.get('actorId'),
        'actorTurnId': candidate.get('actorTurnId'),
        'kind': candidate.get('kind'),
        'score': candidate.get('score'),
        'deterministicRank': candidate.get('deterministicRank'),
        'deterministicScore': candidate.get('deterministicScore'),
        'isFallbackCandidate': candidate.get('isFallbackCandidate'),
        'legalAtGeneration': candidate.get('legalAtGeneration'),
        'intentType': candidate.get('intentType'),
        'targetId': candidate.get('targetId'),
        'abilityId': candidate.get('abilityId'),
        'movementGoal': (candidate.get('intent') or {}).get('movementGoal') if isinstance(candidate.get('intent'), dict) else None,
        'reason': candidate.get('reason'),
        'confidence': candidate.get('confidence'),
        'llmSummary': candidate.get('llmSummary'),
        'mechanicalSummary': candidate.get('mechanicalSummary'),
        'tags': candidate.get('tags'),
        'matcherScore': candidate.get('matcherScore'),
        'matcherSignals': candidate.get('matcherSignals'),
        'bossPlanner': candidate.get('bossPlanner'),
        'brainSource': intent.get('brainSource'),
        'selectionMethod': intent.get('selectionMethod'),
        'selectorSkippedReason': intent.get('selectorSkippedReason'),
        'tacticsCompilation': intent.get('tacticsCompilation'),
        'resolverType': (candidate.get('resolver') or {}).get('resolverType') if isinstance(candidate.get('resolver'), dict) else None,
        'dryRun': candidate.get('dryRun'),
    }


def _recent_intent_strings(enemy: dict[str, Any]) -> list[str]:
    memory = enemy.get('memory') if isinstance(enemy.get('memory'), dict) else {}
    result = []
    for item in memory.get('recentIntents') or memory.get('recent_intents') or []:
        if isinstance(item, str):
            result.append(item.lower())
        elif isinstance(item, dict):
            pieces = [
                item.get('intentType') or item.get('intent_type') or item.get('type'),
                item.get('targetId') or item.get('target_id'),
                item.get('abilityId') or item.get('ability_id'),
            ]
            result.append(' '.join(str(piece or '').lower() for piece in pieces))
    return result[-4:]


def _candidate_matcher_score(candidate: dict[str, Any], enemy: dict[str, Any], settings: dict[str, Any]) -> tuple[float, list[str]]:
    score = float(candidate.get('deterministicScore') or 0.0)
    signals = ['base_deterministic_score']
    tags = candidate.get('tags') if isinstance(candidate.get('tags'), dict) else {}
    intent_tags = set(tags.get('intent') or [])
    targeting_tags = set(tags.get('targeting') or [])
    ability_tags = set(tags.get('abilityProfile') or [])
    objective_tags = set(tags.get('objective') or [])
    risk_posture = str(tags.get('riskPosture') or '')
    behavior = enemy.get('behavior') if isinstance(enemy.get('behavior'), dict) else {}
    primary_goal = str(behavior.get('primaryGoal') or '').strip()
    combat_role = str(behavior.get('combatRole') or '').strip()
    hp = _hp_percent(enemy)
    morale = int_or_default(enemy.get('morale'), default=50)
    if primary_goal and primary_goal in objective_tags | intent_tags:
        score += 0.12
        signals.append('objective_priority_match')
    if combat_role in {'sniper', 'skirmisher', 'support', 'controller'} and {'use_cover', 'preserve_distance'} & intent_tags:
        score += 0.08
        signals.append('role_positioning_match')
    if combat_role in {'tank', 'leader', 'support'} and {'protect_ally', 'objective_support'} & intent_tags:
        score += 0.08
        signals.append('role_support_match')
    if hp <= 35 and risk_posture in {'cautious', 'desperate'}:
        score += 0.08
        signals.append('low_hp_risk_match')
    if morale <= 35 and {'self_preservation', 'use_cover'} & intent_tags:
        score += 0.08
        signals.append('low_morale_fit')
    if settings.get('allowTargetHealers') and targeting_tags & {'caster', 'healer'}:
        score += 0.06
        signals.append('priority_role_target')
    if ability_tags & {'limited_or_special'} and morale < 30:
        score -= 0.08
        signals.append('limited_resource_low_morale_penalty')
    recent = _recent_intent_strings(enemy)
    intent_type = str(candidate.get('intentType') or '').lower()
    target_id = str(candidate.get('targetId') or '').lower()
    if recent and all(intent_type and intent_type in item for item in recent[-2:]):
        score -= 0.08
        signals.append('repetition_penalty')
    if recent and target_id and all(target_id in item for item in recent[-2:]):
        score -= 0.04
        signals.append('target_fixation_penalty')
    return round(max(0.0, min(1.25, score)), 3), signals


def _apply_candidate_matcher(candidates: list[dict[str, Any]], enemy: dict[str, Any], settings: dict[str, Any]) -> None:
    for candidate in candidates:
        matcher_score, signals = _candidate_matcher_score(candidate, enemy, settings)
        candidate['matcherScore'] = matcher_score
        candidate['matcherSignals'] = signals
        intent = candidate.get('intent') if isinstance(candidate.get('intent'), dict) else {}
        intent['matcherScore'] = matcher_score
        intent['matcherSignals'] = signals


def _apply_boss_planner_bias(candidates: list[dict[str, Any]], planner: dict[str, Any] | None, source: str) -> None:
    if not planner:
        return
    preferred_tags = {str(tag or '').strip() for tag in planner.get('preferredTags') or [] if str(tag or '').strip()}
    desired_intent = str(planner.get('desiredIntentType') or '').strip()
    risk_posture = str(planner.get('riskPosture') or '').strip()
    for candidate in candidates:
        tags = candidate.get('tags') if isinstance(candidate.get('tags'), dict) else {}
        candidate_tags = set(tags.get('intent') or []) | set(tags.get('objective') or []) | set(tags.get('abilityProfile') or []) | set(tags.get('positioning') or [])
        bonus = 0.0
        signals = []
        overlap = candidate_tags & preferred_tags
        if overlap:
            bonus += min(0.18, 0.06 * len(overlap))
            signals.append('boss_planner_tag_match')
        if desired_intent and desired_intent == candidate.get('intentType'):
            bonus += 0.08
            signals.append('boss_planner_intent_match')
        if risk_posture and risk_posture == tags.get('riskPosture'):
            bonus += 0.04
            signals.append('boss_planner_risk_match')
        if not bonus:
            continue
        candidate['matcherScore'] = round(min(1.25, float(candidate.get('matcherScore') or candidate.get('deterministicScore') or 0.0) + bonus), 3)
        candidate['bossPlanner'] = {
            'source': source,
            'preferredTags': sorted(preferred_tags),
            'matchedTags': sorted(overlap),
            'desiredIntentType': desired_intent,
            'riskPosture': risk_posture,
            'reasoningSummary': planner.get('reasoningSummary'),
            'expiresAfterTurns': max(1, int_or_default(planner.get('expiresAfterTurns'), default=1)),
        }
        candidate['matcherSignals'] = [*(candidate.get('matcherSignals') or []), *signals]
        intent = candidate.get('intent') if isinstance(candidate.get('intent'), dict) else {}
        intent['matcherScore'] = candidate['matcherScore']
        intent['matcherSignals'] = candidate['matcherSignals']
        intent['bossPlanner'] = candidate['bossPlanner']


def _cached_boss_planner(enemy: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    current_intent = enemy.get('currentIntent') if isinstance(enemy.get('currentIntent'), dict) else {}
    cached = current_intent.get('bossPlanner') if isinstance(current_intent.get('bossPlanner'), dict) else {}
    preferred_tags = [str(tag or '').strip() for tag in cached.get('preferredTags') or [] if str(tag or '').strip()]
    remaining_turns = int_or_default(cached.get('expiresAfterTurns'), default=0)
    if not preferred_tags or remaining_turns <= 1:
        return None, 'no_cache'
    source = str(cached.get('source') or 'cached_boss_planner')
    return (
        {
            'source': f'{source}:cached',
            'preferredTags': preferred_tags,
            'desiredIntentType': cached.get('desiredIntentType'),
            'riskPosture': cached.get('riskPosture'),
            'reasoningSummary': cached.get('reasoningSummary'),
            'expiresAfterTurns': remaining_turns - 1,
        },
        f'{source}:cached',
    )


def _select_deterministic_candidate(candidates: list[dict[str, Any]], settings: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not candidates:
        raise ValueError('candidate list is empty')
    top = candidates[0]
    if not settings.get('allowDeterministicCandidateMatcher', True):
        return top, 'deterministic_scoring'
    margin = _candidate_score_margin(candidates)
    if margin >= 0.08:
        return top, 'deterministic_scoring'
    selected = max(
        candidates,
        key=lambda candidate: (
            float(candidate.get('matcherScore') or 0.0),
            float(candidate.get('deterministicScore') or 0.0),
            -int_or_default(candidate.get('deterministicRank'), default=999),
        ),
    )
    if selected is top:
        return top, 'deterministic_scoring'
    return selected, 'deterministic_matcher'


def _revalidate_candidate(candidate: dict[str, Any], enemy: dict[str, Any], combat: dict[str, Any]) -> dict[str, Any]:
    intent = candidate.get('intent') if isinstance(candidate.get('intent'), dict) else {}
    participants = _participant_lookup(combat)
    abilities = _ability_lookup(enemy)
    target = participants.get(str(intent.get('targetId') or ''))
    ability = abilities.get(str(intent.get('abilityId') or ''))
    report = _candidate_legality_report(intent, enemy=enemy, target=target, ability=ability, combat=combat)
    stale = candidate.get('combatStateVersion') != _combat_state_version(combat)
    return {
        **report,
        'staleCandidateVersion': bool(stale),
        'candidateId': candidate.get('candidateId'),
        'combatStateVersion': candidate.get('combatStateVersion'),
        'currentCombatStateVersion': _combat_state_version(combat),
    }


def _selection_metadata_key(intent: dict[str, Any]) -> str | None:
    if isinstance(intent.get('candidateSelection'), dict):
        return 'candidateSelection'
    if isinstance(intent.get('bossTacticsSelection'), dict):
        return 'bossTacticsSelection'
    return None


def _ordered_resolution_candidate_ids(selected_intent: dict[str, Any]) -> list[str]:
    metadata_key = _selection_metadata_key(selected_intent)
    ordered = []
    if selected_intent.get('candidateId'):
        ordered.append(str(selected_intent['candidateId']))
    if metadata_key:
        metadata = selected_intent.get(metadata_key) if isinstance(selected_intent.get(metadata_key), dict) else {}
        selected_id = str(metadata.get('selectedCandidateId') or '').strip()
        if selected_id and selected_id not in ordered:
            ordered.insert(0, selected_id)
        for backup_id in metadata.get('backupCandidateIds') or []:
            backup_id = str(backup_id or '').strip()
            if backup_id and backup_id not in ordered:
                ordered.append(backup_id)
    return ordered


def _intent_from_resolution_candidate(
    candidate: dict[str, Any],
    selected_intent: dict[str, Any],
    *,
    validation: dict[str, Any],
    resolution_source: str,
) -> dict[str, Any]:
    resolved = deepcopy(candidate.get('intent') if isinstance(candidate.get('intent'), dict) else {})
    for key in (
        'selectionMethod',
        'brainSource',
        'tacticSource',
        'candidateSelection',
        'bossTacticsSelection',
        'selectorSkippedReason',
        'deterministicCandidateMargin',
    ):
        if key in selected_intent:
            resolved[key] = deepcopy(selected_intent[key])
    metadata_key = _selection_metadata_key(resolved)
    if metadata_key:
        metadata = resolved.get(metadata_key) if isinstance(resolved.get(metadata_key), dict) else {}
        resolved[metadata_key] = {
            **metadata,
            'resolvedCandidateId': candidate.get('candidateId'),
            'resolutionSource': resolution_source,
            'resolutionFallbackUsed': candidate.get('candidateId') != metadata.get('selectedCandidateId'),
            'resolutionStale': bool(validation.get('staleCandidateVersion')),
        }
    resolved['selectionScore'] = candidate.get('score')
    resolved['resolutionValidation'] = validation
    resolved['resolutionSource'] = resolution_source
    return resolved


def resolve_selected_candidate_for_current_state(
    selected_intent: dict[str, Any],
    candidates: list[dict[str, Any]],
    enemy: dict[str, Any],
    combat: dict[str, Any],
) -> dict[str, Any]:
    """Resolve a selected candidate against current combat state with backups."""
    candidate_by_id = {
        str(candidate.get('candidateId')): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get('candidateId')
    }
    validation_by_id: dict[str, dict[str, Any]] = {}
    ordered_ids = _ordered_resolution_candidate_ids(selected_intent)
    for candidate_id in ordered_ids:
        candidate = candidate_by_id.get(candidate_id)
        if not candidate:
            continue
        validation = _revalidate_candidate(candidate, enemy, combat)
        validation_by_id[candidate_id] = validation
        if validation.get('can_resolve_now'):
            source = 'selected_candidate' if candidate_id == selected_intent.get('candidateId') else 'backup_candidate'
            return _intent_from_resolution_candidate(candidate, selected_intent, validation=validation, resolution_source=source)
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get('legalAtGeneration') is False:
            continue
        validation = _revalidate_candidate(candidate, enemy, combat)
        validation_by_id[str(candidate.get('candidateId'))] = validation
        if validation.get('can_resolve_now'):
            resolved = _intent_from_resolution_candidate(candidate, selected_intent, validation=validation, resolution_source='deterministic_resolution_fallback')
            resolved['resolutionRejectedCandidates'] = validation_by_id
            return resolved
    fallback = deepcopy(selected_intent)
    fallback['resolutionValidation'] = {
        'can_resolve_now': False,
        'candidateId': selected_intent.get('candidateId'),
        'rejectedCandidates': validation_by_id,
        'blockingReason': 'no legal candidate remained at resolution time',
    }
    fallback['resolutionSource'] = 'no_legal_candidate'
    return fallback


def _candidate_score_margin(candidates: list[dict[str, Any]]) -> float:
    legal = [candidate for candidate in candidates if candidate.get('legalAtGeneration') is not False]
    if len(legal) < 2:
        return 1.0
    scores = sorted((float(candidate.get('deterministicScore') or 0.0) for candidate in legal), reverse=True)
    return round(max(0.0, scores[0] - scores[1]), 3)


def _should_call_sentient_selector(
    enemy: dict[str, Any],
    settings: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    selected_intent: dict[str, Any],
    use_boss_tactics: bool,
) -> bool:
    if use_boss_tactics:
        selected_intent['selectorSkippedReason'] = 'boss_tactics_owns_selection'
        return False
    if isinstance(selected_intent.get('tacticsCompilation'), dict):
        selected_intent['selectorSkippedReason'] = 'freeform_tactics_compiler_selected'
        return False
    if not should_use_sentient_enemy_brain(enemy, settings):
        selected_intent['selectorSkippedReason'] = 'sentient_brain_disabled_or_not_sentient'
        return False
    if settings.get('_sentientSelectorAllowedByBudget') is False:
        selected_intent['selectorSkippedReason'] = 'llm_round_budget_reserved_elsewhere'
        return False
    legal_candidates = [candidate for candidate in candidates if candidate.get('legalAtGeneration') is not False]
    min_candidates = max(1, int_or_default(settings.get('sentientSelectorMinCandidates'), default=2))
    if len(legal_candidates) < min_candidates:
        selected_intent['selectorSkippedReason'] = 'not_enough_legal_candidates'
        return False
    if settings.get('forceSentientEnemyBrain'):
        return True
    margin = _candidate_score_margin(legal_candidates)
    threshold = float(settings.get('skipLlmWhenTopCandidateMarginExceeds') or 0.0)
    selected_intent['deterministicCandidateMargin'] = margin
    if threshold > 0 and margin >= threshold:
        selected_intent['selectorSkippedReason'] = 'deterministic_top_candidate_clear'
        return False
    return True


def _should_call_freeform_tactics(
    enemy: dict[str, Any],
    settings: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    use_boss_tactics: bool,
) -> bool:
    if use_boss_tactics:
        return False
    if not settings.get('allowFreeformEnemyTactics', True):
        return False
    if settings.get('forceSentientEnemyBrain'):
        return False
    if settings.get('_sentientSelectorAllowedByBudget') is False:
        return False
    if not should_use_sentient_enemy_brain(enemy, settings):
        return False
    if settings.get('forceFreeformEnemyTactics'):
        return True
    legal_candidates = [candidate for candidate in candidates if candidate.get('legalAtGeneration') is not False]
    min_candidates = max(1, int_or_default(settings.get('sentientSelectorMinCandidates'), default=2))
    if len(legal_candidates) < min_candidates:
        return True
    threshold = float(settings.get('skipLlmWhenTopCandidateMarginExceeds') or 0.0)
    if threshold > 0 and _candidate_score_margin(legal_candidates) >= threshold:
        return False
    return True


def _morale_after_context(enemy: dict[str, Any], combat: dict[str, Any]) -> int:
    morale, _events = recalculate_morale(enemy, combat)
    return morale


def _score_attack(enemy: dict[str, Any], target: dict[str, Any] | None, behavior: dict[str, Any], settings: dict[str, Any]) -> int:
    score = 35 + int_or_default(behavior.get('aggression'), default=50) // 4
    if not target:
        return score
    target_rank, _armor = _target_priority_value(enemy, target, settings)
    score += min(30, abs(target_rank))
    return score


def _tactic_text(behavior: dict[str, Any]) -> str:
    return ' '.join(str(item or '').lower().replace('-', '_') for item in behavior.get('tactics') or [])


def _environment_candidate(
    enemy: dict[str, Any],
    target: dict[str, Any] | None,
    behavior: dict[str, Any],
    facts: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    if not settings.get('allowEnvironmentalHazards') or not target:
        return None
    intelligence = str(behavior.get('intelligenceProfile') or 'average')
    if intelligence == 'mindless':
        return None
    primary_goal = str(behavior.get('primaryGoal') or '')
    tactic_text = _tactic_text(behavior)
    tactical_level = str(settings.get('tacticalLevel') or 'normal')
    hazards = facts.get('battlefieldHazards') or []
    interactables = facts.get('availableInteractables') or []
    if not hazards and not interactables:
        return None
    uses_terrain = any(word in tactic_text for word in ('use_environment', 'terrain', 'hazard', 'trap', 'lair', 'exploit'))
    objective_uses_field = primary_goal in {'complete_ritual', 'protect_location', 'delay_party', 'capture_target'}
    smart_enough = tactical_level in {'smart', 'brutal'} and intelligence in {'average', 'trained', 'tactical', 'genius', 'alien', 'low_cunning'}
    if not (uses_terrain or objective_uses_field or smart_enough):
        return None
    hazard = hazards[0] if hazards else None
    interactable = interactables[0] if interactables else None
    feature = hazard or interactable or {}
    feature_name = _item_name(feature, 'the battlefield')
    movement_goal = (
        f"maneuver {target.get('name') or 'the target'} toward {feature_name}"
        if hazard
        else f"use {feature_name} to change the fight"
    )
    score = 58 + int_or_default(behavior.get('discipline'), default=50) // 5
    if hazard:
        score += 8
    if objective_uses_field:
        score += 8
    if smart_enough:
        score += 6
    return _candidate(
        _intent(
            enemy,
            'use_environment',
            target=target,
            reason=f"{enemy.get('name')} uses {feature_name} to advance its objective.",
            confidence=0.74,
            movement_goal=movement_goal,
            telegraph=f"{enemy.get('name')} glances toward {feature_name}.",
        ),
        score,
    )


def _cover_candidate(enemy: dict[str, Any], behavior: dict[str, Any], facts: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any] | None:
    cover = next((item for item in facts.get('availableCover') or [] if isinstance(item, dict)), None)
    if not cover:
        return None
    intelligence = str(behavior.get('intelligenceProfile') or 'average')
    if intelligence == 'mindless':
        return None
    position = enemy.get('position') if isinstance(enemy.get('position'), dict) else {}
    if position.get('coverId') and position.get('coverId') == cover.get('id'):
        return None
    tactic_text = _tactic_text(behavior)
    hp = int(facts.get('enemyHpPercent') or 100)
    tactical_level = str(settings.get('tacticalLevel') or 'normal')
    role = str(behavior.get('combatRole') or '')
    wants_cover = (
        'cover' in tactic_text
        or 'hide' in tactic_text
        or 'terrain' in tactic_text
        or role in {'sniper', 'skirmisher', 'support', 'controller'}
        or hp <= 50
        or tactical_level in {'smart', 'brutal'}
    )
    if not wants_cover:
        return None
    cover_name = _item_name(cover, 'cover')
    intent_type = 'hide' if 'hide' in tactic_text or role in {'sniper', 'skirmisher'} else 'defend'
    score = 50 + int_or_default(behavior.get('selfPreservation'), default=50) // 6 + int_or_default(behavior.get('discipline'), default=50) // 8
    if hp <= 50:
        score += 10
    if tactical_level in {'smart', 'brutal'}:
        score += 6
    return _candidate(
        _intent(
            enemy,
            intent_type,
            reason=f"{enemy.get('name')} uses {cover_name} instead of standing exposed.",
            confidence=0.68,
            movement_goal=f"move to {cover_name}",
            telegraph=f"{enemy.get('name')} edges toward {cover_name}.",
        ),
        score,
    )


def _protect_ally_candidate(
    enemy: dict[str, Any],
    target: dict[str, Any] | None,
    behavior: dict[str, Any],
    facts: dict[str, Any],
) -> dict[str, Any] | None:
    if not target:
        return None
    intelligence = str(behavior.get('intelligenceProfile') or 'average')
    if intelligence in {'mindless', 'animal', 'alien'}:
        return None
    allies = [ally for ally in facts.get('allies') or [] if isinstance(ally, dict)]
    if not allies:
        return None
    tactic_text = _tactic_text(behavior)
    role = str(behavior.get('combatRole') or '')
    primary_goal = str(behavior.get('primaryGoal') or '')
    protectable = sorted(
        allies,
        key=lambda ally: (
            0 if (ally.get('behavior') or {}).get('combatRole') in {'leader', 'boss'} or ally.get('kind') == 'boss' else 1,
            _hp_percent(ally),
            str(ally.get('name') or ''),
        ),
    )
    ally = protectable[0]
    ally_role = (ally.get('behavior') or {}).get('combatRole')
    ally_priority = ally_role in {'leader', 'boss', 'support'} or ally.get('kind') == 'boss' or _hp_percent(ally) <= 50
    wants_protect = (
        primary_goal in {'protect_leader', 'protect_location'}
        or role in {'tank', 'support', 'minion', 'leader'}
        or 'protect' in tactic_text
        or int_or_default(behavior.get('loyalty'), default=40) >= 70
    )
    if not (ally_priority and wants_protect):
        return None
    ally_name = ally.get('name') or 'an ally'
    score = 58 + int_or_default(behavior.get('loyalty'), default=40) // 3 + int_or_default(behavior.get('discipline'), default=50) // 6
    return _candidate(
        _intent(
            enemy,
            'protect_ally',
            target=ally,
            reason=f"{enemy.get('name')} moves to protect {ally_name}.",
            confidence=0.72,
            movement_goal=f"interpose near {ally_name} and pressure {target.get('name') or 'the attacker'}",
            telegraph=f"{enemy.get('name')} shifts between {ally_name} and the party.",
        ),
        score,
    )


def _candidate(intent: dict[str, Any], score: int) -> dict[str, Any]:
    return {
        'score': int(score),
        'intentType': intent.get('intentType'),
        'targetId': intent.get('targetId'),
        'abilityId': intent.get('abilityId'),
        'reason': intent.get('reason'),
        'confidence': intent.get('confidence'),
        'intent': intent,
    }


def _intent_from_boss_tactic(enemy: dict[str, Any], tactic: dict[str, Any], source: str) -> dict[str, Any]:
    ability_id = tactic.get('abilityId')
    ability = next((item for item in enemy.get('abilities') or [] if isinstance(item, dict) and item.get('id') == ability_id), None)
    return _intent(
        enemy,
        str(tactic.get('intentType') or 'use_ability'),
        target={'id': tactic.get('targetId')} if tactic.get('targetId') else None,
        ability=ability,
        reason=str(tactic.get('reason') or f"{enemy.get('name')} follows a boss tactic."),
        confidence=float(tactic.get('confidence') or 0.75),
        movement_goal=tactic.get('movementGoal'),
        speech=tactic.get('suggestedSpeech'),
        telegraph=tactic.get('visibleTelegraph'),
    ) | {'tacticSource': source}


def _plan_intent_with_candidates(enemy: dict[str, Any], combat: dict[str, Any], settings: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_settings = settings if isinstance(settings, dict) else {}
    settings = normalize_combat_difficulty_ai(settings)
    for internal_key in ('_sentientSelectorAllowedByBudget',):
        if internal_key in raw_settings:
            settings[internal_key] = raw_settings[internal_key]
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    if flags.get('activeActorId'):
        settings = {**settings, 'activeActorId': str(flags.get('activeActorId'))}
    behavior = enemy.get('behavior') if isinstance(enemy.get('behavior'), dict) else {}
    intelligence = str(behavior.get('intelligenceProfile') or 'average')
    players = _living_participants(combat, 'player')
    target = choose_target(enemy, players, settings)
    facts = build_combat_facts(enemy, combat, settings)
    allowed_target_ids = {str(player.get('id')) for player in reachable_players_for_enemy(enemy, players) if player.get('id')}
    morale = _morale_after_context(enemy, combat)
    hp = _hp_percent(enemy)
    self_preservation = int_or_default(behavior.get('selfPreservation'), default=50)
    survival = behavior.get('survivalRules') if isinstance(behavior.get('survivalRules'), dict) else {}
    fight_to_death = bool(survival.get('fightToDeath'))
    flee_threshold = int_or_default(survival.get('fleeBelowHpPercent', behavior.get('fleeThreshold')), default=25)
    surrender_threshold = int_or_default(survival.get('surrenderBelowMorale', behavior.get('surrenderThreshold')), default=15)
    surrender_hp_threshold = int_or_default(survival.get('surrenderBelowHpPercent'), default=0)
    negotiate_threshold = int_or_default(survival.get('negotiateBelowMorale'), default=surrender_threshold + 10)
    negotiate_hp_threshold = int_or_default(survival.get('negotiateBelowHpPercent'), default=0)
    flee_morale_threshold = int_or_default(survival.get('fleeIfMoraleBelow'), default=0)
    call_help_threshold = int_or_default(survival.get('callForHelpBelowHpPercent'), default=0)
    primary_goal = str(behavior.get('primaryGoal') or 'kill_party')
    candidates: list[dict[str, Any]] = []
    if players and not target:
        candidates.append(
            _candidate(
                _intent(
                    enemy,
                    'reposition',
                    reason=f"{enemy.get('name')} cannot reach any player from its current zone.",
                    confidence=0.82,
                    movement_goal='move toward a reachable line of attack or pressure the nearest zone boundary',
                    telegraph=f"{enemy.get('name')} moves for a better angle instead of striking across separated ground.",
                ),
                90 + int_or_default(behavior.get('discipline'), default=50) // 10,
            )
        )

    outnumbered = _is_outnumbered(combat)
    leader_dead = _leader_dead(combat)
    alone = _is_alone(enemy, combat)
    hp_pressure = hp <= flee_threshold
    morale_pressure = flee_morale_threshold > 0 and morale <= flee_morale_threshold
    outnumbered_pressure = (
        outnumbered
        and survival.get('fleeIfOutnumbered')
        and (hp <= max(35, flee_threshold + 5) or morale <= 35)
    )
    alone_pressure = (
        alone
        and survival.get('fleeIfAlone')
        and (hp <= max(40, flee_threshold + 10) or morale <= 35)
    )
    leader_pressure = leader_dead and survival.get('fleeIfLeaderDies')
    wants_retreat = hp_pressure or morale_pressure or outnumbered_pressure or alone_pressure or leader_pressure
    if settings.get('allowEnemyRetreat') and not fight_to_death and intelligence != 'mindless' and wants_retreat and self_preservation >= 35:
        retreat_score = 55 + max(0, flee_threshold - hp) + self_preservation // 4
        if outnumbered_pressure:
            retreat_score += 12
        if leader_pressure:
            retreat_score += 18
        if alone_pressure:
            retreat_score += 10
        if morale_pressure:
            retreat_score += max(5, flee_morale_threshold - morale)
        candidates.append(
            _candidate(
                _intent(
                    enemy,
                    'retreat',
                    reason=f"{enemy.get('name')} is at {hp}% HP with morale {morale}.",
                    confidence=0.88,
                    movement_goal='nearest safe exit or cover',
                    speech='No fight is worth dying here!' if intelligence not in {'animal', 'alien'} else None,
                    telegraph=f"{enemy.get('name')} looks for a way out.",
                ),
                retreat_score,
            )
        )
    surrender_by_hp = surrender_hp_threshold > 0 and hp <= surrender_hp_threshold
    if settings.get('allowEnemySurrender') and not fight_to_death and intelligence not in {'mindless', 'animal', 'alien'} and (morale <= surrender_threshold or surrender_by_hp):
        candidates.append(
            _candidate(
                _intent(
                    enemy,
                    'surrender',
                    reason=f"{enemy.get('name')} is at {hp}% HP with morale {morale}.",
                    confidence=0.84,
                    speech='Wait! We can make a deal!',
                    telegraph=f"{enemy.get('name')} lowers their weapon and hesitates.",
                ),
                68 + max(0, surrender_threshold - morale) + max(0, surrender_hp_threshold - hp) + self_preservation // 5,
            )
        )
    if (
        not fight_to_death
        and intelligence not in {'mindless', 'animal', 'alien'}
        and primary_goal in {'steal_item', 'negotiate', 'survive'}
        and (morale <= negotiate_threshold or (negotiate_hp_threshold > 0 and hp <= negotiate_hp_threshold))
        and (morale > surrender_threshold or surrender_by_hp is False)
    ):
        candidates.append(
            _candidate(
                _intent(
                    enemy,
                    'negotiate',
                    target=target,
                    reason=f"{enemy.get('name')} still wants the objective but morale has dropped to {morale}.",
                    confidence=0.72,
                    speech='Nobody else has to bleed. Let us talk.',
                    telegraph=f"{enemy.get('name')} shifts from attack posture to bargaining.",
                ),
                72 + max(0, negotiate_threshold - morale) + self_preservation // 8,
            )
        )
    if call_help_threshold > 0 and hp <= call_help_threshold and intelligence not in {'mindless', 'animal'} and morale >= 20:
        call_help_score = 76 + int_or_default(behavior.get('discipline'), default=50) // 8 + max(0, call_help_threshold - hp) // 2
        candidates.append(
            _candidate(
                _intent(
                    enemy,
                    'call_reinforcements',
                    target=target,
                    reason=f"{enemy.get('name')} is hurt enough to seek reinforcements.",
                    confidence=0.7,
                    speech='To me! We need help here!',
                    telegraph=f"{enemy.get('name')} draws breath to shout for aid.",
                ),
                call_help_score,
            )
        )
    use_boss_tactics = should_use_boss_tactics_helper(enemy, combat, settings)
    for situational_candidate in (
        _environment_candidate(enemy, target, behavior, facts, settings),
        _protect_ally_candidate(enemy, target, behavior, facts),
        _cover_candidate(enemy, behavior, facts, settings),
    ):
        if situational_candidate:
            candidates.append(situational_candidate)
    if use_boss_tactics:
        tactic = deterministic_boss_tactic(enemy, combat, settings)
        candidates.append(_candidate(_intent_from_boss_tactic(enemy, tactic, 'deterministic'), 86))
    if primary_goal in {'complete_ritual', 'delay_party', 'steal_item', 'protect_location'} and morale > 20:
        candidates.append(
            _candidate(
                _intent(
                    enemy,
                    'complete_objective' if primary_goal in {'complete_ritual', 'protect_location'} else 'delay' if primary_goal == 'delay_party' else 'retreat',
                    target=target,
                    reason=f"{enemy.get('name')} prioritizes the encounter objective: {primary_goal}.",
                    confidence=0.72,
                    telegraph=f"{enemy.get('name')} keeps attention on the objective rather than simple bloodshed.",
                ),
                70 + int_or_default(behavior.get('discipline'), default=50) // 5,
            )
        )
    if settings.get('allowFocusFire') and target and len(players) > 1:
        focus_score = _score_attack(enemy, target, behavior, settings) + 6
        candidates.append(
            _candidate(
                _intent(
                    enemy,
                    'attack',
                    target=target,
                    ability=_best_ability(enemy, 'attack', round_number=combat.get('round')),
                    reason=f"{enemy.get('name')} focuses the best target based on role and vulnerability.",
                    confidence=0.68,
                    telegraph=f"{enemy.get('name')} tracks {target.get('name') or 'a vulnerable target'}.",
                ),
                focus_score,
            )
        )
    special = _best_ability(enemy, 'use_ability', round_number=combat.get('round'))
    if special and special.get('type') in {'spell', 'special', 'legendary', 'lair'} and morale >= 30:
        candidates.append(
            _candidate(
                _intent(
                    enemy,
                    'use_ability',
                    target=target,
                    ability=special,
                    reason=f"{enemy.get('name')} has a useful ability and enough morale to press the advantage.",
                    confidence=0.76,
                    telegraph=f"{enemy.get('name')} prepares {special.get('name')}.",
                ),
                58 + int_or_default(behavior.get('aggression'), default=50) // 5,
            )
        )
    attack = _best_ability(enemy, 'attack', round_number=combat.get('round'))
    candidates.append(
        _candidate(
            _intent(
                enemy,
                'attack',
                target=target,
                ability=attack,
                reason=f"{enemy.get('name')} attacks the best available target.",
                confidence=0.65,
            ),
            _score_attack(enemy, target, behavior, settings),
        )
    )
    candidates.sort(key=lambda item: item['score'], reverse=True)
    _attach_candidate_contracts(candidates, enemy=enemy, combat=combat)
    _apply_candidate_matcher(candidates, enemy, settings)
    if use_boss_tactics:
        boss_planner, boss_planner_source = _cached_boss_planner(enemy)
        if not boss_planner:
            boss_planner, boss_planner_source = plan_boss_advisory(
                enemy,
                combat,
                settings,
                candidates=candidates,
            )
        _apply_boss_planner_bias(candidates, boss_planner, boss_planner_source)
    if _should_call_freeform_tactics(enemy, settings, candidates, use_boss_tactics=use_boss_tactics):
        deterministic_preview, _deterministic_preview_method = _select_deterministic_candidate(candidates, settings)
        freeform_intent, freeform_source = plan_freeform_enemy_tactic(
            enemy,
            combat,
            settings,
            allowed_target_ids=allowed_target_ids,
            fallback_intent=deterministic_preview.get('intent') if isinstance(deterministic_preview.get('intent'), dict) else {},
            candidates=candidates,
            facts=facts,
        )
        if freeform_intent:
            freeform_score = 80 + round(float(freeform_intent.get('confidence') or 0.7) * 15)
            freeform_intent['selectionMethod'] = 'freeform_tactics_compiler'
            freeform_intent['brainSource'] = freeform_intent.get('brainSource') or freeform_source
            candidates.append(_candidate(freeform_intent, min(96, freeform_score)))
            candidates.sort(key=lambda item: item['score'], reverse=True)
            _attach_candidate_contracts(candidates, enemy=enemy, combat=combat)
            _apply_candidate_matcher(candidates, enemy, settings)
    deterministic_candidate, deterministic_method = _select_deterministic_candidate(candidates, settings)
    selected = deepcopy(deterministic_candidate['intent'])
    selected['selectionScore'] = deterministic_candidate['score']
    if isinstance(selected.get('tacticsCompilation'), dict):
        selected['selectionMethod'] = 'freeform_tactics_compiler'
        selected['deterministicSelectionMethod'] = deterministic_method
    else:
        selected['selectionMethod'] = deterministic_method
    if use_boss_tactics:
        selected, boss_tactic_source = plan_boss_candidate_tactic(
            enemy,
            combat,
            settings,
            fallback_intent=selected,
            candidates=candidates,
        )
        if boss_tactic_source in _NON_HELPER_BOSS_TACTIC_SOURCES:
            selected['tacticSource'] = selected.get('tacticSource') or boss_tactic_source
    should_call_sentient_brain = _should_call_sentient_selector(
        enemy,
        settings,
        candidates,
        selected_intent=selected,
        use_boss_tactics=use_boss_tactics,
    )
    if should_call_sentient_brain:
        selected, brain_source = plan_sentient_enemy_intent(
            enemy,
            combat,
            settings,
            allowed_target_ids=allowed_target_ids,
            fallback_intent=selected,
            candidates=candidates,
        )
        selected['brainSource'] = selected.get('brainSource') or brain_source
    selected = resolve_selected_candidate_for_current_state(selected, candidates, enemy, combat)
    return selected, [_candidate_debug_view(item) for item in candidates]


def plan_intent_for_enemy(enemy: dict[str, Any], combat: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    intent, _candidates = _plan_intent_with_candidates(enemy, combat, settings)
    return intent


def _combat_fact_debug_payload(facts: dict[str, Any]) -> dict[str, Any]:
    return {
        'enemyHpPercent': facts.get('enemyHpPercent'),
        'allyIds': [ally.get('id') for ally in facts.get('allies') or [] if isinstance(ally, dict)],
        'visibleTargetIds': [target.get('id') for target in facts.get('visibleTargets') or [] if isinstance(target, dict)],
        'woundedTargetIds': [target.get('id') for target in facts.get('woundedTargets') or [] if isinstance(target, dict)],
        'spellcasterTargetIds': [target.get('id') for target in facts.get('spellcasterTargets') or [] if isinstance(target, dict)],
        'healerTargetIds': [target.get('id') for target in facts.get('healerTargets') or [] if isinstance(target, dict)],
        'isolatedTargetIds': [target.get('id') for target in facts.get('isolatedTargets') or [] if isinstance(target, dict)],
        'isOutnumbered': facts.get('isOutnumbered'),
        'leaderAlive': facts.get('leaderAlive'),
        'hasEscapeRoute': facts.get('hasEscapeRoute'),
        'canSeeTargets': facts.get('canSeeTargets'),
        'hazardIds': [item.get('id') for item in facts.get('battlefieldHazards') or [] if isinstance(item, dict)],
        'coverIds': [item.get('id') for item in facts.get('availableCover') or [] if isinstance(item, dict)],
        'exitIds': [item.get('id') for item in facts.get('availableExits') or [] if isinstance(item, dict)],
        'interactableIds': [item.get('id') for item in facts.get('availableInteractables') or [] if isinstance(item, dict)],
    }


def plan_enemy_intents(combat_state: dict[str, Any]) -> dict[str, Any]:
    combat = normalize_combat_state(combat_state)
    settings = combat_difficulty_from_state(combat)
    living_enemies = _actionable_participants(combat, 'enemy')
    max_selector_calls = max(0, int_or_default(settings.get('maxLlmCallsPerRound'), default=3))
    selector_budget_candidates = [enemy for enemy in living_enemies if should_use_sentient_enemy_brain(enemy, settings)]
    selector_budget_enemy_ids = {
        str(enemy.get('id'))
        for enemy in selector_budget_candidates[:max_selector_calls]
        if isinstance(enemy, dict) and enemy.get('id')
    }

    def plan_one(enemy: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        enemy_settings = {
            **settings,
            '_sentientSelectorAllowedByBudget': max_selector_calls > 0 and str(enemy.get('id')) in selector_budget_enemy_ids,
        }
        intent, candidates = _plan_intent_with_candidates(enemy, combat, enemy_settings)
        facts = build_combat_facts(enemy, combat, settings)
        return intent, candidates, facts

    def plan_one_in_context(app, enemy: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        if app is not None:
            with app.app_context():
                return plan_one(enemy)
        return plan_one(enemy)

    worker_count = _enemy_intent_max_workers() if _parallel_enemy_intents_enabled() else 1
    if len(living_enemies) <= 1 or worker_count <= 1:
        results = [plan_one(enemy) for enemy in living_enemies]
    else:
        app = current_app._get_current_object() if has_app_context() else None
        max_workers = min(worker_count, len(living_enemies))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='enemy-intent') as pool:
            results = list(pool.map(lambda enemy: plan_one_in_context(app, enemy), living_enemies))

    intents = []
    candidate_debug = {}
    fact_debug = {}
    for enemy, (intent, candidates, facts) in zip(living_enemies, results):
        intents.append(intent)
        if enemy.get('id'):
            candidate_debug[str(enemy['id'])] = candidates
            fact_debug[str(enemy['id'])] = _combat_fact_debug_payload(facts)
    summary_parts = []
    for intent in intents:
        enemy = next((participant for participant in combat.get('participants') or [] if participant.get('id') == intent.get('enemyId')), {})
        summary_parts.append(f"{enemy.get('name', intent.get('enemyId'))}: {intent.get('intentType')} ({intent.get('reason')})")
    return {
        'round': combat.get('round', 1),
        'intents': intents,
        'summaryForDm': ' '.join(summary_parts),
        'difficultyAI': settings,
        'intentCandidates': candidate_debug,
        'combatFactsByEnemy': fact_debug,
        'combatFacts': {
            'livingEnemies': len(living_enemies),
            'livingPlayers': len(_living_participants(combat, 'player')),
            'leaderDead': _leader_dead(combat),
            'outnumbered': _is_outnumbered(combat),
            'battlefieldHazards': len(_battlefield_items(combat, 'hazards')),
            'battlefieldCover': len(_battlefield_items(combat, 'cover')),
            'battlefieldInteractables': len(_battlefield_items(combat, 'interactables')),
            'availableExits': len([exit_item for exit_item in _battlefield_items(combat, 'exits') if not bool(exit_item.get('blocked'))]),
            'activeActorId': str((combat.get('flags') or {}).get('activeActorId') or '') if isinstance(combat.get('flags'), dict) else '',
        },
    }


def attach_intents_to_combat(combat_state: dict[str, Any], intent_plan: dict[str, Any]) -> dict[str, Any]:
    combat = normalize_combat_state(combat_state)
    by_enemy_id = {
        str(intent.get('enemyId')): intent
        for intent in intent_plan.get('intents') or []
        if isinstance(intent, dict) and intent.get('enemyId')
    }
    for participant in combat.get('participants') or []:
        if participant.get('id') in by_enemy_id:
            participant['currentIntent'] = deepcopy(by_enemy_id[participant['id']])
            morale, events = recalculate_morale(participant, combat)
            participant['morale'] = morale
            participant['moraleEvents'] = events
    combat['lastIntentSummary'] = intent_plan.get('summaryForDm')
    return combat
