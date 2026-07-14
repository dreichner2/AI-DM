"""Context assembly for DM prompt requests."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func

from aidm_server.canon_inventory import inventory_payload
from aidm_server.character_state import character_state_for_player
from aidm_server.database import db
from aidm_server.emergent_memory import build_emergent_context, dormant_threads
from aidm_server.interactables import project_scene_interactables
from aidm_server.models import (
    BestiaryEntry,
    Campaign,
    CampaignSegment,
    DmTurn,
    Player,
    PlayerAction,
    Session,
    SessionLogEntry,
    SessionState,
    World,
    safe_json_loads,
)
from aidm_server.race_system import build_race_context_summary
from aidm_server.segment_triggers import parse_trigger_spec
from aidm_server.services.content_settings import session_content_settings
from aidm_server.time_utils import utc_now
from aidm_server.turn_rules import response_mentions_roll_request


CONTEXT_VERSION = 'v2'
MAX_LIVE_ACTIVE_QUESTS = 5
MAX_LIVE_OBJECTIVES_PER_QUEST = 5
MAX_LIVE_RECENT_LOCATIONS = 8
MAX_LIVE_ACTIVE_NPCS = 8
MAX_LIVE_RECENT_KNOWN_NPCS = 8
MAX_LIVE_FLAGS = 20
MAX_LIVE_COMBAT_PARTICIPANTS = 12
MAX_LIVE_SCENE_ITEMS = 12
MAX_LIVE_SCENE_INTERACTABLES = 12
MAX_PACK_CHECKPOINTS = 4
MAX_PACK_LOCATIONS = 6
MAX_PACK_QUESTS = 4
MAX_PACK_NPCS = 6
MAX_PACK_ENCOUNTERS = 4
MAX_PACK_ENEMIES = 6
MAX_PACK_SEGMENTS = 6
MAX_SESSION_MEMORY_BEATS = 5
MAX_SESSION_MEMORY_THREADS = 5
RECENT_TURN_BACKFILL_MULTIPLIER = 3
RECENT_TURN_BACKFILL_EXTRA = 5
RECENT_TURN_CONTEXT_ROLE = 'completed_narration'


def _truncate_text(value: str | None, max_length: int) -> str:
    text = str(value or '').strip()
    if len(text) <= max_length:
        return text
    return f'{text[: max(0, max_length - 1)].rstrip()}…'


def _text_or_none(value, max_length: int) -> str | None:
    text = _truncate_text(value, max_length)
    return text or None


def _string_list(value, *, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or '').strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _numeric_turn_value(record: dict, *keys: str) -> int:
    values: list[int] = []
    for key in keys:
        try:
            value = int(record.get(key))
        except (TypeError, ValueError):
            continue
        values.append(value)
    return max(values) if values else 0


def _compact_objectives(value) -> list[dict]:
    objectives = value if isinstance(value, list) else []
    compact = []
    for objective in objectives:
        if not isinstance(objective, dict):
            continue
        compact.append(
            {
                'id': _text_or_none(objective.get('id'), 120),
                'description': _text_or_none(objective.get('description'), 220),
                'status': _text_or_none(objective.get('status'), 80),
                'optional': objective.get('optional') is True,
                'prerequisiteObjectiveIds': _string_list(
                    objective.get('prerequisiteObjectiveIds') or objective.get('prerequisites'),
                    limit=12,
                ),
                'completeWhen': objective.get('completeWhen') if isinstance(objective.get('completeWhen'), dict) else None,
                'failWhen': objective.get('failWhen') if isinstance(objective.get('failWhen'), dict) else None,
            }
        )
        if len(compact) >= MAX_LIVE_OBJECTIVES_PER_QUEST:
            break
    return compact


def _compact_quest(quest: dict) -> dict:
    return {
        'id': _text_or_none(quest.get('id'), 120),
        'title': _text_or_none(quest.get('title') or quest.get('name'), 180),
        'status': _text_or_none(quest.get('status'), 80),
        'stage': _text_or_none(quest.get('stage'), 180),
        'summary': _text_or_none(quest.get('summary'), 420),
        'completionPolicy': _text_or_none(quest.get('completionPolicy'), 40) or 'all',
        'validationMode': 'mechanical'
        if any(
            isinstance(objective, dict)
            and (
                isinstance(objective.get('completeWhen'), dict)
                or isinstance(objective.get('failWhen'), dict)
                or objective.get('prerequisiteObjectiveIds')
                or objective.get('prerequisites')
            )
            for objective in (quest.get('objectives') or [])
        )
        else 'legacy_narrative',
        'objectives': _compact_objectives(quest.get('objectives')),
    }


def _compact_location(location: dict) -> dict:
    return {
        'id': _text_or_none(location.get('id'), 120),
        'name': _text_or_none(location.get('name'), 180),
        'type': _text_or_none(location.get('type'), 80),
        'status': _text_or_none(location.get('status'), 80),
        'description': _text_or_none(location.get('description'), 420),
        'connectedLocationIds': _string_list(location.get('connectedLocationIds'), limit=12),
    }


def _compact_npc(npc: dict) -> dict:
    return {
        'id': _text_or_none(npc.get('id'), 120),
        'name': _text_or_none(npc.get('name'), 180),
        'race': _text_or_none(npc.get('race'), 80),
        'role': _text_or_none(npc.get('role'), 160),
        'disposition': _text_or_none(npc.get('disposition'), 80),
        'status': _text_or_none(npc.get('status'), 80),
        'locationId': _text_or_none(npc.get('locationId'), 120),
        'questIds': _string_list(npc.get('questIds'), limit=8),
    }


def _compact_scene_item(item: dict) -> dict:
    return {
        'id': _text_or_none(item.get('id') or item.get('itemId'), 120),
        'name': _text_or_none(item.get('name') or item.get('itemName'), 180),
        'quantity': item.get('quantity') if isinstance(item.get('quantity'), (int, float)) else None,
        'type': _text_or_none(item.get('type'), 80),
        'sourceActorId': _text_or_none(item.get('sourceActorId'), 120),
    }


def _compact_scene_interactable(item: dict) -> dict:
    compact: dict[str, Any] = {
        'id': _text_or_none(item.get('id'), 120),
        'name': _text_or_none(item.get('name'), 180),
        'kind': _text_or_none(item.get('kind'), 80),
        'description': _text_or_none(item.get('description'), 360),
        'knowledge': 'player_known',
    }
    for key in (
        'open',
        'locked',
        'broken',
        'searched',
        'inspected',
        'used',
        'usedCount',
        'depleted',
        'usesRemaining',
        'active',
        'triggered',
        'disarmed',
        'contentsKnown',
        'revision',
    ):
        if key in item and isinstance(item.get(key), (bool, int, float)):
            compact[key] = item.get(key)
    if isinstance(item.get('contents'), list):
        compact['contents'] = [
            _compact_scene_item(content)
            for content in item['contents']
            if isinstance(content, dict)
        ][:MAX_LIVE_SCENE_ITEMS]
    return {key: value for key, value in compact.items() if value is not None}


def _compact_flag_value(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate_text(value, 180)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError):
        encoded = str(value)
    return _truncate_text(encoded, 240)


def _compact_flags(flags) -> dict:
    if not isinstance(flags, dict):
        return {}
    compact = {}
    for key in sorted(flags.keys(), key=lambda item: str(item))[:MAX_LIVE_FLAGS]:
        text_key = str(key or '').strip()
        if text_key:
            compact[text_key[:120]] = _compact_flag_value(flags[key])
    return compact


def _compact_combat(snapshot: dict) -> dict:
    combat = snapshot.get('combat') if isinstance(snapshot.get('combat'), dict) else {}
    if not combat:
        return {'status': 'none'}
    participants = []
    for participant in combat.get('participants') or []:
        if not isinstance(participant, dict):
            continue
        hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
        intent = participant.get('currentIntent') if isinstance(participant.get('currentIntent'), dict) else {}
        participants.append(
            {
                'id': _text_or_none(participant.get('id'), 120),
                'name': _text_or_none(participant.get('name'), 160),
                'team': _text_or_none(participant.get('team'), 40),
                'kind': _text_or_none(participant.get('kind'), 40),
                'hp': {
                    'current': hp.get('current'),
                    'max': hp.get('max'),
                },
                'conditions': _string_list(participant.get('conditions'), limit=8),
                'position': participant.get('position') if isinstance(participant.get('position'), dict) else None,
                'isPresent': participant.get('isPresent', participant.get('present', True)) is not False,
                'isAlive': participant.get('isAlive') is not False,
                'isConscious': participant.get('isConscious') is not False,
                'morale': participant.get('morale') if isinstance(participant.get('morale'), (int, float)) else None,
                'availableTactics': [
                    {
                        'id': _text_or_none(ability.get('id'), 120),
                        'name': _text_or_none(ability.get('name'), 160),
                        'type': _text_or_none(ability.get('type'), 80),
                        'available': ability.get('available', True) is not False,
                        'usesRemaining': ability.get('usesRemaining')
                        if isinstance(ability.get('usesRemaining'), (int, float))
                        else None,
                    }
                    for ability in (participant.get('abilities') or [])
                    if isinstance(ability, dict)
                ][:8],
                'intent': {
                    'intentType': _text_or_none(intent.get('intentType'), 80),
                    'reason': _text_or_none(intent.get('reason'), 220),
                    'visibleTelegraph': _text_or_none(intent.get('visibleTelegraph'), 220),
                    'suggestedSpeech': _text_or_none(intent.get('suggestedSpeech'), 160),
                }
                if intent
                else None,
            }
        )
        if len(participants) >= MAX_LIVE_COMBAT_PARTICIPANTS:
            break
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), dict) else {}
    initiative = [
        {
            'participantId': _text_or_none(entry.get('participantId') or entry.get('id'), 120),
            'roll': entry.get('roll'),
            'modifier': entry.get('modifier'),
            'total': entry.get('total'),
            'order': entry.get('order'),
        }
        for entry in (combat.get('initiative') or [])
        if isinstance(entry, dict)
    ][:MAX_LIVE_COMBAT_PARTICIPANTS]
    initiative.sort(key=lambda entry: entry.get('order') if isinstance(entry.get('order'), int) else 999)
    turn_index = combat.get('turnIndex') if isinstance(combat.get('turnIndex'), int) else 0
    active_actor_id = None
    if initiative:
        active_actor_id = initiative[turn_index % len(initiative)].get('participantId')
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    turn_economy = flags.get('turnEconomy') if isinstance(flags.get('turnEconomy'), dict) else {}
    return {
        'status': _text_or_none(combat.get('status'), 40) or 'none',
        'round': combat.get('round') if isinstance(combat.get('round'), (int, float)) else 1,
        'turnIndex': turn_index,
        'activeActorId': _text_or_none(flags.get('activeActorId') or active_actor_id, 120),
        'initiative': initiative,
        'turnEconomy': {
            key: turn_economy.get(key)
            for key in (
                'actorId',
                'round',
                'actionRemaining',
                'bonusActionRemaining',
                'reactionRemaining',
                'movementRemaining',
            )
            if key in turn_economy
        },
        'battlefield': {
            'environmentType': _text_or_none(battlefield.get('environmentType'), 80),
            'lighting': _text_or_none(battlefield.get('lighting'), 40),
            'visibility': _text_or_none(battlefield.get('visibility'), 80),
        },
        'encounterGoal': combat.get('encounterGoal') if isinstance(combat.get('encounterGoal'), dict) else None,
        'participants': participants,
        'lastRoundSummary': _text_or_none(combat.get('lastRoundSummary'), 360),
    }


def _unique_records(records: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for record in records:
        record_id = str(record.get('id') or record.get('name') or '').strip()
        if not record_id or record_id in seen:
            continue
        seen.add(record_id)
        unique.append(record)
    return unique


def _compact_live_world_state(snapshot: dict) -> dict:
    if not isinstance(snapshot, dict) or not snapshot:
        return {}

    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    projected_objects = project_scene_interactables(snapshot, {'isGm': False})
    active_quest_ids = _string_list(scene.get('activeQuestIds'), limit=20)
    active_quest_order = {quest_id: index for index, quest_id in enumerate(active_quest_ids)}
    quests = [quest for quest in (snapshot.get('quests') or []) if isinstance(quest, dict)]

    def quest_is_active(quest: dict) -> bool:
        quest_id = str(quest.get('id') or '').strip()
        status = str(quest.get('status') or '').strip().lower()
        return quest_id in active_quest_order or status in {'active', 'available', 'open', 'in_progress'}

    active_quests = [quest for quest in quests if quest_is_active(quest)]
    active_quests.sort(
        key=lambda quest: (
            active_quest_order.get(str(quest.get('id') or '').strip(), 999),
            -_numeric_turn_value(quest, 'updatedAtTurn', 'createdAtTurn'),
        )
    )
    recent_finished_quests = [
        quest
        for quest in quests
        if not quest_is_active(quest)
        and str(quest.get('status') or '').strip().lower() in {'completed', 'failed', 'resolved'}
        and _numeric_turn_value(quest, 'updatedAtTurn', 'completedAtTurn') > 0
    ]
    recent_finished_quests.sort(
        key=lambda quest: -_numeric_turn_value(quest, 'updatedAtTurn', 'completedAtTurn', 'createdAtTurn')
    )
    compact_quests = [
        _compact_quest(quest)
        for quest in _unique_records([*active_quests, *recent_finished_quests])[:MAX_LIVE_ACTIVE_QUESTS]
    ]

    current_location_id = str(scene.get('locationId') or '').strip()
    locations = [location for location in (snapshot.get('locations') or []) if isinstance(location, dict)]
    locations.sort(
        key=lambda location: (
            0 if current_location_id and str(location.get('id') or '').strip() == current_location_id else 1,
            -_numeric_turn_value(location, 'lastVisitedTurn', 'updatedAtTurn', 'firstDiscoveredTurn'),
        )
    )

    party_npcs = [npc for npc in (snapshot.get('partyNpcs') or []) if isinstance(npc, dict)]
    known_npcs = [npc for npc in (snapshot.get('knownNpcs') or []) if isinstance(npc, dict)]
    active_npc_ids = _string_list(scene.get('activeNpcIds'), limit=20)
    active_npc_order = {npc_id: index for index, npc_id in enumerate(active_npc_ids)}
    all_npcs = _unique_records([*party_npcs, *known_npcs])
    # Scene presence is authoritative. Party membership does not teleport an
    # absent companion into dialogue or combat, and stale narration cannot do
    # so either. Legacy snapshots without an activeNpcIds record fail closed.
    active_npcs = [
        npc for npc in all_npcs if str(npc.get('id') or '').strip() in active_npc_order
    ]
    active_npcs.sort(
        key=lambda npc: (
            active_npc_order.get(str(npc.get('id') or '').strip(), 999),
            -_numeric_turn_value(npc, 'lastSeenTurn', 'updatedAtTurn', 'firstMetTurn'),
        )
    )
    active_npc_ids_included = {str(npc.get('id') or '').strip() for npc in active_npcs}
    recent_known_npcs = [
        npc for npc in known_npcs if str(npc.get('id') or '').strip() not in active_npc_ids_included
    ]
    recent_known_npcs.sort(key=lambda npc: -_numeric_turn_value(npc, 'lastSeenTurn', 'updatedAtTurn', 'firstMetTurn'))

    return {
        'currentScene': {
            'locationId': _text_or_none(scene.get('locationId'), 120),
            'name': _text_or_none(scene.get('name'), 180),
            'sceneType': _text_or_none(scene.get('sceneType'), 80),
            'dangerLevel': scene.get('dangerLevel') if isinstance(scene.get('dangerLevel'), (int, float)) else None,
            'mood': _text_or_none(scene.get('mood'), 120),
            'combatState': _text_or_none(scene.get('combatState'), 80),
            'description': _text_or_none(scene.get('description'), 520),
            'activeNpcIds': active_npc_ids,
            'activeQuestIds': active_quest_ids,
            'items': [
                _compact_scene_item(item)
                for item in (scene.get('items') or [])
                if isinstance(item, dict)
            ][:MAX_LIVE_SCENE_ITEMS],
            'interactables': [
                _compact_scene_interactable(item)
                for item in projected_objects.get('interactables', [])
                if isinstance(item, dict)
            ][:MAX_LIVE_SCENE_INTERACTABLES],
            'hazards': [
                _compact_scene_interactable(item)
                for item in projected_objects.get('hazards', [])
                if isinstance(item, dict)
            ][:MAX_LIVE_SCENE_INTERACTABLES],
        },
        'activeQuests': compact_quests,
        'recentLocations': [_compact_location(location) for location in locations[:MAX_LIVE_RECENT_LOCATIONS]],
        'activeNpcs': [_compact_npc(npc) for npc in active_npcs[:MAX_LIVE_ACTIVE_NPCS]],
        'recentKnownNpcs': [_compact_npc(npc) for npc in recent_known_npcs[:MAX_LIVE_RECENT_KNOWN_NPCS]],
        'combat': _compact_combat(snapshot),
        'flags': _compact_flags(snapshot.get('flags')),
    }


def _live_world_state_for_session(session_id) -> dict:
    if not session_id:
        return {}
    session = db.session.get(Session, session_id)
    if not session:
        return {}
    snapshot = safe_json_loads(session.state_snapshot, {})
    if not isinstance(snapshot, dict):
        return {}
    return _compact_live_world_state(snapshot)


def _record_id(record: dict | None) -> str:
    if not isinstance(record, dict):
        return ''
    return str(record.get('id') or record.get('checkpointId') or record.get('checkpoint_id') or '').strip()


def _record_value(record: dict | None, *keys: str):
    if not isinstance(record, dict):
        return None
    for key in keys:
        if key in record:
            return record.get(key)
    return None


def _string_values(value, *, limit: int = 20) -> list[str]:
    if isinstance(value, str):
        values = [item.strip() for item in value.replace(';', ',').split(',')]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    result: list[str] = []
    for item in values:
        text = str(item or '').strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _pack_matches(record: dict, pack_id: str | None) -> bool:
    if not isinstance(record, dict):
        return False
    if str(record.get('source') or '').strip() == 'campaign_pack':
        return True
    if pack_id and str(record.get('packId') or record.get('pack_id') or '').strip() == pack_id:
        return True
    return False


def _catalog_records(pack: dict, key: str, fallback: list[dict]) -> list[dict]:
    catalog = pack.get('catalog') if isinstance(pack.get('catalog'), dict) else {}
    records = catalog.get(key)
    if isinstance(records, list):
        catalog_records = [record for record in records if isinstance(record, dict)]
        live_records = [record for record in fallback if isinstance(record, dict)]
        live_by_id = {_id_key(_record_id(record) or record.get('id') or record.get('name') or record.get('title')): record for record in live_records}
        merged: list[dict] = []
        seen: set[str] = set()
        for record in catalog_records:
            key_id = _id_key(_record_id(record) or record.get('id') or record.get('name') or record.get('title'))
            live_record = live_by_id.get(key_id)
            merged.append({**record, **live_record} if live_record else record)
            if key_id:
                seen.add(key_id)
        for record in live_records:
            key_id = _id_key(_record_id(record) or record.get('id') or record.get('name') or record.get('title'))
            if key_id and key_id in seen:
                continue
            merged.append(record)
        return merged
    return fallback


def _known_record_ids(records: list[dict], pack_id: str | None) -> set[str]:
    return {
        str(record.get('id') or '').strip()
        for record in records
        if isinstance(record, dict) and _pack_matches(record, pack_id) and str(record.get('id') or '').strip()
    }


def _with_known_to_players(payload: dict, record_id: str | None, known_ids: set[str]) -> dict:
    if not isinstance(payload, dict):
        return payload
    payload['knownToPlayers'] = bool(record_id and record_id in known_ids)
    return payload


def _compact_pack_checkpoint(checkpoint: dict | None, *, runtime_status: str | None = None) -> dict | None:
    if not isinstance(checkpoint, dict):
        return None
    payload = {
        'id': _text_or_none(_record_id(checkpoint), 120),
        'title': _text_or_none(_record_value(checkpoint, 'title', 'name'), 180),
        'playerTitle': _text_or_none(_record_value(checkpoint, 'playerTitle', 'player_title', 'publicTitle', 'public_title'), 180),
        'status': _text_or_none(runtime_status or _record_value(checkpoint, 'status'), 80),
        'optional': True if _checkpoint_optional(checkpoint) else None,
        'terminal': True if _checkpoint_terminal(checkpoint) else None,
        'chapter': _text_or_none(_record_value(checkpoint, 'chapter'), 80),
        'act': _text_or_none(_record_value(checkpoint, 'act'), 80),
        'priority': _positive_int(_record_value(checkpoint, 'priority')),
        'gate': _text_or_none(_record_value(checkpoint, 'gate', 'gateBehavior', 'gate_behavior'), 80),
        'canCompleteOutOfOrder': True if _checkpoint_can_complete_out_of_order(checkpoint) else None,
        'summary': _text_or_none(_record_value(checkpoint, 'summary', 'description'), 420),
        'playerSummary': _text_or_none(_record_value(checkpoint, 'playerSummary', 'player_summary', 'publicSummary', 'public_summary'), 420),
        'locationIds': _string_values(
            _record_value(checkpoint, 'locationIds', 'location_ids', 'locations'),
            limit=8,
        ),
        'questIds': _string_values(_record_value(checkpoint, 'questIds', 'quest_ids', 'quests'), limit=8),
        'npcIds': _string_values(_record_value(checkpoint, 'npcIds', 'npc_ids', 'npcs'), limit=8),
        'encounterIds': _string_values(
            _record_value(checkpoint, 'encounterIds', 'encounter_ids', 'encounters'),
            limit=8,
        ),
        'nextCheckpointIds': _string_values(
            _record_value(
                checkpoint,
                'nextCheckpointIds',
                'next_checkpoint_ids',
                'unlocks',
                'downstreamCheckpointIds',
                'downstream_checkpoint_ids',
            ),
            limit=8,
        ),
        'alternateCheckpointIds': _alternate_checkpoint_ids(checkpoint)[:8],
        'prerequisiteCheckpointIds': _checkpoint_prerequisite_ids(checkpoint)[:8],
        'failureCheckpointIds': _failure_checkpoint_ids(checkpoint)[:8],
        'rejoinTargetCheckpointId': _text_or_none(
            _record_value(checkpoint, 'rejoinTargetCheckpointId', 'rejoin_target_checkpoint_id'),
            120,
        ),
    }
    return {key: value for key, value in payload.items() if value not in (None, [], {})}


def _compact_pack_location(location: dict) -> dict:
    return {
        'id': _text_or_none(location.get('id'), 120),
        'name': _text_or_none(location.get('name') or location.get('title'), 180),
        'type': _text_or_none(location.get('type'), 80),
        'status': _text_or_none(location.get('status'), 80),
        'description': _text_or_none(location.get('description') or location.get('summary'), 420),
        'connectedLocationIds': _string_values(
            location.get('connectedLocationIds') or location.get('connected_location_ids'),
            limit=10,
        ),
    }


def _compact_pack_npc(npc: dict) -> dict:
    return {
        'id': _text_or_none(npc.get('id'), 120),
        'name': _text_or_none(npc.get('name'), 180),
        'role': _text_or_none(npc.get('role'), 160),
        'disposition': _text_or_none(npc.get('disposition'), 80),
        'status': _text_or_none(npc.get('status'), 80),
        'locationId': _text_or_none(npc.get('locationId') or npc.get('location_id'), 120),
        'questIds': _string_values(npc.get('questIds') or npc.get('quest_ids'), limit=8),
    }


def _compact_pack_encounter(encounter: dict) -> dict:
    return {
        'id': _text_or_none(encounter.get('id'), 120),
        'title': _text_or_none(encounter.get('title') or encounter.get('name'), 180),
        'summary': _text_or_none(encounter.get('summary') or encounter.get('description'), 420),
        'locationIds': _string_values(encounter.get('locationIds') or encounter.get('location_ids'), limit=8),
        'questIds': _string_values(encounter.get('questIds') or encounter.get('quest_ids'), limit=8),
        'checkpointIds': _string_values(encounter.get('checkpointIds') or encounter.get('checkpoint_ids'), limit=8),
        'enemyIds': _string_values(encounter.get('enemyIds') or encounter.get('enemy_ids'), limit=8),
    }


def _compact_pack_enemy(entry: BestiaryEntry) -> dict:
    creature = safe_json_loads(entry.creature_json, {})
    creature = creature if isinstance(creature, dict) else {}
    behavior = creature.get('behavior') if isinstance(creature.get('behavior'), dict) else {}
    return {
        'id': _text_or_none(entry.creature_id or creature.get('id'), 120),
        'name': _text_or_none(entry.name or creature.get('name'), 180),
        'source': entry.source,
        'role': _text_or_none(behavior.get('combatRole'), 80),
        'challengeTier': _text_or_none(creature.get('challengeTier'), 80),
        'creatureType': _text_or_none(creature.get('creatureType'), 80),
        'description': _text_or_none(creature.get('descriptionShort') or creature.get('descriptionLong'), 360),
        'locationIds': _string_values(safe_json_loads(entry.location_ids_json, []), limit=8),
        'factionIds': _string_values(safe_json_loads(entry.faction_ids_json, []), limit=8),
        'tags': _string_values(safe_json_loads(entry.tags_json, []), limit=10),
    }


def _compact_pack_segment(segment: CampaignSegment, *, pack_id: str | None) -> dict:
    trigger_spec = parse_trigger_spec(segment.trigger_condition)
    return {
        'segment_id': segment.segment_id,
        'externalId': _text_or_none(segment.external_id, 120),
        'title': _text_or_none(segment.title, 180),
        'description': _text_or_none(segment.description, 420),
        'triggerType': trigger_spec.trigger_type,
        'isTriggered': bool(segment.is_triggered),
        'tags': _string_values(segment.tags, limit=12),
        'source': _text_or_none(segment.source, 40),
        'packId': _text_or_none(segment.source_pack_id, 120) or pack_id,
    }


def _checkpoint_by_id(checkpoints: list[dict], checkpoint_id: str | None) -> dict | None:
    if not checkpoint_id:
        return None
    return next((checkpoint for checkpoint in checkpoints if _record_id(checkpoint) == checkpoint_id), None)


def _checkpoint_ids_from(value) -> list[str]:
    return _unique_pack_ids(_string_values(value, limit=MAX_PACK_CHECKPOINTS))


def _select_active_checkpoint(pack: dict, flags: dict, checkpoints: list[dict]) -> tuple[dict | None, list[str], list[str], list[str]]:
    completed_ids = _checkpoint_ids_from(
        _record_value(pack, 'completedCheckpointIds', 'completed_checkpoint_ids')
        or flags.get('campaignPackCompletedCheckpointIds')
        or flags.get('completedCheckpointIds')
    )
    skipped_ids = _checkpoint_ids_from(
        _record_value(pack, 'skippedCheckpointIds', 'skipped_checkpoint_ids')
        or flags.get('campaignPackSkippedCheckpointIds')
        or flags.get('skippedCheckpointIds')
    )
    failed_ids = _checkpoint_ids_from(
        _record_value(pack, 'failedCheckpointIds', 'failed_checkpoint_ids')
        or flags.get('campaignPackFailedCheckpointIds')
        or flags.get('failedCheckpointIds')
    )
    active_id = (
        str(
            _record_value(pack, 'activeCheckpointId', 'active_checkpoint_id', 'currentCheckpointId', 'current_checkpoint_id')
            or flags.get('campaignPackActiveCheckpointId')
            or ''
        ).strip()
        or None
    )
    active_checkpoint = _checkpoint_by_id(checkpoints, active_id)
    terminal_ids = _terminal_checkpoint_ids(completed_ids, skipped_ids, failed_ids)
    if not active_checkpoint or _id_key(_record_id(active_checkpoint)) in {_id_key(value) for value in terminal_ids}:
        active_checkpoint = _first_incomplete_checkpoint(checkpoints, completed_ids, skipped_ids, failed_ids)
    return active_checkpoint, completed_ids, skipped_ids, failed_ids


def _select_next_checkpoints(
    *,
    checkpoints: list[dict],
    active_checkpoint: dict | None,
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
) -> list[dict]:
    if not active_checkpoint:
        return []
    active_id = _record_id(active_checkpoint)
    terminal_keys = {_id_key(checkpoint_id) for checkpoint_id in _terminal_checkpoint_ids(completed_ids, skipped_ids, failed_ids)}
    next_ids = _unique_pack_ids([*_next_checkpoint_ids(active_checkpoint), *_alternate_checkpoint_ids(active_checkpoint)])
    selected = [_checkpoint_by_id(checkpoints, checkpoint_id) for checkpoint_id in next_ids]
    selected = [
        checkpoint
        for checkpoint in selected
        if checkpoint
        and _id_key(_record_id(checkpoint)) not in terminal_keys
        and _checkpoint_available(checkpoint, completed_ids, skipped_ids, failed_ids)
    ]
    if not selected:
        try:
            active_index = checkpoints.index(active_checkpoint)
        except ValueError:
            active_index = -1
        required = [
            checkpoint
            for checkpoint in checkpoints[active_index + 1 :]
            if _id_key(_record_id(checkpoint)) not in terminal_keys
            and _record_id(checkpoint) != active_id
            and _checkpoint_available(checkpoint, completed_ids, skipped_ids, failed_ids)
            and not _checkpoint_optional(checkpoint)
        ]
        optional = [
            checkpoint
            for checkpoint in checkpoints[active_index + 1 :]
            if _id_key(_record_id(checkpoint)) not in terminal_keys
            and _record_id(checkpoint) != active_id
            and _checkpoint_available(checkpoint, completed_ids, skipped_ids, failed_ids)
            and _checkpoint_optional(checkpoint)
        ]
        selected = required or optional
    return _prioritized_checkpoints(selected, checkpoints)[:MAX_PACK_CHECKPOINTS]


def _next_checkpoint_ids(checkpoint: dict) -> list[str]:
    return _ids_from(
        checkpoint,
        'nextCheckpointIds',
        'next_checkpoint_ids',
        'unlocks',
        'downstreamCheckpointIds',
        'downstream_checkpoint_ids',
    )


def _alternate_checkpoint_ids(checkpoint: dict) -> list[str]:
    return _ids_from(
        checkpoint,
        'alternateCheckpointIds',
        'alternate_checkpoint_ids',
        'alternateRouteCheckpointIds',
        'alternate_route_checkpoint_ids',
        'routeCheckpointIds',
        'route_checkpoint_ids',
    )


def _failure_checkpoint_ids(checkpoint: dict) -> list[str]:
    return _ids_from(
        checkpoint,
        'failureCheckpointIds',
        'failure_checkpoint_ids',
        'failedCheckpointIds',
        'failed_checkpoint_ids',
        'onFailCheckpointIds',
        'on_fail_checkpoint_ids',
    )


def _terminal_checkpoint_ids(completed_ids: list[str], skipped_ids: list[str], failed_ids: list[str]) -> list[str]:
    return _unique_pack_ids([*completed_ids, *skipped_ids, *failed_ids])


def _first_incomplete_checkpoint(
    checkpoints: list[dict],
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
) -> dict | None:
    terminal_keys = {_id_key(checkpoint_id) for checkpoint_id in _terminal_checkpoint_ids(completed_ids, skipped_ids, failed_ids)}
    first_optional: dict | None = None
    for checkpoint in checkpoints:
        if _id_key(_record_id(checkpoint)) in terminal_keys:
            continue
        if not _checkpoint_available(checkpoint, completed_ids, skipped_ids, failed_ids):
            continue
        if _checkpoint_optional(checkpoint):
            first_optional = first_optional or checkpoint
            continue
        return checkpoint
    return first_optional


def _checkpoint_optional(checkpoint: dict) -> bool:
    return _truthy(_record_value(checkpoint, 'optional', 'isOptional', 'is_optional'))


def _checkpoint_terminal(checkpoint: dict) -> bool:
    kind = _status_key(_record_value(checkpoint, 'kind', 'type', 'checkpointType', 'checkpoint_type'))
    return _truthy(_record_value(checkpoint, 'terminal', 'isTerminal', 'is_terminal', 'end', 'isEnd')) or kind in {
        'terminal',
        'end',
        'finale',
    }


def _checkpoint_can_complete_out_of_order(checkpoint: dict) -> bool:
    return _truthy(
        _record_value(
            checkpoint,
            'canCompleteOutOfOrder',
            'can_complete_out_of_order',
            'outOfOrderCompletion',
            'out_of_order_completion',
        )
    )


def _checkpoint_priority(checkpoint: dict) -> int:
    return _positive_int(_record_value(checkpoint, 'priority')) or 0


def _prioritized_checkpoints(selected: list[dict], all_checkpoints: list[dict]) -> list[dict]:
    order = {_record_id(checkpoint): index for index, checkpoint in enumerate(all_checkpoints)}
    return sorted(selected, key=lambda checkpoint: (-_checkpoint_priority(checkpoint), order.get(_record_id(checkpoint), 9999)))


def _checkpoint_prerequisite_ids(checkpoint: dict) -> list[str]:
    return _ids_from(
        checkpoint,
        'prerequisiteCheckpointIds',
        'prerequisite_checkpoint_ids',
        'requiredCheckpointIds',
        'required_checkpoint_ids',
        'requires',
        'requiresCheckpointIds',
        'requires_checkpoint_ids',
    )


def _checkpoint_available(
    checkpoint: dict,
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
) -> bool:
    prerequisites = _checkpoint_prerequisite_ids(checkpoint)
    if not prerequisites:
        return True
    policy = _status_key(_record_value(checkpoint, 'prerequisitePolicy', 'prerequisite_policy')) or 'completed_or_skipped'
    if policy in {'terminal', 'resolved', 'completed_or_skipped_or_failed'}:
        satisfied = {_id_key(value) for value in _terminal_checkpoint_ids(completed_ids, skipped_ids, failed_ids)}
    elif policy in {'completed_or_skipped', 'skipped_allowed'}:
        satisfied = {_id_key(value) for value in _unique_pack_ids([*completed_ids, *skipped_ids])}
    else:
        satisfied = {_id_key(value) for value in completed_ids}
    return {_id_key(value) for value in prerequisites}.issubset(satisfied)


def _checkpoint_statuses(
    checkpoints: list[dict],
    *,
    active_checkpoint_id: str | None,
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
) -> dict[str, str]:
    active_key = _id_key(active_checkpoint_id)
    completed_keys = {_id_key(value) for value in completed_ids}
    skipped_keys = {_id_key(value) for value in skipped_ids}
    failed_keys = {_id_key(value) for value in failed_ids}
    statuses: dict[str, str] = {}
    for checkpoint in checkpoints:
        checkpoint_id = _record_id(checkpoint)
        key = _id_key(checkpoint_id)
        if not checkpoint_id:
            continue
        if key == active_key:
            statuses[checkpoint_id] = 'active'
        elif key in failed_keys:
            statuses[checkpoint_id] = 'failed'
        elif key in skipped_keys:
            statuses[checkpoint_id] = 'skipped'
        elif key in completed_keys:
            statuses[checkpoint_id] = 'completed'
        elif _checkpoint_optional(checkpoint):
            statuses[checkpoint_id] = 'optional'
        else:
            statuses[checkpoint_id] = 'open'
    return statuses


def _compact_checkpoint_statuses(
    checkpoint_statuses: dict[str, str],
    *,
    active_checkpoint: dict | None,
    next_checkpoints: list[dict],
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
) -> dict[str, str]:
    wanted_ids = _unique_pack_ids(
        [
            _record_id(active_checkpoint),
            *[_record_id(checkpoint) for checkpoint in next_checkpoints[:MAX_PACK_CHECKPOINTS]],
            *completed_ids[:MAX_PACK_CHECKPOINTS],
            *skipped_ids[:MAX_PACK_CHECKPOINTS],
            *failed_ids[:MAX_PACK_CHECKPOINTS],
        ]
    )
    return {
        checkpoint_id: checkpoint_statuses[checkpoint_id]
        for checkpoint_id in wanted_ids
        if checkpoint_id in checkpoint_statuses
    }


def _active_director_rules(pack: dict, checkpoint: dict | None) -> dict:
    rules = pack.get('directorRules') if isinstance(pack.get('directorRules'), dict) else {}
    checkpoint_rules = checkpoint.get('directorRules') if isinstance(checkpoint, dict) and isinstance(checkpoint.get('directorRules'), dict) else {}
    return {**rules, **checkpoint_rules}


def _pack_director_policy(director_rules: dict) -> dict:
    rules = director_rules if isinstance(director_rules, dict) else {}
    main_quest_generation = str(rules.get('mainQuestGeneration') or rules.get('main_quest_generation') or 'allowed_tagged').strip()
    side_quest_generation = str(rules.get('sideQuestGeneration') or rules.get('side_quest_generation') or 'allowed_tagged').strip()
    new_npcs = str(rules.get('newNpcs') or rules.get('new_npcs') or 'allowed_as_minor_or_temporary').strip()
    new_locations = str(rules.get('newLocations') or rules.get('new_locations') or 'allowed_as_local_detail').strip()
    off_track_policy = str(rules.get('offTrackPolicy') or rules.get('off_track_policy') or 'improvise_and_reconnect').strip()
    checkpoint_style = str(rules.get('checkpointStyle') or rules.get('checkpoint_style') or 'soft').strip()
    instructions = [
        'Use campaign_pack quests, NPCs, locations, enemies, and checkpoints before inventing replacements.',
        'Treat checkpoints as an adventure spine, not forced player actions.',
        'If players go off track, improvise local consequences and steer toward the rejoin target.',
    ]
    if main_quest_generation == 'pack_only':
        instructions.append('Do not invent replacement main quests unless an explicit admin/director override allows it.')
    if side_quest_generation == 'allowed_tagged':
        instructions.append('Side content may be improvised only as clearly local/emergent and should reconnect to the pack.')
    return {
        'mainQuestGeneration': main_quest_generation,
        'sideQuestGeneration': side_quest_generation,
        'newNpcs': new_npcs,
        'newLocations': new_locations,
        'offTrackPolicy': off_track_policy,
        'checkpointStyle': checkpoint_style,
        'instructions': instructions,
    }


def _compact_pack_operator_notes(pack: dict) -> dict:
    notes: dict[str, Any] = {}
    for output_key, input_keys in {
        'gmNotes': ('gmNotes', 'gm_notes', 'hiddenNotes', 'hidden_notes'),
        'hiddenSceneNotes': ('hiddenSceneNotes', 'hidden_scene_notes'),
    }.items():
        value = _record_value(pack, *input_keys)
        compacted = _compact_pack_note_value(value)
        if compacted not in (None, '', [], {}):
            notes[output_key] = compacted
    return notes


def _compact_pack_note_value(value: Any) -> Any:
    if isinstance(value, str):
        return _text_or_none(value, 700)
    if isinstance(value, list):
        compacted_items = []
        for item in value[:6]:
            compacted_item = _compact_pack_note_value(item)
            if compacted_item not in (None, ''):
                compacted_items.append(compacted_item)
        return compacted_items
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for key, item in list(value.items())[:8]:
            text_key = _text_or_none(key, 80)
            if not text_key:
                continue
            compacted_value = _compact_pack_note_value(item)
            if compacted_value not in (None, '', [], {}):
                compacted[text_key] = compacted_value
        return compacted
    return _text_or_none(value, 240) if value not in (None, '') else None


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
    return _unique_pack_ids([str(value or '').strip() for value in values if str(value or '').strip()])


def _unique_pack_ids(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or '').strip()
        if not text:
            continue
        key = _id_key(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _id_key(value: Any) -> str:
    return str(value or '').strip().casefold()


def _status_key(value: Any) -> str:
    return str(value or '').strip().casefold().replace(' ', '_').replace('-', '_')


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _status_key(value) in {'1', 'true', 'yes', 'y', 'on', 'optional'}


def _location_ids_near_current(current_location_id: str, locations: list[dict]) -> list[str]:
    if not current_location_id:
        return []
    current = next((location for location in locations if str(location.get('id') or '').strip() == current_location_id), None)
    connected = _string_values(
        (current or {}).get('connectedLocationIds') or (current or {}).get('connected_location_ids'),
        limit=10,
    )
    return [current_location_id, *connected]


def _off_track_analysis(
    *,
    snapshot: dict,
    pack: dict,
    current_location_id: str,
    active_checkpoint: dict | None,
    checkpoints: list[dict],
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
    rejoin_target_id: str | None,
) -> dict:
    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    locations = _catalog_records(pack, 'locations', snapshot.get('locations') if isinstance(snapshot.get('locations'), list) else [])
    quests = _catalog_records(pack, 'quests', snapshot.get('quests') if isinstance(snapshot.get('quests'), list) else [])
    npcs = _catalog_records(
        pack,
        'npcs',
        [
            *[npc for npc in (snapshot.get('knownNpcs') or []) if isinstance(npc, dict)],
            *[npc for npc in (snapshot.get('partyNpcs') or []) if isinstance(npc, dict)],
        ],
    )
    clues = _catalog_records(pack, 'clues', snapshot.get('clues') if isinstance(snapshot.get('clues'), list) else [])
    pack_location_ids = {_record_id(location) for location in locations if _record_id(location)}
    pack_quest_ids = {_record_id(quest) for quest in quests if _record_id(quest)}
    active_quest_ids = _string_values(scene.get('activeQuestIds'), limit=20)
    terminal_ids = {_id_key(value) for value in _terminal_checkpoint_ids(completed_ids, skipped_ids, failed_ids)}
    active_checkpoint_id = _record_id(active_checkpoint)
    checkpoint_location_matches: list[str] = []
    if current_location_id:
        for checkpoint in checkpoints:
            checkpoint_id = _record_id(checkpoint)
            if not checkpoint_id or _id_key(checkpoint_id) in terminal_ids or checkpoint_id == active_checkpoint_id:
                continue
            location_ids = _string_values(_record_value(checkpoint, 'locationIds', 'location_ids', 'locations'), limit=20)
            if current_location_id in location_ids:
                checkpoint_location_matches.append(checkpoint_id)

    npc_by_id = {_record_id(npc): npc for npc in npcs if _record_id(npc)}
    required_npc_ids = _string_values(_record_value(active_checkpoint, 'npcIds', 'npc_ids', 'npcs') if active_checkpoint else [], limit=20)
    broken_npc_ids = [
        npc_id
        for npc_id in required_npc_ids
        if _status_key((npc_by_id.get(npc_id) or {}).get('status')) in {'dead', 'hostile', 'missing', 'alienated'}
        or _status_key((npc_by_id.get(npc_id) or {}).get('disposition')) in {'hostile', 'alienated'}
    ]

    clue_by_id = {_record_id(clue): clue for clue in clues if _record_id(clue)}
    required_clue_ids = _string_values(
        _record_value(active_checkpoint, 'clueIds', 'clue_ids', 'requiredClueIds', 'required_clue_ids') if active_checkpoint else [],
        limit=20,
    )
    broken_clue_ids = [
        clue_id
        for clue_id in required_clue_ids
        if _status_key((clue_by_id.get(clue_id) or {}).get('status')) in {'destroyed', 'lost', 'failed', 'unavailable'}
    ]

    combat = snapshot.get('combat') if isinstance(snapshot.get('combat'), dict) else {}
    combat_flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    allowed_outcomes = {_status_key(value) for value in _string_values(combat_flags.get('campaignPackAllowedOutcomes'), limit=20)}
    end_reason = _status_key(combat_flags.get('endReason') or combat_flags.get('end_reason'))
    combat_diverged = bool(
        _record_value(combat_flags, 'campaignPackEncounterId', 'campaign_pack_encounter_id')
        and end_reason
        and allowed_outcomes
        and end_reason not in allowed_outcomes
    )

    details = {
        'locationOffTrack': bool(current_location_id and pack_location_ids and current_location_id not in pack_location_ids),
        'questOffTrack': any(quest_id not in pack_quest_ids for quest_id in active_quest_ids) if pack_quest_ids else False,
        'npcDependencyBroken': bool(broken_npc_ids),
        'brokenNpcIds': broken_npc_ids,
        'checkpointBypassed': bool(checkpoint_location_matches),
        'bypassedCheckpointIds': checkpoint_location_matches[:8],
        'requiredClueDestroyed': bool(broken_clue_ids),
        'brokenClueIds': broken_clue_ids,
        'combatOutcomeDiverged': combat_diverged,
        'combatOutcome': end_reason or None,
        'rejoinTargetConfidence': 'high' if rejoin_target_id and not checkpoint_location_matches else 'medium' if rejoin_target_id else 'low',
    }
    reasons = [
        key
        for key in (
            'locationOffTrack',
            'questOffTrack',
            'npcDependencyBroken',
            'checkpointBypassed',
            'requiredClueDestroyed',
            'combatOutcomeDiverged',
        )
        if details[key]
    ]
    details['score'] = len(reasons)
    details['offTrack'] = bool(reasons)
    details['reasons'] = reasons
    return details


def _campaign_pack_director_for_session(campaign_id: int, session_id) -> dict:
    if not session_id:
        return {}
    session = db.session.get(Session, session_id)
    if not session:
        return {}
    snapshot = safe_json_loads(session.state_snapshot, {})
    if not isinstance(snapshot, dict):
        return {}
    pack = snapshot.get('campaignPack') if isinstance(snapshot.get('campaignPack'), dict) else {}
    pack_id = _text_or_none(_record_value(pack, 'packId', 'pack_id'), 120)
    if not pack_id:
        return {}

    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    flags = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}
    current_location_id = str(scene.get('locationId') or '').strip()
    active_quest_ids = _string_values(scene.get('activeQuestIds'), limit=20)
    if not active_quest_ids and pack.get('startingQuestId'):
        active_quest_ids = _string_values([pack.get('startingQuestId')], limit=1)

    visible_locations = [
        location
        for location in (snapshot.get('locations') or [])
        if isinstance(location, dict) and _pack_matches(location, pack_id)
    ]
    visible_quests = [
        quest
        for quest in (snapshot.get('quests') or [])
        if isinstance(quest, dict) and _pack_matches(quest, pack_id)
    ]
    visible_npcs = [
        npc
        for npc in [*(snapshot.get('partyNpcs') or []), *(snapshot.get('knownNpcs') or [])]
        if isinstance(npc, dict) and _pack_matches(npc, pack_id)
    ]
    visible_npcs = _unique_records(visible_npcs)

    locations = [
        location
        for location in _catalog_records(pack, 'locations', visible_locations)
        if isinstance(location, dict) and _pack_matches(location, pack_id)
    ]
    quests = [
        quest
        for quest in _catalog_records(pack, 'quests', visible_quests)
        if isinstance(quest, dict) and _pack_matches(quest, pack_id)
    ]
    npcs = [
        npc
        for npc in _catalog_records(pack, 'npcs', visible_npcs)
        if isinstance(npc, dict) and _pack_matches(npc, pack_id)
    ]
    npcs = _unique_records(npcs)
    known_location_ids = _known_record_ids(visible_locations, pack_id)
    known_quest_ids = _known_record_ids(visible_quests, pack_id)
    known_npc_ids = _known_record_ids(visible_npcs, pack_id)

    raw_checkpoints = pack.get('checkpoints') if isinstance(pack.get('checkpoints'), list) else []
    checkpoints = [checkpoint for checkpoint in raw_checkpoints if isinstance(checkpoint, dict)]
    active_checkpoint, completed_checkpoint_ids, skipped_checkpoint_ids, failed_checkpoint_ids = _select_active_checkpoint(
        pack,
        flags,
        checkpoints,
    )
    next_checkpoints = _select_next_checkpoints(
        checkpoints=checkpoints,
        active_checkpoint=active_checkpoint,
        completed_ids=completed_checkpoint_ids,
        skipped_ids=skipped_checkpoint_ids,
        failed_ids=failed_checkpoint_ids,
    )
    checkpoint_statuses = _checkpoint_statuses(
        checkpoints,
        active_checkpoint_id=_record_id(active_checkpoint),
        completed_ids=completed_checkpoint_ids,
        skipped_ids=skipped_checkpoint_ids,
        failed_ids=failed_checkpoint_ids,
    )
    compact_checkpoint_statuses = _compact_checkpoint_statuses(
        checkpoint_statuses,
        active_checkpoint=active_checkpoint,
        next_checkpoints=next_checkpoints,
        completed_ids=completed_checkpoint_ids,
        skipped_ids=skipped_checkpoint_ids,
        failed_ids=failed_checkpoint_ids,
    )
    active_director_rules = _active_director_rules(pack, active_checkpoint)

    active_checkpoint_ids = _string_values([_record_id(active_checkpoint)] if active_checkpoint else [], limit=1)
    next_checkpoint_ids = [_record_id(checkpoint) for checkpoint in next_checkpoints]
    checkpoint_quest_ids = _string_values(
        _record_value(active_checkpoint, 'questIds', 'quest_ids', 'quests') if active_checkpoint else [],
        limit=12,
    )
    checkpoint_location_ids = _string_values(
        _record_value(active_checkpoint, 'locationIds', 'location_ids', 'locations') if active_checkpoint else [],
        limit=12,
    )
    checkpoint_npc_ids = _string_values(
        _record_value(active_checkpoint, 'npcIds', 'npc_ids', 'npcs') if active_checkpoint else [],
        limit=12,
    )
    relevant_quest_ids = set(active_quest_ids + checkpoint_quest_ids)
    nearby_location_ids = set(_location_ids_near_current(current_location_id, locations) + checkpoint_location_ids)
    active_npc_ids = set(_string_values(scene.get('activeNpcIds'), limit=20) + checkpoint_npc_ids)

    relevant_quests = [
        quest
        for quest in quests
        if str(quest.get('id') or '').strip() in relevant_quest_ids
        or (
            str(quest.get('id') or '').strip() in known_quest_ids
            and str(quest.get('status') or '').strip().lower() in {'active', 'open', 'available', 'in_progress'}
        )
    ]
    if not relevant_quests:
        relevant_quests = quests[:MAX_PACK_QUESTS]

    relevant_locations = [
        location
        for location in locations
        if str(location.get('id') or '').strip() in nearby_location_ids
    ]
    if not relevant_locations and current_location_id:
        relevant_locations = [location for location in locations if str(location.get('id') or '').strip() == current_location_id]
    if not relevant_locations:
        relevant_locations = locations[:MAX_PACK_LOCATIONS]

    relevant_npcs = []
    for npc in npcs:
        npc_id = str(npc.get('id') or '').strip()
        location_id = str(npc.get('locationId') or npc.get('location_id') or '').strip()
        npc_quest_ids = set(_string_values(npc.get('questIds') or npc.get('quest_ids'), limit=12))
        if npc_id in active_npc_ids or location_id in nearby_location_ids or npc_quest_ids.intersection(relevant_quest_ids):
            relevant_npcs.append(npc)
    if not relevant_npcs:
        relevant_npcs = npcs[:MAX_PACK_NPCS]

    encounters = [
        encounter
        for encounter in _catalog_records(pack, 'encounters', pack.get('encounters') if isinstance(pack.get('encounters'), list) else [])
        if isinstance(encounter, dict)
    ]
    relevant_encounters = []
    for encounter in encounters:
        encounter_checkpoint_ids = set(_string_values(encounter.get('checkpointIds') or encounter.get('checkpoint_ids'), limit=12))
        encounter_location_ids = set(_string_values(encounter.get('locationIds') or encounter.get('location_ids'), limit=12))
        encounter_quest_ids = set(_string_values(encounter.get('questIds') or encounter.get('quest_ids'), limit=12))
        if (
            encounter_checkpoint_ids.intersection(set(active_checkpoint_ids + next_checkpoint_ids))
            or encounter_location_ids.intersection(nearby_location_ids)
            or encounter_quest_ids.intersection(relevant_quest_ids)
        ):
            relevant_encounters.append(encounter)
    if not relevant_encounters:
        relevant_encounters = encounters[:MAX_PACK_ENCOUNTERS]

    pack_segments = [
        segment
        for segment in CampaignSegment.query.filter_by(campaign_id=campaign_id).order_by(
            CampaignSegment.is_triggered.asc(),
            CampaignSegment.segment_id.asc(),
        )
        if segment.source == 'campaign_pack'
        or (pack_id and segment.source_pack_id == pack_id)
        or 'campaign_pack' in _string_values(segment.tags, limit=20)
        or (pack_id and f'pack:{pack_id}' in _string_values(segment.tags, limit=20))
    ]

    enemy_rows = (
        BestiaryEntry.query.filter_by(campaign_id=campaign_id, source='campaign_pack')
        .order_by(BestiaryEntry.updated_at.desc(), BestiaryEntry.bestiary_entry_id.asc())
        .limit(MAX_PACK_ENEMIES)
        .all()
    )

    rejoin_target = (
        _text_or_none(_record_value(active_checkpoint, 'rejoinTargetCheckpointId', 'rejoin_target_checkpoint_id'), 120)
        if active_checkpoint
        else None
    ) or _text_or_none(_record_id(active_checkpoint), 120)
    off_track = _off_track_analysis(
        snapshot=snapshot,
        pack=pack,
        current_location_id=current_location_id,
        active_checkpoint=active_checkpoint,
        checkpoints=checkpoints,
        completed_ids=completed_checkpoint_ids,
        skipped_ids=skipped_checkpoint_ids,
        failed_ids=failed_checkpoint_ids,
        rejoin_target_id=rejoin_target,
    )

    return {
        'enabled': True,
        'pack': {
            'packId': pack_id,
            'title': _text_or_none(_record_value(pack, 'title', 'name'), 180),
            'version': _text_or_none(_record_value(pack, 'version'), 80),
        },
        'policy': _pack_director_policy(active_director_rules),
        'operatorNotes': _compact_pack_operator_notes(pack),
        'activeCheckpoint': _compact_pack_checkpoint(
            active_checkpoint,
            runtime_status=checkpoint_statuses.get(_record_id(active_checkpoint)),
        ),
        'nextCheckpoints': [
            compact
            for compact in (
                _compact_pack_checkpoint(
                    checkpoint,
                    runtime_status=checkpoint_statuses.get(_record_id(checkpoint)),
                )
                for checkpoint in next_checkpoints[:MAX_PACK_CHECKPOINTS]
            )
            if compact
        ],
        'relevantRecords': {
            'quests': [
                _with_known_to_players(_compact_quest(quest), str(quest.get('id') or '').strip(), known_quest_ids)
                for quest in relevant_quests[:MAX_PACK_QUESTS]
            ],
            'locations': [
                _with_known_to_players(
                    _compact_pack_location(location),
                    str(location.get('id') or '').strip(),
                    known_location_ids,
                )
                for location in relevant_locations[:MAX_PACK_LOCATIONS]
            ],
            'npcs': [
                _with_known_to_players(_compact_pack_npc(npc), str(npc.get('id') or '').strip(), known_npc_ids)
                for npc in relevant_npcs[:MAX_PACK_NPCS]
            ],
            'encounters': [_compact_pack_encounter(encounter) for encounter in relevant_encounters[:MAX_PACK_ENCOUNTERS]],
            'enemies': [_compact_pack_enemy(entry) for entry in enemy_rows],
            'segments': [
                _compact_pack_segment(segment, pack_id=pack_id)
                for segment in pack_segments[:MAX_PACK_SEGMENTS]
            ],
        },
        'progress': {
            'completedCheckpointIds': completed_checkpoint_ids[:MAX_PACK_CHECKPOINTS],
            'skippedCheckpointIds': skipped_checkpoint_ids[:MAX_PACK_CHECKPOINTS],
            'failedCheckpointIds': failed_checkpoint_ids[:MAX_PACK_CHECKPOINTS],
            'checkpointStatuses': compact_checkpoint_statuses,
            'activeQuestIds': active_quest_ids[:MAX_PACK_QUESTS],
            'currentLocationId': _text_or_none(current_location_id, 120),
            'offTrack': off_track['offTrack'],
            'offTrackScore': off_track['score'],
            'offTrackReasons': off_track['reasons'],
            'offTrackDetails': off_track,
            'rejoinTargetCheckpointId': rejoin_target,
        },
    }


def _stable_turn_for_recent_context(turn: DmTurn) -> bool:
    """Only completed final narration belongs in recent_turns.

    The active turn is inserted before DM generation and has no dm_output yet.
    Roll-resolution rows can also briefly remain processing with no narration.
    Roll-request rows are likewise not final scene outcomes; unresolved ones are
    represented by pending_checks, and resolved ones should not keep steering
    unrelated future actions.
    Including those rows makes the model continue old actions instead of
    answering the current PLAYER INPUT.
    """
    if str(turn.status or '').strip().lower() == 'processing':
        return False
    if bool(turn.requires_roll) and turn.roll_value is None and response_mentions_roll_request(turn.dm_output):
        return False
    return bool(str(turn.dm_output or '').strip())


def _recent_turns_for_context(session_id: int, max_turns: int) -> list[DmTurn]:
    limit = max(max_turns, max_turns * RECENT_TURN_BACKFILL_MULTIPLIER, max_turns + RECENT_TURN_BACKFILL_EXTRA)
    candidates = (
        DmTurn.query.filter_by(session_id=session_id)
        .order_by(DmTurn.turn_id.desc())
        .limit(limit)
        .all()
    )
    stable_turns = []
    for turn in candidates:
        if _stable_turn_for_recent_context(turn):
            stable_turns.append(turn)
        if len(stable_turns) >= max_turns:
            break
    return list(reversed(stable_turns))


def _recent_actions_by_player(player_ids: list[int], limit_per_player: int = 3) -> dict[int, list[str]]:
    if not player_ids:
        return {}

    ranked_actions = (
        db.session.query(
            PlayerAction.player_id.label('player_id'),
            PlayerAction.action_text.label('action_text'),
            func.row_number()
            .over(
                partition_by=PlayerAction.player_id,
                order_by=(PlayerAction.timestamp.desc(), PlayerAction.action_id.desc()),
            )
            .label('row_number'),
        )
        .filter(PlayerAction.player_id.in_(player_ids))
        .subquery()
    )
    rows = (
        db.session.query(ranked_actions.c.player_id, ranked_actions.c.action_text)
        .filter(ranked_actions.c.row_number <= limit_per_player)
        .order_by(ranked_actions.c.player_id.asc(), ranked_actions.c.row_number.desc())
        .all()
    )

    recent_actions: dict[int, list[str]] = {}
    for row in rows:
        recent_actions.setdefault(int(row.player_id), []).append(str(row.action_text))
    return recent_actions


def _session_snapshot_text(session_obj: Session | None, *keys: str, max_length: int = 1200) -> str:
    if not session_obj:
        return ''
    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    if not isinstance(snapshot, dict):
        return ''
    for key in keys:
        text = _truncate_text(snapshot.get(key), max_length)
        if text:
            return text
    return ''


def _memory_snippet_beat(snippet: dict) -> dict:
    player_input = _truncate_text(snippet.get('player_input'), 160)
    dm_output = _truncate_text(snippet.get('dm_output') or snippet.get('summary'), 220)
    return {
        'turn_id': snippet.get('turn_id'),
        'player_input': player_input,
        'dm_output': dm_output,
        'outcome_status': _text_or_none(snippet.get('outcome_status'), 80),
        'roll_value': snippet.get('roll_value') if isinstance(snippet.get('roll_value'), (int, float)) else None,
    }


def _recent_turn_beat(turn: dict) -> dict:
    return {
        'turn_id': turn.get('turn_id'),
        'player_input': _truncate_text(turn.get('player_input'), 160),
        'dm_output': _truncate_text(turn.get('dm_output'), 220),
        'outcome_status': _text_or_none(turn.get('outcome_status'), 80),
        'roll_value': turn.get('roll_value') if isinstance(turn.get('roll_value'), (int, float)) else None,
    }


def _thread_summary_item(thread: dict) -> dict:
    return {
        'thread_id': thread.get('thread_id'),
        'title': _text_or_none(thread.get('title'), 180),
        'summary': _text_or_none(thread.get('summary'), 360),
        'status': _text_or_none(thread.get('status'), 80),
        'priority': thread.get('priority') if isinstance(thread.get('priority'), int) else None,
        'source': _text_or_none(thread.get('source'), 80),
    }


def _player_recap_text(
    *,
    campaign_summary: dict,
    current_location: str | None,
    current_quest: str | None,
    recap_text: str,
    recent_beats: list[dict],
) -> str:
    parts = [
        f"{campaign_summary.get('title') or 'The campaign'} is currently at {current_location or campaign_summary.get('location') or 'an unknown location'}.",
    ]
    quest = current_quest or campaign_summary.get('current_quest')
    if quest and quest != 'None':
        parts.append(f'Current quest: {quest}.')
    if recap_text:
        parts.append(_truncate_text(recap_text, 520))
    if recent_beats:
        latest = recent_beats[-1]
        latest_text = latest.get('dm_output') or latest.get('player_input')
        if latest_text:
            parts.append(f'Latest beat: {_truncate_text(latest_text, 220)}')
    return ' '.join(part for part in parts if part).strip()


def _build_session_memory(
    *,
    campaign_summary: dict,
    session_state_payload: dict,
    recent_turns: list[dict],
    recent_log: list[str],
    emergent_memory: dict,
    dormant_story_threads: list[dict],
    session_obj: Session | None,
) -> dict:
    memory_snippets = [
        snippet for snippet in session_state_payload.get('memory_snippets', []) if isinstance(snippet, dict)
    ]
    recent_beats = [_memory_snippet_beat(snippet) for snippet in memory_snippets[-MAX_SESSION_MEMORY_BEATS:]]
    if not recent_beats:
        recent_beats = [_recent_turn_beat(turn) for turn in recent_turns[-MAX_SESSION_MEMORY_BEATS:]]
    if not recent_beats and recent_log:
        recent_beats = [
            {
                'turn_id': None,
                'player_input': None,
                'dm_output': _truncate_text(entry, 220),
                'outcome_status': None,
                'roll_value': None,
            }
            for entry in recent_log[-MAX_SESSION_MEMORY_BEATS:]
        ]

    recap_text = _session_snapshot_text(session_obj, 'recap', 'summary', max_length=1200)
    rolling_summary = _truncate_text(session_state_payload.get('rolling_summary'), 1400)
    session_recap = recap_text or rolling_summary
    current_location = session_state_payload.get('current_location') or campaign_summary.get('location')
    current_quest = session_state_payload.get('current_quest') or campaign_summary.get('current_quest')
    open_threads = [
        _thread_summary_item(thread)
        for thread in emergent_memory.get('threads', [])[:MAX_SESSION_MEMORY_THREADS]
        if isinstance(thread, dict)
    ]
    dormant_hooks = [
        _thread_summary_item(thread)
        for thread in dormant_story_threads[:MAX_SESSION_MEMORY_THREADS]
        if isinstance(thread, dict)
    ]
    return {
        'hierarchy_version': 'v1',
        'campaign_arc': {
            'title': _text_or_none(campaign_summary.get('title'), 180),
            'current_location': _text_or_none(current_location, 180),
            'current_quest': _text_or_none(current_quest, 180),
            'summary': _truncate_text(rolling_summary or session_recap, 900),
        },
        'session_recap': _truncate_text(session_recap, 1200),
        'recent_beats': recent_beats,
        'open_threads': open_threads,
        'dormant_hooks': dormant_hooks,
        'player_recap': _player_recap_text(
            campaign_summary=campaign_summary,
            current_location=current_location,
            current_quest=current_quest,
            recap_text=session_recap,
            recent_beats=recent_beats,
        ),
    }


def build_dm_context(
    world_id,
    campaign_id,
    session_id=None,
    max_turns: int = 8,
    query_text: str | None = None,
    active_player_ids: list[int] | None = None,
    current_player_id: int | None = None,
):
    """Build deterministic bounded context for DM responses."""
    world = db.session.get(World, world_id)
    campaign = db.session.get(Campaign, campaign_id)

    world_summary = {
        'world_id': world_id,
        'name': world.name if world else 'Unknown',
        'description': world.description if world else 'No world data available.',
    }

    campaign_summary = {
        'campaign_id': campaign_id,
        'title': campaign.title if campaign else 'Unknown',
        'description': campaign.description if campaign else 'No campaign data available.',
        'current_quest': (campaign.current_quest if campaign else None) or 'None',
        'location': (campaign.location if campaign else None) or 'Unknown',
    }

    players = (
        Player.query.filter_by(workspace_id=campaign.workspace_id, campaign_id=campaign.campaign_id)
        .order_by(Player.player_id.asc())
        .all()
        if campaign
        else []
    )
    active_id_set = {int(player_id) for player_id in active_player_ids or [] if player_id}
    if current_player_id:
        active_id_set.add(int(current_player_id))
    context_players = [player for player in players if not active_id_set or player.player_id in active_id_set]

    recent_actions_map = _recent_actions_by_player([player.player_id for player in context_players])
    active_players = []
    for player in context_players:
        active_players.append(
            {
                'player_id': player.player_id,
                'character_name': player.character_name,
                'race': player.race,
                'race_summary': build_race_context_summary(player.race_selection, player.race),
                'class': player.class_,
                'level': player.level,
                'state': character_state_for_player(player),
                'inventory': inventory_payload(player.inventory),
                'recent_actions': recent_actions_map.get(player.player_id, []),
            }
        )

    recent_turns = []
    if session_id:
        for turn in _recent_turns_for_context(session_id, max_turns):
            recent_turns.append(
                {
                    'turn_id': turn.turn_id,
                    'context_role': RECENT_TURN_CONTEXT_ROLE,
                    'player_id': turn.player_id,
                    'player_input': _truncate_text(turn.player_input, 240),
                    'dm_output': _truncate_text(turn.dm_output, 600),
                    'requires_roll': turn.requires_roll,
                    'rule_type': turn.rule_type,
                    'confidence': turn.confidence,
                    'roll_value': turn.roll_value,
                    'outcome_status': turn.outcome_status,
                }
            )
    current_turn_id = max(
        [int(turn.get('turn_id') or 0) for turn in recent_turns if isinstance(turn, dict)],
        default=0,
    )
    if session_id and current_turn_id <= 0:
        current_turn_id = (
            db.session.query(func.max(DmTurn.turn_id))
            .filter(DmTurn.session_id == session_id)
            .scalar()
            or 0
        )

    recent_log = []
    if session_id and not recent_turns:
        entries = (
            SessionLogEntry.query.filter_by(session_id=session_id)
            .order_by(SessionLogEntry.timestamp.desc(), SessionLogEntry.id.desc())
            .limit(max_turns)
            .all()
        )
        recent_log = [entry.message for entry in reversed(entries)]

    pending_checks = []
    if session_id:
        pending_turns = (
            DmTurn.query.filter_by(session_id=session_id, outcome_status='deferred')
            .order_by(DmTurn.turn_id.asc())
            .limit(5)
            .all()
        )
        for turn in pending_turns:
            turn_hint = safe_json_loads(turn.rules_hint, {})
            turn_metadata = safe_json_loads(turn.metadata_json, {})
            turn_metadata = turn_metadata if isinstance(turn_metadata, dict) else {}
            pending_checks.append(
                {
                    'turn_id': turn.turn_id,
                    'player_input': turn.player_input,
                    'rule_type': turn.rule_type,
                    'dc_hint': turn_hint.get('dc_hint') if isinstance(turn_hint, dict) else None,
                    'turn_number': turn_hint.get('turn_number') if isinstance(turn_hint, dict) else None,
                    'roll_gate': turn_metadata.get('roll_gate'),
                }
            )

    segments = CampaignSegment.query.filter_by(campaign_id=campaign_id, is_triggered=True).all()
    triggered_segments = [
        {
            'segment_id': seg.segment_id,
            'title': seg.title,
            'description': seg.description,
            'tags': seg.tags,
        }
        for seg in segments
    ]

    session_state_payload = {
        'rolling_summary': '',
        'current_location': campaign_summary['location'],
        'current_quest': campaign_summary['current_quest'],
        'active_segments': [],
        'memory_snippets': [],
    }

    if session_id:
        state = SessionState.query.filter_by(session_id=session_id).first()
        if state:
            memory_snippets = safe_json_loads(state.memory_snippets, [])
            memory_snippets = memory_snippets if isinstance(memory_snippets, list) else []
            session_state_payload = {
                'rolling_summary': _truncate_text(state.rolling_summary, 4000),
                'current_location': state.current_location or campaign_summary['location'],
                'current_quest': state.current_quest or campaign_summary['current_quest'],
                'active_segments': safe_json_loads(state.active_segments, []),
                'memory_snippets': [
                    {
                        **snippet,
                        'player_input': _truncate_text(snippet.get('player_input'), 180),
                        'dm_output': _truncate_text(snippet.get('dm_output'), 260),
                    }
                    for snippet in memory_snippets[-8:]
                    if isinstance(snippet, dict)
                ],
            }

    emergent_memory = build_emergent_context(
        campaign_id=campaign_id,
        session_id=session_id,
        query_text=query_text,
        current_location=session_state_payload['current_location'],
        current_quest=session_state_payload['current_quest'],
        recent_turns=recent_turns,
    )
    dormant_story_threads = dormant_threads(
        campaign_id=campaign_id,
        current_turn_id=current_turn_id,
        min_dormancy=30,
        limit=3,
    )
    live_world_state = _live_world_state_for_session(session_id)
    campaign_pack_director = _campaign_pack_director_for_session(campaign_id, session_id)
    session_obj = db.session.get(Session, session_id) if session_id else None
    content_settings = session_content_settings(session_obj)
    session_memory = _build_session_memory(
        campaign_summary=campaign_summary,
        session_state_payload=session_state_payload,
        recent_turns=recent_turns,
        recent_log=recent_log,
        emergent_memory=emergent_memory,
        dormant_story_threads=dormant_story_threads,
        session_obj=session_obj,
    )

    context_payload = {
        'context_version': CONTEXT_VERSION,
        'generated_at': utc_now().isoformat(),
        'world': world_summary,
        'campaign': campaign_summary,
        'session_state': session_state_payload,
        'session_memory': session_memory,
        'live_world_state': live_world_state,
        'campaign_pack_director': campaign_pack_director,
        'content_settings': content_settings,
        'player_identity_rules': [
            'character_name is the in-world player character identity.',
            'Account/profile names are out-of-character labels and are not characters in the scene.',
            'Only active_players are currently active in this session; narration and memory cannot add an absent player.',
        ],
        'state_authority_rules': [
            'live_world_state, active_players, pending_checks, and validated current combat state are authoritative.',
            'Recent narration and memory describe history only; they never override newer structured state.',
            'Only live_world_state.currentScene.activeNpcIds are physically present NPCs allowed to speak or act.',
            'Only player-known live_world_state.currentScene interactables and hazards may be described as discovered; their exact IDs, revisions, and state are authoritative.',
            'Narrate validated gameplay results and legal enemy tactics; do not invent unvalidated mechanical outcomes.',
        ],
        'active_players': active_players,
        'triggered_segments': triggered_segments,
        'authored_segments': triggered_segments,
        'story_threads': emergent_memory.get('threads', []),
        'dormant_threads': dormant_story_threads,
        'emergent_memory': emergent_memory,
        'recent_turns': recent_turns,
        'recent_log': recent_log,
        'pending_checks': pending_checks,
    }
    return json.dumps(context_payload, separators=(',', ':'), ensure_ascii=False)
