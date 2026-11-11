"""The Play Now adventure, played end to end in Safe Mode with typed actions only.

Every check and attack uses a seeded roller, and narration never decides a step:
each assertion names the state change the typed action itself caused.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
import json
import math
from pathlib import Path
from typing import Any

import pytest
import requests

from aidm_server.combat.legal_actions import legal_combat_actions_for_player
from aidm_server.database import db
from aidm_server.combat.state import instantiate_creature
from aidm_server.game_state.campaign_pack_encounters import PARTY_SCALING, materialize_campaign_pack_combat_start
from aidm_server.interactables import available_actions_for_target, interactable_actor_context
from aidm_server.models import DmTurn, Player, Session, SessionLogEntry, safe_json_loads
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_file
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from aidm_server.services.pregen_characters import PREGENERATED_CHARACTER_PRESETS


PACK_PATH = Path(__file__).resolve().parents[1] / 'docs' / 'examples' / 'the_road_of_unremembered_kings_campaign.json'
PACK = json.loads(PACK_PATH.read_text(encoding='utf-8'))
QUEST = 'quest_road_unremembered_kings'
# Flags the pack declares; the diff below ignores the engine's bookkeeping flags.
PACK_FLAGS = {*PACK['startingState']['flags'], *(flag['key'] for flag in PACK['storyFlags']), 'quest_road_complete'}
OPTIONAL_OBJECTIVES = {
    'obj_investigate_empty_caravan',
    'obj_follow_changing_road',
    'obj_recover_erased_names',
    'obj_learn_ward_choices',
}
ENGINE_DIRECTOR_RULES = {'mainQuestGeneration', 'sideQuestGeneration', 'newNpcs', 'newLocations', 'offTrackPolicy', 'checkpointStyle'}
FINAL_CHECKPOINT = next(checkpoint for checkpoint in PACK['checkpoints'] if checkpoint.get('terminal'))
ENDING_VARIANTS = {variant['when']['flagKey']: variant for variant in FINAL_CHECKPOINT['endingVariants']}
FATES = ['use', 'break', 'lock', 'trigger']


def _ending_for(flag_key: str) -> dict:
    """The ending as the player view reports it once the fate behind ``flag_key`` was chosen."""

    variant = ENDING_VARIANTS[flag_key]
    return {
        'checkpointId': FINAL_CHECKPOINT['id'],
        'title': variant['title'],
        'summary': FINAL_CHECKPOINT['playerSummary'],
        'text': variant['text'],
    }
FINAL_BINDING = next(encounter for encounter in PACK['encounters'] if encounter['id'] == 'enc_final_binding')
SCENE_OBJECTS = {record['id']: record for location in PACK['locations'] for record in location.get('interactables', [])}
ROAD_STONE = SCENE_OBJECTS['valley_last_road_stone']
BANNER_STONE_OATH = SCENE_OBJECTS['cairn_banner_stone']['actionText']['use']


@dataclass
class Change:
    """What one typed step changed in the session snapshot."""

    flags: dict[str, Any] = field(default_factory=dict)
    clues: set[str] = field(default_factory=set)
    objectives: dict[str, str] = field(default_factory=dict)
    places: dict[str, str] = field(default_factory=dict)
    gp: int = 0
    xp: int = 0
    items: Counter = field(default_factory=Counter)
    encounter: str | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)


def _flags(snapshot: dict) -> dict[str, Any]:
    return {key: value for key, value in snapshot['flags'].items() if key in PACK_FLAGS}


def _objectives(snapshot: dict) -> dict[str, str]:
    quest = next(quest for quest in snapshot['quests'] if quest['id'] == QUEST)
    return {objective['id']: objective.get('status', 'open') for objective in quest['objectives']}


def _places(snapshot: dict) -> dict[str, str]:
    return {location['id']: location.get('status') or 'known' for location in snapshot['locations']}


def _hero(snapshot: dict) -> dict:
    return snapshot['playerCharacters'][0]


def _item_counts(snapshot: dict) -> Counter:
    counts: Counter = Counter()
    for item in _hero(snapshot)['inventory']['items']:
        counts[item['name']] += item.get('quantity') or 1
    return counts


def _triggered(snapshot: dict) -> list[str]:
    return snapshot['flags'].get('campaignPackTriggeredEncounterIds') or []


def _diff(before: dict, after: dict) -> Change:
    old_flags, old_objectives, old_places = _flags(before), _objectives(before), _places(before)
    new_encounters = _triggered(after)[len(_triggered(before)):]
    return Change(
        flags={key: value for key, value in _flags(after).items() if old_flags.get(key) != value},
        clues={clue['id'] for clue in after.get('clues') or []} - {clue['id'] for clue in before.get('clues') or []},
        objectives={key: value for key, value in _objectives(after).items() if old_objectives.get(key) != value},
        places={key: value for key, value in _places(after).items() if old_places.get(key) != value},
        gp=_hero(after)['inventory']['currency']['gp'] - _hero(before)['inventory']['currency']['gp'],
        xp=_hero(after)['xp']['current'] - _hero(before)['xp']['current'],
        items=_item_counts(after) - _item_counts(before),
        encounter=new_encounters[-1] if new_encounters else None,
        snapshot=after,
    )


class Table:
    """One Play Now hero at the table, acting through the intents the client sends."""

    def __init__(self, app, client, socket_client, ids: dict[str, Any]):
        self.app = app
        self.client = client
        self.socket = socket_client
        self.ids = ids
        self.turn = 0
        self.last = self.snapshot()

    def snapshot(self) -> dict:
        with self.app.app_context():
            return safe_json_loads(db.session.get(Session, self.ids['session_id']).state_snapshot, {})

    def _changed(self, since: dict | None = None) -> Change:
        after = self.snapshot()
        change = _diff(self.last if since is None else since, after)
        self.last = after
        return change

    def send(self, message: str, intent: dict) -> Change:
        self.turn += 1
        message_id = f'flagship-{self.turn}'
        self.socket.emit(
            'send_message',
            {
                'session_id': self.ids['session_id'],
                'campaign_id': self.ids['campaign_id'],
                'player_id': self.ids['player_id'],
                'message': message,
                'client_message_id': message_id,
                'action_intent': {**intent, 'text': message, 'client_message_id': message_id},
            },
        )
        received = self.socket.get_received()
        errors = [event['args'][0] for event in received if event['name'] == 'error' and event['args']]
        assert errors == [], f'{message}: {errors}'
        assert any(event['name'] == 'dm_response_end' for event in received), message
        return self._changed()

    def refused(self, target: str, action: str, name: str) -> list[str]:
        """The refusals a typed object action draws (it changes nothing)."""

        self.turn += 1
        message = f'Arden Vale attempts to {action} {name}.'
        self.socket.emit(
            'send_message',
            {
                'session_id': self.ids['session_id'],
                'campaign_id': self.ids['campaign_id'],
                'player_id': self.ids['player_id'],
                'message': message,
                'client_message_id': f'flagship-{self.turn}',
                'action_intent': {'kind': 'object', 'source': 'scene_panel', 'object': {'id': target, 'action': action}, 'text': message},
            },
        )
        return [event['args'][0]['error'] for event in self.socket.get_received() if event['name'] == 'error' and event['args']]

    def act(self, target: str, action: str, name: str) -> Change:
        # The client's own wording; the object rolls its own check on the server.
        intent = {'kind': 'object', 'source': 'scene_panel', 'object': {'id': target, 'action': action}}
        return self.send(f'Arden Vale attempts to {action} {name}.', intent)

    def travel(self, location_id: str, name: str) -> Change:
        intent = {'kind': 'travel', 'source': 'scene_panel', 'location': {'id': location_id, 'name': name}}
        return self.send(f'The party travels to {name}.', intent)

    def take(self, item_id: str, name: str) -> Change:
        intent = {
            'kind': 'item',
            'source': 'scene_panel',
            'inventory_action': 'pick_up',
            'item': {'id': item_id, 'name': name, 'quantity': 1},
        }
        return self.send(f'Arden Vale picks up {name}.', intent)

    def buy(self, merchant_id: str, stock_id: str, name: str) -> Change:
        intent = {
            'kind': 'shop',
            'source': 'scene_panel',
            'shop': {'action': 'buy', 'merchant_id': merchant_id, 'stock_id': stock_id, 'quantity': 1},
        }
        return self.send(f'Arden Vale buys {name}.', intent)

    def end_combat(self, end_reason: str) -> Change:
        """End the active combat through the operator combat API."""

        response = self.client.post(
            f"/api/sessions/{self.ids['session_id']}/combat/apply-state-changes",
            json={'changes': [{'type': 'combat.end', 'status': 'ended', 'endReason': end_reason}]},
        )
        assert response.status_code == 200, response.get_json()
        assert response.get_json()['validation']['rejected'] == []
        return self._changed()

    def fight(self) -> Change:
        """Swing at the first enemy the HUD's attack can reach (closing in first when it must) until the fight ends."""

        before = self.last
        for _round in range(12):
            if self.snapshot()['combat']['status'] != 'active':
                break
            attack = self._hud_action('combat.attack.')
            target = next((target for target in (attack or {}).get('targets') or [] if target['available']), None)
            if target:
                self._hud('Arden Vale attacks.', attack['id'], target['id'])
            if self.snapshot()['combat']['status'] == 'active':
                self._hud('Arden Vale ends the turn.', 'combat.end_turn')
        return self._changed(since=before)

    def _hud_action(self, prefix: str) -> dict | None:
        with self.app.app_context():
            bundle = legal_combat_actions_for_player(self.snapshot(), db.session.get(Player, self.ids['player_id']))
        return next((action for action in bundle['actions'] if action['id'].startswith(prefix) and action['available']), None)

    def _hud(self, message: str, action_id: str, target_id: str | None = None) -> None:
        combat = {'action_id': action_id, **({'target_id': target_id} if target_id else {})}
        self.send(message, {'kind': 'combat', 'source': 'combat_hud', 'combat': combat})

    def narration(self) -> str:
        """The last Dungeon Master reply, as the log keeps it."""

        with self.app.app_context():
            entry = (
                SessionLogEntry.query.filter_by(session_id=self.ids['session_id'], entry_type='dm')
                .filter(SessionLogEntry.message.startswith('DM: '))
                .order_by(SessionLogEntry.id.desc())
                .first()
            )
            return entry.message

    def pipeline(self) -> dict:
        """The last turn's state pipeline record."""

        with self.app.app_context():
            turn = DmTurn.query.filter_by(session_id=self.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
            return safe_json_loads(turn.metadata_json, {})['state_pipeline']

    def operator(self, changes: list[dict]) -> Change:
        """Set the table up through the operator state API."""

        response = self.client.post(f"/api/sessions/{self.ids['session_id']}/combat/apply-state-changes", json={'changes': changes})
        assert response.status_code == 200, response.get_json()
        assert response.get_json()['validation']['rejected'] == []
        return self._changed()

    def offers(self, target_id: str) -> dict[str, bool]:
        snapshot = self.snapshot()
        offered = available_actions_for_target(
            snapshot,
            location_id=snapshot['currentScene']['locationId'],
            target_id=target_id,
            actor=interactable_actor_context(snapshot, _hero(snapshot)),
        )
        return {entry['action']: entry['legal'] for entry in offered['actions']}

    def pipeline_beats(self) -> list[dict]:
        """The last turn's story beats."""

        with self.app.app_context():
            turn = DmTurn.query.filter_by(session_id=self.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
            return safe_json_loads(turn.metadata_json, {}).get('storyBeats') or []

    def last_check(self) -> tuple[str, int, str]:
        with self.app.app_context():
            turn = DmTurn.query.filter_by(session_id=self.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
            pipeline = safe_json_loads(turn.metadata_json, {})['state_pipeline']
        check = next(
            change['event']['check']
            for change in pipeline['immediateAppliedChanges']
            if change['type'] == 'scene.interactable.action'
        )
        return check['skill'], check['dc'], check['outcome']


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
    # Seeded rollers: object checks roll 15, the hero rolls 15 to hit, and
    # enemies roll 1. Initiative is the server's own.
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 14 if sides == 20 else sides - 1)
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda _sides: 1)

    response = client.post('/api/onboarding/play-now', json={})
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
    return [entry['id'] for entry in filter_session_snapshot_for_player(snapshot)['currentScene']['interactables']]


def _enemies(snapshot: dict) -> list[str]:
    return sorted(
        participant['campaignPackEnemyId']
        for participant in snapshot['combat']['participants']
        if participant['team'] == 'enemy'
    )


def _active_checkpoint(snapshot: dict) -> str | None:
    return snapshot['campaignPack'].get('activeCheckpointId')


def _completed_checkpoints(snapshot: dict) -> list[str]:
    return snapshot['campaignPack'].get('completedCheckpointIds') or []


def _rewards(snapshot: dict) -> list[tuple[str, str]]:
    return sorted((entry['encounterId'], entry['combatOutcome']) for entry in snapshot.get('combatRewardLedger') or [])


def _quest_status(snapshot: dict) -> str:
    return next(quest for quest in snapshot['quests'] if quest['id'] == QUEST)['status']


def _ending(snapshot: dict) -> dict | None:
    return filter_session_snapshot_for_player(snapshot)['campaignPack'].get('ending')


def _scene_items(change: Change) -> list[str]:
    return [item['id'] for item in change.snapshot['currentScene']['items']]


def test_the_pack_lints_strictly_clean_and_keeps_only_engine_director_rules(app):
    with app.app_context():
        result = lint_campaign_pack_file(PACK_PATH, workspace_id='owner')

    assert result['issues'] == []
    assert set(PACK['directorRules']) == ENGINE_DIRECTOR_RULES
    assert len(PACK['gmNotes']) <= 700
    assert len(next(quest for quest in PACK['quests'] if quest['id'] == QUEST)['summary']) <= 400


def test_every_ready_made_hero_starts_with_25_gold():
    assert {preset.character_id: preset.stats['gold'] for preset in PREGENERATED_CHARACTER_PRESETS} == {
        'arden-vale': 25,
        'liora-quill': 25,
        'mara-fen': 25,
        'tovan-ember': 25,
    }


def test_the_caravan_cutthroats_scale_to_the_party(client, app):
    response = client.post('/api/onboarding/play-now', json={})
    with app.app_context():
        snapshot = safe_json_loads(db.session.get(Session, response.get_json()['session_id']).state_snapshot, {})
    start = {'type': 'combat.start', 'campaignPackEncounterId': 'enc_empty_caravan_pressure', 'combat': {}}
    hero = _hero(snapshot)
    party = [hero, *({**hero, 'id': f'player_{index}', 'playerId': index} for index in (91, 92, 93))]

    alone = materialize_campaign_pack_combat_start(snapshot, start)
    full = materialize_campaign_pack_combat_start({**snapshot, 'playerCharacters': party}, start)

    assert _enemies(alone) == ['enemy_ashmarked_cutthroats']
    assert _enemies(full) == ['enemy_ashmarked_cutthroats'] * 2


def test_the_careful_route_renews_the_ward(table):
    start = table.last
    assert _hero(start)['inventory']['currency']['gp'] == 25
    assert _flags(start) == {key: False for key in PACK['startingState']['flags']}
    assert (start['currentScene']['locationId'], start['currentScene']['updatedAtTurn']) == ('loc_lantern_post_inn', 0)
    assert start['currentScene']['restPolicy'] == 'safe'
    assert _visible_objects(start) == ['inn_caravan_ledger', 'inn_drivers_bench', 'inn_lead_wagon', 'inn_wagon_bell']
    assert [flag['key'] for flag in start['campaignPack']['storyFlags']] == [flag['key'] for flag in PACK['storyFlags']]
    world_map = filter_session_snapshot_for_player(start)['worldMap']
    assert (world_map['title'], world_map['terrain'], world_map['seed']) == ('The Western Roadlands', 'moor', 1107)
    assert [(node['id'], node['icon'], node['x'], node['y'], node['state']) for node in world_map['nodes']] == [
        ('loc_lantern_post_inn', 'inn', 22, 82, 'current'),
        ('loc_old_north_road', 'road', 38, 62, 'known'),
    ]
    assert world_map['routes'] == [
        {'from': 'loc_lantern_post_inn', 'to': 'loc_old_north_road', 'style': 'road', 'travelable': True}
    ]

    ledger = table.act('inn_caravan_ledger', 'inspect', 'Caravan Ledger')
    assert ledger.flags == {'caravan_ledger_read': True}
    assert ledger.clues == {'clue_erased_caravan_ledger'}
    assert ledger.objectives == {'obj_investigate_empty_caravan': 'completed'}
    assert _completed_checkpoints(ledger.snapshot) == ['cp_01_empty_caravan']

    bench = table.act('inn_drivers_bench', 'search', "Driver's Bench")
    assert table.last_check() == ('investigation', 10, 'success')
    assert [item['id'] for item in bench.snapshot['currentScene']['items']] == ['caravan_strongbox_key']
    assert table.take('caravan_strongbox_key', 'Caravan Strongbox Key').items['Caravan Strongbox Key'] == 1

    wagon = table.act('inn_lead_wagon', 'search', 'Lead Wagon')
    assert wagon.clues == {'clue_ash_marks'}
    assert 'inn_wagon_strongbox' in _visible_objects(wagon.snapshot)
    table.act('inn_wagon_strongbox', 'unlock', 'Wagon Strongbox')
    opened = table.act('inn_wagon_strongbox', 'open', 'Wagon Strongbox')
    assert opened.snapshot['currentScene']['items'] == []
    # Breaking open a strongbox that already stands open gains nothing.
    assert ('break', 'Break the strongbox open', True) in [
        (action, label, done) for action, label, _legal, done in _offered(_story_view(opened.snapshot), 'inn_wagon_strongbox')
    ]

    strongbox = table.act('inn_wagon_strongbox', 'search', 'Wagon Strongbox')
    assert strongbox.flags == {'stone_fragment_recovered': True}
    assert strongbox.clues == {'clue_road_stone_fragment'}
    assert _scene_items(strongbox) == ['road_stone_fragment', 'inn_wagon_strongbox_coins']
    assert (strongbox.gp, strongbox.items) == (0, Counter())
    assert strongbox.encounter == 'enc_empty_caravan_pressure'
    assert _enemies(strongbox.snapshot) == ['enemy_ashmarked_cutthroats']
    # The cutthroat wins initiative and takes its opening turn in the same turn:
    # the hero is the one acting when the fight is handed over.
    combat = strongbox.snapshot['combat']
    assert combat['status'] == 'active'
    assert combat['initiative'][0]['participantId'].startswith('enemy_')
    assert combat['flags']['activeActorId'] == _hero(strongbox.snapshot)['id']

    fight = table.fight()
    assert fight.snapshot['combat']['status'] == 'ended'
    # Scaled for a lone hero, the cutthroat falls before it can yield: a victory, rewarded once.
    assert _rewards(fight.snapshot) == [('enc_empty_caravan_pressure', 'victory')]
    assert (fight.gp, fight.xp, fight.items['Ash-Marked Token']) == (25, 100, 1)
    assert table.take('road_stone_fragment', 'Pale Road-Stone Fragment').items['Pale Road-Stone Fragment'] == 1
    coins = table.take('inn_wagon_strongbox_coins', '15 gp')
    assert (coins.gp, coins.items) == (15, Counter())
    assert table.act('inn_wagon_bell', 'use', 'Wagon Bell').clues == {'clue_pale_lantern_bell'}

    potion = table.buy('merchant_mara_counter', 'merchant_mara_counter_0', 'a Potion of Healing')
    assert (potion.gp, potion.items['Potion of Healing']) == (-50, 1)
    assert potion.snapshot['merchants']['merchant_mara_counter']['stock'][0] == {
        'stockId': 'merchant_mara_counter_0',
        'catalog': 'potion_healing',
        'quantity': 2,
    }

    road = table.travel('loc_old_north_road', 'The Old North Road')
    assert road.encounter == 'enc_pale_lantern_detour'
    # A lone hero faces one enemy: the first group's.
    assert _enemies(road.snapshot) == ['enemy_pale_lanterns']
    assert road.places == {
        'loc_old_north_road': 'visited',
        'loc_waystation_scratched_names': 'discovered',
        'loc_ridge_and_culvert_paths': 'discovered',
    }
    assert road.snapshot['currentScene']['restPolicy'] == 'risky'
    # The lantern's lure wears off after two of the hero's turns; then the hero cuts the lantern down.
    lanterns = table.fight()
    assert lanterns.snapshot['combat']['status'] == 'ended'
    assert ('enc_pale_lantern_detour', 'victory') in _rewards(lanterns.snapshot)
    assert (lanterns.gp, lanterns.xp) == (20, 150)
    assert table.end_combat('all_enemies_defeated').gp == 0
    cart = table.act('road_abandoned_cart', 'search', 'Abandoned Handcart')
    assert _scene_items(cart) == ['road_cart_torch', 'road_abandoned_cart_coins']
    assert table.take('road_abandoned_cart_coins', '8 gp').gp == 8

    waystation = table.travel('loc_waystation_scratched_names', 'Waystation of Scratched Names')
    assert waystation.objectives == {'obj_follow_changing_road': 'completed'}
    assert waystation.places == {
        'loc_waystation_scratched_names': 'visited',
        'loc_cairns_roadwardens': 'discovered',
        'loc_old_masons_yard': 'discovered',
    }
    assert _active_checkpoint(waystation.snapshot) == 'cp_03_scratched_names'

    wall = table.act('ws_memorial_wall', 'search', 'Memorial Wall')
    assert table.last_check() == ('investigation', 12, 'success')
    # Copying the names is the find: the search does not also say it found nothing.
    assert 'You find nothing more.' not in table.narration()
    assert wall.flags == {'memorial_names_copied': True}
    assert wall.clues == {'clue_scratched_memorial_names'}
    assert wall.objectives == {'obj_recover_erased_names': 'completed'}
    assert wall.encounter == 'enc_waystation_ambush'
    assert _active_checkpoint(wall.snapshot) == 'cp_04_roadwarden_cairns'
    ambush = table.fight()
    assert ambush.snapshot['combat']['status'] == 'ended'
    assert ambush.flags == {'valley_route_known': True}
    assert ambush.places == {'loc_valley_last_road_stone': 'discovered'}
    assert ambush.clues == {'clue_final_valley_map'}
    assert (ambush.gp, ambush.xp, ambush.items['Ashmarked Orders']) == (40, 250, 1)

    hearth = table.act('ws_cold_hearth', 'search', 'Cold Hearth')
    assert hearth.flags == {'oath_learned': True}
    assert hearth.clues == {'clue_original_road_oath'}
    assert (_scene_items(hearth), hearth.items) == (['rusted_waystation_key'], Counter())
    table.take('rusted_waystation_key', 'Rusted Waystation Key')
    table.act('ws_lockbox', 'unlock', "Wardens' Lockbox")
    table.act('ws_lockbox', 'open', "Wardens' Lockbox")
    lockbox = table.act('ws_lockbox', 'search', "Wardens' Lockbox")
    assert lockbox.flags == {'true_name_known': True}
    assert lockbox.clues == {'clue_cinder_scribe_true_name'}
    assert _scene_items(lockbox) == ['ws_lockbox_potion_of_healing', 'ws_lockbox_coins']
    assert lockbox.gp == 0
    assert table.take('ws_lockbox_coins', '20 gp').gp == 20

    yard = table.travel('loc_old_masons_yard', "Old Mason's Yard")
    assert yard.snapshot['currentScene']['restPolicy'] == 'safe'
    assert _active_checkpoint(yard.snapshot) == 'cp_05_stonewright_warning'
    rubbings = table.act('yard_rubbing_table', 'inspect', 'Rubbing Table')
    assert rubbings.flags == {'ward_choices_known': True}
    assert rubbings.objectives == {'obj_learn_ward_choices': 'completed'}
    assert _active_checkpoint(rubbings.snapshot) == 'cp_06_final_valley'
    hammer = table.buy('merchant_brokk_tool_rack', 'merchant_brokk_tool_rack_0', "a Mason's Hammer")
    assert (hammer.gp, hammer.items["Mason's Hammer"]) == (-8, 1)
    assert 'masons_hammer' in {item['id'] for item in _hero(hammer.snapshot)['inventory']['items']}

    # The party learned the oath at the cold hearth, so the nameless dead do not rise against it.
    cairns = table.travel('loc_cairns_roadwardens', 'Cairns of the Roadwardens')
    assert cairns.snapshot['flags']['roadwardens_appeased'] is False
    assert (cairns.encounter, cairns.snapshot['combat']['status'] in {'none', 'ended'}) == (None, True)
    assert _offered(_story_view(cairns.snapshot), 'cairn_banner_stone') == [
        ('use', 'Speak the names at the banner-stone', True, False)
    ]
    judgment = table.act('cairn_banner_stone', 'use', 'Banner-Stone')
    assert judgment.flags == {'roadwardens_appeased': True}
    assert judgment.objectives == {'obj_confront_roadwardens': 'completed'}
    assert (judgment.gp, judgment.xp) == (60, 450)
    assert BANNER_STONE_OATH in table.narration()
    assert _rewards(judgment.snapshot).count(('enc_roadwarden_judgment', 'negotiation')) == 1
    again = table.act('cairn_banner_stone', 'use', 'Banner-Stone')
    assert (again.gp, again.xp, again.flags, again.encounter) == (0, 0, {}, None)
    assert _rewards(again.snapshot).count(('enc_roadwarden_judgment', 'negotiation')) == 1

    valley = table.travel('loc_valley_last_road_stone', 'Valley of the Last Road-Stone')
    assert valley.objectives == {'obj_reach_final_valley': 'completed'}
    assert (valley.encounter, valley.snapshot['combat']['status']) == (None, 'none')
    assert _active_checkpoint(valley.snapshot) == 'cp_06_final_valley'
    assert table.offers('valley_last_road_stone') == {
        'break': False, 'inspect': True, 'lock': False, 'trigger': False, 'use': False,
    }

    binding = table.act('valley_last_road_stone', 'inspect', 'The Last Road-Stone')
    assert binding.encounter == 'enc_final_binding'
    # What the party knows: the appeased Roadwardens stay away and the Scribe's true name weakens him.
    scaling = binding.snapshot['combat']['flags']['partyScaling']
    assert [adjustment['text'] for adjustment in scaling['adjustments']] == [
        adjustment['text'] for adjustment in FINAL_BINDING['adjustments']
    ]
    assert 'enemy_name_eaten_roadwardens' not in [group['enemyId'] for group in [*scaling['groups'], *scaling['droppedGroups']]]
    assert 'enemy_name_eaten_roadwardens' not in _enemies(binding.snapshot)
    scribe = next(participant for participant in binding.snapshot['combat']['participants'] if participant.get('campaignPackEnemyId') == 'enemy_cinder_scribe')
    authored_hp = instantiate_creature(next(enemy for enemy in PACK['enemies'] if enemy['id'] == 'enemy_cinder_scribe'), instance_id='scribe', team='enemy')['hp']['max']
    assert scribe['hp']['max'] == math.ceil(Fraction(authored_hp) * PARTY_SCALING[1][0] * Fraction(3, 4))
    narration = table.narration()
    assert all(adjustment['text'] in narration for adjustment in FINAL_BINDING['adjustments'])
    assert narration.index(FINAL_BINDING['setup']) < narration.index(FINAL_BINDING['adjustments'][0]['text'])
    # A narrating model is told the same lines as ones it must convey.
    instructions = '\n'.join(table.pipeline()['dmContextPacket']['dmInstructions'])
    assert FINAL_BINDING['setup'] in instructions
    assert all(adjustment['text'] in instructions for adjustment in FINAL_BINDING['adjustments'])
    victory = table.fight()
    assert victory.snapshot['combat']['status'] == 'ended'
    assert victory.flags == {'cinder_scribe_resolved': True}
    assert victory.clues == {'clue_binding_quill'}
    assert (victory.gp, victory.xp, victory.items['Ash-Ink Binding Quill']) == (150, 1100, 1)
    assert table.offers('valley_last_road_stone') == {
        'break': True, 'inspect': True, 'lock': True, 'trigger': True, 'use': True,
    }
    stone = _story_entry(table, 'valley_last_road_stone')
    # The four fates are choices, shown together.
    assert [(entry['action'], entry.get('choice'), entry['legal']) for entry in stone['offeredActions']] == [
        ('use', True, True), ('trigger', True, True), ('inspect', None, True), ('lock', True, True), ('break', True, True),
    ]

    renewal = table.act('valley_last_road_stone', 'use', 'The Last Road-Stone')
    assert ROAD_STONE['actionText']['use'] in table.narration()
    assert renewal.flags == {'ward_renewed': True, 'travelers_freed': True, 'quest_road_complete': True}
    assert renewal.objectives == {'obj_decide_ward_fate': 'completed'}
    assert _quest_status(renewal.snapshot) == 'completed'
    assert (renewal.gp, renewal.xp, renewal.items["Roadwarden's Token"]) == (60, 900, 1)
    assert _completed_checkpoints(renewal.snapshot)[-1] == 'cp_06_final_valley'
    assert _active_checkpoint(renewal.snapshot) == FINAL_CHECKPOINT['id']
    # The ending is the one the party chose, wherever it is reported.
    renewed = _ending_for('ward_renewed')
    assert _ending(renewal.snapshot) == renewed
    story = _story_state(table)
    assert story['campaignPack']['ending'] == renewed
    assert (story['journal']['quest']['stageTitle'], story['journal']['quest']['stageSummary']) == (renewed['title'], renewed['text'])
    assert {'kind': 'ending', 'text': f"The story ends — {renewed['title']}"} in table.pipeline_beats()
    # One fate: once it is chosen no other is offered.
    assert table.offers('valley_last_road_stone') == {
        'break': False, 'inspect': True, 'lock': False, 'trigger': False, 'use': False,
    }
    after = {entry['action']: entry for entry in _story_entry(table, 'valley_last_road_stone')['offeredActions']}
    assert {action: (after[action]['legal'], after[action].get('reason')) for action in FATES} == {
        'use': (False, 'That can no longer be done.'),
        'break': (False, 'That can no longer be done.'),
        'lock': (False, 'That can no longer be done.'),
        'trigger': (False, 'That can no longer be done.'),
    }
    assert _rewards(renewal.snapshot) == [
        ('enc_empty_caravan_pressure', 'victory'),
        ('enc_final_binding', 'victory'),
        ('enc_pale_lantern_detour', 'victory'),
        ('enc_roadwarden_judgment', 'negotiation'),
        ('enc_waystation_ambush', 'victory'),
    ]
    # Narration added nothing: every item came from a pickup, a purchase or a reward.
    assert set(_item_counts(renewal.snapshot)) == set(_item_counts(start)) | {
        'Caravan Strongbox Key',
        'Pale Road-Stone Fragment',
        'Ash-Marked Token',
        'Ashmarked Orders',
        'Rusted Waystation Key',
        "Mason's Hammer",
        'Ash-Ink Binding Quill',
        "Roadwarden's Token",
    }


def test_without_the_oath_the_dead_rise_and_the_banner_stone_stays_silent(table):
    table.travel('loc_old_north_road', 'The Old North Road')
    table.end_combat('all_enemies_defeated')
    table.travel('loc_waystation_scratched_names', 'Waystation of Scratched Names')
    assert table.act('ws_memorial_wall', 'search', 'Memorial Wall').encounter == 'enc_waystation_ambush'
    table.end_combat('all_enemies_defeated')

    cairns = table.travel('loc_cairns_roadwardens', 'Cairns of the Roadwardens')

    assert cairns.snapshot['flags']['oath_learned'] is False
    assert (cairns.encounter, cairns.snapshot['combat']['status']) == ('enc_roadwarden_judgment', 'active')
    stone = next(entry for entry in _story_view(cairns.snapshot)['currentScene']['interactables'] if entry['id'] == 'cairn_banner_stone')
    assert stone['offeredActions'] == [
        {
            'action': 'use',
            'label': 'Speak the names at the banner-stone',
            'authored': True,
            'legal': False,
            'done': False,
            'reason': "You don't know enough to do that yet.",
        }
    ]
    assert table.refused('cairn_banner_stone', 'use', 'Banner-Stone') == ["You don't know enough to do that yet."]
    after = table.snapshot()
    assert (after['flags']['roadwardens_appeased'], after['combat']['status']) == (False, 'active')
    assert ('enc_roadwarden_judgment', 'negotiation') not in _rewards(after)


@pytest.mark.parametrize('action', ['use', 'break', 'lock', 'trigger'])
def test_each_ending_is_told_in_the_road_stones_own_words(table, action):
    hero_id = _hero(table.last)['id']
    table.operator(
        [
            *({'type': 'flag.set', 'flagKey': key, 'flagValue': True} for key in ('cinder_scribe_resolved', 'oath_learned', 'memorial_names_copied')),
            {
                'type': 'inventory.add',
                'actorId': hero_id,
                'itemName': 'Pale Road-Stone Fragment',
                'item': {'id': 'road_stone_fragment', 'name': 'Pale Road-Stone Fragment', 'quantity': 1},
                'quantity': 1,
            },
            {
                'type': 'inventory.add',
                'actorId': hero_id,
                'itemName': "Mason's Hammer",
                'item': {'id': 'masons_hammer', 'name': "Mason's Hammer", 'quantity': 1, 'type': 'tool', 'tags': ['tool']},
                'quantity': 1,
            },
            {'type': 'location.discover', 'locationId': 'loc_valley_last_road_stone'},
            {'type': 'scene.move_location', 'locationId': 'loc_valley_last_road_stone', 'name': 'Valley of the Last Road-Stone'},
        ]
    )

    table.act('valley_last_road_stone', action, 'The Last Road-Stone')

    assert ROAD_STONE['actionText'][action] in table.narration()


def test_the_shortcut_reaches_the_valley_by_the_culvert(table):
    road = table.travel('loc_old_north_road', 'The Old North Road')
    assert road.encounter == 'enc_pale_lantern_detour'
    retreat = table.end_combat('players_fled')
    assert (retreat.gp, retreat.xp, retreat.flags) == (0, 0, {})

    ridge = table.travel('loc_ridge_and_culvert_paths', 'Ridge and Culvert Paths')
    assert ridge.places == {'loc_ridge_and_culvert_paths': 'visited', 'loc_cairns_roadwardens': 'discovered'}
    assert ridge.encounter is None
    assert ridge.snapshot['currentScene']['restPolicy'] == 'risky'

    table.act('ridge_culvert_grate', 'unlock', 'Culvert Grate')
    assert table.last_check() == ('athletics', 13, 'success')
    grate = table.act('ridge_culvert_grate', 'open', 'Culvert Grate')
    assert grate.flags == {'valley_route_known': True}
    assert grate.places == {'loc_valley_last_road_stone': 'discovered'}
    routes = filter_session_snapshot_for_player(grate.snapshot)['worldMap']['routes']
    assert {
        'from': 'loc_ridge_and_culvert_paths',
        'to': 'loc_valley_last_road_stone',
        'style': 'hidden',
        'travelable': True,
    } in routes

    valley = table.travel('loc_valley_last_road_stone', 'Valley of the Last Road-Stone')
    assert valley.objectives == {'obj_confront_roadwardens': 'completed', 'obj_reach_final_valley': 'completed'}
    assert valley.places == {'loc_valley_last_road_stone': 'visited', 'loc_old_masons_yard': 'discovered'}
    assert (valley.encounter, valley.snapshot['currentScene']['restPolicy']) == (None, 'none')

    binding = table.act('valley_last_road_stone', 'inspect', 'The Last Road-Stone')
    assert binding.encounter == 'enc_final_binding'
    victory = table.fight()
    assert victory.snapshot['combat']['status'] == 'ended'
    assert victory.flags == {'cinder_scribe_resolved': True}
    assert victory.clues == {'clue_binding_quill'}
    assert table.offers('valley_last_road_stone') == {
        'break': False, 'inspect': True, 'lock': False, 'trigger': True, 'use': False,
    }

    claim = table.act('valley_last_road_stone', 'trigger', 'The Last Road-Stone')
    assert claim.flags == {'ward_claimed': True, 'quest_road_complete': True}
    assert claim.objectives == {'obj_decide_ward_fate': 'completed'}
    assert (claim.gp, claim.xp, claim.items["Roadwarden's Token"]) == (60, 900, 1)
    assert _quest_status(claim.snapshot) == 'completed'
    assert {key: _objectives(claim.snapshot)[key] for key in OPTIONAL_OBJECTIVES} == {key: 'open' for key in OPTIONAL_OBJECTIVES}
    assert _ending(claim.snapshot) == _ending_for('ward_claimed')
    assert table.offers('valley_last_road_stone') == {
        'break': False, 'inspect': True, 'lock': False, 'trigger': False, 'use': False,
    }


def _story_view(snapshot: dict) -> dict:
    return filter_session_snapshot_for_player(snapshot, private_player_ids={_hero(snapshot)['playerId']})


def _story_state(table: Table) -> dict:
    """The state the Story view reads: the player view, told from the hero."""

    hero = _hero(table.snapshot())
    response = table.client.get(f"/api/sessions/{table.ids['session_id']}/state?view=player&player_id={hero['playerId']}")
    assert response.status_code == 200, response.get_json()
    return response.get_json()['state_snapshot']


def _story_entry(table: Table, object_id: str) -> dict:
    return next(entry for entry in _story_state(table)['currentScene']['interactables'] if entry['id'] == object_id)


def _offered(view: dict, object_id: str) -> list[tuple[str, str, bool, bool]]:
    entry = next(entry for entry in view['currentScene']['interactables'] if entry['id'] == object_id)
    return [(action['action'], action['label'], action['legal'], action['done']) for action in entry['offeredActions']]


def test_the_opening_offers_only_authored_actions_and_starts_in_the_evening(table):
    view = _story_view(table.last)

    assert view['clock']['label'] == 'Day 1 · Evening'
    assert "Something wrapped in waxed cloth is wedged beneath the driver's bench." in view['currentScene']['visibleDetails']
    assert 'pale stone' not in next(
        record for record in PACK['locations'][0]['interactables'] if record['id'] == 'inn_drivers_bench'
    )['description']
    assert {entry['id']: _offered(view, entry['id']) for entry in view['currentScene']['interactables']} == {
        'inn_caravan_ledger': [('inspect', 'Read the caravan ledger', True, False)],
        'inn_drivers_bench': [
            ('search', "Search under the driver's bench", True, False),
            ('inspect', "Look over the driver's bench", True, False),
        ],
        'inn_lead_wagon': [('search', 'Search the lead wagon', True, False)],
        'inn_wagon_bell': [('use', 'Ring the wagon bell', True, False)],
    }
    bench = next(entry for entry in view['currentScene']['interactables'] if entry['id'] == 'inn_drivers_bench')
    assert bench['offeredActions'][0]['check'] == {'skill': 'investigation', 'ability': 'intelligence'}


def test_every_object_offers_exactly_its_labelled_actions():
    for location in PACK['locations']:
        for record in location.get('interactables', []):
            assert set(record['allowedActions']) >= set(record['actionLabels']), record['id']
            assert set(record['allowedActions']) - set(record['actionLabels']) <= {'inspect'}, record['id']
            assert all(len(label) <= 48 for label in record['actionLabels'].values())
    assert {enemy['id']: (enemy.get('singularName'), enemy.get('proper')) for enemy in PACK['enemies']} == {
        'enemy_ashmarked_cutthroats': ('Ashmarked Cutthroat', None),
        'enemy_pale_lanterns': ('Pale Lantern', None),
        'enemy_name_eaten_roadwardens': ('Name-Eaten Roadwarden', None),
        'enemy_wardbound_beasts': ('Wardbound Beast', None),
        'enemy_hollow_marshal': (None, True),
        'enemy_cinder_scribe': (None, True),
    }


def test_the_journal_shows_only_the_objectives_the_party_has_reached(table):
    quest = next(quest for quest in PACK['quests'] if quest['id'] == QUEST)
    first = _story_view(table.last)['journal']
    assert first['objectives'] == [
        {'id': 'obj_investigate_empty_caravan', 'title': 'Investigate the empty caravan', 'optional': True, 'status': 'active'}
    ]
    assert (first['quest']['stageTitle'], first['clues']) == ('The Empty Caravan', [])

    journal = _story_view(table.act('inn_caravan_ledger', 'inspect', 'Caravan Ledger').snapshot)['journal']

    assert journal['objectives'] == [
        {'id': 'obj_investigate_empty_caravan', 'title': 'Investigate the empty caravan', 'optional': True, 'status': 'completed'},
        {'id': 'obj_follow_changing_road', 'title': 'Follow the changing road', 'optional': True, 'status': 'active'},
    ]
    assert journal['quest']['stageTitle'] == 'The Changing Road'
    assert [clue['id'] for clue in journal['clues']] == ['clue_erased_caravan_ledger']
    reached = {'obj_investigate_empty_caravan', 'obj_follow_changing_road'}
    rendered = json.dumps(journal)
    for objective in quest['objectives']:
        if objective['id'] not in reached:
            assert objective['title'] not in rendered
    assert _offered(_story_view(table.last), 'inn_caravan_ledger') == [
        ('inspect', 'Read the caravan ledger', False, True)
    ]


def test_one_search_opens_the_locked_strongbox_when_the_hero_holds_its_key(table):
    table.act('inn_drivers_bench', 'search', "Driver's Bench")
    table.take('caravan_strongbox_key', 'Caravan Strongbox Key')
    revealed = _story_view(table.act('inn_lead_wagon', 'search', 'Lead Wagon').snapshot)
    assert 'You uncover the Wagon Strongbox.' in table.narration()

    assert _offered(revealed, 'inn_wagon_strongbox') == [
        ('search', 'Search the strongbox', True, False),
        ('open', 'Open the strongbox', True, False),
        ('unlock', 'Unlock the strongbox', True, False),
        ('break', 'Break the strongbox open', True, False),
    ]

    strongbox = table.act('inn_wagon_strongbox', 'search', 'Wagon Strongbox')

    assert strongbox.flags == {'stone_fragment_recovered': True}
    assert strongbox.clues == {'clue_road_stone_fragment'}
    assert _scene_items(strongbox) == ['road_stone_fragment', 'inn_wagon_strongbox_coins']
    assert strongbox.encounter == 'enc_empty_caravan_pressure'
    with table.app.app_context():
        turn = DmTurn.query.filter_by(session_id=table.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
        pipeline = safe_json_loads(turn.metadata_json, {})['state_pipeline']
    action = next(change for change in pipeline['immediateAppliedChanges'] if change['type'] == 'scene.interactable.action')
    assert action['event']['steps'] == ['unlock', 'open']
    items = {item['id']: item['takeLabel'] for item in _story_view(strongbox.snapshot)['currentScene']['items']}
    assert items == {
        'road_stone_fragment': 'Take the Pale Road-Stone Fragment',
        'inn_wagon_strongbox_coins': 'Take 15 gp',
    }


def _assert_both_views_have_the_hero_acting(table: Table) -> None:
    hero = _hero(table.snapshot())
    state = f"/api/sessions/{table.ids['session_id']}/state"
    full = table.client.get(state).get_json()['state_snapshot']['combat']
    story = table.client.get(f"{state}?view=player&player_id={hero['playerId']}").get_json()['state_snapshot']['combat']
    assert story['turnIndex'] == full['turnIndex']
    for combat in (full, story):
        assert combat['flags']['activeActorId'] == hero['id']
        assert combat['turnOrder'][combat['turnIndex']]['id'] == hero['id']
        assert [entry['id'] for entry in combat['turnOrder'] if entry['isCurrent']] == [hero['id']]


def test_the_story_view_and_the_full_view_agree_whose_turn_it_is(table):
    road = table.travel('loc_old_north_road', 'The Old North Road')
    # Enemies win initiative and take their opening turns; then the hero acts.
    assert road.snapshot['combat']['initiative'][0]['participantId'].startswith('enemy_')
    _assert_both_views_have_the_hero_acting(table)

    table._hud('Arden Vale ends the turn.', 'combat.end_turn')

    combat = table.snapshot()['combat']
    assert (combat['round'], bool(combat['flags']['lastEnemyTurnBlock'])) == (2, True)
    _assert_both_views_have_the_hero_acting(table)
