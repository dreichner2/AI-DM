"""The engine brief an AI narrator is sent above the player's words: what the engine did, what is not done, what is here."""

from __future__ import annotations

import json

import pytest

from aidm_server.prompt_templates import ENGINE_BRIEF_RULES, parse_dm_user_prompt
from tests.ai_narrator import AiTable, ai_narration

BENCH_TEXT = (
    "The lead wagon's box seat, its boards worn pale by years of drivers. In the dark space beneath it sits a knot "
    'of waxed cloth. Unwrapped, the cloth holds a flat iron key on a greasy thong, its bow stamped with a wagon wheel.'
)
INN_OBJECTS = (
    "objects Caravan Ledger: Read the caravan ledger | Driver's Bench: Look over the driver's bench | "
    'Lead Wagon: Search the lead wagon | Wagon Bell: Ring the wagon bell'
)


@pytest.fixture()
def table(app, client, socketio, monkeypatch):
    narrator = ai_narration(app, monkeypatch)
    table = AiTable(app, client, socketio, narrator)
    table.reply('Please narrate the opening scene.', message_kind='opening', client_message_id='opening-1')
    return table


def _context(prompt: str) -> dict:
    return json.loads(prompt.split('CONTEXT:\n', 1)[1].split('\n\n', 1)[0])


def test_the_brief_for_the_leads_three_sentences(table):
    table.reply("I crouch and search under the driver's bench.")
    bench = table.brief()
    table.reply('I pocket the key and climb into the lead wagon to search it.')
    key = table.brief()
    table.reply('I head up the old north road.')
    road = table.brief()

    assert bench == (
        f'Did: Search under the driver\'s bench (Investigation check: 15 — success): done. What it shows: "{BENCH_TEXT}" '
        'Found, and still lying there until taken: a Caravan Strongbox Key.\n'
        'Not done: nothing\n'
        f"Here (only the engine decides these): {INN_OBJECTS}; loose items Caravan Strongbox Key; "
        "ways onward The Old North Road; shops Mara's Counter\n"
        'The party is at The Lantern Post Inn.'
    )
    assert key == (
        'Did: Took the Caravan Strongbox Key.\n'
        'Not done: Search the lead wagon\n'
        f"Here (only the engine decides these): {INN_OBJECTS}; loose items none; "
        "ways onward The Old North Road; shops Mara's Counter\n"
        'The party is at The Lantern Post Inn.'
    )
    assert road.startswith('Did: Arrived at The Old North Road. A fight begins against Pale Lantern. Pale Lantern uses Lantern-Lure')
    assert road.endswith(
        'New place on the map — Waystation of Scratched Names. New place on the map — Ridge and Culvert Paths. '
        'Met — Radan Greycloak. Met — Veyra Ashcap.\n'
        'Not done: nothing\n'
        'Here (only the engine decides these): objects Leaning Milestone: nothing offered | Abandoned Handcart: nothing offered; '
        'loose items none; ways onward The Lantern Post Inn, Waystation of Scratched Names, Ridge and Culvert Paths; shops none\n'
        'The party is at The Old North Road.'
    )


def test_the_brief_sits_directly_above_the_players_words_with_its_rules(table):
    table.reply('I look around.')
    prompt = table.narrator.prompts[-1]

    assert prompt.endswith(f'\n\n{ENGINE_BRIEF_RULES}\n\nPLAYER INPUT:\nI look around.\n')
    assert prompt.index('RULES_HINT:') < prompt.index('ENGINE THIS TURN') < prompt.index('PLAYER INPUT:')
    # The brief is rendered once, not again inside RULES_HINT, and the prompt still parses.
    assert 'engineBrief' not in prompt
    assert parse_dm_user_prompt(prompt)['player_input'] == 'I look around.'
    assert 'ENGINE THIS TURN' not in table.narrator.prompts[0]


def test_hidden_objects_and_contents_reach_the_narrator_only_in_the_turn_that_reveals_them(table):
    table.reply('I look around.')
    before = table.narrator.prompts[-1]
    objects = {entry['name']: entry for entry in _context(before)['live_world_state']['currentScene']['interactables']}

    for hidden in ('Wagon Strongbox', 'strongbox_coins', 'Caravan Strongbox Key', 'flat iron key', 'bolted beneath'):
        assert hidden not in before
    assert objects["Driver's Bench"] == {
        'id': 'inn_drivers_bench',
        'name': "Driver's Bench",
        'kind': 'container',
        'description': "The lead wagon's box seat. A knot of waxed cloth is wedged in the dark space beneath it.",
        'knowledge': 'player_known',
        'open': True,
        'offeredActions': ["Search under the driver's bench", "Look over the driver's bench"],
    }

    table.reply("I search under the driver's bench.")
    assert 'flat iron key' in table.brief()
    table.reply('I search the lead wagon.')
    revealed = table.brief()
    table.reply('I look around.')
    after = table.brief()

    assert 'Uncovered: Wagon Strongbox.' in revealed
    assert 'Wagon Strongbox: Break the strongbox open' in after
    assert 'flat iron key' not in table.narrator.prompts[-1]
    scene = _context(table.narrator.prompts[-1])['live_world_state']['currentScene']
    assert all('contents' not in entry for entry in scene['interactables'])


def test_words_that_name_an_engine_thing_without_a_result_are_not_done(table):
    table.reply("I run a hand over the driver's bench, then glance at the old north road.")

    assert "Not done: Search under the driver's bench or Look over the driver's bench; Go to The Old North Road" in table.brief()
    assert 'Did: nothing' in table.brief()


@pytest.mark.parametrize(
    ('text', 'kind', 'taken', 'line'),
    [
        ('+15 gp', 'coins', {}, 'Gained 15 gp.'),
        ('+15 gp', 'coins', {'gp': 15}, None),
        ('−50 gp', 'coins', {}, 'Lost 50 gp.'),
        ('+10 gp each', 'coins', {}, 'Each hero gained 10 gp.'),
        ('Arden Vale +21 gp', 'coins', {'gp': 15}, 'Arden Vale gained 6 gp.'),
        ('Seren Ashgrove −5 gp', 'coins', {'gp': 15}, 'Seren Ashgrove lost 5 gp.'),
        ('+100 XP', 'xp', {}, 'Gained 100 XP.'),
        ('+75 XP each', 'xp', {}, 'Each hero gained 75 XP.'),
        ('Arden Vale took the Caravan Strongbox Key', 'item_gained', {}, None),
        ('Seren Ashgrove received the Rope', 'item_gained', {}, 'Seren Ashgrove received the Rope.'),
    ],
)
def test_a_beat_becomes_a_did_line_naming_the_hero_in_party_play(text, kind, taken, line):
    from aidm_server.game_state.orchestration.engine_brief import _beat_line

    assert _beat_line({'kind': kind, 'text': text}, taken, acting_hero='Arden Vale') == line
