"""The director, chapter ends where the story does, the players' own plans, coming back, and the end of an adventure.

Through the real socket pipeline on the Play Now adventure, in Safe Mode unless an AI narrator is named
(``tests/ai_narrator.py``, its pre-narration helper a counting fake). Clocks, nudges, chapter breaks, decision lines,
stakes and leads are written into the session by hand: the Play Now pack does not author them (the Lantern Post
chapter will), and stakes and leads come from fights that are another task's. Seeded dice as in
``tests/test_where_everyone_stands.py``.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import pytest
import requests

from aidm_server.contracts import ProviderResponse
from aidm_server.database import db
from aidm_server.game_state.orchestration import director, speech_acts
from aidm_server.models import Account, DmTurn, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.services.adventure_records import chronicle, found_secrets, owner_key, pack_secrets, record_completion
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest
from tests.ai_narrator import AiTable, ai_narration
from tests.test_conversations import MARA, OSRIC, PEOPLE, TOVIN, TalkTable, _reach_the_ending, _rolls
from tests.test_where_everyone_stands import _errors, _seeded

INN = 'loc_lantern_post_inn'
PACK_ID = 'original_fantasy_road_of_unremembered_kings'
PLAY_NOW = Path(__file__).resolve().parents[1] / 'docs' / 'examples' / 'the_road_of_unremembered_kings_campaign.json'
STEP_ONE = 'Out past the hedge a horse stamps, where no horse should be standing.'
STEP_TWO = 'Someone in the lane has stopped pretending to be a traveller.'
CALLBACK = 'Mara sets down the cloth she was holding and does not pick it up again.'
NUDGE = 'The bell on the lead wagon hangs very still, as if it is listening.'
CLARIFY_SENTENCE = 'The Caravan Ledger is still unexamined, and Tovin has not been spoken to.'


def _clock(**fields) -> dict:
    return {'id': 'clock_watchers', 'advanceOn': {'objectActions': ['inn_lead_wagon:search', 'inn_drivers_bench:search']}, 'steps': [{'text': STEP_ONE}, {'text': STEP_TWO, 'effects': {'setsFlags': {'watchers_close': True}}}], **fields}


def _location(snapshot: dict, location_id: str = INN) -> dict:
    return next(record for record in snapshot['campaignPack']['catalog']['locations'] if record['id'] == location_id)


def _npc(snapshot: dict, npc_id: str) -> dict:
    return next(record for record in snapshot['campaignPack']['catalog']['npcs'] if record['id'] == npc_id)


def _add_clock(table: TalkTable, clock: dict) -> None:
    table.edit(lambda snapshot: _location(snapshot).setdefault('clocks', []).append(clock))


def _turn_metadata(table: TalkTable) -> dict:
    with table.app.app_context():
        turn = DmTurn.query.filter_by(session_id=table.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
        return safe_json_loads(turn.metadata_json, {})


def _note(table: TalkTable) -> dict | None:
    return ((table.last()['pipeline'].get('dmContextPacket') or {}).get('narration') or {}).get(director.NOTE_KEY)


@pytest.fixture()
def solo(app, client, socketio, safe_mode):
    table = TalkTable(app, client, socketio, party=False)
    yield table
    table.close()


@pytest.fixture()
def party(app, client, socketio, safe_mode):
    table = TalkTable(app, client, socketio, party=True)
    yield table
    table.close()


@pytest.fixture()
def safe_mode(app, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    _seeded(monkeypatch)


# Clocks ---------------------------------------------------------------------------------------------------------------


def test_a_clock_moves_only_for_its_cause_and_tells_each_step_once(solo):
    _add_clock(solo, _clock())

    solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')
    solo.move('Arden Vale', 'inn_yard', 'Step out to the yard')
    assert STEP_ONE not in solo.last()['narration']
    assert solo.snapshot()['clocks']['clock_watchers']['step'] == 0

    solo.act('Arden Vale', 'inn_lead_wagon', 'search')
    told = solo.last()['narration']
    assert told.split('\n\n')[-1] == STEP_ONE
    assert told.count(STEP_ONE) == 1
    assert _note(solo)['kind'] == 'clock'

    # The same cause never moves it twice; another one does.
    solo.act('Arden Vale', 'inn_lead_wagon', 'search')
    assert STEP_TWO not in solo.last()['narration']
    solo.act('Arden Vale', 'inn_drivers_bench', 'search')
    assert solo.last()['narration'].split('\n\n')[-1] == STEP_TWO
    assert solo.flags()['watchers_close'] is True

    # Every step told, the clock is done.
    solo.act('Arden Vale', 'inn_caravan_ledger', 'inspect')
    assert STEP_ONE not in solo.last()['narration'] and STEP_TWO not in solo.last()['narration']
    record = solo.snapshot()['clocks']['clock_watchers']
    assert record['step'] == 2
    assert record['spent'] == ['object:inn_lead_wagon:search', 'object:inn_drivers_bench:search']


def test_a_clock_waits_for_its_start_and_counts_turns_after_it(solo):
    _add_clock(
        solo,
        _clock(
            advanceOn={'everyTurnsAfterStart': 2},
            startWhen={'flags': {'ledger_watch': True}},
            steps=[{'text': STEP_ONE, 'effects': {'startsEncounterId': 'enc_empty_caravan_pressure'}}],
        ),
    )
    for zone, label in (('inn_common_room', 'Go into the common room'), ('inn_yard', 'Step out to the yard')) * 2:
        solo.move('Arden Vale', zone, label)
    assert 'clock_watchers' not in (solo.snapshot().get('clocks') or {})

    solo.edit(lambda snapshot: snapshot['flags'].update(ledger_watch=True))
    solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')
    solo.move('Arden Vale', 'inn_yard', 'Step out to the yard')
    assert STEP_ONE not in solo.last()['narration']
    solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')

    told = solo.last()['narration']
    assert told.count(STEP_ONE) == 1
    # The step starts the encounter as a triggered one does, once.
    snapshot = solo.snapshot()
    assert snapshot['combat']['status'] == 'active'
    assert 'enc_empty_caravan_pressure' in snapshot['flags']['campaignPackTriggeredEncounterIds']
    assert snapshot['clocks']['clock_watchers']['spent'] == ['turns']


def test_a_turn_inside_a_fight_carries_no_note_and_moves_no_clock(solo):
    _add_clock(solo, _clock(advanceOn={'everyTurnsAfterStart': 1}, steps=[{'text': STEP_ONE}, {'text': STEP_TWO}]))
    for target, action in (('inn_drivers_bench', 'search'), ('inn_lead_wagon', 'search'), ('inn_wagon_strongbox', 'unlock')):
        solo.act('Arden Vale', target, action)
    solo.act('Arden Vale', 'inn_wagon_strongbox', 'search')
    assert solo.snapshot()['combat']['status'] == 'active'
    step = solo.snapshot()['clocks']['clock_watchers']['step']

    solo.take_turn('Arden Vale')

    assert solo.snapshot()['clocks']['clock_watchers']['step'] == step
    assert _note(solo) is None
    assert _turn_metadata(solo)[director.MARK_KEY]['fight'] is True


# The note's priority ----------------------------------------------------------------------------------------------


def _callback_on_her_loss(snapshot: dict) -> None:
    _npc(snapshot, MARA).setdefault('callbacks', []).append(
        {'id': 'cb_mara_loss', 'when': {'remembers': 'promised', 'on': 'topic:mara_who_she_lost'}, 'text': CALLBACK}
    )
    _location(snapshot).setdefault('clocks', []).append(
        {'id': 'clock_trust', 'advanceOn': {'flags': ['mara_trusts_party']}, 'steps': [{'text': STEP_ONE}]}
    )


def test_a_callback_comes_first_and_the_clock_waits_for_the_next_turn(solo):
    solo.edit(_callback_on_her_loss)
    solo.talk('Arden Vale', 'Mara')
    solo.topic('Arden Vale', MARA, 'mara_promise')

    solo.topic('Arden Vale', MARA, 'mara_who_she_lost')

    told = solo.last()['narration']
    assert told.split('\n\n')[0] == CALLBACK
    assert told.count(CALLBACK) == 1 and STEP_ONE not in told
    assert _note(solo)['kind'] == 'callback'
    assert solo.flags()['mara_trusts_party'] is True

    solo.leave('Arden Vale', MARA)
    assert solo.last()['narration'].split('\n\n')[-1] == STEP_ONE
    assert CALLBACK not in solo.last()['narration']


def test_a_clock_comes_before_clarity_and_one_note_is_all_a_turn_carries(solo):
    _add_clock(solo, _clock(advanceOn={'everyTurnsAfterStart': 2}, steps=[{'text': STEP_ONE}, {'text': STEP_TWO}]))
    solo.send('Arden Vale', 'I whistle at the clouds.')
    solo.send('Arden Vale', 'I wave at nothing in particular.')
    solo.send('Arden Vale', 'I count the raindrops.')

    # Two misses in a row, and the clock's second turn: the clock is the note.
    assert _note(solo)['kind'] == 'clock'
    told = solo.last()['narration']
    assert told.split('\n\n')[-1] == STEP_ONE and CLARIFY_SENTENCE not in told
    solo.send('Arden Vale', 'I sing to the horses.')
    assert _note(solo)['kind'] == 'clarify'
    assert solo.last()['narration'].split('\n\n')[-1] == CLARIFY_SENTENCE


def test_what_lets_the_next_turn_breathe():
    state = {'currentScene': {'locationId': INN, 'interactables': []}, 'clues': []}

    def landed(*applied: dict) -> bool:
        _note, mark = director.director_note(
            state_before=state, state_after=state, applied=list(applied), actor_id='player_1', narration={},
            clock_note=None, miss=False, session_turns=[], own_previous=None, beats=list,
        )
        return mark['landed']

    assert landed({'type': 'combat.end', 'endReason': 'all_enemies_defeated'})
    assert not landed({'type': 'combat.end', 'endReason': 'players_fled'})
    assert landed({'type': 'npc.talk', 'operation': 'callback', 'text': CALLBACK})
    assert landed({'type': 'npc.talk', 'operation': 'topic', 'promiseMade': {'text': 'Promise'}})
    assert landed({'type': 'npc.talk', 'operation': 'topic', 'promiseKept': {'text': 'Promise'}})
    assert landed({'type': 'npc.talk', 'operation': 'topic', 'inspiration': ['a secret uncovered']})
    assert landed({'type': 'hero.thread', 'lineId': 'tie_ledger_name'})
    assert not landed({'type': 'npc.talk', 'operation': 'topic', 'outcome': 'told'})
    revealed = {**state, 'currentScene': {'locationId': INN, 'interactables': [{'id': 'box', 'hidden': False}]}}
    hidden = {**state, 'currentScene': {'locationId': INN, 'interactables': [{'id': 'box', 'hidden': True}]}}
    _note, mark = director.director_note(
        state_before=hidden, state_after=revealed, applied=[], actor_id='player_1', narration={},
        clock_note=None, miss=False, session_turns=[], own_previous=None, beats=list,
    )
    assert mark['landed'] is True
    # The turn after: a breath, unless something weightier is due.
    note, _mark = director.director_note(
        state_before=state, state_after=state, applied=[], actor_id='player_1', narration={},
        clock_note=None, miss=False, session_turns=[{'mark': {'landed': True}, 'beats': []}], own_previous=None, beats=list,
    )
    assert note == {'kind': 'breathe', 'instruction': 'Let this moment land. Add no new pressure this turn.'}


def test_an_unmatched_message_twice_from_one_hero_asks_for_clarity_once(party):
    party.send('Arden Vale', 'I whistle at the clouds.')
    assert _turn_metadata(party)[director.MARK_KEY]['miss'] is True
    # Another hero's miss between them does not count for Arden.
    party.send('Seren Ashgrove', 'I hum a tune.')
    assert _note(party) is None

    party.send('Arden Vale', 'I wave at nothing in particular.')

    assert _note(party)['kind'] == 'clarify'
    assert party.last()['narration'].split('\n\n')[-1] == CLARIFY_SENTENCE
    # The next miss starts a new pair.
    party.send('Arden Vale', 'I count the raindrops.')
    assert _note(party) is None
    party.send('Arden Vale', 'I sing to the horses.')
    assert _note(party)['kind'] == 'clarify'


def test_words_aimed_out_of_reach_are_a_miss_the_director_counts(solo):
    mare = {'kind': 'object', 'source': 'scene_panel', 'object': {'id': 'inn_marked_mare', 'action': 'inspect'}}
    solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')
    solo.send('Arden Vale', 'I look over the off-leader mare.', mare)
    assert _turn_metadata(solo)[director.MARK_KEY]['miss'] is True

    solo.send('Arden Vale', 'I whistle at the rafters.')

    assert _note(solo)['kind'] == 'clarify'
    # From the common room: what stands there first, then the rest of the place.
    assert solo.last()['narration'].split('\n\n')[-1] == 'The Caravan Ledger is still unexamined, and Mara has not been spoken to.'


def _nudge_on_the_bell(snapshot: dict) -> None:
    bell = next(entry for entry in snapshot['currentScene']['interactables'] if entry['id'] == 'inn_wagon_bell')
    bell['nudge'] = NUDGE


def test_four_turns_with_no_beat_bring_a_nudge_once(solo):
    solo.edit(_nudge_on_the_bell)
    solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')
    assert solo.last()['beats']
    told = []
    for zone, label in (('inn_yard', 'Step out to the yard'), ('inn_common_room', 'Go into the common room')) * 3:
        solo.move('Arden Vale', zone, label)
        told.append(solo.last()['narration'])

    assert [NUDGE in narration for narration in told] == [False, False, False, True, False, False]
    assert told[3].split('\n\n')[-1] == NUDGE
    assert solo.snapshot()['director']['nudgesUsed'] == ['object:inn_wagon_bell']


def test_a_nudge_on_a_topic_points_at_someone_present(solo):
    solo.edit(lambda snapshot: next(topic for topic in _npc(snapshot, TOVIN)['topics'] if topic['id'] == 'tovin_wheels').update(nudge='Tovin keeps glancing at the stable.'))
    solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')
    for zone, label in (('inn_yard', 'Step out to the yard'), ('inn_common_room', 'Go into the common room')) * 2:
        solo.move('Arden Vale', zone, label)

    assert solo.last()['narration'].split('\n\n')[-1] == 'Tovin keeps glancing at the stable.'
    assert _note(solo)['nudge'] == f'topic:{TOVIN}:tovin_wheels'


def test_after_a_promise_the_scripted_narrator_adds_nothing_to_breathe(solo):
    solo.talk('Arden Vale', 'Mara')
    solo.topic('Arden Vale', MARA, 'mara_promise')
    assert _turn_metadata(solo)[director.MARK_KEY]['landed'] is True

    solo.topic('Arden Vale', MARA, 'mara_caravan')

    assert _note(solo) == {'kind': 'breathe', 'instruction': director.BREATHE}
    assert solo.last()['narration'] == PEOPLE[MARA]['topics'][0]['line']


def test_a_secret_told_lets_the_next_turn_breathe(solo, monkeypatch):
    _rolls(monkeypatch, 15)
    solo.talk('Arden Vale', 'Tovin')
    solo.topic('Arden Vale', TOVIN, 'tovin_gently')
    assert _turn_metadata(solo)[director.MARK_KEY]['landed'] is True

    assert _errors(solo.leave('Arden Vale', TOVIN)) == []

    assert _note(solo)['kind'] == 'breathe'


# With a narrating model ---------------------------------------------------------------------------------------------


class FakeHelper:
    """The pre-narration helper: answers the next queued reply (else ``none``) and counts its calls."""

    model_name = 'deepseek-v4-flash'

    def __init__(self) -> None:
        self.calls = 0
        self.replies: list[dict] = []

    def generate(self, request):
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else {'act': 'none'}
        return ProviderResponse(text=json.dumps(reply), provider='deepseek', model=self.model_name)


@pytest.fixture()
def ai(app, client, socketio, monkeypatch):
    narrator = ai_narration(app, monkeypatch)
    _seeded(monkeypatch)
    app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
    helper = FakeHelper()
    monkeypatch.setattr(speech_acts, 'get_helper_provider', lambda *_args, **_kwargs: helper)
    table = AiTable(app, client, socketio, narrator)
    table.helper = helper
    yield table
    table.socket.disconnect()


def _edit(table, change) -> None:
    with table.app.app_context():
        session = db.session.get(Session, table.ids['session_id'])
        snapshot = safe_json_loads(session.state_snapshot, {})
        change(snapshot)
        session.state_snapshot = safe_json_dumps(snapshot, {})
        db.session.commit()


def _ai_send(table, message: str, intent: dict | None = None, reply: str | None = None) -> str:
    """A turn whose model reply (when a model is asked) is ``reply``; the saved reply."""

    table.narrator.replies.clear()
    table.send(message, reply, **({'action_intent': {**intent, 'text': message}} if intent else {}))
    return table.turn().dm_output or ''


def _ai_note(table) -> dict | None:
    metadata = safe_json_loads(table.turn().metadata_json, {})
    return ((metadata.get('state_pipeline') or {}).get('dmContextPacket') or {}).get('narration', {}).get(director.NOTE_KEY)


def test_with_a_narrator_the_engine_tells_a_clock_step_after_the_reply_and_the_brief_says_so(ai):
    _edit(ai, lambda snapshot: _location(snapshot).setdefault('clocks', []).append(_clock()))

    told = _ai_send(ai, 'I search the lead wagon.', {'kind': 'object', 'source': 'scene_panel', 'object': {'id': 'inn_lead_wagon', 'action': 'search'}}, 'You go through the lead wagon board by board.')

    assert told == f'You go through the lead wagon board by board.\n\n{STEP_ONE}'
    brief = ai.brief()
    assert f'Already told this turn (do not repeat or contradict): {STEP_ONE}' in brief
    assert 'Director —' not in brief and 'Callback — tell it now' not in brief


def test_with_a_narrator_a_callback_opens_the_reply_in_the_engines_words(ai):
    def callback_on_rest(snapshot: dict) -> None:
        _npc(snapshot, MARA).setdefault('callbacks', []).append({'id': 'cb_rest', 'when': {'remembers': 'promised', 'on': 'rest'}, 'text': CALLBACK})

    _edit(ai, callback_on_rest)
    _ai_send(ai, 'Go into the common room', {'kind': 'zone_move', 'source': 'scene_panel', 'zone': {'id': 'inn_common_room', 'name': 'Go into the common room'}})
    _ai_send(ai, 'I talk to Mara.')
    _ai_send(ai, 'Promise to bring word of the drivers', {'kind': 'talk', 'source': 'scene_panel', 'talk': {'action': 'topic', 'npc_id': MARA, 'topic_id': 'mara_promise'}})

    told = _ai_send(ai, 'Arden Vale rests.', {'kind': 'rest', 'source': 'scene_panel', 'rest_type': 'short_rest'}, 'You sit by the fire a while.')

    assert told == f'{CALLBACK}\n\nYou sit by the fire a while.'
    assert told.count(CALLBACK) == 1
    assert f'Already told this turn (do not repeat or contradict): {CALLBACK}' in ai.brief()


def test_with_a_narrator_clarify_and_breathe_are_instructions_in_the_brief(ai):
    _ai_send(ai, 'I whistle at the clouds.', reply='You whistle.')
    _ai_send(ai, 'I wave at nothing in particular.', reply='You wave.')
    assert f'Director — clarify: {director.CLARIFY}' in ai.brief()
    assert _ai_note(ai)['kind'] == 'clarify'
    # The scripted sentence is the scripted narrator's alone.
    assert ai.turn().dm_output == 'You wave.'

    _ai_send(ai, 'I talk to Mara.')
    _ai_send(ai, 'Promise to bring word of the drivers', {'kind': 'talk', 'source': 'scene_panel', 'talk': {'action': 'topic', 'npc_id': MARA, 'topic_id': 'mara_promise'}})
    _ai_send(ai, 'Leave it there.', {'kind': 'talk', 'source': 'scene_panel', 'talk': {'action': 'leave', 'npc_id': MARA}})
    assert _ai_note(ai)['kind'] == 'breathe'


def test_the_helper_reads_a_plan_once_a_sitting(ai):
    _ai_send(ai, 'I talk to Mara.')
    ai.helper.replies.extend([{'act': 'none', 'plan': 'write to my sister when this is over'}, {'act': 'none', 'plan': 'learn to sing'}])

    _ai_send(ai, 'When this is over I mean to write to my sister.', reply='Mara nods.')

    beats = [beat['text'] for beat in safe_json_loads(ai.turn().metadata_json, {}).get('storyBeats') or []]
    assert beats == ['Noted as a plan — write to my sister when this is over']
    assert [plan['text'] for plan in ai.snapshot()['plans']] == ['write to my sister when this is over']
    assert ai.snapshot()['plans'][0]['source'] == 'helper'

    _ai_send(ai, 'One day I would like to learn to sing.', reply='Mara shrugs.')
    assert ai.helper.calls == 2
    assert [plan['text'] for plan in ai.snapshot()['plans']] == ['write to my sister when this is over']


# The end of a chapter -----------------------------------------------------------------------------------------------

DECISIONS = [
    {'when': {'flags': {'promised_mara_word': True}}, 'text': 'You gave Mara Reedwake your word.'},
    {'when': {'remembers': {'npcId': MARA, 'key': 'promised'}}, 'text': 'Mara will hold you to it.'},
    {'when': {'stakes': {'enc_empty_caravan_pressure.fragment': 'secured'}}, 'text': 'You kept the fragment out of their hands.'},
    {'when': {'stakes': {'enc_empty_caravan_pressure.fragment': 'taken'}}, 'text': 'The fragment went north without you.'},
    {'when': {'flags': {'tovin_button_revealed': True}}, 'text': 'Tovin gave up the button.'},
    {'text': 'You came to the Lantern Post.'},
    {'when': {'remembers': [{'npcId': OSRIC, 'key': 'claimed_authority'}]}, 'text': 'You claimed an authority you do not hold.'},
    {'text': 'You left the yard as you found it.'},
    {'text': 'A sixth line that holds is not shown.'},
]


def _chapter_break(snapshot: dict) -> None:
    checkpoint = snapshot['campaignPack']['checkpoints'][0]
    checkpoint.update(chapterBreak=True, decisionLines=DECISIONS)
    # What the theft would have left: a stake and two leads, one followed.
    snapshot['stakes'] = {'enc_empty_caravan_pressure': {'fragment': 'secured', 'witness': 'safe'}}
    snapshot['leads'] = [{'text': 'They had horses waiting in the north lane.'}, {'text': 'A man in grey came asking.', 'followed': True}]


def _plan(table, hero: str, text: str):
    return table.client.post(f"/api/sessions/{table.ids['session_id']}/plans", json={'text': text, 'player_id': table.players[hero]})


def _log_beats(table) -> list[dict]:
    response = table.client.get(f"/api/sessions/{table.ids['session_id']}/log?view=player&player_id={table.players['Arden Vale']}")
    assert response.status_code == 200, response.get_json()
    return [beat for entry in response.get_json()['entries'] for beat in (entry.get('metadata') or {}).get('storyBeats') or []]


def test_a_chapter_break_ends_the_chapter_with_what_was_decided_and_what_is_open(solo):
    solo.edit(_chapter_break)
    solo.talk('Arden Vale', 'Mara')
    solo.topic('Arden Vale', MARA, 'mara_promise')
    solo.leave('Arden Vale', MARA)
    assert _plan(solo, 'Arden Vale', 'Ask Osric about the letter').status_code == 200

    solo.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')

    ended = next(beat for beat in solo.last()['beats'] if beat['kind'] == 'chapter_end')
    assert ended['text'] == 'End of chapter 1 · The Empty Caravan'
    assert ended['chapterEnd'] == {
        'number': 1,
        'title': 'The Empty Caravan',
        'checkpointId': 'cp_01_empty_caravan',
        'decided': [
            'You gave Mara Reedwake your word.',
            'Mara will hold you to it.',
            'You kept the fragment out of their hands.',
            'You came to the Lantern Post.',
            'You left the yard as you found it.',
        ],
        'open': ['Mara Reedwake is still waiting for word.', 'They had horses waiting in the north lane.', 'Ask Osric about the letter'],
        'teaser': 'Next: the old north road, where the lights are waiting.',
    }
    # It is in the log, for whoever reads the story later.
    assert [beat['text'] for beat in _log_beats(solo) if beat['kind'] == 'chapter_end'] == ['End of chapter 1 · The Empty Caravan']


def test_the_next_action_begins_the_next_chapter_and_starting_one_by_hand_adds_no_entry(solo):
    session_id = solo.ids['session_id']
    assert solo.client.post(f'/api/sessions/{session_id}/next-chapter').status_code == 409
    solo.edit(_chapter_break)
    solo.talk('Arden Vale', 'Mara')
    solo.topic('Arden Vale', MARA, 'mara_promise')
    _plan(solo, 'Arden Vale', 'Ask Osric about the letter')
    solo.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    # The lanterns' fight keeps this chapter open until it is over.
    assert solo.client.post(f'/api/sessions/{session_id}/next-chapter').get_json()['error_code'] == 'chapter_unsettled'
    solo.operator([{'type': 'combat.end', 'status': 'ended', 'endReason': 'all_enemies_defeated'}])

    started = solo.client.post(f'/api/sessions/{session_id}/next-chapter')
    again = solo.client.post(f'/api/sessions/{session_id}/next-chapter')

    assert (started.status_code, again.status_code) == (201, 200)
    assert started.get_json()['session_id'] == again.get_json()['session_id'] != session_id
    solo.ids['session_id'] = started.get_json()['session_id']
    carried = solo.snapshot()
    assert [plan['text'] for plan in carried['plans']] == ['Ask Osric about the letter']
    assert [promise['id'] for promise in carried['promises']] == ['promised_mara_word']
    assert [beat for beat in _log_beats(solo) if beat['kind'] == 'chapter_end'] == []


def test_starting_a_chapter_by_hand_adds_no_chapter_end(solo):
    solo.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    solo.operator([{'type': 'combat.end', 'status': 'ended', 'endReason': 'all_enemies_defeated'}])

    response = solo.client.post('/api/sessions/start', json={'campaign_id': solo.ids['campaign_id'], 'continuity': 'continue'})

    assert response.status_code == 201, response.get_json()
    assert [beat for beat in _log_beats(solo) if beat['kind'] == 'chapter_end'] == []
    assert solo.client.post(f"/api/sessions/{solo.ids['session_id']}/next-chapter").status_code == 409


# The players' own plans -------------------------------------------------------------------------------------------


def test_a_plan_is_noted_shown_in_the_journal_and_done(party):
    noted = _plan(party, 'Seren Ashgrove', '  Ask   Osric about the letter ')
    assert noted.status_code == 200, noted.get_json()
    plan = noted.get_json()['plan']
    assert plan['text'] == 'Ask Osric about the letter'
    # Every player at the table sees the party's plans.
    journal = party.view('Arden Vale')['journal']
    assert [(entry['id'], entry['text'], entry['playerId']) for entry in journal['plans']] == [(plan['id'], plan['text'], party.players['Seren Ashgrove'])]
    assert 'plans' not in party.view('Arden Vale')

    assert _plan(party, 'Arden Vale', '   ').status_code == 400
    done = party.client.delete(f"/api/sessions/{party.ids['session_id']}/plans/{plan['id']}")
    assert done.status_code == 200 and done.get_json() == {'session_id': party.ids['session_id'], 'plans': [], 'removed': True}
    assert party.view('Arden Vale')['journal']['plans'] == []


# Coming back ----------------------------------------------------------------------------------------------------------


def _age_turns(table, minutes: int, *, up_to: int | None = None) -> None:
    with table.app.app_context():
        query = DmTurn.query.filter_by(session_id=table.ids['session_id'])
        for turn in query.all():
            if up_to is None or turn.turn_id <= up_to:
                turn.created_at = turn.created_at - timedelta(minutes=minutes)
        db.session.commit()


def _coming_back(table, hero: str = 'Arden Vale') -> dict | None:
    response = table.client.get(f"/api/sessions/{table.ids['session_id']}/coming-back?view=player&player_id={table.players[hero]}")
    assert response.status_code == 200, response.get_json()
    return response.get_json()['comingBack']


def test_coming_back_after_half_an_hour_shows_where_you_stand_until_the_next_action(solo):
    solo.talk('Arden Vale', 'Osric')
    solo.send('Arden Vale', 'I was sent by the wardens of the road.')
    solo.leave('Arden Vale', OSRIC)
    earlier = solo.last_turn_id()
    _age_turns(solo, 180)
    solo.talk('Arden Vale', 'Mara')
    solo.topic('Arden Vale', MARA, 'mara_promise')
    _plan(solo, 'Arden Vale', 'Ask Osric about the letter')
    assert _coming_back(solo) is None

    _age_turns(solo, 31)

    assert _coming_back(solo) == {
        'where': 'You are in the common room at the Lantern Post Inn, set to investigate the empty caravan.',
        # Osric's memory changed in an earlier sitting.
        'people': ['Mara Reedwake is holding you to your word.'],
        'plans': ['Ask Osric about the letter'],
    }
    assert earlier < solo.last_turn_id()
    solo.leave('Arden Vale', MARA)
    assert _coming_back(solo) is None


# The end of an adventure ------------------------------------------------------------------------------------------


def _ending(table, hero: str = 'Arden Vale') -> dict:
    return table.view(hero)['campaignPack']['afterword']


def test_your_word_reads_each_promise_as_it_stood_at_the_end(solo):
    solo.talk('Arden Vale', 'Mara')
    solo.topic('Arden Vale', MARA, 'mara_promise')
    solo.leave('Arden Vale', MARA)
    solo.move('Arden Vale', 'inn_yard', 'Step out to the yard')
    solo.talk('Arden Vale', 'Tovin')
    solo.send('Arden Vale', 'I promise I will find Wenna for you.')
    solo.leave('Arden Vale', TOVIN)
    solo.edit(_reach_the_ending)

    assert _ending(solo)['replay'] == solo.snapshot()['playNow']['examplePackId']
    assert _ending(solo)['yourWord'] == [
        {'id': 'promised_mara_word', 'status': 'left open', 'line': 'Mara Reedwake is still waiting for word.'},
        {'id': solo.snapshot()['promises'][1]['id'], 'status': 'left open', 'line': 'Tovin Bell is still waiting on your word.'},
    ]

    solo.edit(lambda snapshot: snapshot['promises'][0].update(brokenWhen={'flags': {'ward_renewed': True}}))
    assert _ending(solo)['yourWord'][0] == {'id': 'promised_mara_word', 'status': 'broken', 'line': 'You never did bring Mara Reedwake word.'}

    solo.edit(lambda snapshot: snapshot['promises'][0].pop('brokenWhen'))
    solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')
    solo.talk('Arden Vale', 'Mara')
    solo.topic('Arden Vale', MARA, 'mara_tell_drivers')
    assert _ending(solo)['yourWord'][0] == {'id': 'promised_mara_word', 'status': 'kept', 'line': 'You kept your word to Mara Reedwake.'}


def _met(*npc_ids: str):
    def change(snapshot: dict) -> None:
        catalog = {npc['id']: npc for npc in snapshot['campaignPack']['catalog']['npcs']}
        known = {npc['id'] for npc in snapshot['knownNpcs']}
        snapshot['knownNpcs'].extend({'id': npc_id, 'name': catalog[npc_id]['name'], 'disposition': 'neutral'} for npc_id in npc_ids if npc_id not in known)

    return change


def _became(table) -> list[str]:
    return [row['text'] for row in _ending(table)['becameOfThem']]


def test_what_became_of_them_picks_the_first_epilogue_that_holds_for_people_met(solo):
    solo.edit(_reach_the_ending)
    assert [row['name'] for row in _ending(solo)['becameOfThem']] == ['Mara Reedwake', 'Tovin Bell', 'Osric Hale']
    tovin = [entry['text'] for entry in PEOPLE[TOVIN]['epilogues']]
    assert _became(solo)[1] == tovin[2]

    solo.edit(lambda snapshot: snapshot['flags'].update(tovin_button_revealed=True))
    assert _became(solo)[1] == tovin[1]
    solo.edit(lambda snapshot: next(npc for npc in snapshot['knownNpcs'] if npc['id'] == TOVIN).update(disposition=1))
    assert _became(solo)[1] == tovin[0]

    # A companion's ending sentence covers them; at most six people, in the pack's order.
    solo.edit(_met('npc_brokk_stonehand', 'npc_serenil_westward_song', 'npc_veyra_ashcap'))
    solo.edit(lambda snapshot: snapshot.setdefault('partyNpcs', []).append({'id': 'npc_radan_greycloak', 'name': 'Radan Greycloak'}))
    names = [row['name'] for row in _ending(solo)['becameOfThem']]
    assert names == ['Mara Reedwake', 'Tovin Bell', 'Osric Hale', 'Brokk Stonehand', 'Serenil of the Westward Song', 'Veyra Ashcap']
    solo.edit(lambda snapshot: snapshot.update(partyNpcs=[]))
    solo.edit(_met('npc_radan_greycloak'))
    assert 'Radan Greycloak is still reading milestones, somewhere north.' in _became(solo)
    assert len(_became(solo)) == 6


# Records and the chronicle ------------------------------------------------------------------------------------------


def _before_the_ending(snapshot: dict) -> None:
    """The last choice made: the next turn's progress reaches the ending."""

    pack = snapshot['campaignPack']
    checkpoints = [checkpoint['id'] for checkpoint in pack['checkpoints']]
    pack.update(activeCheckpointId='cp_06_final_valley', completedCheckpointIds=checkpoints[:5])
    snapshot['flags'].update(ward_renewed=True, travelers_freed=True)
    for quest in snapshot['quests']:
        for objective in quest.get('objectives') or []:
            if objective['id'] == 'obj_decide_ward_fate':
                objective['status'] = 'completed'


def _chronicle(table) -> list[dict]:
    response = table.client.get('/api/sessions/chronicle')
    assert response.status_code == 200, response.get_json()
    return response.get_json()['adventures']


def test_reaching_the_ending_writes_the_record_the_chronicle_lists(solo):
    assert _chronicle(solo) == []
    solo.edit(_before_the_ending)

    solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')

    assert 'The story ends — The Road Remembers' in solo.beats()
    with solo.app.app_context():
        state = solo.snapshot()
        total, found = len(pack_secrets(state)), len(found_secrets(state))
    assert _chronicle(solo) == [
        {'packId': PACK_ID, 'title': 'The Road of Unremembered Kings', 'endings': ['The Road Remembers'], 'timesCompleted': 1, 'neverFound': total - found}
    ]
    assert total > found


def test_records_keep_each_ending_count_a_secret_once_and_keep_accounts_apart(app, solo):
    first = solo.snapshot()
    _reach_the_ending(first)
    second = deepcopy(first)
    second['flags'] = {**second['flags'], 'ward_renewed': False, 'ward_broken': True}
    next(npc for npc in second['knownNpcs'] if npc['id'] == TOVIN)['talk'] = {'told': ['tovin_gently']}
    with app.app_context():
        workspace = db.session.get(Player, solo.players['Arden Vale']).workspace_id
        record_completion(first, campaign_id=101, workspace_id=workspace)
        record_completion(first, campaign_id=101, workspace_id=workspace)
        record_completion(second, campaign_id=102, workspace_id=workspace)
        account = Account(username='reader', first_name='Re', last_name='Ader')
        db.session.add(account)
        db.session.flush()
        db.session.get(Player, solo.players['Arden Vale']).account_id = account.account_id
        record_completion(first, campaign_id=103, workspace_id=workspace)
        db.session.commit()
        total = len(pack_secrets(first))
        found = found_secrets(first) | found_secrets(second)
        local = chronicle([owner_key(account_id=None, workspace_id=workspace)])
        signed_in = chronicle([owner_key(account_id=account.account_id, workspace_id=workspace)])

    assert local == [
        {
            'packId': PACK_ID,
            'title': 'The Road of Unremembered Kings',
            'endings': ['The Road Remembers', 'Only a Road'],
            'timesCompleted': 2,
            'neverFound': total - len(found),
        }
    ]
    assert f'topic:{TOVIN}:tovin_gently' in found
    assert [(row['endings'], row['timesCompleted']) for row in signed_in] == [(['The Road Remembers'], 1)]


def test_the_packs_secrets_are_hidden_objects_checked_topics_tie_lines_and_secret_clues(solo):
    state = solo.snapshot()
    state['campaignPack']['catalog']['clues'][0]['secret'] = True
    secrets = pack_secrets(state)
    assert 'object:inn_wagon_strongbox' in secrets
    assert f'topic:{TOVIN}:tovin_gently' in secrets and f'topic:{MARA}:mara_who_she_lost' in secrets
    assert f'topic:{MARA}:mara_caravan' not in secrets
    assert 'tie:tie_ledger_name' in secrets
    assert f"clue:{state['campaignPack']['catalog']['clues'][0]['id']}" in secrets
    assert found_secrets(state) == set()


# The pack's fields --------------------------------------------------------------------------------------------------


def test_the_linter_names_what_a_clock_or_a_decision_line_gets_wrong(app):
    pack = json.loads(PLAY_NOW.read_text(encoding='utf-8'))
    pack['locations'][0]['clocks'] = [
        {'id': 'clock_a', 'advanceOn': {'objectActions': ['no_such_wagon:search']}, 'steps': [{'text': 'Nearer.', 'effects': {'startsEncounterId': 'enc_none', 'discoversLocationIds': ['loc_none'], 'affects': [{'npcId': 'npc_none', 'disposition': -1}]}}]},
        {'id': 'clock_a', 'advanceOn': {}, 'steps': []},
    ]
    pack['checkpoints'][0]['decisionLines'] = [{'when': {'remembers': {'npcId': 'npc_none', 'key': 'promised'}, 'stakes': {'enc_none.fragment': 'taken'}}, 'text': 'You did a thing.'}]

    with app.app_context():
        issues = {(issue['code'], issue['path']) for issue in lint_campaign_pack_manifest(pack, strict=True)['issues']}

    assert {
        ('director_unknown_reference', 'locations[0].clocks[0].advanceOn.objectActions'),
        ('director_unknown_reference', 'locations[0].clocks[0].steps[0].effects.startsEncounterId'),
        ('director_unknown_reference', 'locations[0].clocks[0].steps[0].effects.discoversLocationIds'),
        ('director_unknown_reference', 'locations[0].clocks[0].steps[0].effects.affects.npcId'),
        ('clock_duplicate_id', 'clocks'),
        ('clock_without_steps', 'locations[0].clocks[1].steps'),
        ('clock_without_cause', 'locations[0].clocks[1].advanceOn'),
        ('director_unknown_reference', 'checkpoints[0].decisionLines[0].when.remembers.npcId'),
        ('director_unknown_reference', 'checkpoints[0].decisionLines[0].when.stakes'),
    } <= issues


def test_the_play_now_pack_carries_its_teasers_epilogues_and_maras_promise_lines():
    pack = json.loads(PLAY_NOW.read_text(encoding='utf-8'))
    teasers = {checkpoint['id']: checkpoint.get('teaser') for checkpoint in pack['checkpoints']}
    assert teasers['cp_01_empty_caravan'] == 'Next: the old north road, where the lights are waiting.'
    assert teasers['cp_06_final_valley'] == 'Next: the last stone, and the hand that means to hold it.'
    assert teasers['cp_07_road_remembers'] is None
    promise = next(topic for topic in PEOPLE[MARA]['topics'] if topic['id'] == 'mara_promise')['effects']['promise']
    assert promise == {
        'id': 'promised_mara_word',
        'keptText': 'You kept your word to Mara Reedwake.',
        'brokenText': 'You never did bring Mara Reedwake word.',
        'openText': 'Mara Reedwake is still waiting for word.',
    }
    assert PEOPLE['npc_radan_greycloak']['epilogues'][0] == {'when': {'companion': True}}
    assert 'pressure' not in json.dumps(pack['locations'])


def test_where_you_stand_names_the_room_unless_the_place_already_does():
    from aidm_server.services.session_return import where_you_stand

    zones = [{'id': 'road', 'name': 'The Road', 'adjacentTo': ['verge']}, {'id': 'verge', 'name': 'The Ditch and Verge', 'adjacentTo': ['road']}]
    state = {
        'currentScene': {'locationId': 'loc_road', 'playerZones': {'player_1': 'road', 'player_2': 'verge'}},
        'locations': [{'id': 'loc_road', 'name': 'The Old North Road'}],
        'campaignPack': {'catalog': {'locations': [{'id': 'loc_road', 'name': 'The Old North Road', 'zones': zones, 'startZoneId': 'road'}]}},
        'quests': [],
    }
    assert where_you_stand(state, 'player_1') == 'You are at the Old North Road.'
    assert where_you_stand(state, 'player_2') == 'You are in the ditch and verge at the Old North Road.'
