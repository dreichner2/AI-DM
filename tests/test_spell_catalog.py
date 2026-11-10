"""The rules-backed spell catalog: definitions, class lists and one resolution per spell.

Every resolution uses the same steady roller: a d20 shows 8 (a +6 spell attack
hits AC 12, a +2 save misses DC 14) and every other die shows 3.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Callable

import pytest

from aidm_server.combat import modifiers, vitality
from aidm_server.combat.pipeline import apply_turn_boundaries
from aidm_server.spell_effects import (
    after_damage_spell_effects,
    normalized_spell_definition,
    resolve_targeted_spell,
    resolve_turn_start_spell_effects,
    shield_reaction_provider,
)
from aidm_server.spellbook import (
    AUTHORITATIVE_SPELL_EFFECTS,
    CLASS_LEVEL_UNLOCKS,
    CLASS_STARTING_SPELLS,
    NARRATED_SPELLS,
    class_spells_for_level,
    spell_payload,
)


ARCHETYPES = ('wizard', 'sorcerer', 'warlock', 'cleric', 'druid', 'bard', 'paladin', 'ranger', 'artificer')
CASTING_ABILITY = {
    'wizard': 'intelligence', 'sorcerer': 'charisma', 'warlock': 'charisma', 'cleric': 'wisdom', 'druid': 'wisdom',
    'bard': 'charisma', 'paladin': 'charisma', 'ranger': 'wisdom', 'artificer': 'intelligence',
}
WIZ, SOR, WAR, CLE, DRU, BAR, PAL, RAN, ART = ARCHETYPES

# The spec's catalog table: level, casting time, concentration, out of combat, classes.
TABLE: dict[str, tuple[int, str, bool, bool, set[str]]] = {
    'fire bolt': (0, 'action', False, False, {WIZ, SOR, ART}),
    'ray of frost': (0, 'action', False, False, {WIZ, SOR, ART}),
    'shocking grasp': (0, 'action', False, False, {WIZ, SOR}),
    'chill touch': (0, 'action', False, False, {WIZ, SOR, WAR}),
    'poison spray': (0, 'action', False, False, {SOR, WAR, DRU}),
    'sacred flame': (0, 'action', False, False, {CLE}),
    'toll the dead': (0, 'action', False, False, {CLE}),
    'eldritch blast': (0, 'action', False, False, {WAR}),
    'vicious mockery': (0, 'action', False, False, {BAR}),
    'produce flame': (0, 'action', False, False, {DRU}),
    'thorn whip': (0, 'action', False, False, {DRU}),
    'guidance': (0, 'action', True, True, {CLE, DRU}),
    'spare the dying': (0, 'action', False, True, {CLE}),
    'magic missile': (1, 'action', False, False, {WIZ, SOR}),
    'burning hands': (1, 'action', False, False, {WIZ, SOR}),
    'thunderwave': (1, 'action', False, False, {WIZ, SOR, DRU, BAR}),
    'chromatic orb': (1, 'action', False, False, {WIZ, SOR}),
    'sleep': (1, 'action', False, False, {WIZ, SOR, BAR}),
    'mage armor': (1, 'action', False, True, {WIZ, SOR}),
    'false life': (1, 'action', False, True, {WIZ, SOR, ART}),
    'shield': (1, 'reaction', False, False, {WIZ, SOR}),
    'cure wounds': (1, 'action', False, True, {CLE, DRU, BAR, PAL, RAN, ART}),
    'healing word': (1, 'bonus_action', False, True, {CLE, DRU, BAR}),
    'bless': (1, 'action', True, False, {CLE, PAL}),
    'bane': (1, 'action', True, False, {CLE, BAR}),
    'shield of faith': (1, 'bonus_action', True, False, {CLE, PAL}),
    'guiding bolt': (1, 'action', False, False, {CLE}),
    'inflict wounds': (1, 'action', False, False, {CLE}),
    'command': (1, 'action', False, False, {CLE, PAL}),
    'entangle': (1, 'action', True, False, {DRU, RAN}),
    'faerie fire': (1, 'action', True, False, {DRU, BAR, ART}),
    "hunter's mark": (1, 'bonus_action', True, False, {RAN}),
    'hex': (1, 'bonus_action', True, False, {WAR}),
    'armor of agathys': (1, 'action', False, True, {WAR}),
    'charm person': (1, 'action', False, False, {WAR, BAR}),
    'dissonant whispers': (1, 'action', False, False, {BAR}),
    "tasha's hideous laughter": (1, 'action', True, False, {BAR}),
    'heroism': (1, 'action', True, False, {BAR, PAL}),
    'divine favor': (1, 'bonus_action', True, False, {PAL}),
    'grease': (1, 'action', False, False, {ART}),
    'scorching ray': (2, 'action', False, False, {WIZ, SOR}),
    'shatter': (2, 'action', False, False, {WIZ, SOR, WAR, BAR}),
    'hold person': (2, 'action', True, False, {WIZ, SOR, WAR, CLE, DRU, BAR}),
    'misty step': (2, 'bonus_action', False, False, {WIZ, SOR, WAR}),
    'invisibility': (2, 'action', True, True, {WIZ, SOR, WAR, BAR, ART}),
    'web': (2, 'action', True, False, {WIZ, SOR}),
    'blindness/deafness': (2, 'action', False, False, {WIZ, SOR, CLE, BAR}),
    'aid': (2, 'action', False, True, {CLE, PAL, ART}),
    'lesser restoration': (2, 'action', False, True, {CLE, DRU, BAR, PAL, RAN, ART}),
    'prayer of healing': (2, 'action', False, True, {CLE}),
    'spiritual weapon': (2, 'bonus_action', False, False, {CLE}),
    'moonbeam': (2, 'action', True, False, {DRU}),
    'barkskin': (2, 'action', True, True, {DRU, RAN}),
    'fireball': (3, 'action', False, False, {WIZ, SOR}),
    'lightning bolt': (3, 'action', False, False, {WIZ, SOR}),
    'hypnotic pattern': (3, 'action', True, False, {WIZ, SOR, WAR, BAR}),
    'fear': (3, 'action', True, False, {WAR, BAR}),
    'revivify': (3, 'action', False, True, {CLE, PAL}),
    'mass healing word': (3, 'bonus_action', False, True, {CLE, BAR}),
    'spirit guardians': (3, 'action', True, False, {CLE}),
    'dispel magic': (3, 'action', False, True, {WIZ, SOR, WAR, CLE, DRU, BAR, PAL}),
}


def steady(sides: int) -> int:
    return 8 if sides == 20 else 3


def _participant(participant_id: str, *, team: str = 'enemy', hp: int = 30, maximum: int | None = None,
                 band: str = 'near', **extra: object) -> dict:
    return {
        'id': participant_id,
        'name': participant_id.replace('_', ' ').title(),
        'team': team,
        'kind': 'player_character' if team == 'player' else 'creature',
        'level': 5,
        'hp': {'current': hp, 'max': maximum if maximum is not None else hp, 'temp': 0},
        'armorClass': 12,
        'stats': {'strength': 10, 'dexterity': 14, 'constitution': 14, 'intelligence': 16, 'wisdom': 16, 'charisma': 16},
        'conditions': [],
        'position': {'rangeBand': band, 'zoneId': 'hall'},
        'isAlive': hp > 0,
        'isConscious': hp > 0,
        'isPresent': True,
        **extra,
    }


def _enemy(participant_id: str, **extra: object) -> dict:
    enemy = _participant(participant_id, **extra)
    enemy['stats'] = {**enemy['stats'], 'intelligence': 10, 'wisdom': 10, 'charisma': 10}
    return enemy


def _caster(**extra: object) -> dict:
    return _participant('player_mage', team='player', **extra)


def _ally(participant_id: str = 'player_ally', **extra: object) -> dict:
    return _participant(participant_id, team='player', **extra)


def _combat(*participants: dict, status: str = 'active') -> dict:
    return {
        'status': status, 'round': 1, 'turnIndex': 0, 'participants': list(participants),
        'battlefield': {'cover': [{'id': 'wall', 'name': 'Wall', 'coverType': 'full'}]},
        'flags': {'activeActorId': 'player_mage'},
    }


def _spell(name: str) -> dict:
    spell = spell_payload(name)
    spell['delivery']['castingAbility'] = 'wisdom'
    return spell


def _cast(name: str, combat: dict, targets: list[str], *, cast_level: int | None = None,
          roller: Callable[[int], int] = steady, cast_id: str = '', **options: object) -> dict:
    result = resolve_targeted_spell(
        combat, caster_id='player_mage', spell=_spell(name), target_ids=targets,
        cast_id=cast_id or f"cast_{name.replace(' ', '_')}", roller=roller, cast_level=cast_level, caster_resources={},
        **options,
    )
    assert result['ok'] is True, result.get('reason')
    return result


def _by_id(result_or_combat: dict, participant_id: str) -> dict:
    combat = result_or_combat.get('combat', result_or_combat)
    return next(participant for participant in combat['participants'] if participant['id'] == participant_id)


def _effects(result: dict, index: int = 0) -> list[dict]:
    return result['resolution']['targets'][index]['effects']


def _only_effect(participant: dict) -> dict:
    (effect,) = modifiers.active_effects(participant)
    return effect


def _concentrating_on(result: dict, name: str) -> None:
    assert result['resolution']['concentration']['started'] is True
    assert result['casterResources']['concentration']['spellName'].casefold() == name.casefold()


# One resolution check per catalog row.
CASES: dict[str, Callable[[], None]] = {}


def case(name: str):
    def register(fn: Callable[[], None]) -> Callable[[], None]:
        CASES[name] = fn
        return fn
    return register


@case('fire bolt')
def _fire_bolt():
    result = _cast('fire bolt', _combat(_caster(), _enemy('enemy_1', band='distant')), ['enemy_1'])
    damage = _effects(result)[0]
    assert result['resolution']['targets'][0]['delivery']['type'] == 'attack'
    assert (damage['roll']['dice'], damage['damageType'], damage['amountApplied']) == ('2d10', 'fire', 6)


@case('ray of frost')
def _ray_of_frost():
    result = _cast('ray of frost', _combat(_caster(), _enemy('enemy_1', band='far')), ['enemy_1'])
    assert (_effects(result)[0]['roll']['dice'], _effects(result)[0]['damageType']) == ('2d8', 'cold')
    assert _only_effect(_by_id(result, 'enemy_1'))['duration'] == {'rounds': 1, 'tickOn': 'target_turn_end'}
    assert 'slowed' in _by_id(result, 'enemy_1')['conditions']


@case('shocking grasp')
def _shocking_grasp():
    result = _cast('shocking grasp', _combat(_caster(), _enemy('enemy_1', band='melee')), ['enemy_1'])
    assert _effects(result)[0]['amountApplied'] == 6
    effect = _only_effect(_by_id(result, 'enemy_1'))
    assert effect['modifiers'] == {'cannotReact': True}
    assert effect['duration'] == {'rounds': 1, 'tickOn': 'target_turn_start'}


@case('chill touch')
def _chill_touch():
    result = _cast('chill touch', _combat(_caster(), _enemy('enemy_1', band='distant')), ['enemy_1'])
    assert _effects(result)[0]['damageType'] == 'necrotic'
    assert _only_effect(_by_id(result, 'enemy_1'))['duration'] == {'rounds': 1, 'tickOn': 'caster_turn_start'}
    assert modifiers.heals_nothing(_by_id(result, 'enemy_1'))


@case('poison spray')
def _poison_spray():
    result = _cast('poison spray', _combat(_caster(), _enemy('enemy_1')), ['enemy_1'])
    delivery = result['resolution']['targets'][0]['delivery']
    assert (delivery['ability'], delivery['dc'], delivery['saveSucceeded']) == ('constitution', 14, False)
    assert (_effects(result)[0]['roll']['dice'], _effects(result)[0]['amountApplied']) == ('2d12', 6)


@case('sacred flame')
def _sacred_flame():
    behind_wall = _enemy('enemy_1', position={'rangeBand': 'near', 'zoneId': 'hall', 'coverId': 'wall'})
    result = _cast('sacred flame', _combat(_caster(), behind_wall), ['enemy_1'])
    assert result['resolution']['targets'][0]['delivery']['ability'] == 'dexterity'
    assert (_effects(result)[0]['damageType'], _effects(result)[0]['amountApplied']) == ('radiant', 6)


@case('toll the dead')
def _toll_the_dead():
    result = _cast('toll the dead', _combat(_caster(), _enemy('enemy_1', hp=20, maximum=30)), ['enemy_1'])
    assert _effects(result)[0]['roll']['dice'] == '2d12'
    fresh = _cast('toll the dead', _combat(_caster(), _enemy('enemy_1')), ['enemy_1'])
    assert _effects(fresh)[0]['roll']['dice'] == '2d8'


@case('eldritch blast')
def _eldritch_blast():
    result = _cast('eldritch blast', _combat(_caster(), _enemy('enemy_1'), _enemy('enemy_2')), ['enemy_1', 'enemy_2'])
    beams = result['resolution']['targets']
    assert [(beam['targetId'], beam['attacksTotal']) for beam in beams] == [('enemy_1', 2), ('enemy_2', 2)]
    assert [beam['effects'][0]['roll']['dice'] for beam in beams] == ['1d10', '1d10']


@case('vicious mockery')
def _vicious_mockery():
    result = _cast('vicious mockery', _combat(_caster(), _enemy('enemy_1')), ['enemy_1'])
    effect = _only_effect(_by_id(result, 'enemy_1'))
    assert (effect['modifiers'], effect['consumeOn']) == ({'attackMode': 'disadvantage'}, 'attack')
    assert _effects(result)[0]['roll']['dice'] == '2d4'


@case('produce flame')
def _produce_flame():
    result = _cast('produce flame', _combat(_caster(), _enemy('enemy_1')), ['enemy_1'])
    assert (_effects(result)[0]['roll']['dice'], _effects(result)[0]['damageType']) == ('2d8', 'fire')
    far = resolve_targeted_spell(_combat(_caster(), _enemy('enemy_1', band='far')), caster_id='player_mage',
                                 spell=_spell('produce flame'), target_ids=['enemy_1'], cast_id='far', roller=steady)
    assert far['code'] == 'spell_target_out_of_range'


@case('thorn whip')
def _thorn_whip():
    result = _cast('thorn whip', _combat(_caster(), _enemy('enemy_1')), ['enemy_1'])
    assert _effects(result)[0]['roll']['dice'] == '2d6'
    assert (_effects(result)[1]['fromRangeBand'], _effects(result)[1]['toRangeBand']) == ('near', 'melee')


@case('guidance')
def _guidance():
    party = _combat(_caster(), _ally(), status='none')
    result = _cast('guidance', party, ['player_ally'])
    effect = _only_effect(_by_id(result, 'player_ally'))
    assert (effect['modifiers'], effect['consumeOn']) == ({'checkBonusDice': '1d4'}, 'check')
    assert effect['duration'] == {'rounds': 10, 'tickOn': 'target_turn_end'}
    _concentrating_on(result, 'Guidance')
    check = modifiers.check_context(_by_id(result, 'player_ally'), ability='wisdom')
    assert check['bonusDice'] == ['+1d4'] and check['consume']


@case('spare the dying')
def _spare_the_dying():
    dying = _ally(hp=0, maximum=20, conditions=['unconscious', 'dying'])
    result = _cast('spare the dying', _combat(_caster(), dying, status='none'), ['player_ally'])
    assert vitality.is_stable(_by_id(result, 'player_ally'))


@case('magic missile')
def _magic_missile():
    result = _cast('magic missile', _combat(_caster(), _enemy('enemy_1'), _enemy('enemy_2')), ['enemy_1', 'enemy_2'], cast_level=2)
    assert [(dart['targetId'], dart['dart']) for dart in result['resolution']['targets']] == [
        ('enemy_1', 1), ('enemy_2', 2), ('enemy_1', 3), ('enemy_2', 4),
    ]
    assert _by_id(result, 'enemy_1')['hp']['current'] == 30 - 8


@case('burning hands')
def _burning_hands():
    result = _cast('burning hands', _combat(_caster(), _enemy('enemy_1'), _enemy('enemy_2'), _enemy('enemy_3', band='far')), ['enemy_1'])
    assert result['resolution']['targetIds'] == ['enemy_1', 'enemy_2']
    first, second = (target['effects'][0] for target in result['resolution']['targets'])
    assert (first['amountApplied'], second['sharedRollReused']) == (9, True)


@case('thunderwave')
def _thunderwave():
    result = _cast('thunderwave', _combat(_caster(), _enemy('enemy_1', band='melee')), ['enemy_1'], cast_level=2)
    assert _effects(result)[0]['roll']['dice'] == '3d8'
    assert _by_id(result, 'enemy_1')['position']['rangeBand'] == 'near'


@case('chromatic orb')
def _chromatic_orb():
    result = _cast('chromatic orb', _combat(_caster(), _enemy('enemy_1', band='distant')), ['enemy_1'],
                   cast_level=2, damage_type='cold')
    assert result['resolution']['damageType'] == 'cold'
    assert (_effects(result)[0]['roll']['dice'], _effects(result)[0]['damageType']) == ('4d8', 'cold')
    default = _cast('chromatic orb', _combat(_caster(), _enemy('enemy_1')), ['enemy_1'], damage_type='radiant')
    assert _effects(default)[0]['damageType'] == 'fire'


@case('sleep')
def _sleep():
    combat = _combat(_caster(), _enemy('enemy_tough', hp=30), _enemy('enemy_mid', hp=10), _enemy('enemy_weak', hp=5))
    result = _cast('sleep', combat, ['enemy_tough'])
    assert result['resolution']['targetIds'] == ['enemy_weak', 'enemy_mid', 'enemy_tough']
    pools = [target['effects'][0] for target in result['resolution']['targets']]
    assert [pool['poolRemaining'] for pool in pools] == [10, 0, 0]
    assert pools[0]['poolRoll']['total'] == 15 and pools[2]['reason'] == 'pool_exhausted'
    assert 'unconscious' in _by_id(result, 'enemy_mid')['conditions']
    assert 'unconscious' not in _by_id(result, 'enemy_tough')['conditions']
    assert _only_effect(_by_id(result, 'enemy_weak'))['duration'] == {'rounds': 10, 'tickOn': 'target_turn_end'}


@case('mage armor')
def _mage_armor():
    breakdown = {'armorClass': 12, 'gearArmorClass': 12, 'armorKind': 'none', 'source': 'unarmored', 'dexModifier': 2, 'shieldBonus': 0}
    ally = _ally(armorClassBreakdown=breakdown)
    result = _cast('mage armor', _combat(_caster(), ally, status='none'), ['player_ally'])
    effect = _only_effect(_by_id(result, 'player_ally'))
    assert (effect['modifiers'], effect['until'], effect['duration']) == ({'acBase': 13}, 'long_rest', None)
    assert _by_id(result, 'player_ally')['armorClass'] == 15


@case('false life')
def _false_life():
    result = _cast('false life', _combat(_caster(), status='none'), ['player_mage'], cast_level=2)
    assert (_effects(result)[0]['roll']['dice'], _by_id(result, 'player_mage')['hp']['temp']) == ('1d4+9', 12)


@case('shield')
def _shield():
    actor = {'id': 'player_mage', 'name': 'Mage', 'class': 'Wizard', 'level': 3,
             'spellbook': {'knownSpells': [{'name': 'Shield', 'level': 1}], 'preparedSpells': ['Shield']},
             'spellResources': {'slots': {'1': {'current': 1, 'max': 4}}}}
    event = {'type': 'attack_hit', 'attackerId': 'enemy_1', 'targetId': 'player_mage', 'attackTotal': 15,
             'targetAc': 12, 'damage': 5, 'critical': False, 'eventId': 'hit', 'turnId': 3}
    (reaction,) = shield_reaction_provider(event, _combat(_caster(), _enemy('enemy_1')), {'playerCharacters': [actor]})
    ward, cast = reaction['changes']
    assert reaction['cancelsHit'] is True and reaction['consumes'] == {'reaction': 1, 'spellSlot': 1}
    assert (ward['effect']['modifiers'], ward['effect']['duration']) == ({'acBonus': 5}, {'rounds': 1, 'tickOn': 'target_turn_start'})
    assert (cast['type'], cast['castingTime'], cast['castLevel'], cast['resourcePool']) == ('spell.cast', 'reaction', 1, 'standard')


@case('cure wounds')
def _cure_wounds():
    result = _cast('cure wounds', _combat(_caster(), _ally(hp=5, maximum=20, band='melee')), ['player_ally'])
    healing = _effects(result)[0]
    assert (healing['roll']['dice'], healing['roll']['castingModifier'], healing['amountApplied']) == ('1d8', 3, 6)


@case('healing word')
def _healing_word():
    result = _cast('healing word', _combat(_caster(), _ally(hp=5, maximum=20, band='far')), ['player_ally'])
    assert result['resolution']['castingTime'] == 'bonus_action'
    assert _effects(result)[0]['amountApplied'] == 6


@case('bless')
def _bless():
    allies = [_ally(f'player_{index}') for index in range(3)]
    result = _cast('bless', _combat(_caster(), *allies), ['player_mage', 'player_0', 'player_1', 'player_2'], cast_level=2)
    _concentrating_on(result, 'Bless')
    assert all(
        modifiers.attack_context(participant, None, kind='weapon_attack', ranged=False)['attackBonusDice'] == ['+1d4']
        for participant in result['combat']['participants']
    )


@case('bane')
def _bane():
    result = _cast('bane', _combat(_caster(), _enemy('enemy_1')), ['enemy_1'])
    assert result['resolution']['targets'][0]['delivery']['ability'] == 'charisma'
    assert modifiers.save_context(_by_id(result, 'enemy_1'), 'wisdom')['bonusDice'] == ['-1d4']
    _concentrating_on(result, 'Bane')


@case('shield of faith')
def _shield_of_faith():
    result = _cast('shield of faith', _combat(_caster(), _ally(band='far')), ['player_ally'])
    effect = _only_effect(_by_id(result, 'player_ally'))
    assert (effect['modifiers'], effect['duration']['rounds']) == ({'acBonus': 2}, 100)
    assert result['resolution']['castingTime'] == 'bonus_action'


@case('guiding bolt')
def _guiding_bolt():
    result = _cast('guiding bolt', _combat(_caster(), _enemy('enemy_1')), ['enemy_1'], cast_level=2)
    assert _effects(result)[0]['roll']['dice'] == '5d6'
    effect = _only_effect(_by_id(result, 'enemy_1'))
    assert (effect['modifiers'], effect['consumeOn']) == ({'attackedMode': 'advantage'}, 'attacked')


@case('inflict wounds')
def _inflict_wounds():
    result = _cast('inflict wounds', _combat(_caster(), _enemy('enemy_1', band='melee')), ['enemy_1'])
    assert (_effects(result)[0]['roll']['dice'], _effects(result)[0]['amountApplied']) == ('3d10', 9)
    assert result['resolution']['targets'][0]['delivery']['mode'] == 'normal'


@case('command')
def _command():
    result = _cast('command', _combat(_caster(), _enemy('enemy_1'), _enemy('enemy_2')), ['enemy_1', 'enemy_2'], cast_level=2)
    assert not modifiers.can_act(_by_id(result, 'enemy_1')) and not modifiers.can_act(_by_id(result, 'enemy_2'))


@case('entangle')
def _entangle():
    enemies = [_enemy(f'enemy_{index}', band='far') for index in range(5)]
    result = _cast('entangle', _combat(_caster(), *enemies), ['enemy_0'])
    assert result['resolution']['targetIds'] == ['enemy_0', 'enemy_1', 'enemy_2', 'enemy_3']
    restrained = _by_id(result, 'enemy_0')
    assert 'restrained' in restrained['conditions']
    recurring = next(effect for effect in modifiers.active_effects(restrained) if effect.get('recurring'))
    assert recurring['recurring'] == {'ability': 'strength', 'dc': 14, 'repeatSave': True}
    assert recurring['duration'] == {'rounds': 10, 'tickOn': 'target_turn_end'}
    _concentrating_on(result, 'Entangle')


@case('faerie fire')
def _faerie_fire():
    result = _cast('faerie fire', _combat(_caster(), _enemy('enemy_1', conditions=['invisible']), _enemy('enemy_2')), ['enemy_1'])
    lit = _by_id(result, 'enemy_1')
    assert 'invisible' not in lit['conditions']
    assert modifiers.attack_context(_caster(), lit, kind='weapon_attack', ranged=False)['mode'] == 'advantage'
    assert _by_id(result, 'enemy_2')['activeEffects'][0]['modifiers'] == {'attackedMode': 'advantage'}


@case("hunter's mark")
def _hunters_mark():
    result = _cast("hunter's mark", _combat(_caster(), _enemy('enemy_1', band='distant')), ['enemy_1'])
    mark = _only_effect(_by_id(result, 'player_mage'))
    assert mark['modifiers']['bonusDamageVs'] == {'dice': '1d6', 'weaponOnly': True, 'targetId': 'enemy_1'}
    assert mark['until'] == 'encounter'
    extra = modifiers.extra_damage(_by_id(result, 'player_mage'), _by_id(result, 'enemy_1'), kind='weapon_attack', weapon={'damageType': 'piercing'})
    assert extra[0]['dice'] == '1d6' and extra[0]['type'] == 'piercing'
    blast = _cast('eldritch blast', result['combat'], ['enemy_1'])
    assert [effect.get('source') for effect in _effects(blast)] == [None]


@case('hex')
def _hex():
    hexed = _cast('hex', _combat(_caster(), _enemy('enemy_1')), ['enemy_1'])
    _concentrating_on(hexed, 'Hex')
    blast = _cast('eldritch blast', hexed['combat'], ['enemy_1'])
    for beam in blast['resolution']['targets']:
        assert [(effect['damageType'], str(effect.get('source')).casefold()) for effect in beam['effects']] == [('force', 'none'), ('necrotic', 'hex')]


@case('armor of agathys')
def _armor_of_agathys():
    result = _cast('armor of agathys', _combat(_caster(), status='none'), ['player_mage'], cast_level=2)
    assert _by_id(result, 'player_mage')['hp']['temp'] == 10


@case('charm person')
def _charm_person():
    result = _cast('charm person', _combat(_caster(), _ally(), _enemy('enemy_1')), ['enemy_1'])
    delivery = result['resolution']['targets'][0]['delivery']
    assert (delivery['mode'], len(delivery['rolls'])) == ('advantage', 2)
    charmed = _by_id(result, 'enemy_1')
    assert modifiers.attack_block_reason(charmed, _by_id(result, 'player_mage'))
    after_damage_spell_effects(result['combat'], target_id='enemy_1', source_id='enemy_1', event_id='self_harm')
    assert 'charmed' in _by_id(result, 'enemy_1')['conditions']
    after_damage_spell_effects(result['combat'], target_id='enemy_1', source_id='player_ally', event_id='hit')
    assert 'charmed' not in _by_id(result, 'enemy_1')['conditions']


@case('dissonant whispers')
def _dissonant_whispers():
    result = _cast('dissonant whispers', _combat(_caster(), _enemy('enemy_1')), ['enemy_1'])
    assert (_effects(result)[0]['amountApplied'], _effects(result)[1]['toRangeBand']) == (9, 'far')


@case("tasha's hideous laughter")
def _tashas_hideous_laughter():
    result = _cast("tasha's hideous laughter", _combat(_caster(), _enemy('enemy_1')), ['enemy_1'])
    laughing = _by_id(result, 'enemy_1')
    assert {'prone', 'incapacitated'} <= set(laughing['conditions'])
    events = after_damage_spell_effects(result['combat'], target_id='enemy_1', source_id='player_mage', event_id='hit',
                                        roller=lambda sides: 19)
    (event,) = events
    assert (event['kind'], event['save']['mode'], event['save']['succeeded']) == ('repeat_save', 'advantage', True)
    assert not {'prone', 'incapacitated'} & set(_by_id(result, 'enemy_1')['conditions'])


@case('heroism')
def _heroism():
    result = _cast('heroism', _combat(_caster(), _ally(band='melee')), ['player_ally'])
    effect = _only_effect(_by_id(result, 'player_ally'))
    assert effect['modifiers'] == {'tempHpPerTurn': 3, 'conditionImmunities': ['frightened']}
    apply_turn_boundaries(result['combat'], [{'actorId': 'player_ally', 'phase': 'start'}], turn_id=2)
    assert _by_id(result, 'player_ally')['hp']['temp'] == 3


@case('divine favor')
def _divine_favor():
    result = _cast('divine favor', _combat(_caster()), ['player_mage'])
    extra = modifiers.extra_damage(_by_id(result, 'player_mage'), None, kind='weapon_attack')
    assert [(component['dice'], component['type']) for component in extra] == [('1d4', 'radiant')]


@case('grease')
def _grease():
    result = _cast('grease', _combat(_caster(), _enemy('enemy_1'), _enemy('enemy_2'), _enemy('enemy_3'), _enemy('enemy_4')), ['enemy_1'])
    assert len(result['resolution']['targetIds']) == 3
    assert all('prone' in _by_id(result, target)['conditions'] for target in result['resolution']['targetIds'])


@case('scorching ray')
def _scorching_ray():
    result = _cast('scorching ray', _combat(_caster(), _enemy('enemy_1'), _enemy('enemy_2')), ['enemy_1', 'enemy_2'], cast_level=3)
    assert [ray['targetId'] for ray in result['resolution']['targets']] == ['enemy_1', 'enemy_2', 'enemy_1', 'enemy_2']
    assert _by_id(result, 'enemy_1')['hp']['current'] == 30 - 12


@case('shatter')
def _shatter():
    result = _cast('shatter', _combat(_caster(), *[_enemy(f'enemy_{index}') for index in range(5)]), ['enemy_0'])
    assert len(result['resolution']['targetIds']) == 4
    assert _effects(result)[0]['amountApplied'] == 9


@case('hold person')
def _hold_person():
    result = _cast('hold person', _combat(_caster(), _enemy('enemy_1'), _enemy('enemy_2')), ['enemy_1', 'enemy_2'], cast_level=3)
    assert all('paralyzed' in _by_id(result, target)['conditions'] for target in ('enemy_1', 'enemy_2'))
    assert next(effect for effect in modifiers.active_effects(_by_id(result, 'enemy_1')) if effect.get('recurring'))['recurring']['ability'] == 'wisdom'
    _concentrating_on(result, 'Hold Person')


@case('misty step')
def _misty_step():
    result = _cast('misty step', _combat(_caster(band='melee')), ['player_mage'])
    assert _by_id(result, 'player_mage')['position']['rangeBand'] == 'far'
    assert result['resolution']['castingTime'] == 'bonus_action'


@case('invisibility')
def _invisibility():
    result = _cast('invisibility', _combat(_caster(), _ally(), status='none'), ['player_ally'])
    effect = _only_effect(_by_id(result, 'player_ally'))
    assert (effect['condition'], effect['until'], effect['consumeOn'], effect['endsOnCast']) == ('invisible', 'encounter', 'attack', True)
    combat = deepcopy(result['combat'])
    combat['status'] = 'active'
    combat['flags'] = {'activeActorId': 'player_ally'}
    combat['participants'].append(_enemy('enemy_1'))
    cast = resolve_targeted_spell(combat, caster_id='player_ally', spell=_spell('fire bolt'), target_ids=['enemy_1'],
                                  cast_id='reveal', roller=steady)
    assert cast['resolution']['endedOnCast'] == [effect['id']]
    assert 'invisible' not in _by_id(cast, 'player_ally')['conditions']


@case('web')
def _web():
    result = _cast('web', _combat(_caster(), *[_enemy(f'enemy_{index}') for index in range(5)]), ['enemy_0'])
    assert len(result['resolution']['targetIds']) == 4
    assert result['resolution']['targets'][0]['delivery']['ability'] == 'dexterity'
    recurring = next(effect for effect in modifiers.active_effects(_by_id(result, 'enemy_0')) if effect.get('recurring'))
    assert recurring['recurring']['ability'] == 'strength'


@case('blindness/deafness')
def _blindness_deafness():
    result = _cast('blindness/deafness', _combat(_caster(), _enemy('enemy_1')), ['enemy_1'])
    assert 'blinded' in _by_id(result, 'enemy_1')['conditions']
    assert result['resolution']['concentration']['started'] is False
    assert next(effect for effect in modifiers.active_effects(_by_id(result, 'enemy_1')) if effect.get('recurring'))['recurring']['ability'] == 'constitution'


@case('aid')
def _aid():
    allies = [_ally('player_a', hp=10, maximum=20), _ally('player_b', hp=20)]
    result = _cast('aid', _combat(_caster(), *allies, status='none'), ['player_a', 'player_b'], cast_level=3)
    assert _by_id(result, 'player_a')['hp'] == {'current': 20, 'max': 30, 'temp': 0}
    assert _only_effect(_by_id(result, 'player_b'))['until'] == 'long_rest'


@case('lesser restoration')
def _lesser_restoration():
    result = _cast('lesser restoration', _combat(_caster(), _ally(conditions=['poisoned']), status='none'), ['player_ally'])
    assert _by_id(result, 'player_ally')['conditions'] == []


@case('prayer of healing')
def _prayer_of_healing():
    allies = [_ally(f'player_{index}', hp=1, maximum=20) for index in range(5)]
    result = _cast('prayer of healing', _combat(_caster(hp=1, maximum=20), *allies, status='none'),
                   ['player_mage', *[ally['id'] for ally in allies]])
    assert [target['effects'][0]['amountApplied'] for target in result['resolution']['targets']] == [9] * 6
    in_combat = resolve_targeted_spell(_combat(_caster(), _ally()), caster_id='player_mage', spell=_spell('prayer of healing'),
                                       target_ids=['player_ally'], cast_id='no', roller=steady)
    assert in_combat['code'] == 'spell_combat_forbidden'


@case('spiritual weapon')
def _spiritual_weapon():
    result = _cast('spiritual weapon', _combat(_caster(), _enemy('enemy_1', band='far')), ['enemy_1'], cast_level=4)
    damage = _effects(result)[0]
    assert (damage['roll']['dice'], damage['roll']['castingModifier'], damage['amountApplied']) == ('2d8', 3, 9)
    assert result['resolution']['targets'][0]['delivery']['mode'] == 'normal'
    weapon = _only_effect(_by_id(result, 'player_mage'))
    assert weapon['repeatAttack'] == {'castLevel': 4} and weapon['duration']['rounds'] == 10
    again = _cast('spiritual weapon', result['combat'], ['enemy_1'], cast_level=4, repeat_attack=True, cast_id='again')
    assert again['resolution']['repeatAttack'] is True and len(_effects(again)) == 1
    assert _only_effect(_by_id(again, 'player_mage'))['id'] == weapon['id']


@case('moonbeam')
def _moonbeam():
    result = _cast('moonbeam', _combat(_caster(), _enemy('enemy_1', band='distant'), _enemy('enemy_2', band='distant')), ['enemy_1'], cast_level=3)
    assert _effects(result)[0]['roll']['dice'] == '3d10'
    assert _by_id(result, 'enemy_1')['hp']['current'] == 21
    beam = next(effect for effect in modifiers.active_effects(_by_id(result, 'enemy_2')) if effect.get('recurring'))
    assert beam['recurring']['damage'] == {'dice': '3d10', 'type': 'radiant'}
    (burn,) = resolve_turn_start_spell_effects(result['combat'], actor_id='enemy_2', turn_id=4, round_number=1, roller=steady)
    assert (burn['kind'], burn['damage']['amountApplied']) == ('recurring_damage', 9)


@case('barkskin')
def _barkskin():
    result = _cast('barkskin', _combat(_caster(), _ally(), status='none'), ['player_ally'])
    effect = _only_effect(_by_id(result, 'player_ally'))
    assert (effect['modifiers'], effect['until']) == ({'acMinimum': 16}, 'encounter')
    attack = modifiers.attack_context(_enemy('enemy_1'), _by_id(result, 'player_ally'), kind='enemy_attack', ranged=False)
    assert modifiers.armor_class_with_context(12, attack) == 16


@case('fireball')
def _fireball():
    result = _cast('fireball', _combat(_caster(), *[_enemy(f'enemy_{index}', band='distant') for index in range(7)]), ['enemy_0'])
    assert len(result['resolution']['targetIds']) == 6
    assert _effects(result)[0]['amountApplied'] == 24


@case('lightning bolt')
def _lightning_bolt():
    result = _cast('lightning bolt', _combat(_caster(), *[_enemy(f'enemy_{index}') for index in range(4)]), ['enemy_0'], cast_level=4)
    assert len(result['resolution']['targetIds']) == 3
    assert (_effects(result)[0]['roll']['dice'], _effects(result)[0]['damageType']) == ('9d6', 'lightning')


@case('hypnotic pattern')
def _hypnotic_pattern():
    result = _cast('hypnotic pattern', _combat(_caster(), _enemy('enemy_1'), _enemy('enemy_2')), ['enemy_1'])
    assert {'incapacitated', 'charmed'} <= set(_by_id(result, 'enemy_2')['conditions'])
    after_damage_spell_effects(result['combat'], target_id='enemy_2', source_id='player_mage', event_id='slap')
    assert not {'incapacitated', 'charmed'} & set(_by_id(result, 'enemy_2')['conditions'])
    assert 'incapacitated' in _by_id(result, 'enemy_1')['conditions']


@case('fear')
def _fear():
    result = _cast('fear', _combat(_caster(), _enemy('enemy_1'), _enemy('enemy_2', band='far')), ['enemy_1'])
    assert result['resolution']['targetIds'] == ['enemy_1']
    frightened = _by_id(result, 'enemy_1')
    attack = modifiers.attack_context(frightened, _by_id(result, 'player_mage'), kind='enemy_attack', ranged=False, combat=result['combat'])
    assert 'Attacker is frightened' in attack['reasons']
    assert next(effect for effect in modifiers.active_effects(frightened) if effect.get('condition') == 'frightened')['casterId'] == 'player_mage'


@case('revivify')
def _revivify():
    dead = _ally(hp=0, maximum=20, conditions=['dead'], isAlive=False)
    result = _cast('revivify', _combat(_caster(), dead, status='none'), ['player_ally'])
    revived = _by_id(result, 'player_ally')
    assert (revived['hp']['current'], revived['conditions'], revived['isAlive']) == (1, [], True)
    living = resolve_targeted_spell(_combat(_caster(), _ally(), status='none'), caster_id='player_mage', spell=_spell('revivify'),
                                    target_ids=['player_ally'], cast_id='alive', roller=steady)
    assert living['code'] == 'spell_target_not_dead'


@case('mass healing word')
def _mass_healing_word():
    allies = [_ally(f'player_{index}', hp=1, maximum=20, band='far') for index in range(6)]
    result = _cast('mass healing word', _combat(_caster(), *allies), [ally['id'] for ally in allies])
    assert [target['effects'][0]['amountApplied'] for target in result['resolution']['targets']] == [6] * 6


@case('spirit guardians')
def _spirit_guardians():
    combat = _combat(_caster(), _enemy('enemy_near'), _enemy('enemy_far', band='far'), _ally())
    result = _cast('spirit guardians', combat, ['player_mage'], cast_level=4)
    aura = _only_effect(_by_id(result, 'player_mage'))
    assert aura['recurring']['aura'] == {'relation': 'enemy', 'rangeBands': ['melee', 'near']}
    assert aura['duration']['rounds'] == 100
    (burn,) = resolve_turn_start_spell_effects(result['combat'], actor_id='enemy_near', turn_id=5, round_number=1, roller=steady)
    assert (burn['kind'], burn['damage']['roll']['dice'], burn['damage']['amountApplied']) == ('aura_damage', '4d8', 12)
    assert resolve_turn_start_spell_effects(result['combat'], actor_id='enemy_far', turn_id=5, round_number=1, roller=steady) == []
    assert resolve_turn_start_spell_effects(result['combat'], actor_id='player_ally', turn_id=5, round_number=1, roller=steady) == []


@case('dispel magic')
def _dispel_magic():
    blessed = _cast('bless', _combat(_caster(), _ally(hp=10, maximum=20)), ['player_ally'])
    aided = _cast('aid', blessed['combat'], ['player_ally'], cast_level=4)
    assert _by_id(aided, 'player_ally')['hp']['max'] == 35
    result = _cast('dispel magic', aided['combat'], ['player_ally'])
    removed = result['resolution']['targets'][0]['effects'][0]['removedEffects']
    assert [effect['sourceName'].casefold() for effect in removed] == ['bless']
    stronger = _cast('dispel magic', aided['combat'], ['player_ally'], cast_level=4)
    assert _by_id(stronger, 'player_ally')['hp']['max'] == 20 and modifiers.active_effects(_by_id(stronger, 'player_ally')) == []


def test_every_catalog_row_has_one_resolution_case():
    assert len(AUTHORITATIVE_SPELL_EFFECTS) == 61
    assert set(CASES) == set(AUTHORITATIVE_SPELL_EFFECTS) == set(TABLE)


@pytest.mark.parametrize('name', sorted(AUTHORITATIVE_SPELL_EFFECTS))
def test_spell_resolves_as_its_catalog_row(name):
    CASES[name]()


@pytest.mark.parametrize('name', sorted(AUTHORITATIVE_SPELL_EFFECTS))
def test_definition_validates_and_matches_the_table(name):
    level, casting_time, concentration, out_of_combat, classes = TABLE[name]
    payload = _spell(name)
    normalized, error = normalized_spell_definition(payload)
    assert normalized is not None, error
    assert (payload['level'], payload['castingTime'], payload['concentration'], payload['outOfCombat']) == (
        level, casting_time, concentration, out_of_combat,
    )
    assert set(payload['classes']) == classes and payload['school']
    assert payload['rulesBacked'] is True and payload['authoritativeEffect'] is True and payload['narrated'] is False
    assert payload['effectSummary']
    if level == 0:
        assert payload['scaling']['cantrip'] is True


@pytest.mark.parametrize('name', sorted(AUTHORITATIVE_SPELL_EFFECTS))
def test_out_of_combat_legality_follows_the_definition(name):
    party = _combat(_caster(), _ally(), _enemy('enemy_1'), status='none')
    result = resolve_targeted_spell(party, caster_id='player_mage', spell=_spell(name), target_ids=['player_ally'],
                                    cast_id='ooc', roller=steady)
    if TABLE[name][3]:
        assert result['code'] != 'spell_combat_inactive'
    else:
        assert result['code'] == 'spell_combat_inactive'


def test_fire_bolt_summary_reads_as_one_line():
    assert spell_payload('Fire Bolt')['effectSummary'] == 'Ranged spell attack · 1d10 fire · scales with level'


@pytest.mark.parametrize('archetype', ARCHETYPES)
def test_class_lists_contain_exactly_the_catalog_classes(archetype):
    listed = {
        name.casefold()
        for name, level, _description in [
            *CLASS_STARTING_SPELLS[archetype],
            *(spec for specs in CLASS_LEVEL_UNLOCKS[archetype].values() for spec in specs),
        ]
        if level <= 3
    }
    catalog = {**AUTHORITATIVE_SPELL_EFFECTS, **NARRATED_SPELLS}
    assert listed == {name for name, entry in catalog.items() if archetype in entry['classes']}
    assert {'lay on hands', 'divine smite', 'divine sense', 'wild shape'}.isdisjoint(listed)


def test_class_lists_unlock_spell_levels_at_the_specified_character_levels():
    def names(class_name: str, level: int) -> set[str]:
        return {spell['name'] for spell in class_spells_for_level(class_name, level) if spell.get('catalog') != 'aidm-original'}

    assert 'Fire Bolt' in names('Wizard', 1) and 'Magic Missile' in names('Wizard', 1)
    assert 'Scorching Ray' not in names('Wizard', 2) and 'Scorching Ray' in names('Wizard', 3)
    assert 'Fireball' not in names('Wizard', 4) and 'Fireball' in names('Wizard', 5)
    assert 'Cure Wounds' not in names('Paladin', 1) and 'Cure Wounds' in names('Paladin', 2)
    assert "Hunter's Mark" not in names('Ranger', 1) and "Hunter's Mark" in names('Ranger', 2)
    assert 'Aid' not in names('Paladin', 4) and 'Aid' in names('Paladin', 5)
    assert 'Invisibility' not in names('Artificer', 4) and 'Invisibility' in names('Artificer', 5)
    assert 'Grease' in names('Artificer', 1)
    assert 'Dimension Door' in names('Wizard', 7)


@pytest.mark.parametrize('name', sorted(NARRATED_SPELLS))
def test_narrated_spells_never_resolve_mechanically(name):
    payload = spell_payload(name, level=NARRATED_SPELLS[name]['level'])
    assert (payload['rulesBacked'], payload['authoritativeEffect'], payload['narrated']) == (False, False, True)
    assert payload['school'] == NARRATED_SPELLS[name]['school']
    result = resolve_targeted_spell(_combat(_caster(), _enemy('enemy_1')), caster_id='player_mage', spell=payload,
                                    target_ids=['enemy_1'], cast_id='narrated', roller=steady)
    assert result['ok'] is False and result['code'] == 'spell_definition_invalid'


def test_new_characters_get_three_narrated_generated_spells():
    originals = [spell for spell in class_spells_for_level('Cleric', 9) if spell.get('catalog') == 'aidm-original']
    assert len(originals) == 3 and all(spell['narrated'] is True for spell in originals)


def test_rules_backed_payload_fields_are_present_on_every_known_spell():
    for name in AUTHORITATIVE_SPELL_EFFECTS:
        payload = spell_payload(name)
        assert {'rulesBacked', 'authoritativeEffect', 'castingTime', 'level', 'concentration', 'outOfCombat', 'effectSummary'} <= set(payload)
