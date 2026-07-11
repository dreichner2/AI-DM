from __future__ import annotations

import pytest

from aidm_server.database import db
import aidm_server.game_state.extraction.post_dm_outcome_extractor as post_extractor_module
import aidm_server.game_state.extraction.pre_dm_action_extractor as pre_extractor_module
from aidm_server.provider_priority import provider_priority_gate
import aidm_server.turn_control as turn_control_module
from aidm_server.models import Session
from tests.helpers import seed_world_campaign_player_session


class BoundaryReleaseError(RuntimeError):
    pass


def _failing_boundary_callback(message: str):
    baseline = provider_priority_gate.snapshot()

    def fail_after_reservation() -> None:
        reserved = provider_priority_gate.snapshot()
        assert reserved.waiting_foreground == baseline.waiting_foreground + 1
        assert reserved.active_foreground == baseline.active_foreground
        assert reserved.background_active == baseline.background_active
        raise BoundaryReleaseError(message)

    return baseline, fail_after_reservation


def test_pre_dm_boundary_failure_propagates_without_calling_provider(app, monkeypatch):
    provider_requests: list[bool] = []
    baseline, fail_boundary = _failing_boundary_callback('pre boundary failed')

    def unexpected_provider():
        provider_requests.append(True)
        raise AssertionError('the provider must not be requested')

    monkeypatch.setattr(pre_extractor_module, 'get_helper_provider', unexpected_provider)

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        with pytest.raises(BoundaryReleaseError, match='pre boundary failed'):
            pre_extractor_module.extract_pre_dm_actions(
                current_state={},
                player_message='I attack the goblin.',
                recent_timeline=[],
                actor_id='player_1',
                before_provider_call=fail_boundary,
            )

    assert provider_requests == []
    assert provider_priority_gate.snapshot() == baseline


def test_post_dm_boundary_failure_propagates_without_calling_provider(app, monkeypatch):
    provider_requests: list[bool] = []
    baseline, fail_boundary = _failing_boundary_callback('post boundary failed')

    def unexpected_provider():
        provider_requests.append(True)
        raise AssertionError('the provider must not be requested')

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', unexpected_provider)

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        with pytest.raises(BoundaryReleaseError, match='post boundary failed'):
            post_extractor_module.extract_post_dm_outcomes(
                state_before_dm={},
                player_message='I hold my ground.',
                validated_actions={},
                already_applied_changes=[],
                dm_response='You take 2 damage.',
                recent_timeline=[],
                actor_id='player_1',
                turn_id=1,
                before_provider_call=fail_boundary,
            )

    assert provider_requests == []
    assert provider_priority_gate.snapshot() == baseline


def test_turn_conductor_boundary_failure_propagates_without_calling_provider(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    provider_requests: list[bool] = []
    baseline, fail_boundary = _failing_boundary_callback('conductor boundary failed')

    def unexpected_provider():
        provider_requests.append(True)
        raise AssertionError('the provider must not be requested')

    monkeypatch.setattr(turn_control_module, 'get_helper_provider', unexpected_provider)

    with app.app_context():
        app.config['AIDM_TURN_CONDUCTOR_HELPER_IN_TESTS'] = True
        session_obj = db.session.get(Session, ids['session_id'])
        with pytest.raises(BoundaryReleaseError, match='conductor boundary failed'):
            turn_control_module.conduct_turn_submission(
                session_obj,
                player_id=ids['player_id'],
                message='I wait for an opening.',
                action_intent={'kind': 'message'},
                active_player_ids=[ids['player_id']],
                before_helper_call=fail_boundary,
                reload_session_after_helper=lambda: session_obj,
            )

    assert provider_requests == []
    assert provider_priority_gate.snapshot() == baseline


def test_provider_failures_retain_deterministic_fallbacks(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    provider_calls: list[str] = []

    class FailedProvider:
        def __init__(self, name: str):
            self.name = name

        def generate(self, _request):
            provider_calls.append(self.name)
            raise RuntimeError(f'{self.name} unavailable')

    monkeypatch.setattr(pre_extractor_module, 'get_helper_provider', lambda: FailedProvider('pre'))
    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FailedProvider('post'))
    monkeypatch.setattr(turn_control_module, 'get_helper_provider', lambda: FailedProvider('conductor'))
    baseline = provider_priority_gate.snapshot()

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        app.config['AIDM_TURN_CONDUCTOR_HELPER_IN_TESTS'] = True

        pre_result = pre_extractor_module.extract_pre_dm_actions(
            current_state={},
            player_message='I drink my healing potion.',
            recent_timeline=[],
            actor_id='player_1',
            before_provider_call=lambda: None,
        )
        post_result = post_extractor_module.extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I hold my ground.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='You take 2 damage.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=1,
            before_provider_call=lambda: None,
        )
        session_obj = db.session.get(Session, ids['session_id'])
        conductor_result = turn_control_module.conduct_turn_submission(
            session_obj,
            player_id=ids['player_id'],
            message='I wait for an opening.',
            action_intent={'kind': 'message'},
            active_player_ids=[ids['player_id']],
            before_helper_call=lambda: None,
            reload_session_after_helper=lambda: session_obj,
        )

    assert provider_calls == ['pre', 'post', 'conductor']
    assert pre_result['debug']['fallbackReason'] == 'helper_error'
    assert pre_result['debug']['source'] == 'heuristic'
    assert post_result['debug']['fallbackReason'] == 'helper_error'
    assert post_result['debug']['source'] == 'heuristic'
    assert conductor_result[-1] == {'decision': 'allow_free'}
    assert provider_priority_gate.snapshot() == baseline
