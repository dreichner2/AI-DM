from __future__ import annotations

from aidm_server.contracts import ProviderResponse
from aidm_server.database import db
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY, STATE_PIPELINE_VERSION
from aidm_server.game_state.application.applier import apply_state_changes
import aidm_server.game_state.extraction.post_dm_outcome_extractor as post_extractor_module
import aidm_server.game_state.extraction.pre_dm_action_extractor as pre_extractor_module
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
from aidm_server.models import Campaign, DmTurn, Player, Session, TurnEvent, safe_json_dumps, safe_json_loads
from tests.helpers import seed_world_campaign_player_session


def _state(*, items=None, currency=None, hp_current=10, hp_max=20, temp_hp=0):
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
                    'item': {'name': 'Stick', 'quantity': 1, 'type': 'misc'},
                }
            ]
        },
        fallback_actor_id='player_1',
    )

    validation = validate_state_changes(state=state, changes=normalized['proposedChanges'])
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['accepted'][0]['change']['quantity'] == 1
    assert result['nextState']['playerCharacters'][0]['inventory']['items'][0]['name'] == 'Stick'


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
