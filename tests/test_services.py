from __future__ import annotations

from sqlalchemy import text

from aidm_server.database import db
from aidm_server.models import (
    Campaign,
    CampaignSegment,
    DmCoherenceFeedback,
    DmTurn,
    Map,
    PlayerAction,
    Session,
    SessionLogEntry,
    SessionState,
    StoryEntity,
    StoryFact,
    StoryThread,
    TurnCanonUpdate,
    TurnEvent,
)
from aidm_server.services.session_lifecycle import delete_session_record
from aidm_server.services.workspace import campaign_workspace_payload
from tests.helpers import seed_world_campaign_player_session


def test_campaign_workspace_service_matches_workspace_endpoint(client, app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        map_obj = Map(
            world_id=ids['world_id'],
            campaign_id=ids['campaign_id'],
            title='Service Map',
            description='Loaded by the service.',
            map_data='{"tiles": []}',
        )
        segment = CampaignSegment(
            campaign_id=ids['campaign_id'],
            title='Service Segment',
            description='Loaded by the service.',
            trigger_condition='when service tests run',
            tags='service',
            is_triggered=False,
        )
        db.session.add_all([map_obj, segment])
        db.session.commit()

        campaign = db.session.get(Campaign, ids['campaign_id'])
        service_payload = campaign_workspace_payload(campaign)

    endpoint_payload = client.get(f"/api/campaigns/{ids['campaign_id']}/workspace").get_json()

    assert service_payload == endpoint_payload
    assert service_payload['summary']['session_count'] == 1
    assert service_payload['summary']['player_count'] == 1
    assert service_payload['summary']['map_count'] == 1
    assert service_payload['summary']['segment_count'] == 1


def test_session_lifecycle_service_hard_delete_removes_session_origin_canon(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I study the gate.',
            dm_output='The gate remembers the old road.',
            status='completed',
            outcome_status='resolved',
        )
        db.session.add(turn)
        db.session.flush()
        entity = StoryEntity(
            campaign_id=ids['campaign_id'],
            session_id=ids['session_id'],
            entity_type='location',
            name='Old Road Gate',
            first_seen_turn_id=turn.turn_id,
            last_seen_turn_id=turn.turn_id,
        )
        db.session.add(entity)
        db.session.flush()
        fact = StoryFact(
            campaign_id=ids['campaign_id'],
            subject_entity_id=entity.entity_id,
            predicate='remembers',
            value_text='the old road',
            source_turn_id=turn.turn_id,
        )
        thread = StoryThread(
            campaign_id=ids['campaign_id'],
            title='Open the Old Road Gate',
            origin_turn_id=turn.turn_id,
            last_touched_turn_id=turn.turn_id,
            resolved_turn_id=turn.turn_id,
        )
        db.session.add_all([fact, thread, SessionState(session_id=ids['session_id'], rolling_summary='summary')])
        db.session.commit()
        entity_id = entity.entity_id
        fact_id = fact.fact_id
        thread_id = thread.thread_id

        session_obj = db.session.get(Session, ids['session_id'])
        result = delete_session_record(session_obj, hard_delete=True)
        db.session.commit()

        assert result.hard_deleted is True
        assert result.payload == {'deleted': True, 'session_id': ids['session_id']}
        assert db.session.get(Session, ids['session_id']) is None
        assert SessionState.query.filter_by(session_id=ids['session_id']).count() == 0
        assert DmTurn.query.filter_by(session_id=ids['session_id']).count() == 0

        assert db.session.get(StoryEntity, entity_id) is None
        assert db.session.get(StoryFact, fact_id) is None
        assert db.session.get(StoryThread, thread_id) is None


def test_database_session_delete_cascades_owned_rows_and_nulls_canon_refs(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I study the gate.',
            dm_output='The gate remembers the old road.',
            status='completed',
            outcome_status='resolved',
        )
        db.session.add(turn)
        db.session.flush()
        entity = StoryEntity(
            campaign_id=ids['campaign_id'],
            session_id=ids['session_id'],
            entity_type='location',
            name='Old Road Gate',
            first_seen_turn_id=turn.turn_id,
            last_seen_turn_id=turn.turn_id,
        )
        db.session.add(entity)
        db.session.flush()
        fact = StoryFact(
            campaign_id=ids['campaign_id'],
            subject_entity_id=entity.entity_id,
            predicate='remembers',
            value_text='the old road',
            source_turn_id=turn.turn_id,
        )
        thread = StoryThread(
            campaign_id=ids['campaign_id'],
            title='Open the Old Road Gate',
            origin_turn_id=turn.turn_id,
            last_touched_turn_id=turn.turn_id,
            resolved_turn_id=turn.turn_id,
        )
        db.session.add_all(
            [
                fact,
                thread,
                TurnCanonUpdate(turn_id=turn.turn_id, campaign_id=ids['campaign_id']),
                TurnEvent(
                    session_id=ids['session_id'],
                    campaign_id=ids['campaign_id'],
                    turn_id=turn.turn_id,
                    player_id=ids['player_id'],
                    event_type='test_event',
                    payload_json='{}',
                ),
                SessionLogEntry(session_id=ids['session_id'], message='log', entry_type='dm'),
                SessionState(session_id=ids['session_id'], rolling_summary='summary'),
                PlayerAction(
                    player_id=ids['player_id'],
                    session_id=ids['session_id'],
                    action_text='I study the gate.',
                ),
                DmCoherenceFeedback(session_id=ids['session_id'], turn_id=turn.turn_id, coherence_score=4),
            ]
        )
        db.session.commit()
        entity_id = entity.entity_id
        fact_id = fact.fact_id
        thread_id = thread.thread_id
        turn_id = turn.turn_id

        db.session.execute(text('DELETE FROM sessions WHERE session_id = :session_id'), {'session_id': ids['session_id']})
        db.session.commit()

        assert db.session.get(Session, ids['session_id']) is None
        assert DmTurn.query.filter_by(session_id=ids['session_id']).count() == 0
        assert TurnCanonUpdate.query.filter_by(turn_id=turn_id).count() == 0
        assert TurnEvent.query.filter_by(session_id=ids['session_id']).count() == 0
        assert SessionLogEntry.query.filter_by(session_id=ids['session_id']).count() == 0
        assert SessionState.query.filter_by(session_id=ids['session_id']).count() == 0
        assert PlayerAction.query.filter_by(session_id=ids['session_id']).count() == 0
        assert DmCoherenceFeedback.query.filter_by(session_id=ids['session_id']).count() == 0

        entity = db.session.get(StoryEntity, entity_id)
        fact = db.session.get(StoryFact, fact_id)
        thread = db.session.get(StoryThread, thread_id)
        assert entity.session_id is None
        assert entity.first_seen_turn_id is None
        assert entity.last_seen_turn_id is None
        assert fact.source_turn_id is None
        assert thread.origin_turn_id is None
        assert thread.last_touched_turn_id is None
        assert thread.resolved_turn_id is None
