from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
from typing import Any

from aidm_server.game_state.campaign_pack_encounters import (
    ADJUSTMENT_HP_MULTIPLIER_RANGE,
    CATALOG_CONSEQUENCES,
    ENCOUNTER_TRIGGER_TYPES,
    RESOLUTION_END_REASONS,
    START_BANDS,
)
from aidm_server.game_state.models import stable_slug
from aidm_server.game_state.quest_engine import CONSEQUENCE_CHANGE_TYPES, objective_is_mechanical
from aidm_server.game_state.validation.validator import SCENE_TYPES
from aidm_server.game_state.zones import location_zones, zone_steps
from aidm_server.interactables import INTERACTABLE_ACTIONS, supported_actions, validate_interactable_catalog
from aidm_server.segment_triggers import KEYWORD_TRIGGER_TYPES, LOCATION_TRIGGER_TYPE, SUPPORTED_TRIGGER_TYPES
from aidm_server.services.campaign_pack import (
    PACK_BOUNDS_ERROR_CODE,
    CampaignPackImportError,
    campaign_pack_bound_violations,
    import_campaign_pack,
)
from aidm_server.services.campaign_pack_graph import is_terminal_checkpoint
from aidm_server.services.campaign_pack_progress import COMBAT_COMPLETE_REASONS
from aidm_server.services.tie_lines import TIE_LINES_KEY, ending_flags, tie_line_problems


logger = logging.getLogger(__name__)

ID_PATTERN = re.compile(r'[a-z0-9_]+')
QUEST_PREDICATE_COMBINATORS = ('all', 'allOf', 'any', 'anyOf')
# Keys quest_engine.rule_satisfied reads from a leaf predicate.
QUEST_PREDICATE_KEYS = frozenset(
    {
        'eventType',
        'event',
        'changeType',
        'actorId',
        'targetActorId',
        'targetId',
        'objectId',
        'itemId',
        'itemName',
        'npcId',
        'locationId',
        'encounterId',
        'participantId',
        'endReason',
        'flagKey',
        'checkpointId',
        'objectiveId',
        'questId',
        'quantityAtLeast',
        'amountAtLeast',
        'atLocationId',
        'flagValue',
        'equals',
        'possessesItemId',
        'possessesItemName',
        'objectiveIds',
        'objectivePolicy',
    }
)
QUEST_PREDICATE_REFERENCES = {
    'encounterId': 'encounters',
    'objectiveId': 'objectives',
    'questId': 'quests',
    'locationId': 'locations',
    'atLocationId': 'locations',
    'npcId': 'npcs',
    'checkpointId': 'checkpoints',
}
# Keys campaign_pack_progress reads from checkpoint completeWhen / failWhen.
CHECKPOINT_COMPLETE_WHEN_KEYS = frozenset(
    {
        'locationId',
        'locationIds',
        'location_id',
        'location_ids',
        'segmentId',
        'segmentIds',
        'segment_id',
        'segment_ids',
        'triggeredSegmentIds',
        'triggered_segment_ids',
        'objectiveId',
        'objectiveIds',
        'objective_id',
        'objective_ids',
        'questObjectiveIds',
        'quest_objective_ids',
        'questId',
        'questIds',
        'quest_id',
        'quest_ids',
        'encounterId',
        'encounterIds',
        'encounter_id',
        'encounter_ids',
        'clueId',
        'clueIds',
        'clue_id',
        'clue_ids',
        'flags',
        'anyOf',
        'any_of',
        'outcomes',
        'allowedOutcomes',
        'allowed_outcomes',
    }
)
CHECKPOINT_FAIL_WHEN_KEYS = frozenset(
    {
        'questId',
        'questIds',
        'quest_id',
        'quest_ids',
        'objectiveId',
        'objectiveIds',
        'objective_id',
        'objective_ids',
        'questObjectiveIds',
        'quest_objective_ids',
        'encounterId',
        'encounterIds',
        'encounter_id',
        'encounter_ids',
    }
)
ENCOUNTER_OUTCOME_LABELS = frozenset(label for labels in COMBAT_COMPLETE_REASONS.values() for label in labels)
READ_DIRECTOR_RULE_KEYS = frozenset(
    {
        'mainQuestGeneration',
        'main_quest_generation',
        'sideQuestGeneration',
        'side_quest_generation',
        'newNpcs',
        'new_npcs',
        'newLocations',
        'new_locations',
        'offTrackPolicy',
        'off_track_policy',
        'checkpointStyle',
        'checkpoint_style',
    }
)
# Record-level reference fields and the collection each one names.
RECORD_REFERENCE_FIELDS = {
    'connectedLocationIds': 'locations',
    'npcIds': 'npcs',
    'questIds': 'quests',
    'encounterIds': 'encounters',
    'enemyIds': 'enemies',
    'locationIds': 'locations',
    'checkpointIds': 'checkpoints',
}
CHECKPOINT_REFERENCE_FIELDS = {
    'nextCheckpointIds': 'checkpoints',
    'alternateCheckpointIds': 'checkpoints',
    'failureCheckpointIds': 'checkpoints',
    'prerequisiteCheckpointIds': 'checkpoints',
    'rejoinTargetCheckpointId': 'checkpoints',
    'segmentIds': 'segments',
    'objectiveIds': 'objectives',
    'failedObjectiveIds': 'objectives',
    'clueIds': 'clues',
}
CHECKPOINT_CONDITION_REFERENCE_FIELDS = {
    'locationId': 'locations',
    'locationIds': 'locations',
    'segmentId': 'segments',
    'segmentIds': 'segments',
    'triggeredSegmentIds': 'segments',
    'objectiveId': 'objectives',
    'objectiveIds': 'objectives',
    'questObjectiveIds': 'objectives',
    'questId': 'quests',
    'questIds': 'quests',
    'encounterId': 'encounters',
    'encounterIds': 'encounters',
    'clueId': 'clues',
    'clueIds': 'clues',
}
STARTING_STATE_REFERENCE_FIELDS = {
    'locationId': 'locations',
    'questId': 'quests',
    'checkpointId': 'checkpoints',
    'knownLocationIds': 'locations',
    'knownNpcIds': 'npcs',
    'activeNpcIds': 'npcs',
    'knownQuestIds': 'quests',
    'activeQuestIds': 'quests',
}

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
    'merchants',
)
# The id each encounter trigger type names, and the collection it must exist in.
TRIGGER_REFERENCE_FIELDS = {
    'on_enter': ('locationId', 'locations'),
    'on_checkpoint': ('checkpointId', 'checkpoints'),
    'on_interactable': ('targetId', 'interactables'),
}
# Interactable fields keyed by action that name catalog records.
INTERACTABLE_DISCOVERY_FIELDS = {'discoversClueIds': 'clues', 'discoversLocationIds': 'locations'}
MERCHANT_PRICE_KEYS = ('gp', 'sp', 'cp')


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


def lint_campaign_pack_manifest(pack: dict[str, Any], *, workspace_id: str = 'lint', strict: bool = False) -> dict[str, Any]:
    """Validate and lint a pack; ``strict`` makes the rules that are errors under ``lint --strict`` errors."""

    issues: list[CampaignPackLintIssue] = []
    preview: dict[str, Any] | None = None
    try:
        preview = import_campaign_pack(pack, workspace_id=workspace_id, dry_run=True).payload
    except CampaignPackImportError as exc:
        # Bound violations are all reported, with their paths, by the static pass.
        if exc.error_code != PACK_BOUNDS_ERROR_CODE:
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
    issues.extend(_static_lint_issues(manifest if isinstance(manifest, dict) else {}, strict=strict))
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


def lint_campaign_pack_file(path: str | Path, *, workspace_id: str = 'lint', strict: bool = False) -> dict[str, Any]:
    return lint_campaign_pack_manifest(load_campaign_pack_file(path), workspace_id=workspace_id, strict=strict)


def _static_lint_issues(pack: dict[str, Any], *, strict: bool) -> list[CampaignPackLintIssue]:
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
    issues.extend(_engine_contract_issues(pack, strict=strict))
    return issues


def _engine_contract_issues(pack: dict[str, Any], *, strict: bool) -> list[CampaignPackLintIssue]:
    """Authoring the engine would reject, ignore, or never act on."""

    ids = _pack_id_sets(pack)
    issues: list[CampaignPackLintIssue] = [
        _error(PACK_BOUNDS_ERROR_CODE, path, f'{path} {message}.')
        for path, message in campaign_pack_bound_violations(pack)
    ]
    issues.extend(_record_id_issues(pack))
    issues.extend(_reference_issues(pack, ids))
    issues.extend(_interactable_catalog_issues(pack))
    issues.extend(_action_wording_issues(pack))
    issues.extend(_choice_action_issues(pack))
    issues.extend(_condition_duration_issues(pack))
    issues.extend(_ending_variant_issues(pack))
    issues.extend(_segment_trigger_issues(pack))
    issues.extend(_quest_issues(pack, ids))
    issues.extend(_checkpoint_condition_issues(pack))
    issues.extend(_location_connection_issues(pack))
    issues.extend(_branch_flag_issues(pack))
    issues.extend(_director_rule_issues(pack))
    issues.extend(_encounter_trigger_issues(pack, ids))
    issues.extend(_encounter_resolution_issues(pack, ids))
    issues.extend(_encounter_adjustment_issues(pack))
    issues.extend(_world_consequence_issues(pack, ids))
    issues.extend(_merchant_issues(pack, ids))
    issues.extend(_story_flag_issues(pack))
    issues.extend(_story_flag_set_by_pack_issues(pack, strict=strict))
    issues.extend(_npc_listed_nowhere_issues(pack))
    issues.extend(_scene_type_issues(pack))
    issues.extend(_location_text_issues(pack))
    issues.extend(_zone_issues(pack))
    issues.extend(_route_issues(pack, ids))
    issues.extend(_tie_line_issues(pack, ids))
    issues.extend(_inspires_issues(pack))
    issues.extend(_talk_issues(pack, ids))
    issues.extend(_scene_plan_issues(pack, ids))
    issues.extend(_director_issues(pack, ids))
    starting_state = _dict(_first(pack, 'startingState', 'starting_state'))
    if 'campaignFlags' in starting_state or 'campaign_flags' in starting_state:
        issues.append(
            _warning(
                'starting_campaign_flags_alias',
                'startingState.campaignFlags',
                'campaignFlags is an alias of startingState.flags; prefer flags.',
            )
        )
    return issues


def _error(code: str, path: str, message: str) -> CampaignPackLintIssue:
    return CampaignPackLintIssue(severity='error', code=code, path=path, message=message)


def _warning(code: str, path: str, message: str) -> CampaignPackLintIssue:
    return CampaignPackLintIssue(severity='warning', code=code, path=path, message=message)


def _pack_id_sets(pack: dict[str, Any]) -> dict[str, set[str]]:
    ids = {
        collection: {_text(record.get('id')) for record in _records(pack.get(collection)) if _text(record.get('id'))}
        for collection in AUTHORING_COLLECTIONS
    }
    ids['objectives'] = {
        _text(objective.get('id'))
        for quest in _records(pack.get('quests'))
        for objective in _records(quest.get('objectives'))
        if _text(objective.get('id'))
    }
    ids['interactables'] = {
        _text(record.get('id')) for _path, record in _pack_interactables(pack) if _text(record.get('id'))
    }
    return ids


def _pack_interactables(pack: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every authored scene object with its JSON path."""

    found: list[tuple[str, dict[str, Any]]] = []
    for index, location in enumerate(_records(pack.get('locations'))):
        scene_state = _dict(location.get('sceneState'))
        for collection in ('interactables', 'hazards'):
            for source, path in ((location, f'locations[{index}]'), (scene_state, f'locations[{index}].sceneState')):
                found.extend(
                    (f'{path}.{collection}[{record_index}]', record)
                    for record_index, record in enumerate(_records(source.get(collection)))
                )
    current_scene = _dict(_first(_dict(_first(pack, 'startingState', 'starting_state')), 'currentScene', 'current_scene'))
    for collection in ('interactables', 'hazards'):
        found.extend(
            (f'startingState.currentScene.{collection}[{record_index}]', record)
            for record_index, record in enumerate(_records(current_scene.get(collection)))
        )
    return found


def _record_id_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    issues = []
    for collection in AUTHORING_COLLECTIONS:
        for index, record in enumerate(_records(pack.get(collection))):
            paths = [(f'{collection}[{index}].id', record.get('id'))]
            if collection == 'quests':
                paths.extend(
                    (f'{collection}[{index}].objectives[{objective_index}].id', objective.get('id'))
                    for objective_index, objective in enumerate(_records(record.get('objectives')))
                )
            for path, value in paths:
                if value is not None and not ID_PATTERN.fullmatch(_text(value)):
                    issues.append(
                        _error(
                            'invalid_id',
                            path,
                            f'ID "{_text(value)}" must use only lowercase letters, digits and underscores; '
                            'the importer rewrites IDs but not the references to them.',
                        )
                    )
    return issues


def _missing_reference_issues(
    value: Any,
    *,
    path: str,
    target: str,
    ids: dict[str, set[str]],
) -> list[CampaignPackLintIssue]:
    known = ids.get(target, set())
    return [
        _error('unknown_reference', path, f'{path} names "{reference}", which is not one of the pack\'s {target}.')
        for reference in _string_list(value)
        if reference not in known
    ]


def _field_reference_issues(
    record: dict[str, Any],
    fields: dict[str, str],
    *,
    path: str,
    ids: dict[str, set[str]],
) -> list[CampaignPackLintIssue]:
    issues = []
    for field, target in fields.items():
        for key in (field, _snake_alias(field)):
            if key in record:
                issues.extend(_missing_reference_issues(record[key], path=f'{path}.{key}', target=target, ids=ids))
    return issues


def _reference_issues(pack: dict[str, Any], ids: dict[str, set[str]]) -> list[CampaignPackLintIssue]:
    issues: list[CampaignPackLintIssue] = []
    for collection in AUTHORING_COLLECTIONS:
        for index, record in enumerate(_records(pack.get(collection))):
            issues.extend(_field_reference_issues(record, RECORD_REFERENCE_FIELDS, path=f'{collection}[{index}]', ids=ids))
    for collection in ('npcs', 'segments'):
        for index, record in enumerate(_records(pack.get(collection))):
            issues.extend(_field_reference_issues(record, {'locationId': 'locations'}, path=f'{collection}[{index}]', ids=ids))
    for index, segment in enumerate(_records(pack.get('segments'))):
        trigger = _dict(segment.get('trigger'))
        if _text(trigger.get('type')).lower() == LOCATION_TRIGGER_TYPE:
            issues.extend(
                _field_reference_issues(trigger, {'locationId': 'locations'}, path=f'segments[{index}].trigger', ids=ids)
            )
    for index, encounter in enumerate(_records(pack.get('encounters'))):
        for group_index, group in enumerate(_records(_first(encounter, 'enemyGroups', 'enemy_groups'))):
            issues.extend(
                _field_reference_issues(
                    group,
                    {'enemyId': 'enemies'},
                    path=f'encounters[{index}].enemyGroups[{group_index}]',
                    ids=ids,
                )
            )
    for index, checkpoint in enumerate(_records(pack.get('checkpoints'))):
        path = f'checkpoints[{index}]'
        issues.extend(_field_reference_issues(checkpoint, CHECKPOINT_REFERENCE_FIELDS, path=path, ids=ids))
        for condition_key in ('completeWhen', 'failWhen'):
            condition = _first(checkpoint, condition_key, _snake_alias(condition_key))
            if isinstance(condition, dict):
                issues.extend(
                    _field_reference_issues(
                        condition,
                        CHECKPOINT_CONDITION_REFERENCE_FIELDS,
                        path=f'{path}.{condition_key}',
                        ids=ids,
                    )
                )
    starting_state = _dict(_first(pack, 'startingState', 'starting_state'))
    issues.extend(_field_reference_issues(starting_state, STARTING_STATE_REFERENCE_FIELDS, path='startingState', ids=ids))
    issues.extend(
        _field_reference_issues(
            _dict(_first(starting_state, 'currentScene', 'current_scene')),
            STARTING_STATE_REFERENCE_FIELDS,
            path='startingState.currentScene',
            ids=ids,
        )
    )
    return issues


def _interactable_catalog_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    catalog: dict[str, Any] = {'locations': pack.get('locations') if isinstance(pack.get('locations'), list) else []}
    starting_state = _dict(_first(pack, 'startingState', 'starting_state'))
    current_scene = _dict(_first(starting_state, 'currentScene', 'current_scene'))
    scene_collections = {key: current_scene[key] for key in ('interactables', 'hazards') if key in current_scene}
    if scene_collections:
        catalog['currentScene'] = {
            'locationId': _text(_first(starting_state, 'locationId', 'location_id') or current_scene.get('locationId')),
            **scene_collections,
        }
    return [
        _error('invalid_interactable_catalog', _text(error.get('path')), f"{error.get('code')}: {error.get('message')}")
        for error in validate_interactable_catalog(catalog)
    ]


def _action_wording_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    """An object's label or DM text for an action it does not support is never used."""

    issues: list[CampaignPackLintIssue] = []
    for path, record in _pack_interactables(pack):
        supported = supported_actions(record, 'hazards' if '.hazards[' in path else 'interactables')
        name = _text(record.get('name')) or _record_id(record) or 'This object'
        for field, wording in (('actionLabels', 'label'), ('actionText', 'text')):
            for action in _dict(record.get(field)):
                if action not in supported:
                    issues.append(
                        _warning(
                            f'action_{wording}_for_unsupported_action',
                            f'{path}.{field}.{action}',
                            f'{name} does not support "{action}", so this {wording} is never used.',
                        )
                    )
    return issues


def _choice_action_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    """Every action an object lists in ``choiceActions`` is one it supports."""

    issues: list[CampaignPackLintIssue] = []
    for path, record in _pack_interactables(pack):
        if 'choiceActions' not in record:
            continue
        choices = record.get('choiceActions')
        if not isinstance(choices, list):
            issues.append(_error('invalid_choice_actions', f'{path}.choiceActions', 'choiceActions must be a list of actions.'))
            continue
        supported = supported_actions(record, 'hazards' if '.hazards[' in path else 'interactables')
        name = _text(record.get('name')) or _record_id(record) or 'This object'
        for index, action in enumerate(choices):
            if _text(action) not in supported:
                issues.append(
                    _error(
                        'choice_action_unsupported',
                        f'{path}.choiceActions[{index}]',
                        f'{name} does not support "{_text(action)}", so it can never be offered as a choice.',
                    )
                )
    return issues


def _condition_duration_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    """An ability's ``durationRounds`` is a whole number of turns from 1 to 10, on an ability that applies a condition."""

    issues: list[CampaignPackLintIssue] = []
    for enemy_index, enemy in enumerate(_records(pack.get('enemies'))):
        for ability_index, ability in enumerate(_records(enemy.get('abilities'))):
            if 'durationRounds' not in ability:
                continue
            path = f'enemies[{enemy_index}].abilities[{ability_index}].durationRounds'
            rounds = ability.get('durationRounds')
            if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= 10:
                issues.append(_error('invalid_condition_duration', path, 'durationRounds must be a whole number from 1 to 10.'))
            elif not _string_list(ability.get('conditionsApplied')):
                issues.append(
                    _warning('condition_duration_without_conditions', path, 'The ability applies no condition, so durationRounds is never used.')
                )
    return issues


def _ending_variant_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    """Ending variants sit on a terminal checkpoint and each names a flag the pack sets, a title and a text."""

    issues: list[CampaignPackLintIssue] = []
    set_flags = _flag_keys_set_by_pack(pack)
    for index, checkpoint in enumerate(_records(pack.get('checkpoints'))):
        if 'endingVariants' not in checkpoint:
            continue
        path = f'checkpoints[{index}].endingVariants'
        variants = checkpoint.get('endingVariants')
        if not isinstance(variants, list):
            issues.append(_error('invalid_ending_variant', path, 'endingVariants must be a list.'))
            continue
        if not is_terminal_checkpoint(checkpoint):
            issues.append(_warning('ending_variants_not_terminal', path, 'Only a terminal checkpoint is an ending, so these variants are never shown.'))
        for variant_index, variant in enumerate(variants):
            variant_path = f'{path}[{variant_index}]'
            when = _dict(_dict(variant).get('when'))
            if not (_text(_dict(variant).get('title')) and _text(_dict(variant).get('text')) and _text(when.get('flagKey')) and 'equals' in when):
                issues.append(
                    _error('invalid_ending_variant', variant_path, 'An ending variant needs when {flagKey, equals}, a title and a text.')
                )
            elif _text(when.get('flagKey')) not in set_flags and stable_slug(when.get('flagKey')) not in set_flags:
                issues.append(
                    _warning(
                        'ending_variant_flag_never_set',
                        f'{variant_path}.when.flagKey',
                        f'Nothing in the pack sets flag "{_text(when.get("flagKey"))}", so this ending is never told.',
                    )
                )
    return issues


def _segment_trigger_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    issues = []
    for index, segment in enumerate(_records(pack.get('segments'))):
        key = next((key for key in ('trigger', 'triggerCondition', 'trigger_condition') if key in segment), None)
        if key is None or segment[key] is None or isinstance(segment[key], str):
            continue
        path = f'segments[{index}].{key}'
        trigger = segment[key]
        if not isinstance(trigger, dict):
            issues.append(_error('invalid_segment_trigger', path, 'A segment trigger must be a keyword string or a trigger object.'))
            continue
        trigger_type = _text(trigger.get('type') or 'manual').lower()
        if trigger_type not in SUPPORTED_TRIGGER_TYPES:
            issues.append(
                _error(
                    'invalid_segment_trigger',
                    f'{path}.type',
                    f'Trigger type "{trigger_type}" is not one of: {", ".join(sorted(SUPPORTED_TRIGGER_TYPES))}.',
                )
            )
        elif trigger_type in KEYWORD_TRIGGER_TYPES and not _string_list(trigger.get('keywords')):
            issues.append(_error('invalid_segment_trigger', f'{path}.keywords', 'A keyword trigger needs a keywords list.'))
        elif trigger_type == LOCATION_TRIGGER_TYPE and not _text(trigger.get('locationId')):
            issues.append(_error('invalid_segment_trigger', f'{path}.locationId', 'A location trigger needs a locationId.'))
    return issues


def _quest_issues(pack: dict[str, Any], ids: dict[str, set[str]]) -> list[CampaignPackLintIssue]:
    issues: list[CampaignPackLintIssue] = []
    reward_item_ids = _encounter_reward_item_ids(pack)
    for quest_index, quest in enumerate(_records(pack.get('quests'))):
        objectives = _records(quest.get('objectives'))
        quest_objective_ids = {_text(objective.get('id')) for objective in objectives if _text(objective.get('id'))}
        for objective_index, objective in enumerate(objectives):
            path = f'quests[{quest_index}].objectives[{objective_index}]'
            if not objective_is_mechanical(objective):
                issues.append(
                    _warning(
                        'objective_without_mechanics',
                        path,
                        'Objective has no completeWhen, failWhen or prerequisites; only an operator can complete this objective.',
                    )
                )
            # Prerequisites are looked up inside the same quest.
            for key in ('prerequisiteObjectiveIds', 'prerequisites'):
                issues.extend(
                    _missing_reference_issues(
                        objective.get(key),
                        path=f'{path}.{key}',
                        target='objectives',
                        ids={'objectives': quest_objective_ids},
                    )
                )
            rules = _dict(objective.get('rules'))
            for key in ('completeWhen', 'failWhen'):
                if key in objective:
                    predicate, predicate_path = objective[key], f'{path}.{key}'
                elif key in rules:
                    predicate, predicate_path = rules[key], f'{path}.rules.{key}'
                else:
                    continue
                issues.extend(_predicate_issues(predicate, path=predicate_path, ids=ids, reward_item_ids=reward_item_ids))
    return issues


def _predicate_issues(
    predicate: Any,
    *,
    path: str,
    ids: dict[str, set[str]],
    reward_item_ids: set[str],
) -> list[CampaignPackLintIssue]:
    if not isinstance(predicate, dict):
        return [_error('invalid_predicate_entry', path, 'A predicate must be a JSON object; the engine ignores anything else.')]
    if not predicate:
        return [_error('invalid_predicate_entry', path, 'An empty predicate is never satisfied.')]
    issues: list[CampaignPackLintIssue] = []
    for key in QUEST_PREDICATE_COMBINATORS:
        if key in predicate and not isinstance(predicate[key], list):
            issues.append(_error('invalid_predicate_entry', f'{path}.{key}', f'{key} must be a list of predicates.'))
    combinator = next((key for key in QUEST_PREDICATE_COMBINATORS if isinstance(predicate.get(key), list)), None)
    if combinator:
        for key in predicate:
            if key != combinator:
                issues.append(
                    _error('unknown_predicate_key', f'{path}.{key}', f'"{key}" is ignored next to {combinator}.')
                )
        entries = predicate[combinator]
        if not any(isinstance(entry, dict) for entry in entries):
            issues.append(
                _error('invalid_predicate_entry', f'{path}.{combinator}', f'{combinator} has no predicates and is never satisfied.')
            )
        for index, entry in enumerate(entries):
            issues.extend(
                _predicate_issues(entry, path=f'{path}.{combinator}[{index}]', ids=ids, reward_item_ids=reward_item_ids)
            )
        return issues
    for key, value in predicate.items():
        key_path = f'{path}.{key}'
        if key not in QUEST_PREDICATE_KEYS:
            issues.append(_error('unknown_predicate_key', key_path, f'"{key}" is not part of the quest predicate language.'))
        elif key in QUEST_PREDICATE_REFERENCES:
            issues.extend(_missing_reference_issues(value, path=key_path, target=QUEST_PREDICATE_REFERENCES[key], ids=ids))
        elif key == 'objectiveIds':
            issues.extend(_missing_reference_issues(value, path=key_path, target='objectives', ids=ids))
        elif key == 'possessesItemId' and _text(value) in reward_item_ids:
            issues.append(
                _warning(
                    'possesses_encounter_reward_item_id',
                    key_path,
                    f'Encounter rewards give "{_text(value)}" a new instance id, so this never matches; use possessesItemName.',
                )
            )
    return issues


def _checkpoint_condition_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    issues = []
    for index, checkpoint in enumerate(_records(pack.get('checkpoints'))):
        for condition_key, known_keys in (
            ('completeWhen', CHECKPOINT_COMPLETE_WHEN_KEYS),
            ('failWhen', CHECKPOINT_FAIL_WHEN_KEYS),
        ):
            condition = _first(checkpoint, condition_key, _snake_alias(condition_key))
            if not isinstance(condition, dict):
                continue
            path = f'checkpoints[{index}].{condition_key}'
            for key in condition:
                if key not in known_keys:
                    issues.append(
                        _warning(
                            'unread_checkpoint_condition_key',
                            f'{path}.{key}',
                            f'Checkpoint progress does not read {condition_key}.{key}.',
                        )
                    )
            if condition_key != 'completeWhen':
                continue
            for any_of_key in ('anyOf', 'any_of'):
                entries = condition.get(any_of_key)
                prose = [
                    entry
                    for entry in (entries if isinstance(entries, list) else [entries])
                    if isinstance(entry, str) and _status_key(entry) not in ENCOUNTER_OUTCOME_LABELS
                ]
                if prose:
                    issues.append(
                        _warning(
                            'prose_in_complete_when',
                            f'{path}.{any_of_key}',
                            f'{len(prose)} prose entr{"y" if len(prose) == 1 else "ies"} in completeWhen.{any_of_key} '
                            'never complete the checkpoint; only encounter outcome labels are read.',
                        )
                    )
    return issues


def _location_connection_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    return [
        _warning(
            'location_without_connections',
            f'locations[{index}]',
            f'Location "{_record_id(location)}" has no connectedLocationIds; structured travel cannot reach or leave it.',
        )
        for index, location in enumerate(_records(pack.get('locations')))
        if not _string_list(_first(location, 'connectedLocationIds', 'connected_location_ids'))
    ]


def _branch_flag_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    set_flags = _flag_keys_set_by_pack(pack)
    issues = []
    for index, checkpoint in enumerate(_records(pack.get('checkpoints'))):
        branch = _dict(_first(checkpoint, 'branchWhen', 'branch_when'))
        flag_key = _text(_first(branch, 'flagKey', 'flag_key'))
        if flag_key and flag_key not in set_flags:
            issues.append(
                _warning(
                    'branch_flag_never_set',
                    f'checkpoints[{index}].branchWhen.flagKey',
                    f'Nothing in the pack sets flag "{flag_key}", so this branch can only open through an operator.',
                )
            )
    return issues


def _director_rule_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    sources = [('directorRules', _first(pack, 'directorRules', 'director_rules'))]
    sources.extend(
        (f'checkpoints[{index}].directorRules', _first(checkpoint, 'directorRules', 'director_rules'))
        for index, checkpoint in enumerate(_records(pack.get('checkpoints')))
    )
    return [
        _warning('unread_director_rule', f'{path}.{key}', f'The engine does not read director rule "{key}".')
        for path, rules in sources
        for key in _dict(rules)
        if key not in READ_DIRECTOR_RULE_KEYS
    ]


def _encounter_trigger_issues(pack: dict[str, Any], ids: dict[str, set[str]]) -> list[CampaignPackLintIssue]:
    issues: list[CampaignPackLintIssue] = []
    for index, encounter in enumerate(_records(pack.get('encounters'))):
        if 'trigger' not in encounter:
            continue
        path = f'encounters[{index}].trigger'
        trigger = encounter.get('trigger')
        trigger_type = _text(_dict(trigger).get('type'))
        if trigger_type not in ENCOUNTER_TRIGGER_TYPES:
            issues.append(
                _error(
                    'invalid_encounter_trigger',
                    f'{path}.type',
                    f'Trigger type "{trigger_type}" is not one of: {", ".join(ENCOUNTER_TRIGGER_TYPES)}.',
                )
            )
            continue
        if trigger_type == 'on_flag' and not _text(trigger.get('flagKey')):
            issues.append(_error('invalid_encounter_trigger', f'{path}.flagKey', 'An on_flag trigger needs a flagKey.'))
        if trigger_type == 'on_interactable' and _text(trigger.get('action')) and trigger.get('action') not in INTERACTABLE_ACTIONS:
            issues.append(
                _error('invalid_encounter_trigger', f'{path}.action', f'"{trigger.get("action")}" is not an object action.')
            )
        if trigger_type in TRIGGER_REFERENCE_FIELDS:
            field, target = TRIGGER_REFERENCE_FIELDS[trigger_type]
            if not _text(trigger.get(field)):
                issues.append(_error('invalid_encounter_trigger', f'{path}.{field}', f'An {trigger_type} trigger needs {field}.'))
            else:
                issues.extend(_missing_reference_issues(trigger.get(field), path=f'{path}.{field}', target=target, ids=ids))
    return issues


def _encounter_allows(encounter: dict[str, Any], outcome: str) -> bool:
    """Whether a settled ``outcome`` counts as completing the encounter, read as its combat ending would be."""

    allowed = set(_string_list(_dict(encounter.get('completion')).get('anyOf')))
    end_reason = RESOLUTION_END_REASONS.get(outcome)
    return bool(end_reason) and (not allowed or bool(allowed & COMBAT_COMPLETE_REASONS.get(end_reason, set())))


def _encounter_resolution_issues(pack: dict[str, Any], ids: dict[str, set[str]]) -> list[CampaignPackLintIssue]:
    """``resolvesEncounter`` names an existing encounter and an outcome that encounter allows."""

    encounters = {_record_id(encounter): encounter for encounter in _records(pack.get('encounters'))}
    issues: list[CampaignPackLintIssue] = []
    for path, record in _pack_interactables(pack):
        for action, spec in _dict(record.get('resolvesEncounter')).items():
            spec_path = f'{path}.resolvesEncounter.{action}'
            if action not in INTERACTABLE_ACTIONS:
                issues.append(_error('invalid_encounter_resolution', spec_path, f'"{action}" is not an object action.'))
                continue
            encounter_id = _text(_dict(spec).get('encounterId'))
            outcome = _text(_dict(spec).get('outcome'))
            if encounter_id not in ids['encounters']:
                issues.append(
                    _error('unknown_resolved_encounter', f'{spec_path}.encounterId', f'No encounter has the id "{encounter_id}".')
                )
            elif not _encounter_allows(encounters[encounter_id], outcome):
                issues.append(
                    _error(
                        'encounter_outcome_not_allowed',
                        f'{spec_path}.outcome',
                        f'Encounter "{encounter_id}" does not allow the outcome "{outcome}".',
                    )
                )
    return issues


def _encounter_adjustment_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    """``adjustments`` read flags the pack sets, name enemies of their own encounter and keep HP multipliers in range."""

    set_flags = _flag_keys_set_by_pack(pack)
    low, high = ADJUSTMENT_HP_MULTIPLIER_RANGE
    issues: list[CampaignPackLintIssue] = []
    for index, encounter in enumerate(_records(pack.get('encounters'))):
        enemy_ids = set(_string_list(encounter.get('enemyIds'))) | {
            _text(group.get('enemyId')) for group in _records(encounter.get('enemyGroups'))
        }
        for adjustment_index, adjustment in enumerate(_records(encounter.get('adjustments'))):
            path = f'encounters[{index}].adjustments[{adjustment_index}]'
            flag_key = _text(_dict(adjustment.get('when')).get('flagKey'))
            if flag_key not in set_flags:
                issues.append(_error('unknown_adjustment_flag', f'{path}.when.flagKey', f'Nothing in the pack sets flag "{flag_key}".'))
            named = [('enemyId', [adjustment['enemyId']])] if 'enemyId' in adjustment else []
            named.append(('removeEnemyIds', _string_list(adjustment.get('removeEnemyIds'))))
            for field, values in named:
                for enemy_id in values:
                    if _text(enemy_id) not in enemy_ids:
                        issues.append(
                            _error('unknown_adjustment_enemy', f'{path}.{field}', f'Enemy "{enemy_id}" is not in this encounter.')
                        )
            multiplier = adjustment.get('hpMultiplier')
            if multiplier is not None and (
                isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)) or not low <= multiplier <= high
            ):
                issues.append(
                    _error('adjustment_multiplier_out_of_range', f'{path}.hpMultiplier', f'hpMultiplier must be from {low} to {high}.')
                )
    return issues


def _consequence_issues(
    consequences: Any,
    *,
    path: str,
    ids: dict[str, set[str]],
) -> list[CampaignPackLintIssue]:
    issues: list[CampaignPackLintIssue] = []
    for index, consequence in enumerate(_records(consequences)):
        consequence_path = f'{path}[{index}]'
        consequence_type = _text(consequence.get('type'))
        if consequence_type not in CONSEQUENCE_CHANGE_TYPES:
            issues.append(
                _error(
                    'unsupported_consequence_type',
                    f'{consequence_path}.type',
                    f'Consequence type "{consequence_type}" is not applied; use one of: {", ".join(sorted(CONSEQUENCE_CHANGE_TYPES))}.',
                )
            )
        elif consequence_type in CATALOG_CONSEQUENCES:
            collection, id_key, _embedded = CATALOG_CONSEQUENCES[consequence_type]
            issues.extend(
                _missing_reference_issues(consequence.get(id_key), path=f'{consequence_path}.{id_key}', target=collection, ids=ids)
            )
    return issues


def _world_consequence_issues(pack: dict[str, Any], ids: dict[str, set[str]]) -> list[CampaignPackLintIssue]:
    issues: list[CampaignPackLintIssue] = []
    for index, location in enumerate(_records(pack.get('locations'))):
        if 'onEnter' in location:
            issues.extend(_consequence_issues(location.get('onEnter'), path=f'locations[{index}].onEnter', ids=ids))
    for path, interactable in _pack_interactables(pack):
        for field, target in INTERACTABLE_DISCOVERY_FIELDS.items():
            for action, referenced in _dict(interactable.get(field)).items():
                issues.extend(_missing_reference_issues(referenced, path=f'{path}.{field}.{action}', target=target, ids=ids))
    return issues


def _merchant_issues(pack: dict[str, Any], ids: dict[str, set[str]]) -> list[CampaignPackLintIssue]:
    issues: list[CampaignPackLintIssue] = []
    for index, merchant in enumerate(_records(pack.get('merchants'))):
        path = f'merchants[{index}]'
        if not _text(merchant.get('npcId')) and not _text(merchant.get('locationId')):
            issues.append(
                _error('merchant_without_place', path, 'A merchant needs an npcId or a locationId to open for trade.')
            )
        issues.extend(_field_reference_issues(merchant, {'npcId': 'npcs', 'locationId': 'locations'}, path=path, ids=ids))
        for stock_index, entry in enumerate(_records(merchant.get('stock'))):
            if _text(entry.get('catalog')):
                continue
            price = entry.get('price')
            amounts = [_dict(price).get(key) for key in MERCHANT_PRICE_KEYS if key in _dict(price)]
            if not amounts or not all(isinstance(amount, int) and not isinstance(amount, bool) and amount > 0 for amount in amounts):
                issues.append(
                    _error(
                        'invalid_merchant_price',
                        f'{path}.stock[{stock_index}].price',
                        'A stock price needs positive whole gp, sp or cp amounts.',
                    )
                )
    return issues


def _story_flag_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    return [
        _error(
            'invalid_story_flag_key',
            f'storyFlags[{index}].key',
            f'Story flag key "{_text(story_flag.get("key"))}" must use only lowercase letters, digits and underscores.',
        )
        for index, story_flag in enumerate(_records(_first(pack, 'storyFlags', 'story_flags')))
        if not ID_PATTERN.fullmatch(_text(story_flag.get('key')))
    ]


def _story_flag_set_by_pack_issues(pack: dict[str, Any], *, strict: bool) -> list[CampaignPackLintIssue]:
    """A story flag the pack also sets: narration may set any declared flag, so talk can set it first."""

    first_setter: dict[str, str] = {}
    for key, path in _flag_setters(pack):
        first_setter.setdefault(key, path)
    issues = []
    for index, story_flag in enumerate(_records(_first(pack, 'storyFlags', 'story_flags'))):
        key = _text(story_flag.get('key'))
        setter = first_setter.get(stable_slug(key)) if key else None
        if setter:
            issues.append(
                CampaignPackLintIssue(
                    severity='error' if strict else 'warning',
                    code='story_flag_set_by_pack',
                    path=f'storyFlags[{index}].key',
                    message=(
                        f'Story flag "{key}" is also set by {setter}; narration may set a declared flag, '
                        'so talk can set it before that does.'
                    ),
                )
            )
    return issues


def _npc_listed_nowhere_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    """A person with ``knows`` lines whom the party never meets: not visible at start and in no place's ``npcIds``."""

    listed = {
        npc_id
        for location in _records(pack.get('locations'))
        for npc_id in _string_list(_first(location, 'npcIds', 'npc_ids'))
    }
    starting_state = _dict(_first(pack, 'startingState', 'starting_state'))
    known_at_start = {
        npc_id
        for source in (starting_state, _dict(_first(starting_state, 'currentScene', 'current_scene')))
        for field in ('knownNpcIds', 'activeNpcIds')
        for npc_id in _string_list(_first(source, field, _snake_alias(field)))
    }
    return [
        _warning(
            'npc_listed_nowhere',
            f'npcs[{index}]',
            f'"{_text(npc.get("name")) or _record_id(npc)}" has knows lines but is neither visibleAtStart nor in any '
            "location's npcIds, so the party can never talk to them.",
        )
        for index, npc in enumerate(_records(pack.get('npcs')))
        if _string_list(npc.get('knows'))
        # ``hidden: true`` says on purpose that the party never meets them by arriving.
        and npc.get('hidden') is not True
        and not _visible_at_start(npc)
        and _record_id(npc) not in known_at_start | listed
    ]


def _scene_type_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    """A location ``sceneType`` outside the supported set: the place is entered as exploration."""

    issues = []
    for index, location in enumerate(_records(pack.get('locations'))):
        for source, path in ((location, f'locations[{index}]'), (_dict(location.get('sceneState')), f'locations[{index}].sceneState')):
            scene_type = _text(source.get('sceneType'))
            if 'sceneType' in source and scene_type not in SCENE_TYPES:
                issues.append(
                    _warning(
                        'unsupported_scene_type',
                        f'{path}.sceneType',
                        f'"{scene_type}" is not one of: {", ".join(sorted(SCENE_TYPES))}; the place is entered as exploration.',
                    )
                )
    return issues


def _location_text_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    """A location text that speaks of "the players": it is read to them on arrival."""

    return [
        _warning(
            'location_description_addresses_players',
            f'locations[{index}].{field}',
            f'Location "{_record_id(location)}" {field} mentions "the players", and it is read aloud on arrival.',
        )
        for index, location in enumerate(_records(pack.get('locations')))
        for field in ('description', 'playerDescription')
        if 'the players' in _text(location.get(field)).lower()
    ]


def _location_zone_issues(location: dict[str, Any], path: str) -> list[CampaignPackLintIssue]:
    """One location's own zones: unique ids, known neighbours, a start zone, and every zone reachable from it."""

    zones = _records(location.get('zones'))
    if not zones:
        return []
    issues = []
    ids = [_record_id(zone) for zone in zones]
    for index, zone in enumerate(zones):
        if not _record_id(zone) or ids.count(_record_id(zone)) > 1:
            issues.append(_error('duplicate_zone_id', f'{path}.zones[{index}]', f'Zone "{_record_id(zone)}" needs an id of its own.'))
        for other in _string_list(zone.get('adjacentTo')):
            if other not in ids:
                issues.append(_error('unknown_zone_id', f'{path}.zones[{index}].adjacentTo', f'"{other}" is not a zone of this location.'))
    start = _text(location.get('startZoneId'))
    if start not in ids:
        message = f'"{start}" is not a zone of this location.' if start else 'A location with zones needs a startZoneId.'
        issues.append(_error('zone_without_start' if not start else 'unknown_zone_id', f'{path}.startZoneId', message))
    zones_view = location_zones(location)
    reached = {zone['id'] for zone in zones_view if zone_steps(zones_view, ids[0], zone['id']) is not None}
    unreachable = [zone_id for zone_id in ids if zone_id and zone_id not in reached]
    if unreachable:
        issues.append(
            _error(
                'zone_graph_not_connected',
                f'{path}.zones',
                f'Not every zone can be reached from the others: {", ".join(unreachable)}.',
            )
        )
    return issues


def _zone_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    """Rooms and areas (``zones``): each location's graph, and every object, person and encounter that names a
    zone naming one of its own location's; an enemy group's ``startBand``."""

    locations = _records(pack.get('locations'))
    zones_of = {_record_id(location): {_record_id(zone) for zone in _records(location.get('zones'))} for location in locations}
    issues = [issue for index, location in enumerate(locations) for issue in _location_zone_issues(location, f'locations[{index}]')]

    def misplaced(zone_id: str, own: set[str], path: str, what: str) -> CampaignPackLintIssue | None:
        if not zone_id or zone_id in own:
            return None
        elsewhere = next((location_id for location_id, ids in zones_of.items() if zone_id in ids), '')
        if elsewhere:
            return _error('zone_in_other_location', path, f'{what} stands in "{zone_id}", a zone of {elsewhere}, not of its own location.')
        return _error('unknown_zone_id', path, f'{what} names "{zone_id}", which is not a zone of any location.')

    for index, location in enumerate(locations):
        own = zones_of[_record_id(location)]
        for collection in ('interactables', 'hazards'):
            for record_index, record in enumerate(_records(location.get(collection))):
                path = f'locations[{index}].{collection}[{record_index}].zoneId'
                issue = misplaced(_text(record.get('zoneId')), own, path, f'Object "{_record_id(record)}"')
                if issue:
                    issues.append(issue)
    for index, npc in enumerate(_records(pack.get('npcs'))):
        homes = [_text(npc.get('locationId'))] + [
            _record_id(location) for location in locations if _record_id(npc) in _string_list(_first(location, 'npcIds', 'npc_ids'))
        ]
        own = {zone_id for location_id in homes for zone_id in zones_of.get(location_id, set())}
        issue = misplaced(_text(npc.get('zoneId')), own, f'npcs[{index}].zoneId', f'"{_text(npc.get("name")) or _record_id(npc)}"')
        if issue:
            issues.append(issue)
    for index, encounter in enumerate(_records(pack.get('encounters'))):
        own = {zone_id for location_id in _string_list(encounter.get('locationIds')) for zone_id in zones_of.get(location_id, set())}
        issue = misplaced(_text(encounter.get('zoneId')), own, f'encounters[{index}].zoneId', f'Encounter "{_record_id(encounter)}"')
        if issue:
            issues.append(issue)
        for group_index, group in enumerate(_records(encounter.get('enemyGroups'))):
            band = _text(group.get('startBand'))
            if band and band not in START_BANDS:
                issues.append(
                    _error(
                        'unknown_start_band',
                        f'encounters[{index}].enemyGroups[{group_index}].startBand',
                        f'"{band}" is not one of: {", ".join(START_BANDS)}.',
                    )
                )
    return issues


def _route_issues(pack: dict[str, Any], ids: dict[str, set[str]]) -> list[CampaignPackLintIssue]:
    issues: list[CampaignPackLintIssue] = []
    for index, route in enumerate(_records(pack.get('routes'))):
        issues.extend(_field_reference_issues(route, {'from': 'locations', 'to': 'locations'}, path=f'routes[{index}]', ids=ids))
    return issues


def _walk_dicts(value: Any, path: str = '') -> Iterator[tuple[str, dict[str, Any]]]:
    """Every object inside ``value`` with its JSON path under ``path``."""

    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from _walk_dicts(item, f'{path}.{key}' if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_dicts(item, f'{path}[{index}]')


def _reward_containers(record: dict[str, Any], path: str = '') -> list[tuple[str, dict[str, Any]]]:
    containers = [(f'{path}.rewards', _dict(record.get('rewards'))), (f'{path}.reward', _dict(record.get('reward')))]
    for key in ('outcomeRewards', 'outcome_rewards', 'outcomes'):
        for outcome, value in _dict(record.get(key)).items():
            outcome_spec, outcome_path = _dict(value), f'{path}.{key}.{outcome}'
            containers.extend(
                [
                    (outcome_path, outcome_spec),
                    (f'{outcome_path}.rewards', _dict(outcome_spec.get('rewards'))),
                    (f'{outcome_path}.reward', _dict(outcome_spec.get('reward'))),
                ]
            )
    return [(container_path, container) for container_path, container in containers if container]


def _flag_setters(pack: dict[str, Any]) -> list[tuple[str, str]]:
    """``(slugged flag key, path)`` for every flag set by an object, an encounter, a quest, an arrival or a person (a
    topic, reaction or callback, a promise included), in that order."""

    setters: list[tuple[str, str]] = []
    for path, interactable in _pack_interactables(pack):
        for action, flags in _dict(interactable.get('setsFlags')).items():
            setters.extend((stable_slug(key), f'{path}.setsFlags.{action}') for key in _dict(flags))
        for spec_path, spec in _plan_consequences(interactable, path):
            setters.extend((stable_slug(key), f'{spec_path}.setsFlags') for key in _dict(spec.get('setsFlags')))
    for index, encounter in enumerate(_records(pack.get('encounters'))):
        for spec_path, spec in _fight_consequences(encounter, f'encounters[{index}]'):
            setters.extend((stable_slug(key), f'{spec_path}.setsFlags') for key in _dict(spec.get('setsFlags')))
    records = [
        *((f'encounters[{index}]', encounter) for index, encounter in enumerate(_records(pack.get('encounters')))),
        *((f'quests[{index}]', quest) for index, quest in enumerate(_records(pack.get('quests')))),
        *(
            (f'locations[{index}]', {'onEnter': location.get('onEnter')})
            for index, location in enumerate(_records(pack.get('locations')))
        ),
    ]
    for path, record in records:
        for node_path, node in _walk_dicts(record, path):
            if _text(node.get('type')) in {'flag.set', 'flag'} and _text(node.get('flagKey') or node.get('key')):
                setters.append((stable_slug(node.get('flagKey') or node.get('key')), node_path))
        for container_path, container in _reward_containers(record, path):
            flags = container.get('flags')
            if isinstance(flags, dict) and not _text(flags.get('flagKey') or flags.get('key')):
                setters.extend((stable_slug(key), f'{container_path}.flags') for key in flags)
                continue
            for flag in _records(flags if isinstance(flags, list) else [flags]):
                if _text(flag.get('flagKey') or flag.get('key')):
                    setters.append((stable_slug(flag.get('flagKey') or flag.get('key')), f'{container_path}.flags'))
    for path, effects in _talk_effects(pack):
        setters.extend((stable_slug(key), f'{path}.setsFlags') for key in _dict(effects.get('setsFlags')))
        if _promise_id(effects):
            setters.append((stable_slug(_promise_id(effects)), f'{path}.promise'))
    for path, clock in _pack_clocks(pack):
        for step_index, step in enumerate(_records(clock.get('steps'))):
            setters.extend((stable_slug(key), f'{path}.steps[{step_index}].effects.setsFlags') for key in _dict(_dict(step.get('effects')).get('setsFlags')))
    return setters


def _flag_keys_set_by_pack(pack: dict[str, Any]) -> set[str]:
    starting_state = _dict(_first(pack, 'startingState', 'starting_state'))
    # Starting flags are copied verbatim; every other flag write is slugged.
    keys = {
        _text(key)
        for source in ('flags', 'campaignFlags', 'campaign_flags')
        for key in _dict(starting_state.get(source))
    }
    keys.update(_text(story_flag.get('key')) for story_flag in _records(_first(pack, 'storyFlags', 'story_flags')))
    keys.update(key for key, _path in _flag_setters(pack))
    return keys


def _encounter_reward_item_ids(pack: dict[str, Any]) -> set[str]:
    item_ids: set[str] = set()
    for encounter in _records(pack.get('encounters')):
        for _path, container in _reward_containers(encounter):
            items = container.get('items')
            for item in _records(items if isinstance(items, list) else [items]):
                if _text(item.get('id')):
                    item_ids.add(_text(item.get('id')))
        for _path, node in _walk_dicts([encounter.get('rewards'), encounter.get('reward')]):
            if _text(node.get('type')) == 'item' and _text(_dict(node.get('item')).get('id')):
                item_ids.add(_text(_dict(node.get('item')).get('id')))
    return item_ids


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


# Advisory sizes. Records carry the play content the engine reads directly (objects, topics,
# barks, zones, pressure events, plans and clocks), so a rich place or person is expected to be a
# few thousand characters, and an opening chapter's yard (the Play Now inn) about thirteen
# thousand; the warnings are for records that have outgrown even that.
PACK_RECORD_CHAR_BUDGET = 16_000
PACK_COLLECTION_CHAR_BUDGET = 240_000


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
            if len(encoded) > PACK_RECORD_CHAR_BUDGET:
                issues.append(
                    CampaignPackLintIssue(
                        severity='warning',
                        code='pack_record_prompt_budget',
                        path=f'{collection_name}[{index}]',
                        message='Large authored records should be summarized or split before import.',
                    )
                )
        if total_chars > PACK_COLLECTION_CHAR_BUDGET:
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


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _tie_line_issues(pack: dict[str, Any], ids: dict[str, set[str]]) -> list[CampaignPackLintIssue]:
    """Tie lines name the pack's own objects (and an action each supports), locations, encounters and ending
    flags, and use only the tokens their kind can fill."""

    object_actions = {
        _text(record.get('id')): supported_actions(record, 'hazards' if '.hazards[' in path else 'interactables')
        for path, record in _pack_interactables(pack)
        if _text(record.get('id'))
    }
    problems = tie_line_problems(
        pack.get(TIE_LINES_KEY),
        object_actions=object_actions,
        location_ids=ids['locations'],
        encounter_ids=ids['encounters'],
        ending_flag_keys={flag for checkpoint in _records(pack.get('checkpoints')) for flag in ending_flags(checkpoint)},
    )
    return [_error('invalid_tie_line', f'{TIE_LINES_KEY}{suffix}', message) for suffix, message in problems]


def _inspires_issues(pack: dict[str, Any]) -> list[CampaignPackLintIssue]:
    """An object's ``inspires`` names actions it supports, each with its reason."""

    issues: list[CampaignPackLintIssue] = []
    for path, record in _pack_interactables(pack):
        if 'inspires' not in record:
            continue
        inspires = record.get('inspires')
        if not isinstance(inspires, dict) or not all(_text(reason) for reason in inspires.values()):
            issues.append(_error('invalid_inspires', f'{path}.inspires', 'inspires maps an action to the reason it inspires.'))
            continue
        supported = supported_actions(record, 'hazards' if '.hazards[' in path else 'interactables')
        name = _text(record.get('name')) or _record_id(record) or 'This object'
        issues.extend(
            _warning('inspires_unsupported_action', f'{path}.inspires.{action}', f'{name} does not support "{action}", so it never inspires.')
            for action in inspires
            if action not in supported
        )
    return issues


TALK_REFERENCE_FIELDS = {'clueIds': 'clues', 'encounterResolvedIds': 'encounters'}
TALK_EFFECT_REFERENCES = {'discoversClueIds': 'clues', 'discoversLocationIds': 'locations'}


def _pack_item_ids(pack: dict[str, Any]) -> set[str]:
    """Every item the pack can put in a hero's hands: container contents, encounter rewards, people's gifts."""

    item_ids = {_text(item.get('id')) for _path, record in _pack_interactables(pack) for item in _records(record.get('contents')) if _text(item.get('id'))}
    item_ids |= _encounter_reward_item_ids(pack)
    gifts = [effects for _path, effects in _talk_effects(pack)]
    gifts.extend(_dict(affordance.get('result')) for _path, record in _pack_interactables(pack) for affordance in _records(record.get('affordances')))
    gifts.extend(result for encounter in _records(pack.get('encounters')) for result in _records(encounter.get('results')))
    for effects in gifts:
        item_ids.update(_text(item.get('id')) for item in _records(effects.get('givesItems')) if _text(item.get('id')))
    return item_ids


def _talk_effects(pack: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """``(path, effects)`` for every effects block of the people's topics, reactions and callbacks."""

    found = []
    for index, npc in enumerate(_records(pack.get('npcs'))):
        for collection in ('topics', 'reacts', 'callbacks'):
            for record_index, record in enumerate(_records(npc.get(collection))):
                path = f'npcs[{index}].{collection}[{record_index}]'
                check = _dict(record.get('check'))
                for key, block in (('effects', record.get('effects')), ('failEffects', record.get('failEffects')), ('check.failEffects', check.get('failEffects'))):
                    if isinstance(block, dict):
                        found.append((f'{path}.{key}', block))
    return found


def _talk_requirements(value: Any, path: str) -> list[tuple[str, dict[str, Any]]]:
    """A ``requires`` (or ``dcWhen`` entry) and every ``anyOf`` requirement inside it, with their paths."""

    if not isinstance(value, dict):
        return []
    found = [(path, value)]
    for index, entry in enumerate(_records(value.get('anyOf'))):
        found.extend(_talk_requirements(entry, f'{path}.anyOf[{index}]'))
    return found


def _talk_issues(pack: dict[str, Any], ids: dict[str, set[str]]) -> list[CampaignPackLintIssue]:
    """Every id a person's topics, reactions and callbacks name is one the pack has; a topic id is unique per person."""

    item_ids = _pack_item_ids(pack)
    promises = {_promise_id(effects) for _path, effects in _talk_effects(pack) if _promise_id(effects)}
    issues: list[CampaignPackLintIssue] = []

    def unknown(path: str, kind: str, value: str) -> None:
        issues.append(_error('talk_unknown_reference', path, f'Unknown {kind} "{value}".'))

    for index, npc in enumerate(_records(pack.get('npcs'))):
        topic_ids = [_text(topic.get('id')) for topic in _records(npc.get('topics'))]
        for duplicate in sorted({topic_id for topic_id in topic_ids if topic_ids.count(topic_id) > 1}):
            issues.append(_error('talk_duplicate_topic', f'npcs[{index}].topics', f'Topic id "{duplicate}" is used twice.'))
        requirements = []
        for topic_index, topic in enumerate(_records(npc.get('topics'))):
            path = f'npcs[{index}].topics[{topic_index}]'
            requirements.extend(_talk_requirements(topic.get('requires'), f'{path}.requires'))
            for when_index, entry in enumerate(_records(_dict(topic.get('check')).get('dcWhen'))):
                requirements.extend(_talk_requirements(entry, f'{path}.check.dcWhen[{when_index}]'))
        for react_index, react in enumerate(_records(npc.get('reacts'))):
            for when_index, entry in enumerate(_records(_dict(react.get('check')).get('dcWhen'))):
                requirements.extend(_talk_requirements(entry, f'npcs[{index}].reacts[{react_index}].check.dcWhen[{when_index}]'))
            about = _text(react.get('about'))
            if about and _text(react.get('act')) in {'show', 'offer'} and about not in item_ids | ids['clues']:
                unknown(f'npcs[{index}].reacts[{react_index}].about', 'item or clue', about)
        for path, requirement in requirements:
            for field, collection in TALK_REFERENCE_FIELDS.items():
                for value in _string_list(requirement.get(field)):
                    if value not in ids[collection]:
                        unknown(f'{path}.{field}', collection[:-1], value)
            for value in _string_list(requirement.get('itemIds')):
                if value not in item_ids:
                    unknown(f'{path}.itemIds', 'item', value)
            for value in _string_list(requirement.get('topicIds')):
                if value not in topic_ids:
                    unknown(f'{path}.topicIds', 'topic of this person', value)
        for callback_index, callback in enumerate(_records(npc.get('callbacks'))):
            path = f'npcs[{index}].callbacks[{callback_index}]'
            for value in _string_list(callback.get('offers')):
                if value not in topic_ids:
                    unknown(f'{path}.offers', 'topic of this person', value)
            when = _dict(callback.get('when'))
            if _text(when.get('atLocationId')) and _text(when['atLocationId']) not in ids['locations']:
                unknown(f'{path}.when.atLocationId', 'location', _text(when['atLocationId']))
            if _text(when.get('on')).startswith('topic:') and _text(when['on'])[len('topic:'):] not in topic_ids:
                unknown(f'{path}.when.on', 'topic of this person', _text(when['on'])[len('topic:'):])
        for value in _string_list(npc.get('caresAbout')):
            if value not in item_ids | ids['clues']:
                unknown(f'npcs[{index}].caresAbout', 'item or clue', value)
        for value in _string_list(npc.get('wants')):
            if not value.startswith('coin:') and value not in item_ids:
                unknown(f'npcs[{index}].wants', 'item', value)
    for path, effects in _talk_effects(pack):
        for field, collection in TALK_EFFECT_REFERENCES.items():
            for value in _string_list(effects.get(field)):
                if value not in ids[collection]:
                    unknown(f'{path}.{field}', collection[:-1], value)
        for key in ('keepsPromise', 'breaksPromise'):
            if _text(effects.get(key)) and _text(effects[key]) not in promises:
                unknown(f'{path}.{key}', 'promise (no topic makes it)', _text(effects[key]))
    affects = [(f'{path}.affects', entry) for path, effects in _talk_effects(pack) for entry in _records(effects.get('affects'))]
    for path, record in _pack_interactables(pack):
        for action, entries in _dict(record.get('affects')).items():
            affects.extend((f'{path}.affects.{action}', entry) for entry in _records(entries))
    for index, encounter in enumerate(_records(pack.get('encounters'))):
        for outcome, spec in _dict(encounter.get('outcomes')).items():
            affects.extend((f'encounters[{index}].outcomes.{outcome}.affects', entry) for entry in _records(_dict(spec).get('affects')))
    for path, entry in affects:
        if _text(entry.get('npcId')) not in ids['npcs']:
            unknown(f'{path}.npcId', 'person', _text(entry.get('npcId')))
    return issues


def _promise_id(effects: dict[str, Any]) -> str:
    """An effects' ``promise``, written as its id or as ``{"id", ...}``."""

    promise = effects.get('promise')
    return _text(promise.get('id')) if isinstance(promise, dict) else _text(promise)


def _pack_clocks(pack: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every clock of the pack's locations and encounters, with its JSON path."""

    return [
        (f'{collection}[{index}].clocks[{clock_index}]', clock)
        for collection in ('locations', 'encounters')
        for index, record in enumerate(_records(pack.get(collection)))
        for clock_index, clock in enumerate(_records(record.get('clocks')))
    ]


def _director_issues(pack: dict[str, Any], ids: dict[str, set[str]]) -> list[CampaignPackLintIssue]:
    """Clocks, decision lines and epilogues name only what the pack has; a clock has steps and a cause, and its id
    is unique."""

    issues: list[CampaignPackLintIssue] = []

    def unknown(path: str, kind: str, value: str) -> None:
        issues.append(_error('director_unknown_reference', path, f'Unknown {kind} "{value}".'))

    clock_ids = [_text(clock.get('id')) for _path, clock in _pack_clocks(pack)]
    for duplicate in sorted({clock_id for clock_id in clock_ids if clock_ids.count(clock_id) > 1}):
        issues.append(_error('clock_duplicate_id', 'clocks', f'Clock id "{duplicate}" is used twice.'))
    for path, clock in _pack_clocks(pack):
        advance = _dict(clock.get('advanceOn'))
        if not _records(clock.get('steps')):
            issues.append(_error('clock_without_steps', f'{path}.steps', 'A clock needs at least one step.'))
        if not (_string_list(advance.get('flags')) or _string_list(advance.get('objectActions')) or isinstance(advance.get('everyTurnsAfterStart'), int)):
            issues.append(_error('clock_without_cause', f'{path}.advanceOn', 'A clock moves only for a cause: name flags, objectActions or everyTurnsAfterStart.'))
        for value in _string_list(advance.get('objectActions')):
            object_id = value.split(':', 1)[0]
            if object_id not in ids['interactables']:
                unknown(f'{path}.advanceOn.objectActions', 'scene object', object_id)
        for step_index, step in enumerate(_records(clock.get('steps'))):
            effects = _dict(step.get('effects'))
            step_path = f'{path}.steps[{step_index}].effects'
            if _text(effects.get('startsEncounterId')) and _text(effects['startsEncounterId']) not in ids['encounters']:
                unknown(f'{step_path}.startsEncounterId', 'encounter', _text(effects['startsEncounterId']))
            for value in _string_list(effects.get('discoversLocationIds')):
                if value not in ids['locations']:
                    unknown(f'{step_path}.discoversLocationIds', 'location', value)
            for entry in _records(effects.get('affects')):
                if _text(entry.get('npcId')) not in ids['npcs']:
                    unknown(f'{step_path}.affects.npcId', 'person', _text(entry.get('npcId')))
    for index, checkpoint in enumerate(_records(pack.get('checkpoints'))):
        for line_index, line in enumerate(_records(checkpoint.get('decisionLines'))):
            when = _dict(line.get('when'))
            path = f'checkpoints[{index}].decisionLines[{line_index}].when'
            remembers = when.get('remembers')
            for entry in [remembers] if isinstance(remembers, dict) else _records(remembers):
                if _text(entry.get('npcId')) not in ids['npcs']:
                    unknown(f'{path}.remembers.npcId', 'person', _text(entry.get('npcId')))
            for key in _dict(when.get('stakes')):
                if _text(key).split('.', 1)[0] not in ids['encounters']:
                    unknown(f'{path}.stakes', 'encounter', _text(key).split('.', 1)[0])
    return issues


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


def _status_key(value: Any) -> str:
    return _text(value).lower().replace(' ', '_')


# W9-SCENE: tags, affordances and failing forward on scene objects; stakes, goals, results and floors on encounters.


def _location_object_ids(pack: dict[str, Any]) -> dict[str, set[str]]:
    """Each location's scene object ids."""

    found: dict[str, set[str]] = {}
    for location in _records(pack.get('locations')):
        scene_state = _dict(location.get('sceneState'))
        found[_text(location.get('id'))] = {
            _text(record.get('id'))
            for source in (location, scene_state)
            for collection in ('interactables', 'hazards')
            for record in _records(source.get(collection))
            if _text(record.get('id'))
        }
    return found


def _plan_consequences(record: dict[str, Any], path: str) -> list[tuple[str, dict[str, Any]]]:
    """``(path, spec)`` for what a scene object's plans and failed checks bring: each affordance's ``result`` and
    ``onFail``, and the object's ``onFail`` by action."""

    found = []
    for index, affordance in enumerate(_records(record.get('affordances'))):
        found.extend((f'{path}.affordances[{index}].{key}', _dict(affordance.get(key))) for key in ('result', 'onFail') if _dict(affordance.get(key)))
    found.extend((f'{path}.onFail.{action}', _dict(spec)) for action, spec in _dict(record.get('onFail')).items() if _dict(spec))
    return found


def _fight_consequences(encounter: dict[str, Any], path: str) -> list[tuple[str, dict[str, Any]]]:
    """``(path, spec)`` for what the end of an encounter's fight brings: its ``results`` and its ``onDefeat`` (W9)."""

    found = [(f'{path}.results[{index}]', result) for index, result in enumerate(_records(encounter.get('results')))]
    if isinstance(encounter.get('onDefeat'), dict):
        found.append((f'{path}.onDefeat', encounter['onDefeat']))
    return found


def _consequence_reference_issues(path: str, spec: dict[str, Any], ids: dict[str, set[str]]) -> list[CampaignPackLintIssue]:
    issues = [
        _error('unknown_reference', f'{path}.reveals', f'"{object_id}" is not a scene object of the pack.')
        for object_id in _string_list(spec.get('reveals'))
        if object_id not in ids['interactables']
    ]
    encounter_id = _text(spec.get('startsEncounterId'))
    if encounter_id and encounter_id not in ids['encounters']:
        issues.append(_error('unknown_reference', f'{path}.startsEncounterId', f'"{encounter_id}" is not an encounter of the pack.'))
    issues.extend(
        _error('unknown_reference', f'{path}.affects[{index}].npcId', f'"{_text(affect.get("npcId"))}" is not a person of the pack.')
        for index, affect in enumerate(_records(spec.get('affects')))
        if _text(affect.get('npcId')) not in ids['npcs']
    )
    return issues


def _scene_plan_issues(pack: dict[str, Any], ids: dict[str, set[str]]) -> list[CampaignPackLintIssue]:
    from aidm_server.game_state.orchestration.fight_stakes import STATES_BY_KIND, STAKE_KINDS
    from aidm_server.game_state.orchestration.scene_plans import AFFORDANCE_COSTS, EFFECTS, OBJECT_TAGS

    issues: list[CampaignPackLintIssue] = []
    stake_ids = {
        _text(stake.get('id'))
        for encounter in _records(pack.get('encounters'))
        for stake in (encounter.get('stakes') if isinstance(encounter.get('stakes'), list) else [])
        if isinstance(stake, dict) and _text(stake.get('id'))
    }
    by_location = _location_object_ids(pack)
    for path, record in _pack_interactables(pack):
        location_ids = next((objects for objects in by_location.values() if _text(record.get('id')) in objects), set())
        for index, tag in enumerate(record.get('tags') or []):
            if _text(tag) not in OBJECT_TAGS:
                issues.append(_error('unknown_object_tag', f'{path}.tags[{index}]', f'Tag "{_text(tag)}" is not one of {", ".join(sorted(OBJECT_TAGS))}.'))
        guarded = _text(record.get('guards'))
        if guarded and guarded not in location_ids:
            issues.append(_error('unknown_reference', f'{path}.guards', f'"{guarded}" is not a scene object of the same place.'))
        seen: set[str] = set()
        for index, affordance in enumerate(record.get('affordances') or []):
            at = f'{path}.affordances[{index}]'
            if not isinstance(affordance, dict) or not _text(affordance.get('id')) or not _text(affordance.get('label')):
                issues.append(_error('invalid_affordance', at, 'An affordance needs an id and a label.'))
                continue
            if _text(affordance['id']) in seen:
                issues.append(_error('duplicate_affordance_id', f'{at}.id', f'Affordance "{_text(affordance["id"])}" is listed twice on this object.'))
            seen.add(_text(affordance['id']))
            if _text(affordance.get('effect')) not in EFFECTS:
                issues.append(_error('unknown_effect', f'{at}.effect', f'Effect "{_text(affordance.get("effect"))}" is not one of {", ".join(EFFECTS)}.'))
            if 'cost' in affordance and _text(affordance.get('cost')) not in AFFORDANCE_COSTS:
                issues.append(_error('invalid_affordance', f'{at}.cost', f'Cost "{_text(affordance.get("cost"))}" is not one of {", ".join(AFFORDANCE_COSTS)}.'))
            target = _text(affordance.get('target'))
            if target and target not in {'enemy', 'zone'} and not (target.startswith('stake:') and target[len('stake:'):] in stake_ids):
                issues.append(_error('unknown_stake', f'{at}.target', f'Target "{target}" is not enemy, zone or stake:<a stake of the pack>.'))
            result = _dict(affordance.get('result'))
            for stake_id in _dict(result.get('stakes')):
                if stake_id not in stake_ids:
                    issues.append(_error('unknown_stake', f'{at}.result.stakes.{stake_id}', f'"{stake_id}" is not a stake of any encounter.'))
            issues.extend(_consequence_reference_issues(f'{at}.result', result, ids))
            issues.extend(_consequence_reference_issues(f'{at}.onFail', _dict(affordance.get('onFail')), ids))
        for action, spec in _dict(record.get('onFail')).items():
            issues.extend(_consequence_reference_issues(f'{path}.onFail.{action}', _dict(spec), ids))
    for index, encounter in enumerate(_records(pack.get('encounters'))):
        path = f'encounters[{index}]'
        stakes = {}
        for stake_index, stake in enumerate(encounter.get('stakes') if isinstance(encounter.get('stakes'), list) else []):
            at = f'{path}.stakes[{stake_index}]'
            kind = _text(_dict(stake).get('kind'))
            if kind not in STAKE_KINDS:
                issues.append(_error('invalid_stake', f'{at}.kind', f'Stake kind "{kind}" is not one of {", ".join(STAKE_KINDS)}.'))
                continue
            stakes[_text(stake.get('id'))] = kind
            reference, collection = {'item': ('itemId', None), 'person': ('npcId', 'npcs'), 'route': ('objectId', 'interactables')}[kind]
            value = _text(stake.get(reference))
            known = _pack_item_ids(pack) if collection is None else ids[collection]
            if not value or value not in known:
                issues.append(_error('unknown_reference', f'{at}.{reference}', f'"{value}" is not {"an item" if kind == "item" else "a person" if kind == "person" else "a scene object"} of the pack.'))
            if 'startWhen' in stake and kind == 'person':
                issues.append(_error('invalid_stake_state', f'{at}.startWhen', 'Only a route or item stake starts in a state of its own.'))
                continue
            for start_index, entry in enumerate(_records(stake.get('startWhen'))):
                if _text(entry.get('state')) not in STATES_BY_KIND[kind]:
                    issues.append(
                        _error('invalid_stake_state', f'{at}.startWhen[{start_index}].state', f'"{_text(entry.get("state"))}" is not a state of {"an" if kind == "item" else "a"} {kind} stake.')
                    )
        goals = (('enemyGoal', 'stakeId', 'item'), ('enemyGoal', 'routeStakeId', 'route'), ('secondaryGoal', 'stakeId', 'person'))
        for key, field, kind in goals:
            goal = _dict(encounter.get(key))
            stake_id = _text(goal.get(field))
            if goal and (stake_id or field == 'stakeId') and stakes.get(stake_id) != kind:
                issues.append(_error('unknown_stake', f'{path}.{key}.{field}', f'"{stake_id}" is not {"an" if kind == "item" else "a"} {kind} stake of this encounter.'))
        for result_index, result in enumerate(_records(encounter.get('results'))):
            at = f'{path}.results[{result_index}]'
            for stake_id, state in _dict(_dict(result.get('when')).get('stakes')).items():
                if stake_id not in stakes:
                    issues.append(_error('unknown_stake', f'{at}.when.stakes.{stake_id}', f'"{stake_id}" is not a stake of this encounter.'))
                elif _text(state) not in STATES_BY_KIND[stakes[stake_id]]:
                    issues.append(_error('invalid_stake_state', f'{at}.when.stakes.{stake_id}', f'"{_text(state)}" is not a state of {"an" if stakes[stake_id] == "item" else "a"} {stakes[stake_id]} stake.'))
            issues.extend(_consequence_reference_issues(at, result, ids))
        defeat = encounter.get('onDefeat')
        if isinstance(defeat, dict):
            zone_id = _text(defeat.get('wakeZoneId'))
            zones = {
                _text(zone.get('id'))
                for location in _records(pack.get('locations'))
                if _text(location.get('id')) in _string_list(encounter.get('locationIds'))
                for zone in _records(location.get('zones'))
            }
            if zone_id and zone_id not in zones:
                issues.append(_error('unknown_reference', f'{path}.onDefeat.wakeZoneId', f'"{zone_id}" is not a zone of the encounter\'s place.'))
            for stake_id in _dict(defeat.get('stakes')):
                if stake_id not in stakes:
                    issues.append(_error('unknown_stake', f'{path}.onDefeat.stakes.{stake_id}', f'"{stake_id}" is not a stake of this encounter.'))
        minimum = encounter.get('minEnemies')
        if minimum is not None and (not isinstance(minimum, int) or isinstance(minimum, bool) or not 1 <= minimum <= 6):
            issues.append(_error('invalid_floor', f'{path}.minEnemies', 'minEnemies is a whole number from 1 to 6.'))
        for group_index, group in enumerate(_records(encounter.get('enemyGroups'))):
            floor = group.get('minHp')
            if floor is not None and (not isinstance(floor, int) or isinstance(floor, bool) or floor < 1):
                issues.append(_error('invalid_floor', f'{path}.enemyGroups[{group_index}].minHp', 'minHp is a whole number of at least 1.'))
    return issues
