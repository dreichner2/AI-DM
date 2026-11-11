"""Your hero in the story: tie lines and Inspiration, through the real socket pipeline on the Play Now adventure.

Safe Mode tables (scripted narrator) with Arden Vale, whose sister Edda Vale is the one he would not abandon, and
heroes created at the table; the fight tests use the caravan cutthroat's fight (Thalion, then Corin Ashby) narrated
by the recorded AI narrator (``tests/ai_narrator.py``).
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from aidm_server.database import db
from aidm_server.game_state.orchestration.hero_threads import (
    INSPIRED_EFFECT_ID,
    MERCY_REASON,
    TAKE_HEART_ACTION_ID,
    THREAD_CHANGE,
    hero_turn_changes,
)
from aidm_server.models import Player, Session, safe_json_dumps, safe_json_loads
from tests.ai_narrator import AiTable, ai_narration
from tests.test_fight_maneuvers import errors
from tests.test_less_waiting import Fight, _calls, _hud, _metadata, _seeded, _turn
from tests.test_party_play import CLERIC, PartyTable

LEDGER_LINE = 'One name on the last page is not quite gone. Under the black stroke you can still read it: {name}.'
WALL_LINE = 'Among the fresh chisel-scars one name has been cut away twice, as if once was not enough: {name}.'
BURDEN_LINE = "Sleep does not come at once. You turn it over again in the dark: The road-warden's oath you swore and never finished."
RENEWED_TEXT = 'Edda Vale is among those who walk out of the valley at dawn: thinner, blinking, alive.'
BROKEN_TEXT = 'Edda Vale walks out with the others, and does not remember the road at all. Perhaps that is a mercy.'
SERENS_TIES = {
    'desire': {'kind': 'find_someone'},
    'attachment': {'name': 'Tamsin Ashgrove', 'relation': 'mother'},
    'burden': 'A temple bell you rang the night the fire spread.',
}
NOVICE = {**CLERIC, 'character_name': 'Wren Hollis'}
THREAD_KINDS = {'thread', 'inspiration'}


class Table(PartyTable):
    """The Play Now table, with more heroes joining as the tests need them."""

    def join(self, socketio, hero: dict[str, Any]) -> None:
        created = self.client.post(f"/api/players/campaigns/{self.ids['campaign_id']}/players", json=hero)
        assert created.status_code == 201, created.get_json()
        name = hero['character_name']
        self.players[name] = created.get_json()['player_id']
        self.sockets[name] = socketio.test_client(self.app, flask_test_client=self.app.test_client())
        self.sockets[name].emit(
            'join_session', {'session_id': self.ids['session_id'], 'player_id': self.players[name], 'workspace_id': self.ids['workspace_id']}
        )
        self.drain()

    def act(self, hero: str, target_id: str, action: str, message: str) -> dict[str, Any]:
        received = self.send(hero, message, {'kind': 'object', 'source': 'scene_panel', 'object': {'id': target_id, 'action': action}})
        assert errors(received) == []
        return self.last()

    def last(self) -> dict[str, Any]:
        return self.turns_since(self.last_turn_id() - 1)[-1]

    def view(self, hero: str) -> dict[str, Any]:
        response = self.client.get(
            f"/api/sessions/{self.ids['session_id']}/state",
            query_string={'view': 'player', 'player_id': self.players[hero]},
        )
        assert response.status_code == 200, response.get_json()
        return response.get_json()['state_snapshot']

    def set_ties(self, hero: str, ties: dict[str, Any] | None) -> None:
        response = self.client.patch(f'/api/players/{self.players[hero]}', json={'ties': ties})
        assert response.status_code == 200, response.get_json()

    def go(self, location_id: str) -> None:
        self.operator([{'id': f'move-{location_id}', 'type': 'scene.move_location', 'locationId': location_id, 'reason': 'Test setup.'}])

    def edit_snapshot(self, edit) -> None:
        with self.app.app_context():
            session_obj = db.session.get(Session, self.ids['session_id'])
            snapshot = safe_json_loads(session_obj.state_snapshot, {})
            edit(snapshot)
            session_obj.state_snapshot = safe_json_dumps(snapshot, {})
            db.session.commit()


def _hero_beats(turn: dict[str, Any]) -> list[str]:
    return [beat['text'] for beat in turn['beats'] if beat['kind'] in THREAD_KINDS]


def _check(turn: dict[str, Any], target_id: str) -> dict[str, Any]:
    change = next(
        change for change in turn['pipeline']['immediateAppliedChanges'] if change['type'] == 'scene.interactable.action' and change['targetId'] == target_id
    )
    return change['event']['check']


def _inspired(record: dict[str, Any]) -> bool:
    return any(effect.get('id') == INSPIRED_EFFECT_ID for effect in record.get('activeEffects') or [])


def _safe_mode(app, monkeypatch) -> None:
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    # Seeded dice: object checks roll 15, the heroes 15 to hit and their highest damage, enemies 12.
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 15)
    _seeded(monkeypatch)


@pytest.fixture()
def solo(app, client, socketio, monkeypatch):
    _safe_mode(app, monkeypatch)
    table = Table(app, client, socketio, party=False)
    yield table
    table.close()


@pytest.fixture()
def party(app, client, socketio, monkeypatch):
    _safe_mode(app, monkeypatch)
    table = Table(app, client, socketio, party=True)
    table.set_ties('Seren Ashgrove', SERENS_TIES)
    yield table
    table.close()


# Tie lines ----------------------------------------------------------------------------------------------------------


def test_arden_reads_the_ledger_and_finds_his_sisters_name(solo):
    turn = solo.act('Arden Vale', 'inn_caravan_ledger', 'inspect', 'Read the caravan ledger.')

    line = LEDGER_LINE.format(name='Edda Vale')
    assert line in turn['narration'].split('\n\n')
    assert _hero_beats(turn) == ['A thread of your own — Edda Vale', 'Inspiration — a thread of your own']
    assert solo.hero('Arden Vale')['inspiration'] is True
    assert solo.stats('Arden Vale')['inspiration'] is True
    assert solo.view('Arden Vale')['journal']['threads'] == [
        {'id': 'tie_ledger_name', 'title': 'A thread of your own — Edda Vale', 'text': line}
    ]


def test_a_hero_without_ties_hears_no_line_and_gains_nothing(solo):
    solo.set_ties('Arden Vale', None)

    turn = solo.act('Arden Vale', 'inn_caravan_ledger', 'inspect', 'Read the caravan ledger.')

    assert 'Under the black stroke' not in turn['narration']
    assert _hero_beats(turn) == []
    assert 'inspiration' not in solo.hero('Arden Vale')
    assert solo.view('Arden Vale')['journal']['threads'] == []
    assert solo.snapshot()['campaignPack'].get('threads') is None


def test_an_attachment_that_is_not_a_person_hears_no_seeking_line(solo):
    solo.set_ties('Arden Vale', {'attachment': {'name': 'The Lantern Post', 'relation': 'a place'}})

    turn = solo.act('Arden Vale', 'inn_caravan_ledger', 'inspect', 'Read the caravan ledger.')

    assert 'Under the black stroke' not in turn['narration']
    assert _hero_beats(turn) == []


def test_the_hero_who_acts_carries_the_line_and_the_memorial_wall_names_theirs(party):
    ledger = party.act('Seren Ashgrove', 'inn_caravan_ledger', 'inspect', 'Read the caravan ledger.')
    party.go('loc_waystation_scratched_names')
    wall = party.act('Arden Vale', 'ws_memorial_wall', 'inspect', 'Look over the memorial wall.')

    assert LEDGER_LINE.format(name='Tamsin Ashgrove') in ledger['narration'].split('\n\n')
    assert _hero_beats(ledger) == ['Seren Ashgrove — a thread of their own — Tamsin Ashgrove', 'Seren Ashgrove — Inspiration — a thread of their own']
    assert WALL_LINE.format(name='Edda Vale') in wall['narration'].split('\n\n')
    assert _hero_beats(wall) == ['Arden Vale — a thread of their own — Edda Vale', 'Arden Vale — Inspiration — a thread of their own']
    assert [thread['id'] for thread in party.view('Seren Ashgrove')['journal']['threads']] == ['tie_ledger_name']
    assert [thread['id'] for thread in party.view('Arden Vale')['journal']['threads']] == ['tie_memorial_name']


def test_two_heroes_with_ties_each_carry_one_of_two_lines_a_third_hero_brings_due(party, socketio):
    party.join(socketio, NOVICE)

    ledger = party.act('Wren Hollis', 'inn_caravan_ledger', 'inspect', 'Read the caravan ledger.')
    party.go('loc_waystation_scratched_names')
    wall = party.act('Wren Hollis', 'ws_memorial_wall', 'inspect', 'Look over the memorial wall.')

    # Neither carrier acted, so the line goes to the hero with the fewest lines told, the earliest first.
    assert LEDGER_LINE.format(name='Edda Vale') in ledger['narration'].split('\n\n')
    assert WALL_LINE.format(name='Tamsin Ashgrove') in wall['narration'].split('\n\n')
    assert [(record['lineId'], record['heroId']) for record in party.snapshot()['campaignPack']['threads']] == [
        ('tie_ledger_name', party.hero_id('Arden Vale')),
        ('tie_memorial_name', party.hero_id('Seren Ashgrove')),
    ]
    assert party.hero('Arden Vale')['inspiration'] is True
    assert party.hero('Seren Ashgrove')['inspiration'] is True
    assert 'inspiration' not in party.hero('Wren Hollis')


def test_the_burden_line_comes_with_a_long_rest_once(solo):
    solo.go('loc_old_masons_yard')
    rest = {'kind': 'rest', 'source': 'scene_panel', 'rest_type': 'long_rest'}

    assert errors(solo.send('Arden Vale', 'Arden Vale completes a long rest.', rest)) == []
    first = solo.last()
    solo.send('Arden Vale', 'Arden Vale completes a long rest.', rest)
    second = solo.last()

    assert BURDEN_LINE in first['narration'].split('\n\n')
    assert _hero_beats(first) == ['A thread of your own', 'Inspiration — a thread of your own']
    assert 'Sleep does not come at once' not in second['narration']
    assert [record['lineId'] for record in solo.snapshot()['campaignPack']['threads']] == ['tie_burden_first_rest']


def _reach_ending(table: Table, flag_key: str) -> None:
    def edit(snapshot: dict[str, Any]) -> None:
        snapshot['flags'][flag_key] = True
        snapshot['flags']['campaignPackActiveCheckpointId'] = 'cp_07_road_remembers'
        snapshot['campaignPack']['activeCheckpointId'] = 'cp_07_road_remembers'

    table.edit_snapshot(edit)


def test_the_ending_tells_of_the_one_the_hero_came_for_only_after_their_line_fired(solo):
    variants = {
        variant['when']['flagKey']: variant['text']
        for checkpoint in solo.snapshot()['campaignPack']['checkpoints']
        for variant in checkpoint.get('endingVariants') or []
    }
    _reach_ending(solo, 'ward_renewed')
    assert solo.view('Arden Vale')['campaignPack']['ending']['text'] == variants['ward_renewed']

    solo.edit_snapshot(lambda snapshot: snapshot['campaignPack'].update(activeCheckpointId='cp_01_empty_caravan'))
    solo.edit_snapshot(lambda snapshot: snapshot['flags'].update(campaignPackActiveCheckpointId='cp_01_empty_caravan'))
    solo.act('Arden Vale', 'inn_caravan_ledger', 'inspect', 'Read the caravan ledger.')
    _reach_ending(solo, 'ward_renewed')
    assert solo.view('Arden Vale')['campaignPack']['ending']['text'] == f"{variants['ward_renewed']} {RENEWED_TEXT}"
    # The journal tells the story's end in the same words.
    assert solo.view('Arden Vale')['journal']['quest']['stageSummary'] == f"{variants['ward_renewed']} {RENEWED_TEXT}"

    solo.edit_snapshot(lambda snapshot: snapshot['flags'].update(ward_renewed=False, ward_broken=True))
    assert solo.view('Arden Vale')['campaignPack']['ending']['text'] == f"{variants['ward_broken']} {BROKEN_TEXT}"


def test_a_player_view_shows_nothing_of_another_heros_ties(party):
    party.act('Arden Vale', 'inn_caravan_ledger', 'inspect', 'Read the caravan ledger.')

    view = party.view('Seren Ashgrove')
    arden = next(hero for hero in view['playerCharacters'] if hero['name'] == 'Arden Vale')
    seren = next(hero for hero in view['playerCharacters'] if hero['name'] == 'Seren Ashgrove')
    assert 'ties' not in arden
    assert arden['inspiration'] is True
    assert seren['ties']['attachment'] == {'name': 'Tamsin Ashgrove', 'relation': 'mother'}
    assert view['journal']['threads'] == []
    assert 'threads' not in view['campaignPack']
    assert 'tieLines' not in view['campaignPack']
    assert 'Edda Vale' not in str({key: value for key, value in view.items() if key != 'playerCharacters'})


# Inspiration --------------------------------------------------------------------------------------------------------


def test_take_heart_gives_advantage_on_the_next_roll_only(solo):
    solo.act('Arden Vale', 'inn_caravan_ledger', 'inspect', 'Read the caravan ledger.')

    received = solo.send('Arden Vale', 'Arden Vale takes heart.', {'kind': 'inspiration', 'source': 'scene_panel'})
    assert errors(received) == []
    took = solo.last()
    assert took['narration'] == 'You take a breath and remember why you came.'
    assert solo.stats('Arden Vale')['inspiration'] is False
    assert _inspired(solo.hero('Arden Vale'))

    bench = _check(solo.act('Arden Vale', 'inn_drivers_bench', 'search', "Search the driver's bench."), 'inn_drivers_bench')
    assert (bench['mode'], len(bench['rolls'])) == ('advantage', 2)
    assert not _inspired(solo.hero('Arden Vale'))
    assert [effect for effect in solo.stats('Arden Vale').get('active_effects') or [] if effect.get('id') == INSPIRED_EFFECT_ID] == []

    solo.act('Arden Vale', 'inn_lead_wagon', 'search', 'Search the lead wagon.')
    strongbox = _check(solo.act('Arden Vale', 'inn_wagon_strongbox', 'break', 'Break the strongbox open.'), 'inn_wagon_strongbox')
    assert (strongbox['mode'], len(strongbox['rolls'])) == ('normal', 1)


def test_take_heart_without_inspiration_is_refused(solo):
    received = solo.send('Arden Vale', 'Arden Vale takes heart.', {'kind': 'inspiration', 'source': 'scene_panel'})

    assert errors(received) == ['You hold no Inspiration.']


def test_settling_an_encounter_by_negotiation_is_mercy(solo):
    solo.operator([{'id': 'oath', 'type': 'flag.set', 'flagKey': 'oath_learned', 'flagValue': True, 'reason': 'Test setup.'}])
    solo.go('loc_cairns_roadwardens')

    turn = solo.act('Arden Vale', 'cairn_banner_stone', 'use', 'Speak the names at the banner-stone.')

    assert 'Inspiration — mercy' in _hero_beats(turn)
    assert solo.hero('Arden Vale')['inspiration'] is True


def test_sparing_at_the_finishing_blow_is_mercy_and_a_hero_already_inspired_gains_nothing_more():
    hero = {'id': 'player_1', 'playerId': 1, 'name': 'Arden Vale'}
    state = {'playerCharacters': [hero], 'campaignPack': {}}

    changes = hero_turn_changes(
        state_before=state, state=state, applied=[], actor_id='player_1', turn_id=7, action_intent=None, finisher={'spared': True}
    )
    assert [(change['type'], change['operation'], change['reason']) for change in changes] == [('hero.inspiration', 'grant', MERCY_REASON)]

    holding = {'playerCharacters': [{**hero, 'inspiration': True}], 'campaignPack': {}}
    assert hero_turn_changes(
        state_before=holding, state=holding, applied=[], actor_id='player_1', turn_id=7, action_intent=None, finisher={'spared': True}
    ) == []


def test_ending_the_fight_with_end_turn_is_not_mercy():
    state = {'playerCharacters': [{'id': 'player_1', 'playerId': 1, 'name': 'Arden Vale'}], 'campaignPack': {}}
    surrender = [{'type': 'combat.end', 'endReason': 'enemies_surrendered'}]

    by_action = hero_turn_changes(
        state_before=state, state=state, applied=surrender, actor_id='player_1', turn_id=3,
        action_intent={'kind': 'combat', 'combat': {'action_id': 'combat.demand_surrender'}}, finisher=None,
    )
    by_ending = hero_turn_changes(
        state_before=state, state=state, applied=surrender, actor_id='player_1', turn_id=3,
        action_intent={'kind': 'combat', 'combat': {'action_id': 'combat.end_turn'}}, finisher=None,
    )
    assert [change['reason'] for change in by_action] == [MERCY_REASON]
    assert by_ending == []
    assert THREAD_CHANGE not in {change['type'] for change in by_action}


def test_a_pack_object_marked_inspires_grants_its_reason(solo):
    def mark(snapshot: dict[str, Any]) -> None:
        bell = next(entry for entry in snapshot['currentScene']['interactables'] if entry['id'] == 'inn_wagon_bell')
        bell['inspires'] = {'use': 'the alarm raised'}

    solo.edit_snapshot(mark)
    turn = solo.act('Arden Vale', 'inn_wagon_bell', 'use', 'Ring the wagon bell.')

    assert 'Inspiration — the alarm raised' in _hero_beats(turn)


@pytest.fixture()
def fight(app, client, socketio, monkeypatch):
    narrator = ai_narration(app, monkeypatch)
    _seeded(monkeypatch)
    table = Fight(app, client, socketio, monkeypatch)
    table.narrator = narrator
    table.steady_enemy()
    yield table
    table.close()


def test_only_the_engine_moves_inspiration_or_tells_a_thread(solo):
    hero_id = solo.hero_id('Arden Vale')
    response = solo.client.post(
        f"/api/sessions/{solo.ids['session_id']}/combat/apply-state-changes",
        json={
            'changes': [
                {'id': 'grant', 'type': 'hero.inspiration', 'operation': 'grant', 'actorId': hero_id, 'reason': 'eloquence'},
                {'id': 'thread', 'type': 'hero.thread', 'actorId': hero_id, 'lineId': 'tie_ledger_name', 'text': 'A name.'},
            ]
        },
    )

    assert {entry['reason'] for entry in response.get_json()['validation']['rejected']} == {
        'Only the engine tells a thread or moves Inspiration.'
    }
    assert 'inspiration' not in solo.hero('Arden Vale')


def _give_inspiration(table, hero: str) -> None:
    with table.app.app_context():
        player = db.session.get(Player, table.players[hero])
        player.stats = safe_json_dumps({**safe_json_loads(player.stats, {}), 'inspiration': True}, {})
        db.session.commit()


def test_take_heart_in_a_fight_is_free_told_without_a_model_and_the_next_attack_has_advantage(fight):
    _give_inspiration(fight, 'Corin Ashby')
    _hud(fight, 'Thalion', 'combat.end_turn')
    take_heart = fight.actions('Corin Ashby')[TAKE_HEART_ACTION_ID]
    assert (take_heart['label'], take_heart['available'], take_heart['cost']) == (
        'Take heart (Inspiration)', True, {'action': 0, 'bonusAction': 0, 'movement': 0, 'reaction': 0},
    )

    assert _calls(fight, lambda: _hud(fight, 'Corin Ashby', TAKE_HEART_ACTION_ID)) == 0
    assert _turn(fight).dm_output.startswith('Corin Ashby takes a breath and remembers why they came.')
    assert TAKE_HEART_ACTION_ID not in fight.actions('Corin Ashby')
    assert fight.acting_hero() == 'Corin Ashby'

    _hud(fight, 'Corin Ashby', 'combat.attack.starter_paladin_longsword', fight.enemy()['id'], reply='Corin strikes.')
    attack = next(roll for roll in _metadata(fight)['storyRolls'] if roll['kind'] == 'attack')
    assert attack['d20']['mode'] == 'advantage'
    corin = next(participant for participant in fight.snapshot()['combat']['participants'] if participant['name'] == 'Corin Ashby')
    assert not _inspired(corin)


def test_a_hero_holding_inspiration_whose_attack_is_spent_still_has_the_turn_ended(fight):
    _give_inspiration(fight, 'Corin Ashby')
    _hud(fight, 'Thalion', 'combat.end_turn')
    assert fight.actions('Corin Ashby')[TAKE_HEART_ACTION_ID]['available'] is True
    fight.auto_end = True

    _hud(fight, 'Corin Ashby', 'combat.attack.starter_paladin_longsword', fight.enemy()['id'], reply='Corin strikes.')

    assert _metadata(fight)['state_pipeline']['autoEndTurn'] is True
    assert fight.acting_hero() == 'Thalion'


# The AI narrator ------------------------------------------------------------------------------------------------------


@pytest.fixture()
def ai_table(app, client, socketio, monkeypatch):
    table = AiTable(app, client, socketio, ai_narration(app, monkeypatch))
    yield table
    table.socket.disconnect()


def test_the_narrating_model_is_given_the_thread_to_tell_and_the_heros_desire_and_attachment(ai_table):
    ai_table.send(
        'Read the caravan ledger.',
        'Beneath the last black stroke, one name survives.',
        action_intent={'kind': 'object', 'source': 'scene_panel', 'text': 'Read the caravan ledger.', 'object': {'id': 'inn_caravan_ledger', 'action': 'inspect'}},
    )

    brief = ai_table.brief()
    line = LEDGER_LINE.format(name='Edda Vale')
    assert f'Personal thread — tell it in one or two sentences, facts unchanged: "{line}"' in brief
    assert 'Hero: Arden Vale wants to keep a duty and would not abandon Edda Vale (sister).' in brief.splitlines()
    # The beats are the player's: the brief does not ask the narrator to announce them.
    assert 'A thread of your own' not in brief
    assert 'Inspiration —' not in brief
    # The narrator's reply, then the watchers' first step, told by the engine (the Lantern Post chapter).
    assert ai_table.turn().dm_output == 'Beneath the last black stroke, one name survives.\n\nOut past the hedge a horse stamps, where no horse should be standing.'
    assert ai_table.snapshot()['playerCharacters'][0]['inspiration'] is True


def test_long_in_character_speech_earns_no_inspiration(ai_table):
    ai_table.send(
        '"Mara, these wagons trouble me more than I can say, and I have walked a great many roads in a great many seasons."',
        'Mara Reedwake nods slowly.',
    )

    assert 'inspiration' not in ai_table.snapshot()['playerCharacters'][0]
    beats = safe_json_loads(ai_table.turn().metadata_json, {}).get('storyBeats') or []
    assert [beat for beat in beats if beat['kind'] in THREAD_KINDS] == []


def test_take_heart_out_of_a_fight_is_told_without_the_narrating_model(ai_table):
    with ai_table.app.app_context():
        player = db.session.get(Player, ai_table.ids['player_id'])
        player.stats = safe_json_dumps({**safe_json_loads(player.stats, {}), 'inspiration': True}, {})
        db.session.commit()
    prompts = len(ai_table.narrator.prompts)

    narration = ai_table.reply('Arden Vale takes heart.', action_intent={'kind': 'inspiration', 'source': 'scene_panel', 'text': 'Arden Vale takes heart.'})

    assert narration == 'You take a breath and remember why you came.'
    assert len(ai_table.narrator.prompts) == prompts
    assert _inspired(ai_table.snapshot()['playerCharacters'][0])

