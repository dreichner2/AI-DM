from __future__ import annotations

from aidm_server.database import db
from aidm_server.models import Session, safe_json_dumps
from aidm_server.services.scene_state import scene_state_for_session
from tests.helpers import seed_world_campaign_player_session


def test_scene_state_prefers_authored_music_tag(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        session.state_snapshot = safe_json_dumps(
            {
                'currentScene': {
                    'locationId': 'market-square',
                    'name': 'Market Square',
                    'sceneType': 'social',
                    'mood': 'warm',
                    'dangerLevel': 1,
                    'combatState': 'none',
                    'musicTag': 'town',
                },
                'combat': {'status': 'none'},
            },
            {},
        )
        db.session.commit()

        payload = scene_state_for_session(ids['session_id'], acting_player_id=ids['player_id'])

    assert payload == {
        'session_id': ids['session_id'],
        'location_id': 'market-square',
        'location_name': 'Market Square',
        'scene_type': 'social',
        'mood': 'warm',
        'danger_level': 1,
        'combat_state': 'none',
        'in_combat': False,
        'music_tag': 'town',
        'acting_player_id': ids['player_id'],
    }


def test_scene_state_preserves_authored_music_tag_during_combat(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        session.state_snapshot = safe_json_dumps(
            {
                'currentScene': {
                    'name': 'Broken Bridge',
                    'sceneType': 'travel',
                    'dangerLevel': 8,
                    'combatState': 'active',
                    'musicTag': 'forest',
                },
                'combat': {'status': 'active'},
            },
            {},
        )
        db.session.commit()

        payload = scene_state_for_session(ids['session_id'])

    assert payload['in_combat'] is True
    assert payload['combat_state'] == 'active'
    assert payload['music_tag'] == 'forest'


def test_scene_state_falls_back_from_scene_type_and_danger(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        session.state_snapshot = safe_json_dumps(
            {
                'currentScene': {
                    'name': 'Vault Stairs',
                    'sceneType': 'dungeon',
                    'dangerLevel': 7,
                    'combatState': 'none',
                },
                'combat': {'status': 'none'},
            },
            {},
        )
        db.session.commit()

        payload = scene_state_for_session(ids['session_id'])

    assert payload['music_tag'] == 'tension'
    assert payload['location_name'] == 'Vault Stairs'
