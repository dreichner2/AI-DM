from __future__ import annotations

from urllib.parse import quote

PROFILE_ICON_FILES: dict[str, dict[str, str]] = {
    'aarakocra': {'male': '19_Aarakocra_male.png', 'female': '20_Aarakocra_female.png'},
    'aasimar': {'male': '01 - Aasimar (Male).png', 'female': '02 - Aasimar (Female).png'},
    'bugbear': {'male': '15 - Bugbear (Male).png', 'female': '16 - Bugbear (Female).png'},
    'changeling': {'male': '09 - Changeling (Male).png', 'female': '10 - Changeling (Female).png'},
    'dragonborn': {'male': '03 - Dragonborn (Male).png', 'female': '04 - Dragonborn (Female).png'},
    'dwarf': {'male': 'dwarf_male.png', 'female': 'dwarf_female.png'},
    'elf': {'male': 'elf_male.png', 'female': 'elf_female.png'},
    'fairy': {'male': '19 - Fairy (Male).png', 'female': '20 - Fairy (Female).png'},
    'firbolg': {'male': '03_Firbolg_male.png', 'female': '04_Firbolg_female.png'},
    'genasi': {'male': '07 - Genasi (Male).png', 'female': '08 - Genasi (Female).png'},
    'gnome': {'male': '05 - Gnome (Male).png', 'female': '06 - Gnome (Female).png'},
    'goblin': {'male': '07_Goblin_male.png', 'female': '08_Goblin_female.png'},
    'goliath': {'male': '05_Goliath_male.png', 'female': '06_Goliath_female.png'},
    'halfling': {'male': 'halfling_male.png', 'female': 'halfling_female.png'},
    'harengon': {'male': '21 - Harengon (Male).png', 'female': '22 - Harengon (Female).png'},
    'hobgoblin': {'male': '17 - Hobgoblin (Male).png', 'female': '18 - Hobgoblin (Female).png'},
    'human': {'male': 'human_male.png', 'female': 'human_female.png'},
    'kenku': {'male': '11_Kenku_male.png', 'female': '12_Kenku_female.png'},
    'kobold': {'male': '09_Kobold_male.png', 'female': '10_Kobold_female.png'},
    'lizardfolk': {'male': '13 - Lizardfolk (Male).png', 'female': '14 - Lizardfolk (Female).png'},
    'minotaur': {'male': '23 - Minotaur (Male).png', 'female': '24 - Minotaur (Female).png'},
    'orc': {'male': 'orc_male.png', 'female': 'orc_female.png'},
    'satyr': {'male': '17_Satyr_male.png', 'female': '18_Satyr_female.png'},
    'shifter': {'male': '11 - Shifter (Male).png', 'female': '12 - Shifter (Female).png'},
    'tabaxi': {'male': '01_Tabaxi_male.png', 'female': '02_Tabaxi_female.png'},
    'tiefling': {'male': 'tiefling_male.png', 'female': 'tiefling_female.png'},
    'tortle': {'male': '21_Tortle_male.png', 'female': '22_Tortle_female.png'},
    'triton': {'male': '13_Triton_male.png', 'female': '14_Triton_female.png'},
    'warforged': {'male': '23_Warforged_male.png', 'female': '24_Warforged_female.png'},
    'yuan-ti': {'male': '15_Yuan-ti_male.png', 'female': '16_Yuan-ti_female.png'},
}

RACE_ALIASES: dict[str, list[str]] = {
    'aarakocra': ['aarakocra', 'bird', 'birdfolk', 'bird person', 'eagle', 'hawk', 'avian'],
    'aasimar': ['aasimar', 'angel', 'angelic', 'celestial', 'divine', 'heavenborn'],
    'bugbear': ['bugbear', 'hairy goblin', 'large goblin'],
    'changeling': ['changeling', 'shapechanger', 'shapeshifter', 'doppelganger'],
    'dragonborn': ['dragonborn', 'dragon', 'dragon person', 'draconic', 'draconian'],
    'dwarf': ['dwarf', 'dwarven', 'mountain dwarf', 'hill dwarf'],
    'elf': ['elf', 'elven', 'drow', 'dark elf', 'wood elf', 'high elf', 'half elf', 'half-elf'],
    'fairy': ['fairy', 'fae', 'fey', 'pixie', 'sprite'],
    'firbolg': ['firbolg', 'forest giant', 'nature giant'],
    'genasi': ['genasi', 'elemental', 'fire genasi', 'water genasi', 'earth genasi', 'air genasi'],
    'gnome': ['gnome', 'gnomish', 'deep gnome', 'rock gnome', 'forest gnome'],
    'goblin': ['goblin', 'gobbo'],
    'goliath': ['goliath', 'giant', 'giantkin', 'stone giant', 'big strong'],
    'halfling': ['halfling', 'hobbit', 'small folk', 'little folk'],
    'harengon': ['harengon', 'rabbit', 'rabbitfolk', 'bunny', 'hare'],
    'hobgoblin': ['hobgoblin', 'hob goblin', 'military goblin'],
    'human': ['human', 'humanoid', 'mortal'],
    'kenku': ['kenku', 'crow', 'raven', 'corvid', 'flightless bird'],
    'kobold': ['kobold', 'little dragon', 'tiny dragon', 'small dragon'],
    'lizardfolk': ['lizardfolk', 'lizard', 'lizard person', 'reptile', 'reptilian'],
    'minotaur': ['minotaur', 'bull', 'bull man', 'cow person'],
    'orc': ['orc', 'orcish', 'half orc', 'half-orc', 'green skin', 'greenskin'],
    'satyr': ['satyr', 'faun', 'goat', 'goat person'],
    'shifter': ['shifter', 'werewolf', 'lycan', 'lycanthrope', 'beastfolk', 'beast folk'],
    'tabaxi': ['tabaxi', 'cat', 'catfolk', 'cat folk', 'cat person', 'feline'],
    'tiefling': [
        'tiefling',
        'demon',
        'half demon',
        'half-demon',
        'devil',
        'fiend',
        'infernal',
        'demonic',
        'hellborn',
        'hellspawn',
    ],
    'tortle': ['tortle', 'turtle', 'turtlefolk', 'turtle person', 'tortoise'],
    'triton': ['triton', 'merfolk', 'merman', 'mermaid', 'sea elf', 'ocean', 'sea person'],
    'warforged': ['warforged', 'robot', 'construct', 'machine', 'automaton', 'metal person'],
    'yuan-ti': ['yuan-ti', 'yuan ti', 'snake', 'serpent', 'snake person'],
}

FEMALE_ALIASES = {'female', 'f', 'woman', 'girl', 'she', 'her', 'lady'}
MALE_ALIASES = {'male', 'm', 'man', 'boy', 'he', 'him', 'guy'}


def normalize_text(value: str) -> str:
    text = value.lower().replace("'", '').replace('’', '')
    normalized = ''.join(character if character.isalnum() else ' ' for character in text)
    return ' '.join(normalized.split())


def compact_text(value: str) -> str:
    return normalize_text(value).replace(' ', '')


def edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    current = [0] * (len(right) + 1)
    for left_index, left_character in enumerate(left, start=1):
        current[0] = left_index
        for right_index, right_character in enumerate(right, start=1):
            cost = 0 if left_character == right_character else 1
            current[right_index] = min(
                current[right_index - 1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + cost,
            )
        previous, current = current, previous
    return previous[len(right)]


def score_alias(input_value: str, alias: str) -> int:
    input_text = normalize_text(input_value)
    alias_text = normalize_text(alias)
    input_compact = compact_text(input_text)
    alias_compact = compact_text(alias_text)
    if not input_compact or not alias_compact:
        return 0
    if input_compact == alias_compact:
        return 100
    if alias_compact in input_compact or input_compact in alias_compact:
        return min(94, 68 + min(len(input_compact), len(alias_compact)) * 2)

    input_tokens = set(input_text.split())
    alias_tokens = [token for token in alias_text.split() if token]
    overlap = sum(1 for token in alias_tokens if token in input_tokens)
    token_score = 58 + overlap * 12 if overlap else 0
    if len(input_compact) < 4 or len(alias_compact) < 4:
        return token_score

    longest = max(len(input_compact), len(alias_compact))
    typo_score = round(70 - (edit_distance(input_compact, alias_compact) / longest) * 45)
    return max(token_score, typo_score)


def profile_icon_race_for_character(race: str | None) -> str | None:
    input_value = (race or '').strip()
    if not input_value:
        return None

    best_race: str | None = None
    best_score = 0
    for race_key, aliases in RACE_ALIASES.items():
        for alias in aliases:
            score = score_alias(input_value, alias)
            if score > best_score:
                best_race = race_key
                best_score = score

    return best_race if best_race and best_score >= 58 else None


def sex_key_for_character(sex: str | None) -> str:
    normalized = compact_text(sex or '')
    if normalized in FEMALE_ALIASES:
        return 'female'
    if normalized in MALE_ALIASES:
        return 'male'
    return 'male'


def profile_icon_src_for_character(race: str | None, sex: str | None) -> str:
    icon_race = profile_icon_race_for_character(race) or 'human'
    icon_sex = sex_key_for_character(sex)
    filename = PROFILE_ICON_FILES[icon_race][icon_sex]
    return f'/profile-icons/{quote(filename)}'
