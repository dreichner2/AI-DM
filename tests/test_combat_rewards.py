from __future__ import annotations

from copy import deepcopy

import pytest

from aidm_server.combat.rewards import canonical_combat_outcome, derive_combat_outcome_rewards
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.quest_engine import derive_quest_changes
from aidm_server.game_state.validation.validator import validate_state_changes


def _state(end_reason: str = 'all_enemies_defeated') -> dict:
    return {
        'activePlayerIds': [1, 2, 3],
        'playerCharacters': [
            {
                'id': 'player_2',
                'playerId': 2,
                'name': 'Bryn',
                'xp': {'current': 0},
                'inventory': {'items': [], 'currency': {'gp': 0}},
            },
            {
                'id': 'player_1',
                'playerId': 1,
                'name': 'Aria',
                'xp': {'current': 0},
                'inventory': {'items': [], 'currency': {'gp': 0}},
            },
            {
                'id': 'player_3',
                'playerId': 3,
                'name': 'Cato',
                'xp': {'current': 0},
                'inventory': {'items': [], 'currency': {'gp': 0}},
            },
        ],
        'combat': {
            'status': 'ended',
            'round': 4,
            'participants': [
                {
                    'id': 'player_2',
                    'team': 'player',
                    'isAlive': True,
                    'isConscious': False,
                    'isPresent': True,
                    'conditions': ['unconscious'],
                    'hp': {'current': 0, 'max': 15},
                },
                {
                    'id': 'player_1',
                    'team': 'player',
                    'isAlive': True,
                    'isConscious': True,
                    'isPresent': True,
                    'conditions': [],
                    'hp': {'current': 9, 'max': 12},
                },
                {
                    'id': 'player_3',
                    'team': 'player',
                    'isAlive': True,
                    'isConscious': True,
                    'isPresent': False,
                    'conditions': ['absent'],
                    'hp': {'current': 11, 'max': 11},
                },
                {
                    'id': 'enemy_wolf',
                    'team': 'enemy',
                    'isAlive': False,
                    'isConscious': False,
                    'isPresent': True,
                    'conditions': ['defeated'],
                    'hp': {'current': 0, 'max': 8},
                },
            ],
            'flags': {
                'endReason': end_reason,
                'campaignPackEncounterId': 'enc_bridge',
            },
        },
        'campaignPack': {
            'packId': 'pack_greenway',
            'catalog': {
                'encounters': [
                    {
                        'id': 'enc_bridge',
                        'title': 'Battle at the Bridge',
                        'questIds': ['quest_bridge'],
                    }
                ]
            },
        },
        'flags': {},
        'quests': [],
        'stateChangeLedger': [],
    }


def _end_change(end_reason: str = 'all_enemies_defeated') -> dict:
    return {
        'id': 'chg_combat_end_turn_77',
        'turnId': 77,
        'type': 'combat.end',
        'status': 'ended',
        'endReason': end_reason,
        'encounterId': 'enc_bridge',
    }


@pytest.mark.parametrize(
    ('end_reason', 'outcome'),
    [
        ('all_enemies_defeated', 'victory'),
        ('enemies_fled', 'victory'),
        ('objective_failed', 'defeat'),
        ('players_fled', 'retreat'),
        ('enemies_surrendered', 'surrender'),
        ('negotiated_resolution', 'negotiation'),
        ('objective_completed', 'objective_completion'),
        ('allEnemiesDefeated', 'victory'),
    ],
)
def test_canonical_outcome_matches_authoritative_combat_end_reasons(end_reason: str, outcome: str) -> None:
    assert canonical_combat_outcome(end_reason) == outcome

    state = _state(end_reason)
    result = derive_combat_outcome_rewards(state, _end_change(end_reason))

    assert result['valid'] is True
    assert result['outcome'] == outcome


def test_victory_rewards_split_party_totals_and_keep_global_outputs_exact_once() -> None:
    state = _state()
    encounter = {
        'id': 'enc_bridge',
        'title': 'Battle at the Bridge',
        'questIds': ['quest_bridge'],
        'rewards': {
            'xp': 101,
            'gp': 5,
            'items': [{'id': 'loot_bridge_key', 'name': 'Bridge Key', 'type': 'quest', 'quantity': 1}],
            'flags': [{'flagKey': 'bridge_secured', 'flagValue': True}],
        },
        'outcomes': {
            'victory': {
                'consequences': [
                    {'type': 'flag.set', 'flagKey': 'wolves_scattered', 'flagValue': True},
                ]
            }
        },
    }
    before_state = deepcopy(state)
    before_encounter = deepcopy(encounter)

    result = derive_combat_outcome_rewards(state, _end_change(), encounter=encounter)

    assert result['valid'] is True
    assert result['eligibleActorIds'] == ['player_1', 'player_2']
    assert state == before_state
    assert encounter == before_encounter
    assert len(result['ledgerIds']) == len(set(result['ledgerIds']))

    xp = {change['actorId']: change['amount'] for change in result['changes'] if change['type'] == 'xp.add'}
    gp = {change['actorId']: change['amount'] for change in result['changes'] if change['type'] == 'currency.add'}
    assert xp == {'player_1': 51, 'player_2': 50}
    assert gp == {'player_1': 3, 'player_2': 2}
    assert sum(xp.values()) == 101
    assert sum(gp.values()) == 5

    loot = [change for change in result['changes'] if change['type'] == 'inventory.add']
    assert len(loot) == 1
    assert loot[0]['actorId'] == 'player_1'
    assert loot[0]['item']['sourceItemId'] == 'loot_bridge_key'
    assert loot[0]['item']['id'].startswith('itm_instance_')
    assert loot[0]['itemId'] == loot[0]['item']['id']

    flags = {change['flagKey']: change['flagValue'] for change in result['changes'] if change['type'] == 'flag.set'}
    assert flags == {'bridge_secured': True, 'wolves_scattered': True}
    assert len(result['questEvents']) == 1
    event = result['questEvents'][0]
    assert event['id'].startswith('chg_')
    assert event['turnId'] == 77
    assert event['source'] == 'combat_reward_engine'
    assert event['visible'] is True
    assert event['type'] == 'combat.outcome'
    assert event['questId'] == 'quest_bridge'
    assert event['encounterId'] == 'enc_bridge'
    assert all(change['source'] == 'combat_reward_engine' for change in result['changes'])


def test_each_actor_and_exact_actor_allocations_are_distinct_and_stable() -> None:
    encounter = {
        'id': 'enc_bridge',
        'outcomeRewards': {
            'victory': [
                {'type': 'xp', 'amount': 25, 'allocation': 'each'},
                {'type': 'currency', 'currency': 'sp', 'amount': 7, 'actorId': 'player_2'},
                {
                    'type': 'item',
                    'allocation': 'each',
                    'item': {'id': 'field_ration', 'name': 'Field Ration', 'type': 'consumable', 'quantity': 2},
                },
            ]
        },
    }

    first = derive_combat_outcome_rewards(_state(), _end_change(), encounter=encounter)
    second = derive_combat_outcome_rewards(_state(), _end_change(), encounter=deepcopy(encounter))

    assert first == second
    xp = [change for change in first['changes'] if change['type'] == 'xp.add']
    assert [(change['actorId'], change['amount']) for change in xp] == [('player_1', 25), ('player_2', 25)]
    currency = [change for change in first['changes'] if change['type'] == 'currency.add']
    assert [(change['actorId'], change['currency'], change['amount']) for change in currency] == [
        ('player_2', 'sp', 7)
    ]
    items = [change for change in first['changes'] if change['type'] == 'inventory.add']
    assert [(change['actorId'], change['quantity']) for change in items] == [('player_1', 2), ('player_2', 2)]
    assert len({change['itemId'] for change in items}) == 2
    assert all(change['item']['sourceItemId'] == 'field_ration' for change in items)


def test_split_item_quantity_preserves_total_and_never_duplicates_identity() -> None:
    encounter = {
        'id': 'enc_bridge',
        'rewards': [
            {
                'type': 'item',
                'allocation': 'split',
                'item': {'id': 'wolf_pelt', 'name': 'Wolf Pelt', 'quantity': 3},
            }
        ],
    }

    result = derive_combat_outcome_rewards(_state(), _end_change(), encounter=encounter)
    items = [change for change in result['changes'] if change['type'] == 'inventory.add']

    assert [(change['actorId'], change['quantity']) for change in items] == [('player_1', 2), ('player_2', 1)]
    assert sum(change['quantity'] for change in items) == 3
    assert len({change['itemId'] for change in items}) == len(items)


def test_negative_outcome_uses_only_explicit_outcome_rewards_and_consequences() -> None:
    state = _state('objective_failed')
    encounter = {
        'id': 'enc_bridge',
        'rewards': {'xp': 100, 'gp': 20},
        'outcomes': {
            'defeat': {
                'rewards': [{'type': 'xp', 'amount': 10, 'allocation': 'each'}],
                'consequences': [
                    {'type': 'flag.set', 'flagKey': 'bridge_lost', 'flagValue': True},
                ],
            }
        },
    }

    result = derive_combat_outcome_rewards(state, _end_change('objective_failed'), encounter=encounter)

    assert result['outcome'] == 'defeat'
    xp = [change for change in result['changes'] if change['type'] == 'xp.add']
    assert [(change['actorId'], change['amount']) for change in xp] == [('player_1', 10), ('player_2', 10)]
    assert not any(change['type'] == 'currency.add' for change in result['changes'])
    assert next(change for change in result['changes'] if change['type'] == 'flag.set')['flagKey'] == 'bridge_lost'


def test_named_retreat_hook_applies_a_consequence_but_not_default_success_rewards() -> None:
    state = _state('players_fled')
    encounter = {
        'id': 'enc_bridge',
        'rewards': {'xp': 100},
        'onRetreat': [{'type': 'flag.set', 'flagKey': 'bridge_abandoned', 'flagValue': True}],
    }

    result = derive_combat_outcome_rewards(state, _end_change('players_fled'), encounter=encounter)

    assert result['outcome'] == 'retreat'
    assert [(change['type'], change.get('flagKey')) for change in result['changes']] == [
        ('flag.set', 'bridge_abandoned')
    ]


@pytest.mark.parametrize(
    ('end_reason', 'outcome'),
    [
        ('all_enemies_defeated', 'victory'),
        ('objective_failed', 'defeat'),
        ('players_fled', 'retreat'),
        ('enemies_surrendered', 'surrender'),
        ('negotiated_resolution', 'negotiation'),
        ('objective_completed', 'objective_completion'),
    ],
)
def test_every_supported_outcome_can_emit_its_authored_consequence(end_reason: str, outcome: str) -> None:
    state = _state(end_reason)
    encounter = {
        'id': 'enc_bridge',
        'outcomeConsequences': {
            outcome: [{'type': 'flag.set', 'flagKey': f'outcome_{outcome}', 'flagValue': True}]
        },
    }

    result = derive_combat_outcome_rewards(state, _end_change(end_reason), encounter=encounter)

    assert result['valid'] is True
    assert result['outcome'] == outcome
    assert [(change['type'], change.get('flagKey')) for change in result['changes']] == [
        ('flag.set', f'outcome_{outcome}')
    ]


@pytest.mark.parametrize(
    ('mutation', 'expected_reason'),
    [
        ('wrong_type', 'authoritative combat.end'),
        ('missing_id', 'stable ledger ID'),
        ('nonterminal_change', 'not terminal'),
        ('active_state', 'Persisted combat is not ended'),
        ('reason_mismatch', 'does not match'),
        ('unsupported_reason', 'not rewardable'),
        ('missing_encounter', 'stable encounter ID'),
        ('encounter_mismatch', 'IDs disagree'),
    ],
)
def test_invalid_or_unverifiable_outcomes_fail_closed(mutation: str, expected_reason: str) -> None:
    state = _state()
    change = _end_change()
    encounter = {'id': 'enc_bridge', 'rewards': {'xp': 100}}
    if mutation == 'wrong_type':
        change['type'] = 'combat.update'
    elif mutation == 'missing_id':
        change.pop('id')
    elif mutation == 'nonterminal_change':
        change['status'] = 'active'
    elif mutation == 'active_state':
        state['combat']['status'] = 'active'
    elif mutation == 'reason_mismatch':
        change['endReason'] = 'players_fled'
    elif mutation == 'unsupported_reason':
        state['combat']['flags']['endReason'] = 'interrupted'
        change['endReason'] = 'interrupted'
    elif mutation == 'missing_encounter':
        state['combat']['flags'].pop('campaignPackEncounterId')
        change.pop('encounterId')
        encounter = None
    elif mutation == 'encounter_mismatch':
        encounter['id'] = 'enc_wrong'

    result = derive_combat_outcome_rewards(state, change, encounter=encounter)

    assert result['valid'] is False
    assert result['changes'] == []
    assert result['questEvents'] == []
    assert expected_reason in result['reason']


def test_no_present_party_skips_actor_rewards_but_keeps_global_consequences() -> None:
    state = _state()
    for participant in state['combat']['participants']:
        if participant['team'] == 'player':
            participant['isPresent'] = False
            participant['conditions'] = [*participant.get('conditions', []), 'absent']
    encounter = {
        'id': 'enc_bridge',
        'rewards': {'xp': 50, 'gp': 10, 'flags': {'encounter_resolved': True}},
        'onVictory': [{'type': 'flag.set', 'flagKey': 'road_open', 'flagValue': True}],
    }

    result = derive_combat_outcome_rewards(state, _end_change(), encounter=encounter)

    assert result['valid'] is True
    assert result['eligibleActorIds'] == []
    assert not any(change['type'] in {'xp.add', 'currency.add', 'inventory.add'} for change in result['changes'])
    assert {change['flagKey'] for change in result['changes']} == {'encounter_resolved', 'road_open'}
    assert [entry['reason'] for entry in result['skipped']].count('No eligible player participant can receive this reward.') == 2


def test_stale_explicit_reward_actor_fails_closed_without_falling_back() -> None:
    encounter = {
        'id': 'enc_bridge',
        'rewards': [{'type': 'xp', 'amount': 50, 'actorId': 'missing_player'}],
    }

    result = derive_combat_outcome_rewards(_state(), _end_change(), encounter=encounter)

    assert not any(change['type'] == 'xp.add' for change in result['changes'])
    assert result['skipped'] == [
        {'category': 'reward', 'index': 0, 'reason': 'Explicit reward actor is not an eligible participant.'}
    ]


def test_invalid_reward_and_direct_quest_mutation_are_not_emitted() -> None:
    encounter = {
        'id': 'enc_bridge',
        'rewards': [
            {'type': 'currency', 'currency': 'credits', 'amount': 10},
            {'type': 'item', 'item': {'quantity': 1}},
            {'type': 'mystery', 'amount': 99},
        ],
        'onVictory': [
            {'type': 'quest.complete', 'questId': 'quest_bridge'},
            {'type': 'flag.set', 'flagValue': True},
        ],
    }

    result = derive_combat_outcome_rewards(_state(), _end_change(), encounter=encounter)

    assert result['changes'] == []
    reasons = [entry['reason'] for entry in result['skipped']]
    assert reasons == [
        'Currency reward uses an unsupported code.',
        'Item reward requires an id or name.',
        "Unsupported reward type 'mystery'.",
        "Unsupported consequence type 'quest.complete'.",
        'Flag consequence has no key.',
    ]


def test_default_and_authored_quest_events_feed_mechanical_quest_rules() -> None:
    state = _state()
    state['quests'] = [
        {
            'id': 'quest_bridge',
            'title': 'Secure the Bridge',
            'status': 'active',
            'objectives': [
                {
                    'id': 'resolve_bridge',
                    'status': 'open',
                    'completeWhen': {
                        'eventType': 'combat.outcome',
                        'encounterId': 'enc_bridge',
                        'endReason': 'all_enemies_defeated',
                    },
                }
            ],
        }
    ]
    encounter = {
        'id': 'enc_bridge',
        'questIds': ['quest_bridge'],
        'outcomes': {
            'victory': {
                'questEvents': [
                    {
                        'eventType': 'prisoners_spared',
                        'questId': 'quest_bridge',
                        'objectiveId': 'show_mercy',
                        'mercy': True,
                    }
                ]
            }
        },
    }

    result = derive_combat_outcome_rewards(state, _end_change(), encounter=encounter)
    events = {event['type']: event for event in result['questEvents']}

    assert set(events) == {'combat.outcome', 'prisoners_spared'}
    assert events['prisoners_spared']['objectiveId'] == 'show_mercy'
    assert events['prisoners_spared']['mercy'] is True
    derived = derive_quest_changes(state, events['combat.outcome'])
    assert [(change['type'], change['questId'], change['objectiveId'], change['status']) for change in derived] == [
        ('quest.objective.update', 'quest_bridge', 'resolve_bridge', 'completed')
    ]


def test_campaign_pack_encounter_is_resolved_without_a_parallel_reward_store() -> None:
    state = _state()
    state['campaignPack']['catalog']['encounters'][0].update(
        {
            'rewards': {'xp': 20},
            'outcomes': {
                'victory': {
                    'consequences': [{'type': 'flag.set', 'flagKey': 'pack_encounter_won', 'flagValue': True}]
                }
            },
        }
    )

    result = derive_combat_outcome_rewards(state, _end_change())

    assert result['encounterId'] == 'enc_bridge'
    assert sum(change['amount'] for change in result['changes'] if change['type'] == 'xp.add') == 20
    assert next(change for change in result['changes'] if change['type'] == 'flag.set')['flagKey'] == 'pack_encounter_won'


def test_applier_replay_is_idempotent_and_partial_ledgers_only_return_missing_outputs() -> None:
    state = _state()
    encounter = {
        'id': 'enc_bridge',
        'questIds': ['quest_bridge'],
        'rewards': {
            'xp': 31,
            'gp': 3,
            'items': [{'id': 'bridge_token', 'name': 'Bridge Token', 'quantity': 1}],
            'flags': {'bridge_rewarded': True},
        },
    }
    first = derive_combat_outcome_rewards(state, _end_change(), encounter=encounter)

    validation = validate_state_changes(state=state, changes=first['changes'])
    assert validation['rejected'] == []
    validated_changes = [
        *[entry['change'] for entry in validation['accepted']],
        *[entry['modifiedChange'] for entry in validation['modified']],
    ]
    assert {change['id'] for change in validated_changes} == {change['id'] for change in first['changes']}
    applied = apply_state_changes(state, validated_changes)
    replay = derive_combat_outcome_rewards(
        applied['nextState'],
        _end_change(),
        encounter=encounter,
        applied_ledger_ids=[event['id'] for event in first['questEvents']],
    )

    assert replay['valid'] is True
    assert replay['changes'] == []
    assert replay['questEvents'] == []
    assert replay['alreadyApplied'] is True
    actors = {actor['id']: actor for actor in applied['nextState']['playerCharacters']}
    assert actors['player_1']['xp']['current'] == 16
    assert actors['player_2']['xp']['current'] == 15
    assert actors['player_1']['inventory']['currency']['gp'] == 2
    assert actors['player_2']['inventory']['currency']['gp'] == 1
    assert len(actors['player_1']['inventory']['items']) == 1
    assert actors['player_2']['inventory']['items'] == []

    partial_state = deepcopy(state)
    partial_state['stateChangeLedger'] = [{'id': first['changes'][0]['id']}]
    partial = derive_combat_outcome_rewards(partial_state, _end_change(), encounter=encounter)
    assert first['changes'][0]['id'] not in {change['id'] for change in partial['changes']}
    assert {change['id'] for change in partial['changes']} == {
        change['id'] for change in first['changes'][1:]
    }


def test_persisted_outcome_marker_suppresses_every_output() -> None:
    encounter = {'id': 'enc_bridge', 'questIds': ['quest_bridge'], 'rewards': {'xp': 50}}
    first = derive_combat_outcome_rewards(_state(), _end_change(), encounter=encounter)

    replay = derive_combat_outcome_rewards(
        _state(),
        _end_change(),
        encounter=encounter,
        applied_ledger_ids=[first['outcomeLedgerId']],
    )

    assert replay['valid'] is True
    assert replay['alreadyApplied'] is True
    assert replay['changes'] == []
    assert replay['questEvents'] == []
    assert replay['ledgerIds'] == [first['outcomeLedgerId']]


def test_authored_quest_event_without_exact_or_linked_quest_is_skipped() -> None:
    encounter = {
        'id': 'enc_bridge',
        'questEvents': [{'eventType': 'bridge_resolved'}],
    }

    result = derive_combat_outcome_rewards(_state(), _end_change(), encounter=encounter)

    assert result['questEvents'] == []
    assert result['skipped'] == [
        {'category': 'quest_event', 'index': 0, 'reason': 'Quest event has no exact quest ID.'}
    ]
