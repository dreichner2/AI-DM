"""The rules behind the last pass: enemy conditions wear off, a boss stands its ground, the roll names a wound
penalty, endings name the choice, one fate, choices shown together, and the wording fixes."""

from __future__ import annotations

from copy import deepcopy

import pytest

from aidm_server.character_state import character_roll_spec
from aidm_server.combat import pipeline
from aidm_server.combat.intent_planner import intent_is_restricted
from aidm_server.combat.pipeline import apply_turn_boundaries
from aidm_server.creatures.schemas import normalize_ability
from aidm_server.game_state.orchestration.combat_resolution import derive_trusted_damage_changes
from aidm_server.game_state.orchestration.narration_facts import object_facts
from aidm_server.game_state.orchestration.story_beats import beat_basis
from aidm_server.game_state.validation.validator import validate_state_changes
from aidm_server.interactables import available_actions_for_target, interactable_actor_context, offered_scene_actions
from aidm_server.models import Player
from aidm_server.roll_visibility import public_roll_payload
from aidm_server.scripted_narrator import narrate
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest
from aidm_server.services.campaign_pack_visibility import player_ending
from aidm_server.socket_contracts import roll_resolved_payload
from tests.test_campaign_pack_linter import _engine_clean_pack
from tests.test_combat_actions import ACTOR, _apply, _enemy, _p, _state

LURE = {
    'id': 'lure',
    'name': 'Lantern-Lure',
    'type': 'spell',
    'range': 'near',
    'targetType': 'single',
    'cooldown': 'once_per_combat',
    'save': {'ability': 'wis', 'dc': 13, 'effectOnSuccess': 'none'},
    'conditionsApplied': ['charmed', 'drawn_aside'],
    'durationRounds': 2,
}
WICK = {'id': 'wick', 'name': 'Cold Wick', 'type': 'attack', 'range': 'far', 'attackBonus': 5, 'damage': {'dice': '1d6+2', 'type': 'psychic'}, 'conditionsApplied': ['memory_chill']}
HERO_END_TO_NEXT_START = [
    {'actorId': ACTOR, 'phase': 'end', 'round': 1},
    {'actorId': 'enemy_1', 'phase': 'start', 'round': 1},
    {'actorId': 'enemy_1', 'phase': 'end', 'round': 1},
    {'actorId': ACTOR, 'phase': 'start', 'round': 2},
]


# --- 1. Conditions an enemy inflicts wear off ----------------------------------------------------------------------


def _struck(ability: dict, action: dict) -> dict:
    """The state after the enemy's ability landed on the hero during the enemy block of this turn."""

    state = _state(_p(ACTOR, 'player'), _enemy(abilities=[ability]))
    packet = {
        'combatState': {
            'enemyResolvedActions': [
                {'enemyId': 'enemy_1', 'enemyName': 'Enemy 1', 'targetId': ACTOR, 'abilityId': ability['id'], 'abilityName': ability['name'], **action}
            ]
        }
    }
    changes = derive_trusted_damage_changes(state=state, dm_context_packet=packet, actor_id=ACTOR, turn_id=7, already_applied_changes=[]).all_changes
    return _apply(state, changes)


def _hero(state: dict) -> dict:
    return next(participant for participant in state['combat']['participants'] if participant['id'] == ACTOR)


def _lured() -> dict:
    return _struck(LURE, {'intentType': 'use_ability', 'saveDC': 13, 'saveTotal': 3, 'saveSucceeded': False, 'conditionsApplied': ['charmed', 'drawn_aside']})


def test_an_enemy_abilitys_conditions_carry_their_duration_and_save():
    hero = _hero(_lured())

    assert hero['conditions'] == ['charmed', 'drawn_aside']
    assert hero['charmedBy'] == ['enemy_1']
    effects = hero['activeEffects']
    assert [effect['condition'] for effect in effects] == ['charmed', 'drawn_aside']
    assert {effect['castId'] for effect in effects} == {effects[0]['castId']}
    assert {
        (effect['origin'], effect['sourceName'], effect['casterId'], effect['appliedDuringTurnOf'])
        for effect in effects
    } == {('enemy_ability', 'Lantern-Lure', 'enemy_1', 'enemy_1')}
    assert [(effect['duration'], effect['endSave']) for effect in effects] == [
        ({'rounds': 2, 'tickOn': 'target_turn_end'}, {'ability': 'wisdom', 'dc': 13})
    ] * 2


def test_a_condition_landing_in_the_enemy_turns_of_an_advance_is_not_counted_by_the_hero_turn_before_them(monkeypatch):
    monkeypatch.setattr(pipeline, 'deterministic_roller', lambda _seed: lambda _sides: 1)
    combat = _lured()['combat']

    events = apply_turn_boundaries(combat, deepcopy(HERO_END_TO_NEXT_START), turn_id=7)

    hero = _hero({'combat': combat})
    assert hero['conditions'] == ['charmed', 'drawn_aside']
    assert [effect['duration']['rounds'] for effect in hero['activeEffects']] == [2, 2]
    assert all('appliedDuringTurnOf' not in effect for effect in hero['activeEffects'])
    assert 'shakenOff' not in events[0] and 'conditionSaves' not in events[0]


def test_failed_repeat_saves_hold_the_conditions_for_their_duration_then_they_end(monkeypatch):
    monkeypatch.setattr(pipeline, 'deterministic_roller', lambda _seed: lambda _sides: 1)
    combat = _lured()['combat']
    apply_turn_boundaries(combat, deepcopy(HERO_END_TO_NEXT_START), turn_id=7)

    first = apply_turn_boundaries(combat, deepcopy(HERO_END_TO_NEXT_START), turn_id=8)
    assert _hero({'combat': combat})['conditions'] == ['charmed', 'drawn_aside']
    assert [save['save']['saveSucceeded'] for save in first[0]['conditionSaves']] == [False]

    second = apply_turn_boundaries(combat, deepcopy(HERO_END_TO_NEXT_START), turn_id=9)
    hero = _hero({'combat': combat})
    assert (hero['conditions'], hero['activeEffects'], 'charmedBy' in hero) == ([], [], False)
    assert second[0]['shakenOff'] == [{'participantId': ACTOR, 'sourceName': 'Lantern-Lure', 'conditions': ['charmed', 'drawn_aside']}]


def test_a_successful_repeat_save_ends_every_condition_of_the_use_at_once(monkeypatch):
    monkeypatch.setattr(pipeline, 'deterministic_roller', lambda _seed: lambda _sides: 20)
    combat = _lured()['combat']
    apply_turn_boundaries(combat, deepcopy(HERO_END_TO_NEXT_START), turn_id=7)

    events = apply_turn_boundaries(combat, deepcopy(HERO_END_TO_NEXT_START), turn_id=8)

    assert _hero({'combat': combat})['conditions'] == []
    [shaken] = events[0]['shakenOff']
    assert (shaken['sourceName'], shaken['conditions'], shaken['save']['saveSucceeded']) == ('Lantern-Lure', ['charmed', 'drawn_aside'], True)


def test_without_a_duration_a_condition_lasts_to_the_end_of_the_targets_next_turn():
    combat = _struck(WICK, {'intentType': 'attack', 'attackTotal': 15, 'targetArmorClass': 12, 'hit': True, 'damageTotal': 3, 'conditionsApplied': ['memory_chill']})['combat']
    assert _hero({'combat': combat})['activeEffects'][0]['duration'] == {'rounds': 1, 'tickOn': 'target_turn_end'}
    assert 'endSave' not in _hero({'combat': combat})['activeEffects'][0]

    apply_turn_boundaries(combat, deepcopy(HERO_END_TO_NEXT_START), turn_id=7)
    assert _hero({'combat': combat})['conditions'] == ['memory_chill']
    events = apply_turn_boundaries(combat, deepcopy(HERO_END_TO_NEXT_START), turn_id=8)

    assert _hero({'combat': combat})['conditions'] == []
    assert events[0]['shakenOff'] == [{'participantId': ACTOR, 'sourceName': 'Cold Wick', 'conditions': ['memory_chill']}]


def test_only_the_engine_gives_a_condition_a_duration():
    state = _state(_p(ACTOR, 'player'), _enemy())
    proposed = {
        'id': 'narrated', 'type': 'combat.condition.add', 'participantId': ACTOR, 'condition': 'charmed', 'source': 'post_dm',
        'durationRounds': 3, 'castId': 'x', 'endSave': {'ability': 'wis', 'dc': 30},
    }

    [accepted] = validate_state_changes(state=state, changes=[proposed])['accepted']

    assert not {'durationRounds', 'castId', 'endSave'} & set(accepted['change'])


def test_the_importer_keeps_an_abilitys_duration_within_bounds():
    assert normalize_ability(LURE, creature_id='lantern', index=0)['durationRounds'] == 2
    assert normalize_ability({**LURE, 'durationRounds': 40}, creature_id='lantern', index=0)['durationRounds'] == 10
    assert 'durationRounds' not in normalize_ability({**WICK, 'conditionsApplied': [], 'durationRounds': 2}, creature_id='lantern', index=0)


@pytest.mark.parametrize(
    ('duration', 'conditions', 'severity', 'code'),
    [
        (0, ['charmed'], 'error', 'invalid_condition_duration'),
        (11, ['charmed'], 'error', 'invalid_condition_duration'),
        ('two', ['charmed'], 'error', 'invalid_condition_duration'),
        (2, [], 'warning', 'condition_duration_without_conditions'),
    ],
)
def test_the_linter_checks_condition_durations(app, duration, conditions, severity, code):
    pack = _engine_clean_pack()
    pack['enemies'] = [{'id': 'lantern', 'name': 'Lantern', 'abilities': [{**LURE, 'conditionsApplied': conditions, 'durationRounds': duration}]}]

    with app.app_context():
        issues = lint_campaign_pack_manifest(pack, workspace_id='owner')['issues']

    assert {(issue['severity'], issue['code'], issue['path']) for issue in issues} >= {
        (severity, code, 'enemies[0].abilities[0].durationRounds')
    }


def _narrated(**facts) -> str:
    return narrate(
        {'live_world_state': {'currentScene': {'name': 'Road'}}},
        {
            'turn_number': 3,
            'state_pipeline': {
                'stateChangesAlreadyApplied': [],
                'combatState': facts.pop('combatState', {}),
                'narration': {
                    'enemies': [{'name': 'The Cinder Scribe', 'proper': True}, {'name': 'Ashmarked Cutthroat', 'proper': False}],
                    **facts,
                },
            },
        },
        'I end my turn.',
        speaker='Liora',
    )


def test_the_narrator_says_who_shakes_off_which_ability():
    reply = _narrated(shakenOff=[{'name': 'Liora', 'source': 'Lantern-Lure'}, {'name': 'Arden', 'source': 'Erase the Name'}])

    assert reply == 'You shake off Lantern-Lure. Arden shakes off Erase the Name.'


# --- 2. Condition beats only when a condition starts or ends -------------------------------------------------------


def test_in_a_fight_a_heros_conditions_are_read_from_their_combat_participant():
    actor = _p(ACTOR, 'player', conditions=['charmed'])
    state = _state(actor, _enemy())
    # The hero record is rebuilt from the character row at a turn's start and does not carry fight conditions.
    state['playerCharacters'][0]['health']['conditions'] = []

    assert beat_basis(state, actor_id=ACTOR)['heroes'][ACTOR]['conditions'] == ['charmed']
    state['combat']['status'] = 'ended'
    assert beat_basis(state, actor_id=ACTOR)['heroes'][ACTOR]['conditions'] == []


# --- 3. The roll names a wound penalty -----------------------------------------------------------------------------


def _fighter(current_hp: int) -> Player:
    return Player(
        player_id=1,
        character_name='Arden Vale',
        class_='Fighter',
        level=3,
        stats='{"ability_scores": {"strength": 15}, "max_hp": 28, "current_hp": %d}' % current_hp,
    )


@pytest.mark.parametrize(('current_hp', 'modifiers'), [(28, None), (20, [{'label': 'Wounded', 'value': -1}]), (13, [{'label': 'Wounded', 'value': -2}])])
def test_a_wound_penalty_is_named_among_the_rolls_modifiers(current_hp, modifiers):
    spec = character_roll_spec(_fighter(current_hp), roll_type='attack')

    assert spec.get('modifiers') == modifiers


def test_the_rolls_modifiers_reach_every_viewer():
    roll = {'authoritative': True, 'rule_type': 'attack', 'die': 'd20', 'rolls': [7], 'kept': 7, 'modifier': 2, 'total': 9, 'modifiers': [{'label': 'Wounded', 'value': -2}]}

    room = roll_resolved_payload(session_id=1, turn_id=2, player_id=1, client_message_id=None, pending_turn_id=None, roll=roll, include_private_provenance=False)

    assert room['modifiers'] == public_roll_payload(roll)['modifiers'] == [{'label': 'Wounded', 'value': -2}]


def test_the_attack_sentence_names_a_newly_applying_modifier():
    attack = {'sourceActorName': 'Liora', 'weaponName': 'Quarterstaff', 'targetName': 'Ashmarked Cutthroat', 'attackTotal': 9, 'targetArmorClass': 13, 'hit': False}

    told = _narrated(combatState={'playerResolvedAction': attack}, attackModifiers=[{'label': 'Wounded', 'value': -2}])
    quiet = _narrated(combatState={'playerResolvedAction': attack})

    assert told.endswith(' — your wounds cost you 2.')
    assert told.removesuffix(' — your wounds cost you 2.') + '.' == quiet
    assert 'wounds' not in quiet


# --- 4. Bosses stand their ground ----------------------------------------------------------------------------------


@pytest.mark.parametrize('intent', ['negotiate', 'surrender', 'yield', 'retreat', 'flee'])
def test_a_boss_never_gives_ground_above_a_quarter_of_its_hp(intent):
    boss = _enemy(challengeTier='boss', hp=20)
    ally = _enemy('enemy_2', hp=0, isAlive=False)
    combat = _state(_p(ACTOR, 'player'), boss, ally)['combat']
    combat.update(round=6, flags={**combat['flags'], 'allowParley': True})

    assert intent_is_restricted({'intentType': intent}, {**boss, 'hp': {'current': 6, 'max': 20}}, combat) is True
    assert intent_is_restricted({'intentType': intent}, {**boss, 'hp': {'current': 5, 'max': 20}}, combat) is False
    assert intent_is_restricted({'intentType': intent}, {**boss, 'challengeTier': 'hard', 'hp': {'current': 6, 'max': 20}}, combat) is False


# --- 5. Endings name what you chose --------------------------------------------------------------------------------

FINALE = {
    'id': 'cp_end',
    'title': 'The End',
    'playerTitle': 'After the Road',
    'playerSummary': 'See what changed.',
    'terminal': True,
    'endingVariants': [
        {'when': {'flagKey': 'ward_renewed', 'equals': True}, 'title': 'The Road Remembers', 'text': 'The lanterns burn steady.'},
        {'when': {'flagKey': 'ward_broken', 'equals': True}, 'title': 'Only a Road', 'text': 'No ward keeps the roads.'},
    ],
}
REACHED = [{'id': 'cp_end', 'status': 'reached', 'terminal': True, 'title': 'After the Road', 'summary': 'See what changed.'}]


def test_the_first_matching_variant_names_and_tells_the_ending():
    assert player_ending(REACHED, [FINALE], {'ward_renewed': True, 'ward_broken': True}) == {
        'checkpointId': 'cp_end', 'title': 'The Road Remembers', 'summary': 'See what changed.', 'text': 'The lanterns burn steady.',
    }
    assert player_ending(REACHED, [FINALE], {'ward_renewed': False}) == {
        'checkpointId': 'cp_end', 'title': 'After the Road', 'summary': 'See what changed.', 'text': 'See what changed.',
    }


@pytest.mark.parametrize(
    ('variants', 'terminal', 'severity', 'code', 'path'),
    [
        ([{'when': {'flagKey': 'ward_renewed'}, 'title': 'T', 'text': 'X'}], True, 'error', 'invalid_ending_variant', 'checkpoints[0].endingVariants[0]'),
        ([{'when': {'flagKey': 'never_set', 'equals': True}, 'title': 'T', 'text': 'X'}], True, 'warning', 'ending_variant_flag_never_set', 'checkpoints[0].endingVariants[0].when.flagKey'),
        ([{'when': {'flagKey': 'ward_renewed', 'equals': True}, 'title': 'T', 'text': 'X'}], False, 'warning', 'ending_variants_not_terminal', 'checkpoints[0].endingVariants'),
    ],
)
def test_the_linter_checks_ending_variants(app, variants, terminal, severity, code, path):
    pack = _engine_clean_pack()
    pack['startingState']['flags'] = {'ward_renewed': False}
    pack['checkpoints'][0].update(endingVariants=variants, terminal=terminal)

    with app.app_context():
        issues = lint_campaign_pack_manifest(pack, workspace_id='owner')['issues']

    assert (severity, code, path) in {(issue['severity'], issue['code'], issue['path']) for issue in issues}


# --- 6 / 7. One fate, choices shown together -----------------------------------------------------------------------

STONE = {
    'id': 'stone',
    'name': 'The Last Road-Stone',
    'kind': 'object',
    'revision': 0,
    'usable': True,
    'triggerable': True,
    'allowedActions': ['inspect', 'use', 'trigger'],
    'choiceActions': ['use', 'trigger'],
    'requirements': {'use': {'flags': {'quest_done': False}}, 'trigger': {'flags': {'quest_done': False}}},
}


def _scene_state(flags: dict, *objects: dict) -> dict:
    scene = {'locationId': 'valley', 'name': 'Valley', 'interactables': [deepcopy(entry) for entry in objects]}
    return {
        'currentScene': scene,
        'locations': [{'id': 'valley', 'name': 'Valley', 'interactables': [deepcopy(entry) for entry in objects]}],
        'flags': flags,
        'playerCharacters': [{'id': ACTOR, 'playerId': 1, 'name': 'Arden', 'inventory': {'items': []}}],
    }


def _offers(state: dict, object_id: str) -> dict:
    return {entry['action']: entry for entry in offered_scene_actions(state, state['playerCharacters'][0])[object_id]}


def test_a_false_flag_requirement_holds_until_the_flag_is_set():
    unset = _offers(_scene_state({}, STONE), 'stone')
    chosen = _offers(_scene_state({'quest_done': True}, STONE), 'stone')

    assert (unset['use']['legal'], unset['trigger']['legal']) == (True, True)
    assert {action: (chosen[action]['legal'], chosen[action]['reason']) for action in ('use', 'trigger')} == {
        'use': (False, 'That can no longer be done.'),
        'trigger': (False, 'That can no longer be done.'),
    }


def test_listed_choice_actions_are_marked_as_choices():
    offers = _offers(_scene_state({}, STONE), 'stone')

    assert {action: entry.get('choice') for action, entry in offers.items()} == {'use': True, 'trigger': True, 'inspect': None}


def test_the_linter_errors_on_a_choice_the_object_does_not_support(app):
    pack = _engine_clean_pack()
    pack['locations'][0]['interactables'] = [{**STONE, 'choiceActions': ['use', 'break']}]

    with app.app_context():
        issues = lint_campaign_pack_manifest(pack, workspace_id='owner')['issues']

    assert ('error', 'choice_action_unsupported', 'locations[0].interactables[0].choiceActions[1]') in {
        (issue['severity'], issue['code'], issue['path']) for issue in issues
    }


# --- 8. Wording ----------------------------------------------------------------------------------------------------


CHEST = {'id': 'chest', 'name': 'Iron Chest', 'kind': 'container', 'revision': 0, 'breakable': True, 'searchable': True, 'open': True, 'locked': False}


def test_breaking_open_a_container_that_stands_open_is_done():
    offers = _offers(_scene_state({}, CHEST), 'chest')

    assert offers['break']['done'] is True
    assert _offers(_scene_state({}, {**CHEST, 'open': False}), 'chest')['break']['done'] is False


def test_an_object_named_the_is_written_the_inside_a_sentence():
    state = _scene_state({}, {**STONE, 'inspected': True, 'choiceActions': []})
    actions = available_actions_for_target(state, location_id='valley', target_id='stone', actor=interactable_actor_context(state, state['playerCharacters'][0]))['actions']
    tool = {**STONE, 'breakable': True, 'allowedActions': ['break'], 'requirements': {'break': {'anyToolIds': ['hammer']}}}
    refused = _offers(_scene_state({}, tool), 'stone')

    assert next(entry for entry in actions if entry['action'] == 'inspect')['reason'] == 'You have already examined the Last Road-Stone.'
    assert refused['break']['reason'] == 'You have nothing that will break the Last Road-Stone.'


def test_a_proper_enemy_is_someone_not_something():
    reply = _narrated(
        combatState={
            'enemyResolvedActions': [
                {'enemyName': 'The Cinder Scribe', 'intentType': 'complete_objective', 'resolvedWithoutRoll': True},
                {'enemyName': 'The Cinder Scribe', 'intentType': 'surrender', 'resolvedWithoutRoll': True},
                {'enemyName': 'Ashmarked Cutthroat', 'intentType': 'yield', 'resolvedWithoutRoll': True},
            ]
        }
    )

    assert reply == (
        'The Cinder Scribe turns back to their work. The Cinder Scribe throws down their weapon and yields. '
        'The ashmarked cutthroat throws down its weapon and yields.'
    )


def test_closing_in_is_told_in_the_heros_voice_before_the_attack():
    close = {'actionType': 'advance', 'actorName': 'Liora', 'targetName': 'Ashmarked Cutthroat', 'movement': {'toRangeBand': 'melee', 'engagedWith': ['enemy_1']},
             'outcome': 'Liora closes into melee and engages Ashmarked Cutthroat.'}
    attack = {'sourceActorName': 'Liora', 'weaponName': 'Quarterstaff', 'targetName': 'Ashmarked Cutthroat', 'attackTotal': 15, 'targetArmorClass': 13, 'hit': True, 'damageTotal': 4, 'damageType': 'bludgeoning'}

    reply = _narrated(combatState={'playerResolvedCombatAction': close, 'playerResolvedAction': attack})

    assert reply.startswith('You close in on the ashmarked cutthroat. Your Quarterstaff ')
    assert 'closes into melee' not in reply


def test_a_search_that_turns_up_a_clue_or_a_flag_does_not_say_it_found_nothing():
    wall = {'id': 'wall', 'name': 'Memorial Wall', 'kind': 'object', 'searchable': True, 'revision': 0, 'discoversClueIds': {'search': ['clue_names']}}
    plain = {**wall, 'id': 'plain', 'discoversClueIds': {}}
    state = _scene_state({}, wall, plain)
    facts = {
        fact['targetId']: fact
        for fact in object_facts(
            state_before=state,
            state_after=state,
            applied_changes=[
                {'type': 'scene.interactable.action', 'targetId': target, 'objectAction': 'search', 'event': {'type': 'interactable.searched', 'action': 'search', 'targetId': target}}
                for target in ('wall', 'plain')
            ],
            actor_id=ACTOR,
        )
    }

    def told(fact: dict) -> str:
        return narrate(
            {'live_world_state': {'currentScene': {'name': 'Waystation'}}},
            {'turn_number': 4, 'state_pipeline': {'stateChangesAlreadyApplied': [{'type': 'scene.interactable.action', 'targetId': fact['targetId'], 'objectAction': 'search'}], 'narration': {'objects': [fact]}}},
            'search',
            speaker='Arden',
        )

    assert facts['wall']['discovered'] is True and 'discovered' not in facts['plain']
    assert 'You find nothing more.' not in told(facts['wall'])
    assert 'You find nothing more.' in told(facts['plain'])
