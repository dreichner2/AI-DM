from __future__ import annotations

from aidm_server.character_backgrounds import (
    BackgroundValidationError,
    background_catalog,
    normalize_character_background,
)
from aidm_server.character_progression import (
    class_max_hp,
    hit_die_for_class,
    proficiency_bonus_for_level,
)
from aidm_server.character_state import (
    character_roll_spec,
    character_state_for_player,
    sync_character_derived_stats,
    validate_point_buy_payload,
)
from aidm_server.database import db
from aidm_server.game_state.leveling import baseline_max_hp_for_level
from aidm_server.models import Player, safe_json_dumps, safe_json_loads
from tests.helpers import seed_world_campaign_player_session


FIGHTER_SCORES = {
    'strength': 15,
    'dexterity': 12,
    'constitution': 14,
    'intelligence': 10,
    'wisdom': 10,
    'charisma': 10,
}


def _point_buy_stats(*, current_hp: int | None = None, **extra):
    stats = {
        'ability_scores': dict(FIGHTER_SCORES),
        'point_buy': {'budget': 27},
        **extra,
    }
    if current_hp is not None:
        stats['current_hp'] = current_hp
    return stats


def test_background_catalog_is_small_mechanical_and_authoritative():
    catalog = background_catalog()

    assert {entry['id'] for entry in catalog} == {
        'acolyte',
        'criminal',
        'folk_hero',
        'guild_artisan',
        'sage',
        'soldier',
    }
    assert all(len(entry['skillProficiencies']) == 2 for entry in catalog)
    assert all(entry['source'] == 'catalog' for entry in catalog)

    tampered = normalize_character_background(
        {
            'id': 'sage',
            'name': 'Sage',
            'skillProficiencies': ['stealth'],
            'toolProficiencies': ['thieves_tools'],
            'languages': ['Every Language'],
        }
    )
    assert tampered['skillProficiencies'] == ['arcana', 'history']
    assert tampered['toolProficiencies'] == ['calligraphers_supplies']
    assert tampered['languages'] == ['Draconic', 'Elvish']

    try:
        normalize_character_background('Omnipotent Wanderer')
    except BackgroundValidationError as exc:
        assert 'background must be one of' in exc.public_message
    else:  # pragma: no cover - protects the strict validation contract
        raise AssertionError('Unknown new backgrounds must fail closed.')


def test_class_hit_dice_drive_level_one_and_later_hp_and_proficiency():
    assert hit_die_for_class('Barbarian - Berserker') == 12
    assert hit_die_for_class('Fighter - Champion') == 10
    assert hit_die_for_class('Cleric - Life') == 8
    assert hit_die_for_class('Wizard - Evoker') == 6
    assert hit_die_for_class('Unknown Homebrew') == 8

    assert class_max_hp('Fighter', constitution_score=14, level=1) == 12
    assert class_max_hp('Wizard', constitution_score=14, level=1) == 8
    assert class_max_hp('Fighter', constitution_score=14, level=5) == 44
    assert class_max_hp('Wizard', constitution_score=14, level=5) == 32
    assert proficiency_bonus_for_level(4) == 2
    assert proficiency_bonus_for_level(5) == 3
    assert proficiency_bonus_for_level(17) == 6

    fighter, fighter_error = validate_point_buy_payload(
        {**_point_buy_stats(), 'max_hp': 999},
        level=1,
        class_name='Fighter',
    )
    wizard, wizard_error = validate_point_buy_payload(
        {**_point_buy_stats(), 'max_hp': 999},
        level=1,
        class_name='Wizard',
    )
    assert fighter_error is None
    assert wizard_error is None
    assert fighter['max_hp'] == 12
    assert fighter['hit_die'] == 10
    assert wizard['max_hp'] == 8
    assert wizard['hit_die'] == 6

    assert baseline_max_hp_for_level(
        {'ability_scores': {'constitution': 14}, 'hit_die': 10},
        5,
    ) == 44


def test_create_background_applies_once_to_rolls_and_projects_to_ai_and_player(client, app):
    ids = seed_world_campaign_player_session(app)
    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'character_name': 'Talia Forge',
            'race': 'Human',
            'char_class': 'Fighter - Champion',
            'level': 1,
            'background': 'guild_artisan',
            'stats': _point_buy_stats(
                current_hp=7,
                skill_proficiencies=['Persuasion'],
            ),
        },
    )
    assert response.status_code == 201
    player_id = response.get_json()['player_id']

    detail_response = client.get(f'/api/players/{player_id}')
    assert detail_response.status_code == 200
    detail = detail_response.get_json()
    assert detail['background']['id'] == 'guild_artisan'
    assert detail['character_sheet']['background']['name'] == 'Guild Artisan'
    assert detail['stats']['hit_die'] == 10
    assert detail['stats']['max_hp'] == 12
    assert detail['stats']['current_hp'] == 7
    assert detail['stats']['proficiency_bonus'] == 2
    assert detail['skill_proficiencies'].count('persuasion') == 1
    assert {'insight', 'persuasion'} <= set(detail['skill_proficiencies'])
    assert detail['tool_proficiencies'] == ['artisan_tools']
    assert {'Common', 'Dwarvish'} <= set(detail['languages'])
    assert detail['derived']['background']['id'] == 'guild_artisan'

    with app.app_context():
        player = db.session.get(Player, player_id)
        state = character_state_for_player(player)
        roll = character_roll_spec(player, roll_type='persuasion')

        assert state['background']['name'] == 'Guild Artisan'
        assert state['skill_proficiencies'].count('persuasion') == 1
        assert roll['proficiency']['multiplier'] == 1
        assert roll['proficiency']['bonus'] == 2
        assert roll['modifier_breakdown']['wound_penalty'] == 1
        assert roll['modifier'] == 1

        stats = safe_json_loads(player.stats, {})
        stats['skill_expertise'] = ['Persuasion']
        player.stats = safe_json_dumps(stats, {})
        db.session.commit()
        expertise_roll = character_roll_spec(player, roll_type='persuasion')

    assert expertise_roll['proficiency']['multiplier'] == 2
    assert expertise_roll['proficiency']['bonus'] == 4
    assert expertise_roll['modifier'] == 3


def test_background_tool_proficiency_is_authoritative_for_matching_roll(client, app):
    ids = seed_world_campaign_player_session(app)
    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'character_name': 'Mara Lockstep',
            'race': 'Human',
            'char_class': 'Rogue',
            'background': 'criminal',
            'stats': {
                **_point_buy_stats(),
                'ability_scores': {**FIGHTER_SCORES, 'dexterity': 14, 'strength': 13},
            },
        },
    )
    assert response.status_code == 201

    with app.app_context():
        player = db.session.get(Player, response.get_json()['player_id'])
        state = character_state_for_player(player)
        roll = character_roll_spec(player, roll_type='thieves_tools')

    assert 'thieves_tools' in state['tool_proficiencies']
    assert roll['ability']['key'] == 'dexterity'
    assert roll['proficiency']['skills'] == ['tool:thieves_tools']
    assert roll['proficiency']['multiplier'] == 1
    assert roll['modifier'] == 4


def test_level_update_recomputes_class_hp_preserves_damage_and_persists_reload(client, app):
    ids = seed_world_campaign_player_session(app)
    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'character_name': 'Talia Forge',
            'race': 'Human',
            'char_class': 'Fighter',
            'level': 1,
            'background': 'soldier',
            'stats': _point_buy_stats(current_hp=7, xp=6500),
        },
    )
    player_id = response.get_json()['player_id']

    updated = client.patch(f'/api/players/{player_id}', json={'level': 5})
    assert updated.status_code == 200
    updated_stats = updated.get_json()['stats']
    assert updated_stats['hit_die'] == 10
    assert updated_stats['max_hp'] == 44
    assert updated_stats['current_hp'] == 39
    assert updated_stats['proficiency_bonus'] == 3

    reloaded = client.get(f'/api/players/{player_id}')
    assert reloaded.status_code == 200
    assert reloaded.get_json()['stats']['max_hp'] == 44
    assert reloaded.get_json()['stats']['current_hp'] == 39
    assert reloaded.get_json()['background']['id'] == 'soldier'


def test_dead_character_progression_does_not_revive_and_legacy_background_stays_flavor_only(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.class_ = 'Fighter'
        player.level = 1
        player.stats = safe_json_dumps(_point_buy_stats(current_hp=0, max_hp=12), {})
        player.character_sheet = safe_json_dumps({'background': 'Road Warden'}, {})
        db.session.commit()

        stats = safe_json_loads(player.stats, {})
        updated, changed = sync_character_derived_stats(
            stats,
            class_name='Fighter',
            level=5,
            previous_class_name='Fighter',
            previous_level=1,
        )
        state = character_state_for_player(player)

    assert changed is True
    assert updated['max_hp'] == 44
    assert updated['current_hp'] == 0
    assert state['background'] == {
        'schemaVersion': 1,
        'id': 'road_warden',
        'name': 'Road Warden',
        'source': 'legacy',
        'skillProficiencies': [],
        'toolProficiencies': [],
        'languages': [],
    }


def test_unknown_new_background_is_rejected_without_creating_player(client, app):
    ids = seed_world_campaign_player_session(app)
    response = client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={
            'character_name': 'Impossible Hero',
            'char_class': 'Fighter',
            'stats': _point_buy_stats(),
            'background': {'id': 'all_skills_forever'},
        },
    )

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'
    with app.app_context():
        assert Player.query.filter_by(character_name='Impossible Hero').count() == 0
