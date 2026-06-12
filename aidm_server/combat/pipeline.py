from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from aidm_server.combat.difficulty import normalize_combat_difficulty_ai
from aidm_server.combat.end_conditions import check_combat_end, combat_end_change
from aidm_server.combat.intent_planner import attach_intents_to_combat, plan_enemy_intents
from aidm_server.combat.state import (
    combat_summary_for_dm,
    combat_turn_context,
    default_battlefield,
    ensure_combat_state,
    instantiate_creature,
    normalize_combat_state,
    player_combat_participant,
)
from aidm_server.creatures.repository import record_combat_debug_event
from aidm_server.creatures.resolver import default_request_from_session, resolve_creatures_for_encounter
from aidm_server.database import db
from aidm_server.game_state.models import display_actor_id, stable_change_id, stable_slug
from aidm_server.models import Campaign, CombatEncounter, DmTurn, Session, safe_json_dumps


COMBAT_TRIGGER_PATTERN = re.compile(
    r'\b(?:combat|fight|battle|ambush|enemy|enemies|monster|monsters|attack(?:s|ed|ing)?|'
    r'roll initiative|initiative|bandit|goblin|wolf|zombie|skeleton|cultist|guard)\b',
    re.IGNORECASE,
)
DIRECT_HOSTILE_ACTION_PATTERN = re.compile(
    r'\b(?:i|we|[A-Z][A-Za-z0-9\'-]{1,40})\s+'
    r'(?:attack|attacks|stab|stabs|strike|strikes|shoot|shoots|slash|slashes|swing|swings|'
        r'cut|cuts|lunge|lunges|punch|punches|kick|kicks|smite|smites|kill|kills|throw|throws|hurl|hurls|'
        r'smash|smashes|smack|smacks|slam|slams|bash|bashes|crush|crushes|'
        r'grab|grabs|grapple|grapples|slice|slices|stomp|stomps|cripple|cripples|disable|disables)\b|'
    r'\b(?:roll initiative|initiative)\b',
    re.IGNORECASE,
)
DM_COMBAT_START_PATTERN = re.compile(
    r'\b(?:roll initiative|initiative order|combat begins|battle begins|fight begins|'
    r'ambush(?:es|ed)?|attacks? you|lunges? at you|charges? you|arrows?\s+fly|'
    r'blades?\s+drawn|weapons?\s+drawn|draws?\s+(?:a|their|his|her|its)\s+weapon)\b',
    re.IGNORECASE,
)
DM_COMBAT_NEGATION_PATTERN = re.compile(
    r'\b(?:no fighting|no combat|fight is over|combat ends?|battle ends?|surrenders?|yields?|'
    r'no immediate threat|harmless|backs away|lowers? (?:its|their|his|her) weapon)\b',
    re.IGNORECASE,
)


def _living_enemies(combat: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        participant
        for participant in (combat.get('participants') or [])
        if isinstance(participant, dict)
        and participant.get('team') == 'enemy'
        and participant.get('isAlive') is not False
        and (participant.get('hp') or {}).get('current', 1) > 0
    ]


def _combat_is_active(combat: dict[str, Any]) -> bool:
    return str(combat.get('status') or '') in {'starting', 'active'} and bool(_living_enemies(combat))


def _actor_name_by_id(combat: dict[str, Any]) -> dict[str, str]:
    return {
        str(participant.get('id')): str(participant.get('name') or participant.get('id'))
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and participant.get('id')
    }


def _combat_turn_flags(turn_context: dict[str, Any], *, submitted_actor_id: str | None = None) -> dict[str, Any]:
    current_actor = turn_context.get('currentActor') if isinstance(turn_context.get('currentActor'), dict) else {}
    next_actor = turn_context.get('immediateNextActor') if isinstance(turn_context.get('immediateNextActor'), dict) else {}
    handoff_actor = turn_context.get('handoffActor') if isinstance(turn_context.get('handoffActor'), dict) else {}
    enemy_turn_block = [
        actor
        for actor in (turn_context.get('enemyTurnBlock') or [])
        if isinstance(actor, dict)
    ]
    submitted_actor_id = str(submitted_actor_id or '').strip()
    current_actor_id = str(current_actor.get('id') or '').strip()
    off_turn = bool(submitted_actor_id and current_actor_id and submitted_actor_id != current_actor_id)
    return {
        'turnOrderMode': turn_context.get('mode') or 'players_then_enemies',
        'turnOrder': turn_context.get('turnOrderIds') or [],
        'turnOrderText': turn_context.get('turnOrderText') or '',
        'activeActorId': current_actor_id or None,
        'activeActorName': current_actor.get('name'),
        'activeActorTeam': current_actor.get('team'),
        'nextActorId': next_actor.get('id'),
        'nextActorName': next_actor.get('name'),
        'nextActorTeam': next_actor.get('team'),
        'handoffActorId': handoff_actor.get('id'),
        'handoffActorName': handoff_actor.get('name'),
        'handoffActorTeam': handoff_actor.get('team'),
        'enemyTurnBlock': [actor.get('id') for actor in enemy_turn_block],
        'enemyTurnBlockText': ', '.join(str(actor.get('name') or actor.get('id')) for actor in enemy_turn_block),
        'submittedActorId': submitted_actor_id or None,
        'offTurnSubmission': off_turn,
    }


def _sync_combat_turn_context(combat: dict[str, Any], *, submitted_actor_id: str | None = None, active_actor_id: str | None = None) -> dict[str, Any]:
    turn_context = combat_turn_context(combat, active_actor_id=active_actor_id)
    if turn_context.get('turnIndex') is not None:
        combat['turnIndex'] = turn_context['turnIndex']
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    flags.update(_combat_turn_flags(turn_context, submitted_actor_id=submitted_actor_id))
    combat['flags'] = flags
    return turn_context


def _combat_turn_update_change(
    *,
    turn_id: int,
    combat: dict[str, Any],
    turn_context: dict[str, Any],
    reason: str,
) -> dict[str, Any] | None:
    if turn_context.get('turnIndex') is None:
        return None
    return {
        'id': stable_change_id(
            turn_id,
            'combat.turn.prepare',
            turn_context.get('turnIndex'),
            (turn_context.get('currentActor') or {}).get('id') if isinstance(turn_context.get('currentActor'), dict) else None,
        ),
        'turnId': turn_id,
        'type': 'combat.update',
        'round': combat.get('round') or 1,
        'turnIndex': turn_context.get('turnIndex'),
        'flags': _combat_turn_flags(turn_context, submitted_actor_id=(combat.get('flags') or {}).get('submittedActorId') if isinstance(combat.get('flags'), dict) else None),
        'reason': reason,
        'visible': False,
    }


def combat_turn_advance_change(*, state: dict[str, Any], turn: DmTurn, actor_id: str | None = None) -> dict[str, Any] | None:
    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    if not _combat_is_active(combat):
        return None
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    submitted_actor_id = str(flags.get('submittedActorId') or actor_id or display_actor_id(turn.player_id)).strip()
    active_actor_id = str(flags.get('activeActorId') or '').strip()
    if flags.get('offTurnSubmission') and submitted_actor_id and active_actor_id and submitted_actor_id != active_actor_id:
        names = _actor_name_by_id(combat)
        return {
            'id': stable_change_id(turn.turn_id, 'combat.turn.off_turn', submitted_actor_id, active_actor_id),
            'turnId': turn.turn_id,
            'type': 'combat.update',
            'round': combat.get('round') or 1,
            'turnIndex': combat.get('turnIndex') or 0,
            'flags': {
                **_combat_turn_flags(combat_turn_context(combat), submitted_actor_id=submitted_actor_id),
                'offTurnSubmission': False,
                'lastOffTurnActorId': submitted_actor_id,
                'lastOffTurnActorName': names.get(submitted_actor_id),
            },
            'reason': 'Off-turn combat submission did not advance the combat roster.',
            'visible': False,
        }

    current_context = combat_turn_context(combat)
    next_index = current_context.get('nextTurnIndex')
    if next_index is None:
        return None
    order = current_context.get('turnOrder') or []
    handoff_actor = current_context.get('handoffActor') if isinstance(current_context.get('handoffActor'), dict) else {}
    next_context = combat_turn_context(combat, active_actor_id=handoff_actor.get('id'))
    next_round = current_context.get('nextRound') or combat.get('round') or 1
    return {
        'id': stable_change_id(
            turn.turn_id,
            'combat.turn.advance',
            active_actor_id,
            handoff_actor.get('id'),
            next_round,
        ),
        'turnId': turn.turn_id,
        'type': 'combat.update',
        'round': next_round,
        'turnIndex': int(next_index) % max(1, len(order)),
        'flags': {
            **_combat_turn_flags(next_context, submitted_actor_id=None),
            'submittedActorId': None,
            'offTurnSubmission': False,
            'lastResolvedActorId': active_actor_id or (current_context.get('currentActor') or {}).get('id'),
            'lastEnemyTurnBlock': [
                actor.get('id')
                for actor in (current_context.get('enemyTurnBlock') or [])
                if isinstance(actor, dict)
            ],
        },
        'reason': 'Combat roster advanced after the resolved turn.',
        'visible': False,
    }


def _should_start_combat(state: dict[str, Any], player_message: str) -> bool:
    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    if _combat_is_active(combat):
        return False
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    combat_state = str(scene.get('combatState') or '').lower()
    if combat_state in {'pending', 'active'}:
        return bool(DIRECT_HOSTILE_ACTION_PATTERN.search(player_message or ''))
    if combat_state in {'resolved', 'ended'}:
        return bool(DIRECT_HOSTILE_ACTION_PATTERN.search(player_message or ''))
    return bool(DIRECT_HOSTILE_ACTION_PATTERN.search(player_message or ''))


def _should_start_combat_from_dm_response(state: dict[str, Any], dm_response: str) -> bool:
    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    if _combat_is_active(combat):
        return False
    text = dm_response or ''
    if not DM_COMBAT_START_PATTERN.search(text):
        return False
    if DM_COMBAT_NEGATION_PATTERN.search(text):
        return False
    return True


def _participant_position_by_id(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    return {
        str(participant.get('id')): participant.get('position')
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and isinstance(participant.get('position'), dict) and participant.get('id')
    }


def _scene_position_for_actor(state: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    actor_id = str(actor.get('id') or '').strip()
    player_id = str(actor.get('playerId') or '').strip()
    position_sources = [
        scene.get('playerPositions'),
        scene.get('characterPositions'),
        state.get('playerPositions'),
        state.get('characterPositions'),
    ]
    for source in position_sources:
        if not isinstance(source, dict):
            continue
        for key in (actor_id, player_id, actor.get('name')):
            if key is None:
                continue
            raw = source.get(str(key))
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, str) and raw.strip():
                return {'zoneId': raw.strip(), 'rangeBand': 'near'}
    zone_sources = [
        scene.get('playerZones'),
        scene.get('characterZones'),
        state.get('playerZones'),
        state.get('characterZones'),
    ]
    for source in zone_sources:
        if not isinstance(source, dict):
            continue
        for key in (actor_id, player_id, actor.get('name')):
            if key is None:
                continue
            raw = source.get(str(key))
            if isinstance(raw, str) and raw.strip():
                return {'zoneId': raw.strip(), 'rangeBand': 'near'}
    return {'rangeBand': 'near'}


def _player_participants(state: dict[str, Any]) -> list[dict[str, Any]]:
    existing_positions = _participant_position_by_id(state)
    participants: list[dict[str, Any]] = []
    for actor in (state.get('playerCharacters') or []):
        if not isinstance(actor, dict):
            continue
        participant = player_combat_participant(actor)
        participant['position'] = existing_positions.get(participant['id']) or _scene_position_for_actor(state, actor)
        participants.append(participant)
    return participants


def _instantiate_enemy_groups(
    encounter_resolution: dict[str, Any],
    *,
    turn_id: int,
    position: dict[str, Any],
) -> list[dict[str, Any]]:
    enemies: list[dict[str, Any]] = []
    sequence = 1
    for group in encounter_resolution.get('groups') or []:
        if not isinstance(group, dict) or not isinstance(group.get('creature'), dict):
            continue
        creature = group['creature']
        count = max(1, int(group.get('count') or 1))
        for group_index in range(count):
            instance_position = dict(position)
            if group_index > 0 and not instance_position.get('rangeBand'):
                instance_position['rangeBand'] = 'near'
            enemies.append(
                instantiate_creature(
                    creature,
                    instance_id=f"enemy_{stable_slug(creature.get('name'))}_{turn_id}_{sequence}",
                    team='enemy',
                    position=instance_position,
                    current_turn=turn_id,
                )
            )
            sequence += 1
    return enemies


def _encounter_flag_summary(encounter_resolution: dict[str, Any]) -> dict[str, Any]:
    groups = [
        {
            'label': group.get('label'),
            'count': group.get('count'),
            'creatureId': (group.get('creature') or {}).get('id') if isinstance(group.get('creature'), dict) else None,
            'name': (group.get('creature') or {}).get('name') if isinstance(group.get('creature'), dict) else None,
            'creatureTypeName': (group.get('creature') or {}).get('creatureTypeName') if isinstance(group.get('creature'), dict) else None,
            'npcBinding': (group.get('creature') or {}).get('npcBinding') if isinstance(group.get('creature'), dict) else None,
            'source': group.get('source'),
            'resolutionMethod': group.get('resolutionMethod'),
        }
        for group in (encounter_resolution.get('groups') or [])
        if isinstance(group, dict)
    ]
    return {
        'resolverMethod': encounter_resolution.get('resolutionMethod'),
        'creatureSource': ', '.join(encounter_resolution.get('sources') or []),
        'enemyCount': encounter_resolution.get('totalEnemies'),
        'enemyGroups': groups,
    }


def _ensure_encounter_record(
    *,
    session_obj: Session,
    campaign: Campaign,
    combat: dict[str, Any],
) -> CombatEncounter:
    encounter = (
        CombatEncounter.query.filter_by(session_id=session_obj.session_id)
        .filter(CombatEncounter.status.in_(['starting', 'active']))
        .order_by(CombatEncounter.updated_at.desc())
        .first()
    )
    participant_ids = [participant.get('id') for participant in combat.get('participants') or [] if isinstance(participant, dict)]
    if encounter:
        encounter.status = combat.get('status') or encounter.status
        encounter.round = int(combat.get('round') or encounter.round or 1)
        encounter.encounter_goal_json = safe_json_dumps(combat.get('encounterGoal') or {}, {})
        encounter.battlefield_json = safe_json_dumps(combat.get('battlefield') or {}, {})
        encounter.participant_ids_json = safe_json_dumps(participant_ids, [])
        db.session.flush()
        return encounter
    encounter = CombatEncounter(
        session_id=session_obj.session_id,
        campaign_id=campaign.campaign_id,
        status=combat.get('status') or 'active',
        round=int(combat.get('round') or 1),
        encounter_goal_json=safe_json_dumps(combat.get('encounterGoal') or {}, {}),
        battlefield_json=safe_json_dumps(combat.get('battlefield') or {}, {}),
        participant_ids_json=safe_json_dumps(participant_ids, []),
    )
    db.session.add(encounter)
    db.session.flush()
    return encounter


def _intent_changes(turn_id: int, combat: dict[str, Any], intent_plan: dict[str, Any]) -> list[dict[str, Any]]:
    changes = []
    morale_by_enemy = {
        participant.get('id'): participant.get('morale')
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and participant.get('team') == 'enemy'
    }
    for intent in intent_plan.get('intents') or []:
        if not isinstance(intent, dict) or not intent.get('enemyId'):
            continue
        enemy_id = str(intent['enemyId'])
        changes.append(
            {
                'id': stable_change_id(turn_id, 'combat.intent', enemy_id, intent.get('intentType'), intent.get('targetId')),
                'turnId': turn_id,
                'type': 'combat.intent.set',
                'participantId': enemy_id,
                'intent': intent,
                'reason': intent.get('reason'),
                'visible': False,
            }
        )
        if morale_by_enemy.get(enemy_id) is not None:
            changes.append(
                {
                    'id': stable_change_id(turn_id, 'combat.morale', enemy_id, morale_by_enemy.get(enemy_id)),
                    'turnId': turn_id,
                    'type': 'combat.morale.update',
                    'participantId': enemy_id,
                    'morale': morale_by_enemy[enemy_id],
                    'reason': 'Enemy morale recalculated before narration.',
                    'visible': False,
                }
            )
    return changes


def prepare_combat_for_turn(
    *,
    state: dict[str, Any],
    session_obj: Session,
    campaign: Campaign,
    turn: DmTurn,
    player_message: str,
    workspace_id: str,
) -> dict[str, Any]:
    working_state = deepcopy(state)
    combat = ensure_combat_state(working_state)
    submitted_actor_id = display_actor_id(turn.player_id)
    debug: dict[str, Any] = {
        'triggered': False,
        'resolver': None,
        'intentPlan': None,
        'combatSummary': None,
        'combatEncounterId': None,
        'turnContext': None,
    }

    if str(combat.get('status') or '') in {'starting', 'active'}:
        turn_context = _sync_combat_turn_context(combat, submitted_actor_id=submitted_actor_id)
        debug['turnContext'] = turn_context
        end_reason = check_combat_end(combat)
        if end_reason:
            debug['combatEndReason'] = end_reason
            debug['combatSummary'] = combat_summary_for_dm(combat)
            return {
                'changes': [combat_end_change(turn.turn_id, end_reason)],
                'debug': debug,
                'combatContext': debug['combatSummary'],
            }

    if _combat_is_active(combat):
        intent_plan = plan_enemy_intents(combat)
        combat_with_intents = attach_intents_to_combat(combat, intent_plan)
        debug['intentPlan'] = intent_plan
        debug['combatSummary'] = combat_summary_for_dm(combat_with_intents)
        encounter = _ensure_encounter_record(session_obj=session_obj, campaign=campaign, combat=combat_with_intents)
        debug['combatEncounterId'] = encounter.combat_encounter_id
        turn_update_change = _combat_turn_update_change(
            turn_id=turn.turn_id,
            combat=combat,
            turn_context=debug['turnContext'] if isinstance(debug.get('turnContext'), dict) else combat_turn_context(combat),
            reason='Combat roster prepared before narration.',
        )
        changes = _intent_changes(turn.turn_id, combat_with_intents, intent_plan)
        if turn_update_change:
            changes = [turn_update_change, *changes]
        return {
            'changes': changes,
            'debug': debug,
            'combatContext': debug['combatSummary'],
        }

    if not _should_start_combat(working_state, player_message):
        return {'changes': [], 'debug': debug, 'combatContext': None}

    debug['triggered'] = True
    request = default_request_from_session(
        session_obj=session_obj,
        campaign=campaign,
        state=working_state,
        player_message=player_message,
    )
    encounter_resolution = resolve_creatures_for_encounter(request, workspace_id=workspace_id)
    actor_record = next(
        (
            actor
            for actor in (working_state.get('playerCharacters') or [])
            if isinstance(actor, dict) and str(actor.get('id') or '') == submitted_actor_id
        ),
        {},
    )
    enemy_position = _scene_position_for_actor(working_state, actor_record) if actor_record else {'rangeBand': 'near'}
    enemies = _instantiate_enemy_groups(encounter_resolution, turn_id=turn.turn_id, position=enemy_position)
    participants = [*_player_participants(working_state), *enemies]
    scene = working_state.get('currentScene') if isinstance(working_state.get('currentScene'), dict) else {}
    encounter_flags = _encounter_flag_summary(encounter_resolution)
    combat_payload = {
        'status': 'active',
        'round': 1,
        'turnIndex': 0,
        'participants': participants,
        'battlefield': default_battlefield(scene),
        'encounterGoal': encounter_resolution.get('encounterGoal'),
        'initiative': [],
        'flags': {
            **encounter_flags,
            'combatStartedBy': 'player_hostile_action',
            'initiativeRequired': True,
            'combatDifficultyAI': normalize_combat_difficulty_ai((working_state.get('settings') or {}).get('combatDifficultyAI') if isinstance(working_state.get('settings'), dict) else None),
        },
    }
    _sync_combat_turn_context(combat_payload, submitted_actor_id=submitted_actor_id, active_actor_id=submitted_actor_id)
    intent_plan = plan_enemy_intents(combat_payload)
    combat_payload = attach_intents_to_combat(combat_payload, intent_plan)
    encounter = _ensure_encounter_record(session_obj=session_obj, campaign=campaign, combat=combat_payload)
    debug.update(
        {
            'resolver': encounter_resolution,
            'intentPlan': intent_plan,
            'combatSummary': combat_summary_for_dm(combat_payload),
            'combatEncounterId': encounter.combat_encounter_id,
        }
    )
    changes = [
        {
            'id': stable_change_id(turn.turn_id, 'combat.start', encounter_flags.get('enemyCount'), encounter_flags.get('resolverMethod')),
            'turnId': turn.turn_id,
            'type': 'combat.start',
            'combat': combat_payload,
            'reason': f"Combat started with {encounter_flags.get('enemyCount') or len(enemies)} enemy participant(s).",
            'visible': False,
        }
    ]
    return {'changes': changes, 'debug': debug, 'combatContext': debug['combatSummary']}


def prepare_combat_from_dm_response(
    *,
    state: dict[str, Any],
    session_obj: Session,
    campaign: Campaign,
    turn: DmTurn,
    player_message: str,
    dm_response: str,
    workspace_id: str,
) -> dict[str, Any]:
    working_state = deepcopy(state)
    combat = ensure_combat_state(working_state)
    submitted_actor_id = display_actor_id(turn.player_id)
    debug: dict[str, Any] = {
        'triggered': False,
        'resolver': None,
        'intentPlan': None,
        'combatSummary': None,
        'combatEncounterId': None,
        'adjudicationSource': 'post_dm_response',
        'turnContext': None,
    }
    if not _should_start_combat_from_dm_response(working_state, dm_response):
        return {'changes': [], 'debug': debug, 'combatContext': None}

    debug['triggered'] = True
    request = default_request_from_session(
        session_obj=session_obj,
        campaign=campaign,
        state=working_state,
        player_message=f"{player_message}\n\nDM response: {dm_response}",
    )
    encounter_resolution = resolve_creatures_for_encounter(request, workspace_id=workspace_id)
    actor_record = next(
        (
            actor
            for actor in (working_state.get('playerCharacters') or [])
            if isinstance(actor, dict) and str(actor.get('id') or '') == submitted_actor_id
        ),
        {},
    )
    enemy_position = _scene_position_for_actor(working_state, actor_record) if actor_record else {'rangeBand': 'near'}
    enemies = _instantiate_enemy_groups(encounter_resolution, turn_id=turn.turn_id, position=enemy_position)
    participants = [*_player_participants(working_state), *enemies]
    scene = working_state.get('currentScene') if isinstance(working_state.get('currentScene'), dict) else {}
    encounter_flags = _encounter_flag_summary(encounter_resolution)
    combat_payload = {
        'status': 'active',
        'round': 1,
        'turnIndex': 0,
        'participants': participants,
        'battlefield': default_battlefield(scene),
        'encounterGoal': encounter_resolution.get('encounterGoal'),
        'initiative': [],
        'flags': {
            **encounter_flags,
            'combatStartedBy': 'post_dm_adjudicator',
            'initiativeRequired': bool(re.search(r'\binitiative\b', dm_response or '', re.IGNORECASE)),
            'combatDifficultyAI': normalize_combat_difficulty_ai((working_state.get('settings') or {}).get('combatDifficultyAI') if isinstance(working_state.get('settings'), dict) else None),
        },
    }
    debug['turnContext'] = _sync_combat_turn_context(combat_payload, submitted_actor_id=submitted_actor_id, active_actor_id=submitted_actor_id)
    intent_plan = plan_enemy_intents(combat_payload)
    combat_payload = attach_intents_to_combat(combat_payload, intent_plan)
    encounter = _ensure_encounter_record(session_obj=session_obj, campaign=campaign, combat=combat_payload)
    debug.update(
        {
            'resolver': encounter_resolution,
            'intentPlan': intent_plan,
            'combatSummary': combat_summary_for_dm(combat_payload),
            'combatEncounterId': encounter.combat_encounter_id,
        }
    )
    changes = [
        {
            'id': stable_change_id(turn.turn_id, 'combat.start.post_dm', encounter_flags.get('enemyCount'), encounter_flags.get('resolverMethod')),
            'turnId': turn.turn_id,
            'type': 'combat.start',
            'combat': combat_payload,
            'reason': f"Combat started from DM response with {encounter_flags.get('enemyCount') or len(enemies)} enemy participant(s).",
            'visible': False,
        }
    ]
    return {'changes': changes, 'debug': debug, 'combatContext': debug['combatSummary']}


def record_combat_debug_from_prepare(
    *,
    session_obj: Session,
    campaign: Campaign,
    turn: DmTurn,
    prepare_result: dict[str, Any],
) -> None:
    debug = prepare_result.get('debug') if isinstance(prepare_result.get('debug'), dict) else {}
    if not debug.get('triggered') and not debug.get('intentPlan'):
        return
    record_combat_debug_event(
        session_id=session_obj.session_id,
        campaign_id=campaign.campaign_id,
        turn_id=turn.turn_id,
        combat_encounter_id=debug.get('combatEncounterId'),
        event_type='pre_dm_combat_plan',
        payload=debug,
    )


def _combat_change_debug(change: dict[str, Any]) -> dict[str, Any]:
    summary = {
        'id': change.get('id'),
        'type': change.get('type'),
        'participantId': change.get('participantId') or change.get('enemyId'),
        'participantName': change.get('participantName') or change.get('name'),
        'combatStatus': change.get('combatStatus') or change.get('status'),
        'reason': change.get('reason'),
    }
    for key in ('hp', 'conditions', 'isAlive', 'isConscious', 'condition', 'toRangeBand', 'round'):
        if key in change:
            summary[key] = change.get(key)
    return {key: value for key, value in summary.items() if value not in (None, '', [], {})}


def _validation_combat_rejections(validation: dict[str, Any]) -> list[dict[str, Any]]:
    rejected = []
    for item in validation.get('rejected') or []:
        if not isinstance(item, dict):
            continue
        change = item.get('change') if isinstance(item.get('change'), dict) else {}
        if not str(change.get('type') or '').startswith('combat.'):
            continue
        rejected.append(
            {
                'change': _combat_change_debug(change),
                'reason': item.get('reason'),
            }
        )
    return rejected


def record_combat_debug_from_outcome(
    *,
    session_obj: Session,
    campaign: Campaign,
    turn: DmTurn,
    prepare_result: dict[str, Any],
    post_validation: dict[str, Any],
    applied_changes: list[dict[str, Any]],
    state_log: dict[str, Any],
) -> None:
    debug = prepare_result.get('debug') if isinstance(prepare_result.get('debug'), dict) else {}
    applied_combat = [
        _combat_change_debug(change)
        for change in applied_changes
        if isinstance(change, dict) and str(change.get('type') or '').startswith('combat.')
    ]
    rejected_combat = _validation_combat_rejections(post_validation)
    if not debug.get('triggered') and not debug.get('intentPlan') and not applied_combat and not rejected_combat:
        return
    payload = {
        'phase': 'post_dm_outcome',
        'adjudicationSource': debug.get('adjudicationSource') or 'post_dm_pipeline',
        'combatDebug': debug,
        'appliedCombatChanges': applied_combat,
        'rejectedCombatChanges': rejected_combat,
        'validationCounts': {
            'accepted': len(post_validation.get('accepted') or []),
            'modified': len(post_validation.get('modified') or []),
            'rejected': len(post_validation.get('rejected') or []),
        },
        'stateLog': state_log,
    }
    record_combat_debug_event(
        session_id=session_obj.session_id,
        campaign_id=campaign.campaign_id,
        turn_id=turn.turn_id,
        combat_encounter_id=debug.get('combatEncounterId'),
        event_type='post_dm_combat_outcome',
        payload=payload,
    )
