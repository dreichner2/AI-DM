from __future__ import annotations

from aidm_server.spellbook import (
    ensure_character_sheet_spellbook,
    merge_spellbooks,
    original_spell_catalog,
    original_spell_catalog_size,
    spell_from_change,
    spellbook_for_character,
)


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

    assert len(spells) >= 30
    assert len(original_spells) >= 20
    assert 'Magic Missile' in names
    assert any(name not in {'Magic Missile', 'Shield', 'Fireball', 'Counterspell'} for name in names)


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
