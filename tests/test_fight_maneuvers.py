"""Tackle him, bind him, make him yield: fights that take what players type.

Through the real socket pipeline in Safe Mode, on the Play Now adventure's caravan fight (one Ashmarked
Cutthroat for two heroes): Thalion, a ranger with a longbow, and Corin Ashby, a paladin. Seeded dice:
the heroes' attacks and checks, the enemy's rolls and initiative (Thalion, then Corin, then the cutthroat).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
import requests

from aidm_server.combat.intent_planner import plan_intent_for_enemy
from aidm_server.combat.legal_actions import legal_combat_actions_for_player, maneuver_actions
from aidm_server.combat.maneuvers import grapple_holds
from aidm_server.database import db
from aidm_server.game_state.extraction.free_text_actions import fight_words
from aidm_server.models import DmTurn, Player, Session, safe_json_loads
from aidm_server.narration_hygiene import clean_ai_narration
from aidm_server.scripted_narrator import engine_turn_line
from tests.ai_narrator import ENGINE_BRIEF_RE, ai_narration

RANGER = {
    'character_name': 'Thalion',
    'race': 'Elf',
    'char_class': 'Ranger',
    'level': 1,
    'background': 'folk_hero',
    'stats': {
        'ability_scores': {'strength': 10, 'dexterity': 15, 'constitution': 14, 'intelligence': 8, 'wisdom': 14, 'charisma': 10},
        'point_buy': {'budget': 27},
        'gold': 0,
    },
}
PALADIN = {
    'character_name': 'Corin Ashby',
    'race': 'Human',
    'char_class': 'Paladin',
    'level': 1,
    'background': 'soldier',
    'stats': {
        'ability_scores': {'strength': 15, 'dexterity': 10, 'constitution': 14, 'intelligence': 8, 'wisdom': 10, 'charisma': 14},
        'point_buy': {'budget': 27},
        'gold': 0,
    },
}
INITIATIVE = {'Thalion': 20, 'Corin Ashby': 15}


class FightTable:
    """Two browsers at the Play Now table, the caravan cutthroat's fight under way."""

    def __init__(self, app, client, socketio, monkeypatch):
        self.app = app
        self.client = client
        response = client.post('/api/onboarding/play-now', json={})
        assert response.status_code == 201, response.get_data(as_text=True)
        self.ids = {key: response.get_json()[key] for key in ('session_id', 'campaign_id', 'player_id', 'workspace_id')}
        self.players = {}
        for hero in (RANGER, PALADIN):
            created = client.post(f"/api/players/campaigns/{self.ids['campaign_id']}/players", json=hero)
            assert created.status_code == 201, created.get_json()
            self.players[hero['character_name']] = created.get_json()['player_id']
        assert client.delete(f"/api/players/{self.ids['player_id']}").status_code == 200
        order = {f'player_{player_id}': INITIATIVE[name] for name, player_id in self.players.items()}
        monkeypatch.setattr('aidm_server.combat.state._deterministic_initiative_roll', lambda _seed, actor_id: order.get(actor_id, 5))
        self.sockets = {}
        for name, player_id in self.players.items():
            socket_client = socketio.test_client(app, flask_test_client=app.test_client())
            socket_client.emit('join_session', {'session_id': self.ids['session_id'], 'player_id': player_id, 'workspace_id': self.ids['workspace_id']})
            self.sockets[name] = socket_client
        self.drain()
        self.sent = 0
        self.operator([{'type': 'combat.start', 'campaignPackEncounterId': 'enc_empty_caravan_pressure', 'combat': {}}])

    def drain(self) -> None:
        for socket_client in self.sockets.values():
            socket_client.get_received()

    def send(self, hero: str, message: str, intent: dict | None = None) -> list[dict]:
        self.sent += 1
        message_id = f'fight-{self.sent}'
        self.sockets[hero].emit(
            'send_message',
            {
                'session_id': self.ids['session_id'],
                'campaign_id': self.ids['campaign_id'],
                'player_id': self.players[hero],
                'message': message,
                'client_message_id': message_id,
                **({'action_intent': {**intent, 'text': message, 'client_message_id': message_id}} if intent else {}),
            },
        )
        received = self.sockets[hero].get_received()
        self.drain()
        return received

    def type(self, hero: str, message: str) -> list[dict]:
        return self.send(hero, message)

    def hud(self, hero: str, action_id: str, target_id: str | None = None) -> list[dict]:
        combat = {'action_id': action_id, **({'target_id': target_id} if target_id else {})}
        return self.send(hero, f'{hero} acts.', {'kind': 'combat', 'source': 'combat_hud', 'combat': combat})

    def end_turn(self, hero: str) -> list[dict]:
        return self.hud(hero, 'combat.end_turn')

    def operator(self, changes: list[dict]) -> None:
        response = self.client.post(f"/api/sessions/{self.ids['session_id']}/combat/apply-state-changes", json={'changes': changes})
        assert response.status_code == 200, response.get_json()
        assert response.get_json()['validation']['rejected'] == [], response.get_json()['validation']['rejected']

    def snapshot(self) -> dict:
        with self.app.app_context():
            return safe_json_loads(db.session.get(Session, self.ids['session_id']).state_snapshot, {})

    def combat(self) -> dict:
        return self.snapshot().get('combat') or {}

    def enemy(self) -> dict:
        return next(participant for participant in self.combat()['participants'] if participant['team'] == 'enemy')

    def participant(self, hero: str) -> dict:
        return next(participant for participant in self.combat()['participants'] if participant['name'] == hero)

    def hero_id(self, hero: str) -> str:
        return f'player_{self.players[hero]}'

    def acting_hero(self) -> str | None:
        active = (self.combat().get('flags') or {}).get('activeActorId')
        return next((name for name in self.players if self.hero_id(name) == active), None)

    def actions(self, hero: str) -> dict[str, dict]:
        with self.app.app_context():
            bundle = legal_combat_actions_for_player(self.snapshot(), db.session.get(Player, self.players[hero]))
        return {action['id']: action for action in (bundle or {}).get('actions') or []}

    def last_turn_id(self) -> int:
        with self.app.app_context():
            turn = DmTurn.query.filter_by(session_id=self.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
            return turn.turn_id if turn else 0

    def turns_since(self, turn_id: int) -> list[dict[str, Any]]:
        with self.app.app_context():
            turns = DmTurn.query.filter(DmTurn.session_id == self.ids['session_id'], DmTurn.turn_id > turn_id).order_by(DmTurn.turn_id).all()
            return [
                {
                    'narration': turn.dm_output or '',
                    'beats': [beat['text'] for beat in safe_json_loads(turn.metadata_json, {}).get('storyBeats') or []],
                    'pipeline': safe_json_loads(turn.metadata_json, {}).get('state_pipeline') or {},
                    'input': turn.player_input,
                }
                for turn in turns
            ]

    def last(self) -> dict[str, Any]:
        return self.turns_since(self.last_turn_id() - 1)[-1]

    def set_enemy_hp(self, current: int) -> None:
        enemy = self.enemy()
        self.operator([{'type': 'combat.participant.update', 'participantId': enemy['id'], 'hp': {**enemy['hp'], 'current': current}}])

    def steady_enemy(self) -> None:
        """Full morale: the cutthroat stands and fights rather than parley or run."""

        self.operator([{'type': 'combat.morale.update', 'participantId': self.enemy()['id'], 'morale': 100}])

    def close(self) -> None:
        for socket_client in self.sockets.values():
            if socket_client.is_connected():
                socket_client.disconnect()


def errors(received: list[dict]) -> list[str]:
    return [event['args'][0].get('error') for event in received if event['name'] == 'error']


@pytest.fixture()
def safe_mode(app, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    # The heroes roll 15 on every d20 and their highest damage; checks the engine rolls for them, 15.
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 14 if sides == 20 else sides - 1)
    monkeypatch.setattr('aidm_server.combat.actions.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda sides: min(sides, 12))


@pytest.fixture()
def table(app, client, socketio, safe_mode, monkeypatch):
    fight = FightTable(app, client, socketio, monkeypatch)
    yield fight
    fight.close()


def _shot_to_one_hit_point(table: FightTable) -> None:
    """Thalion's longbow hits for 8 (seeded): the cutthroat is left at 1 HP; then Thalion ends the turn."""

    table.set_enemy_hp(9)
    table.hud('Thalion', 'combat.attack.starter_ranger_longbow', table.enemy()['id'])
    assert table.enemy()['hp']['current'] == 1
    table.end_turn('Thalion')
    assert table.acting_hero() == 'Corin Ashby'


def test_the_owners_fight_replayed_tackle_yield_captive_questions_and_leaving_it_behind(table):
    start = table.last_turn_id()
    assert table.actions('Corin Ashby')['combat.grapple']['label'] == 'Close in and grapple Ashmarked Cutthroat'
    _shot_to_one_hit_point(table)

    tackled = table.type('Corin Ashby', 'Close in and tackle him to the floor')
    assert errors(tackled) == []
    enemy = table.enemy()
    assert ('grappled' in enemy['conditions'], enemy['grappledBy']) == (True, table.hero_id('Corin Ashby'))
    tackle = table.last()
    assert tackle['input'] == 'Close in and tackle him to the floor'
    assert tackle['narration'] == (
        'Corin Ashby closes in on the ashmarked cutthroat. They get an arm around the ashmarked cutthroat and bear it down: '
        "Athletics 19 against DC 12. It is going nowhere.\n\nIt is still Corin Ashby's turn."
    )
    assert tackle['beats'] == ['Corin Ashby — Athletics check: 19 — success (DC 12)', 'Grappled — Ashmarked Cutthroat']
    # The typed words took the grapple's own path: the move and the action are spent, nothing else.
    economy = table.combat()['flags']['turnEconomy']
    assert (economy['actionRemaining'], economy['movementRemaining']) == (0, 0)

    table.end_turn('Corin Ashby')
    yielded = table.last()
    assert yielded['narration'] == 'The ashmarked cutthroat goes slack. "Enough," it says.'
    assert [action['intentType'] for action in yielded['pipeline']['dmContextPacket']['combatState']['enemyResolvedActions']] == ['surrender']
    assert yielded['beats'][:2] == ['Ashmarked Cutthroat yields', 'The fight ends — they yield']
    assert '+50 XP each' in yielded['beats'] and 'Clue — Ash Marks on the Road' in yielded['beats']
    combat = table.combat()
    assert (combat['status'], combat['flags']['endReason']) == ('ended', 'enemies_surrendered')
    assert combat['flags']['lastOutcomeRewards']['outcome'] == 'surrender'
    assert table.snapshot()['currentScene']['captives'] == [
        {'id': enemy['id'], 'name': 'Ashmarked Cutthroat', 'enemyId': 'enemy_ashmarked_cutthroats'}
    ]

    told = []
    for _question in range(4):
        assert errors(table.type('Thalion', 'Question the Ashmarked Cutthroat')) == []
        told.append(table.last()['narration'])
    assert told == [
        "\"The road. Lately it don't take you where you're going. It takes you where it wants,\" the ashmarked cutthroat tells Thalion.",
        '"We were paid in ash-marked coin to bring names, ledgers and stone. I never saw who paid," the ashmarked cutthroat says, voice low.',
        "\"There's a man they call the Scribe. He don't want the road dead. He wants it to answer to him,\" the ashmarked cutthroat says, voice low.",
        'The ashmarked cutthroat glares and says nothing more the party can use.',
    ]

    table.send('Thalion', 'The party travels to The Old North Road.', {'kind': 'travel', 'source': 'scene_panel', 'location': {'id': 'loc_old_north_road', 'name': 'The Old North Road'}})
    assert table.last()['narration'].startswith('The party leaves the cutthroat bound for the reeve.')
    assert 'captives' not in table.snapshot()['currentScene']
    for turn in table.turns_since(start):
        print(turn['input'], '|', turn['narration'].replace('\n', ' '), '|', turn['beats'])


def test_the_captive_is_in_the_player_view_with_its_question(table):
    _shot_to_one_hit_point(table)
    table.type('Corin Ashby', 'Close in and tackle him to the floor')
    table.end_turn('Corin Ashby')

    from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player

    scene = filter_session_snapshot_for_player(table.snapshot())['currentScene']
    assert scene['captives'] == [
        {
            'id': table.enemy()['id'],
            'name': 'Ashmarked Cutthroat',
            'enemyId': 'enemy_ashmarked_cutthroats',
            'offeredActions': [{'kind': 'talk', 'label': 'Question Ashmarked Cutthroat'}],
        }
    ]


def _enemy_turn(table: FightTable) -> dict:
    """The enemy block the last turn resolved."""

    return table.last()['pipeline']['dmContextPacket']['combatState']['enemyResolvedActions'][0]


def _enemy_attack_mode(table: FightTable, hero: str) -> tuple[str, list[str]]:
    """How the cutthroat's knife would roll against the hero now."""

    from aidm_server.combat.modifiers import attack_context

    context = attack_context(table.enemy(), table.participant(hero), kind='enemy_attack', ranged=False, combat=table.combat())
    return context['mode'], context['reasons']


def test_a_failed_tackle_lets_it_break_away_and_the_engaged_paladins_opportunity_attack_drops_it(table, monkeypatch):
    table.end_turn('Thalion')
    table.set_enemy_hp(4)
    monkeypatch.setattr('aidm_server.combat.actions.deterministic_roller', lambda _seed: lambda _sides: 1)
    table.type('Corin Ashby', 'Close in and tackle him')
    assert table.last()['narration'].startswith(
        'Corin Ashby closes in on the ashmarked cutthroat. They grab for the ashmarked cutthroat, but it twists free: Athletics 5 against DC 12.'
    )
    assert 'grappled' not in table.enemy()['conditions']
    # Heroes already make opportunity attacks on an enemy leaving melee (combat/reactions.py); the paladin's is rolled here.
    monkeypatch.setattr('aidm_server.combat.attacks.deterministic_roller', lambda _seed: lambda _sides: 15)
    table.end_turn('Corin Ashby')

    turn = _enemy_turn(table)
    assert turn['intentType'] == 'retreat' and turn['droppedByReaction'] is True
    attack = turn['reactions'][0]['attack']
    assert (turn['reactions'][0]['reactorId'], attack['hit'], attack['weaponName']) == (table.hero_id('Corin Ashby'), True, 'Longsword')
    assert table.last()['narration'] == (
        f"As the ashmarked cutthroat breaks away, Corin Ashby's longsword catches it: {attack['attackTotal']} against AC 13, "
        f"{attack['damageTotal']} damage. The ashmarked cutthroat falls where it stands."
    )
    enemy = table.enemy()
    assert enemy['hp']['current'] == 0 and 'fled' not in enemy['conditions']
    assert table.combat()['flags']['endReason'] == 'all_enemies_defeated'
    assert table.participant('Corin Ashby')['reactionRemaining'] == 0


def test_an_unhurt_cutthroat_breaks_free_and_may_then_move(table):
    table.end_turn('Thalion')
    table.steady_enemy()
    table.type('Corin Ashby', 'Close in and grapple the cutthroat')
    assert table.enemy()['grappledBy'] == table.hero_id('Corin Ashby')
    actions = table.actions('Corin Ashby')
    assert actions['combat.subdue']['available'] is False or actions['combat.subdue']['reason'] == "This turn's action is already spent."
    table.end_turn('Corin Ashby')

    turn = _enemy_turn(table)
    assert (turn['intentType'], turn['breakFree']['success']) == ('break_free', True)
    escape = turn['breakFree']
    assert table.last()['narration'].startswith(
        f"The ashmarked cutthroat tears loose of Corin Ashby: {escape['total']} against DC {escape['dc']}."
    )
    enemy = table.enemy()
    assert 'grappled' not in enemy['conditions'] and 'grappledBy' not in enemy
    # Free again, it is planned and moves like any other enemy on its next turn.
    table.end_turn('Thalion')
    table.steady_enemy()
    table.end_turn('Corin Ashby')
    assert _enemy_turn(table)['intentType'] not in {'break_free', 'surrender'}


def test_bind_is_refused_on_an_unhurt_cutthroat_still_on_its_feet(table):
    start = table.last_turn_id()
    refused = table.type('Thalion', 'bind him')
    assert errors(refused) == ['It is still on its feet and fighting.']
    assert table.last_turn_id() == start
    assert table.actions('Thalion')['combat.subdue']['reason'] == 'It is still on its feet and fighting.'


def test_a_shove_puts_it_down_until_the_end_of_its_turn_with_advantage_against_it(table):
    table.end_turn('Thalion')
    table.steady_enemy()
    table.type('Corin Ashby', 'Close in and knock him down')
    assert table.last()['narration'].startswith(
        'Corin Ashby closes in on the ashmarked cutthroat. They put their shoulder into the ashmarked cutthroat and it goes down hard: '
        'Athletics 19 against DC 12.'
    )
    assert 'prone' in table.enemy()['conditions']
    target = next(target for target in table.actions('Corin Ashby')['combat.attack.starter_paladin_longsword']['targets'])
    assert (target['rollMode'], 'Target is prone' in target['rollReasons']) == ('advantage', True)

    # Its own attacks have disadvantage while it is down.
    assert _enemy_attack_mode(table, 'Corin Ashby') == ('disadvantage', ['Attacker is prone'])

    table.end_turn('Corin Ashby')
    # Down through its own turn (it does not stand as that turn starts); it stands only as that turn ends.
    assert _enemy_turn(table)['intentType'] in {'attack', 'use_ability'}
    assert 'prone' not in table.enemy()['conditions']


@pytest.mark.parametrize(('roll', 'yields'), [(15, True), (1, False)], ids=['yields', 'fights-on'])
def test_demanding_its_surrender(table, monkeypatch, roll, yields):
    monkeypatch.setattr('aidm_server.combat.actions.deterministic_roller', lambda _seed: lambda _sides: roll)
    table.end_turn('Thalion')
    table.set_enemy_hp(5)
    table.type('Corin Ashby', 'Close in and demand his surrender')

    narration = table.last()['narration']
    check = table.last()['pipeline']['dmContextPacket']['combatState']['playerResolvedCombatAction']['check']
    assert check['dc'] == 12
    if yields:
        assert narration.startswith(f'Corin Ashby closes in on the ashmarked cutthroat. "Enough," they tell it. Intimidation {check["total"]} against DC 12. The knife drops.')
        assert 'surrendered' in table.enemy()['conditions']
        assert table.combat()['flags']['endReason'] == 'enemies_surrendered'
        assert 'Ashmarked Cutthroat yields' in table.last()['beats']
    else:
        assert narration == (
            f'Corin Ashby closes in on the ashmarked cutthroat. "Enough," they tell it. Intimidation {check["total"]} against DC 12. '
            "It fights on.\n\nIt is still Corin Ashby's turn."
        )
        assert table.combat()['status'] == 'active'


@pytest.mark.parametrize(('roll', 'works'), [(15, True), (1, False)], ids=['success', 'failure'])
def test_an_improvised_action_rolls_and_can_leave_it_off_balance(table, monkeypatch, roll, works):
    monkeypatch.setattr('aidm_server.combat.actions.deterministic_roller', lambda _seed: lambda _sides: roll)
    table.type('Thalion', 'I kick the lantern at it')

    turn = table.last()
    action = turn['pipeline']['dmContextPacket']['combatState']['playerResolvedCombatAction']
    check = action['check']
    assert (action['actionType'], check['dc'], check['success']) == ('improvise', 12, works)
    assert table.combat()['flags']['turnEconomy']['actionRemaining'] == 0
    did = turn['pipeline']['dmContextPacket']['engineBrief']['did']
    assert did[0].startswith('Thalion tries something of their own ("I kick the lantern at it"): ')
    assert turn['beats'][0] == f"Thalion — {check['skill'].title()} check: {check['total']} — {'success' if works else 'failure'} (DC 12)"
    skill = check['skill'].title()
    if works:
        assert turn['narration'].startswith(f'Thalion kicks the lantern at it: {skill} {check["total"]} against DC 12. The ashmarked cutthroat is off balance.')
        assert 'off_balance' in table.enemy()['conditions']
        assert _enemy_attack_mode(table, 'Thalion') == ('disadvantage', ['Attacker has Off balance'])
        table.end_turn('Thalion')
        table.end_turn('Corin Ashby')
        assert 'off_balance' not in table.enemy()['conditions']
    else:
        assert turn['narration'].startswith(f'Thalion kicks the lantern at it, but it does not come off: {skill} {check["total"]} against DC 12.')
        assert 'off_balance' not in table.enemy()['conditions']


@pytest.mark.parametrize('words', ['I tell him to drop the knife.', '"Drop it and you walk away," Thalion says.', 'Who sent you?'])
def test_speech_in_a_fight_is_free(table, words):
    before = table.combat()
    received = table.type('Thalion', words)

    assert errors(received) == []
    after = table.combat()
    assert after['flags']['turnEconomy']['actionRemaining'] == 1 and table.acting_hero() == 'Thalion'
    assert [(record['hp'], record['conditions'], record['position']) for record in after['participants']] == [
        (record['hp'], record['conditions'], record['position']) for record in before['participants']
    ]
    turn = table.last()
    assert turn['input'] == words
    assert turn['narration'] == "It is still Thalion's turn."
    assert turn['pipeline']['postDmExtraction']['debug']['reason'] == 'fight_speech'
    assert turn['beats'] == []


def test_an_action_typed_out_of_turn_is_refused_and_speech_is_not(table):
    start = table.last_turn_id()
    refused = table.type('Corin Ashby', 'Bind this bitch')
    assert errors(refused) == ["It is Thalion's turn."]
    assert table.last_turn_id() == start

    assert errors(table.type('Corin Ashby', '"Easy now," Corin says.')) == []
    assert table.acting_hero() == 'Thalion'
    assert table.combat()['flags']['turnEconomy']['actorId'] == table.hero_id('Thalion')


# The AI narrator ----------------------------------------------------------------------------------------------------


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


def _said(table: FightTable, hero: str, message: str, reply: str | None, intent: dict | None = None) -> str:
    table.narrator.replies.append(reply)
    received = table.send(hero, message, intent)
    assert errors(received) == []
    return table.last()['narration']


def test_the_engine_says_whose_turn_it_is_and_the_model_cannot(ai_table):
    told = _said(
        ai_table,
        'Thalion',
        'Thalion acts.',
        'Thalion drops low behind the wagon wheel, bow half drawn. Corin Ashby, steady your shield, it is your turn. Dodge.',
        {'kind': 'combat', 'source': 'combat_hud', 'combat': {'action_id': 'combat.dodge'}},
    )

    assert told == "Thalion drops low behind the wagon wheel, bow half drawn.\n\nIt is still Thalion's turn."
    prompt = ai_table.narrator.prompts[-1]
    for rule in (
        '8. Resolve what the hero attempted this turn from the "Did" results. Never leave it "still to be done", never hand the decision back, and never end by naming an available action.',
        '9. Do not say whose turn it is or that a turn is over; the engine adds that.',
        '10. Enemies do only what "Did" says they did this turn. Otherwise they hold, watch or brace: they never attack, spring, close in, move off, flee, take or drop anything.',
    ):
        assert rule in prompt
    did = next(line for line in ENGINE_BRIEF_RE.search(prompt).group('brief').splitlines() if line.startswith('Did: '))
    assert did == 'Did: Thalion is dodging until the start of their next turn. No enemy has acted since the last narration. Thalion is dodging.'


def test_the_brief_lists_the_typed_tackle_under_did(ai_table):
    _said(ai_table, 'Thalion', 'Thalion ends the turn.', None, {'kind': 'combat', 'source': 'combat_hud', 'combat': {'action_id': 'combat.end_turn'}})
    told = _said(ai_table, 'Corin Ashby', 'Close in and tackle him to the floor', 'Corin crashes into the cutthroat and drives him into the mud.')

    brief = ENGINE_BRIEF_RE.search(ai_table.narrator.prompts[-1]).group('brief')
    assert (
        'Did: Corin Ashby closes into melee and engages Ashmarked Cutthroat. Corin Ashby grapples Ashmarked Cutthroat: '
        'Athletics 19 against DC 12, a success — Ashmarked Cutthroat is held. No enemy has acted since the last narration.'
    ) in brief
    assert told == "Corin crashes into the cutthroat and drives him into the mud.\n\nIt is still Corin Ashby's turn."
    assert 'grappled' in ai_table.enemy()['conditions']


def test_a_typed_tackle_makes_exactly_the_calls_of_the_grapple_button(ai_table, monkeypatch):
    from aidm_server.game_state.orchestration import turn_pipeline

    calls: Counter = Counter()

    def counted(name: str, function):
        def call(*args, **kwargs):
            calls[name] += 1
            return function(*args, **kwargs)

        return call

    for name in ('extract_pre_dm_actions', 'extract_post_dm_outcomes', 'prepare_combat_from_dm_response'):
        monkeypatch.setattr(turn_pipeline, name, counted(name, getattr(turn_pipeline, name)))
    ai_table.narrator.generate = counted('helper_model', ai_table.narrator.generate)
    ai_table.narrator.stream = counted('narration', ai_table.narrator.stream)

    _said(ai_table, 'Thalion', 'Thalion acts.', None, {'kind': 'combat', 'source': 'combat_hud', 'combat': {'action_id': 'combat.grapple', 'target_id': ai_table.enemy()['id']}})
    button = dict(calls)
    assert 'grappled' in ai_table.enemy()['conditions']
    ai_table.operator([{'type': 'combat.effect.consume', 'source': 'modifier_engine', 'consume': [{'participantId': ai_table.enemy()['id'], 'effectId': f"combat_grapple:{ai_table.hero_id('Thalion')}"}]}])
    _said(ai_table, 'Thalion', 'Thalion ends the turn.', None, {'kind': 'combat', 'source': 'combat_hud', 'combat': {'action_id': 'combat.end_turn'}})
    calls.clear()
    _said(ai_table, 'Corin Ashby', 'Close in and tackle him to the floor', None)

    assert dict(calls) == button
    assert button['narration'] == 1


# Rules ------------------------------------------------------------------------------------------------------------


def _enemy(**fields) -> dict:
    return {
        'id': 'enemy_goblin',
        'name': 'Goblin',
        'team': 'enemy',
        'hp': {'current': 10, 'max': 10, 'temp': 0},
        'stats': {'strength': 8, 'dexterity': 14},
        'conditions': [],
        'position': {'rangeBand': 'melee', 'engagedWith': ['player_1']},
        **fields,
    }


def _hero(**fields) -> dict:
    return {
        'id': 'player_1',
        'name': 'Mara Fen',
        'team': 'player',
        'hp': {'current': 10, 'max': 10, 'temp': 0},
        'conditions': [],
        'position': {'rangeBand': 'melee', 'engagedWith': ['enemy_goblin']},
        **fields,
    }


def _maneuvers(enemy: dict, hero: dict | None = None, *, pack: dict | None = None) -> dict[str, dict]:
    hero = hero or _hero()
    combat = {'status': 'active', 'round': 1, 'participants': [hero, enemy], 'flags': {}}
    snapshot = {'combat': combat, **({'campaignPack': pack} if pack else {})}
    return {action['type']: action for action in maneuver_actions(snapshot, hero, combat)}


def _target(action: dict) -> dict:
    return action['targets'][0]


def test_an_unhurt_enemy_in_reach_can_be_grappled_or_shoved_but_not_bound_or_asked_to_yield():
    actions = _maneuvers(_enemy())

    assert [(action['id'], action['label'], action['cost']['action']) for action in actions.values()] == [
        ('combat.grapple', 'Grapple Goblin', 1),
        ('combat.shove', 'Shove Goblin down', 1),
        ('combat.subdue', 'Bind Goblin', 1),
        ('combat.demand_surrender', "Demand Goblin's surrender", 1),
    ]
    assert (actions['grapple']['available'], actions['shove']['available']) == (True, True)
    assert (actions['subdue']['available'], actions['subdue']['reason']) == (False, 'It is still on its feet and fighting.')
    assert (actions['demand_surrender']['available'], actions['demand_surrender']['reason']) == (False, 'It is not beaten enough to yield.')


def test_out_of_reach_with_the_move_unspent_a_maneuver_closes_in_first():
    hero = _hero(position={'rangeBand': 'near'})
    actions = _maneuvers(_enemy(position={'rangeBand': 'near'}), hero)

    assert (actions['grapple']['label'], actions['grapple']['requiresMove'], _target(actions['grapple'])['requiresMove']) == (
        'Close in and grapple Goblin',
        True,
        True,
    )
    assert actions['shove']['label'] == 'Close in and shove Goblin down'


@pytest.mark.parametrize(
    ('enemy', 'binds', 'yields'),
    [
        (_enemy(hp={'current': 2, 'max': 10, 'temp': 0}), True, True),
        (_enemy(hp={'current': 5, 'max': 10, 'temp': 0}), False, True),
        (_enemy(conditions=['prone']), True, True),
        (_enemy(conditions=['surrendered']), True, None),
    ],
    ids=['quarter-hp', 'half-hp', 'prone', 'yielded'],
)
def test_bind_and_demand_need_a_beaten_held_fallen_or_yielding_enemy(enemy, binds, yields):
    actions = _maneuvers(enemy)

    assert _target(actions['subdue'])['available'] is binds
    if yields is None:
        # A yielded enemy is out of the fight: only binding it is offered.
        assert actions['demand_surrender']['targets'] == [] and actions['grapple']['targets'] == []
    else:
        assert _target(actions['demand_surrender'])['available'] is yields


def test_a_held_enemy_cannot_be_grappled_again_and_a_fallen_one_cannot_be_shoved():
    held = _enemy(
        conditions=['grappled'],
        activeEffects=[{'id': 'combat_grapple:player_1', 'sourceId': 'combat_grapple', 'casterId': 'player_1', 'condition': 'grappled'}],
    )
    assert _target(_maneuvers(held)['grapple'])['reason'] == 'It is already held.'
    assert _target(_maneuvers(held)['subdue'])['available'] is True
    assert _target(_maneuvers(_enemy(conditions=['prone']))['shove'])['reason'] == 'It is already down.'


def test_a_boss_will_not_yield_above_a_quarter_of_its_hit_points():
    boss = _enemy(challengeTier='boss', hp={'current': 4, 'max': 10, 'temp': 0})
    assert _target(_maneuvers(boss)['demand_surrender'])['reason'] == 'It will not yield while it can still fight.'
    assert _target(_maneuvers({**boss, 'hp': {'current': 2, 'max': 10, 'temp': 0}})['demand_surrender'])['available'] is True


def test_an_enemy_record_with_a_pronoun_is_told_by_it():
    pack = {'catalog': {'enemies': [{'id': 'enemy_cutthroats', 'name': 'Cutthroats', 'pronoun': 'he'}]}}
    actions = _maneuvers(_enemy(campaignPackEnemyId='enemy_cutthroats'), pack=pack)
    assert actions['subdue']['reason'] == 'He is still on his feet and fighting.'


def _planned(enemy: dict, *, held: bool) -> str:
    hero = _hero()
    if held:
        enemy = {
            **enemy,
            'conditions': ['grappled'],
            'activeEffects': [{'id': 'combat_grapple:player_1', 'sourceId': 'combat_grapple', 'casterId': 'player_1', 'condition': 'grappled'}],
        }
    combat = {'status': 'active', 'round': 2, 'participants': [hero, _hero(id='player_2', name='Seren', position={'rangeBand': 'near'}), enemy], 'flags': {}}
    return plan_intent_for_enemy(enemy, combat)['intentType']


COWARD = {
    'abilities': [{'id': 'knife', 'name': 'Knife', 'type': 'attack', 'range': 'melee', 'attackBonus': 4, 'damage': {'dice': '1d6'}}],
    'morale': 60,
    'behavior': {'intelligenceProfile': 'trained', 'selfPreservation': 82, 'survivalRules': {'fleeBelowHpPercent': 45, 'fleeIfOutnumbered': True}},
}


def test_the_planner_never_has_a_held_enemy_run_it_yields_or_tries_to_break_free():
    hurt = _enemy(**COWARD, hp={'current': 2, 'max': 10, 'temp': 0})
    unhurt = _enemy(**COWARD)

    assert _planned(hurt, held=False) == 'retreat'
    assert _planned(hurt, held=True) == 'surrender'
    assert _planned(unhurt, held=True) == 'break_free'
    assert _planned({**unhurt, 'morale': 0}, held=True) == 'surrender'


def test_a_hold_lapses_when_the_grappler_is_down_or_out_of_reach():
    held = _enemy(
        conditions=['grappled'],
        activeEffects=[{'id': 'combat_grapple:player_1', 'sourceId': 'combat_grapple', 'casterId': 'player_1', 'condition': 'grappled'}],
    )
    combat = {'participants': [_hero(), held]}
    assert grapple_holds(held, combat) is True
    assert grapple_holds(held, {'participants': [_hero(hp={'current': 0, 'max': 10, 'temp': 0}), held]}) is False
    apart = {**held, 'position': {'rangeBand': 'melee'}}
    assert grapple_holds(apart, {'participants': [_hero(position={'rangeBand': 'near'}), apart]}) is False


def _fight_hint(text_brief: dict | None = None, *, line: str = "It is still Thalion's turn.") -> dict:
    return {
        'requires_roll': False,
        'state_pipeline': {
            'combatState': {'status': 'active'},
            'narration': {'nextActorLine': line},
            'engineBrief': text_brief or {'offered': ['Dodge', 'Grapple Ashmarked Cutthroat', 'End turn']},
        },
    }


@pytest.mark.parametrize(
    'sentence',
    [
        'Corin Ashby, wiping the rain from his eyes, it is your turn.',
        "It's Corin Ashby's turn.",
        'Your turn is spent.',
        'Corin Ashby is up next.',
        'Thalion weighs the next move.',
        'Now it is your turn to act.',
    ],
)
def test_the_model_calling_a_turn_in_a_fight_is_removed(sentence):
    assert clean_ai_narration(f'Rain hammers the wagons. {sentence}', _fight_hint()).text == 'Rain hammers the wagons.'


def test_a_spoken_line_about_turns_stays_and_outside_a_fight_turn_words_are_the_models():
    spoken = clean_ai_narration('The reeve laughs. "Your turn will come," she says.', _fight_hint()).text
    assert spoken == 'The reeve laughs. "Your turn will come," she says.'
    outside = {'requires_roll': False, 'state_pipeline': {'engineBrief': {'offered': ['Search the strongbox']}}}
    assert clean_ai_narration('It is your turn to look around. Search the strongbox.', outside).text == 'It is your turn to look around.'


@pytest.mark.parametrize('label', ['Grapple Ashmarked Cutthroat.', 'grapple ashmarked cutthroat', 'End turn!'])
def test_a_final_bare_action_label_is_removed(label):
    assert clean_ai_narration(f'The cutthroat circles. {label}', _fight_hint()).text == 'The cutthroat circles.'


def test_the_engines_turn_line_is_the_scripted_narrators_and_none_when_the_fight_ends():
    hint = _fight_hint(line='Corin Ashby is up next.')
    assert engine_turn_line(hint) == 'Corin Ashby is up next.'
    hint['state_pipeline']['narration']['fightEnds'] = 'enemies_surrendered'
    assert engine_turn_line(hint) == ''


@pytest.mark.parametrize(
    ('message', 'kind'),
    [
        ('"Drop it," I growl.', 'speech'),
        ('I shout at him to stand down', 'speech'),
        ('Thalion whispers a prayer', 'speech'),
        ('Where is the ledger?', 'speech'),
        ('Close in and tackle him to the floor', 'action'),
        ('I kick the lantern at it', 'improvised'),
        ('I cut the rope', 'improvised'),
    ],
)
def test_how_typed_words_in_a_fight_are_taken(message, kind):
    target = {'id': 'enemy_goblin', 'name': 'Goblin', 'available': True, 'reason': ''}
    grapple = {'id': 'combat.grapple', 'type': 'grapple', 'available': True, 'requiresTarget': True, 'targets': [target]}
    attack = {'id': 'combat.attack.dagger', 'type': 'attack', 'available': True, 'requiresTarget': True, 'targets': [target]}
    bundle = {'isCurrentActor': True, 'actorName': 'Thalion', 'actions': [attack, grapple]}
    words = fight_words({'combat': {'participants': [_enemy()]}}, bundle, message=message)

    assert words.kind == kind
    if kind == 'action':
        assert (words.action_id, words.target_id) == ('combat.grapple', 'enemy_goblin')
    if kind == 'improvised':
        assert (words.action_id, words.target_id) == ('combat.improvise', 'enemy_goblin')


@pytest.mark.parametrize(
    ('message', 'action_type'),
    [
        ('I hold him down', 'grapple'),
        ('jump on the cutthroat', 'grapple'),
        ('I knock him down', 'shove'),
        ('knock him out', 'subdue'),
        ('we take him alive', 'subdue'),
        ('I tie him up', 'subdue'),
        ('Yield!', 'demand_surrender'),
        ('I demand his surrender', 'demand_surrender'),
        ('I swung at the cutthroat', 'attack'),
        ('fall back', 'reposition'),
        ('I end my turn', 'end_turn'),
    ],
)
def test_fight_verbs_name_their_action(message, action_type):
    from aidm_server.game_state.extraction.free_text_actions import _fight_clauses

    assert _fight_clauses(message)[0][0] == action_type


def test_alone_the_hero_is_spoken_to(app, client, socketio, safe_mode, monkeypatch):
    response = client.post('/api/onboarding/play-now', json={})
    ids = response.get_json()
    monkeypatch.setattr('aidm_server.combat.state._deterministic_initiative_roll', lambda _seed, actor_id: 20 if actor_id.startswith('player_') else 1)
    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id'], 'workspace_id': ids['workspace_id']})
    socket_client.get_received()
    started = client.post(
        f"/api/sessions/{ids['session_id']}/combat/apply-state-changes",
        json={'changes': [{'type': 'combat.start', 'campaignPackEncounterId': 'enc_empty_caravan_pressure', 'combat': {}}]},
    )
    assert started.status_code == 200
    try:
        socket_client.emit(
            'send_message',
            {'session_id': ids['session_id'], 'campaign_id': ids['campaign_id'], 'player_id': ids['player_id'], 'message': 'I close in and tackle him'},
        )
        received = socket_client.get_received()
    finally:
        socket_client.disconnect()

    told = next(event['args'][0]['text'] for event in received if event['name'] == 'dm_response_end')
    assert told.startswith('You close in on the ashmarked cutthroat. You get an arm around the ashmarked cutthroat and bear it down: Athletics ')
    assert told.endswith('against DC 12. It is going nowhere.\n\nIt is your turn.')


def test_binding_a_yielded_enemy_needs_no_roll_and_captures_it():
    from aidm_server.combat.actions import resolve_combat_action

    state = {'combat': {'status': 'active', 'round': 2, 'participants': [_hero(), _enemy(conditions=['surrendered'])], 'flags': {}}}
    resolved = resolve_combat_action(
        state=state,
        combat_action={'action_id': 'combat.subdue', 'action_type': 'subdue', 'target_id': 'enemy_goblin', 'check': {'skill': 'athletics', 'modifier': 0}},
        actor_id='player_1',
        turn_id=7,
    )

    assert resolved['ok'] is True and 'check' not in resolved['result']
    assert resolved['result']['outcome'] == 'Mara Fen binds Goblin, who has yielded: no roll — Goblin is captured, out of the fight and alive.'
    assert [(change['type'], change.get('condition')) for change in resolved['changes']] == [('combat.condition.add', 'captured')]


def test_an_enemy_that_gives_up_by_itself_is_held_only_when_it_has_something_to_say():
    from aidm_server.combat.maneuvers import captives_from_combat

    combat = {
        'participants': [
            {'id': 'player_1', 'team': 'player', 'name': 'Arden Vale', 'hp': {'current': 12, 'max': 12}},
            {
                'id': 'enemy_cutthroat_1',
                'team': 'enemy',
                'name': 'Ashmarked Cutthroat',
                'campaignPackEnemyId': 'enemy_ashmarked_cutthroats',
                'conditions': ['negotiated'],
                'hp': {'current': 3, 'max': 11},
            },
            {
                'id': 'enemy_lantern_1',
                'team': 'enemy',
                'name': 'Pale Lantern',
                'campaignPackEnemyId': 'enemy_pale_lanterns',
                'conditions': ['negotiated'],
                'hp': {'current': 4, 'max': 9},
            },
        ]
    }

    held = captives_from_combat(combat, has_captive_lines=lambda enemy_id: enemy_id == 'enemy_ashmarked_cutthroats')

    assert held == [
        {'id': 'enemy_cutthroat_1', 'name': 'Ashmarked Cutthroat', 'enemyId': 'enemy_ashmarked_cutthroats'}
    ]
    # Without a pack to ask, giving up alone holds no one; a bound enemy is always held.
    assert captives_from_combat(combat) == []
    combat['participants'][2]['conditions'] = ['captured']
    assert [captive['id'] for captive in captives_from_combat(combat)] == ['enemy_lantern_1']
