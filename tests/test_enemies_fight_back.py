"""Fights read true: attacks close the distance, the roll card and the narration name one armour class, enemies
fight back instead of idling or yielding unhurt, and the scripted narrator tells enemy turns in the world's words."""

from __future__ import annotations

from copy import deepcopy

from aidm_server.combat.intent_planner import _within_limits, intent_is_restricted, plan_enemy_intents
from aidm_server.combat.legal_actions import resolve_combat_legal_action
from aidm_server.game_state.orchestration.combat_resolution import (
    derive_trusted_damage_changes,
    player_attack_target,
    resolve_authoritative_player_attack,
)
from aidm_server.player_rolls import roll_against_armor_class
from aidm_server.roll_visibility import public_roll_payload
from aidm_server.scripted_narrator import narrate
from aidm_server.socket_contracts import roll_resolved_payload
from tests.test_combat_actions import (
    ACTOR,
    _action,
    _apply,
    _bundle,
    _economy,
    _engage,
    _enemy,
    _p,
    _participant,
    _player,
    _state,
    _take,
)


# --- 3b. Attacking moves you into reach --------------------------------------------------------------------------


def test_an_attack_out_of_reach_is_offered_as_closing_in_while_the_move_is_unspent():
    state = _state(_p(ACTOR, 'player'), _enemy())

    attack = _action(_bundle(state), 'combat.attack.blade')

    assert (attack['available'], attack['requiresMove']) == (True, True)
    assert (attack['targets'][0]['available'], attack['targets'][0]['requiresMove']) == (True, True)


def test_closing_in_and_attacking_spends_the_move_and_the_action_as_an_advance_then_the_attack():
    state = _state(_p(ACTOR, 'player'), _enemy())
    canonical, code, _message = resolve_combat_legal_action(state, _player(), action_id='combat.attack.blade', target_id='enemy_1')

    after, result = _take(state, 'combat.attack.blade', target_id='enemy_1')

    assert code is None
    assert canonical['move_first'] is True
    assert canonical['economy']['movementCost'] == 1
    assert canonical['message'] == 'Player 1 closes in and attacks Enemy 1 with Longsword.'
    assert result['combatContext']['playerResolvedCombatAction']['actionType'] == 'advance'
    assert _participant(after, ACTOR)['position'] == {'rangeBand': 'melee', 'engagedWith': ['enemy_1']}
    assert (_economy(after)['movementRemaining'], _economy(after)['actionRemaining']) == (0, 0)


def test_with_the_move_spent_an_attack_out_of_reach_is_not_offered():
    state = _state(_p(ACTOR, 'player'), _enemy())
    state['combat']['flags']['turnEconomy'] = {
        'actorId': ACTOR, 'round': 1, 'actionRemaining': 1, 'bonusActionRemaining': 1, 'reactionRemaining': 1, 'movementRemaining': 0,
    }

    attack = _action(_bundle(state), 'combat.attack.blade')

    assert (attack['available'], attack.get('requiresMove')) == (False, None)
    assert attack['targets'][0]['reason'] == 'Target is at near range.'


def test_an_attack_in_reach_needs_no_move():
    actor, enemy = _p(ACTOR, 'player'), _enemy()
    _engage(actor, enemy)

    attack = _action(_bundle(_state(actor, enemy)), 'combat.attack.blade')

    assert attack['available'] is True and 'requiresMove' not in attack and 'requiresMove' not in attack['targets'][0]


# --- 3c. One armour class ----------------------------------------------------------------------------------------


def _attack_state() -> tuple[dict, dict]:
    actor, enemy = _p(ACTOR, 'player'), _enemy(ac=13)
    _engage(actor, enemy)
    state = _state(actor, enemy)
    canonical, _code, _message = resolve_combat_legal_action(state, _player(), action_id='combat.attack.blade', target_id='enemy_1')
    return state, canonical


def test_the_attack_roll_carries_the_armour_class_the_engine_compares_against():
    state, canonical = _attack_state()
    target = player_attack_target(state, actor_id=ACTOR, combat_action=canonical)
    roll = roll_against_armor_class(
        {'authoritative': True, 'rule_type': 'attack', 'die': 'd20', 'rolls': [9], 'kept': 9, 'modifier': 5, 'total': 14, 'dc': 14},
        target,
    )

    attack = resolve_authoritative_player_attack(
        state=state, actor_id=ACTOR, turn_id=7, action_intent={'kind': 'combat', 'combat': canonical}, authoritative_roll=roll
    )
    room = roll_resolved_payload(
        session_id=1, turn_id=7, player_id=1, client_message_id=None, pending_turn_id=None, roll=roll, include_private_provenance=False
    )

    assert target == {'armorClass': 13, 'criticalThreshold': 20}
    assert (roll['target_ac'], roll['dc'], roll['outcome']) == (13, 13, 'success')
    assert attack['targetArmorClass'] == roll['target_ac'] and attack['hit'] is True
    assert room['target_ac'] == 13
    assert public_roll_payload(roll)['target_ac'] == 13


def test_a_natural_1_misses_and_a_natural_20_hits_whatever_the_armour_class():
    target = {'armorClass': 13, 'criticalThreshold': 20}

    assert roll_against_armor_class({'kept': 1, 'total': 30}, target)['outcome'] == 'failure'
    assert roll_against_armor_class({'kept': 20, 'total': 21}, {'armorClass': 40, 'criticalThreshold': 20})['outcome'] == 'success'
    assert roll_against_armor_class({'kept': 10, 'total': 12}, target)['outcome'] == 'failure'


# --- 3d / 3e. Enemies fight back ---------------------------------------------------------------------------------


def _schemer(**extra) -> dict:
    """An enemy whose objective scores above an attack: it would idle every turn without the limit."""

    return _enemy(
        behavior={'primaryGoal': 'delay_party', 'discipline': 90, 'aggression': 20, 'intelligenceProfile': 'trained'},
        morale=80,
        **extra,
    )


def _plan(state: dict, history: list[bool] | None = None) -> dict:
    combat = deepcopy(state['combat'])
    if history is not None:
        combat['flags']['enemyTurnHistory'] = {'enemy_1': history}
    return plan_enemy_intents(combat)['intents'][0]


def test_a_quiet_intent_is_taken_at_most_once_in_any_three_turns_and_never_twice_in_a_row():
    actor, enemy = _p(ACTOR, 'player'), _schemer()
    _engage(actor, enemy)
    state = _state(actor, enemy)

    assert _plan(state)['intentType'] == 'delay'
    assert _plan(state, [False, True])['intentType'] == 'attack'
    assert _plan(state, [True, False])['intentType'] == 'attack'
    assert _plan(state, [False, False])['intentType'] == 'delay'


def test_each_enemy_turn_is_recorded_on_the_combat_state():
    actor, enemy = _p(ACTOR, 'player'), _schemer()
    state = _state(actor, enemy)
    quiet = {'combatState': {'enemyResolvedActions': [{'enemyId': 'enemy_1', 'intentType': 'delay', 'resolvedWithoutRoll': True}]}}
    pressing = {'combatState': {'enemyResolvedActions': [{'enemyId': 'enemy_1', 'intentType': 'attack', 'targetId': ACTOR}]}}

    first = _apply(state, derive_trusted_damage_changes(state=state, dm_context_packet=quiet, actor_id=ACTOR, turn_id=1, already_applied_changes=[]).all_changes)
    second = _apply(first, derive_trusted_damage_changes(state=first, dm_context_packet=pressing, actor_id=ACTOR, turn_id=2, already_applied_changes=[]).all_changes)
    third = _apply(second, derive_trusted_damage_changes(state=second, dm_context_packet=pressing, actor_id=ACTOR, turn_id=3, already_applied_changes=[]).all_changes)

    assert first['combat']['flags']['enemyTurnHistory'] == {'enemy_1': [True]}
    assert second['combat']['flags']['enemyTurnHistory'] == {'enemy_1': [True, False]}
    assert third['combat']['flags']['enemyTurnHistory'] == {'enemy_1': [False, False]}


def test_a_helpers_forbidden_choice_is_replaced_by_the_best_damaging_candidate():
    actor, enemy = _p(ACTOR, 'player'), _schemer()
    state = _state(actor, enemy)
    combat = {**state['combat'], 'flags': {**state['combat']['flags'], 'enemyTurnHistory': {'enemy_1': [True]}}}
    candidates = [
        {'score': 90, 'intentType': 'delay', 'intent': {'intentType': 'delay'}},
        {'score': 40, 'intentType': 'attack', 'intent': {'intentType': 'attack', 'targetId': ACTOR}},
    ]

    replaced = _within_limits({'intentType': 'delay', 'selectionMethod': 'sentient_enemy_brain_candidate_selector'}, candidates, enemy, combat)

    assert (replaced['intentType'], replaced['targetId'], replaced['selectionMethod']) == ('attack', ACTOR, 'intent_limit_replacement')


def test_protecting_an_ally_is_quiet_only_while_no_ally_is_below_half():
    enemy, ally = _schemer(), _enemy('enemy_2')
    state = _state(_p(ACTOR, 'player'), enemy, ally)
    combat = {**state['combat'], 'flags': {**state['combat']['flags'], 'enemyTurnHistory': {'enemy_1': [True]}}}

    assert intent_is_restricted({'intentType': 'protect_ally'}, enemy, combat) is True
    ally['hp'] = {'current': 5, 'max': 20, 'temp': 0}
    assert intent_is_restricted({'intentType': 'protect_ally'}, enemy, combat) is False


def test_an_enemy_parleys_or_gives_ground_only_when_hurt_beaten_back_or_late():
    enemy, ally = _enemy(), _enemy('enemy_2')
    combat = _state(_p(ACTOR, 'player'), enemy, ally)['combat']

    for intent in ('negotiate', 'surrender', 'yield', 'retreat', 'flee'):
        assert intent_is_restricted({'intentType': intent}, enemy, combat) is True
    assert intent_is_restricted({'intentType': 'negotiate'}, {**enemy, 'hp': {'current': 10, 'max': 20}}, combat) is False
    fallen = {**combat, 'participants': [*combat['participants'][:2], {**ally, 'hp': {'current': 0, 'max': 20}, 'isAlive': False}]}
    assert intent_is_restricted({'intentType': 'surrender'}, enemy, fallen) is False
    late = {**combat, 'round': 4}
    assert intent_is_restricted({'intentType': 'retreat'}, enemy, late) is True
    assert intent_is_restricted({'intentType': 'retreat'}, {**enemy, 'hp': {'current': 19, 'max': 20}}, late) is False
    allowed = {**combat, 'flags': {**combat['flags'], 'allowParley': True}}
    assert intent_is_restricted({'intentType': 'negotiate'}, enemy, allowed) is False


# --- 3e / 3f. The scripted narrator tells enemy turns in the world's words ----------------------------------------


def _narrate(actions: list[dict], **facts) -> str:
    return narrate(
        {'live_world_state': {'currentScene': {'name': 'Road'}}},
        {
            'turn_number': 3,
            'state_pipeline': {
                'stateChangesAlreadyApplied': [],
                'combatState': {'enemyResolvedActions': actions},
                'narration': {
                    'enemies': [
                        {'name': 'Pale Lantern', 'proper': False},
                        {'name': 'Ashmarked Cutthroat', 'proper': False},
                        {'name': 'Wardbound Beast', 'proper': False},
                    ],
                    **facts,
                },
            },
        },
        'I end my turn.',
        speaker='Liora',
    )


def test_quiet_and_parley_turns_are_told_as_what_the_enemy_does():
    reply = _narrate(
        [
            {'enemyName': 'Pale Lantern', 'intentType': 'delay', 'resolvedWithoutRoll': True},
            {'enemyName': 'Pale Lantern', 'intentType': 'protect_ally', 'targetName': 'Wardbound Beast', 'resolvedWithoutRoll': True},
            {'enemyName': 'Pale Lantern', 'intentType': 'complete_objective', 'resolvedWithoutRoll': True},
            {'enemyName': 'Pale Lantern', 'intentType': 'use_environment', 'resolvedWithoutRoll': True},
            {'enemyName': 'Wardbound Beast', 'intentType': 'retreat', 'resolvedWithoutRoll': True},
            {'enemyName': 'Ashmarked Cutthroat', 'intentType': 'negotiate', 'resolvedWithoutRoll': True},
        ],
        nextActorLine='It is your turn.',
    )

    assert reply == (
        'The pale lantern hangs back, watching. The pale lantern moves to shield the wardbound beast. '
        'The pale lantern turns back to its work. The pale lantern looks for an advantage in the ground. '
        'The wardbound beast gives ground. The ashmarked cutthroat throws down its weapon and yields.\n\nIt is your turn.'
    )
    for jargon in ('chooses to', 'shifts tactics', 'moves to delay', 'negotiate'):
        assert jargon not in reply


def test_a_fight_that_ends_in_flight_says_so_and_hands_no_turn_on():
    alone = _narrate(
        [{'enemyName': 'Wardbound Beast', 'intentType': 'retreat', 'resolvedWithoutRoll': True}],
        fightEnds='enemies_fled',
        nextActorLine='It is your turn.',
    )
    together = _narrate(
        [
            {'enemyName': 'Wardbound Beast', 'intentType': 'retreat', 'resolvedWithoutRoll': True},
            {'enemyName': 'Pale Lantern', 'intentType': 'flee', 'resolvedWithoutRoll': True},
        ],
        fightEnds='enemies_fled',
    )

    assert alone == 'The wardbound beast breaks and runs.'
    assert together == 'The last of them break and run.'


def test_a_save_is_told_as_the_ability_turned_on_the_hero():
    reply = _narrate(
        [
            {
                'enemyName': 'Pale Lantern',
                'targetName': 'Liora',
                'abilityName': 'Lantern-Lure',
                'saveAbility': 'wis',
                'saveTotal': 16,
                'saveDC': 10,
                'saveSucceeded': True,
                'damageTotal': 0,
            }
        ]
    )

    assert reply == 'The pale lantern turns Lantern-Lure on you: a Wisdom save of 16 against DC 10, a success.'


def test_a_search_that_uncovers_a_hidden_object_says_so():
    reply = narrate(
        {'live_world_state': {'currentScene': {'name': 'Inn'}}},
        {
            'turn_number': 2,
            'state_pipeline': {
                'stateChangesAlreadyApplied': [{'type': 'scene.interactable.action', 'targetId': 'wagon', 'objectAction': 'search'}],
                'narration': {
                    'objects': [
                        {'targetId': 'wagon', 'name': 'Lead Wagon', 'action': 'search', 'label': 'Search the lead wagon', 'labelAuthored': True, 'found': [], 'uncovered': ['Wagon Strongbox']}
                    ]
                },
            },
        },
        'I search the lead wagon',
        speaker='Arden',
    )

    assert 'You uncover the Wagon Strongbox.' in reply
    assert 'You find nothing more.' not in reply


def test_telegraphs_are_in_world_cues_and_an_abstract_reason_shows_none():
    actor, schemer = _p(ACTOR, 'player'), _schemer()
    _engage(actor, schemer)

    quiet = _plan(_state(actor, schemer))

    assert quiet['intentType'] == 'delay' and 'visibleTelegraph' not in quiet
    assert 'objective' not in str(quiet.get('visibleTelegraph') or '')
