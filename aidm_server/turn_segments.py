"""Turn-time CampaignSegment evaluation, activation, and persistence."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from flask import current_app

from aidm_server.database import commit_with_retry, db
from aidm_server.models import Campaign, CampaignSegment, DmTurn
from aidm_server.segment_state import build_segment_state_payload
from aidm_server.segment_triggers import evaluate_segment_trigger, parse_trigger_spec
from aidm_server.services.campaign_pack_progress import update_campaign_pack_progress
from aidm_server.socket_contracts import segment_triggered_payload
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.turn_events import SEGMENT_TRIGGERED_EVENT, record_turn_event


@dataclass(frozen=True)
class SegmentEvaluationRequest:
    session_id: int
    campaign_id: int
    player_message: str
    manual_segment_ids: frozenset[int]


@dataclass(frozen=True)
class TurnSegmentDependencies:
    automatic_enabled: Callable[[], bool]
    state_payload: Callable[[int, Campaign], tuple[dict, dict]]
    untriggered_segments: Callable[[int], list[CampaignSegment]]
    manual_segments: Callable[[int, frozenset[int]], list[CampaignSegment]]
    build_triggered_payload: Callable[..., dict]
    record_event: Callable[..., Any]
    update_pack_progress: Callable[..., Any]
    commit: Callable[[], None]
    rollback: Callable[[], None]
    telemetry_metric: Callable[..., Any]
    telemetry_event: Callable[..., Any]
    logger: logging.Logger


class TurnSegmentService:
    """Own segment policy and side effects outside the main turn engine."""

    def __init__(self, dependencies: TurnSegmentDependencies):
        self.dependencies = dependencies

    def segment_state_payload(self, session_id: int, campaign: Campaign) -> tuple[dict, dict]:
        return self.dependencies.state_payload(session_id, campaign)

    def activate_segments(
        self,
        *,
        turn: DmTurn,
        session_id: int,
        segments_to_activate: list[tuple[CampaignSegment, dict]],
    ) -> list[dict]:
        triggered_segments: list[dict] = []
        for segment, payload in segments_to_activate:
            segment.is_triggered = True
            triggered_segments.append(payload)
            self.dependencies.record_event(
                session_id=session_id,
                campaign_id=turn.campaign_id,
                turn_id=turn.turn_id,
                player_id=turn.player_id,
                event_type=SEGMENT_TRIGGERED_EVENT,
                payload={
                    'title': segment.title,
                    'reason': payload.get('reason'),
                    'segment_id': segment.segment_id,
                    'metadata': {'turn_id': turn.turn_id, 'reason': payload.get('reason')},
                },
            )
        return triggered_segments

    def evaluate_segments(
        self,
        *,
        turn: DmTurn,
        campaign: Campaign,
        request: SegmentEvaluationRequest,
        allowed_trigger_types: set[str] | None,
        include_manual: bool,
        state_payload_fn: Callable[[int, Campaign], tuple[dict, dict]] | None = None,
        activate_segments_fn: Callable[..., list[dict]] | None = None,
    ) -> list[dict]:
        triggered_segments: list[dict] = []
        automatic_enabled = bool(self.dependencies.automatic_enabled())
        if not (automatic_enabled or (include_manual and request.manual_segment_ids)):
            return triggered_segments

        state_payload_fn = state_payload_fn or self.segment_state_payload
        activate_segments_fn = activate_segments_fn or self.activate_segments

        try:
            segments_to_activate: list[tuple[CampaignSegment, dict]] = []
            if automatic_enabled:
                session_state_payload, campaign_state = state_payload_fn(request.session_id, campaign)
                for segment in self.dependencies.untriggered_segments(request.campaign_id):
                    trigger_type = parse_trigger_spec(segment.trigger_condition).trigger_type
                    if trigger_type == 'manual':
                        continue
                    if allowed_trigger_types is not None and trigger_type not in allowed_trigger_types:
                        continue
                    matched, reason, trigger_spec = evaluate_segment_trigger(
                        trigger_condition=segment.trigger_condition,
                        player_message=request.player_message,
                        session_state=session_state_payload,
                        campaign_state=campaign_state,
                    )
                    if not matched:
                        continue

                    segments_to_activate.append(
                        (
                            segment,
                            self.dependencies.build_triggered_payload(
                                segment_id=segment.segment_id,
                                title=segment.title,
                                description=segment.description,
                                reason=reason,
                                trigger_spec=trigger_spec,
                            ),
                        )
                    )

            if include_manual and request.manual_segment_ids:
                for segment in self.dependencies.manual_segments(
                    request.campaign_id,
                    request.manual_segment_ids,
                ):
                    payload = self.dependencies.build_triggered_payload(
                        segment_id=segment.segment_id,
                        title=segment.title,
                        description=segment.description,
                        reason='manual_override',
                        trigger_spec={'trigger_type': 'manual', 'raw': {'source': 'client_override'}},
                    )
                    if not any(existing.segment_id == segment.segment_id for existing, _ in segments_to_activate):
                        segments_to_activate.append((segment, payload))

            triggered_segments = activate_segments_fn(
                turn=turn,
                session_id=request.session_id,
                segments_to_activate=segments_to_activate,
            )
            self.dependencies.update_pack_progress(
                session_id=request.session_id,
                campaign_id=request.campaign_id,
                triggered_segments=triggered_segments,
            )
            self.dependencies.commit()
            if triggered_segments:
                self.dependencies.telemetry_metric('socket.segment_triggered_total', len(triggered_segments))
        except Exception as exc:
            self.dependencies.rollback()
            self.dependencies.logger.error('Segment evaluation failed: %s', str(exc))
            self.dependencies.telemetry_event(
                'socket.segment_evaluation_failed',
                payload={
                    'session_id': request.session_id,
                    'campaign_id': request.campaign_id,
                    'error': str(exc),
                },
                severity='error',
            )
            return []

        return triggered_segments


def default_turn_segment_service(*, logger: logging.Logger) -> TurnSegmentService:
    """Build the production segment service around Flask and SQLAlchemy adapters."""

    return TurnSegmentService(
        TurnSegmentDependencies(
            automatic_enabled=lambda: bool(current_app.config.get('AIDM_SEGMENT_EVALUATOR_ENABLED', True)),
            state_payload=build_segment_state_payload,
            untriggered_segments=lambda campaign_id: CampaignSegment.query.filter_by(
                campaign_id=campaign_id,
                is_triggered=False,
            ).all(),
            manual_segments=lambda campaign_id, segment_ids: CampaignSegment.query.filter(
                CampaignSegment.campaign_id == campaign_id,
                CampaignSegment.segment_id.in_(segment_ids),
                CampaignSegment.is_triggered.is_(False),
            ).all(),
            build_triggered_payload=segment_triggered_payload,
            record_event=record_turn_event,
            update_pack_progress=update_campaign_pack_progress,
            commit=lambda: commit_with_retry(label='segment evaluation'),
            rollback=lambda: db.session.rollback(),
            telemetry_metric=telemetry_metric,
            telemetry_event=telemetry_event,
            logger=logger,
        )
    )
