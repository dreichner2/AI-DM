from __future__ import annotations

import re
from typing import Any


AUTHORING_COLLECTIONS = (
    'locations',
    'npcs',
    'quests',
    'segments',
    'checkpoints',
    'encounters',
    'enemies',
    'clues',
    'factions',
    'maps',
    'handouts',
    'lore',
)

CHECKPOINT_REFERENCE_FIELDS = {
    'locations': ('locationId', 'locationIds', 'location_id', 'location_ids', 'locations'),
    'npcs': ('npcId', 'npcIds', 'npc_id', 'npc_ids', 'npcs'),
    'quests': ('questId', 'questIds', 'quest_id', 'quest_ids', 'quests'),
    'segments': ('segmentId', 'segmentIds', 'segment_id', 'segment_ids', 'segments'),
    'encounters': ('encounterId', 'encounterIds', 'encounter_id', 'encounter_ids', 'encounters'),
    'clues': ('clueId', 'clueIds', 'clue_id', 'clue_ids', 'clues'),
    'factions': ('factionId', 'factionIds', 'faction_id', 'faction_ids', 'factions'),
    'maps': ('mapId', 'mapIds', 'map_id', 'map_ids', 'maps'),
    'handouts': ('handoutId', 'handoutIds', 'handout_id', 'handout_ids', 'handouts'),
    'lore': ('loreId', 'loreIds', 'lore_id', 'lore_ids', 'lore'),
}

EDGE_FIELDS = (
    ('next', ('nextCheckpointId', 'nextCheckpointIds', 'next_checkpoint_id', 'next_checkpoint_ids')),
    (
        'alternate',
        (
            'alternateCheckpointId',
            'alternateCheckpointIds',
            'alternate_checkpoint_id',
            'alternate_checkpoint_ids',
            'alternateRouteCheckpointIds',
            'alternate_route_checkpoint_ids',
        ),
    ),
    (
        'failure',
        (
            'failureCheckpointId',
            'failureCheckpointIds',
            'failure_checkpoint_id',
            'failure_checkpoint_ids',
            'failedCheckpointIds',
            'failed_checkpoint_ids',
            'onFailCheckpointIds',
            'on_fail_checkpoint_ids',
        ),
    ),
    ('rejoin', ('rejoinTargetCheckpointId', 'rejoin_target_checkpoint_id')),
)


def build_checkpoint_graph(pack: dict[str, Any]) -> dict[str, Any]:
    checkpoints = collection_records(pack, 'checkpoints')
    node_ids = [record_id(checkpoint) for checkpoint in checkpoints if record_id(checkpoint)]
    edges: list[dict[str, str]] = []
    for checkpoint in checkpoints:
        source = record_id(checkpoint)
        if not source:
            continue
        for edge_type, keys in EDGE_FIELDS:
            for target in string_list(first(checkpoint, *keys)):
                edges.append({'from': source, 'to': target, 'type': edge_type})

    start_id = starting_checkpoint_id(pack, node_ids)
    reachable = sorted(reachable_checkpoint_ids(pack, checkpoints))
    nodes = [
        {
            'id': checkpoint_id,
            'title': record_title(checkpoint) or humanize_identifier(checkpoint_id),
            'terminal': is_terminal_checkpoint(checkpoint),
            'sortOrder': index,
        }
        for index, checkpoint in enumerate(checkpoints)
        for checkpoint_id in [record_id(checkpoint)]
        if checkpoint_id
    ]
    return {
        'startCheckpointId': start_id,
        'nodes': nodes,
        'nodeIds': node_ids,
        'edges': edges,
        'reachable': reachable,
    }


def starting_checkpoint_id(pack: dict[str, Any], node_ids: list[str] | None = None) -> str | None:
    ordered_ids = node_ids or [record_id(checkpoint) for checkpoint in collection_records(pack, 'checkpoints') if record_id(checkpoint)]
    known_ids = set(ordered_ids)
    starting_state = first(pack, 'startingState', 'starting_state')
    starting_state = starting_state if isinstance(starting_state, dict) else {}
    checkpoint_id = text(
        first(starting_state, 'checkpointId', 'checkpoint_id', 'startingCheckpointId', 'starting_checkpoint_id')
        or first(pack, 'startingCheckpointId', 'starting_checkpoint_id')
    )
    if checkpoint_id and (not known_ids or checkpoint_id in known_ids):
        return checkpoint_id
    return ordered_ids[0] if ordered_ids else None


def reachable_checkpoint_ids(pack: dict[str, Any], checkpoints: list[dict[str, Any]] | None = None) -> set[str]:
    checkpoints = checkpoints if checkpoints is not None else collection_records(pack, 'checkpoints')
    by_id = {record_id(checkpoint): checkpoint for checkpoint in checkpoints if record_id(checkpoint)}
    if not by_id:
        return set()
    start_id = starting_checkpoint_id(pack, list(by_id))
    if not start_id:
        return set()

    reachable: set[str] = set()
    stack = [start_id]
    while stack:
        checkpoint_id = stack.pop()
        if checkpoint_id in reachable or checkpoint_id not in by_id:
            continue
        reachable.add(checkpoint_id)
        checkpoint = by_id[checkpoint_id]
        for edge_type, keys in EDGE_FIELDS:
            if edge_type == 'rejoin':
                continue
            stack.extend(string_list(first(checkpoint, *keys)))
    return reachable


def checkpoint_references(checkpoint: dict[str, Any]) -> dict[str, list[str]]:
    return {
        collection: string_list(first(checkpoint, *keys))
        for collection, keys in CHECKPOINT_REFERENCE_FIELDS.items()
    }


def related_record_ids_for_checkpoints(pack: dict[str, Any], checkpoint_ids: list[str]) -> dict[str, list[str]]:
    wanted = {id_key(checkpoint_id) for checkpoint_id in checkpoint_ids if text(checkpoint_id)}
    related: dict[str, list[str]] = {collection: [] for collection in AUTHORING_COLLECTIONS}
    encounter_ids: list[str] = []
    for checkpoint in collection_records(pack, 'checkpoints'):
        checkpoint_id = record_id(checkpoint)
        if id_key(checkpoint_id) not in wanted:
            continue
        for collection, values in checkpoint_references(checkpoint).items():
            related[collection] = unique_ids([*related.get(collection, []), *values])
        if checkpoint_id:
            related['checkpoints'] = unique_ids([*related.get('checkpoints', []), checkpoint_id])
        encounter_ids.extend(related.get('encounters', []))

    encounter_wanted = {id_key(encounter_id) for encounter_id in encounter_ids}
    for encounter in collection_records(pack, 'encounters'):
        encounter_id = record_id(encounter)
        checkpoint_refs = string_list(first(encounter, 'checkpointId', 'checkpointIds', 'checkpoint_id', 'checkpoint_ids'))
        if id_key(encounter_id) in encounter_wanted or wanted.intersection(id_key(value) for value in checkpoint_refs):
            enemy_ids = string_list(first(encounter, 'enemyId', 'enemyIds', 'enemy_id', 'enemy_ids'))
            enemy_groups = records(first(encounter, 'enemyGroups', 'enemy_groups'))
            for enemy_group in enemy_groups:
                enemy_ids.extend(string_list(first(enemy_group, 'enemyId', 'enemy_id', 'id')))
            related['encounters'] = unique_ids([*related.get('encounters', []), encounter_id])
            related['enemies'] = unique_ids([*related.get('enemies', []), *enemy_ids])

    for collection in AUTHORING_COLLECTIONS:
        if collection in {'checkpoints', 'encounters', 'enemies'}:
            continue
        for record in collection_records(pack, collection):
            checkpoint_refs = string_list(first(record, 'checkpointId', 'checkpointIds', 'checkpoint_id', 'checkpoint_ids'))
            if wanted.intersection(id_key(value) for value in checkpoint_refs):
                related[collection] = unique_ids([*related.get(collection, []), record_id(record)])
    return related


def checkpoint_ids_for_record(pack: dict[str, Any], collection: str, record: dict[str, Any]) -> list[str]:
    record_key = id_key(record_id(record))
    if not record_key:
        return []
    checkpoint_ids = string_list(first(record, 'checkpointId', 'checkpointIds', 'checkpoint_id', 'checkpoint_ids'))
    if collection == 'encounters':
        checkpoint_ids.extend(string_list(first(record, 'checkpointIds', 'checkpoint_ids')))
    if checkpoint_ids:
        return unique_ids(checkpoint_ids)

    result: list[str] = []
    for checkpoint in collection_records(pack, 'checkpoints'):
        refs = checkpoint_references(checkpoint).get(collection, [])
        if record_key in {id_key(value) for value in refs}:
            result.append(record_id(checkpoint))
    return unique_ids(result)


def collection_records(pack: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    if not isinstance(pack, dict):
        return []
    direct = records(pack.get(collection))
    if direct:
        return direct
    catalog = pack.get('catalog') if isinstance(pack.get('catalog'), dict) else {}
    return records(catalog.get(collection))


def records(value: Any) -> list[dict[str, Any]]:
    return [record for record in value if isinstance(record, dict)] if isinstance(value, list) else []


def record_id(record: dict[str, Any]) -> str:
    return text(first(record, 'id', 'checkpointId', 'checkpoint_id', 'recordId', 'record_id'))


def record_title(record: dict[str, Any]) -> str:
    return text(first(record, 'title', 'name', 'playerTitle', 'player_title', 'publicTitle', 'public_title'))


def visible_at_start(record: dict[str, Any]) -> bool:
    return truthy(first(record, 'visibleAtStart', 'visible_at_start'))


def hidden_to_players(record: dict[str, Any]) -> bool:
    explicit = first(record, 'hiddenToPlayers', 'hidden_to_players')
    visibility = text(first(record, 'visibility', 'playerVisibility', 'player_visibility')).lower()
    return truthy(explicit) or visibility in {'hidden', 'secret', 'gm', 'gm_only', 'dm', 'dm_only'}


def is_terminal_checkpoint(checkpoint: dict[str, Any]) -> bool:
    kind = text(first(checkpoint, 'type', 'kind', 'checkpointType', 'checkpoint_type')).lower()
    return truthy(first(checkpoint, 'terminal', 'isTerminal', 'is_terminal', 'end')) or kind in {
        'terminal',
        'end',
        'finale',
    }


def first(record: dict[str, Any] | None, *keys: str) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        if key in record:
            return record.get(key)
    return None


def string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        values = value
    elif isinstance(value, str):
        values = value.replace(';', ',').split(',')
    elif value in (None, ''):
        values = []
    else:
        values = [value]
    return unique_ids([text(item) for item in values if text(item)])


def unique_ids(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = text(value)
        if not item:
            continue
        key = id_key(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def id_key(value: Any) -> str:
    return text(value).lower()


def humanize_identifier(value: Any) -> str:
    identifier = text(value)
    if not identifier:
        return ''
    identifier = re.sub(r'^(cp|checkpoint|chk)[_-]+', '', identifier, flags=re.IGNORECASE)
    identifier = identifier.replace('_', ' ').replace('-', ' ')
    return ' '.join(part.capitalize() for part in identifier.split())


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return text(value).lower() in {'1', 'true', 'yes', 'y', 'on'}


def text(value: Any) -> str:
    return str(value or '').strip()
