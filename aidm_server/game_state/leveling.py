from __future__ import annotations

from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.character_progression import (
    DEFAULT_HIT_DIE,
    max_hp_for_level,
    proficiency_bonus_for_level as derived_proficiency_bonus_for_level,
)


XP_THRESHOLDS_BY_LEVEL: dict[int, int] = {
    1: 0,
    2: 300,
    3: 900,
    4: 2700,
    5: 6500,
    6: 14000,
    7: 23000,
    8: 34000,
    9: 48000,
    10: 64000,
    11: 85000,
    12: 100000,
    13: 120000,
    14: 140000,
    15: 165000,
    16: 195000,
    17: 225000,
    18: 265000,
    19: 305000,
    20: 355000,
}


def level_for_xp(xp_total: Any) -> int:
    total = max(0, int_or_default(xp_total, default=0))
    earned_level = 1
    for level, threshold in XP_THRESHOLDS_BY_LEVEL.items():
        if total >= threshold:
            earned_level = level
    return earned_level


def next_level_threshold(level: Any) -> int | None:
    current_level = max(1, min(20, int_or_default(level, default=1)))
    return XP_THRESHOLDS_BY_LEVEL.get(current_level + 1)


def proficiency_bonus_for_level(level: Any) -> int:
    return derived_proficiency_bonus_for_level(level)


def _ability_score(stats: dict[str, Any], key: str) -> int | None:
    ability_scores = stats.get('ability_scores') if isinstance(stats.get('ability_scores'), dict) else {}
    value = ability_scores.get(key, stats.get(key))
    if value is None:
        aliases = {'constitution': 'con', 'strength': 'str', 'dexterity': 'dex', 'intelligence': 'int', 'wisdom': 'wis', 'charisma': 'cha'}
        value = stats.get(aliases.get(key, ''))
    if value is None:
        return None
    return int_or_default(value, default=8)


def ability_modifier(score: Any) -> int:
    return (int_or_default(score, default=10) - 10) // 2


def baseline_max_hp_for_level(stats: dict[str, Any], level: Any) -> int | None:
    constitution = _ability_score(stats, 'constitution')
    if constitution is None:
        return None
    return max_hp_for_level(
        hit_die=stats.get('hit_die', stats.get('hitDie', DEFAULT_HIT_DIE)),
        constitution_modifier=ability_modifier(constitution),
        level=level,
        max_hp_bonus=stats.get('max_hp_bonus', stats.get('maxHpBonus', 0)),
    )


def sync_actor_level_for_xp(actor: dict[str, Any]) -> tuple[int, int]:
    xp = actor.setdefault('xp', {})
    current_xp = max(0, int_or_default(xp.get('current'), default=0))
    level_before = max(1, min(20, int_or_default(actor.get('level'), default=1)))
    earned_level = level_for_xp(current_xp)
    level_after = max(level_before, earned_level)
    if 'level' in actor or level_after > level_before:
        actor['level'] = level_after
    xp['nextLevelAt'] = next_level_threshold(level_after)
    return level_before, level_after


def sync_stats_for_level(stats: dict[str, Any], level: Any) -> bool:
    current_level = max(1, min(20, int_or_default(level, default=1)))
    changed = False

    proficiency = proficiency_bonus_for_level(current_level)
    if int_or_default(stats.get('proficiency_bonus'), default=0) != proficiency:
        stats['proficiency_bonus'] = proficiency
        changed = True

    next_threshold = next_level_threshold(current_level)
    if stats.get('next_level_at') != next_threshold:
        stats['next_level_at'] = next_threshold
        changed = True
    if stats.get('nextLevelAt') != next_threshold:
        stats['nextLevelAt'] = next_threshold
        changed = True

    baseline_max_hp = baseline_max_hp_for_level(stats, current_level)
    if baseline_max_hp is None:
        return changed

    current_max_hp = max(0, int_or_default(stats.get('max_hp', stats.get('hp_max', stats.get('max_hit_points'))), default=0))
    if baseline_max_hp <= current_max_hp:
        return changed

    current_hp = max(0, int_or_default(stats.get('current_hp', stats.get('hp_current', stats.get('hp'))), default=current_max_hp))
    hp_gain = baseline_max_hp - current_max_hp
    next_current_hp = min(baseline_max_hp, current_hp + hp_gain)

    stats['max_hp'] = baseline_max_hp
    stats['hp_max'] = baseline_max_hp
    stats['current_hp'] = next_current_hp
    stats['hp_current'] = next_current_hp
    changed = True
    return changed
