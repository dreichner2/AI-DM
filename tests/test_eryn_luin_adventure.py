"""Shadow Under Eryn Luin, played end to end in Safe Mode with typed actions only.

Three routes run through the socket pipeline on the Play Now table's seeded
dice (``tests/test_flagship_adventure.py``): the careful route seals the
Star-Shard, the forceful route forces the gate and shatters it, and the low road
slips under the cairns and takes it. Each assertion names the state change the
typed action itself caused, and every narration and story beat a route produces
is read for raw ids and engine phrases as the release smoke reads the Play Now
adventure.
"""

from __future__ import annotations

from collections import Counter
from fractions import Fraction
import json
import math
from pathlib import Path
from typing import Any

import pytest
import requests

from aidm_server.combat.state import instantiate_creature
from aidm_server.game_state.campaign_pack_encounters import PARTY_SCALING
from aidm_server.game_state.leveling import level_for_xp
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_file
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from aidm_server.services.pregen_characters import PREGENERATED_CHARACTER_PRESETS
from scripts import solo_balance
from tests import test_flagship_adventure as flagship
from tests.test_meeting_people import _met_by, _talk_offers
from tests.test_play_now_release_smoke import _assert_reads_cleanly, _recording


PACK_PATH = Path(__file__).resolve().parents[1] / 'docs' / 'examples' / 'shadow_under_eryn_luin_campaign_pack.json'
PACK = json.loads(PACK_PATH.read_text(encoding='utf-8'))
QUEST = 'quest_shard_beneath_blue_mountains'
PACK_FLAGS = set(PACK['startingState']['flags'])
SCENE_OBJECTS = {record['id']: record for location in PACK['locations'] for record in location.get('interactables', [])}
ENCOUNTERS = {encounter['id']: encounter for encounter in PACK['encounters']}
CONFRONTATION = ENCOUNTERS['enc_star_shard_confrontation']
RELEASED, TURNED = CONFRONTATION['adjustments']
FINAL_CHECKPOINT = next(checkpoint for checkpoint in PACK['checkpoints'] if checkpoint.get('terminal'))
ENDINGS = {variant['when']['flagKey']: variant for variant in FINAL_CHECKPOINT['endingVariants']}
SHARD = SCENE_OBJECTS['chamber_star_shard']
FATES = {'use': 'shard_sealed', 'break': 'shard_shattered', 'lock': 'shard_returned', 'trigger': 'shard_taken'}
# The XP the careful route's hero holds on reaching each fight's place.
ROUTE_XP = (
    ('enc_moonwell_ambush', 0),
    ('enc_black_pines_oath', 100),
    ('enc_khazad_tarn_sentries', 300),
    ('enc_silvervein_watch_post', 300),
    ('enc_star_shard_confrontation', 450),
)
# What the careful route knows when it reaches each fight (the flags its adjustments read).
ROUTE_FLAGS = {'enc_star_shard_confrontation': {'dead_released': True, 'grishnak_turned': True}}
# Text the Dungeon Master holds that the player view must never carry.
DM_ONLY_TEXTS = [
    *(record['description'] for record in SCENE_OBJECTS.values() if 'description' in record),
    *(text for record in SCENE_OBJECTS.values() for text in (record.get('actionText') or {}).values()),
    *(encounter['setup'] for encounter in PACK['encounters']),
    *(adjustment['text'] for adjustment in CONFRONTATION['adjustments']),
    *(fact for npc in PACK['npcs'] for fact in npc.get('knows', [])),
    *(record['gmNotes'] for collection in ('locations', 'npcs', 'checkpoints') for record in PACK[collection] if 'gmNotes' in record),
    PACK['gmNotes'],
    PACK['hiddenSceneNotes'],
]


def _flags_set_by_the_pack() -> set[str]:
    keys = {key for record in SCENE_OBJECTS.values() for flags in (record.get('setsFlags') or {}).values() for key in flags}
    for encounter in PACK['encounters']:
        for outcome in encounter['outcomes'].values():
            keys |= {change['flagKey'] for change in outcome['consequences'] if change['type'] == 'flag.set'}
    return keys


def _flags(snapshot: dict) -> dict[str, Any]:
    return {key: value for key, value in snapshot['flags'].items() if key in PACK_FLAGS}


def _objectives(snapshot: dict) -> dict[str, str]:
    quest = next(quest for quest in snapshot['quests'] if quest['id'] == QUEST)
    return {objective['id']: objective.get('status', 'open') for objective in quest['objectives']}


def _quest_status(snapshot: dict) -> str:
    return next(quest for quest in snapshot['quests'] if quest['id'] == QUEST)['status']


def _diff(before: dict, after: dict) -> flagship.Change:
    old_flags, old_objectives, old_places = _flags(before), _objectives(before), flagship._places(before)
    new_encounters = flagship._triggered(after)[len(flagship._triggered(before)):]
    return flagship.Change(
        flags={key: value for key, value in _flags(after).items() if old_flags.get(key) != value},
        clues={clue['id'] for clue in after.get('clues') or []} - {clue['id'] for clue in before.get('clues') or []},
        objectives={key: value for key, value in _objectives(after).items() if old_objectives.get(key) != value},
        places={key: value for key, value in flagship._places(after).items() if old_places.get(key) != value},
        gp=flagship._hero(after)['inventory']['currency']['gp'] - flagship._hero(before)['inventory']['currency']['gp'],
        xp=flagship._hero(after)['xp']['current'] - flagship._hero(before)['xp']['current'],
        items=flagship._item_counts(after) - flagship._item_counts(before),
        encounter=new_encounters[-1] if new_encounters else None,
        snapshot=after,
    )


def _ending_for(flag_key: str) -> dict:
    variant = ENDINGS[flag_key]
    return {
        'checkpointId': FINAL_CHECKPOINT['id'],
        'title': variant['title'],
        'summary': FINAL_CHECKPOINT['playerSummary'],
        'text': variant['text'],
    }


class Table(flagship.Table):
    """The Play Now table, reading this pack's flags and quest and noting the ending after every step."""

    def __init__(self, *args: Any):
        super().__init__(*args)
        self.endings: list[dict | None] = []

    def _changed(self, since: dict | None = None) -> flagship.Change:
        after = self.snapshot()
        change = _diff(self.last if since is None else since, after)
        self.endings.append(flagship._ending(after))
        self.last = after
        return change

    def talk(self, message: str) -> str:
        """Say something at the table; returns the Dungeon Master's reply."""

        self.send(message, {'kind': 'message'})
        return self.narration()

    def rejected(self, message: str, intent: dict) -> list[str]:
        """The errors an action draws (it changes nothing)."""

        self.turn += 1
        self.socket.emit(
            'send_message',
            {
                'session_id': self.ids['session_id'],
                'campaign_id': self.ids['campaign_id'],
                'player_id': self.ids['player_id'],
                'message': message,
                'client_message_id': f'flagship-{self.turn}',
                'action_intent': {**intent, 'text': message},
            },
        )
        return [event['args'][0]['error'] for event in self.socket.get_received() if event['name'] == 'error' and event['args']]


@pytest.fixture()
def table(app, client, socketio, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    monkeypatch.setenv('AIDM_HELPER_LLM_PROVIDER', 'deepseek')
    monkeypatch.setenv('AIDM_DEEPSEEK_API_KEY', 'deepseek-test-key')
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1', AIDM_STATE_PIPELINE_HELPER_IN_TESTS=True)
    # The Play Now table's seeded rollers: object checks roll 15, the hero rolls 15 to hit, and enemies roll 1.
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 14 if sides == 20 else sides - 1)
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda _sides: 1)

    response = client.post('/api/onboarding/play-now', json={'example_pack_id': PACK['packId']})
    assert response.status_code == 201, response.get_data(as_text=True)
    ids = {key: response.get_json()[key] for key in ('session_id', 'campaign_id', 'player_id', 'workspace_id')}
    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit(
        'join_session',
        {'session_id': ids['session_id'], 'player_id': ids['player_id'], 'workspace_id': ids['workspace_id']},
    )
    socket_client.get_received()
    yield Table(app, client, socket_client, ids)
    socket_client.disconnect()


def _visible_objects(snapshot: dict) -> list[str]:
    return [entry['id'] for entry in flagship._story_view(snapshot)['currentScene']['interactables']]


def _story_object(snapshot: dict, object_id: str) -> dict:
    return next(entry for entry in flagship._story_view(snapshot)['currentScene']['interactables'] if entry['id'] == object_id)


def _refusals(snapshot: dict, object_id: str) -> dict[str, tuple[bool, str | None]]:
    return {entry['action']: (entry['legal'], entry.get('reason')) for entry in _story_object(snapshot, object_id)['offeredActions']}


def _strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return [value] if isinstance(value, str) else []


def _assert_player_view_hides_dm_text(snapshot: dict) -> None:
    shown = _strings(filter_session_snapshot_for_player(snapshot))
    assert [text for text in DM_ONLY_TEXTS if any(text in string for string in shown)] == []


def _assert_no_ending_before_the_fate(table: Table) -> None:
    """Every step but the last one (the fate) reported no ending."""

    assert [ending for ending in table.endings[:-1] if ending] == []


def _marshal_hp(snapshot: dict) -> int:
    return next(
        participant['hp']['max']
        for participant in snapshot['combat']['participants']
        if participant.get('campaignPackEnemyId') == 'enemy_wight_marshal'
    )


def _authored_marshal_hp() -> int:
    marshal = next(enemy for enemy in PACK['enemies'] if enemy['id'] == 'enemy_wight_marshal')
    return instantiate_creature(marshal, instance_id='marshal', team='enemy')['hp']['max']


def _known_people(snapshot: dict) -> set[str]:
    return {npc['id'] for npc in snapshot.get('knownNpcs') or []}


def _arrive(table: Table, location_id: str, name: str, encounter_id: str) -> flagship.Change:
    """Travel on, checking the hero holds the XP the balance table assumes for the fight met there."""

    arrival = table.travel(location_id, name)
    assert flagship._hero(arrival.snapshot)['xp']['current'] == dict(ROUTE_XP)[encounter_id]
    _assert_player_view_hides_dm_text(arrival.snapshot)
    return arrival


def test_the_pack_lints_strictly_clean_and_every_find_is_reachable(app):
    with app.app_context():
        result = lint_campaign_pack_file(PACK_PATH, workspace_id='owner')

    assert result['issues'] == []
    assert set(PACK['directorRules']) == flagship.ENGINE_DIRECTOR_RULES
    assert len(PACK['gmNotes']) <= 700
    assert [location['id'] for location in PACK['locations'] if location.get('visibleAtStart')] == [
        'loc_graymere_watch',
        'loc_moonwell_crossing',
    ]
    found_clues = {clue for record in SCENE_OBJECTS.values() for clues in (record.get('discoversClueIds') or {}).values() for clue in clues}
    found_places = {place for record in SCENE_OBJECTS.values() for places in (record.get('discoversLocationIds') or {}).values() for place in places}
    set_flags = _flags_set_by_the_pack()
    for encounter in PACK['encounters']:
        for outcome in encounter['outcomes'].values():
            found_places |= {change['locationId'] for change in outcome['consequences'] if change['type'] == 'location.discover'}
    assert found_clues == {clue['id'] for clue in PACK['clues']}
    assert found_places == {location['id'] for location in PACK['locations'] if not location.get('visibleAtStart')}
    assert set_flags == PACK_FLAGS
    for objective in next(quest for quest in PACK['quests'] if quest['id'] == QUEST)['objectives']:
        predicate = objective['completeWhen']
        assert {entry['flagKey'] for entry in predicate.get('anyOf', [predicate])} <= set_flags, objective['id']


def test_no_story_flag_is_one_the_pack_sets_itself():
    # Narration may set a story flag whenever its words come up, so a flag an object or a fight sets is never one.
    assert {flag['key'] for flag in PACK.get('storyFlags', [])} & _flags_set_by_the_pack() == set()


def test_every_object_offers_exactly_its_labelled_actions():
    for record in SCENE_OBJECTS.values():
        assert set(record['allowedActions']) >= set(record['actionLabels']), record['id']
        assert set(record['allowedActions']) - set(record['actionLabels']) <= {'inspect'}, record['id']
        assert all(len(label) <= 48 for label in record['actionLabels'].values())
        assert record['playerDescription'].count('.') == 1 and record['playerDescription'].endswith('.'), record['id']
    assert {enemy['id']: (enemy.get('singularName'), enemy.get('proper'), enemy['challengeTier']) for enemy in PACK['enemies']} == {
        'enemy_palehand_orcs': ('Pale Hand Orc', None, 'easy'),
        'enemy_oathbound_dead': ('Oath-bound Soldier', None, 'standard'),
        'enemy_wight_marshal': (None, True, 'boss'),
        'enemy_khazad_tarn_sentry_stones': ('Khazad-tarn Sentry Stone', None, 'standard'),
    }


def test_the_careful_route_seals_the_star_shard(table):
    events = _recording(table.socket)
    start = table.last
    assert flagship._hero(start)['inventory']['currency']['gp'] == 25
    assert _flags(start) == {key: False for key in PACK_FLAGS}
    assert start['campaignPack'].get('storyFlags', []) == []
    assert start['currentScene']['restPolicy'] == 'safe'
    view = flagship._story_view(start)
    assert view['clock']['label'] == 'Day 1 · Evening'
    assert view['currentScene']['name'] == 'The Alarm at Graymere'
    world_map = view['worldMap']
    assert (world_map['title'], world_map['terrain'], world_map['seed']) == ('Graymere Vale and the Blue Mountains', 'mountain', 3117)
    # The hollow both known places border is only a rumour: nameless, and no road yet.
    assert [(node['id'], node['name'], node['icon'], node['x'], node['y'], node['state']) for node in world_map['nodes']] == [
        ('loc_graymere_watch', 'Graymere Watch', 'tower', 14, 72, 'current'),
        ('loc_moonwell_crossing', 'Moonwell Crossing', 'bridge', 32, 60, 'known'),
        ('loc_hollow_of_black_pines', '?', 'unknown', 46, 40, 'rumoured'),
    ]
    assert [(route['from'], route['to'], route['style'], route['travelable']) for route in world_map['routes']] == [
        ('loc_graymere_watch', 'loc_hollow_of_black_pines', 'trail', False),
        ('loc_graymere_watch', 'loc_moonwell_crossing', 'road', True),
        ('loc_hollow_of_black_pines', 'loc_moonwell_crossing', 'trail', False),
    ]
    assert {entry['id']: flagship._offered(view, entry['id']) for entry in view['currentScene']['interactables']} == {
        'watch_couriers_cot': [
            ('search', 'Take up the token the courier carried', True, False),
            ('inspect', 'Look in on the wounded courier', True, False),
        ],
        'watch_ledger': [('inspect', 'Read the watch ledger', True, False)],
        'watch_torins_harp': [('use', 'Ask Torin to play the gate-song', True, False)],
    }
    assert 'contents' not in _story_object(start, 'watch_couriers_cot')
    _assert_player_view_hides_dm_text(start)

    courier = table.act('watch_couriers_cot', 'inspect', "The Courier's Cot")
    assert courier.flags == {'survivors_questioned': True}
    assert courier.objectives == {'obj_question_survivors': 'completed'}
    assert flagship._completed_checkpoints(courier.snapshot) == ['cp_01_graymere_alarm']
    assert SCENE_OBJECTS['watch_couriers_cot']['description'] in table.narration()
    token = table.act('watch_couriers_cot', 'search', "The Courier's Cot")
    assert (token.flags, token.clues) == ({'token_studied': True}, {'clue_broken_dwarf_token'})
    assert (flagship._scene_items(token), token.items) == (['broken_dwarf_token'], Counter())
    assert table.take('broken_dwarf_token', 'Broken Dwarf-token').items == Counter({'Broken Dwarf-token': 1})
    song = table.act('watch_torins_harp', 'use', "Torin's Harp")
    assert song.flags == {'gate_song_learned': True}
    assert SCENE_OBJECTS['watch_torins_harp']['actionText']['use'] in table.narration()

    moonwell = _arrive(table, 'loc_moonwell_crossing', 'Moonwell Crossing', 'enc_moonwell_ambush')
    assert moonwell.places == {'loc_moonwell_crossing': 'visited'}
    assert moonwell.encounter == 'enc_moonwell_ambush'
    assert flagship._enemies(moonwell.snapshot) == ['enemy_palehand_orcs']
    assert ENCOUNTERS['enc_moonwell_ambush']['setup'] in table.narration()
    assert moonwell.snapshot['currentScene']['restPolicy'] == 'risky'
    assert moonwell.snapshot['currentScene']['activeNpcIds'] == ['npc_elenwen_of_lindon', 'npc_matho_burrows']
    ambush = table.fight()
    assert ambush.snapshot['combat']['status'] == 'ended'
    assert (ambush.flags, ambush.xp, ambush.gp) == ({'ambush_resolved': True}, 100, 0)
    well = table.act('moonwell_the_well', 'inspect', 'The Moonwell')
    assert (well.flags, well.clues) == ({'reflection_seen': True}, {'clue_moonwell_reflection'})
    assert (well.objectives, well.places) == ({'obj_trace_caravan': 'completed'}, {'loc_khazad_tarn_gate': 'discovered'})
    assert flagship._active_checkpoint(well.snapshot) == 'cp_03_black_pines'
    wagon = table.act('moonwell_broken_wagon', 'search', 'Broken Caravan Wagon')
    assert table.last_check() == ('investigation', 10, 'success')
    assert (wagon.flags, wagon.places) == ({'caravan_traced': True}, {'loc_hollow_of_black_pines': 'discovered'})
    assert flagship._scene_items(wagon) == ['moonwell_broken_wagon_potion_of_healing', 'moonwell_broken_wagon_coins']
    assert table.take('moonwell_broken_wagon_coins', '8 gp').gp == 8

    pines = _arrive(table, 'loc_hollow_of_black_pines', 'Hollow of Black Pines', 'enc_black_pines_oath')
    assert (pines.encounter, pines.snapshot['combat']['status']) == ('enc_black_pines_oath', 'active')
    assert flagship._enemies(pines.snapshot) == ['enemy_oathbound_dead']
    assert pines.snapshot['currentScene']['restPolicy'] == 'none'
    assert flagship._offered(flagship._story_view(pines.snapshot), 'pines_fallen_banner') == [
        ('use', "Raise the banner and speak the captain's name", False, False)
    ]
    assert _refusals(pines.snapshot, 'pines_fallen_banner') == {'use': (False, "You don't know enough to do that yet.")}
    # The name is found under the moss while the dead hold the line; finding it spends the hero's action.
    cairns = table.act('pines_cairn_stones', 'search', 'Cairn-Stones')
    assert table.last_check() == ('history', 11, 'success')
    assert (cairns.flags, cairns.clues) == ({'banner_name_known': True}, {'clue_thalara_banner_name'})
    assert cairns.snapshot['combat']['status'] == 'active'
    table._hud('Arden Vale ends the turn.', 'combat.end_turn')
    release = table.act('pines_fallen_banner', 'use', 'Fallen War-Banner')
    assert SCENE_OBJECTS['pines_fallen_banner']['actionText']['use'] in table.narration()
    assert (release.flags, release.objectives) == ({'dead_released': True}, {'obj_release_or_bypass_dead': 'completed'})
    assert (release.snapshot['combat']['status'], release.xp) == ('ended', 200)
    assert flagship._rewards(release.snapshot).count(('enc_black_pines_oath', 'negotiation')) == 1

    gate = _arrive(table, 'loc_khazad_tarn_gate', 'Khazad-tarn Gate', 'enc_khazad_tarn_sentries')
    assert (gate.encounter, gate.snapshot['combat']['status'] in {'none', 'ended'}) == (None, True)
    assert table.offers('gate_rune_lintel') == {'break': True, 'inspect': True, 'use': True}
    opened = table.act('gate_rune_lintel', 'use', 'The Rune Lintel')
    assert SCENE_OBJECTS['gate_rune_lintel']['actionText']['use'] in table.narration()
    assert opened.flags == {'gate_opened_by_song': True, 'gate_open': True}
    assert (opened.objectives, opened.places) == ({'obj_open_khazad_tarn': 'completed'}, {'loc_silvervein_deeps': 'discovered'})
    # The song opens the gate before the sentries wake: there is no fight, and nothing to settle.
    assert (opened.encounter, opened.xp) == (None, 0)
    lintel = _refusals(opened.snapshot, 'gate_rune_lintel')
    assert (lintel['use'], lintel['break']) == ((False, 'That can no longer be done.'), (False, 'That can no longer be done.'))

    deeps = _arrive(table, 'loc_silvervein_deeps', 'Silvervein Deeps', 'enc_silvervein_watch_post')
    assert (deeps.encounter, deeps.snapshot['combat']['status'] in {'none', 'ended'}) == (None, True)
    assert _visible_objects(deeps.snapshot) == ['deeps_watch_post', 'deeps_orc_captain', 'deeps_prisoners_cage', 'deeps_dark_channel']
    # Grishnak is listed at the deeps but never met by arriving there.
    assert _met_by(table, 'scene.move_location') is None
    assert 'npc_grishnak_palehand' not in _known_people(deeps.snapshot) | set(deeps.snapshot['currentScene']['activeNpcIds'])
    table.act('deeps_orc_captain', 'inspect', 'The Orc Captain')
    assert SCENE_OBJECTS['deeps_orc_captain']['description'] in table.narration()
    # The captain takes the way out before any blow is struck: the watch-post is settled without a fight.
    turned = table.act('deeps_orc_captain', 'use', 'The Orc Captain')
    assert table.last_check() == ('persuasion', 13, 'success')
    assert SCENE_OBJECTS['deeps_orc_captain']['actionText']['use'] in table.narration()
    assert (turned.flags, turned.xp, turned.encounter) == ({'grishnak_turned': True, 'watch_post_cleared': True}, 150, None)
    assert turned.snapshot['combat']['status'] in {'none', 'ended'}
    assert flagship._rewards(turned.snapshot).count(('enc_silvervein_watch_post', 'negotiation')) == 1
    assert _refusals(turned.snapshot, 'deeps_orc_captain')['use'] == (False, 'That can no longer be done.')
    assert 'contents' not in _story_object(deeps.snapshot, 'deeps_watch_post')
    post = table.act('deeps_watch_post', 'search', 'Orc Watch-Post')
    assert post.flags == {'cage_key_found': True}
    assert flagship._scene_items(post) == ['iron_cage_key', 'deeps_watch_post_coins']
    table.take('iron_cage_key', 'Iron Cage Key')
    assert table.take('deeps_watch_post_coins', '15 gp').gp == 15
    freed = table.act('deeps_prisoners_cage', 'use', "The Prisoners' Cage")
    assert SCENE_OBJECTS['deeps_prisoners_cage']['actionText']['use'] in table.narration()
    assert (freed.flags, freed.objectives) == ({'prisoners_freed': True}, {'obj_free_prisoners': 'completed'})
    assert 'deeps_caravan_strongbox' in _visible_objects(freed.snapshot)
    assert _refusals(freed.snapshot, 'deeps_prisoners_cage')['break'] == (False, 'That can no longer be done.')
    assert flagship._active_checkpoint(freed.snapshot) == 'cp_05_silvervein_deeps'
    strongbox = table.act('deeps_caravan_strongbox', 'search', 'Caravan Strongbox')
    assert flagship._scene_items(strongbox) == ['deeps_caravan_strongbox_potion_of_healing', 'deeps_caravan_strongbox_coins']
    assert table.take('deeps_caravan_strongbox_coins', '30 gp').gp == 30
    channel = table.act('deeps_dark_channel', 'inspect', 'Dark Water Channel')
    assert channel.places == {'loc_chamber_of_the_star_shard': 'discovered'}

    chamber = _arrive(table, 'loc_chamber_of_the_star_shard', 'Chamber of the Star-Shard', 'enc_star_shard_confrontation')
    assert (chamber.places, chamber.encounter) == ({'loc_chamber_of_the_star_shard': 'visited'}, None)
    assert flagship._active_checkpoint(chamber.snapshot) == 'cp_05_silvervein_deeps'
    assert table.offers('chamber_star_shard') == {'break': False, 'inspect': True, 'lock': False, 'trigger': False, 'use': False}
    confrontation = table.act('chamber_star_shard', 'inspect', 'The Star-Shard')
    assert confrontation.encounter == 'enc_star_shard_confrontation'
    # Thalara's company is with the party and the Marshal is weakened; no Orc stands with him.
    scaling = confrontation.snapshot['combat']['flags']['partyScaling']
    assert [adjustment['text'] for adjustment in scaling['adjustments']] == [RELEASED['text'], TURNED['text']]
    assert 'enemy_palehand_orcs' not in [group['enemyId'] for group in [*scaling['groups'], *scaling['droppedGroups']]]
    assert flagship._enemies(confrontation.snapshot) == ['enemy_wight_marshal']
    assert _marshal_hp(confrontation.snapshot) == math.ceil(Fraction(_authored_marshal_hp()) * PARTY_SCALING[1][0] * Fraction(3, 4))
    narration = table.narration()
    assert narration.index(CONFRONTATION['setup']) < narration.index(RELEASED['text']) < narration.index(TURNED['text'])
    instructions = '\n'.join(table.pipeline()['dmContextPacket']['dmInstructions'])
    assert all(text in instructions for text in (CONFRONTATION['setup'], RELEASED['text'], TURNED['text']))
    victory = table.fight()
    assert victory.snapshot['combat']['status'] == 'ended'
    assert (victory.flags, victory.gp, victory.xp) == ({'marshal_resolved': True}, 60, 600)
    assert [(entry['action'], entry.get('choice'), entry['legal']) for entry in _story_object(victory.snapshot, 'chamber_star_shard')['offeredActions']] == [
        ('use', True, True), ('trigger', True, True), ('inspect', None, True), ('lock', True, True), ('break', True, True),
    ]

    sealed = table.act('chamber_star_shard', 'use', 'The Star-Shard')
    assert SHARD['actionText']['use'] in table.narration()
    assert sealed.flags == {'shard_sealed': True, 'quest_shard_complete': True}
    assert sealed.objectives == {'obj_secure_star_shard': 'completed'}
    assert _quest_status(sealed.snapshot) == 'completed'
    assert (sealed.gp, sealed.xp, sealed.items["Graymere Warden's Brooch"]) == (50, 600, 1)
    assert flagship._completed_checkpoints(sealed.snapshot)[-1] == 'cp_05_silvervein_deeps'
    assert flagship._active_checkpoint(sealed.snapshot) == FINAL_CHECKPOINT['id']
    ending = _ending_for('shard_sealed')
    assert flagship._ending(sealed.snapshot) == ending
    story = flagship._story_state(table)
    assert story['campaignPack']['ending'] == ending
    assert (story['journal']['quest']['stageTitle'], story['journal']['quest']['stageSummary']) == (ending['title'], ending['text'])
    assert {'kind': 'ending', 'text': 'The story ends — The Mountain Sleeps'} in table.pipeline_beats()
    _assert_no_ending_before_the_fate(table)
    # One fate: once it is chosen no other is offered, and nothing is paid twice.
    shard = _refusals(sealed.snapshot, 'chamber_star_shard')
    assert {action: shard[action] for action in FATES} == {action: (False, 'That can no longer be done.') for action in FATES}
    again = table.act('chamber_star_shard', 'inspect', 'The Star-Shard')
    assert (again.encounter, again.gp, again.xp, again.flags) == (None, 0, 0, {})
    assert flagship._rewards(again.snapshot) == [
        ('enc_black_pines_oath', 'negotiation'),
        ('enc_moonwell_ambush', 'victory'),
        ('enc_silvervein_watch_post', 'negotiation'),
        ('enc_star_shard_confrontation', 'victory'),
    ]

    # The careful route finds every clue, reaches every place and completes every objective.
    assert {clue['id'] for clue in again.snapshot['clues']} == {clue['id'] for clue in PACK['clues']}
    nodes = {node['id']: node['state'] for node in flagship._story_view(again.snapshot)['worldMap']['nodes']}
    assert nodes == {**{location['id']: 'visited' for location in PACK['locations']}, 'loc_chamber_of_the_star_shard': 'current'}
    assert set(_objectives(again.snapshot).values()) == {'completed'}
    met = {person['id'] for person in story['journal']['people']}
    assert ('npc_thalara_oathbound' in met, 'npc_grishnak_palehand' in met) == (True, False)
    _assert_player_view_hides_dm_text(again.snapshot)
    _assert_reads_cleanly(table, events)


def test_the_forceful_route_forces_the_gate_and_shatters_the_shard(table):
    events = _recording(table.socket)
    token = table.act('watch_couriers_cot', 'search', "The Courier's Cot")
    assert token.objectives == {'obj_question_survivors': 'completed'}
    ledger = table.act('watch_ledger', 'inspect', 'Watch Ledger')
    assert SCENE_OBJECTS['watch_ledger']['description'] in table.narration()
    assert (ledger.flags, ledger.places) == ({'ranger_marks_read': True}, {'loc_hollow_of_black_pines': 'discovered'})

    pines = table.travel('loc_hollow_of_black_pines', 'Hollow of Black Pines')
    assert pines.encounter == 'enc_black_pines_oath'
    assert flagship._active_checkpoint(pines.snapshot) == 'cp_03_black_pines'
    passed = table.fight()
    assert passed.snapshot['combat']['status'] == 'ended'
    assert (passed.flags, passed.places) == ({'dead_passed': True}, {'loc_khazad_tarn_gate': 'discovered'})
    assert (passed.objectives, passed.xp) == ({'obj_release_or_bypass_dead': 'completed'}, 200)

    gate = table.travel('loc_khazad_tarn_gate', 'Khazad-tarn Gate')
    assert gate.encounter is None
    table.act('gate_rune_lintel', 'inspect', 'The Rune Lintel')
    assert SCENE_OBJECTS['gate_rune_lintel']['description'] in table.narration()
    assert table.offers('gate_rune_lintel') == {'break': True, 'inspect': True, 'use': False}
    shallows = table.act('gate_tarn_shallows', 'search', 'Tarn Shallows')
    assert table.last_check() == ('perception', 12, 'success')
    assert SCENE_OBJECTS['gate_tarn_shallows']['description'] in table.narration()
    assert flagship._scene_items(shallows) == ['gate_tarn_shallows_coins']
    assert table.take('gate_tarn_shallows_coins', '12 gp').gp == 12
    forced = table.act('gate_rune_lintel', 'break', 'The Rune Lintel')
    assert (forced.encounter, forced.flags) == ('enc_khazad_tarn_sentries', {})
    assert flagship._enemies(forced.snapshot) == ['enemy_khazad_tarn_sentry_stones']
    assert ENCOUNTERS['enc_khazad_tarn_sentries']['setup'] in table.narration()
    sentries = table.fight()
    assert sentries.snapshot['combat']['status'] == 'ended'
    assert sentries.flags == {'gate_forced': True, 'gate_open': True}
    assert (sentries.objectives, sentries.places, sentries.xp) == (
        {'obj_open_khazad_tarn': 'completed'},
        {'loc_silvervein_deeps': 'discovered'},
        200,
    )
    assert ('enc_khazad_tarn_sentries', 'victory') in flagship._rewards(sentries.snapshot)

    deeps = table.travel('loc_silvervein_deeps', 'Silvervein Deeps')
    assert deeps.encounter == 'enc_silvervein_watch_post'
    assert flagship._enemies(deeps.snapshot) == ['enemy_palehand_orcs']
    assert deeps.snapshot['combat']['flags']['allowParley'] is True
    assert ENCOUNTERS['enc_silvervein_watch_post']['setup'] in table.narration()
    # Mid-fight, the hero offers the captain a way out; it spends their action and ends the fight.
    parley = table.act('deeps_orc_captain', 'use', 'The Orc Captain')
    assert table.last_check() == ('persuasion', 13, 'success')
    assert SCENE_OBJECTS['deeps_orc_captain']['actionText']['use'] in table.narration()
    assert (parley.flags, parley.xp) == ({'grishnak_turned': True, 'watch_post_cleared': True}, 150)
    assert parley.snapshot['combat']['status'] == 'ended'
    assert flagship._rewards(parley.snapshot).count(('enc_silvervein_watch_post', 'negotiation')) == 1
    table.act('deeps_dark_channel', 'inspect', 'Dark Water Channel')

    chamber = table.travel('loc_chamber_of_the_star_shard', 'Chamber of the Star-Shard')
    assert chamber.encounter is None
    assert _refusals(chamber.snapshot, 'chamber_dwarf_coffer') == {'search': (False, "You don't know enough to do that yet.")}
    confrontation = table.act('chamber_star_shard', 'inspect', 'The Star-Shard')
    assert confrontation.encounter == 'enc_star_shard_confrontation'
    # Grishnak's word holds: no Orc stands with the Marshal, whatever the party's size.
    scaling = confrontation.snapshot['combat']['flags']['partyScaling']
    assert [adjustment['text'] for adjustment in scaling['adjustments']] == [TURNED['text']]
    assert 'enemy_palehand_orcs' not in [group['enemyId'] for group in [*scaling['groups'], *scaling['droppedGroups']]]
    assert flagship._enemies(confrontation.snapshot) == ['enemy_wight_marshal']
    assert _marshal_hp(confrontation.snapshot) == math.ceil(Fraction(_authored_marshal_hp()) * PARTY_SCALING[1][0])
    assert TURNED['text'] in table.narration() and RELEASED['text'] not in table.narration()
    assert table.fight().flags == {'marshal_resolved': True}
    coffer = table.act('chamber_dwarf_coffer', 'search', 'Dwarf Coffer')
    assert flagship._scene_items(coffer) == ['chamber_dwarf_coffer_potion_of_healing', 'chamber_dwarf_coffer_coins']
    assert table.take('chamber_dwarf_coffer_coins', '60 gp').gp == 60

    shattered = table.act('chamber_star_shard', 'break', 'The Star-Shard')
    assert SHARD['actionText']['break'] in table.narration()
    assert shattered.flags == {'shard_shattered': True, 'quest_shard_complete': True}
    assert _quest_status(shattered.snapshot) == 'completed'
    assert flagship._ending(shattered.snapshot) == _ending_for('shard_shattered')
    _assert_no_ending_before_the_fate(table)
    assert table.offers('chamber_star_shard') == {'break': False, 'inspect': True, 'lock': False, 'trigger': False, 'use': False}
    _assert_reads_cleanly(table, events)


def test_the_low_road_takes_the_shard_without_opening_the_gate(table, monkeypatch):
    events = _recording(table.socket)
    table.act('watch_couriers_cot', 'inspect', "The Courier's Cot")
    table.act('watch_ledger', 'inspect', 'Watch Ledger')
    assert table.travel('loc_hollow_of_black_pines', 'Hollow of Black Pines').encounter == 'enc_black_pines_oath'
    assert table.fight().flags == {'dead_passed': True}

    culvert = table.act('pines_mossed_culvert', 'inspect', 'Mossed Culvert')
    assert table.last_check() == ('survival', 13, 'success')
    assert (culvert.flags, culvert.places) == ({'channel_known': True}, {'loc_silvervein_deeps': 'discovered'})
    routes = flagship._story_view(culvert.snapshot)['worldMap']['routes']
    assert {'from': 'loc_hollow_of_black_pines', 'to': 'loc_silvervein_deeps', 'style': 'hidden', 'travelable': True} in routes

    deeps = table.travel('loc_silvervein_deeps', 'Silvervein Deeps')
    assert deeps.places == {'loc_silvervein_deeps': 'visited'}
    assert flagship._active_checkpoint(deeps.snapshot) == 'cp_05_silvervein_deeps'
    assert deeps.encounter == 'enc_silvervein_watch_post'
    # The captain will not hear it this time: the check fails and the fight goes on.
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 2)
    refused = table.act('deeps_orc_captain', 'use', 'The Orc Captain')
    assert table.last_check() == ('persuasion', 13, 'failure')
    assert (refused.flags, refused.xp, refused.snapshot['combat']['status']) == ({}, 0, 'active')
    assert SCENE_OBJECTS['deeps_orc_captain']['actionText']['use'] not in table.narration()
    assert ('enc_silvervein_watch_post', 'negotiation') not in flagship._rewards(refused.snapshot)
    table._hud('Arden Vale ends the turn.', 'combat.end_turn')
    cleared = table.fight()
    assert cleared.snapshot['combat']['status'] == 'ended'
    assert (cleared.flags, cleared.xp) == ({'watch_post_cleared': True}, 150)
    table.act('deeps_dark_channel', 'inspect', 'Dark Water Channel')
    table.travel('loc_chamber_of_the_star_shard', 'Chamber of the Star-Shard')

    confrontation = table.act('chamber_star_shard', 'inspect', 'The Star-Shard')
    assert confrontation.encounter == 'enc_star_shard_confrontation'
    assert 'adjustments' not in confrontation.snapshot['combat']['flags']['partyScaling']
    assert table.fight().flags == {'marshal_resolved': True}
    # Without the gate-song and the half-token the shard cannot be sealed; the other fates stand open.
    assert table.offers('chamber_star_shard') == {'break': True, 'inspect': True, 'lock': True, 'trigger': True, 'use': False}

    taken = table.act('chamber_star_shard', 'trigger', 'The Star-Shard')
    assert SHARD['actionText']['trigger'] in table.narration()
    assert taken.flags == {'shard_taken': True, 'quest_shard_complete': True}
    assert _quest_status(taken.snapshot) == 'completed'
    assert flagship._ending(taken.snapshot) == _ending_for('shard_taken')
    _assert_no_ending_before_the_fate(table)
    assert (taken.snapshot['flags']['gate_open'], taken.snapshot['flags']['prisoners_freed']) == (False, False)
    assert {'npc_thalara_oathbound', 'npc_grishnak_palehand'}.isdisjoint(_known_people(taken.snapshot))
    assert {key: _objectives(taken.snapshot)[key] for key in ('obj_trace_caravan', 'obj_open_khazad_tarn', 'obj_free_prisoners')} == {
        'obj_trace_caravan': 'open',
        'obj_open_khazad_tarn': 'open',
        'obj_free_prisoners': 'open',
    }
    _assert_reads_cleanly(table, events)


@pytest.mark.parametrize('action', list(FATES))
def test_each_fate_is_told_in_the_shards_own_words_and_names_its_ending(table, action):
    hero_id = flagship._hero(table.last)['id']
    table.operator(
        [
            *({'type': 'flag.set', 'flagKey': key, 'flagValue': True} for key in ('marshal_resolved', 'gate_song_learned')),
            {
                'type': 'inventory.add',
                'actorId': hero_id,
                'itemName': 'Broken Dwarf-token',
                'item': {'id': 'broken_dwarf_token', 'name': 'Broken Dwarf-token', 'quantity': 1, 'type': 'quest'},
                'quantity': 1,
            },
            {'type': 'location.discover', 'locationId': 'loc_chamber_of_the_star_shard'},
            {'type': 'scene.move_location', 'locationId': 'loc_chamber_of_the_star_shard', 'name': 'Chamber of the Star-Shard'},
        ]
    )
    override = table.client.post(
        f"/api/sessions/{table.ids['session_id']}/campaign-pack/progress",
        json={'action': 'override', 'checkpointId': 'cp_05_silvervein_deeps', 'reason': 'Seat the party in the deeps'},
    )
    assert override.status_code == 200, override.get_json()

    fate = table.act('chamber_star_shard', action, 'The Star-Shard')

    assert SHARD['actionText'][action] in table.narration()
    assert fate.flags == {FATES[action]: True, 'quest_shard_complete': True}
    assert flagship._ending(fate.snapshot) == _ending_for(FATES[action])


def test_brennas_stores_sell_at_the_watch(table):
    stores = table.last['merchants']['merchant_brennas_stores']
    assert (stores['buyRate'], stores['restock']) == (0.5, 'long_rest')
    assert [(entry['catalog'], entry['quantity']) for entry in stores['stock']] == [
        ('potion_healing', 2),
        ('healers_kit', 1),
        ('ration', None),
        ('torch', None),
        ('rope', None),
    ]

    kit = table.buy('merchant_brennas_stores', 'merchant_brennas_stores_1', "a Healer's Kit")

    assert (kit.gp, kit.items) == (-5, Counter({"Healer's Kit": 1}))
    assert kit.snapshot['merchants']['merchant_brennas_stores']['stock'][1]['quantity'] == 0
    # A ready-made hero sets out with 25 gp, short of a potion's price.
    shop = {'action': 'buy', 'merchant_id': 'merchant_brennas_stores', 'stock_id': 'merchant_brennas_stores_0', 'quantity': 1}
    assert table.rejected('Arden Vale buys a Potion of Healing.', {'kind': 'shop', 'source': 'scene_panel', 'shop': shop}) == [
        'Arden Vale cannot afford 1 Potion of Healing for 50 gp; they have 20 gp.'
    ]


def test_the_people_of_the_vale_are_met_where_they_live_and_tell_what_they_know(table):
    events = _recording(table.socket)
    knows = {npc['id']: npc['knows'] for npc in PACK['npcs'] if 'knows' in npc}
    assert {location['id']: location['npcIds'] for location in PACK['locations'] if 'npcIds' in location} == {
        'loc_graymere_watch': ['npc_brenna_graymere', 'npc_torin_stoneharp'],
        'loc_moonwell_crossing': ['npc_elenwen_of_lindon', 'npc_matho_burrows'],
        'loc_hollow_of_black_pines': ['npc_calanor_ranger', 'npc_thalara_oathbound'],
        'loc_silvervein_deeps': ['npc_grishnak_palehand'],
    }

    def told(name: str, npc_id: str) -> None:
        for fact in knows[npc_id]:
            # The fact's last full stop becomes the comma before "<name> tells you".
            assert fact[:-1] in table.talk(f'Arden Vale asks {name} what they know.')
        assert 'has nothing more to add' in table.talk(f'Arden Vale asks {name} what else they know.')

    told('Brenna', 'npc_brenna_graymere')
    told('Torin', 'npc_torin_stoneharp')
    # Talk changes nothing: "Half a key opens nothing" opens no gate, and Brenna's marks are not read.
    assert _flags(table.snapshot()) == {key: False for key in PACK_FLAGS}

    table.travel('loc_moonwell_crossing', 'Moonwell Crossing')
    assert _met_by(table, 'scene.move_location') == ['npc_elenwen_of_lindon', 'npc_matho_burrows']
    assert [beat['text'] for beat in table.pipeline_beats() if beat['kind'] == 'met'] == [
        'Met — Elenwen of Lindon',
        'Met — Matho Burrows',
    ]
    assert _talk_offers(table.fight().snapshot) == ['Talk to Elenwen of Lindon', 'Talk to Matho Burrows']
    # The last orc's end is the hero's to tell; they let the story tell it before talking.
    assert table.talk('Let the story tell it') == 'DM: The pale hand orc falls.'
    told('Elenwen', 'npc_elenwen_of_lindon')
    told('Matho', 'npc_matho_burrows')

    table.act('moonwell_broken_wagon', 'search', 'Broken Caravan Wagon')
    pines = table.travel('loc_hollow_of_black_pines', 'Hollow of Black Pines')
    # Calanor is met on arrival; Thalara waits until her dead are released.
    assert _met_by(table, 'scene.move_location') == ['npc_calanor_ranger']
    assert 'npc_thalara_oathbound' not in _known_people(pines.snapshot) | set(pines.snapshot['currentScene']['activeNpcIds'])
    table.act('pines_cairn_stones', 'search', 'Cairn-Stones')
    table._hud('Arden Vale ends the turn.', 'combat.end_turn')
    released = table.act('pines_fallen_banner', 'use', 'Fallen War-Banner')
    assert {'kind': 'met', 'text': 'Met — Thalara the Oathbound', 'refId': 'npc_thalara_oathbound'} in table.pipeline_beats()
    assert 'npc_thalara_oathbound' in _known_people(released.snapshot)
    # With the fight in the hollow over, both can be talked to.
    assert _talk_offers(released.snapshot) == ['Talk to Calanor the Green-cloaked', 'Talk to Thalara the Oathbound']
    told('Calanor', 'npc_calanor_ranger')
    told('Thalara', 'npc_thalara_oathbound')
    _assert_player_view_hides_dm_text(table.snapshot())
    _assert_reads_cleanly(table, events)


def test_a_lone_ready_made_hero_wins_every_fight_at_the_careful_routes_level(monkeypatch):
    for encounter_id, flags in ROUTE_FLAGS.items():
        monkeypatch.setitem(solo_balance.ROUTE_FLAGS, encounter_id, flags)

    rates = {
        (preset.character_id, encounter_id): sum(
            solo_balance.fight(preset.character_id, encounter_id, seed, level=level_for_xp(xp), pack=PACK) for seed in range(1, 41)
        ) / 40
        for preset in PREGENERATED_CHARACTER_PRESETS
        for encounter_id, xp in ROUTE_XP
    }

    assert len(rates) == 4 * len(ENCOUNTERS)
    assert {cell: rate for cell, rate in rates.items() if rate < 0.5} == {}
