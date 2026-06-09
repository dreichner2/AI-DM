from __future__ import annotations

from aidm_server.database import db
from aidm_server.models import DmTurn, Player, PlayerAction, TurnEvent
from tests.helpers import seed_world_campaign_player_session


def test_create_player_accepts_structured_inventory_and_get_returns_parsed_inventory(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Borin',
            'character_name': 'Borin Stoneshield',
            'sex': 'male',
            'inventory': [
                {'name': 'Rope', 'quantity': 2},
                'Torch',
            ],
        },
    )
    assert response.status_code == 201

    player_id = response.get_json()['player_id']
    player_response = client.get(f'/api/players/{player_id}')
    assert player_response.status_code == 200
    payload = player_response.get_json()

    assert payload['sex'] == 'male'
    assert payload['profile_image'] == '/profile-icons/human_male.png'
    assert payload['inventory'] == [
        {'name': 'Rope', 'quantity': 2, 'weight': 10},
        {'name': 'Torch', 'quantity': 1, 'weight': 1},
    ]


def test_create_player_validates_point_buy_stats(client, app):
    ids = seed_world_campaign_player_session(app)

    valid_response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Borin',
            'character_name': 'Borin Stoneshield',
            'stats': {
                'ability_scores': {
                    'strength': 15,
                    'dexterity': 14,
                    'constitution': 13,
                    'intelligence': 12,
                    'wisdom': 8,
                    'charisma': 8,
                },
                'point_buy': {'budget': 27},
            },
        },
    )
    assert valid_response.status_code == 201

    invalid_response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Mira',
            'character_name': 'Mira Bright',
            'stats': {
                'ability_scores': {
                    'strength': 15,
                    'dexterity': 15,
                    'constitution': 15,
                    'intelligence': 15,
                    'wisdom': 15,
                    'charisma': 15,
                },
                'point_buy': {'budget': 27},
            },
        },
    )
    assert invalid_response.status_code == 400
    assert invalid_response.get_json()['error_code'] == 'validation_error'


def test_update_player_persists_profile_sheet_stats_and_inventory(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.patch(
        f"/api/players/{ids['player_id']}",
        json={
            'name': 'Alice Updated',
            'character_name': 'Seraphina Vale',
            'race': 'Half-Elf',
            'sex': 'female',
            'char_class': 'Rogue',
            'level': '4',
            'stats': {'strength': 10, 'dexterity': 18},
            'character_sheet': {'current_hp': 22, 'max_hp': 28},
            'inventory': [{'name': 'Silver Key', 'quantity': 1}, 'Torch'],
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['name'] == 'Alice Updated'
    assert payload['character_name'] == 'Seraphina Vale'
    assert payload['race'] == 'Elf'
    assert payload['race_selection'] == {
        'raceId': 'elf',
        'raceName': 'Elf',
        'source': 'curated',
        'selectedOptions': {},
    }
    assert payload['sex'] == 'female'
    assert payload['profile_image'] == '/profile-icons/elf_female.png'
    assert payload['class_'] == 'Rogue'
    assert payload['char_class'] == 'Rogue'
    assert payload['level'] == 4
    assert payload['stats'] == {'strength': 10, 'dexterity': 18}
    assert payload['character_sheet'] == {'current_hp': 22, 'max_hp': 28}
    assert payload['inventory'] == [
        {'name': 'Silver Key', 'quantity': 1, 'weight': 0.1},
        {'name': 'Torch', 'quantity': 1, 'weight': 1},
    ]

    detail_response = client.get(f"/api/players/{ids['player_id']}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.get_json()
    assert detail_payload['stats'] == {'strength': 10, 'dexterity': 18}
    assert detail_payload['inventory'][0]['name'] == 'Silver Key'


def test_update_player_rejects_invalid_payloads(client, app):
    ids = seed_world_campaign_player_session(app)

    missing_response = client.patch('/api/players/99999', json={'name': 'Nobody'})
    assert missing_response.status_code == 404
    assert missing_response.get_json()['error_code'] == 'player_not_found'

    empty_name_response = client.patch(f"/api/players/{ids['player_id']}", json={'name': '   '})
    assert empty_name_response.status_code == 400
    assert empty_name_response.get_json()['error_code'] == 'validation_error'

    bad_level_response = client.patch(f"/api/players/{ids['player_id']}", json={'level': 21})
    assert bad_level_response.status_code == 400
    assert bad_level_response.get_json()['error_code'] == 'validation_error'


def test_create_player_requires_json_and_string_names(client, app):
    ids = seed_world_campaign_player_session(app)

    non_json_response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        data='not-json',
        content_type='text/plain',
    )
    assert non_json_response.status_code == 400
    assert non_json_response.get_json()['error_code'] == 'validation_error'

    numeric_name_response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={'name': 123, 'character_name': 'Seraphina'},
    )
    assert numeric_name_response.status_code == 400
    assert numeric_name_response.get_json()['error_code'] == 'validation_error'


def test_list_players_returns_404_for_missing_campaign(client):
    response = client.get('/api/players/campaigns/99999/players')

    assert response.status_code == 404
    assert response.get_json()['error_code'] == 'campaign_not_found'


def test_delete_player_removes_character_and_clears_history_references(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I test deletion.',
            dm_output='Deletion tested.',
        )
        event = TurnEvent(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            event_type='player_message',
            payload_json='{}',
        )
        action = PlayerAction(
            action_id=100,
            player_id=ids['player_id'],
            session_id=ids['session_id'],
            action_text='I test deletion.',
        )
        db.session.add_all([turn, event, action])
        db.session.commit()
        turn_id = turn.turn_id
        event_id = event.event_id

    response = client.delete(f"/api/players/{ids['player_id']}")

    assert response.status_code == 200
    assert response.get_json()['deleted'] is True
    assert client.get(f"/api/players/{ids['player_id']}").status_code == 404
    with app.app_context():
        assert db.session.get(Player, ids['player_id']) is None
        assert PlayerAction.query.filter_by(player_id=ids['player_id']).count() == 0
        assert db.session.get(DmTurn, turn_id).player_id is None
        assert db.session.get(TurnEvent, event_id).player_id is None
