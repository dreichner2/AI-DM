"""Story rolls: every roll of a turn on the table, as the roll cards show them.

Socket-level tests play the Play Now adventure in Safe Mode with seeded dice (Arden Vale and Seren
Ashgrove, through the real pipeline); unit tests pin how the engine's results become records.
"""

from __future__ import annotations

import pytest

from aidm_server.api_type_contract import API_TYPE_CONTRACT_BY_NAME
from aidm_server.database import db
from aidm_server.game_state.orchestration.story_rolls import (
    attack_damage,
    numbered,
    story_rolls_after_narration,
    story_rolls_before_narration,
)
from aidm_server.models import DmTurn, SessionLogEntry, TurnEvent, safe_json_loads
from aidm_server.turn_events import DM_RESPONSE_EVENT
from tests.test_party_play import PartyTable, party, safe_mode  # noqa: F401 - the Play Now party table fixtures

ROLL_FIELDS = {field.name for field in API_TYPE_CONTRACT_BY_NAME['StoryRoll'].fields}
REQUIRED_FIELDS = {field.name for field in API_TYPE_CONTRACT_BY_NAME['StoryRoll'].fields if not field.optional}


def _events(received: list[dict], name: str) -> list[dict]:
    return [event['args'][0] for event in received if event['name'] == name and event['args']]


def _completed(received: list[dict]) -> dict:
    return next(
        payload['details']
        for payload in _events(received, 'turn_status')
        if payload['status'] == 'saved' and payload['details'].get('stage') == 'post_turn'
    )


def _turn(table: PartyTable, turn_id: int) -> dict:
    with table.app.app_context():
        return safe_json_loads(db.session.get(DmTurn, turn_id).metadata_json, {})


def _rolls_of(table: PartyTable, turn_id: int) -> list[dict]:
    return _turn(table, turn_id)['storyRolls']


def _without_ids(rolls: list[dict]) -> list[dict]:
    return [{key: value for key, value in roll.items() if key != 'id'} for roll in rolls]


def _adds_up(roll: dict) -> None:
    """The card's numbers are the engine's: the kept face and every part give the total."""

    assert roll['total'] == roll['d20']['kept'] + sum(part['value'] for part in roll['parts']), roll


def test_a_party_fight_puts_every_roll_on_the_table_before_the_narration(party):  # noqa: F811
    start = party.last_turn_id()
    opening = party.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    opening_turn = start + 1
    names = [event['name'] for event in opening]

    # The rolls arrive before the narrator writes a word, and again complete when the turn is saved.
    assert names.index('turn_rolls') < names.index('dm_chunk')
    sent = _events(opening, 'turn_rolls')[0]
    assert (sent['session_id'], sent['turn_id']) == (party.ids['session_id'], opening_turn)
    assert _completed(opening)['storyRolls'] == _rolls_of(party, opening_turn) == sent['rolls']

    initiative, lure, pounce, pounce_save = _rolls_of(party, opening_turn)
    combat = party.snapshot()['combat']
    order = sorted(combat['initiative'], key=lambda entry: entry['order'])
    assert initiative == {
        'id': f't{opening_turn}-1',
        'kind': 'initiative',
        'label': 'Initiative',
        'entries': [
            {
                'name': entry['name'],
                'side': 'enemy' if entry['participantId'].startswith('enemy') else 'hero',
                'faces': [entry['roll']],
                'bonus': entry['modifier'],
                'total': entry['total'],
            }
            for entry in order
        ],
    }
    assert [entry['name'] for entry in initiative['entries']] == ['Wardbound Beast', 'Pale Lantern', 'Arden Vale', 'Seren Ashgrove']

    resolved = _turn(party, opening_turn)['state_pipeline']['dmContextPacket']['combatState']['enemyResolvedActions']
    lantern, beast = resolved
    # A forced save: the hero who rolls it, the ability that forced it.
    assert lure == {
        'id': f't{opening_turn}-2',
        'kind': 'save',
        'actor': {'id': party.hero_id('Arden Vale'), 'name': 'Arden Vale', 'side': 'hero'},
        'label': 'Wisdom save',
        'source': 'Lantern-Lure',
        'd20': {'faces': lantern['saveRolls'], 'kept': lantern['saveRoll'], 'mode': 'normal'},
        'parts': [],
        'total': lantern['saveTotal'],
        'against': {'kind': 'DC', 'value': lantern['saveDC']},
        'outcome': 'success',
        'natural': None,
    }
    # The enemy's attack with its damage dice; party scaling took the 8 + 3 down to the 7 Seren lost.
    assert pounce['kind'] == 'attack' and pounce['label'] == 'Dust-Marked Pounce'
    assert pounce['actor'] == {'id': beast['enemyId'], 'name': 'Wardbound Beast', 'side': 'enemy'}
    assert pounce['target'] == {'id': party.hero_id('Seren Ashgrove'), 'name': 'Seren Ashgrove'}
    assert (pounce['total'], pounce['against'], pounce['outcome']) == (beast['attackTotal'], {'kind': 'AC', 'value': beast['targetArmorClass']}, 'hit')
    assert pounce['parts'] == [{'label': 'attack bonus', 'value': beast['attackBonus']}]
    _adds_up(pounce)
    assert pounce['damage'] == {
        'dice': '1d8+3',
        'faces': beast['damageRolls'],
        'bonus': 3,
        'total': beast['damageTotal'],
        'type': 'piercing',
        'critical': False,
        'note': 'scaled for the party',
    }
    lost = party.hero('Seren Ashgrove')['health']['maxHp'] - party.hero('Seren Ashgrove')['health']['currentHp']
    assert pounce['damage']['total'] == lost == 7
    assert (pounce_save['label'], pounce_save['source'], pounce_save['actor']['name']) == ('Strength save', 'Dust-Marked Pounce', 'Seren Ashgrove')
    _adds_up(pounce_save)

    # Arden's swing: the d20 he pressed, his modifiers, and the damage the lantern took. The lantern starts out of
    # reach; set it a move away so the longsword can find it.
    party.operator([{'type': 'combat.move', 'participantId': lantern['enemyId'], 'toRangeBand': 'near'}])
    swing_turn = party.last_turn_id() + 1
    attack = next(action for action in party.actions('Arden Vale') if action['id'].startswith('combat.attack.'))
    lantern_hp = next(record for record in party.snapshot()['combat']['participants'] if record['name'] == 'Pale Lantern')['hp']['current']
    swing = party.hud('Arden Vale', 'Arden Vale attacks.', attack['id'], lantern['enemyId'])
    (hero_attack,) = _completed(swing)['storyRolls']
    roll = _turn(party, swing_turn)['authoritative_roll']
    assert hero_attack['actor']['name'] == 'Arden Vale' and hero_attack['target']['name'] == 'Pale Lantern'
    assert hero_attack['label'] == 'Longsword'
    assert hero_attack['d20'] == {'faces': [15], 'kept': 15, 'mode': 'normal'}
    assert hero_attack['parts'] == [{'label': 'Strength', 'value': 2}, {'label': 'proficiency', 'value': 2}]
    assert (hero_attack['total'], hero_attack['against']['value']) == (roll['total'], roll['target_ac'])
    _adds_up(hero_attack)
    damage = hero_attack['damage']
    assert damage['faces'] and damage['total'] == sum(damage['faces']) + damage['bonus']
    after = next(record for record in party.snapshot()['combat']['participants'] if record['name'] == 'Pale Lantern')['hp']['current']
    assert damage['total'] == lantern_hp - after

    for record in [*_rolls_of(party, opening_turn), hero_attack]:
        assert REQUIRED_FIELDS <= set(record) <= ROLL_FIELDS


def test_a_hero_down_rolls_death_saves_on_the_table(party):  # noqa: F811
    party.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    start = party.last_turn_id()
    party.fight()

    saves = [
        (turn_id, roll)
        for turn_id in range(start + 1, party.last_turn_id() + 1)
        for roll in _turn(party, turn_id).get('storyRolls') or []
        if roll['kind'] == 'death_save'
    ]
    assert saves
    turn_id, save = saves[0]
    change = next(
        change
        for change in _turn(party, turn_id)['state_pipeline']['postAppliedChanges']
        if change['type'] == 'combat.death_save'
    )
    assert save['actor'] == {'id': party.hero_id('Seren Ashgrove'), 'name': 'Seren Ashgrove', 'side': 'hero'}
    assert save['label'] == 'Death save'
    assert save['d20'] == {'faces': [change['roll']], 'kept': change['roll'], 'mode': 'normal'}
    assert (save['total'], save['against'], save['outcome']) == (change['roll'], {'kind': 'DC', 'value': 10}, 'success')
    # Known before narration, so it is not told twice when the turn is saved.
    assert [roll['kind'] for roll in _rolls_of(party, turn_id)].count('death_save') == 1


def test_a_healing_spell_is_a_card_with_its_dice(party):  # noqa: F811
    party.operator([{'type': 'health.damage', 'actorId': party.hero_id('Arden Vale'), 'amount': 6}])
    turn_id = party.last_turn_id() + 1
    received = party.cast('Seren Ashgrove', 'Healing Word', 'Arden Vale')

    (healing,) = _completed(received)['storyRolls']
    cast = next(change for change in _turn(party, turn_id)['state_pipeline']['immediateAppliedChanges'] if change['type'] == 'spell.cast')
    effect = cast['spellResolution']['targets'][0]['effects'][0]
    assert healing['kind'] == 'healing' and healing['label'] == 'Healing Word'
    assert healing['actor'] == {'id': party.hero_id('Seren Ashgrove'), 'name': 'Seren Ashgrove', 'side': 'hero'}
    assert healing['target'] == {'id': party.hero_id('Arden Vale'), 'name': 'Arden Vale'}
    assert healing['healing'] == {
        'dice': f"{effect['roll']['dice']}{effect['roll']['bonus']:+d}",
        'faces': effect['roll']['rolls'],
        'bonus': effect['roll']['bonus'],
        'total': effect['roll']['total'],
    }
    assert healing['healing']['total'] == sum(healing['healing']['faces']) + healing['healing']['bonus']


def test_an_object_check_outside_a_fight_and_a_pressed_check_are_cards(party):  # noqa: F811
    # Object checks roll 15 in Safe Mode's seeded dice.
    bench = party.send('Arden Vale', "Search under the driver's bench.")
    (check,) = _completed(bench)['storyRolls']
    assert check['kind'] == 'check' and check['label'] == 'Investigation check'
    assert check['actor']['name'] == 'Arden Vale'
    assert check['target'] == {'id': 'inn_drivers_bench', 'name': "Driver's Bench"}
    assert check['d20'] == {'faces': [15], 'kept': 15, 'mode': 'normal'}
    assert (check['against'], check['outcome']) == ({'kind': 'DC', 'value': 10}, 'success')
    _adds_up(check)

    # A check the narrator asks for, rolled from the prompt: the player's d20 lands on 15.
    party.send('Arden Vale', 'I sneak around behind the last wagon.')
    pressed_turn = party.last_turn_id() + 1
    pressed = party.send(
        'Arden Vale',
        'I roll.',
        {'kind': 'roll', 'source': 'story_roll_prompt', 'roll': {'die': 'd20', 'mode': 'normal', 'result_visibility': 'hidden_until_landed'}},
    )
    (rolled,) = _completed(pressed)['storyRolls']
    roll = _turn(party, pressed_turn)['authoritative_roll']
    assert rolled['kind'] == 'check' and rolled['label'] == 'Stealth check'
    assert rolled['d20'] == {'faces': roll['rolls'], 'kept': roll['kept'], 'mode': 'normal'}
    assert (rolled['total'], rolled['against'], rolled['outcome']) == (roll['total'], {'kind': 'DC', 'value': roll['dc']}, roll['outcome'])
    _adds_up(rolled)


def test_every_player_sees_the_rolls_on_the_log_entry_and_turn_event(party):  # noqa: F811
    party.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    turn_id = party.last_turn_id()
    rolls = _rolls_of(party, turn_id)

    # Seren's player reads Arden's turn: nothing in the rolls is withheld.
    seren = party.players['Seren Ashgrove']
    entries = party.client.get(f"/api/sessions/{party.ids['session_id']}/log?view=player&player_id={seren}").get_json()['entries']
    logged = [entry['metadata']['storyRolls'] for entry in entries if entry['metadata'].get('turn_id') == turn_id and 'storyRolls' in entry['metadata']]
    assert logged == [rolls]
    events = party.client.get(f"/api/sessions/{party.ids['session_id']}/events?view=player&player_id={seren}").get_json()['events']
    assert [event['payload']['metadata']['storyRolls'] for event in events if event['event_type'] == DM_RESPONSE_EVENT and event['turn_id'] == turn_id] == [rolls]
    with party.app.app_context():
        narration = [
            safe_json_loads(entry.metadata_json, {})
            for entry in SessionLogEntry.query.filter_by(session_id=party.ids['session_id'], entry_type='dm')
            if entry.message.startswith('DM: ')
        ]
        assert [metadata['storyRolls'] for metadata in narration if metadata.get('turn_id') == turn_id] == [rolls]
        event = TurnEvent.query.filter_by(turn_id=turn_id, event_type=DM_RESPONSE_EVENT).one()
        assert safe_json_loads(event.payload_json, {})['metadata']['storyRolls'] == rolls


def test_the_contract_declares_the_rolls():
    assert API_TYPE_CONTRACT_BY_NAME['SessionLogEntry'].fields[3].ts_type == 'SessionLogEntryMetadata'
    assert 'storyRolls?: StoryRoll[]' in (API_TYPE_CONTRACT_BY_NAME['SessionLogEntryMetadata'].alias or '')
    assert {field.name for field in API_TYPE_CONTRACT_BY_NAME['TurnRollsPayload'].fields} == {'session_id', 'turn_id', 'rolls'}


HERO = {'id': 'player_1', 'name': 'Arden Vale', 'playerId': 1}
STATE = {
    'playerCharacters': [HERO, {'id': 'player_2', 'name': 'Seren Ashgrove'}],
    'combat': {
        'participants': [
            {'id': 'player_1', 'name': 'Arden Vale', 'team': 'player'},
            {'id': 'player_2', 'name': 'Seren Ashgrove', 'team': 'player'},
            {'id': 'enemy_cutthroat_1', 'name': 'Ashmarked Cutthroat', 'team': 'enemy'},
            {'id': 'ally_radan', 'name': 'Radan Greycloak', 'team': 'ally'},
        ],
    },
}


def _before(**fields) -> list[dict]:
    return story_rolls_before_narration(
        actor_id='player_1',
        state_before=STATE,
        state_before_dm=STATE,
        authoritative_roll=fields.pop('authoritative_roll', None),
        applied_changes=fields.pop('applied_changes', []),
        dm_context=fields.pop('dm_context', {}),
    )


def test_advantage_keeps_both_faces_and_bless_is_a_dice_part():
    action = {
        'enemyId': 'enemy_cutthroat_1', 'enemyName': 'Ashmarked Cutthroat', 'targetId': 'player_1', 'targetName': 'Arden Vale',
        'abilityName': 'Hooked Knife', 'attackMode': 'advantage', 'attackRolls': [6, 17], 'attackRoll': 17, 'attackBonus': 4,
        'attackBonusDice': [{'dice': '+1d4', 'rolls': [3], 'total': 3}], 'attackTotal': 24, 'targetArmorClass': 16, 'hit': True,
        'critical': False, 'damageDice': '1d4+2', 'damageRolls': [4], 'damageBonus': 2, 'damageTotal': 3, 'damageType': 'slashing',
        'reactions': [{'reactorId': 'player_1', 'damageMultiplier': 0.5}],
    }
    (roll,) = _before(dm_context={'combatState': {'enemyResolvedActions': [action]}})

    assert roll['d20'] == {'faces': [6, 17], 'kept': 17, 'mode': 'advantage'}
    assert roll['parts'] == [{'label': 'attack bonus', 'value': 4}, {'label': 'bonus die', 'dice': '1d4', 'faces': [3], 'value': 3}]
    _adds_up(roll)
    assert roll['damage']['note'] == 'resisted'


def test_a_critical_doubles_the_dice_and_extra_damage_is_a_part():
    damage = attack_damage({
        'hit': True, 'critical': True, 'damageTotal': 21, 'damageType': 'piercing',
        'damageComponents': [
            {'source': 'Rapier', 'dice': '2d8', 'rolls': [5, 6], 'bonus': 3, 'type': 'piercing', 'multiplier': 1.0, 'total': 14},
            {'source': 'Sneak Attack', 'dice': '2d6', 'rolls': [3, 4], 'bonus': 0, 'type': 'piercing', 'multiplier': 1.0, 'total': 7},
        ],
    })

    assert damage == {
        'dice': '2d8+3', 'faces': [5, 6], 'bonus': 3, 'total': 21, 'type': 'piercing', 'critical': True,
        'note': 'doubled dice (critical)', 'parts': [{'label': 'Sneak Attack', 'dice': '2d6', 'faces': [3, 4], 'value': 7}],
    }
    assert attack_damage({'hit': False, 'damageTotal': 0}) is None


def test_a_save_spell_halves_its_damage_on_a_success_and_its_saver_is_the_actor():
    cast = {
        'type': 'spell.cast', 'actorId': 'player_2',
        'spellResolution': {
            'casterId': 'player_2', 'spellName': 'Sacred Flame',
            'targets': [{
                'targetId': 'enemy_cutthroat_1', 'targetName': 'Ashmarked Cutthroat',
                'delivery': {'type': 'save', 'ability': 'dexterity', 'rolls': [14], 'naturalRoll': 14, 'mode': 'normal', 'modifier': 2,
                             'bonusDice': [], 'total': 16, 'dc': 13, 'saveSucceeded': True},
                'effects': [{'kind': 'damage', 'damageType': 'radiant', 'roll': {'dice': '1d8', 'rolls': [7], 'bonus': 0, 'total': 7},
                             'rolledAmount': 7, 'afterSaveAmount': 3, 'postDefenseAmount': 3, 'amountApplied': 3, 'defense': None}],
            }],
        },
    }
    (save,) = _before(applied_changes=[cast])

    assert save['actor'] == {'id': 'enemy_cutthroat_1', 'name': 'Ashmarked Cutthroat', 'side': 'enemy'}
    assert (save['label'], save['source'], save['outcome']) == ('Dexterity save', 'Sacred Flame', 'success')
    assert save['parts'] == [{'label': 'bonus', 'value': 2}]
    assert save['damage'] == {'dice': '1d8', 'faces': [7], 'bonus': 0, 'total': 3, 'type': 'radiant', 'critical': False, 'note': 'halved on a success'}


def test_second_wind_hit_dice_and_a_potion_are_healing_cards():
    changes = [
        {'type': 'class_feature.use', 'actorId': 'player_1', 'capabilityId': 'second_wind', 'capabilityName': 'Second Wind',
         'resolution': {'effectType': 'heal', 'amount': 7, 'rolls': [6], 'targetId': 'player_1'}},
        {'type': 'rest.complete', 'actorId': 'player_1', 'hitDiceResolution': {'die': 'd10', 'rolls': [4, 9], 'healing': 15, 'spent': 2}},
        {'type': 'item.use', 'actorId': 'player_1',
         'resolution': {'itemName': 'Potion of Healing', 'targetId': 'player_2', 'targetName': 'Seren Ashgrove',
                        'healing': {'dice': '2d4+2', 'rolls': [3, 1], 'bonus': 2, 'total': 6}}},
    ]
    wind, rest, potion = _before(applied_changes=changes)

    assert (wind['label'], wind['healing']) == ('Second Wind', {'dice': '1d10+1', 'faces': [6], 'bonus': 1, 'total': 7})
    assert (rest['label'], rest['healing']) == ('Hit dice', {'dice': '2d10+2', 'faces': [4, 9], 'bonus': 2, 'total': 15})
    assert potion['target'] == {'id': 'player_2', 'name': 'Seren Ashgrove'}
    assert potion['healing'] == {'dice': '2d4+2', 'faces': [3, 1], 'bonus': 2, 'total': 6}


def test_a_maneuver_check_and_a_player_save_from_the_prompt():
    shove = {'actorId': 'player_1', 'actorName': 'Arden Vale', 'targetId': 'enemy_cutthroat_1', 'targetName': 'Ashmarked Cutthroat',
             'check': {'skill': 'athletics', 'ability': 'strength', 'roll': 20, 'rolls': [20], 'mode': 'normal', 'modifier': 4,
                       'bonusDice': [], 'total': 24, 'dc': 12, 'success': True}}
    save = {'rule_type': 'constitution_saving_throw', 'die': 'd20', 'mode': 'disadvantage', 'rolls': [1, 11], 'kept': 1, 'modifier': 1,
            'bonus_dice': [], 'total': 2, 'dc': 13, 'outcome': 'failure', 'reason': 'Poisoned dart',
            'ability': {'key': 'constitution'}, 'modifier_breakdown': {'ability_modifier': 1, 'proficiency_bonus': 0}}
    pressed, maneuver = _before(authoritative_roll=save, dm_context={'combatState': {'playerResolvedCombatAction': shove}})

    assert (pressed['kind'], pressed['label'], pressed['source']) == ('save', 'Constitution save', 'Poisoned dart')
    assert pressed['d20'] == {'faces': [1, 11], 'kept': 1, 'mode': 'disadvantage'}
    assert (pressed['natural'], pressed['outcome'], pressed['parts']) == ('one', 'failure', [{'label': 'Constitution', 'value': 1}])
    assert (maneuver['label'], maneuver['natural'], maneuver['target']['name']) == ('Athletics check', 'twenty', 'Ashmarked Cutthroat')
    _adds_up(maneuver)


def test_rolls_made_after_narration_are_appended_without_repeating_known_death_saves():
    pipeline = {
        'dmContextPacket': {'combatState': {'deathSaves': [{'participantId': 'player_2', 'round': 2, 'roll': 8}]}},
        'postDmExtraction': {'engineResolvedActions': [{
            'enemyId': 'enemy_cutthroat_1', 'enemyName': 'Ashmarked Cutthroat', 'targetId': 'player_1', 'targetName': 'Arden Vale',
            'abilityName': 'Hooked Knife', 'attackRolls': [2], 'attackRoll': 2, 'attackBonus': 4, 'attackTotal': 6,
            'targetArmorClass': 16, 'hit': False,
        }]},
        'postAppliedChanges': [
            {'type': 'combat.death_save', 'id': 'chg_unknown', 'participantId': 'player_2', 'participantName': 'Seren Ashgrove', 'roll': 3, 'result': 'failure'},
            {'type': 'combat.update', 'turnBoundaryEvents': [{'actorId': 'player_1', 'conditionSaves': [{'sourceName': 'Hooked Knife', 'save': {
                'saveAbility': 'con', 'saveDC': 12, 'saveRoll': 13, 'saveRolls': [13], 'saveMode': 'normal', 'saveModifier': 2,
                'saveBonusDice': [], 'saveTotal': 15, 'saveSucceeded': True}}]}]},
        ],
    }
    miss, death, shaken = story_rolls_after_narration(turn_id=9, pipeline=pipeline, state=STATE)

    assert (miss['kind'], miss['outcome']) == ('attack', 'miss') and 'damage' not in miss
    assert (death['kind'], death['total'], death['outcome']) == ('death_save', 3, 'failure')
    assert (shaken['label'], shaken['source'], shaken['actor']['name']) == ('Constitution save', 'Hooked Knife', 'Arden Vale')
    assert [roll['id'] for roll in numbered([miss, death], turn_id=9, start=4)] == ['t9-4', 't9-5']


@pytest.mark.parametrize('free_roll, label', [({'die': 'd6', 'rolls': [4], 'kept': 4, 'total': 4}, 'd6'), ({'die': 'd20', 'rolls': [20], 'kept': 20, 'total': 23, 'ability': {'key': 'strength'}, 'modifier_breakdown': {'ability_modifier': 3}}, 'Strength')])
def test_a_roll_of_their_own_has_no_target_number(free_roll, label):
    (roll,) = _before(authoritative_roll={'rule_type': 'check', 'mode': 'normal', 'free': True, 'dc': None, 'outcome': None, **free_roll})

    assert (roll['label'], roll['against'], roll['outcome']) == (label, None, None)
    assert roll.get('die', 'd20') == free_roll['die']
    assert roll['natural'] == ('twenty' if free_roll['die'] == 'd20' else None)
    _adds_up(roll)


def test_a_flask_thrown_a_spell_attack_and_magic_missile_darts_are_cards():
    changes = [
        {'type': 'item.use', 'actorId': 'player_1', 'resolution': {
            'itemName': 'Alchemist’s Fire', 'targetId': 'enemy_cutthroat_1', 'targetName': 'Ashmarked Cutthroat',
            'attack': {'mode': 'normal', 'd20': [20], 'natural': 20, 'bonus': 4, 'bonusDice': [], 'total': 24, 'targetArmorClass': 13, 'hit': True, 'critical': True},
            'damage': {'dice': '1d4', 'rolls': [3, 2], 'bonus': 0, 'total': 5, 'type': 'fire', 'multiplier': 1.0, 'defense': None},
        }},
        {'type': 'spell.cast', 'actorId': 'player_2', 'spellResolution': {'casterId': 'player_2', 'spellName': 'Guiding Bolt', 'targets': [{
            'targetId': 'enemy_cutthroat_1', 'targetName': 'Ashmarked Cutthroat',
            'delivery': {'type': 'attack', 'delivered': True, 'critical': False, 'mode': 'normal', 'rolls': [12], 'naturalRoll': 12, 'modifier': 5,
                         'bonusDice': [], 'total': 17, 'targetArmorClass': 13},
            'effects': [{'kind': 'damage', 'damageType': 'radiant', 'roll': {'dice': '4d6', 'rolls': [1, 2, 3, 4], 'bonus': 0, 'total': 10},
                         'rolledAmount': 10, 'afterSaveAmount': 10, 'postDefenseAmount': 10, 'amountApplied': 10, 'defense': None}],
        }]}},
        {'type': 'spell.cast', 'actorId': 'player_2', 'spellResolution': {'casterId': 'player_2', 'spellName': 'Magic Missile', 'targets': [
            {'targetId': 'enemy_cutthroat_1', 'targetName': 'Ashmarked Cutthroat', 'dart': index, 'delivery': {'type': 'automatic', 'delivered': True},
             'effects': [{'kind': 'damage', 'damageType': 'force', 'roll': {'dice': '1d4+1', 'rolls': [face], 'bonus': 1, 'total': face + 1},
                          'rolledAmount': face + 1, 'afterSaveAmount': face + 1, 'postDefenseAmount': face + 1, 'amountApplied': face + 1, 'defense': None}]}
            for index, face in ((1, 2), (2, 4))
        ]}},
    ]
    flask, bolt, *darts = _before(applied_changes=changes)

    assert (flask['label'], flask['natural'], flask['outcome']) == ('Alchemist’s Fire', 'twenty', 'hit')
    assert flask['damage'] == {'dice': '1d4', 'faces': [3, 2], 'bonus': 0, 'total': 5, 'type': 'fire', 'critical': True, 'note': 'doubled dice (critical)'}
    assert (bolt['kind'], bolt['label'], bolt['actor']['name'], bolt['against']) == ('attack', 'Guiding Bolt', 'Seren Ashgrove', {'kind': 'AC', 'value': 13})
    assert bolt['parts'] == [{'label': 'attack bonus', 'value': 5}] and bolt['damage']['faces'] == [1, 2, 3, 4]
    assert [(dart['kind'], dart['damage']['dice'], dart['damage']['total']) for dart in darts] == [('damage', '1d4+1', 3), ('damage', '1d4+1', 5)]
    assert 'd20' not in darts[0]


def test_enemy_turns_break_free_heal_and_draw_opportunity_attacks():
    actions = [
        {'enemyId': 'enemy_cutthroat_1', 'enemyName': 'Ashmarked Cutthroat', 'held': True, 'grapplerId': 'player_1', 'grapplerName': 'Arden Vale',
         'intentType': 'break_free', 'breakFree': {'roll': 14, 'modifier': 2, 'total': 16, 'dc': 13, 'success': True}},
        {'enemyId': 'enemy_cutthroat_1', 'enemyName': 'Ashmarked Cutthroat', 'resolutionMode': 'heal', 'abilityName': 'Bind the Cut',
         'targetId': 'enemy_cutthroat_1', 'targetName': 'Ashmarked Cutthroat', 'healingDice': '1d6+1', 'healingRolls': [4], 'healingTotal': 3},
        {'enemyId': 'enemy_cutthroat_1', 'enemyName': 'Ashmarked Cutthroat', 'intentType': 'retreat', 'resolvedWithoutRoll': True,
         'reactions': [{'reactorId': 'player_1', 'kind': 'opportunity_attack', 'attack': {
             'weaponName': 'Longsword', 'attackRolls': [16], 'attackRoll': 16, 'attackMode': 'normal', 'attackBonus': 4, 'attackBonusDice': [],
             'attackTotal': 20, 'targetArmorClass': 13, 'hit': True, 'critical': False, 'damageTotal': 7, 'damageType': 'slashing',
             'damageComponents': [{'source': 'Longsword', 'dice': '1d8', 'rolls': [5], 'bonus': 2, 'type': 'slashing', 'multiplier': 1.0, 'total': 7}],
         }}]},
    ]
    escape, heal, opportunity = _before(dm_context={'combatState': {'enemyResolvedActions': actions}})

    assert (escape['kind'], escape['label'], escape['target']['name'], escape['outcome']) == ('check', 'Break free', 'Arden Vale', 'success')
    _adds_up(escape)
    assert (heal['kind'], heal['actor']['side'], heal['healing']) == ('healing', 'enemy', {'dice': '1d6+1', 'faces': [4], 'bonus': 1, 'total': 5})
    assert (opportunity['actor']['name'], opportunity['target']['name'], opportunity['label']) == ('Arden Vale', 'Ashmarked Cutthroat', 'Longsword')
    assert opportunity['damage']['total'] == 7


def test_turn_undead_and_stunning_strike_saves_and_ongoing_damage_after_narration():
    turned = {'type': 'class_feature.use', 'actorId': 'player_2', 'capabilityId': 'turn_undead', 'capabilityName': 'Turn Undead', 'resolution': {
        'effectType': 'turn_undead', 'turnUndead': [{'targetId': 'enemy_cutthroat_1', 'targetName': 'Ashmarked Cutthroat', 'outcome': 'turned',
                                                     'saveAbility': 'wisdom', 'saveDC': 13, 'saveRoll': 6, 'saveRolls': [6], 'saveMode': 'normal',
                                                     'saveModifier': 0, 'saveBonusDice': [], 'saveTotal': 6, 'saveSucceeded': False}]}}
    stunned = {'type': 'class_feature.use', 'actorId': 'player_1', 'capabilityId': 'stunning_strike', 'capabilityName': 'Stunning Strike',
               'targetId': 'enemy_cutthroat_1', 'resolution': {'effectType': 'attack_rider', 'targetId': 'enemy_cutthroat_1', 'save': {
                   'saveAbility': 'constitution', 'saveDC': 12, 'saveRoll': 15, 'saveRolls': [15], 'saveMode': 'normal', 'saveModifier': 1,
                   'saveBonusDice': [], 'saveTotal': 16, 'saveSucceeded': True}}}
    turn, strike = _before(applied_changes=[turned, stunned])
    assert (turn['label'], turn['source'], turn['actor']['name'], turn['outcome']) == ('Wisdom save', 'Turn Undead', 'Ashmarked Cutthroat', 'failure')
    assert (strike['label'], strike['source'], strike['outcome']) == ('Constitution save', 'Stunning Strike', 'success')

    pipeline = {'postAppliedChanges': [{'type': 'combat.update', 'turnBoundaryEvents': [{'actorId': 'enemy_cutthroat_1', 'phase': 'start', 'ongoingDamage': [
        {'effectId': 'fx_1', 'dice': '1d6', 'rolls': [4], 'damageType': 'fire', 'rolled': 4, 'defense': None, 'damage': 4},
    ]}]}]}
    (burn,) = story_rolls_after_narration(turn_id=9, pipeline=pipeline, state=STATE)
    assert (burn['kind'], burn['label'], burn['target']['name'], burn['damage']['total']) == ('damage', 'Ongoing damage', 'Ashmarked Cutthroat', 4)
