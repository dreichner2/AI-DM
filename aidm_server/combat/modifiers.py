"""One modifier engine for conditions and active effects.

Every attack, saving throw, ability check, damage roll and turn-taking decision
reads its condition and active-effect consequences from this module; it is the
only place condition rules live.  All functions are pure over participant
dicts, except the explicit effect-removal helpers which mutate the participant
or combat they are given (callers pass their own working copy).

Active effects (``participant['activeEffects']``) share one shape::

    {"id", "sourceId", "sourceName", "casterId", "modifiers": {...},
     "duration": {"rounds": n, "tickOn": "target_turn_start" | "target_turn_end" | "caster_turn_end"
                  | "caster_turn_start"} | None,
     "concentration": bool, "consumeOn": "attack" | "attacked" | "save" | "check" | None}

An effect may also maintain a condition (``condition``) and carry ``castId`` and
``preserveConditionOnExpiry``.  ``endsWhen`` lists events that end it early:
``damage_taken``, ``unconscious`` (its bearer falls unconscious) and
``no_attack_or_damage_since_last_turn`` (checked as its bearer starts a turn;
:func:`note_attack_or_damage` keeps it going).  A Bardic Inspiration effect
carries ``inspirationDie``.  A condition an enemy ability applied is an effect
with ``origin: "enemy_ability"`` maintaining it for ``target_turn_end`` rounds;
its ``endSave`` (``{"ability", "dc"}``) is repeated at the end of each of its
bearer's turns, and ``appliedDuringTurnOf`` (the participant whose turn it
landed in) keeps the turn boundaries passed before that turn from counting it
(see :func:`aidm_server.combat.pipeline.apply_turn_boundaries`).  Records written before this shape
(``sourceActorId``/``sourceSpellId``/``duration.remaining``/``tick``) are read
through :func:`normalize_active_effect`.

Supported ``modifiers`` keys: attackBonusDice, saveBonusDice, checkBonusDice
(dice strings, sign allowed), acBonus, acMinimum, attackMode, attackedMode,
saveMode ({"abilities": [...], "mode"}), checkMode, bonusDamageVs ({"targetId",
"dice", "type", "weaponOnly"}), bonusWeaponDamage ({"dice" | "flat", "type",
"meleeOnly", "ability"}: the last two limit it to melee attacks using that
ability), resistances (damage types or ["all"]), speedZero, cannotAct,
cannotReact, noHealing, tempHpPerTurn.

Modifier providers registered with :func:`register_modifier_provider` are
called as ``fn(participant, role, context)`` where role is ``attacker``,
``target``, ``saver`` or ``checker`` and context holds ``event``, ``kind``,
``ranged``, ``combat``, ``ability``, ``skill``, ``weapon`` (attacks) and
``other`` (the opposing participant).  Extra-damage providers registered with
:func:`register_extra_damage_provider` are called as ``fn(attacker, target,
context)`` with ``kind``, ``weapon``, ``attack`` and, for spells, ``spellId``
(see :mod:`aidm_server.class_features`) and return damage components (``dice``
or ``flat``, ``type``, ``source``, optional ``oncePerTurn``).  Modifier
providers return a partial result dict whose keys are merged into the
result being built: ``mode`` adds one advantage/disadvantage source, lists
extend, integers add and booleans combine with ``or``.

Attacks-per-action providers registered with
:func:`register_attacks_per_action_provider` are called as
``fn(participant, combat)`` and return the number of weapon attacks one Attack
action grants (Extra Attack); the highest answer, never below 1, wins.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Iterable, Mapping

from aidm_server.canon_text import int_or_default
from aidm_server.damage_dice import parse_signed_dice


ModifierProvider = Callable[[Mapping[str, Any], str, Mapping[str, Any]], Mapping[str, Any] | None]
ExtraDamageProvider = Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Iterable[Mapping[str, Any]] | None]
AttacksPerActionProvider = Callable[[Mapping[str, Any], Mapping[str, Any] | None], int | None]

ABILITY_ALIASES = {
    'str': 'strength',
    'dex': 'dexterity',
    'con': 'constitution',
    'int': 'intelligence',
    'wis': 'wisdom',
    'cha': 'charisma',
}
MODIFIER_ENGINE_SOURCE = 'modifier_engine'
ROLL_MODES = ('advantage', 'disadvantage')
EFFECT_TICKS = frozenset({'target_turn_start', 'target_turn_end', 'caster_turn_end', 'caster_turn_start'})
LEGACY_EFFECT_TICKS = {'source_turn_end': 'caster_turn_end'}
CONSUME_EVENTS = frozenset({'attack', 'attacked', 'save', 'check'})
ENDS_WITHOUT_ATTACK_OR_DAMAGE = 'no_attack_or_damage_since_last_turn'

# Participants with these conditions have left the fight: no turns, no targeting.
REMOVED_CONDITIONS = frozenset(
    {'dead', 'fled', 'escaped', 'retreated', 'withdrawn', 'surrendered', 'yielded', 'absent'}
)
INCAPACITATING_CONDITIONS = frozenset(
    {'incapacitated', 'paralyzed', 'petrified', 'stunned', 'unconscious'}
)
TURN_EXCLUDING_CONDITIONS = REMOVED_CONDITIONS | INCAPACITATING_CONDITIONS
TARGET_EXCLUDING_CONDITIONS = REMOVED_CONDITIONS | {'unconscious'}
SPEED_ZERO_CONDITIONS = ('grappled', 'paralyzed', 'restrained', 'slowed')
STR_DEX_AUTO_FAIL_CONDITIONS = ('paralyzed', 'petrified', 'stunned', 'unconscious')
OWN_ATTACK_DISADVANTAGE_CONDITIONS = ('blinded', 'poisoned', 'prone', 'restrained')
ATTACKED_WITH_ADVANTAGE_CONDITIONS = (
    'blinded',
    'paralyzed',
    'petrified',
    'restrained',
    'stunned',
    'unconscious',
)
MELEE_CRITICAL_CONDITIONS = ('paralyzed', 'unconscious')
CHECK_DISADVANTAGE_CONDITIONS = ('exhausted', 'poisoned')
COVER_BONUSES = {'half': 2, 'three_quarters': 5}

_MODIFIER_PROVIDERS: list[ModifierProvider] = []
_EXTRA_DAMAGE_PROVIDERS: list[ExtraDamageProvider] = []
_ATTACKS_PER_ACTION_PROVIDERS: list[AttacksPerActionProvider] = []


def _text(value: Any) -> str:
    return str(value or '').strip()


def condition_key(value: Any) -> str:
    return _text(value).lower().replace(' ', '_').replace('-', '_')


def ability_key(value: Any) -> str:
    key = condition_key(value)
    return ABILITY_ALIASES.get(key, key)


def condition_keys(participant: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(participant, Mapping):
        return set()
    values = participant.get('conditions')
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {condition_key(value) for value in values if _text(value)}


def _participant_id(participant: Mapping[str, Any] | None) -> str:
    if not isinstance(participant, Mapping):
        return ''
    return _text(participant.get('id') or participant.get('participantId'))


def _participant_name(participant: Mapping[str, Any] | None) -> str:
    if not isinstance(participant, Mapping):
        return ''
    return _text(participant.get('name')) or _participant_id(participant)


def _position(participant: Mapping[str, Any] | None) -> Mapping[str, Any]:
    value = participant.get('position') if isinstance(participant, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def range_band(participant: Mapping[str, Any] | None) -> str:
    position = _position(participant)
    return condition_key(position.get('rangeBand') or position.get('range_band')) or 'near'


def is_hidden(participant: Mapping[str, Any] | None) -> bool:
    position = _position(participant)
    return 'hidden' in condition_keys(participant) or position.get('isHidden', position.get('is_hidden')) is True


def engaged_with(participant: Mapping[str, Any] | None) -> list[str]:
    """Ids of the participants this one is locked in melee with."""

    values = _position(participant).get('engagedWith')
    if not isinstance(values, (list, tuple)):
        return []
    return list(dict.fromkeys(_text(value) for value in values if _text(value)))


def _engaged_by_threat(participant: Mapping[str, Any], combat: Mapping[str, Any] | None) -> bool:
    engaged = engaged_with(participant)
    if not isinstance(combat, Mapping):
        return bool(engaged)
    return any(can_act(_combat_participant(combat, other_id)) for other_id in engaged)


def _hp_current(participant: Mapping[str, Any]) -> int:
    hp = participant.get('hp') if isinstance(participant.get('hp'), Mapping) else {}
    return int_or_default(hp.get('current', hp.get('currentHp')), default=1)


def _is_present(participant: Mapping[str, Any]) -> bool:
    return (
        participant.get('isPresent', participant.get('present', True)) is not False
        and 'absent' not in condition_keys(participant)
    )


def _bool(value: Any) -> bool:
    return value is True or condition_key(value) in {'true', 'yes', '1'}


def _dice_list(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else [value]
    dice: list[str] = []
    for item in values:
        parsed = parse_signed_dice(item) if _text(item) else None
        if parsed:
            dice.append(str(parsed['dice']))
    return dice


def normalize_active_effect(raw: Any) -> dict[str, Any] | None:
    """Return one effect in the standard shape, upgrading legacy spell records."""

    if not isinstance(raw, Mapping):
        return None
    effect_id = _text(raw.get('id'))
    if not effect_id:
        return None
    duration_raw = raw.get('duration') if isinstance(raw.get('duration'), Mapping) else None
    duration = None
    if duration_raw is not None:
        rounds = int_or_default(duration_raw.get('rounds', duration_raw.get('remaining')), default=0)
        tick = condition_key(duration_raw.get('tickOn') or duration_raw.get('tick') or 'target_turn_end')
        tick = LEGACY_EFFECT_TICKS.get(tick, tick)
        if rounds > 0 and tick in EFFECT_TICKS:
            duration = {'rounds': min(1000, rounds), 'tickOn': tick}
    consume_on = condition_key(raw.get('consumeOn'))
    modifiers = raw.get('modifiers') if isinstance(raw.get('modifiers'), Mapping) else {}
    effect = {
        **{key: deepcopy(value) for key, value in raw.items() if key not in {
            'sourceActorId', 'sourceSpellId', 'sourceSpellName', 'operation', 'kind',
        }},
        'id': effect_id,
        'sourceId': _text(raw.get('sourceId') or raw.get('sourceSpellId')),
        'sourceName': _text(raw.get('sourceName') or raw.get('sourceSpellName')),
        'casterId': _text(raw.get('casterId') or raw.get('sourceActorId')),
        'modifiers': deepcopy(dict(modifiers)),
        'duration': duration,
        'concentration': raw.get('concentration') is True,
        'consumeOn': consume_on if consume_on in CONSUME_EVENTS else None,
    }
    condition = condition_key(raw.get('condition'))
    if condition:
        effect['condition'] = condition
    else:
        effect.pop('condition', None)
    return effect


def active_effects(participant: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(participant, Mapping):
        return []
    values = participant.get('activeEffects', participant.get('active_effects'))
    if not isinstance(values, list):
        return []
    return [effect for value in values if (effect := normalize_active_effect(value)) is not None]


def _effects_with(participant: Mapping[str, Any] | None, key: str) -> list[dict[str, Any]]:
    return [effect for effect in active_effects(participant) if effect['modifiers'].get(key) not in (None, False, '', [])]


def _effect_label(effect: Mapping[str, Any]) -> str:
    return _text(effect.get('sourceName')) or _text(effect.get('sourceId')) or 'an effect'


def _sources(participant: Mapping[str, Any], condition: str) -> set[str]:
    raw = participant.get(f'{condition}By')
    values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    sources = {_text(value) for value in values if _text(value)}
    sources.update(
        effect['casterId']
        for effect in active_effects(participant)
        if effect.get('condition') == condition and effect['casterId']
    )
    return sources


def _combat_participant(combat: Mapping[str, Any] | None, participant_id: str) -> Mapping[str, Any] | None:
    if not isinstance(combat, Mapping):
        return None
    for participant in combat.get('participants') or []:
        if isinstance(participant, Mapping) and _participant_id(participant) == participant_id:
            return participant
    return None


def _fear_source_present(participant: Mapping[str, Any], combat: Mapping[str, Any] | None) -> bool:
    sources = _sources(participant, 'frightened')
    if not sources or not isinstance(combat, Mapping):
        return True
    for source_id in sources:
        source = _combat_participant(combat, source_id)
        if (
            isinstance(source, Mapping)
            and _is_present(source)
            and source.get('isAlive') is not False
            and _hp_current(source) > 0
            and not condition_keys(source).intersection(REMOVED_CONDITIONS)
        ):
            return True
    return False


def _new_result(**fields: Any) -> dict[str, Any]:
    return {'_advantage': [], '_disadvantage': [], 'reasons': [], 'consume': [], **fields}


def _add_mode(result: dict[str, Any], mode: Any, reason: str) -> None:
    normalized = condition_key(mode)
    if normalized not in ROLL_MODES:
        return
    result['_advantage' if normalized == 'advantage' else '_disadvantage'].append(reason)
    result['reasons'].append(reason)


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    advantage = result.pop('_advantage')
    disadvantage = result.pop('_disadvantage')
    if advantage and disadvantage:
        result['mode'] = 'normal'
    elif advantage:
        result['mode'] = 'advantage'
    elif disadvantage:
        result['mode'] = 'disadvantage'
    else:
        result['mode'] = 'normal'
    result['reasons'] = list(dict.fromkeys(result['reasons']))
    return result


def _consume(result: dict[str, Any], participant: Mapping[str, Any] | None, event: str) -> None:
    participant_id = _participant_id(participant)
    for effect in active_effects(participant):
        if effect['consumeOn'] == event:
            result['consume'].append({'participantId': participant_id, 'effectId': effect['id']})


def _merge_provider_results(
    result: dict[str, Any],
    participant: Mapping[str, Any] | None,
    role: str,
    context: Mapping[str, Any],
) -> None:
    if not isinstance(participant, Mapping):
        return
    for provider in list(_MODIFIER_PROVIDERS):
        partial = provider(participant, role, context)
        if not isinstance(partial, Mapping):
            continue
        for key, value in partial.items():
            if key == 'mode':
                _add_mode(result, value, f'{_participant_name(participant)}: {value}')
            elif key == 'reasons':
                result['reasons'].extend(_text(item) for item in value or [] if _text(item))
            elif key not in result or key.startswith('_'):
                continue
            elif isinstance(result[key], bool):
                result[key] = result[key] or bool(value)
            elif isinstance(result[key], list):
                result[key].extend(_dice_list(value) if key.lower().endswith('dice') else list(value or []))
            elif isinstance(result[key], int):
                result[key] += int_or_default(value, default=0)


def register_modifier_provider(fn: ModifierProvider) -> ModifierProvider:
    if fn not in _MODIFIER_PROVIDERS:
        _MODIFIER_PROVIDERS.append(fn)
    return fn


def unregister_modifier_provider(fn: ModifierProvider) -> None:
    if fn in _MODIFIER_PROVIDERS:
        _MODIFIER_PROVIDERS.remove(fn)


def register_extra_damage_provider(fn: ExtraDamageProvider) -> ExtraDamageProvider:
    if fn not in _EXTRA_DAMAGE_PROVIDERS:
        _EXTRA_DAMAGE_PROVIDERS.append(fn)
    return fn


def unregister_extra_damage_provider(fn: ExtraDamageProvider) -> None:
    if fn in _EXTRA_DAMAGE_PROVIDERS:
        _EXTRA_DAMAGE_PROVIDERS.remove(fn)


def register_attacks_per_action_provider(fn: AttacksPerActionProvider) -> AttacksPerActionProvider:
    if fn not in _ATTACKS_PER_ACTION_PROVIDERS:
        _ATTACKS_PER_ACTION_PROVIDERS.append(fn)
    return fn


def unregister_attacks_per_action_provider(fn: AttacksPerActionProvider) -> None:
    if fn in _ATTACKS_PER_ACTION_PROVIDERS:
        _ATTACKS_PER_ACTION_PROVIDERS.remove(fn)


def attacks_per_action(participant: Mapping[str, Any] | None, combat: Mapping[str, Any] | None = None) -> int:
    """Weapon attacks one Attack action grants this participant (1 without Extra Attack)."""

    if not isinstance(participant, Mapping):
        return 1
    granted = [int_or_default(provider(participant, combat), default=1) for provider in list(_ATTACKS_PER_ACTION_PROVIDERS)]
    return max(1, min(4, max(granted, default=1)))


def cover_bonus(combat: Mapping[str, Any] | None, target: Mapping[str, Any] | None) -> int:
    cover_id = _text(_position(target).get('coverId') or _position(target).get('cover_id'))
    battlefield = combat.get('battlefield') if isinstance(combat, Mapping) and isinstance(combat.get('battlefield'), Mapping) else {}
    for entry in battlefield.get('cover') or []:
        if isinstance(entry, Mapping) and cover_id and _text(entry.get('id')) == cover_id:
            return COVER_BONUSES.get(condition_key(entry.get('coverType') or entry.get('cover_type')), 0)
    return 0


def in_melee_band(attacker: Mapping[str, Any] | None, target: Mapping[str, Any] | None, *, ranged: bool) -> bool:
    """Coarse bands: melee attacks are in the melee band; ranged ones when either side is at melee."""

    return not ranged or range_band(attacker) == 'melee' or range_band(target) == 'melee'


def attack_block_reason(attacker: Mapping[str, Any] | None, target: Mapping[str, Any] | None) -> str:
    """Explain why an attack or harmful spell may not target this participant at all."""

    if not isinstance(attacker, Mapping) or not isinstance(target, Mapping):
        return ''
    if 'charmed' in condition_keys(attacker) and _participant_id(target) in _sources(attacker, 'charmed'):
        return f'{_participant_name(attacker)} is charmed by {_participant_name(target)}.'
    return ''


def attack_context(
    attacker: Mapping[str, Any],
    target: Mapping[str, Any] | None,
    *,
    kind: str,
    ranged: bool,
    combat: Mapping[str, Any] | None = None,
    ignore_cover: bool = False,
    weapon: Mapping[str, Any] | None = None,
    ability: Any = None,
) -> dict[str, Any]:
    """Roll mode, bonus dice and target AC adjustments for one attack roll.

    ``kind`` is ``weapon_attack``, ``spell_attack`` or ``enemy_attack``;
    ``weapon`` (the weapon descriptor) and ``ability`` (the ability the attack
    uses) reach the providers' context.  Besides the documented keys the result
    carries ``coverBonus``, ``targetAcMinimum``, ``consume`` (effects and
    ``hidden`` spent by this attack), ``criticalRangeBonus`` (a critical needs a
    natural roll this much lower than 20) and ``damageRerollBelow`` (weapon
    damage dice at or below it are rerolled once).
    """

    result = _new_result(
        autoCrit=False,
        attackBonusDice=[],
        attackFlatBonus=0,
        targetAcBonus=0,
        coverBonus=0,
        targetAcMinimum=0,
        criticalRangeBonus=0,
        damageRerollBelow=0,
    )
    attacker_keys = condition_keys(attacker)
    for condition in OWN_ATTACK_DISADVANTAGE_CONDITIONS:
        if condition in attacker_keys:
            _add_mode(result, 'disadvantage', f'Attacker is {condition}')
    if 'frightened' in attacker_keys and _fear_source_present(attacker, combat):
        _add_mode(result, 'disadvantage', 'Attacker is frightened')
    if 'invisible' in attacker_keys:
        _add_mode(result, 'advantage', 'Attacker is invisible')
    if is_hidden(attacker):
        _add_mode(result, 'advantage', 'Attacker is hidden')
        result['consume'].append({'participantId': _participant_id(attacker), 'condition': 'hidden'})
    if ranged and _engaged_by_threat(attacker, combat):
        _add_mode(result, 'disadvantage', 'Ranged attack while engaged in melee')
    for effect in _effects_with(attacker, 'attackMode'):
        _add_mode(result, effect['modifiers']['attackMode'], f'Attacker has {_effect_label(effect)}')
    for effect in _effects_with(attacker, 'attackBonusDice'):
        result['attackBonusDice'].extend(_dice_list(effect['modifiers']['attackBonusDice']))
    _consume(result, attacker, 'attack')

    if isinstance(target, Mapping):
        target_keys = condition_keys(target)
        melee = in_melee_band(attacker, target, ranged=ranged)
        for condition in ATTACKED_WITH_ADVANTAGE_CONDITIONS:
            if condition in target_keys:
                _add_mode(result, 'advantage', f'Target is {condition}')
        if 'invisible' in target_keys:
            _add_mode(result, 'disadvantage', 'Target is invisible')
        if 'dodging' in target_keys:
            _add_mode(result, 'disadvantage', 'Target is dodging')
        if target_keys.intersection({'prone', 'unconscious'}):
            if melee:
                _add_mode(result, 'advantage', 'Target is prone')
            else:
                _add_mode(result, 'disadvantage', 'Target is prone at range')
        if melee and target_keys.intersection(MELEE_CRITICAL_CONDITIONS):
            result['autoCrit'] = True
            result['reasons'].append('A melee hit on a paralyzed or unconscious target is critical')
        for effect in _effects_with(target, 'attackedMode'):
            _add_mode(result, effect['modifiers']['attackedMode'], f'Target has {_effect_label(effect)}')
        for effect in _effects_with(target, 'acBonus'):
            result['targetAcBonus'] += int_or_default(effect['modifiers']['acBonus'], default=0)
        for effect in _effects_with(target, 'acMinimum'):
            result['targetAcMinimum'] = max(
                result['targetAcMinimum'],
                int_or_default(effect['modifiers']['acMinimum'], default=0),
            )
        if not ignore_cover:
            result['coverBonus'] = cover_bonus(combat, target)
            result['targetAcBonus'] += result['coverBonus']
        _consume(result, target, 'attacked')

    context = {
        'event': 'attack',
        'kind': kind,
        'ranged': ranged,
        'combat': combat,
        'other': target,
        'weapon': weapon,
        'ability': ability_key(ability) if _text(ability) else None,
    }
    _merge_provider_results(result, attacker, 'attacker', context)
    _merge_provider_results(result, target, 'target', {**context, 'other': attacker})
    return _finish(result)


def armor_class_with_context(base_armor_class: int, context: Mapping[str, Any]) -> int:
    """Apply effect AC bonuses, an AC floor (cover still stacks on it) and cover."""

    bonus = int_or_default(context.get('targetAcBonus'), default=0)
    cover = int_or_default(context.get('coverBonus'), default=0)
    minimum = int_or_default(context.get('targetAcMinimum'), default=0)
    return max(1, min(50, max(int(base_armor_class) + bonus, minimum + cover)))


def save_context(
    target: Mapping[str, Any],
    ability: Any,
    *,
    combat: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Auto-fail, roll mode and bonus dice for one saving throw."""

    normalized_ability = ability_key(ability)
    result = _new_result(autoFail=False, bonusDice=[], flatBonus=0)
    keys = condition_keys(target)
    if normalized_ability in {'strength', 'dexterity'}:
        for condition in STR_DEX_AUTO_FAIL_CONDITIONS:
            if condition in keys:
                result['autoFail'] = True
                result['reasons'].append(f'Saver is {condition}')
        if normalized_ability == 'dexterity':
            if 'restrained' in keys:
                _add_mode(result, 'disadvantage', 'Saver is restrained')
            if 'dodging' in keys:
                _add_mode(result, 'advantage', 'Saver is dodging')
    for effect in _effects_with(target, 'saveMode'):
        raw = effect['modifiers']['saveMode']
        spec = raw if isinstance(raw, Mapping) else {'mode': raw}
        abilities = {ability_key(value) for value in spec.get('abilities') or []}
        if not abilities or normalized_ability in abilities:
            _add_mode(result, spec.get('mode'), f'Saver has {_effect_label(effect)}')
    for effect in _effects_with(target, 'saveBonusDice'):
        result['bonusDice'].extend(_dice_list(effect['modifiers']['saveBonusDice']))
    _consume(result, target, 'save')
    _merge_provider_results(
        result,
        target,
        'saver',
        {'event': 'save', 'ability': normalized_ability, 'combat': combat},
    )
    return _finish(result)


def check_context(
    actor: Mapping[str, Any],
    *,
    ability: Any = None,
    skill: Any = None,
    combat: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Roll mode and bonus dice for one ability check."""

    result = _new_result(bonusDice=[])
    keys = condition_keys(actor)
    for condition in CHECK_DISADVANTAGE_CONDITIONS:
        if condition in keys:
            _add_mode(result, 'disadvantage', f'Character is {condition}')
    if 'frightened' in keys and _fear_source_present(actor, combat):
        _add_mode(result, 'disadvantage', 'Character is frightened')
    for effect in _effects_with(actor, 'checkMode'):
        _add_mode(result, effect['modifiers']['checkMode'], f'Character has {_effect_label(effect)}')
    for effect in _effects_with(actor, 'checkBonusDice'):
        result['bonusDice'].extend(_dice_list(effect['modifiers']['checkBonusDice']))
    _consume(result, actor, 'check')
    _merge_provider_results(
        result,
        actor,
        'checker',
        {'event': 'check', 'ability': ability_key(ability), 'skill': condition_key(skill), 'combat': combat},
    )
    return _finish(result)


def _defense_set(participant: Mapping[str, Any], field: str) -> set[str]:
    values = participant.get(field)
    if values is None and isinstance(participant.get('stats'), Mapping):
        values = participant['stats'].get(field)
    if isinstance(values, str):
        values = [values]
    return {condition_key(value) for value in values or [] if _text(value)}


def damage_defense(target: Mapping[str, Any], damage_type: Any) -> tuple[float, str]:
    """Return the multiplier and its label for one damage type against a target."""

    damage = condition_key(damage_type)
    immunities = _defense_set(target, 'immunities')
    if damage in immunities or 'all' in immunities:
        return 0.0, 'immune'
    resistances = _defense_set(target, 'resistances')
    for effect in _effects_with(target, 'resistances'):
        raw = effect['modifiers']['resistances']
        resistances.update(condition_key(value) for value in (raw if isinstance(raw, list) else [raw]))
    resistant = damage in resistances or 'all' in resistances or 'petrified' in condition_keys(target)
    vulnerable = damage in _defense_set(target, 'vulnerabilities')
    if resistant and vulnerable:
        return 1.0, 'resistance_and_vulnerability_cancel'
    if resistant:
        return 0.5, 'resistant'
    if vulnerable:
        return 2.0, 'vulnerable'
    return 1.0, 'normal'


def damage_multiplier(target: Mapping[str, Any], damage_type: Any) -> float:
    return damage_defense(target, damage_type)[0]


def apply_damage_multiplier(amount: int, multiplier: float) -> int:
    return max(0, int(int(amount) * multiplier))


def extra_damage(
    attacker: Mapping[str, Any],
    target: Mapping[str, Any] | None,
    *,
    kind: str,
    weapon: Mapping[str, Any] | None = None,
    attack: Mapping[str, Any] | None = None,
    spell_id: Any = None,
) -> list[dict[str, Any]]:
    """Additional damage components granted to this attacker for this hit.

    ``attack`` describes the attack (``mode``, ``ranged``, ``ability``,
    ``critical``, ``combat``, ``rider``, ``oncePerTurnUsed``); ``spell_id``
    names the spell of a spell attack.  A component without a ``type`` takes
    the weapon's (the caller supplies a spell's).
    """

    weapon_type = condition_key((weapon or {}).get('damageType') or (weapon or {}).get('type'))
    attack_info = dict(attack) if isinstance(attack, Mapping) else {}
    target_id = _participant_id(target)
    components: list[dict[str, Any]] = []
    for effect in _effects_with(attacker, 'bonusDamageVs'):
        spec = effect['modifiers']['bonusDamageVs']
        if not isinstance(spec, Mapping) or (spec.get('weaponOnly') is True and kind != 'weapon_attack'):
            continue
        if target_id and _text(spec.get('targetId')) == target_id and _dice_list(spec.get('dice')):
            components.append(
                {'dice': _dice_list(spec.get('dice'))[0].lstrip('+'), 'type': condition_key(spec.get('type')) or weapon_type, 'source': _effect_label(effect)}
            )
    if kind == 'weapon_attack':
        for effect in _effects_with(attacker, 'bonusWeaponDamage'):
            spec = effect['modifiers']['bonusWeaponDamage']
            if not isinstance(spec, Mapping):
                continue
            if spec.get('meleeOnly') is True and attack_info.get('ranged') is not False:
                continue
            if _text(spec.get('ability')) and ability_key(attack_info.get('ability')) != ability_key(spec.get('ability')):
                continue
            component: dict[str, Any] = {'type': condition_key(spec.get('type')) or weapon_type, 'source': _effect_label(effect)}
            if spec.get('flat') is not None:
                component['flat'] = int_or_default(spec.get('flat'), default=0)
            elif _dice_list(spec.get('dice')):
                component['dice'] = _dice_list(spec.get('dice'))[0].lstrip('+')
            else:
                continue
            components.append(component)
    context: dict[str, Any] = {'kind': kind, 'weapon': weapon, 'attack': attack_info}
    if _text(spell_id):
        context['spellId'] = _text(spell_id)
    for provider in list(_EXTRA_DAMAGE_PROVIDERS):
        for component in provider(attacker, target or {}, context) or []:
            if isinstance(component, Mapping) and (component.get('dice') or component.get('flat') is not None):
                components.append(dict(component))
    return components


def cannot_act_reasons(participant: Mapping[str, Any]) -> list[str]:
    reasons = [
        f'{condition.replace("_", " ").capitalize()}.'
        for condition in sorted(condition_keys(participant).intersection(TURN_EXCLUDING_CONDITIONS))
    ]
    reasons.extend(f'Held by {_effect_label(effect)}.' for effect in _effects_with(participant, 'cannotAct'))
    return reasons


def can_act(participant: Mapping[str, Any] | None) -> bool:
    """True when the participant is present, alive, conscious and free to take turns or cast."""

    if not isinstance(participant, Mapping):
        return False
    return (
        _is_present(participant)
        and participant.get('isAlive') is not False
        and participant.get('isConscious') is not False
        and _hp_current(participant) > 0
        and not cannot_act_reasons(participant)
    )


def cannot_react(participant: Mapping[str, Any] | None) -> bool:
    return bool(_effects_with(participant, 'cannotReact'))


def speed_zero_reasons(participant: Mapping[str, Any]) -> list[str]:
    keys = condition_keys(participant)
    reasons = [f'{condition.capitalize()}: no movement.' for condition in SPEED_ZERO_CONDITIONS if condition in keys]
    reasons.extend(f'{_effect_label(effect)}: no movement.' for effect in _effects_with(participant, 'speedZero'))
    return reasons


def speed_is_zero(participant: Mapping[str, Any] | None) -> bool:
    return isinstance(participant, Mapping) and bool(speed_zero_reasons(participant))


def heals_nothing(participant: Mapping[str, Any] | None) -> bool:
    return bool(_effects_with(participant, 'noHealing'))


def temp_hp_per_turn(participant: Mapping[str, Any] | None) -> int:
    return max(
        [int_or_default(effect['modifiers'].get('tempHpPerTurn'), default=0) for effect in _effects_with(participant, 'tempHpPerTurn')]
        or [0]
    )


def _maintained_conditions(effects: Iterable[Mapping[str, Any]]) -> set[str]:
    return {_text(effect.get('condition')) for effect in effects if _text(effect.get('condition'))}


def remove_active_effects(
    participant: dict[str, Any],
    predicate: Callable[[dict[str, Any]], bool],
) -> list[dict[str, Any]]:
    """Remove matching effects and any condition no remaining effect maintains."""

    retained: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for effect in active_effects(participant):
        (removed if predicate(effect) else retained).append(effect)
    if not removed and not isinstance(participant.get('activeEffects'), list):
        return []
    participant['activeEffects'] = retained
    still_maintained = _maintained_conditions(retained)
    preserved = {
        _text(effect.get('condition'))
        for effect in removed
        if effect.get('preserveConditionOnExpiry') is True
    }
    dropped = _maintained_conditions(removed) - still_maintained - preserved
    if dropped:
        participant['conditions'] = [
            condition
            for condition in dict.fromkeys(condition_key(value) for value in participant.get('conditions') or [] if _text(value))
            if condition not in dropped
        ]
    for condition in dropped:
        participant.pop(f'{condition}By', None)
    return removed


def _ends_when(effect: Mapping[str, Any]) -> set[str]:
    raw = effect.get('endsWhen')
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    return {condition_key(value) for value in values if _text(value)}


def _unconscious(participant: Mapping[str, Any]) -> bool:
    return _hp_current(participant) <= 0 or 'unconscious' in condition_keys(participant)


def note_attack_or_damage(participant: dict[str, Any]) -> None:
    """The bearer attacked or took damage: effects that end without either keep going this turn."""

    effects = active_effects(participant)
    sustained = [effect for effect in effects if ENDS_WITHOUT_ATTACK_OR_DAMAGE in _ends_when(effect)]
    if not sustained:
        return
    for effect in sustained:
        effect['attackedOrDamaged'] = True
    participant['activeEffects'] = effects


def _remove_ending(participant: dict[str, Any], ends: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    # Untouched records keep their stored shape when nothing ends.
    if not any(ends(effect) for effect in active_effects(participant)):
        return []
    return remove_active_effects(participant, ends)


def end_effects_on_damage(participant: dict[str, Any]) -> list[dict[str, Any]]:
    """Damage landed on the bearer: end what damage or unconsciousness ends; returns the removed effects."""

    note_attack_or_damage(participant)
    unconscious = _unconscious(participant)
    return _remove_ending(
        participant,
        lambda effect: 'damage_taken' in _ends_when(effect) or (unconscious and 'unconscious' in _ends_when(effect)),
    )


def end_effects_at_turn_start(participant: dict[str, Any]) -> list[dict[str, Any]]:
    """The bearer starts a turn: end effects it let lapse (no attack or damage since its last turn,
    or it is unconscious) and start the next watch; returns the removed effects."""

    unconscious = _unconscious(participant)
    removed = _remove_ending(
        participant,
        lambda effect: (unconscious and 'unconscious' in _ends_when(effect))
        or (ENDS_WITHOUT_ATTACK_OR_DAMAGE in _ends_when(effect) and effect.get('attackedOrDamaged') is not True),
    )
    effects = active_effects(participant)
    if any('attackedOrDamaged' in effect for effect in effects):
        for effect in effects:
            effect.pop('attackedOrDamaged', None)
        participant['activeEffects'] = effects
    return removed


def roll_inspiration(participant: Mapping[str, Any] | None, roller: Callable[[int], int]) -> dict[str, Any] | None:
    """Roll the participant's unused Bardic Inspiration die for a d20 roll that would fail:
    ``{"die", "roll", "consume"}``, or None without one.

    ``consume`` is the entry that spends the effect once the roll is recorded.
    """

    effect = next((effect for effect in active_effects(participant) if _text(effect.get('inspirationDie'))), None)
    sides = int_or_default(_text(effect['inspirationDie']).lower().removeprefix('d'), default=0) if effect else 0
    if sides < 2:
        return None
    roll = max(1, min(sides, int_or_default(roller(sides), default=1)))
    return {
        'die': f'd{sides}',
        'roll': roll,
        'consume': {'participantId': _participant_id(participant), 'effectId': effect['id']},
    }


def _participants(combat: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [participant for participant in combat.get('participants') or [] if isinstance(participant, dict)]


def end_concentration_effects(combat: dict[str, Any], *, caster_id: Any, source_id: Any) -> list[dict[str, Any]]:
    """Concentration ended: drop every effect of that caster and source held by concentration."""

    caster = _text(caster_id)
    source = _text(source_id)
    removed: list[dict[str, Any]] = []
    for participant in _participants(combat):
        removed.extend(
            {**effect, 'targetId': _participant_id(participant)}
            for effect in remove_active_effects(
                participant,
                lambda effect: effect['casterId'] == caster and effect['sourceId'] == source and effect['concentration'],
            )
        )
    return removed


def consume_modifiers(combat: dict[str, Any], consumed: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Spend the effects (and ``hidden``) an attack, save or check reported in ``consume``."""

    removed: list[dict[str, Any]] = []
    for entry in consumed:
        participant = next(
            (item for item in _participants(combat) if _participant_id(item) == _text(entry.get('participantId'))),
            None,
        )
        if participant is None:
            continue
        if entry.get('condition') == 'hidden':
            participant['conditions'] = [value for value in participant.get('conditions') or [] if condition_key(value) != 'hidden']
            if isinstance(participant.get('position'), dict):
                participant['position']['isHidden'] = False
            removed.append({'participantId': _participant_id(participant), 'condition': 'hidden'})
            continue
        effect_id = _text(entry.get('effectId'))
        removed.extend(
            {**effect, 'targetId': _participant_id(participant)}
            for effect in remove_active_effects(participant, lambda effect: effect['id'] == effect_id)
        )
    return removed


def tick_effects(
    combat: dict[str, Any],
    *,
    actor_id: Any,
    tick_on: str,
    steps: int = 1,
    dormant: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Advance durations for one boundary; target ticks affect effects on the actor,
    the caster ticks tick effects the actor cast; ``dormant`` effect ids do not tick.
    Returns the expired effects."""

    actor = _text(actor_id)
    tick = LEGACY_EFFECT_TICKS.get(tick_on, tick_on)
    decrement = max(1, min(1000, int_or_default(steps, default=1)))
    expired: list[dict[str, Any]] = []
    for participant in _participants(combat):
        bearer = _participant_id(participant)
        effects = active_effects(participant)
        if not effects:
            continue
        expiring: set[str] = set()
        for effect in effects:
            duration = effect.get('duration')
            if not duration or duration['tickOn'] != tick or effect['id'] in dormant:
                continue
            owner = bearer if tick in {'target_turn_start', 'target_turn_end'} else effect['casterId']
            if owner != actor:
                continue
            duration['rounds'] -= decrement
            if duration['rounds'] <= 0:
                expiring.add(effect['id'])
        participant['activeEffects'] = effects
        expired.extend(
            {**effect, 'targetId': bearer}
            for effect in remove_active_effects(participant, lambda effect: effect['id'] in expiring)
        )
    return expired


def tick_turn_boundary(
    combat: dict[str, Any], *, actor_id: Any, phase: str, dormant: frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    if phase == 'start':
        return [
            *tick_effects(combat, actor_id=actor_id, tick_on='target_turn_start', dormant=dormant),
            *tick_effects(combat, actor_id=actor_id, tick_on='caster_turn_start', dormant=dormant),
        ]
    return [
        *tick_effects(combat, actor_id=actor_id, tick_on='target_turn_end', dormant=dormant),
        *tick_effects(combat, actor_id=actor_id, tick_on='caster_turn_end', dormant=dormant),
    ]
