from __future__ import annotations

from copy import deepcopy

from aidm_server.action_intent import validate_action_intent
from aidm_server.combat.legal_actions import legal_combat_actions_for_player, resolve_combat_legal_action
from aidm_server.combat.state import consume_combat_turn_economy, normalize_combat_state
from aidm_server.database import db
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.extraction.pre_dm_action_extractor import extract_pre_dm_actions
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.interactables import REPEAT_CHECK_MESSAGE, available_actions_for_target
from aidm_server.models import Player, Session, safe_json_loads
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player


def _objects(*, unlock_dc: int = 1, disarm_dc: int = 30, save_dc: int = 30, on_success: str = 'half') -> tuple[list, list]:
    interactables = [
        {
            'id': 'vault_door',
            'name': 'Vault Door',
            'kind': 'door',
            'locked': True,
            'revision': 0,
            'requirements': {'unlock': {'check': {'id': 'pick_vault', 'dc': unlock_dc, 'skill': 'sleight_of_hand'}}},
            'discoversClueIds': {'unlock': ['clue_ledger']},
            'setsFlags': {'unlock': {'vault_open': True}},
            'discoversLocationIds': {'unlock': ['loc_cellar']},
        },
        {
            'id': 'strongbox',
            'name': 'Strongbox',
            'kind': 'container',
            'revision': 0,
            'contents': [
                {'id': 'silver_ring', 'name': 'Silver Ring', 'quantity': 1, 'type': 'treasure'},
                {'id': 'false_letter', 'name': 'False Letter', 'quantity': 1, 'hidden': True},
                {'id': 'gm_secret', 'name': 'GM Secret', 'gmOnly': True},
            ],
        },
    ]
    hazards = [
        {
            'id': 'needle_trap',
            'name': 'Needle Trap',
            'kind': 'hazard',
            'active': True,
            'disarmable': True,
            'revision': 0,
            'requirements': {'disarm': {'check': {'id': 'disarm_needle', 'dc': disarm_dc, 'ability': 'dexterity'}}},
            'mechanicalEffects': {
                'failure:disarm': {
                    'damage': {
                        'dice': '2d6',
                        'type': 'piercing',
                        'save': {'ability': 'dexterity', 'dc': save_dc, 'onSuccess': on_success},
                    }
                },
                'trigger': {'damage': {'dice': '1d4+1', 'type': 'fire'}, 'targets': 'party'},
            },
        }
    ]
    return interactables, hazards


def _actor(actor_id: str, player_id: int, name: str) -> dict:
    return {
        'id': actor_id,
        'playerId': player_id,
        'name': name,
        'class': 'Rogue',
        'level': 1,
        'stats': {'dexterity': 14, 'strength': 10},
        'health': {'currentHp': 20, 'maxHp': 20, 'tempHp': 0, 'conditions': []},
        'inventory': {'items': [], 'currency': {'gp': 0}},
    }


def _state(**options) -> dict:
    interactables, hazards = _objects(**options)
    return {
        'sessionId': 9,
        'activePlayerIds': [1, 2],
        'playerCharacters': [_actor('player_1', 1, 'Mara'), _actor('player_2', 2, 'Tovin')],
        'currentScene': {
            'locationId': 'vault',
            'name': 'Vault',
            'items': [],
            'interactables': deepcopy(interactables),
            'hazards': deepcopy(hazards),
        },
        'locations': [
            {
                'id': 'vault',
                'name': 'Vault',
                'status': 'visited',
                'sceneState': {'interactables': deepcopy(interactables), 'hazards': deepcopy(hazards)},
            }
        ],
        'flags': {},
        'campaignPack': {
            'packId': 'vault_pack',
            'catalog': {
                'clues': [{'id': 'clue_ledger', 'title': 'Hidden Ledger', 'summary': 'Names in a careful hand.'}],
                'locations': [
                    {'id': 'vault', 'name': 'Vault'},
                    {'id': 'loc_cellar', 'name': 'Smugglers Cellar', 'description': 'A damp cellar.'},
                ],
            },
        },
        'stateChangeLedger': [],
    }


def _act(state: dict, target_id: str, action: str, *, turn: int, actor_id: str = 'player_1') -> tuple[dict, dict]:
    declared = validate_declared_actions(
        state=state,
        declared_actions=[
            {
                'id': f'act_{turn}',
                'type': 'scene.interactable.action',
                'actorId': actor_id,
                'targetId': target_id,
                'objectAction': action,
            }
        ],
        current_turn=turn,
        expected_actor_id=actor_id,
    )
    validation = validate_state_changes(state=state, changes=declared['immediateChanges'], expected_actor_id=actor_id)
    return declared, apply_state_changes(state, validated_changes_for_application(validation))


def _object(state: dict, object_id: str) -> dict:
    scene = state['currentScene']
    return next(item for collection in ('interactables', 'hazards') for item in scene[collection] if item['id'] == object_id)


def test_a_check_that_succeeds_is_rolled_by_the_server_and_stored_on_the_event():
    state = _state(unlock_dc=1)
    declared, result = _act(state, 'vault_door', 'unlock', turn=3)

    validated = declared['validatedActions'][0]
    change = declared['immediateChanges'][0]
    check = change['event']['check']
    assert validated['status'] == 'valid'
    assert (check['id'], check['skill'], check['ability'], check['dc'], check['outcome']) == (
        'pick_vault',
        'sleight_of_hand',
        'dexterity',
        1,
        'success',
    )
    assert check['modifier'] == 2
    assert check['total'] == check['roll'] + check['modifier'] + check['bonus']
    assert f"= {check['total']} against DC 1, success." in validated['reason']
    assert _act(_state(unlock_dc=1), 'vault_door', 'unlock', turn=3)[0]['immediateChanges'][0]['event']['check'] == check
    assert _object(result['nextState'], 'vault_door')['locked'] is False
    assert 'mechanicalEffectsDeferred' not in result['appliedChanges'][0]


def test_a_failed_check_is_remembered_until_the_object_changes():
    state = _state(unlock_dc=30)
    declared, result = _act(state, 'vault_door', 'unlock', turn=3)

    change = declared['immediateChanges'][0]
    assert declared['validatedActions'][0]['status'] == 'valid'
    assert change['event']['type'] == 'interactable.check_failed'
    assert change['event']['check']['outcome'] == 'failure'
    assert declared['validatedActions'][0]['reason'].endswith('against DC 30, failure. The attempt fails.')
    after_failure = result['nextState']
    door = _object(after_failure, 'vault_door')
    assert door['locked'] is True
    assert door['failedCheckActorIds'] == ['player_1']
    assert door['revision'] == 0
    assert 'vault_open' not in after_failure['flags']
    assert not after_failure.get('clues')

    retry, _ = _act(after_failure, 'vault_door', 'unlock', turn=4)
    assert retry['validatedActions'][0]['status'] == 'invalid'
    assert retry['validatedActions'][0]['reason'] == REPEAT_CHECK_MESSAGE

    other_actor, _ = _act(after_failure, 'vault_door', 'unlock', turn=4, actor_id='player_2')
    assert other_actor['validatedActions'][0]['status'] == 'valid'

    _, inspected = _act(after_failure, 'vault_door', 'inspect', turn=5)
    changed = inspected['nextState']
    assert 'failedCheckActorIds' not in _object(changed, 'vault_door')
    retried, _ = _act(changed, 'vault_door', 'unlock', turn=6)
    assert retried['validatedActions'][0]['status'] == 'valid'


def test_a_failed_disarm_springs_the_trap_with_a_saving_throw():
    state = _state(disarm_dc=30, save_dc=30)
    declared, result = _act(state, 'needle_trap', 'disarm', turn=7)

    event = declared['immediateChanges'][0]['event']
    effect = event['effectResults']
    target = effect['targets'][0]
    assert event['type'] == 'hazard.check_failed'
    assert effect['damage']['dice'] == '2d6' and effect['damage']['type'] == 'piercing'
    assert target['actorId'] == 'player_1'
    assert target['save']['passed'] is False
    assert target['damage'] == effect['damage']['total']
    damage = next(change for change in result['appliedChanges'] if change['type'] == 'health.damage')
    assert (damage['actorId'], damage['amount'], damage['source']) == ('player_1', target['damage'], 'interactable_effect')
    actor = result['nextState']['playerCharacters'][0]
    assert actor['health']['currentHp'] == 20 - target['damage']
    assert result['nextState']['playerCharacters'][1]['health']['currentHp'] == 20

    halved = _act(_state(disarm_dc=30, save_dc=1), 'needle_trap', 'disarm', turn=7)[0]
    halved_target = halved['immediateChanges'][0]['event']['effectResults']['targets'][0]
    assert halved_target['save']['passed'] is True
    assert halved_target['damage'] == halved['immediateChanges'][0]['event']['effectResults']['damage']['total'] // 2

    _, negated = _act(_state(disarm_dc=30, save_dc=1, on_success='none'), 'needle_trap', 'disarm', turn=7)
    assert not [change for change in negated['appliedChanges'] if change['type'] == 'health.damage']


def test_a_triggered_hazard_damages_the_whole_party():
    state = _state()
    for scene_state in (state['currentScene'], state['locations'][0]['sceneState']):
        scene_state['hazards'][0]['playerKnown'] = True
    _, result = _act(state, 'needle_trap', 'trigger', turn=8)

    damaged = {change['actorId']: change['amount'] for change in result['appliedChanges'] if change['type'] == 'health.damage'}
    assert set(damaged) == {'player_1', 'player_2'}
    assert len(set(damaged.values())) == 1 and 2 <= next(iter(damaged.values())) <= 5


def test_a_successful_action_discovers_clues_locations_and_sets_flags():
    _, result = _act(_state(unlock_dc=1), 'vault_door', 'unlock', turn=3)
    state = result['nextState']

    assert state['flags']['vault_open'] is True
    clue = next(clue for clue in state['clues'] if clue['id'] == 'clue_ledger')
    assert (clue['title'], clue['summary'], clue['source']) == ('Hidden Ledger', 'Names in a careful hand.', 'campaign_pack')
    cellar = next(location for location in state['locations'] if location['id'] == 'loc_cellar')
    assert (cellar['name'], cellar['status']) == ('Smugglers Cellar', 'discovered')


def test_searched_container_contents_can_be_taken_with_the_exact_scene_pickup():
    _, opened = _act(_state(), 'strongbox', 'open', turn=3)
    _, searched = _act(opened['nextState'], 'strongbox', 'search', turn=4)
    state = searched['nextState']

    assert [(item['id'], item['containerId']) for item in state['currentScene']['items']] == [
        ('silver_ring', 'strongbox'),
        ('false_letter', 'strongbox'),
    ]
    assert [item['id'] for item in _object(state, 'strongbox')['contents']] == ['gm_secret']
    offers = available_actions_for_target(state, location_id='vault', target_id='strongbox', actor={'id': 'player_1'})
    assert offers['take'] == [
        {'itemId': 'silver_ring', 'name': 'Silver Ring'},
        {'itemId': 'false_letter', 'name': 'False Letter'},
    ]

    intent, error = validate_action_intent(
        {
            'kind': 'item',
            'source': 'scene_object',
            'text': 'Mara takes the silver ring.',
            'inventory_action': 'pick_up',
            'item': {'id': 'silver_ring', 'name': 'Silver Ring', 'quantity': 1},
        }
    )
    assert error is None
    extraction = extract_pre_dm_actions(
        current_state=state,
        player_message='Mara takes the silver ring.',
        recent_timeline=[],
        actor_id='player_1',
        action_intent=intent,
    )
    declared = validate_declared_actions(
        state=state,
        declared_actions=extraction['declaredActions'],
        current_turn=5,
        expected_actor_id='player_1',
    )
    taken = apply_state_changes(
        state,
        validated_changes_for_application(
            validate_state_changes(state=state, changes=declared['immediateChanges'], expected_actor_id='player_1')
        ),
    )['nextState']

    assert [item['id'] for item in taken['playerCharacters'][0]['inventory']['items']] == ['silver_ring']
    projected_box = next(
        item
        for item in filter_session_snapshot_for_player(taken)['currentScene']['interactables']
        if item['id'] == 'strongbox'
    )
    assert [item['id'] for item in projected_box['contents']] == ['false_letter']


def _combat_snapshot() -> dict:
    state = _state()
    state['combat'] = normalize_combat_state(
        {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [
                {'id': 'player_1', 'playerId': 1, 'name': 'Mara', 'team': 'player', 'kind': 'player_character',
                 'hp': {'current': 20, 'max': 20}},
                {'id': 'enemy_1', 'name': 'Thug', 'team': 'enemy', 'hp': {'current': 8, 'max': 8}},
            ],
            'initiative': [
                {'participantId': 'player_1', 'roll': 15, 'modifier': 0, 'total': 15, 'order': 0},
                {'participantId': 'enemy_1', 'roll': 10, 'modifier': 0, 'total': 10, 'order': 1},
            ],
            'flags': {},
        },
        state['currentScene'],
    )
    return state


def _object_action(bundle: dict, action_id: str) -> dict:
    return next(action for action in bundle['actions'] if action['id'] == action_id)


def test_object_actions_are_offered_in_combat_and_all_but_inspect_cost_the_action():
    snapshot = _combat_snapshot()
    player = Player(player_id=1, character_name='Mara', class_='Rogue', level=1)

    bundle = legal_combat_actions_for_player(snapshot, player)
    inspect = _object_action(bundle, 'object.strongbox.inspect')
    open_box = _object_action(bundle, 'object.strongbox.open')
    unlock = _object_action(bundle, 'object.vault_door.unlock')
    assert inspect['economy']['action'] == 0 and inspect['available'] is True
    assert open_box['economy']['action'] == 1 and open_box['available'] is True
    assert unlock['description'] == 'Uses your action. Needs a check.'
    assert open_box['object'] == {'id': 'strongbox', 'action': 'open', 'revision': 0}

    resolved, code, _message = resolve_combat_legal_action(snapshot, player, action_id='object.strongbox.open')
    assert code is None
    assert resolved == {
        'action_id': 'object.strongbox.open',
        'action_type': 'object',
        'economy': open_box['economy'],
        'authoritative': True,
        'object': {'id': 'strongbox', 'action': 'open', 'revision': 0},
        'message': 'Mara opens the Strongbox.',
    }

    consumed, reason, _remaining = consume_combat_turn_economy(
        snapshot['combat'],
        actor_id='player_1',
        action_id='object.strongbox.open',
        action_cost=resolved['economy']['action'],
    )
    assert consumed, reason
    spent = legal_combat_actions_for_player(snapshot, player)
    assert _object_action(spent, 'object.strongbox.open')['available'] is False
    assert _object_action(spent, 'object.strongbox.open')['reason'] == "This turn's action is already spent."
    assert _object_action(spent, 'object.strongbox.inspect')['available'] is True


def _arrival_pack() -> dict:
    return {
        'packId': 'arrival_pack',
        'title': 'Arrival Pack',
        'startingState': {'locationId': 'gate', 'knownLocationIds': ['gate', 'hall']},
        'locations': [
            {
                'id': 'gate',
                'name': 'Gate',
                'connectedLocationIds': ['hall'],
                'onEnter': [{'type': 'flag.set', 'flagKey': 'gate_seen', 'flagValue': True}],
            },
            {
                'id': 'hall',
                'name': 'Hall',
                'connectedLocationIds': ['gate'],
                'onEnter': [
                    {'type': 'flag.set', 'flagKey': 'hall_alarm', 'flagValue': True},
                    {'type': 'clue.discover', 'clueId': 'clue_banner'},
                    {'type': 'quest.add', 'questId': 'quest_banner'},
                    {'type': 'location.discover', 'locationId': 'crypt'},
                    {'type': 'combat.end'},
                ],
            },
            {'id': 'crypt', 'name': 'Crypt', 'connectedLocationIds': ['hall']},
        ],
        'quests': [
            {'id': 'quest_gate', 'title': 'Hold the Gate', 'status': 'active'},
            {'id': 'quest_banner', 'title': 'The Torn Banner', 'status': 'active', 'summary': 'Find who tore it.'},
        ],
        'clues': [{'id': 'clue_banner', 'title': 'Torn Banner'}],
    }


def _move(state: dict, location_id: str, name: str, change_id: str) -> dict:
    return apply_state_changes(
        state,
        [{'id': change_id, 'type': 'scene.move_location', 'locationId': location_id, 'name': name}],
    )['nextState']


def test_on_enter_consequences_apply_once_on_the_first_arrival(client, app):
    response = client.post('/api/campaigns/import-pack', json={'pack': _arrival_pack()})
    assert response.status_code == 201, response.get_json()
    with app.app_context():
        snapshot = safe_json_loads(db.session.get(Session, response.get_json()['session_id']).state_snapshot, {})

    assert snapshot['flags']['gate_seen'] is True
    assert snapshot['flags']['locationEnteredIds'] == ['gate']

    hall = _move(snapshot, 'hall', 'Hall', 'move_hall')
    assert hall['flags']['hall_alarm'] is True
    assert hall['flags']['locationEnteredIds'] == ['gate', 'hall']
    assert [clue['title'] for clue in hall['clues']] == ['Torn Banner']
    quest = next(quest for quest in hall['quests'] if quest['id'] == 'quest_banner')
    assert (quest['title'], quest['summary']) == ('The Torn Banner', 'Find who tore it.')
    crypt = next(location for location in hall['locations'] if location['id'] == 'crypt')
    assert crypt['status'] == 'discovered'
    assert hall['combat']['status'] == 'none'

    hall['flags']['hall_alarm'] = False
    returned = _move(_move(hall, 'gate', 'Gate', 'move_gate'), 'hall', 'Hall', 'move_hall_again')
    assert returned['flags']['hall_alarm'] is False


def test_authored_consequences_materialise_catalog_records_they_name():
    state = _state()
    state['campaignPack']['catalog']['quests'] = [{'id': 'quest_vault', 'title': 'Crack the Vault', 'status': 'active'}]
    state['quests'] = [
        {
            'id': 'quest_scout',
            'title': 'Scout',
            'status': 'active',
            'objectives': [{'id': 'obj_open', 'description': 'Open the vault', 'completeWhen': {'flagKey': 'vault_open'}}],
            'onComplete': [
                {'type': 'quest.add', 'questId': 'quest_vault'},
                {'type': 'location.update', 'locationId': 'loc_cellar', 'description': 'Smoke drifts up.'},
            ],
        }
    ]

    result = apply_state_changes(state, [{'id': 'set_open', 'type': 'flag.set', 'flagKey': 'vault_open', 'flagValue': True}])
    state = result['nextState']

    assert next(quest for quest in state['quests'] if quest['id'] == 'quest_vault')['title'] == 'Crack the Vault'
    cellar = next(location for location in state['locations'] if location['id'] == 'loc_cellar')
    assert (cellar['name'], cellar['status']) == ('Smugglers Cellar', 'discovered')
    assert 'Smoke drifts up.' in cellar['description']
