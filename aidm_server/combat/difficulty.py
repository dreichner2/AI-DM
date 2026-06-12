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
    'allowFreeformEnemyTactics': True,
    'allowBossWarmPlanner': True,
    'maxLlmCallsPerRound': 3,
    'sentientSelectorMinCandidates': 2,
    'skipLlmWhenTopCandidateMarginExceeds': 0.25,
    'forceSentientEnemyBrain': False,
    'forceFreeformEnemyTactics': False,
    'allowDeterministicCandidateMatcher': True,
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
        'allowFreeformEnemyTactics': False,
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
        'allowFreeformEnemyTactics',
        'allowBossWarmPlanner',
        'forceSentientEnemyBrain',
        'forceFreeformEnemyTactics',
        'allowDeterministicCandidateMatcher',
    ):
        if key in raw:
            settings[key] = bool(raw[key])
    for key in ('maxLlmCallsPerRound', 'sentientSelectorMinCandidates'):
        if key in raw:
            try:
                settings[key] = max(0, int(raw[key]))
            except (TypeError, ValueError):
                pass
    if 'skipLlmWhenTopCandidateMarginExceeds' in raw:
        try:
            settings['skipLlmWhenTopCandidateMarginExceeds'] = max(0.0, float(raw['skipLlmWhenTopCandidateMarginExceeds']))
        except (TypeError, ValueError):
            pass
    return settings


def combat_difficulty_from_state(combat_state: dict[str, Any] | None) -> dict[str, Any]:
    flags = combat_state.get('flags') if isinstance(combat_state, dict) and isinstance(combat_state.get('flags'), dict) else {}
    return normalize_combat_difficulty_ai(flags.get('combatDifficultyAI') or flags.get('combat_difficulty_ai'))
