import json
from types import SimpleNamespace

import pytest

from aidm_server.turn_roll_policy import TurnRollPolicy


def _turn(**overrides):
    values = {
        'requires_roll': False,
        'outcome_status': 'resolved',
        'roll_value': None,
        'rule_type': None,
        'rules_hint': '{}',
        'player_id': 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('Everyone roll initiative.', True),
        ('All of you make a Wisdom saving throw.', True),
        ('Both of you do not need a roll.', False),
        ('The party crosses without requiring a check.', False),
        ('Lyra should roll stealth.', False),
    ],
)
def test_group_roll_detection_respects_explicit_no_roll_language(text, expected):
    assert TurnRollPolicy.response_requests_group_roll(text) is expected


@pytest.mark.parametrize(
    ('text', 'fallback', 'expected'),
    [
        ('Everyone roll initiative.', None, 'initiative'),
        ('Make an attack with your weapon.', None, 'attack'),
        ('Try to levitate the relic with magic.', None, 'spell'),
        ('Make a Dexterity acrobatics check.', None, 'mobility'),
        ('Test the old inscription.', 'wisdom', 'wisdom'),
        ('Test the old inscription.', None, 'check'),
    ],
)
def test_roll_type_classification_has_stable_fallback(text, fallback, expected):
    assert TurnRollPolicy.roll_type_from_response(text, fallback) == expected


def test_build_roll_gate_uses_group_roster_only_for_group_requests():
    gate = TurnRollPolicy.build_roll_gate(
        turn=_turn(),
        dm_response_text='Everyone roll initiative.',
        response_requests_roll=True,
        group_player_ids=[3, 4, 3],
    )

    assert gate == {
        'scope': 'group',
        'rule_type': 'initiative',
        'required_player_ids': [3, 4],
        'resolved_player_ids': [],
        'remaining_player_ids': [3, 4],
        'roll_spec': {
            'die': 'd20',
            'mode': 'normal',
            'rule_type': 'initiative',
            'result_visibility': 'hidden_until_landed',
        },
    }


def test_build_roll_gate_prioritizes_pvp_contest_metadata():
    gate = TurnRollPolicy.build_roll_gate(
        turn=_turn(
            requires_roll=True,
            outcome_status='deferred',
            rule_type='attack',
            rules_hint=json.dumps({'pvp': {'target_player_id': '8'}}),
        ),
        dm_response_text='Roll an attack.',
        response_requests_roll=True,
        group_player_ids=[3, 5, 8],
    )

    assert gate == {
        'scope': 'pvp_contest',
        'rule_type': 'attack',
        'required_player_ids': [3, 8],
        'resolved_player_ids': [],
        'remaining_player_ids': [3, 8],
        'target_player_id': 8,
        'roll_spec': {
            'die': 'd20',
            'mode': 'normal',
            'rule_type': 'attack',
            'result_visibility': 'hidden_until_landed',
        },
    }


def test_build_roll_gate_keeps_pvp_target_pending_after_actor_authoritative_roll():
    roll_spec = {'die': 'd20', 'mode': 'normal', 'ability': {'key': 'strength'}}
    gate = TurnRollPolicy.build_roll_gate(
        turn=_turn(
            requires_roll=True,
            outcome_status='resolved',
            roll_value=18,
            rule_type='attack',
            rules_hint=json.dumps(
                {
                    'pvp': {'target_player_id': 8},
                    'roll_spec': roll_spec,
                }
            ),
        ),
        dm_response_text='The defender must roll an opposed check.',
        response_requests_roll=True,
        group_player_ids=[3, 8],
    )

    assert gate == {
        'scope': 'pvp_contest',
        'rule_type': 'attack',
        'required_player_ids': [3, 8],
        'resolved_player_ids': [3],
        'remaining_player_ids': [8],
        'target_player_id': 8,
        'roll_spec': {
            'die': 'd20',
            'mode': 'normal',
            'rule_type': 'attack',
            'result_visibility': 'hidden_until_landed',
            'ability': {'key': 'strength'},
        },
    }


def test_build_roll_gate_returns_none_for_resolved_or_unrequested_turns():
    assert TurnRollPolicy.build_roll_gate(
        turn=_turn(),
        dm_response_text='The door opens.',
        response_requests_roll=False,
        group_player_ids=[],
    ) is None
    assert TurnRollPolicy.build_roll_gate(
        turn=_turn(requires_roll=True, outcome_status='deferred', roll_value=17),
        dm_response_text='Roll a check.',
        response_requests_roll=True,
        group_player_ids=[],
    ) is None
