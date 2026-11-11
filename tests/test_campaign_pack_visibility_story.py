"""What the player projection tells the Story screen: offered actions, take labels, enemy looks and the journal."""

from __future__ import annotations

from copy import deepcopy
import json

from aidm_server.services.campaign_pack_visibility import (
    acting_hero,
    filter_session_snapshot_for_player,
    with_scene_offers,
)


def _strongbox() -> dict:
    return {
        'id': 'strongbox',
        'name': 'Strongbox',
        'kind': 'container',
        'open': False,
        'locked': True,
        'requirements': {'unlock': {'anyItemIds': ['box_key']}},
        'actionLabels': {'search': 'Search the strongbox'},
        'actionText': {'search': 'Under the straw lies a sealed letter.'},
    }


def _hidden_cache() -> dict:
    return {'id': 'cache', 'name': 'Loose Floorboard', 'kind': 'container', 'hidden': True, 'open': True}


def _hero(player_id: int, *items: str) -> dict:
    return {
        'id': f'player_{player_id}',
        'playerId': player_id,
        'name': f'Hero {player_id}',
        'inventory': {'items': [{'id': item, 'name': item.replace('_', ' ').title()} for item in items]},
        'spellResources': {'slots': {'1': {'current': 2, 'max': 2}}},
    }


def _snapshot() -> dict:
    objects = [_strongbox(), _hidden_cache()]
    return {
        'currentScene': {
            'locationId': 'vault',
            'name': 'Vault',
            'interactables': deepcopy(objects),
            'hazards': [],
            'items': [
                {'id': 'ring', 'name': 'Silver Ring'},
                {'id': 'vault_coins', 'name': '15 gp', 'type': 'currency', 'currency': {'gp': 15}},
            ],
        },
        'locations': [
            {'id': 'vault', 'name': 'The Vault', 'status': 'visited', 'sceneState': {'interactables': deepcopy(objects), 'hazards': []}},
            {'id': 'stair', 'name': 'Long Stair'},
        ],
        'playerCharacters': [_hero(1), _hero(2, 'box_key'), _hero(3, 'box_key')],
        'activePlayerIds': [1, 2],
        'knownNpcs': [
            {
                'id': 'npc_warden',
                'name': 'Old Warden',
                'role': 'gate_warden_and_witness',
                'disposition': 'guarded_helpful',
                'playerSummary': 'Keeps the vault keys.',
                'secret': 'Sold the second key.',
            }
        ],
        'quests': [
            {
                'id': 'quest_vault',
                'title': 'The Vault',
                'status': 'active',
                'playerSummary': 'Find out who emptied the vault.',
                'summary': 'DM summary: the warden did it.',
                'objectives': [
                    {'id': 'obj_enter', 'title': 'Enter the vault', 'status': 'completed'},
                    {'id': 'obj_search', 'title': 'Search the vault', 'optional': True},
                    {'id': 'obj_bribe', 'title': 'Bribe the warden', 'status': 'failed'},
                    {'id': 'obj_confront', 'title': 'Confront the warden'},
                ],
            }
        ],
        'clues': [{'id': 'clue_scratch', 'name': 'Scratched Lock', 'description': 'Someone forced the lock.', 'gmNotes': 'The warden.'}],
        'campaignPack': {
            'packId': 'vault_pack',
            'title': 'Vault',
            'activeCheckpointId': 'cp_vault',
            'startingQuestId': 'quest_vault',
            'checkpoints': [
                {
                    'id': 'cp_vault',
                    'title': 'The Empty Vault',
                    'playerSummary': 'Look for what was taken.',
                    'objectiveIds': ['obj_search', 'obj_bribe'],
                    'questIds': ['quest_vault'],
                },
                {'id': 'cp_confront', 'title': 'The Warden', 'objectiveIds': ['obj_confront']},
            ],
        },
        'combat': {
            'status': 'active',
            'participants': [
                {'id': 'player_1', 'playerId': 1, 'name': 'Hero 1', 'team': 'player', 'hp': {'current': 9, 'max': 12}},
                {'id': 'enemy_rat', 'name': 'Rat', 'team': 'enemy', 'hp': {'current': 5, 'max': 11}},
                {'id': 'ally_dog', 'name': 'Dog', 'team': 'ally', 'hp': {'current': 4, 'max': 6}},
            ],
        },
    }


def _search(view: dict) -> dict:
    strongbox = next(entry for entry in view['currentScene']['interactables'] if entry['id'] == 'strongbox')
    return next(action for action in strongbox['offeredActions'] if action['action'] == 'search')


def test_offered_actions_are_judged_for_the_named_hero():
    snapshot = _snapshot()
    # Out of a fight an object's actions are judged by the object and the hero alone.
    snapshot['combat']['status'] = 'ended'

    keyless = filter_session_snapshot_for_player(snapshot, private_player_ids={1}, acting_player_id=1)
    keyed = filter_session_snapshot_for_player(snapshot, private_player_ids={2}, acting_player_id=2)

    assert _search(keyless) == {
        'action': 'search',
        'label': 'Search the strongbox',
        'authored': True,
        'legal': False,
        'done': False,
        'reason': 'The Strongbox is locked.',
    }
    assert _search(keyed)['legal'] is True
    assert [entry['id'] for entry in keyed['currentScene']['interactables']] == ['strongbox']
    assert 'actionText' not in json.dumps(keyed)
    assert keyed['playerCharacters'][1]['spellResources'] == _hero(2)['spellResources']
    assert 'inventory' not in keyed['playerCharacters'][0]


def test_without_a_named_hero_legality_is_for_the_first_present_hero():
    snapshot = _snapshot()

    assert acting_hero(snapshot)['playerId'] == 1
    assert acting_hero(snapshot, 3)['playerId'] == 3
    assert acting_hero(snapshot, 99)['playerId'] == 1
    assert _search(filter_session_snapshot_for_player(snapshot, private_player_ids={2}))['legal'] is False
    snapshot['activePlayerIds'] = [2, 3]
    assert acting_hero(snapshot)['playerId'] == 2
    snapshot['activePlayerIds'] = []
    assert acting_hero(snapshot)['playerId'] == 1


def test_loose_items_carry_a_take_label_in_both_projections():
    snapshot = _snapshot()

    player_items = filter_session_snapshot_for_player(snapshot)['currentScene']['items']
    full_items = with_scene_offers(snapshot)['currentScene']['items']

    assert [item['takeLabel'] for item in player_items] == ['Take the Silver Ring', 'Take 15 gp']
    assert [item['takeLabel'] for item in full_items] == ['Take the Silver Ring', 'Take 15 gp']


def test_the_full_projection_offers_actions_only_on_objects_the_hero_can_see_and_never_writes_back():
    snapshot = _snapshot()
    original = deepcopy(snapshot)

    full = with_scene_offers(snapshot, acting_player_id=2)

    by_id = {entry['id']: entry for entry in full['currentScene']['interactables']}
    assert [action['action'] for action in by_id['strongbox']['offeredActions']] == [
        'search', 'open', 'unlock', 'inspect', 'close', 'lock',
    ]
    assert 'offeredActions' not in by_id['cache']
    assert by_id['strongbox']['actionText'] == _strongbox()['actionText']
    assert snapshot == original


def test_players_see_how_an_enemy_looks_but_not_its_hit_points():
    participants = {
        participant['id']: participant
        for participant in filter_session_snapshot_for_player(_snapshot())['combat']['participants']
    }

    assert 'hp' not in participants['enemy_rat']
    assert participants['enemy_rat']['health'] == 'Wounded'
    assert participants['ally_dog']['hp'] == {'current': 4, 'max': 6}


def test_the_journal_holds_the_stage_and_only_reached_objectives():
    journal = filter_session_snapshot_for_player(_snapshot())['journal']

    assert journal['quest'] == {
        'id': 'quest_vault',
        'title': 'The Vault',
        'summary': 'Find out who emptied the vault.',
        'status': 'active',
        'stageTitle': 'The Empty Vault',
        'stageSummary': 'Look for what was taken.',
    }
    assert journal['objectives'] == [
        {'id': 'obj_enter', 'title': 'Enter the vault', 'optional': False, 'status': 'completed'},
        {'id': 'obj_search', 'title': 'Search the vault', 'optional': True, 'status': 'active'},
    ]
    assert 'Confront the warden' not in json.dumps(journal)
    assert journal['clues'] == [{'id': 'clue_scratch', 'title': 'Scratched Lock', 'text': 'Someone forced the lock.'}]
    assert journal['people'] == [
        {'id': 'npc_warden', 'name': 'Old Warden', 'role': 'gate warden and witness', 'summary': 'Keeps the vault keys.', 'met': True}
    ]
    assert journal['places'] == [
        {'id': 'vault', 'name': 'The Vault', 'state': 'current'},
        {'id': 'stair', 'name': 'Long Stair', 'state': 'discovered'},
    ]
    assert 'guarded_helpful' not in json.dumps(journal)
    assert 'Sold the second key' not in json.dumps(journal)


def test_a_hidden_stage_shows_only_its_player_title():
    snapshot = _snapshot()
    stage = snapshot['campaignPack']['checkpoints'][0]
    stage.update({'dmOnly': True, 'playerTitle': 'Somewhere Below'})
    stage.pop('playerSummary')

    quest = filter_session_snapshot_for_player(snapshot)['journal']['quest']

    assert (quest['stageTitle'], quest['stageSummary']) == ('Somewhere Below', '')


def test_a_session_without_a_campaign_pack_has_no_journal():
    snapshot = _snapshot()
    snapshot.pop('campaignPack')

    assert 'journal' not in filter_session_snapshot_for_player(snapshot)


def _initiative_snapshot(**combat) -> dict:
    """A beast that won initiative, then the hero, then a lantern; it is the hero's turn."""

    return {
        'playerCharacters': [_hero(2)],
        'activePlayerIds': [2],
        'combat': {
            'status': 'active',
            'round': 1,
            'turnIndex': 1,
            'participants': [
                {'id': 'enemy_beast', 'name': 'Wardbound Beast', 'team': 'enemy', 'hp': {'current': 9, 'max': 9}},
                {'id': 'player_2', 'playerId': 2, 'name': 'Hero 2', 'team': 'player', 'hp': {'current': 10, 'max': 10}},
                {'id': 'enemy_lantern', 'name': 'Pale Lantern', 'team': 'enemy', 'hp': {'current': 4, 'max': 4}},
            ],
            'initiative': [
                {'participantId': 'enemy_beast', 'total': 16, 'order': 0},
                {'participantId': 'player_2', 'total': 12, 'order': 1},
                {'participantId': 'enemy_lantern', 'total': 6, 'order': 2},
            ],
            'flags': {'activeActorId': 'player_2'},
            **combat,
        },
    }


def test_the_turn_index_counts_in_initiative_order_as_the_full_view_does():
    combat = filter_session_snapshot_for_player(_initiative_snapshot(), private_player_ids={2})['combat']

    assert combat['turnIndex'] == 1
    assert [entry['participantId'] for entry in combat['initiative']][combat['turnIndex']] == 'player_2'


def test_a_hidden_participant_ahead_of_the_actor_shifts_the_turn_index_past_it():
    snapshot = _initiative_snapshot()
    snapshot['combat']['participants'][0]['hiddenToPlayers'] = True

    combat = filter_session_snapshot_for_player(snapshot, private_player_ids={2})['combat']

    assert [entry['participantId'] for entry in combat['initiative']] == ['player_2', 'enemy_lantern']
    assert combat['turnIndex'] == 0


def test_without_an_active_actor_the_stored_index_still_counts_in_initiative_order():
    combat = filter_session_snapshot_for_player(_initiative_snapshot(turnIndex=2, flags={}))['combat']

    assert combat['turnIndex'] == 2
