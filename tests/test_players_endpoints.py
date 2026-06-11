from __future__ import annotations

from aidm_server.database import db
from aidm_server.models import DmTurn, Player, PlayerAction, Session, TurnEvent, safe_json_dumps, safe_json_loads
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


def test_create_player_assigns_starting_inventory_from_class(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Borin',
            'character_name': 'Borin Stoneshield',
            'char_class': 'Fighter - Champion',
        },
    )
    assert response.status_code == 201

    player_id = response.get_json()['player_id']
    player_response = client.get(f'/api/players/{player_id}')
    assert player_response.status_code == 200
    inventory = {item['name']: item for item in player_response.get_json()['inventory']}

    assert inventory['Longsword']['type'] == 'weapon'
    assert inventory['Longsword']['equipped'] is True
    assert inventory['Longsword']['slot'] == 'main_hand'
    assert inventory['Shield']['equipped'] is True
    assert inventory['Shield']['slot'] == 'off_hand'
    assert inventory['Chain Mail']['equipped'] is True
    assert inventory['Chain Mail']['slot'] == 'body_armor'
    assert inventory['Ration']['quantity'] == 5
    assert inventory['Torch']['quantity'] == 5


def test_create_player_assigns_starting_inventory_from_extended_class(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Cass',
            'character_name': 'Cass Quickshot',
            'char_class': 'Gunslinger - Sniper',
        },
    )
    assert response.status_code == 201

    player_response = client.get(f"/api/players/{response.get_json()['player_id']}")
    assert player_response.status_code == 200
    inventory = {item['name']: item for item in player_response.get_json()['inventory']}

    assert inventory['Pistol']['type'] == 'weapon'
    assert inventory['Pistol']['equipped'] is True
    assert inventory['Pistol']['slot'] == 'main_hand'
    assert inventory['Leather Armor']['equipped'] is True
    assert inventory['Ammunition']['quantity'] == 20
    assert "Gunsmith's Tools" in inventory


def test_create_player_assigns_starting_spells_from_magical_class_and_race(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Nessa',
            'character_name': 'Nessa Ember',
            'race': 'Tiefling',
            'char_class': 'Wizard - Evoker',
        },
    )
    assert response.status_code == 201

    player_response = client.get(f"/api/players/{response.get_json()['player_id']}")
    assert player_response.status_code == 200
    sheet = player_response.get_json()['character_sheet']
    known = {spell['name'] for spell in sheet['spellbook']['knownSpells']}

    assert {'Fire Bolt', 'Magic Missile', 'Shield', 'Detect Magic'} <= known
    assert {'Thaumaturgy', 'Hellish Rebuke'} <= known


def test_update_player_level_up_unlocks_more_spells(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Nessa',
            'character_name': 'Nessa Ember',
            'char_class': 'Wizard',
            'level': 1,
        },
    )
    assert response.status_code == 201
    player_id = response.get_json()['player_id']

    update_response = client.patch(f'/api/players/{player_id}', json={'level': 5})
    assert update_response.status_code == 200
    sheet = update_response.get_json()['character_sheet']
    known = {spell['name'] for spell in sheet['spellbook']['knownSpells']}

    assert 'Fireball' in known
    assert 'Counterspell' in known
    assert 'Magic Missile' in known


def test_create_player_respects_explicit_empty_inventory_over_class_starter(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Mira',
            'character_name': 'Mira Quickstep',
            'char_class': 'Rogue',
            'inventory': [],
        },
    )
    assert response.status_code == 201

    player_response = client.get(f"/api/players/{response.get_json()['player_id']}")
    assert player_response.status_code == 200
    assert player_response.get_json()['inventory'] == []


def test_get_player_does_not_backfill_starting_inventory_for_existing_blank_player(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.get(f"/api/players/{ids['player_id']}")
    assert response.status_code == 200
    assert response.get_json()['inventory'] == []

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert not player.inventory


def test_repair_starting_loadout_backfills_inventory_for_existing_blank_player(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(f"/api/players/{ids['player_id']}/repair-starting-loadout")
    assert response.status_code == 200
    payload = response.get_json()
    inventory = {item['name']: item for item in payload['inventory']}

    assert payload['repaired'] == {'inventory': True, 'spells': True}
    assert inventory['Longbow']['equipped'] is True
    assert inventory['Longbow']['slot'] == 'two_hands'
    assert inventory['Leather Armor']['slot'] == 'body_armor'
    assert inventory['Arrow']['quantity'] == 20

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert player.inventory


def test_get_player_does_not_backfill_starting_spells_for_existing_blank_player(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.class_ = 'Shapeshifter'
        player.race = 'Changeling'
        player.race_selection = None
        player.character_sheet = None
        db.session.commit()

    response = client.get(f"/api/players/{ids['player_id']}")
    assert response.status_code == 200
    assert response.get_json()['character_sheet'] is None

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert player.character_sheet is None


def test_repair_starting_loadout_backfills_spells_for_existing_blank_player(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps([], [])
        player.class_ = 'Shapeshifter'
        player.race = 'Changeling'
        player.race_selection = None
        player.character_sheet = None
        db.session.commit()

    response = client.post(f"/api/players/{ids['player_id']}/repair-starting-loadout")
    assert response.status_code == 200
    payload = response.get_json()
    sheet = payload['character_sheet']
    known = {spell['name'] for spell in sheet['spellbook']['knownSpells']}

    assert payload['repaired'] == {'inventory': False, 'spells': True}
    assert 'Primal Shift' in known
    assert 'Disguise Self' in known

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert player.character_sheet


def test_get_player_does_not_backfill_explicit_empty_inventory(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps([], [])
        db.session.commit()

    response = client.get(f"/api/players/{ids['player_id']}")
    assert response.status_code == 200
    assert response.get_json()['inventory'] == []


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
    assert payload['character_sheet']['current_hp'] == 22
    assert payload['character_sheet']['max_hp'] == 28
    known = {spell['name'] for spell in payload['character_sheet']['spellbook']['knownSpells']}
    assert {'Minor Illusion', 'Detect Magic'} <= known
    assert payload['inventory'] == [
        {'name': 'Silver Key', 'quantity': 1, 'weight': 0.1},
        {'name': 'Torch', 'quantity': 1, 'weight': 1},
    ]

    detail_response = client.get(f"/api/players/{ids['player_id']}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.get_json()
    assert detail_payload['stats'] == {'strength': 10, 'dexterity': 18}
    assert detail_payload['inventory'][0]['name'] == 'Silver Key'


def test_manual_equipment_endpoint_enforces_slots_and_preserves_inventory_payload(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps(
            [
                {'id': 'great', 'name': 'Greatsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'greatsword'},
                {'id': 'long', 'name': 'Longsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'sword', 'equipped': True, 'slot': 'main_hand'},
                {'id': 'dagger', 'name': 'Dagger', 'quantity': 1, 'type': 'weapon', 'subtype': 'dagger', 'equipped': True, 'slot': 'off_hand'},
                {'id': 'hood', 'name': 'Travel Hood', 'quantity': 1, 'type': 'clothing', 'subtype': 'hood', 'equipped': True, 'slot': 'hood'},
                {'id': 'helmet', 'name': 'Iron Helmet', 'quantity': 1, 'type': 'armor', 'subtype': 'helmet', 'equipped': True, 'slot': 'helmet'},
            ],
            [],
        )
        session = db.session.get(Session, ids['session_id'])
        assert session is not None
        session.state_snapshot = safe_json_dumps(
            {
                'currentScene': {'name': 'Old Ruins'},
                'playerCharacters': [
                    {
                        'id': f"player_{ids['player_id']}",
                        'playerId': ids['player_id'],
                        'name': player.character_name,
                        'inventory': {
                            'items': [
                                {'id': 'long', 'name': 'Longsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'sword', 'equipped': True, 'slot': 'main_hand'},
                            ],
                            'currency': {},
                        },
                        'metadata': {},
                    }
                ],
                'stateChangeLedger': [],
            },
            {},
        )
        db.session.commit()

    response = client.patch(
        f"/api/players/{ids['player_id']}/inventory/equipment",
        json={'action': 'equip', 'item_id': 'great', 'session_id': ids['session_id']},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['snapshot_changed'] is True
    assert payload['equipment_update']['session_id'] == ids['session_id']
    assert payload['equipment_update']['snapshot_changed'] is True
    inventory = {item['id']: item for item in payload['inventory']}
    assert inventory['great']['equipped'] is True
    assert inventory['great']['slot'] == 'two_hands'
    assert inventory['long'].get('equipped', False) is False
    assert inventory['dagger'].get('equipped', False) is False
    assert inventory['hood']['equipped'] is True
    assert inventory['helmet']['equipped'] is True

    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        snapshot = safe_json_loads(session.state_snapshot, {})
        actor = next(actor for actor in snapshot['playerCharacters'] if actor['playerId'] == ids['player_id'])
        snapshot_inventory = {item['id']: item for item in actor['inventory']['items']}
        assert snapshot_inventory['great']['equipped'] is True
        assert snapshot_inventory['great']['slot'] == 'two_hands'
        assert snapshot_inventory['long'].get('equipped', False) is False
        assert snapshot_inventory['dagger'].get('equipped', False) is False

    unequip_response = client.patch(
        f"/api/players/{ids['player_id']}/inventory/equipment",
        json={'action': 'unequip', 'item_id': 'great', 'session_id': ids['session_id']},
    )
    assert unequip_response.status_code == 200
    assert unequip_response.get_json()['snapshot_changed'] is True
    assert {item['id']: item for item in unequip_response.get_json()['inventory']}['great'].get('equipped', False) is False
    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        snapshot = safe_json_loads(session.state_snapshot, {})
        actor = next(actor for actor in snapshot['playerCharacters'] if actor['playerId'] == ids['player_id'])
        snapshot_inventory = {item['id']: item for item in actor['inventory']['items']}
        assert snapshot_inventory['great'].get('equipped', False) is False


def test_manual_equipment_endpoint_infers_sparse_axe_items(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps(
            [
                {'id': 'greataxe', 'name': 'Greataxe', 'quantity': 1},
                {'id': 'handaxe', 'name': 'Handaxe', 'quantity': 1, 'type': 'misc'},
            ],
            [],
        )
        db.session.commit()

    greataxe_response = client.patch(
        f"/api/players/{ids['player_id']}/inventory/equipment",
        json={'action': 'equip', 'item_id': 'greataxe'},
    )

    assert greataxe_response.status_code == 200
    inventory = {item['id']: item for item in greataxe_response.get_json()['inventory']}
    assert inventory['greataxe']['type'] == 'weapon'
    assert inventory['greataxe']['subtype'] == 'greataxe'
    assert inventory['greataxe']['equipped'] is True
    assert inventory['greataxe']['slot'] == 'two_hands'

    handaxe_response = client.patch(
        f"/api/players/{ids['player_id']}/inventory/equipment",
        json={'action': 'equip', 'item_id': 'handaxe'},
    )

    assert handaxe_response.status_code == 200
    inventory = {item['id']: item for item in handaxe_response.get_json()['inventory']}
    assert inventory['greataxe'].get('equipped', False) is False
    assert inventory['handaxe']['type'] == 'weapon'
    assert inventory['handaxe']['subtype'] == 'handaxe'
    assert inventory['handaxe']['equipped'] is True
    assert inventory['handaxe']['slot'] == 'main_hand'


def test_manual_equipment_endpoint_rejects_session_from_another_campaign(client, app):
    ids = seed_world_campaign_player_session(app)
    other_ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert player is not None
        player.inventory = safe_json_dumps(
            [{'id': 'long', 'name': 'Longsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'sword'}],
            [],
        )
        db.session.commit()

    response = client.patch(
        f"/api/players/{ids['player_id']}/inventory/equipment",
        json={'action': 'equip', 'item_id': 'long', 'session_id': other_ids['session_id']},
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['error_code'] == 'validation_error'
    assert 'player campaign' in payload['error']


def test_manual_equipment_endpoint_rolls_back_on_apply_error(client, app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps(
            [{'id': 'long', 'name': 'Longsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'sword'}],
            [],
        )
        db.session.commit()
        original_inventory = player.inventory

    from aidm_server.blueprints import players as players_module

    def fail_apply_state_changes(*_args, **_kwargs):
        raise RuntimeError('simulated equipment failure')

    monkeypatch.setattr(players_module, 'apply_state_changes', fail_apply_state_changes)

    response = client.patch(
        f"/api/players/{ids['player_id']}/inventory/equipment",
        json={'action': 'equip', 'item_id': 'long'},
    )

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'equipment_update_failed'
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert player.inventory == original_inventory


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
