from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

from aidm_server.action_intent import validate_action_intent
from aidm_server.canon_inventory import inventory_change_from_intent_outcome
from aidm_server.commerce import payment_deltas
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.extraction.pre_dm_action_extractor import _extract_from_action_intent, extract_pre_dm_actions
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.llm_context import _compact_live_world_state
from aidm_server.models import DmTurn
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from aidm_server.turn_engine import TurnEngine


STOCK = [
    {'stockId': 'mara_0', 'name': 'Potion of Healing', 'type': 'potion', 'effectId': 'potion_healing', 'price': {'gp': 50}, 'quantity': 2},
    {'stockId': 'mara_1', 'catalog': 'rope', 'quantity': None},
    {'stockId': 'mara_2', 'name': 'Lantern', 'type': 'gear', 'price': {'gp': 5, 'sp': 5}, 'quantity': 1},
]


def _state(*, currency: dict | None = None, scene: dict | None = None) -> dict:
    return {
        'currentScene': {
            'locationId': 'market',
            'name': 'Market',
            'combatState': 'none',
            'activeNpcIds': ['mara'],
            **(scene or {}),
        },
        'merchants': {
            'mara_shop': {
                'id': 'mara_shop',
                'name': "Mara's Sundries",
                'npcId': 'mara',
                'locationId': 'market',
                'buyRate': 0.5,
                'restock': 'long_rest',
                'stock': deepcopy(STOCK),
                'initialStock': deepcopy(STOCK),
            },
            'smithy': {
                'id': 'smithy',
                'name': 'Smithy',
                'locationId': 'forge',
                'buyRate': 0.5,
                'restock': 'never',
                'stock': [{'stockId': 'smithy_0', 'catalog': 'longsword', 'quantity': 1}],
                'initialStock': [{'stockId': 'smithy_0', 'catalog': 'longsword', 'quantity': 1}],
            },
        },
        'activePlayerIds': [1],
        'playerCharacters': [
            {
                'id': 'player_1',
                'playerId': 1,
                'name': 'Aria',
                'class': 'Fighter',
                'level': 1,
                'health': {'currentHp': 10, 'maxHp': 10, 'tempHp': 0, 'conditions': []},
                'inventory': {
                    'items': [
                        {'id': 'sword_spare', 'name': 'Longsword', 'quantity': 1, 'type': 'weapon'},
                        {'id': 'sword_main', 'name': 'Longsword', 'quantity': 1, 'type': 'weapon', 'equipped': True, 'slot': 'main_hand'},
                        {'id': 'idol', 'name': 'Strange Idol', 'quantity': 1, 'type': 'misc'},
                    ],
                    'currency': currency if currency is not None else {'pp': 1, 'gp': 3, 'sp': 4},
                },
                'xp': {'current': 0},
            }
        ],
        'flags': {},
        'stateChangeLedger': [],
    }


def _trade(state: dict, declared_action: dict, *, turn_id: int) -> tuple[dict, dict, dict]:
    declared = validate_declared_actions(
        state=state,
        declared_actions=[{'id': 'act_001', 'actorId': 'player_1', 'sourceText': 'Trade.', **declared_action}],
        current_turn=turn_id,
        expected_actor_id='player_1',
    )
    validation = validate_state_changes(state=state, changes=declared['immediateChanges'], expected_actor_id='player_1')
    applied = apply_state_changes(state, validated_changes_for_application(validation))
    return declared, validation, applied


def _buy(state: dict, stock_id: str, *, quantity: int = 1, turn_id: int = 3) -> tuple[dict, dict, dict]:
    return _trade(
        state,
        {'type': 'shop.buy', 'merchantId': 'mara_shop', 'stockId': stock_id, 'quantity': quantity},
        turn_id=turn_id,
    )


def _purse(state: dict) -> dict:
    return state['playerCharacters'][0]['inventory']['currency']


def _items(state: dict) -> dict[str, dict]:
    return {item['id']: item for item in state['playerCharacters'][0]['inventory']['items']}


def _stock(state: dict, merchant_id: str = 'mara_shop') -> dict[str, dict]:
    return {entry['stockId']: entry for entry in state['merchants'][merchant_id]['stock']}


def test_typed_shop_intents_map_to_trades() -> None:
    intent, error = validate_action_intent(
        {'kind': 'shop', 'shop': {'action': 'buy', 'merchant_id': 'mara_shop', 'stock_id': 'mara_0', 'quantity': 2}}
    )
    assert error is None
    declared = _extract_from_action_intent(intent, actor_id='player_1', player_message='I buy two potions.')
    assert declared['declaredActions'][0] | {} == {
        'id': 'act_001',
        'type': 'shop.buy',
        'actorId': 'player_1',
        'confidence': 1.0,
        'sourceText': 'I buy two potions.',
        'requiresDMResolution': False,
        'merchantId': 'mara_shop',
        'stockId': 'mara_0',
        'quantity': 2,
    }
    _intent, error = validate_action_intent({'kind': 'shop', 'shop': {'action': 'sell', 'merchant_id': 'mara_shop'}})
    assert error == 'shop.item_id is required and may contain only supported ID characters.'


def test_buying_pays_across_denominations_and_gives_change() -> None:
    state = _state()
    declared, validation, applied = _buy(state, 'mara_2')
    transaction = declared['immediateChanges'][0]['transaction']
    assert transaction['totalCopper'] == 550
    assert transaction['currencyDeltas'] == {'pp': -1, 'gp': 4, 'sp': 5}
    assert validation['rejected'] == []
    assert _purse(applied['nextState']) == {'pp': 0, 'gp': 7, 'sp': 9}
    assert _stock(applied['nextState'])['mara_2']['quantity'] == 0
    lantern = next(item for item in _items(applied['nextState']).values() if item['name'] == 'Lantern')
    assert lantern['quantity'] == 1

    replay = apply_state_changes(applied['nextState'], validated_changes_for_application(validation))
    assert replay['appliedChanges'] == []
    assert _purse(replay['nextState']) == {'pp': 0, 'gp': 7, 'sp': 9}

    declared, _validation, _applied = _buy(applied['nextState'], 'mara_2', turn_id=4)
    assert declared['validatedActions'][0]['reason'] == "Mara's Sundries is out of Lantern."


def test_payment_breaks_a_large_coin_only_when_needed() -> None:
    assert payment_deltas({'gp': 10, 'cp': 500}, 100) == {'gp': -1}
    assert payment_deltas({'gp': 1, 'sp': 5}, 120) == {'gp': -1, 'sp': -2}
    assert payment_deltas({'pp': 1}, 50) == {'pp': -1, 'gp': 9, 'sp': 5}
    assert payment_deltas({'sp': 3}, 50) is None


def test_purchased_item_keeps_its_effect_and_stacks() -> None:
    state = _state(currency={'pp': 20})
    _declared, _validation, applied = _buy(state, 'mara_0', quantity=2)
    potion = next(item for item in _items(applied['nextState']).values() if item['name'] == 'Potion of Healing')
    assert potion['metadata'] == {'effectId': 'potion_healing'}
    assert potion['quantity'] == 2
    assert _purse(applied['nextState']) == {'pp': 10}

    declared, _validation, _applied = _buy(applied['nextState'], 'mara_0', turn_id=4)
    assert declared['validatedActions'][0]['reason'] == "Mara's Sundries is out of Potion of Healing."
    declared, _validation, rope = _buy(applied['nextState'], 'mara_1', quantity=3, turn_id=5)
    assert declared['immediateChanges'][0]['transaction']['unitPrice'] == {'gp': 1}
    assert _stock(rope['nextState'])['mara_1'].get('quantity') is None


def test_insufficient_funds_and_short_stock_are_rejected() -> None:
    declared, _validation, applied = _buy(_state(), 'mara_0')
    assert declared['validatedActions'][0]['reason'] == (
        'Aria cannot afford 1 Potion of Healing for 50 gp; they have 13 gp, 4 sp.'
    )
    assert _purse(applied['nextState']) == {'pp': 1, 'gp': 3, 'sp': 4}
    declared, _validation, _applied = _buy(_state(currency={'pp': 20}), 'mara_0', quantity=3)
    assert declared['validatedActions'][0]['reason'] == "Mara's Sundries has only 2 Potion of Healing left."


def test_closed_merchants_do_not_trade() -> None:
    declared, _validation, _applied = _trade(
        _state(),
        {'type': 'shop.buy', 'merchantId': 'smithy', 'stockId': 'smithy_0', 'quantity': 1},
        turn_id=3,
    )
    assert declared['validatedActions'][0]['reason'] == 'Smithy is not open for trade right now.'
    away = _state(scene={'activeNpcIds': []})
    assert _buy(away, 'mara_2')[0]['validatedActions'][0]['reason'] == "Mara's Sundries is not open for trade right now."
    fighting = _state(scene={'combatState': 'active'})
    fighting['combat'] = {'status': 'active', 'participants': [{'id': 'player_1', 'name': 'Aria', 'team': 'player'}]}
    assert _buy(fighting, 'mara_2')[0]['validatedActions'][0]['reason'] == "Mara's Sundries is not open for trade right now."


def test_selling_pays_the_buy_rate_rounded_down_to_copper() -> None:
    state = _state(currency={})
    state['merchants']['mara_shop']['buyRate'] = 0.33
    declared, validation, applied = _trade(
        state,
        {'type': 'shop.sell', 'merchantId': 'mara_shop', 'itemId': 'sword_spare', 'quantity': 1},
        turn_id=3,
    )
    assert declared['immediateChanges'][0]['transaction']['totalCopper'] == 495
    assert validation['rejected'] == []
    assert _purse(applied['nextState']) == {'gp': 4, 'sp': 9, 'cp': 5}
    assert 'sword_spare' not in _items(applied['nextState'])

    equipped = _trade(state, {'type': 'shop.sell', 'merchantId': 'mara_shop', 'itemId': 'sword_main', 'quantity': 1}, turn_id=4)
    assert equipped[0]['validatedActions'][0]['reason'] == 'Unequip Longsword before selling it.'
    unpriced = _trade(state, {'type': 'shop.sell', 'merchantId': 'mara_shop', 'itemId': 'idol', 'quantity': 1}, turn_id=5)
    assert unpriced[0]['validatedActions'][0]['reason'] == "Mara's Sundries has no use for that."


def test_long_rest_restocks_merchants_that_restock() -> None:
    state = _state(currency={'pp': 20})
    _declared, _validation, bought = _buy(state, 'mara_2')
    bought['nextState']['merchants']['smithy']['stock'][0]['quantity'] = 0
    rested = validate_declared_actions(
        state=bought['nextState'],
        declared_actions=[{'id': 'act_rest', 'type': 'rest.complete', 'actorId': 'player_1', 'sourceText': 'Sleep.', 'restType': 'long_rest'}],
        current_turn=4,
        expected_actor_id='player_1',
    )
    validation = validate_state_changes(state=bought['nextState'], changes=rested['immediateChanges'])
    applied = apply_state_changes(bought['nextState'], validated_changes_for_application(validation))
    assert _stock(applied['nextState'])['mara_2']['quantity'] == 1
    assert _stock(applied['nextState'], 'smithy')['smithy_0']['quantity'] == 0
    assert applied['appliedChanges'][0]['restockedMerchantIds'] == ['mara_shop']


def test_players_and_the_dm_see_only_open_merchants() -> None:
    state = _state()
    projected = filter_session_snapshot_for_player(state, private_player_ids={1})
    assert 'merchants' not in projected
    assert projected['currentScene']['merchants'] == [
        {
            'id': 'mara_shop',
            'name': "Mara's Sundries",
            'npcId': 'mara',
            'buyRate': 0.5,
            'stock': [
                {'stockId': 'mara_0', 'name': 'Potion of Healing', 'type': 'potion', 'price': {'gp': 50}, 'quantity': 2},
                {'stockId': 'mara_1', 'name': 'Rope', 'type': 'gear', 'price': {'gp': 1}, 'quantity': None},
                {'stockId': 'mara_2', 'name': 'Lantern', 'type': 'gear', 'price': {'gp': 5, 'sp': 5}, 'quantity': 1},
            ],
        }
    ]
    context = _compact_live_world_state(state)['merchants']
    assert [merchant['id'] for merchant in context] == ['mara_shop']
    assert context[0]['stock'][1] == {'name': 'Rope', 'price': '1 gp', 'quantity': 'unlimited'}
    assert filter_session_snapshot_for_player(_state(scene={'activeNpcIds': []}), private_player_ids={1})['currentScene']['merchants'] == []


def test_legacy_buy_uses_the_server_price_and_ignores_cost_gold() -> None:
    intent, error = validate_action_intent(
        {'kind': 'item', 'inventory_action': 'buy', 'item': {'name': 'lantern', 'quantity': 1}, 'cost_gold': 1}
    )
    assert error is None
    assert 'cost_gold' not in intent
    declared_actions = extract_pre_dm_actions(
        current_state=_state(), player_message='I buy a lantern for 1 gold.', recent_timeline=[],
        actor_id='player_1', action_intent=intent,
    )['declaredActions']
    assert declared_actions[0]['type'] == 'shop.buy'

    state = _state()
    declared = validate_declared_actions(state=state, declared_actions=declared_actions, current_turn=3, expected_actor_id='player_1')
    change = declared['immediateChanges'][0]
    assert (change['merchantId'], change['stockId'], change['transaction']['totalCopper']) == ('mara_shop', 'mara_2', 550)
    applied = apply_state_changes(state, validated_changes_for_application(validate_state_changes(state=state, changes=[change])))
    assert _purse(applied['nextState']) == {'pp': 0, 'gp': 7, 'sp': 9}

    elsewhere = _state(scene={'activeNpcIds': []})
    unrouted = validate_declared_actions(state=elsewhere, declared_actions=declared_actions, current_turn=4, expected_actor_id='player_1')
    assert unrouted['validatedActions'][0]['status'] == 'pending'
    assert unrouted['immediateChanges'] == []


def test_typed_shop_actions_survive_extraction_and_use_server_stock() -> None:
    state = _state()
    for action, reference in [('buy', {'stock_id': 'mara_2'}), ('sell', {'item_id': 'sword_spare'})]:
        intent, error = validate_action_intent(
            {'kind': 'shop', 'shop': {'action': action, 'merchant_id': 'mara_shop', **reference, 'quantity': 1}}
        )
        assert error is None
        extraction = extract_pre_dm_actions(
            current_state=state, player_message='Trade.', recent_timeline=[], actor_id='player_1', action_intent=intent
        )
        raw = extraction['declaredActions'][0]
        assert raw['merchantId'] == 'mara_shop'
        assert raw['stockId' if action == 'buy' else 'itemId'] == next(iter(reference.values()))
        declared = validate_declared_actions(
            state=state, declared_actions=extraction['declaredActions'], current_turn=3, expected_actor_id='player_1'
        )
        assert declared['validatedActions'][0]['status'] == 'valid'
        assert declared['immediateChanges'][0]['transaction']['totalCopper'] == (550 if action == 'buy' else 750)


def test_rejected_shop_trades_cannot_be_narrated_as_success() -> None:
    result = {'preValidation': {'validatedActions': [{
        'status': 'invalid', 'reason': 'Out of stock.', 'originalAction': {'type': 'shop.buy'}
    }]}}
    for intent in ({'kind': 'shop'}, {'kind': 'item', 'inventory_action': 'buy'}):
        rejection = TurnEngine._typed_action_rejection(SimpleNamespace(action_intent=intent), result)
        assert rejection['reason'] == 'Out of stock.'
    result['preValidation']['validatedActions'][0]['status'] = 'pending'
    assert TurnEngine._typed_action_rejection(SimpleNamespace(action_intent={'kind': 'item'}), result) is None


def test_legacy_sell_routes_to_the_open_merchant() -> None:
    intent, _error = validate_action_intent({'kind': 'item', 'inventory_action': 'sell', 'item': {'id': 'sword_spare', 'name': 'Longsword'}})
    declared_actions = _extract_from_action_intent(intent, actor_id='player_1', player_message='I sell my spare longsword.')['declaredActions']
    declared = validate_declared_actions(state=_state(currency={}), declared_actions=declared_actions, current_turn=3, expected_actor_id='player_1')
    assert declared['immediateChanges'][0]['transaction']['currencyDeltas'] == {'gp': 7, 'sp': 5}


def test_narration_does_not_repeat_a_server_settled_trade() -> None:
    intent = {'kind': 'item', 'inventory_action': 'buy', 'item': {'name': 'lantern', 'quantity': 1}}

    def turn(status: str) -> DmTurn:
        validated = [{'status': status, 'originalAction': {'type': 'shop.buy'}}]
        return DmTurn(
            metadata_json=json.dumps(
                {'action_intent': intent, STATE_PIPELINE_METADATA_KEY: {'preDmValidation': {'validatedActions': validated}}}
            )
        )

    narration = 'Mara hands you the lantern and you buy it gladly.'
    assert inventory_change_from_intent_outcome(turn('valid'), narration) is None
    assert inventory_change_from_intent_outcome(turn('invalid'), narration) is None
    assert inventory_change_from_intent_outcome(turn('pending'), narration)['action'] == 'acquire'
    potion_turn = DmTurn(
        metadata_json=json.dumps({'action_intent': {'kind': 'item', 'inventory_action': 'use', 'item': {'name': 'Potion of Healing'}}})
    )
    assert inventory_change_from_intent_outcome(potion_turn, 'You drink the Potion of Healing.') is None


def test_a_purchase_keeps_the_item_id_its_stock_entry_names() -> None:
    state = _state(currency={'gp': 20})
    hammer = {'stockId': 'mara_3', 'name': "Mason's Hammer", 'type': 'tool', 'price': {'gp': 8}, 'itemId': 'masons_hammer'}
    state['merchants']['mara_shop']['stock'].append(deepcopy(hammer))

    _declared, _validation, bought = _buy(state, 'mara_3')
    _declared, _validation, again = _buy(bought['nextState'], 'mara_3', turn_id=4)

    assert _items(bought['nextState'])['masons_hammer']['name'] == "Mason's Hammer"
    assert _items(again['nextState'])['masons_hammer']['quantity'] == 2
    assert _purse(again['nextState'])['gp'] == 4
