"""What the party learns changes the fights: object actions that settle an encounter, object actions usable in a
fight, encounter adjustments, and taking time over a failed check."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from aidm_server.action_intent import validate_action_intent
from aidm_server.combat.state import normalize_combat_state
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.campaign_pack_encounters import (
    RESOLVED_ENCOUNTERS_FLAG,
    TRIGGERED_ENCOUNTERS_FLAG,
    due_encounter,
    materialize_campaign_pack_combat_start,
    party_scaling,
)
from aidm_server.game_state.extraction.free_text_actions import free_text_action
from aidm_server.game_state.orchestration.story_beats import story_beats
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.interactables import REPEAT_CHECK_MESSAGE, available_actions_for_target
from aidm_server.models import Session, safe_json_loads
from aidm_server.scripted_narrator import narrate
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player

BANDIT = {'id': 'bandit', 'name': 'Bandit', 'source': 'campaign_pack', 'stats': {'maxHp': 20, 'armorClass': 12}}
WARDEN = {'id': 'warden', 'name': 'Warden', 'source': 'campaign_pack', 'stats': {'maxHp': 40, 'armorClass': 14}}
BANNER = {
    'id': 'banner',
    'name': 'Banner-Stone',
    'kind': 'object',
    'usable': True,
    'revision': 0,
    'allowedActions': ['use'],
    'usableInCombat': ['use'],
    'requirements': {'use': {'flags': {'oath_learned': True}}},
    'resolvesEncounter': {'use': {'encounterId': 'enc_judgment', 'outcome': 'negotiation'}},
}
BENCH = {
    'id': 'bench',
    'name': "Driver's Bench",
    'kind': 'object',
    'searchable': True,
    'revision': 0,
    'allowedActions': ['search'],
    'requirements': {'search': {'check': {'id': 'bench_search', 'dc': 10, 'skill': 'investigation'}}},
    'contents': [{'id': 'key', 'name': 'Strongbox Key', 'quantity': 1, 'hidden': True}],
}
JUDGMENT = {
    'id': 'enc_judgment',
    'title': 'Judgment',
    'questIds': ['quest_road'],
    'enemyGroups': [{'enemyId': 'warden', 'count': 2}],
    'trigger': {'type': 'on_enter', 'locationId': 'cairns'},
    'unless': {'anyOf': [{'flagKey': 'appeased', 'equals': True}, {'flagKey': 'oath_learned', 'equals': True}]},
    'completion': {'anyOf': ['negotiate', 'defeat']},
    'rewards': {'xp': 100, 'gp': 10},
    'outcomes': {'negotiation': {'consequences': [{'type': 'flag.set', 'flagKey': 'appeased', 'flagValue': True}]}},
}


def _actor(*, intelligence: int = 10) -> dict:
    return {
        'id': 'player_1',
        'playerId': 1,
        'name': 'Arden',
        'level': 1,
        'stats': {'intelligence': intelligence, 'dexterity': 12, 'strength': 14},
        'health': {'currentHp': 12, 'maxHp': 12, 'tempHp': 0, 'conditions': []},
        'inventory': {'items': [], 'currency': {'gp': 0}},
        'xp': {'current': 0},
    }


def _state(*, flags: dict | None = None, objects: list[dict] | None = None, encounters: list[dict] | None = None) -> dict:
    objects = deepcopy(objects if objects is not None else [BANNER])
    return {
        'sessionId': 7,
        'activePlayerIds': [1],
        'playerCharacters': [_actor()],
        'currentScene': {'locationId': 'cairns', 'name': 'Cairns', 'items': [], 'interactables': deepcopy(objects), 'hazards': []},
        'locations': [
            {'id': 'cairns', 'name': 'Cairns', 'status': 'visited', 'connectedLocationIds': ['road'],
             'sceneState': {'interactables': deepcopy(objects), 'hazards': []}},
            {'id': 'road', 'name': 'Road', 'status': 'visited', 'connectedLocationIds': ['cairns']},
        ],
        'flags': dict(flags or {}),
        'clock': {'day': 1, 'phase': 'morning'},
        'quests': [{'id': 'quest_road', 'title': 'The Road', 'status': 'active', 'objectives': []}],
        'combat': {'status': 'none', 'round': 1, 'participants': [], 'flags': {}},
        'campaignPack': {
            'packId': 'choices_pack',
            'catalog': {
                'enemies': [deepcopy(BANDIT), deepcopy(WARDEN)],
                'encounters': deepcopy(encounters if encounters is not None else [JUDGMENT]),
                'locations': [{'id': 'cairns', 'name': 'Cairns'}, {'id': 'road', 'name': 'Road'}],
            },
        },
        'stateChangeLedger': [],
    }


def _act(state: dict, target: str, action: str, *, turn: int = 4, **fields) -> dict:
    """Validate and apply one typed object action; returns the apply result (``nextState``, ``appliedChanges``)."""

    result = validate_declared_actions(
        state=state,
        declared_actions=[
            {'id': 'act_001', 'type': 'scene.interactable.action', 'actorId': 'player_1', 'targetId': target, 'objectAction': action, **fields}
        ],
        current_turn=turn,
        expected_actor_id='player_1',
    )
    validated = result['validatedActions'][0]
    assert validated['status'] == 'valid', validated['reason']
    changes = validated_changes_for_application(validate_state_changes(state=state, changes=result['immediateChanges'], expected_actor_id='player_1'))
    return apply_state_changes(state, changes)


def _refusal(state: dict, target: str, action: str, **fields) -> str:
    result = validate_declared_actions(
        state=state,
        declared_actions=[
            {'id': 'act_001', 'type': 'scene.interactable.action', 'actorId': 'player_1', 'targetId': target, 'objectAction': action, **fields}
        ],
        current_turn=9,
        expected_actor_id='player_1',
    )
    assert result['validatedActions'][0]['status'] == 'invalid'
    return result['validatedActions'][0]['reason']


def _rewards(state: dict) -> list[tuple[str, str]]:
    return [(entry['encounterId'], entry['combatOutcome']) for entry in state.get('combatRewardLedger') or []]


# --- 1. An object action can settle an encounter ----------------------------------------------------------------


def test_an_object_action_settles_its_encounter_without_a_fight_and_pays_once():
    state = _state(flags={'oath_learned': True})

    first = _act(state, 'banner', 'use')
    settled = first['nextState']
    again = _act(settled, 'banner', 'use', turn=5)['nextState']

    resolve = next(change for change in first['appliedChanges'] if change['type'] == 'encounter.resolve')
    assert (resolve['encounterId'], resolve['outcome'], resolve['endReason']) == ('enc_judgment', 'negotiation', 'negotiated_resolution')
    assert settled['flags']['appeased'] is True
    assert settled['flags']['campaignPackCompletedEncounterIds'] == ['enc_judgment']
    assert settled['flags'][RESOLVED_ENCOUNTERS_FLAG] == ['enc_judgment']
    assert _rewards(settled) == [('enc_judgment', 'negotiation')]
    assert (settled['playerCharacters'][0]['xp']['current'], settled['playerCharacters'][0]['inventory']['currency']['gp']) == (100, 10)
    assert any(change['type'] == 'combat.outcome' and change['questId'] == 'quest_road' for change in first['appliedChanges'])
    assert settled['combat']['status'] == 'none'
    # Using it again settles nothing more and pays nothing more.
    assert _rewards(again) == [('enc_judgment', 'negotiation')]
    assert (again['playerCharacters'][0]['xp']['current'], again['playerCharacters'][0]['inventory']['currency']['gp']) == (100, 10)


def test_a_settled_encounters_trigger_never_fires_afterwards():
    judgment = {**JUDGMENT, 'unless': {'flagKey': 'never_set', 'equals': True}, 'once': False}
    settled = _act(_state(flags={'oath_learned': True}, encounters=[judgment]), 'banner', 'use')['nextState']

    arrival = {'id': 'move_1', 'turnId': 6, 'type': 'scene.move_location', 'locationId': 'cairns', 'name': 'Cairns'}

    assert due_encounter(settled, [arrival]) is None
    assert due_encounter(_state(encounters=[judgment]), [arrival]) is not None


def test_unless_any_of_keeps_an_encounter_from_firing_when_either_flag_holds():
    arrival = {'id': 'move_1', 'turnId': 6, 'type': 'scene.move_location', 'locationId': 'cairns', 'name': 'Cairns'}

    assert due_encounter(_state(), [arrival])['id'] == 'enc_judgment'
    assert due_encounter(_state(flags={'oath_learned': True}), [arrival]) is None
    assert due_encounter(_state(flags={'appeased': True}), [arrival]) is None


def test_settling_the_encounter_of_an_active_fight_ends_it_with_that_outcome():
    state = _state(flags={'oath_learned': True})
    started = materialize_campaign_pack_combat_start(state, {'type': 'combat.start', 'campaignPackEncounterId': 'enc_judgment', 'combat': {}})
    state = apply_state_changes(state, [{**started, 'id': 'start_1', 'turnId': 3}])['nextState']
    state['combat'] = normalize_combat_state(
        {**state['combat'], 'turnIndex': 0, 'initiative': [{'participantId': 'player_1', 'order': 0}], 'flags': {**state['combat']['flags'], 'activeActorId': 'player_1'}},
        state['currentScene'],
    )

    result = _act(state, 'banner', 'use')
    after = result['nextState']

    end = next(change for change in result['appliedChanges'] if change['type'] == 'combat.end')
    assert end['endReason'] == 'negotiated_resolution'
    assert after['combat']['status'] == 'ended'
    assert _rewards(after) == [('enc_judgment', 'negotiation')]
    assert after['flags']['appeased'] is True


def test_the_linter_checks_what_an_object_settles_and_how_an_encounter_adjusts():
    pack = {
        'packId': 'lint_pack',
        'title': 'Lint Pack',
        'startingState': {'locationId': 'cairns', 'flags': {'oath_learned': False}},
        'locations': [
            {
                'id': 'cairns',
                'name': 'Cairns',
                'connectedLocationIds': [],
                'interactables': [
                    {**deepcopy(BANNER), 'resolvesEncounter': {'use': {'encounterId': 'enc_missing', 'outcome': 'negotiation'}}},
                    {**deepcopy(BANNER), 'id': 'shrine', 'resolvesEncounter': {'use': {'encounterId': 'enc_judgment', 'outcome': 'surrender'}}},
                ],
            }
        ],
        'enemies': [deepcopy(WARDEN)],
        'encounters': [
            {
                **deepcopy(JUDGMENT),
                'adjustments': [
                    {'when': {'flagKey': 'nobody_sets_this', 'equals': True}, 'enemyId': 'warden', 'hpMultiplier': 0.75, 'text': 'A.'},
                    {'when': {'flagKey': 'oath_learned', 'equals': True}, 'removeEnemyIds': ['bandit'], 'text': 'B.'},
                    {'when': {'flagKey': 'oath_learned', 'equals': True}, 'enemyId': 'warden', 'hpMultiplier': 2, 'text': 'C.'},
                ],
            }
        ],
    }

    issues = {(issue['code'], issue['path']) for issue in lint_campaign_pack_manifest(pack)['issues'] if issue['severity'] == 'error'}

    assert {
        ('unknown_resolved_encounter', 'locations[0].interactables[0].resolvesEncounter.use.encounterId'),
        ('encounter_outcome_not_allowed', 'locations[0].interactables[1].resolvesEncounter.use.outcome'),
        ('unknown_adjustment_flag', 'encounters[0].adjustments[0].when.flagKey'),
        ('unknown_adjustment_enemy', 'encounters[0].adjustments[1].removeEnemyIds'),
        ('adjustment_multiplier_out_of_range', 'encounters[0].adjustments[2].hpMultiplier'),
    } <= issues


def test_the_importer_keeps_the_new_object_and_encounter_fields(client, app):
    pack = json.loads((Path(__file__).resolve().parents[1] / 'docs' / 'examples' / 'the_road_of_unremembered_kings_campaign.json').read_text())
    session_id = client.post('/api/onboarding/play-now', json={}).get_json()['session_id']
    with app.app_context():
        from aidm_server.database import db

        snapshot = safe_json_loads(db.session.get(Session, session_id).state_snapshot, {})

    stone = next(
        record
        for location in snapshot['campaignPack']['catalog']['locations']
        for record in location.get('interactables') or []
        if record['id'] == 'cairn_banner_stone'
    )
    encounters = {encounter['id']: encounter for encounter in snapshot['campaignPack']['catalog']['encounters']}
    authored = {encounter['id']: encounter for encounter in pack['encounters']}
    assert (stone['usableInCombat'], stone['resolvesEncounter']) == (
        ['use'],
        {'use': {'encounterId': 'enc_roadwarden_judgment', 'outcome': 'negotiation'}},
    )
    assert encounters['enc_final_binding']['adjustments'] == authored['enc_final_binding']['adjustments']
    assert encounters['enc_roadwarden_judgment']['unless'] == authored['enc_roadwarden_judgment']['unless']
    # Neither field reaches players.
    projected = json.dumps(filter_session_snapshot_for_player(snapshot))
    assert 'resolvesEncounter' not in projected and 'usableInCombat' not in projected


# --- 3. What the party knows adjusts an encounter ----------------------------------------------------------------


FINAL = {
    'id': 'enc_final',
    'title': 'Final',
    'enemyGroups': [{'enemyId': 'warden', 'count': 1}, {'enemyId': 'bandit', 'count': 2}],
    'trigger': {'type': 'on_enter', 'locationId': 'road'},
    'adjustments': [
        {'when': {'flagKey': 'true_name', 'equals': True}, 'enemyId': 'warden', 'hpMultiplier': 0.75, 'text': 'You speak his name.'},
        {'when': {'flagKey': 'appeased', 'equals': True}, 'removeEnemyIds': ['bandit'], 'text': 'The bandits stand aside.'},
    ],
}


def _start(state: dict) -> dict:
    return materialize_campaign_pack_combat_start(state, {'type': 'combat.start', 'campaignPackEncounterId': 'enc_final', 'combat': {}})


def _pair(**options) -> dict:
    state = _state(**options)
    state['playerCharacters'].append({**_actor(), 'id': 'player_2', 'playerId': 2, 'name': 'Mara'})
    state['activePlayerIds'] = [1, 2]
    return state


def test_adjustments_apply_before_party_scaling_and_are_recorded():
    plain = _start(_pair(encounters=[FINAL]))
    known = _start(_pair(encounters=[FINAL], flags={'true_name': True, 'appeased': True}))

    def warden(change: dict) -> dict:
        return next(participant for participant in change['combat']['participants'] if participant.get('campaignPackEnemyId') == 'warden')

    # Two heroes: HP x 3/5 from party scaling, then x 3/4 from the adjustment (rounded up).
    assert warden(plain)['hp']['max'] == 24
    assert warden(known)['hp']['max'] == 18
    assert sorted({participant['campaignPackEnemyId'] for participant in plain['combat']['participants'] if participant['team'] == 'enemy'}) == ['bandit', 'warden']
    assert [participant['campaignPackEnemyId'] for participant in known['combat']['participants'] if participant['team'] == 'enemy'] == ['warden']
    scaling = known['combat']['flags']['partyScaling']
    assert [adjustment['text'] for adjustment in scaling['adjustments']] == ['You speak his name.', 'The bandits stand aside.']
    assert (scaling['droppedGroups'], [group['enemyId'] for group in scaling['groups']]) == ([], ['warden'])
    assert 'adjustments' not in plain['combat']['flags']['partyScaling']


def test_removed_groups_are_gone_before_the_most_enemies_is_applied():
    encounter = {**FINAL, 'enemyGroups': [{'enemyId': 'warden', 'count': 1}, {'enemyId': 'bandit', 'count': 4}, {'enemyId': 'wolf', 'count': 1}]}
    adjustments = [{'when': {}, 'removeEnemyIds': ['bandit'], 'text': 'x'}]

    # Two heroes face at most three enemies.
    assert [group['enemyId'] for group in party_scaling(encounter, party_size=2, adjustments=adjustments)['groups']] == ['warden', 'wolf']
    assert [group['enemyId'] for group in party_scaling(encounter, party_size=2)['groups']] == ['warden', 'bandit']


def test_the_scripted_narrator_tells_the_setup_and_the_adjustments_before_the_first_blows():
    reply = narrate(
        {'live_world_state': {'currentScene': {'name': 'Valley'}}},
        {
            'turn_number': 4,
            'state_pipeline': {
                'stateChangesAlreadyApplied': [{'type': 'combat.start'}],
                'combatState': {'enemyResolvedActions': [{'enemyName': 'Warden', 'targetName': 'Arden', 'abilityName': 'Oathblade', 'attackTotal': 9, 'targetArmorClass': 16, 'hit': False}]},
                'narration': {
                    'fightStart': {'setup': 'The stone pulses beneath dead banners.', 'adjustments': ['You speak his name.', 'The bandits stand aside.']},
                    'enemies': [{'name': 'Warden', 'proper': False}],
                    'nextActorLine': 'It is your turn.',
                },
            },
        },
        'I approach the stone.',
        speaker='Arden',
    )

    paragraphs = reply.split('\n\n')
    assert paragraphs[:3] == ['The stone pulses beneath dead banners.', 'You speak his name.', 'The bandits stand aside.']
    assert 'the warden' in paragraphs[3].lower() and paragraphs[-1] == 'It is your turn.'


# --- 3a. A failed check is never a dead end ----------------------------------------------------------------------


def _bench_state(*, intelligence: int = 10) -> dict:
    state = _state(objects=[BENCH], encounters=[])
    state['playerCharacters'][0] = _actor(intelligence=intelligence)
    return state


def _failed_bench(monkeypatch, state: dict) -> dict:
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 2)
    after = _act(state, 'bench', 'search')['nextState']
    assert after['currentScene']['interactables'][0]['failedCheckActorIds'] == ['player_1']
    return after


def _bench_offer(state: dict) -> dict:
    view = filter_session_snapshot_for_player(state, private_player_ids={1}, acting_player_id=1)
    return next(entry for entry in view['currentScene']['interactables'] if entry['id'] == 'bench')['offeredActions'][0]


def test_after_a_failed_check_the_action_is_offered_again_as_taking_time(monkeypatch):
    failed = _failed_bench(monkeypatch, _bench_state())

    assert _bench_offer(failed) == {'action': 'search', 'label': "Search Driver's Bench", 'legal': True, 'done': False, 'retry': True}
    assert _refusal(failed, 'bench', 'search') == REPEAT_CHECK_MESSAGE


def test_taking_time_resolves_the_check_as_a_20_and_costs_a_phase(monkeypatch):
    failed = _failed_bench(monkeypatch, _bench_state())

    result = _act(failed, 'bench', 'search', turn=6, takeTime=True)
    after = result['nextState']

    action = next(change for change in result['appliedChanges'] if change['type'] == 'scene.interactable.action')
    assert (action['event']['check']['roll'], action['event']['check']['total'], action['event']['check']['outcome']) == (20, 20, 'success')
    assert action['event']['takeTime'] is True
    assert after['clock']['phase'] == 'afternoon'
    assert [item['id'] for item in after['currentScene']['items']] == ['key']
    beats = story_beats({}, {}, actor_id='player_1', state_before=failed, applied_changes=result['appliedChanges'])
    assert beats[0] == {'kind': 'check', 'text': 'Investigation check: 20 — success (taking your time)'}


def test_taking_time_can_fail_again_and_stays_retryable(monkeypatch):
    bench = {**deepcopy(BENCH), 'requirements': {'search': {'check': {'id': 'bench_search', 'dc': 25, 'skill': 'investigation'}}}}
    state = _state(objects=[bench], encounters=[])
    failed = _failed_bench(monkeypatch, state)

    result = _act(failed, 'bench', 'search', turn=6, takeTime=True)
    after = result['nextState']

    assert next(change for change in result['appliedChanges'] if change['type'] == 'scene.interactable.action')['event']['type'].endswith('check_failed')
    assert after['clock']['phase'] == 'afternoon'
    assert _bench_offer(after)['retry'] is True


def test_in_a_fight_a_failed_check_is_not_offered_again(monkeypatch):
    bench = {**deepcopy(BENCH), 'usableInCombat': ['search']}
    failed = _failed_bench(monkeypatch, _state(objects=[bench], encounters=[]))
    failed['combat'] = normalize_combat_state(
        {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [
                {'id': 'player_1', 'playerId': 1, 'name': 'Arden', 'team': 'player', 'kind': 'player_character', 'hp': {'current': 12, 'max': 12}},
                {'id': 'enemy_1', 'name': 'Thug', 'team': 'enemy', 'hp': {'current': 8, 'max': 8}},
            ],
            'initiative': [{'participantId': 'player_1', 'order': 0}, {'participantId': 'enemy_1', 'order': 1}],
            'flags': {},
        },
        failed['currentScene'],
    )

    assert (_bench_offer(failed)['legal'], _bench_offer(failed).get('retry')) == (False, None)
    assert _refusal(failed, 'bench', 'search', takeTime=True) == REPEAT_CHECK_MESSAGE


def test_words_that_name_a_failed_action_take_time_over_it(monkeypatch):
    failed = _failed_bench(monkeypatch, _bench_state())

    mapped = free_text_action(failed, actor_id='player_1', player_id=1, message="I search the driver's bench again")

    assert mapped.intents[0]['take_time'] is True
    assert mapped.intents[0]['object'] == {'id': 'bench', 'action': 'search', 'revision': 0}


def test_the_typed_retry_carries_take_time_at_the_top_of_the_intent():
    intent, error = validate_action_intent(
        {'kind': 'object', 'source': 'story', 'object': {'id': 'bench', 'action': 'search'}, 'take_time': True}
    )
    _bad, bad_error = validate_action_intent({'kind': 'object', 'object': {'id': 'bench', 'action': 'search'}, 'take_time': 'yes'})

    assert error is None and intent['take_time'] is True
    assert bad_error == 'take_time must be true or false.'


def test_the_scripted_narrator_says_the_hero_took_their_time_first():
    reply = narrate(
        {'live_world_state': {'currentScene': {'name': 'Inn'}}},
        {
            'turn_number': 2,
            'state_pipeline': {
                'stateChangesAlreadyApplied': [{'type': 'scene.interactable.action', 'targetId': 'bench', 'objectAction': 'search'}],
                'narration': {'objects': [{'targetId': 'bench', 'name': "Driver's Bench", 'action': 'search', 'label': "Search Driver's Bench", 'takeTime': True, 'found': ['a Strongbox Key']}]},
            },
        },
        "I search the driver's bench",
        speaker='Arden',
    )

    assert reply.startswith('You take your time over it; the light has changed by the time you are done.')
    assert 'You find a Strongbox Key.' in reply


def test_the_offered_retry_is_available_through_the_rules_api(monkeypatch):
    failed = _failed_bench(monkeypatch, _bench_state())

    options = available_actions_for_target(failed, location_id='cairns', target_id='bench', actor={'id': 'player_1'})['actions']

    assert options == [{'action': 'search', 'legal': True, 'code': 'retry', 'retry': True}]


def test_a_settled_encounter_is_not_settled_again_after_its_fight_was_won():
    state = _state(flags={'oath_learned': True, 'campaignPackCompletedEncounterIds': ['enc_judgment'], TRIGGERED_ENCOUNTERS_FLAG: ['enc_judgment']})

    result = _act(state, 'banner', 'use')

    assert not any(change['type'] in {'encounter.resolve', 'combat.end'} for change in result['appliedChanges'])
    assert _rewards(result['nextState']) == []
