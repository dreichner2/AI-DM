#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aidm_server.blueprints.races import (  # noqa: E402
    CUSTOM_RACE_HELPER_TASK,
    CUSTOM_RACE_SYSTEM_MESSAGE,
    _build_custom_race_prompt,
    _race_payload_from_helper,
)
import aidm_server.blueprints.races as races_module  # noqa: E402
from aidm_server.combat.evaluation import run_combat_helper_evaluation  # noqa: E402
from aidm_server.combat.state import instantiate_creature, player_combat_participant  # noqa: E402
import aidm_server.combat.boss_tactics as boss_tactics_module  # noqa: E402
import aidm_server.combat.enemy_brain as enemy_brain_module  # noqa: E402
from aidm_server.contracts import ProviderRequest  # noqa: E402
from aidm_server.creatures.balance import analyze_creature_balance, auto_scale_creature  # noqa: E402
from aidm_server.creatures.core_bestiary import core_creature  # noqa: E402
from aidm_server.creatures.generator import CREATURE_HELPER_TASK, CREATURE_SYSTEM_MESSAGE, build_creature_generation_prompt  # noqa: E402
import aidm_server.creatures.generator as creature_generator_module  # noqa: E402
from aidm_server.creatures.schemas import normalize_creature_definition  # noqa: E402
from aidm_server.env_loader import load_runtime_env  # noqa: E402
from aidm_server.game_state.extraction.schemas import extract_json_object  # noqa: E402
import aidm_server.llm_providers as llm_provider_module  # noqa: E402


PROFILE_TASK_ENV = {
    'custom_race': 'AIDM_HELPER_PROFILE_CUSTOM_RACE',
    'creature_generation': 'AIDM_HELPER_PROFILE_CREATURE_GENERATION',
    'boss_tactics_planner': 'AIDM_HELPER_PROFILE_BOSS_TACTICS_PLANNER',
    'boss_tactics': 'AIDM_HELPER_PROFILE_BOSS_TACTICS',
    'sentient_enemy_brain': 'AIDM_HELPER_PROFILE_SENTIENT_ENEMY_BRAIN',
}

OLD_DEFAULT_TASK_PROFILES = {
    'custom_race': 'deepseek_pro',
    'creature_generation': 'fast',
    'boss_tactics_planner': 'deepseek_pro',
    'boss_tactics': 'deepseek_pro',
    'sentient_enemy_brain': 'deepseek_pro',
}


def _progress(enabled: bool, message: str):
    if enabled:
        print(message, file=sys.stderr, flush=True)


def _maybe_truncate(value: str, max_chars: int) -> tuple[str, bool]:
    text = str(value or '')
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars], True
    return text, False


class RecordingProvider:
    def __init__(self, provider: Any, *, task: str | None, recorder: HelperOutputRecorder):
        self._provider = provider
        self._task = task
        self._recorder = recorder

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def generate(self, request: ProviderRequest):
        started = time.perf_counter()
        try:
            response = self._provider.generate(request)
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            self._recorder.record_error(task=self._task, request=request, error=exc, elapsed_ms=elapsed_ms)
            raise
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        self._recorder.record_success(task=self._task, request=request, response=response, elapsed_ms=elapsed_ms)
        return response


class HelperOutputRecorder:
    def __init__(self, *, max_chars: int = 0):
        self.max_chars = max(0, int(max_chars))
        self.profile = ''
        self.calls: list[dict[str, Any]] = []

    def factory(self, task: str | None = None) -> RecordingProvider:
        return RecordingProvider(llm_provider_module.get_helper_provider(task=task), task=task, recorder=self)

    def _base_record(self, *, task: str | None, request: ProviderRequest, elapsed_ms: float) -> dict[str, Any]:
        prompt_preview, prompt_truncated = _maybe_truncate(request.prompt, 2000)
        return {
            'index': len(self.calls) + 1,
            'profile': self.profile,
            'task': task,
            'elapsed_ms': elapsed_ms,
            'system_message': request.system_message,
            'prompt_preview': prompt_preview,
            'prompt_preview_truncated': prompt_truncated,
        }

    def record_success(self, *, task: str | None, request: ProviderRequest, response: Any, elapsed_ms: float):
        raw_text, raw_truncated = _maybe_truncate(getattr(response, 'text', ''), self.max_chars)
        parsed = None
        parse_error = None
        try:
            parsed = extract_json_object(getattr(response, 'text', ''))
        except Exception as exc:
            parse_error = str(exc)[:300]
        self.calls.append(
            {
                **self._base_record(task=task, request=request, elapsed_ms=elapsed_ms),
                'ok': True,
                'provider': getattr(response, 'provider', None),
                'model': getattr(response, 'model', None),
                'raw_output': raw_text,
                'raw_output_truncated': raw_truncated,
                'parsed_output': parsed,
                'parse_error': parse_error,
            }
        )

    def record_error(self, *, task: str | None, request: ProviderRequest, error: Exception, elapsed_ms: float):
        self.calls.append(
            {
                **self._base_record(task=task, request=request, elapsed_ms=elapsed_ms),
                'ok': False,
                'error': str(error)[:1200],
            }
        )


@contextmanager
def _record_helper_outputs(recorder: HelperOutputRecorder | None):
    if recorder is None:
        yield
        return
    original_values = {
        boss_tactics_module: boss_tactics_module.get_helper_provider,
        enemy_brain_module: enemy_brain_module.get_helper_provider,
        races_module: races_module.get_helper_provider,
        creature_generator_module: creature_generator_module.get_helper_provider,
    }
    try:
        for module in original_values:
            module.get_helper_provider = recorder.factory
        yield
    finally:
        for module, original in original_values.items():
            module.get_helper_provider = original


@contextmanager
def _recording_profile(recorder: HelperOutputRecorder | None, profile: str):
    if recorder is None:
        yield
        return
    previous = recorder.profile
    recorder.profile = profile
    try:
        yield
    finally:
        recorder.profile = previous


def _player(player_id: int, name: str, hp: int = 20, armor_class: int = 13, role: str = '') -> dict[str, Any]:
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


def _creature(creature_id: str, instance_id: str, **updates) -> dict[str, Any]:
    creature = instantiate_creature(core_creature(creature_id), instance_id=instance_id)
    for key, value in updates.items():
        if key == 'behavior' and isinstance(value, dict):
            creature['behavior'] = {**creature.get('behavior', {}), **value}
        elif key == 'hp_current':
            creature['hp']['current'] = int(value)
        else:
            creature[key] = value
    return creature


def built_in_snapshots() -> list[dict[str, Any]]:
    boss = _creature(
        'cult_leader',
        'enemy_cult_leader_1',
        behavior={'primaryGoal': 'complete_ritual', 'intelligenceProfile': 'tactical'},
    )
    boss_snapshot = {
        'status': 'active',
        'round': 3,
        'participants': [
            _player(1, 'Loki', hp=16, armor_class=14, role='warlock'),
            _player(2, 'Himeros', hp=9, armor_class=12, role='cleric'),
            boss,
        ],
        'battlefield': {
            'environmentType': 'ritual_chamber',
            'lighting': 'dim',
            'visibility': 'clear',
            'hazards': [{'id': 'ritual_fire', 'name': 'Ritual Fire'}],
            'cover': [{'id': 'bone_pillar', 'name': 'Bone Pillar', 'coverType': 'half'}],
            'interactables': [{'id': 'unstable_altar', 'name': 'Unstable Altar'}],
        },
        'flags': {
            'combatDifficultyAI': {
                'tacticalLevel': 'smart',
                'allowBossTacticsHelper': True,
                'allowBossWarmPlanner': True,
                'allowSentientEnemyBrain': True,
                'forceSentientEnemyBrain': True,
                'maxLlmCallsPerRound': 2,
                'skipLlmWhenTopCandidateMarginExceeds': 0,
            }
        },
    }

    mercenary = _creature(
        'mercenary',
        'enemy_mercenary_1',
        hp_current=12,
        behavior={
            'primaryGoal': 'protect_location',
            'intelligenceProfile': 'trained',
            'targetPriority': ['healer', 'wounded', 'spellcaster'],
        },
    )
    bandit = _creature(
        'bandit_thug',
        'enemy_bandit_1',
        hp_current=8,
        behavior={'intelligenceProfile': 'average', 'selfPreservation': 65},
    )
    sentient_snapshot = {
        'status': 'active',
        'round': 2,
        'participants': [
            _player(1, 'Loki', hp=7, armor_class=13, role='warlock'),
            _player(2, 'Himeros', hp=22, armor_class=15, role='cleric healer'),
            mercenary,
            bandit,
        ],
        'battlefield': {
            'environmentType': 'forest_ruins',
            'lighting': 'bright',
            'visibility': 'clear',
            'cover': [{'id': 'fallen_column', 'name': 'Fallen Column', 'coverType': 'half'}],
            'exits': [{'id': 'east_trail', 'name': 'East Trail', 'blocked': False}],
        },
        'flags': {
            'combatDifficultyAI': {
                'tacticalLevel': 'smart',
                'allowBossTacticsHelper': False,
                'allowSentientEnemyBrain': True,
                'forceSentientEnemyBrain': True,
                'maxLlmCallsPerRound': 2,
                'skipLlmWhenTopCandidateMarginExceeds': 0,
            }
        },
    }

    return [boss_snapshot, sentient_snapshot]


def _load_snapshots(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return built_in_snapshots()
    payload = json.loads(path.read_text())
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get('snapshots'), list):
        return [item for item in payload['snapshots'] if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    raise SystemExit(f'Unsupported snapshot payload in {path}')


@contextmanager
def _profile_environment(profile: str):
    keys = list(PROFILE_TASK_ENV.values())
    old_values = {key: os.environ.get(key) for key in keys}
    try:
        profile_name = profile.strip().lower()
        if profile_name == 'old_defaults':
            for task, key in PROFILE_TASK_ENV.items():
                os.environ[key] = OLD_DEFAULT_TASK_PROFILES[task]
        elif profile_name not in {'current_defaults', 'default', 'current'}:
            for key in keys:
                os.environ[key] = profile_name
        yield
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _quality_score(metrics: dict[str, Any]) -> float:
    total = max(1, int(metrics.get('total_decisions') or 0))
    helper_rate = (metrics.get('helper_assisted') or 0) / total
    score = 50.0
    score += helper_rate * 20
    score += float(metrics.get('selected_non_fallback_rate') or 0) * 15
    score += float(metrics.get('changed_baseline_rate') or 0) * 10
    score -= float(metrics.get('fallback_used_rate') or 0) * 35
    score -= float(metrics.get('resolution_stale_rate') or 0) * 35
    return round(max(0.0, min(100.0, score)), 2)


def _safe_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _trait_has_contract_fields(trait: Any) -> bool:
    if not isinstance(trait, dict):
        return False
    required = {'id', 'name', 'description', 'category', 'balanceCost', 'mechanics', 'aiHint'}
    return required.issubset(trait.keys())


def _custom_race_contract_score(race: dict[str, Any], *, model: str) -> float:
    traits = race.get('traits') if isinstance(race.get('traits'), list) else []
    visual = race.get('visual') if isinstance(race.get('visual'), dict) else {}
    physical = race.get('physical') if isinstance(race.get('physical'), dict) else {}
    balance = race.get('balance') if isinstance(race.get('balance'), dict) else {}
    score = 45.0
    score += 10 if race.get('name') else 0
    score += 10 if len(traits) >= 2 else max(0, len(traits)) * 4
    score += 10 if traits and all(_trait_has_contract_fields(trait) for trait in traits) else 0
    score += 10 if {'portraitKey', 'iconKey', 'bodyType', 'commonFeatures'}.issubset(visual.keys()) else 0
    score += 5 if {'averageHeight', 'averageWeight'}.issubset(physical.keys()) else 0
    score += 5 if balance.get('tier') or race.get('approvalStatus') else 0
    score += 5 if _safe_len(race.get('aiNarrationHints')) > 0 else 0
    score += 5 if model not in {'deterministic', 'deterministic_fallback'} else 0
    return round(max(0.0, min(100.0, score)), 2)


def _creature_contract_score(creature: dict[str, Any], *, model: str) -> float:
    abilities = creature.get('abilities') if isinstance(creature.get('abilities'), list) else []
    behavior = creature.get('behavior') if isinstance(creature.get('behavior'), dict) else {}
    stats = creature.get('stats') if isinstance(creature.get('stats'), dict) else {}
    balance = creature.get('balance') if isinstance(creature.get('balance'), dict) else {}
    score = 45.0
    score += 10 if creature.get('name') and creature.get('descriptionShort') else 0
    score += 10 if abilities else 0
    score += 10 if {'maxHp', 'armorClass'}.issubset(stats.keys()) else 0
    score += 10 if {'intelligenceProfile', 'combatRole', 'primaryGoal', 'targetPriority', 'tactics'}.issubset(behavior.keys()) else 0
    score += 5 if _safe_len(creature.get('aiNarrationHints')) > 0 else 0
    score += 5 if balance.get('estimatedTier') not in {'overpowered', None} else 0
    score += 5 if model not in {'deterministic', 'deterministic_fallback'} else 0
    return round(max(0.0, min(100.0, score)), 2)


def _call_helper(
    task: str,
    prompt: str,
    system_message: str,
    *,
    recorder: HelperOutputRecorder | None = None,
) -> tuple[str, str, float]:
    started = time.perf_counter()
    provider = recorder.factory(task=task) if recorder else llm_provider_module.get_helper_provider(task=task)
    response = provider.generate(
        ProviderRequest(
            prompt=prompt,
            system_message=system_message,
        )
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return response.text, response.model, elapsed_ms


def _evaluate_custom_race(*, progress: bool = False, recorder: HelperOutputRecorder | None = None) -> dict[str, Any]:
    _progress(progress, '  task custom_race start')
    prompt = (
        'Create a playable crystal-veined moon elf offshoot for a mystery-heavy campaign. '
        'They should sense hidden doors, endure psychic pressure, and have one costly lunar flare ability.'
    )
    try:
        text, model, elapsed_ms = _call_helper(
            CUSTOM_RACE_HELPER_TASK,
            _build_custom_race_prompt(prompt, 'standard', 'balanced'),
            CUSTOM_RACE_SYSTEM_MESSAGE,
            recorder=recorder,
        )
        payload = extract_json_object(text)
        if not payload:
            raise ValueError('helper returned invalid JSON')
        race = _race_payload_from_helper(payload, prompt)
        traits = race.get('traits') if isinstance(race.get('traits'), list) else []
        result = {
            'task': CUSTOM_RACE_HELPER_TASK,
            'elapsed_ms': elapsed_ms,
            'model': model,
            'score': _custom_race_contract_score(race, model=model),
            'valid': True,
            'summary': {
                'name': race.get('name'),
                'trait_count': len(traits),
                'balance_tier': (race.get('balance') or {}).get('tier'),
                'approval_status': race.get('approvalStatus'),
            },
        }
        _progress(progress, f"  task custom_race done {elapsed_ms}ms score={result['score']}")
        return result
    except Exception as exc:
        _progress(progress, f'  task custom_race error {str(exc)[:160]}')
        return {
            'task': CUSTOM_RACE_HELPER_TASK,
            'elapsed_ms': None,
            'model': None,
            'score': 0.0,
            'valid': False,
            'error': str(exc)[:500],
        }


def _evaluate_creature_generation(*, progress: bool = False, recorder: HelperOutputRecorder | None = None) -> dict[str, Any]:
    _progress(progress, '  task creature_generation start')
    input_payload = {
        'campaignTone': 'dangerous but heroic',
        'campaignThemes': ['ancient ruins', 'forbidden research'],
        'forbiddenThemes': ['graphic gore'],
        'partyLevel': 3,
        'partySize': 4,
        'location': 'collapsed star observatory',
        'region': 'Moonfall Ridge',
        'encounterPurpose': 'guarding a damaged astrolabe',
        'difficulty': 'hard',
        'desiredRole': 'controller',
        'desiredCreatureType': 'construct',
        'creatureConcept': 'a cracked brass observatory sentinel that bends gravity in short pulses',
        'existingBestiaryNames': ['Bandit Thug', 'Cult Leader', 'Wolf'],
        'existingWorldLore': 'Moonfall Ridge has unstable lunar metal that reacts to spellcasting.',
        'maxAbilities': 3,
        'allowFlight': False,
        'allowHardControl': False,
        'allowInstantDeath': False,
    }
    try:
        text, model, elapsed_ms = _call_helper(
            CREATURE_HELPER_TASK,
            build_creature_generation_prompt(input_payload),
            CREATURE_SYSTEM_MESSAGE,
            recorder=recorder,
        )
        payload = extract_json_object(text)
        if not payload:
            raise ValueError('helper returned invalid JSON')
        creature = normalize_creature_definition(payload, source='generated')
        analysis = analyze_creature_balance(
            creature,
            party_level=input_payload['partyLevel'],
            party_size=input_payload['partySize'],
            target_difficulty=input_payload['difficulty'],
        )
        creature['balance'] = analysis
        if analysis['estimatedTier'] == 'overpowered' or analysis.get('warnings'):
            creature = auto_scale_creature(
                creature,
                analysis,
                target_difficulty=input_payload['difficulty'],
                party_level=input_payload['partyLevel'],
                party_size=input_payload['partySize'],
            )
        abilities = creature.get('abilities') if isinstance(creature.get('abilities'), list) else []
        result = {
            'task': CREATURE_HELPER_TASK,
            'elapsed_ms': elapsed_ms,
            'model': model,
            'score': _creature_contract_score(creature, model=model),
            'valid': True,
            'summary': {
                'name': creature.get('name'),
                'ability_count': len(abilities),
                'challenge_tier': creature.get('challengeTier'),
                'estimated_tier': (creature.get('balance') or {}).get('estimatedTier'),
            },
        }
        _progress(progress, f"  task creature_generation done {elapsed_ms}ms score={result['score']}")
        return result
    except Exception as exc:
        _progress(progress, f'  task creature_generation error {str(exc)[:160]}')
        return {
            'task': CREATURE_HELPER_TASK,
            'elapsed_ms': None,
            'model': None,
            'score': 0.0,
            'valid': False,
            'error': str(exc)[:500],
        }


def _generation_task_average(generation_tasks: list[dict[str, Any]]) -> float:
    if not generation_tasks:
        return 0.0
    return round(sum(float(task.get('score') or 0.0) for task in generation_tasks) / len(generation_tasks), 2)


def _record_preview(result: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    records = []
    for run in result.get('runs') or []:
        for record in run.get('records') or []:
            records.append(
                {
                    'snapshot_index': run.get('snapshot_index'),
                    'actor_id': record.get('actor_id'),
                    'method': record.get('selection_method'),
                    'helper_selected': record.get('helper_selected_candidate_id'),
                    'executed': record.get('executed_candidate_id'),
                    'changed_baseline': record.get('helper_changed_baseline'),
                    'fallback_used': record.get('fallback_used'),
                    'stale': record.get('resolution_stale'),
                    'confidence': record.get('confidence'),
                }
            )
    return records[:limit]


def evaluate_profile(
    profile: str,
    snapshots: list[dict[str, Any]],
    *,
    progress: bool = False,
    recorder: HelperOutputRecorder | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    with _profile_environment(profile), _recording_profile(recorder, profile):
        _progress(progress, f'profile {profile} combat start')
        combat_started = time.perf_counter()
        result = run_combat_helper_evaluation(deepcopy(snapshots))
        combat_elapsed_ms = round((time.perf_counter() - combat_started) * 1000, 1)
        _progress(progress, f'profile {profile} combat done {combat_elapsed_ms}ms')
        generation_tasks = [
            _evaluate_custom_race(progress=progress, recorder=recorder),
            _evaluate_creature_generation(progress=progress, recorder=recorder),
        ]
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    metrics = result.get('metrics') or {}
    combat_score = _quality_score(metrics)
    generation_score = _generation_task_average(generation_tasks)
    return {
        'profile': profile,
        'elapsed_ms': elapsed_ms,
        'combat': {
            'elapsed_ms': combat_elapsed_ms,
            'metrics': metrics,
            'quality_score': combat_score,
            'records': _record_preview(result),
        },
        'generation_tasks': generation_tasks,
        'generation_quality_score': generation_score,
        'quality_score': round((combat_score * 0.6) + (generation_score * 0.4), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Compare AIDM helper model profiles on fixed combat snapshots.')
    parser.add_argument('snapshot_file', nargs='?', type=Path, help='Optional JSON combat snapshot file.')
    parser.add_argument(
        '--profiles',
        default='current_defaults,old_defaults,fast,deepseek_pro,codex_low,codex_medium,codex_high,codex_extra_high',
        help='Comma-separated helper profiles to test. Default: current_defaults,old_defaults,fast,deepseek_pro,codex_low,codex_medium,codex_high,codex_extra_high.',
    )
    parser.add_argument('--indent', type=int, default=2, help='JSON indentation for output.')
    parser.add_argument('--no-env', action='store_true', help='Do not load .env/.env.local before running.')
    parser.add_argument('--quiet-progress', action='store_true', help='Suppress per-profile progress logs on stderr.')
    parser.add_argument('--save-outputs', type=Path, help='Save raw helper outputs and parsed JSON to this file.')
    parser.add_argument(
        '--raw-output-max-chars',
        type=int,
        default=0,
        help='Max raw output chars per call in --save-outputs. Default 0 saves full output.',
    )
    args = parser.parse_args()

    if not args.no_env:
        load_runtime_env(REPO_ROOT)

    snapshots = _load_snapshots(args.snapshot_file)
    profiles = [item.strip() for item in args.profiles.split(',') if item.strip()]
    results = []
    recorder = HelperOutputRecorder(max_chars=args.raw_output_max_chars) if args.save_outputs else None
    with _record_helper_outputs(recorder):
        for profile in profiles:
            _progress(not args.quiet_progress, f'profile {profile} start')
            try:
                result = evaluate_profile(profile, snapshots, progress=not args.quiet_progress, recorder=recorder)
                results.append(result)
                _progress(
                    not args.quiet_progress,
                    f"profile {profile} done {result['elapsed_ms']}ms quality={result['quality_score']}",
                )
            except Exception as exc:
                results.append({'profile': profile, 'error': str(exc), 'elapsed_ms': None, 'quality_score': 0.0})
                _progress(not args.quiet_progress, f'profile {profile} error {str(exc)[:160]}')

    successful = [result for result in results if not result.get('error')]
    fastest = min(successful, key=lambda item: item['elapsed_ms'])['profile'] if successful else None
    best_quality = max(successful, key=lambda item: item['quality_score'])['profile'] if successful else None
    payload = {
        'snapshot_count': len(snapshots),
        'profiles': results,
        'winner_by_speed': fastest,
        'winner_by_quality': best_quality,
        'quality_score_note': 'Contract-focused score: 60% combat helper metrics, 40% custom race/creature schema-contract validation.',
    }
    if args.save_outputs:
        payload['raw_outputs_file'] = str(args.save_outputs)
        payload['raw_output_call_count'] = len(recorder.calls) if recorder else 0
        args.save_outputs.parent.mkdir(parents=True, exist_ok=True)
        args.save_outputs.write_text(
            json.dumps(
                {
                    'summary': payload,
                    'calls': recorder.calls if recorder else [],
                },
                indent=args.indent,
                sort_keys=True,
            ),
            encoding='utf-8',
        )
    print(json.dumps(payload, indent=args.indent, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
