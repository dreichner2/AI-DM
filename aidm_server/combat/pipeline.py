from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Callable

from aidm_server.canon_text import int_or_default
from aidm_server.combat.actions import resolve_combat_action
from aidm_server.combat.attacks import resolve_save
from aidm_server.combat.difficulty import normalize_combat_difficulty_ai
from aidm_server.combat.end_conditions import check_combat_end, combat_end_change
from aidm_server.combat.intent_planner import attach_intents_to_combat, plan_enemy_intents
from aidm_server.combat.maneuvers import held_prone
from aidm_server.combat.modifiers import (
    REMOVED_CONDITIONS,
    active_effects,
    apply_damage_multiplier,
    attacks_per_action,
    can_act,
    condition_key,
    condition_keys,
    consume_modifiers,
    damage_defense,
    end_effects_at_turn_start,
    note_attack_or_damage,
    remove_active_effects,
    temp_hp_per_turn,
    tick_turn_boundary,
)
from aidm_server.combat.turn_start import run_turn_start_hooks
from aidm_server.combat.vitality import apply_death_save, death_save_roll, is_dying, is_player_character, take_damage
from aidm_server.combat.working_state import ENEMY_CONDITION_ORIGIN
from aidm_server.combat.state import (
    MAX_MOVEMENT_PER_TURN,
    combat_summary_for_dm,
    combat_turn_context,
    consume_combat_turn_economy,
    default_turn_economy,
    default_battlefield,
    ensure_combat_state,
    ensure_combat_turn_economy,
    instantiate_creature,
    normalize_combat_state,
    player_combat_participant,
)
from aidm_server.creatures.repository import record_combat_debug_event
from aidm_server.damage_dice import deterministic_roller, parse_damage_dice_expression
from aidm_server.creatures.resolver import (
    CreatureResolutionPlan,
    default_request_from_session,
    persist_creature_resolution_plan,
    plan_creatures_for_encounter,
    resolve_creatures_for_encounter,  # noqa: F401 - compatibility monkeypatch seam
)
from aidm_server.database import db
from aidm_server.game_state.campaign_pack_encounters import TRIGGER_STARTED_BY, due_encounter, materialize_campaign_pack_combat_start
from aidm_server.game_state.models import display_actor_id, stable_change_id, stable_slug
from aidm_server.models import Campaign, CombatEncounter, DmTurn, Session, safe_json_dumps, safe_json_loads
from aidm_server.provider_priority import foreground_provider_reservation
from aidm_server.spell_effects import after_damage_spell_effects
from aidm_server.time_utils import utc_now


COMBAT_TRIGGER_PATTERN = re.compile(
    r'\b(?:combat|fight|battle|ambush|enemy|enemies|monster|monsters|attack(?:s|ed|ing)?|'
    r'roll initiative|initiative|bandit|goblin|wolf|zombie|skeleton|cultist|guard)\b',
    re.IGNORECASE,
)
_HOSTILE_VERB = (
    r'(?:attack|attacks|stab|stabs|strike|strikes|shoot|shoots|slash|slashes|swing|swings|'
    r'cut|cuts|lunge|lunges|punch|punches|kick|kicks|smite|smites|kill|kills|throw|throws|hurl|hurls|'
    r'smash|smashes|smack|smacks|slam|slams|bash|bashes|crush|crushes|'
    r'grab|grabs|grapple|grapples|slice|slices|stomp|stomps|cripple|cripples|disable|disables)'
)
_HOSTILE_NEGATION = r"(?:not|never|cannot|refuses?|(?:do|does|did|wo|ca|should|would|could|is|are|was|were)n['’]?t)"
_HOSTILE_HYPOTHETICAL = r'(?:if|unless|whether|until|before|should|would|could|might|may|wonders?)'
# A subject straight after one of these is asked about or imagined, not acting: "should I attack".
_HOSTILE_QUESTION_LEADS = (
    'if', 'unless', 'whether', 'when', 'whenever', 'before', 'until',
    'should', 'would', 'could', 'might', 'may', 'must', 'shall', 'can', 'will', 'do', 'does', 'did',
)
# "I", "we" or a capitalised name, then up to six words, then a hostile verb ("I draw my
# longsword and attack the nearest cutthroat"). None of those words may negate the action or
# make it hypothetical ("I do not attack", "we should attack", "if they attack").
DIRECT_HOSTILE_ACTION_PATTERN = re.compile(
    r"(?<![\w'’])"
    + ''.join(rf'(?<!\b{lead}\s)' for lead in _HOSTILE_QUESTION_LEADS)
    + rf"(?!(?:{_HOSTILE_NEGATION}|{_HOSTILE_HYPOTHETICAL}|{'|'.join(_HOSTILE_QUESTION_LEADS)})\b)"
    + r"(?:i|we|(?-i:[A-Z][A-Za-z0-9'’-]{1,40}))(?:['’]ll)?"
    + rf"(?:\s+(?!(?:{_HOSTILE_NEGATION}|{_HOSTILE_HYPOTHETICAL})\b)[\w'’-]+,?){{0,6}}"
    + rf"\s+(?:(?:try|tries|attempt|attempts)\s+to\s+)?{_HOSTILE_VERB}\b"
    + r'|\b(?:roll initiative|initiative)\b',
    re.IGNORECASE,
)
# Things a hostile verb can be aimed at that are not creatures ("I kick the door").
OBJECT_TARGET_WORDS = frozenset({
    'door', 'doors', 'gate', 'gates', 'wall', 'walls', 'window', 'windows', 'shutter', 'shutters', 'hatch', 'trapdoor',
    'portcullis', 'lock', 'padlock', 'chest', 'crate', 'crates', 'box', 'boxes', 'barrel', 'barrels', 'cask', 'keg',
    'sack', 'table', 'chair', 'bench', 'shelf', 'rock', 'rocks', 'boulder', 'stone', 'stones', 'tree', 'branch', 'log',
    'rope', 'chain', 'chains', 'bars', 'grate', 'statue', 'pillar', 'column', 'dummy', 'post', 'lever', 'barricade',
    'fence', 'plank', 'planks', 'board', 'boards', 'floor', 'ground', 'ceiling', 'bell', 'gong', 'glass', 'jar', 'vase',
    'pot', 'bottle', 'mirror', 'crystal', 'idol', 'altar', 'coffin', 'sarcophagus', 'cage', 'wagon', 'cart', 'ice',
    'bush', 'hedge', 'sign', 'signpost', 'lantern', 'lamp', 'urn', 'beam', 'rubble', 'mechanism',
})
# "swing my axe at the rope": what follows one of these is what the blow is aimed at.
_AIM_WORDS = frozenset({'at', 'into', 'against', 'through', 'on', 'onto'})
# Words that end the description of what the blow strikes ("the door open", "the bandit by the cart").
_TARGET_PHRASE_END = frozenset({
    'with', 'using', 'by', 'and', 'then', 'while', 'before', 'after', 'until', 'so', 'to', 'as', 'near', 'beside',
    'behind', 'who', 'that', 'which', 'open', 'down', 'up', 'in', 'off', 'apart', 'again', 'hard',
})


def hostile_action_targets_creature(message: str) -> bool:
    """A direct hostile action aimed at a creature, not at an object ("I kick the door").

    The struck thing is the last word of the phrase after the hostile verb (or
    after ``at``/``into``... when the verb is followed by the weapon used).
    """

    match = DIRECT_HOSTILE_ACTION_PATTERN.search(message or '')
    if match is None:
        return False
    following = re.split(r'[.!?,;]', (message or '')[match.end():], maxsplit=1)[0]
    words = re.findall(r"[a-z']+", following.lower())
    aim = next((index for index, word in enumerate(words[:4]) if word in _AIM_WORDS), None)
    phrase: list[str] = []
    for word in words[aim + 1 if aim is not None else 0:]:
        if word in _TARGET_PHRASE_END:
            if phrase:
                break
            continue
        if word.endswith('ing') or len(phrase) == 4:
            break
        phrase.append(word)
    return not (phrase and phrase[-1] in OBJECT_TARGET_WORDS)


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


def deterministic_enemy_intent_plan(combat: dict[str, Any]) -> dict[str, Any]:
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
                    intent_plan = deterministic_enemy_intent_plan(combat)
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


def _turn_combat_action(turn: DmTurn) -> dict[str, Any] | None:
    metadata = safe_json_loads(turn.metadata_json, {})
    action_intent = metadata.get('action_intent') if isinstance(metadata, dict) else None
    if not isinstance(action_intent, dict):
        return None
    combat_action = action_intent.get('combat')
    if not isinstance(combat_action, dict) or combat_action.get('authoritative') is not True:
        return None
    return combat_action


def _turn_action_ledger_id(turn: DmTurn, combat_action: dict[str, Any]) -> str:
    # One submission is one turn record: the same HUD action id may be taken
    # again in a later submission (Extra Attack, a second move after Dash), but
    # replaying the same turn record never spends twice.
    return f"{combat_action.get('action_id')}@turn:{turn.turn_id}"


def _turn_action_already_spent(combat: dict[str, Any], *, turn: DmTurn, actor_id: str) -> bool:
    combat_action = _turn_combat_action(turn)
    if combat_action is None:
        return False
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    economy = flags.get('turnEconomy') if isinstance(flags.get('turnEconomy'), dict) else {}
    return (
        str(economy.get('actorId') or '') == actor_id
        and _turn_action_ledger_id(turn, combat_action) in (economy.get('spentActionIds') or [])
    )


def _consume_turn_combat_action(
    combat: dict[str, Any],
    *,
    turn: DmTurn,
    actor_id: str,
) -> tuple[bool, str]:
    """Spend the submitted action's cost and record what it grants this turn.

    The first weapon attack spends the action and sets ``attacksRemaining`` to
    the actor's attacks per action minus one; later attacks spend those.  An
    off-hand attack or a bonus-action strike (Martial Arts, Flurry of Blows)
    pays only its own cost: the bonus action, or nothing for a strike Flurry of
    Blows already paid for.  Dash adds a movement and Disengage marks the turn
    disengaged.
    """

    combat_action = _turn_combat_action(turn)
    if combat_action is None:
        return True, ''
    economy = combat_action.get('economy') if isinstance(combat_action.get('economy'), dict) else {}
    action_type = str(combat_action.get('action_type') or '')
    ledger_id = _turn_action_ledger_id(turn, combat_action)
    replay = _turn_action_already_spent(combat, turn=turn, actor_id=actor_id)
    current = ensure_combat_turn_economy(combat, actor_id=actor_id)
    weapon_attack = (
        action_type == 'attack'
        and combat_action.get('offhand') is not True
        and combat_action.get('bonus_attack') is not True
    )
    action_cost = int_or_default(economy.get('action'), default=0)
    if weapon_attack:
        action_cost = 0 if int(current.get('attacksRemaining') or 0) > 0 else 1
    claim = safe_json_dumps(
        {
            'actionType': action_type,
            'targetId': combat_action.get('target_id'),
            'weaponId': combat_action.get('weapon_id'),
        },
        {},
    )
    consumed, reason, remaining = consume_combat_turn_economy(
        combat,
        actor_id=actor_id,
        action_id=ledger_id,
        action_claim=claim,
        action_cost=action_cost,
        bonus_action_cost=int_or_default(economy.get('bonusAction'), default=0),
        reaction_cost=int_or_default(economy.get('reaction'), default=0),
        movement_cost=int_or_default(economy.get('movementCost'), default=0),
    )
    if not consumed or replay:
        return consumed, reason
    if ledger_id not in remaining['spentActionIds']:
        remaining['spentActionIds'].append(ledger_id)
        remaining['spentActionClaims'][ledger_id] = claim
    if weapon_attack:
        if action_cost:
            actor = next(
                (item for item in combat.get('participants') or [] if isinstance(item, dict) and str(item.get('id') or '') == actor_id),
                None,
            )
            remaining['attacksRemaining'] = attacks_per_action(actor, combat) - 1
        else:
            remaining['attacksRemaining'] = max(0, int(remaining.get('attacksRemaining') or 0) - 1)
    if action_type == 'attack' and str(combat_action.get('weapon_id') or ''):
        remaining['attackWeaponIds'] = list(dict.fromkeys([*remaining.get('attackWeaponIds', []), str(combat_action['weapon_id'])]))
    if action_type == 'dash':
        remaining['movementRemaining'] = min(MAX_MOVEMENT_PER_TURN, int(remaining.get('movementRemaining') or 0) + 1)
    if action_type == 'disengage':
        remaining['disengaged'] = True
    combat['flags']['turnEconomy'] = remaining
    return True, ''


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
    off_turn = bool(
        submitted_actor_id
        and current_actor_id
        and submitted_actor_id != current_actor_id
        and current_actor.get('team') != 'enemy'
    )
    return {
        'turnOrderMode': turn_context.get('mode') or 'initiative_order_with_enemy_blocks',
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
    current_actor = turn_context.get('currentActor') if isinstance(turn_context.get('currentActor'), dict) else {}
    if current_actor.get('id'):
        ensure_combat_turn_economy(
            combat,
            actor_id=current_actor.get('id'),
            round_number=combat.get('round'),
        )
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
    flags = _combat_turn_flags(
        turn_context,
        submitted_actor_id=(combat.get('flags') or {}).get('submittedActorId') if isinstance(combat.get('flags'), dict) else None,
    )
    turn_economy = (combat.get('flags') or {}).get('turnEconomy') if isinstance(combat.get('flags'), dict) else None
    if isinstance(turn_economy, dict):
        flags['turnEconomy'] = deepcopy(turn_economy)
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
        'flags': flags,
        'reason': reason,
        'visible': False,
    }


def _participant_index(combat: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(participant.get('id')): participant
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and participant.get('id')
    }


def turn_boundaries(combat: dict[str, Any], turn_context: dict[str, Any]) -> list[dict[str, Any]]:
    """Every turn end and start crossed when the active actor hands initiative on,
    including actors unable to act, in initiative order.

    Incapacitated or otherwise prevented actors are omitted from the actionable
    turn order, but their initiative slot must still pass so effects that end on
    their turn do not last forever.
    """

    current = turn_context.get('currentActor') if isinstance(turn_context.get('currentActor'), dict) else {}
    handoff = turn_context.get('handoffActor') if isinstance(turn_context.get('handoffActor'), dict) else {}
    if not current.get('id') or not handoff.get('id'):
        return []
    round_number = max(1, int_or_default(combat.get('round'), default=1))
    ending = {'actorId': current['id'], 'phase': 'end', 'round': round_number}
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    economy = flags.get('turnEconomy') if isinstance(flags.get('turnEconomy'), dict) else {}
    if str(economy.get('actorId') or '') == str(current['id']) and economy.get('attackWeaponIds'):
        # Rage lasts while its owner attacks; the persisted economy knows whether this turn did.
        ending['attacked'] = True
    boundaries = [ending]
    initiative = {
        str(entry.get('participantId') or entry.get('id') or ''): int_or_default(entry.get('order'), default=9999)
        for entry in combat.get('initiative') or [] if isinstance(entry, dict)
    }
    participants = [
        participant for participant in combat.get('participants') or []
        if isinstance(participant, dict) and participant.get('id')
        and participant.get('isPresent', participant.get('present', True)) is not False
        and not condition_keys(participant).intersection(REMOVED_CONDITIONS)
    ]
    participants.sort(key=lambda participant: (initiative.get(str(participant['id']), 9999), str(participant['id'])))
    full_order = [str(participant['id']) for participant in participants]
    current_id, handoff_id = str(current['id']), str(handoff['id'])
    if current_id in full_order and handoff_id in full_order:
        current_index = full_order.index(current_id)
        for offset in range(1, len(full_order) + 1):
            actor_index = (current_index + offset) % len(full_order)
            actor_id = full_order[actor_index]
            if actor_id == handoff_id:
                break
            actor_round = round_number + int(current_index + offset >= len(full_order))
            boundaries.extend([
                {'actorId': actor_id, 'phase': 'start', 'round': actor_round},
                {'actorId': actor_id, 'phase': 'end', 'round': actor_round},
            ])
    else:
        # Preserve the established actionable-order fallback for legacy states
        # whose participant roster has already been compacted.
        order_ids = [str(actor_id) for actor_id in turn_context.get('turnOrderIds') or []]
        current_index = int_or_default(turn_context.get('turnIndex'), default=0)
        for enemy in turn_context.get('enemyTurnBlock') or []:
            enemy_id = str((enemy or {}).get('id') or '')
            if not enemy_id or enemy_id == current_id or enemy_id not in order_ids:
                continue
            enemy_round = round_number if order_ids.index(enemy_id) > current_index else round_number + 1
            boundaries.extend([
                {'actorId': enemy_id, 'phase': 'start', 'round': enemy_round},
                {'actorId': enemy_id, 'phase': 'end', 'round': enemy_round},
            ])
    boundaries.append(
        {
            'actorId': handoff['id'],
            'phase': 'start',
            'round': max(1, int_or_default(turn_context.get('nextRound'), default=round_number)),
        }
    )
    return boundaries


def participant_at_turn_start(participant: dict[str, Any]) -> dict[str, Any]:
    """A copy of an actor as it begins its turn: standing up if it can (not when a shove holds it
    down to the end of this turn), no longer dodging, its reaction back.

    Enemy blocks resolve before the advance that records their turn boundaries,
    so their own rolls read this view.
    """

    view = deepcopy(participant)
    keys = condition_keys(view)
    ended = {'dodging'} | ({'prone'} if can_act(view) and not held_prone(view) else set())
    view['conditions'] = [value for value in view.get('conditions') or [] if str(value).strip().lower() not in (keys & ended)]
    view['reactionRemaining'] = 1
    return view


def apply_start_turn_damage(combat: dict[str, Any], participant: dict[str, Any], *, turn_id: Any, round_number: int) -> list[dict[str, Any]]:
    """Resolve each ongoing damage effect with a reproducible, recorded roll.

    Damage that lands runs the effects that react to damage (a sleeper wakes),
    recorded on the result as ``afterDamage``.
    """
    actor_id = str(participant.get('id') or '')
    results = []
    for effect in active_effects(participant):
        modifiers = effect.get('modifiers') if isinstance(effect.get('modifiers'), dict) else {}
        damage = modifiers.get('damagePerTurn') if isinstance(modifiers.get('damagePerTurn'), dict) else {}
        dice = parse_damage_dice_expression(damage.get('dice'))
        if not dice:
            continue
        roll = deterministic_roller(f"turn_damage:{turn_id}:{round_number}:{actor_id}:{effect['id']}")
        rolls = [roll(int(dice['sides'])) for _ in range(int(dice['count']))]
        rolled = max(0, sum(rolls) + int(dice['bonus']))
        damage_type = str(damage.get('type') or '').strip().lower()
        multiplier, defense = damage_defense(participant, damage_type)
        amount = apply_damage_multiplier(rolled, multiplier)
        if is_player_character(participant):
            applied = take_damage(participant, amount, in_combat=True)
        else:
            hp = participant.setdefault('hp', {})
            temp = max(0, int_or_default(hp.get('temp'), default=0))
            current = max(0, int_or_default(hp.get('current'), default=0))
            absorbed = min(temp, amount)
            dealt = min(current, amount - absorbed)
            hp['temp'] = temp - absorbed
            hp['current'] = current - dealt
            if hp['current'] <= 0:
                participant['isAlive'] = False
                participant['isConscious'] = False
            applied = {'amount': absorbed + dealt, 'tempHpDamage': absorbed, 'hpDamage': dealt}
        result = {
            'effectId': effect['id'], 'dice': dice['dice'], 'rolls': rolls, 'damageType': damage_type,
            'rolled': rolled, 'defense': defense, 'damage': amount, **applied,
        }
        if applied['amount'] > 0:
            after_damage = after_damage_spell_effects(
                combat, target_id=actor_id, source_id=effect['casterId'],
                event_id=f"turn_damage:{turn_id}:{round_number}:{actor_id}:{effect['id']}",
            )
            if after_damage:
                result['afterDamage'] = after_damage
        results.append(result)
    return results


def _start_turn(combat: dict[str, Any], participant: dict[str, Any], *, turn_id: Any, round_number: int) -> dict[str, Any]:
    actor_id = str(participant.get('id'))
    result: dict[str, Any] = {}
    participant['reactionRemaining'] = 1
    granted = temp_hp_per_turn(participant)
    hp = participant.setdefault('hp', {})
    if granted > int_or_default(hp.get('temp'), default=0):
        hp['temp'] = granted
        result['tempHpGranted'] = granted
    ongoing_damage = apply_start_turn_damage(combat, participant, turn_id=turn_id, round_number=round_number)
    if ongoing_damage:
        result['ongoingDamage'] = ongoing_damage
    turn_start_effects = run_turn_start_hooks(combat, actor_id=actor_id, turn_id=turn_id, round_number=round_number)
    if turn_start_effects:
        result['turnStartEffects'] = turn_start_effects
    # A hook may replace the combat's records, so the actor is read again.
    participant = _participant_index(combat).get(actor_id) or participant
    lapsed = end_effects_at_turn_start(participant)
    if lapsed:
        result['effectsEnded'] = [effect['id'] for effect in lapsed]
    keys = condition_keys(participant)
    removed = {'dodging'} & keys
    if 'prone' in keys and can_act(participant) and not held_prone(participant):
        removed.add('prone')
        economy = (combat.get('flags') or {}).get('turnEconomy')
        if isinstance(economy, dict) and str(economy.get('actorId') or '') == actor_id:
            economy['movementRemaining'] = 0
        result['stoodUp'] = True
    if removed:
        participant['conditions'] = [value for value in participant.get('conditions') or [] if str(value).strip().lower() not in removed]
        result['conditionsEnded'] = sorted(removed)
    recharged = []
    for ability in participant.get('abilities') or []:
        if not isinstance(ability, dict) or ability.get('needsRecharge') is not True:
            continue
        ability_id = str(ability.get('id') or '')
        roll = deterministic_roller(f'recharge:{turn_id}:{actor_id}:{ability_id}:{round_number}')(6)
        if roll >= 5:
            ability['needsRecharge'] = False
        recharged.append({'abilityId': ability_id, 'roll': roll, 'recharged': roll >= 5})
    if recharged:
        result['rechargeRolls'] = recharged
    return result


def _waking_boundaries(combat: dict[str, Any], boundaries: list[dict[str, Any]]) -> dict[str, int]:
    """Effect id -> index of the boundary from which an effect applied during a later turn of
    this list counts: the start of the turn it landed in (``appliedDuringTurnOf``)."""

    starts: dict[str, int] = {}
    for index, boundary in enumerate(boundaries):
        if isinstance(boundary, dict) and boundary.get('phase') == 'start':
            starts.setdefault(str(boundary.get('actorId') or '').strip(), index)
    waking: dict[str, int] = {}
    for participant in combat.get('participants') or []:
        for effect in active_effects(participant if isinstance(participant, dict) else {}):
            landed_in = str(effect.get('appliedDuringTurnOf') or '').strip()
            if landed_in in starts:
                waking[effect['id']] = starts[landed_in]
    return waking


def _settle_landed_effects(combat: dict[str, Any]) -> None:
    """Every effect has now passed the turn it landed in: boundaries count it from here on."""

    for participant in combat.get('participants') or []:
        if not isinstance(participant, dict):
            continue
        effects = active_effects(participant)
        if any('appliedDuringTurnOf' in effect for effect in effects):
            for effect in effects:
                effect.pop('appliedDuringTurnOf', None)
            participant['activeEffects'] = effects


def _without_conditions_of(participant: dict[str, Any], effect_ids: set[str]) -> dict[str, Any]:
    """The participant as it stood before the effects in ``effect_ids`` landed."""

    later = {effect.get('condition') for effect in active_effects(participant) if effect['id'] in effect_ids}
    kept = {effect.get('condition') for effect in active_effects(participant) if effect['id'] not in effect_ids}
    view = deepcopy(participant)
    view['conditions'] = [value for value in view.get('conditions') or [] if condition_key(value) not in later - kept]
    return view


def _end_turn_condition_saves(
    combat: dict[str, Any],
    participant: dict[str, Any],
    *,
    turn_id: Any,
    round_number: int,
    dormant: frozenset[str],
) -> list[dict[str, Any]]:
    """Each enemy ability's conditions on the actor (one use at a time) repeat their save; success ends them."""

    actor_id = str(participant.get('id') or '')
    results: list[dict[str, Any]] = []
    casts: dict[str, dict[str, Any]] = {}
    for effect in active_effects(participant):
        if effect['id'] not in dormant and isinstance(effect.get('endSave'), dict) and effect.get('castId'):
            casts.setdefault(str(effect['castId']), effect)
    for cast_id, effect in casts.items():
        save = {'ability': effect['endSave'].get('ability'), 'dc': int_or_default(effect['endSave'].get('dc'), default=10)}
        rolled = resolve_save(
            _without_conditions_of(participant, set(dormant)),
            save,
            combat,
            deterministic_roller(f'condition_save:{turn_id}:{round_number}:{actor_id}:{cast_id}'),
        )
        consume_modifiers(combat, rolled.pop('consume'))
        ended = remove_active_effects(participant, lambda candidate, cast_id=cast_id: str(candidate.get('castId') or '') == cast_id) if rolled['saveSucceeded'] else []
        results.append({'castId': cast_id, 'sourceName': effect['sourceName'], 'save': rolled, 'ended': ended})
    return results


def _shaken_off(participant_id: str, ended: list[dict[str, Any]], save: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Enemy-ability conditions that ended at a turn's end, one entry per use of the ability."""

    entries: dict[str, dict[str, Any]] = {}
    for effect in ended:
        if effect.get('origin') != ENEMY_CONDITION_ORIGIN:
            continue
        entry = entries.setdefault(
            str(effect.get('castId') or effect.get('id')),
            {'participantId': participant_id, 'sourceName': effect.get('sourceName') or '', 'conditions': []},
        )
        if effect.get('condition'):
            entry['conditions'].append(effect['condition'])
        if save is not None:
            entry['save'] = {key: save[key] for key in ('saveAbility', 'saveDC', 'saveRoll', 'saveTotal', 'saveSucceeded') if key in save}
    return list(entries.values())


def apply_turn_boundaries(
    combat: dict[str, Any],
    boundaries: list[dict[str, Any]],
    *,
    turn_id: Any,
    through: int | None = None,
) -> list[dict[str, Any]]:
    """Run turn-start and turn-end processing for each boundary, mutating ``combat``.

    Turn end ticks effects held by (``target_turn_end``) or cast by
    (``caster_turn_end``) the actor, repeats the save of each enemy ability's
    conditions on the actor (success ends them) and records the enemy-ability
    conditions that ended in ``shakenOff``; when the boundary records that the
    actor ``attacked``, it keeps its attack-sustained effects going.  Turn start
    ticks ``caster_turn_start`` effects, restores the actor's reaction, grants
    ``tempHpPerTurn``, deals ongoing damage, runs the turn-start hooks (spell
    repeat saves and auras), ends effects whose ``endsWhen`` lapsed, ends
    ``dodging``, stands a prone actor that can act and no shove holds down
    (spending its movement) and rolls recharge for abilities marked
    ``needsRecharge`` (5-6 on the deterministic d6 recharges).

    The enemy turns of an advance are resolved before it is applied, so an
    effect that landed during one of them (``appliedDuringTurnOf``) is not
    counted by the boundaries before that turn's start.  ``through`` stops
    after that many boundaries (a preview of the first ones).
    """

    waking = _waking_boundaries(combat, boundaries)
    events: list[dict[str, Any]] = []
    for index, boundary in enumerate(boundaries[:through]):
        if not isinstance(boundary, dict):
            continue
        actor_id = str(boundary.get('actorId') or '').strip()
        phase = 'start' if boundary.get('phase') == 'start' else 'end'
        round_number = max(1, int_or_default(boundary.get('round'), default=1))
        dormant = frozenset(effect_id for effect_id, wakes_at in waking.items() if index < wakes_at)
        event: dict[str, Any] = {'actorId': actor_id, 'phase': phase, 'round': round_number}
        expired = tick_turn_boundary(combat, actor_id=actor_id, phase=phase, dormant=dormant)
        if expired:
            event['expiredEffects'] = [effect.get('id') for effect in expired]
        participant = _participant_index(combat).get(actor_id)
        if phase == 'start' and participant is not None:
            event.update(_start_turn(combat, participant, turn_id=turn_id, round_number=round_number))
        elif participant is not None:
            shaken = _shaken_off(actor_id, [effect for effect in expired if effect.get('targetId') == actor_id])
            for result in _end_turn_condition_saves(combat, participant, turn_id=turn_id, round_number=round_number, dormant=dormant):
                event.setdefault('conditionSaves', []).append({key: result[key] for key in ('castId', 'sourceName', 'save')})
                shaken.extend(_shaken_off(actor_id, result['ended'], result['save']))
            if shaken:
                event['shakenOff'] = shaken
            if boundary.get('attacked') is True:
                note_attack_or_damage(participant)
        events.append(event)
    if through is None:
        _settle_landed_effects(combat)
    return events


def turn_end_preview(state: dict[str, Any], *, turn_id: Any) -> list[dict[str, Any]]:
    """What ending the acting hero's turn ends for them (``shakenOff``), computed on a copy
    exactly as the turn's advance will do it once its enemy turns are applied."""

    combat = normalize_combat_state(deepcopy(state.get('combat')), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    if not _combat_is_active(combat):
        return []
    boundaries = turn_boundaries(combat, combat_turn_context(combat))
    if not boundaries or boundaries[0].get('phase') != 'end':
        return []
    events = apply_turn_boundaries(combat, boundaries, turn_id=turn_id, through=1)
    return (events[0].get('shakenOff') or []) if events else []


def death_saves_due(combat: Any, *, turn_id: Any) -> list[dict[str, Any]]:
    """Death saves owed by dying player characters whose initiative slots the
    ending turn passes on its way to the next actor (rolled, not yet applied)."""

    normalized = normalize_combat_state(combat)
    if str(normalized.get('status') or '') not in {'starting', 'active'}:
        return []
    context = combat_turn_context(normalized)
    current = context.get('currentActor') if isinstance(context.get('currentActor'), dict) else {}
    handoff = context.get('handoffActor') if isinstance(context.get('handoffActor'), dict) else {}
    initiative = sorted(
        (entry for entry in normalized.get('initiative') or [] if isinstance(entry, dict)),
        key=lambda entry: int_or_default(entry.get('order'), default=0),
    )
    slot_ids = [str(entry.get('participantId')) for entry in initiative]
    if str(current.get('id')) not in slot_ids or str(handoff.get('id')) not in slot_ids:
        return []
    participants = _participant_index(normalized)
    start = slot_ids.index(str(current['id']))
    stop = slot_ids.index(str(handoff['id']))
    round_number = max(1, int_or_default(normalized.get('round'), default=1))
    results: list[dict[str, Any]] = []
    for step in range(1, len(slot_ids)):
        index = (start + step) % len(slot_ids)
        if index == stop:
            break
        participant = participants.get(slot_ids[index])
        if not isinstance(participant, dict) or not is_player_character(participant) or not is_dying(participant):
            continue
        slot_round = round_number if index > start else round_number + 1
        roll = death_save_roll(turn_id, participant['id'], slot_round)
        outcome = apply_death_save(deepcopy(participant), roll)
        results.append(
            {
                'participantId': participant['id'],
                'participantName': participant.get('name') or participant['id'],
                'round': slot_round,
                'roll': roll,
                'result': outcome['result'],
                'successes': outcome['successes'],
                'failures': outcome['failures'],
                'outcome': outcome['outcome'],
                'authoritative': True,
            }
        )
    return results


def combat_turn_advance_change(*, state: dict[str, Any], turn: DmTurn, actor_id: str | None = None) -> dict[str, Any] | None:
    """The roster advance after the active player character's turn-ending action."""

    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    if not _combat_is_active(combat):
        return None
    combat_action = _turn_combat_action(turn)
    # Only a server-issued action belonging to the eligible active actor may
    # hand initiative to the enemy block.  Chat, item/interact intents, and
    # other narration-bearing turns must never implicitly end combat turns.
    if combat_action is None:
        return None
    action_economy = combat_action.get('economy') if isinstance(combat_action.get('economy'), dict) else {}
    if action_economy.get('endsTurn') is not True:
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
    return _roster_advance_change(combat, turn_id=turn.turn_id)


def engine_controlled_turn_is_up(combat: dict[str, Any]) -> bool:
    """An active combat waits on a participant the engine plays (an enemy or a
    non-player ally) while a player character can still take a turn."""

    normalized = normalize_combat_state(combat)
    if not _combat_is_active(normalized):
        return False
    turn_context = combat_turn_context(normalized)
    current_actor = turn_context.get('currentActor')
    return (
        isinstance(current_actor, dict)
        and current_actor.get('team') != 'player'
        and any(entry.get('team') == 'player' for entry in turn_context.get('turnOrder') or [])
    )


def engine_turn_advance_change(state: dict[str, Any], *, turn_id: Any) -> dict[str, Any] | None:
    """The roster advance past the engine-controlled participants whose turns were just resolved."""

    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    if not engine_controlled_turn_is_up(combat):
        return None
    return _roster_advance_change(combat, turn_id=turn_id)


def _roster_advance_change(combat: dict[str, Any], *, turn_id: Any) -> dict[str, Any] | None:
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    active_actor_id = str(flags.get('activeActorId') or '').strip()
    current_context = combat_turn_context(combat)
    next_index = current_context.get('nextTurnIndex')
    if next_index is None:
        return None
    order = current_context.get('turnOrder') or []
    handoff_actor = current_context.get('handoffActor') if isinstance(current_context.get('handoffActor'), dict) else {}
    next_context = combat_turn_context(combat, active_actor_id=handoff_actor.get('id'))
    next_round = current_context.get('nextRound') or combat.get('round') or 1
    next_economy = default_turn_economy(handoff_actor.get('id'), next_round)
    return {
        'id': stable_change_id(
            turn_id,
            'combat.turn.advance',
            active_actor_id,
            handoff_actor.get('id'),
            next_round,
        ),
        'turnId': turn_id,
        'type': 'combat.update',
        'round': next_round,
        'turnIndex': int(next_index) % max(1, len(order)),
        'turnBoundaries': turn_boundaries(combat, current_context),
        'flags': {
            **_combat_turn_flags(next_context, submitted_actor_id=None),
            'turnEconomy': next_economy,
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
    # Striking an object ("I kick the door") is an ordinary action.
    return hostile_action_targets_creature(player_message)


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
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    scene = working_state.get('currentScene') if isinstance(working_state.get('currentScene'), dict) else {}
    enemy_position = _scene_position_for_actor(working_state, actor_record) if actor_record else {'rangeBand': 'near'}
    base_change = {
        'id': stable_change_id(turn.turn_id, 'combat.start.campaign_pack', combat_started_by),
        'turnId': turn.turn_id,
        'type': 'combat.start',
        'source': 'campaign_pack',
        # A due trigger names its encounter; the applier records it as triggered.
        **({'campaignPackEncounterId': trigger['encounterId'], 'campaignPackTrigger': trigger} if trigger else {}),
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
                'initiativeSeed': f'combat:{session_obj.session_id}:{turn.turn_id}:campaign_pack',
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
            'initiativeSeed': f'combat:{session_obj.session_id}:{turn.turn_id}:campaign_pack',
            'combatDifficultyAI': _combat_difficulty_ai(working_state),
            'resolverMethod': 'campaign_pack',
            'creatureSource': 'campaign_pack',
            'enemyCount': len(enemies),
            'enemyGroups': _pack_enemy_group_flags(enemies),
        }
    )
    combat_payload['flags'] = flags
    combat_payload = normalize_combat_state(combat_payload, scene)
    turn_context = _sync_combat_turn_context(combat_payload, submitted_actor_id=submitted_actor_id)
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
        combat_action = _turn_combat_action(turn)
        action_resolution = (
            resolve_combat_action(
                state=working_state,
                combat_action=combat_action,
                actor_id=submitted_actor_id,
                turn_id=turn.turn_id,
            )
            if combat_action is not None and not _turn_action_already_spent(combat, turn=turn, actor_id=submitted_actor_id)
            else None
        )
        if action_resolution is not None and not action_resolution['ok']:
            action_consumed, action_block_reason = False, action_resolution['reason']
        else:
            action_consumed, action_block_reason = _consume_turn_combat_action(
                combat,
                turn=turn,
                actor_id=submitted_actor_id,
            )
        if not action_consumed:
            debug['combatActionBlockedReason'] = action_block_reason
            debug['combatSummary'] = combat_summary_for_dm(combat)
            return {
                'changes': [],
                'debug': debug,
                'combatContext': debug['combatSummary'],
            }
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
        combat_context = debug['combatSummary']
        if action_resolution is not None:
            changes = [*action_resolution['changes'], *changes]
            combat_context = {**combat_context, 'playerResolvedCombatAction': action_resolution['result']}
            debug['combatActionResolution'] = action_resolution['result']
        if turn_update_change:
            changes = [turn_update_change, *changes]
        return _defer_combat_prepare_finalization({
            'changes': changes,
            'debug': debug,
            'combatContext': combat_context,
        })

    actor_record = next(
        (
            actor
            for actor in (working_state.get('playerCharacters') or [])
            if isinstance(actor, dict) and str(actor.get('id') or '') == submitted_actor_id
        ),
        {},
    )
    # An encounter whose checkpoint trigger is due starts before narration.
    due = due_encounter(working_state, [])
    if due is not None:
        trigger_start = _prepare_campaign_pack_combat_start(
            working_state=working_state,
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            submitted_actor_id=submitted_actor_id,
            actor_record=actor_record,
            combat_started_by=TRIGGER_STARTED_BY,
            initiative_required=True,
            reason='Campaign pack encounter started by its authored trigger.',
            before_intent_provider_call=before_intent_provider_call,
            reload_orm_after_intent_provider_call=reload_orm_after_intent_provider_call,
            trigger={'encounterId': str(due.get('id') or ''), 'type': str((due.get('trigger') or {}).get('type') or '')},
        )
        if trigger_start:
            return trigger_start

    if not _should_start_combat(working_state, player_message):
        return {'changes': [], 'debug': debug, 'combatContext': None}

    debug['triggered'] = True
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
            'initiativeSeed': f'combat:{session_obj.session_id}:{turn.turn_id}:player_hostile_action',
            'combatDifficultyAI': _combat_difficulty_ai(working_state),
        },
    }
    combat_payload = normalize_combat_state(combat_payload, scene)
    _sync_combat_turn_context(combat_payload, submitted_actor_id=submitted_actor_id)
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
            'initiativeSeed': f'combat:{session_obj.session_id}:{turn.turn_id}:post_dm_adjudicator',
            'combatDifficultyAI': _combat_difficulty_ai(working_state),
        },
    }
    combat_payload = normalize_combat_state(combat_payload, scene)
    debug['turnContext'] = _sync_combat_turn_context(combat_payload, submitted_actor_id=submitted_actor_id)
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
