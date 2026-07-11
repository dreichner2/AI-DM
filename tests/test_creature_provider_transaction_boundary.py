from __future__ import annotations

import json
import threading

import pytest
from sqlalchemy import event

from aidm_server.contracts import ProviderResponse
import aidm_server.creatures.generator as generator_module
import aidm_server.creatures.resolver as resolver_module
from aidm_server.creatures.generator import deterministic_generated_creature, generate_new_creature
from aidm_server.creatures.repository import save_bestiary_entry as repository_save_bestiary_entry
from aidm_server.creatures.resolver import (
    persist_creature_resolution_plan,
    plan_creatures_for_encounter,
    resolve_creature_for_encounter,
)
from aidm_server.database import db, release_clean_scoped_session
from aidm_server.models import BestiaryEntry, Campaign
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


def _generation_request(ids: dict[str, int], *, groups: int = 1) -> dict:
    enemy_groups = [
        {
            'label': f'generated_{index + 1}',
            'descriptionHint': f'prismatic gravity jailer {index + 1}',
            'themeTags': [f'prismatic_{index + 1}', 'gravity'],
        }
        for index in range(groups)
    ]
    return {
        'campaignId': ids['campaign_id'],
        'sessionId': ids['session_id'],
        'regionId': 'clockwork_vault',
        'encounterPurpose': 'ritual',
        'desiredRole': 'controller',
        'desiredCreatureType': 'aberration',
        'themeTags': ['prismatic', 'gravity'],
        'partyLevel': 3,
        'partySize': 4,
        'difficulty': 'standard',
        'allowGeneration': True,
        'allowVariants': False,
        'saveGenerated': True,
        'enemyGroups': enemy_groups,
    }


def test_group_generation_plan_is_clean_until_explicit_persistence(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    _enable_creature_helper(app)
    events: list[str] = []
    provider_calls = 0

    with app.app_context():
        engine = db.engine
    snapshot, checkout, checkin = _pool_checkout_probe(engine)

    class FakeCreatureProvider:
        def generate(self, _request):
            nonlocal provider_calls
            assert db.session.registry.has() is False
            assert snapshot() == 0
            provider_calls += 1
            events.append(f'provider_{provider_calls}')
            creature = deterministic_generated_creature(
                {
                    'creatureConcept': f'Boundary Creature {provider_calls}',
                    'themeTags': ['prismatic', 'gravity'],
                    'partyLevel': 3,
                    'partySize': 4,
                    'difficulty': 'standard',
                    'desiredRole': 'controller',
                    'desiredCreatureType': 'aberration',
                }
            )
            return ProviderResponse(
                text=json.dumps(creature),
                provider='fake',
                model=f'fake-creature-{provider_calls}',
            )

    def before_provider_call() -> None:
        events.append('before_provider')
        release_clean_scoped_session(boundary='creature provider')

    def after_provider_call() -> None:
        events.append('after_provider')

    def save_bestiary_entry(**kwargs):
        events.append(f"save_{kwargs['creature']['id']}")
        return repository_save_bestiary_entry(**kwargs)

    monkeypatch.setattr(generator_module, 'get_helper_provider', lambda **_kwargs: FakeCreatureProvider())
    monkeypatch.setattr(resolver_module, 'save_bestiary_entry', save_bestiary_entry)

    try:
        with app.app_context():
            plan = plan_creatures_for_encounter(
                _generation_request(ids, groups=2),
                workspace_id='owner',
                before_provider_call=before_provider_call,
                after_provider_call=after_provider_call,
            )
            scoped_session = db.session()
            assert not scoped_session.new
            assert not scoped_session.dirty
            assert not scoped_session.deleted
            assert plan.persisted is False
            assert len(plan.pending_saves) == 2
            assert BestiaryEntry.query.filter_by(
                campaign_id=ids['campaign_id'],
                source='generated',
            ).count() == 0
            assert events == [
                'before_provider',
                'provider_1',
                'before_provider',
                'provider_2',
                'after_provider',
            ]

            result = persist_creature_resolution_plan(plan)
            assert plan.persisted is True
            saved_ids = {
                entry.creature_id
                for entry in BestiaryEntry.query.filter_by(campaign_id=ids['campaign_id'], source='generated').all()
            }
            assert persist_creature_resolution_plan(plan) is result

        assert result['generated'] is True
        assert result['savedToBestiary'] is True
        assert provider_calls == 2
        assert saved_ids == {'boundary_creature_1', 'boundary_creature_2'}
        assert events == [
            'before_provider',
            'provider_1',
            'before_provider',
            'provider_2',
            'after_provider',
            'save_boundary_creature_1',
            'save_boundary_creature_2',
        ]
    finally:
        event.remove(engine, 'checkout', checkout)
        event.remove(engine, 'checkin', checkin)


def test_generation_boundary_failure_is_not_treated_as_provider_fallback(app, monkeypatch):
    _enable_creature_helper(app)

    class UnexpectedProvider:
        def generate(self, _request):
            raise AssertionError('provider must not run after a failed database boundary')

    monkeypatch.setattr(generator_module, 'get_helper_provider', lambda **_kwargs: UnexpectedProvider())

    def fail_boundary() -> None:
        raise RuntimeError('dirty creature transaction')

    with app.app_context(), pytest.raises(RuntimeError, match='dirty creature transaction'):
        generate_new_creature(
            {'creatureConcept': 'Boundary Failure'},
            before_provider_call=fail_boundary,
        )


def test_stale_campaign_is_revalidated_before_generated_entry_flush(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    _enable_creature_helper(app)

    class FakeCreatureProvider:
        def generate(self, _request):
            creature = deterministic_generated_creature(
                {
                    'creatureConcept': 'Stale Target Creature',
                    'themeTags': ['prismatic', 'gravity'],
                    'partyLevel': 3,
                    'partySize': 4,
                    'difficulty': 'standard',
                    'desiredRole': 'controller',
                    'desiredCreatureType': 'aberration',
                }
            )
            return ProviderResponse(text=json.dumps(creature), provider='fake', model='fake-creature')

    def after_provider_call() -> None:
        campaign = db.session.get(Campaign, ids['campaign_id'])
        assert campaign is not None
        campaign.workspace_id = 'moved-workspace'
        db.session.commit()
        db.session.remove()

    monkeypatch.setattr(generator_module, 'get_helper_provider', lambda **_kwargs: FakeCreatureProvider())

    with app.app_context():
        with pytest.raises(LookupError, match='no longer available in workspace owner'):
            resolve_creature_for_encounter(
                _generation_request(ids),
                workspace_id='owner',
                before_provider_call=lambda: release_clean_scoped_session(boundary='creature provider'),
                after_provider_call=after_provider_call,
            )
        assert BestiaryEntry.query.filter_by(
            campaign_id=ids['campaign_id'],
            source='generated',
        ).count() == 0
