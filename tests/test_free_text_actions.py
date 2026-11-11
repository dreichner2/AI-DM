"""Typed words that clearly name one action take exactly the path of that action's button."""

from __future__ import annotations

from copy import deepcopy

import pytest
import requests

from aidm_server.database import db
from aidm_server.game_state.extraction.free_text_actions import free_text_action
from aidm_server.models import DmTurn, Session, safe_json_loads
from tests.test_search_and_take import leave_where_it_lies


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
        # A lead-in before the verb: "take a look around" is looking around.
        ('take a look around', ('look',)),
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


def _deferred(mapped) -> list[str]:
    return [action['label'] for action in mapped.deferred] if mapped is not None else []


BENCH_SEARCH = _object('inn_drivers_bench', 'search')
NORTH_ROAD = ('travel', 'loc_old_north_road')


@pytest.mark.parametrize(
    ('message', 'expected', 'deferred'),
    [
        # The lead's sentences from the first live session.
        ("I crouch and search under the driver's bench.", BENCH_SEARCH, []),
        ('I pocket the key and climb into the lead wagon to search it.', ('pickup', 'caravan_strongbox_key'), ['Search the lead wagon']),
        ('I head up the old north road.', NORTH_ROAD, []),
        ('I find a chest with 500 gold.', None, []),
        # A lead-in before the verb, a purpose phrase, "it" for the thing named before it.
        ('Carefully, I open the strongbox.', _object('inn_wagon_strongbox', 'open'), []),
        ('I walk over to the bench and look under it.', BENCH_SEARCH, []),
        ('I climb into the lead wagon to search it.', _object('inn_lead_wagon', 'search'), []),
        ('I hurry across the muddy yard to the lead wagon and search it.', _object('inn_lead_wagon', 'search'), []),
        ('I step up to the wagon bell and pull it.', _object('inn_wagon_bell', 'use'), []),
        ('Let me read the caravan ledger.', _object('inn_caravan_ledger', 'inspect'), []),
        ('I kneel down and examine the ledger.', _object('inn_caravan_ledger', 'inspect'), []),
        ('Then I ring the wagon bell.', _object('inn_wagon_bell', 'use'), []),
        # Pickups, the new verbs among them.
        ('Quickly, I grab the strongbox key.', ('pickup', 'caravan_strongbox_key'), []),
        ('I scoop up the coins.', ('pickup', 'inn_wagon_strongbox_coins'), []),
        ('I help myself to the gold.', ('pickup', 'inn_wagon_strongbox_coins'), []),
        # Travel: any travel verb, a preposition, a way onward by name.
        ('I make my way up the old north road.', NORTH_ROAD, []),
        ('We set off along the north road.', NORTH_ROAD, []),
        ('I take the old north road.', NORTH_ROAD, []),
        ('I leave for the old north road.', NORTH_ROAD, []),
        ('I head north.', NORTH_ROAD, []),
        ('I follow the old north road', NORTH_ROAD, []),
        ('I start down the old north road.', NORTH_ROAD, []),
        ('We march out along the old north road.', NORTH_ROAD, []),
        # The first named action is taken; every later one is deferred by its label.
        ('I take the key, then I open the strongbox.', ('pickup', 'caravan_strongbox_key'), ['Open the strongbox']),
        ('I read the ledger; then I ring the bell.', _object('inn_caravan_ledger', 'inspect'), ['Ring the wagon bell']),
        (
            "I search under the driver's bench, then head up the old north road.",
            BENCH_SEARCH,
            ['Go to The Old North Road'],
        ),
        ('I look around and then take the key.', ('look',), ['Take the Caravan Strongbox Key']),
        ('I ask Tovin about the bell and ring it.', ('talk', 'npc_tovin_bell'), ['Ring the wagon bell']),
        ('I scoop up the coins and the key, then take a short rest.', ('pickup', 'inn_wagon_strongbox_coins'), ['Take a short rest']),
        # Only the first two sentences are read.
        ('I wait. I listen. I open the strongbox.', None, []),
    ],
)
def test_ordinary_sentences_map_clause_by_clause(inn, message, expected, deferred):
    ids, state = inn
    mapped = _map(ids, state, message)

    assert _summary(mapped) == expected
    assert _deferred(mapped) == deferred


@pytest.mark.parametrize(
    'message',
    [
        # Negated or hypothetical.
        "I don't open the strongbox.",
        "I won't search under the driver's bench",
        'I never touch the bell.',
        'I search the bench without touching anything.',
        'I would open the strongbox.',
        'Before I leave, I search under the bench.',
        'If I open the strongbox, what happens?',
        # Questions.
        'Should I open the strongbox?',
        'Can I take the key?',
        'Could I read the ledger?',
        'I open the strongbox?',
        # Speech: the verb inside quotation marks.
        'I shout, "Open the strongbox!"',
        '"Search the lead wagon," I tell Mara.',
        'I say “ring the bell” to no one.',
        # Someone else's verb.
        'I tell Mara to search the lead wagon.',
        'I watch Tovin ring the bell.',
        'I let Osric open the strongbox.',
        'Beside Mara, I open the strongbox.',
    ],
)
def test_negated_hypothetical_spoken_and_others_words_map_nothing(inn, message):
    ids, state = inn

    assert _map(ids, state, message) is None


def test_it_is_the_thing_named_before_it_else_what_the_previous_turn_acted_on(inn):
    ids, state = inn

    def mapped(message, previous=None):
        return _summary(
            free_text_action(
                state, actor_id=f"player_{ids['player_id']}", player_id=ids['player_id'], message=message, previous_target=previous
            )
        )

    assert mapped('I search it.') is None
    assert mapped('I search it.', ('object', 'inn_lead_wagon')) == _object('inn_lead_wagon', 'search')
    assert mapped('I look over it.', ('object', 'inn_drivers_bench')) == _object('inn_drivers_bench', 'inspect')
    assert mapped('I pocket it.', ('item', 'caravan_strongbox_key')) == ('pickup', 'caravan_strongbox_key')
    # Named in the message wins over the previous turn; a thing of another kind maps nothing.
    assert mapped('I study the ledger and then search it.', ('object', 'inn_lead_wagon')) == _object('inn_caravan_ledger', 'inspect')
    assert mapped('I look at the bench and take it.') == _object('inn_drivers_bench', 'inspect')
    assert mapped('I take it.', ('object', 'inn_lead_wagon')) is None
    # Something the previous turn acted on that is no longer here.
    assert mapped('I pocket it.', ('item', 'not_here')) is None


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
    # The bench's search leaves the key lying there, so the next step takes it.
    for hero in (typed, words):
        leave_where_it_lies(app, hero.ids['session_id'], 'caravan_strongbox_key')

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
    leave_where_it_lies(app, hero.ids['session_id'], 'caravan_strongbox_key')

    ledger = hero.send('read the ledger and ring the bell')
    again = hero.send('Read the caravan ledger')
    bench = hero.send("search under the driver's bench")
    key = hero.send('I take the key')
    missing = hero.send('I take the lantern')

    assert ledger.startswith("You read the caravan ledger.\n\nA drover's travel ledger")
    assert ledger.endswith('One thing at a time — the wagon bell is still there to ring.')
    assert again == 'You have already examined the Caravan Ledger.'
    assert bench.startswith("You search under the driver's bench.")
    # The key found, then the watchers' second step (the Lantern Post chapter).
    assert bench.endswith('You find a Caravan Strongbox Key.\n\nTovin has gone very still. "There\'s someone in the lane," he says, without moving his lips.')
    assert key == 'You take the Caravan Strongbox Key.'
    assert missing == 'There is no lantern here to take.'


def _pipeline(hero: Hero) -> dict:
    return hero.turn()['metadata']['state_pipeline']


def test_a_sentence_with_a_lead_in_searches_the_object_through_the_socket(app, client, socketio, safe_mode):
    hero = Hero(app, client, socketio)

    text = hero.send("I crouch and search under the driver's bench.")
    turn = hero.turn()

    assert hero.state()['objects']['inn_drivers_bench']['searched'] is True
    # The search takes what it finds.
    assert 'Caravan Strongbox Key' in hero.state()['heroItems']
    recorded = turn['metadata']['action_intent']['object']
    assert (recorded['id'], recorded['action']) == ('inn_drivers_bench', 'search')
    assert turn['requires_roll'] is False
    assert text.startswith("You search under the driver's bench.")
    # "it" in the next turn is what this turn acted on.
    hero.send('I look over it.')
    assert hero.state()['objects']['inn_drivers_bench']['inspected'] is True


def test_a_pickup_is_taken_and_the_search_after_it_deferred_through_the_socket(app, client, socketio, safe_mode):
    hero = Hero(app, client, socketio)
    leave_where_it_lies(app, hero.ids['session_id'], 'caravan_strongbox_key')
    hero.send("search under the driver's bench")

    text = hero.send('I pocket the key and climb into the lead wagon to search it.')
    turn = hero.turn()
    deferred = [{'kind': 'object', 'label': 'Search the lead wagon', 'target': 'the lead wagon', 'verb': 'search'}]

    assert 'Caravan Strongbox Key' in hero.state()['heroItems']
    assert hero.state()['objects']['inn_lead_wagon']['searched'] is not True
    assert turn['metadata']['free_text_action']['deferred'] == deferred
    assert _pipeline(hero)['dmContextPacket']['narration']['freeText']['deferred'] == deferred
    assert text == 'You take the Caravan Strongbox Key.\n\nOne thing at a time — the lead wagon is still there to search.'


def test_a_travel_sentence_moves_the_party_through_the_socket(app, client, socketio, safe_mode):
    hero = Hero(app, client, socketio)

    hero.send('I head up the old north road.')
    turn = hero.turn()

    assert hero.state()['location'] == 'loc_old_north_road'
    assert turn['metadata']['action_intent']['location']['id'] == 'loc_old_north_road'
    assert turn['requires_roll'] is False
