"""The Lantern Post Inn, the Play Now adventure's opening chapter (W9-LANTERN-POST), played three ways and lost once.

Through the real socket pipeline in Safe Mode, every action sent as the app sends it: an offered object action or plan
as its label, a fight action from the combat ribbon with the sentence the ribbon shows, a conversation chip as its
label, typed words as typed; after the chapter break the next action, once no fight holds the chapter, begins the next
chapter and is played there. After every step each hero's player view is checked for what it must not show, and every
narration and beat of a route is read with the release smoke's phrase scan.

Seeded dice as in ``tests/test_plans_and_stakes.py``: a hero's d20 shows 15 and their damage dice their highest; checks
the engine rolls for a hero show 15; enemies roll 5 on a d20 and 3 on anything else; heroes win initiative. A route
that needs a failure, a miss or a beating says so where it seeds it. Arden Vale (the ready-made fighter) plays with Seren Ashgrove (a created
cleric), or alone on the inventive route.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

import pytest
import requests

from aidm_server.services.campaign_pack_linter import lint_campaign_pack_file
from tests import test_flagship_adventure as flagship
from tests.scene_yard_pack import YardTable, install, yard_pack
from tests.test_flagship_adventure import table as flagship_table  # noqa: F401 - the flagship's Play Now table fixture
from tests.test_party_play import CLERIC
from tests.test_play_now_release_smoke import _persisted_texts, _streamed_texts, leaks
from tests.test_where_everyone_stands import Table, _errors

PACK_PATH = Path(__file__).resolve().parents[1] / 'docs' / 'examples' / 'the_road_of_unremembered_kings_campaign.json'
PACK = json.loads(PACK_PATH.read_text(encoding='utf-8'))
ARDEN, SEREN = 'Arden Vale', 'Seren Ashgrove'
MARA, TOVIN, OSRIC = 'npc_mara_reedwake', 'npc_tovin_bell', 'npc_osric_hale'
INN, ROAD, WAYSTATION = 'loc_lantern_post_inn', 'loc_old_north_road', 'loc_waystation_scratched_names'
THEFT = 'enc_empty_caravan_pressure'
PEOPLE = {npc['id']: npc for npc in PACK['npcs']}
TOPICS = {npc_id: {topic['id']: topic for topic in npc.get('topics') or []} for npc_id, npc in PEOPLE.items()}
LINES = {npc_id: {topic_id: topic['line'] for topic_id, topic in topics.items()} for npc_id, topics in TOPICS.items()}
OBJECTS = {entry['id']: entry for location in PACK['locations'] for entry in location.get('interactables') or []}
ENCOUNTERS = {encounter['id']: encounter for encounter in PACK['encounters']}
CHAPTER = next(checkpoint for checkpoint in PACK['checkpoints'] if checkpoint['id'] == 'cp_01_empty_caravan')
CALLBACKS = {callback['id']: callback['text'] for npc in PACK['npcs'] for callback in npc.get('callbacks') or []}
CLOCK_STEPS = [step['text'] for record in [*PACK['locations'], *PACK['encounters']] for clock in record.get('clocks') or [] for step in clock['steps']]
STEP_ONE, STEP_TWO, STEP_THREE = CLOCK_STEPS
RESULTS = ENCOUNTERS[THEFT]['results']
TIE_DUTY = next(line['text'] for line in PACK['tieLines'] if line['id'] == 'tie_duty_theft')
ON_DEFEAT = ENCOUNTERS[THEFT]['onDefeat']
# The flags this chapter writes, read in the end states.
CHAPTER_FLAGS = (
    'promised_mara_word', 'mara_trusts_party', 'tovin_button_revealed', 'tovin_leaned_on', 'tovin_named_the_lane', 'watch_posted',
    'osric_yields_wagons', 'strongbox_broken', 'cutthroat_captured', 'theft_resolved', 'fragment_stolen', 'tovin_hurt',
    'lookout_seen', 'team_loose', 'yard_lantern_out', 'carries_maras_lamp', 'tovin_guides', 'yard_chained', 'owes_mara',
    'woke_on_maras_floor',
)
TAKEN_OUT = {'captured', 'unconscious', 'dead', 'escaped', 'withdrawn', 'surrendered', 'fled'}


def _dice(monkeypatch, *, check: int = 15, enemy_d20: int = 5) -> None:
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: check)
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 14 if sides == 20 else sides - 1)
    monkeypatch.setattr('aidm_server.combat.actions.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr(
        'aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda sides: enemy_d20 if sides == 20 else min(sides, 3)
    )
    # The dice hashed from ids (initiative, a parting blow, a hero's second die, saves) are seeded too, so a route plays
    # the same beside another adventure: heroes act first, and those dice show 15 and their damage its highest.
    monkeypatch.setattr('aidm_server.combat.state._deterministic_initiative_roll', lambda _seed, participant_id: 15 if participant_id.startswith('player_') else 5)
    for module in ('aidm_server.combat.attacks', 'aidm_server.combat.pipeline', 'aidm_server.combat.vitality', 'aidm_server.game_state.orchestration.combat_resolution'):
        monkeypatch.setattr(f'{module}.deterministic_roller', _hashed_die)


def _hashed_die(_seed: str):
    return lambda sides: 15 if sides == 20 else sides


class Chapter(Table):
    """The Play Now table played as the app plays it; ``fresh`` starts another adventure beside one already played."""

    def __init__(self, app, client, socketio, *, party: bool, fresh: bool = False):
        self.app = app
        self.client = client
        response = client.post('/api/onboarding/play-now', json={'fresh': True} if fresh else {})
        assert response.status_code == 201, response.get_data(as_text=True)
        self.ids = {key: response.get_json()[key] for key in ('session_id', 'campaign_id', 'player_id', 'workspace_id')}
        self.players = {ARDEN: self.ids['player_id']}
        if party:
            created = client.post(f"/api/players/campaigns/{self.ids['campaign_id']}/players", json=CLERIC)
            assert created.status_code == 201, created.get_json()
            self.players[SEREN] = created.get_json()['player_id']
        self.sockets = {}
        for name, player_id in self.players.items():
            socket_client = socketio.test_client(app, flask_test_client=app.test_client())
            socket_client.emit('join_session', {'session_id': self.ids['session_id'], 'player_id': player_id, 'workspace_id': self.ids['workspace_id']})
            self.sockets[name] = socket_client
        self.drain()
        self.sent = 0
        self.events: list[dict] = []
        self.sessions = [self.ids['session_id']]
        self.steps = 0

    # Sending, as the app does -----------------------------------------------------------------------------------

    def send(self, hero: str, message: str, intent: dict | None = None) -> list[dict]:
        self._begin_next_chapter_if_due()
        self.sent += 1
        message_id = f'lantern-{self.sent}'
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
        self.events.extend(received)
        assert _errors(received) == [], (message, _errors(received))
        self.steps += 1
        self.assert_views_keep_secrets()
        return received

    def _begin_next_chapter_if_due(self) -> None:
        """After a chapter break the next action, once no fight holds the chapter, begins the next chapter there
        (``useStoryReturn.beginNextChapter``)."""

        if not any(beat.get('kind') == 'chapter_end' for beat in self.log_beats()):
            return
        if (self.snapshot().get('combat') or {}).get('status') in {'active', 'starting'}:
            return
        started = self.client.post(f"/api/sessions/{self.ids['session_id']}/next-chapter")
        assert started.status_code == 201, started.get_json()
        self.ids['session_id'] = started.get_json()['session_id']
        self.sessions.append(self.ids['session_id'])
        for name, socket_client in self.sockets.items():
            socket_client.emit('join_session', {'session_id': self.ids['session_id'], 'player_id': self.players[name], 'workspace_id': self.ids['workspace_id']})
        self.drain()

    def talk(self, hero: str, name: str) -> list[dict]:
        """The ``Talk to {Name}`` chip."""

        return self.send(hero, f'I talk to {name}.')

    def say(self, hero: str, words: str) -> list[dict]:
        return self.send(hero, words)

    def chip(self, hero: str, label: str) -> list[dict]:
        """A conversation chip, sent as its label (``talkTopicSubmission``, ``leaveConversationSubmission``)."""

        conversation = self.view(hero)['currentScene']['conversation']
        action = next(action for action in conversation['actions'] if action['label'] == label)
        talk = {'action': 'leave', 'npc_id': conversation['npcId']} if action['kind'] == 'leave_conversation' else {'action': 'topic', 'npc_id': conversation['npcId'], 'topic_id': action['id']}
        return self.send(hero, f'{label}.', {'kind': 'talk', 'source': 'scene_panel', 'talk': talk})

    def labels(self, hero: str) -> list[str]:
        return [action['label'] for action in (self.view(hero)['currentScene'].get('conversation') or {}).get('actions') or []]

    def click(self, hero: str, object_id: str, action: str) -> list[dict]:
        """An offered object action, sent as its label; a failed check retried takes the hero's time
        (``objectActionSubmission``)."""

        entry = next(entry for entry in self.view(hero)['currentScene']['interactables'] if entry['id'] == object_id)
        offer = next(offer for offer in entry['offeredActions'] if offer['action'] == action)
        label = f"{offer['label']} — take your time" if offer.get('retry') else offer['label']
        target = {'id': object_id, 'action': action, **({'revision': entry['revision']} if isinstance(entry.get('revision'), int) else {})}
        return self.send(hero, f'{label}.', {'kind': 'object', 'source': 'scene_panel', 'object': target, **({'take_time': True} if offer.get('retry') else {})})

    def take(self, hero: str, item_id: str) -> list[dict]:
        """A loose item's take chip (``pickupSubmission``)."""

        item = next(item for item in self.view(hero)['currentScene']['items'] if item['id'] == item_id)
        intent = {'kind': 'item', 'source': 'scene_panel', 'inventory_action': 'pick_up', 'item': {'id': item['id'], 'name': item['name'], 'quantity': item['quantity']}}
        return self.send(hero, f"{item['takeLabel']}.", intent)

    def step(self, hero: str, zone_id: str) -> list[dict]:
        """The offered move into another room (``zoneMoveSubmission``)."""

        zone = next(zone for zone in self.view(hero)['currentScene']['zones'] if zone['id'] == zone_id)
        intent = {'kind': 'zone_move', 'source': 'scene_panel', 'zone': {'id': zone_id, 'name': zone['name']}}
        return self.send(hero, f"{zone.get('moveLabel') or 'Go to ' + zone['name']}.", intent)

    def long_rest(self, hero: str) -> list[dict]:
        return self.send(hero, f'{hero} completes a long rest.', {'kind': 'rest', 'source': 'scene_panel', 'rest_type': 'long_rest'})

    def ribbon(self, hero: str, action_id: str, target: str | None = None) -> list[dict]:
        """A fight action from the combat ribbon, with the sentence the ribbon sends (``combatActionSubmission``)."""

        action = next(action for action in self.actions(hero) if action['id'] == action_id)
        assert action['available'], (action_id, action.get('reason'))
        chosen = next((option for option in action.get('targets') or [] if target in {option['name'], option['id']}), None) if target else None
        assert target is None or (chosen and chosen['available']), (action_id, target, action.get('targets'))
        weapon = (action.get('weapon') or {}).get('name') or 'their weapon'
        if chosen is None:
            message = action['message']
        elif action['type'] == 'attack':
            message = f"{hero} {'closes in and attacks' if action.get('requiresMove') else 'attacks'} {chosen['name']} with {weapon}."
        else:
            message = f"{hero}: {action['label']} on {chosen['name']}."
        return self.send(hero, message, {'kind': 'combat', 'source': 'combat_hud', 'combat': {'action_id': action_id, **({'target_id': chosen['id']} if chosen else {})}})

    def end_turn(self, hero: str) -> None:
        if self.fighting() and self.acting_hero() == hero:
            self.ribbon(hero, 'combat.end_turn')

    def take_turn(self, hero: str) -> None:
        """Swing at the first enemy the ribbon's attack can reach (closing in first when it must), else advance on one
        standing off, then end the turn: :meth:`PartyTable.fight` played with the ribbon's own sentences."""

        actions = self.actions(hero)
        attack = next((action for action in actions if action['id'].startswith('combat.attack.') and action['available']), None)
        target = next((target for target in (attack or {}).get('targets') or [] if target['available']), None)
        advance = next((action for action in actions if action['id'] == 'combat.advance' and action['available']), None)
        toward = next((target for target in (advance or {}).get('targets') or [] if target['available']), None)
        if target:
            self.ribbon(hero, attack['id'], target['id'])
        elif toward:
            self.ribbon(hero, 'combat.advance', toward['id'])
        self.end_turn(hero)

    # Reading --------------------------------------------------------------------------------------------------

    def log_beats(self) -> list[dict]:
        response = self.client.get(f"/api/sessions/{self.ids['session_id']}/log?view=player&player_id={self.players[ARDEN]}")
        assert response.status_code == 200, response.get_json()
        return [beat for entry in response.get_json()['entries'] for beat in (entry.get('metadata') or {}).get('storyBeats') or []]

    def told(self) -> str:
        return self.last()['narration']

    def beats(self) -> list[str]:
        return [beat['text'] for beat in self.last()['beats']]

    def flags(self) -> dict[str, Any]:
        return self.snapshot()['flags']

    def person(self, npc_id: str) -> dict:
        return next(npc for npc in self.snapshot()['knownNpcs'] if npc['id'] == npc_id)

    def combat(self) -> dict:
        return self.snapshot().get('combat') or {}

    def fighting(self) -> bool:
        return self.combat().get('status') in {'active', 'starting'}

    def stakes(self) -> dict[str, str]:
        return {stake['id']: stake['state'] for stake in (self.combat().get('flags') or {}).get('stakes') or []}

    def standing(self) -> list[dict]:
        return [
            enemy
            for enemy in self.combat().get('participants') or []
            if enemy['team'] == 'enemy' and not set(enemy.get('conditions') or []) & TAKEN_OUT and enemy['hp']['current'] > 0
        ]

    def runner(self) -> dict | None:
        """The enemy carrying the fragment, else the one the fight named to run, else the first standing."""

        standing = self.standing()
        named = (self.combat().get('flags') or {}).get('runnerId')
        return next((enemy for enemy in standing if enemy.get('carrying')), None) or next((enemy for enemy in standing if enemy['id'] == named), None) or (standing[0] if standing else None)

    def chapter_end(self) -> dict | None:
        return next((beat['chapterEnd'] for beat in self.log_beats() if beat.get('kind') == 'chapter_end'), None)

    # What a player must never read --------------------------------------------------------------------------------

    def assert_views_keep_secrets(self) -> None:
        """Each hero's player view shows no hidden object, no person's secret or untold line, no one not yet met, and
        nothing of what drives the fight or the clocks."""

        snapshot = self.snapshot()
        scene = snapshot.get('currentScene') or {}
        known = {npc['id']: npc for npc in snapshot.get('knownNpcs') or []}
        secrets: list[str] = []
        for entry in [*(scene.get('interactables') or []), *(scene.get('hazards') or [])]:
            if entry.get('hidden') is True and entry.get('playerKnown') is not True:
                authored = OBJECTS.get(entry['id'], entry)
                secrets.extend(text for text in (authored.get('name'), authored.get('description'), authored.get('playerDescription')) if text)
        for npc_id, npc in PEOPLE.items():
            person = known.get(npc_id)
            if person is None:
                secrets.append(npc['name'])
            secrets.extend(text for key in ('secret', 'personality', 'motivation', 'gmNotes') if isinstance(text := npc.get(key), str))
            told = set(((person or {}).get('talk') or {}).get('told') or [])
            secrets.extend(line for topic_id, line in LINES[npc_id].items() if topic_id not in told)
            fired = set(((person or {}).get('talk') or {}).get('callbacks') or [])
            secrets.extend(callback['text'] for callback in npc.get('callbacks') or [] if callback['id'] not in fired)
        secrets.extend(CLOCK_STEPS)
        for hero in self.players:
            view = self.view(hero)
            text = json.dumps(view, ensure_ascii=False)
            shown = [secret for secret in secrets if secret in text]
            assert shown == [], (hero, self.steps, shown)
            fight = (view.get('combat') or {}).get('flags') or {}
            assert not {'stakes', 'enemyGoal', 'secondaryGoal', 'runnerId'} & set(fight), (hero, self.steps)
            assert not {'clocks', 'clocksStarted', 'director'} & set(view), (hero, self.steps)

    def assert_reads_cleanly(self) -> None:
        """The release smoke's phrase scan over everything streamed and persisted in every chapter played."""

        texts = [*_streamed_texts(self.events), *(text for session_id in self.sessions for text in _persisted_texts(self.app, session_id))]
        found = [(label, leak, text) for label, text in texts for leak in leaks(text)]
        assert found == [], found
        assert len([label for label, _text in texts if label.startswith('streamed narration')]) >= 20


@dataclass
class Route:
    """What a route leaves: the end of its chapter, and what its return to the inn told."""

    table: Chapter
    flags: dict[str, Any] = field(default_factory=dict)
    people: dict[str, tuple[int, tuple[str, ...]]] = field(default_factory=dict)
    leads: list[str] = field(default_factory=list)
    items: set[str] = field(default_factory=set)
    facts: list[str] = field(default_factory=list)
    decided: list[str] = field(default_factory=list)
    rounds: int = 0
    road: str = ''
    returned: str = ''


@pytest.fixture()
def safe_mode(app, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    _dice(monkeypatch)


def _carried(table: Chapter) -> set[str]:
    return {item['name'] for hero in table.snapshot()['playerCharacters'] for item in hero['inventory']['items']}


def _people(table: Chapter) -> dict[str, tuple[int, tuple[str, ...]]]:
    """Each inn person's disposition (-2 hostile to 2 loyal) and what they remember of the heroes."""

    from aidm_server.game_state.orchestration.conversations import disposition_value

    return {
        npc_id: (disposition_value(table.person(npc_id).get('disposition')), tuple(entry['key'] for entry in table.person(npc_id).get('remembers') or []))
        for npc_id in (MARA, TOVIN, OSRIC)
    }


def _leave_the_inn(table: Chapter, route: Route, kit: set[str]) -> None:
    """Record the inn as the party leaves it, then set out: the chapter ends on the road, where the pale lanterns wait."""

    route.flags = {key: table.flags().get(key) for key in CHAPTER_FLAGS if table.flags().get(key) is not None}
    route.people = _people(table)
    route.leads = [lead['text'] for lead in table.snapshot().get('leads') or []]
    route.items = _carried(table) - kit
    route.facts = list(table.snapshot()['currentScene'].get('facts') or [])
    table.travel(ARDEN, ROAD, 'The Old North Road')
    route.road = table.told()
    ended = table.chapter_end()
    assert ended is not None and ended['title'] == 'The Empty Caravan'
    route.decided = ended['decided']
    assert table.fighting()
    table.fight()
    assert table.combat()['status'] == 'ended'


def _return_to_the_inn(table: Chapter, route: Route) -> None:
    """The next action begins the next chapter: the party walks back into the Lantern Post."""

    table.travel(ARDEN, INN, 'The Lantern Post Inn')
    assert len(table.sessions) == 2 and table.snapshot()['chapter']['number'] == 2
    route.returned = table.told()


def _fight_until_over(table: Chapter, turn, limit: int = 40) -> None:
    for _step in range(limit):
        if not table.fighting():
            return
        hero = table.acting_hero()
        assert hero, table.combat().get('flags')
        turn(hero)
        table.end_turn(hero)
    raise AssertionError('the fight did not end')


# The sympathetic route --------------------------------------------------------------------------------------------


def _sympathetic(table: Chapter, monkeypatch) -> Route:
    route = Route(table)
    table.talk(ARDEN, 'Mara Reedwake')
    table.chip(ARDEN, 'Ask about the caravan')
    kit = _carried(table)
    # The promise in the player's own words reaches Mara's promise.
    table.say(ARDEN, "You have my word, I'll bring news of them.")
    assert table.told() == LINES[MARA]['mara_promise']
    assert 'A promise made — Promise to bring word of the drivers' in table.beats()
    table.say(ARDEN, 'Who did you lose, Mara?')
    assert table.told() == LINES[MARA]['mara_who_she_lost']
    table.chip(ARDEN, 'Leave it there')
    table.step(ARDEN, 'inn_yard')

    # Seren is gentle with Tovin; the check is seeded to fail, so he keeps the button for now.
    table.talk(SEREN, 'Tovin Bell')
    _dice(monkeypatch, check=5)
    table.chip(SEREN, 'Ask gently what he is hiding')
    assert table.told() == TOPICS[TOVIN]['tovin_gently']['check']['failLine']
    _dice(monkeypatch)

    # The ledger and the bench key bring the watchers' first two steps.
    table.click(ARDEN, 'inn_caravan_ledger', 'inspect')
    assert table.told().endswith(STEP_ONE)
    table.click(ARDEN, 'inn_drivers_bench', 'search')
    assert table.told().endswith(STEP_TWO)
    table.say(SEREN, '"Tovin, who is out in the lane?"')
    assert table.told().endswith(LINES[TOVIN]['tovin_lane'])
    table.talk(ARDEN, 'Osric Hale')
    table.chip(ARDEN, 'Show him what you found')
    table.say(ARDEN, '"Put your watch on the gate tonight."')
    assert table.told() == LINES[OSRIC]['osric_post_watch']
    assert table.flags()['watch_posted'] is True

    # The key opens the strongbox in one search; the theft begins with the watch already at the gate.
    table.click(ARDEN, 'inn_lead_wagon', 'search')
    table.click(ARDEN, 'inn_wagon_strongbox', 'search')
    told = table.told()
    assert ENCOUNTERS[THEFT]['setup'] in told and STEP_THREE not in told
    # Arden wants to keep a duty: his thread is told with the setup.
    assert TIE_DUTY in told
    assert table.stakes() == {'fragment': 'in_place', 'witness': 'safe', 'way_out': 'blocked'}
    assert [enemy['hp']['max'] for enemy in table.standing()] == [14, 14]

    # Seren stands over Tovin every turn; Arden grapples the cutthroat going for the fragment and binds him.
    def turn(hero: str) -> None:
        if hero == SEREN:
            table.say(SEREN, 'I stand over Tovin.')
            return
        target = table.runner()
        held = 'grappled' in (target.get('conditions') or [])
        table.ribbon(ARDEN, 'combat.subdue' if held else 'combat.grapple', target['name'])

    _fight_until_over(table, turn)
    route.rounds = table.combat()['round']
    assert table.snapshot()['stakes'][THEFT] == {'fragment': 'secured', 'witness': 'safe', 'way_out': 'blocked'}
    assert RESULTS[0]['affects'][0]['line'] in table.beats()
    # Tovin was stood over: his callback gives the button to the hero who did it.
    assert ('player_2', 'protected') in {(entry['heroId'], entry['key']) for entry in table.person(TOVIN)['remembers']}
    assert table.person(TOVIN)['talk']['callbacks'] == ['tovin_stood_over_me']
    assert "Silver Driver's Button" in {item['name'] for item in table.hero(SEREN)['inventory']['items']}

    captive = next(entry for entry in table.view(ARDEN)['currentScene']['captives'] if entry['enemyId'] == 'enemy_ashmarked_cutthroats')
    question = captive['offeredActions'][0]['label']
    knows = next(enemy for enemy in PACK['enemies'] if enemy['id'] == 'enemy_ashmarked_cutthroats')['captiveKnows']
    for line in knows:
        table.say(ARDEN, question)
        assert table.told().startswith(f'"{line[:-1]}')
    table.take(ARDEN, 'road_stone_fragment')
    table.talk(ARDEN, 'Mara Reedwake')
    table.chip(ARDEN, 'Ask Mara for a lamp for the road')
    assert table.told().startswith(LINES[MARA]['mara_lamp'])
    table.chip(ARDEN, 'Leave it there')
    table.step(ARDEN, 'inn_yard')

    _leave_the_inn(table, route, kit)
    _return_to_the_inn(table, route)
    return route


def test_the_sympathetic_route_keeps_the_fragment_takes_a_prisoner_and_is_watched_for(app, client, socketio, safe_mode, monkeypatch):
    table = Chapter(app, client, socketio, party=True)
    route = _sympathetic(table, monkeypatch)

    assert route.flags == {
        'promised_mara_word': True,
        'mara_trusts_party': True,
        'tovin_button_revealed': True,
        'tovin_named_the_lane': True,
        'watch_posted': True,
        'cutthroat_captured': True,
        'theft_resolved': True,
        'carries_maras_lamp': True,
    }
    assert route.people == {MARA: (2, ('promised',)), TOVIN: (2, ('protected',)), OSRIC: (1, ())}
    assert route.leads == [RESULTS[0]['lead']]
    assert {'Pale Road-Stone Fragment', "Silver Driver's Button", "Mara's Storm Lamp", 'Ash-Marked Token'} <= route.items
    assert route.decided == ['You gave Mara Reedwake your word.', 'You took the cutthroat alive.', 'You stood over Tovin when the knives came out.']
    # On the road Mara's lamp keeps the pale lights back; back at the inn she looks for the drivers behind the party.
    assert ENCOUNTERS['enc_pale_lantern_detour']['adjustments'][0]['text'] in route.road
    assert route.returned.startswith(CALLBACKS['mara_watches_the_door'])
    assert route.rounds == 3
    table.assert_reads_cleanly()
    table.close()


# The forceful route -----------------------------------------------------------------------------------------------


def _forceful(table: Chapter, monkeypatch) -> Route:
    route = Route(table)
    table.talk(ARDEN, 'Tovin Bell')
    kit = _carried(table)
    table.say(ARDEN, 'I lean on him until he talks.')
    assert table.told().startswith(LINES[TOVIN]['tovin_lean'])
    table.click(ARDEN, 'inn_lead_wagon', 'search')
    assert table.told().endswith(STEP_ONE)

    # The first try at the lid is seeded to fail: it fails forward, and Osric cools.
    _dice(monkeypatch, check=2)
    table.click(ARDEN, 'inn_wagon_strongbox', 'break')
    assert table.told().endswith(OBJECTS['inn_wagon_strongbox']['onFail']['break']['text'])
    assert table.person(OSRIC)['disposition'] == -2
    _dice(monkeypatch)
    table.click(ARDEN, 'inn_wagon_strongbox', 'break')
    assert table.flags()['strongbox_broken'] is True

    # Weapons only, and every swing and parting blow misses: the runner is seeded to get away.
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 0)
    monkeypatch.setattr('aidm_server.combat.attacks.deterministic_roller', lambda _seed: lambda _sides: 1)
    table.click(ARDEN, 'inn_wagon_strongbox', 'search')
    assert table.stakes() == {'fragment': 'in_place', 'way_out': 'open'}

    def turn(hero: str) -> None:
        runner = table.runner()
        others = [enemy for enemy in table.standing() if enemy['id'] != runner['id']]
        if others:
            attack = next(action for action in table.actions(hero) if action['id'].startswith('combat.attack.'))
            table.ribbon(hero, attack['id'], others[0]['name'])

    _fight_until_over(table, turn)
    route.rounds = table.combat()['round']
    assert table.combat()['flags']['endReason'] == 'enemy_goal_met'
    assert table.told().endswith(RESULTS[2]['text'])
    _dice(monkeypatch)
    _leave_the_inn(table, route, kit)

    # The next chapter: at the waystation one of the agents is the man from the yard, and he still has it.
    table.travel(ARDEN, WAYSTATION, 'Waystation of Scratched Names')
    table.click(ARDEN, 'ws_memorial_wall', 'search')
    ambush = table.told()
    assert ENCOUNTERS['enc_waystation_ambush']['setup'] in ambush
    assert ambush.index(ENCOUNTERS['enc_waystation_ambush']['setup']) < ambush.index('One of them is the man from the yard.')
    table.fight()
    assert table.told().endswith(ENCOUNTERS['enc_waystation_ambush']['results'][0]['text'])
    assert 'Pale Road-Stone Fragment' in _carried(table)
    assert (table.flags()['fragment_recovered'], table.flags()['fragment_stolen']) == (True, False)
    table.travel(ARDEN, ROAD, 'The Old North Road')
    table.travel(ARDEN, INN, 'The Lantern Post Inn')
    route.returned = table.told()
    return route


def test_the_forceful_route_loses_the_fragment_north_and_gets_it_back_at_the_waystation(app, client, socketio, safe_mode, monkeypatch):
    table = Chapter(app, client, socketio, party=True)
    route = _forceful(table, monkeypatch)

    assert route.flags == {
        'tovin_button_revealed': True,
        'tovin_leaned_on': True,
        'strongbox_broken': True,
        'theft_resolved': True,
        'fragment_stolen': True,
    }
    # Osric is hostile, Mara cool, Tovin wary; nobody remembers the forceful hero by name.
    assert route.people == {MARA: (-1, ()), TOVIN: (-1, ()), OSRIC: (-2, ())}
    assert route.leads == [RESULTS[2]['lead']]
    assert route.items == {"Silver Driver's Button"}
    assert route.facts == []
    assert route.decided == ['You leaned on the stable-boy until he gave it up.', "You broke the strongbox against the reeve's word.", 'The fragment went north without you.']
    assert route.rounds == 3
    # Back at the inn nobody greets them: no callback is due (Osric's for a hostile reeve needs a memory to hang on).
    assert not any(text in route.returned for text in CALLBACKS.values())
    table.assert_reads_cleanly()
    table.close()


# The inventive route ----------------------------------------------------------------------------------------------


def _inventive(table: Chapter, monkeypatch, *, keep_the_claim: bool) -> Route:
    route = Route(table)
    table.talk(ARDEN, 'Osric Hale')
    kit = _carried(table)
    table.say(ARDEN, 'I was sent by the wardens of the road.')
    assert table.told() == PEOPLE[OSRIC]['reacts'][0]['line']
    assert table.flags()['osric_yields_wagons'] is True
    table.click(ARDEN, 'inn_drivers_bench', 'search')
    table.click(ARDEN, 'inn_lead_wagon', 'search')
    table.click(ARDEN, 'inn_wagon_strongbox', 'search')
    assert table.stakes() == {'fragment': 'in_place', 'way_out': 'open'}

    # Every plan by typed words. The gate is barred first: the watch the bell is meant to bring cannot be written yet,
    # and without a way out held the runner is gone before the canvas comes down.
    plans = [
        ('I slam the gate shut and drop the bar.', OBJECTS['inn_yard_gate']['affordances'][0]),
        ('I ring the bell for the watch!', OBJECTS['inn_wagon_bell']['affordances'][0]),
        ('I throw the lantern into the hedge.', OBJECTS['inn_yard_lantern']['affordances'][0]),
        ('Cut the horses loose!', OBJECTS['inn_wagon_harness']['affordances'][0]),
        ('I pull the canvas down on him.', OBJECTS['inn_wagon_canvas']['affordances'][0]),
    ]
    for words, affordance in plans:
        assert table.acting_hero() == ARDEN
        table.say(ARDEN, words)
        effect = next(change for change in table.last()['pipeline']['combatAppliedChanges'] if change['type'] == 'scene.effect')
        assert (effect['affordanceId'], effect['success']) == (affordance['id'], True)
        if affordance.get('result', {}).get('text'):
            assert table.told().startswith(affordance['result']['text'])
        table.end_turn(ARDEN)
    runner = table.runner()
    assert 'restrained' in runner['conditions'] and runner.get('carrying')
    assert next(entry for entry in table.snapshot()['currentScene']['interactables'] if entry['id'] == 'inn_lane_horses')['hidden'] is False

    # The canvas holds him where he is, but a Bind takes a grapple: put the other down, close in, grapple, bind.
    def turn(hero: str) -> None:
        runner = table.runner()
        others = [enemy for enemy in table.standing() if enemy['id'] != runner['id']]
        actions = {action['id']: action for action in table.actions(hero)}
        if others:
            attack = next(action for action in actions.values() if action['id'].startswith('combat.attack.'))
            table.ribbon(hero, attack['id'], others[0]['name'])
        elif 'grappled' in (runner.get('conditions') or []):
            table.ribbon(hero, 'combat.subdue', runner['name'])
        else:
            def reachable(action_id: str) -> bool:
                action = next(action for action in table.actions(hero) if action['id'] == action_id)
                return action['available'] and any(target['id'] == runner['id'] and target['available'] for target in action.get('targets') or [])

            if not reachable('combat.grapple') and reachable('combat.advance'):
                table.ribbon(hero, 'combat.advance', runner['name'])
            if reachable('combat.grapple'):
                table.ribbon(hero, 'combat.grapple', runner['name'])

    _fight_until_over(table, turn)
    route.rounds = table.combat()['round']
    assert table.snapshot()['stakes'][THEFT] == {'fragment': 'secured', 'way_out': 'blocked'}
    table.take(ARDEN, 'road_stone_fragment')
    _leave_the_inn(table, route, kit)
    _return_to_the_inn(table, route)

    # The rider is waiting for the report Osric was promised; Arden hears Osric out, and the rider's two ways show.
    table.talk(ARDEN, 'Osric Hale')
    while not {'Admit you were never sent', 'Keep up the claim'} <= set(table.labels(ARDEN)):
        table.chip(ARDEN, next(label for label in table.labels(ARDEN) if label.startswith('Ask about')))
    if keep_the_claim:
        table.chip(ARDEN, 'Keep up the claim')
        assert table.told() == LINES[OSRIC]['osric_keep_claim']
        assert table.flags()['named_in_wardens_report'] is True
    else:
        table.chip(ARDEN, 'Admit you were never sent')
        assert table.told() == LINES[OSRIC]['osric_admit']
        assert [entry['key'] for entry in table.person(OSRIC)['remembers']] == ['owned_the_lie']
    return route


@pytest.mark.parametrize('keep_the_claim', [False, True], ids=['owns-the-lie', 'keeps-the-claim'])
def test_the_inventive_route_turns_the_yard_against_the_thieves_and_the_rider_comes_for_the_claim(
    app, client, socketio, safe_mode, monkeypatch, keep_the_claim
):
    table = Chapter(app, client, socketio, party=False)
    route = _inventive(table, monkeypatch, keep_the_claim=keep_the_claim)

    assert route.flags == {
        'osric_yields_wagons': True,
        'cutthroat_captured': True,
        'theft_resolved': True,
        'lookout_seen': True,
        'team_loose': True,
    }
    assert route.people == {MARA: (0, ()), TOVIN: (0, ()), OSRIC: (1, ('claimed_authority',))}
    assert route.leads == [RESULTS[0]['lead']]
    assert {'Pale Road-Stone Fragment', 'Ash-Marked Token'} <= route.items and "Silver Driver's Button" not in route.items
    # The loose team's fact is a separate's, which passes with the fight; the barred gate and the burning hedge stay.
    assert route.facts == ['The yard gate is barred.', 'The hedge is burning; the lane is lit.']
    assert route.decided == ['You claimed an authority you do not hold.', 'You took the cutthroat alive.', "You turned the settlement's horses loose."]
    assert route.returned.startswith(CALLBACKS['osric_the_rider'])
    assert route.rounds == 9
    table.assert_reads_cleanly()
    table.close()


# Beaten -------------------------------------------------------------------------------------------------------------


def test_beaten_in_the_yard_the_party_wakes_on_maras_floor_and_the_story_goes_on(app, client, socketio, safe_mode, monkeypatch):
    table = Chapter(app, client, socketio, party=True)
    table.click(ARDEN, 'inn_drivers_bench', 'search')
    table.click(ARDEN, 'inn_lead_wagon', 'search')
    table.click(ARDEN, 'inn_wagon_strongbox', 'search')
    # Arden snatches the fragment up; the cutthroats then hit every time and as hard as they can.
    table.say(table.acting_hero(), 'I grab the road-stone fragment.')
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda sides: sides)
    _fight_until_over(table, lambda _hero: None)
    rounds = table.combat()['round']

    snapshot = table.snapshot()
    assert snapshot['combat']['flags']['endReason'] == 'objective_failed'
    assert table.told().endswith(ON_DEFEAT['text'])
    assert (table.zone(ARDEN), table.zone(SEREN)) == ('inn_common_room', 'inn_common_room')
    assert {key: snapshot['flags'].get(key) for key in ON_DEFEAT['setsFlags']} == ON_DEFEAT['setsFlags']
    assert snapshot['stakes'][THEFT]['fragment'] == 'taken'
    assert THEFT in snapshot['flags']['campaignPackCompletedEncounterIds']
    assert rounds == 4
    _dice(monkeypatch)

    # Mara names her price; Osric opens the yard (the check seeded to pass through their bruises).
    table.talk(ARDEN, 'Mara Reedwake')
    table.say(ARDEN, 'What do I owe you, Mara?')
    assert table.told() == LINES[MARA]['mara_owe']
    assert (table.flags()['owes_mara'], table.flags()['promised_mara_word']) == (False, True)
    _dice(monkeypatch, check=20)
    table.step(SEREN, 'inn_yard')
    table.say(SEREN, '"Osric, open the yard."')
    assert table.told().endswith(LINES[OSRIC]['osric_open_yard'])
    assert table.flags()['yard_chained'] is False
    _dice(monkeypatch)

    # A night's rest, and on to the road: the chapter ends, and the theft does not come again.
    table.long_rest(ARDEN)
    table.step(ARDEN, 'inn_yard')
    table.travel(ARDEN, ROAD, 'The Old North Road')
    assert table.chapter_end()['decided'] == ['You gave Mara Reedwake your word.', 'The fragment went north without you.', "You woke on Mara's floor, in her debt."]
    table.fight()
    table.travel(ARDEN, INN, 'The Lantern Post Inn')
    assert not table.fighting()
    assert table.snapshot()['flags']['campaignPackTriggeredEncounterIds'].count(THEFT) == 1
    table.assert_reads_cleanly()
    table.close()


# The three routes against one another -----------------------------------------------------------------------------


def test_the_three_routes_leave_three_different_inns_and_each_return_is_its_own(app, client, socketio, safe_mode, monkeypatch):
    sympathetic = _sympathetic(Chapter(app, client, socketio, party=True), monkeypatch)
    forceful = _forceful(Chapter(app, client, socketio, party=True, fresh=True), monkeypatch)
    inventive = _inventive(Chapter(app, client, socketio, party=False, fresh=True), monkeypatch, keep_the_claim=False)
    routes = {'sympathetic': sympathetic, 'forceful': forceful, 'inventive': inventive}

    pairs = [('sympathetic', 'forceful'), ('sympathetic', 'inventive'), ('forceful', 'inventive')]
    for one, two in pairs:
        first, second = routes[one], routes[two]
        assert first.flags != second.flags, (one, two)
        assert first.items != second.items, (one, two)
        for npc_id in (MARA, TOVIN, OSRIC):
            assert first.people[npc_id] != second.people[npc_id], (one, two, npc_id)
    # The lantern's lookout lead and the watch's fact at the gate are not written (no lead from a plan, no fact from a
    # topic), so the sympathetic and inventive routes share their lead and the sympathetic and forceful leave no fact.
    assert sympathetic.leads != forceful.leads != inventive.leads
    assert inventive.facts not in (sympathetic.facts, forceful.facts)

    # Each return to the inn tells only its own route's line.
    lines = {'sympathetic': CALLBACKS['mara_watches_the_door'], 'inventive': CALLBACKS['osric_the_rider']}
    for name, route in routes.items():
        assert [owner for owner, line in lines.items() if line in route.returned] == ([name] if name in lines else [])
    for route in routes.values():
        route.table.close()


def test_the_pack_lints_strictly_clean():
    result = lint_campaign_pack_file(PACK_PATH, workspace_id='owner', strict=True)

    assert result['issues'] == []


def test_the_flagship_route_still_completes(flagship_table):  # noqa: F811 - the fixture imported above
    flagship.test_the_careful_route_renews_the_ward(flagship_table)


# The two engine additions, on the coaching yard ---------------------------------------------------------------------


def _yard(app, client, socketio, monkeypatch, pack: dict, *, party: bool) -> YardTable:
    install(monkeypatch, pack)
    table = YardTable(app, client, socketio, party=party)
    # Wenna is met with the first thing the hero does.
    table.act(ARDEN, 'feed_crate', 'inspect')
    return table


def test_a_plans_result_gives_its_items_to_the_hero_who_carried_it_out(app, client, socketio, safe_mode, monkeypatch):
    pack = yard_pack()
    pack['locations'][0]['interactables'][0]['affordances'][0]['result']['givesItems'] = [{'id': 'gate_bar', 'name': 'Gate Bar', 'type': 'gear'}]
    table = _yard(app, client, socketio, monkeypatch, pack, party=True)

    table.affordance(SEREN, 'yard_gate', 'bar_gate', 'Shut and bar the yard gate')

    given = [item for item in table.hero(SEREN)['inventory']['items'] if item['id'] == 'gate_bar']
    assert [(item['name'], item['quantity']) for item in given] == [('Gate Bar', 1)]
    assert "Seren Ashgrove received the Gate Bar" in [beat['text'] for beat in table.last()['beats']]
    assert 'gate_bar' not in {item['id'] for item in table.hero(ARDEN)['inventory']['items']}
    table.close()


def test_a_way_out_started_blocked_holds_until_an_enemy_clears_it(app, client, socketio, safe_mode, monkeypatch):
    pack = yard_pack()
    pack['encounters'][0]['stakes'][2]['startWhen'] = [{'flags': {'gate_watched': True}, 'state': 'blocked'}]
    pack['startingState']['flags'] = {'gate_watched': True}
    table = _yard(app, client, socketio, monkeypatch, pack, party=False)

    table.act(ARDEN, 'mail_wagon', 'search')
    assert table.stakes()['way_out'] == 'blocked'
    # The hold is the fight's own: the gate itself was never barred.
    assert 'blocked' not in next(entry for entry in table.snapshot()['currentScene']['interactables'] if entry['id'] == 'yard_gate')
    for _step in range(12):
        if 'heaves at the yard gate, and it holds' in table.last()['narration']:
            break
        table.hud(ARDEN, 'End turn.', 'combat.end_turn')
    assert table.stakes()['way_out'] == 'blocked'

    _dice(monkeypatch, enemy_d20=20)
    for _step in range(4):
        if 'wrenches the yard gate open' in table.last()['narration']:
            break
        table.hud(ARDEN, 'End turn.', 'combat.end_turn')
    assert 'wrenches the yard gate open' in table.last()['narration']
    assert table.stakes()['way_out'] == 'open'
    table.close()


def test_start_when_sets_a_stake_only_while_its_flags_hold():
    from aidm_server.game_state.orchestration.fight_stakes import initial_stakes

    encounter = yard_pack()['encounters'][0]
    encounter['stakes'][2]['startWhen'] = [{'flags': {'gate_watched': True}, 'state': 'blocked'}]
    state = {'flags': {}, 'currentScene': {'interactables': [{'id': 'yard_gate'}], 'items': []}}

    assert {stake['id']: (stake['state'], stake.get('held')) for stake in initial_stakes(state, encounter)} == {
        'fragment': ('in_place', None),
        'witness': ('safe', None),
        'way_out': ('open', None),
    }
    watched = initial_stakes({**state, 'flags': {'gate_watched': True}}, encounter)
    assert [(stake['state'], stake.get('held')) for stake in watched if stake['id'] == 'way_out'] == [('blocked', True)]


def _yard_lint(pack: dict) -> list[tuple[str, str, str]]:
    from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest

    return [(issue['severity'], issue['code'], issue['path']) for issue in lint_campaign_pack_manifest(pack, workspace_id='owner', strict=True)['issues']]


@pytest.mark.parametrize(
    ('change', 'expected'),
    [
        (
            lambda pack: pack['encounters'][0]['stakes'][2].update(startWhen=[{'flags': {'gate_watched': True}, 'state': 'held'}]),
            ('error', 'invalid_stake_state', 'encounters[0].stakes[2].startWhen[0].state'),
        ),
        (
            lambda pack: pack['encounters'][0]['stakes'][1].update(startWhen=[{'state': 'safe'}]),
            ('error', 'invalid_stake_state', 'encounters[0].stakes[1].startWhen'),
        ),
    ],
    ids=['route state', 'person'],
)
def test_the_linter_reports_a_start_when_the_engine_cannot_use(app, change, expected):
    pack = yard_pack()
    change(pack)

    with app.app_context():
        assert expected in _yard_lint(pack)


def test_flags_and_items_a_fight_or_a_plan_gives_are_the_packs_own(app):
    pack = yard_pack()
    # A fight's result sets ``fragment_lost``; a plan gives the gate bar that Wenna's topic asks for.
    pack['encounters'][0]['adjustments'] = [{'when': {'flagKey': 'fragment_lost', 'equals': True}, 'text': 'They have done this before.'}]
    pack['locations'][0]['interactables'][0]['affordances'][0]['result']['givesItems'] = [{'id': 'gate_bar', 'name': 'Gate Bar'}]
    pack['npcs'][0]['topics'][0]['requires'] = {'itemIds': ['gate_bar']}

    with app.app_context():
        assert _yard_lint(pack) == []
