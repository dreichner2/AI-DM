"""Typed words that clearly name one action take exactly the path of that action's button."""

from __future__ import annotations

from copy import deepcopy

import pytest
import requests

from aidm_server.database import db
from aidm_server.game_state.extraction.free_text_actions import free_text_action
from aidm_server.models import DmTurn, Session, safe_json_loads


KEY = {'id': 'caravan_strongbox_key', 'name': 'Caravan Strongbox Key', 'type': 'quest', 'quantity': 1, 'playerKnown': True}
COINS = {'id': 'inn_wagon_strongbox_coins', 'name': '15 gp', 'type': 'currency', 'currency': {'gp': 15}, 'playerKnown': True}


def _start(client, app, *, fresh: bool = False) -> tuple[dict, dict]:
    response = client.post('/api/onboarding/play-now', json={'fresh': fresh})
    assert response.status_code == 201, response.get_data(as_text=True)
    ids = {key: response.get_json()[key] for key in ('session_id', 'campaign_id', 'player_id', 'workspace_id')}
    with app.app_context():
        return ids, safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})


@pytest.fixture()
def inn(client, app):
    """The Play Now opening, with the key and a coin pile lying loose and the strongbox in plain view."""

    ids, state = _start(client, app)
    state['currentScene']['items'] = [deepcopy(KEY), deepcopy(COINS)]
    for collection in [state['currentScene'], *(location.get('sceneState') or {} for location in state['locations'])]:
        for entry in collection.get('interactables') or []:
            if entry['id'] == 'inn_wagon_strongbox':
                entry['hidden'] = False
                entry['playerKnown'] = True
    return ids, state


def _map(ids, state, message):
    return free_text_action(state, actor_id=f"player_{ids['player_id']}", player_id=ids['player_id'], message=message)


def _object(target_id: str, action: str) -> tuple[str, str]:
    return ('object', f'{target_id}:{action}')


def _summary(mapped):
    if mapped is None:
        return None
    if mapped.refusal:
        return ('refusal', mapped.refusal)
    if mapped.kind == 'object':
        return ('object', ','.join(f"{intent['object']['id']}:{intent['object']['action']}" for intent in mapped.intents))
    if mapped.kind == 'pickup':
        return ('pickup', ','.join(intent['item']['id'] for intent in mapped.intents))
    if mapped.kind == 'travel':
        return ('travel', mapped.intents[0]['location']['id'])
    if mapped.kind == 'rest':
        return ('rest', mapped.intents[0]['rest_type'])
    if mapped.kind == 'talk':
        return ('talk', mapped.npc['id'])
    return (mapped.kind,)


@pytest.mark.parametrize(
    ('message', 'expected'),
    [
        # Pickups: the named loose item, everything, the coins, and something that is not there.
        ('I take the key', ('pickup', 'caravan_strongbox_key')),
        ('Grab the strongbox key.', ('pickup', 'caravan_strongbox_key')),
        ('pocket the Caravan Strongbox Key', ('pickup', 'caravan_strongbox_key')),
        ('take everything', ('pickup', 'caravan_strongbox_key,inn_wagon_strongbox_coins')),
        ('We loot it all.', ('pickup', 'caravan_strongbox_key,inn_wagon_strongbox_coins')),
        ('take the coins', ('pickup', 'inn_wagon_strongbox_coins')),
        ('I collect the gold', ('pickup', 'inn_wagon_strongbox_coins')),
        ('I take the lantern', ('refusal', 'There is no lantern here to take.')),
        ('take a look around', None),
        # An offered label, said as written or as the start of the message.
        ('Read the caravan ledger', _object('inn_caravan_ledger', 'inspect')),
        ("I search under the driver's bench.", _object('inn_drivers_bench', 'search')),
        ("Search under the driver's bench carefully", _object('inn_drivers_bench', 'search')),
        # Each verb family, with the object's name or a run of its words.
        ('read the ledger', _object('inn_caravan_ledger', 'inspect')),
        ('I study the ledger', _object('inn_caravan_ledger', 'inspect')),
        ("look over the driver's bench", _object('inn_drivers_bench', 'inspect')),
        ('look under the bench', _object('inn_drivers_bench', 'search')),
        ('rummage through the lead wagon', _object('inn_lead_wagon', 'search')),
        ('ring the bell', _object('inn_wagon_bell', 'use')),
        ('pull the wagon bell', _object('inn_wagon_bell', 'use')),
        ('open the strongbox', _object('inn_wagon_strongbox', 'open')),
        ('unlock the strongbox', _object('inn_wagon_strongbox', 'unlock')),
        ('smash the strongbox', _object('inn_wagon_strongbox', 'break')),
        # "force": an unlock that needs a roll when offered, otherwise break.
        ('force the strongbox', _object('inn_wagon_strongbox', 'break')),
        # An action the object does not offer is not mapped; neither is a name two objects share.
        ('open the ledger', None),
        ('search the wagon', None),
        ('touch the moon', None),
        # Travel to an open way onward, rests, looking around, talking.
        ('go to the old north road', ('travel', 'loc_old_north_road')),
        ('Head for the north road.', ('travel', 'loc_old_north_road')),
        ('travel to the waystation', None),
        ('I take a short rest', ('rest', 'short_rest')),
        ('take a breather', ('rest', 'short_rest')),
        ('we make camp', ('rest', 'long_rest')),
        ('I rest until morning', ('rest', 'long_rest')),
        ('look around', ('look',)),
        ('Survey the yard', ('look',)),
        ('Talk to Mara', ('talk', 'npc_mara_reedwake')),
        ('ask Tovin what he saw', ('talk', 'npc_tovin_bell')),
        ('I dance a jig', None),
    ],
)
def test_words_map_to_the_action_they_clearly_name(inn, message, expected):
    ids, state = inn

    assert _summary(_map(ids, state, message)) == expected


@pytest.mark.parametrize(
    ('message', 'more'),
    [
        ('read the ledger and ring the bell', True),
        ('I take the key, then open the strongbox.', True),
        ('read the ledger. Then go to the old north road', True),
        ('read the ledger slowly and carefully', False),
    ],
)
def test_only_the_first_named_action_is_taken(inn, message, more):
    ids, state = inn
    mapped = _map(ids, state, message)

    assert mapped is not None and len(mapped.intents) == 1
    assert mapped.more is more


# The real turn pipeline in Safe Mode ----------------------------------------------------------------------------


@pytest.fixture()
def safe_mode(app, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    # Object checks roll 15; the enemies win initiative and roll 1s.
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr(
        'aidm_server.combat.state._deterministic_initiative_roll',
        lambda _seed, actor_id: 1 if actor_id.startswith('player_') else 20,
    )
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda _sides: 1)


class Hero:
    def __init__(self, app, client, socketio, *, fresh: bool = False):
        self.app = app
        self.ids, _state = _start(client, app, fresh=fresh)
        self.socket = socketio.test_client(app, flask_test_client=app.test_client())
        self.socket.emit(
            'join_session',
            {'session_id': self.ids['session_id'], 'player_id': self.ids['player_id'], 'workspace_id': self.ids['workspace_id']},
        )
        self.socket.get_received()

    def send(self, message: str, intent: dict | None = None) -> str:
        self.socket.emit(
            'send_message',
            {
                'session_id': self.ids['session_id'],
                'campaign_id': self.ids['campaign_id'],
                'player_id': self.ids['player_id'],
                'message': message,
                **({'action_intent': {**intent, 'text': message}} if intent else {}),
            },
        )
        received = self.socket.get_received()
        errors = [event['args'][0] for event in received if event['name'] == 'error']
        assert errors == [], errors
        return next(event['args'][0]['text'] for event in received if event['name'] == 'dm_response_end')

    def turn(self) -> dict:
        with self.app.app_context():
            turn = DmTurn.query.filter_by(session_id=self.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
            return {'requires_roll': turn.requires_roll, 'metadata': safe_json_loads(turn.metadata_json, {})}

    def state(self) -> dict:
        with self.app.app_context():
            snapshot = safe_json_loads(db.session.get(Session, self.ids['session_id']).state_snapshot, {})
        hero = snapshot['playerCharacters'][0]
        scene = snapshot['currentScene']
        return {
            'location': scene['locationId'],
            'sceneItems': sorted(item['id'] for item in scene.get('items') or []),
            'objects': {
                entry['id']: {key: entry.get(key) for key in ('inspected', 'searched', 'open', 'locked', 'used')}
                for entry in scene.get('interactables') or []
            },
            'heroItems': sorted(item['name'] for item in hero['inventory']['items']),
            'gp': hero['inventory']['currency'].get('gp'),
            'hp': hero['health']['currentHp'],
            'flags': {key: value for key, value in snapshot['flags'].items() if not key.startswith('campaignPack')},
            'triggered': snapshot['flags'].get('campaignPackTriggeredEncounterIds'),
            'clues': sorted(clue['id'] for clue in snapshot.get('clues') or []),
            'objectives': {
                objective['id']: objective.get('status')
                for quest in snapshot['quests']
                for objective in quest['objectives']
            },
            'places': {location['id']: location.get('status') for location in snapshot['locations']},
            'checkpoint': snapshot['campaignPack'].get('activeCheckpointId'),
            'combat': (snapshot['combat']['status'], sorted(
                participant['campaignPackEnemyId']
                for participant in snapshot['combat'].get('participants') or []
                if participant['team'] == 'enemy'
            )),
        }


STEPS = [
    ('read the ledger', {'kind': 'object', 'source': 'scene_panel', 'object': {'id': 'inn_caravan_ledger', 'action': 'inspect'}}),
    ("search under the driver's bench", {'kind': 'object', 'source': 'scene_panel', 'object': {'id': 'inn_drivers_bench', 'action': 'search'}}),
    ('I take the key', {
        'kind': 'item', 'source': 'scene_panel', 'inventory_action': 'pick_up',
        'item': {'id': 'caravan_strongbox_key', 'name': 'Caravan Strongbox Key', 'quantity': 1},
    }),
    ('go to the old north road', {'kind': 'travel', 'source': 'scene_panel', 'location': {'id': 'loc_old_north_road', 'name': 'The Old North Road'}}),
]


def test_typed_words_give_the_same_state_as_the_buttons(app, client, socketio, safe_mode):
    typed, words = Hero(app, client, socketio), Hero(app, client, socketio, fresh=True)

    for message, intent in STEPS:
        typed.send(message, intent)
        text = words.send(message)
        turn = words.turn()

        assert words.state() == typed.state(), message
        # The turn records the mapped intent and asks for no roll the engine does not ask for.
        recorded = turn['metadata']['action_intent']
        assert (recorded['kind'], recorded['source']) == (intent['kind'], 'free_text')
        for key in ('object', 'item', 'location'):
            if key in intent:
                assert recorded[key]['id'] == intent[key]['id']
        assert recorded.get('object', {}).get('action') == intent.get('object', {}).get('action')
        assert turn['requires_roll'] is False
        assert 'You do just that.' not in text

    final = words.state()
    assert 'Caravan Strongbox Key' in final['heroItems']
    assert final['location'] == 'loc_old_north_road'
    assert final['combat'][0] == 'active'


def test_the_narration_tells_what_the_words_did(app, client, socketio, safe_mode):
    hero = Hero(app, client, socketio)

    ledger = hero.send('read the ledger and ring the bell')
    again = hero.send('Read the caravan ledger')
    bench = hero.send("search under the driver's bench")
    key = hero.send('I take the key')
    missing = hero.send('I take the lantern')

    assert ledger.startswith("You read the caravan ledger.\n\nA drover's travel ledger")
    assert ledger.endswith('One thing at a time — the rest is still yours to do.')
    assert again == 'You have already examined the Caravan Ledger.'
    assert bench.startswith("You search under the driver's bench.")
    assert bench.endswith('You find a Caravan Strongbox Key.')
    assert key == 'You take the Caravan Strongbox Key.'
    assert missing == 'There is no lantern here to take.'
