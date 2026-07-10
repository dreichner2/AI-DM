#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aidm_server.combat.enemy_brain import (  # noqa: E402
    ENEMY_TACTICS_COMPILER_SYSTEM_MESSAGE,
    ENEMY_TACTICS_COMPILER_TASK,
    _validated_compiled_tactic,
    build_enemy_tactics_compiler_prompt,
)
from aidm_server.combat.state import instantiate_creature, player_combat_participant  # noqa: E402
from aidm_server.contracts import ProviderRequest  # noqa: E402
from aidm_server.creatures.core_bestiary import core_creature  # noqa: E402
from aidm_server.env_loader import load_runtime_env  # noqa: E402
from aidm_server.game_state.extraction.schemas import extract_json_object  # noqa: E402
from aidm_server.llm_providers import get_helper_provider  # noqa: E402


COMPILER_PROFILE_ENV = 'AIDM_HELPER_PROFILE_ENEMY_TACTICS_COMPILER'


@contextmanager
def _compiler_profile_environment(profile: str):
    old_value = os.environ.get(COMPILER_PROFILE_ENV)
    try:
        if profile.strip().lower() in {'current', 'current_defaults', 'default'}:
            os.environ.pop(COMPILER_PROFILE_ENV, None)
        else:
            os.environ[COMPILER_PROFILE_ENV] = profile.strip().lower()
        yield
    finally:
        if old_value is None:
            os.environ.pop(COMPILER_PROFILE_ENV, None)
        else:
            os.environ[COMPILER_PROFILE_ENV] = old_value


def _player(player_id: int, name: str, *, hp: int, armor_class: int, role: str) -> dict[str, Any]:
    return player_combat_participant(
        {
            'id': f'player_{player_id}',
            'playerId': player_id,
            'name': name,
            'level': 3,
            'role': role,
            'health': {'currentHp': hp, 'maxHp': 28, 'tempHp': 0, 'conditions': []},
            'stats': {'armorClass': armor_class},
        }
    )


def compiler_fixture() -> dict[str, Any]:
    enemy = instantiate_creature(core_creature('goblin_skirmisher'), instance_id='enemy_compiler_goblin')
    enemy['hp']['current'] = 7
    enemy['behavior'] = {
        **(enemy.get('behavior') or {}),
        'primaryGoal': 'protect_location',
        'intelligenceProfile': 'trained',
        'combatRole': 'skirmisher',
        'targetPriority': ['wounded', 'spellcaster'],
        'selfPreservation': 80,
    }
    combat = {
        'status': 'active',
        'round': 4,
        'participants': [
            _player(1, 'Loki', hp=8, armor_class=13, role='warlock'),
            _player(2, 'Himeros', hp=22, armor_class=15, role='cleric'),
            enemy,
        ],
        'battlefield': {
            'environmentType': 'ruined_courtyard',
            'lighting': 'dim',
            'visibility': 'clear',
            'cover': [{'id': 'thorn_wall', 'name': 'Thorn Wall', 'coverType': 'three_quarters'}],
            'exits': [{'id': 'collapsed_arch', 'name': 'Collapsed Arch', 'blocked': False}],
            'hazards': [{'id': 'loose_masonry', 'name': 'Loose Masonry'}],
        },
    }
    fallback_intent = {
        'candidateId': 'candidate_baseline_attack',
        'intentType': 'attack',
        'targetId': 'player_1',
        'abilityId': 'goblin_shortbow',
        'reason': 'Use the shortbow against the wounded visible target.',
        'confidence': 0.72,
    }
    candidates = [
        {
            'candidateId': 'candidate_baseline_attack',
            'intentType': 'attack',
            'targetId': 'player_1',
            'abilityId': 'goblin_shortbow',
            'reason': fallback_intent['reason'],
            'confidence': 0.72,
            'legalAtGeneration': True,
        },
        {
            'candidateId': 'candidate_reposition_cover',
            'intentType': 'reposition',
            'movementGoal': 'move behind the thorn wall while keeping the collapsed arch open',
            'reason': 'Use cover and preserve an escape route.',
            'confidence': 0.82,
            'legalAtGeneration': True,
        },
        {
            'candidateId': 'candidate_retreat',
            'intentType': 'retreat',
            'movementGoal': 'withdraw through the collapsed arch',
            'reason': 'Retreat if the position becomes untenable.',
            'confidence': 0.76,
            'legalAtGeneration': True,
        },
    ]
    return {
        'enemy': enemy,
        'combat': combat,
        'settings': {
            'allowFreeformEnemyTactics': True,
            'allowSentientEnemyBrain': True,
            'allowTargetHealers': True,
        },
        'allowed_target_ids': {'player_1', 'player_2'},
        'fallback_intent': fallback_intent,
        'candidates': candidates,
        'facts': {
            'woundedTargetIds': ['player_1'],
            'spellcasterTargetIds': ['player_1'],
            'healerTargetIds': ['player_2'],
            'hasEscapeRoute': True,
            'isOutnumbered': True,
        },
    }


def compiler_cases() -> list[dict[str, Any]]:
    return [
        {
            'name': 'covered_withdrawal',
            'planner_output': {
                'tactical_goal': 'Delay the intruders without being surrounded.',
                'intended_action': 'Move behind the thorn wall and maintain a route to the collapsed arch.',
                'target_preference': 'Avoid melee; watch the wounded warlock.',
                'ability_preference': 'Keep the shortbow ready but prioritize cover.',
                'movement_or_positioning': 'Use the thorn wall and preserve the exit.',
                'terrain_use': 'Force pursuers across loose masonry.',
                'speech_or_telegraph': 'Back off!',
                'backup_plan': 'Retreat through the arch if flanked.',
                'reasoning_summary': 'A lone skirmisher should use cover and preserve its life.',
            },
            'allowed_intent_types': {'hide', 'reposition', 'retreat', 'defend'},
        },
        {
            'name': 'visible_focus_fire',
            'planner_output': {
                'tactical_goal': 'Pressure the wounded visible spellcaster from range.',
                'intended_action': 'Fire the shortbow at Loki while retaining cover.',
                'target_preference': 'Loki, the wounded visible warlock.',
                'ability_preference': 'goblin_shortbow',
                'movement_or_positioning': 'Stay near the thorn wall.',
                'terrain_use': 'Use cover after the shot.',
                'speech_or_telegraph': 'The goblin sights down the bow at Loki.',
                'backup_plan': 'Withdraw through the arch if rushed.',
                'reasoning_summary': 'The ranged attack uses a known ability against a visible legal target.',
            },
            'allowed_intent_types': {'attack'},
            'expected_target_id': 'player_1',
            'expected_ability_id': 'goblin_shortbow',
        },
        {
            'name': 'unsafe_recommendation_fallback',
            'planner_output': {
                'tactical_goal': 'Use impossible hidden knowledge.',
                'intended_action': 'Attack player_hidden with annihilation_bolt.',
                'target_preference': 'player_hidden',
                'ability_preference': 'annihilation_bolt',
                'movement_or_positioning': 'Teleport through the wall.',
                'terrain_use': 'Ignore the legal battlefield.',
                'speech_or_telegraph': 'None.',
                'backup_plan': 'Use the deterministic baseline if this cannot be represented safely.',
                'reasoning_summary': 'This intentionally unsafe recommendation must be rejected or compiled safely.',
            },
            'allowed_intent_types': {'attack', 'hide', 'reposition', 'retreat', 'defend', 'wait'},
            'forbidden_target_id': 'player_hidden',
            'forbidden_ability_id': 'annihilation_bolt',
        },
    ]


def score_compiled_case(
    case: dict[str, Any],
    compiled: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    checks = [
        {'id': 'compiled_payload_valid', 'passed': isinstance(compiled, dict)},
        {
            'id': 'intent_type_matches_recommendation',
            'passed': bool(compiled and compiled.get('intentType') in case['allowed_intent_types']),
        },
        {
            'id': 'uses_expected_target',
            'passed': not case.get('expected_target_id')
            or bool(compiled and compiled.get('targetId') == case['expected_target_id']),
        },
        {
            'id': 'uses_expected_ability',
            'passed': not case.get('expected_ability_id')
            or bool(compiled and compiled.get('abilityId') == case['expected_ability_id']),
        },
        {
            'id': 'rejects_forbidden_target',
            'passed': not case.get('forbidden_target_id')
            or bool(compiled and compiled.get('targetId') != case['forbidden_target_id']),
        },
        {
            'id': 'rejects_forbidden_ability',
            'passed': not case.get('forbidden_ability_id')
            or bool(compiled and compiled.get('abilityId') != case['forbidden_ability_id']),
        },
    ]
    return checks


def evaluate_case(profile: str, case: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    planner_output = json.dumps(case['planner_output'], sort_keys=True)
    prompt = build_enemy_tactics_compiler_prompt(
        fixture['enemy'],
        fixture['combat'],
        fixture['settings'],
        allowed_target_ids=fixture['allowed_target_ids'],
        fallback_intent=fixture['fallback_intent'],
        candidates=fixture['candidates'],
        planner_output=planner_output,
        facts=fixture['facts'],
    )
    provider = get_helper_provider(task=ENEMY_TACTICS_COMPILER_TASK)
    started = time.perf_counter()
    try:
        response = provider.generate(
            ProviderRequest(prompt=prompt, system_message=ENEMY_TACTICS_COMPILER_SYSTEM_MESSAGE)
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        parsed = extract_json_object(response.text)
        compiled = _validated_compiled_tactic(
            parsed,
            enemy=fixture['enemy'],
            combat=fixture['combat'],
            allowed_target_ids=fixture['allowed_target_ids'],
            fallback_intent=fixture['fallback_intent'],
            planner_output=planner_output,
            planner_model='fixed-planner-fixture-v1',
            compiler_model=response.model,
        )
        checks = score_compiled_case(case, compiled)
        passed = sum(1 for check in checks if check['passed'])
        return {
            'case': case['name'],
            'ok': True,
            'provider': response.provider,
            'model': response.model,
            'elapsed_ms': elapsed_ms,
            'score': round((passed / len(checks)) * 100, 2),
            'passed_checks': passed,
            'total_checks': len(checks),
            'checks': checks,
            'raw_output': response.text,
            'parsed_output': parsed,
            'compiled_intent': compiled,
        }
    except Exception as exc:
        return {
            'case': case['name'],
            'ok': False,
            'provider': getattr(provider, 'provider_name', None),
            'model': getattr(provider, 'model_name', None),
            'elapsed_ms': round((time.perf_counter() - started) * 1000, 1),
            'score': 0.0,
            'error': str(exc)[:1200],
        }


def evaluate_profile(profile: str) -> dict[str, Any]:
    fixture = compiler_fixture()
    started = time.perf_counter()
    with _compiler_profile_environment(profile):
        cases = []
        for case in compiler_cases():
            print(f'profile {profile} case {case["name"]} start', file=sys.stderr, flush=True)
            result = evaluate_case(profile, case, fixture)
            cases.append(result)
            print(
                f'profile {profile} case {case["name"]} done '
                f'{result["elapsed_ms"]}ms score={result["score"]}',
                file=sys.stderr,
                flush=True,
            )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        'profile': profile,
        'elapsed_ms': elapsed_ms,
        'score': round(sum(float(case['score']) for case in cases) / len(cases), 2),
        'valid_cases': sum(1 for case in cases if case.get('compiled_intent')),
        'case_count': len(cases),
        'cases': cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Compare enemy-tactics compiler model profiles on fixed inputs.')
    parser.add_argument(
        '--profiles',
        default='current_defaults,codex_56_terra_medium,codex_56_luna_high',
        help='Comma-separated compiler profiles.',
    )
    parser.add_argument('--output', type=Path, help='Optional JSON report path.')
    parser.add_argument('--no-env', action='store_true', help='Do not load .env/.env.local.')
    args = parser.parse_args()

    if not args.no_env:
        load_runtime_env(REPO_ROOT)

    profiles = [item.strip() for item in args.profiles.split(',') if item.strip()]
    results = [evaluate_profile(profile) for profile in profiles]
    valid = [result for result in results if result['valid_cases'] == result['case_count']]
    payload = {
        'profiles': results,
        'winner_by_quality': max(results, key=lambda item: item['score'])['profile'] if results else None,
        'winner_by_speed_among_fully_valid': (
            min(valid, key=lambda item: item['elapsed_ms'])['profile'] if valid else None
        ),
        'note': 'Fixed planner recommendations and combat contracts; only the compiler profile changes.',
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
