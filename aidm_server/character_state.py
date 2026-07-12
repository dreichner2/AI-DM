"""Persistent character state helpers for rules, resources, and rewards."""

from __future__ import annotations

import re
from typing import Any

from aidm_server.canon_text import int_or_default, normalized_name
from aidm_server.database import db
from aidm_server.models import DmTurn, Player, safe_json_dumps, safe_json_loads
from aidm_server.spellbook import (
    class_spell_archetype,
    known_spell_names,
    merge_spellbooks,
    spellbook_for_character,
    spellbook_from_character_sheet,
)
from aidm_server.weapon_proficiency import (
    match_weapon_proficiency,
    normalize_weapon_proficiencies,
)


ABILITY_KEYS = ('strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma')
ABILITY_LABELS = {
    'strength': 'STR',
    'dexterity': 'DEX',
    'constitution': 'CON',
    'intelligence': 'INT',
    'wisdom': 'WIS',
    'charisma': 'CHA',
}
SKILL_ABILITY = {
    'acrobatics': 'dexterity',
    'animal_handling': 'wisdom',
    'arcana': 'intelligence',
    'athletics': 'strength',
    'deception': 'charisma',
    'history': 'intelligence',
    'insight': 'wisdom',
    'intimidation': 'charisma',
    'investigation': 'intelligence',
    'medicine': 'wisdom',
    'nature': 'intelligence',
    'perception': 'wisdom',
    'performance': 'charisma',
    'persuasion': 'charisma',
    'religion': 'intelligence',
    'sleight_of_hand': 'dexterity',
    'stealth': 'dexterity',
    'survival': 'wisdom',
    'thieves_tools': 'dexterity',
}
CLASS_SAVING_THROW_PROFICIENCIES = {
    'artificer': {'constitution', 'intelligence'},
    'barbarian': {'strength', 'constitution'},
    'bard': {'dexterity', 'charisma'},
    'cleric': {'wisdom', 'charisma'},
    'druid': {'intelligence', 'wisdom'},
    'fighter': {'strength', 'constitution'},
    'monk': {'strength', 'dexterity'},
    'paladin': {'wisdom', 'charisma'},
    'ranger': {'strength', 'dexterity'},
    'rogue': {'dexterity', 'intelligence'},
    'sorcerer': {'constitution', 'charisma'},
    'warlock': {'wisdom', 'charisma'},
    'wizard': {'intelligence', 'wisdom'},
}
SPELLCASTING_ABILITY_BY_CLASS = {
    'artificer': 'intelligence',
    'bard': 'charisma',
    'cleric': 'wisdom',
    'druid': 'wisdom',
    'paladin': 'charisma',
    'ranger': 'wisdom',
    'sorcerer': 'charisma',
    'warlock': 'charisma',
    'wizard': 'intelligence',
}
POINT_BUY_BUDGET = 27
POINT_BUY_COSTS = {
    8: 0,
    9: 1,
    10: 2,
    11: 3,
    12: 4,
    13: 5,
    14: 7,
    15: 9,
}

ROLL_TYPE_ABILITY = {
    **SKILL_ABILITY,
    'initiative': 'dexterity',
    'mobility': 'dexterity',
    'lore': 'intelligence',
    'social': 'charisma',
    'strength': 'strength',
    'dexterity': 'dexterity',
    'constitution': 'constitution',
    'intelligence': 'intelligence',
    'wisdom': 'wisdom',
    'charisma': 'charisma',
}
RANGED_WEAPON_MARKERS = {
    'bow',
    'crossbow',
    'firearm',
    'gun',
    'long rifle',
    'longbow',
    'pistol',
    'rifle',
    'shortbow',
    'sidearm',
    'sling',
}
FINESSE_WEAPON_MARKERS = {
    'dagger',
    'knife',
    'rapier',
    'scimitar',
    'shortsword',
    'whip',
}
ROLL_TYPE_BASE_DC = {
    'attack': 14,
    'stealth': 14,
    'social': 15,
    'lore': 13,
    'athletics': 14,
    'thieves_tools': 15,
    'mobility': 14,
    'check': 14,
    **{
        skill: (
            15
            if ability == 'charisma'
            else 13
            if ability == 'intelligence'
            else 14
        )
        for skill, ability in SKILL_ABILITY.items()
    },
}
SKILL_NAME_ALIASES = {
    'animal_handling': 'animal_handling',
    'sleight': 'sleight_of_hand',
    'sleight_hand': 'sleight_of_hand',
    'sleight_of_hand': 'sleight_of_hand',
    'thieves_tool': 'thieves_tools',
    'thieves_tools': 'thieves_tools',
}
KNOWN_SKILLS = set(SKILL_ABILITY)

GOLD_SPEND_PATTERNS = [
    re.compile(r'\b(?:spend|pay|paid|buy|bought|purchase|purchased|hand over|give)\s+(?:[^.!?\n]{0,80}?\s+)?(\d{1,5})\s+(?:gp|gold)\b', re.IGNORECASE),
    re.compile(r'\b(?:for|costs?|price(?: is)?)\s+(\d{1,5})\s+(?:gp|gold)\b', re.IGNORECASE),
]
GOLD_GAIN_PATTERNS = [
    re.compile(r'\b(?:gain|gains|receive|receives|loot|loots|find|finds|earn|earns|take|takes)\s+(\d{1,5})\s+(?:gp|gold)\b', re.IGNORECASE),
]
GOLD_LOSS_PATTERNS = [
    re.compile(r'\b(?:spend|spends|pay|pays|paid|lose|loses|hand over|hands over|give|gives)\s+(\d{1,5})\s+(?:gp|gold)\b', re.IGNORECASE),
    re.compile(r'\b(?:buy|buys|bought|purchase|purchases|purchased)\b[^.!?\n]{0,80}?\b(?:for|costs?)\s+(\d{1,5})\s+(?:gp|gold)\b', re.IGNORECASE),
]
CURRENCY_ALIASES = {
    'copper': 'copper',
    'coppers': 'copper',
    'cp': 'copper',
    'silver': 'silver',
    'silvers': 'silver',
    'sp': 'silver',
    'electrum': 'electrum',
    'electrums': 'electrum',
    'ep': 'electrum',
    'platinum': 'platinum',
    'platinums': 'platinum',
    'pp': 'platinum',
}
CURRENCY_GAIN_PATTERNS = [
    re.compile(
        r'\b(?:gain|gains|receive|receives|loot|loots|find|finds|earn|earns|take|takes|collect|collects|pick up|picks up)\s+'
        r'(\d{1,5})\s+(copper|coppers|cp|silver|silvers|sp|electrum|electrums|ep|platinum|platinums|pp)'
        r'(?:\s+(?:pieces?|coins?))?\b',
        re.IGNORECASE,
    ),
]
CURRENCY_LOSS_PATTERNS = [
    re.compile(
        r'\b(?:spend|spends|pay|pays|paid|lose|loses|hand over|hands over|give|gives)\s+'
        r'(\d{1,5})\s+(copper|coppers|cp|silver|silvers|sp|electrum|electrums|ep|platinum|platinums|pp)'
        r'(?:\s+(?:pieces?|coins?))?\b',
        re.IGNORECASE,
    ),
]
DAMAGE_PATTERNS = [
    re.compile(r'\b(?:take|takes|suffer|suffers)\s+(\d{1,4})\s+(?:points?\s+of\s+)?damage\b', re.IGNORECASE),
    re.compile(r'\b(?:hit|hits|struck|wounded)\b[^.!?\n]{0,80}?\bfor\s+(\d{1,4})\s+(?:points?\s+of\s+)?damage\b', re.IGNORECASE),
]
HEAL_PATTERNS = [
    re.compile(r'\b(?:heal|heals|regain|regains|recover|recovers)\s+(\d{1,4})\s+(?:hp|hit points?)\b', re.IGNORECASE),
]
XP_GAIN_PATTERNS = [
    re.compile(r'\b(?:gain|gains|earn|earns|award(?:ed)?|receive|receives)\s+(\d{1,6})\s+(?:xp|experience)\b', re.IGNORECASE),
]


def _as_record(value: Any) -> dict[str, Any]:
    loaded = safe_json_loads(value, {})
    return loaded if isinstance(loaded, dict) else {}


def _coerce_score(value: Any) -> int | None:
    parsed = int_or_default(value, default=-1)
    return parsed if parsed >= 1 else None


def ability_modifier(score: int | None) -> int:
    if score is None:
        return 0
    return (int(score) - 10) // 2


def _skill_key(value: Any) -> str:
    text = str(value or '').strip().lower().replace('&', 'and')
    text = re.sub(r"['’]", '', text)
    text = re.sub(r'[^a-z0-9]+', '_', text).strip('_')
    return SKILL_NAME_ALIASES.get(text, text)


def _collect_skill_values(raw: Any, proficiencies: set[str]) -> None:
    if isinstance(raw, str):
        key = _skill_key(raw)
        if key in KNOWN_SKILLS:
            proficiencies.add(key)
        return
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                if item.get('proficient') is False or item.get('trained') is False:
                    continue
                _collect_skill_values(item.get('name') or item.get('skill') or item.get('id'), proficiencies)
            else:
                _collect_skill_values(item, proficiencies)
        return
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                enabled = value.get('proficient', value.get('trained', value.get('value', True)))
                if not enabled:
                    continue
            elif isinstance(value, bool):
                if not value:
                    continue
            else:
                continue
            _collect_skill_values(key, proficiencies)


def _extract_skill_proficiencies(stats: dict[str, Any], sheet: dict[str, Any]) -> list[str]:
    proficiencies: set[str] = set()
    for source in (stats, sheet):
        if not isinstance(source, dict):
            continue
        for key in (
            'skill_proficiencies',
            'skillProficiencies',
            'proficientSkills',
            'skills',
        ):
            _collect_skill_values(source.get(key), proficiencies)
    return sorted(proficiencies)


def _extract_skill_expertise(stats: dict[str, Any], sheet: dict[str, Any]) -> list[str]:
    expertise: set[str] = set()
    for source in (stats, sheet):
        if not isinstance(source, dict):
            continue
        for key in ('skill_expertise', 'skillExpertise', 'expertise', 'expertSkills'):
            _collect_skill_values(source.get(key), expertise)

        skills = source.get('skills')
        if isinstance(skills, list):
            for record in skills:
                if not isinstance(record, dict):
                    continue
                multiplier = int_or_default(
                    record.get('proficiency_multiplier', record.get('proficiencyMultiplier')),
                    default=0,
                )
                if record.get('expertise') or multiplier >= 2:
                    _collect_skill_values(record.get('name') or record.get('skill') or record.get('id'), expertise)
        elif isinstance(skills, dict):
            for skill_name, record in skills.items():
                if not isinstance(record, dict):
                    continue
                multiplier = int_or_default(
                    record.get('proficiency_multiplier', record.get('proficiencyMultiplier')),
                    default=0,
                )
                if record.get('expertise') or multiplier >= 2:
                    _collect_skill_values(skill_name, expertise)
    return sorted(expertise)


def _collect_ability_values(raw: Any, proficiencies: set[str]) -> None:
    if isinstance(raw, str):
        key = _skill_key(raw).removesuffix('_saving_throw').removesuffix('_save')
        if key in ABILITY_KEYS:
            proficiencies.add(key)
        return
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                if item.get('proficient') is False or item.get('trained') is False:
                    continue
                _collect_ability_values(item.get('ability') or item.get('name') or item.get('id'), proficiencies)
            else:
                _collect_ability_values(item, proficiencies)
        return
    if isinstance(raw, dict):
        for key, enabled in raw.items():
            if isinstance(enabled, dict):
                enabled = enabled.get('proficient', enabled.get('trained', enabled.get('value', True)))
            if enabled:
                _collect_ability_values(key, proficiencies)


def _base_class_name(value: Any) -> str:
    class_name = normalized_name(value)
    for candidate in CLASS_SAVING_THROW_PROFICIENCIES:
        if class_name == candidate or class_name.startswith(f'{candidate} '):
            return candidate
    return class_name


def _extract_saving_throw_proficiencies(
    player: Player,
    stats: dict[str, Any],
    sheet: dict[str, Any],
) -> list[str]:
    class_archetype = class_spell_archetype(player.class_) or _base_class_name(player.class_)
    proficiencies = set(CLASS_SAVING_THROW_PROFICIENCIES.get(class_archetype, set()))
    for source in (stats, sheet):
        if not isinstance(source, dict):
            continue
        for key in (
            'saving_throw_proficiencies',
            'savingThrowProficiencies',
            'proficientSavingThrows',
            'saving_throws',
            'savingThrows',
        ):
            _collect_ability_values(source.get(key), proficiencies)
    return sorted(proficiencies)


def _race_skill_proficiencies(player: Player) -> list[str]:
    try:
        from aidm_server.race_system import race_definition_from_selection, race_selection_from_json

        selection = race_selection_from_json(player.race_selection, player.race)
        race = race_definition_from_selection(selection, player.race)
    except (TypeError, ValueError):
        return []
    if not isinstance(race, dict):
        return []

    proficiencies: set[str] = set()
    for trait in race.get('traits') or []:
        if not isinstance(trait, dict):
            continue
        mechanics = trait.get('mechanics') if isinstance(trait.get('mechanics'), dict) else {}
        bonus = mechanics.get('skillBonus') if isinstance(mechanics.get('skillBonus'), dict) else {}
        if str(bonus.get('bonusType') or '').strip().lower() not in {'proficiency', 'expertise'}:
            continue
        for skill in bonus.get('skills') or []:
            skill_key = _skill_key(skill)
            if skill_key in KNOWN_SKILLS:
                proficiencies.add(skill_key)
    return sorted(proficiencies)


def _extract_ability_scores(stats: dict[str, Any]) -> dict[str, int]:
    source = stats.get('ability_scores') if isinstance(stats.get('ability_scores'), dict) else stats
    scores: dict[str, int] = {}
    for key in ABILITY_KEYS:
        raw_value = source.get(key)
        if raw_value is None:
            raw_value = source.get(ABILITY_LABELS[key].lower())
        score = _coerce_score(raw_value)
        if score is not None:
            scores[key] = score
    return scores


def point_buy_spent(scores: dict[str, int]) -> int:
    spent = 0
    for key in ABILITY_KEYS:
        score = int(scores.get(key, 8))
        spent += POINT_BUY_COSTS.get(score, POINT_BUY_BUDGET + 1)
    return spent


def validate_point_buy_payload(raw_stats: Any, *, level: int) -> tuple[dict[str, Any] | None, str | None]:
    stats = _as_record(raw_stats)
    has_point_buy_shape = isinstance(stats.get('ability_scores'), dict) or isinstance(stats.get('point_buy'), dict)
    if not has_point_buy_shape:
        return stats, None

    scores = _extract_ability_scores(stats)
    missing = [key for key in ABILITY_KEYS if key not in scores]
    if missing:
        return None, f"stats.ability_scores is missing: {', '.join(missing)}."

    for key, score in scores.items():
        if score not in POINT_BUY_COSTS:
            return None, f'stats.ability_scores.{key} must be between 8 and 15 for point buy.'

    spent = point_buy_spent(scores)
    if spent > POINT_BUY_BUDGET:
        return None, f'stats point buy exceeds {POINT_BUY_BUDGET} points.'

    con_mod = ability_modifier(scores['constitution'])
    max_hp = int_or_default(
        stats.get('max_hp', stats.get('hp_max', stats.get('max_hit_points'))),
        default=max(1, 8 + con_mod + max(0, int(level) - 1) * max(1, 5 + con_mod)),
    )
    current_hp = int_or_default(
        stats.get('current_hp', stats.get('hp_current', stats.get('hp'))),
        default=max_hp,
    )
    current_hp = max(0, min(current_hp, max_hp))
    gold = max(0, int_or_default(stats.get('gold'), default=0))
    xp = max(0, int_or_default(stats.get('xp', stats.get('experience')), default=0))

    normalized: dict[str, Any] = {
        **stats,
        'ability_scores': scores,
        'point_buy': {
            'budget': POINT_BUY_BUDGET,
            'spent': spent,
            'remaining': POINT_BUY_BUDGET - spent,
        },
        'current_hp': current_hp,
        'hp_current': current_hp,
        'max_hp': max_hp,
        'hp_max': max_hp,
        'gold': gold,
        'xp': xp,
        'experience': xp,
        'proficiency_bonus': 2 + max(0, int(level) - 1) // 4,
        'armor_class': int_or_default(stats.get('armor_class', stats.get('ac')), default=10 + ability_modifier(scores['dexterity'])),
        'initiative': int_or_default(stats.get('initiative'), default=ability_modifier(scores['dexterity'])),
        'speed': int_or_default(stats.get('speed'), default=30),
        'carrying_capacity': int_or_default(stats.get('carrying_capacity'), default=scores['strength'] * 15),
    }
    for key, score in scores.items():
        normalized[key] = score
        normalized[ABILITY_LABELS[key].lower()] = score
    return normalized, None


def serialize_stats_payload(raw_stats: Any, *, level: int) -> tuple[str | None, str | None]:
    if raw_stats is None:
        return None, None
    normalized, error = validate_point_buy_payload(raw_stats, level=level)
    if error:
        return None, error
    return safe_json_dumps(normalized, {}), None


def character_state_for_player(player: Player | None) -> dict[str, Any]:
    if not player:
        return {}

    stats = _as_record(player.stats)
    sheet = _as_record(player.character_sheet)
    scores = _extract_ability_scores(stats)
    con_mod = ability_modifier(scores.get('constitution'))
    max_hp = int_or_default(
        stats.get('max_hp', stats.get('hp_max', stats.get('max_hit_points'))),
        default=(max(1, 8 + con_mod + max(0, int(player.level or 1) - 1) * max(1, 5 + con_mod)) if scores else 0),
    )
    current_hp = int_or_default(
        stats.get('current_hp', stats.get('hp_current', stats.get('hp'))),
        default=max_hp,
    )
    gold = max(0, int_or_default(stats.get('gold'), default=0))
    copper = max(0, int_or_default(stats.get('copper'), default=0))
    silver = max(0, int_or_default(stats.get('silver'), default=0))
    electrum = max(0, int_or_default(stats.get('electrum'), default=0))
    platinum = max(0, int_or_default(stats.get('platinum'), default=0))
    xp = max(0, int_or_default(stats.get('xp', stats.get('experience')), default=0))
    spent = point_buy_spent(scores) if scores and all(key in scores for key in ABILITY_KEYS) else None
    spellbook = merge_spellbooks(
        spellbook_from_character_sheet(player.character_sheet),
        spellbook_for_character(
            class_name=player.class_,
            race_name=player.race,
            race_selection=player.race_selection,
            level=player.level or 1,
        ),
    )

    state = {
        'ability_scores': scores,
        'ability_modifiers': {key: ability_modifier(scores.get(key)) for key in ABILITY_KEYS if key in scores},
        'point_buy': {
            'budget': POINT_BUY_BUDGET,
            'spent': spent,
            'remaining': (POINT_BUY_BUDGET - spent if spent is not None else None),
        },
        'hp': {
            'current': max(0, current_hp),
            'max': max_hp,
            'bloodied': bool(max_hp and current_hp <= max_hp // 2),
            'critical': bool(max_hp and current_hp <= max(1, max_hp // 4)),
        },
        'gold': gold,
        'copper': copper,
        'silver': silver,
        'electrum': electrum,
        'platinum': platinum,
        'xp': xp,
        'level': int(player.level or 1),
        'proficiency_bonus': int_or_default(stats.get('proficiency_bonus'), default=2 + max(0, int(player.level or 1) - 1) // 4),
    }
    skill_expertise = _extract_skill_expertise(stats, sheet)
    skill_proficiencies = sorted(
        {
            *_extract_skill_proficiencies(stats, sheet),
            *_race_skill_proficiencies(player),
            *skill_expertise,
        }
    )
    if skill_proficiencies:
        state['skill_proficiencies'] = skill_proficiencies
    if skill_expertise:
        state['skill_expertise'] = skill_expertise
    saving_throw_proficiencies = _extract_saving_throw_proficiencies(player, stats, sheet)
    if saving_throw_proficiencies:
        state['saving_throw_proficiencies'] = saving_throw_proficiencies
    weapon_proficiencies = normalize_weapon_proficiencies(player.weapon_proficiencies)
    if weapon_proficiencies:
        state['weapon_proficiencies'] = weapon_proficiencies
    if spellbook.get('knownSpells'):
        state['spellbook'] = spellbook
        state['spells'] = known_spell_names(spellbook)
    return state


def _task_dc_from_hint(dc_hint: Any, *, fallback: int | None) -> int | None:
    """Read an exact task DC without treating a range as a resolved DC."""

    text = str(dc_hint or '').strip()
    match = re.search(r'\bDC\s*(\d{1,2})\b', text, re.IGNORECASE)
    if not match:
        match = re.match(r'^(\d{1,2})(?!\s*-)', text)
    if match:
        parsed = int(match.group(1))
        if 5 <= parsed <= 30:
            return parsed
    return fallback


def _weapon_labels(item: dict[str, Any]) -> set[str]:
    labels = {
        normalized_name(item.get('name')),
        normalized_name(item.get('subtype')),
        *[normalized_name(alias) for alias in item.get('aliases') or []],
        *[normalized_name(tag) for tag in item.get('tags') or []],
    }
    return {label for label in labels if label}


def _text_mentions_weapon(text: str, item: dict[str, Any]) -> bool:
    normalized_text = normalized_name(text)
    return any(
        re.search(rf'(?:^|\s){re.escape(label)}(?:\s|$)', normalized_text)
        for label in _weapon_labels(item)
    )


def server_attack_roll_context(player: Player | None, action_text: str | None) -> dict[str, Any]:
    """Resolve weapon/ability from inventory and proficiency from the player profile."""

    from aidm_server.canon_inventory import inventory_payload

    state = character_state_for_player(player)
    modifiers = state.get('ability_modifiers') if isinstance(state.get('ability_modifiers'), dict) else {}
    weapons = [
        item
        for item in inventory_payload(player.inventory if player else None)
        if normalized_name(item.get('type')) == 'weapon'
    ]
    mentioned = [item for item in weapons if _text_mentions_weapon(str(action_text or ''), item)]
    weapon = mentioned[0] if len(mentioned) == 1 else None
    resolution = 'named_inventory_weapon' if weapon else None

    if weapon is None:
        equipped = [item for item in weapons if item.get('equipped')]
        primary = [
            item
            for item in equipped
            if normalized_name(item.get('slot') or item.get('equipmentSlot')) in {'main hand', 'two hands'}
        ]
        if len(primary) == 1:
            weapon = primary[0]
            resolution = 'equipped_primary_weapon'
        elif len(equipped) == 1:
            weapon = equipped[0]
            resolution = 'equipped_weapon'

    if weapon is None:
        return {
            'ability_key': 'strength',
            'proficient': False,
            'source': 'server_default',
            'resolution': 'no_unique_persisted_weapon',
        }

    labels = _weapon_labels(weapon)
    ranged = any(
        marker in label or label in RANGED_WEAPON_MARKERS
        for label in labels
        for marker in ('bow', 'crossbow', 'firearm', 'pistol', 'rifle', 'sidearm', 'sling', 'ranged')
    )
    finesse = not ranged and any(
        marker in label or label in FINESSE_WEAPON_MARKERS
        for label in labels
        for marker in FINESSE_WEAPON_MARKERS
    )
    if ranged:
        ability_key = 'dexterity'
        classification = 'ranged'
    elif finesse:
        strength_modifier = int_or_default(modifiers.get('strength'), default=0)
        dexterity_modifier = int_or_default(modifiers.get('dexterity'), default=0)
        ability_key = 'dexterity' if dexterity_modifier > strength_modifier else 'strength'
        classification = 'finesse'
    else:
        ability_key = 'strength'
        classification = 'melee'

    proficient, proficiency_selector = match_weapon_proficiency(
        player.weapon_proficiencies if player else None,
        weapon,
    )
    proficiency_source = 'player_weapon_proficiencies' if proficient else None
    weapon_payload = {
        key: weapon.get(key)
        for key in ('id', 'name', 'subtype')
        if weapon.get(key) not in (None, '')
    }
    weapon_payload['classification'] = classification
    return {
        'ability_key': ability_key,
        'weapon': weapon_payload,
        'proficient': proficient,
        'proficiency_source': proficiency_source,
        'proficiency_selector': proficiency_selector,
        'source': 'persisted_inventory',
        'resolution': resolution,
    }


def _requested_roll_ability(
    roll_type: str,
    requested_ability_key: str | None,
    player: Player | None,
    attack_context: dict[str, Any] | None = None,
) -> str | None:
    if roll_type.endswith('_saving_throw'):
        saving_throw_ability = roll_type.removesuffix('_saving_throw')
        return saving_throw_ability if saving_throw_ability in ABILITY_KEYS else None
    if roll_type == 'attack':
        attack_ability = str((attack_context or {}).get('ability_key') or '').strip().lower()
        return attack_ability if attack_ability in {'strength', 'dexterity'} else 'strength'

    mapped = ROLL_TYPE_ABILITY.get(roll_type)
    if mapped:
        return mapped

    if roll_type == 'spell' and player:
        class_ability = SPELLCASTING_ABILITY_BY_CLASS.get(class_spell_archetype(player.class_) or '')
        if class_ability:
            return class_ability
    requested = str(requested_ability_key or '').strip().lower()
    if requested in ABILITY_KEYS:
        return requested
    return None


def character_roll_spec(
    player: Player | None,
    *,
    roll_type: str | None,
    requested_ability_key: str | None = None,
    dc_hint: str | None = None,
    attack_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a server-owned d20 modifier and task-DC description.

    Client ability labels may select an otherwise ambiguous ability, but every
    score, proficiency, and penalty is derived from the persisted player.
    """

    normalized_roll_type = str(roll_type or 'check').strip().lower() or 'check'
    state = character_state_for_player(player)
    ability_key = _requested_roll_ability(
        normalized_roll_type,
        requested_ability_key,
        player,
        attack_context,
    )
    scores = state.get('ability_scores') if isinstance(state.get('ability_scores'), dict) else {}
    modifiers = state.get('ability_modifiers') if isinstance(state.get('ability_modifiers'), dict) else {}
    ability_mod = int_or_default(modifiers.get(ability_key), default=0) if ability_key else 0
    skill_proficiencies = set(state.get('skill_proficiencies') if isinstance(state.get('skill_proficiencies'), list) else [])
    skill_expertise = set(state.get('skill_expertise') if isinstance(state.get('skill_expertise'), list) else [])
    saving_throw_proficiencies = set(
        state.get('saving_throw_proficiencies')
        if isinstance(state.get('saving_throw_proficiencies'), list)
        else []
    )
    proficiency_multiplier = 0
    if normalized_roll_type == 'attack' and bool((attack_context or {}).get('proficient')):
        weapon = (attack_context or {}).get('weapon')
        weapon = weapon if isinstance(weapon, dict) else {}
        weapon_name = normalized_name(weapon.get('name') or weapon.get('subtype')) or 'weapon'
        matching_proficiencies = [f'weapon:{weapon_name}']
        proficiency_multiplier = 1
    elif normalized_roll_type.endswith('_saving_throw'):
        saving_throw_ability = normalized_roll_type.removesuffix('_saving_throw')
        matching_proficiencies = (
            [f'save:{saving_throw_ability}']
            if saving_throw_ability in saving_throw_proficiencies
            else []
        )
        proficiency_multiplier = 1 if matching_proficiencies else 0
    elif normalized_roll_type in SKILL_ABILITY:
        matching_proficiencies = (
            [normalized_roll_type]
            if normalized_roll_type in skill_proficiencies
            else []
        )
        proficiency_multiplier = 2 if normalized_roll_type in skill_expertise else (1 if matching_proficiencies else 0)
    elif normalized_roll_type == 'spell' and (
        spell_archetype := class_spell_archetype(player.class_ if player else None)
    ) in SPELLCASTING_ABILITY_BY_CLASS:
        matching_proficiencies = [f'spellcasting:{spell_archetype}']
        proficiency_multiplier = 1
    else:
        # Broad legacy categories do not identify one concrete skill. Applying
        # whichever related proficiency happens to exist would let Deception
        # qualify Persuasion, History qualify Arcana, and similar mismatches.
        matching_proficiencies = []
    base_proficiency_bonus = int_or_default(state.get('proficiency_bonus'), default=0)
    proficiency_bonus = base_proficiency_bonus * proficiency_multiplier
    hp = state.get('hp') if isinstance(state.get('hp'), dict) else {}
    current_hp = int_or_default(hp.get('current'), default=0)
    max_hp = int_or_default(hp.get('max'), default=0)
    hp_penalty = 0
    if max_hp > 0:
        ratio = current_hp / max_hp
        if ratio <= 0.25:
            hp_penalty = 4
        elif ratio <= 0.5:
            hp_penalty = 2
        elif ratio <= 0.75:
            hp_penalty = 1

    total_modifier = ability_mod + proficiency_bonus - hp_penalty
    fallback_dc = ROLL_TYPE_BASE_DC.get(normalized_roll_type)
    if fallback_dc is None and normalized_roll_type != 'initiative':
        fallback_dc = ROLL_TYPE_BASE_DC['check']
    task_dc = _task_dc_from_hint(dc_hint, fallback=fallback_dc)
    ability_label = ABILITY_LABELS.get(ability_key or '', 'ability')
    score = scores.get(ability_key) if ability_key else None
    result = {
        'die': 'd20',
        'mode': 'normal',
        'rule_type': normalized_roll_type,
        'task_dc': task_dc,
        'ability': (
            {
                'key': ability_key,
                'label': ability_label,
                'score': score,
                'modifier': ability_mod,
            }
            if ability_key
            else None
        ),
        'proficiency': {
            'bonus': proficiency_bonus,
            'skills': matching_proficiencies,
            'multiplier': proficiency_multiplier,
        },
        'modifier_breakdown': {
            'ability_modifier': ability_mod,
            'proficiency_bonus': proficiency_bonus,
            'proficiency_multiplier': proficiency_multiplier,
            'wound_penalty': hp_penalty,
            'total': total_modifier,
        },
        'modifier': total_modifier,
    }
    if normalized_roll_type == 'attack' and attack_context:
        result['attack'] = {
            key: value
            for key, value in attack_context.items()
            if value is not None
        }
    return result


def apply_character_dc_adjustment(
    rule_hint,
    player: Player | None,
    *,
    requested_ability_key: str | None = None,
    attack_context: dict[str, Any] | None = None,
):
    if not getattr(rule_hint, 'requires_roll', False) or getattr(rule_hint, 'roll_value', None) is not None:
        return rule_hint

    spec = character_roll_spec(
        player,
        roll_type=getattr(rule_hint, 'roll_type', None),
        requested_ability_key=requested_ability_key,
        dc_hint=getattr(rule_hint, 'dc_hint', None),
        attack_context=attack_context,
    )
    ability = spec.get('ability') if isinstance(spec.get('ability'), dict) else None
    proficiency = spec.get('proficiency') if isinstance(spec.get('proficiency'), dict) else {}
    breakdown = spec.get('modifier_breakdown') if isinstance(spec.get('modifier_breakdown'), dict) else {}
    details = [f"roll mod {int_or_default(spec.get('modifier'), default=0):+d}"]
    if ability:
        details.append(
            f"{ability.get('label') or 'ability'} {ability.get('score') if ability.get('score') is not None else 'unknown'} "
            f"mod {int_or_default(ability.get('modifier'), default=0):+d}"
        )
    if int_or_default(proficiency.get('bonus'), default=0):
        details.append(
            f"proficiency +{int_or_default(proficiency.get('bonus'), default=0)} "
            f"({'/'.join(proficiency.get('skills') or [])})"
        )
    if int_or_default(breakdown.get('wound_penalty'), default=0):
        details.append(f"wounded -{int_or_default(breakdown.get('wound_penalty'), default=0)}")

    task_dc = spec.get('task_dc')
    dc_label = str(task_dc) if task_dc is not None else str(getattr(rule_hint, 'dc_hint', None) or 'initiative order')
    rule_hint.dc_hint = f'{dc_label} ({", ".join(details)})'
    return rule_hint


def inventory_contains(player: Player | None, item_name: str | None, quantity: int = 1) -> bool:
    if not player or not item_name:
        return False
    from aidm_server.canon_inventory import load_inventory

    wanted = normalized_name(item_name)
    for item in load_inventory(player.inventory):
        if normalized_name(item.get('name')) == wanted and int_or_default(item.get('quantity'), default=1) >= quantity:
            return True
    return False


def requested_gold_spend(text: str | None) -> int:
    total = 0
    for pattern in GOLD_SPEND_PATTERNS:
        for match in pattern.finditer(text or ''):
            total += max(0, int_or_default(match.group(1), default=0))
    return total


def _sum_patterns(text: str, patterns: list[re.Pattern]) -> int:
    total = 0
    for pattern in patterns:
        for match in pattern.finditer(text or ''):
            total += max(0, int_or_default(match.group(1), default=0))
    return total


def _sum_currency_patterns(text: str, patterns: list[re.Pattern]) -> dict[str, int]:
    totals = {denomination: 0 for denomination in {'copper', 'silver', 'electrum', 'platinum'}}
    for pattern in patterns:
        for match in pattern.finditer(text or ''):
            denomination = CURRENCY_ALIASES.get(str(match.group(2) or '').strip().lower())
            if denomination:
                totals[denomination] += max(0, int_or_default(match.group(1), default=0))
    return totals


def _intent_confirmed_gold_delta(turn: DmTurn, dm_output: str, *, gold_gain: int, gold_loss: int) -> int:
    metadata = safe_json_loads(turn.metadata_json, {})
    action_intent = metadata.get('action_intent') if isinstance(metadata, dict) else None
    if not isinstance(action_intent, dict) or action_intent.get('kind') != 'item':
        return 0

    inventory_action = str(action_intent.get('inventory_action') or '').strip().lower()
    if inventory_action not in {'buy', 'sell'}:
        return 0

    cost_gold = max(0, int_or_default(action_intent.get('cost_gold'), default=0))
    if not cost_gold:
        return 0

    from aidm_server.canon_inventory import inventory_change_from_intent_outcome

    if not inventory_change_from_intent_outcome(turn, dm_output):
        return 0
    if inventory_action == 'buy' and gold_loss <= 0:
        return -cost_gold
    if inventory_action == 'sell' and gold_gain <= 0:
        return cost_gold
    return 0


def _stats_with_state(player: Player) -> dict[str, Any]:
    stats = _as_record(player.stats)
    state = character_state_for_player(player)
    hp = state.get('hp') if isinstance(state.get('hp'), dict) else {}
    stats.setdefault('gold', state.get('gold', 0))
    stats.setdefault('copper', state.get('copper', 0))
    stats.setdefault('silver', state.get('silver', 0))
    stats.setdefault('electrum', state.get('electrum', 0))
    stats.setdefault('platinum', state.get('platinum', 0))
    stats.setdefault('xp', state.get('xp', 0))
    stats.setdefault('experience', state.get('xp', 0))
    if hp.get('max'):
        stats.setdefault('max_hp', hp.get('max'))
        stats.setdefault('hp_max', hp.get('max'))
        stats.setdefault('current_hp', hp.get('current'))
        stats.setdefault('hp_current', hp.get('current'))
    return stats


def _apply_delta(
    player: Player,
    *,
    gold_delta: int = 0,
    hp_delta: int = 0,
    xp_delta: int = 0,
    currency_delta: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    currency_delta = currency_delta or {}
    if not any((gold_delta, hp_delta, xp_delta, *currency_delta.values())):
        return None

    stats = _stats_with_state(player)
    current_gold = max(0, int_or_default(stats.get('gold'), default=0))
    current_xp = max(0, int_or_default(stats.get('xp', stats.get('experience')), default=0))
    max_hp = max(0, int_or_default(stats.get('max_hp', stats.get('hp_max')), default=0))
    current_hp = int_or_default(stats.get('current_hp', stats.get('hp_current', stats.get('hp'))), default=max_hp)

    next_gold = max(0, current_gold + gold_delta)
    next_xp = max(0, current_xp + xp_delta)
    next_hp = max(0, min(max_hp, current_hp + hp_delta)) if max_hp else max(0, current_hp + hp_delta)
    next_currency = {
        denomination: max(0, int_or_default(stats.get(denomination), default=0) + int(delta))
        for denomination, delta in currency_delta.items()
    }

    stats['gold'] = next_gold
    for denomination, amount in next_currency.items():
        stats[denomination] = amount
    stats['xp'] = next_xp
    stats['experience'] = next_xp
    if max_hp:
        stats['current_hp'] = next_hp
        stats['hp_current'] = next_hp
        stats['max_hp'] = max_hp
        stats['hp_max'] = max_hp
    player.stats = safe_json_dumps(stats, {})

    return {
        'player_id': player.player_id,
        'character_name': player.character_name,
        'gold_delta': gold_delta,
        'gold': next_gold,
        'currency_delta': {key: value for key, value in currency_delta.items() if value},
        **next_currency,
        'hp_delta': hp_delta,
        'hp_current': next_hp,
        'hp_max': max_hp,
        'xp_delta': xp_delta,
        'xp': next_xp,
    }


def apply_character_state_changes(turn: DmTurn, dm_output: str) -> list[dict[str, Any]]:
    if not turn.player_id:
        return []

    player = db.session.get(Player, turn.player_id)
    if not player:
        return []

    text = re.sub(r'\*+', '', dm_output or '')
    gold_gain = _sum_patterns(text, GOLD_GAIN_PATTERNS)
    gold_loss = _sum_patterns(text, GOLD_LOSS_PATTERNS)
    gold_delta = gold_gain - gold_loss + _intent_confirmed_gold_delta(turn, text, gold_gain=gold_gain, gold_loss=gold_loss)
    currency_gain = _sum_currency_patterns(text, CURRENCY_GAIN_PATTERNS)
    currency_loss = _sum_currency_patterns(text, CURRENCY_LOSS_PATTERNS)
    currency_delta = {
        denomination: currency_gain.get(denomination, 0) - currency_loss.get(denomination, 0)
        for denomination in {'copper', 'silver', 'electrum', 'platinum'}
        if currency_gain.get(denomination, 0) - currency_loss.get(denomination, 0)
    }
    hp_delta = _sum_patterns(text, HEAL_PATTERNS) - _sum_patterns(text, DAMAGE_PATTERNS)
    xp_delta = _sum_patterns(text, XP_GAIN_PATTERNS)
    applied = _apply_delta(
        player,
        gold_delta=gold_delta,
        hp_delta=hp_delta,
        xp_delta=xp_delta,
        currency_delta=currency_delta,
    )
    return [applied] if applied else []
