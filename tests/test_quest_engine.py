from __future__ import annotations

from copy import deepcopy

from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.extraction.schemas import normalize_state_change
from aidm_server.game_state.validation.validator import validate_state_changes


def _state() -> dict:
    return {
        'currentScene': {
            'locationId': 'village',
            'name': 'Village',
            'activeQuestIds': ['quest_relic'],
            'activeNpcIds': [],
            'items': [],
        },
        'activePlayerIds': [1],
        'playerCharacters': [
            {
                'id': 'player_1',
                'playerId': 1,
                'name': 'Aria',
                'inventory': {'items': [], 'currency': {'gp': 0}},
                'health': {'currentHp': 10, 'maxHp': 10, 'tempHp': 0},
                'xp': {'current': 0},
            }
        ],
        'quests': [
            {
                'id': 'quest_relic',
                'title': 'Recover the Relic',
                'status': 'active',
                'completionPolicy': 'all',
                'xpReward': 75,
                'rewards': {
                    'gp': 12,
                    'items': [
                        {
                            'id': 'item_oath_token',
                            'name': 'Oath Token',
                            'type': 'quest',
                            'quantity': 1,
                        }
                    ],
                },
                'onComplete': [
                    {
                        'type': 'flag.set',
                        'flagKey': 'relic_returned',
                        'flagValue': True,
                    }
                ],
                'objectives': [
                    {
                        'id': 'take_relic',
                        'description': 'Take the exact relic.',
                        'status': 'open',
                        'completeWhen': {
                            'eventType': 'inventory.add',
                            'actorId': 'player_1',
                            'itemId': 'item_relic',
                        },
                    },
                    {
                        'id': 'return_home',
                        'description': 'Return to the village.',
                        'status': 'blocked',
                        'prerequisiteObjectiveIds': ['take_relic'],
                        'completeWhen': {'atLocationId': 'village'},
                    },
                ],
            }
        ],
        'flags': {},
        'stateChangeLedger': [],
    }


def _accepted(validation: dict) -> list[dict]:
    return [entry['change'] for entry in validation['accepted']]


def test_narration_cannot_complete_mechanical_quest_before_objectives() -> None:
    state = _state()

    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'narrated-completion',
                'turnId': 1,
                'type': 'quest.complete',
                'questId': 'quest_relic',
                'reason': 'The narrator said it was done.',
            }
        ],
    )

    assert validation['accepted'] == []
    assert 'mechanical objectives remain incomplete' in validation['rejected'][0]['reason']


def test_validated_item_event_completes_prerequisite_chain_and_rewards_once() -> None:
    state = _state()
    event = {
        'id': 'turn-2:take-relic',
        'turnId': 2,
        'type': 'inventory.add',
        'actorId': 'player_1',
        'itemId': 'item_relic',
        'itemName': 'Ancient Relic',
        'item': {
            'id': 'item_relic',
            'name': 'Ancient Relic',
            'type': 'quest',
            'quantity': 1,
        },
        'quantity': 1,
        'reason': 'The relic was removed from the scene and picked up.',
    }
    validation = validate_state_changes(state=state, changes=[event], expected_actor_id='player_1')
    result = apply_state_changes(state, _accepted(validation))
    next_state = result['nextState']

    quest = next_state['quests'][0]
    assert quest['status'] == 'completed'
    assert {objective['id']: objective['status'] for objective in quest['objectives']} == {
        'take_relic': 'completed',
        'return_home': 'completed',
    }
    actor = next_state['playerCharacters'][0]
    assert actor['xp']['current'] == 75
    assert actor['inventory']['currency']['gp'] == 12
    assert [item['id'] for item in actor['inventory']['items']] == ['item_relic', 'item_oath_token']
    assert next_state['flags']['relic_returned'] is True
    assert 'quest_relic' not in next_state['currentScene']['activeQuestIds']

    replay = apply_state_changes(next_state, [event])
    replay_actor = replay['nextState']['playerCharacters'][0]
    assert replay_actor['xp']['current'] == 75
    assert replay_actor['inventory']['currency']['gp'] == 12
    assert [item['id'] for item in replay_actor['inventory']['items']] == ['item_relic', 'item_oath_token']
    assert replay['appliedChanges'] == []


def test_quest_reward_rekeys_incompatible_authored_item_id_without_duplication() -> None:
    state = _state()
    state['playerCharacters'][0]['inventory']['items'] = [
        {
            'id': 'item_oath_token',
            'name': 'Counterfeit Knife',
            'type': 'weapon',
            'quantity': 1,
        }
    ]
    event = {
        'id': 'turn-2:take-relic-with-collision',
        'turnId': 2,
        'type': 'inventory.add',
        'actorId': 'player_1',
        'itemId': 'item_relic',
        'itemName': 'Ancient Relic',
        'item': {'id': 'item_relic', 'name': 'Ancient Relic', 'type': 'quest', 'quantity': 1},
        'quantity': 1,
    }

    validation = validate_state_changes(state=state, changes=[event], expected_actor_id='player_1')
    result = apply_state_changes(state, _accepted(validation))
    items = result['nextState']['playerCharacters'][0]['inventory']['items']
    reward = next(item for item in items if item['name'] == 'Oath Token')

    assert len({item['id'] for item in items}) == len(items)
    assert reward['id'].startswith('itm_reward_')
    assert reward['sourceItemId'] == 'item_oath_token'
    assert next(item for item in items if item['id'] == 'item_oath_token')['name'] == 'Counterfeit Knife'

    replay = apply_state_changes(result['nextState'], [event])
    assert replay['appliedChanges'] == []
    assert replay['nextState']['playerCharacters'][0]['inventory']['items'] == items


def test_prerequisite_objective_is_blocked_until_validated_event() -> None:
    state = _state()
    state['currentScene']['locationId'] = 'ruins'

    unrelated = apply_state_changes(
        state,
        [
            {
                'id': 'turn-1:unrelated',
                'turnId': 1,
                'type': 'flag.set',
                'flagKey': 'weather_clear',
                'flagValue': True,
            }
        ],
    )['nextState']
    objectives = {objective['id']: objective for objective in unrelated['quests'][0]['objectives']}
    assert objectives['take_relic']['status'] == 'open'
    assert objectives['return_home']['status'] == 'blocked'

    pickup = apply_state_changes(
        unrelated,
        [
            {
                'id': 'turn-2:take-relic',
                'turnId': 2,
                'type': 'inventory.add',
                'actorId': 'player_1',
                'itemId': 'item_relic',
                'itemName': 'Ancient Relic',
                'item': {'id': 'item_relic', 'name': 'Ancient Relic', 'type': 'quest', 'quantity': 1},
                'quantity': 1,
            }
        ],
    )['nextState']
    objectives = {objective['id']: objective for objective in pickup['quests'][0]['objectives']}
    assert objectives['take_relic']['status'] == 'completed'
    assert objectives['return_home']['status'] == 'open'
    assert pickup['quests'][0]['status'] == 'active'

    arrival = apply_state_changes(
        pickup,
        [
            {
                'id': 'turn-3:return-home',
                'turnId': 3,
                'type': 'scene.move_location',
                'locationId': 'village',
                'name': 'Village',
            }
        ],
    )['nextState']
    assert arrival['quests'][0]['status'] == 'completed'


def test_required_failure_rule_fails_quest_and_applies_failure_consequence_once() -> None:
    state = _state()
    quest = state['quests'][0]
    quest['failOnObjectiveFailure'] = True
    quest['failureConsequences'] = [
        {'type': 'flag.set', 'flagKey': 'relic_destroyed', 'flagValue': True}
    ]
    quest['objectives'] = [
        {
            'id': 'protect_relic',
            'description': 'Keep the relic intact.',
            'status': 'open',
            'completeWhen': {'eventType': 'flag.set', 'flagKey': 'relic_safe'},
            'failWhen': {
                'eventType': 'flag.set',
                'flagKey': 'relic_broken',
            },
        }
    ]

    result = apply_state_changes(
        state,
        [
            {
                'id': 'turn-4:break-relic',
                'turnId': 4,
                'type': 'flag.set',
                'flagKey': 'relic_broken',
                'flagValue': True,
            }
        ],
    )

    assert result['nextState']['quests'][0]['status'] == 'failed'
    assert result['nextState']['quests'][0]['objectives'][0]['status'] == 'failed'
    assert result['nextState']['flags']['relic_destroyed'] is True


def test_legacy_narrative_quest_remains_backward_compatible() -> None:
    state = _state()
    state['quests'][0]['objectives'] = [
        {'id': 'talk', 'description': 'Talk to the elder.', 'status': 'open'}
    ]
    original = deepcopy(state)

    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'legacy-complete',
                'turnId': 5,
                'type': 'quest.complete',
                'questId': 'quest_relic',
            }
        ],
    )

    assert len(validation['accepted']) == 1
    assert state == original


def test_post_dm_cannot_force_terminal_quest_or_objective_updates() -> None:
    state = _state()
    changes = [
        {
            'id': 'narrated-objective',
            'type': 'quest.objective.update',
            'source': 'post_dm',
            'questId': 'quest_relic',
            'objectiveId': 'take_relic',
            'objectiveStatus': 'completed',
        },
        {
            'id': 'narrated-quest',
            'type': 'quest.update',
            'source': 'post_dm',
            'questId': 'quest_relic',
            'status': 'completed',
        },
        {
            'id': 'narrated-nested-objective',
            'type': 'quest.update',
            'source': 'post_dm',
            'questId': 'quest_relic',
            'objectives': [{'id': 'take_relic', 'status': 'failed'}],
        },
    ]

    validation = validate_state_changes(state=state, changes=changes)

    assert validation['accepted'] == []
    assert len(validation['rejected']) == 3
    assert all('Narration cannot' in entry['reason'] for entry in validation['rejected'])
    assert state['quests'][0]['status'] == 'active'
    assert state['quests'][0]['objectives'][0]['status'] == 'open'


def test_post_dm_helper_cannot_spoof_trusted_quest_engine_source() -> None:
    state = _state()
    normalized = normalize_state_change(
        {
            'type': 'quest.objective.update',
            'source': 'quest_engine',
            'questId': 'quest_relic',
            'objectiveId': 'take_relic',
            'objectiveStatus': 'completed',
        },
        fallback_actor_id='player_1',
        fallback_id='spoofed-source',
        source='post_dm',
    )
    assert normalized is not None
    assert normalized['source'] == 'post_dm'

    validation = validate_state_changes(state=state, changes=[normalized])
    assert validation['accepted'] == []
    assert 'Narration cannot' in validation['rejected'][0]['reason']


def test_post_dm_flag_cannot_drive_authoritative_quest_state() -> None:
    state = _state()
    state['quests'][0]['objectives'] = [
        {
            'id': 'choose_route',
            'description': 'Choose the betrayal route.',
            'status': 'open',
            'completeWhen': {'eventType': 'flag.set', 'flagKey': 'route_choice'},
        }
    ]
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'narrated-route',
                'type': 'flag.set',
                'source': 'post_dm',
                'flagKey': 'route_choice',
                'flagValue': 'betray',
            }
        ],
    )
    result = apply_state_changes(state, _accepted(validation))

    assert validation['accepted'] == []
    assert validation['rejected'][0]['reason'] == 'Narration cannot set authoritative gameplay flags.'
    assert result['nextState']['flags'] == {}
    assert result['nextState']['quests'][0]['objectives'][0]['status'] == 'open'


def test_stale_explicit_quest_and_objective_ids_fail_closed() -> None:
    state = _state()
    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'stale-quest',
                'type': 'quest.update',
                'questId': 'missing_quest',
                'title': 'Recover the Relic',
                'stage': 'Wrong target',
            },
            {
                'id': 'stale-objective',
                'type': 'quest.objective.update',
                'source': 'quest_engine',
                'questId': 'quest_relic',
                'objectiveId': 'missing_objective',
                'description': 'Take the exact relic.',
                'objectiveStatus': 'completed',
            },
        ],
    )

    assert validation['accepted'] == []
    assert [entry['reason'] for entry in validation['rejected']] == [
        'Quest update target id was stale or not found.',
        'Quest objective update target id was stale or not found.',
    ]


def test_stale_per_reward_actor_uses_valid_fallback_once() -> None:
    state = _state()
    quest = state['quests'][0]
    quest['xpReward'] = 0
    quest['rewards'] = [{'type': 'xp', 'amount': 25, 'actorId': 'missing_actor'}]
    quest['objectives'] = [
        {
            'id': 'take_relic',
            'status': 'open',
            'completeWhen': {'eventType': 'inventory.add', 'actorId': 'player_1', 'itemId': 'item_relic'},
        }
    ]
    event = {
        'id': 'take-relic-for-reward',
        'turnId': 8,
        'type': 'inventory.add',
        'actorId': 'player_1',
        'itemId': 'item_relic',
        'itemName': 'Ancient Relic',
        'item': {'id': 'item_relic', 'name': 'Ancient Relic', 'type': 'quest', 'quantity': 1},
        'quantity': 1,
    }

    first = apply_state_changes(state, [event])
    replay = apply_state_changes(first['nextState'], [event])

    assert first['nextState']['quests'][0]['status'] == 'completed'
    assert first['nextState']['playerCharacters'][0]['xp']['current'] == 25
    assert replay['nextState']['playerCharacters'][0]['xp']['current'] == 25


def test_actor_reward_defers_terminal_ledger_until_recipient_exists() -> None:
    state = _state()
    actor = state['playerCharacters'].pop()
    state['activePlayerIds'] = []
    quest = state['quests'][0]
    quest['xpReward'] = 20
    quest['rewards'] = []
    quest['objectives'] = [
        {
            'id': 'signal',
            'status': 'open',
            'completeWhen': {'eventType': 'flag.set', 'flagKey': 'signal_lit'},
        }
    ]

    without_recipient = apply_state_changes(
        state,
        [{'id': 'signal-without-actor', 'type': 'flag.set', 'flagKey': 'signal_lit', 'flagValue': True}],
    )['nextState']
    assert without_recipient['quests'][0]['objectives'][0]['status'] == 'completed'
    assert without_recipient['quests'][0]['status'] == 'active'

    without_recipient['playerCharacters'] = [actor]
    recovered = apply_state_changes(
        without_recipient,
        [{'id': 'recipient-restored', 'type': 'flag.set', 'flagKey': 'actor_ready', 'flagValue': True}],
    )['nextState']
    assert recovered['quests'][0]['status'] == 'completed'
    assert recovered['playerCharacters'][0]['xp']['current'] == 20


def test_stale_predicate_actor_and_cross_quest_objective_do_not_satisfy_rule() -> None:
    state = _state()
    state['playerCharacters'][0]['inventory']['items'] = [
        {'id': 'item_relic', 'name': 'Ancient Relic', 'quantity': 1}
    ]
    state['quests'][0]['objectives'] = [
        {
            'id': 'wrong_actor',
            'status': 'open',
            'completeWhen': {'possessesItemId': 'item_relic', 'actorId': 'missing_actor'},
        },
        {
            'id': 'wrong_quest',
            'status': 'open',
            'completeWhen': {'objectiveIds': ['shared_objective']},
        },
    ]
    state['quests'].append(
        {
            'id': 'other_quest',
            'title': 'Other Quest',
            'status': 'active',
            'objectives': [{'id': 'shared_objective', 'status': 'completed'}],
        }
    )

    result = apply_state_changes(
        state,
        [{'id': 'unrelated-tick', 'type': 'flag.set', 'flagKey': 'weather_clear', 'flagValue': True}],
    )

    statuses = {objective['id']: objective['status'] for objective in result['nextState']['quests'][0]['objectives']}
    assert statuses == {'wrong_actor': 'open', 'wrong_quest': 'open'}


def test_event_and_state_predicates_must_both_match() -> None:
    state = _state()
    state['quests'][0]['objectives'] = [
        {
            'id': 'signal_at_ruins',
            'status': 'open',
            'completeWhen': {
                'eventType': 'flag.set',
                'flagKey': 'signal_lit',
                'atLocationId': 'ruins',
            },
        }
    ]

    wrong_place = apply_state_changes(
        state,
        [{'id': 'signal-village', 'type': 'flag.set', 'flagKey': 'signal_lit', 'flagValue': True}],
    )['nextState']
    assert wrong_place['quests'][0]['objectives'][0]['status'] == 'open'

    wrong_place['currentScene']['locationId'] = 'ruins'
    right_place = apply_state_changes(
        wrong_place,
        [{'id': 'signal-ruins', 'type': 'flag.set', 'flagKey': 'signal_lit', 'flagValue': True}],
    )['nextState']
    assert right_place['quests'][0]['objectives'][0]['status'] == 'completed'
