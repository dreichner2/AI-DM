"""Less waiting: End turn costs no reply of its own, an enemy phase is one reply, the turn ends itself when only
moving is left, and every turn records how long it kept the table waiting.

Through the real socket pipeline with the recorded AI narrator (``tests/ai_narrator.py``), seeded dice (heroes roll
15 to hit and their highest damage, enemies 12), on the Play Now adventure for two heroes: the caravan cutthroat's
fight (Thalion, then Corin Ashby, then the cutthroat) and the Old North Road's (the wardbound beast and the pale
lantern, then Arden Vale, then Seren Ashgrove).
"""

from __future__ import annotations

from typing import Any

import pytest

from aidm_server.database import db
from aidm_server.game_state.orchestration import turn_pipeline
from aidm_server.game_state.orchestration.turn_flow import (
    TOLD_BY_NARRATOR,
    TOLD_SCRIPTED,
    TOLD_TURN_LINE,
    telling,
    time_to_act_rows,
    turn_timings,
)
from aidm_server.models import DmTurn, Session, SessionLogEntry, safe_json_dumps, safe_json_loads
from tests.ai_narrator import ENGINE_BRIEF_RE, ai_narration
from tests.test_fight_maneuvers import FightTable, errors
from tests.test_party_play import PartyTable

NARRATION_EVENTS = {'dm_response_start', 'dm_chunk', 'dm_response_end'}
TIMING_KEYS = {'engine_ms', 'narration_ms', 'settle_ms', 'model_called', 'kind', 'time_to_act_ms'}


class _Choosing:
    """A table whose players send their ``End my turn for me`` choice (``auto_end``) with every action."""

    auto_end = False

    def send(self, hero: str, message: str, intent: dict | None = None) -> list[dict]:
        self.sent += 1
        message_id = f'flow-{self.sent}'
        self.sockets[hero].emit(
            'send_message',
            {
                'session_id': self.ids['session_id'],
                'campaign_id': self.ids['campaign_id'],
                'player_id': self.players[hero],
                'message': message,
                'client_message_id': message_id,
                'auto_end_turn': self.auto_end,
                **({'action_intent': {**intent, 'text': message, 'client_message_id': message_id}} if intent else {}),
            },
        )
        received = self.sockets[hero].get_received()
        self.drain()
        return received


class Fight(_Choosing, FightTable):
    pass


class Party(_Choosing, PartyTable):
    pass


def _seeded(monkeypatch) -> None:
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 14 if sides == 20 else sides - 1)
    monkeypatch.setattr('aidm_server.combat.actions.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda sides: min(sides, 12))


@pytest.fixture()
def fight(app, client, socketio, monkeypatch):
    narrator = ai_narration(app, monkeypatch)
    _seeded(monkeypatch)
    table = Fight(app, client, socketio, monkeypatch)
    table.narrator = narrator
    table.steady_enemy()
    yield table
    table.close()


@pytest.fixture()
def road(app, client, socketio, monkeypatch):
    """Arden Vale and Seren Ashgrove on the Old North Road, the fight its enemies opened handed to Arden."""

    narrator = ai_narration(app, monkeypatch)
    _seeded(monkeypatch)
    table = Party(app, client, socketio, party=True)
    table.narrator = narrator
    narrator.replies.append('Pale lights rise between the stones and something heavy moves in the rain.')
    table.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    assert table.acting_hero() == 'Arden Vale'
    yield table
    table.close()


def _turn(table) -> DmTurn:
    with table.app.app_context():
        turn = DmTurn.query.filter_by(session_id=table.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
        db.session.expunge(turn)
        return turn


def _metadata(table) -> dict[str, Any]:
    return safe_json_loads(_turn(table).metadata_json, {})


def _calls(table, play) -> int:
    """The narrator calls ``play`` made."""

    before = len(table.narrator.prompts)
    play()
    return len(table.narrator.prompts) - before


def _brief_did(table) -> str:
    brief = ENGINE_BRIEF_RE.search(table.narrator.prompts[-1]).group('brief')
    return next(line for line in brief.splitlines() if line.startswith('Did: '))


def _hud(table, hero: str, action_id: str, target_id: str | None = None, reply: str | None = None) -> list[dict]:
    if reply is not None:
        table.narrator.replies.append(reply)
    combat = {'action_id': action_id, **({'target_id': target_id} if target_id else {})}
    received = table.send(hero, f'{hero} acts.', {'kind': 'combat', 'source': 'combat_hud', 'combat': combat})
    assert errors(received) == []
    return received


def _enemy_id(table, name: str) -> str:
    return next(participant['id'] for participant in table.snapshot()['combat']['participants'] if participant['name'] == name)


def _first_attack(table, hero: str) -> tuple[str, str]:
    attack = next(action for action in table.actions(hero) if action['id'].startswith('combat.attack.') and action['available'])
    return attack['id'], next(target['id'] for target in attack['targets'] if target['available'])


# A. Ending a turn costs no reply of its own -------------------------------------------------------------------------


def test_ending_a_turn_with_a_hero_next_is_the_turn_line_alone_and_offers_the_next_heros_actions(fight):
    _hud(fight, 'Thalion', 'combat.attack.starter_ranger_longbow', fight.enemy()['id'], reply='Thalion looses; the shaft hums past the wagon.')
    received: list[dict] = []

    calls = _calls(fight, lambda: received.extend(_hud(fight, 'Thalion', 'combat.end_turn')))

    assert calls == 0
    assert not NARRATION_EVENTS & {event['name'] for event in received}
    turn = _turn(fight)
    assert (turn.dm_output, turn.llm_provider) == ('Corin Ashby is up next.', 'fallback')
    metadata = safe_json_loads(turn.metadata_json, {})
    assert metadata['turnFlow'] == {'told': TOLD_TURN_LINE}
    assert metadata['state_pipeline']['postDmExtraction']['debug']['reason'] == 'engine_told'
    with fight.app.app_context():
        logged = [
            entry.message
            for entry in SessionLogEntry.query.filter_by(session_id=fight.ids['session_id'], entry_type='dm').all()
            if safe_json_loads(entry.metadata_json, {}).get('turn_id') == turn.turn_id
        ]
    assert logged == ['DM: Corin Ashby is up next.']
    assert fight.acting_hero() == 'Corin Ashby'
    assert fight.actions('Corin Ashby')['combat.attack.starter_paladin_longsword']['available'] is True


def test_the_enemy_phase_is_one_reply_naming_every_enemy_then_the_turn_line(road):
    assert _calls(road, lambda: _hud(road, 'Arden Vale', 'combat.end_turn')) == 0
    assert _turn(road).dm_output == 'Seren Ashgrove is up next.'

    reply = 'The beast drives in low while the lantern drifts closer, singing.'
    calls = _calls(road, lambda: _hud(road, 'Seren Ashgrove', 'combat.end_turn', reply=reply))

    assert calls == 1
    did = _brief_did(road)
    assert 'Wardbound Beast' in did and 'Pale Lantern' in did
    resolved = _metadata(road)['state_pipeline']['dmContextPacket']['combatState']['enemyResolvedActions']
    assert sorted(action['enemyName'] for action in resolved) == ['Pale Lantern', 'Wardbound Beast']
    assert _turn(road).dm_output == f'{reply}\n\nArden Vale is up next.'
    assert road.acting_hero() == 'Arden Vale'


def test_the_scripted_narrator_tells_an_enemy_phase_one_sentence_per_enemy(app, client, socketio, monkeypatch):
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    _seeded(monkeypatch)
    table = Party(app, client, socketio, party=True)
    try:
        table.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
        _hud(table, 'Arden Vale', 'combat.end_turn')
        _hud(table, 'Seren Ashgrove', 'combat.end_turn')
        narration = _turn(table).dm_output
    finally:
        table.close()

    assert narration.endswith('\n\nArden Vale is up next.')
    assert 'wardbound beast' in narration and 'lantern' in narration


@pytest.mark.parametrize(
    ('action_id', 'told'),
    [('combat.dodge', TOLD_SCRIPTED), ('combat.disengage', TOLD_SCRIPTED), ('combat.reposition', TOLD_SCRIPTED)],
)
def test_a_fight_action_with_nothing_to_tell_beyond_its_result_makes_no_model_call(fight, action_id, told):
    received: list[dict] = []

    calls = _calls(fight, lambda: received.extend(_hud(fight, 'Thalion', action_id)))

    assert calls == 0
    turn = _turn(fight)
    assert (turn.llm_provider, turn.llm_model) == ('fallback', 'deterministic-v1')
    assert safe_json_loads(turn.metadata_json, {})['turnFlow'] == {'told': told}
    # It is told as Safe Mode tells it: a reply of its own, ending on whose turn it is.
    assert 'dm_response_end' in {event['name'] for event in received}
    assert turn.dm_output.endswith("\n\nIt is still Thalion's turn.")
    assert safe_json_loads(turn.metadata_json, {})['state_pipeline']['postDmExtraction']['debug']['reason'] == 'engine_told'


def test_a_turn_holding_an_attack_speech_or_an_enemy_phase_is_the_narrators():
    def hint(**combat_state: Any) -> dict:
        return {'requires_roll': False, 'state_pipeline': {'combatState': {'status': 'active', **combat_state}, 'narration': {}}}

    dodge = {'kind': 'combat', 'combat': {'action_type': 'dodge'}}
    assert telling(hint(), dodge, player_input='', speaker='Thalion')[0] == TOLD_SCRIPTED
    assert telling(hint(enemyResolvedActions=[{'enemyId': 'enemy_1'}]), dodge, player_input='', speaker='Thalion')[0] == TOLD_BY_NARRATOR
    assert telling(hint(), {'kind': 'combat', 'combat': {'action_type': 'grapple'}}, player_input='', speaker='Thalion')[0] == TOLD_BY_NARRATOR
    assert telling(hint(), {'kind': 'message', 'source': 'fight_words', 'text': 'Yield!'}, player_input='', speaker='Thalion')[0] == TOLD_BY_NARRATOR
    assert telling(hint(), {'kind': 'spell'}, player_input='', speaker='Thalion')[0] == TOLD_BY_NARRATOR
    # Outside a fight the narrator tells every turn.
    assert telling({'requires_roll': False, 'state_pipeline': {}}, dodge, player_input='', speaker='Thalion')[0] == TOLD_BY_NARRATOR
    turn_line = hint()
    turn_line['state_pipeline']['narration'] = {'nextActorLine': 'Corin Ashby is up next.'}
    assert telling(turn_line, {'kind': 'combat', 'combat': {'action_type': 'end_turn'}}, player_input='', speaker='Thalion') == (
        TOLD_TURN_LINE,
        'Corin Ashby is up next.',
    )


# B. The turn ends itself when nothing is left to do -----------------------------------------------------------------


def test_an_attack_that_leaves_only_moving_ends_the_turn_and_the_enemy_phase_is_in_the_same_reply(fight):
    _hud(fight, 'Thalion', 'combat.end_turn')
    fight.auto_end = True
    reply = 'Corin\'s blade bites; the cutthroat answers with a knife that scrapes his mail.'

    calls = _calls(fight, lambda: _hud(fight, 'Corin Ashby', 'combat.attack.starter_paladin_longsword', fight.enemy()['id'], reply=reply))

    assert calls == 1
    did = _brief_did(fight)
    assert 'Corin Ashby attacks Ashmarked Cutthroat' in did
    assert 'Ashmarked Cutthroat uses Ashmark Feint on Corin Ashby' in did
    metadata = _metadata(fight)
    assert metadata['state_pipeline']['autoEndTurn'] is True
    # The hero's roll card and the one the enemy's turn forced arrive with the one reply.
    assert [(roll['kind'], roll['actor']['name'], roll.get('source')) for roll in metadata['storyRolls']] == [
        ('attack', 'Corin Ashby', None),
        ('save', 'Corin Ashby', 'Ashmark Feint'),
    ]
    assert _turn(fight).dm_output == f'{reply}\n\nThalion is up next.'
    assert fight.acting_hero() == 'Thalion'


def test_an_attack_sent_in_the_apps_own_words_ends_the_turn_too(fight):
    """The app sends a fight action with the sentence it shows the players. Those words read to the declared-action
    check as an attack still waiting on its roll; the turn carries that roll, so it ends itself all the same."""

    _hud(fight, 'Thalion', 'combat.end_turn')
    fight.auto_end = True
    fight.narrator.replies.append('Corin\'s blade bites.')
    message = 'Corin Ashby attacks Ashmarked Cutthroat with Longsword.'
    combat = {'action_id': 'combat.attack.starter_paladin_longsword', 'target_id': fight.enemy()['id']}

    received = fight.send('Corin Ashby', message, {'kind': 'combat', 'source': 'combat_hud', 'combat': combat})

    assert errors(received) == []
    metadata = _metadata(fight)
    assert metadata['action_intent']['kind'] == 'roll'
    pipeline = metadata['state_pipeline']
    # No model is asked what a clicked fight action meant, nor what its narration changed.
    assert pipeline['preDmExtraction']['debug']['reason'] == 'engine_fight_action'
    assert pipeline['postDmExtraction']['debug']['reason'] == 'engine_fight_action'
    assert metadata['state_pipeline']['autoEndTurn'] is True
    assert fight.acting_hero() == 'Thalion'


def test_with_the_choice_off_an_attack_leaves_the_turn_open_as_today(fight):
    _hud(fight, 'Thalion', 'combat.end_turn')

    _hud(fight, 'Corin Ashby', 'combat.attack.starter_paladin_longsword', fight.enemy()['id'], reply='Corin cuts at the cutthroat.')

    metadata = _metadata(fight)
    assert 'autoEndTurn' not in metadata['state_pipeline']
    assert metadata['state_pipeline']['dmContextPacket']['combatState']['enemyResolvedActions'] == []
    assert _turn(fight).dm_output.endswith("\n\nIt is still Corin Ashby's turn.")
    assert fight.acting_hero() == 'Corin Ashby'


def test_a_hero_with_second_wind_left_is_not_auto_ended(road):
    road.operator([{'type': 'health.damage', 'actorId': road.hero_id('Arden Vale'), 'amount': 4}])
    assert next(action for action in road.actions('Arden Vale') if action['id'] == 'class.use.second_wind')['available'] is True
    road.auto_end = True
    action_id, target_id = _first_attack(road, 'Arden Vale')

    _hud(road, 'Arden Vale', action_id, target_id, reply='Arden swings at the lights.')

    assert 'autoEndTurn' not in _metadata(road)['state_pipeline']
    assert road.acting_hero() == 'Arden Vale'


def test_a_potion_nobody_needs_does_not_keep_the_turn_open(fight):
    assert fight.actions('Thalion')['item.use.starter_ranger_potion_healing']['available'] is True
    fight.auto_end = True

    _hud(fight, 'Thalion', 'combat.attack.starter_ranger_longbow', fight.enemy()['id'], reply='Thalion shoots.')

    assert _metadata(fight)['state_pipeline']['autoEndTurn'] is True
    assert fight.acting_hero() == 'Corin Ashby'
    assert _turn(fight).dm_output == 'Thalion shoots.\n\nCorin Ashby is up next.'


def test_the_finishing_blow_question_and_the_roll_cards_arrive_on_a_turn_that_ended_itself(road):
    _hud(road, 'Arden Vale', 'combat.end_turn')
    road.operator([{'type': 'health.heal', 'actorId': road.hero_id('Seren Ashgrove'), 'amount': 20}])
    snapshot = road.snapshot()
    beast = next(participant for participant in snapshot['combat']['participants'] if participant['name'] == 'Wardbound Beast')
    beast.update({'proper': True, 'hp': {**beast['hp'], 'current': 1}})
    with road.app.app_context():
        db.session.get(Session, road.ids['session_id']).state_snapshot = safe_json_dumps(snapshot, {})
        db.session.commit()
    road.auto_end = True
    road.narrator.replies.append('Light lances from Seren\'s hand into the beast; the lantern swings toward her.')
    # A levelled spell leaves no bonus-action spell legal this turn, and nobody needs healing.
    intent = {
        'kind': 'spell',
        'source': 'scene_panel',
        'spell': {'name': 'Guiding Bolt', 'effect': 'Resolve the server-owned spell effect.', 'resource_pool': 'auto', 'target_ids': [beast['id']]},
    }
    assert errors(road.send('Seren Ashgrove', 'Seren Ashgrove casts Guiding Bolt at Wardbound Beast.', intent)) == []

    metadata = _metadata(road)
    assert metadata['state_pipeline']['autoEndTurn'] is True
    assert road.snapshot()['combat']['flags']['pendingFinisher']['enemyName'] == 'Wardbound Beast'
    assert _turn(road).dm_output.endswith('\n\nArden Vale is up next.\n\nWardbound Beast is finished. Seren Ashgrove, how do you end it?')
    assert {roll['actor']['name'] for roll in metadata['storyRolls']} >= {'Seren Ashgrove', 'Pale Lantern'}


def test_a_turn_waiting_on_a_roll_does_not_end_itself_until_the_roll_resolves(monkeypatch):
    asked: list[dict] = []
    monkeypatch.setattr(turn_pipeline, 'turn_ends_itself', lambda **kwargs: asked.append(kwargs['action_intent']) or True)
    asking = {'auto_end_turn': True}
    awaiting = DmTurn(requires_roll=True, roll_value=None, metadata_json=safe_json_dumps(asking, {}))
    rolled = DmTurn(requires_roll=True, roll_value=14, metadata_json=safe_json_dumps(asking, {}))
    common = {
        'state': {'combat': {'status': 'active', 'round': 1, 'participants': []}},
        'player': None,
        'actor_id': 'player_1',
        'pre_validation': {},
        'combat_debug': {},
    }

    assert turn_pipeline._turn_ends_itself(turn=awaiting, action_intent={'kind': 'combat', 'combat': {'action_type': 'dodge'}}, **common) is False
    assert turn_pipeline._turn_ends_itself(turn=rolled, action_intent={'kind': 'roll'}, **common) is True
    assert asked == [{'kind': 'roll'}]


# D. Seeing where the time goes --------------------------------------------------------------------------------------


def test_every_finished_turn_records_its_timings_and_telemetry_sums_them_by_kind(fight, monkeypatch):
    recorded: list[tuple[str, float, dict]] = []
    monkeypatch.setattr('aidm_server.turn_engine.telemetry_timing', lambda name, value, tags=None: recorded.append((name, value, tags or {})))

    _hud(fight, 'Thalion', 'combat.attack.starter_ranger_longbow', fight.enemy()['id'], reply='Thalion shoots.')
    attack = _metadata(fight)['timings']
    _hud(fight, 'Thalion', 'combat.end_turn')
    end_turn = _metadata(fight)['timings']
    _hud(fight, 'Corin Ashby', 'combat.end_turn', reply='The cutthroat lunges at Thalion.')
    enemy_phase = _metadata(fight)['timings']

    assert set(attack) == set(end_turn) == set(enemy_phase) == TIMING_KEYS
    assert [timings['kind'] for timings in (attack, end_turn, enemy_phase)] == ['fight_action', 'end_turn', 'enemy_phase']
    assert (attack['model_called'], end_turn['model_called'], enemy_phase['model_called']) == (True, False, True)
    assert end_turn['narration_ms'] == 0
    for timings in (attack, end_turn, enemy_phase):
        assert timings['time_to_act_ms'] >= timings['engine_ms'] + timings['narration_ms'] + timings['settle_ms'] - 1
    acted = [(tags['kind'], value) for name, value, tags in recorded if name == 'turn.time_to_act_ms']
    assert [kind for kind, _value in acted] == ['fight_action', 'end_turn', 'enemy_phase']


def test_ops_reads_the_median_and_ninetieth_percentile_time_to_act_by_kind(fight):
    _hud(fight, 'Thalion', 'combat.attack.starter_ranger_longbow', fight.enemy()['id'], reply='Thalion shoots.')
    _hud(fight, 'Thalion', 'combat.end_turn')

    response = fight.client.get(f"/api/beta/session-quality?session_id={fight.ids['session_id']}")

    assert response.status_code == 200, response.get_json()
    rows = response.get_json()['time_to_act']
    assert [(row['kind'], row['label'], row['turn_count']) for row in rows] == [('fight_action', 'Fight action', 1), ('end_turn', 'End turn', 1)]
    assert all(isinstance(row['median_ms'], int) and row['p90_ms'] >= row['median_ms'] for row in rows)


def test_time_to_act_rows_are_the_median_and_nearest_rank_ninetieth_percentile():
    timings = [{'kind': 'talk', 'time_to_act_ms': value} for value in (100, 200, 300, 400, 5000)]
    timings += [{'kind': 'end_turn', 'time_to_act_ms': 40}, {'kind': 'explore'}, 'not a record']

    assert time_to_act_rows(timings) == [
        {'kind': 'talk', 'label': 'Talk', 'turn_count': 5, 'median_ms': 300, 'p90_ms': 5000},
        {'kind': 'end_turn', 'label': 'End turn', 'turn_count': 1, 'median_ms': 40, 'p90_ms': 40},
    ]
    assert turn_timings(
        submitted_at=10.0, engine_done_at=10.2, narration_ms=1500, model_called=False, narration_ended_at=10.25, finished_at=10.4, kind='end_turn'
    ) == {'engine_ms': 200, 'narration_ms': 0, 'settle_ms': 150, 'model_called': False, 'kind': 'end_turn', 'time_to_act_ms': 400}


# The engine runs a pack adventure's table ---------------------------------------------------------------------------


def test_no_model_is_asked_who_may_act_in_a_pack_adventure(fight, monkeypatch):
    """Two players at one table used to cost every submission a call to the table-flow conductor."""

    from aidm_server import turn_control

    fight.app.config['AIDM_TURN_CONDUCTOR_HELPER_IN_TESTS'] = True
    fight.app.config['AIDM_LLM_PROVIDER'] = 'deepseek'
    asked: list[str] = []
    monkeypatch.setattr(turn_control, '_ai_turn_conductor_decision', lambda **kwargs: asked.append(kwargs['message']))

    _hud(fight, 'Thalion', 'combat.end_turn')
    fight.narrator.replies.append('Corin calls across the yard.')
    received = fight.send('Corin Ashby', 'I shout at the cutthroat to drop the knife.')

    assert errors(received) == []
    assert asked == []


def test_no_model_picks_an_enemys_move_in_a_pack_fight_unless_the_table_asks():
    from aidm_server.combat.difficulty import ENEMY_TACTICS_MODEL_SETTINGS, normalize_combat_difficulty_ai, pack_combat_difficulty_ai

    quiet = pack_combat_difficulty_ai(None)
    assert [quiet[key] for key in ENEMY_TACTICS_MODEL_SETTINGS] == [False] * len(ENEMY_TACTICS_MODEL_SETTINGS)
    assert quiet['maxLlmCallsPerRound'] == 0
    # Everything else about the fight's difficulty is as the table set it.
    assert {key: value for key, value in quiet.items() if key not in {*ENEMY_TACTICS_MODEL_SETTINGS, 'maxLlmCallsPerRound'}} == {
        key: value for key, value in normalize_combat_difficulty_ai(None).items() if key not in {*ENEMY_TACTICS_MODEL_SETTINGS, 'maxLlmCallsPerRound'}
    }
    asked_for = pack_combat_difficulty_ai({'enemyTacticsModel': True, 'tacticalLevel': 'smart'})
    assert asked_for == normalize_combat_difficulty_ai({'tacticalLevel': 'smart'})


def test_a_pack_fight_starts_with_the_engines_own_enemy_tactics(road):
    difficulty = road.snapshot()['combat']['flags']['combatDifficultyAI']

    assert (difficulty['allowBossWarmPlanner'], difficulty['allowSentientEnemyBrain'], difficulty['maxLlmCallsPerRound']) == (False, False, 0)
    # A boss still leads with its signature ability: the engine scores that candidate itself.
    assert difficulty['allowBossTacticsHelper'] is True


def test_with_no_model_calls_allowed_a_boss_tactic_is_the_engines_own(monkeypatch):
    from aidm_server.combat import boss_tactics

    def no_provider(**_kwargs):
        raise AssertionError('no model may be asked for an enemy tactic')

    monkeypatch.setattr(boss_tactics, 'get_helper_provider', no_provider)
    monkeypatch.setattr(boss_tactics, 'boss_tactics_helper_enabled', lambda: True)
    monkeypatch.setattr(boss_tactics, 'boss_tactics_planner_enabled', lambda: True)
    settings = {'allowBossWarmPlanner': True, 'maxLlmCallsPerRound': 0}
    fallback = {'intentType': 'use_ability', 'abilityId': 'lure'}

    assert boss_tactics.plan_boss_advisory({}, {}, settings, candidates=[]) == (None, 'disabled')
    chosen, source = boss_tactics.plan_boss_candidate_tactic({}, {}, settings, fallback_intent=fallback, candidates=[])
    assert (chosen['abilityId'], source) == ('lure', 'deterministic')
