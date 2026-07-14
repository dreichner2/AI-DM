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
to ``none`` or ``half``.  The effect vocabulary is intentionally small:
``damage``, ``healing``, ``temporary_hp``, and ``condition`` (add/remove).
Instead of an explicit attack bonus or save DC, trusted definitions may provide
``castingAbility`` and let the resolver derive the value from persisted caster
stats and proficiency.

All public resolvers are storage-agnostic and return deep copies.  The caller
must persist the returned combat/resources together in the same transaction as
slot consumption and turn-economy consumption.  An injected ``roller(sides)``
makes every d20 and effect roll deterministic and testable.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping

from aidm_server.character_resources import normalize_concentration
from aidm_server.combat.state import combat_turn_context
from aidm_server.creatures.schemas import DAMAGE_TYPES
from aidm_server.damage_dice import parse_damage_dice_expression


Roller = Callable[[int], int]

SUPPORTED_DELIVERY_TYPES = frozenset({'attack', 'save', 'automatic'})
SUPPORTED_EFFECT_KINDS = frozenset({'damage', 'healing', 'temporary_hp', 'condition'})
SUPPORTED_RELATIONS = frozenset({'enemy', 'ally', 'self', 'any'})
SUPPORTED_SAVE_OUTCOMES = frozenset({'none', 'half'})
SUPPORTED_DURATION_TICKS = frozenset({'target_turn_end', 'source_turn_end'})
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
CASTER_EXCLUDING_CONDITIONS = frozenset(
    {
        'dead',
        'fled',
        'escaped',
        'retreated',
        'withdrawn',
        'surrendered',
        'yielded',
        'unconscious',
        'incapacitated',
        'paralyzed',
        'stunned',
        'absent',
    }
)
CONCENTRATION_BREAKING_CONDITIONS = frozenset(
    {'dead', 'unconscious', 'incapacitated', 'paralyzed', 'stunned', 'petrified'}
)
SPELL_RESOLUTION_LEDGER_LIMIT = 200


__all__ = [
    'ABILITY_KEYS',
    'SUPPORTED_DELIVERY_TYPES',
    'SUPPORTED_EFFECT_KINDS',
    'advance_spell_effect_durations',
    'resolve_concentration_check',
    'resolve_targeted_spell',
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


def _conditions(participant: Mapping[str, Any]) -> set[str]:
    values = participant.get('conditions')
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    return {_key(value) for value in values if _text(value)}


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
    hp = _hp(participant)
    return (
        _is_present(participant)
        and participant.get('isAlive', hp['current'] > 0) is not False
        and participant.get('isConscious', hp['current'] > 0) is not False
        and hp['current'] > 0
        and not _conditions(participant).intersection(CASTER_EXCLUDING_CONDITIONS)
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
        'maxTargets': _bounded_int(
            raw.get('maxTargets', raw.get('max_targets')),
            default=1,
            minimum=1,
            maximum=20,
        ),
        'allowDefeated': raw.get('allowDefeated', raw.get('allow_defeated', False)) is True,
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

    duration_raw = raw.get('duration')
    duration: dict[str, Any] | None = None
    if isinstance(duration_raw, Mapping):
        remaining = _int(
            duration_raw.get('remaining', duration_raw.get('rounds')),
            default=0,
        )
        if remaining > 0:
            duration = {
                'remaining': min(1000, remaining),
                'tick': _key(duration_raw.get('tick') or 'target_turn_end'),
            }
    elif _int(raw.get('durationRounds', raw.get('duration_rounds')), default=0) > 0:
        duration = {
            'remaining': min(
                1000,
                _int(raw.get('durationRounds', raw.get('duration_rounds')), default=0),
            ),
            'tick': 'target_turn_end',
        }

    damage_type = _key(raw.get('damageType') or raw.get('damage_type') or 'force')
    condition = _key(raw.get('condition'))
    operation = _key(raw.get('operation') or 'add')
    return {
        'kind': kind,
        'expression': expression,
        'damageType': damage_type,
        'condition': condition,
        'operation': operation,
        'duration': duration,
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
        if effect['kind'] in {'damage', 'healing', 'temporary_hp'} and not effect['expression']:
            return None, f"Spell effect {index + 1} requires a bounded dice or amount expression."
        if effect['kind'] == 'damage' and effect['damageType'] not in DAMAGE_TYPES:
            return None, f"Unsupported damage type: {effect['damageType'] or 'missing'}."
        if effect['kind'] == 'condition':
            if not effect['condition']:
                return None, f'Spell effect {index + 1} requires a condition.'
            if effect['operation'] not in {'add', 'remove'}:
                return None, 'Condition operation must be add or remove.'
            duration = effect['duration']
            if duration and duration['tick'] not in SUPPORTED_DURATION_TICKS:
                return None, 'Condition duration tick must be target_turn_end or source_turn_end.'
        if effect['onSuccessfulSave'] and effect['onSuccessfulSave'] not in {'none', 'half', 'full'}:
            return None, 'Effect onSuccessfulSave must be none, half, or full.'
        effects.append(effect)

    return (
        {
            'id': spell_id,
            'name': spell_name,
            'delivery': delivery,
            'target': target,
            'effects': effects,
            'concentration': spell.get('concentration') is True,
            'requireActiveTurn': spell.get(
                'requireActiveTurn', spell.get('require_active_turn', True)
            ) is not False,
            'requireActiveCombat': spell.get(
                'requireActiveCombat', spell.get('require_active_combat', True)
            ) is not False,
        },
        '',
    )


def _legality_failure(code: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {'legal': False, 'code': code, 'reason': reason, **extra}


def spell_target_legality(
    combat: Mapping[str, Any],
    *,
    caster_id: str,
    spell: Mapping[str, Any],
    target_ids: Iterable[str],
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
    if normalized_spell['requireActiveCombat'] and status not in {'starting', 'active'}:
        return _legality_failure(
            'spell_combat_inactive',
            'This spell action requires an active encounter.',
            targets=[],
        )
    if normalized_spell['requireActiveTurn']:
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
        elif not target_spec['allowDefeated'] and not _is_alive(target):
            code = 'spell_target_defeated'
            reason = 'The selected target is already defeated.'
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

        if target is not None and not code and target_id != _text(caster_id):
            position = _position(target)
            range_band = _key(position.get('rangeBand') or position.get('range_band') or 'near')
            caster_zone = _text(_position(caster).get('zoneId') or _position(caster).get('zone_id'))
            target_zone = _text(position.get('zoneId') or position.get('zone_id'))
            cover = _cover_entry(combat, target)
            cover_type = _key((cover or {}).get('coverType') or (cover or {}).get('cover_type'))
            hidden = position.get('isHidden', position.get('is_hidden')) is True
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


def _armor_class(
    combat: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    ignore_cover: bool = False,
) -> tuple[int, int]:
    stats = target.get('stats') if isinstance(target.get('stats'), Mapping) else {}
    base = _bounded_int(
        target.get('armorClass', stats.get('armorClass', stats.get('armor_class'))),
        default=10,
        minimum=1,
        maximum=40,
    )
    cover = None if ignore_cover else _cover_entry(combat, target)
    cover_type = _key((cover or {}).get('coverType') or (cover or {}).get('cover_type'))
    cover_bonus = {'half': 2, 'three_quarters': 5}.get(cover_type, 0)
    return min(45, base + cover_bonus), cover_bonus


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


def _defense_types(participant: Mapping[str, Any], field: str) -> set[str]:
    values = participant.get(field)
    if values is None and isinstance(participant.get('stats'), Mapping):
        values = participant['stats'].get(field)
    if isinstance(values, str):
        values = [values]
    return {_key(value) for value in values or [] if _text(value)}


def _condition_immunities(participant: Mapping[str, Any]) -> set[str]:
    for key in ('conditionImmunities', 'condition_immunities'):
        values = participant.get(key)
        if isinstance(values, str):
            values = [values]
        if isinstance(values, (list, tuple, set)):
            return {_key(value) for value in values if _text(value)}
    return set()


def _active_effects(participant: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = participant.get('activeEffects', participant.get('active_effects'))
    return [deepcopy(value) for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def _effect_id(cast_id: str, target_id: str, effect_index: int) -> str:
    digest = hashlib.sha256(f'{cast_id}:{target_id}:{effect_index}'.encode()).hexdigest()[:20]
    return f'sfx_{digest}'


def _set_conditions(participant: dict[str, Any], conditions: Iterable[str]) -> None:
    participant['conditions'] = list(dict.fromkeys(_key(value) for value in conditions if _key(value)))


def _condition_maintained(participant: Mapping[str, Any], condition: str) -> bool:
    return any(
        _key(effect.get('kind')) == 'condition'
        and _key(effect.get('operation') or 'add') == 'add'
        and _key(effect.get('condition')) == condition
        for effect in _active_effects(participant)
    )


def _removed_effects_preserve_condition(
    effects: Iterable[Mapping[str, Any]], condition: str
) -> bool:
    return any(
        _key(effect.get('condition')) == condition
        and effect.get('preserveConditionOnExpiry') is True
        for effect in effects
    )


def _remove_active_effects(
    combat: dict[str, Any],
    *,
    source_actor_id: str,
    source_spell_id: str,
) -> list[dict[str, Any]]:
    removed: list[dict[str, Any]] = []
    for participant in _participants(combat):
        retained = []
        removed_from_participant = []
        for effect in _active_effects(participant):
            if (
                _text(effect.get('sourceActorId')) == source_actor_id
                and _text(effect.get('sourceSpellId')) == source_spell_id
                and effect.get('concentration') is True
            ):
                removed.append(deepcopy(effect))
                removed_from_participant.append(effect)
            else:
                retained.append(effect)
        participant['activeEffects'] = retained
        conditions = _conditions(participant)
        for effect in removed_from_participant:
            condition = _key(effect.get('condition'))
            if (
                condition
                and not _condition_maintained(participant, condition)
                and not _removed_effects_preserve_condition(
                    removed_from_participant, condition
                )
            ):
                conditions.discard(condition)
        _set_conditions(participant, sorted(conditions))
    return removed


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
        removed = _remove_active_effects(
            combat,
            source_actor_id=previous_caster,
            source_spell_id=_text(previous.get('spellId')),
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
        return {'type': 'automatic', 'delivered': True, 'critical': False}
    natural_roll = _roll_die(20, roller)
    if delivery_type == 'attack':
        attack_bonus = _bounded_int(delivery.get('attackBonus'), default=0, minimum=-20, maximum=30)
        target_ac, cover_bonus = _armor_class(
            combat,
            target,
            ignore_cover=target_spec.get('ignoreCover') is True,
        )
        total = natural_roll + attack_bonus
        critical = natural_roll == 20
        hit = natural_roll != 1 and (critical or total >= target_ac)
        return {
            'type': 'attack',
            'delivered': hit,
            'critical': critical,
            'naturalRoll': natural_roll,
            'modifier': attack_bonus,
            'total': total,
            'targetArmorClass': target_ac,
            'coverBonus': cover_bonus,
        }
    ability = str(delivery['ability'])
    modifier = _save_modifier(target, ability)
    total = natural_roll + modifier
    success = total >= int(delivery['dc'])
    return {
        'type': 'save',
        'delivered': not success,
        'critical': False,
        'ability': ability,
        'naturalRoll': natural_roll,
        'modifier': modifier,
        'total': total,
        'dc': int(delivery['dc']),
        'saveSucceeded': success,
        'onSuccess': delivery['onSuccess'],
    }


def _save_effect_mode(effect: Mapping[str, Any], delivery: Mapping[str, Any]) -> str:
    explicit = _key(effect.get('onSuccessfulSave'))
    if explicit:
        return explicit
    if effect['kind'] == 'damage':
        return _key(delivery.get('onSuccess'))
    return 'none'


def _damage_multiplier(target: Mapping[str, Any], damage_type: str) -> tuple[int, int, str]:
    if damage_type in _defense_types(target, 'immunities'):
        return 0, 1, 'immune'
    resistant = damage_type in _defense_types(target, 'resistances')
    vulnerable = damage_type in _defense_types(target, 'vulnerabilities')
    if resistant and vulnerable:
        return 1, 1, 'resistance_and_vulnerability_cancel'
    if resistant:
        return 1, 2, 'resistant'
    if vulnerable:
        return 2, 1, 'vulnerable'
    return 1, 1, 'normal'


def _apply_damage(
    participant: dict[str, Any],
    *,
    rolled: dict[str, Any],
    damage_type: str,
    save_divisor: int,
) -> dict[str, Any]:
    hp_before = _hp(participant)
    after_save = int(rolled['total']) // max(1, save_divisor)
    numerator, denominator, defense = _damage_multiplier(participant, damage_type)
    final_damage = (after_save * numerator) // denominator
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
    was_defeated = (
        hp_before['current'] <= 0
        or participant.get('isAlive', hp_before['current'] > 0) is False
    )
    missing = max(0, hp_before['max'] - hp_before['current'])
    applied = min(missing, int(rolled['total']))
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
    before = sorted(_conditions(participant))
    conditions = set(before)
    active_effects = _active_effects(participant)
    if operation == 'remove':
        conditions.discard(condition)
        active_effects = [
            active_effect
            for active_effect in active_effects
            if _key(active_effect.get('condition')) != condition
        ]
        participant['activeEffects'] = active_effects
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
    if effect.get('duration') or spell.get('concentration') is True:
        existing_condition_effects = [
            existing
            for existing in active_effects
            if _key(existing.get('kind')) == 'condition'
            and _key(existing.get('operation') or 'add') == 'add'
            and _key(existing.get('condition')) == condition
        ]
        preserve_on_expiry = condition in before and (
            not existing_condition_effects
            or any(
                existing.get('preserveConditionOnExpiry') is True
                for existing in existing_condition_effects
            )
        )
        active_effect = {
            'id': _effect_id(cast_id, _participant_id(participant), effect_index),
            'kind': 'condition',
            'operation': 'add',
            'condition': condition,
            'sourceActorId': caster_id,
            'sourceSpellId': spell['id'],
            'sourceSpellName': spell['name'],
            'castId': cast_id,
            'concentration': spell.get('concentration') is True,
            'preserveConditionOnExpiry': preserve_on_expiry,
        }
        if effect.get('duration'):
            active_effect['duration'] = deepcopy(effect['duration'])
        elif spell.get('concentration') is True:
            active_effect['duration'] = {'kind': 'concentration'}
        active_effects = [
            existing
            for existing in active_effects
            if _text(existing.get('id')) != active_effect['id']
        ]
        active_effects.append(active_effect)
        participant['activeEffects'] = active_effects
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


def _apply_effect(
    participant: dict[str, Any],
    *,
    effect: Mapping[str, Any],
    delivery: Mapping[str, Any],
    delivery_result: Mapping[str, Any],
    roller: Roller,
    cast_id: str,
    caster_id: str,
    spell: Mapping[str, Any],
    effect_index: int,
) -> dict[str, Any]:
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
    rolled = _roll_expression(
        str(effect['expression']),
        roller,
        critical=delivery_result.get('critical') is True and effect['kind'] == 'damage',
    )
    if effect['kind'] == 'damage':
        return _apply_damage(
            participant,
            rolled=rolled,
            damage_type=str(effect['damageType']),
            save_divisor=2 if save_succeeded and save_mode == 'half' else 1,
        )
    if effect['kind'] == 'healing':
        return _apply_healing(participant, rolled=rolled)
    return _apply_temporary_hp(participant, rolled=rolled)


def resolve_targeted_spell(
    combat: Mapping[str, Any],
    *,
    caster_id: str,
    spell: Mapping[str, Any],
    target_ids: Iterable[str],
    cast_id: str,
    roller: Roller,
    caster_resources: Mapping[str, Any] | None = None,
    current_turn: int | None = None,
) -> dict[str, Any]:
    """Resolve one exact-ID spell cast atomically and return JSON-safe state.

    The returned ``combat`` is a deep copy.  Invalid casts and cast-id conflicts
    do not roll or mutate it.  ``spellResolutionLedger`` makes retries against
    the returned combat idempotent; callers should still keep the repository's
    broader state-change ledger as the durable cross-system guard.
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
    for prior in _ledger(combat):
        if _text(prior.get('castId')) != normalized_cast_id:
            continue
        same_request = (
            _text(prior.get('casterId')) == _text(caster_id)
            and _text(prior.get('spellId')) == requested_spell_id
            and list(prior.get('targetIds') or []) == normalized_target_ids
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
    next_combat = deepcopy(dict(combat))
    caster = _participant_by_id(next_combat, caster_id)
    assert caster is not None  # Proven by legality before any roll or mutation.

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

    target_resolutions: list[dict[str, Any]] = []
    for target_id in normalized_target_ids:
        target = _participant_by_id(next_combat, target_id)
        assert target is not None  # Proven by legality before any roll or mutation.
        delivery_result = _resolve_delivery(
            next_combat,
            caster,
            target,
            normalized_spell['delivery'],
            normalized_spell['target'],
            roller,
        )
        effect_results = [
            _apply_effect(
                target,
                effect=effect,
                delivery=normalized_spell['delivery'],
                delivery_result=delivery_result,
                roller=roller,
                cast_id=normalized_cast_id,
                caster_id=_text(caster_id),
                spell=normalized_spell,
                effect_index=index,
            )
            for index, effect in enumerate(normalized_spell['effects'])
        ]
        target_resolutions.append(
            {
                'targetId': target_id,
                'targetName': _text(target.get('name')) or target_id,
                'delivery': delivery_result,
                'effects': effect_results,
            }
        )

    resolution = {
        'castId': normalized_cast_id,
        'casterId': _text(caster_id),
        'spellId': normalized_spell['id'],
        'spellName': normalized_spell['name'],
        'targetIds': normalized_target_ids,
        'targets': target_resolutions,
        'concentration': {
            'started': normalized_spell['concentration'],
            'replaced': replaced_concentration,
            'removedEffects': removed_concentration_effects,
        },
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
            'targetIds': normalized_target_ids,
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

    ``target_turn_end`` decrements effects on ``actor_id``.  ``source_turn_end``
    decrements effects cast by ``actor_id``.  Expiration removes a condition
    only when no other active effect still maintains it.
    """

    normalized_timing = _key(timing)
    if normalized_timing not in SUPPORTED_DURATION_TICKS:
        return {
            'ok': False,
            'code': 'spell_duration_timing_invalid',
            'reason': 'Duration timing must be target_turn_end or source_turn_end.',
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
    decrement = max(1, min(1000, _int(steps, default=1)))
    next_combat = deepcopy(dict(combat))
    expired: list[dict[str, Any]] = []
    for participant in _participants(next_combat):
        retained = []
        expired_here: list[dict[str, Any]] = []
        for effect in _active_effects(participant):
            duration = effect.get('duration') if isinstance(effect.get('duration'), Mapping) else {}
            effect_timing = _key(duration.get('tick'))
            applies = effect_timing == normalized_timing and (
                (
                    normalized_timing == 'target_turn_end'
                    and _participant_id(participant) == normalized_actor_id
                )
                or (
                    normalized_timing == 'source_turn_end'
                    and _text(effect.get('sourceActorId')) == normalized_actor_id
                )
            )
            if not applies:
                retained.append(effect)
                continue
            next_remaining = max(0, _int(duration.get('remaining'), default=0) - decrement)
            if next_remaining <= 0:
                expired_effect = deepcopy(effect)
                expired_effect['targetId'] = _participant_id(participant)
                expired.append(expired_effect)
                expired_here.append(effect)
                continue
            effect['duration'] = {**duration, 'remaining': next_remaining}
            retained.append(effect)
        participant['activeEffects'] = retained
        conditions = _conditions(participant)
        for effect in expired_here:
            condition = _key(effect.get('condition'))
            if (
                condition
                and not _condition_maintained(participant, condition)
                and not _removed_effects_preserve_condition(expired_here, condition)
            ):
                conditions.discard(condition)
        _set_conditions(participant, sorted(conditions))
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
        or bool(_conditions(caster).intersection(CONCENTRATION_BREAKING_CONDITIONS))
    )
    dc = max(10, max(0, _int(damage)) // 2)
    natural_roll = None
    modifier = _save_modifier(caster, 'constitution')
    total = None
    maintained = False
    failure_reason = 'caster_incapacitated'
    if not incapacitated:
        natural_roll = _roll_die(20, roller)
        total = natural_roll + modifier
        maintained = total >= dc
        failure_reason = '' if maintained else 'saving_throw_failed'

    removed: list[dict[str, Any]] = []
    if not maintained:
        removed = _remove_active_effects(
            next_combat,
            source_actor_id=_text(caster_id),
            source_spell_id=_text(concentration.get('spellId')),
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
            'modifier': modifier,
            'total': total,
            'succeeded': maintained,
            'automaticFailure': incapacitated,
        },
        'removedEffects': removed,
    }
