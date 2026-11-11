"""Searching takes what it finds: a successful search (or opening) of a container outside a fight puts its contents
in the searching hero's pack in the same turn, except what the pack marks ``"leave": true`` and anything found while
a fight is on; things already lying in a scene are taken all at once with ``Take everything here``.

Through the real socket pipeline on the Play Now adventure, in Safe Mode unless the AI narrator's brief is the point;
object checks roll 15.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
import requests

from aidm_server.database import db
from aidm_server.game_state.orchestration.search_take import search_take_actions
from aidm_server.models import Session, safe_json_dumps, safe_json_loads
from tests.ai_narrator import AiTable, RecordedNarrator, ai_narration
from tests.test_party_play import PartyTable

BENCH = ('inn_drivers_bench', "Driver's Bench")
KEY = 'caravan_strongbox_key'


def _safe_mode(app, monkeypatch) -> RecordedNarrator:
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 15)
    return RecordedNarrator()


@pytest.fixture()
def table(app, client, socketio, monkeypatch) -> AiTable:
    return AiTable(app, client, socketio, _safe_mode(app, monkeypatch))


@pytest.fixture()
def ai_table(app, client, socketio, monkeypatch) -> AiTable:
    narrator = ai_narration(app, monkeypatch)
    return AiTable(app, client, socketio, narrator)


def _search(table: AiTable, target_id: str, name: str) -> list[dict]:
    message = f'Arden Vale attempts to search {name}.'
    intent = {'kind': 'object', 'source': 'scene_panel', 'text': message, 'object': {'id': target_id, 'action': 'search'}}
    return table.send(message, action_intent=intent)


def _operator(client, table, changes: list[dict]) -> None:
    response = client.post(f"/api/sessions/{table.ids['session_id']}/combat/apply-state-changes", json={'changes': changes})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['validation']['rejected'] == []


def _beats(table) -> list[str]:
    return [beat['text'] for beat in safe_json_loads(table.turn().metadata_json, {}).get('storyBeats') or []]


def _hero(table) -> dict[str, Any]:
    return table.snapshot()['playerCharacters'][0]


def _pack(table) -> Counter:
    counts: Counter = Counter()
    for item in _hero(table)['inventory']['items']:
        counts[item['name']] += item.get('quantity') or 1
    return counts


def _loose(table) -> list[str]:
    return [item['id'] for item in table.snapshot()['currentScene'].get('items') or []]


def leave_where_it_lies(app, session_id: int, item_id: str) -> None:
    """Mark a container's content ``"leave": true`` wherever the session's snapshot records it."""

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for entry in value.get('contents') or []:
                if isinstance(entry, dict) and entry.get('id') == item_id:
                    entry['leave'] = True
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    with app.app_context():
        session = db.session.get(Session, session_id)
        snapshot = safe_json_loads(session.state_snapshot, {})
        visit(snapshot)
        session.state_snapshot = safe_json_dumps(snapshot, {})
        db.session.commit()


def test_a_search_puts_what_it_finds_in_the_pack_in_the_same_turn(table):
    _search(table, *BENCH)

    assert _loose(table) == []
    assert _pack(table)['Caravan Strongbox Key'] == 1
    assert _beats(table) == ['Investigation check: 15 — success', 'Taken — Caravan Strongbox Key']
    assert 'You find and take a Caravan Strongbox Key.' in table.turn().dm_output


def test_coins_go_into_the_purse_and_are_told_first(app, client, socketio, monkeypatch):
    _safe_mode(app, monkeypatch)
    table = AiTable(app, client, socketio, RecordedNarrator())
    # Radan's alliance keeps the road quiet, so the handcart is searched outside a fight.
    _operator(client, table, [{'type': 'flag.set', 'flagKey': 'radan_allied', 'flagValue': True}])
    road = {'id': 'loc_old_north_road', 'name': 'The Old North Road'}
    table.send('The party travels to The Old North Road.', action_intent={'kind': 'travel', 'source': 'scene_panel', 'location': road})
    gold, torches = _hero(table)['inventory']['currency']['gp'], _pack(table)['Torch']

    _search(table, 'road_abandoned_cart', 'Abandoned Handcart')

    assert _loose(table) == []
    assert (_hero(table)['inventory']['currency']['gp'] - gold, _pack(table)['Torch'] - torches) == (8, 1)
    assert 'You find and take 8 gp and a Torch.' in table.turn().dm_output
    assert {'Taken — Torch', '+8 gp'} <= set(_beats(table))


def test_in_party_play_the_search_names_the_hero_who_took_it(app, client, socketio, monkeypatch):
    _safe_mode(app, monkeypatch)
    table = PartyTable(app, client, socketio, party=True)
    try:
        intent = {'kind': 'object', 'source': 'scene_panel', 'object': {'id': BENCH[0], 'action': 'search'}}
        table.send('Seren Ashgrove', "Seren Ashgrove attempts to search Driver's Bench.", intent)
        turn = table.turns_since(table.last_turn_id() - 1)[-1]
    finally:
        table.close()

    assert 'Seren Ashgrove finds and takes a Caravan Strongbox Key.' in turn['narration']
    assert 'Seren Ashgrove took the Caravan Strongbox Key' in [beat['text'] for beat in turn['beats']]


def test_what_the_pack_marks_leave_stays_where_it_lies_with_its_take_suggestion(table):
    leave_where_it_lies(table.app, table.ids['session_id'], KEY)

    _search(table, *BENCH)

    assert _loose(table) == [KEY]
    assert 'Caravan Strongbox Key' not in _pack(table)
    assert _beats(table) == ['Investigation check: 15 — success']
    narration = table.turn().dm_output
    assert 'You find a Caravan Strongbox Key.' in narration and 'take' not in narration
    story = table.app.test_client().get(f"/api/sessions/{table.ids['session_id']}/state?view=player&player_id={table.ids['player_id']}")
    items = story.get_json()['state_snapshot']['currentScene']['items']
    assert [(item['id'], item['takeLabel']) for item in items] == [(KEY, 'Take the Caravan Strongbox Key')]


def test_nothing_found_while_a_fight_is_on_is_taken():
    revealed = {'id': 'loot', 'name': 'Loot'}
    change = {
        'type': 'scene.interactable.action',
        'objectAction': 'search',
        'targetId': 'crate',
        'event': {'type': 'interactable.searched', 'actorId': 'player_1', 'action': 'search', 'revealedItems': [revealed]},
    }
    calm = {'currentScene': {'items': [revealed]}, 'combat': {'status': 'none'}}
    fighting = {'currentScene': {'items': [revealed]}, 'combat': {'status': 'active', 'round': 1, 'participants': []}}

    assert [action['itemId'] for action in search_take_actions(calm, [change], actor_id='player_1')] == ['loot']
    assert search_take_actions(fighting, [change], actor_id='player_1') == []
    # Only the searching hero takes, and only from a search or an opening that worked.
    assert search_take_actions(calm, [change], actor_id='player_2') == []
    failed = {**change, 'event': {**change['event'], 'type': 'interactable.check_failed'}}
    assert search_take_actions(calm, [failed], actor_id='player_1') == []


def test_take_everything_here_takes_every_loose_thing_in_one_turn(table, client):
    _operator(
        client,
        table,
        [
            {'type': 'scene.item.add', 'item': {'id': 'inn_lantern_hook', 'name': 'Lantern Hook', 'type': 'gear', 'quantity': 1}},
            {'type': 'scene.item.add', 'item': {'id': 'inn_spare_rope', 'name': 'Spare Rope', 'type': 'gear', 'quantity': 1}},
        ],
    )
    table.send('Take everything here.')

    assert table.turn().player_input == 'Take everything here.'
    assert _loose(table) == []
    assert {'Lantern Hook', 'Spare Rope'} <= set(_pack(table))
    assert set(_beats(table)) == {'Taken — Lantern Hook', 'Taken — Spare Rope'}


def test_the_ai_narrator_is_told_what_the_search_found_and_took(ai_table):
    ai_table.reply("I search under the driver's bench.", 'Arden runs a hand under the bench and comes up with a key.')

    did = next(line for line in ai_table.brief().splitlines() if line.startswith('Did: '))
    assert 'Found and taken by the hero: a Caravan Strongbox Key.' in did
    assert 'still lying there' not in did
