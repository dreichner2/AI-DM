"""Taking your time over a failed check, through the real socket turn pipeline."""

from __future__ import annotations

from aidm_server.models import DmTurn

from tests.test_flagship_adventure import table  # noqa: F401 - the Play Now table fixture


def test_a_failed_search_can_be_retried_by_taking_time_through_the_socket(table, monkeypatch):  # noqa: F811
    # The first search fails whatever the hero's bonus: the die shows 1.
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 1)
    intent = {'kind': 'object', 'source': 'scene_panel', 'object': {'id': 'inn_drivers_bench', 'action': 'search'}}

    failed = table.send("Search under the driver's bench.", intent)
    assert failed.snapshot['currentScene']['items'] == []

    retried = table.send("Search under the driver's bench — take your time.", {**intent, 'take_time': True})

    with table.app.app_context():
        turn = DmTurn.query.filter_by(session_id=table.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
        narration = turn.dm_output or ''
    assert 'already tried' not in narration, narration
    # The search that succeeds takes what it finds.
    assert ([item['id'] for item in retried.snapshot['currentScene']['items']], retried.items['Caravan Strongbox Key']) == ([], 1)
    assert retried.snapshot['clock']['phase'] == 'night'
