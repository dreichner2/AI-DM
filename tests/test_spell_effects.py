from __future__ import annotations

from copy import deepcopy
import json

from aidm_server.spell_effects import (
    advance_spell_effect_durations,
    resolve_concentration_check,
    resolve_targeted_spell,
    spell_target_legality,
)


class SequenceRoller:
    def __init__(self, *values: int):
        self.values = list(values)
        self.sides: list[int] = []

    def __call__(self, sides: int) -> int:
        self.sides.append(sides)
        assert self.values, f'Unexpected d{sides} roll.'
        return self.values.pop(0)


def _participant(
    participant_id: str,
    *,
    team: str,
    hp: int = 12,
    maximum: int | None = None,
    temporary: int = 0,
    armor_class: int = 12,
    range_band: str = 'near',
    zone_id: str = 'hall',
    **extra: object,
) -> dict:
    return {
        'id': participant_id,
        'name': participant_id.replace('_', ' ').title(),
        'team': team,
        'kind': 'player_character' if team == 'player' else 'creature',
        'level': 5,
        'hp': {'current': hp, 'max': maximum if maximum is not None else hp, 'temp': temporary},
        'armorClass': armor_class,
        'stats': {
            'strength': 10,
            'dexterity': 14,
            'constitution': 14,
            'intelligence': 16,
            'wisdom': 10,
            'charisma': 10,
        },
        'conditions': [],
        'position': {'rangeBand': range_band, 'zoneId': zone_id},
        'isAlive': hp > 0,
        'isConscious': hp > 0,
        'isPresent': True,
        **extra,
    }


def _combat(*participants: dict, active_actor_id: str = 'player_mage') -> dict:
    return {
        'status': 'active',
        'round': 2,
        'turnIndex': 0,
        'participants': list(participants),
        'battlefield': {
            'environmentType': 'dungeon_room',
            'cover': [
                {'id': 'pillar', 'name': 'Pillar', 'coverType': 'half'},
                {'id': 'wall', 'name': 'Wall', 'coverType': 'full'},
            ],
        },
        'flags': {'activeActorId': active_actor_id},
    }


def _damage_spell(**overrides: object) -> dict:
    spell = {
        'id': 'spell_frost_bolt',
        'name': 'Frost Bolt',
        'delivery': {'type': 'attack', 'attackBonus': 5},
        'target': {'relation': 'enemy', 'rangeBands': ['near', 'far'], 'maxTargets': 1},
        'effects': [{'kind': 'damage', 'dice': '1d6', 'damageType': 'cold'}],
    }
    spell.update(overrides)
    return spell


def _resources(spell_id: str | None = None) -> dict:
    concentration = None
    if spell_id:
        concentration = {
            'active': True,
            'spellId': spell_id,
            'spellName': spell_id.replace('spell_', '').replace('_', ' ').title(),
            'casterActorId': 'player_mage',
            'targetIds': ['enemy_ogre'],
        }
    return {'revision': 3, 'slots': {'1': {'current': 1, 'max': 2}}, 'concentration': concentration}


def test_target_legality_fails_closed_for_turn_relation_range_presence_and_exact_ids() -> None:
    caster = _participant('player_mage', team='player')
    ally = _participant('player_ally', team='ally')
    enemy = _participant('enemy_ogre', team='enemy', range_band='distant')
    absent = _participant('enemy_absent', team='enemy', isPresent=False)
    combat = _combat(caster, ally, enemy, absent)
    spell = _damage_spell()

    missing = spell_target_legality(
        combat,
        caster_id='player_mage',
        spell=spell,
        target_ids=['enemy_stale'],
    )
    wrong_relation = spell_target_legality(
        combat,
        caster_id='player_mage',
        spell=spell,
        target_ids=['player_ally'],
    )
    distant = spell_target_legality(
        combat,
        caster_id='player_mage',
        spell=spell,
        target_ids=['enemy_ogre'],
    )
    absent_result = spell_target_legality(
        combat,
        caster_id='player_mage',
        spell=spell,
        target_ids=['enemy_absent'],
    )
    out_of_turn = spell_target_legality(
        _combat(caster, enemy, active_actor_id='enemy_ogre'),
        caster_id='player_mage',
        spell=spell,
        target_ids=['enemy_ogre'],
    )

    assert missing['code'] == 'spell_target_missing'
    assert wrong_relation['code'] == 'spell_target_relation_invalid'
    assert distant['code'] == 'spell_target_out_of_range'
    assert absent_result['code'] == 'spell_target_absent'
    assert out_of_turn['code'] == 'spell_out_of_turn'


def test_target_set_is_atomic_and_duplicate_or_one_invalid_target_rejects_all_without_rolls() -> None:
    caster = _participant('player_mage', team='player')
    first = _participant('enemy_one', team='enemy')
    hidden = _participant('enemy_hidden', team='enemy')
    hidden['position']['isHidden'] = True
    combat = _combat(caster, first, hidden)
    spell = _damage_spell(
        target={'relation': 'enemy', 'rangeBands': ['near'], 'minTargets': 2, 'maxTargets': 2}
    )

    duplicate = spell_target_legality(
        combat,
        caster_id='player_mage',
        spell=spell,
        target_ids=['enemy_one', 'enemy_one'],
    )
    roller = SequenceRoller(20, 6)
    invalid = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=spell,
        target_ids=['enemy_one', 'enemy_hidden'],
        cast_id='cast_atomic',
        roller=roller,
    )

    assert duplicate['code'] == 'spell_target_duplicate'
    assert invalid['ok'] is False
    assert invalid['code'] == 'spell_target_hidden'
    assert invalid['combat'] == combat
    assert roller.sides == []


def test_full_cover_blocks_line_of_sight_and_half_cover_changes_attack_ac() -> None:
    caster = _participant('player_mage', team='player')
    target = _participant('enemy_ogre', team='enemy', armor_class=12)
    target['position']['coverId'] = 'wall'
    combat = _combat(caster, target)

    blocked = spell_target_legality(
        combat,
        caster_id='player_mage',
        spell=_damage_spell(),
        target_ids=['enemy_ogre'],
    )
    assert blocked['code'] == 'spell_target_full_cover'

    target['position']['coverId'] = 'pillar'
    roller = SequenceRoller(8)
    result = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=_damage_spell(),
        target_ids=['enemy_ogre'],
        cast_id='cast_cover',
        roller=roller,
    )
    delivery = result['resolution']['targets'][0]['delivery']
    assert delivery['total'] == 13
    assert delivery['targetArmorClass'] == 14
    assert delivery['coverBonus'] == 2
    assert delivery['delivered'] is False
    assert roller.sides == [20]


def test_casting_ability_derives_spell_attack_bonus_and_save_dc_from_caster() -> None:
    caster = _participant('player_mage', team='player')
    attack_target = _participant('enemy_attack', team='enemy', armor_class=14)
    save_target = _participant('enemy_save', team='enemy', hp=12)
    combat = _combat(caster, attack_target, save_target)
    attack_spell = _damage_spell(
        delivery={'type': 'attack', 'castingAbility': 'intelligence'}
    )

    attack = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=attack_spell,
        target_ids=['enemy_attack'],
        cast_id='cast_derived_attack',
        roller=SequenceRoller(8, 3),
    )
    attack_delivery = attack['resolution']['targets'][0]['delivery']
    assert attack_delivery['modifier'] == 6  # Intelligence +3 and level-five proficiency +3.
    assert attack_delivery['total'] == 14
    assert attack_delivery['delivered'] is True

    save_spell = {
        'id': 'spell_mind_surge',
        'name': 'Mind Surge',
        'delivery': {
            'type': 'save',
            'ability': 'wisdom',
            'castingAbility': 'intelligence',
            'onSuccess': 'none',
        },
        'target': {'relation': 'enemy', 'rangeBands': ['near']},
        'effects': [{'kind': 'damage', 'amount': 3, 'damageType': 'psychic'}],
    }
    save = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=save_spell,
        target_ids=['enemy_save'],
        cast_id='cast_derived_save',
        roller=SequenceRoller(13),
    )
    save_delivery = save['resolution']['targets'][0]['delivery']
    assert save_delivery['dc'] == 14
    assert save_delivery['modifier'] == 0
    assert save_delivery['total'] == 13
    assert save_delivery['saveSucceeded'] is False


def test_critical_spell_attack_doubles_dice_then_applies_resistance_and_temp_hp() -> None:
    caster = _participant('player_mage', team='player')
    target = _participant(
        'enemy_ogre',
        team='enemy',
        hp=12,
        maximum=12,
        temporary=3,
        resistances=['cold'],
    )
    combat = _combat(caster, target)
    roller = SequenceRoller(20, 4, 4)

    result = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=_damage_spell(),
        target_ids=['enemy_ogre'],
        cast_id='cast_critical',
        roller=roller,
    )

    assert result['ok'] is True
    delivery = result['resolution']['targets'][0]['delivery']
    damage = result['resolution']['targets'][0]['effects'][0]
    updated = next(
        participant
        for participant in result['combat']['participants']
        if participant['id'] == 'enemy_ogre'
    )
    assert delivery['critical'] is True
    assert damage['roll']['rolls'] == [4, 4]
    assert damage['rolledAmount'] == 8
    assert damage['defense'] == 'resistant'
    assert damage['amountApplied'] == 4
    assert damage['tempHpDamage'] == 3
    assert damage['hpDamage'] == 1
    assert updated['hp'] == {'current': 11, 'max': 12, 'temp': 0}
    assert combat['participants'][1]['hp'] == {'current': 12, 'max': 12, 'temp': 3}
    assert roller.sides == [20, 6, 6]


def test_spell_attack_miss_never_rolls_or_applies_effects() -> None:
    caster = _participant('player_mage', team='player')
    target = _participant('enemy_ogre', team='enemy', armor_class=18)
    combat = _combat(caster, target)
    roller = SequenceRoller(4)

    result = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=_damage_spell(),
        target_ids=['enemy_ogre'],
        cast_id='cast_miss',
        roller=roller,
    )

    target_result = result['resolution']['targets'][0]
    assert target_result['delivery']['delivered'] is False
    assert target_result['effects'] == [
        {'kind': 'damage', 'applied': False, 'reason': 'spell_attack_missed'}
    ]
    assert result['combat']['participants'][1]['hp']['current'] == 12
    assert roller.sides == [20]


def test_failed_save_applies_full_damage_and_a_timed_condition() -> None:
    caster = _participant('player_mage', team='player')
    target = _participant('enemy_ogre', team='enemy', hp=20)
    combat = _combat(caster, target)
    spell = {
        'id': 'spell_binding_flame',
        'name': 'Binding Flame',
        'delivery': {'type': 'save', 'ability': 'dexterity', 'dc': 14, 'onSuccess': 'half'},
        'target': {'relation': 'enemy', 'rangeBands': ['near']},
        'effects': [
            {'kind': 'damage', 'dice': '2d6', 'damageType': 'fire'},
            {
                'kind': 'condition',
                'condition': 'restrained',
                'duration': {'remaining': 2, 'tick': 'target_turn_end'},
            },
        ],
    }
    roller = SequenceRoller(5, 4, 3)

    result = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=spell,
        target_ids=['enemy_ogre'],
        cast_id='cast_failed_save',
        roller=roller,
    )

    target_result = result['resolution']['targets'][0]
    updated = result['combat']['participants'][1]
    assert target_result['delivery']['saveSucceeded'] is False
    assert target_result['delivery']['modifier'] == 2
    assert target_result['effects'][0]['amountApplied'] == 7
    assert target_result['effects'][1]['applied'] is True
    assert updated['hp']['current'] == 13
    assert updated['conditions'] == ['restrained']
    assert updated['activeEffects'][0]['duration'] == {
        'remaining': 2,
        'tick': 'target_turn_end',
    }


def test_successful_save_halves_damage_and_blocks_condition_by_default() -> None:
    caster = _participant('player_mage', team='player')
    target = _participant('enemy_ogre', team='enemy', hp=20)
    combat = _combat(caster, target)
    spell = {
        'id': 'spell_thunder_bind',
        'name': 'Thunder Bind',
        'delivery': {'type': 'save', 'ability': 'dexterity', 'dc': 14, 'onSuccess': 'half'},
        'target': {'relation': 'enemy', 'rangeBands': ['near']},
        'effects': [
            {'kind': 'damage', 'dice': '2d6', 'damageType': 'thunder'},
            {'kind': 'condition', 'condition': 'restrained'},
        ],
    }
    roller = SequenceRoller(15, 4, 3)

    result = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=spell,
        target_ids=['enemy_ogre'],
        cast_id='cast_successful_save',
        roller=roller,
    )

    effects = result['resolution']['targets'][0]['effects']
    updated = result['combat']['participants'][1]
    assert effects[0]['rolledAmount'] == 7
    assert effects[0]['afterSaveAmount'] == 3
    assert effects[0]['amountApplied'] == 3
    assert effects[1] == {'kind': 'condition', 'applied': False, 'reason': 'successful_save'}
    assert updated['hp']['current'] == 17
    assert updated['conditions'] == []


def test_damage_immunity_and_vulnerability_are_authoritative() -> None:
    caster = _participant('player_mage', team='player')
    immune = _participant('enemy_immune', team='enemy', immunities=['fire'])
    vulnerable = _participant('enemy_vulnerable', team='enemy', vulnerabilities=['fire'])
    combat = _combat(caster, immune, vulnerable)
    spell = {
        'id': 'spell_fire_wave',
        'name': 'Fire Wave',
        'delivery': {'type': 'automatic'},
        'target': {
            'relation': 'enemy',
            'rangeBands': ['near'],
            'minTargets': 2,
            'maxTargets': 2,
        },
        'effects': [{'kind': 'damage', 'amount': 5, 'damageType': 'fire'}],
    }
    roller = SequenceRoller()

    result = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=spell,
        target_ids=['enemy_immune', 'enemy_vulnerable'],
        cast_id='cast_defenses',
        roller=roller,
    )

    first, second = result['resolution']['targets']
    assert first['effects'][0]['defense'] == 'immune'
    assert first['effects'][0]['amountApplied'] == 0
    assert second['effects'][0]['defense'] == 'vulnerable'
    assert second['effects'][0]['amountApplied'] == 10
    assert result['combat']['participants'][1]['hp']['current'] == 12
    assert result['combat']['participants'][2]['hp']['current'] == 2
    assert roller.sides == []


def test_automatic_healing_caps_at_max_and_temporary_hp_does_not_stack() -> None:
    caster = _participant('player_mage', team='player')
    ally = _participant('player_ally', team='ally', hp=3, maximum=10, temporary=5)
    combat = _combat(caster, ally)
    spell = {
        'id': 'spell_amber_aid',
        'name': 'Amber Aid',
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': ['near']},
        'effects': [
            {'kind': 'healing', 'dice': '1d8+2'},
            {'kind': 'temporary_hp', 'dice': '1d6'},
        ],
    }
    roller = SequenceRoller(8, 4)

    result = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=spell,
        target_ids=['player_ally'],
        cast_id='cast_heal',
        roller=roller,
    )

    healing, temporary = result['resolution']['targets'][0]['effects']
    assert healing['rolledAmount'] == 10
    assert healing['amountApplied'] == 7
    assert temporary['rolledAmount'] == 4
    assert temporary['amountApplied'] == 0
    assert result['combat']['participants'][1]['hp'] == {'current': 10, 'max': 10, 'temp': 5}
    json.dumps(result)


def test_ordinary_healing_does_not_clear_unrelated_unconscious_state() -> None:
    caster = _participant('player_mage', team='player')
    ally = _participant('player_ally', team='ally', hp=3, maximum=10)
    ally['isConscious'] = False
    ally['conditions'] = ['unconscious']
    spell = {
        'id': 'spell_minor_mending',
        'name': 'Minor Mending',
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': ['near']},
        'effects': [{'kind': 'healing', 'amount': 3}],
    }

    result = resolve_targeted_spell(
        _combat(caster, ally),
        caster_id='player_mage',
        spell=spell,
        target_ids=['player_ally'],
        cast_id='cast_sleeping_heal',
        roller=SequenceRoller(),
    )

    updated = result['combat']['participants'][1]
    assert updated['hp']['current'] == 6
    assert updated['isConscious'] is False
    assert updated['conditions'] == ['unconscious']


def test_defeated_target_requires_explicit_revival_permission() -> None:
    caster = _participant('player_mage', team='player')
    defeated = _participant('player_ally', team='ally', hp=0, maximum=10)
    combat = _combat(caster, defeated)
    base_spell = {
        'id': 'spell_return_breath',
        'name': 'Return Breath',
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'ally', 'rangeBands': ['near']},
        'effects': [{'kind': 'healing', 'amount': 3}],
    }

    rejected = spell_target_legality(
        combat,
        caster_id='player_mage',
        spell=base_spell,
        target_ids=['player_ally'],
    )
    revival = deepcopy(base_spell)
    revival['target']['allowDefeated'] = True
    result = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=revival,
        target_ids=['player_ally'],
        cast_id='cast_revival',
        roller=SequenceRoller(),
    )

    assert rejected['code'] == 'spell_target_defeated'
    revived = result['combat']['participants'][1]
    assert revived['hp']['current'] == 3
    assert revived['isAlive'] is True
    assert revived['isConscious'] is True


def test_condition_immunity_blocks_condition_without_blocking_other_effects() -> None:
    caster = _participant('player_mage', team='player')
    target = _participant(
        'enemy_ogre',
        team='enemy',
        hp=10,
        conditionImmunities=['restrained'],
    )
    combat = _combat(caster, target)
    spell = {
        'id': 'spell_force_chain',
        'name': 'Force Chain',
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'enemy', 'rangeBands': ['near']},
        'effects': [
            {'kind': 'damage', 'amount': 2, 'damageType': 'force'},
            {'kind': 'condition', 'condition': 'restrained'},
        ],
    }

    result = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=spell,
        target_ids=['enemy_ogre'],
        cast_id='cast_immunity',
        roller=SequenceRoller(),
    )

    damage, condition = result['resolution']['targets'][0]['effects']
    assert damage['amountApplied'] == 2
    assert condition['immune'] is True
    assert condition['applied'] is False
    assert result['combat']['participants'][1]['conditions'] == []


def test_starting_new_concentration_replaces_old_effects_but_preserves_other_sources() -> None:
    caster = _participant('player_mage', team='player')
    target = _participant(
        'enemy_ogre',
        team='enemy',
        conditions=['restrained', 'frightened'],
        activeEffects=[
            {
                'id': 'old_restraint',
                'kind': 'condition',
                'operation': 'add',
                'condition': 'restrained',
                'sourceActorId': 'player_mage',
                'sourceSpellId': 'spell_old_bind',
                'concentration': True,
                'duration': {'kind': 'concentration'},
            },
            {
                'id': 'other_fear',
                'kind': 'condition',
                'operation': 'add',
                'condition': 'frightened',
                'sourceActorId': 'player_bard',
                'sourceSpellId': 'spell_fear_song',
                'concentration': True,
                'duration': {'kind': 'concentration'},
            },
        ],
    )
    combat = _combat(caster, target)
    spell = {
        'id': 'spell_mind_chain',
        'name': 'Mind Chain',
        'concentration': True,
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'enemy', 'rangeBands': ['near']},
        'effects': [{'kind': 'condition', 'condition': 'charmed'}],
    }

    result = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=spell,
        target_ids=['enemy_ogre'],
        cast_id='cast_new_concentration',
        roller=SequenceRoller(),
        caster_resources=_resources('spell_old_bind'),
        current_turn=14,
    )

    updated = result['combat']['participants'][1]
    concentration = result['casterResources']['concentration']
    concentration_result = result['resolution']['concentration']
    assert updated['conditions'] == ['charmed', 'frightened']
    assert {effect['id'] for effect in updated['activeEffects']} == {
        'other_fear',
        updated['activeEffects'][1]['id'],
    }
    assert updated['activeEffects'][1]['sourceSpellId'] == 'spell_mind_chain'
    assert concentration == {
        'active': True,
        'spellId': 'spell_mind_chain',
        'spellName': 'Mind Chain',
        'casterActorId': 'player_mage',
        'targetIds': ['enemy_ogre'],
        'startedAtTurn': 14,
    }
    assert result['casterResources']['revision'] == 4
    assert concentration_result['replaced']['spellId'] == 'spell_old_bind'
    assert [effect['id'] for effect in concentration_result['removedEffects']] == ['old_restraint']


def test_concentration_check_uses_constitution_save_and_maintains_effect_on_success() -> None:
    caster = _participant(
        'player_mage',
        team='player',
        savingThrowProficiencies=['constitution'],
    )
    target = _participant(
        'enemy_ogre',
        team='enemy',
        conditions=['restrained'],
        activeEffects=[
            {
                'id': 'bind',
                'kind': 'condition',
                'operation': 'add',
                'condition': 'restrained',
                'sourceActorId': 'player_mage',
                'sourceSpellId': 'spell_old_bind',
                'concentration': True,
                'duration': {'kind': 'concentration'},
            }
        ],
    )
    combat = _combat(caster, target)
    roller = SequenceRoller(5)

    result = resolve_concentration_check(
        combat,
        _resources('spell_old_bind'),
        caster_id='player_mage',
        damage=8,
        roller=roller,
    )

    assert result['required'] is True
    assert result['check']['dc'] == 10
    assert result['check']['modifier'] == 5  # Constitution +2 and level-five proficiency +3.
    assert result['check']['total'] == 10
    assert result['maintained'] is True
    assert result['casterResources']['concentration']['spellId'] == 'spell_old_bind'
    assert result['combat'] == combat


def test_failed_concentration_check_removes_only_exact_spell_effects() -> None:
    caster = _participant('player_mage', team='player')
    target = _participant(
        'enemy_ogre',
        team='enemy',
        conditions=['restrained', 'frightened'],
        activeEffects=[
            {
                'id': 'bind',
                'kind': 'condition',
                'operation': 'add',
                'condition': 'restrained',
                'sourceActorId': 'player_mage',
                'sourceSpellId': 'spell_old_bind',
                'concentration': True,
                'duration': {'kind': 'concentration'},
            },
            {
                'id': 'fear',
                'kind': 'condition',
                'operation': 'add',
                'condition': 'frightened',
                'sourceActorId': 'player_bard',
                'sourceSpellId': 'spell_fear_song',
                'concentration': True,
                'duration': {'kind': 'concentration'},
            },
        ],
    )
    combat = _combat(caster, target)

    result = resolve_concentration_check(
        combat,
        _resources('spell_old_bind'),
        caster_id='player_mage',
        damage=26,
        roller=SequenceRoller(3),
    )

    updated = result['combat']['participants'][1]
    assert result['check']['dc'] == 13
    assert result['check']['total'] == 5
    assert result['maintained'] is False
    assert result['reason'] == 'saving_throw_failed'
    assert result['casterResources']['concentration'] is None
    assert result['casterResources']['revision'] == 4
    assert updated['conditions'] == ['frightened']
    assert [effect['id'] for effect in updated['activeEffects']] == ['fear']


def test_incapacitation_ends_concentration_without_rolling() -> None:
    caster = _participant('player_mage', team='player')
    caster['conditions'] = ['stunned']
    target = _participant(
        'enemy_ogre',
        team='enemy',
        conditions=['restrained'],
        activeEffects=[
            {
                'id': 'bind',
                'kind': 'condition',
                'operation': 'add',
                'condition': 'restrained',
                'sourceActorId': 'player_mage',
                'sourceSpellId': 'spell_old_bind',
                'concentration': True,
                'duration': {'kind': 'concentration'},
            }
        ],
    )
    roller = SequenceRoller(20)

    result = resolve_concentration_check(
        _combat(caster, target),
        _resources('spell_old_bind'),
        caster_id='player_mage',
        damage=1,
        roller=roller,
    )

    assert result['required'] is False
    assert result['maintained'] is False
    assert result['reason'] == 'caster_incapacitated'
    assert result['check']['automaticFailure'] is True
    assert result['check']['naturalRoll'] is None
    assert result['combat']['participants'][1]['conditions'] == []
    assert roller.sides == []


def test_duration_advancement_expires_at_correct_boundary_and_preserves_overlap() -> None:
    caster = _participant('player_mage', team='player')
    target = _participant(
        'enemy_ogre',
        team='enemy',
        conditions=['restrained', 'slowed'],
        activeEffects=[
            {
                'id': 'restraint_short',
                'kind': 'condition',
                'operation': 'add',
                'condition': 'restrained',
                'sourceActorId': 'player_mage',
                'sourceSpellId': 'spell_one',
                'duration': {'remaining': 1, 'tick': 'target_turn_end'},
            },
            {
                'id': 'restraint_long',
                'kind': 'condition',
                'operation': 'add',
                'condition': 'restrained',
                'sourceActorId': 'player_bard',
                'sourceSpellId': 'spell_two',
                'duration': {'remaining': 2, 'tick': 'target_turn_end'},
            },
            {
                'id': 'slow_source',
                'kind': 'condition',
                'operation': 'add',
                'condition': 'slowed',
                'sourceActorId': 'player_mage',
                'sourceSpellId': 'spell_three',
                'duration': {'remaining': 1, 'tick': 'source_turn_end'},
            },
        ],
    )
    combat = _combat(caster, target)

    target_tick = advance_spell_effect_durations(
        combat,
        timing='target_turn_end',
        actor_id='enemy_ogre',
    )
    after_target = target_tick['combat']['participants'][1]
    assert [effect['id'] for effect in target_tick['expiredEffects']] == ['restraint_short']
    assert after_target['conditions'] == ['restrained', 'slowed']
    assert next(
        effect for effect in after_target['activeEffects'] if effect['id'] == 'restraint_long'
    )['duration']['remaining'] == 1

    source_tick = advance_spell_effect_durations(
        target_tick['combat'],
        timing='source_turn_end',
        actor_id='player_mage',
    )
    after_source = source_tick['combat']['participants'][1]
    assert [effect['id'] for effect in source_tick['expiredEffects']] == ['slow_source']
    assert after_source['conditions'] == ['restrained']


def test_timed_spell_does_not_erase_same_preexisting_condition_when_it_expires() -> None:
    caster = _participant('player_mage', team='player')
    target = _participant('enemy_ogre', team='enemy', conditions=['restrained'])
    spell = {
        'id': 'spell_reinforcing_bind',
        'name': 'Reinforcing Bind',
        'delivery': {'type': 'automatic'},
        'target': {'relation': 'enemy', 'rangeBands': ['near']},
        'effects': [
            {
                'kind': 'condition',
                'condition': 'restrained',
                'duration': {'remaining': 1, 'tick': 'target_turn_end'},
            }
        ],
    }
    cast = resolve_targeted_spell(
        _combat(caster, target),
        caster_id='player_mage',
        spell=spell,
        target_ids=['enemy_ogre'],
        cast_id='cast_reinforcing_bind',
        roller=SequenceRoller(),
    )
    active_effect = cast['combat']['participants'][1]['activeEffects'][0]
    assert active_effect['preserveConditionOnExpiry'] is True

    expired = advance_spell_effect_durations(
        cast['combat'],
        timing='target_turn_end',
        actor_id='enemy_ogre',
    )
    updated = expired['combat']['participants'][1]
    assert updated['activeEffects'] == []
    assert updated['conditions'] == ['restrained']


def test_cast_id_retry_is_idempotent_and_conflicting_reuse_fails_closed() -> None:
    caster = _participant('player_mage', team='player')
    target = _participant('enemy_ogre', team='enemy', hp=12)
    combat = _combat(caster, target)
    first_roller = SequenceRoller(15, 6)
    first = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=_damage_spell(),
        target_ids=['enemy_ogre'],
        cast_id='cast_retry_safe',
        roller=first_roller,
    )
    retry_roller = SequenceRoller(1, 1)

    retried = resolve_targeted_spell(
        first['combat'],
        caster_id='player_mage',
        spell=_damage_spell(),
        target_ids=['enemy_ogre'],
        cast_id='cast_retry_safe',
        roller=retry_roller,
    )
    conflict = resolve_targeted_spell(
        first['combat'],
        caster_id='player_mage',
        spell={**_damage_spell(), 'id': 'spell_forged'},
        target_ids=['enemy_ogre'],
        cast_id='cast_retry_safe',
        roller=retry_roller,
    )

    assert first['combat']['participants'][1]['hp']['current'] == 6
    assert retried['ok'] is True
    assert retried['duplicate'] is True
    assert retried['combat'] == first['combat']
    assert retry_roller.sides == []
    assert conflict['ok'] is False
    assert conflict['code'] == 'spell_cast_id_conflict'
    assert conflict['combat'] == first['combat']


def test_invalid_spell_definition_never_rolls_or_mutates() -> None:
    caster = _participant('player_mage', team='player')
    target = _participant('enemy_ogre', team='enemy')
    combat = _combat(caster, target)
    roller = SequenceRoller(20)
    invalid_spell = _damage_spell(
        effects=[{'kind': 'damage', 'dice': '1000d9999', 'damageType': 'cold'}]
    )

    result = resolve_targeted_spell(
        combat,
        caster_id='player_mage',
        spell=invalid_spell,
        target_ids=['enemy_ogre'],
        cast_id='cast_invalid',
        roller=roller,
    )

    assert result['ok'] is False
    assert result['code'] == 'spell_definition_invalid'
    assert result['combat'] == combat
    assert roller.sides == []
