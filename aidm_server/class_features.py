"""Class-derived passive rules installed through the combat registries.

Only the primary class grants these features on a multiclass sheet. The
providers are intentionally pure: combat remains responsible for applying the
result and tracking turn economy.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from aidm_server.canon_text import int_or_default
from aidm_server.character_progression import class_archetype
from aidm_server.combat.modifiers import (
    INCAPACITATING_CONDITIONS,
    condition_keys,
    register_attacks_per_action_provider,
    register_modifier_provider,
)


EXTRA_ATTACK_CLASSES = frozenset({'barbarian', 'fighter', 'monk', 'paladin', 'ranger'})


def primary_class(value: Any) -> str | None:
    """Resolve only the first class on a multiclass label."""

    label = re.split(r'[/+,&]|\band\b', str(value or ''), maxsplit=1, flags=re.IGNORECASE)[0]
    return class_archetype(label)


def extra_attack_provider(participant: Mapping[str, Any], _combat: Mapping[str, Any] | None) -> int | None:
    if primary_class(participant.get('class')) in EXTRA_ATTACK_CLASSES and int_or_default(participant.get('level'), default=1) >= 5:
        return 2
    return None


def danger_sense_provider(
    participant: Mapping[str, Any], role: str, context: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        role != 'saver'
        or context.get('ability') != 'dexterity'
        or primary_class(participant.get('class')) != 'barbarian'
        or int_or_default(participant.get('level'), default=1) < 2
        or condition_keys(participant) & (INCAPACITATING_CONDITIONS | {'blinded', 'deafened', 'dead'})
    ):
        return None
    return {'mode': 'advantage', 'reasons': ['Danger Sense']}


def register_class_feature_providers() -> None:
    """Register the providers once; registries ignore repeated registration."""

    register_attacks_per_action_provider(extra_attack_provider)
    register_modifier_provider(danger_sense_provider)
