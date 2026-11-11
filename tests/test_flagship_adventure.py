"""The Play Now adventure, played end to end in Safe Mode with typed actions only.

Every check and attack uses a seeded roller, and narration never decides a step:
each assertion names the state change the typed action itself caused.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

import pytest
import requests

from aidm_server.combat.legal_actions import legal_combat_actions_for_player
from aidm_server.database import db
from aidm_server.game_state.campaign_pack_encounters import materialize_campaign_pack_combat_start
from aidm_server.interactables import available_actions_for_target, interactable_actor_context
from aidm_server.models import DmTurn, Player, Session, safe_json_loads
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
ENDING = {
    'checkpointId': FINAL_CHECKPOINT['id'],
    'title': FINAL_CHECKPOINT['playerTitle'],
    'summary': FINAL_CHECKPOINT['playerSummary'],
}


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
        """Close in and swing with the HUD's own actions until the fight ends."""

        before = self.last
        for _round in range(6):
            snapshot = self.snapshot()
            if snapshot['combat']['status'] != 'active':
                break
            enemy = next(participant for participant in snapshot['combat']['participants'] if participant['team'] == 'enemy')
            if not self._hud_action('combat.attack.'):
                self._hud('Arden Vale closes in.', 'combat.advance', enemy['id'])
            attack = self._hud_action('combat.attack.')
            if attack:
                self._hud('Arden Vale attacks.', attack['id'], enemy['id'])
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

    def offers(self, target_id: str) -> dict[str, bool]:
        snapshot = self.snapshot()
        offered = available_actions_for_target(
            snapshot,
            location_id=snapshot['currentScene']['locationId'],
            target_id=target_id,
            actor=interactable_actor_context(snapshot, _hero(snapshot)),
        )
        return {entry['action']: entry['legal'] for entry in offered['actions']}

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
    assert table.act('inn_wagon_strongbox', 'open', 'Wagon Strongbox').snapshot['currentScene']['items'] == []

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
    # Wounded and alone, the cutthroat yields: a successful outcome, rewarded once.
    assert _rewards(fight.snapshot) == [('enc_empty_caravan_pressure', 'negotiation')]
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
    assert _enemies(road.snapshot) == ['enemy_pale_lanterns', 'enemy_wardbound_beasts']
    assert road.places == {
        'loc_old_north_road': 'visited',
        'loc_waystation_scratched_names': 'discovered',
        'loc_ridge_and_culvert_paths': 'discovered',
    }
    assert road.snapshot['currentScene']['restPolicy'] == 'risky'
    lanterns = table.end_combat('all_enemies_defeated')
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
    assert wall.flags == {'memorial_names_copied': True}
    assert wall.clues == {'clue_scratched_memorial_names'}
    assert wall.objectives == {'obj_recover_erased_names': 'completed'}
    assert wall.encounter == 'enc_waystation_ambush'
    assert _active_checkpoint(wall.snapshot) == 'cp_04_roadwarden_cairns'
    ambush = table.end_combat('all_enemies_defeated')
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

    cairns = table.travel('loc_cairns_roadwardens', 'Cairns of the Roadwardens')
    assert cairns.snapshot['flags']['roadwardens_appeased'] is False
    assert cairns.encounter == 'enc_roadwarden_judgment'
    assert _enemies(cairns.snapshot) == ['enemy_name_eaten_roadwardens']
    judgment = table.end_combat('negotiated_resolution')
    assert judgment.flags == {'roadwardens_appeased': True}
    assert judgment.objectives == {'obj_confront_roadwardens': 'completed'}
    assert (judgment.gp, judgment.xp) == (60, 450)

    valley = table.travel('loc_valley_last_road_stone', 'Valley of the Last Road-Stone')
    assert valley.objectives == {'obj_reach_final_valley': 'completed'}
    assert (valley.encounter, valley.snapshot['combat']['status']) == (None, 'none')
    assert _active_checkpoint(valley.snapshot) == 'cp_06_final_valley'
    assert table.offers('valley_last_road_stone') == {
        'break': False, 'inspect': True, 'lock': False, 'trigger': False, 'use': False,
    }

    binding = table.act('valley_last_road_stone', 'inspect', 'The Last Road-Stone')
    assert binding.encounter == 'enc_final_binding'
    assert _enemies(binding.snapshot) == [
        'enemy_ashmarked_cutthroats',
        'enemy_cinder_scribe',
        'enemy_hollow_marshal',
        'enemy_name_eaten_roadwardens',
    ]
    victory = table.end_combat('all_enemies_defeated')
    assert victory.flags == {'cinder_scribe_resolved': True}
    assert victory.clues == {'clue_binding_quill'}
    assert (victory.gp, victory.xp, victory.items['Ash-Ink Binding Quill']) == (150, 1100, 1)
    assert table.offers('valley_last_road_stone') == {
        'break': True, 'inspect': True, 'lock': True, 'trigger': True, 'use': True,
    }

    renewal = table.act('valley_last_road_stone', 'use', 'The Last Road-Stone')
    assert renewal.flags == {'ward_renewed': True, 'travelers_freed': True, 'quest_road_complete': True}
    assert renewal.objectives == {'obj_decide_ward_fate': 'completed'}
    assert _quest_status(renewal.snapshot) == 'completed'
    assert (renewal.gp, renewal.xp, renewal.items["Roadwarden's Token"]) == (60, 900, 1)
    assert _completed_checkpoints(renewal.snapshot)[-1] == 'cp_06_final_valley'
    assert _active_checkpoint(renewal.snapshot) == FINAL_CHECKPOINT['id']
    assert _ending(renewal.snapshot) == ENDING
    assert _rewards(renewal.snapshot) == [
        ('enc_empty_caravan_pressure', 'negotiation'),
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
    victory = table.end_combat('all_enemies_defeated')
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
    assert _ending(claim.snapshot) == ENDING
    assert table.offers('valley_last_road_stone') == {
        'break': False, 'inspect': True, 'lock': False, 'trigger': False, 'use': False,
    }
