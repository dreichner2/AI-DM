from __future__ import annotations

import json

from aidm_server.combat.legal_actions import (
    legal_combat_actions_for_player,
    resolve_combat_legal_action,
    with_combat_legal_actions,
)
from aidm_server.database import db
from aidm_server.models import Player, Session
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from tests.helpers import seed_world_campaign_player_session


def _active_combat(player_id: int, *, turn_index: int = 0) -> dict:
    return {
        'status': 'active',
        'round': 2,
        'turnIndex': turn_index,
        'battlefield': {
            'environmentType': 'dungeon_room',
            'visibility': 'clear',
            'lighting': 'dim',
            'cover': [{'id': 'sealed_wall', 'name': 'Sealed wall', 'coverType': 'full'}],
        },
        'participants': [
            {
                'id': f'player_{player_id}',
                'playerId': player_id,
                'name': 'Seraphina',
                'team': 'player',
                'kind': 'player_character',
                'hp': {'current': 20, 'max': 20},
                'position': {'rangeBand': 'near', 'zoneId': 'hall'},
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_goblin_1',
                'name': 'Goblin Sentry',
                'team': 'enemy',
                'kind': 'creature',
                'hp': {'current': 7, 'max': 7},
                'position': {'rangeBand': 'near', 'zoneId': 'hall'},
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_archer_1',
                'name': 'Distant Archer',
                'team': 'enemy',
                'kind': 'creature',
                'hp': {'current': 9, 'max': 9},
                'position': {'rangeBand': 'far', 'zoneId': 'gallery'},
                'isAlive': True,
                'isConscious': True,
            },
        ],
    }


def _player(player_id: int, *, ranged: bool = False) -> Player:
    weapon = (
        {'id': 'bow', 'name': 'Longbow', 'type': 'weapon', 'subtype': 'longbow', 'equipped': True}
        if ranged
        else {'id': 'blade', 'name': 'Longsword', 'type': 'weapon', 'subtype': 'longsword', 'equipped': True}
    )
    return Player(
        player_id=player_id,
        name='Alice',
        character_name='Seraphina',
        class_='Fighter',
        level=3,
        stats=json.dumps({'strength': 16, 'dexterity': 14}),
        inventory=json.dumps([weapon]),
    )


def test_legal_actions_use_stable_weapon_ids_turn_order_and_range_bands():
    player = _player(7)
    snapshot = {'combat': _active_combat(7)}

    bundle = legal_combat_actions_for_player(snapshot, player)

    assert bundle is not None
    assert bundle['playerId'] == 7
    assert bundle['round'] == 2
    assert bundle['isCurrentActor'] is True
    assert bundle['economy'] == {
        'tracking': 'persisted_turn_economy',
        'actionAvailable': True,
        'bonusActionAvailable': True,
        'movementAvailable': True,
        'reactionAvailable': True,
        'actionRemaining': 1,
        'bonusActionRemaining': 1,
        'reactionRemaining': 1,
        'movementRemaining': 1,
        'reactionTracked': True,
        'subTurnCountersTracked': True,
    }
    attack = next(action for action in bundle['actions'] if action['type'] == 'attack')
    assert all(action['type'] != 'defend' for action in bundle['actions'])
    assert attack['id'] == 'combat.attack.blade'
    assert attack['range']['classification'] == 'melee'
    targets = {target['id']: target for target in attack['targets']}
    assert targets['enemy_goblin_1']['available'] is True
    assert targets['enemy_archer_1']['available'] is False
    assert targets['enemy_archer_1']['reason'] == 'Target is at far range.'
    assert attack['economy']['tracking'] == 'persisted_turn_economy'
    assert attack['economy']['endsTurn'] is False
    assert attack['economy']['reactionTracked'] is True
    assert attack['economy']['subTurnCountersTracked'] is True


def test_ranged_action_allows_far_target_and_resolution_ignores_client_descriptions():
    player = _player(7, ranged=True)
    snapshot = {'combat': _active_combat(7)}

    resolved, error_code, error_message = resolve_combat_legal_action(
        snapshot,
        player,
        action_id='combat.attack.bow',
        target_id='enemy_archer_1',
    )

    assert error_code is None
    assert error_message is None
    assert resolved == {
        'action_id': 'combat.attack.bow',
        'action_type': 'attack',
        'economy': {
            'action': 1,
            'bonusAction': 0,
            'reaction': 0,
            'movement': 'optional',
            'movementCost': 0,
            'endsTurn': False,
            'tracking': 'persisted_turn_economy',
            'reactionTracked': True,
            'subTurnCountersTracked': True,
        },
        'authoritative': True,
        'target_id': 'enemy_archer_1',
        'target_name': 'Distant Archer',
        'weapon_id': 'bow',
        'weapon_name': 'Longbow',
        'damage_dice': '1d8',
        'damage_type': 'piercing',
        'range_band': 'far',
        'message': 'Seraphina attacks Distant Archer with Longbow.',
    }


def test_resolver_rejects_forged_actions_targets_and_out_of_turn_actor():
    player = _player(7)
    snapshot = {'combat': _active_combat(7)}

    forged, forged_code, _message = resolve_combat_legal_action(
        snapshot,
        player,
        action_id='combat.attack.admin_sword',
        target_id='enemy_goblin_1',
    )
    wrong_target, target_code, _message = resolve_combat_legal_action(
        snapshot,
        player,
        action_id='combat.attack.blade',
        target_id='enemy_archer_1',
    )
    out_of_turn_snapshot = {'combat': _active_combat(7, turn_index=1)}
    out_of_turn, turn_code, turn_message = resolve_combat_legal_action(
        out_of_turn_snapshot,
        player,
        action_id='combat.end_turn',
    )

    assert forged is None
    assert forged_code == 'combat_action_invalid'
    assert wrong_target is None
    assert target_code == 'combat_target_unavailable'
    assert out_of_turn is None
    assert turn_code == 'combat_action_unavailable'
    assert turn_message == 'Goblin Sentry is acting now.'


def test_missing_legacy_turn_index_recovers_deterministic_hud_authority():
    player = _player(7)
    combat = _active_combat(7)
    combat.pop('turnIndex')

    bundle = legal_combat_actions_for_player({'combat': combat}, player)
    resolved, error_code, error_message = resolve_combat_legal_action(
        {'combat': combat},
        player,
        action_id='combat.end_turn',
    )

    assert bundle is not None
    assert bundle['currentActorId'] == 'player_7'
    assert bundle['isCurrentActor'] is True
    assert any(action['available'] is True for action in bundle['actions'])
    assert resolved is not None
    assert error_code is None
    assert error_message is None


def test_explicit_participant_player_id_cannot_be_overridden_by_duplicate_character_name():
    current_player = _player(7)
    current_player.character_name = 'Shared Name'
    impersonator = _player(8)
    impersonator.character_name = 'Shared Name'
    combat = _active_combat(current_player.player_id)
    combat['participants'][0]['name'] = 'Shared Name'

    bundle = legal_combat_actions_for_player({'combat': combat}, impersonator)
    resolved, error_code, error_message = resolve_combat_legal_action(
        {'combat': combat},
        impersonator,
        action_id='combat.end_turn',
    )

    assert bundle is None
    assert resolved is None
    assert error_code == 'combat_not_active'
    assert error_message == 'No active combat action is available for this character.'


def test_legacy_identity_fallback_fails_closed_for_ambiguous_duplicate_names():
    player = _player(7)
    combat = _active_combat(player.player_id)
    combat['participants'][0].pop('playerId')
    combat['participants'][0]['id'] = 'legacy_seraphina_1'
    combat['participants'].insert(
        1,
        {
            **combat['participants'][0],
            'id': 'legacy_seraphina_2',
        },
    )

    bundle = legal_combat_actions_for_player({'combat': combat}, player)
    resolved, error_code, error_message = resolve_combat_legal_action(
        {'combat': combat},
        player,
        action_id='combat.end_turn',
    )

    assert bundle is None
    assert resolved is None
    assert error_code == 'combat_not_active'
    assert error_message == 'No active combat action is available for this character.'


def test_viewer_scoped_snapshot_only_contains_bundles_for_supplied_players():
    player = _player(7)
    other = _player(8)
    other.character_name = 'Borin'
    snapshot = {'combat': _active_combat(7)}
    snapshot['combat']['participants'].insert(
        1,
        {
            'id': 'player_8',
            'playerId': 8,
            'name': 'Borin',
            'team': 'player',
            'kind': 'player_character',
            'hp': {'current': 18, 'max': 18},
            'position': {'rangeBand': 'near'},
            'isAlive': True,
            'isConscious': True,
        },
    )

    projected = with_combat_legal_actions(snapshot, [player])

    assert [bundle['playerId'] for bundle in projected['combat']['legalActions']] == [7]
    assert 'legalActions' not in snapshot['combat']


def test_player_projection_discards_persisted_or_imported_legal_action_bundles():
    snapshot = {'combat': _active_combat(7)}
    snapshot['combat']['legalActions'] = [
        {'playerId': 99, 'actions': [{'id': 'combat.attack.private_weapon'}]}
    ]
    snapshot['combat']['legalActionsSchemaVersion'] = 999

    projected = filter_session_snapshot_for_player(snapshot, private_player_ids={7})

    assert 'legalActions' not in projected['combat']
    assert 'legalActionsSchemaVersion' not in projected['combat']


def test_player_projection_fails_closed_when_hidden_combatant_owns_the_turn():
    player = _player(7)
    combat = _active_combat(7, turn_index=1)
    combat['participants'][1]['hiddenToPlayers'] = True
    combat['flags'] = {
        'activeActorId': 'enemy_goblin_1',
        'activeActorName': 'Goblin Sentry',
        'turnOrder': ['player_7', 'enemy_goblin_1', 'enemy_archer_1'],
    }

    projected = filter_session_snapshot_for_player({'combat': combat}, private_player_ids={7})
    projected = with_combat_legal_actions(projected, [player])

    assert [actor['id'] for actor in projected['combat']['participants']] == [
        'player_7',
        'enemy_archer_1',
    ]
    assert projected['combat']['turnIndex'] is None
    assert 'activeActorId' not in projected['combat']['flags']
    bundle = projected['combat']['legalActions'][0]
    assert bundle['currentActorId'] is None
    assert all(action['available'] is False for action in bundle['actions'])


def test_session_state_endpoint_attaches_server_issued_combat_actions(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        session = db.session.get(Session, ids['session_id'])
        assert player is not None and session is not None
        player.stats = json.dumps({'strength': 16, 'dexterity': 14})
        player.inventory = json.dumps(
            [{'id': 'blade', 'name': 'Longsword', 'type': 'weapon', 'equipped': True, 'slot': 'main_hand'}]
        )
        session.state_snapshot = json.dumps({'combat': _active_combat(ids['player_id'])})
        db.session.commit()

    response = client.get(f"/api/sessions/{ids['session_id']}/state")

    assert response.status_code == 200
    combat = response.get_json()['state_snapshot']['combat']
    assert combat['legalActionsSchemaVersion'] == 1
    assert combat['legalActions'][0]['playerId'] == ids['player_id']
    assert combat['legalActions'][0]['actions'][0]['id'] == 'combat.attack.blade'
