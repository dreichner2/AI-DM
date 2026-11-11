"""What you improvise stays true (W9-FREEFORM), through the real socket pipeline.

The coaching yard (``tests/scene_yard_pack.py``, with Tobias Hob waiting unmet on Mill Lane) in Safe Mode unless an AI
narrator is named (``tests/ai_narrator.py``), whose helpers are a fake here: the improvised-fact helper answers what a
test queues (raising a queued exception), every other helper ``{}``. Seeded dice as in
``tests/test_plans_and_stakes.py``: Arden Vale climbs onto the stable roof with Athletics 19 against DC 14.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from aidm_server.contracts import ProviderResponse
from aidm_server.game_state.orchestration.improvised_facts import PROMPT_HEAD, SYSTEM_MESSAGE
from aidm_server.prompt_templates import IMPROVISED_RULE
from tests.ai_narrator import ai_narration
from tests.scene_yard_pack import YardTable, install, yard_pack
from tests.test_plans_and_stakes import HELPER_MODULES, _brief, _dice, _line, _model_calls, _roll_for, _start_fight, safe_mode  # noqa: F401 - the Safe Mode fixture
from tests.test_where_everyone_stands import _errors

ARDEN = 'Arden Vale'
CLIMB = 'I climb onto the stable roof.'
ROPE = 'A knotted rope hangs from the stable roof into the yard.'
OWN = 'Arden Vale: "I climb onto the stable roof." — done.'
DID = 'Did: Improvised — "I climb onto the stable roof.": Athletics 19 vs DC 14, success.'


def _pack() -> dict[str, Any]:
    pack = yard_pack()
    pack['npcs'].append({'id': 'npc_hob', 'name': 'Tobias Hob', 'role': 'miller', 'playerSummary': 'The miller.'})
    pack['locations'][1]['npcIds'] = ['npc_hob']
    return pack


class FactHelper:
    """Every helper model: the improvised-fact helper's queued answers (a JSON object, a string, or an exception it
    raises), ``{}`` for anything else; keeps each request."""

    def __init__(self) -> None:
        self.answers: list[Any] = []
        self.requests: list[Any] = []

    def generate(self, request):
        self.requests.append(request)
        answer: Any = '{}'
        if request.prompt.startswith(PROMPT_HEAD):
            answer = self.answers.pop(0) if self.answers else {'none': True}
        if isinstance(answer, Exception):
            raise answer
        return ProviderResponse(text=answer if isinstance(answer, str) else json.dumps(answer), provider='deepseek', model='helper')

    def asked(self) -> list[Any]:
        return [request for request in self.requests if request.prompt.startswith(PROMPT_HEAD)]

    @property
    def prompts(self) -> list[str]:
        return [request.prompt for request in self.requests]


@pytest.fixture()
def ai(app, client, socketio, monkeypatch):
    narrator = ai_narration(app, monkeypatch)
    _dice(monkeypatch)
    install(monkeypatch, _pack())
    app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
    helper = FactHelper()
    for module in HELPER_MODULES:
        monkeypatch.setattr(f'{module}.get_helper_provider', lambda *_args, **_kwargs: helper)
    table = YardTable(app, client, socketio, party=False)
    table.narrator, table.helper = narrator, helper
    table.act(ARDEN, 'feed_crate', 'inspect')
    yield table
    table.close()


@pytest.fixture()
def scripted(app, client, socketio, safe_mode, monkeypatch):  # noqa: F811 - the fixture imported above
    """Safe Mode, with a fact helper that keeps every request made of it."""

    install(monkeypatch, _pack())
    helper = FactHelper()
    monkeypatch.setattr('aidm_server.game_state.orchestration.improvised_facts.get_helper_provider', lambda *_args, **_kwargs: helper)

    def table(party: bool) -> YardTable:
        made = YardTable(app, client, socketio, party=party)
        made.helper = helper
        return made

    return table


def _improvise(table: YardTable, answer: Any = None) -> None:
    """Arden Vale climbs onto the stable roof, then rolls for it; ``answer`` is the fact helper's."""

    if answer is not None:
        table.helper.answers.append(answer)
    assert _errors(table.send(ARDEN, CLIMB)) == []
    assert _errors(table.send(ARDEN, 'I roll.', _roll_for(table))) == []


def _record(table: YardTable) -> dict[str, Any]:
    return table.last()['pipeline'].get('improvisedFact') or {}


def _improvised(table: YardTable) -> list[str]:
    return [record['text'] for record in table.snapshot()['currentScene'].get('improvisedFacts') or []]


def _wenna(table: YardTable) -> dict[str, Any]:
    return next(npc for npc in table.snapshot()['knownNpcs'] if npc['id'] == 'npc_wenna')


# An accepted fact ----------------------------------------------------------------------------------------------------


def test_a_valid_fact_is_stored_shown_and_binds_the_narrator_now_and_on_the_next_turn(ai):
    answer = {'fact': ROPE, 'effect': None, 'targetId': None, 'about': [], 'removes': None, 'none': False}
    ai.helper.answers.append(answer)
    ai.send(ARDEN, CLIMB)

    assert _model_calls(ai, lambda: ai.send(ARDEN, 'I roll.', _roll_for(ai))) == (1, 1)

    assert ai.facts() == [ROPE]
    turn = ai.last_turn_id()
    assert ai.snapshot()['currentScene']['improvisedFacts'] == [{'text': ROPE, 'heroId': ai.hero_id(ARDEN), 'turn': turn}]
    view = ai.view(ARDEN)['currentScene']
    assert (view['facts'], 'improvisedFacts' in view) == ([ROPE], False)
    assert {'kind': 'change', 'text': ROPE} in ai.last()['beats']
    assert _line(ai, 'Did: ').startswith(f'{DID} Now true here: {ROPE}')
    assert IMPROVISED_RULE.endswith('On a success tell it happening exactly as "Now true here" states, and add nothing else that changes the scene.')
    assert IMPROVISED_RULE in ai.narrator.prompts[-1]
    assert _line(ai, 'Now true here: ') == f'Now true here: {ROPE}'
    assert {key: _record(ai)[key] for key in ('source', 'fact', 'reason', 'effect')} == {'source': 'helper', 'fact': ROPE, 'reason': '', 'effect': ''}
    # The post-narration helper is not asked about the turn.
    assert ai.last()['pipeline']['postDmExtraction']['debug']['reason'] == 'improvised'

    ai.send(ARDEN, 'I look around the yard.')
    assert _line(ai, 'Now true here: ') == f'Now true here: {ROPE}'
    assert IMPROVISED_RULE not in ai.narrator.prompts[-1]


def test_the_helper_is_asked_once_with_only_what_stands_in_the_heros_room(ai):
    _improvise(ai, {'fact': ROPE})

    (request,) = ai.helper.asked()
    assert request.system_message == SYSTEM_MESSAGE
    prompt = request.prompt
    assert prompt.startswith(f'{PROMPT_HEAD}Arden Vale:\n"{CLIMB}"\nAthletics 19 vs DC 14: success.')
    assert 'Place: The Coaching Yard, The Yard.' in prompt
    for line in ('- yard_gate: Yard Gate', '- yard_lantern: Yard Lantern', '- npc_wenna: Wenna Pike', f'- {ai.hero_id(ARDEN)}: Arden Vale'):
        assert line in prompt
    # Nothing next door, nothing hidden, nobody unmet.
    for absent in ('hay_pile', 'wagon_strongbox', 'saddle_cache', 'npc_hob', 'Tobias'):
        assert absent not in prompt
    assert 'Already true here:\n- nothing yet' in prompt
    assert 'Effects: obstruct, distract, expose, conceal, separate, restrain, destroy, protect, take.' in prompt


def test_a_person_the_fact_is_about_remembers_it_and_the_journal_says_so(ai):
    _improvise(ai, {'fact': ROPE, 'about': ['npc_wenna', 'yard_gate', 'npc_wenna']})

    (memory,) = _wenna(ai)['remembers']
    assert memory == {'heroId': ai.hero_id(ARDEN), 'key': 'witnessed', 'words': ROPE, 'turn': ai.last_turn_id()}
    wenna = next(person for person in ai.view(ARDEN)['journal']['people'] if person['id'] == 'npc_wenna')
    # The Story view shows it as "Remembers: A knotted rope hangs from the stable roof into the yard."
    assert wenna['remembers'] == [ROPE.rstrip('.')]

    # Someone in another room saw nothing.
    ai.move(ARDEN, 'stable', 'Go to the stable')
    _improvise(ai, {'fact': 'The stable door is wedged shut with a pitchfork.', 'about': ['npc_wenna']})
    assert 'The stable door is wedged shut with a pitchfork.' in ai.facts()
    assert len(_wenna(ai)['remembers']) == 1


def test_a_valid_effect_applies_without_a_second_roll_and_its_own_fact_is_kept(ai):
    _improvise(ai, {'fact': 'The yard lantern lies smashed in the mud.', 'effect': 'destroy', 'targetId': 'yard_lantern'})

    kept = 'The yard lantern is out; the yard is dark.'
    assert ai.facts() == [kept]
    assert _improvised(ai) == []
    assert ai.snapshot()['currentScene']['zoneConditions'] == {'yard': ['dark']}
    lantern = next(entry for entry in ai.snapshot()['currentScene']['interactables'] if entry['id'] == 'yard_lantern')
    assert lantern['broken'] is True
    (change,) = [change for change in ai.last()['pipeline']['combatAppliedChanges'] if change['type'] == 'scene.effect']
    assert (change['effect'], change['success'], change['fromImprovised'], 'check' in change) == ('destroy', True, True, False)
    # The player's own roll is the only one; the effect is told by the improvised line, not as a plan.
    rolls = ai.last()['pipeline']['dmContextPacket']['storyRolls']
    assert [(roll['label'], roll['total'], roll['against']) for roll in rolls] == [('Athletics check', 19, {'kind': 'DC', 'value': 14})]
    assert _line(ai, 'Did: ').startswith(f'{DID} Now true here: {kept}')
    assert 'Effect — ' not in _brief(ai)
    assert _record(ai)['effect'] == 'destroy'


@pytest.mark.parametrize(
    ('effect', 'target'),
    [('distract', 'yard_gate'), ('conceal', 'hay_pile'), ('fly', 'yard_gate'), ('take', 'yard_lantern'), ('destroy', 'npc_wenna')],
    ids=['not for its tags', 'next door', 'no such effect', 'not by the table', 'not a thing'],
)
def test_an_invalid_effect_and_target_leave_the_plain_fact(ai, effect, target):
    _improvise(ai, {'fact': ROPE, 'effect': effect, 'targetId': target})

    assert ai.facts() == [ROPE]
    assert _improvised(ai) == [ROPE]
    assert not [change for change in ai.last()['pipeline']['combatAppliedChanges'] if change['type'] == 'scene.effect' and change.get('effect')]
    assert _record(ai)['effect'] == ''


# What the engine refuses ---------------------------------------------------------------------------------------------


REFUSED = [
    ({'none': True}, 'none'),
    ('not json at all', 'invalid_answer'),
    ({'effect': 'destroy', 'targetId': 'yard_lantern'}, 'invalid_answer'),
    ({'fact': 'The wagon strongbox lies open in the straw.'}, 'names_hidden'),
    ({'fact': 'A ladder now leans up into the loft.'}, 'names_hidden'),
    ({'fact': 'Tobias Hob is watching from the gate.'}, 'names_unmet_person'),
    ({'fact': 'Arden Vale pockets a handful of silver coins.'}, 'gives_or_takes'),
    ({'fact': 'Arden Vale has the road-stone fragment tucked in a boot.'}, 'gives_or_takes'),
    ({'fact': 'Wenna Pike now knows the password to the mill.'}, 'gives_or_takes'),
    ({'fact': 'Wenna Pike is dead in the mud.'}, 'kills_or_turns'),
    ({'fact': 'Wenna Pike joins the party for the road ahead.'}, 'kills_or_turns'),
    ({'fact': 'A knotted rope hangs from the stable roof into the yard, swaying in the wind over the mud, the straw and the old wagon.'}, 'length'),
    ({'fact': 'Rope.'}, 'length'),
    ({'fact': 'The rope holds. Wenna Pike watches it sway.'}, 'not_one_sentence'),
    ({'fact': 'A knotted rope hangs from the stable roof'}, 'not_one_sentence'),
    ({'fact': 'A knotted rope hangs\nfrom the stable roof.'}, 'not_one_sentence'),
    ({'fact': 'A rope with 3 knots hangs from the stable roof.'}, 'digits'),
    ({'fact': ROPE, 'removes': 'The yard is quiet.'}, 'unknown_removes'),
]


@pytest.mark.parametrize(('answer', 'reason'), REFUSED, ids=[f'{reason}-{index}' for index, (_answer, reason) in enumerate(REFUSED)])
def test_a_refused_fact_records_nothing_and_the_turn_is_told_as_before(ai, answer, reason):
    ai.helper.answers.append(answer)
    ai.send(ARDEN, CLIMB)

    assert _model_calls(ai, lambda: ai.send(ARDEN, 'I roll.', _roll_for(ai))) == (1, 1)

    assert ai.facts() == []
    assert _improvised(ai) == []
    assert _record(ai)['reason'] == reason
    assert _line(ai, 'Did: ').startswith(f'{DID} The engine changed nothing.')
    assert not [beat for beat in ai.last()['beats'] if beat['kind'] == 'change']
    assert 'remembers' not in _wenna(ai)


def test_a_person_in_another_room_is_refused(ai):
    ai.move(ARDEN, 'stable', 'Go to the stable')

    _improvise(ai, {'fact': 'Wenna Pike is watching the stable door.'})

    assert _record(ai)['reason'] == 'person_not_here'
    assert ai.facts() == []


@pytest.mark.parametrize('failure', [TimeoutError('read timed out'), RuntimeError('provider unavailable')], ids=['timeout', 'failure'])
def test_a_helper_that_times_out_or_fails_leaves_the_engines_own_record(ai, failure):
    _improvise(ai, failure)

    assert ai.facts() == [OWN]
    assert (_record(ai)['source'], _record(ai)['reason'], bool(_record(ai)['helper']['helperError'])) == ('engine', '', True)
    assert _line(ai, 'Did: ').startswith(f'{DID} Now true here: {OWN}')


# Removing, the cap, and coming back ----------------------------------------------------------------------------------


def test_removes_undoes_an_earlier_improvised_fact(ai):
    _improvise(ai, {'fact': ROPE})
    loose = 'The knotted rope lies coiled in the mud of the yard.'

    _improvise(ai, {'fact': loose, 'removes': ROPE})

    assert ai.facts() == [loose]
    assert _improvised(ai) == [loose]
    (_ask, again) = ai.helper.asked()
    assert f'- {ROPE} (improvised)' in again.prompt


def test_a_place_keeps_six_improvised_facts_and_never_drops_its_own(ai):
    ai.affordance(ARDEN, 'yard_gate', 'bar_gate', 'Shut and bar the yard gate')
    marks = [f'A chalk mark of the {word} kind is on the stable roof.' for word in ('first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh')]

    for mark in marks:
        _improvise(ai, {'fact': mark})

    assert ai.facts() == ['The yard gate is barred.', *marks[1:]]
    assert _improvised(ai) == marks[1:]


def test_facts_survive_leaving_and_returning_and_a_chapter_change(ai):
    _improvise(ai, {'fact': ROPE})
    records = ai.snapshot()['currentScene']['improvisedFacts']

    ai.travel(ARDEN, 'loc_lane', 'Mill Lane')
    assert ai.facts() == []
    ai.travel(ARDEN, 'loc_yard', 'The Coaching Yard')
    assert ai.facts() == [ROPE]
    assert ai.snapshot()['currentScene']['improvisedFacts'] == records

    started = ai.client.post('/api/sessions/start', json={'campaign_id': ai.ids['campaign_id'], 'continuity': 'continue'})
    assert started.status_code == 201, started.get_json()
    ai.ids['session_id'] = started.get_json()['session_id']
    assert ai.facts() == [ROPE]
    assert ai.snapshot()['currentScene']['improvisedFacts'] == records


# The scripted narrator, a failure, a fight ---------------------------------------------------------------------------


@pytest.mark.parametrize(('party', 'line'), [(False, 'You manage it.'), (True, 'Arden Vale manages it.')], ids=['solo', 'party'])
def test_the_scripted_narrator_keeps_the_engines_own_record_and_asks_no_helper(scripted, party, line):
    table = scripted(party)
    table.act(ARDEN, 'feed_crate', 'inspect')

    _improvise(table)

    assert table.last()['narration'].endswith(line)
    assert table.facts() == [OWN]
    assert {'kind': 'change', 'text': OWN} in table.last()['beats']
    assert (_record(table)['source'], 'helper' in _record(table), table.helper.requests) == ('engine', False, [])
    table.close()


def test_a_failed_improvised_roll_records_nothing(ai, monkeypatch):
    ai.send(ARDEN, CLIMB)
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda _sides: 0)

    assert _model_calls(ai, lambda: ai.send(ARDEN, 'I roll.', _roll_for(ai))) == (1, 0)

    assert ai.facts() == []
    assert _record(ai) == {}
    assert _line(ai, 'Did: ').startswith('Did: Improvised — "I climb onto the stable roof.": Athletics 5 vs DC 14, failure. The engine changed nothing.')


def test_a_fight_keeps_its_off_balance_and_records_the_fact(ai):
    _start_fight(ai)
    assert ai.acting_hero() == ARDEN
    mud = 'Churned mud slicks the yard by the mail wagon.'
    ai.helper.answers.append({'fact': mud})

    assert _model_calls(ai, lambda: ai.send(ARDEN, 'I kick mud into the eyes of Cutthroat 1.')) == (1, 1)

    target = ai.enemy('Cutthroat 1')
    assert [effect['condition'] for effect in target.get('activeEffects') or []] == ['off_balance']
    assert ai.facts() == [mud]
    assert {'kind': 'change', 'text': mud} in ai.last()['beats']
    # The action's own result is the turn's line, and ends with what is now true here.
    assert (
        'Arden Vale tries something of their own ("I kick mud into the eyes of Cutthroat 1."): Athletics 19 against DC 12, a success '
        f'— Cutthroat 1 is off balance until the end of its next turn. Now true here: {mud}'
    ) in _line(ai, 'Did: ')
    assert 'Improvised — ' not in _brief(ai)
    assert _record(ai)['fact'] == mud
