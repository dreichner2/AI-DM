from __future__ import annotations

from aidm_server.action_intent import (
    apply_action_intent_to_rule_hint,
    has_reserved_admin_prefix,
    strip_reserved_admin_prefix,
    validate_action_intent,
)
from aidm_server.rules import RuleHint


def test_validate_roll_action_intent_normalizes_roll_metadata():
    intent, error = validate_action_intent(
        {
            'kind': 'roll',
            'source': 'dice_roller',
            'text': 'I roll a d20+2: 18 = 20',
            'client_message_id': 'local-test-1',
            'ability': {'key': 'dexterity', 'label': 'DEX', 'modifier': 2},
            'roll': {
                'die': 'D20',
                'mode': 'advantage',
                'modifier': 2,
                'rolls': [9, 18],
                'kept': 18,
                'total': 20,
                'result_visibility': 'hidden_until_landed',
                'reason': 'checking the lock',
                'target_pending_turn_id': '42',
            },
        }
    )

    assert error is None
    assert intent is not None
    assert intent['kind'] == 'roll'
    assert intent['client_message_id'] == 'local-test-1'
    assert intent['roll']['die'] == 'd20'
    assert intent['roll']['mode'] == 'advantage'
    assert 'total' not in intent['roll']
    assert 'modifier' not in intent['roll']
    assert 'rolls' not in intent['roll']
    assert 'kept' not in intent['roll']
    assert intent['roll']['target_pending_turn_id'] == 42
    assert intent['ability'] == {'key': 'dexterity', 'label': 'DEX'}


def test_validate_roll_action_ignores_client_claimed_outcome():
    intent, error = validate_action_intent(
        {
            'kind': 'roll',
            'roll': {
                'die': 'd20',
                'mode': 'normal',
                'modifier': 2,
                'rolls': [12],
                'kept': 12,
                'total': 99,
            },
        }
    )

    assert error is None
    assert intent is not None
    assert intent['roll'] == {
        'die': 'd20',
        'mode': 'normal',
        'result_visibility': 'hidden_until_landed',
        'reason': '',
    }


def test_validate_roll_action_rejects_invalid_pending_target():
    intent, error = validate_action_intent(
        {
            'kind': 'roll',
            'roll': {
                'die': 'd20',
                'mode': 'normal',
                'rolls': [12],
                'kept': 12,
                'total': 12,
                'target_pending_turn_id': 0,
            },
        }
    )

    assert intent is None
    assert error == 'roll.target_pending_turn_id must be a positive integer.'


def test_apply_roll_intent_overrides_natural_language_rule_hint():
    hint = RuleHint(
        requires_roll=False,
        roll_type=None,
        dc_hint=None,
        reason='Narrative action',
        confidence=0.1,
    )
    intent, error = validate_action_intent(
        {
            'kind': 'roll',
            'ability': {'key': 'strength', 'label': 'STR', 'modifier': 3},
            'roll': {
                'die': 'd20',
                'mode': 'normal',
                'modifier': 3,
                'rolls': [17],
                'kept': 17,
                'total': 20,
                'reason': 'saving throw',
            },
        }
    )

    assert error is None
    updated = apply_action_intent_to_rule_hint(intent, hint)

    assert updated.requires_roll is True
    assert updated.roll_type == 'strength'
    assert updated.roll_value is None
    assert updated.outcome_deferred is True
    assert updated.confidence == 0.99


def test_validate_ability_and_item_intents():
    ability, ability_error = validate_action_intent(
        {
            'kind': 'ability',
            'ability': {'key': 'strength', 'label': 'STR', 'modifier': 4},
        }
    )
    item, item_error = validate_action_intent(
        {
            'kind': 'item',
            'inventory_action': 'buy',
            'item': {'id': 'healing-potion-1', 'name': 'Healing Potion', 'quantity': 2},
            'cost_gold': '5',
        }
    )

    assert ability_error is None
    assert item_error is None
    assert ability['ability']['key'] == 'strength'
    assert item['item']['name'] == 'Healing Potion'
    assert item['item']['id'] == 'healing-potion-1'
    assert item['inventory_action'] == 'buy'
    assert item['cost_gold'] == 5


def test_validate_item_intent_rejects_unsafe_item_id():
    intent, error = validate_action_intent(
        {
            'kind': 'item',
            'inventory_action': 'use',
            'item': {'id': 'potion id with spaces', 'name': 'Healing Potion', 'quantity': 1},
        }
    )

    assert intent is None
    assert error == 'item.id contains unsupported characters.'


def test_validate_spell_intent_and_applies_spellcasting_rule_hint():
    intent, error = validate_action_intent(
        {
            'kind': 'spell',
            'text': 'Timmeh casts Wild Surge: lift the bubbles with magic',
            'spell': {'name': 'Wild Surge', 'effect': 'lift the bubbles with magic'},
            'ability': {'key': 'charisma', 'label': 'CHA', 'modifier': -1},
        }
    )
    hint = RuleHint(
        requires_roll=False,
        roll_type=None,
        dc_hint=None,
        reason='Narrative action',
        confidence=0.1,
    )

    assert error is None
    assert intent is not None
    assert intent['kind'] == 'spell'
    assert intent['spell'] == {
        'name': 'Wild Surge',
        'effect': 'lift the bubbles with magic',
        'resource_pool': 'auto',
    }

    updated = apply_action_intent_to_rule_hint(intent, hint)

    assert updated.requires_roll is True
    assert updated.roll_type == 'spell'
    assert updated.dc_hint == '12-18'
    assert updated.outcome_deferred is True
    assert updated.reason == 'Typed spell action: Wild Surge'


def test_validate_item_intent_rejects_non_items():
    intent, error = validate_action_intent(
        {
            'kind': 'item',
            'inventory_action': 'pick_up',
            'item': {'name': 'hope', 'quantity': 1},
            'cost_gold': 0,
        }
    )

    assert intent is None
    assert error == 'item.name must be a tangible inventory item.'


def test_validate_combat_intent_keeps_only_server_resolvable_ids():
    intent, error = validate_action_intent(
        {
            'kind': 'combat',
            'source': 'combat_hud',
            'text': 'I deal 999 damage.',
            'combat': {
                'action_id': 'combat.attack.blade',
                'target_id': 'enemy_goblin_1',
                'action_type': 'instant_kill',
                'damage': 999,
                'available': True,
            },
        }
    )

    assert error is None
    assert intent is not None
    assert intent['kind'] == 'combat'
    assert intent['combat'] == {
        'action_id': 'combat.attack.blade',
        'target_id': 'enemy_goblin_1',
    }


def test_server_resolved_combat_attack_forces_attack_roll_hint():
    hint = RuleHint(
        requires_roll=False,
        roll_type=None,
        dc_hint=None,
        reason='Narrative action',
        confidence=0.1,
    )
    intent = {
        'kind': 'combat',
        'combat': {
            'action_id': 'combat.attack.blade',
            'action_type': 'attack',
            'authoritative': True,
        },
    }

    updated = apply_action_intent_to_rule_hint(intent, hint)

    assert updated.requires_roll is True
    assert updated.roll_type == 'attack'
    assert updated.roll_value is None
    assert updated.outcome_deferred is True
    assert updated.confidence == 1.0


def test_validate_interaction_intent_normalizes_target_metadata():
    intent, error = validate_action_intent(
        {
            'kind': 'interact',
            'source': 'composer',
            'text': 'Seraphina says to Borin: hold the bridge',
            'client_message_id': 'interact-1',
            'interaction': {'type': 'speak_to', 'label': 'Speak to'},
            'target': {
                'player_id': '42',
                'character_name': 'Borin',
                'player_name': 'Maya',
            },
        }
    )

    assert error is None
    assert intent is not None
    assert intent['kind'] == 'interact'
    assert intent['interaction'] == {'type': 'speak_to', 'label': 'Speak to'}
    assert intent['target'] == {
        'kind': 'player',
        'player_id': 42,
        'character_name': 'Borin',
        'player_name': 'Maya',
    }


def test_validate_interaction_intent_accepts_npc_target_metadata():
    intent, error = validate_action_intent(
        {
            'kind': 'interact',
            'source': 'composer',
            'text': 'Seraphina says to Captain Velra: hold the bridge',
            'client_message_id': 'interact-npc-1',
            'interaction': {'type': 'speak_to', 'label': 'Speak to'},
            'target': {
                'kind': 'npc',
                'npc_id': 'captain_velra',
                'character_name': 'Captain Velra',
                'player_name': 'dock captain',
            },
        }
    )

    assert error is None
    assert intent is not None
    assert intent['target'] == {
        'kind': 'npc',
        'npc_id': 'captain_velra',
        'character_name': 'Captain Velra',
        'player_name': 'dock captain',
    }


def test_validate_admin_action_intent_normalizes_without_passcode():
    intent, error = validate_action_intent(
        {
            'kind': 'admin',
            'source': 'composer',
            'text': '[ADMIN] make the door open',
            'client_message_id': 'admin-1',
            'admin_passcode': 'must-not-be-persisted',
        }
    )

    assert error is None
    assert intent == {
        'kind': 'admin',
        'text': '[ADMIN] make the door open',
        'source': 'composer',
        'client_message_id': 'admin-1',
    }


def test_reserved_admin_prefix_detection_matches_auth_only_markers():
    assert has_reserved_admin_prefix('[ADMIN] open the door')
    assert has_reserved_admin_prefix('(ADMIN) open the door')
    assert has_reserved_admin_prefix('/ADMIN/ open the door')
    assert has_reserved_admin_prefix('/ADMIN open the door')
    assert not has_reserved_admin_prefix('I ask the admin for help')
    assert not has_reserved_admin_prefix('/administer the potion')


def test_reserved_admin_prefix_strip_removes_one_authenticated_marker():
    assert strip_reserved_admin_prefix('[ADMIN] open the door') == 'open the door'
    assert strip_reserved_admin_prefix('(ADMIN) open the door') == 'open the door'
    assert strip_reserved_admin_prefix('/ADMIN/ open the door') == 'open the door'
    assert strip_reserved_admin_prefix('/ADMIN open the door') == 'open the door'
