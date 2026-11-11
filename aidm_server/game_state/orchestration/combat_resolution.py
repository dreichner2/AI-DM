from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import random
import re
from typing import Any, Callable

from aidm_server.canon_text import int_or_default
from aidm_server.class_capabilities import attack_rider_change
from aidm_server.combat.attacks import (
    TEXT_DAMAGE_PATTERN,
    ability_damage,
    ability_reach_profile,
    attack_armor_class,
    attack_weapon_descriptor,
    bounded_int,
    condition_duration_fields,
    resolve_attack,
    resolve_enemy_ability,
    resolve_enemy_healing,
)
from aidm_server.combat.end_conditions import check_combat_end, combat_end_change
from aidm_server.combat.intent_planner import ENEMY_TURN_HISTORY_FLAG, QUIET_TURN_WINDOW, attach_intents_to_combat, intent_is_quiet
from aidm_server.combat.maneuvers import (
    GRAPPLE_SOURCE,
    ability_mod,
    at_most_quarter,
    escape_dc,
    grapple_effect,
    grapple_holds,
    grappler_id,
)
from aidm_server.combat.modifiers import (
    MODIFIER_ENGINE_SOURCE,
    active_effects,
    attack_context,
    can_act,
    condition_keys,
    engaged_with,
    range_band,
    remove_active_effects,
    speed_is_zero,
    temp_hp_per_turn,
    tick_turn_boundary,
)
from aidm_server.combat.pipeline import (
    apply_start_turn_damage,
    death_saves_due,
    deterministic_enemy_intent_plan,
    engine_controlled_turn_is_up,
    engine_turn_advance_change,
    participant_at_turn_start,
    turn_boundaries,
)
from aidm_server.combat.positioning import band_outward, enemy_approach, move_participant, participant_by_id
from aidm_server.combat.reactions import collect_reactions, reaction_changes
from aidm_server.combat.turn_start import run_turn_start_hooks
from aidm_server.combat.vitality import DEATH_SAVE_SOURCE, death_save_change_id
from aidm_server.combat.state import (
    combat_ability_is_available,
    combat_ability_resolution_mode,
    combat_summary_for_dm,
    combat_turn_context,
    participant_can_take_turn,
    participant_is_targetable,
)
from aidm_server.damage_dice import deterministic_roller, normalize_damage_dice_expression
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.models import normalize_item_name, stable_change_id
from aidm_server.game_state.orchestration.fight_stakes import GOAL_INTENT, resolve_goal_turn, stake_changes, wrest_after_hit
from aidm_server.game_state.orchestration.scene_plans import RESTRAINED_SOURCE
from aidm_server.game_state.validation.validator import validate_state_changes, validated_changes_for_application


__all__ = [
    'TEXT_DAMAGE_PATTERN',
    'EngineTurnSettlement',
    'TrustedDamageChanges',
    'build_dm_combat_context',
    'combat_participant_update_signature',
    'derive_trusted_damage_changes',
    'player_attack_target',
    'resolve_authoritative_player_attack',
    'settle_engine_controlled_turns',
    'without_trusted_damage_overlaps',
]


_TRUSTED_DAMAGE_SOURCE_TYPES = {
    'player_attack': 'trusted_player_attack',
    'player_resolved_attack': 'trusted_player_attack',
    'environmental_hazard': 'trusted_environmental_hazard',
    'environment_hazard': 'trusted_environmental_hazard',
    'hazard': 'trusted_environmental_hazard',
    'trap': 'trusted_environmental_hazard',
}
ENEMY_RESOLVED_SOURCE = 'enemy_resolved_action'
ENEMY_TURN_HISTORY_SOURCE = 'enemy_turn_history'


@dataclass(frozen=True)
class TrustedDamageChanges:
    enemy: list[dict[str, Any]]
    resolved: list[dict[str, Any]]
    death_saves: list[dict[str, Any]] = field(default_factory=list)
    turn_history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def all_changes(self) -> list[dict[str, Any]]:
        return [*self.enemy, *self.resolved, *self.death_saves, *self.turn_history]


@dataclass(frozen=True)
class EngineTurnSettlement:
    """Engine-controlled turns resolved so that initiative rests on a player character."""

    state: dict[str, Any]
    changes: list[dict[str, Any]]
    validation: dict[str, Any]
    applied_changes: list[dict[str, Any]]
    resolved_actions: list[dict[str, Any]]


def _signature_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((str(key), _signature_value(value[key])) for key in sorted(value))
    if isinstance(value, (list, tuple)):
        return tuple(_signature_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted(_signature_value(item) for item in value))
    if isinstance(value, str):
        return normalize_item_name(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return normalize_item_name(value)


def _signature_string_list(value: Any) -> tuple[str, ...]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return tuple(sorted(normalize_item_name(item) for item in values if str(item or '').strip()))


def combat_participant_update_signature(change: dict[str, Any]) -> tuple[Any, ...]:
    fields: list[tuple[str, Any]] = []
    if 'hp' in change:
        hp = change.get('hp')
        if isinstance(hp, dict):
            fields.append(
                (
                    'hp',
                    (
                        ('current', _signature_value(hp.get('current', hp.get('currentHp')))),
                        ('max', _signature_value(hp.get('max', hp.get('maxHp')))),
                        ('temp', _signature_value(hp.get('temp', hp.get('tempHp')))),
                    ),
                )
            )
        else:
            fields.append(('hp', _signature_value(hp)))
    if 'conditions' in change:
        fields.append(('conditions', _signature_string_list(change.get('conditions'))))
    if 'position' in change:
        fields.append(('position', _signature_value(change.get('position'))))
    if 'participant' in change:
        fields.append(('participant', _signature_value(change.get('participant'))))
    for key in ('isAlive', 'isConscious'):
        if key in change:
            fields.append((key, _signature_value(change.get(key))))
    return tuple(fields)


def _damage_change_signature(change: dict[str, Any]) -> tuple[Any, ...] | None:
    change_type = str(change.get('type') or '').strip()
    if change_type not in {'health.heal', 'health.damage'}:
        return None
    actor_id = str(change.get('actorId') or change.get('actor_id') or '')
    return (change_type, actor_id, int_or_default(change.get('amount'), default=0))


def _participant_id(participant: dict[str, Any]) -> str:
    return str(participant.get('id') or participant.get('participantId') or participant.get('actorId') or '').strip()


def _participant_name(participant: dict[str, Any] | None, fallback: str) -> str:
    if not isinstance(participant, dict):
        return fallback
    return str(participant.get('name') or participant.get('displayName') or fallback).strip() or fallback


def _combat_participants(state: dict[str, Any]) -> list[dict[str, Any]]:
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    participants = combat.get('participants') if isinstance(combat.get('participants'), list) else []
    return [participant for participant in participants if isinstance(participant, dict)]


def _participant_by_id(participants: list[dict[str, Any]], participant_id: Any) -> dict[str, Any] | None:
    wanted = str(participant_id or '').strip()
    if not wanted:
        return None
    for participant in participants:
        if _participant_id(participant) == wanted:
            return participant
    return None


def _ability_by_id(participant: dict[str, Any] | None, ability_id: Any) -> dict[str, Any] | None:
    if not isinstance(participant, dict):
        return None
    wanted = str(ability_id or '').strip()
    abilities = participant.get('abilities') if isinstance(participant.get('abilities'), list) else []
    if wanted:
        for ability in abilities:
            if isinstance(ability, dict) and str(ability.get('id') or ability.get('abilityId') or '').strip() == wanted:
                return ability
        return None
    for ability in abilities:
        if isinstance(ability, dict) and str(ability.get('type') or '').strip().lower() == 'attack':
            return ability
    return abilities[0] if abilities and isinstance(abilities[0], dict) else None


def _random_roll(sides: int) -> int:
    return random.randint(1, max(1, int(sides)))


def _attack_weapon_item(player_actor: dict[str, Any], combat_action: dict[str, Any]) -> dict[str, Any]:
    weapon_id = str(combat_action.get('weapon_id') or '').strip()
    weapon_name = str(combat_action.get('weapon_name') or 'weapon').strip() or 'weapon'
    inventory = player_actor.get('inventory') if isinstance(player_actor.get('inventory'), dict) else {}
    return next(
        (item for item in inventory.get('items') or [] if isinstance(item, dict) and weapon_id and str(item.get('id') or '') == weapon_id),
        {'id': weapon_id, 'name': weapon_name},
    )


def player_attack_target(state: dict[str, Any], *, actor_id: str, combat_action: Any) -> dict[str, int] | None:
    """The armour class (and lowest critical d20) a server-issued HUD attack will be resolved against.

    The roll is shown against this ``armorClass`` and the engine compares the
    attack with the same number, so the roll card and the narration agree.
    """

    if not isinstance(combat_action, dict) or str(combat_action.get('action_type') or '').strip().lower() != 'attack':
        return None
    participants = _combat_participants(state)
    actor = _participant_by_id(participants, actor_id)
    target = _participant_by_id(participants, combat_action.get('target_id'))
    if not isinstance(actor, dict) or not isinstance(target, dict):
        return None
    ranged = combat_action.get('ranged') is True
    player_actor = _player_actors_by_id(state).get(actor_id) or {}
    modifiers = attack_context(
        actor,
        target,
        kind='weapon_attack',
        ranged=ranged,
        combat=state.get('combat') if isinstance(state.get('combat'), dict) else {},
        weapon=attack_weapon_descriptor(player_actor, _attack_weapon_item(player_actor, combat_action), ranged=ranged),
    )
    return {
        'armorClass': attack_armor_class(target, modifiers),
        'criticalThreshold': 20 - max(0, int(modifiers['criticalRangeBonus'])),
    }


def resolve_authoritative_player_attack(
    *,
    state: dict[str, Any],
    actor_id: str,
    turn_id: int | None,
    action_intent: dict[str, Any] | None,
    authoritative_roll: dict[str, Any] | None,
    damage_roller: Callable[[int], int] | None = None,
) -> dict[str, Any] | None:
    """Resolve a canonical HUD attack before the DM is allowed to narrate it.

    An off-hand attack adds the ability modifier to damage only when it is
    negative.  Reactions the hit provokes are returned in ``reactions``.
    """

    if not isinstance(action_intent, dict) or not isinstance(authoritative_roll, dict):
        return None
    combat_action = action_intent.get('combat') if isinstance(action_intent.get('combat'), dict) else None
    if (
        not isinstance(combat_action, dict)
        or combat_action.get('authoritative') is not True
        or str(combat_action.get('action_type') or '').strip().lower() != 'attack'
        or authoritative_roll.get('authoritative') is not True
    ):
        return None

    participants = _combat_participants(state)
    actor = _participant_by_id(participants, actor_id)
    target_id = str(combat_action.get('target_id') or '').strip()
    target = _participant_by_id(participants, target_id)
    if (
        not isinstance(actor, dict)
        or str(actor.get('team') or '').strip().lower() != 'player'
        or not isinstance(target, dict)
        or str(target.get('team') or '').strip().lower() != 'enemy'
    ):
        return None
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    active_actor_id = str(flags.get('activeActorId') or '').strip()
    if active_actor_id and active_actor_id != actor_id:
        return None

    player_die = bounded_int(authoritative_roll.get('kept'), default=1, minimum=1, maximum=20)
    attack_bonus = int_or_default(authoritative_roll.get('total'), default=player_die) - player_die
    weapon_id = str(combat_action.get('weapon_id') or '').strip()
    damage_type = str(combat_action.get('damage_type') or 'slashing').strip().lower()
    damage_dice = normalize_damage_dice_expression(combat_action.get('damage_dice')) or '1d6'
    modifier_breakdown = (
        authoritative_roll.get('modifier_breakdown')
        if isinstance(authoritative_roll.get('modifier_breakdown'), dict)
        else {}
    )
    damage_bonus = int_or_default(modifier_breakdown.get('ability_modifier'), default=0)
    offhand = combat_action.get('offhand') is True
    if offhand:
        damage_bonus = min(0, damage_bonus)
    weapon_name = str(combat_action.get('weapon_name') or 'weapon').strip() or 'weapon'
    ranged = combat_action.get('ranged') is True
    player_actor = _player_actors_by_id(state).get(actor_id) or {}
    weapon_item = _attack_weapon_item(player_actor, combat_action)
    ability = authoritative_roll.get('ability') if isinstance(authoritative_roll.get('ability'), dict) else {}
    economy = flags.get('turnEconomy') if isinstance(flags.get('turnEconomy'), dict) else {}
    once_per_turn_used = economy.get('oncePerTurnUsed') if str(economy.get('actorId') or '') == actor_id else []
    rider = combat_action.get('rider') if isinstance(combat_action.get('rider'), dict) else None
    working_combat = deepcopy(combat)
    working = working_combat['participants'] if isinstance(working_combat.get('participants'), list) else []
    # The player's server-rolled d20 is the first die; advantage or disadvantage
    # adds a second die from the engine's deterministic roller.
    attack = resolve_attack(
        attacker=_participant_by_id(working, actor_id) or actor,
        target=_participant_by_id(working, target_id) or target,
        combat=working_combat,
        state={**state, 'combat': working_combat},
        kind='weapon_attack',
        ranged=ranged,
        first_d20=player_die,
        attack_bonus=attack_bonus,
        attack_roller=deterministic_roller(f'player_attack_roll:{turn_id}:{actor_id}:{target_id}:{weapon_id}'),
        damage={'dice': damage_dice, 'type': damage_type, 'bonus': damage_bonus, 'source': weapon_name},
        damage_roller=damage_roller or deterministic_roller(f'player_attack:{turn_id}:{actor_id}:{target_id}:{weapon_id}'),
        event_id=f'player_attack:{turn_id}:{actor_id}:{target_id}:{weapon_id}',
        turn_id=turn_id,
        weapon=attack_weapon_descriptor(player_actor, weapon_item, ranged=ranged),
        ability=ability.get('key'),
        rider=rider,
        once_per_turn_used=once_per_turn_used or [],
        armor_class=authoritative_roll.get('target_ac') if isinstance(authoritative_roll.get('target_ac'), int) else None,
    )
    result = {
        'sourceType': 'player_attack',
        'sourceActorId': actor_id,
        'sourceActorName': _participant_name(actor, actor_id),
        'targetId': target_id,
        'targetName': _participant_name(target, target_id),
        'weaponId': weapon_id or None,
        'weaponName': weapon_name,
        **attack,
        'damageDice': damage_dice,
        'damageBonus': damage_bonus,
        'damageType': damage_type,
        'authoritative': True,
        'instruction': 'Narrate this player attack exactly as resolved by the engine; do not alter hit, damage, or target.',
    }
    if offhand:
        result['offhand'] = True
    if rider is not None:
        result['rider'] = deepcopy(rider)
    return result


def _enemy_reach(enemy: dict[str, Any], target: dict[str, Any], ability: dict[str, Any], combat: dict[str, Any], *, can_move: bool) -> dict[str, Any]:
    melee, bands = ability_reach_profile(ability)
    return enemy_approach(enemy, target, melee=melee, allowed_bands=bands, combat=combat, can_move=can_move)


def _move_enemy(combat: dict[str, Any], enemy_id: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    movement: list[dict[str, Any]] = []
    for step in steps:
        enemy = participant_by_id(combat, enemy_id)
        from_band = range_band(enemy)
        move_participant(combat, enemy_id, step['toRangeBand'], engage_with=step.get('engageWith') or [])
        movement.append({'fromRangeBand': from_band, 'toRangeBand': step['toRangeBand'], 'engageWith': list(step.get('engageWith') or [])})
    return movement


def _begin_enemy_turn(enemy: dict[str, Any]) -> bool:
    """Apply the turn-start view to the working enemy; True when it may still move."""

    started = participant_at_turn_start(enemy)
    stood_up = 'prone' in condition_keys(enemy) and 'prone' not in condition_keys(started)
    enemy['conditions'] = started['conditions']
    enemy['reactionRemaining'] = started['reactionRemaining']
    return not stood_up and not speed_is_zero(enemy)


def _retreat(
    *,
    enemy: dict[str, Any],
    combat: dict[str, Any],
    state: dict[str, Any],
    can_move: bool,
    event_id: str,
    turn_id: int | None,
) -> dict[str, Any]:
    """Move a retreating enemy one band outward; leaving melee while engaged provokes."""

    enemy_id = str(enemy.get('id') or '')
    band = range_band(enemy)
    if not can_move or band == 'distant':
        return {}
    engaged = engaged_with(enemy)
    reactions = []
    if band == 'melee' and engaged:
        reactions = collect_reactions(
            {'type': 'leaves_melee', 'moverId': enemy_id, 'fromEngagedWith': engaged, 'eventId': f'{event_id}:leaves_melee', 'turnId': turn_id},
            combat,
            state,
        )
    outcome: dict[str, Any] = {'reactions': reactions} if reactions else {}
    if not can_act(participant_by_id(combat, enemy_id)):
        outcome['droppedByReaction'] = True
        return outcome
    outcome['movement'] = _move_enemy(combat, enemy_id, [{'toRangeBand': band_outward(band), 'engageWith': []}])
    return outcome


def _held_turn(enemy: dict[str, Any], combat: dict[str, Any], roll: Callable[[int], int]) -> dict[str, Any]:
    """The turn of an enemy a hero holds, whatever it planned: beaten (a quarter of its hit points or
    less, or no morale) it yields; otherwise it spends its action trying to break free, d20 + its better
    Strength or Dexterity modifier against the grapple's DC."""

    grappler = participant_by_id(combat, grappler_id(enemy))
    held = {'held': True, 'grapplerId': grappler_id(enemy), 'grapplerName': _participant_name(grappler, 'its captor')}
    # The morale the planner recalculated for this turn, as it was stored.
    if at_most_quarter(enemy) or int_or_default(enemy.get('morale'), default=50) <= 0:
        return {**held, 'intentType': 'surrender', 'resolvedWithoutRoll': True}
    natural = bounded_int(roll(20), default=1, minimum=1, maximum=20)
    modifier = max(ability_mod(enemy, 'strength'), ability_mod(enemy, 'dexterity'))
    dc = escape_dc(enemy)
    success = natural + modifier >= dc
    if success:
        remove_active_effects(enemy, lambda effect: effect['sourceId'] == GRAPPLE_SOURCE)
    return {
        **held,
        'intentType': 'break_free',
        'breakFree': {'roll': natural, 'modifier': modifier, 'total': natural + modifier, 'dc': dc, 'success': success},
    }


def _restrained_turn(enemy: dict[str, Any], roll: Callable[[int], int]) -> dict[str, Any] | None:
    """The turn of an enemy a scene object holds (a net, a canvas): it spends its action trying to break free, d20 +
    its better Strength or Dexterity modifier against the hold's DC, as from a grapple."""

    effect = next((effect for effect in active_effects(enemy) if effect['sourceId'] == RESTRAINED_SOURCE), None)
    if effect is None:
        return None
    natural = bounded_int(roll(20), default=1, minimum=1, maximum=20)
    modifier = max(ability_mod(enemy, 'strength'), ability_mod(enemy, 'dexterity'))
    dc = int_or_default(effect.get('escapeDc'), default=12)
    success = natural + modifier >= dc
    if success:
        remove_active_effects(enemy, lambda candidate: candidate['sourceId'] == RESTRAINED_SOURCE)
    return {
        'held': True,
        'restrained': True,
        'restraintId': effect['id'],
        'grapplerName': 'what holds it',
        'intentType': 'break_free',
        'breakFree': {'roll': natural, 'modifier': modifier, 'total': natural + modifier, 'dc': dc, 'success': success},
    }


def _resolve_enemy_required_actions(
    *,
    state: dict[str, Any],
    combat_context: dict[str, Any] | None,
    roller: Callable[[int], int] | None = None,
    turn_id: int | None = None,
) -> list[dict[str, Any]]:
    """Resolve the enemy block in initiative order against one working copy.

    Each enemy begins its turn (reaction back, standing up), closes on a melee
    target or retreats, then rolls; moves, reactions and healing it causes are
    seen by the enemies after it. An enemy a hero holds only yields or tries to
    break free (:func:`_held_turn`); one whose grappler went down or away is
    let go (``grappleEnded``) and acts as planned.
    """

    if not isinstance(combat_context, dict):
        return []
    actions = combat_context.get('enemyRequiredActions') if isinstance(combat_context.get('enemyRequiredActions'), list) else []
    if not actions:
        return []
    if isinstance(combat_context.get('enemyTurnBlock'), list):
        allowed_enemy_ids = {
            str(entry.get('id') if isinstance(entry, dict) else entry or '').strip()
            for entry in combat_context['enemyTurnBlock']
        }
        allowed_enemy_ids.discard('')
        actions = [
            action
            for action in actions
            if isinstance(action, dict)
            and str(action.get('enemyId') or action.get('actorId') or action.get('participantId') or '').strip()
            in allowed_enemy_ids
        ]
        if not actions:
            return []

    working_state = deepcopy(state)
    combat = working_state.get('combat') if isinstance(working_state.get('combat'), dict) else {}
    working_state['combat'] = combat
    roll = roller or _random_roll
    resolved: list[dict[str, Any]] = []
    # Enemy actions are planned before the persisted turn-advance change.  Use
    # the same deterministic turn-start damage on this working copy so an
    # enemy defeated by an effect cannot attack before that change is applied.
    starts = {
        str(boundary.get('actorId')): boundary.get('round')
        for boundary in turn_boundaries(combat, combat_turn_context(combat))
        if boundary.get('phase') == 'start'
    }
    started: set[str] = set()
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        intent_type = str(action.get('intentType') or action.get('type') or '').strip().lower()
        enemy_id = str(action.get('enemyId') or action.get('actorId') or action.get('participantId') or '').strip()
        target_id = str(action.get('targetId') or action.get('targetActorId') or '').strip()
        ability_id = str(action.get('abilityId') or action.get('ability_id') or '').strip()
        enemy = participant_by_id(combat, enemy_id)
        turn_start: dict[str, Any] = {}
        if isinstance(enemy, dict) and enemy_id in starts and enemy_id not in started:
            started.add(enemy_id)
            tick_turn_boundary(combat, actor_id=enemy_id, phase='start')
            hp = enemy.setdefault('hp', {})
            hp['temp'] = max(int_or_default(hp.get('temp'), default=0), temp_hp_per_turn(enemy))
            ongoing_damage = apply_start_turn_damage(combat, enemy, turn_id=turn_id, round_number=starts[enemy_id])
            if ongoing_damage:
                turn_start['ongoingDamage'] = ongoing_damage
            hooked = run_turn_start_hooks(combat, actor_id=enemy_id, turn_id=turn_id, round_number=starts[enemy_id])
            if hooked:
                turn_start['turnStartEffects'] = hooked
        # A turn-start hook may replace the working combat's records.
        enemy = participant_by_id(combat, enemy_id)
        target = participant_by_id(combat, target_id)
        ability = _ability_by_id(enemy, ability_id)
        if (
            not isinstance(enemy, dict)
            or str(enemy.get('team') or '').strip().lower() != 'enemy'
            or not participant_can_take_turn(enemy)
        ):
            if turn_start:
                resolved.append({
                    'enemyId': enemy_id,
                    'enemyName': _participant_name(enemy, enemy_id or 'Enemy'),
                    'resolvedWithoutRoll': True,
                    **turn_start,
                    'turnLost': True,
                    'authoritative': True,
                    'instruction': 'Narrate the turn-start effects and lost turn exactly as resolved by the engine.',
                })
            continue
        ability_name = str((ability or {}).get('name') or action.get('abilityName') or ability_id or '').strip()
        entry: dict[str, Any] = {
            'enemyId': enemy_id,
            'enemyName': _participant_name(enemy, enemy_id or 'Enemy'),
            'targetId': target_id,
            'targetName': _participant_name(target, target_id or 'target'),
            'intentType': intent_type,
            'abilityId': ability_id or ((ability or {}).get('id') if isinstance(ability, dict) else None),
            'abilityName': ability_name or None,
            'sourceIntent': action,
            'authoritative': True,
            'instruction': 'Narrate this enemy result as already resolved by the engine; do not ask the player to roll it.',
            **turn_start,
        }
        event_id = f'enemy_turn:{turn_id}:{index}:{enemy_id}'
        if grapple_effect(enemy) is not None:
            if grapple_holds(enemy, combat):
                _begin_enemy_turn(enemy)
                entry.update(_held_turn(enemy, combat, roll))
                resolved.append(entry)
                continue
            entry['grappleEnded'] = True
            remove_active_effects(enemy, lambda effect: effect['sourceId'] == GRAPPLE_SOURCE)
        restrained = _restrained_turn(enemy, roll)
        if restrained is not None:
            _begin_enemy_turn(enemy)
            entry.update(restrained)
            resolved.append(entry)
            continue
        if intent_type == GOAL_INTENT:
            # What the fight is about (W9-SCENE): the runner's or the silencer's move.
            can_move = _begin_enemy_turn(enemy)
            entry.update(
                resolve_goal_turn(
                    enemy=enemy,
                    combat=combat,
                    state=working_state,
                    roll=roll,
                    retreat=lambda mover: _retreat(enemy=mover, combat=combat, state=working_state, can_move=can_move, event_id=event_id, turn_id=turn_id),
                )
            )
            resolved.append(entry)
            continue

        if intent_type in {'attack', 'use_ability'}:
            if not isinstance(ability, dict) or not combat_ability_is_available(ability, round_number=combat.get('round')):
                continue
            resolution_mode = combat_ability_resolution_mode(ability)
            if resolution_mode == 'heal':
                _begin_enemy_turn(enemy)
                healing = resolve_enemy_healing(enemy=enemy, ability=ability, combat=combat, roller=roll)
                if healing is None:
                    continue
                healed = participant_by_id(combat, healing['healTargetId'])
                if healed is not None:
                    healed['hp'] = dict(healing['resultingHp'])
                entry.update(healing)
                entry['targetId'] = healing['healTargetId']
                entry['targetName'] = healing['healTargetName']
                resolved.append(entry)
                continue
            if (
                not resolution_mode
                or not isinstance(target, dict)
                or str(target.get('team') or '').strip().lower() not in {'player', 'ally'}
                or (resolution_mode in {'attack', 'attack_with_save'} and not ability_damage(ability).get('dice'))
            ):
                continue
            can_move = _begin_enemy_turn(enemy)
            approach = _enemy_reach(enemy, target, ability, combat, can_move=can_move)
            if not approach['reachable']:
                continue
            movement = _move_enemy(combat, enemy_id, approach['steps'])
            if movement:
                entry['movement'] = movement
            if not approach['attack']:
                entry.update({'resolvedWithoutRoll': True, 'moveOnly': True})
                resolved.append(entry)
                continue
            result = resolve_enemy_ability(
                enemy=enemy,
                target=target,
                ability=ability,
                combat=combat,
                state=working_state,
                roller=roll,
                event_id=event_id,
                turn_id=turn_id,
            )
            if result is None:
                continue
            entry.update(result)
            if (enemy.get('currentIntent') or {}).get('wrestStakeId'):
                wrest_after_hit(enemy=enemy, target=target, combat=combat, state=working_state, result=entry, roll=roll)
        elif intent_type in {'retreat', 'flee'}:
            can_move = _begin_enemy_turn(enemy)
            entry.update(_retreat(enemy=enemy, combat=combat, state=working_state, can_move=can_move, event_id=event_id, turn_id=turn_id))
            entry['resolvedWithoutRoll'] = True
        else:
            _begin_enemy_turn(enemy)
            entry['resolvedWithoutRoll'] = True
        resolved.append(entry)
    return resolved


def build_dm_combat_context(
    *,
    state: dict[str, Any],
    combat_context: dict[str, Any] | None,
    pending_rolls: list[dict[str, Any]],
    resolved_player_roll: bool,
    enemy_roller: Callable[[int], int] | None = None,
    turn_id: int | None = None,
) -> dict[str, Any] | None:
    if not isinstance(combat_context, dict):
        return None
    context = deepcopy(combat_context)
    if pending_rolls or resolved_player_roll:
        context['enemyRequiredActions'] = []
        context['enemyIntentSummary'] = ''
        context['enemyTelegraphs'] = []
        context['enemyResolvedActions'] = []
        context['enemyActionDeferredReason'] = 'pending_player_roll' if pending_rolls else 'player_roll_resolution'
        return context
    resolved_actions = _resolve_enemy_required_actions(state=state, combat_context=context, roller=enemy_roller, turn_id=turn_id)
    requested_count = len(context.get('enemyRequiredActions') or [])
    # Once the engine has attempted resolution, narration may consume only the
    # validated results.  Leaving unresolved requests in context would let the
    # model narrate mechanics the engine deliberately rejected.
    context['enemyRequiredActions'] = []
    context['enemyResolvedActions'] = resolved_actions
    if requested_count > len(resolved_actions):
        context['enemyResolutionBlockedCount'] = requested_count - len(resolved_actions)
    if turn_id is not None:
        # The ending turn passes the initiative slots of downed characters: each
        # dying one rolls its death save now so narration and the log carry it.
        context['deathSaves'] = death_saves_due(state.get('combat'), turn_id=turn_id)
    return context


def _player_actors_by_id(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    actors: dict[str, dict[str, Any]] = {}
    for actor in state.get('playerCharacters') or []:
        if not isinstance(actor, dict):
            continue
        actor_id = str(actor.get('id') or '').strip()
        if actor_id:
            actors[actor_id] = actor
    return actors


def _enemy_movement_changes(action: dict[str, Any], enemy: dict[str, Any], *, turn_id: int | None, index: int) -> list[dict[str, Any]]:
    enemy_id = str(enemy.get('id') or '')
    changes: list[dict[str, Any]] = []
    for step_index, step in enumerate(action.get('movement') or []):
        if not isinstance(step, dict):
            continue
        change = {
            'id': stable_change_id('trusted_enemy_movement', turn_id, index, enemy_id, step_index, step.get('fromRangeBand'), step.get('toRangeBand')),
            'turnId': turn_id,
            'type': 'combat.move',
            'participantId': enemy_id,
            'fromRangeBand': step.get('fromRangeBand'),
            'toRangeBand': step.get('toRangeBand'),
            'source': ENEMY_RESOLVED_SOURCE,
            'sourceEnemyId': enemy_id,
            'reason': f"Engine-resolved enemy movement: {_participant_name(enemy, enemy_id)} moved from {step.get('fromRangeBand')} to {step.get('toRangeBand')} range.",
            'visible': True,
        }
        if step.get('engageWith'):
            change['engageWith'] = list(step['engageWith'])
        changes.append(change)
    return changes


def _enemy_heal_change(
    action: dict[str, Any],
    enemy: dict[str, Any],
    *,
    participants: list[dict[str, Any]],
    turn_id: int | None,
    index: int,
) -> dict[str, Any] | None:
    # ``resultingHp`` was computed on the enemy block's working copy, so several
    # heals on one ally in the same block accumulate.
    target_id = str(action.get('healTargetId') or '').strip()
    target = _participant_by_id(participants, target_id)
    resulting_hp = action.get('resultingHp') if isinstance(action.get('resultingHp'), dict) else {}
    if not isinstance(target, dict) or target.get('team') != enemy.get('team') or int_or_default(action.get('healingTotal'), default=0) <= 0:
        return None
    hp = target.get('hp') if isinstance(target.get('hp'), dict) else {}
    current = max(0, int_or_default(hp.get('current'), default=0))
    maximum = max(current, int_or_default(hp.get('max'), default=current))
    resulting = max(current, min(maximum, int_or_default(resulting_hp.get('current'), default=current)))
    return {
        'id': stable_change_id('trusted_enemy_healing', turn_id, index, enemy.get('id'), target_id, resulting),
        'turnId': turn_id,
        'type': 'combat.participant.update',
        'participantId': target_id,
        'hp': {'current': resulting, 'max': maximum, 'temp': max(0, int_or_default(hp.get('temp'), default=0))},
        'isAlive': resulting > 0,
        'isConscious': resulting > 0,
        'source': ENEMY_RESOLVED_SOURCE,
        'sourceEnemyId': str(enemy.get('id') or ''),
        'reason': f"Engine-resolved enemy healing: {_participant_name(enemy, str(enemy.get('id') or ''))} restored {resulting - current} HP to {_participant_name(target, target_id)}.",
        'visible': True,
    }


def _trusted_enemy_resolved_damage_changes(
    *,
    state: dict[str, Any],
    dm_context_packet: dict[str, Any],
    turn_id: int | None,
    already_applied_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    combat_state = dm_context_packet.get('combatState') if isinstance(dm_context_packet.get('combatState'), dict) else {}
    resolved_actions = combat_state.get('enemyResolvedActions') if isinstance(combat_state.get('enemyResolvedActions'), list) else []
    if not resolved_actions:
        return []

    players_by_actor_id = _player_actors_by_id(state)
    if not players_by_actor_id:
        return []
    participants = _combat_participants(state)
    already_signatures = {
        signature
        for change in already_applied_changes
        if isinstance(change, dict)
        for signature in [_damage_change_signature(change)]
        if signature
    }
    already_ids = {
        str(change.get('id') or '').strip()
        for change in already_applied_changes
        if isinstance(change, dict) and str(change.get('id') or '').strip()
    }

    changes: list[dict[str, Any]] = []

    def append(change: dict[str, Any]) -> None:
        change_id = str(change.get('id') or '').strip()
        if change_id and change_id not in already_ids:
            changes.append(change)
            already_ids.add(change_id)

    for index, action in enumerate(resolved_actions):
        if not isinstance(action, dict):
            continue
        enemy_id = str(action.get('enemyId') or action.get('actorId') or '').strip()
        intent_type = str(action.get('intentType') or action.get('type') or '').strip().lower()
        enemy = _participant_by_id(participants, enemy_id)
        if (
            not isinstance(enemy, dict)
            or str(enemy.get('team') or '').strip().lower() != 'enemy'
            or not participant_can_take_turn(enemy)
        ):
            continue
        if action.get('restrained') is True:
            if (action.get('breakFree') or {}).get('success') is True:
                append(_restraint_released_change(enemy, action, turn_id=turn_id, index=index))
            continue
        if action.get('grappleEnded') or (action.get('breakFree') or {}).get('success') is True:
            append(_grapple_released_change(enemy, turn_id=turn_id, index=index))
        if intent_type == 'break_free':
            continue
        for change in _enemy_movement_changes(action, enemy, turn_id=turn_id, index=index):
            append(change)
        for change in reaction_changes(action.get('reactions')):
            append(change)
        for change in stake_changes(action, turn_id=turn_id, index=index):
            append(change)
        if action.get('droppedByReaction') or action.get('moveOnly') or intent_type == GOAL_INTENT:
            continue

        outcome_condition = {
            'retreat': 'fled',
            'flee': 'fled',
            'surrender': 'surrendered',
            'negotiate': 'negotiated',
        }.get(intent_type)
        if outcome_condition == 'fled' and 'turned' in condition_keys(enemy):
            # A turned creature only moves away; it rejoins the fight when the turning ends.
            continue
        if outcome_condition:
            change = {
                'id': stable_change_id(
                    'trusted_enemy_resolved_outcome',
                    turn_id,
                    index,
                    enemy_id,
                    intent_type,
                    outcome_condition,
                ),
                'turnId': turn_id,
                'type': 'combat.condition.add',
                'participantId': enemy_id,
                'condition': outcome_condition,
                'source': 'enemy_resolved_action',
                'sourceEnemyId': enemy_id,
                'reason': f"Engine-resolved enemy action: {_participant_name(enemy, enemy_id)} chose to {intent_type}.",
                'visible': True,
            }
            if str(change.get('id') or '') not in already_ids:
                changes.append(change)
                already_ids.add(str(change['id']))
            continue

        ability_id = str(action.get('abilityId') or '').strip()
        ability = _ability_by_id(enemy, ability_id)
        if intent_type in {'attack', 'use_ability'}:
            combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
            if (
                not ability_id
                or not isinstance(ability, dict)
                or not combat_ability_is_available(ability, round_number=combat.get('round'))
            ):
                continue
            mark_used = {
                'id': stable_change_id(
                    'trusted_enemy_ability_used',
                    turn_id,
                    index,
                    enemy_id,
                    ability_id,
                ),
                'turnId': turn_id,
                'type': 'combat.ability.mark_used',
                'participantId': enemy_id,
                'abilityId': ability_id,
                'source': 'enemy_resolved_action',
                'sourceEnemyId': enemy_id,
                'reason': f"Engine-resolved enemy action consumed {_participant_name(enemy, enemy_id)}'s {str(ability.get('name') or ability_id)} ability.",
                'visible': False,
            }
            if str(mark_used['id']) not in already_ids:
                changes.append(mark_used)
                already_ids.add(str(mark_used['id']))
            consume_change = _consume_change(
                action.get('consume'),
                turn_id=turn_id,
                origin=('trusted_enemy_modifiers_consumed', index, enemy_id, ability_id),
            )
            if consume_change and consume_change['id'] not in already_ids:
                changes.append(consume_change)
                already_ids.add(consume_change['id'])
            if action.get('resolutionMode') == 'heal':
                heal_change = _enemy_heal_change(action, enemy, participants=participants, turn_id=turn_id, index=index)
                if heal_change:
                    append(heal_change)
                continue

        target_id = str(action.get('targetId') or action.get('targetActorId') or '').strip()
        target_participant = _participant_by_id(participants, target_id)
        if (
            target_id not in players_by_actor_id
            or not isinstance(target_participant, dict)
            or str(target_participant.get('team') or '').strip().lower() != 'player'
            or not participant_is_targetable(target_participant)
        ):
            continue
        enemy_name = _participant_name(enemy, enemy_id)
        target_name = _participant_name(target_participant, str(players_by_actor_id[target_id].get('name') or target_id))

        for condition in action.get('conditionsApplied') or []:
            normalized_condition = re.sub(r'[^a-z0-9]+', '_', str(condition or '').strip().lower()).strip('_')
            if not normalized_condition:
                continue
            condition_change = {
                'id': stable_change_id(
                    'trusted_enemy_resolved_condition',
                    turn_id,
                    index,
                    enemy_id,
                    target_id,
                    ability_id,
                    normalized_condition,
                ),
                'turnId': turn_id,
                'type': 'combat.condition.add',
                'participantId': target_id,
                'condition': normalized_condition,
                'source': 'enemy_resolved_action',
                'sourceEnemyId': enemy_id,
                'sourceAbilityId': ability_id or None,
                **condition_duration_fields(
                    ability if isinstance(ability, dict) else {},
                    cast_id=stable_change_id('trusted_enemy_resolved_conditions', turn_id, index, enemy_id, target_id, ability_id),
                    applied_during_turn_of=enemy_id,
                ),
                'reason': f"Engine-resolved enemy action: {enemy_name} applied {normalized_condition} to {target_name}.",
                'visible': True,
            }
            if str(condition_change['id']) not in already_ids:
                changes.append(condition_change)
                already_ids.add(str(condition_change['id']))

        damage_total = max(0, int_or_default(action.get('damageTotal'), default=0))
        if damage_total <= 0 or not enemy_id:
            continue
        damage_type = str(action.get('damageType') or '').strip().lower()
        change = {
            'id': stable_change_id(
                'trusted_enemy_resolved_damage',
                turn_id,
                index,
                enemy_id,
                target_id,
                ability_id,
                damage_total,
            ),
            'turnId': turn_id,
            'type': 'health.damage',
            'actorId': target_id,
            'amount': damage_total,
            'source': 'enemy_resolved_action',
            'sourceEnemyId': enemy_id,
            'sourceAbilityId': ability_id or None,
            'reason': f"Engine-resolved enemy action: {enemy_name} hit {target_name} for {damage_total} damage.",
            'visible': True,
        }
        if damage_type:
            change['damageType'] = damage_type
        if action.get('critical') is True:
            change['critical'] = True
        if str(change.get('id') or '') in already_ids:
            continue
        signature = _damage_change_signature(change)
        if signature and signature in already_signatures:
            continue
        changes.append(change)
        already_ids.add(str(change['id']))
    return changes


def _restraint_released_change(enemy: dict[str, Any], action: dict[str, Any], *, turn_id: int | None, index: int) -> dict[str, Any]:
    enemy_id = str(enemy.get('id') or '')
    return {
        'id': stable_change_id('trusted_enemy_restraint_released', turn_id, index, enemy_id),
        'turnId': turn_id,
        'type': 'combat.effect.consume',
        'consume': [{'participantId': enemy_id, 'effectId': action.get('restraintId')}],
        'source': MODIFIER_ENGINE_SOURCE,
        'reason': f'{_participant_name(enemy, enemy_id)} breaks free.',
        'visible': True,
    }


def _grapple_released_change(enemy: dict[str, Any], *, turn_id: int | None, index: int) -> dict[str, Any]:
    enemy_id = str(enemy.get('id') or '')
    return {
        'id': stable_change_id('trusted_enemy_grapple_released', turn_id, index, enemy_id),
        'turnId': turn_id,
        'type': 'combat.effect.consume',
        'consume': [{'participantId': enemy_id, 'effectId': (grapple_effect(enemy) or {}).get('id')}],
        'source': MODIFIER_ENGINE_SOURCE,
        'reason': f'{_participant_name(enemy, enemy_id)} is no longer held.',
        'visible': True,
    }


def _enemy_turn_history_changes(
    *,
    state: dict[str, Any],
    dm_context_packet: dict[str, Any],
    turn_id: int | None,
    already_applied_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Records, per enemy that took its turn, whether that turn did nothing mechanically (the quiet-turn limit reads it)."""

    combat_state = dm_context_packet.get('combatState') if isinstance(dm_context_packet.get('combatState'), dict) else {}
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    participants = _combat_participants(state)
    quiet_turns: dict[str, bool] = {}
    for action in combat_state.get('enemyResolvedActions') if isinstance(combat_state.get('enemyResolvedActions'), list) else []:
        if not isinstance(action, dict) or action.get('turnLost'):
            continue
        enemy_id = str(action.get('enemyId') or action.get('actorId') or '').strip()
        enemy = _participant_by_id(participants, enemy_id)
        if isinstance(enemy, dict) and str(enemy.get('team') or '').strip().lower() == 'enemy':
            intent_type = str(action.get('intentType') or action.get('type') or '').strip().lower()
            quiet_turns[enemy_id] = intent_is_quiet({'intentType': intent_type}, enemy, combat)
    if not quiet_turns:
        return []
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    history = deepcopy(flags.get(ENEMY_TURN_HISTORY_FLAG)) if isinstance(flags.get(ENEMY_TURN_HISTORY_FLAG), dict) else {}
    for enemy_id, quiet in quiet_turns.items():
        history[enemy_id] = [*(history.get(enemy_id) or []), quiet][-QUIET_TURN_WINDOW:]
    change = {
        'id': stable_change_id('trusted_enemy_turn_history', turn_id, *sorted(quiet_turns.items())),
        'turnId': turn_id,
        'type': 'combat.update',
        'flags': {ENEMY_TURN_HISTORY_FLAG: history},
        'source': ENEMY_TURN_HISTORY_SOURCE,
        'reason': 'Engine-recorded enemy turns.',
        'visible': False,
    }
    already_ids = {str(applied.get('id') or '') for applied in already_applied_changes if isinstance(applied, dict)}
    return [] if change['id'] in already_ids else [change]


def _consume_change(consume: Any, *, turn_id: int | None, origin: tuple[Any, ...]) -> dict[str, Any] | None:
    """One trusted change spending the effects (and ``hidden``) a resolved roll used."""

    entries = [
        {key: str(entry.get(key)) for key in ('participantId', 'effectId', 'condition') if entry.get(key)}
        for entry in (consume if isinstance(consume, list) else [])
        if isinstance(entry, dict) and entry.get('participantId')
    ]
    if not entries:
        return None
    return {
        'id': stable_change_id(*origin, turn_id),
        'turnId': turn_id,
        'type': 'combat.effect.consume',
        'consume': entries,
        'source': MODIFIER_ENGINE_SOURCE,
        'reason': 'Effects spent by an engine-resolved roll.',
        'visible': False,
    }


def _trusted_death_save_changes(
    *,
    state: dict[str, Any],
    dm_context_packet: dict[str, Any],
    turn_id: int | None,
    already_applied_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    combat_state = dm_context_packet.get('combatState') if isinstance(dm_context_packet.get('combatState'), dict) else {}
    already_ids = {
        str(change.get('id') or '').strip()
        for change in already_applied_changes
        if isinstance(change, dict)
    }
    participants = _combat_participants(state)
    changes: list[dict[str, Any]] = []
    for save in combat_state.get('deathSaves') or []:
        if not isinstance(save, dict):
            continue
        participant_id = str(save.get('participantId') or '').strip()
        participant = _participant_by_id(participants, participant_id)
        if not isinstance(participant, dict):
            continue
        round_number = int_or_default(save.get('round'), default=1)
        change = {
            'id': death_save_change_id(turn_id, participant_id, round_number),
            'turnId': turn_id,
            'type': 'combat.death_save',
            'participantId': participant_id,
            'actorId': participant_id,
            'round': round_number,
            'roll': int_or_default(save.get('roll'), default=0),
            'source': DEATH_SAVE_SOURCE,
            'reason': (
                f"Death saving throw: {_participant_name(participant, participant_id)} rolled "
                f"{save.get('roll')} ({str(save.get('result') or '').replace('_', ' ')}); "
                f"{save.get('successes')} success, {save.get('failures')} failure."
            ),
            'visible': True,
        }
        if change['id'] not in already_ids:
            changes.append(change)
    return changes


def _battlefield_hazards_by_id(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), dict) else {}
    hazards = battlefield.get('hazards') if isinstance(battlefield.get('hazards'), list) else []
    by_id: dict[str, dict[str, Any]] = {}
    for hazard in hazards:
        if not isinstance(hazard, dict):
            continue
        hazard_id = str(hazard.get('id') or hazard.get('hazardId') or '').strip()
        if hazard_id:
            by_id[hazard_id] = hazard
    return by_id


def _trusted_damage_events(dm_context_packet: dict[str, Any]) -> list[dict[str, Any]]:
    combat_state = dm_context_packet.get('combatState') if isinstance(dm_context_packet.get('combatState'), dict) else {}
    events: list[dict[str, Any]] = []
    for source in (dm_context_packet.get('trustedDamageEvents'), combat_state.get('trustedDamageEvents')):
        if isinstance(source, list):
            events.extend(event for event in source if isinstance(event, dict))
    return events


def _trusted_resolved_damage_changes(
    *,
    state: dict[str, Any],
    dm_context_packet: dict[str, Any],
    actor_id: str,
    turn_id: int | None,
    already_applied_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events = _trusted_damage_events(dm_context_packet)
    if not events:
        return []

    players_by_actor_id = _player_actors_by_id(state)
    if not players_by_actor_id:
        return []
    participants = _combat_participants(state)
    hazards_by_id = _battlefield_hazards_by_id(state)
    already_signatures = {
        signature
        for change in already_applied_changes
        if isinstance(change, dict)
        for signature in [_damage_change_signature(change)]
        if signature
    }
    already_ids = {
        str(change.get('id') or '').strip()
        for change in already_applied_changes
        if isinstance(change, dict) and str(change.get('id') or '').strip()
    }

    changes: list[dict[str, Any]] = []
    expected_actor_id = str(actor_id or '').strip()
    for index, damage_event in enumerate(events):
        source_type = str(damage_event.get('sourceType') or damage_event.get('source_type') or '').strip().lower()
        source = _TRUSTED_DAMAGE_SOURCE_TYPES.get(source_type)
        if not source:
            continue
        if damage_event.get('hit') is False:
            continue
        damage_total = max(
            0,
            int_or_default(
                damage_event.get('damageTotal', damage_event.get('damageAmount', damage_event.get('amount'))),
                default=0,
            ),
        )
        target_id = str(damage_event.get('targetId') or damage_event.get('targetActorId') or '').strip()
        if damage_total <= 0 or not target_id:
            continue

        source_id = ''
        source_name = ''
        if source == 'trusted_player_attack':
            source_id = str(damage_event.get('sourceActorId') or damage_event.get('actorId') or '').strip()
            if not source_id or source_id != expected_actor_id or source_id not in players_by_actor_id:
                continue
            source_participant = _participant_by_id(participants, source_id)
            if isinstance(source_participant, dict) and str(source_participant.get('team') or '').strip().lower() != 'player':
                continue
            source_name = _participant_name(source_participant, str(players_by_actor_id[source_id].get('name') or source_id))
        else:
            source_id = str(damage_event.get('hazardId') or damage_event.get('sourceId') or '').strip()
            hazard = hazards_by_id.get(source_id)
            if not source_id or not isinstance(hazard, dict):
                continue
            source_name = str(damage_event.get('hazardName') or hazard.get('name') or source_id).strip()

        target_participant = _participant_by_id(participants, target_id)
        target_team = str((target_participant or {}).get('team') or '').strip().lower()
        if source == 'trusted_player_attack':
            if not isinstance(target_participant, dict) or target_team not in {'enemy', 'player'}:
                continue
        elif target_id not in players_by_actor_id or (target_participant is not None and target_team != 'player'):
            continue
        target_name = _participant_name(
            target_participant,
            str((players_by_actor_id.get(target_id) or {}).get('name') or target_id),
        )
        damage_type = str(damage_event.get('damageType') or damage_event.get('damage_type') or '').strip().lower()
        reason = (
            f"Engine-resolved player attack: {source_name} hit {target_name} for {damage_total} damage."
            if source == 'trusted_player_attack'
            else f"Engine-resolved hazard: {source_name} damaged {target_name} for {damage_total} damage."
        )
        change: dict[str, Any] = {
            'id': stable_change_id(
                'trusted_resolved_damage',
                source,
                turn_id,
                index,
                source_id,
                target_id,
                damage_total,
                damage_type,
            ),
            'turnId': turn_id,
            'source': source,
            'sourceId': source_id,
            'reason': reason,
            'visible': True,
        }
        if source == 'trusted_player_attack' and target_team == 'enemy':
            hp = target_participant.get('hp') if isinstance(target_participant.get('hp'), dict) else {}
            current_hp = max(0, int_or_default(hp.get('current'), default=0))
            max_hp = max(current_hp, int_or_default(hp.get('max'), default=current_hp))
            temp_hp = max(0, int_or_default(hp.get('temp'), default=0))
            absorbed = min(temp_hp, damage_total)
            remaining_damage = max(0, damage_total - absorbed)
            resulting_hp = max(0, current_hp - remaining_damage)
            change.update(
                {
                    'type': 'combat.participant.update',
                    'participantId': target_id,
                    'hp': {
                        'current': resulting_hp,
                        'max': max_hp,
                        'temp': temp_hp - absorbed,
                    },
                    'isAlive': resulting_hp > 0,
                    'isConscious': resulting_hp > 0,
                    'damageAmount': damage_total,
                }
            )
        else:
            change.update(
                {
                    'type': 'health.damage',
                    'actorId': target_id,
                    'amount': damage_total,
                }
            )
            if damage_event.get('critical') is True:
                change['critical'] = True
        if damage_type:
            change['damageType'] = damage_type
        if str(change.get('id') or '') in already_ids:
            continue
        signature = _damage_change_signature(change)
        if signature and signature in already_signatures:
            continue
        changes.append(change)
    return changes


def derive_trusted_damage_changes(
    *,
    state: dict[str, Any],
    dm_context_packet: dict[str, Any],
    actor_id: str,
    turn_id: int | None,
    already_applied_changes: list[dict[str, Any]],
) -> TrustedDamageChanges:
    return TrustedDamageChanges(
        enemy=_trusted_enemy_resolved_damage_changes(
            state=state,
            dm_context_packet=dm_context_packet,
            turn_id=turn_id,
            already_applied_changes=already_applied_changes,
        ),
        resolved=[
            *_trusted_resolved_damage_changes(
                state=state,
                dm_context_packet=dm_context_packet,
                actor_id=actor_id,
                turn_id=turn_id,
                already_applied_changes=already_applied_changes,
            ),
            *_trusted_player_consume_changes(
                dm_context_packet=dm_context_packet,
                actor_id=actor_id,
                turn_id=turn_id,
                already_applied_changes=already_applied_changes,
            ),
            *_trusted_player_reaction_changes(
                dm_context_packet=dm_context_packet,
                actor_id=actor_id,
                already_applied_changes=already_applied_changes,
            ),
            *_trusted_player_attack_followups(
                state=state,
                dm_context_packet=dm_context_packet,
                actor_id=actor_id,
                turn_id=turn_id,
                already_applied_changes=already_applied_changes,
            ),
        ],
        death_saves=_trusted_death_save_changes(
            state=state,
            dm_context_packet=dm_context_packet,
            turn_id=turn_id,
            already_applied_changes=already_applied_changes,
        ),
        turn_history=_enemy_turn_history_changes(
            state=state,
            dm_context_packet=dm_context_packet,
            turn_id=turn_id,
            already_applied_changes=already_applied_changes,
        ),
    )


def settle_engine_controlled_turns(
    state: dict[str, Any],
    *,
    turn_id: Any,
    already_applied_changes: list[dict[str, Any]],
) -> EngineTurnSettlement:
    """Play the turns of engine-controlled participants until a player character can act.

    Each waiting block (enemies, or a non-player ally) is planned by the
    deterministic intent planner and resolved like the enemy block after a
    player's turn; its results are trusted changes.  The combat ends when they
    decide it; otherwise initiative is handed on.  ``turn_id`` scopes the change
    ids and the engine's seeded rolls.
    """

    changes: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    resolved_actions: list[dict[str, Any]] = []
    validation: dict[str, list[dict[str, Any]]] = {'accepted': [], 'modified': [], 'rejected': []}

    def apply(step_changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nonlocal state
        step_validation = validate_state_changes(state=state, changes=step_changes)
        result = apply_state_changes(state, validated_changes_for_application(step_validation))
        state = result['nextState']
        changes.extend(step_changes)
        applied.extend(result['appliedChanges'])
        for key, entries in validation.items():
            entries.extend(entry for entry in step_validation.get(key) or [] if isinstance(entry, dict))
        return result['appliedChanges']

    def end_if_decided() -> bool:
        reason = check_combat_end(state.get('combat') if isinstance(state.get('combat'), dict) else {})
        if reason:
            apply([combat_end_change(turn_id, reason)])
        return bool(reason)

    # Every block hands initiative at least one slot on, so a player character
    # is reached within one block per participant.
    for _block in range(len((state.get('combat') or {}).get('participants') or [])):
        combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
        if not engine_controlled_turn_is_up(combat):
            break
        block = build_dm_combat_context(
            state=state,
            combat_context=combat_summary_for_dm(attach_intents_to_combat(combat, deterministic_enemy_intent_plan(combat))),
            pending_rolls=[],
            resolved_player_roll=False,
            turn_id=turn_id,
        ) or {}
        resolved_actions.extend(block.get('enemyResolvedActions') or [])
        apply(
            derive_trusted_damage_changes(
                state=state,
                dm_context_packet={'combatState': block},
                actor_id='',
                turn_id=turn_id,
                already_applied_changes=[*already_applied_changes, *applied],
            ).all_changes
        )
        if end_if_decided():
            break
        advance = engine_turn_advance_change(state, turn_id=turn_id)
        # Turn-start effects of the advance can decide the fight as well.
        if advance is None or not apply([advance]) or end_if_decided():
            break
    return EngineTurnSettlement(
        state=state,
        changes=changes,
        validation=validation,
        applied_changes=applied,
        resolved_actions=resolved_actions,
    )


def _trusted_player_consume_changes(
    *,
    dm_context_packet: dict[str, Any],
    actor_id: str,
    turn_id: int | None,
    already_applied_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    already_ids = {str(change.get('id') or '').strip() for change in already_applied_changes if isinstance(change, dict)}
    changes: list[dict[str, Any]] = []
    for index, event in enumerate(_trusted_damage_events(dm_context_packet)):
        source_type = str(event.get('sourceType') or '').strip().lower()
        if _TRUSTED_DAMAGE_SOURCE_TYPES.get(source_type) != 'trusted_player_attack':
            continue
        if str(event.get('sourceActorId') or '').strip() != str(actor_id or '').strip():
            continue
        change = _consume_change(
            event.get('consume'),
            turn_id=turn_id,
            origin=('trusted_player_modifiers_consumed', index, actor_id, event.get('targetId'), event.get('weaponId')),
        )
        if change and change['id'] not in already_ids and change['id'] not in {item['id'] for item in changes}:
            changes.append(change)
    return changes


def _trusted_player_reaction_changes(
    *,
    dm_context_packet: dict[str, Any],
    actor_id: str,
    already_applied_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The changes of reactions a player attack provoked (spent reactions and their effects)."""

    seen = {str(change.get('id') or '').strip() for change in already_applied_changes if isinstance(change, dict)}
    changes: list[dict[str, Any]] = []
    for event in _trusted_damage_events(dm_context_packet):
        if _TRUSTED_DAMAGE_SOURCE_TYPES.get(str(event.get('sourceType') or '').strip().lower()) != 'trusted_player_attack':
            continue
        if str(event.get('sourceActorId') or '').strip() != str(actor_id or '').strip():
            continue
        for change in reaction_changes(event.get('reactions')):
            change_id = str(change.get('id') or '').strip()
            if change_id and change_id not in seen:
                seen.add(change_id)
                changes.append(change)
    return changes


def _trusted_player_attack_followups(
    *,
    state: dict[str, Any],
    dm_context_packet: dict[str, Any],
    actor_id: str,
    turn_id: int | None,
    already_applied_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """What a resolved player hit spends: its declared rider (Divine Smite's slot, Stunning
    Strike's ki and save) and the once-per-turn damage it used (Sneak Attack, Colossus Slayer)."""

    seen = {str(change.get('id') or '').strip() for change in already_applied_changes if isinstance(change, dict)}
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    economy = flags.get('turnEconomy') if isinstance(flags.get('turnEconomy'), dict) else {}
    changes: list[dict[str, Any]] = []
    for event in _trusted_damage_events(dm_context_packet):
        if (
            _TRUSTED_DAMAGE_SOURCE_TYPES.get(str(event.get('sourceType') or '').strip().lower()) != 'trusted_player_attack'
            or str(event.get('sourceActorId') or '').strip() != str(actor_id or '').strip()
            or event.get('hit') is not True
        ):
            continue
        target_id = str(event.get('targetId') or '').strip()
        followups: list[dict[str, Any]] = []
        if isinstance(event.get('rider'), dict):
            rider_change = attack_rider_change(
                state,
                actor_id=actor_id,
                rider=event['rider'],
                target_id=target_id,
                hit=True,
                turn_id=turn_id,
                roller=deterministic_roller(f'attack_rider:{turn_id}:{actor_id}:{target_id}'),
            )
            if rider_change:
                followups.append(rider_change)
        used = [str(key) for key in event.get('oncePerTurn') or [] if str(key or '').strip()]
        if used and str(economy.get('actorId') or '') == str(actor_id):
            once_per_turn = list(dict.fromkeys([*(economy.get('oncePerTurnUsed') or []), *used]))
            followups.append({
                'id': stable_change_id('trusted_once_per_turn', turn_id, actor_id, *once_per_turn),
                'turnId': turn_id,
                'type': 'combat.update',
                'flags': {'turnEconomy': {**deepcopy(economy), 'oncePerTurnUsed': once_per_turn}},
                'source': MODIFIER_ENGINE_SOURCE,
                'reason': f"Once-per-turn damage used this turn: {', '.join(once_per_turn)}.",
                'visible': False,
            })
        for change in followups:
            if change['id'] not in seen:
                seen.add(change['id'])
                changes.append(change)
    return changes


def without_trusted_damage_overlaps(
    changes: list[dict[str, Any]],
    trusted_changes: list[dict[str, Any]],
    *,
    dm_context_packet: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    trusted_ids = {str(change.get('id') or '').strip() for change in trusted_changes if isinstance(change, dict)}
    trusted_signatures = {
        signature
        for change in trusted_changes
        if isinstance(change, dict)
        for signature in [_damage_change_signature(change)]
        if signature
    }
    trusted_ability_uses = {
        (
            str(change.get('participantId') or '').strip(),
            str(change.get('abilityId') or '').strip(),
        )
        for change in trusted_changes
        if (
            isinstance(change, dict)
            and str(change.get('type') or '').strip() == 'combat.ability.mark_used'
        )
    }
    combat_state = (
        dm_context_packet.get('combatState')
        if isinstance(dm_context_packet, dict) and isinstance(dm_context_packet.get('combatState'), dict)
        else {}
    )
    resolved_player_action = (
        combat_state.get('playerResolvedAction')
        if isinstance(combat_state.get('playerResolvedAction'), dict)
        else {}
    )
    protected_target_ids = {
        str(resolved.get('targetId') or '').strip()
        for resolved in (resolved_player_action, combat_state.get('playerResolvedCombatAction'))
        if isinstance(resolved, dict) and resolved.get('authoritative') is True
    }
    for source in (
        combat_state.get('enemyResolvedActions'),
        combat_state.get('trustedDamageEvents'),
        dm_context_packet.get('trustedDamageEvents') if isinstance(dm_context_packet, dict) else None,
    ):
        for outcome in source if isinstance(source, list) else []:
            if not isinstance(outcome, dict):
                continue
            target_id = str(outcome.get('targetId') or outcome.get('targetActorId') or '').strip()
            if target_id:
                protected_target_ids.add(target_id)
            enemy_id = str(outcome.get('enemyId') or '').strip()
            if enemy_id:
                protected_target_ids.add(enemy_id)
    protected_target_ids.discard('')
    trusted_state_changes = (
        dm_context_packet.get('trustedStateChanges')
        if isinstance(dm_context_packet, dict) and isinstance(dm_context_packet.get('trustedStateChanges'), list)
        else []
    )
    engine_ended_combat = any(
        isinstance(change, dict) and str(change.get('type') or '').strip() == 'combat.end'
        for change in trusted_state_changes
    )
    filtered: list[dict[str, Any]] = []
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        change_id = str(change.get('id') or '').strip()
        if change_id and change_id in trusted_ids:
            continue
        signature = _damage_change_signature(change)
        if signature and signature in trusted_signatures:
            continue
        change_type = str(change.get('type') or '').strip()
        if (engine_ended_combat or protected_target_ids) and change_type == 'combat.end':
            continue
        participant_id = str(change.get('participantId') or change.get('participant_id') or '').strip()
        actor_id = str(change.get('actorId') or change.get('actor_id') or '').strip()
        ability_id = str(change.get('abilityId') or change.get('ability_id') or '').strip()
        if change_type == 'combat.ability.mark_used' and (participant_id, ability_id) in trusted_ability_uses:
            continue
        if participant_id in protected_target_ids and change_type == 'combat.participant.update':
            replacement = change.get('participant') if isinstance(change.get('participant'), dict) else {}
            protected_fields = {'hp', 'conditions', 'isAlive', 'isConscious'}
            if protected_fields.intersection(change) or protected_fields.intersection(replacement):
                continue
        if participant_id in protected_target_ids and change_type in {'combat.condition.add', 'combat.condition.remove', 'combat.move'}:
            continue
        if actor_id in protected_target_ids and change_type in {'health.damage', 'health.heal', 'health.max.set'}:
            continue
        filtered.append(change)
    return filtered
