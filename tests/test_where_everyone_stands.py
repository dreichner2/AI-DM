"""Where everyone stands: the rooms of a place, and distances in a fight.

Through the real socket pipeline on the Play Now adventure, in Safe Mode unless an AI narrator is named
(``tests/ai_narrator.py``): the Lantern Post Inn's yard, common room and stable; the lantern standing off on the
Old North Road. Seeded dice: object checks roll 15, the heroes roll 15 to hit and their highest damage, enemies 12.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest
import requests

from aidm_server.combat.positioning import advances_toward, reach_kind
from aidm_server.database import db
from aidm_server.game_state.zones import hero_zone_id
from aidm_server.models import DmTurn, Session, safe_json_dumps, safe_json_loads
from aidm_server.prompt_templates import ENGINE_BRIEF_RULES
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_file, lint_campaign_pack_manifest
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from tests.ai_narrator import ENGINE_BRIEF_RE, ai_narration
from tests.test_fight_maneuvers import RANGER
from tests.test_pack_author_traps import _pack as _trap_pack
from tests.test_party_play import PartyTable

PLAY_NOW = Path(__file__).resolve().parents[1] / 'docs' / 'examples' / 'the_road_of_unremembered_kings_campaign.json'

COMMON_ROOM_LINE = (
    'A low room warmed by a peat fire, its scrubbed tables and hanging lamps half empty tonight, with a counter of jars '
    'and barrels at the back.'
)
OUT_OF_REACH = 'The pale lantern is out of reach this turn. Advance first, or use something with range.'
NO_SPACE_RULE = (
    '16. Someone or something in another area is not here: the hero cannot touch it, search it or hold a conversation '
    'with it until they go there. The hero stays in the area "Where" names unless "Did" says they moved: never walk '
    'them into another area. A fight\'s distances are given under "Positions"; a hero cannot strike with a hand '
    'weapon beyond 5 ft unless "Did" says they closed in.'
)


class Table(PartyTable):
    """The Play Now table, with the moves and reads the rooms need."""

    def move(self, hero: str, zone_id: str, label: str) -> list[dict]:
        return self.send(hero, label, {'kind': 'zone_move', 'source': 'scene_panel', 'zone': {'id': zone_id, 'name': label}})

    def act(self, hero: str, target: str, action: str) -> list[dict]:
        return self.send(hero, f'{hero} attempts to {action} {target}.', {'kind': 'object', 'source': 'scene_panel', 'object': {'id': target, 'action': action}})

    def rest(self, hero: str, rest_type: str = 'short_rest') -> list[dict]:
        return self.send(hero, f'{hero} rests.', {'kind': 'rest', 'source': 'scene_panel', 'rest_type': rest_type})

    def zone(self, hero: str) -> str:
        return hero_zone_id(self.snapshot(), self.hero_id(hero))

    def view(self, hero: str) -> dict:
        response = self.client.get(f"/api/sessions/{self.ids['session_id']}/state?view=player&player_id={self.players[hero]}")
        assert response.status_code == 200, response.get_json()
        return response.get_json()['state_snapshot']

    def last(self) -> dict[str, Any]:
        with self.app.app_context():
            turn = DmTurn.query.filter_by(session_id=self.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
            metadata = safe_json_loads(turn.metadata_json, {})
            return {
                'narration': turn.dm_output or '',
                'provider': turn.llm_provider,
                'beats': metadata.get('storyBeats') or [],
                'pipeline': metadata.get('state_pipeline') or {},
            }


def _errors(received: list[dict]) -> list[str]:
    return [event['args'][0]['error'] for event in received if event['name'] == 'error' and event['args']]


def _here(view: dict) -> dict[str, tuple[bool, bool, list[str]]]:
    return {zone['id']: (zone['current'], zone['adjacent'], [person['name'] for person in zone['here']]) for zone in view['currentScene']['zones']}


def _seeded(monkeypatch) -> None:
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 14 if sides == 20 else sides - 1)
    monkeypatch.setattr('aidm_server.combat.actions.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda sides: min(sides, 12))


@pytest.fixture()
def solo(app, client, socketio, safe_mode):
    table = Table(app, client, socketio, party=False)
    yield table
    table.close()


@pytest.fixture()
def party(app, client, socketio, safe_mode):
    table = Table(app, client, socketio, party=True)
    yield table
    table.close()


@pytest.fixture()
def safe_mode(app, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    _seeded(monkeypatch)


# B. Rooms and areas ---------------------------------------------------------------------------------------------


def test_arriving_puts_every_hero_in_the_yard_and_the_view_names_who_stands_where(party):
    assert party.zone('Arden Vale') == party.zone('Seren Ashgrove') == 'inn_yard'

    party.move('Arden Vale', 'inn_common_room', 'Go into the common room')

    view = party.view('Seren Ashgrove')
    assert _here(view) == {
        'inn_yard': (True, False, ['Seren Ashgrove', 'Tovin Bell', 'Osric Hale']),
        'inn_common_room': (False, True, ['Arden Vale', 'Mara Reedwake']),
        'inn_stable': (False, True, []),
    }
    assert {zone['id']: zone.get('moveLabel') for zone in view['currentScene']['zones']} == {
        'inn_yard': None,
        'inn_common_room': 'Go into the common room',
        'inn_stable': 'Go to the stable',
    }
    assert party.last()['narration'] == f'Arden Vale steps into the common room. {COMMON_ROOM_LINE} Mara Reedwake is here.'
    objects = {entry['id']: entry['zoneId'] for entry in view['currentScene']['interactables']}
    assert objects['inn_caravan_ledger'] == 'inn_yard' and objects['inn_marked_mare'] == 'inn_stable'
    assert [(merchant['id'], merchant['zoneId']) for merchant in view['currentScene']['merchants']] == [
        ('merchant_mara_counter', 'inn_common_room')
    ]


def test_a_zone_move_takes_a_turn_and_no_time_and_tells_the_room_the_first_time(solo):
    clock = solo.snapshot().get('clock')

    assert _errors(solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')) == []

    first = solo.last()
    assert solo.zone('Arden Vale') == 'inn_common_room'
    assert first['narration'] == f'You step into the common room. {COMMON_ROOM_LINE} Mara Reedwake is here.'
    assert first['beats'] == [{'kind': 'place_within', 'text': 'Found — The Common Room', 'refId': 'inn_common_room'}]
    assert solo.snapshot().get('clock') == clock

    solo.move('Arden Vale', 'inn_yard', 'Step out to the yard')
    again = solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')
    assert _errors(again) == []
    assert solo.last()['narration'] == 'You step into the common room. Mara Reedwake is here.'
    assert solo.last()['beats'] == []


def test_typed_words_name_a_room(solo):
    solo.send('Arden Vale', 'I step out to the stable.')

    assert solo.zone('Arden Vale') == 'inn_stable'
    assert solo.last()['narration'].startswith('You step into the stable.')


def test_talking_to_someone_next_door_steps_in_first_in_the_same_turn(solo):
    solo.send('Arden Vale', 'I talk to Mara.')

    told = solo.last()
    assert solo.zone('Arden Vale') == 'inn_common_room'
    assert told['narration'].startswith(f'You step into the common room. {COMMON_ROOM_LINE} Mara Reedwake is here.\n\n"')
    assert 'Mara' in told['narration'].split('\n\n')[1]
    assert {'kind': 'place_within', 'text': 'Found — The Common Room', 'refId': 'inn_common_room'} in told['beats']


def test_two_rooms_away_is_refused_before_narration(solo):
    solo.move('Arden Vale', 'inn_stable', 'Go to the stable')

    assert _errors(solo.send('Arden Vale', 'I talk to Mara.')) == ['Mara Reedwake is in the common room. Go there first.']
    assert _errors(solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')) == [
        'The common room is beyond the yard. Go there first.'
    ]
    assert solo.zone('Arden Vale') == 'inn_stable'
    view = solo.view('Arden Vale')
    assert [(zone['id'], zone['adjacent']) for zone in view['currentScene']['zones'] if zone['adjacent']] == [('inn_yard', True)]
    assert view['currentScene']['merchants'] == []


def test_an_object_two_rooms_away_is_not_offered_and_cannot_be_used(solo):
    solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')

    mare = next(entry for entry in solo.view('Arden Vale')['currentScene']['interactables'] if entry['id'] == 'inn_marked_mare')
    assert [(offer['label'], offer['legal'], offer.get('reason')) for offer in mare['offeredActions']] == [
        ('Look over the off-leader', False, 'The Off-Leader Mare is in the stable. Go there first.')
    ]
    assert _errors(solo.act('Arden Vale', 'inn_marked_mare', 'inspect')) == ['The Off-Leader Mare is in the stable. Go there first.']


def test_searching_the_strongbox_from_the_common_room_steps_out_to_the_yard_first(solo):
    solo.act('Arden Vale', 'inn_drivers_bench', 'search')
    solo.act('Arden Vale', 'inn_lead_wagon', 'search')
    solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')

    assert _errors(solo.act('Arden Vale', 'inn_wagon_strongbox', 'search')) == []

    told = solo.last()
    assert solo.zone('Arden Vale') == 'inn_yard'
    assert told['narration'].startswith('You step into the yard. Tovin Bell and Osric Hale are here.')
    assert solo.snapshot()['combat']['status'] == 'active'


def test_a_mention_of_someone_next_door_is_a_call_and_words_said_to_them_walk_in(solo):
    solo.send('Arden Vale', '"Is Mara in there? We have need of her."')

    told = solo.last()
    assert told['narration'] == '"Come through if there is more to say," Mara Reedwake calls from the common room.'
    assert solo.zone('Arden Vale') == 'inn_yard'
    assert 'talk' not in told['pipeline']['dmContextPacket']['narration']

    # Spoken to her by name, the words are hers to answer: the hero goes in, as any action aimed next door does.
    solo.send('Arden Vale', '"Mara, is there a room free tonight?"')
    assert solo.zone('Arden Vale') == 'inn_common_room'
    assert solo.snapshot()['conversations']['player_1']['npcId'] == 'npc_mara_reedwake'


def test_resting_happens_only_in_the_common_room(solo):
    view = solo.view('Arden Vale')['currentScene']
    assert (view['restPolicy'], view['restReason']) == ('none', 'You can rest in the common room. Go there first.')
    assert _errors(solo.rest('Arden Vale')) == ['You can rest in the common room. Go there first.']

    solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')

    assert solo.view('Arden Vale')['currentScene']['restPolicy'] == 'safe'
    assert _errors(solo.rest('Arden Vale')) == []
    assert any(change['type'] == 'rest.complete' for change in solo.last()['pipeline']['immediateAppliedChanges'])


def test_the_party_sets_out_from_whichever_room_it_is_in(solo):
    """Walking back to the way out is no decision: travel is never refused for the room the hero stands in."""

    solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')
    assert next(zone for zone in solo.view('Arden Vale')['currentScene']['zones'] if zone['current'])['exit'] is False

    assert _errors(solo.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')) == []
    assert solo.snapshot()['currentScene']['locationId'] == 'loc_old_north_road'
    assert solo.zone('Arden Vale') == 'road_crown'


def test_a_hero_in_the_common_room_comes_running_when_the_yard_fight_starts(party):
    party.move('Seren Ashgrove', 'inn_common_room', 'Go into the common room')
    party.act('Arden Vale', 'inn_drivers_bench', 'search')
    party.act('Arden Vale', 'inn_lead_wagon', 'search')

    party.act('Arden Vale', 'inn_wagon_strongbox', 'search')

    started = party.last()
    assert 'Seren Ashgrove comes running from the common room.' in started['narration']
    positions = {participant['name']: participant['position']['rangeBand'] for participant in party.snapshot()['combat']['participants']}
    assert (positions['Arden Vale'], positions['Seren Ashgrove']) == ('near', 'far')
    seen = {participant['name']: participant.get('reach') for participant in party.view('Arden Vale')['combat']['participants']}
    assert seen['Seren Ashgrove'] == 'out_of_reach'
    assert party.zone('Seren Ashgrove') == 'inn_yard'

    party.operator([{'type': 'combat.end', 'status': 'ended', 'endReason': 'all_enemies_defeated'}])
    assert party.zone('Arden Vale') == party.zone('Seren Ashgrove') == 'inn_yard'


def test_zones_become_known_by_standing_in_them_or_next_to_them(solo):
    snapshot = solo.snapshot()
    inn = next(location for location in snapshot['campaignPack']['catalog']['locations'] if location['id'] == 'loc_lantern_post_inn')
    # A cellar beyond the stable: two rooms from the yard, unknown until someone stands in the stable.
    inn['zones'] = [*inn['zones'], {'id': 'inn_cellar', 'name': 'The Cellar', 'adjacentTo': ['inn_stable']}]
    hero = snapshot['playerCharacters'][0]
    with solo.app.app_context():
        session = db.session.get(Session, solo.ids['session_id'])
        session.state_snapshot = safe_json_dumps(snapshot, {})
        db.session.commit()

    def known() -> list[str]:
        view = filter_session_snapshot_for_player(solo.snapshot(), private_player_ids={hero['playerId']}, acting_player_id=hero['playerId'])
        return [zone['id'] for zone in view['currentScene']['zones']]

    assert known() == ['inn_yard', 'inn_common_room', 'inn_stable']
    solo.move('Arden Vale', 'inn_stable', 'Go to the stable')
    assert known() == ['inn_yard', 'inn_common_room', 'inn_stable', 'inn_cellar']
    solo.move('Arden Vale', 'inn_yard', 'Step out to the yard')
    assert known() == ['inn_yard', 'inn_common_room', 'inn_stable', 'inn_cellar']


def test_a_place_without_zones_has_none(solo):
    snapshot = deepcopy(solo.snapshot())
    snapshot['currentScene']['locationId'] = 'loc_cairns_roadwardens'

    view = filter_session_snapshot_for_player(snapshot)

    assert 'zones' not in view['currentScene']


# A. Distance you can read ---------------------------------------------------------------------------------------


@pytest.fixture()
def road(app, client, socketio, safe_mode):
    """Arden Vale (a fighter, longsword) and Thalion (a ranger, longbow) on the Old North Road: the pale lantern
    stands off at far, the wardbound beast near."""

    table = Table(app, client, socketio, party=False)
    created = client.post(f"/api/players/campaigns/{table.ids['campaign_id']}/players", json=RANGER)
    assert created.status_code == 201, created.get_json()
    table.players['Thalion'] = created.get_json()['player_id']
    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit('join_session', {'session_id': table.ids['session_id'], 'player_id': table.players['Thalion'], 'workspace_id': table.ids['workspace_id']})
    table.sockets['Thalion'] = socket_client
    table.drain()
    table.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    yield table
    table.close()


def _attack(table: Table, hero: str, weapon_id: str) -> dict:
    return next(action for action in table.actions(hero) if action['id'] == weapon_id)


def _lantern(table: Table) -> dict:
    return next(participant for participant in table.snapshot()['combat']['participants'] if participant['name'] == 'Pale Lantern')


def _until_turn_of(table: Table, hero: str) -> None:
    for _step in range(8):
        acting = table.acting_hero()
        if acting == hero or table.snapshot()['combat']['status'] != 'active':
            return
        table.hud(acting, f'{acting} ends the turn.', 'combat.end_turn')


def test_the_fight_starts_with_the_lantern_out_of_reach_and_says_so(road):
    opening = next(turn for turn in road.turns_since(0) if 'well out of reach' in turn['narration'])
    assert 'The pale lantern is well out of reach; the wardbound beast is a move away.' in opening['narration']
    # An enemy coming in is told in distance words.
    assert 'The wardbound beast crosses about thirty feet to Thalion.' in opening['narration']
    assert _lantern(road)['position']['rangeBand'] == 'far'
    seen = {participant['name']: (participant.get('reach'), participant.get('reachLabel')) for participant in road.view('Arden Vale')['combat']['participants']}
    assert seen['Pale Lantern'] == ('out_of_reach', 'out of reach this turn · 60 ft or more')
    assert seen['Wardbound Beast'][0] in {'one_move', 'in_reach'}
    assert 'reach' not in next(participant for participant in road.view('Arden Vale')['combat']['participants'] if participant['name'] == 'Arden Vale')


def test_a_sword_cannot_reach_the_lantern_but_a_bow_can(road):
    sword = _attack(road, 'Arden Vale', 'combat.attack.starter_fighter_longsword')
    target = next(target for target in sword['targets'] if target['id'] == _lantern(road)['id'])
    assert (target['available'], target['reason']) == (False, OUT_OF_REACH)

    bow = _attack(road, 'Thalion', 'combat.attack.starter_ranger_longbow')
    assert next(target for target in bow['targets'] if target['id'] == _lantern(road)['id'])['available'] is True


def test_typing_a_stab_at_the_lantern_is_refused_with_its_distance(road):
    _until_turn_of(road, 'Arden Vale')

    assert _errors(road.send('Arden Vale', 'I stab the lantern.')) == [OUT_OF_REACH]


def test_advancing_on_the_lantern_brings_it_a_move_away_then_close_in_and_attack(road):
    _until_turn_of(road, 'Arden Vale')
    advance = next(action for action in road.actions('Arden Vale') if action['id'] == 'combat.advance')
    assert next(target for target in advance['targets'] if target['id'] == _lantern(road)['id'])['available'] is True

    road.hud('Arden Vale', 'Arden Vale advances.', 'combat.advance', _lantern(road)['id'])

    arden = road.participant('Arden Vale')
    assert arden['position']['rangeBand'] == 'far'
    from_arden = {participant['name']: participant.get('reach') for participant in road.view('Arden Vale')['combat']['participants']}
    from_thalion = {participant['name']: participant.get('reach') for participant in road.view('Thalion')['combat']['participants']}
    assert (from_arden['Pale Lantern'], from_thalion['Pale Lantern']) == ('one_move', 'out_of_reach')

    if road.acting_hero() == 'Arden Vale':
        road.hud('Arden Vale', 'Arden Vale ends the turn.', 'combat.end_turn')
    _until_turn_of(road, 'Arden Vale')
    assert road.acting_hero() == 'Arden Vale' and _lantern(road)['position']['rangeBand'] == 'far'
    sword = _attack(road, 'Arden Vale', 'combat.attack.starter_fighter_longsword')
    target = next(target for target in sword['targets'] if target['id'] == _lantern(road)['id'])
    assert (target['available'], target.get('requiresMove')) == (True, True)

    assert _errors(road.hud('Arden Vale', 'Arden Vale attacks.', sword['id'], _lantern(road)['id'])) == []
    assert _lantern(road)['position']['rangeBand'] == 'melee'


# The AI narrator ------------------------------------------------------------------------------------------------


@pytest.fixture()
def ai(app, client, socketio, monkeypatch):
    narrator = ai_narration(app, monkeypatch)
    _seeded(monkeypatch)
    table = Table(app, client, socketio, party=False)
    table.narrator = narrator
    yield table
    table.close()


def _brief(table: Table) -> str:
    return ENGINE_BRIEF_RE.search(table.narrator.prompts[-1]).group('brief')


def test_a_zone_move_makes_no_model_call_with_an_ai_narrator(ai):
    before = len(ai.narrator.prompts)

    assert _errors(ai.move('Arden Vale', 'inn_common_room', 'Go into the common room')) == []

    told = ai.last()
    assert len(ai.narrator.prompts) == before
    assert told['provider'] == 'fallback'
    assert told['narration'] == f'You step into the common room. {COMMON_ROOM_LINE} Mara Reedwake is here.'
    assert told['pipeline']['postDmExtraction']['debug']['reason'] == 'zone_move'
    assert told['pipeline']['preDmExtraction']['notes'] == ['zone_move']


def test_the_brief_says_where_everyone_stands_and_the_rule(ai):
    ai.narrator.replies.append('Rain drips from the eaves.')
    ai.send('Arden Vale', 'I look around the yard.')

    brief = _brief(ai)
    assert 'Where: Arden Vale is in the yard with Tovin Bell and Osric Hale. Next door, in the common room: Mara Reedwake.' in brief.splitlines()
    assert NO_SPACE_RULE in ENGINE_BRIEF_RULES
    assert NO_SPACE_RULE in ai.narrator.prompts[-1]


def test_acting_next_door_with_an_ai_narrator_tells_the_step_in_the_brief(ai):
    before = len(ai.narrator.prompts)
    # Talking to Mara next door is her written greeting after the engine's own arrival line: no model is asked.
    ai.send('Arden Vale', 'I talk to Mara.')
    assert len(ai.narrator.prompts) == before
    assert ai.zone('Arden Vale') == 'inn_common_room'
    assert COMMON_ROOM_LINE in ai.last()['narration']

    # Reading the ledger back in the yard is the narrator's to tell, and the brief says the hero stepped out first.
    ai.narrator.replies.append('The pages are swollen with rain.')
    ai.act('Arden Vale', 'inn_caravan_ledger', 'inspect')

    assert len(ai.narrator.prompts) == before + 1
    did = next(line for line in _brief(ai).splitlines() if line.startswith('Did: '))
    assert 'Stepped into the yard' in did
    assert ai.zone('Arden Vale') == 'inn_yard'


def test_the_brief_gives_fight_positions(ai):
    ai.narrator.replies.extend(['Pale lights rise over the road.'] * 3)
    ai.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    ai.narrator.replies.append('Arden braces.')
    _until_turn_of(ai, 'Arden Vale')
    ai.narrator.replies.append('Arden swings at nothing.')
    ai.send('Arden Vale', 'I look over the fight.', {'kind': 'message', 'source': 'composer'})

    positions = next(line for line in _brief(ai).splitlines() if line.startswith('Positions: '))
    assert 'Pale Lantern: out of reach this turn · 60 ft or more' in positions


# Pack rules: schema and linter ----------------------------------------------------------------------------------


def _zoned_pack() -> dict:
    """The pack-trap base pack with a two-room inn: clean."""

    pack = _trap_pack()
    pack['locations'][0].update(
        startZoneId='inn_yard',
        zones=[
            {'id': 'inn_yard', 'name': 'The Yard', 'exit': True, 'adjacentTo': ['inn_hall']},
            {'id': 'inn_hall', 'name': 'The Hall', 'rest': True, 'adjacentTo': [], 'moveLabel': 'Go into the hall'},
        ],
        npcIds=['npc_keeper'],
        interactables=[{'id': 'chest', 'name': 'Chest', 'kind': 'object', 'allowedActions': ['inspect'], 'zoneId': 'inn_hall'}],
    )
    pack['locations'][1].update(startZoneId='road_top', zones=[{'id': 'road_top', 'name': 'The Road', 'adjacentTo': []}])
    pack['npcs'] = [{'id': 'npc_keeper', 'name': 'The Keeper', 'locationId': 'start', 'zoneId': 'inn_hall', 'visibleAtStart': True}]
    pack['enemies'] = [{'id': 'enemy_guard', 'name': 'Gate Guard'}]
    pack['encounters'] = [
        {'id': 'enc_gate', 'title': 'The Gate', 'locationIds': ['start'], 'zoneId': 'inn_yard',
         'enemyGroups': [{'enemyId': 'enemy_guard', 'count': 1, 'startBand': 'far'}]},
    ]
    return pack


def _codes(pack: dict) -> list[tuple[str, str, str]]:
    return [
        (issue['severity'], issue['code'], issue['path'])
        for issue in lint_campaign_pack_manifest(pack, workspace_id='owner', strict=True)['issues']
    ]


def test_a_pack_with_rooms_lints_clean(app):
    with app.app_context():
        assert _codes(_zoned_pack()) == []


@pytest.mark.parametrize(
    ('change', 'expected'),
    [
        (lambda pack: pack['locations'][0].pop('startZoneId'), ('error', 'zone_without_start', 'locations[0].startZoneId')),
        (lambda pack: pack['locations'][0].update(startZoneId='inn_cellar'), ('error', 'unknown_zone_id', 'locations[0].startZoneId')),
        (lambda pack: pack['locations'][0]['zones'][0]['adjacentTo'].append('inn_cellar'), ('error', 'unknown_zone_id', 'locations[0].zones[0].adjacentTo')),
        (lambda pack: pack['locations'][0]['zones'][0].update(adjacentTo=[]), ('error', 'zone_graph_not_connected', 'locations[0].zones')),
        (lambda pack: pack['locations'][0]['zones'][1].update(id='inn_yard'), ('error', 'duplicate_zone_id', 'locations[0].zones[1]')),
        (lambda pack: pack['npcs'][0].update(zoneId='road_top'), ('error', 'zone_in_other_location', 'npcs[0].zoneId')),
        (lambda pack: pack['locations'][0]['interactables'][0].update(zoneId='nowhere'), ('error', 'unknown_zone_id', 'locations[0].interactables[0].zoneId')),
        (lambda pack: pack['encounters'][0].update(zoneId='road_top'), ('error', 'zone_in_other_location', 'encounters[0].zoneId')),
        (lambda pack: pack['encounters'][0]['enemyGroups'][0].update(startBand='distant'), ('error', 'unknown_start_band', 'encounters[0].enemyGroups[0].startBand')),
    ],
    ids=['no start', 'unknown start', 'unknown neighbour', 'not connected', 'duplicate', 'person elsewhere', 'object nowhere', 'fight elsewhere', 'start band'],
)
def test_the_linter_reports_rooms_the_engine_cannot_use(app, change, expected):
    pack = _zoned_pack()
    change(pack)

    with app.app_context():
        assert expected in _codes(pack)


def test_the_schema_holds_zones_and_start_bands(app):
    with app.app_context():
        pack = _zoned_pack()
        pack['locations'][0]['zones'][1].pop('adjacentTo')
        missing = lint_campaign_pack_manifest(pack, workspace_id='owner')['issues']
        pack = _zoned_pack()
        pack['encounters'][0]['enemyGroups'][0]['startBand'] = 'distant'
        band = lint_campaign_pack_manifest(pack, workspace_id='owner')['issues']

    assert ('invalid_campaign_pack_schema', 'locations[0].zones[1].adjacentTo is required.') in [(issue['code'], issue['message']) for issue in missing]
    assert ('invalid_campaign_pack_schema', 'encounters[0].enemyGroups[0].startBand must be one of: melee, near, far.') in [
        (issue['code'], issue['message']) for issue in band
    ]


def test_the_play_now_pack_lints_strictly_clean_with_its_rooms(app):
    with app.app_context():
        result = lint_campaign_pack_file(PLAY_NOW, workspace_id='owner', strict=True)

    assert result['issues'] == []
    zones = {location['id']: [zone['id'] for zone in location.get('zones') or []] for location in json.loads(PLAY_NOW.read_text())['locations']}
    assert zones == {
        'loc_lantern_post_inn': ['inn_yard', 'inn_common_room', 'inn_stable'],
        'loc_old_north_road': ['road_crown', 'road_verge'],
        'loc_waystation_scratched_names': ['ws_court', 'ws_hall', 'ws_stable'],
        'loc_cairns_roadwardens': [],
        'loc_old_masons_yard': ['yard_open', 'yard_workshop'],
        'loc_ridge_and_culvert_paths': ['ridge_path', 'ridge_culvert'],
        'loc_valley_last_road_stone': ['valley_rim', 'valley_refuge', 'valley_stone'],
    }


# Reach between participants ---------------------------------------------------------------------------------------


def _at(participant_id: str, band: str, team: str = 'enemy', engaged: list[str] | None = None) -> dict:
    position = {'rangeBand': band, **({'engagedWith': engaged} if engaged else {})}
    return {'id': participant_id, 'name': participant_id, 'team': team, 'hp': {'current': 5, 'max': 5}, 'isAlive': True, 'isConscious': True, 'position': position}


@pytest.mark.parametrize(
    ('viewer', 'target', 'kind'),
    [
        (_at('hero', 'melee', 'player', ['foe']), _at('foe', 'melee', engaged=['hero']), 'in_reach'),
        (_at('hero', 'melee', 'player'), _at('foe', 'melee'), 'in_reach'),
        (_at('hero', 'near', 'player'), _at('foe', 'melee'), 'one_move'),
        (_at('hero', 'near', 'player'), _at('foe', 'near'), 'one_move'),
        (_at('hero', 'far', 'player'), _at('foe', 'far'), 'one_move'),
        (_at('hero', 'near', 'player'), _at('foe', 'far'), 'out_of_reach'),
        (_at('hero', 'far', 'player'), _at('foe', 'near'), 'out_of_reach'),
        (_at('hero', 'near', 'player'), _at('foe', 'distant'), 'far_off'),
    ],
)
def test_reach_reads_what_one_move_can_do(viewer, target, kind):
    assert reach_kind(viewer, target) == kind


def test_a_hero_in_the_press_advances_on_an_enemy_standing_off_unless_held():
    foe, far = _at('foe', 'melee', engaged=['hero']), _at('lantern', 'far')
    held = _at('hero', 'melee', 'player', ['foe'])
    free = _at('hero', 'melee', 'player')

    assert advances_toward(free, far, {'participants': [free, foe, far]}) is True
    assert advances_toward(held, far, {'participants': [held, foe, far]}) is False
    assert advances_toward(_at('hero', 'far', 'player'), far, {'participants': [far]}) is False


def test_a_hero_who_joins_between_turns_stands_in_the_start_room_in_everyones_view(solo):
    """The stored snapshot learns of a new hero only when a turn runs. Until then the joiner saw no room of their
    own (and the others did not see them): the view adds them on read, where arriving heroes stand."""

    from aidm_server.models import Player

    solo.move('Arden Vale', 'inn_common_room', 'Go into the common room')
    with solo.app.app_context():
        arden = db.session.get(Player, solo.players['Arden Vale'])
        joiner = Player(
            campaign_id=solo.ids['campaign_id'],
            workspace_id=arden.workspace_id,
            account_id=arden.account_id,
            name='Second Player',
            character_name='Rowan Pell',
            race='Human',
            class_='Cleric',
            level=1,
        )
        db.session.add(joiner)
        db.session.commit()
        solo.players['Rowan Pell'] = joiner.player_id
        stored_before = db.session.get(Session, solo.ids['session_id']).state_snapshot

    def rooms(hero: str) -> dict[str, tuple[bool, list[str]]]:
        return {zone['id']: (zone['current'], [person['name'] for person in zone['here']]) for zone in solo.view(hero)['currentScene']['zones']}

    theirs = rooms('Rowan Pell')
    assert theirs['inn_yard'] == (True, ['Rowan Pell', 'Tovin Bell', 'Osric Hale'])
    assert theirs['inn_common_room'] == (False, ['Arden Vale', 'Mara Reedwake'])
    # The hero already there sees them arrive too.
    assert rooms('Arden Vale')['inn_yard'] == (False, ['Rowan Pell', 'Tovin Bell', 'Osric Hale'])
    # Nothing is written back by a read.
    with solo.app.app_context():
        assert db.session.get(Session, solo.ids['session_id']).state_snapshot == stored_before


def test_one_hero_talking_never_shuts_the_other_out(party):
    """Talking to someone used to hand that hero "the spotlight", and the other player's next action was refused
    without a word on their screen. A pack adventure holds no spotlight and no queue."""

    from aidm_server.turn_control import turn_control_from_session

    assert _errors(party.send('Arden Vale', 'I talk to Tovin Bell.')) == []
    assert _errors(party.act('Seren Ashgrove', 'inn_caravan_ledger', 'inspect')) == []
    with party.app.app_context():
        assert turn_control_from_session(db.session.get(Session, party.ids['session_id']))['mode'] == 'free'


def test_a_spotlight_left_by_an_older_build_is_cleared_on_the_next_action(party):
    from aidm_server.turn_control import set_session_turn_control, turn_control_from_session

    with party.app.app_context():
        session_obj = db.session.get(Session, party.ids['session_id'])
        set_session_turn_control(
            session_obj,
            mode='spotlight',
            active_player_id=party.players['Arden Vale'],
            participant_player_ids=[party.players['Arden Vale']],
            updated_by_player_id=party.players['Arden Vale'],
            source='auto',
        )
        db.session.commit()

    assert _errors(party.act('Seren Ashgrove', 'inn_caravan_ledger', 'inspect')) == []
    with party.app.app_context():
        assert turn_control_from_session(db.session.get(Session, party.ids['session_id']))['mode'] == 'free'


def test_naming_the_hidden_strongbox_searches_the_wagon_that_hides_it(solo):
    """The Play Now pack writes ``reveals`` as a plain list (what a search uncovers). Typed words naming the
    still-hidden strongbox search the lead wagon, instead of asking for a roll about a thing nobody has seen."""

    assert _errors(solo.send('Arden Vale', 'I search the wagon strongbox.')) == []

    told = solo.last()
    assert [change.get('targetId') for change in told['pipeline']['immediateAppliedChanges'] if change.get('type') == 'scene.interactable.action'] == ['inn_lead_wagon']
    strongbox = next(entry for entry in solo.snapshot()['currentScene']['interactables'] if entry['id'] == 'inn_wagon_strongbox')
    assert strongbox.get('hidden') is False
