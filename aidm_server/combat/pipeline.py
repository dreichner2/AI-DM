from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from aidm_server.combat.difficulty import normalize_combat_difficulty_ai
from aidm_server.combat.end_conditions import check_combat_end, combat_end_change
from aidm_server.combat.intent_planner import attach_intents_to_combat, plan_enemy_intents
from aidm_server.combat.state import (
    combat_summary_for_dm,
    default_battlefield,
    ensure_combat_state,
    instantiate_creature,
    normalize_combat_state,
    player_combat_participant,
)
from aidm_server.creatures.repository import record_combat_debug_event
from aidm_server.creatures.resolver import default_request_from_session, resolve_creature_for_encounter
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
    r'cast|casts|charge|charges|punch|punches|kick|kicks|smite|smites|kill|kills)\b|'
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


def _should_start_combat(state: dict[str, Any], player_message: str) -> bool:
    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    if _combat_is_active(combat):
        return False
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    combat_state = str(scene.get('combatState') or '').lower()
    if combat_state in {'pending', 'active'}:
        return True
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
    active_actor_id = display_actor_id(turn.player_id)
    combat_flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    combat_flags['activeActorId'] = active_actor_id
    combat['flags'] = combat_flags
    debug: dict[str, Any] = {
        'triggered': False,
        'resolver': None,
        'intentPlan': None,
        'combatSummary': None,
        'combatEncounterId': None,
    }

    if str(combat.get('status') or '') in {'starting', 'active'}:
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
        return {
            'changes': _intent_changes(turn.turn_id, combat_with_intents, intent_plan),
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
    resolution = resolve_creature_for_encounter(request, workspace_id=workspace_id)
    creature = resolution['creature']
    enemy_instance_id = f"enemy_{stable_slug(creature.get('name'))}_{turn.turn_id}"
    actor_record = next(
        (
            actor
            for actor in (working_state.get('playerCharacters') or [])
            if isinstance(actor, dict) and str(actor.get('id') or '') == active_actor_id
        ),
        {},
    )
    enemy_position = _scene_position_for_actor(working_state, actor_record) if actor_record else {'rangeBand': 'near'}
    enemy = instantiate_creature(
        creature,
        instance_id=enemy_instance_id,
        team='enemy',
        position=enemy_position,
        current_turn=turn.turn_id,
    )
    participants = [*_player_participants(working_state), enemy]
    scene = working_state.get('currentScene') if isinstance(working_state.get('currentScene'), dict) else {}
    combat_payload = {
        'status': 'active',
        'round': 1,
        'turnIndex': 0,
        'participants': participants,
        'battlefield': default_battlefield(scene),
        'encounterGoal': {
            'type': 'kill_all_enemies' if request.get('encounterPurpose') in {'ambush', 'random_encounter'} else 'custom',
            'description': resolution['notes'][0] if resolution.get('notes') else 'Survive and resolve the immediate threat.',
            'enemyObjective': (creature.get('behavior') or {}).get('primaryGoal'),
            'playerObjective': 'Survive, protect allies, and resolve the threat.',
            'successConditions': ['Enemies are defeated, flee, surrender, negotiate, or their objective is stopped.'],
            'failureConditions': ['Enemies achieve their objective or incapacitate the party.'],
        },
        'initiative': [],
        'flags': {
            'resolverMethod': resolution.get('resolutionMethod'),
            'creatureSource': resolution.get('source'),
            'activeActorId': active_actor_id,
            'combatStartedBy': 'player_hostile_action',
            'initiativeRequired': True,
            'combatDifficultyAI': normalize_combat_difficulty_ai((working_state.get('settings') or {}).get('combatDifficultyAI') if isinstance(working_state.get('settings'), dict) else None),
        },
    }
    intent_plan = plan_enemy_intents(combat_payload)
    combat_payload = attach_intents_to_combat(combat_payload, intent_plan)
    encounter = _ensure_encounter_record(session_obj=session_obj, campaign=campaign, combat=combat_payload)
    debug.update(
        {
            'resolver': resolution,
            'intentPlan': intent_plan,
            'combatSummary': combat_summary_for_dm(combat_payload),
            'combatEncounterId': encounter.combat_encounter_id,
        }
    )
    changes = [
        {
            'id': stable_change_id(turn.turn_id, 'combat.start', enemy_instance_id),
            'turnId': turn.turn_id,
            'type': 'combat.start',
            'combat': combat_payload,
            'reason': f"Combat started with {creature.get('name')}.",
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
    active_actor_id = display_actor_id(turn.player_id)
    combat_flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    combat_flags['activeActorId'] = active_actor_id
    combat['flags'] = combat_flags
    debug: dict[str, Any] = {
        'triggered': False,
        'resolver': None,
        'intentPlan': None,
        'combatSummary': None,
        'combatEncounterId': None,
        'adjudicationSource': 'post_dm_response',
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
    resolution = resolve_creature_for_encounter(request, workspace_id=workspace_id)
    creature = resolution['creature']
    enemy_instance_id = f"enemy_{stable_slug(creature.get('name'))}_{turn.turn_id}"
    actor_record = next(
        (
            actor
            for actor in (working_state.get('playerCharacters') or [])
            if isinstance(actor, dict) and str(actor.get('id') or '') == active_actor_id
        ),
        {},
    )
    enemy_position = _scene_position_for_actor(working_state, actor_record) if actor_record else {'rangeBand': 'near'}
    enemy = instantiate_creature(
        creature,
        instance_id=enemy_instance_id,
        team='enemy',
        position=enemy_position,
        current_turn=turn.turn_id,
    )
    participants = [*_player_participants(working_state), enemy]
    scene = working_state.get('currentScene') if isinstance(working_state.get('currentScene'), dict) else {}
    combat_payload = {
        'status': 'active',
        'round': 1,
        'turnIndex': 0,
        'participants': participants,
        'battlefield': default_battlefield(scene),
        'encounterGoal': {
            'type': 'custom',
            'description': 'Resolve the immediate hostile action described by the DM.',
            'enemyObjective': (creature.get('behavior') or {}).get('primaryGoal'),
            'playerObjective': 'Survive, protect allies, and resolve the threat.',
            'successConditions': ['Enemies are defeated, flee, surrender, negotiate, or their objective is stopped.'],
            'failureConditions': ['Enemies achieve their objective or incapacitate the party.'],
        },
        'initiative': [],
        'flags': {
            'resolverMethod': resolution.get('resolutionMethod'),
            'creatureSource': resolution.get('source'),
            'activeActorId': active_actor_id,
            'combatStartedBy': 'post_dm_adjudicator',
            'initiativeRequired': bool(re.search(r'\binitiative\b', dm_response or '', re.IGNORECASE)),
            'combatDifficultyAI': normalize_combat_difficulty_ai((working_state.get('settings') or {}).get('combatDifficultyAI') if isinstance(working_state.get('settings'), dict) else None),
        },
    }
    intent_plan = plan_enemy_intents(combat_payload)
    combat_payload = attach_intents_to_combat(combat_payload, intent_plan)
    encounter = _ensure_encounter_record(session_obj=session_obj, campaign=campaign, combat=combat_payload)
    debug.update(
        {
            'resolver': resolution,
            'intentPlan': intent_plan,
            'combatSummary': combat_summary_for_dm(combat_payload),
            'combatEncounterId': encounter.combat_encounter_id,
        }
    )
    changes = [
        {
            'id': stable_change_id(turn.turn_id, 'combat.start.post_dm', enemy_instance_id),
            'turnId': turn.turn_id,
            'type': 'combat.start',
            'combat': combat_payload,
            'reason': f"Combat started from DM response with {creature.get('name')}.",
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
