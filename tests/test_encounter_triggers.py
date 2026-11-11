from __future__ import annotations

from copy import deepcopy

import pytest

import aidm_server.game_state.orchestration.turn_pipeline as turn_pipeline_module
from aidm_server.combat.attacks import resolve_enemy_ability
from aidm_server.combat.defeat import party_defeat_recovery
from aidm_server.combat.end_conditions import combat_end_change
from aidm_server.combat.pipeline import prepare_combat_for_turn
from aidm_server.combat.state import instantiate_creature, player_combat_participant
from aidm_server.creatures.core_bestiary import core_creature
from aidm_server.database import db
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.campaign_pack_encounters import (
    TRIGGERED_ENCOUNTERS_FLAG,
    due_encounter,
    materialize_campaign_pack_combat_start,
    party_scaling,
)
from aidm_server.game_state.orchestration.combat_resolution import settle_engine_controlled_turns
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.models import (
    Campaign,
    CombatDebugEvent,
    DmTurn,
    Player,
    Session,
    SessionStateMutationAudit,
    safe_json_dumps,
    safe_json_loads,
)
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
    ('players', 'counts', 'hp', 'numbers', 'dropped'),
    [
        (1, (1, 0), 6, (0.3, 0.4, -3, 1), ['archer']),
        (2, (2, 1), 12, (0.6, 0.7, -1, 3), []),
        (3, (3, 0), 16, (0.8, 0.85, 0, 4), ['archer']),
        (4, (4, 2), 20, (1.0, 1.0, 0, None), []),
        (5, (4, 2), 20, (1.0, 1.0, 0, None), []),
    ],
)
def test_encounters_scale_to_the_present_party(players, counts, hp, numbers, dropped):
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
    assert (
        scaling['hpMultiplier'],
        scaling['damageMultiplier'],
        scaling['attackAndDcAdjustment'],
        scaling['maxEnemies'],
    ) == numbers
    assert [group['enemyId'] for group in scaling['droppedGroups']] == dropped
    assert [group['count'] for group in scaling['groups']] == [count for count in counts if count]


def test_a_lone_hero_faces_one_enemy_and_the_first_group_is_never_dropped():
    encounter = _encounter('enc_horde', {'type': 'on_enter', 'locationId': 'bridge'})
    encounter['enemyGroups'] = [{'enemyId': 'bandit', 'count': 12}, {'enemyId': 'archer', 'count': 2}]
    state = _state([encounter], players=1)

    scaling = party_scaling(encounter, party_size=1)
    change = materialize_campaign_pack_combat_start(state, {'type': 'combat.start', 'campaignPackEncounterId': 'enc_horde', 'combat': {}})

    assert scaling['groups'] == [{'enemyId': 'bandit', 'authoredCount': 12, 'count': 1}]
    assert scaling['droppedGroups'] == [{'enemyId': 'archer', 'authoredCount': 2, 'count': 1}]
    assert [enemy['name'] for enemy in change['combat']['participants'] if enemy['team'] == 'enemy'] == ['Bandit']


def test_party_scaling_lowers_enemy_attack_bonus_save_dc_and_every_damage_roll():
    encounter = _encounter('enc_hard', {'type': 'on_enter', 'locationId': 'bridge'})
    encounter['enemyGroups'] = [{'enemyId': 'bandit', 'count': 1}]
    state = _state([encounter], players=1)
    state['campaignPack']['catalog']['enemies'][0]['abilities'] = [
        {'id': 'cut', 'name': 'Cut', 'type': 'attack', 'attackBonus': 6, 'damage': {'dice': '1d4+9', 'type': 'slashing'}},
        {
            'id': 'hex',
            'name': 'Hex',
            'type': 'spell',
            'save': {'ability': 'wisdom', 'dc': 14},
            'damage': {'dice': '1d4', 'type': 'psychic'},
        },
    ]

    change = materialize_campaign_pack_combat_start(state, {'type': 'combat.start', 'campaignPackEncounterId': 'enc_hard', 'combat': {}})
    combat = change['combat']
    enemy = next(participant for participant in combat['participants'] if participant['team'] == 'enemy')
    hero = next(participant for participant in combat['participants'] if participant['team'] == 'player')
    cut, hex_ability = enemy['abilities']

    assert (enemy['name'], cut['attackBonus'], hex_ability['save']['dc']) == ('Bandit', 3, 11)
    hit = resolve_enemy_ability(
        enemy={**enemy, 'abilities': [cut]},
        target={**hero, 'armorClass': 1},
        ability=cut,
        combat=combat,
        state=state,
        roller=lambda sides: 20 if sides == 20 else 1,
        event_id='cut',
        turn_id=1,
    )
    save = resolve_enemy_ability(
        enemy=enemy, target=hero, ability=hex_ability, combat=combat, state=state, roller=lambda _sides: 1, event_id='hex', turn_id=1,
    )
    # A natural 20 doubles the die: 2d4+9 rolls 11; times 0.4 rounded down is 4. A psychic 1 stays at least 1.
    assert hit['damageTotal'] == 4
    assert save['damageTotal'] == 1


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


@pytest.fixture()
def play_now_table(app, client, socketio, monkeypatch):
    from tests.test_flagship_adventure import Table

    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr('requests.Session.request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    response = client.post('/api/onboarding/play-now', json={})
    assert response.status_code == 201, response.get_data(as_text=True)
    ids = {key: response.get_json()[key] for key in ('session_id', 'campaign_id', 'player_id', 'workspace_id')}
    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit(
        'join_session',
        {'session_id': ids['session_id'], 'player_id': ids['player_id'], 'workspace_id': ids['workspace_id']},
    )
    socket_client.get_received()
    yield Table(app, client, socket_client, ids)
    socket_client.disconnect()


def test_a_self_started_fight_runs_the_enemies_opening_turns_up_to_the_hero(play_now_table, monkeypatch):
    table = play_now_table
    # The enemies win initiative; every enemy attack roll is a 1 and misses.
    monkeypatch.setattr(
        'aidm_server.combat.state._deterministic_initiative_roll',
        lambda _seed, actor_id: 1 if actor_id.startswith('player_') else 20,
    )
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda _sides: 1)
    hero_id = f"player_{table.ids['player_id']}"

    road = table.travel('loc_old_north_road', 'The Old North Road')
    combat = road.snapshot['combat']

    assert road.encounter == 'enc_pale_lantern_detour'
    assert combat['status'] == 'active'
    assert combat['initiative'][-1]['participantId'] == hero_id
    assert (combat['flags']['activeActorId'], combat['round']) == (hero_id, 1)
    with table.app.app_context():
        turn = DmTurn.query.filter_by(session_id=table.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
        resolved = safe_json_loads(turn.metadata_json, {})['state_pipeline']['dmContextPacket']['combatState']['enemyResolvedActions']
    enemy_ids = [participant['id'] for participant in combat['participants'] if participant['team'] == 'enemy']
    assert sorted(action['enemyId'] for action in resolved) == sorted(enemy_ids)
    assert table._hud_action('combat.end_turn') is not None


def test_a_fight_narration_sets_off_still_ends_the_turn_with_the_hero(client, app, monkeypatch):
    response = client.post('/api/campaigns/import-pack', json={'pack': _travel_pack()})
    assert response.status_code == 201, response.get_json()
    payload = response.get_json()
    monkeypatch.setattr(turn_pipeline_module, 'recent_timeline_for_session', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        'aidm_server.combat.state._deterministic_initiative_roll',
        lambda _seed, actor_id: 1 if actor_id.startswith('player_') else 20,
    )
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda _sides: 1)

    def narrated_arrival(**kwargs):
        return {
            'proposedChanges': [
                {'id': 'narrated_move', 'turnId': kwargs['turn_id'], 'type': 'scene.move_location', 'source': 'post_dm',
                 'actorId': kwargs['actor_id'], 'locationId': 'bridge', 'name': 'Bridge'},
            ],
            'uncertainChanges': [],
            'notes': [],
            'debug': {},
        }

    monkeypatch.setattr(turn_pipeline_module, 'extract_post_dm_outcomes', narrated_arrival)

    with app.app_context():
        campaign = db.session.get(Campaign, payload['campaign_id'])
        session_obj = db.session.get(Session, payload['session_id'])
        player = Player(workspace_id=campaign.workspace_id, campaign_id=campaign.campaign_id, name='Alice',
                        character_name='Seraphina', class_='Fighter', level=1,
                        stats=safe_json_dumps({'ability_scores': {'strength': 15, 'dexterity': 12, 'constitution': 14}}, {}))
        db.session.add(player)
        db.session.flush()
        turn = DmTurn(session_id=session_obj.session_id, campaign_id=campaign.campaign_id, player_id=player.player_id,
                      player_input='We follow the river.', dm_output='The path bends and the bridge looms ahead.')
        db.session.add(turn)
        db.session.flush()
        turn_pipeline_module.pre_dm_pipeline(
            turn=turn, session_obj=session_obj, campaign=campaign, player=player, player_message=turn.player_input,
        )
        result = turn_pipeline_module.post_dm_pipeline(
            turn=turn, session_obj=session_obj, campaign=campaign, player=player, dm_response_text=turn.dm_output,
        )
        persisted = safe_json_loads(session_obj.state_snapshot, {})

    applied_types = [change['type'] for change in result['postAppliedChanges']]
    combat = persisted['combat']
    assert applied_types[:2] == ['scene.move_location', 'combat.start']
    assert 'combat.ability.mark_used' in applied_types or 'combat.move' in applied_types
    assert (combat['status'], combat['flags']['activeActorId'], combat['round']) == ('active', f'player_{player.player_id}', 1)


def _enemies_go_first(monkeypatch, roll=lambda _sides: 1) -> None:
    """Every non-player wins initiative; every engine roll is ``roll(sides)`` (a 1 misses)."""

    monkeypatch.setattr(
        'aidm_server.combat.state._deterministic_initiative_roll',
        lambda _seed, actor_id: 1 if actor_id.startswith('player_') else 20,
    )
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', roll)


def _engine_results(changes: list[dict]) -> list[dict]:
    return [change for change in changes if change.get('source') == 'enemy_resolved_action']


def _camp_pack(*, bandits: int = 1) -> dict:
    """A party at camp whose checkpoint holds a bandit raid that nothing triggers on its own."""

    pack = _travel_pack()
    pack['startingState']['checkpointId'] = 'cp_camp'
    pack['checkpoints'] = [{'id': 'cp_camp', 'title': 'Camp', 'locationIds': ['camp'], 'encounterIds': ['enc_camp_raid']}]
    pack['encounters'] = [
        {'id': 'enc_camp_raid', 'title': 'Camp Raid', 'checkpointIds': ['cp_camp'], 'scaling': False,
         'enemyGroups': [{'enemyId': 'bandit', 'count': bandits}]},
    ]
    return pack


@pytest.fixture()
def camp(client, app, monkeypatch):
    """``camp(pack, **stats)`` imports the pack, seats one hero with those stats and returns the ids."""

    monkeypatch.setattr(turn_pipeline_module, 'recent_timeline_for_session', lambda *_args, **_kwargs: [])
    # Narration's own outcomes are out of scope here: only the engine changes state.
    monkeypatch.setattr(
        turn_pipeline_module,
        'extract_post_dm_outcomes',
        lambda **_kwargs: {'proposedChanges': [], 'uncertainChanges': [], 'notes': [], 'debug': {}},
    )

    def hero(pack: dict, **stats) -> dict:
        response = client.post('/api/campaigns/import-pack', json={'pack': pack})
        assert response.status_code == 201, response.get_json()
        ids = {key: response.get_json()[key] for key in ('campaign_id', 'session_id')}
        with app.app_context():
            campaign = db.session.get(Campaign, ids['campaign_id'])
            player = Player(workspace_id=campaign.workspace_id, campaign_id=campaign.campaign_id, name='Alice',
                            character_name='Seraphina', class_='Fighter', level=1,
                            stats=safe_json_dumps({'ability_scores': {'strength': 15, 'dexterity': 12, 'constitution': 14},
                                                   **stats}, {}))
            db.session.add(player)
            db.session.commit()
            ids['player_id'] = player.player_id
        ids['hero_id'] = f"player_{ids['player_id']}"
        return ids

    return hero


def _play_turn(app, ids: dict, message: str, narration: str, *, narration_asks_for_a_roll: bool = False) -> tuple[dict, dict]:
    """Both halves of the state pipeline for one turn; returns its pipeline record and the saved state."""

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        player = db.session.get(Player, ids['player_id'])
        turn = DmTurn(session_id=session_obj.session_id, campaign_id=campaign.campaign_id, player_id=player.player_id,
                      player_input=message, dm_output=narration)
        db.session.add(turn)
        db.session.flush()
        turn_pipeline_module.pre_dm_pipeline(
            turn=turn, session_obj=session_obj, campaign=campaign, player=player, player_message=message,
        )
        if narration_asks_for_a_roll:
            # The turn engine's roll gate after "Roll initiative!".
            turn.requires_roll, turn.outcome_status = True, 'deferred'
        turn_pipeline_module.post_dm_pipeline(
            turn=turn, session_obj=session_obj, campaign=campaign, player=player, dm_response_text=narration,
        )
        db.session.commit()
        return safe_json_loads(turn.metadata_json, {})['state_pipeline'], safe_json_loads(session_obj.state_snapshot, {})


def test_a_hostile_action_that_starts_a_fight_lets_the_enemies_open_it(play_now_table, monkeypatch):
    table = play_now_table
    _enemies_go_first(monkeypatch)
    hero_id = f"player_{table.ids['player_id']}"

    fight = table.send('Arden Vale attacks the nearest cutthroat.', {'kind': 'message', 'source': 'composer'})
    combat = fight.snapshot['combat']
    enemy_ids = [participant['id'] for participant in combat['participants'] if participant['team'] == 'enemy']
    with table.app.app_context():
        turn = DmTurn.query.filter_by(session_id=table.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
        metadata = safe_json_loads(turn.metadata_json, {})
        attack_roll_pending = turn.requires_roll
    pipeline = metadata['state_pipeline']
    engine_results = _engine_results(pipeline['postAppliedChanges'])

    assert combat['flags']['combatStartedBy'] == 'player_hostile_action'
    assert combat['initiative'][-1]['participantId'] == hero_id
    assert (combat['status'], combat['flags']['activeActorId'], combat['round']) == ('active', hero_id, 1)
    # The enemies' opening turns were resolved before narration and recorded as trusted results.
    assert [action['enemyId'] for action in pipeline['dmContextPacket']['combatState']['enemyResolvedActions']] == enemy_ids
    visible_results = {change['id'] for change in engine_results if change.get('visible')}
    assert visible_results and visible_results <= {line['changeId'] for line in pipeline['stateLog']['lines']}
    # The attack that started the fight waits for the hero's own turn: no roll, no damage.
    assert (attack_roll_pending, metadata['roll_requirement_cleared']['source']) == (False, 'combat_opening')
    assert all(participant['hp']['current'] == participant['hp']['max']
               for participant in combat['participants'] if participant['team'] == 'enemy')
    assert table._hud_action('combat.end_turn') is not None


def test_two_enemies_ahead_of_the_hero_both_act_before_the_heros_first_turn(app, camp, monkeypatch):
    ids = camp(_camp_pack(bandits=2))
    _enemies_go_first(monkeypatch)

    pipeline, state = _play_turn(app, ids, 'I attack the bandit.', 'Steel rings out across the camp.')
    combat = state['combat']
    bandit_ids = [participant['id'] for participant in combat['participants'] if participant['team'] == 'enemy']

    assert len(bandit_ids) == 2
    assert [entry['participantId'] for entry in combat['initiative']] == [*bandit_ids, ids['hero_id']]
    assert sorted(action['enemyId'] for action in pipeline['dmContextPacket']['combatState']['enemyResolvedActions']) == sorted(bandit_ids)
    assert {change['sourceEnemyId'] for change in _engine_results(pipeline['postAppliedChanges'])} == set(bandit_ids)
    assert (combat['status'], combat['flags']['activeActorId'], combat['round']) == ('active', ids['hero_id'], 1)
    assert sorted(combat['flags']['lastEnemyTurnBlock']) == sorted(bandit_ids)


def test_narrated_opening_turns_land_even_when_the_narration_calls_for_initiative(app, camp, monkeypatch):
    ids = camp(_camp_pack())
    # The bandit's narrated strike hits (19, then 4 damage); any later roll is a 1 and would miss.
    rolls = iter([19, 4])
    _enemies_go_first(monkeypatch, roll=lambda _sides: next(rolls, 1))

    pipeline, state = _play_turn(
        app, ids, 'I attack the bandit.', 'The bandit is quicker. Roll initiative!', narration_asks_for_a_roll=True,
    )
    narrated = pipeline['dmContextPacket']['combatState']['enemyResolvedActions'][0]
    health = state['playerCharacters'][0]['health']

    assert (narrated['hit'], narrated['damageTotal']) == (True, 4)
    assert health['currentHp'] == health['maxHp'] - 4
    assert (state['combat']['flags']['activeActorId'], state['combat']['round']) == (ids['hero_id'], 1)


def test_in_a_pack_adventure_a_fight_narration_alone_starts_no_fight(app, camp, monkeypatch):
    ids = camp(_camp_pack())
    _enemies_go_first(monkeypatch)

    pipeline, state = _play_turn(app, ids, 'We rest by the fire.', 'A bandit bursts from the trees and attacks you!')

    # Nothing is due and the hero attacked no one: the camp raid waits for what the pack says starts it.
    assert state['combat']['status'] != 'active'
    assert _engine_results(pipeline['postAppliedChanges']) == []
    assert not (state.get('flags') or {}).get('campaignPackTriggeredEncounterIds')


def test_opening_turns_that_down_the_only_hero_end_the_fight(app, camp, monkeypatch):
    ids = camp(_camp_pack(), current_hp=1, hp_current=1, max_hp=12, hp_max=12)
    # Every engine die rolls its highest: the bandit's strike is a critical hit.
    _enemies_go_first(monkeypatch, roll=lambda sides: sides)

    _pipeline, state = _play_turn(app, ids, 'I attack the bandit.', 'The bandit strikes first.')

    assert (state['combat']['status'], state['combat']['flags']['endReason']) == ('ended', 'objective_failed')
    # The beaten hero wakes later with 1 HP, and the bandit is gone.
    assert state['playerCharacters'][0]['health']['currentHp'] == 1
    assert [participant['team'] for participant in state['combat']['participants']] == ['player']


def test_an_operator_started_fight_resolves_the_enemies_opening_turns(client, app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    hero_id = f"player_{ids['player_id']}"
    _enemies_go_first(monkeypatch)
    with app.app_context():
        db.session.get(Player, ids['player_id']).stats = safe_json_dumps({'current_hp': 20, 'max_hp': 20}, {})
        db.session.commit()

    response = client.post(
        f"/api/sessions/{ids['session_id']}/combat/start",
        json={'creature': core_creature('wolf'), 'enemyCount': 1},
    )

    assert response.status_code == 200, response.get_json()
    combat = response.get_json()['combat']
    wolf_id = next(participant['id'] for participant in combat['participants'] if participant['team'] == 'enemy')
    assert combat['initiative'][0]['participantId'] == wolf_id
    assert (combat['status'], combat['flags']['activeActorId'], combat['round']) == ('active', hero_id, 1)
    assert combat['flags']['lastEnemyTurnBlock'] == [wolf_id]
    with app.app_context():
        ledger = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})['stateChangeLedger']
        event = CombatDebugEvent.query.filter_by(session_id=ids['session_id'], event_type='api_combat_start').one()
        opening_turns = safe_json_loads(event.payload_json, {})['openingTurns']
    assert [action['enemyId'] for action in opening_turns] == [wolf_id]
    assert _engine_results(ledger)


def test_an_operator_started_fight_the_enemies_win_at_once_wakes_the_hero_before_it_returns(client, app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    hero_id = f"player_{ids['player_id']}"
    # The wolf wins initiative and every engine die rolls its highest: its opening bite drops the only hero.
    _enemies_go_first(monkeypatch, roll=lambda sides: sides)
    with app.app_context():
        db.session.get(Player, ids['player_id']).stats = safe_json_dumps({'current_hp': 1, 'max_hp': 20}, {})
        db.session.commit()

    response = client.post(
        f"/api/sessions/{ids['session_id']}/combat/start",
        json={'creature': core_creature('wolf'), 'enemyCount': 1},
    )

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert (body['combat']['status'], body['combat']['flags']['endReason']) == ('ended', 'objective_failed')
    assert body['combat']['flags']['defeatRecovery']['wokenActorIds'] == [hero_id]
    assert [participant['team'] for participant in body['combat']['participants']] == ['player']
    state = client.get(f"/api/sessions/{ids['session_id']}/state").get_json()['state_snapshot']
    with app.app_context():
        player_stats = safe_json_loads(db.session.get(Player, ids['player_id']).stats, {})
    hero = state['playerCharacters'][0]
    assert (hero['health']['currentHp'], hero['health']['conditions']) == (1, [])
    assert player_stats['current_hp'] == 1
    assert state['clock']['phase'] == 'afternoon'
    # The waking joins the opening turns' revision and audit record.
    recovery = next(change for change in state['stateChangeLedger'] if change['type'] == 'combat.defeat_recovery')
    with app.app_context():
        audits = SessionStateMutationAudit.query.filter_by(session_id=ids['session_id']).all()
        recorded = [(audit.source, audit.state_revision, safe_json_loads(audit.applied_change_ids_json, [])) for audit in audits]
    assert body['stateRevision'] == state['stateRevision'] == 1
    assert len(recorded) == 1 and recorded[0][:2] == ('api.combat.start', 1)
    assert recovery['id'] in recorded[0][2]
    assert recovery['id'] in state['lastMutation']['appliedChangeIds']


def test_a_turn_never_ends_waiting_on_an_enemy(app, camp, monkeypatch):
    ids = camp(_camp_pack())
    _enemies_go_first(monkeypatch)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        state = safe_json_loads(session_obj.state_snapshot, {})
        hero = {'id': ids['hero_id'], 'name': 'Seraphina', 'health': {'currentHp': 12, 'maxHp': 12}}
        # A fight left on "Bandit is acting now" before this rule existed.
        state['combat'] = {
            'status': 'active', 'round': 1, 'turnIndex': 0,
            'participants': [instantiate_creature(BANDIT, instance_id='enemy_bandit_1'), player_combat_participant(hero)],
            'initiative': [{'participantId': 'enemy_bandit_1', 'order': 0}, {'participantId': ids['hero_id'], 'order': 1}],
            'flags': {'activeActorId': 'enemy_bandit_1'},
        }
        session_obj.state_snapshot = safe_json_dumps(state, {})
        db.session.commit()

    pipeline, state = _play_turn(app, ids, 'I keep my guard up.', 'The bandit circles you.')

    assert [change['sourceEnemyId'] for change in _engine_results(pipeline['postAppliedChanges'])][:1] == ['enemy_bandit_1']
    assert (state['combat']['flags']['activeActorId'], state['combat']['round']) == (ids['hero_id'], 1)


def test_the_engine_plays_every_non_player_turn_ahead_of_the_hero(monkeypatch):
    _enemies_go_first(monkeypatch)
    state = _state([])
    hero = player_combat_participant(state['playerCharacters'][0])
    wolf = instantiate_creature(core_creature('wolf'), instance_id='enemy_wolf_1')
    hound = instantiate_creature({**core_creature('wolf'), 'name': 'Loyal Hound'}, instance_id='ally_hound', team='ally')
    state['combat'] = {
        'status': 'active', 'round': 1, 'turnIndex': 0, 'participants': [hero, wolf, hound],
        'initiative': [{'participantId': 'enemy_wolf_1', 'order': 0}, {'participantId': 'ally_hound', 'order': 1},
                       {'participantId': 'player_1', 'order': 2}],
        'flags': {'activeActorId': 'enemy_wolf_1'},
    }

    settled = settle_engine_controlled_turns(state, turn_id=9, already_applied_changes=[])

    combat = settled.state['combat']
    assert [action['enemyId'] for action in settled.resolved_actions] == ['enemy_wolf_1']
    advances = [change for change in settled.applied_changes if change['type'] == 'combat.update' and 'lastResolvedActorId' in change['flags']]
    assert [change['flags']['lastResolvedActorId'] for change in advances] == [
        'enemy_wolf_1',
        'ally_hound',
    ]
    assert (combat['status'], combat['flags']['activeActorId'], combat['round']) == ('active', 'player_1', 1)
    assert settled.validation['rejected'] == []



def _beaten_state(trigger: dict, *, flags: dict | None = None, **encounter_fields) -> dict:
    """A combat that just ended with the only hero down (dying, then stabilized by the end) and prone."""

    state = _state(
        [_encounter('enc_raid', trigger, **encounter_fields)],
        flags={TRIGGERED_ENCOUNTERS_FLAG: ['enc_raid'], **(flags or {})},
    )
    state['clock'] = {'day': 1, 'phase': 'evening'}
    start = materialize_campaign_pack_combat_start(state, {'type': 'combat.start', 'campaignPackEncounterId': 'enc_raid', 'combat': {}})
    state = apply_state_changes(state, [{**start, 'id': 'start_raid'}])['nextState']
    hero = next(participant for participant in state['combat']['participants'] if participant['team'] == 'player')
    hero.update({'hp': {'current': 0, 'max': 12, 'temp': 0}, 'conditions': ['unconscious', 'dying', 'prone'],
                 'deathSaves': {'successes': 1, 'failures': 2}, 'isConscious': False})
    state['playerCharacters'][0]['health'].update({'currentHp': 0, 'conditions': ['unconscious', 'dying']})
    return apply_state_changes(state, [combat_end_change(30, 'objective_failed')])['nextState']


@pytest.mark.parametrize(
    'trigger',
    [
        {'type': 'on_enter', 'locationId': 'bridge'},
        {'type': 'on_interactable', 'targetId': 'toll_chest', 'action': 'search'},
    ],
    ids=['travel', 'object'],
)
def test_a_beaten_party_wakes_later_and_the_encounter_can_fire_again(trigger):
    state = _beaten_state(trigger)
    cause = (
        {'id': 'again', 'type': 'scene.move_location', 'locationId': 'bridge'}
        if trigger['type'] == 'on_enter'
        else {'id': 'again', 'type': 'scene.interactable.action', 'targetId': 'toll_chest', 'objectAction': 'search', 'event': {'type': 'interactable.searched'}}
    )
    assert due_encounter(state, [cause]) is None

    change = party_defeat_recovery(state, turn_id=31)

    hero = state['playerCharacters'][0]
    participant = state['combat']['participants'][0]
    assert change['type'] == 'combat.defeat_recovery'
    assert (hero['health']['currentHp'], hero['health']['conditions']) == (1, [])
    assert (participant['hp']['current'], participant['conditions'], participant['deathSaves']) == (1, [], {'successes': 0, 'failures': 0})
    assert participant['isConscious'] is True
    # The clock moved one phase, the enemies left, and the encounter is armed again.
    assert state['clock']['phase'] == 'night' and state['clock']['day'] == 1
    assert [participant['team'] for participant in state['combat']['participants']] == ['player']
    assert 'enc_raid' not in state['flags'][TRIGGERED_ENCOUNTERS_FLAG]
    assert due_encounter(state, [cause])['id'] == 'enc_raid'
    assert (change['departedEnemies'], change['departedEnemiesPlural']) == ('the bandit', False)
    # The defeat is the encounter's recorded outcome, and it paid nothing.
    assert [(entry['encounterId'], entry['combatOutcome']) for entry in state['combatRewardLedger']] == [('enc_raid', 'defeat')]
    assert party_defeat_recovery(state, turn_id=32) is None


def test_a_lethal_encounter_wakes_the_party_like_any_defeat():
    trigger = {'type': 'on_enter', 'locationId': 'bridge'}
    beaten, lethal = _beaten_state(trigger), _beaten_state(trigger, lethalDefeat=True)

    # lethalDefeat only routes the story (campaign_pack_progress); the waking is the same.
    assert party_defeat_recovery(lethal, turn_id=31) == party_defeat_recovery(beaten, turn_id=31)
    assert lethal['playerCharacters'] == beaten['playerCharacters']
    assert lethal['playerCharacters'][0]['health']['currentHp'] == 1
    assert (lethal['clock'], lethal['flags'][TRIGGERED_ENCOUNTERS_FLAG]) == (beaten['clock'], [])


def _bridge_checkpoint_pack(*, lethal: bool) -> dict:
    """Holding the bridge is a checkpoint whose failure route is the ford; its fight may be lethal."""

    pack = _travel_pack()
    pack['startingState']['checkpointId'] = 'cp_bridge'
    pack['checkpoints'] = [
        {'id': 'cp_bridge', 'title': 'Hold the bridge', 'completeWhen': {'encounterIds': ['enc_bridge']},
         'failWhen': {'encounterIds': ['enc_bridge']}, 'nextCheckpointIds': ['cp_far_bank'], 'failureCheckpointIds': ['cp_ford']},
        {'id': 'cp_far_bank', 'title': 'The far bank'},
        {'id': 'cp_ford', 'title': 'Find the ford'},
    ]
    pack['encounters'] = [
        _encounter('enc_bridge', {'type': 'on_enter', 'locationId': 'bridge'}, scaling=False,
                   enemyGroups=[{'enemyId': 'bandit', 'count': 1}], **({'lethalDefeat': True} if lethal else {})),
    ]
    return pack


@pytest.mark.parametrize(('lethal', 'failed', 'active'), [(False, [], 'cp_bridge'), (True, ['cp_bridge'], 'cp_ford')])
def test_a_beaten_party_wakes_when_the_fight_ends_and_only_a_lethal_one_fails_the_checkpoint(
    client, app, socketio, monkeypatch, lethal, failed, active,
):
    for target in ('requests.Session.request', 'aidm_server.llm_providers.http_post', 'aidm_server.llm_providers.subprocess.Popen'):
        monkeypatch.setattr(target, lambda *_args, **_kwargs: pytest.fail('Safe Mode must not call a network or CLI model'))
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    response = client.post('/api/campaigns/import-pack', json={'pack': _bridge_checkpoint_pack(lethal=lethal)})
    assert response.status_code == 201, response.get_json()
    ids = {key: response.get_json()[key] for key in ('campaign_id', 'session_id')}
    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        player = Player(workspace_id=campaign.workspace_id, campaign_id=campaign.campaign_id, name='Alice',
                        character_name='Seraphina', class_='Fighter', level=1,
                        stats=safe_json_dumps({'ability_scores': {'strength': 15, 'dexterity': 12, 'constitution': 14},
                                               'current_hp': 1, 'hp_current': 1, 'max_hp': 12, 'hp_max': 12}, {}))
        db.session.add(player)
        db.session.commit()
        ids['player_id'], ids['workspace_id'] = player.player_id, campaign.workspace_id
    state_path = f"/api/sessions/{ids['session_id']}/state?view=player&player_id={ids['player_id']}"
    clock_before = client.get(state_path).get_json()['state_snapshot']['clock']
    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit('join_session', {key: ids[key] for key in ('session_id', 'player_id', 'workspace_id')})
    socket_client.get_received()
    # The bandit wins initiative and every engine die rolls its highest: its opening strike drops the hero.
    _enemies_go_first(monkeypatch, roll=lambda sides: sides)

    socket_client.emit('send_message', {
        'session_id': ids['session_id'], 'campaign_id': ids['campaign_id'], 'player_id': ids['player_id'],
        'message': 'The party travels to Bridge.', 'client_message_id': 'cross-bridge',
        'action_intent': {'kind': 'travel', 'source': 'scene_panel', 'location': {'id': 'bridge', 'name': 'Bridge'},
                          'text': 'The party travels to Bridge.', 'client_message_id': 'cross-bridge'},
    })
    received = socket_client.get_received()
    socket_client.disconnect()

    assert [event['args'] for event in received if event['name'] == 'error'] == []
    beats = [beat['text'] for event in received if event['name'] == 'turn_status'
             for beat in (event['args'][0].get('details') or {}).get('storyBeats') or []]
    assert 'Defeated — you wake later with 1 HP' in beats
    # Awake at once, lethal or not: 1 HP a phase later, and the bandit gone.
    snapshot = client.get(state_path).get_json()['state_snapshot']
    hero = snapshot['playerCharacters'][0]
    assert (snapshot['combat']['status'], snapshot['combat']['flags']['endReason']) == ('ended', 'objective_failed')
    assert (hero['health']['currentHp'], hero['health']['conditions']) == (1, [])
    assert [participant['team'] for participant in snapshot['combat']['participants']] == ['player']
    assert (clock_before['label'], snapshot['clock']['label']) == ('Day 1 · Morning', 'Day 1 · Afternoon')
    # Only a lethal defeat fails the checkpoint and takes its failure route.
    progress = client.get(f"/api/sessions/{ids['session_id']}/campaign-pack/progress").get_json()
    assert (progress['failedCheckpointIds'], progress['activeCheckpointId']) == (failed, active)


def test_a_defeat_then_a_victory_pays_the_once_only_reward_once(app, camp, client, monkeypatch):
    pack = _travel_pack()
    pack['encounters'] = [
        _encounter('enc_bridge', {'type': 'on_enter', 'locationId': 'bridge'}, scaling=False,
                   enemyGroups=[{'enemyId': 'bandit', 'count': 1}], rewards={'xp': 50, 'gp': 10}),
    ]
    ids = camp(pack, current_hp=1, hp_current=1, max_hp=12, hp_max=12)
    # The bandit wins initiative and every engine die rolls its highest: the hero goes down at once.
    _enemies_go_first(monkeypatch, roll=lambda sides: sides)

    pipeline, beaten = _play_turn(app, ids, 'go to the bridge', 'You set out for the bridge.')
    hero = beaten['playerCharacters'][0]
    recovery = next(change for change in pipeline['postAppliedChanges'] if change['type'] == 'combat.defeat_recovery')

    assert pipeline['dmContextPacket']['narration']['partyDefeat'] == {'enemies': 'the bandit', 'plural': False}
    assert (beaten['combat']['flags']['endReason'], hero['health']['currentHp']) == ('objective_failed', 1)
    assert recovery['clock']['phase'] != pipeline['stateBeforeDm'].get('clock', {}).get('phase')
    assert 'enc_bridge' not in beaten['flags'][TRIGGERED_ENCOUNTERS_FLAG]
    assert [participant['team'] for participant in beaten['combat']['participants']] == ['player']

    _enemies_go_first(monkeypatch)
    _play_turn(app, ids, 'go to the camp', 'You limp back to camp.')
    _pipeline, again = _play_turn(app, ids, 'go to the bridge', 'You return to the bridge.')
    assert again['combat']['status'] == 'active'
    assert again['flags'][TRIGGERED_ENCOUNTERS_FLAG] == ['enc_bridge']

    response = client.post(
        f"/api/sessions/{ids['session_id']}/combat/apply-state-changes",
        json={'changes': [{'type': 'combat.end', 'status': 'ended', 'endReason': 'all_enemies_defeated'}]},
    )
    assert response.status_code == 200, response.get_json()
    with app.app_context():
        won = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
    rewards = [(entry['encounterId'], entry['combatOutcome']) for entry in won['combatRewardLedger']]
    currency = won['playerCharacters'][0]['inventory']['currency']

    assert rewards == [('enc_bridge', 'defeat'), ('enc_bridge', 'victory')]
    assert (won['playerCharacters'][0]['xp']['current'], currency.get('gp')) == (50, 10)


def test_pack_enemies_are_named_by_their_singular_name_and_numbered_in_groups():
    encounter = _encounter('enc_named', {'type': 'on_enter', 'locationId': 'bridge'}, scaling=False)
    encounter['enemyGroups'] = [{'enemyId': 'cutthroats', 'count': 2}, {'enemyId': 'marshal', 'count': 1}, {'enemyId': 'bandit', 'count': 1}]
    state = _state([encounter], players=1)
    state['campaignPack']['catalog']['enemies'].extend(
        [
            {'id': 'cutthroats', 'name': 'Ashmarked Cutthroats', 'singularName': 'Ashmarked Cutthroat', 'stats': {'maxHp': 18}},
            {'id': 'marshal', 'name': 'The Hollow Marshal', 'proper': True, 'stats': {'maxHp': 88}},
        ]
    )

    change = materialize_campaign_pack_combat_start(state, {'type': 'combat.start', 'campaignPackEncounterId': 'enc_named', 'combat': {}})
    enemies = [participant for participant in change['combat']['participants'] if participant['team'] == 'enemy']

    assert [(enemy['name'], enemy.get('proper', False)) for enemy in enemies] == [
        ('Ashmarked Cutthroat 1', False),
        ('Ashmarked Cutthroat 2', False),
        ('The Hollow Marshal', True),
        ('Bandit', False),
    ]
