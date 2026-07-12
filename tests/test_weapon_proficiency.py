from __future__ import annotations

from aidm_server.character_state import server_attack_roll_context
from aidm_server.database import db
from aidm_server.game_state.models import player_character_from_model
from aidm_server.models import Player, safe_json_dumps
from aidm_server.player_rolls import resolve_authoritative_player_roll
from aidm_server.response_dtos import party_player_payload
from aidm_server.weapon_proficiency import (
    default_weapon_proficiencies_for_class,
    match_weapon_proficiency,
    normalize_weapon_proficiencies,
    serialize_weapon_proficiencies,
)
from tests.helpers import seed_world_campaign_player_session


def _attacker(*, profile) -> Player:
    return Player(
        player_id=91,
        name='Player',
        character_name='Arden',
        class_='Fighter',
        level=3,
        stats=safe_json_dumps(
            {
                'ability_scores': {
                    'strength': 16,
                    'dexterity': 12,
                    'constitution': 12,
                    'intelligence': 10,
                    'wisdom': 10,
                    'charisma': 10,
                },
                'current_hp': 20,
                'max_hp': 20,
                'proficiency_bonus': 2,
            },
            {},
        ),
        inventory=safe_json_dumps(
            [
                {
                    'id': 'owned-longbow',
                    'name': 'Longbow',
                    'type': 'weapon',
                    'subtype': 'longbow',
                    'equipped': True,
                    'slot': 'two_hands',
                }
            ],
            [],
        ),
        weapon_proficiencies=serialize_weapon_proficiencies(profile),
    )


def test_weapon_proficiency_normalization_accepts_legacy_shapes_and_rejects_unknown_categories():
    assert normalize_weapon_proficiencies(
        {
            'categories': ['Martial Weapons', 'unknown'],
            'weapons': ['Rapier', {'ignored': True}],
            'weapon_ids': ['Starter_Rapier'],
        }
    ) == [
        'category:martial',
        'id:starter rapier',
        'weapon:rapier',
    ]


def test_server_defaults_cover_standard_categories_and_extended_class_starter_weapons():
    fighter = default_weapon_proficiencies_for_class('Fighter - Champion')
    oracle = default_weapon_proficiencies_for_class('Oracle - Battle Seer')

    assert {'category:simple', 'category:martial', 'weapon:longsword'}.issubset(fighter)
    assert 'weapon:quarterstaff' in oracle


def test_attack_roll_uses_persisted_player_profile_and_ignores_client_proficiency_claim():
    player = _attacker(profile=['category:martial'])
    attack_context = server_attack_roll_context(player, 'I fire my longbow.')
    roll = resolve_authoritative_player_roll(
        player=player,
        rule_type='attack',
        dc_hint='DC 14',
        action_intent={
            'kind': 'roll',
            'ability': {'key': 'strength'},
            'roll': {
                'die': 'd20',
                'modifier': 99,
                'proficient': False,
                'proficiency_bonus': 99,
            },
        },
        attack_context=attack_context,
        roller=lambda _sides: 10,
    )

    assert attack_context['proficient'] is True
    assert attack_context['proficiency_source'] == 'player_weapon_proficiencies'
    assert attack_context['proficiency_selector'] == 'category:martial'
    assert roll['ability']['key'] == 'dexterity'
    assert roll['proficiency'] == {'bonus': 2, 'skills': ['weapon:longbow']}
    assert roll['modifier'] == 3
    assert roll['total'] == 13


def test_empty_persisted_profile_does_not_accept_client_proficiency_claim():
    player = _attacker(profile=[])
    player.inventory = safe_json_dumps(
        [
            {
                'id': 'owned-longbow',
                'name': 'Longbow',
                'type': 'weapon',
                'subtype': 'longbow',
                'equipped': True,
                'slot': 'two_hands',
                # Legacy inventory assertions are migrated, not trusted live.
                'metadata': {'weaponProficient': True},
            }
        ],
        [],
    )
    attack_context = server_attack_roll_context(player, 'I fire my longbow.')
    roll = resolve_authoritative_player_roll(
        player=player,
        rule_type='attack',
        dc_hint='DC 14',
        action_intent={
            'kind': 'roll',
            'roll': {'die': 'd20', 'proficient': True, 'proficiency_bonus': 99},
        },
        attack_context=attack_context,
        roller=lambda _sides: 10,
    )

    assert attack_context['proficient'] is False
    assert roll['proficiency'] == {'bonus': 0, 'skills': []}
    assert roll['modifier'] == 1
    assert roll['total'] == 11


def test_weapon_profile_matches_specific_weapon_and_persisted_id():
    item = {'id': 'Moon-Blade', 'name': 'Silvered Rapier', 'type': 'weapon', 'subtype': 'rapier'}

    assert match_weapon_proficiency(['weapon:rapier'], item) == (True, 'weapon:rapier')
    assert match_weapon_proficiency(['id:moon blade'], item) == (True, 'id:moon blade')
    assert match_weapon_proficiency(['weapon:longbow'], item) == (False, None)


def test_weapon_profile_is_private_in_party_projection():
    player = _attacker(profile=['category:martial'])

    assert party_player_payload(player, include_private=True)['weapon_proficiencies'] == [
        'category:martial'
    ]
    assert 'weapon_proficiencies' not in party_player_payload(player, include_private=False)


def test_player_creation_persists_private_profile_and_class_change_replaces_it(client, app):
    ids = seed_world_campaign_player_session(app)
    create_response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Arden',
            'character_name': 'Arden Vale',
            'char_class': 'Fighter - Champion',
            # This is intentionally ignored; the server derives the profile.
            'weapon_proficiencies': ['category:all'],
        },
    )
    assert create_response.status_code == 201
    player_id = create_response.get_json()['player_id']

    detail = client.get(f'/api/players/{player_id}').get_json()
    assert 'category:all' not in detail['weapon_proficiencies']
    assert {'category:simple', 'category:martial', 'weapon:longsword'}.issubset(
        detail['weapon_proficiencies']
    )

    summary = client.get(f"/api/players/campaigns/{ids['campaign_id']}/players").get_json()
    assert all('weapon_proficiencies' not in item for item in summary)

    update_response = client.patch(
        f'/api/players/{player_id}',
        json={'char_class': 'Wizard', 'weapon_proficiencies': ['category:all']},
    )
    assert update_response.status_code == 200
    updated = update_response.get_json()
    assert 'category:all' not in updated['weapon_proficiencies']
    assert 'category:martial' not in updated['weapon_proficiencies']
    assert {'weapon:quarterstaff', 'weapon:dagger'}.issubset(updated['weapon_proficiencies'])

    with app.app_context():
        player = db.session.get(Player, player_id)
        assert player is not None
        assert normalize_weapon_proficiencies(player.weapon_proficiencies) == updated['weapon_proficiencies']
        assert player_character_from_model(player)['metadata']['weaponProficiencies'] == updated[
            'weapon_proficiencies'
        ]

    export_response = client.get(
        f"/api/sessions/{ids['session_id']}/export?player_id={player_id}"
    )
    assert export_response.status_code == 200
    export_payload = export_response.get_json()
    assert export_payload['selectedPlayer']['weapon_proficiencies'] == updated['weapon_proficiencies']
    exported_player = next(item for item in export_payload['players'] if item['player_id'] == player_id)
    assert exported_player['weapon_proficiencies'] == updated['weapon_proficiencies']
