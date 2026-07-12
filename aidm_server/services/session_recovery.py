from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import secrets
from typing import Any

from aidm_server.database import db
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY
from aidm_server.models import DmTurn, Session, safe_json_dumps, safe_json_loads
from aidm_server.operator_audit import record_operator_action
from aidm_server.services.session_state_mutation import (
    SessionSnapshotMetadataMutationResult,
    mutate_session_snapshot_metadata,
)
from aidm_server.time_utils import utc_now


TURN_RECOVERY_GATE_KEY = 'turnRecoveryGate'
TURN_RECOVERY_GATE_REASON = 'post_dm_state_application_failed'
TURN_RECOVERY_RESOLUTIONS = frozenset(
    {'state_corrected', 'no_mechanical_change_required'}
)
OPERATOR_NOTE_MAX_LENGTH = 1000
MECHANICS_STATUS_NONE = 'none'
MECHANICS_STATUS_PARTIAL = 'partial'
MECHANICS_STATUSES = frozenset({MECHANICS_STATUS_NONE, MECHANICS_STATUS_PARTIAL})


class SessionRecoveryConflictError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        public_message: str,
        *,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(public_message)
        self.error_code = error_code
        self.public_message = public_message
        self.details = details or {}


@dataclass(frozen=True)
class SessionRecoveryResolutionResult:
    session_id: int
    turn_id: int
    resolution: str
    state_revision: int
    idempotent_replay: bool


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _pre_dm_applied_changes(turn: DmTurn) -> list[dict[str, Any]]:
    """Return persisted, already-committed pre-narration mechanics evidence."""

    metadata = safe_json_loads(turn.metadata_json, {})
    metadata = metadata if isinstance(metadata, dict) else {}
    pipeline = metadata.get(STATE_PIPELINE_METADATA_KEY)
    pipeline = pipeline if isinstance(pipeline, dict) else {}
    return [
        change
        for key in ('immediateAppliedChanges', 'combatAppliedChanges')
        for change in (pipeline.get(key) or [])
        if isinstance(change, dict)
    ]


def _mechanics_summary_from_turn(turn: DmTurn) -> dict[str, Any]:
    applied_changes = _pre_dm_applied_changes(turn)
    count = len(applied_changes)
    pre_dm_applied = count > 0
    return {
        'mechanics_status': (
            MECHANICS_STATUS_PARTIAL if pre_dm_applied else MECHANICS_STATUS_NONE
        ),
        # Compatibility summary: this means at least one authoritative mechanic
        # was applied, not that the complete post-DM state phase succeeded.
        'mechanics_applied': pre_dm_applied,
        'pre_dm_mechanics_applied': pre_dm_applied,
        'pre_dm_applied_change_count': count,
        'post_dm_mechanics_applied': False,
    }


def _mechanics_summary_from_gate(raw_gate: dict[str, Any]) -> dict[str, Any]:
    raw_count = _nonnegative_int(raw_gate.get('preDmAppliedChangeCount'))
    raw_status = str(raw_gate.get('mechanicsStatus') or '').strip().lower()
    if raw_status not in MECHANICS_STATUSES:
        raw_status = ''
    pre_dm_applied = bool(
        raw_gate.get('preDmMechanicsApplied')
        or raw_gate.get('mechanicsApplied')
        or (raw_count is not None and raw_count > 0)
        or raw_status == MECHANICS_STATUS_PARTIAL
    )
    mechanics_status = (
        MECHANICS_STATUS_PARTIAL if pre_dm_applied else MECHANICS_STATUS_NONE
    )
    return {
        'mechanicsStatus': mechanics_status,
        'mechanicsApplied': pre_dm_applied,
        'preDmMechanicsApplied': pre_dm_applied,
        # New gates always persist an exact count. Treat a compatibility gate
        # that only said mechanicsApplied=true as one-or-more, never as zero.
        'preDmAppliedChangeCount': (
            max(1, raw_count or 0) if pre_dm_applied else 0
        ),
        'postDmMechanicsApplied': False,
    }


def _snake_mechanics_summary_from_gate(gate: dict[str, Any]) -> dict[str, Any]:
    return {
        'mechanics_status': gate['mechanicsStatus'],
        'mechanics_applied': gate['mechanicsApplied'],
        'pre_dm_mechanics_applied': gate['preDmMechanicsApplied'],
        'pre_dm_applied_change_count': gate['preDmAppliedChangeCount'],
        'post_dm_mechanics_applied': gate['postDmMechanicsApplied'],
    }


def _gate_mechanics_fields(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        'mechanicsStatus': summary['mechanics_status'],
        'mechanicsApplied': summary['mechanics_applied'],
        'preDmMechanicsApplied': summary['pre_dm_mechanics_applied'],
        'preDmAppliedChangeCount': summary['pre_dm_applied_change_count'],
        'postDmMechanicsApplied': summary['post_dm_mechanics_applied'],
    }


def _mechanics_audit_evidence(turn: DmTurn) -> dict[str, Any]:
    """Keep stable identifiers in recovery audits; exact changes remain on the turn."""

    applied_changes = _pre_dm_applied_changes(turn)
    return {
        'pre_dm_applied_change_ids': [
            str(change['id']) for change in applied_changes if change.get('id') is not None
        ],
        'pre_dm_applied_change_types': [
            str(change['type'])
            for change in applied_changes
            if str(change.get('type') or '').strip()
        ],
    }


def _snapshot_dict(session_or_snapshot: Session | dict[str, Any] | str | None) -> dict[str, Any]:
    if isinstance(session_or_snapshot, Session):
        raw_snapshot: Any = session_or_snapshot.state_snapshot
    else:
        raw_snapshot = session_or_snapshot
    if isinstance(raw_snapshot, dict):
        return raw_snapshot
    snapshot = safe_json_loads(raw_snapshot, {})
    return snapshot if isinstance(snapshot, dict) else {}


def active_turn_recovery_gate(
    session_or_snapshot: Session | dict[str, Any] | str | None,
) -> dict[str, Any] | None:
    """Return only the safe, active projection of a persisted recovery gate."""

    raw_gate = _snapshot_dict(session_or_snapshot).get(TURN_RECOVERY_GATE_KEY)
    if not isinstance(raw_gate, dict) or raw_gate.get('status') != 'required':
        return None
    turn_id = _positive_int(raw_gate.get('turnId'))
    if turn_id is None:
        return None
    return {
        'status': 'required',
        'reason': TURN_RECOVERY_GATE_REASON,
        'turnId': turn_id,
        'narrationSaved': True,
        **_mechanics_summary_from_gate(raw_gate),
        'createdAt': str(raw_gate.get('createdAt') or ''),
    }


def recovery_required_details(gate: dict[str, Any]) -> dict[str, Any]:
    return {
        'turn_id': int(gate['turnId']),
        'narration_saved': True,
        'mechanics_status': gate['mechanicsStatus'],
        'mechanics_applied': gate['mechanicsApplied'],
        'pre_dm_mechanics_applied': gate['preDmMechanicsApplied'],
        'pre_dm_applied_change_count': gate['preDmAppliedChangeCount'],
        'post_dm_mechanics_applied': gate['postDmMechanicsApplied'],
        'recovery_required': True,
    }


def _turn_for_session(session_id: int, turn_id: int) -> DmTurn | None:
    turn = db.session.get(DmTurn, turn_id, populate_existing=True)
    if turn is None or int(turn.session_id) != int(session_id):
        return None
    return turn


def _post_dm_state(turn: DmTurn) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = safe_json_loads(turn.metadata_json, {})
    metadata = metadata if isinstance(metadata, dict) else {}
    post_dm_state = metadata.get('post_dm_state')
    post_dm_state = post_dm_state if isinstance(post_dm_state, dict) else {}
    return metadata, post_dm_state


def _operator_note_fingerprint(operator_note: str, *, salt: str) -> str:
    """Return a one-way replay key without persisting privileged note text."""

    return hashlib.sha256(
        f'{salt}\0{operator_note}'.encode('utf-8')
    ).hexdigest()


def mark_turn_recovery_required(turn: DmTurn) -> dict[str, Any]:
    """Persist-safe failed-turn metadata independently of the snapshot gate.

    The turn row is the fail-closed source of truth when adding the redundant
    snapshot gate fails. The caller owns the transaction.
    """

    mechanics_summary = _mechanics_summary_from_turn(turn)
    metadata, post_dm_state = _post_dm_state(turn)
    post_dm_state.update(
        {
            'status': 'failed',
            'narration_saved': bool(turn.dm_output),
            **mechanics_summary,
            'recovery_required': True,
        }
    )
    post_dm_state.pop('recovery_resolution', None)
    metadata['post_dm_state'] = post_dm_state
    metadata['canon_status'] = 'blocked_state_failure'
    turn.metadata_json = safe_json_dumps(metadata, {})
    turn.status = 'failed'
    created_at = turn.completed_at or turn.created_at
    return {
        'status': 'required',
        'reason': TURN_RECOVERY_GATE_REASON,
        'turnId': int(turn.turn_id),
        'narrationSaved': bool(turn.dm_output),
        **_gate_mechanics_fields(mechanics_summary),
        'createdAt': created_at.isoformat() if created_at is not None else '',
    }


def unresolved_turn_recovery_turn(session_id: int) -> DmTurn | None:
    """Find the oldest unresolved failed post-DM state phase for a session."""

    turns = (
        DmTurn.query.filter_by(session_id=session_id, status='failed')
        .filter(DmTurn.metadata_json.contains('post_dm_state'))
        .order_by(DmTurn.turn_id.asc())
        .all()
    )
    for turn in turns:
        _metadata, post_dm_state = _post_dm_state(turn)
        if (
            post_dm_state.get('status') == 'failed'
            and post_dm_state.get('narration_saved') is True
            and post_dm_state.get('recovery_required') is True
            and not isinstance(post_dm_state.get('recovery_resolution'), dict)
        ):
            return turn
    return None


def recovery_gate_from_failed_turn(turn: DmTurn) -> dict[str, Any] | None:
    """Build a safe blocking projection when the snapshot gate is absent."""

    _metadata, post_dm_state = _post_dm_state(turn)
    if not (
        post_dm_state.get('status') == 'failed'
        and post_dm_state.get('narration_saved') is True
        and post_dm_state.get('recovery_required') is True
        and not isinstance(post_dm_state.get('recovery_resolution'), dict)
    ):
        return None
    mechanics_summary = _mechanics_summary_from_turn(turn)
    created_at = turn.completed_at or turn.created_at
    return {
        'status': 'required',
        'reason': TURN_RECOVERY_GATE_REASON,
        'turnId': int(turn.turn_id),
        'narrationSaved': True,
        **_gate_mechanics_fields(mechanics_summary),
        'createdAt': created_at.isoformat() if created_at is not None else '',
    }


def activate_turn_recovery_gate(
    *,
    session_id: int,
    turn_id: int,
) -> SessionSnapshotMetadataMutationResult:
    """Fail closed after narration persists but both mechanics paths fail."""

    now = utc_now().isoformat()

    def mutate_snapshot(session_obj: Session, state: dict[str, Any]) -> dict[str, Any]:
        turn = _turn_for_session(session_obj.session_id, turn_id)
        if turn is None:
            raise SessionRecoveryConflictError(
                'session_recovery_invalid',
                'The failed turn could not be associated with this session.',
            )

        existing_gate = active_turn_recovery_gate(state)
        if existing_gate is not None and existing_gate['turnId'] != turn_id:
            raise SessionRecoveryConflictError(
                'session_recovery_conflict',
                'Another turn already requires Dungeon Master recovery.',
                details={'turn_id': int(existing_gate['turnId'])},
            )

        mechanics_summary = _mechanics_summary_from_turn(turn)
        state[TURN_RECOVERY_GATE_KEY] = {
            'status': 'required',
            'reason': TURN_RECOVERY_GATE_REASON,
            'turnId': turn_id,
            'narrationSaved': True,
            **_gate_mechanics_fields(mechanics_summary),
            'createdAt': (
                str(existing_gate.get('createdAt') or now)
                if existing_gate is not None
                else now
            ),
        }

        mark_turn_recovery_required(turn)
        return {
            'turn_id': turn_id,
            'reason': TURN_RECOVERY_GATE_REASON,
            'narration_saved': True,
            **mechanics_summary,
            **_mechanics_audit_evidence(turn),
            'recovery_required': True,
        }

    return mutate_session_snapshot_metadata(
        session_id,
        mutate_snapshot=mutate_snapshot,
        source='system.turn_recovery.activate',
        change_type='session.recovery.required',
        actor='system:turn_engine',
    )


def resolve_turn_recovery_gate(
    *,
    session_id: int,
    turn_id: int,
    resolution: str,
    operator_note: str,
) -> SessionRecoveryResolutionResult:
    normalized_resolution = str(resolution or '').strip()
    normalized_note = str(operator_note or '').strip()
    if normalized_resolution not in TURN_RECOVERY_RESOLUTIONS:
        raise ValueError('Unsupported recovery resolution.')
    if not normalized_note or len(normalized_note) > OPERATOR_NOTE_MAX_LENGTH:
        raise ValueError('Operator note is outside the supported bounds.')

    idempotent_replay = False

    def mutate_snapshot(session_obj: Session, state: dict[str, Any]) -> dict[str, Any]:
        nonlocal idempotent_replay
        turn = _turn_for_session(session_obj.session_id, turn_id)
        gate = active_turn_recovery_gate(state)

        # The turn row remains authoritative if the redundant snapshot gate
        # could not be written. Resolve that state in this same coordinated
        # transaction so the DM does not need to provoke another player action
        # merely to repair the snapshot first.
        if gate is None and turn is not None:
            gate = recovery_gate_from_failed_turn(turn)
            if gate is not None:
                turn.status = 'failed'

        if gate is None:
            if turn is None:
                raise SessionRecoveryConflictError(
                    'session_recovery_not_required',
                    'This session does not have an active recovery gate.',
                )
            _metadata, post_dm_state = _post_dm_state(turn)
            prior_resolution = post_dm_state.get('recovery_resolution')
            if isinstance(prior_resolution, dict):
                same_resolution = (
                    prior_resolution.get('resolution') == normalized_resolution
                )
                persisted_fingerprint = str(
                    prior_resolution.get('operator_note_fingerprint') or ''
                )
                fingerprint_salt = str(
                    prior_resolution.get('operator_note_fingerprint_salt') or ''
                )
                same_note = (
                    bool(persisted_fingerprint and fingerprint_salt)
                    and hmac.compare_digest(
                        persisted_fingerprint,
                        _operator_note_fingerprint(
                            normalized_note,
                            salt=fingerprint_salt,
                        ),
                    )
                )
                if not same_resolution or not same_note:
                    raise SessionRecoveryConflictError(
                        'session_recovery_already_resolved',
                        'This turn was already resolved with a different recovery decision or operator note.',
                        details={
                            'turn_id': turn_id,
                            'resolution': prior_resolution.get('resolution'),
                        },
                    )
                idempotent_replay = True
                return {
                    'turn_id': turn_id,
                    'resolution': normalized_resolution,
                    'idempotent_replay': True,
                }
            raise SessionRecoveryConflictError(
                'session_recovery_not_required',
                'This session does not have an active recovery gate.',
            )

        gated_turn_id = int(gate['turnId'])
        if gated_turn_id != turn_id:
            raise SessionRecoveryConflictError(
                'recovery_turn_mismatch',
                'The recovery request does not match the turn blocking this session.',
                details={'expected_turn_id': gated_turn_id, 'actual_turn_id': turn_id},
            )
        if turn is None or turn.status != 'failed':
            raise SessionRecoveryConflictError(
                'session_recovery_invalid',
                'The recovery gate is not associated with a failed turn.',
                details={'turn_id': gated_turn_id},
            )

        resolved_at = utc_now().isoformat()
        fingerprint_salt = secrets.token_hex(16)
        note_fingerprint = _operator_note_fingerprint(
            normalized_note,
            salt=fingerprint_salt,
        )
        metadata, post_dm_state = _post_dm_state(turn)
        mechanics_summary = _snake_mechanics_summary_from_gate(gate)
        post_dm_state.update(
            {
                'status': 'failed',
                'narration_saved': True,
                **mechanics_summary,
                'recovery_required': False,
                'recovery_resolution': {
                    'resolution': normalized_resolution,
                    'resolved_at': resolved_at,
                    'operator_note_recorded': True,
                    'operator_note_fingerprint': note_fingerprint,
                    'operator_note_fingerprint_salt': fingerprint_salt,
                    **mechanics_summary,
                },
            }
        )
        metadata['post_dm_state'] = post_dm_state
        turn.metadata_json = safe_json_dumps(metadata, {})
        state.pop(TURN_RECOVERY_GATE_KEY, None)

        record_operator_action(
            action='session.turn_recovery_resolved',
            resource_type='dm_turn',
            workspace_id=session_obj.campaign.workspace_id,
            campaign_id=session_obj.campaign_id,
            session_id=session_obj.session_id,
            resource_id=turn_id,
            details={
                'turn_id': turn_id,
                'resolution': normalized_resolution,
                'operator_note': normalized_note,
                'narration_saved': True,
                **mechanics_summary,
                **_mechanics_audit_evidence(turn),
            },
        )
        return {
            'turn_id': turn_id,
            'resolution': normalized_resolution,
            'operator_note': normalized_note,
            'narration_saved': True,
            **mechanics_summary,
            **_mechanics_audit_evidence(turn),
            'recovery_required': False,
            'idempotent_replay': False,
        }

    mutation = mutate_session_snapshot_metadata(
        session_id,
        mutate_snapshot=mutate_snapshot,
        source='operator.session_recovery.resolve',
        change_type='session.recovery.resolved',
    )
    if mutation.session_obj is None:
        raise SessionRecoveryConflictError(
            'session_not_found',
            'Session not found.',
        )
    return SessionRecoveryResolutionResult(
        session_id=session_id,
        turn_id=turn_id,
        resolution=normalized_resolution,
        state_revision=mutation.state_revision,
        idempotent_replay=idempotent_replay,
    )
