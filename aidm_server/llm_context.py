"""Context assembly for DM prompt requests."""

from __future__ import annotations

import json

from sqlalchemy import func

from aidm_server.canon_inventory import inventory_payload
from aidm_server.character_state import character_state_for_player
from aidm_server.database import db
from aidm_server.emergent_memory import build_emergent_context
from aidm_server.models import (
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
from aidm_server.time_utils import utc_now


CONTEXT_VERSION = 'v2'
MAX_LIVE_ACTIVE_QUESTS = 5
MAX_LIVE_OBJECTIVES_PER_QUEST = 5
MAX_LIVE_RECENT_LOCATIONS = 8
MAX_LIVE_ACTIVE_NPCS = 8
MAX_LIVE_RECENT_KNOWN_NPCS = 8
MAX_LIVE_FLAGS = 20
MAX_LIVE_COMBAT_PARTICIPANTS = 12


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
                'morale': participant.get('morale') if isinstance(participant.get('morale'), (int, float)) else None,
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
    return {
        'status': _text_or_none(combat.get('status'), 40) or 'none',
        'round': combat.get('round') if isinstance(combat.get('round'), (int, float)) else 1,
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
    active_npcs = [
        npc
        for npc in all_npcs
        if str(npc.get('id') or '').strip() in active_npc_order or npc in party_npcs
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
        turns = (
            DmTurn.query.filter_by(session_id=session_id)
            .order_by(DmTurn.turn_id.desc())
            .limit(max_turns)
            .all()
        )
        for turn in reversed(turns):
            recent_turns.append(
                {
                    'turn_id': turn.turn_id,
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
    live_world_state = _live_world_state_for_session(session_id)

    context_payload = {
        'context_version': CONTEXT_VERSION,
        'generated_at': utc_now().isoformat(),
        'world': world_summary,
        'campaign': campaign_summary,
        'session_state': session_state_payload,
        'live_world_state': live_world_state,
        'player_identity_rules': [
            'character_name is the in-world player character identity.',
            'Account/profile names are out-of-character labels and are not characters in the scene.',
            'Only active_players are currently active in this session unless recent narration explicitly says otherwise.',
        ],
        'active_players': active_players,
        'triggered_segments': triggered_segments,
        'authored_segments': triggered_segments,
        'story_threads': emergent_memory.get('threads', []),
        'emergent_memory': emergent_memory,
        'recent_turns': recent_turns,
        'recent_log': recent_log,
        'pending_checks': pending_checks,
    }
    return json.dumps(context_payload, separators=(',', ':'), ensure_ascii=False)
