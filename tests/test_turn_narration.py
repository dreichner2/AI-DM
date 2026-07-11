from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aidm_server.llm import EmergencyFallbackChunk
from aidm_server.provider_priority import background_provider_slot
from aidm_server.turn_narration import (
    DM_GENERATION_FAILED_MESSAGE,
    NarrationRequest,
    TurnNarrationDependencies,
    TurnNarrationService,
)


def _request(**overrides):
    values = {
        'session_id': 7,
        'campaign_id': 5,
        'turn_id': 11,
        'player_id': 3,
        'requires_roll': False,
        'roll_value': None,
        'rule_type': None,
        'confidence': 0.8,
        'serialized_rules_hint': '{}',
        'player_label': 'Aria',
        'world_id': 2,
        'user_input': 'I open the door.',
        'model_user_input': 'Aria: I open the door.',
        'rules_hint_payload': {'requires_roll': False, 'turn_number': 4},
        'resolved_turn_id': None,
        'pre_narration_effects': {'state_change_count': 0},
    }
    values.update(overrides)
    return NarrationRequest(**values)


def _service(stream):
    emitted: list[tuple[str, dict, dict]] = []
    statuses: list[tuple] = []
    timings: list[str] = []
    telemetry_events: list[tuple[str, dict]] = []
    telemetry_metrics: list[tuple[str, int, dict]] = []
    sleeps: list[float] = []
    lifecycle: list[str] = []
    context_builder = Mock(return_value='compact-context')
    roll_prompt_builder = Mock(return_value='Roll Dexterity.')
    logger = Mock()
    config = {'AIDM_LLM_PROVIDER': 'gemini', 'AIDM_LLM_MODEL': 'gemini-test'}

    def emit(event_name, payload, **kwargs):
        emitted.append((event_name, payload, kwargs))

    def record_phase_timing(phase, started_at, **kwargs):
        del started_at, kwargs
        timings.append(phase)

    def telemetry_event(event_name, payload=None, severity='info'):
        telemetry_events.append((event_name, {'payload': payload or {}, 'severity': severity}))

    def telemetry_metric(metric_name, value, tags=None):
        telemetry_metrics.append((metric_name, value, tags or {}))

    service = TurnNarrationService(
        TurnNarrationDependencies(
            emit=emit,
            sleep=sleeps.append,
            stream=stream,
            build_context=context_builder,
            release_session=lambda: lifecycle.append('session_released'),
            active_player_ids=lambda session_id: [2, 0, 3] if session_id == 7 else [],
            record_phase_timing=record_phase_timing,
            emit_turn_status=lambda *args: statuses.append(args),
            build_roll_prompt=roll_prompt_builder,
            response_requests_roll=lambda text: 'roll ' in text.lower(),
            response_explains_no_roll_needed=lambda text: 'no roll needed' in text.lower(),
            telemetry_event=telemetry_event,
            telemetry_metric=telemetry_metric,
            config_get=config.get,
            logger=logger,
        )
    )
    evidence = SimpleNamespace(
        emitted=emitted,
        statuses=statuses,
        timings=timings,
        telemetry_events=telemetry_events,
        telemetry_metrics=telemetry_metrics,
        sleeps=sleeps,
        context_builder=context_builder,
        roll_prompt_builder=roll_prompt_builder,
        logger=logger,
        config=config,
        lifecycle=lifecycle,
    )
    return service, evidence


def _events(evidence, event_name):
    return [payload for name, payload, _kwargs in evidence.emitted if name == event_name]


def test_narration_streams_visible_chunks_and_preserves_event_lifecycle():
    def stream(user_input, context, *, speaking_player, rules_hint):
        evidence.lifecycle.append('provider_started')
        assert user_input == 'Aria: I open the door.'
        assert context == 'compact-context'
        assert speaking_player == {'character_name': 'Aria', 'player_id': '3'}
        assert rules_hint['turn_number'] == 4
        yield 'The door '
        yield '<thought>hidden'
        yield ' reasoning</thought>opens.'

    service, evidence = _service(stream)
    result = service.narrate(_request())

    assert result.text == 'The door opens.'
    assert result.stream_error is None
    assert result.provider == 'gemini'
    assert result.model == 'gemini-test'
    assert [name for name, _payload, _kwargs in evidence.emitted] == [
        'dm_response_start',
        'dm_chunk',
        'dm_chunk',
        'dm_response_end',
    ]
    assert ''.join(payload['chunk'] for payload in _events(evidence, 'dm_chunk')) == result.text
    assert _events(evidence, 'dm_response_end')[0]['text'] == result.text
    assert all(kwargs == {'room': '7'} for name, _payload, kwargs in evidence.emitted if name != 'error')
    assert evidence.statuses == [
        (7, 11, 'narrating'),
        (7, 11, 'response_complete', {'ok': True, 'degraded': False}),
    ]
    assert evidence.timings == [
        'context_build',
        'provider_time_to_first_token',
        'provider_total',
        'dm_response_emit',
    ]
    evidence.context_builder.assert_called_once_with(
        2,
        5,
        7,
        query_text='I open the door.',
        active_player_ids=[2, 3],
        current_player_id=3,
    )
    assert evidence.sleeps == [0, 0, 0]
    assert evidence.lifecycle == ['session_released', 'provider_started']


def test_narration_reads_provider_identity_after_context_construction():
    service, evidence = _service(lambda *args, **kwargs: iter(['Ready.']))

    def build_context(*args, **kwargs):
        del args, kwargs
        evidence.config.update(
            {
                'AIDM_LLM_PROVIDER': 'nvidia',
                'AIDM_LLM_MODEL': 'nemotron-live',
            }
        )
        return 'updated-context'

    evidence.context_builder.side_effect = build_context
    result = service.narrate(_request())

    assert result.provider == 'nvidia'
    assert result.model == 'nemotron-live'
    started_event = next(event for event in evidence.telemetry_events if event[0] == 'socket.dm_stream_started')
    assert started_event[1]['payload']['provider'] == 'nvidia'
    assert started_event[1]['payload']['model'] == 'nemotron-live'


def test_narration_releases_session_before_waiting_for_active_background_provider():
    session_released = threading.Event()
    provider_started = threading.Event()
    completed = threading.Event()
    failures: list[BaseException] = []

    def stream(*_args, **_kwargs):
        provider_started.set()
        yield 'Ready.'

    service, _evidence = _service(stream)
    service.dependencies = replace(
        service.dependencies,
        release_session=session_released.set,
    )

    def run_narration():
        try:
            service.narrate(_request())
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below.
            failures.append(exc)
        finally:
            completed.set()

    with background_provider_slot():
        worker = threading.Thread(target=run_narration)
        worker.start()
        assert session_released.wait(timeout=1.0)
        time.sleep(0.02)
        assert provider_started.is_set() is False
        assert completed.is_set() is False

    worker.join(timeout=1.0)
    assert worker.is_alive() is False
    assert failures == []
    assert provider_started.is_set() is True
    assert completed.is_set() is True


def test_narration_returns_public_failure_when_context_build_fails():
    service, evidence = _service(lambda *args, **kwargs: iter(['unused']))
    evidence.context_builder.side_effect = RuntimeError('context unavailable')

    result = service.narrate(_request())

    assert result.text == ''
    assert result.stream_error == DM_GENERATION_FAILED_MESSAGE
    assert [event[0] for event in evidence.emitted] == ['error']
    assert evidence.emitted[0][1]['error_code'] == 'dm_context_failed'
    assert evidence.statuses == [(7, 11, 'failed', {'stage': 'context_build'})]
    assert evidence.timings == ['context_build']
    assert evidence.lifecycle == ['session_released']
    assert evidence.telemetry_events[-1][0] == 'socket.dm_context_failed'


def test_narration_injects_required_roll_prompt_with_pending_turn_id():
    service, evidence = _service(lambda *args, **kwargs: iter(['A trap clicks.']))
    request = _request(
        requires_roll=True,
        rule_type='dexterity',
        serialized_rules_hint='{"dc_hint":"DC 14"}',
        rules_hint_payload={'requires_roll': True},
        resolved_turn_id=19,
    )

    result = service.narrate(request)

    assert result.text == 'A trap clicks.\n\nRoll Dexterity.'
    hint = evidence.roll_prompt_builder.call_args.args[0]
    assert hint.requires_roll is True
    assert hint.roll_type == 'dexterity'
    assert hint.dc_hint == 'DC 14'
    assert evidence.roll_prompt_builder.call_args.kwargs == {'pending_turn_id': 19}
    assert ('socket.roll_prompt_injected_total', 1, {}) in evidence.telemetry_metrics


@pytest.mark.parametrize(
    ('response_text', 'request_overrides'),
    [
        ('The die settles.', {'roll_value': 17}),
        ('Please roll Dexterity.', {}),
        ('No roll needed for this action.', {}),
    ],
)
def test_narration_does_not_inject_roll_prompt_when_gate_is_already_satisfied(
    response_text,
    request_overrides,
):
    service, evidence = _service(lambda *args, **kwargs: iter([response_text]))

    result = service.narrate(
        _request(
            requires_roll=True,
            rule_type='dexterity',
            rules_hint_payload={'requires_roll': True},
            **request_overrides,
        )
    )

    assert result.text == response_text
    evidence.roll_prompt_builder.assert_not_called()
    assert not any(metric[0] == 'socket.roll_prompt_injected_total' for metric in evidence.telemetry_metrics)


def test_narration_marks_emergency_provider_fallback_without_leaking_error_text():
    def stream(*args, **kwargs):
        del args, kwargs
        yield EmergencyFallbackChunk(
            'Continuity-safe narration.',
            error=RuntimeError('secret upstream response'),
            failed_provider='gemini',
            failed_model='gemini-private',
        )
        yield EmergencyFallbackChunk(
            ' The scene continues.',
            error=RuntimeError('second secret response'),
            failed_provider='gemini',
            failed_model='gemini-private',
        )

    service, evidence = _service(stream)
    result = service.narrate(
        _request(pre_narration_effects={'state_change_count': 2, 'state_change_types': ['inventory.equip']})
    )

    assert result.provider == 'fallback'
    assert result.model == 'continuity-safe-v1'
    assert result.text == 'Continuity-safe narration. The scene continues.'
    assert result.emergency_fallback is not None
    assert result.emergency_fallback['post_dm_state_mutation_skipped'] is True
    assert result.emergency_fallback['canon_mutation_skipped'] is True
    assert result.emergency_fallback['pre_narration_effects']['state_change_count'] == 2
    assert 'secret upstream response' not in json.dumps(_events(evidence, 'dm_response_end'))
    assert evidence.telemetry_metrics == [
        (
            'socket.dm_provider_failure_total',
            1,
            {'provider': 'gemini', 'model': 'gemini-private'},
        )
    ]
    degraded_events = [event for event in evidence.telemetry_events if event[0] == 'socket.dm_provider_degraded']
    assert len(degraded_events) == 1


def test_narration_stream_failure_returns_public_error_and_retains_partial_text():
    def stream(*args, **kwargs):
        del args, kwargs
        yield 'A partial response.'
        raise RuntimeError('secret provider credential')

    service, evidence = _service(stream)
    result = service.narrate(_request())

    assert result.text == 'A partial response.'
    assert result.stream_error == DM_GENERATION_FAILED_MESSAGE
    evidence.logger.error.assert_called_once_with(
        'Error generating streamed DM response error_type=%s',
        'RuntimeError',
    )
    assert 'secret provider credential' not in json.dumps(evidence.logger.mock_calls)
    error_events = _events(evidence, 'error')
    assert error_events == [
        {
            'error': DM_GENERATION_FAILED_MESSAGE,
            'error_code': 'dm_generation_failed',
            'details': {},
        }
    ]
    end_payload = _events(evidence, 'dm_response_end')[0]
    assert end_payload['ok'] is False
    assert end_payload['text'] == 'A partial response.'
    assert end_payload['error'] == DM_GENERATION_FAILED_MESSAGE
    assert 'secret provider credential' not in json.dumps(evidence.emitted)
    assert 'provider_total' in evidence.timings
    assert evidence.statuses[-1] == (7, 11, 'response_complete', {'ok': False, 'degraded': False})
