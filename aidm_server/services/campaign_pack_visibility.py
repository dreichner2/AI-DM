from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.combat.state import normalize_battlefield, normalize_position
from aidm_server.interactables import project_scene_interactables
from aidm_server.services.campaign_pack_graph import hidden_to_players
from aidm_server.services.campaign_pack_snapshot import migrate_campaign_pack_snapshot


PLAYER_REVEALED_CHECKPOINT_STATUSES = frozenset({'active', 'completed', 'skipped', 'failed'})

PUBLIC_PLAYER_CHARACTER_KEYS = (
    'id',
    'playerId',
    'player_id',
    'name',
    'characterName',
    'character_name',
    'race',
    'class',
    'class_',
    'level',
    'sex',
    'profileImage',
    'profile_image',
)

PUBLIC_PLAYER_COMBAT_KEYS = (
    'id',
    'playerId',
    'player_id',
    'name',
    'team',
    'kind',
    'class',
    'class_',
    'level',
    'conditions',
    'isAlive',
    'isConscious',
)

PUBLIC_NON_PLAYER_COMBAT_KEYS = (
    'id',
    'name',
    'team',
    'kind',
    'creatureType',
    'creatureTypeName',
    'level',
    'challengeTier',
    'hp',
    'armorClass',
    'conditions',
    'position',
    'isAlive',
    'isConscious',
)

PUBLIC_ENEMY_INTENT_KEYS = (
    'visibleTelegraph',
)

PUBLIC_SNAPSHOT_SCALAR_KEYS = (
    'schemaVersion',
    'sessionId',
    'campaignId',
    'lastUpdatedAt',
)

PUBLIC_LOCATION_KEYS = (
    'id',
    'name',
    'type',
    'status',
    'region',
    'parentLocationId',
    'firstDiscoveredTurn',
    'lastVisitedTurn',
    'updatedAtTurn',
    'createdAtTurn',
    'sceneType',
    'dangerLevel',
    'mood',
    'musicTag',
)

PUBLIC_NPC_KEYS = (
    'id',
    'name',
    'race',
    'species',
    'ancestry',
    'role',
    'disposition',
    'locationId',
    'status',
    'faction',
    'firstMetTurn',
    'lastSeenTurn',
    'updatedAtTurn',
)

PUBLIC_QUEST_KEYS = (
    'id',
    'title',
    'name',
    'status',
    'stage',
    'createdAtTurn',
    'updatedAtTurn',
    'completedAtTurn',
)

PUBLIC_SCENE_KEYS = (
    'locationId',
    'name',
    'sceneType',
    'dangerLevel',
    'mood',
    'combatState',
    'musicTag',
    'updatedAtTurn',
)

PUBLIC_SCENE_ITEM_KEYS = (
    'id',
    'name',
    'quantity',
    'type',
    'subtype',
    'rarity',
    'equipped',
    'slot',
    'weight',
    'value',
)

PUBLIC_COMBAT_FLAG_SCALAR_KEYS = (
    'combatStartedBy',
    'creatureSource',
    'endReason',
    'initiativeRequired',
    'resolverMethod',
)

PUBLIC_INITIATIVE_KEYS = (
    'participantId',
    'participant_id',
    'id',
    'name',
    'total',
    'roll',
    'modifier',
    'order',
    'initiative',
)

PUBLIC_DOMAIN_COLLECTIONS = ('clues', 'factions', 'maps', 'handouts', 'lore')

COMBAT_SINGLE_PARTICIPANT_REFERENCE_KEYS = frozenset(
    {
        'activeActorId',
        'handoffActorId',
        'lastResolvedActorId',
        'nextActorId',
        'participantId',
        'pendingActorId',
        'sourceActorId',
        'submittedActorId',
        'targetActorId',
        'targetId',
    }
)

COMBAT_PARTICIPANT_REFERENCE_LIST_KEYS = frozenset(
    {
        'enemyIds',
        'enemyTurnBlock',
        'participantIds',
        'targetIds',
        'turnOrder',
    }
)

COMBAT_REFERENCE_COMPANION_KEYS = {
    'activeActorId': ('activeActorName', 'activeActorTeam'),
    'handoffActorId': ('handoffActorName', 'handoffActorTeam'),
    'nextActorId': ('nextActorName', 'nextActorTeam'),
    'targetActorId': ('targetActorName', 'targetActorTeam'),
    'targetId': ('targetName',),
}


def filter_session_snapshot_for_player(
    snapshot: Any,
    *,
    private_player_ids: set[int] | frozenset[int] | None = None,
) -> Any:
    if not isinstance(snapshot, dict):
        return snapshot

    migrated, _migrations_applied = migrate_campaign_pack_snapshot(snapshot)
    owned_player_ids = _normalized_player_ids(private_player_ids)
    # Build the player response from a top-level allowlist. Persisted snapshots
    # are extensible and may contain operator-only recovery/debug fields; a
    # deepcopy followed by a few pops makes every new field public by default.
    filtered = _public_scalar_fields(migrated, PUBLIC_SNAPSHOT_SCALAR_KEYS)

    flags = migrated.get('flags') if isinstance(migrated.get('flags'), dict) else {}
    pack = migrated.get('campaignPack') if isinstance(migrated.get('campaignPack'), dict) else {}
    player_pack: dict = {}
    if pack:
        player_pack = _player_pack_snapshot(pack, flags)
        filtered['campaignPack'] = player_pack
    if flags:
        filtered['flags'] = _player_flags(flags, player_pack)

    raw_player_characters = [
        record
        for record in (migrated.get('playerCharacters') or [])
        if isinstance(record, dict)
    ]
    if isinstance(migrated.get('playerCharacters'), list):
        filtered['playerCharacters'] = [
            _player_character_for_viewer(record, owned_player_ids)
            for record in raw_player_characters
        ]
    if isinstance(migrated.get('activePlayerIds'), list):
        filtered['activePlayerIds'] = [
            player_id
            for value in migrated['activePlayerIds']
            if (player_id := _positive_int(value)) is not None and player_id > 0
        ]

    raw_locations = _visible_records(migrated.get('locations'))
    raw_known_npcs = _visible_records(migrated.get('knownNpcs'))
    raw_party_npcs = _visible_records(migrated.get('partyNpcs'))
    raw_quests = _visible_records(migrated.get('quests'))
    raw_scene = migrated.get('currentScene') if isinstance(migrated.get('currentScene'), dict) else {}

    visible_location_ids = {_record_id(record) for record in raw_locations if _record_id(record)}
    current_location_id = _id_key(_first(raw_scene, 'locationId', 'location_id'))
    if current_location_id:
        visible_location_ids.add(current_location_id)
    visible_npc_ids = {
        _record_id(record)
        for record in [*raw_known_npcs, *raw_party_npcs]
        if _record_id(record)
    }
    visible_quest_ids = {_record_id(record) for record in raw_quests if _record_id(record)}

    if isinstance(migrated.get('locations'), list):
        filtered['locations'] = [
            _location_for_viewer(
                record,
                visible_location_ids=visible_location_ids,
                visible_npc_ids=visible_npc_ids,
                visible_quest_ids=visible_quest_ids,
            )
            for record in raw_locations
        ]
    if isinstance(migrated.get('knownNpcs'), list):
        filtered['knownNpcs'] = [
            _npc_for_viewer(
                record,
                visible_location_ids=visible_location_ids,
                visible_quest_ids=visible_quest_ids,
            )
            for record in raw_known_npcs
        ]
    if isinstance(migrated.get('partyNpcs'), list):
        filtered['partyNpcs'] = [
            _npc_for_viewer(
                record,
                visible_location_ids=visible_location_ids,
                visible_quest_ids=visible_quest_ids,
            )
            for record in raw_party_npcs
        ]
    if isinstance(migrated.get('quests'), list):
        filtered['quests'] = [
            _quest_for_viewer(
                record,
                visible_location_ids=visible_location_ids,
                visible_npc_ids=visible_npc_ids,
            )
            for record in raw_quests
        ]

    visible_checkpoint_ids = {
        _id_key(checkpoint.get('id'))
        for checkpoint in (player_pack.get('checkpoints') or [])
        if isinstance(checkpoint, dict) and checkpoint.get('id')
    }
    for key in PUBLIC_DOMAIN_COLLECTIONS:
        if isinstance(migrated.get(key), list):
            filtered[key] = [
                _domain_record_for_viewer(
                    record,
                    visible_location_ids=visible_location_ids,
                    visible_npc_ids=visible_npc_ids,
                    visible_quest_ids=visible_quest_ids,
                    visible_checkpoint_ids=visible_checkpoint_ids,
                )
                for record in _visible_records(migrated.get(key))
            ]

    if isinstance(migrated.get('turnControl'), dict) or isinstance(migrated.get('turn_control'), dict):
        filtered['turnControl'] = _turn_control_for_viewer(
            migrated.get('turnControl') if isinstance(migrated.get('turnControl'), dict) else migrated.get('turn_control')
        )
    if isinstance(migrated.get('contentSettings'), dict) or isinstance(migrated.get('content_settings'), dict):
        filtered['contentSettings'] = _content_settings_for_viewer(
            migrated.get('contentSettings') if isinstance(migrated.get('contentSettings'), dict) else migrated.get('content_settings')
        )

    combat = deepcopy(migrated.get('combat')) if isinstance(migrated.get('combat'), dict) else None
    visible_combat_npc_ids: set[str] = set()
    if combat is not None:
        _filter_combat_for_player(combat, owned_player_ids, scene=raw_scene)
        filtered['combat'] = combat
        visible_combat_npc_ids = {
            _record_id(record)
            for record in combat.get('participants') or []
            if isinstance(record, dict) and not _is_player_combat_participant(record) and _record_id(record)
        }

    if isinstance(migrated.get('currentScene'), dict):
        visible_npc_ids.update(visible_combat_npc_ids)
        visible_actor_refs = _visible_actor_reference_keys(
            raw_player_characters,
            [*raw_known_npcs, *raw_party_npcs],
        )
        filtered['currentScene'] = _scene_for_viewer(
            raw_scene,
            visible_actor_refs=visible_actor_refs,
            visible_npc_ids=visible_npc_ids,
            visible_quest_ids=visible_quest_ids,
        )
        filtered['currentScene'].update(
            project_scene_interactables(migrated, {'isGm': False})
        )

    return filtered


def _normalized_player_ids(values: set[int] | frozenset[int] | None) -> frozenset[int]:
    normalized = {
        parsed
        for value in values or ()
        if (parsed := _positive_int(value)) is not None and parsed > 0
    }
    return frozenset(normalized)


def _record_player_id(record: dict) -> int | None:
    direct = _positive_int(_first(record, 'playerId', 'player_id'))
    if direct is not None and direct > 0:
        return direct

    actor_id = _text(record.get('id')).lower()
    for prefix in ('player_', 'player-'):
        if actor_id.startswith(prefix):
            parsed = _positive_int(actor_id[len(prefix):])
            return parsed if parsed is not None and parsed > 0 else None
    return None


def _public_fields(record: dict, keys: tuple[str, ...]) -> dict:
    return {key: deepcopy(record[key]) for key in keys if key in record}


def _public_scalar_fields(record: dict, keys: tuple[str, ...]) -> dict:
    return {
        key: deepcopy(record[key])
        for key in keys
        if key in record and isinstance(record[key], (str, int, float, bool))
    }


def _visible_records(value: Any) -> list[dict]:
    return [
        record
        for record in (value or [])
        if isinstance(record, dict) and _record_player_visible(record)
    ]


def _public_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _public_alias_text(record: dict, *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _set_public_text(payload: dict, key: str, record: dict, *source_keys: str) -> None:
    value = _public_alias_text(record, *source_keys)
    if value:
        payload[key] = value


def _location_for_viewer(
    record: dict,
    *,
    visible_location_ids: set[str],
    visible_npc_ids: set[str],
    visible_quest_ids: set[str],
) -> dict:
    payload = _public_scalar_fields(record, PUBLIC_LOCATION_KEYS)
    _set_public_text(
        payload,
        'summary',
        record,
        'playerSummary',
        'player_summary',
        'publicSummary',
        'public_summary',
        'summary',
    )
    _set_public_text(
        payload,
        'description',
        record,
        'playerDescription',
        'player_description',
        'publicDescription',
        'public_description',
        'description',
    )
    payload['connectedLocationIds'] = _visible_reference_ids(
        record.get('connectedLocationIds'),
        visible_location_ids,
    )
    payload['npcIds'] = _visible_reference_ids(record.get('npcIds'), visible_npc_ids)
    payload['questIds'] = _visible_reference_ids(record.get('questIds'), visible_quest_ids)
    if isinstance(record.get('tags'), list):
        payload['tags'] = _public_string_list(record.get('tags'))
    return payload


def _public_relationship(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    return _public_scalar_fields(value, ('score', 'label'))


def _npc_for_viewer(
    record: dict,
    *,
    visible_location_ids: set[str],
    visible_quest_ids: set[str],
) -> dict:
    payload = _public_scalar_fields(record, PUBLIC_NPC_KEYS)
    location_id = _id_key(payload.get('locationId'))
    if location_id and location_id not in visible_location_ids:
        payload.pop('locationId', None)
    payload['questIds'] = _visible_reference_ids(record.get('questIds'), visible_quest_ids)
    for key in ('aliases', 'tags'):
        if isinstance(record.get(key), list):
            payload[key] = _public_string_list(record.get(key))
    # Exact relationship scores and internal labels are DM-facing mechanics.
    # Player-visible disposition/status fields above carry only observable state.
    _set_public_text(
        payload,
        'summary',
        record,
        'playerSummary',
        'player_summary',
        'publicSummary',
        'public_summary',
        'summary',
    )
    _set_public_text(
        payload,
        'description',
        record,
        'playerDescription',
        'player_description',
        'publicDescription',
        'public_description',
        'description',
    )
    return payload


def _public_objectives(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    objectives = []
    for record in value:
        if not isinstance(record, dict) or not _record_player_visible(record):
            continue
        payload = _public_scalar_fields(record, ('id', 'status', 'title', 'name'))
        _set_public_text(
            payload,
            'description',
            record,
            'playerDescription',
            'player_description',
            'publicDescription',
            'public_description',
            'description',
        )
        objectives.append(payload)
    return objectives


def _quest_for_viewer(
    record: dict,
    *,
    visible_location_ids: set[str],
    visible_npc_ids: set[str],
) -> dict:
    payload = _public_scalar_fields(record, PUBLIC_QUEST_KEYS)
    _set_public_text(
        payload,
        'summary',
        record,
        'playerSummary',
        'player_summary',
        'publicSummary',
        'public_summary',
        'summary',
    )
    _set_public_text(
        payload,
        'description',
        record,
        'playerDescription',
        'player_description',
        'publicDescription',
        'public_description',
        'description',
    )
    payload['relatedNpcIds'] = _visible_reference_ids(record.get('relatedNpcIds'), visible_npc_ids)
    payload['relatedLocationIds'] = _visible_reference_ids(record.get('relatedLocationIds'), visible_location_ids)
    if isinstance(record.get('objectives'), list):
        payload['objectives'] = _public_objectives(record.get('objectives'))
    if isinstance(record.get('tags'), list):
        payload['tags'] = _public_string_list(record.get('tags'))
    return payload


def _domain_record_for_viewer(
    record: dict,
    *,
    visible_location_ids: set[str],
    visible_npc_ids: set[str],
    visible_quest_ids: set[str],
    visible_checkpoint_ids: set[str],
) -> dict:
    payload = _public_scalar_fields(
        record,
        (
            'id',
            'title',
            'name',
            'status',
            'revealed',
            'firstRevealedTurn',
            'updatedAtTurn',
        ),
    )
    _set_public_text(
        payload,
        'summary',
        record,
        'playerSummary',
        'player_summary',
        'publicSummary',
        'public_summary',
        'summary',
    )
    _set_public_text(
        payload,
        'description',
        record,
        'playerDescription',
        'player_description',
        'publicDescription',
        'public_description',
        'description',
    )
    payload['locationIds'] = _visible_reference_ids(record.get('locationIds'), visible_location_ids)
    payload['npcIds'] = _visible_reference_ids(record.get('npcIds'), visible_npc_ids)
    payload['questIds'] = _visible_reference_ids(record.get('questIds'), visible_quest_ids)
    payload['checkpointIds'] = _visible_reference_ids(record.get('checkpointIds'), visible_checkpoint_ids)
    if isinstance(record.get('tags'), list):
        payload['tags'] = _public_string_list(record.get('tags'))
    relationship = _public_relationship(record.get('relationship'))
    if relationship:
        payload['relationship'] = relationship
    if isinstance(record.get('regions'), list):
        payload['regions'] = _public_objectives(record.get('regions'))
    return payload


def _scene_item_for_viewer(record: dict) -> dict:
    payload = _public_scalar_fields(record, PUBLIC_SCENE_ITEM_KEYS)
    _set_public_text(
        payload,
        'description',
        record,
        'playerDescription',
        'player_description',
        'publicDescription',
        'public_description',
        'description',
    )
    for key in ('aliases', 'tags'):
        if isinstance(record.get(key), list):
            payload[key] = _public_string_list(record.get(key))
    return payload


def _visible_actor_reference_keys(players: list[dict], npcs: list[dict]) -> set[str]:
    keys: set[str] = set()
    for record in [*players, *npcs]:
        for value in (
            record.get('id'),
            record.get('name'),
            record.get('characterName'),
            record.get('playerId'),
            record.get('player_id'),
        ):
            if _text(value):
                keys.add(_id_key(value))
    return keys


def _position_map_for_viewer(value: Any, visible_actor_refs: set[str]) -> dict:
    if not isinstance(value, dict):
        return {}
    payload: dict = {}
    for key, position in value.items():
        if _id_key(key) not in visible_actor_refs:
            continue
        if isinstance(position, dict):
            payload[str(key)] = normalize_position(position)
        elif isinstance(position, (str, int, float, bool)):
            payload[str(key)] = position
    return payload


def _scene_for_viewer(
    scene: dict,
    *,
    visible_actor_refs: set[str],
    visible_npc_ids: set[str],
    visible_quest_ids: set[str],
) -> dict:
    payload = _public_scalar_fields(scene, PUBLIC_SCENE_KEYS)
    _set_public_text(
        payload,
        'description',
        scene,
        'playerDescription',
        'player_description',
        'publicDescription',
        'public_description',
        'description',
    )
    payload['activeNpcIds'] = _visible_reference_ids(scene.get('activeNpcIds'), visible_npc_ids)
    payload['activeQuestIds'] = _visible_reference_ids(scene.get('activeQuestIds'), visible_quest_ids)
    payload['items'] = [
        _scene_item_for_viewer(record)
        for record in (scene.get('items') or [])
        if isinstance(record, dict) and _record_player_visible(record)
    ]
    for key in ('playerPositions', 'playerZones', 'characterPositions', 'characterZones'):
        payload[key] = _position_map_for_viewer(scene.get(key), visible_actor_refs)
    return payload


def _turn_control_for_viewer(value: Any) -> dict:
    raw = value if isinstance(value, dict) else {}
    payload = _public_scalar_fields(
        raw,
        (
            'mode',
            'source',
            'focusType',
            'activePlayerId',
            'activePlayerName',
            'participantPlayerIds',
            'participantPlayerNames',
            'reason',
            'confidence',
            'updatedAt',
        ),
    )
    requests = []
    for record in raw.get('pendingJoinRequests') or []:
        if isinstance(record, dict):
            requests.append(_public_scalar_fields(record, ('playerId', 'playerName', 'reason', 'requestedAt')))
    payload['pendingJoinRequests'] = requests
    return payload


def _content_settings_for_viewer(value: Any) -> dict:
    raw = value if isinstance(value, dict) else {}
    payload = _public_scalar_fields(raw, ('contentRating', 'updatedAt'))
    if isinstance(raw.get('toneTags'), list):
        payload['toneTags'] = _public_string_list(raw.get('toneTags'))[:4]
    return payload


def _player_character_for_viewer(record: dict, private_player_ids: frozenset[int]) -> dict:
    player_id = _record_player_id(record)
    if player_id is not None and player_id in private_player_ids:
        return deepcopy(record)
    return _public_scalar_fields(record, PUBLIC_PLAYER_CHARACTER_KEYS)


def _public_hp(value: Any) -> dict:
    hp = value if isinstance(value, dict) else {}
    return _public_scalar_fields(hp, ('current', 'max', 'temp', 'currentHp', 'maxHp', 'tempHp'))


def _combat_participant_for_viewer(record: dict, private_player_ids: frozenset[int]) -> dict:
    player_id = _record_player_id(record)
    is_player = _is_player_combat_participant(record)
    if not is_player:
        payload = _public_scalar_fields(record, PUBLIC_NON_PLAYER_COMBAT_KEYS)
        public_hp = _public_hp(record.get('hp'))
        if public_hp:
            payload['hp'] = public_hp
        if isinstance(record.get('conditions'), list):
            payload['conditions'] = _public_string_list(record.get('conditions'))
        if isinstance(record.get('position'), dict):
            payload['position'] = normalize_position(record.get('position'))
        intent = record.get('currentIntent') if isinstance(record.get('currentIntent'), dict) else {}
        public_intent = _public_scalar_fields(intent, PUBLIC_ENEMY_INTENT_KEYS)
        if public_intent:
            payload['currentIntent'] = public_intent
        return payload
    if player_id is not None and player_id in private_player_ids:
        return deepcopy(record)
    payload = _public_scalar_fields(record, PUBLIC_PLAYER_COMBAT_KEYS)
    public_hp = _public_hp(record.get('hp'))
    public_hp.pop('temp', None)
    public_hp.pop('tempHp', None)
    if public_hp:
        payload['hp'] = public_hp
    if isinstance(record.get('conditions'), list):
        payload['conditions'] = _public_string_list(record.get('conditions'))
    return payload


def _is_player_combat_participant(record: dict) -> bool:
    player_id = _record_player_id(record)
    team = _text(record.get('team')).lower()
    kind = _text(record.get('kind')).lower()
    return player_id is not None or team == 'player' or kind in {'player', 'player_character'}


def _record_id(record: dict) -> str:
    return _id_key(_first(record, 'id', 'npcId', 'npc_id', 'questId', 'quest_id', 'participantId', 'participant_id'))


def _visible_reference_ids(values: Any, visible_ids: set[str]) -> list[Any]:
    if not isinstance(values, list):
        return []
    return [
        deepcopy(value)
        for value in values
        if isinstance(value, (str, int)) and not isinstance(value, bool) and _id_key(value) in visible_ids
    ]


def _combat_participant_can_take_turn(record: dict) -> bool:
    hp = record.get('hp') if isinstance(record.get('hp'), dict) else {}
    current_hp = _positive_int(_first(hp, 'current', 'currentHp', 'current_hp'))
    return (
        record.get('isAlive') is not False
        and record.get('isConscious') is not False
        and (current_hp is None or current_hp > 0)
        and _text(record.get('team')).lower() in {'player', 'ally', 'enemy'}
    )


def _combat_turn_order_ids(participants: list[dict]) -> list[str]:
    eligible = [record for record in participants if _record_id(record) and _combat_participant_can_take_turn(record)]
    ordered = [
        *[record for record in eligible if _text(record.get('team')).lower() in {'player', 'ally'}],
        *[record for record in eligible if _text(record.get('team')).lower() == 'enemy'],
    ]
    return [_record_id(record) for record in ordered]


def _current_combat_actor_id(combat: dict, turn_order_ids: list[str]) -> str:
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    active_actor_id = _id_key(_first(flags, 'activeActorId', 'active_actor_id'))
    if active_actor_id in turn_order_ids:
        return active_actor_id
    if not turn_order_ids or combat.get('turnIndex') is None:
        return ''
    try:
        turn_index = int(combat.get('turnIndex')) % len(turn_order_ids)
    except (TypeError, ValueError):
        return ''
    return turn_order_ids[turn_index]


def _filter_combat_reference_record(record: dict, visible_ids: set[str]) -> None:
    for key in tuple(record):
        value = record.get(key)
        if key in COMBAT_SINGLE_PARTICIPANT_REFERENCE_KEYS:
            if value not in (None, '') and _id_key(value) not in visible_ids:
                record.pop(key, None)
                for companion_key in COMBAT_REFERENCE_COMPANION_KEYS.get(key, ()):
                    record.pop(companion_key, None)
            continue
        if key in COMBAT_PARTICIPANT_REFERENCE_LIST_KEYS and isinstance(value, list):
            record[key] = _visible_reference_ids(value, visible_ids)


def _filter_combat_initiative(value: Any, participants_by_id: dict[str, dict]) -> list[dict]:
    if not isinstance(value, list):
        return []
    visible_names = {
        _text(record.get('name')).casefold()
        for record in participants_by_id.values()
        if _text(record.get('name'))
    }
    filtered: list[dict] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        participant_id = _record_id(entry)
        if participant_id:
            if participant_id in participants_by_id:
                filtered.append(_public_scalar_fields(entry, PUBLIC_INITIATIVE_KEYS))
            continue
        if _text(entry.get('name')).casefold() in visible_names:
            filtered.append(_public_scalar_fields(entry, PUBLIC_INITIATIVE_KEYS))
    return filtered


def _public_enemy_groups(value: Any, participants_by_id: dict[str, dict]) -> list[dict]:
    visible_enemy_names = {
        _text(record.get('name')).casefold()
        for record in participants_by_id.values()
        if _text(record.get('team')).lower() == 'enemy' and _text(record.get('name'))
    }
    groups: list[dict] = []
    for record in value if isinstance(value, list) else []:
        if not isinstance(record, dict):
            continue
        name = _text(record.get('name'))
        label = _text(record.get('label'))
        if name and name.casefold() not in visible_enemy_names:
            continue
        if not name and label.casefold() not in visible_enemy_names:
            continue
        payload = _public_scalar_fields(record, ('count', 'name', 'creatureTypeName'))
        payload['label'] = name or label
        groups.append(payload)
    return groups


def _filter_combat_flags(flags: dict, participants_by_id: dict[str, dict]) -> dict:
    visible_ids = set(participants_by_id)
    payload = _public_scalar_fields(flags, PUBLIC_COMBAT_FLAG_SCALAR_KEYS)
    for key in COMBAT_SINGLE_PARTICIPANT_REFERENCE_KEYS:
        value = flags.get(key)
        if value not in (None, '') and _id_key(value) in visible_ids:
            payload[key] = deepcopy(value)
            for companion_key in COMBAT_REFERENCE_COMPANION_KEYS.get(key, ()):
                if companion_key in flags:
                    payload[companion_key] = deepcopy(flags[companion_key])
    for key in COMBAT_PARTICIPANT_REFERENCE_LIST_KEYS:
        if isinstance(flags.get(key), list):
            payload[key] = _visible_reference_ids(flags[key], visible_ids)

    names_by_id = {
        participant_id: _text(record.get('name')) or _text(record.get('id'))
        for participant_id, record in participants_by_id.items()
    }
    turn_order = payload.get('turnOrder') if isinstance(payload.get('turnOrder'), list) else None
    if turn_order is not None:
        payload['turnOrderText'] = ' -> '.join(names_by_id[_id_key(value)] for value in turn_order if _id_key(value) in names_by_id)
    enemy_turn_block = payload.get('enemyTurnBlock') if isinstance(payload.get('enemyTurnBlock'), list) else None
    if enemy_turn_block is not None:
        payload['enemyTurnBlockText'] = ', '.join(
            names_by_id[_id_key(value)]
            for value in enemy_turn_block
            if _id_key(value) in names_by_id
        )

    groups = _public_enemy_groups(flags.get('enemyGroups'), participants_by_id)
    if groups:
        payload['enemyGroups'] = groups
    turn_economy = flags.get('turnEconomy') if isinstance(flags.get('turnEconomy'), dict) else {}
    economy_actor_id = _id_key(_first(turn_economy, 'actorId', 'actor_id'))
    if economy_actor_id and economy_actor_id in visible_ids:
        payload['turnEconomy'] = {
            **_public_scalar_fields(
                turn_economy,
                (
                    'version',
                    'round',
                    'actionRemaining',
                    'bonusActionRemaining',
                    'reactionRemaining',
                    'movementRemaining',
                ),
            ),
            'actorId': economy_actor_id,
        }
    payload['enemyCount'] = sum(
        1
        for record in participants_by_id.values()
        if _text(record.get('team')).lower() == 'enemy'
    )
    return payload


def _public_encounter_goal(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    payload = _public_scalar_fields(value, ('status', 'type'))
    player_objective = _public_alias_text(
        value,
        'playerObjective',
        'player_objective',
        'playerDescription',
        'player_description',
        'publicDescription',
        'public_description',
        'description',
    )
    if player_objective:
        payload['playerObjective'] = player_objective
        payload['description'] = player_objective
    return payload or None


def _battlefield_for_player(value: Any, *, scene: dict) -> dict:
    raw = deepcopy(value) if isinstance(value, dict) else {}
    for collection_key in ('zones', 'hazards', 'cover', 'exits', 'interactables'):
        records = raw.get(collection_key)
        if isinstance(records, list):
            raw[collection_key] = [
                record
                for record in records
                if isinstance(record, dict) and _record_player_visible(record)
            ]
    return normalize_battlefield(raw, scene)


def _filter_combat_for_player(
    combat: dict,
    private_player_ids: frozenset[int],
    *,
    scene: dict,
) -> None:
    original_participants = [
        record
        for record in (combat.get('participants') or [])
        if isinstance(record, dict)
    ]
    original_turn_order_ids = _combat_turn_order_ids(original_participants)
    current_actor_id = _current_combat_actor_id(combat, original_turn_order_ids)

    participants = [
        _combat_participant_for_viewer(record, private_player_ids)
        for record in original_participants
        if _is_player_combat_participant(record) or _record_player_visible(record)
    ]
    participants_by_id = {
        _record_id(record): record
        for record in participants
        if _record_id(record)
    }
    visible_ids = set(participants_by_id)
    for participant in participants:
        current_intent = participant.get('currentIntent')
        if isinstance(current_intent, dict):
            _filter_combat_reference_record(current_intent, visible_ids)
    payload = _public_scalar_fields(combat, ('status', 'round', 'turnIndex', 'lastRoundSummary'))
    payload['participants'] = participants
    payload['battlefield'] = _battlefield_for_player(combat.get('battlefield'), scene=scene)
    encounter_goal = _public_encounter_goal(combat.get('encounterGoal'))
    if encounter_goal:
        payload['encounterGoal'] = encounter_goal
    payload['initiative'] = _filter_combat_initiative(combat.get('initiative'), participants_by_id)
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    payload['flags'] = _filter_combat_flags(flags, participants_by_id)

    visible_turn_order_ids = _combat_turn_order_ids(participants)
    if current_actor_id and current_actor_id in visible_turn_order_ids:
        payload['turnIndex'] = visible_turn_order_ids.index(current_actor_id)
    elif 'turnIndex' in payload:
        payload['turnIndex'] = None
        payload.setdefault('flags', {})['turnAuthorityRedacted'] = True
    combat.clear()
    combat.update(payload)


def _player_pack_snapshot(pack: dict, flags: dict) -> dict:
    active_id = _text(
        _first(pack, 'activeCheckpointId', 'active_checkpoint_id', 'currentCheckpointId', 'current_checkpoint_id')
        or _first(flags, 'campaignPackActiveCheckpointId', 'activeCheckpointId')
    )
    completed_ids = _ids_from(pack, 'completedCheckpointIds', 'completed_checkpoint_ids') or _ids_from(
        flags,
        'campaignPackCompletedCheckpointIds',
        'completedCheckpointIds',
    )
    skipped_ids = _ids_from(pack, 'skippedCheckpointIds', 'skipped_checkpoint_ids') or _ids_from(
        flags,
        'campaignPackSkippedCheckpointIds',
        'skippedCheckpointIds',
    )
    failed_ids = _ids_from(pack, 'failedCheckpointIds', 'failed_checkpoint_ids') or _ids_from(
        flags,
        'campaignPackFailedCheckpointIds',
        'failedCheckpointIds',
    )
    checkpoint_statuses = _checkpoint_statuses(
        [checkpoint for checkpoint in (pack.get('checkpoints') or []) if isinstance(checkpoint, dict)],
        active_id=active_id,
        completed_ids=completed_ids,
        skipped_ids=skipped_ids,
        failed_ids=failed_ids,
    )
    visible_checkpoints = player_visible_checkpoint_payloads(pack.get('checkpoints'), checkpoint_statuses)
    visible_id_keys = {
        _id_key(checkpoint.get('id'))
        for checkpoint in visible_checkpoints
        if _text(checkpoint.get('id'))
    }
    visible_active_id = active_id if _id_key(active_id) in visible_id_keys else ''
    visible_completed_ids = [value for value in completed_ids if _id_key(value) in visible_id_keys]
    visible_skipped_ids = [value for value in skipped_ids if _id_key(value) in visible_id_keys]
    visible_failed_ids = [value for value in failed_ids if _id_key(value) in visible_id_keys]
    visible_statuses = {
        checkpoint['id']: checkpoint['status']
        for checkpoint in visible_checkpoints
        if checkpoint.get('id')
    }

    result = {
        'packId': _text(_first(pack, 'packId', 'pack_id')),
        'title': _text(_first(pack, 'title', 'name')),
        'schemaVersion': _text(_first(pack, 'schemaVersion', 'schema_version')) or '1',
        'version': _text(_first(pack, 'version')),
        'source': _text(_first(pack, 'source')) or 'campaign_pack',
        'visibility': 'player',
        'progressSchemaVersion': _positive_int(_first(pack, 'progressSchemaVersion', 'progress_schema_version')) or 1,
        'progressRevision': _progress_revision(pack, flags),
        'activeCheckpointId': visible_active_id or None,
        'completedCheckpointIds': visible_completed_ids,
        'skippedCheckpointIds': visible_skipped_ids,
        'failedCheckpointIds': visible_failed_ids,
        'checkpointStatuses': visible_statuses,
        'checkpoints': visible_checkpoints,
    }
    return {key: value for key, value in result.items() if value not in ('', None)}


def player_visible_checkpoint_payloads(value: Any, checkpoint_statuses: dict[str, str]) -> list[dict]:
    """Project checkpoint records without leaking hidden authored details.

    A hidden checkpoint remains absent even after it becomes active or terminal.
    Authors may intentionally expose a player-safe alias through the schema's
    ``playerTitle`` or ``playerSummary`` fields once the checkpoint would
    otherwise be visible.
    """

    checkpoints = [checkpoint for checkpoint in (value or []) if isinstance(checkpoint, dict)]
    visible: list[dict] = []
    for checkpoint in checkpoints:
        checkpoint_id = _checkpoint_id(checkpoint)
        if not checkpoint_id:
            continue
        status = checkpoint_statuses.get(checkpoint_id) or 'open'
        status_revealed = status in PLAYER_REVEALED_CHECKPOINT_STATUSES
        if not status_revealed and not _checkpoint_player_visible(checkpoint):
            continue

        player_title = _text(
            _first(checkpoint, 'playerTitle', 'player_title', 'publicTitle', 'public_title')
        )
        player_summary = _text(
            _first(checkpoint, 'playerSummary', 'player_summary', 'publicSummary', 'public_summary')
        )
        hidden = _checkpoint_player_hidden(checkpoint)
        if hidden and not (player_title or player_summary):
            continue

        payload = {
            'id': checkpoint_id,
            'status': status,
        }
        title = player_title or ('' if hidden else _text(_first(checkpoint, 'title', 'name')))
        if title:
            payload['title'] = title
        summary = player_summary or (
            ''
            if hidden or not status_revealed
            else _text(_first(checkpoint, 'summary', 'description'))
        )
        if summary:
            payload['summary'] = summary
        if _truthy(_first(checkpoint, 'optional', 'isOptional', 'is_optional')):
            payload['optional'] = True
        visible.append(payload)
    return visible


def _checkpoint_player_hidden(checkpoint: dict) -> bool:
    metadata = checkpoint.get('metadata') if isinstance(checkpoint.get('metadata'), dict) else {}
    return any(
        hidden_to_players(source)
        or _truthy(_first(source, 'dmOnly', 'dm_only', 'secret'))
        for source in (checkpoint, metadata)
    )


def _checkpoint_player_visible(checkpoint: dict) -> bool:
    return _truthy(
        _first(
            checkpoint,
            'visibleToPlayers',
            'visible_to_players',
            'knownToPlayers',
            'known_to_players',
            'playerVisible',
            'player_visible',
        )
    )


def _player_flags(flags: dict, player_pack: dict) -> dict:
    result = {}
    progress_revision_key = 'campaignPackProgressRevision'
    if progress_revision_key in flags:
        result[progress_revision_key] = flags[progress_revision_key]

    projection_keys = {
        'campaignPackActiveCheckpointId': 'activeCheckpointId',
        'campaignPackCompletedCheckpointIds': 'completedCheckpointIds',
        'campaignPackSkippedCheckpointIds': 'skippedCheckpointIds',
        'campaignPackFailedCheckpointIds': 'failedCheckpointIds',
    }
    for flag_key, pack_key in projection_keys.items():
        if flag_key not in flags:
            continue
        value = player_pack.get(pack_key)
        if pack_key == 'activeCheckpointId' and not value:
            continue
        result[flag_key] = value if value is not None else []
    return result


def _record_player_visible(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    metadata = record.get('metadata') if isinstance(record.get('metadata'), dict) else {}
    if hidden_to_players(record) or hidden_to_players(metadata):
        return False
    for source in (record, metadata):
        if _truthy(
            _first(
                source,
                'hiddenToPlayers',
                'hidden_to_players',
                'dmOnly',
                'dm_only',
                'secret',
            )
        ):
            return False
        explicit_visibility = _first(
            source,
            'knownToPlayers',
            'known_to_players',
            'visibleToPlayers',
            'visible_to_players',
            'playerVisible',
            'player_visible',
        )
        if explicit_visibility is not None and not _truthy(explicit_visibility):
            return False
    return True


def _checkpoint_statuses(
    checkpoints: list[dict],
    *,
    active_id: str,
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
) -> dict[str, str]:
    active_key = _id_key(active_id)
    completed_keys = {_id_key(value) for value in completed_ids}
    skipped_keys = {_id_key(value) for value in skipped_ids}
    failed_keys = {_id_key(value) for value in failed_ids}
    statuses: dict[str, str] = {}
    for checkpoint in checkpoints:
        checkpoint_id = _checkpoint_id(checkpoint)
        if not checkpoint_id:
            continue
        key = _id_key(checkpoint_id)
        if key == active_key:
            statuses[checkpoint_id] = 'active'
        elif key in failed_keys:
            statuses[checkpoint_id] = 'failed'
        elif key in skipped_keys:
            statuses[checkpoint_id] = 'skipped'
        elif key in completed_keys:
            statuses[checkpoint_id] = 'completed'
        elif _truthy(_first(checkpoint, 'optional', 'isOptional', 'is_optional')):
            statuses[checkpoint_id] = 'optional'
        else:
            statuses[checkpoint_id] = 'open'
    return statuses


def _progress_revision(pack: dict, flags: dict) -> int:
    for value in (
        _first(pack, 'progressRevision', 'progress_revision'),
        _first(flags, 'campaignPackProgressRevision', 'progressRevision', 'progress_revision'),
    ):
        revision = _positive_int(value)
        if revision is not None:
            return revision
    return 0


def _checkpoint_id(checkpoint: dict) -> str:
    return _text(_first(checkpoint, 'id', 'checkpointId', 'checkpoint_id'))


def _ids_from(record: dict, *keys: str) -> list[str]:
    if not isinstance(record, dict):
        return []
    values: list[Any] = []
    for key in keys:
        if key not in record:
            continue
        value = record.get(key)
        if isinstance(value, str):
            values.extend(item.strip() for item in value.replace(';', ',').split(','))
        elif isinstance(value, list):
            values.extend(value)
        elif value not in (None, ''):
            values.append(value)
    return _unique_ids([_text(value) for value in values if _text(value)])


def _unique_ids(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if not text:
            continue
        key = _id_key(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _first(record: dict | None, *keys: str) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        if key in record:
            return record.get(key)
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower().replace(' ', '_') in {'1', 'true', 'yes', 'y', 'on', 'known', 'visible'}


def _text(value: Any) -> str:
    return str(value or '').strip()


def _id_key(value: Any) -> str:
    return _text(value).lower()
