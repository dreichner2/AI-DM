from __future__ import annotations

from aidm_server.class_capabilities import (
    capabilities_for_class,
    normalize_class_feature_state,
    resolve_capability_use,
    restore_class_capabilities,
    spend_capability,
)
from aidm_server.game_state.extraction.pre_dm_action_extractor import extract_pre_dm_actions


def _actor(class_name: str, *, level: int = 2, current_hp: int = 5, max_hp: int = 20) -> dict:
    return {
        'id': 'player_1',
        'name': 'Ilyra',
        'class': class_name,
        'level': level,
        'team': 'player',
        'health': {'currentHp': current_hp, 'maxHp': max_hp, 'tempHp': 0, 'conditions': []},
    }


def test_fighter_catalog_unlocks_complete_features_by_level() -> None:
    assert [item['id'] for item in capabilities_for_class('Fighter', 1)] == ['second_wind']
    assert [item['id'] for item in capabilities_for_class('Fighter - Champion', 2)] == [
        'second_wind',
        'action_surge',
    ]


def test_second_wind_requires_bonus_action_and_spends_once() -> None:
    actor = _actor('Fighter', level=3)
    actor['classFeatureState'] = normalize_class_feature_state(None, class_name='Fighter', level=3)
    result = resolve_capability_use(
        actor=actor,
        capability_id='second_wind',
        target=actor,
        turn_economy={'bonusActionRemaining': 1},
        in_combat=True,
        roller=lambda _sides: 6,
    )
    assert result['ok'] is True
    assert result['amount'] == 9
    assert result['actionEconomy'] == 'bonus_action'

    spent = spend_capability(
        actor['classFeatureState'],
        class_name='Fighter',
        level=3,
        capability_id='second_wind',
        amount=result['resourceCost'],
        turn_id=4,
    )
    assert spent['second_wind']['current'] == 0
    actor['classFeatureState'] = spent
    exhausted = resolve_capability_use(
        actor=actor,
        capability_id='second_wind',
        target=actor,
        turn_economy={'bonusActionRemaining': 1},
        in_combat=True,
    )
    assert exhausted == {'ok': False, 'reason': 'Second Wind has no uses remaining.'}


def test_action_surge_restores_only_a_spent_combat_action() -> None:
    actor = _actor('Fighter', level=2)
    actor['classFeatureState'] = normalize_class_feature_state(None, class_name='Fighter', level=2)
    ready = resolve_capability_use(
        actor=actor,
        capability_id='action_surge',
        target=actor,
        turn_economy={'actionRemaining': 0},
        in_combat=True,
    )
    assert ready['ok'] is True
    assert ready['effectType'] == 'restore_action'

    redundant = resolve_capability_use(
        actor=actor,
        capability_id='action_surge',
        target=actor,
        turn_economy={'actionRemaining': 1},
        in_combat=True,
    )
    assert redundant['ok'] is False
    assert redundant['reason'] == 'The action for this turn is still available.'


def test_lay_on_hands_pool_targets_exact_ally_and_clamps_to_missing_hp() -> None:
    paladin = _actor('Paladin', level=3, current_hp=18, max_hp=18)
    paladin['classFeatureState'] = normalize_class_feature_state(None, class_name='Paladin', level=3)
    ally = _actor('Wizard', level=3, current_hp=2, max_hp=8)
    ally['id'] = 'player_2'
    result = resolve_capability_use(
        actor=paladin,
        capability_id='lay_on_hands',
        target=ally,
        requested_amount=10,
        turn_economy={'actionRemaining': 1},
        in_combat=True,
    )
    assert result['ok'] is True
    assert result['targetId'] == 'player_2'
    assert result['amount'] == 6
    assert result['resourceCost'] == 6

    spent = spend_capability(
        paladin['classFeatureState'],
        class_name='Paladin',
        level=3,
        capability_id='lay_on_hands',
        amount=6,
        turn_id=7,
    )
    assert spent['lay_on_hands']['current'] == 9


def test_short_and_long_rest_refresh_only_matching_capabilities() -> None:
    fighter = normalize_class_feature_state(None, class_name='Fighter', level=2)
    fighter['second_wind']['current'] = 0
    fighter['action_surge']['current'] = 0
    short = restore_class_capabilities(
        fighter,
        class_name='Fighter',
        level=2,
        rest_type='short_rest',
    )
    assert short['second_wind']['current'] == 1
    assert short['action_surge']['current'] == 1

    paladin = normalize_class_feature_state(None, class_name='Paladin', level=2)
    paladin['lay_on_hands']['current'] = 0
    short_paladin = restore_class_capabilities(
        paladin,
        class_name='Paladin',
        level=2,
        rest_type='short_rest',
    )
    assert short_paladin['lay_on_hands']['current'] == 0
    long_paladin = restore_class_capabilities(
        short_paladin,
        class_name='Paladin',
        level=2,
        rest_type='long_rest',
    )
    assert long_paladin['lay_on_hands']['current'] == 10


def test_capability_intent_without_optional_amount_survives_pre_dm_normalization() -> None:
    extraction = extract_pre_dm_actions(
        current_state={},
        player_message='I use Second Wind.',
        recent_timeline=[],
        actor_id='player_1',
        action_intent={
            'kind': 'capability',
            'capability': {'id': 'second_wind', 'target_id': 'player_1'},
        },
    )

    assert extraction['declaredActions'] == [
        {
            'id': 'act_001',
            'type': 'class_feature.use',
            'actorId': 'player_1',
            'confidence': 1.0,
            'sourceText': 'I use Second Wind.',
            'requiresDMResolution': False,
            'capabilityId': 'second_wind',
            'targetId': 'player_1',
        }
    ]
