from __future__ import annotations

from unittest.mock import patch

from aidm_server.game_state.application.applier import apply_state_changes


def _state_with_concentration() -> dict:
    concentration = {
        'active': True,
        'spellId': 'spell_entangle',
        'spellName': 'Entangle',
        'casterActorId': 'player_1',
        'targetIds': ['enemy_1'],
    }
    return {
        'currentScene': {
            'locationId': 'glade',
            'sceneType': 'combat',
            'combatState': 'active',
        },
        'playerCharacters': [
            {
                'id': 'player_1',
                'playerId': 1,
                'name': 'Rowan',
                'stats': {'constitution': 10},
                'health': {
                    'currentHp': 18,
                    'maxHp': 18,
                    'tempHp': 0,
                    'conditions': [],
                },
                'spellResources': {
                    'revision': 4,
                    'slots': {'1': {'current': 0, 'max': 2}},
                    'concentration': dict(concentration),
                },
            }
        ],
        'combat': {
            'status': 'active',
            'round': 2,
            'turnIndex': 0,
            'participants': [
                {
                    'id': 'player_1',
                    'name': 'Rowan',
                    'team': 'player',
                    'kind': 'player_character',
                    'stats': {'constitution': 10},
                    'hp': {'current': 18, 'max': 18, 'temp': 0},
                    'conditions': [],
                    'isAlive': True,
                    'isConscious': True,
                    'isPresent': True,
                },
                {
                    'id': 'enemy_1',
                    'name': 'Mire Wolf',
                    'team': 'enemy',
                    'kind': 'creature',
                    'hp': {'current': 12, 'max': 12, 'temp': 0},
                    'conditions': ['restrained'],
                    'activeEffects': [
                        {
                            'id': 'entangle_restrained_enemy_1',
                            'kind': 'condition',
                            'operation': 'add',
                            'condition': 'restrained',
                            'sourceActorId': 'player_1',
                            'sourceSpellId': 'spell_entangle',
                            'concentration': True,
                            'duration': {'kind': 'concentration'},
                        }
                    ],
                    'isAlive': True,
                    'isConscious': True,
                    'isPresent': True,
                },
            ],
            'flags': {'activeActorId': 'player_1'},
        },
        'stateChangeLedger': [],
    }


def test_authoritative_damage_resolves_one_concentration_check_and_retry_is_idempotent() -> None:
    state = _state_with_concentration()
    damage = {
        'id': 'damage_player_1_round_2',
        'type': 'health.damage',
        'actorId': 'player_1',
        'amount': 7,
        'turnId': 22,
        'source': 'combat_resolution',
    }

    # A natural 1 fails the DC 10 Constitution save.
    with patch('aidm_server.game_state.application.applier.secrets.randbelow', return_value=0) as roller:
        applied = apply_state_changes(state, [damage])

    assert roller.call_count == 1
    next_state = applied['nextState']
    assert next_state['playerCharacters'][0]['health']['currentHp'] == 11
    assert next_state['playerCharacters'][0]['spellResources']['concentration'] is None
    enemy = next_state['combat']['participants'][1]
    assert enemy['conditions'] == []
    assert enemy['activeEffects'] == []
    check = applied['appliedChanges'][0]['concentrationCheck']
    assert check['required'] is True
    assert check['maintained'] is False
    assert check['check']['dc'] == 10

    with patch('aidm_server.game_state.application.applier.secrets.randbelow') as retry_roller:
        retried = apply_state_changes(next_state, [damage])

    assert retry_roller.call_count == 0
    assert retried['nextState']['playerCharacters'][0]['health']['currentHp'] == 11
    assert retried['nextState']['combat']['participants'][1]['conditions'] == []


def test_turn_advance_expires_target_timed_effect_and_syncs_player_condition() -> None:
    state = _state_with_concentration()
    actor = state['playerCharacters'][0]
    actor['health']['conditions'] = ['slowed']
    player = state['combat']['participants'][0]
    player['conditions'] = ['slowed']
    player['activeEffects'] = [
        {
            'id': 'ray_of_frost_slow_player_1',
            'kind': 'condition',
            'operation': 'add',
            'condition': 'slowed',
            'sourceActorId': 'enemy_1',
            'sourceSpellId': 'spell_ray_of_frost',
            'duration': {'remaining': 1, 'tick': 'target_turn_end'},
        }
    ]
    update = {
        'id': 'advance_from_player_1_round_2',
        'type': 'combat.update',
        'round': 2,
        'turnIndex': 1,
        'flags': {'activeActorId': 'enemy_1'},
        'turnId': 22,
        'source': 'combat_turn_engine',
    }

    applied = apply_state_changes(state, [update])

    next_state = applied['nextState']
    player_after = next_state['combat']['participants'][0]
    assert player_after['activeEffects'] == []
    assert player_after['conditions'] == []
    assert next_state['playerCharacters'][0]['health']['conditions'] == []
    assert next_state['combat']['flags']['activeActorId'] == 'enemy_1'
