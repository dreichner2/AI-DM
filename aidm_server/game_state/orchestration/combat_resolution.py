from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import random
import re
from typing import Any, Callable

from aidm_server.canon_text import int_or_default
from aidm_server.combat.state import (
    combat_ability_is_available,
    combat_ability_resolution_mode,
    participant_can_take_turn,
    participant_is_targetable,
)
from aidm_server.damage_dice import normalize_damage_dice_expression, parse_damage_dice_expression
from aidm_server.game_state.models import normalize_item_name, stable_change_id


__all__ = [
    'TEXT_DAMAGE_PATTERN',
    'TrustedDamageChanges',
    'build_dm_combat_context',
    'combat_participant_update_signature',
    'derive_trusted_damage_changes',
    'resolve_authoritative_player_attack',
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
TEXT_DAMAGE_PATTERN = re.compile(
    r'\b(?:deals?|does|for)\s+(\d{0,2}d\d{1,3}(?:\s*[+-]\s*\d{1,4})?)\s+'
    r'(acid|cold|fire|force|lightning|necrotic|poison|psychic|radiant|thunder|bludgeoning|piercing|slashing)\s+damage\b',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TrustedDamageChanges:
    enemy: list[dict[str, Any]]
    resolved: list[dict[str, Any]]

    @property
    def all_changes(self) -> list[dict[str, Any]]:
        return [*self.enemy, *self.resolved]


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


def _roll_die(sides: int, roller: Callable[[int], int] | None) -> int:
    sides = max(1, int_or_default(sides, default=1))
    raw_value = roller(sides) if roller else random.randint(1, sides)
    value = int_or_default(raw_value, default=1)
    return max(1, min(sides, value))


def _roll_damage_expression(dice_expression: Any, roller: Callable[[int], int] | None) -> dict[str, Any]:
    expression = str(dice_expression or '').strip().replace(' ', '')
    parsed = parse_damage_dice_expression(expression)
    if not parsed:
        return {'dice': expression[:24], 'rolls': [], 'bonus': 0, 'total': 0}

    count = int(parsed['count'])
    sides = int(parsed['sides'])
    bonus = int(parsed['bonus'])
    rolls = [_roll_die(sides, roller) for _ in range(count)] if sides > 0 and count > 0 else []
    return {'dice': parsed['dice'], 'rolls': rolls, 'bonus': bonus, 'total': max(0, sum(rolls) + bonus)}


def _deterministic_damage_roller(seed: str) -> Callable[[int], int]:
    counter = 0

    def roll(sides: int) -> int:
        nonlocal counter
        counter += 1
        digest = hashlib.sha256(f'{seed}:{counter}:{sides}'.encode('utf-8')).digest()
        return int.from_bytes(digest[:4], 'big') % max(1, int(sides)) + 1

    return roll


def _critical_damage_expression(value: Any) -> str:
    parsed = parse_damage_dice_expression(value)
    if not parsed or int(parsed.get('count') or 0) <= 0:
        return str(value or '').strip()
    count = min(100, int(parsed['count']) * 2)
    sides = int(parsed['sides'])
    return f'{count}d{sides}'


def resolve_authoritative_player_attack(
    *,
    state: dict[str, Any],
    actor_id: str,
    turn_id: int | None,
    action_intent: dict[str, Any] | None,
    authoritative_roll: dict[str, Any] | None,
    damage_roller: Callable[[int], int] | None = None,
) -> dict[str, Any] | None:
    """Resolve a canonical HUD attack before the DM is allowed to narrate it."""

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

    natural_roll = _bounded_int(authoritative_roll.get('kept'), default=1, minimum=1, maximum=20)
    attack_total = int_or_default(authoritative_roll.get('total'), default=natural_roll)
    attack_bonus = attack_total - natural_roll
    target_ac = _target_armor_class(target)
    critical = natural_roll == 20
    hit = natural_roll != 1 and (critical or attack_total >= target_ac)
    damage_dice = normalize_damage_dice_expression(combat_action.get('damage_dice')) or '1d6'
    roll_expression = _critical_damage_expression(damage_dice) if critical else damage_dice
    modifier_breakdown = (
        authoritative_roll.get('modifier_breakdown')
        if isinstance(authoritative_roll.get('modifier_breakdown'), dict)
        else {}
    )
    damage_bonus = int_or_default(modifier_breakdown.get('ability_modifier'), default=0)
    weapon_id = str(combat_action.get('weapon_id') or '').strip()
    roller = damage_roller or _deterministic_damage_roller(
        f'player_attack:{turn_id}:{actor_id}:{target_id}:{weapon_id}'
    )
    damage_roll = (
        _roll_damage_expression(roll_expression, roller)
        if hit
        else {'dice': roll_expression, 'rolls': [], 'bonus': 0, 'total': 0}
    )
    damage_total = max(0, int(damage_roll.get('total') or 0) + damage_bonus) if hit else 0
    return {
        'sourceType': 'player_attack',
        'sourceActorId': actor_id,
        'sourceActorName': _participant_name(actor, actor_id),
        'targetId': target_id,
        'targetName': _participant_name(target, target_id),
        'weaponId': weapon_id or None,
        'weaponName': str(combat_action.get('weapon_name') or 'weapon').strip() or 'weapon',
        'attackRoll': natural_roll,
        'attackBonus': attack_bonus,
        'attackTotal': attack_total,
        'targetArmorClass': target_ac,
        'hit': hit,
        'critical': critical,
        'damageDice': damage_dice,
        'damageRolls': damage_roll.get('rolls') or [],
        'damageBonus': damage_bonus,
        'damageTotal': damage_total,
        'damageType': str(combat_action.get('damage_type') or 'slashing').strip().lower(),
        'authoritative': True,
        'instruction': 'Narrate this player attack exactly as resolved by the engine; do not alter hit, damage, or target.',
    }


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    parsed = int_or_default(value, default=default)
    return max(minimum, min(maximum, parsed))


def _target_armor_class(target: dict[str, Any] | None) -> int:
    if not isinstance(target, dict):
        return 10
    stats = target.get('stats') if isinstance(target.get('stats'), dict) else {}
    return _bounded_int(target.get('armorClass', stats.get('armorClass')), default=10, minimum=1, maximum=40)


def _ability_modifier(score: int) -> int:
    return (int(score) - 10) // 2


def _attack_stat_modifier(enemy: dict[str, Any] | None, ability: dict[str, Any] | None) -> int:
    stats = enemy.get('stats') if isinstance(enemy, dict) and isinstance(enemy.get('stats'), dict) else {}
    strength = int_or_default(stats.get('strength'), default=10)
    dexterity = int_or_default(stats.get('dexterity'), default=10)
    text = normalize_item_name(
        ' '.join(
            str(value or '')
            for value in [
                (ability or {}).get('name') if isinstance(ability, dict) else '',
                (ability or {}).get('description') if isinstance(ability, dict) else '',
                (ability or {}).get('range') if isinstance(ability, dict) else '',
            ]
        )
    )
    if re.search(r'\b(?:bow|crossbow|sling|dart|javelin|ranged|shot|arrow)\b', text):
        return _ability_modifier(dexterity)
    return max(_ability_modifier(strength), _ability_modifier(dexterity))


def _proficiency_bonus(enemy: dict[str, Any] | None) -> int:
    level = int_or_default((enemy or {}).get('level'), default=1) if isinstance(enemy, dict) else 1
    return 2 + max(0, (level - 1) // 4)


def _ability_attack_bonus(enemy: dict[str, Any] | None, ability: dict[str, Any] | None) -> int:
    if isinstance(ability, dict):
        explicit = ability.get('attackBonus', ability.get('toHitBonus'))
        if explicit is not None:
            return int_or_default(explicit, default=0)
    return _proficiency_bonus(enemy) + _attack_stat_modifier(enemy, ability)


def _ability_damage(enemy: dict[str, Any] | None, ability: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ability, dict):
        return {}
    damage = ability.get('damage')
    if isinstance(damage, dict):
        normalized_dice = normalize_damage_dice_expression(damage.get('dice'))
        result = {'type': damage.get('type') or damage.get('damageType')}
        if normalized_dice:
            result['dice'] = normalized_dice
        return result
    if isinstance(damage, str):
        normalized_dice = normalize_damage_dice_expression(damage)
        return {'dice': normalized_dice} if normalized_dice else {}
    match = TEXT_DAMAGE_PATTERN.search(str(ability.get('description') or ''))
    if match:
        normalized_dice = normalize_damage_dice_expression(match.group(1).replace(' ', ''))
        if normalized_dice:
            return {'dice': normalized_dice, 'type': match.group(2).lower()}
    return {}


def _ability_conditions(ability: dict[str, Any] | None) -> list[str]:
    if not isinstance(ability, dict):
        return []
    raw = ability.get('conditionsApplied', ability.get('conditions'))
    values = raw if isinstance(raw, list) else [raw]
    conditions: list[str] = []
    for value in values:
        condition = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
        if condition and condition not in conditions:
            conditions.append(condition)
    return conditions


def _saving_throw_modifier(target: dict[str, Any] | None, ability_name: Any) -> int:
    if not isinstance(target, dict):
        return 0
    ability = str(ability_name or '').strip().lower()
    aliases = {
        'str': 'strength',
        'dex': 'dexterity',
        'con': 'constitution',
        'int': 'intelligence',
        'wis': 'wisdom',
        'cha': 'charisma',
    }
    ability = aliases.get(ability, ability)
    short = ability[:3]
    stats = target.get('stats') if isinstance(target.get('stats'), dict) else {}
    save_sources = [
        target.get('savingThrows'),
        target.get('saving_throws'),
        stats.get('savingThrows'),
        stats.get('saving_throws'),
    ]
    for source in save_sources:
        if not isinstance(source, dict):
            continue
        for key in (ability, short, f'{ability}Save', f'{ability}_save'):
            if source.get(key) is not None:
                return int_or_default(source.get(key), default=0)
    score = int_or_default(stats.get(ability, target.get(ability)), default=10)
    return _ability_modifier(score)


def _ability_save(ability: dict[str, Any] | None) -> dict[str, Any]:
    save = ability.get('save') if isinstance(ability, dict) and isinstance(ability.get('save'), dict) else {}
    return {
        'ability': str(save.get('ability') or '').strip().lower(),
        'dc': max(1, min(40, int_or_default(save.get('dc'), default=10))),
        'effectOnSuccess': str(save.get('effectOnSuccess') or save.get('effect_on_success') or 'none').strip().lower(),
    }


def _resolve_enemy_required_actions(
    *,
    state: dict[str, Any],
    combat_context: dict[str, Any] | None,
    roller: Callable[[int], int] | None = None,
) -> list[dict[str, Any]]:
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

    participants = _combat_participants(state)
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    resolved: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        intent_type = str(action.get('intentType') or action.get('type') or '').strip().lower()
        enemy_id = str(action.get('enemyId') or action.get('actorId') or action.get('participantId') or '').strip()
        target_id = str(action.get('targetId') or action.get('targetActorId') or '').strip()
        ability_id = str(action.get('abilityId') or action.get('ability_id') or '').strip()
        enemy = _participant_by_id(participants, enemy_id)
        target = _participant_by_id(participants, target_id)
        ability = _ability_by_id(enemy, ability_id)
        if (
            not isinstance(enemy, dict)
            or str(enemy.get('team') or '').strip().lower() != 'enemy'
            or not participant_can_take_turn(enemy)
        ):
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
        }

        if intent_type in {'attack', 'use_ability'}:
            if (
                not isinstance(target, dict)
                or str(target.get('team') or '').strip().lower() not in {'player', 'ally'}
                or not participant_is_targetable(target)
                or not isinstance(ability, dict)
                or not combat_ability_is_available(ability, round_number=combat.get('round'))
            ):
                continue
            resolution_mode = combat_ability_resolution_mode(ability)
            if not resolution_mode:
                continue
            damage = _ability_damage(enemy, ability)
            conditions = _ability_conditions(ability)
            entry['resolutionMode'] = resolution_mode

            if resolution_mode in {'attack', 'attack_with_save'}:
                if not damage.get('dice'):
                    continue
                attack_bonus = _ability_attack_bonus(enemy, ability)
                attack_roll = _roll_die(20, roller)
                attack_total = attack_roll + attack_bonus
                target_ac = _target_armor_class(target)
                hit = attack_roll != 1 and (attack_roll == 20 or attack_total >= target_ac)
                damage_roll = (
                    _roll_damage_expression(damage.get('dice'), roller)
                    if hit and damage.get('dice')
                    else {'dice': damage.get('dice'), 'rolls': [], 'bonus': 0, 'total': 0}
                )
                entry.update(
                    {
                        'attackRoll': attack_roll,
                        'attackBonus': attack_bonus,
                        'attackTotal': attack_total,
                        'targetArmorClass': target_ac,
                        'hit': hit,
                        'critical': attack_roll == 20,
                        'damageDice': damage.get('dice'),
                        'damageRolls': damage_roll.get('rolls') or [],
                        'damageBonus': damage_roll.get('bonus') or 0,
                        'damageTotal': damage_roll.get('total') or 0,
                        'damageType': damage.get('type') or damage.get('damageType'),
                        'conditionsApplied': [],
                    }
                )
                if hit and resolution_mode == 'attack_with_save':
                    save = _ability_save(ability)
                    save_roll = _roll_die(20, roller)
                    save_modifier = _saving_throw_modifier(target, save['ability'])
                    save_total = save_roll + save_modifier
                    save_succeeded = save_total >= save['dc']
                    entry.update(
                        {
                            'saveAbility': save['ability'],
                            'saveDC': save['dc'],
                            'saveRoll': save_roll,
                            'saveModifier': save_modifier,
                            'saveTotal': save_total,
                            'saveSucceeded': save_succeeded,
                            'conditionsApplied': [] if save_succeeded else conditions,
                        }
                    )
            else:
                save = _ability_save(ability)
                save_roll = _roll_die(20, roller)
                save_modifier = _saving_throw_modifier(target, save['ability'])
                save_total = save_roll + save_modifier
                save_succeeded = save_total >= save['dc']
                damage_roll = (
                    _roll_damage_expression(damage.get('dice'), roller)
                    if damage.get('dice')
                    else {'dice': None, 'rolls': [], 'bonus': 0, 'total': 0}
                )
                damage_total = int_or_default(damage_roll.get('total'), default=0)
                if save_succeeded:
                    if save['effectOnSuccess'] in {'half', 'half_damage'}:
                        damage_total //= 2
                    else:
                        damage_total = 0
                entry.update(
                    {
                        'saveAbility': save['ability'],
                        'saveDC': save['dc'],
                        'saveRoll': save_roll,
                        'saveModifier': save_modifier,
                        'saveTotal': save_total,
                        'saveSucceeded': save_succeeded,
                        'hit': damage_total > 0,
                        'critical': False,
                        'damageDice': damage.get('dice'),
                        'damageRolls': damage_roll.get('rolls') or [],
                        'damageBonus': damage_roll.get('bonus') or 0,
                        'damageTotal': damage_total,
                        'damageType': damage.get('type') or damage.get('damageType'),
                        'conditionsApplied': [] if save_succeeded else conditions,
                    }
                )
        else:
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
    resolved_actions = _resolve_enemy_required_actions(state=state, combat_context=context, roller=enemy_roller)
    requested_count = len(context.get('enemyRequiredActions') or [])
    # Once the engine has attempted resolution, narration may consume only the
    # validated results.  Leaving unresolved requests in context would let the
    # model narrate mechanics the engine deliberately rejected.
    context['enemyRequiredActions'] = []
    context['enemyResolvedActions'] = resolved_actions
    if requested_count > len(resolved_actions):
        context['enemyResolutionBlockedCount'] = requested_count - len(resolved_actions)
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

        outcome_condition = {
            'retreat': 'fled',
            'flee': 'fled',
            'surrender': 'surrendered',
            'negotiate': 'negotiated',
        }.get(intent_type)
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
        if str(change.get('id') or '') in already_ids:
            continue
        signature = _damage_change_signature(change)
        if signature and signature in already_signatures:
            continue
        changes.append(change)
        already_ids.add(str(change['id']))
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
        resolved=_trusted_resolved_damage_changes(
            state=state,
            dm_context_packet=dm_context_packet,
            actor_id=actor_id,
            turn_id=turn_id,
            already_applied_changes=already_applied_changes,
        ),
    )


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
        str(resolved_player_action.get('targetId') or '').strip()
    } if resolved_player_action.get('authoritative') is True else set()
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
        if participant_id in protected_target_ids and change_type in {'combat.condition.add', 'combat.condition.remove'}:
            continue
        if actor_id in protected_target_ids and change_type in {'health.damage', 'health.heal', 'health.max.set'}:
            continue
        filtered.append(change)
    return filtered
