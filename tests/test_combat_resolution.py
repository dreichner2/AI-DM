from __future__ import annotations

from aidm_server.game_state.orchestration.combat_resolution import (
    build_dm_combat_context,
    combat_participant_update_signature,
    derive_trusted_damage_changes,
    without_trusted_damage_overlaps,
)
from aidm_server.combat.end_conditions import check_combat_end
from aidm_server.game_state.application.applier import apply_state_changes


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


def test_engine_resolved_targets_reject_narrated_hp_condition_and_consciousness_overrides():
    packet = {
        'combatState': {
            'enemyResolvedActions': [
                {
                    'enemyId': 'enemy_wolf',
                    'targetId': 'player_2',
                    'hit': True,
                    'damageTotal': 5,
                }
            ]
        }
    }
    proposed = [
        {'id': 'extra-damage', 'type': 'health.damage', 'actorId': 'player_2', 'amount': 2},
        {
            'id': 'narrated-death',
            'type': 'combat.participant.update',
            'participantId': 'player_2',
            'hp': {'current': 0, 'max': 10},
            'conditions': ['dead'],
            'isAlive': False,
            'isConscious': False,
        },
        {
            'id': 'narrated-condition',
            'type': 'combat.condition.add',
            'participantId': 'player_2',
            'condition': 'stunned',
        },
        {
            'id': 'allowed-morale',
            'type': 'combat.morale.update',
            'participantId': 'player_2',
            'morale': 25,
        },
    ]

    assert without_trusted_damage_overlaps(
        proposed,
        [{'id': 'engine-damage', 'type': 'health.damage', 'actorId': 'player_2', 'amount': 5}],
        dm_context_packet=packet,
    ) == [proposed[-1]]


def test_engine_owned_player_miss_rejects_narrated_condition_override():
    packet = {
        'combatState': {
            'playerResolvedAction': {
                'authoritative': True,
                'targetId': 'enemy_wolf',
                'hit': False,
                'damageTotal': 0,
            }
        }
    }

    assert without_trusted_damage_overlaps(
        [
            {
                'id': 'stun-on-miss',
                'type': 'combat.participant.update',
                'participantId': 'enemy_wolf',
                'conditions': ['stunned'],
                'isConscious': False,
            }
        ],
        [],
        dm_context_packet=packet,
    ) == []


def test_engine_resolved_enemy_retreat_applies_trusted_condition_and_nonlethal_end():
    state = {
        'playerCharacters': [{'id': 'player_1', 'name': 'Kael'}],
        'combat': {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [
                {
                    'id': 'player_1',
                    'name': 'Kael',
                    'team': 'player',
                    'kind': 'player_character',
                    'hp': {'current': 10, 'max': 10},
                    'isAlive': True,
                    'isConscious': True,
                },
                {
                    'id': 'enemy_wolf',
                    'name': 'Dire Wolf',
                    'team': 'enemy',
                    'kind': 'creature',
                    'hp': {'current': 3, 'max': 12},
                    'conditions': [],
                    'isAlive': True,
                    'isConscious': True,
                },
            ],
        },
    }
    packet = {
        'combatState': {
            'enemyResolvedActions': [
                {
                    'enemyId': 'enemy_wolf',
                    'intentType': 'retreat',
                    'resolvedWithoutRoll': True,
                }
            ]
        }
    }

    trusted = derive_trusted_damage_changes(
        state=state,
        dm_context_packet=packet,
        actor_id='player_1',
        turn_id=55,
        already_applied_changes=[],
    ).enemy
    next_state = apply_state_changes(state, trusted)['nextState']
    wolf = next(participant for participant in next_state['combat']['participants'] if participant['id'] == 'enemy_wolf')

    assert trusted[0]['type'] == 'combat.condition.add'
    assert trusted[0]['condition'] == 'fled'
    assert wolf['conditions'] == ['fled']
    assert wolf['hp']['current'] == 3
    assert check_combat_end(next_state['combat']) == 'enemies_fled'
    assert without_trusted_damage_overlaps(
        [
            {
                'id': 'narrated-fight-on',
                'type': 'combat.participant.update',
                'participantId': 'enemy_wolf',
                'conditions': [],
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'narrated-end',
                'type': 'combat.end',
                'status': 'ended',
                'endReason': 'all_enemies_defeated',
            },
        ],
        trusted,
        dm_context_packet=packet,
    ) == []
