from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from functools import lru_cache
from typing import Any, Iterable

from aidm_server.models import safe_json_loads
from aidm_server.race_system import find_curated_race, race_definition_from_selection, race_selection_from_json


def _normalize_text(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip())


def _slug(value: Any) -> str:
    normalized = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
    return normalized or 'spell'


def _bounded_spell_level(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(9, parsed))


def _bounded_character_level(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(20, parsed))


def _spell_id(name: str) -> str:
    return f"spell_{_slug(name)}"


def _source_label(source_type: str | None, source_detail: str | None) -> str | None:
    source_type = _normalize_text(source_type).lower()
    source_detail = _normalize_text(source_detail)
    if source_type and source_detail:
        return f'{source_type}:{source_detail}'
    return source_type or source_detail or None


# Rules-backed spell definitions: the server resolves these mechanically.
# Recognized entries are merged after imported spell data so a campaign cannot
# replace a server-owned effect with client-authored damage or targeting rules.
# Range bands follow the coarse battlefield: touch is melee, 30 ft reaches near,
# 60 ft reaches far and 90 ft or more reaches every band.
TOUCH = ['melee']
RANGE_30_FT = ['melee', 'near']
RANGE_60_FT = ['melee', 'near', 'far']
RANGE_90_FT = ['melee', 'near', 'far', 'distant']
ONE_MINUTE = {'rounds': 10, 'tickOn': 'target_turn_end'}
TEN_MINUTES = {'rounds': 100, 'tickOn': 'target_turn_end'}
UNTIL_ENCOUNTER_ENDS = {'until': 'encounter'}
UNTIL_LONG_REST = {'until': 'long_rest'}
CHROMATIC_ORB_DAMAGE_TYPES = ['acid', 'cold', 'fire', 'lightning', 'poison', 'thunder']

AUTHORITATIVE_SPELL_EFFECTS: dict[str, dict[str, Any]] = {
    'fire bolt': {
        'level': 0, 'school': 'evocation', 'classes': ['wizard', 'sorcerer', 'artificer'],
        'delivery': {'type': 'attack'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'maxTargets': 1},
        'effects': [{'kind': 'damage', 'dice': '1d10', 'damageType': 'fire'}],
        'scaling': {'cantrip': True},
    },
    'ray of frost': {
        'level': 0, 'school': 'evocation', 'classes': ['wizard', 'sorcerer', 'artificer'],
        'delivery': {'type': 'attack'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_60_FT, 'maxTargets': 1},
        'effects': [
            {'kind': 'damage', 'dice': '1d8', 'damageType': 'cold'},
            {'kind': 'condition', 'condition': 'slowed', 'duration': {'rounds': 1, 'tickOn': 'target_turn_end'}},
        ],
        'scaling': {'cantrip': True},
    },
    'shocking grasp': {
        'level': 0, 'school': 'evocation', 'classes': ['wizard', 'sorcerer'],
        'delivery': {'type': 'attack', 'ranged': False},
        'target': {'relation': 'enemy', 'rangeBands': TOUCH, 'maxTargets': 1},
        'effects': [
            {'kind': 'damage', 'dice': '1d8', 'damageType': 'lightning'},
            {'kind': 'modifier', 'modifiers': {'cannotReact': True},
             'duration': {'rounds': 1, 'tickOn': 'target_turn_start'}},
        ],
        'scaling': {'cantrip': True},
    },
    'chill touch': {
        'level': 0, 'school': 'necromancy', 'classes': ['wizard', 'sorcerer', 'warlock'],
        'delivery': {'type': 'attack'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'maxTargets': 1},
        'effects': [
            {'kind': 'damage', 'dice': '1d8', 'damageType': 'necrotic'},
            {'kind': 'modifier', 'modifiers': {'noHealing': True},
             'duration': {'rounds': 1, 'tickOn': 'caster_turn_start'}},
        ],
        'scaling': {'cantrip': True},
    },
    'poison spray': {
        'level': 0, 'school': 'conjuration', 'classes': ['sorcerer', 'warlock', 'druid'],
        'delivery': {'type': 'save', 'ability': 'constitution', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_30_FT, 'maxTargets': 1},
        'effects': [{'kind': 'damage', 'dice': '1d12', 'damageType': 'poison'}],
        'scaling': {'cantrip': True},
    },
    'sacred flame': {
        'level': 0, 'school': 'evocation', 'classes': ['cleric'],
        'delivery': {'type': 'save', 'ability': 'dexterity', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_60_FT, 'maxTargets': 1, 'ignoreCover': True},
        'effects': [{'kind': 'damage', 'dice': '1d8', 'damageType': 'radiant'}],
        'scaling': {'cantrip': True},
    },
    'toll the dead': {
        'level': 0, 'school': 'necromancy', 'classes': ['cleric'],
        'delivery': {'type': 'save', 'ability': 'wisdom', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_60_FT, 'maxTargets': 1},
        'effects': [{'kind': 'damage', 'dice': '1d8', 'woundedDice': '1d12', 'damageType': 'necrotic'}],
        'scaling': {'cantrip': True},
    },
    'eldritch blast': {
        'level': 0, 'school': 'evocation', 'classes': ['warlock'],
        'delivery': {'type': 'attack', 'attacks': 1},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'maxTargets': 1},
        'effects': [{'kind': 'damage', 'dice': '1d10', 'damageType': 'force'}],
        'scaling': {'cantrip': True},
    },
    'vicious mockery': {
        'level': 0, 'school': 'enchantment', 'classes': ['bard'],
        'delivery': {'type': 'save', 'ability': 'wisdom', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_60_FT, 'maxTargets': 1},
        'effects': [
            {'kind': 'damage', 'dice': '1d4', 'damageType': 'psychic'},
            {'kind': 'modifier', 'modifiers': {'attackMode': 'disadvantage'}, 'consumeOn': 'attack',
             'duration': {'rounds': 1, 'tickOn': 'target_turn_end'}},
        ],
        'scaling': {'cantrip': True},
    },
    'produce flame': {
        'level': 0, 'school': 'conjuration', 'classes': ['druid'],
        'delivery': {'type': 'attack'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_30_FT, 'maxTargets': 1},
        'effects': [{'kind': 'damage', 'dice': '1d8', 'damageType': 'fire'}],
        'scaling': {'cantrip': True},
    },
    'thorn whip': {
        'level': 0, 'school': 'transmutation', 'classes': ['druid'],
        'delivery': {'type': 'attack', 'ranged': False},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_30_FT, 'maxTargets': 1},
        'effects': [{'kind': 'damage', 'dice': '1d6', 'damageType': 'piercing'}, {'kind': 'move', 'bands': -1}],
        'scaling': {'cantrip': True},
    },
    'guidance': {
        'level': 0, 'school': 'divination', 'classes': ['cleric', 'druid'],
        'outOfCombat': True, 'concentration': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'modifier', 'modifiers': {'checkBonusDice': '1d4'}, 'consumeOn': 'check',
                     'duration': ONE_MINUTE}],
        'scaling': {'cantrip': True},
    },
    'spare the dying': {
        'level': 0, 'school': 'necromancy', 'classes': ['cleric'], 'outOfCombat': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True, 'requiresDying': True},
        'effects': [{'kind': 'stabilize'}],
        'scaling': {'cantrip': True},
    },
    'magic missile': {
        'level': 1, 'school': 'evocation', 'classes': ['wizard', 'sorcerer'],
        'delivery': {'type': 'automatic', 'darts': {'count': 3, 'dice': '1d4+1'}},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'maxTargets': 3, 'ignoreCover': True},
        'effects': [{'kind': 'damage', 'dice': '1d4+1', 'damageType': 'force'}],
        'scaling': {'upcast': {'darts': 1}},
    },
    'burning hands': {
        'level': 1, 'school': 'evocation', 'classes': ['wizard', 'sorcerer'],
        'delivery': {'type': 'save', 'ability': 'dexterity', 'onSuccess': 'half'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_30_FT, 'area': {'maxTargets': 3}},
        'effects': [{'kind': 'damage', 'dice': '3d6', 'damageType': 'fire'}],
        'scaling': {'upcast': {'dice': '1d6'}},
    },
    'thunderwave': {
        'level': 1, 'school': 'evocation', 'classes': ['wizard', 'sorcerer', 'druid', 'bard'],
        'delivery': {'type': 'save', 'ability': 'constitution', 'onSuccess': 'half'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_30_FT, 'area': {'maxTargets': 3}},
        'effects': [{'kind': 'damage', 'dice': '2d8', 'damageType': 'thunder'}, {'kind': 'move', 'bands': 1}],
        'scaling': {'upcast': {'dice': '1d8'}},
    },
    'chromatic orb': {
        'level': 1, 'school': 'evocation', 'classes': ['wizard', 'sorcerer'],
        'delivery': {'type': 'attack'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'maxTargets': 1},
        'effects': [{'kind': 'damage', 'dice': '3d8', 'damageType': 'fire',
                     'damageTypeChoices': CHROMATIC_ORB_DAMAGE_TYPES}],
        'scaling': {'upcast': {'dice': '1d8'}},
    },
    'sleep': {
        'level': 1, 'school': 'enchantment', 'classes': ['wizard', 'sorcerer', 'bard'],
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'area': {'maxTargets': 4}},
        'effects': [{'kind': 'sleep_pool', 'dice': '5d8', 'duration': ONE_MINUTE}],
        'scaling': {'upcast': {'dice': '2d8'}},
    },
    'mage armor': {
        'level': 1, 'school': 'abjuration', 'classes': ['wizard', 'sorcerer'], 'outOfCombat': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'modifier', 'modifiers': {'acBase': 13}, 'duration': UNTIL_LONG_REST}],
    },
    'false life': {
        'level': 1, 'school': 'necromancy', 'classes': ['wizard', 'sorcerer', 'artificer'], 'outOfCombat': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'self', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'temporary_hp', 'dice': '1d4+4'}],
        'scaling': {'upcast': {'flat': 5}},
    },
    'shield': {
        'level': 1, 'school': 'abjuration', 'classes': ['wizard', 'sorcerer'], 'castingTime': 'reaction',
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'self', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'modifier', 'modifiers': {'acBonus': 5},
                     'duration': {'rounds': 1, 'tickOn': 'target_turn_start'}}],
    },
    'cure wounds': {
        'level': 1, 'school': 'evocation', 'classes': ['cleric', 'druid', 'bard', 'paladin', 'ranger', 'artificer'],
        'outOfCombat': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'healing', 'dice': '1d8', 'addCastingModifier': True}],
        'scaling': {'upcast': {'dice': '1d8'}},
    },
    'healing word': {
        'level': 1, 'school': 'evocation', 'classes': ['cleric', 'druid', 'bard'],
        'castingTime': 'bonus_action', 'outOfCombat': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': RANGE_60_FT, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'healing', 'dice': '1d4', 'addCastingModifier': True}],
        'scaling': {'upcast': {'dice': '1d4'}},
    },
    'bless': {
        'level': 1, 'school': 'enchantment', 'classes': ['cleric', 'paladin'], 'concentration': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': RANGE_30_FT, 'maxTargets': 3, 'allowSelf': True},
        'effects': [{'kind': 'modifier', 'modifiers': {'attackBonusDice': '1d4', 'saveBonusDice': '1d4'},
                     'duration': ONE_MINUTE}],
        'scaling': {'upcast': {'targets': 1}},
    },
    'bane': {
        'level': 1, 'school': 'enchantment', 'classes': ['cleric', 'bard'], 'concentration': True,
        'delivery': {'type': 'save', 'ability': 'charisma', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_30_FT, 'maxTargets': 3},
        'effects': [{'kind': 'modifier', 'modifiers': {'attackBonusDice': '-1d4', 'saveBonusDice': '-1d4'},
                     'duration': ONE_MINUTE}],
        'scaling': {'upcast': {'targets': 1}},
    },
    'shield of faith': {
        'level': 1, 'school': 'abjuration', 'classes': ['cleric', 'paladin'],
        'castingTime': 'bonus_action', 'concentration': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': RANGE_60_FT, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'modifier', 'modifiers': {'acBonus': 2}, 'duration': TEN_MINUTES}],
    },
    'guiding bolt': {
        'level': 1, 'school': 'evocation', 'classes': ['cleric'],
        'delivery': {'type': 'attack'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'maxTargets': 1},
        'effects': [
            {'kind': 'damage', 'dice': '4d6', 'damageType': 'radiant'},
            {'kind': 'modifier', 'modifiers': {'attackedMode': 'advantage'}, 'consumeOn': 'attacked',
             'duration': {'rounds': 2, 'tickOn': 'caster_turn_end'}},
        ],
        'scaling': {'upcast': {'dice': '1d6'}},
    },
    'inflict wounds': {
        'level': 1, 'school': 'necromancy', 'classes': ['cleric'],
        'delivery': {'type': 'attack', 'ranged': False},
        'target': {'relation': 'enemy', 'rangeBands': TOUCH, 'maxTargets': 1},
        'effects': [{'kind': 'damage', 'dice': '3d10', 'damageType': 'necrotic'}],
        'scaling': {'upcast': {'dice': '1d10'}},
    },
    'command': {
        'level': 1, 'school': 'enchantment', 'classes': ['cleric', 'paladin'],
        'delivery': {'type': 'save', 'ability': 'wisdom', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_60_FT, 'maxTargets': 1},
        'effects': [{'kind': 'modifier', 'modifiers': {'cannotAct': True},
                     'duration': {'rounds': 1, 'tickOn': 'target_turn_end'}}],
        'scaling': {'upcast': {'targets': 1}},
    },
    'entangle': {
        'level': 1, 'school': 'conjuration', 'classes': ['druid', 'ranger'], 'concentration': True,
        'delivery': {'type': 'save', 'ability': 'strength', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'area': {'maxTargets': 4}},
        'effects': [
            {'kind': 'condition', 'condition': 'restrained', 'duration': ONE_MINUTE},
            {'kind': 'recurring', 'repeatSave': 'strength', 'duration': ONE_MINUTE},
        ],
    },
    'faerie fire': {
        'level': 1, 'school': 'evocation', 'classes': ['druid', 'bard', 'artificer'], 'concentration': True,
        'delivery': {'type': 'save', 'ability': 'dexterity', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_60_FT, 'area': {'maxTargets': 4}},
        'effects': [
            {'kind': 'modifier', 'modifiers': {'attackedMode': 'advantage'}, 'duration': ONE_MINUTE},
            {'kind': 'remove_condition', 'conditions': ['invisible', 'hidden'], 'removeAll': True},
        ],
    },
    "hunter's mark": {
        'level': 1, 'school': 'divination', 'classes': ['ranger'],
        'castingTime': 'bonus_action', 'concentration': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'maxTargets': 1},
        'effects': [{'kind': 'modifier', 'onCaster': True,
                     'modifiers': {'bonusDamageVs': {'dice': '1d6', 'weaponOnly': True}},
                     'duration': UNTIL_ENCOUNTER_ENDS}],
    },
    'hex': {
        'level': 1, 'school': 'enchantment', 'classes': ['warlock'],
        'castingTime': 'bonus_action', 'concentration': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'maxTargets': 1},
        'effects': [{'kind': 'modifier', 'onCaster': True,
                     'modifiers': {'bonusDamageVs': {'dice': '1d6', 'type': 'necrotic'}},
                     'duration': UNTIL_ENCOUNTER_ENDS}],
    },
    'armor of agathys': {
        'level': 1, 'school': 'abjuration', 'classes': ['warlock'], 'outOfCombat': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'self', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'temporary_hp', 'dice': '5'}],
        'scaling': {'upcast': {'flat': 5}},
    },
    'charm person': {
        'level': 1, 'school': 'enchantment', 'classes': ['warlock', 'bard'],
        'delivery': {'type': 'save', 'ability': 'wisdom', 'onSuccess': 'none', 'combatSaveMode': 'advantage'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_30_FT, 'maxTargets': 1},
        'effects': [{'kind': 'condition', 'condition': 'charmed', 'duration': UNTIL_ENCOUNTER_ENDS, 'endsOnHarm': True}],
        'scaling': {'upcast': {'targets': 1}},
    },
    'dissonant whispers': {
        'level': 1, 'school': 'enchantment', 'classes': ['bard'],
        'delivery': {'type': 'save', 'ability': 'wisdom', 'onSuccess': 'half'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_60_FT, 'maxTargets': 1},
        'effects': [{'kind': 'damage', 'dice': '3d6', 'damageType': 'psychic'}, {'kind': 'move', 'bands': 1}],
        'scaling': {'upcast': {'dice': '1d6'}},
    },
    "tasha's hideous laughter": {
        'level': 1, 'school': 'enchantment', 'classes': ['bard'], 'concentration': True,
        'delivery': {'type': 'save', 'ability': 'wisdom', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_30_FT, 'maxTargets': 1},
        'effects': [
            {'kind': 'condition', 'condition': 'prone', 'duration': ONE_MINUTE},
            {'kind': 'condition', 'condition': 'incapacitated', 'duration': ONE_MINUTE},
            {'kind': 'recurring', 'repeatSave': 'wisdom', 'repeatSaveOnDamage': True, 'duration': ONE_MINUTE},
        ],
    },
    'heroism': {
        'level': 1, 'school': 'enchantment', 'classes': ['bard', 'paladin'], 'concentration': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'modifier', 'modifiers': {'tempHpPerTurn': 0, 'conditionImmunities': ['frightened']},
                     'castingModifierKeys': ['tempHpPerTurn'], 'duration': ONE_MINUTE}],
        'scaling': {'upcast': {'targets': 1}},
    },
    'divine favor': {
        'level': 1, 'school': 'evocation', 'classes': ['paladin'],
        'castingTime': 'bonus_action', 'concentration': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'self', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'modifier', 'modifiers': {'bonusWeaponDamage': {'dice': '1d4', 'type': 'radiant'}},
                     'duration': ONE_MINUTE}],
    },
    'grease': {
        'level': 1, 'school': 'conjuration', 'classes': ['artificer'],
        'delivery': {'type': 'save', 'ability': 'dexterity', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_60_FT, 'area': {'maxTargets': 3}},
        'effects': [{'kind': 'condition', 'condition': 'prone'}],
    },
    'scorching ray': {
        'level': 2, 'school': 'evocation', 'classes': ['wizard', 'sorcerer'],
        'delivery': {'type': 'attack', 'attacks': 3},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'maxTargets': 3},
        'effects': [{'kind': 'damage', 'dice': '2d6', 'damageType': 'fire'}],
        'scaling': {'upcast': {'attacks': 1}},
    },
    'shatter': {
        'level': 2, 'school': 'evocation', 'classes': ['wizard', 'sorcerer', 'warlock', 'bard'],
        'delivery': {'type': 'save', 'ability': 'constitution', 'onSuccess': 'half'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_60_FT, 'area': {'maxTargets': 4}},
        'effects': [{'kind': 'damage', 'dice': '3d8', 'damageType': 'thunder'}],
        'scaling': {'upcast': {'dice': '1d8'}},
    },
    'hold person': {
        'level': 2, 'school': 'enchantment',
        'classes': ['wizard', 'sorcerer', 'warlock', 'cleric', 'druid', 'bard'], 'concentration': True,
        'delivery': {'type': 'save', 'ability': 'wisdom', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_60_FT, 'maxTargets': 1},
        'effects': [
            {'kind': 'condition', 'condition': 'paralyzed', 'duration': ONE_MINUTE},
            {'kind': 'recurring', 'repeatSave': 'wisdom', 'duration': ONE_MINUTE},
        ],
        'scaling': {'upcast': {'targets': 1}},
    },
    'misty step': {
        'level': 2, 'school': 'conjuration', 'classes': ['wizard', 'sorcerer', 'warlock'], 'castingTime': 'bonus_action',
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'self', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'move', 'teleport': 'away'}],
    },
    'invisibility': {
        'level': 2, 'school': 'illusion', 'classes': ['wizard', 'sorcerer', 'warlock', 'bard', 'artificer'],
        'outOfCombat': True, 'concentration': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'condition', 'condition': 'invisible', 'duration': UNTIL_ENCOUNTER_ENDS,
                     'consumeOn': 'attack', 'endsOnCast': True}],
        'scaling': {'upcast': {'targets': 1}},
    },
    'web': {
        'level': 2, 'school': 'conjuration', 'classes': ['wizard', 'sorcerer'], 'concentration': True,
        'delivery': {'type': 'save', 'ability': 'dexterity', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_60_FT, 'area': {'maxTargets': 4}},
        'effects': [
            {'kind': 'condition', 'condition': 'restrained', 'duration': ONE_MINUTE},
            {'kind': 'recurring', 'repeatSave': 'strength', 'duration': ONE_MINUTE},
        ],
    },
    'blindness/deafness': {
        'level': 2, 'school': 'necromancy', 'classes': ['wizard', 'sorcerer', 'cleric', 'bard'],
        'delivery': {'type': 'save', 'ability': 'constitution', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_30_FT, 'maxTargets': 1},
        'effects': [
            {'kind': 'condition', 'condition': 'blinded', 'duration': ONE_MINUTE},
            {'kind': 'recurring', 'repeatSave': 'constitution', 'duration': ONE_MINUTE},
        ],
        'scaling': {'upcast': {'targets': 1}},
    },
    'aid': {
        'level': 2, 'school': 'abjuration', 'classes': ['cleric', 'paladin', 'artificer'], 'outOfCombat': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': RANGE_30_FT, 'maxTargets': 3, 'allowSelf': True},
        'effects': [{'kind': 'max_hp_bonus', 'amount': 5, 'duration': UNTIL_LONG_REST}],
        'scaling': {'upcast': {'flat': 5}},
    },
    'lesser restoration': {
        'level': 2, 'school': 'abjuration', 'classes': ['cleric', 'druid', 'bard', 'paladin', 'ranger', 'artificer'],
        'outOfCombat': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'remove_condition', 'conditions': ['blinded', 'deafened', 'paralyzed', 'poisoned']}],
    },
    'prayer of healing': {
        'level': 2, 'school': 'evocation', 'classes': ['cleric'], 'outOfCombat': True, 'outOfCombatOnly': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': RANGE_30_FT, 'maxTargets': 6, 'allowSelf': True},
        'effects': [{'kind': 'healing', 'dice': '2d8', 'addCastingModifier': True}],
        'scaling': {'upcast': {'dice': '1d8'}},
    },
    'spiritual weapon': {
        'level': 2, 'school': 'evocation', 'classes': ['cleric'], 'castingTime': 'bonus_action',
        'delivery': {'type': 'attack', 'ranged': False},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_60_FT, 'maxTargets': 1},
        'effects': [
            {'kind': 'damage', 'dice': '1d8', 'damageType': 'force', 'addCastingModifier': True},
            {'kind': 'repeat_attack', 'duration': ONE_MINUTE},
        ],
        'scaling': {'upcast': {'dice': '1d8', 'perLevels': 2}},
    },
    'moonbeam': {
        'level': 2, 'school': 'evocation', 'classes': ['druid'], 'concentration': True,
        'delivery': {'type': 'save', 'ability': 'constitution', 'onSuccess': 'half'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'area': {'maxTargets': 3}},
        'effects': [
            {'kind': 'damage', 'dice': '2d10', 'damageType': 'radiant'},
            {'kind': 'recurring', 'save': {'ability': 'constitution', 'onSuccess': 'half'},
             'damage': {'dice': '2d10', 'damageType': 'radiant'}, 'onSuccessfulSave': 'full', 'duration': ONE_MINUTE},
        ],
        'scaling': {'upcast': {'dice': '1d10'}},
    },
    'barkskin': {
        'level': 2, 'school': 'transmutation', 'classes': ['druid', 'ranger'], 'outOfCombat': True, 'concentration': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'modifier', 'modifiers': {'acMinimum': 16}, 'duration': UNTIL_ENCOUNTER_ENDS}],
    },
    'fireball': {
        'level': 3, 'school': 'evocation', 'classes': ['wizard', 'sorcerer'],
        'delivery': {'type': 'save', 'ability': 'dexterity', 'onSuccess': 'half'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'area': {'maxTargets': 6}},
        'effects': [{'kind': 'damage', 'dice': '8d6', 'damageType': 'fire'}],
        'scaling': {'upcast': {'dice': '1d6'}},
    },
    'lightning bolt': {
        'level': 3, 'school': 'evocation', 'classes': ['wizard', 'sorcerer'],
        'delivery': {'type': 'save', 'ability': 'dexterity', 'onSuccess': 'half'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'area': {'maxTargets': 3}},
        'effects': [{'kind': 'damage', 'dice': '8d6', 'damageType': 'lightning'}],
        'scaling': {'upcast': {'dice': '1d6'}},
    },
    'hypnotic pattern': {
        'level': 3, 'school': 'illusion', 'classes': ['wizard', 'sorcerer', 'warlock', 'bard'], 'concentration': True,
        'delivery': {'type': 'save', 'ability': 'wisdom', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_90_FT, 'area': {'maxTargets': 4}},
        'effects': [
            {'kind': 'condition', 'condition': 'incapacitated', 'duration': ONE_MINUTE, 'endsOnDamage': True},
            {'kind': 'condition', 'condition': 'charmed', 'duration': ONE_MINUTE, 'endsOnDamage': True},
        ],
    },
    'fear': {
        'level': 3, 'school': 'illusion', 'classes': ['warlock', 'bard'], 'concentration': True,
        'delivery': {'type': 'save', 'ability': 'wisdom', 'onSuccess': 'none'},
        'target': {'relation': 'enemy', 'rangeBands': RANGE_30_FT, 'area': {'maxTargets': 4}},
        'effects': [
            {'kind': 'condition', 'condition': 'frightened', 'duration': ONE_MINUTE},
            {'kind': 'recurring', 'repeatSave': 'wisdom', 'duration': ONE_MINUTE},
        ],
    },
    'revivify': {
        'level': 3, 'school': 'necromancy', 'classes': ['cleric', 'paladin'], 'outOfCombat': True,
        'consumes': {'gp': 300},
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': TOUCH, 'maxTargets': 1, 'requiresDead': True},
        'effects': [{'kind': 'revive', 'hp': 1}],
    },
    'mass healing word': {
        'level': 3, 'school': 'evocation', 'classes': ['cleric', 'bard'], 'castingTime': 'bonus_action', 'outOfCombat': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': RANGE_60_FT, 'maxTargets': 6, 'allowSelf': True},
        'effects': [{'kind': 'healing', 'dice': '1d4', 'addCastingModifier': True}],
        'scaling': {'upcast': {'dice': '1d4'}},
    },
    'spirit guardians': {
        'level': 3, 'school': 'conjuration', 'classes': ['cleric'], 'concentration': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'self', 'rangeBands': TOUCH, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'recurring', 'save': {'ability': 'wisdom', 'onSuccess': 'half'},
                     'damage': {'dice': '3d8', 'damageType': 'radiant'},
                     'aura': {'relation': 'enemy', 'rangeBands': RANGE_30_FT}, 'duration': TEN_MINUTES}],
        'scaling': {'upcast': {'dice': '1d8'}},
    },
    'dispel magic': {
        'level': 3, 'school': 'abjuration',
        'classes': ['wizard', 'sorcerer', 'warlock', 'cleric', 'druid', 'bard', 'paladin'], 'outOfCombat': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'any', 'rangeBands': RANGE_90_FT, 'maxTargets': 1, 'allowSelf': True},
        'effects': [{'kind': 'dispel'}],
    },
}


# Spells on the class lists that the Dungeon Master adjudicates: no mechanical
# state, and only levelled ones spend a slot.
NARRATED_SPELLS: dict[str, dict[str, Any]] = {
    'mage hand': {'level': 0, 'school': 'conjuration', 'classes': ['wizard', 'sorcerer', 'warlock', 'bard', 'artificer']},
    'prestidigitation': {'level': 0, 'school': 'transmutation', 'classes': ['wizard']},
    'minor illusion': {'level': 0, 'school': 'illusion', 'classes': ['sorcerer', 'warlock', 'bard']},
    'light': {'level': 0, 'school': 'evocation', 'classes': ['wizard', 'sorcerer', 'cleric', 'bard']},
    'thaumaturgy': {'level': 0, 'school': 'transmutation', 'classes': ['cleric']},
    'druidcraft': {'level': 0, 'school': 'transmutation', 'classes': ['druid']},
    'mending': {'level': 0, 'school': 'transmutation', 'classes': ['artificer']},
    'message': {'level': 0, 'school': 'transmutation', 'classes': ['wizard', 'sorcerer', 'bard']},
    'dancing lights': {'level': 0, 'school': 'evocation', 'classes': ['wizard', 'sorcerer', 'bard']},
    'detect magic': {'level': 1, 'school': 'divination', 'classes': ['wizard', 'cleric']},
    'speak with animals': {'level': 1, 'school': 'divination', 'classes': ['druid', 'ranger']},
    'goodberry': {'level': 1, 'school': 'transmutation', 'classes': ['druid', 'ranger']},
    'hellish rebuke': {'level': 1, 'school': 'evocation', 'classes': ['warlock']},
    'chaos bolt': {'level': 1, 'school': 'evocation', 'classes': ['sorcerer']},
    'primal shift': {'level': 1, 'school': 'transmutation', 'classes': ['druid']},
    'arcane tinkering': {'level': 1, 'school': 'transmutation', 'classes': ['artificer']},
    'pass without trace': {'level': 2, 'school': 'abjuration', 'classes': ['druid', 'ranger']},
    'mirror image': {'level': 2, 'school': 'illusion', 'classes': ['warlock']},
    'flaming sphere': {'level': 2, 'school': 'conjuration', 'classes': ['wizard', 'druid']},
    'heat metal': {'level': 2, 'school': 'transmutation', 'classes': ['artificer']},
    'counterspell': {'level': 3, 'school': 'abjuration', 'classes': ['wizard', 'warlock']},
    'fly': {'level': 3, 'school': 'transmutation', 'classes': ['wizard', 'sorcerer', 'warlock']},
    'haste': {'level': 3, 'school': 'transmutation', 'classes': ['sorcerer', 'artificer']},
    'call lightning': {'level': 3, 'school': 'conjuration', 'classes': ['druid']},
}


SPELL_DESCRIPTIONS: dict[str, str] = {
    'fire bolt': 'A precise ranged spark of arcane fire.',
    'ray of frost': 'A chilling ray that slows a target.',
    'shocking grasp': 'A touch of lightning prevents reactions.',
    'chill touch': 'Necrotic magic prevents healing briefly.',
    'poison spray': 'A short-range poisonous spray.',
    'sacred flame': 'Radiant fire against a visible foe.',
    'toll the dead': 'A deathly bell hurts wounded foes more.',
    'eldritch blast': 'Beams of pact-born force.',
    'vicious mockery': 'Cutting words backed by enchantment.',
    'produce flame': 'Hold and throw a small flame.',
    'thorn whip': 'A thorny lash pulls an enemy closer.',
    'guidance': 'A small divine boost to an ability check.',
    'spare the dying': 'Stabilize a dying ally.',
    'magic missile': 'Reliable darts of arcane force.',
    'burning hands': 'A short cone of flame.',
    'thunderwave': 'A concussive burst of sound.',
    'chromatic orb': 'A sphere of the elemental energy you choose.',
    'sleep': 'Lull weak creatures into magical sleep.',
    'mage armor': 'Protective arcane armor until your next long rest.',
    'false life': 'Gain temporary hit points.',
    'shield': 'A reflexive ward that turns a hit into a miss.',
    'cure wounds': 'Touch-based healing.',
    'healing word': 'Quick ranged healing.',
    'bless': 'Bolster allies in danger.',
    'bane': 'Sap the attacks and saves of enemies.',
    'shield of faith': 'Protect an ally with divine force.',
    'guiding bolt': 'Radiant magic reveals a target for the next attack.',
    'inflict wounds': 'Inflict necrotic harm with a touch.',
    'command': 'Compel an enemy to lose its next turn.',
    'entangle': 'Grasping plants restrain an area.',
    'faerie fire': 'Outline creatures in revealing light.',
    "hunter's mark": 'Mark prey so your weapon strikes bite harder.',
    'hex': 'Curse a foe so your attacks deal extra necrotic damage.',
    'armor of agathys': 'A frosty ward of temporary hit points.',
    'charm person': 'Briefly charm a foe.',
    'dissonant whispers': 'Psychic fear through sound.',
    "tasha's hideous laughter": 'A foe collapses in helpless laughter.',
    'heroism': 'Courage that grants temporary hit points and banishes fear.',
    'divine favor': 'Imbue weapon attacks with radiant power.',
    'grease': 'Make a slick hazardous surface.',
    'scorching ray': 'Fire several rays of flame.',
    'shatter': 'A burst of destructive sound.',
    'hold person': 'Paralyze a foe with magic.',
    'misty step': 'Teleport a short distance you can see.',
    'invisibility': 'Hide a creature from sight.',
    'web': 'Fill an area with sticky strands.',
    'blindness/deafness': 'Rob a foe of its sight.',
    'aid': 'Bolster allies with extra hit points until a long rest.',
    'lesser restoration': 'End a lesser affliction.',
    'prayer of healing': 'Heal allies during a safe pause.',
    'spiritual weapon': 'Manifest a floating divine weapon that keeps striking.',
    'moonbeam': 'Call down a radiant column.',
    'barkskin': 'Toughen a creature\'s skin like bark.',
    'fireball': 'A large explosive blast of fire.',
    'lightning bolt': 'A line of lightning through several foes.',
    'hypnotic pattern': 'Mesmerize creatures with light.',
    'fear': 'A phantasm drives foes to terror.',
    'revivify': 'Restore a recently dead ally.',
    'mass healing word': 'Heal several allies with a word.',
    'spirit guardians': 'Call protective spirits around you.',
    'dispel magic': 'End magical effects on a creature.',
    'mage hand': 'Move or manipulate a small object at a distance.',
    'prestidigitation': 'Create small harmless sensory magical effects.',
    'minor illusion': 'Create a small image or sound.',
    'light': 'Make an object shed bright light.',
    'thaumaturgy': 'Small divine signs and omens.',
    'druidcraft': 'Small nature signs and harmless natural magic.',
    'mending': 'Repair a small break or tear.',
    'message': 'Whisper a message to a distant creature.',
    'dancing lights': 'Create floating lights.',
    'detect magic': 'Sense nearby magic.',
    'speak with animals': 'Communicate with beasts.',
    'goodberry': 'Create nourishing healing berries.',
    'hellish rebuke': 'Retaliatory infernal fire.',
    'chaos bolt': 'Unstable elemental damage.',
    'primal shift': 'Brief shapeshifting or bestial adaptation when the story supports it.',
    'arcane tinkering': 'Imbue a tiny object with a minor magical effect.',
    'pass without trace': 'Hide the group in nature magic.',
    'mirror image': 'Create illusory duplicates.',
    'flaming sphere': 'Roll a sphere of fire.',
    'heat metal': 'Overheat worked metal.',
    'counterspell': 'Interrupt another spell.',
    'fly': 'Grant a creature flight.',
    'haste': 'Supercharge a willing creature.',
    'call lightning': 'Command repeated lightning strikes.',
}


def authoritative_spell_effect(name: Any) -> dict[str, Any] | None:
    effect = AUTHORITATIVE_SPELL_EFFECTS.get(_normalize_text(name).casefold())
    return deepcopy(effect) if isinstance(effect, dict) else None


def _duration_label(duration: Any) -> str:
    if not isinstance(duration, dict):
        return ''
    if duration.get('until') == 'long_rest':
        return 'until a long rest'
    if duration.get('until') == 'encounter':
        return 'until the encounter ends'
    rounds = int(duration.get('rounds') or 0)
    if rounds >= 100:
        return '10 minutes'
    if rounds >= 10:
        return '1 minute'
    return ''


def _modifier_summary(effect: dict[str, Any]) -> list[str]:
    modifiers = effect.get('modifiers') if isinstance(effect.get('modifiers'), dict) else {}
    parts = []
    if modifiers.get('cannotReact'):
        parts.append('prevents reactions')
    if modifiers.get('cannotAct'):
        parts.append('prevents the target acting on its next turn')
    if modifiers.get('noHealing'):
        parts.append('prevents healing')
    if modifiers.get('attackMode'):
        parts.append(f"{modifiers['attackMode']} on next attack")
    if modifiers.get('attackedMode'):
        timing = 'next attack' if effect.get('consumeOn') == 'attacked' else 'attacks'
        parts.append(f"{modifiers['attackedMode']} on {timing} against target")
    if modifiers.get('attackBonusDice') or modifiers.get('saveBonusDice'):
        parts.append(f"{modifiers.get('attackBonusDice', '')} to attacks and saves")
    if modifiers.get('checkBonusDice'):
        parts.append(f"+{modifiers['checkBonusDice']} to one ability check")
    if modifiers.get('acBonus'):
        parts.append(f"+{modifiers['acBonus']} armor class")
    if modifiers.get('acBase'):
        parts.append(f"armor class {modifiers['acBase']} + DEX without armour")
    if modifiers.get('acMinimum'):
        parts.append(f"armor class at least {modifiers['acMinimum']}")
    if 'tempHpPerTurn' in modifiers:
        parts.append('temporary HP each turn')
    for condition in modifiers.get('conditionImmunities') or []:
        parts.append(f'immune to {condition}')
    if isinstance(modifiers.get('bonusDamageVs'), dict):
        bonus = modifiers['bonusDamageVs']
        damage = ' '.join(value for value in (bonus.get('dice'), bonus.get('type')) if value)
        scope = 'weapon hits' if bonus.get('weaponOnly') else 'hits'
        parts.append(f'+{damage} damage on your {scope} against the target')
    if isinstance(modifiers.get('bonusWeaponDamage'), dict):
        bonus = modifiers['bonusWeaponDamage']
        parts.append(f"{bonus.get('dice', bonus.get('flat', ''))} {bonus.get('type', '')} weapon damage".strip())
    return parts


def _attack_label(delivery: dict[str, Any], target: dict[str, Any]) -> str:
    ranged = delivery.get('ranged')
    if not isinstance(ranged, bool):
        ranged = list(target.get('rangeBands') or []) != ['melee']
    return 'Ranged spell attack' if ranged else 'Melee spell attack'


def spell_effect_summary(definition: dict[str, Any]) -> str:
    """A one-line player-facing description derived from the authoritative data."""

    delivery = definition.get('delivery') if isinstance(definition.get('delivery'), dict) else {}
    target = definition.get('target') if isinstance(definition.get('target'), dict) else {}
    delivery_type = _normalize_text(delivery.get('type') or 'automatic').lower()
    if delivery_type == 'save':
        label = f"{_normalize_text(delivery.get('ability'))[:3].upper()} save"
        if delivery.get('onSuccess') == 'half':
            label += ', half on success'
    elif delivery_type == 'attack':
        label = _attack_label(delivery, target)
    else:
        label = 'Automatic reaction when an attack would hit you' if definition.get('castingTime') == 'reaction' else 'Automatic'
    parts = [label]
    darts = delivery.get('darts') if isinstance(delivery.get('darts'), dict) else {}
    durations: list[str] = []
    for effect in definition.get('effects') or []:
        if not isinstance(effect, dict):
            continue
        kind = _normalize_text(effect.get('kind')).lower()
        dice = _normalize_text(effect.get('dice') or effect.get('amount'))
        if kind == 'damage':
            damage_type = '/'.join(effect['damageTypeChoices']) if effect.get('damageTypeChoices') else _normalize_text(effect.get('damageType'))
            damage = ' '.join(value for value in (dice, damage_type) if value)
            if effect.get('addCastingModifier'):
                damage = f'{dice} + casting modifier {damage_type}'
            if effect.get('woundedDice'):
                damage += f" ({_normalize_text(effect['woundedDice'])} against a wounded target)"
            attacks = delivery.get('attacks')
            parts.append(f"{darts['count']} darts of {damage}" if darts.get('count') else
                         f"{attacks} {'attack' if attacks == 1 else 'attacks'} of {damage}" if attacks else damage)
        elif kind == 'healing':
            parts.append(f"{dice}{' + casting modifier' if effect.get('addCastingModifier') else ''} healing")
        elif kind == 'temporary_hp':
            parts.append(f'{dice} temporary HP')
        elif kind == 'max_hp_bonus':
            parts.append(f'+{dice} maximum and current HP')
        elif kind == 'sleep_pool':
            parts.append(f'{dice} hit points of creatures fall asleep, weakest first')
        elif kind == 'revive':
            parts.append(f"returns a dead ally to life with {int(effect.get('hp') or 1)} HP")
        elif kind == 'dispel':
            parts.append('ends spell effects of the cast level or lower')
        elif kind == 'repeat_attack':
            parts.append('attack again as a bonus action while it lasts')
        elif kind == 'move':
            if effect.get('teleport') == 'away':
                parts.append('teleport to far range')
            elif effect.get('bands') == 1:
                parts.append('push one range band')
            elif effect.get('bands') == -1:
                parts.append('pull one range band')
        elif kind == 'stabilize':
            parts.append('stabilizes a dying ally')
        elif kind == 'remove_condition':
            conditions = ', '.join(_normalize_text(value) for value in effect.get('conditions') or [])
            parts.append(f'removes {conditions}' if effect.get('removeAll') else f'removes one of: {conditions}')
        elif kind == 'condition':
            parts.append(_normalize_text(effect.get('condition')).replace('_', ' '))
        elif kind == 'modifier':
            parts.extend(_modifier_summary(effect))
        elif kind == 'recurring':
            if effect.get('aura'):
                save = effect.get('save') if isinstance(effect.get('save'), dict) else {}
                damage = effect.get('damage') if isinstance(effect.get('damage'), dict) else {}
                parts.append(
                    f"enemies near you take {damage.get('dice')} {damage.get('damageType')} each turn "
                    f"({_normalize_text(save.get('ability'))[:3].upper()} save for half)"
                )
            elif effect.get('repeatSave'):
                parts.append(f"repeat {_normalize_text(effect['repeatSave'])[:3].upper()} save each turn to end it")
            else:
                parts.append('the save and damage repeat each turn')
        duration = _duration_label(effect.get('duration'))
        if duration and duration not in durations:
            durations.append(duration)
    area = target.get('area') if isinstance(target.get('area'), dict) else {}
    if area.get('maxTargets'):
        parts.append(f"area: up to {int(area['maxTargets'])} enemies")
    parts.extend(durations)
    if definition.get('concentration') is True:
        parts.append('concentration')
    consumes = definition.get('consumes') if isinstance(definition.get('consumes'), dict) else {}
    if consumes.get('gp'):
        parts.append(f"consumes {int(consumes['gp'])} gp")
    scaling = definition.get('scaling') if isinstance(definition.get('scaling'), dict) else {}
    if scaling.get('cantrip') is True and any(
        isinstance(effect, dict) and effect.get('kind') == 'damage' for effect in definition.get('effects') or []
    ):
        parts.append('scales with level')
    return ' · '.join(part for part in parts if part)


def _apply_spell_metadata(payload: dict[str, Any], authoritative_effect: dict[str, Any] | None) -> None:
    if authoritative_effect:
        payload.update(authoritative_effect)
        payload.update({'rulesBacked': True, 'authoritativeEffect': True, 'narrated': False})
        payload['effectSummary'] = spell_effect_summary(authoritative_effect)
    else:
        payload.update({'rulesBacked': False, 'authoritativeEffect': False, 'narrated': True})
        payload['effectSummary'] = _normalize_text(payload.get('description')) or 'Narrated by the Dungeon Master.'
        narrated = NARRATED_SPELLS.get(_normalize_text(payload.get('name')).casefold())
        if narrated:
            payload.setdefault('school', narrated['school'])
    payload.setdefault('castingTime', 'action')
    payload.setdefault('concentration', False)
    payload.setdefault('outOfCombat', False)


def spell_payload(
    name: str,
    *,
    level: int = 0,
    source_type: str | None = None,
    source_detail: str | None = None,
    description: str | None = None,
    learned_at_level: int | None = None,
    learned_from: str | None = None,
    tags: Iterable[str] | None = None,
    tradition: str | None = None,
    catalog: str | None = None,
) -> dict[str, Any]:
    spell_name = _normalize_text(name)
    payload: dict[str, Any] = {
        'id': _spell_id(spell_name),
        'name': spell_name,
        'level': _bounded_spell_level(level),
    }
    if source_type:
        payload['sourceType'] = _normalize_text(source_type).lower()
    if source_detail:
        payload['sourceDetail'] = _normalize_text(source_detail)
    source = _source_label(source_type, source_detail)
    if source:
        payload['source'] = source
        payload['sources'] = [source]
    if description:
        payload['description'] = _normalize_text(description)
    tag_list = [_normalize_text(tag).lower() for tag in (tags or []) if _normalize_text(tag)]
    if tag_list:
        payload['tags'] = list(dict.fromkeys(tag_list))
    if tradition:
        payload['tradition'] = _normalize_text(tradition).lower()
    if catalog:
        payload['catalog'] = _normalize_text(catalog).lower()
    learned_level = _bounded_character_level(learned_at_level)
    if learned_level:
        payload['learnedAtLevel'] = learned_level
    if learned_from:
        payload['learnedFrom'] = _normalize_text(learned_from)
    authoritative_effect = authoritative_spell_effect(spell_name)
    _apply_spell_metadata(payload, authoritative_effect)
    return payload


SpellSpec = tuple[str, int, str]


OriginalTheme = tuple[str, str, tuple[str, ...], str]
OriginalForm = tuple[str, int, tuple[str, ...], str]


ORIGINAL_MAGIC_THEMES: tuple[OriginalTheme, ...] = (
    ('amber', 'Amber', ('arcane', 'time', 'ward'), 'fossilized sunlight and preserved moments'),
    ('ash', 'Ash', ('fire', 'death', 'shadow'), 'cold ash, memory of flame, and soft ruin'),
    ('aurora', 'Aurora', ('light', 'cosmic', 'illusion'), 'curtains of many-colored skyfire'),
    ('basalt', 'Basalt', ('earth', 'ward', 'craft'), 'volcanic stone and patient pressure'),
    ('beacon', 'Beacon', ('light', 'holy', 'guidance'), 'guiding light that refuses to be hidden'),
    ('briar', 'Briar', ('nature', 'thorn', 'ranger'), 'living thorns, hooked vines, and wild borders'),
    ('brine', 'Brine', ('water', 'storm', 'travel'), 'saltwater, tide-pull, and old drowned roads'),
    ('candle', 'Candle', ('light', 'spirit', 'ward'), 'small flames that hold back worse dark'),
    ('carnival', 'Carnival', ('fey', 'illusion', 'emotion'), 'bright masks, impossible music, and fey bargains'),
    ('cedar', 'Cedar', ('nature', 'healing', 'spirit'), 'evergreen breath, resin, and sheltering woods'),
    ('clockwork', 'Clockwork', ('craft', 'time', 'metal'), 'gears, measured time, and obedient mechanisms'),
    ('cobalt', 'Cobalt', ('storm', 'metal', 'arcane'), 'blue sparks, charged metal, and clean impact'),
    ('comet', 'Comet', ('cosmic', 'fire', 'travel'), 'falling stars and long burning omens'),
    ('copper', 'Copper', ('metal', 'lightning', 'craft'), 'conductive metal, living current, and quick repairs'),
    ('coral', 'Coral', ('water', 'life', 'ward'), 'reef growth, tide pools, and armored life'),
    ('crow', 'Crow', ('shadow', 'memory', 'spirit'), 'black feathers, stolen words, and watchful omens'),
    ('crystal', 'Crystal', ('arcane', 'earth', 'light'), 'faceted stone, refracted force, and clear resonance'),
    ('dawn', 'Dawn', ('holy', 'light', 'healing'), 'first light, renewal, and broken curses'),
    ('deepbell', 'Deepbell', ('sound', 'water', 'spirit'), 'low bells heard through water and stone'),
    ('dreamglass', 'Dreamglass', ('dream', 'illusion', 'mind'), 'transparent dreams, sleeping mirrors, and soft lies'),
    ('dust', 'Dust', ('earth', 'decay', 'travel'), 'old roads, dry bones, and forgotten footprints'),
    ('echo', 'Echo', ('sound', 'memory', 'bard'), 'returning sound and remembered voices'),
    ('ember', 'Ember', ('fire', 'elemental', 'sorcerer'), 'banked heat, sparks, and stubborn flame'),
    ('feral', 'Feral', ('beast', 'nature', 'shapeshift'), 'claws, scent, hunger, and borrowed instinct'),
    ('frostlace', 'Frostlace', ('cold', 'ward', 'illusion'), 'lace-thin ice, numbing air, and white patterns'),
    ('gilded', 'Gilded', ('light', 'social', 'charm'), 'golden glamour, courtly shine, and tempting promises'),
    ('glass', 'Glass', ('illusion', 'ward', 'arcane'), 'clear barriers, reflections, and brittle edges'),
    ('gravebloom', 'Gravebloom', ('death', 'life', 'spirit'), 'flowers rooted in old graves and unfinished farewells'),
    ('harbor', 'Harbor', ('water', 'ward', 'travel'), 'safe docks, mooring lines, and foghorn calls'),
    ('honey', 'Honey', ('healing', 'nature', 'emotion'), 'sweet preservation, soothing warmth, and patient bees'),
    ('horizon', 'Horizon', ('travel', 'space', 'guidance'), 'far roads, distance, and the line where worlds meet'),
    ('ink', 'Ink', ('shadow', 'knowledge', 'bard'), 'living ink, hidden clauses, and black script'),
    ('iron', 'Iron', ('metal', 'ward', 'oath'), 'cold iron, sworn boundaries, and refusing force'),
    ('ivory', 'Ivory', ('spirit', 'memory', 'holy'), 'white relics, ancestral signs, and quiet vows'),
    ('jade', 'Jade', ('life', 'earth', 'healing'), 'green stone, steady breath, and restored balance'),
    ('lantern', 'Lantern', ('light', 'travel', 'spirit'), 'carried light, safe passage, and revealed faces'),
    ('lilac', 'Lilac', ('emotion', 'dream', 'healing'), 'gentle fragrance, nostalgia, and softened grief'),
    ('lodestone', 'Lodestone', ('metal', 'force', 'travel'), 'magnetic pull, heavy direction, and anchored paths'),
    ('loom', 'Loom', ('fate', 'craft', 'bard'), 'threads, knots, and choices woven into pattern'),
    ('marble', 'Marble', ('earth', 'holy', 'ward'), 'polished stone, temple silence, and enduring shape'),
    ('mire', 'Mire', ('poison', 'earth', 'nature'), 'mud, bog gas, rot, and hungry ground'),
    ('mirror', 'Mirror', ('illusion', 'mind', 'space'), 'reflections, doubles, and reversed angles'),
    ('moon', 'Moon', ('moon', 'dream', 'shapeshift'), 'silver light, tides, and changing forms'),
    ('mycelium', 'Mycelium', ('nature', 'death', 'mind'), 'fungal threads, buried messages, and shared decay'),
    ('obsidian', 'Obsidian', ('shadow', 'fire', 'ward'), 'black glass, volcanic edges, and sealed heat'),
    ('orchid', 'Orchid', ('nature', 'charm', 'poison'), 'lush petals, alluring scent, and hidden venom'),
    ('paper', 'Paper', ('knowledge', 'craft', 'arcane'), 'folded pages, written orders, and fragile maps'),
    ('pearl', 'Pearl', ('water', 'healing', 'light'), 'soft luster, patient oceans, and protected wounds'),
    ('phantom', 'Phantom', ('spirit', 'illusion', 'shadow'), 'half-seen bodies, chills, and unfinished presence'),
    ('quartz', 'Quartz', ('earth', 'sound', 'arcane'), 'ringing crystal, vibration, and stored pressure'),
    ('quill', 'Quill', ('knowledge', 'bard', 'fate'), 'flying script, edits, and decisive signatures'),
    ('rain', 'Rain', ('water', 'storm', 'healing'), 'falling water, washed tracks, and softened ground'),
    ('ravenous', 'Ravenous', ('beast', 'death', 'warlock'), 'hunger, teeth, and consuming dark'),
    ('redwood', 'Redwood', ('nature', 'ward', 'life'), 'ancient trees, height, and deep roots'),
    ('riddle', 'Riddle', ('mind', 'illusion', 'knowledge'), 'unsolved questions, hidden doors, and clever traps'),
    ('river', 'River', ('water', 'travel', 'fate'), 'currents, crossings, and inevitable movement'),
    ('rose', 'Rose', ('emotion', 'thorn', 'healing'), 'petals, devotion, thorns, and costly mercy'),
    ('rust', 'Rust', ('metal', 'decay', 'artificer'), 'oxidized iron, failing locks, and red dust'),
    ('saffron', 'Saffron', ('fire', 'healing', 'social'), 'warm spice, golden smoke, and restored appetite'),
    ('sapphire', 'Sapphire', ('arcane', 'water', 'mind'), 'blue clarity, still water, and precise thought'),
    ('scarab', 'Scarab', ('earth', 'life', 'death'), 'carapaces, burial charms, and returning life'),
    ('selenite', 'Selenite', ('moon', 'ward', 'spirit'), 'pale crystal, lunar calm, and quiet protection'),
    ('serpent', 'Serpent', ('poison', 'charm', 'shapeshift'), 'coils, venom, hypnotic motion, and shed skin'),
    ('shale', 'Shale', ('earth', 'sound', 'decay'), 'layered stone, brittle records, and cracking plates'),
    ('silk', 'Silk', ('illusion', 'fey', 'craft'), 'fine threads, soft bindings, and impossible cloth'),
    ('silver', 'Silver', ('moon', 'holy', 'ward'), 'moonlit metal, purity, and clean cuts through curses'),
    ('smoke', 'Smoke', ('fire', 'shadow', 'travel'), 'vanishing trails, choking haze, and hidden exits'),
    ('song', 'Song', ('sound', 'emotion', 'bard'), 'melody, courage, grief, and shared rhythm'),
    ('spindle', 'Spindle', ('fate', 'time', 'craft'), 'turning tools, measured thread, and delayed outcomes'),
    ('starling', 'Starling', ('beast', 'sound', 'travel'), 'flocking birds, mimicry, and sudden turns'),
    ('storm', 'Storm', ('storm', 'lightning', 'air'), 'thunderheads, pressure, and wild current'),
    ('sunspot', 'Sunspot', ('light', 'fire', 'holy'), 'burning halos, exposed truth, and fierce daylight'),
    ('thorn', 'Thorn', ('thorn', 'nature', 'pain'), 'barbs, brambles, and boundaries that bite'),
    ('thunderhead', 'Thunderhead', ('storm', 'sound', 'force'), 'black clouds, rolling sound, and concussive air'),
    ('tidepool', 'Tidepool', ('water', 'life', 'illusion'), 'small seas, reflected skies, and hidden creatures'),
    ('topaz', 'Topaz', ('lightning', 'light', 'arcane'), 'yellow crystal, bright charge, and sudden insight'),
    ('verdigris', 'Verdigris', ('metal', 'poison', 'time'), 'green patina, age, and beautiful corrosion'),
    ('violet', 'Violet', ('mind', 'dream', 'emotion'), 'purple haze, delicate thought, and shared feeling'),
    ('void', 'Void', ('shadow', 'space', 'warlock'), 'starless gaps, silence, and impossible absence'),
    ('wheat', 'Wheat', ('life', 'healing', 'nature'), 'grain, harvest, common meals, and survival'),
    ('whisper', 'Whisper', ('sound', 'shadow', 'mind'), 'low voices, secrets, and carried breath'),
    ('wildglass', 'Wildglass', ('chaos', 'illusion', 'sorcerer'), 'unstable reflections and beautiful accidents'),
    ('windmill', 'Windmill', ('air', 'craft', 'travel'), 'turning blades, grain dust, and useful wind'),
    ('winter', 'Winter', ('cold', 'death', 'ward'), 'still snow, preserved silence, and hard survival'),
    ('wyrm', 'Wyrm', ('dragon', 'fire', 'oath'), 'draconic breath, old pride, and scaled power'),
    ('yew', 'Yew', ('death', 'nature', 'spirit'), 'grave trees, bows, poison, and ancestral shade'),
    ('zenith', 'Zenith', ('cosmic', 'light', 'oath'), 'high noon, perfect angles, and exposed purpose'),
)


ORIGINAL_MAGIC_FORMS: tuple[OriginalForm, ...] = (
    ('Glimmer', 0, ('utility', 'sensory'), 'Create a harmless sign of {motif}.'),
    ('Whisper', 0, ('utility', 'communication'), 'Carry a brief message through {motif}.'),
    ('Trace', 0, ('utility', 'tracking'), 'Mark, reveal, or follow a faint trace through {motif}.'),
    ('Palm', 0, ('utility', 'minor'), 'Hold a tiny useful expression of {motif} in one hand.'),
    ('Knot', 0, ('utility', 'control'), 'Tie a minor magical knot shaped by {motif}.'),
    ('Spark', 0, ('damage', 'minor'), 'Release a small, precise bite of {motif}.'),
    ('Mote', 0, ('utility', 'light'), 'Set a floating mote of {motif} near you.'),
    ('Charm', 1, ('social', 'emotion'), 'Tint a social moment with {motif}.'),
    ('Veil', 1, ('illusion', 'stealth'), 'Drape a short-lived veil of {motif} over a creature or object.'),
    ('Ward', 1, ('defense', 'ward'), 'Raise a quick protective sign of {motif}.'),
    ('Lash', 1, ('damage', 'control'), 'Strike or pull a target with a lash of {motif}.'),
    ('Step', 1, ('movement', 'travel'), 'Move through a short opening created by {motif}.'),
    ('Bloom', 1, ('healing', 'creation'), 'Coax a small restorative bloom of {motif}.'),
    ('Brand', 1, ('mark', 'damage'), 'Mark a target with a lingering sign of {motif}.'),
    ('Lens', 1, ('divination', 'knowledge'), 'Study a scene through a lens of {motif}.'),
    ('Snare', 2, ('control', 'trap'), 'Catch a creature or object in a snare of {motif}.'),
    ('Mirror', 2, ('illusion', 'defense'), 'Create a misleading reflection made from {motif}.'),
    ('Mantle', 2, ('buff', 'defense'), 'Wrap a willing creature in a mantle of {motif}.'),
    ('Tether', 2, ('control', 'movement'), 'Bind two points, creatures, or ideas with {motif}.'),
    ('Door', 2, ('movement', 'space'), 'Open a brief small passage through {motif}.'),
    ('Pulse', 3, ('area', 'damage'), 'Send a wave of {motif} through the immediate area.'),
    ('Bastion', 3, ('defense', 'area'), 'Build a temporary defensive shape out of {motif}.'),
    ('Chorus', 3, ('support', 'sound'), 'Let several allies share a chorus of {motif}.'),
    ('Swarm', 3, ('summon', 'area'), 'Call a restless swarm shaped by {motif}.'),
    ('Script', 3, ('knowledge', 'ritual'), 'Write a short-lived law or instruction in {motif}.'),
    ('Rift', 4, ('space', 'damage'), 'Tear open a dangerous rift filled with {motif}.'),
    ('Crown', 4, ('buff', 'command'), 'Crown a creature with commanding {motif}.'),
    ('Engine', 4, ('craft', 'sustained'), 'Assemble a sustained magical engine powered by {motif}.'),
    ('Eidolon', 5, ('summon', 'spirit'), 'Shape an autonomous eidolon from {motif}.'),
    ('Labyrinth', 5, ('control', 'mind'), 'Trap a target or area in confusing paths of {motif}.'),
    ('Dominion', 6, ('area', 'command'), 'Claim a broad zone under the rules of {motif}.'),
    ('Vessel', 6, ('transformation', 'buff'), 'Turn a willing creature into a vessel for {motif}.'),
    ('Parliament', 7, ('summon', 'social'), 'Convene many voices, shades, or forces of {motif}.'),
    ('Mandate', 7, ('command', 'ritual'), 'Declare a powerful temporary law written in {motif}.'),
    ('Apotheosis', 8, ('transformation', 'mythic'), 'Briefly raise a creature into a mythic shape of {motif}.'),
    ('Horizon', 8, ('space', 'travel'), 'Move a group along a vast boundary of {motif}.'),
    ('Genesis', 9, ('creation', 'mythic'), 'Create a lasting miracle seeded with {motif}.'),
    ('Apocalypse', 9, ('damage', 'mythic'), 'Unleash a scene-changing catastrophe of {motif}.'),
)


ORIGINAL_ARCHETYPE_TAGS: dict[str, set[str]] = {
    'wizard': {'arcane', 'knowledge', 'time', 'space', 'force', 'illusion', 'mind', 'cosmic', 'ward', 'craft'},
    'sorcerer': {'fire', 'storm', 'lightning', 'chaos', 'dragon', 'cosmic', 'light', 'shadow', 'elemental', 'air'},
    'warlock': {'shadow', 'void', 'death', 'spirit', 'dream', 'curse', 'ravenous', 'mind', 'space'},
    'cleric': {'holy', 'healing', 'life', 'spirit', 'ward', 'light', 'death', 'truth', 'mercy'},
    'druid': {'nature', 'beast', 'shapeshift', 'earth', 'water', 'life', 'storm', 'thorn', 'moon', 'plant'},
    'bard': {'sound', 'emotion', 'illusion', 'dream', 'memory', 'fey', 'social', 'fate', 'knowledge'},
    'paladin': {'holy', 'oath', 'ward', 'light', 'sun', 'valor', 'mercy', 'iron', 'truth'},
    'ranger': {'nature', 'beast', 'travel', 'tracking', 'thorn', 'moon', 'earth', 'water', 'storm'},
    'artificer': {'craft', 'metal', 'clockwork', 'rune', 'alchemy', 'lightning', 'ward', 'force', 'knowledge'},
}


# New characters receive this many generated (narrated) spells, once.
ORIGINAL_STARTER_COUNT = 3


ORIGINAL_RACE_TAGS = {
    'aasimar': {'holy', 'light', 'healing', 'spirit'},
    'changeling': {'shapeshift', 'illusion', 'social', 'mirror'},
    'dragonborn': {'dragon', 'fire', 'storm', 'oath'},
    'elf': {'arcane', 'moon', 'dream', 'nature'},
    'fairy': {'fey', 'illusion', 'nature', 'emotion'},
    'firbolg': {'nature', 'spirit', 'beast', 'ward'},
    'genasi': {'fire', 'water', 'storm', 'earth', 'air', 'elemental'},
    'gnome': {'illusion', 'craft', 'knowledge', 'arcane'},
    'satyr': {'fey', 'sound', 'emotion', 'social'},
    'saiyan': {'force', 'storm', 'light', 'cosmic', 'beast'},
    'tiefling': {'fire', 'shadow', 'social', 'oath'},
    'triton': {'water', 'storm', 'travel', 'ward'},
    'yuan-ti': {'poison', 'serpent', 'mind', 'shadow'},
}


SHAPESHIFT_TRAIT_KEYWORDS = (
    'shapechanger',
    'shapeshifter',
    'shapeshift',
    'wild shape',
    'polymorph',
    'alter your appearance',
    'alter appearance',
    'change your shape',
    'change shape',
    'reshape your body',
)

STALE_AUTO_RACE_SOURCE_DETAILS = {'shapeshifter'}
AUTO_CLASS_SPELL_SOURCE_TYPES = frozenset({'class', 'level', 'class_catalog'})
AUTO_RACE_SPELL_SOURCE_TYPES = frozenset({'race', 'race_catalog'})


def _looks_like_shapeshifter_trait(trait_text: str) -> bool:
    return any(keyword in trait_text for keyword in SHAPESHIFT_TRAIT_KEYWORDS)


def _theme_record(theme: OriginalTheme) -> dict[str, Any]:
    key, title, tags, motif = theme
    return {'key': key, 'title': title, 'tags': set(tags), 'motif': motif}


def _form_record(form: OriginalForm) -> dict[str, Any]:
    suffix, level, tags, template = form
    return {'suffix': suffix, 'level': level, 'tags': set(tags), 'template': template}


@lru_cache(maxsize=1)
def original_spell_catalog() -> tuple[dict[str, Any], ...]:
    """Return the generated original AIDM spell catalog.

    These are intentionally non-canon names. Character sheets receive small
    deterministic subsets; the full catalog exists so future UI/DM helpers can
    search a very broad magic space without being limited to tabletop books.
    """

    spells: list[dict[str, Any]] = []
    for raw_theme in ORIGINAL_MAGIC_THEMES:
        theme = _theme_record(raw_theme)
        for raw_form in ORIGINAL_MAGIC_FORMS:
            form = _form_record(raw_form)
            name = f"{theme['title']} {form['suffix']}"
            tags = sorted({*theme['tags'], *form['tags'], 'original', 'aidm'})
            spells.append(
                spell_payload(
                    name,
                    level=int(form['level']),
                    source_type='original_catalog',
                    source_detail=str(theme['key']),
                    description=str(form['template']).format(motif=theme['motif']),
                    tags=tags,
                    tradition='aidm-original',
                    catalog='aidm-original',
                )
            )
    return tuple(spells)


def original_spell_catalog_size() -> int:
    return len(original_spell_catalog())


def _stable_rank(seed: str, spell: dict[str, Any]) -> str:
    source = f"{seed}|{spell.get('id')}|{spell.get('name')}"
    return hashlib.sha1(source.encode('utf-8')).hexdigest()


def _max_spell_level_for_character(character_level: int) -> int:
    level = max(1, min(20, int(character_level or 1)))
    return max(1, min(9, (level + 1) // 2))


def _retag_catalog_spell(spell: dict[str, Any], *, source_type: str, source_detail: str, learned_at_level: int) -> dict[str, Any]:
    payload = deepcopy(spell)
    source_type = _normalize_text(source_type).lower()
    source_detail = _normalize_text(source_detail)
    source = _source_label(source_type, source_detail)
    payload['sourceType'] = source_type
    payload['sourceDetail'] = source_detail
    if source:
        payload['source'] = source
        payload['sources'] = [source]
    payload['learnedAtLevel'] = max(1, min(20, int(learned_at_level)))
    payload['catalog'] = payload.get('catalog') or 'aidm-original'
    return payload


def _catalog_candidates(*, tags: set[str], max_spell_level: int) -> list[dict[str, Any]]:
    max_level = max(0, min(9, int(max_spell_level)))
    candidates = []
    for spell in original_spell_catalog():
        spell_tags = {str(tag or '').strip().lower() for tag in spell.get('tags') or []}
        if int(spell.get('level') or 0) <= max_level and (not tags or spell_tags & tags):
            candidates.append(spell)
    return candidates


def _take_ranked_spells(
    candidates: Iterable[dict[str, Any]],
    *,
    count: int,
    seed: str,
    seen: set[str],
) -> list[dict[str, Any]]:
    ranked = sorted(candidates, key=lambda spell: (_stable_rank(seed, spell), str(spell.get('name') or '')))
    selected: list[dict[str, Any]] = []
    for spell in ranked:
        key = _slug(spell.get('name'))
        if key in seen:
            continue
        selected.append(deepcopy(spell))
        seen.add(key)
        if len(selected) >= count:
            break
    return selected


def original_class_starter_spells(archetype: str) -> list[dict[str, Any]]:
    class_tags = ORIGINAL_ARCHETYPE_TAGS.get(archetype, set())
    if not class_tags:
        return []
    picked = _take_ranked_spells(
        _catalog_candidates(tags=class_tags, max_spell_level=_max_spell_level_for_character(1)),
        count=ORIGINAL_STARTER_COUNT,
        seed=f'{archetype}:1:aidm-original',
        seen=set(),
    )
    return [
        _retag_catalog_spell(
            spell,
            source_type='class_catalog',
            source_detail=f'{archetype}:original:1',
            learned_at_level=1,
        )
        for spell in picked
    ]


def _race_catalog_tags(
    *,
    race_key: str | None,
    tags: set[str],
    trait_text: str,
) -> set[str]:
    result = set(ORIGINAL_RACE_TAGS.get(str(race_key or ''), set()))
    result.update(tag for tag in tags if tag in {'magical', 'fey', 'elemental', 'celestial', 'fiendish', 'draconic', 'aquatic', 'nature'})
    if _looks_like_shapeshifter_trait(trait_text):
        result.update({'shapeshift', 'illusion', 'mirror'})
    if 'breath_weapon' in trait_text or 'elemental_cone_or_line' in trait_text:
        result.update({'dragon', 'fire', 'storm'})
    if 'elemental' in tags or 'elemental' in trait_text:
        result.update({'fire', 'water', 'storm', 'earth', 'air'})
    if 'celestial' in tags or 'minor_healing' in trait_text:
        result.update({'holy', 'light', 'healing'})
    if 'fiendish' in tags or 'infernal' in trait_text:
        result.update({'fire', 'shadow', 'social'})
    if 'aquatic' in tags or 'ocean' in trait_text:
        result.update({'water', 'storm', 'travel'})
    if 'magical' in tags:
        result.update({'arcane', 'illusion', 'ward'})
    return result


def original_race_spells(
    *,
    race_key: str | None,
    race_tags: set[str],
    trait_text: str,
    count: int = 2,
) -> list[dict[str, Any]]:
    catalog_tags = _race_catalog_tags(race_key=race_key, tags=race_tags, trait_text=trait_text)
    if not catalog_tags:
        return []
    candidates = _catalog_candidates(tags=catalog_tags, max_spell_level=1)
    seen: set[str] = set()
    picked = _take_ranked_spells(
        candidates,
        count=max(1, count),
        seed=f'race:{race_key or "custom"}:aidm-original',
        seen=seen,
    )
    return [
        _retag_catalog_spell(
            spell,
            source_type='race_catalog',
            source_detail=str(race_key or 'custom'),
            learned_at_level=1,
        )
        for spell in picked
    ]


SPELLCASTING_ARCHETYPES = ('wizard', 'sorcerer', 'warlock', 'cleric', 'druid', 'bard', 'paladin', 'ranger', 'artificer')
HALF_CASTER_ARCHETYPES = frozenset({'paladin', 'ranger', 'artificer'})


# Spells of level 4 and above stay narrated unlocks at the levels their slots arrive.
CLASS_HIGH_LEVEL_UNLOCKS: dict[str, dict[int, list[SpellSpec]]] = {
    'wizard': {
        7: [('Dimension Door', 4, 'Teleport yourself and one companion farther away.')],
        9: [('Wall of Force', 5, 'Create a nearly unbreakable barrier of force.')],
        11: [('Disintegrate', 6, 'A dangerous beam of destructive force.')],
        13: [('Teleport', 7, 'Travel instantly across great distances.')],
        15: [('Sunburst', 8, 'A brilliant burst of radiant power.')],
        17: [('Wish', 9, 'Reality-bending high magic.')],
    },
    'sorcerer': {
        7: [('Greater Invisibility', 4, 'Turn a creature invisible through battle.')],
        9: [('Telekinesis', 5, 'Move creatures or objects with sustained force.')],
        11: [('Chain Lightning', 6, 'Lightning arcs between multiple targets.')],
        13: [('Teleport', 7, 'Travel instantly across great distances.')],
        15: [('Power Word Stun', 8, 'Stun a weakened creature with a word.')],
        17: [('Meteor Swarm', 9, 'Call down devastating meteors.')],
    },
    'warlock': {
        7: [('Banishment', 4, 'Send a target out of the scene briefly.')],
        9: [('Hold Monster', 5, 'Paralyze a creature with pact magic.')],
        11: [('Circle of Death', 6, 'A wide burst of necrotic power.')],
        13: [('Finger of Death', 7, 'A lethal necromantic attack.')],
        15: [('Maddening Darkness', 8, 'Summon supernatural darkness and psychic pain.')],
        17: [('Foresight', 9, 'Grant uncanny luck and awareness.')],
    },
    'cleric': {
        7: [('Death Ward', 4, 'Protect an ally from dropping to death.')],
        9: [('Greater Restoration', 5, 'End a major affliction.'), ('Flame Strike', 5, 'Call down divine fire.')],
        11: [('Heal', 6, 'Massive restorative magic.')],
        13: [('Resurrection', 7, 'Return a dead creature to life.')],
        15: [('Holy Aura', 8, 'Radiant protection for nearby allies.')],
        17: [('Mass Heal', 9, 'Restore many allies at once.')],
    },
    'druid': {
        7: [('Polymorph', 4, 'Transform a creature into another form.')],
        9: [('Greater Restoration', 5, 'End a major affliction.'), ('Tree Stride', 5, 'Step through trees.')],
        11: [('Transport via Plants', 6, 'Travel through living plants.')],
        13: [('Regenerate', 7, 'Restore severe injuries over time.')],
        15: [('Animal Shapes', 8, 'Transform willing allies into beasts.')],
        17: [('Shapechange', 9, 'Assume powerful magical forms.')],
    },
    'bard': {
        7: [('Greater Invisibility', 4, 'Turn a creature invisible through battle.')],
        9: [('Hold Monster', 5, 'Paralyze a creature.'), ('Synaptic Static', 5, 'A psychic blast that muddles minds.')],
        11: [('Mass Suggestion', 6, 'Influence many creatures at once.')],
        13: [('Teleport', 7, 'Travel instantly across great distances.')],
        15: [('Power Word Stun', 8, 'Stun a weakened creature with a word.')],
        17: [('Power Word Heal', 9, 'Restore an ally with a word.')],
    },
    'paladin': {
        9: [('Death Ward', 4, 'Protect an ally from dropping to death.')],
        13: [('Banishing Smite', 5, 'Strike with banishing force.')],
    },
    'ranger': {
        9: [('Freedom of Movement', 4, 'Ignore many restraints and movement hindrances.')],
        13: [('Swift Quiver', 5, 'Attack rapidly with enchanted ammunition.')],
    },
    'artificer': {
        7: [('Fabricate', 4, 'Convert raw materials into crafted objects.')],
        9: [('Animate Objects', 5, 'Bring objects to life briefly.')],
    },
}


def catalog_unlock_level(archetype: str, spell_level: int) -> int:
    """The character level at which a class list gains catalog spells of one spell level."""

    half_caster = archetype in HALF_CASTER_ARCHETYPES
    if spell_level <= 0:
        return 1
    if spell_level == 1:
        return 2 if archetype in {'paladin', 'ranger'} else 1
    if spell_level == 2:
        return 5 if half_caster else 3
    return 5


def _catalog_class_lists() -> tuple[dict[str, list[SpellSpec]], dict[str, dict[int, list[SpellSpec]]]]:
    """Rebuild the curated class lists from each catalog spell's ``classes``."""

    starting: dict[str, list[SpellSpec]] = {archetype: [] for archetype in SPELLCASTING_ARCHETYPES}
    unlocks: dict[str, dict[int, list[SpellSpec]]] = {archetype: {} for archetype in SPELLCASTING_ARCHETYPES}
    catalog = {**AUTHORITATIVE_SPELL_EFFECTS, **NARRATED_SPELLS}
    for key in sorted(catalog, key=lambda name: (int(catalog[name]['level']), name)):
        entry = catalog[key]
        spec: SpellSpec = (_catalog_display_name(key), int(entry['level']), SPELL_DESCRIPTIONS[key])
        for archetype in entry['classes']:
            unlock = catalog_unlock_level(archetype, int(entry['level']))
            if unlock == 1:
                starting[archetype].append(spec)
            else:
                unlocks[archetype].setdefault(unlock, []).append(spec)
    for archetype, high_levels in CLASS_HIGH_LEVEL_UNLOCKS.items():
        for unlock, specs in high_levels.items():
            unlocks[archetype].setdefault(unlock, []).extend(specs)
    return starting, {archetype: dict(sorted(levels.items())) for archetype, levels in unlocks.items()}


def _catalog_display_name(key: str) -> str:
    small_words = {'of', 'the', 'with', 'without'}
    words = key.split(' ')
    return ' '.join(
        word if index and word in small_words else '/'.join(part[:1].upper() + part[1:] for part in word.split('/'))
        for index, word in enumerate(words)
    )


CLASS_STARTING_SPELLS, CLASS_LEVEL_UNLOCKS = _catalog_class_lists()


CLASS_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (('wizard', 'mage', 'witch', 'arcanist', 'psion', 'magus', 'summoner', 'elementalist', 'psychic', 'medium', 'occultist', 'mesmerist', 'necromancer', 'mystic', 'spellblade', 'spell hacker'), 'wizard'),
    (('sorcerer', 'dragon blood', 'wild magic'), 'sorcerer'),
    (('warlock', 'pact', 'hexblade'), 'warlock'),
    (('cleric', 'healer', 'oracle', 'priest', 'shaman', 'warpriest', 'inquisitor'), 'cleric'),
    (('druid', 'beastmaster', 'shapeshifter', 'shapechanger', 'warden'), 'druid'),
    (('bard', 'skald', 'entertainer'), 'bard'),
    (('paladin', 'holy knight', 'divine knight'), 'paladin'),
    (('ranger',), 'ranger'),
    (('artificer', 'alchemist', 'engineer', 'inventor', 'technomancer'), 'artificer'),
)


RACE_SPELLS: dict[str, list[SpellSpec]] = {
    'aasimar': [('Light', 0, 'Create holy light.'), ('Healing Hands', 1, 'Restore a small amount of health.')],
    'changeling': [('Disguise Self', 1, 'Alter your appearance magically.'), ('Alter Self', 2, 'Briefly reshape your body.')],
    'dragonborn': [('Draconic Spark', 0, 'Create a tiny elemental sign.'), ('Breath Weapon', 1, 'Exhale elemental power tied to ancestry.')],
    'elf': [('Minor Illusion', 0, 'Create a small image or sound.'), ('Detect Magic', 1, 'Sense nearby magic.')],
    'fairy': [('Druidcraft', 0, 'Small fey nature signs.'), ('Faerie Fire', 1, 'Outline creatures in magical light.')],
    'firbolg': [('Hidden Step', 1, 'Briefly vanish from sight.'), ('Speak with Animals', 1, 'Communicate with beasts.')],
    'genasi': [('Elemental Attunement', 0, 'Shape a tiny expression of your element.'), ('Elemental Burst', 1, 'Release a small elemental burst.')],
    'gnome': [('Minor Illusion', 0, 'Create a small image or sound.'), ('Speak with Small Beasts', 1, 'Communicate simple ideas with small animals.')],
    'satyr': [('Friends', 0, 'Add a flash of fey charm to a social moment.'), ('Charm Person', 1, 'Briefly influence a humanoid.')],
    'saiyan': [
        ('Ki Blast', 0, 'Project a focused ranged burst of life energy.'),
        ('Ki Sense', 0, 'Feel strong nearby life force, battle pressure, or hidden power.'),
        ('Battle Aura', 1, 'Flare your ki to empower a charge, strike, leap, or intimidation moment.'),
        ('Zenkai Surge', 1, 'After surviving terrible harm, convert recovery into a short burst of fighting power.'),
        ('Great Ape Transformation', 2, 'A dangerous full-moon or Blutz Wave transformation into a gigantic berserk form.'),
    ],
    'tiefling': [('Thaumaturgy', 0, 'Small infernal signs and omens.'), ('Hellish Rebuke', 1, 'Retaliatory infernal fire.')],
    'triton': [('Shape Water', 0, 'Shape a small amount of water.'), ('Fog Cloud', 1, 'Create concealing mist.'), ('Thunderwave', 1, 'A concussive burst of sound.')],
    'yuan-ti': [('Poison Spray', 0, 'A short-range poison attack.'), ('Suggestion', 2, 'Magically influence a course of action.')],
}


def class_spell_archetype(class_name: str | None) -> str | None:
    normalized = re.sub(r'[^a-z0-9]+', ' ', str(class_name or '').split('-', 1)[0].lower()).strip()
    if not normalized:
        return None
    compact = normalized.replace(' ', '_')
    if compact in CLASS_STARTING_SPELLS:
        return compact
    tokens = set(normalized.split())
    for keywords, archetype in CLASS_KEYWORDS:
        if any(keyword in tokens or keyword in normalized for keyword in keywords):
            return archetype
    return None


PREPARED_SPELLCASTER_ARCHETYPES = frozenset({'wizard', 'cleric', 'druid', 'paladin', 'artificer'})
KNOWN_SPELLCASTER_ARCHETYPES = frozenset({'bard', 'ranger', 'sorcerer', 'warlock'})


def spell_preparation_policy_for_class(class_name: str | None) -> dict[str, Any]:
    """Return the persisted known-vs-prepared policy for a class label.

    Unknown and non-spellcasting classes retain the legacy ``known`` behavior.
    That default is intentionally permissive for old character sheets which had
    no preparation concept at all; authoritative resource validation can still
    reject spells that are absent from the known-spell catalog.
    """

    archetype = class_spell_archetype(class_name)
    requires_preparation = archetype in PREPARED_SPELLCASTER_ARCHETYPES
    return {
        'schemaVersion': 1,
        'mode': 'prepared' if requires_preparation else 'known',
        'requiresPreparation': requires_preparation,
        'classArchetype': archetype,
        'source': 'class' if archetype in PREPARED_SPELLCASTER_ARCHETYPES | KNOWN_SPELLCASTER_ARCHETYPES else 'legacy',
    }


def normalize_spell_preparation_policy(
    raw_policy: Any,
    *,
    class_name: str | None = None,
) -> dict[str, Any]:
    derived = spell_preparation_policy_for_class(class_name)
    if isinstance(raw_policy, str):
        mode = _normalize_text(raw_policy).lower()
        raw_policy = {'mode': mode}
    if not isinstance(raw_policy, dict):
        return derived

    archetype = class_spell_archetype(
        class_name
        or raw_policy.get('classArchetype')
        or raw_policy.get('class_archetype')
    )
    if archetype in PREPARED_SPELLCASTER_ARCHETYPES:
        mode = 'prepared'
    elif archetype in KNOWN_SPELLCASTER_ARCHETYPES:
        mode = 'known'
    else:
        mode = _normalize_text(raw_policy.get('mode') or raw_policy.get('policy')).lower()
        if mode not in {'known', 'prepared'}:
            mode = derived['mode']
    requires_preparation = mode == 'prepared'
    source = (
        'class'
        if archetype in PREPARED_SPELLCASTER_ARCHETYPES | KNOWN_SPELLCASTER_ARCHETYPES
        else _normalize_text(raw_policy.get('source')) or derived['source']
    )
    policy = {
        'schemaVersion': 1,
        'mode': mode,
        'requiresPreparation': requires_preparation,
        'classArchetype': archetype,
        'source': source,
    }
    if raw_policy.get('legacyDefaultApplied') is True or raw_policy.get('legacy_default_applied') is True:
        policy['legacyDefaultApplied'] = True
    return policy


def _spell_specs_to_payloads(
    specs: Iterable[SpellSpec],
    *,
    source_type: str,
    source_detail: str,
    learned_at_level: int | None = None,
) -> list[dict[str, Any]]:
    return [
        spell_payload(
            name,
            level=level,
            source_type=source_type,
            source_detail=source_detail,
            description=description,
            learned_at_level=learned_at_level,
        )
        for name, level, description in specs
        if _normalize_text(name)
    ]


def class_spells_for_level(class_name: str | None, level: int = 1) -> list[dict[str, Any]]:
    archetype = class_spell_archetype(class_name)
    if not archetype:
        return []
    character_level = max(1, min(20, int(level or 1)))
    spells = _spell_specs_to_payloads(
        CLASS_STARTING_SPELLS.get(archetype, []),
        source_type='class',
        source_detail=archetype,
        learned_at_level=1,
    )
    for unlock_level, specs in sorted(CLASS_LEVEL_UNLOCKS.get(archetype, {}).items()):
        if unlock_level <= character_level:
            spells.extend(
                _spell_specs_to_payloads(
                    specs,
                    source_type='level',
                    source_detail=f'{archetype}:{unlock_level}',
                    learned_at_level=unlock_level,
                )
            )
    spells.extend(original_class_starter_spells(archetype))
    return spells


def _race_definition(race_name: str | None, race_selection: Any = None) -> dict[str, Any] | None:
    if isinstance(race_selection, str):
        selection = race_selection_from_json(race_selection, race_name)
    elif isinstance(race_selection, dict):
        selection = race_selection
    else:
        selection = None
    race = race_definition_from_selection(selection, race_name) if selection else None
    return race or find_curated_race(race_name)


def _race_spell_key(race: dict[str, Any] | None, race_name: str | None) -> str | None:
    if race:
        race_id = str(race.get('id') or '').strip().lower()
        race_text = ' '.join(
            str(value or '')
            for value in [race_id, race.get('name'), *(race.get('aliases') if isinstance(race.get('aliases'), list) else [])]
        ).lower()
        for key in RACE_SPELLS:
            if key in race_id or key in race_text:
                return key
    normalized = str(race_name or '').strip().lower()
    for key in RACE_SPELLS:
        if key in normalized:
            return key
    return None


def race_spells(race_name: str | None, race_selection: Any = None) -> list[dict[str, Any]]:
    race = _race_definition(race_name, race_selection)
    key = _race_spell_key(race, race_name)
    tags = {str(tag or '').strip().lower() for tag in (race or {}).get('tags', []) if str(tag or '').strip()}
    traits = race.get('traits') if isinstance(race, dict) else []
    trait_text = json.dumps(traits or [], sort_keys=True).lower()
    if key:
        return [
            *_spell_specs_to_payloads(RACE_SPELLS[key], source_type='race', source_detail=key, learned_at_level=1),
            *original_race_spells(race_key=key, race_tags=tags, trait_text=trait_text, count=2),
        ]

    if _looks_like_shapeshifter_trait(trait_text):
        return [
            *_spell_specs_to_payloads(RACE_SPELLS['changeling'], source_type='race', source_detail='shapeshifter', learned_at_level=1),
            *original_race_spells(race_key='shapeshifter', race_tags=tags, trait_text=trait_text, count=2),
        ]
    if 'breath_weapon' in trait_text or 'elemental_cone_or_line' in trait_text:
        return [
            *_spell_specs_to_payloads(RACE_SPELLS['dragonborn'], source_type='race', source_detail='draconic', learned_at_level=1),
            *original_race_spells(race_key='draconic', race_tags=tags, trait_text=trait_text, count=2),
        ]
    if 'elemental' in tags or 'elemental' in trait_text:
        return [
            *_spell_specs_to_payloads(RACE_SPELLS['genasi'], source_type='race', source_detail='elemental', learned_at_level=1),
            *original_race_spells(race_key='elemental', race_tags=tags, trait_text=trait_text, count=2),
        ]
    if 'celestial' in tags or 'minor_healing' in trait_text:
        return [
            *_spell_specs_to_payloads(RACE_SPELLS['aasimar'], source_type='race', source_detail='celestial', learned_at_level=1),
            *original_race_spells(race_key='celestial', race_tags=tags, trait_text=trait_text, count=2),
        ]
    race_id = str((race or {}).get('id') or '').strip().lower()
    if 'magical' in tags and race_id not in {'human', 'afro-diasporic-human'}:
        source_detail = str((race or {}).get('id') or race_name or 'magical')
        return [
            *_spell_specs_to_payloads(
                [('Prestidigitation', 0, 'A small inherited magical effect.'), ('Detect Magic', 1, 'Sense nearby magic.')],
                source_type='race',
                source_detail=source_detail,
                learned_at_level=1,
            ),
            *original_race_spells(race_key=source_detail, race_tags=tags, trait_text=trait_text, count=2),
        ]
    return []


def normalize_spell(raw_spell: Any) -> dict[str, Any] | None:
    if isinstance(raw_spell, str):
        name = _normalize_text(raw_spell)
        return spell_payload(name) if name else None
    if not isinstance(raw_spell, dict):
        return None
    name = _normalize_text(raw_spell.get('name') or raw_spell.get('spellName') or raw_spell.get('spell_name'))
    if not name:
        return None
    normalized = {
        **raw_spell,
        'id': _normalize_text(raw_spell.get('id') or raw_spell.get('spellId') or raw_spell.get('spell_id') or _spell_id(name)),
        'name': name,
        'level': _bounded_spell_level(raw_spell.get('level', raw_spell.get('spellLevel', raw_spell.get('spell_level', 0)))),
    }
    sources = raw_spell.get('sources')
    if isinstance(sources, list):
        normalized['sources'] = [_normalize_text(source) for source in sources if _normalize_text(source)]
    elif raw_spell.get('source'):
        normalized['sources'] = [_normalize_text(raw_spell.get('source'))]
    else:
        source = _source_label(raw_spell.get('sourceType'), raw_spell.get('sourceDetail'))
        if source:
            normalized['source'] = source
            normalized['sources'] = [source]
    authoritative_effect = authoritative_spell_effect(name)
    _apply_spell_metadata(normalized, authoritative_effect)
    return normalized


def _spellbook_field(raw_spellbook: dict[str, Any], *keys: str) -> tuple[Any, bool]:
    for key in keys:
        if key in raw_spellbook:
            return raw_spellbook.get(key), True
    return None, False


def _prepared_spell_name(raw_value: Any) -> str:
    if isinstance(raw_value, dict):
        return _normalize_text(
            raw_value.get('name')
            or raw_value.get('spellName')
            or raw_value.get('spell_name')
            or raw_value.get('id')
        )
    return _normalize_text(raw_value)


def _spell_uses_preparation(spell: dict[str, Any]) -> bool:
    if _bounded_spell_level(spell.get('level')) <= 0:
        return False
    source_type = _normalize_text(spell.get('sourceType') or spell.get('source_type')).lower()
    if source_type in {'race', 'race_catalog', 'ancestry', 'innate'}:
        return False
    if spell.get('requiresPreparation') is False or spell.get('requires_preparation') is False:
        return False
    return True


def normalize_spellbook(
    raw_spellbook: Any,
    *,
    class_name: str | None = None,
) -> dict[str, Any]:
    if isinstance(raw_spellbook, dict):
        raw_known, _known_field_present = _spellbook_field(
            raw_spellbook,
            'knownSpells',
            'known_spells',
            'spells',
        )
        prepared, prepared_field_present = _spellbook_field(
            raw_spellbook,
            'preparedSpells',
            'prepared_spells',
        )
        raw_policy, policy_field_present = _spellbook_field(
            raw_spellbook,
            'preparationPolicy',
            'preparation_policy',
        )
        sources = raw_spellbook.get('sources') or []
    elif isinstance(raw_spellbook, list):
        raw_known = raw_spellbook
        prepared = []
        prepared_field_present = False
        raw_policy = None
        policy_field_present = False
        sources = []
    else:
        raw_known = []
        prepared = []
        prepared_field_present = False
        raw_policy = None
        policy_field_present = False
        sources = []

    if not isinstance(raw_known, list):
        raw_known = []
    if not isinstance(prepared, list):
        prepared = []

    known: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_spell in raw_known:
        spell = normalize_spell(raw_spell)
        if not spell:
            continue
        key = _slug(spell.get('id') or spell.get('name'))
        name_key = _slug(spell.get('name'))
        if key in seen or name_key in seen:
            continue
        seen.add(key)
        seen.add(name_key)
        known.append(spell)
    known_names = {_slug(spell.get('name')): spell['name'] for spell in known if spell.get('name')}
    known_ids = {_slug(spell.get('id')): spell['name'] for spell in known if spell.get('id')}
    prepared_names: list[str] = []
    for raw_prepared in prepared:
        candidate = _prepared_spell_name(raw_prepared)
        canonical_name = known_names.get(_slug(candidate)) or known_ids.get(_slug(candidate))
        if canonical_name and canonical_name not in prepared_names:
            prepared_names.append(canonical_name)

    policy = normalize_spell_preparation_policy(
        raw_policy if policy_field_present else None,
        class_name=class_name,
    )
    if policy['requiresPreparation'] and not prepared_field_present:
        # Legacy sheets treated every known spell as immediately usable. Preserve
        # that behavior once, but persist the class's real preparation policy so
        # a future preparation UI can replace this explicit migrated selection.
        prepared_names = [spell['name'] for spell in known if _spell_uses_preparation(spell)]
        policy['legacyDefaultApplied'] = True

    source_names = [_normalize_text(item) for item in sources if _normalize_text(item)] if isinstance(sources, list) else []
    for spell in known:
        for source in spell.get('sources') or []:
            if source and source not in source_names:
                source_names.append(source)
    return {
        'knownSpells': known,
        'preparedSpells': prepared_names,
        'preparationPolicy': policy,
        'sources': source_names,
    }


def known_spell(raw_spellbook: Any, spell_name_or_id: Any) -> dict[str, Any] | None:
    spellbook = normalize_spellbook(raw_spellbook)
    lookup = _slug(spell_name_or_id)
    for spell in spellbook['knownSpells']:
        if lookup in {_slug(spell.get('id')), _slug(spell.get('name'))}:
            return deepcopy(spell)
    return None


def spell_requires_preparation(raw_spellbook: Any, spell_name_or_id: Any) -> bool:
    spellbook = normalize_spellbook(raw_spellbook)
    spell = known_spell(spellbook, spell_name_or_id)
    return bool(
        spell
        and spellbook['preparationPolicy']['requiresPreparation']
        and _spell_uses_preparation(spell)
    )


def spell_is_prepared(raw_spellbook: Any, spell_name_or_id: Any) -> bool:
    spellbook = normalize_spellbook(raw_spellbook)
    spell = known_spell(spellbook, spell_name_or_id)
    if not spell:
        return False
    if not spell_requires_preparation(spellbook, spell.get('id') or spell.get('name')):
        return True
    prepared = {_slug(name) for name in spellbook.get('preparedSpells') or []}
    return _slug(spell.get('name')) in prepared or _slug(spell.get('id')) in prepared


def _auto_race_source_details(spell: dict[str, Any]) -> set[str]:
    details: set[str] = set()
    source_type = _normalize_text(spell.get('sourceType') or spell.get('source_type')).lower()
    source_detail = _normalize_text(spell.get('sourceDetail') or spell.get('source_detail')).lower()
    if source_type in {'race', 'race_catalog'} and source_detail:
        details.add(source_detail)

    raw_sources = spell.get('sources') if isinstance(spell.get('sources'), list) else []
    if spell.get('source'):
        raw_sources = [*raw_sources, spell.get('source')]
    for raw_source in raw_sources:
        source = _normalize_text(raw_source).lower()
        if source.startswith('race:') or source.startswith('race_catalog:'):
            details.add(source.split(':', 1)[1])
    return details


def _spell_source_labels(spell: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    raw_sources = spell.get('sources') if isinstance(spell.get('sources'), list) else []
    for raw_source in [*raw_sources, spell.get('source')]:
        source = _normalize_text(raw_source)
        if source and source not in labels:
            labels.append(source)
    if labels:
        return labels
    source = _source_label(
        spell.get('sourceType') or spell.get('source_type'),
        spell.get('sourceDetail') or spell.get('source_detail'),
    )
    return [source] if source else []


def _source_parts(source: Any) -> tuple[str, str]:
    source_text = _normalize_text(source)
    source_type, separator, source_detail = source_text.partition(':')
    return source_type.lower(), source_detail if separator else ''


def _spell_with_sources(spell: dict[str, Any], sources: list[str]) -> dict[str, Any]:
    updated = deepcopy(spell)
    for key in ('source', 'sourceType', 'source_type', 'sourceDetail', 'source_detail'):
        updated.pop(key, None)
    updated['sources'] = list(sources)
    primary_source = sources[0]
    source_type, source_detail = _source_parts(primary_source)
    updated['source'] = primary_source
    if source_type:
        updated['sourceType'] = source_type
    if source_detail:
        updated['sourceDetail'] = source_detail
    return updated


def _remove_spell_sources(
    raw_spellbook: Any,
    *,
    remove_source,
    reset_preparation_policy: bool = False,
) -> dict[str, Any]:
    """Remove selected grant provenance without deleting independently learned spells."""

    spellbook = normalize_spellbook(raw_spellbook)
    filtered_spells: list[dict[str, Any]] = []
    for spell in spellbook['knownSpells']:
        sources = _spell_source_labels(spell)
        removed_sources = [source for source in sources if remove_source(source)]
        if not removed_sources:
            filtered_spells.append(deepcopy(spell))
            continue
        remaining_sources = [source for source in sources if not remove_source(source)]
        if remaining_sources:
            filtered_spells.append(_spell_with_sources(spell, remaining_sources))
        # A spell with only replaced automatic provenance is intentionally
        # removed. Source-less legacy entries never reach this branch.

    remaining_names = {_slug(spell.get('name')) for spell in filtered_spells if spell.get('name')}
    reconciled: dict[str, Any] = {
        'knownSpells': filtered_spells,
        'sources': list(
            dict.fromkeys(
                source
                for spell in filtered_spells
                for source in _spell_source_labels(spell)
            )
        ),
    }
    if not reset_preparation_policy:
        reconciled['preparedSpells'] = [
            spell_name
            for spell_name in spellbook.get('preparedSpells', [])
            if _slug(spell_name) in remaining_names
        ]
        reconciled['preparationPolicy'] = spellbook.get('preparationPolicy')
    return reconciled


def remove_automatic_spell_grants(
    raw_spellbook: Any,
    *,
    remove_class_grants: bool = False,
    remove_race_grants: bool = False,
    reset_preparation_policy: bool = False,
) -> dict[str, Any]:
    """Remove replaceable class/race grants while retaining durable provenance.

    Story-learned and source-less legacy spells survive a respec. A spell with
    mixed provenance survives with only the sources that still justify it.
    """

    removed_types: set[str] = set()
    if remove_class_grants:
        removed_types.update(AUTO_CLASS_SPELL_SOURCE_TYPES)
    if remove_race_grants:
        removed_types.update(AUTO_RACE_SPELL_SOURCE_TYPES)
    if not removed_types:
        return normalize_spellbook(raw_spellbook)
    return _remove_spell_sources(
        raw_spellbook,
        remove_source=lambda source: _source_parts(source)[0] in removed_types,
        reset_preparation_policy=reset_preparation_policy,
    )


def _remove_stale_auto_race_spells(raw_spellbook: Any, current_race_key: str | None) -> dict[str, Any]:
    spellbook = normalize_spellbook(raw_spellbook)
    current_key = _normalize_text(current_race_key).lower()
    if current_key in STALE_AUTO_RACE_SOURCE_DETAILS:
        return spellbook

    return normalize_spellbook(
        _remove_spell_sources(
            spellbook,
            remove_source=lambda source: (
                _source_parts(source)[0] in AUTO_RACE_SPELL_SOURCE_TYPES
                and _source_parts(source)[1].lower() in STALE_AUTO_RACE_SOURCE_DETAILS
            ),
        )
    )


def merge_spellbooks(existing: Any, incoming: Any) -> dict[str, Any]:
    existing_prepared_explicit = isinstance(existing, dict) and any(
        key in existing for key in ('preparedSpells', 'prepared_spells')
    )
    existing_policy_explicit = isinstance(existing, dict) and any(
        key in existing for key in ('preparationPolicy', 'preparation_policy')
    )
    merged = normalize_spellbook(existing)
    incoming_book = normalize_spellbook(incoming)
    existing_policy = normalize_spell_preparation_policy(merged.get('preparationPolicy'))
    existing_is_legacy = (
        existing_policy.get('source') == 'legacy'
        and existing_policy.get('mode') == 'known'
        and not existing_policy.get('requiresPreparation')
    )
    if existing_is_legacy:
        # Normalization adds empty compatibility fields. They must not make an
        # old sheet look as though it explicitly chose a preparation policy.
        existing_prepared_explicit = False
        existing_policy_explicit = False
    spells = merged['knownSpells']
    by_name = {_slug(spell.get('name')): spell for spell in spells if spell.get('name')}
    by_id = {_slug(spell.get('id')): spell for spell in spells if spell.get('id')}
    for incoming_spell in incoming_book['knownSpells']:
        name_key = _slug(incoming_spell.get('name'))
        id_key = _slug(incoming_spell.get('id'))
        existing_spell = by_name.get(name_key) or by_id.get(id_key)
        if existing_spell:
            existing_sources = existing_spell.setdefault('sources', [])
            if not isinstance(existing_sources, list):
                existing_sources = []
                existing_spell['sources'] = existing_sources
            for source in incoming_spell.get('sources') or []:
                if source not in existing_sources:
                    existing_sources.append(source)
            for key, value in incoming_spell.items():
                if key not in existing_spell and value not in (None, '', [], {}):
                    existing_spell[key] = value
            continue
        spells.append(deepcopy(incoming_spell))
        by_name[name_key] = spells[-1]
        by_id[id_key] = spells[-1]
    for source in incoming_book.get('sources') or []:
        if source not in merged['sources']:
            merged['sources'].append(source)
    incoming_policy = incoming_book.get('preparationPolicy')
    if not existing_policy_explicit and isinstance(incoming_policy, dict):
        merged['preparationPolicy'] = deepcopy(incoming_policy)

    prepared_names = list(merged.get('preparedSpells') or [])
    incoming_policy = normalize_spell_preparation_policy(incoming_book.get('preparationPolicy'))
    merge_incoming_prepared = not (
        existing_prepared_explicit and incoming_policy.get('legacyDefaultApplied') is True
    )
    if merge_incoming_prepared:
        for spell_name in incoming_book.get('preparedSpells') or []:
            if spell_name not in prepared_names:
                prepared_names.append(spell_name)
    final_policy = normalize_spell_preparation_policy(merged.get('preparationPolicy'))
    if final_policy['requiresPreparation'] and not existing_prepared_explicit:
        for spell in spells:
            if _spell_uses_preparation(spell) and spell.get('name') not in prepared_names:
                prepared_names.append(spell['name'])
        final_policy['legacyDefaultApplied'] = True
    return normalize_spellbook(
        {
            'knownSpells': spells,
            'preparedSpells': prepared_names,
            'preparationPolicy': final_policy,
            'sources': merged.get('sources') or [],
        }
    )


def spellbook_for_character(
    *,
    class_name: str | None,
    race_name: str | None,
    race_selection: Any = None,
    level: int = 1,
) -> dict[str, Any]:
    spells = [
        *class_spells_for_level(class_name, level),
        *race_spells(race_name, race_selection),
    ]
    return normalize_spellbook(spells, class_name=class_name)


def character_sheet_record(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return deepcopy(raw_value)
    if raw_value is None:
        return {}
    loaded = safe_json_loads(raw_value, None)
    if isinstance(loaded, dict):
        return loaded
    if isinstance(raw_value, str) and raw_value.strip():
        return {'notes': raw_value.strip()}
    return {}


def spellbook_from_character_sheet(raw_sheet: Any) -> dict[str, Any]:
    sheet = character_sheet_record(raw_sheet)
    raw_spellbook = sheet.get('spellbook')
    if raw_spellbook is None:
        raw_spellbook = sheet.get('knownSpells') or sheet.get('known_spells') or sheet.get('spells')
    return normalize_spellbook(raw_spellbook)


def known_spell_names(raw_spellbook: Any) -> list[str]:
    return [spell['name'] for spell in normalize_spellbook(raw_spellbook).get('knownSpells', []) if spell.get('name')]


def ensure_character_sheet_spellbook(
    raw_sheet: Any,
    *,
    class_name: str | None,
    race_name: str | None,
    race_selection: Any = None,
    level: int = 1,
    replace_class_grants: bool = False,
    replace_race_grants: bool = False,
    reset_preparation_policy: bool = False,
) -> tuple[dict[str, Any], bool]:
    sheet = character_sheet_record(raw_sheet)
    before = json.dumps(sheet, sort_keys=True, default=str)
    race = _race_definition(race_name, race_selection)
    current_race_key = _race_spell_key(race, race_name)
    baseline = spellbook_for_character(
        class_name=class_name,
        race_name=race_name,
        race_selection=race_selection,
        level=level,
    )
    existing = sheet.get('spellbook')
    if existing is None:
        existing = sheet.get('knownSpells') or sheet.get('known_spells') or sheet.get('spells')
    had_existing_spell_data = existing is not None
    if replace_class_grants or replace_race_grants:
        existing = remove_automatic_spell_grants(
            existing,
            remove_class_grants=replace_class_grants,
            remove_race_grants=replace_race_grants,
            reset_preparation_policy=reset_preparation_policy,
        )
    existing = _remove_stale_auto_race_spells(existing, current_race_key)
    if not baseline.get('knownSpells') and not had_existing_spell_data and not (
        replace_class_grants or replace_race_grants
    ):
        return sheet, False
    merged = (
        merge_spellbooks(existing, baseline)
        if baseline.get('knownSpells')
        else normalize_spellbook(existing, class_name=class_name)
    )
    sheet['spellbook'] = merged
    sheet['spells'] = known_spell_names(merged)
    after = json.dumps(sheet, sort_keys=True, default=str)
    return sheet, before != after


def spell_from_change(change: dict[str, Any]) -> dict[str, Any] | None:
    raw_spell = change.get('spell') if isinstance(change.get('spell'), dict) else {}
    spell_name = _normalize_text(
        change.get('spellName')
        or change.get('spell_name')
        or raw_spell.get('name')
        or raw_spell.get('spellName')
        or raw_spell.get('spell_name')
    )
    if not spell_name and isinstance(change.get('spell'), str):
        spell_name = _normalize_text(change.get('spell'))
    if not spell_name:
        return None
    return spell_payload(
        spell_name,
        level=_bounded_spell_level(change.get('spellLevel', change.get('spell_level', raw_spell.get('level', raw_spell.get('spellLevel', 0))))),
        source_type=change.get('sourceType') or change.get('source_type') or raw_spell.get('sourceType') or 'story',
        source_detail=change.get('sourceDetail') or change.get('source_detail') or raw_spell.get('sourceDetail') or 'learned',
        description=change.get('description') or raw_spell.get('description'),
        learned_at_level=change.get('learnedAtLevel') or change.get('learned_at_level') or raw_spell.get('learnedAtLevel'),
        learned_from=change.get('learnedFrom') or change.get('learned_from') or raw_spell.get('learnedFrom') or change.get('sourceText'),
    )
