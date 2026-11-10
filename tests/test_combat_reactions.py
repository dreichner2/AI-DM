from __future__ import annotations

from copy import deepcopy
import json

import pytest

import aidm_server.combat.attacks as combat_attacks
from aidm_server.combat.legal_actions import legal_combat_actions_for_player, resolve_combat_legal_action
from aidm_server.combat.pipeline import apply_turn_boundaries, prepare_combat_for_turn
from aidm_server.combat.reactions import (
    REACTION_SOURCE,
    can_react,
    collect_reactions,
    register_reaction_provider,
    unregister_reaction_provider,
)
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.orchestration.combat_resolution import (
    build_dm_combat_context,
    derive_trusted_damage_changes,
    resolve_authoritative_player_attack,
)
from aidm_server.game_state.validation.validator import validate_state_changes, validated_changes_for_application
from aidm_server.models import Campaign, DmTurn, Player, Session


ACTOR = 'player_1'
NO_LLM = {'tacticalLevel': 'simple', 'allowSentientEnemyBrain': False, 'allowFreeformEnemyTactics': False, 'maxLlmCallsPerRound': 0}
CLUB = {'id': 'club', 'name': 'Club', 'type': 'attack', 'range': 'melee', 'attackBonus': 3, 'damage': {'dice': '1d4+1', 'type': 'bludgeoning'}}


def _p(pid: str, team: str, *, band: str = 'near', hp: int = 20, **extra) -> dict:
    participant = {
        'id': pid,
        'name': pid.replace('_', ' ').title(),
        'team': team,
        'kind': 'player_character' if team == 'player' else 'creature',
        'hp': {'current': hp, 'max': 20, 'temp': 0},
        'armorClass': 12,
        'stats': {'strength': 14, 'dexterity': 12, 'wisdom': 10},
        'conditions': [],
        'position': {'rangeBand': band},
        **extra,
    }
    if team == 'player':
        participant['playerId'] = int(pid.split('_')[1])
    else:
        participant.setdefault('abilities', [deepcopy(CLUB)])
    return participant


def _engage(first: dict, second: dict) -> None:
    for one, other in ((first, second), (second, first)):
        one['position'] = {**one['position'], 'rangeBand': 'melee', 'engagedWith': [*one['position'].get('engagedWith', []), other['id']]}


def _state(*participants: dict, auto_reactions: bool = True) -> dict:
    actors = []
    for player in (participant for participant in participants if participant['team'] == 'player'):
        actor = {
            'id': player['id'],
            'playerId': player['playerId'],
            'name': player['name'],
            'level': 3,
            'health': {'currentHp': player['hp']['current'], 'maxHp': 20, 'conditions': []},
            'stats': {'strength': 14, 'dexterity': 12},
            'inventory': {'items': [{'id': 'blade', 'name': 'Longsword', 'type': 'weapon', 'subtype': 'longsword', 'equipped': True, 'slot': 'main_hand'}]},
            'metadata': {'weaponProficiencies': ['category:all']},
        }
        if not auto_reactions:
            actor['autoReactions'] = False
        actors.append(actor)
    return {
        'playerCharacters': actors,
        'combat': {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': list(participants),
            'initiative': [
                {'participantId': participant['id'], 'roll': 20 - index, 'modifier': 0, 'total': 20 - index, 'order': index}
                for index, participant in enumerate(participants)
            ],
            'flags': {'activeActorId': ACTOR, 'combatDifficultyAI': NO_LLM},
        },
    }


def _player() -> Player:
    return Player(
        player_id=1,
        character_name='Player 1',
        class_='Fighter',
        level=3,
        stats=json.dumps({'ability_scores': {'strength': 16, 'dexterity': 14}}),
        inventory=json.dumps([{'id': 'blade', 'name': 'Longsword', 'type': 'weapon', 'equipped': True, 'slot': 'main_hand'}]),
    )


def _participant(state: dict, participant_id: str) -> dict:
    return next(participant for participant in state['combat']['participants'] if participant['id'] == participant_id)


def _apply(state: dict, changes: list[dict]) -> dict:
    validation = validate_state_changes(state=state, changes=changes)
    assert validation['rejected'] == []
    return apply_state_changes(state, validated_changes_for_application(validation))['nextState']


def _take(state: dict, action_id: str, *, turn_id: int) -> tuple[dict, dict]:
    canonical, code, message = resolve_combat_legal_action(state, _player(), action_id=action_id)
    assert code is None, message
    result = prepare_combat_for_turn(
        state=state,
        session_obj=Session(session_id=1, campaign_id=1),
        campaign=Campaign(campaign_id=1, workspace_id='workspace'),
        turn=DmTurn(turn_id=turn_id, player_id=1, metadata_json=json.dumps({'action_intent': {'kind': 'combat', 'combat': canonical}})),
        player_message=canonical['message'],
        workspace_id='workspace',
    )
    return _apply(state, result['changes']), result


@pytest.fixture
def hits(monkeypatch):
    """Opportunity attacks roll 15 on the d20 and 3 on every damage die."""

    monkeypatch.setattr(combat_attacks, 'deterministic_roller', lambda _seed: (lambda sides: 15 if sides == 20 else 3))


@pytest.fixture
def provider():
    registered = []

    def register(fn):
        registered.append(register_reaction_provider(fn))
        return fn

    yield register
    for fn in registered:
        unregister_reaction_provider(fn)


def _enemy_block(state: dict, *actions: dict, roller=None) -> dict:
    return build_dm_combat_context(
        state=state,
        combat_context={'enemyRequiredActions': list(actions)},
        pending_rolls=[],
        resolved_player_roll=False,
        enemy_roller=roller,
        turn_id=60,
    )


def _enemy_changes(state: dict, context: dict) -> list[dict]:
    return derive_trusted_damage_changes(
        state=state, dm_context_packet={'combatState': context}, actor_id=ACTOR, turn_id=60, already_applied_changes=[],
    ).enemy


def test_reaction_returns_at_the_start_of_its_own_turn_and_every_reaction_spends_it(provider):
    guard = _p('enemy_1', 'enemy', reactionRemaining=0)
    combat = _state(_p(ACTOR, 'player'), guard)['combat']
    assert can_react(guard) is False
    apply_turn_boundaries(combat, [{'actorId': 'enemy_1', 'phase': 'start', 'round': 1}], turn_id=1)
    assert combat['participants'][1]['reactionRemaining'] == 1

    @provider
    def parry(event, _combat, _state):
        return [{'reactorId': 'enemy_1', 'kind': 'parry', 'label': 'Parry', 'damageMultiplier': 0.5}]

    event = {'type': 'attack_hit', 'attackerId': ACTOR, 'targetId': 'enemy_1', 'eventId': 'e1', 'turnId': 1}
    first = collect_reactions(event, combat, {})
    assert [reaction['kind'] for reaction in first] == ['parry']
    assert first[0]['changes'][0]['type'] == 'combat.reaction.use'
    assert combat['participants'][1]['reactionRemaining'] == 0
    assert collect_reactions({**event, 'eventId': 'e2'}, combat, {}) == []


def test_leaving_melee_while_engaged_provokes_an_opportunity_attack(hits):
    actor, enemy = _p(ACTOR, 'player'), _p('enemy_1', 'enemy')
    _engage(actor, enemy)
    state, result = _take(_state(actor, enemy), 'combat.reposition', turn_id=100)

    reactions = result['combatContext']['playerResolvedCombatAction']['reactions']
    assert [(reaction['reactorId'], reaction['kind'], reaction['hit'], reaction['damageTotal']) for reaction in reactions] == [
        ('enemy_1', 'opportunity_attack', True, 4),
    ]
    assert _participant(state, ACTOR)['hp']['current'] == 16
    assert state['playerCharacters'][0]['health']['currentHp'] == 16
    assert _participant(state, 'enemy_1')['reactionRemaining'] == 0
    assert _participant(state, ACTOR)['position'] == {'rangeBand': 'near'}
    assert _participant(state, 'enemy_1')['position'] == {'rangeBand': 'melee'}


def test_an_opportunity_attack_that_drops_the_mover_stops_the_move(hits):
    actor, enemy = _p(ACTOR, 'player', hp=3), _p('enemy_1', 'enemy')
    _engage(actor, enemy)
    state, result = _take(_state(actor, enemy), 'combat.reposition', turn_id=100)

    assert result['combatContext']['playerResolvedCombatAction']['movement']['toRangeBand'] == 'melee'
    downed = _participant(state, ACTOR)
    assert 'dying' in downed['conditions']
    assert downed['position']['rangeBand'] == 'melee'


def test_a_disengaged_mover_provokes_no_opportunity_attack(hits):
    actor, enemy = _p(ACTOR, 'player'), _p('enemy_1', 'enemy')
    _engage(actor, enemy)
    state, _result = _take(_state(actor, enemy), 'combat.disengage', turn_id=100)
    state, result = _take(state, 'combat.reposition', turn_id=101)

    assert 'reactions' not in result['combatContext']['playerResolvedCombatAction']
    assert _participant(state, ACTOR)['hp']['current'] == 20
    assert _participant(state, 'enemy_1').get('reactionRemaining', 1) == 1


@pytest.mark.parametrize(
    'blocker',
    [
        {'conditions': ['stunned']},
        {'conditions': ['incapacitated']},
        {'activeEffects': [{'id': 'daze', 'sourceId': 'spell_daze', 'modifiers': {'cannotReact': True}}]},
        {'reactionRemaining': 0},
    ],
)
def test_enemies_that_cannot_act_or_react_make_no_opportunity_attack(hits, blocker):
    actor, enemy = _p(ACTOR, 'player'), _p('enemy_1', 'enemy', **blocker)
    _engage(actor, enemy)
    state, result = _take(_state(actor, enemy), 'combat.reposition', turn_id=100)

    assert 'reactions' not in result['combatContext']['playerResolvedCombatAction']
    assert _participant(state, ACTOR)['hp']['current'] == 20
    assert _participant(state, ACTOR)['position']['rangeBand'] == 'near'


def test_player_characters_strike_a_retreating_enemy_unless_auto_reactions_are_off(hits):
    actor, enemy = _p(ACTOR, 'player'), _p('enemy_1', 'enemy')
    _engage(actor, enemy)
    retreat = {'enemyId': 'enemy_1', 'intentType': 'retreat'}
    state = _state(actor, enemy)
    context = _enemy_block(state, retreat)
    action = context['enemyResolvedActions'][0]
    assert [(reaction['reactorId'], reaction['kind'], reaction['attack']['weaponName']) for reaction in action['reactions']] == [
        (ACTOR, 'opportunity_attack', 'Longsword'),
    ]
    changes = _enemy_changes(state, context)
    assert [change['type'] for change in changes] == [
        'combat.move', 'combat.reaction.use', 'combat.participant.update', 'combat.condition.add',
    ]
    after = _apply(state, changes)
    assert _participant(after, 'enemy_1')['hp']['current'] == 15
    assert _participant(after, ACTOR)['reactionRemaining'] == 0
    assert 'fled' in _participant(after, 'enemy_1')['conditions']

    opted_out = _state(deepcopy(actor), deepcopy(enemy), auto_reactions=False)
    assert 'reactions' not in _enemy_block(opted_out, retreat)['enemyResolvedActions'][0]


def test_an_enemy_cut_down_while_retreating_does_not_flee(hits):
    actor, enemy = _p(ACTOR, 'player'), _p('enemy_1', 'enemy', hp=4)
    _engage(actor, enemy)
    state = _state(actor, enemy)
    context = _enemy_block(state, {'enemyId': 'enemy_1', 'intentType': 'retreat'})
    assert context['enemyResolvedActions'][0]['droppedByReaction'] is True
    changes = _enemy_changes(state, context)
    assert [change['type'] for change in changes] == ['combat.reaction.use', 'combat.participant.update']
    assert _participant(_apply(state, changes), 'enemy_1')['isAlive'] is False


def _attack(enemy_id: str) -> dict:
    return {'enemyId': enemy_id, 'targetId': ACTOR, 'intentType': 'attack', 'abilityId': 'club'}


def test_reaction_provider_contract_first_provider_wins_and_a_cancelled_hit_deals_nothing(provider):
    events = []

    @provider
    def shield(event, combat, state):
        events.append(dict(event))
        if event['type'] != 'attack_hit' or event['targetId'] != ACTOR:
            return []
        return [
            {
                'reactorId': ACTOR,
                'kind': 'shield',
                'label': 'Shield',
                'cancelsHit': True,
                'changes': [
                    {
                        'id': f"shield_{event['eventId']}",
                        'turnId': event['turnId'],
                        'type': 'combat.effect.add',
                        'participantId': ACTOR,
                        'source': REACTION_SOURCE,
                        'effect': {'id': 'shield_ward', 'sourceName': 'Shield', 'casterId': ACTOR, 'modifiers': {'acBonus': 5}},
                    }
                ],
                'consumes': {'reaction': 1, 'spellSlot': 1},
            }
        ]

    @provider
    def halve(event, combat, state):
        return [{'reactorId': ACTOR, 'kind': 'uncanny_dodge', 'label': 'Uncanny Dodge', 'damageMultiplier': 0.5}] if event['type'] == 'attack_hit' else []

    actor = _p(ACTOR, 'player', position={'rangeBand': 'melee'})
    state = _state(actor, _p('enemy_1', 'enemy', band='melee'), _p('enemy_2', 'enemy', band='melee'))
    context = _enemy_block(state, _attack('enemy_1'), _attack('enemy_2'), roller=lambda sides: 15 if sides == 20 else 2)
    first, second = context['enemyResolvedActions']

    assert first['hit'] is False and first['damageTotal'] == 0 and first['hitCancelledBy'] == 'Shield'
    assert [(reaction['kind'], reaction['consumes']) for reaction in first['reactions']] == [('shield', {'reaction': 1, 'spellSlot': 1})]
    assert first['targetArmorClass'] == 12 and second['targetArmorClass'] == 17
    assert second['hit'] is True and second['reactions'] == [] and second['damageTotal'] == 3
    hit_event = next(event for event in events if event['type'] == 'attack_hit')
    assert {'attackerId', 'targetId', 'attackTotal', 'targetAc', 'damage', 'critical'} <= set(hit_event)
    assert any(event['type'] == 'damage_taken' and event['amount'] == 3 for event in events)

    changes = _enemy_changes(state, context)
    assert [change['type'] for change in changes] == [
        'combat.reaction.use', 'combat.effect.add', 'combat.ability.mark_used', 'combat.ability.mark_used', 'health.damage',
    ]
    after = _apply(state, changes)
    assert _participant(after, ACTOR)['reactionRemaining'] == 0
    assert [effect['id'] for effect in _participant(after, ACTOR)['activeEffects']] == ['shield_ward']
    assert _participant(after, ACTOR)['hp']['current'] == 17


def test_a_damage_multiplier_reaction_halves_the_hit(provider):
    @provider
    def uncanny_dodge(event, combat, state):
        if event['type'] == 'attack_hit' and event['targetId'] == ACTOR:
            return [{'reactorId': ACTOR, 'kind': 'uncanny_dodge', 'label': 'Uncanny Dodge', 'damageMultiplier': 0.5}]
        return []

    state = _state(_p(ACTOR, 'player', position={'rangeBand': 'melee'}), _p('enemy_1', 'enemy', band='melee'))
    action = _enemy_block(state, _attack('enemy_1'), roller=lambda sides: 15 if sides == 20 else 4)['enemyResolvedActions'][0]
    assert (action['hit'], action['damageTotal']) == (True, 2)
    assert [reaction['kind'] for reaction in action['reactions']] == ['uncanny_dodge']


def test_player_attacks_raise_attack_hit_for_the_targets_reactions(provider):
    @provider
    def brace(event, combat, state):
        if event['type'] == 'attack_hit' and event['targetId'] == 'enemy_1':
            return [{'reactorId': 'enemy_1', 'kind': 'brace', 'label': 'Brace', 'damageMultiplier': 0.5}]
        return []

    actor, enemy = _p(ACTOR, 'player'), _p('enemy_1', 'enemy')
    _engage(actor, enemy)
    state = _state(actor, enemy)
    resolved = resolve_authoritative_player_attack(
        state=state,
        actor_id=ACTOR,
        turn_id=70,
        action_intent={
            'combat': {
                'authoritative': True, 'action_type': 'attack', 'target_id': 'enemy_1', 'weapon_id': 'blade',
                'weapon_name': 'Longsword', 'damage_dice': '1d8', 'damage_type': 'slashing',
            }
        },
        authoritative_roll={'authoritative': True, 'kept': 15, 'total': 20, 'modifier_breakdown': {'ability_modifier': 3}},
        damage_roller=lambda _sides: 5,
    )
    assert (resolved['hit'], resolved['damageTotal']) == (True, 4)
    assert resolved['reactions'][0]['reactorName'] == 'Enemy 1'
    trusted = derive_trusted_damage_changes(
        state=state,
        dm_context_packet={'combatState': {'playerResolvedAction': resolved, 'trustedDamageEvents': [resolved]}},
        actor_id=ACTOR,
        turn_id=70,
        already_applied_changes=[],
    ).resolved
    assert [change['type'] for change in trusted] == ['combat.participant.update', 'combat.reaction.use']
    assert _participant(_apply(state, trusted), 'enemy_1')['reactionRemaining'] == 0


def test_the_bundle_reports_the_reaction_the_character_has_left():
    actor = _p(ACTOR, 'player', reactionRemaining=0)
    bundle = legal_combat_actions_for_player(_state(actor, _p('enemy_1', 'enemy')), _player())
    assert bundle['reactionRemaining'] == 0
    assert bundle['economy']['reactionAvailable'] is False
