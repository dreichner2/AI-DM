from __future__ import annotations

from copy import deepcopy
import json

import pytest

import aidm_server.combat.actions as combat_actions
from aidm_server.combat import modifiers
from aidm_server.combat.intent_planner import plan_enemy_intents
from aidm_server.combat.legal_actions import (
    legal_combat_actions_for_player,
    resolve_combat_legal_action,
    with_combat_legal_actions,
)
from aidm_server.combat.pipeline import apply_turn_boundaries, prepare_combat_for_turn
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.orchestration.combat_resolution import (
    build_dm_combat_context,
    derive_trusted_damage_changes,
    resolve_authoritative_player_attack,
    without_trusted_damage_overlaps,
)
from aidm_server.game_state.validation.validator import validate_state_changes, validated_changes_for_application
from aidm_server.models import Campaign, DmTurn, Player, Session
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player


ACTOR = 'player_1'
NO_LLM = {'tacticalLevel': 'simple', 'allowSentientEnemyBrain': False, 'allowFreeformEnemyTactics': False, 'maxLlmCallsPerRound': 0}
CLUB = {'id': 'club', 'name': 'Club', 'type': 'attack', 'range': 'melee', 'attackBonus': 3, 'damage': {'dice': '1d4+1', 'type': 'bludgeoning'}}
SHORTBOW = {'id': 'shortbow', 'name': 'Shortbow', 'type': 'attack', 'range': 'far', 'attackBonus': 4, 'damage': {'dice': '1d6+2', 'type': 'piercing'}}


def _p(pid: str, team: str, *, band: str = 'near', hp: int = 20, ac: int = 12, **extra) -> dict:
    return {
        'id': pid,
        'name': pid.replace('_', ' ').title(),
        'team': team,
        'kind': 'player_character' if team == 'player' else 'creature',
        'hp': {'current': hp, 'max': 20, 'temp': 0},
        'armorClass': ac,
        'stats': {'strength': 14, 'dexterity': 12, 'wisdom': 10},
        'conditions': [],
        'position': {'rangeBand': band},
        **({'playerId': int(pid.split('_')[1])} if team == 'player' else {}),
        **extra,
    }


def _enemy(pid: str = 'enemy_1', *, band: str = 'near', abilities: list | None = None, **extra) -> dict:
    return _p(pid, 'enemy', band=band, abilities=deepcopy(abilities if abilities is not None else [CLUB]), **extra)


def _engage(first: dict, second: dict) -> None:
    for one, other in ((first, second), (second, first)):
        one['position'] = {**one['position'], 'rangeBand': 'melee', 'engagedWith': [*one['position'].get('engagedWith', []), other['id']]}


def _state(*participants: dict, battlefield: dict | None = None) -> dict:
    players = [participant for participant in participants if participant['team'] == 'player']
    return {
        'playerCharacters': [
            {
                'id': player['id'],
                'playerId': player['playerId'],
                'name': player['name'],
                'level': 3,
                'health': {'currentHp': player['hp']['current'], 'maxHp': 20, 'conditions': list(player['conditions'])},
                'stats': {'strength': 14, 'dexterity': 12},
                'inventory': {'items': [{'id': 'blade', 'name': 'Longsword', 'type': 'weapon', 'equipped': True, 'slot': 'main_hand'}]},
            }
            for player in players
        ],
        'combat': {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': list(participants),
            'battlefield': battlefield or {},
            'initiative': [
                {'participantId': participant['id'], 'roll': 20 - index, 'modifier': 0, 'total': 20 - index, 'order': index}
                for index, participant in enumerate(participants)
            ],
            'flags': {'activeActorId': ACTOR, 'combatDifficultyAI': NO_LLM},
        },
    }


def _player(*, inventory: list | None = None, stats: dict | None = None) -> Player:
    return Player(
        player_id=1,
        character_name='Player 1',
        class_='Fighter',
        level=3,
        stats=json.dumps({'ability_scores': stats or {'strength': 16, 'dexterity': 14, 'wisdom': 12}}),
        weapon_proficiencies=json.dumps([{'kind': 'category', 'value': 'all'}]),
        inventory=json.dumps(
            inventory
            if inventory is not None
            else [{'id': 'blade', 'name': 'Longsword', 'type': 'weapon', 'subtype': 'longsword', 'equipped': True, 'slot': 'main_hand'}]
        ),
    )


def _bundle(state: dict, player: Player | None = None) -> dict:
    bundle = legal_combat_actions_for_player(state, player or _player())
    assert bundle is not None
    return bundle


def _action(bundle: dict, action_id: str) -> dict:
    return next(action for action in bundle['actions'] if action['id'] == action_id)


def _turn(turn_id: int, canonical: dict) -> DmTurn:
    return DmTurn(turn_id=turn_id, player_id=1, metadata_json=json.dumps({'action_intent': {'kind': 'combat', 'combat': canonical}}))


def _prepare(state: dict, turn: DmTurn) -> dict:
    return prepare_combat_for_turn(
        state=state,
        session_obj=Session(session_id=1, campaign_id=1),
        campaign=Campaign(campaign_id=1, workspace_id='workspace'),
        turn=turn,
        player_message='',
        workspace_id='workspace',
    )


def _apply(state: dict, changes: list[dict], *, replay: bool = False) -> dict:
    validation = validate_state_changes(state=state, changes=changes)
    if replay:
        assert {entry['reason'] for entry in validation['rejected']} <= {'State change was already applied.'}
    else:
        assert validation['rejected'] == []
    return apply_state_changes(state, validated_changes_for_application(validation))['nextState']


def _take(state: dict, action_id: str, *, target_id: str | None = None, turn_id: int = 100, player: Player | None = None) -> tuple[dict, dict]:
    canonical, code, message = resolve_combat_legal_action(state, player or _player(), action_id=action_id, target_id=target_id)
    assert code is None, message
    result = _prepare(state, _turn(turn_id, canonical))
    assert 'combatActionBlockedReason' not in result['debug'], result['debug'].get('combatActionBlockedReason')
    return _apply(state, result['changes']), result


def _participant(state: dict, participant_id: str) -> dict:
    return next(participant for participant in state['combat']['participants'] if participant['id'] == participant_id)


def _economy(state: dict) -> dict:
    return state['combat']['flags']['turnEconomy']


def _fixed_roller(monkeypatch, value: int) -> None:
    monkeypatch.setattr(combat_actions, 'deterministic_roller', lambda _seed: (lambda _sides: value))


# --- A. universal actions -------------------------------------------------------


def test_every_universal_action_is_in_the_bundle_with_its_cost_and_plain_reasons():
    bundle = _bundle(_state(_p(ACTOR, 'player'), _enemy(band='far')))

    ids = [action['id'] for action in bundle['actions']]
    assert ids[0] == 'combat.attack.blade'
    assert ids[-1] == 'combat.end_turn'
    assert {
        'combat.advance', 'combat.reposition', 'combat.dash', 'combat.disengage',
        'combat.dodge', 'combat.help', 'combat.hide', 'combat.stabilize',
    } <= set(ids)
    costs = {action['id']: action['cost'] for action in bundle['actions']}
    assert costs['combat.dodge'] == {'action': 1, 'bonusAction': 0, 'movement': 0, 'reaction': 0}
    assert costs['combat.advance'] == {'action': 0, 'bonusAction': 0, 'movement': 1, 'reaction': 0}
    assert costs['combat.reposition'] == costs['combat.advance']
    assert costs['combat.dash'] == costs['combat.disengage'] == costs['combat.help'] == costs['combat.dodge']
    assert costs['combat.end_turn'] == {'action': 0, 'bonusAction': 0, 'movement': 0, 'reaction': 0}
    assert _action(bundle, 'combat.reposition')['label'] == 'Fall back'
    advance = _action(bundle, 'combat.advance')
    assert (advance['available'], advance['reason']) == (False, 'No enemy is close enough to engage.')
    assert _action(bundle, 'combat.help')['reason'] == 'No ally is close enough to help.'
    assert _action(bundle, 'combat.stabilize')['reason'] == 'No dying ally is within reach.'
    assert (bundle['attacksRemaining'], bundle['reactionRemaining']) == (0, 1)


def test_dodge_spends_the_action_and_lasts_until_the_actors_next_turn():
    state, result = _take(_state(_p(ACTOR, 'player'), _enemy()), 'combat.dodge')

    assert 'dodging' in _participant(state, ACTOR)['conditions']
    assert _economy(state)['actionRemaining'] == 0
    assert result['combatContext']['playerResolvedCombatAction']['outcome'] == 'Player 1 is dodging until the start of their next turn.'
    dodge = _action(_bundle(state), 'combat.dodge')
    assert (dodge['available'], dodge['reason']) == (False, 'Already dodging.')
    assert modifiers.attack_context(_enemy(), _participant(state, ACTOR), kind='enemy_attack', ranged=False)['mode'] == 'disadvantage'
    apply_turn_boundaries(state['combat'], [{'actorId': ACTOR, 'phase': 'start', 'round': 2}], turn_id=101)
    assert 'dodging' not in _participant(state, ACTOR)['conditions']


def test_advance_moves_inward_and_closes_into_melee_engaging_both_sides():
    state = _state(_p(ACTOR, 'player', band='far'), _enemy())
    bundle = _bundle(state)
    advance = _action(bundle, 'combat.advance')
    assert advance['available'] is True and not advance.get('requiresTarget')

    state, _result = _take(state, 'combat.advance', turn_id=100)
    assert _participant(state, ACTOR)['position'] == {'rangeBand': 'near'}
    assert _economy(state)['movementRemaining'] == 0

    state['combat']['flags'].pop('turnEconomy')
    state, result = _take(state, 'combat.advance', target_id='enemy_1', turn_id=101)
    assert _participant(state, ACTOR)['position'] == {'rangeBand': 'melee', 'engagedWith': ['enemy_1']}
    assert _participant(state, 'enemy_1')['position'] == {'rangeBand': 'melee', 'engagedWith': [ACTOR]}
    assert result['combatContext']['playerResolvedCombatAction']['movement'] == {
        'fromRangeBand': 'near', 'toRangeBand': 'melee', 'engagedWith': ['enemy_1'],
    }

    state['combat']['flags'].pop('turnEconomy')
    in_melee = _action(_bundle(state), 'combat.advance')
    assert (in_melee['available'], in_melee['reason']) == (False, 'Already in melee.')
    _participant(state, ACTOR)['conditions'] = ['grappled']
    assert _action(_bundle(state), 'combat.advance')['reason'] == 'Grappled: no movement.'


def test_fall_back_moves_outward_and_ends_engagement_on_both_sides():
    actor, enemy = _p(ACTOR, 'player'), _enemy()
    _engage(actor, enemy)
    state = _state(actor, enemy)
    state, _result = _take(state, 'combat.disengage', turn_id=100)
    state, result = _take(state, 'combat.reposition', turn_id=101)

    assert _participant(state, ACTOR)['position'] == {'rangeBand': 'near'}
    assert _participant(state, 'enemy_1')['position'] == {'rangeBand': 'melee'}
    assert result['combatContext']['playerResolvedCombatAction']['movement']['leftEngagementWith'] == ['enemy_1']
    far_state = _state(_p(ACTOR, 'player', band='distant'), _enemy())
    assert _action(_bundle(far_state), 'combat.reposition')['reason'] == 'Already as far back as you can go.'


def test_dash_grants_one_more_movement_this_turn():
    state = _state(_p(ACTOR, 'player', band='distant'), _enemy())
    state, _result = _take(state, 'combat.dash', turn_id=100)
    assert (_economy(state)['actionRemaining'], _economy(state)['movementRemaining']) == (0, 2)

    state, _result = _take(state, 'combat.advance', turn_id=101)
    state, _result = _take(state, 'combat.advance', turn_id=102)
    assert _participant(state, ACTOR)['position']['rangeBand'] == 'near'
    assert _action(_bundle(state), 'combat.advance')['reason'] == "This turn's movement is already spent."


def test_disengage_only_grants_protection_and_leaves_movement_for_the_player():
    actor, enemy = _p(ACTOR, 'player'), _enemy()
    _engage(actor, enemy)
    state, _result = _take(_state(actor, enemy), 'combat.disengage')

    assert _participant(state, ACTOR)['position']['rangeBand'] == 'melee'
    assert _economy(state)['disengaged'] is True
    assert (_economy(state)['actionRemaining'], _economy(state)['movementRemaining']) == (0, 1)
    assert _action(_bundle(state), 'combat.reposition')['available'] is True
    assert _action(_bundle(state), 'combat.disengage')['reason'] == 'Already disengaged this turn.'


def test_help_gives_an_ally_in_reach_advantage_on_one_attack_until_the_helpers_next_turn():
    ally = _p('player_2', 'player', band='far')
    distant_ally = _p('player_3', 'player', band='distant')
    state = _state(_p(ACTOR, 'player'), ally, distant_ally, _enemy())
    targets = {target['id']: target for target in _action(_bundle(state), 'combat.help')['targets']}
    assert targets['player_2']['available'] is True
    assert targets['player_3']['reason'] == 'Too far away to help.'

    state, _result = _take(state, 'combat.help', target_id='player_2')
    helped = _participant(state, 'player_2')
    context = modifiers.attack_context(helped, _participant(state, 'enemy_1'), kind='weapon_attack', ranged=True)
    assert context['mode'] == 'advantage'
    assert context['consume'] == [{'participantId': 'player_2', 'effectId': 'help_100_player_1_player_2'}]
    apply_turn_boundaries(state['combat'], [{'actorId': ACTOR, 'phase': 'start', 'round': 2}], turn_id=101)
    assert _participant(state, 'player_2')['activeEffects'] == []


def test_hide_rolls_stealth_against_the_sharpest_passive_perception(monkeypatch):
    sharp = _enemy('enemy_2', stats={'strength': 10, 'dexterity': 10, 'wisdom': 16})
    state = _state(_p(ACTOR, 'player'), _enemy(), sharp)
    canonical, code, _message = resolve_combat_legal_action(state, _player(), action_id='combat.hide')
    assert code is None
    assert canonical['check']['skill'] == 'stealth' and canonical['check']['modifier'] == 2

    _fixed_roller(monkeypatch, 11)
    hidden = _prepare(state, _turn(100, canonical))
    check = hidden['combatContext']['playerResolvedCombatAction']['check']
    assert (check['roll'], check['total'], check['dc'], check['success']) == (11, 13, 13, True)
    _fixed_roller(monkeypatch, 10)
    missed = _prepare(state, _turn(101, canonical))
    assert missed['combatContext']['playerResolvedCombatAction']['check']['success'] is False
    assert all(change['type'] != 'combat.condition.add' for change in missed['changes'])

    hidden_state = _apply(state, hidden['changes'])
    assert modifiers.is_hidden(_participant(hidden_state, ACTOR))
    assert _participant(hidden_state, ACTOR)['position']['isHidden'] is True


def test_hide_is_not_available_in_melee_without_cover():
    actor, enemy = _p(ACTOR, 'player'), _enemy()
    _engage(actor, enemy)
    state = _state(actor, enemy)
    assert _action(_bundle(state), 'combat.hide')['reason'] == 'You cannot hide in melee without cover.'
    actor['position']['coverId'] = 'crates'
    covered = _state(actor, enemy, battlefield={'cover': [{'id': 'crates', 'name': 'Crates', 'coverType': 'half'}]})
    assert _action(_bundle(covered), 'combat.hide')['available'] is True


def test_stabilize_rolls_medicine_dc_10_for_a_dying_ally_in_reach(monkeypatch):
    dying = _p('player_2', 'player', hp=0, conditions=['unconscious', 'dying'], deathSaves={'successes': 1, 'failures': 1})
    state = _state(_p(ACTOR, 'player'), dying, _enemy())
    target = _action(_bundle(state), 'combat.stabilize')['targets'][0]
    assert (target['id'], target['available']) == ('player_2', True)

    _fixed_roller(monkeypatch, 7)
    canonical, _code, _message = resolve_combat_legal_action(state, _player(), action_id='combat.stabilize', target_id='player_2')
    assert canonical['check']['skill'] == 'medicine' and canonical['check']['modifier'] == 1
    failed = _prepare(state, _turn(100, canonical))
    assert failed['combatContext']['playerResolvedCombatAction']['check']['total'] == 8
    assert all(change['type'] != 'combat.stabilize' for change in failed['changes'])

    _fixed_roller(monkeypatch, 9)
    state, result = _take(state, 'combat.stabilize', target_id='player_2', turn_id=101)
    assert result['combatContext']['playerResolvedCombatAction']['check']['success'] is True
    stabilized = _participant(state, 'player_2')
    assert 'stable' in stabilized['conditions'] and 'dying' not in stabilized['conditions']
    assert state['playerCharacters'][1]['health']['deathSaves'] == {'successes': 0, 'failures': 0}

    far = _p('player_2', 'player', band='distant', hp=0, conditions=['unconscious', 'dying'])
    assert _action(_bundle(_state(_p(ACTOR, 'player'), far, _enemy())), 'combat.stabilize')['targets'][0]['reason'] == 'Too far away to reach.'


def test_actions_are_replay_safe_by_turn_record():
    state = _state(_p(ACTOR, 'player'), _enemy())
    canonical, _code, _message = resolve_combat_legal_action(state, _player(), action_id='combat.dodge')
    turn = _turn(100, canonical)
    first = _prepare(state, turn)
    after = _apply(state, first['changes'])

    replay = _prepare(after, turn)
    assert 'playerResolvedCombatAction' not in replay['combatContext']
    assert all(change['type'] != 'combat.condition.add' for change in replay['changes'])
    replayed = _apply(after, replay['changes'], replay=True)
    assert _economy(replayed)['actionRemaining'] == 0
    assert _economy(replayed)['spentActionIds'] == ['combat.dodge@turn:100']
    assert apply_state_changes(after, first['changes'])['appliedChanges'] == []


# --- B. attacks -----------------------------------------------------------------


def test_melee_weapons_need_engagement_or_both_in_melee_and_reach_weapons_reach_near():
    actor, engaged, scrum, near = _p(ACTOR, 'player'), _enemy('enemy_1'), _enemy('enemy_2', band='melee'), _enemy('enemy_3')
    _engage(actor, engaged)
    targets = {target['id']: target for target in _action(_bundle(_state(actor, engaged, scrum, near)), 'combat.attack.blade')['targets']}
    assert targets['enemy_1']['available'] and targets['enemy_2']['available']
    assert targets['enemy_3']['reason'] == 'Target is at near range.'

    far_actor = _p(ACTOR, 'player')
    spear = [{'id': 'pike', 'name': 'Pike', 'type': 'weapon', 'equipped': True, 'slot': 'main_hand', 'tags': ['reach']}]
    pike = _action(_bundle(_state(far_actor, _enemy('enemy_3')), _player(inventory=spear)), 'combat.attack.pike')
    assert pike['range']['allowedBands'] == ['melee', 'near']
    assert pike['targets'][0]['available'] is True
    assert _action(_bundle(_state(_p(ACTOR, 'player'), _enemy('enemy_3'))), 'combat.attack.blade')['range']['allowedBands'] == ['melee']


def test_ranged_attacks_made_while_engaged_have_disadvantage():
    archer, enemy = _p(ACTOR, 'player'), _enemy()
    _engage(archer, enemy)
    bow = [{'id': 'bow', 'name': 'Longbow', 'type': 'weapon', 'subtype': 'longbow', 'equipped': True, 'slot': 'two_hands'}]
    target = _action(_bundle(_state(archer, enemy), _player(inventory=bow)), 'combat.attack.bow')['targets'][0]
    assert target['rollMode'] == 'disadvantage'
    assert 'Ranged attack while engaged in melee' in target['rollReasons']
    assert modifiers.attack_context(archer, enemy, kind='spell_attack', ranged=True)['mode'] == 'disadvantage'
    assert modifiers.attack_context(archer, enemy, kind='weapon_attack', ranged=False)['mode'] == 'normal'
    enemy['conditions'] = ['stunned']
    assert modifiers.attack_context(archer, enemy, kind='weapon_attack', ranged=True, combat={'participants': [archer, enemy]})['mode'] == 'advantage'


def _two_attacks(participant, _combat):
    return 2 if participant.get('id') == ACTOR else None


def test_extra_attack_spends_the_action_once_then_counts_attacks():
    actor, enemy = _p(ACTOR, 'player'), _enemy()
    _engage(actor, enemy)
    state = _state(actor, enemy)
    modifiers.register_attacks_per_action_provider(_two_attacks)
    try:
        state, _result = _take(state, 'combat.attack.blade', target_id='enemy_1', turn_id=100)
        assert (_economy(state)['actionRemaining'], _economy(state)['attacksRemaining']) == (0, 1)
        bundle = _bundle(state)
        attack = _action(bundle, 'combat.attack.blade')
        assert attack['available'] is True and attack['cost']['action'] == 0
        assert bundle['attacksRemaining'] == 1

        canonical, _code, _message = resolve_combat_legal_action(state, _player(), action_id='combat.attack.blade', target_id='enemy_1')
        second = _turn(101, canonical)
        state = _apply(state, _prepare(state, second)['changes'])
        assert (_economy(state)['actionRemaining'], _economy(state)['attacksRemaining']) == (0, 0)
        state = _apply(state, _prepare(state, second)['changes'], replay=True)
        assert _economy(state)['attacksRemaining'] == 0
        assert _action(_bundle(state), 'combat.attack.blade')['reason'] == "This turn's action is already spent."
    finally:
        modifiers.unregister_attacks_per_action_provider(_two_attacks)
    assert modifiers.attacks_per_action(actor) == 1


def test_level_five_fighter_gets_extra_attack_in_the_actual_turn():
    actor = _p(ACTOR, 'player', level=5, **{'class': 'Fighter'})
    enemy = _enemy()
    _engage(actor, enemy)
    state, _result = _take(_state(actor, enemy), 'combat.attack.blade', target_id='enemy_1', turn_id=100)
    assert (_economy(state)['actionRemaining'], _economy(state)['attacksRemaining']) == (0, 1)
    assert _action(_bundle(state), 'combat.attack.blade')['available'] is True


DUAL_WIELD = [
    {'id': 'fang', 'name': 'Dagger', 'type': 'weapon', 'subtype': 'dagger', 'equipped': True, 'slot': 'main_hand'},
    {'id': 'sting', 'name': 'Shortsword', 'type': 'weapon', 'subtype': 'shortsword', 'equipped': True, 'slot': 'off_hand'},
]


def test_offhand_attack_needs_two_light_weapons_and_an_attack_this_turn():
    actor, enemy = _p(ACTOR, 'player'), _enemy()
    _engage(actor, enemy)
    state = _state(actor, enemy)
    player = _player(inventory=DUAL_WIELD)
    offhand = _action(_bundle(state, player), 'combat.offhand_attack')
    assert (offhand['available'], offhand['reason']) == (False, 'Attack with one of your light weapons first.')
    assert offhand['cost'] == {'action': 0, 'bonusAction': 1, 'movement': 0, 'reaction': 0}

    state, _result = _take(state, 'combat.attack.fang', target_id='enemy_1', player=player)
    offhand = _action(_bundle(state, player), 'combat.offhand_attack')
    assert offhand['available'] is True and offhand['weapon']['name'] == 'Shortsword'
    canonical, code, _message = resolve_combat_legal_action(state, player, action_id='combat.offhand_attack', target_id='enemy_1')
    assert code is None
    assert canonical['offhand'] is True
    assert canonical['message'] == 'Player 1 attacks Enemy 1 with their off-hand Shortsword.'
    state = _apply(state, _prepare(state, _turn(101, canonical))['changes'])
    assert (_economy(state)['bonusActionRemaining'], _economy(state)['actionRemaining']) == (0, 0)

    shield = [DUAL_WIELD[0], {'id': 'board', 'name': 'Shield', 'type': 'armor', 'equipped': True, 'slot': 'off_hand'}]
    assert all(action['id'] != 'combat.offhand_attack' for action in _bundle(_state(_p(ACTOR, 'player'), _enemy()), _player(inventory=shield))['actions'])


@pytest.mark.parametrize(('ability_modifier', 'expected'), [(3, 4), (-1, 3)])
def test_offhand_damage_adds_the_ability_modifier_only_when_negative(ability_modifier, expected):
    actor, enemy = _p(ACTOR, 'player'), _enemy(ac=5)
    _engage(actor, enemy)
    resolved = resolve_authoritative_player_attack(
        state=_state(actor, enemy),
        actor_id=ACTOR,
        turn_id=7,
        action_intent={
            'combat': {
                'authoritative': True, 'action_type': 'attack', 'offhand': True, 'target_id': 'enemy_1',
                'weapon_id': 'sting', 'weapon_name': 'Shortsword', 'damage_dice': '1d6', 'damage_type': 'piercing',
            }
        },
        authoritative_roll={'authoritative': True, 'kept': 15, 'total': 20, 'modifier_breakdown': {'ability_modifier': ability_modifier}},
        damage_roller=lambda _sides: 4,
    )
    assert resolved['offhand'] is True
    assert resolved['damageTotal'] == expected


def _enemy_turn(state: dict, enemy_id: str, *, target_id: str = ACTOR, ability_id: str = 'club', intent: str = 'attack', roller=None) -> dict:
    return build_dm_combat_context(
        state=state,
        combat_context={'enemyRequiredActions': [{'enemyId': enemy_id, 'targetId': target_id, 'intentType': intent, 'abilityId': ability_id}]},
        pending_rolls=[],
        resolved_player_roll=False,
        enemy_roller=roller,
        turn_id=55,
    )


def test_ongoing_damage_defeats_enemy_before_its_planned_attack(monkeypatch):
    monkeypatch.setattr('aidm_server.combat.pipeline.deterministic_roller', lambda _seed: lambda _sides: 4)
    enemy = _enemy(hp=3, activeEffects=[{
        'id': 'burn', 'modifiers': {'damagePerTurn': {'dice': '1d4', 'type': 'fire'}},
        'duration': {'rounds': 2, 'tickOn': 'target_turn_end'},
    }])
    state = _state(_p(ACTOR, 'player'), enemy)
    context = build_dm_combat_context(
        state=state,
        combat_context={'enemyRequiredActions': [{
            'enemyId': 'enemy_1', 'targetId': ACTOR, 'intentType': 'attack', 'abilityId': 'club',
        }]},
        pending_rolls=[], resolved_player_roll=False, turn_id=17,
    )
    result = context['enemyResolvedActions'][0]
    assert result['turnLost'] is True
    assert result['ongoingDamage'][0]['rolls'] == [4]
    assert 'attackRoll' not in result
    assert enemy['hp']['current'] == 3  # The preview does not mutate persisted state.
    applied = apply_state_changes(state, [{
        'id': 'advance-17', 'turnId': 17, 'type': 'combat.update', 'round': 2, 'turnIndex': 0,
        'turnBoundaries': [
            {'actorId': 'enemy_1', 'phase': 'start', 'round': 1},
            {'actorId': 'enemy_1', 'phase': 'end', 'round': 1},
        ],
    }])
    assert applied['nextState']['combat']['participants'][1]['hp']['current'] == 0
    assert applied['appliedChanges'][0]['turnBoundaryEvents'][0]['ongoingDamage'][0]['rolls'] == [4]


def _enemy_changes(state: dict, context: dict) -> list[dict]:
    return derive_trusted_damage_changes(
        state=state, dm_context_packet={'combatState': context}, actor_id=ACTOR, turn_id=55, already_applied_changes=[],
    ).enemy


def test_enemy_closes_into_melee_then_attacks():
    state = _state(_p(ACTOR, 'player'), _enemy())
    rolls = iter([15, 3])
    context = _enemy_turn(state, 'enemy_1', roller=lambda _sides: next(rolls))
    action = context['enemyResolvedActions'][0]
    assert action['movement'] == [{'fromRangeBand': 'near', 'toRangeBand': 'melee', 'engageWith': [ACTOR]}]
    assert (action['hit'], action['damageTotal']) == (True, 4)

    changes = _enemy_changes(state, context)
    assert [change['type'] for change in changes] == ['combat.move', 'combat.ability.mark_used', 'health.damage']
    after = _apply(state, changes)
    assert _participant(after, ACTOR)['position'] == {'rangeBand': 'melee', 'engagedWith': ['enemy_1']}
    assert _participant(after, 'enemy_1')['position'] == {'rangeBand': 'melee', 'engagedWith': [ACTOR]}
    narrated_retreat = [{'id': 'narrated_retreat', 'type': 'combat.move', 'participantId': 'enemy_1', 'toRangeBand': 'far'}]
    assert without_trusted_damage_overlaps(narrated_retreat, changes, dm_context_packet={'combatState': context}) == []


def test_enemy_too_far_to_strike_spends_its_turn_moving_two_bands():
    state = _state(_p(ACTOR, 'player'), _enemy(band='far'))

    def no_rolls(_sides):
        raise AssertionError('an enemy that only moves rolls nothing')

    context = _enemy_turn(state, 'enemy_1', roller=no_rolls)
    action = context['enemyResolvedActions'][0]
    assert action['moveOnly'] is True and 'attackTotal' not in action
    assert [step['toRangeBand'] for step in action['movement']] == ['near', 'melee']
    changes = _enemy_changes(state, context)
    assert [change['type'] for change in changes] == ['combat.move', 'combat.move']
    assert _participant(_apply(state, changes), 'enemy_1')['position']['engagedWith'] == [ACTOR]


def test_ranged_enemy_shoots_from_where_it_stands():
    state = _state(_p(ACTOR, 'player', band='far'), _enemy(band='far', abilities=[SHORTBOW]))
    rolls = iter([12, 2])
    action = _enemy_turn(state, 'enemy_1', ability_id='shortbow', roller=lambda _sides: next(rolls))['enemyResolvedActions'][0]
    assert 'movement' not in action
    assert action['attackTotal'] == 16


def test_enemy_targeting_respects_hidden_cover_and_range_bands():
    hidden = _p('player_2', 'player', conditions=['hidden'], position={'rangeBand': 'near', 'isHidden': True})
    covered = _p('player_3', 'player', position={'rangeBand': 'near', 'coverId': 'wall'})
    far = _p('player_4', 'player', band='far', hp=2)
    exposed = _p(ACTOR, 'player')
    state = _state(exposed, hidden, covered, far, _enemy(), battlefield={'cover': [{'id': 'wall', 'name': 'Wall', 'coverType': 'full'}]})
    plan = plan_enemy_intents(state['combat'])
    assert plan['intents'][0]['targetId'] == ACTOR
    assert set(plan['combatFactsByEnemy']['enemy_1']['visibleTargetIds']) == {ACTOR}

    archer_state = _state(_p(ACTOR, 'player', band='distant'), far, _enemy(abilities=[SHORTBOW]))
    archer_plan = plan_enemy_intents(archer_state['combat'])
    assert archer_plan['intents'][0]['targetId'] == 'player_4'
    assert archer_plan['intents'][0]['abilityId'] == 'shortbow'

    def no_rolls(_sides):
        raise AssertionError('a hidden target cannot be attacked')

    context = _enemy_turn(state, 'enemy_1', target_id='player_2', roller=no_rolls)
    assert context['enemyResolvedActions'] == [] and context['enemyResolutionBlockedCount'] == 1


MEND = {'id': 'mend', 'name': 'Mend', 'type': 'spell', 'targetType': 'single', 'healing': {'dice': '2d4'}, 'cooldown': 'none'}


def test_enemy_healing_heals_itself_or_its_most_wounded_ally():
    healer = _enemy('enemy_1', abilities=[MEND, CLUB])
    hurt = _enemy('enemy_2', hp=4)
    scratched = _enemy('enemy_3', hp=15)
    state = _state(_p(ACTOR, 'player'), healer, hurt, scratched)
    plan = plan_enemy_intents(state['combat'])
    healer_intent = next(intent for intent in plan['intents'] if intent['enemyId'] == 'enemy_1')
    assert (healer_intent['intentType'], healer_intent['abilityId'], healer_intent['targetId']) == ('use_ability', 'mend', 'enemy_2')

    context = _enemy_turn(state, 'enemy_1', target_id='enemy_2', ability_id='mend', intent='use_ability', roller=lambda _sides: 3)
    action = context['enemyResolvedActions'][0]
    assert (action['healTargetId'], action['healingTotal']) == ('enemy_2', 6)
    changes = _enemy_changes(state, context)
    assert [change['type'] for change in changes] == ['combat.ability.mark_used', 'combat.participant.update']
    assert _participant(_apply(state, changes), 'enemy_2')['hp']['current'] == 10

    selfish = _enemy('enemy_1', hp=5, abilities=[{**MEND, 'targetType': 'self'}])
    self_context = _enemy_turn(_state(_p(ACTOR, 'player'), selfish, _enemy('enemy_2', hp=1)), 'enemy_1', ability_id='mend', intent='use_ability', roller=lambda _sides: 4)
    assert self_context['enemyResolvedActions'][0]['healTargetId'] == 'enemy_1'


def test_retreating_enemy_moves_outward_before_it_flees():
    state = _state(_p(ACTOR, 'player'), _enemy(band='melee'))
    context = _enemy_turn(state, 'enemy_1', intent='retreat', ability_id='')
    action = context['enemyResolvedActions'][0]
    assert action['movement'] == [{'fromRangeBand': 'melee', 'toRangeBand': 'near', 'engageWith': []}]
    changes = _enemy_changes(state, context)
    assert [(change['type'], change.get('condition')) for change in changes] == [('combat.move', None), ('combat.condition.add', 'fled')]


# --- D. player projection --------------------------------------------------------


def test_projection_exposes_the_initiative_ribbon_and_visible_engagements_only():
    actor, enemy, lurker = _p(ACTOR, 'player'), _enemy(), _enemy('enemy_2', hiddenToPlayers=True)
    _engage(actor, enemy)
    _engage(actor, lurker)
    state = _state(actor, enemy, lurker)
    projected = with_combat_legal_actions(filter_session_snapshot_for_player(deepcopy(state), private_player_ids={1}), [_player()])
    combat = projected['combat']
    assert combat['turnOrder'] == [
        {'id': ACTOR, 'name': 'Player 1', 'team': 'player', 'isCurrent': True},
        {'id': 'enemy_1', 'name': 'Enemy 1', 'team': 'enemy', 'isCurrent': False},
    ]
    projected_actor = next(participant for participant in combat['participants'] if participant['id'] == ACTOR)
    assert projected_actor['position']['engagedWith'] == ['enemy_1']
    projected_enemy = next(participant for participant in combat['participants'] if participant['id'] == 'enemy_1')
    assert projected_enemy['position'] == {'rangeBand': 'melee', 'engagedWith': [ACTOR]}
    bundle = combat['legalActions'][0]
    assert {'attacksRemaining', 'reactionRemaining'} <= set(bundle)
    assert all({'action', 'bonusAction', 'movement', 'reaction'} == set(action['cost']) for action in bundle['actions'])
