from __future__ import annotations

import json
import threading

from sqlalchemy import event
from sqlalchemy.orm import Session as SqlAlchemySession

from aidm_server.contracts import ProviderResponse
import aidm_server.blueprints.creatures as creatures_blueprint
import aidm_server.creatures.generator as generator_module
import aidm_server.services.session_state_mutation as mutation_module
from aidm_server.creatures.generator import deterministic_generated_creature
from aidm_server.database import db
from aidm_server.models import (
    BestiaryEntry,
    CombatDebugEvent,
    CombatEncounter,
    Session,
    SessionStateMutationAudit,
    safe_json_dumps,
)
from tests.helpers import seed_world_campaign_player_session


def _pool_checkout_probe(engine):
    lock = threading.Lock()
    active = 0

    def checkout(*_args):
        nonlocal active
        with lock:
            active += 1

    def checkin(*_args):
        nonlocal active
        with lock:
            active -= 1

    def snapshot() -> int:
        with lock:
            return active

    event.listen(engine, 'checkout', checkout)
    event.listen(engine, 'checkin', checkin)
    return snapshot, checkout, checkin


def _enable_creature_helper(app) -> None:
    app.config['AIDM_CREATURE_HELPER_IN_TESTS'] = True
    app.config['AIDM_CREATURE_HELPER_ENABLED'] = 'true'


def _generated_combat_payload() -> dict:
    return {
        'regionId': 'voidglass_boundary_region',
        'locationId': 'voidglass_boundary_vault',
        'encounterPurpose': 'ritual',
        'desiredRole': 'controller',
        'desiredCreatureType': 'aberration',
        'themeTags': ['voidglass_boundary', 'gravity_prism'],
        'descriptionHint': 'A unique voidglass boundary sentinel.',
        'partyLevel': 3,
        'difficulty': 'standard',
        'allowGeneration': True,
        'allowVariants': False,
        'saveGenerated': True,
    }


def _generated_provider_response() -> ProviderResponse:
    creature = deterministic_generated_creature(
        {
            'creatureConcept': 'Voidglass Boundary Sentinel',
            'themeTags': ['voidglass_boundary', 'gravity_prism'],
            'partyLevel': 3,
            'partySize': 1,
            'difficulty': 'standard',
            'desiredRole': 'controller',
            'desiredCreatureType': 'aberration',
        }
    )
    return ProviderResponse(text=json.dumps(creature), provider='fake', model='fake-creature')


def test_combat_start_provider_phases_release_database_and_commit_atomically(client, app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    _enable_creature_helper(app)
    provider_phases: list[str] = []
    commit_count = 0

    with app.app_context():
        engine = db.engine
    snapshot, checkout, checkin = _pool_checkout_probe(engine)

    class FakeCreatureProvider:
        def generate(self, _request):
            assert db.session.registry.has() is False
            assert snapshot() == 0
            provider_phases.append('creature')
            return _generated_provider_response()

    def plan_intents(_combat):
        assert db.session.registry.has() is False
        assert snapshot() == 0
        provider_phases.append('intent')
        return {'intents': [], 'summaryForDm': 'Boundary-safe intent plan.'}

    def after_commit(_session):
        nonlocal commit_count
        commit_count += 1

    monkeypatch.setattr(generator_module, 'get_helper_provider', lambda **_kwargs: FakeCreatureProvider())
    monkeypatch.setattr(creatures_blueprint, 'plan_enemy_intents', plan_intents)
    event.listen(SqlAlchemySession, 'after_commit', after_commit)
    try:
        response = client.post(
            f"/api/sessions/{ids['session_id']}/combat/start",
            json=_generated_combat_payload(),
        )
    finally:
        event.remove(SqlAlchemySession, 'after_commit', after_commit)
        event.remove(engine, 'checkout', checkout)
        event.remove(engine, 'checkin', checkin)

    assert response.status_code == 200
    assert provider_phases == ['creature', 'intent']
    assert commit_count == 1
    assert response.get_json()['combat']['status'] == 'active'
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        state = json.loads(session_obj.state_snapshot)
        bestiary_entries = BestiaryEntry.query.filter_by(
            campaign_id=ids['campaign_id'],
            source='generated',
        ).all()
        encounter = CombatEncounter.query.filter_by(session_id=ids['session_id']).one()
        debug_event = CombatDebugEvent.query.filter_by(
            session_id=ids['session_id'],
            event_type='api_combat_start',
        ).one()
        audit = SessionStateMutationAudit.query.filter_by(
            session_id=ids['session_id'],
            source='api.combat.start',
        ).one()

    assert len(bestiary_entries) == 1
    assert state['combat']['status'] == 'active'
    assert encounter.status == 'active'
    assert debug_event.combat_encounter_id == encounter.combat_encounter_id
    assert debug_event.event_type == 'api_combat_start'
    assert audit.state_revision == 1


def test_combat_start_validation_rejection_discards_pending_bestiary_and_encounter(
    client,
    app,
    monkeypatch,
):
    ids = seed_world_campaign_player_session(app)
    _enable_creature_helper(app)

    class FakeCreatureProvider:
        def generate(self, _request):
            assert db.session.registry.has() is False
            return _generated_provider_response()

    original_validate = mutation_module.validate_state_changes

    def reject_combat_start(*, state, changes, **kwargs):
        if any(
            isinstance(change, dict) and change.get('type') == 'combat.start'
            for change in changes
        ):
            return {
                'accepted': [],
                'modified': [],
                'rejected': [
                    {
                        'change': next(
                            change
                            for change in changes
                            if isinstance(change, dict)
                            and change.get('type') == 'combat.start'
                        ),
                        'reason': 'Forced combat validator rejection.',
                    }
                ],
            }
        return original_validate(state=state, changes=changes, **kwargs)

    monkeypatch.setattr(
        generator_module,
        'get_helper_provider',
        lambda **_kwargs: FakeCreatureProvider(),
    )
    monkeypatch.setattr(
        creatures_blueprint,
        'plan_enemy_intents',
        lambda _combat: {'intents': [], 'summaryForDm': 'Rejected intent plan.'},
    )
    monkeypatch.setattr(mutation_module, 'validate_state_changes', reject_combat_start)

    response = client.post(
        f"/api/sessions/{ids['session_id']}/combat/start",
        json=_generated_combat_payload(),
    )

    assert response.status_code == 200
    assert response.get_json()['validation']['rejected'][0]['reason'] == (
        'Forced combat validator rejection.'
    )
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        state = json.loads(session_obj.state_snapshot or '{}')
        debug_events = CombatDebugEvent.query.filter_by(
            session_id=ids['session_id'],
            event_type='api_combat_start',
        ).count()

        assert BestiaryEntry.query.filter_by(
            campaign_id=ids['campaign_id'],
            source='generated',
        ).count() == 0
        assert CombatEncounter.query.filter_by(session_id=ids['session_id']).count() == 0
        assert state.get('combat', {}).get('status') != 'active'
        assert debug_events == 0


def test_combat_start_debug_failure_rolls_back_all_accepted_writes(
    client,
    app,
    monkeypatch,
):
    ids = seed_world_campaign_player_session(app)
    _enable_creature_helper(app)

    class FakeCreatureProvider:
        def generate(self, _request):
            return _generated_provider_response()

    monkeypatch.setattr(
        generator_module,
        'get_helper_provider',
        lambda **_kwargs: FakeCreatureProvider(),
    )
    monkeypatch.setattr(
        creatures_blueprint,
        'plan_enemy_intents',
        lambda _combat: {'intents': [], 'summaryForDm': 'Accepted intent plan.'},
    )
    monkeypatch.setattr(
        creatures_blueprint,
        'record_combat_debug_event',
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError('forced combat debug failure')
        ),
    )

    response = client.post(
        f"/api/sessions/{ids['session_id']}/combat/start",
        json=_generated_combat_payload(),
    )

    assert response.status_code == 500

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        state = json.loads(session_obj.state_snapshot or '{}')

        assert BestiaryEntry.query.filter_by(
            campaign_id=ids['campaign_id'],
            source='generated',
        ).count() == 0
        assert CombatEncounter.query.filter_by(session_id=ids['session_id']).count() == 0
        assert SessionStateMutationAudit.query.filter_by(
            session_id=ids['session_id'],
            source='api.combat.start',
        ).count() == 0
        assert state.get('combat', {}).get('status') != 'active'


def test_combat_start_revision_drift_discards_pending_bestiary_plan(client, app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    _enable_creature_helper(app)

    class FakeCreatureProvider:
        def generate(self, _request):
            assert db.session.registry.has() is False
            return _generated_provider_response()

    def plan_intents(_combat):
        assert db.session.registry.has() is False
        session_obj = db.session.get(Session, ids['session_id'])
        state = json.loads(session_obj.state_snapshot or '{}')
        state['stateRevision'] = 1
        state['concurrentMarker'] = 'intent-provider-drift'
        session_obj.state_snapshot = safe_json_dumps(state, {})
        db.session.commit()
        db.session.remove()
        return {'intents': [], 'summaryForDm': 'Stale intent plan.'}

    monkeypatch.setattr(generator_module, 'get_helper_provider', lambda **_kwargs: FakeCreatureProvider())
    monkeypatch.setattr(creatures_blueprint, 'plan_enemy_intents', plan_intents)

    response = client.post(
        f"/api/sessions/{ids['session_id']}/combat/start",
        json=_generated_combat_payload(),
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload['error_code'] == 'state_conflict'
    assert payload['details']['expected_state_revision'] == 0
    assert payload['details']['actual_state_revision'] == 1
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        state = json.loads(session_obj.state_snapshot)
        generated_entries = BestiaryEntry.query.filter_by(
            campaign_id=ids['campaign_id'],
            source='generated',
        ).count()
        encounters = CombatEncounter.query.filter_by(session_id=ids['session_id']).count()
        debug_events = CombatDebugEvent.query.filter_by(
            session_id=ids['session_id'],
            event_type='api_combat_start',
        ).count()

    assert state['stateRevision'] == 1
    assert state['concurrentMarker'] == 'intent-provider-drift'
    assert state.get('combat', {}).get('status') != 'active'
    assert generated_entries == 0
    assert encounters == 0
    assert debug_events == 0
