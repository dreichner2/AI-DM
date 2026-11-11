"""Two friends at one table, through the real socket pipeline in Safe Mode: nobody dies, and the story names who
did what. Arden Vale (the ready-made hero) and Seren Ashgrove (a created cleric) play the Play Now adventure."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import pytest
import requests

from aidm_server.combat.legal_actions import legal_combat_actions_for_player
from aidm_server.database import db
from aidm_server.game_clock import advance_phases
from aidm_server.models import DmTurn, Player, Session, safe_json_dumps, safe_json_loads
from tests.ai_narrator import AiTable, ai_narration

PACK = Path(__file__).resolve().parents[1] / 'docs' / 'examples' / 'the_road_of_unremembered_kings_campaign.json'
YOU = re.compile(r'\byou(?:r|rself)?\b', re.IGNORECASE)
FINISHER_QUESTION = re.compile(r'^(?:Arden Vale|Seren Ashgrove), how do you end it\?$')
HEROES = ('Arden Vale', 'Seren Ashgrove')
OUT = {'unconscious', 'stable', 'out_of_fight'}

CLERIC = {
    'character_name': 'Seren Ashgrove',
    'race': 'Human',
    'char_class': 'Cleric',
    'level': 1,
    'background': 'acolyte',
    'stats': {
        'ability_scores': {'strength': 14, 'dexterity': 10, 'constitution': 14, 'intelligence': 8, 'wisdom': 15, 'charisma': 10},
        'point_buy': {'budget': 27},
        'gold': 0,
    },
}


class PartyTable:
    """The Play Now table: Arden Vale's player, and in party play Seren Ashgrove's at a second browser."""

    def __init__(self, app, client, socketio, *, party: bool):
        self.app = app
        self.client = client
        response = client.post('/api/onboarding/play-now', json={})
        assert response.status_code == 201, response.get_data(as_text=True)
        self.ids = {key: response.get_json()[key] for key in ('session_id', 'campaign_id', 'player_id', 'workspace_id')}
        self.players = {'Arden Vale': self.ids['player_id']}
        if party:
            created = client.post(f"/api/players/campaigns/{self.ids['campaign_id']}/players", json=CLERIC)
            assert created.status_code == 201, created.get_json()
            self.players['Seren Ashgrove'] = created.get_json()['player_id']
        self.sockets = {}
        for name, player_id in self.players.items():
            socket_client = socketio.test_client(app, flask_test_client=app.test_client())
            socket_client.emit('join_session', {'session_id': self.ids['session_id'], 'player_id': player_id, 'workspace_id': self.ids['workspace_id']})
            self.sockets[name] = socket_client
        self.drain()
        self.sent = 0

    def drain(self) -> None:
        for socket_client in self.sockets.values():
            socket_client.get_received()

    def send(self, hero: str, message: str, intent: dict | None = None) -> list[dict]:
        self.sent += 1
        message_id = f'party-{self.sent}'
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

    def snapshot(self) -> dict:
        with self.app.app_context():
            return safe_json_loads(db.session.get(Session, self.ids['session_id']).state_snapshot, {})

    def last_turn_id(self) -> int:
        with self.app.app_context():
            turn = DmTurn.query.filter_by(session_id=self.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
            return turn.turn_id if turn else 0

    def turns_since(self, turn_id: int) -> list[dict[str, Any]]:
        with self.app.app_context():
            turns = DmTurn.query.filter(DmTurn.session_id == self.ids['session_id'], DmTurn.turn_id > turn_id).order_by(DmTurn.turn_id).all()
            metadata = [safe_json_loads(turn.metadata_json, {}) for turn in turns]
            return [
                {'narration': turn.dm_output or '', 'beats': meta.get('storyBeats') or [], 'player': turn.player_id, 'pipeline': meta.get('state_pipeline') or {}}
                for turn, meta in zip(turns, metadata)
            ]

    def actions(self, hero: str) -> list[dict]:
        with self.app.app_context():
            bundle = legal_combat_actions_for_player(self.snapshot(), db.session.get(Player, self.players[hero]))
        return (bundle or {}).get('actions') or []

    def hud(self, hero: str, message: str, action_id: str, target_id: str | None = None) -> list[dict]:
        combat = {'action_id': action_id, **({'target_id': target_id} if target_id else {})}
        return self.send(hero, message, {'kind': 'combat', 'source': 'combat_hud', 'combat': combat})

    def travel(self, hero: str, location_id: str, name: str) -> list[dict]:
        intent = {'kind': 'travel', 'source': 'scene_panel', 'location': {'id': location_id, 'name': name}}
        return self.send(hero, f'The party travels to {name}.', intent)

    def acting_hero(self) -> str | None:
        flags = (self.snapshot().get('combat') or {}).get('flags') or {}
        return next((name for name, player_id in self.players.items() if flags.get('activeActorId') == f'player_{player_id}'), None)

    def take_turn(self, hero: str) -> None:
        """Swing at the first enemy in reach (closing in first when it must), else advance on one standing off,
        then end the turn."""

        actions = self.actions(hero)
        attack = next((action for action in actions if action['id'].startswith('combat.attack.') and action['available']), None)
        target = next((target for target in (attack or {}).get('targets') or [] if target['available']), None)
        if target:
            self.hud(hero, f'{hero} attacks.', attack['id'], target['id'])
        else:
            advance = next((action for action in actions if action['id'] == 'combat.advance' and action['available']), None)
            toward = next((target for target in (advance or {}).get('targets') or [] if target['available']), None)
            if toward:
                self.hud(hero, f'{hero} advances.', advance['id'], toward['id'])
        if self.acting_hero() == hero and self.snapshot()['combat']['status'] == 'active':
            self.hud(hero, f'{hero} ends their turn.', 'combat.end_turn')

    def fight(self, rounds: int = 12) -> None:
        for _step in range(rounds * len(self.players)):
            if (self.snapshot().get('combat') or {}).get('status') != 'active':
                return
            hero = self.acting_hero()
            assert hero, self.snapshot()['combat'].get('flags')
            self.take_turn(hero)

    def operator(self, changes: list[dict]) -> None:
        """Set the table up through the operator state API."""

        response = self.client.post(f"/api/sessions/{self.ids['session_id']}/combat/apply-state-changes", json={'changes': changes})
        assert response.status_code == 200, response.get_json()
        assert response.get_json()['validation']['rejected'] == []

    def stats(self, hero: str) -> dict:
        with self.app.app_context():
            return safe_json_loads(db.session.get(Player, self.players[hero]).stats, {})

    def hero_id(self, hero: str) -> str:
        return f'player_{self.players[hero]}'

    def hero(self, hero: str) -> dict:
        return next(record for record in self.snapshot()['playerCharacters'] if record['name'] == hero)

    def participant(self, hero: str) -> dict:
        return next(record for record in self.snapshot()['combat']['participants'] if record['name'] == hero)

    def cast(self, hero: str, spell: str, *targets: str) -> list[dict]:
        intent = {
            'kind': 'spell',
            'source': 'scene_panel',
            'spell': {'name': spell, 'effect': 'Resolve the server-owned spell effect.', 'resource_pool': 'auto', 'target_ids': [self.hero_id(name) for name in targets]},
        }
        return self.send(hero, f"{hero} casts {spell} at {', '.join(targets)}.", intent)

    def close(self) -> None:
        for socket_client in self.sockets.values():
            if socket_client.is_connected():
                socket_client.disconnect()


@pytest.fixture()
def safe_mode(app, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    # Seeded dice: object checks roll 15, the heroes 15 to hit and their highest damage, enemies 12.
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 14 if sides == 20 else sides - 1)
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda sides: min(sides, 12))


@pytest.fixture()
def party(app, client, socketio, safe_mode):
    table = PartyTable(app, client, socketio, party=True)
    yield table
    table.close()




def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


# Authored pack text (descriptions, setups, adjustments, endings) is printed as authored.
AUTHORED = sorted(
    {sentence for text in _strings(json.loads(PACK.read_text())) for sentence in re.split(r'(?<=[.!?])\s+', text.strip()) if len(sentence) > 12},
    key=len,
    reverse=True,
)


def _engine_sentences(narration: str) -> list[str]:
    for passage in AUTHORED:
        narration = narration.replace(passage, '')
    return [sentence for sentence in re.split(r'(?<=[.!?])\s+', narration.replace('\n', ' ')) if sentence.strip()]


def _road_fight(table: PartyTable) -> list[dict]:
    start = table.last_turn_id()
    table.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    table.fight()
    assert table.snapshot()['combat']['status'] == 'ended'
    return table.turns_since(start)


def test_the_road_fight_in_party_play_names_each_hero_and_never_says_you(party):
    turns = _road_fight(party)
    names = {player_id: name for name, player_id in party.players.items()}
    narrations = [turn['narration'] for turn in turns]
    beats = [beat['text'] for turn in turns for beat in turn['beats']]

    # The one sentence that speaks to a hero is the finishing blow's question, which names them.
    assert [
        sentence for turn in turns for sentence in _engine_sentences(turn['narration']) if YOU.search(sentence) and not FINISHER_QUESTION.match(sentence)
    ] == []
    assert [beat for beat in beats if YOU.search(beat)] == []
    for turn in turns:
        for sentence in _engine_sentences(turn['narration']):
            # A hero's own blow or move is told on that hero's turn, by name.
            own = re.match(r"^(?P<hero>[A-Z][a-z]+ [A-Z][a-z]+)(?:'s \w+ | closes in )", sentence)
            if own and own['hero'] in HEROES:
                assert own['hero'] == names[turn['player']], sentence
    # The rolls are shown as cards, so the sentences tell what came of them without their numbers. The pale lantern
    # stands off out of reach, so the heroes take the wardbound beast first.
    assert 'Arden Vale closes in on the wardbound beast. Their longsword finds the wardbound beast and draws blood.' in narrations[1]
    assert "The wardbound beast's pounce lands on Seren Ashgrove." in narrations[0]
    assert "Arden Vale shakes off the pale lantern's lure." in narrations[0]
    # The enemies opened the fight after Arden's travel, so the round has come back to Arden.
    assert narrations[0].endswith("It is Arden Vale's turn again.")
    # Arden ends the turn and nobody acts before Seren: only who is up next.
    assert narrations[2] == 'Seren Ashgrove is up next.'
    assert narrations[3].startswith("Seren Ashgrove's mace finds the wardbound beast and draws blood — their wounds drag at them.")
    # Then Arden goes out after the lantern, and the next move closes in on it.
    assert narrations[5].startswith('Arden Vale moves out toward the pale lantern.')
    assert 'Arden Vale closes in on the pale lantern.' in narrations[7]
    assert '+10 gp each' in beats
    assert [beat['text'] for beat in turns[4]['beats']] == ['Seren Ashgrove took 3 damage', 'Seren Ashgrove is dying', 'Seren Ashgrove is unconscious']
    assert 'Seren Ashgrove: death save: 12 — success' in beats
    # The lantern falls in the enemies' phase, after Seren's death save of that round.
    assert [beat['text'] for beat in turns[-1]['beats']] == [
        'Seren Ashgrove comes round', 'Seren Ashgrove: death save: 4 — failure', 'Victory', '+10 gp each', '+75 XP each'
    ]


def test_the_same_road_fight_alone_still_speaks_to_the_hero(app, client, socketio, safe_mode):
    table = PartyTable(app, client, socketio, party=False)
    try:
        narration = ' '.join(turn['narration'] for turn in _road_fight(table))
    finally:
        table.close()

    # The rolls are shown as cards, so the sentences tell what came of them without their numbers.
    assert "You shake off the pale lantern's lure." in narration
    assert 'Your longsword finds the pale lantern and draws blood.' in narration
    assert 'It is your turn.' in narration


def _participant_id(table: PartyTable, name: str) -> str:
    return next(participant['id'] for participant in table.snapshot()['combat']['participants'] if participant['name'] == name)


def _down(table: PartyTable, hero: str) -> None:
    """Knock a hero to 0 HP in the fight: dying, death saves 0/0."""

    table.operator([{'type': 'health.damage', 'actorId': table.hero_id(hero), 'amount': table.hero(hero)['health']['currentHp']}])


def test_three_failed_death_saves_leave_a_hero_out_of_the_fight_until_their_companion_wins(party, monkeypatch):
    # Enemies roll 1 and miss; every death save rolls 5, a failure.
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda _sides: 1)
    monkeypatch.setattr('aidm_server.combat.pipeline.death_save_roll', lambda *_args: 5)
    monkeypatch.setattr('aidm_server.combat.vitality.death_save_roll', lambda *_args: 5)
    party.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    clock = party.snapshot()['clock']
    party.hud('Arden Vale', 'Arden Vale ends their turn.', 'combat.end_turn')
    _down(party, 'Arden Vale')
    start = party.last_turn_id()
    while party.participant('Arden Vale')['deathSaves']['failures'] < 3 and 'out_of_fight' not in party.participant('Arden Vale')['conditions']:
        assert party.acting_hero() == 'Seren Ashgrove'
        party.hud('Seren Ashgrove', 'Seren Ashgrove ends their turn.', 'combat.end_turn')

    out = party.participant('Arden Vale')
    assert set(out['conditions']) == OUT and out['isAlive'] is True and out['deathSaves'] == {'successes': 0, 'failures': 0}
    assert set(party.hero('Arden Vale')['health']['conditions']) == OUT
    beats = [beat['text'] for turn in party.turns_since(start) for beat in turn['beats']]
    assert 'Arden Vale is out of the fight' in beats
    assert not [beat for beat in beats if 'dead' in beat.lower()]

    # Nothing raises a hero out of the fight: a Healing Word aimed at Arden is refused and changes nothing.
    refused = party.cast('Seren Ashgrove', 'Healing Word', 'Arden Vale')
    assert [event['args'][0]['error'] for event in refused if event['name'] == 'error'] == ['They are out of this fight.']
    assert party.participant('Arden Vale')['hp']['current'] == 0

    won = party.last_turn_id()
    party.fight()

    assert party.snapshot()['combat']['status'] == 'ended'
    assert party.snapshot()['combat']['flags']['endReason'] == 'all_enemies_defeated'
    # Out of the fight, Arden took no turn and no enemy went for him.
    enemy_targets = {
        action.get('targetId')
        for turn in party.turns_since(won)
        for action in ((turn['pipeline'].get('dmContextPacket') or {}).get('combatState') or {}).get('enemyResolvedActions') or []
    }
    assert enemy_targets and party.hero_id('Arden Vale') not in enemy_targets
    assert {turn['player'] for turn in party.turns_since(won)} == {party.players['Seren Ashgrove']}
    health = party.hero('Arden Vale')['health']
    assert (health['currentHp'], health['conditions'], health['deathSaves']) == (1, [], {'successes': 0, 'failures': 0})
    assert (party.participant('Arden Vale')['hp']['current'], party.participant('Arden Vale')['conditions']) == (1, [])
    final = party.turns_since(won)[-1]['beats']
    assert {'kind': 'condition', 'text': 'Arden Vale comes round'} in final
    assert party.snapshot()['clock'] == clock


def test_massive_damage_and_a_failure_taken_at_zero_hp_put_a_hero_out_of_the_fight(party):
    party.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    seren = party.hero('Seren Ashgrove')['health']

    party.operator([{'type': 'health.damage', 'actorId': party.hero_id('Seren Ashgrove'), 'amount': seren['currentHp'] + seren['maxHp']}])
    _down(party, 'Arden Vale')
    for _hit in range(3):
        party.operator([{'type': 'health.damage', 'actorId': party.hero_id('Arden Vale'), 'amount': 1}])

    for hero in HEROES:
        participant = party.participant(hero)
        assert set(participant['conditions']) == OUT, hero
        assert (participant['isAlive'], participant['deathSaves']) == (True, {'successes': 0, 'failures': 0})
        assert set(party.hero(hero)['health']['conditions']) == OUT


def test_with_every_hero_down_the_party_is_beaten_as_before(party, monkeypatch):
    party.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    clock = party.snapshot()['clock']
    party.hud('Arden Vale', 'Arden Vale ends their turn.', 'combat.end_turn')
    arden = party.hero('Arden Vale')['health']
    party.operator([
        {'type': 'health.damage', 'actorId': party.hero_id('Arden Vale'), 'amount': arden['currentHp'] + arden['maxHp']},
        {'type': 'health.damage', 'actorId': party.hero_id('Seren Ashgrove'), 'amount': party.hero('Seren Ashgrove')['health']['currentHp'] - 1},
    ])
    # Every enemy die rolls its highest, so the next blow drops Seren too.
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda sides: sides)
    start = party.last_turn_id()

    party.hud('Seren Ashgrove', 'Seren Ashgrove ends their turn.', 'combat.end_turn')

    snapshot = party.snapshot()
    assert (snapshot['combat']['status'], snapshot['combat']['flags']['endReason']) == ('ended', 'objective_failed')
    for hero in HEROES:
        assert (party.hero(hero)['health']['currentHp'], party.hero(hero)['health']['conditions']) == (1, [])
    assert [participant['team'] for participant in snapshot['combat']['participants']] == ['player', 'player']
    assert snapshot['clock'] == {**clock, **advance_phases(clock, 1)}
    assert 'enc_pale_lantern_detour' not in snapshot['flags'].get('campaignPackTriggeredEncounterIds', [])
    beats = [beat['text'] for turn in party.turns_since(start) for beat in turn['beats']]
    assert 'Defeated — you wake later with 1 HP' in beats


def test_a_hero_still_dying_when_the_fight_is_won_comes_round_with_one_hit_point(party, monkeypatch):
    # Every death save succeeds, and the fight is over before a third one.
    monkeypatch.setattr('aidm_server.combat.pipeline.death_save_roll', lambda *_args: 12)
    monkeypatch.setattr('aidm_server.combat.vitality.death_save_roll', lambda *_args: 12)
    party.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    # The lantern starts out of reach; a move away, the fight is won before a third death save.
    party.operator([{'type': 'combat.move', 'participantId': _participant_id(party, 'Pale Lantern'), 'toRangeBand': 'near'}])
    clock = party.snapshot()['clock']
    _down(party, 'Seren Ashgrove')
    states = []
    while party.snapshot()['combat']['status'] == 'active':
        states.append(set(party.participant('Seren Ashgrove')['conditions']))
        party.take_turn('Arden Vale')

    assert states[-1] == {'unconscious', 'dying'}
    health = party.hero('Seren Ashgrove')['health']
    assert (health['currentHp'], health['conditions']) == (1, [])
    assert {'kind': 'condition', 'text': 'Seren Ashgrove comes round'} in party.turns_since(0)[-1]['beats']
    assert party.snapshot()['clock'] == clock


def test_both_heroes_share_the_fights_rewards_and_the_beats_say_each(party):
    before = {hero: party.stats(hero) for hero in HEROES}

    turns = _road_fight(party)

    for hero in HEROES:
        after = party.stats(hero)
        assert (after['xp'] - before[hero]['xp'], after['gold'] - before[hero]['gold']) == (75, 10)
    beats = [beat for turn in turns for beat in turn['beats']]
    assert {'kind': 'coins', 'text': '+10 gp each'} in beats
    assert {'kind': 'xp', 'text': '+75 XP each'} in beats


def _old_save_dead(table: PartyTable, hero: str) -> None:
    """Write a hero the way a save from before this rule kept a dead one: on their row and in the fight."""

    with table.app.app_context():
        player = db.session.get(Player, table.players[hero])
        stats = safe_json_loads(player.stats, {})
        stats.update(current_hp=0, hp_current=0, conditions=['dead'], death_saves={'successes': 0, 'failures': 3})
        player.stats = safe_json_dumps(stats, {})
        session_obj = db.session.get(Session, table.ids['session_id'])
        snapshot = safe_json_loads(session_obj.state_snapshot, {})
        for participant in (snapshot.get('combat') or {}).get('participants') or []:
            if participant.get('name') == hero:
                participant.update(hp={**participant['hp'], 'current': 0}, conditions=['dead'], isAlive=False, isConscious=False)
        session_obj.state_snapshot = safe_json_dumps(snapshot, {})
        db.session.commit()


@pytest.mark.parametrize('in_fight', [True, False], ids=['in-the-fight', 'after-the-fight'])
def test_an_old_saves_dead_hero_is_out_of_the_fight_and_comes_round(party, in_fight):
    party.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    if not in_fight:
        party.fight()
        assert party.snapshot()['combat']['status'] == 'ended'
    clock = party.snapshot()['clock']
    _old_save_dead(party, 'Seren Ashgrove')

    if in_fight:
        party.fight()
    else:
        party.send('Arden Vale', 'Arden Vale looks back down the road.')

    assert party.snapshot()['combat']['status'] == 'ended'
    health = party.hero('Seren Ashgrove')['health']
    assert (health['currentHp'], health['conditions'], health['deathSaves']) == (1, [], {'successes': 0, 'failures': 0})
    assert {'kind': 'condition', 'text': 'Seren Ashgrove comes round'} in party.turns_since(0)[-1]['beats']
    assert party.snapshot()['clock'] == clock


def test_a_created_hero_starts_with_fifteen_gold_and_the_ready_made_hero_keeps_their_own(party):
    assert party.stats('Seren Ashgrove')['gold'] == 15
    assert party.stats('Arden Vale')['gold'] == 25


@pytest.mark.parametrize('heroes', [1, 2], ids=['alone', 'party'])
def test_the_ai_narrator_is_told_to_name_every_hero_only_in_party_play(app, client, socketio, monkeypatch, heroes):
    table = AiTable(app, client, socketio, ai_narration(app, monkeypatch))
    if heroes == 2:
        created = client.post(f"/api/players/campaigns/{table.ids['campaign_id']}/players", json=CLERIC)
        assert created.status_code == 201, created.get_json()

    table.reply('I look around.')
    prompt = table.narrator.prompts[-1]

    party_rule = '6. Name the acting hero and every other hero by name; never write "you" for a hero. No closing question (the opening may end on one). No markdown.'
    solo_rule = '6. Speak to the acting hero as "you". No closing question (the opening may end on one). No markdown.'
    # Arden Vale's own threads follow the acting-hero line (W8-HERO).
    hero = 'Hero: Arden Vale wants to keep a duty and would not abandon Edda Vale (sister).\n'
    if heroes == 2:
        assert party_rule in prompt and solo_rule not in prompt
        assert table.brief().startswith(f'Acting hero: Arden Vale. The party: Arden Vale, Seren Ashgrove.\n{hero}Did:')
    else:
        assert solo_rule in prompt and party_rule not in prompt
        assert table.brief().startswith(f'{hero}Did:')
