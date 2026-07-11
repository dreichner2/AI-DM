from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Callable

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
from aidm_server.creatures.resolver import (
    CreatureResolutionPlan,
    default_request_from_session,
    persist_creature_resolution_plan,
    plan_creatures_for_encounter,
    resolve_creatures_for_encounter,  # noqa: F401 - compatibility monkeypatch seam
)
from aidm_server.database import db
from aidm_server.game_state.campaign_pack_encounters import materialize_campaign_pack_combat_start
from aidm_server.game_state.models import display_actor_id, stable_change_id, stable_slug
from aidm_server.models import Campaign, CombatEncounter, DmTurn, Session, safe_json_dumps
from aidm_server.provider_priority import foreground_provider_reservation
from aidm_server.time_utils import utc_now


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


CombatOrmReloader = Callable[
    [int, int, int],
    tuple[Session | None, Campaign | None, DmTurn | None],
]


@dataclass(frozen=True)
class _IntentPlanningBoundaryResult:
    intent_plan: dict[str, Any] | None
    session_obj: Session
    campaign: Campaign
    turn: DmTurn
    session_release_attempted: bool = False
    session_released: bool = False
    deterministic_fallback_used: bool = False
    stale: bool = False
    error: str | None = None


@dataclass(frozen=True)
class _CreaturePlanningBoundaryResult:
    resolution_plan: CreatureResolutionPlan | None
    session_obj: Session
    campaign: Campaign
    turn: DmTurn
    session_release_attempted: bool = False
    session_released: bool = False
    stale: bool = False
    error: str | None = None


@dataclass
class CombatPrepareFinalization:
    """Database writes deferred until an exact prepared change is applied."""

    change_ids: tuple[str, ...]
    resolution_plan: CreatureResolutionPlan | None = None
    finalized: bool = False
    combat_encounter_id: int | None = None


_COMBAT_FINALIZATION_KEY = '_combatPrepareFinalization'


def _defer_combat_prepare_finalization(
    prepare_result: dict[str, Any],
    *,
    resolution_plan: CreatureResolutionPlan | None = None,
) -> dict[str, Any]:
    change_ids = tuple(
        str(change.get('id') or '').strip()
        for change in (prepare_result.get('changes') or [])
        if isinstance(change, dict) and str(change.get('id') or '').strip()
    )
    if change_ids:
        prepare_result[_COMBAT_FINALIZATION_KEY] = CombatPrepareFinalization(
            change_ids=change_ids,
            resolution_plan=resolution_plan,
        )
    return prepare_result


def finalize_combat_prepare(
    *,
    session_obj: Session,
    campaign: Campaign,
    prepare_result: dict[str, Any],
    applied_changes: list[dict[str, Any]],
    final_state: dict[str, Any],
) -> CombatEncounter | None:
    """Persist a prepared combat only after its exact change survived validation.

    The caller owns the surrounding transaction. Any bestiary flush, encounter
    synchronization, snapshot persistence, debug event, and audit row therefore
    commit or roll back together.
    """

    finalization = prepare_result.get(_COMBAT_FINALIZATION_KEY)
    if not isinstance(finalization, CombatPrepareFinalization):
        return None
    if finalization.finalized:
        if finalization.combat_encounter_id is None:
            return None
        return db.session.get(CombatEncounter, finalization.combat_encounter_id)

    applied_ids = {
        str(change.get('id') or '').strip()
        for change in applied_changes
        if isinstance(change, dict) and str(change.get('id') or '').strip()
    }
    if not applied_ids.intersection(finalization.change_ids):
        return None

    if finalization.resolution_plan is not None:
        persisted_resolution = persist_creature_resolution_plan(
            finalization.resolution_plan
        )
        debug = prepare_result.get('debug')
        if isinstance(debug, dict):
            debug['resolver'] = persisted_resolution

    combat = final_state.get('combat') if isinstance(final_state.get('combat'), dict) else {}
    encounter = sync_combat_encounter_record(
        session_obj=session_obj,
        campaign=campaign,
        combat=combat,
    )
    finalization.finalized = True
    if encounter is not None:
        finalization.combat_encounter_id = encounter.combat_encounter_id
        debug = prepare_result.get('debug')
        if isinstance(debug, dict):
            debug['combatEncounterId'] = encounter.combat_encounter_id
    return encounter


def _combat_orm_token(
    *,
    session_obj: Session,
    campaign: Campaign,
    turn: DmTurn,
) -> tuple[Any, ...]:
    """Capture the rows an intent plan is allowed to accompany into persistence."""

    return (
        (
            session_obj.session_id,
            session_obj.campaign_id,
            session_obj.status,
            session_obj.state_snapshot,
            session_obj.updated_at,
            session_obj.deleted_at,
        ),
        (
            campaign.campaign_id,
            campaign.workspace_id,
            campaign.world_id,
            campaign.status,
            campaign.current_quest,
            campaign.plot_points,
            campaign.active_npcs,
            campaign.location,
            campaign.updated_at,
        ),
        (
            turn.turn_id,
            turn.session_id,
            turn.campaign_id,
            turn.player_id,
            turn.status,
            turn.dm_output,
            turn.requires_roll,
            turn.roll_value,
            turn.rule_type,
            turn.outcome_status,
            turn.rules_hint,
            turn.metadata_json,
            turn.completed_at,
        ),
    )


def _reload_combat_orm(
    session_id: int,
    campaign_id: int,
    turn_id: int,
) -> tuple[Session | None, Campaign | None, DmTurn | None]:
    return (
        db.session.get(Session, session_id),
        db.session.get(Campaign, campaign_id),
        db.session.get(DmTurn, turn_id),
    )


def _reload_validated_combat_orm(
    expected_token: tuple[Any, ...],
    reloader: CombatOrmReloader | None,
    *,
    phase: str,
) -> tuple[Session, Campaign, DmTurn]:
    reload_models = reloader or _reload_combat_orm
    try:
        loaded_session, loaded_campaign, loaded_turn = reload_models(
            int(expected_token[0][0]),
            int(expected_token[1][0]),
            int(expected_token[2][0]),
        )
    except Exception as exc:
        raise RuntimeError(f'Combat ORM reload failed after {phase}: {exc}') from exc
    if not all((loaded_session, loaded_campaign, loaded_turn)):
        raise RuntimeError(f'Combat ORM rows disappeared during {phase}.')
    assert loaded_session is not None
    assert loaded_campaign is not None
    assert loaded_turn is not None
    current_token = _combat_orm_token(
        session_obj=loaded_session,
        campaign=loaded_campaign,
        turn=loaded_turn,
    )
    if current_token != expected_token:
        raise RuntimeError(f'Combat state changed during {phase}.')
    return loaded_session, loaded_campaign, loaded_turn


def _deterministic_enemy_intent_plan(combat: dict[str, Any]) -> dict[str, Any]:
    deterministic_combat = deepcopy(combat)
    flags = (
        dict(deterministic_combat.get('flags'))
        if isinstance(deterministic_combat.get('flags'), dict)
        else {}
    )
    settings = normalize_combat_difficulty_ai(
        flags.get('combatDifficultyAI') or flags.get('combat_difficulty_ai')
    )
    settings.update(
        {
            'allowBossTacticsHelper': False,
            'allowSentientEnemyBrain': False,
            'allowFreeformEnemyTactics': False,
            'allowBossWarmPlanner': False,
            'forceSentientEnemyBrain': False,
            'forceFreeformEnemyTactics': False,
            'maxLlmCallsPerRound': 0,
        }
    )
    flags['combatDifficultyAI'] = settings
    deterministic_combat['flags'] = flags
    return plan_enemy_intents(deterministic_combat)


def _plan_enemy_intents_at_provider_boundary(
    combat: dict[str, Any],
    *,
    session_obj: Session,
    campaign: Campaign,
    turn: DmTurn,
    before_intent_provider_call: Callable[[], None] | None,
    reload_orm_after_intent_provider_call: CombatOrmReloader | None,
) -> _IntentPlanningBoundaryResult:
    """Plan from plain data, then revalidate ORM rows before any persistence.

    Foreground demand is registered before the caller releases its scoped
    session, so a queued canon job cannot win the provider slot in between.
    """

    expected_token = _combat_orm_token(
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
    )
    session_release_attempted = False
    session_released = False
    deterministic_fallback_used = False
    intent_plan: dict[str, Any] | None = None
    planning_error: str | None = None

    with foreground_provider_reservation() as activate_provider:
        if before_intent_provider_call:
            session_release_attempted = True
            try:
                before_intent_provider_call()
                session_released = True
            except Exception as exc:
                planning_error = f'Database session release failed: {exc}'
        if planning_error is None:
            activate_provider()
            try:
                intent_plan = plan_enemy_intents(combat)
            except Exception as exc:
                planning_error = f'Enemy intent planning failed: {exc}'
                try:
                    intent_plan = _deterministic_enemy_intent_plan(combat)
                    deterministic_fallback_used = True
                except Exception as fallback_exc:
                    planning_error = (
                        f'{planning_error}; deterministic fallback failed: {fallback_exc}'
                    )

    reloaded_session = session_obj
    reloaded_campaign = campaign
    reloaded_turn = turn
    stale = False
    if session_release_attempted:
        try:
            reloaded_session, reloaded_campaign, reloaded_turn = (
                _reload_validated_combat_orm(
                    expected_token,
                    reload_orm_after_intent_provider_call,
                    phase='enemy intent planning',
                )
            )
        except Exception as exc:
            stale = True
            planning_error = (
                f'{planning_error + "; " if planning_error else ""}'
                f'{exc}'
            )

    if stale or intent_plan is None:
        intent_plan = None

    return _IntentPlanningBoundaryResult(
        intent_plan=intent_plan,
        session_obj=reloaded_session,
        campaign=reloaded_campaign,
        turn=reloaded_turn,
        session_release_attempted=session_release_attempted,
        session_released=session_released,
        deterministic_fallback_used=deterministic_fallback_used,
        stale=stale,
        error=planning_error,
    )


def _record_intent_boundary_debug(
    debug: dict[str, Any],
    boundary: _IntentPlanningBoundaryResult,
) -> None:
    debug['ormSessionReleaseAttempted'] = bool(
        debug.get('ormSessionReleaseAttempted') or boundary.session_release_attempted
    )
    debug['ormSessionReleased'] = bool(
        debug.get('ormSessionReleased') or boundary.session_released
    )
    debug['intentOrmSessionReleaseAttempted'] = boundary.session_release_attempted
    debug['intentOrmSessionReleased'] = boundary.session_released
    debug['intentPlanningFallbackUsed'] = boundary.deterministic_fallback_used
    debug['intentPlanningStale'] = boundary.stale
    if boundary.error:
        debug['intentPlanningError'] = boundary.error


def _plan_creatures_at_provider_boundary(
    request: dict[str, Any],
    *,
    workspace_id: str,
    session_obj: Session,
    campaign: Campaign,
    turn: DmTurn,
    before_creature_provider_call: Callable[[], None] | None,
    reload_orm_after_creature_provider_call: CombatOrmReloader | None,
) -> _CreaturePlanningBoundaryResult:
    expected_token = _combat_orm_token(
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
    )
    session_release_attempted = False
    session_released = False
    stale = False
    planning_error: str | None = None
    reloaded_session = session_obj
    reloaded_campaign = campaign
    reloaded_turn = turn

    def before_provider() -> None:
        nonlocal session_release_attempted, session_released
        session_release_attempted = True
        assert before_creature_provider_call is not None
        before_creature_provider_call()
        session_released = True

    def after_provider() -> None:
        nonlocal reloaded_session, reloaded_campaign, reloaded_turn, stale, planning_error
        try:
            reloaded_session, reloaded_campaign, reloaded_turn = (
                _reload_validated_combat_orm(
                    expected_token,
                    reload_orm_after_creature_provider_call,
                    phase='creature encounter planning',
                )
            )
        except Exception as exc:
            stale = True
            planning_error = str(exc)
            raise

    try:
        resolution_plan = plan_creatures_for_encounter(
            request,
            workspace_id=workspace_id,
            before_provider_call=(
                before_provider if before_creature_provider_call else None
            ),
            after_provider_call=(
                after_provider if before_creature_provider_call else None
            ),
        )
    except Exception as exc:
        resolution_plan = None
        if planning_error is None:
            planning_error = f'Creature encounter planning failed: {exc}'

    return _CreaturePlanningBoundaryResult(
        resolution_plan=resolution_plan,
        session_obj=reloaded_session,
        campaign=reloaded_campaign,
        turn=reloaded_turn,
        session_release_attempted=session_release_attempted,
        session_released=session_released,
        stale=stale,
        error=planning_error,
    )


def _record_creature_boundary_debug(
    debug: dict[str, Any],
    boundary: _CreaturePlanningBoundaryResult,
) -> None:
    debug['ormSessionReleaseAttempted'] = bool(
        debug.get('ormSessionReleaseAttempted') or boundary.session_release_attempted
    )
    debug['ormSessionReleased'] = bool(
        debug.get('ormSessionReleased') or boundary.session_released
    )
    debug['creatureOrmSessionReleaseAttempted'] = boundary.session_release_attempted
    debug['creatureOrmSessionReleased'] = boundary.session_released
    debug['creaturePlanningStale'] = boundary.stale
    if boundary.error:
        debug['creaturePlanningError'] = boundary.error


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


def _combat_difficulty_ai(state: dict[str, Any]) -> dict[str, Any]:
    settings = state.get('settings') if isinstance(state.get('settings'), dict) else {}
    return normalize_combat_difficulty_ai(settings.get('combatDifficultyAI'))


def _pack_enemy_group_flags(enemies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for enemy in enemies:
        groups.append(
            {
                'label': enemy.get('campaignPackEnemyId') or enemy.get('definitionId') or enemy.get('name'),
                'count': 1,
                'creatureId': enemy.get('definitionId'),
                'name': enemy.get('name'),
                'creatureTypeName': enemy.get('creatureTypeName'),
                'source': enemy.get('source'),
                'resolutionMethod': 'campaign_pack',
            }
        )
    return groups


def _campaign_pack_resolution_summary(combat: dict[str, Any], enemies: list[dict[str, Any]]) -> dict[str, Any]:
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    return {
        'resolutionMethod': 'campaign_pack',
        'resolutionMethods': ['campaign_pack'],
        'sources': ['campaign_pack'],
        'groups': _pack_enemy_group_flags(enemies),
        'totalEnemies': len(enemies),
        'generated': False,
        'savedToBestiary': False,
        'encounterGoal': combat.get('encounterGoal'),
        'debug': {
            'campaignPackId': flags.get('campaignPackId') or flags.get('packId'),
            'campaignPackEncounterId': flags.get('campaignPackEncounterId'),
            'campaignPackCheckpointIds': flags.get('campaignPackCheckpointIds') or [],
        },
    }


def _prepare_campaign_pack_combat_start(
    *,
    working_state: dict[str, Any],
    session_obj: Session,
    campaign: Campaign,
    turn: DmTurn,
    submitted_actor_id: str,
    actor_record: dict[str, Any],
    combat_started_by: str,
    initiative_required: bool,
    reason: str,
    before_intent_provider_call: Callable[[], None] | None,
    reload_orm_after_intent_provider_call: CombatOrmReloader | None,
) -> dict[str, Any] | None:
    scene = working_state.get('currentScene') if isinstance(working_state.get('currentScene'), dict) else {}
    enemy_position = _scene_position_for_actor(working_state, actor_record) if actor_record else {'rangeBand': 'near'}
    base_change = {
        'id': stable_change_id(turn.turn_id, 'combat.start.campaign_pack', combat_started_by),
        'turnId': turn.turn_id,
        'type': 'combat.start',
        'source': 'campaign_pack',
        'combat': {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [],
            'battlefield': default_battlefield(scene),
            'encounterGoal': None,
            'initiative': [],
            'flags': {
                'combatStartedBy': combat_started_by,
                'initiativeRequired': initiative_required,
                'combatDifficultyAI': _combat_difficulty_ai(working_state),
            },
        },
        'reason': reason,
        'visible': False,
    }
    materialized = materialize_campaign_pack_combat_start(working_state, base_change)
    combat_payload = materialized.get('combat') if isinstance(materialized.get('combat'), dict) else {}
    flags = combat_payload.get('flags') if isinstance(combat_payload.get('flags'), dict) else {}
    enemies = [
        participant
        for participant in (combat_payload.get('participants') or [])
        if isinstance(participant, dict) and participant.get('team') == 'enemy'
    ]
    if not flags.get('campaignPackEncounterId') or not enemies:
        return None

    for enemy in enemies:
        enemy['position'] = dict(enemy_position or {'rangeBand': 'near'})
    flags.update(
        {
            'combatStartedBy': combat_started_by,
            'initiativeRequired': initiative_required,
            'combatDifficultyAI': _combat_difficulty_ai(working_state),
            'resolverMethod': 'campaign_pack',
            'creatureSource': 'campaign_pack',
            'enemyCount': len(enemies),
            'enemyGroups': _pack_enemy_group_flags(enemies),
        }
    )
    combat_payload['flags'] = flags
    combat_payload = normalize_combat_state(combat_payload, scene)
    turn_context = _sync_combat_turn_context(combat_payload, submitted_actor_id=submitted_actor_id, active_actor_id=submitted_actor_id)
    intent_boundary = _plan_enemy_intents_at_provider_boundary(
        combat_payload,
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        before_intent_provider_call=before_intent_provider_call,
        reload_orm_after_intent_provider_call=reload_orm_after_intent_provider_call,
    )
    boundary_debug: dict[str, Any] = {}
    _record_intent_boundary_debug(boundary_debug, intent_boundary)
    if intent_boundary.intent_plan is None:
        return {
            'changes': [],
            'debug': {
                'triggered': True,
                'resolver': _campaign_pack_resolution_summary(combat_payload, enemies),
                'intentPlan': None,
                'combatSummary': None,
                'combatEncounterId': None,
                'turnContext': turn_context,
                'campaignPackEncounterId': flags.get('campaignPackEncounterId'),
                **boundary_debug,
            },
            'combatContext': None,
        }
    turn = intent_boundary.turn
    intent_plan = intent_boundary.intent_plan
    combat_payload = attach_intents_to_combat(combat_payload, intent_plan)
    materialized['combat'] = combat_payload
    materialized['id'] = stable_change_id(
        turn.turn_id,
        'combat.start.campaign_pack',
        flags.get('campaignPackEncounterId'),
        combat_started_by,
    )
    materialized['reason'] = reason
    materialized['visible'] = False
    resolution = _campaign_pack_resolution_summary(combat_payload, enemies)
    return _defer_combat_prepare_finalization({
        'changes': [materialized],
        'debug': {
            'triggered': True,
            'resolver': resolution,
            'intentPlan': intent_plan,
            'combatSummary': combat_summary_for_dm(combat_payload),
            'combatEncounterId': None,
            'turnContext': turn_context,
            'campaignPackEncounterId': flags.get('campaignPackEncounterId'),
            **boundary_debug,
        },
        'combatContext': combat_summary_for_dm(combat_payload),
    })


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


def sync_combat_encounter_record(
    *,
    session_obj: Session,
    campaign: Campaign,
    combat: dict[str, Any],
) -> CombatEncounter | None:
    """Keep the durable combat row aligned with the snapshot combat state."""
    if not isinstance(combat, dict):
        return None
    status = str(combat.get('status') or '').strip().lower()
    if status in {'starting', 'active'}:
        return _ensure_encounter_record(session_obj=session_obj, campaign=campaign, combat=combat)

    if status not in {'ended', 'resolved', 'none'}:
        return None

    encounter = (
        CombatEncounter.query.filter_by(session_id=session_obj.session_id)
        .filter(CombatEncounter.status.in_(['starting', 'active']))
        .order_by(CombatEncounter.updated_at.desc())
        .first()
    )
    if not encounter:
        return None

    participant_ids = [participant.get('id') for participant in combat.get('participants') or [] if isinstance(participant, dict)]
    encounter.status = 'ended'
    encounter.round = int(combat.get('round') or encounter.round or 1)
    encounter.encounter_goal_json = safe_json_dumps(combat.get('encounterGoal') or {}, {})
    encounter.battlefield_json = safe_json_dumps(combat.get('battlefield') or {}, {})
    encounter.participant_ids_json = safe_json_dumps(participant_ids, [])
    encounter.ended_at = encounter.ended_at or utc_now()
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
    before_intent_provider_call: Callable[[], None] | None = None,
    reload_orm_after_intent_provider_call: CombatOrmReloader | None = None,
    before_creature_provider_call: Callable[[], None] | None = None,
    reload_orm_after_creature_provider_call: CombatOrmReloader | None = None,
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
        intent_boundary = _plan_enemy_intents_at_provider_boundary(
            combat,
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            before_intent_provider_call=before_intent_provider_call,
            reload_orm_after_intent_provider_call=reload_orm_after_intent_provider_call,
        )
        _record_intent_boundary_debug(debug, intent_boundary)
        if intent_boundary.intent_plan is None:
            debug['combatSummary'] = combat_summary_for_dm(combat)
            return {
                'changes': [],
                'debug': debug,
                'combatContext': debug['combatSummary'],
            }
        turn = intent_boundary.turn
        intent_plan = intent_boundary.intent_plan
        combat_with_intents = attach_intents_to_combat(combat, intent_plan)
        debug['intentPlan'] = intent_plan
        debug['combatSummary'] = combat_summary_for_dm(combat_with_intents)
        turn_update_change = _combat_turn_update_change(
            turn_id=turn.turn_id,
            combat=combat,
            turn_context=debug['turnContext'] if isinstance(debug.get('turnContext'), dict) else combat_turn_context(combat),
            reason='Combat roster prepared before narration.',
        )
        changes = _intent_changes(turn.turn_id, combat_with_intents, intent_plan)
        if turn_update_change:
            changes = [turn_update_change, *changes]
        return _defer_combat_prepare_finalization({
            'changes': changes,
            'debug': debug,
            'combatContext': debug['combatSummary'],
        })

    if not _should_start_combat(working_state, player_message):
        return {'changes': [], 'debug': debug, 'combatContext': None}

    debug['triggered'] = True
    actor_record = next(
        (
            actor
            for actor in (working_state.get('playerCharacters') or [])
            if isinstance(actor, dict) and str(actor.get('id') or '') == submitted_actor_id
        ),
        {},
    )
    pack_start = _prepare_campaign_pack_combat_start(
        working_state=working_state,
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        submitted_actor_id=submitted_actor_id,
        actor_record=actor_record,
        combat_started_by='player_hostile_action',
        initiative_required=True,
        reason='Campaign pack combat started from player hostile action.',
        before_intent_provider_call=before_intent_provider_call,
        reload_orm_after_intent_provider_call=reload_orm_after_intent_provider_call,
    )
    if pack_start:
        return pack_start

    request = default_request_from_session(
        session_obj=session_obj,
        campaign=campaign,
        state=working_state,
        player_message=player_message,
    )
    creature_boundary = _plan_creatures_at_provider_boundary(
        request,
        workspace_id=workspace_id,
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        before_creature_provider_call=before_creature_provider_call,
        reload_orm_after_creature_provider_call=reload_orm_after_creature_provider_call,
    )
    _record_creature_boundary_debug(debug, creature_boundary)
    if creature_boundary.resolution_plan is None:
        return {'changes': [], 'debug': debug, 'combatContext': None}
    session_obj = creature_boundary.session_obj
    campaign = creature_boundary.campaign
    turn = creature_boundary.turn
    creature_plan = creature_boundary.resolution_plan
    encounter_resolution = creature_plan.result
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
            'combatDifficultyAI': _combat_difficulty_ai(working_state),
        },
    }
    _sync_combat_turn_context(combat_payload, submitted_actor_id=submitted_actor_id, active_actor_id=submitted_actor_id)
    intent_boundary = _plan_enemy_intents_at_provider_boundary(
        combat_payload,
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        before_intent_provider_call=before_intent_provider_call,
        reload_orm_after_intent_provider_call=reload_orm_after_intent_provider_call,
    )
    _record_intent_boundary_debug(debug, intent_boundary)
    if intent_boundary.intent_plan is None:
        debug['resolver'] = encounter_resolution
        return {'changes': [], 'debug': debug, 'combatContext': None}
    turn = intent_boundary.turn
    intent_plan = intent_boundary.intent_plan
    combat_payload = attach_intents_to_combat(combat_payload, intent_plan)
    debug.update(
        {
            'resolver': encounter_resolution,
            'intentPlan': intent_plan,
            'combatSummary': combat_summary_for_dm(combat_payload),
            'combatEncounterId': None,
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
    return _defer_combat_prepare_finalization(
        {'changes': changes, 'debug': debug, 'combatContext': debug['combatSummary']},
        resolution_plan=creature_plan,
    )


def prepare_combat_from_dm_response(
    *,
    state: dict[str, Any],
    session_obj: Session,
    campaign: Campaign,
    turn: DmTurn,
    player_message: str,
    dm_response: str,
    workspace_id: str,
    before_intent_provider_call: Callable[[], None] | None = None,
    reload_orm_after_intent_provider_call: CombatOrmReloader | None = None,
    before_creature_provider_call: Callable[[], None] | None = None,
    reload_orm_after_creature_provider_call: CombatOrmReloader | None = None,
) -> dict[str, Any]:
    working_state = deepcopy(state)
    ensure_combat_state(working_state)
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
    actor_record = next(
        (
            actor
            for actor in (working_state.get('playerCharacters') or [])
            if isinstance(actor, dict) and str(actor.get('id') or '') == submitted_actor_id
        ),
        {},
    )
    pack_start = _prepare_campaign_pack_combat_start(
        working_state=working_state,
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        submitted_actor_id=submitted_actor_id,
        actor_record=actor_record,
        combat_started_by='post_dm_adjudicator',
        initiative_required=bool(re.search(r'\binitiative\b', dm_response or '', re.IGNORECASE)),
        reason='Campaign pack combat started from DM response.',
        before_intent_provider_call=before_intent_provider_call,
        reload_orm_after_intent_provider_call=reload_orm_after_intent_provider_call,
    )
    if pack_start:
        pack_start['debug']['adjudicationSource'] = 'post_dm_response'
        return pack_start

    request = default_request_from_session(
        session_obj=session_obj,
        campaign=campaign,
        state=working_state,
        player_message=f"{player_message}\n\nDM response: {dm_response}",
    )
    creature_boundary = _plan_creatures_at_provider_boundary(
        request,
        workspace_id=workspace_id,
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        before_creature_provider_call=before_creature_provider_call,
        reload_orm_after_creature_provider_call=reload_orm_after_creature_provider_call,
    )
    _record_creature_boundary_debug(debug, creature_boundary)
    if creature_boundary.resolution_plan is None:
        return {'changes': [], 'debug': debug, 'combatContext': None}
    session_obj = creature_boundary.session_obj
    campaign = creature_boundary.campaign
    turn = creature_boundary.turn
    creature_plan = creature_boundary.resolution_plan
    encounter_resolution = creature_plan.result
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
            'combatDifficultyAI': _combat_difficulty_ai(working_state),
        },
    }
    debug['turnContext'] = _sync_combat_turn_context(combat_payload, submitted_actor_id=submitted_actor_id, active_actor_id=submitted_actor_id)
    intent_boundary = _plan_enemy_intents_at_provider_boundary(
        combat_payload,
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        before_intent_provider_call=before_intent_provider_call,
        reload_orm_after_intent_provider_call=reload_orm_after_intent_provider_call,
    )
    _record_intent_boundary_debug(debug, intent_boundary)
    if intent_boundary.intent_plan is None:
        debug['resolver'] = encounter_resolution
        return {'changes': [], 'debug': debug, 'combatContext': None}
    turn = intent_boundary.turn
    intent_plan = intent_boundary.intent_plan
    combat_payload = attach_intents_to_combat(combat_payload, intent_plan)
    debug.update(
        {
            'resolver': encounter_resolution,
            'intentPlan': intent_plan,
            'combatSummary': combat_summary_for_dm(combat_payload),
            'combatEncounterId': None,
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
    return _defer_combat_prepare_finalization(
        {'changes': changes, 'debug': debug, 'combatContext': debug['combatSummary']},
        resolution_plan=creature_plan,
    )


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
