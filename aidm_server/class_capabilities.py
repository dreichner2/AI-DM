"""The class capability engine: resolve and apply capability uses, and offer class actions in combat.

A use is resolved once before narration (the ``class_feature.use`` change),
replayed by the validator with the dice it recorded, and applied by
:func:`apply_capability_use`.  Nothing here mutates its inputs except the apply
function, which mutates the state it is given.

Combat glue registered at import:

* the action provider offers ``class.use.<capabilityId>`` (a typed capability
  use; its canonical action carries no economy cost because the
  ``class_feature.use`` change charges the turn itself), Cunning Action as
  ``class.cunning_action.<dash|disengage|hide>`` (universal actions at a bonus
  action), the Martial Arts unarmed strike ``class.attack.unarmed``, the bonus
  strikes ``class.strike.<martial_arts_strike|flurry_of_blows>`` (canonical
  ``bonus_attack``) and attack riders ``class.rider.<weapon>.<capability>[.<option>]``
  (canonical ``rider``, spent only on a hit through :func:`resolve_attack_rider`);
* Uncanny Dodge as a reaction provider.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Callable, Mapping

from aidm_server.canon_text import int_or_default
from aidm_server.character_resources import normalize_spell_resources
from aidm_server.class_features import (
    COMBAT_ONLY_REASON,
    OUT_OF_COMBAT_ONLY_REASON,
    RAGE_RESISTANCES,
    ROLE_REASONS,
    adjust_class_pool,
    arcane_recovery_choice,
    bounded_level,
    capabilities_for_class,
    capability_by_id,
    capability_variants,
    challenge_at_most_half,
    class_feature_descriptors,
    has_effect,
    is_raging,
    normalize_class_feature_state,
    primary_class,
    resource_reason,
    spend_capability,
    spell_save_dc,
    split_capability_id,
    unarmed_damage_dice,
    variant_for,
)
from aidm_server.combat.attacks import resolve_save
from aidm_server.combat.legal_actions import register_action_provider, register_action_resolver, weapon_damage_profile
from aidm_server.combat.modifiers import (
    attack_context,
    can_act,
    condition_key,
    condition_keys,
    consume_modifiers,
    is_hidden,
    range_band,
    speed_zero_reasons,
)
from aidm_server.combat.positioning import (
    RANGE_BANDS,
    REACH_WEAPON_BANDS,
    adjacent_or_same_band,
    has_cover,
    participant_by_id,
    reach_block_reason,
    targeting_block_reason,
)
from aidm_server.combat.reactions import auto_reactions_enabled, register_reaction_provider
from aidm_server.combat.state import (
    MAX_MOVEMENT_PER_TURN,
    combat_turn_economy,
    default_turn_economy,
    participant_is_targetable,
)
from aidm_server.combat.working_state import add_active_effect
from aidm_server.game_state.models import actor_items, find_actor, stable_change_id
from aidm_server.weapon_proficiency import weapon_properties


Roller = Callable[[int], int]

USE_ACTION_PREFIX = 'class.use.'
STRIKE_ACTION_PREFIX = 'class.strike.'
RIDER_ACTION_PREFIX = 'class.rider.'
CUNNING_ACTION_PREFIX = 'class.cunning_action.'
MARTIAL_ARTS_ACTION_ID = 'class.attack.unarmed'
ATTACK_RIDER_SOURCE = 'combat_attack_rider'
FRIENDLY_TEAMS = frozenset({'player', 'ally'})
TURN_UNDEAD_BANDS = frozenset({'melee', 'near', 'far'})
EFFECT_ROUNDS = {'rage': 10, 'turned': 10, 'bardic_inspiration': 100}
FLURRY_STRIKES = 2
# Every resolution key the validator replays and compares.
TRUSTED_RESOLUTION_KEYS = (
    'capabilityId',
    'targetId',
    'resourceCost',
    'cost',
    'gain',
    'actionEconomy',
    'effectType',
    'amount',
    'rolls',
    'effects',
    'grant',
    'removesConditions',
    'turnUndead',
    'restoredSlots',
    'consume',
    'save',
    'hit',
)


def _text(value: Any) -> str:
    return str(value or '').strip()


def _name(record: Mapping[str, Any] | None, fallback: str = 'someone') -> str:
    return _text((record or {}).get('name') or (record or {}).get('characterName')) or _text((record or {}).get('id')) or fallback


def _fail(reason: str) -> dict[str, Any]:
    return {'ok': False, 'reason': reason}


def trusted_resolution(resolution: Mapping[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(resolution.get(key)) for key in TRUSTED_RESOLUTION_KEYS if resolution.get(key) is not None}


def _health(actor: Mapping[str, Any]) -> tuple[int, int]:
    health = actor.get('health') if isinstance(actor.get('health'), dict) else {}
    maximum = max(0, int_or_default(health.get('maxHp', health.get('max')), default=0))
    current = max(0, min(maximum, int_or_default(health.get('currentHp', health.get('current')), default=maximum)))
    return current, maximum


def _conditions(record: Mapping[str, Any]) -> set[str]:
    health = record.get('health') if isinstance(record.get('health'), Mapping) and not isinstance(record.get('hp'), Mapping) else record
    return condition_keys(health)


def _is_dead(target: Mapping[str, Any]) -> bool:
    return 'dead' in _conditions(target)


def _combat(state: Mapping[str, Any] | None) -> dict[str, Any]:
    combat = (state or {}).get('combat')
    return combat if isinstance(combat, dict) else {}


def _economy(*, action: int = 0, bonus_action: int = 0, movement: str = 'unused') -> dict[str, Any]:
    return {
        'action': action,
        'bonusAction': bonus_action,
        'reaction': 0,
        'movement': movement,
        'movementCost': 0,
        'endsTurn': False,
        'tracking': 'persisted_turn_economy',
        'reactionTracked': True,
        'subTurnCountersTracked': True,
    }


def _economy_for(kind: str) -> dict[str, Any]:
    return _economy(action=1 if kind == 'action' else 0, bonus_action=1 if kind == 'bonus_action' else 0)


def attacked_this_turn(economy: Mapping[str, Any] | None) -> bool:
    economy = economy if isinstance(economy, Mapping) else {}
    claims = economy.get('spentActionClaims') if isinstance(economy.get('spentActionClaims'), Mapping) else {}
    return bool(economy.get('attackWeaponIds')) or any('"actionType":"attack"' in _text(claim).replace(' ', '') for claim in claims.values())


def _flurry_strikes_used(economy: Mapping[str, Any] | None) -> int:
    spent = (economy or {}).get('spentActionIds') if isinstance(economy, Mapping) else None
    return sum(1 for entry in spent or [] if _text(entry).startswith(f'{STRIKE_ACTION_PREFIX}flurry_of_blows@'))


# Effects ------------------------------------------------------------------------


def _effect(effect_id: str, source_id: str, source_name: str, caster_id: str, **fields: Any) -> dict[str, Any]:
    return {
        'id': effect_id,
        'sourceId': source_id,
        'sourceName': source_name,
        'casterId': caster_id,
        'modifiers': fields.pop('modifiers', {}),
        'duration': fields.pop('duration', None),
        'concentration': False,
        'consumeOn': fields.pop('consumeOn', None),
        **fields,
    }


def rage_effect(actor_id: str) -> dict[str, Any]:
    # ``endsWhen`` and ``cannotCast`` record rules the turn and spell engines enforce.
    return _effect(
        f'rage:{actor_id}', 'rage', 'Rage', actor_id,
        modifiers={'bonusWeaponDamage': {'flat': 2}, 'resistances': list(RAGE_RESISTANCES)},
        duration={'rounds': EFFECT_ROUNDS['rage'], 'tickOn': 'target_turn_end'},
        endsWhen=['unconscious', 'no_attack_or_damage_since_last_turn'],
        cannotCast=True,
    )


def turned_effect(target_id: str, cleric_id: str) -> dict[str, Any]:
    # Being attacked ends the turning (``consumeOn``) until damage can end it (``endsWhen``).
    return _effect(
        f'turned:{target_id}', 'turn_undead', 'Turn Undead', cleric_id,
        condition='turned',
        modifiers={'cannotAct': True, 'cannotReact': True},
        duration={'rounds': EFFECT_ROUNDS['turned'], 'tickOn': 'target_turn_end'},
        consumeOn='attacked',
        endsWhen=['damage_taken'],
    )


def _apply_modifier_effects(capability_id: str, actor_id: str, target_id: str, level: int, option: str) -> list[dict[str, Any]]:
    if capability_id == 'rage':
        return [{'participantId': actor_id, 'effect': rage_effect(actor_id)}]
    if capability_id == 'reckless_attack':
        return [
            {'participantId': actor_id, 'effect': _effect(
                f'reckless_attack:{actor_id}', 'reckless_attack', 'Reckless Attack', actor_id,
                duration={'rounds': 1, 'tickOn': 'target_turn_end'},
            )},
            {'participantId': actor_id, 'effect': _effect(
                f'reckless_attack_exposed:{actor_id}', 'reckless_attack', 'Reckless Attack', actor_id,
                modifiers={'attackedMode': 'advantage'},
                duration={'rounds': 1, 'tickOn': 'target_turn_start'},
            )},
        ]
    if capability_id == 'bardic_inspiration':
        return [{'participantId': target_id, 'effect': _effect(
            f'bardic_inspiration:{target_id}', 'bardic_inspiration', 'Bardic Inspiration', actor_id,
            inspirationDie='d8' if level >= 5 else 'd6',
            duration={'rounds': EFFECT_ROUNDS['bardic_inspiration'], 'tickOn': 'target_turn_end'},
            # Rounds tick only in combat; outside it the die lapses at the next rest.
            expiresOn='rest',
        )}]
    if capability_id in {'quickened_spell', 'twinned_spell'}:
        fields: dict[str, Any] = {'metamagic': 'quickened' if capability_id == 'quickened_spell' else 'twinned'}
        if capability_id == 'twinned_spell':
            fields['spellLevel'] = int_or_default(option, default=0)
        return [{'participantId': actor_id, 'effect': _effect(
            f'{capability_id}:{actor_id}', capability_id, capability_id.replace('_', ' ').title(), actor_id,
            duration={'rounds': 1, 'tickOn': 'target_turn_end'},
            **fields,
        )}]
    if capability_id == 'flurry_of_blows':
        return [{'participantId': actor_id, 'effect': _effect(
            f'flurry_of_blows:{actor_id}', 'flurry_of_blows', 'Flurry of Blows', actor_id,
            duration={'rounds': 1, 'tickOn': 'target_turn_end'},
        )}]
    return []


# Resolution ---------------------------------------------------------------------------


def _recording(roller: Roller) -> tuple[Roller, list[int]]:
    recorded: list[int] = []

    def roll(sides: int) -> int:
        value = max(1, min(max(1, int(sides)), int_or_default(roller(sides), default=1)))
        recorded.append(value)
        return value

    return roll, recorded


def _target_reason(
    capability: Mapping[str, Any],
    actor: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    in_combat: bool,
    actor_participant: Mapping[str, Any] | None,
    target_participant: Mapping[str, Any] | None,
) -> str:
    policy = _text(capability.get('targetPolicy')).lower()
    name = _text(capability.get('name'))
    if policy in {'self_or_ally', 'ally'} and in_combat:
        actor_team = _text(actor.get('team') or 'player')
        target_team = _text(target.get('team') or 'player')
        if actor_team and target_team and actor_team != target_team:
            return f'{name} can target only self or an ally.' if policy == 'self_or_ally' else f'{name} can target only an ally.'
    if policy == 'ally':
        if _text(target.get('id')) == _text(actor.get('id')):
            return f'{name} must target an ally other than yourself.'
        record = target_participant or target
        if _is_dead(target) or (target_participant is not None and not participant_is_targetable(dict(target_participant))):
            return 'Choose an ally who can still fight.'
        if in_combat and actor_participant is not None and target_participant is not None and not adjacent_or_same_band(actor_participant, target_participant):
            return 'Too far away; the ally must be in your range band or the next one.'
        if has_effect(record, f"bardic_inspiration:{_text(target.get('id'))}"):
            return f"{_name(target)} already has a Bardic Inspiration die."
    return ''


def _undead_in_reach(combat: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        participant
        for participant in combat.get('participants') or []
        if isinstance(participant, dict)
        and _text(participant.get('team')).lower() == 'enemy'
        and condition_key(participant.get('creatureType') or participant.get('creatureTypeName')) == 'undead'
        and participant_is_targetable(participant)
        and range_band(participant) in TURN_UNDEAD_BANDS
    ]


def _turn_undead(actor: Mapping[str, Any], combat: Mapping[str, Any], level: int, roller: Roller) -> dict[str, Any]:
    dc = spell_save_dc(actor, 'wisdom')
    actor_id = _text(actor.get('id'))
    outcomes: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []
    consume: list[dict[str, Any]] = []
    for undead in _undead_in_reach(combat):
        save = resolve_save(undead, {'ability': 'wisdom', 'dc': dc, 'effectOnSuccess': 'none'}, combat, roller)
        consume.extend(save.pop('consume'))
        undead_id = _text(undead.get('id'))
        outcome = 'resisted' if save['saveSucceeded'] else 'destroyed' if level >= 5 and challenge_at_most_half(undead) else 'turned'
        outcomes.append({'targetId': undead_id, 'targetName': _name(undead), 'outcome': outcome, **save})
        if outcome == 'turned':
            effects.append({'participantId': undead_id, 'effect': turned_effect(undead_id, actor_id)})
    return {'turnUndead': outcomes, 'effects': effects, 'consume': consume}


def resolve_capability_use(
    *,
    actor: dict[str, Any],
    capability_id: Any,
    target: dict[str, Any] | None = None,
    requested_amount: Any = None,
    turn_economy: dict[str, Any] | None = None,
    in_combat: bool = False,
    roller: Roller | None = None,
    state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and resolve one capability use without mutating input state.

    ``state`` supplies the combat (participants, positions) and the clock;
    capabilities that act on combatants need it.
    """

    base_id, option = split_capability_id(capability_id)
    capability = capability_by_id(actor.get('class'), actor.get('level'), base_id)
    if not capability:
        return _fail('That class capability is not available at this level.')
    name = _text(capability.get('name'))
    role = _text(capability.get('role')) or 'panel'
    if role != 'panel':
        return _fail(ROLE_REASONS[role])
    level = bounded_level(actor.get('level'))
    spell_resources = actor.get('spellResources')
    variant = variant_for(capability, option, level=level, spell_resources=spell_resources)
    if variant is None:
        return _fail(f'That {name} option is not available.')
    feature_state = normalize_class_feature_state(
        actor.get('classFeatureState'), class_name=actor.get('class'), level=level, scores=actor.get('stats'),
    )
    reason = resource_reason(capability, variant, feature_state=feature_state, spell_resources=spell_resources, level=level)
    if reason:
        return _fail(reason)
    if capability.get('combatOnly') and not in_combat:
        return _fail(COMBAT_ONLY_REASON)
    if capability.get('outOfCombatOnly') and in_combat:
        return _fail(OUT_OF_COMBAT_ONLY_REASON)

    economy = turn_economy if isinstance(turn_economy, dict) else {}
    economy_kind = _text(capability.get('actionEconomy')).lower()
    if economy_kind == 'action' and in_combat and int_or_default(economy.get('actionRemaining'), default=0) <= 0:
        return _fail('The action for this turn is already spent.')
    if economy_kind == 'bonus_action' and in_combat and int_or_default(economy.get('bonusActionRemaining'), default=0) <= 0:
        return _fail('The bonus action for this turn is already spent.')

    policy = _text(capability.get('targetPolicy')).lower()
    resolved_target = actor if policy == 'self' else target
    if not isinstance(resolved_target, dict):
        return _fail(f'{name} requires an exact target.')
    combat = _combat(state)
    actor_id = _text(actor.get('id'))
    participant = participant_by_id(combat, actor_id) if in_combat else None
    target_participant = participant_by_id(combat, resolved_target.get('id')) if in_combat else None
    reason = _target_reason(
        capability, actor, resolved_target, in_combat=in_combat, actor_participant=participant, target_participant=target_participant,
    )
    if reason:
        return _fail(reason)

    effect = capability.get('effect') if isinstance(capability.get('effect'), dict) else {}
    effect_type = _text(effect.get('type')).lower()
    cost = deepcopy(variant['cost'])
    result: dict[str, Any] = {
        'ok': True,
        'capability': capability,
        'capabilityId': f'{base_id}:{option}' if option else base_id,
        'targetId': resolved_target.get('id'),
        'resourceCost': 0 if not cost.get('pool') else 1,
        'actionEconomy': economy_kind,
        'effectType': effect_type,
    }
    if cost:
        result['cost'] = cost
    if variant.get('gain'):
        result['gain'] = deepcopy(variant['gain'])
    if in_combat and effect_type in {'apply_modifier', 'grant_action', 'turn_undead'} and participant is None:
        return _fail(f'{_name(actor)} is not part of this encounter.')

    if effect_type == 'heal':
        current_hp, maximum_hp = _health(resolved_target)
        if maximum_hp <= 0 or current_hp <= 0:
            return _fail('An unconscious or dead target cannot use this recovery feature.')
        if current_hp >= maximum_hp:
            return _fail('The target is already at full hit points.')
        dice = effect.get('dice') if isinstance(effect.get('dice'), dict) else {}
        count = max(1, int_or_default(dice.get('count'), default=1))
        sides = max(2, int_or_default(dice.get('sides'), default=10))
        roll = roller or (lambda die_sides: die_sides)
        rolls = [max(1, min(sides, int_or_default(roll(sides), default=1))) for _ in range(count)]
        level_bonus = level if effect.get('levelBonus') else 0
        result.update({'effectType': 'heal', 'amount': sum(rolls) + level_bonus, 'rolls': rolls})
    elif effect_type == 'healing_pool' and option == 'cure_poison':
        if _is_dead(resolved_target):
            return _fail('Lay on Hands cannot restore the dead.')
        if 'poisoned' not in _conditions(target_participant or resolved_target):
            return _fail(f'{_name(resolved_target)} is not poisoned.')
        result.update({'effectType': 'remove_condition', 'removesConditions': ['poisoned'], 'resourceCost': 5})
    elif effect_type == 'healing_pool':
        current_hp, maximum_hp = _health(resolved_target)
        if _is_dead(resolved_target):
            return _fail('Lay on Hands cannot restore the dead.')
        if maximum_hp <= 0:
            return _fail('Lay on Hands requires a target with known hit points.')
        missing_hp = max(0, maximum_hp - current_hp)
        remaining = int_or_default(feature_state.get('lay_on_hands', {}).get('current'), default=0)
        amount = min(max(1, int_or_default(requested_amount, default=1)), remaining, missing_hp)
        if amount <= 0:
            return _fail('The target has no missing hit points.')
        result.update({'effectType': 'heal', 'amount': amount, 'resourceCost': amount})
    elif effect_type == 'restore_action':
        if not in_combat:
            return _fail('Action Surge is available only during active combat.')
        if int_or_default(economy.get('actionRemaining'), default=0) > 0:
            return _fail('The action for this turn is still available.')
        result.update({'effectType': 'restore_action', 'amount': 1})
    elif effect_type == 'apply_modifier':
        blocker = _modifier_blocker(base_id, participant, economy, actor_id)
        if blocker:
            return _fail(blocker)
        result['effects'] = _apply_modifier_effects(base_id, actor_id, _text(resolved_target.get('id')), level, option)
    elif effect_type == 'grant_action':
        grant = option or _text(effect.get('action'))
        blocker = _grant_blocker(base_id, grant, participant, economy, actor_id)
        if blocker:
            return _fail(blocker)
        result['grant'] = grant
        result['effects'] = _apply_modifier_effects(base_id, actor_id, actor_id, level, option)
    elif effect_type == 'turn_undead':
        if not _undead_in_reach(combat):
            return _fail('No undead enemy is within melee, near or far range.')
        recording_roller, rolls = _recording(roller or (lambda die_sides: die_sides))
        result.update(_turn_undead(actor, combat, level, recording_roller))
        result['rolls'] = rolls
    elif effect_type == 'resource_convert':
        result['resourceCost'] = int_or_default(cost.get('amount'), default=0) if cost.get('pool') else 0
    elif effect_type == 'restore_slots':
        clock = (state or {}).get('clock') if isinstance((state or {}).get('clock'), Mapping) else {}
        if int_or_default(clock.get('shortRestsSinceLong'), default=0) <= 0:
            return _fail('Arcane Recovery is part of a short rest; finish a short rest first.')
        result['restoredSlots'] = list(arcane_recovery_choice(option, level=level, spell_resources=spell_resources))
    else:
        return _fail('That capability does not yet have an authoritative effect.')
    if cost.get('pool') and effect_type in {'apply_modifier', 'grant_action', 'turn_undead', 'restore_slots'}:
        result['resourceCost'] = int_or_default(cost.get('amount'), default=1)
    return result


def _modifier_blocker(capability_id: str, participant: Mapping[str, Any] | None, economy: Mapping[str, Any], actor_id: str) -> str:
    if capability_id == 'rage' and is_raging(participant):
        return 'Already raging.'
    if capability_id == 'reckless_attack':
        if has_effect(participant, f'reckless_attack:{actor_id}'):
            return 'Already attacking recklessly this turn.'
        if attacked_this_turn(economy):
            return 'Declare Reckless Attack before your first attack this turn.'
    if capability_id in {'quickened_spell', 'twinned_spell'} and has_effect(participant, f'{capability_id}:{actor_id}'):
        return 'That Metamagic is already readied for this turn.'
    return ''


def _grant_blocker(capability_id: str, grant: str, participant: Mapping[str, Any] | None, economy: Mapping[str, Any], actor_id: str) -> str:
    if grant == 'dodge' and 'dodging' in condition_keys(participant):
        return 'Already dodging.'
    if grant == 'dash':
        blocked = ' '.join(speed_zero_reasons(participant or {}))
        if blocked:
            return blocked
        if int_or_default(economy.get('movementRemaining'), default=0) >= MAX_MOVEMENT_PER_TURN:
            return 'You cannot move any farther this turn.'
    if grant == 'disengage' and economy.get('disengaged'):
        return 'Already disengaged this turn.'
    if capability_id == 'flurry_of_blows':
        if has_effect(participant, f'flurry_of_blows:{actor_id}'):
            return 'Flurry of Blows is already under way this turn.'
        if not attacked_this_turn(economy):
            return 'Take the Attack action first.'
    return ''


# Application ----------------------------------------------------------------------------


def _set_conditions(record: dict[str, Any], conditions: list[str]) -> None:
    holder = record['health'] if isinstance(record.get('health'), dict) and not isinstance(record.get('hp'), dict) else record
    holder['conditions'] = conditions


def _add_condition(record: dict[str, Any], condition: str) -> None:
    holder = record['health'] if isinstance(record.get('health'), dict) and not isinstance(record.get('hp'), dict) else record
    existing = [condition_key(value) for value in holder.get('conditions') or [] if _text(value)]
    _set_conditions(record, existing if condition in existing else [*existing, condition])


def _remove_conditions(record: dict[str, Any], removed: set[str]) -> None:
    holder = record['health'] if isinstance(record.get('health'), dict) and not isinstance(record.get('hp'), dict) else record
    _set_conditions(record, [value for value in holder.get('conditions') or [] if condition_key(value) not in removed])


def _spend_slot(resources: dict[str, Any], slot_level: int, delta: int) -> bool:
    slot = (resources.get('slots') or {}).get(str(slot_level))
    if not isinstance(slot, dict):
        return False
    current = int_or_default(slot.get('current'), default=0) + delta
    if current < 0 or current > int_or_default(slot.get('max'), default=0):
        return False
    slot['current'] = current
    return True


def apply_capability_use(state: dict[str, Any], actor: dict[str, Any], change: Mapping[str, Any]) -> dict[str, Any] | None:
    """Spend the use's resources, charge the turn and apply every effect except healing.

    Returns ``None`` (leaving ``state`` untouched) when a prerequisite went stale.
    The result's ``affectedParticipantIds`` names the combatants whose
    conditions changed, for the caller to sync onto player actors.
    """

    resolution = change.get('resolution') if isinstance(change.get('resolution'), Mapping) else {}
    effect_type = _text(resolution.get('effectType')).lower()
    combat = _combat(state)
    combat_active = _text(combat.get('status')).lower() == 'active'
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    economy = flags.get('turnEconomy') if isinstance(flags.get('turnEconomy'), dict) else {}
    actor_id = _text(actor.get('id'))
    if combat_active and _text(flags.get('activeActorId')) != actor_id:
        return None
    if effect_type == 'restore_action' and (not economy or not combat_active):
        return None

    feature_state = normalize_class_feature_state(
        actor.get('classFeatureState'), class_name=actor.get('class'), level=actor.get('level'), scores=actor.get('stats'),
    )
    resources = normalize_spell_resources(actor.get('spellResources'), class_name=_text(actor.get('class')), level=bounded_level(actor.get('level')))
    cost = resolution.get('cost') if isinstance(resolution.get('cost'), Mapping) else {}
    gain = resolution.get('gain') if isinstance(resolution.get('gain'), Mapping) else {}
    if cost.get('pool'):
        spent = spend_capability(
            feature_state, class_name=actor.get('class'), level=actor.get('level'), capability_id=resolution.get('capabilityId'),
            amount=resolution.get('resourceCost'), turn_id=change.get('turnId'), scores=actor.get('stats'),
        )
        if spent is None:
            return None
        feature_state = spent
    if cost.get('spellSlot') and not _spend_slot(resources, int(cost['spellSlot']), -1):
        return None
    if gain.get('pool') and not adjust_class_pool(feature_state, gain['pool'], int_or_default(gain.get('amount'), default=0)):
        return None
    if gain.get('spellSlot') and not _spend_slot(resources, int(gain['spellSlot']), 1):
        return None
    for slot_level in resolution.get('restoredSlots') or []:
        if not _spend_slot(resources, int(slot_level), 1):
            return None

    affected: list[str] = []
    planned = _planned_participant_changes(state, combat, resolution, combat_active=combat_active)
    if planned is None:
        return None

    economy_kind = _text(resolution.get('actionEconomy')).lower()
    if combat_active:
        if economy_kind == 'action':
            economy['actionRemaining'] = 0
        elif economy_kind == 'bonus_action':
            economy['bonusActionRemaining'] = 0
        if effect_type == 'restore_action':
            economy['actionRemaining'] = max(1, int_or_default(resolution.get('amount'), default=1))
        grant = _text(resolution.get('grant'))
        if grant == 'dash':
            economy['movementRemaining'] = min(MAX_MOVEMENT_PER_TURN, int_or_default(economy.get('movementRemaining'), default=0) + 1)
        elif grant == 'disengage':
            economy['disengaged'] = True
    for record, apply in planned:
        apply()
        if combat_active and record.get('id'):
            affected.append(_text(record.get('id')))
    if resolution.get('consume'):
        consume_modifiers(combat, resolution['consume'])
    actor['classFeatureState'] = feature_state
    if cost.get('spellSlot') or gain.get('spellSlot') or resolution.get('restoredSlots'):
        resources['revision'] = int_or_default(resources.get('revision'), default=0) + 1
        actor['spellResources'] = resources
    return {
        'capabilityId': resolution.get('capabilityId') or change.get('capabilityId'),
        'capabilityName': change.get('capabilityName'),
        'targetId': resolution.get('targetId'),
        'effectType': effect_type,
        'resourceCost': int_or_default(resolution.get('resourceCost'), default=0),
        'remainingClassFeatureState': deepcopy(actor.get('classFeatureState')),
        'turnEconomy': deepcopy(economy) if economy else None,
        'affectedParticipantIds': list(dict.fromkeys(affected)),
        **({'remainingSpellResources': deepcopy(actor['spellResources'])} if 'spellResources' in actor else {}),
    }


def _planned_participant_changes(
    state: dict[str, Any],
    combat: dict[str, Any],
    resolution: Mapping[str, Any],
    *,
    combat_active: bool,
) -> list[tuple[dict[str, Any], Callable[[], Any]]] | None:
    """Resolve every record an effect touches before anything is mutated."""

    planned: list[tuple[dict[str, Any], Callable[[], Any]]] = []
    for entry in resolution.get('effects') or []:
        participant_id = _text(entry.get('participantId'))
        holder = participant_by_id(combat, participant_id) if combat_active else find_actor(state, participant_id)
        if holder is None:
            return None
        planned.append((holder, lambda holder=holder, effect=entry.get('effect'): add_active_effect(holder, deepcopy(effect))))
    if _text(resolution.get('grant')) == 'dodge':
        participant = participant_by_id(combat, resolution.get('targetId'))
        if participant is None:
            return None
        planned.append((participant, lambda participant=participant: _add_condition(participant, 'dodging')))
    removed = {condition_key(value) for value in resolution.get('removesConditions') or []}
    if removed:
        records = [record for record in (find_actor(state, resolution.get('targetId')), participant_by_id(combat, resolution.get('targetId'))) if record is not None]
        if not records:
            return None
        planned.extend((record, lambda record=record: _remove_conditions(record, removed)) for record in records)
    for outcome in resolution.get('turnUndead') or []:
        if outcome.get('outcome') != 'destroyed':
            continue
        undead = participant_by_id(combat, outcome.get('targetId'))
        if undead is None:
            return None
        planned.append((undead, lambda undead=undead: _destroy(undead)))
    return planned


def _destroy(participant: dict[str, Any]) -> None:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    participant['hp'] = {**hp, 'current': 0, 'temp': 0}
    participant['isAlive'] = False
    participant['isConscious'] = False


# Attack riders ----------------------------------------------------------------------


def stunned_effect(target_id: str, monk_id: str) -> dict[str, Any]:
    # Two ticks of the monk's turn end: the current turn, then the next.
    return _effect(
        f'stunning_strike:{target_id}', 'stunning_strike', 'Stunning Strike', monk_id,
        condition='stunned',
        duration={'rounds': 2, 'tickOn': 'caster_turn_end'},
    )


def attack_rider_variants(actor: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every rider a melee weapon attack may declare: ``{"capabilityId", "option", "name", "cost"}``."""

    level = bounded_level(actor.get('level'))
    return [
        {'capabilityId': capability['id'], **variant}
        for capability in capabilities_for_class(actor.get('class'), level)
        if capability.get('role') == 'attack_rider'
        for variant in capability_variants(capability, level=level, spell_resources=actor.get('spellResources'))
    ]


def resolve_attack_rider(
    *,
    actor: Mapping[str, Any],
    rider: Mapping[str, Any],
    target: Mapping[str, Any],
    hit: bool,
    combat: Mapping[str, Any],
    roller: Roller | None = None,
) -> dict[str, Any]:
    """Resolve a declared rider once the attack is known: nothing is spent on a miss.

    Divine Smite's dice are added by the extra-damage provider; this pays for
    the rider and rolls Stunning Strike's save.
    """

    if not hit:
        return _fail('The attack missed, so the rider is not spent.')
    capability = capability_by_id(actor.get('class'), actor.get('level'), rider.get('capabilityId'))
    if capability is None or capability.get('role') != 'attack_rider':
        return _fail('That rider is not available at this level.')
    level = bounded_level(actor.get('level'))
    option = _text(rider.get('option'))
    variant = variant_for(capability, option, level=level, spell_resources=actor.get('spellResources'))
    if variant is None:
        return _fail(f"That {capability['name']} option is not available.")
    feature_state = normalize_class_feature_state(
        actor.get('classFeatureState'), class_name=actor.get('class'), level=level, scores=actor.get('stats'),
    )
    reason = resource_reason(capability, variant, feature_state=feature_state, spell_resources=actor.get('spellResources'), level=level)
    if reason:
        return _fail(reason)
    cost = deepcopy(variant['cost'])
    result: dict[str, Any] = {
        'ok': True,
        'capability': capability,
        'capabilityId': f"{capability['id']}:{option}" if option else capability['id'],
        'targetId': _text(target.get('id')),
        'resourceCost': int_or_default(cost.get('amount'), default=1) if cost.get('pool') else 0,
        'cost': cost,
        'actionEconomy': 'free',
        'effectType': 'attack_rider',
        'hit': True,
    }
    if capability['id'] == 'stunning_strike':
        recording_roller, rolls = _recording(roller or (lambda die_sides: die_sides))
        save = resolve_save(target, {'ability': 'constitution', 'dc': spell_save_dc(actor, 'wisdom'), 'effectOnSuccess': 'none'}, combat, recording_roller)
        result['consume'] = save.pop('consume')
        result['save'] = save
        result['rolls'] = rolls
        if not save['saveSucceeded']:
            result['effects'] = [{'participantId': _text(target.get('id')), 'effect': stunned_effect(_text(target.get('id')), _text(actor.get('id')))}]
    return result


def attack_rider_change(
    state: Mapping[str, Any],
    *,
    actor_id: Any,
    rider: Mapping[str, Any],
    target_id: Any,
    hit: bool,
    turn_id: Any,
    roller: Roller | None = None,
) -> dict[str, Any] | None:
    """The trusted ``class_feature.use`` change a resolved attack emits for its rider, or None."""

    actor = find_actor(dict(state), actor_id)
    combat = _combat(state)
    target = participant_by_id(combat, target_id)
    if actor is None or target is None:
        return None
    resolution = resolve_attack_rider(actor=actor, rider=rider, target=target, hit=hit, combat=combat, roller=roller)
    if not resolution.get('ok'):
        return None
    capability = resolution['capability']
    return {
        'id': stable_change_id(turn_id, ATTACK_RIDER_SOURCE, actor_id, resolution['capabilityId'], target_id),
        'turnId': turn_id,
        'type': 'class_feature.use',
        'source': ATTACK_RIDER_SOURCE,
        'actorId': _text(actor_id),
        'capabilityId': resolution['capabilityId'],
        'capabilityName': capability['name'],
        'targetId': _text(target_id),
        'resolutionAuthority': 'class_capability_engine',
        'resolution': trusted_resolution(resolution),
        'reason': f"{capability['name']} was spent on a hit.",
        'visible': True,
    }


# Combat legal actions -------------------------------------------------------------


def _target_option(record: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        'id': _text(record.get('id')),
        'name': _name(record, 'Unknown target'),
        'rangeBand': range_band(record),
        'available': not reason,
        'reason': reason,
    }


def _weapon_key(weapon: Mapping[str, Any]) -> str:
    return re.sub(r'[^a-z0-9]+', '_', _text(weapon.get('id') or weapon.get('name')).lower()).strip('_')[:48] or 'weapon'


def _attack_targets(participant: Mapping[str, Any], combat: Mapping[str, Any], *, reach: bool) -> list[dict[str, Any]]:
    allowed = tuple(sorted(REACH_WEAPON_BANDS, key=RANGE_BANDS.index)) if reach else ('melee',)
    targets = []
    for enemy in combat.get('participants') or []:
        if not isinstance(enemy, dict) or _text(enemy.get('team')).lower() != 'enemy':
            continue
        reason = targeting_block_reason(participant, enemy, combat) or reach_block_reason(
            participant, enemy, melee=True, allowed_bands=allowed, reach=reach,
        )
        roll = attack_context(participant, enemy, kind='weapon_attack', ranged=False, combat=combat)
        targets.append({**_target_option(enemy, reason), 'rollMode': roll['mode'], 'rollReasons': roll['reasons']})
    return targets


def _melee_attack_action(
    action_id: str,
    label: str,
    description: str,
    weapon: Mapping[str, Any],
    *,
    participant: Mapping[str, Any],
    combat: Mapping[str, Any],
    economy: dict[str, Any],
    reason: str = '',
) -> dict[str, Any]:
    reach = any(_text(tag).lower() == 'reach' for tag in weapon.get('tags') or [])
    targets = _attack_targets(participant, combat, reach=reach)
    blocker = reason or ('' if any(target['available'] for target in targets) else 'No target is currently in range.')
    damage = weapon_damage_profile(dict(weapon), 'melee') if weapon.get('id') != 'unarmed' else dict(weapon['damage'])
    return {
        'id': action_id,
        'type': 'attack',
        'label': label,
        'description': description,
        'message': '',
        'available': not blocker,
        'reason': blocker,
        'economy': economy,
        'requiresTarget': True,
        'targets': targets,
        'range': {'classification': 'melee', 'allowedBands': ['melee', 'near'] if reach else ['melee'], 'assessment': 'range_band_and_engagement'},
        'roll': {'die': 'd20', 'outcome': 'server_authoritative'},
        'weapon': {
            'id': _text(weapon.get('id')) or _weapon_key(weapon),
            'name': _text(weapon.get('name')) or 'weapon',
            'classification': 'melee',
            'damage': damage,
        },
    }


def _attack_economy(turn_economy: Mapping[str, Any]) -> dict[str, Any]:
    return _economy(action=0 if int_or_default(turn_economy.get('attacksRemaining'), default=0) > 0 else 1, movement='optional')


def _unarmed_weapon(actor: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'id': 'unarmed',
        'name': 'Unarmed strike',
        'damage': {'dice': unarmed_damage_dice(actor.get('class'), actor.get('level')), 'type': 'bludgeoning'},
    }


def _record_for(state: Mapping[str, Any], participant: Mapping[str, Any]) -> dict[str, Any]:
    return find_actor(dict(state), participant.get('id')) or dict(participant)


def _use_action(
    descriptor: Mapping[str, Any],
    actor: dict[str, Any],
    participant: Mapping[str, Any],
    combat: Mapping[str, Any],
    state: Mapping[str, Any],
    economy: dict[str, Any],
) -> dict[str, Any]:
    policy = _text(descriptor.get('targetPolicy')).lower()

    def attempt(target: Mapping[str, Any]) -> dict[str, Any]:
        return resolve_capability_use(
            actor=actor, capability_id=descriptor['id'], target=dict(target), requested_amount=1,
            turn_economy=economy, in_combat=True, state=state,
        )

    targets: list[dict[str, Any]] = []
    if policy == 'self':
        outcome = attempt(actor)
        reason = '' if outcome['ok'] else outcome['reason']
    else:
        for candidate in combat.get('participants') or []:
            if (
                not isinstance(candidate, dict)
                or _text(candidate.get('team')).lower() not in FRIENDLY_TEAMS
                or (policy == 'ally' and _text(candidate.get('id')) == _text(actor.get('id')))
                or candidate.get('isPresent', candidate.get('present', True)) is False
            ):
                continue
            outcome = attempt(_record_for(state, candidate))
            targets.append(_target_option(candidate, '' if outcome['ok'] else outcome['reason']))
        available = [target for target in targets if target['available']]
        reason = '' if available else (targets[0]['reason'] if targets else 'No legal target is available.')
    feature_state = normalize_class_feature_state(
        actor.get('classFeatureState'), class_name=actor.get('class'), level=actor.get('level'), scores=actor.get('stats'),
    )
    pool = feature_state.get(_text(descriptor.get('pool')))
    action = {
        'id': f"{USE_ACTION_PREFIX}{descriptor['id']}",
        'type': 'class_feature',
        'label': descriptor['name'],
        'description': _text(descriptor.get('description')),
        'message': '',
        'available': not reason,
        'reason': reason,
        'economy': _economy_for(_text(descriptor.get('actionEconomy')).lower()),
        'requiresTarget': policy != 'self',
        'capability': {
            'id': descriptor['id'],
            'baseId': descriptor['baseId'],
            'name': descriptor['name'],
            'effectType': _text((descriptor.get('effect') or {}).get('type')),
            'targetPolicy': policy,
            'actionEconomy': _text(descriptor.get('actionEconomy')),
            'cost': deepcopy(descriptor.get('cost') or {}),
            'pool': descriptor.get('pool'),
            'current': int_or_default((pool or {}).get('current'), default=0) if pool else None,
            'max': int_or_default((pool or {}).get('max'), default=0) if pool else None,
            'refreshesOn': descriptor.get('refreshesOn'),
        },
    }
    if policy != 'self':
        action['targets'] = targets
    return action


def _cunning_action(option: str, participant: Mapping[str, Any], combat: Mapping[str, Any], economy: Mapping[str, Any]) -> dict[str, Any]:
    name = _name(participant, 'The rogue')
    if option == 'dash':
        reason = ' '.join(speed_zero_reasons(participant)) or (
            'You cannot move any farther this turn.' if int_or_default(economy.get('movementRemaining'), default=0) >= MAX_MOVEMENT_PER_TURN else ''
        )
        description = 'Gain one more movement this turn, as a bonus action.'
    elif option == 'disengage':
        reason = 'Already disengaged this turn.' if economy.get('disengaged') else ''
        description = 'Your movement provokes no opportunity attacks for the rest of this turn, as a bonus action.'
    else:
        reason = (
            'Already hidden.'
            if is_hidden(participant)
            else 'You cannot hide in melee without cover.'
            if range_band(participant) == 'melee' and not has_cover(combat, participant)
            else ''
        )
        description = 'As a bonus action, make a server-rolled Dexterity (Stealth) check against the sharpest enemy passive Perception.'
    return {
        'id': f'{CUNNING_ACTION_PREFIX}{option}',
        'type': option,
        'label': f'Cunning Action: {option.capitalize()}',
        'description': description,
        'message': f'{name} uses Cunning Action to {option}.',
        'available': not reason,
        'reason': reason,
        'economy': _economy(bonus_action=1),
    }


def _monk_actions(actor: Mapping[str, Any], participant: Mapping[str, Any], combat: Mapping[str, Any], economy: dict[str, Any]) -> list[dict[str, Any]]:
    level = bounded_level(actor.get('level'))
    unarmed = _unarmed_weapon(actor)
    dice = unarmed['damage']['dice']
    actions = [
        _melee_attack_action(
            MARTIAL_ARTS_ACTION_ID, 'Unarmed strike (Martial Arts)',
            f'Strike with fists, feet or elbows for {dice} bludgeoning damage, using Dexterity if it is higher.',
            unarmed, participant=participant, combat=combat, economy=_attack_economy(economy),
        ),
        _melee_attack_action(
            f'{STRIKE_ACTION_PREFIX}martial_arts_strike', 'Martial Arts: bonus unarmed strike',
            'After the Attack action, make one unarmed strike as a bonus action.',
            unarmed, participant=participant, combat=combat, economy=_economy(bonus_action=1, movement='optional'),
            reason='' if attacked_this_turn(economy) else 'Take the Attack action first.',
        ),
    ]
    if level >= 2:
        flurrying = has_effect(participant, f"flurry_of_blows:{_text(participant.get('id'))}")
        used = _flurry_strikes_used(economy)
        actions.append(
            _melee_attack_action(
                f'{STRIKE_ACTION_PREFIX}flurry_of_blows', f'Flurry of Blows strike ({max(0, FLURRY_STRIKES - used)} left)' if flurrying else 'Flurry of Blows strike',
                'One of the two unarmed strikes Flurry of Blows grants this turn.',
                unarmed, participant=participant, combat=combat, economy=_economy(movement='optional'),
                reason='' if flurrying and used < FLURRY_STRIKES else 'Both Flurry of Blows strikes are spent.' if flurrying else 'Use Flurry of Blows first.',
            )
        )
    return actions


def _rider_actions(actor: Mapping[str, Any], participant: Mapping[str, Any], combat: Mapping[str, Any], economy: dict[str, Any]) -> list[dict[str, Any]]:
    variants = attack_rider_variants(actor)
    if not variants:
        return []
    level = bounded_level(actor.get('level'))
    feature_state = normalize_class_feature_state(actor.get('classFeatureState'), class_name=actor.get('class'), level=level, scores=actor.get('stats'))
    weapons = [
        item
        for item in actor_items(dict(actor))
        if condition_key(item.get('type')) == 'weapon' and item.get('equipped') and not weapon_properties(item)['ranged']
    ]
    if primary_class(actor.get('class')) == 'monk':
        weapons.append(_unarmed_weapon(actor))
    actions = []
    for weapon in weapons:
        for variant in variants:
            capability = capability_by_id(actor.get('class'), level, variant['capabilityId'])
            suffix = f".{variant['option']}" if variant['option'] else ''
            action = _melee_attack_action(
                f"{RIDER_ACTION_PREFIX}{_weapon_key(weapon)}.{variant['capabilityId']}{suffix}",
                f"Attack with {_text(weapon.get('name')) or 'weapon'} + {variant['name']}",
                f"{_text((capability or {}).get('description'))} Spent only if the attack hits.",
                weapon, participant=participant, combat=combat, economy=_attack_economy(economy),
                reason=resource_reason(capability or {}, variant, feature_state=feature_state, spell_resources=actor.get('spellResources'), level=level),
            )
            action['rider'] = {
                'capabilityId': variant['capabilityId'],
                'option': variant['option'],
                **({'slotLevel': int(variant['cost']['spellSlot'])} if variant['cost'].get('spellSlot') else {}),
            }
            actions.append(action)
    return actions


def class_combat_actions(snapshot: Mapping[str, Any], participant: Mapping[str, Any], combat: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Action provider: every class capability, Cunning Action, Martial Arts strike and attack rider."""

    actor = find_actor(dict(snapshot), participant.get('id'))
    if actor is None or primary_class(actor.get('class')) is None:
        return []
    economy = combat_turn_economy(dict(combat), actor_id=participant.get('id')) or default_turn_economy(participant.get('id'), combat.get('round'))
    state = {**snapshot, 'combat': combat}
    actions: list[dict[str, Any]] = []
    for descriptor in class_feature_descriptors(
        actor.get('class'), actor.get('level'), scores=actor.get('stats'),
        feature_state=actor.get('classFeatureState'), spell_resources=actor.get('spellResources'),
    ):
        if descriptor['role'] == 'panel':
            actions.append(_use_action(descriptor, actor, participant, combat, state, economy))
        elif descriptor['baseId'] == 'cunning_action':
            actions.append(_cunning_action(split_capability_id(descriptor['id'])[1], participant, combat, economy))
    if primary_class(actor.get('class')) == 'monk':
        actions.extend(_monk_actions(actor, participant, combat, economy))
    actions.extend(_rider_actions(actor, participant, combat, economy))
    return actions


def _chosen_target(action: Mapping[str, Any], target_id: str) -> tuple[dict[str, Any] | None, str | None, str | None]:
    target = next((entry for entry in action.get('targets') or [] if entry.get('id') == target_id), None)
    if target is None:
        return None, 'combat_target_invalid', 'Choose a current combat target for that action.'
    if not target.get('available'):
        return None, 'combat_target_unavailable', _text(target.get('reason')) or 'That target is not available.'
    return target, None, None


def resolve_class_use_action(
    snapshot: dict[str, Any], player: Any, action: dict[str, Any], target_id: str,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Action resolver for ``class.use.*``: the canonical capability use.

    The ``class_feature.use`` change charges the turn economy itself, so the
    canonical action carries no cost for the combat pipeline to charge.
    """

    capability = action.get('capability') if isinstance(action.get('capability'), dict) else {}
    user = _text(player.character_name) or 'I'
    if action.get('requiresTarget'):
        target, error_code, error_message = _chosen_target(action, target_id)
        if target is None:
            return None, error_code, error_message
    else:
        if target_id:
            return None, 'combat_target_invalid', 'That combat action does not accept a target.'
        target = {'id': f'player_{player.player_id}', 'name': user}
    canonical = {
        'action_id': action['id'],
        'action_type': 'class_feature',
        'economy': _economy(),
        'authoritative': True,
        'capability_id': capability.get('id'),
        'target_id': target['id'],
        'target_name': target['name'],
        'message': f"{user} uses {action.get('label')}." if not action.get('requiresTarget') else f"{user} uses {action.get('label')} on {target['name']}.",
    }
    if capability.get('effectType') == 'healing_pool' and capability.get('id') == 'lay_on_hands':
        record = find_actor(snapshot, target['id']) or participant_by_id(_combat(snapshot), target['id']) or {}
        current_hp, maximum_hp = _health(record)
        canonical['amount'] = max(1, min(int_or_default(capability.get('current'), default=1), maximum_hp - current_hp))
    return canonical, None, None


def resolve_class_attack_action(
    snapshot: dict[str, Any], player: Any, action: dict[str, Any], target_id: str,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Action resolver for bonus strikes and attack riders: a canonical weapon attack.

    Bonus strikes carry ``bonus_attack`` (charged at their own bonus-action cost,
    never against the Attack action); riders carry ``rider`` for the attack
    resolver to apply on a hit.
    """

    target, error_code, error_message = _chosen_target(action, target_id)
    if target is None:
        return None, error_code, error_message
    weapon = action.get('weapon') if isinstance(action.get('weapon'), dict) else {}
    damage = weapon.get('damage') if isinstance(weapon.get('damage'), dict) else {}
    weapon_name = _text(weapon.get('name')) or 'weapon'
    canonical = {
        'action_id': action['id'],
        'action_type': 'attack',
        'economy': deepcopy(action['economy']),
        'authoritative': True,
        'target_id': target['id'],
        'target_name': target['name'],
        'weapon_id': _text(weapon.get('id')),
        'weapon_name': weapon_name,
        'damage_dice': _text(damage.get('dice')),
        'damage_type': _text(damage.get('type')),
        'ranged': False,
        'range_band': _text(target.get('rangeBand')),
        'message': f"{player.character_name} attacks {target['name']} with {weapon_name}.",
    }
    if _text(action['id']).startswith(STRIKE_ACTION_PREFIX):
        canonical['bonus_attack'] = True
    if isinstance(action.get('rider'), dict):
        canonical['rider'] = deepcopy(action['rider'])
    return canonical, None, None


# Reactions -----------------------------------------------------------------------------


def uncanny_dodge_provider(event: Mapping[str, Any], combat: Mapping[str, Any], state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A level 5+ rogue hit by an attack halves its damage with their reaction."""

    if _text(event.get('type')) != 'attack_hit' or int_or_default(event.get('damage'), default=0) <= 0:
        return []
    rogue = participant_by_id(combat, event.get('targetId'))
    if (
        rogue is None
        or primary_class(rogue.get('class')) != 'rogue'
        or bounded_level(rogue.get('level')) < 5
        or not can_act(rogue)
        or not auto_reactions_enabled(rogue, state)
    ):
        return []
    return [
        {
            'reactorId': _text(rogue.get('id')),
            'kind': 'uncanny_dodge',
            'label': f'{_name(rogue)} uses Uncanny Dodge to halve the damage.',
            'cancelsHit': False,
            'damageMultiplier': 0.5,
            'changes': [],
            'consumes': {'reaction': 1},
        }
    ]


register_action_provider(class_combat_actions)
register_action_resolver(USE_ACTION_PREFIX, resolve_class_use_action)
register_action_resolver(STRIKE_ACTION_PREFIX, resolve_class_attack_action)
register_action_resolver(RIDER_ACTION_PREFIX, resolve_class_attack_action)
register_reaction_provider(uncanny_dodge_provider)
