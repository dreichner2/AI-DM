"""People you can talk to: conversations, topics, speech acts, memory and callbacks.

Through the real socket pipeline on the Play Now adventure, in Safe Mode unless an AI narrator is named
(``tests/ai_narrator.py``, whose pre-narration helper is a counting fake here). Seeded dice: checks the engine rolls
for a hero come up 15 unless a test says otherwise (Arden Vale has no Charisma or Wisdom bonus, Strength +2).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from aidm_server.contracts import ProviderResponse
from aidm_server.database import db
from aidm_server.game_state.orchestration import conversations, speech_acts
from aidm_server.models import Session, safe_json_dumps, safe_json_loads
from aidm_server.prompt_templates import ENGINE_BRIEF_RULES
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest
from tests.ai_narrator import ai_narration
from tests.test_fight_maneuvers import _one_cutthroat
from tests.test_pack_author_traps import _pack as _trap_pack
from tests.test_where_everyone_stands import Table, _errors, _seeded

PACK_PATH = Path(__file__).resolve().parents[1] / 'docs' / 'examples' / 'the_road_of_unremembered_kings_campaign.json'
PACK = json.loads(PACK_PATH.read_text(encoding='utf-8'))
PEOPLE = {npc['id']: npc for npc in PACK['npcs']}
LINES = {npc_id: {topic['id']: topic['line'] for topic in npc.get('topics') or []} for npc_id, npc in PEOPLE.items()}
MARA, TOVIN, OSRIC = 'npc_mara_reedwake', 'npc_tovin_bell', 'npc_osric_hale'
MARA_GREETING = '"You\'ll want something hot. Then you\'ll want to know what I know," Mara says.'
MARA_ASKS = ['Ask about the caravan', 'Ask about the horses', 'Ask about strangers on the road', 'Ask about the bell']
TALKING = ['Say something else', 'Leave it there']
LEDGER_NUDGE = next(entry for entry in PACK['locations'][0]['interactables'] if entry['id'] == 'inn_caravan_ledger')['nudge']


class TalkTable(Table):
    """The Play Now table, with what a conversation needs."""

    def talk(self, hero: str, name: str) -> list[dict]:
        return self.send(hero, f'I talk to {name}.')

    def topic(self, hero: str, npc_id: str, topic_id: str) -> list[dict]:
        """Choose a topic: as the chip offers it, or one waiting behind the first four by its label."""

        written = next(topic['label'] for topic in PEOPLE[npc_id]['topics'] if topic['id'] == topic_id)
        label = next((action['label'] for action in self.talk_actions(hero) if action.get('id') == topic_id), written)
        return self.send(hero, label, {'kind': 'talk', 'source': 'scene_panel', 'talk': {'action': 'topic', 'npc_id': npc_id, 'topic_id': topic_id}})

    def leave(self, hero: str, npc_id: str) -> list[dict]:
        return self.send(hero, 'Leave it there.', {'kind': 'talk', 'source': 'scene_panel', 'talk': {'action': 'leave', 'npc_id': npc_id}})

    def conversation(self, hero: str) -> dict | None:
        return self.view(hero)['currentScene'].get('conversation')

    def talk_actions(self, hero: str) -> list[dict]:
        return (self.conversation(hero) or {}).get('actions') or []

    def labels(self, hero: str) -> list[str]:
        return [action['label'] for action in self.talk_actions(hero)]

    def person(self, npc_id: str) -> dict:
        return next(npc for npc in self.snapshot()['knownNpcs'] if npc['id'] == npc_id)

    def journal_person(self, hero: str, name: str) -> dict:
        return next(person for person in self.view(hero)['journal']['people'] if person['name'] == name)

    def beats(self) -> list[str]:
        return [beat['text'] for beat in self.last()['beats']]

    def flags(self) -> dict:
        return self.snapshot()['flags']

    def edit(self, change) -> None:
        """Change the saved session directly (what the test stands up rather than plays)."""

        with self.app.app_context():
            session = db.session.get(Session, self.ids['session_id'])
            snapshot = safe_json_loads(session.state_snapshot, {})
            change(snapshot)
            session.state_snapshot = safe_json_dumps(snapshot, {})
            db.session.commit()


@pytest.fixture()
def safe_mode(app, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    _seeded(monkeypatch)


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


def _rolls(monkeypatch, value: int) -> None:
    """Checks the engine rolls for a hero come up ``value`` from now on."""

    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: value)


def _talk_changes(table: TalkTable) -> list[dict]:
    pipeline = table.last()['pipeline']
    return [change for change in pipeline.get('immediateAppliedChanges') or [] if change['type'] == 'npc.talk']


# B. Opening a conversation, topics -----------------------------------------------------------------------------


def test_talk_to_mara_opens_a_conversation_with_her_greeting_and_spends_no_knows_line(solo):
    assert _errors(solo.talk('Arden Vale', 'Mara')) == []

    told = solo.last()
    assert solo.zone('Arden Vale') == 'inn_common_room'
    assert told['narration'].split('\n\n')[1] == MARA_GREETING
    assert solo.snapshot()['conversations'] == {'player_1': {'npcId': MARA, 'sinceTurn': 1, 'failed': []}}
    conversation = solo.conversation('Arden Vale')
    assert (conversation['npcId'], conversation['name'], conversation['summary']) == (MARA, 'Mara Reedwake', PEOPLE[MARA]['playerSummary'])
    assert solo.labels('Arden Vale') == [*MARA_ASKS, *TALKING]
    assert [action['kind'] for action in solo.talk_actions('Arden Vale')] == ['topic'] * 4 + ['say_free', 'leave_conversation']
    assert solo.person(MARA).get('talk', {}).get('knowsTold') is None

    # Again later: no greeting, she turns to you.
    solo.leave('Arden Vale', MARA)
    solo.talk('Arden Vale', 'Mara')
    assert solo.last()['narration'] == 'Mara Reedwake turns to you.'


def test_the_four_asks_then_the_promise_warms_mara_and_opens_who_she_lost(solo):
    solo.talk('Arden Vale', 'Mara')
    for topic_id, label in zip(('mara_caravan', 'mara_horses', 'mara_strangers', 'mara_bell'), MARA_ASKS):
        assert _errors(solo.topic('Arden Vale', MARA, topic_id)) == []
        assert solo.last()['narration'].split('\n\n')[0] == LINES[MARA][topic_id]
        assert label not in solo.labels('Arden Vale')
    # The fourth quiet turn at the inn brings its nudge (the Lantern Post chapter's ledger).
    assert solo.last()['narration'].split('\n\n')[1] == LEDGER_NUDGE
    assert solo.labels('Arden Vale') == ['Promise to bring word of the drivers', *TALKING]
    assert solo.journal_person('Arden Vale', 'Mara Reedwake')['disposition'] == 'neutral'

    solo.topic('Arden Vale', MARA, 'mara_promise')

    assert solo.last()['narration'] == LINES[MARA]['mara_promise']
    assert solo.beats() == [
        'A promise made — Promise to bring word of the drivers',
        'Mara Reedwake warms to you.',
        'Inspiration — your word given',
    ]
    assert solo.flags()['promised_mara_word'] is True
    assert solo.person(MARA)['disposition'] == 1
    view = solo.view('Arden Vale')
    assert next(npc for npc in view['knownNpcs'] if npc['id'] == MARA)['disposition'] == 'warm'
    mara = solo.journal_person('Arden Vale', 'Mara Reedwake')
    assert (mara['disposition'], mara['remembers']) == ('warm', ['you gave your word to bring word of the drivers'])
    assert view['journal']['yourWord'] == [
        {'id': 'promised_mara_word', 'text': 'Promise to bring word of the drivers', 'to': 'Mara Reedwake', 'status': 'open'}
    ]
    assert solo.labels('Arden Vale') == ['Ask about who she lost', *TALKING]

    solo.topic('Arden Vale', MARA, 'mara_who_she_lost')
    assert solo.last()['narration'] == LINES[MARA]['mara_who_she_lost']
    assert solo.flags()['mara_trusts_party'] is True
    assert solo.person(MARA)['disposition'] == 2
    # Her secret told is a secret uncovered; the hero still holds the Inspiration their word gave.
    assert solo.beats() == ['Mara Reedwake warms to you.']
    assert solo.labels('Arden Vale') == TALKING


def test_tovin_asked_gently_gives_the_button_and_mara_sees_it(solo):
    solo.talk('Arden Vale', 'Tovin')
    assert solo.labels('Arden Vale') == ['Ask about the wheels', 'Ask about the horses', 'Ask about under the bench', 'Ask about the bell', *TALKING]
    for topic_id in ('tovin_wheels', 'tovin_horses', 'tovin_bench', 'tovin_bell'):
        solo.topic('Arden Vale', TOVIN, topic_id)
    assert solo.labels('Arden Vale') == ['Ask gently what he is hiding', 'Lean on him until he talks', *TALKING]

    solo.topic('Arden Vale', TOVIN, 'tovin_gently')

    assert solo.last()['narration'].startswith(LINES[TOVIN]['tovin_gently'])
    assert solo.beats()[:4] == [
        'Persuasion check: 15 — success',
        'Received — Silver Driver\'s Button',
        'Tovin Bell warms to you.',
        'Mara Reedwake saw how you spoke to the boy.',
    ]
    assert solo.flags()['tovin_button_revealed'] is True
    assert 'silver_drivers_button' in {item['id'] for item in solo.hero('Arden Vale')['inventory']['items']}
    assert (solo.person(TOVIN)['disposition'], solo.person(MARA)['disposition']) == (1, 1)
    # Either way of getting the button retires the other.
    assert solo.labels('Arden Vale') == TALKING


def test_leaning_on_tovin_cools_him_and_mara(solo):
    solo.talk('Arden Vale', 'Tovin')
    for topic_id in ('tovin_wheels', 'tovin_horses', 'tovin_bench', 'tovin_bell'):
        solo.topic('Arden Vale', TOVIN, topic_id)

    solo.topic('Arden Vale', TOVIN, 'tovin_lean')

    assert solo.last()['narration'].startswith(LINES[TOVIN]['tovin_lean'])
    beats = solo.last()['beats']
    assert [beat['text'] for beat in beats if beat['kind'] == 'bond'] == ['Tovin Bell will remember that.', 'Mara Reedwake will remember how you spoke to the boy.']
    assert all(beat.get('down') is True for beat in beats if beat['kind'] == 'bond')
    assert (solo.person(TOVIN)['disposition'], solo.person(MARA)['disposition']) == (-1, -1)
    assert solo.journal_person('Arden Vale', 'Tovin Bell')['disposition'] == 'wary'
    assert solo.flags()['tovin_button_revealed'] is True


def test_a_failed_check_is_offered_again_only_in_a_new_conversation(solo, monkeypatch):
    solo.talk('Arden Vale', 'Tovin')
    for topic_id in ('tovin_wheels', 'tovin_horses', 'tovin_bench', 'tovin_bell'):
        solo.topic('Arden Vale', TOVIN, topic_id)
    _rolls(monkeypatch, 2)

    solo.topic('Arden Vale', TOVIN, 'tovin_gently')

    assert solo.last()['narration'] == '"It\'s nothing! It\'s— it\'s nothing."'
    assert solo.beats() == ['Persuasion check: 2 — failure']
    assert solo.labels('Arden Vale') == ['Lean on him until he talks', *TALKING]
    solo.leave('Arden Vale', TOVIN)
    solo.talk('Arden Vale', 'Tovin')
    assert solo.labels('Arden Vale') == ['Ask gently what he is hiding', 'Lean on him until he talks', *TALKING]


def _read_the_ledger(table: TalkTable) -> None:
    table.act('Arden Vale', 'inn_caravan_ledger', 'inspect')


def _win_osric_over(table: TalkTable) -> None:
    """From wary: show him the ledger (neutral), then hear about the letter."""

    _read_the_ledger(table)
    table.talk('Arden Vale', 'Osric')
    table.topic('Arden Vale', OSRIC, 'osric_show')
    assert table.person(OSRIC)['disposition'] == 0
    table.topic('Arden Vale', OSRIC, 'osric_letter')


def test_osric_stands_with_the_party_at_dc_13_and_at_dc_10_once_mara_trusts_them(solo, monkeypatch):
    solo.talk('Arden Vale', 'Osric')
    assert solo.last()['narration'].split('\n\n')[-1] == '"Say it quickly," Osric says.'
    # Wary, and nothing found yet: neither the letter nor what was found is his to hear.
    assert solo.labels('Arden Vale') == ['Ask about the missing', 'Ask about the one who came back', 'Ask about the stores', *TALKING]
    solo.leave('Arden Vale', OSRIC)
    _win_osric_over(solo)
    _rolls(monkeypatch, 11)

    solo.topic('Arden Vale', OSRIC, 'osric_stand')

    failed = _talk_changes(solo)[0]
    assert (failed['check']['dc'], failed['outcome']) == (13, 'failed')
    assert solo.last()['narration'] == '"I have a town to feed. Ask me when you\'ve shown me something."'
    solo.leave('Arden Vale', OSRIC)
    solo.operator([{'type': 'flag.set', 'flagKey': 'mara_trusts_party', 'flagValue': True}])
    solo.talk('Arden Vale', 'Osric')

    solo.topic('Arden Vale', OSRIC, 'osric_stand')

    told = _talk_changes(solo)[0]
    assert (told['check']['dc'], told['outcome']) == (10, 'told')
    assert solo.last()['narration'].startswith(LINES[OSRIC]['osric_stand'])
    assert solo.flags()['osric_supports_party'] is True
    potions = [item for item in solo.hero('Arden Vale')['inventory']['items'] if item['id'] == 'osric_store_potion_of_healing']
    assert [(item['name'], item['quantity']) for item in potions] == [('Potion of Healing', 2)]
    assert solo.person(OSRIC)['disposition'] == 1


def test_breaking_the_strongbox_open_cools_osric(solo):
    solo.act('Arden Vale', 'inn_lead_wagon', 'search')

    solo.act('Arden Vale', 'inn_wagon_strongbox', 'break')

    beats = solo.last()['beats']
    assert {'kind': 'bond', 'text': 'Osric Hale will remember that.', 'refId': OSRIC, 'down': True} in beats
    assert solo.person(OSRIC)['disposition'] == -2
    assert solo.journal_person('Arden Vale', 'Osric Hale')['disposition'] == 'hostile'


def test_typed_words_choose_a_topic_and_free_speech_changes_nothing(solo):
    solo.talk('Arden Vale', 'Mara')

    solo.send('Arden Vale', 'Ask her about the bell.')
    assert solo.last()['narration'] == LINES[MARA]['mara_bell']
    solo.send('Arden Vale', 'Tell me about those horses of yours.')
    assert solo.last()['narration'] == LINES[MARA]['mara_horses']

    before = solo.snapshot()
    solo.send('Arden Vale', 'What a miserable night for it.')

    assert solo.last()['narration'] == 'Mara Reedwake has nothing to say to that.'
    assert solo.beats() == ['Scripted narrator — use the suggestions, or connect an AI narrator in Settings for free-form play']
    after = solo.snapshot()
    for key in ('knownNpcs', 'flags', 'conversations', 'playerCharacters', 'promises'):
        assert after.get(key) == before.get(key), key
    assert _talk_changes(solo) == []


def test_leaving_stepping_out_travel_and_a_fight_each_end_a_conversation(solo):
    solo.talk('Arden Vale', 'Mara')
    received = solo.leave('Arden Vale', MARA)
    assert _errors(received) == []
    assert solo.last()['narration'] == 'You leave it there with Mara Reedwake.'
    assert solo.snapshot()['conversations'] == {}
    assert solo.view('Arden Vale')['currentScene'].get('conversation') is None

    solo.talk('Arden Vale', 'Mara')
    solo.move('Arden Vale', 'inn_yard', 'Step out to the yard')
    assert solo.snapshot()['conversations'] == {}

    # Any other action of the hero ends it.
    solo.talk('Arden Vale', 'Osric')
    solo.act('Arden Vale', 'inn_wagon_bell', 'use')
    assert solo.snapshot()['conversations'] == {}

    solo.talk('Arden Vale', 'Tovin')
    solo.act('Arden Vale', 'inn_drivers_bench', 'search')
    solo.act('Arden Vale', 'inn_lead_wagon', 'search')
    solo.act('Arden Vale', 'inn_wagon_strongbox', 'unlock')
    solo.talk('Arden Vale', 'Tovin')
    assert solo.snapshot()['conversations']['player_1']['npcId'] == TOVIN
    # The strongbox's search starts the cutthroat's fight.
    solo.act('Arden Vale', 'inn_wagon_strongbox', 'search')
    assert solo.snapshot()['combat']['status'] == 'active'
    assert solo.snapshot()['conversations'] == {}


def test_travel_ends_a_conversation(solo):
    solo.talk('Arden Vale', 'Osric')
    solo.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    assert solo.snapshot()['conversations'] == {}


def test_two_heroes_talk_to_two_people_at_once(party):
    party.talk('Arden Vale', 'Tovin')
    party.move('Seren Ashgrove', 'inn_common_room', 'Go into the common room')
    party.talk('Seren Ashgrove', 'Mara')

    assert {hero: entry['npcId'] for hero, entry in party.snapshot()['conversations'].items()} == {
        party.hero_id('Arden Vale'): TOVIN,
        party.hero_id('Seren Ashgrove'): MARA,
    }
    assert party.conversation('Arden Vale')['name'] == 'Tovin Bell'
    assert party.conversation('Seren Ashgrove')['name'] == 'Mara Reedwake'
    party.topic('Seren Ashgrove', MARA, 'mara_bell')
    assert party.last()['narration'] == LINES[MARA]['mara_bell']
    party.topic('Arden Vale', TOVIN, 'tovin_bell')
    assert party.last()['narration'] == LINES[TOVIN]['tovin_bell']
    assert party.conversation('Seren Ashgrove')['name'] == 'Mara Reedwake'
    # In party play the person turns to the hero by name, and warming names the party.
    party.leave('Arden Vale', TOVIN)
    party.talk('Arden Vale', 'Tovin')
    assert party.last()['narration'] == 'Tovin Bell turns to Arden Vale.'


def test_the_player_view_shows_the_word_never_the_number_and_nothing_unsaid(solo):
    solo.talk('Arden Vale', 'Mara')
    solo.topic('Arden Vale', MARA, 'mara_bell')
    view = json.dumps(solo.view('Arden Vale'), ensure_ascii=False)

    for npc in PEOPLE.values():
        assert npc['secret'] not in view
        for topic in npc.get('topics') or []:
            if topic['id'] != 'mara_bell':
                assert topic['line'] not in view, topic['id']
        for key in ('"topics"', '"reacts"', '"callbacks"', '"caresAbout"', '"remembers": [{', '"talk"'):
            assert key not in view
    assert next(npc for npc in solo.view('Arden Vale')['knownNpcs'] if npc['id'] == MARA)['disposition'] == 'neutral'


# G. Speech acts, memory, callbacks --------------------------------------------------------------------------------


def _four_asks(table: TalkTable, hero: str = 'Arden Vale') -> None:
    for topic_id in ('tovin_wheels', 'tovin_horses', 'tovin_bench', 'tovin_bell'):
        table.topic(hero, TOVIN, topic_id)


def test_a_promise_in_the_heros_own_words_is_remembered_and_kept_open(solo):
    solo.talk('Arden Vale', 'Tovin')

    solo.send('Arden Vale', "I promise I'll find Wenna for you.")

    assert solo.last()['narration'] == 'Tovin Bell takes your word.'
    assert solo.beats() == ['A promise made — "I promise I\'ll find Wenna for you."', 'Tovin Bell warms to you.', 'Inspiration — your word given']
    assert solo.person(TOVIN)['remembers'][-1] | {'turn': None} == {'heroId': 'player_1', 'key': 'promised', 'words': "I promise I'll find Wenna for you.", 'turn': None}
    view = solo.view('Arden Vale')
    assert view['journal']['yourWord'] == [{'id': _talk_changes(solo)[0]['id'], 'text': "I promise I'll find Wenna for you.", 'to': 'Tovin Bell', 'status': 'open'}]
    assert solo.journal_person('Arden Vale', 'Tovin Bell')['remembers'] == ['you gave your word: "I promise I\'ll find Wenna for you."']

    solo.send('Arden Vale', 'I swear it.')
    assert solo.beats() == ['A promise made — "I swear it."']
    assert solo.person(TOVIN)['disposition'] == 1


def test_a_promise_in_maras_hearing_reaches_her_own_promise(solo):
    solo.talk('Arden Vale', 'Mara')

    solo.send('Arden Vale', 'I swear I will bring you word of your drivers.')

    assert solo.last()['narration'] == LINES[MARA]['mara_promise']
    assert solo.flags()['promised_mara_word'] is True
    assert solo.view('Arden Vale')['journal']['yourWord'][0]['id'] == 'promised_mara_word'


def test_showing_mara_the_ledger_moves_her_once_and_tovin_shrugs(solo):
    _read_the_ledger(solo)
    solo.talk('Arden Vale', 'Mara')

    solo.send('Arden Vale', 'Look at this. The caravan ledger, every name cut out.')

    assert solo.last()['narration'] == 'Mara Reedwake looks at it for a long moment.'
    assert solo.beats() == ['Mara Reedwake warms to you.']
    assert solo.journal_person('Arden Vale', 'Mara Reedwake')['remembers'] == ['you showed them the Erased Caravan Ledger']
    solo.send('Arden Vale', 'Here is the ledger again.')
    assert (solo.last()['narration'], solo.beats()) == ('Mara Reedwake looks, and shrugs.', [])
    solo.leave('Arden Vale', MARA)
    solo.move('Arden Vale', 'inn_yard', 'Step out to the yard')
    solo.talk('Arden Vale', 'Tovin')
    solo.send('Arden Vale', 'Here is the ledger I read.')
    assert solo.last()['narration'] == 'Tovin Bell looks, and shrugs.'
    # Words that name what an object offers are that action, and end the conversation.
    solo.send('Arden Vale', 'I look at the ledger again.')
    assert solo.last()['narration'] == 'You have already examined the Caravan Ledger.'
    assert solo.snapshot()['conversations'] == {}


def test_a_threat_that_works_spares_the_next_check_and_an_apology_mends_it_once(solo):
    solo.talk('Arden Vale', 'Tovin')
    _four_asks(solo)

    solo.send('Arden Vale', "Tell me what you took, or I'll hurt you.")

    assert solo.last()['narration'] == 'Tovin Bell gives way.'
    assert solo.beats() == ['Intimidation check: 15 — success', 'Tovin Bell will remember that.']
    assert solo.person(TOVIN)['remembers'][-1]['key'] == 'threatened'
    solo.topic('Arden Vale', TOVIN, 'tovin_gently')
    assert 'check' not in _talk_changes(solo)[0]
    assert solo.flags()['tovin_button_revealed'] is True

    solo.send('Arden Vale', "I'm sorry. I should not have threatened you.")
    assert solo.last()['narration'] == 'Tovin Bell lets it go.'
    assert [entry['key'] for entry in solo.person(TOVIN)['remembers']] == []
    assert solo.person(TOVIN)['disposition'] == 1
    solo.send('Arden Vale', 'Forgive me, truly.')
    assert solo.last()['narration'] == 'Tovin Bell has nothing to say to that.'


def test_an_offer_meets_what_a_person_wants_or_they_are_not_for_sale(solo):
    solo.edit(lambda snapshot: next(npc for npc in snapshot['campaignPack']['catalog']['npcs'] if npc['id'] == MARA).update(wants=['coin:5']))
    solo.talk('Arden Vale', 'Mara')

    solo.send('Arden Vale', "I'll pay you 10 gold for what you know.")

    assert solo.last()['narration'].split('\n\n')[0] == 'Mara Reedwake takes it.'
    assert solo.hero('Arden Vale')['inventory']['currency']['gp'] == 20
    assert solo.person(MARA)['disposition'] == 1
    assert solo.person(MARA)['talk']['waived'] == ['player_1']
    solo.leave('Arden Vale', MARA)
    solo.move('Arden Vale', 'inn_yard', 'Step out to the yard')
    solo.talk('Arden Vale', 'Tovin')
    solo.send('Arden Vale', "I'll pay you 5 gold.")
    assert solo.last()['narration'].split('\n\n')[0] == 'Tovin Bell is not for sale.'


def test_claims_osrics_authored_reaction_and_the_default(solo, monkeypatch):
    solo.talk('Arden Vale', 'Osric')

    solo.send('Arden Vale', 'I was sent by the wardens of the road.')

    assert solo.last()['narration'] == PEOPLE[OSRIC]['reacts'][0]['line']
    assert solo.beats() == ['Deception check: 15 — success', 'Osric Hale warms to you.']
    assert solo.flags()['osric_yields_wagons'] is True
    assert solo.person(OSRIC)['remembers'][-1]['key'] == 'claimed_authority'
    _rolls(monkeypatch, 2)
    solo.send('Arden Vale', 'I am here on behalf of the crown.')
    assert solo.last()['narration'] == PEOPLE[OSRIC]['reacts'][0]['failLine']
    assert (solo.person(OSRIC)['disposition'], solo.person(OSRIC)['remembers'][-1]['key']) == (-1, 'caught_lying')
    assert solo.journal_person('Arden Vale', 'Osric Hale')['remembers'] == ['you were caught lying', 'you claimed to act with authority']

    _rolls(monkeypatch, 15)
    solo.leave('Arden Vale', OSRIC)
    solo.talk('Arden Vale', 'Tovin')
    solo.send('Arden Vale', 'I am a warden of the old roads.')
    assert solo.last()['narration'] == 'Tovin Bell takes you at your word.'
    assert solo.person(TOVIN)['remembers'][-1]['key'] == 'claimed_identity'


def _claim_then_return(table: TalkTable) -> None:
    """Osric believes the hero was sent; the party goes to the road and comes back to the Lantern Post."""

    table.talk('Arden Vale', 'Osric')
    table.send('Arden Vale', 'I was sent by the wardens of the road.')
    table.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    table.operator([{'type': 'combat.end', 'status': 'ended', 'endReason': 'all_enemies_defeated'}])
    table.travel('Arden Vale', 'loc_lantern_post_inn', 'The Lantern Post Inn')


RIDER = PEOPLE[OSRIC]['callbacks'][0]['text']


def test_osrics_rider_comes_back_and_owning_the_lie_wins_him(solo):
    _claim_then_return(solo)

    assert solo.last()['narration'].split('\n\n')[0] == RIDER
    assert solo.flags()['rider_expects_report'] is True
    solo.talk('Arden Vale', 'Osric')
    # Say topics follow the asks: the rider's two wait behind the first four, and can be named meanwhile.
    for topic_id in ('osric_missing', 'osric_came_back', 'osric_stores', 'osric_letter'):
        solo.topic('Arden Vale', OSRIC, topic_id)
    assert solo.labels('Arden Vale') == ['Ask him to stand with you', 'Admit you were never sent', 'Keep up the claim', *TALKING]

    solo.topic('Arden Vale', OSRIC, 'osric_admit')

    assert solo.last()['narration'] == LINES[OSRIC]['osric_admit']
    assert [entry['key'] for entry in solo.person(OSRIC)['remembers']] == ['owned_the_lie']
    assert solo.person(OSRIC)['disposition'] == 1
    assert 'Keep up the claim' not in solo.labels('Arden Vale')
    # A callback fires once.
    solo.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    solo.travel('Arden Vale', 'loc_lantern_post_inn', 'The Lantern Post Inn')
    assert RIDER not in solo.last()['narration']


@pytest.mark.parametrize(('roll', 'outcome'), [(15, 'named'), (2, 'caught')])
def test_keeping_up_the_claim_before_the_rider(solo, monkeypatch, roll, outcome):
    _claim_then_return(solo)
    solo.talk('Arden Vale', 'Osric')
    _rolls(monkeypatch, roll)

    solo.topic('Arden Vale', OSRIC, 'osric_keep_claim')

    if outcome == 'named':
        assert solo.last()['narration'] == LINES[OSRIC]['osric_keep_claim']
        assert solo.flags()['named_in_wardens_report'] is True
    else:
        topic = next(topic for topic in PEOPLE[OSRIC]['topics'] if topic['id'] == 'osric_keep_claim')
        assert solo.last()['narration'] == topic['check']['failLine']
        assert (solo.person(OSRIC)['disposition'], solo.person(OSRIC)['remembers'][-1]['key']) == (-2, 'caught_lying')
        assert 'named_in_wardens_report' not in solo.flags()


def test_tovin_remembers_who_stood_over_him(solo):
    # W9-SCENE has not landed: the memory guarding him would leave is set by hand.
    def guarded(snapshot):
        tovin = next(npc for npc in snapshot['knownNpcs'] if npc['id'] == TOVIN)
        tovin['remembers'] = [{'heroId': 'player_1', 'key': 'protected', 'turn': 0}]

    solo.edit(guarded)
    _one_cutthroat(solo.app, solo.ids['session_id'])
    for target, action in (('inn_drivers_bench', 'search'), ('inn_lead_wagon', 'search'), ('inn_wagon_strongbox', 'unlock')):
        solo.act('Arden Vale', target, action)
    solo.act('Arden Vale', 'inn_wagon_strongbox', 'search')
    assert solo.snapshot()['combat']['status'] == 'active'

    narrations = []
    for _round in range(12):
        if solo.snapshot()['combat']['status'] != 'active':
            break
        solo.take_turn('Arden Vale')
        narrations.append(solo.last()['narration'])

    callback = PEOPLE[TOVIN]['callbacks'][0]['text']
    assert any(narration.startswith(callback) for narration in narrations)
    assert solo.flags()['tovin_button_revealed'] is True
    assert 'silver_drivers_button' in {item['id'] for item in solo.hero('Arden Vale')['inventory']['items']}
    assert solo.person(TOVIN)['disposition'] == 1


def _reach_the_ending(snapshot: dict) -> None:
    """The road-stone renewed: the adventure's last checkpoint is reached."""

    pack = snapshot['campaignPack']
    checkpoints = [checkpoint['id'] for checkpoint in pack['checkpoints']]
    pack.update(activeCheckpointId=checkpoints[-1], completedCheckpointIds=checkpoints[:-1])
    snapshot['flags'].update(ward_renewed=True, travelers_freed=True)


def test_maras_promise_is_left_open_at_an_ending_until_she_is_told(solo):
    solo.talk('Arden Vale', 'Mara')
    solo.send('Arden Vale', 'I promise to bring word of the drivers.')
    solo.leave('Arden Vale', MARA)
    solo.edit(_reach_the_ending)

    view = solo.view('Arden Vale')
    assert view['campaignPack']['ending']['title'] == 'The Road Remembers'
    assert view['journal']['yourWord'] == [{'id': 'promised_mara_word', 'text': 'Promise to bring word of the drivers', 'to': 'Mara Reedwake', 'status': 'left open'}]

    solo.send('Arden Vale', 'Arden Vale takes heart.', {'kind': 'inspiration', 'source': 'scene_panel'})
    solo.talk('Arden Vale', 'Mara')
    solo.topic('Arden Vale', MARA, 'mara_tell_drivers')

    assert solo.last()['narration'] == LINES[MARA]['mara_tell_drivers']
    assert solo.beats() == ['Inspiration — your word kept']
    assert solo.view('Arden Vale')['journal']['yourWord'][0]['status'] == 'kept'


# C. With an AI narrator: the brief, the rules and the model calls -----------------------------------------------


class FakeHelper:
    """The pre-narration helper: answers the next queued reply (else ``none``) and counts its calls."""

    model_name = 'deepseek-v4-flash'

    def __init__(self) -> None:
        self.calls = 0
        self.replies: list[dict] = []
        self.prompts: list[str] = []

    def generate(self, request):
        self.calls += 1
        self.prompts.append(request.prompt)
        reply = self.replies.pop(0) if self.replies else {'act': 'none'}
        return ProviderResponse(text=json.dumps(reply), provider='deepseek', model=self.model_name)


@pytest.fixture()
def ai(app, client, socketio, monkeypatch):
    from aidm_server.game_state.orchestration import turn_pipeline
    from tests.ai_narrator import AiTable

    narrator = ai_narration(app, monkeypatch)
    _seeded(monkeypatch)
    app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
    helper = FakeHelper()
    monkeypatch.setattr(speech_acts, 'get_helper_provider', lambda *_args, **_kwargs: helper)
    read: list[int] = []
    real = turn_pipeline.extract_post_dm_outcomes
    monkeypatch.setattr(turn_pipeline, 'extract_post_dm_outcomes', lambda **kwargs: read.append(1) or real(**kwargs))
    table = AiTable(app, client, socketio, narrator)
    table.helper, table.read = helper, read
    return table


def _topic_intent(npc_id: str, topic_id: str, label: str) -> dict:
    return {'action_intent': {'kind': 'talk', 'source': 'scene_panel', 'text': label, 'talk': {'action': 'topic', 'npc_id': npc_id, 'topic_id': topic_id}}}


def _model_calls(table, play) -> int:
    """The narrator, pre-narration helper and post-narration helper calls ``play`` made."""

    before = (len(table.narrator.prompts), table.helper.calls, len(table.read))
    play()
    return len(table.narrator.prompts) - before[0] + table.helper.calls - before[1] + len(table.read) - before[2]


def _brief_lines(table) -> dict[str, str]:
    return {line.split(':', 1)[0]: line for line in table.brief().splitlines() if ':' in line}


def test_with_an_ai_narrator_what_a_person_was_written_to_say_is_told_as_written_by_the_engine(ai):
    """A greeting, a topic and leaving reach no model: the words are the pack's own, told at once. A model asked
    to "convey" a written line has dropped it and invented a gift and a lead instead. Words with no written answer
    go to the narrator, who speaks as that person knowing what was told and what may be."""

    assert _model_calls(ai, lambda: ai.reply('I talk to Mara.')) == 0
    assert MARA_GREETING.split(',')[0].lstrip('"') in ai.turn().dm_output
    assert ai.turn().llm_provider == 'fallback'

    topic = _topic_intent(MARA, 'mara_caravan', 'Ask about the caravan')
    assert _model_calls(ai, lambda: ai.send('Ask about the caravan', **topic)) == 0
    assert LINES[MARA]['mara_caravan'] in ai.turn().dm_output
    assert ai.turn().llm_provider == 'fallback'

    # The pre-narration helper once (no cue word), then the narrator.
    assert _model_calls(ai, lambda: ai.reply('What a miserable night.', 'Mara shrugs.')) == 2
    lines = _brief_lines(ai)
    assert 'The hero spoke to Mara Reedwake' in lines['Did']
    assert lines['Conversation'] == (
        f"Conversation: Arden Vale is talking with Mara Reedwake. Personality: {PEOPLE[MARA]['personality']} Disposition: neutral."
    )
    assert LINES[MARA]['mara_caravan'] in lines['Already told']
    assert lines['Remembers of Arden Vale'] == 'Remembers of Arden Vale: nothing yet. They speak from what they remember of this hero.'
    assert lines['May tell if asked'] == 'May tell if asked: ' + ' | '.join(LINES[MARA][topic_id] for topic_id in ('mara_horses', 'mara_strangers', 'mara_bell'))
    # Her secret, and what she tells only once warm, are nowhere in the prompt.
    assert PEOPLE[MARA]['secret'] not in ai.narrator.prompts[-1]
    assert LINES[MARA]['mara_who_she_lost'] not in ai.narrator.prompts[-1]
    assert ENGINE_BRIEF_RULES.split('\n')[-1] in ai.narrator.prompts[-1]
    assert ai.turn().llm_provider == 'deepseek'

    leave = {'action_intent': {'kind': 'talk', 'source': 'scene_panel', 'text': 'Leave it there.', 'talk': {'action': 'leave', 'npc_id': MARA}}}
    assert _model_calls(ai, lambda: ai.send('Leave it there.', **leave)) == 0
    assert ai.turn().dm_output == 'You leave it there with Mara Reedwake.'


def test_with_an_ai_narrator_words_with_no_cue_ask_the_helper_once(ai):
    ai.reply('I talk to Mara.')

    assert _model_calls(ai, lambda: ai.reply('What a miserable night.', 'Mara shrugs.')) == 2
    assert 'The hero spoke to Mara Reedwake' in _brief_lines(ai)['Did']
    # Words with a cue are the engine's alone, and her written answer to a promise is told without a model.
    assert _model_calls(ai, lambda: ai.reply('I promise you, I will be careful.')) == 0
    assert ai.helper.calls == 1


@pytest.mark.parametrize(
    ('words', 'answer', 'result'),
    [
        ('You can count on me to bring them home.', {'act': 'promise', 'text': 'bring them home'}, 'A promise made'),
        ('And the old ringing thing on the wagon, what is the story there?', {'act': 'ask', 'about': 'mara_bell'}, 'mara_bell'),
        ('Have a look, the names are all gone.', {'act': 'show', 'about': 'clue_erased_caravan_ledger'}, 'Mara Reedwake warms to you.'),
        ('Mind your tongue with me.', {'act': 'threaten'}, 'Intimidation check: 15 — success'),
        ('The wardens of the road are my masters.', {'act': 'claim', 'about': 'authority'}, 'Deception check: 15 — success'),
        ('That came out wrong, it was my fault.', {'act': 'apologise'}, 'nothing'),
        ('Lovely evening.', {'act': 'dance'}, 'nothing'),
    ],
)
def test_the_helpers_answer_is_the_act_the_engine_applies(ai, words, answer, result):
    ai.reply('Arden Vale inspects the Caravan Ledger.', action_intent={'kind': 'object', 'source': 'scene_panel', 'text': 'x', 'object': {'id': 'inn_caravan_ledger', 'action': 'inspect'}})
    ai.reply('I talk to Mara.')
    ai.helper.replies.append(answer)

    ai.reply(words)

    beats = [beat['text'] for beat in safe_json_loads(ai.turn().metadata_json, {}).get('storyBeats') or []]
    if result == 'mara_bell':
        assert LINES[MARA]['mara_bell'] in ai.turn().dm_output
    elif result == 'nothing':
        assert 'The hero spoke to Mara Reedwake' in _brief_lines(ai)['Did']
    else:
        assert any(beat.startswith(result) for beat in beats), beats
    assert ai.helper.calls == 1


def test_the_brief_tells_what_a_person_remembers_and_what_warmth_opens(ai):
    ai.reply('I talk to Mara.')
    ai.send('Promise', **_topic_intent(MARA, 'mara_promise', 'Promise to bring word of the drivers'))
    ai.reply('What a miserable night.', 'Mara shrugs.')

    lines = _brief_lines(ai)
    assert lines['Remembers of Arden Vale'].startswith('Remembers of Arden Vale: you gave your word to bring word of the drivers.')
    assert LINES[MARA]['mara_who_she_lost'] in lines['May tell if asked']
    assert 'Disposition: warm.' in lines['Conversation']


# A. The pack's fields: schema and linter ---------------------------------------------------------------------


def _talking_pack() -> dict:
    pack = _trap_pack()
    pack['locations'][0]['npcIds'] = ['npc_gatekeeper']
    pack['clues'] = [{'id': 'clue_seal', 'name': 'A Seal'}]
    pack['npcs'] = [
        {
            'id': 'npc_gatekeeper',
            'name': 'Hesk the Gatekeeper',
            'locationId': 'start',
            'disposition': 'wary',
            'greeting': '"State your business."',
            'topics': [
                {'id': 'gate_road', 'label': 'the road', 'line': '"Muddy."', 'requires': {'clueIds': ['clue_seal']}},
                {'id': 'gate_open', 'label': 'Ask him to open the gate', 'kind': 'say', 'line': 'He opens it.', 'effects': {'setsFlags': {'gate_open': True}}},
            ],
        }
    ]
    return pack


def _codes(pack: dict, *, strict: bool = False) -> list[tuple[str, str]]:
    return [(issue['code'], issue['path']) for issue in lint_campaign_pack_manifest(pack, workspace_id='owner', strict=strict)['issues']]


def test_the_linter_knows_a_topic_sets_a_story_flag(app):
    with app.app_context():
        assert _codes(_talking_pack(), strict=True) == [('story_flag_set_by_pack', 'storyFlags[0].key')]
        pack = _talking_pack()
        pack['storyFlags'] = []
        assert _codes(pack, strict=True) == []


@pytest.mark.parametrize(
    ('change', 'path'),
    [
        (lambda npc: npc['topics'][0]['requires'].update(clueIds=['clue_missing']), 'npcs[0].topics[0].requires.clueIds'),
        (lambda npc: npc['topics'][0]['requires'].update(itemIds=['item_missing']), 'npcs[0].topics[0].requires.itemIds'),
        (lambda npc: npc['topics'][0]['requires'].update(anyOf=[{'topicIds': ['gate_nothing']}]), 'npcs[0].topics[0].requires.anyOf[0].topicIds'),
        (lambda npc: npc['topics'][0]['requires'].update(encounterResolvedIds=['enc_none']), 'npcs[0].topics[0].requires.encounterResolvedIds'),
        (lambda npc: npc['topics'][1]['effects'].update(discoversLocationIds=['nowhere']), 'npcs[0].topics[1].effects.discoversLocationIds'),
        (lambda npc: npc['topics'][1]['effects'].update(affects=[{'npcId': 'npc_nobody', 'disposition': 1}]), 'npcs[0].topics[1].effects.affects.npcId'),
        (lambda npc: npc['topics'][1]['effects'].update(keepsPromise='never_made'), 'npcs[0].topics[1].effects.keepsPromise'),
        (lambda npc: npc.update(callbacks=[{'id': 'cb', 'when': {'remembers': 'x', 'on': 'topic:gate_nothing'}, 'text': 'Back.', 'offers': ['gate_none']}]), 'npcs[0].callbacks[0].offers'),
        (lambda npc: npc.update(caresAbout=['clue_nope']), 'npcs[0].caresAbout'),
    ],
)
def test_unknown_ids_in_a_persons_talk_are_errors(app, change, path):
    pack = _talking_pack()
    pack['storyFlags'] = []
    change(pack['npcs'][0])
    with app.app_context():
        assert ('talk_unknown_reference', path) in _codes(pack)
        assert all(severity == 'error' for severity, code, issue_path in [(issue['severity'], issue['code'], issue['path']) for issue in lint_campaign_pack_manifest(pack, workspace_id='owner')['issues']] if code == 'talk_unknown_reference')


def test_an_objects_or_an_outcomes_affects_must_name_a_person(app):
    pack = _talking_pack()
    pack['storyFlags'] = []
    pack['locations'][0]['interactables'] = [
        {'id': 'gate_lever', 'name': 'Gate Lever', 'kind': 'object', 'allowedActions': ['break'], 'affects': {'break': [{'npcId': 'npc_ghost', 'disposition': -1}]}}
    ]
    with app.app_context():
        assert ('talk_unknown_reference', 'locations[0].interactables[0].affects.break.npcId') in _codes(pack)


def test_the_schema_holds_a_topic_to_its_shape(client):
    pack = _talking_pack()
    del pack['npcs'][0]['topics'][0]['line']
    response = client.post('/api/campaigns/import-pack', json={'pack': pack})
    assert (response.status_code, response.get_json()['error']) == (400, 'npcs[0].topics[0].line is required.')

    pack = _talking_pack()
    pack['npcs'][0]['topics'][0]['kind'] = 'shout'
    response = client.post('/api/campaigns/import-pack', json={'pack': pack})
    assert (response.status_code, response.get_json()['error']) == (400, 'npcs[0].topics[0].kind must be one of: ask, say.')
    assert client.post('/api/campaigns/import-pack', json={'pack': _talking_pack()}).status_code == 201


# Recognition, effects and journal rows in isolation ------------------------------------------------------------


@pytest.mark.parametrize(
    ('words', 'act'),
    [
        ('I give you my word.', {'act': 'promise'}),
        ('I will find them, I swear.', {'act': 'promise'}),
        ('Here is the token.', {'act': 'show', 'about': 'token'}),
        ('Talk or I hurt you.', {'act': 'threaten'}),
        ('Last chance.', {'act': 'threaten'}),
        ('I can give you 5 gp for it.', {'act': 'offer'}),
        ('In exchange, the token is yours.', {'act': 'offer', 'about': 'token'}),
        ('I serve the reeve.', {'act': 'claim', 'about': 'authority'}),
        ('I am Arden of the wardens.', {'act': 'claim', 'about': 'identity'}),
        ('I am sorry for that.', {'act': 'apologise'}),
        ('I was wrong.', {'act': 'apologise'}),
        ("I'm looking for the drivers.", None),
        ('Show me the way.', None),
    ],
)
def test_each_act_has_its_cue_words(words, act):
    assert speech_acts.cue_act(words, things=[{'id': 'token', 'name': 'Ash-Marked Token'}]) == act


def test_an_outcomes_affects_and_a_broken_promise_apply_to_the_people_they_name():
    state = {
        'campaignPack': {'packId': 'p', 'catalog': {'npcs': [], 'encounters': [{'id': 'enc_gate', 'outcomes': {'victory': {'affects': [{'npcId': 'npc_a', 'disposition': 1, 'line': 'A cheers.'}]}}}]}},
        'knownNpcs': [{'id': 'npc_a', 'name': 'A', 'disposition': 'wary'}],
        'currentScene': {'locationId': 'here', 'activeNpcIds': ['npc_a']},
        'combat': {'status': 'ended', 'flags': {'campaignPackEncounterId': 'enc_gate'}},
        'playerCharacters': [{'id': 'player_1', 'name': 'Hero'}],
        'promises': [{'id': 'oath', 'heroId': 'player_1', 'npcId': 'npc_a', 'text': 'Hold the gate', 'authored': True, 'status': 'open'}],
    }
    [affect] = conversations.talk_followup_changes(state, {'id': 'end', 'type': 'combat.end', 'endReason': 'all_enemies_defeated', 'turnId': 3})
    assert (affect['operation'], affect['delta'], affect['line']) == ('affect', 1, 'A cheers.')
    assert conversations.apply_talk_change(state, affect) == {'bonds': [{'npcId': 'npc_a', 'name': 'A', 'delta': 1, 'line': 'A cheers.'}]}
    assert conversations.disposition_word(state['knownNpcs'][0]['disposition']) == 'neutral'

    broken = {**affect, 'operation': 'callback', 'heroId': 'player_1', 'callbackId': 'cb', 'effects': {'breaksPromise': 'oath'}}
    conversations.apply_talk_change(state, broken)
    assert conversations.journal_promises(state, {'player_1'}, ended=False) == []
    assert conversations.journal_promises(state, {'player_1'}, ended=True) == [{'id': 'oath', 'text': 'Hold the gate', 'to': 'A', 'status': 'broken'}]


def test_a_private_topic_waits_until_nobody_else_is_in_the_room():
    state = {
        'campaignPack': {'packId': 'p', 'catalog': {'npcs': [{'id': 'npc_a', 'name': 'A', 'topics': [{'id': 'quiet', 'label': 'the quiet thing', 'line': 'Shh.', 'private': True}]}]}},
        'knownNpcs': [{'id': 'npc_a', 'name': 'A'}, {'id': 'npc_b', 'name': 'B'}],
        'currentScene': {'locationId': 'here', 'activeNpcIds': ['npc_a', 'npc_b']},
        'playerCharacters': [{'id': 'player_1', 'name': 'Hero'}],
    }
    assert conversations.available_topics(state, 'player_1', 'npc_a') == []
    state['currentScene']['activeNpcIds'] = ['npc_a']
    assert [topic['id'] for topic in conversations.available_topics(state, 'player_1', 'npc_a')] == ['quiet']


def test_words_spoken_to_someone_else_by_name_are_said_to_them(solo):
    solo.talk('Arden Vale', 'Tovin')

    solo.send('Arden Vale', 'Osric, what of the stores?')

    assert solo.snapshot()['conversations']['player_1']['npcId'] == OSRIC
    assert solo.last()['narration'] == f'"Say it quickly," Osric says.\n\n{LINES[OSRIC]["osric_stores"]}'
    # Naming someone in passing keeps the conversation where it is.
    solo.send('Arden Vale', 'And what does Tovin know about the horses?')
    assert solo.snapshot()['conversations']['player_1']['npcId'] == OSRIC


def test_words_spoken_by_name_to_someone_next_door_step_out_to_them(solo):
    """From Mara's common room, "Tovin, …" is said to Tovin in the yard: the hero steps out first, as any action
    aimed next door does, and the talk with Mara is over."""

    solo.talk('Arden Vale', 'Mara')
    assert solo.zone('Arden Vale') == 'inn_common_room'

    assert _errors(solo.send('Arden Vale', 'Tovin, what is wrong with the wheels?')) == []

    assert solo.zone('Arden Vale') == 'inn_yard'
    assert solo.snapshot()['conversations']['player_1']['npcId'] == TOVIN
    assert LINES[TOVIN]['tovin_wheels'] in solo.last()['narration']
    # Naming him in passing, from her room, is still said to her.
    solo.talk('Arden Vale', 'Mara')
    solo.send('Arden Vale', 'What does Tovin know about the horses?')
    assert solo.zone('Arden Vale') == 'inn_common_room'
    assert solo.snapshot()['conversations']['player_1']['npcId'] == MARA


def test_players_see_a_pack_persons_word_and_anyone_elses_disposition_as_written():
    assert conversations.shown_disposition({'disposition': 1}) == 'warm'
    assert conversations.shown_disposition({'disposition': 'cautious_helpful', 'source': 'campaign_pack'}) == 'neutral'
    assert conversations.shown_disposition({'disposition': 'wary', 'source': 'campaign_pack'}) == 'wary'
    assert conversations.shown_disposition({'disposition': 'friendly'}) == 'friendly'
    assert conversations.shown_disposition({}) is None


def test_an_attack_on_someone_present_is_never_words_to_them(solo):
    snapshot = solo.snapshot()
    assert speech_acts.conversation_words(snapshot, actor_id='player_1', player_id=solo.players['Arden Vale'], message='I attack Osric') is None
    assert speech_acts.conversation_words(snapshot, actor_id='player_1', player_id=solo.players['Arden Vale'], message='Osric, a word.') == {'action': 'words', 'npc_id': OSRIC}
