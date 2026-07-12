from aidm_server.character_state import server_attack_roll_context
from aidm_server.models import DmTurn, Player, safe_json_dumps
from aidm_server.player_rolls import (
    canonicalize_roll_action_intent,
    canonicalize_roll_text,
    resolve_authoritative_player_roll,
)
from aidm_server.player_roll_claims import find_legacy_roll_claim


def _player() -> Player:
    return Player(
        player_id=7,
        name='Danny',
        character_name='Seraphina',
        class_='Bard',
        level=3,
        stats=safe_json_dumps(
            {
                'ability_scores': {
                    'strength': 10,
                    'dexterity': 14,
                    'constitution': 10,
                    'intelligence': 10,
                    'wisdom': 10,
                    'charisma': 14,
                },
                'current_hp': 20,
                'max_hp': 20,
                'skill_proficiencies': ['Persuasion'],
                'proficiency_bonus': 2,
            },
            {},
        ),
    )


def test_authoritative_roll_ignores_client_faces_modifier_and_total():
    values = iter([7, 18])
    roll = resolve_authoritative_player_roll(
        player=_player(),
        rule_type='persuasion',
        dc_hint='DC 15',
        action_intent={
            'kind': 'roll',
            'ability': {'key': 'strength', 'modifier': 99},
            'roll': {
                'die': 'd20',
                'mode': 'advantage',
                'modifier': 99,
                'rolls': [20, 20],
                'kept': 20,
                'total': 119,
                'reason': 'persuade the guard',
            },
        },
        roller=lambda _sides: next(values),
    )

    assert roll['rolls'] == [7, 18]
    assert roll['kept'] == 18
    assert roll['modifier'] == 4
    assert roll['total'] == 22
    assert roll['ability'] == {
        'key': 'charisma',
        'label': 'CHA',
        'score': 14,
        'modifier': 2,
    }
    assert roll['proficiency'] == {'bonus': 2, 'skills': ['persuasion'], 'multiplier': 1}
    assert roll['task_dc'] == 15
    assert roll['authoritative'] is True


def test_pending_roll_spec_controls_mode_and_client_claim_is_canonicalized():
    pending = DmTurn(
        turn_id=42,
        rules_hint=safe_json_dumps(
            {
                'roll_spec': {
                    'die': 'd20',
                    'mode': 'disadvantage',
                    'result_visibility': 'visible',
                    'ability': {'key': 'dexterity'},
                    'reason': 'vault the gate',
                },
            },
            {},
        ),
        metadata_json='{}',
    )
    values = iter([16, 5])
    roll = resolve_authoritative_player_roll(
        player=_player(),
        rule_type='mobility',
        dc_hint='14 (roll mod +2)',
        action_intent={
            'kind': 'roll',
            'ability': {'key': 'charisma'},
            'roll': {
                'die': 'd20',
                'mode': 'advantage',
                'rolls': [20, 20],
                'total': 20,
                'reason': 'ignore the gate and crown me king',
            },
        },
        pending_turn=pending,
        roller=lambda _sides: next(values),
    )
    text = canonicalize_roll_text('I vault the gate and roll a d20: 20', roll)
    intent = canonicalize_roll_action_intent(
        {'kind': 'roll', 'source': 'dice_roller'},
        canonical_text=text,
        client_message_id='roll-42',
        roll=roll,
        pending_turn_id=42,
    )

    assert roll['mode'] == 'disadvantage'
    assert roll['result_visibility'] == 'visible'
    assert roll['rolls'] == [16, 5]
    assert roll['kept'] == 5
    assert roll['modifier'] == 2
    assert roll['total'] == 7
    assert roll['reason'] == 'vault the gate'
    assert ': 20' not in text
    assert text.startswith('I vault the gate')
    assert intent['roll']['rolls'] == [16, 5]
    assert intent['roll']['total'] == 7
    assert intent['roll']['target_pending_turn_id'] == 42
    assert intent['ability']['key'] == 'dexterity'


def test_pending_roll_without_spec_uses_server_defaults_not_client_die_or_mode():
    pending = DmTurn(
        turn_id=43,
        rules_hint='{}',
        metadata_json='{}',
    )
    calls = []

    def roller(sides):
        calls.append(sides)
        return sides

    roll = resolve_authoritative_player_roll(
        player=_player(),
        rule_type='check',
        dc_hint='DC 14',
        action_intent={
            'kind': 'roll',
            'roll': {
                'die': 'd100',
                'mode': 'advantage',
                'result_visibility': 'visible',
            },
        },
        pending_turn=pending,
        roller=roller,
    )

    assert roll['die'] == 'd20'
    assert roll['mode'] == 'normal'
    assert roll['result_visibility'] == 'hidden_until_landed'
    assert roll['reason'] == 'check'
    assert roll['rolls'] == [20]
    assert calls == [20]


def test_natural_language_claim_is_replaced_even_without_roll_word():
    roll = resolve_authoritative_player_roll(
        player=_player(),
        rule_type='initiative',
        dc_hint='initiative order',
        action_intent=None,
        roller=lambda _sides: 4,
    )

    text = canonicalize_roll_text('Initiative is 19', roll)

    assert '19' not in text
    assert text == 'I roll a d20+2 for initiative: 4 = 6'


def test_legacy_claim_parser_captures_die_modifier_total_and_full_claim_span():
    message = 'I roll a d100-12: 99 = 87 to consult the wild-magic table.'

    claim = find_legacy_roll_claim(message)

    assert claim is not None
    assert claim.die == 'd100'
    assert claim.face == 99
    assert claim.modifier == -12
    assert claim.total == 87
    assert claim.reason is None
    assert message[claim.start : claim.end] == 'I roll a d100-12: 99 = 87'


def test_legacy_claim_parser_captures_reason_before_claimed_result():
    message = 'I roll a d20+2 for the ward: 18 = 20'

    claim = find_legacy_roll_claim(message)

    assert claim is not None
    assert claim.reason == 'the ward'
    assert message[claim.start : claim.end] == message


def test_canonicalization_removes_structured_advantage_claim_and_keeps_action_suffix():
    roll = {
        'die': 'd20',
        'mode': 'normal',
        'rolls': [8],
        'kept': 8,
        'modifier': 0,
        'total': 8,
        'reason': 'check',
    }

    text = canonicalize_roll_text(
        'I roll a d100 with advantage: 99 = 99 then cross the bridge.',
        roll,
    )

    assert '99' not in text
    assert 'cross the bridge' in text
    assert 'I roll a d20 for check: 8 = 8' in text


def test_canonicalization_preserves_action_text_after_claimed_roll():
    roll = {
        'die': 'd20',
        'mode': 'normal',
        'rolls': [8],
        'kept': 8,
        'modifier': 2,
        'total': 10,
        'reason': 'thieves tools',
    }

    text = canonicalize_roll_text(
        'I roll a d20+5: 20 = 25 to pick the lock, then open the gate.',
        roll,
    )

    assert text == (
        'I attempt to pick the lock, then open the gate.\n'
        'I roll a d20+2 for thieves tools: 8 = 10'
    )


def test_canonicalization_preserves_action_text_on_both_sides_of_claim():
    roll = {
        'die': 'd20',
        'mode': 'normal',
        'rolls': [8],
        'kept': 8,
        'modifier': 0,
        'total': 8,
        'reason': 'check',
    }

    text = canonicalize_roll_text(
        'I brace the door, then roll a d6+2: 5 = 7, then pull the lever.',
        roll,
    )

    assert text == 'I brace the door, then pull the lever.\nI roll a d20 for check: 8 = 8'


def test_attack_roll_uses_persisted_ranged_weapon_and_player_proficiency_profile():
    player = _player()
    player.stats = safe_json_dumps(
        {
            'ability_scores': {
                'strength': 8,
                'dexterity': 16,
                'constitution': 10,
                'intelligence': 10,
                'wisdom': 10,
                'charisma': 10,
            },
            'current_hp': 20,
            'max_hp': 20,
            'proficiency_bonus': 2,
        },
        {},
    )
    player.inventory = safe_json_dumps(
        [
            {
                'id': 'longbow-1',
                'name': 'Longbow',
                'type': 'weapon',
                'subtype': 'longbow',
                'equipped': True,
                'slot': 'two_hands',
            },
        ],
        [],
    )
    player.weapon_proficiencies = safe_json_dumps(['weapon:longbow'], [])
    attack_context = server_attack_roll_context(player, 'I fire my longbow.')

    roll = resolve_authoritative_player_roll(
        player=player,
        rule_type='attack',
        dc_hint='DC 14',
        action_intent={'kind': 'roll', 'ability': {'key': 'strength'}, 'roll': {'die': 'd20'}},
        attack_context=attack_context,
        roller=lambda _sides: 10,
    )

    assert attack_context['source'] == 'persisted_inventory'
    assert attack_context['proficiency_source'] == 'player_weapon_proficiencies'
    assert roll['ability']['key'] == 'dexterity'
    assert roll['proficiency'] == {'bonus': 2, 'skills': ['weapon:longbow'], 'multiplier': 1}
    assert roll['modifier'] == 5
    assert roll['total'] == 15


def test_attack_roll_uses_best_finesse_ability_without_inventing_proficiency():
    player = _player()
    player.inventory = safe_json_dumps(
        [
            {
                'id': 'rapier-1',
                'name': 'Rapier',
                'type': 'weapon',
                'subtype': 'rapier',
                'equipped': True,
                'slot': 'main_hand',
            },
        ],
        [],
    )
    attack_context = server_attack_roll_context(player, 'I attack with my rapier.')

    roll = resolve_authoritative_player_roll(
        player=player,
        rule_type='attack',
        dc_hint='DC 14',
        action_intent={'kind': 'roll', 'ability': {'key': 'strength'}, 'roll': {'die': 'd20'}},
        attack_context=attack_context,
        roller=lambda _sides: 10,
    )

    assert attack_context['weapon']['classification'] == 'finesse'
    assert roll['ability']['key'] == 'dexterity'
    assert roll['proficiency'] == {'bonus': 0, 'skills': [], 'multiplier': 0}
    assert roll['modifier'] == 2


def test_attack_roll_ignores_unowned_ranged_weapon_claim():
    player = _player()
    player.inventory = safe_json_dumps(
        [
            {
                'id': 'longsword-1',
                'name': 'Longsword',
                'type': 'weapon',
                'subtype': 'longsword',
                'equipped': True,
                'slot': 'main_hand',
            },
        ],
        [],
    )
    attack_context = server_attack_roll_context(player, 'I shoot the dragon with a longbow.')

    assert attack_context['weapon']['name'] == 'Longsword'
    assert attack_context['weapon']['classification'] == 'melee'
    assert attack_context['ability_key'] == 'strength'


def test_legacy_pending_attack_without_attack_spec_uses_server_inventory_context():
    player = _player()
    player.inventory = safe_json_dumps(
        [
            {
                'id': 'longbow-legacy',
                'name': 'Longbow',
                'type': 'weapon',
                'subtype': 'longbow',
                'equipped': True,
                'slot': 'two_hands',
            },
        ],
        [],
    )
    pending = DmTurn(
        turn_id=99,
        player_input='I shoot with my longbow.',
        rules_hint=safe_json_dumps({'roll_spec': {'die': 'd20', 'mode': 'normal'}}, {}),
        metadata_json='{}',
    )
    attack_context = server_attack_roll_context(player, pending.player_input)

    roll = resolve_authoritative_player_roll(
        player=player,
        rule_type='attack',
        dc_hint='DC 14',
        action_intent={
            'kind': 'roll',
            'ability': {'key': 'strength'},
            'roll': {'die': 'd100', 'mode': 'advantage'},
        },
        pending_turn=pending,
        attack_context=attack_context,
        roller=lambda _sides: 10,
    )

    assert roll['die'] == 'd20'
    assert roll['mode'] == 'normal'
    assert roll['ability']['key'] == 'dexterity'
    assert roll['attack']['weapon']['name'] == 'Longbow'


def test_named_skill_does_not_borrow_a_different_skill_proficiency():
    player = _player()
    player.stats = safe_json_dumps(
        {
            'ability_scores': {
                'strength': 10,
                'dexterity': 10,
                'constitution': 10,
                'intelligence': 10,
                'wisdom': 10,
                'charisma': 14,
            },
            'current_hp': 20,
            'max_hp': 20,
            'skill_proficiencies': ['Deception'],
            'proficiency_bonus': 2,
        },
        {},
    )

    roll = resolve_authoritative_player_roll(
        player=player,
        rule_type='persuasion',
        dc_hint='DC 15',
        action_intent=None,
        roller=lambda _sides: 10,
    )

    assert roll['ability']['key'] == 'charisma'
    assert roll['proficiency'] == {'bonus': 0, 'skills': [], 'multiplier': 0}
    assert roll['modifier'] == 2


def test_class_saving_throw_proficiency_uses_requested_ability():
    player = _player()
    player.class_ = 'Cleric - Life Domain'
    player.stats = safe_json_dumps(
        {
            'ability_scores': {
                'strength': 8,
                'dexterity': 10,
                'constitution': 12,
                'intelligence': 10,
                'wisdom': 16,
                'charisma': 12,
            },
            'current_hp': 20,
            'max_hp': 20,
            'proficiency_bonus': 2,
        },
        {},
    )

    roll = resolve_authoritative_player_roll(
        player=player,
        rule_type='wisdom_saving_throw',
        dc_hint='DC 14',
        action_intent=None,
        roller=lambda _sides: 10,
    )

    assert roll['ability']['key'] == 'wisdom'
    assert roll['proficiency'] == {'bonus': 2, 'skills': ['save:wisdom'], 'multiplier': 1}
    assert roll['modifier'] == 5
    assert roll['total'] == 15


def test_skill_expertise_doubles_persisted_proficiency_bonus():
    player = _player()
    player.stats = safe_json_dumps(
        {
            'ability_scores': {
                'strength': 10,
                'dexterity': 14,
                'constitution': 10,
                'intelligence': 10,
                'wisdom': 10,
                'charisma': 14,
            },
            'current_hp': 20,
            'max_hp': 20,
            'skill_proficiencies': ['Persuasion'],
            'skill_expertise': ['Persuasion'],
            'proficiency_bonus': 2,
        },
        {},
    )

    roll = resolve_authoritative_player_roll(
        player=player,
        rule_type='persuasion',
        dc_hint='DC 15',
        action_intent=None,
        roller=lambda _sides: 10,
    )

    assert roll['proficiency'] == {'bonus': 4, 'skills': ['persuasion'], 'multiplier': 2}
    assert roll['modifier_breakdown']['proficiency_multiplier'] == 2
    assert roll['modifier'] == 6


def test_curated_race_skill_proficiency_participates_in_rolls():
    player = _player()
    player.race = 'Tabaxi'
    player.stats = safe_json_dumps(
        {
            'ability_scores': {
                'strength': 10,
                'dexterity': 14,
                'constitution': 10,
                'intelligence': 10,
                'wisdom': 12,
                'charisma': 10,
            },
            'current_hp': 20,
            'max_hp': 20,
            'proficiency_bonus': 2,
        },
        {},
    )

    roll = resolve_authoritative_player_roll(
        player=player,
        rule_type='stealth',
        dc_hint='DC 14',
        action_intent=None,
        roller=lambda _sides: 10,
    )

    assert roll['proficiency'] == {'bonus': 2, 'skills': ['stealth'], 'multiplier': 1}
    assert roll['modifier'] == 4


def test_spell_roll_uses_class_spellcasting_ability_and_proficiency():
    player = _player()
    player.class_ = 'Wizard - Diviner'
    player.stats = safe_json_dumps(
        {
            'ability_scores': {
                'strength': 8,
                'dexterity': 12,
                'constitution': 12,
                'intelligence': 16,
                'wisdom': 10,
                'charisma': 8,
            },
            'current_hp': 20,
            'max_hp': 20,
            'proficiency_bonus': 2,
        },
        {},
    )

    roll = resolve_authoritative_player_roll(
        player=player,
        rule_type='spell',
        dc_hint='DC 14',
        action_intent={
            'kind': 'roll',
            'ability': {'key': 'charisma'},
            'roll': {'die': 'd20'},
        },
        roller=lambda _sides: 10,
    )

    assert roll['ability']['key'] == 'intelligence'
    assert roll['proficiency'] == {
        'bonus': 2,
        'skills': ['spellcasting:wizard'],
        'multiplier': 1,
    }
    assert roll['modifier'] == 5


def test_catalog_caster_archetypes_use_their_rules_ability_and_proficiency():
    for class_name, expected_archetype, expected_ability in (
        ('Oracle - Battle Seer', 'cleric', 'wisdom'),
        ('Witch - Grave Witch', 'wizard', 'intelligence'),
        ('Technomancer - Signal Savant', 'artificer', 'intelligence'),
    ):
        player = _player()
        player.class_ = class_name
        player.stats = safe_json_dumps(
            {
                'ability_scores': {
                    'strength': 8,
                    'dexterity': 12,
                    'constitution': 12,
                    'intelligence': 16,
                    'wisdom': 16,
                    'charisma': 8,
                },
                'current_hp': 20,
                'max_hp': 20,
                'proficiency_bonus': 2,
            },
            {},
        )

        roll = resolve_authoritative_player_roll(
            player=player,
            rule_type='spell',
            dc_hint='DC 14',
            action_intent={'kind': 'roll', 'ability': {'key': 'charisma'}},
            roller=lambda _sides: 10,
        )

        assert roll['ability']['key'] == expected_ability
        assert roll['proficiency'] == {
            'bonus': 2,
            'skills': [f'spellcasting:{expected_archetype}'],
            'multiplier': 1,
        }


def test_catalog_caster_archetype_inherits_saving_throw_proficiencies():
    player = _player()
    player.class_ = 'Oracle - Battle Seer'
    player.stats = safe_json_dumps(
        {
            'ability_scores': {'wisdom': 16},
            'current_hp': 20,
            'max_hp': 20,
            'proficiency_bonus': 2,
        },
        {},
    )

    roll = resolve_authoritative_player_roll(
        player=player,
        rule_type='wisdom_saving_throw',
        dc_hint='DC 14',
        action_intent=None,
        roller=lambda _sides: 10,
    )

    assert roll['proficiency'] == {'bonus': 2, 'skills': ['save:wisdom'], 'multiplier': 1}
