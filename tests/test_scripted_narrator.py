from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from aidm_server import scripted_narrator
from aidm_server.scripted_narrator import TEMPLATES, narrate, requests_roll
from aidm_server.turn_roll_policy import TurnRollPolicy
from aidm_server.turn_rules import response_mentions_roll_request


SPEAKER = 'Arden Vale'
MARA_SECRET = 'Her husband vanished on this road years ago.'


def _context(**overrides):
    context = {
        'live_world_state': {
            'currentScene': {
                'locationId': 'loc_inn',
                'name': 'The Lantern Post',
                'summary': 'At dusk, an empty caravan rolls into the Lantern Post.',
                'description': 'Rain taps on the inn shutters. The wagons stand intact and silent.',
                'visibleDetails': [
                    'One wagon bell rings though no hand touches it.',
                    'The wheel rims are packed with cold road-mud.',
                    "A cloth-wrapped chip of pale stone rests beneath the driver's bench.",
                    'A faint ash mark stains the rear signboard.',
                ],
                'interactables': [
                    {
                        'id': 'obj_strongbox',
                        'name': 'Iron Strongbox',
                        'kind': 'container',
                        'contents': [{'id': 'itm_key', 'name': 'Tarnished Key'}],
                    }
                ],
            },
            'activeNpcs': [
                {
                    'id': 'npc_mara',
                    'name': 'Mara Reedwake',
                    'role': 'innkeeper_and_local_witness',
                    'disposition': 'cautious_helpful',
                },
                {'id': 'npc_tovin', 'name': 'Tovin Bell', 'role': 'stablehand', 'disposition': 'afraid'},
            ],
            'recentLocations': [
                {'id': 'loc_inn', 'name': 'The Lantern Post', 'connectedLocationIds': ['loc_road', 'loc_secret']},
                {'id': 'loc_road', 'name': 'the Old North Road'},
            ],
            'combat': {
                'status': 'active',
                'participants': [
                    {'id': 'enemy_1', 'name': 'Ashmarked Cutthroat', 'team': 'enemy', 'hp': {'current': 0, 'max': 18}},
                    {'id': 'pc_1', 'name': SPEAKER, 'team': 'player', 'hp': {'current': 9, 'max': 12}},
                ],
            },
        },
        'campaign_pack_director': {
            'relevantRecords': {
                'npcs': [
                    {
                        'id': 'npc_mara',
                        'name': 'Mara Reedwake',
                        'knows': [
                            'The caravan was expected before sunset.',
                            'The horses were calm, not panicked.',
                        ],
                        'secret': MARA_SECRET,
                    }
                ],
                'locations': [{'id': 'loc_secret', 'name': 'Hidden Barrow', 'knownToPlayers': False}],
                'clues': [
                    {'id': 'clue_ledger', 'title': 'Ledger', 'summary': 'Every name in the ledger is scratched out.', 'discovered': True},
                    {
                        'id': 'clue_stone',
                        'title': 'Road-Stone',
                        'summary': 'The stone chip carries half a name-rune, like a broken road map.',
                        'discovered': False,
                    },
                ],
            }
        },
        'recent_turns': [],
    }
    context.update(overrides)
    return context


def _hint(**overrides):
    hint = {'requires_roll': False, 'roll_value': None, 'turn_number': 4}
    hint.update(overrides)
    return hint


def _pipeline(**overrides):
    pipeline = {
        'validatedActions': [],
        'invalidActions': [],
        'stateChangesAlreadyApplied': [],
        'combatState': None,
    }
    pipeline.update(overrides)
    return pipeline


def _words(text):
    return len(text.split())


def _assert_turn_shape(text, *, max_words=110):
    assert 40 <= _words(text) <= max_words, (_words(text), text)
    for banned in ('AI', 'model', 'reconnect', 'Safe Mode', 'narration'):
        assert banned not in text


def test_a_opening_narrates_authored_scene_and_present_npcs_without_a_roll():
    brief = {
        'scene': {
            'name': 'The Caravan Without Names',
            'summary': 'At dusk, an empty caravan rolls into the Lantern Post. Every name in its ledger is gone.',
            'description': 'Rain taps on the shutters.',
            'visibleDetails': [
                'One wagon bell rings three times though no hand touches it.',
                'The wheel rims are packed with cold road-mud.',
                "A cloth-wrapped chip of pale stone rests beneath the driver's bench.",
                'A faint ash mark stains the rear signboard.',
            ],
        },
        'presentNpcs': [
            {'name': 'Mara Reedwake', 'role': 'innkeeper_and_local_witness', 'description': 'Practical.'},
            {'name': 'Tovin Bell', 'role': 'stablehand'},
        ],
        'activeCheckpoint': {'summary': 'Investigate.', 'gmNotes': 'Secret notes for the DM.'},
    }

    text = narrate(_context(), _hint(message_kind='opening', requires_roll=False), '(The adventure begins.)', SPEAKER, brief)

    _assert_turn_shape(text, max_words=220)
    assert text.startswith('At dusk, an empty caravan rolls into the Lantern Post. Every name in its ledger is gone.')
    assert 'One wagon bell rings three times though no hand touches it.' in text
    assert "A cloth-wrapped chip of pale stone rests beneath the driver's bench." in text
    assert 'A faint ash mark stains the rear signboard.' not in text
    assert 'Mara Reedwake and Tovin Bell are here with you.' in text
    assert text.endswith('What do you do?')
    assert 'Secret notes' not in text
    assert requests_roll(text) is False


def test_a_opening_names_the_cast_without_their_authoring_roles():
    brief = {
        'scene': {'summary': 'At dusk, an empty caravan rolls into the Lantern Post.'},
        'presentNpcs': [
            {'name': 'Mara Reedwake', 'role': 'innkeeper_and_local_witness'},
            {'name': 'Tovin Bell', 'role': 'stablehand and accidental witness'},
            {'name': 'Osric Hale', 'role': 'local_reeve'},
        ],
    }

    text = narrate(_context(), _hint(message_kind='opening', turn_number=1), '(The adventure begins.)', SPEAKER, brief)

    assert 'Within earshot are Mara Reedwake, Tovin Bell, and Osric Hale.' in text
    for role_word in ('innkeeper', 'witness', 'stablehand', 'reeve', '('):
        assert role_word not in text


def test_b_invalid_action_is_explained_plainly():
    hint = _hint(
        state_pipeline=_pipeline(
            invalidActions=[{'status': 'invalid', 'summary': 'drink potion', 'reason': 'Arden Vale has no Healing Potion.'}]
        )
    )

    text = narrate(_context(), hint, 'I drink my healing potion.', SPEAKER)

    _assert_turn_shape(text)
    assert text.startswith('That will not work: Arden Vale has no Healing Potion.')
    assert requests_roll(text) is False


def test_c_engine_resolved_combat_is_narrated_with_exact_results_in_order():
    player_attack = {
        'sourceActorName': SPEAKER,
        'targetName': 'Ashmarked Cutthroat',
        'weaponName': 'longsword',
        'attackTotal': 17,
        'targetArmorClass': 13,
        'hit': True,
        'damageTotal': 9,
        'damageType': 'slashing',
    }
    combat_state = {
        'playerResolvedAction': player_attack,
        'trustedDamageEvents': [dict(player_attack)],
        'enemyResolvedActions': [
            {
                'enemyName': 'Road Thief',
                'targetName': SPEAKER,
                'abilityName': 'Hooked Road-Knife',
                'attackTotal': 11,
                'targetArmorClass': 15,
                'hit': False,
                'damageTotal': 0,
                'damageType': 'piercing',
                'conditionsApplied': [],
            },
            {
                'enemyName': 'Ash Witch',
                'targetName': SPEAKER,
                'abilityName': 'Cinder Hex',
                'saveAbility': 'wisdom',
                'saveDC': 13,
                'saveTotal': 9,
                'saveSucceeded': False,
                'damageTotal': 4,
                'damageType': 'necrotic',
                'conditionsApplied': ['frightened'],
            },
        ],
    }
    hint = _hint(
        roll_value=17,
        requires_roll=True,
        state_pipeline=_pipeline(
            combatState=combat_state,
            stateChangesAlreadyApplied=[
                {'type': 'combat.end', 'combatStatus': 'ended'},
                {'type': 'xp.add', 'xp': 50, 'amount': 50},
                {'type': 'currency.add', 'amount': 12, 'currency': 'gp'},
            ],
        ),
    )

    text = narrate(_context(), hint, 'I attack the cutthroat.', SPEAKER)

    _assert_turn_shape(text)
    assert text.count('Ashmarked Cutthroat') >= 2
    assert '17 against AC 13' in text
    assert '9 slashing damage' in text
    assert '11 against AC 15' in text
    assert 'Wisdom save of 9 against DC 13, a failure, taking 4 necrotic damage' in text
    assert 'You are now frightened' in text or 'leaves you frightened' in text or 'you are frightened' in text
    assert 'fight is over' in text
    assert '50 XP' in text
    assert '12 gold' in text
    assert text.index('17 against AC 13') < text.index('11 against AC 15') < text.index('fight is over')
    assert requests_roll(text) is False


def test_c_death_save_events_are_narrated_defensively():
    hint = _hint(
        state_pipeline=_pipeline(
            stateChangesAlreadyApplied=[
                {'type': 'combat.death_save', 'participantName': SPEAKER, 'roll': 12, 'result': 'success'},
                {'type': 'combat.death_save', 'participantName': 'Tovin Bell'},
            ]
        )
    )

    text = narrate(_context(), hint, 'I hold on.', SPEAKER)

    _assert_turn_shape(text)
    assert ': 12, success' in text
    assert 'Tovin Bell' in text
    assert requests_roll(text) is False


def test_d_resolved_spell_reports_save_damage_condition_and_concentration():
    resolution = {
        'spellName': 'Frost Lance',
        'targets': [
            {
                'targetName': 'Ashmarked Cutthroat',
                'delivery': {'type': 'save', 'ability': 'dexterity', 'total': 8, 'dc': 14, 'saveSucceeded': False},
                'effects': [
                    {'kind': 'damage', 'applied': True, 'amountApplied': 11, 'damageType': 'cold'},
                    {'kind': 'condition', 'operation': 'add', 'condition': 'restrained', 'applied': True},
                ],
            },
            {
                'targetName': 'Road Thief',
                'delivery': {'type': 'attack', 'total': 9, 'targetArmorClass': 14, 'delivered': False},
                'effects': [{'kind': 'damage', 'applied': False, 'reason': 'spell_attack_missed'}],
            },
        ],
        'concentration': {'started': True},
    }
    hint = _hint(
        state_pipeline=_pipeline(
            stateChangesAlreadyApplied=[{'type': 'spell.cast', 'spellName': 'Frost Lance', 'spellResolution': resolution}]
        )
    )

    text = narrate(_context(), hint, 'I cast Frost Lance.', SPEAKER)

    _assert_turn_shape(text)
    assert 'Dexterity save of 8 against DC 14, a failure' in text
    assert '11 cold damage' in text
    assert 'restrained' in text
    assert '9 against AC 14, a miss' in text
    assert 'Frost Lance' in text
    assert 'concentration' in text or 'focus' in text or 'steady' in text
    assert requests_roll(text) is False


def test_e_class_capability_use_names_the_feature_and_healing():
    hint = _hint(
        state_pipeline=_pipeline(
            stateChangesAlreadyApplied=[
                {'type': 'class_feature.use', 'capabilityName': 'Second Wind', 'effectType': 'self_heal', 'amount': 7},
                {'type': 'class_feature.use', 'capabilityName': 'Action Surge', 'effectType': 'extra_action'},
            ]
        )
    )

    text = narrate(_context(), hint, 'I catch my breath and press on.', SPEAKER)

    _assert_turn_shape(text)
    assert 'Second Wind' in text
    assert '7 hit points' in text
    assert 'Action Surge' in text
    assert 'extra action' in text
    assert requests_roll(text) is False


def test_f_travel_objects_items_rest_and_quests_are_narrated():
    context = _context()
    hint = _hint(
        state_pipeline=_pipeline(
            stateChangesAlreadyApplied=[
                {'type': 'scene.move_location', 'locationName': 'The Lantern Post'},
                {
                    'type': 'scene.interactable.action',
                    'objectAction': 'search',
                    'targetId': 'obj_strongbox',
                    'targetName': None,
                    'eventType': 'searched',
                },
                {'type': 'inventory.add', 'itemName': 'Tarnished Key', 'amount': 1},
                {'type': 'rest.complete', 'amount': 6},
                {'type': 'quest.complete', 'questTitle': 'The Empty Caravan'},
            ]
        )
    )

    text = narrate(context, hint, 'I head to the inn and search the strongbox.', SPEAKER)

    _assert_turn_shape(text)
    assert 'The Lantern Post' in text
    assert 'Mara Reedwake and Tovin Bell' in text
    assert 'search the Iron Strongbox' in text
    assert 'Tarnished Key' in text
    assert '6 hit points' in text
    assert 'The Empty Caravan' in text
    assert requests_roll(text) is False


def _resolved_roll_hint(rule_type, total, **roll_fields):
    return _hint(
        requires_roll=True,
        roll_value=total,
        roll_type=rule_type,
        resolved_turn_id=21,
        authoritative_roll={'rule_type': rule_type, 'total': total, **roll_fields},
    )


def test_g_successful_investigation_reveals_an_undiscovered_clue():
    hint = _resolved_roll_hint('investigation', 17, task_dc=13, outcome='success')

    text = narrate(_context(), hint, 'I roll.', SPEAKER)

    _assert_turn_shape(text)
    assert text.startswith('Intelligence (Investigation): 17 against DC 13 — success.')
    assert 'The stone chip carries half a name-rune, like a broken road map.' in text
    assert 'Every name in the ledger is scratched out.' not in text


def test_g_failed_perception_finds_nothing_new_and_outcome_is_computed_when_absent():
    hint = _resolved_roll_hint('perception', 6, task_dc=13)

    text = narrate(_context(), hint, 'I roll.', SPEAKER)

    _assert_turn_shape(text)
    assert text.startswith('Wisdom (Perception): 6 against DC 13 — failure.')
    assert 'road map' not in text
    assert any(
        phrase in text
        for phrase in ('nothing new', 'stays hidden', 'did not already know')
    )


def test_g_stealth_outcome_names_the_npc_from_the_original_action():
    context = _context(
        session_memory={'recent_beats': [{'turn_id': 21, 'player_input': 'I sneak past Tovin to the stables.'}]}
    )
    success = narrate(context, _resolved_roll_hint('stealth', 19, dc=14, outcome='success'), 'I roll.', SPEAKER)
    failure = narrate(context, _resolved_roll_hint('stealth', 5, dc=14, outcome='failure'), 'I roll.', SPEAKER)

    _assert_turn_shape(success)
    _assert_turn_shape(failure)
    assert success.startswith('Dexterity (Stealth): 19 against DC 14 — success.')
    assert failure.startswith('Dexterity (Stealth): 5 against DC 14 — failure.')
    assert 'Tovin Bell' in success.split('\n\n')[1]
    assert 'Tovin Bell' in failure.split('\n\n')[1]


@pytest.mark.parametrize(
    ('rule_type', 'label'),
    [
        ('athletics', 'Strength (Athletics)'),
        ('persuasion', 'Charisma (Persuasion)'),
        ('wisdom', 'Wisdom'),
    ],
)
def test_g_roll_families_have_success_and_failure_lines_and_omit_missing_dc(rule_type, label):
    success = narrate(_context(), _resolved_roll_hint(rule_type, 18, outcome='success'), 'I roll.', SPEAKER)
    failure = narrate(_context(), _resolved_roll_hint(rule_type, 3, outcome='failure'), 'I roll.', SPEAKER)

    assert success.startswith(f'{label}: 18 — success.')
    assert failure.startswith(f'{label}: 3 — failure.')
    assert success.split('\n\n')[1] != failure.split('\n\n')[1]
    for text in (success, failure):
        _assert_turn_shape(text)
        assert requests_roll(text) is False


def test_h_required_roll_ends_with_check_request_the_server_turns_into_a_gate():
    hint = _hint(requires_roll=True, roll_type='stealth', dc_hint='13 (roll mod +3, DEX 16 mod +3)')

    text = narrate(_context(), hint, 'I creep around the back of the wagon.', SPEAKER)

    _assert_turn_shape(text)
    assert text.endswith('Make a Dexterity (Stealth) check, DC 13.')
    assert '"I creep around the back of the wagon"' in text
    turn = SimpleNamespace(
        requires_roll=True,
        outcome_status='deferred',
        roll_value=None,
        rule_type='stealth',
        rules_hint=json.dumps({'dc_hint': '13 (roll mod +3, DEX 16 mod +3)'}),
        player_id=3,
    )
    gate = TurnRollPolicy.build_roll_gate(
        turn=turn,
        dm_response_text=text,
        response_requests_roll=response_mentions_roll_request(text),
        group_player_ids=[3],
    )
    assert gate is not None
    assert gate['rule_type'] == 'stealth'
    assert gate['dc_hint'] == 'DC 13'
    assert json.loads(turn.rules_hint)['dc_hint'] == 'DC 13'


@pytest.mark.parametrize(
    ('rule_type', 'dc_hint', 'expected'),
    [
        ('social', None, 'Make a Charisma (Persuasion) check, DC 15.'),
        ('lore', '12-17', 'Make an Intelligence (Investigation) check, DC 13.'),
        ('perception', 'DC 11', 'Make a Wisdom (Perception) check, DC 11.'),
        ('dexterity_saving_throw', None, 'Make a Dexterity saving throw, DC 14.'),
    ],
)
def test_h_roll_request_uses_rule_type_label_and_hint_or_base_dc(rule_type, dc_hint, expected):
    text = narrate(_context(), _hint(requires_roll=True, roll_type=rule_type, dc_hint=dc_hint), 'I try it.', SPEAKER)

    assert text.endswith(expected)
    assert requests_roll(text) is True


def test_i_talking_to_a_present_npc_shares_what_they_know_in_order():
    first = narrate(_context(), _hint(), 'I ask Mara what she saw.', SPEAKER)
    later_context = _context(
        recent_turns=[
            {'turn_id': 2, 'player_input': 'I wave to Mara.', 'dm_output': 'Mara nods.'},
            {'turn_id': 3, 'player_input': 'I look around.', 'dm_output': 'Rain falls.'},
            {'turn_id': 4, 'player_input': 'Mara Reedwake, anything else?', 'dm_output': 'She thinks.'},
        ]
    )
    capped = narrate(later_context, _hint(), 'Mara, tell me more.', SPEAKER)

    _assert_turn_shape(first)
    _assert_turn_shape(capped)
    assert first.startswith('Mara Reedwake, the innkeeper and local witness,')
    assert 'weighing each word' in first
    assert '"The caravan was expected before sunset,"' in first
    assert '"The horses were calm, not panicked,"' in capped
    for text in (first, capped):
        assert MARA_SECRET not in text
        assert requests_roll(text) is False


ACTION_ACKNOWLEDGEMENTS = ('You set to it.', 'You take your time over it.', 'Nothing stops you.', 'You do just that.')


def test_j_any_other_action_is_acknowledged_without_quoting_adds_a_fresh_detail_and_names_leads():
    previous = 'At dusk, an empty caravan rolls in. One wagon bell rings though no hand touches it.'
    context = _context(recent_turns=[{'turn_id': 3, 'player_input': 'I look.', 'dm_output': previous}])

    text = narrate(context, _hint(), 'I walk slowly along the line of wagons, ' + 'looking closely ' * 20, SPEAKER)

    _assert_turn_shape(text)
    assert text.startswith(ACTION_ACKNOWLEDGEMENTS)
    assert '"' not in text
    assert 'line of wagons' not in text
    assert 'looking closely' not in text
    assert 'One wagon bell rings' not in text
    assert 'The wheel rims are packed with cold road-mud.' in text
    assert 'Mara Reedwake' in text
    assert 'the Iron Strongbox' in text
    assert 'the way to the Old North Road' in text
    assert 'Hidden Barrow' not in text
    assert requests_roll(text) is False


def test_j_acknowledgement_is_chosen_by_turn_from_the_four_short_lines():
    acknowledgements = set()
    for number in range(10):
        hint = _hint(turn_number=number)
        text = narrate(_context(), hint, 'I look around.', SPEAKER)
        assert text == narrate(_context(), hint, 'I look around.', SPEAKER)
        acknowledgements.update(line for line in ACTION_ACKNOWLEDGEMENTS if text.startswith(line))

    assert acknowledgements == set(ACTION_ACKNOWLEDGEMENTS)


def test_j_second_scene_detail_is_added_only_when_the_word_floor_needs_it():
    short_text = narrate(_context(), _hint(), 'I look around.', SPEAKER)
    long_detail = (
        'Every wagon in the line has been unhitched with care, its harness hung on the same peg and its lamp trimmed '
        'and cold, as if the drivers meant to come back before dark.'
    )
    long_context = _context()
    long_context['live_world_state']['currentScene']['visibleDetails'] = [long_detail, 'The wheel rims are packed with cold road-mud.']
    long_text = narrate(long_context, _hint(), 'I look around.', SPEAKER)

    first_paragraph, leads_paragraph = short_text.split('\n\n')
    assert first_paragraph.startswith(ACTION_ACKNOWLEDGEMENTS)
    assert 'One wagon bell rings though no hand touches it.' in first_paragraph
    assert 'The wheel rims are packed with cold road-mud.' in first_paragraph
    assert leads_paragraph.startswith('Close at hand: Mara Reedwake, the Iron Strongbox, and the way to the Old North Road.')
    assert long_text.split('\n\n')[0].endswith(long_detail)
    assert 'cold road-mud' not in long_text
    assert 'Close at hand: Mara Reedwake' in long_text.split('\n\n')[1]
    _assert_turn_shape(short_text)
    _assert_turn_shape(long_text)


def test_j_input_that_reads_as_a_roll_request_is_not_quoted():
    text = narrate(_context(), _hint(), 'Please roll a d20 for me.', SPEAKER)

    _assert_turn_shape(text)
    assert 'roll' not in text.lower()
    assert requests_roll(text) is False


def test_narration_is_deterministic_and_varies_by_turn_number():
    context = _context()
    first = narrate(copy.deepcopy(context), _hint(turn_number=4), 'I look around.', SPEAKER)
    again = narrate(copy.deepcopy(context), _hint(turn_number=4), 'I look around.', SPEAKER)
    other_turns = {narrate(copy.deepcopy(context), _hint(turn_number=number), 'I look around.', SPEAKER) for number in range(1, 9)}

    assert first == again
    assert len(other_turns) > 1


def test_sparse_context_still_meets_the_word_floor():
    text = narrate({}, {}, 'I wait.', None)

    _assert_turn_shape(text)
    assert requests_roll(text) is False


class _SampleValues(dict):
    def __missing__(self, key):
        return {'verb': 'is', 'be': 'is', 'amount': '3', 'total': '12', 'ac': '13', 'dc': '14'}.get(key, 'Mara Reedwake')


@pytest.mark.parametrize('template_key', sorted(TEMPLATES))
def test_no_template_reads_as_a_roll_request(template_key):
    for template in TEMPLATES[template_key]:
        text = template.format_map(_SampleValues())
        assert response_mentions_roll_request(text) is False, text
        assert TurnRollPolicy.response_requests_group_roll(text) is False, text
    assert response_mentions_roll_request(scripted_narrator.OPENING_CLOSING_LINE) is False
