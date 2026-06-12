"""Session projection updates derived from accepted canon and turns."""

from __future__ import annotations

import re
from typing import Any

from aidm_server.database import db
from aidm_server.models import (
    Campaign,
    DmTurn,
    Session,
    StoryFact,
    StoryThread,
    get_or_create_session_state,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.time_utils import utc_now

ACTIVE_THREAD_STATUSES = ('open', 'active')
ACTIVE_QUEST_STATUSES = {'active', 'open', 'available', 'in_progress', 'in-progress', 'started'}


def _valid_location_label(value: Any) -> bool:
    label = _text(value)
    if not label:
        return False
    words = re.findall(r'[A-Za-z0-9]+', label)
    return len(label) <= 90 and len(words) <= 10


def append_session_memory(turn: DmTurn):
    state = get_or_create_session_state(turn.session_id, turn.campaign)
    memory_snippets = safe_json_loads(state.memory_snippets, [])
    memory_snippets = memory_snippets if isinstance(memory_snippets, list) else []
    memory_snippets = [
        snippet
        for snippet in memory_snippets
        if not isinstance(snippet, dict) or snippet.get('turn_id') != turn.turn_id
    ]
    memory_snippets.append(
        {
            'turn_id': turn.turn_id,
            'player_input': turn.player_input[:250],
            'dm_output': (turn.dm_output or '')[:350],
            'requires_roll': turn.requires_roll,
            'rule_type': turn.rule_type,
            'confidence': turn.confidence,
            'roll_value': turn.roll_value,
            'outcome_status': turn.outcome_status,
            'created_at': utc_now().isoformat(),
        }
    )
    state.memory_snippets = safe_json_dumps(memory_snippets[-12:], [])

    existing_summary = (state.rolling_summary or '').strip()
    next_line = f"T{turn.turn_id} | P{turn.player_id}: {turn.player_input[:160]} | DM: {(turn.dm_output or '')[:220]}"
    summary_lines = [
        line
        for line in existing_summary.splitlines()
        if not line.startswith(f"T{turn.turn_id} | ")
    ]
    summary_lines.append(next_line)
    state.rolling_summary = '\n'.join(summary_lines).strip()[-8000:]
    state.updated_at = utc_now()
    return state


def _latest_location_fact(campaign_id: int) -> StoryFact | None:
    candidates = (
        StoryFact.query.filter(
            StoryFact.campaign_id == campaign_id,
            StoryFact.predicate == 'current_location',
            StoryFact.fact_status == 'accepted',
        )
        .order_by(StoryFact.fact_id.desc())
        .limit(100)
        .all()
    )
    return next((fact for fact in candidates if _valid_location_label(fact.value_text)), None)


def _text(value: Any) -> str:
    return str(value or '').strip()


def _slug(value: Any) -> str:
    normalized = re.sub(r'\s+', ' ', _text(value).lower().replace('_', ' ').replace('-', ' '))
    slug = re.sub(r'[^a-z0-9]+', '_', normalized).strip('_')
    return slug or 'item'


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _snapshot_records(snapshot: dict, key: str) -> list[dict]:
    records = snapshot.get(key)
    if isinstance(records, list):
        records[:] = [record for record in records if isinstance(record, dict)]
        return records
    records = []
    snapshot[key] = records
    return records


def _find_record(records: list[dict], *, record_id: str = '', title: str = '', name: str = '') -> dict | None:
    requested_id = _text(record_id)
    requested_title = _slug(title)
    requested_name = _slug(name)
    for record in records:
        if requested_id and _text(record.get('id') or record.get('locationId')) == requested_id:
            return record
    for record in records:
        record_label = _slug(record.get('title') or record.get('name'))
        if requested_title and record_label == requested_title:
            return record
        if requested_name and record_label == requested_name:
            return record
    return None


def _ensure_scene(snapshot: dict) -> dict:
    scene = snapshot.get('currentScene')
    if not isinstance(scene, dict):
        scene = {}
        snapshot['currentScene'] = scene
    scene.setdefault('sceneType', 'exploration')
    scene.setdefault('dangerLevel', 0)
    scene.setdefault('combatState', 'none')
    scene.setdefault('activeNpcIds', [])
    scene.setdefault('activeQuestIds', [])
    return scene


def _latest_valid_snapshot_location(snapshot: dict) -> str:
    for location in reversed(_snapshot_records(snapshot, 'locations')):
        location_name = _text(location.get('name') or location.get('locationName') or location.get('id'))
        if _valid_location_label(location_name):
            return location_name
    return ''


def _session_snapshot_location(session_id: int) -> str:
    session_obj = db.session.get(Session, session_id)
    if not session_obj:
        return ''
    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    if not isinstance(snapshot, dict):
        return ''
    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    scene_name = _text(scene.get('name') or scene.get('locationId'))
    if _valid_location_label(scene_name):
        return scene_name
    return _latest_valid_snapshot_location(snapshot)


def _sync_snapshot_location(
    snapshot: dict,
    location_fact: StoryFact | None,
    *,
    fallback_location: str = '',
) -> bool:
    location_name = _text(location_fact.value_text if location_fact else None)
    if not _valid_location_label(location_name):
        location_name = _text(fallback_location)
    if not _valid_location_label(location_name):
        location_name = _latest_valid_snapshot_location(snapshot)
    if not _valid_location_label(location_name):
        scene = _ensure_scene(snapshot)
        current_name = _text(scene.get('name') or scene.get('locationId'))
        if _valid_location_label(current_name):
            return False
        location_name = 'Unknown Location'

    scene = _ensure_scene(snapshot)
    location_id = _slug(location_name)
    changed = _slug(scene.get('name') or scene.get('locationId')) != location_id
    scene['locationId'] = location_id
    scene['name'] = location_name
    if changed:
        scene['sceneType'] = 'exploration'
        scene['dangerLevel'] = 0
        scene['combatState'] = 'none'
        scene['description'] = ''
        scene['activeNpcIds'] = []
        scene.pop('mood', None)
    if location_fact and location_fact.source_turn_id:
        scene['updatedAtTurn'] = location_fact.source_turn_id

    locations = _snapshot_records(snapshot, 'locations')
    location = _find_record(locations, record_id=location_id, name=location_name)
    if not location:
        location = {
            'id': location_id,
            'name': location_name,
            'type': 'other',
            'description': '',
            'status': 'visited',
            'parentLocationId': None,
            'connectedLocationIds': [],
            'npcIds': [],
            'questIds': [],
            'tags': [],
            'firstDiscoveredTurn': location_fact.source_turn_id if location_fact else None,
            'metadata': {},
        }
        locations.append(location)
    location['id'] = location_id
    location['name'] = location_name
    location['status'] = 'visited'
    if location_fact and location_fact.source_turn_id:
        if not location.get('firstDiscoveredTurn'):
            location['firstDiscoveredTurn'] = location_fact.source_turn_id
        location['lastVisitedTurn'] = location_fact.source_turn_id
    return True


def _thread_quest_id(thread: StoryThread) -> str:
    metadata = safe_json_loads(thread.metadata_json, {})
    metadata = metadata if isinstance(metadata, dict) else {}
    return _slug(metadata.get('quest_id') or metadata.get('questId') or thread.title)


def _sync_snapshot_quests(snapshot: dict, open_threads: list[StoryThread]) -> bool:
    if not open_threads:
        return False

    scene = _ensure_scene(snapshot)
    quests = _snapshot_records(snapshot, 'quests')
    active_quest_ids: list[str] = []
    for thread in open_threads:
        quest_id = _thread_quest_id(thread)
        active_quest_ids.append(quest_id)
        quest = _find_record(quests, record_id=quest_id, title=thread.title)
        if not quest:
            quest = {
                'id': quest_id,
                'title': thread.title,
                'status': 'active',
                'summary': '',
                'stage': '',
                'objectives': [],
                'relatedNpcIds': [],
                'relatedLocationIds': [],
                'importantItemIds': [],
                'flags': {},
                'metadata': {'source': 'canon_projection'},
                'createdAtTurn': thread.origin_turn_id,
                'completedAtTurn': None,
            }
            quests.append(quest)
        quest['id'] = quest_id
        quest['title'] = thread.title
        quest['status'] = 'active'
        if thread.summary:
            quest['summary'] = thread.summary
        if thread.last_touched_turn_id:
            quest['updatedAtTurn'] = thread.last_touched_turn_id

    stale_active_ids = set(_string_list(scene.get('activeQuestIds'))) - set(active_quest_ids)
    for quest in quests:
        quest_id = _text(quest.get('id'))
        status = _text(quest.get('status')).lower()
        if quest_id in stale_active_ids and status in ACTIVE_QUEST_STATUSES:
            quest['status'] = 'completed'
            quest['completedAtTurn'] = quest.get('completedAtTurn') or quest.get('updatedAtTurn')

    scene['activeQuestIds'] = active_quest_ids
    return True


def _sync_session_snapshot(
    session_id: int,
    location_fact: StoryFact | None,
    open_threads: list[StoryThread],
    *,
    fallback_location: str = '',
) -> None:
    session_obj = db.session.get(Session, session_id)
    if not session_obj:
        return
    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    if not isinstance(snapshot, dict):
        return

    changed = False
    changed = _sync_snapshot_location(snapshot, location_fact, fallback_location=fallback_location) or changed
    changed = _sync_snapshot_quests(snapshot, open_threads) or changed
    if changed:
        session_obj.state_snapshot = safe_json_dumps(snapshot, {})


def refresh_session_projection(session_id: int, campaign: Campaign, triggered_segments: list[dict] | None = None):
    triggered_segments = triggered_segments or []
    state = get_or_create_session_state(session_id, campaign)

    location_fact = _latest_location_fact(campaign.campaign_id)
    if location_fact and location_fact.value_text:
        state.current_location = location_fact.value_text
    elif not state.current_location or not _valid_location_label(state.current_location):
        fallback_location = campaign.location or _session_snapshot_location(session_id)
        state.current_location = fallback_location if _valid_location_label(fallback_location) else campaign.location

    open_threads = (
        StoryThread.query.filter(
            StoryThread.campaign_id == campaign.campaign_id,
            StoryThread.status.in_(ACTIVE_THREAD_STATUSES),
        )
        .order_by(StoryThread.priority.desc(), StoryThread.updated_at.desc(), StoryThread.thread_id.desc())
        .all()
    )
    if open_threads:
        state.current_quest = ' | '.join(thread.title for thread in open_threads[:3])
    else:
        state.current_quest = campaign.current_quest
    _sync_session_snapshot(session_id, location_fact, open_threads, fallback_location=state.current_location or campaign.location or '')

    active_segments = safe_json_loads(state.active_segments, [])
    active_segments = active_segments if isinstance(active_segments, list) else []
    for segment_payload in triggered_segments:
        if not any(existing.get('segment_id') == segment_payload.get('segment_id') for existing in active_segments):
            active_segments.append(segment_payload)
    state.active_segments = safe_json_dumps(active_segments, [])
    state.updated_at = utc_now()
    return state
