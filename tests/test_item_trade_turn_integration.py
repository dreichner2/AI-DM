from __future__ import annotations

from aidm_server.database import db
from aidm_server.game_state.orchestration import turn_pipeline
from aidm_server.models import Campaign, DmTurn, Player, Session, safe_json_dumps, safe_json_loads
from tests.helpers import seed_world_campaign_player_session


def _run_turn(app, ids, intent, message):
    campaign = db.session.get(Campaign, ids['campaign_id'])
    session = db.session.get(Session, ids['session_id'])
    player = db.session.get(Player, ids['player_id'])
    turn = DmTurn(
        session_id=session.session_id, campaign_id=campaign.campaign_id, player_id=player.player_id,
        player_input=message,
    )
    db.session.add(turn)
    db.session.flush()
    return turn_pipeline.pre_dm_pipeline(
        turn=turn, session_obj=session, campaign=campaign, player=player,
        player_message=message, action_intent=intent,
    )


def test_item_effect_is_persisted_before_narration(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr(turn_pipeline, 'recent_timeline_for_session', lambda *_args, **_kwargs: [])
    monkeypatch.setattr('secrets.randbelow', lambda _sides: 1)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.stats = safe_json_dumps({'current_hp': 5, 'max_hp': 20}, {})
        player.inventory = safe_json_dumps([{
            'id': 'potion_1', 'name': 'Potion of Healing', 'quantity': 2, 'type': 'potion',
        }], [])
        db.session.flush()
        result = _run_turn(app, ids, {'kind': 'item', 'inventory_action': 'use', 'item': {'id': 'potion_1'}}, 'I drink a potion.')
        applied = result['immediateAppliedChanges']
        assert [change['type'] for change in applied] == ['item.use']
        assert applied[0]['actualAmount'] == 6
        actor = result['stateBeforeDm']['playerCharacters'][0]
        assert actor['health']['currentHp'] == 11
        assert actor['inventory']['items'][0]['quantity'] == 1
        assert safe_json_loads(player.stats, {})['current_hp'] == 11
        assert safe_json_loads(player.inventory, [])[0]['quantity'] == 1
        assert 'potion' in str(result['dmContextPacket']).lower()


def test_shop_purchase_is_persisted_at_server_price_before_narration(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr(turn_pipeline, 'recent_timeline_for_session', lambda *_args, **_kwargs: [])
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.stats = safe_json_dumps({'current_hp': 10, 'max_hp': 10, 'gold': 12}, {})
        player.inventory = safe_json_dumps([], [])
        session = db.session.get(Session, ids['session_id'])
        session.state_snapshot = safe_json_dumps({
            'currentScene': {'name': 'Market', 'locationId': 'market', 'activeNpcIds': ['mara'], 'combatState': 'none'},
            'merchants': {'mara_shop': {
                'id': 'mara_shop', 'name': 'Mara', 'npcId': 'mara', 'locationId': 'market',
                'stock': [{'stockId': 'rope', 'name': 'Rope', 'type': 'gear', 'price': {'gp': 5}, 'quantity': 2}],
            }},
        }, {})
        db.session.flush()
        result = _run_turn(app, ids, {
            'kind': 'shop', 'shop': {'action': 'buy', 'merchant_id': 'mara_shop', 'stock_id': 'rope', 'quantity': 1},
        }, 'I buy the rope.')
        applied = result['immediateAppliedChanges']
        assert [change['type'] for change in applied] == ['shop.buy']
        assert applied[0]['transaction']['totalCopper'] == 500
        actor = result['stateBeforeDm']['playerCharacters'][0]
        assert actor['inventory']['currency']['gp'] == 7
        assert any(item['name'] == 'Rope' for item in actor['inventory']['items'])
        assert result['stateBeforeDm']['merchants']['mara_shop']['stock'][0]['quantity'] == 1
        assert safe_json_loads(player.stats, {})['gold'] == 7
