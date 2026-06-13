from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from aidm_server.services.campaign_pack import CampaignPackImportError, import_campaign_pack


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
                message=str(exc),
            )
        )
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        issues.append(
            CampaignPackLintIssue(
                severity='error',
                code='campaign_pack_lint_failed',
                path='campaign pack',
                message=str(exc),
            )
        )

    manifest = pack.get('pack') if isinstance(pack.get('pack'), dict) else pack
    issues.extend(_static_lint_issues(manifest if isinstance(manifest, dict) else {}))
    graph = _checkpoint_graph(manifest if isinstance(manifest, dict) else {})
    summary = _lint_summary(manifest if isinstance(manifest, dict) else {})
    return {
        'ok': not any(issue.severity == 'error' for issue in issues),
        'issues': [issue.payload() for issue in issues],
        'preview': preview,
        'graph': graph,
        'summary': summary,
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
    collections = (
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
    )
    return {
        'packId': _text(pack.get('packId') or pack.get('pack_id')),
        'title': _text(pack.get('title') or pack.get('name')),
        'version': _text(pack.get('version')) or '1.0.0',
        'schemaVersion': _text(pack.get('schemaVersion') or pack.get('schema_version')) or '1',
        'counts': {collection: len(_records(pack.get(collection))) for collection in collections},
        'dependencies': len(_records(pack.get('dependencies'))),
        'mods': len(_records(pack.get('mods'))),
    }


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
            for target in _string_list(checkpoint.get(field)):
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
    start_id = _text(
        (pack.get('startingState') if isinstance(pack.get('startingState'), dict) else {}).get('checkpointId')
        or pack.get('startingCheckpointId')
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
        stack.extend(_string_list(checkpoint.get('nextCheckpointIds')))
        stack.extend(_string_list(checkpoint.get('alternateCheckpointIds')))
        stack.extend(_string_list(checkpoint.get('failureCheckpointIds')))
    return reachable


def _has_completion_cue(checkpoint: dict[str, Any]) -> bool:
    if any(
        checkpoint.get(key)
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
    return bool(_string_list(checkpoint.get('nextCheckpointIds')) or _string_list(checkpoint.get('alternateCheckpointIds')))


def _pack_only(pack: dict[str, Any]) -> bool:
    rules = pack.get('directorRules') if isinstance(pack.get('directorRules'), dict) else {}
    return _text(rules.get('mainQuestGeneration') or rules.get('main_quest_generation')) == 'pack_only'


def _records(value: Any) -> list[dict[str, Any]]:
    return [record for record in value if isinstance(record, dict)] if isinstance(value, list) else []


def _record_id(record: dict[str, Any]) -> str:
    return _text(record.get('id') or record.get('checkpointId') or record.get('checkpoint_id'))


def _terminal(checkpoint: dict[str, Any]) -> bool:
    kind = _text(checkpoint.get('type') or checkpoint.get('kind') or checkpoint.get('checkpointType'))
    return _truthy(checkpoint.get('terminal') or checkpoint.get('isTerminal') or checkpoint.get('end')) or kind in {
        'terminal',
        'end',
        'finale',
    }


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
