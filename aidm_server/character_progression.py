"""Class-derived durability and level progression helpers."""

from __future__ import annotations

import re
from typing import Any

from aidm_server.canon_text import int_or_default


DEFAULT_HIT_DIE = 8

# The core tabletop classes use their canonical hit dice. Extended AIDM
# classes receive a conservative role-equivalent die; unknown/custom classes
# retain the old d8 behavior for backward compatibility.
CLASS_HIT_DICE: dict[str, int] = {
    'barbarian': 12,
    'fighter': 10,
    'ranger': 10,
    'paladin': 10,
    'gunslinger': 10,
    'swashbuckler': 10,
    'cavalier': 10,
    'guardian': 10,
    'marshal': 10,
    'inquisitor': 10,
    'warpriest': 10,
    'warden': 10,
    'beastmaster': 10,
    'shapeshifter': 10,
    'blood hunter': 10,
    'rune knight': 10,
    'rogue': 8,
    'monk': 8,
    'bard': 8,
    'cleric': 8,
    'druid': 8,
    'warlock': 8,
    'artificer': 8,
    'oracle': 8,
    'shaman': 8,
    'witch': 8,
    'magus': 8,
    'summoner': 8,
    'psychic': 8,
    'psion': 8,
    'medium': 8,
    'occultist': 8,
    'mesmerist': 8,
    'alchemist': 8,
    'investigator': 8,
    'skald': 8,
    'shadowblade': 8,
    'dragon disciple': 8,
    'technomancer': 8,
    'engineer': 8,
    'medic': 8,
    'operative': 8,
    'pilot': 8,
    'public safety officer': 8,
    'medical professional': 8,
    'tradesperson': 8,
    'street operator': 8,
    'wizard': 6,
    'sorcerer': 6,
    'elementalist': 6,
    'necromancer': 6,
    'scholar': 6,
    'mystic theurge': 6,
    'business professional': 6,
    'entertainer': 6,
    'legal professional': 6,
    'media professional': 6,
    'educator': 6,
    'service worker': 6,
}


def bounded_character_level(value: Any) -> int:
    return max(1, min(20, int_or_default(value, default=1)))


def proficiency_bonus_for_level(level: Any) -> int:
    return 2 + (bounded_character_level(level) - 1) // 4


def _class_key(value: Any) -> str:
    text = str(value or '').split('-', 1)[0].strip().lower()
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def class_archetype(value: Any) -> str | None:
    key = _class_key(value)
    if not key:
        return None
    if key in CLASS_HIT_DICE:
        return key
    for candidate in sorted(CLASS_HIT_DICE, key=len, reverse=True):
        if key.startswith(f'{candidate} ') or candidate in key.split(' / '):
            return candidate
    return None


def hit_die_for_class(class_name: Any) -> int:
    return CLASS_HIT_DICE.get(class_archetype(class_name) or '', DEFAULT_HIT_DIE)


def average_hit_point_gain(hit_die: Any, constitution_modifier: Any) -> int:
    die = max(4, min(20, int_or_default(hit_die, default=DEFAULT_HIT_DIE)))
    con_mod = int_or_default(constitution_modifier, default=0)
    return max(1, die // 2 + 1 + con_mod)


def max_hp_for_level(
    *,
    hit_die: Any,
    constitution_modifier: Any,
    level: Any,
    max_hp_bonus: Any = 0,
) -> int:
    die = max(4, min(20, int_or_default(hit_die, default=DEFAULT_HIT_DIE)))
    con_mod = int_or_default(constitution_modifier, default=0)
    character_level = bounded_character_level(level)
    bonus = max(0, int_or_default(max_hp_bonus, default=0))
    return max(
        1,
        die
        + con_mod
        + (character_level - 1) * average_hit_point_gain(die, con_mod)
        + bonus,
    )


def class_max_hp(
    class_name: Any,
    *,
    constitution_score: Any,
    level: Any,
    max_hp_bonus: Any = 0,
) -> int:
    con_mod = (int_or_default(constitution_score, default=10) - 10) // 2
    return max_hp_for_level(
        hit_die=hit_die_for_class(class_name),
        constitution_modifier=con_mod,
        level=level,
        max_hp_bonus=max_hp_bonus,
    )
