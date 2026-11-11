from __future__ import annotations

import json
from copy import deepcopy

import pytest

from aidm_server.canon_jobs import _evaluate_state_segments_after_turn
from aidm_server.database import db
from aidm_server.game_state.quest_engine import rule_satisfied
from aidm_server.interactables import available_actions_for_target
from aidm_server.models import Campaign, CampaignSegment, DmTurn, Session, safe_json_dumps, safe_json_loads
from aidm_server.segment_triggers import evaluate_segment_trigger
from aidm_server.services.campaign_pack_progress import update_campaign_pack_progress
from aidm_server.services.campaign_pack_snapshot import migrate_campaign_pack_snapshot
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player


def _nested_all_of(leaf: dict, wrappers: int) -> dict:
    predicate = leaf
    for _ in range(wrappers):
        predicate = {'allOf': [predicate]}
    return predicate


# The leaf sits eight levels below completeWhen: four allOf lists and four dicts.
DEPTH_8_PREDICATE = _nested_all_of({'eventType': 'interactable.opened', 'targetId': 'gate_door'}, 4)

LOCKED_DOOR = {
    'id': 'gate_door',
    'name': 'Iron Gate',
    'kind': 'door',
    'locked': True,
    'open': False,
    'requirements': {'unlock': {'anyItemIds': ['gate_key']}},
}


def _pack(**overrides) -> dict:
    pack = {
        'packId': 'fidelity_pack',
        'title': 'Fidelity Pack',
        'startingState': {
            'locationId': 'gatehouse',
            'questId': 'quest_gate',
            'currentScene': {
                'title': 'Rain at the Gate',
                'summary': 'Rain hammers the gatehouse.',
                'description': 'The gatehouse is dark and wet.',
                'visibleDetails': ['A lantern swings on its hook.', 'Fresh mud on the threshold.'],
                'hiddenSceneNotes': 'SECRET_SCENE_NOTE',
            },
            'campaignFlags': {'gate_warned': False, 'shared': 'alias'},
            'flags': {'shared': 'explicit'},
            'partyStatus': {'knownThreat': 'SECRET_PARTY_STATUS'},
            'partyNotes': ['SECRET_PARTY_NOTE'],
        },
        'locations': [
            {
                'id': 'gatehouse',
                'name': 'Gatehouse',
                'connectedLocationIds': ['yard'],
                'gmNotes': 'SECRET_LOCATION_GM_NOTES',
                'sceneState': {'interactables': [deepcopy(LOCKED_DOOR)]},
                'hazards': [{'id': 'loose_flagstone', 'name': 'Loose Flagstone', 'kind': 'hazard', 'active': True}],
                'items': [{'id': 'rusty_lamp', 'name': 'Rusty Lamp', 'quantity': 1}],
            },
            {
                'id': 'yard',
                'name': 'Yard',
                'connectedLocationIds': ['gatehouse'],
                'interactables': [
                    {
                        'id': 'yard_chest',
                        'name': 'Yard Chest',
                        'kind': 'container',
                        'locked': True,
                        'requirements': {'open': {'anyItemIds': ['chest_key']}, '*': {'flags': {'yard_safe': True}}},
                        'transitions': {'open': [{'targetId': 'yard_door', 'set': {'open': True}}]},
                    },
                    {'id': 'yard_door', 'name': 'Yard Door', 'kind': 'door'},
                ],
            },
        ],
        'npcs': [
            {
                'id': 'npc_warden',
                'name': 'Warden Ilse',
                'locationId': 'gatehouse',
                'description': 'A tired gate warden.',
                'personality': 'SECRET_PERSONALITY',
                'motivation': 'SECRET_MOTIVATION',
                'knows': 'SECRET_KNOWS',
                'secret': 'SECRET_SECRET',
                'useInPlay': 'SECRET_USE_IN_PLAY',
                'ifSkipped': 'SECRET_IF_SKIPPED',
                'gmNotes': 'SECRET_NPC_GM_NOTES',
                'trueName': 'SECRET_TRUE_NAME',
                'visibleAtStart': True,
            }
        ],
        'quests': [
            {
                'id': 'quest_gate',
                'title': 'Open the Gate',
                'status': 'active',
                'objectives': [
                    {'id': 'obj_open_gate', 'description': 'Open the iron gate.', 'completeWhen': DEPTH_8_PREDICATE}
                ],
            }
        ],
        'enemies': [{'id': 'enemy_raider', 'name': 'Raider'}],
        'encounters': [
            {
                'id': 'enc_raiders',
                'title': 'Raiders at the Gate',
                'enemyIds': ['enemy_raider'],
                'setup': 'SECRET_SETUP',
                'stakes': 'SECRET_STAKES',
                'gmNotes': 'SECRET_ENCOUNTER_GM_NOTES',
                'difficulty': 'hard',
                'tags': ['ambush'],
                'outcomes': {
                    'victory': {'rewards': {'items': [{'id': 'raider_badge', 'name': 'Raider Badge', 'quantity': 1}]}}
                },
            }
        ],
        'checkpoints': [
            {
                'id': 'cp_gate',
                'title': 'The Gate',
                'gmNotes': 'SECRET_CHECKPOINT_GM_NOTES',
                'locationIds': ['gatehouse'],
                'terminal': True,
            }
        ],
        'segments': [
            {
                'id': 'seg_yard',
                'title': 'Into the Yard',
                'summary': 'The party crosses the yard.',
                'description': 'Rain sheets across the yard.',
                'playerSummary': 'Cross the yard.',
                'tone': 'tense',
                'gmNotes': 'SECRET_SEGMENT_GM_NOTES',
                'locationId': 'yard',
                'questIds': ['quest_gate'],
                'tags': ['yard'],
            },
            {
                'id': 'seg_bell',
                'title': 'The Bell',
                'trigger': {'type': 'keyword', 'keywords': ['bell', 'ring']},
            },
        ],
        'lore': [{'id': 'lore_gate', 'title': 'Gate Oath', 'summary': 'The gate was sworn shut.'}],
    }
    pack.update(overrides)
    return pack


def _import(client, pack: dict) -> dict:
    response = client.post('/api/campaigns/import-pack', json={'pack': pack})
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _snapshot(app, session_id: int) -> dict:
    with app.app_context():
        return safe_json_loads(db.session.get(Session, session_id).state_snapshot, {})


def test_nested_predicates_rewards_and_interactable_requirements_survive_import(client, app):
    payload = _import(client, _pack())
    snapshot = _snapshot(app, payload['session_id'])
    catalog = snapshot['campaignPack']['catalog']

    objective = catalog['quests'][0]['objectives'][0]
    assert objective['completeWhen'] == DEPTH_8_PREDICATE
    assert rule_satisfied(objective['completeWhen'], snapshot, {'type': 'flag.set', 'flagKey': 'weather'}) is False
    assert rule_satisfied(
        objective['completeWhen'],
        snapshot,
        {'type': 'interactable.opened', 'targetId': 'gate_door'},
    ) is True

    assert catalog['encounters'][0]['outcomes']['victory']['rewards']['items'] == [
        {'id': 'raider_badge', 'name': 'Raider Badge', 'quantity': 1}
    ]
    yard_chest = catalog['locations'][1]['interactables'][0]
    assert yard_chest['requirements'] == {
        'open': {'anyItemIds': ['chest_key']},
        '*': {'flags': {'yard_safe': True}},
    }
    assert yard_chest['transitions'] == {'open': [{'targetId': 'yard_door', 'set': {'open': True}}]}
    assert catalog['locations'][0]['sceneState']['interactables'] == [LOCKED_DOOR]


def test_locked_starting_door_needs_its_key(client, app):
    payload = _import(client, _pack())
    snapshot = _snapshot(app, payload['session_id'])

    def unlock_legal(actor: dict) -> bool:
        result = available_actions_for_target(snapshot, location_id='gatehouse', target_id='gate_door', actor=actor)
        assert result['ok'] is True
        return next(entry['legal'] for entry in result['actions'] if entry['action'] == 'unlock')

    assert unlock_legal({'id': 'player_1'}) is False
    assert unlock_legal({'id': 'player_1', 'itemIds': ['gate_key']}) is True


@pytest.mark.parametrize(
    ('mutate', 'expected_path', 'expected_message'),
    [
        (
            lambda pack: pack['quests'][0]['objectives'][0].update(
                completeWhen=_nested_all_of({'eventType': 'interactable.opened'}, 5)
            ),
            'quests[0].objectives[0].completeWhen.allOf[0].allOf[0].allOf[0].allOf[0].allOf',
            'is nested more than 12 levels deep',
        ),
        (
            lambda pack: pack['npcs'][0].update(knows='x' * 4_001),
            'npcs[0].knows',
            'is longer than 4000 characters',
        ),
        (
            lambda pack: pack['npcs'][0].update(aliases=[f'alias {index}' for index in range(151)]),
            'npcs[0].aliases',
            'has more than 150 items',
        ),
        (
            lambda pack: pack['startingState']['flags'].update({f'flag_{index}': True for index in range(80)}),
            'startingState.flags',
            'has more than 80 keys',
        ),
    ],
)
def test_out_of_bounds_content_is_rejected_with_its_json_path(client, app, mutate, expected_path, expected_message):
    pack = _pack()
    mutate(pack)

    response = client.post('/api/campaigns/import-pack', json={'pack': pack})

    assert response.status_code == 400
    body = response.get_json()
    assert body['error_code'] == 'pack_content_out_of_bounds'
    assert body['error'] == f'{expected_path} {expected_message}.'
    with app.app_context():
        assert Campaign.query.count() == 0


def test_opening_scene_keeps_authored_content_and_starting_brief(client, app):
    pack = _pack()
    pack['startingState']['currentScene']['interactables'] = [
        {'id': 'scene_crate', 'name': 'Crate', 'kind': 'container', 'searchable': True}
    ]
    payload = _import(client, pack)
    snapshot = _snapshot(app, payload['session_id'])
    scene = snapshot['currentScene']

    assert scene['summary'] == 'Rain hammers the gatehouse.'
    assert scene['visibleDetails'] == ['A lantern swings on its hook.', 'Fresh mud on the threshold.']
    assert scene['hiddenSceneNotes'] == 'SECRET_SCENE_NOTE'
    assert scene['interactables'] == [{'id': 'scene_crate', 'name': 'Crate', 'kind': 'container', 'searchable': True}]
    assert scene['hazards'] == [{'id': 'loose_flagstone', 'name': 'Loose Flagstone', 'kind': 'hazard', 'active': True}]
    assert scene['items'] == [{'id': 'rusty_lamp', 'name': 'Rusty Lamp', 'quantity': 1}]
    assert snapshot['flags']['gate_warned'] is False
    assert snapshot['flags']['shared'] == 'explicit'
    assert snapshot['campaignPack']['startingBrief'] == {
        'partyStatus': {'knownThreat': 'SECRET_PARTY_STATUS'},
        'partyNotes': ['SECRET_PARTY_NOTE'],
    }


def test_opening_scene_without_objects_uses_the_starting_location_objects(client, app):
    payload = _import(client, _pack())
    snapshot = _snapshot(app, payload['session_id'])

    assert snapshot['currentScene']['interactables'] == [LOCKED_DOOR]
    assert snapshot['currentScene']['hazards'][0]['id'] == 'loose_flagstone'
    player_scene = filter_session_snapshot_for_player(snapshot)['currentScene']
    assert [entry['id'] for entry in player_scene['interactables']] == ['gate_door']


def test_player_projection_shows_scene_summary_and_details(client, app):
    payload = _import(client, _pack())
    player_scene = filter_session_snapshot_for_player(_snapshot(app, payload['session_id']))['currentScene']

    assert player_scene['summary'] == 'Rain hammers the gatehouse.'
    assert player_scene['visibleDetails'] == ['A lantern swings on its hook.', 'Fresh mud on the threshold.']
    assert 'hiddenSceneNotes' not in player_scene


@pytest.mark.parametrize(
    ('catalog_path', 'sentinel'),
    [
        (('npcs', 0, 'personality'), 'SECRET_PERSONALITY'),
        (('npcs', 0, 'motivation'), 'SECRET_MOTIVATION'),
        (('npcs', 0, 'knows'), 'SECRET_KNOWS'),
        (('npcs', 0, 'secret'), 'SECRET_SECRET'),
        (('npcs', 0, 'useInPlay'), 'SECRET_USE_IN_PLAY'),
        (('npcs', 0, 'ifSkipped'), 'SECRET_IF_SKIPPED'),
        (('npcs', 0, 'gmNotes'), 'SECRET_NPC_GM_NOTES'),
        (('npcs', 0, 'trueName'), 'SECRET_TRUE_NAME'),
        (('encounters', 0, 'setup'), 'SECRET_SETUP'),
        (('encounters', 0, 'stakes'), 'SECRET_STAKES'),
        (('encounters', 0, 'gmNotes'), 'SECRET_ENCOUNTER_GM_NOTES'),
        (('locations', 0, 'gmNotes'), 'SECRET_LOCATION_GM_NOTES'),
        (('segments', 0, 'gmNotes'), 'SECRET_SEGMENT_GM_NOTES'),
    ],
)
def test_author_fields_survive_import_but_never_reach_players(client, app, catalog_path, sentinel):
    payload = _import(client, _pack())
    snapshot = _snapshot(app, payload['session_id'])
    collection, index, field = catalog_path

    assert snapshot['campaignPack']['catalog'][collection][index][field] == sentinel
    assert sentinel not in json.dumps(filter_session_snapshot_for_player(snapshot))


@pytest.mark.parametrize(
    'sentinel',
    ['SECRET_CHECKPOINT_GM_NOTES', 'SECRET_SCENE_NOTE', 'SECRET_PARTY_STATUS', 'SECRET_PARTY_NOTE'],
)
def test_gm_only_starting_content_never_reaches_players(client, app, sentinel):
    payload = _import(client, _pack())
    snapshot = _snapshot(app, payload['session_id'])

    assert sentinel in json.dumps(snapshot)
    projected = filter_session_snapshot_for_player(snapshot)
    assert sentinel not in json.dumps(projected)
    assert 'startingBrief' not in projected['campaignPack']


def test_npc_encounter_and_checkpoint_author_fields_survive_import(client, app):
    payload = _import(client, _pack())
    pack = _snapshot(app, payload['session_id'])['campaignPack']

    assert pack['catalog']['npcs'][0]['description'] == 'A tired gate warden.'
    assert pack['catalog']['encounters'][0]['difficulty'] == 'hard'
    assert pack['catalog']['encounters'][0]['tags'] == ['ambush']
    assert pack['checkpoints'][0]['gmNotes'] == 'SECRET_CHECKPOINT_GM_NOTES'


def test_lore_is_cataloged_under_lore_on_import_and_migration(client, app):
    payload = _import(client, _pack())
    catalog = _snapshot(app, payload['session_id'])['campaignPack']['catalog']

    assert [record['id'] for record in catalog['lore']] == ['lore_gate']
    assert 'lores' not in catalog

    legacy = {'campaignPack': {'packId': 'old', 'catalog': {'lores': [{'id': 'lore_old'}]}}}
    migrated, applied = migrate_campaign_pack_snapshot(legacy)
    assert migrated['campaignPack']['catalog']['lore'] == [{'id': 'lore_old'}]
    assert 'lores' not in migrated['campaignPack']['catalog']
    assert 'campaign_pack.catalog_lore' in applied
    assert 'lores' in legacy['campaignPack']['catalog']


def test_segments_keep_authored_fields_and_get_location_triggers(client, app):
    payload = _import(client, _pack())
    snapshot = _snapshot(app, payload['session_id'])

    assert snapshot['campaignPack']['catalog']['segments'][0] == {
        'id': 'seg_yard',
        'title': 'Into the Yard',
        'summary': 'The party crosses the yard.',
        'description': 'Rain sheets across the yard.',
        'playerSummary': 'Cross the yard.',
        'tone': 'tense',
        'gmNotes': 'SECRET_SEGMENT_GM_NOTES',
        'locationId': 'yard',
        'questIds': ['quest_gate'],
        'tags': ['yard'],
    }
    with app.app_context():
        segments = {
            segment.external_id: segment
            for segment in CampaignSegment.query.filter_by(campaign_id=payload['campaign_id']).all()
        }
    yard_trigger = json.loads(segments['seg_yard'].trigger_condition)
    assert yard_trigger['type'] == 'location'
    assert yard_trigger['locationId'] == 'yard'
    assert segments['seg_yard'].description == 'Rain sheets across the yard.'

    at_yard, _reason, _spec = evaluate_segment_trigger(
        segments['seg_yard'].trigger_condition, 'I look around.', {'current_location_id': 'yard'}, {}
    )
    at_gate, _reason, _spec = evaluate_segment_trigger(
        segments['seg_yard'].trigger_condition, 'I look around.', {'current_location_id': 'gatehouse'}, {}
    )
    assert (at_yard, at_gate) == (True, False)

    rang, reason, spec = evaluate_segment_trigger(segments['seg_bell'].trigger_condition, 'I ring the bell.', {}, {})
    assert rang is True
    assert spec['trigger_type'] == 'keywords'
    assert reason == 'keywords:bell,ring'


def test_segment_with_location_auto_triggers_when_the_party_arrives(client, app):
    payload = _import(client, _pack())

    with app.app_context():
        session = db.session.get(Session, payload['session_id'])
        campaign = db.session.get(Campaign, payload['campaign_id'])
        turn = DmTurn(
            session_id=session.session_id,
            campaign_id=campaign.campaign_id,
            player_input='I wait at the gate.',
            dm_output='Rain falls.',
            status='completed',
        )
        db.session.add(turn)
        db.session.commit()

        assert _evaluate_state_segments_after_turn(turn, campaign) == []

        db.session.expire_all()
        session = db.session.get(Session, payload['session_id'])
        snapshot = safe_json_loads(session.state_snapshot, {})
        snapshot['currentScene']['locationId'] = 'yard'
        snapshot['currentScene']['name'] = 'Yard'
        session.state_snapshot = safe_json_dumps(snapshot, {})
        db.session.commit()

        triggered = _evaluate_state_segments_after_turn(turn, campaign)
        yard_segment = CampaignSegment.query.filter_by(campaign_id=campaign.campaign_id, external_id='seg_yard').one()

    assert [segment['title'] for segment in triggered] == ['Into the Yard']
    assert yard_segment.is_triggered is True


def _checkpoint_pack(complete_when: dict) -> dict:
    return _pack(
        checkpoints=[
            {'id': 'cp_search', 'title': 'Search the gatehouse', 'completeWhen': complete_when, 'nextCheckpointIds': ['cp_end']},
            {'id': 'cp_end', 'title': 'Beyond the gate', 'terminal': True},
        ],
        clues=[
            {'id': 'clue_mud', 'title': 'Fresh Mud'},
            {'id': 'clue_lantern', 'title': 'Swinging Lantern'},
        ],
    )


def _progress_after(app, payload: dict, change) -> object:
    with app.app_context():
        session = db.session.get(Session, payload['session_id'])
        snapshot = safe_json_loads(session.state_snapshot, {})
        change(snapshot)
        session.state_snapshot = safe_json_dumps(snapshot, {})
        db.session.commit()
        return update_campaign_pack_progress(session_id=payload['session_id'], campaign_id=payload['campaign_id'])


def test_checkpoint_completes_when_all_listed_clues_are_discovered(client, app):
    payload = _import(client, _checkpoint_pack({'clueIds': ['clue_mud', 'clue_lantern']}))

    one_clue = _progress_after(
        app, payload, lambda snapshot: snapshot.update(clues=[{'id': 'clue_mud', 'status': 'discovered'}])
    )
    assert one_clue.changed is False

    both = _progress_after(
        app,
        payload,
        lambda snapshot: snapshot['clues'].append({'id': 'clue_lantern', 'status': 'discovered'}),
    )
    assert both.changed is True
    assert both.reason == 'checkpoint_clues_discovered'
    assert both.active_checkpoint_id == 'cp_end'


def test_checkpoint_completes_when_all_flags_match(client, app):
    payload = _import(client, _checkpoint_pack({'flags': {'gate_warned': True, 'alarm_level': 2}}))

    wrong_type = _progress_after(
        app, payload, lambda snapshot: snapshot['flags'].update(gate_warned='true', alarm_level=2)
    )
    assert wrong_type.changed is False

    matched = _progress_after(app, payload, lambda snapshot: snapshot['flags'].update(gate_warned=True))
    assert matched.changed is True
    assert matched.reason == 'checkpoint_flags_matched'
    assert matched.active_checkpoint_id == 'cp_end'


def test_the_imported_scene_is_authoritative_from_turn_zero(client, app):
    payload = _import(client, _pack())
    with app.app_context():
        snapshot = safe_json_loads(db.session.get(Session, payload['session_id']).state_snapshot, {})

    assert (snapshot['currentScene']['locationId'], snapshot['currentScene']['updatedAtTurn']) == ('gatehouse', 0)


def test_action_wording_and_enemy_names_are_carried_into_state_but_dm_text_never_reaches_players(client, app):
    door = {
        **LOCKED_DOOR,
        'description': 'An iron gate, its lock stamped with a crown.',
        'actionLabels': {'unlock': 'Unlock the iron gate'},
        'actionText': {'unlock': 'SECRET_ACTION_TEXT the lock turns with a cough of rust.'},
    }
    pack = _pack(
        enemies=[
            {'id': 'enemy_raider', 'name': 'Raiders', 'singularName': 'Raider'},
            {'id': 'enemy_marshal', 'name': 'The Hollow Marshal', 'proper': True},
        ]
    )
    pack['locations'][0]['sceneState']['interactables'] = [door]

    payload = _import(client, pack)
    snapshot = _snapshot(app, payload['session_id'])

    scene_door = snapshot['currentScene']['interactables'][0]
    assert {key: scene_door[key] for key in ('description', 'actionLabels', 'actionText')} == {
        key: door[key] for key in ('description', 'actionLabels', 'actionText')
    }
    assert [
        (enemy['id'], enemy.get('singularName'), enemy.get('proper'))
        for enemy in snapshot['campaignPack']['catalog']['enemies']
    ] == [('enemy_raider', 'Raider', None), ('enemy_marshal', None, True)]
    projected = filter_session_snapshot_for_player(snapshot)
    assert 'SECRET_ACTION_TEXT' not in json.dumps(projected)
    assert projected['currentScene']['interactables'][0]['offeredActions'][1] == {
        'action': 'unlock',
        'label': 'Unlock the iron gate',
        'authored': True,
        'legal': False,
        'done': False,
        'reason': 'It is locked, and you carry no key that fits.',
    }


def test_an_action_label_longer_than_48_characters_is_rejected_on_import(client, app):
    pack = _pack()
    pack['locations'][0]['sceneState']['interactables'] = [{**LOCKED_DOOR, 'actionLabels': {'unlock': 'x' * 49}}]

    response = client.post('/api/campaigns/import-pack', json={'pack': pack})

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'invalid_campaign_pack_schema'
    assert 'actionLabels.unlock' in response.get_json()['error']
