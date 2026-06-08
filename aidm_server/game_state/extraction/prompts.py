from __future__ import annotations

import json
from typing import Any


PRE_DM_SYSTEM_MESSAGE = (
    'You are a state action extraction assistant for an AI tabletop RPG. '
    'Extract only the player declared actions. Return JSON only. '
    'Do not narrate, decide success, validate inventory, mutate state, invent items, or award rewards.'
)

POST_DM_SYSTEM_MESSAGE = (
    'You are a state outcome extraction assistant for an AI tabletop RPG. '
    'Extract only concrete state changes explicitly stated or unambiguously implied by the DM response. '
    'Return JSON only. Do not narrate, validate, apply, invent, or duplicate already-applied changes.'
)


def build_pre_dm_prompt(*, current_state: dict[str, Any], player_message: str, recent_timeline: list[dict[str, Any]]) -> str:
    return (
        'Return JSON with key declaredActions, where each action has id, type, actorId, confidence, '
        'sourceText, requiresDMResolution, and type-specific fields.\n'
        'Allowed types: inventory.consume, inventory.use, inventory.transfer, currency.transfer, combat.attack, generic.intent.\n\n'
        f'Current state:\n{json.dumps(current_state, separators=(",", ":"))}\n\n'
        f'Recent timeline:\n{json.dumps(recent_timeline[-5:], separators=(",", ":"))}\n\n'
        f'Player message:\n{player_message}\n\n'
        'Extract declared player actions.'
    )


def build_post_dm_prompt(
    *,
    state_before_dm: dict[str, Any],
    player_message: str,
    validated_actions: dict[str, Any],
    already_applied_changes: list[dict[str, Any]],
    dm_response: str,
    recent_timeline: list[dict[str, Any]],
) -> str:
    return (
        'Return JSON with keys proposedChanges, uncertainChanges, notes. '
        'Allowed proposedChanges types: inventory.add, inventory.remove, currency.add, currency.remove, health.heal, health.damage.\n\n'
        f'State before DM:\n{json.dumps(state_before_dm, separators=(",", ":"))}\n\n'
        f'Player message:\n{player_message}\n\n'
        f'Validated pre-DM actions:\n{json.dumps(validated_actions, separators=(",", ":"))}\n\n'
        f'Changes already applied:\n{json.dumps(already_applied_changes, separators=(",", ":"))}\n\n'
        f'Recent timeline:\n{json.dumps(recent_timeline[-5:], separators=(",", ":"))}\n\n'
        f'DM response:\n{dm_response}\n\n'
        'Extract proposed state changes.'
    )

