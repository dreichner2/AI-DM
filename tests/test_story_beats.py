"""Story beats: what changed in a turn, as the quiet lines under the Dungeon Master's reply."""

from __future__ import annotations

from copy import deepcopy
import re

import pytest
import requests

from aidm_server.game_state.orchestration.story_beats import HINT_TEXT, story_beats


ACTOR = 'player_1'
ARDEN = {'name': 'Arden Vale', 'hp': 12, 'conditions': [], 'items': {'Torch': 5}, 'currency': {'gp': 25}, 'xp': 250, 'level': 1}
MARA = {'name': 'Mara Fen', 'hp': 9, 'conditions': [], 'items': {}, 'currency': {}, 'xp': 0, 'level': 1}


def _party_basis(**overrides) -> dict:
    """Two heroes in the session: party play."""

    return _basis(heroes={ACTOR: deepcopy(ARDEN), 'player_2': deepcopy(MARA)}, **overrides)


def _basis(**overrides) -> dict:
    """The acting hero alone."""

    basis = {
        'heroes': {ACTOR: deepcopy(ARDEN)},
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


def _every_kind_of_change(before: dict) -> tuple[dict, list[dict]]:
    after = deepcopy(before)
    after['heroes'][ACTOR].update(hp=5, conditions=['prone'], items={'Torch': 4, 'Caravan Strongbox Key': 1, 'Ash-Marked Token': 2},
                                  currency={'gp': 40}, xp=350, level=2)
    # Alone, the hero's own death save; in party play, Mara's.
    saver = 'player_2' if 'player_2' in after['heroes'] else ACTOR
    if saver == 'player_2':
        after['heroes']['player_2'].update(hp=11, conditions=['frightened'])
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
        {'type': 'combat.death_save', 'participantId': saver, 'participantName': after['heroes'][saver]['name'], 'roll': 12, 'result': 'success'},
        {'type': 'combat.end', 'endReason': 'all_enemies_defeated'},
        {'type': 'combat.defeat_recovery'},
    ]
    return after, applied


def test_every_kind_of_change_is_one_beat_in_contract_order():
    before = _basis()
    after, applied = _every_kind_of_change(before)

    beats = story_beats(before, after, actor_id=ACTOR, state_before={}, applied_changes=applied)

    assert _texts(beats) == [
        ('check', 'Investigation check: 14 — success'),
        ('hp', 'Took 7 damage'),
        ('condition', 'You are prone'),
        ('death_save', 'Death save: 12 — success'),
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


def test_in_party_play_every_beat_about_a_hero_names_them():
    before = _party_basis()
    after, applied = _every_kind_of_change(before)

    beats = story_beats(before, after, actor_id=ACTOR, state_before={}, applied_changes=applied)

    assert _texts(beats) == [
        ('check', 'Arden Vale — Investigation check: 14 — success'),
        ('hp', 'Arden Vale took 7 damage'),
        ('hp', 'Mara Fen healed 2'),
        ('condition', 'Arden Vale is prone'),
        ('condition', 'Mara Fen is frightened'),
        ('death_save', 'Mara Fen: death save: 12 — success'),
        ('combat_end', 'Victory'),
        ('item_gained', 'Arden Vale took the Caravan Strongbox Key'),
        ('item_gained', 'Arden Vale received Ash-Marked Token ×2'),
        ('item_lost', 'Arden Vale used the Torch'),
        ('coins', 'Arden Vale +15 gp'),
        ('clue', 'Clue — Ash Marks on the Road'),
        ('objective_complete', 'Objective complete — Investigate the empty caravan'),
        ('objective_new', 'New objective — Follow the changing road'),
        ('place', 'New place on the map — The Old North Road'),
        ('xp', 'Arden Vale +100 XP'),
        ('level', 'Arden Vale reached level 2'),
        ('defeat', 'Defeated — you wake later with 1 HP'),
        ('ending', 'The story ends — The Road Remembers'),
    ]
    assert not [text for _kind, text in _texts(beats) if re.search(r'\byou(?:r|rself)?\b', text, re.IGNORECASE) and _kind != 'defeat']


def test_in_party_play_a_gain_every_hero_shares_is_one_beat_each():
    before = _party_basis()
    after = deepcopy(before)
    after['heroes'][ACTOR].update(currency={'gp': 35, 'sp': 4}, xp=325)
    after['heroes']['player_2'].update(currency={'gp': 10}, xp=75)

    beats = story_beats(before, after, actor_id=ACTOR, state_before={}, applied_changes=[])

    assert _texts(beats) == [('coins', '+10 gp each'), ('coins', 'Arden Vale +4 sp'), ('xp', '+75 XP each')]


def test_a_hero_out_of_the_fight_and_a_hero_coming_round_are_one_beat_each():
    before = _party_basis()
    before['heroes']['player_2'].update(hp=0, conditions=['dying', 'unconscious'])
    out = deepcopy(before)
    out['heroes']['player_2'].update(conditions=['out_of_fight', 'stable', 'unconscious'])

    assert _texts(story_beats(before, out, actor_id=ACTOR, state_before={}, applied_changes=[])) == [
        ('condition', 'Mara Fen is out of the fight'),
    ]

    woken = deepcopy(out)
    woken['heroes']['player_2'].update(hp=1, conditions=[])
    come_round = [{'type': 'party.come_round', 'wokenActorIds': ['player_2']}]
    assert _texts(story_beats(out, woken, actor_id=ACTOR, state_before={}, applied_changes=come_round)) == [
        ('condition', 'Mara Fen comes round'),
    ]


def test_alone_the_hero_is_out_of_the_fight_and_comes_round_in_the_second_person():
    before = _basis()
    before['heroes'][ACTOR].update(hp=0, conditions=['dying', 'unconscious'])
    out = deepcopy(before)
    out['heroes'][ACTOR].update(conditions=['out_of_fight', 'stable', 'unconscious'])
    woken = deepcopy(out)
    woken['heroes'][ACTOR].update(hp=1, conditions=[])
    recovery = [{'type': 'combat.defeat_recovery', 'wokenActorIds': [ACTOR]}]

    assert _texts(story_beats(before, out, actor_id=ACTOR, state_before={}, applied_changes=[])) == [
        ('condition', 'You are out of the fight'),
    ]
    assert _texts(story_beats(out, woken, actor_id=ACTOR, state_before={}, applied_changes=recovery)) == [
        ('condition', 'You come round'),
        ('defeat', 'Defeated — you wake later with 1 HP'),
    ]


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


def test_conditions_the_hero_loses_read_in_the_second_person_alone_and_by_name_in_party_play():
    alone = _basis()
    alone['heroes'][ACTOR]['conditions'] = ['prone']
    party = _party_basis()
    for hero in party['heroes'].values():
        hero['conditions'] = ['prone']

    assert _texts(story_beats(alone, _basis(), actor_id=ACTOR, state_before={}, applied_changes=[])) == [
        ('condition', 'No longer prone'),
    ]
    assert _texts(story_beats(party, _party_basis(), actor_id=ACTOR, state_before={}, applied_changes=[])) == [
        ('condition', 'Arden Vale is no longer prone'),
        ('condition', 'Mara Fen is no longer prone'),
    ]


def test_nothing_changed_is_an_empty_list():
    assert story_beats(_basis(), _basis(), actor_id=ACTOR, state_before={}, applied_changes=[]) == []


@pytest.mark.parametrize(
    ('roll', 'text'),
    [
        # A check names its skill or ability once, and plain "Check" when neither is known.
        ({'rule_type': 'check', 'total': 14, 'dc': 14, 'outcome': 'success'}, 'Check: 14 — success (DC 14)'),
        ({'rule_type': 'stealth', 'total': 9, 'dc': 12, 'outcome': 'failure'}, 'Stealth check: 9 — failure (DC 12)'),
        ({'rule_type': 'lore', 'total': 15, 'outcome': 'success', 'ability': {'key': 'intelligence'}}, 'Intelligence check: 15 — success'),
        ({'rule_type': 'check', 'total': 11, 'outcome': 'failure', 'ability': {'key': 'wisdom'}}, 'Wisdom check: 11 — failure'),
        ({'rule_type': 'dexterity_saving_throw', 'total': 12, 'dc': 13, 'outcome': 'failure'}, 'Dexterity saving throw: 12 — failure (DC 13)'),
        # A roll made with no check pending is no check: the die, or the ability picked and its arithmetic.
        ({'rule_type': 'check', 'free': True, 'die': 'd20', 'kept': 10, 'modifier': 0, 'total': 10}, 'd20: 10'),
        (
            {'rule_type': 'check', 'free': True, 'die': 'd20', 'kept': 10, 'modifier': 3, 'total': 13, 'ability': {'key': 'strength'}},
            'Strength: 13 (d20 10 + 3)',
        ),
        (
            {'rule_type': 'check', 'free': True, 'die': 'd20', 'kept': 10, 'modifier': -1, 'total': 9, 'ability': {'key': 'charisma'}},
            'Charisma: 9 (d20 10 − 1)',
        ),
        ({'rule_type': 'check', 'free': True, 'die': 'd6', 'kept': 4, 'modifier': 0, 'total': 4}, 'd6: 4'),
    ],
)
def test_a_roll_beat_says_what_was_rolled(roll, text):
    beats = story_beats(_basis(), _basis(), actor_id=ACTOR, state_before={}, applied_changes=[], authoritative_roll=roll)

    assert _texts(beats) == [('check', text)]


def test_an_object_check_with_no_skill_or_ability_reads_check():
    applied = [{'type': 'scene.interactable.action', 'targetId': 'gate', 'event': {'check': {'total': 8, 'outcome': 'failure'}}}]

    assert _texts(story_beats(_basis(), _basis(), actor_id=ACTOR, state_before={}, applied_changes=applied)) == [
        ('check', 'Check: 8 — failure')
    ]


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
