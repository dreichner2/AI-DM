"""Plans that change the scene, and fights about something (W9-SCENE), through the real socket pipeline.

The coaching yard (``tests/scene_yard_pack.py``) in Safe Mode unless an AI narrator is named (``tests/ai_narrator.py``,
whose pre-narration helper is a counting fake here). Seeded dice: checks the engine rolls for a hero come up 15, the
heroes roll 15 to hit and their highest damage, enemies roll 5 on a d20 and 3 on anything else (they miss the heroes,
fail to clear a barred gate and to break a hold). Arden Vale (fighter, Athletics +4) plays alone or with Seren
Ashgrove (cleric).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from aidm_server.contracts import ProviderResponse
from aidm_server.models import DmTurn, safe_json_loads
from aidm_server.prompt_templates import IMPROVISED_RULE, PLAN_RULE
from tests.ai_narrator import ENGINE_BRIEF_RE, ai_narration
from tests.scene_yard_pack import YardTable, install, yard_pack
from tests.test_where_everyone_stands import _errors

ARDEN, SEREN = 'Arden Vale', 'Seren Ashgrove'
BAR_GATE = 'plan.yard_gate.bar_gate'


def _dice(monkeypatch, *, hero_checks: int = 15, enemy_d20: int = 5) -> None:
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: hero_checks)
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 14 if sides == 20 else sides - 1)
    monkeypatch.setattr('aidm_server.combat.actions.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr(
        'aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda sides: enemy_d20 if sides == 20 else min(sides, 3)
    )


@pytest.fixture()
def safe_mode(app, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    _dice(monkeypatch)
    install(monkeypatch)


@pytest.fixture()
def solo(app, client, socketio, safe_mode):
    table = YardTable(app, client, socketio, party=False)
    # Wenna is met with the first thing the hero does.
    table.act(ARDEN, 'feed_crate', 'inspect')
    yield table
    table.close()


@pytest.fixture()
def party(app, client, socketio, safe_mode):
    table = YardTable(app, client, socketio, party=True)
    table.act(ARDEN, 'feed_crate', 'inspect')
    yield table
    table.close()


def _beats(table: YardTable) -> list[str]:
    return [beat['text'] for beat in table.last()['beats']]


def _rolls(table: YardTable) -> list[dict]:
    with table.app.app_context():
        turn = DmTurn.query.filter_by(session_id=table.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
        return safe_json_loads(turn.metadata_json, {}).get('storyRolls') or []


def _effects(table: YardTable) -> list[dict]:
    pipeline = table.last()['pipeline']
    return [change for change in pipeline.get('immediateAppliedChanges') or [] + (pipeline.get('combatAppliedChanges') or []) if change['type'] == 'scene.effect']


def _start_fight(table: YardTable) -> dict[str, str]:
    """Search the mail wagon: the fragment is found and left lying as the cutthroats come over the wall."""

    assert _errors(table.act(ARDEN, 'mail_wagon', 'search')) == []
    enemies = [participant['id'] for participant in table.combat()['participants'] if participant['team'] == 'enemy']
    runner = table.combat()['flags'].get('runnerId') or enemies[0]
    return {'runner': runner, 'other': next((enemy for enemy in enemies if enemy != runner), '')}


def _rounds_until_over(table: YardTable) -> int:
    table.fight(rounds=8)
    return table.snapshot()['combat']['round']


def _final(table: YardTable) -> dict[str, Any]:
    flags = {key: value for key, value in table.snapshot()['flags'].items() if key in {'cutthroat_taken', 'fragment_kept_in_the_dark', 'fragment_lost'}}
    return {'facts': table.facts(), 'flags': flags, 'stakes': table.final_stakes(), 'leads': table.leads(), 'captives': table.captives()}


# The acceptance pair ---------------------------------------------------------------------------------------------


def test_two_plans_in_the_same_yard_leave_two_different_lasting_situations(app, client, socketio, safe_mode):
    """Plan one bars the gate and grapples the runner; plan two puts out the lantern and hides the fragment in the
    crate. Same yard, same dice; the two final states differ in facts, flags, stakes and leads."""

    one = YardTable(app, client, socketio, party=True)
    enemies = _start_fight(one)
    first = one.acting_hero()
    second = SEREN if first == ARDEN else ARDEN
    assert _errors(one.hud(first, 'Bar the gate.', BAR_GATE)) == []
    one.hud(first, 'End turn.', 'combat.end_turn')
    assert one.stakes()['fragment'] == 'held_by_enemy'
    assert _errors(one.hud(second, 'Grapple the runner.', 'combat.grapple', enemies['runner'])) == []
    one.hud(second, 'End turn.', 'combat.end_turn')
    assert _errors(one.hud(first, 'Bind him.', 'combat.subdue', enemies['runner'])) == []
    assert one.stakes()['fragment'] == 'on_ground'
    one.hud(first, 'End turn.', 'combat.end_turn')
    rounds_one = _rounds_until_over(one)
    plan_one = _final(one)
    one.close()

    two = YardTable(app, client, socketio, party=True, fresh=True)
    _start_fight(two)
    first = two.acting_hero()
    second = SEREN if first == ARDEN else ARDEN
    assert _errors(two.send(first, 'I hide the road-stone fragment in the feed crate.')) == []
    two.hud(first, 'End turn.', 'combat.end_turn')
    # The runner has to search it out before it can take it.
    assert 'The cutthroat hunts about and finds where the road-stone fragment was hidden.' in two.last()['narration']
    assert _errors(two.send(second, 'I snuff the lantern.')) == []
    assert two.view(second)['currentScene']['zones'][0]['conditions'] == ['dark']
    two.hud(second, 'End turn.', 'combat.end_turn')
    rounds_two = _rounds_until_over(two)
    plan_two = _final(two)
    two.close()

    assert min(rounds_one, rounds_two) >= 2
    assert plan_one == {
        'facts': ['The yard gate is barred.'],
        'flags': {'cutthroat_taken': True},
        'stakes': {'fragment': 'secured', 'witness': 'threatened', 'way_out': 'blocked'},
        'leads': ['The bound cutthroat names a buyer at the mill.'],
        'captives': ['Cutthroat 1'],
    }
    assert plan_two == {
        'facts': ['The yard lantern is out; the yard is dark.', 'Mud and blood mark where the cutthroats fell.'],
        'flags': {'fragment_kept_in_the_dark': True},
        'stakes': {'fragment': 'secured', 'witness': 'hurt', 'way_out': 'open'},
        'leads': ['Whoever sent them will come again by daylight.'],
        'captives': [],
    }
    for key in ('facts', 'flags', 'stakes', 'leads'):
        assert plan_one[key] != plan_two[key]


# Part A — effects ------------------------------------------------------------------------------------------------


def _gate_offer(table: YardTable, hero: str = ARDEN) -> dict:
    gate = next(entry for entry in table.view(hero)['currentScene']['interactables'] if entry['id'] == 'yard_gate')
    return next(offer for offer in gate['offeredActions'] if offer['action'] == 'plan')


def test_an_authored_affordance_is_offered_and_clicking_it_bars_the_gate_with_its_check(solo):
    offer = _gate_offer(solo)
    assert offer == {
        'action': 'plan',
        'planId': 'bar_gate',
        'label': 'Shut and bar the yard gate',
        'authored': True,
        'legal': True,
        'done': False,
        'effect': 'obstruct',
        'check': {'skill': 'athletics'},
        'suggested': True,
    }

    assert _errors(solo.affordance(ARDEN, 'yard_gate', 'bar_gate', 'Shut and bar the yard gate')) == []

    told = solo.last()
    assert told['narration'] == 'You shut and bar the yard gate. The yard gate is barred.'
    assert _beats(solo) == ['Athletics check: 19 — success (DC 10)', 'The yard gate is barred.']
    (card,) = _rolls(solo)
    assert (card['kind'], card['label'], card['total'], card['against'], card['outcome']) == ('check', 'Athletics check', 19, {'kind': 'DC', 'value': 10}, 'success')
    assert card['target'] == {'id': 'yard_gate', 'name': 'Yard Gate'}
    assert solo.facts() == ['The yard gate is barred.']
    gate = next(entry for entry in solo.snapshot()['currentScene']['interactables'] if entry['id'] == 'yard_gate')
    assert gate['blocked'] is True and gate['affordancesUsed'] == ['bar_gate']
    # Once done it is not offered again, and the Scene tab lists what has changed.
    view = solo.view(ARDEN)
    gate = next(entry for entry in view['currentScene']['interactables'] if entry['id'] == 'yard_gate')
    assert not [offer for offer in gate['offeredActions'] if offer['action'] == 'plan']
    assert view['currentScene']['facts'] == ['The yard gate is barred.']
    # A plan only for a fight is shown, and why it waits.
    horse = next(entry for entry in view['currentScene']['interactables'] if entry['id'] == 'tethered_horse')
    assert [(offer['label'], offer['legal'], offer['reason']) for offer in horse['offeredActions'] if offer['action'] == 'plan'] == [
        ('Slash the tether and slap the horse', False, 'Only in a fight.')
    ]


def test_a_failed_affordance_check_tells_its_failure_as_written_and_changes_nothing(solo, monkeypatch):
    _dice(monkeypatch, hero_checks=2)

    solo.affordance(ARDEN, 'yard_gate', 'bar_gate', 'Shut and bar the yard gate')

    assert solo.last()['narration'] == 'The bar jumps out of its brackets and lands in the mud.'
    assert _beats(solo) == ['Athletics check: 6 — failure (DC 10)']
    assert solo.facts() == []
    assert 'blocked' not in next(entry for entry in solo.snapshot()['currentScene']['interactables'] if entry['id'] == 'yard_gate')


def test_typed_words_with_an_affordance_keyword_and_the_objects_name_choose_it(solo):
    solo.send(ARDEN, 'I slam the gate shut and drop the bar across it.')

    assert solo.facts() == ['The yard gate is barred.']
    (effect,) = _effects(solo)
    assert (effect['affordanceId'], effect['effect']) == ('bar_gate', 'obstruct')


@pytest.mark.parametrize(
    ('words', 'effect', 'fact', 'beats'),
    [
        ('I snuff the lantern.', 'destroy', 'The yard lantern is out; the yard is dark.', ['The yard lantern is out; the yard is dark.']),
        ('I hide behind the feed crate.', 'conceal', 'Arden Vale is hidden in the feed crate.', ['Stealth check: 16 — success (DC 12)', 'Arden Vale is hidden in the feed crate.']),
        ('I spook the horse.', 'distract', 'Everyone near the tethered horse is looking the wrong way.', ['Animal Handling check: 15 — success (DC 12)', 'Everyone near the tethered horse is looking the wrong way.']),
        ('I stand over Wenna.', 'protect', 'Wenna Pike is behind Arden Vale.', ['Wenna Pike is behind Arden Vale.']),
        ('I use the drovers to cut Wenna off from the others.', 'separate', 'Wenna Pike is cut off from the others.', ['Persuasion check: 15 — success (DC 12)', 'Wenna Pike is cut off from the others.']),
    ],
)
def test_each_effect_by_its_verbs_on_a_tagged_thing_in_reach(solo, words, effect, fact, beats):
    assert _errors(solo.send(ARDEN, words)) == []

    (change,) = _effects(solo)
    assert (change['effect'], change['success']) == (effect, True)
    assert fact in solo.facts()
    assert _beats(solo)[: len(beats)] == beats


def test_putting_out_the_light_darkens_the_yard_in_the_where_line_data(solo):
    solo.send(ARDEN, 'I put out the yard lantern.')

    zones = {zone['id']: zone for zone in solo.view(ARDEN)['currentScene']['zones']}
    assert zones['yard']['conditions'] == ['dark']
    assert 'conditions' not in zones['stable']
    assert solo.snapshot()['currentScene']['zoneConditions'] == {'yard': ['dark']}


def test_guarding_a_person_is_remembered_by_them(solo):
    solo.send(ARDEN, 'I shield Wenna Pike.')

    wenna = next(npc for npc in solo.snapshot()['knownNpcs'] if npc['id'] == 'npc_wenna')
    assert [entry['key'] for entry in wenna['remembers']] == ['protected']


def test_a_person_cut_off_from_the_others_can_be_told_what_is_private(solo):
    solo.snapshot()
    solo.send(ARDEN, 'I talk to Wenna.')
    labels = [action['label'] for action in solo.view(ARDEN)['currentScene']['conversation']['actions']]
    assert 'Ask about who paid her' in labels

    # With a second person beside her, the private topic waits until a plan draws her apart.
    def another_person(snapshot: dict) -> None:
        snapshot['knownNpcs'].append({'id': 'npc_drover', 'name': 'Old Hob', 'zoneId': 'yard'})
        snapshot['currentScene']['activeNpcIds'].append('npc_drover')

    solo.edit(another_person)
    assert 'Ask about who paid her' not in [action['label'] for action in solo.view(ARDEN)['currentScene']['conversation']['actions']]
    solo.send(ARDEN, 'I use the drovers to split Wenna off from Old Hob.')
    solo.send(ARDEN, 'I talk to Wenna.')
    assert 'Ask about who paid her' in [action['label'] for action in solo.view(ARDEN)['currentScene']['conversation']['actions']]


@pytest.mark.parametrize('words', ['I bar the lantern.', 'I snuff the feed crate.', 'I hide behind Wenna.'])
def test_an_impossible_pair_falls_through_to_the_old_floor(solo, words):
    solo.send(ARDEN, words)

    assert _effects(solo) == []
    assert solo.facts() == []


def test_scene_facts_persist_across_leaving_and_returning(solo):
    solo.affordance(ARDEN, 'yard_gate', 'bar_gate', 'Shut and bar the yard gate')
    solo.send(ARDEN, 'I put out the yard lantern.')
    facts = solo.facts()

    solo.travel(ARDEN, 'loc_lane', 'Mill Lane')
    assert solo.facts() == []
    solo.travel(ARDEN, 'loc_yard', 'The Coaching Yard')

    assert solo.facts() == facts == ['The yard gate is barred.', 'The yard lantern is out; the yard is dark.']
    assert solo.snapshot()['currentScene']['zoneConditions'] == {'yard': ['dark']}
    # Coming back is not new: no change beat.
    assert not [beat for beat in solo.last()['beats'] if beat['kind'] == 'change']


# Part B — stakes -------------------------------------------------------------------------------------------------


def _end_turns_until(table: YardTable, done, limit: int = 20) -> None:
    for _step in range(limit):
        if done() or table.combat().get('status') != 'active':
            return
        hero = table.acting_hero()
        assert hero, table.combat().get('flags')
        table.hud(hero, 'End turn.', 'combat.end_turn')


def test_the_runner_takes_the_fragment_and_escapes_when_nobody_stops_it(party):
    _start_fight(party)
    xp = {hero: party.hero(hero)['xp']['current'] for hero in (ARDEN, SEREN)}

    _end_turns_until(party, lambda: False)

    combat = party.combat()
    assert combat['status'] == 'ended' and combat['flags']['endReason'] == 'enemy_goal_met'
    assert combat['round'] >= 2
    assert party.final_stakes()['fragment'] == 'taken'
    assert party.leads() == ['The fragment went north with the cutthroat.']
    assert party.snapshot()['flags']['fragment_lost'] is True
    told = party.last()
    assert 'gets clear with the road-stone fragment; the others melt away after it.' in told['narration']
    assert told['narration'].endswith('Hoofbeats fade up the lane.')
    assert {'kind': 'lead', 'text': 'A lead — The fragment went north with the cutthroat.'} in told['beats']
    # No flee reward is authored: half the encounter's XP, split.
    assert {hero: party.hero(hero)['xp']['current'] - xp[hero] for hero in (ARDEN, SEREN)} == {ARDEN: 25, SEREN: 25}


def test_a_barred_gate_makes_the_runner_spend_its_turn_clearing_it(party):
    _start_fight(party)
    first = party.acting_hero()
    party.hud(first, 'Bar the gate.', BAR_GATE)

    _end_turns_until(party, lambda: 'heaves at the yard gate' in party.last()['narration'])

    told = party.last()
    assert 'The cutthroat heaves at the yard gate, and it holds.' in told['narration']
    clear = next(roll for roll in _rolls(party) if roll['label'] == 'Clear the way')
    assert (clear['actor']['side'], clear['total'], clear['against'], clear['outcome']) == ('enemy', 7, {'kind': 'DC', 'value': 12}, 'failure')
    assert party.stakes()['way_out'] == 'blocked'


def test_a_guarded_witness_stays_safe(party):
    _start_fight(party)
    guard = party.acting_hero()
    assert _errors(party.send(guard, 'I guard Wenna Pike.')) == []

    party.hud(guard, 'End turn.', 'combat.end_turn')

    # The silencer has to get past the guard: it goes at the hero instead of Wenna.
    silencer = next(enemy for enemy in party.combat()['participants'] if enemy['team'] == 'enemy' and enemy['id'] != party.combat()['flags']['runnerId'])
    assert (silencer['currentIntent']['reason'], silencer['currentIntent']['targetId']) == (f'Get past {guard} to Wenna Pike.', party.hero_id(guard))
    assert party.stakes()['witness'] == 'safe'
    _end_turns_until(party, lambda: False)
    assert party.final_stakes()['witness'] == 'safe'


def test_on_defeat_replaces_the_re_arm(app, client, socketio, safe_mode, monkeypatch):
    pack = yard_pack()
    encounter = pack['encounters'][0]
    encounter.pop('enemyGoal')
    encounter.pop('secondaryGoal')
    encounter['onDefeat'] = {
        'wakeZoneId': 'stable',
        'text': 'You wake in the straw of the stable. The wagon stands open and empty.',
        'setsFlags': {'beaten_in_the_yard': True},
        'stakes': {'fragment': 'taken'},
    }
    install(monkeypatch, pack)
    _dice(monkeypatch, enemy_d20=20)
    table = YardTable(app, client, socketio, party=False)
    _start_fight(table)

    _end_turns_until(table, lambda: False, limit=30)

    snapshot = table.snapshot()
    assert snapshot['combat']['flags']['endReason'] == 'objective_failed'
    assert 'enc_yard_theft' in snapshot['flags']['campaignPackCompletedEncounterIds']
    assert 'enc_yard_theft' in snapshot['flags']['campaignPackTriggeredEncounterIds']
    assert snapshot['flags']['beaten_in_the_yard'] is True
    assert snapshot['stakes']['enc_yard_theft']['fragment'] == 'taken'
    assert table.zone(ARDEN) == 'stable'
    assert table.last()['narration'].endswith('You wake in the straw of the stable. The wagon stands open and empty.')
    table.close()


def test_a_failed_check_fails_forward_and_with_no_retry_is_not_offered_again(solo):
    solo.act(ARDEN, 'loose_board', 'search')

    assert solo.last()['narration'] == 'You try to search the Loose Board.\n\nThe board snaps under your boot: only rats, and a scrap of ash-marked cloth.'
    assert solo.snapshot()['flags']['ash_cloth_found'] is True
    assert 'The loose board is splintered.' in solo.facts()
    board = next(entry for entry in solo.view(ARDEN)['currentScene']['interactables'] if entry['id'] == 'loose_board')
    search = next(offer for offer in board['offeredActions'] if offer['action'] == 'search')
    assert search['legal'] is False and 'retry' not in search

    solo.act(ARDEN, 'rain_barrel', 'search')
    assert solo.last()['narration'].endswith('Cold water to the elbow, and a drowned glove that is not yours.')
    barrel = next(entry for entry in solo.view(ARDEN)['currentScene']['interactables'] if entry['id'] == 'rain_barrel')
    assert next(offer for offer in barrel['offeredActions'] if offer['action'] == 'search')['retry'] is True


# Floors (amendments 1 and 2) -------------------------------------------------------------------------------------


def _enemy_hp(table: YardTable) -> list[int]:
    return [participant['hp']['max'] for participant in table.combat()['participants'] if participant['team'] == 'enemy']


def test_a_lone_hero_still_meets_two_cutthroats_and_a_runner_worth_stopping(solo):
    _start_fight(solo)

    hp = _enemy_hp(solo)
    assert len(hp) == 2
    # The runner has at least 12 HP against a lone hero; the other keeps its scaled HP.
    assert solo.enemy('Cutthroat 1')['id'] == solo.combat()['flags']['runnerId']
    assert hp[0] >= 12 and hp[1] < 12
    assert solo.combat()['flags']['partyScaling']['floors'] == {'source': 'default', 'minEnemies': 2, 'runnerMinHp': 12}


def test_the_floors_a_pack_sets_hold_for_a_small_party(app, client, socketio, safe_mode, monkeypatch):
    pack = yard_pack()
    encounter = pack['encounters'][0]
    encounter['minEnemies'] = 3
    encounter['enemyGroups'][0]['minHp'] = 14
    install(monkeypatch, pack)
    table = YardTable(app, client, socketio, party=False)
    table.act(ARDEN, 'feed_crate', 'inspect')

    _start_fight(table)

    assert _enemy_hp(table) == [14, 14, 14]
    assert table.combat()['flags']['partyScaling']['floors'] == {'source': 'pack', 'minEnemies': 3, 'minHp': {'enemy_cutthroat': 14}}
    table.close()


def test_no_floor_holds_for_a_party_of_four():
    from aidm_server.game_state.campaign_pack_encounters import party_scaling

    encounter = yard_pack()['encounters'][0]
    encounter['minEnemies'] = 3

    assert 'floors' not in party_scaling(encounter, party_size=4)
    assert party_scaling(encounter, party_size=3)['floors'] == {'source': 'pack', 'minEnemies': 3}


# The player view, the journal and the brief ----------------------------------------------------------------------


def test_the_player_view_tells_what_is_at_stake_but_not_who_runs(party):
    _start_fight(party)

    combat = party.view(ARDEN)['combat']
    assert combat['stakes'] == [
        {'id': 'fragment', 'kind': 'item', 'name': 'the road-stone fragment', 'state': 'in_place', 'words': 'where it lies'},
        {'id': 'witness', 'kind': 'person', 'name': 'Wenna Pike', 'state': 'safe', 'words': 'safe'},
        {'id': 'way_out', 'kind': 'route', 'name': 'the yard gate', 'state': 'open', 'words': 'open'},
    ]
    assert not {'stakes', 'enemyGoal', 'secondaryGoal', 'runnerId'} & set(combat['flags'])


def test_a_lead_goes_into_the_journal(party):
    _start_fight(party)
    _end_turns_until(party, lambda: False)

    assert party.view(SEREN)['journal']['leads'] == [{'text': 'The fragment went north with the cutthroat.'}]
    assert party.snapshot()['leads'] == [{'text': 'The fragment went north with the cutthroat.'}]


class Helper:
    """Every helper model (before and after narration): the plan helper's answers in turn, ``{}`` for anything else;
    keeps each prompt."""

    def __init__(self) -> None:
        self.answers: list[str] = []
        self.prompts: list[str] = []

    def generate(self, request):
        self.prompts.append(request.prompt)
        answer = self.answers.pop(0) if self.answers and self.planning(request.prompt) else '{}'
        return ProviderResponse(text=answer, provider='deepseek', model='helper')

    @staticmethod
    def planning(prompt: str) -> bool:
        return prompt.startswith('The hero tries:')

    def plans(self) -> int:
        return sum(1 for prompt in self.prompts if self.planning(prompt))


HELPER_MODULES = (
    'aidm_server.game_state.orchestration.scene_plans',
    'aidm_server.game_state.orchestration.speech_acts',
    'aidm_server.game_state.extraction.pre_dm_action_extractor',
    'aidm_server.game_state.extraction.post_dm_outcome_extractor',
    'aidm_server.turn_control',
)


@pytest.fixture()
def ai(app, client, socketio, monkeypatch):
    narrator = ai_narration(app, monkeypatch)
    _dice(monkeypatch)
    install(monkeypatch)
    app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
    helper = Helper()
    for module in HELPER_MODULES:
        monkeypatch.setattr(f'{module}.get_helper_provider', lambda *_args, **_kwargs: helper)
    table = YardTable(app, client, socketio, party=False)
    table.narrator, table.helper = narrator, helper
    table.act(ARDEN, 'feed_crate', 'inspect')
    yield table
    table.close()


def _brief(table: YardTable) -> str:
    return ENGINE_BRIEF_RE.search(table.narrator.prompts[-1]).group('brief')


def _line(table: YardTable, head: str) -> str:
    return next((line for line in _brief(table).splitlines() if line.startswith(head)), '')


def _model_calls(table: YardTable, play) -> tuple[int, int]:
    """``(narrator, helper)`` calls ``play`` made."""

    before = (len(table.narrator.prompts), len(table.helper.prompts))
    play()
    return len(table.narrator.prompts) - before[0], len(table.helper.prompts) - before[1]


def test_the_brief_tells_the_plan_what_is_now_true_and_the_rule(ai):
    assert _model_calls(ai, lambda: ai.affordance(ARDEN, 'yard_gate', 'bar_gate', 'Shut and bar the yard gate')) == (1, 0)

    assert _line(ai, 'Did: ') == 'Did: Effect — obstruct: the yard gate is barred (Athletics 19 vs DC 10)'
    assert _line(ai, 'Now true here: ') == 'Now true here: The yard gate is barred.'
    assert PLAN_RULE in ai.narrator.prompts[-1]

    ai.send(ARDEN, 'I look around the yard.')
    assert PLAN_RULE not in ai.narrator.prompts[-1]
    assert _line(ai, 'Now true here: ') == 'Now true here: The yard gate is barred.'


def test_the_brief_says_what_is_at_stake_once_the_enemies_have_moved(ai):
    _start_fight(ai)
    assert _line(ai, 'At stake: ') == 'At stake: the road-stone fragment (where it lies); Wenna Pike (safe); the yard gate (open)'

    _end_turns_until(ai, lambda: 'snatches up' in _line(ai, 'Did: '))

    # The runner's grab is applied after the narration; the brief already says where the fragment is.
    assert _line(ai, 'At stake: ').startswith('At stake: the road-stone fragment (the cutthroat has it)')


# Model calls (amendment 4) and the helper ------------------------------------------------------------------------


def test_a_clicked_affordance_in_a_fight_calls_only_the_narrator(ai):
    _start_fight(ai)

    assert _model_calls(ai, lambda: ai.hud(ARDEN, 'Shut and bar the yard gate', BAR_GATE)) == (1, 0)
    assert ai.last()['pipeline']['preDmExtraction']['debug']['reason'] == 'engine_fight_action'
    ai.hud(ARDEN, 'End turn.', 'combat.end_turn')
    # An affordance whose result is authored text is the engine's to tell: no model at all.
    assert _model_calls(ai, lambda: ai.hud(ARDEN, 'Slash the tether and slap the horse', 'plan.tethered_horse.slap_horse')) == (0, 0)
    assert ai.last()['narration'].startswith('The horse bolts across the yard and the cutthroats scatter from its hooves.')


def test_typed_words_the_first_pass_places_never_reach_the_helper(ai):
    assert _model_calls(ai, lambda: ai.send(ARDEN, 'I snuff the lantern.')) == (1, 0)

    assert ai.facts() == ['The yard lantern is out; the yard is dark.']


def test_the_helper_places_words_the_first_pass_could_not_once(ai):
    ai.helper.answers.append(json.dumps({'effect': 'distract', 'targetId': 'tethered_horse', 'summary': 'spooks the horse'}))

    assert _model_calls(ai, lambda: ai.send(ARDEN, 'I wave my cloak wildly at the beast.')) == (1, 1)

    (effect,) = _effects(ai)
    assert (effect['effect'], effect['objectId'], effect['success']) == ('distract', 'tethered_horse', True)
    assert _line(ai, 'Did: ') == 'Did: Effect — distract: Everyone near the tethered horse is looking the wrong way (Animal Handling 15 vs DC 12)'
    # The helper is given only what stands in the hero's own room.
    prompt = ai.helper.prompts[-1]
    assert '- tethered_horse: Tethered Horse (object, tags animal, rope)' in prompt
    assert 'hay_pile' not in prompt and 'saddle_cache' not in prompt


@pytest.mark.parametrize(
    'answer',
    [
        {'effect': 'fly', 'targetId': 'tethered_horse'},
        {'effect': 'distract', 'targetId': 'the_moon'},
        {'effect': 'conceal', 'targetId': 'hay_pile'},
        'not json at all',
    ],
    ids=['unknown effect', 'unknown id', 'next door', 'invalid'],
)
def test_an_answer_outside_the_effects_and_ids_in_reach_is_ignored(ai, answer):
    ai.helper.answers.append(answer if isinstance(answer, str) else json.dumps(answer))

    ai.send(ARDEN, 'I wave my cloak wildly at the beast.')

    # Asked once; the words then fall to the old floor, and the narration is read after it as before.
    assert ai.helper.plans() == 1
    assert _effects(ai) == []
    assert ai.facts() == []


def test_speech_never_goes_to_the_plan_helper(ai):
    ai.send(ARDEN, '"Hold the gate!"')

    assert ai.helper.plans() == 0


# Typed words that name a hidden thing (amendment 5) --------------------------------------------------------------


def test_words_naming_a_hidden_thing_take_the_action_that_reveals_it(solo):
    assert _errors(solo.send(ARDEN, 'I search the wagon strongbox.')) == []

    assert solo.zone(ARDEN) == 'stable'
    strongbox = next(entry for entry in solo.snapshot()['currentScene']['interactables'] if entry['id'] == 'wagon_strongbox')
    assert strongbox.get('hidden') is False
    assert solo.last()['narration'].startswith('You step into the stable.')


@pytest.mark.parametrize(('table', 'line'), [('solo', 'You see no saddle cache here.'), ('party', 'Arden Vale sees no saddle cache here.')])
def test_words_naming_a_hidden_thing_nobody_can_reveal_see_nothing(request, table, line):
    table = request.getfixturevalue(table)
    before = table.snapshot()

    assert _errors(table.send(ARDEN, 'I search the saddle cache.')) == []

    told = table.last()
    assert told['narration'] == line
    assert _rolls(table) == [] and told['beats'] == []
    after = table.snapshot()
    assert after['currentScene'] == before['currentScene']
    assert after['playerCharacters'] == before['playerCharacters']


def test_words_naming_a_hidden_thing_reach_no_model_with_an_ai_narrator(ai):
    """The engine tells its own line. A model given this turn had a bystander deny that the thing existed."""

    assert _model_calls(ai, lambda: ai.send(ARDEN, 'I search the saddle cache.')) == (0, 0)

    assert ai.last()['narration'] == 'You see no saddle cache here.'


# A hero does not move by narration (amendment 6) -----------------------------------------------------------------


def test_a_plan_aimed_next_door_steps_in_first_and_two_rooms_off_is_refused(solo):
    assert _errors(solo.send(ARDEN, 'I hide behind the hay pile.')) == []
    assert solo.zone(ARDEN) == 'stable'
    assert solo.last()['narration'].startswith('You step into the stable.')
    assert 'Arden Vale is hidden behind the hay pile.' in solo.facts()

    solo.move(ARDEN, 'yard', 'Go back to the yard')
    assert _errors(solo.plan(ARDEN, 'Bar the loft hatch', {'objectId': 'loft_hatch', 'effect': 'obstruct'})) == ['The Loft Hatch is in the loft. Go there first.']
    assert solo.zone(ARDEN) == 'yard'


# An improvised attempt is told, not invented (amendment 7) -------------------------------------------------------


def _roll_for(table: YardTable) -> dict:
    roll = {'die': 'd20', 'mode': 'normal', 'result_visibility': 'hidden_until_landed', 'target_pending_turn_id': table.last_turn_id()}
    return {'kind': 'roll', 'source': 'dice_tray', 'roll': roll}


@pytest.mark.parametrize(('table', 'line'), [('solo', 'You manage it, for what it is worth.'), ('party', 'Arden Vale manages it, for what it is worth.')])
def test_an_improvised_attempt_is_told_in_the_engines_words(request, table, line):
    table = request.getfixturevalue(table)
    table.send(ARDEN, 'I climb onto the stable roof.')

    table.send(ARDEN, 'I roll.', _roll_for(table))

    assert table.last()['narration'].endswith(line)
    assert table.facts() == []


def test_an_improvised_attempt_tells_an_ai_narrator_the_engine_changed_nothing(ai):
    ai.send(ARDEN, 'I climb onto the stable roof.')

    assert _model_calls(ai, lambda: ai.send(ARDEN, 'I roll.', _roll_for(ai))) == (1, 0)

    assert _line(ai, 'Did: ').startswith(
        'Did: Improvised — "I climb onto the stable roof.": Athletics 19 vs DC 14, success. The engine changed nothing.'
    )
    assert IMPROVISED_RULE in ai.narrator.prompts[-1]
    assert ai.last()['pipeline']['postDmExtraction']['debug']['reason'] == 'improvised'


# Every check is a roll card (amendment 8) ------------------------------------------------------------------------


def _names(received: list[dict]) -> list[str]:
    return [event['name'] for event in received]


def test_an_affordance_check_is_a_card_on_the_table_before_the_narration(solo):
    received = solo.affordance(ARDEN, 'yard_gate', 'bar_gate', 'Shut and bar the yard gate')

    sent = next(event['args'][0] for event in received if event['name'] == 'turn_rolls')
    assert _names(received).index('turn_rolls') < _names(received).index('dm_chunk')
    assert [(roll['kind'], roll['label']) for roll in sent['rolls']] == [('check', 'Athletics check')]


def test_a_conversation_check_is_a_card_on_the_table_before_the_narration(app, client, socketio, monkeypatch):
    from tests.test_conversations import TOVIN, TalkTable
    from tests.test_where_everyone_stands import _seeded

    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    _seeded(monkeypatch)
    table = TalkTable(app, client, socketio, party=False)
    table.talk(ARDEN, 'Tovin')
    for topic_id in ('tovin_wheels', 'tovin_horses', 'tovin_bench', 'tovin_bell'):
        table.topic(ARDEN, TOVIN, topic_id)

    received = table.topic(ARDEN, TOVIN, 'tovin_gently')

    sent = next(event['args'][0] for event in received if event['name'] == 'turn_rolls')
    assert _names(received).index('turn_rolls') < _names(received).index('dm_chunk')
    (card,) = sent['rolls']
    assert (card['kind'], card['label'], card['against'], card['outcome'], card['target']['name']) == (
        'check',
        'Persuasion check',
        {'kind': 'DC', 'value': 11},
        'success',
        'Tovin Bell',
    )
    table.close()


# Written words are told as written (amendment 9) -----------------------------------------------------------------


def test_an_ai_narrator_tells_around_a_failing_forward_text_it_is_given_as_told(ai):
    ai.narrator.replies.append('You plunge an arm into the barrel.')

    ai.act(ARDEN, 'rain_barrel', 'search')

    assert ai.last()['narration'] == 'You plunge an arm into the barrel.\n\nCold water to the elbow, and a drowned glove that is not yours.'
    assert _line(ai, 'Did: ') == 'Did: Search Rain Barrel (Investigation check: 15 — failure): failed.'
    assert _line(ai, 'Already told this turn') == (
        'Already told this turn (do not repeat or contradict): Cold water to the elbow, and a drowned glove that is not yours.'
    )


def test_a_fights_result_text_follows_the_enemy_phase_the_model_tells(app, client, socketio, monkeypatch):
    narrator = ai_narration(app, monkeypatch)
    _dice(monkeypatch)
    install(monkeypatch)
    table = YardTable(app, client, socketio, party=True)
    table.narrator = narrator
    _start_fight(table)

    for _step in range(20):
        if table.combat().get('status') != 'active':
            break
        narrator.replies.append('The cutthroats move.')
        table.hud(table.acting_hero(), 'End turn.', 'combat.end_turn')

    assert table.combat()['flags']['endReason'] == 'enemy_goal_met'
    assert table.last()['narration'] == 'The cutthroats move.\n\nHoofbeats fade up the lane.'
    brief = ENGINE_BRIEF_RE.search(narrator.prompts[-1]).group('brief')
    assert 'Already told this turn (do not repeat or contradict): Hoofbeats fade up the lane.' in brief.splitlines()
    table.close()


# The pack: linter and schema -------------------------------------------------------------------------------------


def _lint(pack: dict) -> list[tuple[str, str, str]]:
    from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest

    return [(issue['severity'], issue['code'], issue['path']) for issue in lint_campaign_pack_manifest(pack, workspace_id='owner', strict=True)['issues']]


def test_the_yard_lints_strictly_clean(app):
    with app.app_context():
        assert _lint(yard_pack()) == []


def _gate(pack: dict) -> dict:
    return pack['locations'][0]['interactables'][0]['affordances'][0]


@pytest.mark.parametrize(
    ('change', 'expected'),
    [
        (lambda pack: _gate(pack).update(effect='fly'), ('error', 'unknown_effect', 'locations[0].interactables[0].affordances[0].effect')),
        (lambda pack: pack['locations'][0]['interactables'][1]['tags'].append('glowing'), ('error', 'unknown_object_tag', 'locations[0].interactables[1].tags[2]')),
        (lambda pack: _gate(pack)['result'].update(stakes={'exit_route': 'blocked'}), ('error', 'unknown_stake', 'locations[0].interactables[0].affordances[0].result.stakes.exit_route')),
        (lambda pack: _gate(pack).update(target='stake:horse'), ('error', 'unknown_stake', 'locations[0].interactables[0].affordances[0].target')),
        (lambda pack: pack['encounters'][0]['stakes'][1].update(npcId='npc_nobody'), ('error', 'unknown_reference', 'encounters[0].stakes[1].npcId')),
        (lambda pack: pack['encounters'][0]['enemyGoal'].update(stakeId='witness'), ('error', 'unknown_stake', 'encounters[0].enemyGoal.stakeId')),
        (lambda pack: pack['encounters'][0]['results'][0]['when']['stakes'].update(fragment='lost'), ('error', 'invalid_stake_state', 'encounters[0].results[0].when.stakes.fragment')),
        (lambda pack: pack['encounters'][0].update(onDefeat={'wakeZoneId': 'cellar', 'text': 'You wake.'}), ('error', 'unknown_reference', 'encounters[0].onDefeat.wakeZoneId')),
        (lambda pack: pack['encounters'][0].update(minEnemies=9), ('error', 'invalid_floor', 'encounters[0].minEnemies')),
        (lambda pack: pack['encounters'][0]['enemyGroups'][0].update(minHp=0), ('error', 'invalid_floor', 'encounters[0].enemyGroups[0].minHp')),
    ],
    ids=['effect', 'tag', 'result stake', 'target', 'person', 'goal stake', 'stake state', 'wake zone', 'min enemies', 'min hp'],
)
def test_the_linter_reports_plans_and_stakes_the_engine_cannot_use(app, change, expected):
    pack = yard_pack()
    change(pack)

    with app.app_context():
        assert expected in _lint(pack)


def test_the_schema_holds_tags_affordances_stakes_goals_and_floors(app):
    from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest

    pack = yard_pack()
    pack['encounters'][0]['enemyGoal']['type'] = 'burn_it_down'
    pack['encounters'][0]['minEnemies'] = 2
    pack['encounters'][0]['enemyGroups'][0]['minHp'] = 14
    with app.app_context():
        issues = lint_campaign_pack_manifest(pack, workspace_id='owner')['issues']

    assert [(issue['code'], issue['message']) for issue in issues] == [
        ('invalid_campaign_pack_schema', 'encounters[0].enemyGoal.type must be one of: take_and_flee.')
    ]
