"""A campaign pack may start the party's clock at an authored day and phase."""

from __future__ import annotations

from aidm_server.database import db
from aidm_server.game_clock import public_clock
from aidm_server.models import Session, safe_json_loads
from aidm_server.services.session_chapters import initial_campaign_pack_snapshot


def _pack(**starting_state) -> dict:
    return {
        'packId': 'clock_pack',
        'title': 'Clock Pack',
        'locations': [{'id': 'inn', 'name': 'Inn'}],
        'startingState': {'locationId': 'inn', **starting_state},
    }


def _import(client, app, pack: dict) -> dict:
    response = client.post('/api/campaigns/import-pack', json={'pack': pack})
    assert response.status_code == 201, response.get_json()
    with app.app_context():
        return safe_json_loads(db.session.get(Session, response.get_json()['session_id']).state_snapshot, {})


def test_an_authored_starting_clock_is_where_the_adventure_begins(client, app):
    snapshot = _import(client, app, _pack(clock={'day': 3, 'phase': 'night'}))

    assert snapshot['clock'] == {'day': 3, 'phase': 'night', 'shortRestsSinceLong': 0}
    assert public_clock(snapshot['clock'])['label'] == 'Day 3 · Night'


def test_without_one_the_clock_starts_on_the_first_morning_as_before(client, app):
    snapshot = _import(client, app, _pack())

    assert 'clock' not in snapshot
    assert public_clock(snapshot.get('clock'))['label'] == 'Day 1 · Morning'


def test_a_fresh_chapter_opens_at_the_authored_clock_too(app):
    with app.app_context():
        snapshot = initial_campaign_pack_snapshot(
            _pack(clock={'day': 1, 'phase': 'evening'}),
            campaign_id=1,
            imported_at='2026-11-10T00:00:00',
        )

    assert snapshot['clock']['phase'] == 'evening'


def test_an_unknown_phase_is_rejected_on_import(client):
    response = client.post('/api/campaigns/import-pack', json={'pack': _pack(clock={'day': 1, 'phase': 'dusk'})})

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'invalid_campaign_pack_schema'
    assert 'startingState.clock.phase' in response.get_json()['error']
