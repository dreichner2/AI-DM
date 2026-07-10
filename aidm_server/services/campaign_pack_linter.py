from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Any

from aidm_server.services.campaign_pack import CampaignPackImportError, import_campaign_pack


logger = logging.getLogger(__name__)

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


@dataclass(frozen=True)
class CampaignPackLintIssue:
    severity: str
    code: str
    path: str
    message: str

    def payload(self) -> dict[str, str]:
        return {
            'severity': self.severity,
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def load_campaign_pack_file(path: str | Path) -> dict[str, Any]:
    with Path(path).open('r', encoding='utf-8') as pack_file:
        payload = json.load(pack_file)
    if not isinstance(payload, dict):
        raise ValueError('Campaign pack file must contain a JSON object.')
    return payload


def lint_campaign_pack_manifest(pack: dict[str, Any], *, workspace_id: str = 'lint') -> dict[str, Any]:
    issues: list[CampaignPackLintIssue] = []
    preview: dict[str, Any] | None = None
    try:
        preview = import_campaign_pack(pack, workspace_id=workspace_id, dry_run=True).payload
    except CampaignPackImportError as exc:
        issues.append(
            CampaignPackLintIssue(
                severity='error',
                code=exc.error_code,
                path='campaign pack',
                message=exc.public_message,
            )
        )
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        logger.exception('Unexpected campaign pack lint failure: %s', str(exc))
        issues.append(
            CampaignPackLintIssue(
                severity='error',
                code='campaign_pack_lint_failed',
                path='campaign pack',
                message='Campaign pack lint failed.',
            )
        )

    manifest = pack.get('pack') if isinstance(pack.get('pack'), dict) else pack
    issues.extend(_static_lint_issues(manifest if isinstance(manifest, dict) else {}))
    graph = _checkpoint_graph(manifest if isinstance(manifest, dict) else {})
    summary = _lint_summary(manifest if isinstance(manifest, dict) else {})
    authoring_report = _authoring_report(manifest if isinstance(manifest, dict) else {}, graph=graph)
    return {
        'ok': not any(issue.severity == 'error' for issue in issues),
        'issues': [issue.payload() for issue in issues],
        'preview': preview,
        'graph': graph,
        'summary': summary,
        'authoring_report': authoring_report,
    }


def lint_campaign_pack_file(path: str | Path, *, workspace_id: str = 'lint') -> dict[str, Any]:
    return lint_campaign_pack_manifest(load_campaign_pack_file(path), workspace_id=workspace_id)


def _static_lint_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    issues: list[CampaignPackLintIssue] = []
    checkpoints = _records(pack.get('checkpoints'))
    checkpoint_ids = [_record_id(checkpoint) for checkpoint in checkpoints if _record_id(checkpoint)]
    reachable_ids = _reachable_checkpoint_ids(pack, checkpoints)
    for index, checkpoint in enumerate(checkpoints):
        checkpoint_id = _record_id(checkpoint) or f'checkpoint_{index}'
        if checkpoint_id not in reachable_ids:
            issues.append(
                CampaignPackLintIssue(
                    severity='warning',
                    code='unreachable_checkpoint',
                    path=f'checkpoints[{index}]',
                    message=f'Checkpoint "{checkpoint_id}" is not reachable from the starting checkpoint.',
                )
            )
        if not _terminal(checkpoint) and not _has_completion_cue(checkpoint):
            issues.append(
                CampaignPackLintIssue(
                    severity='warning',
                    code='checkpoint_without_completion_condition',
                    path=f'checkpoints[{index}]',
                    message=f'Checkpoint "{checkpoint_id}" has no explicit completion cue.',
                )
            )
        if _pack_only(pack) and not _text(checkpoint.get('rejoinTargetCheckpointId') or checkpoint.get('rejoin_target_checkpoint_id')):
            issues.append(
                CampaignPackLintIssue(
                    severity='warning',
                    code='pack_only_checkpoint_without_rejoin_target',
                    path=f'checkpoints[{index}]',
                    message=f'Checkpoint "{checkpoint_id}" should declare a rejoin target in pack_only mode.',
                )
            )
    if len(checkpoints) > 200:
        issues.append(
            CampaignPackLintIssue(
                severity='warning',
                code='large_checkpoint_graph',
                path='checkpoints',
                message='Large checkpoint graphs should be tested against prompt budget caps.',
            )
        )
    issues.extend(_pack_budget_issues(pack))
    issues.extend(_dependency_issues(pack))
    for collection_name in ('locations', 'npcs', 'quests', 'clues', 'factions', 'maps', 'handouts', 'lore'):
        for index, record in enumerate(_records(pack.get(collection_name))):
            if _truthy(record.get('visibleAtStart') or record.get('visible_at_start')) and _truthy(
                record.get('hiddenToPlayers') or record.get('hidden_to_players')
            ):
                issues.append(
                    CampaignPackLintIssue(
                        severity='error',
                        code='hidden_record_visible_at_start',
                        path=f'{collection_name}[{index}]',
                        message='Record cannot be both visible at start and hidden from players.',
                    )
                )
    if checkpoints and not reachable_ids.intersection(set(checkpoint_ids)):
        issues.append(
            CampaignPackLintIssue(
                severity='error',
                code='checkpoint_graph_has_no_start',
                path='checkpoints',
                message='Checkpoint graph has no reachable starting checkpoint.',
            )
        )
    return issues


def _lint_summary(pack: dict[str, Any]) -> dict[str, Any]:
    return {
        'packId': _text(pack.get('packId') or pack.get('pack_id')),
        'title': _text(pack.get('title') or pack.get('name')),
        'version': _text(pack.get('version')) or '1.0.0',
        'schemaVersion': _text(pack.get('schemaVersion') or pack.get('schema_version')) or '1',
        'counts': {collection: len(_records(pack.get(collection))) for collection in AUTHORING_COLLECTIONS},
        'dependencies': len(_records(pack.get('dependencies'))),
        'mods': len(_records(pack.get('mods'))),
    }


def _authoring_report(pack: dict[str, Any], *, graph: dict[str, Any]) -> dict[str, Any]:
    checkpoints = _records(pack.get('checkpoints'))
    checkpoint_reachable = set(_string_list(graph.get('reachable')))
    encounters = _records(pack.get('encounters'))
    collections = [_collection_report(pack, collection_name) for collection_name in AUTHORING_COLLECTIONS]
    checkpoint_items = [_checkpoint_report_item(checkpoint, checkpoint_reachable) for checkpoint in checkpoints]
    encounter_items = [_encounter_report_item(encounter) for encounter in encounters]
    unlinked_encounter_ids = [
        item['id']
        for item in encounter_items
        if item['id'] and not item['checkpointIds']
    ]
    unreachable_ids = sorted(
        set(_record_id(checkpoint) for checkpoint in checkpoints if _record_id(checkpoint)) - checkpoint_reachable
    )
    return {
        'starting': _starting_report(pack),
        'collections': collections,
        'visibility': {
            'visibleAtStart': {
                item['collection']: item['visibleAtStartIds']
                for item in collections
                if item['visibleAtStartIds']
            },
            'hiddenToPlayers': {
                item['collection']: item['hiddenToPlayersIds']
                for item in collections
                if item['hiddenToPlayersIds']
            },
        },
        'checkpoints': {
            'total': len(checkpoints),
            'reachable': len(checkpoint_reachable),
            'unreachableIds': unreachable_ids,
            'optionalIds': [item['id'] for item in checkpoint_items if item['optional']],
            'terminalIds': [item['id'] for item in checkpoint_items if item['terminal']],
            'items': checkpoint_items,
        },
        'encounters': {
            'total': len(encounters),
            'linkedToCheckpoint': len(encounters) - len(unlinked_encounter_ids),
            'unlinkedIds': unlinked_encounter_ids,
            'items': encounter_items,
        },
    }


def _starting_report(pack: dict[str, Any]) -> dict[str, str]:
    starting_state_value = _first(pack, 'startingState', 'starting_state')
    starting_state = starting_state_value if isinstance(starting_state_value, dict) else {}
    return {
        'locationId': _text(_first(starting_state, 'locationId', 'location_id')),
        'questId': _text(_first(starting_state, 'questId', 'quest_id')),
        'checkpointId': _text(
            _first(starting_state, 'checkpointId', 'checkpoint_id')
            or _first(pack, 'startingCheckpointId', 'starting_checkpoint_id')
        ),
    }


def _collection_report(pack: dict[str, Any], collection_name: str) -> dict[str, Any]:
    records = _records(pack.get(collection_name))
    visible_ids = [_record_id(record) for record in records if _visible_at_start(record) and _record_id(record)]
    hidden_ids = [_record_id(record) for record in records if _hidden_to_players(record) and _record_id(record)]
    return {
        'collection': collection_name,
        'count': len(records),
        'visibleAtStartCount': len(visible_ids),
        'hiddenToPlayersCount': len(hidden_ids),
        'visibleAtStartIds': visible_ids,
        'hiddenToPlayersIds': hidden_ids,
    }


def _checkpoint_report_item(checkpoint: dict[str, Any], reachable_ids: set[str]) -> dict[str, Any]:
    checkpoint_id = _record_id(checkpoint)
    next_ids = _string_list(_first(checkpoint, 'nextCheckpointIds', 'next_checkpoint_ids'))
    alternate_ids = _string_list(_first(checkpoint, 'alternateCheckpointIds', 'alternate_checkpoint_ids'))
    failure_ids = _string_list(_first(checkpoint, 'failureCheckpointIds', 'failure_checkpoint_ids'))
    encounter_ids = _string_list(_first(checkpoint, 'encounterIds', 'encounter_ids'))
    return {
        'id': checkpoint_id,
        'title': _record_title(checkpoint),
        'reachable': checkpoint_id in reachable_ids,
        'optional': _truthy(_first(checkpoint, 'optional', 'isOptional', 'is_optional')),
        'terminal': _terminal(checkpoint),
        'nextCheckpointIds': next_ids,
        'alternateCheckpointIds': alternate_ids,
        'failureCheckpointIds': failure_ids,
        'encounterIds': encounter_ids,
        'completionCues': _completion_cues(checkpoint),
        'branchCount': len(next_ids) + len(alternate_ids) + len(failure_ids),
    }


def _encounter_report_item(encounter: dict[str, Any]) -> dict[str, Any]:
    enemy_ids = _string_list(_first(encounter, 'enemyIds', 'enemy_ids'))
    enemy_groups = _records(_first(encounter, 'enemyGroups', 'enemy_groups'))
    completion_value = _first(encounter, 'completion')
    completion = completion_value if isinstance(completion_value, dict) else {}
    return {
        'id': _record_id(encounter),
        'title': _record_title(encounter),
        'checkpointIds': _string_list(_first(encounter, 'checkpointIds', 'checkpoint_ids')),
        'enemyIds': enemy_ids,
        'enemyGroupCount': len(enemy_groups),
        'enemyCount': len(enemy_ids) + sum(max(1, _int(_first(group, 'count'), default=1)) for group in enemy_groups),
        'completionOutcomes': _completion_outcomes(completion),
    }


def _completion_cues(checkpoint: dict[str, Any]) -> list[str]:
    cues = []
    for field in (
        'completeWhen',
        'locationIds',
        'questIds',
        'objectiveIds',
        'segmentIds',
        'encounterIds',
        'clueIds',
        'failWhen',
    ):
        if _first(checkpoint, field, _snake_alias(field)) not in (None, '', [], {}):
            cues.append(field)
    return cues


def _completion_outcomes(completion: dict[str, Any]) -> list[str]:
    outcomes: list[str] = []
    for item in _records(_first(completion, 'anyOf', 'any_of')):
        outcome = _text(_first(item, 'outcome', 'type', 'status'))
        if outcome and outcome not in outcomes:
            outcomes.append(outcome)
    return outcomes


def _pack_budget_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    issues: list[CampaignPackLintIssue] = []
    for collection_name in (
        'locations',
        'npcs',
        'quests',
        'enemies',
        'encounters',
        'segments',
        'checkpoints',
        'clues',
        'factions',
        'maps',
        'handouts',
        'lore',
    ):
        total_chars = 0
        for index, record in enumerate(_records(pack.get(collection_name))):
            encoded = json.dumps(record, sort_keys=True, ensure_ascii=True)
            total_chars += len(encoded)
            if len(encoded) > 6_000:
                issues.append(
                    CampaignPackLintIssue(
                        severity='warning',
                        code='pack_record_prompt_budget',
                        path=f'{collection_name}[{index}]',
                        message='Large authored records should be summarized or split before import.',
                    )
                )
        if total_chars > 120_000:
            issues.append(
                CampaignPackLintIssue(
                    severity='warning',
                    code='pack_collection_prompt_budget',
                    path=collection_name,
                    message='Large authored collections should be load-tested against prompt and inspector budgets.',
                )
            )
    return issues


def _dependency_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    issues: list[CampaignPackLintIssue] = []
    dependencies = _records(pack.get('dependencies'))
    for index, dependency in enumerate(dependencies):
        dependency_id = _text(dependency.get('packId') or dependency.get('pack_id') or dependency.get('id'))
        if not dependency_id:
            issues.append(
                CampaignPackLintIssue(
                    severity='error',
                    code='missing_pack_dependency_id',
                    path=f'dependencies[{index}]',
                    message='Pack dependencies must declare packId.',
                )
            )
    if dependencies:
        issues.append(
            CampaignPackLintIssue(
                severity='warning',
                code='pack_dependencies_require_library_resolution',
                path='dependencies',
                message='Dependency declarations are preserved, but installed-pack resolution should be checked before publication.',
            )
        )
    return issues


def _checkpoint_graph(pack: dict[str, Any]) -> dict[str, Any]:
    checkpoints = _records(pack.get('checkpoints'))
    nodes = [_record_id(checkpoint) for checkpoint in checkpoints if _record_id(checkpoint)]
    edges = []
    for checkpoint in checkpoints:
        source = _record_id(checkpoint)
        if not source:
            continue
        for field, kind in (
            ('nextCheckpointIds', 'next'),
            ('alternateCheckpointIds', 'alternate'),
            ('failureCheckpointIds', 'failure'),
        ):
            for target in _string_list(_first(checkpoint, field, _snake_alias(field))):
                edges.append({'from': source, 'to': target, 'type': kind})
    return {
        'nodes': nodes,
        'edges': edges,
        'reachable': sorted(_reachable_checkpoint_ids(pack, checkpoints)),
    }


def _reachable_checkpoint_ids(pack: dict[str, Any], checkpoints: list[dict[str, Any]]) -> set[str]:
    by_id = {_record_id(checkpoint): checkpoint for checkpoint in checkpoints if _record_id(checkpoint)}
    if not by_id:
        return set()
    starting_state_value = _first(pack, 'startingState', 'starting_state')
    starting_state = starting_state_value if isinstance(starting_state_value, dict) else {}
    start_id = _text(
        _first(starting_state, 'checkpointId', 'checkpoint_id')
        or _first(pack, 'startingCheckpointId', 'starting_checkpoint_id')
    )
    start_id = start_id if start_id in by_id else next(iter(by_id))
    reachable: set[str] = set()
    stack = [start_id]
    while stack:
        checkpoint_id = stack.pop()
        if checkpoint_id in reachable or checkpoint_id not in by_id:
            continue
        reachable.add(checkpoint_id)
        checkpoint = by_id[checkpoint_id]
        stack.extend(_string_list(_first(checkpoint, 'nextCheckpointIds', 'next_checkpoint_ids')))
        stack.extend(_string_list(_first(checkpoint, 'alternateCheckpointIds', 'alternate_checkpoint_ids')))
        stack.extend(_string_list(_first(checkpoint, 'failureCheckpointIds', 'failure_checkpoint_ids')))
    return reachable


def _has_completion_cue(checkpoint: dict[str, Any]) -> bool:
    if any(
        _first(checkpoint, key, _snake_alias(key))
        for key in (
            'completeWhen',
            'locationIds',
            'questIds',
            'objectiveIds',
            'segmentIds',
            'encounterIds',
            'clueIds',
        )
    ):
        return True
    return bool(
        _string_list(_first(checkpoint, 'nextCheckpointIds', 'next_checkpoint_ids'))
        or _string_list(_first(checkpoint, 'alternateCheckpointIds', 'alternate_checkpoint_ids'))
    )


def _pack_only(pack: dict[str, Any]) -> bool:
    rules = pack.get('directorRules') if isinstance(pack.get('directorRules'), dict) else {}
    return _text(rules.get('mainQuestGeneration') or rules.get('main_quest_generation')) == 'pack_only'


def _records(value: Any) -> list[dict[str, Any]]:
    return [record for record in value if isinstance(record, dict)] if isinstance(value, list) else []


def _record_id(record: dict[str, Any]) -> str:
    return _text(record.get('id') or record.get('checkpointId') or record.get('checkpoint_id'))


def _record_title(record: dict[str, Any]) -> str:
    return _text(_first(record, 'title', 'name', 'playerTitle', 'player_title', 'publicTitle', 'public_title'))


def _terminal(checkpoint: dict[str, Any]) -> bool:
    kind = _text(checkpoint.get('type') or checkpoint.get('kind') or checkpoint.get('checkpointType'))
    return _truthy(checkpoint.get('terminal') or checkpoint.get('isTerminal') or checkpoint.get('end')) or kind in {
        'terminal',
        'end',
        'finale',
    }


def _visible_at_start(record: dict[str, Any]) -> bool:
    return _truthy(_first(record, 'visibleAtStart', 'visible_at_start'))


def _hidden_to_players(record: dict[str, Any]) -> bool:
    explicit = _first(record, 'hiddenToPlayers', 'hidden_to_players')
    visibility = _text(_first(record, 'visibility', 'playerVisibility', 'player_visibility')).lower()
    return _truthy(explicit) or visibility in {'hidden', 'secret', 'gm', 'gm_only', 'dm_only'}


def _first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if isinstance(record, dict) and key in record:
            return record[key]
    return None


def _snake_alias(field: str) -> str:
    return field[0].lower() + ''.join(f'_{char.lower()}' if char.isupper() else char for char in field[1:])


def _int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        values = value
    elif isinstance(value, str):
        values = [item.strip() for item in value.replace(';', ',').split(',')]
    elif value in (None, ''):
        values = []
    else:
        values = [value]
    result: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in result:
            result.append(text)
    return result


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {'1', 'true', 'yes', 'y', 'on'}


def _text(value: Any) -> str:
    return str(value or '').strip()
