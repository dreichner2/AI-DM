from __future__ import annotations

import re
from pathlib import Path

import pytest

from aidm_server.database import db
from aidm_server.game_state.models import player_character_from_model
from aidm_server.models import (
    DmTurn,
    Player,
    PlayerAction,
    Session,
    SessionStateMutationAudit,
    TurnEvent,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.starting_inventory import starting_inventory_for_class
from tests.helpers import seed_world_campaign_player_session


VALID_CREATION_SCORES = {
    'strength': 15,
    'dexterity': 14,
    'constitution': 13,
    'intelligence': 12,
    'wisdom': 8,
    'charisma': 8,
}


def _creation_stats(**extra):
    return {
        'ability_scores': dict(VALID_CREATION_SCORES),
        'point_buy': {'budget': 27},
        **extra,
    }


def _class_catalog_choices() -> list[str]:
    catalog_path = Path(__file__).resolve().parents[1] / 'aidm_frontend' / 'src' / 'classCatalog.ts'
    choices: list[str] = []
    in_class = False
    in_subclasses = False
    class_name = ''
    subclasses: list[str] = []
    for line in catalog_path.read_text().splitlines():
        stripped = line.strip()
        if stripped == 'cls({':
            in_class = True
            in_subclasses = False
            class_name = ''
            subclasses = []
            continue
        if not in_class:
            continue
        if not class_name:
            match = re.search(r"name: '([^']+)'", line)
            if match:
                class_name = match.group(1)
            continue
        if stripped.startswith('subclasses: ['):
            in_subclasses = True
            continue
        if in_subclasses:
            if stripped.startswith('],'):
                in_subclasses = False
                continue
            match = re.search(r"\['([^']+)'", line)
            if match:
                subclasses.append(match.group(1))
            continue
        if stripped == '}),':
            choices.append(class_name)
            choices.extend(f'{class_name} - {subclass}' for subclass in subclasses)
            in_class = False
    return choices


def test_create_player_accepts_structured_inventory_and_get_returns_parsed_inventory(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Borin',
            'character_name': 'Borin Stoneshield',
            'char_class': 'Fighter',
            'sex': 'male',
            'stats': _creation_stats(),
            'inventory': [
                {'name': 'Rope', 'quantity': 2},
                'Torch',
            ],
        },
    )
    assert response.status_code == 201

    player_id = response.get_json()['player_id']
    player_response = client.get(f'/api/players/{player_id}')
    assert player_response.status_code == 200
    payload = player_response.get_json()

    assert payload['sex'] == 'male'
    assert payload['profile_image'] == '/profile-icons/human_male.png'
    assert payload['inventory'] == [
        {'name': 'Rope', 'quantity': 2, 'weight': 10},
        {'name': 'Torch', 'quantity': 1, 'weight': 1},
    ]


def test_create_player_assigns_starting_inventory_from_class(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Borin',
            'character_name': 'Borin Stoneshield',
            'char_class': 'Fighter - Champion',
            'stats': _creation_stats(),
        },
    )
    assert response.status_code == 201

    player_id = response.get_json()['player_id']
    player_response = client.get(f'/api/players/{player_id}')
    assert player_response.status_code == 200
    inventory = {item['name']: item for item in player_response.get_json()['inventory']}

    assert inventory['Longsword']['type'] == 'weapon'
    assert inventory['Longsword']['equipped'] is True
    assert inventory['Longsword']['slot'] == 'main_hand'
    assert inventory['Shield']['equipped'] is True
    assert inventory['Shield']['slot'] == 'off_hand'
    assert inventory['Chain Mail']['equipped'] is True
    assert inventory['Chain Mail']['slot'] == 'body_armor'
    assert inventory['Ration']['quantity'] == 5
    assert inventory['Torch']['quantity'] == 5

    with app.app_context():
        player = db.session.get(Player, player_id)
        actor = player_character_from_model(player)
        assert actor['stats']['armorClass'] == 18
        assert actor['metadata']['armorClassBreakdown']['shieldBonus'] == 2


def test_player_character_from_model_ignores_non_finite_persisted_armor_metadata(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert player is not None
        player.stats = safe_json_dumps({'dexterity': 14}, {})
        player.inventory = (
            '[{"name":"Homebrew Armor","type":"armor","equipped":true,'
            '"slot":"body_armor","baseAc":1e10000}]'
        )
        db.session.commit()

        actor = player_character_from_model(player)

    assert actor['stats']['armorClass'] == 13
    assert actor['metadata']['armorClassBreakdown']['armorBase'] == 11


def test_get_player_returns_derived_armor_class_without_overwriting_raw_stats(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert player is not None
        player.stats = safe_json_dumps({'dexterity': 14, 'ac': 12, 'armor_class': 12}, {})
        player.inventory = safe_json_dumps(
            [
                {
                    'id': 'scale_mail',
                    'name': 'Scale Mail',
                    'quantity': 1,
                    'type': 'armor',
                    'subtype': 'medium armor',
                    'equipped': True,
                    'slot': 'body_armor',
                },
                {
                    'id': 'shield',
                    'name': 'Shield',
                    'quantity': 1,
                    'type': 'armor',
                    'subtype': 'shield',
                    'equipped': True,
                    'slot': 'off_hand',
                },
            ],
            [],
        )
        db.session.commit()

    response = client.get(f"/api/players/{ids['player_id']}")
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['stats'] == {'dexterity': 14, 'ac': 12, 'armor_class': 12}
    assert payload['derived']['armorClass'] == 18
    assert payload['derived']['armorClassBreakdown']['armorName'] == 'Scale Mail'
    assert payload['derived']['armorClassBreakdown']['shieldBonus'] == 2


def test_create_player_assigns_starting_inventory_from_extended_class(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Cass',
            'character_name': 'Cass Quickshot',
            'char_class': 'Gunslinger - Sniper',
            'stats': _creation_stats(),
        },
    )
    assert response.status_code == 201

    player_response = client.get(f"/api/players/{response.get_json()['player_id']}")
    assert player_response.status_code == 200
    inventory = {item['name']: item for item in player_response.get_json()['inventory']}

    assert inventory['Long Rifle']['type'] == 'weapon'
    assert inventory['Long Rifle']['equipped'] is True
    assert inventory['Long Rifle']['slot'] == 'two_hands'
    assert inventory['Pistol']['type'] == 'weapon'
    assert inventory['Pistol'].get('equipped', False) is False
    assert inventory['Leather Armor']['equipped'] is True
    assert inventory['Ammunition']['quantity'] == 30
    assert "Gunsmith's Tools" in inventory


def test_starting_inventory_covers_catalog_classes_and_subclasses():
    missing = [
        class_choice
        for class_choice in _class_catalog_choices()
        if not starting_inventory_for_class(class_choice)
    ]

    assert missing == []


def test_subclass_starting_inventory_changes_role_defining_gear():
    archer = {item['name']: item for item in starting_inventory_for_class('Fighter - Archer')}
    shieldbearer = {item['name']: item for item in starting_inventory_for_class('Fighter - Shieldbearer')}
    emt = {item['name']: item for item in starting_inventory_for_class('Public Safety Officer - EMT')}
    attorney = {item['name']: item for item in starting_inventory_for_class('Legal Professional - Defense Attorney')}
    accountant = {item['name']: item for item in starting_inventory_for_class('Business Professional - Accountant')}

    assert archer['Longbow']['equipped'] is True
    assert archer['Longbow']['slot'] == 'two_hands'
    assert archer['Arrow']['quantity'] == 20
    assert archer['Shield'].get('equipped', False) is False
    assert archer['Studded Leather Armor']['equipped'] is True

    assert shieldbearer['Shield']['equipped'] is True
    assert shieldbearer['Reinforced Armor']['equipped'] is True
    assert shieldbearer['Armor Repair Kit']['type'] == 'tool'

    assert emt['Medical Bag']['type'] == 'gear'
    assert emt["Healer's Kit"]['type'] == 'tool'
    assert 'Sidearm' not in emt

    assert attorney['Legal Briefcase']['type'] == 'gear'
    assert attorney['Case Files']['type'] == 'gear'
    assert accountant['Laptop']['type'] == 'tool'
    assert accountant['Ledger']['type'] == 'gear'


def test_create_player_assigns_starting_spells_from_magical_class_and_race(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Nessa',
            'character_name': 'Nessa Ember',
            'race': 'Tiefling',
            'char_class': 'Wizard - Evoker',
            'stats': _creation_stats(),
        },
    )
    assert response.status_code == 201

    player_response = client.get(f"/api/players/{response.get_json()['player_id']}")
    assert player_response.status_code == 200
    sheet = player_response.get_json()['character_sheet']
    known = {spell['name'] for spell in sheet['spellbook']['knownSpells']}

    assert {'Fire Bolt', 'Magic Missile', 'Shield', 'Detect Magic'} <= known
    assert {'Thaumaturgy', 'Hellish Rebuke'} <= known


def test_update_player_level_up_unlocks_more_spells(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Nessa',
            'character_name': 'Nessa Ember',
            'char_class': 'Wizard',
            'level': 1,
            'stats': _creation_stats(xp=6500),
        },
    )
    assert response.status_code == 201
    player_id = response.get_json()['player_id']

    update_response = client.patch(f'/api/players/{player_id}', json={'level': 5})
    assert update_response.status_code == 200
    sheet = update_response.get_json()['character_sheet']
    known = {spell['name'] for spell in sheet['spellbook']['knownSpells']}

    assert 'Fireball' in known
    assert 'Counterspell' in known
    assert 'Magic Missile' in known


def test_profile_level_increase_requires_persisted_xp_and_rejection_grants_nothing(client, app):
    ids = seed_world_campaign_player_session(app)
    created = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'character_name': 'Nessa Earned',
            'char_class': 'Wizard',
            'level': 1,
            'stats': _creation_stats(xp=900),
        },
    )
    assert created.status_code == 201
    player_id = created.get_json()['player_id']

    earned = client.patch(f'/api/players/{player_id}', json={'level': 3})
    assert earned.status_code == 200
    assert earned.get_json()['level'] == 3

    forged = client.patch(
        f'/api/players/{player_id}',
        json={'level': 20, 'stats': _creation_stats(xp=355000)},
    )
    assert forged.status_code == 400
    assert forged.get_json()['error_code'] == 'gameplay_state_write_forbidden'

    forged_xp_only = client.patch(
        f'/api/players/{player_id}',
        json={'stats': _creation_stats(xp=355000)},
    )
    assert forged_xp_only.status_code == 400
    assert forged_xp_only.get_json()['error_code'] == 'gameplay_state_write_forbidden'

    too_high = client.patch(f'/api/players/{player_id}', json={'level': 4})
    assert too_high.status_code == 400

    reloaded = client.get(f'/api/players/{player_id}').get_json()
    names = {spell['name'] for spell in reloaded['character_sheet']['spellbook']['knownSpells']}
    assert reloaded['level'] == 3
    assert reloaded['stats']['xp'] == 900
    assert 'Fireball' not in names
    assert 'Counterspell' not in names


def test_class_and_race_respec_replaces_only_automatic_mechanics(client, app):
    ids = seed_world_campaign_player_session(app)
    created = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'character_name': 'Nessa Respecced',
            'race': 'Tiefling',
            'char_class': 'Wizard',
            'level': 3,
            'stats': _creation_stats(
                xp=900,
                race_ability_state={'infernal_step': {'available': False}},
                class_feature_state={'arcane_recovery': {'current': 0, 'max': 1}},
            ),
            'character_sheet': {
                'campaignNote': 'The observatory remembers her oath.',
                'classFeatureState': {'arcane_recovery': {'current': 0, 'max': 1}},
                'spellbook': {
                    'knownSpells': [
                        {
                            'name': 'Ashen Promise',
                            'level': 1,
                            'sourceType': 'story',
                            'sourceDetail': 'observatory_oath',
                            'description': 'A lesson earned by sparing the star-reader.',
                            'learnedFrom': 'The star-reader',
                        }
                    ]
                },
            },
        },
    )
    assert created.status_code == 201
    player_id = created.get_json()['player_id']

    updated = client.patch(
        f'/api/players/{player_id}',
        json={'char_class': 'Fighter', 'race': 'Human'},
    )
    assert updated.status_code == 200

    reloaded = client.get(f'/api/players/{player_id}').get_json()
    sheet = reloaded['character_sheet']
    known = {spell['name']: spell for spell in sheet['spellbook']['knownSpells']}
    assert {'Magic Missile', 'Fire Bolt', 'Hellish Rebuke', 'Thaumaturgy'}.isdisjoint(known)
    assert known['Ashen Promise']['description'] == 'A lesson earned by sparing the star-reader.'
    assert known['Ashen Promise']['learnedFrom'] == 'The star-reader'
    assert known['Ashen Promise']['sources'] == ['story:observatory_oath']
    assert sheet['campaignNote'] == 'The observatory remembers her oath.'
    assert 'classFeatureState' not in sheet
    assert sheet['spellResources']['castingMode'] == 'none'
    assert sheet['spellResources']['concentration'] is None
    assert 'class_feature_state' not in reloaded['stats']
    assert 'race_ability_state' not in reloaded['stats']
    surviving_names = set(known)
    assert set(sheet['spellbook']['preparedSpells']) <= surviving_names
    assert set(sheet['spells']) == surviving_names
    assert all(
        not source.startswith(('class:', 'level:', 'class_catalog:', 'race:', 'race_catalog:'))
        for source in sheet['spellbook']['sources']
    )


def test_create_player_respects_explicit_empty_inventory_over_class_starter(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Mira',
            'character_name': 'Mira Quickstep',
            'char_class': 'Rogue',
            'stats': _creation_stats(),
            'inventory': [],
        },
    )
    assert response.status_code == 201

    player_response = client.get(f"/api/players/{response.get_json()['player_id']}")
    assert player_response.status_code == 200
    assert player_response.get_json()['inventory'] == []


def test_get_player_does_not_backfill_starting_inventory_for_existing_blank_player(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.get(f"/api/players/{ids['player_id']}")
    assert response.status_code == 200
    assert response.get_json()['inventory'] == []

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert not player.inventory


def test_repair_starting_loadout_backfills_inventory_for_existing_blank_player(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(f"/api/players/{ids['player_id']}/repair-starting-loadout")
    assert response.status_code == 200
    payload = response.get_json()
    inventory = {item['name']: item for item in payload['inventory']}

    assert payload['repaired'] == {'inventory': True, 'spells': True}
    assert inventory['Longbow']['equipped'] is True
    assert inventory['Longbow']['slot'] == 'two_hands'
    assert inventory['Leather Armor']['slot'] == 'body_armor'
    assert inventory['Arrow']['quantity'] == 20

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert player.inventory


def test_get_player_does_not_backfill_starting_spells_for_existing_blank_player(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.class_ = 'Shapeshifter'
        player.race = 'Changeling'
        player.race_selection = None
        player.character_sheet = None
        db.session.commit()

    response = client.get(f"/api/players/{ids['player_id']}")
    assert response.status_code == 200
    assert response.get_json()['character_sheet'] is None

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert player.character_sheet is None


def test_repair_starting_loadout_backfills_spells_for_existing_blank_player(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps([], [])
        player.class_ = 'Shapeshifter'
        player.race = 'Changeling'
        player.race_selection = None
        player.character_sheet = None
        db.session.commit()

    response = client.post(f"/api/players/{ids['player_id']}/repair-starting-loadout")
    assert response.status_code == 200
    payload = response.get_json()
    sheet = payload['character_sheet']
    known = {spell['name'] for spell in sheet['spellbook']['knownSpells']}

    assert payload['repaired'] == {'inventory': False, 'spells': True}
    assert 'Primal Shift' in known
    assert 'Disguise Self' in known

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert player.character_sheet


def test_get_player_does_not_backfill_explicit_empty_inventory(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps([], [])
        db.session.commit()

    response = client.get(f"/api/players/{ids['player_id']}")
    assert response.status_code == 200
    assert response.get_json()['inventory'] == []


@pytest.mark.parametrize('score_shape', ['nested', 'flat'])
def test_create_player_validates_complete_point_buy_stats_in_both_shapes(client, app, score_shape):
    ids = seed_world_campaign_player_session(app)

    def shaped_stats(scores):
        return {'ability_scores': scores, 'point_buy': {'budget': 27}} if score_shape == 'nested' else scores

    valid_response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'name': 'Borin',
            'character_name': f'Borin Stoneshield {score_shape}',
            'char_class': 'Fighter',
            'stats': shaped_stats(dict(VALID_CREATION_SCORES)),
        },
    )
    assert valid_response.status_code == 201
    detail = client.get(f"/api/players/{valid_response.get_json()['player_id']}").get_json()
    assert detail['stats']['ability_scores'] == VALID_CREATION_SCORES

    missing = dict(VALID_CREATION_SCORES)
    missing.pop('charisma')
    invalid_scores = [
        missing,
        {**VALID_CREATION_SCORES, 'strength': 16},
        {key: 15 for key in VALID_CREATION_SCORES},
    ]
    for index, scores in enumerate(invalid_scores):
        invalid_response = client.post(
            f"/api/players/campaigns/{ids['campaign_id']}/players",
            json={
                'name': 'Mira',
                'character_name': f'Invalid {score_shape} {index}',
                'char_class': 'Fighter',
                'stats': shaped_stats(scores),
            },
        )
        assert invalid_response.status_code == 400
        assert invalid_response.get_json()['error_code'] == 'validation_error'

    with app.app_context():
        assert Player.query.filter(Player.character_name.like(f'Invalid {score_shape}%')).count() == 0


@pytest.mark.parametrize(
    'class_payload',
    [{}, {'char_class': None}, {'char_class': ''}, {'char_class': '   '}, {'class_': '   '}],
)
def test_create_player_requires_a_nonblank_class(client, app, class_payload):
    ids = seed_world_campaign_player_session(app)
    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'character_name': 'Classless Draft',
            'stats': _creation_stats(),
            **class_payload,
        },
    )

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'
    with app.app_context():
        assert Player.query.filter_by(character_name='Classless Draft').count() == 0


def test_create_player_requires_all_six_ability_scores(client, app):
    ids = seed_world_campaign_player_session(app)
    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={'character_name': 'Statless Draft', 'char_class': 'Fighter'},
    )

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'
    assert 'strength' in response.get_json()['error']
    with app.app_context():
        assert Player.query.filter_by(character_name='Statless Draft').count() == 0


def test_update_player_rejects_direct_gameplay_state_replacement_but_allows_profile_respec(client, app):
    ids = seed_world_campaign_player_session(app)

    before = client.get(f"/api/players/{ids['player_id']}").get_json()
    blocked = client.patch(
        f"/api/players/{ids['player_id']}",
        json={
            'stats': {'strength': 10, 'dexterity': 18},
            'character_sheet': {'current_hp': 22, 'max_hp': 28},
            'inventory': [{'name': 'Silver Key', 'quantity': 1}, 'Torch'],
            'weapon_proficiencies': ['category:all'],
        },
    )
    assert blocked.status_code == 400
    assert blocked.get_json()['error_code'] == 'gameplay_state_write_forbidden'
    assert blocked.get_json()['details']['fields'] == [
        'character_sheet',
        'inventory',
        'stats',
        'weapon_proficiencies',
    ]
    unchanged = client.get(f"/api/players/{ids['player_id']}").get_json()
    assert unchanged['stats'] == before['stats']
    assert unchanged['inventory'] == before['inventory']
    assert unchanged['character_sheet'] == before['character_sheet']

    response = client.patch(
        f"/api/players/{ids['player_id']}",
        json={
            'name': 'Alice Updated',
            'character_name': 'Seraphina Vale',
            'race': 'Half-Elf',
            'sex': 'female',
            'char_class': 'Rogue',
            'level': '3',
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['name'] == 'Alice Updated'
    assert payload['character_name'] == 'Seraphina Vale'
    assert payload['race'] == 'Elf'
    assert payload['race_selection'] == {
        'raceId': 'elf',
        'raceName': 'Elf',
        'source': 'curated',
        'selectedOptions': {},
    }
    assert payload['sex'] == 'female'
    assert payload['profile_image'] == '/profile-icons/elf_female.png'
    assert payload['class_'] == 'Rogue'
    assert payload['char_class'] == 'Rogue'
    assert payload['level'] == 3
    known = {spell['name'] for spell in payload['character_sheet']['spellbook']['knownSpells']}
    assert {'Minor Illusion', 'Detect Magic'} <= known
    assert payload['inventory'] == before['inventory']

    detail_response = client.get(f"/api/players/{ids['player_id']}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.get_json()
    assert detail_payload['inventory'] == before['inventory']


def test_manual_equipment_endpoint_enforces_slots_and_preserves_inventory_payload(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps(
            [
                {'id': 'great', 'name': 'Greatsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'greatsword'},
                {'id': 'long', 'name': 'Longsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'sword', 'equipped': True, 'slot': 'main_hand'},
                {'id': 'dagger', 'name': 'Dagger', 'quantity': 1, 'type': 'weapon', 'subtype': 'dagger', 'equipped': True, 'slot': 'off_hand'},
                {'id': 'hood', 'name': 'Travel Hood', 'quantity': 1, 'type': 'clothing', 'subtype': 'hood', 'equipped': True, 'slot': 'hood'},
                {'id': 'helmet', 'name': 'Iron Helmet', 'quantity': 1, 'type': 'armor', 'subtype': 'helmet', 'equipped': True, 'slot': 'helmet'},
            ],
            [],
        )
        session = db.session.get(Session, ids['session_id'])
        assert session is not None
        session.state_snapshot = safe_json_dumps(
            {
                'currentScene': {'name': 'Old Ruins'},
                'playerCharacters': [
                    {
                        'id': f"player_{ids['player_id']}",
                        'playerId': ids['player_id'],
                        'name': player.character_name,
                        'inventory': {
                            'items': [
                                {'id': 'long', 'name': 'Longsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'sword', 'equipped': True, 'slot': 'main_hand'},
                            ],
                            'currency': {},
                        },
                        'metadata': {},
                    }
                ],
                'stateChangeLedger': [],
            },
            {},
        )
        db.session.commit()

    response = client.patch(
        f"/api/players/{ids['player_id']}/inventory/equipment",
        json={'action': 'equip', 'item_id': 'great', 'session_id': ids['session_id']},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['snapshot_changed'] is True
    assert payload['equipment_update']['session_id'] == ids['session_id']
    assert payload['equipment_update']['snapshot_changed'] is True
    inventory = {item['id']: item for item in payload['inventory']}
    assert inventory['great']['equipped'] is True
    assert inventory['great']['slot'] == 'two_hands'
    assert inventory['long'].get('equipped', False) is False
    assert inventory['dagger'].get('equipped', False) is False
    assert inventory['hood']['equipped'] is True
    assert inventory['helmet']['equipped'] is True

    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        snapshot = safe_json_loads(session.state_snapshot, {})
        actor = next(actor for actor in snapshot['playerCharacters'] if actor['playerId'] == ids['player_id'])
        snapshot_inventory = {item['id']: item for item in actor['inventory']['items']}
        assert snapshot_inventory['great']['equipped'] is True
        assert snapshot_inventory['great']['slot'] == 'two_hands'
        assert snapshot_inventory['long'].get('equipped', False) is False
        assert snapshot_inventory['dagger'].get('equipped', False) is False
        audit = SessionStateMutationAudit.query.filter_by(session_id=ids['session_id']).one()
        assert audit.source == 'api.player.equipment'
        assert audit.actor == 'local_operator'
        assert audit.applied_change_count == 1
        assert audit.previous_revision == 0
        assert audit.state_revision == 1
        applied_change_ids = safe_json_loads(audit.applied_change_ids_json, [])
        assert len(applied_change_ids) == 1
        assert applied_change_ids[0].startswith('chg_')
        diff_paths = {entry['path'] for entry in safe_json_loads(audit.diff_json, [])}
        assert any('inventory.items' in path for path in diff_paths)

    unequip_response = client.patch(
        f"/api/players/{ids['player_id']}/inventory/equipment",
        json={'action': 'unequip', 'item_id': 'great', 'session_id': ids['session_id']},
    )
    assert unequip_response.status_code == 200
    assert unequip_response.get_json()['snapshot_changed'] is True
    assert {item['id']: item for item in unequip_response.get_json()['inventory']}['great'].get('equipped', False) is False
    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        snapshot = safe_json_loads(session.state_snapshot, {})
        actor = next(actor for actor in snapshot['playerCharacters'] if actor['playerId'] == ids['player_id'])
        snapshot_inventory = {item['id']: item for item in actor['inventory']['items']}
        assert snapshot_inventory['great'].get('equipped', False) is False


def test_manual_equipment_endpoint_rejects_stale_session_state_revision(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps(
            [{'id': 'great', 'name': 'Greatsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'greatsword'}],
            [],
        )
        session = db.session.get(Session, ids['session_id'])
        assert session is not None
        session.state_snapshot = safe_json_dumps(
            {
                'stateRevision': 2,
                'playerCharacters': [
                    {
                        'id': f"player_{ids['player_id']}",
                        'playerId': ids['player_id'],
                        'name': player.character_name,
                        'inventory': {'items': [], 'currency': {}},
                        'metadata': {},
                    }
                ],
                'stateChangeLedger': [],
            },
            {},
        )
        db.session.commit()

    response = client.patch(
        f"/api/players/{ids['player_id']}/inventory/equipment",
        json={
            'action': 'equip',
            'item_id': 'great',
            'session_id': ids['session_id'],
            'expectedStateRevision': 1,
        },
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload['error_code'] == 'state_conflict'
    assert payload['details']['expected_state_revision'] == 1
    assert payload['details']['actual_state_revision'] == 2
    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        snapshot = safe_json_loads(session.state_snapshot, {})
        actor = next(actor for actor in snapshot['playerCharacters'] if actor['playerId'] == ids['player_id'])
        assert actor['inventory']['items'] == []
        assert snapshot['stateRevision'] == 2
        assert 'stateMutationAudit' not in snapshot


def test_manual_equipment_endpoint_infers_sparse_axe_items(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps(
            [
                {'id': 'greataxe', 'name': 'Greataxe', 'quantity': 1},
                {'id': 'handaxe', 'name': 'Handaxe', 'quantity': 1, 'type': 'misc'},
            ],
            [],
        )
        db.session.commit()

    greataxe_response = client.patch(
        f"/api/players/{ids['player_id']}/inventory/equipment",
        json={'action': 'equip', 'item_id': 'greataxe'},
    )

    assert greataxe_response.status_code == 200
    inventory = {item['id']: item for item in greataxe_response.get_json()['inventory']}
    assert inventory['greataxe']['type'] == 'weapon'
    assert inventory['greataxe']['subtype'] == 'greataxe'
    assert inventory['greataxe']['equipped'] is True
    assert inventory['greataxe']['slot'] == 'two_hands'

    handaxe_response = client.patch(
        f"/api/players/{ids['player_id']}/inventory/equipment",
        json={'action': 'equip', 'item_id': 'handaxe'},
    )

    assert handaxe_response.status_code == 200
    inventory = {item['id']: item for item in handaxe_response.get_json()['inventory']}
    assert inventory['greataxe'].get('equipped', False) is False
    assert inventory['handaxe']['type'] == 'weapon'
    assert inventory['handaxe']['subtype'] == 'handaxe'
    assert inventory['handaxe']['equipped'] is True
    assert inventory['handaxe']['slot'] == 'main_hand'


def test_manual_equipment_endpoint_rejects_session_from_another_campaign(client, app):
    ids = seed_world_campaign_player_session(app)
    other_ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert player is not None
        player.inventory = safe_json_dumps(
            [{'id': 'long', 'name': 'Longsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'sword'}],
            [],
        )
        db.session.commit()

    response = client.patch(
        f"/api/players/{ids['player_id']}/inventory/equipment",
        json={'action': 'equip', 'item_id': 'long', 'session_id': other_ids['session_id']},
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['error_code'] == 'validation_error'
    assert 'player campaign' in payload['error']


def test_manual_equipment_endpoint_rolls_back_on_apply_error(client, app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps(
            [{'id': 'long', 'name': 'Longsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'sword'}],
            [],
        )
        db.session.commit()
        original_inventory = player.inventory

    from aidm_server.blueprints import players as players_module

    def fail_apply_state_changes(*_args, **_kwargs):
        raise RuntimeError('simulated equipment failure')

    monkeypatch.setattr(players_module, 'apply_state_changes', fail_apply_state_changes)

    response = client.patch(
        f"/api/players/{ids['player_id']}/inventory/equipment",
        json={'action': 'equip', 'item_id': 'long'},
    )

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'equipment_update_failed'
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        assert player.inventory == original_inventory


def test_update_player_rejects_invalid_payloads(client, app):
    ids = seed_world_campaign_player_session(app)

    missing_response = client.patch('/api/players/99999', json={'name': 'Nobody'})
    assert missing_response.status_code == 404
    assert missing_response.get_json()['error_code'] == 'player_not_found'

    empty_name_response = client.patch(f"/api/players/{ids['player_id']}", json={'name': '   '})
    assert empty_name_response.status_code == 400
    assert empty_name_response.get_json()['error_code'] == 'validation_error'

    bad_level_response = client.patch(f"/api/players/{ids['player_id']}", json={'level': 21})
    assert bad_level_response.status_code == 400
    assert bad_level_response.get_json()['error_code'] == 'validation_error'

    blank_class_response = client.patch(
        f"/api/players/{ids['player_id']}",
        json={'char_class': '   '},
    )
    assert blank_class_response.status_code == 400
    assert blank_class_response.get_json()['error_code'] == 'validation_error'


def test_create_player_requires_json_and_string_names(client, app):
    ids = seed_world_campaign_player_session(app)

    non_json_response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        data='not-json',
        content_type='text/plain',
    )
    assert non_json_response.status_code == 400
    assert non_json_response.get_json()['error_code'] == 'validation_error'

    numeric_name_response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={'name': 123, 'character_name': 'Seraphina'},
    )
    assert numeric_name_response.status_code == 400
    assert numeric_name_response.get_json()['error_code'] == 'validation_error'


def test_list_players_returns_404_for_missing_campaign(client):
    response = client.get('/api/players/campaigns/99999/players')

    assert response.status_code == 404
    assert response.get_json()['error_code'] == 'campaign_not_found'


def test_delete_player_removes_character_and_clears_history_references(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I test deletion.',
            dm_output='Deletion tested.',
        )
        event = TurnEvent(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            event_type='player_message',
            payload_json='{}',
        )
        action = PlayerAction(
            action_id=100,
            player_id=ids['player_id'],
            session_id=ids['session_id'],
            action_text='I test deletion.',
        )
        db.session.add_all([turn, event, action])
        db.session.commit()
        turn_id = turn.turn_id
        event_id = event.event_id

    response = client.delete(f"/api/players/{ids['player_id']}")

    assert response.status_code == 200
    assert response.get_json()['deleted'] is True
    assert client.get(f"/api/players/{ids['player_id']}").status_code == 404
    with app.app_context():
        assert db.session.get(Player, ids['player_id']) is None
        assert PlayerAction.query.filter_by(player_id=ids['player_id']).count() == 0
        assert db.session.get(DmTurn, turn_id).player_id is None
        assert db.session.get(TurnEvent, event_id).player_id is None
