"""Pace, voice and the finishing blow: brisk AI narration that ends on what matters, scripted sentences
without the numbers the roll cards show, enemies with a voice (``barks``), the hero's own killing blow,
three corrections from playing the fight work, and fights that narration alone never starts.

Socket tests run the Play Now adventure's caravan fight for two heroes (Thalion, then Corin Ashby, then the
Ashmarked Cutthroat) with the seeded dice of ``tests/test_fight_maneuvers.py``.
"""

from __future__ import annotations

from copy import deepcopy
import re

import pytest
import requests

from aidm_server.combat.pipeline import _should_start_combat_from_dm_response
from aidm_server.game_state.extraction.free_text_actions import fight_words
from aidm_server.game_state.extraction.post_dm_outcome_extractor import extract_post_dm_outcomes
from aidm_server.game_state.orchestration.finishing_blow import finisher_actions, finishing_blow
from aidm_server.game_state.orchestration.narration_facts import enemy_barks, next_actor_line
from aidm_server.models import safe_json_loads
from aidm_server.narration_hygiene import clean_ai_narration
from aidm_server.prompt_templates import build_dm_stream_request
from aidm_server.scripted_narrator import ROLLED_TEMPLATES, engine_turn_line, narrate
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from tests.ai_narrator import ENGINE_BRIEF_RE, AiTable, ai_narration
from tests.test_fight_maneuvers import FightTable, errors
from tests.test_scripted_narrator import _context, _hint, _pipeline

ARDEN = 'Arden Vale'
SEREN = 'Seren Ashgrove'
PARTY = {'actor': SEREN, 'heroes': [{'id': 'player_1', 'name': ARDEN}, {'id': 'player_2', 'name': SEREN}]}
CLOSING = 'The ashmarked cutthroat is finished. Thalion, how do you end it?'
START_BARK = re.compile(r'"Names, ledgers and stone\. Hand them over and walk away," the ashmarked cutthroat (says|spits|hisses|calls)\.')
NEW_RULES = (
    '11. Be brisk. In a fight: at most 60 words. Outside a fight: at most 90 words. Arriving somewhere new or opening a '
    'chapter: at most 140. An ending may run long.',
    '12. Open on what the hero does or perceives, not on the weather twice running.',
    '13. End on what matters now, taken from what this brief already holds: the result just told, someone here waiting '
    'on an answer, or something under "Here" still untouched. Invent nothing to end on: no new sound, movement, object, '
    'clue or mystery. After a win, a discovery, a kindness or a joke, let that moment land and end there. Never a '
    'question to the players, never a list of options, never the name of a button.',
    '14. When someone present would plainly react to what the hero just did, give them one short spoken line in their '
    'own voice: a reaction, never a new fact about what happened here.',
    '15. Do not start a fight, and do not bring any creature, enemy or new danger into the scene, unless "Did" says a '
    'fight began. What belongs to other places stays there.',
)
DICE_RULE = (
    '16. The dice are shown to the players. Tell what happened in the fiction; do not restate roll totals, ACs, DCs or '
    'damage numbers.'
)
ROLL_STUB = [{'id': 't4-1', 'kind': 'attack'}]


@pytest.fixture()
def safe_mode(app, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 14 if sides == 20 else sides - 1)
    monkeypatch.setattr('aidm_server.combat.actions.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda sides: min(sides, 12))


@pytest.fixture()
def table(app, client, socketio, safe_mode, monkeypatch):
    fight = FightTable(app, client, socketio, monkeypatch)
    yield fight
    fight.close()


@pytest.fixture()
def ai_table(app, client, socketio, monkeypatch):
    narrator = ai_narration(app, monkeypatch)
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 14 if sides == 20 else sides - 1)
    monkeypatch.setattr('aidm_server.combat.actions.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda sides: min(sides, 12))
    fight = FightTable(app, client, socketio, monkeypatch)
    fight.narrator = narrator
    yield fight
    fight.close()


def _said(table: FightTable, hero: str, message: str, reply: str | None = None, intent: dict | None = None) -> dict:
    table.narrator.replies.append(reply)
    assert errors(table.send(hero, message, intent)) == []
    return table.last()


def _felled_by_thalion(table: FightTable) -> dict:
    """Thalion's longbow hits for 8 (seeded): the cutthroat, at 5 HP, is the last enemy and falls."""

    table.set_enemy_hp(5)
    table.hud('Thalion', 'combat.attack.starter_ranger_longbow', table.enemy()['id'])
    return table.last()


def _brief(table: FightTable) -> str:
    return ENGINE_BRIEF_RE.search(table.narrator.prompts[-1]).group('brief')


# E. Three corrections ------------------------------------------------------------------------------------------------


def _failed_break(narration: dict) -> str:
    fact = {
        'targetId': 'inn_wagon_strongbox', 'name': 'Wagon Strongbox', 'action': 'break', 'failed': True,
        'label': 'Break the strongbox open', 'labelAuthored': True, 'actionText': 'The lid splits and swings free.',
    }
    change = {'type': 'scene.interactable.action', 'targetId': 'inn_wagon_strongbox', 'objectAction': 'break'}
    return narrate(_context(), _hint(state_pipeline=_pipeline(stateChangesAlreadyApplied=[change], narration={'objects': [fact], **narration})), 'break it', ARDEN)


def test_a_failed_attempt_tries_and_never_says_it_was_done():
    assert _failed_break({}) == 'You try to break the strongbox open. It will not give.'
    assert _failed_break({'party': {**PARTY, 'actor': ARDEN}}) == 'Arden Vale tries to break the strongbox open. It will not give.'


@pytest.mark.parametrize(
    ('message', 'kind'),
    [
        ('Question the Ashmarked Cutthroat', 'speech'),
        ('I threaten him with the noose', 'speech'),
        ('Thalion laughs at him', 'speech'),
        ('Look for an opening', 'look'),
        ('I study the cutthroat', 'look'),
        ('search for a way past him', 'look'),
        ('I demand his surrender', 'action'),
        ('I kick the lantern at it', 'improvised'),
    ],
)
def test_typed_words_in_a_fight_are_speech_a_look_an_action_or_improvised(message, kind):
    target = {'id': 'enemy_goblin', 'name': 'Goblin', 'available': True, 'reason': ''}
    demand = {'id': 'combat.demand_surrender', 'type': 'demand_surrender', 'available': True, 'requiresTarget': True, 'targets': [target]}
    attack = {'id': 'combat.attack.dagger', 'type': 'attack', 'available': True, 'requiresTarget': True, 'targets': [target]}
    bundle = {'isCurrentActor': True, 'actorName': 'Thalion', 'actions': [attack, demand]}
    enemy = {'id': 'enemy_goblin', 'name': 'Goblin', 'team': 'enemy', 'hp': {'current': 3, 'max': 10}, 'conditions': []}

    assert fight_words({'combat': {'participants': [enemy]}}, bundle, message=message).kind == kind


def test_questioning_in_a_fight_is_free_and_rolls_nothing(table):
    received = table.type('Thalion', 'Question the Ashmarked Cutthroat')

    assert errors(received) == []
    turn = table.last()
    assert turn['narration'].endswith("\n\nIt is still Thalion's turn.")
    assert 'Athletics' not in turn['narration'] and 'DC' not in turn['narration']
    assert turn['beats'] == []
    assert table.combat()['flags']['turnEconomy']['actionRemaining'] == 1


def test_a_look_in_a_fight_tells_where_everyone_stands_and_costs_nothing(table):
    told = table.type('Thalion', 'I study the cutthroat')

    assert errors(told) == []
    assert table.last()['narration'].endswith(
        'The ashmarked cutthroat is a few strides off, unhurt; Corin Ashby is a few strides off.\n\nIt is still Thalion\'s turn.'
    )
    assert table.combat()['flags']['turnEconomy']['actionRemaining'] == 1


def test_the_ai_narrator_is_told_a_look_is_free_and_where_everyone_stands(ai_table):
    _said(ai_table, 'Thalion', 'I study the cutthroat', 'The cutthroat waits by the wagons, knife low.')
    did = next(line for line in _brief(ai_table).splitlines() if line.startswith('Did: '))
    assert 'The hero looked over the fight: free, nothing is spent and nothing changes. Describe what they see in at most two sentences.' in did
    assert 'Where everyone stands: the ashmarked cutthroat a few strides off, unhurt; Corin Ashby a few strides off.' in did


def test_the_turn_line_is_still_only_while_nobody_else_has_acted(table):
    state = table.snapshot()
    thalion = table.hero_id('Thalion')
    assert next_actor_line(state, actor_id=thalion, turn_ends=False) == "It is still Thalion's turn."
    assert next_actor_line(state, actor_id=thalion, turn_ends=True) == 'Corin Ashby is up next.'
    # With Corin down the round comes straight back to Thalion once their turn ends.
    corin = next(participant for participant in state['combat']['participants'] if participant['name'] == 'Corin Ashby')
    corin.update({'hp': {**corin['hp'], 'current': 0}, 'conditions': ['unconscious', 'stable'], 'isConscious': False})
    assert next_actor_line(state, actor_id=thalion, turn_ends=True) == "It is Thalion's turn again."
    alone = deepcopy(state)
    alone['playerCharacters'] = [hero for hero in alone['playerCharacters'] if hero['id'] == thalion]
    assert next_actor_line(alone, actor_id=thalion, turn_ends=True) == 'It is your turn.'
    assert next_actor_line(alone, actor_id=thalion, turn_ends=False) == 'It is your turn.'


# A. Pace: the rules and the trim -------------------------------------------------------------------------------------


def _brief_hint(*, heroes: list[str], rolls: list | None = None, narration: dict | None = None, **pipeline) -> dict:
    brief = {'did': ['Thalion attacks.'], 'here': {}, 'heroes': heroes, 'actingHero': heroes[0], 'offered': []}
    return {
        'requires_roll': False,
        'state_pipeline': {'engineBrief': brief, 'narration': narration or {}, **({'storyRolls': rolls} if rolls is not None else {}), **pipeline},
    }


@pytest.mark.parametrize('heroes', [['Thalion'], ['Thalion', 'Corin Ashby']], ids=['solo', 'party'])
def test_the_pace_rules_are_given_to_the_ai_narrator_and_the_dice_rule_only_with_roll_cards(heroes):
    plain = build_dm_stream_request('I attack.', '{}', rules_hint=_brief_hint(heroes=heroes)).prompt
    carded = build_dm_stream_request('I attack.', '{}', rules_hint=_brief_hint(heroes=heroes, rolls=ROLL_STUB)).prompt
    empty = build_dm_stream_request('I attack.', '{}', rules_hint=_brief_hint(heroes=heroes, rolls=[])).prompt

    for prompt in (plain, carded):
        for rule in NEW_RULES:
            assert rule in prompt
    assert DICE_RULE not in plain and DICE_RULE not in empty
    assert f'{NEW_RULES[-1]}\n{DICE_RULE}\n\nPLAYER INPUT:' in carded


def _sentences(count: int, words: int = 10) -> str:
    return ' '.join(f"Sentence {index} {' '.join(['word'] * (words - 3))} ends." for index in range(count))


@pytest.fixture()
def trimmed(monkeypatch):
    counted = []
    monkeypatch.setattr('aidm_server.narration_hygiene.telemetry_metric', lambda name, value, **_kw: counted.append((name, value)))
    return counted


def test_a_narration_over_twice_its_limit_is_cut_at_a_sentence_end(trimmed):
    fight = _brief_hint(heroes=['Thalion'], combatState={'status': 'active'})
    outside = _brief_hint(heroes=['Thalion'])

    cut = clean_ai_narration(_sentences(14), fight).text
    assert cut == _sentences(12)
    assert trimmed == [('narration.hygiene.trimmed_total', 1)]
    assert clean_ai_narration(_sentences(18), outside).text == _sentences(18)
    assert clean_ai_narration(_sentences(19), outside).text == _sentences(18)


def test_the_trim_never_cuts_inside_a_quotation_and_spares_arrivals_openings_and_endings(trimmed):
    fight = _brief_hint(heroes=['Thalion'], combatState={'status': 'active'})
    quoted = f'{_sentences(11)} "Hold. Stand your post. Hold the road, hold it, hold it," the warden says.'

    assert clean_ai_narration(quoted, fight).text == _sentences(11)
    long = _sentences(25)
    arrival = _brief_hint(heroes=['Thalion'], narration={'arrival': 'The Old North Road'})
    ending = _brief_hint(heroes=['Thalion'])
    ending['state_pipeline']['engineBrief']['ending'] = True
    opening = {**_brief_hint(heroes=['Thalion']), 'message_kind': 'opening'}
    for hint in (arrival, ending, opening):
        assert clean_ai_narration(long, hint).text == long
    assert len(trimmed) == 1


def test_the_sanctioned_finisher_question_survives_the_hygiene_pass_once_and_last():
    closing = 'The hollow marshal is finished. How do you end it?'
    long = _sentences(14)
    hint = _brief_hint(
        heroes=['Thalion'],
        combatState={'status': 'active'},
        narration={'finishingBlow': {'enemyId': 'enemy_1', 'enemy': 'Hollow Marshal', 'closing': closing}, 'nextActorLine': "It is still Thalion's turn."},
    )

    # As the narration service does: the cleaned reply, then the engine's own closing lines.
    told = f"{clean_ai_narration(f'{long} {closing}', hint).text}\n\n{engine_turn_line(hint)}"
    assert told == f"{_sentences(12)}\n\nIt is still Thalion's turn.\n\n{closing}"
    assert told.count('How do you end it?') == 1


# B. Numbers leave the scripted sentences -----------------------------------------------------------------------------

HERO_ATTACK = {'sourceActorName': ARDEN, 'targetName': 'Wardbound Beast', 'weaponName': 'Longsword', 'attackTotal': 17,
               'targetArmorClass': 13, 'hit': True, 'damageTotal': 9, 'damageType': 'slashing'}
ENEMY_ATTACK = {'enemyId': 'enemy_beast_1', 'enemyName': 'Wardbound Beast', 'targetName': ARDEN, 'abilityName': 'Dust-Marked Pounce',
                'attackTotal': 16, 'targetArmorClass': 16, 'hit': True, 'damageTotal': 7, 'damageType': 'piercing'}
ENEMY_SAVE = {'enemyId': 'enemy_lantern_1', 'enemyName': 'Pale Lantern', 'targetName': ARDEN, 'abilityName': 'Lantern-Lure',
              'saveAbility': 'wisdom', 'saveDC': 12, 'saveTotal': 12, 'saveSucceeded': True, 'damageTotal': 0}
SPELL = {'type': 'spell.cast', 'spellName': 'Sacred Flame', 'spellResolution': {'spellName': 'Sacred Flame', 'targets': [
    {'targetName': 'Wardbound Beast', 'delivery': {'type': 'save', 'ability': 'dexterity', 'total': 9, 'dc': 13, 'saveSucceeded': False},
     'effects': [{'kind': 'damage', 'applied': True, 'amountApplied': 6, 'damageType': 'radiant'}]}]}}
ENEMIES = [{'name': 'Wardbound Beast', 'proper': False}, {'name': 'Pale Lantern', 'proper': False}]
OUTCOMES = {
    'hero_hit': ('hero', {}),
    'hero_miss': ('hero', {'hit': False}),
    'hero_critical': ('hero', {'critical': True}),
    'enemy_hit': ('enemy', {}),
    'enemy_miss': ('enemy', {'hit': False}),
    'enemy_critical': ('enemy', {'critical': True}),
    'enemy_save_made': ('save', {}),
    'enemy_save_failed': ('save', {'saveSucceeded': False}),
}


def _rolled_turn(kind: str, change: dict, *, turn: int, party: bool, rolls: list | None) -> str:
    if kind == 'hero':
        attack = {**HERO_ATTACK, **change, **({'sourceActorName': SEREN} if party else {})}
        combat = {'playerResolvedAction': attack, 'trustedDamageEvents': [dict(attack)]}
    else:
        combat = {'enemyResolvedActions': [{**(ENEMY_ATTACK if kind == 'enemy' else ENEMY_SAVE), **change, **({'targetName': SEREN} if party else {})}]}
    narration = {'enemies': ENEMIES, **({'party': PARTY} if party else {})}
    pipeline = _pipeline(combatState=combat, narration=narration, **({'storyRolls': rolls} if rolls is not None else {}))
    return narrate(_context(), _hint(turn_number=turn, state_pipeline=pipeline), 'I fight.', SEREN if party else ARDEN)


@pytest.mark.parametrize('party', [False, True], ids=['solo', 'party'])
@pytest.mark.parametrize('outcome', list(OUTCOMES))
def test_with_roll_cards_each_rolled_result_has_three_numberless_sentences(outcome, party):
    kind, change = OUTCOMES[outcome]
    told = {_rolled_turn(kind, change, turn=turn, party=party, rolls=ROLL_STUB) for turn in range(40)}

    assert len(told) == len(ROLLED_TEMPLATES[outcome]) == 3
    assert not any(re.search(r'\d', sentence) for sentence in told)
    if party:
        assert not any(re.search(r'\byou', sentence, re.IGNORECASE) for sentence in told)


def test_the_numberless_sentences_read_as_written():
    told = {
        key: {_rolled_turn(kind, change, turn=turn, party=key != 'hero_hit', rolls=ROLL_STUB) for turn in range(40)}
        for key, (kind, change) in OUTCOMES.items()
        if key in {'hero_hit', 'enemy_hit'}
    }
    assert 'Your longsword bites deep into the wardbound beast.' in {
        _rolled_turn('hero', {}, turn=turn, party=False, rolls=ROLL_STUB) for turn in range(40)
    }
    assert "The wardbound beast's pounce bears Seren Ashgrove down hard." in told['enemy_hit']
    save = {
        narrate(_context(), _hint(turn_number=turn, state_pipeline=_pipeline(
            combatState={'enemyResolvedActions': [ENEMY_SAVE]},
            narration={'enemies': ENEMIES, 'party': {**PARTY, 'actor': SEREN}},
            storyRolls=ROLL_STUB,
        )), 'I fight.', SEREN)
        for turn in range(40)
    }
    assert "The pale lantern's lure slides off Arden Vale." in save


@pytest.mark.parametrize('rolls', [None, []], ids=['no-cards', 'no-rolls'])
def test_without_roll_cards_the_sentences_keep_their_numbers(rolls):
    hero = _rolled_turn('hero', {}, turn=4, party=False, rolls=rolls)
    enemy = _rolled_turn('enemy', {}, turn=4, party=False, rolls=rolls)
    save = _rolled_turn('save', {}, turn=4, party=False, rolls=rolls)

    assert '17 against AC 13' in hero and '9 slashing damage' in hero
    assert '16 against AC 16' in enemy and '7 piercing damage' in enemy
    assert 'a Wisdom save of 12 against DC 12, a success' in save


def test_with_roll_cards_spells_checks_and_maneuvers_carry_no_numbers():
    spell = narrate(_context(), _hint(state_pipeline=_pipeline(stateChangesAlreadyApplied=[SPELL], narration={'enemies': ENEMIES}, storyRolls=ROLL_STUB)), 'I pray.', ARDEN)
    grapple = {'actionType': 'grapple', 'targetName': 'Wardbound Beast', 'success': True, 'check': {'skill': 'athletics', 'total': 19, 'dc': 12}}
    demand = {'actionType': 'demand_surrender', 'targetName': 'Wardbound Beast', 'success': False, 'check': {'skill': 'intimidation', 'total': 5, 'dc': 12}}
    maneuvers = [
        narrate(_context(), _hint(state_pipeline=_pipeline(combatState={'playerResolvedCombatAction': action}, narration={'enemies': ENEMIES}, storyRolls=ROLL_STUB)), 'Grab it.', ARDEN)
        for action in (grapple, demand)
    ]
    check = narrate(_context(), _hint(roll_value=14, resolved_turn_id=3, authoritative_roll={'total': 14, 'dc': 12, 'outcome': 'success', 'rule_type': 'athletics'},
                                      state_pipeline=_pipeline(storyRolls=ROLL_STUB)), 'I climb.', ARDEN)

    assert spell.startswith('You cast Sacred Flame.') and not re.search(r'\d', spell)
    assert maneuvers == ['You get an arm around the wardbound beast and bear it down. It is going nowhere.', '"Enough," you tell it. It fights on.']
    assert check and not re.search(r'\d|DC', check)


# C. Barks ------------------------------------------------------------------------------------------------------------


def _plan(intent_type: str | None):
    """The cutthroat's next planned intent, or the planner's own when None."""

    from aidm_server.combat import intent_planner

    real = intent_planner._plan_intent_with_candidates

    def plan(enemy, combat, settings):
        intent, candidates = real(enemy, combat, settings)
        return ({**intent, 'intentType': intent_type, 'targetId': None} if intent_type else intent), candidates

    return plan


def test_barks_each_trigger_once_in_the_fight_and_a_taunt_replaces_a_turn_that_only_fills_time(table, monkeypatch):
    start = table.last_turn_id()
    table.steady_enemy()
    monkeypatch.setattr('aidm_server.combat.intent_planner._plan_intent_with_candidates', _plan('complete_objective'))
    table.end_turn('Thalion')
    table.end_turn('Corin Ashby')
    first, second = (turn['narration'] for turn in table.turns_since(start))
    assert START_BARK.match(first)
    assert '"The road\'s already forgotten you," the ashmarked cutthroat' in second
    assert 'turns back to its work' not in second

    # Within two rounds the same quiet turn is told plainly; then the cutthroat is hurt, and yields when tackled.
    table.end_turn('Thalion')
    table.end_turn('Corin Ashby')
    assert 'The ashmarked cutthroat turns back to its work.' in table.last()['narration']
    monkeypatch.setattr('aidm_server.combat.intent_planner._plan_intent_with_candidates', _plan(None))
    table.set_enemy_hp(1)
    table.end_turn('Thalion')
    hurt = table.last()['narration']
    assert re.search(r'"Not paid enough for this," the ashmarked cutthroat (says|spits|hisses|calls)\.', hurt)
    table.type('Corin Ashby', 'Close in and tackle him to the floor')
    table.end_turn('Corin Ashby')
    yielded = table.last()['narration']
    assert re.search(r'^The ashmarked cutthroat goes slack\. "Enough," it says\. "Not worth my neck!" the ashmarked cutthroat (says|spits|hisses|calls)\.$', yielded)

    told = ' '.join(turn['narration'] for turn in table.turns_since(start))
    assert len(START_BARK.findall(told)) == 1 and told.count('Not paid enough for this') == 1
    barks = [bark for turn in table.turns_since(start) for bark in turn['pipeline']['dmContextPacket']['narration'].get('barks') or []]
    assert [bark['kind'] for bark in barks] == ['start', 'taunt', 'hurt', 'turning']


def test_a_line_is_not_repeated_while_another_is_unused():
    enemy = {'id': 'enemy_1', 'name': 'Ashmarked Cutthroat', 'team': 'enemy', 'campaignPackEnemyId': 'enemy_cutthroats',
             'hp': {'current': 10, 'max': 10}, 'conditions': []}
    pack = {'campaignPack': {'catalog': {'enemies': [{'id': 'enemy_cutthroats', 'barks': {'taunt': ['"One."', '"Two."', '"Three."']}}]}}}
    state = {'combat': {'status': 'active', 'round': 1, 'participants': [enemy]}, **pack}
    filler = {'enemyId': 'enemy_1', 'intentType': 'delay', 'resolvedWithoutRoll': True}
    told: list[dict] = []
    for round_number in (1, 3, 5, 7):
        state['combat']['round'] = round_number
        told.extend(enemy_barks(state_before=state, state_after=state, enemy_actions=[filler], applied_changes=[], told=told, turn_id=round_number))
    assert [bark['kind'] for bark in told] == ['taunt'] * 4
    assert sorted(bark['line'] for bark in told[:3]) == ['"One."', '"Three."', '"Two."']
    # A second quiet turn in the next round has no taunt.
    state['combat']['round'] = 8
    assert enemy_barks(state_before=state, state_after=state, enemy_actions=[filler], applied_changes=[], told=told, turn_id=8) == []


def test_the_ai_brief_carries_the_bark_under_the_enemy(ai_table):
    attack = {'action_id': 'combat.attack.starter_ranger_longbow', 'target_id': ai_table.enemy()['id']}
    _said(ai_table, 'Thalion', 'Thalion acts.', None, {'kind': 'combat', 'source': 'combat_hud', 'combat': attack})

    did = next(line for line in _brief(ai_table).splitlines() if line.startswith('Did: '))
    assert (
        'Bark: "Names, ledgers and stone. Hand them over and walk away." (Ashmarked Cutthroat; use this line, word for word if it is speech)'
    ) in did


# D. The finishing blow -----------------------------------------------------------------------------------------------


def test_the_last_enemy_felled_is_left_for_the_hero_to_finish_and_their_words_end_it(table):
    felled = _felled_by_thalion(table)

    assert felled['narration'].endswith(f'\n\n{CLOSING}')
    assert not re.search(r'goes down|crumples|falls', felled['narration'])
    # The fight resolved as ever: victory and its rewards, on this turn.
    assert table.combat()['status'] == 'ended'
    assert 'Victory' in felled['beats'] and '+50 XP each' in felled['beats']
    pending = table.combat()['flags']['pendingFinisher']
    assert pending == {
        'heroId': table.hero_id('Thalion'), 'heroName': 'Thalion', 'enemyId': table.enemy()['id'], 'enemyName': 'Ashmarked Cutthroat',
        'turnId': table.last_turn_id(), 'othersActed': 0, 'waitingLine': 'Thalion is deciding how it ends…',
    }
    assert felled['pipeline']['dmContextPacket']['engineBrief']['offered'][:2] == ['Describe how you end it', 'Let the story tell it']
    assert [action['label'] for action in finisher_actions(table.snapshot(), table.hero_id('Thalion'))] == ['Describe how you end it', 'Let the story tell it']
    assert finisher_actions(table.snapshot(), table.hero_id('Thalion'))[0]['prompt'] == 'How do you end it?'
    assert finisher_actions(table.snapshot(), table.hero_id('Corin Ashby')) == []
    xp = table.snapshot()['playerCharacters'][0]['xp']

    assert errors(table.type('Thalion', 'I put my last arrow through its throat.')) == []
    told = table.last()
    assert told['input'] == 'I put my last arrow through its throat.'
    assert told['narration'] == 'The ashmarked cutthroat falls, and does not rise.'
    assert told['beats'] == []
    assert table.snapshot()['playerCharacters'][0]['xp'] == xp
    assert 'pendingFinisher' not in {key for key, value in table.combat()['flags'].items() if value}
    assert table.combat()['status'] == 'ended'


def test_mercy_words_make_a_captive(table):
    _felled_by_thalion(table)

    assert errors(table.type('Thalion', 'Stay my hand. We take him alive.')) == []
    spared = table.last()
    assert spared['narration'] == 'Thalion turns the blow. The ashmarked cutthroat lives, and is theirs.'
    assert spared['beats'] == ['Captured — Ashmarked Cutthroat']
    assert table.snapshot()['currentScene']['captives'] == [
        {'id': table.enemy()['id'], 'name': 'Ashmarked Cutthroat', 'enemyId': 'enemy_ashmarked_cutthroats'}
    ]
    assert table.enemy()['hp']['current'] == 1
    assert errors(table.type('Corin Ashby', 'Question the Ashmarked Cutthroat')) == []
    assert table.last()['narration'].startswith('"The road. Lately it don\'t take you where you\'re going.')


def test_alone_the_spared_enemy_is_yours():
    told = {'enemyId': 'enemy_1', 'enemy': 'Ashmarked Cutthroat', 'words': 'Spare him.', 'spared': True}
    text = narrate(_context(), _hint(state_pipeline=_pipeline(narration={'finisher': told, 'enemies': [{'name': 'Ashmarked Cutthroat'}]})), 'Spare him.', ARDEN)
    assert text == 'You turn the blow. The ashmarked cutthroat lives, and is yours.'


@pytest.mark.parametrize('path', ['story', 'other-action', 'others-twice'])
def test_the_finisher_lapses_and_the_enemy_simply_falls(table, path):
    _felled_by_thalion(table)

    if path == 'story':
        assert errors(table.type('Thalion', 'Let the story tell it')) == []
        assert table.last()['narration'] == 'The ashmarked cutthroat falls.'
    elif path == 'other-action':
        table.send('Thalion', 'Thalion reads the ledger.', {'kind': 'object', 'source': 'scene_panel', 'object': {'id': 'inn_caravan_ledger', 'action': 'inspect'}})
        assert table.last()['narration'].startswith('The ashmarked cutthroat falls.\n\n')
    else:
        assert errors(table.type('Corin Ashby', 'I look around')) == []
        assert 'falls' not in table.last()['narration']
        assert table.combat()['flags']['pendingFinisher']['othersActed'] == 1
        assert errors(table.type('Corin Ashby', 'I look around')) == []
        assert table.last()['narration'].startswith('The ashmarked cutthroat falls.\n\n')
    assert not table.combat()['flags'].get('pendingFinisher')
    # Words after it lapses are an ordinary turn again.
    assert errors(table.type('Thalion', 'I look around')) == []
    assert 'falls' not in table.last()['narration']


def _marshal_state(marshal_hp: int, warden_hp: int, *, tier: str = 'deadly', proper: bool = True) -> dict:
    pack = {'campaignPack': {'catalog': {'enemies': [
        {'id': 'enemy_hollow_marshal', 'name': 'The Hollow Marshal', 'proper': proper, 'challengeTier': tier},
        {'id': 'enemy_name_eaten_roadwardens', 'name': 'Name-Eaten Roadwardens', 'challengeTier': 'standard'},
    ]}}}
    heroes = [{'id': 'player_1', 'name': 'Corin Ashby'}, {'id': 'player_2', 'name': 'Thalion'}]
    participants = [
        {'id': 'player_1', 'name': 'Corin Ashby', 'team': 'player', 'hp': {'current': 10, 'max': 10}},
        {'id': 'enemy_marshal_1', 'name': 'The Hollow Marshal', 'team': 'enemy', 'campaignPackEnemyId': 'enemy_hollow_marshal',
         'hp': {'current': marshal_hp, 'max': 40}, 'conditions': []},
        {'id': 'enemy_warden_1', 'name': 'Name-Eaten Roadwarden', 'team': 'enemy', 'campaignPackEnemyId': 'enemy_name_eaten_roadwardens',
         'hp': {'current': warden_hp, 'max': 20}, 'conditions': []},
    ]
    return {'playerCharacters': heroes, 'combat': {'status': 'active', 'round': 2, 'participants': participants, 'flags': {}}, **pack}


@pytest.mark.parametrize(
    ('tier', 'proper', 'set'),
    [('deadly', True, True), ('hard', False, True), ('boss', False, True), ('standard', False, False)],
    ids=['proper', 'hard', 'boss', 'standard-with-others-standing'],
)
def test_a_proper_or_hard_foe_is_finished_by_the_hero_while_the_fight_goes_on(tier, proper, set):
    changes, facts = finishing_blow(
        state_before=_marshal_state(6, 20, tier=tier, proper=proper),
        state=_marshal_state(0, 20, tier=tier, proper=proper),
        actor_id='player_1',
        turn_id=9,
        action_intent={'kind': 'combat'},
        applied_changes=[],
    )

    if not set:
        assert (changes, facts) == ([], {})
        return
    assert changes[0]['flags']['pendingFinisher']['enemyId'] == 'enemy_marshal_1'
    closing = f"{'The Hollow Marshal' if proper else 'The hollow marshal'} is finished. Corin Ashby, how do you end it?"
    assert facts['finishingBlow']['closing'] == closing
    # The scripted narration says whose turn it is, then asks; the engine's lines for the AI narrator end the same.
    hint = _hint(state_pipeline=_pipeline(narration={
        'enemies': [{'name': 'The Hollow Marshal', 'proper': proper}], 'party': {**PARTY, 'actor': 'Corin Ashby'},
        'nextActorLine': "It is still Corin Ashby's turn.", 'finishingBlow': facts['finishingBlow'],
    }))
    assert narrate(_context(), hint, 'I strike.', 'Corin Ashby').endswith(f"It is still Corin Ashby's turn.\n\n{closing}")
    assert engine_turn_line(hint) == f"It is still Corin Ashby's turn.\n\n{closing}"


def test_never_two_finishers_at_once():
    state_before = _marshal_state(6, 4, tier='deadly')
    state = _marshal_state(0, 0, tier='deadly')
    changes, facts = finishing_blow(state_before=state_before, state=state, actor_id='player_1', turn_id=9, action_intent=None, applied_changes=[])
    assert [change['flags']['pendingFinisher']['enemyId'] for change in changes] == ['enemy_marshal_1']
    state['combat']['flags']['pendingFinisher'] = changes[0]['flags']['pendingFinisher']
    state_before['combat']['flags']['pendingFinisher'] = changes[0]['flags']['pendingFinisher']
    # Another hero's blow while one is pending is told as any other.
    other_changes, other_facts = finishing_blow(state_before=state_before, state=state, actor_id='player_2', turn_id=10, action_intent=None, applied_changes=[])
    assert 'finishingBlow' not in other_facts
    assert other_changes[0]['flags']['pendingFinisher']['othersActed'] == 1


def test_the_ai_narrator_tells_the_players_own_finishing_words(ai_table):
    ai_table.set_enemy_hp(5)
    felled = _said(
        ai_table, 'Thalion', 'Thalion acts.',
        'Thalion looses, and the cutthroat folds over the wagon tongue. The ashmarked cutthroat is finished. How do you end it?',
        {'kind': 'combat', 'source': 'combat_hud', 'combat': {'action_id': 'combat.attack.starter_ranger_longbow', 'target_id': ai_table.enemy()['id']}},
    )
    assert felled['narration'] == f'Thalion looses, and the cutthroat folds over the wagon tongue.\n\n{CLOSING}'
    assert 'Ashmarked Cutthroat is finished: down at Thalion\'s mercy, not yet dead.' in _brief(ai_table)

    told = _said(ai_table, 'Thalion', 'I spare him and bind his hands.', 'Thalion lowers the bow and binds him.')
    did = next(line for line in _brief(ai_table).splitlines() if line.startswith('Did: '))
    assert did.startswith(
        'Did: Finisher — the player\'s own words. Tell it faithfully in at most three sentences; add no new facts and no new dangers: '
        '"I spare him and bind his hands." The hero spares the enemy; it is alive and captured.'
    )
    assert told['narration'] == 'Thalion lowers the bow and binds him.'
    assert ai_table.snapshot()['currentScene']['captives'][0]['name'] == 'Ashmarked Cutthroat'


# F. Narration alone never starts a fight -----------------------------------------------------------------------------

# Matches the narration's fight-start pattern ("draws its weapon") and names the road's pale lanterns.
LANTERNS_AT_THE_INN = 'A bell tolls three times and pale lanterns kindle in the inn yard. A hooded shape at the gate draws its weapon.'


def test_narration_alone_starts_no_fight_and_the_road_keeps_its_own_encounter(app, client, socketio, monkeypatch):
    narrator = ai_narration(app, monkeypatch)
    table = AiTable(app, client, socketio, narrator)

    table.reply('I ask Osric about the road north.', LANTERNS_AT_THE_INN)
    snapshot = table.snapshot()
    assert (snapshot.get('combat') or {}).get('status') in {None, 'none'}
    assert not (snapshot.get('flags') or {}).get('campaignPackTriggeredEncounterIds')
    assert snapshot['playerCharacters'][0]['xp']['current'] == 0

    table.reply('The party travels to The Old North Road.', 'The road climbs out of the rain.', action_intent={
        'kind': 'travel', 'source': 'scene_panel', 'location': {'id': 'loc_old_north_road', 'name': 'The Old North Road'},
        'text': 'The party travels to The Old North Road.',
    })
    combat = table.snapshot()['combat']
    assert combat['status'] == 'active' and combat['flags']['campaignPackEncounterId'] == 'enc_pale_lantern_detour'


def test_a_hero_who_attacks_someone_present_fights_that_person_only(app, client, socketio, monkeypatch):
    narrator = ai_narration(app, monkeypatch)
    table = AiTable(app, client, socketio, narrator)

    table.reply('I attack Osric', 'Osric throws up his arms and draws a weapon.')
    combat = table.snapshot()['combat']
    assert combat['status'] == 'active'
    assert [participant['name'] for participant in combat['participants'] if participant['team'] == 'enemy'] == ['Osric Hale']
    assert not combat['flags'].get('campaignPackEncounterId')


def test_only_a_due_trigger_or_an_attack_lets_narration_start_a_fight_in_a_pack_adventure():
    started = 'Blades drawn, the bandits attack you.'
    assert _should_start_combat_from_dm_response({'combat': {}}, started) is True
    pack = {'packId': 'pack', 'catalog': {'encounters': []}}
    assert _should_start_combat_from_dm_response({'combat': {}, 'campaignPack': pack}, started) is False
    due = {**pack, 'catalog': {'encounters': [{'id': 'enc_due', 'trigger': {'type': 'on_checkpoint', 'checkpointId': 'cp_1'}}]}}
    assert _should_start_combat_from_dm_response({'combat': {}, 'campaignPack': {**due, 'activeCheckpointId': 'cp_1'}}, started) is True



# The finisher in the player view and the fight's action list ---------------------------------------------------------


def test_the_player_view_carries_the_pending_finisher_and_only_its_hero_sees_its_actions(table):
    _felled_by_thalion(table)
    snapshot = table.snapshot()
    views = {
        hero: filter_session_snapshot_for_player(snapshot, private_player_ids=frozenset({table.players[hero]}))['combat']
        for hero in ('Thalion', 'Corin Ashby')
    }

    shared = {'heroId': table.hero_id('Thalion'), 'heroName': 'Thalion', 'enemyName': 'Ashmarked Cutthroat', 'waitingLine': 'Thalion is deciding how it ends…'}
    assert views['Thalion']['pendingFinisher'] == {**shared, 'offeredActions': finisher_actions(snapshot, table.hero_id('Thalion'))}
    assert [action['label'] for action in views['Thalion']['pendingFinisher']['offeredActions']] == ['Describe how you end it', 'Let the story tell it']
    # Another player sees who is deciding, never the finishing hero's actions; nothing else of the flags shows.
    assert views['Corin Ashby']['pendingFinisher'] == shared
    assert all('pendingFinisher' not in view['flags'] and 'enemyId' not in view['pendingFinisher'] for view in views.values())


def _pending_mid_fight(table: FightTable) -> None:
    """A finishing blow left to Thalion while the cutthroat fights on (as when a proper foe falls first)."""

    pending = {
        'heroId': table.hero_id('Thalion'), 'heroName': 'Thalion', 'enemyId': 'enemy_hollow_marshal_1', 'enemyName': 'The Hollow Marshal',
        'turnId': table.last_turn_id(), 'othersActed': 0, 'waitingLine': 'Thalion is deciding how it ends…',
    }
    table.operator([{'type': 'combat.update', 'flags': {'pendingFinisher': pending}}])


def test_while_the_fight_goes_on_the_finishing_heros_actions_start_with_the_finisher(table):
    _pending_mid_fight(table)

    actions = list(table.actions('Thalion').values())
    assert [(action['id'], action['kind'], action['available'], action['cost'], action['economy']['endsTurn']) for action in actions[:2]] == [
        ('finisher.describe', 'finisher', True, {'action': 0, 'bonusAction': 0, 'movement': 0, 'reaction': 0}, False),
        ('finisher.story', 'finisher', True, {'action': 0, 'bonusAction': 0, 'movement': 0, 'reaction': 0}, False),
    ]
    assert actions[0]['prompt'] == 'How do you end it?'
    assert not any(action['id'].startswith('finisher.') for action in table.actions('Corin Ashby').values())

    # Taken from the list: the hero's words end it, at no cost, and the turn goes on.
    assert errors(table.send('Thalion', 'I drive the marshal into the mud.', {'kind': 'combat', 'source': 'combat_hud', 'combat': {'action_id': 'finisher.describe'}})) == []
    assert table.last()['narration'].startswith('The Hollow Marshal falls, and does not rise.')
    assert table.combat()['flags']['turnEconomy']['actionRemaining'] == 1 and table.acting_hero() == 'Thalion'
    assert not any(action['id'].startswith('finisher.') for action in table.actions('Thalion').values())


def test_letting_the_story_tell_it_from_the_list_lets_the_moment_pass(table):
    _pending_mid_fight(table)

    assert errors(table.send('Thalion', 'Let the story tell it', {'kind': 'combat', 'source': 'combat_hud', 'combat': {'action_id': 'finisher.story'}})) == []
    assert table.last()['narration'].startswith('The Hollow Marshal falls.\n\n')
    assert table.acting_hero() == 'Thalion' and not table.combat()['flags'].get('pendingFinisher')


# Narration alone never makes the scene a fight ------------------------------------------------------------------------

# The owner's session: the stablehand grabbed, a bell, and pale lights closing in at the inn yard.
LIGHTS_CLOSE_IN = (
    'A bell tolls three times. In the inn yard, pale lights kindle between the wagons and close in on you, '
    'and one lunges at you out of the rain.'
)


def test_narrated_lights_closing_in_leave_the_scene_out_of_a_fight_and_the_road_open(app, client, socketio, monkeypatch):
    narrator = ai_narration(app, monkeypatch)
    table = AiTable(app, client, socketio, narrator)

    table.reply('I approach him and ask him what he saw, quietly.', LIGHTS_CLOSE_IN)
    scene = table.snapshot()['currentScene']
    assert (scene.get('combatState') or 'none') == 'none' and scene.get('sceneType') != 'combat' and (scene.get('dangerLevel') or 0) < 8
    assert (table.snapshot().get('combat') or {}).get('status') in {None, 'none'}

    table.reply('I look around.')
    assert 'Go to The Old North Road' in safe_json_loads(table.turn().metadata_json, {})['state_pipeline']['dmContextPacket']['engineBrief']['offered']
    table.reply('The party travels to The Old North Road.', 'The road climbs out of the rain.', action_intent={
        'kind': 'travel', 'source': 'scene_panel', 'location': {'id': 'loc_old_north_road', 'name': 'The Old North Road'},
        'text': 'The party travels to The Old North Road.',
    })
    snapshot = table.snapshot()
    assert snapshot['currentScene']['locationId'] == 'loc_old_north_road'
    assert (snapshot['combat']['status'], snapshot['combat']['flags']['campaignPackEncounterId']) == ('active', 'enc_pale_lantern_detour')


@pytest.mark.parametrize('pack', [True, False], ids=['pack', 'no-pack'])
def test_without_a_pack_narration_still_sets_the_scene_to_a_fight(app, pack):
    state = {'currentScene': {'locationId': 'loc_yard', 'name': 'Inn Yard', 'sceneType': 'exploration', 'dangerLevel': 2, 'combatState': 'none'}}
    with app.app_context():
        extracted = extract_post_dm_outcomes(
            state_before_dm=state, player_message='I wait.', validated_actions={}, already_applied_changes=[], dm_response=LIGHTS_CLOSE_IN,
            recent_timeline=[], actor_id='player_1', turn_id=3, narration_starts_no_fight=pack,
        )
    scene = [change for change in extracted['proposedChanges'] if change.get('type') == 'scene.update']
    if pack:
        assert [(change.get('combatState'), change.get('sceneType'), change['dangerLevel']) for change in scene] == [(None, None, 7)]
    else:
        assert [(change['combatState'], change['sceneType'], change['dangerLevel']) for change in scene] == [('active', 'combat', 8)]
