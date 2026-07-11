"""DM narration streaming orchestration for a persisted turn."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from aidm_server.llm import CONTEXT_VERSION, EmergencyFallbackChunk
from aidm_server.models import safe_json_loads
from aidm_server.provider_priority import foreground_provider_reservation
from aidm_server.rules import RuleHint
from aidm_server.socket_contracts import (
    dm_chunk_payload,
    dm_response_end_payload,
    dm_response_start_payload,
    socket_error_payload as socket_error,
)
from aidm_server.text_sanitization import ReasoningBlockFilter


DM_GENERATION_FAILED_MESSAGE = 'The DM response could not be generated. Please retry.'


@dataclass(frozen=True)
class NarrationRequest:
    session_id: int
    campaign_id: int
    turn_id: int
    player_id: int
    requires_roll: bool
    roll_value: int | None
    rule_type: str | None
    confidence: float | None
    serialized_rules_hint: str | None
    player_label: str
    world_id: int
    user_input: str
    model_user_input: str
    rules_hint_payload: dict[str, Any]
    resolved_turn_id: int | None
    pre_narration_effects: dict[str, Any]


@dataclass(frozen=True)
class NarrationResult:
    text: str
    stream_error: str | None
    provider: str | None
    model: str | None
    emergency_fallback: dict[str, Any] | None = None


@dataclass(frozen=True)
class TurnNarrationDependencies:
    emit: Callable[..., Any]
    sleep: Callable[[float], Any]
    stream: Callable[..., Iterable[str]]
    build_context: Callable[..., str]
    release_session: Callable[[], None]
    active_player_ids: Callable[[int], list[int]] | None
    record_phase_timing: Callable[..., None]
    emit_turn_status: Callable[..., None]
    build_roll_prompt: Callable[[RuleHint, int | None], str]
    response_requests_roll: Callable[[str], bool]
    response_explains_no_roll_needed: Callable[[str], bool]
    telemetry_event: Callable[..., Any]
    telemetry_metric: Callable[..., Any]
    config_get: Callable[[str], Any]
    logger: logging.Logger


class TurnNarrationService:
    """Stream one DM response and emit its public Socket.IO lifecycle."""

    def __init__(self, dependencies: TurnNarrationDependencies):
        self.dependencies = dependencies

    def narrate(self, request: NarrationRequest) -> NarrationResult:
        # Register foreground demand before context construction so background
        # canon work cannot claim the provider between context materialization
        # and the first narration token. Foreground callers may still overlap.
        with foreground_provider_reservation() as activate_provider:
            return self._narrate_with_priority(request, activate_provider=activate_provider)

    def _narrate_with_priority(
        self,
        request: NarrationRequest,
        *,
        activate_provider: Callable[[], None],
    ) -> NarrationResult:
        dependencies = self.dependencies

        context_started = time.perf_counter()
        context_error: Exception | None = None
        try:
            active_player_ids: list[int] = []
            if dependencies.active_player_ids:
                active_player_ids = [
                    player_id
                    for player_id in dependencies.active_player_ids(request.session_id)
                    if player_id
                ]
            context = dependencies.build_context(
                request.world_id,
                request.campaign_id,
                request.session_id,
                query_text=request.user_input,
                active_player_ids=active_player_ids,
                current_player_id=request.player_id,
            )
        except Exception as exc:
            context_error = exc
        finally:
            # Context is now a scalar string. Fully discard the scoped session
            # (and its read transaction/checked-out connection) before provider
            # iteration. The caller reloads persistence models by primary key.
            try:
                dependencies.release_session()
            except Exception as exc:
                context_error = context_error or exc
        dependencies.record_phase_timing(
            'context_build',
            context_started,
            campaign_id=request.campaign_id,
            session_id=request.session_id,
        )
        if context_error is not None:
            dependencies.logger.error(
                'Error building DM context error_type=%s',
                type(context_error).__name__,
            )
            dependencies.emit(
                'error',
                socket_error('dm_context_failed', DM_GENERATION_FAILED_MESSAGE),
            )
            dependencies.emit_turn_status(
                request.session_id,
                request.turn_id,
                'failed',
                {'stage': 'context_build'},
            )
            dependencies.telemetry_event(
                'socket.dm_context_failed',
                payload={
                    'session_id': request.session_id,
                    'campaign_id': request.campaign_id,
                    'turn_id': request.turn_id,
                    'error_type': type(context_error).__name__,
                },
                severity='error',
            )
            return NarrationResult(
                text='',
                stream_error=DM_GENERATION_FAILED_MESSAGE,
                provider=str(dependencies.config_get('AIDM_LLM_PROVIDER') or 'unknown'),
                model=str(dependencies.config_get('AIDM_LLM_MODEL') or 'unknown'),
            )
        dependencies.emit_turn_status(request.session_id, request.turn_id, 'narrating')
        dependencies.telemetry_event(
            'socket.dm_stream_started',
            payload={
                'session_id': request.session_id,
                'campaign_id': request.campaign_id,
                'turn_id': request.turn_id,
                'provider': dependencies.config_get('AIDM_LLM_PROVIDER'),
                'model': dependencies.config_get('AIDM_LLM_MODEL'),
                'context_version': CONTEXT_VERSION,
            },
        )
        dependencies.emit(
            'dm_response_start',
            dm_response_start_payload(
                session_id=request.session_id,
                turn_id=request.turn_id,
                requires_roll=request.requires_roll,
                rules_hint=request.rules_hint_payload,
                context_version=CONTEXT_VERSION,
                turn_number=request.rules_hint_payload.get('turn_number'),
            ),
            room=str(request.session_id),
        )

        dm_response_text = ''
        stream_error: str | None = None
        configured_provider = str(dependencies.config_get('AIDM_LLM_PROVIDER') or 'unknown')
        configured_model = str(dependencies.config_get('AIDM_LLM_MODEL') or 'unknown')
        narration_provider: str | None = configured_provider
        narration_model: str | None = configured_model
        emergency_fallback: dict[str, Any] | None = None
        reasoning_filter = ReasoningBlockFilter()
        provider_started = time.perf_counter()
        first_token_recorded = False
        # If canon already owns the background slot, this is the only blocking
        # point. Context DB work has finished and the scoped session is gone.
        activate_provider()
        try:
            for raw_chunk in dependencies.stream(
                request.model_user_input,
                context,
                speaking_player={
                    'character_name': request.player_label,
                    'player_id': str(request.player_id),
                },
                rules_hint=request.rules_hint_payload,
            ):
                if not raw_chunk:
                    continue
                if isinstance(raw_chunk, EmergencyFallbackChunk):
                    narration_provider = raw_chunk.provider
                    narration_model = raw_chunk.model
                    if emergency_fallback is None:
                        emergency_fallback = self._build_emergency_fallback_payload(
                            request=request,
                            chunk=raw_chunk,
                            configured_provider=configured_provider,
                            configured_model=configured_model,
                        )
                        self._record_emergency_fallback(
                            request=request,
                            chunk=raw_chunk,
                            payload=emergency_fallback,
                        )
                if not first_token_recorded:
                    dependencies.record_phase_timing(
                        'provider_time_to_first_token',
                        provider_started,
                        campaign_id=request.campaign_id,
                        session_id=request.session_id,
                    )
                    first_token_recorded = True
                chunk = reasoning_filter.filter(raw_chunk)
                if not chunk:
                    continue
                self._emit_chunk(request, chunk)
                dm_response_text += chunk

            final_chunk = reasoning_filter.finish()
            if final_chunk:
                self._emit_chunk(request, final_chunk)
                dm_response_text += final_chunk
        except Exception as exc:
            stream_error = DM_GENERATION_FAILED_MESSAGE
            dependencies.logger.error(
                'Error generating streamed DM response error_type=%s',
                type(exc).__name__,
            )
            dependencies.emit('error', socket_error('dm_generation_failed', DM_GENERATION_FAILED_MESSAGE))
            dependencies.telemetry_event(
                'socket.dm_generation_failed',
                payload={
                    'session_id': request.session_id,
                    'turn_id': request.turn_id,
                    'error_type': type(exc).__name__,
                },
                severity='error',
            )
        finally:
            dependencies.record_phase_timing(
                'provider_total',
                provider_started,
                campaign_id=request.campaign_id,
                session_id=request.session_id,
            )

        if self._roll_prompt_is_required(request, dm_response_text):
            injected_prompt = dependencies.build_roll_prompt(
                RuleHint(
                    requires_roll=True,
                    roll_type=request.rule_type,
                    dc_hint=safe_json_loads(request.serialized_rules_hint, {}).get('dc_hint'),
                    reason='Roll prompt injected',
                    confidence=request.confidence or 1.0,
                    roll_value=None,
                    outcome_deferred=True,
                ),
                pending_turn_id=request.resolved_turn_id,
            )
            injected_chunk = f'\n\n{injected_prompt}' if dm_response_text.strip() else injected_prompt
            self._emit_chunk(request, injected_chunk)
            dm_response_text += injected_chunk
            dependencies.telemetry_metric('socket.roll_prompt_injected_total', 1)

        response_emit_started = time.perf_counter()
        dependencies.emit(
            'dm_response_end',
            dm_response_end_payload(
                session_id=request.session_id,
                turn_id=request.turn_id,
                requires_roll=request.requires_roll,
                rules_hint=request.rules_hint_payload,
                context_version=CONTEXT_VERSION,
                ok=stream_error is None,
                text=dm_response_text,
                error=stream_error[:500] if stream_error else None,
                turn_number=request.rules_hint_payload.get('turn_number'),
                degraded=emergency_fallback is not None,
                fallback=emergency_fallback,
            ),
            room=str(request.session_id),
        )
        dependencies.record_phase_timing(
            'dm_response_emit',
            response_emit_started,
            campaign_id=request.campaign_id,
            session_id=request.session_id,
        )
        dependencies.emit_turn_status(
            request.session_id,
            request.turn_id,
            'response_complete',
            {'ok': stream_error is None, 'degraded': emergency_fallback is not None},
        )
        # Flush the final event before persistence, canon extraction, and other
        # post-turn work that can take substantially longer than streaming.
        dependencies.sleep(0)
        return NarrationResult(
            text=dm_response_text,
            stream_error=stream_error,
            provider=narration_provider,
            model=narration_model,
            emergency_fallback=emergency_fallback,
        )

    def _emit_chunk(self, request: NarrationRequest, chunk: str) -> None:
        self.dependencies.emit(
            'dm_chunk',
            dm_chunk_payload(
                chunk=chunk,
                session_id=request.session_id,
                turn_id=request.turn_id,
                requires_roll=request.requires_roll,
                rules_hint=request.rules_hint_payload,
                context_version=CONTEXT_VERSION,
                turn_number=request.rules_hint_payload.get('turn_number'),
            ),
            room=str(request.session_id),
        )
        self.dependencies.sleep(0)

    @staticmethod
    def _build_emergency_fallback_payload(
        *,
        request: NarrationRequest,
        chunk: EmergencyFallbackChunk,
        configured_provider: str,
        configured_model: str,
    ) -> dict[str, Any]:
        return {
            'kind': 'emergency_continuity',
            'reason': chunk.reason,
            'configured_provider': configured_provider,
            'configured_model': configured_model,
            'failed_provider': chunk.failed_provider,
            'failed_model': chunk.failed_model,
            'error_type': chunk.error_type,
            'message': chunk.public_message,
            'post_dm_state_mutation_skipped': True,
            'canon_mutation_skipped': True,
            'pre_narration_effects': request.pre_narration_effects,
        }

    def _record_emergency_fallback(
        self,
        *,
        request: NarrationRequest,
        chunk: EmergencyFallbackChunk,
        payload: dict[str, Any],
    ) -> None:
        self.dependencies.telemetry_metric(
            'socket.dm_provider_failure_total',
            1,
            tags={'provider': chunk.failed_provider, 'model': chunk.failed_model or 'unknown'},
        )
        self.dependencies.telemetry_event(
            'socket.dm_provider_degraded',
            payload={
                'session_id': request.session_id,
                'campaign_id': request.campaign_id,
                'turn_id': request.turn_id,
                **payload,
            },
            severity='warning',
        )

    def _roll_prompt_is_required(self, request: NarrationRequest, response_text: str) -> bool:
        return bool(
            request.requires_roll
            and request.roll_value is None
            and not self.dependencies.response_requests_roll(response_text)
            and not self.dependencies.response_explains_no_roll_needed(response_text)
        )
