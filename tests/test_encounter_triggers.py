from __future__ import annotations

from copy import deepcopy

import pytest

import aidm_server.game_state.orchestration.turn_pipeline as turn_pipeline_module
from aidm_server.combat.pipeline import prepare_combat_for_turn
from aidm_server.database import db
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.campaign_pack_encounters import (
    TRIGGERED_ENCOUNTERS_FLAG,
    due_encounter,
    materialize_campaign_pack_combat_start,
)
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.models import Campaign, DmTurn, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest
from tests.helpers import seed_world_campaign_player_session

BANDIT = {'id': 'bandit', 'name': 'Bandit', 'source': 'campaign_pack', 'stats': {'maxHp': 20, 'armorClass': 12}}
CHEST = {'id': 'toll_chest', 'name': 'Toll Chest', 'kind': 'container', 'revision': 0}


def _actor(index: int) -> dict:
    return {
        'id': f'player_{index}',
        'playerId': index,
        'name': f'Hero {index}',
        'level': 1,
        'stats': {'dexterity': 12},
        'health': {'currentHp': 12, 'maxHp': 12, 'tempHp': 0, 'conditions': []},
        'inventory': {'items': [], 'currency': {}},
    }


def _encounter(encounter_id: str, trigger: dict, **fields) -> dict:
    return {
        'id': encounter_id,
        'title': encounter_id.replace('_', ' ').title(),
        'enemyGroups': [{'enemyId': 'bandit', 'count': 2}],
        'trigger': trigger,
        **fields,
    }


def _state(encounters: list[dict], *, players: int = 1, flags: dict | None = None, checkpoint: str = 'cp_road') -> dict:
    return {
        'sessionId': 5,
        'playerCharacters': [_actor(index) for index in range(1, players + 1)],
        'currentScene': {
            'locationId': 'camp',
            'name': 'Camp',
            'items': [],
            'interactables': [deepcopy(CHEST)],
            'hazards': [],
        },
        'locations': [
            {'id': 'camp', 'name': 'Camp', 'status': 'visited', 'connectedLocationIds': ['bridge'],
             'sceneState': {'interactables': [deepcopy(CHEST)], 'hazards': []}},
            {'id': 'bridge', 'name': 'Bridge', 'status': 'discovered', 'connectedLocationIds': ['camp']},
        ],
        'flags': dict(flags or {}),
        'combat': {'status': 'none', 'round': 1, 'participants': [], 'flags': {}},
        'campaignPack': {
            'packId': 'trigger_pack',
            'activeCheckpointId': checkpoint,
            'checkpoints': [{'id': 'cp_road', 'title': 'Road'}, {'id': 'cp_ambush', 'title': 'Ambush'}],
            'catalog': {
                'enemies': [deepcopy(BANDIT)],
                'encounters': encounters,
                'locations': [{'id': 'camp', 'name': 'Camp'}, {'id': 'bridge', 'name': 'Bridge'}],
            },
        },
        'stateChangeLedger': [],
    }


def _apply(state: dict, *changes: dict) -> dict:
    return apply_state_changes(state, list(changes))


def _move(location_id: str, change_id: str) -> dict:
    return {'id': change_id, 'turnId': 3, 'type': 'scene.move_location', 'locationId': location_id, 'name': location_id.title()}


def _end_combat(change_id: str) -> dict:
    return {'id': change_id, 'type': 'combat.end', 'endReason': 'all_enemies_defeated'}


def _enemies(state: dict) -> list[dict]:
    return [participant for participant in state['combat']['participants'] if participant['team'] == 'enemy']


def test_entering_a_location_starts_its_encounter_once():
    state = _state([_encounter('enc_bridge', {'type': 'on_enter', 'locationId': 'bridge'})])

    result = _apply(state, _move('bridge', 'move_1'))
    after = result['nextState']

    start = next(change for change in result['appliedChanges'] if change['type'] == 'combat.start')
    assert start['visible'] is True
    assert after['combat']['status'] == 'active'
    assert after['combat']['flags']['combatStartedBy'] == 'campaign_pack_trigger'
    assert after['combat']['flags']['campaignPackEncounterId'] == 'enc_bridge'
    assert after['combat']['initiative']
    assert after['flags'][TRIGGERED_ENCOUNTERS_FLAG] == ['enc_bridge']

    calm = _apply(after, _end_combat('end_1'), _move('camp', 'move_2'), _move('bridge', 'move_3'))['nextState']
    assert calm['combat']['status'] == 'none'
    assert calm['flags'][TRIGGERED_ENCOUNTERS_FLAG] == ['enc_bridge']


def test_an_encounter_that_is_not_once_fires_again():
    state = _state([_encounter('enc_bridge', {'type': 'on_enter', 'locationId': 'bridge'}, once=False)])

    first = _apply(state, _move('bridge', 'move_1'))['nextState']
    again = _apply(first, _end_combat('end_1'), _move('camp', 'move_2'), _move('bridge', 'move_3'))['nextState']

    assert again['combat']['status'] == 'active'


def test_unless_and_when_gate_a_trigger_on_flags():
    unless = _encounter(
        'enc_guards',
        {'type': 'on_enter', 'locationId': 'bridge'},
        unless={'flagKey': 'has_seal', 'equals': True},
    )
    assert _apply(_state([unless], flags={'has_seal': True}), _move('bridge', 'm'))['nextState']['combat']['status'] == 'none'
    assert _apply(_state([unless], flags={'has_seal': False}), _move('bridge', 'm'))['nextState']['combat']['status'] == 'active'

    when = _encounter('enc_night', {'type': 'on_enter', 'locationId': 'bridge'}, when={'flagKey': 'night', 'equals': True})
    assert _apply(_state([when]), _move('bridge', 'm'))['nextState']['combat']['status'] == 'none'
    assert _apply(_state([when], flags={'night': True}), _move('bridge', 'm'))['nextState']['combat']['status'] == 'active'


def test_setting_a_flag_to_the_trigger_value_starts_the_encounter():
    state = _state([_encounter('enc_alarm', {'type': 'on_flag', 'flagKey': 'alarm_raised', 'equals': True})])

    quiet = _apply(state, {'id': 'flag_1', 'type': 'flag.set', 'flagKey': 'alarm_raised', 'flagValue': False})['nextState']
    loud = _apply(quiet, {'id': 'flag_2', 'type': 'flag.set', 'flagKey': 'alarm_raised', 'flagValue': True})['nextState']

    assert quiet['combat']['status'] == 'none'
    assert loud['combat']['status'] == 'active'
    assert loud['combat']['flags']['campaignPackEncounterId'] == 'enc_alarm'


def test_a_successful_object_action_starts_its_encounter():
    state = _state([_encounter('enc_toll', {'type': 'on_interactable', 'targetId': 'toll_chest', 'action': 'open'})])

    inspected = _object_action(state, 'inspect', turn=2)
    assert inspected['combat']['status'] == 'none'
    opened = _object_action(inspected, 'open', turn=3)
    assert opened['combat']['status'] == 'active'
    assert opened['flags'][TRIGGERED_ENCOUNTERS_FLAG] == ['enc_toll']


def _object_action(state: dict, action: str, *, turn: int) -> dict:
    declared = validate_declared_actions(
        state=state,
        declared_actions=[
            {'id': f'act_{turn}', 'type': 'scene.interactable.action', 'actorId': 'player_1',
             'targetId': 'toll_chest', 'objectAction': action}
        ],
        current_turn=turn,
        expected_actor_id='player_1',
    )
    validation = validate_state_changes(state=state, changes=declared['immediateChanges'], expected_actor_id='player_1')
    return apply_state_changes(state, validated_changes_for_application(validation))['nextState']


def test_a_narrated_arrival_starts_the_encounter_in_the_same_turn():
    state = _state([_encounter('enc_bridge', {'type': 'on_enter', 'locationId': 'bridge'})])
    narrated = {**_move('bridge', 'post_move'), 'source': 'post_dm'}

    validation = validate_state_changes(state=state, changes=[narrated], expected_actor_id='player_1')
    result = apply_state_changes(state, validated_changes_for_application(validation))

    assert [change['type'] for change in result['appliedChanges']][:2] == ['scene.move_location', 'combat.start']
    assert result['nextState']['combat']['status'] == 'active'


def test_an_active_checkpoint_starts_its_encounter_before_narration(app):
    ids = seed_world_campaign_player_session(app)
    state = _state(
        [_encounter('enc_ambush', {'type': 'on_checkpoint', 'checkpointId': 'cp_ambush'})],
        checkpoint='cp_ambush',
    )
    state['playerCharacters'][0]['id'] = f"player_{ids['player_id']}"
    assert due_encounter(_state(state['campaignPack']['catalog']['encounters']), []) is None

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        turn = DmTurn(session_id=ids['session_id'], campaign_id=ids['campaign_id'], player_id=ids['player_id'],
                      player_input='We walk on.')
        db.session.add(turn)
        db.session.flush()
        result = prepare_combat_for_turn(
            state=state,
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            player_message=turn.player_input,
            workspace_id=campaign.workspace_id,
        )
        validation = validate_state_changes(state=state, changes=result['changes'])
        started = apply_state_changes(state, validated_changes_for_application(validation))['nextState']

        assert validation['rejected'] == []
        assert result['combatContext']
        assert started['combat']['flags']['combatStartedBy'] == 'campaign_pack_trigger'
        assert started['flags'][TRIGGERED_ENCOUNTERS_FLAG] == ['enc_ambush']

        ended = apply_state_changes(started, [_end_combat('end_ambush')])['nextState']
        again = prepare_combat_for_turn(
            state=ended,
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            player_message=turn.player_input,
            workspace_id=campaign.workspace_id,
        )
        assert again['changes'] == []


def _travel_pack() -> dict:
    return {
        'packId': 'travel_pack',
        'title': 'Travel Pack',
        'startingState': {'locationId': 'camp', 'knownLocationIds': ['camp', 'bridge']},
        'locations': [
            {'id': 'camp', 'name': 'Camp', 'connectedLocationIds': ['bridge']},
            {'id': 'bridge', 'name': 'Bridge', 'connectedLocationIds': ['camp']},
        ],
        'enemies': [{'id': 'bandit', 'name': 'Bandit', 'stats': {'maxHp': 20, 'armorClass': 12}}],
        'encounters': [_encounter('enc_bridge', {'type': 'on_enter', 'locationId': 'bridge'})],
    }


def test_travel_into_a_triggered_location_hands_narration_the_started_combat(client, app, monkeypatch):
    response = client.post('/api/campaigns/import-pack', json={'pack': _travel_pack()})
    assert response.status_code == 201, response.get_json()
    payload = response.get_json()
    monkeypatch.setattr(turn_pipeline_module, 'recent_timeline_for_session', lambda *_args, **_kwargs: [])

    with app.app_context():
        campaign = db.session.get(Campaign, payload['campaign_id'])
        session_obj = db.session.get(Session, payload['session_id'])
        player = Player(
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.campaign_id,
            name='Alice',
            character_name='Seraphina',
            class_='Fighter',
            level=1,
            stats=safe_json_dumps({'ability_scores': {'strength': 15, 'dexterity': 12, 'constitution': 14}}, {}),
        )
        db.session.add(player)
        db.session.flush()
        turn = DmTurn(session_id=session_obj.session_id, campaign_id=campaign.campaign_id, player_id=player.player_id,
                      player_input='We cross to the bridge.')
        db.session.add(turn)
        db.session.flush()

        result = turn_pipeline_module.pre_dm_pipeline(
            turn=turn,
            session_obj=session_obj,
            campaign=campaign,
            player=player,
            player_message=turn.player_input,
            action_intent={'kind': 'travel', 'text': turn.player_input, 'location': {'id': 'bridge', 'name': 'Bridge'}},
        )
        persisted = safe_json_loads(session_obj.state_snapshot, {})

    combat = result['stateBeforeDm']['combat']
    assert [change['type'] for change in result['immediateAppliedChanges']][:2] == ['scene.move_location', 'combat.start']
    assert combat['status'] == 'active'
    assert {participant['team'] for participant in combat['participants']} == {'player', 'enemy'}
    assert combat['initiative']
    dm_combat = result['dmContextPacket']['combatState']
    assert dm_combat and 'Bandit' in str(dm_combat)
    assert persisted['combat']['status'] == 'active'
    assert persisted['flags'][TRIGGERED_ENCOUNTERS_FLAG] == ['enc_bridge']


@pytest.mark.parametrize(
    ('players', 'counts', 'hp'),
    [
        (1, (1, 1), 14),
        (2, (2, 1), 16),
        (3, (3, 2), 18),
        (4, (4, 2), 20),
        (5, (4, 2), 20),
    ],
)
def test_encounters_scale_to_the_present_party(players, counts, hp):
    encounter = _encounter('enc_scaled', {'type': 'on_enter', 'locationId': 'bridge'})
    encounter['enemyGroups'] = [{'enemyId': 'bandit', 'count': 4}, {'enemyId': 'archer', 'count': 2}]
    state = _state([encounter], players=players)
    state['campaignPack']['catalog']['enemies'].append(
        {'id': 'archer', 'name': 'Archer', 'source': 'campaign_pack', 'stats': {'maxHp': 20, 'armorClass': 13}}
    )

    change = materialize_campaign_pack_combat_start(state, {'type': 'combat.start', 'campaignPackEncounterId': 'enc_scaled', 'combat': {}})

    enemies = [participant for participant in change['combat']['participants'] if participant['team'] == 'enemy']
    assert sum(enemy['campaignPackEnemyId'] == 'bandit' for enemy in enemies) == counts[0]
    assert sum(enemy['campaignPackEnemyId'] == 'archer' for enemy in enemies) == counts[1]
    assert {(enemy['hp']['current'], enemy['hp']['max']) for enemy in enemies} == {(hp, hp)}
    scaling = change['combat']['flags']['partyScaling']
    assert scaling['partySize'] == players
    assert scaling['applied'] is (players < 4)
    assert [group['count'] for group in scaling['groups']] == list(counts)


def test_scaling_false_keeps_authored_counts_and_hp():
    encounter = _encounter('enc_full', {'type': 'on_enter', 'locationId': 'bridge'}, scaling=False)
    state = _state([encounter], players=1)

    change = materialize_campaign_pack_combat_start(state, {'type': 'combat.start', 'campaignPackEncounterId': 'enc_full', 'combat': {}})

    enemies = [participant for participant in change['combat']['participants'] if participant['team'] == 'enemy']
    assert len(enemies) == 2
    assert {enemy['hp']['max'] for enemy in enemies} == {20}
    assert change['combat']['flags']['partyScaling']['applied'] is False


def test_linter_reports_unknown_ids_in_triggers():
    pack = _travel_pack()
    pack['checkpoints'] = [{'id': 'cp_one', 'title': 'One', 'locationIds': ['camp'], 'terminal': True}]
    pack['encounters'] = [
        _encounter('enc_a', {'type': 'on_enter', 'locationId': 'nowhere'}),
        _encounter('enc_b', {'type': 'on_checkpoint', 'checkpointId': 'cp_missing'}),
        _encounter('enc_c', {'type': 'on_interactable', 'targetId': 'ghost_door', 'action': 'open'}),
        _encounter('enc_d', {'type': 'on_whim'}),
        _encounter('enc_e', {'type': 'on_flag'}),
        _encounter('enc_ok', {'type': 'on_checkpoint', 'checkpointId': 'cp_one'}),
    ]

    errors = {
        (issue['code'], issue['path'])
        for issue in lint_campaign_pack_manifest(pack)['issues']
        if issue['severity'] == 'error'
    }

    assert errors - {('invalid_campaign_pack_schema', 'campaign pack')} == {
        ('unknown_reference', 'encounters[0].trigger.locationId'),
        ('unknown_reference', 'encounters[1].trigger.checkpointId'),
        ('unknown_reference', 'encounters[2].trigger.targetId'),
        ('invalid_encounter_trigger', 'encounters[3].trigger.type'),
        ('invalid_encounter_trigger', 'encounters[4].trigger.flagKey'),
    }
