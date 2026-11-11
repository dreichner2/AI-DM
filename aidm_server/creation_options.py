"""The rules a level-1 hero is made under, read by the character creator.

Built from the tables the creation validator and the rules engine use (class
skills and saves, fighting styles, expertise, starter kits, level-1 spells,
class capabilities and backgrounds), so the client never mirrors them.
"""

from __future__ import annotations

from typing import Any

from aidm_server.character_backgrounds import background_catalog
from aidm_server.character_progression import hit_die_for_class
from aidm_server.character_state import CLASS_SAVING_THROW_PROFICIENCIES, CLASS_SKILL_CHOICES, EXPERTISE_CHOICE_COUNT
from aidm_server.class_features import (
    CLASS_CAPABILITY_CATALOG,
    EXTRA_ATTACK_CLASSES,
    FIGHTING_STYLE_LEVEL,
    FIGHTING_STYLES,
)
from aidm_server.spellbook import CLASS_LEVEL_UNLOCKS, catalog_unlock_level, class_spell_archetype, class_spells_for_level
from aidm_server.starting_inventory import starting_inventory_for_class


ABILITY_ORDER = ('strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma')
SPELL_LEVEL_ORDINALS = {1: '1st', 2: '2nd', 3: '3rd', 4: '4th', 5: '5th'}
# The creator shows what a hero gains through level 5.
HIGHEST_LEVEL_SHOWN = 5

# Rules-backed features the engine applies through providers, armour class and
# the creation validator rather than a capability entry: (level, name, summary).
PASSIVE_FEATURES: dict[str, tuple[tuple[int, str, str], ...]] = {
    'barbarian': (
        (1, 'Unarmored Defense', 'Without armour your AC is 10 + Dexterity + Constitution.'),
        (2, 'Danger Sense', 'Advantage on Dexterity saves against what you can see.'),
    ),
    'bard': ((2, 'Jack of All Trades', 'Half your proficiency bonus on checks you are untrained in.'),),
    'fighter': ((3, 'Improved Critical', 'As a Champion, you score critical hits on 19 and 20.'),),
    'monk': ((1, 'Unarmored Defense', 'Without armour or a shield your AC is 10 + Dexterity + Wisdom.'),),
    'ranger': ((3, 'Colossus Slayer', 'As a Hunter, once per turn +1d8 damage to a wounded foe.'),),
    'rogue': (
        (
            1,
            'Sneak Attack',
            'Once per turn, +1d6 damage with a finesse or ranged weapon when you have advantage or an ally is '
            'beside your target.',
        ),
        (1, 'Expertise', f'Double proficiency in {EXPERTISE_CHOICE_COUNT} skills you choose.'),
    ),
    'warlock': ((2, 'Agonizing Blast', 'Add your Charisma to every Eldritch Blast beam.'),),
}
FIGHTING_STYLE_FEATURE = ('Fighting Style', 'A trained way of fighting that the rules apply in every fight.')
EXTRA_ATTACK_FEATURE = (5, 'Extra Attack', 'Two attacks with every Attack action.')
SPELLCASTING_FEATURES = {'warlock': ('Pact Magic', 'A few spells, cast from slots that return on a short rest.')}
DEFAULT_SPELLCASTING_FEATURE = ('Spellcasting', 'Magic cast from spell slots that return on a long rest.')


def _class_features(archetype: str) -> list[tuple[int, str, str]]:
    rows = [
        (int(capability.get('minimumLevel') or 1), capability['name'], capability.get('description') or '')
        for capability in CLASS_CAPABILITY_CATALOG.get(archetype, ())
    ]
    if archetype in FIGHTING_STYLE_LEVEL:
        rows.append((FIGHTING_STYLE_LEVEL[archetype], *FIGHTING_STYLE_FEATURE))
    rows.extend(PASSIVE_FEATURES.get(archetype, ()))
    if archetype in EXTRA_ATTACK_CLASSES:
        rows.append(EXTRA_ATTACK_FEATURE)
    if class_spell_archetype(archetype):
        rows.append((catalog_unlock_level(archetype, 1), *SPELLCASTING_FEATURES.get(archetype, DEFAULT_SPELLCASTING_FEATURE)))
        for unlock_level, specs in sorted(CLASS_LEVEL_UNLOCKS.get(archetype, {}).items()):
            if unlock_level > HIGHEST_LEVEL_SHOWN:
                continue
            for spell_level in sorted({spec[1] for spec in specs if spec[1] >= 2}):
                names = [spec[0] for spec in specs if spec[1] == spell_level]
                rows.append((unlock_level, f'{SPELL_LEVEL_ORDINALS[spell_level]}-level spells', f"{', '.join(names)}."))
    rows = [row for row in rows if row[0] <= HIGHEST_LEVEL_SHOWN]
    rows.sort(key=lambda row: row[0])
    return rows


def _class_entry(archetype: str) -> dict[str, Any]:
    count, options = CLASS_SKILL_CHOICES[archetype]
    spells = class_spells_for_level(archetype, 1)
    features = _class_features(archetype)
    entry: dict[str, Any] = {
        'id': archetype,
        'name': archetype.capitalize(),
        'hitDie': hit_die_for_class(archetype),
        'savingThrows': [ability for ability in ABILITY_ORDER if ability in CLASS_SAVING_THROW_PROFICIENCIES[archetype]],
        'skillChoices': {'count': count, 'options': sorted(options)},
        'startingKit': [item['name'] for item in starting_inventory_for_class(archetype)],
        'cantrips': [spell['name'] for spell in spells if int(spell.get('level') or 0) == 0],
        'spells': [spell['name'] for spell in spells if int(spell.get('level') or 0) == 1],
        'levelOneFeatures': [{'name': name, 'summary': summary} for level, name, summary in features if level == 1],
        'laterFeatures': [{'level': level, 'name': name, 'summary': summary} for level, name, summary in features if level > 1],
    }
    if archetype in FIGHTING_STYLES:
        entry['fightingStyles'] = {'level': FIGHTING_STYLE_LEVEL[archetype], 'options': list(FIGHTING_STYLES[archetype])}
    if archetype == 'rogue':
        entry['expertise'] = {'count': EXPERTISE_CHOICE_COUNT}
    return entry


def creation_options_payload() -> dict[str, Any]:
    """The twelve core classes and the backgrounds with the skills each grants."""

    return {
        'classes': [_class_entry(archetype) for archetype in CLASS_SKILL_CHOICES],
        'backgrounds': [
            {'id': record['id'], 'name': record['name'], 'skills': list(record['skillProficiencies'])}
            for record in background_catalog()
        ],
    }
