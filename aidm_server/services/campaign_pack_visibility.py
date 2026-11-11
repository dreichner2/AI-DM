from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.combat.positioning import REACH_LABELS, reach_kind
from aidm_server.combat.state import combat_turn_order, normalize_battlefield, normalize_position
from aidm_server.game_state.campaign_pack_encounters import flag_condition_holds
from aidm_server.game_state.orchestration.conversations import (
    conversation_view,
    epilogue_lines,
    journal_person,
    journal_promises,
    shown_disposition,
    word_lines,
)
from aidm_server.game_state.zones import (
    captive_zone_id,
    fight_running,
    hero_zone_id,
    known_zone_ids,
    merchant_zone_id,
    object_zone_id,
    place_zones,
    rest_refusal,
    zone_steps,
    zones_for_viewer,
)
from aidm_server.commerce import open_merchant_projections
from aidm_server.game_clock import public_clock, scene_rest_policy
from aidm_server.interactables import offered_scene_actions, project_scene_interactables, take_label
from aidm_server.game_state.orchestration.fight_stakes import leads_of, stakes_view
from aidm_server.game_state.orchestration.scene_plans import affordance_offers, scene_facts
from aidm_server.services.campaign_pack_graph import hidden_to_players, is_terminal_checkpoint
from aidm_server.services.campaign_pack_snapshot import migrate_campaign_pack_snapshot
from aidm_server.services.session_return import plans_for_view
from aidm_server.services.tie_lines import THREADS_KEY, ending_lines
from aidm_server.world_map import project_world_map


PLAYER_REVEALED_CHECKPOINT_STATUSES = frozenset({'active', 'reached', 'completed', 'skipped', 'failed'})

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
    'inspiration',
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

# Where another hero stands; engagedWith is narrowed to visible participants.
PUBLIC_PLAYER_POSITION_KEYS = ('rangeBand', 'engagedWith')

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
    # What the place sounds and looks like: the Story view picks its ambience and weather from these.
    'ambience',
    'weather',
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
    'containerId',
    'containerName',
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
# Handouts and lore are read in full once the party has them.
READABLE_DOMAIN_COLLECTIONS = frozenset({'handouts', 'lore'})
READABLE_DOMAIN_STATUSES = frozenset({'revealed', 'unlocked'})

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
    acting_player_id: int | None = None,
    enemy_hit_points: bool = False,
) -> Any:
    """The player-safe projection of a session snapshot.

    ``private_player_ids`` names the heroes whose own records are included in
    full. Scene objects carry ``offeredActions`` for the acting hero: the one
    ``acting_player_id`` names, else the first present hero. Enemies show a
    ``health`` word instead of their hit points; ``enemy_hit_points`` keeps the
    numbers for server-side rules that judge the projection, which then call
    :func:`hide_enemy_hit_points`. A finishing blow a hero has yet to tell is
    ``combat.pendingFinisher`` (with its offered actions only in that hero's view).
    """

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
                    collection=key,
                    visible_location_ids=visible_location_ids,
                    visible_npc_ids=visible_npc_ids,
                    visible_quest_ids=visible_quest_ids,
                    visible_checkpoint_ids=visible_checkpoint_ids,
                )
                for record in _visible_records(migrated.get(key))
            ]
    world_map = project_world_map(migrated, record_visible=_record_player_visible)
    if world_map is not None:
        filtered['worldMap'] = world_map

    if isinstance(migrated.get('turnControl'), dict) or isinstance(migrated.get('turn_control'), dict):
        filtered['turnControl'] = _turn_control_for_viewer(
            migrated.get('turnControl') if isinstance(migrated.get('turnControl'), dict) else migrated.get('turn_control')
        )
    if isinstance(migrated.get('contentSettings'), dict) or isinstance(migrated.get('content_settings'), dict):
        filtered['contentSettings'] = _content_settings_for_viewer(
            migrated.get('contentSettings') if isinstance(migrated.get('contentSettings'), dict) else migrated.get('content_settings')
        )

    viewer = acting_hero(migrated, acting_player_id)
    viewer_id = _text((viewer or {}).get('id'))
    combat = deepcopy(migrated.get('combat')) if isinstance(migrated.get('combat'), dict) else None
    visible_combat_npc_ids: set[str] = set()
    if combat is not None:
        _filter_combat_for_player(combat, owned_player_ids, scene=raw_scene, viewer_id=viewer_id)
        # What the fight is about, in words (W9-SCENE); which enemy runs with it is the engine's to know.
        stakes = stakes_view(migrated)
        if stakes:
            combat['stakes'] = stakes
        if isinstance(combat.get('flags'), dict):
            for key in ('stakes', 'enemyGoal', 'secondaryGoal', 'runnerId'):
                combat['flags'].pop(key, None)
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
        offers = offered_scene_actions(migrated, viewer)
        # An object's authored affordances (W9-SCENE) are offered with its actions.
        plans = affordance_offers(migrated, viewer)
        for collection in ('interactables', 'hazards'):
            for entry in filtered['currentScene'][collection]:
                entry['offeredActions'] = [*offers.get(_text(entry.get('id')), []), *plans.get(_text(entry.get('id')), [])]
        filtered['currentScene']['facts'] = scene_facts(migrated)
        # Trade and rest: only open merchants, and the rest policy in effect here.
        filtered['currentScene']['merchants'] = open_merchant_projections(migrated)
        filtered['currentScene']['restPolicy'] = scene_rest_policy(raw_scene)
        _place_scene_for_viewer(migrated, filtered['currentScene'], viewer_id)
        talking = conversation_view(migrated, viewer_id)
        if talking:
            filtered['currentScene']['conversation'] = talking

    filtered['clock'] = public_clock(migrated.get('clock'))
    if pack:
        filtered['journal'] = _journal(migrated, filtered, owned_player_ids)
        if isinstance((filtered.get('campaignPack') or {}).get('ending'), dict):
            # The ending card's last sections: what became of the people the party met, how each hero's word stood at
            # the end, and the ready-made adventure a new one can start again from (``Play it again``).
            replay = _text((migrated.get('playNow') or {}).get('examplePackId')) if isinstance(migrated.get('playNow'), dict) else ''
            filtered['campaignPack']['afterword'] = {
                'becameOfThem': epilogue_lines(migrated),
                'yourWord': word_lines(migrated, {f'player_{player_id}' for player_id in owned_player_ids}),
                **({'replay': replay} if replay else {}),
            }
    return filtered if enemy_hit_points else hide_enemy_hit_points(filtered)


def _place_scene_for_viewer(state: dict, scene: dict, viewer_id: str) -> None:
    """In a place with rooms (:mod:`aidm_server.game_state.zones`): the zones the viewer knows (``zones``), and
    each object, merchant and captive marked with its ``zoneId``. Objects in zones no hero knows yet are left out,
    a merchant or captive beyond the next zone (or next door during a fight) is not offered, and resting outside
    the rest zones reads ``none`` with its ``restReason``."""

    zones = place_zones(state)
    if not zones:
        return
    known = set(known_zone_ids(state, zones))
    here = hero_zone_id(state, viewer_id, zones)

    def reachable(zone_id: str) -> bool:
        steps = zone_steps(zones, here, zone_id)
        return steps == 0 or (steps == 1 and not fight_running(state))

    scene['zones'] = zones_for_viewer(state, viewer_id)
    for collection in ('interactables', 'hazards'):
        scene[collection] = [
            {**entry, 'zoneId': zone_id}
            for entry in scene.get(collection) or []
            if (zone_id := object_zone_id(state, entry.get('id'), zones)) in known
        ]
    scene['merchants'] = [
        {**merchant, 'zoneId': zone_id}
        for merchant in scene.get('merchants') or []
        if reachable(zone_id := merchant_zone_id(state, merchant, zones))
    ]
    if isinstance(scene.get('captives'), list):
        captives = []
        for captive in scene['captives']:
            zone_id = captive_zone_id(state, captive, zones)
            captives.append({**captive, 'zoneId': zone_id, **({} if reachable(zone_id) else {'offeredActions': []})})
        scene['captives'] = captives
    refusal = rest_refusal(state, actor_id=viewer_id)
    if refusal:
        scene['restPolicy'] = 'none'
        scene['restReason'] = refusal


def hide_enemy_hit_points(snapshot: Any) -> Any:
    """Players judge an enemy by how it looks: its ``hp`` becomes a ``health`` word."""

    combat = snapshot.get('combat') if isinstance(snapshot, dict) else None
    if not isinstance(combat, dict):
        return snapshot
    for participant in combat.get('participants') or []:
        if isinstance(participant, dict) and _text(participant.get('team')).lower() == 'enemy':
            health = _health_word(_public_hp(participant.pop('hp', None)))
            if health:
                participant['health'] = health
    return snapshot


def acting_hero(snapshot: dict, acting_player_id: int | None = None) -> dict | None:
    """The hero scene-object legality is computed for.

    The hero ``acting_player_id`` names, else the first present hero (every
    hero counts as present when none is marked).
    """

    heroes = [record for record in (snapshot.get('playerCharacters') or []) if isinstance(record, dict)]
    if acting_player_id is not None:
        named = next((hero for hero in heroes if _record_player_id(hero) == acting_player_id), None)
        if named is not None:
            return named
    active_ids = {
        player_id
        for value in (snapshot.get('activePlayerIds') or [])
        if (player_id := _positive_int(value)) is not None
    }
    present = [hero for hero in heroes if not active_ids or _record_player_id(hero) in active_ids]
    return present[0] if present else None


def with_scene_offers(snapshot: Any, *, acting_player_id: int | None = None) -> Any:
    """The full snapshot with the scene's ``offeredActions`` and items' ``takeLabel``.

    Derived on read for the operator view; the persisted snapshot is untouched.
    Objects the acting hero cannot see carry no ``offeredActions``.
    """

    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('currentScene'), dict):
        return snapshot
    payload = dict(snapshot)
    scene = deepcopy(snapshot['currentScene'])
    offers = offered_scene_actions(snapshot, acting_hero(snapshot, acting_player_id))
    for collection in ('interactables', 'hazards'):
        for entry in scene.get(collection) or []:
            if isinstance(entry, dict) and _text(entry.get('id')) in offers:
                entry['offeredActions'] = offers[_text(entry.get('id'))]
    for item in scene.get('items') or []:
        if isinstance(item, dict):
            item['takeLabel'] = take_label(item)
    viewer = acting_hero(snapshot, acting_player_id)
    talking = conversation_view(snapshot, _text((viewer or {}).get('id')))
    if talking:
        scene['conversation'] = talking
    payload['currentScene'] = scene
    return payload


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
    if 'disposition' in record:
        # The engine's number is the players' word (``warm``), never the number.
        payload['disposition'] = shown_disposition(record)
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
        payload = _public_scalar_fields(record, ('id', 'status', 'title', 'name', 'optional'))
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
    collection: str,
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
    if collection in READABLE_DOMAIN_COLLECTIONS and _text(record.get('status')).lower() in READABLE_DOMAIN_STATUSES:
        _set_public_text(payload, 'text', record, 'playerText', 'player_text', 'text')
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
    payload['takeLabel'] = take_label(record)
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
    _set_public_text(
        payload,
        'summary',
        scene,
        'playerSummary',
        'player_summary',
        'publicSummary',
        'public_summary',
        'summary',
    )
    if isinstance(scene.get('visibleDetails'), list):
        payload['visibleDetails'] = _public_string_list(scene.get('visibleDetails'))
    payload['activeNpcIds'] = _visible_reference_ids(scene.get('activeNpcIds'), visible_npc_ids)
    payload['activeQuestIds'] = _visible_reference_ids(scene.get('activeQuestIds'), visible_quest_ids)
    payload['items'] = [
        _scene_item_for_viewer(record)
        for record in (scene.get('items') or [])
        if isinstance(record, dict) and _record_player_visible(record)
    ]
    for key in ('playerPositions', 'playerZones', 'characterPositions', 'characterZones'):
        payload[key] = _position_map_for_viewer(scene.get(key), visible_actor_refs)
    # Enemies a fight left captured or yielded; questioning one is a talk action.
    captives = [
        {
            **{key: _text(captive.get(key)) for key in ('id', 'name', 'enemyId')},
            'offeredActions': [{'kind': 'talk', 'label': f"Question {_text(captive.get('name'))}"}],
        }
        for captive in scene.get('captives') or []
        if isinstance(captive, dict) and _text(captive.get('name'))
    ]
    if captives:
        payload['captives'] = captives
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


def _health_word(hp: dict) -> str:
    current = _positive_int(_first(hp, 'current', 'currentHp'))
    maximum = _positive_int(_first(hp, 'max', 'maxHp'))
    if current is None or not maximum:
        return ''
    if current <= 0:
        return 'Down'
    if current >= maximum:
        return 'Unhurt'
    ratio = current / maximum
    if ratio <= 0.25:
        return 'Critical'
    return 'Wounded' if ratio <= 0.6 else 'Hurt'


def _public_death_saves(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {key: _positive_int(value.get(key)) or 0 for key in ('successes', 'failures')}


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
    if isinstance(record.get('position'), dict):
        position = normalize_position(record.get('position'))
        payload['position'] = {key: position[key] for key in PUBLIC_PLAYER_POSITION_KEYS if key in position}
    if isinstance(record.get('conditions'), list):
        payload['conditions'] = _public_string_list(record.get('conditions'))
    death_saves = _public_death_saves(record.get('deathSaves'))
    if death_saves is not None:
        payload['deathSaves'] = death_saves
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


def _combat_turn_order_ids(combat: dict) -> list[str]:
    """Who takes turns, in the engine's own initiative order (the order ``turnIndex`` counts in)."""

    return [_id_key(entry.get('id')) for entry in combat_turn_order(combat) if _id_key(entry.get('id'))]


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
                    'leveledSpellCast',
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
    viewer_id: str = '',
) -> None:
    """The player-safe fight; with ``viewer_id`` each other participant carries how far it is from that hero
    (``reach``: ``in_reach`` | ``one_move`` | ``out_of_reach`` | ``far_off``, and its ``reachLabel``)."""

    original_participants = [
        record
        for record in (combat.get('participants') or [])
        if isinstance(record, dict)
    ]
    turn_order_ids = _combat_turn_order_ids(combat)
    current_actor_id = _current_combat_actor_id(combat, turn_order_ids)
    viewer = next((record for record in original_participants if _record_id(record) == _id_key(viewer_id)), None) if viewer_id else None

    participants = []
    for record in original_participants:
        if not (_is_player_combat_participant(record) or _record_player_visible(record)):
            continue
        payload = _combat_participant_for_viewer(record, private_player_ids)
        if viewer is not None and record is not viewer:
            kind = reach_kind(viewer, record)
            payload['reach'] = kind
            payload['reachLabel'] = REACH_LABELS[kind]
        participants.append(payload)
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
        position = participant.get('position')
        if _is_player_combat_participant(participant) and isinstance(position, dict) and 'engagedWith' in position:
            engaged = _visible_reference_ids(position['engagedWith'], visible_ids)
            if engaged:
                position['engagedWith'] = engaged
            else:
                position.pop('engagedWith')
    payload = _public_scalar_fields(combat, ('status', 'round', 'turnIndex', 'lastRoundSummary'))
    payload['participants'] = participants
    payload['battlefield'] = _battlefield_for_player(combat.get('battlefield'), scene=scene)
    encounter_goal = _public_encounter_goal(combat.get('encounterGoal'))
    if encounter_goal:
        payload['encounterGoal'] = encounter_goal
    payload['initiative'] = _filter_combat_initiative(combat.get('initiative'), participants_by_id)
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    payload['flags'] = _filter_combat_flags(flags, participants_by_id)
    finisher = _pending_finisher_for_viewer(flags, private_player_ids)
    if finisher:
        payload['pendingFinisher'] = finisher

    # The same order less the participants players cannot see, so an index in
    # it is the full view's whenever nobody is hidden.
    visible_turn_order_ids = [participant_id for participant_id in turn_order_ids if participant_id in visible_ids]
    if current_actor_id and current_actor_id in visible_turn_order_ids:
        payload['turnIndex'] = visible_turn_order_ids.index(current_actor_id)
    elif 'turnIndex' in payload:
        payload['turnIndex'] = None
        payload.setdefault('flags', {})['turnAuthorityRedacted'] = True
    combat.clear()
    combat.update(payload)


def _pending_finisher_for_viewer(flags: dict, private_player_ids: frozenset[int]) -> dict | None:
    """The finishing blow a hero has yet to tell: who, against whom, and the line the other players see; the
    finishing hero's own view also carries its offered actions (``finishing_blow.finisher_actions``)."""

    # Deferred import: the finishing blow reads pack records, whose modules import this one.
    from aidm_server.game_state.orchestration.finishing_blow import PENDING_KEY, finisher_actions

    pending = flags.get(PENDING_KEY) if isinstance(flags.get(PENDING_KEY), dict) else {}
    hero_id = _text(pending.get('heroId'))
    if not hero_id:
        return None
    finisher = {key: _text(pending.get(key)) for key in ('heroId', 'heroName', 'enemyName', 'waitingLine')}
    if _record_player_id({'id': hero_id}) in private_player_ids:
        finisher['offeredActions'] = finisher_actions({'combat': {'flags': flags}}, hero_id)
    return finisher


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
        'ending': player_ending(
            visible_checkpoints,
            [checkpoint for checkpoint in (pack.get('checkpoints') or []) if isinstance(checkpoint, dict)],
            flags,
            pack=pack,
        ),
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
            'terminal': is_terminal_checkpoint(checkpoint),
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


def _chosen_variant(checkpoint: dict, flags: dict) -> dict | None:
    for variant in checkpoint.get('endingVariants') if isinstance(checkpoint.get('endingVariants'), list) else []:
        if (
            isinstance(variant, dict)
            and _text(variant.get('title'))
            and _text(variant.get('text'))
            and flag_condition_holds(flags, variant.get('when'))
        ):
            return variant
    return None


def ending_variant(checkpoint: dict, flags: dict) -> dict | None:
    """The first of a terminal checkpoint's ``endingVariants`` whose ``when`` holds: what the party chose."""

    variant = _chosen_variant(checkpoint, flags)
    return {'title': _text(variant['title']), 'text': _text(variant['text'])} if variant else None


def player_ending(visible_checkpoints: list[dict], checkpoints: list[dict], flags: dict, *, pack: dict | None = None) -> dict | None:
    """The reached terminal checkpoint as the story's ending, in its player-safe words.

    ``title`` and ``text`` are the matching ending variant's (else the
    checkpoint's own title and summary); ``summary`` stays the checkpoint's.
    The ``pack``'s ending lines for that variant follow its text, one for each
    hero whose seeking line fired.
    """

    reached = next((checkpoint for checkpoint in visible_checkpoints if checkpoint.get('status') == 'reached'), None)
    if reached is None:
        return None
    record = next((checkpoint for checkpoint in checkpoints if isinstance(checkpoint, dict) and _checkpoint_id(checkpoint) == reached['id']), {})
    variant = ending_variant(record, flags) or {}
    return {
        'checkpointId': reached['id'],
        'title': variant.get('title') or reached.get('title') or '',
        'summary': reached.get('summary') or '',
        'text': _told_ending(variant.get('text') or reached.get('summary') or '', pack, _chosen_variant(record, flags)),
    }


def _told_ending(text: str, pack: dict | None, chosen: dict | None) -> str:
    """An ending's text followed by the ``pack``'s ending lines for the ``chosen`` variant."""

    lines = ending_lines(pack, chosen) if pack and chosen else []
    return ' '.join(part for part in [text, *lines] if part)


TERMINAL_OBJECTIVE_STATUSES = frozenset({'completed', 'failed'})
JOURNAL_PLACE_STATES = {'current': 'current', 'visited': 'visited', 'known': 'discovered'}


def _journal(snapshot: dict, filtered: dict, owned_player_ids: frozenset[int]) -> dict:
    """What the party knows of its story, in player-safe words.

    The quest and stage come from the active checkpoint; objectives are every
    completed one, then the open ones of the active checkpoint (later ones are
    spoilers); clues, people and places are only those the party knows,
    each person with their disposition word and up to two lines of what they
    remember of the viewer's heroes; threads are the tie lines told for the
    viewer's own heroes, and ``yourWord`` their promises (open ones; once the
    story has ended, all of them, an open one ``left open``); ``plans`` are the
    players' own notes of what they mean to do; ``leads`` what a fight's
    outcome left the party to follow (W9-SCENE).
    """

    pack = snapshot['campaignPack']
    checkpoints = {
        _checkpoint_id(checkpoint): checkpoint
        for checkpoint in (pack.get('checkpoints') or [])
        if isinstance(checkpoint, dict) and _checkpoint_id(checkpoint)
    }
    active_id = _text((filtered.get('campaignPack') or {}).get('activeCheckpointId'))
    active = checkpoints.get(active_id) or {}
    hidden_stage = _checkpoint_player_hidden(active) if active else True
    quests = _visible_records(snapshot.get('quests'))
    quests_by_id = {_record_id(quest): quest for quest in quests}
    quest = next(
        (
            quests_by_id[_id_key(quest_id)]
            for quest_id in [
                *_ids_from(active, 'questIds', 'quest_ids'),
                _text(_first(pack, 'startingQuestId', 'starting_quest_id')),
            ]
            if _id_key(quest_id) in quests_by_id
        ),
        next((record for record in quests if _text(record.get('status')).lower() == 'active'), None),
    )
    stage_title = _text(_first(active, 'playerTitle', 'player_title')) or (
        '' if hidden_stage else _text(_first(active, 'title', 'name'))
    )
    stage_summary = _public_alias_text(active, 'playerSummary', 'player_summary')
    # The story's end is told as the party chose it, as the ending itself is, with its ending lines.
    flags = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}
    variant = ending_variant(active, flags) if active and is_terminal_checkpoint(active) else None
    if variant:
        stage_title, stage_summary = variant['title'], _told_ending(variant['text'], pack, _chosen_variant(active, flags))
    stage_objective_ids = {_id_key(value) for value in _ids_from(active, 'objectiveIds', 'objective_ids')}
    owned_hero_ids = {f'player_{player_id}' for player_id in owned_player_ids}
    completed: list[dict] = []
    open_now: list[dict] = []
    for record in quests:
        for objective in record.get('objectives') or []:
            if not isinstance(objective, dict) or not _record_player_visible(objective) or not _record_id(objective):
                continue
            status = _text(objective.get('status')).lower()
            if status == 'completed':
                completed.append(_journal_objective(objective, 'completed'))
            elif status not in TERMINAL_OBJECTIVE_STATUSES and _record_id(objective) in stage_objective_ids:
                open_now.append(_journal_objective(objective, 'active'))
    return {
        'quest': (
            {
                'id': _text(quest.get('id')),
                'title': _text(_first(quest, 'title', 'name')),
                'summary': _public_alias_text(quest, 'playerSummary', 'player_summary'),
                'status': _text(quest.get('status')) or 'active',
                'stageTitle': stage_title,
                'stageSummary': stage_summary,
            }
            if quest is not None
            else None
        ),
        'objectives': [*completed, *open_now],
        'clues': [
            {
                'id': _text(clue.get('id')),
                'title': _text(_first(clue, 'title', 'name')),
                'text': _public_alias_text(
                    clue,
                    'playerText',
                    'player_text',
                    'playerDescription',
                    'player_description',
                    'description',
                    'playerSummary',
                    'player_summary',
                    'summary',
                ),
            }
            for clue in _visible_records(snapshot.get('clues'))
            if _text(clue.get('id'))
        ],
        'people': [
            {
                'id': _text(npc.get('id')),
                'name': _text(npc.get('name')),
                'role': ' '.join(_text(npc.get('role')).replace('_', ' ').split()),
                'summary': _public_alias_text(npc, 'playerSummary', 'player_summary'),
                'met': True,
                **journal_person(npc, owned_hero_ids),
            }
            for npc in {
                _record_id(record): record
                for record in [*_visible_records(snapshot.get('knownNpcs')), *_visible_records(snapshot.get('partyNpcs'))]
                if _record_id(record)
            }.values()
        ],
        'places': [
            {'id': node['id'], 'name': node['name'], 'state': JOURNAL_PLACE_STATES[node['state']]}
            for node in ((filtered.get('worldMap') or {}).get('nodes') or [])
            if node.get('state') in JOURNAL_PLACE_STATES
        ],
        'threads': [
            {'id': _text(record.get('lineId')), 'title': _text(record.get('title')), 'text': _text(record.get('text'))}
            for record in pack.get(THREADS_KEY) or []
            if isinstance(record, dict) and _record_player_id({'id': record.get('heroId')}) in owned_player_ids
        ],
        'yourWord': journal_promises(snapshot, owned_hero_ids, ended=bool((filtered.get('campaignPack') or {}).get('ending'))),
        'leads': leads_of(snapshot),
        'plans': plans_for_view(snapshot),
    }


def _journal_objective(objective: dict, status: str) -> dict:
    return {
        'id': _text(objective.get('id')),
        'title': _text(_first(objective, 'title', 'name')) or _text(objective.get('description')),
        'optional': _truthy(_first(objective, 'optional', 'isOptional', 'is_optional')),
        'status': status,
    }


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
            statuses[checkpoint_id] = 'reached' if is_terminal_checkpoint(checkpoint) else 'active'
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
