import logging
from types import SimpleNamespace
from unittest.mock import Mock

from aidm_server.socket_contracts import segment_triggered_payload
from aidm_server.turn_engine import TurnCommand, TurnEngine
from aidm_server.turn_segments import (
    SegmentEvaluationRequest,
    TurnSegmentDependencies,
    TurnSegmentService,
)


def _segment(
    segment_id: int,
    *,
    trigger_condition: str,
    title: str = 'Hidden Sigil',
):
    return SimpleNamespace(
        segment_id=segment_id,
        title=title,
        description=f'{title} description',
        trigger_condition=trigger_condition,
        is_triggered=False,
    )


def _dependencies(**overrides):
    defaults = {
        'automatic_enabled': Mock(return_value=True),
        'state_payload': Mock(return_value=({'current_location': 'ruins'}, {})),
        'untriggered_segments': Mock(return_value=[]),
        'manual_segments': Mock(return_value=[]),
        'build_triggered_payload': segment_triggered_payload,
        'record_event': Mock(),
        'update_pack_progress': Mock(),
        'commit': Mock(),
        'rollback': Mock(),
        'telemetry_metric': Mock(),
        'telemetry_event': Mock(),
        'logger': Mock(spec=logging.Logger),
    }
    defaults.update(overrides)
    return TurnSegmentDependencies(**defaults)


def _request(*, manual_segment_ids=frozenset()):
    return SegmentEvaluationRequest(
        session_id=7,
        campaign_id=5,
        player_message='I press the glowing sigil.',
        manual_segment_ids=manual_segment_ids,
    )


def _turn():
    return SimpleNamespace(turn_id=13, campaign_id=5, player_id=11)


def test_turn_segment_service_activates_only_allowed_automatic_trigger_types():
    keyword_segment = _segment(
        17,
        trigger_condition='{"type":"keywords","keywords":["sigil"],"match":"any"}',
    )
    state_segment = _segment(
        18,
        trigger_condition='{"type":"state","location_contains":"ruins"}',
        title='Ruins State',
    )
    manual_segment = _segment(19, trigger_condition='{"type":"manual"}', title='Manual Beat')
    record_event = Mock()
    update_pack_progress = Mock()
    commit = Mock()
    telemetry_metric = Mock()
    dependencies = _dependencies(
        untriggered_segments=Mock(return_value=[keyword_segment, state_segment, manual_segment]),
        record_event=record_event,
        update_pack_progress=update_pack_progress,
        commit=commit,
        telemetry_metric=telemetry_metric,
    )
    service = TurnSegmentService(dependencies)
    turn = _turn()

    triggered = service.evaluate_segments(
        turn=turn,
        campaign=SimpleNamespace(campaign_id=5),
        request=_request(),
        allowed_trigger_types={'keywords'},
        include_manual=False,
    )

    assert len(triggered) == 1
    assert triggered[0]['segment_id'] == 17
    assert triggered[0]['reason'] == 'keywords:sigil'
    assert keyword_segment.is_triggered is True
    assert state_segment.is_triggered is False
    assert manual_segment.is_triggered is False
    record_event.assert_called_once_with(
        session_id=7,
        campaign_id=5,
        turn_id=13,
        player_id=11,
        event_type='segment_triggered',
        payload={
            'title': 'Hidden Sigil',
            'reason': 'keywords:sigil',
            'segment_id': 17,
            'metadata': {'turn_id': 13, 'reason': 'keywords:sigil'},
        },
    )
    update_pack_progress.assert_called_once_with(
        session_id=7,
        campaign_id=5,
        triggered_segments=triggered,
    )
    commit.assert_called_once_with()
    telemetry_metric.assert_called_once_with('socket.segment_triggered_total', 1)
    dependencies.manual_segments.assert_not_called()


def test_turn_segment_service_keeps_manual_activation_when_automatic_evaluation_is_disabled():
    manual_segment = _segment(21, trigger_condition='{"type":"manual"}', title='Operator Beat')
    state_payload = Mock()
    untriggered_segments = Mock()
    dependencies = _dependencies(
        automatic_enabled=Mock(return_value=False),
        state_payload=state_payload,
        untriggered_segments=untriggered_segments,
        manual_segments=Mock(return_value=[manual_segment]),
    )
    service = TurnSegmentService(dependencies)

    triggered = service.evaluate_segments(
        turn=_turn(),
        campaign=SimpleNamespace(campaign_id=5),
        request=_request(manual_segment_ids=frozenset({21})),
        allowed_trigger_types=None,
        include_manual=True,
    )

    assert triggered == [
        {
            'segment_id': 21,
            'title': 'Operator Beat',
            'description': 'Operator Beat description',
            'reason': 'manual_override',
            'trigger_spec': {'trigger_type': 'manual', 'raw': {'source': 'client_override'}},
        }
    ]
    assert manual_segment.is_triggered is True
    state_payload.assert_not_called()
    untriggered_segments.assert_not_called()
    dependencies.manual_segments.assert_called_once_with(5, frozenset({21}))
    dependencies.commit.assert_called_once_with()


def test_turn_segment_service_rolls_back_and_reports_evaluation_failure():
    state_payload = Mock(side_effect=RuntimeError('projection unavailable'))
    rollback = Mock()
    telemetry_event = Mock()
    commit = Mock()
    logger = Mock(spec=logging.Logger)
    dependencies = _dependencies(
        state_payload=state_payload,
        rollback=rollback,
        telemetry_event=telemetry_event,
        commit=commit,
        logger=logger,
    )
    service = TurnSegmentService(dependencies)

    triggered = service.evaluate_segments(
        turn=_turn(),
        campaign=SimpleNamespace(campaign_id=5),
        request=_request(),
        allowed_trigger_types={'keywords'},
        include_manual=False,
    )

    assert triggered == []
    rollback.assert_called_once_with()
    commit.assert_not_called()
    logger.error.assert_called_once_with('Segment evaluation failed: %s', 'projection unavailable')
    telemetry_event.assert_called_once_with(
        'socket.segment_evaluation_failed',
        payload={'session_id': 7, 'campaign_id': 5, 'error': 'projection unavailable'},
        severity='error',
    )


def test_turn_engine_segment_helpers_remain_thin_compatible_delegates():
    segment_service = Mock(spec=TurnSegmentService)
    segment_service.segment_state_payload.return_value = ({'location': 'ruins'}, {'quest': 'gate'})
    segment_service.activate_segments.return_value = [{'segment_id': 17}]
    segment_service.evaluate_segments.return_value = [{'segment_id': 18}]
    engine = TurnEngine(
        socketio=Mock(),
        emit_fn=Mock(),
        stream_fn=Mock(),
        segment_service=segment_service,
    )
    campaign = SimpleNamespace(campaign_id=5)
    turn = _turn()
    activation = [(_segment(17, trigger_condition='sigil'), {'segment_id': 17})]

    assert engine._segment_state_payload(7, campaign) == ({'location': 'ruins'}, {'quest': 'gate'})
    assert engine._activate_segments(
        turn=turn,
        session_id=7,
        segments_to_activate=activation,
    ) == [{'segment_id': 17}]

    command = TurnCommand(
        sid='socket-1',
        session_id=7,
        campaign_id=5,
        world_id=3,
        player_id=11,
        user_input='I inspect the sigil.',
        manual_segment_ids={21},
    )
    assert engine._evaluate_segments(
        turn,
        campaign,
        command,
        allowed_trigger_types={'keywords'},
        include_manual=True,
    ) == [{'segment_id': 18}]

    evaluate_kwargs = segment_service.evaluate_segments.call_args.kwargs
    assert evaluate_kwargs['request'] == SegmentEvaluationRequest(
        session_id=7,
        campaign_id=5,
        player_message='I inspect the sigil.',
        manual_segment_ids=frozenset({21}),
    )
    assert evaluate_kwargs['allowed_trigger_types'] == {'keywords'}
    assert evaluate_kwargs['include_manual'] is True
    assert evaluate_kwargs['state_payload_fn'].__self__ is engine
    assert evaluate_kwargs['activate_segments_fn'].__self__ is engine
