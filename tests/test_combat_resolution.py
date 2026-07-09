from __future__ import annotations

from aidm_server.game_state.orchestration.combat_resolution import (
    build_dm_combat_context,
    combat_participant_update_signature,
    derive_trusted_damage_changes,
    without_trusted_damage_overlaps,
)


def _combat_state() -> dict:
    return {
        'playerCharacters': [
            {'id': 'player_1', 'name': 'Kael'},
            {'id': 'player_2', 'name': 'Mira'},
        ],
        'combat': {
            'participants': [
                {'id': 'player_1', 'name': 'Kael', 'team': 'player', 'armorClass': 13},
                {'id': 'player_2', 'name': 'Mira', 'team': 'player', 'armorClass': 12},
                {
                    'id': 'enemy_wolf',
                    'name': 'Dire Wolf',
                    'team': 'enemy',
                    'level': 2,
                    'stats': {'strength': 14, 'dexterity': 12},
                    'abilities': [
                        {
                            'id': 'bite',
                            'name': 'Bite',
                            'type': 'attack',
                            'attackBonus': 4,
                            'damage': {'dice': '1d6+2', 'type': 'piercing'},
                        }
                    ],
                },
            ],
            'battlefield': {
                'hazards': [
                    {'id': 'burning_floor', 'name': 'Burning Floor'},
                ]
            },
        },
    }


def test_build_dm_combat_context_resolves_engine_owned_enemy_action_without_mutating_input():
    state = _combat_state()
    combat_context = {
        'enemyRequiredActions': [
            {
                'enemyId': 'enemy_wolf',
                'targetId': 'player_2',
                'intentType': 'attack',
                'abilityId': 'bite',
            }
        ]
    }
    rolls = iter([15, 3])

    resolved = build_dm_combat_context(
        state=state,
        combat_context=combat_context,
        pending_rolls=[],
        resolved_player_roll=False,
        enemy_roller=lambda _sides: next(rolls),
    )

    assert resolved is not None
    action = resolved['enemyResolvedActions'][0]
    assert action['attackRoll'] == 15
    assert action['attackTotal'] == 19
    assert action['targetArmorClass'] == 12
    assert action['hit'] is True
    assert action['damageRolls'] == [3]
    assert action['damageTotal'] == 5
    assert action['damageType'] == 'piercing'
    assert 'enemyResolvedActions' not in combat_context


def test_build_dm_combat_context_defers_enemy_action_for_pending_player_roll():
    combat_context = {
        'enemyRequiredActions': [{'enemyId': 'enemy_wolf', 'targetId': 'player_2'}],
        'enemyIntentSummary': 'The wolf lunges.',
        'enemyTelegraphs': ['The wolf lowers its head.'],
    }

    deferred = build_dm_combat_context(
        state=_combat_state(),
        combat_context=combat_context,
        pending_rolls=[{'actorId': 'player_1', 'rollType': 'dexterity'}],
        resolved_player_roll=False,
    )

    assert deferred is not None
    assert deferred['enemyRequiredActions'] == []
    assert deferred['enemyResolvedActions'] == []
    assert deferred['enemyActionDeferredReason'] == 'pending_player_roll'
    assert combat_context['enemyRequiredActions'] != []


def test_derive_trusted_damage_changes_separates_enemy_and_validated_event_sources():
    context = {
        'combatState': {
            'enemyResolvedActions': [
                {
                    'enemyId': 'enemy_wolf',
                    'targetId': 'player_2',
                    'abilityId': 'bite',
                    'hit': True,
                    'damageTotal': 5,
                    'damageType': 'piercing',
                }
            ]
        },
        'trustedDamageEvents': [
            {
                'sourceType': 'player_attack',
                'sourceActorId': 'player_1',
                'targetId': 'player_2',
                'damageTotal': 4,
                'damageType': 'slashing',
            },
            {
                'sourceType': 'environmental_hazard',
                'hazardId': 'burning_floor',
                'targetId': 'player_2',
                'damageTotal': 3,
                'damageType': 'fire',
            },
            {
                'sourceType': 'player_attack',
                'sourceActorId': 'player_2',
                'targetId': 'player_1',
                'damageTotal': 99,
            },
        ],
    }

    changes = derive_trusted_damage_changes(
        state=_combat_state(),
        dm_context_packet=context,
        actor_id='player_1',
        turn_id=42,
        already_applied_changes=[],
    )

    assert len(changes.enemy) == 1
    assert changes.enemy[0]['source'] == 'enemy_resolved_action'
    assert changes.enemy[0]['actorId'] == 'player_2'
    assert changes.enemy[0]['amount'] == 5
    assert [change['source'] for change in changes.resolved] == [
        'trusted_player_attack',
        'trusted_environmental_hazard',
    ]
    assert [change['amount'] for change in changes.all_changes] == [5, 4, 3]


def test_combat_signatures_and_overlap_filter_are_semantic():
    first = combat_participant_update_signature(
        {
            'hp': {'current': 7, 'max': 10, 'temp': 1},
            'conditions': ['Prone', 'Poisoned'],
            'isConscious': True,
        }
    )
    reordered = combat_participant_update_signature(
        {
            'hp': {'maxHp': 10, 'currentHp': 7, 'tempHp': 1},
            'conditions': ['poisoned', 'prone'],
            'isConscious': True,
        }
    )
    assert first == reordered

    trusted = [{'id': 'trusted-1', 'type': 'health.damage', 'actorId': 'player_2', 'amount': 5}]
    filtered = without_trusted_damage_overlaps(
        [
            {'id': 'helper-copy', 'type': 'health.damage', 'actorId': 'player_2', 'amount': 5},
            {'id': 'trusted-1', 'type': 'health.damage', 'actorId': 'player_2', 'amount': 8},
            {'id': 'different', 'type': 'health.damage', 'actorId': 'player_2', 'amount': 2},
        ],
        trusted,
    )

    assert filtered == [
        {'id': 'different', 'type': 'health.damage', 'actorId': 'player_2', 'amount': 2}
    ]
