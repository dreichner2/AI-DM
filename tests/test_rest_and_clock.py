from __future__ import annotations

import secrets

import pytest

from aidm_server.action_intent import validate_action_intent
from aidm_server.database import db
from aidm_server.game_clock import clock_label, scene_rest_policy
from aidm_server.game_state.application.applier import apply_state_changes, persist_state_to_database
from aidm_server.game_state.extraction.pre_dm_action_extractor import extract_pre_dm_actions
from aidm_server.game_state.models import state_snapshot_for_session
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.llm_context import _compact_live_world_state
from aidm_server.models import Campaign, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from tests.helpers import seed_world_campaign_player_session


def _dice(monkeypatch: pytest.MonkeyPatch, *values: int) -> None:
    rolls = iter(values)
    monkeypatch.setattr(secrets, 'randbelow', lambda sides: next(rolls) - 1)


def _state(*, hp: int = 4, level: int = 3, hit_dice: int | None = None, scene: dict | None = None, clock: dict | None = None) -> dict:
    actor = {
        'id': 'player_1',
        'playerId': 1,
        'name': 'Aria',
        'class': 'Fighter',
        'level': level,
        'stats': {'constitution': 14},
        'health': {'currentHp': hp, 'maxHp': 30, 'tempHp': 0, 'conditions': []},
        'inventory': {'items': [], 'currency': {}},
        'xp': {'current': 0},
    }
    if hit_dice is not None:
        actor['hitDice'] = {'current': hit_dice}
    state = {
        'currentScene': {
            'locationId': 'camp',
            'name': 'Camp',
            'combatState': 'none',
            'activeNpcIds': [],
            **(scene or {}),
        },
        'locations': [
            {'id': 'camp', 'name': 'Camp', 'status': 'visited', 'connectedLocationIds': ['ford']},
            {'id': 'ford', 'name': 'Ford', 'status': 'discovered', 'connectedLocationIds': ['camp']},
        ],
        'activePlayerIds': [1],
        'playerCharacters': [actor],
        'flags': {},
        'stateChangeLedger': [],
    }
    if clock is not None:
        state['clock'] = clock
    return state


def _rest(state: dict, rest_type: str, *, turn_id: int, hit_dice: int | None = None) -> tuple[dict, dict, dict]:
    action = {'id': 'act_001', 'type': 'rest.complete', 'actorId': 'player_1', 'sourceText': 'I rest.', 'restType': rest_type}
    if hit_dice is not None:
        action['hitDice'] = hit_dice
    declared = validate_declared_actions(
        state=state,
        declared_actions=[action],
        current_turn=turn_id,
        expected_actor_id='player_1',
    )
    validation = validate_state_changes(state=state, changes=declared['immediateChanges'], expected_actor_id='player_1')
    applied = apply_state_changes(state, validated_changes_for_application(validation))
    return declared, validation, applied


def _actor(state: dict) -> dict:
    return state['playerCharacters'][0]


def test_rest_intent_accepts_a_hit_dice_count_for_short_rests_only() -> None:
    intent, error = validate_action_intent({'kind': 'rest', 'rest_type': 'short', 'hit_dice': 2})
    assert error is None and intent['hit_dice'] == 2
    long_rest, error = validate_action_intent({'kind': 'rest', 'rest_type': 'long_rest', 'hit_dice': 2})
    assert error is None and 'hit_dice' not in long_rest
    _intent, error = validate_action_intent({'kind': 'rest', 'rest_type': 'short_rest', 'hit_dice': -1})
    assert error == 'hit_dice must be a whole number from 0 to 20.'


@pytest.mark.parametrize('requested', [0, 1])
def test_short_rest_hit_dice_survive_the_action_extraction_path(requested, monkeypatch) -> None:
    if requested:
        _dice(monkeypatch, 4)
    state = _state(hp=10)
    intent, error = validate_action_intent({'kind': 'rest', 'rest_type': 'short_rest', 'hit_dice': requested})
    assert error is None
    extraction = extract_pre_dm_actions(
        current_state=state, player_message='I rest.', recent_timeline=[], actor_id='player_1', action_intent=intent
    )
    assert extraction['declaredActions'][0]['hitDice'] == requested
    declared = validate_declared_actions(
        state=state, declared_actions=extraction['declaredActions'], current_turn=3, expected_actor_id='player_1'
    )
    assert declared['immediateChanges'][0]['hitDiceResolution']['spent'] == requested


def test_short_rest_spends_hit_dice_until_full_by_default(monkeypatch) -> None:
    _dice(monkeypatch, 10, 9, 8)
    declared, validation, applied = _rest(_state(hp=4), 'short_rest', turn_id=3)
    resolution = declared['immediateChanges'][0]['hitDiceResolution']
    # d10 + CON 2 per die: 12 + 11 = 23 heals 4 -> 27; a third die finishes the job.
    assert resolution['rolls'] == [10, 9, 8]
    assert resolution['spent'] == 3
    assert validation['rejected'] == []
    actor = _actor(applied['nextState'])
    assert actor['health']['currentHp'] == 30
    assert actor['hitDice'] == {'current': 0, 'max': 3, 'die': 'd10'}
    assert 'spending 3 hit dice' in declared['validatedActions'][0]['reason']


def test_short_rest_spends_the_requested_number_with_a_minimum_of_one_per_die(monkeypatch) -> None:
    _dice(monkeypatch, 1)
    state = _state(hp=10)
    _actor(state)['stats']['constitution'] = 6
    declared, _validation, applied = _rest(state, 'short_rest', turn_id=3, hit_dice=1)
    resolution = declared['immediateChanges'][0]['hitDiceResolution']
    assert resolution['conModifier'] == -2
    assert resolution['healing'] == 1
    assert _actor(applied['nextState'])['health']['currentHp'] == 11
    assert _actor(applied['nextState'])['hitDice']['current'] == 2

    declared, _validation, applied = _rest(_state(hp=10), 'short_rest', turn_id=4, hit_dice=0)
    assert declared['immediateChanges'][0]['hitDiceResolution']['spent'] == 0
    assert _actor(applied['nextState'])['health']['currentHp'] == 10


def test_long_rest_restores_hit_points_and_half_the_hit_dice() -> None:
    _declared, _validation, applied = _rest(_state(hp=4, level=5, hit_dice=0), 'long_rest', turn_id=3)
    actor = _actor(applied['nextState'])
    assert actor['health']['currentHp'] == 30
    assert actor['hitDice']['current'] == 2

    _declared, _validation, applied = _rest(_state(hp=4, level=1, hit_dice=0), 'long_rest', turn_id=4)
    assert _actor(applied['nextState'])['hitDice']['current'] == 1


def test_forged_hit_dice_rolls_are_rejected(monkeypatch) -> None:
    _dice(monkeypatch, 2)
    state = _state(hp=20)
    declared, _validation, _applied = _rest(state, 'short_rest', turn_id=3, hit_dice=1)
    forged = dict(declared['immediateChanges'][0])
    forged['hitDiceResolution'] = {**forged['hitDiceResolution'], 'healing': 12}
    rejected = validate_state_changes(state=state, changes=[forged])['rejected']
    assert rejected[0]['reason'] == 'Hit dice result does not match the authoritative roll replay.'


def test_rests_advance_the_clock_and_short_rests_are_limited() -> None:
    state = _state(hp=30)
    assert clock_label(state.get('clock')) == 'Day 1 · Morning'

    _declared, _validation, first = _rest(state, 'short_rest', turn_id=3)
    assert first['nextState']['clock'] == {'day': 1, 'phase': 'afternoon', 'shortRestsSinceLong': 1}
    _declared, _validation, second = _rest(first['nextState'], 'short_rest', turn_id=4)
    assert second['nextState']['clock']['phase'] == 'evening'

    declared, _validation, third = _rest(second['nextState'], 'short_rest', turn_id=5)
    assert declared['validatedActions'][0]['reason'] == 'You need a long rest before another short rest helps.'
    assert third['nextState']['clock'] == second['nextState']['clock']

    _declared, _validation, slept = _rest(second['nextState'], 'long_rest', turn_id=6)
    assert slept['nextState']['clock'] == {'day': 2, 'phase': 'morning', 'shortRestsSinceLong': 0, 'lastLongRestDay': 2}

    declared, _validation, _again = _rest(slept['nextState'], 'long_rest', turn_id=7)
    assert declared['validatedActions'][0]['reason'] == 'You have already rested today.'

    tomorrow = {**slept['nextState'], 'clock': {**slept['nextState']['clock'], 'day': 3, 'phase': 'night'}}
    declared, _validation, _later = _rest(tomorrow, 'long_rest', turn_id=8)
    assert declared['validatedActions'][0]['status'] == 'valid'


def test_night_short_rest_rolls_over_to_the_next_morning() -> None:
    _declared, _validation, applied = _rest(_state(hp=30, clock={'day': 4, 'phase': 'night'}), 'short_rest', turn_id=3)
    assert applied['nextState']['clock'] == {'day': 5, 'phase': 'morning', 'shortRestsSinceLong': 1}


@pytest.mark.parametrize(
    ('scene', 'short_reason', 'long_reason'),
    [
        ({'restPolicy': 'safe', 'dangerLevel': 9}, None, None),
        ({'restPolicy': 'risky'}, None, "This place is too dangerous for a full night's rest."),
        ({'restPolicy': 'none'}, 'It is too dangerous to rest here.', 'It is too dangerous to rest here.'),
        ({'dangerLevel': 3}, None, "This place is too dangerous for a full night's rest."),
        ({'dangerLevel': 6}, 'It is too dangerous to rest here.', 'It is too dangerous to rest here.'),
    ],
)
def test_rest_policy_of_the_scene(scene, short_reason, long_reason) -> None:
    for rest_type, expected in (('short_rest', short_reason), ('long_rest', long_reason)):
        declared, _validation, _applied = _rest(_state(hp=30, scene=scene), rest_type, turn_id=3)
        result = declared['validatedActions'][0]
        if expected is None:
            assert result['status'] == 'valid'
        else:
            assert (result['status'], result['reason']) == ('invalid', expected)
    assert scene_rest_policy({'dangerLevel': 2}) == 'safe'


def test_combat_still_blocks_rest() -> None:
    state = _state(hp=30, scene={'combatState': 'active'})
    declared, _validation, _applied = _rest(state, 'short_rest', turn_id=3)
    assert declared['validatedActions'][0]['reason'] == 'Rest is unavailable while combat is active.'


def test_structured_travel_advances_one_phase() -> None:
    state = _state(hp=30, clock={'day': 2, 'phase': 'evening'})
    intent, error = validate_action_intent({'kind': 'travel', 'location': {'id': 'ford'}})
    assert error is None
    extraction = extract_pre_dm_actions(
        current_state=state,
        player_message='We cross to the ford.',
        recent_timeline=[],
        actor_id='player_1',
        action_intent=intent,
    )
    declared = validate_declared_actions(
        state=state,
        declared_actions=extraction['declaredActions'],
        current_turn=3,
        expected_actor_id='player_1',
    )
    validation = validate_state_changes(state=state, changes=declared['immediateChanges'], expected_actor_id='player_1')
    applied = apply_state_changes(state, validated_changes_for_application(validation))
    assert applied['nextState']['currentScene']['locationId'] == 'ford'
    assert applied['nextState']['clock'] == {'day': 2, 'phase': 'night', 'shortRestsSinceLong': 0}


def test_clock_and_rest_policy_reach_players_and_the_dm() -> None:
    snapshot = _state(hp=30, hit_dice=2, scene={'dangerLevel': 4}, clock={'day': 2, 'phase': 'evening', 'shortRestsSinceLong': 1})
    projected = filter_session_snapshot_for_player(snapshot, private_player_ids={1})
    assert projected['clock'] == {'day': 2, 'phase': 'evening', 'shortRestsSinceLong': 1, 'label': 'Day 2 · Evening'}
    assert projected['currentScene']['restPolicy'] == 'risky'
    assert projected['playerCharacters'][0]['hitDice'] == {'current': 2}
    assert 'hitDice' not in filter_session_snapshot_for_player(snapshot, private_player_ids=set())['playerCharacters'][0]
    assert _compact_live_world_state(snapshot)['clock']['label'] == 'Day 2 · Evening'


def test_clock_and_hit_dice_persist_across_a_snapshot_rebuild(app, monkeypatch) -> None:
    ids = seed_world_campaign_player_session(app)
    _dice(monkeypatch, 3)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        player = db.session.get(Player, ids['player_id'])
        player.class_ = 'Fighter'
        player.level = 2
        player.stats = safe_json_dumps({'current_hp': 5, 'max_hp': 20, 'ability_scores': {'constitution': 10}}, {})
        state = state_snapshot_for_session(session_obj=session_obj, campaign=campaign, players=[player])
        actor = state['playerCharacters'][0]
        assert actor['hitDice'] == {'current': 2, 'max': 2, 'die': 'd10'}
        declared = validate_declared_actions(
            state=state,
            declared_actions=[
                {'id': 'act_001', 'type': 'rest.complete', 'actorId': actor['id'], 'sourceText': 'Rest.', 'restType': 'short_rest', 'hitDice': 1}
            ],
            current_turn=2,
            expected_actor_id=actor['id'],
        )
        validation = validate_state_changes(state=state, changes=declared['immediateChanges'], expected_actor_id=actor['id'])
        applied = apply_state_changes(state, validated_changes_for_application(validation))
        persist_state_to_database(session_obj=session_obj, state=applied['nextState'], players_by_id={player.player_id: player})
        db.session.commit()

        assert safe_json_loads(player.stats, {})['hit_dice'] == 1
        rebuilt = state_snapshot_for_session(session_obj=session_obj, campaign=campaign, players=[player])
        assert rebuilt['clock'] == {'day': 1, 'phase': 'afternoon', 'shortRestsSinceLong': 1}
        assert rebuilt['playerCharacters'][0]['hitDice'] == {'current': 1, 'max': 2, 'die': 'd10'}
        assert rebuilt['playerCharacters'][0]['health']['currentHp'] == 8
