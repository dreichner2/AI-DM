"""Story beats: what changed in a turn, as the quiet lines under the Dungeon Master's reply."""

from __future__ import annotations

from copy import deepcopy

import pytest
import requests

from aidm_server.game_state.orchestration.story_beats import HINT_TEXT, story_beats


ACTOR = 'player_1'


def _basis(**overrides) -> dict:
    basis = {
        'heroes': {
            ACTOR: {'name': 'Arden Vale', 'hp': 12, 'conditions': [], 'items': {'Torch': 5}, 'currency': {'gp': 25}, 'xp': 250, 'level': 1},
            'player_2': {'name': 'Mara Fen', 'hp': 9, 'conditions': [], 'items': {}, 'currency': {}, 'xp': 0, 'level': 1},
        },
        'sceneItems': ['Caravan Strongbox Key'],
        'clues': {},
        'objectives': {'obj_one': {'title': 'Investigate the empty caravan', 'status': 'active'}},
        'places': {'loc_inn': 'The Lantern Post Inn'},
        'ending': '',
    }
    basis.update(overrides)
    return basis


def _texts(beats: list[dict]) -> list[tuple[str, str]]:
    return [(beat['kind'], beat['text']) for beat in beats]


def test_every_kind_of_change_is_one_beat_in_contract_order():
    before = _basis()
    after = deepcopy(before)
    arden, mara = after['heroes'][ACTOR], after['heroes']['player_2']
    arden.update(hp=5, conditions=['prone'], items={'Torch': 4, 'Caravan Strongbox Key': 1, 'Ash-Marked Token': 2},
                 currency={'gp': 40}, xp=350, level=2)
    mara.update(hp=11, conditions=['frightened'])
    after['sceneItems'] = []
    after['clues'] = {'clue_ash_marks': 'Ash Marks on the Road'}
    after['objectives'] = {
        'obj_one': {'title': 'Investigate the empty caravan', 'status': 'completed'},
        'obj_two': {'title': 'Follow the changing road', 'status': 'active'},
    }
    after['places'] = {**before['places'], 'loc_road': 'The Old North Road'}
    after['ending'] = 'The Road Remembers'
    applied = [
        {'type': 'scene.interactable.action', 'targetId': 'inn_drivers_bench', 'objectAction': 'search',
         'event': {'check': {'skill': 'investigation', 'ability': 'intelligence', 'total': 14, 'dc': 10, 'outcome': 'success'}}},
        {'type': 'combat.death_save', 'participantId': 'player_2', 'participantName': 'Mara Fen', 'roll': 12, 'result': 'success'},
        {'type': 'combat.end', 'endReason': 'all_enemies_defeated'},
        {'type': 'combat.defeat_recovery'},
    ]

    beats = story_beats(before, after, actor_id=ACTOR, state_before={}, applied_changes=applied)

    assert _texts(beats) == [
        ('check', 'Investigation check: 14 — success'),
        ('hp', 'Took 7 damage'),
        ('hp', 'Mara Fen healed 2'),
        ('condition', 'You are prone'),
        ('condition', 'Mara Fen is frightened'),
        ('death_save', 'Mara Fen: death save: 12 — success'),
        ('combat_end', 'Victory'),
        ('item_gained', 'Taken — Caravan Strongbox Key'),
        ('item_gained', 'Received — Ash-Marked Token ×2'),
        ('item_lost', 'Used — Torch'),
        ('coins', '+15 gp'),
        ('clue', 'Clue — Ash Marks on the Road'),
        ('objective_complete', 'Objective complete — Investigate the empty caravan'),
        ('objective_new', 'New objective — Follow the changing road'),
        ('place', 'New place on the map — The Old North Road'),
        ('xp', '+100 XP'),
        ('level', 'Level 2 reached'),
        ('defeat', 'Defeated — you wake later with 1 HP'),
        ('ending', 'The story ends — The Road Remembers'),
    ]
    assert {beat['refId'] for beat in beats if 'refId' in beat} == {'clue_ash_marks', 'obj_one', 'obj_two', 'loc_road'}


@pytest.mark.parametrize(
    ('reason', 'text'),
    [
        ('all_enemies_defeated', 'Victory'),
        ('enemies_surrendered', 'The fight ends — they yield'),
        ('negotiated_resolution', 'The fight ends — they yield'),
        ('enemies_fled', 'The fight ends — they flee'),
        ('players_fled', 'You got away'),
    ],
)
def test_the_end_of_a_fight_is_one_beat_before_its_rewards(reason, text):
    before = _basis()
    after = deepcopy(before)
    after['heroes'][ACTOR].update(currency={'gp': 50}, xp=350)

    beats = story_beats(before, after, actor_id=ACTOR, state_before={}, applied_changes=[{'type': 'combat.end', 'endReason': reason}])

    assert _texts(beats) == [('combat_end', text), ('coins', '+25 gp'), ('xp', '+100 XP')]
    assert not any('enemy_' in beat['text'] for beat in beats)


def test_losses_say_how_the_item_went_and_coins_use_a_true_minus():
    before = _basis()
    before['heroes'][ACTOR].update(items={'Torch': 5, 'Potion of Healing': 1, 'Lantern': 1, 'Rope': 1}, currency={'gp': 75})
    after = deepcopy(before)
    after['heroes'][ACTOR].update(items={'Torch': 5}, currency={'gp': 25})
    applied = [
        {'type': 'shop.sell', 'itemName': 'Lantern'},
        {'type': 'inventory.transfer', 'itemName': 'Rope'},
    ]

    beats = story_beats(before, after, actor_id=ACTOR, state_before={}, applied_changes=applied)

    assert _texts(beats) == [
        ('item_lost', 'Used — Potion of Healing'),
        ('item_lost', 'Sold — Lantern'),
        ('item_lost', 'Given — Rope'),
        ('coins', '−50 gp'),
    ]


def test_a_check_shows_its_dc_only_when_the_pack_shows_it():
    state = {'currentScene': {'interactables': [
        {'id': 'grate', 'requirements': {'unlock': {'check': {'skill': 'athletics', 'dc': 13, 'showDc': True}}}},
        {'id': 'wall', 'requirements': {'search': {'check': {'skill': 'investigation', 'dc': 12}}}},
    ]}}
    applied = [
        {'type': 'scene.interactable.action', 'targetId': 'grate', 'objectAction': 'unlock',
         'event': {'check': {'skill': 'athletics', 'total': 9, 'dc': 13, 'outcome': 'failure'}}},
        {'type': 'scene.interactable.action', 'targetId': 'wall', 'objectAction': 'search',
         'event': {'check': {'skill': 'investigation', 'total': 7, 'dc': 12, 'outcome': 'failure'}}},
    ]

    beats = story_beats(_basis(), _basis(), actor_id=ACTOR, state_before=state, applied_changes=applied)

    assert _texts(beats) == [
        ('check', 'Athletics check: 9 — failure (DC 13)'),
        ('check', 'Investigation check: 7 — failure'),
    ]


def test_conditions_the_acting_hero_loses_read_in_the_second_person():
    before = _basis()
    before['heroes'][ACTOR]['conditions'] = ['prone']
    before['heroes']['player_2']['conditions'] = ['prone']

    beats = story_beats(before, _basis(), actor_id=ACTOR, state_before={}, applied_changes=[])

    assert _texts(beats) == [('condition', 'No longer prone'), ('condition', 'Mara Fen is no longer prone')]


def test_nothing_changed_is_an_empty_list():
    assert story_beats(_basis(), _basis(), actor_id=ACTOR, state_before={}, applied_changes=[]) == []


# The real turn in Safe Mode: the completion event, the log entry and a reload agree ------------------------------


@pytest.fixture()
def table(app, client, socketio, monkeypatch):
    from tests.test_flagship_adventure import Table

    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 15)
    response = client.post('/api/onboarding/play-now', json={})
    ids = {key: response.get_json()[key] for key in ('session_id', 'campaign_id', 'player_id', 'workspace_id')}
    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id'], 'workspace_id': ids['workspace_id']})
    socket_client.get_received()
    yield Table(app, client, socket_client, ids)
    socket_client.disconnect()


def _send(table, message: str, intent: dict | None = None) -> list[dict]:
    table.socket.emit(
        'send_message',
        {
            'session_id': table.ids['session_id'],
            'campaign_id': table.ids['campaign_id'],
            'player_id': table.ids['player_id'],
            'message': message,
            **({'action_intent': {**intent, 'text': message}} if intent else {}),
        },
    )
    received = table.socket.get_received()
    return next(
        event['args'][0]['details']['storyBeats']
        for event in received
        if event['name'] == 'turn_status' and event['args'][0]['status'] == 'saved'
        and event['args'][0]['details'].get('stage') == 'post_turn'
    )


def _logged_beats(table) -> list:
    entries = table.client.get(f"/api/sessions/{table.ids['session_id']}/log?view=player&player_id={table.ids['player_id']}").get_json()['entries']
    return [entry['metadata'].get('storyBeats') for entry in entries if entry['entry_type'] == 'dm' and entry['message'].startswith('DM: ')]


def test_a_turns_beats_reach_the_completion_event_and_survive_a_reload(table):
    ledger = _send(table, 'Read the caravan ledger.', {'kind': 'object', 'source': 'scene_panel', 'object': {'id': 'inn_caravan_ledger', 'action': 'inspect'}})
    bench = _send(table, "Search under the driver's bench.")
    key = _send(table, 'Take the Caravan Strongbox Key.', {
        'kind': 'item', 'source': 'scene_panel', 'inventory_action': 'pick_up',
        'item': {'id': 'caravan_strongbox_key', 'name': 'Caravan Strongbox Key', 'quantity': 1},
    })

    assert _texts(ledger)[0] == ('clue', 'Clue — Erased Caravan Ledger')
    assert ('objective_complete', 'Objective complete — Investigate the empty caravan') in _texts(ledger)
    assert _texts(bench) == [('check', 'Investigation check: 15 — success')]
    assert _texts(key) == [('item_gained', 'Taken — Caravan Strongbox Key')]
    assert _logged_beats(table)[-3:] == [ledger, bench, key]


def test_words_that_name_nothing_bring_the_scripted_narrator_hint_once_per_session(table):
    first = _send(table, 'I hum an old marching song.')
    second = _send(table, 'I whistle.')

    assert first == [{'kind': 'hint', 'text': HINT_TEXT}]
    assert second == []
