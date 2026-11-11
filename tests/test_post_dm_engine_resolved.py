"""A turn the engine resolved before narration makes no second model call.

On the Play Now adventure, through the socket pipeline: the post-narration
helper (``extract_post_dm_outcomes``) and the narration's combat preparation
(``prepare_combat_from_dm_response``) are replaced by counting fakes. They run
the real functions, so a turn that still calls them plays exactly as before, and
they fail the test when called on a turn that must skip them.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Callable

import pytest
import requests

from aidm_server.game_state.orchestration import turn_pipeline
from aidm_server.models import safe_json_loads
from tests.ai_narrator import AiTable, RecordedNarrator, ai_narration

HELPER = 'extract_post_dm_outcomes'
COMBAT_PREPARATION = 'prepare_combat_from_dm_response'
SKIPPED_METRIC = 'state_pipeline.post_dm.skipped_total'
ENGINE_RESOLVED_READ = ('skipped', 'engine_resolved', 'post_dm_skipped_engine_resolved')
ROAD = {'id': 'loc_old_north_road', 'name': 'The Old North Road'}
BENCH_SEARCH = "I search under the driver's bench."
INN = 'loc_lantern_post_inn'
NO_FIGHT = ([], 'none')
# The bench search as Safe Mode plays it; the hero starts with 25 gp.
BENCH_SEARCHED = {
    'beats': ['Investigation check: 15 — success'],
    'place': INN,
    'looseItems': ['caravan_strongbox_key'],
    'gp': 25,
    'fight': NO_FIGHT,
}


class PostNarrationCalls:
    """Counting stand-ins for the post-narration helper and combat preparation; a call fails the test while ``must_skip`` is set."""

    def __init__(self, monkeypatch):
        self.counts: Counter = Counter()
        self.must_skip = False
        for name in (HELPER, COMBAT_PREPARATION):
            monkeypatch.setattr(turn_pipeline, name, self._counting(name, getattr(turn_pipeline, name)))

    def _counting(self, name: str, real: Callable) -> Callable:
        def counted(**kwargs):
            if self.must_skip:
                pytest.fail(f'{name} was called on a turn the engine resolved before narration')
            self.counts[name] += 1
            return real(**kwargs)

        return counted

    def made_by(self, play: Callable[[], object], *, must_skip: bool = False) -> tuple[int, int]:
        """The helper and combat-preparation calls ``play`` made."""

        self.counts.clear()
        self.must_skip = must_skip
        try:
            play()
        finally:
            self.must_skip = False
        return self.counts[HELPER], self.counts[COMBAT_PREPARATION]


def _safe_mode(app, monkeypatch) -> RecordedNarrator:
    """Safe Mode: the scripted narrator tells every turn and every helper takes its deterministic path.

    The returned narrator is never asked; the table only queues replies on it.
    """

    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    monkeypatch.setenv('AIDM_HELPER_LLM_PROVIDER', 'deepseek')
    monkeypatch.setenv('AIDM_DEEPSEEK_API_KEY', 'deepseek-test-key')
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1', AIDM_STATE_PIPELINE_HELPER_IN_TESTS=True)
    # Object checks roll 15, as for the AI narrator.
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 15)
    return RecordedNarrator()


@pytest.fixture()
def table(request, app, client, socketio, monkeypatch) -> AiTable:
    """The Play Now table after its opening: narrated by the recorded AI narrator, or in Safe Mode with ``'safe_mode'``."""

    narrator = _safe_mode(app, monkeypatch) if getattr(request, 'param', 'ai') == 'safe_mode' else ai_narration(app, monkeypatch)
    # Enemies roll 1.
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda _sides: 1)
    table = AiTable(app, client, socketio, narrator)
    table.reply('Please narrate the opening scene.', message_kind='opening', client_message_id='opening-1')
    return table


@pytest.fixture()
def calls(monkeypatch) -> PostNarrationCalls:
    return PostNarrationCalls(monkeypatch)


@pytest.fixture()
def skip_metrics(monkeypatch) -> list[tuple[str, int, dict]]:
    recorded: list[tuple[str, int, dict]] = []
    monkeypatch.setattr(turn_pipeline, 'telemetry_metric', lambda name, value, tags=None: recorded.append((name, value, tags)))
    return recorded


def _pipeline(table: AiTable) -> dict:
    return safe_json_loads(table.turn().metadata_json, {})['state_pipeline']


def _post_dm_read(table: AiTable) -> tuple[str, str | None, str | None]:
    """How the last turn's narration was read: the stored extraction's source, reason and first note.

    Later steps (a pickup the narration confirms, engine-played turns) may add notes after it.
    """

    extraction = _pipeline(table)['postDmExtraction']
    return extraction['debug']['source'], extraction['debug'].get('reason'), next(iter(extraction['notes']), None)


def _engine_results(table: AiTable) -> dict:
    """What the last turn did as the player sees it: its beats, the place, the loose items there, the hero's gold and any fight."""

    snapshot = table.snapshot()
    beats = safe_json_loads(table.turn().metadata_json, {}).get('storyBeats') or []
    return {
        'beats': [beat['text'] for beat in beats],
        'place': snapshot['currentScene']['locationId'],
        'looseItems': [item['id'] for item in snapshot['currentScene'].get('items') or []],
        'gp': snapshot['playerCharacters'][0]['inventory']['currency']['gp'],
        'fight': (snapshot['flags'].get('campaignPackTriggeredEncounterIds') or [], snapshot['combat']['status']),
    }


def _object_action(target_id: str, action: str, name: str) -> tuple[str, dict]:
    message = f'Arden Vale attempts to {action} {name}.'
    intent = {'kind': 'object', 'source': 'scene_panel', 'text': message, 'object': {'id': target_id, 'action': action}}
    return message, {'action_intent': intent}


def _travel(location: dict[str, str]) -> tuple[str, dict]:
    message = f"The party travels to {location['name']}."
    return message, {'action_intent': {'kind': 'travel', 'source': 'scene_panel', 'text': message, 'location': location}}


def _roll_for(table: AiTable) -> dict:
    """The dice-tray roll that answers the last turn's pending check."""

    roll = {'die': 'd20', 'mode': 'normal', 'result_visibility': 'hidden_until_landed', 'target_pending_turn_id': table.turn().turn_id}
    return {'kind': 'roll', 'source': 'dice_tray', 'text': 'I roll.', 'roll': roll}


def _ally_radan(client, table: AiTable) -> None:
    """Radan's alliance keeps the lanterns off the road, so arriving there starts no fight."""

    response = client.post(
        f"/api/sessions/{table.ids['session_id']}/combat/apply-state-changes",
        json={'changes': [{'type': 'flag.set', 'flagKey': 'radan_allied', 'flagValue': True}]},
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['validation']['rejected'] == []


# Each turn kind the engine resolves before narration, and what the engine does on it as Safe Mode plays it.
ENGINE_RESOLVED_TURNS = [
    (_object_action('inn_lead_wagon', 'search', 'Lead Wagon'), {**BENCH_SEARCHED, 'beats': ['Clue — Ash Marks on the Road'], 'looseItems': []}),
    ((BENCH_SEARCH, {}), BENCH_SEARCHED),
    (('I pick up the key.', {}), {**BENCH_SEARCHED, 'beats': ['Taken — Caravan Strongbox Key'], 'looseItems': []}),
    (
        _travel(ROAD),
        {
            'beats': [
                'New objective — Follow the changing road',
                'New place on the map — Waystation of Scratched Names',
                'New place on the map — Ridge and Culvert Paths',
                'Met — Radan Greycloak',
                'Met — Veyra Ashcap',
            ],
            'place': 'loc_old_north_road',
            'looseItems': [],
            'gp': 25,
            'fight': NO_FIGHT,
        },
    ),
    (
        ('I head to the waystation.', {}),
        {
            'beats': [
                'Objective complete — Follow the changing road',
                'New objective — Recover the erased names',
                'New place on the map — Cairns of the Roadwardens',
                "New place on the map — Old Mason's Yard",
                'Met — Serenil of the Westward Song',
            ],
            'place': 'loc_waystation_scratched_names',
            'looseItems': [],
            'gp': 25,
            'fight': NO_FIGHT,
        },
    ),
]


@pytest.mark.parametrize('table', ['ai', 'safe_mode'], indirect=True)
def test_turns_the_engine_resolved_make_no_second_call_and_do_what_safe_mode_does(table, calls, skip_metrics, client):
    _ally_radan(client, table)
    made, read, results = [], [], []
    for (message, extra), _expected in ENGINE_RESOLVED_TURNS:
        made.append(calls.made_by(lambda: table.send(message, **extra), must_skip=True))
        read.append(_post_dm_read(table))
        results.append(_engine_results(table))

    # A button search, a typed search, a typed pickup, button travel and typed travel.
    assert made == [(0, 0)] * 5
    assert read == [ENGINE_RESOLVED_READ] * 5
    assert skip_metrics == [(SKIPPED_METRIC, 1, {'reason': 'engine_resolved'})] * 5
    assert results == [expected for _turn, expected in ENGINE_RESOLVED_TURNS]


def test_talk_and_an_improvised_action_have_their_narration_read_once(table, calls):
    talk = calls.made_by(lambda: table.reply('I ask Mara about the caravan.'))
    talk_read = _post_dm_read(table)
    # Climbing needs a roll: the narration is read once, after the roll that answers it.
    climb = calls.made_by(lambda: table.reply('I climb onto the inn roof.'))
    roll = calls.made_by(lambda: table.send('I roll.', client_message_id='roll-1', action_intent=_roll_for(table)))

    assert (talk, climb, roll) == ((1, 1), (0, 0), (1, 1))
    assert talk_read[:2] == ('heuristic', None)
    assert _post_dm_read(table)[:2] == ('heuristic', None)


@pytest.mark.parametrize('table', ['ai', 'safe_mode'], indirect=True)
def test_turns_in_a_fight_and_the_arrival_that_opens_one_read_the_narration_as_before(table, calls):
    message, extra = _travel(ROAD)
    arrival = calls.made_by(lambda: table.send(message, **extra))
    arrival_read = _post_dm_read(table)
    arrival_results = _engine_results(table)
    opening_enemy_turns = _pipeline(table)['openingEnemyTurns']
    end_turn = calls.made_by(
        lambda: table.send(
            'Arden Vale ends the turn.',
            action_intent={'kind': 'combat', 'source': 'combat_hud', 'text': 'Arden Vale ends the turn.', 'combat': {'action_id': 'combat.end_turn'}},
        )
    )

    assert opening_enemy_turns is True
    assert (arrival, end_turn) == ((1, 1), (1, 1))
    assert arrival_read[:2] == ('heuristic', None)
    assert _post_dm_read(table)[:2] == ('heuristic', None)
    # The arrival starts the road's fight exactly as in Safe Mode.
    assert (arrival_results['place'], arrival_results['fight']) == ('loc_old_north_road', (['enc_pale_lantern_detour'], 'active'))
    assert _engine_results(table)['fight'] == (['enc_pale_lantern_detour'], 'active')


def test_a_turn_waiting_on_a_roll_is_still_skipped_as_pending_roll(table, calls, skip_metrics):
    climb = calls.made_by(lambda: table.reply('I climb onto the inn roof.'))

    assert table.turn().outcome_status == 'deferred'
    assert climb == (0, 0)
    assert _post_dm_read(table) == ('skipped', 'pending_roll', 'post_dm_skipped_pending_roll')
    assert skip_metrics == [(SKIPPED_METRIC, 1, {'reason': 'pending_roll'})]


def test_a_claim_in_the_narration_of_an_engine_resolved_turn_changes_nothing(table, calls):
    claim = "You crouch by the driver's bench and feel beneath it. You also find 50 gold wedged against the boards."
    made = calls.made_by(lambda: table.reply(BENCH_SEARCH, claim), must_skip=True)

    assert 'You also find 50 gold' in table.turn().dm_output
    assert made == (0, 0)
    assert _engine_results(table) == BENCH_SEARCHED
    assert _pipeline(table)['postAppliedChanges'] == []


RESOLVED_PIPELINE = {
    'openingEnemyTurns': False,
    'preDmExtraction': {
        'declaredActions': [{'id': 'act_001', 'type': 'scene.interactable.action', 'requiresDMResolution': False}],
        'notes': ['free_text_action_pre_dm'],
        'debug': {'source': 'free_text', 'reason': 'free_text_action', 'helperAttempted': False},
    },
    'preDmValidation': {'validatedActions': [{'actionId': 'act_001', 'status': 'valid'}]},
    'pendingImmediateChanges': [],
    'immediateAppliedChanges': [{'id': 'chg_1', 'type': 'scene.interactable.action'}],
    'combatDebug': {'triggered': False},
}
NEEDS_DM = {'id': 'act_002', 'type': 'inventory.use', 'requiresDMResolution': True}


def _engine_resolved(changes: dict | None = None, *, fight: str = 'none', pending_roll: bool = False) -> bool:
    """The rule for ``RESOLVED_PIPELINE`` with the top-level ``changes`` merged in, in a state whose combat has ``fight`` status."""

    pipeline = deepcopy(RESOLVED_PIPELINE)
    for key, value in (changes or {}).items():
        pipeline[key] = {**pipeline[key], **value} if isinstance(value, dict) else value
    return turn_pipeline._engine_resolved_turn(pipeline, {'combat': {'status': fight}}, pending_roll=pending_roll)


def test_a_turn_the_engine_resolved_before_narration_is_engine_resolved():
    assert _engine_resolved() is True


@pytest.mark.parametrize(
    ('changes', 'fight', 'pending_roll'),
    [
        pytest.param({}, 'none', True, id='1-waiting-on-a-roll'),
        pytest.param({'openingEnemyTurns': True}, 'none', False, id='2-opening-enemy-turns'),
        pytest.param({'preDmExtraction': {'debug': {'helperAttempted': True}}}, 'none', False, id='3-pre-narration-helper-ran'),
        pytest.param({'preDmExtraction': {'notes': ['clarification_resume']}}, 'none', False, id='3-clarification-resume'),
        pytest.param({'preDmExtraction': {'declaredActions': []}}, 'none', False, id='3-no-declared-action'),
        pytest.param(
            {'preDmExtraction': {'declaredActions': [*RESOLVED_PIPELINE['preDmExtraction']['declaredActions'], NEEDS_DM]}},
            'none',
            False,
            id='3-an-action-needs-the-dungeon-master',
        ),
        pytest.param({'preDmValidation': {'validatedActions': [{'actionId': 'act_001', 'status': 'pending'}]}}, 'none', False, id='4-an-action-not-valid'),
        pytest.param({'pendingImmediateChanges': [{'id': 'chg_2', 'type': 'inventory.transfer'}]}, 'none', False, id='5-a-change-pending'),
        pytest.param({'immediateAppliedChanges': []}, 'none', False, id='5-nothing-applied'),
        pytest.param({}, 'active', False, id='6-fight-active'),
        pytest.param({}, 'starting', False, id='6-fight-starting'),
        pytest.param({'combatDebug': {'triggered': True}}, 'none', False, id='6-combat-preparation-triggered'),
    ],
)
def test_each_clause_keeps_the_narration_read(changes, fight, pending_roll):
    assert _engine_resolved(changes, fight=fight, pending_roll=pending_roll) is False
