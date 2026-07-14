from __future__ import annotations

from aidm_server.combat.end_conditions import check_combat_end
from aidm_server.combat.intent_planner import plan_enemy_intents
from aidm_server.combat.legal_actions import legal_combat_actions_for_player
from aidm_server.combat.state import consume_combat_turn_economy
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.orchestration.combat_resolution import (
    build_dm_combat_context,
    derive_trusted_damage_changes,
)
from aidm_server.models import Player


def _participant(
    participant_id: str,
    team: str,
    *,
    hp: int = 10,
    conditions: list[str] | None = None,
    present: bool = True,
    conscious: bool = True,
) -> dict:
    return {
        'id': participant_id,
        'name': participant_id,
        'team': team,
        'kind': 'player_character' if team == 'player' else 'creature',
        'hp': {'current': hp, 'max': 10},
        'armorClass': 10,
        'stats': {'strength': 10, 'dexterity': 10, 'wisdom': 10},
        'conditions': list(conditions or []),
        'position': {'rangeBand': 'near', 'zoneId': 'room'},
        'isAlive': hp > 0,
        'isConscious': conscious,
        'isPresent': present,
    }


def _attack(ability_id: str = 'club') -> dict:
    return {
        'id': ability_id,
        'name': ability_id.title(),
        'type': 'attack',
        'attackBonus': 3,
        'damage': {'dice': '1d4+1', 'type': 'bludgeoning'},
        'cooldown': 'none',
    }


def _mindless_behavior() -> dict:
    return {
        'intelligenceProfile': 'mindless',
        'primaryGoal': 'kill_party',
        'aggression': 100,
        'selfPreservation': 0,
        'morale': 100,
        'survivalRules': {'fightToDeath': True},
    }


def test_terminal_participants_are_neither_planned_actors_nor_targets():
    active_enemy = _participant('enemy_active', 'enemy')
    active_enemy.update({'abilities': [_attack()], 'behavior': _mindless_behavior()})
    surrendered_enemy = _participant('enemy_surrendered', 'enemy', conditions=['surrendered'])
    surrendered_enemy.update({'abilities': [_attack()], 'behavior': _mindless_behavior()})
    conscious_player = _participant('player_conscious', 'player')
    conscious_player['armorClass'] = 18
    unconscious_player = _participant('player_unconscious', 'player', conscious=False)
    unconscious_player['armorClass'] = 1
    absent_player = _participant('player_absent', 'player', present=False)
    absent_player['armorClass'] = 1

    plan = plan_enemy_intents(
        {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [
                active_enemy,
                surrendered_enemy,
                conscious_player,
                unconscious_player,
                absent_player,
            ],
        }
    )

    assert [intent['enemyId'] for intent in plan['intents']] == ['enemy_active']
    assert plan['intents'][0]['targetId'] == 'player_conscious'
    assert plan['combatFacts']['livingPlayers'] == 1


def test_used_once_per_combat_ability_is_not_planned_or_resolved_again():
    enemy = _participant('enemy_cultist', 'enemy')
    enemy.update(
        {
            'abilities': [
                {
                    'id': 'dark_hex',
                    'name': 'Dark Hex',
                    'type': 'spell',
                    'damage': {'dice': '1d6', 'type': 'necrotic'},
                    'save': {'ability': 'wis', 'dc': 11, 'effectOnSuccess': 'half_damage'},
                    'cooldown': 'once_per_combat',
                    'used': True,
                },
                _attack('dagger'),
            ],
            'behavior': _mindless_behavior(),
        }
    )
    player = _participant('player_1', 'player')
    state = {
        'playerCharacters': [{'id': 'player_1', 'name': 'Hero', 'health': {'currentHp': 10, 'maxHp': 10}}],
        'combat': {'status': 'active', 'round': 2, 'turnIndex': 0, 'participants': [enemy, player]},
    }

    plan = plan_enemy_intents(state['combat'])
    assert plan['intents'][0].get('abilityId') == 'dagger'
    assert all(
        candidate.get('abilityId') != 'dark_hex'
        for candidate in plan['intentCandidates']['enemy_cultist']
    )

    context = build_dm_combat_context(
        state=state,
        combat_context={
            'enemyTurnBlock': [{'id': 'enemy_cultist'}],
            'enemyRequiredActions': [
                {
                    'enemyId': 'enemy_cultist',
                    'targetId': 'player_1',
                    'intentType': 'use_ability',
                    'abilityId': 'dark_hex',
                }
            ],
        },
        pending_rolls=[],
        resolved_player_roll=False,
    )
    assert context['enemyRequiredActions'] == []
    assert context['enemyResolvedActions'] == []
    assert context['enemyResolutionBlockedCount'] == 1


def test_enemy_save_condition_is_authoritative_and_consumes_limited_ability():
    enemy = _participant('enemy_spider', 'enemy')
    enemy['abilities'] = [
        {
            'id': 'web',
            'name': 'Web',
            'type': 'special',
            'conditionsApplied': ['restrained'],
            'save': {'ability': 'dex', 'dc': 12, 'effectOnSuccess': 'none'},
            'cooldown': 'once_per_combat',
        }
    ]
    player = _participant('player_1', 'player')
    state = {
        'playerCharacters': [
            {'id': 'player_1', 'name': 'Hero', 'health': {'currentHp': 10, 'maxHp': 10, 'conditions': []}}
        ],
        'combat': {'status': 'active', 'round': 1, 'turnIndex': 0, 'participants': [enemy, player]},
    }
    context = build_dm_combat_context(
        state=state,
        combat_context={
            'enemyTurnBlock': [{'id': 'enemy_spider'}],
            'enemyRequiredActions': [
                {
                    'enemyId': 'enemy_spider',
                    'targetId': 'player_1',
                    'intentType': 'use_ability',
                    'abilityId': 'web',
                }
            ],
        },
        pending_rolls=[],
        resolved_player_roll=False,
        enemy_roller=lambda _sides: 5,
    )

    resolved = context['enemyResolvedActions'][0]
    assert resolved['resolutionMode'] == 'save'
    assert resolved['saveSucceeded'] is False
    assert resolved['conditionsApplied'] == ['restrained']
    assert resolved['damageTotal'] == 0
    assert resolved['damageDice'] is None

    changes = derive_trusted_damage_changes(
        state=state,
        dm_context_packet={'combatState': context},
        actor_id='player_1',
        turn_id=77,
        already_applied_changes=[],
    ).enemy
    assert [change['type'] for change in changes] == [
        'combat.ability.mark_used',
        'combat.condition.add',
    ]

    next_state = apply_state_changes(state, changes)['nextState']
    next_enemy = next(item for item in next_state['combat']['participants'] if item['id'] == 'enemy_spider')
    next_player = next(item for item in next_state['combat']['participants'] if item['id'] == 'player_1')
    assert next_enemy['abilities'][0]['used'] is True
    assert next_player['conditions'] == ['restrained']
    assert next_state['playerCharacters'][0]['health']['conditions'] == ['restrained']


def test_underspecified_nondamaging_enemy_ability_fails_closed():
    enemy = _participant('enemy_shouter', 'enemy')
    enemy['abilities'] = [
        {'id': 'ominous_roar', 'name': 'Ominous Roar', 'type': 'special', 'cooldown': 'once_per_combat'}
    ]
    player = _participant('player_1', 'player')
    state = {
        'playerCharacters': [{'id': 'player_1', 'name': 'Hero'}],
        'combat': {'status': 'active', 'round': 1, 'turnIndex': 0, 'participants': [enemy, player]},
    }

    context = build_dm_combat_context(
        state=state,
        combat_context={
            'enemyTurnBlock': [{'id': 'enemy_shouter'}],
            'enemyRequiredActions': [
                {
                    'enemyId': 'enemy_shouter',
                    'targetId': 'player_1',
                    'intentType': 'use_ability',
                    'abilityId': 'ominous_roar',
                }
            ],
        },
        pending_rolls=[],
        resolved_player_roll=False,
        enemy_roller=lambda sides: sides,
    )

    assert context['enemyRequiredActions'] == []
    assert context['enemyResolvedActions'] == []
    assert context['enemyResolutionBlockedCount'] == 1


def test_repeated_server_action_id_is_an_idempotent_economy_replay():
    combat = {'status': 'active', 'round': 1, 'flags': {}, 'participants': []}
    first = consume_combat_turn_economy(
        combat,
        actor_id='player_1',
        action_id='combat.attack.sword',
        action_claim='target:enemy_1',
        action_cost=1,
    )
    replay = consume_combat_turn_economy(
        combat,
        actor_id='player_1',
        action_id='combat.attack.sword',
        action_claim='target:enemy_1',
        action_cost=1,
    )

    assert first[0] is True
    assert replay[0] is True
    assert combat['flags']['turnEconomy']['actionRemaining'] == 0
    assert combat['flags']['turnEconomy']['spentActionIds'] == ['combat.attack.sword']

    conflict = consume_combat_turn_economy(
        combat,
        actor_id='player_1',
        action_id='combat.attack.sword',
        action_claim='target:enemy_2',
        action_cost=1,
    )
    assert conflict[:2] == (
        False,
        'This action id is already bound to a different combat action.',
    )


def test_end_conditions_do_not_treat_disarmed_as_surrender_or_absence_as_active():
    player = _participant('player_1', 'player')
    disarmed = _participant('enemy_disarmed', 'enemy', conditions=['disarmed'])
    assert check_combat_end({'status': 'active', 'participants': [player, disarmed]}) is None

    defeated = _participant('enemy_defeated', 'enemy', hp=0, conscious=False)
    absent = _participant('enemy_reserve', 'enemy', present=False)
    assert check_combat_end({'status': 'active', 'participants': [player, defeated, absent]}) == 'all_enemies_defeated'
    assert check_combat_end({'status': 'active', 'participants': [player, absent]}) == 'enemies_fled'


def test_legacy_active_combat_without_turn_index_still_projects_legal_actions():
    player = _participant('player_1', 'player')
    player['playerId'] = 1
    enemy = _participant('enemy_1', 'enemy')
    snapshot = {
        'combat': {
            'status': 'active',
            'round': 1,
            'participants': [player, enemy],
            'initiative': [
                {'participantId': 'player_1', 'total': 18, 'order': 0},
                {'participantId': 'enemy_1', 'total': 10, 'order': 1},
            ],
            'flags': {},
        }
    }
    player_record = Player(
        player_id=1,
        name='Player',
        character_name='player_1',
        stats='{}',
        inventory='[]',
        weapon_proficiencies='[]',
    )

    bundle = legal_combat_actions_for_player(snapshot, player_record)

    assert bundle is not None
    assert bundle['currentActorId'] == 'player_1'
    assert bundle['isCurrentActor'] is True
    assert any(action['available'] for action in bundle['actions'])
