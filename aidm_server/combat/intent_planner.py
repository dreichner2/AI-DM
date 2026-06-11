from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.combat.boss_tactics import plan_boss_tactic, should_use_boss_tactics_helper
from aidm_server.combat.difficulty import combat_difficulty_from_state, normalize_combat_difficulty_ai
from aidm_server.combat.enemy_brain import plan_sentient_enemy_intent, should_use_sentient_enemy_brain
from aidm_server.combat.morale import living_participants, recalculate_morale
from aidm_server.canon_text import int_or_default
from aidm_server.combat.state import normalize_combat_state


def _hp_percent(participant: dict[str, Any]) -> int:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    current = max(0, int_or_default(hp.get('current'), default=0))
    maximum = max(1, int_or_default(hp.get('max'), default=1))
    return round((current / maximum) * 100)


def _living_participants(combat: dict[str, Any], team: str | None = None) -> list[dict[str, Any]]:
    return living_participants(combat, team)


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


def choose_target(enemy: dict[str, Any], players: list[dict[str, Any]], settings: dict[str, Any] | None = None) -> dict[str, Any] | None:
    reachable_players = reachable_players_for_enemy(enemy, players)
    if not reachable_players:
        return None
    raw_settings = settings if isinstance(settings, dict) else {}
    settings = normalize_combat_difficulty_ai(raw_settings)
    if raw_settings.get('activeActorId'):
        settings['activeActorId'] = str(raw_settings.get('activeActorId'))
    return sorted(reachable_players, key=lambda target: _target_priority_value(enemy, target, settings))[0]


def _best_ability(enemy: dict[str, Any], intent_type: str = 'attack') -> dict[str, Any] | None:
    abilities = [ability for ability in (enemy.get('abilities') or []) if isinstance(ability, dict)]
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
    settings = normalize_combat_difficulty_ai(settings)
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    if flags.get('activeActorId'):
        settings = {**settings, 'activeActorId': str(flags.get('activeActorId'))}
    behavior = enemy.get('behavior') if isinstance(enemy.get('behavior'), dict) else {}
    intelligence = str(behavior.get('intelligenceProfile') or 'average')
    players = _living_participants(combat, 'player')
    target = choose_target(enemy, players, settings)
    allowed_target_ids = {str(player.get('id')) for player in reachable_players_for_enemy(enemy, players) if player.get('id')}
    morale = _morale_after_context(enemy, combat)
    hp = _hp_percent(enemy)
    self_preservation = int_or_default(behavior.get('selfPreservation'), default=50)
    survival = behavior.get('survivalRules') if isinstance(behavior.get('survivalRules'), dict) else {}
    fight_to_death = bool(survival.get('fightToDeath'))
    flee_threshold = int_or_default(survival.get('fleeBelowHpPercent', behavior.get('fleeThreshold')), default=25)
    surrender_threshold = int_or_default(survival.get('surrenderBelowMorale', behavior.get('surrenderThreshold')), default=15)
    negotiate_threshold = int_or_default(survival.get('negotiateBelowMorale'), default=surrender_threshold + 10)
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

    if settings.get('allowEnemyRetreat') and not fight_to_death and intelligence != 'mindless' and hp <= flee_threshold and self_preservation >= 45:
        retreat_score = 55 + max(0, flee_threshold - hp) + self_preservation // 4
        if _is_outnumbered(combat):
            retreat_score += 12
        if _leader_dead(combat):
            retreat_score += 18
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
    if settings.get('allowEnemySurrender') and not fight_to_death and intelligence not in {'mindless', 'animal', 'alien'} and morale <= surrender_threshold:
        candidates.append(
            _candidate(
                _intent(
                    enemy,
                    'surrender',
                    reason=f"{enemy.get('name')} morale has collapsed to {morale}.",
                    confidence=0.84,
                    speech='Wait! We can make a deal!',
                    telegraph=f"{enemy.get('name')} lowers their weapon and hesitates.",
                ),
                68 + max(0, surrender_threshold - morale) + self_preservation // 5,
            )
        )
    if (
        not fight_to_death
        and intelligence not in {'mindless', 'animal', 'alien'}
        and primary_goal in {'steal_item', 'negotiate', 'survive'}
        and morale <= negotiate_threshold
        and morale > surrender_threshold
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
    if should_use_boss_tactics_helper(enemy, combat, settings):
        tactic, tactic_source = plan_boss_tactic(enemy, combat, settings)
        candidates.append(_candidate(_intent_from_boss_tactic(enemy, tactic, tactic_source), 86))
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
                    ability=_best_ability(enemy, 'attack'),
                    reason=f"{enemy.get('name')} focuses the best target based on role and vulnerability.",
                    confidence=0.68,
                    telegraph=f"{enemy.get('name')} tracks {target.get('name') or 'a vulnerable target'}.",
                ),
                focus_score,
            )
        )
    special = _best_ability(enemy, 'use_ability')
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
    attack = _best_ability(enemy, 'attack')
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
    selected = deepcopy(candidates[0]['intent'])
    selected['selectionScore'] = candidates[0]['score']
    selected['selectionMethod'] = 'deterministic_scoring'
    if should_use_sentient_enemy_brain(enemy, settings):
        selected, brain_source = plan_sentient_enemy_intent(
            enemy,
            combat,
            settings,
            allowed_target_ids=allowed_target_ids,
            fallback_intent=selected,
            candidates=[{key: value for key, value in item.items() if key != 'intent'} for item in candidates],
        )
        selected['brainSource'] = selected.get('brainSource') or brain_source
    return selected, [{key: value for key, value in item.items() if key != 'intent'} for item in candidates]


def plan_intent_for_enemy(enemy: dict[str, Any], combat: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    intent, _candidates = _plan_intent_with_candidates(enemy, combat, settings)
    return intent


def plan_enemy_intents(combat_state: dict[str, Any]) -> dict[str, Any]:
    combat = normalize_combat_state(combat_state)
    settings = combat_difficulty_from_state(combat)
    intents = []
    candidate_debug = {}
    for enemy in _living_participants(combat, 'enemy'):
        intent, candidates = _plan_intent_with_candidates(enemy, combat, settings)
        intents.append(intent)
        if enemy.get('id'):
            candidate_debug[str(enemy['id'])] = candidates
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
        'combatFacts': {
            'livingEnemies': len(_living_participants(combat, 'enemy')),
            'livingPlayers': len(_living_participants(combat, 'player')),
            'leaderDead': _leader_dead(combat),
            'outnumbered': _is_outnumbered(combat),
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
