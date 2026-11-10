from __future__ import annotations

from aidm_server.spellbook import (
    ensure_character_sheet_spellbook,
    merge_spellbooks,
    normalize_spell,
    original_spell_catalog,
    original_spell_catalog_size,
    spell_from_change,
    spell_payload,
    spellbook_for_character,
)


def test_spell_metadata_distinguishes_rules_backed_and_narrated_spells():
    fireball = spell_payload('Fireball', level=1)
    assert fireball['level'] == 3
    assert fireball['rulesBacked'] is fireball['authoritativeEffect'] is True
    assert '8d6 fire' in fireball['effectSummary']
    assert 'area: up to 6 enemies' in fireball['effectSummary']
    assert fireball['castingTime'] == 'action'
    assert '3 darts of 1d4+1 force' in spell_payload('Magic Missile', level=1)['effectSummary']
    narrated = normalize_spell({'name': 'An Invented Spell', 'level': 1, 'authoritativeEffect': True})
    assert narrated['rulesBacked'] is narrated['authoritativeEffect'] is False
    assert narrated['narrated'] is True


def test_original_spell_catalog_is_broad_and_noncanonical():
    catalog = original_spell_catalog()
    names = {spell['name'] for spell in catalog}

    assert original_spell_catalog_size() >= 3000
    assert {'Amber Glimmer', 'Void Apocalypse', 'Mycelium Parliament'} <= names
    assert all(spell.get('catalog') == 'aidm-original' for spell in catalog[:50])


def test_magical_classes_receive_original_catalog_spells():
    spellbook = spellbook_for_character(class_name='Wizard - Chronomancer', race_name='Human', level=5)
    spells = spellbook['knownSpells']
    original_spells = [spell for spell in spells if spell.get('catalog') == 'aidm-original']
    names = {spell['name'] for spell in spells}

    assert len(original_spells) == 13  # Three starters plus the existing level 2, 3 and 5 unlocks.
    assert all(spell['narrated'] is True and spell['rulesBacked'] is False for spell in original_spells)
    assert 'Magic Missile' in names
    assert any(name not in {'Magic Missile', 'Shield', 'Fireball', 'Counterspell'} for name in names)


def test_original_starter_sample_is_three_without_removing_existing_spells():
    starter = spellbook_for_character(class_name='Wizard', race_name='Human', level=1)
    original = [spell for spell in starter['knownSpells'] if spell.get('catalog') == 'aidm-original']
    assert len(original) == 3
    old_extra = next(spell for spell in original_spell_catalog() if spell['name'] not in {item['name'] for item in original})
    existing = {'spellbook': {'knownSpells': [*starter['knownSpells'], old_extra]}}
    sheet, _ = ensure_character_sheet_spellbook(existing, class_name='Wizard', race_name='Human', level=1)
    assert old_extra['name'] in {spell['name'] for spell in sheet['spellbook']['knownSpells']}


def test_custom_invented_spell_names_are_valid_story_magic():
    spell = spell_from_change(
        {
            'type': 'spell.learn',
            'spellName': 'Velvet Thunder Argument',
            'spellLevel': 4,
            'learnedFrom': 'A talking storm taught the phrase.',
        }
    )

    assert spell is not None
    assert spell['name'] == 'Velvet Thunder Argument'
    assert spell['level'] == 4
    assert spell['sourceType'] == 'story'


def test_saiyan_race_gets_ki_abilities_not_shapeshifter_spells():
    race_selection = {
        'raceName': 'Saiyan',
        'customRaceDefinition': {
            'id': 'custom_saiyan_canon_v4pro',
            'name': 'Saiyan',
            'aliases': ['Saiya-jin', 'Warrior Race'],
            'tags': ['beastlike', 'exotic', 'martial', 'monstrous', 'durable'],
            'traits': [
                {
                    'id': 'great_ape_transformation',
                    'name': 'Great Ape Transformation',
                    'description': 'Transform into a gigantic Great Ape under a full moon or Blutz Waves.',
                },
                {
                    'id': 'ki_aptitude',
                    'name': 'Ki Aptitude',
                    'description': 'Project life energy as blasts and sense the power of others.',
                },
            ],
        },
    }

    spellbook = spellbook_for_character(class_name='Fighter - Battle Master', race_name='Saiyan', race_selection=race_selection, level=1)
    spells = spellbook['knownSpells']
    names = {spell['name'] for spell in spells}

    assert {'Ki Blast', 'Ki Sense', 'Battle Aura', 'Zenkai Surge', 'Great Ape Transformation'} <= names
    assert 'Disguise Self' not in names
    assert 'Alter Self' not in names
    assert all(spell.get('description') for spell in spells)
    assert any(spell.get('sourceDetail') == 'saiyan' for spell in spells)


def test_saiyan_repair_removes_stale_auto_shapeshifter_spells():
    race_selection = {
        'raceName': 'Saiyan',
        'customRaceDefinition': {
            'id': 'custom_saiyan_canon_v4pro',
            'name': 'Saiyan',
            'traits': [
                {'id': 'great_ape_transformation', 'name': 'Great Ape Transformation', 'description': 'Full moon transformation.'},
            ],
        },
    }
    sheet = {
        'spellbook': {
            'knownSpells': [
                {
                    'name': 'Disguise Self',
                    'level': 1,
                    'sourceType': 'race',
                    'sourceDetail': 'shapeshifter',
                    'source': 'race:shapeshifter',
                    'sources': ['race:shapeshifter'],
                    'description': 'Old incorrect auto spell.',
                },
                {
                    'name': 'Story Mask',
                    'level': 1,
                    'sourceType': 'story',
                    'sourceDetail': 'teacher',
                    'description': 'A story-learned disguise trick.',
                },
            ],
        },
    }

    repaired, changed = ensure_character_sheet_spellbook(
        sheet,
        class_name='Fighter',
        race_name='Saiyan',
        race_selection=race_selection,
        level=1,
    )
    names = {spell['name'] for spell in repaired['spellbook']['knownSpells']}

    assert changed is True
    assert 'Disguise Self' not in names
    assert 'Story Mask' in names
    assert 'Ki Blast' in names


def test_respec_removes_only_automatic_spell_provenance_and_is_idempotent():
    automatic = spellbook_for_character(class_name='Wizard', race_name='Tiefling', level=3)
    story_fire_bolt = spell_from_change(
        {
            'spellName': 'Fire Bolt',
            'spellLevel': 0,
            'sourceDetail': 'ember_tutor',
            'learnedFrom': 'The ember tutor',
        }
    )
    mixed = merge_spellbooks(
        automatic,
        {
            'knownSpells': [
                story_fire_bolt,
                {'name': 'Old Nameless Rune', 'level': 1},
            ]
        },
    )

    reconciled, changed = ensure_character_sheet_spellbook(
        {'spellbook': mixed},
        class_name='Fighter',
        race_name='Human',
        level=3,
        replace_class_grants=True,
        replace_race_grants=True,
        reset_preparation_policy=True,
    )
    assert changed is True
    spells = {spell['name']: spell for spell in reconciled['spellbook']['knownSpells']}
    assert set(spells) == {'Fire Bolt', 'Old Nameless Rune'}
    assert spells['Fire Bolt']['sources'] == ['story:ember_tutor']
    assert spells['Fire Bolt']['sourceType'] == 'story'
    assert spells['Old Nameless Rune'].get('sources', []) == []
    assert reconciled['spellbook']['sources'] == ['story:ember_tutor']
    assert set(reconciled['spellbook']['preparedSpells']) <= set(spells)

    repeated, changed_again = ensure_character_sheet_spellbook(
        reconciled,
        class_name='Fighter',
        race_name='Human',
        level=3,
        replace_class_grants=True,
        replace_race_grants=True,
        reset_preparation_policy=True,
    )
    assert changed_again is False
    assert repeated == reconciled
