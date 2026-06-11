from __future__ import annotations

from typing import Any


TACTICAL_LEVELS = {'simple', 'normal', 'smart', 'brutal'}

DEFAULT_COMBAT_DIFFICULTY_AI = {
    'tacticalLevel': 'normal',
    'allowFocusFire': True,
    'allowTargetHealers': True,
    'allowEnemyRetreat': True,
    'allowEnemySurrender': True,
    'allowEnvironmentalHazards': True,
    'allowBossTacticsHelper': True,
    'allowSentientEnemyBrain': True,
}

LEVEL_DEFAULTS = {
    'simple': {
        'allowFocusFire': False,
        'allowTargetHealers': False,
        'allowEnemyRetreat': True,
        'allowEnemySurrender': True,
        'allowEnvironmentalHazards': False,
        'allowBossTacticsHelper': False,
        'allowSentientEnemyBrain': False,
    },
    'normal': DEFAULT_COMBAT_DIFFICULTY_AI,
    'smart': {
        **DEFAULT_COMBAT_DIFFICULTY_AI,
        'allowEnvironmentalHazards': True,
        'allowBossTacticsHelper': True,
    },
    'brutal': {
        **DEFAULT_COMBAT_DIFFICULTY_AI,
        'allowFocusFire': True,
        'allowTargetHealers': True,
        'allowEnemyRetreat': True,
        'allowEnemySurrender': False,
        'allowEnvironmentalHazards': True,
        'allowBossTacticsHelper': True,
    },
}


def normalize_combat_difficulty_ai(value: Any = None) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    tactical_level = str(raw.get('tacticalLevel') or raw.get('tactical_level') or 'normal').strip().lower()
    if tactical_level not in TACTICAL_LEVELS:
        tactical_level = 'normal'
    settings = {**LEVEL_DEFAULTS[tactical_level], 'tacticalLevel': tactical_level}
    for key in (
        'allowFocusFire',
        'allowTargetHealers',
        'allowEnemyRetreat',
        'allowEnemySurrender',
        'allowEnvironmentalHazards',
        'allowBossTacticsHelper',
        'allowSentientEnemyBrain',
    ):
        if key in raw:
            settings[key] = bool(raw[key])
    return settings


def combat_difficulty_from_state(combat_state: dict[str, Any] | None) -> dict[str, Any]:
    flags = combat_state.get('flags') if isinstance(combat_state, dict) and isinstance(combat_state.get('flags'), dict) else {}
    return normalize_combat_difficulty_ai(flags.get('combatDifficultyAI') or flags.get('combat_difficulty_ai'))
