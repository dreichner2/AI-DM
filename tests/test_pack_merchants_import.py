from __future__ import annotations

from aidm_server.database import db
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.models import Session, safe_json_loads
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest


def _pack(**overrides) -> dict:
    pack = {
        'packId': 'market_pack',
        'title': 'Market Pack',
        'startingState': {'locationId': 'square', 'knownLocationIds': ['square', 'wilds']},
        'locations': [
            {'id': 'square', 'name': 'Market Square', 'restPolicy': 'safe', 'connectedLocationIds': ['wilds']},
            {'id': 'wilds', 'name': 'The Wilds', 'restPolicy': 'none', 'connectedLocationIds': ['square']},
        ],
        'npcs': [{'id': 'npc_hilde', 'name': 'Hilde', 'locationId': 'square', 'visibleAtStart': True}],
        'merchants': [
            {
                'id': 'hilde_stall',
                'name': "Hilde's Stall",
                'npcId': 'npc_hilde',
                'buyRate': 0.4,
                'restock': 'never',
                'stock': [
                    {
                        'name': 'Potion of Healing',
                        'type': 'consumable',
                        'effectId': 'potion_healing',
                        'price': {'gp': 50},
                        'quantity': 3,
                    },
                    {'name': 'Rope', 'price': {'sp': 5, 'cp': 2}, 'metadata': {'lengthFeet': 50}},
                    {'catalog': 'torch', 'quantity': 10},
                ],
            },
            {
                'id': 'square_board',
                'name': 'Notice Board Trader',
                'locationId': 'square',
                'stock': [{'catalog': 'rations'}],
            },
        ],
    }
    pack.update(overrides)
    return pack


def _import(client, app, pack: dict) -> dict:
    response = client.post('/api/campaigns/import-pack', json={'pack': pack})
    assert response.status_code == 201, response.get_json()
    with app.app_context():
        return safe_json_loads(db.session.get(Session, response.get_json()['session_id']).state_snapshot, {})


def test_merchants_import_in_the_runtime_trade_shape(client, app):
    snapshot = _import(client, app, _pack())

    stall = snapshot['merchants']['hilde_stall']
    expected_stock = [
        {
            'stockId': 'hilde_stall_0',
            'name': 'Potion of Healing',
            'type': 'consumable',
            'price': {'gp': 50},
            'quantity': 3,
            'effectId': 'potion_healing',
        },
        {
            'stockId': 'hilde_stall_1',
            'name': 'Rope',
            'type': 'gear',
            'price': {'sp': 5, 'cp': 2},
            'quantity': None,
            'metadata': {'lengthFeet': 50},
        },
        {'stockId': 'hilde_stall_2', 'catalog': 'torch', 'quantity': 10},
    ]
    assert stall == {
        'id': 'hilde_stall',
        'name': "Hilde's Stall",
        'npcId': 'npc_hilde',
        'locationId': None,
        'buyRate': 0.4,
        'restock': 'never',
        'stock': expected_stock,
        'initialStock': expected_stock,
    }
    stall['stock'][0]['quantity'] = 1
    assert stall['initialStock'][0]['quantity'] == 3

    board = snapshot['merchants']['square_board']
    assert (board['npcId'], board['locationId'], board['buyRate'], board['restock']) == (None, 'square', 0.5, 'long_rest')
    assert board['stock'] == [{'stockId': 'square_board_0', 'catalog': 'rations', 'quantity': None}]
    assert [record['id'] for record in snapshot['campaignPack']['catalog']['merchants']] == ['hilde_stall', 'square_board']


def test_pack_without_merchants_has_an_empty_merchant_map(client, app):
    pack = _pack()
    del pack['merchants']

    assert _import(client, app, pack)['merchants'] == {}


def test_linter_requires_a_place_known_references_and_positive_prices():
    pack = _pack(
        merchants=[
            {'id': 'drifter', 'name': 'Drifter', 'stock': [{'name': 'Charm', 'price': {'gp': 1}}]},
            {'id': 'ghost', 'name': 'Ghost Trader', 'npcId': 'npc_nobody', 'locationId': 'nowhere', 'stock': []},
            {
                'id': 'cheap',
                'name': 'Cheap Trader',
                'locationId': 'square',
                'stock': [{'name': 'Free Thing', 'price': {}}, {'name': 'Odd Thing', 'price': {'gp': 1, 'sp': 1.5}}],
            },
        ]
    )

    issues = lint_campaign_pack_manifest(pack)['issues']
    errors = {(issue['code'], issue['path']) for issue in issues if issue['severity'] == 'error'}

    assert ('merchant_without_place', 'merchants[0]') in errors
    assert ('unknown_reference', 'merchants[1].npcId') in errors
    assert ('unknown_reference', 'merchants[1].locationId') in errors
    assert ('invalid_merchant_price', 'merchants[2].stock[0].price') in errors
    assert ('invalid_merchant_price', 'merchants[2].stock[1].price') in errors
    assert lint_campaign_pack_manifest(_pack())['ok'] is True


def test_import_rejects_a_non_positive_price(client):
    pack = _pack()
    pack['merchants'][0]['stock'][0]['price'] = {'gp': 0}

    response = client.post('/api/campaigns/import-pack', json={'pack': pack})

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'invalid_campaign_pack_schema'


def test_rest_policy_is_copied_to_the_scene_at_import_and_on_arrival(client, app):
    snapshot = _import(client, app, _pack())
    assert snapshot['currentScene']['restPolicy'] == 'safe'

    moved = apply_state_changes(
        snapshot,
        [{'id': 'move_wilds', 'type': 'scene.move_location', 'locationId': 'wilds', 'name': 'The Wilds'}],
    )['nextState']
    assert moved['currentScene']['restPolicy'] == 'none'

    back = apply_state_changes(
        moved,
        [{'id': 'move_square', 'type': 'scene.move_location', 'locationId': 'square', 'name': 'Market Square'}],
    )['nextState']
    assert back['currentScene']['restPolicy'] == 'safe'


def test_a_stock_entry_keeps_the_item_id_it_names(client, app):
    pack = _pack()
    pack['merchants'][0]['stock'].append({'name': "Mason's Hammer", 'type': 'tool', 'price': {'gp': 8}, 'itemId': 'masons_hammer'})
    pack['merchants'][1]['stock'][0]['itemId'] = 'board_rations'

    snapshot = _import(client, app, pack)

    assert snapshot['merchants']['hilde_stall']['stock'][3] == {
        'stockId': 'hilde_stall_3',
        'name': "Mason's Hammer",
        'type': 'tool',
        'price': {'gp': 8},
        'quantity': None,
        'itemId': 'masons_hammer',
    }
    assert snapshot['merchants']['square_board']['stock'] == [
        {'stockId': 'square_board_0', 'catalog': 'rations', 'quantity': None, 'itemId': 'board_rations'}
    ]
