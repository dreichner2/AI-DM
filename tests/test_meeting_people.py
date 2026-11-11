"""People are met when the party arrives where the pack lists them, and can then be talked to.

Played like ``tests/test_flagship_adventure.py``: one hero at the table in Safe Mode, acting through
the intents the client sends over the socket. The Play Now road meets its people on arrival; a
small inline pack holds people back with ``hidden`` and ``hiddenUntil``.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest
import requests

from aidm_server.database import db
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.orchestration.story_beats import story_beats
from aidm_server.models import Campaign, DmTurn, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from tests import test_flagship_adventure as flagship
from tests import test_play_now_release_smoke as smoke

TOPIC_LINES = {npc['id']: {topic['id']: topic['line'] for topic in npc.get('topics') or []} for npc in flagship.PACK['npcs']}
UNREACHABLE_STATUSES = {'dead', 'deceased', 'destroyed'}

WREN_KNOWS = 'The lamp in the loft burns all night for someone who never comes home.'
ILSABET_KNOWS = 'The wheel turns backwards when the drowned are listening.'
ILSABET_SECRET = 'Ilsabet opened the sluice the night the river ran red.'
CORRAN_KNOWS = 'Three crates under the mill floor are not flour.'
CORRAN_SECRET = 'Corran pays the reeve to look away from the lower landing.'
# What Hesk will not say yet, would take, or would believe: none of it may reach a player.
HESK_SLUICE = 'The sluice was opened from the inside.'
HESK_REACTS = 'Hesk spits. "Liar."'
HESK_UNSAID = [HESK_SLUICE, HESK_REACTS, 'coin:500', 'reeve_seal', 'Hesk remembers who held the lamp.']
HELD_BACK = {
    'npc_ilsabet': ['Ilsabet Thornquill', ILSABET_KNOWS, ILSABET_SECRET],
    'npc_corran': ['Corran Vey', CORRAN_KNOWS, CORRAN_SECRET],
}


def _mill_pack() -> dict:
    """A lane and a mill whose four people are met on arrival, once a flag holds, once the wheel turns, and never."""

    return {
        'packId': 'mill_meeting',
        'title': 'The Old Mill',
        'startingState': {
            'locationId': 'loc_lane',
            'knownLocationIds': ['loc_lane', 'loc_mill'],
            'flags': {'lamp_lit': True},
        },
        'locations': [
            {'id': 'loc_lane', 'name': 'Mill Lane', 'sceneType': 'travel', 'connectedLocationIds': ['loc_mill']},
            {
                'id': 'loc_mill',
                'name': 'Old Mill',
                'sceneType': 'social',
                'connectedLocationIds': ['loc_lane'],
                'npcIds': ['npc_hesk', 'npc_wren', 'npc_ilsabet', 'npc_corran'],
                'interactables': [
                    {
                        'id': 'mill_wheel',
                        'name': 'Mill Wheel',
                        'kind': 'object',
                        'allowedActions': ['inspect'],
                        'setsFlags': {'inspect': {'mill_wheel_turned': True}},
                    }
                ],
            },
        ],
        'npcs': [
            {'id': 'npc_hesk', 'name': 'Hesk the Miller', 'locationId': 'loc_mill',
             'knows': ['The wheel stopped the night the river ran red.'],
             'topics': [{'id': 'hesk_sluice', 'label': 'the sluice', 'line': HESK_SLUICE, 'requires': {'dispositionAtLeast': 2}}],
             'reacts': [{'act': 'claim', 'line': HESK_REACTS}],
             'callbacks': [{'id': 'hesk_lamp', 'when': {'remembers': 'held_lamp', 'on': 'rest'}, 'text': 'Hesk remembers who held the lamp.'}],
             'wants': ['coin:500'],
             'believes': [{'claim': 'authority', 'ifFlags': {'reeve_seal': True}}]},
            # equals defaults to true.
            {'id': 'npc_wren', 'name': 'Wren Lampwick', 'locationId': 'loc_mill',
             'hiddenUntil': {'flagKey': 'lamp_lit'}, 'knows': [WREN_KNOWS]},
            {'id': 'npc_ilsabet', 'name': 'Ilsabet Thornquill', 'locationId': 'loc_mill',
             'hiddenUntil': {'flagKey': 'mill_wheel_turned', 'equals': True}, 'knows': [ILSABET_KNOWS],
             'secret': ILSABET_SECRET},
            {'id': 'npc_corran', 'name': 'Corran Vey', 'locationId': 'loc_mill', 'hidden': True,
             'knows': [CORRAN_KNOWS], 'secret': CORRAN_SECRET},
        ],
    }


def _safe_mode(app, monkeypatch) -> None:
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


def _joined(app, socketio, ids: dict[str, Any]):
    socket_client = socketio.test_client(app, flask_test_client=app.test_client())
    socket_client.emit(
        'join_session',
        {'session_id': ids['session_id'], 'player_id': ids['player_id'], 'workspace_id': ids['workspace_id']},
    )
    socket_client.get_received()
    return socket_client


class MillTable(flagship.Table):
    """The flagship table without the Play Now quest: each step returns the snapshot it left."""

    def _changed(self, since: dict | None = None) -> dict:
        self.last = self.snapshot()
        return self.last


@pytest.fixture()
def road(app, client, socketio, monkeypatch):
    _safe_mode(app, monkeypatch)
    response = client.post('/api/onboarding/play-now', json={})
    assert response.status_code == 201, response.get_data(as_text=True)
    ids = {key: response.get_json()[key] for key in ('session_id', 'campaign_id', 'player_id', 'workspace_id')}
    socket_client = _joined(app, socketio, ids)
    yield flagship.Table(app, client, socket_client, ids)
    socket_client.disconnect()


@pytest.fixture()
def mill(app, client, socketio, monkeypatch):
    """The mill pack at its lane, one hero seated; returns the table and every event its socket received."""

    _safe_mode(app, monkeypatch)
    response = client.post('/api/campaigns/import-pack', json={'pack': _mill_pack()})
    assert response.status_code == 201, response.get_json()
    ids = {key: response.get_json()[key] for key in ('campaign_id', 'session_id')}
    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        player = Player(
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.campaign_id,
            name='Alice',
            character_name='Seraphina',
            class_='Fighter',
            level=1,
            stats=safe_json_dumps({'ability_scores': {'strength': 15, 'dexterity': 12, 'constitution': 14}}, {}),
        )
        db.session.add(player)
        db.session.commit()
        ids.update(player_id=player.player_id, workspace_id=campaign.workspace_id)
    socket_client = _joined(app, socketio, ids)
    events = smoke._recording(socket_client)
    yield MillTable(app, client, socket_client, ids), events
    socket_client.disconnect()


def _view(snapshot: dict) -> dict:
    return filter_session_snapshot_for_player(snapshot)


def _talk_offers(snapshot: dict) -> list[str]:
    """The Story view's talk suggestions (``storySuggestions.ts``): known people present, none during a fight."""

    view = _view(snapshot)
    if view['combat']['status'] in {'starting', 'active'}:
        return []
    present = set(view['currentScene']['activeNpcIds'])
    return [
        f"Talk to {npc['name']}"
        for npc in view['knownNpcs']
        if npc['id'] in present and str(npc.get('status') or '').lower() not in UNREACHABLE_STATUSES
    ]


def _met_by(table: flagship.Table, change_type: str) -> list[str]:
    """``metNpcIds`` on the last turn's applied change of this type."""

    pipeline = table.pipeline()
    applied = [change for key in ('immediateAppliedChanges', 'postAppliedChanges') for change in pipeline.get(key) or []]
    return next(change.get('metNpcIds') for change in applied if change['type'] == change_type)


def _met_beats(table: flagship.Table) -> list[str]:
    """Every ``met`` beat stored on the session's turns, in order."""

    with table.app.app_context():
        turns = DmTurn.query.filter_by(session_id=table.ids['session_id']).order_by(DmTurn.turn_id).all()
        return [
            beat['text']
            for turn in turns
            for beat in safe_json_loads(turn.metadata_json, {}).get('storyBeats') or []
            if beat['kind'] == 'met'
        ]


def _people(snapshot: dict) -> list[str]:
    return [person['name'] for person in _view(snapshot)['journal']['people']]


def _talk(table: flagship.Table, first_name: str, npc_id: str) -> tuple[str, str]:
    """Talk to someone by name, then ask the first thing the conversation offers: ``(topic id, its narration)``."""

    table.send(f'I talk to {first_name}', {'kind': 'message'})
    topic = _view(table.last)['currentScene']['conversation']['actions'][0]
    table.send(topic['label'], {'kind': 'talk', 'talk': {'action': 'topic', 'npc_id': npc_id, 'topic_id': topic['id']}})
    return topic['id'], table.narration()


def _tells_first_topic(table: flagship.Table, first_name: str, npc_id: str) -> bool:
    topic_id, narration = _talk(table, first_name, npc_id)
    return TOPIC_LINES[npc_id][topic_id] in narration


def test_the_people_of_the_road_are_met_on_arrival_and_can_be_talked_to(road):
    table = road
    start = _view(table.last)
    assert 'Radan' not in json.dumps(start)
    assert 'npc_radan_greycloak' not in {npc['id'] for npc in start['knownNpcs']}

    arrival = table.travel('loc_old_north_road', 'The Old North Road')
    assert _met_by(table, 'scene.move_location') == ['npc_radan_greycloak', 'npc_veyra_ashcap']
    assert [beat for beat in table.pipeline_beats() if beat['kind'] == 'met'] == [
        {'kind': 'met', 'text': 'Met — Radan Greycloak', 'refId': 'npc_radan_greycloak'},
        {'kind': 'met', 'text': 'Met — Veyra Ashcap', 'refId': 'npc_veyra_ashcap'},
    ]
    # The lanterns' fight starts on arrival; nobody is offered for talk until it is over.
    assert arrival.encounter == 'enc_pale_lantern_detour'
    assert _talk_offers(arrival.snapshot) == []
    after_fight = table.end_combat('all_enemies_defeated')
    assert _talk_offers(after_fight.snapshot) == ['Talk to Radan Greycloak', 'Talk to Veyra Ashcap']
    assert {'Radan Greycloak', 'Veyra Ashcap'} <= set(_people(after_fight.snapshot))
    assert _tells_first_topic(table, 'Radan', 'npc_radan_greycloak')

    table.travel('loc_waystation_scratched_names', 'Waystation of Scratched Names')
    assert _met_by(table, 'scene.move_location') == ['npc_serenil_westward_song']
    assert _talk_offers(table.last) == ['Talk to Serenil of the Westward Song']
    assert _tells_first_topic(table, 'Serenil', 'npc_serenil_westward_song')
    # The ambush there reveals the hidden valley.
    assert table.act('ws_memorial_wall', 'search', 'Memorial Wall').encounter == 'enc_waystation_ambush'
    assert table.end_combat('all_enemies_defeated').places == {'loc_valley_last_road_stone': 'discovered'}

    table.travel('loc_old_masons_yard', "Old Mason's Yard")
    assert _met_by(table, 'scene.move_location') == ['npc_brokk_stonehand']
    assert _talk_offers(table.last) == ['Talk to Brokk Stonehand']
    assert _tells_first_topic(table, 'Brokk', 'npc_brokk_stonehand')

    # Caelorn and the Cinder Scribe are listed at no place: arriving at theirs meets nobody.
    table.travel('loc_cairns_roadwardens', 'Cairns of the Roadwardens')
    assert _met_by(table, 'scene.move_location') is None
    table.end_combat('all_enemies_defeated')
    table.travel('loc_valley_last_road_stone', 'Valley of the Last Road-Stone')
    assert table.last['currentScene']['locationId'] == 'loc_valley_last_road_stone'
    assert _met_by(table, 'scene.move_location') is None
    known = {npc['id'] for npc in table.last['knownNpcs']}
    assert {'npc_caelorn_erased', 'npc_cinder_scribe'}.isdisjoint(known)

    assert _met_beats(table) == [
        'Met — Radan Greycloak',
        'Met — Veyra Ashcap',
        'Met — Serenil of the Westward Song',
        'Met — Brokk Stonehand',
    ]


def _player_texts(table: MillTable, events: list[dict]) -> str:
    """Everything the player was sent or can read: socket events, the player view, narration and beats."""

    persisted = [text for _label, text in smoke._persisted_texts(table.app, table.ids['session_id'])]
    return json.dumps([events, _view(table.last), persisted], ensure_ascii=False, default=str)


def _leaked(texts: str, *npc_ids: str) -> list[str]:
    """The held-back people's name, ``knows`` and ``secret`` found in ``texts`` (a line may be told without its full stop)."""

    return [text for npc_id in npc_ids for text in HELD_BACK[npc_id] if text.rstrip('.') in texts]


def test_hidden_and_hidden_until_hold_people_back_and_nothing_about_them_reaches_the_player(mill):
    table, events = mill
    assert _leaked(_player_texts(table, events), 'npc_ilsabet', 'npc_corran') == []

    table.travel('loc_mill', 'Old Mill')
    # Hesk is met; Wren too, as her flag already holds; Ilsabet waits for the wheel and Corran is hidden.
    assert _met_by(table, 'scene.move_location') == ['npc_hesk', 'npc_wren']
    assert table.last['currentScene']['activeNpcIds'] == ['npc_hesk', 'npc_wren']
    assert _talk_offers(table.last) == ['Talk to Hesk the Miller', 'Talk to Wren Lampwick']
    assert {npc['id']: npc['status'] for npc in table.last['knownNpcs']} == {'npc_hesk': 'met', 'npc_wren': 'met'}
    assert WREN_KNOWS.rstrip('.') in _talk(table, 'Wren', 'npc_wren')[1]
    assert _leaked(_player_texts(table, events), 'npc_ilsabet', 'npc_corran') == []

    # Turning the wheel sets the flag while the party stands there: Ilsabet is met then and joins the scene.
    table.act('mill_wheel', 'inspect', 'Mill Wheel')
    assert table.last['flags']['mill_wheel_turned'] is True
    assert _met_by(table, 'flag.set') == ['npc_ilsabet']
    assert {'kind': 'met', 'text': 'Met — Ilsabet Thornquill', 'refId': 'npc_ilsabet'} in table.pipeline_beats()
    assert table.last['currentScene']['activeNpcIds'] == ['npc_hesk', 'npc_wren', 'npc_ilsabet']
    assert 'Talk to Ilsabet Thornquill' in _talk_offers(table.last)
    assert ILSABET_KNOWS.rstrip('.') in _talk(table, 'Ilsabet', 'npc_ilsabet')[1]
    # Met, she reaches the player like anyone known; her secret never does.
    assert _leaked(_player_texts(table, events), 'npc_ilsabet') == ['Ilsabet Thornquill', ILSABET_KNOWS]
    # Talking with Hesk shows his topics' labels; an untold line, his reactions, wants and beliefs never.
    table.send('I talk to Hesk', {'kind': 'message'})
    assert [action['label'] for action in _view(table.last)['currentScene']['conversation']['actions']] == ['Say something else', 'Leave it there']
    assert [text for text in HESK_UNSAID if text in _player_texts(table, events)] == []

    # Corran stays hidden whatever happens there, and away from the lane and back.
    table.travel('loc_lane', 'Mill Lane')
    table.travel('loc_mill', 'Old Mill')
    assert _met_by(table, 'scene.move_location') is None
    assert 'npc_corran' not in {npc['id'] for npc in table.last['knownNpcs']}
    assert 'npc_corran' not in table.last['currentScene']['activeNpcIds']
    assert _leaked(_player_texts(table, events), 'npc_corran') == []
    assert _met_beats(table) == ['Met — Hesk the Miller', 'Met — Wren Lampwick', 'Met — Ilsabet Thornquill']


def test_a_hidden_person_revealed_by_an_explicit_change_can_be_talked_to(mill):
    table, _events = mill
    table.travel('loc_mill', 'Old Mill')

    table.operator([{'type': 'npc.discover', 'npcId': 'npc_corran', 'name': 'Corran Vey', 'locationId': 'loc_mill'}])

    assert 'Talk to Corran Vey' in _talk_offers(table.last)


def test_an_older_save_meets_the_people_of_its_place_with_its_next_change(road):
    table = road
    # A save made at the road before arrival met people: they are present but unknown.
    saved = deepcopy(table.last)
    saved['currentScene'].update(locationId='loc_old_north_road', activeNpcIds=['npc_radan_greycloak', 'npc_veyra_ashcap'])

    result = apply_state_changes(saved, [{'id': 'old_save_mood', 'type': 'scene.update', 'mood': 'tense'}])

    assert result['appliedChanges'][0]['metNpcIds'] == ['npc_radan_greycloak', 'npc_veyra_ashcap']
    assert {'npc_radan_greycloak', 'npc_veyra_ashcap'} <= {npc['id'] for npc in result['nextState']['knownNpcs']}
    assert result['nextState']['currentScene']['activeNpcIds'] == ['npc_radan_greycloak', 'npc_veyra_ashcap']


def _hero(name: str) -> dict:
    return {'name': name, 'hp': 10, 'conditions': [], 'items': {}, 'currency': {}, 'xp': 0, 'level': 1}


@pytest.mark.parametrize('heroes', [['player_1'], ['player_1', 'player_2']])
def test_one_met_beat_per_person_reads_the_same_alone_and_in_party_play(heroes):
    basis = {'heroes': {hero_id: _hero(hero_id) for hero_id in heroes}}
    catalog = {'npcs': [{'id': 'npc_radan', 'name': 'Radan Greycloak'}, {'id': 'npc_veyra', 'name': 'Veyra Ashcap'}]}

    beats = story_beats(
        basis,
        basis,
        actor_id='player_1',
        state_before={'campaignPack': {'catalog': catalog}},
        applied_changes=[
            {'type': 'scene.move_location', 'metNpcIds': ['npc_radan', 'npc_veyra']},
            {'type': 'flag.set', 'metNpcIds': ['npc_radan']},
        ],
    )

    assert beats == [
        {'kind': 'met', 'text': 'Met — Radan Greycloak', 'refId': 'npc_radan'},
        {'kind': 'met', 'text': 'Met — Veyra Ashcap', 'refId': 'npc_veyra'},
    ]


def test_the_importer_keeps_hidden_and_stores_hidden_until_with_its_default(client, app):
    response = client.post('/api/campaigns/import-pack', json={'pack': _mill_pack()})
    assert response.status_code == 201, response.get_json()
    with app.app_context():
        snapshot = safe_json_loads(db.session.get(Session, response.get_json()['session_id']).state_snapshot, {})

    people = {npc['id']: npc for npc in snapshot['campaignPack']['catalog']['npcs']}
    assert people['npc_corran']['hidden'] is True
    assert people['npc_wren']['hiddenUntil'] == {'flagKey': 'lamp_lit', 'equals': True}
    assert people['npc_ilsabet']['hiddenUntil'] == {'flagKey': 'mill_wheel_turned', 'equals': True}


@pytest.mark.parametrize(
    ('field', 'value', 'message'),
    [
        ('hidden', 'yes', 'npcs[3].hidden must be a boolean.'),
        ('hiddenUntil', {'equals': True}, 'npcs[3].hiddenUntil.flagKey is required.'),
    ],
)
def test_the_schema_rejects_a_malformed_hidden_or_hidden_until(client, field, value, message):
    pack = _mill_pack()
    pack['npcs'][3][field] = value

    response = client.post('/api/campaigns/import-pack', json={'pack': pack})

    assert response.status_code == 400
    assert response.get_json()['error'] == message
