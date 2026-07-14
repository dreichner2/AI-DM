from __future__ import annotations

import json

import aidm_server.game_state.orchestration.combat_resolution as combat_resolution_module
import aidm_server.game_state.validation.validator as validator_module
import aidm_server.player_rolls as player_rolls_module
from aidm_server.character_resources import ensure_character_sheet_spell_resources
from aidm_server.combat.state import default_turn_economy
from aidm_server.creatures.core_bestiary import core_creature
from aidm_server.database import db
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY
from aidm_server.models import DmTurn, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from aidm_server.spellbook import normalize_spellbook
from tests.helpers import seed_world_campaign_player_session


def _event_payload(received: list[dict], name: str) -> dict | None:
    for event in received:
        if event['name'] == name and event.get('args'):
            return event['args'][0]
    return None


def _status_payloads(received: list[dict], status: str) -> list[dict]:
    return [
        event['args'][0]
        for event in received
        if event['name'] == 'turn_status'
        and event.get('args')
        and event['args'][0].get('status') == status
    ]


def _join(socketio, app, ids: dict):
    client = socketio.test_client(app, flask_test_client=app.test_client())
    assert client.is_connected()
    client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    received = client.get_received()
    assert _event_payload(received, 'error') is None
    return client, received


def _send(client, ids: dict, *, message: str, client_message_id: str, intent: dict) -> list[dict]:
    client.emit(
        'send_message',
        {
            'session_id': ids['session_id'],
            'campaign_id': ids['campaign_id'],
            'world_id': ids['world_id'],
            'player_id': ids['player_id'],
            'message': message,
            'client_message_id': client_message_id,
            'action_intent': {
                **intent,
                'text': message,
                'client_message_id': client_message_id,
            },
        },
    )
    return client.get_received()


def _snapshot(ids: dict) -> dict:
    session = db.session.get(Session, ids['session_id'])
    assert session is not None
    return safe_json_loads(session.state_snapshot, {})


def test_real_socket_world_quest_travel_reconnect_and_next_dm_context(
    app,
    socketio,
    app_runtime,
    monkeypatch,
) -> None:
    socketio_module = app_runtime['modules']['socketio_events']
    captured_contexts: list[dict] = []

    def fake_stream(user_input, context, speaking_player=None, rules_hint=None):
        del speaking_player, rules_hint
        parsed_context = json.loads(context)
        captured_contexts.append(parsed_context)
        if 'silvered key' in (user_input or '').lower():
            yield 'The silvered key is now securely in Seraphina\'s pack.'
        elif 'uses the field tonic' in (user_input or '').lower():
            yield 'Seraphina consumes the field tonic; it is used up.'
        elif 'ruins' in (user_input or '').lower():
            yield 'Seraphina follows the validated road into the ruins.'
        elif 'camp' in (user_input or '').lower():
            yield 'Seraphina returns along the validated road to camp.'
        elif 'what changed' in (user_input or '').lower():
            assert parsed_context['live_world_state']['flags']['silvered_key_recovered'] is True
            yield (
                'Because Seraphina recovered the silvered key, the validated quest is complete '
                'and the campaign now follows the return-the-key branch.'
            )
        else:
            yield 'The consequence remains part of the world.'

    monkeypatch.setattr(socketio_module, 'query_dm_function_stream', fake_stream)
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        session = db.session.get(Session, ids['session_id'])
        assert player is not None and session is not None
        player.inventory = safe_json_dumps([], [])
        player.stats = safe_json_dumps(
            {'gold': 0, 'current_hp': 12, 'hp_current': 12, 'max_hp': 12, 'hp_max': 12, 'xp': 0},
            {},
        )
        actor_id = f"player_{player.player_id}"
        session.state_snapshot = safe_json_dumps(
            {
                'currentScene': {
                    'locationId': 'camp',
                    'name': 'Camp',
                    'sceneType': 'exploration',
                    'combatState': 'none',
                    'activeNpcIds': ['npc_scout'],
                    'activeQuestIds': ['recover_key'],
                    'items': [
                        {'id': 'iron_key_rusted', 'name': 'Iron Key', 'type': 'quest', 'quantity': 1},
                        {'id': 'iron_key_silvered', 'name': 'Iron Key', 'type': 'quest', 'quantity': 1},
                        {
                            'id': 'practice_blade',
                            'name': 'Practice Blade',
                            'type': 'weapon',
                            'subtype': 'longsword',
                            'quantity': 1,
                        },
                        {
                            'id': 'field_tonic',
                            'name': 'Field Tonic',
                            'type': 'consumable',
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
                            'description': 'Broken stones surround a dry well.',
                            'activeNpcIds': [],
                            'activeQuestIds': [],
                            'items': [{'id': 'chalk', 'name': 'Chalk', 'type': 'misc', 'quantity': 1}],
                        },
                    },
                ],
                'knownNpcs': [
                    {
                        'id': 'npc_scout',
                        'name': 'Camp Scout',
                        'status': 'met',
                        'locationId': 'camp',
                    }
                ],
                'quests': [
                    {
                        'id': 'recover_key',
                        'title': 'Recover the Silvered Key',
                        'status': 'active',
                        'xpReward': 25,
                        'rewards': {'gp': 4},
                        'onComplete': [
                            {'type': 'flag.set', 'flagKey': 'silvered_key_recovered', 'flagValue': True}
                        ],
                        'objectives': [
                            {
                                'id': 'take_silvered_key',
                                'description': 'Take the exact silvered key.',
                                'status': 'open',
                                'completeWhen': {
                                    'eventType': 'inventory.add',
                                    'actorId': actor_id,
                                    'itemId': 'iron_key_silvered',
                                },
                            }
                        ],
                    }
                ],
                'flags': {'campaignPackActiveCheckpointId': 'cp_recover_key'},
                'campaignPack': {
                    'packId': 'runtime_key_branch',
                    'title': 'The Silvered Key',
                    'activeCheckpointId': 'cp_recover_key',
                    'completedCheckpointIds': [],
                    'checkpoints': [
                        {
                            'id': 'cp_recover_key',
                            'title': 'Recover the key',
                            'questIds': ['recover_key'],
                            'nextCheckpointIds': ['cp_force', 'cp_mercy', 'cp_fallback'],
                        },
                        {
                            'id': 'cp_force',
                            'title': 'Force the lock',
                            'branchWhen': {'flagKey': 'silvered_key_recovered', 'equals': False},
                        },
                        {
                            'id': 'cp_mercy',
                            'title': 'Return the recovered key',
                            'branchWhen': {'flagKey': 'silvered_key_recovered', 'equals': True},
                        },
                        {'id': 'cp_fallback', 'title': 'Search for another route'},
                    ],
                    'directorRules': {'offTrackPolicy': 'improvise_and_reconnect'},
                },
                'stateChangeLedger': [],
            },
            {},
        )
        db.session.commit()

    client, _ = _join(socketio, app, ids)
    pickup = _send(
        client,
        ids,
        message='Seraphina picks up the silvered key.',
        client_message_id='runtime-pickup-silver-key',
        intent={
            'kind': 'item',
            'source': 'scene_panel',
            'inventory_action': 'pick_up',
            'item': {'id': 'iron_key_silvered', 'name': 'Iron Key', 'quantity': 1},
        },
    )
    assert _event_payload(pickup, 'error') is None
    assert _status_payloads(pickup, 'state_applied')
    with app.app_context():
        state = _snapshot(ids)
        actor = state['playerCharacters'][0]
        assert [item['id'] for item in state['currentScene']['items']] == [
            'iron_key_rusted',
            'practice_blade',
            'field_tonic',
        ]
        assert [item['id'] for item in actor['inventory']['items']] == ['iron_key_silvered']
        assert state['quests'][0]['status'] == 'completed'
        assert state['quests'][0]['objectives'][0]['status'] == 'completed'
        assert actor['xp']['current'] == 25
        assert actor['inventory']['currency']['gp'] == 4
        assert state['flags']['silvered_key_recovered'] is True
        assert state['campaignPack']['completedCheckpointIds'] == ['cp_recover_key']
        assert state['campaignPack']['activeCheckpointId'] == 'cp_mercy'

    for message_id, item_id, item_name in (
        ('runtime-pickup-practice-blade', 'practice_blade', 'Practice Blade'),
        ('runtime-pickup-field-tonic', 'field_tonic', 'Field Tonic'),
    ):
        pickup_item = _send(
            client,
            ids,
            message=f'Seraphina picks up the {item_name}.',
            client_message_id=message_id,
            intent={
                'kind': 'item',
                'source': 'scene_panel',
                'inventory_action': 'pick_up',
                'item': {'id': item_id, 'name': item_name, 'quantity': 1},
            },
        )
        assert _event_payload(pickup_item, 'error') is None

    equip_blade = _send(
        client,
        ids,
        message='Seraphina equips the practice blade.',
        client_message_id='runtime-equip-practice-blade',
        intent={
            'kind': 'item',
            'source': 'composer',
            'inventory_action': 'equip',
            'item': {'id': 'practice_blade', 'name': 'Practice Blade', 'quantity': 1},
        },
    )
    assert _event_payload(equip_blade, 'error') is None

    use_tonic = _send(
        client,
        ids,
        message='Seraphina uses the field tonic.',
        client_message_id='runtime-use-field-tonic',
        intent={
            'kind': 'item',
            'source': 'composer',
            'inventory_action': 'use',
            'item': {'id': 'field_tonic', 'name': 'Field Tonic', 'quantity': 1},
        },
    )
    assert _event_payload(use_tonic, 'error') is None
    with app.app_context():
        state = _snapshot(ids)
        actor = state['playerCharacters'][0]
        inventory_by_id = {item['id']: item for item in actor['inventory']['items']}
        assert set(inventory_by_id) == {'iron_key_silvered', 'practice_blade'}
        assert inventory_by_id['practice_blade']['equipped'] is True
        assert inventory_by_id['practice_blade']['slot'] == 'main_hand'
        assert [item['id'] for item in state['currentScene']['items']] == ['iron_key_rusted']

    to_ruins = _send(
        client,
        ids,
        message='The party travels to Ruins.',
        client_message_id='runtime-travel-ruins',
        intent={
            'kind': 'travel',
            'source': 'scene_panel',
            'location': {'id': 'ruins', 'name': 'Ruins'},
        },
    )
    assert _event_payload(to_ruins, 'error') is None
    with app.app_context():
        state = _snapshot(ids)
        assert state['currentScene']['locationId'] == 'ruins'
        assert [item['id'] for item in state['currentScene']['items']] == ['chalk']
        assert 'npc_scout' not in state['currentScene']['activeNpcIds']
        turn_count_before_absent_interaction = DmTurn.query.filter_by(
            session_id=ids['session_id']
        ).count()

    absent_scout = _send(
        client,
        ids,
        message='Seraphina asks the Camp Scout what they can see.',
        client_message_id='runtime-absent-scout-interaction',
        intent={
            'kind': 'interact',
            'source': 'scene_panel',
            'interaction': {'type': 'speak_to', 'label': 'Speak to'},
            'target': {
                'kind': 'npc',
                'npc_id': 'npc_scout',
                'character_name': 'Camp Scout',
                'player_name': 'Current scene NPC',
            },
        },
    )
    absent_scout_error = _event_payload(absent_scout, 'error')
    assert absent_scout_error is not None
    assert absent_scout_error['error_code'] == 'interaction_target_invalid'
    with app.app_context():
        assert _snapshot(ids)['currentScene']['locationId'] == 'ruins'
        assert (
            DmTurn.query.filter_by(session_id=ids['session_id']).count()
            == turn_count_before_absent_interaction
        )

    to_camp = _send(
        client,
        ids,
        message='The party travels to Camp.',
        client_message_id='runtime-travel-camp',
        intent={
            'kind': 'travel',
            'source': 'scene_panel',
            'location': {'id': 'camp', 'name': 'Camp'},
        },
    )
    assert _event_payload(to_camp, 'error') is None
    with app.app_context():
        state = _snapshot(ids)
        assert state['currentScene']['locationId'] == 'camp'
        assert [item['id'] for item in state['currentScene']['items']] == ['iron_key_rusted']
        assert state['currentScene']['activeNpcIds'] == ['npc_scout']

    follow_up = _send(
        client,
        ids,
        message='What changed because I recovered the key?',
        client_message_id='runtime-consequence-recall',
        intent={'kind': 'message', 'source': 'composer'},
    )
    assert _event_payload(follow_up, 'error') is None
    latest_context = captured_contexts[-1]
    assert latest_context['live_world_state']['flags']['silvered_key_recovered'] is True
    assert latest_context['live_world_state']['activeQuests'][0]['status'] == 'completed'
    with app.app_context():
        follow_up_turn = DmTurn.query.filter_by(
            client_message_id='runtime-consequence-recall'
        ).one()
        assert 'recovered the silvered key' in follow_up_turn.dm_output
        assert 'return-the-key branch' in follow_up_turn.dm_output

    client.disconnect()
    reconnect, reconnect_events = _join(socketio, app, ids)
    scene = _event_payload(reconnect_events, 'scene_state')
    assert scene['location_id'] == 'camp'
    assert scene['location_name'] == 'Camp'
    reconnect.disconnect()

    with app.app_context():
        state = _snapshot(ids)
        player_projection = filter_session_snapshot_for_player(
            state,
            private_player_ids=frozenset({ids['player_id']}),
        )
        projected_actor = player_projection['playerCharacters'][0]
        projected_inventory = {
            item['id']: item for item in projected_actor['inventory']['items']
        }
        assert set(projected_inventory) == {'iron_key_silvered', 'practice_blade'}
        assert projected_inventory['practice_blade']['equipped'] is True
        assert projected_inventory['practice_blade']['slot'] == 'main_hand'
        assert player_projection['quests'][0]['status'] == 'completed'
        assert player_projection['currentScene']['locationId'] == 'camp'
        assert 'catalog' not in player_projection.get('campaignPack', {})
        projected_checkpoints = player_projection['campaignPack']['checkpoints']
        assert [checkpoint['id'] for checkpoint in projected_checkpoints] == [
            'cp_recover_key',
            'cp_mercy',
        ]
        assert all('branchWhen' not in checkpoint for checkpoint in projected_checkpoints)
        assert all('nextCheckpointIds' not in checkpoint for checkpoint in projected_checkpoints)
        assert 'directorRules' not in player_projection['campaignPack']
        assert 'branchWhen' not in json.dumps(player_projection)


def test_real_socket_spell_exhaustion_rest_retry_and_reload(
    app,
    socketio,
    app_runtime,
    monkeypatch,
) -> None:
    socketio_module = app_runtime['modules']['socketio_events']
    streamed_inputs: list[str] = []

    def fake_stream(user_input, context, speaking_player=None, rules_hint=None):
        del context, speaking_player, rules_hint
        streamed_inputs.append(str(user_input or ''))
        if 'rest' in (user_input or '').lower():
            yield 'The completed rest restores only the resources allowed by the rules.'
        else:
            yield 'The validated spell resource is spent before its magic takes effect.'

    monkeypatch.setattr(socketio_module, 'query_dm_function_stream', fake_stream)
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        session = db.session.get(Session, ids['session_id'])
        assert player is not None and session is not None
        player.class_ = 'Wizard'
        player.level = 1
        spellbook = normalize_spellbook(
            {
                'knownSpells': [
                    {'id': 'spell_magic_missile', 'name': 'Magic Missile', 'level': 1},
                ]
            },
            class_name='Wizard',
        )
        sheet, _ = ensure_character_sheet_spell_resources(
            {'spellbook': spellbook},
            class_name='Wizard',
            level=1,
        )
        player.character_sheet = safe_json_dumps(sheet, {})
        player.stats = safe_json_dumps(
            {
                'current_hp': 3,
                'hp_current': 3,
                'max_hp': 10,
                'hp_max': 10,
                'temp_hp': 2,
                'conditions': ['scarred'],
            },
            {},
        )
        session.state_snapshot = safe_json_dumps(
            {
                'currentScene': {
                    'locationId': 'camp',
                    'name': 'Camp',
                    'sceneType': 'exploration',
                    'combatState': 'none',
                    'activeNpcIds': [],
                    'items': [],
                },
                'stateChangeLedger': [],
            },
            {},
        )
        db.session.commit()

    client, _ = _join(socketio, app, ids)

    def cast(message_id: str) -> list[dict]:
        return _send(
            client,
            ids,
            message='Seraphina casts Magic Missile.',
            client_message_id=message_id,
            intent={
                'kind': 'spell',
                'source': 'composer',
                'spell': {'name': 'Magic Missile', 'effect': 'Force darts strike the chosen target.'},
            },
        )

    first = cast('runtime-cast-1')
    second = cast('runtime-cast-2')
    assert _event_payload(first, 'error') is None
    assert _event_payload(second, 'error') is None
    assert len(streamed_inputs) == 2
    with app.app_context():
        state = _snapshot(ids)
        assert state['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 0
        cast_entries = [entry for entry in state['stateChangeLedger'] if entry['type'] == 'spell.cast']
        assert len(cast_entries) == 2

    exhausted = cast('runtime-cast-3')
    exhausted_error = _event_payload(exhausted, 'error')
    assert exhausted_error is not None
    assert exhausted_error['error_code'] == 'gameplay_action_invalid'
    assert 'No legal level 1 or higher spell resource remains' in exhausted_error['error']
    assert len(streamed_inputs) == 2
    with app.app_context():
        state = _snapshot(ids)
        assert state['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 0
        assert len([entry for entry in state['stateChangeLedger'] if entry['type'] == 'spell.cast']) == 2
        exhausted_turn = DmTurn.query.filter_by(client_message_id='runtime-cast-3').one()
        assert exhausted_turn.status == 'completed'
        assert exhausted_turn.llm_provider == 'rules_engine'
        assert 'No legal level 1 or higher spell resource remains' in exhausted_turn.dm_output

    duplicate = cast('runtime-cast-2')
    assert _event_payload(duplicate, 'turn_duplicate') is not None
    with app.app_context():
        state = _snapshot(ids)
        assert len([entry for entry in state['stateChangeLedger'] if entry['type'] == 'spell.cast']) == 2

    short_rest = _send(
        client,
        ids,
        message='Seraphina completes a short rest.',
        client_message_id='runtime-short-rest',
        intent={'kind': 'rest', 'source': 'scene_panel', 'rest_type': 'short_rest'},
    )
    assert _event_payload(short_rest, 'error') is None
    with app.app_context():
        actor = _snapshot(ids)['playerCharacters'][0]
        assert actor['spellResources']['slots']['1']['current'] == 0
        assert actor['health']['currentHp'] == 3
        assert actor['health']['tempHp'] == 2
        assert actor['health']['conditions'] == ['scarred']

    long_rest = _send(
        client,
        ids,
        message='Seraphina completes a long rest.',
        client_message_id='runtime-long-rest',
        intent={'kind': 'rest', 'source': 'scene_panel', 'rest_type': 'long_rest'},
    )
    assert _event_payload(long_rest, 'error') is None
    with app.app_context():
        state = _snapshot(ids)
        actor = state['playerCharacters'][0]
        assert actor['spellResources']['slots']['1'] == {'current': 2, 'max': 2}
        assert actor['health']['currentHp'] == 10
        assert actor['health']['tempHp'] == 0
        assert actor['health']['conditions'] == ['scarred']
        sheet = safe_json_loads(db.session.get(Player, ids['player_id']).character_sheet, {})
        assert sheet['spellResources']['slots']['1'] == {'current': 2, 'max': 2}

    client.disconnect()
    reconnect, _ = _join(socketio, app, ids)
    reconnect.disconnect()
    with app.app_context():
        state = _snapshot(ids)
        assert state['playerCharacters'][0]['spellResources']['slots']['1'] == {'current': 2, 'max': 2}
        assert DmTurn.query.filter_by(session_id=ids['session_id']).count() == 5


def test_real_socket_interactable_quest_stale_retry_context_and_reload(
    app,
    socketio,
    app_runtime,
    monkeypatch,
) -> None:
    socketio_module = app_runtime['modules']['socketio_events']
    captured_contexts: list[dict] = []
    captured_rule_hints: list[dict] = []

    def fake_stream(user_input, context, speaking_player=None, rules_hint=None):
        del speaking_player
        captured_contexts.append(json.loads(context))
        captured_rule_hints.append(rules_hint)
        if 'what changed' in str(user_input or '').lower():
            yield 'The spent bronze lever remains depleted, and the sealed passage is now open.'
        else:
            yield 'Seraphina pulls the validated bronze lever once; the sealed passage opens.'

    monkeypatch.setattr(socketio_module, 'query_dm_function_stream', fake_stream)
    ids = seed_world_campaign_player_session(app)
    actor_id = f"player_{ids['player_id']}"
    lever = {
        'id': 'bronze_lever',
        'name': 'Bronze Lever',
        'kind': 'object',
        'usable': True,
        'depletable': True,
        'usesRemaining': 1,
        'used': False,
        'depleted': False,
        'playerKnown': True,
        'revision': 0,
    }
    hidden_hazard = {
        'id': 'sealed_needle_trap',
        'name': 'Sealed Needle Trap',
        'kind': 'hazard',
        'active': True,
        'hidden': True,
        'playerKnown': False,
        'revision': 0,
    }
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        session = db.session.get(Session, ids['session_id'])
        assert player is not None and session is not None
        player.stats = safe_json_dumps(
            {'current_hp': 12, 'hp_current': 12, 'max_hp': 12, 'hp_max': 12, 'xp': 0, 'gold': 0},
            {},
        )
        session.state_snapshot = safe_json_dumps(
            {
                'currentScene': {
                    'locationId': 'sealed_gallery',
                    'name': 'Sealed Gallery',
                    'sceneType': 'exploration',
                    'combatState': 'none',
                    'activeNpcIds': [],
                    'activeQuestIds': ['open_sealed_passage'],
                    'items': [],
                    'interactables': [lever],
                    'hazards': [hidden_hazard],
                },
                'locations': [
                    {
                        'id': 'sealed_gallery',
                        'name': 'Sealed Gallery',
                        'status': 'visited',
                        'connectedLocationIds': [],
                        'sceneState': {
                            'interactables': [lever],
                            'hazards': [hidden_hazard],
                        },
                    }
                ],
                'quests': [
                    {
                        'id': 'open_sealed_passage',
                        'title': 'Open the Sealed Passage',
                        'status': 'active',
                        'completionPolicy': 'all',
                        'xpReward': 15,
                        'rewards': {'gp': 2},
                        'onComplete': [
                            {
                                'type': 'flag.set',
                                'flagKey': 'sealed_passage_open',
                                'flagValue': True,
                            }
                        ],
                        'objectives': [
                            {
                                'id': 'use_exact_lever',
                                'description': 'Use the bronze lever once.',
                                'status': 'open',
                                'completeWhen': {
                                    'eventType': 'interactable.used',
                                    'actorId': actor_id,
                                    'targetId': 'bronze_lever',
                                },
                            }
                        ],
                    }
                ],
                'flags': {},
                'stateChangeLedger': [],
            },
            {},
        )
        db.session.commit()

    client, _ = _join(socketio, app, ids)
    use_lever = _send(
        client,
        ids,
        message='Seraphina uses the bronze lever.',
        client_message_id='runtime-use-bronze-lever',
        intent={
            'kind': 'object',
            'source': 'scene_panel',
            'object': {'id': 'bronze_lever', 'action': 'use', 'revision': 0},
        },
    )
    assert _event_payload(use_lever, 'error') is None
    applied_for_narration = captured_rule_hints[0]['state_pipeline']['stateChangesAlreadyApplied']
    assert applied_for_narration[0]['type'] == 'scene.interactable.action'
    assert applied_for_narration[0]['targetId'] == 'bronze_lever'
    assert applied_for_narration[0]['eventType'] == 'interactable.used'
    with app.app_context():
        state = _snapshot(ids)
        current_lever = state['currentScene']['interactables'][0]
        persisted_lever = state['locations'][0]['sceneState']['interactables'][0]
        assert current_lever['usesRemaining'] == 0
        assert current_lever['depleted'] is True
        assert current_lever['revision'] == 1
        assert persisted_lever == current_lever
        assert state['quests'][0]['status'] == 'completed'
        assert state['quests'][0]['objectives'][0]['status'] == 'completed'
        assert state['playerCharacters'][0]['xp']['current'] == 15
        assert state['playerCharacters'][0]['inventory']['currency']['gp'] == 2
        assert state['flags']['sealed_passage_open'] is True

    duplicate = _send(
        client,
        ids,
        message='Seraphina uses the bronze lever.',
        client_message_id='runtime-use-bronze-lever',
        intent={
            'kind': 'object',
            'source': 'scene_panel',
            'object': {'id': 'bronze_lever', 'action': 'use', 'revision': 0},
        },
    )
    assert _event_payload(duplicate, 'turn_duplicate') is not None

    stale = _send(
        client,
        ids,
        message='Seraphina tries the old lever state again.',
        client_message_id='runtime-stale-bronze-lever',
        intent={
            'kind': 'object',
            'source': 'scene_panel',
            'object': {'id': 'bronze_lever', 'action': 'use', 'revision': 0},
        },
    )
    stale_error = _event_payload(stale, 'error')
    assert stale_error is not None
    assert stale_error['error_code'] == 'gameplay_action_invalid'

    follow_up = _send(
        client,
        ids,
        message='What changed in this gallery?',
        client_message_id='runtime-object-consequence-recall',
        intent={'kind': 'message', 'source': 'composer'},
    )
    assert _event_payload(follow_up, 'error') is None
    latest_live_state = captured_contexts[-1]['live_world_state']
    assert latest_live_state['currentScene']['interactables'][0]['id'] == 'bronze_lever'
    assert latest_live_state['currentScene']['interactables'][0]['depleted'] is True
    assert latest_live_state['currentScene']['interactables'][0]['revision'] == 1
    assert latest_live_state['currentScene']['hazards'] == []
    assert latest_live_state['flags']['sealed_passage_open'] is True
    client.disconnect()

    reconnect, _ = _join(socketio, app, ids)
    reconnect.disconnect()
    with app.app_context():
        db.session.expire_all()
        reloaded = _snapshot(ids)
        assert reloaded['currentScene']['interactables'][0]['depleted'] is True
        assert reloaded['locations'][0]['sceneState']['interactables'][0]['revision'] == 1
        assert reloaded['playerCharacters'][0]['xp']['current'] == 15
        assert reloaded['playerCharacters'][0]['inventory']['currency']['gp'] == 2


def test_real_socket_targeted_spell_resolves_before_narration_and_reload(
    app,
    socketio,
    app_runtime,
    monkeypatch,
) -> None:
    socketio_module = app_runtime['modules']['socketio_events']
    captured_rule_hints: list[dict] = []

    def fake_stream(user_input, context, speaking_player=None, rules_hint=None):
        del user_input, context, speaking_player
        captured_rule_hints.append(rules_hint)
        resolution = rules_hint['state_pipeline']['stateChangesAlreadyApplied'][0]['spellResolution']
        assert resolution['spellName'] == 'Magic Missile'
        assert resolution['targetIds'] == ['enemy_ogre']
        yield 'Three validated force darts strike the exact ogre target for 12 force damage.'

    monkeypatch.setattr(socketio_module, 'query_dm_function_stream', fake_stream)
    monkeypatch.setattr(validator_module.secrets, 'randbelow', lambda _sides: 2)
    ids = seed_world_campaign_player_session(app)
    actor_id = f"player_{ids['player_id']}"
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        session = db.session.get(Session, ids['session_id'])
        assert player is not None and session is not None
        player.class_ = 'Wizard'
        player.level = 1
        spellbook = normalize_spellbook(
            {'knownSpells': [{'id': 'spell_magic_missile', 'name': 'Magic Missile', 'level': 1}]},
            class_name='Wizard',
        )
        sheet, _ = ensure_character_sheet_spell_resources(
            {'spellbook': spellbook},
            class_name='Wizard',
            level=1,
        )
        player.character_sheet = safe_json_dumps(sheet, {})
        player.stats = safe_json_dumps(
            {
                'strength': 8,
                'dexterity': 14,
                'constitution': 12,
                'intelligence': 16,
                'wisdom': 10,
                'charisma': 8,
                'current_hp': 10,
                'hp_current': 10,
                'max_hp': 10,
                'hp_max': 10,
            },
            {},
        )
        session.state_snapshot = safe_json_dumps(
            {
                'currentScene': {
                    'locationId': 'ogre_hall',
                    'name': 'Ogre Hall',
                    'sceneType': 'combat',
                    'combatState': 'active',
                    'activeNpcIds': [],
                    'items': [],
                },
                'combat': {
                    'status': 'active',
                    'round': 3,
                    'turnIndex': 0,
                    'participants': [
                        {
                            'id': actor_id,
                            'name': 'Seraphina',
                            'team': 'player',
                            'kind': 'player_character',
                            'level': 1,
                            'stats': {
                                'strength': 8,
                                'dexterity': 14,
                                'constitution': 12,
                                'intelligence': 16,
                                'wisdom': 10,
                                'charisma': 8,
                            },
                            'hp': {'current': 10, 'max': 10, 'temp': 0},
                            'armorClass': 12,
                            'conditions': [],
                            'position': {'rangeBand': 'near'},
                            'isAlive': True,
                            'isConscious': True,
                            'isPresent': True,
                        },
                        {
                            'id': 'enemy_ogre',
                            'name': 'Ogre',
                            'team': 'enemy',
                            'kind': 'creature',
                            'level': 2,
                            'stats': {'strength': 18, 'dexterity': 8, 'constitution': 16},
                            'hp': {'current': 30, 'max': 30, 'temp': 0},
                            'armorClass': 11,
                            'conditions': [],
                            'position': {'rangeBand': 'near'},
                            'isAlive': True,
                            'isConscious': True,
                            'isPresent': True,
                        },
                    ],
                    'initiative': [
                        {
                            'participantId': actor_id,
                            'name': 'Seraphina',
                            'roll': 16,
                            'modifier': 2,
                            'total': 18,
                            'order': 0,
                        },
                        {
                            'participantId': 'enemy_ogre',
                            'name': 'Ogre',
                            'roll': 8,
                            'modifier': -1,
                            'total': 7,
                            'order': 1,
                        },
                    ],
                    'battlefield': {},
                    'flags': {
                        'activeActorId': actor_id,
                        'turnEconomy': default_turn_economy(actor_id, 3),
                    },
                },
                'stateChangeLedger': [],
            },
            {},
        )
        db.session.commit()

    client, _ = _join(socketio, app, ids)
    cast = _send(
        client,
        ids,
        message='Seraphina casts Magic Missile at the ogre.',
        client_message_id='runtime-targeted-magic-missile',
        intent={
            'kind': 'spell',
            'source': 'composer',
            'spell': {
                'name': 'Magic Missile',
                'effect': 'Three force darts strike the selected target.',
                'target_ids': ['enemy_ogre'],
            },
        },
    )
    assert _event_payload(cast, 'error') is None
    assert len(captured_rule_hints) == 1
    with app.app_context():
        state = _snapshot(ids)
        enemy = next(item for item in state['combat']['participants'] if item['id'] == 'enemy_ogre')
        assert enemy['hp']['current'] == 18
        actor = state['playerCharacters'][0]
        assert actor['spellResources']['slots']['1']['current'] == 1
        assert state['combat']['flags']['turnEconomy']['actionRemaining'] == 0

    repeated = _send(
        client,
        ids,
        message='Seraphina casts Magic Missile again without another action.',
        client_message_id='runtime-targeted-magic-missile-again',
        intent={
            'kind': 'spell',
            'source': 'composer',
            'spell': {
                'name': 'Magic Missile',
                'effect': 'Three force darts strike the selected target.',
                'target_ids': ['enemy_ogre'],
            },
        },
    )
    repeated_error = _event_payload(repeated, 'error')
    assert repeated_error is not None
    assert repeated_error['error_code'] == 'gameplay_action_invalid'
    assert len(captured_rule_hints) == 1
    client.disconnect()

    reconnect, reconnect_events = _join(socketio, app, ids)
    assert _event_payload(reconnect_events, 'scene_state')['in_combat'] is True
    reconnect.disconnect()
    with app.app_context():
        db.session.expire_all()
        state = _snapshot(ids)
        enemy = next(item for item in state['combat']['participants'] if item['id'] == 'enemy_ogre')
        assert enemy['hp']['current'] == 18
        assert state['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 1
        assert state['combat']['flags']['activeActorId'] == actor_id
        assert state['combat']['flags']['turnEconomy']['actionRemaining'] == 0


def test_real_socket_combat_lifecycle_tactic_change_nonlethal_end_and_reload(
    app,
    socketio,
    app_runtime,
    monkeypatch,
) -> None:
    socketio_module = app_runtime['modules']['socketio_events']

    def fake_stream(user_input, context, speaking_player=None, rules_hint=None):
        del user_input, speaking_player, rules_hint
        parsed = json.loads(context)
        assert parsed['live_world_state']['combat']['status'] in {'active', 'ended'}
        yield 'The rules-engine result is narrated exactly as resolved.'

    monkeypatch.setattr(socketio_module, 'query_dm_function_stream', fake_stream)
    monkeypatch.setattr(
        player_rolls_module.secrets,
        'randbelow',
        lambda sides: 14 if sides == 20 else 5,
    )
    monkeypatch.setattr(
        combat_resolution_module,
        '_deterministic_damage_roller',
        lambda _seed: (lambda _sides: 3),
    )
    monkeypatch.setattr(
        combat_resolution_module.random,
        'randint',
        lambda _minimum, maximum: 15 if maximum == 20 else 2,
    )

    ids = seed_world_campaign_player_session(app)
    actor_id = f"player_{ids['player_id']}"
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        session = db.session.get(Session, ids['session_id'])
        assert player is not None and session is not None
        player.class_ = 'Fighter'
        player.level = 3
        player.stats = safe_json_dumps(
            {
                'strength': 18,
                'dexterity': 14,
                'current_hp': 20,
                'hp_current': 20,
                'max_hp': 20,
                'hp_max': 20,
            },
            {},
        )
        player.inventory = safe_json_dumps(
            [
                {
                    'id': 'blade',
                    'name': 'Longsword',
                    'type': 'weapon',
                    'subtype': 'longsword',
                    'equipped': True,
                    'slot': 'main_hand',
                }
            ],
            [],
        )
        session.state_snapshot = safe_json_dumps(
            {
                'currentScene': {
                    'locationId': 'wolf_den',
                    'name': 'Wolf Den',
                    'sceneType': 'exploration',
                    'combatState': 'none',
                    'activeNpcIds': [],
                    'items': [],
                },
                'stateChangeLedger': [],
            },
            {},
        )
        db.session.commit()

    start_response = app.test_client().post(
        f"/api/sessions/{ids['session_id']}/combat/start",
        json={
            'creature': core_creature('wolf'),
            'enemyCount': 1,
            'battlefield': {
                'environmentType': 'cavern',
                'lighting': 'dim',
                'visibility': 'clear',
            },
        },
    )
    assert start_response.status_code == 200
    started_combat = start_response.get_json()['combat']
    assert started_combat['status'] == 'active'
    assert len(started_combat['initiative']) == 2
    assert {participant['team'] for participant in started_combat['participants']} == {'player', 'enemy'}
    enemy_id = next(
        participant['id']
        for participant in started_combat['participants']
        if participant['team'] == 'enemy'
    )

    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        assert session is not None
        state = _snapshot(ids)
        combat = state['combat']
        player_participant = next(
            participant for participant in combat['participants'] if participant['id'] == actor_id
        )
        player_participant.update(
            {
                'hp': {'current': 20, 'max': 20, 'temp': 0},
                'armorClass': 12,
                'stats': {'strength': 18, 'dexterity': 14},
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'isAlive': True,
                'isConscious': True,
            }
        )
        enemy = next(
            participant for participant in combat['participants'] if participant['id'] == enemy_id
        )
        enemy['hp'] = {'current': 20, 'max': 20, 'temp': 0}
        enemy['behavior']['fleeThreshold'] = 40
        enemy['behavior']['survivalRules'].update(
            {
                'fleeBelowHpPercent': 40,
                'fleeIfAlone': False,
                'fleeIfOutnumbered': False,
                'protectSelfIfBloodied': False,
            }
        )
        combat.update(
            {
                'round': 1,
                'turnIndex': 0,
                'initiative': [
                    {
                        'participantId': actor_id,
                        'name': 'Seraphina',
                        'roll': 16,
                        'modifier': 2,
                        'total': 18,
                        'order': 0,
                        'source': 'runtime_test',
                    },
                    {
                        'participantId': enemy_id,
                        'name': 'Wolf',
                        'roll': 11,
                        'modifier': 2,
                        'total': 13,
                        'order': 1,
                        'source': 'runtime_test',
                    },
                ],
                'flags': {
                    **(combat.get('flags') or {}),
                    'activeActorId': actor_id,
                    'turnEconomy': default_turn_economy(actor_id, 1),
                    'combatDifficultyAI': {
                        'tacticalLevel': 'simple',
                        'allowSentientEnemyBrain': False,
                        'maxLlmCallsPerRound': 0,
                    },
                },
            },
        )
        session.state_snapshot = safe_json_dumps(state, {})
        db.session.commit()

    client, joined = _join(socketio, app, ids)
    joined_scene = _event_payload(joined, 'scene_state')
    assert joined_scene['in_combat'] is True

    first_attack = _send(
        client,
        ids,
        message='I attack the wolf.',
        client_message_id='runtime-combat-attack-round-1',
        intent={
            'kind': 'combat',
            'source': 'combat_hud',
            'combat': {'action_id': 'combat.attack.blade', 'target_id': enemy_id},
        },
    )
    assert _event_payload(first_attack, 'error') is None
    first_roll = _event_payload(first_attack, 'roll_resolved')
    assert first_roll['authoritative'] is True
    assert first_roll['total'] == 19
    with app.app_context():
        state = _snapshot(ids)
        assert state['combat']['round'] == 1
        assert state['combat']['turnIndex'] == 0
        assert state['combat']['flags']['activeActorId'] == actor_id
        assert state['combat']['flags']['turnEconomy']['actionRemaining'] == 0
        assert [entry['participantId'] for entry in state['combat']['initiative']] == [actor_id, enemy_id]
        enemy = next(item for item in state['combat']['participants'] if item['id'] == enemy_id)
        assert enemy['hp']['current'] == 13

    repeated_attack = _send(
        client,
        ids,
        message='I attack again without ending my turn.',
        client_message_id='runtime-combat-illegal-second-action',
        intent={
            'kind': 'combat',
            'source': 'combat_hud',
            'combat': {'action_id': 'combat.attack.blade', 'target_id': enemy_id},
        },
    )
    action_error = _event_payload(repeated_attack, 'error')
    assert action_error['error_code'] == 'combat_action_unavailable'
    assert "action is already spent" in action_error['error']

    end_turn = _send(
        client,
        ids,
        message='I end my turn.',
        client_message_id='runtime-combat-end-round-1',
        intent={
            'kind': 'combat',
            'source': 'combat_hud',
            'combat': {'action_id': 'combat.end_turn'},
        },
    )
    assert _event_payload(end_turn, 'error') is None
    with app.app_context():
        state = _snapshot(ids)
        assert state['combat']['round'] == 2
        assert state['combat']['turnIndex'] == 0
        assert state['combat']['flags']['activeActorId'] == actor_id
        assert state['combat']['flags']['lastEnemyTurnBlock'] == [enemy_id]
        assert state['combat']['flags']['turnEconomy'] == default_turn_economy(actor_id, 2)
        actor = next(item for item in state['combat']['participants'] if item['id'] == actor_id)
        assert actor['hp']['current'] == 16
        end_turn_record = DmTurn.query.filter_by(client_message_id='runtime-combat-end-round-1').one()
        metadata = safe_json_loads(end_turn_record.metadata_json, {})
        combat_context = metadata[STATE_PIPELINE_METADATA_KEY]['dmContextPacket']['combatState']
        resolved_enemy = combat_context['enemyResolvedActions'][0]
        assert resolved_enemy['enemyId'] == enemy_id
        assert resolved_enemy['targetId'] == actor_id
        assert resolved_enemy['abilityId'] == 'wolf_bite'
        assert resolved_enemy['intentType'] == 'attack'
        assert resolved_enemy['hit'] is True
        assert resolved_enemy['damageTotal'] == 4
        enemy = next(item for item in state['combat']['participants'] if item['id'] == enemy_id)
        assert resolved_enemy['abilityId'] in {ability['id'] for ability in enemy['abilities']}

    client.disconnect()
    with app.app_context():
        db.session.expire_all()
        reloaded = _snapshot(ids)
        assert reloaded['combat']['round'] == 2
        assert reloaded['combat']['flags']['activeActorId'] == actor_id
        assert reloaded['combat']['flags']['turnEconomy'] == default_turn_economy(actor_id, 2)
        assert next(
            item for item in reloaded['combat']['participants'] if item['id'] == actor_id
        )['hp']['current'] == 16
        assert reloaded['playerCharacters'][0]['class'] == 'Fighter'
        assert reloaded['playerCharacters'][0]['classFeatureState']['second_wind']['current'] == 1
        assert reloaded['playerCharacters'][0]['health']['currentHp'] == 16

    reconnected, reconnect_events = _join(socketio, app, ids)
    reconnect_scene = _event_payload(reconnect_events, 'scene_state')
    assert reconnect_scene['in_combat'] is True
    second_wind = _send(
        reconnected,
        ids,
        message='I use Second Wind before pressing the wolf.',
        client_message_id='runtime-combat-second-wind-round-2',
        intent={
            'kind': 'capability',
            'source': 'combat_hud',
            'capability': {'id': 'second_wind', 'target_id': actor_id},
        },
    )
    assert _event_payload(second_wind, 'error') is None
    with app.app_context():
        state = _snapshot(ids)
        second_wind_turn = DmTurn.query.filter_by(
            client_message_id='runtime-combat-second-wind-round-2'
        ).one()
        second_wind_pipeline = safe_json_loads(second_wind_turn.metadata_json, {})[
            STATE_PIPELINE_METADATA_KEY
        ]
        assert [
            change['type'] for change in second_wind_pipeline['immediateAppliedChanges']
        ] == ['class_feature.use']
        actor = next(item for item in state['combat']['participants'] if item['id'] == actor_id)
        assert actor['hp']['current'] == 20
        assert state['playerCharacters'][0]['health']['currentHp'] == 20
        assert state['playerCharacters'][0]['classFeatureState']['second_wind']['current'] == 0
        assert state['combat']['flags']['turnEconomy']['bonusActionRemaining'] == 0
        assert state['combat']['flags']['turnEconomy']['actionRemaining'] == 1

    exhausted_wind = _send(
        reconnected,
        ids,
        message='I try to use Second Wind again.',
        client_message_id='runtime-combat-second-wind-exhausted',
        intent={
            'kind': 'capability',
            'source': 'combat_hud',
            'capability': {'id': 'second_wind', 'target_id': actor_id},
        },
    )
    exhausted_wind_error = _event_payload(exhausted_wind, 'error')
    assert exhausted_wind_error is not None
    assert exhausted_wind_error['error_code'] == 'gameplay_action_invalid'
    second_attack = _send(
        reconnected,
        ids,
        message='I press the now-wounded wolf.',
        client_message_id='runtime-combat-attack-round-2',
        intent={
            'kind': 'combat',
            'source': 'combat_hud',
            'combat': {'action_id': 'combat.attack.blade', 'target_id': enemy_id},
        },
    )
    assert _event_payload(second_attack, 'error') is None
    with app.app_context():
        state = _snapshot(ids)
        enemy = next(item for item in state['combat']['participants'] if item['id'] == enemy_id)
        assert enemy['hp']['current'] == 6
        assert state['combat']['status'] == 'active'
        assert state['combat']['flags']['turnEconomy']['actionRemaining'] == 0

    retreat_turn = _send(
        reconnected,
        ids,
        message='I end my turn and give the bloodied wolf a chance to flee.',
        client_message_id='runtime-combat-end-round-2',
        intent={
            'kind': 'combat',
            'source': 'combat_hud',
            'combat': {'action_id': 'combat.end_turn'},
        },
    )
    assert _event_payload(retreat_turn, 'error') is None
    final_scene = _event_payload(retreat_turn, 'scene_state')
    assert final_scene['in_combat'] is False
    recovery = _send(
        reconnected,
        ids,
        message='I complete a short rest after the wolf retreats.',
        client_message_id='runtime-combat-short-rest-after-retreat',
        intent={'kind': 'rest', 'source': 'scene_panel', 'rest_type': 'short_rest'},
    )
    assert _event_payload(recovery, 'error') is None
    reconnected.disconnect()

    with app.app_context():
        db.session.expire_all()
        state = _snapshot(ids)
        assert state['combat']['status'] == 'ended'
        assert state['combat']['flags']['endReason'] == 'enemies_fled'
        assert [entry['participantId'] for entry in state['combat']['initiative']] == [actor_id, enemy_id]
        assert state['combat']['flags']['activeActorId'] == actor_id
        assert state['combat']['round'] == 2
        enemy = next(item for item in state['combat']['participants'] if item['id'] == enemy_id)
        assert enemy['hp']['current'] == 6
        assert 'fled' in enemy['conditions']
        assert state['playerCharacters'][0]['classFeatureState']['second_wind']['current'] == 1
        assert state['playerCharacters'][0]['health']['currentHp'] == 20
        retreat_record = DmTurn.query.filter_by(client_message_id='runtime-combat-end-round-2').one()
        metadata = safe_json_loads(retreat_record.metadata_json, {})
        combat_context = metadata[STATE_PIPELINE_METADATA_KEY]['dmContextPacket']['combatState']
        resolved_enemy = combat_context['enemyResolvedActions'][0]
        assert resolved_enemy['enemyId'] == enemy_id
        assert resolved_enemy['intentType'] == 'retreat'
        assert resolved_enemy['resolvedWithoutRoll'] is True
        assert DmTurn.query.filter_by(session_id=ids['session_id']).count() == 7
