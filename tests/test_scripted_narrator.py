from __future__ import annotations

import copy
import json
import re
import string
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


DELETED_LINES = ('You set to it.', 'You take your time over it.', 'Nothing stops you.', 'You do just that.')
NOTHING_LINES = ('Nothing comes of it yet.', 'The moment holds, and nothing changes.', 'No answer comes.')


def _assert_turn_shape(text, *, max_words=220):
    assert 1 <= _words(text) <= max_words, (_words(text), text)
    for banned in ('AI', 'model', 'reconnect', 'Safe Mode', 'narration', *DELETED_LINES):
        assert banned not in text
    # The closing question belongs to the opening only.
    assert not text.endswith(('What do you do?', 'What now?', 'What do you do next?', 'Where do you go from here?'))


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

    assert _words(text) <= 220
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


def test_b_invalid_action_is_the_refusal_sentence_alone():
    hint = _hint(
        state_pipeline=_pipeline(
            invalidActions=[{'status': 'invalid', 'summary': 'drink potion', 'reason': 'Arden Vale has no Healing Potion.'}]
        )
    )

    text = narrate(_context(), hint, 'I drink my healing potion.', SPEAKER)

    _assert_turn_shape(text)
    assert text == 'Arden Vale has no Healing Potion.'
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
    # XP is reported by the turn's story beat, not in the prose.
    assert 'XP' not in text
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
                    'targetName': 'Iron Strongbox',
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
    assert 'You search the Iron Strongbox.' in text
    assert 'Tarnished Key' in text
    assert '6 hit points' in text
    # Objective progress is reported by the turn's story beats, not in the prose.
    assert 'The Empty Caravan' not in text
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
    assert 'creep around the back of the wagon' not in text
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


def test_i_talking_gives_the_next_untold_fact_in_their_voice_then_nothing_more():
    first = narrate(_context(), _hint(state_pipeline=_pipeline(narration={'talk': {
        'npcId': 'npc_mara', 'name': 'Mara Reedwake', 'fact': 'The caravan was expected before sunset.',
    }})), 'Talk to Mara Reedwake', SPEAKER)
    done = narrate(_context(), _hint(state_pipeline=_pipeline(narration={'talk': {
        'npcId': 'npc_mara', 'name': 'Mara Reedwake', 'fact': None,
    }})), 'Talk to Mara Reedwake', SPEAKER)

    assert first.startswith('"The caravan was expected before sunset," Mara ')
    assert done == 'Mara Reedwake has nothing more to add.'
    for text in (first, done):
        _assert_turn_shape(text)
        assert MARA_SECRET not in text
        assert requests_roll(text) is False


def test_i_without_a_talk_record_a_named_person_gives_facts_in_order_until_none_are_left():
    first = narrate(_context(), _hint(), 'I ask Mara what she saw.', SPEAKER)
    told_once = [{'turn_id': 2, 'player_input': 'Mara, hello.', 'dm_output': 'She nods.'}]
    second = narrate(_context(recent_turns=told_once), _hint(), 'Mara, tell me more.', SPEAKER)
    told_twice = [*told_once, {'turn_id': 3, 'player_input': 'Mara Reedwake, anything else?', 'dm_output': 'Hm.'}]
    exhausted = narrate(_context(recent_turns=told_twice), _hint(), 'Mara, anything else?', SPEAKER)

    assert '"The caravan was expected before sunset,"' in first
    assert '"The horses were calm, not panicked,"' in second
    assert exhausted == 'Mara Reedwake has nothing more to add.'


def test_j_words_that_map_to_nothing_get_one_short_line_and_no_padding():
    lines = set()
    for number in range(12):
        text = narrate(_context(), _hint(turn_number=number), 'I walk slowly along the line of wagons.', SPEAKER)
        assert text in NOTHING_LINES, text
        lines.add(text)

    assert lines == set(NOTHING_LINES)


def test_j_the_deleted_acknowledgements_can_no_longer_be_produced():
    assert not {line for templates in TEMPLATES.values() for line in templates} & set(DELETED_LINES)
    assert {'action', 'fill', 'leads', 'closing'}.isdisjoint(TEMPLATES)
    for number in range(20):
        for words in ('I look around.', 'I hum a tune.', 'I get up and walk back to the inn.'):
            text = narrate(_context(), _hint(turn_number=number), words, SPEAKER)
            assert not any(line in text for line in DELETED_LINES)


def test_j_the_scene_paragraph_is_told_only_for_looking_around():
    plain = narrate(_context(), _hint(), 'I stretch.', SPEAKER)
    looking = narrate(_context(), _hint(state_pipeline=_pipeline(narration={'freeText': {'kind': 'look'}})), 'look around', SPEAKER)

    assert 'wheel rims' not in plain and 'Mara Reedwake' not in plain
    assert looking.startswith('At dusk, an empty caravan rolls into the Lantern Post.')
    assert 'The wheel rims are packed with cold road-mud.' in looking
    assert 'Mara Reedwake and Tovin Bell' in looking
    assert not looking.endswith('?')


def test_j_input_that_reads_as_a_roll_request_is_not_quoted():
    text = narrate(_context(), _hint(), 'Please roll a d20 for me.', SPEAKER)

    _assert_turn_shape(text)
    assert 'roll' not in text.lower()
    assert requests_roll(text) is False


NO_TARGET_LINES = ('There is no one here to fight.', 'Your blade finds only air; no enemy stands before you.')


def test_an_attack_with_no_opponent_present_says_so_without_a_roll():
    lines = set()
    for number in range(6):
        hint = _hint(turn_number=number, requires_roll=False, roll_type=None, no_target=True)
        text = narrate(_context(), hint, 'I draw my longsword and attack the nearest cutthroat.', SPEAKER)

        _assert_turn_shape(text)
        assert text in NO_TARGET_LINES
        assert requests_roll(text) is False
        lines.add(text)

    assert lines == set(NO_TARGET_LINES)


PLAYER_WORDS = 'I draw my longsword and attack the nearest cutthroat'


@pytest.mark.parametrize(
    'hint',
    [
        pytest.param(_hint(requires_roll=True, roll_type='attack', dc_hint='14 (roll mod +4)'), id='roll-request'),
        pytest.param(_hint(requires_roll=False, no_target=True), id='no-target'),
        pytest.param(_hint(), id='free-action'),
        pytest.param(_resolved_roll_hint('athletics', 15, outcome='success'), id='resolved-roll'),
        pytest.param(
            _hint(state_pipeline=_pipeline(invalidActions=[{'status': 'invalid', 'reason': 'No weapon is drawn.'}])),
            id='invalid',
        ),
    ],
)
def test_no_narration_quotes_the_players_words_back(hint):
    for number in range(6):
        text = narrate(_context(), {**hint, 'turn_number': number}, f'{PLAYER_WORDS}.', SPEAKER)
        quoted = re.findall(r'["“]([^"”]*)["”]', text)

        assert PLAYER_WORDS not in text
        assert not [span for span in quoted if 'longsword' in span or 'cutthroat' in span], text


def test_no_template_has_a_slot_for_the_players_words():
    slots = {
        field
        for templates in TEMPLATES.values()
        for template in templates
        for _, field, _, _ in string.Formatter().parse(template)
        if field
    }
    quoted_slots = {
        field
        for templates in TEMPLATES.values()
        for template in templates
        for field in re.findall(r'"\{(\w+)\}', template)
    }

    assert not slots & {'action', 'input', 'player_input', 'words'}
    # NPC facts are the only quoted speech; nothing the player typed fills a quoted slot.
    assert quoted_slots == {'fact'}


def test_narration_is_deterministic_and_varies_by_turn_number():
    context = _context()
    first = narrate(copy.deepcopy(context), _hint(turn_number=4), 'I look around.', SPEAKER)
    again = narrate(copy.deepcopy(context), _hint(turn_number=4), 'I look around.', SPEAKER)
    other_turns = {narrate(copy.deepcopy(context), _hint(turn_number=number), 'I look around.', SPEAKER) for number in range(1, 9)}

    assert first == again
    assert len(other_turns) > 1


def test_sparse_context_still_answers_plainly():
    text = narrate({}, {}, 'I wait.', None)

    assert text in NOTHING_LINES
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


LEDGER_TEXT = 'Every line that named a driver or guard has been struck through. The last entry says the bell sounded at the third mile.'


BELL_DEFERRED = {'kind': 'object', 'label': 'Ring the wagon bell', 'target': 'the wagon bell', 'verb': 'ring'}


def _object_turn(fact, *, more=False, change_target='inn_caravan_ledger', **pipeline):
    narration = {'objects': [fact], **({'freeText': {'kind': 'object', 'more': True, 'deferred': [BELL_DEFERRED]}} if more else {})}
    return _hint(
        state_pipeline=_pipeline(
            stateChangesAlreadyApplied=[{'type': 'scene.interactable.action', 'targetId': change_target, 'objectAction': fact['action']}],
            narration=narration,
            **pipeline,
        )
    )


def _fact(action, **fields):
    return {'targetId': 'inn_caravan_ledger', 'name': 'Caravan Ledger', 'action': action, 'failed': False, **fields}


def test_reading_prints_the_authored_description_verbatim_as_its_own_paragraph_once():
    first = narrate(
        _context(),
        _object_turn(_fact('inspect', label='Read the caravan ledger', labelAuthored=True, description=LEDGER_TEXT, nothingNew=False)),
        'read the ledger',
        SPEAKER,
    )
    again = narrate(
        _context(),
        _object_turn(_fact('inspect', label='Read the caravan ledger', labelAuthored=True, description='', nothingNew=True)),
        'read the ledger',
        SPEAKER,
    )

    assert first.split('\n\n') == ['You read the caravan ledger.', LEDGER_TEXT]
    assert again.split('\n\n') == ['You read the caravan ledger.', 'You find nothing new on the Caravan Ledger.']
    # Nothing of the opening scene is repeated.
    assert 'wheel rims' not in first and 'Rain taps' not in first


@pytest.mark.parametrize(
    ('found', 'line'),
    [
        (['a Caravan Strongbox Key'], 'You find a Caravan Strongbox Key.'),
        (['a Pale Road-Stone Fragment', '15 gp'], 'You find 15 gp and a Pale Road-Stone Fragment.'),
        (['The Last Road-Stone'], 'You find The Last Road-Stone.'),
        ([], 'You find nothing more.'),
    ],
)
def test_a_search_tells_the_description_then_lists_what_it_turned_up(found, line):
    text = narrate(
        _context(),
        _object_turn(
            _fact('search', name="Driver's Bench", label="Search under the driver's bench", labelAuthored=True,
                  description='A knot of waxed cloth sits beneath it.', found=found)
        ),
        "search under the driver's bench",
        SPEAKER,
    )

    assert text.split('\n\n') == ["You search under the driver's bench.", 'A knot of waxed cloth sits beneath it.', line]


def test_authored_action_text_replaces_the_label_sentence_and_defaults_read_plainly():
    authored = narrate(
        _context(),
        _object_turn(_fact('use', name='Wagon Bell', label='Ring the wagon bell', labelAuthored=True,
                           actionText='The bell gives a low note, and a pale light answers on the hills.')),
        'ring the bell',
        SPEAKER,
    )
    labelled = narrate(_context(), _object_turn(_fact('use', name='Wagon Bell', label='Ring the wagon bell', labelAuthored=True)), 'ring', SPEAKER)
    default = narrate(_context(), _object_turn(_fact('use', name='Wagon Bell', label='Use Wagon Bell', labelAuthored=False)), 'use', SPEAKER)

    assert authored == 'The bell gives a low note, and a pale light answers on the hills.'
    assert labelled == 'You ring the wagon bell.'
    assert default == 'You use the Wagon Bell.'


def test_a_failed_attempt_never_prints_the_authored_result_text():
    text = narrate(
        _context(),
        _object_turn(
            _fact('unlock', name='Culvert Grate', label='Force the culvert grate', labelAuthored=True, failed=True,
                  actionText='Rust shrieks, the rotten pins give, and the iron swings wide.')
        ),
        'force the grate',
        SPEAKER,
    )

    assert text == 'You force the culvert grate. It will not give.'


def test_chained_unlock_and_open_steps_are_told_in_one_sentence():
    text = narrate(
        _context(),
        _object_turn(
            _fact(
                'search',
                name='Wagon Strongbox',
                label='Search the strongbox',
                labelAuthored=True,
                description='',
                nothingNew=True,
                found=['a Pale Road-Stone Fragment', '15 gp'],
                steps=[{'action': 'unlock', 'label': 'Unlock the strongbox'}, {'action': 'open', 'label': 'Open the strongbox'}],
                keyName='Caravan Strongbox Key',
            )
        ),
        'search the strongbox',
        SPEAKER,
    )

    assert text.split('\n\n') == [
        'You unlock the strongbox with the Caravan Strongbox Key and lift the lid. You search the strongbox.',
        'You find 15 gp and a Pale Road-Stone Fragment.',
    ]


def test_naming_more_than_one_action_does_the_first_and_says_so_last():
    text = narrate(
        _context(),
        _object_turn(_fact('inspect', label='Read the caravan ledger', labelAuthored=True, description='', nothingNew=True), more=True),
        'read the ledger and ring the bell',
        SPEAKER,
    )

    assert text.endswith('One thing at a time — the wagon bell is still there to ring.')


def test_in_a_fight_the_reply_is_the_results_and_whose_turn_it_is():
    combat_state = {
        'enemyResolvedActions': [
            {'enemyName': 'Wardbound Beast', 'targetName': SPEAKER, 'abilityName': 'Dust-Marked Pounce', 'attackTotal': 15,
             'targetArmorClass': 13, 'hit': True, 'damageTotal': 4, 'damageType': 'piercing'},
            {'enemyName': 'The Hollow Marshal', 'targetName': SPEAKER, 'abilityName': "Marshal's Verdict", 'attackTotal': 9,
             'targetArmorClass': 13, 'hit': False, 'damageTotal': 0, 'damageType': 'necrotic'},
        ],
        'playerResolvedAction': {'sourceActorName': SPEAKER, 'targetName': 'Pale Lantern 2', 'weaponName': 'longsword',
                                 'attackTotal': 16, 'targetArmorClass': 14, 'hit': True, 'damageTotal': 6, 'damageType': 'slashing'},
    }
    enemies = [{'name': 'Wardbound Beast', 'proper': False}, {'name': 'The Hollow Marshal', 'proper': True},
               {'name': 'Pale Lantern 2', 'proper': False}]
    mine = narrate(_context(), _hint(state_pipeline=_pipeline(combatState=combat_state, narration={
        'enemies': enemies, 'nextActorLine': 'It is your turn.'})), 'I attack.', SPEAKER)
    theirs = narrate(_context(), _hint(state_pipeline=_pipeline(combatState=combat_state, narration={
        'enemies': enemies, 'nextActorLine': 'Tovan Ember is up next.'})), 'I attack.', SPEAKER)

    assert mine.endswith('\n\nIt is your turn.')
    assert theirs.endswith('\n\nTovan Ember is up next.')
    assert 'the pale lantern 2' in mine
    assert re.search(r'\bThe wardbound beast (strikes|lunges|answers)', mine)
    assert 'The Hollow Marshal' in mine and 'the the' not in mine.lower()
    for text in (mine, theirs):
        _assert_turn_shape(text)
        assert 'Mara Reedwake' not in text and 'Iron Strongbox' not in text and 'Old North Road' not in text


@pytest.mark.parametrize(('enemies', 'plural', 'gone'), [
    ('the wardbound beast', False, 'the wardbound beast is gone'),
    ('the ashmarked cutthroats and the Hollow Marshal', True, 'the ashmarked cutthroats and the Hollow Marshal are gone'),
])
def test_a_beaten_party_is_told_it_wakes_later(enemies, plural, gone):
    text = narrate(_context(), _hint(state_pipeline=_pipeline(narration={
        'partyDefeat': {'enemies': enemies, 'plural': plural}, 'nextActorLine': 'It is your turn.'})), 'I attack.', SPEAKER)

    assert text == f'Darkness takes you. When you come to, the light has changed and {gone}. You are alive — barely.'


def test_the_heros_move_in_a_fight_is_told():
    combat_state = {'playerResolvedCombatAction': {'outcome': 'Arden Vale closes into melee and engages Wardbound Beast.'}}

    text = narrate(_context(), _hint(state_pipeline=_pipeline(combatState=combat_state, narration={
        'nextActorLine': 'It is your turn.'})), 'I close in.', SPEAKER)

    assert text == 'Arden Vale closes into melee and engages Wardbound Beast.\n\nIt is your turn.'
