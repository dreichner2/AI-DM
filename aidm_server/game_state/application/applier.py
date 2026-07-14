from __future__ import annotations

from copy import deepcopy
import re
import secrets
from typing import Any

from aidm_server.armor_class import sync_actor_armor_class
from aidm_server.canon_text import int_or_default
from aidm_server.class_capabilities import restore_class_capabilities, spend_capability
from aidm_server.character_resources import (
    consume_spell_cast,
    ensure_character_sheet_spell_resources,
    restore_spell_resources,
)
from aidm_server.combat.rewards import SOURCE as COMBAT_REWARD_SOURCE, derive_combat_outcome_rewards
from aidm_server.combat.state import ensure_combat_state, normalize_battlefield, normalize_combat_state, normalize_participant, normalize_position
from aidm_server.game_state.campaign_pack_encounters import materialize_campaign_pack_combat_start
from aidm_server.game_state.equipment import conflict_items, equipment_slot_label, infer_equipment_slot
from aidm_server.game_state.models import (
    CURRENCY_CODES,
    actor_currency,
    actor_items,
    append_change_ledger,
    dump_inventory_items,
    find_actor,
    normalize_item_name,
    parse_actor_player_id,
    stable_slug,
    stable_item_id,
    stable_item_instance_id,
    stats_with_currency,
)
from aidm_server.game_state.leveling import sync_actor_level_for_xp, sync_stats_for_level
from aidm_server.game_state.quest_engine import derive_quest_changes
from aidm_server.models import Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.spellbook import (
    character_sheet_record,
    ensure_character_sheet_spellbook,
    known_spell_names,
    merge_spellbooks,
    normalize_spellbook,
    spell_from_change,
)
from aidm_server.spell_effects import advance_spell_effect_durations, resolve_concentration_check
from aidm_server.time_utils import utc_now


def _change_value(change: dict[str, Any], camel_key: str, snake_key: str | None = None, default=None):
    if camel_key in change:
        return change.get(camel_key)
    if snake_key and snake_key in change:
        return change.get(snake_key)
    return default


def _find_item(items: list[dict[str, Any]], *, item_id: str | None = None, item_name: str | None = None) -> dict[str, Any] | None:
    if item_id:
        return next((item for item in items if str(item.get('id')) == str(item_id)), None)
    requested = normalize_item_name(item_name)
    if requested:
        return next((item for item in items if normalize_item_name(item.get('name')) == requested), None)
    return None


def _item_payload(change: dict[str, Any]) -> dict[str, Any]:
    raw_item = change.get('item') if isinstance(change.get('item'), dict) else {}
    name = str(raw_item.get('name') or change.get('itemName') or change.get('item_name') or '').strip()
    quantity = max(1, int_or_default(raw_item.get('quantity', change.get('quantity')), default=1))
    item_id = str(raw_item.get('id') or raw_item.get('itemId') or change.get('itemId') or stable_item_id(name)).strip()
    payload = {
        **raw_item,
        'id': item_id,
        'name': name,
        'quantity': quantity,
        'type': raw_item.get('type') or change.get('itemType') or change.get('item_type') or 'misc',
    }
    source_actor_id = str(change.get('sourceActorId') or change.get('fromActorId') or raw_item.get('sourceActorId') or '').strip()
    if source_actor_id:
        payload['sourceActorId'] = source_actor_id
    return payload


def _items_are_stack_compatible(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    for key in ('name', 'type', 'subtype', 'rarity', 'damageType', 'damage_type', 'slot'):
        existing_value = normalize_item_name(existing.get(key))
        incoming_value = normalize_item_name(incoming.get(key))
        if existing_value and incoming_value and existing_value != incoming_value:
            return False
    for key in ('damage', 'damageDice', 'damage_dice', 'armorClass', 'armor_class', 'acBonus', 'ac_bonus', 'effect', 'effects'):
        existing_value = existing.get(key)
        incoming_value = incoming.get(key)
        if existing_value not in (None, '', [], {}) and incoming_value not in (None, '', [], {}) and existing_value != incoming_value:
            return False
    return True


def _merge_item(items: list[dict[str, Any]], incoming: dict[str, Any]) -> dict[str, Any] | None:
    incoming_id = str(incoming.get('id') or '').strip()
    identity_matches = [item for item in items if incoming_id and str(item.get('id') or '').strip() == incoming_id]
    if len(identity_matches) > 1:
        return None
    existing = _find_item(
        items,
        item_id=incoming_id or None,
        item_name=None if incoming_id else str(incoming.get('name') or ''),
    )
    if existing:
        if not _items_are_stack_compatible(existing, incoming):
            return None
        existing['quantity'] = max(0, int_or_default(existing.get('quantity'), default=0)) + max(
            1,
            int_or_default(incoming.get('quantity'), default=1),
        )
        for key, value in incoming.items():
            if key not in {'quantity'} and value not in (None, '', [], {}):
                existing.setdefault(key, value)
        return existing
    items.append(incoming)
    return incoming


def _merge_scene_item(items: list[dict[str, Any]], incoming: dict[str, Any]) -> dict[str, Any] | None:
    item = _merge_item(items, incoming)
    if item and incoming.get('sourceActorId') and not item.get('sourceActorId'):
        item['sourceActorId'] = incoming.get('sourceActorId')
    return item


def _quest_reward_collision_item_id(change: dict[str, Any], actor: dict[str, Any], payload: dict[str, Any]) -> str:
    return stable_item_instance_id(
        'quest_reward_collision',
        change.get('id'),
        actor.get('id'),
        payload.get('id'),
        payload.get('name'),
        prefix='itm_reward',
    )


def _remove_item(items: list[dict[str, Any]], change: dict[str, Any]) -> dict[str, Any] | None:
    item = _find_item(
        items,
        item_id=_change_value(change, 'itemId', 'item_id'),
        item_name=_change_value(change, 'itemName', 'item_name'),
    )
    if not item:
        return None
    quantity = max(1, int_or_default(change.get('quantity'), default=1))
    item['quantity'] = max(0, int_or_default(item.get('quantity'), default=1) - quantity)
    if item['quantity'] <= 0:
        items.remove(item)
    return item


def _equip_item(items: list[dict[str, Any]], change: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    item = _find_item(
        items,
        item_id=_change_value(change, 'itemId', 'item_id'),
        item_name=_change_value(change, 'itemName', 'item_name'),
    )
    if not item:
        return None, [], None
    slot = infer_equipment_slot(item, requested_slot=_change_value(change, 'slot', 'equipment_slot'), equipped_items=items)
    if not slot:
        return item, [], None
    conflicts = conflict_items(items, item, slot)
    for conflict in conflicts:
        conflict['equipped'] = False
        conflict['slot'] = conflict.get('slot') or infer_equipment_slot(conflict) or 'none'
    item['equipped'] = True
    item['slot'] = slot
    if change.get('turnId') or change.get('turn_id'):
        item['lastEquippedAtTurn'] = change.get('turnId') or change.get('turn_id')
    return item, conflicts, slot


def _unequip_item(items: list[dict[str, Any]], change: dict[str, Any]) -> dict[str, Any] | None:
    item = _find_item(
        items,
        item_id=_change_value(change, 'itemId', 'item_id'),
        item_name=_change_value(change, 'itemName', 'item_name'),
    )
    if not item:
        return None
    item['equipped'] = False
    item['slot'] = item.get('slot') or infer_equipment_slot(item) or 'none'
    return item


def _sync_actor_and_combat_armor_class(state: dict[str, Any], actor: dict[str, Any] | None) -> int:
    armor_class = sync_actor_armor_class(actor)
    if not isinstance(actor, dict):
        return armor_class
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    participants = combat.get('participants') if isinstance(combat.get('participants'), list) else []
    actor_id = str(actor.get('id') or '')
    player_id = parse_actor_player_id(actor_id) or actor.get('playerId')
    participant_ids = {actor_id}
    if player_id:
        participant_ids.add(f'player_{player_id}')
    breakdown = (actor.get('metadata') or {}).get('armorClassBreakdown') if isinstance(actor.get('metadata'), dict) else None
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        participant_id = str(participant.get('id') or '')
        participant_player_id = participant.get('playerId') or parse_actor_player_id(participant_id)
        if participant_id not in participant_ids and (not player_id or str(participant_player_id) != str(player_id)):
            continue
        participant['armorClass'] = armor_class
        stats = participant.setdefault('stats', {})
        if not isinstance(stats, dict):
            stats = {}
            participant['stats'] = stats
        stats['armorClass'] = armor_class
        stats['armor_class'] = armor_class
        if breakdown:
            participant['armorClassBreakdown'] = breakdown
    return armor_class


def _apply_currency(actor: dict[str, Any], change: dict[str, Any], direction: int) -> int:
    currency_code = str(change.get('currency') or '').strip().lower()
    if currency_code not in CURRENCY_CODES:
        return 0
    amount = max(0, int_or_default(change.get('amount'), default=0))
    inventory = actor.setdefault('inventory', {})
    currency = inventory.setdefault('currency', {})
    current = max(0, int_or_default(currency.get(currency_code), default=0))
    if direction < 0:
        actual = min(current, amount)
        currency[currency_code] = current - actual
        return -actual
    currency[currency_code] = current + amount
    return amount


def _apply_health_heal(actor: dict[str, Any], change: dict[str, Any]) -> int:
    amount = max(0, int_or_default(change.get('amount'), default=0))
    health = actor.setdefault('health', {})
    current_hp = max(0, int_or_default(health.get('currentHp'), default=0))
    max_hp = max(0, int_or_default(health.get('maxHp'), default=0))
    if max_hp:
        actual = max(0, min(amount, max_hp - current_hp))
        health['currentHp'] = min(max_hp, current_hp + amount)
    else:
        actual = amount
        health['currentHp'] = current_hp + amount
    return actual


def _apply_health_max_set(actor: dict[str, Any], change: dict[str, Any]) -> dict[str, int]:
    health = actor.setdefault('health', {})
    old_max = max(0, int_or_default(health.get('maxHp'), default=0))
    old_current = max(0, int_or_default(health.get('currentHp'), default=0))
    new_max = max(1, int_or_default(change.get('maxHp', change.get('amount')), default=old_max or 1))
    if change.get('currentHp') is not None:
        new_current = max(0, min(new_max, int_or_default(change.get('currentHp'), default=old_current)))
    elif change.get('healToMax') or change.get('setCurrentToMax'):
        new_current = new_max
    else:
        new_current = min(old_current, new_max)
    health['maxHp'] = new_max
    health['currentHp'] = new_current
    return {
        'oldMaxHp': old_max,
        'newMaxHp': new_max,
        'oldCurrentHp': old_current,
        'newCurrentHp': new_current,
        'maxHpDelta': new_max - old_max,
        'currentHpDelta': new_current - old_current,
    }


def _apply_health_damage(actor: dict[str, Any], change: dict[str, Any]) -> dict[str, int]:
    amount = max(0, int_or_default(change.get('amount'), default=0))
    health = actor.setdefault('health', {})
    current_hp = max(0, int_or_default(health.get('currentHp'), default=0))
    temp_hp = max(0, int_or_default(health.get('tempHp'), default=0))
    temp_damage = min(temp_hp, amount)
    remaining = amount - temp_damage
    hp_damage = min(current_hp, remaining)
    health['tempHp'] = temp_hp - temp_damage
    health['currentHp'] = current_hp - hp_damage
    return {'amount': temp_damage + hp_damage, 'tempHpDamage': temp_damage, 'hpDamage': hp_damage}


def _apply_xp(actor: dict[str, Any], change: dict[str, Any], direction: int) -> int:
    amount = max(0, int_or_default(change.get('amount'), default=0))
    xp = actor.setdefault('xp', {})
    current = max(0, int_or_default(xp.get('current'), default=0))
    if direction < 0:
        actual = min(current, amount)
        xp['current'] = current - actual
        sync_actor_level_for_xp(actor)
        return actual
    xp['current'] = current + amount
    sync_actor_level_for_xp(actor)
    return amount


def _apply_spell_learn(actor: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    spell = spell_from_change(change)
    if not spell:
        return None
    existing = normalize_spellbook(actor.get('spellbook') if isinstance(actor.get('spellbook'), dict) else {})
    known_before = {normalize_item_name(candidate.get('name')) for candidate in existing.get('knownSpells', []) if isinstance(candidate, dict)}
    merged = merge_spellbooks(existing, {'knownSpells': [spell]})
    actor['spellbook'] = merged
    actor['spells'] = known_spell_names(merged)
    return {
        'spellId': spell.get('id'),
        'spellName': spell.get('name'),
        'spellLevel': spell.get('level'),
        'alreadyKnown': normalize_item_name(spell.get('name')) in known_before,
    }


def _apply_spell_cast(
    state: dict[str, Any],
    actor: dict[str, Any],
    change: dict[str, Any],
) -> dict[str, Any] | None:
    """Consume a validated spell resource on the authoritative actor snapshot."""

    cast_level = _change_value(change, 'castLevel', 'cast_level')
    result = consume_spell_cast(
        actor.get('spellbook'),
        actor.get('spellResources'),
        spell_name_or_id=(
            _change_value(change, 'spellId', 'spell_id')
            or _change_value(change, 'spellName', 'spell_name')
        ),
        class_name=_text(actor.get('class')),
        level=max(1, int_or_default(actor.get('level'), default=1)),
        cast_level=(int_or_default(cast_level, default=0) if cast_level is not None else None),
        resource_pool=_text(_change_value(change, 'resourcePool', 'resource_pool')) or 'auto',
        concentration=(
            False
            if _text(change.get('resolutionAuthority')) == 'spell_effect_engine'
            else (bool(change.get('concentration')) if change.get('concentration') is not None else None)
        ),
        caster_actor_id=_text(actor.get('id')) or None,
        target_ids=[
            _text(target_id)
            for target_id in (change.get('targetIds') or change.get('target_ids') or [])
            if _text(target_id)
        ],
        started_at_turn=int_or_default(change.get('turnId', change.get('turn_id')), default=0) or None,
    )
    if not result.get('ok'):
        return None
    legality = result.get('legality') if isinstance(result.get('legality'), dict) else {}
    spell = legality.get('spell') if isinstance(legality.get('spell'), dict) else {}
    consumed = result.get('consumed') if isinstance(result.get('consumed'), dict) else None
    resources = result['resources']
    spell_resolution = None
    if _text(change.get('resolutionAuthority')) == 'spell_effect_engine':
        resolved_combat = change.get('resolvedCombat') if isinstance(change.get('resolvedCombat'), dict) else None
        effect_resources = (
            change.get('effectCasterResources')
            if isinstance(change.get('effectCasterResources'), dict)
            else None
        )
        if resolved_combat is None or effect_resources is None:
            return None
        prior_concentration = deepcopy(resources.get('concentration'))
        resources['concentration'] = deepcopy(effect_resources.get('concentration'))
        if prior_concentration != resources.get('concentration'):
            resources['revision'] = max(
                int_or_default(resources.get('revision'), default=0),
                int_or_default(effect_resources.get('revision'), default=0),
            ) + 1
        state['combat'] = deepcopy(resolved_combat)
        for participant in state['combat'].get('participants') or []:
            if isinstance(participant, dict):
                _sync_player_actor_from_combat_participant(state, participant)
        spell_resolution = deepcopy(change.get('resolution'))
    actor['spellResources'] = resources
    return {
        'spellId': spell.get('id'),
        'spellName': spell.get('name'),
        'castLevel': legality.get('castLevel'),
        'resourcePool': (consumed or legality.get('resource') or {}).get('pool'),
        'resourceSlotLevel': (consumed or legality.get('resource') or {}).get('slotLevel'),
        'resourceConsumed': bool(consumed),
        'replacedConcentration': result.get('replacedConcentration'),
        'remainingSpellResources': deepcopy(resources),
        **({'spellResolution': spell_resolution} if spell_resolution is not None else {}),
    }


def _apply_class_feature_use(
    state: dict[str, Any],
    actor: dict[str, Any],
    change: dict[str, Any],
) -> dict[str, Any] | None:
    resolution = change.get('resolution') if isinstance(change.get('resolution'), dict) else {}
    effect_type = _text(resolution.get('effectType')).lower()
    target: dict[str, Any] | None = None
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    economy = flags.get('turnEconomy') if isinstance(flags.get('turnEconomy'), dict) else {}

    # Resolve every prerequisite before mutating either the resource pool or
    # combat economy.  Validation normally guarantees these references, but
    # application remains fail-closed if a stale snapshot slips through.
    if effect_type == 'heal':
        target = find_actor(state, resolution.get('targetId'))
        if not target:
            return None
    elif effect_type == 'restore_action':
        if not economy or _text(combat.get('status')).lower() != 'active':
            return None
    else:
        return None

    resource_cost = max(1, int_or_default(resolution.get('resourceCost'), default=1))
    next_feature_state = spend_capability(
        actor.get('classFeatureState'),
        class_name=actor.get('class'),
        level=actor.get('level'),
        capability_id=change.get('capabilityId'),
        amount=resource_cost,
        turn_id=change.get('turnId'),
    )
    if next_feature_state is None:
        return None

    economy_kind = _text(resolution.get('actionEconomy')).lower()
    if _text(combat.get('status')).lower() == 'active':
        if _text(flags.get('activeActorId')) != _text(actor.get('id')):
            return None
        if economy_kind == 'action':
            economy['actionRemaining'] = 0
        elif economy_kind == 'bonus_action':
            economy['bonusActionRemaining'] = 0

    actual_amount = 0
    if effect_type == 'heal':
        assert target is not None
        actual_amount = _apply_health_heal(
            target,
            {
                'amount': resolution.get('amount'),
                'clearConditions': [],
            },
        )
        _sync_actor_health_to_combat_participant(state, target)
    elif effect_type == 'restore_action':
        if not economy:
            return None
        economy['actionRemaining'] = max(1, int_or_default(resolution.get('amount'), default=1))
        actual_amount = 1
    actor['classFeatureState'] = next_feature_state
    return {
        'capabilityId': change.get('capabilityId'),
        'capabilityName': change.get('capabilityName'),
        'targetId': resolution.get('targetId'),
        'effectType': effect_type,
        'resourceCost': resource_cost,
        'actualAmount': actual_amount,
        'remainingClassFeatureState': deepcopy(next_feature_state),
        'turnEconomy': deepcopy(economy) if economy else None,
    }


def _apply_rest(actor: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """Restore only resources guaranteed by the completed rest type."""

    rest_type = _text(_change_value(change, 'restType', 'rest_type')).lower()
    before_resources = deepcopy(actor.get('spellResources'))
    resources = restore_spell_resources(
        actor.get('spellResources'),
        rest_type=rest_type,
        class_name=_text(actor.get('class')),
        level=max(1, int_or_default(actor.get('level'), default=1)),
    )
    actor['spellResources'] = resources
    before_class_features = deepcopy(actor.get('classFeatureState'))
    actor['classFeatureState'] = restore_class_capabilities(
        actor.get('classFeatureState'),
        class_name=actor.get('class'),
        level=actor.get('level'),
        rest_type=rest_type,
    )

    ability_state = actor.get('raceAbilityState')
    refreshed_ability_ids: list[str] = []
    if isinstance(ability_state, dict):
        refreshable = {'short_rest', 'long_rest'} if rest_type == 'long_rest' else {'short_rest'}
        for ability_id, raw_state in ability_state.items():
            if not isinstance(raw_state, dict):
                continue
            refreshes_on = _text(raw_state.get('refreshesOn') or raw_state.get('refreshes_on')).lower()
            if refreshes_on in refreshable and raw_state.get('available') is not True:
                raw_state['available'] = True
                raw_state.pop('usedAtTurn', None)
                refreshed_ability_ids.append(_text(ability_id))

    hp_restored = 0
    if rest_type == 'long_rest':
        health = actor.setdefault('health', {})
        current_hp = max(0, int_or_default(health.get('currentHp'), default=0))
        max_hp = max(current_hp, int_or_default(health.get('maxHp'), default=current_hp))
        hp_restored = max(0, max_hp - current_hp)
        health['currentHp'] = max_hp
        health['tempHp'] = 0

    return {
        'restType': rest_type,
        'hpRestored': hp_restored,
        'refreshedAbilityIds': refreshed_ability_ids,
        'spellResourcesChanged': before_resources != resources,
        'classFeaturesChanged': before_class_features != actor.get('classFeatureState'),
        'remainingClassFeatureState': deepcopy(actor.get('classFeatureState')),
        'remainingSpellResources': deepcopy(resources),
    }


def _text(value: Any) -> str:
    return str(value or '').strip()


def _world_id(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return stable_slug(text)
    return ''


def _ensure_list(container: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = container.get(key)
    if isinstance(value, list):
        return value
    container[key] = []
    return container[key]


def _ensure_dict(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    if isinstance(value, dict):
        return value
    container[key] = {}
    return container[key]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _merge_unique(existing: Any, incoming: Any) -> list[str]:
    merged: list[str] = []
    for value in [*_string_list(existing), *_string_list(incoming)]:
        if value not in merged:
            merged.append(value)
    return merged


def _remove_active_quest_id(state: dict[str, Any], quest_id: Any) -> None:
    quest_id_text = _text(quest_id)
    if not quest_id_text:
        return
    scene = state.get('currentScene')
    if isinstance(scene, dict):
        active_quest_ids = scene.get('activeQuestIds')
        if isinstance(active_quest_ids, list):
            scene['activeQuestIds'] = [item for item in active_quest_ids if _text(item) != quest_id_text]

    # A terminal quest must also be removed from every saved location scene.
    # Otherwise traveling back to an older location restores the stale quest.
    scene_states = state.get('locationSceneStates')
    if not isinstance(scene_states, dict):
        return
    for saved_scene in scene_states.values():
        if not isinstance(saved_scene, dict):
            continue
        saved_active_quest_ids = saved_scene.get('activeQuestIds')
        if isinstance(saved_active_quest_ids, list):
            saved_scene['activeQuestIds'] = [
                item for item in saved_active_quest_ids if _text(item) != quest_id_text
            ]


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


def _location_record(state: dict[str, Any], *, location_id: Any = None, name: Any = None) -> dict[str, Any] | None:
    return _find_record(_ensure_list(state, 'locations'), record_id=location_id, name=name)


def _quest_record(state: dict[str, Any], *, quest_id: Any = None, title: Any = None) -> dict[str, Any] | None:
    return _find_record(_ensure_list(state, 'quests'), record_id=quest_id, title=title)


def _npc_record(state: dict[str, Any], *, npc_id: Any = None, name: Any = None) -> dict[str, Any] | None:
    return _find_record(
        [*_ensure_list(state, 'knownNpcs'), *_ensure_list(state, 'partyNpcs')],
        record_id=npc_id,
        name=name,
    )


def _turn_id(change: dict[str, Any]) -> int | None:
    if change.get('turnId') is None and change.get('turn_id') is None:
        return None
    value = int_or_default(change.get('turnId', change.get('turn_id')), default=0)
    return value if value > 0 else None


def _merge_rich_text(record: dict[str, Any], key: str, value: Any) -> None:
    if value == '':
        record[key] = ''
        return
    incoming = _text(value)
    if not incoming:
        return
    existing = _text(record.get(key))
    if not existing or len(incoming) >= len(existing):
        record[key] = incoming


def _set_if_present(record: dict[str, Any], key: str, value: Any) -> None:
    if value not in (None, '', [], {}):
        record[key] = value


def _merge_metadata(record: dict[str, Any], incoming: Any) -> None:
    if not isinstance(incoming, dict):
        return
    metadata = record.setdefault('metadata', {})
    if not isinstance(metadata, dict):
        metadata = {}
        record['metadata'] = metadata
    for key, value in incoming.items():
        if value not in (None, '', [], {}):
            metadata[key] = value


CONTENT_RECORD_SOURCES = {'campaign_pack', 'emergent', 'player_created', 'dm_override', 'admin_override'}
PROTECTED_CONTENT_RECORD_SOURCES = {'campaign_pack', 'dm_override', 'admin_override'}


def _content_record_source(*values: Any) -> str | None:
    for value in values:
        source = _text(value)
        if source in CONTENT_RECORD_SOURCES:
            return source
    return None


def _record_source_fields(change: dict[str, Any], embedded: dict[str, Any], metadata: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    source = _content_record_source(change.get('source'), embedded.get('source'), metadata.get('source'))
    if source:
        fields['source'] = source
    pack_id = _text(
        change.get('packId')
        or change.get('pack_id')
        or embedded.get('packId')
        or embedded.get('pack_id')
        or metadata.get('packId')
        or metadata.get('pack_id')
    )
    if pack_id:
        fields['packId'] = pack_id
    return fields


def _merge_record_source(record: dict[str, Any], payload: dict[str, Any]) -> None:
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    source = _content_record_source(payload.get('source'), metadata.get('source'))
    if source:
        existing_source = _text(record.get('source'))
        if (
            not existing_source
            or source in PROTECTED_CONTENT_RECORD_SOURCES
            or existing_source not in PROTECTED_CONTENT_RECORD_SOURCES
        ):
            record['source'] = source
    pack_id = _text(payload.get('packId') or payload.get('pack_id') or metadata.get('packId') or metadata.get('pack_id'))
    if pack_id and not _text(record.get('packId') or record.get('pack_id')):
        record['packId'] = pack_id


def _domain_record_payload(
    change: dict[str, Any],
    *,
    embedded_key: str,
    id_key: str,
    default_status: str,
) -> dict[str, Any]:
    embedded = change.get(embedded_key) if isinstance(change.get(embedded_key), dict) else {}
    title = _text(change.get('title') or change.get('name') or embedded.get('title') or embedded.get('name'))
    record_id = _world_id(change.get(id_key), embedded.get('id'), embedded.get(id_key), title)
    metadata = embedded.get('metadata') if isinstance(embedded.get('metadata'), dict) else {}
    if isinstance(change.get('metadata'), dict):
        metadata = {**metadata, **change['metadata']}
    payload: dict[str, Any] = {
        **embedded,
        'id': record_id,
        'title': title or record_id,
        'name': change.get('name') or embedded.get('name'),
        'status': change.get('status') or embedded.get('status') or default_status,
        'summary': change.get('summary') or embedded.get('summary'),
        'description': change.get('description') or embedded.get('description'),
        'locationIds': _merge_unique(embedded.get('locationIds'), change.get('locationIds')),
        'npcIds': _merge_unique(embedded.get('npcIds'), change.get('npcIds')),
        'questIds': _merge_unique(embedded.get('questIds'), change.get('questIds')),
        'checkpointIds': _merge_unique(embedded.get('checkpointIds'), change.get('checkpointIds')),
        'tags': _merge_unique(embedded.get('tags'), change.get('tags')),
        'metadata': metadata,
    }
    if isinstance(change.get('flags'), dict) or isinstance(embedded.get('flags'), dict):
        payload['flags'] = {
            **(embedded.get('flags') if isinstance(embedded.get('flags'), dict) else {}),
            **(change.get('flags') if isinstance(change.get('flags'), dict) else {}),
        }
    payload.update(_record_source_fields(change, embedded, metadata))
    turn_id = _turn_id(change)
    if turn_id is not None:
        payload['updatedAtTurn'] = turn_id
        payload.setdefault('firstRevealedTurn', turn_id)
    return payload


def _merge_domain_record(state: dict[str, Any], collection_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    collection = _ensure_list(state, collection_key)
    record = _find_record(collection, record_id=payload.get('id'), title=payload.get('title'), name=payload.get('name'))
    if not record:
        record = {
            'id': payload.get('id'),
            'title': payload.get('title') or payload.get('name') or payload.get('id'),
            'name': payload.get('name'),
            'status': payload.get('status'),
            'summary': _text(payload.get('summary')),
            'description': _text(payload.get('description')),
            'locationIds': _string_list(payload.get('locationIds')),
            'npcIds': _string_list(payload.get('npcIds')),
            'questIds': _string_list(payload.get('questIds')),
            'checkpointIds': _string_list(payload.get('checkpointIds')),
            'tags': _string_list(payload.get('tags')),
            'flags': payload.get('flags') if isinstance(payload.get('flags'), dict) else {},
            'metadata': payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {},
            'firstRevealedTurn': payload.get('firstRevealedTurn'),
            'updatedAtTurn': payload.get('updatedAtTurn'),
        }
        _merge_record_source(record, payload)
        collection.append(record)
        return record
    for key in ('title', 'name', 'status', 'firstRevealedTurn', 'updatedAtTurn'):
        if key == 'firstRevealedTurn' and record.get(key):
            continue
        _set_if_present(record, key, payload.get(key))
    _merge_rich_text(record, 'summary', payload.get('summary'))
    _merge_rich_text(record, 'description', payload.get('description'))
    for key in ('locationIds', 'npcIds', 'questIds', 'checkpointIds', 'tags'):
        record[key] = _merge_unique(record.get(key), payload.get(key))
    if isinstance(record.setdefault('flags', {}), dict) and isinstance(payload.get('flags'), dict):
        record['flags'].update(payload['flags'])
    _merge_metadata(record, payload.get('metadata'))
    _merge_record_source(record, payload)
    return record


def _apply_faction_relationship_update(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    faction = _merge_domain_record(
        state,
        'factions',
        _domain_record_payload(
            change,
            embedded_key='faction',
            id_key='factionId',
            default_status='known',
        ),
    )
    relationship = faction.setdefault('relationship', {})
    if not isinstance(relationship, dict):
        relationship = {}
        faction['relationship'] = relationship
    current = int_or_default(relationship.get('score'), default=0)
    if change.get('scoreDelta') is not None:
        relationship['score'] = max(-100, min(100, current + int_or_default(change.get('scoreDelta'), default=0)))
    elif change.get('relationshipScore') is not None:
        relationship['score'] = max(-100, min(100, int_or_default(change.get('relationshipScore'), default=current)))
    elif isinstance(change.get('relationship'), dict) and change['relationship'].get('score') is not None:
        relationship['score'] = max(-100, min(100, int_or_default(change['relationship'].get('score'), default=current)))
    if change.get('relationshipLabel'):
        relationship['label'] = _text(change.get('relationshipLabel'))
    elif isinstance(change.get('relationship'), dict) and change['relationship'].get('label'):
        relationship['label'] = _text(change['relationship'].get('label'))
    relationship.setdefault('score', 0)
    relationship.setdefault('label', 'neutral')
    return faction


def _apply_map_change(state: dict[str, Any], change: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    map_record = _merge_domain_record(
        state,
        'maps',
        _domain_record_payload(
            change,
            embedded_key='map',
            id_key='mapId',
            default_status='revealed' if change.get('type') == 'map.reveal' else 'known',
        ),
    )
    if change.get('type') == 'map.reveal':
        map_record['revealed'] = True
    region = change.get('region') if isinstance(change.get('region'), dict) else {}
    region_title = _text(change.get('regionTitle') or change.get('regionName') or region.get('title') or region.get('name'))
    region_id = _world_id(change.get('regionId'), region.get('id'), region.get('regionId'), region_title)
    if not region_id:
        return map_record, None
    regions = map_record.setdefault('regions', [])
    if not isinstance(regions, list):
        regions = []
        map_record['regions'] = regions
    existing_region = _find_record(regions, record_id=region_id, title=region_title, name=region_title)
    if not existing_region:
        existing_region = {'id': region_id, 'title': region_title or region_id}
        regions.append(existing_region)
    existing_region['revealed'] = bool(change.get('type') == 'map.reveal' or change.get('revealed', True))
    _set_if_present(existing_region, 'status', change.get('regionStatus') or region.get('status'))
    _merge_rich_text(existing_region, 'summary', change.get('summary') or region.get('summary'))
    _merge_rich_text(existing_region, 'description', change.get('description') or region.get('description'))
    region_metadata = region.get('metadata') if isinstance(region.get('metadata'), dict) else {}
    change_metadata = change.get('metadata') if isinstance(change.get('metadata'), dict) else {}
    if region_metadata or change_metadata:
        _merge_metadata(existing_region, {**region_metadata, **change_metadata})
    return map_record, existing_region


def _ensure_scene(state: dict[str, Any]) -> dict[str, Any]:
    scene = state.get('currentScene')
    if not isinstance(scene, dict):
        scene = {}
        state['currentScene'] = scene
    scene.setdefault('sceneType', 'exploration')
    scene.setdefault('dangerLevel', 0)
    scene.setdefault('combatState', 'none')
    scene.setdefault('activeNpcIds', [])
    scene.setdefault('activeQuestIds', [])
    return scene


def _scene_items(scene: dict[str, Any]) -> list[dict[str, Any]]:
    items = scene.get('items')
    if isinstance(items, list):
        return items
    scene['items'] = []
    return scene['items']


LOCATION_SCENE_STATES_KEY = 'locationSceneStates'
SCENE_LOCAL_SCALAR_FIELDS = (
    'sceneType',
    'dangerLevel',
    'mood',
    'description',
    'musicTag',
    'updatedAtTurn',
)
SCENE_LOCAL_LIST_FIELDS = ('activeNpcIds', 'activeQuestIds', 'items', 'interactables', 'hazards')
SCENE_LOCAL_DICT_FIELDS = ('playerPositions', 'playerZones', 'characterPositions', 'characterZones')
PUBLIC_AUTHORED_LOCATION_FIELDS = (
    'region',
    'playerSummary',
    'player_summary',
    'playerDescription',
    'player_description',
    'publicSummary',
    'public_summary',
    'publicDescription',
    'public_description',
    'visibleAtStart',
    'visible_at_start',
    'hiddenToPlayers',
    'hidden_to_players',
    'knownToPlayers',
    'known_to_players',
    'visibleToPlayers',
    'visible_to_players',
    'playerVisible',
    'player_visible',
    *SCENE_LOCAL_SCALAR_FIELDS,
    *SCENE_LOCAL_LIST_FIELDS,
    *SCENE_LOCAL_DICT_FIELDS,
)


def _location_scene_states(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    scene_states = state.get(LOCATION_SCENE_STATES_KEY)
    if not isinstance(scene_states, dict):
        scene_states = {}
        state[LOCATION_SCENE_STATES_KEY] = scene_states
    return scene_states


def _scene_local_state(scene: dict[str, Any]) -> dict[str, Any]:
    saved: dict[str, Any] = {}
    for key in SCENE_LOCAL_SCALAR_FIELDS:
        if key in scene:
            saved[key] = deepcopy(scene.get(key))
    for key in SCENE_LOCAL_LIST_FIELDS:
        saved[key] = deepcopy(scene.get(key)) if isinstance(scene.get(key), list) else []
    for key in SCENE_LOCAL_DICT_FIELDS:
        saved[key] = deepcopy(scene.get(key)) if isinstance(scene.get(key), dict) else {}
    return saved


def _public_authored_location_fields(payload: dict[str, Any]) -> dict[str, Any]:
    authored = {
        key: deepcopy(payload.get(key))
        for key in PUBLIC_AUTHORED_LOCATION_FIELDS
        if key in payload and payload.get(key) not in (None, '', [], {})
    }
    scene_state = payload.get('sceneState') if isinstance(payload.get('sceneState'), dict) else payload.get('scene_state')
    if isinstance(scene_state, dict):
        # Only scene-runtime fields are copied into the player-visible location
        # record; author-only notes remain confined to the campaign-pack catalog.
        authored['sceneState'] = _scene_local_state(scene_state)
    return authored


def _save_current_scene_state(state: dict[str, Any]) -> None:
    scene = _ensure_scene(state)
    location_id = _world_id(scene.get('locationId'), scene.get('name'))
    if location_id:
        _location_scene_states(state)[location_id] = _scene_local_state(scene)


def _cached_scene_state(state: dict[str, Any], location: dict[str, Any]) -> dict[str, Any]:
    location_id = _text(location.get('id'))
    saved = _location_scene_states(state).get(location_id)
    authored = location.get('sceneState') if isinstance(location.get('sceneState'), dict) else location.get('scene_state')
    authored_state: dict[str, Any] = {}
    for key in SCENE_LOCAL_SCALAR_FIELDS:
        if key in location:
            authored_state[key] = deepcopy(location.get(key))
    for key in SCENE_LOCAL_LIST_FIELDS:
        if isinstance(location.get(key), list):
            authored_state[key] = deepcopy(location.get(key))
    for key in SCENE_LOCAL_DICT_FIELDS:
        if isinstance(location.get(key), dict):
            authored_state[key] = deepcopy(location.get(key))
    authored_state['activeNpcIds'] = _merge_unique(authored_state.get('activeNpcIds'), location.get('npcIds'))
    authored_state['activeQuestIds'] = _merge_unique(authored_state.get('activeQuestIds'), location.get('questIds'))
    if isinstance(authored, dict):
        authored_state.update(deepcopy(authored))
    if isinstance(saved, dict):
        authored_state.update(deepcopy(saved))

    terminal_quest_ids = {
        _text(quest.get('id'))
        for quest in _ensure_list(state, 'quests')
        if isinstance(quest, dict)
        and _text(quest.get('id'))
        and _text(quest.get('status')).lower() in {'completed', 'failed', 'abandoned'}
    }
    authored_state['activeQuestIds'] = [
        quest_id
        for quest_id in _string_list(authored_state.get('activeQuestIds'))
        if quest_id not in terminal_quest_ids
    ]
    return authored_state


def _npc_matches(record: dict[str, Any], npc_id: Any) -> bool:
    return bool(
        isinstance(record, dict)
        and _text(npc_id)
        and _text(record.get('id') or record.get('npcId')) == _text(npc_id)
    )


def _set_cached_location_npc_presence(
    state: dict[str, Any],
    *,
    location_id: Any,
    npc_id: Any,
    present: bool,
) -> None:
    location_key = _world_id(location_id)
    npc_key = _text(npc_id)
    if not location_key or not npc_key:
        return
    scene_state = _location_scene_states(state).setdefault(location_key, {})
    active_ids = _string_list(scene_state.get('activeNpcIds'))
    if present:
        scene_state['activeNpcIds'] = _merge_unique(active_ids, [npc_key])
    else:
        scene_state['activeNpcIds'] = [item for item in active_ids if _text(item) != npc_key]


def _remove_npc_location_reference(state: dict[str, Any], *, location_id: Any, npc_id: Any) -> None:
    location = _location_record(state, location_id=location_id)
    if location:
        location['npcIds'] = [
            item
            for item in _string_list(location.get('npcIds'))
            if _text(item) != _text(npc_id)
        ]
    _set_cached_location_npc_presence(state, location_id=location_id, npc_id=npc_id, present=False)


def _add_npc_location_reference(state: dict[str, Any], *, location_id: Any, npc_id: Any) -> None:
    location = _location_record(state, location_id=location_id)
    if location:
        location['npcIds'] = _merge_unique(location.get('npcIds'), [npc_id])
    _set_cached_location_npc_presence(state, location_id=location_id, npc_id=npc_id, present=True)


def _reconcile_current_scene_npcs(state: dict[str, Any]) -> None:
    scene = _ensure_scene(state)
    location_id = _text(scene.get('locationId'))
    active_ids = _string_list(scene.get('activeNpcIds'))
    party_ids = {
        _text(npc.get('id'))
        for npc in _ensure_list(state, 'partyNpcs')
        if isinstance(npc, dict) and _text(npc.get('id'))
    }
    tracked = {
        _text(npc.get('id')): npc
        for npc in [*_ensure_list(state, 'knownNpcs'), *_ensure_list(state, 'partyNpcs')]
        if isinstance(npc, dict) and _text(npc.get('id'))
    }
    reconciled: list[str] = []
    for npc_id in active_ids:
        npc = tracked.get(npc_id)
        npc_location_id = _text(npc.get('locationId')) if npc else ''
        if npc and npc_id not in party_ids and npc_location_id and npc_location_id != location_id:
            continue
        if npc_id not in reconciled:
            reconciled.append(npc_id)
    for npc_id, npc in tracked.items():
        if npc_id in party_ids or (_text(npc.get('locationId')) and _text(npc.get('locationId')) == location_id):
            if npc_id not in reconciled:
                reconciled.append(npc_id)
    scene['activeNpcIds'] = reconciled


def _apply_scene_fields(scene: dict[str, Any], change: dict[str, Any]) -> None:
    for key in ('locationId', 'name', 'sceneType', 'mood', 'combatState', 'musicTag'):
        _set_if_present(scene, key, change.get(key))
    if 'dangerLevel' in change:
        scene['dangerLevel'] = max(0, min(10, int_or_default(change.get('dangerLevel'), default=0)))
    if 'description' in change:
        _merge_rich_text(scene, 'description', change.get('description'))
    if 'activeNpcIds' in change:
        scene['activeNpcIds'] = _string_list(change.get('activeNpcIds'))
    if 'activeQuestIds' in change:
        scene['activeQuestIds'] = _merge_unique(scene.get('activeQuestIds'), change.get('activeQuestIds'))
    for key in ('playerPositions', 'playerZones', 'characterPositions', 'characterZones'):
        if isinstance(change.get(key), dict):
            current = scene.get(key) if isinstance(scene.get(key), dict) else {}
            scene[key] = {**current, **{str(k): v for k, v in change[key].items() if v not in (None, '', [], {})}}
    turn_id = _turn_id(change)
    if turn_id is not None:
        scene['updatedAtTurn'] = turn_id


def _location_payload(change: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    location = change.get('location') if isinstance(change.get('location'), dict) else {}
    location_id = _world_id(
        change.get('locationId'),
        location.get('id'),
        location.get('locationId'),
        change.get('name') or change.get('locationName'),
        location.get('name'),
    )
    name = _text(change.get('name') or change.get('locationName') or location.get('name') or location_id)
    turn_id = _turn_id(change)
    payload: dict[str, Any] = {
        **location,
        'id': location_id,
        'name': name,
        'type': change.get('type') if str(change.get('type') or '').startswith('location_type.') else location.get('type'),
        'description': change.get('description') or location.get('description'),
        'status': status or change.get('status') or location.get('status') or 'discovered',
        'parentLocationId': change.get('parentLocationId') or location.get('parentLocationId'),
        'connectedLocationIds': _merge_unique(location.get('connectedLocationIds'), change.get('connectedLocationIds')),
        'npcIds': _merge_unique(location.get('npcIds'), change.get('npcIds')),
        'questIds': _merge_unique(location.get('questIds'), change.get('questIds')),
        'tags': _merge_unique(location.get('tags'), change.get('tags')),
        'metadata': location.get('metadata') if isinstance(location.get('metadata'), dict) else {},
    }
    location_type = change.get('locationType') or location.get('type')
    if location_type:
        payload['type'] = location_type
    if turn_id is not None:
        if status in {'visited', 'discovered'} or payload.get('status') in {'visited', 'discovered'}:
            payload['firstDiscoveredTurn'] = location.get('firstDiscoveredTurn') or change.get('firstDiscoveredTurn') or turn_id
        if status == 'visited' or payload.get('status') == 'visited':
            payload['lastVisitedTurn'] = turn_id
    if change.get('metadata'):
        payload['metadata'] = {**payload.get('metadata', {}), **(change.get('metadata') if isinstance(change.get('metadata'), dict) else {})}
    payload.update(_record_source_fields(change, location, payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}))
    return payload


def _merge_location(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    locations = _ensure_list(state, 'locations')
    record = _find_record(locations, record_id=payload.get('id'), name=payload.get('name'))
    if not record:
        # Keep authored campaign-pack fields (including sceneState and items)
        # when a catalog location first becomes part of visible world state.
        record = {
            **_public_authored_location_fields(payload),
            'id': payload.get('id'),
            'name': payload.get('name'),
            'type': payload.get('type') or 'other',
            'description': _text(payload.get('description')),
            'status': payload.get('status') or 'discovered',
            'parentLocationId': payload.get('parentLocationId'),
            'connectedLocationIds': _string_list(payload.get('connectedLocationIds')),
            'npcIds': _string_list(payload.get('npcIds')),
            'questIds': _string_list(payload.get('questIds')),
            'tags': _string_list(payload.get('tags')),
            'firstDiscoveredTurn': payload.get('firstDiscoveredTurn'),
            'lastVisitedTurn': payload.get('lastVisitedTurn'),
            'metadata': payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {},
        }
        _merge_record_source(record, payload)
        locations.append(record)
        return record
    for key in ('name', 'type', 'status', 'parentLocationId', 'firstDiscoveredTurn', 'lastVisitedTurn'):
        if key == 'firstDiscoveredTurn' and record.get(key):
            continue
        _set_if_present(record, key, payload.get(key))
    _merge_rich_text(record, 'description', payload.get('description'))
    for key in ('connectedLocationIds', 'npcIds', 'questIds', 'tags'):
        record[key] = _merge_unique(record.get(key), payload.get(key))
    for key, value in _public_authored_location_fields(payload).items():
        if record.get(key) in (None, '', [], {}):
            record[key] = deepcopy(value)
    _merge_metadata(record, payload.get('metadata'))
    _merge_record_source(record, payload)
    return record


def _apply_scene_move(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    scene = _ensure_scene(state)
    previous_location_id = _text(scene.get('locationId'))
    _save_current_scene_state(state)
    location_payload = _location_payload(change, status='visited')
    location = _merge_location(state, location_payload)
    target_location_id = _text(location.get('id'))
    target_state = _cached_scene_state(state, location)
    scene.clear()
    scene.update(
        {
            'locationId': target_location_id,
            'name': location.get('name'),
            'sceneType': target_state.get('sceneType') or location.get('sceneType') or 'exploration',
            'dangerLevel': max(0, min(10, int_or_default(target_state.get('dangerLevel'), default=0))),
            'combatState': 'none',
            'description': target_state.get('description') or location.get('description') or '',
            'activeNpcIds': _string_list(target_state.get('activeNpcIds')),
            'activeQuestIds': _string_list(target_state.get('activeQuestIds')),
            'items': deepcopy(target_state.get('items')) if isinstance(target_state.get('items'), list) else [],
            'playerPositions': deepcopy(target_state.get('playerPositions')) if isinstance(target_state.get('playerPositions'), dict) else {},
            'playerZones': deepcopy(target_state.get('playerZones')) if isinstance(target_state.get('playerZones'), dict) else {},
            'characterPositions': deepcopy(target_state.get('characterPositions')) if isinstance(target_state.get('characterPositions'), dict) else {},
            'characterZones': deepcopy(target_state.get('characterZones')) if isinstance(target_state.get('characterZones'), dict) else {},
        }
    )
    for key in ('mood', 'musicTag', 'updatedAtTurn'):
        if key in target_state:
            scene[key] = deepcopy(target_state.get(key))
    _apply_scene_fields(
        scene,
        {
            **change,
            'locationId': target_location_id,
            'name': location.get('name'),
            'combatState': 'none',
        },
    )
    if scene.get('sceneType') == 'combat':
        scene['sceneType'] = 'exploration'

    for npc in _ensure_list(state, 'partyNpcs'):
        if not isinstance(npc, dict) or not _text(npc.get('id')):
            continue
        old_location_id = _text(npc.get('locationId')) or previous_location_id
        if old_location_id and old_location_id != target_location_id:
            _remove_npc_location_reference(state, location_id=old_location_id, npc_id=npc.get('id'))
        npc['locationId'] = target_location_id
        _add_npc_location_reference(state, location_id=target_location_id, npc_id=npc.get('id'))

    _reconcile_current_scene_npcs(state)
    state['combat'] = normalize_combat_state({}, scene)
    scene['combatState'] = 'none'
    return location


def _normalize_objective(raw: dict[str, Any]) -> dict[str, Any]:
    description_explicit = 'description' in raw and raw.get('description') is not None
    description = _text(raw.get('description'))
    objective_id = _world_id(raw.get('id'), raw.get('objectiveId'), description)
    return {
        **raw,
        'id': objective_id,
        'description': description,
        '_descriptionExplicit': description_explicit,
        'status': raw.get('status') or 'open',
    }


def _merge_objectives(existing: Any, incoming: Any) -> list[dict[str, Any]]:
    objectives = [dict(item) for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
    incoming_items = incoming if isinstance(incoming, list) else []
    for raw_objective in incoming_items:
        if not isinstance(raw_objective, dict):
            continue
        objective = _normalize_objective(raw_objective)
        description_explicit = bool(objective.pop('_descriptionExplicit', False))
        current = _find_record(objectives, record_id=objective.get('id'), name=objective.get('description'))
        if not current:
            objectives.append(objective)
            continue
        if description_explicit:
            _merge_rich_text(current, 'description', objective.get('description'))
        _set_if_present(current, 'status', objective.get('status'))
        for key in (
            'title',
            'optional',
            'prerequisiteObjectiveIds',
            'prerequisites',
            'completeWhen',
            'failWhen',
            'rules',
            'branchId',
            'outcomeId',
            'metadata',
        ):
            if key in objective:
                current[key] = deepcopy(objective.get(key))
    return objectives


def _quest_payload(change: dict[str, Any]) -> dict[str, Any]:
    quest = change.get('quest') if isinstance(change.get('quest'), dict) else {}
    title = _text(change.get('title') or change.get('name') or quest.get('title') or quest.get('name'))
    quest_id = _world_id(change.get('questId'), quest.get('id'), quest.get('questId'), title)
    turn_id = _turn_id(change)
    payload: dict[str, Any] = {
        **quest,
        'id': quest_id,
        'title': title or quest_id,
        'status': change.get('status') or quest.get('status') or ('active' if str(change.get('type')) == 'quest.add' else None),
        'summary': change.get('summary') or quest.get('summary'),
        'stage': change.get('stage') or quest.get('stage'),
        'objectives': change.get('objectives') if isinstance(change.get('objectives'), list) else quest.get('objectives') if isinstance(quest.get('objectives'), list) else [],
        'relatedNpcIds': _merge_unique(quest.get('relatedNpcIds'), change.get('relatedNpcIds')),
        'relatedLocationIds': _merge_unique(quest.get('relatedLocationIds'), change.get('relatedLocationIds')),
        'importantItemIds': _merge_unique(quest.get('importantItemIds'), change.get('importantItemIds')),
        'flags': quest.get('flags') if isinstance(quest.get('flags'), dict) else {},
        'metadata': quest.get('metadata') if isinstance(quest.get('metadata'), dict) else {},
        '_titleExplicit': bool(title),
    }
    for key in (
        'completionPolicy',
        'failOnObjectiveFailure',
        'validationMode',
        'rewards',
        'reward',
        'xpReward',
        'rewardXp',
        'experienceReward',
        'rewardActorId',
        'onComplete',
        'onFail',
        'completionConsequences',
        'failureConsequences',
    ):
        if key in change:
            payload[key] = deepcopy(change.get(key))
        elif key in quest:
            payload[key] = deepcopy(quest.get(key))
    if isinstance(change.get('flags'), dict):
        payload['flags'] = {**payload['flags'], **change['flags']}
    if isinstance(change.get('metadata'), dict):
        payload['metadata'] = {**payload['metadata'], **change['metadata']}
    payload.update(_record_source_fields(change, quest, payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}))
    if turn_id is not None:
        payload['createdAtTurn'] = quest.get('createdAtTurn') or change.get('createdAtTurn') or turn_id
        payload['updatedAtTurn'] = turn_id
    return payload


def _merge_quest(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    quests = _ensure_list(state, 'quests')
    record = _find_record(quests, record_id=payload.get('id'), title=payload.get('title'))
    if not record:
        record = {
            'id': payload.get('id'),
            'title': payload.get('title'),
            'status': payload.get('status') or 'active',
            'summary': _text(payload.get('summary')),
            'stage': _text(payload.get('stage')),
            'objectives': _merge_objectives([], payload.get('objectives')),
            'relatedNpcIds': _string_list(payload.get('relatedNpcIds')),
            'relatedLocationIds': _string_list(payload.get('relatedLocationIds')),
            'importantItemIds': _string_list(payload.get('importantItemIds')),
            'flags': payload.get('flags') if isinstance(payload.get('flags'), dict) else {},
            'createdAtTurn': payload.get('createdAtTurn'),
            'updatedAtTurn': payload.get('updatedAtTurn'),
            'completedAtTurn': payload.get('completedAtTurn'),
            'metadata': payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {},
        }
        for key in (
            'completionPolicy',
            'failOnObjectiveFailure',
            'validationMode',
            'rewards',
            'reward',
            'xpReward',
            'rewardXp',
            'experienceReward',
            'rewardActorId',
            'onComplete',
            'onFail',
            'completionConsequences',
            'failureConsequences',
        ):
            if key in payload:
                record[key] = deepcopy(payload.get(key))
        _merge_record_source(record, payload)
        quests.append(record)
        return record
    for key in ('title', 'status', 'stage', 'createdAtTurn', 'updatedAtTurn', 'completedAtTurn'):
        if key == 'createdAtTurn' and record.get(key):
            continue
        if key == 'title' and not payload.get('_titleExplicit'):
            continue
        _set_if_present(record, key, payload.get(key))
    _merge_rich_text(record, 'summary', payload.get('summary'))
    record['objectives'] = _merge_objectives(record.get('objectives'), payload.get('objectives'))
    for key in ('relatedNpcIds', 'relatedLocationIds', 'importantItemIds'):
        record[key] = _merge_unique(record.get(key), payload.get(key))
    flags = record.setdefault('flags', {})
    if isinstance(flags, dict) and isinstance(payload.get('flags'), dict):
        flags.update(payload['flags'])
    _merge_metadata(record, payload.get('metadata'))
    _merge_record_source(record, payload)
    for key in (
        'completionPolicy',
        'failOnObjectiveFailure',
        'validationMode',
        'rewards',
        'reward',
        'xpReward',
        'rewardXp',
        'experienceReward',
        'rewardActorId',
        'onComplete',
        'onFail',
        'completionConsequences',
        'failureConsequences',
    ):
        if key in payload:
            record[key] = deepcopy(payload.get(key))
    return record


def _apply_objective_change(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    quest = _quest_record(state, quest_id=change.get('questId'), title=change.get('title'))
    if not quest:
        return None
    objective = change.get('objective') if isinstance(change.get('objective'), dict) else {}
    objective = {
        **objective,
        'id': change.get('objectiveId') or objective.get('id') or objective.get('objectiveId'),
        'description': change.get('description') or objective.get('description'),
        'status': change.get('status') or change.get('objectiveStatus') or objective.get('status') or 'open',
    }
    quest['objectives'] = _merge_objectives(quest.get('objectives'), [objective])
    turn_id = _turn_id(change)
    if turn_id is not None:
        quest['updatedAtTurn'] = turn_id
    return quest


def _npc_payload(change: dict[str, Any]) -> dict[str, Any]:
    npc = change.get('npc') if isinstance(change.get('npc'), dict) else {}
    name = _text(change.get('name') or change.get('npcName') or npc.get('name'))
    npc_id = _world_id(change.get('npcId'), npc.get('id'), npc.get('npcId'), name)
    turn_id = _turn_id(change)
    race = (
        change.get('race')
        or change.get('species')
        or change.get('ancestry')
        or npc.get('race')
        or npc.get('species')
        or npc.get('ancestry')
    )
    payload: dict[str, Any] = {
        **npc,
        'id': npc_id,
        'name': name or npc_id,
        'race': race,
        'role': change.get('role') or npc.get('role'),
        'description': change.get('description') or npc.get('description'),
        'disposition': change.get('disposition') or npc.get('disposition') or 'unknown',
        'relationship': npc.get('relationship') if isinstance(npc.get('relationship'), dict) else {},
        'locationId': _world_id(change.get('locationId'), npc.get('locationId')) if (change.get('locationId') or npc.get('locationId')) else None,
        'status': change.get('status') or npc.get('status') or 'known',
        'faction': change.get('faction') or npc.get('faction'),
        'aliases': _merge_unique(npc.get('aliases'), change.get('aliases')),
        'questIds': _merge_unique(npc.get('questIds'), change.get('questIds')),
        'memory': _merge_unique(npc.get('memory'), change.get('memory')),
        'metadata': npc.get('metadata') if isinstance(npc.get('metadata'), dict) else {},
    }
    if isinstance(change.get('relationship'), dict):
        payload['relationship'] = {**payload['relationship'], **change['relationship']}
    if isinstance(change.get('metadata'), dict):
        payload['metadata'] = {**payload['metadata'], **change['metadata']}
    payload.update(_record_source_fields(change, npc, payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}))
    if turn_id is not None:
        if str(change.get('type')) == 'npc.discover':
            payload['firstMetTurn'] = npc.get('firstMetTurn') or change.get('firstMetTurn') or turn_id
        payload['lastSeenTurn'] = turn_id
    return payload


def _party_membership_change(change: dict[str, Any]) -> bool | None:
    for key in ('party', 'partyNpc', 'inParty'):
        if key not in change:
            continue
        value = change.get(key)
        if isinstance(value, bool):
            return value
        return _text(value).lower() in {'1', 'true', 'yes', 'on'}
    return None


def _party_npc_remote_location_error(
    state: dict[str, Any],
    npc: dict[str, Any] | None,
    *,
    target_location_id: Any,
    membership_change: bool | None = None,
) -> str | None:
    target_id = _world_id(target_location_id)
    npc_id = _text(npc.get('id')) if isinstance(npc, dict) else ''
    if not target_id or not npc_id:
        return None
    is_party_npc = any(
        _npc_matches(candidate, npc_id)
        for candidate in _ensure_list(state, 'partyNpcs')
        if isinstance(candidate, dict)
    )
    if not is_party_npc or membership_change is False:
        return None
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    current_location_id = _world_id(scene.get('locationId'), scene.get('name'))
    if target_id == current_location_id:
        return None
    return 'Party NPCs must remain at the current scene unless they leave the party in the same NPC update.'


def _merge_npc(state: dict[str, Any], payload: dict[str, Any], *, party: bool | None = None) -> dict[str, Any]:
    known_collection = _ensure_list(state, 'knownNpcs')
    party_collection = _ensure_list(state, 'partyNpcs')
    record = _npc_record(state, npc_id=payload.get('id'), name=payload.get('name'))
    record_id = _text((record or payload).get('id'))
    already_in_party = any(_npc_matches(candidate, record_id) for candidate in party_collection)
    collection_key = 'partyNpcs' if (party if party is not None else already_in_party) else 'knownNpcs'
    collection = party_collection if collection_key == 'partyNpcs' else known_collection
    if not record:
        record = {
            'id': payload.get('id'),
            'name': payload.get('name'),
            'race': payload.get('race'),
            'role': payload.get('role'),
            'description': _text(payload.get('description')),
            'disposition': payload.get('disposition') or 'unknown',
            'relationship': payload.get('relationship') if isinstance(payload.get('relationship'), dict) else {'score': 0, 'label': 'neutral'},
            'locationId': payload.get('locationId'),
            'status': payload.get('status') or 'known',
            'faction': payload.get('faction'),
            'aliases': _string_list(payload.get('aliases')),
            'questIds': _string_list(payload.get('questIds')),
            'memory': _string_list(payload.get('memory')),
            'firstMetTurn': payload.get('firstMetTurn'),
            'lastSeenTurn': payload.get('lastSeenTurn'),
            'metadata': payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {},
        }
        _merge_record_source(record, payload)
        collection.append(record)
        return record

    # Membership is exclusive. An explicit party flag moves the existing
    # record atomically; an omitted flag preserves its current collection.
    for key in ('knownNpcs', 'partyNpcs'):
        state[key] = [
            candidate
            for candidate in _ensure_list(state, key)
            if not _npc_matches(candidate, record_id)
        ]
    state[collection_key].append(record)
    for key in ('name', 'race', 'role', 'disposition', 'locationId', 'status', 'faction', 'firstMetTurn', 'lastSeenTurn'):
        if key == 'firstMetTurn' and record.get(key):
            continue
        if key == 'name' and payload.get('name') == payload.get('id') and record.get('name'):
            continue
        _set_if_present(record, key, payload.get(key))
    _merge_rich_text(record, 'description', payload.get('description'))
    record['aliases'] = _merge_unique(record.get('aliases'), payload.get('aliases'))
    record['questIds'] = _merge_unique(record.get('questIds'), payload.get('questIds'))
    record['memory'] = _merge_unique(record.get('memory'), payload.get('memory'))
    relationship = record.setdefault('relationship', {})
    if not isinstance(relationship, dict):
        relationship = {}
        record['relationship'] = relationship
    if isinstance(payload.get('relationship'), dict):
        relationship.update({key: value for key, value in payload['relationship'].items() if value not in (None, '')})
    relationship.setdefault('score', 0)
    relationship.setdefault('label', 'neutral')
    _merge_metadata(record, payload.get('metadata'))
    _merge_record_source(record, payload)
    return record


def _link_npc_and_quest_refs(
    state: dict[str, Any],
    npc: dict[str, Any],
    *,
    previous_location_id: Any = None,
) -> None:
    npc_id = _text(npc.get('id'))
    if not npc_id:
        return
    location_id = _text(npc.get('locationId'))
    old_location_id = _text(previous_location_id)
    if old_location_id and old_location_id != location_id:
        _remove_npc_location_reference(state, location_id=old_location_id, npc_id=npc_id)
    if location_id:
        _add_npc_location_reference(state, location_id=location_id, npc_id=npc_id)
    for quest_id in _string_list(npc.get('questIds')):
        quest = _quest_record(state, quest_id=quest_id)
        if quest:
            quest['relatedNpcIds'] = _merge_unique(quest.get('relatedNpcIds'), [npc_id])
    _reconcile_current_scene_npcs(state)


def _apply_relationship_update(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    npc = _npc_record(state, npc_id=change.get('npcId'), name=change.get('name'))
    if not npc:
        return None
    relationship = npc.setdefault('relationship', {})
    if not isinstance(relationship, dict):
        relationship = {}
        npc['relationship'] = relationship
    current = int_or_default(relationship.get('score'), default=0)
    if change.get('scoreDelta') is not None:
        relationship['score'] = max(-100, min(100, current + int_or_default(change.get('scoreDelta'), default=0)))
    elif change.get('relationshipScore') is not None:
        relationship['score'] = max(-100, min(100, int_or_default(change.get('relationshipScore'), default=current)))
    elif isinstance(change.get('relationship'), dict) and change['relationship'].get('score') is not None:
        relationship['score'] = max(-100, min(100, int_or_default(change['relationship'].get('score'), default=current)))
    if change.get('relationshipLabel'):
        relationship['label'] = _text(change.get('relationshipLabel'))
    elif isinstance(change.get('relationship'), dict) and change['relationship'].get('label'):
        relationship['label'] = _text(change['relationship'].get('label'))
    relationship.setdefault('score', 0)
    relationship.setdefault('label', 'neutral')
    return npc


def _combat_participant(combat: dict[str, Any], participant_id: Any) -> dict[str, Any] | None:
    requested = _text(participant_id)
    if not requested:
        return None
    for participant in combat.get('participants') or []:
        if isinstance(participant, dict) and _text(participant.get('id')) == requested:
            return participant
    requested_keys = _combat_reference_keys(requested)
    matches = [
        participant
        for participant in combat.get('participants') or []
        if isinstance(participant, dict)
        and requested_keys.intersection(_combat_participant_reference_keys(participant))
    ]
    unique_ids = {_text(participant.get('id')) for participant in matches if _text(participant.get('id'))}
    if len(unique_ids) == 1:
        resolved_id = next(iter(unique_ids))
        return next(participant for participant in matches if _text(participant.get('id')) == resolved_id)
    return None


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


def _sync_actor_health_to_combat_participant(state: dict[str, Any], actor: dict[str, Any]) -> None:
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else None
    if not combat:
        return
    participant = _combat_participant(combat, actor.get('id'))
    if not participant:
        return
    health = actor.get('health') if isinstance(actor.get('health'), dict) else {}
    hp = participant.setdefault('hp', {})
    current = max(0, int_or_default(health.get('currentHp'), default=hp.get('current') or 0))
    maximum = max(current, int_or_default(health.get('maxHp'), default=hp.get('max') or current))
    hp['current'] = current
    hp['max'] = maximum
    hp['temp'] = max(0, int_or_default(health.get('tempHp'), default=hp.get('temp') or 0))
    if participant.get('team') == 'player':
        participant['isAlive'] = participant.get('isAlive', True)
        participant['isConscious'] = current > 0


def _sync_player_actor_from_combat_participant(state: dict[str, Any], participant: dict[str, Any]) -> None:
    if participant.get('team') != 'player':
        return
    actor = find_actor(state, participant.get('id'))
    if not actor:
        return

    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    if hp:
        health = actor.setdefault('health', {})
        current = max(0, int_or_default(hp.get('current', hp.get('currentHp')), default=health.get('currentHp') or 0))
        maximum = max(current, int_or_default(hp.get('max', hp.get('maxHp')), default=health.get('maxHp') or current))
        health['currentHp'] = current
        health['maxHp'] = maximum
        health['tempHp'] = max(0, int_or_default(hp.get('temp', hp.get('tempHp')), default=health.get('tempHp') or 0))
    if 'conditions' in participant:
        actor.setdefault('health', {})['conditions'] = _string_list(participant.get('conditions'))


def _resolve_damage_concentration(
    state: dict[str, Any],
    *,
    participant_id: Any,
    damage: Any,
) -> dict[str, Any] | None:
    damage_amount = max(0, int_or_default(damage, default=0))
    if damage_amount <= 0:
        return None
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    participant = _combat_participant(combat, participant_id)
    if not participant:
        return None
    actor = find_actor(state, participant.get('id'))
    resources = (
        actor.get('spellResources')
        if isinstance(actor, dict) and isinstance(actor.get('spellResources'), dict)
        else participant.get('spellResources')
        if isinstance(participant.get('spellResources'), dict)
        else {}
    )
    if not isinstance(resources.get('concentration'), dict):
        return None
    resolved = resolve_concentration_check(
        combat,
        resources,
        caster_id=_text(participant.get('id')),
        damage=damage_amount,
        roller=lambda sides: secrets.randbelow(max(1, sides)) + 1,
    )
    if not resolved.get('ok'):
        return None
    state['combat'] = deepcopy(resolved['combat'])
    next_participant = _combat_participant(state['combat'], participant.get('id'))
    if isinstance(actor, dict):
        actor['spellResources'] = deepcopy(resolved['casterResources'])
        if isinstance(next_participant, dict):
            _sync_player_actor_from_combat_participant(state, next_participant)
    elif isinstance(next_participant, dict):
        next_participant['spellResources'] = deepcopy(resolved['casterResources'])
    return {
        'required': resolved.get('required'),
        'maintained': resolved.get('maintained'),
        'reason': resolved.get('reason'),
        'check': deepcopy(resolved.get('check')),
        'removedEffects': deepcopy(resolved.get('removedEffects') or []),
    }


def _sync_actor_level_to_combat_participant(state: dict[str, Any], actor: dict[str, Any]) -> None:
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else None
    if not combat:
        return
    participant = _combat_participant(combat, actor.get('id'))
    if participant:
        participant['level'] = actor.get('level')


def _sync_scene_combat_state(state: dict[str, Any], combat: dict[str, Any]) -> None:
    scene = _ensure_scene(state)
    status = _text(combat.get('status'))
    if status in {'starting', 'active'}:
        scene['sceneType'] = 'combat'
        scene['combatState'] = 'active'
        scene['dangerLevel'] = max(8, int_or_default(scene.get('dangerLevel'), default=0))
    elif status == 'ended':
        scene['combatState'] = 'resolved'
        scene['dangerLevel'] = min(int_or_default(scene.get('dangerLevel'), default=0), 4)
        if scene.get('sceneType') == 'combat':
            scene['sceneType'] = 'exploration'
        if scene.get('mood') == 'dangerous':
            scene['mood'] = 'calm'
    elif status == 'none':
        scene['combatState'] = 'none'


def _apply_combat_start(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    change = materialize_campaign_pack_combat_start(state, change)
    combat_payload = change.get('combat') if isinstance(change.get('combat'), dict) else change
    combat = normalize_combat_state(
        {
            **combat_payload,
            'status': combat_payload.get('status') or 'active',
            'round': combat_payload.get('round') or 1,
        },
        state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {},
    )
    state['combat'] = combat
    _sync_scene_combat_state(state, combat)
    return combat


def _apply_combat_update(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    combat = ensure_combat_state(state)
    previous_flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    previous_economy = (
        previous_flags.get('turnEconomy')
        if isinstance(previous_flags.get('turnEconomy'), dict)
        else {}
    )
    previous_actor_id = _text(
        previous_flags.get('activeActorId')
        or previous_flags.get('active_actor_id')
        or previous_economy.get('actorId')
        or previous_economy.get('actor_id')
    )
    previous_turn_index = combat.get('turnIndex')
    previous_round = combat.get('round')
    for key in ('status', 'round', 'turnIndex', 'lastRoundSummary', 'encounterGoal'):
        _set_if_present(combat, key, change.get(key))
    if isinstance(change.get('flags'), dict):
        flags = combat.setdefault('flags', {})
        if not isinstance(flags, dict):
            flags = {}
            combat['flags'] = flags
        flags.update(change['flags'])
    turn_advanced = bool(
        previous_actor_id
        and (
            combat.get('turnIndex') != previous_turn_index
            or combat.get('round') != previous_round
        )
    )
    if turn_advanced:
        target_tick = advance_spell_effect_durations(
            combat,
            timing='target_turn_end',
            actor_id=previous_actor_id,
        )
        if target_tick.get('ok'):
            combat = target_tick['combat']
        source_tick = advance_spell_effect_durations(
            combat,
            timing='source_turn_end',
            actor_id=previous_actor_id,
        )
        if source_tick.get('ok'):
            combat = source_tick['combat']
        state['combat'] = combat
        for participant in combat.get('participants') or []:
            if isinstance(participant, dict):
                _sync_player_actor_from_combat_participant(state, participant)
    _sync_scene_combat_state(state, combat)
    return combat


def _apply_combat_participant_update(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    combat = ensure_combat_state(state)
    participant = _combat_participant(combat, change.get('participantId'))
    if not participant:
        return None
    replacement = normalize_participant(change.get('participant')) if isinstance(change.get('participant'), dict) else None
    if replacement:
        participant.update(replacement)
    hp = change.get('hp') if isinstance(change.get('hp'), dict) else {}
    if hp:
        current = max(0, int_or_default(hp.get('current', hp.get('currentHp', participant.get('hp', {}).get('current'))), default=0))
        maximum = max(current, int_or_default(hp.get('max', hp.get('maxHp', participant.get('hp', {}).get('max'))), default=current))
        participant['hp'] = {
            'current': current,
            'max': maximum,
            'temp': max(0, int_or_default(hp.get('temp', participant.get('hp', {}).get('temp')), default=0)),
        }
        participant['isAlive'] = current > 0
        participant['isConscious'] = current > 0 and bool(change.get('isConscious', participant.get('isConscious', True)))
    if 'conditions' in change:
        participant['conditions'] = _string_list(change.get('conditions'))
    if isinstance(change.get('position'), dict):
        participant['position'] = change['position']
    for key in ('isAlive', 'isConscious'):
        if key in change:
            participant[key] = bool(change[key])
    _sync_player_actor_from_combat_participant(state, participant)
    return participant


def _apply_combat_intent(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    combat = ensure_combat_state(state)
    participant = _combat_participant(combat, change.get('participantId'))
    if not participant:
        return None
    participant['currentIntent'] = change.get('intent') if isinstance(change.get('intent'), dict) else None
    return participant


def _apply_combat_morale(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    combat = ensure_combat_state(state)
    participant = _combat_participant(combat, change.get('participantId'))
    if not participant:
        return None
    participant['morale'] = max(0, min(100, int_or_default(change.get('morale'), default=participant.get('morale') or 50)))
    if change.get('event'):
        participant['moraleEvents'] = _merge_unique(participant.get('moraleEvents'), [change.get('event')])
    return participant


def _apply_combat_move(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    combat = ensure_combat_state(state)
    participant = _combat_participant(combat, change.get('participantId'))
    if not participant:
        return None
    position = normalize_position({**(participant.get('position') if isinstance(participant.get('position'), dict) else {}), 'rangeBand': change.get('toRangeBand')})
    for key in ('zoneId', 'coverId', 'isHidden'):
        if key in change:
            position[key] = change[key]
    participant['position'] = position
    return participant


def _apply_combat_condition(state: dict[str, Any], change: dict[str, Any], *, add: bool) -> dict[str, Any] | None:
    combat = ensure_combat_state(state)
    participant = _combat_participant(combat, change.get('participantId'))
    if not participant:
        return None
    condition = _text(change.get('condition')).lower().replace(' ', '_')
    conditions = [str(item).strip().lower().replace(' ', '_') for item in participant.get('conditions') or [] if str(item or '').strip()]
    if add and condition and condition not in conditions:
        conditions.append(condition)
    if not add:
        conditions = [item for item in conditions if item != condition]
    participant['conditions'] = conditions
    if condition in {'fled', 'escaped'} and add:
        participant['isConscious'] = False
    if condition == 'surrendered' and add:
        participant['currentIntent'] = None
    _sync_player_actor_from_combat_participant(state, participant)
    return participant


def _apply_combat_ability_mark_used(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    combat = ensure_combat_state(state)
    participant = _combat_participant(combat, change.get('participantId'))
    if not participant:
        return None
    ability_id = _text(change.get('abilityId'))
    for ability in participant.get('abilities') or []:
        if not isinstance(ability, dict) or _text(ability.get('id')) != ability_id:
            continue
        if ability.get('usesRemaining') is not None:
            ability['usesRemaining'] = max(0, int_or_default(ability.get('usesRemaining'), default=1) - 1)
        ability['lastUsedRound'] = (combat.get('round') or 1)
        if ability.get('cooldown') in {'once_per_combat', 'short_rest', 'long_rest'}:
            ability['used'] = True
        return participant
    return None


def _combat_reward_transaction_changes(
    result: dict[str, Any],
    end_change: dict[str, Any],
) -> list[dict[str, Any]]:
    """Queue derived outputs followed by an all-outputs-present marker."""

    outcome_ledger_id = _text(result.get('outcomeLedgerId'))
    if not outcome_ledger_id:
        return []
    outputs = [
        deepcopy(change)
        for change in [*(result.get('changes') or []), *(result.get('questEvents') or [])]
        if isinstance(change, dict)
    ]
    required_ids = sorted(
        {
            _text(value)
            for value in (result.get('ledgerIds') or [])
            if _text(value) and _text(value) != outcome_ledger_id
        }
    )
    outputs.append(
        {
            'id': outcome_ledger_id,
            'turnId': end_change.get('turnId') or end_change.get('turn_id'),
            'type': 'combat.reward.finalize',
            'source': COMBAT_REWARD_SOURCE,
            'encounterId': result.get('encounterId'),
            'combatOutcome': result.get('outcome'),
            'endReason': result.get('endReason'),
            'requiredChangeIds': required_ids,
            'visible': False,
            'reason': 'Finalize the exact-once encounter reward transaction.',
        }
    )
    return outputs


def _apply_interactable_action_state(
    state: dict[str, Any],
    change: dict[str, Any],
) -> dict[str, Any] | None:
    resolved = change.get('resolvedState') if isinstance(change.get('resolvedState'), dict) else None
    if resolved is None:
        return None
    resolved_scene = resolved.get('currentScene') if isinstance(resolved.get('currentScene'), dict) else None
    resolved_locations = resolved.get('locations') if isinstance(resolved.get('locations'), list) else None
    if resolved_scene is None or resolved_locations is None:
        return None
    scene = _ensure_scene(state)
    if _text(scene.get('locationId')) != _text(change.get('locationId')):
        return None
    for collection in ('interactables', 'hazards'):
        scene[collection] = deepcopy(
            resolved_scene.get(collection)
            if isinstance(resolved_scene.get(collection), list)
            else []
        )
    state['locations'] = deepcopy(resolved_locations)
    resolved_ledger = resolved.get('interactableActionLedger')
    if isinstance(resolved_ledger, dict):
        state['interactableActionLedger'] = deepcopy(resolved_ledger)
    event = change.get('event') if isinstance(change.get('event'), dict) else None
    if event:
        event_ledger = state.setdefault('gameplayEventLedger', [])
        if not isinstance(event_ledger, list):
            event_ledger = []
            state['gameplayEventLedger'] = event_ledger
        event_ledger.append(
            {
                'id': event.get('id'),
                'type': event.get('type'),
                'actorId': event.get('actorId'),
                'targetId': event.get('targetId'),
                'locationId': event.get('locationId'),
                'turnId': change.get('turnId'),
            }
        )
    return {
        'objectAction': change.get('objectAction'),
        'targetId': change.get('targetId'),
        'locationId': change.get('locationId'),
        'event': deepcopy(event),
        'eventType': event.get('type') if event else None,
        'targetName': event.get('targetName') if event else None,
        'mechanicalEffectsDeferred': bool(event and event.get('mechanicalEffects')),
    }


def apply_state_changes(previous_state: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    next_state = deepcopy(previous_state)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_ids = {str(entry.get('id')) for entry in next_state.get('stateChangeLedger', []) if isinstance(entry, dict)}

    pending_changes = [deepcopy(change) for change in changes if isinstance(change, dict)]
    for raw_change in pending_changes:
        if not isinstance(raw_change, dict):
            continue
        change = deepcopy(raw_change)
        change_id = str(change.get('id') or '').strip()
        if change_id and change_id in seen_ids:
            # A retried terminal combat event is also the recovery hook for a
            # partially-applied reward transaction. Stable output IDs make the
            # derivation safe to enqueue again; the ledger filters completed
            # pieces and the final marker closes only after every piece lands.
            if str(change.get('type') or '').strip() == 'combat.end':
                recovered_rewards = derive_combat_outcome_rewards(next_state, change)
                if recovered_rewards.get('valid') and not recovered_rewards.get('alreadyApplied'):
                    pending_changes.extend(
                        _combat_reward_transaction_changes(recovered_rewards, change)
                    )
            if str(change.get('type') or '').strip() == 'scene.interactable.action':
                event = change.get('event') if isinstance(change.get('event'), dict) else None
                if event:
                    pending_changes.extend(derive_quest_changes(next_state, event))
            skipped.append({'change': change, 'reason': 'State change was already applied.'})
            continue

        change_type = str(change.get('type') or '').strip()
        actor_id = _change_value(change, 'actorId', 'actor_id')
        actor = find_actor(next_state, actor_id) if actor_id is not None else None
        applied_change = deepcopy(change)
        applied_change['actualAmount'] = None

        if change_type == 'inventory.add' and actor:
            inventory = actor.setdefault('inventory', {})
            items = inventory.setdefault('items', [])
            incoming_item = _item_payload(change)
            original_item_id = incoming_item.get('id')
            item = _merge_item(items, incoming_item)
            if item is None and str(change.get('source') or '').strip().lower() == 'quest_engine':
                incoming_item = {
                    **incoming_item,
                    'id': _quest_reward_collision_item_id(change, actor, incoming_item),
                    'sourceItemId': original_item_id,
                }
                item = _merge_item(items, incoming_item)
                if item is not None:
                    applied_change['sourceItemId'] = original_item_id
            if item is None:
                skipped.append({'change': change, 'reason': 'Item identity conflicts with an existing inventory item.'})
                continue
            _sync_actor_and_combat_armor_class(next_state, actor)
            applied_change['itemId'] = item.get('id')
            applied_change['itemName'] = item.get('name')
            applied_change['actualAmount'] = max(1, int_or_default(change.get('quantity', item.get('quantity')), default=1))
        elif change_type == 'inventory.remove' and actor:
            removed = _remove_item(actor_items(actor), change)
            if not removed:
                skipped.append({'change': change, 'reason': 'Item missing during inventory removal.'})
                continue
            _sync_actor_and_combat_armor_class(next_state, actor)
            applied_change['itemId'] = _change_value(change, 'itemId', 'item_id') or (removed or {}).get('id')
            applied_change['itemName'] = _change_value(change, 'itemName', 'item_name') or (removed or {}).get('name')
            applied_change['actualAmount'] = max(1, int_or_default(change.get('quantity'), default=1))
        elif change_type == 'inventory.equip' and actor:
            item, conflicts, slot = _equip_item(actor_items(actor), change)
            if not item or not slot:
                skipped.append({'change': change, 'reason': 'Item missing or not equippable during equip.'})
                continue
            _sync_actor_and_combat_armor_class(next_state, actor)
            applied_change['itemId'] = item.get('id')
            applied_change['itemName'] = item.get('name')
            applied_change['slot'] = slot
            applied_change['slotLabel'] = equipment_slot_label(slot)
            applied_change['conflictItemIds'] = [conflict.get('id') for conflict in conflicts if conflict.get('id')]
            applied_change['conflictItemNames'] = [conflict.get('name') for conflict in conflicts if conflict.get('name')]
        elif change_type == 'inventory.unequip' and actor:
            item = _unequip_item(actor_items(actor), change)
            if not item:
                skipped.append({'change': change, 'reason': 'Item missing during unequip.'})
                continue
            _sync_actor_and_combat_armor_class(next_state, actor)
            applied_change['itemId'] = item.get('id')
            applied_change['itemName'] = item.get('name')
            applied_change['slot'] = item.get('slot')
            applied_change['slotLabel'] = equipment_slot_label(item.get('slot'))
        elif change_type == 'inventory.mark_used' and actor:
            item = _find_item(actor_items(actor), item_id=_change_value(change, 'itemId', 'item_id'))
            if item:
                item['lastUsedAtTurn'] = change.get('turnId') or change.get('turn_id') or item.get('lastUsedAtTurn')
                applied_change['itemName'] = item.get('name')
        elif change_type == 'race_ability.mark_used' and actor:
            ability_id = _text(_change_value(change, 'abilityId', 'ability_id'))
            if not ability_id:
                skipped.append({'change': change, 'reason': 'Race ability id missing during mark used.'})
                continue
            ability_state = actor.setdefault('raceAbilityState', {})
            ability_state[ability_id] = {
                'available': False,
                'usedAtTurn': change.get('turnId') or change.get('turn_id'),
                'refreshesOn': _text(_change_value(change, 'refreshesOn', 'refreshes_on')) or 'short_rest',
            }
            applied_change['abilityId'] = ability_id
        elif change_type == 'race_ability.refresh' and actor:
            ability_id = _text(_change_value(change, 'abilityId', 'ability_id'))
            if not ability_id:
                skipped.append({'change': change, 'reason': 'Race ability id missing during refresh.'})
                continue
            ability_state = actor.setdefault('raceAbilityState', {})
            current = ability_state.setdefault(ability_id, {})
            current['available'] = True
            current.pop('usedAtTurn', None)
            applied_change['abilityId'] = ability_id
        elif change_type == 'currency.add' and actor:
            applied_change['actualAmount'] = _apply_currency(actor, change, 1)
        elif change_type == 'currency.remove' and actor:
            currency_code = str(change.get('currency') or '').strip().lower()
            requested_amount = max(0, int_or_default(change.get('amount'), default=0))
            if actor_currency(actor).get(currency_code, 0) < requested_amount:
                skipped.append({'change': change, 'reason': 'Insufficient currency during removal.'})
                continue
            applied_change['actualAmount'] = abs(_apply_currency(actor, change, -1))
        elif change_type == 'health.heal' and actor:
            applied_change['actualAmount'] = _apply_health_heal(actor, change)
            _sync_actor_health_to_combat_participant(next_state, actor)
        elif change_type == 'health.max.set' and actor:
            applied_change.update(_apply_health_max_set(actor, change))
            applied_change['actualAmount'] = applied_change.get('maxHpDelta', 0)
            _sync_actor_health_to_combat_participant(next_state, actor)
        elif change_type == 'health.damage' and actor:
            result = _apply_health_damage(actor, change)
            _sync_actor_health_to_combat_participant(next_state, actor)
            applied_change.update(result)
            applied_change['actualAmount'] = result['amount']
            concentration_check = _resolve_damage_concentration(
                next_state,
                participant_id=actor.get('id'),
                damage=result['amount'],
            )
            if concentration_check:
                applied_change['concentrationCheck'] = concentration_check
        elif change_type == 'xp.add' and actor:
            applied_change['actualAmount'] = _apply_xp(actor, change, 1)
            _sync_actor_level_to_combat_participant(next_state, actor)
        elif change_type == 'xp.remove' and actor:
            applied_change['actualAmount'] = _apply_xp(actor, change, -1)
            _sync_actor_level_to_combat_participant(next_state, actor)
        elif change_type == 'spell.learn' and actor:
            result = _apply_spell_learn(actor, change)
            if not result:
                skipped.append({'change': change, 'reason': 'Spell missing during learn application.'})
                continue
            applied_change.update(result)
            applied_change['actualAmount'] = 0 if result.get('alreadyKnown') else 1
        elif change_type == 'spell.cast' and actor:
            result = _apply_spell_cast(next_state, actor, change)
            if not result:
                skipped.append({'change': change, 'reason': 'Spell resource was unavailable during cast application.'})
                continue
            applied_change.update(result)
            applied_change['actualAmount'] = 1 if result.get('resourceConsumed') else 0
        elif change_type == 'class_feature.use' and actor:
            result = _apply_class_feature_use(next_state, actor, change)
            if not result:
                skipped.append({'change': change, 'reason': 'Class capability was unavailable during application.'})
                continue
            applied_change.update(result)
            applied_change['actualAmount'] = result.get('actualAmount', 0)
        elif change_type == 'rest.complete' and actor:
            result = _apply_rest(actor, change)
            _sync_actor_health_to_combat_participant(next_state, actor)
            applied_change.update(result)
            applied_change['actualAmount'] = result.get('hpRestored', 0)
        elif change_type == 'scene.interactable.action' and actor:
            result = _apply_interactable_action_state(next_state, change)
            if not result:
                skipped.append({'change': change, 'reason': 'Scene-object transition was stale during application.'})
                continue
            applied_change.update(result)
        elif change_type == 'combat.start':
            combat = _apply_combat_start(next_state, change)
            applied_change['combatStatus'] = combat.get('status')
            applied_change['participantCount'] = len(combat.get('participants') or [])
        elif change_type == 'combat.update':
            combat = _apply_combat_update(next_state, change)
            applied_change['combatStatus'] = combat.get('status')
            applied_change['round'] = combat.get('round')
        elif change_type == 'combat.round.advance':
            combat = ensure_combat_state(next_state)
            combat['round'] = max(1, int_or_default(change.get('round'), default=int_or_default(combat.get('round'), default=1) + 1))
            combat['turnIndex'] = 0
            if change.get('summary'):
                combat['lastRoundSummary'] = change.get('summary')
            applied_change['combatStatus'] = combat.get('status')
            applied_change['round'] = combat.get('round')
        elif change_type == 'combat.battlefield.update':
            combat = ensure_combat_state(next_state)
            combat['battlefield'] = normalize_battlefield(
                change.get('battlefield'),
                next_state.get('currentScene') if isinstance(next_state.get('currentScene'), dict) else {},
            )
            applied_change['battlefieldType'] = combat['battlefield'].get('environmentType')
        elif change_type == 'combat.participant.update':
            combat_before = ensure_combat_state(next_state)
            participant_before = _combat_participant(combat_before, change.get('participantId'))
            hp_before = (
                int_or_default((participant_before.get('hp') or {}).get('current'), default=0)
                + int_or_default((participant_before.get('hp') or {}).get('temp'), default=0)
                if isinstance(participant_before, dict)
                else 0
            )
            participant = _apply_combat_participant_update(next_state, change)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during update.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            hp_after = int_or_default((participant.get('hp') or {}).get('current'), default=hp_before) + int_or_default(
                (participant.get('hp') or {}).get('temp'),
                default=0,
            )
            concentration_check = _resolve_damage_concentration(
                next_state,
                participant_id=participant.get('id'),
                damage=max(0, hp_before - hp_after),
            )
            if concentration_check:
                applied_change['concentrationCheck'] = concentration_check
        elif change_type == 'combat.move':
            participant = _apply_combat_move(next_state, change)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during movement.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['toRangeBand'] = (participant.get('position') or {}).get('rangeBand')
        elif change_type == 'combat.condition.add':
            participant = _apply_combat_condition(next_state, change, add=True)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during condition add.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['condition'] = change.get('condition')
        elif change_type == 'combat.condition.remove':
            participant = _apply_combat_condition(next_state, change, add=False)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during condition remove.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['condition'] = change.get('condition')
        elif change_type == 'combat.ability.mark_used':
            participant = _apply_combat_ability_mark_used(next_state, change)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant or ability missing during ability mark used.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['abilityId'] = change.get('abilityId')
        elif change_type == 'combat.intent.set':
            participant = _apply_combat_intent(next_state, change)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during intent update.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['intentType'] = (participant.get('currentIntent') or {}).get('intentType') if isinstance(participant.get('currentIntent'), dict) else None
        elif change_type == 'combat.morale.update':
            participant = _apply_combat_morale(next_state, change)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during morale update.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['morale'] = participant.get('morale')
        elif change_type == 'combat.morale.event':
            participant = _apply_combat_morale(next_state, change)
            if not participant:
                skipped.append({'change': change, 'reason': 'Combat participant missing during morale event.'})
                continue
            applied_change['participantId'] = participant.get('id')
            applied_change['participantName'] = participant.get('name')
            applied_change['morale'] = participant.get('morale')
            applied_change['event'] = change.get('event')
        elif change_type == 'combat.end':
            combat = ensure_combat_state(next_state)
            combat['status'] = change.get('status') or 'ended'
            combat['lastRoundSummary'] = change.get('summary') or change.get('reason') or combat.get('lastRoundSummary')
            flags = combat.setdefault('flags', {})
            if change.get('endReason'):
                flags['endReason'] = change.get('endReason')
            encounter_id = _text(flags.get('campaignPackEncounterId') or flags.get('campaign_pack_encounter_id'))
            end_reason = _text(flags.get('endReason') or flags.get('end_reason')).lower()
            if encounter_id and end_reason in {
                'all_enemies_defeated',
                'enemies_fled',
                'enemies_surrendered',
                'negotiated_resolution',
                'objective_completed',
            }:
                state_flags = next_state.setdefault('flags', {})
                if not isinstance(state_flags, dict):
                    state_flags = {}
                    next_state['flags'] = state_flags
                completed_encounter_ids = _merge_unique(
                    state_flags.get('campaignPackCompletedEncounterIds'),
                    [encounter_id],
                )
                state_flags['campaignPackCompletedEncounterIds'] = completed_encounter_ids
                flags['campaignPackCompletedEncounterIds'] = completed_encounter_ids
            for participant in combat.get('participants') or []:
                if not isinstance(participant, dict):
                    continue
                if participant.get('team') == 'enemy':
                    participant['currentIntent'] = None
                    if 'morale' in participant:
                        participant['morale'] = min(int_or_default(participant.get('morale'), default=0), 10)
            _sync_scene_combat_state(next_state, combat)
            applied_change['combatStatus'] = combat.get('status')
        elif change_type == 'combat.reward.finalize' and str(change.get('source') or '') == COMBAT_REWARD_SOURCE:
            required_ids = {
                str(value).strip()
                for value in (change.get('requiredChangeIds') or [])
                if str(value or '').strip()
            }
            if not required_ids.issubset(seen_ids):
                skipped.append(
                    {
                        'change': change,
                        'reason': 'Encounter reward transaction is incomplete; final marker was not persisted.',
                    }
                )
                continue
            reward_ledger = next_state.setdefault('combatRewardLedger', [])
            if not isinstance(reward_ledger, list):
                reward_ledger = []
                next_state['combatRewardLedger'] = reward_ledger
            reward_ledger.append(
                {
                    'id': change_id,
                    'encounterId': change.get('encounterId'),
                    'combatOutcome': change.get('combatOutcome'),
                    'endReason': change.get('endReason'),
                    'turnId': change.get('turnId'),
                    'rewardChangeIds': sorted(required_ids),
                }
            )
            combat = ensure_combat_state(next_state)
            combat.setdefault('flags', {})['lastOutcomeRewards'] = {
                'id': change_id,
                'encounterId': change.get('encounterId'),
                'outcome': change.get('combatOutcome'),
                'endReason': change.get('endReason'),
                'rewardChangeIds': sorted(required_ids),
            }
            applied_change['rewardChangeCount'] = len(required_ids)
        elif (
            str(change.get('source') or '') == COMBAT_REWARD_SOURCE
            and change.get('questId')
            and change.get('eventType')
        ):
            event_ledger = next_state.setdefault('gameplayEventLedger', [])
            if not isinstance(event_ledger, list):
                event_ledger = []
                next_state['gameplayEventLedger'] = event_ledger
            event_ledger.append(
                {
                    'id': change_id,
                    'type': change_type,
                    'eventType': change.get('eventType'),
                    'questId': change.get('questId'),
                    'objectiveId': change.get('objectiveId'),
                    'encounterId': change.get('encounterId'),
                    'turnId': change.get('turnId'),
                }
            )
            applied_change['questId'] = change.get('questId')
            applied_change['objectiveId'] = change.get('objectiveId')
        elif change_type == 'scene.update':
            scene = _ensure_scene(next_state)
            _apply_scene_fields(scene, change)
            applied_change['sceneName'] = scene.get('name')
        elif change_type == 'scene.move_location':
            location = _apply_scene_move(next_state, change)
            applied_change['locationId'] = location.get('id')
            applied_change['locationName'] = location.get('name')
        elif change_type == 'scene.item.add':
            scene = _ensure_scene(next_state)
            item = _merge_scene_item(_scene_items(scene), _item_payload(change))
            if item is None:
                skipped.append({'change': change, 'reason': 'Item identity conflicts with an existing scene item.'})
                continue
            applied_change['itemId'] = item.get('id')
            applied_change['itemName'] = item.get('name')
            applied_change['actualAmount'] = max(1, int_or_default(change.get('quantity', item.get('quantity')), default=1))
            if change.get('sourceActorId') or change.get('fromActorId'):
                applied_change['sourceActorId'] = change.get('sourceActorId') or change.get('fromActorId')
        elif change_type == 'scene.item.remove':
            scene = _ensure_scene(next_state)
            removed = _remove_item(_scene_items(scene), change)
            if not removed:
                skipped.append({'change': change, 'reason': 'Scene item missing during removal.'})
                continue
            applied_change['itemId'] = _change_value(change, 'itemId', 'item_id') or removed.get('id')
            applied_change['itemName'] = _change_value(change, 'itemName', 'item_name') or removed.get('name')
            applied_change['actualAmount'] = max(1, int_or_default(change.get('quantity'), default=1))
        elif change_type in {'location.discover', 'location.update'}:
            if change_type == 'location.update' and not _location_record(
                next_state,
                location_id=change.get('locationId'),
            ):
                skipped.append({'change': change, 'reason': 'Location update target missing during application.'})
                continue
            payload = _location_payload(change, status='discovered' if change_type == 'location.discover' else None)
            location = _merge_location(next_state, payload)
            applied_change['locationId'] = location.get('id')
            applied_change['locationName'] = location.get('name')
        elif change_type == 'location.connect':
            first_payload = _location_payload(
                {**change, 'locationId': change.get('locationId'), 'name': change.get('name')},
                status='discovered',
            )
            second_payload = _location_payload(
                {
                    **change,
                    'locationId': change.get('connectedLocationId'),
                    'name': change.get('connectedLocationName') or change.get('toLocationName'),
                    'connectedLocationIds': [change.get('locationId')],
                },
                status='discovered',
            )
            first = _merge_location(next_state, first_payload)
            second = _merge_location(next_state, second_payload)
            first['connectedLocationIds'] = _merge_unique(first.get('connectedLocationIds'), [second.get('id')])
            second['connectedLocationIds'] = _merge_unique(second.get('connectedLocationIds'), [first.get('id')])
            applied_change['locationId'] = first.get('id')
            applied_change['connectedLocationId'] = second.get('id')
        elif change_type in {'quest.add', 'quest.update'}:
            if change_type == 'quest.update' and not _quest_record(
                next_state,
                quest_id=change.get('questId'),
            ):
                skipped.append({'change': change, 'reason': 'Quest update target missing during application.'})
                continue
            quest = _merge_quest(next_state, _quest_payload(change))
            applied_change['questId'] = quest.get('id')
            applied_change['questTitle'] = quest.get('title')
            if quest.get('status') == 'active':
                scene = _ensure_scene(next_state)
                scene['activeQuestIds'] = _merge_unique(scene.get('activeQuestIds'), [quest.get('id')])
        elif change_type in {'quest.objective.add', 'quest.objective.update'}:
            if change_type == 'quest.objective.update':
                target_quest = _quest_record(next_state, quest_id=change.get('questId'))
                target_objective = _find_record(
                    _ensure_list(target_quest, 'objectives') if isinstance(target_quest, dict) else [],
                    record_id=change.get('objectiveId'),
                )
                if not target_objective:
                    skipped.append({'change': change, 'reason': 'Quest objective update target missing during application.'})
                    continue
            quest = _apply_objective_change(next_state, change)
            if not quest:
                skipped.append({'change': change, 'reason': 'Quest missing during objective update.'})
                continue
            applied_change['questId'] = quest.get('id')
            applied_change['questTitle'] = quest.get('title')
        elif change_type in {'quest.complete', 'quest.fail'}:
            quest = _quest_record(next_state, quest_id=change.get('questId'), title=change.get('title'))
            if not quest:
                skipped.append({'change': change, 'reason': 'Quest missing during status update.'})
                continue
            quest['status'] = 'completed' if change_type == 'quest.complete' else 'failed'
            turn_id = _turn_id(change)
            if turn_id is not None:
                quest['updatedAtTurn'] = turn_id
                if change_type == 'quest.complete':
                    quest['completedAtTurn'] = quest.get('completedAtTurn') or turn_id
            _remove_active_quest_id(next_state, quest.get('id'))
            applied_change['questId'] = quest.get('id')
            applied_change['questTitle'] = quest.get('title')
        elif change_type in {'npc.discover', 'npc.update'}:
            if change_type == 'npc.update' and not _npc_record(
                next_state,
                npc_id=change.get('npcId'),
            ):
                skipped.append({'change': change, 'reason': 'NPC update target missing during application.'})
                continue
            previous_npc = _npc_record(next_state, npc_id=change.get('npcId'), name=change.get('name'))
            previous_location_id = previous_npc.get('locationId') if isinstance(previous_npc, dict) else None
            membership_change = _party_membership_change(change)
            party_location_error = _party_npc_remote_location_error(
                next_state,
                previous_npc,
                target_location_id=change.get('locationId'),
                membership_change=membership_change if change_type == 'npc.update' else None,
            )
            if party_location_error:
                skipped.append({'change': change, 'reason': party_location_error})
                continue
            npc = _merge_npc(
                next_state,
                _npc_payload(change),
                party=membership_change,
            )
            _link_npc_and_quest_refs(next_state, npc, previous_location_id=previous_location_id)
            applied_change['npcId'] = npc.get('id')
            applied_change['npcName'] = npc.get('name')
        elif change_type == 'npc.move':
            npc = _npc_record(next_state, npc_id=change.get('npcId'), name=change.get('name'))
            if not npc:
                skipped.append({'change': change, 'reason': 'NPC missing during movement.'})
                continue
            party_location_error = _party_npc_remote_location_error(
                next_state,
                npc,
                target_location_id=change.get('locationId'),
            )
            if party_location_error:
                skipped.append({'change': change, 'reason': party_location_error})
                continue
            previous_location_id = npc.get('locationId')
            npc['locationId'] = _world_id(change.get('locationId'))
            turn_id = _turn_id(change)
            if turn_id is not None:
                npc['lastSeenTurn'] = turn_id
            _link_npc_and_quest_refs(next_state, npc, previous_location_id=previous_location_id)
            applied_change['npcId'] = npc.get('id')
            applied_change['npcName'] = npc.get('name')
        elif change_type == 'npc.relationship.update':
            npc = _apply_relationship_update(next_state, change)
            if not npc:
                skipped.append({'change': change, 'reason': 'NPC missing during relationship update.'})
                continue
            applied_change['npcId'] = npc.get('id')
            applied_change['npcName'] = npc.get('name')
        elif change_type in {'clue.discover', 'clue.update'}:
            clue = _merge_domain_record(
                next_state,
                'clues',
                _domain_record_payload(
                    change,
                    embedded_key='clue',
                    id_key='clueId',
                    default_status='discovered' if change_type == 'clue.discover' else 'known',
                ),
            )
            applied_change['clueId'] = clue.get('id')
            applied_change['clueTitle'] = clue.get('title')
        elif change_type == 'faction.discover':
            faction = _merge_domain_record(
                next_state,
                'factions',
                _domain_record_payload(change, embedded_key='faction', id_key='factionId', default_status='known'),
            )
            applied_change['factionId'] = faction.get('id')
            applied_change['factionTitle'] = faction.get('title')
        elif change_type == 'faction.relationship.update':
            faction = _apply_faction_relationship_update(next_state, change)
            applied_change['factionId'] = faction.get('id')
            applied_change['factionTitle'] = faction.get('title')
        elif change_type in {'map.reveal', 'map.region.update'}:
            map_record, region = _apply_map_change(next_state, change)
            applied_change['mapId'] = map_record.get('id')
            applied_change['mapTitle'] = map_record.get('title')
            if region:
                applied_change['regionId'] = region.get('id')
        elif change_type == 'handout.reveal':
            handout = _merge_domain_record(
                next_state,
                'handouts',
                _domain_record_payload(change, embedded_key='handout', id_key='handoutId', default_status='revealed'),
            )
            applied_change['handoutId'] = handout.get('id')
            applied_change['handoutTitle'] = handout.get('title')
        elif change_type == 'lore.unlock':
            lore = _merge_domain_record(
                next_state,
                'lore',
                _domain_record_payload(change, embedded_key='lore', id_key='loreId', default_status='unlocked'),
            )
            applied_change['loreId'] = lore.get('id')
            applied_change['loreTitle'] = lore.get('title')
        elif change_type == 'flag.set':
            flags = _ensure_dict(next_state, 'flags')
            flags[_world_id(change.get('flagKey'))] = change.get('flagValue')
            applied_change['flagKey'] = _world_id(change.get('flagKey'))
        elif change_type == 'flag.unset':
            flags = _ensure_dict(next_state, 'flags')
            flags.pop(_world_id(change.get('flagKey')), None)
            applied_change['flagKey'] = _world_id(change.get('flagKey'))
        else:
            skipped.append({'change': change, 'reason': 'Unsupported change or actor missing during application.'})
            continue

        applied.append(applied_change)
        if change_id:
            seen_ids.add(change_id)
            append_change_ledger(next_state, applied_change)
        pending_changes.extend(derive_quest_changes(next_state, applied_change))
        if change_type == 'scene.interactable.action' and isinstance(applied_change.get('event'), dict):
            pending_changes.extend(derive_quest_changes(next_state, applied_change['event']))
        if change_type == 'combat.end':
            reward_result = derive_combat_outcome_rewards(next_state, applied_change)
            if reward_result.get('valid') and not reward_result.get('alreadyApplied'):
                pending_changes.extend(
                    _combat_reward_transaction_changes(reward_result, applied_change)
                )

    next_state['lastUpdatedAt'] = utc_now().isoformat()
    return {'nextState': next_state, 'appliedChanges': applied, 'skippedChanges': skipped}


def persist_state_to_database(
    *,
    session_obj: Session,
    state: dict[str, Any],
    players_by_id: dict[int, Player],
) -> None:
    for actor in state.get('playerCharacters') or []:
        if not isinstance(actor, dict):
            continue
        player_id = parse_actor_player_id(actor.get('id')) or actor.get('playerId')
        player = players_by_id.get(int(player_id)) if player_id else None
        if not player:
            continue

        inventory = actor.get('inventory') if isinstance(actor.get('inventory'), dict) else {}
        items = inventory.get('items') if isinstance(inventory.get('items'), list) else []
        player.inventory = dump_inventory_items(items)

        stats = safe_json_loads(player.stats, {})
        stats = stats if isinstance(stats, dict) else {}
        currency = actor_currency(actor)
        stats = stats_with_currency(stats, currency)
        health = actor.get('health') if isinstance(actor.get('health'), dict) else {}
        if health:
            current_hp = max(0, int_or_default(health.get('currentHp'), default=0))
            max_hp = max(0, int_or_default(health.get('maxHp'), default=0))
            temp_hp = max(0, int_or_default(health.get('tempHp'), default=0))
            stats['current_hp'] = current_hp
            stats['hp_current'] = current_hp
            if max_hp:
                stats['max_hp'] = max_hp
                stats['hp_max'] = max_hp
            stats['temp_hp'] = temp_hp
        xp = actor.get('xp') if isinstance(actor.get('xp'), dict) else {}
        if xp:
            current_xp = max(0, int_or_default(xp.get('current'), default=0))
            stats['xp'] = current_xp
            stats['experience'] = current_xp
            sync_actor_level_for_xp(actor)
        if int_or_default(actor.get('level'), default=0) > int_or_default(player.level, default=1):
            player.level = int(actor['level'])
        sync_stats_for_level(stats, player.level or 1)
        if isinstance(actor.get('raceAbilityState'), dict):
            stats['race_ability_state'] = deepcopy(actor['raceAbilityState'])
        if isinstance(actor.get('classFeatureState'), dict):
            stats['class_feature_state'] = deepcopy(actor['classFeatureState'])
        health = actor.setdefault('health', {})
        current_hp = max(0, int_or_default(stats.get('current_hp', stats.get('hp_current')), default=0))
        max_hp = max(current_hp, int_or_default(stats.get('max_hp', stats.get('hp_max')), default=current_hp))
        if max_hp:
            health['currentHp'] = current_hp
            health['maxHp'] = max_hp
            health['tempHp'] = max(0, int_or_default(stats.get('temp_hp', stats.get('tempHp')), default=0))
            _sync_actor_health_to_combat_participant(state, actor)
        actor['level'] = int_or_default(player.level, default=1)
        actor_xp = actor.setdefault('xp', {})
        actor_xp['current'] = max(0, int_or_default(stats.get('xp', stats.get('experience')), default=0))
        sync_actor_level_for_xp(actor)
        _sync_actor_level_to_combat_participant(state, actor)
        player.stats = safe_json_dumps(stats, {})
        spellbook = actor.get('spellbook') if isinstance(actor.get('spellbook'), dict) else {}
        spell_resources = actor.get('spellResources') if isinstance(actor.get('spellResources'), dict) else None
        sheet = character_sheet_record(player.character_sheet)
        if spellbook.get('knownSpells'):
            sheet['spellbook'] = normalize_spellbook(spellbook, class_name=player.class_)
            sheet['spells'] = known_spell_names(sheet['spellbook'])
        if spell_resources is not None:
            sheet['spellResources'] = deepcopy(spell_resources)
        if isinstance(actor.get('classFeatureState'), dict):
            sheet['classFeatureState'] = deepcopy(actor['classFeatureState'])
        sheet, spellbook_changed = ensure_character_sheet_spellbook(
            sheet,
            class_name=player.class_,
            race_name=player.race,
            race_selection=player.race_selection,
            level=player.level or 1,
        )
        sheet, resources_changed = ensure_character_sheet_spell_resources(
            sheet,
            class_name=player.class_,
            level=player.level or 1,
        )
        if (
            spellbook_changed
            or resources_changed
            or spellbook.get('knownSpells')
            or spell_resources is not None
            or isinstance(actor.get('classFeatureState'), dict)
        ):
            player.character_sheet = safe_json_dumps(sheet, {})
            synced_spellbook = normalize_spellbook(sheet.get('spellbook'), class_name=player.class_)
            if synced_spellbook.get('knownSpells'):
                actor['spellbook'] = synced_spellbook
                actor['spells'] = known_spell_names(synced_spellbook)
            actor['spellResources'] = deepcopy(sheet['spellResources'])

    session_obj.state_snapshot = safe_json_dumps(state, {})


def legacy_immediate_summary_from_applied(applied_changes: list[dict[str, Any]], rejected: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    inventory_changes: list[dict[str, Any]] = []
    character_changes: list[dict[str, Any]] = []
    currency_names = {
        'pp': 'platinum',
        'gp': 'gold',
        'ep': 'electrum',
        'sp': 'silver',
        'cp': 'copper',
    }
    for change in applied_changes:
        change_type = str(change.get('type') or '')
        if change_type in {'inventory.add', 'inventory.remove'}:
            inventory_changes.append(
                {
                    'player_id': parse_actor_player_id(change.get('actorId') or change.get('actor_id')),
                    'action': 'acquire' if change_type == 'inventory.add' else 'lose',
                    'item_name': change.get('itemName') or change.get('item_name') or change.get('item', {}).get('name'),
                    'quantity': max(1, int_or_default(change.get('quantity'), default=1)),
                    'source': change.get('source') or 'state_pipeline',
                    'state_change_id': change.get('id'),
                }
            )
        elif change_type in {'inventory.equip', 'inventory.unequip'}:
            inventory_changes.append(
                {
                    'player_id': parse_actor_player_id(change.get('actorId') or change.get('actor_id')),
                    'action': 'equip' if change_type == 'inventory.equip' else 'unequip',
                    'item_name': change.get('itemName') or change.get('item_name'),
                    'quantity': 1,
                    'slot': change.get('slot'),
                    'conflict_item_names': change.get('conflictItemNames') or [],
                    'source': change.get('source') or 'state_pipeline',
                    'state_change_id': change.get('id'),
                }
            )
        elif change_type in {'health.heal', 'health.damage', 'health.max.set', 'currency.add', 'currency.remove', 'xp.add', 'xp.remove'}:
            amount = int_or_default(change.get('actualAmount', change.get('amount')), default=0)
            signed_amount = -amount if change_type in {'health.damage', 'currency.remove', 'xp.remove'} else amount
            character_change = {
                'player_id': parse_actor_player_id(change.get('actorId') or change.get('actor_id')),
                'change_type': change_type,
                'amount': amount,
                'currency': change.get('currency'),
                'state_change_id': change.get('id'),
                'already_applied': True,
            }
            if change_type in {'health.heal', 'health.damage'}:
                character_change['hp_delta'] = signed_amount
            if change_type == 'health.max.set':
                character_change['max_hp_delta'] = int_or_default(change.get('maxHpDelta'), default=0)
                character_change['hp_delta'] = int_or_default(change.get('currentHpDelta'), default=0)
            if change_type in {'xp.add', 'xp.remove'}:
                character_change['xp_delta'] = signed_amount
            if change_type in {'currency.add', 'currency.remove'}:
                currency_code = str(change.get('currency') or '').lower()
                if currency_code == 'gp':
                    character_change['gold_delta'] = signed_amount
                elif currency_code in currency_names:
                    character_change['gold_delta'] = 0
                    character_change['currency_delta'] = {currency_names[currency_code]: signed_amount}
            character_changes.append(character_change)
        elif change_type == 'spell.learn':
            character_changes.append(
                {
                    'player_id': parse_actor_player_id(change.get('actorId') or change.get('actor_id')),
                    'change_type': change_type,
                    'spell_name': change.get('spellName') or change.get('spell_name'),
                    'spell_level': change.get('spellLevel') if change.get('spellLevel') is not None else change.get('spell_level'),
                    'state_change_id': change.get('id'),
                    'already_applied': True,
                }
            )
    return {
        'inventory_changes_applied': inventory_changes,
        'character_state_changes_applied': character_changes,
        'rejections': rejected or [],
        'source': 'state_pipeline',
    }
