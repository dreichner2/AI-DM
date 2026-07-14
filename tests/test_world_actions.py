from __future__ import annotations

from aidm_server.action_intent import validate_action_intent
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.extraction.pre_dm_action_extractor import extract_pre_dm_actions
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)


def _state() -> dict:
    return {
        'currentScene': {
            'locationId': 'camp',
            'name': 'Camp',
            'sceneType': 'exploration',
            'combatState': 'none',
            'activeNpcIds': [],
            'activeQuestIds': ['recover_key'],
            'items': [
                {
                    'id': 'iron_key_rusted',
                    'name': 'Iron Key',
                    'description': 'Rusted and bent.',
                    'type': 'quest',
                    'quantity': 1,
                },
                {
                    'id': 'iron_key_silvered',
                    'name': 'Iron Key',
                    'description': 'Silvered teeth.',
                    'type': 'quest',
                    'quantity': 1,
                },
            ],
        },
        'locations': [
            {
                'id': 'camp',
                'name': 'Camp',
                'status': 'visited',
                'connectedLocationIds': ['ruins'],
            },
            {
                'id': 'ruins',
                'name': 'Ruins',
                'status': 'discovered',
                'connectedLocationIds': ['camp'],
                'sceneState': {
                    'description': 'Broken stones ring a dry well.',
                    'items': [{'id': 'chalk', 'name': 'Chalk', 'type': 'misc', 'quantity': 1}],
                    'activeNpcIds': [],
                },
            },
            {
                'id': 'tower',
                'name': 'Tower',
                'status': 'discovered',
                'connectedLocationIds': [],
            },
        ],
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
                'id': 'recover_key',
                'title': 'Recover the Silvered Key',
                'status': 'active',
                'objectives': [
                    {
                        'id': 'take_silvered_key',
                        'description': 'Take the silvered key.',
                        'status': 'open',
                        'completeWhen': {
                            'eventType': 'inventory.add',
                            'itemId': 'iron_key_silvered',
                            'actorId': 'player_1',
                        },
                    }
                ],
            }
        ],
        'flags': {},
        'stateChangeLedger': [],
    }


def _run_intent(state: dict, raw_intent: dict, *, turn_id: int) -> tuple[dict, dict, dict]:
    intent, error = validate_action_intent(raw_intent)
    assert error is None
    extraction = extract_pre_dm_actions(
        current_state=state,
        player_message=raw_intent.get('text') or 'Do it.',
        recent_timeline=[],
        actor_id='player_1',
        action_intent=intent,
    )
    declared = validate_declared_actions(
        state=state,
        declared_actions=extraction['declaredActions'],
        current_turn=turn_id,
        expected_actor_id='player_1',
    )
    immediate = validate_state_changes(
        state=state,
        changes=declared['immediateChanges'],
        expected_actor_id='player_1',
    )
    result = apply_state_changes(state, validated_changes_for_application(immediate))
    return declared, immediate, result


def test_same_name_scene_items_are_picked_up_by_exact_id_and_drive_quest() -> None:
    state = _state()
    declared, immediate, result = _run_intent(
        state,
        {
            'kind': 'item',
            'source': 'scene_object',
            'text': 'Aria picks up the silvered iron key.',
            'inventory_action': 'pick_up',
            'item': {'id': 'iron_key_silvered', 'name': 'Iron Key', 'quantity': 1},
        },
        turn_id=1,
    )

    assert declared['validatedActions'][0]['status'] == 'valid'
    assert len(immediate['accepted']) == 2
    next_state = result['nextState']
    assert [item['id'] for item in next_state['currentScene']['items']] == ['iron_key_rusted']
    assert [item['id'] for item in next_state['playerCharacters'][0]['inventory']['items']] == [
        'iron_key_silvered'
    ]
    assert next_state['quests'][0]['objectives'][0]['status'] == 'completed'
    assert next_state['quests'][0]['status'] == 'completed'


def test_stale_scene_item_id_fails_closed_without_name_fallback() -> None:
    state = _state()
    declared, immediate, result = _run_intent(
        state,
        {
            'kind': 'item',
            'text': 'Aria picks up an iron key.',
            'inventory_action': 'pick_up',
            'item': {'id': 'missing_key', 'name': 'Iron Key', 'quantity': 1},
        },
        turn_id=2,
    )

    assert declared['validatedActions'][0]['status'] == 'invalid'
    assert 'stale or no longer present' in declared['validatedActions'][0]['reason']
    assert immediate['accepted'] == []
    assert result['nextState']['currentScene']['items'] == state['currentScene']['items']


def test_stale_world_entity_ids_do_not_fall_back_to_matching_names() -> None:
    state = _state()
    state['knownNpcs'] = [
        {'id': 'npc_guide', 'name': 'Guide', 'status': 'known', 'locationId': 'camp'}
    ]
    changes = [
        {
            'id': 'stale-location-update',
            'type': 'location.update',
            'locationId': 'missing_camp',
            'name': 'Camp',
            'status': 'visited',
        },
        {
            'id': 'stale-npc-update',
            'type': 'npc.update',
            'npcId': 'missing_guide',
            'name': 'Guide',
            'status': 'met',
        },
    ]

    validation = validate_state_changes(state=state, changes=changes)
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['accepted'] == []
    assert [entry['reason'] for entry in validation['rejected']] == [
        'Location update target id was stale or not found.',
        'NPC update target id was stale or not found.',
    ]
    assert result['nextState']['locations'] == state['locations']
    assert result['nextState']['knownNpcs'] == state['knownNpcs']


def test_name_only_duplicate_scene_item_requests_clarification() -> None:
    state = _state()
    declared, _, _ = _run_intent(
        state,
        {
            'kind': 'item',
            'text': 'Aria picks up an iron key.',
            'inventory_action': 'pick_up',
            'item': {'name': 'Iron Key', 'quantity': 1},
        },
        turn_id=3,
    )

    assert declared['validatedActions'][0]['status'] == 'needs_clarification'
    assert {option['itemId'] for option in declared['clarificationRequests'][0]['options']} == {
        'iron_key_rusted',
        'iron_key_silvered',
    }


def test_drop_moves_one_exact_identity_from_inventory_to_scene_and_is_retry_safe() -> None:
    state = _state()
    state['playerCharacters'][0]['inventory']['items'] = [
        {'id': 'rope_hemp', 'name': 'Rope', 'type': 'gear', 'quantity': 1},
        {'id': 'rope_silk', 'name': 'Rope', 'type': 'gear', 'quantity': 1},
    ]
    intent = {
        'kind': 'item',
        'text': 'Aria drops the silk rope.',
        'inventory_action': 'drop',
        'item': {'id': 'rope_silk', 'name': 'Rope', 'quantity': 1},
    }
    _, _, result = _run_intent(state, intent, turn_id=4)
    next_state = result['nextState']

    assert [item['id'] for item in next_state['playerCharacters'][0]['inventory']['items']] == ['rope_hemp']
    assert [item['id'] for item in next_state['currentScene']['items']] == [
        'iron_key_rusted',
        'iron_key_silvered',
        'rope_silk',
    ]

    _, _, replay = _run_intent(next_state, intent, turn_id=4)
    assert replay['nextState']['currentScene']['items'] == next_state['currentScene']['items']


def test_partial_pickup_mints_stable_split_identity_and_retry_is_idempotent() -> None:
    state = _state()
    state['currentScene']['items'].append(
        {'id': 'arrow_stack', 'name': 'Arrow', 'type': 'ammo', 'quantity': 3}
    )
    intent = {
        'kind': 'item',
        'source': 'scene_object',
        'text': 'Aria picks up one arrow.',
        'inventory_action': 'pick_up',
        'item': {'id': 'arrow_stack', 'name': 'Arrow', 'quantity': 1},
    }

    _, _, result = _run_intent(state, intent, turn_id=40)
    next_state = result['nextState']
    source = next(item for item in next_state['currentScene']['items'] if item['id'] == 'arrow_stack')
    moved = next(item for item in next_state['playerCharacters'][0]['inventory']['items'] if item['name'] == 'Arrow')

    assert source['quantity'] == 2
    assert moved['id'].startswith('itm_split_')
    assert moved['id'] != source['id']
    assert moved['splitFromItemId'] == 'arrow_stack'
    assert len({source['id'], moved['id']}) == 2

    _, _, replay = _run_intent(next_state, intent, turn_id=40)
    assert replay['nextState']['currentScene']['items'] == next_state['currentScene']['items']
    assert replay['nextState']['playerCharacters'][0]['inventory']['items'] == next_state['playerCharacters'][0]['inventory']['items']


def test_partial_drop_mints_stable_split_identity_and_retry_is_idempotent() -> None:
    state = _state()
    state['playerCharacters'][0]['inventory']['items'] = [
        {'id': 'rope_stack', 'name': 'Rope', 'type': 'gear', 'quantity': 2}
    ]
    intent = {
        'kind': 'item',
        'text': 'Aria drops one rope.',
        'inventory_action': 'drop',
        'item': {'id': 'rope_stack', 'name': 'Rope', 'quantity': 1},
    }

    _, _, result = _run_intent(state, intent, turn_id=41)
    next_state = result['nextState']
    source = next_state['playerCharacters'][0]['inventory']['items'][0]
    moved = next(item for item in next_state['currentScene']['items'] if item['name'] == 'Rope')

    assert source == {'id': 'rope_stack', 'name': 'Rope', 'type': 'gear', 'quantity': 1}
    assert moved['id'].startswith('itm_split_')
    assert moved['id'] != source['id']
    assert moved['splitFromItemId'] == 'rope_stack'

    _, _, replay = _run_intent(next_state, intent, turn_id=41)
    assert replay['nextState']['currentScene']['items'] == next_state['currentScene']['items']
    assert replay['nextState']['playerCharacters'][0]['inventory']['items'] == next_state['playerCharacters'][0]['inventory']['items']


def test_structured_travel_requires_known_adjacent_destination_and_restores_local_scene() -> None:
    state = _state()
    _, _, outbound = _run_intent(
        state,
        {
            'kind': 'travel',
            'text': 'The party travels to the ruins.',
            'location': {'id': 'ruins', 'name': 'Ruins'},
        },
        turn_id=5,
    )
    at_ruins = outbound['nextState']
    assert at_ruins['currentScene']['locationId'] == 'ruins'
    assert at_ruins['currentScene']['description'] == 'Broken stones ring a dry well.'
    assert [item['id'] for item in at_ruins['currentScene']['items']] == ['chalk']

    declared, immediate, blocked = _run_intent(
        at_ruins,
        {
            'kind': 'travel',
            'text': 'The party jumps to the tower.',
            'location': {'id': 'tower', 'name': 'Tower'},
        },
        turn_id=6,
    )
    assert declared['validatedActions'][0]['status'] == 'invalid'
    assert 'not adjacent' in declared['validatedActions'][0]['reason']
    assert immediate['accepted'] == []
    assert blocked['nextState']['currentScene']['locationId'] == 'ruins'

    _, _, returned = _run_intent(
        at_ruins,
        {
            'kind': 'travel',
            'text': 'The party returns to camp.',
            'location': {'id': 'camp', 'name': 'Camp'},
        },
        turn_id=7,
    )
    assert returned['nextState']['currentScene']['locationId'] == 'camp'
    assert {item['id'] for item in returned['nextState']['currentScene']['items']} == {
        'iron_key_rusted',
        'iron_key_silvered',
    }
