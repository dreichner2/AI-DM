from __future__ import annotations

from copy import deepcopy
import re
import secrets
from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.class_capabilities import resolve_capability_use
from aidm_server.character_resources import spell_cast_legality
from aidm_server.game_state.equipment import (
    conflict_items,
    equipment_slot_label,
    infer_equipment_slot,
    is_equippable,
)
from aidm_server.game_state.action_types import PRE_DM_ACTION_TYPES
from aidm_server.game_state.change_types import COMBAT_STATE_CHANGE_TYPES, CURRENCY_TYPES, PHASE_1_STATE_CHANGE_TYPES, STATE_CHANGE_TYPES, WORLD_STATE_CHANGE_TYPES
from aidm_server.game_state.models import (
    actor_currency,
    actor_items,
    actor_name,
    find_actor,
    find_actor_by_name,
    normalize_item_name,
    stable_item_id,
    stable_item_instance_id,
    stable_slug,
    stable_change_id,
    state_applied_change_ids,
)
from aidm_server.combat.morale import MORALE_EVENTS, apply_morale_event
from aidm_server.combat.state import RANGE_BANDS, normalize_battlefield, normalize_combat_state, normalize_participant
from aidm_server.spell_effects import resolve_targeted_spell
from aidm_server.game_state.campaign_pack_encounters import materialize_campaign_pack_combat_start
from aidm_server.game_state.validation.inventory_validator import resolve_inventory_item_reference
from aidm_server.game_state.quest_engine import quest_completion_blocker, quest_failure_blocker
from aidm_server.interactables import resolve_interactable_action
from aidm_server.spellbook import class_spell_archetype, normalize_spellbook, spell_from_change


CONSUMABLE_TYPES = {'consumable', 'potion', 'food'}
UNRESOLVED_TARGET_LABELS = {'', 'target', 'someone', 'somebody', 'an npc', 'a npc', 'npc'}
SPELLCASTING_ABILITY_BY_ARCHETYPE = {
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
GENERIC_EXTRACTED_REASON = 'Extracted from DM response.'
TRANSFER_STATE_CHANGE_TYPES = {'inventory.transfer', 'currency.transfer'}
PLAYER_OWNED_STATE_CHANGE_TYPES = PHASE_1_STATE_CHANGE_TYPES - TRANSFER_STATE_CHANGE_TYPES
PLAYER_COMBAT_PARTICIPANT_CHANGE_TYPES = {
    'combat.participant.update',
    'combat.move',
    'combat.condition.add',
    'combat.condition.remove',
    'combat.ability.mark_used',
}
AUTHORIZED_CROSS_ACTOR_BYPASS_CHANGE_TYPES = {'health.heal', 'xp.add'}
SCENE_TYPES = {'social', 'exploration', 'travel', 'combat', 'dungeon', 'rest', 'mystery', 'shopping', 'dialogue'}
SCENE_MOODS = {'calm', 'tense', 'eerie', 'heroic', 'sad', 'mysterious', 'dangerous'}
COMBAT_STATES = {'none', 'pending', 'active', 'resolved'}
LOCATION_TYPES = {'tavern', 'town', 'dungeon', 'forest', 'road', 'shop', 'castle', 'ruins', 'cave', 'wilderness', 'other'}
LOCATION_STATUSES = {'known', 'discovered', 'visited', 'hidden', 'inaccessible'}
GENERIC_RANGED_WEAPON_NAMES = {'ranged weapon', 'ranged attack', 'ranged'}
RANGED_WEAPON_LABELS = {'ranged', 'bow', 'longbow', 'shortbow', 'crossbow', 'sling'}
QUEST_STATUSES = {'available', 'active', 'blocked', 'completed', 'failed', 'abandoned', 'hidden'}
OBJECTIVE_STATUSES = {'open', 'blocked', 'completed', 'failed', 'optional'}
TERMINAL_QUEST_UPDATE_STATUSES = {'completed', 'failed'}
TERMINAL_OBJECTIVE_UPDATE_STATUSES = {'completed', 'failed'}
NARRATION_AUTHORITY_SOURCES = {'post_dm', 'narration', 'dm_narration', 'helper', 'heuristic'}
NPC_DISPOSITIONS = {'friendly', 'neutral', 'hostile', 'suspicious', 'afraid', 'loyal', 'unknown'}
NPC_STATUSES = {'known', 'met', 'allied', 'hostile', 'fleeing', 'dead', 'missing', 'unknown'}
NPC_DISPOSITION_ALIASES = {
    'cautious': 'suspicious',
    'fearful': 'afraid',
    'grateful': 'friendly',
    'helpful': 'friendly',
    'hopeful': 'friendly',
    'scared': 'afraid',
    'terrified': 'afraid',
    'wary': 'suspicious',
}
NPC_STATUS_ALIASES = {
    'alive': 'known',
    'bleeding': 'known',
    'down': 'known',
    'dying': 'known',
    'escaped': 'missing',
    'fled': 'missing',
    'injured': 'known',
    'present': 'known',
    'unconscious': 'known',
    'wounded': 'known',
}
PACK_CONTENT_CHANGE_CONFIG = {
    'clue.discover': {
        'catalog_key': 'clues',
        'collection_key': 'clues',
        'embedded_key': 'clue',
        'id_key': 'clueId',
        'label': 'clue',
        'default_status': 'discovered',
    },
    'clue.update': {
        'catalog_key': 'clues',
        'collection_key': 'clues',
        'embedded_key': 'clue',
        'id_key': 'clueId',
        'label': 'clue',
        'default_status': 'known',
    },
    'faction.discover': {
        'catalog_key': 'factions',
        'collection_key': 'factions',
        'embedded_key': 'faction',
        'id_key': 'factionId',
        'label': 'faction',
        'default_status': 'known',
    },
    'faction.relationship.update': {
        'catalog_key': 'factions',
        'collection_key': 'factions',
        'embedded_key': 'faction',
        'id_key': 'factionId',
        'label': 'faction',
        'default_status': 'known',
    },
    'map.reveal': {
        'catalog_key': 'maps',
        'collection_key': 'maps',
        'embedded_key': 'map',
        'id_key': 'mapId',
        'label': 'map',
        'default_status': 'revealed',
    },
    'map.region.update': {
        'catalog_key': 'maps',
        'collection_key': 'maps',
        'embedded_key': 'map',
        'id_key': 'mapId',
        'label': 'map',
        'default_status': 'known',
    },
    'handout.reveal': {
        'catalog_key': 'handouts',
        'collection_key': 'handouts',
        'embedded_key': 'handout',
        'id_key': 'handoutId',
        'label': 'handout',
        'default_status': 'revealed',
    },
    'lore.unlock': {
        'catalog_key': 'lore',
        'collection_key': 'lore',
        'embedded_key': 'lore',
        'id_key': 'loreId',
        'label': 'lore',
        'default_status': 'unlocked',
    },
}


def _action_value(action: dict[str, Any], camel_key: str, snake_key: str | None = None, default=None):
    if camel_key in action:
        return action.get(camel_key)
    if snake_key and snake_key in action:
        return action.get(snake_key)
    return default


def _actor_ref(value: Any) -> str:
    return str(value or '').strip()


def _actor_matches_expected(actor_id: Any, expected_actor_id: str | None) -> bool:
    expected = _actor_ref(expected_actor_id)
    if not expected:
        return True
    actor = _actor_ref(actor_id)
    return bool(actor and actor == expected)


def _declared_action_actor_error(action: dict[str, Any], action_type: str, expected_actor_id: str | None) -> str | None:
    expected = _actor_ref(expected_actor_id)
    if not expected:
        return None

    actor_id = _action_value(action, 'actorId', 'actor_id')
    if not _actor_matches_expected(actor_id, expected):
        return 'Declared action actor does not match the current player.'

    if action_type in TRANSFER_STATE_CHANGE_TYPES:
        source_actor_id = _action_value(action, 'fromActorId', 'from_actor_id') or actor_id
        if not _actor_matches_expected(source_actor_id, expected):
            return 'Transfer source actor does not match the current player.'
    return None


def _target_actor_from_payload(state: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    target_id = (
        _action_value(payload, 'toActorId', 'to_actor_id')
        or _action_value(payload, 'targetActorId', 'target_actor_id')
        or _action_value(payload, 'targetId', 'target_id')
    )
    if target_id:
        target = find_actor(state, target_id)
        if target:
            return target, ''
        return None, f"Target actor '{target_id}' was not found."

    target_name = (
        _action_value(payload, 'toActorName', 'to_actor_name')
        or _action_value(payload, 'targetActorName', 'target_actor_name')
        or _action_value(payload, 'targetName', 'target_name')
    )
    normalized_target_name = normalize_item_name(target_name)
    if normalized_target_name in UNRESOLVED_TARGET_LABELS:
        return None, 'Transfer target is missing.'
    target = find_actor_by_name(state, target_name)
    if target:
        return target, ''
    return None, f"Target actor '{target_name}' was not found."


def _target_actor_name_from_payload(payload: dict[str, Any]) -> str:
    target_name = (
        _action_value(payload, 'toActorName', 'to_actor_name')
        or _action_value(payload, 'targetActorName', 'target_actor_name')
        or _action_value(payload, 'targetName', 'target_name')
    )
    return str(target_name or '').strip()


def _has_named_untracked_target(payload: dict[str, Any]) -> bool:
    target_name = _target_actor_name_from_payload(payload)
    return bool(target_name and normalize_item_name(target_name) not in UNRESOLVED_TARGET_LABELS)


def _invalid(action: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        'actionId': action.get('id'),
        'status': 'invalid',
        'originalAction': action,
        'reason': reason,
    }


def _valid(
    action: dict[str, Any],
    reason: str,
    *,
    normalized_action: dict[str, Any] | None = None,
    immediate_changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        'actionId': action.get('id'),
        'status': 'valid',
        'originalAction': action,
        'normalizedAction': normalized_action or {},
        'reason': reason,
        'immediateChanges': immediate_changes or [],
    }


def _pending(
    action: dict[str, Any],
    reason: str,
    *,
    normalized_action: dict[str, Any] | None = None,
    required_rolls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        'actionId': action.get('id'),
        'status': 'pending',
        'originalAction': action,
        'normalizedAction': normalized_action or {},
        'reason': reason,
        'requiredRolls': required_rolls or [],
    }


def _clarification(action: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
    return {
        'actionId': action.get('id'),
        'status': 'needs_clarification',
        'originalAction': action,
        'reason': resolution.get('reason') or 'Item reference is ambiguous.',
        'clarificationRequest': {
            'type': 'item_resolution',
            'prompt': resolution.get('query') or 'Which item do you use?',
            'originalAction': action,
            'options': resolution.get('options') or [],
        },
    }


def _resolve_action_item(
    *,
    action: dict[str, Any],
    state: dict[str, Any],
    item_name: str,
    requested_type: str | None = None,
    requested_subtype: str | None = None,
    current_turn: int = 0,
    recent_context: list[str] | None = None,
    selected_item_id: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    actor = find_actor(state, _action_value(action, 'actorId', 'actor_id'))
    if not actor:
        return None, None, {'status': 'missing', 'reason': 'Actor not found.', 'searchedName': item_name}
    metadata = actor.get('metadata') if isinstance(actor.get('metadata'), dict) else {}
    resolution = resolve_inventory_item_reference(
        actor_inventory=actor_items(actor),
        requested_name=item_name,
        requested_type=requested_type,
        requested_subtype=requested_subtype,
        current_turn=current_turn,
        recent_context=recent_context or [],
        default_item_id=metadata.get('defaultWeaponId') or metadata.get('default_weapon_id'),
        selected_item_id=selected_item_id,
    )
    item = None
    if resolution.get('status') == 'resolved':
        item = next((candidate for candidate in actor_items(actor) if candidate.get('id') == resolution.get('itemId')), None)
    return actor, item, resolution


def _is_ranged_weapon(item: dict[str, Any]) -> bool:
    if normalize_item_name(item.get('type')) != 'weapon':
        return False
    labels = {
        normalize_item_name(item.get('name')),
        normalize_item_name(item.get('subtype')),
        *[normalize_item_name(alias) for alias in item.get('aliases') or []],
        *[normalize_item_name(tag) for tag in item.get('tags') or []],
    }
    labels = {label for label in labels if label}
    return any(label in RANGED_WEAPON_LABELS or 'bow' in label or 'ranged' in label for label in labels)


def _resolve_generic_ranged_attack_weapon(actor: dict[str, Any], weapon_name: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    ranged_weapons = [item for item in actor_items(actor) if _is_ranged_weapon(item)]
    equipped = [item for item in ranged_weapons if item.get('equipped')]
    candidates = equipped or ranged_weapons
    if len(candidates) == 1:
        item = candidates[0]
        return item, {
            'status': 'resolved',
            'itemId': item.get('id'),
            'itemName': item.get('name'),
            'resolutionMethod': 'generic_ranged_weapon',
            'confidence': 0.9,
            'needsClarification': False,
        }
    if len(candidates) > 1:
        return None, {
            'status': 'needs_clarification',
            'reason': f"Multiple ranged weapons match '{weapon_name}'.",
            'query': 'Which ranged weapon do you use?',
            'options': [
                {
                    'itemId': item.get('id'),
                    'label': item.get('name'),
                    'description': 'Equipped' if item.get('equipped') else str(item.get('type') or 'weapon'),
                }
                for item in candidates
            ],
        }
    return None, {
        'status': 'missing',
        'reason': f"No ranged weapon matches '{weapon_name}'.",
        'searchedName': weapon_name,
    }


def _validate_consume_item(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
    recent_context: list[str],
    selected_item_id: str | None,
) -> dict[str, Any]:
    item_name = str(_action_value(action, 'itemName', 'item_name') or '').strip()
    quantity = max(1, int_or_default(action.get('quantity'), default=1))
    actor, item, resolution = _resolve_action_item(
        action=action,
        state=state,
        item_name=item_name,
        current_turn=current_turn,
        recent_context=recent_context,
        selected_item_id=selected_item_id,
    )
    if not actor:
        return _invalid(action, resolution.get('reason') or 'Actor not found.')
    if resolution.get('status') == 'needs_clarification':
        return _clarification(action, resolution)
    if resolution.get('status') == 'missing' or not item:
        return _invalid(action, f"{actor_name(actor)} does not have {item_name or 'that item'}.")
    if int_or_default(item.get('quantity'), default=1) < quantity:
        return _invalid(action, f"Not enough {item.get('name')}. Available: {item.get('quantity')}.")
    item_type = normalize_item_name(item.get('type'))
    item_subtype = normalize_item_name(item.get('subtype'))
    item_labels = {item_type, item_subtype, *[normalize_item_name(tag) for tag in item.get('tags') or []]}
    if not (item_labels & CONSUMABLE_TYPES) and 'potion' not in normalize_item_name(item.get('name')):
        return _invalid(action, f"{item.get('name')} is not consumable.")

    change = {
        'id': stable_change_id(current_turn, 'pre_dm', action.get('id'), 'inventory.remove', item.get('id'), quantity),
        'turnId': current_turn,
        'type': 'inventory.remove',
        'source': 'pre_dm',
        'actorId': actor.get('id'),
        'itemId': item.get('id'),
        'itemName': item.get('name'),
        'quantity': quantity,
        'reason': f"{item.get('name')} consumed.",
        'visible': True,
    }
    return _valid(
        action,
        f"{actor_name(actor)} has {item.get('name')} x{item.get('quantity')}.",
        normalized_action={
            **action,
            'itemId': item.get('id'),
            'itemName': item.get('name'),
            'resolution': resolution,
        },
        immediate_changes=[change],
    )


def _validate_use_or_transfer_item(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
    recent_context: list[str],
    selected_item_id: str | None,
) -> dict[str, Any]:
    item_name = str(_action_value(action, 'itemName', 'item_name') or '').strip()
    quantity = max(1, int_or_default(action.get('quantity'), default=1))
    actor, item, resolution = _resolve_action_item(
        action=action,
        state=state,
        item_name=item_name,
        current_turn=current_turn,
        recent_context=recent_context,
        selected_item_id=selected_item_id,
    )
    if not actor:
        return _invalid(action, resolution.get('reason') or 'Actor not found.')
    if resolution.get('status') == 'needs_clarification':
        return _clarification(action, resolution)
    if resolution.get('status') == 'missing' or not item:
        return _invalid(action, f"{actor_name(actor)} does not have {item_name or 'that item'}.")
    if int_or_default(item.get('quantity'), default=1) < quantity:
        return _invalid(action, f"Not enough {item.get('name')}. Available: {item.get('quantity')}.")
    return _pending(
        action,
        f"{actor_name(actor)} has {item.get('name')} x{item.get('quantity')}.",
        normalized_action={
            **action,
            'itemId': item.get('id'),
            'itemName': item.get('name'),
            'resolution': resolution,
        },
    )


def _validate_inventory_transfer(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
    recent_context: list[str],
    selected_item_id: str | None,
) -> dict[str, Any]:
    item_name = str(_action_value(action, 'itemName', 'item_name') or '').strip()
    quantity = max(1, int_or_default(action.get('quantity'), default=1))
    source_action = {
        **action,
        'actorId': _action_value(action, 'fromActorId', 'from_actor_id') or _action_value(action, 'actorId', 'actor_id'),
    }
    actor, item, resolution = _resolve_action_item(
        action=source_action,
        state=state,
        item_name=item_name,
        current_turn=current_turn,
        recent_context=recent_context,
        selected_item_id=selected_item_id,
    )
    if not actor:
        return _invalid(action, resolution.get('reason') or 'Actor not found.')
    if resolution.get('status') == 'needs_clarification':
        return _clarification(action, resolution)
    if resolution.get('status') == 'missing' or not item:
        return _invalid(action, f"{actor_name(actor)} does not have {item_name or 'that item'}.")
    if int_or_default(item.get('quantity'), default=1) < quantity:
        return _invalid(action, f"Not enough {item.get('name')}. Available: {item.get('quantity')}.")

    target, target_error = _target_actor_from_payload(state, action)
    if not target:
        if _has_named_untracked_target(action):
            target_name = _target_actor_name_from_payload(action)
            return _pending(
                action,
                f"{actor_name(actor)} can offer {item.get('name')} x{quantity} to {target_name}; target is not tracked, so DM must resolve the exchange.",
                normalized_action={
                    **action,
                    'fromActorId': actor.get('id'),
                    'toActorName': target_name,
                    'itemId': item.get('id'),
                    'itemName': item.get('name'),
                    'quantity': quantity,
                    'resolution': resolution,
                    'untrackedTarget': True,
                },
            )
        return _invalid(action, target_error or 'Transfer target was not found.')
    if str(target.get('id')) == str(actor.get('id')):
        return _invalid(action, 'Transfer target must be different from the source actor.')

    return _pending(
        action,
        f"{actor_name(actor)} can give {item.get('name')} x{quantity} to {actor_name(target)}.",
        normalized_action={
            **action,
            'fromActorId': actor.get('id'),
            'toActorId': target.get('id'),
            'toActorName': actor_name(target),
            'itemId': item.get('id'),
            'itemName': item.get('name'),
            'quantity': quantity,
            'resolution': resolution,
        },
    )


def _validate_equipment_action(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
    recent_context: list[str],
    selected_item_id: str | None,
    equip: bool,
) -> dict[str, Any]:
    item_name = str(_action_value(action, 'itemName', 'item_name') or '').strip()
    actor, item, resolution = _resolve_action_item(
        action=action,
        state=state,
        item_name=item_name,
        current_turn=current_turn,
        recent_context=recent_context,
        selected_item_id=selected_item_id,
    )
    if not actor:
        return _invalid(action, resolution.get('reason') or 'Actor not found.')
    if resolution.get('status') == 'needs_clarification':
        return _clarification(action, resolution)
    if resolution.get('status') == 'missing' or not item:
        return _invalid(action, f"{actor_name(actor)} does not have {item_name or 'that item'}.")

    if not equip:
        if not item.get('equipped'):
            return _valid(
                action,
                f"{item.get('name')} is already unequipped.",
                normalized_action={**action, 'itemId': item.get('id'), 'itemName': item.get('name'), 'resolution': resolution},
            )
        change = {
            'id': stable_change_id(current_turn, 'pre_dm', action.get('id'), 'inventory.unequip', item.get('id')),
            'turnId': current_turn,
            'type': 'inventory.unequip',
            'source': 'pre_dm',
            'actorId': actor.get('id'),
            'itemId': item.get('id'),
            'itemName': item.get('name'),
            'reason': f"{actor_name(actor)} unequipped {item.get('name')}.",
            'visible': False,
        }
        return _valid(
            action,
            f"{actor_name(actor)} can unequip {item.get('name')}.",
            normalized_action={**action, 'itemId': item.get('id'), 'itemName': item.get('name'), 'resolution': resolution},
            immediate_changes=[change],
        )

    if not is_equippable(item):
        return _invalid(action, f"{item.get('name')} is not equippable.")
    slot = infer_equipment_slot(
        item,
        requested_slot=_action_value(action, 'slot', 'equipment_slot'),
        equipped_items=actor_items(actor),
    )
    if not slot:
        return _invalid(action, f"{item.get('name')} does not have an equipment slot.")
    if item.get('equipped') and str(item.get('slot') or '') == slot:
        return _valid(
            action,
            f"{item.get('name')} is already equipped in {equipment_slot_label(slot)}.",
            normalized_action={
                **action,
                'itemId': item.get('id'),
                'itemName': item.get('name'),
                'slot': slot,
                'resolution': resolution,
            },
        )
    conflicts = conflict_items(actor_items(actor), item, slot)
    change = {
        'id': stable_change_id(current_turn, 'pre_dm', action.get('id'), 'inventory.equip', item.get('id'), slot),
        'turnId': current_turn,
        'type': 'inventory.equip',
        'source': 'pre_dm',
        'actorId': actor.get('id'),
        'itemId': item.get('id'),
        'itemName': item.get('name'),
        'slot': slot,
        'conflictItemIds': [conflict.get('id') for conflict in conflicts if conflict.get('id')],
        'conflictItemNames': [conflict.get('name') for conflict in conflicts if conflict.get('name')],
        'reason': f"{actor_name(actor)} equipped {item.get('name')} in {equipment_slot_label(slot)}.",
        'visible': False,
    }
    return _valid(
        action,
        f"{actor_name(actor)} can equip {item.get('name')} in {equipment_slot_label(slot)}.",
        normalized_action={
            **action,
            'itemId': item.get('id'),
            'itemName': item.get('name'),
            'slot': slot,
            'resolution': resolution,
        },
        immediate_changes=[change],
    )


def _validate_currency_transfer(action: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    actor = find_actor(state, _action_value(action, 'fromActorId', 'from_actor_id') or _action_value(action, 'actorId', 'actor_id'))
    if not actor:
        return _invalid(action, 'Actor not found.')
    currency = str(action.get('currency') or '').strip().lower()
    amount = max(0, int_or_default(action.get('amount'), default=0))
    if currency not in CURRENCY_TYPES or amount <= 0:
        return _invalid(action, 'Currency transfer requires a positive amount and valid denomination.')
    available = actor_currency(actor).get(currency, 0)
    if amount > available:
        return _invalid(action, f"{actor_name(actor)} has {available} {currency} and cannot transfer {amount}.")
    target, target_error = _target_actor_from_payload(state, action)
    if not target:
        if _has_named_untracked_target(action):
            target_name = _target_actor_name_from_payload(action)
            return _pending(
                action,
                f"{actor_name(actor)} can offer {amount} {currency} to {target_name}; target is not tracked, so DM must resolve the exchange.",
                normalized_action={
                    **action,
                    'fromActorId': actor.get('id'),
                    'toActorName': target_name,
                    'amount': amount,
                    'currency': currency,
                    'untrackedTarget': True,
                },
            )
        return _invalid(action, target_error or 'Transfer target was not found.')
    if str(target.get('id')) == str(actor.get('id')):
        return _invalid(action, 'Transfer target must be different from the source actor.')
    return _pending(
        action,
        f"{actor_name(actor)} can give {amount} {currency} to {actor_name(target)}.",
        normalized_action={
            **action,
            'fromActorId': actor.get('id'),
            'toActorId': target.get('id'),
            'toActorName': actor_name(target),
            'amount': amount,
            'currency': currency,
        },
    )


def _validate_scene_item_pickup(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
) -> dict[str, Any]:
    actor = find_actor(state, _action_value(action, 'actorId', 'actor_id'))
    if not actor:
        return _invalid(action, 'Actor not found.')
    requested_id = _text(_action_value(action, 'itemId', 'item_id'))
    requested_name = normalize_item_name(_action_value(action, 'itemName', 'item_name'))
    scene_items = _scene_items(state)
    if requested_id:
        matches = [item for item in scene_items if _text(item.get('id')) == requested_id]
        if not matches:
            return _invalid(action, f"Scene item '{requested_id}' is stale or no longer present.")
    else:
        matches = [item for item in scene_items if normalize_item_name(item.get('name')) == requested_name]
        if len(matches) > 1:
            return _clarification(
                action,
                {
                    'status': 'needs_clarification',
                    'reason': f"Multiple scene items match '{_action_value(action, 'itemName', 'item_name')}'.",
                    'query': 'Which exact scene item do you pick up?',
                    'options': [
                        {
                            'itemId': item.get('id'),
                            'label': item.get('name'),
                            'description': str(item.get('description') or item.get('type') or 'scene item'),
                        }
                        for item in matches
                    ],
                },
            )
        if not matches:
            return _invalid(action, f"No scene item matches '{_action_value(action, 'itemName', 'item_name') or 'that item'}'.")
    item = matches[0]
    item_id = _text(item.get('id'))
    if item_id and any(
        _text(owned.get('id')) == item_id
        for candidate in _records(state, 'playerCharacters')
        for owned in actor_items(candidate)
    ):
        return _invalid(action, f"Item identity '{item_id}' already exists in an inventory.")
    quantity = max(1, int_or_default(action.get('quantity'), default=1))
    available = max(1, int_or_default(item.get('quantity'), default=1))
    if quantity > available:
        return _invalid(action, f"Not enough {item.get('name')} in the scene. Available: {available}.")
    transfer_id = stable_change_id(current_turn, 'pre_dm', action.get('id'), 'scene.item.pickup', item_id, quantity)
    moved_item_id = (
        _split_stack_item_id(
            transfer_id=transfer_id,
            source_item_id=item_id,
            destination_id=_text(actor.get('id')),
        )
        if quantity < available
        else item_id
    )
    moved_item = {**item, 'id': moved_item_id, 'quantity': quantity}
    if moved_item_id != item_id:
        moved_item['splitFromItemId'] = item_id
    remove_change = {
        'id': f'{transfer_id}:scene_remove',
        'turnId': current_turn,
        'type': 'scene.item.remove',
        'source': 'pre_dm_world_action',
        'actorId': actor.get('id'),
        'itemId': item_id,
        'itemName': item.get('name'),
        'quantity': quantity,
        'item': {**item, 'quantity': quantity},
        'transferId': transfer_id,
        'reason': f"{actor_name(actor)} picked up {item.get('name')}.",
        'visible': False,
    }
    add_change = {
        'id': f'{transfer_id}:inventory_add',
        'turnId': current_turn,
        'type': 'inventory.add',
        'source': 'pre_dm_world_action',
        'actorId': actor.get('id'),
        'itemId': moved_item_id,
        'itemName': item.get('name'),
        'quantity': quantity,
        'item': moved_item,
        **({'sourceItemId': item_id, 'splitFromItemId': item_id} if moved_item_id != item_id else {}),
        'transferId': transfer_id,
        'reason': f"{actor_name(actor)} picked up {item.get('name')}.",
        'visible': False,
    }
    return _valid(
        action,
        f"{actor_name(actor)} can pick up the exact scene item {item.get('name')}.",
        normalized_action={**action, 'itemId': item_id, 'itemName': item.get('name')},
        immediate_changes=[remove_change, add_change],
    )


def _validate_scene_item_drop(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
    recent_context: list[str],
    selected_item_id: str | None,
) -> dict[str, Any]:
    item_name = _text(_action_value(action, 'itemName', 'item_name'))
    actor, item, resolution = _resolve_action_item(
        action=action,
        state=state,
        item_name=item_name,
        current_turn=current_turn,
        recent_context=recent_context,
        selected_item_id=selected_item_id,
    )
    if not actor:
        return _invalid(action, resolution.get('reason') or 'Actor not found.')
    if resolution.get('status') == 'needs_clarification':
        return _clarification(action, resolution)
    if resolution.get('status') == 'missing' or not item:
        return _invalid(action, f"{actor_name(actor)} does not have {item_name or 'that item'}.")
    item_id = _text(item.get('id'))
    if item_id and any(_text(scene_item.get('id')) == item_id for scene_item in _scene_items(state)):
        return _invalid(action, f"Item identity '{item_id}' already exists in the current scene.")
    quantity = max(1, int_or_default(action.get('quantity'), default=1))
    available = max(1, int_or_default(item.get('quantity'), default=1))
    if quantity > available:
        return _invalid(action, f"Not enough {item.get('name')}. Available: {available}.")
    transfer_id = stable_change_id(current_turn, 'pre_dm', action.get('id'), 'scene.item.drop', item_id, quantity)
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    moved_item_id = (
        _split_stack_item_id(
            transfer_id=transfer_id,
            source_item_id=item_id,
            destination_id=_text(scene.get('locationId')) or 'current_scene',
        )
        if quantity < available
        else item_id
    )
    moved_item = {**item, 'id': moved_item_id, 'quantity': quantity, 'equipped': False}
    if moved_item_id != item_id:
        moved_item['splitFromItemId'] = item_id
    remove_change = {
        'id': f'{transfer_id}:inventory_remove',
        'turnId': current_turn,
        'type': 'inventory.remove',
        'source': 'pre_dm_world_action',
        'actorId': actor.get('id'),
        'itemId': item_id,
        'itemName': item.get('name'),
        'quantity': quantity,
        'transferId': transfer_id,
        'reason': f"{actor_name(actor)} dropped {item.get('name')}.",
        'visible': False,
    }
    add_change = {
        'id': f'{transfer_id}:scene_add',
        'turnId': current_turn,
        'type': 'scene.item.add',
        'source': 'pre_dm_world_action',
        'actorId': actor.get('id'),
        'sourceActorId': actor.get('id'),
        'itemId': moved_item_id,
        'itemName': item.get('name'),
        'quantity': quantity,
        'item': moved_item,
        **({'sourceItemId': item_id, 'splitFromItemId': item_id} if moved_item_id != item_id else {}),
        'transferId': transfer_id,
        'reason': f"{actor_name(actor)} dropped {item.get('name')}.",
        'visible': False,
    }
    return _valid(
        action,
        f"{actor_name(actor)} can drop the exact inventory item {item.get('name')}.",
        normalized_action={**action, 'itemId': item_id, 'itemName': item.get('name'), 'resolution': resolution},
        immediate_changes=[remove_change, add_change],
    )


def _validate_world_travel(action: dict[str, Any], state: dict[str, Any], *, current_turn: int) -> dict[str, Any]:
    actor = find_actor(state, _action_value(action, 'actorId', 'actor_id'))
    if not actor:
        return _invalid(action, 'Actor not found.')
    destination_id = _text(_action_value(action, 'locationId', 'location_id'))
    destination = next(
        (location for location in _records(state, 'locations') if _text(location.get('id')) == destination_id),
        None,
    )
    if not destination:
        return _invalid(action, f"Destination '{destination_id or 'unknown'}' is stale, hidden, or undiscovered.")
    if _text(destination.get('status')).lower() in {'hidden', 'inaccessible'}:
        return _invalid(action, f"{destination.get('name') or destination_id} is not currently accessible.")
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    current_id = _text(scene.get('locationId'))
    current = next(
        (location for location in _records(state, 'locations') if _text(location.get('id')) == current_id),
        None,
    )
    if not current:
        return _invalid(action, 'The current location is not tracked, so adjacency cannot be validated.')
    connected = {_text(value) for value in _list(current.get('connectedLocationIds')) if _text(value)}
    if destination_id not in connected:
        return _invalid(action, f"{destination.get('name') or destination_id} is not adjacent to {current.get('name') or current_id}.")
    change = {
        'id': stable_change_id(current_turn, 'pre_dm', action.get('id'), 'world.travel', current_id, destination_id),
        'turnId': current_turn,
        'type': 'scene.move_location',
        'source': 'pre_dm_world_action',
        'actorId': actor.get('id'),
        'locationId': destination_id,
        'name': destination.get('name') or destination_id,
        'sceneType': destination.get('sceneType') or destination.get('type') or 'exploration',
        'reason': f"The party traveled from {current.get('name') or current_id} to {destination.get('name') or destination_id}.",
        'visible': False,
    }
    return _valid(
        action,
        f"{destination.get('name') or destination_id} is an accessible adjacent destination.",
        normalized_action={**action, 'locationId': destination_id, 'locationName': destination.get('name')},
        immediate_changes=[change],
    )


def _interactable_actor_context(state: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    items = actor_items(actor)
    tool_ids = [
        _text(item.get('id'))
        for item in items
        if _text(item.get('id'))
        and (
            _text(item.get('type')).lower() == 'tool'
            or 'tool' in {_text(tag).lower() for tag in (item.get('tags') or [])}
        )
    ]
    capability_ids = [
        _text(capability.get('id'))
        for capability in (actor.get('classFeatures') or [])
        if isinstance(capability, dict) and _text(capability.get('id'))
    ]
    return {
        'id': actor.get('id'),
        'items': deepcopy(items),
        'itemIds': [_text(item.get('id')) for item in items if _text(item.get('id'))],
        'toolIds': tool_ids,
        'capabilities': capability_ids,
        'flags': deepcopy(state.get('flags')) if isinstance(state.get('flags'), dict) else {},
    }


def _interactable_request(
    *,
    action_id: str,
    action: dict[str, Any],
    state: dict[str, Any],
    current_turn: int,
) -> dict[str, Any]:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    request = {
        'actionId': action_id,
        'action': _text(_action_value(action, 'objectAction', 'object_action')).lower(),
        'targetId': _text(_action_value(action, 'targetId', 'target_id')),
        'locationId': _text(scene.get('locationId')),
        'turnId': current_turn,
    }
    expected_revision = _action_value(action, 'expectedRevision', 'expected_revision')
    if expected_revision is not None:
        request['expectedRevision'] = max(0, int_or_default(expected_revision, default=0))
    return request


def _validate_interactable_action(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
) -> dict[str, Any]:
    actor = find_actor(state, _action_value(action, 'actorId', 'actor_id'))
    if not actor:
        return _invalid(action, 'Actor not found.')
    action_id = stable_change_id(
        current_turn,
        'pre_dm',
        action.get('id'),
        'scene.interactable.action',
        actor.get('id'),
        _action_value(action, 'targetId', 'target_id'),
        _action_value(action, 'objectAction', 'object_action'),
    )
    request = _interactable_request(
        action_id=action_id,
        action=action,
        state=state,
        current_turn=current_turn,
    )
    resolved = resolve_interactable_action(
        state,
        request,
        _interactable_actor_context(state, actor),
    )
    if not resolved.get('ok'):
        return _invalid(action, _text(resolved.get('message')) or 'That object action is not legal now.')
    event = resolved.get('events', [None])[0] if resolved.get('events') else None
    change = {
        'id': action_id,
        'turnId': current_turn,
        'type': 'scene.interactable.action',
        'source': 'pre_dm_interactable_action',
        'actorId': actor.get('id'),
        'targetId': request['targetId'],
        'locationId': request['locationId'],
        'objectAction': request['action'],
        **(
            {'expectedRevision': request['expectedRevision']}
            if request.get('expectedRevision') is not None
            else {}
        ),
        'event': deepcopy(event) if isinstance(event, dict) else None,
        'resolutionAuthority': 'interactable_rules_engine',
        'reason': _text(resolved.get('message')),
        'visible': True,
    }
    return _valid(
        action,
        change['reason'],
        normalized_action={
            **action,
            'targetId': request['targetId'],
            'objectAction': request['action'],
            'event': deepcopy(change.get('event')),
        },
        immediate_changes=[change],
    )


def _authoritative_spell_definition(spell: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any] | None:
    if spell.get('authoritativeEffect') is not True:
        return None
    definition = deepcopy(spell)
    delivery = definition.get('delivery') if isinstance(definition.get('delivery'), dict) else {}
    if _text(delivery.get('type')).lower() in {'attack', 'save'}:
        casting_ability = SPELLCASTING_ABILITY_BY_ARCHETYPE.get(
            class_spell_archetype(_text(actor.get('class'))) or ''
        )
        if not casting_ability:
            return None
        delivery = {**delivery, 'castingAbility': casting_ability}
        definition['delivery'] = delivery
    return definition


def _spell_resolution_rolls(resolution: Any) -> list[int]:
    """Flatten trusted rolls in the resolver's exact consumption order."""

    if not isinstance(resolution, dict):
        return []
    rolls: list[int] = []
    for target in resolution.get('targets') or []:
        if not isinstance(target, dict):
            continue
        delivery = target.get('delivery') if isinstance(target.get('delivery'), dict) else {}
        if delivery.get('naturalRoll') is not None:
            rolls.append(max(1, int_or_default(delivery.get('naturalRoll'), default=1)))
        for effect in target.get('effects') or []:
            if not isinstance(effect, dict):
                continue
            roll = effect.get('roll') if isinstance(effect.get('roll'), dict) else {}
            rolls.extend(
                max(1, int_or_default(value, default=1))
                for value in (roll.get('rolls') or [])
            )
    return rolls


def _resolve_spell_effect(
    *,
    state: dict[str, Any],
    actor: dict[str, Any],
    spell: dict[str, Any],
    target_ids: list[str],
    cast_id: str,
    current_turn: int,
    replay_rolls: list[int] | None = None,
) -> dict[str, Any]:
    roll_index = 0

    def _roller(sides: int) -> int:
        nonlocal roll_index
        if replay_rolls is None:
            return secrets.randbelow(max(1, sides)) + 1
        value = replay_rolls[roll_index] if roll_index < len(replay_rolls) else 1
        roll_index += 1
        return max(1, min(max(1, sides), int_or_default(value, default=1)))

    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    return resolve_targeted_spell(
        combat,
        caster_id=_text(actor.get('id')),
        spell=spell,
        target_ids=target_ids,
        cast_id=cast_id,
        roller=_roller,
        caster_resources=(
            actor.get('spellResources')
            if isinstance(actor.get('spellResources'), dict)
            else {}
        ),
        current_turn=current_turn,
    )


def _validate_spell_action(action: dict[str, Any], state: dict[str, Any], *, current_turn: int) -> dict[str, Any]:
    actor = find_actor(state, _action_value(action, 'actorId', 'actor_id'))
    if not actor:
        return _invalid(action, 'Actor not found.')
    spell_name = _text(_action_value(action, 'spellName', 'spell_name'))
    cast_level = _action_value(action, 'castLevel', 'cast_level')
    cast_level = int_or_default(cast_level, default=0) if cast_level is not None else None
    legality = spell_cast_legality(
        actor.get('spellbook'),
        actor.get('spellResources'),
        spell_name_or_id=spell_name,
        class_name=_text(actor.get('class')),
        level=max(1, int_or_default(actor.get('level'), default=1)),
        cast_level=cast_level,
        resource_pool=_text(_action_value(action, 'resourcePool', 'resource_pool')) or 'auto',
    )
    if not legality.get('legal'):
        return _invalid(action, _text(legality.get('reason')) or 'That spell cannot be cast now.')
    spell = legality.get('spell') if isinstance(legality.get('spell'), dict) else {}
    resource = legality.get('resource') if isinstance(legality.get('resource'), dict) else {}
    target_ids = [
        _text(target_id)
        for target_id in (action.get('targetIds') or action.get('target_ids') or [])
        if _text(target_id)
    ]
    authoritative_spell = _authoritative_spell_definition(spell, actor)
    # Legacy clients did not send exact targets. Keep their resource-only cast
    # path compatible; the current typed UI supplies target IDs and therefore
    # receives the full authoritative effect resolution.
    if authoritative_spell and not target_ids:
        authoritative_spell = None
    change = {
        'id': stable_change_id(
            current_turn,
            'pre_dm',
            action.get('id'),
            'spell.cast',
            actor.get('id'),
            spell.get('id') or spell.get('name'),
            resource.get('pool'),
            resource.get('slotLevel'),
            *target_ids,
        ),
        'turnId': current_turn,
        'type': 'spell.cast',
        'source': 'pre_dm_spell_action',
        'actorId': actor.get('id'),
        'spellId': spell.get('id'),
        'spellName': spell.get('name') or spell_name,
        'castLevel': legality.get('castLevel'),
        'resourcePool': resource.get('pool') or 'cantrip',
        'concentration': (
            authoritative_spell.get('concentration') is True
            if authoritative_spell
            else action.get('concentration')
        ),
        'targetIds': target_ids,
        'reason': legality.get('reason'),
        'visible': False,
    }
    if authoritative_spell:
        effect_result = _resolve_spell_effect(
            state=state,
            actor=actor,
            spell=authoritative_spell,
            target_ids=target_ids,
            cast_id=change['id'],
            current_turn=current_turn,
        )
        if not effect_result.get('ok'):
            return _invalid(
                action,
                _text(effect_result.get('reason')) or 'That spell target is not legal now.',
            )
        change.update(
            {
                'resolutionAuthority': 'spell_effect_engine',
                'resolution': deepcopy(effect_result.get('resolution')),
                'visible': True,
            }
        )
    return _valid(
        action,
        f"{actor_name(actor)} can cast {spell.get('name') or spell_name}. {legality.get('reason')}",
        normalized_action={
            **action,
            'spellId': spell.get('id'),
            'spellName': spell.get('name') or spell_name,
            'castLevel': legality.get('castLevel'),
            'resourcePool': resource.get('pool'),
            'targetIds': target_ids,
            **(
                {'resolution': deepcopy(change.get('resolution'))}
                if authoritative_spell
                else {}
            ),
        },
        immediate_changes=[change],
    )


def _class_feature_target(
    state: dict[str, Any],
    actor: dict[str, Any],
    target_id: Any,
) -> dict[str, Any] | None:
    requested = _text(target_id)
    if not requested or requested == _text(actor.get('id')):
        return actor
    target_actor = find_actor(state, requested)
    if target_actor:
        return target_actor
    participant = _combat_participant(state, _resolve_combat_participant_id(state, requested) or requested)
    if not isinstance(participant, dict):
        return None
    linked_actor = find_actor(state, participant.get('id'))
    return linked_actor or participant


def _class_feature_context(state: dict[str, Any], actor: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    in_combat = _text(combat.get('status')).lower() == 'active'
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    economy = flags.get('turnEconomy') if isinstance(flags.get('turnEconomy'), dict) else {}
    if in_combat and _text(flags.get('activeActorId')) != _text(actor.get('id')):
        return True, {}
    return in_combat, economy


def _validate_class_feature_action(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
) -> dict[str, Any]:
    actor = find_actor(state, _action_value(action, 'actorId', 'actor_id'))
    if not actor:
        return _invalid(action, 'Actor not found.')
    target = _class_feature_target(
        state,
        actor,
        _action_value(action, 'targetId', 'target_id'),
    )
    if _action_value(action, 'targetId', 'target_id') and not target:
        return _invalid(action, 'The selected capability target is stale or unavailable.')
    in_combat, economy = _class_feature_context(state, actor)
    if in_combat and not economy:
        return _invalid(action, 'Only the active combat actor can use a class capability.')
    resolution = resolve_capability_use(
        actor=actor,
        capability_id=_action_value(action, 'capabilityId', 'capability_id'),
        target=target,
        requested_amount=action.get('amount'),
        turn_economy=economy,
        in_combat=in_combat,
        roller=lambda sides: secrets.randbelow(max(1, sides)) + 1,
    )
    if not resolution.get('ok'):
        return _invalid(action, _text(resolution.get('reason')) or 'That class capability is not legal now.')
    capability = resolution.get('capability') if isinstance(resolution.get('capability'), dict) else {}
    trusted_resolution = {
        key: deepcopy(resolution.get(key))
        for key in (
            'targetId',
            'resourceCost',
            'actionEconomy',
            'effectType',
            'amount',
            'rolls',
        )
        if resolution.get(key) is not None
    }
    change = {
        'id': stable_change_id(
            current_turn,
            'pre_dm',
            action.get('id'),
            'class_feature.use',
            actor.get('id'),
            capability.get('id'),
            trusted_resolution.get('targetId'),
        ),
        'turnId': current_turn,
        'type': 'class_feature.use',
        'source': 'pre_dm_class_feature_action',
        'actorId': actor.get('id'),
        'capabilityId': capability.get('id'),
        'capabilityName': capability.get('name'),
        'targetId': trusted_resolution.get('targetId'),
        'requestedAmount': action.get('amount'),
        'resolutionAuthority': 'class_capability_engine',
        'resolution': trusted_resolution,
        'reason': f"{capability.get('name')} was validated by the class capability engine.",
        'visible': True,
    }
    return _valid(
        action,
        change['reason'],
        normalized_action={
            **action,
            'capabilityId': capability.get('id'),
            'targetId': trusted_resolution.get('targetId'),
            'resolution': trusted_resolution,
        },
        immediate_changes=[change],
    )


def _validate_class_feature_change(
    change: dict[str, Any],
    state: dict[str, Any],
) -> tuple[str, str, dict[str, Any] | None]:
    if _text(change.get('source')).lower() != 'pre_dm_class_feature_action':
        return 'rejected', 'Class capability effects must originate in the pre-narration rules engine.', None
    if _text(change.get('resolutionAuthority')) != 'class_capability_engine':
        return 'rejected', 'Class capability resolution authority is missing.', None
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Class capability actor was not found.', None
    target = _class_feature_target(state, actor, _action_value(change, 'targetId', 'target_id'))
    if not target:
        return 'rejected', 'Class capability target was not found.', None
    in_combat, economy = _class_feature_context(state, actor)
    if in_combat and not economy:
        return 'rejected', 'Only the active combat actor can use a class capability.', None
    supplied = change.get('resolution') if isinstance(change.get('resolution'), dict) else {}
    supplied_rolls = [
        max(1, int_or_default(value, default=1))
        for value in (supplied.get('rolls') or [])
    ]
    roll_index = 0

    def _replay_roll(sides: int) -> int:
        nonlocal roll_index
        value = supplied_rolls[roll_index] if roll_index < len(supplied_rolls) else 1
        roll_index += 1
        return max(1, min(max(1, sides), value))

    resolved = resolve_capability_use(
        actor=actor,
        capability_id=_action_value(change, 'capabilityId', 'capability_id'),
        target=target,
        requested_amount=change.get('requestedAmount'),
        turn_economy=economy,
        in_combat=in_combat,
        roller=_replay_roll,
    )
    if not resolved.get('ok'):
        return 'rejected', _text(resolved.get('reason')) or 'Class capability is no longer legal.', None
    expected = {
        key: resolved.get(key)
        for key in ('targetId', 'resourceCost', 'actionEconomy', 'effectType', 'amount', 'rolls')
        if resolved.get(key) is not None
    }
    if supplied != expected:
        return 'rejected', 'Class capability resolution does not match the authoritative rules result.', None
    capability = resolved.get('capability') if isinstance(resolved.get('capability'), dict) else {}
    return (
        'accepted',
        'Class capability and resource cost are valid.',
        {
            **change,
            'capabilityId': capability.get('id'),
            'capabilityName': capability.get('name'),
            'targetId': expected.get('targetId'),
            'resolution': expected,
        },
    )


def _validate_authoritative_spell_change(
    change: dict[str, Any],
    state: dict[str, Any],
    actor: dict[str, Any],
    legality: dict[str, Any],
) -> tuple[str, str, dict[str, Any] | None]:
    spell = legality.get('spell') if isinstance(legality.get('spell'), dict) else {}
    definition = _authoritative_spell_definition(spell, actor)
    if not definition:
        return 'rejected', 'The spell has no server-owned authoritative effect definition.', None
    if _text(change.get('source')).lower() != 'pre_dm_spell_action':
        return 'rejected', 'Authoritative spell effects must originate before narration.', None
    if _text(change.get('resolutionAuthority')) != 'spell_effect_engine':
        return 'rejected', 'Authoritative spell resolution provenance is missing.', None
    target_ids = [
        _text(target_id)
        for target_id in (change.get('targetIds') or change.get('target_ids') or [])
        if _text(target_id)
    ]
    if not target_ids or len(target_ids) != len(set(target_ids)):
        return 'rejected', 'Authoritative spell effects require unique exact target IDs.', None
    supplied_resolution = change.get('resolution') if isinstance(change.get('resolution'), dict) else None
    if supplied_resolution is None:
        return 'rejected', 'Authoritative spell resolution is missing.', None
    replay = _resolve_spell_effect(
        state=state,
        actor=actor,
        spell=definition,
        target_ids=target_ids,
        cast_id=_text(change.get('id')),
        current_turn=max(1, int_or_default(change.get('turnId', change.get('turn_id')), default=1)),
        replay_rolls=_spell_resolution_rolls(supplied_resolution),
    )
    if not replay.get('ok'):
        return 'rejected', _text(replay.get('reason')) or 'Authoritative spell replay failed.', None
    if replay.get('resolution') != supplied_resolution:
        return 'rejected', 'Supplied spell result does not match authoritative roll replay.', None
    resource = legality.get('resource') if isinstance(legality.get('resource'), dict) else {}
    normalized = {
        **change,
        'spellId': spell.get('id'),
        'spellName': spell.get('name'),
        'castLevel': legality.get('castLevel'),
        'resourcePool': resource.get('pool'),
        'targetIds': target_ids,
        'concentration': definition.get('concentration') is True,
        'resolution': deepcopy(replay.get('resolution')),
        'resolvedCombat': deepcopy(replay.get('combat')),
        'effectCasterResources': deepcopy(replay.get('casterResources')),
    }
    return 'accepted', 'Spell targets, rolls, and effects match the authoritative rules engine.', normalized


def _validate_interactable_change(
    change: dict[str, Any],
    state: dict[str, Any],
) -> tuple[str, str, dict[str, Any] | None]:
    if _text(change.get('source')).lower() != 'pre_dm_interactable_action':
        return 'rejected', 'Scene-object transitions must originate before narration.', None
    if _text(change.get('resolutionAuthority')) != 'interactable_rules_engine':
        return 'rejected', 'Scene-object resolution provenance is missing.', None
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Scene-object actor was not found.', None
    request = _interactable_request(
        action_id=_text(change.get('id')),
        action=change,
        state=state,
        current_turn=max(0, int_or_default(change.get('turnId', change.get('turn_id')), default=0)),
    )
    if request['locationId'] != _text(change.get('locationId')):
        return 'rejected', 'The scene-object location changed before application.', None
    resolved = resolve_interactable_action(
        state,
        request,
        _interactable_actor_context(state, actor),
    )
    if not resolved.get('ok'):
        return 'rejected', _text(resolved.get('message')) or 'Scene-object transition is no longer legal.', None
    expected_event = resolved.get('events', [None])[0] if resolved.get('events') else None
    supplied_event = change.get('event') if isinstance(change.get('event'), dict) else None
    if supplied_event != expected_event:
        return 'rejected', 'Scene-object event does not match the authoritative transition.', None
    return (
        'accepted',
        'Scene-object transition matches the authoritative rules engine.',
        {
            **change,
            'event': deepcopy(expected_event),
            'resolvedState': deepcopy(resolved.get('nextState')),
            'resolvedChanges': deepcopy(resolved.get('changes') or []),
        },
    )


def _validate_rest_action(action: dict[str, Any], state: dict[str, Any], *, current_turn: int) -> dict[str, Any]:
    actor = find_actor(state, _action_value(action, 'actorId', 'actor_id'))
    if not actor:
        return _invalid(action, 'Actor not found.')
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    combat = normalize_combat_state(state.get('combat'), scene)
    if _text(combat.get('status')).lower() in {'starting', 'active'} or _text(scene.get('combatState')).lower() in {'pending', 'active'}:
        return _invalid(action, 'Rest is unavailable while combat is active.')
    health = actor.get('health') if isinstance(actor.get('health'), dict) else {}
    conditions = {_text(value).lower() for value in health.get('conditions') or []}
    if int_or_default(health.get('currentHp'), default=1) <= 0 or 'dead' in conditions:
        return _invalid(action, 'A dead or dying character cannot complete a rest.')
    rest_type = _text(_action_value(action, 'restType', 'rest_type')).lower()
    if rest_type not in {'short_rest', 'long_rest'}:
        return _invalid(action, 'Rest type must be short_rest or long_rest.')
    change = {
        'id': stable_change_id(current_turn, 'pre_dm', action.get('id'), 'rest.complete', actor.get('id'), rest_type),
        'turnId': current_turn,
        'type': 'rest.complete',
        'source': 'pre_dm_rest_action',
        'actorId': actor.get('id'),
        'restType': rest_type,
        'reason': f"{actor_name(actor)} completed a {'short' if rest_type == 'short_rest' else 'long'} rest.",
        'visible': False,
    }
    return _valid(
        action,
        change['reason'],
        normalized_action={**action, 'restType': rest_type},
        immediate_changes=[change],
    )


def _validate_attack(
    action: dict[str, Any],
    state: dict[str, Any],
    *,
    current_turn: int,
    recent_context: list[str],
    selected_item_id: str | None,
) -> dict[str, Any]:
    weapon_name = str(_action_value(action, 'weaponName', 'weapon_name') or '').strip()
    if not weapon_name:
        return _pending(action, 'Attack requires DM resolution.')
    actor, item, resolution = _resolve_action_item(
        action=action,
        state=state,
        item_name=weapon_name,
        requested_type='weapon',
        current_turn=current_turn,
        recent_context=recent_context,
        selected_item_id=selected_item_id,
    )
    if not actor:
        return _invalid(action, resolution.get('reason') or 'Actor not found.')
    if (
        resolution.get('status') == 'missing'
        and not selected_item_id
        and normalize_item_name(weapon_name) in GENERIC_RANGED_WEAPON_NAMES
    ):
        item, resolution = _resolve_generic_ranged_attack_weapon(actor, weapon_name)
    if resolution.get('status') == 'needs_clarification':
        return _clarification(action, resolution)
    if resolution.get('status') == 'missing' or not item:
        return _invalid(action, f"{actor_name(actor)} does not have {weapon_name}.")
    mark_used = {
        'id': stable_change_id(current_turn, 'pre_dm', action.get('id'), 'inventory.mark_used', item.get('id')),
        'turnId': current_turn,
        'type': 'inventory.mark_used',
        'source': 'pre_dm',
        'actorId': actor.get('id'),
        'itemId': item.get('id'),
        'reason': f"{actor_name(actor)} used {item.get('name')} for an attack.",
        'visible': False,
    }
    return _pending(
        action,
        f"{actor_name(actor)} can attack with {item.get('name')}.",
        normalized_action={
            **action,
            'weaponId': item.get('id'),
            'weaponName': item.get('name'),
            'resolution': resolution,
        },
        required_rolls=[
            {
                'type': 'attack_roll',
                'actorId': actor.get('id'),
                'targetName': action.get('targetName') or action.get('target_name'),
                'weaponId': item.get('id'),
            }
        ],
    ) | {'immediateChanges': [mark_used]}


def validate_declared_actions(
    *,
    state: dict[str, Any],
    declared_actions: list[dict[str, Any]],
    current_turn: int,
    recent_context: list[str] | None = None,
    selected_item_ids: dict[str, str] | None = None,
    expected_actor_id: str | None = None,
) -> dict[str, Any]:
    selected_item_ids = selected_item_ids or {}
    validated: list[dict[str, Any]] = []
    clarification_requests: list[dict[str, Any]] = []
    for action in declared_actions:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get('type') or '').strip()
        if action_type not in PRE_DM_ACTION_TYPES:
            validated.append(_invalid(action, f"Unsupported declared action type '{action_type}'."))
            continue
        actor_error = _declared_action_actor_error(action, action_type, expected_actor_id)
        if actor_error:
            validated.append(_invalid(action, actor_error))
            continue
        selected_item_id = (
            selected_item_ids.get(str(action.get('id')))
            or _action_value(action, 'itemId', 'item_id')
            or _action_value(action, 'weaponId', 'weapon_id')
        )
        if action_type == 'inventory.consume':
            result = _validate_consume_item(
                action,
                state,
                current_turn=current_turn,
                recent_context=recent_context or [],
                selected_item_id=selected_item_id,
            )
        elif action_type == 'inventory.use':
            result = _validate_use_or_transfer_item(
                action,
                state,
                current_turn=current_turn,
                recent_context=recent_context or [],
                selected_item_id=selected_item_id,
            )
        elif action_type == 'inventory.equip':
            result = _validate_equipment_action(
                action,
                state,
                current_turn=current_turn,
                recent_context=recent_context or [],
                selected_item_id=selected_item_id,
                equip=True,
            )
        elif action_type == 'inventory.unequip':
            result = _validate_equipment_action(
                action,
                state,
                current_turn=current_turn,
                recent_context=recent_context or [],
                selected_item_id=selected_item_id,
                equip=False,
            )
        elif action_type == 'inventory.transfer':
            result = _validate_inventory_transfer(
                action,
                state,
                current_turn=current_turn,
                recent_context=recent_context or [],
                selected_item_id=selected_item_id,
            )
        elif action_type == 'currency.transfer':
            result = _validate_currency_transfer(action, state)
        elif action_type == 'scene.item.pickup':
            result = _validate_scene_item_pickup(action, state, current_turn=current_turn)
        elif action_type == 'scene.item.drop':
            result = _validate_scene_item_drop(
                action,
                state,
                current_turn=current_turn,
                recent_context=recent_context or [],
                selected_item_id=selected_item_id,
            )
        elif action_type == 'world.travel':
            result = _validate_world_travel(action, state, current_turn=current_turn)
        elif action_type == 'scene.interactable.action':
            result = _validate_interactable_action(action, state, current_turn=current_turn)
        elif action_type == 'spell.cast':
            result = _validate_spell_action(action, state, current_turn=current_turn)
        elif action_type == 'class_feature.use':
            result = _validate_class_feature_action(action, state, current_turn=current_turn)
        elif action_type == 'rest.complete':
            result = _validate_rest_action(action, state, current_turn=current_turn)
        elif action_type == 'combat.attack':
            result = _validate_attack(
                action,
                state,
                current_turn=current_turn,
                recent_context=recent_context or [],
                selected_item_id=selected_item_id,
            )
        else:
            result = _pending(action, action.get('summary') or 'Player intent needs DM narration.')
        if result.get('status') == 'needs_clarification' and isinstance(result.get('clarificationRequest'), dict):
            clarification_requests.append(result['clarificationRequest'])
        validated.append(result)

    valid_summaries = []
    invalid_summaries = []
    pending_rolls = []
    immediate_changes = []
    for result in validated:
        status = result.get('status')
        reason = result.get('reason')
        original = result.get('originalAction') if isinstance(result.get('originalAction'), dict) else {}
        label = original.get('summary') or original.get('sourceText') or original.get('type')
        if status in {'valid', 'pending'}:
            valid_summaries.append(f"{label}: {reason}")
        elif status == 'invalid':
            invalid_summaries.append(f"{label}: {reason}")
        for roll in result.get('requiredRolls') or []:
            if isinstance(roll, dict):
                pending_rolls.append(roll)
        for change in result.get('immediateChanges') or []:
            if isinstance(change, dict):
                immediate_changes.append(change)

    summary_parts = []
    if valid_summaries:
        summary_parts.append('Allowed or pending: ' + '; '.join(valid_summaries))
    if invalid_summaries:
        summary_parts.append('Invalid: ' + '; '.join(invalid_summaries))

    return {
        'validatedActions': validated,
        'dmContextSummary': ' '.join(summary_parts).strip(),
        'pendingRolls': pending_rolls,
        'immediateChanges': immediate_changes,
        'clarificationRequests': clarification_requests,
    }


def _accepted(change: dict[str, Any], reason: str) -> dict[str, Any]:
    return {'change': change, 'reason': reason}


def _rejected(change: dict[str, Any], reason: str) -> dict[str, Any]:
    return {'change': change, 'reason': reason}


def _modified(original: dict[str, Any], modified: dict[str, Any], reason: str) -> dict[str, Any]:
    return {'originalChange': original, 'modifiedChange': modified, 'reason': reason}


def _inventory_add_payload(change: dict[str, Any]) -> dict[str, Any]:
    raw_item = change.get('item') if isinstance(change.get('item'), dict) else {}
    name = _text(raw_item.get('name') or change.get('itemName') or change.get('item_name'))
    item_id = _text(
        raw_item.get('id')
        or raw_item.get('itemId')
        or change.get('itemId')
        or change.get('item_id')
        or stable_item_id(name)
    )
    return {
        **raw_item,
        'id': item_id,
        'name': name,
        'quantity': max(1, int_or_default(raw_item.get('quantity', change.get('quantity')), default=1)),
        'type': raw_item.get('type') or change.get('itemType') or change.get('item_type') or 'misc',
    }


def _inventory_items_are_identity_compatible(first: dict[str, Any], second: dict[str, Any]) -> bool:
    text_fields = ('name', 'type', 'subtype', 'rarity', 'damageType', 'damage_type', 'slot')
    for key in text_fields:
        first_value = normalize_item_name(first.get(key))
        second_value = normalize_item_name(second.get(key))
        if first_value and second_value and first_value != second_value:
            return False
    structured_fields = ('damage', 'damageDice', 'damage_dice', 'armorClass', 'armor_class', 'acBonus', 'ac_bonus', 'effect', 'effects')
    for key in structured_fields:
        first_value = first.get(key)
        second_value = second.get(key)
        if first_value not in (None, '', [], {}) and second_value not in (None, '', [], {}) and first_value != second_value:
            return False
    return True


def _inventory_add_matches(
    actor: dict[str, Any],
    payload: dict[str, Any],
    pending_adds: dict[tuple[str, str], list[dict[str, Any]]] | None,
) -> list[dict[str, Any]]:
    item_id = _text(payload.get('id'))
    if not item_id:
        return []
    actor_id = _text(actor.get('id'))
    matches = [item for item in actor_items(actor) if _text(item.get('id')) == item_id]
    if pending_adds is not None:
        matches.extend(pending_adds.get((actor_id, item_id), []))
    return matches


def _quest_reward_collision_item_id(change: dict[str, Any], actor: dict[str, Any], payload: dict[str, Any]) -> str:
    return stable_item_instance_id(
        'quest_reward_collision',
        change.get('id'),
        actor.get('id'),
        payload.get('id'),
        payload.get('name'),
        prefix='itm_reward',
    )


def _reserve_inventory_add(
    actor: dict[str, Any],
    change: dict[str, Any],
    pending_adds: dict[tuple[str, str], list[dict[str, Any]]],
) -> None:
    payload = _inventory_add_payload(change)
    actor_id = _text(actor.get('id'))
    item_id = _text(payload.get('id'))
    if actor_id and item_id:
        pending_adds.setdefault((actor_id, item_id), []).append(payload)


def _validate_inventory_change(
    state: dict[str, Any],
    change: dict[str, Any],
    *,
    pending_adds: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
) -> tuple[str, str, dict[str, Any] | None]:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Actor not found.', None
    quantity = max(0, int_or_default(change.get('quantity'), default=0))
    if quantity <= 0:
        return 'rejected', 'Inventory change quantity must be positive.', None
    if change.get('type') == 'inventory.add':
        payload = _inventory_add_payload(change)
        item_name = _text(payload.get('name'))
        if not item_name:
            return 'rejected', 'Inventory add requires an item name.', None
        matches = _inventory_add_matches(actor, payload, pending_adds)
        existing_identity_matches = [
            item for item in actor_items(actor) if _text(item.get('id')) == _text(payload.get('id'))
        ]
        collision = len(existing_identity_matches) > 1 or any(
            not _inventory_items_are_identity_compatible(existing, payload)
            for existing in matches
        )
        if collision and _text(change.get('source')).lower() == 'quest_engine':
            original_id = payload.get('id')
            payload['id'] = _quest_reward_collision_item_id(change, actor, payload)
            if _inventory_add_matches(actor, payload, pending_adds):
                return 'rejected', 'Quest reward item identity collides with an existing inventory item.', None
            normalized = {
                **change,
                'itemId': payload['id'],
                'itemName': payload['name'],
                'item': payload,
                'sourceItemId': original_id,
            }
            return 'accepted', 'Quest reward item was assigned a distinct deterministic identity.', normalized
        if collision:
            return 'rejected', f"Item identity '{payload.get('id')}' conflicts with an existing inventory item.", None
        return 'accepted', 'Inventory add is valid.', {
            **change,
            'itemId': payload['id'],
            'itemName': payload['name'],
            'item': payload,
        }
    item_id = _action_value(change, 'itemId', 'item_id')
    item_name = _action_value(change, 'itemName', 'item_name')
    if item_id:
        matches = [candidate for candidate in actor_items(actor) if str(candidate.get('id')) == str(item_id)]
        if len(matches) > 1:
            return 'rejected', f"Item identity '{item_id}' is duplicated in the inventory and cannot be targeted safely.", None
        item = matches[0] if matches else None
    else:
        item = next(
            (
                candidate
                for candidate in actor_items(actor)
                if item_name and normalize_item_name(candidate.get('name')) == normalize_item_name(item_name)
            ),
            None,
        )
    if not item:
        return 'rejected', 'Item not found in inventory.', None
    if int_or_default(item.get('quantity'), default=1) < quantity:
        return 'rejected', f"Insufficient quantity. Available: {item.get('quantity')}.", None
    normalized = deepcopy(change)
    normalized['itemId'] = item.get('id')
    normalized['itemName'] = item.get('name')
    return 'accepted', 'Inventory remove is valid.', normalized


def _validate_equipment_change(state: dict[str, Any], change: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Actor not found.', None
    item_id = _action_value(change, 'itemId', 'item_id')
    item_name = _action_value(change, 'itemName', 'item_name')
    if item_id:
        item = next((candidate for candidate in actor_items(actor) if str(candidate.get('id')) == str(item_id)), None)
    else:
        item = next(
            (
                candidate
                for candidate in actor_items(actor)
                if item_name and normalize_item_name(candidate.get('name')) == normalize_item_name(item_name)
            ),
            None,
        )
    if not item:
        return 'rejected', 'Item not found in inventory.', None

    normalized = deepcopy(change)
    normalized['itemId'] = item.get('id')
    normalized['itemName'] = item.get('name')
    if str(change.get('type') or '') == 'inventory.unequip':
        return 'accepted', 'Equipment unequip is valid.', normalized

    if not is_equippable(item):
        return 'rejected', 'Item is not equippable.', None
    slot = infer_equipment_slot(
        item,
        requested_slot=_action_value(change, 'slot', 'equipment_slot'),
        equipped_items=actor_items(actor),
    )
    if not slot:
        return 'rejected', 'Equippable item does not have a valid slot.', None
    conflicts = conflict_items(actor_items(actor), item, slot)
    normalized['slot'] = slot
    normalized['conflictItemIds'] = [conflict.get('id') for conflict in conflicts if conflict.get('id')]
    normalized['conflictItemNames'] = [conflict.get('name') for conflict in conflicts if conflict.get('name')]
    return 'accepted', 'Equipment equip is valid.', normalized


def _validate_currency_change(state: dict[str, Any], change: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Actor not found.', None
    currency = str(change.get('currency') or '').strip().lower()
    amount = max(0, int_or_default(change.get('amount'), default=0))
    if currency not in CURRENCY_TYPES or amount <= 0:
        return 'rejected', 'Currency change requires a positive amount and valid denomination.', None
    if change.get('type') == 'currency.remove' and amount > actor_currency(actor).get(currency, 0):
        return 'rejected', f"Insufficient {currency}. Available: {actor_currency(actor).get(currency, 0)}.", None
    return 'accepted', 'Currency change is valid.', None


def _validate_health_change(state: dict[str, Any], change: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Actor not found.', None
    if change.get('type') == 'health.max.set':
        max_hp = int_or_default(
            change.get('maxHp', change.get('max_hp', change.get('amount'))),
            default=0,
        )
        if max_hp <= 0:
            return 'rejected', 'Max HP change requires a positive maxHp value.', None
        normalized = deepcopy(change)
        normalized['maxHp'] = max_hp
        if normalized.get('currentHp') is not None or normalized.get('current_hp') is not None:
            normalized['currentHp'] = max(
                0,
                min(max_hp, int_or_default(normalized.get('currentHp', normalized.get('current_hp')), default=0)),
            )
        return 'accepted', 'Max HP change is valid.', normalized
    amount = max(0, int_or_default(change.get('amount'), default=0))
    if amount <= 0:
        return 'rejected', 'Health change amount must be positive.', None
    if change.get('type') == 'health.heal':
        health = actor.get('health') if isinstance(actor.get('health'), dict) else {}
        current_hp = max(0, int_or_default(health.get('currentHp'), default=0))
        max_hp = max(0, int_or_default(health.get('maxHp'), default=0))
        if max_hp and current_hp + amount > max_hp:
            modified = deepcopy(change)
            modified['amount'] = max(0, max_hp - current_hp)
            if modified['amount'] <= 0:
                return 'rejected', 'Healing has no effect because HP is already at maximum.', None
            return 'modified', 'Healing capped at max HP.', modified
    return 'accepted', 'Health change is valid.', None


def _validate_xp_change(state: dict[str, Any], change: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Actor not found.', None
    amount = max(0, int_or_default(change.get('amount'), default=0))
    if amount <= 0:
        return 'rejected', 'XP change amount must be positive.', None
    if change.get('type') == 'xp.remove':
        xp = actor.get('xp') if isinstance(actor.get('xp'), dict) else {}
        current_xp = max(0, int_or_default(xp.get('current'), default=0))
        if amount > current_xp:
            modified = deepcopy(change)
            modified['amount'] = current_xp
            if modified['amount'] <= 0:
                return 'rejected', 'XP loss has no effect because XP is already zero.', None
            return 'modified', 'XP loss capped at current XP.', modified
    return 'accepted', 'XP change is valid.', None


def _validate_spell_learn_change(state: dict[str, Any], change: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'rejected', 'Actor not found.', None
    spell = spell_from_change(change)
    if not spell:
        return 'rejected', 'Spell learn requires a spell name.', None
    normalized = deepcopy(change)
    normalized['actorId'] = actor.get('id')
    normalized['spell'] = spell
    normalized['spellName'] = spell.get('name')
    normalized['spellLevel'] = spell.get('level')
    spellbook = normalize_spellbook(actor.get('spellbook') if isinstance(actor.get('spellbook'), dict) else {})
    known = {normalize_item_name(candidate.get('name')) for candidate in spellbook.get('knownSpells', []) if isinstance(candidate, dict)}
    if normalize_item_name(spell.get('name')) in known:
        normalized['alreadyKnown'] = True
        return 'accepted', 'Spell is already known.', normalized
    return 'accepted', 'Spell learn is valid.', normalized


def _text(value: Any) -> str:
    return str(value or '').strip()


def _stable_id(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return stable_slug(text)
    return ''


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _records(state: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [record for record in _list(state.get(key)) if isinstance(record, dict)]


def _find_record(records: list[dict[str, Any]], *, record_id: Any = None, name: Any = None, title: Any = None) -> dict[str, Any] | None:
    requested_id = _text(record_id)
    requested_name = normalize_item_name(name or title)
    if requested_id:
        return next((record for record in records if _text(record.get('id')) == requested_id), None)
    if requested_name:
        for record in records:
            record_name = normalize_item_name(record.get('name') or record.get('title'))
            if record_name == requested_name:
                return record
    return None


def _is_narration_authority_change(change: dict[str, Any]) -> bool:
    return _text(change.get('source')).lower() in NARRATION_AUTHORITY_SOURCES


def _find_location(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    return _find_record(
        _records(state, 'locations'),
        record_id=change.get('locationId'),
        name=change.get('name') or change.get('locationName'),
    )


def _find_quest(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    return _find_record(_records(state, 'quests'), record_id=change.get('questId'), title=change.get('title') or change.get('name'))


def _find_npc(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    return _find_record(
        [*_records(state, 'knownNpcs'), *_records(state, 'partyNpcs')],
        record_id=change.get('npcId'),
        name=change.get('name') or change.get('npcName'),
    )


def _find_pack_content_record(state: dict[str, Any], change: dict[str, Any], config: dict[str, str]) -> dict[str, Any] | None:
    embedded = change.get(config['embedded_key']) if isinstance(change.get(config['embedded_key']), dict) else {}
    return _find_record(
        _records(state, config['collection_key']),
        record_id=change.get(config['id_key']) or embedded.get('id') or embedded.get(config['id_key']),
        name=change.get('name') or embedded.get('name'),
        title=change.get('title') or embedded.get('title'),
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {'1', 'true', 'yes', 'y', 'on', 'allow', 'allowed'}


def _party_membership_change(change: dict[str, Any]) -> bool | None:
    for key in ('party', 'partyNpc', 'inParty'):
        if key not in change:
            continue
        value = change.get(key)
        if isinstance(value, bool):
            return value
        return _truthy(value)
    return None


def _campaign_pack_policy(state: dict[str, Any]) -> tuple[str | None, dict[str, Any], str | None]:
    pack = state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}
    pack_id = _text(pack.get('packId') or pack.get('pack_id'))
    if not pack_id:
        return None, {}, None
    rules = pack.get('directorRules') if isinstance(pack.get('directorRules'), dict) else {}
    flags = state.get('flags') if isinstance(state.get('flags'), dict) else {}
    active_checkpoint_id = _text(
        pack.get('activeCheckpointId')
        or pack.get('active_checkpoint_id')
        or pack.get('currentCheckpointId')
        or pack.get('current_checkpoint_id')
        or flags.get('campaignPackActiveCheckpointId')
        or flags.get('activeCheckpointId')
    )
    rejoin_target_id = active_checkpoint_id or None
    checkpoints = pack.get('checkpoints') if isinstance(pack.get('checkpoints'), list) else []
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, dict):
            continue
        checkpoint_id = _text(
            checkpoint.get('id')
            or checkpoint.get('checkpointId')
            or checkpoint.get('checkpoint_id')
        )
        if checkpoint_id and checkpoint_id == active_checkpoint_id:
            rejoin_target_id = _text(
                checkpoint.get('rejoinTargetCheckpointId')
                or checkpoint.get('rejoin_target_checkpoint_id')
            ) or checkpoint_id
            break
    return pack_id, rules, rejoin_target_id


def _record_is_campaign_pack(record: dict[str, Any] | None, pack_id: str | None) -> bool:
    if not isinstance(record, dict):
        return False
    if _text(record.get('source')) == 'campaign_pack':
        return True
    metadata = record.get('metadata') if isinstance(record.get('metadata'), dict) else {}
    if _text(metadata.get('source')) == 'campaign_pack':
        return True
    return bool(pack_id and _text(record.get('packId') or record.get('pack_id') or metadata.get('packId')) == pack_id)


def _campaign_pack_catalog_records(state: dict[str, Any], key: str) -> list[dict[str, Any]]:
    pack = state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}
    catalog = pack.get('catalog') if isinstance(pack.get('catalog'), dict) else {}
    records = catalog.get(key)
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _pack_catalog_record_for_change(
    state: dict[str, Any],
    *,
    pack_id: str,
    key: str,
    change: dict[str, Any],
) -> dict[str, Any] | None:
    records = _campaign_pack_catalog_records(state, key)
    if key == 'locations':
        return _find_record(
            records,
            record_id=change.get('locationId'),
            name=change.get('name') or change.get('locationName'),
        )
    if key == 'quests':
        return _find_record(records, record_id=change.get('questId'), title=change.get('title') or change.get('name'))
    if key == 'npcs':
        return _find_record(
            records,
            record_id=change.get('npcId'),
            name=change.get('name') or change.get('npcName'),
        )
    for config in PACK_CONTENT_CHANGE_CONFIG.values():
        if key != config['catalog_key']:
            continue
        embedded = change.get(config['embedded_key']) if isinstance(change.get(config['embedded_key']), dict) else {}
        return _find_record(
            records,
            record_id=change.get(config['id_key']) or embedded.get('id') or embedded.get(config['id_key']),
            name=change.get('name') or embedded.get('name'),
            title=change.get('title') or embedded.get('title'),
        )
    return None


def _materialize_pack_catalog_change(
    change: dict[str, Any],
    record: dict[str, Any],
    *,
    pack_id: str,
    embedded_key: str,
    id_key: str,
    label_key: str,
) -> dict[str, Any]:
    updated = deepcopy(change)
    embedded = deepcopy(record)
    existing_embedded = updated.get(embedded_key) if isinstance(updated.get(embedded_key), dict) else {}
    embedded.update(existing_embedded)
    embedded['source'] = 'campaign_pack'
    embedded['packId'] = pack_id
    updated[embedded_key] = embedded
    updated['source'] = 'campaign_pack'
    updated['packId'] = pack_id
    updated.setdefault(id_key, record.get('id'))
    if label_key and not updated.get(label_key):
        updated[label_key] = record.get(label_key) or record.get('name') or record.get('title')
    if embedded_key == 'quest' and not updated.get('title'):
        updated['title'] = record.get('title') or record.get('name')
    if embedded_key == 'location' and not updated.get('name'):
        updated['name'] = record.get('name') or record.get('title')
    if embedded_key == 'npc' and not updated.get('name'):
        updated['name'] = record.get('name')

    record_metadata = record.get('metadata') if isinstance(record.get('metadata'), dict) else {}
    change_metadata = updated.get('metadata') if isinstance(updated.get('metadata'), dict) else {}
    catalog_metadata = {
        **record_metadata,
        **change_metadata,
        'source': 'campaign_pack',
        'packId': pack_id,
        'packContentRole': 'authored',
        'driftControl': 'materialized_from_catalog',
    }
    updated['metadata'] = catalog_metadata
    embedded_metadata = embedded.get('metadata') if isinstance(embedded.get('metadata'), dict) else {}
    embedded['metadata'] = {**embedded_metadata, **catalog_metadata}

    if embedded_key == 'quest':
        record_flags = record.get('flags') if isinstance(record.get('flags'), dict) else {}
        change_flags = updated.get('flags') if isinstance(updated.get('flags'), dict) else {}
        if record_flags or change_flags:
            flags = {**record_flags, **change_flags}
            updated['flags'] = flags
            embedded['flags'] = {**(embedded.get('flags') if isinstance(embedded.get('flags'), dict) else {}), **flags}

    return updated


def _content_payload(change: dict[str, Any], key: str) -> dict[str, Any]:
    value = change.get(key)
    return value if isinstance(value, dict) else {}


def _content_metadata(change: dict[str, Any], key: str) -> dict[str, Any]:
    payload = _content_payload(change, key)
    metadata: dict[str, Any] = {}
    if isinstance(payload.get('metadata'), dict):
        metadata.update(payload['metadata'])
    if isinstance(change.get('metadata'), dict):
        metadata.update(change['metadata'])
    return metadata


def _content_flags(change: dict[str, Any], key: str) -> dict[str, Any]:
    payload = _content_payload(change, key)
    flags: dict[str, Any] = {}
    if isinstance(payload.get('flags'), dict):
        flags.update(payload['flags'])
    if isinstance(change.get('flags'), dict):
        flags.update(change['flags'])
    return flags


def _content_source(change: dict[str, Any], key: str) -> str:
    payload = _content_payload(change, key)
    metadata = _content_metadata(change, key)
    return _text(change.get('source') or payload.get('source') or metadata.get('source'))


def _is_pack_override(change: dict[str, Any], key: str) -> bool:
    source = _content_source(change, key)
    metadata = _content_metadata(change, key)
    flags = _content_flags(change, key)
    return (
        source in {'campaign_pack', 'dm_override', 'admin_override'}
        or _truthy(metadata.get('allowPackOverride'))
        or _truthy(metadata.get('allow_pack_override'))
        or _truthy(metadata.get('promoteToPackMainline'))
        or _truthy(metadata.get('promote_to_pack_mainline'))
        or _truthy(flags.get('dmOverride'))
        or _truthy(flags.get('dm_override'))
    )


def _set_content_source_and_metadata(
    change: dict[str, Any],
    *,
    key: str,
    source: str,
    pack_id: str,
    role: str,
    drift_control: str,
    rejoin_target_id: str | None,
) -> None:
    change['source'] = source
    change['packId'] = pack_id
    payload = change.get(key)
    if isinstance(payload, dict):
        payload['source'] = source
        payload['packId'] = pack_id

    additions = {
        'source': source,
        'packId': pack_id,
        'packContentRole': role,
        'driftControl': drift_control,
    }
    if rejoin_target_id:
        additions['rejoinTargetCheckpointId'] = rejoin_target_id

    metadata = change.get('metadata') if isinstance(change.get('metadata'), dict) else {}
    change['metadata'] = {**metadata, **additions}
    if isinstance(payload, dict):
        payload_metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
        payload['metadata'] = {**payload_metadata, **additions}


def _set_quest_side_flags(change: dict[str, Any]) -> None:
    payload = change.get('quest')
    flags = _content_flags(change, 'quest')
    flags.update({'sideQuest': True, 'mainQuest': False, 'packSideQuest': True})
    change['flags'] = flags
    if isinstance(payload, dict):
        payload['flags'] = {**(payload.get('flags') if isinstance(payload.get('flags'), dict) else {}), **flags}

    metadata = change.get('metadata') if isinstance(change.get('metadata'), dict) else {}
    metadata.update({'questType': 'side_quest'})
    change['metadata'] = metadata
    if isinstance(payload, dict):
        payload_metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
        payload['metadata'] = {**payload_metadata, 'questType': 'side_quest'}


def _apply_campaign_pack_drift_control(
    state: dict[str, Any],
    change: dict[str, Any],
) -> tuple[str, str | None, dict[str, Any] | None]:
    pack_id, rules, rejoin_target_id = _campaign_pack_policy(state)
    if not pack_id:
        return 'accepted', None, change

    change_type = _text(change.get('type'))
    if change_type == 'scene.move_location':
        catalog_location = _pack_catalog_record_for_change(
            state,
            pack_id=pack_id,
            key='locations',
            change=change,
        )
        if catalog_location:
            return (
                'modified',
                'Campaign pack catalog location was materialized for scene movement.',
                _materialize_pack_catalog_change(
                    change,
                    catalog_location,
                    pack_id=pack_id,
                    embedded_key='location',
                    id_key='locationId',
                    label_key='name',
                ),
            )

    if change_type == 'quest.add':
        existing_quest = _find_quest(state, change)
        if _record_is_campaign_pack(existing_quest, pack_id):
            return 'accepted', None, change
        catalog_quest = _pack_catalog_record_for_change(state, pack_id=pack_id, key='quests', change=change)
        if catalog_quest:
            return (
                'modified',
                'Campaign pack catalog quest was revealed into visible state.',
                _materialize_pack_catalog_change(
                    change,
                    catalog_quest,
                    pack_id=pack_id,
                    embedded_key='quest',
                    id_key='questId',
                    label_key='title',
                ),
            )
        if _is_pack_override(change, 'quest'):
            return 'accepted', None, change

        main_quest_policy = _text(rules.get('mainQuestGeneration') or rules.get('main_quest_generation') or 'allowed_tagged')
        side_quest_policy = _text(rules.get('sideQuestGeneration') or rules.get('side_quest_generation') or 'allowed_tagged')
        if main_quest_policy == 'pack_only' and side_quest_policy in {'blocked', 'disabled', 'none', 'pack_only'}:
            return 'rejected', 'Campaign pack policy blocks new non-pack quests.', None

        updated = deepcopy(change)
        _set_content_source_and_metadata(
            updated,
            key='quest',
            source='emergent',
            pack_id=pack_id,
            role='side_quest' if main_quest_policy == 'pack_only' else 'runtime_quest',
            drift_control='downgraded_from_mainline' if main_quest_policy == 'pack_only' else 'tagged_emergent_quest',
            rejoin_target_id=rejoin_target_id,
        )
        if main_quest_policy == 'pack_only':
            _set_quest_side_flags(updated)
            return 'modified', 'Campaign pack drift control downgraded new quest to emergent side content.', updated
        return 'modified', 'Campaign pack drift control tagged new quest as emergent content.', updated

    if change_type == 'location.discover':
        existing_location = _find_location(state, change)
        if _record_is_campaign_pack(existing_location, pack_id):
            return 'accepted', None, change
        catalog_location = _pack_catalog_record_for_change(state, pack_id=pack_id, key='locations', change=change)
        if catalog_location:
            return (
                'modified',
                'Campaign pack catalog location was revealed into visible state.',
                _materialize_pack_catalog_change(
                    change,
                    catalog_location,
                    pack_id=pack_id,
                    embedded_key='location',
                    id_key='locationId',
                    label_key='name',
                ),
            )
        if _is_pack_override(change, 'location'):
            return 'accepted', None, change
        new_location_policy = _text(rules.get('newLocations') or rules.get('new_locations') or 'allowed_as_local_detail')
        if new_location_policy in {'blocked', 'disabled', 'none', 'pack_only'}:
            return 'rejected', 'Campaign pack policy blocks new non-pack locations.', None
        updated = deepcopy(change)
        _set_content_source_and_metadata(
            updated,
            key='location',
            source='emergent',
            pack_id=pack_id,
            role='local_detail',
            drift_control='tagged_local_detail',
            rejoin_target_id=rejoin_target_id,
        )
        return 'modified', 'Campaign pack drift control tagged new location as emergent local detail.', updated

    if change_type == 'npc.discover':
        existing_npc = _find_npc(state, change)
        if _record_is_campaign_pack(existing_npc, pack_id):
            return 'accepted', None, change
        catalog_npc = _pack_catalog_record_for_change(state, pack_id=pack_id, key='npcs', change=change)
        if catalog_npc:
            return (
                'modified',
                'Campaign pack catalog NPC was revealed into visible state.',
                _materialize_pack_catalog_change(
                    change,
                    catalog_npc,
                    pack_id=pack_id,
                    embedded_key='npc',
                    id_key='npcId',
                    label_key='name',
                ),
            )
        if _is_pack_override(change, 'npc'):
            return 'accepted', None, change
        new_npc_policy = _text(rules.get('newNpcs') or rules.get('new_npcs') or 'allowed_as_minor_or_temporary')
        if new_npc_policy in {'blocked', 'disabled', 'none', 'pack_only'}:
            return 'rejected', 'Campaign pack policy blocks new non-pack NPCs.', None
        updated = deepcopy(change)
        _set_content_source_and_metadata(
            updated,
            key='npc',
            source='emergent',
            pack_id=pack_id,
            role='minor_or_temporary',
            drift_control='tagged_minor_or_temporary',
            rejoin_target_id=rejoin_target_id,
        )
        return 'modified', 'Campaign pack drift control tagged new NPC as emergent minor content.', updated

    if change_type in PACK_CONTENT_CHANGE_CONFIG:
        config = PACK_CONTENT_CHANGE_CONFIG[change_type]
        existing_record = _find_pack_content_record(state, change, config)
        if _record_is_campaign_pack(existing_record, pack_id):
            return 'accepted', None, change
        catalog_record = _pack_catalog_record_for_change(
            state,
            pack_id=pack_id,
            key=config['catalog_key'],
            change=change,
        )
        if catalog_record:
            return (
                'modified',
                f"Campaign pack catalog {config['label']} was revealed into visible state.",
                _materialize_pack_catalog_change(
                    change,
                    catalog_record,
                    pack_id=pack_id,
                    embedded_key=config['embedded_key'],
                    id_key=config['id_key'],
                    label_key='title',
                ),
            )
        if _is_pack_override(change, config['embedded_key']):
            return 'accepted', None, change
        updated = deepcopy(change)
        _set_content_source_and_metadata(
            updated,
            key=config['embedded_key'],
            source='emergent',
            pack_id=pack_id,
            role=f"{config['label']}_content",
            drift_control=f"tagged_runtime_{config['label']}",
            rejoin_target_id=rejoin_target_id,
        )
        return 'modified', f"Campaign pack drift control tagged {config['label']} content as emergent.", updated

    if change_type == 'scene.item.add':
        if _is_pack_override(change, 'item'):
            return 'accepted', None, change
        updated = deepcopy(change)
        _set_content_source_and_metadata(
            updated,
            key='item',
            source='emergent',
            pack_id=pack_id,
            role='local_item',
            drift_control='tagged_local_item',
            rejoin_target_id=rejoin_target_id,
        )
        return 'modified', 'Campaign pack drift control tagged new scene item as emergent local content.', updated

    if change_type == 'location.update':
        existing_location = _find_location(state, change)
        if _record_is_campaign_pack(existing_location, pack_id) or _is_pack_override(change, 'location'):
            return 'accepted', None, change
        updated = deepcopy(change)
        _set_content_source_and_metadata(
            updated,
            key='location',
            source='emergent',
            pack_id=pack_id,
            role='local_detail_update',
            drift_control='tagged_local_detail_update',
            rejoin_target_id=rejoin_target_id,
        )
        return 'modified', 'Campaign pack drift control tagged location update as emergent local detail.', updated

    if change_type == 'location.connect':
        if _is_pack_override(change, 'location'):
            return 'accepted', None, change
        updated = deepcopy(change)
        _set_content_source_and_metadata(
            updated,
            key='location',
            source='emergent',
            pack_id=pack_id,
            role='local_route',
            drift_control='tagged_local_route',
            rejoin_target_id=rejoin_target_id,
        )
        return 'modified', 'Campaign pack drift control tagged new location connection as emergent route content.', updated

    if change_type == 'npc.relationship.update':
        existing_npc = _find_npc(state, change)
        if _is_pack_override(change, 'npc'):
            return 'accepted', None, change
        updated = deepcopy(change)
        _set_content_source_and_metadata(
            updated,
            key='relationship',
            source='player_created' if _record_is_campaign_pack(existing_npc, pack_id) else 'emergent',
            pack_id=pack_id,
            role='relationship_delta',
            drift_control='tagged_relationship_delta',
            rejoin_target_id=rejoin_target_id,
        )
        return 'modified', 'Campaign pack drift control tagged NPC relationship change with source metadata.', updated

    if change_type == 'flag.set':
        if _is_pack_override(change, 'flag'):
            return 'accepted', None, change
        updated = deepcopy(change)
        _set_content_source_and_metadata(
            updated,
            key='flag',
            source='emergent',
            pack_id=pack_id,
            role='runtime_flag',
            drift_control='tagged_runtime_flag',
            rejoin_target_id=rejoin_target_id,
        )
        return 'modified', 'Campaign pack drift control tagged runtime flag as emergent content.', updated

    return 'accepted', None, change


def _apply_campaign_pack_inventory_drift_control(
    state: dict[str, Any],
    change: dict[str, Any],
) -> tuple[str, str | None, dict[str, Any] | None]:
    pack_id, _rules, rejoin_target_id = _campaign_pack_policy(state)
    if not pack_id or _text(change.get('type')) != 'inventory.add':
        return 'accepted', None, change
    item = change.get('item') if isinstance(change.get('item'), dict) else {}
    if _record_is_campaign_pack(item, pack_id) or _is_pack_override(change, 'item'):
        return 'accepted', None, change
    updated = deepcopy(change)
    if not isinstance(updated.get('item'), dict):
        item_name = _text(updated.get('itemName') or updated.get('item_name'))
        updated['item'] = {
            'id': _text(updated.get('itemId') or updated.get('item_id')) or stable_slug(item_name),
            'name': item_name,
            'quantity': max(1, int_or_default(updated.get('quantity'), default=1)),
            'type': _text(updated.get('itemType') or updated.get('item_type')) or 'misc',
        }
    _set_content_source_and_metadata(
        updated,
        key='item',
        source='emergent',
        pack_id=pack_id,
        role='runtime_inventory_item',
        drift_control='tagged_runtime_inventory_item',
        rejoin_target_id=rejoin_target_id,
    )
    return 'modified', 'Campaign pack drift control tagged inventory item as emergent runtime content.', updated


def _apply_campaign_pack_combat_drift_control(
    state: dict[str, Any],
    change: dict[str, Any],
) -> tuple[str, str | None, dict[str, Any] | None]:
    pack_id, _rules, rejoin_target_id = _campaign_pack_policy(state)
    if not pack_id or _text(change.get('type')) != 'combat.start':
        return 'accepted', None, change

    combat = change.get('combat') if isinstance(change.get('combat'), dict) else {}
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    if (
        _text(change.get('source')) in {'campaign_pack', 'dm_override', 'admin_override'}
        or _text(flags.get('campaignPackEncounterId'))
        or _text(flags.get('campaignPackId')) == pack_id
    ):
        return 'accepted', None, change

    updated = deepcopy(change)
    updated['source'] = 'emergent'
    updated['packId'] = pack_id
    updated_combat = updated.get('combat') if isinstance(updated.get('combat'), dict) else {}
    updated['combat'] = updated_combat
    updated_flags = updated_combat.get('flags') if isinstance(updated_combat.get('flags'), dict) else {}
    updated_flags.update(
        {
            'source': 'emergent',
            'packId': pack_id,
            'packContentRole': 'runtime_encounter',
            'driftControl': 'tagged_emergent_combat',
        }
    )
    if rejoin_target_id:
        updated_flags['rejoinTargetCheckpointId'] = rejoin_target_id
    updated_combat['flags'] = updated_flags
    metadata = updated.get('metadata') if isinstance(updated.get('metadata'), dict) else {}
    metadata.update(
        {
            'source': 'emergent',
            'packId': pack_id,
            'packContentRole': 'runtime_encounter',
            'driftControl': 'tagged_emergent_combat',
        }
    )
    if rejoin_target_id:
        metadata['rejoinTargetCheckpointId'] = rejoin_target_id
    updated['metadata'] = metadata
    return 'modified', 'Campaign pack drift control tagged non-pack combat as emergent runtime encounter.', updated


def _find_npc_by_reference(state: dict[str, Any], reference: Any) -> dict[str, Any] | None:
    reference_text = _text(reference)
    if not reference_text:
        return None
    reference_keys = {
        normalize_item_name(reference_text),
        stable_slug(reference_text),
    }
    for npc in [*_records(state, 'knownNpcs'), *_records(state, 'partyNpcs')]:
        candidate_values = [
            npc.get('id'),
            npc.get('npcId'),
            npc.get('name'),
            npc.get('npcName'),
            *(
                npc.get('aliases')
                if isinstance(npc.get('aliases'), list)
                else []
            ),
        ]
        candidate_keys = {
            key
            for value in candidate_values
            for key in (normalize_item_name(value), stable_slug(value))
            if key
        }
        if reference_keys.intersection(candidate_keys):
            return npc
    return None


def _player_character_collision_label(state: dict[str, Any], change: dict[str, Any]) -> str | None:
    requested_id = _text(change.get('npcId') or change.get('id'))
    requested_name = normalize_item_name(change.get('name') or change.get('npcName'))
    for actor in _records(state, 'playerCharacters'):
        actor_name = _text(actor.get('name') or actor.get('characterName'))
        actor_player_id = _text(actor.get('playerId') or actor.get('player_id'))
        actor_ids = {
            _text(actor.get('id')),
            actor_player_id,
            f'player_{actor_player_id}' if actor_player_id else '',
            stable_slug(actor_name) if actor_name else '',
        }
        if requested_id and requested_id in {value for value in actor_ids if value}:
            return actor_name or requested_id
        if requested_name and actor_name and requested_name == normalize_item_name(actor_name):
            return actor_name
    return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _bounded_danger(value: Any) -> int | None:
    if value is None:
        return None
    parsed = int_or_default(value, default=-1)
    if parsed < 0:
        return None
    return max(0, min(10, parsed))


def _valid_location_label(value: Any) -> bool:
    label = _text(value)
    if not label:
        return True
    words = re.findall(r'[A-Za-z0-9]+', label)
    return len(label) <= 90 and len(words) <= 10


def _append_metadata_note(normalized: dict[str, Any], key: str, value: str) -> None:
    metadata = normalized.get('metadata') if isinstance(normalized.get('metadata'), dict) else {}
    metadata[key] = value
    normalized['metadata'] = metadata


def _normalize_npc_status_and_disposition(normalized: dict[str, Any]) -> None:
    disposition = _text(normalized.get('disposition')).lower()
    if disposition in NPC_DISPOSITION_ALIASES:
        normalized['disposition'] = NPC_DISPOSITION_ALIASES[disposition]
        _append_metadata_note(normalized, 'extractedDisposition', disposition)

    status = _text(normalized.get('status')).lower()
    if status in NPC_STATUS_ALIASES:
        normalized['status'] = NPC_STATUS_ALIASES[status]
        _append_metadata_note(normalized, 'extractedStatus', status)
        normalized['memory'] = _string_list(normalized.get('memory')) + [f'Status note: {status}.']


def _noncombat_condition_as_npc_update(change: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    if str(change.get('type') or '').strip() not in {'combat.condition.add', 'combat.condition.remove'}:
        return None
    reference = change.get('participantId') or change.get('participant_id') or change.get('enemyId') or change.get('enemy_id')
    if _resolve_combat_participant_id(state, _text(reference)):
        return None
    npc = _find_npc_by_reference(state, reference)
    if not npc:
        return None
    condition = _text(change.get('condition') or change.get('conditionName') or change.get('condition_name')).lower().replace(' ', '_')
    if not condition:
        return None
    action = 'removed' if str(change.get('type') or '').strip() == 'combat.condition.remove' else 'added'
    return {
        **change,
        'type': 'npc.update',
        'npcId': npc.get('id') or npc.get('npcId'),
        'name': npc.get('name') or npc.get('npcName'),
        'status': 'known',
        'memory': _string_list(change.get('memory')) + [f'Condition {action}: {condition}.'],
        'metadata': {
            **(change.get('metadata') if isinstance(change.get('metadata'), dict) else {}),
            'sourceCombatCondition': condition,
            'sourceCombatConditionAction': action,
        },
    }


def _scene_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    items = scene.get('items') if isinstance(scene.get('items'), list) else []
    return [item for item in items if isinstance(item, dict)]


def _split_stack_item_id(*, transfer_id: str, source_item_id: str, destination_id: str) -> str:
    return stable_item_instance_id(
        'split_stack',
        transfer_id,
        source_item_id,
        destination_id,
        prefix='itm_split',
    )


def _scene_item_payload(change: dict[str, Any]) -> dict[str, Any]:
    item = change.get('item') if isinstance(change.get('item'), dict) else {}
    name = _text(item.get('name') or change.get('itemName') or change.get('item_name'))
    item_id = _text(item.get('id') or item.get('itemId') or change.get('itemId') or change.get('item_id') or stable_slug(name))
    quantity = max(1, int_or_default(item.get('quantity', change.get('quantity')), default=1))
    payload = {
        **item,
        'id': item_id,
        'name': name,
        'quantity': quantity,
        'type': item.get('type') or change.get('itemType') or change.get('item_type') or 'misc',
    }
    source_actor_id = _text(change.get('sourceActorId') or change.get('fromActorId') or item.get('sourceActorId'))
    if source_actor_id:
        payload['sourceActorId'] = source_actor_id
    return payload


def _find_scene_item(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    requested_id = _text(change.get('itemId') or change.get('item_id'))
    item_payload = change.get('item') if isinstance(change.get('item'), dict) else {}
    requested_name = normalize_item_name(change.get('itemName') or change.get('item_name') or item_payload.get('name'))
    for item in _scene_items(state):
        if requested_id and _text(item.get('id')) == requested_id:
            return item
    if requested_id:
        return None
    if requested_name:
        for item in _scene_items(state):
            if normalize_item_name(item.get('name')) == requested_name:
                return item
    return None


def _turn_value(change: dict[str, Any]) -> int | None:
    if change.get('turnId') is None and change.get('turn_id') is None:
        return None
    value = int_or_default(change.get('turnId', change.get('turn_id')), default=0)
    return value if value > 0 else None


def _normalize_pack_content_change(change_type: str, normalized: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    config = PACK_CONTENT_CHANGE_CONFIG[change_type]
    embedded = normalized.get(config['embedded_key']) if isinstance(normalized.get(config['embedded_key']), dict) else {}
    label = _text(normalized.get('title') or normalized.get('name') or embedded.get('title') or embedded.get('name'))
    record_id = _stable_id(normalized.get(config['id_key']), embedded.get('id'), embedded.get(config['id_key']), label)
    if not record_id:
        return 'rejected', f"{config['label'].title()} change requires an id or title.", None
    normalized[config['id_key']] = record_id
    if label:
        normalized.setdefault('title', label)
    normalized.setdefault('status', config['default_status'])
    for list_key in ('locationIds', 'npcIds', 'questIds', 'checkpointIds', 'tags'):
        normalized[list_key] = _string_list(normalized.get(list_key))

    if change_type == 'faction.relationship.update':
        relationship = normalized.get('relationship') if isinstance(normalized.get('relationship'), dict) else {}
        score_value = normalized.get('relationshipScore', relationship.get('score'))
        if score_value is not None:
            try:
                normalized['relationshipScore'] = max(-100, min(100, int(score_value)))
            except (TypeError, ValueError):
                return 'rejected', 'Faction relationship score must be numeric.', None
        delta_value = normalized.get('scoreDelta')
        if delta_value is not None:
            try:
                normalized['scoreDelta'] = max(-100, min(100, int(delta_value)))
            except (TypeError, ValueError):
                return 'rejected', 'Faction relationship scoreDelta must be numeric.', None
        if normalized.get('relationshipLabel') is None and relationship.get('label') is not None:
            normalized['relationshipLabel'] = relationship.get('label')

    if change_type in {'map.reveal', 'map.region.update'}:
        region = normalized.get('region') if isinstance(normalized.get('region'), dict) else {}
        region_label = _text(
            normalized.get('regionTitle')
            or normalized.get('regionName')
            or region.get('title')
            or region.get('name')
        )
        region_id = _stable_id(normalized.get('regionId'), region.get('id'), region.get('regionId'), region_label)
        if change_type == 'map.region.update' and not region_id:
            return 'rejected', 'Map region update requires a region id or name.', None
        if region_id:
            normalized['regionId'] = region_id
            if region_label:
                normalized.setdefault('regionTitle', region_label)
        if change_type == 'map.reveal':
            normalized['revealed'] = True

    return 'accepted', f"{config['label'].title()} change is valid.", normalized


def _normalize_world_change(change: dict[str, Any], state: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    change_type = str(change.get('type') or '').strip()
    normalized = deepcopy(change)
    turn_id = _turn_value(normalized)
    if turn_id is not None:
        normalized['turnId'] = turn_id

    if change_type == 'scene.update':
        scene_type = _text(normalized.get('sceneType'))
        mood = _text(normalized.get('mood'))
        combat_state = _text(normalized.get('combatState'))
        if (normalized.get('locationId') or normalized.get('name')) and not _valid_location_label(
            normalized.get('name') or normalized.get('locationId')
        ):
            has_other_scene_fields = any(
                key in normalized
                for key in (
                    'sceneType',
                    'dangerLevel',
                    'mood',
                    'combatState',
                    'description',
                    'activeNpcIds',
                    'activeQuestIds',
                    'playerPositions',
                    'playerZones',
                    'characterPositions',
                    'characterZones',
                    'musicTag',
                )
            )
            if not has_other_scene_fields:
                return 'rejected', 'Scene update location must be a short place name.', None
            normalized.pop('locationId', None)
            normalized.pop('name', None)
        if scene_type and scene_type not in SCENE_TYPES:
            return 'rejected', f"Unsupported scene type '{scene_type}'.", None
        if mood and mood not in SCENE_MOODS:
            return 'rejected', f"Unsupported scene mood '{mood}'.", None
        if combat_state and combat_state not in COMBAT_STATES:
            return 'rejected', f"Unsupported combat state '{combat_state}'.", None
        if 'dangerLevel' in normalized:
            danger_level = _bounded_danger(normalized.get('dangerLevel'))
            if danger_level is None:
                return 'rejected', 'Scene dangerLevel must be a non-negative number.', None
            normalized['dangerLevel'] = danger_level
        if normalized.get('locationId') or normalized.get('name'):
            normalized['locationId'] = _stable_id(normalized.get('locationId'), normalized.get('name'))
        normalized['activeNpcIds'] = _string_list(normalized.get('activeNpcIds'))
        normalized['activeQuestIds'] = _string_list(normalized.get('activeQuestIds'))
        for key in ('playerPositions', 'playerZones', 'characterPositions', 'characterZones'):
            if key in normalized and not isinstance(normalized.get(key), dict):
                return 'rejected', f'Scene {key} must be an object.', None
        return 'accepted', 'Scene update is valid.', normalized

    if change_type == 'scene.move_location':
        scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
        combat = normalize_combat_state(state.get('combat'), scene)
        combat_status = _text(combat.get('status')).lower()
        scene_combat_status = _text(scene.get('combatState')).lower()
        if combat_status in {'starting', 'active'} or scene_combat_status in {'pending', 'active'}:
            return 'rejected', 'Scene movement is unavailable while combat is active.', None
        if not _valid_location_label(normalized.get('name') or normalized.get('locationName') or normalized.get('locationId')):
            return 'rejected', 'Scene movement location must be a short place name.', None
        location_id = _stable_id(normalized.get('locationId'), normalized.get('name') or normalized.get('locationName'))
        if not location_id:
            return 'rejected', 'Scene movement requires a location id or name.', None
        normalized['locationId'] = location_id
        if normalized.get('sceneType') and _text(normalized.get('sceneType')) not in SCENE_TYPES:
            return 'rejected', f"Unsupported scene type '{normalized.get('sceneType')}'.", None
        if normalized.get('mood') and _text(normalized.get('mood')) not in SCENE_MOODS:
            return 'rejected', f"Unsupported scene mood '{normalized.get('mood')}'.", None
        if normalized.get('combatState') and _text(normalized.get('combatState')) not in COMBAT_STATES:
            return 'rejected', f"Unsupported combat state '{normalized.get('combatState')}'.", None
        if 'dangerLevel' in normalized:
            danger_level = _bounded_danger(normalized.get('dangerLevel'))
            if danger_level is None:
                return 'rejected', 'Scene movement dangerLevel must be a non-negative number.', None
            normalized['dangerLevel'] = danger_level
        return 'accepted', 'Scene movement is valid.', normalized

    if change_type == 'scene.item.add':
        item_payload = _scene_item_payload(normalized)
        if not item_payload.get('name'):
            return 'rejected', 'Scene item add requires an item name.', None
        normalized['item'] = item_payload
        normalized['itemId'] = item_payload.get('id')
        normalized['itemName'] = item_payload.get('name')
        normalized['quantity'] = item_payload.get('quantity')
        return 'accepted', 'Scene item add is valid.', normalized

    if change_type == 'scene.item.remove':
        scene_item = _find_scene_item(state, normalized)
        if not scene_item:
            return 'rejected', 'Scene item was not found.', None
        quantity = max(1, int_or_default(normalized.get('quantity'), default=1))
        available = max(1, int_or_default(scene_item.get('quantity'), default=1))
        if quantity > available:
            return 'rejected', f"Not enough {scene_item.get('name')} in scene. Available: {available}.", None
        normalized['itemId'] = scene_item.get('id')
        normalized['itemName'] = scene_item.get('name')
        normalized['quantity'] = quantity
        normalized['item'] = {**scene_item, 'quantity': quantity}
        return 'accepted', 'Scene item remove is valid.', normalized

    if change_type in {'location.discover', 'location.update'}:
        if not _valid_location_label(normalized.get('name') or normalized.get('locationName') or normalized.get('locationId')):
            return 'rejected', 'Location name must be a short place name.', None
        explicit_location_id = _text(change.get('locationId') or change.get('location_id'))
        location_name = normalized.get('name') or normalized.get('locationName')
        existing_location = _find_record(
            _records(state, 'locations'),
            record_id=explicit_location_id,
            name=None if explicit_location_id else location_name,
        )
        if change_type == 'location.update' and explicit_location_id and not existing_location:
            return 'rejected', 'Location update target id was stale or not found.', None
        location_id = _stable_id(
            existing_location.get('id') if existing_location else explicit_location_id,
            location_name,
        )
        if not location_id:
            return 'rejected', 'Location change requires a location id or name.', None
        normalized['locationId'] = location_id
        location = normalized.get('location') if isinstance(normalized.get('location'), dict) else {}
        location_type = _text(normalized.get('locationType') or location.get('type'))
        status = _text(normalized.get('status'))
        if location_type and location_type not in LOCATION_TYPES:
            return 'rejected', f"Unsupported location type '{location_type}'.", None
        if location_type:
            normalized['locationType'] = location_type
        if status and status not in LOCATION_STATUSES:
            return 'rejected', f"Unsupported location status '{status}'.", None
        if change_type == 'location.update' and not existing_location:
            change_type = 'location.discover'
            normalized['type'] = change_type
        normalized['connectedLocationIds'] = [_stable_id(value) for value in _string_list(normalized.get('connectedLocationIds'))]
        normalized['npcIds'] = _string_list(normalized.get('npcIds'))
        normalized['questIds'] = _string_list(normalized.get('questIds'))
        normalized['tags'] = _string_list(normalized.get('tags'))
        return 'accepted', 'Location change is valid.', normalized

    if change_type == 'location.connect':
        normalized['locationId'] = _stable_id(normalized.get('locationId') or normalized.get('fromLocationId'), normalized.get('name'))
        normalized['connectedLocationId'] = _stable_id(
            normalized.get('connectedLocationId') or normalized.get('toLocationId'),
            normalized.get('connectedLocationName') or normalized.get('toLocationName'),
        )
        if not normalized['locationId'] or not normalized['connectedLocationId']:
            return 'rejected', 'Location connection requires two location ids or names.', None
        if normalized['locationId'] == normalized['connectedLocationId']:
            return 'rejected', 'Location connection targets must be different.', None
        return 'accepted', 'Location connection is valid.', normalized

    if change_type.startswith('quest.'):
        explicit_quest_id = _text(change.get('questId') or change.get('quest_id'))
        quest_title = normalized.get('title') or normalized.get('name')
        existing_quest = None
        if change_type != 'quest.add':
            existing_quest = _find_record(
                _records(state, 'quests'),
                record_id=explicit_quest_id,
                title=None if explicit_quest_id else quest_title,
            )
            if not existing_quest:
                return 'rejected', 'Quest update target id was stale or not found.', None
        normalized['questId'] = _stable_id(
            existing_quest.get('id') if existing_quest else explicit_quest_id,
            quest_title,
        )
        if not normalized['questId']:
            return 'rejected', 'Quest change requires a quest id or title.', None
        status = _text(normalized.get('status'))
        if change_type not in {'quest.objective.add', 'quest.objective.update'} and status and status not in QUEST_STATUSES:
            return 'rejected', f"Unsupported quest status '{status}'.", None
        if (
            change_type == 'quest.update'
            and status in TERMINAL_QUEST_UPDATE_STATUSES
            and _is_narration_authority_change(normalized)
        ):
            return 'rejected', 'Narration cannot complete or fail an authoritative quest.', None
        if change_type == 'quest.complete':
            blocker = quest_completion_blocker(state, normalized['questId'])
            if blocker:
                return 'rejected', blocker, None
        if change_type == 'quest.fail':
            blocker = quest_failure_blocker(state, normalized['questId'])
            if blocker:
                return 'rejected', blocker, None
        if isinstance(normalized.get('objectives'), list):
            objectives = []
            for objective in normalized.get('objectives') or []:
                if not isinstance(objective, dict):
                    continue
                objective_status = _text(objective.get('status'))
                if objective_status and objective_status not in OBJECTIVE_STATUSES:
                    return 'rejected', f"Unsupported quest objective status '{objective_status}'.", None
                if (
                    change_type == 'quest.update'
                    and objective_status in TERMINAL_OBJECTIVE_UPDATE_STATUSES
                    and _is_narration_authority_change(normalized)
                ):
                    return 'rejected', 'Narration cannot complete or fail an authoritative quest objective.', None
                explicit_objective_id = _text(objective.get('id') or objective.get('objectiveId'))
                existing_objective = None
                if change_type == 'quest.update' and isinstance(existing_quest, dict):
                    existing_objective = _find_record(
                        _records(existing_quest, 'objectives'),
                        record_id=explicit_objective_id,
                        name=None if explicit_objective_id else objective.get('description'),
                    )
                    if explicit_objective_id and not existing_objective:
                        return 'rejected', 'Quest objective update target id was stale or not found.', None
                objective_id = _stable_id(
                    existing_objective.get('id') if existing_objective else explicit_objective_id,
                    objective.get('description'),
                )
                if not objective_id and change_type in {'quest.add', 'quest.update'}:
                    return 'rejected', 'Quest objective requires an id or description.', None
                objectives.append({**objective, 'id': objective_id or objective.get('id')})
            normalized['objectives'] = objectives
        if change_type in {'quest.objective.add', 'quest.objective.update'}:
            objective = normalized.get('objective') if isinstance(normalized.get('objective'), dict) else {}
            objective_status = _text(objective.get('status') or normalized.get('objectiveStatus') or normalized.get('status'))
            if objective_status and objective_status not in OBJECTIVE_STATUSES:
                return 'rejected', f"Unsupported quest objective status '{objective_status}'.", None
            if (
                change_type == 'quest.objective.update'
                and objective_status in TERMINAL_OBJECTIVE_UPDATE_STATUSES
                and _is_narration_authority_change(normalized)
            ):
                return 'rejected', 'Narration cannot complete or fail an authoritative quest objective.', None
            if objective_status:
                normalized['objectiveStatus'] = objective_status
            explicit_objective_id = _text(normalized.get('objectiveId') or objective.get('id'))
            existing_objective = None
            if change_type == 'quest.objective.update' and isinstance(existing_quest, dict):
                existing_objective = _find_record(
                    _records(existing_quest, 'objectives'),
                    record_id=explicit_objective_id,
                    name=None if explicit_objective_id else objective.get('description') or normalized.get('description'),
                )
                if not existing_objective:
                    return 'rejected', 'Quest objective update target id was stale or not found.', None
            objective_id = _stable_id(
                existing_objective.get('id') if existing_objective else explicit_objective_id,
                objective.get('description') or normalized.get('description'),
            )
            if not objective_id:
                return 'rejected', 'Quest objective change requires an objective id or description.', None
            normalized['objectiveId'] = objective_id
        normalized['relatedNpcIds'] = _string_list(normalized.get('relatedNpcIds'))
        normalized['relatedLocationIds'] = [_stable_id(value) for value in _string_list(normalized.get('relatedLocationIds'))]
        normalized['importantItemIds'] = _string_list(normalized.get('importantItemIds'))
        return 'accepted', 'Quest change is valid.', normalized

    if change_type.startswith('npc.'):
        explicit_npc_id = _text(change.get('npcId') or change.get('npc_id'))
        npc_name = normalized.get('name') or normalized.get('npcName')
        existing_npc = _find_record(
            [*_records(state, 'knownNpcs'), *_records(state, 'partyNpcs')],
            record_id=explicit_npc_id,
            name=None if explicit_npc_id else npc_name,
        )
        normalized['npcId'] = _stable_id(
            existing_npc.get('id') if existing_npc else explicit_npc_id,
            npc_name,
        )
        if not normalized['npcId']:
            return 'rejected', 'NPC change requires an npc id or name.', None
        player_collision = _player_character_collision_label(state, normalized)
        if player_collision:
            return 'rejected', f"NPC change targets player character '{player_collision}'.", None
        _normalize_npc_status_and_disposition(normalized)
        disposition = _text(normalized.get('disposition'))
        status = _text(normalized.get('status'))
        if disposition and disposition not in NPC_DISPOSITIONS:
            return 'rejected', f"Unsupported NPC disposition '{disposition}'.", None
        if status and status not in NPC_STATUSES:
            return 'rejected', f"Unsupported NPC status '{status}'.", None
        if change_type != 'npc.discover' and not existing_npc:
            if change_type == 'npc.update' and not explicit_npc_id and _text(npc_name):
                change_type = 'npc.discover'
                normalized['type'] = change_type
                return 'accepted', 'NPC update target was missing, so it will be applied as a discovery.', normalized
            return 'rejected', 'NPC update target id was stale or not found.', None
        membership_change = _party_membership_change(normalized)
        current_scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
        current_location_id = _stable_id(
            current_scene.get('locationId'),
            current_scene.get('name'),
        )
        if membership_change is True:
            if not current_location_id:
                return 'rejected', 'NPC cannot join the party without a current scene location.', None
            # Joining is a physical transition: even a remote NPC is moved to
            # the party's current scene. Leaving does not alter locationId.
            normalized['locationId'] = current_location_id
        if normalized.get('locationId'):
            normalized['locationId'] = _stable_id(normalized.get('locationId'))
        party_npc = _find_record(
            _records(state, 'partyNpcs'),
            record_id=normalized.get('npcId'),
            name=normalized.get('name') or normalized.get('npcName'),
        )
        target_location_id = _text(normalized.get('locationId'))
        leaves_party_atomically = change_type == 'npc.update' and membership_change is False
        if (
            party_npc
            and target_location_id
            and target_location_id != current_location_id
            and not leaves_party_atomically
        ):
            return (
                'rejected',
                'Party NPCs must remain at the current scene unless they leave the party in the same NPC update.',
                None,
            )
        normalized['questIds'] = _string_list(normalized.get('questIds'))
        if change_type == 'npc.relationship.update':
            relationship = normalized.get('relationship') if isinstance(normalized.get('relationship'), dict) else {}
            score_value = normalized.get('relationshipScore', relationship.get('score'))
            if score_value is not None:
                try:
                    normalized['relationshipScore'] = max(-100, min(100, int(score_value)))
                except (TypeError, ValueError):
                    return 'rejected', 'NPC relationship score must be numeric.', None
            delta_value = normalized.get('scoreDelta')
            if delta_value is not None:
                try:
                    normalized['scoreDelta'] = max(-100, min(100, int(delta_value)))
                except (TypeError, ValueError):
                    return 'rejected', 'NPC relationship scoreDelta must be numeric.', None
            if normalized.get('relationshipLabel') is None and relationship.get('label') is not None:
                normalized['relationshipLabel'] = relationship.get('label')
        return 'accepted', 'NPC change is valid.', normalized

    if change_type in PACK_CONTENT_CHANGE_CONFIG:
        return _normalize_pack_content_change(change_type, normalized)

    if change_type == 'flag.set':
        if not _text(normalized.get('flagKey')):
            return 'rejected', 'Flag set requires flagKey.', None
        if _is_narration_authority_change(normalized):
            return 'rejected', 'Narration cannot set authoritative gameplay flags.', None
        normalized['flagKey'] = stable_slug(normalized.get('flagKey'))
        return 'accepted', 'Flag set is valid.', normalized
    if change_type == 'flag.unset':
        if not _text(normalized.get('flagKey')):
            return 'rejected', 'Flag unset requires flagKey.', None
        normalized['flagKey'] = stable_slug(normalized.get('flagKey'))
        return 'accepted', 'Flag unset is valid.', normalized

    return 'rejected', 'Unsupported world state change.', None


def _combat_participant_ids(state: dict[str, Any]) -> set[str]:
    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    return {
        _text(participant.get('id'))
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and _text(participant.get('id'))
    }


def _combat_reference_keys(value: Any) -> set[str]:
    text = _text(value)
    if not text:
        return set()
    normalized = normalize_item_name(text)
    cleaned = re.sub(r'[^a-z0-9]+', ' ', normalized).strip()
    candidates = {normalized, cleaned, stable_slug(text)}
    for candidate in list(candidates):
        if not candidate:
            continue
        for article in ('the ', 'a ', 'an '):
            if candidate.startswith(article):
                candidates.add(candidate[len(article) :].strip())
        for marker in (' the ', ' a ', ' an '):
            if marker in candidate:
                candidates.add(candidate.rsplit(marker, 1)[-1].strip())
    return {candidate for candidate in candidates if candidate}


def _combat_participant_reference_keys(participant: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for value in (
        participant.get('id'),
        participant.get('name'),
        participant.get('definitionId'),
        participant.get('creatureType'),
    ):
        keys.update(_combat_reference_keys(value))
    for alias in participant.get('aliases') or []:
        keys.update(_combat_reference_keys(alias))
    return keys


def _resolve_combat_participant_id(state: dict[str, Any], participant_id: str) -> str | None:
    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    requested = _text(participant_id)
    if not requested:
        return None
    for participant in combat.get('participants') or []:
        if isinstance(participant, dict) and _text(participant.get('id')) == requested:
            return requested
    requested_keys = _combat_reference_keys(requested)
    matches = []
    for participant in combat.get('participants') or []:
        if not isinstance(participant, dict):
            continue
        if requested_keys.intersection(_combat_participant_reference_keys(participant)):
            matches.append(_text(participant.get('id')))
    unique_matches = {match for match in matches if match}
    if len(unique_matches) == 1:
        return next(iter(unique_matches))
    enemy_ids = [
        _text(participant.get('id'))
        for participant in combat.get('participants') or []
        if isinstance(participant, dict)
        and participant.get('team') == 'enemy'
        and participant.get('isAlive') is not False
        and _text(participant.get('id'))
    ]
    requested_keys = _combat_reference_keys(requested)
    if len(enemy_ids) == 1 and not any(key.startswith('player_') or key.startswith('player ') for key in requested_keys):
        return enemy_ids[0]
    return None


def _combat_participant(state: dict[str, Any], participant_id: str) -> dict[str, Any] | None:
    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    for participant in combat.get('participants') or []:
        if isinstance(participant, dict) and _text(participant.get('id')) == participant_id:
            return participant
    return None


def _state_change_actor_error(
    state: dict[str, Any],
    change: dict[str, Any],
    change_type: str,
    expected_actor_id: str | None,
) -> str | None:
    expected = _actor_ref(expected_actor_id)
    if not expected:
        return None

    if change_type in PLAYER_OWNED_STATE_CHANGE_TYPES:
        if not _actor_matches_expected(_action_value(change, 'actorId', 'actor_id'), expected):
            return 'State change actor does not match the current player.'
        return None

    if change_type in TRANSFER_STATE_CHANGE_TYPES:
        source_actor_id = _action_value(change, 'fromActorId', 'from_actor_id') or _action_value(change, 'actorId', 'actor_id')
        if not _actor_matches_expected(source_actor_id, expected):
            return 'Transfer source actor does not match the current player.'
        return None

    if change_type in PLAYER_COMBAT_PARTICIPANT_CHANGE_TYPES:
        participant_id = _actor_ref(
            _action_value(change, 'participantId', 'participant_id')
            or _action_value(change, 'enemyId', 'enemy_id')
        )
        resolved_id = _resolve_combat_participant_id(state, participant_id) if participant_id else None
        participant = _combat_participant(state, resolved_id) if resolved_id else None
        if isinstance(participant, dict) and participant.get('team') == 'player' and not _actor_matches_expected(participant.get('id'), expected):
            return 'Combat participant change actor does not match the current player.'
    return None


def _authorized_cross_actor_bypass_allowed(change_type: str) -> bool:
    return change_type in AUTHORIZED_CROSS_ACTOR_BYPASS_CHANGE_TYPES


def _combat_start_reopens_resolved_enemy(state: dict[str, Any], combat: dict[str, Any], normalized: dict[str, Any]) -> str | None:
    if normalized.get('allowResolvedEncounterRestart') or normalized.get('allow_resolved_encounter_restart'):
        return None
    existing = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    if str(existing.get('status') or '') not in {'ended', 'none'} and str(scene.get('combatState') or '') not in {'resolved', 'none'}:
        return None
    resolved_signatures: set[str] = set()
    for participant in existing.get('participants') or []:
        if not isinstance(participant, dict) or participant.get('team') != 'enemy':
            continue
        conditions = {str(item or '').lower() for item in participant.get('conditions') or []}
        if not conditions.intersection({'surrendered', 'fled', 'defeated'}) and participant.get('isAlive') is not False:
            continue
        for value in (participant.get('definitionId'), participant.get('name'), participant.get('id')):
            text = stable_slug(value)
            if text:
                resolved_signatures.add(text)
    if not resolved_signatures:
        return None
    for participant in combat.get('participants') or []:
        if not isinstance(participant, dict) or participant.get('team') != 'enemy':
            continue
        for value in (participant.get('definitionId'), participant.get('name'), participant.get('id')):
            if stable_slug(value) in resolved_signatures:
                return str(participant.get('name') or participant.get('id') or 'enemy')
    return None


def _normalize_combat_change(change: dict[str, Any], state: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    change_type = _text(change.get('type'))
    normalized = materialize_campaign_pack_combat_start(state, deepcopy(change))
    turn_id = _turn_value(normalized)
    if turn_id is not None:
        normalized['turnId'] = turn_id

    if change_type == 'combat.start':
        combat_payload = normalized.get('combat') if isinstance(normalized.get('combat'), dict) else normalized
        combat = normalize_combat_state(
            {
                **combat_payload,
                'status': combat_payload.get('status') or 'active',
                'round': combat_payload.get('round') or 1,
            },
            state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {},
        )
        if not combat.get('participants'):
            return 'rejected', 'Combat start requires participants.', None
        if not any(participant.get('team') == 'enemy' for participant in combat['participants']):
            return 'rejected', 'Combat start requires at least one enemy participant.', None
        reopened_enemy = _combat_start_reopens_resolved_enemy(state, combat, normalized)
        if reopened_enemy:
            return 'rejected', f"Combat start would reopen resolved enemy '{reopened_enemy}'.", None
        normalized['combat'] = combat
        return 'accepted', 'Combat start is valid.', normalized

    if change_type == 'combat.update':
        data = normalized.pop('data', None)
        if isinstance(data, dict):
            for key in ('status', 'round', 'turnIndex', 'lastRoundSummary', 'encounterGoal'):
                if key in data and key not in normalized:
                    normalized[key] = data[key]
            if isinstance(data.get('flags'), dict):
                flags = normalized.get('flags') if isinstance(normalized.get('flags'), dict) else {}
                normalized['flags'] = {**flags, **data['flags']}
        status = _text(normalized.get('status'))
        if status and status not in {'none', 'starting', 'active', 'ended'}:
            return 'rejected', f"Unsupported combat status '{status}'.", None
        if normalized.get('round') is not None:
            round_number = int_or_default(normalized.get('round'), default=0)
            if round_number < 1:
                return 'rejected', 'Combat round must be positive.', None
            normalized['round'] = min(999, round_number)
        if normalized.get('flags') is not None and not isinstance(normalized.get('flags'), dict):
            return 'rejected', 'Combat flags must be an object.', None
        return 'accepted', 'Combat update is valid.', normalized

    if change_type == 'combat.round.advance':
        combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
        normalized['round'] = max(1, int_or_default(normalized.get('round'), default=int(combat.get('round') or 1) + 1))
        return 'accepted', 'Combat round advance is valid.', normalized

    if change_type == 'combat.battlefield.update':
        normalized['battlefield'] = normalize_battlefield(
            normalized.get('battlefield'),
            state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {},
        )
        return 'accepted', 'Combat battlefield update is valid.', normalized

    if change_type == 'combat.end':
        status = _text(normalized.get('status') or 'ended')
        if status not in {'ended', 'none'}:
            return 'rejected', 'Combat end status must be ended or none.', None
        normalized['status'] = status
        return 'accepted', 'Combat end is valid.', normalized

    participant_id = _text(normalized.get('participantId') or normalized.get('participant_id') or normalized.get('enemyId') or normalized.get('enemy_id'))
    if not participant_id:
        return 'rejected', 'Combat participant change requires participantId.', None
    resolved_participant_id = _resolve_combat_participant_id(state, participant_id)
    if not resolved_participant_id:
        return 'rejected', f"Combat participant '{participant_id}' was not found.", None
    normalized['participantId'] = resolved_participant_id

    if change_type == 'combat.participant.update':
        if normalized.get('hp') is not None and not isinstance(normalized.get('hp'), dict):
            return 'rejected', 'Combat participant hp must be an object.', None
        if normalized.get('conditions') is not None:
            normalized['conditions'] = _string_list(normalized.get('conditions'))
        if normalized.get('position') is not None and not isinstance(normalized.get('position'), dict):
            return 'rejected', 'Combat participant position must be an object.', None
        if normalized.get('participant') is not None:
            participant = normalize_participant(normalized.get('participant'))
            if not participant:
                return 'rejected', 'Combat participant payload is invalid.', None
            normalized['participant'] = participant
        return 'accepted', 'Combat participant update is valid.', normalized

    if change_type == 'combat.move':
        to_range = _text(normalized.get('toRangeBand') or normalized.get('to_range_band') or normalized.get('rangeBand') or normalized.get('range_band')).lower().replace(' ', '_')
        if to_range not in RANGE_BANDS:
            return 'rejected', 'Combat movement requires a valid toRangeBand.', None
        normalized['toRangeBand'] = to_range
        from_range = _text(normalized.get('fromRangeBand') or normalized.get('from_range_band')).lower().replace(' ', '_')
        if from_range and from_range not in RANGE_BANDS:
            return 'rejected', 'Combat movement fromRangeBand is invalid.', None
        if from_range:
            normalized['fromRangeBand'] = from_range
        return 'accepted', 'Combat movement is valid.', normalized

    if change_type in {'combat.condition.add', 'combat.condition.remove'}:
        condition = _text(normalized.get('condition') or normalized.get('conditionName') or normalized.get('condition_name')).lower().replace(' ', '_')
        if not condition:
            return 'rejected', 'Combat condition change requires condition.', None
        normalized['condition'] = condition
        return 'accepted', 'Combat condition change is valid.', normalized

    if change_type == 'combat.ability.mark_used':
        ability_id = _text(normalized.get('abilityId') or normalized.get('ability_id'))
        if not ability_id:
            return 'rejected', 'Combat ability mark used requires abilityId.', None
        participant = _combat_participant(state, resolved_participant_id)
        ability_ids = {
            _text(ability.get('id'))
            for ability in (participant.get('abilities') if isinstance(participant, dict) else []) or []
            if isinstance(ability, dict) and _text(ability.get('id'))
        }
        if ability_id not in ability_ids:
            return 'rejected', f"Combat ability '{ability_id}' was not found for participant '{resolved_participant_id}'.", None
        normalized['abilityId'] = ability_id
        return 'accepted', 'Combat ability mark used is valid.', normalized

    if change_type == 'combat.intent.set':
        intent = normalized.get('intent') if isinstance(normalized.get('intent'), dict) else {}
        intent_type = _text(intent.get('intentType') or normalized.get('intentType'))
        if not intent_type:
            return 'rejected', 'Combat intent requires intentType.', None
        try:
            confidence = float(intent.get('confidence', normalized.get('confidence', 0.5)) or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        normalized['intent'] = {
            **intent,
            'enemyId': participant_id,
            'intentType': intent_type,
            'reason': _text(intent.get('reason') or normalized.get('reason')) or 'Backend selected enemy intent.',
            'confidence': max(0.0, min(1.0, confidence)),
        }
        return 'accepted', 'Combat intent update is valid.', normalized

    if change_type == 'combat.morale.update':
        morale = int_or_default(normalized.get('morale'), default=-1)
        if morale < 0:
            return 'rejected', 'Combat morale update requires morale.', None
        normalized['morale'] = max(0, min(100, morale))
        return 'accepted', 'Combat morale update is valid.', normalized

    if change_type == 'combat.morale.event':
        event = _text(normalized.get('event') or normalized.get('moraleEvent') or normalized.get('morale_event')).lower()
        if event not in MORALE_EVENTS:
            return 'rejected', 'Combat morale event is unsupported.', None
        participant = _combat_participant(state, participant_id)
        if not participant:
            return 'rejected', f"Combat participant '{participant_id}' was not found.", None
        normalized['event'] = event
        normalized['morale'] = apply_morale_event(participant, event)
        return 'accepted', 'Combat morale event is valid.', normalized

    return 'rejected', 'Unsupported combat state change.', None


def _atomic_change_id(parent_id: str, suffix: str, *parts: Any) -> str:
    if parent_id:
        return f'{parent_id}:{suffix}'
    return stable_change_id('transfer', suffix, *parts)


def _transfer_source_actor(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    return find_actor(state, _action_value(change, 'fromActorId', 'from_actor_id') or _action_value(change, 'actorId', 'actor_id'))


def _change_id_already_seen(change: dict[str, Any], applied_ids: set[str], seen_ids: set[str]) -> bool:
    change_id = str(change.get('id') or '').strip()
    return bool(change_id and (change_id in applied_ids or change_id in seen_ids))


def _inventory_reservation_key(actor: dict[str, Any], item: dict[str, Any]) -> tuple[str, str] | None:
    actor_id = str(actor.get('id') or '').strip()
    item_key = str(item.get('id') or '').strip() or normalize_item_name(item.get('name'))
    if not actor_id or not item_key:
        return None
    return actor_id, item_key


def _inventory_item_from_change(actor: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    item_id = _action_value(change, 'itemId', 'item_id')
    item_name = _action_value(change, 'itemName', 'item_name')
    if item_id:
        return next((candidate for candidate in actor_items(actor) if str(candidate.get('id')) == str(item_id)), None)
    return next(
        (
            candidate
            for candidate in actor_items(actor)
            if item_name and normalize_item_name(candidate.get('name')) == normalize_item_name(item_name)
        ),
        None,
    )


def _reserve_inventory_quantity(
    actor: dict[str, Any],
    item: dict[str, Any],
    quantity: int,
    reservations: dict[tuple[str, str], int],
) -> str | None:
    key = _inventory_reservation_key(actor, item)
    if not key:
        return 'Inventory transfer source item is invalid.'
    available = max(0, int_or_default(item.get('quantity'), default=1))
    reserved = max(0, int_or_default(reservations.get(key), default=0))
    remaining = max(0, available - reserved)
    if quantity > remaining:
        return f"Insufficient quantity. Available: {remaining}."
    reservations[key] = reserved + quantity
    return None


def _reserve_inventory_remove_change(
    state: dict[str, Any],
    change: dict[str, Any],
    reservations: dict[tuple[str, str], int],
) -> str | None:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'Actor not found.'
    item = _inventory_item_from_change(actor, change)
    if not item:
        return 'Item not found in inventory.'
    quantity = max(0, int_or_default(change.get('quantity'), default=0))
    return _reserve_inventory_quantity(actor, item, quantity, reservations)


def _reserve_currency_amount(
    actor: dict[str, Any],
    currency: str,
    amount: int,
    reservations: dict[tuple[str, str], int],
) -> str | None:
    actor_id = str(actor.get('id') or '').strip()
    key = (actor_id, currency)
    available = max(0, int_or_default(actor_currency(actor).get(currency), default=0))
    reserved = max(0, int_or_default(reservations.get(key), default=0))
    remaining = max(0, available - reserved)
    if amount > remaining:
        return f"Insufficient {currency}. Available: {remaining}."
    reservations[key] = reserved + amount
    return None


def _reserve_currency_remove_change(
    state: dict[str, Any],
    change: dict[str, Any],
    reservations: dict[tuple[str, str], int],
) -> str | None:
    actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
    if not actor:
        return 'Actor not found.'
    currency = str(change.get('currency') or '').strip().lower()
    amount = max(0, int_or_default(change.get('amount'), default=0))
    return _reserve_currency_amount(actor, currency, amount, reservations)


def _transfer_reason(change: dict[str, Any], fallback: str) -> str:
    reason = str(change.get('reason') or '').strip()
    if not reason or reason == GENERIC_EXTRACTED_REASON:
        return fallback
    return reason


def _validate_inventory_transfer_change(
    state: dict[str, Any],
    change: dict[str, Any],
    *,
    applied_ids: set[str],
    seen_ids: set[str],
    inventory_reservations: dict[tuple[str, str], int],
    inventory_add_reservations: dict[tuple[str, str], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = _transfer_source_actor(state, change)
    if not source:
        return [], [_rejected(change, 'Transfer source actor not found.')]
    target, target_error = _target_actor_from_payload(state, change)
    if not target:
        return [], [_rejected(change, target_error or 'Transfer target actor not found.')]
    if str(source.get('id')) == str(target.get('id')):
        return [], [_rejected(change, 'Transfer target must be different from the source actor.')]

    item_id = _action_value(change, 'itemId', 'item_id')
    item_name = _action_value(change, 'itemName', 'item_name')
    if item_id:
        source_item = next((candidate for candidate in actor_items(source) if str(candidate.get('id')) == str(item_id)), None)
    else:
        source_item = next(
            (
                candidate
                for candidate in actor_items(source)
                if item_name and normalize_item_name(candidate.get('name')) == normalize_item_name(item_name)
            ),
            None,
        )
    if not source_item:
        return [], [_rejected(change, 'Item not found in source inventory.')]

    quantity = max(0, int_or_default(change.get('quantity'), default=0))
    if quantity <= 0:
        return [], [_rejected(change, 'Inventory transfer quantity must be positive.')]

    parent_id = str(change.get('id') or '').strip()
    source_name = actor_name(source)
    target_name = actor_name(target)
    remove_change_id = _atomic_change_id(parent_id, 'remove', source.get('id'), source_item.get('id'), quantity)
    add_change_id = _atomic_change_id(parent_id, 'add', target.get('id'), source_item.get('id'), quantity)
    remove_change = {
        **change,
        'id': remove_change_id,
        'type': 'inventory.remove',
        'actorId': source.get('id'),
        'fromActorName': source_name,
        'toActorName': target_name,
        'itemId': source_item.get('id'),
        'itemName': source_item.get('name'),
        'quantity': quantity,
        'reason': _transfer_reason(change, f"{source_name} gave {source_item.get('name')} x{quantity} to {target_name}."),
        'transferId': parent_id or None,
        'transferDirection': 'source',
    }
    item_payload = deepcopy(source_item)
    item_payload['quantity'] = quantity
    available = max(1, int_or_default(source_item.get('quantity'), default=1))
    moved_item_id = (
        _split_stack_item_id(
            transfer_id=add_change_id,
            source_item_id=_text(source_item.get('id')),
            destination_id=_text(target.get('id')),
        )
        if quantity < available
        else _text(source_item.get('id'))
    )
    item_payload['id'] = moved_item_id
    if moved_item_id != _text(source_item.get('id')):
        item_payload['splitFromItemId'] = source_item.get('id')
    add_change = {
        **change,
        'id': add_change_id,
        'type': 'inventory.add',
        'actorId': target.get('id'),
        'fromActorName': source_name,
        'toActorName': target_name,
        'itemId': moved_item_id,
        'itemName': source_item.get('name'),
        'quantity': quantity,
        'item': item_payload,
        'reason': _transfer_reason(change, f"{target_name} received {source_item.get('name')} x{quantity} from {source_name}."),
        'transferId': parent_id or None,
        'transferDirection': 'target',
        **(
            {'sourceItemId': source_item.get('id'), 'splitFromItemId': source_item.get('id')}
            if moved_item_id != _text(source_item.get('id'))
            else {}
        ),
    }
    if _change_id_already_seen(remove_change, applied_ids, seen_ids) or _change_id_already_seen(add_change, applied_ids, seen_ids):
        return [], [_rejected(change, 'State transfer was already applied.')]

    remove_status, remove_reason, normalized_remove = _validate_inventory_change(
        state,
        remove_change,
        pending_adds=inventory_add_reservations,
    )
    add_status, add_reason, normalized_add = _validate_inventory_change(
        state,
        add_change,
        pending_adds=inventory_add_reservations,
    )
    if remove_status != 'accepted' or add_status != 'accepted':
        reasons = [reason for status, reason in ((remove_status, remove_reason), (add_status, add_reason)) if status != 'accepted']
        return [], [_rejected(change, '; '.join(reasons) or 'Inventory transfer validation failed.')]
    reservation_error = _reserve_inventory_quantity(source, source_item, quantity, inventory_reservations)
    if reservation_error:
        return [], [_rejected(change, reservation_error)]
    _reserve_inventory_add(target, normalized_add or add_change, inventory_add_reservations)

    accepted = [
        _accepted(normalized_remove or remove_change, 'Inventory transfer source removal is valid.'),
        _accepted(normalized_add or add_change, 'Inventory transfer target add is valid.'),
    ]
    for entry in accepted:
        atomic_id = str(entry['change'].get('id') or '').strip()
        if atomic_id:
            seen_ids.add(atomic_id)
    return accepted, []


def _validate_currency_transfer_change(
    state: dict[str, Any],
    change: dict[str, Any],
    *,
    applied_ids: set[str],
    seen_ids: set[str],
    currency_reservations: dict[tuple[str, str], int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = _transfer_source_actor(state, change)
    if not source:
        return [], [_rejected(change, 'Transfer source actor not found.')]
    target, target_error = _target_actor_from_payload(state, change)
    if not target:
        return [], [_rejected(change, target_error or 'Transfer target actor not found.')]
    if str(source.get('id')) == str(target.get('id')):
        return [], [_rejected(change, 'Transfer target must be different from the source actor.')]

    currency = str(change.get('currency') or '').strip().lower()
    amount = max(0, int_or_default(change.get('amount'), default=0))
    parent_id = str(change.get('id') or '').strip()
    source_name = actor_name(source)
    target_name = actor_name(target)
    remove_change = {
        **change,
        'id': _atomic_change_id(parent_id, 'remove', source.get('id'), currency, amount),
        'type': 'currency.remove',
        'actorId': source.get('id'),
        'fromActorName': source_name,
        'toActorName': target_name,
        'currency': currency,
        'amount': amount,
        'reason': _transfer_reason(change, f"{source_name} gave {amount} {currency} to {target_name}."),
        'transferId': parent_id or None,
        'transferDirection': 'source',
    }
    add_change = {
        **change,
        'id': _atomic_change_id(parent_id, 'add', target.get('id'), currency, amount),
        'type': 'currency.add',
        'actorId': target.get('id'),
        'fromActorName': source_name,
        'toActorName': target_name,
        'currency': currency,
        'amount': amount,
        'reason': _transfer_reason(change, f"{target_name} received {amount} {currency} from {source_name}."),
        'transferId': parent_id or None,
        'transferDirection': 'target',
    }
    if _change_id_already_seen(remove_change, applied_ids, seen_ids) or _change_id_already_seen(add_change, applied_ids, seen_ids):
        return [], [_rejected(change, 'State transfer was already applied.')]

    remove_status, remove_reason, normalized_remove = _validate_currency_change(state, remove_change)
    add_status, add_reason, normalized_add = _validate_currency_change(state, add_change)
    if remove_status != 'accepted' or add_status != 'accepted':
        reasons = [reason for status, reason in ((remove_status, remove_reason), (add_status, add_reason)) if status != 'accepted']
        return [], [_rejected(change, '; '.join(reasons) or 'Currency transfer validation failed.')]
    reservation_error = _reserve_currency_amount(source, currency, amount, currency_reservations)
    if reservation_error:
        return [], [_rejected(change, reservation_error)]

    accepted = [
        _accepted(normalized_remove or remove_change, 'Currency transfer source removal is valid.'),
        _accepted(normalized_add or add_change, 'Currency transfer target add is valid.'),
    ]
    for entry in accepted:
        atomic_id = str(entry['change'].get('id') or '').strip()
        if atomic_id:
            seen_ids.add(atomic_id)
    return accepted, []


def validate_state_changes(
    *,
    state: dict[str, Any],
    changes: list[dict[str, Any]],
    expected_actor_id: str | None = None,
    authorized_cross_actor_change_ids: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    modified: list[dict[str, Any]] = []
    applied_ids = state_applied_change_ids(state)
    seen_ids: set[str] = set()
    inventory_reservations: dict[tuple[str, str], int] = {}
    inventory_add_reservations: dict[tuple[str, str], list[dict[str, Any]]] = {}
    currency_reservations: dict[tuple[str, str], int] = {}
    authorized_cross_actor_ids = {
        str(change_id).strip()
        for change_id in (authorized_cross_actor_change_ids or [])
        if str(change_id or '').strip()
    }

    for raw_change in changes:
        if not isinstance(raw_change, dict):
            continue
        change = deepcopy(raw_change)
        npc_condition_change = _noncombat_condition_as_npc_update(change, state)
        if npc_condition_change:
            change = npc_condition_change
        change_type = str(change.get('type') or '').strip()
        change_id = str(change.get('id') or '').strip()
        if change_type not in STATE_CHANGE_TYPES:
            rejected.append(_rejected(change, f"Unsupported state change type '{change_type}'."))
            continue
        actor_error = None
        if not (
            change_id
            and change_id in authorized_cross_actor_ids
            and _authorized_cross_actor_bypass_allowed(change_type)
        ):
            actor_error = _state_change_actor_error(state, change, change_type, expected_actor_id)
        if actor_error:
            rejected.append(_rejected(change, actor_error))
            continue
        if change_type == 'inventory.transfer':
            transfer_accepted, transfer_rejected = _validate_inventory_transfer_change(
                state,
                change,
                applied_ids=applied_ids,
                seen_ids=seen_ids,
                inventory_reservations=inventory_reservations,
                inventory_add_reservations=inventory_add_reservations,
            )
            accepted.extend(transfer_accepted)
            rejected.extend(transfer_rejected)
            continue
        if change_type == 'currency.transfer':
            transfer_accepted, transfer_rejected = _validate_currency_transfer_change(
                state,
                change,
                applied_ids=applied_ids,
                seen_ids=seen_ids,
                currency_reservations=currency_reservations,
            )
            accepted.extend(transfer_accepted)
            rejected.extend(transfer_rejected)
            continue
        if change_id and (change_id in applied_ids or change_id in seen_ids):
            rejected.append(_rejected(change, 'State change was already applied.'))
            continue
        if change_id:
            seen_ids.add(change_id)

        if change_type in {'inventory.add', 'inventory.remove'}:
            status, reason, normalized = _validate_inventory_change(
                state,
                change,
                pending_adds=inventory_add_reservations,
            )
            if status == 'accepted' and change_type == 'inventory.add':
                drift_status, drift_reason, drift_normalized = _apply_campaign_pack_inventory_drift_control(state, normalized or change)
                if drift_status != 'accepted':
                    status = drift_status
                    reason = drift_reason or reason
                    normalized = drift_normalized
        elif change_type in {'inventory.equip', 'inventory.unequip'}:
            status, reason, normalized = _validate_equipment_change(state, change)
        elif change_type in {'currency.add', 'currency.remove'}:
            status, reason, normalized = _validate_currency_change(state, change)
        elif change_type in {'health.heal', 'health.damage', 'health.max.set'}:
            status, reason, normalized = _validate_health_change(state, change)
        elif change_type in {'xp.add', 'xp.remove'}:
            status, reason, normalized = _validate_xp_change(state, change)
        elif change_type == 'spell.learn':
            status, reason, normalized = _validate_spell_learn_change(state, change)
        elif change_type == 'spell.cast':
            actor = find_actor(state, _action_value(change, 'actorId', 'actor_id'))
            if not actor:
                status, reason, normalized = 'rejected', 'Spell cast actor was not found.', None
            else:
                requested_level = _action_value(change, 'castLevel', 'cast_level')
                legality = spell_cast_legality(
                    actor.get('spellbook'),
                    actor.get('spellResources'),
                    spell_name_or_id=_action_value(change, 'spellId', 'spell_id')
                    or _action_value(change, 'spellName', 'spell_name'),
                    class_name=_text(actor.get('class')),
                    level=max(1, int_or_default(actor.get('level'), default=1)),
                    cast_level=(int_or_default(requested_level, default=0) if requested_level is not None else None),
                    resource_pool=_text(_action_value(change, 'resourcePool', 'resource_pool')) or 'auto',
                )
                if not legality.get('legal'):
                    status, reason, normalized = 'rejected', _text(legality.get('reason')) or 'Spell cast is not legal.', None
                elif change.get('resolutionAuthority') or change.get('resolution'):
                    status, reason, normalized = _validate_authoritative_spell_change(
                        change,
                        state,
                        actor,
                        legality,
                    )
                else:
                    spell = legality.get('spell') if isinstance(legality.get('spell'), dict) else {}
                    resource = legality.get('resource') if isinstance(legality.get('resource'), dict) else {}
                    normalized = {
                        **change,
                        'spellId': spell.get('id'),
                        'spellName': spell.get('name'),
                        'castLevel': legality.get('castLevel'),
                        'resourcePool': resource.get('pool'),
                    }
                    status, reason = 'accepted', _text(legality.get('reason')) or 'Spell cast resource is available.'
        elif change_type == 'class_feature.use':
            status, reason, normalized = _validate_class_feature_change(change, state)
        elif change_type == 'scene.interactable.action':
            status, reason, normalized = _validate_interactable_change(change, state)
        elif change_type == 'rest.complete':
            rest_result = _validate_rest_action(
                {
                    **change,
                    'type': 'rest.complete',
                    'restType': _action_value(change, 'restType', 'rest_type'),
                },
                state,
                current_turn=max(1, int_or_default(change.get('turnId', change.get('turn_id')), default=1)),
            )
            if rest_result.get('status') != 'valid':
                status, reason, normalized = 'rejected', _text(rest_result.get('reason')) or 'Rest is not legal.', None
            else:
                normalized = {**change, 'restType': (rest_result.get('normalizedAction') or {}).get('restType')}
                status, reason = 'accepted', _text(rest_result.get('reason')) or 'Rest is valid.'
        elif change_type == 'inventory.mark_used':
            status, reason, normalized = 'accepted', 'Inventory use marker is valid.', None
        elif change_type == 'race_ability.mark_used':
            ability_id = str(change.get('abilityId') or change.get('ability_id') or '').strip()
            refreshes_on = str(change.get('refreshesOn') or change.get('refreshes_on') or '').strip()
            if not change.get('actorId') and not change.get('actor_id'):
                status, reason, normalized = 'rejected', 'Race ability use requires actorId.'
            elif not ability_id:
                status, reason, normalized = 'rejected', 'Race ability use requires abilityId.'
            elif refreshes_on not in {'short_rest', 'long_rest', 'session'}:
                status, reason, normalized = 'rejected', 'Race ability use requires a supported refreshesOn value.'
            else:
                status, reason, normalized = 'accepted', 'Race ability use marker is valid.', None
        elif change_type == 'race_ability.refresh':
            ability_id = str(change.get('abilityId') or change.get('ability_id') or '').strip()
            if not change.get('actorId') and not change.get('actor_id'):
                status, reason, normalized = 'rejected', 'Race ability refresh requires actorId.'
            elif not ability_id:
                status, reason, normalized = 'rejected', 'Race ability refresh requires abilityId.'
            else:
                status, reason, normalized = 'accepted', 'Race ability refresh marker is valid.', None
        elif change_type in COMBAT_STATE_CHANGE_TYPES:
            status, reason, normalized = _normalize_combat_change(change, state)
            if status == 'accepted' and normalized:
                drift_status, drift_reason, drift_normalized = _apply_campaign_pack_combat_drift_control(state, normalized)
                if drift_status != 'accepted':
                    status = drift_status
                    reason = drift_reason or reason
                    normalized = drift_normalized
        elif change_type in WORLD_STATE_CHANGE_TYPES:
            status, reason, normalized = _normalize_world_change(change, state)
            if status == 'accepted' and normalized:
                drift_status, drift_reason, drift_normalized = _apply_campaign_pack_drift_control(state, normalized)
                if drift_status != 'accepted':
                    status = drift_status
                    reason = drift_reason or reason
                    normalized = drift_normalized
        else:
            status, reason, normalized = 'rejected', 'State pipeline does not apply this change directly.', None

        if status == 'accepted':
            accepted_change = normalized or change
            if change_type == 'inventory.remove':
                reservation_error = _reserve_inventory_remove_change(state, accepted_change, inventory_reservations)
                if reservation_error:
                    rejected.append(_rejected(change, reservation_error))
                    continue
            if change_type == 'currency.remove':
                reservation_error = _reserve_currency_remove_change(state, accepted_change, currency_reservations)
                if reservation_error:
                    rejected.append(_rejected(change, reservation_error))
                    continue
            if change_type == 'inventory.add':
                actor = find_actor(state, _action_value(accepted_change, 'actorId', 'actor_id'))
                if actor:
                    _reserve_inventory_add(actor, accepted_change, inventory_add_reservations)
            accepted.append(_accepted(normalized or change, reason))
        elif status == 'modified' and normalized:
            if change_type == 'inventory.add':
                actor = find_actor(state, _action_value(normalized, 'actorId', 'actor_id'))
                if actor:
                    _reserve_inventory_add(actor, normalized, inventory_add_reservations)
            modified.append(_modified(change, normalized, reason))
        else:
            rejected.append(_rejected(change, reason))

    return {'accepted': accepted, 'rejected': rejected, 'modified': modified}


def validated_changes_for_application(validation_result: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for entry in validation_result.get('accepted') or []:
        if isinstance(entry, dict) and isinstance(entry.get('change'), dict):
            changes.append(entry['change'])
    for entry in validation_result.get('modified') or []:
        if isinstance(entry, dict) and isinstance(entry.get('modifiedChange'), dict):
            changes.append(entry['modifiedChange'])
    return changes
