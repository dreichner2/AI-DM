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
        'For inventory.consume, inventory.use, and inventory.transfer, include quantity; use 1 when exactly one item is indicated by context. '
        'For currency.transfer, include amount and currency using key "currency" with one of pp, gp, ep, sp, cp.\n\n'
        'For transfer actions, include fromActorId when known and toActorId or toActorName. Do not invent recipients.\n\n'
        'For generic.intent, include summary with the concrete object/action the player described. '
        'If the player tries to pick up, grab, take, or collect something, preserve the object description in summary.\n\n'
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
        'Allowed proposedChanges types: inventory.add, inventory.remove, inventory.transfer, currency.add, currency.remove, currency.transfer, health.heal, health.damage, xp.add, xp.remove.\n\n'
        'For every inventory.add, inventory.remove, and inventory.transfer, include quantity; use 1 when exactly one item is indicated by context. '
        'For inventory.add, provide item as an object with name, quantity, and numeric weight in pounds when the item is physical. Do not return item as a bare string. '
        'If exact weight is not stated, infer a reasonable game weight from the item and context.\n\n'
        'For inventory.remove and inventory.transfer, include itemName or itemId.\n\n'
        'For currency.add, currency.remove, and currency.transfer, include amount and currency using key "currency" with one of pp, gp, ep, sp, cp. '
        'For transfer changes, include the source actor as actorId/fromActorId and the recipient as toActorId or toActorName.\n\n'
        'For XP changes, use xp.add or xp.remove with positive integer amount.\n\n'
        f'State before DM:\n{json.dumps(state_before_dm, separators=(",", ":"))}\n\n'
        f'Player message:\n{player_message}\n\n'
        f'Validated pre-DM actions:\n{json.dumps(validated_actions, separators=(",", ":"))}\n\n'
        f'Changes already applied:\n{json.dumps(already_applied_changes, separators=(",", ":"))}\n\n'
        f'Recent timeline:\n{json.dumps(recent_timeline[-5:], separators=(",", ":"))}\n\n'
        f'DM response:\n{dm_response}\n\n'
        'Extract proposed state changes.'
    )
