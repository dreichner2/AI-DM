from __future__ import annotations

from aidm_server.contracts import ProviderResponse
from aidm_server.database import db
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY, STATE_PIPELINE_VERSION
from aidm_server.game_state.application.applier import apply_state_changes
import aidm_server.game_state.extraction.post_dm_outcome_extractor as post_extractor_module
import aidm_server.game_state.extraction.pre_dm_action_extractor as pre_extractor_module
import aidm_server.game_state.orchestration.turn_pipeline as turn_pipeline_module
from aidm_server.game_state.extraction.post_dm_outcome_extractor import extract_post_dm_outcomes
from aidm_server.game_state.extraction.pre_dm_action_extractor import extract_pre_dm_actions
from aidm_server.game_state.extraction.schemas import normalize_post_extraction
from aidm_server.game_state.logging.state_log_builder import build_state_log
from aidm_server.game_state.orchestration.turn_pipeline import post_dm_pipeline
from aidm_server.game_state.validation.inventory_validator import resolve_inventory_item_reference
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.emergent_memory import apply_canon_patch
from aidm_server.models import Campaign, DmTurn, Player, Session, SessionLogEntry, TurnEvent, safe_json_dumps, safe_json_loads
from tests.helpers import seed_world_campaign_player_session


def _state(*, items=None, currency=None, hp_current=10, hp_max=20, temp_hp=0, xp_current=0):
    return {
        'sessionId': 1,
        'campaignId': 1,
        'playerCharacters': [
            {
                'id': 'player_1',
                'playerId': 1,
                'name': 'Kael',
                'health': {'currentHp': hp_current, 'maxHp': hp_max, 'tempHp': temp_hp, 'conditions': []},
                'inventory': {
                    'items': items or [],
                    'currency': currency or {'pp': 0, 'gp': 0, 'ep': 0, 'sp': 0, 'cp': 0},
                },
                'xp': {'current': xp_current, 'nextLevelAt': 300},
                'metadata': {},
            }
        ],
        'stateChangeLedger': [],
    }


def _item(name, *, item_id=None, quantity=1, item_type='misc', subtype=None, equipped=False, last_used=None, favorite=False):
    return {
        'id': item_id or f'itm_{name.lower().replace(" ", "_")}',
        'name': name,
        'quantity': quantity,
        'type': item_type,
        'subtype': subtype,
        'equipped': equipped,
        'aliases': [subtype] if subtype else [],
        'tags': [subtype] if subtype else [],
        'lastUsedAtTurn': last_used,
        'favorite': favorite,
    }


def _two_player_state():
    state = _state(
        items=[_item('Rope', item_id='rope_1', quantity=1)],
        currency={'pp': 0, 'gp': 5, 'ep': 0, 'sp': 0, 'cp': 12},
    )
    state['playerCharacters'].append(
        {
            'id': 'player_2',
            'playerId': 2,
            'name': 'Borin',
            'health': {'currentHp': 12, 'maxHp': 12, 'tempHp': 0, 'conditions': []},
            'inventory': {
                'items': [],
                'currency': {'pp': 0, 'gp': 1, 'ep': 0, 'sp': 0, 'cp': 0},
            },
            'xp': {'current': 0, 'nextLevelAt': 300},
            'metadata': {},
        }
    )
    return state


def test_extract_consume_item_from_player_message(app):
    with app.app_context():
        result = extract_pre_dm_actions(
            current_state={},
            player_message='I drink my healing potion.',
            recent_timeline=[],
            actor_id='player_1',
        )

    assert result['declaredActions'][0]['type'] == 'inventory.consume'
    assert result['declaredActions'][0]['itemName'] == 'healing potion'


def test_extract_pickup_item_from_player_message(app):
    with app.app_context():
        result = extract_pre_dm_actions(
            current_state={},
            player_message='I pick a stick up.',
            recent_timeline=[],
            actor_id='player_1',
        )

    assert result['declaredActions'][0]['type'] == 'generic.intent'
    assert result['declaredActions'][0]['summary'] == 'Player attempts to pick up stick.'


def test_pre_dm_helper_debug_captures_raw_response(app, monkeypatch):
    helper_text = (
        '{"declaredActions":[{"id":"act_001","type":"generic.intent","actorId":"player_1",'
        '"confidence":0.91,"sourceText":"I pick up the stick","requiresDMResolution":true,'
        '"summary":"Player attempts to pick up the stick."}],"notes":"helper saw pickup intent"}'
    )

    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(text=helper_text, provider='fake', model='fake-pre-helper')

    monkeypatch.setattr(pre_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_pre_dm_actions(
            current_state={},
            player_message='I pick up the stick',
            recent_timeline=[],
            actor_id='player_1',
        )

    assert result['declaredActions'][0]['summary'] == 'Player attempts to pick up the stick.'
    assert result['notes'] == ['helper saw pickup intent']
    assert result['debug']['source'] == 'helper'
    assert result['debug']['helperAttempted'] is True
    assert result['debug']['helperSchemaValid'] is True
    assert result['debug']['helperModel'] == 'fake-pre-helper'
    assert result['debug']['helperRawText'] == helper_text
    assert result['debug']['helperParsed']['declaredActions'][0]['type'] == 'generic.intent'
    assert result['debug']['fallbackRan'] is False


def test_pre_dm_helper_intent_description_becomes_summary(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text=(
                    '{"declaredActions":[{"id":"act_001","type":"generic.intent","actorId":"player_1",'
                    '"confidence":0.9,"sourceText":"I pick this random thing up. Looks like 50 Shades of Grey",'
                    '"requiresDMResolution":true,'
                    '"intentDescription":"Player wants to pick up an object from the floor described as 50 Shades of Grey."}]}'
                ),
                provider='fake',
                model='fake-pre-helper',
            )

    monkeypatch.setattr(pre_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_pre_dm_actions(
            current_state={},
            player_message='I pick this random thing up. Looks like 50 Shades of Grey',
            recent_timeline=[],
            actor_id='player_1',
        )

    assert result['declaredActions'][0]['summary'] == (
        'Player wants to pick up an object from the floor described as 50 Shades of Grey.'
    )


def test_pre_dm_helper_debug_records_fallback_reason(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(text='not json', provider='fake', model='fake-pre-helper')

    monkeypatch.setattr(pre_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_pre_dm_actions(
            current_state={},
            player_message='I pick up the stick',
            recent_timeline=[],
            actor_id='player_1',
        )

    assert result['declaredActions'][0]['summary'] == 'Player attempts to pick up stick.'
    assert result['debug']['source'] == 'heuristic'
    assert result['debug']['helperAttempted'] is True
    assert result['debug']['helperSchemaValid'] is False
    assert result['debug']['helperRawText'] == 'not json'
    assert result['debug']['helperParsed'] is None
    assert result['debug']['fallbackRan'] is True
    assert result['debug']['fallbackReason'] == 'helper_json_invalid'


def test_validate_consume_existing_item():
    state = _state(items=[_item('Minor Healing Potion', item_id='potion_1', item_type='consumable', subtype='potion')])
    action = {
        'id': 'act_001',
        'type': 'inventory.consume',
        'actorId': 'player_1',
        'itemName': 'healing potion',
        'quantity': 1,
        'sourceText': 'I drink my healing potion.',
    }

    result = validate_declared_actions(state=state, declared_actions=[action], current_turn=7)

    validated = result['validatedActions'][0]
    assert validated['status'] == 'valid'
    assert validated['immediateChanges'][0]['type'] == 'inventory.remove'
    assert validated['immediateChanges'][0]['itemId'] == 'potion_1'


def test_reject_consume_missing_item():
    result = validate_declared_actions(
        state=_state(items=[]),
        declared_actions=[
            {
                'id': 'act_001',
                'type': 'inventory.consume',
                'actorId': 'player_1',
                'itemName': 'healing potion',
                'quantity': 1,
                'sourceText': 'I drink my healing potion.',
            }
        ],
        current_turn=7,
    )

    assert result['validatedActions'][0]['status'] == 'invalid'
    assert 'does not have' in result['validatedActions'][0]['reason']


def test_generic_intent_summary_anchors_dm_context():
    result = validate_declared_actions(
        state=_state(),
        declared_actions=[
            {
                'id': 'act_001',
                'type': 'generic.intent',
                'actorId': 'player_1',
                'confidence': 0.9,
                'sourceText': 'I pick this random thing up. Looks like 50 Shades of Grey',
                'requiresDMResolution': True,
                'summary': 'Player wants to pick up an object described as 50 Shades of Grey.',
            }
        ],
        current_turn=12,
    )

    assert 'Player wants to pick up an object described as 50 Shades of Grey.' in result['dmContextSummary']


def test_apply_inventory_remove_quantity_and_delete_zero():
    state = _state(items=[_item('Minor Healing Potion', item_id='potion_1', quantity=1, item_type='consumable')])
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'chg_remove',
                'type': 'inventory.remove',
                'actorId': 'player_1',
                'itemId': 'potion_1',
                'quantity': 1,
                'source': 'pre_dm',
                'reason': 'Potion consumed.',
                'visible': True,
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert result['nextState']['playerCharacters'][0]['inventory']['items'] == []


def test_apply_health_heal_caps_at_max():
    state = _state(hp_current=18, hp_max=20)
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'chg_heal',
                'type': 'health.heal',
                'actorId': 'player_1',
                'amount': 7,
                'source': 'post_dm',
                'reason': 'DM stated healing.',
                'visible': True,
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['modified'][0]['modifiedChange']['amount'] == 2
    assert result['nextState']['playerCharacters'][0]['health']['currentHp'] == 20
    assert result['appliedChanges'][0]['actualAmount'] == 2


def test_apply_health_damage_uses_temp_hp_first():
    state = _state(hp_current=10, hp_max=20, temp_hp=3)
    result = apply_state_changes(
        state,
        [
            {
                'id': 'chg_damage',
                'type': 'health.damage',
                'actorId': 'player_1',
                'amount': 5,
                'source': 'post_dm',
                'reason': 'Damage.',
                'visible': True,
            }
        ],
    )

    health = result['nextState']['playerCharacters'][0]['health']
    assert health['tempHp'] == 0
    assert health['currentHp'] == 8


def test_apply_xp_gain_and_capped_loss():
    state = _state(xp_current=25)
    gain_validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'xp_gain',
                'type': 'xp.add',
                'actorId': 'player_1',
                'amount': 75,
                'source': 'post_dm',
                'reason': 'Quest reward.',
                'visible': True,
            }
        ],
    )
    gained = apply_state_changes(state, validated_changes_for_application(gain_validation))

    assert gain_validation['rejected'] == []
    assert gained['nextState']['playerCharacters'][0]['xp']['current'] == 100

    loss_validation = validate_state_changes(
        state=gained['nextState'],
        changes=[
            {
                'id': 'xp_loss',
                'type': 'xp.remove',
                'actorId': 'player_1',
                'amount': 150,
                'source': 'post_dm',
                'reason': 'XP penalty.',
                'visible': True,
            }
        ],
    )
    result = apply_state_changes(gained['nextState'], validated_changes_for_application(loss_validation))
    state_log = build_state_log(turn_id=1, post_validation=loss_validation)

    assert loss_validation['modified'][0]['modifiedChange']['amount'] == 100
    assert result['nextState']['playerCharacters'][0]['xp']['current'] == 0
    assert state_log['lines'][0]['message'] == 'Removed 100 XP (capped at current XP).'


def test_inventory_transfer_expands_to_atomic_remove_and_add():
    state = _two_player_state()
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'transfer_rope',
                'type': 'inventory.transfer',
                'actorId': 'player_1',
                'fromActorId': 'player_1',
                'toActorId': 'player_2',
                'itemId': 'rope_1',
                'itemName': 'Rope',
                'quantity': 1,
                'visible': True,
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['rejected'] == []
    assert [entry['change']['type'] for entry in validation['accepted']] == ['inventory.remove', 'inventory.add']
    assert result['nextState']['playerCharacters'][0]['inventory']['items'] == []
    target_items = result['nextState']['playerCharacters'][1]['inventory']['items']
    assert target_items[0]['name'] == 'Rope'
    state_log = build_state_log(turn_id=1, post_validation=validation)
    assert len(state_log['lines']) == 1
    assert any('Rope' in line['message'] for line in state_log['lines'])


def test_inventory_transfer_missing_item_rejects_without_partial_add():
    state = _two_player_state()
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'transfer_missing',
                'type': 'inventory.transfer',
                'actorId': 'player_1',
                'fromActorId': 'player_1',
                'toActorId': 'player_2',
                'itemName': 'Lantern',
                'quantity': 1,
                'visible': True,
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['accepted'] == []
    assert validation['rejected'][0]['reason'] == 'Item not found in source inventory.'
    assert result['appliedChanges'] == []
    assert result['nextState']['playerCharacters'][1]['inventory']['items'] == []


def test_currency_transfer_expands_to_atomic_remove_and_add():
    state = _two_player_state()
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'transfer_gold',
                'type': 'currency.transfer',
                'actorId': 'player_1',
                'fromActorId': 'player_1',
                'toActorId': 'player_2',
                'currency': 'gp',
                'amount': 3,
                'visible': True,
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['rejected'] == []
    assert [entry['change']['type'] for entry in validation['accepted']] == ['currency.remove', 'currency.add']
    assert result['nextState']['playerCharacters'][0]['inventory']['currency']['gp'] == 2
    assert result['nextState']['playerCharacters'][1]['inventory']['currency']['gp'] == 4
    state_log = build_state_log(turn_id=1, post_validation=validation)
    assert len(state_log['lines']) == 1
    assert state_log['lines'][0]['message'] == 'Transferred 3 gp from Kael to Borin.'


def test_inventory_transfer_log_uses_structured_transfer_message_for_helper_reason():
    state = _two_player_state()
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'transfer_rope',
                'type': 'inventory.transfer',
                'actorId': 'player_1',
                'fromActorId': 'player_1',
                'toActorId': 'player_2',
                'itemName': 'Rope',
                'quantity': 1,
                'visible': True,
                'reason': 'Extracted from DM response.',
            }
        ],
    )

    state_log = build_state_log(turn_id=1, post_validation=validation)

    assert validation['rejected'] == []
    assert len(state_log['lines']) == 1
    assert state_log['lines'][0]['message'] == 'Transferred Rope x1 from Kael to Borin.'


def test_currency_transfer_insufficient_funds_rejects_without_partial_add():
    state = _two_player_state()
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'transfer_too_much',
                'type': 'currency.transfer',
                'actorId': 'player_1',
                'fromActorId': 'player_1',
                'toActorId': 'player_2',
                'currency': 'gp',
                'amount': 10,
                'visible': True,
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['accepted'] == []
    assert 'Insufficient gp' in validation['rejected'][0]['reason']
    assert result['appliedChanges'] == []
    assert result['nextState']['playerCharacters'][0]['inventory']['currency']['gp'] == 5
    assert result['nextState']['playerCharacters'][1]['inventory']['currency']['gp'] == 1


def test_currency_offer_to_untracked_npc_is_pending_not_rejected():
    state = _two_player_state()
    validation = validate_declared_actions(
        state=state,
        declared_actions=[
            {
                'id': 'act_trade_old_woman',
                'type': 'currency.transfer',
                'actorId': 'player_1',
                'fromActorId': 'player_1',
                'toActorName': 'the old woman',
                'currency': 'gp',
                'amount': 1,
                'sourceText': 'I give the old woman one gold coin for bread.',
            }
        ],
        current_turn=1,
    )
    state_log = build_state_log(turn_id=1, pre_validation=validation)
    confirmed = turn_pipeline_module._confirmed_pre_dm_changes(
        turn=DmTurn(turn_id=1),
        pre_validation=validation,
        pending_immediate_changes=[],
        dm_response_text='You give the old woman 1 gold and she hands you bread.',
    )

    assert validation['validatedActions'][0]['status'] == 'pending'
    assert validation['validatedActions'][0]['normalizedAction']['untrackedTarget'] is True
    assert not any(result['status'] == 'invalid' for result in validation['validatedActions'])
    assert state_log['lines'] == []
    assert confirmed == []


def test_mixed_state_change_stress_batch_is_atomic_non_negative_and_idempotent():
    state = _two_player_state()
    actor = state['playerCharacters'][0]
    actor['health'] = {'currentHp': 12, 'maxHp': 20, 'tempHp': 3, 'conditions': []}
    actor['xp'] = {'current': 100, 'nextLevelAt': 300}
    actor['inventory']['items'] = [
        _item('Minor Healing Potion', item_id='potion_1', quantity=2, item_type='consumable', subtype='potion'),
        _item('Iron Sword', item_id='sword_1', quantity=1, item_type='weapon', subtype='sword'),
        _item('Trail Ration', item_id='ration_1', quantity=5, item_type='consumable', subtype='food'),
    ]
    actor['inventory']['currency'] = {'pp': 0, 'gp': 20, 'ep': 0, 'sp': 10, 'cp': 25}

    changes = [
        {
            'id': 'loot_herbs',
            'type': 'inventory.add',
            'actorId': 'player_1',
            'item': {'name': 'Moonlit Herb', 'quantity': 3, 'weight': 0.1, 'type': 'misc'},
            'quantity': 3,
            'visible': True,
        },
        {'id': 'drink_potion', 'type': 'inventory.remove', 'actorId': 'player_1', 'itemId': 'potion_1', 'quantity': 1, 'visible': True},
        {'id': 'buy_shield_gold', 'type': 'currency.remove', 'actorId': 'player_1', 'currency': 'gp', 'amount': 5, 'visible': True},
        {
            'id': 'buy_shield_item',
            'type': 'inventory.add',
            'actorId': 'player_1',
            'item': {'name': 'Iron Shield', 'quantity': 1, 'weight': 6, 'type': 'armor', 'subtype': 'shield'},
            'quantity': 1,
            'visible': True,
        },
        {'id': 'sell_rations_item', 'type': 'inventory.remove', 'actorId': 'player_1', 'itemId': 'ration_1', 'quantity': 2, 'visible': True},
        {'id': 'sell_rations_silver', 'type': 'currency.add', 'actorId': 'player_1', 'currency': 'sp', 'amount': 4, 'visible': True},
        {
            'id': 'give_sword',
            'type': 'inventory.transfer',
            'actorId': 'player_1',
            'fromActorId': 'player_1',
            'toActorId': 'player_2',
            'itemId': 'sword_1',
            'quantity': 1,
            'visible': True,
        },
        {
            'id': 'give_copper',
            'type': 'currency.transfer',
            'actorId': 'player_1',
            'fromActorId': 'player_1',
            'toActorId': 'player_2',
            'currency': 'cp',
            'amount': 10,
            'visible': True,
        },
        {'id': 'trap_damage', 'type': 'health.damage', 'actorId': 'player_1', 'amount': 7, 'visible': True},
        {'id': 'healing_light', 'type': 'health.heal', 'actorId': 'player_1', 'amount': 5, 'visible': True},
        {'id': 'quest_xp', 'type': 'xp.add', 'actorId': 'player_1', 'amount': 75, 'visible': True},
    ]

    validation = validate_state_changes(state=state, changes=changes)
    result = apply_state_changes(state, validated_changes_for_application(validation))
    next_state = result['nextState']
    retry = apply_state_changes(next_state, validated_changes_for_application(validation))
    source = next_state['playerCharacters'][0]
    target = next_state['playerCharacters'][1]
    source_items = {item['id']: item for item in source['inventory']['items']}
    source_items_by_name = {item['name']: item for item in source['inventory']['items']}
    target_items = {item['name']: item for item in target['inventory']['items']}
    state_log = build_state_log(turn_id=99, post_validation=validation)

    assert validation['rejected'] == []
    assert source_items['potion_1']['quantity'] == 1
    assert source_items['ration_1']['quantity'] == 3
    assert source_items_by_name['Moonlit Herb']['quantity'] == 3
    assert source_items_by_name['Moonlit Herb']['weight'] == 0.1
    assert source_items_by_name['Iron Shield']['weight'] == 6
    assert 'sword_1' not in source_items
    assert target_items['Iron Sword']['quantity'] == 1
    assert source['inventory']['currency'] == {'pp': 0, 'gp': 15, 'ep': 0, 'sp': 14, 'cp': 15}
    assert target['inventory']['currency']['cp'] == 10
    assert source['health']['tempHp'] == 0
    assert source['health']['currentHp'] == 13
    assert source['xp']['current'] == 175
    assert retry['appliedChanges'] == []
    assert len(retry['skippedChanges']) == len(result['appliedChanges'])
    assert all(item.get('quantity', 0) > 0 for actor_state in next_state['playerCharacters'] for item in actor_state['inventory']['items'])
    assert all(amount >= 0 for actor_state in next_state['playerCharacters'] for amount in actor_state['inventory']['currency'].values())
    assert source['health']['currentHp'] >= 0
    assert source['xp']['current'] >= 0
    assert len([line for line in state_log['lines'] if line['changeType'] in {'inventory.remove', 'inventory.add'} and 'Sword' in line['message']]) == 1
    assert any(line['message'] == 'Added 75 XP.' for line in state_log['lines'])


def test_invalid_state_change_stress_rejects_without_partial_mutation():
    state = _two_player_state()
    state['playerCharacters'][0]['xp'] = {'current': 0, 'nextLevelAt': 300}
    before_source_items = list(state['playerCharacters'][0]['inventory']['items'])
    before_source_currency = dict(state['playerCharacters'][0]['inventory']['currency'])
    before_target_items = list(state['playerCharacters'][1]['inventory']['items'])
    before_target_currency = dict(state['playerCharacters'][1]['inventory']['currency'])

    validation = validate_state_changes(
        state=state,
        changes=[
            {'id': 'bad_remove_too_many', 'type': 'inventory.remove', 'actorId': 'player_1', 'itemId': 'rope_1', 'quantity': 3},
            {'id': 'bad_overspend', 'type': 'currency.remove', 'actorId': 'player_1', 'currency': 'gp', 'amount': 99},
            {
                'id': 'bad_self_item_transfer',
                'type': 'inventory.transfer',
                'actorId': 'player_1',
                'fromActorId': 'player_1',
                'toActorId': 'player_1',
                'itemId': 'rope_1',
                'quantity': 1,
            },
            {
                'id': 'bad_missing_target_currency_transfer',
                'type': 'currency.transfer',
                'actorId': 'player_1',
                'fromActorId': 'player_1',
                'toActorId': 'player_missing',
                'currency': 'cp',
                'amount': 1,
            },
            {'id': 'bad_zero_xp', 'type': 'xp.add', 'actorId': 'player_1', 'amount': 0},
            {'id': 'bad_xp_loss_at_zero', 'type': 'xp.remove', 'actorId': 'player_1', 'amount': 5},
            {'id': 'bad_zero_heal', 'type': 'health.heal', 'actorId': 'player_1', 'amount': 0},
            {'id': 'bad_add_missing_quantity', 'type': 'inventory.add', 'actorId': 'player_1', 'item': {'name': 'Lantern'}},
            {'id': 'bad_unknown_type', 'type': 'quest.delete', 'actorId': 'player_1', 'amount': 1},
            {'id': 'bad_missing_actor', 'type': 'health.damage', 'actorId': 'player_missing', 'amount': 1},
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['accepted'] == []
    assert validation['modified'] == []
    assert len(validation['rejected']) == 10
    assert result['appliedChanges'] == []
    assert result['nextState']['playerCharacters'][0]['inventory']['items'] == before_source_items
    assert result['nextState']['playerCharacters'][0]['inventory']['currency'] == before_source_currency
    assert result['nextState']['playerCharacters'][1]['inventory']['items'] == before_target_items
    assert result['nextState']['playerCharacters'][1]['inventory']['currency'] == before_target_currency
    assert result['nextState']['playerCharacters'][0]['xp']['current'] == 0


def test_scene_update_changes_mood_danger_and_type_without_removing_location():
    state = _state()
    state['currentScene'] = {
        'locationId': 'blackwake_tavern',
        'name': 'Blackwake Tavern',
        'sceneType': 'social',
        'dangerLevel': 0,
        'mood': 'calm',
        'combatState': 'none',
    }

    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'scene_turn_1',
                'type': 'scene.update',
                'source': 'post_dm',
                'reason': 'The tavern mood changes.',
                'turnId': 21,
                'sceneType': 'mystery',
                'dangerLevel': 3,
                'mood': 'tense',
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))
    scene = result['nextState']['currentScene']

    assert validation['rejected'] == []
    assert scene['locationId'] == 'blackwake_tavern'
    assert scene['name'] == 'Blackwake Tavern'
    assert scene['sceneType'] == 'mystery'
    assert scene['dangerLevel'] == 3
    assert scene['mood'] == 'tense'
    assert scene['updatedAtTurn'] == 21


def test_scene_move_location_updates_scene_and_marks_location_visited():
    state = _state()
    state['currentScene'] = {'locationId': 'blackwake_tavern', 'name': 'Blackwake Tavern'}
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'move_old_harbor',
                'type': 'scene.move_location',
                'source': 'post_dm',
                'reason': 'The party arrives.',
                'turnId': 22,
                'locationId': 'old_harbor',
                'name': 'Old Harbor',
                'sceneType': 'exploration',
                'mood': 'mysterious',
                'dangerLevel': 2,
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))
    scene = result['nextState']['currentScene']
    location = result['nextState']['locations'][0]

    assert validation['rejected'] == []
    assert scene['locationId'] == 'old_harbor'
    assert scene['name'] == 'Old Harbor'
    assert location['id'] == 'old_harbor'
    assert location['status'] == 'visited'
    assert location['firstDiscoveredTurn'] == 22
    assert location['lastVisitedTurn'] == 22


def test_scene_move_location_resets_stale_scene_local_fields():
    state = _state()
    state['currentScene'] = {
        'locationId': 'blackwake_tavern',
        'name': 'Blackwake Tavern',
        'sceneType': 'combat',
        'mood': 'dangerous',
        'dangerLevel': 8,
        'combatState': 'active',
        'description': 'Kozuki is down while stale NPCs crowd the room.',
        'activeNpcIds': ['captain_velra', 'stale_orc'],
    }
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'move_old_harbor_clean',
                'type': 'scene.move_location',
                'source': 'post_dm',
                'reason': 'The party leaves for the harbor.',
                'turnId': 23,
                'locationId': 'old_harbor',
                'name': 'Old Harbor',
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))
    scene = result['nextState']['currentScene']

    assert validation['rejected'] == []
    assert scene['locationId'] == 'old_harbor'
    assert scene['dangerLevel'] == 0
    assert scene['combatState'] == 'none'
    assert scene['description'] == ''
    assert scene['activeNpcIds'] == []
    assert 'mood' not in scene


def test_location_discover_adds_location_and_does_not_duplicate_on_retry():
    state = _state()
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'discover_old_harbor',
                'type': 'location.discover',
                'source': 'post_dm',
                'reason': 'The old harbor becomes known.',
                'turnId': 23,
                'locationId': 'old_harbor',
                'name': 'Old Harbor',
                'locationType': 'town',
                'description': 'A foggy harbor with old stone piers.',
                'tags': ['coastal'],
            }
        ],
    )
    first = apply_state_changes(state, validated_changes_for_application(validation))
    retry = apply_state_changes(first['nextState'], validated_changes_for_application(validation))

    assert validation['rejected'] == []
    assert len(first['nextState']['locations']) == 1
    assert first['nextState']['locations'][0]['name'] == 'Old Harbor'
    assert retry['appliedChanges'] == []
    assert len(retry['nextState']['locations']) == 1


def test_quest_add_creates_quest_with_objective():
    state = _state()
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'add_missing_sailor',
                'type': 'quest.add',
                'source': 'post_dm',
                'reason': 'Velra gives the party a quest.',
                'turnId': 24,
                'questId': 'find_missing_sailor',
                'title': 'Find the Missing Sailor',
                'summary': 'Find what happened to the missing sailor.',
                'stage': 'Investigate the docks',
                'objectives': [
                    {
                        'id': 'talk_to_captain_velra',
                        'description': 'Talk to Captain Velra about the missing sailor.',
                        'status': 'open',
                    }
                ],
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))
    quest = result['nextState']['quests'][0]

    assert validation['rejected'] == []
    assert quest['id'] == 'find_missing_sailor'
    assert quest['status'] == 'active'
    assert quest['objectives'][0]['id'] == 'talk_to_captain_velra'
    assert result['nextState']['currentScene']['activeQuestIds'] == ['find_missing_sailor']


def test_quest_update_updates_stage_and_objective_without_duplicates():
    state = _state()
    state['quests'] = [
        {
            'id': 'find_missing_sailor',
            'title': 'Find the Missing Sailor',
            'status': 'active',
            'summary': 'Find what happened to the missing sailor.',
            'stage': 'Investigate the docks',
            'objectives': [
                {
                    'id': 'talk_to_captain_velra',
                    'description': 'Talk to Captain Velra about the missing sailor.',
                    'status': 'open',
                }
            ],
        }
    ]
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'update_missing_sailor_stage',
                'type': 'quest.update',
                'source': 'post_dm',
                'reason': 'The clue changes the quest stage.',
                'turnId': 25,
                'questId': 'find_missing_sailor',
                'stage': 'Search Old Harbor',
                'objectives': [
                    {
                        'id': 'talk_to_captain_velra',
                        'description': 'Talk to Captain Velra about the missing sailor.',
                        'status': 'completed',
                    }
                ],
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))
    quest = result['nextState']['quests'][0]

    assert validation['rejected'] == []
    assert quest['stage'] == 'Search Old Harbor'
    assert len(quest['objectives']) == 1
    assert quest['objectives'][0]['status'] == 'completed'


def test_quest_complete_marks_completed_and_does_not_recomplete_on_retry():
    state = _state()
    state['quests'] = [{'id': 'find_missing_sailor', 'title': 'Find the Missing Sailor', 'status': 'active'}]
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'complete_missing_sailor',
                'type': 'quest.complete',
                'source': 'post_dm',
                'reason': 'The DM clearly confirms completion.',
                'turnId': 26,
                'questId': 'find_missing_sailor',
            }
        ],
    )
    first = apply_state_changes(state, validated_changes_for_application(validation))
    retry = apply_state_changes(first['nextState'], validated_changes_for_application(validation))

    assert validation['rejected'] == []
    assert first['nextState']['quests'][0]['status'] == 'completed'
    assert first['nextState']['quests'][0]['completedAtTurn'] == 26
    assert retry['appliedChanges'] == []
    assert retry['nextState']['quests'][0]['completedAtTurn'] == 26


def test_npc_discover_adds_npc_and_links_location_and_quest():
    state = _state()
    state['locations'] = [{'id': 'old_harbor', 'name': 'Old Harbor', 'npcIds': [], 'questIds': []}]
    state['quests'] = [{'id': 'find_missing_sailor', 'title': 'Find the Missing Sailor', 'relatedNpcIds': []}]
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'discover_velra',
                'type': 'npc.discover',
                'source': 'post_dm',
                'reason': 'Captain Velra introduces herself.',
                'turnId': 27,
                'npcId': 'captain_velra',
                'name': 'Captain Velra',
                'race': 'Human',
                'role': 'dock captain',
                'disposition': 'neutral',
                'locationId': 'old_harbor',
                'questIds': ['find_missing_sailor'],
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['rejected'] == []
    assert result['nextState']['knownNpcs'][0]['id'] == 'captain_velra'
    assert result['nextState']['knownNpcs'][0]['race'] == 'Human'
    assert result['nextState']['locations'][0]['npcIds'] == ['captain_velra']
    assert result['nextState']['quests'][0]['relatedNpcIds'] == ['captain_velra']


def test_npc_change_targeting_player_character_is_rejected():
    state = _state()
    state['playerCharacters'][0]['name'] = 'Kozuki'
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'discover_kozuki_as_npc',
                'type': 'npc.discover',
                'source': 'post_dm',
                'reason': 'The extractor mistook a player for an NPC.',
                'turnId': 27,
                'npcId': 'kozuki',
                'name': 'Kozuki',
                'role': 'orc adventurer',
                'status': 'dead',
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['accepted'] == []
    assert validation['rejected'][0]['reason'] == "NPC change targets player character 'Kozuki'."
    assert result['nextState'].get('knownNpcs') in (None, [])


def test_npc_update_merges_memory_disposition_and_location_without_wiping_description():
    state = _state()
    state['locations'] = [{'id': 'old_harbor', 'name': 'Old Harbor', 'npcIds': []}]
    state['knownNpcs'] = [
        {
            'id': 'captain_velra',
            'name': 'Captain Velra',
            'description': 'A stern harbor watch captain with a scarred blue coat.',
            'disposition': 'neutral',
            'locationId': 'blackwake_tavern',
            'memory': ['Promised payment for help.'],
            'relationship': {'score': 0, 'label': 'neutral'},
        }
    ]
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'update_velra',
                'type': 'npc.update',
                'source': 'post_dm',
                'reason': 'Velra shares more context.',
                'turnId': 28,
                'npcId': 'captain_velra',
                'species': 'Elf',
                'disposition': 'friendly',
                'locationId': 'old_harbor',
                'memory': ['Promised to help the party find the missing sailor.'],
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))
    npc = result['nextState']['knownNpcs'][0]

    assert validation['rejected'] == []
    assert npc['description'] == 'A stern harbor watch captain with a scarred blue coat.'
    assert npc['race'] == 'Elf'
    assert npc['disposition'] == 'friendly'
    assert npc['locationId'] == 'old_harbor'
    assert npc['memory'] == [
        'Promised payment for help.',
        'Promised to help the party find the missing sailor.',
    ]


def test_missing_named_npc_update_becomes_discovery():
    state = _state()
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'update_marta_intro',
                'type': 'npc.update',
                'source': 'post_dm',
                'reason': 'Marta introduces herself after being asked her name.',
                'turnId': 29,
                'npcId': 'marta_fenwick',
                'name': 'Marta Fenwick',
                'role': 'corner shopkeeper',
                'disposition': 'friendly',
                'memory': ['Told Hoggy her name after being asked.'],
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))
    npc = result['nextState']['knownNpcs'][0]
    state_log = build_state_log(turn_id=29, post_validation=validation)

    assert validation['rejected'] == []
    assert validation['accepted'][0]['change']['type'] == 'npc.discover'
    assert npc['id'] == 'marta_fenwick'
    assert npc['name'] == 'Marta Fenwick'
    assert npc['firstMetTurn'] == 29
    assert npc['memory'] == ['Told Hoggy her name after being asked.']
    assert state_log['lines'][0]['message'] == 'Discovered NPC: Marta Fenwick.'


def test_missing_id_only_npc_update_still_rejects():
    validation = validate_state_changes(
        state=_state(),
        changes=[
            {
                'id': 'update_old_woman',
                'type': 'npc.update',
                'source': 'post_dm',
                'reason': 'Ambiguous update without a concrete name.',
                'turnId': 29,
                'npcId': 'old_woman',
                'memory': ['She seems nervous.'],
            }
        ],
    )

    assert validation['accepted'] == []
    assert validation['rejected'][0]['reason'] == 'NPC update target was not found.'


def test_unsupported_world_change_type_is_rejected():
    validation = validate_state_changes(
        state=_state(),
        changes=[
            {
                'id': 'delete_quest',
                'type': 'quest.delete',
                'source': 'post_dm',
                'reason': 'Unsupported deletion.',
                'questId': 'find_missing_sailor',
            }
        ],
    )

    assert validation['accepted'] == []
    assert validation['rejected'][0]['reason'] == "Unsupported state change type 'quest.delete'."


def test_post_dm_extract_loot(app):
    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I search the goblin.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The goblin collapses. You find a rusted key and 12 copper pieces on its belt.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=9,
        )

    change_types = {change['type'] for change in result['proposedChanges']}
    assert 'inventory.add' in change_types
    assert 'currency.add' in change_types
    assert any(change.get('currency') == 'cp' and change.get('amount') == 12 for change in result['proposedChanges'])


def test_post_dm_extracts_explicit_xp_gain(app):
    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I claim the bounty.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The bounty is accepted. You gain 75 XP.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=10,
        )

    assert any(change['type'] == 'xp.add' and change.get('amount') == 75 for change in result['proposedChanges'])


def test_post_dm_does_not_extract_pending_roll_prompt_as_loot(app):
    dm_response = (
        'Danny, the stick lies before you on the cold stone floor. '
        'It would take a careful touch to lift the stick without snagging those wires.\n\n'
        'Make a Dexterity (Thieves Tools) check against a DC of 16. '
        'This represents the precision needed to safely pick up the stick without disturbing the inert trap.'
    )
    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I pick up a stick off the floor',
            validated_actions={},
            already_applied_changes=[],
            dm_response=dm_response,
            recent_timeline=[],
            actor_id='player_1',
            turn_id=11,
        )

    assert result['proposedChanges'] == []


def test_post_dm_does_not_extract_visible_unclaimed_item_as_loot(app):
    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I look around the chapel.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='You find a silver key resting on the altar, visible in the candlelight.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=112,
        )

    assert result['proposedChanges'] == []


def test_post_dm_heuristic_ignores_metaphorical_non_mechanical_phrases(app):
    phrases = [
        'You find your courage.',
        'You take a breath.',
        'You drop your guard.',
        'You gain confidence.',
        'You take cover.',
        'You lose focus.',
        'You spend a moment looking around.',
    ]

    with app.app_context():
        for phrase in phrases:
            result = extract_post_dm_outcomes(
                state_before_dm={},
                player_message='I steady myself.',
                validated_actions={},
                already_applied_changes=[],
                dm_response=phrase,
                recent_timeline=[],
                actor_id='player_1',
                turn_id=111,
            )
            assert result['proposedChanges'] == [], phrase


def test_post_dm_extracts_confirmed_pickup_as_loot(app):
    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I pick up a stick off the floor',
            validated_actions={},
            already_applied_changes=[],
            dm_response='You pick up the stick and tuck it under your arm.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=12,
        )

    assert any(
        change['type'] == 'inventory.add' and change.get('itemName') == 'stick'
        for change in result['proposedChanges']
    )


def test_post_dm_extracts_scene_danger_increase(app):
    state = _state()
    state['currentScene'] = {
        'locationId': 'old_road',
        'name': 'Old Road',
        'sceneType': 'travel',
        'dangerLevel': 1,
        'combatState': 'none',
    }
    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I keep walking down the road.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='Bandits spring from the pines, blades drawn. Roll initiative as arrows fly.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=13,
        )

    scene_change = next(change for change in result['proposedChanges'] if change['type'] == 'scene.update')
    assert scene_change['dangerLevel'] == 8
    assert scene_change['sceneType'] == 'combat'
    assert scene_change['combatState'] == 'active'
    assert scene_change['mood'] == 'dangerous'


def test_valid_empty_post_dm_helper_response_still_updates_scene_danger(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text='{"proposedChanges":[],"uncertainChanges":[],"notes":["no_concrete_inventory_change"]}',
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())
    state = _state()
    state['currentScene'] = {'locationId': 'ravine_bridge', 'name': 'Ravine Bridge', 'dangerLevel': 1}

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I step onto the bridge.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The unstable bridge groans underfoot, a dangerous drop yawning through the broken planks.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=14,
        )

    scene_change = next(change for change in result['proposedChanges'] if change['type'] == 'scene.update')
    assert scene_change['dangerLevel'] == 5
    assert scene_change['mood'] == 'tense'
    assert 'helper_post_dm' in result['notes']
    assert 'heuristic_scene_danger' in result['notes']
    assert result['debug']['source'] == 'helper'


def test_post_dm_extracts_scene_danger_decrease(app):
    state = _state()
    state['currentScene'] = {
        'locationId': 'old_road',
        'name': 'Old Road',
        'sceneType': 'combat',
        'dangerLevel': 8,
        'mood': 'dangerous',
        'combatState': 'active',
    }
    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I finish the fight.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The last enemy falls. The fight is over, and there is no immediate threat.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=15,
        )

    scene_change = next(change for change in result['proposedChanges'] if change['type'] == 'scene.update')
    assert scene_change['dangerLevel'] == 1
    assert scene_change['combatState'] == 'resolved'
    assert scene_change['mood'] == 'calm'


def test_valid_empty_post_dm_helper_response_prevents_fallback(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text='{"proposedChanges":[],"uncertainChanges":[],"notes":["no_concrete_state_change"]}',
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I pick up a stick off the floor',
            validated_actions={},
            already_applied_changes=[],
            dm_response='You pick up the stick and tuck it under your arm.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=13,
        )

    assert result['proposedChanges'] == []
    assert result['notes'] == ['no_concrete_state_change', 'helper_post_dm']
    assert result['debug']['source'] == 'helper'
    assert result['debug']['fallbackRan'] is False
    assert result['debug']['helperSchemaValid'] is True
    assert result['debug']['helperParsed']['proposedChanges'] == []


def test_post_dm_helper_string_item_is_normalized_and_gets_turn_scoped_id(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text=(
                    '{"proposedChanges":[{"type":"inventory.add","target":"player_1",'
                    '"item":"Wedged Stick (tripwire remnants attached, inert)","quantity":1}],'
                    '"uncertainChanges":[],"notes":"The DM explicitly says the item is gained."}'
                ),
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I roll a d20: 20',
            validated_actions={},
            already_applied_changes=[],
            dm_response='Danny gains: Wedged Stick (tripwire remnants attached, inert).',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=222,
        )

    change = result['proposedChanges'][0]
    assert change['actorId'] == 'player_1'
    assert change['itemName'] == 'Wedged Stick (tripwire remnants attached, inert)'
    assert change['item']['name'] == 'Wedged Stick (tripwire remnants attached, inert)'
    assert change['id'].startswith('chg_')
    assert change['id'] != 'post_chg_001'
    assert change['turnId'] == 222


def test_valid_empty_post_dm_helper_response_with_string_notes_prevents_fallback(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text=(
                    '{"proposedChanges":[],"uncertainChanges":[],"notes":"The DM response asks for a skill check; '
                    'the stick is not yet acquired."}'
                ),
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I pick a stick up off the floor',
            validated_actions={},
            already_applied_changes=[],
            dm_response=(
                'It will take a steady, careful hand to lift the stick without snagging the wires. '
                'Make a Dexterity check against DC 16.'
            ),
            recent_timeline=[],
            actor_id='player_1',
            turn_id=15,
        )

    assert result['proposedChanges'] == []
    assert result['notes'] == ['The DM response asks for a skill check; the stick is not yet acquired.', 'helper_post_dm']
    assert result['debug']['source'] == 'helper'
    assert result['debug']['fallbackRan'] is False
    assert result['debug']['helperSchemaValid'] is True


def test_invalid_post_dm_helper_response_uses_fallback(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(text='not json', provider='fake', model='fake-helper')

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I pick up a stick off the floor',
            validated_actions={},
            already_applied_changes=[],
            dm_response='You pick up the stick and tuck it under your arm.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=14,
        )

    assert any(
        change['type'] == 'inventory.add' and change.get('itemName') == 'stick'
        for change in result['proposedChanges']
    )
    assert result['debug']['source'] == 'heuristic'
    assert result['debug']['fallbackRan'] is True
    assert result['debug']['fallbackReason'] == 'helper_json_invalid'


def test_post_dm_helper_unsupported_change_type_does_not_apply_or_fallback(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text='{"proposedChanges":[{"type":"quest.delete","actorId":"player_1","name":"Find the moon"}],"uncertainChanges":[]}',
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I pick up the stick',
            validated_actions={},
            already_applied_changes=[],
            dm_response='You pick up the stick.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=31,
        )

    assert result['proposedChanges'] == []
    assert result['debug']['source'] == 'helper'
    assert result['debug']['fallbackRan'] is False


def test_post_dm_helper_missing_required_fields_does_not_apply_or_fallback(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text='{"proposedChanges":[{"type":"currency.add","actorId":"player_1","currency":"gp"}],"uncertainChanges":[]}',
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I search the pouch',
            validated_actions={},
            already_applied_changes=[],
            dm_response='You find 5 gold.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=32,
        )

    assert result['proposedChanges'] == []
    assert result['debug']['source'] == 'helper'
    assert result['debug']['fallbackRan'] is False


def test_post_dm_helper_currency_type_alias_normalizes_to_currency(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text=(
                    '{"proposedChanges":[{"type":"currency.transfer","actorId":"player_1",'
                    '"fromActorId":"player_1","toActorId":"player_2","currencyType":"gp","amount":1}],'
                    '"uncertainChanges":[]}'
                ),
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm=_two_player_state(),
            player_message='I give Borin one gold.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='Kael gives 1 gold to Borin.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=33,
        )

    validation = validate_state_changes(state=_two_player_state(), changes=result['proposedChanges'])
    applied = validated_changes_for_application(validation)

    assert result['proposedChanges'][0]['currency'] == 'gp'
    assert validation['rejected'] == []
    assert [change['type'] for change in applied] == ['currency.remove', 'currency.add']


def test_post_dm_helper_inventory_remove_missing_quantity_does_not_apply_or_fallback(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text=(
                    '{"proposedChanges":[{"type":"inventory.remove","actorId":"player_1",'
                    '"itemName":"Wedged Stick"}],"uncertainChanges":[]}'
                ),
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I drop the stick',
            validated_actions={},
            already_applied_changes=[],
            dm_response='You drop the Wedged Stick.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=33,
        )

    assert result['proposedChanges'] == []
    assert result['debug']['source'] == 'helper'
    assert result['debug']['fallbackRan'] is False


def test_post_dm_pipeline_skips_extraction_for_pending_roll_turn(app):
    ids = seed_world_campaign_player_session(app)
    dm_response = (
        'It will take a steady, careful hand to lift the stick without snagging the wires. '
        'Make a Dexterity check against DC 16. Roll a d20.'
    )

    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        player = db.session.get(Player, ids['player_id'])
        session_obj = db.session.get(Session, ids['session_id'])
        assert campaign is not None
        assert player is not None
        assert session_obj is not None

        actor_id = f'player_{player.player_id}'
        state = _state(items=[_item('Smooth Stone')])
        state['playerCharacters'][0]['id'] = actor_id
        state['playerCharacters'][0]['playerId'] = player.player_id
        session_obj.state_snapshot = safe_json_dumps(state, {})

        turn = DmTurn(
            session_id=session_obj.session_id,
            campaign_id=campaign.campaign_id,
            player_id=player.player_id,
            player_input='I pick a stick up off the floor',
            dm_output=dm_response,
            requires_roll=True,
            roll_value=None,
            outcome_status='deferred',
            status='completed',
            metadata_json=safe_json_dumps(
                {
                    STATE_PIPELINE_METADATA_KEY: {
                        'version': STATE_PIPELINE_VERSION,
                        'actorId': actor_id,
                        'stateBeforeDm': state,
                        'preDmValidation': {'validatedActions': [], 'immediateChanges': []},
                        'immediateValidation': {'accepted': [], 'rejected': [], 'modified': []},
                        'immediateAppliedChanges': [],
                    }
                },
                {},
            ),
        )
        db.session.add(turn)
        db.session.commit()

        result = post_dm_pipeline(
            turn=turn,
            session_obj=session_obj,
            campaign=campaign,
            player=player,
            dm_response_text=dm_response,
        )
        db.session.commit()

        refreshed_session = db.session.get(Session, ids['session_id'])
        refreshed_turn = db.session.get(DmTurn, turn.turn_id)
        assert refreshed_session is not None
        assert refreshed_turn is not None
        snapshot = safe_json_loads(refreshed_session.state_snapshot, {})
        item_names = [
            item.get('name')
            for item in snapshot['playerCharacters'][0]['inventory']['items']
            if isinstance(item, dict)
        ]

        assert result['postExtraction']['notes'] == ['post_dm_skipped_pending_roll']
        assert result['postExtraction']['debug']['source'] == 'skipped'
        assert result['postExtraction']['debug']['fallbackRan'] is False
        assert result['postAppliedChanges'] == []
        assert item_names == ['Smooth Stone']
        assert TurnEvent.query.filter_by(turn_id=turn.turn_id, event_type='state_update').count() == 0


def test_canon_patch_credits_state_pipeline_character_changes_without_double_apply(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        player = db.session.get(Player, ids['player_id'])
        session_obj = db.session.get(Session, ids['session_id'])
        assert campaign is not None
        assert player is not None
        assert session_obj is not None
        player.stats = safe_json_dumps(
            {
                'current_hp': 17,
                'hp_current': 17,
                'max_hp': 20,
                'hp_max': 20,
                'copper': 12,
                'xp': 50,
                'experience': 50,
            },
            {},
        )
        turn = DmTurn(
            session_id=session_obj.session_id,
            campaign_id=campaign.campaign_id,
            player_id=player.player_id,
            player_input='I drink my potion and search the pouch.',
            dm_output='You drink the potion. Restore 7 HP. You gain 12 copper pieces.',
            status='completed',
            metadata_json=safe_json_dumps(
                {
                    'immediate_state_changes_applied': {
                        'inventory_changes_applied': [],
                        'character_state_changes_applied': [
                            {'player_id': player.player_id, 'change_type': 'health.heal', 'hp_delta': 7},
                            {
                                'player_id': player.player_id,
                                'change_type': 'currency.add',
                                'currency_delta': {'copper': 12},
                            },
                            {'player_id': player.player_id, 'change_type': 'xp.add', 'xp_delta': 50},
                        ],
                    }
                },
                {},
            ),
        )
        db.session.add(turn)
        db.session.commit()

        applied = apply_canon_patch(
            turn=turn,
            campaign=campaign,
            patch={'entities': [], 'facts': [], 'threads': [], 'inventory_changes': [], 'projection': {}},
            extractor_model='test',
        )
        db.session.commit()

        refreshed = db.session.get(Player, ids['player_id'])
        stats = safe_json_loads(refreshed.stats, {})
        assert stats['current_hp'] == 17
        assert stats['copper'] == 12
        assert stats['xp'] == 50
        assert all(change.get('already_applied') for change in applied['character_state_changes_applied'])


def test_canon_patch_skips_state_pipeline_managed_state_domains(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        player = db.session.get(Player, ids['player_id'])
        session_obj = db.session.get(Session, ids['session_id'])
        assert campaign is not None
        assert player is not None
        assert session_obj is not None
        player.inventory = safe_json_dumps([], [])
        player.stats = safe_json_dumps(
            {
                'current_hp': 5,
                'hp_current': 5,
                'max_hp': 10,
                'hp_max': 10,
                'copper': 0,
                'xp': 0,
                'experience': 0,
            },
            {},
        )
        turn = DmTurn(
            session_id=session_obj.session_id,
            campaign_id=campaign.campaign_id,
            player_id=player.player_id,
            player_input='I grab it.',
            dm_output='You grab it. Restore 3 HP. You gain 10 copper pieces. You gain 50 XP.',
            status='completed',
            metadata_json=safe_json_dumps(
                {
                    'state_pipeline': {
                        'managedDomains': ['inventory', 'currency', 'health', 'xp'],
                    },
                    'immediate_state_changes_applied': {
                        'inventory_changes_applied': [],
                        'character_state_changes_applied': [],
                    },
                },
                {},
            ),
        )
        db.session.add(turn)
        db.session.commit()

        applied = apply_canon_patch(
            turn=turn,
            campaign=campaign,
            patch={
                'entities': [],
                'facts': [],
                'threads': [],
                'inventory_changes': [{'action': 'acquire', 'item_name': 'it', 'quantity': 1}],
                'projection': {},
            },
            extractor_model='test',
        )
        db.session.commit()

        refreshed = db.session.get(Player, ids['player_id'])
        stats = safe_json_loads(refreshed.stats, {})
        assert safe_json_loads(refreshed.inventory, []) == []
        assert stats['current_hp'] == 5
        assert stats['copper'] == 0
        assert stats['xp'] == 0
        assert applied['inventory_changes_applied'] == []
        assert applied['character_state_changes_applied'] == []


def test_post_dm_pipeline_retry_does_not_duplicate_item_hp_currency_or_xp(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        player = db.session.get(Player, ids['player_id'])
        session_obj = db.session.get(Session, ids['session_id'])
        assert campaign is not None
        assert player is not None
        assert session_obj is not None
        actor_id = f'player_{player.player_id}'
        state = _state(items=[], currency={'pp': 0, 'gp': 0, 'ep': 0, 'sp': 0, 'cp': 0}, hp_current=10, hp_max=20, xp_current=0)
        state['playerCharacters'][0]['id'] = actor_id
        state['playerCharacters'][0]['playerId'] = player.player_id
        session_obj.state_snapshot = safe_json_dumps(state, {})
        player.inventory = safe_json_dumps([], [])
        player.stats = safe_json_dumps(
            {'current_hp': 10, 'hp_current': 10, 'max_hp': 20, 'hp_max': 20, 'xp': 0, 'experience': 0},
            {},
        )
        turn = DmTurn(
            session_id=session_obj.session_id,
            campaign_id=campaign.campaign_id,
            player_id=player.player_id,
            player_input='I search the goblin.',
            dm_output='You find a rusted key and 12 copper pieces. Restore 3 HP. You gain 8 XP.',
            status='completed',
            metadata_json=safe_json_dumps(
                {
                    STATE_PIPELINE_METADATA_KEY: {
                        'version': STATE_PIPELINE_VERSION,
                        'actorId': actor_id,
                        'stateBeforeDm': state,
                        'preDmValidation': {'validatedActions': [], 'immediateChanges': []},
                        'immediateValidation': {'accepted': [], 'rejected': [], 'modified': []},
                        'immediateAppliedChanges': [],
                    }
                },
                {},
            ),
        )
        db.session.add(turn)
        db.session.commit()

        first = post_dm_pipeline(
            turn=turn,
            session_obj=session_obj,
            campaign=campaign,
            player=player,
            dm_response_text=turn.dm_output,
        )
        db.session.commit()
        second = post_dm_pipeline(
            turn=turn,
            session_obj=session_obj,
            campaign=campaign,
            player=player,
            dm_response_text=turn.dm_output,
        )
        db.session.commit()

        refreshed_player = db.session.get(Player, ids['player_id'])
        inventory = safe_json_loads(refreshed_player.inventory, [])
        stats = safe_json_loads(refreshed_player.stats, {})
        assert len([item for item in inventory if item.get('name') == 'rusted key']) == 1
        assert stats['copper'] == 12
        assert stats['current_hp'] == 13
        assert stats['xp'] == 8
        assert len(first['postAppliedChanges']) == len(second['postAppliedChanges'])


def test_post_dm_pipeline_retry_does_not_duplicate_world_state_records(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    helper_text = (
        '{"proposedChanges":['
        '{"type":"scene.move_location","locationId":"blackwake_tavern","name":"Blackwake Tavern","sceneType":"social","mood":"tense"},'
        '{"type":"location.discover","locationId":"blackwake_tavern","name":"Blackwake Tavern","locationType":"tavern","description":"A busy tavern full of dockside rumors."},'
        '{"type":"quest.add","questId":"find_missing_sailor","title":"Find the Missing Sailor","objectives":[{"id":"talk_to_velra","description":"Talk to Captain Velra.","status":"open"}]},'
        '{"type":"npc.discover","npcId":"captain_velra","name":"Captain Velra","role":"dock captain","locationId":"blackwake_tavern","questIds":["find_missing_sailor"]}'
        '],"uncertainChanges":[]}'
    )

    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(text=helper_text, provider='fake', model='fake-world-helper')

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        campaign = db.session.get(Campaign, ids['campaign_id'])
        player = db.session.get(Player, ids['player_id'])
        session_obj = db.session.get(Session, ids['session_id'])
        assert campaign is not None
        assert player is not None
        assert session_obj is not None
        actor_id = f'player_{player.player_id}'
        state = _state()
        state['playerCharacters'][0]['id'] = actor_id
        state['playerCharacters'][0]['playerId'] = player.player_id
        state['currentScene'] = {'locationId': 'old_road', 'name': 'Old Road', 'sceneType': 'travel', 'dangerLevel': 1}
        state['locations'] = []
        state['quests'] = []
        state['knownNpcs'] = []
        session_obj.state_snapshot = safe_json_dumps(state, {})
        turn = DmTurn(
            session_id=session_obj.session_id,
            campaign_id=campaign.campaign_id,
            player_id=player.player_id,
            player_input='I enter the tavern.',
            dm_output='You arrive at Blackwake Tavern. Captain Velra asks you to find the missing sailor.',
            status='completed',
            metadata_json=safe_json_dumps(
                {
                    STATE_PIPELINE_METADATA_KEY: {
                        'version': STATE_PIPELINE_VERSION,
                        'actorId': actor_id,
                        'stateBeforeDm': state,
                        'preDmValidation': {'validatedActions': [], 'immediateChanges': []},
                        'immediateValidation': {'accepted': [], 'rejected': [], 'modified': []},
                        'immediateAppliedChanges': [],
                    }
                },
                {},
            ),
        )
        db.session.add(turn)
        db.session.commit()

        first = post_dm_pipeline(
            turn=turn,
            session_obj=session_obj,
            campaign=campaign,
            player=player,
            dm_response_text=turn.dm_output,
        )
        db.session.commit()
        second = post_dm_pipeline(
            turn=turn,
            session_obj=session_obj,
            campaign=campaign,
            player=player,
            dm_response_text=turn.dm_output,
        )
        db.session.commit()

        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
        assert snapshot['currentScene']['locationId'] == 'blackwake_tavern'
        assert len([location for location in snapshot['locations'] if location.get('id') == 'blackwake_tavern']) == 1
        assert len([quest for quest in snapshot['quests'] if quest.get('id') == 'find_missing_sailor']) == 1
        assert len([npc for npc in snapshot['knownNpcs'] if npc.get('id') == 'captain_velra']) == 1
        assert snapshot['locations'][0]['npcIds'] == ['captain_velra']
        assert snapshot['quests'][0]['relatedNpcIds'] == ['captain_velra']
        assert len(first['postAppliedChanges']) == len(second['postAppliedChanges'])


def test_validate_state_changes_does_not_treat_new_turn_fallback_id_as_duplicate():
    state = _state(items=[])
    state['stateChangeLedger'] = [{'id': 'post_chg_001', 'type': 'inventory.remove', 'source': 'post_dm'}]

    normalized = normalize_post_extraction(
        {
            'proposedChanges': [
                {
                    'id': 'chg_new_turn_add',
                    'type': 'inventory.add',
                    'actorId': 'player_1',
                    'item': 'Wedged Stick',
                    'quantity': 1,
                }
            ]
        },
        fallback_actor_id='player_1',
    )
    validation = validate_state_changes(state=state, changes=normalized['proposedChanges'])

    assert validation['accepted'][0]['change']['itemName'] == 'Wedged Stick'
    assert validation['rejected'] == []


def test_validate_inventory_add_accepts_nested_item_quantity():
    state = _state()
    normalized = normalize_post_extraction(
        {
            'proposedChanges': [
                {
                    'type': 'inventory.add',
                    'actorId': 'player_1',
                    'item': {'name': 'Stick', 'quantity': 1, 'weight': 0.5, 'type': 'misc'},
                }
            ]
        },
        fallback_actor_id='player_1',
    )

    validation = validate_state_changes(state=state, changes=normalized['proposedChanges'])
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['accepted'][0]['change']['quantity'] == 1
    item = result['nextState']['playerCharacters'][0]['inventory']['items'][0]
    assert item['name'] == 'Stick'
    assert item['weight'] == 0.5


def test_post_dm_inventory_remove_requires_helper_quantity():
    normalized = normalize_post_extraction(
        {
            'proposedChanges': [
                {
                    'type': 'inventory.remove',
                    'actorId': 'player_1',
                    'itemName': 'Wedged Stick',
                }
            ]
        },
        fallback_actor_id='player_1',
    )

    assert normalized['proposedChanges'] == []


def test_post_dm_inventory_remove_with_explicit_quantity_applies():
    state = _state(items=[_item('Wedged Stick', item_id='stick_1', quantity=1)])
    normalized = normalize_post_extraction(
        {
            'proposedChanges': [
                {
                    'type': 'inventory.remove',
                    'actorId': 'player_1',
                    'itemName': 'Wedged Stick',
                    'quantity': 1,
                }
            ]
        },
        fallback_actor_id='player_1',
    )

    validation = validate_state_changes(state=state, changes=normalized['proposedChanges'])
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert normalized['proposedChanges'][0]['quantity'] == 1
    assert validation['rejected'] == []
    assert validation['accepted'][0]['change']['itemId'] == 'stick_1'
    assert result['nextState']['playerCharacters'][0]['inventory']['items'] == []


def test_inventory_add_accepts_helper_weight_alias():
    state = _state()
    normalized = normalize_post_extraction(
        {
            'proposedChanges': [
                {
                    'type': 'inventory.add',
                    'actorId': 'player_1',
                    'item': {'name': 'Sandstone Chunk', 'quantity': 1, 'weightLbs': '4 lbs'},
                }
            ]
        },
        fallback_actor_id='player_1',
    )

    validation = validate_state_changes(state=state, changes=normalized['proposedChanges'])
    result = apply_state_changes(state, validated_changes_for_application(validation))

    item = result['nextState']['playerCharacters'][0]['inventory']['items'][0]
    assert validation['rejected'] == []
    assert normalized['proposedChanges'][0]['item']['weight'] == 4
    assert item['name'] == 'Sandstone Chunk'
    assert item['weight'] == 4


def test_reject_duplicate_state_change_id():
    state = _state()
    first = apply_state_changes(
        state,
        [
            {
                'id': 'dup_change',
                'type': 'health.heal',
                'actorId': 'player_1',
                'amount': 2,
                'source': 'post_dm',
                'reason': 'Healing.',
                'visible': True,
            }
        ],
    )

    validation = validate_state_changes(
        state=first['nextState'],
        changes=[
            {
                'id': 'dup_change',
                'type': 'health.heal',
                'actorId': 'player_1',
                'amount': 2,
                'source': 'post_dm',
                'reason': 'Healing.',
                'visible': True,
            }
        ],
    )

    assert validation['rejected'][0]['reason'] == 'State change was already applied.'


def test_build_visible_state_log():
    validation = {
        'accepted': [
            {
                'change': {
                    'id': 'chg_1',
                    'type': 'inventory.remove',
                    'itemName': 'Minor Healing Potion',
                    'quantity': 1,
                    'visible': True,
                },
                'reason': 'ok',
            }
        ],
        'modified': [],
        'rejected': [],
    }

    state_log = build_state_log(turn_id=1, immediate_validation=validation)

    assert state_log['lines'][0]['message'] == 'Removed Minor Healing Potion x1.'


def test_resolve_exact_item_name():
    result = resolve_inventory_item_reference(
        actor_inventory=[_item('Greatsword', item_type='weapon', subtype='sword'), _item('Longsword', item_type='weapon', subtype='sword')],
        requested_name='greatsword',
        requested_type='weapon',
    )

    assert result['status'] == 'resolved'
    assert result['itemName'] == 'Greatsword'
    assert result['resolutionMethod'] == 'exact_name'


def test_resolve_equipped_sword_when_multiple_swords_exist():
    result = resolve_inventory_item_reference(
        actor_inventory=[
            _item('Greatsword', item_type='weapon', subtype='sword'),
            _item('Longsword', item_type='weapon', subtype='sword', equipped=True),
        ],
        requested_name='sword',
        requested_type='weapon',
    )

    assert result['status'] == 'resolved'
    assert result['itemName'] == 'Longsword'
    assert result['resolutionMethod'] == 'equipped_item'


def test_resolve_single_candidate_sword():
    result = resolve_inventory_item_reference(
        actor_inventory=[
            _item('Longsword', item_type='weapon', subtype='sword'),
            _item('Shield', item_type='armor'),
        ],
        requested_name='sword',
        requested_type='weapon',
    )

    assert result['status'] == 'resolved'
    assert result['itemName'] == 'Longsword'
    assert result['resolutionMethod'] == 'single_candidate'


def test_requires_clarification_when_multiple_swords_exist_without_equipped_weapon():
    result = resolve_inventory_item_reference(
        actor_inventory=[
            _item('Greatsword', item_type='weapon', subtype='sword'),
            _item('Longsword', item_type='weapon', subtype='sword'),
        ],
        requested_name='sword',
        requested_type='weapon',
    )

    assert result['status'] == 'needs_clarification'
    assert [option['label'] for option in result['options']] == ['Greatsword', 'Longsword']


def test_resolve_recently_used_weapon_when_context_is_strong():
    result = resolve_inventory_item_reference(
        actor_inventory=[
            _item('Greatsword', item_type='weapon', subtype='sword'),
            _item('Longsword', item_type='weapon', subtype='sword'),
        ],
        requested_name='sword',
        requested_type='weapon',
        recent_context=['You grip your greatsword as the skeleton charges.'],
    )

    assert result['status'] == 'resolved'
    assert result['itemName'] == 'Greatsword'
    assert result['resolutionMethod'] == 'recent_context'


def test_resolve_default_weapon_when_no_equipped_weapon():
    result = resolve_inventory_item_reference(
        actor_inventory=[
            _item('Greatsword', item_id='great', item_type='weapon', subtype='sword'),
            _item('Longsword', item_id='long', item_type='weapon', subtype='sword'),
        ],
        requested_name='sword',
        requested_type='weapon',
        default_item_id='long',
    )

    assert result['status'] == 'resolved'
    assert result['itemName'] == 'Longsword'
    assert result['resolutionMethod'] == 'default_item'


def test_missing_item_when_no_candidate_exists():
    result = resolve_inventory_item_reference(
        actor_inventory=[_item('Shield', item_type='armor')],
        requested_name='longbow',
        requested_type='weapon',
    )

    assert result['status'] == 'missing'
