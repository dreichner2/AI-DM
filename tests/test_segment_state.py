from __future__ import annotations

import aidm_server.game_state.extraction.post_dm_outcome_extractor as post_extractor_module
import aidm_server.segment_state as segment_state_module
from aidm_server.canon_jobs import _evaluate_state_segments_after_turn
from aidm_server.contracts import ProviderResponse
from aidm_server.database import db
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY, STATE_PIPELINE_VERSION
from aidm_server.game_state.orchestration.turn_pipeline import post_dm_pipeline
from aidm_server.models import (
    Campaign,
    CampaignSegment,
    DmTurn,
    Player,
    Session,
    SessionState,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.segment_state import build_segment_state_payload
from aidm_server.segment_triggers import evaluate_segment_trigger
from tests.helpers import seed_world_campaign_player_session


def _base_snapshot(*, scene_name='Old Road', scene_id='old_road') -> dict:
    return {
        'schemaVersion': 1,
        'currentScene': {
            'locationId': scene_id,
            'name': scene_name,
            'sceneType': 'travel',
            'dangerLevel': 1,
            'mood': 'tense',
            'combatState': 'none',
            'activeNpcIds': [],
            'activeQuestIds': [],
        },
        'playerCharacters': [],
        'quests': [],
        'locations': [],
        'knownNpcs': [],
        'partyNpcs': [],
        'flags': {},
        'stateChangeLedger': [],
    }


def _set_stale_projection(session_id: int, *, location='Old Road', quest='Old Quest') -> None:
    state = SessionState.query.filter_by(session_id=session_id).first()
    if not state:
        state = SessionState(session_id=session_id)
        db.session.add(state)
    state.current_location = location
    state.current_quest = quest


def test_state_trigger_uses_live_current_scene_over_stale_projection_and_campaign(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        session = db.session.get(Session, ids['session_id'])
        assert campaign is not None
        assert session is not None
        campaign.location = 'Old Road'
        campaign.current_quest = 'Old Quest'
        _set_stale_projection(ids['session_id'])

        snapshot = _base_snapshot(scene_name='Soot-Stained Chapel', scene_id='soot_stained_chapel')
        session.state_snapshot = safe_json_dumps(snapshot, {})
        db.session.commit()

        session_state_payload, campaign_state = build_segment_state_payload(ids['session_id'], campaign)

    matched, reason, _spec = evaluate_segment_trigger(
        trigger_condition='{"type":"state","location_contains":"chapel"}',
        player_message='I look around.',
        session_state=session_state_payload,
        campaign_state=campaign_state,
    )

    assert matched is True
    assert reason == 'state:location=chapel;quest=*'
    assert session_state_payload['current_location'] == 'Soot-Stained Chapel'
    assert session_state_payload['current_location_id'] == 'soot_stained_chapel'


def test_segment_state_payload_skips_projection_refresh_when_snapshot_is_usable(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)

    def fail_refresh(*_args, **_kwargs):
        raise AssertionError('projection refresh should not run for a usable live snapshot')

    monkeypatch.setattr(segment_state_module, 'refresh_session_projection', fail_refresh)

    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        session = db.session.get(Session, ids['session_id'])
        assert campaign is not None
        assert session is not None
        _set_stale_projection(ids['session_id'], location='Old Road', quest='Old Quest')
        session.state_snapshot = safe_json_dumps(
            _base_snapshot(scene_name='Soot-Stained Chapel', scene_id='soot_stained_chapel'),
            {},
        )
        db.session.commit()

        session_state_payload, _campaign_state = build_segment_state_payload(ids['session_id'], campaign)

    assert session_state_payload['current_location'] == 'Soot-Stained Chapel'
    assert session_state_payload['current_location_id'] == 'soot_stained_chapel'


def test_state_trigger_falls_back_to_session_state_when_snapshot_missing(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        session = db.session.get(Session, ids['session_id'])
        assert campaign is not None
        assert session is not None
        campaign.location = 'Old Road'
        session.state_snapshot = 'not-json'
        _set_stale_projection(ids['session_id'], location='Old Chapel')
        db.session.commit()

        session_state_payload, campaign_state = build_segment_state_payload(ids['session_id'], campaign)

    matched, _reason, _spec = evaluate_segment_trigger(
        trigger_condition='{"type":"state","location_contains":"chapel"}',
        player_message='I look around.',
        session_state=session_state_payload,
        campaign_state=campaign_state,
    )

    assert matched is True
    assert session_state_payload['current_location'] == 'Old Chapel'


def test_quest_trigger_uses_live_active_quest_over_stale_projection_and_campaign(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        session = db.session.get(Session, ids['session_id'])
        assert campaign is not None
        assert session is not None
        campaign.current_quest = 'Old Quest'
        _set_stale_projection(ids['session_id'], quest='Old Quest')

        snapshot = _base_snapshot()
        snapshot['currentScene']['activeQuestIds'] = ['find_missing_sailor']
        snapshot['quests'] = [
            {
                'id': 'find_missing_sailor',
                'title': 'Find the Missing Sailor',
                'status': 'active',
                'stage': 'Search the old harbor',
                'summary': 'Find what happened to the missing sailor.',
                'objectives': [
                    {
                        'id': 'talk_to_velra',
                        'description': 'Talk to Captain Velra about the missing sailor.',
                        'status': 'open',
                    }
                ],
            }
        ]
        session.state_snapshot = safe_json_dumps(snapshot, {})
        db.session.commit()

        session_state_payload, campaign_state = build_segment_state_payload(ids['session_id'], campaign)

    matched, _reason, _spec = evaluate_segment_trigger(
        trigger_condition='{"type":"state","quest_contains":"missing sailor"}',
        player_message='I look around.',
        session_state=session_state_payload,
        campaign_state=campaign_state,
    )

    assert matched is True
    assert session_state_payload['active_quest_ids'] == ['find_missing_sailor']
    assert session_state_payload['current_quest'] == 'Find the Missing Sailor - Search the old harbor'


def test_location_contains_does_not_match_known_non_current_location(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        session = db.session.get(Session, ids['session_id'])
        assert campaign is not None
        assert session is not None
        _set_stale_projection(ids['session_id'])

        snapshot = _base_snapshot(scene_name='Old Road', scene_id='old_road')
        snapshot['locations'] = [
            {'id': 'old_road', 'name': 'Old Road', 'status': 'visited'},
            {'id': 'soot_stained_chapel', 'name': 'Soot-Stained Chapel', 'status': 'discovered'},
        ]
        session.state_snapshot = safe_json_dumps(snapshot, {})
        db.session.commit()

        session_state_payload, campaign_state = build_segment_state_payload(ids['session_id'], campaign)

    matched, _reason, _spec = evaluate_segment_trigger(
        trigger_condition='{"type":"state","location_contains":"chapel"}',
        player_message='I look around.',
        session_state=session_state_payload,
        campaign_state=campaign_state,
    )

    assert matched is False
    assert 'Soot-Stained Chapel' in session_state_payload['known_location_names']


def test_post_dm_state_segment_triggers_from_same_turn_live_snapshot(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    helper_text = (
        '{"proposedChanges":['
        '{"id":"post_move_chapel","type":"scene.move_location","locationId":"soot_stained_chapel","name":"Soot-Stained Chapel"}'
        '],"uncertainChanges":[]}'
    )

    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(text=helper_text, provider='fake', model='fake-world-helper')

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        campaign = db.session.get(Campaign, ids['campaign_id'])
        player = db.session.get(Player, ids['player_id'])
        session = db.session.get(Session, ids['session_id'])
        assert campaign is not None
        assert player is not None
        assert session is not None

        campaign.location = 'Old Road'
        campaign.current_quest = 'Old Quest'
        _set_stale_projection(ids['session_id'])
        state = _base_snapshot()
        session.state_snapshot = safe_json_dumps(state, {})
        db.session.add(
            CampaignSegment(
                campaign_id=ids['campaign_id'],
                title='Hidden Chamber Unlocked',
                description='The chamber awakens.',
                trigger_condition='{"type":"state","location_contains":"chapel"}',
                tags='chamber,secret',
                is_triggered=False,
            )
        )
        turn = DmTurn(
            session_id=session.session_id,
            campaign_id=campaign.campaign_id,
            player_id=player.player_id,
            player_input='I enter the chapel.',
            dm_output='You enter the soot-stained chapel.',
            status='completed',
            metadata_json=safe_json_dumps(
                {
                    STATE_PIPELINE_METADATA_KEY: {
                        'version': STATE_PIPELINE_VERSION,
                        'actorId': f'player_{player.player_id}',
                        'stateBeforeDm': state,
                        'preDmValidation': {'validatedActions': [], 'immediateChanges': []},
                        'immediateValidation': {'accepted': [], 'rejected': [], 'modified': []},
                        'immediateAppliedChanges': [],
                    }
                },
                {},
            ),
        )
        db.session.add(turn)
        db.session.commit()

        post_dm_pipeline(
            turn=turn,
            session_obj=session,
            campaign=campaign,
            player=player,
            dm_response_text=turn.dm_output,
        )
        _set_stale_projection(ids['session_id'])
        campaign.location = 'Old Road'
        db.session.commit()

        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
        assert snapshot['currentScene']['name'] == 'Soot-Stained Chapel'

        triggered = _evaluate_state_segments_after_turn(turn, campaign)

        segment = CampaignSegment.query.filter_by(campaign_id=campaign.campaign_id).first()
        assert triggered
        assert triggered[0]['title'] == 'Hidden Chamber Unlocked'
        assert segment is not None
        assert segment.is_triggered is True
