from __future__ import annotations

from copy import deepcopy
from datetime import timezone
import hashlib
import re
from typing import Any, Iterable

from aidm_server.armor_class import ARMOR_METADATA_KEYS, sync_actor_armor_class
from aidm_server.canon_inventory import clean_inventory_item_name, item_weight_for_name
from aidm_server.canon_text import int_or_default, positive_int
from aidm_server.character_state import character_state_for_player
from aidm_server.game_state.equipment import infer_equipment_slot
from aidm_server.models import Campaign, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.spellbook import known_spell_names
from aidm_server.time_utils import utc_now


CURRENCY_CODES = ('pp', 'gp', 'ep', 'sp', 'cp')
CURRENCY_STAT_KEYS = {
    'pp': 'platinum',
    'gp': 'gold',
    'ep': 'electrum',
    'sp': 'silver',
    'cp': 'copper',
}
STAT_CURRENCY_CODES = {value: key for key, value in CURRENCY_STAT_KEYS.items()}
WEAPON_WORDS = {
    'axe',
    'battleaxe',
    'blade',
    'bow',
    'club',
    'crossbow',
    'dagger',
    'greataxe',
    'greatsword',
    'hammer',
    'handaxe',
    'knife',
    'longbow',
    'longsword',
    'mace',
    'maul',
    'shortbow',
    'shortsword',
    'sling',
    'spear',
    'staff',
    'sword',
}
WEAPON_PHRASES = {'battle axe', 'great axe', 'hand axe', 'war pick'}
CONSUMABLE_WORDS = {'potion', 'ration', 'food', 'drink', 'elixir', 'vial', 'flask'}
ARMOR_WORDS = {'armor', 'armour', 'mail', 'shield', 'helm', 'helmet'}
CLOTHING_WORDS = {'hood', 'cowl', 'cloak', 'cape', 'clothing', 'clothes', 'shirt', 'tunic', 'robe', 'boots', 'gloves', 'underwear'}


def normalize_item_name(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip().lower().replace('_', ' ').replace('-', ' '))


def display_actor_id(player_id: int | str | None) -> str:
    return f'player_{player_id}' if player_id is not None else 'player_unknown'


def parse_actor_player_id(actor_id: Any) -> int | None:
    if isinstance(actor_id, int):
        return actor_id
    text = str(actor_id or '').strip()
    if not text:
        return None
    if text.startswith('player_'):
        text = text[len('player_') :]
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def stable_slug(value: Any) -> str:
    normalized = normalize_item_name(value)
    slug = re.sub(r'[^a-z0-9]+', '_', normalized).strip('_')
    return slug or 'item'


def stable_item_id(name: Any, *, prefix: str = 'itm') -> str:
    slug = stable_slug(name)
    digest = hashlib.sha1(slug.encode('utf-8')).hexdigest()[:8]
    return f'{prefix}_{slug}_{digest}'


def stable_change_id(*parts: Any) -> str:
    """Return the compatibility-stable ledger ID for a logical state change."""
    source = '|'.join(str(part or '') for part in parts)
    # This SHA-1 output is a persisted, non-secret identifier. Changing it in place
    # would break retry deduplication for state-change ledgers created before upgrade.
    # codeql[py/weak-sensitive-data-hashing]
    digest = hashlib.sha1(source.encode('utf-8')).hexdigest()[:16]
    return f'chg_{digest}'


def stable_item_instance_id(*parts: Any, prefix: str = 'itm_instance') -> str:
    """Return a deterministic identity for an item instance created by a state change."""

    return f"{prefix}_{stable_change_id(*parts).removeprefix('chg_')}"


def actor_name(actor: dict[str, Any] | None) -> str:
    if not actor:
        return 'Character'
    return str(actor.get('name') or actor.get('characterName') or actor.get('id') or 'Character')


def _as_record(value: Any) -> dict[str, Any]:
    loaded = safe_json_loads(value, {})
    return loaded if isinstance(loaded, dict) else {}


def _as_list(value: Any) -> list[Any]:
    loaded = safe_json_loads(value, [])
    return loaded if isinstance(loaded, list) else []


def _list_text(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or '').strip()]


def _infer_item_type(item: dict[str, Any], name: str) -> str:
    explicit = str(item.get('type') or '').strip().lower()
    name_key = normalize_item_name(name)
    tokens = set(name_key.split())
    inferred_from_name = None
    if tokens & CONSUMABLE_WORDS:
        inferred_from_name = 'consumable'
    elif tokens & WEAPON_WORDS or any(phrase in name_key for phrase in WEAPON_PHRASES):
        inferred_from_name = 'weapon'
    elif tokens & ARMOR_WORDS:
        inferred_from_name = 'armor'
    elif tokens & CLOTHING_WORDS:
        inferred_from_name = 'clothing'
    if explicit and (explicit != 'misc' or inferred_from_name is None):
        return explicit
    return inferred_from_name or 'misc'


def _infer_item_subtype(item: dict[str, Any], name: str, item_type: str) -> str | None:
    explicit = str(item.get('subtype') or '').strip().lower()
    if explicit:
        return explicit
    name_key = normalize_item_name(name)
    if item_type == 'weapon':
        if 'greataxe' in name_key or 'great axe' in name_key:
            return 'greataxe'
        if 'handaxe' in name_key or 'hand axe' in name_key:
            return 'handaxe'
        if 'axe' in name_key:
            return 'axe'
        if 'sword' in name_key or 'blade' in name_key:
            return 'sword'
        if 'bow' in name_key:
            return 'bow'
        if 'dagger' in name_key or 'knife' in name_key:
            return 'dagger'
        if 'staff' in name_key:
            return 'staff'
    if item_type == 'consumable' and any(word in name_key for word in CONSUMABLE_WORDS):
        return 'potion' if 'potion' in name_key else 'consumable'
    if item_type in {'armor', 'clothing'}:
        if 'shield' in name_key:
            return 'shield'
        if 'helm' in name_key or 'helmet' in name_key:
            return 'helmet'
        if 'hood' in name_key or 'cowl' in name_key:
            return 'hood'
        if 'cloak' in name_key or 'cape' in name_key:
            return 'cloak'
        if 'underwear' in name_key or 'underclothes' in name_key:
            return 'underwear'
    return None


def normalize_inventory_item(raw_item: Any) -> dict[str, Any] | None:
    if isinstance(raw_item, str):
        name = clean_inventory_item_name(raw_item)
        raw_item = {'name': name}
    if not isinstance(raw_item, dict):
        return None

    name = clean_inventory_item_name(raw_item.get('name'))
    if not name:
        return None
    quantity = positive_int(raw_item.get('quantity', 1))
    item_type = _infer_item_type(raw_item, name)
    subtype = _infer_item_subtype(raw_item, name, item_type)
    item_id = str(raw_item.get('id') or raw_item.get('itemId') or stable_item_id(name)).strip()
    aliases = _list_text(raw_item.get('aliases'))
    tags = _list_text(raw_item.get('tags'))
    if subtype and subtype not in tags:
        tags.append(subtype)
    if item_type and item_type not in tags:
        tags.append(item_type)
    if subtype and subtype not in aliases and subtype != normalize_item_name(name):
        aliases.append(subtype)
    metadata = dict(raw_item.get('metadata')) if isinstance(raw_item.get('metadata'), dict) else {}
    for key in ARMOR_METADATA_KEYS:
        if key in raw_item and raw_item.get(key) not in (None, ''):
            metadata.setdefault(key, raw_item.get(key))

    item: dict[str, Any] = {
        'id': item_id,
        'name': name,
        'quantity': quantity,
        'type': item_type,
        'subtype': subtype,
        'equipped': bool(raw_item.get('equipped')),
        'slot': raw_item.get('slot') or raw_item.get('equipmentSlot') or raw_item.get('equipment_slot') or None,
        'aliases': aliases,
        'tags': tags,
        'lastUsedAtTurn': raw_item.get('lastUsedAtTurn', raw_item.get('last_used_at_turn')),
        'lastEquippedAtTurn': raw_item.get('lastEquippedAtTurn', raw_item.get('last_equipped_at_turn')),
        'favorite': bool(raw_item.get('favorite')),
        'weight': raw_item.get('weight'),
        'metadata': metadata,
    }
    inferred_slot = infer_equipment_slot(item)
    if inferred_slot:
        item['slot'] = item.get('slot') or inferred_slot
    else:
        item['slot'] = item.get('slot') or 'none'
    if item['weight'] is None:
        item['weight'] = item_weight_for_name(name)
    return item


def load_inventory_items(raw_value: Any) -> list[dict[str, Any]]:
    payload = safe_json_loads(raw_value, raw_value)
    if isinstance(payload, dict):
        payload = payload.get('items', [])
    if isinstance(payload, list):
        items = [normalize_inventory_item(item) for item in payload]
        return [item for item in items if item is not None]
    if isinstance(raw_value, str):
        items = [normalize_inventory_item(part.strip()) for part in raw_value.split(',') if part.strip()]
        return [item for item in items if item is not None]
    return []


def dump_inventory_items(items: Iterable[dict[str, Any]]) -> str:
    compacted: list[dict[str, Any]] = []
    for item in items:
        normalized = normalize_inventory_item(item)
        if not normalized or normalized.get('quantity', 0) <= 0:
            continue
        name = str(normalized.get('name') or '')
        compact: dict[str, Any] = {
            'name': name,
            'quantity': positive_int(normalized.get('quantity', 1)),
        }
        if normalized.get('weight') is not None:
            compact['weight'] = normalized.get('weight')
        if normalized.get('id') and normalized.get('id') != stable_item_id(name):
            compact['id'] = normalized.get('id')
        if normalized.get('type') and normalized.get('type') != 'misc':
            compact['type'] = normalized.get('type')
        if normalized.get('subtype'):
            compact['subtype'] = normalized.get('subtype')
        if normalized.get('equipped'):
            compact['equipped'] = True
        if normalized.get('slot') and normalized.get('slot') != 'none':
            compact['slot'] = normalized.get('slot')
            compact['equipmentSlot'] = normalized.get('slot')
        aliases = normalized.get('aliases') if isinstance(normalized.get('aliases'), list) else []
        default_aliases = {normalize_item_name(normalized.get('subtype'))} if normalized.get('subtype') else set()
        meaningful_aliases = [alias for alias in aliases if normalize_item_name(alias) not in default_aliases]
        if meaningful_aliases:
            compact['aliases'] = meaningful_aliases
        tags = normalized.get('tags') if isinstance(normalized.get('tags'), list) else []
        default_tags = {normalize_item_name(normalized.get('type')), normalize_item_name(normalized.get('subtype'))}
        meaningful_tags = [tag for tag in tags if normalize_item_name(tag) not in default_tags]
        if meaningful_tags:
            compact['tags'] = meaningful_tags
        if normalized.get('lastUsedAtTurn') is not None:
            compact['lastUsedAtTurn'] = normalized.get('lastUsedAtTurn')
        if normalized.get('lastEquippedAtTurn') is not None:
            compact['lastEquippedAtTurn'] = normalized.get('lastEquippedAtTurn')
        if normalized.get('favorite'):
            compact['favorite'] = True
        if normalized.get('metadata'):
            compact['metadata'] = normalized.get('metadata')
        compacted.append(compact)
    return safe_json_dumps(compacted, [])


def currency_from_stats(stats: dict[str, Any]) -> dict[str, int]:
    return {
        code: max(0, int_or_default(stats.get(stat_key), default=0))
        for code, stat_key in CURRENCY_STAT_KEYS.items()
    }


def stats_with_currency(stats: dict[str, Any], currency: dict[str, Any]) -> dict[str, Any]:
    next_stats = dict(stats)
    for code, stat_key in CURRENCY_STAT_KEYS.items():
        next_stats[stat_key] = max(0, int_or_default(currency.get(code), default=0))
    return next_stats


def _health_from_player(player: Player, stats: dict[str, Any]) -> dict[str, Any]:
    state = character_state_for_player(player)
    hp = state.get('hp') if isinstance(state.get('hp'), dict) else {}
    max_hp = max(0, int_or_default(hp.get('max'), default=0))
    current_hp = max(0, int_or_default(hp.get('current'), default=max_hp))
    return {
        'currentHp': min(current_hp, max_hp) if max_hp else current_hp,
        'maxHp': max_hp,
        'tempHp': max(0, int_or_default(stats.get('temp_hp', stats.get('tempHp')), default=0)),
        'conditions': stats.get('conditions') if isinstance(stats.get('conditions'), list) else [],
    }


def player_character_from_model(player: Player) -> dict[str, Any]:
    stats = _as_record(player.stats)
    state = character_state_for_player(player)
    spellbook = state.get('spellbook') if isinstance(state.get('spellbook'), dict) else {}
    spell_resources = state.get('spell_resources') if isinstance(state.get('spell_resources'), dict) else {}
    ability_scores = dict(state.get('ability_scores') if isinstance(state.get('ability_scores'), dict) else {})
    inventory_items = load_inventory_items(player.inventory)
    actor = {
        'id': display_actor_id(player.player_id),
        'playerId': player.player_id,
        'name': player.character_name,
        'race': player.race,
        'class': player.class_,
        'level': int(player.level or 1),
        'health': _health_from_player(player, stats),
        'stats': ability_scores,
        'savingThrowProficiencies': list(state.get('saving_throw_proficiencies') or []),
        'inventory': {
            'items': inventory_items,
            'currency': currency_from_stats(stats),
            'carryingCapacity': int_or_default(stats.get('carrying_capacity'), default=0),
        },
        'xp': {
            'current': max(0, int_or_default(stats.get('xp', stats.get('experience')), default=0)),
            'nextLevelAt': stats.get('next_level_at') or stats.get('nextLevelAt'),
        },
        'metadata': {
            'defaultWeaponId': stats.get('default_weapon_id') or stats.get('defaultWeaponId'),
            'weaponProficiencies': list(state.get('weapon_proficiencies') or []),
        },
    }
    sync_actor_armor_class(actor)
    if spellbook.get('knownSpells'):
        actor['spellbook'] = spellbook
        actor['spells'] = known_spell_names(spellbook)
    if spell_resources:
        actor['spellResources'] = spell_resources
    class_capabilities = state.get('class_capabilities') if isinstance(state.get('class_capabilities'), list) else []
    class_feature_state = state.get('class_feature_state') if isinstance(state.get('class_feature_state'), dict) else {}
    if class_capabilities:
        actor['classFeatures'] = deepcopy(class_capabilities)
        actor['classFeatureState'] = deepcopy(class_feature_state)
    race_ability_state = stats.get('race_ability_state', stats.get('raceAbilityState'))
    if isinstance(race_ability_state, dict):
        actor['raceAbilityState'] = deepcopy(race_ability_state)
    return actor


def state_snapshot_for_session(
    *,
    session_obj: Session,
    campaign: Campaign,
    players: Iterable[Player],
    active_player_ids: Iterable[int] | None = None,
) -> dict[str, Any]:
    existing = safe_json_loads(session_obj.state_snapshot, {})
    snapshot = deepcopy(existing) if isinstance(existing, dict) else {}
    now = utc_now().replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')
    player_characters = [player_character_from_model(player) for player in players]
    active_ids: list[int] = []
    for raw_id in active_player_ids or []:
        parsed = int_or_default(raw_id, default=0)
        if parsed > 0 and parsed not in active_ids:
            active_ids.append(parsed)

    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    snapshot.update(
        {
            'schemaVersion': int_or_default(snapshot.get('schemaVersion'), default=1),
            'sessionId': session_obj.session_id,
            'campaignId': campaign.campaign_id,
            'currentScene': {
                'locationId': scene.get('locationId') or stable_slug(campaign.location or 'unknown_location'),
                'name': scene.get('name') or campaign.location,
                'sceneType': scene.get('sceneType') or 'exploration',
                'dangerLevel': int_or_default(scene.get('dangerLevel'), default=0),
                'mood': scene.get('mood') or None,
                'combatState': scene.get('combatState') or 'none',
                'description': scene.get('description') or '',
                'activeNpcIds': scene.get('activeNpcIds') if isinstance(scene.get('activeNpcIds'), list) else [],
                'activeQuestIds': scene.get('activeQuestIds') if isinstance(scene.get('activeQuestIds'), list) else [],
                'playerPositions': scene.get('playerPositions') if isinstance(scene.get('playerPositions'), dict) else {},
                'playerZones': scene.get('playerZones') if isinstance(scene.get('playerZones'), dict) else {},
                'characterPositions': scene.get('characterPositions') if isinstance(scene.get('characterPositions'), dict) else {},
                'characterZones': scene.get('characterZones') if isinstance(scene.get('characterZones'), dict) else {},
                'items': [dict(item) for item in (scene.get('items') or []) if isinstance(item, dict)],
                'interactables': [
                    deepcopy(item)
                    for item in (scene.get('interactables') or [])
                    if isinstance(item, dict)
                ],
                'hazards': [
                    deepcopy(item)
                    for item in (scene.get('hazards') or [])
                    if isinstance(item, dict)
                ],
                'musicTag': scene.get('musicTag') or None,
                'updatedAtTurn': scene.get('updatedAtTurn'),
            },
            'playerCharacters': player_characters,
            'activePlayerIds': active_ids,
            'partyNpcs': snapshot.get('partyNpcs') if isinstance(snapshot.get('partyNpcs'), list) else [],
            'knownNpcs': snapshot.get('knownNpcs') if isinstance(snapshot.get('knownNpcs'), list) else [],
            'quests': snapshot.get('quests') if isinstance(snapshot.get('quests'), list) else [],
            'locations': snapshot.get('locations') if isinstance(snapshot.get('locations'), list) else [],
            'combat': snapshot.get('combat') if isinstance(snapshot.get('combat'), dict) else {'status': 'none', 'round': 1, 'participants': [], 'battlefield': {}, 'flags': {}},
            'flags': snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {},
            'stateChangeLedger': snapshot.get('stateChangeLedger') if isinstance(snapshot.get('stateChangeLedger'), list) else [],
            'lastUpdatedAt': now,
        }
    )
    return snapshot


def compact_state_for_extraction(state: dict[str, Any]) -> dict[str, Any]:
    players = []
    for actor in state.get('playerCharacters') or []:
        if not isinstance(actor, dict):
            continue
        inventory = actor.get('inventory') if isinstance(actor.get('inventory'), dict) else {}
        health = actor.get('health') if isinstance(actor.get('health'), dict) else {}
        stats = actor.get('stats') if isinstance(actor.get('stats'), dict) else {}
        spellbook = actor.get('spellbook') if isinstance(actor.get('spellbook'), dict) else {}
        spell_resources = actor.get('spellResources') if isinstance(actor.get('spellResources'), dict) else {}
        players.append(
            {
                'id': actor.get('id'),
                'name': actor.get('name'),
                'playerId': actor.get('playerId'),
                'race': actor.get('race'),
                'class': actor.get('class'),
                'level': actor.get('level'),
                'health': {
                    'currentHp': health.get('currentHp'),
                    'maxHp': health.get('maxHp'),
                    'tempHp': health.get('tempHp'),
                },
                'armorClass': stats.get('armorClass'),
                'xp': actor.get('xp') if isinstance(actor.get('xp'), dict) else {},
                'spellbook': {
                    'knownSpells': [
                        {
                            'id': spell.get('id'),
                            'name': spell.get('name'),
                            'level': spell.get('level'),
                            'source': spell.get('source'),
                        }
                        for spell in (spellbook.get('knownSpells') or [])
                        if isinstance(spell, dict)
                    ][:30],
                    'preparedSpells': list(spellbook.get('preparedSpells') or [])[:30],
                    'preparationPolicy': spellbook.get('preparationPolicy')
                    if isinstance(spellbook.get('preparationPolicy'), dict)
                    else {},
                }
                if spellbook.get('knownSpells')
                else {},
                'spellResources': {
                    'slots': spell_resources.get('slots') if isinstance(spell_resources.get('slots'), dict) else {},
                    'pactSlots': spell_resources.get('pactSlots') if isinstance(spell_resources.get('pactSlots'), dict) else {},
                    'mysticArcanum': spell_resources.get('mysticArcanum')
                    if isinstance(spell_resources.get('mysticArcanum'), dict)
                    else {},
                    'concentration': spell_resources.get('concentration')
                    if isinstance(spell_resources.get('concentration'), dict)
                    else None,
                }
                if spell_resources
                else {},
                'classFeatures': [
                    {
                        'id': feature.get('id'),
                        'name': feature.get('name'),
                        'actionEconomy': feature.get('actionEconomy'),
                        'targetPolicy': feature.get('targetPolicy'),
                        'refreshesOn': feature.get('refreshesOn'),
                        'description': feature.get('description'),
                    }
                    for feature in (actor.get('classFeatures') or [])
                    if isinstance(feature, dict)
                ],
                'classFeatureState': deepcopy(actor.get('classFeatureState'))
                if isinstance(actor.get('classFeatureState'), dict)
                else {},
                'inventory': [
                    {
                        'id': item.get('id'),
                        'name': item.get('name'),
                        'quantity': item.get('quantity'),
                        'type': item.get('type'),
                        'subtype': item.get('subtype'),
                        'equipped': item.get('equipped'),
                        'slot': item.get('slot'),
                        'aliases': item.get('aliases') or [],
                        'tags': item.get('tags') or [],
                    }
                    for item in (inventory.get('items') or [])
                    if isinstance(item, dict)
                ],
                'currency': inventory.get('currency') if isinstance(inventory.get('currency'), dict) else {},
            }
        )
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    quests = [
        {
            'id': quest.get('id'),
            'title': quest.get('title'),
            'status': quest.get('status'),
            'stage': quest.get('stage'),
            'summary': quest.get('summary'),
            'xpReward': quest.get('xpReward'),
            'rewardXp': quest.get('rewardXp'),
            'experienceReward': quest.get('experienceReward'),
            'difficulty': quest.get('difficulty'),
            'priority': quest.get('priority'),
            'type': quest.get('type'),
            'completionPolicy': quest.get('completionPolicy') or 'all',
            'failOnObjectiveFailure': quest.get('failOnObjectiveFailure') is True,
            'reward': quest.get('reward') if isinstance(quest.get('reward'), dict) else {},
            'rewards': quest.get('rewards') if isinstance(quest.get('rewards'), (dict, list)) else {},
            'objectives': [
                {
                    'id': objective.get('id'),
                    'description': objective.get('description'),
                    'status': objective.get('status'),
                    'optional': objective.get('optional') is True,
                    'prerequisiteObjectiveIds': objective.get('prerequisiteObjectiveIds')
                    if isinstance(objective.get('prerequisiteObjectiveIds'), list)
                    else objective.get('prerequisites')
                    if isinstance(objective.get('prerequisites'), list)
                    else [],
                    'completeWhen': objective.get('completeWhen')
                    if isinstance(objective.get('completeWhen'), dict)
                    else {},
                    'failWhen': objective.get('failWhen')
                    if isinstance(objective.get('failWhen'), dict)
                    else {},
                }
                for objective in (quest.get('objectives') or [])
                if isinstance(objective, dict)
            ],
        }
        for quest in (state.get('quests') or [])
        if isinstance(quest, dict)
    ]
    locations = [
        {
            'id': location.get('id'),
            'name': location.get('name'),
            'type': location.get('type'),
            'status': location.get('status'),
            'description': location.get('description'),
            'connectedLocationIds': location.get('connectedLocationIds') if isinstance(location.get('connectedLocationIds'), list) else [],
        }
        for location in (state.get('locations') or [])
        if isinstance(location, dict)
    ]
    npcs = [
        {
            'id': npc.get('id'),
            'name': npc.get('name'),
            'role': npc.get('role'),
            'disposition': npc.get('disposition'),
            'status': npc.get('status'),
            'locationId': npc.get('locationId'),
            'questIds': npc.get('questIds') if isinstance(npc.get('questIds'), list) else [],
        }
        for npc in [*(state.get('knownNpcs') or []), *(state.get('partyNpcs') or [])]
        if isinstance(npc, dict)
    ]
    turn_control = state.get('turnControl') if isinstance(state.get('turnControl'), dict) else {}
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    combat_participants = []
    for participant in combat.get('participants') or []:
        if not isinstance(participant, dict):
            continue
        hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
        intent = participant.get('currentIntent') if isinstance(participant.get('currentIntent'), dict) else {}
        combat_participants.append(
            {
                'id': participant.get('id'),
                'name': participant.get('name'),
                'team': participant.get('team'),
                'kind': participant.get('kind'),
                'definitionId': participant.get('definitionId'),
                'level': participant.get('level'),
                'challengeTier': participant.get('challengeTier'),
                'xpReward': participant.get('xpReward'),
                'hp': {'current': hp.get('current'), 'max': hp.get('max'), 'temp': hp.get('temp')},
                'armorClass': participant.get('armorClass'),
                'conditions': participant.get('conditions') if isinstance(participant.get('conditions'), list) else [],
                'position': participant.get('position') if isinstance(participant.get('position'), dict) else {},
                'morale': participant.get('morale'),
                'isAlive': participant.get('isAlive'),
                'currentIntent': {
                    'intentType': intent.get('intentType'),
                    'targetId': intent.get('targetId'),
                    'abilityId': intent.get('abilityId'),
                    'reason': intent.get('reason'),
                    'visibleTelegraph': intent.get('visibleTelegraph'),
                    'suggestedSpeech': intent.get('suggestedSpeech'),
                }
                if intent
                else None,
            }
        )
    return {
        'sessionId': state.get('sessionId'),
        'campaignId': state.get('campaignId'),
        'activePlayerIds': state.get('activePlayerIds') if isinstance(state.get('activePlayerIds'), list) else [],
        'currentScene': {
            'locationId': scene.get('locationId'),
            'name': scene.get('name'),
            'sceneType': scene.get('sceneType'),
            'dangerLevel': scene.get('dangerLevel'),
            'mood': scene.get('mood'),
            'combatState': scene.get('combatState'),
            'description': scene.get('description'),
            'activeNpcIds': scene.get('activeNpcIds') if isinstance(scene.get('activeNpcIds'), list) else [],
            'activeQuestIds': scene.get('activeQuestIds') if isinstance(scene.get('activeQuestIds'), list) else [],
            'playerPositions': scene.get('playerPositions') if isinstance(scene.get('playerPositions'), dict) else {},
            'playerZones': scene.get('playerZones') if isinstance(scene.get('playerZones'), dict) else {},
            'characterPositions': scene.get('characterPositions') if isinstance(scene.get('characterPositions'), dict) else {},
            'characterZones': scene.get('characterZones') if isinstance(scene.get('characterZones'), dict) else {},
            'items': [
                {
                    'id': item.get('id'),
                    'name': item.get('name'),
                    'quantity': item.get('quantity'),
                    'type': item.get('type'),
                    'subtype': item.get('subtype'),
                    'sourceActorId': item.get('sourceActorId'),
                }
                for item in (scene.get('items') or [])
                if isinstance(item, dict)
            ][:20],
            'interactables': [
                {
                    key: deepcopy(item.get(key))
                    for key in (
                        'id',
                        'name',
                        'kind',
                        'description',
                        'open',
                        'locked',
                        'broken',
                        'searched',
                        'used',
                        'depleted',
                        'usesRemaining',
                        'playerKnown',
                        'hidden',
                        'revision',
                    )
                    if key in item
                }
                for item in (scene.get('interactables') or [])
                if isinstance(item, dict)
            ][:20],
            'hazards': [
                {
                    key: deepcopy(item.get(key))
                    for key in (
                        'id',
                        'name',
                        'kind',
                        'description',
                        'active',
                        'triggered',
                        'disarmed',
                        'playerKnown',
                        'hidden',
                        'revision',
                    )
                    if key in item
                }
                for item in (scene.get('hazards') or [])
                if isinstance(item, dict)
            ][:20],
        },
        'playerCharacters': players,
        'quests': quests,
        'locations': locations,
        'npcs': npcs,
        'combat': {
            'status': combat.get('status') or 'none',
            'round': combat.get('round') or 1,
            'turnIndex': combat.get('turnIndex'),
            'participants': combat_participants[:12],
            'battlefield': combat.get('battlefield') if isinstance(combat.get('battlefield'), dict) else {},
            'encounterGoal': combat.get('encounterGoal') if isinstance(combat.get('encounterGoal'), dict) else None,
            'lastRoundSummary': combat.get('lastRoundSummary'),
            'flags': combat.get('flags') if isinstance(combat.get('flags'), dict) else {},
        },
        'turnControl': {
            'activePlayerId': turn_control.get('activePlayerId'),
            'activePlayerName': turn_control.get('activePlayerName'),
            'participantPlayerIds': turn_control.get('participantPlayerIds') if isinstance(turn_control.get('participantPlayerIds'), list) else [],
            'participantPlayerNames': turn_control.get('participantPlayerNames') if isinstance(turn_control.get('participantPlayerNames'), list) else [],
        }
        if turn_control
        else {},
        'flags': state.get('flags') if isinstance(state.get('flags'), dict) else {},
    }


ACTOR_COLLECTION_KEYS = ('playerCharacters', 'partyNpcs', 'knownNpcs')


def iter_state_actors(state: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for collection_key in ACTOR_COLLECTION_KEYS:
        for actor in state.get(collection_key) or []:
            if isinstance(actor, dict):
                yield actor


def find_actor(state: dict[str, Any], actor_id: Any) -> dict[str, Any] | None:
    requested_id = str(actor_id or '').strip()
    if not requested_id:
        return None
    for actor in iter_state_actors(state):
        if str(actor.get('id')) == requested_id:
            return actor
        if actor.get('playerId') is not None and parse_actor_player_id(requested_id) == actor.get('playerId'):
            return actor
    return None


def find_actor_by_name(state: dict[str, Any], actor_name_value: Any) -> dict[str, Any] | None:
    requested_name = normalize_item_name(actor_name_value)
    if not requested_name:
        return None
    exact_matches = [
        actor
        for actor in iter_state_actors(state)
        if normalize_item_name(actor.get('name') or actor.get('characterName') or actor.get('displayName')) == requested_name
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    partial_matches = [
        actor
        for actor in iter_state_actors(state)
        if requested_name
        in normalize_item_name(actor.get('name') or actor.get('characterName') or actor.get('displayName'))
    ]
    return partial_matches[0] if len(partial_matches) == 1 else None


def actor_inventory(actor: dict[str, Any] | None) -> dict[str, Any]:
    if not actor:
        return {'items': [], 'currency': {}}
    inventory = actor.get('inventory')
    return inventory if isinstance(inventory, dict) else {'items': [], 'currency': {}}


def actor_items(actor: dict[str, Any] | None) -> list[dict[str, Any]]:
    inventory = actor_inventory(actor)
    items = inventory.get('items')
    return items if isinstance(items, list) else []


def actor_currency(actor: dict[str, Any] | None) -> dict[str, int]:
    inventory = actor_inventory(actor)
    currency = inventory.get('currency') if isinstance(inventory.get('currency'), dict) else {}
    return {code: max(0, int_or_default(currency.get(code), default=0)) for code in CURRENCY_CODES}


def state_applied_change_ids(state: dict[str, Any]) -> set[str]:
    ledger = state.get('stateChangeLedger') if isinstance(state.get('stateChangeLedger'), list) else []
    return {
        str(entry.get('id') or entry.get('changeId'))
        for entry in ledger
        if isinstance(entry, dict) and (entry.get('id') or entry.get('changeId'))
    }


def append_change_ledger(state: dict[str, Any], applied_change: dict[str, Any]) -> None:
    ledger = state.setdefault('stateChangeLedger', [])
    if not isinstance(ledger, list):
        ledger = []
        state['stateChangeLedger'] = ledger
    ledger.append(
        {
            'id': applied_change.get('id'),
            'type': applied_change.get('type'),
            'source': applied_change.get('source'),
            'appliedAt': utc_now().replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z'),
        }
    )


def recent_timeline_for_session(session_id: int, *, limit: int = 5) -> list[dict[str, Any]]:
    from aidm_server.models import DmTurn

    turns = (
        DmTurn.query.filter_by(session_id=session_id)
        .order_by(DmTurn.turn_id.desc())
        .limit(max(1, int(limit)))
        .all()
    )
    recent = []
    for turn in reversed(turns):
        recent.append(
            {
                'turnId': turn.turn_id,
                'playerMessage': turn.player_input,
                'dmResponse': turn.dm_output,
            }
        )
    return recent
