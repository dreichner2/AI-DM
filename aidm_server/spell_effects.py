"""Pure authoritative resolution for a small, explicit spell-effect vocabulary.

The rest of AI-DM already owns spell knowledge, preparation, slots, and action
economy.  This module deliberately does not spend those resources.  It resolves
what a validated cast does to persisted combat participants and concentration.

Supported spell payloads use this compact shape::

    {
        "id": "spell_frost_bolt",
        "name": "Frost Bolt",
        "delivery": {"type": "attack", "attackBonus": 5},
        "target": {
            "relation": "enemy",
            "rangeBands": ["near", "far"],
            "maxTargets": 1,
        },
        "effects": [
            {"kind": "damage", "dice": "1d8", "damageType": "cold"},
            {
                "kind": "condition",
                "condition": "slowed",
                "duration": {"remaining": 1, "tick": "target_turn_end"},
            },
        ],
    }

``delivery.type`` may be ``attack``, ``save``, or ``automatic``.  A save
delivery also requires the target's save ``ability`` and may set ``onSuccess``
to ``none`` or ``half``.  The effect vocabulary is ``damage``, ``healing``,
``temporary_hp``, ``condition`` (add/remove), ``modifier``, ``move``,
``remove_condition``, ``stabilize``, ``revive``, ``max_hp_bonus``,
``sleep_pool``, ``recurring`` (start-of-turn repeat saves or save/damage),
``dispel`` and ``repeat_attack`` (Spiritual Weapon's later bonus-action
attacks).  Durations are ``{"rounds", "tickOn"}`` or ``{"until": "long_rest" |
"encounter"}``.  Instead of an explicit attack bonus or save DC, trusted
definitions may provide ``castingAbility`` and let the resolver derive the
value from persisted caster stats and proficiency.

All public resolvers are storage-agnostic and return deep copies.  The caller
must persist the returned combat/resources together in the same transaction as
slot consumption and turn-economy consumption.  An injected ``roller(sides)``
makes every d20 and effect roll deterministic and testable; the resolution
records every value it consumed in ``rollSequence`` so a replay is exact.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping

from aidm_server.armor_class import armor_class_with_spell_armor
from aidm_server.character_resources import normalize_concentration, spell_cast_legality
from aidm_server.combat import vitality
from aidm_server.combat.modifiers import (
    INCAPACITATING_CONDITIONS,
    active_effects,
    apply_damage_multiplier,
    armor_class_with_context,
    attack_block_reason,
    attack_context,
    can_act,
    condition_keys,
    consume_modifiers,
    damage_defense,
    end_concentration_effects,
    end_effects_on_damage,
    extra_damage,
    heals_nothing,
    is_hidden,
    range_band as participant_range_band,
    remove_active_effects,
    roll_inspiration,
    save_context,
    tick_effects,
)
from aidm_server.combat.reactions import REACTION_SOURCE, auto_reactions_enabled, register_reaction_provider
from aidm_server.combat.state import combat_turn_context
from aidm_server.combat.positioning import RANGE_BANDS, band_index, move_participant
from aidm_server.combat.turn_start import register_turn_start_hook
from aidm_server.creatures.schemas import DAMAGE_TYPES
from aidm_server.damage_dice import deterministic_roller, parse_damage_dice_expression, roll_signed_dice
from aidm_server.game_state.models import stable_change_id


Roller = Callable[[int], int]

SUPPORTED_DELIVERY_TYPES = frozenset({'attack', 'save', 'automatic'})
SUPPORTED_EFFECT_KINDS = frozenset({
    'damage', 'healing', 'temporary_hp', 'condition', 'modifier', 'move', 'remove_condition', 'stabilize',
    'revive', 'max_hp_bonus', 'sleep_pool', 'recurring', 'dispel', 'repeat_attack',
})
SUPPORTED_RELATIONS = frozenset({'enemy', 'ally', 'self', 'any'})
SUPPORTED_SAVE_OUTCOMES = frozenset({'none', 'half'})
SUPPORTED_DURATION_TICKS = frozenset({'target_turn_start', 'target_turn_end', 'source_turn_end', 'caster_turn_end', 'caster_turn_start'})
SUPPORTED_DURATION_UNTIL = frozenset({'long_rest', 'encounter'})
SUPPORTED_CASTING_TIMES = frozenset({'action', 'bonus_action', 'reaction'})
SUPPORTED_MODIFIER_KEYS = frozenset({
    'attackBonusDice', 'saveBonusDice', 'checkBonusDice', 'acBonus', 'acMinimum', 'acBase',
    'attackMode', 'attackedMode', 'saveMode', 'checkMode', 'bonusDamageVs',
    'bonusWeaponDamage', 'resistances', 'speedZero', 'cannotAct', 'cannotReact',
    'noHealing', 'tempHpPerTurn', 'conditionImmunities',
})
# Fields an effect definition copies onto the active effect it writes.
EFFECT_LIFECYCLE_FLAGS = ('endsOnDamage', 'endsOnHarm', 'endsOnCast', 'repeatSaveOnDamage')
SHIELD_SPELL_NAME = 'Shield'
SUPPORTED_RANGE_BANDS = frozenset({'melee', 'near', 'far', 'distant'})
ABILITY_KEYS = frozenset(
    {'strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma'}
)
ABILITY_ALIASES = {
    'str': 'strength',
    'dex': 'dexterity',
    'con': 'constitution',
    'int': 'intelligence',
    'wis': 'wisdom',
    'cha': 'charisma',
}
SPELL_RESOLUTION_LEDGER_LIMIT = 200


__all__ = [
    'ABILITY_KEYS',
    'SUPPORTED_DELIVERY_TYPES',
    'SUPPORTED_EFFECT_KINDS',
    'active_repeat_attack',
    'advance_spell_effect_durations',
    'after_damage_spell_effects',
    'cannot_cast_reason',
    'end_encounter_spell_effects',
    'end_long_rest_spell_effects',
    'normalized_spell_definition',
    'resolve_concentration_check',
    'resolve_targeted_spell',
    'resolve_turn_start_spell_effects',
    'shield_reaction_provider',
    'spell_target_legality',
]


def _text(value: Any) -> str:
    return str(value or '').strip()


def _key(value: Any) -> str:
    return re.sub(r'[^a-z0-9]+', '_', _text(value).lower()).strip('_')


def _int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, _int(value, default=default)))


def _ability_key(value: Any) -> str:
    key = _key(value)
    return ABILITY_ALIASES.get(key, key)


def _participant_id(participant: Mapping[str, Any]) -> str:
    return _text(
        participant.get('id')
        or participant.get('participantId')
        or participant.get('actorId')
    )


def _participants(combat: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = combat.get('participants')
    return [participant for participant in raw if isinstance(participant, dict)] if isinstance(raw, list) else []


def _participant_by_id(
    combat: Mapping[str, Any], participant_id: Any
) -> dict[str, Any] | None:
    wanted = _text(participant_id)
    if not wanted:
        return None
    return next(
        (participant for participant in _participants(combat) if _participant_id(participant) == wanted),
        None,
    )


def _hp(participant: Mapping[str, Any]) -> dict[str, int]:
    raw = participant.get('hp') if isinstance(participant.get('hp'), Mapping) else {}
    current = max(0, _int(raw.get('current', raw.get('currentHp')), default=0))
    maximum = max(current, _int(raw.get('max', raw.get('maxHp')), default=current))
    temporary = max(0, _int(raw.get('temp', raw.get('tempHp')), default=0))
    return {'current': current, 'max': maximum, 'temp': temporary}


def _is_present(participant: Mapping[str, Any]) -> bool:
    return participant.get('isPresent', participant.get('present', True)) is not False


def _is_alive(participant: Mapping[str, Any]) -> bool:
    hp = _hp(participant)
    return participant.get('isAlive', hp['current'] > 0) is not False and hp['current'] > 0


def _can_cast(participant: Mapping[str, Any]) -> bool:
    return _hp(participant)['current'] > 0 and can_act(participant)


def cannot_cast_reason(participant: Mapping[str, Any] | None) -> str:
    """Why an active effect on the caster forbids casting ('' when none does)."""

    effect = next((effect for effect in active_effects(participant) if effect.get('cannotCast') is True), None)
    if effect is None:
        return ''
    return 'You cannot cast while raging.' if effect['sourceId'] == 'rage' else f"{effect['sourceName'] or 'An effect'} prevents spellcasting."


def _metamagic_marker(caster: Mapping[str, Any], metamagic: str) -> dict[str, Any] | None:
    """The caster's readied Metamagic (a Sorcerer's Quickened or Twinned Spell), if any."""

    return next((effect for effect in active_effects(caster) if effect.get('metamagic') == metamagic), None)


def _twinnable(spell: Mapping[str, Any], caster: Mapping[str, Any], cast_level: int) -> bool:
    """Twinned Spell readied for this spell level, and the spell can target only one creature."""

    marker = _metamagic_marker(caster, 'twinned')
    delivery = spell['delivery']
    return (
        marker is not None
        and _int(marker.get('spellLevel'), default=-1) == cast_level
        and spell['target']['maxTargets'] == 1
        and not spell['target']['areaMaxTargets']
        and not delivery.get('darts')
        and delivery.get('attacks') is None
    )


def _restores_downed_player(spell: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    """Healing or ally spells may reach a player character at 0 HP who is not dead."""

    helpful = spell['target']['relation'] == 'ally' or any(
        effect['kind'] == 'healing' for effect in spell['effects']
    )
    return helpful and vitality.is_player_character(target) and not vitality.is_dead(target)


def _harmful(spell: Mapping[str, Any]) -> bool:
    return spell['target']['relation'] == 'enemy' or any(
        effect['kind'] == 'damage' for effect in spell['effects']
    )


def _friendly(caster: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    caster_team = _key(caster.get('team'))
    target_team = _key(target.get('team'))
    if caster_team in {'player', 'ally'}:
        return target_team in {'player', 'ally'}
    return bool(caster_team) and caster_team == target_team


def _position(participant: Mapping[str, Any]) -> Mapping[str, Any]:
    value = participant.get('position')
    return value if isinstance(value, Mapping) else {}


def _cover_entry(combat: Mapping[str, Any], target: Mapping[str, Any]) -> Mapping[str, Any] | None:
    cover_id = _text(_position(target).get('coverId') or _position(target).get('cover_id'))
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), Mapping) else {}
    cover = battlefield.get('cover') if isinstance(battlefield.get('cover'), list) else []
    return next(
        (
            entry
            for entry in cover
            if isinstance(entry, Mapping) and _text(entry.get('id')) == cover_id
        ),
        None,
    )


def _active_actor_id(combat: Mapping[str, Any]) -> str:
    flags = combat.get('flags') if isinstance(combat.get('flags'), Mapping) else {}
    persisted = _text(flags.get('activeActorId') or flags.get('active_actor_id'))
    if persisted:
        return persisted
    if combat.get('turnIndex', combat.get('turn_index')) is None:
        return ''
    context = combat_turn_context(dict(combat))
    current = context.get('currentActor') if isinstance(context.get('currentActor'), Mapping) else {}
    return _text(current.get('id'))


def _target_spec(spell: Mapping[str, Any]) -> dict[str, Any]:
    raw = spell.get('target') if isinstance(spell.get('target'), Mapping) else {}
    area = raw.get('area') if isinstance(raw.get('area'), Mapping) else None
    relation = _key(raw.get('relation') or raw.get('team') or 'enemy')
    raw_bands = raw.get('rangeBands', raw.get('range_bands'))
    if isinstance(raw_bands, str):
        raw_bands = [raw_bands]
    range_band_set = {
        _key(value)
        for value in (raw_bands if isinstance(raw_bands, (list, tuple, set)) else SUPPORTED_RANGE_BANDS)
        if _key(value) in SUPPORTED_RANGE_BANDS
    }
    range_bands = [
        band for band in ('melee', 'near', 'far', 'distant') if band in range_band_set
    ]
    allow_self_value = raw.get('allowSelf', raw.get('allow_self'))
    return {
        'relation': relation,
        'rangeBands': range_bands,
        'minTargets': _bounded_int(
            raw.get('minTargets', raw.get('min_targets')),
            default=1,
            minimum=1,
            maximum=20,
        ),
        'maxTargets': 1 if area else _bounded_int(
            raw.get('maxTargets', raw.get('max_targets')),
            default=1,
            minimum=1,
            maximum=20,
        ),
        'areaMaxTargets': _bounded_int(area.get('maxTargets'), default=1, minimum=1, maximum=20) if area else 0,
        'allowDefeated': raw.get('allowDefeated', raw.get('allow_defeated', False)) is True,
        'requiresDying': raw.get('requiresDying', raw.get('requires_dying', False)) is True,
        'requiresDead': raw.get('requiresDead', raw.get('requires_dead', False)) is True,
        'requiresPresent': raw.get('requiresPresent', raw.get('requires_present', True)) is not False,
        'requiresLineOfSight': raw.get(
            'requiresLineOfSight', raw.get('requires_line_of_sight', True)
        ) is not False,
        'requiresSameZone': raw.get(
            'requiresSameZone', raw.get('requires_same_zone', False)
        ) is True,
        'ignoreCover': raw.get('ignoreCover', raw.get('ignore_cover', False)) is True,
        'allowSelf': (
            allow_self_value is True
            if allow_self_value is not None
            else relation in {'ally', 'self', 'any'}
        ),
    }


def _spell_id(spell: Mapping[str, Any]) -> str:
    spell_id = _text(spell.get('id') or spell.get('spellId'))
    name = _text(spell.get('name') or spell.get('spellName'))
    return spell_id or (f'spell_{_key(name)}' if name else '')


def _delivery_spec(spell: Mapping[str, Any]) -> dict[str, Any]:
    raw = spell.get('delivery')
    if isinstance(raw, str):
        raw = {'type': raw}
    raw = raw if isinstance(raw, Mapping) else {}
    attack_bonus = raw.get('attackBonus', raw.get('attack_bonus'))
    save_dc = raw.get('dc', raw.get('saveDc', raw.get('save_dc')))
    darts = raw.get('darts') if isinstance(raw.get('darts'), Mapping) else None
    return {
        'type': _key(raw.get('type') or 'automatic'),
        'attackBonus': _int(attack_bonus) if attack_bonus is not None else None,
        'ability': _ability_key(raw.get('ability') or raw.get('saveAbility')),
        'dc': _int(save_dc) if save_dc is not None else None,
        'castingAbility': _ability_key(
            raw.get('castingAbility')
            or raw.get('casting_ability')
            or spell.get('castingAbility')
            or spell.get('casting_ability')
        ),
        'onSuccess': _key(raw.get('onSuccess', raw.get('on_success')) or 'none'),
        'combatSaveMode': _key(raw.get('combatSaveMode')),
        'ranged': raw.get('ranged') if isinstance(raw.get('ranged'), bool) else None,
        'darts': (
            {'count': _bounded_int(darts.get('count'), default=1, minimum=1, maximum=20), 'dice': darts.get('dice')}
            if darts else None
        ),
        'attacks': _bounded_int(raw.get('attacks'), default=1, minimum=1, maximum=20) if raw.get('attacks') is not None else None,
    }


def _recurring_spec(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    if raw.get('repeatSave') is None and not isinstance(raw.get('save'), Mapping):
        return None
    save = raw.get('save') if isinstance(raw.get('save'), Mapping) else {}
    damage = raw.get('damage') if isinstance(raw.get('damage'), Mapping) else {}
    aura = raw.get('aura') if isinstance(raw.get('aura'), Mapping) else None
    parsed = parse_damage_dice_expression(damage.get('dice')) if damage else None
    return {
        'repeatSave': raw.get('repeatSave') is not None,
        'ability': _ability_key(raw.get('repeatSave') or save.get('ability')),
        'onSuccess': _key(save.get('onSuccess') or 'none'),
        'damageExpression': str(parsed['dice']) if parsed else None,
        'hasDamage': bool(damage),
        'damageType': _key(damage.get('damageType') or damage.get('type')),
        'aura': (
            {
                'relation': _key(aura.get('relation') or 'enemy'),
                'rangeBands': [_key(band) for band in aura.get('rangeBands') or [] if _key(band)],
            }
            if aura is not None else None
        ),
    }


def _effect_spec(raw: Mapping[str, Any]) -> dict[str, Any]:
    kind = _key(raw.get('kind') or raw.get('type'))
    dice_value = raw.get('dice')
    amount_value = raw.get('amount')
    expression = None
    if dice_value is not None:
        parsed = parse_damage_dice_expression(dice_value)
        expression = str(parsed['dice']) if parsed else None
    elif amount_value is not None:
        parsed = parse_damage_dice_expression(amount_value)
        expression = str(parsed['dice']) if parsed else None
    recurring = _recurring_spec(raw) if kind == 'recurring' else None
    if recurring and recurring['damageExpression']:
        expression = recurring['damageExpression']

    wounded_dice = raw.get('woundedDice')
    wounded_parsed = parse_damage_dice_expression(wounded_dice) if wounded_dice is not None else None

    duration_raw = raw.get('duration') if isinstance(raw.get('duration'), Mapping) else None
    duration: dict[str, Any] | None = None
    until = _key(duration_raw.get('until')) if duration_raw is not None else ''
    rounds = (
        _int(duration_raw.get('remaining', duration_raw.get('rounds')), default=0)
        if duration_raw is not None
        else _int(raw.get('durationRounds', raw.get('duration_rounds')), default=0)
    )
    if not until and rounds > 0:
        duration = {
            'remaining': min(1000, rounds),
            'tick': _key((duration_raw or {}).get('tickOn') or (duration_raw or {}).get('tick') or 'target_turn_end'),
        }

    damage_type = _key(raw.get('damageType') or raw.get('damage_type') or 'force')
    if recurring and recurring['hasDamage']:
        damage_type = recurring['damageType']
    condition = _key(raw.get('condition'))
    operation = _key(raw.get('operation') or 'add')
    choices = raw.get('damageTypeChoices')
    return {
        'kind': kind,
        'expression': expression,
        'woundedExpression': str(wounded_parsed['dice']) if wounded_parsed else None,
        'hasWoundedDice': wounded_dice is not None,
        'damageType': damage_type,
        'damageTypeChoices': [_key(value) for value in choices if _key(value)] if isinstance(choices, (list, tuple)) else [],
        'condition': condition,
        'operation': operation,
        'duration': duration,
        'until': until,
        'modifiers': deepcopy(dict(raw.get('modifiers'))) if isinstance(raw.get('modifiers'), Mapping) else {},
        'consumeOn': _key(raw.get('consumeOn')),
        'bands': _int(raw.get('bands'), default=0),
        'teleport': _key(raw.get('teleport')),
        'conditions': [_key(value) for value in raw.get('conditions') or [] if _key(value)]
        if isinstance(raw.get('conditions'), (list, tuple)) else [],
        'removeAll': raw.get('removeAll') is True,
        'addCastingModifier': raw.get('addCastingModifier') is True,
        'castingModifierKeys': [_text(value) for value in raw.get('castingModifierKeys') or [] if _text(value)],
        'onCaster': raw.get('onCaster') is True,
        'hp': _int(raw.get('hp'), default=1),
        'recurring': recurring,
        **{flag: raw.get(flag) is True for flag in EFFECT_LIFECYCLE_FLAGS},
        'onSuccessfulSave': _key(
            raw.get('onSuccessfulSave', raw.get('on_successful_save')) or ''
        ),
    }


def _normalized_spell(spell: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
    spell_id = _spell_id(spell)
    spell_name = _text(spell.get('name') or spell.get('spellName'))
    if not spell_id or not spell_name:
        return None, 'Spell effects require a stable spell id and name.'

    delivery = _delivery_spec(spell)
    if delivery['type'] not in SUPPORTED_DELIVERY_TYPES:
        return None, f"Unsupported spell delivery type: {delivery['type'] or 'missing'}."
    if delivery['darts'] and (delivery['type'] != 'automatic' or not parse_damage_dice_expression(delivery['darts']['dice'])):
        return None, 'Dart delivery requires automatic hits and bounded dice.'
    if delivery['attacks'] is not None and delivery['type'] != 'attack':
        return None, 'Multiple attacks require attack delivery.'
    if delivery['type'] == 'attack':
        if delivery['attackBonus'] is not None and not -20 <= delivery['attackBonus'] <= 30:
            return None, 'Spell attack bonus must be between -20 and 30.'
        if delivery['attackBonus'] is None and delivery['castingAbility'] not in ABILITY_KEYS:
            return None, 'Attack delivery requires an attackBonus or valid castingAbility.'
    if delivery['type'] == 'save':
        if delivery['ability'] not in ABILITY_KEYS:
            return None, 'Save delivery requires a valid saving-throw ability.'
        if delivery['dc'] is not None and not 1 <= delivery['dc'] <= 40:
            return None, 'Save delivery DC must be between 1 and 40.'
        if delivery['dc'] is None and delivery['castingAbility'] not in ABILITY_KEYS:
            return None, 'Save delivery requires a DC or valid castingAbility.'
        if delivery['onSuccess'] not in SUPPORTED_SAVE_OUTCOMES:
            return None, 'Save success behavior must be none or half.'
        if delivery['combatSaveMode'] not in {'', 'advantage', 'disadvantage'}:
            return None, 'A combat save mode must be advantage or disadvantage.'
    casting_time = _key(spell.get('castingTime') or 'action')
    if casting_time not in SUPPORTED_CASTING_TIMES:
        return None, 'Casting time must be action, bonus_action, or reaction.'
    consumes = spell.get('consumes') if isinstance(spell.get('consumes'), Mapping) else {}
    if set(consumes) - {'gp'} or _int(consumes.get('gp'), default=0) < 0:
        return None, 'A spell may consume only a non-negative gp cost.'

    target = _target_spec(spell)
    if target['relation'] not in SUPPORTED_RELATIONS:
        return None, f"Unsupported target relation: {target['relation'] or 'missing'}."
    if not target['rangeBands']:
        return None, 'Spell target rangeBands must contain at least one supported range band.'
    if target['minTargets'] > target['maxTargets']:
        return None, 'Spell minTargets cannot exceed maxTargets.'

    raw_effects = spell.get('effects')
    if not isinstance(raw_effects, list) or not raw_effects:
        return None, 'Spell effects require a non-empty effects list.'
    effects: list[dict[str, Any]] = []
    for index, raw_effect in enumerate(raw_effects):
        if not isinstance(raw_effect, Mapping):
            return None, f'Spell effect {index + 1} must be an object.'
        effect = _effect_spec(raw_effect)
        if effect['kind'] not in SUPPORTED_EFFECT_KINDS:
            return None, f"Unsupported spell effect kind: {effect['kind'] or 'missing'}."
        if effect['kind'] in {'damage', 'healing', 'temporary_hp', 'sleep_pool', 'max_hp_bonus'} and not effect['expression']:
            return None, f"Spell effect {index + 1} requires a bounded dice or amount expression."
        if effect['hasWoundedDice'] and (effect['kind'] != 'damage' or not effect['woundedExpression']):
            return None, 'Wounded-target damage requires bounded alternate damage dice.'
        if effect['kind'] == 'damage' and effect['damageType'] not in DAMAGE_TYPES:
            return None, f"Unsupported damage type: {effect['damageType'] or 'missing'}."
        if set(effect['damageTypeChoices']) - DAMAGE_TYPES or (
            effect['damageTypeChoices'] and effect['damageType'] not in effect['damageTypeChoices']
        ):
            return None, 'Damage type choices must be damage types that include the default.'
        if effect['kind'] == 'condition':
            if not effect['condition']:
                return None, f'Spell effect {index + 1} requires a condition.'
            if effect['operation'] not in {'add', 'remove'}:
                return None, 'Condition operation must be add or remove.'
        duration = effect['duration']
        if duration and duration['tick'] not in SUPPORTED_DURATION_TICKS:
            return None, 'Effect duration has an unsupported turn boundary.'
        if effect['until'] and effect['until'] not in SUPPORTED_DURATION_UNTIL:
            return None, 'Effect duration must last until a long rest or the end of the encounter.'
        if effect['kind'] == 'modifier':
            if not effect['modifiers'] or set(effect['modifiers']) - SUPPORTED_MODIFIER_KEYS:
                return None, 'Modifier effects require supported modifier keys.'
            if set(effect['castingModifierKeys']) - set(effect['modifiers']):
                return None, 'Casting-modifier keys must name modifiers of the same effect.'
        if effect['consumeOn'] not in {'', 'attack', 'attacked', 'save', 'check'}:
            return None, 'Effect consumeOn has an unsupported event.'
        if effect['kind'] == 'recurring':
            recurring = effect['recurring']
            if recurring is None or recurring['ability'] not in ABILITY_KEYS:
                return None, 'A recurring effect requires a repeat save or a saving throw ability.'
            if not recurring['repeatSave'] and (
                not recurring['damageExpression'] or recurring['damageType'] not in DAMAGE_TYPES
                or recurring['onSuccess'] not in SUPPORTED_SAVE_OUTCOMES
            ):
                return None, 'A recurring saving throw requires bounded damage of a known type.'
            aura = recurring['aura']
            if aura is not None and (
                aura['relation'] not in SUPPORTED_RELATIONS
                or not aura['rangeBands'] or set(aura['rangeBands']) - SUPPORTED_RANGE_BANDS
            ):
                return None, 'A recurring aura requires a relation and supported range bands.'
            if not duration and not effect['until']:
                return None, 'A recurring effect requires a duration.'
        if effect['kind'] == 'revive' and effect['hp'] < 1:
            return None, 'Revival must restore at least one hit point.'
        if effect['kind'] == 'move' and not (
            (effect['bands'] in {-1, 1} and not effect['teleport'])
            or (effect['teleport'] == 'away' and effect['bands'] == 0)
        ):
            return None, 'Movement must push or pull one band or teleport away.'
        if effect['kind'] == 'remove_condition' and not effect['conditions']:
            return None, 'Removing a condition requires a non-empty condition list.'
        if effect['onSuccessfulSave'] and effect['onSuccessfulSave'] not in {'none', 'half', 'full'}:
            return None, 'Effect onSuccessfulSave must be none, half, or full.'
        effects.append(effect)

    return (
        {
            'id': spell_id,
            'name': spell_name,
            'level': _bounded_int(spell.get('level'), default=0, minimum=0, maximum=9),
            'delivery': delivery,
            'target': target,
            'effects': effects,
            'scaling': deepcopy(spell.get('scaling')) if isinstance(spell.get('scaling'), Mapping) else {},
            'outOfCombat': spell.get('outOfCombat') is True,
            'outOfCombatOnly': spell.get('outOfCombatOnly') is True,
            'concentration': spell.get('concentration') is True,
            'castingTime': casting_time,
            'consumes': {'gp': _int(consumes.get('gp'), default=0)} if consumes else {},
            'requireActiveTurn': spell.get(
                'requireActiveTurn', spell.get('require_active_turn', True)
            ) is not False,
            'requireActiveCombat': spell.get(
                'requireActiveCombat', spell.get('require_active_combat', True)
            ) is not False,
        },
        '',
    )


def normalized_spell_definition(spell: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """The resolver's normalised view of a definition, or ``None`` and the reason it is invalid."""

    return _normalized_spell(spell)


def _legality_failure(code: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {'legal': False, 'code': code, 'reason': reason, **extra}


def spell_target_legality(
    combat: Mapping[str, Any],
    *,
    caster_id: str,
    spell: Mapping[str, Any],
    target_ids: Iterable[str],
    cast_level: int | None = None,
) -> dict[str, Any]:
    """Validate an entire exact-ID target set atomically without rolling."""

    normalized_spell, error = _normalized_spell(spell)
    if normalized_spell is None:
        return _legality_failure('spell_definition_invalid', error, targets=[])

    caster = _participant_by_id(combat, caster_id)
    if caster is None:
        return _legality_failure(
            'spell_caster_missing',
            'The casting participant is not present in this encounter.',
            targets=[],
        )
    if not _can_cast(caster):
        return _legality_failure(
            'spell_caster_unavailable',
            'The casting participant is unable to cast.',
            targets=[],
        )
    caster_blocker = cannot_cast_reason(caster)
    if caster_blocker:
        return _legality_failure('spell_caster_cannot_cast', caster_blocker, targets=[])
    spell_level = normalized_spell['level']
    selected_level = spell_level if cast_level is None else _int(cast_level, default=-1)
    if selected_level < spell_level or selected_level > 9 or (spell_level == 0 and selected_level != 0):
        return _legality_failure('invalid_cast_level', 'The selected cast level is not valid for this spell.', targets=[])
    if not _apply_spell_scaling(normalized_spell, caster, selected_level):
        return _legality_failure('spell_scaling_invalid', 'This spell has an invalid scaling definition.', targets=[])
    if _twinnable(normalized_spell, caster, selected_level):
        # Twinned Spell: a second legal target, resolved with its own rolls.
        normalized_spell['target']['maxTargets'] = 2
        normalized_spell['twinnable'] = True
    delivery = normalized_spell['delivery']
    if delivery['type'] == 'attack' and delivery['attackBonus'] is None:
        delivery['attackBonus'] = _spell_attack_bonus(caster, delivery['castingAbility'])
        if delivery['attackBonus'] is None:
            return _legality_failure(
                'spell_definition_invalid',
                'The caster has no authoritative score for this spell casting ability.',
                targets=[],
            )
    if delivery['type'] == 'save' and delivery['dc'] is None:
        delivery['dc'] = _spell_save_dc(caster, delivery['castingAbility'])
        if delivery['dc'] is None:
            return _legality_failure(
                'spell_definition_invalid',
                'The caster has no authoritative score for this spell casting ability.',
                targets=[],
            )

    status = _key(combat.get('status'))
    in_combat = status in {'starting', 'active'}
    if in_combat and normalized_spell['outOfCombatOnly']:
        return _legality_failure('spell_combat_forbidden', 'This spell can only be cast outside combat.', targets=[])
    if normalized_spell['requireActiveCombat'] and not in_combat and not normalized_spell['outOfCombat']:
        return _legality_failure(
            'spell_combat_inactive',
            'This spell action requires an active encounter.',
            targets=[],
        )
    if normalized_spell['requireActiveTurn'] and (in_combat or not normalized_spell['outOfCombat']):
        active_actor_id = _active_actor_id(combat)
        if not active_actor_id:
            return _legality_failure(
                'spell_turn_unresolved',
                'Combat turn order is not established.',
                targets=[],
            )
        if active_actor_id != _text(caster_id):
            return _legality_failure(
                'spell_out_of_turn',
                'Only the active combat participant may cast this spell.',
                targets=[],
            )

    normalized_ids = [_text(target_id) for target_id in target_ids if _text(target_id)]
    if len(normalized_ids) != len(set(normalized_ids)):
        return _legality_failure(
            'spell_target_duplicate',
            'A spell cannot target the same participant more than once.',
            targets=[],
        )
    target_spec = normalized_spell['target']
    if not target_spec['minTargets'] <= len(normalized_ids) <= target_spec['maxTargets']:
        return _legality_failure(
            'spell_target_count_invalid',
            (
                f"This spell requires {target_spec['minTargets']} to "
                f"{target_spec['maxTargets']} exact targets."
            ),
            targets=[],
        )

    target_results: list[dict[str, Any]] = []
    for target_id in normalized_ids:
        target = _participant_by_id(combat, target_id)
        code = ''
        reason = ''
        if target is None:
            code = 'spell_target_missing'
            reason = 'The selected target is no longer part of this encounter.'
        elif target_spec['requiresPresent'] and not _is_present(target):
            code = 'spell_target_absent'
            reason = 'The selected target is not physically present.'
        elif target_spec['requiresDying'] and not vitality.is_dying(target):
            code = 'spell_target_not_dying'
            reason = 'This spell requires a dying target.'
        elif target_spec['requiresDead'] and not vitality.is_dead(target):
            code = 'spell_target_not_dead'
            reason = 'This spell can only restore a creature that has died.'
        elif (
            not target_spec['requiresDead']
            and not target_spec['allowDefeated']
            and not _is_alive(target)
            and not _restores_downed_player(normalized_spell, target)
        ):
            code = 'spell_target_defeated'
            reason = 'The selected target is already defeated.'
        elif not in_combat and normalized_spell['outOfCombat'] and not _friendly(caster, target):
            code = 'spell_target_relation_invalid'
            reason = 'Outside combat, this spell can target only the party.'
        elif _harmful(normalized_spell) and attack_block_reason(caster, target):
            code = 'spell_target_charmer'
            reason = attack_block_reason(caster, target)
        elif target_spec['relation'] == 'self' and target_id != _text(caster_id):
            code = 'spell_target_relation_invalid'
            reason = 'This spell can target only its caster.'
        elif target_spec['relation'] == 'ally' and not _friendly(caster, target):
            code = 'spell_target_relation_invalid'
            reason = 'This spell requires an allied target.'
        elif target_spec['relation'] == 'enemy' and (
            _friendly(caster, target) or _key(target.get('team')) == 'neutral'
        ):
            code = 'spell_target_relation_invalid'
            reason = 'This spell requires a hostile target.'
        elif target_id == _text(caster_id) and not target_spec['allowSelf']:
            code = 'spell_target_relation_invalid'
            reason = 'This spell cannot target its caster.'

        if target is not None and not code and target_id != _text(caster_id) and in_combat:
            position = _position(target)
            range_band = _key(position.get('rangeBand') or position.get('range_band') or 'near')
            caster_zone = _text(_position(caster).get('zoneId') or _position(caster).get('zone_id'))
            target_zone = _text(position.get('zoneId') or position.get('zone_id'))
            cover = _cover_entry(combat, target)
            cover_type = _key((cover or {}).get('coverType') or (cover or {}).get('cover_type'))
            hidden = is_hidden(target)
            if range_band not in target_spec['rangeBands']:
                code = 'spell_target_out_of_range'
                reason = f'The selected target is at {range_band or "unknown"} range.'
            elif target_spec['requiresSameZone'] and caster_zone and target_zone != caster_zone:
                code = 'spell_target_wrong_zone'
                reason = 'The selected target is in another battlefield zone.'
            elif target_spec['requiresLineOfSight'] and hidden:
                code = 'spell_target_hidden'
                reason = 'The selected target is hidden from the caster.'
            elif (
                target_spec['requiresLineOfSight']
                and not target_spec['ignoreCover']
                and cover_type == 'full'
            ):
                code = 'spell_target_full_cover'
                reason = 'The selected target has full cover.'

        target_results.append(
            {
                'id': target_id,
                'legal': not code,
                'code': code or None,
                'reason': reason,
            }
        )

    invalid = next((result for result in target_results if not result['legal']), None)
    if invalid:
        return _legality_failure(
            invalid['code'],
            invalid['reason'],
            targets=target_results,
            spell=normalized_spell,
        )
    return {
        'legal': True,
        'code': None,
        'reason': '',
        'targets': target_results,
        'spell': normalized_spell,
    }


def _roll_die(sides: int, roller: Roller) -> int:
    sides = max(1, int(sides))
    return max(1, min(sides, _int(roller(sides), default=1)))


def _roll_expression(expression: str, roller: Roller, *, critical: bool = False) -> dict[str, Any]:
    parsed = parse_damage_dice_expression(expression)
    if not parsed:
        raise ValueError('Invalid bounded dice expression reached resolution.')
    count = int(parsed['count'])
    sides = int(parsed['sides'])
    bonus = int(parsed['bonus'])
    if critical and count > 0:
        count *= 2
    rolls = [_roll_die(sides, roller) for _ in range(count)] if sides and count else []
    total = max(0, sum(rolls) + bonus)
    return {
        'dice': str(parsed['dice']),
        'criticalDice': critical and int(parsed['count']) > 0,
        'rolls': rolls,
        'bonus': bonus,
        'total': total,
    }


def _scaled_expression(expression: str, *, multiplier: int = 1, extra_dice: Any = None, steps: int = 0, flat: int = 0) -> str | None:
    """Combine compatible bounded dice without introducing a second roll path."""

    base = parse_damage_dice_expression(expression)
    extra = parse_damage_dice_expression(extra_dice) if extra_dice is not None else None
    if not base or (extra_dice is not None and not extra):
        return None
    count = int(base['count']) * multiplier
    sides = int(base['sides'])
    bonus = int(base['bonus']) + flat * steps
    if extra and steps:
        if sides and int(extra['sides']) and sides != int(extra['sides']):
            return None
        sides = sides or int(extra['sides'])
        count += int(extra['count']) * steps
        bonus += int(extra['bonus']) * steps
    expression = f'{count}d{sides}{bonus:+d}' if count and bonus else f'{count}d{sides}' if count else str(bonus)
    parsed = parse_damage_dice_expression(expression)
    return str(parsed['dice']) if parsed else None


def _apply_spell_scaling(spell: dict[str, Any], caster: Mapping[str, Any], cast_level: int) -> bool:
    scaling = spell.get('scaling') if isinstance(spell.get('scaling'), Mapping) else {}
    if scaling.get('cantrip') is True and spell['level'] == 0:
        level = _bounded_int(caster.get('level'), default=1, minimum=1, maximum=20)
        tier = 4 if level >= 17 else 3 if level >= 11 else 2 if level >= 5 else 1
        if spell['delivery'].get('attacks') is not None:
            spell['delivery']['attacks'] = min(20, spell['delivery']['attacks'] * tier)
            spell['target']['maxTargets'] = spell['delivery']['attacks']
        else:
            for effect in spell['effects']:
                if effect['kind'] == 'damage':
                    expression = _scaled_expression(str(effect['expression']), multiplier=tier)
                    if expression is None:
                        return False
                    effect['expression'] = expression
                    if effect.get('woundedExpression'):
                        wounded = _scaled_expression(str(effect['woundedExpression']), multiplier=tier)
                        if wounded is None:
                            return False
                        effect['woundedExpression'] = wounded
    upcast = scaling.get('upcast') if isinstance(scaling.get('upcast'), Mapping) else {}
    levels_above = max(0, cast_level - int(spell['level']))
    steps = levels_above // max(1, _int(upcast.get('perLevels'), default=1))
    spell['target']['maxTargets'] = min(
        20, spell['target']['maxTargets'] + steps * max(0, _int(upcast.get('targets'), default=0)),
    )
    darts = spell['delivery'].get('darts')
    if isinstance(darts, dict):
        darts['count'] = min(20, darts['count'] + steps * max(0, _int(upcast.get('darts'), default=0)))
        spell['target']['maxTargets'] = darts['count']
    if spell['delivery'].get('attacks') is not None:
        spell['delivery']['attacks'] = min(20, spell['delivery']['attacks'] + steps * max(0, _int(upcast.get('attacks'), default=0)))
        spell['target']['maxTargets'] = spell['delivery']['attacks']
    if steps and (upcast.get('dice') is not None or upcast.get('flat') is not None):
        # The first damage or healing effect takes the extra dice; a recurring
        # save/damage effect (Moonbeam) repeats the upcast damage every turn.
        scaled = [effect for effect in spell['effects'] if effect['expression'] and effect['kind'] in {
            'damage', 'healing', 'temporary_hp', 'sleep_pool', 'max_hp_bonus', 'recurring',
        }]
        if not scaled:
            return False
        for effect in [scaled[0], *(item for item in scaled[1:] if item['kind'] == 'recurring')]:
            expression = _scaled_expression(
                str(effect['expression']), extra_dice=upcast.get('dice'), steps=steps,
                flat=_int(upcast.get('flat'), default=0),
            )
            if expression is None:
                return False
            effect['expression'] = expression
    return True


def _armor_class(target: Mapping[str, Any], context: Mapping[str, Any]) -> int:
    stats = target.get('stats') if isinstance(target.get('stats'), Mapping) else {}
    base = _bounded_int(
        target.get('armorClass', stats.get('armorClass', stats.get('armor_class'))),
        default=10,
        minimum=1,
        maximum=40,
    )
    return armor_class_with_context(base, context)


def _d20(mode: str, roller: Roller) -> tuple[int, list[int]]:
    rolls = [_roll_die(20, roller)]
    if mode in {'advantage', 'disadvantage'}:
        rolls.append(_roll_die(20, roller))
    kept = max(rolls) if mode == 'advantage' else min(rolls) if mode == 'disadvantage' else rolls[0]
    return kept, rolls


def _bonus_dice(dice: Iterable[str], roller: Roller) -> list[dict[str, Any]]:
    return [
        rolled
        for expression in dice
        if (rolled := roll_signed_dice(expression, lambda sides: _roll_die(sides, roller))) is not None
    ]


def _proficiency_bonus(participant: Mapping[str, Any]) -> int:
    stats = participant.get('stats') if isinstance(participant.get('stats'), Mapping) else {}
    explicit = stats.get('proficiencyBonus', stats.get('proficiency_bonus'))
    if explicit is None:
        explicit = participant.get('proficiencyBonus', participant.get('proficiency_bonus'))
    if explicit is not None:
        return _bounded_int(explicit, default=2, minimum=0, maximum=12)
    level = _bounded_int(participant.get('level'), default=1, minimum=1, maximum=40)
    return 2 + max(0, (level - 1) // 4)


def _ability_modifier(participant: Mapping[str, Any], ability: str) -> int | None:
    if ability not in ABILITY_KEYS:
        return None
    stats = participant.get('stats') if isinstance(participant.get('stats'), Mapping) else {}
    raw_score = stats.get(ability, stats.get(ability[:3], participant.get(ability)))
    if raw_score is None:
        return None
    score = _bounded_int(raw_score, default=10, minimum=1, maximum=40)
    return (score - 10) // 2


def _spell_attack_bonus(participant: Mapping[str, Any], casting_ability: str) -> int | None:
    stats = participant.get('stats') if isinstance(participant.get('stats'), Mapping) else {}
    for container in (participant, stats):
        explicit = container.get('spellAttackBonus', container.get('spell_attack_bonus'))
        if explicit is not None:
            return _bounded_int(explicit, default=0, minimum=-20, maximum=30)
    modifier = _ability_modifier(participant, casting_ability)
    return None if modifier is None else modifier + _proficiency_bonus(participant)


def _spell_save_dc(participant: Mapping[str, Any], casting_ability: str) -> int | None:
    stats = participant.get('stats') if isinstance(participant.get('stats'), Mapping) else {}
    for container in (participant, stats):
        explicit = container.get('spellSaveDc', container.get('spell_save_dc'))
        if explicit is not None:
            return _bounded_int(explicit, default=10, minimum=1, maximum=40)
    modifier = _ability_modifier(participant, casting_ability)
    return None if modifier is None else 8 + modifier + _proficiency_bonus(participant)


def _save_modifier(participant: Mapping[str, Any], ability: str) -> int:
    stats = participant.get('stats') if isinstance(participant.get('stats'), Mapping) else {}
    for container in (participant, stats):
        for map_key in ('savingThrowModifiers', 'saving_throw_modifiers', 'saveModifiers', 'save_modifiers'):
            modifiers = container.get(map_key)
            if isinstance(modifiers, Mapping):
                explicit = modifiers.get(ability, modifiers.get(ability[:3]))
                if explicit is not None:
                    return _bounded_int(explicit, default=0, minimum=-20, maximum=30)
        for direct_key in (
            f'{ability}Save',
            f'{ability}_save',
            f'{ability[:3]}Save',
            f'{ability[:3]}_save',
        ):
            if container.get(direct_key) is not None:
                return _bounded_int(container.get(direct_key), default=0, minimum=-20, maximum=30)

    score = _bounded_int(
        stats.get(ability, stats.get(ability[:3], participant.get(ability))),
        default=10,
        minimum=1,
        maximum=40,
    )
    modifier = (score - 10) // 2
    raw_proficiencies = (
        participant.get('savingThrowProficiencies')
        or participant.get('saving_throw_proficiencies')
        or stats.get('savingThrowProficiencies')
        or stats.get('saving_throw_proficiencies')
        or []
    )
    if isinstance(raw_proficiencies, str):
        raw_proficiencies = [raw_proficiencies]
    proficiencies = {_ability_key(value) for value in raw_proficiencies}
    if ability in proficiencies:
        modifier += _proficiency_bonus(participant)
    return modifier


def _condition_immunities(participant: Mapping[str, Any]) -> set[str]:
    immunities: set[str] = set()
    for key in ('conditionImmunities', 'condition_immunities'):
        values = participant.get(key)
        if isinstance(values, str):
            values = [values]
        if isinstance(values, (list, tuple, set)):
            immunities = {_key(value) for value in values if _text(value)}
            break
    for effect in active_effects(participant):
        values = effect['modifiers'].get('conditionImmunities')
        if isinstance(values, (list, tuple)):
            immunities.update(_key(value) for value in values if _text(value))
    return immunities


def _effect_id(cast_id: str, target_id: str, effect_index: int) -> str:
    digest = hashlib.sha256(f'{cast_id}:{target_id}:{effect_index}'.encode()).hexdigest()[:20]
    return f'sfx_{digest}'


def _set_conditions(participant: dict[str, Any], conditions: Iterable[str]) -> None:
    participant['conditions'] = list(dict.fromkeys(_key(value) for value in conditions if _key(value)))


def _start_concentration(
    combat: dict[str, Any],
    resources: Mapping[str, Any] | None,
    *,
    caster_id: str,
    spell: Mapping[str, Any],
    target_ids: list[str],
    current_turn: int | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    next_resources = deepcopy(dict(resources or {}))
    previous = normalize_concentration(next_resources.get('concentration'))
    removed: list[dict[str, Any]] = []
    if previous:
        previous_caster = _text(previous.get('casterActorId')) or caster_id
        removed = end_concentration_effects(
            combat,
            caster_id=previous_caster,
            source_id=_text(previous.get('spellId')),
        )
    concentration: dict[str, Any] = {
        'active': True,
        'spellId': spell['id'],
        'spellName': spell['name'],
        'casterActorId': caster_id,
        'targetIds': list(target_ids),
    }
    if current_turn is not None and _int(current_turn, default=0) > 0:
        concentration['startedAtTurn'] = _int(current_turn)
    next_resources['concentration'] = concentration
    next_resources['revision'] = max(0, _int(next_resources.get('revision'))) + 1
    return next_resources, previous, removed


def _ledger(combat: Mapping[str, Any]) -> list[dict[str, Any]]:
    flags = combat.get('flags') if isinstance(combat.get('flags'), Mapping) else {}
    values = flags.get('spellResolutionLedger')
    return [deepcopy(value) for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def _resolve_delivery(
    combat: Mapping[str, Any],
    caster: Mapping[str, Any],
    target: Mapping[str, Any],
    delivery: Mapping[str, Any],
    target_spec: Mapping[str, Any],
    roller: Roller,
) -> dict[str, Any]:
    delivery_type = delivery['type']
    if delivery_type == 'automatic':
        return {'type': 'automatic', 'delivered': True, 'critical': False, 'consume': []}
    if delivery_type == 'attack':
        context = attack_context(
            caster,
            target,
            kind='spell_attack',
            ranged=(delivery['ranged'] if delivery.get('ranged') is not None
                    else list(target_spec.get('rangeBands') or []) != ['melee']),
            combat=combat,
            ignore_cover=target_spec.get('ignoreCover') is True,
        )
        natural_roll, rolls = _d20(context['mode'], roller)
        bonus_dice = _bonus_dice(context['attackBonusDice'], roller)
        attack_bonus = _bounded_int(delivery.get('attackBonus'), default=0, minimum=-20, maximum=30)
        target_ac = _armor_class(target, context)
        total = natural_roll + attack_bonus + context['attackFlatBonus'] + sum(item['total'] for item in bonus_dice)
        hit = natural_roll != 1 and (natural_roll == 20 or total >= target_ac)
        inspiration = roll_inspiration(caster, roller) if not hit and natural_roll != 1 else None
        if inspiration:
            total += inspiration['roll']
            context['consume'].append(inspiration.pop('consume'))
            hit = total >= target_ac
        return {
            'type': 'attack',
            'delivered': hit,
            'critical': hit and (natural_roll == 20 or context['autoCrit']),
            'mode': context['mode'],
            'modeReasons': context['reasons'],
            'rolls': rolls,
            'naturalRoll': natural_roll,
            'modifier': attack_bonus,
            'bonusDice': bonus_dice,
            'total': total,
            'targetArmorClass': target_ac,
            'coverBonus': context['coverBonus'],
            'consume': context['consume'],
            **({'inspiration': inspiration} if inspiration else {}),
        }
    ability = str(delivery['ability'])
    context = save_context(target, ability, combat=combat)
    if delivery.get('combatSaveMode') and _key(combat.get('status')) in {'starting', 'active'}:
        context = _with_extra_mode(context, delivery['combatSaveMode'], 'The spell grants this save during combat')
    modifier = _save_modifier(target, ability)
    if context['autoFail']:
        return {
            'type': 'save',
            'delivered': True,
            'critical': False,
            'ability': ability,
            'autoFailed': True,
            'modeReasons': context['reasons'],
            'rolls': [],
            'naturalRoll': None,
            'modifier': modifier,
            'bonusDice': [],
            'total': None,
            'dc': int(delivery['dc']),
            'saveSucceeded': False,
            'onSuccess': delivery['onSuccess'],
            'consume': context['consume'],
        }
    natural_roll, rolls = _d20(context['mode'], roller)
    bonus_dice = _bonus_dice(context['bonusDice'], roller)
    total = natural_roll + modifier + context['flatBonus'] + sum(item['total'] for item in bonus_dice)
    inspiration = None if total >= int(delivery['dc']) else roll_inspiration(target, roller)
    if inspiration:
        total += inspiration['roll']
        context['consume'].append(inspiration.pop('consume'))
    success = total >= int(delivery['dc'])
    return {
        'type': 'save',
        'delivered': not success,
        'critical': False,
        'ability': ability,
        'mode': context['mode'],
        'modeReasons': context['reasons'],
        'rolls': rolls,
        'naturalRoll': natural_roll,
        'modifier': modifier,
        'bonusDice': bonus_dice,
        'total': total,
        'dc': int(delivery['dc']),
        'saveSucceeded': success,
        'onSuccess': delivery['onSuccess'],
        'consume': context['consume'],
        **({'inspiration': inspiration} if inspiration else {}),
    }


def _with_extra_mode(context: Mapping[str, Any], mode: str, reason: str) -> dict[str, Any]:
    """Add one advantage or disadvantage source to a finished modifier-engine context.

    A finished context keeps only its net mode, so an opposite net mode cancels
    to normal; a normal mode with recorded reasons already held both sources.
    """

    current = _key(context.get('mode')) or 'normal'
    opposite = 'disadvantage' if mode == 'advantage' else 'advantage'
    if current == opposite:
        net = 'normal'
    elif current == 'normal' and context.get('reasons'):
        net = 'normal'
    else:
        net = mode
    return {**context, 'mode': net, 'reasons': [*context.get('reasons', []), reason]}


def _save_effect_mode(effect: Mapping[str, Any], delivery: Mapping[str, Any]) -> str:
    explicit = _key(effect.get('onSuccessfulSave'))
    if explicit:
        return explicit
    if effect['kind'] == 'damage':
        return _key(delivery.get('onSuccess'))
    return 'none'


def _apply_damage(
    participant: dict[str, Any],
    *,
    rolled: dict[str, Any],
    damage_type: str,
    save_divisor: int,
    critical: bool,
    in_combat: bool,
) -> dict[str, Any]:
    hp_before = _hp(participant)
    after_save = int(rolled['total']) // max(1, save_divisor)
    multiplier, defense = damage_defense(participant, damage_type)
    final_damage = apply_damage_multiplier(after_save, multiplier)
    if vitality.is_player_character(participant):
        taken = vitality.take_damage(participant, final_damage, critical=critical, in_combat=in_combat)
        absorbed, hp_damage = taken['tempHpDamage'], taken['hpDamage']
        hp_after = _hp(participant)
    else:
        absorbed = min(hp_before['temp'], final_damage)
        hp_damage = min(hp_before['current'], final_damage - absorbed)
        hp_after = {
            'current': hp_before['current'] - hp_damage,
            'max': hp_before['max'],
            'temp': hp_before['temp'] - absorbed,
        }
        participant['hp'] = hp_after
        if hp_after['current'] <= 0:
            participant['isAlive'] = False
            participant['isConscious'] = False
    return {
        'kind': 'damage',
        'damageType': damage_type,
        'roll': rolled,
        'rolledAmount': int(rolled['total']),
        'afterSaveAmount': after_save,
        'defense': defense,
        'postDefenseAmount': final_damage,
        'amountApplied': absorbed + hp_damage,
        'overkillAmount': max(0, final_damage - absorbed - hp_damage),
        'tempHpDamage': absorbed,
        'hpDamage': hp_damage,
        'hpBefore': hp_before,
        'hpAfter': hp_after,
    }


def _apply_healing(participant: dict[str, Any], *, rolled: dict[str, Any]) -> dict[str, Any]:
    hp_before = _hp(participant)
    if vitality.is_player_character(participant):
        applied = vitality.heal(participant, int(rolled['total']))
        hp_after = _hp(participant)
    else:
        was_defeated = (
            hp_before['current'] <= 0
            or participant.get('isAlive', hp_before['current'] > 0) is False
        )
        missing = max(0, hp_before['max'] - hp_before['current'])
        applied = 0 if heals_nothing(participant) else min(missing, int(rolled['total']))
        hp_after = {**hp_before, 'current': hp_before['current'] + applied}
        participant['hp'] = hp_after
        # Ordinary healing must not wake an actor who is unconscious for another
        # reason. Reanimation occurs only after target legality explicitly allowed
        # a defeated target and the spell actually restored positive HP.
        if was_defeated and applied > 0:
            participant['isAlive'] = True
            participant['isConscious'] = True
    return {
        'kind': 'healing',
        'roll': rolled,
        'rolledAmount': int(rolled['total']),
        'amountApplied': applied,
        'hpBefore': hp_before,
        'hpAfter': hp_after,
    }


def _apply_temporary_hp(participant: dict[str, Any], *, rolled: dict[str, Any]) -> dict[str, Any]:
    hp_before = _hp(participant)
    requested = int(rolled['total'])
    hp_after = {**hp_before, 'temp': max(hp_before['temp'], requested)}
    participant['hp'] = hp_after
    return {
        'kind': 'temporary_hp',
        'roll': rolled,
        'rolledAmount': requested,
        'amountApplied': hp_after['temp'] - hp_before['temp'],
        'hpBefore': hp_before,
        'hpAfter': hp_after,
    }


def _apply_condition(
    participant: dict[str, Any],
    *,
    effect: Mapping[str, Any],
    cast_id: str,
    caster_id: str,
    spell: Mapping[str, Any],
    effect_index: int,
) -> dict[str, Any]:
    condition = _key(effect['condition'])
    operation = _key(effect['operation'])
    before = sorted(condition_keys(participant))
    conditions = set(before)
    effects = active_effects(participant)
    if operation == 'remove':
        conditions.discard(condition)
        participant['activeEffects'] = [
            active_effect
            for active_effect in effects
            if active_effect.get('condition') != condition
        ]
        _set_conditions(participant, sorted(conditions))
        return {
            'kind': 'condition',
            'operation': 'remove',
            'condition': condition,
            'applied': condition in before,
            'conditionsBefore': before,
            'conditionsAfter': sorted(conditions),
        }

    if condition in _condition_immunities(participant):
        return {
            'kind': 'condition',
            'operation': 'add',
            'condition': condition,
            'applied': False,
            'immune': True,
            'conditionsBefore': before,
            'conditionsAfter': before,
        }

    conditions.add(condition)
    active_effect = None
    if (
        effect.get('duration') or effect.get('until') or spell.get('concentration') is True
        or any(effect.get(flag) for flag in EFFECT_LIFECYCLE_FLAGS)
    ):
        existing_condition_effects = [
            existing for existing in effects if existing.get('condition') == condition
        ]
        preserve_on_expiry = condition in before and (
            not existing_condition_effects
            or any(
                existing.get('preserveConditionOnExpiry') is True
                for existing in existing_condition_effects
            )
        )
        active_effect = {
            **_spell_active_effect(participant, effect=effect, cast_id=cast_id, caster_id=caster_id,
                                   spell=spell, effect_index=effect_index, modifiers={}),
            'condition': condition,
            'preserveConditionOnExpiry': preserve_on_expiry,
        }
        participant['activeEffects'] = [
            *[existing for existing in effects if existing['id'] != active_effect['id']],
            active_effect,
        ]
    _set_conditions(participant, sorted(conditions))
    return {
        'kind': 'condition',
        'operation': 'add',
        'condition': condition,
        'applied': condition not in before,
        'immune': False,
        'activeEffect': deepcopy(active_effect),
        'conditionsBefore': before,
        'conditionsAfter': sorted(conditions),
    }


def _spell_active_effect(
    participant: Mapping[str, Any], *, effect: Mapping[str, Any], cast_id: str, caster_id: str,
    spell: Mapping[str, Any], effect_index: int, modifiers: Mapping[str, Any],
) -> dict[str, Any]:
    """The modifier engine's active-effect shape for one spell effect on one bearer."""

    duration = effect.get('duration')
    active_effect: dict[str, Any] = {
        'id': _effect_id(cast_id, _participant_id(participant), effect_index),
        'sourceId': spell['id'], 'sourceName': spell['name'], 'casterId': caster_id,
        'castId': cast_id, 'modifiers': deepcopy(dict(modifiers)),
        'duration': ({
            'rounds': duration['remaining'],
            'tickOn': 'caster_turn_end' if duration['tick'] == 'source_turn_end' else duration['tick'],
        } if duration else None),
        'concentration': spell.get('concentration') is True,
        'consumeOn': effect.get('consumeOn') or None,
        'spellLevel': _int(spell.get('castLevel'), default=_int(spell.get('level'), default=0)),
    }
    if effect.get('until'):
        active_effect['until'] = effect['until']
    for flag in EFFECT_LIFECYCLE_FLAGS:
        if effect.get(flag):
            active_effect[flag] = True
    return active_effect


def _refresh_armor_class(participant: dict[str, Any]) -> None:
    """Recompute a player participant's AC after an armour-setting effect (Mage Armor) changed."""

    breakdown = participant.get('armorClassBreakdown')
    if not isinstance(breakdown, Mapping):
        return
    armor_class = armor_class_with_spell_armor(breakdown, active_effects(participant))
    participant['armorClass'] = armor_class
    if isinstance(participant.get('stats'), dict):
        participant['stats']['armorClass'] = armor_class
        participant['stats']['armor_class'] = armor_class


def _apply_modifier(
    participant: dict[str, Any], *, effect: Mapping[str, Any], cast_id: str,
    caster_id: str, spell: Mapping[str, Any], effect_index: int, casting_modifier: int = 0,
    bound_target_id: str = '',
) -> dict[str, Any]:
    modifiers = deepcopy(effect['modifiers'])
    for key in effect.get('castingModifierKeys') or []:
        modifiers[key] = max(0, casting_modifier)
    if bound_target_id and isinstance(modifiers.get('bonusDamageVs'), dict):
        modifiers['bonusDamageVs']['targetId'] = bound_target_id
    active_effect = _spell_active_effect(
        participant, effect=effect, cast_id=cast_id, caster_id=caster_id, spell=spell,
        effect_index=effect_index, modifiers=modifiers,
    )
    participant['activeEffects'] = [
        *[existing for existing in active_effects(participant) if existing['id'] != active_effect['id']],
        active_effect,
    ]
    if 'acBase' in modifiers:
        _refresh_armor_class(participant)
    return {'kind': 'modifier', 'applied': True, 'activeEffect': deepcopy(active_effect)}


def _remove_spell_effects(participant: dict[str, Any], predicate: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    """Remove spell effects, undoing what they changed beyond the modifier engine."""

    removed = remove_active_effects(participant, predicate)
    bonus = sum(max(0, _int(effect.get('maxHpBonus'), default=0)) for effect in removed)
    if bonus:
        hp = _hp(participant)
        maximum = max(1, hp['max'] - bonus)
        participant['hp'] = {**hp, 'max': maximum, 'current': min(hp['current'], maximum)}
    if any('acBase' in effect['modifiers'] for effect in removed):
        _refresh_armor_class(participant)
    return removed


def _apply_effect(
    participant: dict[str, Any],
    *,
    combat: dict[str, Any],
    effect: Mapping[str, Any],
    delivery: Mapping[str, Any],
    delivery_result: Mapping[str, Any],
    roller: Roller,
    cast_id: str,
    caster_id: str,
    spell: Mapping[str, Any],
    effect_index: int,
    in_combat: bool,
    casting_modifier: int = 0,
    shared_roll: Mapping[str, Any] | None = None,
    sleep_pool: dict[str, Any] | None = None,
    recurring_dc: int | None = None,
    damage_type: str = '',
) -> dict[str, Any]:
    caster = _participant_by_id(combat, caster_id)
    assert caster is not None  # Proven by legality before any roll or mutation.
    if effect['kind'] == 'repeat_attack':
        # The spiritual weapon stays whether or not its first attack hit.
        active_effect = {
            **_spell_active_effect(caster, effect=effect, cast_id=cast_id, caster_id=caster_id,
                                   spell=spell, effect_index=effect_index, modifiers={}),
            'repeatAttack': {'castLevel': _int(spell.get('castLevel'), default=spell['level'])},
        }
        caster['activeEffects'] = [
            *[existing for existing in active_effects(caster) if existing['sourceId'] != spell['id'] or not existing.get('repeatAttack')],
            active_effect,
        ]
        return {'kind': 'repeat_attack', 'applied': True, 'activeEffect': deepcopy(active_effect)}
    save_succeeded = delivery_result.get('saveSucceeded') is True
    save_mode = _save_effect_mode(effect, delivery) if save_succeeded else 'full'
    if not delivery_result.get('delivered') and not (save_succeeded and save_mode in {'half', 'full'}):
        return {
            'kind': effect['kind'],
            'applied': False,
            'reason': 'successful_save' if save_succeeded else 'spell_attack_missed',
        }
    if save_succeeded and save_mode == 'none':
        return {'kind': effect['kind'], 'applied': False, 'reason': 'successful_save'}

    if effect['kind'] == 'condition':
        return _apply_condition(
            participant,
            effect=effect,
            cast_id=cast_id,
            caster_id=caster_id,
            spell=spell,
            effect_index=effect_index,
        )
    if effect['kind'] == 'modifier':
        bearer = caster if effect.get('onCaster') else participant
        return _apply_modifier(
            bearer, effect=effect, cast_id=cast_id, caster_id=caster_id,
            spell=spell, effect_index=effect_index, casting_modifier=casting_modifier,
            bound_target_id=_participant_id(participant) if effect.get('onCaster') else '',
        )
    if effect['kind'] == 'recurring':
        recurring = effect['recurring']
        bearer = caster if recurring['aura'] else participant
        active_effect = {
            **_spell_active_effect(bearer, effect=effect, cast_id=cast_id, caster_id=caster_id,
                                   spell=spell, effect_index=effect_index, modifiers={}),
            'recurring': {
                'ability': recurring['ability'],
                'dc': recurring_dc,
                'repeatSave': recurring['repeatSave'],
                **({'damage': {'dice': effect['expression'], 'type': effect['damageType']},
                    'onSuccess': recurring['onSuccess']} if recurring['hasDamage'] else {}),
                **({'aura': deepcopy(recurring['aura'])} if recurring['aura'] else {}),
            },
        }
        bearer['activeEffects'] = [
            *[existing for existing in active_effects(bearer) if existing['id'] != active_effect['id']],
            active_effect,
        ]
        return {'kind': 'recurring', 'applied': True, 'activeEffect': deepcopy(active_effect)}
    if effect['kind'] == 'remove_condition':
        present = [value for value in effect['conditions'] if value in condition_keys(participant)]
        if not present:
            return {'kind': 'remove_condition', 'applied': False, 'reason': 'no_matching_condition'}
        before = sorted(condition_keys(participant))
        removed = present if effect.get('removeAll') else present[:1]
        for condition in removed:
            _apply_condition(
                participant, effect={'condition': condition, 'operation': 'remove'},
                cast_id=cast_id, caster_id=caster_id, spell=spell, effect_index=effect_index,
            )
            if condition == 'hidden' and isinstance(participant.get('position'), dict):
                participant['position']['isHidden'] = False
        return {'kind': 'remove_condition', 'operation': 'remove', 'applied': True, 'condition': removed[0],
                'removed': removed, 'conditionsBefore': before, 'conditionsAfter': sorted(condition_keys(participant))}
    if effect['kind'] == 'revive':
        before = sorted(condition_keys(participant))
        vitality.revive(participant, effect['hp'])
        return {'kind': 'revive', 'applied': True, 'hpAfter': _hp(participant),
                'conditionsBefore': before, 'conditionsAfter': sorted(condition_keys(participant))}
    if effect['kind'] == 'dispel':
        threshold = _int(spell.get('castLevel'), default=spell['level'])
        removed = _remove_spell_effects(
            participant,
            lambda active: bool(active.get('castId')) and _int(active.get('spellLevel'), default=0) <= threshold,
        )
        return {'kind': 'dispel', 'applied': bool(removed), 'removedEffects': [
            {'id': active['id'], 'sourceName': active['sourceName'], 'spellLevel': active.get('spellLevel')}
            for active in removed
        ]}
    if effect['kind'] == 'sleep_pool':
        assert sleep_pool is not None  # The resolver opens one pool per cast.
        if 'roll' not in sleep_pool:
            sleep_pool['roll'] = _roll_expression(str(effect['expression']), roller)
            sleep_pool['remaining'] = int(sleep_pool['roll']['total'])
        hp_now = _hp(participant)['current']
        if hp_now > sleep_pool['remaining']:
            return {'kind': 'sleep_pool', 'applied': False, 'reason': 'pool_exhausted',
                    'poolRoll': deepcopy(sleep_pool['roll']), 'poolRemaining': sleep_pool['remaining']}
        result = _apply_condition(
            participant, effect={**effect, 'condition': 'unconscious', 'operation': 'add', 'endsOnDamage': True},
            cast_id=cast_id, caster_id=caster_id, spell=spell, effect_index=effect_index,
        )
        if not result.get('immune'):
            sleep_pool['remaining'] -= hp_now
        return {**result, 'kind': 'sleep_pool', 'poolRoll': deepcopy(sleep_pool['roll']),
                'poolRemaining': sleep_pool['remaining']}
    if effect['kind'] == 'max_hp_bonus':
        rolled = _roll_expression(str(effect['expression']), roller)
        hp_before = _hp(participant)
        # A second casting replaces the first rather than stacking.
        _remove_spell_effects(participant, lambda active: active['sourceId'] == spell['id'] and bool(active.get('maxHpBonus')))
        hp = _hp(participant)
        amount = int(rolled['total'])
        participant['hp'] = {**hp, 'max': hp['max'] + amount}
        if vitality.is_player_character(participant):
            vitality.heal(participant, amount)
        else:
            participant['hp']['current'] = hp['current'] + amount
        active_effect = {
            **_spell_active_effect(participant, effect=effect, cast_id=cast_id, caster_id=caster_id,
                                   spell=spell, effect_index=effect_index, modifiers={}),
            'maxHpBonus': amount,
        }
        participant['activeEffects'] = [*active_effects(participant), active_effect]
        return {'kind': 'max_hp_bonus', 'applied': True, 'roll': rolled, 'amountApplied': amount,
                'hpBefore': hp_before, 'hpAfter': _hp(participant), 'activeEffect': deepcopy(active_effect)}
    if effect['kind'] == 'stabilize':
        before = sorted(condition_keys(participant))
        applied = vitality.stabilize(participant)
        return {'kind': 'stabilize', 'applied': applied, 'conditionsBefore': before,
                'conditionsAfter': sorted(condition_keys(participant))}
    if effect['kind'] == 'move':
        mover_id = caster_id if effect.get('teleport') == 'away' else _participant_id(participant)
        mover = _participant_by_id(combat, mover_id)
        assert mover is not None
        before = _key(_position(mover).get('rangeBand') or 'near')
        destination = ('far' if effect.get('teleport') == 'away' else
                       RANGE_BANDS[max(0, min(len(RANGE_BANDS) - 1, band_index(before) + effect['bands']))])
        move_participant(combat, mover_id, destination)
        return {'kind': 'move', 'applied': before != destination, 'targetId': mover_id,
                'fromRangeBand': before, 'toRangeBand': destination,
                'teleport': effect.get('teleport') or None}
    hp = _hp(participant)
    expression = (effect.get('woundedExpression') if hp['current'] < hp['max'] else None) or effect['expression']
    rolled = deepcopy(dict(shared_roll)) if shared_roll is not None else _roll_expression(
        str(expression), roller,
        critical=delivery_result.get('critical') is True and effect['kind'] == 'damage',
    )
    if effect.get('addCastingModifier') is True and shared_roll is None:
        bonus = max(0, casting_modifier)
        rolled['castingModifier'] = bonus
        rolled['bonus'] += bonus
        rolled['total'] += bonus
    if effect['kind'] == 'damage':
        return _apply_damage(
            participant,
            rolled=rolled,
            damage_type=damage_type if damage_type in effect['damageTypeChoices'] else str(effect['damageType']),
            save_divisor=2 if save_succeeded and save_mode == 'half' else 1,
            critical=delivery_result.get('critical') is True,
            in_combat=in_combat,
        )
    if effect['kind'] == 'healing':
        return _apply_healing(participant, rolled=rolled)
    return _apply_temporary_hp(participant, rolled=rolled)


def _area_targets(
    combat: Mapping[str, Any], caster_id: str, spell: Mapping[str, Any], primary_id: str, maximum: int, cast_level: int,
) -> list[str]:
    """Expand an area around the primary target using only legal enemies."""

    caster = _participant_by_id(combat, caster_id)
    primary = _participant_by_id(combat, primary_id)
    if caster is None or primary is None:
        return [primary_id]
    primary_position = _position(primary)
    primary_band = _key(primary_position.get('rangeBand') or primary_position.get('range_band') or 'near')
    primary_zone = _text(primary_position.get('zoneId') or primary_position.get('zone_id'))
    candidates = []
    for candidate in _participants(combat):
        candidate_id = _participant_id(candidate)
        if not candidate_id or candidate_id == primary_id or _friendly(caster, candidate) or _key(candidate.get('team')) == 'neutral':
            continue
        position = _position(candidate)
        band = _key(position.get('rangeBand') or position.get('range_band') or 'near')
        zone = _text(position.get('zoneId') or position.get('zone_id'))
        if band != primary_band or zone != primary_zone:
            continue
        if spell_target_legality(combat, caster_id=caster_id, spell=spell, target_ids=[candidate_id], cast_level=cast_level)['legal']:
            candidates.append(candidate_id)
    return [primary_id, *sorted(candidates)[:max(0, maximum - 1)]]


def active_repeat_attack(combat: Mapping[str, Any], caster_id: str, spell_id: str) -> dict[str, Any] | None:
    """The caster's still-active effect that lets it repeat this spell's attack (Spiritual Weapon)."""

    caster = _participant_by_id(combat, caster_id)
    return next(
        (
            effect for effect in active_effects(caster)
            if effect['sourceId'] == _text(spell_id) and effect['casterId'] == _text(caster_id)
            and isinstance(effect.get('repeatAttack'), Mapping)
        ),
        None,
    )


def _spell_attack_bonus_damage(
    caster: Mapping[str, Any], target: Mapping[str, Any], *, spell_id: str, delivery: Mapping[str, Any], combat: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Damage the caster adds to a spell attack that hit: marks on the target (Hex, never
    weapon-only marks) and class features (Agonizing Blast on each Eldritch Blast beam)."""

    attack = {'mode': delivery.get('mode'), 'critical': delivery.get('critical') is True, 'combat': combat}
    return [
        {
            'expression': str(bonus['dice']) if bonus.get('dice') else str(_int(bonus.get('flat'))),
            'type': _key(bonus.get('type')),
            'source': _text(bonus.get('source')),
        }
        for bonus in extra_damage(caster, target, kind='spell_attack', attack=attack, spell_id=spell_id)
    ]


def _recurring_save(
    combat: dict[str, Any], target: Mapping[str, Any], ability: str, dc: Any, roller: Roller, *, mode: str = '',
) -> dict[str, Any]:
    context = save_context(target, ability, combat=combat)
    if mode:
        context = _with_extra_mode(context, mode, 'The spell grants this save advantage')
    modifier = _save_modifier(target, ability)
    record: dict[str, Any] = {'ability': ability, 'dc': _int(dc, default=10), 'modifier': modifier, 'modeReasons': context['reasons']}
    if context['autoFail']:
        return {**record, 'autoFailed': True, 'rolls': [], 'naturalRoll': None, 'bonusDice': [], 'total': None, 'succeeded': False}
    natural_roll, rolls = _d20(context['mode'], roller)
    bonus_dice = _bonus_dice(context['bonusDice'], roller)
    total = natural_roll + modifier + context['flatBonus'] + sum(item['total'] for item in bonus_dice)
    consume = list(context['consume'])
    inspiration = None if total >= record['dc'] else roll_inspiration(target, roller)
    if inspiration:
        total += inspiration['roll']
        consume.append(inspiration.pop('consume'))
    consume_modifiers(combat, consume)
    return {
        **record, 'mode': context['mode'], 'rolls': rolls, 'naturalRoll': natural_roll,
        'bonusDice': bonus_dice, 'total': total, 'succeeded': total >= record['dc'],
        **({'inspiration': inspiration} if inspiration else {}),
    }


def _end_cast_on(participant: dict[str, Any], cast_id: str) -> list[str]:
    return [effect['id'] for effect in _remove_spell_effects(participant, lambda effect: effect.get('castId') == cast_id)]


def _after_damage(combat: dict[str, Any], target: dict[str, Any], *, source_id: str, roller: Roller) -> list[dict[str, Any]]:
    """Effects that end, or allow another save, when their bearer takes damage.

    Besides the spell lifecycle flags this applies every effect's damage
    ``endsWhen`` (Turn Undead's turning, Rage when its bearer drops).
    """

    events: list[dict[str, Any]] = []
    source = _participant_by_id(combat, source_id)

    def harmed_by_casters_side(effect: Mapping[str, Any]) -> bool:
        caster = _participant_by_id(combat, effect.get('casterId'))
        if source is None:
            return False
        return _text(effect.get('casterId')) == source_id or (caster is not None and _friendly(caster, source))

    ended = [
        *_remove_spell_effects(
            target,
            lambda effect: effect.get('endsOnDamage') is True or (effect.get('endsOnHarm') is True and harmed_by_casters_side(effect)),
        ),
        *end_effects_on_damage(target),
    ]
    if ended:
        events.append({'kind': 'effects_ended', 'reason': 'took_damage', 'effectIds': [effect['id'] for effect in ended],
                       'sources': sorted({effect['sourceName'] for effect in ended})})
    for effect in active_effects(target):
        recurring = effect.get('recurring')
        if effect.get('repeatSaveOnDamage') is not True or not isinstance(recurring, Mapping):
            continue
        if effect['id'] not in {current['id'] for current in active_effects(target)}:
            continue
        save = _recurring_save(combat, target, _ability_key(recurring.get('ability')), recurring.get('dc'), roller, mode='advantage')
        event = {'kind': 'repeat_save', 'reason': 'took_damage', 'effectId': effect['id'], 'sourceName': effect['sourceName'], 'save': save}
        if save['succeeded']:
            event['endedEffectIds'] = _end_cast_on(target, _text(effect.get('castId')))
        events.append(event)
    return events


def resolve_targeted_spell(
    combat: Mapping[str, Any],
    *,
    caster_id: str,
    spell: Mapping[str, Any],
    target_ids: Iterable[str],
    cast_id: str,
    roller: Roller,
    caster_resources: Mapping[str, Any] | None = None,
    target_resources: Mapping[str, Mapping[str, Any]] | None = None,
    current_turn: int | None = None,
    cast_level: int | None = None,
    damage_type: str | None = None,
    repeat_attack: bool = False,
) -> dict[str, Any]:
    """Resolve one exact-ID spell cast atomically and return JSON-safe state.

    The returned ``combat`` is a deep copy.  Invalid casts and cast-id conflicts
    do not roll or mutate it.  ``spellResolutionLedger`` makes retries against
    the returned combat idempotent; callers should still keep the repository's
    broader state-change ledger as the durable cross-system guard.
    ``damage_type`` picks among a damage effect's ``damageTypeChoices``;
    ``repeat_attack`` makes the active spiritual weapon attack again.  The
    caster's readied Metamagic shapes and is spent by the cast (``metamagic``
    in the resolution): Quickened makes an action spell a bonus action, Twinned
    lets a single-target spell take a second target.
    """

    if not callable(roller):
        raise TypeError('roller must be a callable accepting the die size.')
    normalized_cast_id = _text(cast_id)
    if not normalized_cast_id:
        return {
            'ok': False,
            'duplicate': False,
            'code': 'spell_cast_id_missing',
            'reason': 'Authoritative spell resolution requires a stable cast id.',
            'combat': deepcopy(dict(combat)),
            'casterResources': deepcopy(dict(caster_resources or {})),
            'resolution': None,
        }

    normalized_target_ids = [_text(value) for value in target_ids if _text(value)]
    requested_spell_id = _spell_id(spell)
    spell_level = _bounded_int(spell.get('level'), default=0, minimum=0, maximum=9)
    selected_level = spell_level if cast_level is None else _int(cast_level, default=-1)
    if selected_level < spell_level or selected_level > 9 or (spell_level == 0 and selected_level != 0):
        return {
            'ok': False, 'duplicate': False, 'code': 'invalid_cast_level',
            'reason': 'The selected cast level is not valid for this spell.',
            'combat': deepcopy(dict(combat)), 'casterResources': deepcopy(dict(caster_resources or {})),
            'resolution': None,
        }
    for prior in _ledger(combat):
        if _text(prior.get('castId')) != normalized_cast_id:
            continue
        same_request = (
            _text(prior.get('casterId')) == _text(caster_id)
            and _text(prior.get('spellId')) == requested_spell_id
            and list(prior.get('targetIds') or []) == normalized_target_ids
            and _int(prior.get('castLevel'), default=spell_level) == selected_level
        )
        if not same_request:
            return {
                'ok': False,
                'duplicate': False,
                'code': 'spell_cast_id_conflict',
                'reason': 'This cast id is already bound to a different spell resolution.',
                'combat': deepcopy(dict(combat)),
                'casterResources': deepcopy(dict(caster_resources or {})),
                'resolution': None,
            }
        return {
            'ok': True,
            'duplicate': True,
            'code': None,
            'reason': '',
            'combat': deepcopy(dict(combat)),
            'casterResources': deepcopy(dict(caster_resources or {})),
            'resolution': deepcopy(prior.get('resolution')),
        }

    legality = spell_target_legality(
        combat,
        caster_id=caster_id,
        spell=spell,
        target_ids=normalized_target_ids,
        cast_level=selected_level,
    )
    if not legality['legal']:
        return {
            'ok': False,
            'duplicate': False,
            'code': legality['code'],
            'reason': legality['reason'],
            'combat': deepcopy(dict(combat)),
            'casterResources': deepcopy(dict(caster_resources or {})),
            'resolution': None,
            'legality': legality,
        }

    normalized_spell = legality['spell']
    normalized_spell['castLevel'] = selected_level
    if repeat_attack:
        if active_repeat_attack(combat, _text(caster_id), normalized_spell['id']) is None:
            return {
                'ok': False, 'duplicate': False, 'code': 'spell_repeat_unavailable',
                'reason': f"{normalized_spell['name']} is not active for this caster.",
                'combat': deepcopy(dict(combat)), 'casterResources': deepcopy(dict(caster_resources or {})),
                'resolution': None,
            }
        normalized_spell['effects'] = [effect for effect in normalized_spell['effects'] if effect['kind'] != 'repeat_attack']
        normalized_spell['concentration'] = False
    requested_target_ids = list(normalized_target_ids)
    if normalized_spell['target']['areaMaxTargets']:
        normalized_target_ids = _area_targets(
            combat, _text(caster_id), spell, normalized_target_ids[0], normalized_spell['target']['areaMaxTargets'], selected_level,
        )
    if any(effect['kind'] == 'sleep_pool' for effect in normalized_spell['effects']):
        hit_points = {
            target_id: _hp(_participant_by_id(combat, target_id) or {})['current'] for target_id in normalized_target_ids
        }
        normalized_target_ids = sorted(normalized_target_ids, key=lambda target_id: (hit_points[target_id], target_id))
    next_combat = deepcopy(dict(combat))
    caster = _participant_by_id(next_combat, caster_id)
    assert caster is not None  # Proven by legality before any roll or mutation.
    casting_modifier = _ability_modifier(caster, str(normalized_spell['delivery'].get('castingAbility') or '')) or 0
    roll_sequence: list[int] = []

    def logged_roller(sides: int) -> int:
        value = roller(sides)
        roll_sequence.append(_int(value, default=1))
        return value

    ended_on_cast = [
        effect['id']
        for effect in _remove_spell_effects(caster, lambda effect: effect.get('endsOnCast') is True)
    ]
    # Readied Metamagic is spent by the spell it shapes: Twinned by a second
    # target, Quickened by the next spell that would cost an action.
    metamagic = []
    if normalized_spell.get('twinnable') and len(normalized_target_ids) == 2:
        metamagic.append('twinned')
    if normalized_spell['castingTime'] == 'action' and _metamagic_marker(caster, 'quickened') is not None:
        metamagic.append('quickened')
        normalized_spell['castingTime'] = 'bonus_action'
    if metamagic:
        remove_active_effects(caster, lambda effect: effect.get('metamagic') in metamagic)
    recurring_dc = normalized_spell['delivery']['dc']
    if recurring_dc is None and any(effect['kind'] == 'recurring' for effect in normalized_spell['effects']):
        recurring_dc = _spell_save_dc(caster, normalized_spell['delivery']['castingAbility'])
    sleep_pool: dict[str, Any] = {}

    next_resources = deepcopy(dict(caster_resources or {}))
    replaced_concentration = None
    removed_concentration_effects: list[dict[str, Any]] = []
    if normalized_spell['concentration']:
        next_resources, replaced_concentration, removed_concentration_effects = _start_concentration(
            next_combat,
            next_resources,
            caster_id=_text(caster_id),
            spell=normalized_spell,
            target_ids=normalized_target_ids,
            current_turn=current_turn,
        )

    in_combat = _key(next_combat.get('status')) in {'starting', 'active'}
    updated_target_resources: dict[str, dict[str, Any]] = {}
    shared_damage_rolls: dict[int, dict[str, Any]] = {}
    target_resolutions: list[dict[str, Any]] = []
    darts = normalized_spell['delivery'].get('darts')
    dart_count = darts['count'] if isinstance(darts, dict) else 0
    attack_count = normalized_spell['delivery'].get('attacks') or 0
    repetitions = dart_count or attack_count
    scheduled_targets = [normalized_target_ids[index % len(normalized_target_ids)] for index in range(repetitions)] if repetitions else normalized_target_ids
    chosen_damage_type = _key(damage_type)
    spell_damage_type = next(
        (
            chosen_damage_type if chosen_damage_type in effect['damageTypeChoices'] else effect['damageType']
            for effect in normalized_spell['effects'] if effect['kind'] == 'damage'
        ),
        '',
    )

    def check_concentration(effect_result: dict[str, Any], target_id: str, damage: int) -> None:
        nonlocal next_combat, next_resources, caster
        target = _participant_by_id(next_combat, target_id)
        assert target is not None
        resources_for_target = (
            next_resources if target_id == _text(caster_id)
            else updated_target_resources.get(target_id)
            or (target_resources or {}).get(target_id)
            or (target.get('spellResources') if isinstance(target.get('spellResources'), Mapping) else {})
        )
        if damage <= 0 or not isinstance(resources_for_target.get('concentration'), Mapping):
            return
        concentration = resolve_concentration_check(
            next_combat, resources_for_target, caster_id=target_id, damage=damage, roller=logged_roller,
        )
        if not concentration.get('ok'):
            return
        effect_result['concentrationCheck'] = {
            'required': concentration.get('required'),
            'maintained': concentration.get('maintained'),
            'reason': concentration.get('reason'),
            'check': deepcopy(concentration.get('check')),
            'removedEffects': deepcopy(concentration.get('removedEffects') or []),
        }
        next_combat = concentration['combat']
        updated_target_resources[target_id] = deepcopy(concentration['casterResources'])
        if target_id == _text(caster_id):
            next_resources = deepcopy(concentration['casterResources'])
        target = _participant_by_id(next_combat, target_id)
        caster = _participant_by_id(next_combat, caster_id)
        assert target is not None and caster is not None
        if isinstance(target.get('spellResources'), Mapping):
            target['spellResources'] = deepcopy(concentration['casterResources'])

    for delivery_index, target_id in enumerate(scheduled_targets):
        target = _participant_by_id(next_combat, target_id)
        assert target is not None  # Proven by legality before any roll or mutation.
        delivery_result = _resolve_delivery(
            next_combat,
            caster,
            target,
            normalized_spell['delivery'],
            normalized_spell['target'],
            logged_roller,
        )
        consumed = consume_modifiers(next_combat, delivery_result.pop('consume', []))
        if consumed:
            delivery_result['consumedEffects'] = [
                _text(item.get('id') or item.get('condition')) for item in consumed
            ]
        effect_results = []
        damage_taken = 0
        for index, effect in enumerate(normalized_spell['effects']):
            target = _participant_by_id(next_combat, target_id)
            assert target is not None
            shared_roll = shared_damage_rolls.get(index) if normalized_spell['target']['areaMaxTargets'] and effect['kind'] == 'damage' else None
            effect_result = _apply_effect(
                target,
                combat=next_combat,
                effect=effect,
                delivery=normalized_spell['delivery'],
                delivery_result=delivery_result,
                roller=logged_roller,
                cast_id=normalized_cast_id,
                caster_id=_text(caster_id),
                spell=normalized_spell,
                effect_index=index,
                in_combat=in_combat,
                casting_modifier=casting_modifier,
                shared_roll=shared_roll,
                sleep_pool=sleep_pool,
                recurring_dc=recurring_dc,
                damage_type=chosen_damage_type,
            )
            if normalized_spell['target']['areaMaxTargets'] and effect['kind'] == 'damage' and isinstance(effect_result.get('roll'), dict):
                effect_result['sharedRollReused'] = shared_roll is not None
                if shared_roll is None:
                    shared_damage_rolls[index] = deepcopy(effect_result['roll'])
            damage = _int(effect_result.get('amountApplied'), default=0) if effect['kind'] == 'damage' else 0
            damage_taken += damage
            check_concentration(effect_result, target_id, damage)
            effect_results.append(effect_result)
        if delivery_result.get('type') == 'attack' and delivery_result.get('delivered'):
            target = _participant_by_id(next_combat, target_id)
            assert target is not None
            bonuses = _spell_attack_bonus_damage(
                caster, target, spell_id=normalized_spell['id'], delivery=delivery_result, combat=next_combat,
            )
            for bonus in bonuses:
                target = _participant_by_id(next_combat, target_id)
                assert target is not None
                bonus_result = _apply_damage(
                    target,
                    rolled=_roll_expression(bonus['expression'], logged_roller, critical=delivery_result.get('critical') is True),
                    damage_type=bonus['type'] or spell_damage_type or 'force',
                    save_divisor=1,
                    critical=delivery_result.get('critical') is True,
                    in_combat=in_combat,
                )
                bonus_result['source'] = bonus['source']
                damage_taken += int(bonus_result['amountApplied'])
                check_concentration(bonus_result, target_id, int(bonus_result['amountApplied']))
                effect_results.append(bonus_result)
        target = _participant_by_id(next_combat, target_id)
        assert target is not None
        after_damage = (
            _after_damage(next_combat, target, source_id=_text(caster_id), roller=logged_roller)
            if damage_taken > 0 else []
        )
        target_resolutions.append(
            {
                'targetId': target_id,
                'targetName': _text(target.get('name')) or target_id,
                **({'dart': delivery_index + 1, 'dartsTotal': dart_count} if dart_count else {}),
                **({'attackIndex': delivery_index + 1, 'attacksTotal': attack_count} if attack_count else {}),
                'delivery': delivery_result,
                'effects': effect_results,
                **({'afterDamage': after_damage} if after_damage else {}),
            }
        )

    resolution = {
        'castId': normalized_cast_id,
        'casterId': _text(caster_id),
        'spellId': normalized_spell['id'],
        'spellName': normalized_spell['name'],
        'castLevel': selected_level,
        'castingTime': normalized_spell['castingTime'],
        'targetIds': normalized_target_ids,
        **({'requestedTargetIds': requested_target_ids} if normalized_spell['target']['areaMaxTargets'] else {}),
        **({'damageType': spell_damage_type} if any(effect['damageTypeChoices'] for effect in normalized_spell['effects']) else {}),
        **({'repeatAttack': True} if repeat_attack else {}),
        **({'endedOnCast': ended_on_cast} if ended_on_cast else {}),
        **({'metamagic': metamagic} if metamagic else {}),
        'targets': target_resolutions,
        'concentration': {
            'started': normalized_spell['concentration'],
            'replaced': replaced_concentration,
            'removedEffects': removed_concentration_effects,
        },
        'rollSequence': roll_sequence,
        'authoritative': True,
        'instruction': (
            'Narrate these spell targets, rolls, saves, effects, and resulting state exactly; '
            'do not add damage, healing, conditions, or targets.'
        ),
    }
    flags = next_combat.get('flags') if isinstance(next_combat.get('flags'), dict) else {}
    next_combat['flags'] = flags
    ledger = _ledger(next_combat)
    ledger.append(
        {
            'castId': normalized_cast_id,
            'casterId': _text(caster_id),
            'spellId': normalized_spell['id'],
            'castLevel': selected_level,
            'targetIds': requested_target_ids,
            'resolution': deepcopy(resolution),
        }
    )
    flags['spellResolutionLedger'] = ledger[-SPELL_RESOLUTION_LEDGER_LIMIT:]
    return {
        'ok': True,
        'duplicate': False,
        'code': None,
        'reason': '',
        'combat': next_combat,
        'casterResources': next_resources,
        'targetResources': updated_target_resources,
        'resolution': resolution,
        'legality': legality,
    }


def advance_spell_effect_durations(
    combat: Mapping[str, Any],
    *,
    timing: str,
    actor_id: str,
    steps: int = 1,
) -> dict[str, Any]:
    """Advance duration counters at one explicit turn boundary.

    ``target_turn_end`` decrements effects on ``actor_id``; ``source_turn_end``
    (``caster_turn_end``) and ``caster_turn_start`` decrement effects cast by
    ``actor_id``.  Expiration removes a condition only when no other active
    effect still maintains it.
    """

    normalized_timing = _key(timing)
    if normalized_timing not in SUPPORTED_DURATION_TICKS:
        return {
            'ok': False,
            'code': 'spell_duration_timing_invalid',
            'reason': 'Duration timing must be a supported target or caster turn boundary.',
            'combat': deepcopy(dict(combat)),
            'expiredEffects': [],
        }
    normalized_actor_id = _text(actor_id)
    if not normalized_actor_id:
        return {
            'ok': False,
            'code': 'spell_duration_actor_missing',
            'reason': 'Duration advancement requires an exact actor id.',
            'combat': deepcopy(dict(combat)),
            'expiredEffects': [],
        }
    next_combat = deepcopy(dict(combat))
    expired = tick_effects(
        next_combat,
        actor_id=normalized_actor_id,
        tick_on=normalized_timing,
        steps=_int(steps, default=1),
    )
    return {
        'ok': True,
        'code': None,
        'reason': '',
        'combat': next_combat,
        'expiredEffects': expired,
    }


def resolve_concentration_check(
    combat: Mapping[str, Any],
    caster_resources: Mapping[str, Any] | None,
    *,
    caster_id: str,
    damage: int,
    roller: Roller,
) -> dict[str, Any]:
    """Resolve the Constitution save caused by one authoritative damage event.

    The DC is ``max(10, floor(damage / 2))``.  Incapacitation ends
    concentration automatically.  On failure, only active effects belonging to
    the exact caster/spell pair are removed.
    """

    if not callable(roller):
        raise TypeError('roller must be a callable accepting the die size.')
    next_combat = deepcopy(dict(combat))
    next_resources = deepcopy(dict(caster_resources or {}))
    concentration = normalize_concentration(next_resources.get('concentration'))
    if concentration is None or max(0, _int(damage)) == 0:
        return {
            'ok': True,
            'required': False,
            'maintained': concentration is not None,
            'reason': 'no_active_concentration' if concentration is None else 'no_damage',
            'combat': next_combat,
            'casterResources': next_resources,
            'check': None,
            'removedEffects': [],
        }

    caster = _participant_by_id(next_combat, caster_id)
    if caster is None:
        return {
            'ok': False,
            'required': False,
            'maintained': False,
            'reason': 'concentration_caster_missing',
            'combat': deepcopy(dict(combat)),
            'casterResources': deepcopy(dict(caster_resources or {})),
            'check': None,
            'removedEffects': [],
        }
    concentration_caster = _text(concentration.get('casterActorId'))
    if concentration_caster and concentration_caster != _text(caster_id):
        return {
            'ok': False,
            'required': False,
            'maintained': False,
            'reason': 'concentration_caster_mismatch',
            'combat': deepcopy(dict(combat)),
            'casterResources': deepcopy(dict(caster_resources or {})),
            'check': None,
            'removedEffects': [],
        }

    hp = _hp(caster)
    incapacitated = (
        caster.get('isAlive', hp['current'] > 0) is False
        or caster.get('isConscious', hp['current'] > 0) is False
        or hp['current'] <= 0
        or bool(condition_keys(caster).intersection(INCAPACITATING_CONDITIONS | {'dead'}))
    )
    dc = max(10, max(0, _int(damage)) // 2)
    natural_roll = None
    rolls: list[int] = []
    bonus_dice: list[dict[str, Any]] = []
    modifier = _save_modifier(caster, 'constitution')
    total = None
    maintained = False
    failure_reason = 'caster_incapacitated'
    if not incapacitated:
        context = save_context(caster, 'constitution', combat=next_combat)
        natural_roll, rolls = _d20(context['mode'], roller)
        bonus_dice = _bonus_dice(context['bonusDice'], roller)
        total = natural_roll + modifier + context['flatBonus'] + sum(item['total'] for item in bonus_dice)
        maintained = total >= dc
        failure_reason = '' if maintained else 'saving_throw_failed'
        consume_modifiers(next_combat, context['consume'])

    removed: list[dict[str, Any]] = []
    if not maintained:
        removed = end_concentration_effects(
            next_combat,
            caster_id=_text(caster_id),
            source_id=_text(concentration.get('spellId')),
        )
        next_resources['concentration'] = None
        next_resources['revision'] = max(0, _int(next_resources.get('revision'))) + 1
    return {
        'ok': True,
        'required': not incapacitated,
        'maintained': maintained,
        'reason': failure_reason,
        'combat': next_combat,
        'casterResources': next_resources,
        'check': {
            'ability': 'constitution',
            'damage': max(0, _int(damage)),
            'dc': dc,
            'naturalRoll': natural_roll,
            'rolls': rolls,
            'modifier': modifier,
            'bonusDice': bonus_dice,
            'total': total,
            'succeeded': maintained,
            'automaticFailure': incapacitated,
        },
        'removedEffects': removed,
    }


def _replace_in_place(combat: dict[str, Any], replacement: Mapping[str, Any]) -> None:
    combat.clear()
    combat.update(replacement)


def _recurring_damage(
    combat: dict[str, Any], target_id: str, effect: Mapping[str, Any], roller: Roller,
) -> dict[str, Any]:
    """One start-of-turn saving throw against a recurring effect's damage, with its consequences."""

    target = _participant_by_id(combat, target_id)
    assert target is not None
    recurring = effect['recurring']
    damage = recurring.get('damage') if isinstance(recurring.get('damage'), Mapping) else {}
    save = _recurring_save(combat, target, _ability_key(recurring.get('ability')), recurring.get('dc'), roller)
    result: dict[str, Any] = {'save': save}
    if save['succeeded'] and _key(recurring.get('onSuccess')) != 'half':
        return result
    applied = _apply_damage(
        target,
        rolled=_roll_expression(str(damage.get('dice')), roller),
        damage_type=_key(damage.get('type')),
        save_divisor=2 if save['succeeded'] else 1,
        critical=False,
        in_combat=True,
    )
    result['damage'] = applied
    amount = int(applied['amountApplied'])
    resources = target.get('spellResources') if isinstance(target.get('spellResources'), Mapping) else {}
    if amount > 0 and isinstance(resources.get('concentration'), Mapping):
        concentration = resolve_concentration_check(combat, resources, caster_id=target_id, damage=amount, roller=roller)
        if concentration.get('ok'):
            _replace_in_place(combat, concentration['combat'])
            target = _participant_by_id(combat, target_id)
            assert target is not None
            target['spellResources'] = deepcopy(concentration['casterResources'])
            result['concentrationCheck'] = deepcopy(concentration.get('check'))
    if amount > 0:
        target = _participant_by_id(combat, target_id)
        assert target is not None
        after = _after_damage(combat, target, source_id=_text(effect.get('casterId')), roller=roller)
        if after:
            result['afterDamage'] = after
    return result


def _aura_reaches(bearer: Mapping[str, Any], actor: Mapping[str, Any], aura: Mapping[str, Any]) -> bool:
    relation = _key(aura.get('relation'))
    hostile = not _friendly(bearer, actor) and _key(actor.get('team')) != 'neutral'
    if (relation == 'enemy' and not hostile) or (relation == 'ally' and hostile):
        return False
    return participant_range_band(actor) in {_key(band) for band in aura.get('rangeBands') or []}


def resolve_turn_start_spell_effects(
    combat: dict[str, Any],
    *,
    actor_id: str,
    turn_id: Any,
    round_number: int,
    roller: Roller | None = None,
) -> list[dict[str, Any]]:
    """Run the recurring spell effects that fire as ``actor_id`` starts its turn, mutating ``combat``.

    The actor repeats the save against each of its repeat-save effects (a
    success ends every effect of that cast on it), saves against recurring
    damage it bears (Moonbeam), and saves against each hostile aura whose range
    bands it stands in (Spirit Guardians).  Without an injected ``roller`` every
    roll is deterministic per turn, round, actor and effect, so an enemy
    block's working copy and the persisted turn advance reach the same result.
    """

    actor = _participant_by_id(combat, actor_id)
    if actor is None or not _is_present(actor) or not _is_alive(actor):
        return []

    def effect_roller(effect_id: str) -> Roller:
        return roller or deterministic_roller(f'spell_recurring:{turn_id}:{round_number}:{actor_id}:{effect_id}')

    results: list[dict[str, Any]] = []
    for effect in active_effects(actor):
        recurring = effect.get('recurring')
        if not isinstance(recurring, Mapping) or recurring.get('aura'):
            continue
        actor = _participant_by_id(combat, actor_id)
        if actor is None or not _is_alive(actor) or effect['id'] not in {current['id'] for current in active_effects(actor)}:
            continue
        entry: dict[str, Any] = {
            'participantId': _text(actor_id), 'effectId': effect['id'],
            'sourceName': effect['sourceName'], 'casterId': effect['casterId'],
        }
        if recurring.get('repeatSave'):
            save = _recurring_save(combat, actor, _ability_key(recurring.get('ability')), recurring.get('dc'), effect_roller(effect['id']))
            entry.update({'kind': 'repeat_save', 'save': save})
            if save['succeeded']:
                entry['endedEffectIds'] = _end_cast_on(actor, _text(effect.get('castId')))
        else:
            entry.update({'kind': 'recurring_damage', **_recurring_damage(combat, _text(actor_id), effect, effect_roller(effect['id']))})
        results.append(entry)
    for bearer in list(_participants(combat)):
        bearer_id = _participant_id(bearer)
        if bearer_id == _text(actor_id):
            continue
        for effect in active_effects(bearer):
            recurring = effect.get('recurring')
            aura = recurring.get('aura') if isinstance(recurring, Mapping) else None
            actor = _participant_by_id(combat, actor_id)
            if not isinstance(aura, Mapping) or actor is None or not _is_alive(actor) or not _aura_reaches(bearer, actor, aura):
                continue
            results.append({
                'participantId': _text(actor_id), 'effectId': effect['id'], 'sourceName': effect['sourceName'],
                'casterId': effect['casterId'], 'kind': 'aura_damage',
                **_recurring_damage(combat, _text(actor_id), effect, effect_roller(effect['id'])),
            })
    return results


def after_damage_spell_effects(
    combat: dict[str, Any], *, target_id: str, source_id: str, event_id: str, roller: Roller | None = None,
) -> list[dict[str, Any]]:
    """End or re-save the spell effects that react to their bearer taking damage, mutating ``combat``.

    Sleep and Hypnotic Pattern end, a charm ends when the charmer's side deals
    the damage, and Tasha's Hideous Laughter grants a save with advantage.
    """

    target = _participant_by_id(combat, target_id)
    if target is None:
        return []
    return _after_damage(
        combat, target, source_id=_text(source_id),
        roller=roller or deterministic_roller(f'spell_after_damage:{event_id}:{target_id}'),
    )


register_turn_start_hook(resolve_turn_start_spell_effects)


def shield_reaction_provider(event: Mapping[str, Any], combat: Mapping[str, Any], state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Cast Shield for a player character when it would turn an enemy's hit into a miss.

    The character must have Shield ready, a level 1+ slot, its reaction and
    ``autoReactions`` not turned off, and the attack total must be below its
    AC + 5.  The slot is spent by a ``spell.cast`` change and the +5 AC lasts
    until the start of the character's next turn.
    """

    if _text(event.get('type')) != 'attack_hit' or event.get('critical') is True:
        return []
    target_id = _text(event.get('targetId'))
    target = _participant_by_id(combat, target_id)
    if target is None or not vitality.is_player_character(target) or not auto_reactions_enabled(target, state):
        return []
    target_ac = _int(event.get('targetAc'), default=0)
    if _int(event.get('attackTotal'), default=0) >= target_ac + 5:
        return []
    actor = next(
        (candidate for candidate in state.get('playerCharacters') or []
         if isinstance(candidate, Mapping) and _text(candidate.get('id')) == target_id),
        None,
    )
    if actor is None:
        return []
    legality = spell_cast_legality(
        actor.get('spellbook'), actor.get('spellResources'), spell_name_or_id=SHIELD_SPELL_NAME,
        class_name=_text(actor.get('class')), level=max(1, _int(actor.get('level'), default=1)),
    )
    spell = legality.get('spell') if isinstance(legality.get('spell'), Mapping) else {}
    if not legality.get('legal') or spell.get('authoritativeEffect') is not True or _key(spell.get('castingTime')) != 'reaction':
        return []
    normalized, _error = _normalized_spell(spell)
    if normalized is None:
        return []
    resource = legality['resource']
    cast_id = stable_change_id('spell_reaction', event.get('turnId'), event.get('eventId'), target_id, normalized['id'])
    normalized['castLevel'] = legality['castLevel']
    ward = _spell_active_effect(
        target, effect=normalized['effects'][0], cast_id=cast_id, caster_id=target_id,
        spell=normalized, effect_index=0, modifiers=normalized['effects'][0]['modifiers'],
    )
    bonus = _int(ward['modifiers'].get('acBonus'), default=0)
    name = _text(target.get('name')) or target_id
    return [{
        'reactorId': target_id,
        'kind': 'shield',
        'label': f'{name} casts Shield: armor class {target_ac} becomes {target_ac + bonus} and the attack misses.',
        'cancelsHit': True,
        'changes': [
            {
                'id': stable_change_id(cast_id, 'effect'), 'turnId': event.get('turnId'),
                'type': 'combat.effect.add', 'participantId': target_id, 'source': REACTION_SOURCE,
                'effect': ward, 'reason': 'Shield raises armor class until the start of its caster\'s next turn.',
                'visible': False,
            },
            {
                'id': cast_id, 'turnId': event.get('turnId'), 'type': 'spell.cast', 'source': 'reaction',
                'actorId': target_id, 'spellId': normalized['id'], 'spellName': normalized['name'],
                'castLevel': legality['castLevel'], 'resourcePool': resource['pool'], 'castingTime': 'reaction',
                'targetIds': [target_id], 'reason': f'{name} cast Shield as a reaction to a hit.', 'visible': True,
            },
        ],
        'consumes': {'reaction': 1, 'spellSlot': legality['castLevel']},
    }]


register_reaction_provider(shield_reaction_provider)


def _actor_remove_effects(actor: dict[str, Any], predicate: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    """Remove effects from a state actor (``health`` shape), dropping conditions and HP they granted."""

    retained: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for effect in active_effects(actor):
        (removed if predicate(effect) else retained).append(effect)
    if not removed:
        return []
    actor['activeEffects'] = retained
    health = actor.setdefault('health', {})
    maintained = {_text(effect.get('condition')) for effect in retained if effect.get('condition')}
    dropped = {
        _text(effect.get('condition')) for effect in removed
        if effect.get('condition') and effect.get('preserveConditionOnExpiry') is not True
    } - maintained
    if dropped:
        health['conditions'] = [value for value in health.get('conditions') or [] if _key(value) not in dropped]
    bonus = sum(max(0, _int(effect.get('maxHpBonus'), default=0)) for effect in removed)
    if bonus:
        maximum = max(1, _int(health.get('maxHp'), default=1) - bonus)
        health['maxHp'] = maximum
        health['currentHp'] = min(_int(health.get('currentHp'), default=0), maximum)
    return removed


def end_encounter_spell_effects(actor: dict[str, Any], participant: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Combat ended: keep only the actor's spell effects that last until a long rest.

    When the character took part, its participant's effects are the current
    ones (an effect dispelled in the fight stays gone).  Returns what ended.
    """

    if participant is not None:
        current_ids = {effect['id'] for effect in active_effects(participant)}
        carried = [
            effect for effect in active_effects(participant)
            if effect.get('until') == 'long_rest' and effect['id'] not in {own['id'] for own in active_effects(actor)}
        ]
        ended = _actor_remove_effects(
            actor, lambda effect: bool(effect.get('castId')) and (effect.get('until') != 'long_rest' or effect['id'] not in current_ids),
        )
        if carried:
            actor['activeEffects'] = [*active_effects(actor), *carried]
        return ended
    return _actor_remove_effects(actor, lambda effect: bool(effect.get('castId')) and effect.get('until') != 'long_rest')


def end_long_rest_spell_effects(actor: dict[str, Any]) -> list[dict[str, Any]]:
    """A long rest ends every spell effect the actor carries (Mage Armor, Aid, out-of-combat buffs)."""

    return _actor_remove_effects(actor, lambda effect: bool(effect.get('castId')))
