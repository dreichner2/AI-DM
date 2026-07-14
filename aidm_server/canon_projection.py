"""Session projection updates derived from accepted canon and turns."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import case

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
TERMINAL_QUEST_STATUSES = {'completed', 'failed', 'abandoned'}


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
            StoryFact.fact_status.in_(('accepted', 'superseded')),
        )
        .order_by(
            case((StoryFact.source_turn_id.is_(None), 1), else_=0).asc(),
            StoryFact.source_turn_id.desc(),
            StoryFact.fact_id.desc(),
        )
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


def _id_key(value: Any) -> str:
    return _slug(value)


def _record_source(record: dict) -> str:
    metadata = record.get('metadata') if isinstance(record.get('metadata'), dict) else {}
    return _text(record.get('source') or metadata.get('source'))


def _campaign_pack(snapshot: dict) -> dict:
    pack = snapshot.get('campaignPack')
    return pack if isinstance(pack, dict) else {}


def _campaign_pack_rules(snapshot: dict) -> dict:
    pack = _campaign_pack(snapshot)
    rules = pack.get('activeDirectorRules') if isinstance(pack.get('activeDirectorRules'), dict) else None
    if rules is None:
        rules = pack.get('directorRules') if isinstance(pack.get('directorRules'), dict) else {}
    return rules


def _campaign_pack_main_quest_policy(snapshot: dict) -> str:
    rules = _campaign_pack_rules(snapshot)
    return _text(rules.get('mainQuestGeneration') or rules.get('main_quest_generation') or '').casefold()


def _record_is_campaign_pack(record: dict, pack_id: str = '') -> bool:
    if not isinstance(record, dict):
        return False
    if _record_source(record) == 'campaign_pack':
        return True
    return bool(pack_id and _text(record.get('packId') or record.get('pack_id')) == pack_id)


def _active_pack_quest_ids(snapshot: dict, quests: list[dict]) -> list[str]:
    pack = _campaign_pack(snapshot)
    pack_id = _text(pack.get('packId') or pack.get('pack_id'))
    pack_quest_ids = {
        _id_key(quest.get('id') or quest.get('questId'))
        for quest in quests
        if _record_is_campaign_pack(quest, pack_id)
    }
    catalog = pack.get('catalog') if isinstance(pack.get('catalog'), dict) else {}
    for quest in catalog.get('quests') or []:
        if isinstance(quest, dict):
            pack_quest_ids.add(_id_key(quest.get('id') or quest.get('questId')))

    scene = _ensure_scene(snapshot)
    active_ids = [
        quest_id
        for quest_id in _string_list(scene.get('activeQuestIds'))
        if _id_key(quest_id) in pack_quest_ids
    ]
    if active_ids:
        return active_ids

    quest_by_key = {_id_key(quest.get('id') or quest.get('questId')): quest for quest in quests}
    active_ids = [
        _text(quest.get('id') or quest.get('questId'))
        for quest in quests
        if _record_is_campaign_pack(quest, pack_id)
        and _text(quest.get('id') or quest.get('questId'))
        and _text(quest.get('status')).lower() in ACTIVE_QUEST_STATUSES
    ]
    if active_ids:
        return active_ids

    starting_quest_id = _text(pack.get('startingQuestId') or pack.get('starting_quest_id'))
    if starting_quest_id and (_id_key(starting_quest_id) in pack_quest_ids or _id_key(starting_quest_id) in quest_by_key):
        return [starting_quest_id]
    return []


def _quest_is_canon_projection(quest: dict) -> bool:
    metadata = quest.get('metadata') if isinstance(quest.get('metadata'), dict) else {}
    return _text(metadata.get('source') or quest.get('source')) == 'canon_projection'


def _sync_pack_only_snapshot_quests(snapshot: dict, open_threads: list[StoryThread]) -> bool:
    scene = _ensure_scene(snapshot)
    quests = _snapshot_records(snapshot, 'quests')
    active_pack_ids = _active_pack_quest_ids(snapshot, quests)
    changed = scene.get('activeQuestIds') != active_pack_ids
    scene['activeQuestIds'] = active_pack_ids

    open_thread_ids = {_id_key(_thread_quest_id(thread)) for thread in open_threads}
    for quest in quests:
        if not _quest_is_canon_projection(quest):
            continue
        quest_id = _id_key(quest.get('id') or quest.get('questId'))
        if quest_id not in open_thread_ids:
            continue
        if _text(quest.get('status')).lower() in ACTIVE_QUEST_STATUSES:
            quest['status'] = 'noted'
            changed = True
        metadata = quest.setdefault('metadata', {})
        if isinstance(metadata, dict) and metadata.get('questType') != 'story_thread':
            metadata['questType'] = 'story_thread'
            changed = True
    return changed


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


def _session_pack_main_quest_policy(session_id: int) -> str:
    session_obj = db.session.get(Session, session_id)
    if not session_obj:
        return ''
    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    if not isinstance(snapshot, dict):
        return ''
    return _campaign_pack_main_quest_policy(snapshot)


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
    if _campaign_pack_main_quest_policy(snapshot) == 'pack_only':
        return _sync_pack_only_snapshot_quests(snapshot, open_threads)
    if not open_threads:
        return False

    scene = _ensure_scene(snapshot)
    quests = _snapshot_records(snapshot, 'quests')
    # Story threads are remembered narrative hooks. They may create and update
    # their own projected journal records, but they must not reactivate,
    # complete, or otherwise take ownership of mechanically-authored quests.
    canon_active_ids: list[str] = []
    for thread in open_threads:
        quest_id = _thread_quest_id(thread)
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
        elif _record_source(quest) != 'canon_projection':
            # A title/id collision with authoritative quest state does not
            # transfer authority to canon. Preserve its current mechanics.
            continue

        status = _text(quest.get('status')).lower()
        if status in TERMINAL_QUEST_STATUSES or (status and status not in ACTIVE_QUEST_STATUSES):
            continue

        canon_active_ids.append(quest_id)
        quest['id'] = quest_id
        quest['title'] = thread.title
        quest['status'] = 'active'
        if thread.summary:
            quest['summary'] = thread.summary
        if thread.last_touched_turn_id:
            quest['updatedAtTurn'] = thread.last_touched_turn_id

    prior_active_ids = _string_list(scene.get('activeQuestIds'))
    stale_canon_ids = set(prior_active_ids) - set(canon_active_ids)
    for quest in quests:
        quest_id = _text(quest.get('id'))
        status = _text(quest.get('status')).lower()
        if (
            quest_id in stale_canon_ids
            and _record_source(quest) == 'canon_projection'
            and status in ACTIVE_QUEST_STATUSES
        ):
            quest['status'] = 'completed'
            quest['completedAtTurn'] = quest.get('completedAtTurn') or quest.get('updatedAtTurn')

    mechanical_active_ids: list[str] = []
    quest_by_id = {_text(quest.get('id')): quest for quest in quests if _text(quest.get('id'))}
    for quest_id in prior_active_ids:
        quest = quest_by_id.get(quest_id)
        if (
            quest
            and _record_source(quest) != 'canon_projection'
            and _text(quest.get('status')).lower() in ACTIVE_QUEST_STATUSES
        ):
            mechanical_active_ids.append(quest_id)
    scene['activeQuestIds'] = list(dict.fromkeys([*mechanical_active_ids, *canon_active_ids]))
    return True


def _sync_session_snapshot(
    session_id: int,
    location_fact: StoryFact | None,
    open_threads: list[StoryThread],
    *,
    fallback_location: str = '',
    sync_location: bool = True,
) -> None:
    session_obj = db.session.get(Session, session_id)
    if not session_obj:
        return
    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    if not isinstance(snapshot, dict):
        return

    changed = False
    if sync_location:
        changed = _sync_snapshot_location(snapshot, location_fact, fallback_location=fallback_location) or changed
    changed = _sync_snapshot_quests(snapshot, open_threads) or changed
    if changed:
        session_obj.state_snapshot = safe_json_dumps(snapshot, {})


def refresh_session_projection(session_id: int, campaign: Campaign, triggered_segments: list[dict] | None = None):
    triggered_segments = triggered_segments or []
    state = get_or_create_session_state(session_id, campaign)

    location_fact = _latest_location_fact(campaign.campaign_id)
    session_obj = db.session.get(Session, session_id)
    snapshot = safe_json_loads(session_obj.state_snapshot, {}) if session_obj else {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    scene_location = _text(scene.get('name') or scene.get('locationId'))
    scene_turn_id = _int(scene.get('updatedAtTurn') or scene.get('updated_at_turn'))
    # Structured live scene state is authoritative. Canon is a projection of
    # remembered narrative and may only initialize/repair location state, or
    # supply a genuinely newer legacy location fact. It must never move a
    # party backward after a validated travel action.
    fact_can_update_scene = bool(
        location_fact
        and location_fact.value_text
        and (not _valid_location_label(scene_location) or not scene_turn_id)
    )
    if fact_can_update_scene:
        state.current_location = location_fact.value_text
    elif _valid_location_label(scene_location):
        state.current_location = scene_location
    else:
        fallback_location = _session_snapshot_location(session_id) or state.current_location or campaign.location
        state.current_location = fallback_location if _valid_location_label(fallback_location) else campaign.location

    open_threads = (
        StoryThread.query.filter(
            StoryThread.campaign_id == campaign.campaign_id,
            StoryThread.status.in_(ACTIVE_THREAD_STATUSES),
        )
        .order_by(StoryThread.priority.desc(), StoryThread.updated_at.desc(), StoryThread.thread_id.desc())
        .all()
    )
    if open_threads and _session_pack_main_quest_policy(session_id) != 'pack_only':
        state.current_quest = ' | '.join(thread.title for thread in open_threads[:3])
    else:
        state.current_quest = campaign.current_quest
    _sync_session_snapshot(
        session_id,
        location_fact if fact_can_update_scene else None,
        open_threads,
        fallback_location=state.current_location or campaign.location or '',
        sync_location=fact_can_update_scene or not _valid_location_label(scene_location),
    )

    active_segments = safe_json_loads(state.active_segments, [])
    active_segments = active_segments if isinstance(active_segments, list) else []
    for segment_payload in triggered_segments:
        if not any(existing.get('segment_id') == segment_payload.get('segment_id') for existing in active_segments):
            active_segments.append(segment_payload)
    state.active_segments = safe_json_dumps(active_segments, [])
    state.updated_at = utc_now()
    return state
