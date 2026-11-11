"""Shadow over the Greenway, played end to end in Safe Mode with typed actions only.

Built like ``tests/test_flagship_adventure.py``: one ready-made hero at the table,
acting through the intents the client sends over the socket, with that file's
seeded dice (object checks roll 15, the hero rolls 15 to hit, enemies roll 1).
Every step also checks the player view (no hidden objects, no contents before a
search, no Dungeon Master text), and each route ends with the release smoke's
scan of every narration and beat it produced.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
import requests

from aidm_server.combat.state import instantiate_creature
from aidm_server.game_state.campaign_pack_encounters import PARTY_SCALING, materialize_campaign_pack_combat_start
from aidm_server.game_state.leveling import level_for_xp
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_file
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from aidm_server.services.pregen_characters import PREGENERATED_CHARACTER_PRESETS
from scripts import solo_balance
from tests import test_flagship_adventure as flagship
from tests import test_play_now_release_smoke as smoke

PACK_PATH = Path(__file__).resolve().parents[1] / 'docs' / 'examples' / 'shadow_over_the_greenway_campaign_pack.json'
PACK = json.loads(PACK_PATH.read_text(encoding='utf-8'))
PACK_ID = 'middle_earth.shadow_over_the_greenway'
QUEST = 'quest_shadow_over_greenway'
PACK_FLAGS = set(PACK['startingState']['flags'])
SCENE_OBJECTS = {record['id']: record for location in PACK['locations'] for record in location.get('interactables', [])}
ENCOUNTERS = {encounter['id']: encounter for encounter in PACK['encounters']}
RITE = ENCOUNTERS['enc_shadow_rite_amon_sul']
OATH_STONE = SCENE_OBJECTS['camp_oath_stone']
FATES = ['use', 'lock', 'break', 'trigger']
FINAL_CHECKPOINT = next(checkpoint for checkpoint in PACK['checkpoints'] if checkpoint.get('terminal'))
ENDING_VARIANTS = {variant['when']['flagKey']: variant for variant in FINAL_CHECKPOINT['endingVariants']}
SAERETH = 'enemy_saereth_shadow_emissary'
SCOUT = 'enemy_black_feather_orc_scout'

GATE = ('loc_bree_west_gate', 'Bree West Gate')
INN = ('loc_prancing_pony_common_room', 'The Prancing Pony Common Room')
MARSH = ('loc_midgewater_causeway', 'Midgewater Causeway')
TOWER = ('loc_fornost_watch_ruin', 'Ruined Watchtower of Fornost Road')
DOWNS = ('loc_barrow_downs_outer_ring', 'Outer Ring of the Barrow-downs')
CAMP = ('loc_amon_sul_shadow_camp', 'Shadow Camp below Amon Sûl')

# Every fight, with the XP the careful route holds on reaching its place (the careful route asserts these);
# the robbed barrow's wight is met at the Barrow-downs, like the cold memory.
ROUTE_XP = (
    ('enc_marsh_black_feathers', 0),
    ('enc_barrow_cold_memory', 100),
    ('enc_barrow_wight_roused', 100),
    ('enc_shadow_rite_amon_sul', 300),
)
# What the careful route knows when it reaches the rite: both of its adjustments apply.
CAREFUL_RITE_FLAGS = {'true_name_known': True, 'barrow_blessing': True}
BALANCE_SEEDS = 40


def _dm_only_texts() -> list[str]:
    """Every authored string the player view must never carry."""

    texts = [PACK['gmNotes'], PACK['hiddenSceneNotes']]
    for key in ('locations', 'npcs', 'quests', 'enemies', 'encounters', 'segments', 'checkpoints', 'clues', 'factions', 'maps', 'handouts', 'lore'):
        texts.extend(record['gmNotes'] for record in PACK[key] if record.get('gmNotes'))
    for record in SCENE_OBJECTS.values():
        texts.append(record['description'])
        texts.extend(record.get('actionText', {}).values())
    for npc in PACK['npcs']:
        texts.extend(npc.get('knows', []))
        if not npc.get('visibleAtStart'):
            texts.extend([npc['description'], npc['playerSummary']])
    for encounter in PACK['encounters']:
        texts.extend(encounter[key] for key in ('setup', 'stakes') if encounter.get(key))
        texts.extend(adjustment['text'] for adjustment in encounter.get('adjustments', []))
    texts.extend(lore['summary'] for lore in PACK['lore'] if lore.get('hiddenToPlayers'))
    return texts


DM_ONLY_TEXTS = _dm_only_texts()


def _found_by_actions() -> tuple[set[str], set[str], set[str]]:
    """The clues, places and flags that object actions and fight outcomes find, reveal and set (places include the start)."""

    clues, places, flags = set(), set(PACK['startingState']['knownLocationIds']), set()
    for record in SCENE_OBJECTS.values():
        clues.update(clue for found in record.get('discoversClueIds', {}).values() for clue in found)
        places.update(place for found in record.get('discoversLocationIds', {}).values() for place in found)
        flags.update(flag for found in record.get('setsFlags', {}).values() for flag in found)
    for encounter in PACK['encounters']:
        for outcome in encounter.get('outcomes', {}).values():
            for consequence in outcome['consequences']:
                {'clue.discover': clues, 'location.discover': places, 'flag.set': flags}[consequence['type']].add(
                    consequence.get('clueId') or consequence.get('locationId') or consequence.get('flagKey')
                )
    return clues, places, flags


def _flags(snapshot: dict) -> dict[str, Any]:
    return {key: value for key, value in snapshot['flags'].items() if key in PACK_FLAGS}


def _objectives(snapshot: dict) -> dict[str, str]:
    quest = next(quest for quest in snapshot['quests'] if quest['id'] == QUEST)
    return {objective['id']: objective.get('status', 'open') for objective in quest['objectives']}


def _item_ids(snapshot: dict) -> set[str]:
    return {item['id'] for item in flagship._hero(snapshot)['inventory']['items']}


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


def _assert_player_view_is_clean(snapshot: dict) -> None:
    """No hidden object, no contents before a search and no Dungeon Master text in what the player sees."""

    view = flagship._story_view(snapshot)
    rendered = json.dumps(view, ensure_ascii=False)
    assert [text for text in DM_ONLY_TEXTS if text in rendered] == []
    objects = {entry['id']: entry for entry in view['currentScene']['interactables']}
    if snapshot['flags'].get('watchtower_decoded') is not True:
        assert 'tower_rangers_cache' not in objects
    for entry in objects.values():
        assert 'contents' not in entry or entry.get('searched') is True, entry['id']
    for item in view['currentScene']['items']:
        assert not item.get('containerId') or objects[item['containerId']].get('searched') is True, item['id']


class GreenwayTable(flagship.Table):
    """The flagship table, reading this pack's flags and quest and checking the player view after every step."""

    def __init__(self, app, client, socket_client, ids: dict[str, Any]):
        self.events = smoke._recording(socket_client)
        # (whether the last intent was a fate of the oath-stone, the ending the player view reports) per step.
        self.endings: list[tuple[bool, dict | None]] = []
        self._last_intent: dict = {}
        super().__init__(app, client, socket_client, ids)

    def send(self, message: str, intent: dict) -> flagship.Change:
        self._last_intent = intent
        return super().send(message, intent)

    def _changed(self, since: dict | None = None) -> flagship.Change:
        after = self.snapshot()
        change = _diff(self.last if since is None else since, after)
        self.last = after
        _assert_player_view_is_clean(after)
        target = self._last_intent.get('object') or {}
        fate = target.get('id') == OATH_STONE['id'] and target.get('action') in FATES
        self.endings.append((fate, flagship._ending(after)))
        return change

    def talk(self, name: str) -> str:
        """Talk to a person by name, as typed; returns what the narrator said."""

        self.send(f'Talk to {name}', {'kind': 'message'})
        return self.narration()

    def refusals(self, message: str, intent: dict) -> list[str]:
        """The refusals an intent draws (it changes nothing)."""

        self.turn += 1
        self.socket.emit(
            'send_message',
            {
                'session_id': self.ids['session_id'],
                'campaign_id': self.ids['campaign_id'],
                'player_id': self.ids['player_id'],
                'message': message,
                'client_message_id': f'greenway-refused-{self.turn}',
                'action_intent': {**intent, 'text': message},
            },
        )
        return [event['args'][0]['error'] for event in self.socket.get_received() if event['name'] == 'error' and event['args']]

    def reads_cleanly(self) -> None:
        """The release smoke's scan: no raw id, change type or engine phrase in any narration or beat so far."""

        smoke._assert_reads_cleanly(self, self.events)

    def no_ending_before_a_fate(self) -> dict:
        """No step reported an ending until a fate of the oath-stone was chosen; returns the ending that step reported."""

        first_fate = next(index for index, (fate, _ending) in enumerate(self.endings) if fate)
        assert [ending for _fate, ending in self.endings[:first_fate] if ending] == []
        return self.endings[first_fate][1]


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
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 14 if sides == 20 else sides - 1)
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda _sides: 1)

    response = client.post('/api/onboarding/play-now', json={'example_pack_id': PACK_ID})
    assert response.status_code == 201, response.get_data(as_text=True)
    ids = {key: response.get_json()[key] for key in ('session_id', 'campaign_id', 'player_id', 'workspace_id')}
    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit(
        'join_session',
        {'session_id': ids['session_id'], 'player_id': ids['player_id'], 'workspace_id': ids['workspace_id']},
    )
    socket_client.get_received()
    yield GreenwayTable(app, client, socket_client, ids)
    socket_client.disconnect()


def _ending_for(flag_key: str) -> dict:
    variant = ENDING_VARIANTS[flag_key]
    return {
        'checkpointId': FINAL_CHECKPOINT['id'],
        'title': variant['title'],
        'summary': FINAL_CHECKPOINT['playerSummary'],
        'text': variant['text'],
    }


def _scaling(snapshot: dict) -> dict:
    return snapshot['combat']['flags']['partyScaling']


def _scaled_enemy_ids(scaling: dict) -> list[str]:
    """Every enemy group the fight was authored with that no adjustment removed (kept or dropped for party size)."""

    return [group['enemyId'] for group in [*scaling['groups'], *scaling['droppedGroups']]]


def _rite_for_a_party_of_four(snapshot: dict) -> list[str]:
    """The enemies the rite would field, from this state, against four heroes."""

    hero = flagship._hero(snapshot)
    party = [hero, *({**hero, 'id': f'player_{index}', 'playerId': index} for index in (91, 92, 93))]
    start = {'type': 'combat.start', 'campaignPackEncounterId': RITE['id'], 'combat': {}}
    return flagship._enemies(materialize_campaign_pack_combat_start({**snapshot, 'playerCharacters': party}, start))


def _participant(snapshot: dict, enemy_id: str) -> dict:
    return next(entry for entry in snapshot['combat']['participants'] if entry.get('campaignPackEnemyId') == enemy_id)


def _authored_hp(enemy_id: str) -> int:
    enemy = next(enemy for enemy in PACK['enemies'] if enemy['id'] == enemy_id)
    return instantiate_creature(enemy, instance_id='probe', team='enemy')['hp']['max']


def _fate_reasons(table: GreenwayTable) -> dict[str, tuple[bool, str | None]]:
    offered = {entry['action']: entry for entry in flagship._story_entry(table, OATH_STONE['id'])['offeredActions']}
    return {action: (offered[action]['legal'], offered[action].get('reason')) for action in FATES}


def _short_route_to_the_freed_stone(table: GreenwayTable) -> None:
    """Tracks, the causeway, the way-marker, the fallen stair and the goat path; the rite fought and won."""

    tracks = table.act('gate_road_tracks', 'inspect', 'Tracks in the Road Mud')
    assert table.last_check() == ('survival', 11, 'success')
    assert tracks.flags == {'courier_trail_found': True}
    assert tracks.places == {MARSH[0]: 'discovered'}

    marsh = table.travel(*MARSH)
    assert marsh.encounter == 'enc_marsh_black_feathers'
    assert table.fight().snapshot['combat']['status'] == 'ended'
    marker = table.act('marsh_sunken_marker', 'inspect', 'Sunken Way-Marker')
    assert marker.clues == {'clue_greenway_stone_marks'}
    assert marker.places == {TOWER[0]: 'discovered'}

    tower = table.travel(*TOWER)
    assert tower.places == {TOWER[0]: 'visited'}
    stair = table.act('tower_fallen_stair', 'use', 'Fallen Stair')
    assert table.last_check() == ('athletics', 13, 'success')
    assert stair.flags == {'goat_path_known': True}
    assert stair.places == {CAMP[0]: 'discovered'}
    assert SCENE_OBJECTS['tower_fallen_stair']['actionText']['use'] in table.narration()
    goat_path = [
        (route['style'], route['travelable'])
        for route in filter_session_snapshot_for_player(stair.snapshot)['worldMap']['routes']
        if {route['from'], route['to']} == {TOWER[0], CAMP[0]}
    ]
    assert goat_path == [('hidden', True)]

    camp = table.travel(*CAMP)
    assert (camp.encounter, camp.snapshot['currentScene']['restPolicy']) == (None, 'none')
    rite = table.act(OATH_STONE['id'], 'inspect', OATH_STONE['name'])
    assert rite.encounter == RITE['id']
    # The party learned nothing that weakens the rite: no adjustment, and the scouts are only
    # left out for a lone hero's party size; a party of four meets Saereth and both scouts.
    scaling = _scaling(rite.snapshot)
    assert 'adjustments' not in scaling
    assert _scaled_enemy_ids(scaling) == [SAERETH, SCOUT]
    assert flagship._enemies(rite.snapshot) == [SAERETH]
    assert _rite_for_a_party_of_four(rite.snapshot) == [SCOUT, SCOUT, SAERETH]
    assert all(adjustment['text'] not in table.narration() for adjustment in RITE['adjustments'])
    victory = table.fight()
    assert victory.snapshot['combat']['status'] == 'ended'
    assert victory.flags == {'saereth_resolved': True}
    # Without the broken seal there is nothing to restore it with.
    assert table.offers(OATH_STONE['id']) == {'inspect': True, 'use': False, 'lock': True, 'break': True, 'trigger': True}


def test_the_pack_lints_strictly_clean_and_every_clue_place_and_objective_has_a_player_action(app):
    with app.app_context():
        result = lint_campaign_pack_file(PACK_PATH, workspace_id='owner')
    assert result['issues'] == []
    assert set(PACK['directorRules']) == flagship.ENGINE_DIRECTOR_RULES
    assert len(PACK['gmNotes']) <= 700

    clues, places, flags = _found_by_actions()
    assert clues == {clue['id'] for clue in PACK['clues']}
    assert places == {location['id'] for location in PACK['locations']}
    assert flags == PACK_FLAGS
    quest = next(quest for quest in PACK['quests'] if quest['id'] == QUEST)
    for objective in quest['objectives']:
        predicate = objective['completeWhen']
        assert {entry['flagKey'] for entry in predicate.get('anyOf', [predicate])} <= flags, objective['id']
    # Gate and inn are known at start; every other place is found, never announced on arrival.
    assert PACK['startingState']['knownLocationIds'] == [GATE[0], INN[0]]
    assert all('onEnter' not in location for location in PACK['locations'])


def test_narration_may_set_no_flag_that_an_object_or_a_fight_sets():
    # The post-narration heuristic sets a declared story flag whenever its key words appear in two
    # sentences ("stone ... broken"), so a flag the mechanics set is never declared for narration.
    _clues, _places, flags = _found_by_actions()
    declared = {flag['key'] for flag in PACK.get('storyFlags', [])}

    assert declared & flags == set()
    assert PACK['startingState']['flags'] == {key: False for key in flags}


def test_every_object_offers_exactly_its_labelled_actions_and_every_enemy_is_named():
    for record in SCENE_OBJECTS.values():
        assert set(record['allowedActions']) == set(record['actionLabels']), record['id']
        assert all(len(label) <= 48 for label in record['actionLabels'].values())
        assert record['playerDescription'] != record['description']
    names = {enemy['id']: (enemy['name'], enemy.get('singularName'), enemy.get('proper'), enemy['challengeTier']) for enemy in PACK['enemies']}
    assert names == {
        SCOUT: ('Black-Feather Orc Scout', 'Black-Feather Orc Scout', None, 'easy'),
        'enemy_barrow_cold_wight': ('Barrow-cold Wight', 'Barrow-cold Wight', None, 'standard'),
        SAERETH: ('Saereth the Ash-Mouthed', None, True, 'boss'),
    }


def test_the_opening_offers_only_authored_actions_in_the_evening_at_a_safe_gate(table):
    start = table.last
    view = flagship._story_view(start)

    assert view['clock']['label'] == 'Day 1 · Evening'
    assert start['currentScene']['restPolicy'] == 'safe'
    assert _flags(start) == {key: False for key in PACK_FLAGS}
    assert start['campaignPack']['storyFlags'] == []
    assert {entry['id']: flagship._offered(view, entry['id']) for entry in view['currentScene']['interactables']} == {
        'gate_abandoned_wagon': [
            ('search', 'Search the wagon bed', True, False),
            ('inspect', 'Look over the abandoned wagon', True, False),
        ],
        'gate_road_tracks': [('inspect', 'Read the tracks in the mud', True, False)],
        'gate_wardens_tally': [('inspect', "Read the gatewarden's tally", True, False)],
    }
    world_map = view['worldMap']
    assert (world_map['title'], world_map['terrain'], world_map['seed']) == ('Bree-land and the Lone-lands', 'plains', 2041)
    assert [(node['id'], node['icon'], node['x'], node['y'], node['state']) for node in world_map['nodes']] == [
        (GATE[0], 'gate', 20, 72, 'current'),
        (INN[0], 'inn', 27, 62, 'known'),
        (MARSH[0], 'unknown', 50, 57, 'rumoured'),
    ]
    assert world_map['routes'] == [
        {'from': GATE[0], 'to': MARSH[0], 'style': 'trail', 'travelable': False},
        {'from': GATE[0], 'to': INN[0], 'style': 'road', 'travelable': True},
    ]


def test_the_people_of_bree_tell_what_they_know_one_thing_per_talk(table):
    knows = {npc['name']: npc.get('knows', []) for npc in PACK['npcs']}

    def told(name: str, first: str) -> None:
        for fact in knows[name]:
            assert fact.rstrip('.') in table.talk(first), (name, fact)
        assert table.talk(first) == f'DM: {name} has nothing more to add.'

    told('Tomas Reedbank', 'Tomas')
    told('Calenhir', 'Calenhir')
    table.travel(*INN)
    told('Marla Underhill', 'Marla')
    told('Borin Ironhand', 'Borin')
    told('Elenwen', 'Elenwen')
    # Hadrin is never met; what he knew is in his torn message, read in the reed hide.
    assert knows['Hadrin Vale'] == []
    assert 'npc_hadrin_courier' not in {npc['id'] for npc in table.last['knownNpcs']}
    hide = SCENE_OBJECTS['marsh_reed_hide']['description']
    for fact in (
        'They called the one who leads them the Ash-Mouthed.',
        'The seal is broken, but it still turns cold toward the Weather Hills.',
        'I was to carry it to the Rangers. Let no servant of ash speak over the old stone.',
    ):
        assert fact in hide
    table.reads_cleanly()


def test_the_careful_route_restores_the_oath_stone(table):
    start = table.last
    assert flagship._hero(start)['inventory']['currency']['gp'] == 25

    wagon = table.act('gate_abandoned_wagon', 'inspect', 'Abandoned Wagon')
    assert wagon.flags == {'wagon_inspected': True}
    assert wagon.objectives == {'obj_question_bree_witnesses': 'completed'}
    assert flagship._completed_checkpoints(wagon.snapshot) == ['cp_bree_warning']
    bed = table.act('gate_abandoned_wagon', 'search', 'Abandoned Wagon')
    assert table.last_check() == ('investigation', 10, 'success')
    assert bed.clues == {'clue_black_feather_arrow'}
    assert flagship._scene_items(bed) == ['black_feather_arrow', 'gate_abandoned_wagon_coins']
    assert table.take('black_feather_arrow', 'Black-Feathered Arrow').items['Black-Feathered Arrow'] == 1
    assert table.take('gate_abandoned_wagon_coins', '6 gp').gp == 6
    tracks = table.act('gate_road_tracks', 'inspect', 'Tracks in the Road Mud')
    assert table.last_check() == ('survival', 11, 'success')
    assert (tracks.flags, tracks.places) == ({'courier_trail_found': True}, {MARSH[0]: 'discovered'})

    inn = table.travel(*INN)
    assert inn.snapshot['currentScene']['restPolicy'] == 'safe'
    assert flagship._active_checkpoint(inn.snapshot) == 'cp_prancing_pony_clues'
    corner = table.act('pony_corner_table', 'search', 'Corner Table')
    assert table.last_check() == ('investigation', 11, 'success')
    assert corner.flags == {'inn_rumors_heard': True}
    assert flagship._scene_items(corner) == ['muddy_road_sketch']
    assert table.take('muddy_road_sketch', 'Muddy Road-Sketch').items['Muddy Road-Sketch'] == 1
    rubbing = table.act('pony_road_stone_rubbing', 'inspect', "Borin's Road-Stone Rubbing")
    assert (rubbing.flags, rubbing.clues) == ({'stone_marks_seen': True}, {'clue_greenway_stone_marks'})
    assert rubbing.places == {TOWER[0]: 'discovered'}
    song = table.act('pony_hearthside', 'use', 'The Hearthside')
    assert song.flags == {'oath_lore_heard': True}
    assert SCENE_OBJECTS['pony_hearthside']['actionText']['use'] in table.narration()
    # Marla's counter is open while she is here, but a Potion of Healing costs 50 gp and the
    # careful route reaches the inn with the starting 25 gp and the wagon's 6.
    counter = song.snapshot['merchants']['merchant_pony_counter']
    assert [(entry['catalog'], entry.get('quantity')) for entry in counter['stock']] == [
        ('potion_healing', 2), ('healers_kit', 1), ('antitoxin', 1), ('ration', None), ('torch', None), ('rope', None),
    ]
    assert 'merchant_pony_counter' in {merchant['id'] for merchant in flagship._story_view(song.snapshot)['currentScene']['merchants']}
    potion = {
        'kind': 'shop',
        'source': 'scene_panel',
        'shop': {'action': 'buy', 'merchant_id': 'merchant_pony_counter', 'stock_id': 'merchant_pony_counter_0', 'quantity': 1},
    }
    assert table.refusals('Arden Vale buys a Potion of Healing.', potion) == [
        'Arden Vale cannot afford 1 Potion of Healing for 50 gp; they have 31 gp.'
    ]

    table.travel(*GATE)
    marsh = table.travel(*MARSH)
    assert flagship._hero(marsh.snapshot)['xp']['current'] == dict(ROUTE_XP)['enc_marsh_black_feathers']
    assert marsh.encounter == 'enc_marsh_black_feathers'
    assert flagship._enemies(marsh.snapshot) == [SCOUT]
    assert marsh.snapshot['currentScene']['restPolicy'] == 'risky'
    assert flagship._active_checkpoint(marsh.snapshot) == 'cp_midgewater_courier'
    scouts = table.fight()
    assert scouts.snapshot['combat']['status'] == 'ended'
    assert (scouts.flags, scouts.clues) == ({'hadrin_saved': True}, {'clue_name_ash_mouthed'})
    assert scouts.objectives == {'obj_track_missing_courier': 'completed'}
    assert (scouts.gp, scouts.xp) == (0, 100)
    hide = table.act('marsh_reed_hide', 'search', 'Reed Hide')
    assert (hide.flags, hide.clues) == ({'seal_recovered': True}, {'clue_broken_seal'})
    assert hide.objectives == {'obj_recover_broken_seal': 'completed'}
    assert flagship._scene_items(hide) == ['broken_seal_of_the_north', 'hadrins_torn_message']
    assert 'They called the one who leads them the Ash-Mouthed.' in table.narration()
    table.take('broken_seal_of_the_north', 'Broken Seal of the North')
    table.take('hadrins_torn_message', "Hadrin's Torn Message")
    assert {'broken_seal_of_the_north', 'hadrins_torn_message'} <= _item_ids(table.last)

    tower = table.travel(*TOWER)
    assert tower.snapshot['currentScene']['restPolicy'] == 'risky'
    assert 'tower_rangers_cache' not in flagship._visible_objects(tower.snapshot)
    lintel = table.act('tower_carved_lintel', 'inspect', 'Carved Lintel')
    assert table.last_check() == ('history', 12, 'success')
    assert (lintel.flags, lintel.clues) == ({'watchtower_decoded': True}, {'clue_oath_stone_map'})
    assert lintel.places == {DOWNS[0]: 'discovered'}
    assert lintel.objectives == {'obj_decode_watchtower_marks': 'completed'}
    assert 'tower_rangers_cache' in flagship._visible_objects(lintel.snapshot)
    cache = table.act('tower_rangers_cache', 'search', "Rangers' Cache")
    assert flagship._scene_items(cache) == ['tower_rangers_cache_potion_of_healing', 'tower_rangers_cache_coins']
    assert table.take('tower_rangers_cache_coins', '10 gp').gp == 10
    assert table.take('tower_rangers_cache_potion_of_healing', 'Potion of Healing').items['Potion of Healing'] == 1
    name = table.act('tower_name_stone', 'inspect', 'Cracked Name-Stone')
    assert (name.flags, name.clues) == ({'true_name_known': True}, {'clue_saereth_true_name_fragment'})

    # The party learned the road-song at the inn, so the barrow-wight does not rise.
    downs = table.travel(*DOWNS)
    assert flagship._hero(downs.snapshot)['xp']['current'] == dict(ROUTE_XP)['enc_barrow_cold_memory']
    assert (downs.encounter, downs.snapshot['combat']['status']) == (None, 'none')
    assert downs.snapshot['currentScene']['restPolicy'] == 'none'
    assert flagship._offered(flagship._story_view(downs.snapshot), 'barrow_standing_stone') == [
        ('use', 'Speak the road-oath at the standing stone', True, False)
    ]
    oath = table.act('barrow_standing_stone', 'use', 'Standing Stone')
    assert SCENE_OBJECTS['barrow_standing_stone']['actionText']['use'] in table.narration()
    assert (oath.flags, oath.clues) == ({'barrow_blessing': True}, {'clue_barrow_blessing'})
    assert oath.places == {CAMP[0]: 'discovered'}
    assert (oath.gp, oath.xp) == (0, 200)
    assert flagship._active_checkpoint(oath.snapshot) == 'cp_shadow_camp_finale'

    camp = table.travel(*CAMP)
    assert flagship._hero(camp.snapshot)['xp']['current'] == dict(ROUTE_XP)['enc_shadow_rite_amon_sul']
    assert (camp.encounter, camp.snapshot['currentScene']['restPolicy']) == (None, 'none')
    assert table.offers(OATH_STONE['id']) == {'inspect': True, 'use': False, 'lock': False, 'break': False, 'trigger': False}
    rite = table.act(OATH_STONE['id'], 'inspect', OATH_STONE['name'])
    assert rite.encounter == RITE['id']
    # What the party knows: Saereth's name weakens him and the blessing keeps the scouts away.
    scaling = _scaling(rite.snapshot)
    assert [adjustment['text'] for adjustment in scaling['adjustments']] == [adjustment['text'] for adjustment in RITE['adjustments']]
    assert _scaled_enemy_ids(scaling) == [SAERETH]
    assert flagship._enemies(rite.snapshot) == [SAERETH]
    assert _rite_for_a_party_of_four(rite.snapshot) == [SAERETH]
    hp = _participant(rite.snapshot, SAERETH)['hp']['max']
    assert hp == math.ceil(_authored_hp(SAERETH) * PARTY_SCALING[1][0] * 3 / 4)
    told = [RITE['setup'], *(adjustment['text'] for adjustment in RITE['adjustments'])]
    narration = table.narration()
    assert [narration.index(text) for text in told] == sorted(narration.index(text) for text in told)
    instructions = '\n'.join(table.pipeline()['dmContextPacket']['dmInstructions'])
    assert all(text in instructions for text in told)
    victory = table.fight()
    assert victory.snapshot['combat']['status'] == 'ended'
    assert victory.flags == {'saereth_resolved': True}
    assert victory.objectives == {'obj_interrupt_shadow_rite': 'completed'}
    assert (victory.gp, victory.xp) == (60, 600)
    assert flagship._ending(victory.snapshot) is None
    # The four fates are choices, shown together.
    offered = flagship._story_entry(table, OATH_STONE['id'])['offeredActions']
    assert [(entry['action'], entry.get('choice'), entry['legal']) for entry in offered] == [
        ('use', True, True), ('trigger', True, True), ('inspect', None, True), ('lock', True, True), ('break', True, True),
    ]
    relics = table.act('camp_stolen_relics', 'search', 'Stolen Relics')
    assert flagship._scene_items(relics) == ['camp_stolen_relics_potion_of_healing', 'camp_stolen_relics_coins']
    assert table.take('camp_stolen_relics_coins', '60 gp').gp == 60

    restored = table.act(OATH_STONE['id'], 'use', OATH_STONE['name'])
    assert OATH_STONE['actionText']['use'] in table.narration()
    assert restored.flags == {'stone_restored': True, 'quest_greenway_complete': True}
    assert restored.objectives == {'obj_restore_oath_stone': 'completed'}
    assert _quest_status(restored.snapshot) == 'completed'
    assert (restored.gp, restored.xp, restored.items["Ranger's Star-Token"]) == (50, 600, 1)
    assert flagship._active_checkpoint(restored.snapshot) == FINAL_CHECKPOINT['id']
    ending = _ending_for('stone_restored')
    assert table.no_ending_before_a_fate() == ending
    story = flagship._story_state(table)
    assert story['campaignPack']['ending'] == ending
    assert (story['journal']['quest']['stageTitle'], story['journal']['quest']['stageSummary']) == (ending['title'], ending['text'])
    assert {'kind': 'ending', 'text': 'The story ends — The Road Remembers'} in table.pipeline_beats()
    # One fate: once it is chosen no other is offered, and choosing again pays nothing.
    assert _fate_reasons(table) == {action: (False, 'That can no longer be done.') for action in FATES}
    assert table.refused(OATH_STONE['id'], 'trigger', OATH_STONE['name']) == ['That can no longer be done.']
    assert flagship._rewards(table.snapshot()) == [
        ('enc_barrow_cold_memory', 'negotiation'),
        ('enc_marsh_black_feathers', 'victory'),
        ('enc_shadow_rite_amon_sul', 'victory'),
    ]
    end = table.snapshot()
    assert flagship._hero(end)['xp']['current'] == 1500
    assert {clue['id'] for clue in end['clues']} == {clue['id'] for clue in PACK['clues']}
    assert flagship._places(end) == {location['id']: 'visited' for location in PACK['locations']}
    assert set(_objectives(end).values()) == {'completed'}
    assert _item_ids(end) == _item_ids(start) | {
        'black_feather_arrow',
        'muddy_road_sketch',
        'broken_seal_of_the_north',
        'hadrins_torn_message',
        'tower_rangers_cache_potion_of_healing',
        'rangers_star_token',
    }
    table.reads_cleanly()


def test_the_short_route_takes_the_goat_path_and_claims_the_stone(table):
    _short_route_to_the_freed_stone(table)

    claim = table.act(OATH_STONE['id'], 'trigger', OATH_STONE['name'])

    assert OATH_STONE['actionText']['trigger'] in table.narration()
    assert claim.flags == {'stone_claimed': True, 'quest_greenway_complete': True}
    assert claim.objectives == {'obj_restore_oath_stone': 'completed'}
    assert _quest_status(claim.snapshot) == 'completed'
    assert (claim.gp, claim.xp, claim.items["Ranger's Star-Token"]) == (50, 600, 1)
    assert table.no_ending_before_a_fate() == _ending_for('stone_claimed')
    # The optional objectives the goat path went around stay open.
    skipped = ('obj_question_bree_witnesses', 'obj_recover_broken_seal', 'obj_decode_watchtower_marks')
    assert {key: _objectives(claim.snapshot)[key] for key in skipped} == {key: 'open' for key in skipped}
    assert {action: legal for action, (legal, _reason) in _fate_reasons(table).items()} == {action: False for action in FATES}
    table.reads_cleanly()


@pytest.mark.parametrize(('action', 'flag'), [('lock', 'stone_hidden'), ('break', 'stone_broken')])
def test_hiding_or_breaking_the_stone_ends_the_story_in_its_own_words(table, action, flag):
    _short_route_to_the_freed_stone(table)

    fate = table.act(OATH_STONE['id'], action, OATH_STONE['name'])

    assert OATH_STONE['actionText'][action] in table.narration()
    assert fate.flags == {flag: True, 'quest_greenway_complete': True}
    assert table.no_ending_before_a_fate() == _ending_for(flag)
    assert {'kind': 'ending', 'text': f"The story ends — {ENDING_VARIANTS[flag]['title']}"} in table.pipeline_beats()
    table.reads_cleanly()


def _to_the_downs(table: GreenwayTable, *, song: bool) -> flagship.Change:
    """The inn (Borin's rubbing, and Elenwen's song when ``song``), the watchtower's lintel, then the Barrow-downs."""

    table.travel(*INN)
    table.act('pony_road_stone_rubbing', 'inspect', "Borin's Road-Stone Rubbing")
    if song:
        table.act('pony_hearthside', 'use', 'The Hearthside')
    table.travel(*TOWER)
    table.act('tower_carved_lintel', 'inspect', 'Carved Lintel')
    return table.travel(*DOWNS)


def test_the_greedy_route_robs_the_barrow_and_the_wight_rises(table):
    downs = _to_the_downs(table, song=True)
    assert (downs.encounter, downs.snapshot['combat']['status']) == (None, 'none')

    robbed = table.act('barrow_open_mound', 'search', 'Open Barrow')

    assert robbed.flags == {'barrow_robbed': True}
    assert flagship._scene_items(robbed) == ['barrow_open_mound_coins']
    assert robbed.encounter == 'enc_barrow_wight_roused'
    assert flagship._enemies(robbed.snapshot) == ['enemy_barrow_cold_wight']
    assert ENCOUNTERS['enc_barrow_wight_roused']['setup'] in table.narration()
    assert table.refused('barrow_standing_stone', 'use', 'Standing Stone') == ['That can no longer be done.']
    wight = table.fight()
    assert wight.snapshot['combat']['status'] == 'ended'
    assert wight.flags == {'barrow_passed': True}
    assert wight.places == {CAMP[0]: 'discovered'}
    assert (wight.gp, wight.xp) == (0, 200)
    assert flagship._rewards(wight.snapshot) == [('enc_barrow_wight_roused', 'victory')]
    assert table.take('barrow_open_mound_coins', '40 gp').gp == 40
    after = table.snapshot()
    assert after['flags']['barrow_blessing'] is False
    assert flagship._offered(flagship._story_view(after), 'barrow_standing_stone') == [
        ('use', 'Speak the road-oath at the standing stone', False, False)
    ]
    assert ('enc_barrow_cold_memory', 'negotiation') not in flagship._rewards(after)
    table.reads_cleanly()


def test_a_blessed_party_that_robs_the_dead_loses_the_blessing(table):
    _to_the_downs(table, song=True)
    assert table.act('barrow_standing_stone', 'use', 'Standing Stone').flags == {'barrow_blessing': True}

    robbed = table.act('barrow_open_mound', 'search', 'Open Barrow')

    assert robbed.flags == {'barrow_robbed': True, 'barrow_blessing': False}
    assert robbed.encounter == 'enc_barrow_wight_roused'
    assert table.fight().flags == {'barrow_passed': True}
    assert flagship._rewards(table.snapshot()) == [
        ('enc_barrow_cold_memory', 'negotiation'),
        ('enc_barrow_wight_roused', 'victory'),
    ]
    table.reads_cleanly()


def test_without_the_road_song_the_wight_rises_on_arrival_and_the_gold_then_costs_no_second_fight(table):
    downs = _to_the_downs(table, song=False)

    assert downs.encounter == 'enc_barrow_cold_memory'
    assert flagship._enemies(downs.snapshot) == ['enemy_barrow_cold_wight']
    assert ENCOUNTERS['enc_barrow_cold_memory']['setup'] in table.narration()
    assert table.refused('barrow_standing_stone', 'use', 'Standing Stone') == ["You don't know enough to do that yet."]
    wight = table.fight()
    assert wight.snapshot['combat']['status'] == 'ended'
    assert wight.flags == {'barrow_passed': True}
    assert wight.places == {CAMP[0]: 'discovered'}
    assert (wight.gp, wight.xp) == (0, 200)
    assert flagship._active_checkpoint(wight.snapshot) == 'cp_shadow_camp_finale'

    robbed = table.act('barrow_open_mound', 'search', 'Open Barrow')

    assert robbed.flags == {'barrow_robbed': True}
    assert (robbed.encounter, robbed.snapshot['combat']['status']) == (None, 'ended')
    assert table.take('barrow_open_mound_coins', '40 gp').gp == 40
    assert flagship._rewards(table.snapshot()) == [('enc_barrow_cold_memory', 'victory')]
    table.reads_cleanly()


def test_a_lone_ready_made_hero_wins_every_greenway_fight_often_enough(monkeypatch):
    monkeypatch.setitem(solo_balance.ROUTE_FLAGS, RITE['id'], CAREFUL_RITE_FLAGS)
    rates = {
        (preset.character_id, encounter_id): sum(
            solo_balance.fight(preset.character_id, encounter_id, seed, level=level_for_xp(xp), pack=PACK)
            for seed in range(1, BALANCE_SEEDS + 1)
        ) / BALANCE_SEEDS
        for preset in PREGENERATED_CHARACTER_PRESETS
        for encounter_id, xp in ROUTE_XP
    }

    assert len(rates) == len(PREGENERATED_CHARACTER_PRESETS) * len(ROUTE_XP) == 16
    assert {cell: rate for cell, rate in rates.items() if rate < 0.5} == {}
