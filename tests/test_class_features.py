from __future__ import annotations

from copy import deepcopy
import json

import pytest

from aidm_server.class_capabilities import (  # Loads the same providers as the game.
    apply_capability_use,
    attack_rider_change,
    capabilities_for_class,
    class_combat_actions,
    resolve_attack_rider,
    resolve_capability_use,
)
from aidm_server.armor_class import armor_class_details, sync_actor_armor_class
from aidm_server.class_features import (
    agonizing_blast_provider,
    attack_rider_provider,
    class_feature_descriptors,
    colossus_slayer_provider,
    default_fighting_style,
    dueling_provider,
    fighting_style_for,
    great_weapon_fighting_provider,
    improved_critical_provider,
    normalize_class_feature_state,
    restore_class_capabilities,
    sneak_attack_provider,
)
from aidm_server.combat import modifiers
from aidm_server.combat.attacks import damage_component
from aidm_server.combat.legal_actions import legal_combat_actions_for_player, resolve_combat_legal_action
from aidm_server.combat.modifiers import attacks_per_action, check_context, damage_defense, extra_damage, save_context, tick_effects
from aidm_server.combat.reactions import collect_reactions
from aidm_server.combat.state import default_turn_economy, player_combat_participant
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.validation import validator as validator_module
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.models import Player


ACTOR = 'player_1'
SCORES = {'strength': 16, 'dexterity': 14, 'constitution': 14, 'intelligence': 10, 'wisdom': 14, 'charisma': 16}


def _actor(class_name: str, level: int = 1, *, pid: str = ACTOR, **extra) -> dict:
    return {
        'id': pid,
        'playerId': int(pid.split('_')[1]),
        'name': pid.replace('_', ' ').title(),
        'class': class_name,
        'level': level,
        'team': 'player',
        'stats': dict(SCORES),
        'health': {'currentHp': 20, 'maxHp': 30, 'conditions': []},
        'inventory': {'items': []},
        **extra,
    }


def _pc(actor: dict, *, band: str = 'melee', **extra) -> dict:
    return {
        'id': actor['id'],
        'playerId': actor['playerId'],
        'name': actor['name'],
        'team': 'player',
        'kind': 'player_character',
        'class': actor['class'],
        'level': actor['level'],
        'hp': {'current': 20, 'max': 30, 'temp': 0},
        'armorClass': 14,
        'stats': dict(actor['stats']),
        'conditions': [],
        'activeEffects': [],
        'position': {'rangeBand': band},
        **extra,
    }


def _enemy(pid: str = 'enemy_1', *, band: str = 'melee', creature: str = 'humanoid', xp: int = 50, hp: int = 20, **extra) -> dict:
    return {
        'id': pid,
        'name': pid.replace('_', ' ').title(),
        'team': 'enemy',
        'kind': 'creature',
        'creatureType': creature,
        'xpReward': xp,
        'hp': {'current': hp, 'max': 20, 'temp': 0},
        'armorClass': 12,
        'stats': {'strength': 12, 'dexterity': 10, 'constitution': 10, 'wisdom': 6},
        'conditions': [],
        'position': {'rangeBand': band},
        'abilities': [],
        **extra,
    }


def _state(actors: list[dict], participants: list[dict] | None = None, *, economy: dict | None = None, clock: dict | None = None) -> dict:
    state: dict = {'playerCharacters': actors, 'clock': clock or {'day': 1, 'shortRestsSinceLong': 0}}
    if participants is not None:
        state['combat'] = {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': participants,
            'battlefield': {},
            'initiative': [
                {'participantId': participant['id'], 'roll': 20 - index, 'modifier': 0, 'total': 20 - index, 'order': index}
                for index, participant in enumerate(participants)
            ],
            'flags': {'activeActorId': ACTOR, 'turnEconomy': {**default_turn_economy(ACTOR, 1), **(economy or {})}},
        }
    return state


def _declare(state: dict, capability_id: str, *, target_id: str | None = None, amount: int | None = None) -> dict:
    action = {'id': 'act_1', 'type': 'class_feature.use', 'actorId': ACTOR, 'capabilityId': capability_id}
    if target_id:
        action['targetId'] = target_id
    if amount is not None:
        action['amount'] = amount
    return validate_declared_actions(state=state, declared_actions=[action], current_turn=5, expected_actor_id=ACTOR)


def _use(state: dict, capability_id: str, **kwargs) -> dict:
    declared = _declare(state, capability_id, **kwargs)
    verdict = declared['validatedActions'][0]
    assert verdict['status'] == 'valid', verdict['reason']
    validation = validate_state_changes(state=state, changes=declared['immediateChanges'])
    assert validation['rejected'] == [], validation['rejected']
    return apply_state_changes(state, validated_changes_for_application(validation))['nextState']


def _refusal(state: dict, capability_id: str, **kwargs) -> str:
    verdict = _declare(state, capability_id, **kwargs)['validatedActions'][0]
    assert verdict['status'] == 'invalid'
    return verdict['reason']


def _combatant(state: dict, participant_id: str) -> dict:
    return next(participant for participant in state['combat']['participants'] if participant['id'] == participant_id)


def _effect_ids(record: dict) -> set[str]:
    return {effect['id'] for effect in record.get('activeEffects') or []}


def _fixed_d20(monkeypatch, value: int) -> None:
    monkeypatch.setattr(validator_module.secrets, 'randbelow', lambda _size: value - 1)


# --- Passive features already in place -----------------------------------------------


@pytest.mark.parametrize('class_name', ['Barbarian', 'Fighter', 'Monk', 'Paladin', 'Ranger'])
def test_extra_attack_is_granted_at_level_five(class_name):
    capabilities_for_class(class_name, 5)
    assert attacks_per_action({'class': class_name, 'level': 4}) == 1
    assert attacks_per_action({'class': class_name, 'level': 5}) == 2


def test_extra_attack_uses_primary_class_only():
    assert attacks_per_action({'class': 'Wizard / Fighter', 'level': 5}) == 1
    assert attacks_per_action({'class': 'Fighter / Wizard', 'level': 5}) == 2
    assert attacks_per_action({'class': 'Rogue', 'level': 5}) == 1


def test_danger_sense_respects_level_ability_and_incapacitation():
    barbarian = {'id': 'b', 'name': 'Barbarian', 'class': 'Barbarian', 'level': 2, 'conditions': []}
    assert save_context(barbarian, 'dexterity')['mode'] == 'advantage'
    assert save_context(barbarian, 'strength')['mode'] == 'normal'
    assert save_context({**barbarian, 'level': 1}, 'dexterity')['mode'] == 'normal'
    for condition in ('blinded', 'deafened', 'incapacitated'):
        assert save_context({**barbarian, 'conditions': [condition]}, 'dexterity')['mode'] == 'normal'
    assert save_context({**barbarian, 'conditions': ['restrained']}, 'dexterity')['mode'] == 'normal'


def test_unarmored_defense_and_shield_rules_apply_in_sheet_and_combat():
    scores = {'dexterity': 14, 'constitution': 16, 'wisdom': 18}
    shield = {'name': 'Shield', 'type': 'armor', 'equipped': True, 'slot': 'off_hand'}
    armor = {'name': 'Leather Armor', 'type': 'armor', 'equipped': True, 'slot': 'body_armor'}
    assert armor_class_details(scores, class_name='Barbarian')['armorClass'] == 15
    assert armor_class_details(scores, [shield], class_name='Barbarian')['armorClass'] == 17
    assert armor_class_details(scores, [armor], class_name='Barbarian')['armorClass'] == 13
    assert armor_class_details(scores, class_name='Monk')['armorClass'] == 16
    assert armor_class_details(scores, [shield], class_name='Monk')['armorClass'] == 14
    assert armor_class_details(scores, class_name='Wizard / Monk')['armorClass'] == 12

    actor = {'id': 'player_1', 'name': 'Monk', 'class': 'Monk', 'stats': dict(scores),
             'health': {'currentHp': 10, 'maxHp': 10}, 'inventory': {'items': []}}
    assert sync_actor_armor_class(actor) == 16
    assert player_combat_participant(actor)['armorClass'] == 16
    actor['inventory']['items'] = [shield]
    assert sync_actor_armor_class(actor) == 14
    assert player_combat_participant(actor)['armorClass'] == 14
    actor['inventory']['items'] = []
    assert sync_actor_armor_class(actor) == 16


# --- Pools, level gating, rests --------------------------------------------------------


def test_class_pools_are_sized_by_level_and_gated():
    def sizes(class_name: str, level: int, scores: dict | None = None) -> dict:
        return {key: entry['max'] for key, entry in normalize_class_feature_state(None, class_name=class_name, level=level, scores=scores).items()}

    assert sizes('Barbarian', 2) == {'rage': 2}
    assert sizes('Barbarian', 3) == {'rage': 3}
    assert sizes('Monk', 1) == {}
    assert sizes('Monk', 4) == {'ki': 4}
    assert sizes('Sorcerer', 1) == {}
    assert sizes('Sorcerer', 5) == {'sorcery_points': 5}
    assert sizes('Cleric', 1) == {}
    assert sizes('Cleric', 2) == {'channel_divinity': 1}
    assert sizes('Wizard', 1) == {'arcane_recovery': 1}
    assert sizes('Bard', 1, {'charisma': 16}) == {'bardic_inspiration': 3}
    assert sizes('Bard', 1, {'charisma': 8}) == {'bardic_inspiration': 1}
    assert sizes('Wizard / Monk', 4) == {'arcane_recovery': 1}
    assert [item['id'] for item in capabilities_for_class('Monk', 1)] == ['martial_arts_strike']
    assert [item['id'] for item in capabilities_for_class('Monk', 5)][-1] == 'stunning_strike'
    assert 'reckless_attack' not in [item['id'] for item in capabilities_for_class('Barbarian', 1)]


def test_level_up_grows_pools_without_refilling_spent_uses():
    spent = {'rage': {'current': 0, 'max': 2, 'refreshesOn': 'long_rest'}}
    grown = normalize_class_feature_state(spent, class_name='Barbarian', level=3)
    assert grown['rage'] == {'current': 1, 'max': 3, 'refreshesOn': 'long_rest'}
    ki = normalize_class_feature_state({'ki': {'current': 1, 'max': 2}}, class_name='Monk', level=3)
    assert (ki['ki']['current'], ki['ki']['max']) == (2, 3)
    # Without ability scores a Charisma-sized pool keeps its stored size.
    kept = normalize_class_feature_state({'bardic_inspiration': {'current': 2, 'max': 3}}, class_name='Bard', level=1)
    assert (kept['bardic_inspiration']['current'], kept['bardic_inspiration']['max']) == (2, 3)


def test_short_rest_refreshes_short_pools_and_bardic_inspiration_from_level_five():
    def spent(class_name: str, level: int) -> dict:
        state = normalize_class_feature_state(None, class_name=class_name, level=level, scores=SCORES)
        for entry in state.values():
            entry['current'] = 0
        return state

    def after(class_name: str, level: int, rest: str) -> dict:
        state = restore_class_capabilities(spent(class_name, level), class_name=class_name, level=level, rest_type=rest, scores=SCORES)
        return {key: entry['current'] for key, entry in state.items()}

    assert after('Monk', 2, 'short_rest') == {'ki': 2}
    assert after('Cleric', 2, 'short_rest') == {'channel_divinity': 1}
    assert after('Barbarian', 1, 'short_rest') == {'rage': 0}
    assert after('Sorcerer', 2, 'short_rest') == {'sorcery_points': 0}
    assert after('Wizard', 1, 'short_rest') == {'arcane_recovery': 0}
    assert after('Bard', 4, 'short_rest') == {'bardic_inspiration': 0}
    assert after('Bard', 5, 'short_rest') == {'bardic_inspiration': 3}
    assert after('Barbarian', 1, 'long_rest') == {'rage': 2}
    assert after('Sorcerer', 2, 'long_rest') == {'sorcery_points': 2}


# --- Barbarian ---------------------------------------------------------------------


def _barbarian_state(level: int = 1, **economy) -> dict:
    actor = _actor('Barbarian', level)
    return _state([actor], [_pc(actor), _enemy()], economy=economy)


def test_rage_spends_a_use_and_bonus_action_and_grants_its_benefits():
    state = _use(_barbarian_state(), 'rage')
    barbarian = _combatant(state, ACTOR)
    assert state['playerCharacters'][0]['classFeatureState']['rage']['current'] == 1
    assert state['combat']['flags']['turnEconomy']['bonusActionRemaining'] == 0
    assert damage_defense(barbarian, 'slashing') == (0.5, 'resistant')
    assert damage_defense(barbarian, 'fire') == (1.0, 'normal')
    strength_melee = {'ranged': False, 'ability': 'strength'}
    assert {'flat': 2, 'type': 'slashing', 'source': 'Rage'} in extra_damage(
        barbarian, _enemy(), kind='weapon_attack', weapon={'damageType': 'slashing'}, attack=strength_melee,
    )
    # The +2 is for melee attacks using Strength only.
    for attack in ({'ranged': True, 'ability': 'strength'}, {'ranged': False, 'ability': 'dexterity'}):
        assert extra_damage(barbarian, _enemy(), kind='weapon_attack', weapon={'damageType': 'slashing'}, attack=attack) == []
    assert check_context(barbarian, ability='strength')['mode'] == 'advantage'
    assert check_context(barbarian, ability='dexterity')['mode'] == 'normal'
    assert save_context(barbarian, 'strength')['mode'] == 'advantage'
    rage = next(effect for effect in barbarian['activeEffects'] if effect['id'] == f'rage:{ACTOR}')
    assert rage['duration'] == {'rounds': 10, 'tickOn': 'target_turn_end'}
    assert rage['endsWhen'] == ['unconscious', 'no_attack_or_damage_since_last_turn']
    assert rage['cannotCast'] is True

    unconscious = {**barbarian, 'hp': {'current': 0, 'max': 30}, 'conditions': ['unconscious']}
    assert check_context(unconscious, ability='strength')['mode'] != 'advantage'
    for _ in range(10):
        tick_effects(state['combat'], actor_id=ACTOR, tick_on='target_turn_end')
    assert f'rage:{ACTOR}' not in _effect_ids(barbarian)


def test_rage_needs_combat_uses_and_cannot_stack():
    out_of_combat = _state([_actor('Barbarian')])
    assert _refusal(out_of_combat, 'rage') == 'Available only during active combat.'
    raging = _use(_barbarian_state(), 'rage')
    raging['combat']['flags']['turnEconomy']['bonusActionRemaining'] = 1
    assert _refusal(raging, 'rage') == 'Already raging.'
    exhausted = _barbarian_state()
    exhausted['playerCharacters'][0]['classFeatureState'] = {'rage': {'current': 0, 'max': 2}}
    assert _refusal(exhausted, 'rage') == 'Rage has no uses remaining.'
    spent_bonus = _barbarian_state(bonusActionRemaining=0)
    assert _refusal(spent_bonus, 'rage') == 'The bonus action for this turn is already spent.'


def test_reckless_attack_only_before_the_first_attack_and_exposes_the_barbarian():
    assert _refusal(_barbarian_state(1), 'reckless_attack') == 'That class capability is not available at this level.'
    assert _refusal(_barbarian_state(2, attackWeaponIds=['axe']), 'reckless_attack') == 'Declare Reckless Attack before your first attack this turn.'
    state = _use(_barbarian_state(2), 'reckless_attack')
    barbarian = _combatant(state, ACTOR)
    assert state['combat']['flags']['turnEconomy']['bonusActionRemaining'] == 1
    assert modifiers.attack_context(barbarian, _enemy(), kind='weapon_attack', ranged=False, ability='strength')['mode'] == 'advantage'
    assert modifiers.attack_context(barbarian, _enemy(), kind='weapon_attack', ranged=False, ability='dexterity')['mode'] == 'normal'
    assert modifiers.attack_context(barbarian, _enemy(band='far'), kind='weapon_attack', ranged=True, ability='strength')['mode'] == 'normal'
    assert modifiers.attack_context(_enemy(), barbarian, kind='enemy_attack', ranged=False)['mode'] == 'advantage'
    tick_effects(state['combat'], actor_id=ACTOR, tick_on='target_turn_end')
    assert modifiers.attack_context(barbarian, _enemy(), kind='weapon_attack', ranged=False, ability='strength')['mode'] == 'normal'
    assert modifiers.attack_context(_enemy(), barbarian, kind='enemy_attack', ranged=False)['mode'] == 'advantage'
    tick_effects(state['combat'], actor_id=ACTOR, tick_on='target_turn_start')
    assert modifiers.attack_context(_enemy(), barbarian, kind='enemy_attack', ranged=False)['mode'] == 'normal'


# --- Bard -------------------------------------------------------------------------------


def test_bardic_inspiration_targets_another_ally_with_a_level_scaled_die():
    bard, ally = _actor('Bard', 1), _actor('Fighter', 1, pid='player_2')
    out_of_combat = _state([bard, ally])
    assert _refusal(out_of_combat, 'bardic_inspiration', target_id=ACTOR) == 'Bardic Inspiration must target an ally other than yourself.'
    state = _use(out_of_combat, 'bardic_inspiration', target_id='player_2')
    inspired = state['playerCharacters'][1]['activeEffects'][0]
    assert (inspired['id'], inspired['inspirationDie'], inspired['casterId']) == ('bardic_inspiration:player_2', 'd6', ACTOR)
    assert (inspired['duration'], inspired['expiresOn']) == ({'rounds': 100, 'tickOn': 'target_turn_end'}, 'rest')
    assert state['playerCharacters'][0]['classFeatureState']['bardic_inspiration'] == {
        'current': 2, 'max': 3, 'refreshesOn': 'long_rest', 'usedAtTurn': 5,
    }
    assert _refusal(state, 'bardic_inspiration', target_id='player_2') == 'Player 2 already has a Bardic Inspiration die.'

    level_five = _state([_actor('Bard', 5), ally])
    assert _use(level_five, 'bardic_inspiration', target_id='player_2')['playerCharacters'][1]['activeEffects'][0]['inspirationDie'] == 'd8'


def test_bardic_inspiration_in_combat_needs_a_nearby_ally():
    bard, ally = _actor('Bard', 1), _actor('Fighter', 1, pid='player_2')
    far = _state([bard, ally], [_pc(bard, band='melee'), _pc(ally, band='far'), _enemy()])
    assert _refusal(far, 'bardic_inspiration', target_id='player_2') == 'Too far away; the ally must be in your range band or the next one.'
    near = _state([bard, ally], [_pc(bard, band='melee'), _pc(ally, band='near'), _enemy()])
    state = _use(near, 'bardic_inspiration', target_id='player_2')
    assert 'bardic_inspiration:player_2' in _effect_ids(_combatant(state, 'player_2'))
    assert state['combat']['flags']['turnEconomy']['bonusActionRemaining'] == 0


# --- Cleric -------------------------------------------------------------------------------


def _cleric_state(level: int = 2, *enemies: dict) -> dict:
    cleric = _actor('Cleric', level)
    return _state([cleric], [_pc(cleric), *enemies])


def test_turn_undead_turns_only_undead_that_fail_their_save(monkeypatch):
    _fixed_d20(monkeypatch, 5)
    zombie = _enemy('enemy_zombie', creature='undead', band='near')
    goblin = _enemy('enemy_goblin')
    distant = _enemy('enemy_far_zombie', creature='undead', band='distant')
    state = _use(_cleric_state(2, zombie, goblin, distant), 'turn_undead')
    turned = _combatant(state, 'enemy_zombie')
    assert 'turned' in turned['conditions']
    effect = next(effect for effect in turned['activeEffects'] if effect['id'] == 'turned:enemy_zombie')
    assert effect['modifiers'] == {'cannotReact': True}
    assert effect['duration'] == {'rounds': 10, 'tickOn': 'target_turn_end'}
    assert (effect['consumeOn'], effect['endsWhen']) == (None, ['damage_taken'])
    # A turned creature still takes its turns, to move away.
    assert modifiers.can_act(turned) is True
    assert _combatant(state, 'enemy_goblin')['conditions'] == []
    assert _combatant(state, 'enemy_far_zombie')['conditions'] == []
    assert state['playerCharacters'][0]['classFeatureState']['channel_divinity']['current'] == 0
    assert state['combat']['flags']['turnEconomy']['actionRemaining'] == 0
    # Being attacked does not end the turning; taking damage does.
    context = modifiers.attack_context(_pc(_actor('Fighter')), turned, kind='weapon_attack', ranged=False)
    modifiers.consume_modifiers(state['combat'], context['consume'])
    assert 'turned' in turned['conditions']
    modifiers.end_effects_on_damage(turned)
    assert 'turned' not in turned['conditions']


def test_turn_undead_saves_against_the_cleric_spell_dc_and_destroys_weak_undead_at_level_five(monkeypatch):
    _fixed_d20(monkeypatch, 14)  # Zombie total 12 against DC 12 (8 + 2 + Wisdom 2): saved.
    resisted = _use(_cleric_state(2, _enemy('enemy_zombie', creature='undead')), 'turn_undead')
    assert _combatant(resisted, 'enemy_zombie')['conditions'] == []

    _fixed_d20(monkeypatch, 2)
    weak, strong = _enemy('enemy_skeleton', creature='undead', xp=100), _enemy('enemy_wight', creature='undead', xp=700)
    state = _use(_cleric_state(5, weak, strong), 'turn_undead')
    assert _combatant(state, 'enemy_skeleton')['isAlive'] is False
    assert _combatant(state, 'enemy_skeleton')['hp']['current'] == 0
    assert 'turned' in _combatant(state, 'enemy_wight')['conditions']


def test_turn_undead_needs_level_two_an_undead_target_and_a_short_rest_refresh():
    assert _refusal(_cleric_state(1, _enemy(creature='undead')), 'turn_undead') == 'That class capability is not available at this level.'
    assert _refusal(_cleric_state(2, _enemy()), 'turn_undead') == 'No undead enemy is within melee, near or far range.'
    spent = _cleric_state(2, _enemy(creature='undead'))
    spent['playerCharacters'][0]['classFeatureState'] = {'channel_divinity': {'current': 0, 'max': 1}}
    assert _refusal(spent, 'turn_undead') == 'No Channel Divinity uses remaining.'


# --- Fighter, Paladin, Ranger fighting styles --------------------------------------------------


def _item(name: str, slot: str = 'main_hand', item_type: str = 'weapon', **extra) -> dict:
    return {'name': name, 'type': item_type, 'equipped': True, 'slot': slot, **extra}


def test_fighting_style_defaults_from_equipment_and_respects_the_class_list():
    assert default_fighting_style('Fighter', [_item('Longsword')]) == 'dueling'
    assert default_fighting_style('Fighter', [_item('Longsword'), _item('Shield', 'off_hand', 'armor')]) == 'dueling'
    assert default_fighting_style('Fighter', [_item('Greatsword', 'two_hands')]) == 'great_weapon'
    assert default_fighting_style('Fighter', [_item('Longbow', 'two_hands')]) == 'archery'
    assert default_fighting_style('Fighter', [_item('Dagger'), _item('Shortsword', 'off_hand')]) == 'defense'
    assert default_fighting_style('Fighter', []) == 'defense'
    assert default_fighting_style('Paladin', [_item('Longbow', 'two_hands')]) == 'defense'
    assert default_fighting_style('Ranger', [_item('Longbow', 'two_hands')]) == 'archery'
    assert default_fighting_style('Ranger', [_item('Greataxe', 'two_hands')]) == 'defense'
    assert fighting_style_for('Fighter', 1, chosen='Archery', items=[_item('Greatsword', 'two_hands')]) == 'archery'
    assert fighting_style_for('Paladin', 2, chosen='archery', items=[_item('Greatsword', 'two_hands')]) == 'great_weapon'
    assert fighting_style_for('Paladin', 1, items=[]) is None
    assert fighting_style_for('Ranger', 1, items=[]) is None
    assert fighting_style_for('Wizard / Fighter', 5, items=[]) is None


def test_fighting_style_providers():
    archer = {'id': 'p', 'class': 'Fighter', 'level': 1, 'fightingStyle': 'archery', 'conditions': []}
    assert modifiers.attack_context(archer, _enemy(), kind='weapon_attack', ranged=True)['attackFlatBonus'] == 2
    assert modifiers.attack_context(archer, _enemy(), kind='weapon_attack', ranged=False)['attackFlatBonus'] == 0
    defender = {'id': 'd', 'fightingStyle': 'defense', 'conditions': [], 'armorClassBreakdown': {'source': 'armor'}}
    assert modifiers.attack_context(_enemy(), defender, kind='enemy_attack', ranged=False)['targetAcBonus'] == 1
    unarmored = {**defender, 'armorClassBreakdown': {'source': 'unarmored'}}
    assert modifiers.attack_context(_enemy(), unarmored, kind='enemy_attack', ranged=False)['targetAcBonus'] == 0

    duelist = {'id': 'f', 'fightingStyle': 'dueling'}
    attack = {'mode': 'normal'}
    assert dueling_provider(duelist, _enemy(), {'kind': 'weapon_attack', 'weapon': {'name': 'Longsword', 'otherWeaponEquipped': False}, 'attack': attack}) == [
        {'flat': 2, 'source': 'Dueling fighting style'}
    ]
    assert dueling_provider(duelist, _enemy(), {'kind': 'weapon_attack', 'weapon': {'name': 'Longsword', 'otherWeaponEquipped': True}, 'attack': attack}) == []
    assert dueling_provider(duelist, _enemy(), {'kind': 'weapon_attack', 'weapon': {'name': 'Greatsword', 'otherWeaponEquipped': False}, 'attack': attack}) == []
    great = {'fightingStyle': 'great_weapon'}
    assert great_weapon_fighting_provider(great, 'attacker', {'weapon': {'name': 'Greataxe'}})['damageRerollBelow'] == 2
    assert great_weapon_fighting_provider(great, 'attacker', {'weapon': {'name': 'Longsword'}}) is None


def test_improved_critical_needs_the_champion_subclass_at_level_three():
    context = {'kind': 'weapon_attack'}
    assert improved_critical_provider({'class': 'Fighter - Champion', 'level': 3}, 'attacker', context) == {'criticalRangeBonus': 1}
    assert improved_critical_provider({'class': 'Fighter - Champion', 'level': 2}, 'attacker', context) is None
    assert improved_critical_provider({'class': 'Fighter - Battle Master', 'level': 5}, 'attacker', context) is None


# --- Monk ---------------------------------------------------------------------------------


def _monk_state(level: int = 2, **economy) -> dict:
    monk = _actor('Monk', level)
    return _state([monk], [_pc(monk), _enemy()], economy=economy)


def test_patient_defense_and_step_of_the_wind_spend_ki_and_the_bonus_action():
    state = _use(_monk_state(), 'patient_defense')
    assert 'dodging' in _combatant(state, ACTOR)['conditions']
    assert state['playerCharacters'][0]['classFeatureState']['ki']['current'] == 1
    assert state['combat']['flags']['turnEconomy']['bonusActionRemaining'] == 0

    dashed = _use(_monk_state(), 'step_of_the_wind:dash')
    assert dashed['combat']['flags']['turnEconomy']['movementRemaining'] == 2
    disengaged = _use(_monk_state(), 'step_of_the_wind:disengage')
    assert disengaged['combat']['flags']['turnEconomy']['disengaged'] is True
    assert _refusal(_monk_state(disengaged=True), 'step_of_the_wind:disengage') == 'Already disengaged this turn.'
    assert _refusal(_monk_state(), 'step_of_the_wind:fly') == 'That Step of the Wind option is not available.'
    assert _refusal(_monk_state(1), 'patient_defense') == 'That class capability is not available at this level.'
    broke = _monk_state()
    broke['playerCharacters'][0]['classFeatureState'] = {'ki': {'current': 0, 'max': 2}}
    assert _refusal(broke, 'patient_defense') == 'No ki points remaining.'


def test_flurry_of_blows_follows_the_attack_action_and_grants_two_strikes():
    assert _refusal(_monk_state(), 'flurry_of_blows') == 'Take the Attack action first.'
    state = _use(_monk_state(attackWeaponIds=['unarmed'], actionRemaining=0), 'flurry_of_blows')
    assert f'flurry_of_blows:{ACTOR}' in _effect_ids(_combatant(state, ACTOR))
    assert state['playerCharacters'][0]['classFeatureState']['ki']['current'] == 1
    actions = {action['id']: action for action in class_combat_actions(state, _combatant(state, ACTOR), state['combat'])}
    strike = actions['class.strike.flurry_of_blows']
    assert (strike['available'], strike['economy']['bonusAction'], strike['economy']['action']) == (True, 0, 0)
    state['combat']['flags']['turnEconomy']['spentActionIds'] = [f'class.strike.flurry_of_blows@turn:{turn}' for turn in (6, 7)]
    actions = {action['id']: action for action in class_combat_actions(state, _combatant(state, ACTOR), state['combat'])}
    assert actions['class.strike.flurry_of_blows']['reason'] == 'Both Flurry of Blows strikes are spent.'


def test_martial_arts_unarmed_strike_and_bonus_strike_actions():
    state = _monk_state(1)
    actions = {action['id']: action for action in class_combat_actions(state, _combatant(state, ACTOR), state['combat'])}
    unarmed = actions['class.attack.unarmed']
    assert unarmed['weapon']['damage'] == {'dice': '1d4', 'type': 'bludgeoning'}
    assert unarmed['economy']['action'] == 1 and unarmed['available'] is True
    bonus = actions['class.strike.martial_arts_strike']
    assert (bonus['available'], bonus['reason'], bonus['economy']['bonusAction']) == (False, 'Take the Attack action first.', 1)
    assert 'class.strike.flurry_of_blows' not in actions
    attacked = _monk_state(5, attackWeaponIds=['unarmed'], attacksRemaining=1)
    actions = {action['id']: action for action in class_combat_actions(attacked, _combatant(attacked, ACTOR), attacked['combat'])}
    assert actions['class.attack.unarmed']['weapon']['damage']['dice'] == '1d6'
    assert actions['class.attack.unarmed']['economy']['action'] == 0
    assert actions['class.strike.martial_arts_strike']['available'] is True
    assert _refusal(attacked, 'martial_arts_strike') == 'Choose it from the combat actions on your turn.'


def test_bonus_strike_and_rider_actions_resolve_to_tagged_attacks():
    monk = _actor('Monk', 5, inventory={'items': [_item('Quarterstaff', id='staff')]})
    state = _state([monk], [_pc(monk), _enemy()], economy={'attackWeaponIds': ['staff'], 'actionRemaining': 0})
    player = Player(player_id=1, character_name='Kai', class_='Monk', level=5, stats=json.dumps({'ability_scores': SCORES}),
                    inventory=json.dumps(monk['inventory']['items']))
    canonical, code, message = resolve_combat_legal_action(state, player, action_id='class.strike.martial_arts_strike', target_id='enemy_1')
    assert code is None, message
    assert (canonical['action_type'], canonical['bonus_attack'], canonical['damage_dice'], canonical['economy']['bonusAction']) == ('attack', True, '1d6', 1)
    bundle = legal_combat_actions_for_player(state, player)
    rider = next(action for action in bundle['actions'] if action['id'] == 'class.rider.staff.stunning_strike')
    assert rider['label'] == 'Attack with Quarterstaff + Stunning Strike'
    assert rider['available'] is False  # The Attack action is spent and no attacks remain.
    state['combat']['flags']['turnEconomy']['attacksRemaining'] = 1
    canonical, code, _message = resolve_combat_legal_action(state, player, action_id='class.rider.staff.stunning_strike', target_id='enemy_1')
    assert code is None
    assert canonical['rider'] == {'capabilityId': 'stunning_strike', 'option': ''}


def test_stunning_strike_spends_ki_only_on_a_hit_and_stuns_on_a_failed_save():
    monk = _actor('Monk', 5)
    target = _enemy()
    state = _state([monk], [_pc(monk), target])
    assert attack_rider_change(state, actor_id=ACTOR, rider={'capabilityId': 'stunning_strike'}, target_id='enemy_1', hit=False, turn_id=5) is None
    change = attack_rider_change(state, actor_id=ACTOR, rider={'capabilityId': 'stunning_strike'}, target_id='enemy_1', hit=True, turn_id=5, roller=lambda _sides: 3)
    assert change['resolution']['save']['saveSucceeded'] is False  # 3 + 0 against DC 8 + 3 + 2.
    validation = validate_state_changes(state=state, changes=[change])
    assert validation['rejected'] == []
    after = apply_state_changes(state, validated_changes_for_application(validation))['nextState']
    stunned = _combatant(after, 'enemy_1')
    assert 'stunned' in stunned['conditions']
    assert after['playerCharacters'][0]['classFeatureState']['ki']['current'] == 4
    effect = next(effect for effect in stunned['activeEffects'] if effect['id'] == 'stunning_strike:enemy_1')
    assert effect['duration'] == {'rounds': 2, 'tickOn': 'caster_turn_end'}
    tick_effects(after['combat'], actor_id=ACTOR, tick_on='caster_turn_end')
    assert 'stunned' in stunned['conditions']
    tick_effects(after['combat'], actor_id=ACTOR, tick_on='caster_turn_end')
    assert 'stunned' not in stunned['conditions']

    resisted = resolve_attack_rider(actor=monk, rider={'capabilityId': 'stunning_strike'}, target=target, hit=True, combat=state['combat'], roller=lambda _sides: 20)
    assert (resisted['save']['saveSucceeded'], resisted['resourceCost'], 'effects' in resisted) == (True, 1, False)
    assert resolve_attack_rider(actor=_actor('Monk', 4), rider={'capabilityId': 'stunning_strike'}, target=target, hit=True, combat=state['combat'])['reason'] == (
        'That rider is not available at this level.'
    )


# --- Paladin -------------------------------------------------------------------------------


def _paladin(level: int = 2, *, slots: dict | None = None) -> dict:
    return _actor('Paladin', level, spellResources={'slots': slots or {'1': {'current': 2, 'max': 2}}}, inventory={'items': [_item('Longsword', id='sword')]})


def test_divine_smite_dice_scale_with_slot_and_undead_and_double_on_a_critical():
    context = {'kind': 'weapon_attack', 'attack': {'rider': {'capabilityId': 'divine_smite', 'slotLevel': 1}}}
    paladin = {'class': 'Paladin', 'level': 2}
    assert attack_rider_provider(paladin, _enemy(), context) == [{'dice': '2d8', 'type': 'radiant', 'source': 'Divine Smite'}]
    second = {'kind': 'weapon_attack', 'attack': {'rider': {'capabilityId': 'divine_smite', 'slotLevel': 2}}}
    assert attack_rider_provider(paladin, _enemy(creature='undead'), second)[0]['dice'] == '4d8'
    fifth = {'kind': 'weapon_attack', 'attack': {'rider': {'capabilityId': 'divine_smite', 'slotLevel': 5}}}
    assert attack_rider_provider(paladin, _enemy(), fifth)[0]['dice'] == '5d8'
    assert attack_rider_provider(paladin, _enemy(creature='fiend'), fifth)[0]['dice'] == '6d8'
    assert attack_rider_provider(paladin, _enemy(), {'kind': 'weapon_attack', 'attack': {}}) == []
    critical = damage_component(dice='2d8', damage_type='radiant', source='Divine Smite', target=_enemy(), critical=True, roller=lambda _sides: 1)
    assert critical['rolls'] == [1, 1, 1, 1]


def test_divine_smite_spends_the_chosen_slot_only_on_a_hit_and_is_offered_per_slot():
    paladin = _paladin(5, slots={'1': {'current': 1, 'max': 4}, '2': {'current': 0, 'max': 2}})
    state = _state([paladin], [_pc(paladin), _enemy()])
    rider = {'capabilityId': 'divine_smite', 'option': '1', 'slotLevel': 1}
    assert attack_rider_change(state, actor_id=ACTOR, rider=rider, target_id='enemy_1', hit=False, turn_id=5) is None
    change = attack_rider_change(state, actor_id=ACTOR, rider=rider, target_id='enemy_1', hit=True, turn_id=5)
    validation = validate_state_changes(state=state, changes=[change])
    assert validation['rejected'] == []
    after = apply_state_changes(state, validated_changes_for_application(validation))['nextState']
    assert after['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 0
    assert after['combat']['flags']['turnEconomy']['actionRemaining'] == 1

    actions = {action['id']: action for action in class_combat_actions(state, _combatant(state, ACTOR), state['combat'])}
    first, second = actions['class.rider.sword.divine_smite.1'], actions['class.rider.sword.divine_smite.2']
    assert first['label'] == 'Attack with Longsword + Divine Smite (1st-level slot)'
    assert first['available'] is True
    assert (second['available'], second['reason']) == (False, 'No 2nd-level spell slot left.')
    assert attack_rider_change(after, actor_id=ACTOR, rider=rider, target_id='enemy_1', hit=True, turn_id=6) is None


def test_lay_on_hands_can_cure_poison_for_five_points():
    paladin = _actor('Paladin', 1)
    ally = _actor('Fighter', 1, pid='player_2', health={'currentHp': 10, 'maxHp': 10, 'conditions': ['poisoned']})
    state = _use(_state([paladin, ally]), 'lay_on_hands:cure_poison', target_id='player_2')
    assert state['playerCharacters'][1]['health']['conditions'] == []
    assert state['playerCharacters'][0]['classFeatureState']['lay_on_hands']['current'] == 0
    assert _refusal(_state([_actor('Paladin', 1), _actor('Fighter', 1, pid='player_2')]), 'lay_on_hands:cure_poison', target_id='player_2') == (
        'Player 2 is not poisoned.'
    )


# --- Ranger, Rogue, Warlock damage providers ------------------------------------------------------


def test_colossus_slayer_is_hunter_only_once_per_turn_against_a_wounded_target():
    hunter = {'class': 'Ranger - Hunter', 'level': 3}
    wounded = _enemy(hp=10)
    context = {'kind': 'weapon_attack', 'weapon': {'name': 'Longbow'}, 'attack': {'oncePerTurnUsed': []}}
    assert colossus_slayer_provider(hunter, wounded, context) == [{'dice': '1d8', 'source': 'Colossus Slayer', 'oncePerTurn': 'colossus_slayer'}]
    assert colossus_slayer_provider(hunter, _enemy(hp=20), context) == []
    assert colossus_slayer_provider(hunter, wounded, {**context, 'attack': {'oncePerTurnUsed': ['colossus_slayer']}}) == []
    assert colossus_slayer_provider({'class': 'Ranger - Beast Master', 'level': 3}, wounded, context) == []
    assert colossus_slayer_provider({**hunter, 'level': 2}, wounded, context) == []


def test_sneak_attack_needs_finesse_or_ranged_and_advantage_or_an_engaged_ally():
    rogue = {'id': ACTOR, 'class': 'Rogue', 'level': 5}
    target = _enemy(position={'rangeBand': 'melee', 'engagedWith': ['player_2']})
    ally = {**_pc(_actor('Fighter', 1, pid='player_2')), 'position': {'rangeBand': 'melee', 'engagedWith': ['enemy_1']}}
    combat = {'participants': [rogue, ally, target]}

    def provide(weapon: str, mode: str, used: list | None = None, fight: dict | None = None) -> list:
        return sneak_attack_provider(rogue, target, {
            'kind': 'weapon_attack', 'weapon': {'name': weapon},
            'attack': {'mode': mode, 'combat': fight or combat, 'oncePerTurnUsed': used or []},
        })

    assert provide('Rapier', 'advantage') == [{'dice': '3d6', 'source': 'Sneak Attack', 'oncePerTurn': 'sneak_attack'}]
    assert provide('Shortbow', 'normal')[0]['dice'] == '3d6'
    assert provide('Longsword', 'advantage') == []
    assert provide('Rapier', 'disadvantage') == []
    assert provide('Rapier', 'normal', fight={'participants': [rogue, target]}) == []
    downed = {**ally, 'hp': {'current': 0, 'max': 30}}
    assert provide('Rapier', 'normal', fight={'participants': [rogue, downed, target]}) == []
    assert provide('Rapier', 'advantage', used=['sneak_attack']) == []
    level_one = sneak_attack_provider({**rogue, 'level': 1}, target, {
        'kind': 'weapon_attack', 'weapon': {'name': 'Dagger'}, 'attack': {'mode': 'advantage', 'combat': combat},
    })
    assert level_one[0]['dice'] == '1d6'
    critical = damage_component(dice='3d6', damage_type='piercing', source='Sneak Attack', target=_enemy(), critical=True, roller=lambda _sides: 2)
    assert critical['total'] == 12


def test_agonizing_blast_adds_charisma_to_eldritch_blast_from_level_two():
    warlock = {'class': 'Warlock', 'level': 2, 'stats': {'charisma': 16}}
    context = {'kind': 'spell_attack', 'spellId': 'eldritch_blast'}
    assert agonizing_blast_provider(warlock, _enemy(), context) == [{'flat': 3, 'type': 'force', 'source': 'Agonizing Blast'}]
    assert agonizing_blast_provider({**warlock, 'level': 1}, _enemy(), context) == []
    assert agonizing_blast_provider(warlock, _enemy(), {'kind': 'spell_attack', 'spellId': 'fire_bolt'}) == []


def test_cunning_action_is_a_bonus_action_dash_disengage_or_hide():
    rogue = _actor('Rogue', 2)
    state = _state([rogue], [_pc(rogue, band='near'), _enemy(band='far')])
    actions = {action['id']: action for action in class_combat_actions(state, _combatant(state, ACTOR), state['combat'])}
    for option in ('dash', 'disengage', 'hide'):
        action = actions[f'class.cunning_action.{option}']
        assert (action['type'], action['economy']['bonusAction'], action['economy']['action'], action['available']) == (option, 1, 0, True)
    level_one = _state([_actor('Rogue', 1)], [_pc(_actor('Rogue', 1)), _enemy()])
    assert not [action for action in class_combat_actions(level_one, _combatant(level_one, ACTOR), level_one['combat']) if 'cunning' in action['id']]
    in_melee = _state([rogue], [_pc(rogue, band='melee'), _enemy()], economy={'disengaged': True})
    actions = {action['id']: action for action in class_combat_actions(in_melee, _combatant(in_melee, ACTOR), in_melee['combat'])}
    assert actions['class.cunning_action.hide']['reason'] == 'You cannot hide in melee without cover.'
    assert actions['class.cunning_action.disengage']['reason'] == 'Already disengaged this turn.'


def test_uncanny_dodge_halves_one_hit_per_round_through_the_reaction_registry():
    rogue = _pc(_actor('Rogue', 5))
    combat = {'status': 'active', 'participants': [rogue, _enemy()], 'flags': {}}
    event = {'type': 'attack_hit', 'attackerId': 'enemy_1', 'targetId': ACTOR, 'damage': 9, 'eventId': 'hit_1', 'turnId': 3}
    reactions = collect_reactions(event, combat, {'combat': combat})
    assert [(reaction['kind'], reaction['damageMultiplier']) for reaction in reactions] == [('uncanny_dodge', 0.5)]
    assert rogue['reactionRemaining'] == 0
    assert collect_reactions({**event, 'eventId': 'hit_2'}, combat, {'combat': combat}) == []

    for participant in ({**_pc(_actor('Rogue', 4))}, {**_pc(_actor('Rogue', 5)), 'autoReactions': False}):
        fresh = {'status': 'active', 'participants': [participant, _enemy()], 'flags': {}}
        assert collect_reactions(event, fresh, {'combat': fresh}) == []


# --- Sorcerer -----------------------------------------------------------------------------------


def _sorcerer(level: int = 3, *, points: int | None = None, slots: dict | None = None) -> dict:
    actor = _actor('Sorcerer', level, spellResources={'slots': slots or {'1': {'current': 4, 'max': 4}, '2': {'current': 1, 'max': 2}}})
    if points is not None:
        actor['classFeatureState'] = {'sorcery_points': {'current': points, 'max': level}}
    return actor


def test_flexible_casting_converts_slots_and_points_both_ways():
    state = _use(_state([_sorcerer(3, points=1)]), 'flexible_casting:slot_to_points.2')
    sorcerer = state['playerCharacters'][0]
    assert sorcerer['classFeatureState']['sorcery_points']['current'] == 3
    assert sorcerer['spellResources']['slots']['2']['current'] == 0

    state = _use(_state([_sorcerer(3, points=3)]), 'flexible_casting:points_to_slot.2')
    sorcerer = state['playerCharacters'][0]
    assert sorcerer['classFeatureState']['sorcery_points']['current'] == 0
    assert sorcerer['spellResources']['slots']['2']['current'] == 2

    assert _refusal(_state([_sorcerer(3)]), 'flexible_casting:slot_to_points.1') == 'That would exceed your sorcery point maximum.'
    assert _refusal(_state([_sorcerer(3, points=3)]), 'flexible_casting:points_to_slot.1') == 'No expended 1st-level slot to restore.'
    assert _refusal(_state([_sorcerer(3, points=2)]), 'flexible_casting:points_to_slot.2') == 'Needs 3 sorcery points; 2 left.'
    assert _refusal(_state([_sorcerer(3, points=3)]), 'flexible_casting:points_to_slot.3') == 'That Flexible Casting option is not available.'
    in_combat = _sorcerer(3, points=1)
    combat_state = _state([in_combat], [_pc(in_combat), _enemy()], economy={'bonusActionRemaining': 0})
    assert _refusal(combat_state, 'flexible_casting:slot_to_points.1') == 'The bonus action for this turn is already spent.'


def test_metamagic_readies_the_next_spell_for_its_points():
    sorcerer = _sorcerer(3)
    state = _use(_state([sorcerer], [_pc(sorcerer), _enemy()]), 'quickened_spell')
    marker = next(effect for effect in _combatant(state, ACTOR)['activeEffects'] if effect['id'] == f'quickened_spell:{ACTOR}')
    assert (marker['metamagic'], marker['duration']) == ('quickened', {'rounds': 1, 'tickOn': 'target_turn_end'})
    assert state['playerCharacters'][0]['classFeatureState']['sorcery_points']['current'] == 1
    assert state['combat']['flags']['turnEconomy']['bonusActionRemaining'] == 1
    assert _refusal(state, 'quickened_spell') == 'Needs 2 sorcery points; 1 left.'

    twinned = _use(_state([_sorcerer(3)], [_pc(_sorcerer(3)), _enemy()]), 'twinned_spell:2')
    marker = next(effect for effect in _combatant(twinned, ACTOR)['activeEffects'] if effect['id'] == f'twinned_spell:{ACTOR}')
    assert (marker['metamagic'], marker['spellLevel']) == ('twinned', 2)
    assert twinned['playerCharacters'][0]['classFeatureState']['sorcery_points']['current'] == 1
    cantrip = _use(_state([_sorcerer(3)], [_pc(_sorcerer(3)), _enemy()]), 'twinned_spell:0')
    assert cantrip['playerCharacters'][0]['classFeatureState']['sorcery_points']['current'] == 2
    assert _refusal(_state([_sorcerer(2)], [_pc(_sorcerer(2)), _enemy()]), 'quickened_spell') == 'That class capability is not available at this level.'


# --- Wizard -----------------------------------------------------------------------------------------


def _wizard(level: int, slots: dict) -> dict:
    return _actor('Wizard', level, spellResources={'slots': slots})


def test_arcane_recovery_restores_up_to_half_the_level_lowest_first_after_a_short_rest():
    rested = {'day': 1, 'shortRestsSinceLong': 1}
    wizard = _wizard(5, {'1': {'current': 1, 'max': 4}, '2': {'current': 1, 'max': 3}, '3': {'current': 1, 'max': 2}})
    assert _refusal(_state([wizard]), 'arcane_recovery') == 'Arcane Recovery is part of a short rest; finish a short rest first.'
    state = _use(_state([wizard], clock=rested), 'arcane_recovery')
    slots = state['playerCharacters'][0]['spellResources']['slots']
    assert (slots['1']['current'], slots['2']['current'], slots['3']['current']) == (4, 1, 1)
    assert state['playerCharacters'][0]['classFeatureState']['arcane_recovery']['current'] == 0
    assert _refusal(state, 'arcane_recovery') == 'Arcane Recovery has no uses remaining.'

    chosen = _use(_state([_wizard(5, {'1': {'current': 1, 'max': 4}, '2': {'current': 1, 'max': 3}, '3': {'current': 1, 'max': 2}})], clock=rested), 'arcane_recovery:3')
    assert chosen['playerCharacters'][0]['spellResources']['slots']['3']['current'] == 2
    mixed = _use(_state([_wizard(5, {'1': {'current': 1, 'max': 4}, '2': {'current': 1, 'max': 3}})], clock=rested), 'arcane_recovery:1.2')
    assert [mixed['playerCharacters'][0]['spellResources']['slots'][level]['current'] for level in ('1', '2')] == [2, 2]

    level_two = _wizard(2, {'1': {'current': 0, 'max': 3}})
    assert _use(_state([level_two], clock=rested), 'arcane_recovery')['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 1
    assert _refusal(_state([_wizard(2, {'1': {'current': 0, 'max': 3}})], clock=rested), 'arcane_recovery:1.1') == 'That Arcane Recovery option is not available.'
    assert _refusal(_state([_wizard(1, {'1': {'current': 2, 'max': 2}})], clock=rested), 'arcane_recovery') == 'No expended spell slot can be recovered.'
    in_combat = _wizard(5, {'1': {'current': 0, 'max': 4}})
    assert _refusal(_state([in_combat], [_pc(in_combat), _enemy()], clock=rested), 'arcane_recovery') == 'Available only outside combat.'


# --- Exposure in the player's available actions ---------------------------------------------------


def test_capabilities_appear_in_the_combat_bundle_with_cost_uses_targets_and_reasons():
    bard, ally = _actor('Bard', 2), _actor('Fighter', 1, pid='player_2')
    state = _state([bard, ally], [_pc(bard), _pc(ally, band='distant'), _enemy()])
    player = Player(player_id=1, character_name='Lyra', class_='Bard', level=2, stats=json.dumps({'ability_scores': SCORES}), inventory='[]')
    bundle = legal_combat_actions_for_player(state, player)
    inspiration = next(action for action in bundle['actions'] if action['id'] == 'class.use.bardic_inspiration')
    assert inspiration['cost'] == {'action': 0, 'bonusAction': 1, 'movement': 0, 'reaction': 0}
    assert (inspiration['capability']['current'], inspiration['capability']['max'], inspiration['capability']['pool']) == (3, 3, 'bardic_inspiration')
    assert [target['id'] for target in inspiration['targets']] == ['player_2']
    assert (inspiration['available'], inspiration['reason']) == (False, 'Too far away; the ally must be in your range band or the next one.')

    _combatant(state, 'player_2')['position']['rangeBand'] = 'near'
    bundle = legal_combat_actions_for_player(state, player)
    inspiration = next(action for action in bundle['actions'] if action['id'] == 'class.use.bardic_inspiration')
    assert inspiration['available'] is True
    canonical, code, _message = resolve_combat_legal_action(state, player, action_id='class.use.bardic_inspiration', target_id='player_2')
    assert code is None
    assert (canonical['action_type'], canonical['capability_id'], canonical['target_id']) == ('class_feature', 'bardic_inspiration', 'player_2')
    assert canonical['economy']['bonusAction'] == 0

    state['combat']['flags']['turnEconomy']['bonusActionRemaining'] = 0
    bundle = legal_combat_actions_for_player(state, player)
    inspiration = next(action for action in bundle['actions'] if action['id'] == 'class.use.bardic_inspiration')
    assert inspiration['available'] is False and 'bonus action' in inspiration['reason']


def test_out_of_combat_descriptors_list_every_variant_with_its_pool_and_blockers():
    descriptors = {
        entry['id']: entry
        for entry in class_feature_descriptors(
            'Sorcerer', 3, scores=SCORES,
            feature_state={'sorcery_points': {'current': 2, 'max': 3}},
            spell_resources={'slots': {'1': {'current': 4, 'max': 4}, '2': {'current': 0, 'max': 2}}},
        )
    }
    assert {'flexible_casting:slot_to_points.1', 'flexible_casting:points_to_slot.2', 'quickened_spell', 'twinned_spell:0', 'twinned_spell:2'} <= set(descriptors)
    assert descriptors['flexible_casting:slot_to_points.2']['reason'] == 'No 2nd-level spell slot left.'
    assert descriptors['flexible_casting:points_to_slot.1']['reason'] == 'No expended 1st-level slot to restore.'
    assert descriptors['flexible_casting:points_to_slot.2']['reason'] == 'Needs 3 sorcery points; 2 left.'
    assert descriptors['quickened_spell']['pool'] == 'sorcery_points'
    assert descriptors['twinned_spell:2']['cost'] == {'pool': 'sorcery_points', 'amount': 2}
    rogue = {entry['id']: entry for entry in class_feature_descriptors('Rogue', 5)}
    assert rogue['cunning_action:hide']['reason'] == 'Choose it from the combat actions on your turn.'
    assert rogue['uncanny_dodge']['reason'] == 'Used automatically as your reaction when an attack hits you.'
    paladin = {entry['id']: entry for entry in class_feature_descriptors('Paladin', 2, spell_resources={'slots': {'1': {'current': 1, 'max': 2}}})}
    assert paladin['divine_smite:1']['role'] == 'attack_rider'


def test_capability_use_without_combat_state_is_refused_cleanly():
    result = resolve_capability_use(actor=_actor('Barbarian'), capability_id='rage', in_combat=True, turn_economy={'bonusActionRemaining': 1})
    assert result == {'ok': False, 'reason': 'Player 1 is not part of this encounter.'}
    stale = _barbarian_state()
    change = {'turnId': 5, 'resolution': {'effectType': 'apply_modifier', 'actionEconomy': 'bonus_action', 'cost': {'pool': 'rage'}, 'resourceCost': 1,
                                          'capabilityId': 'rage', 'effects': [{'participantId': 'player_9', 'effect': {'id': 'x'}}]}}
    before = deepcopy(stale)
    assert apply_capability_use(stale, stale['playerCharacters'][0], change) is None
    assert stale == before


def test_cunning_action_dash_resolves_through_the_combat_pipeline_at_a_bonus_action():
    from aidm_server.combat.pipeline import prepare_combat_for_turn
    from aidm_server.models import Campaign, DmTurn, Session

    rogue = _actor('Rogue', 2)
    state = _state([rogue], [_pc(rogue, band='near'), _enemy(band='far')])
    state['combat']['flags']['combatDifficultyAI'] = {'tacticalLevel': 'simple', 'allowSentientEnemyBrain': False, 'maxLlmCallsPerRound': 0}
    player = Player(player_id=1, character_name='Mara', class_='Rogue', level=2, stats=json.dumps({'ability_scores': SCORES}), inventory='[]')
    canonical, code, message = resolve_combat_legal_action(state, player, action_id='class.cunning_action.dash')
    assert code is None, message
    turn = DmTurn(turn_id=40, player_id=1, metadata_json=json.dumps({'action_intent': {'kind': 'combat', 'combat': canonical}}))
    result = prepare_combat_for_turn(
        state=state, session_obj=Session(session_id=1, campaign_id=1), campaign=Campaign(campaign_id=1, workspace_id='w'),
        turn=turn, player_message='', workspace_id='w',
    )
    assert 'combatActionBlockedReason' not in result['debug']
    validation = validate_state_changes(state=state, changes=result['changes'])
    assert validation['rejected'] == []
    after = apply_state_changes(state, validated_changes_for_application(validation))['nextState']
    economy = after['combat']['flags']['turnEconomy']
    assert (economy['actionRemaining'], economy['bonusActionRemaining'], economy['movementRemaining']) == (1, 0, 2)
