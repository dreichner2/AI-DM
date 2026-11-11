"""Release smoke: the Play Now adventure, read the way a player reads it.

Plays the careful route of ``tests/test_flagship_adventure.py`` (ledger to the
renewed ward) through the socket pipeline with that file's seeded dice, and a
second leg that loses the final fight for real, wakes, fights again and only
then chooses a fate. Both record every narration and story beat the turns
produced, as streamed to the table and as persisted, and fail on any raw id,
dotted change type or engine phrase in them, or on an ending reported before a
fate was chosen. Run it alone with ``make play-now-smoke``.
"""

from __future__ import annotations

import re

import pytest

from aidm_server.combat.vitality import hit_points
from aidm_server.models import DmTurn, SessionLogEntry, safe_json_loads
from tests import test_flagship_adventure as flagship
from tests.test_flagship_adventure import table  # noqa: F401 - the Play Now table fixture

pytestmark = pytest.mark.release_smoke

RAW_ID = re.compile(r'\b(?:loc|enemy|clue|obj|cp)_[a-z0-9_]+')
# A change or event type such as ``scene.interactable.action`` or ``xp.add``.
DOTTED_CHANGE_TYPE = re.compile(r'(?<![\w.])[a-z][a-z_]+(?:\.[a-z][a-z_]+)+(?![\w])')
ENGINE_PHRASES = ('update applied', 'not legal')
# The careful route narrates about forty turns; far fewer means it did not play and the scan proves nothing.
MIN_NARRATED_TURNS = 30
ROAD_STONE = 'valley_last_road_stone'
FATE_ACTIONS = frozenset(flagship.FATES)
DEFEAT_BEAT = 'Defeated — you wake later with 1 HP'


def leaks(text: str) -> list[str]:
    """Raw ids, dotted change types and engine phrases a player must never read."""

    found = [*RAW_ID.findall(text), *DOTTED_CHANGE_TYPE.findall(text)]
    found.extend(phrase for phrase in ENGINE_PHRASES if phrase in text.lower())
    return found


def _recording(socket_client) -> list[dict]:
    """Keep a copy of every event the table's socket receives."""

    events: list[dict] = []
    receive = socket_client.get_received

    def get_received(*args, **kwargs):
        received = receive(*args, **kwargs)
        events.extend(received)
        return received

    socket_client.get_received = get_received
    return events


def _watching_endings(table) -> list[tuple[bool, dict | None]]:  # noqa: F811
    """After every change a step makes: whether a fate of the road-stone was the last action sent, and the ending the player view reports."""

    steps: list[tuple[bool, dict | None]] = []
    last_intent: dict = {}
    send, changed = table.send, table._changed

    def watched_send(message: str, intent: dict):
        last_intent.clear()
        last_intent.update(intent)
        return send(message, intent)

    def watched_changed(*args, **kwargs):
        change = changed(*args, **kwargs)
        target = last_intent.get('object') or {}
        fate = last_intent.get('kind') == 'object' and target.get('id') == ROAD_STONE and target.get('action') in FATE_ACTIONS
        steps.append((fate, flagship._ending(change.snapshot)))
        return change

    table.send, table._changed = watched_send, watched_changed
    return steps


def _assert_no_ending_before_a_fate(steps: list[tuple[bool, dict | None]]) -> dict:
    """No step reports an ending until a fate is chosen; the fate's step reports it. Returns that ending."""

    first_fate = next(index for index, (fate, _ending) in enumerate(steps) if fate)
    early = [index for index, (_fate, ending) in enumerate(steps[:first_fate]) if ending]
    assert early == [], f'an ending was reported before any fate was chosen, at steps {early}: {steps[early[0]][1]}'
    ending = steps[first_fate][1]
    assert ending, 'choosing a fate reported no ending'
    return ending


def _assert_reads_cleanly(table, events: list[dict]) -> list[tuple[str, str]]:  # noqa: F811
    """Fail on any leak in the streamed and persisted narration and beats; return the persisted texts."""

    persisted = _persisted_texts(table.app, table.ids['session_id'])
    found = [(label, leak, text) for label, text in [*_streamed_texts(events), *persisted] for leak in leaks(text)]
    assert found == [], '\n'.join(f'{label}: {leak!r} in {text!r}' for label, leak, text in found)
    return persisted


def _streamed_texts(events: list[dict]) -> list[tuple[str, str]]:
    """Each turn's streamed narration and every beat the final turn status carried."""

    narration: dict[int, list[tuple[int, str]]] = {}
    texts: list[tuple[str, str]] = []
    for event in events:
        payload = event['args'][0] if event.get('args') else {}
        if event['name'] == 'dm_chunk':
            narration.setdefault(payload['turn_id'], []).append((payload['index'], payload['chunk']))
        elif event['name'] == 'turn_status':
            for beat in (payload.get('details') or {}).get('storyBeats') or []:
                texts.append((f"streamed beat (turn {payload['turn_id']})", beat['text']))
    for turn_id, chunks in narration.items():
        texts.append((f'streamed narration (turn {turn_id})', ''.join(chunk for _index, chunk in sorted(chunks))))
    return texts


def _persisted_texts(app, session_id: int) -> list[tuple[str, str]]:
    """Every Dungeon Master log entry and every beat stored on the session's turns and log entries."""

    texts: list[tuple[str, str]] = []
    with app.app_context():
        for entry in SessionLogEntry.query.filter_by(session_id=session_id, entry_type='dm').order_by(SessionLogEntry.id):
            texts.append((f'log entry {entry.id}', entry.message))
            for beat in safe_json_loads(entry.metadata_json, {}).get('storyBeats') or []:
                texts.append((f'log entry {entry.id} beat', beat['text']))
        for turn in DmTurn.query.filter_by(session_id=session_id).order_by(DmTurn.turn_id):
            for beat in safe_json_loads(turn.metadata_json, {}).get('storyBeats') or []:
                texts.append((f'turn {turn.turn_id} beat', beat['text']))
    return texts


def test_leaks_catches_what_a_player_must_never_read():
    assert leaks('You reach loc_old_north_road.') == ['loc_old_north_road']
    assert leaks('scene.interactable.action update applied') == ['scene.interactable.action', 'update applied']
    assert leaks('+100 XP from xp.add') == ['xp.add']
    assert leaks('That action is not legal: locked.') == ['not legal']
    assert leaks('The Cinder Scribe falls. Clue — The erased caravan ledger. +15 gp, e.g. coins.') == []


def test_the_careful_route_reads_cleanly_from_ledger_to_renewed_ward(table):  # noqa: F811
    events = _recording(table.socket)
    steps = _watching_endings(table)

    flagship.test_the_careful_route_renews_the_ward(table)

    assert _assert_no_ending_before_a_fate(steps)['title'] == 'The Road Remembers'
    narrated_turns = [label for label, _text in _streamed_texts(events) if label.startswith('streamed narration')]
    assert len(narrated_turns) >= MIN_NARRATED_TURNS, narrated_turns
    persisted = _assert_reads_cleanly(table, events)
    assert any(label.endswith('beat') for label, _text in persisted)
    assert any(text == 'The story ends — The Road Remembers' for _label, text in persisted)


def test_a_lost_final_fight_is_fought_again_before_any_ending(table, monkeypatch):  # noqa: F811
    events = _recording(table.socket)
    steps = _watching_endings(table)
    table.travel('loc_old_north_road', 'The Old North Road')
    table.end_combat('players_fled')
    table.travel('loc_ridge_and_culvert_paths', 'Ridge and Culvert Paths')
    table.act('ridge_culvert_grate', 'unlock', 'Culvert Grate')
    table.act('ridge_culvert_grate', 'open', 'Culvert Grate')
    table.travel('loc_valley_last_road_stone', 'Valley of the Last Road-Stone')
    assert table.act(ROAD_STONE, 'inspect', 'The Last Road-Stone').encounter == 'enc_final_binding'

    # The hero misses every swing and the Scribe's side hits hard: the party is beaten.
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda _sides: 0)
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda sides: sides - 1)
    beaten = table.fight()

    assert beaten.snapshot['combat']['status'] == 'ended'
    assert flagship._ending(beaten.snapshot) is None
    assert flagship._active_checkpoint(beaten.snapshot) == 'cp_06_final_valley'
    assert flagship._quest_status(beaten.snapshot) != 'completed'
    assert hit_points(flagship._hero(beaten.snapshot))['current'] == 1
    assert DEFEAT_BEAT in [beat['text'] for beat in table.pipeline_beats()]

    # Awake again, the hero faces the Scribe a second time, wins, and only then chooses a fate. At 1 HP
    # their wounds cost 4 on every d20, so the seeded 15 would miss; a natural 20 still lands.
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: sides - 1)
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda _sides: 1)
    assert table.act(ROAD_STONE, 'inspect', 'The Last Road-Stone').encounter == 'enc_final_binding'
    assert table.fight().flags == {'cinder_scribe_resolved': True}
    table.act(ROAD_STONE, 'trigger', 'The Last Road-Stone')

    assert _assert_no_ending_before_a_fate(steps) == flagship._ending_for('ward_claimed')
    persisted = _assert_reads_cleanly(table, events)
    assert DEFEAT_BEAT in [text for _label, text in persisted]
