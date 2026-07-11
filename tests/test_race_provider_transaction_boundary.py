from __future__ import annotations

import json
import threading

import pytest
from sqlalchemy import event

import aidm_server.blueprints.races as races_module
from aidm_server.contracts import ProviderResponse
from aidm_server.database import db
from aidm_server.provider_priority import provider_priority_gate


def _pool_checkout_probe(engine):
    lock = threading.Lock()
    active = 0
    total = 0

    def checkout(*_args):
        nonlocal active, total
        with lock:
            active += 1
            total += 1

    def checkin(*_args):
        nonlocal active
        with lock:
            active -= 1

    def snapshot() -> tuple[int, int]:
        with lock:
            return active, total

    event.listen(engine, 'checkout', checkout)
    event.listen(engine, 'checkin', checkin)
    return snapshot, checkout, checkin


def test_custom_race_provider_releases_auth_database_lease(
    app,
    client,
    monkeypatch,
):
    app.config['AIDM_AUTH_REQUIRED'] = True
    app.config['AIDM_API_AUTH_TOKENS'] = ['race-boundary-token']
    app.config['AIDM_API_AUTH_TOKEN_WORKSPACES'] = {
        'race-boundary-token': 'race-boundary-workspace',
    }
    monkeypatch.setattr(races_module, '_custom_race_helper_enabled', lambda: True)

    with app.app_context():
        engine = db.engine
    snapshot, checkout, checkin = _pool_checkout_probe(engine)

    class RaceProvider:
        def generate(self, _request):
            active, total = snapshot()
            assert total >= 1
            assert active == 0
            assert db.session.registry.has() is False
            assert db.session().in_transaction() is False
            assert snapshot()[0] == 0
            return ProviderResponse(
                text=json.dumps({'name': 'Boundary Folk', 'traits': []}),
                provider='fake',
                model='race-boundary-model',
            )

    monkeypatch.setattr(
        races_module,
        'get_helper_provider',
        lambda **_kwargs: RaceProvider(),
    )

    try:
        response = client.post(
            '/api/custom-races/generate',
            headers={'Authorization': 'Bearer race-boundary-token'},
            json={'prompt': 'Create a race called Boundary Folk.'},
        )
    finally:
        event.remove(engine, 'checkout', checkout)
        event.remove(engine, 'checkin', checkin)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['draftRace']['name'] == 'Boundary Folk'
    assert payload['generationSource'] == 'race-boundary-model'


def test_custom_race_boundary_error_is_not_swallowed_as_provider_fallback(
    app,
    monkeypatch,
):
    monkeypatch.setattr(races_module, '_custom_race_helper_enabled', lambda: True)
    provider_called = False
    initial_snapshot = provider_priority_gate.snapshot()

    class RaceProvider:
        def generate(self, _request):
            nonlocal provider_called
            provider_called = True
            raise AssertionError('provider must not run after a boundary failure')

    def fail_boundary() -> None:
        reservation_snapshot = provider_priority_gate.snapshot()
        assert reservation_snapshot.waiting_foreground == initial_snapshot.waiting_foreground + 1
        assert reservation_snapshot.active_foreground == initial_snapshot.active_foreground
        raise RuntimeError('dirty custom race boundary')

    monkeypatch.setattr(
        races_module,
        'get_helper_provider',
        lambda **_kwargs: RaceProvider(),
    )

    with app.app_context(), pytest.raises(RuntimeError, match='dirty custom race boundary'):
        races_module._generate_custom_race_draft(
            'Create a race called Boundary Folk.',
            strictness='standard',
            before_provider_call=fail_boundary,
        )

    assert provider_called is False
    assert provider_priority_gate.snapshot() == initial_snapshot
