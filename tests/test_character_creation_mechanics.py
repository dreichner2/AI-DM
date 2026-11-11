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
    server_attack_roll_context,
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


def _class_player(app, ids, class_name: str, level: int, *, scores=None, sheet=None, inventory=None, **stats_extra):
    player = db.session.get(Player, ids['player_id'])
    player.class_ = class_name
    player.level = level
    player.stats = safe_json_dumps({**_point_buy_stats(), 'ability_scores': scores or dict(FIGHTER_SCORES), **stats_extra}, {})
    player.character_sheet = safe_json_dumps(sheet or {}, {})
    player.inventory = safe_json_dumps(inventory or [], [])
    db.session.commit()
    return player


def test_jack_of_all_trades_adds_half_proficiency_to_unskilled_bard_checks(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        bard = _class_player(app, ids, 'Bard', 2, skill_proficiencies=['Persuasion'])
        unskilled = character_roll_spec(bard, roll_type='athletics')
        skilled = character_roll_spec(bard, roll_type='persuasion')
        save = character_roll_spec(bard, roll_type='strength_saving_throw')
        raw = character_roll_spec(bard, roll_type='strength')
        bard.level = 1
        db.session.commit()
        level_one = character_roll_spec(bard, roll_type='athletics')

    assert unskilled['proficiency'] == {'bonus': 1, 'skills': ['jack_of_all_trades'], 'multiplier': 0}
    assert unskilled['modifier'] == 2 + 1
    assert skilled['proficiency']['bonus'] == 2
    assert save['proficiency']['bonus'] == 0
    assert raw['proficiency']['bonus'] == 1
    assert level_one['proficiency']['bonus'] == 0


def test_rogue_expertise_defaults_to_stealth_and_first_other_skill_and_keeps_explicit_choices(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        rogue = _class_player(app, ids, 'Rogue', 1, skill_proficiencies=['Perception', 'Acrobatics'])
        state = character_state_for_player(rogue)
        stealth = character_roll_spec(rogue, roll_type='stealth')
        acrobatics = character_roll_spec(rogue, roll_type='acrobatics')
        perception = character_roll_spec(rogue, roll_type='perception')
        chosen = _class_player(app, ids, 'Rogue', 1, skill_proficiencies=['Perception', 'Acrobatics'], skill_expertise=['Perception', 'Acrobatics'])
        chosen_state = character_state_for_player(chosen)
        fighter_state = character_state_for_player(_class_player(app, ids, 'Fighter', 1, skill_proficiencies=['Perception']))

    assert state['skill_expertise'] == ['stealth', 'acrobatics']
    assert (stealth['proficiency']['multiplier'], acrobatics['proficiency']['multiplier'], perception['proficiency']['multiplier']) == (2, 2, 1)
    assert chosen_state['skill_expertise'] == ['acrobatics', 'perception']
    assert 'skill_expertise' not in fighter_state


def test_fighting_style_is_stored_choice_or_equipment_default_and_class_features_show_pools(app):
    ids = seed_world_campaign_player_session(app)
    greatsword = [{'id': 'gs', 'name': 'Greatsword', 'type': 'weapon', 'equipped': True, 'slot': 'two_hands'}]
    with app.app_context():
        defaulted = character_state_for_player(_class_player(app, ids, 'Fighter', 1, inventory=greatsword))
        chosen = character_state_for_player(_class_player(app, ids, 'Fighter', 1, inventory=greatsword, sheet={'fightingStyle': 'archery'}))
        wizard = character_state_for_player(_class_player(app, ids, 'Wizard', 1))
        monk = character_state_for_player(_class_player(app, ids, 'Monk', 3, class_feature_state={'ki': {'current': 1, 'max': 3}}))

    assert defaulted['fighting_style'] == 'great_weapon'
    assert chosen['fighting_style'] == 'archery'
    assert 'fighting_style' not in wizard
    features = {feature['id']: feature for feature in monk['class_capabilities']}
    assert features['flurry_of_blows']['pool'] == 'ki'
    assert features['step_of_the_wind:dash']['name'] == 'Step of the Wind: Dash'
    assert monk['class_feature_state']['ki'] == {'current': 1, 'max': 3, 'refreshesOn': 'short_rest'}


def test_martial_arts_uses_dexterity_for_unarmed_strikes_and_monk_weapons(app):
    ids = seed_world_campaign_player_session(app)
    staff = [{'id': 'staff', 'name': 'Quarterstaff', 'type': 'weapon', 'equipped': True, 'slot': 'main_hand'}]
    nimble = {'strength': 10, 'dexterity': 15, 'constitution': 13, 'intelligence': 10, 'wisdom': 14, 'charisma': 8}
    with app.app_context():
        monk = _class_player(app, ids, 'Monk', 1, scores=nimble, inventory=staff)
        unarmed = server_attack_roll_context(monk, 'Kai attacks the goblin with Unarmed strike.')
        staff_attack = server_attack_roll_context(monk, 'Kai attacks the goblin with Quarterstaff.')
        fighter = _class_player(app, ids, 'Fighter', 1, scores=nimble, inventory=staff)
        fighter_staff = server_attack_roll_context(fighter, 'Talia attacks the goblin with Quarterstaff.')

    assert (unarmed['ability_key'], unarmed['proficient'], unarmed['weapon']['id']) == ('dexterity', True, 'unarmed')
    assert (staff_attack['ability_key'], staff_attack['weapon']['classification']) == ('dexterity', 'finesse')
    assert fighter_staff['ability_key'] == 'strength'


def _create(client, ids, **fields):
    return client.post(
        f"/api/players/campaigns/{ids['campaign_id']}/players",
        json={'character_name': 'New Hero', 'race': 'Human', 'stats': _point_buy_stats(), **fields},
    )


def test_creation_records_class_skill_choices_beside_background_skills(client, app):
    ids = seed_world_campaign_player_session(app)
    response = _create(
        client,
        ids,
        char_class='Fighter',
        background='soldier',
        skill_choices=['Acrobatics', 'perception'],
    )
    assert response.status_code == 201
    player_id = response.get_json()['player_id']
    detail = client.get(f'/api/players/{player_id}').get_json()
    omitted = _create(client, ids, character_name='Plain Hero', char_class='Fighter', background='soldier')

    assert set(detail['skill_proficiencies']) == {'acrobatics', 'athletics', 'intimidation', 'perception'}
    with app.app_context():
        player = db.session.get(Player, player_id)
        assert safe_json_loads(player.stats, {})['skill_proficiencies'] == ['acrobatics', 'perception']
        assert character_roll_spec(player, roll_type='perception')['proficiency']['multiplier'] == 1
        plain = db.session.get(Player, omitted.get_json()['player_id'])
        assert character_state_for_player(plain)['skill_proficiencies'] == ['athletics', 'intimidation']


def test_creation_rejects_class_skill_choices_off_the_list_from_the_background_or_miscounted(client, app):
    ids = seed_world_campaign_player_session(app)
    cases = [
        ({'char_class': 'Fighter', 'background': 'soldier', 'skill_choices': ['arcana', 'perception']},
         'Arcana is not a Fighter class skill.'),
        ({'char_class': 'Fighter', 'background': 'soldier', 'skill_choices': ['athletics', 'perception']},
         'Athletics already comes from your background; choose another class skill.'),
        ({'char_class': 'Fighter', 'background': 'soldier', 'skill_choices': ['perception']},
         'A Fighter chooses exactly 2 class skills.'),
        ({'char_class': 'Fighter', 'background': 'soldier', 'skill_choices': ['perception', 'Perception']},
         'Each class skill can be chosen only once.'),
        ({'char_class': 'Rogue', 'background': 'sage', 'skill_choices': ['stealth', 'insight', 'perception']},
         'A Rogue chooses exactly 4 class skills.'),
        ({'char_class': 'Bard', 'background': 'sage', 'skill_choices': ['stealth', 'insight']},
         'A Bard chooses exactly 3 class skills.'),
        ({'char_class': 'Ranger', 'background': 'sage', 'skill_choices': ['stealth', 'nature']},
         'A Ranger chooses exactly 3 class skills.'),
        ({'char_class': 'Wizard', 'background': 'sage', 'skill_choices': 'arcana'},
         'skill_choices must be a list of skill names.'),
    ]
    for fields, message in cases:
        response = _create(client, ids, character_name='Rejected Hero', **fields)
        assert response.status_code == 400, fields
        assert response.get_json()['error_code'] == 'validation_error'
        assert response.get_json()['error'] == message

    accepted = {
        'Rogue': ['stealth', 'insight', 'perception', 'acrobatics'],
        'Bard': ['stealth', 'insight', 'medicine'],
        'Ranger': ['stealth', 'nature', 'survival'],
        'Cleric': ['medicine', 'persuasion'],
    }
    for class_name, skills in accepted.items():
        response = _create(client, ids, char_class=class_name, background='sage', skill_choices=skills)
        assert response.status_code == 201, class_name
    with app.app_context():
        assert Player.query.filter_by(character_name='Rejected Hero').count() == 0


def test_rogue_expertise_is_two_proficient_skills_and_only_for_rogues(client, app):
    ids = seed_world_campaign_player_session(app)
    rogue = _create(
        client,
        ids,
        char_class='Rogue',
        race='Elf',
        race_selection={'raceId': 'elf'},
        background='criminal',
        skill_choices=['acrobatics', 'insight', 'investigation', 'sleight_of_hand'],
        expertise=['Sleight of Hand', 'perception'],
    )
    assert rogue.status_code == 201
    with app.app_context():
        player = db.session.get(Player, rogue.get_json()['player_id'])
        state = character_state_for_player(player)
        perception = character_roll_spec(player, roll_type='perception')
        stealth = character_roll_spec(player, roll_type='stealth')

    # Perception comes from the elf's Keen Senses, Stealth from the Criminal background.
    assert state['skill_expertise'] == ['perception', 'sleight_of_hand']
    assert (perception['proficiency']['multiplier'], stealth['proficiency']['multiplier']) == (2, 1)

    rogue_fields = {'char_class': 'Rogue', 'background': 'criminal',
                    'skill_choices': ['acrobatics', 'insight', 'investigation', 'sleight_of_hand']}
    cases = [
        ({**rogue_fields, 'expertise': ['stealth', 'arcana']},
         'Expertise needs a skill you are proficient in, and Arcana is not one of yours.'),
        ({**rogue_fields, 'expertise': ['stealth']}, 'A Rogue chooses exactly two different expertise skills.'),
        ({**rogue_fields, 'expertise': ['stealth', 'stealth']}, 'A Rogue chooses exactly two different expertise skills.'),
        ({'char_class': 'Bard', 'background': 'criminal', 'expertise': ['stealth', 'deception']},
         'Only a Rogue chooses expertise.'),
    ]
    for fields, message in cases:
        response = _create(client, ids, **fields)
        assert response.status_code == 400, fields
        assert response.get_json()['error'] == message


def test_fighting_style_choice_is_validated_per_class_and_waits_for_level_two_on_paladins(client, app):
    ids = seed_world_campaign_player_session(app)
    fighter = _create(client, ids, char_class='Fighter', fighting_style='archery')
    paladin = _create(client, ids, char_class='Paladin - Devotion', fighting_style='great_weapon')
    assert (fighter.status_code, paladin.status_code) == (201, 201)
    with app.app_context():
        fighter_state = character_state_for_player(db.session.get(Player, fighter.get_json()['player_id']))
        paladin_player = db.session.get(Player, paladin.get_json()['player_id'])
        paladin_level_one = character_state_for_player(paladin_player)
        stored = safe_json_loads(paladin_player.character_sheet, {})['fightingStyle']
        paladin_player.level = 2
        db.session.commit()
        paladin_level_two = character_state_for_player(paladin_player)

    assert fighter_state['fighting_style'] == 'archery'
    assert stored == 'great_weapon'
    assert 'fighting_style' not in paladin_level_one
    assert paladin_level_two['fighting_style'] == 'great_weapon'

    cases = [
        ({'char_class': 'Ranger', 'fighting_style': 'great_weapon'},
         "A Ranger's fighting_style must be one of: archery, defense, dueling."),
        ({'char_class': 'Fighter', 'fighting_style': 'two_weapon'},
         "A Fighter's fighting_style must be one of: defense, dueling, great_weapon, archery."),
        ({'char_class': 'Wizard', 'fighting_style': 'defense'},
         'Only a Fighter, Paladin or Ranger chooses a fighting style.'),
    ]
    for fields, message in cases:
        response = _create(client, ids, **fields)
        assert response.status_code == 400, fields
        assert response.get_json()['error'] == message


def test_narrated_classes_are_created_as_today_but_take_no_class_choices(client, app):
    ids = seed_world_campaign_player_session(app)
    for class_name in ('Artificer', 'Dream Knight - Mirror Oath'):
        response = _create(client, ids, char_class=class_name, background='sage')
        assert response.status_code == 201, class_name

    rejected = _create(client, ids, char_class='Dream Knight', background='sage', skill_choices=['arcana', 'history'])
    assert rejected.status_code == 400
    assert rejected.get_json()['error'] == 'Class skill choices apply only to the twelve core classes.'


def test_the_recommended_ancestries_are_stored_as_chosen(client, app):
    ids = seed_world_campaign_player_session(app)
    recommended = ['human', 'elf', 'dwarf', 'halfling', 'gnome', 'dragonborn', 'tiefling']

    stored = {}
    for race_id in recommended:
        response = _create(
            client,
            ids,
            char_class='Fighter',
            race=race_id.title(),
            race_selection={'raceId': race_id, 'source': 'curated'},
            background='soldier',
        )
        assert response.status_code == 201, race_id
        with app.app_context():
            player = db.session.get(Player, response.get_json()['player_id'])
            stored[race_id] = (safe_json_loads(player.race_selection, {})['raceId'], player.race)

    assert stored == {race_id: (race_id, race_id.title()) for race_id in recommended}
