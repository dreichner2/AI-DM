from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.combat.pipeline import sync_combat_encounter_record
from aidm_server.database import db
from aidm_server.errors import error_response
from aidm_server.game_state.application.applier import apply_state_changes, persist_state_to_database
from aidm_server.game_state.models import state_snapshot_for_session
from aidm_server.game_state.validation.validator import validate_state_changes, validated_changes_for_application
from aidm_server.models import Campaign, Player, Session, SessionStateMutationAudit, safe_json_dumps, safe_json_loads
from aidm_server.time_utils import utc_now
from aidm_server.turn_coordinator import session_turn_coordinator
from aidm_server.workspace_access import current_account_id, current_account_is_workspace_admin


STATE_MUTATION_AUDIT_LIMIT = 50
SNAPSHOT_DIFF_LIMIT = 80
SNAPSHOT_DIFF_DEPTH_LIMIT = 5
SNAPSHOT_DIFF_VALUE_LIMIT = 160


@dataclass
class SessionStateMutationPlan:
    changes: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionStateMutationResult:
    session_obj: Session | None
    state: dict[str, Any]
    validation: dict[str, Any]
    applied_changes: list[dict[str, Any]]
    metadata: dict[str, Any]
    wait_ms: float
    previous_revision: int
    state_revision: int
    conflict: bool = False


@dataclass
class SessionSnapshotMetadataMutationResult:
    session_obj: Session | None
    state: dict[str, Any]
    metadata: dict[str, Any]
    wait_ms: float
    previous_revision: int
    state_revision: int
    changed: bool = False


MutationBuilder = Callable[[Session, dict[str, Any]], SessionStateMutationPlan | Sequence[Any]]
SnapshotMetadataMutator = Callable[[Session, dict[str, Any]], dict[str, Any] | None]
AfterPersistHook = Callable[[SessionStateMutationResult], None]
ProgressRefresher = Callable[[Session], dict[str, Any] | None]


def expected_state_revision_from_payload(payload: dict[str, Any]) -> int | None:
    raw_value = None
    for key in ('expectedStateRevision', 'expected_state_revision', 'stateRevision', 'state_revision'):
        candidate = payload.get(key)
        if candidate in (None, ''):
            continue
        raw_value = candidate
        break
    if raw_value in (None, ''):
        return None
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def state_conflict_response(result: SessionStateMutationResult):
    return error_response(
        'state_conflict',
        'Session state changed before this mutation could be applied. Refresh and retry.',
        409,
        {
            'expected_state_revision': result.previous_revision,
            'actual_state_revision': result.state_revision,
        },
    )


def _campaign_players(campaign: Campaign) -> list[Player]:
    return (
        Player.query.filter_by(workspace_id=campaign.workspace_id, campaign_id=campaign.campaign_id)
        .order_by(Player.player_id.asc())
        .all()
    )


def _state_revision(state: dict[str, Any]) -> int:
    raw_value = state.get('stateRevision')
    if raw_value in (None, ''):
        raw_value = state.get('state_revision')
    return max(0, int_or_default(raw_value, default=0))


def _mutation_actor() -> str:
    account_id = current_account_id()
    if account_id is None:
        return 'local_operator'
    role = 'admin' if current_account_is_workspace_admin() else 'player'
    return f'account:{account_id}:{role}'


def _mutation_actor_role(actor: str) -> str:
    if actor == 'local_operator':
        return 'local_operator'
    parts = actor.split(':')
    if len(parts) >= 3 and parts[0] == 'account':
        return parts[-1] or 'account'
    return actor.split(':', 1)[0] or 'unknown'


def _mutation_actor_account_id(actor: str) -> int | None:
    parts = actor.split(':')
    if len(parts) < 3 or parts[0] != 'account':
        return None
    try:
        return int(parts[1])
    except (TypeError, ValueError):
        return None


def _json_preview(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > SNAPSHOT_DIFF_VALUE_LIMIT:
            return value[:SNAPSHOT_DIFF_VALUE_LIMIT] + '...'
        return value
    if isinstance(value, list):
        return {'type': 'list', 'length': len(value)}
    if isinstance(value, dict):
        return {'type': 'object', 'keys': sorted(str(key) for key in value.keys())[:12]}
    return str(value)[:SNAPSHOT_DIFF_VALUE_LIMIT]


def _append_snapshot_diff(
    diffs: list[dict[str, Any]],
    path: str,
    before: Any,
    after: Any,
    *,
    depth: int = 0,
) -> None:
    if len(diffs) >= SNAPSHOT_DIFF_LIMIT or before == after:
        return
    if depth >= SNAPSHOT_DIFF_DEPTH_LIMIT:
        diffs.append({'path': path, 'before': _json_preview(before), 'after': _json_preview(after)})
        return
    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(set(before.keys()) | set(after.keys()), key=str)
        for key in keys:
            if len(diffs) >= SNAPSHOT_DIFF_LIMIT:
                return
            child_path = f'{path}.{key}' if path else str(key)
            _append_snapshot_diff(diffs, child_path, before.get(key), after.get(key), depth=depth + 1)
        return
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            diffs.append(
                {
                    'path': path,
                    'before': {'type': 'list', 'length': len(before)},
                    'after': {'type': 'list', 'length': len(after)},
                }
            )
            return
        for index, (before_item, after_item) in enumerate(zip(before, after)):
            if len(diffs) >= SNAPSHOT_DIFF_LIMIT:
                return
            _append_snapshot_diff(diffs, f'{path}[{index}]', before_item, after_item, depth=depth + 1)
        return
    diffs.append({'path': path, 'before': _json_preview(before), 'after': _json_preview(after)})


def snapshot_diff_summary(before_state: dict[str, Any], after_state: dict[str, Any]) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    _append_snapshot_diff(diffs, '', before_state, after_state)
    return diffs


def _audit_json(value: Any, default: Any) -> str:
    try:
        return json.dumps(value if value is not None else default, default=str)
    except (TypeError, ValueError):
        return json.dumps(default)


def record_session_state_mutation_audit(
    *,
    session_obj: Session,
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    source: str,
    actor: str,
    previous_revision: int,
    state_revision: int,
    applied_changes: list[dict[str, Any]] | None = None,
    rejected_count: int = 0,
    metadata: dict[str, Any] | None = None,
) -> SessionStateMutationAudit:
    applied = [change for change in (applied_changes or []) if isinstance(change, dict)]
    applied_change_ids = [str(change.get('id')) for change in applied if change.get('id')]
    audit = SessionStateMutationAudit(
        session_id=session_obj.session_id,
        campaign_id=session_obj.campaign_id,
        source=source[:120],
        actor=actor[:160],
        actor_account_id=_mutation_actor_account_id(actor),
        actor_role=_mutation_actor_role(actor)[:32],
        previous_revision=max(0, int(previous_revision or 0)),
        state_revision=max(0, int(state_revision or 0)),
        applied_change_count=len(applied),
        rejected_change_count=max(0, int(rejected_count or 0)),
        applied_change_ids_json=_audit_json(applied_change_ids, []),
        diff_json=_audit_json(snapshot_diff_summary(before_state, after_state), []),
        metadata_json=_audit_json(metadata or {}, {}),
        created_at=utc_now(),
    )
    db.session.add(audit)
    return audit


def _stamp_mutation_audit(
    state: dict[str, Any],
    *,
    source: str,
    actor: str,
    previous_revision: int,
    next_revision: int,
    applied_changes: list[dict[str, Any]],
    rejected_count: int,
    wait_ms: float,
) -> None:
    state['stateRevision'] = next_revision
    applied_change_ids = [str(change.get('id')) for change in applied_changes if isinstance(change, dict) and change.get('id')]
    audit_entry = {
        'at': utc_now().isoformat(),
        'source': source,
        'actor': actor,
        'previousRevision': previous_revision,
        'stateRevision': next_revision,
        'appliedChangeIds': applied_change_ids,
        'appliedChangeCount': len(applied_changes),
        'rejectedChangeCount': rejected_count,
        'lockWaitMs': round(wait_ms, 3),
    }
    state['lastMutation'] = audit_entry
    audit = state.get('stateMutationAudit') if isinstance(state.get('stateMutationAudit'), list) else []
    audit.append(audit_entry)
    state['stateMutationAudit'] = audit[-STATE_MUTATION_AUDIT_LIMIT:]


def _normalize_plan(plan: SessionStateMutationPlan | Sequence[Any]) -> SessionStateMutationPlan:
    if isinstance(plan, SessionStateMutationPlan):
        return plan
    return SessionStateMutationPlan(changes=list(plan))


def mutate_session_snapshot_metadata(
    session_id: int,
    *,
    mutate_snapshot: SnapshotMetadataMutator,
    source: str,
    change_type: str,
    actor: str | None = None,
) -> SessionSnapshotMetadataMutationResult:
    """Serialize and audit a non-gameplay mutation of the live session snapshot.

    Metadata such as session content settings is not represented by the gameplay
    state-change schema, but it still shares the same snapshot and revision
    stream.  This boundary reloads the snapshot after acquiring the turn lock,
    commits before releasing it, and records the same revision/audit evidence as
    gameplay mutations so a concurrent turn cannot be lost.
    """

    with session_turn_coordinator.serialized(session_id) as wait_ms:
        try:
            session_obj = db.session.get(Session, session_id)
            if session_obj is None:
                return SessionSnapshotMetadataMutationResult(
                    session_obj=None,
                    state={},
                    metadata={},
                    wait_ms=wait_ms,
                    previous_revision=0,
                    state_revision=0,
                )

            # The endpoint may have loaded this identity before waiting for an
            # active turn. Refresh inside the coordinator to prevent a stale
            # read/modify/write from replacing that turn's committed snapshot.
            db.session.refresh(session_obj)
            state = safe_json_loads(session_obj.state_snapshot, {})
            state = state if isinstance(state, dict) else {}
            before_state = deepcopy(state)
            previous_revision = _state_revision(state)
            metadata = mutate_snapshot(session_obj, state) or {}
            changed = state != before_state
            state_revision = previous_revision

            if changed:
                state_revision = previous_revision + 1
                applied_changes = [
                    {
                        'id': f'{change_type}.{state_revision}',
                        'type': change_type,
                    }
                ]
                diff_state = deepcopy(state)
                actor_label = actor or _mutation_actor()
                _stamp_mutation_audit(
                    state,
                    source=source,
                    actor=actor_label,
                    previous_revision=previous_revision,
                    next_revision=state_revision,
                    applied_changes=applied_changes,
                    rejected_count=0,
                    wait_ms=wait_ms,
                )
                session_obj.state_snapshot = safe_json_dumps(state, {})
                session_obj.updated_at = utc_now()
                record_session_state_mutation_audit(
                    session_obj=session_obj,
                    before_state=before_state,
                    after_state=diff_state,
                    source=source,
                    actor=actor_label,
                    previous_revision=previous_revision,
                    state_revision=state_revision,
                    applied_changes=applied_changes,
                    rejected_count=0,
                    metadata=metadata,
                )

            # The database commit is part of the coordination boundary. Releasing
            # the lock first would let the next request read the old snapshot.
            db.session.commit()
            return SessionSnapshotMetadataMutationResult(
                session_obj=session_obj,
                state=state,
                metadata=metadata,
                wait_ms=wait_ms,
                previous_revision=previous_revision,
                state_revision=state_revision,
                changed=changed,
            )
        except Exception:
            db.session.rollback()
            raise


def mutate_session_state(
    session_id: int,
    *,
    build_changes: MutationBuilder,
    source: str,
    expected_revision: int | None = None,
    actor: str | None = None,
    sync_combat: bool = False,
    refresh_progress: ProgressRefresher | None = None,
    after_persist: AfterPersistHook | None = None,
    reject_on_validation_error: bool = False,
) -> SessionStateMutationResult:
    with session_turn_coordinator.serialized(session_id) as wait_ms:
        session_obj = db.session.get(Session, session_id)
        if session_obj is None:
            return SessionStateMutationResult(
                session_obj=None,
                state={},
                validation={'accepted': [], 'rejected': []},
                applied_changes=[],
                metadata={},
                wait_ms=wait_ms,
                previous_revision=0,
                state_revision=0,
            )

        campaign = session_obj.campaign
        players = _campaign_players(campaign)
        state = state_snapshot_for_session(session_obj=session_obj, campaign=campaign, players=players)
        before_state = deepcopy(state)
        current_revision = _state_revision(state)
        if expected_revision is not None and expected_revision != current_revision:
            return SessionStateMutationResult(
                session_obj=session_obj,
                state=state,
                validation={'accepted': [], 'rejected': []},
                applied_changes=[],
                metadata={},
                wait_ms=wait_ms,
                previous_revision=expected_revision,
                state_revision=current_revision,
                conflict=True,
            )

        plan = _normalize_plan(build_changes(session_obj, state))
        validation = validate_state_changes(state=state, changes=plan.changes)
        if reject_on_validation_error and validation.get('rejected'):
            return SessionStateMutationResult(
                session_obj=session_obj,
                state=state,
                validation=validation,
                applied_changes=[],
                metadata=dict(plan.metadata),
                wait_ms=wait_ms,
                previous_revision=current_revision,
                state_revision=current_revision,
            )
        applied = validated_changes_for_application(validation)
        apply_result = apply_state_changes(state, applied)
        next_state = apply_result['nextState']
        applied_changes = apply_result['appliedChanges']
        rejected_count = len(validation.get('rejected') or [])
        next_revision = current_revision + 1 if applied_changes else current_revision
        actor_label = actor or _mutation_actor()
        gameplay_diff_state = deepcopy(next_state)
        _stamp_mutation_audit(
            next_state,
            source=source,
            actor=actor_label,
            previous_revision=current_revision,
            next_revision=next_revision,
            applied_changes=applied_changes,
            rejected_count=rejected_count,
            wait_ms=wait_ms,
        )
        persist_state_to_database(
            session_obj=session_obj,
            state=next_state,
            players_by_id={player.player_id: player for player in players},
        )
        if sync_combat:
            sync_combat_encounter_record(
                session_obj=session_obj,
                campaign=campaign,
                combat=next_state.get('combat') if isinstance(next_state.get('combat'), dict) else {},
            )
        progress = refresh_progress(session_obj) if refresh_progress else None
        metadata = dict(plan.metadata)
        if progress is not None:
            metadata['campaignPackProgress'] = progress
        record_session_state_mutation_audit(
            session_obj=session_obj,
            before_state=before_state,
            after_state=gameplay_diff_state,
            source=source,
            actor=actor_label,
            previous_revision=current_revision,
            state_revision=next_revision,
            applied_changes=applied_changes,
            rejected_count=rejected_count,
            metadata=metadata,
        )
        final_state = safe_json_loads(session_obj.state_snapshot, {})
        final_state = final_state if isinstance(final_state, dict) else next_state
        result = SessionStateMutationResult(
            session_obj=session_obj,
            state=final_state,
            validation=validation,
            applied_changes=applied_changes,
            metadata=metadata,
            wait_ms=wait_ms,
            previous_revision=current_revision,
            state_revision=_state_revision(final_state),
        )
        if after_persist:
            after_persist(result)
        db.session.commit()
        return result
