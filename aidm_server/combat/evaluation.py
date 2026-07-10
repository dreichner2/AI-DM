from __future__ import annotations

from statistics import mean
from typing import Any

from aidm_server.combat.intent_planner import plan_enemy_intents


def _selection_metadata(intent: dict[str, Any]) -> dict[str, Any]:
    for key in ('candidateSelection', 'bossTacticsSelection'):
        metadata = intent.get(key)
        if isinstance(metadata, dict):
            return metadata
    return {}


def _candidate_id(candidate: dict[str, Any] | None) -> str | None:
    if not isinstance(candidate, dict):
        return None
    value = candidate.get('candidateId') or candidate.get('candidate_id')
    return str(value) if value else None


def _fallback_candidate_id(candidates: list[dict[str, Any]]) -> str | None:
    fallback = next((candidate for candidate in candidates if candidate.get('isFallbackCandidate')), None)
    return _candidate_id(fallback or (candidates[0] if candidates else None))


def _deterministic_top_candidate_id(candidates: list[dict[str, Any]]) -> str | None:
    ranked = sorted(
        [candidate for candidate in candidates if isinstance(candidate, dict)],
        key=lambda candidate: int(candidate.get('deterministicRank') or 999),
    )
    return _candidate_id(ranked[0] if ranked else None)


def decision_record_from_intent(
    *,
    round_number: int | None,
    actor_id: str,
    intent: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata = _selection_metadata(intent)
    tactics_compilation = intent.get('tacticsCompilation') if isinstance(intent.get('tacticsCompilation'), dict) else {}
    fallback_id = _fallback_candidate_id(candidates)
    selected_id = metadata.get('selectedCandidateId') or intent.get('candidateId')
    executed_id = metadata.get('resolvedCandidateId') or intent.get('candidateId')
    helper_selected_id = selected_id if metadata or tactics_compilation else None
    deterministic_top_id = _deterministic_top_candidate_id(candidates)
    resolution_validation = intent.get('resolutionValidation') if isinstance(intent.get('resolutionValidation'), dict) else {}
    resolution_source = intent.get('resolutionSource')
    fallback_used = resolution_source in {'backup_candidate', 'deterministic_resolution_fallback', 'no_legal_candidate'}
    return {
        'round': round_number,
        'actor_id': actor_id,
        'selection_method': intent.get('selectionMethod'),
        'candidate_count': len(candidates),
        'fallback_candidate_id': fallback_id,
        'deterministic_top_candidate_id': deterministic_top_id,
        'helper_selected_candidate_id': helper_selected_id,
        'executed_candidate_id': executed_id,
        'helper_changed_baseline': bool(metadata.get('changedDeterministicBaseline'))
        or bool(tactics_compilation and selected_id and selected_id != deterministic_top_id),
        'selected_non_fallback': bool(helper_selected_id and fallback_id and helper_selected_id != fallback_id),
        'freeform_tactics_compiled': bool(tactics_compilation),
        'advisory_planner_applied': any(
            isinstance(candidate.get('bossPlanner'), dict)
            for candidate in candidates
            if isinstance(candidate, dict)
        ),
        'valid_on_first_pass': bool(resolution_validation.get('can_resolve_now', True)) and not fallback_used,
        'resolution_stale': bool(metadata.get('resolutionStale') or resolution_validation.get('staleCandidateVersion')),
        'fallback_used': fallback_used,
        'resolution_source': resolution_source,
        'selector_skipped_reason': intent.get('selectorSkippedReason'),
        'confidence': metadata.get('confidence', intent.get('confidence')),
    }


def summarize_decision_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    if not total:
        return {
            'total_decisions': 0,
            'helper_assisted': 0,
            'changed_baseline_rate': 0.0,
            'selected_non_fallback_rate': 0.0,
            'fallback_used_rate': 0.0,
            'resolution_stale_rate': 0.0,
            'average_candidate_count': 0.0,
            'freeform_tactics_compiled': 0,
            'advisory_planner_applied': 0,
        }
    helper_records = [record for record in records if record.get('helper_selected_candidate_id')]
    return {
        'total_decisions': total,
        'helper_assisted': len(helper_records),
        'changed_baseline_rate': round(sum(1 for record in records if record.get('helper_changed_baseline')) / total, 3),
        'selected_non_fallback_rate': round(sum(1 for record in records if record.get('selected_non_fallback')) / total, 3),
        'fallback_used_rate': round(sum(1 for record in records if record.get('fallback_used')) / total, 3),
        'resolution_stale_rate': round(sum(1 for record in records if record.get('resolution_stale')) / total, 3),
        'average_candidate_count': round(mean(record.get('candidate_count') or 0 for record in records), 2),
        'freeform_tactics_compiled': sum(1 for record in records if record.get('freeform_tactics_compiled')),
        'advisory_planner_applied': sum(1 for record in records if record.get('advisory_planner_applied')),
    }


def summarize_combat_helper_plan(intent_plan: dict[str, Any]) -> dict[str, Any]:
    round_number = intent_plan.get('round')
    candidates_by_enemy = intent_plan.get('intentCandidates') if isinstance(intent_plan.get('intentCandidates'), dict) else {}
    records = []
    for intent in intent_plan.get('intents') or []:
        if not isinstance(intent, dict) or not intent.get('enemyId'):
            continue
        actor_id = str(intent['enemyId'])
        candidates = candidates_by_enemy.get(actor_id) if isinstance(candidates_by_enemy.get(actor_id), list) else []
        records.append(
            decision_record_from_intent(
                round_number=round_number,
                actor_id=actor_id,
                intent=intent,
                candidates=candidates,
            )
        )
    return {
        'records': records,
        'metrics': summarize_decision_records(records),
    }


def run_combat_helper_evaluation(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    runs = []
    all_records = []
    for index, snapshot in enumerate(snapshots, start=1):
        plan = plan_enemy_intents(snapshot)
        summary = summarize_combat_helper_plan(plan)
        runs.append(
            {
                'snapshot_index': index,
                'round': plan.get('round'),
                'records': summary['records'],
                'metrics': summary['metrics'],
            }
        )
        all_records.extend(summary['records'])
    return {
        'snapshot_count': len(snapshots),
        'runs': runs,
        'metrics': summarize_decision_records(all_records),
    }
