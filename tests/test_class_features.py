from __future__ import annotations

import pytest

from aidm_server.class_capabilities import capabilities_for_class  # Loads the same providers as the game.
from aidm_server.armor_class import armor_class_details, sync_actor_armor_class
from aidm_server.combat.modifiers import attacks_per_action, save_context
from aidm_server.combat.state import player_combat_participant


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
