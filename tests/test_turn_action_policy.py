import json
from types import SimpleNamespace

from aidm_server.rules import RuleHint
from aidm_server.turn_action_policy import TurnActionPolicy


def _player(**overrides):
    values = {
        'player_id': 7,
        'workspace_id': 'workspace-1',
        'campaign_id': 11,
        'character_name': 'Lyra Moonfall',
        'name': 'Danny',
        'race': 'elf',
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_model_input_for_action_keeps_policy_specific_prompts_together():
    assert TurnActionPolicy.model_input_for_action('look around', None, 'Lyra') == 'look around'

    admin_prompt = TurnActionPolicy.model_input_for_action(
        '[ADMIN] open the sealed gate',
        {'kind': 'admin'},
        'Lyra',
    )
    assert admin_prompt.startswith('ADMIN OVERRIDE (authenticated):\nopen the sealed gate')
    assert '[ADMIN]' not in admin_prompt

    item_prompt = TurnActionPolicy.model_input_for_action(
        'I buy the lantern.',
        {
            'kind': 'item',
            'inventory_action': 'buy',
            'item': {'name': 'lantern', 'quantity': 2},
            'cost_gold': 5,
        },
        'Lyra',
    )
    assert 'Attempted action: buy' in item_prompt
    assert 'Item: lantern x2' in item_prompt
    assert 'Known price/value: 5 gold' in item_prompt


def test_model_input_for_action_prioritizes_pvp_target_context():
    target = _player(player_id=8, character_name='Thorn', name='Alex')

    prompt = TurnActionPolicy.model_input_for_action(
        'I strike Thorn.',
        {'kind': 'interact', 'target': {'player_id': 8}},
        'Lyra',
        target,
    )

    assert prompt.startswith('PLAYER-VS-PLAYER ACTION (ALLOWED):')
    assert 'Target player character: Thorn' in prompt
    assert 'contested' in prompt


def test_target_policy_matches_named_players_without_generic_race_false_positives():
    elf = _player(character_name='Lyra Moonfall', race='elf')
    orc = _player(character_name='Gorak', race='orc')

    assert TurnActionPolicy.harmful_text_targets_player('I attack Lyra Moonfall.', elf)
    assert not TurnActionPolicy.harmful_text_targets_player('I attack the elf scout.', elf)
    assert TurnActionPolicy.harmful_text_targets_player('I strike the orc.', orc)
    assert not TurnActionPolicy.harmful_text_targets_player('I greet Gorak.', orc)


def test_current_scene_npc_target_requires_scene_availability():
    session = SimpleNamespace(
        state_snapshot=json.dumps(
            {
                'currentScene': {'locationId': 'market', 'activeNpcIds': ['npc-mara']},
                'knownNpcs': [
                    {
                        'id': 'npc-mara',
                        'name': 'Mara Voss',
                        'aliases': ['the smith'],
                        'role': 'Blacksmith',
                        'locationId': 'market',
                    },
                    {
                        'id': 'npc-remote',
                        'name': 'Distant Sage',
                        'locationId': 'tower',
                    },
                ],
            }
        )
    )

    assert TurnActionPolicy.current_scene_npc_target(session, {'npc_id': 'npc-mara'}) == {
        'npc_id': 'npc-mara',
        'character_name': 'Mara Voss',
        'player_name': 'Blacksmith',
    }
    assert TurnActionPolicy.current_scene_npc_target(session, {'npc_id': 'npc-remote'}) is None
    assert TurnActionPolicy.current_scene_npc_target_from_text(session, 'I speak to the smith.') == {
        'npc_id': 'npc-mara',
        'character_name': 'Mara Voss',
        'player_name': 'Blacksmith',
    }


def test_pvp_rules_policy_marks_contested_resolution():
    target = _player(player_id=8, character_name='Thorn')
    payload = TurnActionPolicy.pvp_rules_payload(target)
    hint = RuleHint(
        requires_roll=False,
        roll_type='check',
        dc_hint=None,
        reason='Unclassified action',
        confidence=0.2,
    )

    result = TurnActionPolicy.apply_pvp_rule_hint(hint, payload)

    assert payload == {
        'allowed': True,
        'requires_contested_resolution': True,
        'target_player_id': 8,
        'target_character_name': 'Thorn',
    }
    assert result is hint
    assert result.requires_roll is True
    assert result.roll_type == 'attack'
    assert result.outcome_deferred is True
    assert result.confidence == 0.97


def test_player_availability_is_scoped_to_workspace_and_campaign():
    campaign = SimpleNamespace(workspace_id='workspace-1', campaign_id=11)

    assert TurnActionPolicy.player_is_available_for_campaign(_player(), campaign)
    assert not TurnActionPolicy.player_is_available_for_campaign(
        _player(workspace_id='workspace-2'),
        campaign,
    )
    assert not TurnActionPolicy.player_is_available_for_campaign(None, campaign)
