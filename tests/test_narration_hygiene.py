"""AI narration is cleaned before it is stored and sent: no closing prompt, no markdown, no restated or invented
gains, no roll requests for things the engine rolls itself."""

from __future__ import annotations

from copy import deepcopy

import pytest

from aidm_server.narration_hygiene import clean_ai_narration
from aidm_server.scripted_narrator import TEMPLATES
from tests.ai_narrator import AiTable, ai_narration

INN = {
    'did': [],
    'notDone': [],
    'here': {
        'objects': [{'name': "Driver's Bench", 'labels': ["Search under the driver's bench"]}, {'name': 'Lead Wagon', 'labels': []}],
        'items': ['15 gp', 'Pale Road-Stone Fragment', 'Caravan Strongbox Key'],
        'waysOnward': ['The Old North Road'],
        'shops': ["Mara's Counter"],
    },
    'place': 'The Lantern Post Inn',
    'heroes': ['Arden Vale'],
    'gains': {'xp': 0, 'coins': {}, 'items': []},
    'engineOwned': [
        "Driver's Bench", 'Lead Wagon', 'Wagon Strongbox', '15 gp', 'Pale Road-Stone Fragment', 'Caravan Strongbox Key',
        'The Old North Road', "Mara's Counter",
    ],
}


def _hint(brief: dict | None = None, **hint) -> dict:
    return {'requires_roll': False, **hint, 'state_pipeline': {'engineBrief': brief if brief is not None else INN}}


def _gains(**gains) -> dict:
    brief = deepcopy(INN)
    brief['gains'] = {'xp': 0, 'coins': {}, 'items': [], **gains}
    return brief


def _clean(text: str, rules_hint: dict | None = None) -> str:
    return clean_ai_narration(text, rules_hint or _hint()).text


@pytest.mark.parametrize(
    ('text', 'brief', 'expected'),
    [
        # The lead's examples: coins found but not taken, and rewards the beats already report.
        (
            "You lift the lid. Inside, packed in straw, lie a fragment of pale road-stone and a drover's purse. You gain 15 gp.",
            INN,
            "You lift the lid. Inside, packed in straw, lie a fragment of pale road-stone and a drover's purse.",
        ),
        (
            'The last cutthroat folds into the mud.\n\nArden Vale gains 100 XP, 25 gp, and one Ash-Marked Token.',
            _gains(xp=100, coins={'gp': 25}, items=['Ash-Marked Token']),
            'The last cutthroat folds into the mud.',
        ),
        ('You gain 100 XP. The rain eases.', _gains(xp=100), 'The rain eases.'),
        ('Arden earns 100 experience points and twenty-five gold.', _gains(xp=100, coins={'gp': 25}), 'Arden earns 100 experience points and twenty-five gold.'),
        ('You receive fifteen gold pieces.', _gains(coins={'gp': 15}), ''),
        # Taking what "Did" says was taken stays; taking what was only found does not.
        ('You take the Caravan Strongbox Key.', _gains(items=['Caravan Strongbox Key']), 'You take the Caravan Strongbox Key.'),
        ('You take the key from the straw.', INN, 'You take the key from the straw.'),
        ('You take the key. It is cold.', INN, 'It is cold.'),
        ('You take 15 gp.', _gains(coins={'gp': 15}), 'You take 15 gp.'),
        ('You take 30 gp.', _gains(coins={'gp': 15}), ''),
        # Not about coins or items at all.
        ('You take a moment to steady yourself.', INN, 'You take a moment to steady yourself.'),
        ('Mara gains a little confidence.', _gains(xp=100), 'Mara gains a little confidence.'),
        ('You gain 50 XP for your cleverness.', INN, 'You gain 50 XP for your cleverness.'),
    ],
)
def test_gains_are_told_once_by_the_beats_and_only_when_they_happened(text, brief, expected):
    assert _clean(text, _hint(brief)) == expected


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('The bell is silent.\n\nWhat do you do?', 'The bell is silent.'),
        ('The bell is silent. What do you do next?', 'The bell is silent.'),
        ('Mara turns to you. "What do you do?"', 'Mara turns to you.'),
        ('The yard waits.\n“What now?”', 'The yard waits.'),
        ('What will you do, Arden?', ''),
        ('"What do you want?" Mara asks.', '"What do you want?" Mara asks.'),
        ('What do you do? The bell rings again.', 'What do you do? The bell rings again.'),
    ],
)
def test_a_closing_prompt_to_act_is_dropped_outside_the_opening(text, expected):
    assert _clean(text) == expected


def test_the_opening_may_end_on_its_question():
    assert _clean('Rain on the yard. What do you do?', _hint(message_kind='opening')) == 'Rain on the yard. What do you do?'


def test_markdown_markers_go_and_their_words_stay():
    text = '# The Yard\n*Pale Road-Stone Fragment … 15 gp*\n**Arden** looks at the `bell`, _softly_ humming by the old_gate.'

    assert _clean(text) == 'The Yard\nPale Road-Stone Fragment … 15 gp\nArden looks at the bell, softly humming by the old_gate.'


def test_lines_with_nothing_taken_out_are_kept_exactly():
    text = 'Rain  drums on the canvas.\n\n  The bell is still.'

    assert _clean(text) == 'Rain  drums on the canvas.\n\n  The bell is still.'.strip()


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        # The lead's example: a check of the model's own for the bench's search.
        (
            "You crouch beside the driver's bench and peer into the dark. Make an Intelligence check, DC 13.",
            "You crouch beside the driver's bench and peer into the dark.",
        ),
        ('Make an Intelligence (Investigation) check, DC 13, to search the lead wagon.', ''),
        ('The road climbs into fog. Make a Wisdom (Survival) check, DC 12, to keep to the old north road.', 'The road climbs into fog.'),
        ("Mara eyes your purse at Mara's Counter. Make a Charisma (Persuasion) check to haggle the price down.", "Mara eyes your purse at Mara's Counter."),
        ('The cutthroat lunges. Roll an attack against it.', 'The cutthroat lunges.'),
    ],
)
def test_a_roll_request_for_something_the_engine_rolls_is_dropped(text, expected):
    cleaned = clean_ai_narration(text, _hint())

    assert (cleaned.text, cleaned.engine_rolls_removed) == (expected, True)


@pytest.mark.parametrize(
    'text',
    [
        'You slip behind the lead wagon. Make a Dexterity (Stealth) check, DC 12.',
        'You brace against the slick wall of the inn. Make a Strength (Athletics) check, DC 12.',
        'Tovin watches you closely. Make a Charisma (Persuasion) check, DC 13.',
        'The bandits draw steel. Everyone roll initiative.',
    ],
)
def test_a_roll_request_for_an_improvised_action_stays(text):
    cleaned = clean_ai_narration(text, _hint())

    assert (cleaned.text, cleaned.engine_rolls_removed) == (text, False)


def test_a_roll_the_engine_waits_on_stays():
    text = 'The cutthroat lunges. Roll an attack against it.'
    cleaned = clean_ai_narration(text, _hint(requires_roll=True, roll_type='attack', roll_value=None))

    assert (cleaned.text, cleaned.engine_rolls_removed) == (text, False)


def test_without_a_brief_only_the_engines_own_rolls_and_the_form_are_cleaned():
    hint = {'requires_roll': False}

    assert clean_ai_narration('You gain 15 gp. *Mara* nods. Roll an attack.\n\nWhat now?', hint).text == 'You gain 15 gp. Mara nods.'


def test_the_cleaned_narration_is_what_is_sent_and_stored_and_no_roll_is_asked(app, client, socketio, monkeypatch):
    table = AiTable(app, client, socketio, ai_narration(app, monkeypatch))
    table.reply('Please narrate the opening scene.', message_kind='opening', client_message_id='opening-1')
    gold = table.snapshot()['playerCharacters'][0]['inventory']['currency']['gp']
    reply = (
        "**You** kneel by the driver's bench and run your fingers along the boards. You gain 15 gp.\n\n"
        'Make an Intelligence (Investigation) check, DC 13.\n\n"What do you do?"'
    )

    received = table.send("I investigate the driver's bench.", reply)
    sent = next(event['args'][0] for event in received if event['name'] == 'dm_response_end')
    turn = table.turn()

    assert sent['text'] == "You kneel by the driver's bench and run your fingers along the boards."
    assert turn.dm_output == sent['text']
    assert [event for event in received if event['name'] == 'roll_required'] == []
    assert (turn.requires_roll, turn.outcome_status) == (False, 'resolved')
    assert '"roll_gate"' not in turn.metadata_json
    assert '"reason": "engine_rolls_it"' in turn.metadata_json
    assert table.snapshot()['playerCharacters'][0]['inventory']['currency']['gp'] == gold


def test_a_reply_with_nothing_left_once_cleaned_is_told_the_scripted_way(app, client, socketio, monkeypatch):
    table = AiTable(app, client, socketio, ai_narration(app, monkeypatch))
    table.reply('Please narrate the opening scene.', message_kind='opening', client_message_id='opening-1')

    text = table.reply("I investigate the driver's bench.", "Make an Intelligence (Investigation) check, DC 13, to search the driver's bench.")
    turn = table.turn()

    assert text in TEMPLATES['nothing_happens']
    assert turn.dm_output == text
    assert (turn.requires_roll, turn.outcome_status) == (False, 'resolved')
