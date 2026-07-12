from __future__ import annotations

from aidm_server.roll_visibility import (
    public_action_intent_payload,
    public_roll_payload,
    public_rules_hint_payload,
    public_segment_triggered_payload,
    public_turn_event_payload,
)


def _private_roll() -> dict:
    return {
        'rule_type': 'social',
        'die': 'd20',
        'mode': 'advantage',
        'rolls': [8, 17],
        'kept': 17,
        'modifier': 4,
        'total': 21,
        'reason': 'persuade the guard',
        'result_visibility': 'hidden_until_landed',
        'dc_hint': 'DC 14; CHA 18; persuasion proficiency; wound penalty 2',
        'ability': {'key': 'charisma', 'label': 'CHA', 'score': 18, 'modifier': 4},
        'proficiency': {'bonus': 2, 'skills': ['persuasion']},
        'modifier_breakdown': {
            'ability_modifier': 4,
            'proficiency_bonus': 2,
            'wound_penalty': 2,
            'total': 4,
        },
    }


def test_public_roll_payload_keeps_outcome_and_removes_character_provenance():
    private = _private_roll()

    public = public_roll_payload(private)

    assert public == {
        'rule_type': 'social',
        'die': 'd20',
        'mode': 'advantage',
        'rolls': [8, 17],
        'kept': 17,
        'modifier': 4,
        'total': 21,
        'reason': 'persuade the guard',
        'result_visibility': 'hidden_until_landed',
    }
    assert private['ability']['score'] == 18
    assert 'ability' not in public
    assert 'proficiency' not in public
    assert 'modifier_breakdown' not in public
    assert 'dc_hint' not in public


def test_public_rules_and_action_intent_remove_parallel_provenance_paths():
    private_roll = _private_roll()
    rules_hint = {
        'requires_roll': True,
        'roll_type': 'social',
        'dc_hint': 'DC 14; CHA 18; persuasion proficiency; wound penalty 2',
        'roll_spec': {**private_roll, 'task_dc': 14},
        'authoritative_roll': private_roll,
        'roll_gate': {
            'scope': 'single_player',
            'roll_spec': {**private_roll, 'task_dc': 14},
        },
    }
    action_intent = {
        'kind': 'roll',
        'ability': {'key': 'charisma', 'label': 'CHA', 'score': 18, 'modifier': 4},
        'roll': private_roll,
        'attack': {'weapon': {'name': 'DM_ONLY_WEAPON'}, 'proficient': True},
        'task_dc': 14,
        'modifier': 4,
        'proficiency': {'skills': ['persuasion']},
    }

    public_rules = public_rules_hint_payload(rules_hint)
    public_intent = public_action_intent_payload(action_intent)

    assert 'dc_hint' not in public_rules
    assert set(public_rules['roll_spec']) == {
        'die',
        'mode',
        'rule_type',
        'reason',
        'result_visibility',
    }
    assert 'ability' not in public_rules['authoritative_roll']
    assert 'modifier_breakdown' not in public_rules['roll_gate']['roll_spec']
    assert 'proficiency' not in public_intent['roll']
    assert public_intent['ability'] == {'key': 'charisma', 'label': 'CHA'}
    assert 'attack' not in public_intent
    assert 'task_dc' not in public_intent
    assert 'modifier' not in public_intent
    assert 'proficiency' not in public_intent


def test_public_turn_event_redacts_roll_and_metadata_copies_without_mutating_source():
    private_roll = _private_roll()
    event = {
        'roll_value': 21,
        'roll': private_roll,
        'metadata': {
            'dc_hint': 'DC 14; CHA 18; persuasion proficiency; wound penalty 2',
            'action_intent': {'kind': 'roll', 'roll': private_roll},
            'authoritative_roll': private_roll,
            'roll_gate': {'roll_spec': {**private_roll, 'task_dc': 14}},
            'state_pipeline': {
                'clarificationRequest': {
                    'originalAction': {'itemName': 'PRIVATE_WEAPON'},
                    'options': [{'id': 'private', 'label': 'PRIVATE_WEAPON'}],
                },
                'preDmValidation': {
                    'validatedActions': [{'resolvedItem': {'itemName': 'PRIVATE_WEAPON'}}],
                },
            },
        },
    }

    public = public_turn_event_payload(event)

    assert public['roll']['total'] == 21
    assert 'ability' not in public['roll']
    assert 'dc_hint' not in public['metadata']
    assert 'proficiency' not in public['metadata']['authoritative_roll']
    assert 'modifier_breakdown' not in public['metadata']['action_intent']['roll']
    assert 'ability' not in public['metadata']['roll_gate']['roll_spec']
    assert 'state_pipeline' not in public['metadata']
    assert event['roll']['ability']['score'] == 18
    assert event['metadata']['dc_hint'].startswith('DC 14')
    assert event['metadata']['state_pipeline']['clarificationRequest']['options'][0]['label'] == 'PRIVATE_WEAPON'


def test_public_segment_event_keeps_revealed_story_but_hides_trigger_recipe():
    public = public_segment_triggered_payload(
        {
            'segment_id': 9,
            'title': 'The Sealed Door Opens',
            'description': 'Stone grinds aside.',
            'reason': 'keywords:secret altar phrase',
            'trigger_spec': {
                'trigger_type': 'keywords',
                'raw': {'keywords': ['secret altar phrase']},
            },
        }
    )

    assert public == {
        'segment_id': 9,
        'title': 'The Sealed Door Opens',
        'description': 'Stone grinds aside.',
    }
