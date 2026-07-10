from __future__ import annotations

from scripts.compare_tactics_compilers import compiler_cases, score_compiled_case


def test_score_compiled_case_accepts_expected_visible_attack():
    case = next(item for item in compiler_cases() if item['name'] == 'visible_focus_fire')

    checks = score_compiled_case(
        case,
        {
            'intentType': 'attack',
            'targetId': 'player_1',
            'abilityId': 'goblin_shortbow',
        },
    )

    assert all(check['passed'] for check in checks)


def test_score_compiled_case_rejects_unsafe_ids():
    case = next(item for item in compiler_cases() if item['name'] == 'unsafe_recommendation_fallback')

    checks = score_compiled_case(
        case,
        {
            'intentType': 'attack',
            'targetId': 'player_hidden',
            'abilityId': 'annihilation_bolt',
        },
    )
    by_id = {check['id']: check['passed'] for check in checks}

    assert by_id['rejects_forbidden_target'] is False
    assert by_id['rejects_forbidden_ability'] is False


def test_score_compiled_case_accepts_safe_non_attack_fallback():
    case = next(item for item in compiler_cases() if item['name'] == 'unsafe_recommendation_fallback')

    checks = score_compiled_case(case, {'intentType': 'retreat'})

    assert all(check['passed'] for check in checks)
