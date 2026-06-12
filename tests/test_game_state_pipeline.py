from __future__ import annotations

from aidm_server.contracts import ProviderResponse
from aidm_server.database import db
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY, STATE_PIPELINE_VERSION
from aidm_server.game_state.application.applier import apply_state_changes, persist_state_to_database
import aidm_server.game_state.extraction.post_dm_outcome_extractor as post_extractor_module
import aidm_server.game_state.extraction.pre_dm_action_extractor as pre_extractor_module
import aidm_server.game_state.orchestration.turn_pipeline as turn_pipeline_module
from aidm_server.game_state.extraction.post_dm_outcome_extractor import extract_post_dm_outcomes
from aidm_server.game_state.extraction.pre_dm_action_extractor import extract_pre_dm_actions
from aidm_server.game_state.extraction.schemas import normalize_post_extraction
from aidm_server.game_state.logging.state_log_builder import build_state_log
from aidm_server.game_state.models import compact_state_for_extraction, display_actor_id
from aidm_server.game_state.orchestration.turn_pipeline import post_dm_pipeline
from aidm_server.game_state.validation.inventory_validator import resolve_inventory_item_reference
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.emergent_memory import apply_canon_patch
from aidm_server.models import Campaign, CombatDebugEvent, DmTurn, Player, Session, TurnEvent, safe_json_dumps, safe_json_loads
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


def _enemy_roll_test_state():
    state = _state(hp_current=20, hp_max=20)
    state['playerCharacters'][0]['name'] = 'Legoless'
    state['playerCharacters'][0]['stats'] = {'armorClass': 13}
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {'id': 'player_1', 'name': 'Legoless', 'team': 'player', 'armorClass': 13},
            {
                'id': 'enemy_right_flank_raider_1',
                'name': 'Right-Flank Raider',
                'team': 'enemy',
                'armorClass': 12,
                'level': 1,
                'stats': {'strength': 10, 'dexterity': 14},
                'abilities': [
                    {
                        'id': 'shortbow',
                        'name': 'Shortbow',
                        'type': 'attack',
                        'attackBonus': 4,
                        'damage': {'dice': '1d6+2', 'type': 'piercing'},
                    }
                ],
            },
        ],
    }
    return state


def _enemy_attack_combat_context():
    return {
        'enemyIntentSummary': 'Right-Flank Raider fires from the bracken.',
        'enemyRequiredActions': [
            {
                'enemyId': 'enemy_right_flank_raider_1',
                'targetId': 'player_1',
                'intentType': 'attack',
                'abilityId': 'shortbow',
            }
        ],
        'enemyTelegraphs': ['The raider draws a bead on Legoless.'],
    }


def test_dm_context_resolves_enemy_attack_rolls_without_player_prompt():
    rolls = iter([14, 5])

    context = turn_pipeline_module._dm_context_packet(
        state=_enemy_roll_test_state(),
        player_message='I move up the slope.',
        pre_validation={'validatedActions': [], 'pendingRolls': []},
        applied_changes=[],
        combat_context=_enemy_attack_combat_context(),
        enemy_roller=lambda _sides: next(rolls),
    )

    resolved = context['combatState']['enemyResolvedActions'][0]
    assert resolved['enemyName'] == 'Right-Flank Raider'
    assert resolved['targetName'] == 'Legoless'
    assert resolved['attackRoll'] == 14
    assert resolved['attackBonus'] == 4
    assert resolved['attackTotal'] == 18
    assert resolved['targetArmorClass'] == 13
    assert resolved['hit'] is True
    assert resolved['damageRolls'] == [5]
    assert resolved['damageBonus'] == 2
    assert resolved['damageTotal'] == 7
    assert resolved['damageType'] == 'piercing'
    assert any('Enemy rolls are engine-owned' in instruction for instruction in context['dmInstructions'])
    assert any('Never ask the player to roll enemy attacks' in instruction for instruction in context['dmInstructions'])


def test_dm_context_defers_enemy_actions_when_player_roll_is_pending_or_resolved():
    def fail_if_called(_sides):
        raise AssertionError('enemy roller should not run while a player roll is being resolved')

    pending_context = turn_pipeline_module._dm_context_packet(
        state=_enemy_roll_test_state(),
        player_message='I shoot the raider.',
        pre_validation={'validatedActions': [], 'pendingRolls': [{'rollType': 'attack'}]},
        applied_changes=[],
        combat_context=_enemy_attack_combat_context(),
        enemy_roller=fail_if_called,
    )
    resolved_context = turn_pipeline_module._dm_context_packet(
        state=_enemy_roll_test_state(),
        player_message='I roll a d20+4: 13 = 17',
        pre_validation={'validatedActions': [], 'pendingRolls': []},
        applied_changes=[],
        combat_context=_enemy_attack_combat_context(),
        resolved_player_roll=True,
        enemy_roller=fail_if_called,
    )

    assert pending_context['combatState']['enemyResolvedActions'] == []
    assert pending_context['combatState']['enemyRequiredActions'] == []
    assert pending_context['combatState']['enemyActionDeferredReason'] == 'pending_player_roll'
    assert resolved_context['combatState']['enemyResolvedActions'] == []
    assert resolved_context['combatState']['enemyRequiredActions'] == []
    assert resolved_context['combatState']['enemyActionDeferredReason'] == 'player_roll_resolution'


def test_attack_roll_resolution_allows_enemy_action_on_hit_or_miss(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        pending_turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I shoot the raider.',
            dm_output='Roll Legoless longbow attack: `1d20 + 4` against AC 13.',
            requires_roll=True,
            rule_type='attack',
            outcome_status='deferred',
        )
        db.session.add(pending_turn)
        db.session.flush()
        miss_turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I roll a d20+4: 6 = 10',
            requires_roll=True,
            rule_type='attack',
            roll_value=10,
            outcome_status='resolved',
            rules_hint=safe_json_dumps(
                {
                    'roll_type': 'attack',
                    'roll_value': 10,
                    'outcome_deferred': False,
                    'resolved_turn_id': pending_turn.turn_id,
                },
                {},
            ),
        )
        hit_turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I roll a d20+4: 14 = 18',
            requires_roll=True,
            rule_type='attack',
            roll_value=18,
            outcome_status='resolved',
            rules_hint=safe_json_dumps(
                {
                    'roll_type': 'attack',
                    'roll_value': 18,
                    'outcome_deferred': False,
                    'resolved_turn_id': pending_turn.turn_id,
                },
                {},
            ),
        )

        assert turn_pipeline_module._resolved_player_roll_should_defer_enemy(miss_turn) is False
        assert turn_pipeline_module._resolved_player_roll_should_defer_enemy(hit_turn) is False


def test_enemy_attack_rolls_infer_missing_generated_ability_mechanics():
    state = _enemy_roll_test_state()
    ability = state['combat']['participants'][1]['abilities'][0]
    ability.pop('attackBonus')
    ability.pop('damage')
    ability['description'] = 'Makes one ranged weapon attack. On a hit, deals 1d6+2 piercing damage.'
    rolls = iter([14, 5])

    context = turn_pipeline_module._dm_context_packet(
        state=state,
        player_message='I wait.',
        pre_validation={'validatedActions': [], 'pendingRolls': []},
        applied_changes=[],
        combat_context=_enemy_attack_combat_context(),
        enemy_roller=lambda _sides: next(rolls),
    )

    resolved = context['combatState']['enemyResolvedActions'][0]
    assert resolved['attackBonus'] == 4
    assert resolved['attackTotal'] == 18
    assert resolved['damageDice'] == '1d6+2'
    assert resolved['damageRolls'] == [5]
    assert resolved['damageBonus'] == 2
    assert resolved['damageTotal'] == 7
    assert resolved['damageType'] == 'piercing'


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


def test_extract_equipment_actions_from_player_message(app):
    with app.app_context():
        equip_result = extract_pre_dm_actions(
            current_state={},
            player_message='I equip my greatsword.',
            recent_timeline=[],
            actor_id='player_1',
        )
        unequip_result = extract_pre_dm_actions(
            current_state={},
            player_message='I take off my iron helmet.',
            recent_timeline=[],
            actor_id='player_1',
        )

    assert equip_result['declaredActions'][0]['type'] == 'inventory.equip'
    assert equip_result['declaredActions'][0]['itemName'] == 'greatsword'
    assert unequip_result['declaredActions'][0]['type'] == 'inventory.unequip'
    assert unequip_result['declaredActions'][0]['itemName'] == 'iron helmet'


def test_item_action_intent_drop_uses_generic_resolution(app):
    with app.app_context():
        result = extract_pre_dm_actions(
            current_state={},
            player_message='I drop the wooden shield on the floor.',
            recent_timeline=[],
            actor_id='player_1',
            action_intent={
                'kind': 'item',
                'item': {'name': 'Wooden Shield', 'quantity': 1},
                'inventory_action': 'drop',
            },
        )

    action = result['declaredActions'][0]
    assert action['type'] == 'generic.intent'
    assert action['summary'] == 'Player attempts to drop Wooden Shield.'


def test_post_dm_extracts_equipment_outcomes(app):
    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm={},
            player_message='I change gear.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='You strap on the iron helmet, then stow the dagger.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=42,
        )

    changes = result['proposedChanges']
    assert [change['type'] for change in changes] == ['inventory.equip', 'inventory.unequip']
    assert changes[0]['itemName'] == 'iron helmet'
    assert changes[1]['itemName'] == 'dagger'


def test_normalize_post_extraction_preserves_nested_scene_update_changes():
    result = normalize_post_extraction(
        {
            'proposedChanges': [
                {
                    'type': 'scene.update',
                    'changes': {
                        'dangerLevel': 5,
                        'mood': 'tense',
                        'combatState': 'pending',
                        'activeNpcIds': ['ash_pale_watcher_right', 'second_pale_shape_left'],
                    },
                }
            ],
            'uncertainChanges': [],
        },
        fallback_actor_id='player_1',
    )

    change = result['proposedChanges'][0]

    assert change['dangerLevel'] == 5
    assert change['mood'] == 'tense'
    assert change['combatState'] == 'pending'
    assert change['activeNpcIds'] == ['ash_pale_watcher_right', 'second_pale_shape_left']


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


def test_validate_transfer_uses_from_actor_inventory_for_pickups():
    state = _two_player_state()
    state['playerCharacters'][1]['inventory']['items'] = [
        _item('Scimitar', item_id='starter_druid_scimitar', item_type='weapon', subtype='scimitar'),
        _item('Wooden Shield', item_id='starter_druid_wooden_shield', item_type='armor', subtype='shield'),
    ]

    result = validate_declared_actions(
        state=state,
        declared_actions=[
            {
                'id': 'act_001',
                'type': 'inventory.transfer',
                'actorId': 'player_1',
                'fromActorId': 'player_2',
                'toActorId': 'player_1',
                'itemName': 'Scimitar',
                'quantity': 1,
                'sourceText': 'I grab Horlicks dropped scimitar.',
            }
        ],
        current_turn=12,
    )

    validated = result['validatedActions'][0]
    assert validated['status'] == 'pending'
    assert validated['normalizedAction']['fromActorId'] == 'player_2'
    assert validated['normalizedAction']['itemId'] == 'starter_druid_scimitar'
    assert 'Borin can give Scimitar x1 to Kael' in validated['reason']


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


def test_apply_health_max_set_can_heal_to_new_max_and_sync_combat():
    state = _state(hp_current=7, hp_max=15)
    state['combat'] = {
        'status': 'active',
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 7, 'max': 15, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            }
        ],
    }
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'level_reward_hp',
                'type': 'health.max.set',
                'actorId': 'player_1',
                'maxHp': 21,
                'healToMax': True,
                'source': 'post_dm',
                'reason': 'Level reward.',
                'visible': True,
            }
        ],
    )

    result = apply_state_changes(state, validated_changes_for_application(validation))

    health = result['nextState']['playerCharacters'][0]['health']
    assert validation['rejected'] == []
    assert health['currentHp'] == 21
    assert health['maxHp'] == 21
    assert result['nextState']['combat']['participants'][0]['hp'] == {'current': 21, 'max': 21, 'temp': 0}
    assert result['appliedChanges'][0]['maxHpDelta'] == 6
    assert result['appliedChanges'][0]['currentHpDelta'] == 14


def test_apply_health_damage_syncs_matching_combat_participant():
    state = _state(hp_current=10, hp_max=20, temp_hp=0)
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            }
        ],
    }

    result = apply_state_changes(
        state,
        [
            {
                'id': 'chg_damage_combat_sync',
                'type': 'health.damage',
                'actorId': 'player_1',
                'amount': 4,
                'source': 'post_dm',
                'reason': 'Damage.',
                'visible': True,
            }
        ],
    )

    actor_health = result['nextState']['playerCharacters'][0]['health']
    combat_hp = result['nextState']['combat']['participants'][0]['hp']
    assert actor_health['currentHp'] == 6
    assert combat_hp == {'current': 6, 'max': 20, 'temp': 0}
    assert result['nextState']['combat']['participants'][0]['isConscious'] is True


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


def test_apply_xp_gain_auto_levels_when_threshold_is_reached():
    state = _state(xp_current=250)
    state['playerCharacters'][0]['level'] = 1
    state['combat'] = {
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'level': 1,
                'hp': {'current': 10, 'max': 20, 'temp': 0},
            }
        ]
    }
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'xp_gain_level_up',
                'type': 'xp.add',
                'actorId': 'player_1',
                'amount': 50,
                'source': 'post_dm',
                'reason': 'Quest reward.',
                'visible': True,
            }
        ],
    )

    gained = apply_state_changes(state, validated_changes_for_application(validation))
    actor = gained['nextState']['playerCharacters'][0]

    assert validation['rejected'] == []
    assert actor['xp'] == {'current': 300, 'nextLevelAt': 900}
    assert actor['level'] == 2
    assert gained['nextState']['combat']['participants'][0]['level'] == 2


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


def test_equipping_two_handed_weapon_clears_both_hands_without_touching_armor_slots():
    state = _state(
        items=[
            _item('Greatsword', item_id='greatsword_1', item_type='weapon', subtype='greatsword'),
            {**_item('Longsword', item_id='longsword_1', item_type='weapon', subtype='sword', equipped=True), 'slot': 'main_hand'},
            {**_item('Dagger', item_id='dagger_1', item_type='weapon', subtype='dagger', equipped=True), 'slot': 'off_hand'},
            {**_item('Iron Helmet', item_id='helmet_1', item_type='armor', subtype='helmet', equipped=True), 'slot': 'helmet'},
            {**_item('Travel Hood', item_id='hood_1', item_type='clothing', subtype='hood', equipped=True), 'slot': 'hood'},
        ],
    )
    validation = validate_declared_actions(
        state=state,
        declared_actions=[
            {
                'id': 'equip_greatsword',
                'type': 'inventory.equip',
                'actorId': 'player_1',
                'sourceText': 'I equip my greatsword.',
                'itemName': 'Greatsword',
            }
        ],
        current_turn=7,
    )
    result = apply_state_changes(state, validation['immediateChanges'])
    items = {item['id']: item for item in result['nextState']['playerCharacters'][0]['inventory']['items']}

    assert validation['validatedActions'][0]['status'] == 'valid'
    assert validation['immediateChanges'][0]['slot'] == 'two_hands'
    assert set(validation['immediateChanges'][0]['conflictItemIds']) == {'longsword_1', 'dagger_1'}
    assert items['greatsword_1']['equipped'] is True
    assert items['greatsword_1']['slot'] == 'two_hands'
    assert items['longsword_1']['equipped'] is False
    assert items['dagger_1']['equipped'] is False
    assert items['helmet_1']['equipped'] is True
    assert items['hood_1']['equipped'] is True


def test_equipment_slots_allow_hood_under_helmet_but_replace_same_slot():
    state = _state(
        items=[
            {**_item('Travel Hood', item_id='hood_1', item_type='clothing', subtype='hood', equipped=True), 'slot': 'hood'},
            {**_item('Iron Helmet', item_id='helmet_1', item_type='armor', subtype='helmet', equipped=True), 'slot': 'helmet'},
            _item('Steel Helmet', item_id='helmet_2', item_type='armor', subtype='helmet'),
            _item('Shortsword', item_id='shortsword_1', item_type='weapon', subtype='sword'),
            _item('Dagger', item_id='dagger_1', item_type='weapon', subtype='dagger'),
        ],
    )
    first_validation = validate_state_changes(
        state=state,
        changes=[
            {'id': 'equip_shortsword', 'type': 'inventory.equip', 'actorId': 'player_1', 'itemId': 'shortsword_1'},
        ],
    )
    first_result = apply_state_changes(state, validated_changes_for_application(first_validation))
    second_validation = validate_state_changes(
        state=first_result['nextState'],
        changes=[
            {'id': 'equip_dagger', 'type': 'inventory.equip', 'actorId': 'player_1', 'itemId': 'dagger_1'},
            {'id': 'equip_steel_helmet', 'type': 'inventory.equip', 'actorId': 'player_1', 'itemId': 'helmet_2'},
        ],
    )
    result = apply_state_changes(first_result['nextState'], validated_changes_for_application(second_validation))
    items = {item['id']: item for item in result['nextState']['playerCharacters'][0]['inventory']['items']}

    assert first_validation['rejected'] == []
    assert second_validation['rejected'] == []
    dagger_change = next(change['change'] for change in second_validation['accepted'] if change['change']['itemId'] == 'dagger_1')
    assert dagger_change['conflictItemIds'] == []
    assert items['shortsword_1']['equipped'] is True
    assert items['shortsword_1']['slot'] == 'main_hand'
    assert items['dagger_1']['equipped'] is True
    assert items['dagger_1']['slot'] == 'off_hand'
    assert items['helmet_2']['equipped'] is True
    assert items['helmet_1']['equipped'] is False
    assert items['hood_1']['equipped'] is True


def test_equipping_armor_updates_actor_and_active_combat_armor_class():
    state = _state(
        items=[
            _item('Leather Armor', item_id='leather_1', item_type='armor', subtype='light armor'),
        ],
    )
    state['playerCharacters'][0]['stats'] = {'dexterity': 15, 'armorClass': 12}
    state['combat'] = {
        'status': 'active',
        'participants': [
            {'id': 'player_1', 'playerId': 1, 'name': 'Kael', 'team': 'player', 'armorClass': 12, 'stats': {'dexterity': 15, 'armorClass': 12}},
        ],
    }
    validation = validate_state_changes(
        state=state,
        changes=[
            {'id': 'equip_leather', 'type': 'inventory.equip', 'actorId': 'player_1', 'itemId': 'leather_1'},
        ],
    )

    result = apply_state_changes(state, validated_changes_for_application(validation))
    actor = result['nextState']['playerCharacters'][0]
    participant = result['nextState']['combat']['participants'][0]

    assert validation['rejected'] == []
    assert actor['stats']['armorClass'] == 13
    assert actor['metadata']['armorClassBreakdown']['armorName'] == 'Leather Armor'
    assert participant['armorClass'] == 13
    assert participant['stats']['armorClass'] == 13


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


def test_scene_update_tracks_character_positions_and_zones():
    state = _state()
    state['currentScene'] = {'locationId': 'colosseum', 'name': 'Colosseum'}
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'scene_positions_1',
                'type': 'scene.update',
                'playerPositions': {
                    'player_1': {'zoneId': 'inside_colosseum', 'rangeBand': 'near'},
                    '2': {'zoneId': 'outside_colosseum', 'rangeBand': 'far'},
                },
                'characterZones': {'Loki': 'inside_colosseum', 'Himeros': 'outside_colosseum'},
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))
    scene = result['nextState']['currentScene']

    assert validation['rejected'] == []
    assert scene['playerPositions']['player_1']['zoneId'] == 'inside_colosseum'
    assert scene['playerPositions']['2']['zoneId'] == 'outside_colosseum'
    assert scene['characterZones']['Loki'] == 'inside_colosseum'
    assert scene['characterZones']['Himeros'] == 'outside_colosseum'


def test_scene_item_add_and_remove_updates_current_scene_items():
    state = _state()
    state['currentScene'] = {'locationId': 'road', 'name': 'Old Road'}
    add_validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'scene_add_shield',
                'type': 'scene.item.add',
                'itemName': 'Wooden Shield',
                'quantity': 1,
                'sourceActorId': 'player_2',
                'item': {'id': 'starter_druid_wooden_shield', 'name': 'Wooden Shield', 'quantity': 1, 'type': 'armor'},
                'visible': True,
            }
        ],
    )
    added = apply_state_changes(state, validated_changes_for_application(add_validation))

    scene_items = added['nextState']['currentScene']['items']
    assert add_validation['rejected'] == []
    assert scene_items == [
        {
            'id': 'starter_druid_wooden_shield',
            'name': 'Wooden Shield',
            'quantity': 1,
            'type': 'armor',
            'sourceActorId': 'player_2',
        }
    ]

    remove_validation = validate_state_changes(
        state=added['nextState'],
        changes=[
            {
                'id': 'scene_remove_shield',
                'type': 'scene.item.remove',
                'itemName': 'Wooden Shield',
                'quantity': 1,
                'visible': True,
            }
        ],
    )
    removed = apply_state_changes(added['nextState'], validated_changes_for_application(remove_validation))

    assert remove_validation['rejected'] == []
    assert removed['nextState']['currentScene']['items'] == []


def test_compact_state_preserves_current_scene_items():
    state = _state()
    state['currentScene'] = {
        'locationId': 'old_road',
        'name': 'Old Road',
        'items': [
            {
                'id': 'starter_druid_wooden_shield',
                'name': 'Wooden Shield',
                'quantity': 1,
                'type': 'armor',
                'sourceActorId': 'player_2',
                'privateNote': 'should not leak',
            }
        ],
    }

    compact = compact_state_for_extraction(state)

    assert compact['currentScene']['items'] == [
        {
            'id': 'starter_druid_wooden_shield',
            'name': 'Wooden Shield',
            'quantity': 1,
            'type': 'armor',
            'subtype': None,
            'sourceActorId': 'player_2',
        }
    ]


def test_scene_update_ignores_sentence_length_location_but_keeps_mood():
    state = _state()
    state['currentScene'] = {'locationId': 'blackwake_tavern', 'name': 'Blackwake Tavern', 'mood': 'tense'}
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'scene_sentence_name',
                'type': 'scene.update',
                'name': 'Inside the tavern, Vesra has just accepted Lin offer and the patrons are watching with curiosity.',
                'mood': 'calm',
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))

    scene = result['nextState']['currentScene']
    assert validation['rejected'] == []
    assert scene['locationId'] == 'blackwake_tavern'
    assert scene['name'] == 'Blackwake Tavern'
    assert scene['mood'] == 'calm'


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


def test_scene_move_location_rejects_sentence_length_location_names():
    state = _state()
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'bad_scene_location',
                'type': 'scene.move_location',
                'name': 'The stone archway groans while the whole hall fills with cold blue fire',
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['accepted'] == []
    assert validation['rejected'][0]['reason'] == 'Scene movement location must be a short place name.'
    assert result['nextState'].get('locations') in (None, [])


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


def test_missing_location_update_is_applied_as_discovery():
    state = _state()
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'update_rensira',
                'type': 'location.update',
                'source': 'post_dm',
                'turnId': 24,
                'locationId': 'rensira',
                'name': 'Rensira',
                'locationType': 'town',
                'status': 'visited',
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['rejected'] == []
    assert validation['accepted'][0]['change']['type'] == 'location.discover'
    assert result['nextState']['locations'][0]['id'] == 'rensira'


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


def test_quest_objective_update_accepts_top_level_open_status():
    state = _state()
    state['quests'] = [
        {
            'id': 'rensira_threads',
            'title': 'Rensira Threads',
            'status': 'active',
            'objectives': [
                {
                    'id': 'find_the_source',
                    'description': 'Find the source of the black threads.',
                    'status': 'completed',
                }
            ],
        }
    ]
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'reopen_source_objective',
                'type': 'quest.objective.update',
                'source': 'post_dm',
                'turnId': 26,
                'questId': 'rensira_threads',
                'objectiveId': 'find_the_source',
                'description': 'Find the source of the black threads.',
                'status': 'open',
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['rejected'] == []
    assert result['nextState']['quests'][0]['objectives'][0]['status'] == 'open'


def test_quest_complete_marks_completed_and_does_not_recomplete_on_retry():
    state = _state()
    state['quests'] = [{'id': 'find_missing_sailor', 'title': 'Find the Missing Sailor', 'status': 'active'}]
    state['currentScene'] = {
        'locationId': 'old_harbor',
        'name': 'Old Harbor',
        'activeQuestIds': ['find_missing_sailor', 'find_smuggler_cache'],
    }
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
    assert first['nextState']['quests'][0]['id'] == 'find_missing_sailor'
    assert first['nextState']['currentScene']['activeQuestIds'] == ['find_smuggler_cache']
    assert retry['appliedChanges'] == []
    assert retry['nextState']['quests'][0]['completedAtTurn'] == 26
    assert retry['nextState']['quests'][0]['id'] == 'find_missing_sailor'
    assert retry['nextState']['currentScene']['activeQuestIds'] == ['find_smuggler_cache']


def test_quest_fail_marks_failed_and_removes_from_active_scene_on_retry():
    state = _state()
    state['quests'] = [{'id': 'find_missing_sailor', 'title': 'Find the Missing Sailor', 'status': 'active'}]
    state['currentScene'] = {
        'locationId': 'old_harbor',
        'name': 'Old Harbor',
        'activeQuestIds': ['find_missing_sailor', 'find_smuggler_cache'],
    }
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'fail_missing_sailor',
                'type': 'quest.fail',
                'source': 'post_dm',
                'reason': 'The DM clearly confirms failure.',
                'turnId': 27,
                'questId': 'find_missing_sailor',
            }
        ],
    )
    first = apply_state_changes(state, validated_changes_for_application(validation))
    retry = apply_state_changes(first['nextState'], validated_changes_for_application(validation))

    assert validation['rejected'] == []
    assert first['nextState']['quests'][0]['status'] == 'failed'
    assert first['nextState']['quests'][0]['id'] == 'find_missing_sailor'
    assert first['nextState']['currentScene']['activeQuestIds'] == ['find_smuggler_cache']
    assert retry['appliedChanges'] == []
    assert retry['nextState']['quests'][0]['status'] == 'failed'
    assert retry['nextState']['quests'][0]['id'] == 'find_missing_sailor'
    assert retry['nextState']['currentScene']['activeQuestIds'] == ['find_smuggler_cache']


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


def test_npc_update_normalizes_condition_like_status_and_disposition_aliases():
    state = _state()
    state['knownNpcs'] = [
        {
            'id': 'grey_wool_stranger',
            'name': 'Grey Wool Stranger',
            'status': 'met',
            'disposition': 'suspicious',
            'memory': [],
            'metadata': {},
        }
    ]
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'stranger_dying_hopeful',
                'type': 'npc.update',
                'source': 'post_dm',
                'turnId': 28,
                'npcId': 'grey_wool_stranger',
                'status': 'dying',
                'disposition': 'hopeful',
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))
    npc = result['nextState']['knownNpcs'][0]

    assert validation['rejected'] == []
    assert npc['status'] == 'known'
    assert npc['disposition'] == 'friendly'
    assert npc['metadata']['extractedStatus'] == 'dying'
    assert npc['metadata']['extractedDisposition'] == 'hopeful'
    assert 'Status note: dying.' in npc['memory']


def test_noncombat_condition_for_known_npc_becomes_npc_update():
    state = _state()
    state['knownNpcs'] = [
        {
            'id': 'grey_wool_stranger',
            'name': 'Grey Wool Stranger',
            'status': 'met',
            'disposition': 'hostile',
            'memory': [],
            'metadata': {},
        }
    ]
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'poison_stranger_noncombat',
                'type': 'combat.condition.add',
                'source': 'post_dm',
                'turnId': 29,
                'participantId': 'grey_wool_stranger',
                'condition': 'poisoned',
            }
        ],
    )
    result = apply_state_changes(state, validated_changes_for_application(validation))
    npc = result['nextState']['knownNpcs'][0]

    assert validation['rejected'] == []
    assert validation['accepted'][0]['change']['type'] == 'npc.update'
    assert npc['metadata']['sourceCombatCondition'] == 'poisoned'
    assert 'Condition added: poisoned.' in npc['memory']


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


def test_post_dm_extracts_explicit_max_hp_reward(app):
    state = _state(hp_current=7, hp_max=15)
    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I accept the level reward.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='Koryl reaches level 3. His max HP becomes 21 and he receives a full heal.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=10,
        )

    max_hp_change = next(change for change in result['proposedChanges'] if change['type'] == 'health.max.set')
    assert max_hp_change['maxHp'] == 21
    assert max_hp_change['currentHp'] == 21
    assert max_hp_change['healToMax'] is True


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


def test_post_dm_helper_dropped_equipment_becomes_scene_item_and_filters_noncombat_ability(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text=(
                    '{"proposedChanges":['
                    '{"type":"inventory.unequip","actorId":"player_1","itemName":"Wooden Shield"},'
                    '{"type":"combat.ability.mark_used","participantId":"player_1","abilityName":"Primal Shift"}'
                    '],"uncertainChanges":[],"notes":["helper saw shapeshift drop"]}'
                ),
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())
    state = _state(items=[_item('Wooden Shield', item_id='starter_druid_wooden_shield', item_type='armor', subtype='shield', equipped=True)])
    state['currentScene'] = {'locationId': 'old_road', 'name': 'Old Road'}
    state['combat'] = {'status': 'none', 'participants': []}

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I turn into a bird.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The Wooden Shield drops from your arm and clatters onto the stone floor.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=545,
        )

    change_types = [change['type'] for change in result['proposedChanges']]
    assert 'inventory.unequip' in change_types
    assert 'inventory.remove' in change_types
    assert 'scene.item.add' in change_types
    assert 'combat.ability.mark_used' not in change_types
    scene_item = next(change for change in result['proposedChanges'] if change['type'] == 'scene.item.add')
    assert scene_item['itemName'] == 'Wooden Shield'
    assert scene_item['sourceActorId'] == 'player_1'
    assert 'scene_item_grounding' in result['notes']
    assert 'filtered_noncombat_ability' in result['notes']


def test_post_dm_scene_item_pickup_adds_inventory_and_removes_scene_item(app):
    state = _state()
    state['currentScene'] = {
        'locationId': 'old_road',
        'name': 'Old Road',
        'items': [
            {'id': 'starter_druid_wooden_shield', 'name': 'Wooden Shield', 'quantity': 1, 'type': 'armor', 'sourceActorId': 'player_2'}
        ],
    }

    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I grab the shield.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='Kael grabs the Wooden Shield from the floor and carries it under one arm.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=546,
        )

    assert any(change['type'] == 'scene.item.remove' and change.get('itemName') == 'Wooden Shield' for change in result['proposedChanges'])
    assert any(change['type'] == 'inventory.add' and change.get('itemName') == 'Wooden Shield' for change in result['proposedChanges'])


def test_post_dm_helper_does_not_learn_spell_for_transform_only_form_use(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text='{"proposedChanges":[{"type":"spell.learn","actorId":"player_1","spellName":"Finch Form"}],"uncertainChanges":[],"notes":[]}',
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm=_state(),
            player_message='I turn into a little finch.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='Your form ripples and transforms into a little finch.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=547,
        )

    assert not any(change['type'] == 'spell.learn' for change in result['proposedChanges'])
    form_change = next(change for change in result['proposedChanges'] if change['type'] == 'flag.set')
    assert form_change['flagKey'] == 'player_1_current_form'
    assert form_change['flagValue'] == 'little finch'
    assert 'filtered_transform_only_spell_learn' in result['notes']
    assert 'heuristic_form_state' in result['notes']


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


def test_post_dm_marks_mentioned_known_npcs_active(app):
    state = _state()
    state['currentScene'] = {
        'locationId': 'colosseum',
        'name': 'Colosseum',
        'sceneType': 'social',
        'activeNpcIds': [],
    }
    state['knownNpcs'] = [
        {
            'id': 'mirror_trickster',
            'name': 'Mirror Trickster',
            'locationId': 'colosseum',
            'status': 'met',
        },
        {
            'id': 'distant_judge',
            'name': 'Distant Judge',
            'locationId': 'upper_gallery',
            'status': 'known',
        },
    ]

    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I ask the Trickster why it attacked.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The Mirror Trickster lowers its shard and answers in a hoarse whisper.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=16,
        )

    scene_change = next(
        change for change in result['proposedChanges']
        if change['type'] == 'scene.update' and change.get('activeNpcIds')
    )
    assert scene_change['activeNpcIds'] == ['mirror_trickster']
    assert 'heuristic_active_npcs' in result['notes']


def test_active_npc_heuristic_keeps_newly_discovered_hostile_visible():
    state = _state()
    state['currentScene'] = {
        'locationId': 'dead_shelter',
        'name': 'Dead Shelter',
        'sceneType': 'combat',
        'activeNpcIds': ['captor_1', 'captor_2', 'captive_human'],
    }
    state['knownNpcs'] = [
        {'id': 'captor_1', 'name': 'Captor 1', 'status': 'known', 'disposition': 'hostile'},
        {'id': 'captor_2', 'name': 'Captor 2', 'status': 'known', 'disposition': 'hostile'},
        {'id': 'captive_human', 'name': 'Human Captive', 'status': 'met', 'disposition': 'friendly'},
    ]

    changes = post_extractor_module._heuristic_active_npc_changes(
        state_before_dm=state,
        dm_response='A third captor shifts inside the dead shelter, still visible through the torn hide flap.',
        turn_id=17,
        already_applied_changes=[],
        proposed_changes=[
            {
                'type': 'npc.discover',
                'npcId': 'captor_3',
                'name': 'Captor 3',
                'status': 'known',
                'disposition': 'hostile',
            }
        ],
    )

    scene_change = next(change for change in changes if change['type'] == 'scene.update')
    assert scene_change['activeNpcIds'] == ['captor_1', 'captor_2', 'captive_human', 'captor_3']


def test_bound_combat_creature_updates_known_npc_after_defeat():
    state = _state()
    state['knownNpcs'] = [
        {
            'id': 'captor_3',
            'name': 'Captor 3',
            'status': 'known',
            'disposition': 'hostile',
            'memory': [],
            'metadata': {},
        }
    ]
    state['combat'] = {
        'status': 'active',
        'participants': [
            {
                'id': 'enemy_shelter_lurker_1',
                'team': 'enemy',
                'name': 'Shelter Lurker (Captor 3)',
                'creatureTypeName': 'Shelter Lurker',
                'npcBinding': {
                    'npcId': 'captor_3',
                    'npcName': 'Captor 3',
                    'creatureTypeName': 'Shelter Lurker',
                },
                'hp': {'current': 4, 'max': 4, 'temp': 0},
                'conditions': [],
                'isAlive': True,
            }
        ],
    }

    changes = post_extractor_module._bound_npc_updates_from_combat_changes(
        state_before_dm=state,
        changes=[
            {
                'type': 'combat.participant.update',
                'participantId': 'enemy_shelter_lurker_1',
                'conditions': ['defeated'],
                'isAlive': False,
            }
        ],
        turn_id=18,
        already_applied_changes=[],
    )
    validation = validate_state_changes(state=state, changes=changes)
    result = apply_state_changes(state, validated_changes_for_application(validation))
    npc = result['nextState']['knownNpcs'][0]

    assert validation['rejected'] == []
    assert changes[0]['npcId'] == 'captor_3'
    assert changes[0]['status'] == 'dead'
    assert changes[0]['metadata']['creatureTypeName'] == 'Shelter Lurker'
    assert npc['status'] == 'dead'
    assert 'entered combat as Shelter Lurker' in npc['memory'][-1]

    fled_changes = post_extractor_module._bound_npc_updates_from_combat_changes(
        state_before_dm=state,
        changes=[
            {
                'type': 'combat.participant.update',
                'participantId': 'enemy_shelter_lurker_1',
                'conditions': ['fled'],
                'isAlive': False,
            }
        ],
        turn_id=20,
        already_applied_changes=[],
    )
    assert fled_changes[0]['status'] == 'fleeing'
    assert fled_changes[0]['metadata']['combatOutcome'] == 'fleeing'


def test_npc_update_by_id_preserves_display_name_and_merges_aliases():
    state = _state()
    state['knownNpcs'] = [
        {
            'id': 'ash_pale_watcher_right',
            'name': 'Ash-pale watcher (right slope)',
            'status': 'met',
            'disposition': 'hostile',
            'aliases': ['right watcher'],
            'memory': [],
            'metadata': {},
        }
    ]
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'update_bound_npc',
                'type': 'npc.update',
                'npcId': 'ash_pale_watcher_right',
                'status': 'dead',
                'aliases': ['ash-pale watcher'],
            }
        ],
    )

    result = apply_state_changes(state, validated_changes_for_application(validation))
    npc = result['nextState']['knownNpcs'][0]

    assert validation['rejected'] == []
    assert npc['name'] == 'Ash-pale watcher (right slope)'
    assert npc['status'] == 'dead'
    assert npc['aliases'] == ['right watcher', 'ash-pale watcher']


def test_fleeing_group_marks_remaining_hostile_npcs_and_removes_offscreen_active_ids():
    state = _state()
    state['currentScene'] = {
        'locationId': 'dead_shelter',
        'name': 'Dead Shelter',
        'sceneType': 'combat',
        'activeNpcIds': ['captor_1', 'captor_2', 'captive_human'],
    }
    state['knownNpcs'] = [
        {'id': 'captor_1', 'name': 'Captor 1', 'status': 'known', 'disposition': 'hostile', 'memory': [], 'metadata': {}},
        {'id': 'captor_2', 'name': 'Captor 2', 'status': 'known', 'disposition': 'hostile', 'memory': [], 'metadata': {}},
        {'id': 'captor_3', 'name': 'Captor 3', 'status': 'known', 'disposition': 'hostile', 'memory': [], 'metadata': {}},
        {'id': 'captive_human', 'name': 'Human Captive', 'status': 'met', 'disposition': 'friendly'},
    ]

    changes = post_extractor_module._heuristic_fleeing_npc_changes(
        state_before_dm=state,
        dm_response='The other two captors are no longer in sight; their tracks cut west-northwest through the thorn break.',
        turn_id=19,
        already_applied_changes=[],
        proposed_changes=[{'type': 'npc.update', 'npcId': 'captor_3', 'status': 'dead'}],
    )
    validation = validate_state_changes(state=state, changes=changes)
    result = apply_state_changes(state, validated_changes_for_application(validation))
    by_id = {npc['id']: npc for npc in result['nextState']['knownNpcs']}
    scene = result['nextState']['currentScene']

    assert validation['rejected'] == []
    assert [change['npcId'] for change in changes if change['type'] == 'npc.update'] == ['captor_1', 'captor_2']
    assert by_id['captor_1']['status'] == 'fleeing'
    assert by_id['captor_2']['status'] == 'fleeing'
    assert by_id['captor_1']['metadata']['lastKnownDirection'].startswith('west-northwest')
    assert scene['activeNpcIds'] == ['captive_human']


def test_post_dm_extracts_enemy_combat_damage_and_conditions(app):
    state = _state()
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_wolf_1',
                'team': 'enemy',
                'name': 'Wolf',
                'kind': 'creature',
                'hp': {'current': 11, 'max': 11, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 50,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'forest'},
        'flags': {},
    }

    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I slash the wolf and try to scare it back.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='Your strike deals 5 damage to the Wolf. The Wolf is frightened.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=16,
        )

    damage_change = next(
        change
        for change in result['proposedChanges']
        if change['type'] == 'combat.participant.update' and change.get('participantId') == 'enemy_wolf_1'
    )
    condition_change = next(change for change in result['proposedChanges'] if change['type'] == 'combat.condition.add')
    assert damage_change['hp']['current'] == 6
    assert damage_change['hp']['max'] == 11
    assert damage_change['isAlive'] is True
    assert condition_change['participantId'] == 'enemy_wolf_1'
    assert condition_change['condition'] == 'frightened'
    assert 'heuristic_combat_outcomes' in result['notes']


def test_post_dm_filters_helper_enemy_health_damage_and_updates_combat_participant(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text=(
                    '{"proposedChanges":[{"type":"health.damage","participantId":"enemy_wolf_1",'
                    '"participantName":"Wolf","amount":1}],'
                    '"uncertainChanges":[],"notes":["enemy damage extracted by helper"]}'
                ),
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())
    state = _state(hp_current=10, hp_max=20)
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_wolf_1',
                'team': 'enemy',
                'name': 'Wolf',
                'kind': 'creature',
                'hp': {'current': 11, 'max': 11, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 50,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'forest'},
        'flags': {},
    }

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I punch the wolf.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='Kael deals 1 bludgeoning damage to the Wolf (HP 11 to 10).',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=18,
        )

    assert all(change['type'] != 'health.damage' for change in result['proposedChanges'])
    damage_change = next(change for change in result['proposedChanges'] if change['type'] == 'combat.participant.update')
    assert damage_change['participantId'] == 'enemy_wolf_1'
    assert damage_change['hp']['current'] == 10
    assert 'filtered_misrouted_combat_health' in result['notes']
    validation = validate_state_changes(state=state, changes=result['proposedChanges'])
    applied = apply_state_changes(state, validated_changes_for_application(validation))
    player = applied['nextState']['playerCharacters'][0]
    enemy = next(participant for participant in applied['nextState']['combat']['participants'] if participant['id'] == 'enemy_wolf_1')
    assert not validation['rejected']
    assert player['health']['currentHp'] == 10
    assert enemy['hp']['current'] == 10


def test_post_dm_combat_heuristic_does_not_end_when_enemy_does_not_fall(app):
    state = _state(hp_current=10, hp_max=20)
    state['combat'] = {
        'status': 'active',
        'round': 2,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_shelter_lurker_1',
                'team': 'enemy',
                'name': 'Shelter Lurker',
                'kind': 'creature',
                'xpReward': 25,
                'hp': {'current': 9, 'max': 9, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 50,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'forest'},
        'flags': {},
    }

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = False
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I roll damage.',
            validated_actions={},
            already_applied_changes=[],
            dm_response=(
                'Shelter Lurker takes 4 piercing damage. It does not fall. Instead, it tears itself '
                'sideways with a wet snarl and lunges at Kael with a scrap-spear. Kael, roll Dexterity '
                'saving throw: d20 + 2 against DC 12.'
            ),
            recent_timeline=[],
            actor_id='player_1',
            turn_id=118,
        )

    enemy_updates = [
        change for change in result['proposedChanges']
        if change['type'] == 'combat.participant.update' and change.get('participantId') == 'enemy_shelter_lurker_1'
    ]
    assert any((change.get('hp') or {}).get('current') == 5 for change in enemy_updates)
    assert not any((change.get('hp') or {}).get('current') == 0 for change in enemy_updates)
    assert not any(change['type'] == 'combat.end' for change in result['proposedChanges'])
    assert not any(change['type'] == 'xp.add' for change in result['proposedChanges'])


def test_post_dm_combat_heuristic_does_not_defeat_enemy_when_player_drops_knee(app):
    state = _state(hp_current=10, hp_max=20)
    state['combat'] = {
        'status': 'active',
        'round': 3,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_arrowmark_scout_1',
                'team': 'enemy',
                'name': 'Hollow Arrowmark Scout',
                'kind': 'creature',
                'xpReward': 50,
                'hp': {'current': 7, 'max': 10, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 50,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'forest'},
        'flags': {},
    }

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = False
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I roll a save.',
            validated_actions={},
            already_applied_changes=[],
            dm_response=(
                "The Hollow Arrowmark Scout's arrow snaps down through the thorn tangle and punches "
                "into Kael's side, driving the breath from him as he drops one knee into the wet mud. "
                'Kael takes 4 piercing damage. The Hollow Arrowmark Scout is still alive, wounded, '
                'and moving in cover.'
            ),
            recent_timeline=[],
            actor_id='player_1',
            turn_id=119,
        )

    assert any(change['type'] == 'health.damage' and change.get('amount') == 4 for change in result['proposedChanges'])
    enemy_updates = [
        change for change in result['proposedChanges']
        if change['type'] == 'combat.participant.update' and change.get('participantId') == 'enemy_arrowmark_scout_1'
    ]
    assert not any((change.get('hp') or {}).get('current') == 0 for change in enemy_updates)
    assert not any(change['type'] == 'combat.end' for change in result['proposedChanges'])
    assert not any(change['type'] == 'xp.add' for change in result['proposedChanges'])


def test_single_enemy_combat_participant_update_resolves_unknown_npc_alias():
    state = _state(hp_current=10, hp_max=20)
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_thunderer_1',
                'team': 'enemy',
                'name': 'The Thunderer',
                'kind': 'creature',
                'hp': {'current': 20, 'max': 20, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 80,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'custom'},
        'flags': {},
    }

    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'thor_bloodied',
                'type': 'combat.participant.update',
                'participantId': 'thor',
                'conditions': ['bloodied'],
            }
        ],
    )

    assert not validation['rejected']
    assert validation['accepted'][0]['change']['participantId'] == 'enemy_thunderer_1'


def test_player_combat_participant_hp_update_syncs_actor_health():
    state = _state(hp_current=17, hp_max=17)
    state['combat'] = {
        'status': 'active',
        'round': 3,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'kind': 'player_character',
                'hp': {'current': 17, 'max': 17, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'melee'},
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_arena_champion_1',
                'team': 'enemy',
                'name': 'Arena Champion',
                'kind': 'creature',
                'hp': {'current': 20, 'max': 20, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'melee'},
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {},
        'flags': {},
    }
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'goliath_takes_8',
                'type': 'combat.participant.update',
                'participantId': 'player_1',
                'hp': {'current': 9, 'max': 17},
                'conditions': ['stunned'],
            }
        ],
    )

    applied = apply_state_changes(state, validated_changes_for_application(validation))

    actor = applied['nextState']['playerCharacters'][0]
    participant = applied['nextState']['combat']['participants'][0]
    assert not validation['rejected']
    assert actor['health']['currentHp'] == 9
    assert actor['health']['maxHp'] == 17
    assert actor['health']['conditions'] == ['stunned']
    assert participant['hp']['current'] == 9
    assert participant['conditions'] == ['stunned']


def test_post_dm_filters_helper_enemy_actor_health_damage_at_combat_end(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text=(
                    '{"proposedChanges":[{"type":"health.damage","actorId":"enemy_wolf_1","amount":11}],'
                    '"uncertainChanges":[],"notes":["enemy killed"]}'
                ),
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())
    state = _state()
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_wolf_1',
                'team': 'enemy',
                'name': 'Wolf',
                'kind': 'creature',
                'hp': {'current': 11, 'max': 11, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 50,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'forest'},
        'flags': {},
    }

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I finish the wolf.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The Wolf is slain. The fight is over.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=19,
        )

    assert all(change['type'] != 'health.damage' for change in result['proposedChanges'])
    assert any(change['type'] == 'combat.participant.update' and change['hp']['current'] == 0 for change in result['proposedChanges'])
    assert any(change['type'] == 'combat.end' for change in result['proposedChanges'])
    validation = validate_state_changes(state=state, changes=result['proposedChanges'])
    assert not validation['rejected']


def test_post_dm_awards_enemy_defeat_xp_from_combat_participant(app):
    state = _state(xp_current=0)
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_wolf_1',
                'team': 'enemy',
                'name': 'Wolf',
                'kind': 'creature',
                'level': 1,
                'challengeTier': 'easy',
                'xpReward': 35,
                'hp': {'current': 11, 'max': 11, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 50,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'forest'},
        'flags': {},
    }

    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I finish the wolf.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The Wolf is slain. The fight is over.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=119,
        )

    xp_changes = [change for change in result['proposedChanges'] if change['type'] == 'xp.add']
    assert len(xp_changes) == 1
    assert xp_changes[0]['amount'] == 35
    assert xp_changes[0]['actorId'] == 'player_1'
    assert 'automatic_xp_award' in result['notes']

    validation = validate_state_changes(state=state, changes=result['proposedChanges'])
    applied = apply_state_changes(state, validated_changes_for_application(validation))
    assert validation['rejected'] == []
    assert applied['nextState']['playerCharacters'][0]['xp']['current'] == 35


def test_post_dm_awards_combat_xp_to_all_player_participants(app):
    state = _two_player_state()
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'player_2',
                'team': 'player',
                'name': 'Borin',
                'hp': {'current': 12, 'max': 12, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_serpent_1',
                'team': 'enemy',
                'name': 'Sea Serpent',
                'kind': 'creature',
                'challengeTier': 'hard',
                'xpReward': 125,
                'hp': {'current': 18, 'max': 18, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 50,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'coast'},
        'flags': {},
    }

    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='We finish the serpent.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The Sea Serpent is slain. The fight is over.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=122,
        )

    xp_changes = [change for change in result['proposedChanges'] if change['type'] == 'xp.add']
    assert {change['actorId'] for change in xp_changes} == {'player_1', 'player_2'}
    assert all(change['amount'] == 125 for change in xp_changes)

    validation = validate_state_changes(state=state, changes=result['proposedChanges'])
    applied = apply_state_changes(state, validated_changes_for_application(validation))
    assert validation['rejected'] == []
    assert [actor['xp']['current'] for actor in applied['nextState']['playerCharacters']] == [125, 125]


def test_post_dm_combat_xp_uses_turn_control_participants_when_present(app):
    state = _two_player_state()
    state['turnControl'] = {'participantPlayerIds': [1], 'activePlayerId': 1}
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'player_2',
                'team': 'player',
                'name': 'Borin',
                'hp': {'current': 12, 'max': 12, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_serpent_1',
                'team': 'enemy',
                'name': 'Sea Serpent',
                'kind': 'creature',
                'challengeTier': 'hard',
                'xpReward': 125,
                'hp': {'current': 18, 'max': 18, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 50,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'coast'},
        'flags': {},
    }

    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I finish the serpent.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The Sea Serpent is slain. The fight is over.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=123,
        )

    xp_changes = [change for change in result['proposedChanges'] if change['type'] == 'xp.add']
    assert len(xp_changes) == 1
    assert xp_changes[0]['actorId'] == 'player_1'
    assert xp_changes[0]['amount'] == 125


def test_post_dm_combat_xp_prefers_active_session_players(app):
    state = _two_player_state()
    state['activePlayerIds'] = [1, 2]
    state['turnControl'] = {'participantPlayerIds': [1], 'activePlayerId': 1}
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'player_2',
                'team': 'player',
                'name': 'Borin',
                'hp': {'current': 12, 'max': 12, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_serpent_1',
                'team': 'enemy',
                'name': 'Sea Serpent',
                'kind': 'creature',
                'challengeTier': 'hard',
                'xpReward': 125,
                'hp': {'current': 18, 'max': 18, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 50,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'coast'},
        'flags': {},
    }

    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I finish the serpent.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The Sea Serpent is slain. The fight is over.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=126,
        )

    xp_changes = [change for change in result['proposedChanges'] if change['type'] == 'xp.add']
    assert {change['actorId'] for change in xp_changes} == {'player_1', 'player_2'}
    assert all(change['amount'] == 125 for change in xp_changes)


def test_post_dm_awards_npc_defeat_xp_in_combat_context(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text=(
                    '{"proposedChanges":[{"type":"npc.update","npcId":"unknown_segmented_creature",'
                    '"status":"dead"}],"uncertainChanges":[]}'
                ),
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())
    state = _two_player_state()
    state['currentScene'] = {'sceneType': 'combat', 'combatState': 'resolved', 'dangerLevel': 1}
    state['knownNpcs'] = [
        {
            'id': 'unknown_segmented_creature',
            'name': 'Unknown Segmented Creature',
            'description': 'A massive segmented horror the size of a small whale.',
            'status': 'known',
            'disposition': 'unknown',
        }
    ]

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='We drag the creature ashore.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The massive creature is dead on the dock.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=124,
        )

    xp_changes = [change for change in result['proposedChanges'] if change['type'] == 'xp.add']
    assert {change['actorId'] for change in xp_changes} == {'player_1', 'player_2'}
    assert all(change['amount'] == 100 for change in xp_changes)
    assert 'automatic_xp_award' in result['notes']


def test_post_dm_does_not_award_npc_death_xp_for_friendly_npc(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text='{"proposedChanges":[{"type":"npc.update","npcId":"old_mentor","status":"dead"}],"uncertainChanges":[]}',
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())
    state = _state()
    state['currentScene'] = {'sceneType': 'combat', 'combatState': 'resolved', 'dangerLevel': 1}
    state['knownNpcs'] = [
        {
            'id': 'old_mentor',
            'name': 'Old Mentor',
            'description': 'A friendly teacher.',
            'status': 'met',
            'disposition': 'friendly',
        }
    ]

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='The mentor dies.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The old mentor dies protecting you.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=125,
        )

    assert not any(change['type'] == 'xp.add' for change in result['proposedChanges'])


def test_post_dm_does_not_double_award_combat_xp_when_dm_states_xp(app):
    state = _state(xp_current=0)
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_wolf_1',
                'team': 'enemy',
                'name': 'Wolf',
                'kind': 'creature',
                'challengeTier': 'easy',
                'xpReward': 35,
                'hp': {'current': 11, 'max': 11, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 50,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'forest'},
        'flags': {},
    }

    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I finish the wolf.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The Wolf is slain. The fight is over. You gain 35 XP.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=120,
        )

    xp_changes = [change for change in result['proposedChanges'] if change['type'] == 'xp.add']
    assert len(xp_changes) == 1
    assert xp_changes[0]['amount'] == 35
    assert 'automatic_xp_award' not in result['notes']


def test_post_dm_helper_quest_complete_awards_quest_xp(app, monkeypatch):
    class FakeProvider:
        def generate(self, _request):
            return ProviderResponse(
                text=(
                    '{"proposedChanges":[{"type":"quest.complete","questId":"find_missing_sailor",'
                    '"reason":"The missing sailor is rescued."}],"uncertainChanges":[]}'
                ),
                provider='fake',
                model='fake-helper',
            )

    monkeypatch.setattr(post_extractor_module, 'get_helper_provider', lambda: FakeProvider())
    state = _state(xp_current=0)
    state['quests'] = [
        {
            'id': 'find_missing_sailor',
            'title': 'Find the Missing Sailor',
            'status': 'active',
            'xpReward': 80,
        }
    ]

    with app.app_context():
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I bring the sailor home.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The missing sailor is safe, and the job is done.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=121,
        )

    xp_change = next(change for change in result['proposedChanges'] if change['type'] == 'xp.add')
    assert xp_change['amount'] == 80
    assert xp_change['actorId'] == 'player_1'
    assert 'automatic_xp_award' in result['notes']

    validation = validate_state_changes(state=state, changes=result['proposedChanges'])
    applied = apply_state_changes(state, validated_changes_for_application(validation))
    assert validation['rejected'] == []
    assert applied['nextState']['quests'][0]['status'] == 'completed'
    assert applied['nextState']['playerCharacters'][0]['xp']['current'] == 80


def test_post_dm_heuristic_spell_learn_updates_spellbook(app):
    state = _state()

    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I study the old tome.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The old tome teaches you Misty Step.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=127,
        )

    spell_changes = [change for change in result['proposedChanges'] if change['type'] == 'spell.learn']
    assert len(spell_changes) == 1
    assert spell_changes[0]['spellName'] == 'Misty Step'

    validation = validate_state_changes(state=state, changes=result['proposedChanges'])
    applied = apply_state_changes(state, validated_changes_for_application(validation))
    known = {
        spell['name']
        for spell in applied['nextState']['playerCharacters'][0]['spellbook']['knownSpells']
    }
    assert validation['rejected'] == []
    assert 'Misty Step' in known


def test_spell_learn_persists_to_player_character_sheet(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        session_obj = db.session.get(Session, ids['session_id'])
        player.character_sheet = safe_json_dumps({'current_hp': 12}, {})
        db.session.commit()

        actor_id = display_actor_id(player.player_id)
        state = {
            'sessionId': session_obj.session_id,
            'campaignId': ids['campaign_id'],
            'playerCharacters': [
                {
                    'id': actor_id,
                    'playerId': player.player_id,
                    'name': player.character_name,
                    'health': {'currentHp': 12, 'maxHp': 12, 'tempHp': 0, 'conditions': []},
                    'inventory': {'items': [], 'currency': {'pp': 0, 'gp': 0, 'ep': 0, 'sp': 0, 'cp': 0}},
                    'xp': {'current': 0},
                    'metadata': {},
                }
            ],
            'stateChangeLedger': [],
        }
        changes = [
            {
                'id': 'learn_misty_step',
                'type': 'spell.learn',
                'source': 'post_dm',
                'actorId': actor_id,
                'spellName': 'Misty Step',
                'spellLevel': 2,
                'visible': True,
            }
        ]
        validation = validate_state_changes(state=state, changes=changes)
        applied = apply_state_changes(state, validated_changes_for_application(validation))
        persist_state_to_database(
            session_obj=session_obj,
            state=applied['nextState'],
            players_by_id={player.player_id: player},
        )
        db.session.commit()

        sheet = safe_json_loads(player.character_sheet, {})
        known = {spell['name'] for spell in sheet['spellbook']['knownSpells']}
        assert validation['rejected'] == []
        assert sheet['current_hp'] == 12
        assert 'Misty Step' in known


def test_xp_auto_level_persists_level_stats_and_spell_unlocks(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        session_obj = db.session.get(Session, ids['session_id'])
        player.class_ = 'Wizard'
        player.level = 4
        player.stats = safe_json_dumps(
            {
                'ability_scores': {
                    'strength': 10,
                    'dexterity': 10,
                    'constitution': 10,
                    'intelligence': 15,
                    'wisdom': 10,
                    'charisma': 10,
                },
                'current_hp': 20,
                'hp_current': 20,
                'max_hp': 20,
                'hp_max': 20,
                'xp': 6400,
                'experience': 6400,
                'proficiency_bonus': 2,
            },
            {},
        )
        player.character_sheet = safe_json_dumps({}, {})
        db.session.commit()

        actor_id = display_actor_id(player.player_id)
        state = {
            'sessionId': session_obj.session_id,
            'campaignId': ids['campaign_id'],
            'playerCharacters': [
                {
                    'id': actor_id,
                    'playerId': player.player_id,
                    'name': player.character_name,
                    'class': player.class_,
                    'level': 4,
                    'health': {'currentHp': 20, 'maxHp': 20, 'tempHp': 0, 'conditions': []},
                    'inventory': {'items': [], 'currency': {'pp': 0, 'gp': 0, 'ep': 0, 'sp': 0, 'cp': 0}},
                    'xp': {'current': 6400, 'nextLevelAt': 6500},
                    'metadata': {},
                }
            ],
            'combat': {
                'participants': [
                    {
                        'id': actor_id,
                        'team': 'player',
                        'name': player.character_name,
                        'level': 4,
                        'hp': {'current': 20, 'max': 20, 'temp': 0},
                    }
                ]
            },
            'stateChangeLedger': [],
        }
        validation = validate_state_changes(
            state=state,
            changes=[
                {
                    'id': 'xp_gain_level_five',
                    'type': 'xp.add',
                    'source': 'post_dm',
                    'actorId': actor_id,
                    'amount': 100,
                    'visible': True,
                }
            ],
        )
        applied = apply_state_changes(state, validated_changes_for_application(validation))
        persist_state_to_database(
            session_obj=session_obj,
            state=applied['nextState'],
            players_by_id={player.player_id: player},
        )
        db.session.commit()

        refreshed = db.session.get(Player, player.player_id)
        stats = safe_json_loads(refreshed.stats, {})
        sheet = safe_json_loads(refreshed.character_sheet, {})
        known = {spell['name'] for spell in sheet['spellbook']['knownSpells']}
        snapshot = safe_json_loads(session_obj.state_snapshot, {})
        actor = snapshot['playerCharacters'][0]

        assert validation['rejected'] == []
        assert refreshed.level == 5
        assert stats['xp'] == 6500
        assert stats['experience'] == 6500
        assert stats['nextLevelAt'] == 14000
        assert stats['proficiency_bonus'] == 3
        assert stats['max_hp'] == 28
        assert stats['current_hp'] == 28
        assert actor['level'] == 5
        assert actor['xp'] == {'current': 6500, 'nextLevelAt': 14000}
        assert actor['health']['maxHp'] == 28
        assert snapshot['combat']['participants'][0]['level'] == 5
        assert snapshot['combat']['participants'][0]['hp']['max'] == 28
        assert 'Fireball' in known
        assert 'Fireball' in actor['spells']


def test_post_dm_heuristic_enemy_takes_word_damage_does_not_damage_player(app):
    state = _state(hp_current=10, hp_max=20)
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_wolf_1',
                'team': 'enemy',
                'name': 'Wolf',
                'kind': 'creature',
                'hp': {'current': 11, 'max': 11, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 50,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'forest'},
        'flags': {},
    }

    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I graze the wolf.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The Wolf takes one slashing damage.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=20,
        )

    assert all(change['type'] != 'health.damage' for change in result['proposedChanges'])
    damage_change = next(change for change in result['proposedChanges'] if change['type'] == 'combat.participant.update')
    assert damage_change['participantId'] == 'enemy_wolf_1'
    assert damage_change['hp']['current'] == 10


def test_post_dm_heuristic_player_takes_typed_word_damage_still_damages_player(app):
    state = _state(hp_current=10, hp_max=20)
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_wolf_1',
                'team': 'enemy',
                'name': 'Wolf',
                'kind': 'creature',
                'hp': {'current': 11, 'max': 11, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 50,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'forest'},
        'flags': {},
    }

    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I hold my ground.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The Wolf rakes your arm. You take one slashing damage.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=21,
        )

    player_damage = next(change for change in result['proposedChanges'] if change['type'] == 'health.damage')
    assert player_damage['actorId'] == 'player_1'
    assert player_damage['amount'] == 1
    assert not any(change['type'] == 'combat.participant.update' for change in result['proposedChanges'])


def test_post_dm_does_not_damage_enemy_when_enemy_damages_player(app):
    state = _state()
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'participants': [
            {
                'id': 'player_1',
                'team': 'player',
                'name': 'Kael',
                'hp': {'current': 10, 'max': 20, 'temp': 0},
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_wolf_1',
                'team': 'enemy',
                'name': 'Wolf',
                'kind': 'creature',
                'hp': {'current': 11, 'max': 11, 'temp': 0},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'abilities': [],
                'morale': 50,
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'battlefield': {'environmentType': 'forest'},
        'flags': {},
    }

    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=state,
            player_message='I hold my ground.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='The Wolf bites you; you take 5 damage.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=17,
        )

    enemy_updates = [
        change
        for change in result['proposedChanges']
        if change['type'] == 'combat.participant.update' and change.get('participantId') == 'enemy_wolf_1'
    ]
    assert enemy_updates == []


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


def test_post_dm_pipeline_can_pick_up_scene_item_from_compact_state(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
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
        state['currentScene'] = {
            'locationId': 'old_road',
            'name': 'Old Road',
            'items': [
                {
                    'id': 'starter_druid_wooden_shield',
                    'name': 'Wooden Shield',
                    'quantity': 1,
                    'type': 'armor',
                    'sourceActorId': 'player_2',
                }
            ],
        }
        session_obj.state_snapshot = safe_json_dumps(state, {})
        turn = DmTurn(
            session_id=session_obj.session_id,
            campaign_id=campaign.campaign_id,
            player_id=player.player_id,
            player_input='I grab the wooden shield from the ground.',
            dm_output='Seraphina grabs the Wooden Shield from the ground and carries it under one arm.',
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
            dm_response_text=turn.dm_output,
        )
        db.session.commit()

        refreshed_session = db.session.get(Session, ids['session_id'])
        refreshed_player = db.session.get(Player, ids['player_id'])
        snapshot = safe_json_loads(refreshed_session.state_snapshot, {})
        inventory = safe_json_loads(refreshed_player.inventory, [])

        assert any(change['type'] == 'scene.item.remove' for change in result['postAppliedChanges'])
        assert any(change['type'] == 'inventory.add' for change in result['postAppliedChanges'])
        assert snapshot['currentScene']['items'] == []
        assert any(item.get('name') == 'Wooden Shield' for item in inventory)


def test_post_dm_pipeline_records_combat_outcome_debug_event(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
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
        state['currentScene'] = {'sceneType': 'combat', 'combatState': 'active', 'dangerLevel': 8}
        state['combat'] = {
            'status': 'active',
            'round': 1,
            'participants': [
                {
                    'id': actor_id,
                    'team': 'player',
                    'name': player.character_name,
                    'kind': 'player_character',
                    'hp': {'current': 10, 'max': 20, 'temp': 0},
                    'conditions': [],
                    'position': {'rangeBand': 'near'},
                    'isAlive': True,
                    'isConscious': True,
                },
                {
                    'id': 'enemy_wolf_1',
                    'team': 'enemy',
                    'name': 'Wolf',
                    'kind': 'creature',
                    'hp': {'current': 11, 'max': 11, 'temp': 0},
                    'conditions': [],
                    'position': {'rangeBand': 'near'},
                    'abilities': [],
                    'morale': 50,
                    'isAlive': True,
                    'isConscious': True,
                },
            ],
            'battlefield': {'environmentType': 'forest'},
            'flags': {},
        }
        session_obj.state_snapshot = safe_json_dumps(state, {})
        turn = DmTurn(
            session_id=session_obj.session_id,
            campaign_id=campaign.campaign_id,
            player_id=player.player_id,
            player_input='I slash the wolf.',
            dm_output='Your strike deals 5 damage to the Wolf.',
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
            dm_response_text=turn.dm_output,
        )
        db.session.commit()

        debug_event = CombatDebugEvent.query.filter_by(
            turn_id=turn.turn_id,
            event_type='post_dm_combat_outcome',
        ).one()
        payload = safe_json_loads(debug_event.payload_json, {})

        assert any(change['type'] == 'combat.participant.update' for change in result['postAppliedChanges'])
        assert payload['validationCounts']['accepted'] >= 1
        assert payload['appliedCombatChanges'][0]['participantId'] == 'enemy_wolf_1'
        assert payload['appliedCombatChanges'][0]['hp']['current'] == 6


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


def test_post_dm_semantic_merge_dedupes_duplicate_combat_outcomes():
    changes = turn_pipeline_module._merge_state_changes(
        [
            {
                'id': 'helper_end',
                'type': 'combat.end',
                'reason': 'Combat ends after the scout is killed.',
            },
            {
                'id': 'helper_participant',
                'type': 'combat.participant.update',
                'participantId': 'enemy_scout_1',
                'hp': {'current': 0, 'max': 10},
            },
            {
                'id': 'heuristic_participant',
                'type': 'combat.participant.update',
                'participantId': 'enemy_scout_1',
                'hp': {'current': 0, 'max': 10, 'temp': 0},
                'conditions': ['defeated'],
                'isAlive': False,
            },
            {
                'id': 'heuristic_end',
                'type': 'combat.end',
                'status': 'ended',
                'summary': 'Combat ended from DM narration.',
            },
            {
                'id': 'xp',
                'type': 'xp.add',
                'actorId': 'player_1',
                'amount': 50,
            },
        ]
    )

    assert [change['id'] for change in changes] == ['helper_end', 'helper_participant', 'xp']


def test_build_visible_state_log_names_combat_outcomes():
    validation = {
        'accepted': [
            {
                'change': {
                    'id': 'participant',
                    'type': 'combat.participant.update',
                    'participantName': 'Hollow Arrowmark Scout',
                    'participantId': 'enemy_scout_1',
                    'hp': {'current': 0, 'max': 10},
                },
                'reason': 'ok',
            },
            {
                'change': {'id': 'end', 'type': 'combat.end', 'status': 'ended'},
                'reason': 'ok',
            },
        ],
        'modified': [],
        'rejected': [],
    }

    state_log = build_state_log(turn_id=1, post_validation=validation)

    assert [line['message'] for line in state_log['lines']] == [
        'Hollow Arrowmark Scout defeated.',
        'Combat ended.',
    ]


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
