from __future__ import annotations

from typing import Any

from aidm_server.models import (
    CampaignPackCheckpointProgress,
    CampaignPackProgressEvent,
    CampaignPackSession,
    DmTurn,
    Session,
    SessionState,
    safe_json_loads,
)
from aidm_server.services.campaign_pack_graph import (
    AUTHORING_COLLECTIONS,
    build_checkpoint_graph,
    checkpoint_ids_for_record,
    collection_records,
    first,
    hidden_to_players,
    humanize_identifier,
    id_key,
    is_terminal_checkpoint,
    record_id,
    record_title,
    related_record_ids_for_checkpoints,
    string_list,
    text,
    unique_ids,
    visible_at_start,
)


TERMINAL_STATUSES = {'completed', 'failed', 'skipped'}
VISITED_STATUSES = {*TERMINAL_STATUSES, 'active'}
COMMON_UNDISCOVERED_COLLECTIONS = (
    'locations',
    'npcs',
    'quests',
    'clues',
    'encounters',
    'enemies',
    'factions',
    'maps',
    'handouts',
    'lore',
)


def session_recap_payload(session_obj: Session) -> dict[str, Any]:
    snapshot = _session_snapshot(session_obj)
    state_record = SessionState.query.filter_by(session_id=session_obj.session_id).first()
    state_payload = _session_state_payload(snapshot, state_record)

    snapshot_recap = _snapshot_recap(snapshot)
    if snapshot_recap:
        recap = snapshot_recap
        source = 'state_snapshot'
    elif text(state_record.rolling_summary if state_record else ''):
        recap = text(state_record.rolling_summary)
        source = 'session_state'
    else:
        recap = _recent_turn_recap(session_obj.session_id)
        source = 'recent_turns' if recap else 'empty'

    return {
        'session_id': session_obj.session_id,
        'campaign_id': session_obj.campaign_id,
        'sessionId': session_obj.session_id,
        'campaignId': session_obj.campaign_id,
        'recap': recap,
        'source': source,
        'generated': False,
        'state': state_payload,
    }


def campaign_pack_commentary_payload(session_obj: Session) -> dict[str, Any]:
    snapshot = _session_snapshot(session_obj)
    pack = snapshot.get('campaignPack') if isinstance(snapshot.get('campaignPack'), dict) else {}
    pack_session = CampaignPackSession.query.filter_by(session_id=session_obj.session_id).first()
    checkpoints = collection_records(pack, 'checkpoints')
    pack_id = text(first(pack, 'packId', 'pack_id') or (pack_session.pack_id if pack_session else ''))
    if not pack_id or not checkpoints:
        return {
            'enabled': False,
            'sessionId': session_obj.session_id,
            'campaignId': session_obj.campaign_id,
            'pack': None,
            'routeTaken': [],
            'roadsNotTaken': [],
            'alternateEndings': [],
            'undiscoveredRecords': {collection: [] for collection in COMMON_UNDISCOVERED_COLLECTIONS},
            'summary': {
                'routeTakenCount': 0,
                'roadsNotTakenCount': 0,
                'alternateEndingsCount': 0,
                'undiscoveredRecordsCount': 0,
            },
            'commentary': [],
        }

    graph = build_checkpoint_graph(pack)
    checkpoint_by_id = {record_id(checkpoint): checkpoint for checkpoint in checkpoints if record_id(checkpoint)}
    status_by_id = _status_by_checkpoint_id(pack=pack, snapshot=snapshot, pack_session=pack_session)
    route_taken = _route_taken(
        checkpoints=checkpoints,
        checkpoint_by_id=checkpoint_by_id,
        status_by_id=status_by_id,
        session_id=session_obj.session_id,
    )
    roads_not_taken = _roads_not_taken(
        graph=graph,
        checkpoint_by_id=checkpoint_by_id,
        status_by_id=status_by_id,
    )
    alternate_endings = _alternate_endings(checkpoints=checkpoints, status_by_id=status_by_id)
    undiscovered_records = _undiscovered_records(
        pack=pack,
        snapshot=snapshot,
        visited_checkpoint_ids=[item['checkpointId'] for item in route_taken],
    )
    undiscovered_count = sum(len(records) for records in undiscovered_records.values())
    summary = {
        'routeTakenCount': len(route_taken),
        'roadsNotTakenCount': len(roads_not_taken),
        'alternateEndingsCount': len(alternate_endings),
        'undiscoveredRecordsCount': undiscovered_count,
    }
    return {
        'enabled': True,
        'sessionId': session_obj.session_id,
        'campaignId': session_obj.campaign_id,
        'pack': {
            'packId': pack_id,
            'title': text(first(pack, 'title', 'name') or (pack_session.pack_title if pack_session else '')),
            'version': text(first(pack, 'version') or (pack_session.pack_version if pack_session else '')),
            'schemaVersion': text(first(pack, 'schemaVersion', 'schema_version') or '1'),
        },
        'progress': {
            'activeCheckpointId': _active_checkpoint_id(status_by_id),
            'completedCheckpointIds': _ids_with_status(status_by_id, 'completed'),
            'skippedCheckpointIds': _ids_with_status(status_by_id, 'skipped'),
            'failedCheckpointIds': _ids_with_status(status_by_id, 'failed'),
            'statusByCheckpointId': status_by_id,
            'progressRevision': _progress_revision(pack, snapshot),
        },
        'graph': graph,
        'routeTaken': route_taken,
        'roadsNotTaken': roads_not_taken,
        'alternateEndings': alternate_endings,
        'undiscoveredRecords': undiscovered_records,
        'summary': summary,
        'commentary': _commentary_notes(route_taken, roads_not_taken, alternate_endings, undiscovered_count),
    }


def build_session_commentary(session_obj: Session) -> dict[str, Any]:
    """Backward-compatible alias for older call sites."""

    return campaign_pack_commentary_payload(session_obj)


def _session_snapshot(session_obj: Session) -> dict[str, Any]:
    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    return snapshot if isinstance(snapshot, dict) else {}


def _snapshot_recap(snapshot: dict[str, Any]) -> str:
    for key in ('recap', 'sessionRecap', 'session_recap', 'previouslyOn', 'previously_on'):
        value = text(snapshot.get(key))
        if value:
            return value
    return ''


def _session_state_payload(snapshot: dict[str, Any], state_record: SessionState | None) -> dict[str, Any]:
    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    return {
        'rolling_summary': text(state_record.rolling_summary if state_record else ''),
        'current_location': text(state_record.current_location if state_record else '')
        or text(first(scene, 'name', 'title', 'locationName', 'location_name')),
        'current_quest': text(state_record.current_quest if state_record else ''),
        'updated_at': state_record.updated_at.isoformat() if state_record and state_record.updated_at else None,
    }


def _recent_turn_recap(session_id: int) -> str:
    turns = (
        DmTurn.query.filter_by(session_id=session_id)
        .order_by(DmTurn.created_at.desc(), DmTurn.turn_id.desc())
        .limit(3)
        .all()
    )
    entries: list[str] = []
    for turn in reversed(turns):
        player_input = text(turn.player_input)
        dm_output = text(turn.dm_output)
        if player_input:
            entries.append(f'Player: {player_input}')
        if dm_output:
            entries.append(f'DM: {dm_output}')
    return '\n'.join(entries)


def _status_by_checkpoint_id(
    *,
    pack: dict[str, Any],
    snapshot: dict[str, Any],
    pack_session: CampaignPackSession | None,
) -> dict[str, str]:
    flags = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}
    statuses: dict[str, str] = {}
    for status, keys in {
        'completed': ('completedCheckpointIds', 'completed_checkpoint_ids', 'campaignPackCompletedCheckpointIds'),
        'skipped': ('skippedCheckpointIds', 'skipped_checkpoint_ids', 'campaignPackSkippedCheckpointIds'),
        'failed': ('failedCheckpointIds', 'failed_checkpoint_ids', 'campaignPackFailedCheckpointIds'),
    }.items():
        values: list[str] = []
        for key in keys:
            values.extend(string_list(pack.get(key)))
            values.extend(string_list(flags.get(key)))
        for checkpoint_id in unique_ids(values):
            statuses[checkpoint_id] = status

    active_id = text(
        first(pack, 'activeCheckpointId', 'active_checkpoint_id', 'currentCheckpointId', 'current_checkpoint_id')
        or first(flags, 'campaignPackActiveCheckpointId', 'activeCheckpointId')
    )
    if active_id:
        statuses[active_id] = 'active'

    if pack_session:
        rows = (
            CampaignPackCheckpointProgress.query.filter_by(
                campaign_pack_session_id=pack_session.campaign_pack_session_id,
            )
            .order_by(CampaignPackCheckpointProgress.sort_order.asc(), CampaignPackCheckpointProgress.checkpoint_progress_id.asc())
            .all()
        )
        for row in rows:
            status = text(row.status) or 'open'
            if status != 'open':
                statuses[row.checkpoint_id] = status
        if text(pack_session.active_checkpoint_id):
            statuses[text(pack_session.active_checkpoint_id)] = 'active'
    return statuses


def _route_taken(
    *,
    checkpoints: list[dict[str, Any]],
    checkpoint_by_id: dict[str, dict[str, Any]],
    status_by_id: dict[str, str],
    session_id: int,
) -> list[dict[str, Any]]:
    route: list[dict[str, Any]] = []
    seen: set[str] = set()

    events = (
        CampaignPackProgressEvent.query.filter_by(session_id=session_id)
        .order_by(CampaignPackProgressEvent.created_at.asc(), CampaignPackProgressEvent.progress_event_id.asc())
        .all()
    )
    for event in events:
        checkpoint_id = text(event.to_checkpoint_id or event.from_checkpoint_id)
        if not checkpoint_id:
            continue
        status = status_by_id.get(checkpoint_id) or text(event.action) or 'visited'
        if status not in VISITED_STATUSES and status != 'visited':
            continue
        _append_route_item(route, seen, checkpoint_id, status, checkpoint_by_id.get(checkpoint_id), event.reason)

    for checkpoint in checkpoints:
        checkpoint_id = record_id(checkpoint)
        status = status_by_id.get(checkpoint_id)
        if checkpoint_id and status in VISITED_STATUSES:
            _append_route_item(route, seen, checkpoint_id, status, checkpoint, None)
    return route


def _append_route_item(
    route: list[dict[str, Any]],
    seen: set[str],
    checkpoint_id: str,
    status: str,
    checkpoint: dict[str, Any] | None,
    reason: str | None,
) -> None:
    key = id_key(checkpoint_id)
    if key in seen:
        return
    checkpoint = checkpoint if isinstance(checkpoint, dict) else {}
    route.append(
        {
            'id': checkpoint_id,
            'checkpointId': checkpoint_id,
            'title': record_title(checkpoint) or humanize_identifier(checkpoint_id),
            'status': status,
            'summary': _record_summary(checkpoint),
            'reason': reason,
        }
    )
    seen.add(key)


def _roads_not_taken(
    *,
    graph: dict[str, Any],
    checkpoint_by_id: dict[str, dict[str, Any]],
    status_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    outgoing_counts: dict[str, int] = {}
    for edge in graph.get('edges') or []:
        if not isinstance(edge, dict):
            continue
        source_id = text(edge.get('from'))
        if source_id:
            outgoing_counts[source_id] = outgoing_counts.get(source_id, 0) + 1

    roads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for edge in graph.get('edges') or []:
        if not isinstance(edge, dict):
            continue
        source_id = text(edge.get('from'))
        target_id = text(edge.get('to'))
        edge_type = text(edge.get('type')) or 'next'
        source_status = status_by_id.get(source_id)
        target_status = status_by_id.get(target_id)
        if source_status not in TERMINAL_STATUSES or target_status in VISITED_STATUSES:
            continue
        if edge_type == 'next' and outgoing_counts.get(source_id, 0) <= 1:
            continue
        key = f'{source_id}:{target_id}:{edge_type}'
        if key in seen:
            continue
        checkpoint = checkpoint_by_id.get(target_id, {})
        source = checkpoint_by_id.get(source_id, {})
        roads.append(
            {
                'id': target_id,
                'checkpointId': target_id,
                'title': record_title(checkpoint) or humanize_identifier(target_id),
                'summary': _record_summary(checkpoint),
                'edgeType': edge_type,
                'fromCheckpointId': source_id,
                'fromTitle': record_title(source) or humanize_identifier(source_id),
            }
        )
        seen.add(key)
    return roads


def _alternate_endings(
    *,
    checkpoints: list[dict[str, Any]],
    status_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    endings: list[dict[str, Any]] = []
    for checkpoint in checkpoints:
        checkpoint_id = record_id(checkpoint)
        if not checkpoint_id or status_by_id.get(checkpoint_id) in VISITED_STATUSES:
            continue
        next_ids = string_list(
            first(
                checkpoint,
                'nextCheckpointId',
                'nextCheckpointIds',
                'next_checkpoint_id',
                'next_checkpoint_ids',
                'alternateCheckpointId',
                'alternateCheckpointIds',
                'alternate_checkpoint_id',
                'alternate_checkpoint_ids',
                'failureCheckpointId',
                'failureCheckpointIds',
                'failure_checkpoint_id',
                'failure_checkpoint_ids',
            )
        )
        if is_terminal_checkpoint(checkpoint) or not next_ids:
            endings.append(
                {
                    'id': checkpoint_id,
                    'checkpointId': checkpoint_id,
                    'title': record_title(checkpoint) or humanize_identifier(checkpoint_id),
                    'summary': _record_summary(checkpoint),
                }
            )
    return endings


def _undiscovered_records(
    *,
    pack: dict[str, Any],
    snapshot: dict[str, Any],
    visited_checkpoint_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    known = _known_record_keys(pack, snapshot, visited_checkpoint_ids)
    collections = [collection for collection in AUTHORING_COLLECTIONS if collection != 'checkpoints']
    for collection in COMMON_UNDISCOVERED_COLLECTIONS:
        if collection not in collections:
            collections.append(collection)

    result: dict[str, list[dict[str, Any]]] = {collection: [] for collection in COMMON_UNDISCOVERED_COLLECTIONS}
    for collection in collections:
        result.setdefault(collection, [])
        for record in collection_records(pack, collection):
            item_id = record_id(record)
            if not item_id or id_key(item_id) in known.get(collection, set()):
                continue
            result[collection].append(_record_payload(collection, record, pack))
    return result


def _known_record_keys(
    pack: dict[str, Any],
    snapshot: dict[str, Any],
    visited_checkpoint_ids: list[str],
) -> dict[str, set[str]]:
    known: dict[str, set[str]] = {collection: set() for collection in AUTHORING_COLLECTIONS}
    for collection in AUTHORING_COLLECTIONS:
        for record in collection_records(pack, collection):
            if visible_at_start(record):
                _add_known(known, collection, record_id(record))

    related = related_record_ids_for_checkpoints(pack, visited_checkpoint_ids)
    for collection, values in related.items():
        for value in values:
            _add_known(known, collection, value)

    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    for value in string_list(first(scene, 'locationId', 'locationIds', 'location_id', 'location_ids')):
        _add_known(known, 'locations', value)
    for value in string_list(first(scene, 'activeNpcIds', 'active_npc_ids', 'npcIds', 'npc_ids')):
        _add_known(known, 'npcs', value)
    for value in string_list(first(scene, 'activeQuestIds', 'active_quest_ids', 'questIds', 'quest_ids')):
        _add_known(known, 'quests', value)

    for snapshot_key, collection in {
        'locations': 'locations',
        'knownLocations': 'locations',
        'known_locations': 'locations',
        'npcs': 'npcs',
        'knownNpcs': 'npcs',
        'known_npcs': 'npcs',
        'quests': 'quests',
        'activeQuests': 'quests',
        'active_quests': 'quests',
        'knownClues': 'clues',
        'known_clues': 'clues',
        'encounters': 'encounters',
    }.items():
        for record in _snapshot_records(snapshot.get(snapshot_key)):
            _add_known(known, collection, record_id(record))

    flags = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}
    for value in string_list(
        first(flags, 'campaignPackCompletedEncounterIds', 'completedEncounterIds', 'completed_encounter_ids')
    ):
        _add_known(known, 'encounters', value)
    return known


def _snapshot_records(value: Any) -> list[dict[str, Any]]:
    return [record for record in value if isinstance(record, dict)] if isinstance(value, list) else []


def _add_known(known: dict[str, set[str]], collection: str, value: str | None) -> None:
    clean_value = text(value)
    if clean_value:
        known.setdefault(collection, set()).add(id_key(clean_value))


def _record_payload(collection: str, record: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    item_id = record_id(record)
    checkpoint_ids = _record_checkpoint_ids(collection, record, pack)
    return {
        'id': item_id,
        'title': record_title(record) or humanize_identifier(item_id),
        'summary': _record_summary(record),
        'hidden': hidden_to_players(record),
        'checkpointIds': checkpoint_ids,
    }


def _record_checkpoint_ids(collection: str, record: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    return checkpoint_ids_for_record(pack, collection, record)


def _record_summary(record: dict[str, Any]) -> str:
    return text(
        first(
            record,
            'playerSummary',
            'player_summary',
            'publicSummary',
            'public_summary',
            'summary',
            'description',
            'gmNotes',
            'gm_notes',
        )
    )


def _active_checkpoint_id(status_by_id: dict[str, str]) -> str | None:
    return next((checkpoint_id for checkpoint_id, status in status_by_id.items() if status == 'active'), None)


def _ids_with_status(status_by_id: dict[str, str], wanted_status: str) -> list[str]:
    return [checkpoint_id for checkpoint_id, status in status_by_id.items() if status == wanted_status]


def _progress_revision(pack: dict[str, Any], snapshot: dict[str, Any]) -> int:
    flags = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}
    for value in (
        first(pack, 'progressRevision', 'progress_revision'),
        first(flags, 'campaignPackProgressRevision', 'progressRevision', 'progress_revision'),
    ):
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def _commentary_notes(
    route_taken: list[dict[str, Any]],
    roads_not_taken: list[dict[str, Any]],
    alternate_endings: list[dict[str, Any]],
    undiscovered_count: int,
) -> list[str]:
    notes = [
        f"Route taken: {len(route_taken)} checkpoint{'s' if len(route_taken) != 1 else ''} reached.",
        f"Roads not taken: {len(roads_not_taken)} branch{'es' if len(roads_not_taken) != 1 else ''} remain off the table.",
    ]
    if alternate_endings:
        notes.append(
            f"Alternate endings: {len(alternate_endings)} unresolved terminal beat{'s' if len(alternate_endings) != 1 else ''}."
        )
    notes.append(
        f"Undiscovered records: {undiscovered_count} item{'s' if undiscovered_count != 1 else ''} still hidden from play."
    )
    return notes
