from __future__ import annotations

from copy import deepcopy

import pytest

from aidm_server.character_resources import (
    clear_concentration,
    consume_spell_cast,
    derive_spell_slot_maxima,
    ensure_character_sheet_spell_resources,
    normalize_spell_resources,
    restore_spell_resources,
    set_concentration,
    spell_cast_legality,
)
from aidm_server.spellbook import (
    merge_spellbooks,
    normalize_spellbook,
    spell_is_prepared,
    spell_preparation_policy_for_class,
)


def _spell(name: str, level: int, **extra: object) -> dict[str, object]:
    return {
        'id': f'spell_{name.lower().replace(" ", "_")}',
        'name': name,
        'level': level,
        **extra,
    }


def _spellbook(
    class_name: str,
    *spells: dict[str, object],
    prepared: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {'knownSpells': list(spells)}
    if prepared is not None:
        payload['preparedSpells'] = prepared
    return normalize_spellbook(payload, class_name=class_name)


def test_preparation_policy_is_class_authoritative_and_legacy_safe():
    wizard_policy = spell_preparation_policy_for_class('Wizard - Chronomancer')
    sorcerer_policy = spell_preparation_policy_for_class('Sorcerer')

    assert wizard_policy['mode'] == 'prepared'
    assert wizard_policy['requiresPreparation'] is True
    assert sorcerer_policy['mode'] == 'known'
    assert sorcerer_policy['requiresPreparation'] is False

    legacy_wizard = normalize_spellbook(
        {
            'knownSpells': [
                _spell('Fire Bolt', 0),
                _spell('Magic Missile', 1),
                _spell('Fey Step', 1, sourceType='race'),
            ],
        },
        class_name='Wizard',
    )
    assert legacy_wizard['preparationPolicy'] == {
        'schemaVersion': 1,
        'mode': 'prepared',
        'requiresPreparation': True,
        'classArchetype': 'wizard',
        'source': 'class',
        'legacyDefaultApplied': True,
    }
    assert legacy_wizard['preparedSpells'] == ['Magic Missile']
    assert spell_is_prepared(legacy_wizard, 'Fire Bolt') is True
    assert spell_is_prepared(legacy_wizard, 'Fey Step') is True
    assert spell_is_prepared(legacy_wizard, 'Magic Missile') is True

    explicit_selection = normalize_spellbook(
        {
            'knownSpells': [_spell('Magic Missile', 1), _spell('Shield', 1)],
            'preparedSpells': ['spell_magic_missile', 'not-a-known-spell'],
            # Persisted state cannot opt a Wizard out of preparation rules.
            'preparationPolicy': {'mode': 'known'},
        },
        class_name='Wizard',
    )
    assert explicit_selection['preparationPolicy']['mode'] == 'prepared'
    assert explicit_selection['preparedSpells'] == ['Magic Missile']
    assert spell_is_prepared(explicit_selection, 'Magic Missile') is True
    assert spell_is_prepared(explicit_selection, 'Shield') is False

    merged = merge_spellbooks(
        explicit_selection,
        normalize_spellbook(
            [_spell('Magic Missile', 1), _spell('Shield', 1), _spell('Web', 2)],
            class_name='Wizard',
        ),
    )
    assert {spell['name'] for spell in merged['knownSpells']} == {'Magic Missile', 'Shield', 'Web'}
    assert merged['preparedSpells'] == ['Magic Missile']


@pytest.mark.parametrize(
    ('class_name', 'level', 'expected'),
    [
        ('Wizard', 5, {'1': 4, '2': 3, '3': 2}),
        ('Paladin', 1, {}),
        ('Paladin', 5, {'1': 4, '2': 2}),
        ('Ranger', 5, {'1': 4, '2': 2}),
        ('Artificer', 1, {'1': 2}),
    ],
)
def test_single_class_slot_maxima_match_class_progression(class_name, level, expected):
    maxima = derive_spell_slot_maxima(class_name, level)

    assert maxima['standard'] == expected
    assert maxima['classLevelInference'] == 'single_class'


def test_pact_and_multiclass_slot_maxima_are_separate_and_conservative():
    warlock = derive_spell_slot_maxima('Warlock', 5)
    assert warlock['standard'] == {}
    assert warlock['pact'] == {'slotLevel': 3, 'max': 2}
    assert warlock['castingMode'] == 'pact'

    multiclass = derive_spell_slot_maxima(
        'Wizard / Paladin / Warlock',
        7,
        class_levels={'Wizard': 3, 'Paladin': 2, 'Warlock': 2},
    )
    assert multiclass['effectiveCasterLevel'] == 4
    assert multiclass['standard'] == {'1': 4, '2': 3}
    assert multiclass['pact'] == {'slotLevel': 1, 'max': 2}
    assert multiclass['castingMode'] == 'hybrid'

    parsed = derive_spell_slot_maxima('Wizard 3 / Paladin 2', 5)
    assert parsed['classLevelInference'] == 'parsed_multiclass'
    assert parsed['standard'] == {'1': 4, '2': 3}

    ambiguous = derive_spell_slot_maxima('Wizard / Fighter', 10)
    assert ambiguous['classLevelInference'] == 'conservative_multiclass'
    assert ambiguous['classLevels'] == {'wizard': 1}
    assert ambiguous['standard'] == {'1': 2}

    malformed_split = derive_spell_slot_maxima(
        'Wizard / Warlock',
        3,
        class_levels={'Wizard': 20, 'Warlock': 20},
    )
    assert malformed_split['classLevelInference'] == 'explicit_capped'
    assert sum(malformed_split['classLevels'].values()) == 3
    assert malformed_split['effectiveCasterLevel'] <= 3


@pytest.mark.parametrize(
    'class_name',
    [
        'Wizard 3 / Paladin 2',
        'Wizard 3 + Paladin 2',
        'Wizard 3 & Paladin 2',
        'Wizard 3, Paladin 2',
        'Wizard 3\tAND\nPaladin 2',
    ],
)
def test_multiclass_parser_accepts_supported_separators_without_ambiguous_whitespace(class_name):
    parsed = derive_spell_slot_maxima(class_name, 5)

    assert parsed['classLevelInference'] == 'parsed_multiclass'
    assert parsed['classLevels'] == {'paladin': 2, 'wizard': 3}
    assert parsed['standard'] == {'1': 4, '2': 3}


def test_multiclass_parser_only_splits_and_as_a_standalone_word():
    parsed = derive_spell_slot_maxima('Land Druid', 5)

    assert parsed['classLevelInference'] == 'single_class'
    assert parsed['classLevels'] == {'druid': 5}


def test_resource_normalization_caps_current_values_and_restores_persisted_multiclass_split():
    normalized = normalize_spell_resources(
        {
            'slots': {
                '1': {'current': 99, 'max': 99},
                '2': {'current': -3, 'max': 99},
                '9': {'current': 8, 'max': 8},
            },
            'pactSlots': {'current': 99, 'max': 99, 'slotLevel': 9},
        },
        class_name='Wizard',
        level=5,
    )
    assert normalized['slots'] == {
        '1': {'current': 4, 'max': 4},
        '2': {'current': 0, 'max': 3},
        '3': {'current': 2, 'max': 2},
    }
    assert normalized['pactSlots'] == {'current': 0, 'max': 0, 'slotLevel': 0}

    exact = normalize_spell_resources(
        None,
        class_name='Wizard / Paladin',
        level=5,
        class_levels={'Wizard': 3, 'Paladin': 2},
    )
    reloaded = normalize_spell_resources(
        exact,
        class_name='Wizard / Paladin',
        level=5,
    )
    assert reloaded['classLevels'] == {'wizard': 3, 'paladin': 2}
    assert reloaded['effectiveCasterLevel'] == 4
    assert reloaded['slots'] == exact['slots']


def test_cast_legality_requires_known_and_prepared_spells_but_cantrips_are_free():
    spellbook = _spellbook(
        'Wizard',
        _spell('Fire Bolt', 0),
        _spell('Magic Missile', 1),
        _spell('Shield', 1),
        prepared=['Magic Missile'],
    )
    resources = normalize_spell_resources(None, class_name='Wizard', level=1)
    before_slots = deepcopy(resources['slots'])

    cantrip = consume_spell_cast(
        spellbook,
        resources,
        spell_name_or_id='Fire Bolt',
        class_name='Wizard',
        level=1,
    )
    assert cantrip['ok'] is True
    assert cantrip['consumed'] is None
    assert cantrip['resources']['slots'] == before_slots

    unknown = spell_cast_legality(
        spellbook,
        resources,
        spell_name_or_id='Fireball',
        class_name='Wizard',
        level=1,
    )
    assert unknown['legal'] is False
    assert unknown['errorCode'] == 'spell_not_known'

    unprepared = spell_cast_legality(
        spellbook,
        resources,
        spell_name_or_id='Shield',
        class_name='Wizard',
        level=1,
    )
    assert unprepared['legal'] is False
    assert unprepared['errorCode'] == 'spell_not_prepared'

    sorcerer_book = _spellbook('Sorcerer', _spell('Chaos Bolt', 1), prepared=[])
    known_caster = spell_cast_legality(
        sorcerer_book,
        None,
        spell_name_or_id='Chaos Bolt',
        class_name='Sorcerer',
        level=1,
    )
    assert known_caster['legal'] is True


def test_slot_consumption_is_deterministic_and_exhaustion_does_not_mutate_state():
    spellbook = _spellbook('Wizard', _spell('Magic Missile', 1), prepared=['Magic Missile'])
    resources = normalize_spell_resources(None, class_name='Wizard', level=3)
    resources['slots']['1']['current'] = 0
    resources['slots']['2']['current'] = 1

    exact_level = spell_cast_legality(
        spellbook,
        resources,
        spell_name_or_id='Magic Missile',
        class_name='Wizard',
        level=3,
        cast_level=1,
    )
    assert exact_level['legal'] is False
    assert exact_level['errorCode'] == 'spell_slot_exhausted'

    upcast = consume_spell_cast(
        spellbook,
        resources,
        spell_name_or_id='Magic Missile',
        class_name='Wizard',
        level=3,
    )
    assert upcast['ok'] is True
    assert upcast['consumed'] == {'pool': 'standard', 'slotLevel': 2}
    assert upcast['resources']['slots']['2']['current'] == 0

    exhausted_snapshot = deepcopy(upcast['resources'])
    exhausted = consume_spell_cast(
        spellbook,
        exhausted_snapshot,
        spell_name_or_id='Magic Missile',
        class_name='Wizard',
        level=3,
    )
    assert exhausted['ok'] is False
    assert exhausted['legality']['errorCode'] == 'spell_slot_exhausted'
    assert exhausted['resources'] == exhausted_snapshot


def test_pact_slots_restore_on_short_rest_while_standard_slots_do_not():
    spellbook = _spellbook('Warlock', _spell('Hex', 1), prepared=[])
    resources = normalize_spell_resources(None, class_name='Warlock', level=5)

    first = consume_spell_cast(
        spellbook,
        resources,
        spell_name_or_id='Hex',
        class_name='Warlock',
        level=5,
    )
    second = consume_spell_cast(
        spellbook,
        first['resources'],
        spell_name_or_id='Hex',
        class_name='Warlock',
        level=5,
    )
    assert second['resources']['pactSlots']['current'] == 0
    assert consume_spell_cast(
        spellbook,
        second['resources'],
        spell_name_or_id='Hex',
        class_name='Warlock',
        level=5,
    )['ok'] is False

    short_rested = restore_spell_resources(
        second['resources'],
        rest_type='short_rest',
        class_name='Warlock',
        level=5,
    )
    assert short_rested['pactSlots']['current'] == 2

    hybrid = normalize_spell_resources(
        None,
        class_name='Wizard / Warlock',
        level=5,
        class_levels={'Wizard': 3, 'Warlock': 2},
    )
    hybrid['slots']['1']['current'] = 0
    hybrid['pactSlots']['current'] = 0
    hybrid_short_rest = restore_spell_resources(
        hybrid,
        rest_type='short',
        class_name='Wizard / Warlock',
        level=5,
        class_levels={'Wizard': 3, 'Warlock': 2},
    )
    assert hybrid_short_rest['slots']['1']['current'] == 0
    assert hybrid_short_rest['pactSlots']['current'] == 2


def test_mystic_arcanum_only_restores_on_long_rest():
    spellbook = _spellbook('Warlock', _spell('Otherworldly Gate', 6), prepared=[])
    resources = normalize_spell_resources(None, class_name='Warlock', level=11)

    cast = consume_spell_cast(
        spellbook,
        resources,
        spell_name_or_id='Otherworldly Gate',
        class_name='Warlock',
        level=11,
    )
    assert cast['ok'] is True
    assert cast['consumed'] == {'pool': 'arcanum', 'slotLevel': 6}
    assert cast['resources']['mysticArcanum']['6']['current'] == 0

    short_rested = restore_spell_resources(
        cast['resources'],
        rest_type='short_rest',
        class_name='Warlock',
        level=11,
    )
    assert short_rested['mysticArcanum']['6']['current'] == 0

    long_rested = restore_spell_resources(
        short_rested,
        rest_type='long_rest',
        class_name='Warlock',
        level=11,
    )
    assert long_rested['mysticArcanum']['6']['current'] == 1


def test_concentration_helpers_replace_and_conditionally_clear_state():
    resources = normalize_spell_resources(None, class_name='Wizard', level=3)
    concentrating, previous = set_concentration(
        resources,
        spell=_spell('Web', 2),
        class_name='Wizard',
        level=3,
        caster_actor_id='actor-1',
        target_ids=['enemy-1', 'enemy-1', 'enemy-2'],
        started_at_turn=4,
    )
    assert previous is None
    assert concentrating['concentration'] == {
        'active': True,
        'spellId': 'spell_web',
        'spellName': 'Web',
        'casterActorId': 'actor-1',
        'targetIds': ['enemy-1', 'enemy-2'],
        'startedAtTurn': 4,
    }

    replaced, previous = set_concentration(
        concentrating,
        spell=_spell('Hold Person', 2),
        class_name='Wizard',
        level=3,
    )
    assert previous['spellId'] == 'spell_web'
    assert replaced['concentration']['spellId'] == 'spell_hold_person'

    unchanged, cleared = clear_concentration(
        replaced,
        class_name='Wizard',
        level=3,
        expected_spell_id='spell_web',
    )
    assert cleared is None
    assert unchanged['concentration']['spellId'] == 'spell_hold_person'

    cleared_resources, cleared = clear_concentration(
        unchanged,
        class_name='Wizard',
        level=3,
        expected_spell_id='spell_hold_person',
    )
    assert cleared['spellId'] == 'spell_hold_person'
    assert cleared_resources['concentration'] is None


def test_long_rest_restores_standard_resources_and_ends_concentration():
    resources = normalize_spell_resources(None, class_name='Wizard', level=5)
    resources['slots']['1']['current'] = 0
    resources['slots']['2']['current'] = 1
    resources, _ = set_concentration(
        resources,
        spell=_spell('Web', 2),
        class_name='Wizard',
        level=5,
    )

    short_rested = restore_spell_resources(
        resources,
        rest_type='short_rest',
        class_name='Wizard',
        level=5,
    )
    assert short_rested['slots']['1']['current'] == 0
    assert short_rested['slots']['2']['current'] == 1
    assert short_rested['concentration']['spellId'] == 'spell_web'

    long_rested = restore_spell_resources(
        short_rested,
        rest_type='long_rest',
        class_name='Wizard',
        level=5,
    )
    assert all(slot['current'] == slot['max'] for slot in long_rested['slots'].values())
    assert long_rested['concentration'] is None


def test_character_sheet_migration_persists_policy_and_capped_resources_idempotently():
    legacy_sheet = {
        'knownSpells': [_spell('Magic Missile', 1)],
        'spellSlots': {'1': {'current': 99}},
    }
    migrated, changed = ensure_character_sheet_spell_resources(
        legacy_sheet,
        class_name='Wizard',
        level=1,
    )

    assert changed is True
    assert 'spellSlots' not in migrated
    assert migrated['spells'] == ['Magic Missile']
    assert migrated['spellbook']['preparedSpells'] == ['Magic Missile']
    assert migrated['spellbook']['preparationPolicy']['mode'] == 'prepared'
    assert migrated['spellResources']['slots']['1'] == {'current': 2, 'max': 2}

    reloaded, changed_again = ensure_character_sheet_spell_resources(
        migrated,
        class_name='Wizard',
        level=1,
    )
    assert changed_again is False
    assert reloaded == migrated


def test_invalid_rest_type_fails_closed():
    with pytest.raises(ValueError, match='short_rest or long_rest'):
        restore_spell_resources(
            None,
            rest_type='eight-hour-nap-ish',
            class_name='Wizard',
            level=1,
        )
