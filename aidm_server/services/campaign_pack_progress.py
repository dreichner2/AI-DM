from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aidm_server.database import db
from aidm_server.game_state.models import stable_slug
from aidm_server.models import CampaignSegment, Session, safe_json_dumps, safe_json_loads
from aidm_server.segment_triggers import parse_trigger_spec
from aidm_server.time_utils import utc_now


COMPLETED_STATUSES = {'complete', 'completed', 'done', 'resolved', 'succeeded', 'success', 'closed'}
FAILED_STATUSES = {'failed', 'failure', 'lost', 'abandoned'}
COMBAT_COMPLETE_REASONS = {
    'all_enemies_defeated': {'defeat', 'defeated', 'combat', 'resolve', 'resolved', 'success'},
    'enemies_fled': {'flee', 'fled', 'escape', 'escaped', 'resolve', 'resolved', 'success'},
    'enemies_surrendered': {'surrender', 'surrendered', 'spare', 'spared', 'resolve', 'resolved', 'success'},
    'negotiated_resolution': {'bargain', 'negotiate', 'negotiated', 'parley', 'resolve', 'resolved', 'success'},
    'objective_completed': {'objective', 'completed', 'resolve', 'resolved', 'success'},
}
COMBAT_FAILED_REASONS = {'objective_failed', 'players_fled'}


@dataclass(frozen=True)
class CampaignPackProgressResult:
    changed: bool
    active_checkpoint_id: str | None
    completed_checkpoint_ids: list[str]
    reason: str | None = None
    skipped_checkpoint_ids: list[str] | None = None
    failed_checkpoint_ids: list[str] | None = None


class CampaignPackProgressError(ValueError):
    def __init__(self, message: str, *, error_code: str = 'validation_error', status_code: int = 400):
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


@dataclass(frozen=True)
class CampaignPackControlResult:
    changed: bool
    active_checkpoint_id: str | None
    completed_checkpoint_ids: list[str]
    skipped_checkpoint_ids: list[str]
    reason: str
    failed_checkpoint_ids: list[str] | None = None


def update_campaign_pack_progress(
    *,
    session_id: int,
    campaign_id: int,
    triggered_segments: list[dict] | None = None,
) -> CampaignPackProgressResult:
    session = db.session.get(Session, session_id)
    if not session:
        return CampaignPackProgressResult(False, None, [], None)

    snapshot = safe_json_loads(session.state_snapshot, {})
    if not isinstance(snapshot, dict):
        return CampaignPackProgressResult(False, None, [], None)

    pack = snapshot.get('campaignPack') if isinstance(snapshot.get('campaignPack'), dict) else {}
    pack_id = _text(_first(pack, 'packId', 'pack_id'))
    checkpoints = [checkpoint for checkpoint in (pack.get('checkpoints') or []) if isinstance(checkpoint, dict)]
    if not pack_id or not checkpoints:
        return CampaignPackProgressResult(False, None, [], None)

    flags = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}
    snapshot['flags'] = flags

    completed_ids = _unique_ids(
        _ids_from(pack, 'completedCheckpointIds', 'completed_checkpoint_ids')
        or _ids_from(flags, 'campaignPackCompletedCheckpointIds', 'completedCheckpointIds')
    )
    skipped_ids = _unique_ids(
        _ids_from(pack, 'skippedCheckpointIds', 'skipped_checkpoint_ids')
        or _ids_from(flags, 'campaignPackSkippedCheckpointIds', 'skippedCheckpointIds')
    )
    failed_ids = _unique_ids(
        _ids_from(pack, 'failedCheckpointIds', 'failed_checkpoint_ids')
        or _ids_from(flags, 'campaignPackFailedCheckpointIds', 'failedCheckpointIds')
    )
    previous_completed_ids = list(completed_ids)
    previous_skipped_ids = list(skipped_ids)
    previous_failed_ids = list(failed_ids)
    previous_active_id = _text(
        _first(pack, 'activeCheckpointId', 'active_checkpoint_id', 'currentCheckpointId', 'current_checkpoint_id')
        or _first(flags, 'campaignPackActiveCheckpointId', 'activeCheckpointId')
    )

    active_checkpoint = _checkpoint_by_id(checkpoints, previous_active_id)
    if not active_checkpoint or _checkpoint_id(active_checkpoint) in _terminal_checkpoint_ids(completed_ids, skipped_ids, failed_ids):
        active_checkpoint = _first_incomplete_checkpoint(checkpoints, completed_ids, skipped_ids, failed_ids)

    triggered_segment_refs = _triggered_segment_refs(
        campaign_id=campaign_id,
        pack_id=pack_id,
        triggered_segments=triggered_segments or [],
    )
    next_checkpoint = active_checkpoint
    reason = None
    if active_checkpoint:
        downstream_location_match = _matching_downstream_checkpoint(
            active_checkpoint=active_checkpoint,
            checkpoints=checkpoints,
            completed_ids=completed_ids,
            skipped_ids=skipped_ids,
            failed_ids=failed_ids,
            snapshot=snapshot,
        )
        if downstream_location_match and _checkpoint_id(downstream_location_match) != _checkpoint_id(active_checkpoint):
            _add_unique_id(completed_ids, _checkpoint_id(active_checkpoint))
            next_checkpoint = downstream_location_match
            reason = 'reached_downstream_checkpoint_location'
        else:
            failure_reason = _checkpoint_failure_reason(active_checkpoint, pack=pack, snapshot=snapshot)
            if failure_reason:
                _add_unique_id(failed_ids, _checkpoint_id(active_checkpoint))
                next_checkpoint = _failure_next_checkpoint(active_checkpoint, checkpoints, completed_ids, skipped_ids, failed_ids)
                reason = failure_reason
            else:
                completion_reason = _checkpoint_completion_reason(
                    active_checkpoint,
                    pack=pack,
                    snapshot=snapshot,
                    triggered_segment_refs=triggered_segment_refs,
                )
                if completion_reason:
                    _add_unique_id(completed_ids, _checkpoint_id(active_checkpoint))
                    next_checkpoint = _next_checkpoint(active_checkpoint, checkpoints, completed_ids, skipped_ids, failed_ids)
                    reason = completion_reason

    active_checkpoint_id = _checkpoint_id(next_checkpoint) if next_checkpoint else None
    if active_checkpoint_id in _terminal_checkpoint_ids(completed_ids, skipped_ids, failed_ids):
        active_checkpoint_id = _checkpoint_id(_first_incomplete_checkpoint(checkpoints, completed_ids, skipped_ids, failed_ids))

    changed = (
        active_checkpoint_id != (previous_active_id or None)
        or completed_ids != previous_completed_ids
        or skipped_ids != previous_skipped_ids
        or failed_ids != previous_failed_ids
        or flags.get('campaignPackActiveCheckpointId') != active_checkpoint_id
        or flags.get('campaignPackCompletedCheckpointIds') != completed_ids
        or flags.get('campaignPackSkippedCheckpointIds') != skipped_ids
        or flags.get('campaignPackFailedCheckpointIds') != failed_ids
    )
    if not changed:
        return CampaignPackProgressResult(False, active_checkpoint_id, completed_ids, None, skipped_ids, failed_ids)

    flags['campaignPackActiveCheckpointId'] = active_checkpoint_id
    flags['campaignPackCompletedCheckpointIds'] = completed_ids
    flags['campaignPackSkippedCheckpointIds'] = skipped_ids
    flags['campaignPackFailedCheckpointIds'] = failed_ids
    pack['activeCheckpointId'] = active_checkpoint_id
    pack['completedCheckpointIds'] = completed_ids
    pack['skippedCheckpointIds'] = skipped_ids
    pack['failedCheckpointIds'] = failed_ids
    pack['activeDirectorRules'] = _active_director_rules(pack, next_checkpoint)
    if reason:
        pack['lastProgressReason'] = reason
    snapshot['campaignPack'] = pack
    session.state_snapshot = safe_json_dumps(snapshot, {})
    return CampaignPackProgressResult(True, active_checkpoint_id, completed_ids, reason, skipped_ids, failed_ids)


def campaign_pack_progress_payload(*, session_id: int) -> dict:
    snapshot, pack, checkpoints, flags = _session_pack_state(session_id)
    active_checkpoint, completed_ids, skipped_ids, failed_ids = _current_pack_progress(pack, flags, checkpoints)
    skipped_ids = _unique_ids(
        _ids_from(pack, 'skippedCheckpointIds', 'skipped_checkpoint_ids')
        or _ids_from(flags, 'campaignPackSkippedCheckpointIds', 'skippedCheckpointIds')
    )
    failed_ids = _unique_ids(
        _ids_from(pack, 'failedCheckpointIds', 'failed_checkpoint_ids')
        or _ids_from(flags, 'campaignPackFailedCheckpointIds', 'failedCheckpointIds')
    )
    active_rules = _active_director_rules(pack, active_checkpoint)
    return {
        'enabled': True,
        'pack': {
            'packId': _text(_first(pack, 'packId', 'pack_id')),
            'title': _text(_first(pack, 'title', 'name')),
            'version': _text(_first(pack, 'version')),
            'schemaVersion': _text(_first(pack, 'schemaVersion', 'schema_version')) or '1',
            'source': _text(_first(pack, 'source')) or 'campaign_pack',
        },
        'activeCheckpointId': _checkpoint_id(active_checkpoint),
        'completedCheckpointIds': completed_ids,
        'skippedCheckpointIds': skipped_ids,
        'failedCheckpointIds': failed_ids,
        'checkpointStatuses': _checkpoint_statuses(
            checkpoints,
            active_checkpoint_id=_checkpoint_id(active_checkpoint),
            completed_ids=completed_ids,
            skipped_ids=skipped_ids,
            failed_ids=failed_ids,
        ),
        'checkpoints': checkpoints,
        'directorRules': pack.get('directorRules') if isinstance(pack.get('directorRules'), dict) else {},
        'activeDirectorRules': active_rules,
        'flags': flags,
        'currentLocationId': _current_location_id(snapshot),
    }


def control_campaign_pack_progress(
    *,
    session_id: int,
    action: str,
    checkpoint_id: str | None = None,
    reason: str | None = None,
) -> CampaignPackControlResult:
    session, snapshot, pack, checkpoints, flags = _mutable_session_pack_state(session_id)
    normalized_action = _status_key(action)
    if normalized_action not in {'advance', 'skip', 'fail', 'rewind', 'override', 'set'}:
        raise CampaignPackProgressError('Campaign pack checkpoint action must be advance, skip, fail, rewind, or override.')

    active_checkpoint, completed_ids, skipped_ids, failed_ids = _current_pack_progress(pack, flags, checkpoints)
    previous_active_id = _checkpoint_id(active_checkpoint)
    target_checkpoint = _checkpoint_by_id(checkpoints, checkpoint_id)
    if checkpoint_id and not target_checkpoint:
        raise CampaignPackProgressError('checkpointId must reference an imported checkpoint.')

    control_reason = reason or f'manual_{normalized_action}'
    if normalized_action == 'advance':
        if active_checkpoint:
            _add_unique_id(completed_ids, _checkpoint_id(active_checkpoint))
        next_checkpoint = target_checkpoint or _next_checkpoint(active_checkpoint, checkpoints, completed_ids, skipped_ids, failed_ids)
    elif normalized_action == 'skip':
        if active_checkpoint:
            active_id = _checkpoint_id(active_checkpoint)
            _add_unique_id(completed_ids, active_id)
            _add_unique_id(skipped_ids, active_id)
        next_checkpoint = target_checkpoint or _next_checkpoint(active_checkpoint, checkpoints, completed_ids, skipped_ids, failed_ids)
    elif normalized_action == 'fail':
        if active_checkpoint:
            _add_unique_id(failed_ids, _checkpoint_id(active_checkpoint))
        next_checkpoint = target_checkpoint or _failure_next_checkpoint(active_checkpoint, checkpoints, completed_ids, skipped_ids, failed_ids)
    elif normalized_action == 'rewind':
        if target_checkpoint:
            target_id = _checkpoint_id(target_checkpoint)
            completed_ids = [value for value in completed_ids if _id_key(value) != _id_key(target_id)]
            skipped_ids = [value for value in skipped_ids if _id_key(value) != _id_key(target_id)]
            failed_ids = [value for value in failed_ids if _id_key(value) != _id_key(target_id)]
            next_checkpoint = target_checkpoint
        elif completed_ids:
            target_id = completed_ids[-1]
            completed_ids = completed_ids[:-1]
            skipped_ids = [value for value in skipped_ids if _id_key(value) != _id_key(target_id)]
            failed_ids = [value for value in failed_ids if _id_key(value) != _id_key(target_id)]
            next_checkpoint = _checkpoint_by_id(checkpoints, target_id)
        elif failed_ids:
            target_id = failed_ids[-1]
            failed_ids = failed_ids[:-1]
            skipped_ids = [value for value in skipped_ids if _id_key(value) != _id_key(target_id)]
            next_checkpoint = _checkpoint_by_id(checkpoints, target_id)
        else:
            next_checkpoint = active_checkpoint or _first_incomplete_checkpoint(checkpoints, completed_ids, skipped_ids, failed_ids)
    else:
        if not target_checkpoint:
            raise CampaignPackProgressError('checkpointId is required when overriding the active checkpoint.')
        target_id = _checkpoint_id(target_checkpoint)
        completed_ids = [value for value in completed_ids if _id_key(value) != _id_key(target_id)]
        skipped_ids = [value for value in skipped_ids if _id_key(value) != _id_key(target_id)]
        failed_ids = [value for value in failed_ids if _id_key(value) != _id_key(target_id)]
        next_checkpoint = target_checkpoint

    active_checkpoint_id = _checkpoint_id(next_checkpoint) if next_checkpoint else None
    previous_completed_ids = _unique_ids(
        _ids_from(pack, 'completedCheckpointIds', 'completed_checkpoint_ids')
        or _ids_from(flags, 'campaignPackCompletedCheckpointIds', 'completedCheckpointIds')
    )
    previous_skipped_ids = _unique_ids(
        _ids_from(pack, 'skippedCheckpointIds', 'skipped_checkpoint_ids')
        or _ids_from(flags, 'campaignPackSkippedCheckpointIds', 'skippedCheckpointIds')
    )
    previous_failed_ids = _unique_ids(
        _ids_from(pack, 'failedCheckpointIds', 'failed_checkpoint_ids')
        or _ids_from(flags, 'campaignPackFailedCheckpointIds', 'failedCheckpointIds')
    )
    changed = (
        active_checkpoint_id != (previous_active_id or None)
        or completed_ids != previous_completed_ids
        or skipped_ids != previous_skipped_ids
        or failed_ids != previous_failed_ids
    )

    now = utc_now().isoformat()
    flags['campaignPackActiveCheckpointId'] = active_checkpoint_id
    flags['campaignPackCompletedCheckpointIds'] = completed_ids
    flags['campaignPackSkippedCheckpointIds'] = skipped_ids
    flags['campaignPackFailedCheckpointIds'] = failed_ids
    flags['campaignPackLastManualControl'] = {
        'action': normalized_action,
        'checkpointId': active_checkpoint_id,
        'previousCheckpointId': previous_active_id,
        'reason': control_reason,
        'at': now,
    }
    pack['activeCheckpointId'] = active_checkpoint_id
    pack['completedCheckpointIds'] = completed_ids
    pack['skippedCheckpointIds'] = skipped_ids
    pack['failedCheckpointIds'] = failed_ids
    pack['activeDirectorRules'] = _active_director_rules(pack, next_checkpoint)
    pack['lastProgressReason'] = control_reason
    pack['lastManualControlAt'] = now
    snapshot['flags'] = flags
    snapshot['campaignPack'] = pack
    session.state_snapshot = safe_json_dumps(snapshot, {})
    return CampaignPackControlResult(changed, active_checkpoint_id, completed_ids, skipped_ids, control_reason, failed_ids)


def _session_pack_state(session_id: int) -> tuple[dict, dict, list[dict], dict]:
    session = db.session.get(Session, session_id)
    if not session:
        raise CampaignPackProgressError('Session not found.', error_code='session_not_found', status_code=404)
    snapshot = safe_json_loads(session.state_snapshot, {})
    if not isinstance(snapshot, dict):
        raise CampaignPackProgressError('Session does not have structured state.')
    pack = snapshot.get('campaignPack') if isinstance(snapshot.get('campaignPack'), dict) else {}
    pack_id = _text(_first(pack, 'packId', 'pack_id'))
    checkpoints = [checkpoint for checkpoint in (pack.get('checkpoints') or []) if isinstance(checkpoint, dict)]
    if not pack_id or not checkpoints:
        raise CampaignPackProgressError(
            'Session does not have an imported campaign pack.',
            error_code='campaign_pack_not_found',
            status_code=404,
        )
    flags = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}
    return snapshot, pack, checkpoints, flags


def _mutable_session_pack_state(session_id: int) -> tuple[Session, dict, dict, list[dict], dict]:
    session = db.session.get(Session, session_id)
    if not session:
        raise CampaignPackProgressError('Session not found.', error_code='session_not_found', status_code=404)
    snapshot = safe_json_loads(session.state_snapshot, {})
    if not isinstance(snapshot, dict):
        raise CampaignPackProgressError('Session does not have structured state.')
    pack = snapshot.get('campaignPack') if isinstance(snapshot.get('campaignPack'), dict) else {}
    pack_id = _text(_first(pack, 'packId', 'pack_id'))
    checkpoints = [checkpoint for checkpoint in (pack.get('checkpoints') or []) if isinstance(checkpoint, dict)]
    if not pack_id or not checkpoints:
        raise CampaignPackProgressError(
            'Session does not have an imported campaign pack.',
            error_code='campaign_pack_not_found',
            status_code=404,
        )
    flags = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}
    snapshot['flags'] = flags
    snapshot['campaignPack'] = pack
    return session, snapshot, pack, checkpoints, flags


def _current_pack_progress(pack: dict, flags: dict, checkpoints: list[dict]) -> tuple[dict | None, list[str], list[str], list[str]]:
    completed_ids = _unique_ids(
        _ids_from(pack, 'completedCheckpointIds', 'completed_checkpoint_ids')
        or _ids_from(flags, 'campaignPackCompletedCheckpointIds', 'completedCheckpointIds')
    )
    skipped_ids = _unique_ids(
        _ids_from(pack, 'skippedCheckpointIds', 'skipped_checkpoint_ids')
        or _ids_from(flags, 'campaignPackSkippedCheckpointIds', 'skippedCheckpointIds')
    )
    failed_ids = _unique_ids(
        _ids_from(pack, 'failedCheckpointIds', 'failed_checkpoint_ids')
        or _ids_from(flags, 'campaignPackFailedCheckpointIds', 'failedCheckpointIds')
    )
    active_id = _text(
        _first(pack, 'activeCheckpointId', 'active_checkpoint_id', 'currentCheckpointId', 'current_checkpoint_id')
        or _first(flags, 'campaignPackActiveCheckpointId', 'activeCheckpointId')
    )
    active_checkpoint = _checkpoint_by_id(checkpoints, active_id)
    if not active_checkpoint or _checkpoint_id(active_checkpoint) in _terminal_checkpoint_ids(completed_ids, skipped_ids, failed_ids):
        active_checkpoint = _first_incomplete_checkpoint(checkpoints, completed_ids, skipped_ids, failed_ids)
    return active_checkpoint, completed_ids, skipped_ids, failed_ids


def _checkpoint_completion_reason(
    checkpoint: dict,
    *,
    pack: dict,
    snapshot: dict,
    triggered_segment_refs: set[str],
) -> str | None:
    completion = _completion_spec(checkpoint)
    explicit_completion = bool(completion)

    location_ids = _ids_from(completion, 'locationId', 'locationIds', 'location_id', 'location_ids')
    if not location_ids and not explicit_completion:
        location_ids = _ids_from(
            checkpoint,
            'locationId',
            'locationIds',
            'location_id',
            'location_ids',
            'locations',
        )
    current_location_id = _current_location_id(snapshot)
    location_completion_allowed = explicit_completion or not _checkpoint_has_non_location_completion_cues(checkpoint)
    if (
        location_completion_allowed
        and location_ids
        and current_location_id
        and _id_key(current_location_id) in {_id_key(location_id) for location_id in location_ids}
    ):
        return 'checkpoint_location_reached'

    segment_ids = _ids_from(
        completion,
        'segmentId',
        'segmentIds',
        'segment_id',
        'segment_ids',
        'triggeredSegmentIds',
        'triggered_segment_ids',
    )
    if not segment_ids and not explicit_completion:
        segment_ids = _ids_from(
            checkpoint,
            'segmentId',
            'segmentIds',
            'segment_id',
            'segment_ids',
            'triggeredSegmentIds',
            'triggered_segment_ids',
        )
    if segment_ids and {_id_key(segment_id) for segment_id in segment_ids}.intersection(triggered_segment_refs):
        return 'checkpoint_segment_triggered'

    objective_ids = _ids_from(
        completion,
        'objectiveId',
        'objectiveIds',
        'objective_id',
        'objective_ids',
        'questObjectiveIds',
        'quest_objective_ids',
    )
    if not objective_ids and not explicit_completion:
        objective_ids = _ids_from(
            checkpoint,
            'objectiveId',
            'objectiveIds',
            'objective_id',
            'objective_ids',
            'questObjectiveIds',
            'quest_objective_ids',
        )
    quest_ids = _ids_from(completion, 'questId', 'questIds', 'quest_id', 'quest_ids')
    objective_quest_scope_ids = quest_ids or _ids_from(checkpoint, 'questId', 'questIds', 'quest_id', 'quest_ids', 'quests')
    if objective_ids and _objectives_completed(snapshot, objective_ids=objective_ids, quest_ids=objective_quest_scope_ids):
        return 'checkpoint_objective_completed'

    if not quest_ids and not explicit_completion:
        quest_ids = _ids_from(
            checkpoint,
            'questId',
            'questIds',
            'quest_id',
            'quest_ids',
            'quests',
        )
    if quest_ids and _quests_completed(snapshot, quest_ids):
        return 'checkpoint_quest_completed'

    encounter_ids = _ids_from(completion, 'encounterId', 'encounterIds', 'encounter_id', 'encounter_ids')
    if not encounter_ids and not explicit_completion:
        encounter_ids = _ids_from(
            checkpoint,
            'encounterId',
            'encounterIds',
            'encounter_id',
            'encounter_ids',
            'encounters',
        )
    if encounter_ids and _encounters_completed(pack=pack, snapshot=snapshot, encounter_ids=encounter_ids, completion=completion):
        return 'checkpoint_encounter_completed'

    return None


def _checkpoint_failure_reason(
    checkpoint: dict,
    *,
    pack: dict,
    snapshot: dict,
) -> str | None:
    failure = _failure_spec(checkpoint)
    quest_ids = _ids_from(failure, 'questId', 'questIds', 'quest_id', 'quest_ids') or _ids_from(
        checkpoint,
        'questId',
        'questIds',
        'quest_id',
        'quest_ids',
        'quests',
    )
    if quest_ids and _quests_failed(snapshot, quest_ids):
        return 'checkpoint_quest_failed'

    objective_ids = _ids_from(
        failure,
        'objectiveId',
        'objectiveIds',
        'objective_id',
        'objective_ids',
        'questObjectiveIds',
        'quest_objective_ids',
    ) or _ids_from(checkpoint, 'failedObjectiveIds', 'failed_objective_ids')
    if objective_ids and _objectives_failed(snapshot, objective_ids=objective_ids, quest_ids=quest_ids):
        return 'checkpoint_objective_failed'

    encounter_ids = _ids_from(failure, 'encounterId', 'encounterIds', 'encounter_id', 'encounter_ids') or _ids_from(
        checkpoint,
        'encounterId',
        'encounterIds',
        'encounter_id',
        'encounter_ids',
        'encounters',
    )
    if encounter_ids and _encounters_failed(pack=pack, snapshot=snapshot, encounter_ids=encounter_ids):
        return 'checkpoint_encounter_failed'

    return None


def _completion_spec(checkpoint: dict) -> dict:
    for key in ('completeWhen', 'complete_when', 'completion', 'trigger', 'advanceWhen', 'advance_when'):
        value = checkpoint.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _checkpoint_has_non_location_completion_cues(checkpoint: dict) -> bool:
    return bool(
        _ids_from(
            checkpoint,
            'segmentId',
            'segmentIds',
            'segment_id',
            'segment_ids',
            'triggeredSegmentIds',
            'triggered_segment_ids',
        )
        or _ids_from(
            checkpoint,
            'objectiveId',
            'objectiveIds',
            'objective_id',
            'objective_ids',
            'questObjectiveIds',
            'quest_objective_ids',
        )
        or _ids_from(checkpoint, 'encounterId', 'encounterIds', 'encounter_id', 'encounter_ids', 'encounters')
    )


def _failure_spec(checkpoint: dict) -> dict:
    for key in ('failWhen', 'fail_when', 'failure', 'failedWhen', 'failed_when'):
        value = checkpoint.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _matching_downstream_checkpoint(
    *,
    active_checkpoint: dict,
    checkpoints: list[dict],
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
    snapshot: dict,
) -> dict | None:
    current_location_id = _current_location_id(snapshot)
    if not current_location_id:
        return None
    next_ids = _unique_ids([*_next_checkpoint_ids(active_checkpoint), *_alternate_checkpoint_ids(active_checkpoint)])
    if not next_ids:
        return None
    terminal_keys = {_id_key(checkpoint_id) for checkpoint_id in _terminal_checkpoint_ids(completed_ids, skipped_ids, failed_ids)}
    for checkpoint_id in next_ids:
        candidate = _checkpoint_by_id(checkpoints, checkpoint_id)
        if not candidate or _id_key(_checkpoint_id(candidate)) in terminal_keys:
            continue
        if not _checkpoint_available(candidate, completed_ids, skipped_ids, failed_ids):
            continue
        location_ids = _ids_from(candidate, 'locationId', 'locationIds', 'location_id', 'location_ids', 'locations')
        if _id_key(current_location_id) in {_id_key(location_id) for location_id in location_ids}:
            return candidate
    return None


def _next_checkpoint(
    active_checkpoint: dict | None,
    checkpoints: list[dict],
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
) -> dict | None:
    terminal_keys = {_id_key(checkpoint_id) for checkpoint_id in _terminal_checkpoint_ids(completed_ids, skipped_ids, failed_ids)}
    if not active_checkpoint:
        return _first_incomplete_checkpoint(checkpoints, completed_ids, skipped_ids, failed_ids)
    for checkpoint_id in _next_checkpoint_ids(active_checkpoint):
        candidate = _checkpoint_by_id(checkpoints, checkpoint_id)
        if (
            candidate
            and _id_key(_checkpoint_id(candidate)) not in terminal_keys
            and _checkpoint_available(candidate, completed_ids, skipped_ids, failed_ids)
        ):
            return candidate
    try:
        active_index = checkpoints.index(active_checkpoint)
    except ValueError:
        active_index = -1
    for checkpoint in checkpoints[active_index + 1 :]:
        if (
            _id_key(_checkpoint_id(checkpoint)) not in terminal_keys
            and _checkpoint_available(checkpoint, completed_ids, skipped_ids, failed_ids)
            and not _checkpoint_optional(checkpoint)
        ):
            return checkpoint
    first_later_optional = next(
        (
            checkpoint
            for checkpoint in checkpoints[active_index + 1 :]
            if _id_key(_checkpoint_id(checkpoint)) not in terminal_keys
            and _checkpoint_available(checkpoint, completed_ids, skipped_ids, failed_ids)
            and _checkpoint_optional(checkpoint)
        ),
        None,
    )
    return first_later_optional


def _failure_next_checkpoint(
    active_checkpoint: dict | None,
    checkpoints: list[dict],
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
) -> dict | None:
    if active_checkpoint:
        terminal_keys = {_id_key(checkpoint_id) for checkpoint_id in _terminal_checkpoint_ids(completed_ids, skipped_ids, failed_ids)}
        for checkpoint_id in _failure_checkpoint_ids(active_checkpoint):
            candidate = _checkpoint_by_id(checkpoints, checkpoint_id)
            if (
                candidate
                and _id_key(_checkpoint_id(candidate)) not in terminal_keys
                and _checkpoint_available(candidate, completed_ids, skipped_ids, failed_ids)
            ):
                return candidate
    return _next_checkpoint(active_checkpoint, checkpoints, completed_ids, skipped_ids, failed_ids)


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


def _first_incomplete_checkpoint(
    checkpoints: list[dict],
    completed_ids: list[str],
    skipped_ids: list[str],
    failed_ids: list[str],
) -> dict | None:
    terminal_keys = {_id_key(checkpoint_id) for checkpoint_id in _terminal_checkpoint_ids(completed_ids, skipped_ids, failed_ids)}
    first_optional: dict | None = None
    for checkpoint in checkpoints:
        if _id_key(_checkpoint_id(checkpoint)) in terminal_keys:
            continue
        if not _checkpoint_available(checkpoint, completed_ids, skipped_ids, failed_ids):
            continue
        if _checkpoint_optional(checkpoint):
            first_optional = first_optional or checkpoint
            continue
        return checkpoint
    return first_optional


def _checkpoint_by_id(checkpoints: list[dict], checkpoint_id: str | None) -> dict | None:
    if not checkpoint_id:
        return None
    checkpoint_key = _id_key(checkpoint_id)
    return next((checkpoint for checkpoint in checkpoints if _id_key(_checkpoint_id(checkpoint)) == checkpoint_key), None)


def _checkpoint_id(checkpoint: dict | None) -> str | None:
    if not isinstance(checkpoint, dict):
        return None
    return _text(_first(checkpoint, 'id', 'checkpointId', 'checkpoint_id')) or None


def _current_location_id(snapshot: dict) -> str | None:
    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    return _text(_first(scene, 'locationId', 'location_id')) or None


def _quests(snapshot: dict) -> list[dict]:
    return [quest for quest in (snapshot.get('quests') or []) if isinstance(quest, dict)]


def _quests_completed(snapshot: dict, quest_ids: list[str]) -> bool:
    quest_keys = {_id_key(quest_id) for quest_id in quest_ids}
    for quest in _quests(snapshot):
        quest_id = _text(_first(quest, 'id', 'questId', 'quest_id'))
        if _id_key(quest_id) in quest_keys and _status_key(quest.get('status')) in COMPLETED_STATUSES:
            return True
    return False


def _quests_failed(snapshot: dict, quest_ids: list[str]) -> bool:
    quest_keys = {_id_key(quest_id) for quest_id in quest_ids}
    for quest in _quests(snapshot):
        quest_id = _text(_first(quest, 'id', 'questId', 'quest_id'))
        if _id_key(quest_id) in quest_keys and _status_key(quest.get('status')) in FAILED_STATUSES:
            return True
    return False


def _objectives_completed(snapshot: dict, *, objective_ids: list[str], quest_ids: list[str]) -> bool:
    objective_keys = {_id_key(objective_id) for objective_id in objective_ids}
    quest_keys = {_id_key(quest_id) for quest_id in quest_ids}
    for quest in _quests(snapshot):
        quest_id = _text(_first(quest, 'id', 'questId', 'quest_id'))
        if quest_keys and _id_key(quest_id) not in quest_keys:
            continue
        for objective in quest.get('objectives') or []:
            if not isinstance(objective, dict):
                continue
            objective_id = _text(_first(objective, 'id', 'objectiveId', 'objective_id'))
            if _id_key(objective_id) in objective_keys and _status_key(objective.get('status')) in COMPLETED_STATUSES:
                return True
    return False


def _objectives_failed(snapshot: dict, *, objective_ids: list[str], quest_ids: list[str]) -> bool:
    objective_keys = {_id_key(objective_id) for objective_id in objective_ids}
    quest_keys = {_id_key(quest_id) for quest_id in quest_ids}
    for quest in _quests(snapshot):
        quest_id = _text(_first(quest, 'id', 'questId', 'quest_id'))
        if quest_keys and _id_key(quest_id) not in quest_keys:
            continue
        for objective in quest.get('objectives') or []:
            if not isinstance(objective, dict):
                continue
            objective_id = _text(_first(objective, 'id', 'objectiveId', 'objective_id'))
            if _id_key(objective_id) in objective_keys and _status_key(objective.get('status')) in FAILED_STATUSES:
                return True
    return False


def _combat_flags(snapshot: dict) -> dict:
    combat = snapshot.get('combat') if isinstance(snapshot.get('combat'), dict) else {}
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    return flags


def _combat_has_ended(snapshot: dict) -> bool:
    combat = snapshot.get('combat') if isinstance(snapshot.get('combat'), dict) else {}
    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    return _status_key(combat.get('status')) in {'ended', 'resolved'} or _status_key(scene.get('combatState')) == 'resolved'


def _completed_encounter_ids(snapshot: dict) -> list[str]:
    flags = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}
    return _unique_ids(
        _ids_from(flags, 'campaignPackCompletedEncounterIds', 'completedEncounterIds')
        + _ids_from(_combat_flags(snapshot), 'campaignPackCompletedEncounterIds', 'completedEncounterIds')
    )


def _encounter_by_id(pack: dict, encounter_id: str) -> dict:
    catalog = pack.get('catalog') if isinstance(pack.get('catalog'), dict) else {}
    encounters = [encounter for encounter in catalog.get('encounters') or [] if isinstance(encounter, dict)]
    encounter_key = _id_key(encounter_id)
    return next((encounter for encounter in encounters if _id_key(_first(encounter, 'id', 'encounterId', 'encounter_id')) == encounter_key), {})


def _encounter_completion_spec(pack: dict, encounter_id: str) -> dict:
    encounter = _encounter_by_id(pack, encounter_id)
    return encounter.get('completion') if isinstance(encounter.get('completion'), dict) else {}


def _allowed_encounter_outcomes(pack: dict, encounter_ids: list[str], completion: dict) -> set[str]:
    allowed = set(_ids_from(completion, 'anyOf', 'any_of', 'outcomes', 'allowedOutcomes', 'allowed_outcomes'))
    for encounter_id in encounter_ids:
        allowed.update(
            _ids_from(
                _encounter_completion_spec(pack, encounter_id),
                'anyOf',
                'any_of',
                'outcomes',
                'allowedOutcomes',
                'allowed_outcomes',
            )
        )
    return {_status_key(value) for value in allowed if value}


def _encounters_completed(*, pack: dict, snapshot: dict, encounter_ids: list[str], completion: dict) -> bool:
    encounter_keys = {_id_key(encounter_id) for encounter_id in encounter_ids}
    if encounter_keys.intersection({_id_key(encounter_id) for encounter_id in _completed_encounter_ids(snapshot)}):
        return True

    flags = _combat_flags(snapshot)
    active_encounter_id = _text(_first(flags, 'campaignPackEncounterId', 'campaign_pack_encounter_id'))
    if not active_encounter_id or _id_key(active_encounter_id) not in encounter_keys or not _combat_has_ended(snapshot):
        return False

    end_reason = _status_key(_first(flags, 'endReason', 'end_reason'))
    if end_reason in COMBAT_FAILED_REASONS:
        return False

    allowed = _allowed_encounter_outcomes(pack, encounter_ids, completion)
    if not allowed:
        return True
    return bool(allowed.intersection(COMBAT_COMPLETE_REASONS.get(end_reason, {end_reason})))


def _encounters_failed(*, pack: dict, snapshot: dict, encounter_ids: list[str]) -> bool:
    del pack
    encounter_keys = {_id_key(encounter_id) for encounter_id in encounter_ids}
    flags = _combat_flags(snapshot)
    active_encounter_id = _text(_first(flags, 'campaignPackEncounterId', 'campaign_pack_encounter_id'))
    if not active_encounter_id or _id_key(active_encounter_id) not in encounter_keys or not _combat_has_ended(snapshot):
        return False
    return _status_key(_first(flags, 'endReason', 'end_reason')) in COMBAT_FAILED_REASONS


def _terminal_checkpoint_ids(completed_ids: list[str], skipped_ids: list[str], failed_ids: list[str]) -> list[str]:
    return _unique_ids([*completed_ids, *skipped_ids, *failed_ids])


def _checkpoint_optional(checkpoint: dict) -> bool:
    return _truthy(_first(checkpoint, 'optional', 'isOptional', 'is_optional'))


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
    policy = _status_key(_first(checkpoint, 'prerequisitePolicy', 'prerequisite_policy')) or 'completed_or_skipped'
    if policy in {'terminal', 'resolved', 'completed_or_skipped_or_failed'}:
        satisfied = {_id_key(value) for value in _terminal_checkpoint_ids(completed_ids, skipped_ids, failed_ids)}
    elif policy in {'completed_or_skipped', 'skipped_allowed'}:
        satisfied = {_id_key(value) for value in _unique_ids([*completed_ids, *skipped_ids])}
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
        checkpoint_id = _checkpoint_id(checkpoint)
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


def _active_director_rules(pack: dict, checkpoint: dict | None) -> dict:
    rules = pack.get('directorRules') if isinstance(pack.get('directorRules'), dict) else {}
    checkpoint_rules = checkpoint.get('directorRules') if isinstance(checkpoint, dict) and isinstance(checkpoint.get('directorRules'), dict) else {}
    return {**rules, **checkpoint_rules}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _status_key(value) in {'1', 'true', 'yes', 'y', 'on', 'optional'}


def _triggered_segment_refs(*, campaign_id: int, pack_id: str, triggered_segments: list[dict]) -> set[str]:
    refs: set[str] = set()
    for payload in triggered_segments:
        refs.update(_payload_segment_refs(payload))

    segments = CampaignSegment.query.filter_by(campaign_id=campaign_id, is_triggered=True).all()
    for segment in segments:
        tags = _ids_from({'tags': segment.tags}, 'tags')
        if 'campaign_pack' not in tags and f'pack:{pack_id}' not in tags:
            continue
        refs.add(_id_key(segment.segment_id))
        refs.add(_id_key(segment.title))
        refs.add(_id_key(stable_slug(segment.title)))
        for tag in tags:
            if tag.startswith('pack_segment:'):
                refs.add(_id_key(tag.split(':', 1)[1]))
        trigger_spec = parse_trigger_spec(segment.trigger_condition)
        raw = trigger_spec.raw if isinstance(trigger_spec.raw, dict) else {}
        refs.update(_ids_from(raw, 'packSegmentId', 'pack_segment_id', 'segmentId', 'segment_id'))
    return {_id_key(ref) for ref in refs if ref}


def _payload_segment_refs(payload: dict) -> set[str]:
    refs = set(_ids_from(payload, 'segment_id', 'segmentId', 'packSegmentId', 'pack_segment_id'))
    if payload.get('title'):
        refs.add(_text(payload.get('title')))
        refs.add(stable_slug(payload.get('title')))
    trigger_spec = payload.get('trigger_spec') if isinstance(payload.get('trigger_spec'), dict) else {}
    raw = trigger_spec.get('raw') if isinstance(trigger_spec.get('raw'), dict) else {}
    refs.update(_ids_from(raw, 'packSegmentId', 'pack_segment_id', 'segmentId', 'segment_id'))
    return {_id_key(ref) for ref in refs if ref}


def _ids_from(record: dict, *keys: str) -> list[str]:
    if not isinstance(record, dict):
        return []
    values: list[Any] = []
    for key in keys:
        if key in record:
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


def _add_unique_id(values: list[str], value: str | None) -> None:
    text = _text(value)
    if not text:
        return
    if _id_key(text) not in {_id_key(existing) for existing in values}:
        values.append(text)


def _first(record: dict, *keys: str) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        if key in record:
            return record.get(key)
    return None


def _text(value: Any) -> str:
    return str(value or '').strip()


def _id_key(value: Any) -> str:
    return _text(value).lower()


def _status_key(value: Any) -> str:
    return _text(value).lower().replace(' ', '_')
