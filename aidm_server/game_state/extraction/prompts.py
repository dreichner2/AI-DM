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
        'Allowed types: inventory.consume, inventory.use, inventory.equip, inventory.unequip, inventory.transfer, currency.transfer, combat.attack, generic.intent.\n\n'
        'For inventory.consume, inventory.use, and inventory.transfer, include quantity; use 1 when exactly one item is indicated by context. '
        'For inventory.equip and inventory.unequip, include itemName or itemId and include slot only when the player explicitly names a slot such as main_hand, off_hand, helmet, hood, body_armor, clothing, or underwear. '
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
    allowed_types = (
        'inventory.add, inventory.remove, inventory.transfer, inventory.equip, inventory.unequip, currency.add, currency.remove, currency.transfer, '
        'health.heal, health.damage, xp.add, xp.remove, spell.learn, scene.update, scene.move_location, '
        'location.discover, location.update, location.connect, quest.add, quest.update, '
        'quest.objective.add, quest.objective.update, quest.complete, quest.fail, '
        'npc.discover, npc.update, npc.move, npc.relationship.update, flag.set, flag.unset, '
        'combat.update, combat.round.advance, combat.battlefield.update, combat.participant.update, combat.move, '
        'combat.condition.add, combat.condition.remove, combat.ability.mark_used, combat.morale.update, combat.morale.event, combat.end'
    )
    return (
        'Return JSON with keys proposedChanges, uncertainChanges, notes. '
        f'Allowed proposedChanges types: {allowed_types}.\n\n'
        'For every inventory.add, inventory.remove, and inventory.transfer, include quantity; use 1 when exactly one item is indicated by context. '
        'For inventory.add, provide item as an object with name, quantity, and numeric weight in pounds when the item is physical. Do not return item as a bare string. '
        'Only use inventory.add when the DM response says the character actually takes, receives, buys, loots, pockets, claims, or picks up the item; merely seeing, spotting, finding, or noticing an item in the scene is not an inventory gain. '
        'If exact weight is not stated, infer a reasonable game weight from the item and context.\n\n'
        'For inventory.remove and inventory.transfer, include itemName or itemId.\n\n'
        'For inventory.equip and inventory.unequip, include itemName or itemId. Use equip when the DM response confirms gear is equipped, worn, donned, wielded, readied, or strapped on. Use unequip when gear is removed, taken off, doffed, stowed, sheathed, or put away. Do not emit equip/unequip for flavor-only mentions.\n\n'
        'For currency.add, currency.remove, and currency.transfer, include amount and currency using key "currency" with one of pp, gp, ep, sp, cp. '
        'For transfer changes, include the source actor as actorId/fromActorId and the recipient as toActorId or toActorName.\n\n'
        'For XP changes, use xp.add or xp.remove with positive integer amount.\n\n'
        'For spell.learn, include actorId and spellName. Magic is not limited to D&D, Pathfinder, or any official spell list; preserve invented spell names and magical techniques when the DM response confirms them. Use spell.learn when the DM response confirms a character learns, copies, reads, is taught, unlocks, or masters a spell, cantrip, ritual, magical technique, shapeshifting form, or race/class magic. Include spellLevel when known and learnedFrom when a book, teacher, item, or source is named.\n\n'
        'For scene.update, include only persistent scene fields that clearly changed: locationId, name, sceneType, dangerLevel, mood, combatState, description, activeNpcIds, activeQuestIds, playerPositions, playerZones, characterPositions, characterZones, musicTag. '
        'Use playerPositions/playerZones or characterPositions/characterZones when the response clearly separates characters by zone, room, inside/outside boundary, line of sight, or reachability. '
        'Update dangerLevel whenever immediate scene danger clearly rises or falls; use 0 for safe/calm, 5 for meaningful threat, and 8-10 for active combat or lethal danger. '
        'sceneType must be one of social, exploration, travel, combat, dungeon, rest, mystery, shopping, dialogue. '
        'mood must be one of calm, tense, eerie, heroic, sad, mysterious, dangerous. '
        'combatState must be one of none, pending, active, resolved.\n\n'
        'For scene.move_location, include locationId when known and name for the destination. Include dangerLevel, description, and activeNpcIds when the destination establishes them; omitted scene-local values will be reset for the new scene. This should only be used when the response says the party arrives, enters, leaves for, or otherwise actually moves to the place.\n\n'
        'For location.discover and location.update, use locationId plus name. If no id is stated, provide a stable slug in locationId. '
        'Use locationType for the location category, not type, with one of tavern, town, dungeon, forest, road, shop, castle, ruins, cave, wilderness, other. '
        'Use status only when clearly known: known, discovered, visited, hidden, inaccessible. Do not create locations for throwaway flavor mentions.\n\n'
        'For quests, use questId plus title. For quest.add include summary/stage/objectives when stated. '
        'Quest status must be available, active, completed, failed, abandoned, or hidden. '
        'Objectives have id, description, and status open, completed, failed, or optional. '
        'Only use quest.complete or quest.fail when completion or failure is clearly confirmed by the DM response.\n\n'
        'For NPCs, use npcId plus name. npc.discover is for newly known or directly introduced NPCs; npc.update is for known NPC facts. '
        'Never create or update an NPC record for a player character listed in playerCharacters; player characters are not NPCs. '
        'Include race when the DM clearly states an NPC race, species, ancestry, or kind; do not guess race from name, role, or vibe. '
        'NPC disposition must be friendly, neutral, hostile, suspicious, afraid, loyal, or unknown. '
        'NPC status must be known, met, allied, hostile, dead, missing, or unknown. '
        'Use memory for short stable NPC memories that the app should persist.\n\n'
        'For flags, use flagKey and flagValue for flag.set; use flagKey for flag.unset.\n\n'
        'For combat changes, use participantId for the affected combat participant. '
        'Use combat.participant.update for HP changes, defeat, fleeing, surrender, consciousness, or full participant replacement; include hp.current/hp.max when HP changes. '
        'Use combat.condition.add/remove for named conditions. Use combat.move with toRangeBand for range-band movement. '
        'Use combat.ability.mark_used only when a creature ability was actually spent. Use combat.morale.update/event only for explicit morale changes or supported morale events. '
        'Use combat.end only when the response clearly ends the fight, and do not duplicate combat changes already applied before narration.\n\n'
        'Extract world/story changes only when the DM response clearly states them. Avoid speculative, conditional, hypothetical, or purely flavor-only mentions. '
        'Do not duplicate already-applied changes. The app validates and applies changes; you only propose structured changes.\n\n'
        f'State before DM:\n{json.dumps(state_before_dm, separators=(",", ":"))}\n\n'
        f'Player message:\n{player_message}\n\n'
        f'Validated pre-DM actions:\n{json.dumps(validated_actions, separators=(",", ":"))}\n\n'
        f'Changes already applied:\n{json.dumps(already_applied_changes, separators=(",", ":"))}\n\n'
        f'Recent timeline:\n{json.dumps(recent_timeline[-5:], separators=(",", ":"))}\n\n'
        f'DM response:\n{dm_response}\n\n'
        'Extract proposed state changes.'
    )
