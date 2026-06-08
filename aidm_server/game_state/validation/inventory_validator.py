from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any

from aidm_server.game_state.models import normalize_item_name


AUTO_RESOLVE_CONFIDENCE_THRESHOLD = 0.85


def _item_labels(item: dict[str, Any]) -> set[str]:
    labels = {normalize_item_name(item.get('name'))}
    labels.update(normalize_item_name(alias) for alias in item.get('aliases') or [])
    labels.update(normalize_item_name(tag) for tag in item.get('tags') or [])
    if item.get('subtype'):
        labels.add(normalize_item_name(item.get('subtype')))
    return {label for label in labels if label}


def _resolved(item: dict[str, Any], method: str, confidence: float) -> dict[str, Any]:
    return {
        'status': 'resolved',
        'itemId': item.get('id'),
        'itemName': item.get('name'),
        'resolutionMethod': method,
        'confidence': round(float(confidence), 3),
        'needsClarification': False,
    }


def _clarification(reason: str, items: list[dict[str, Any]], requested_name: str | None = None) -> dict[str, Any]:
    label = requested_name or 'item'
    return {
        'status': 'needs_clarification',
        'reason': reason,
        'query': f'Which {label} do you use?' if label != 'item' else 'Which item do you use?',
        'options': [
            {
                'itemId': item.get('id'),
                'label': item.get('name'),
                'description': 'Equipped' if item.get('equipped') else str(item.get('type') or 'item'),
            }
            for item in items
        ],
    }


def _recency_score(last_used_at_turn: Any, current_turn: int) -> float:
    try:
        last_turn = int(last_used_at_turn)
    except (TypeError, ValueError):
        return 0.0
    age = max(0, int(current_turn or 0) - last_turn)
    if age == 0:
        return 0.95
    if age == 1:
        return 0.9
    if age == 2:
        return 0.86
    if age <= 5:
        return 0.75
    if age <= 10:
        return 0.6
    return 0.4


def _context_score(item: dict[str, Any], recent_context: list[str]) -> float:
    joined = normalize_item_name('\n'.join(recent_context or []))
    if not joined:
        return 0.0
    name = normalize_item_name(item.get('name'))
    if name and name in joined:
        return 0.9
    for label in _item_labels(item) - {name}:
        if label and re.search(rf'\b{re.escape(label)}\b', joined):
            return 0.86
    return 0.0


def _fuzzy_candidates(items: list[dict[str, Any]], requested_name: str) -> list[tuple[dict[str, Any], float]]:
    requested = normalize_item_name(requested_name)
    scored: list[tuple[dict[str, Any], float]] = []
    for item in items:
        best = 0.0
        for label in _item_labels(item):
            if not label:
                continue
            best = max(best, SequenceMatcher(None, requested, label).ratio())
        if best >= 0.78:
            scored.append((item, best))
    return sorted(scored, key=lambda entry: entry[1], reverse=True)


def resolve_inventory_item_reference(
    *,
    actor_inventory: list[dict[str, Any]],
    requested_name: str,
    requested_type: str | None = None,
    requested_subtype: str | None = None,
    current_turn: int = 0,
    recent_context: list[str] | None = None,
    default_item_id: str | None = None,
    selected_item_id: str | None = None,
) -> dict[str, Any]:
    requested_name = str(requested_name or '').strip()
    if not requested_name and not selected_item_id:
        return {
            'status': 'missing',
            'reason': 'No item name was provided.',
            'searchedName': requested_name,
        }

    if selected_item_id:
        selected = next((item for item in actor_inventory if str(item.get('id')) == str(selected_item_id)), None)
        if selected:
            return _resolved(selected, 'exact_id', 1.0)
        return {
            'status': 'missing',
            'reason': f"Selected item '{selected_item_id}' is not in inventory.",
            'searchedName': selected_item_id,
        }

    normalized_requested = normalize_item_name(requested_name)

    exact_id = next((item for item in actor_inventory if str(item.get('id')) == requested_name), None)
    if exact_id:
        return _resolved(exact_id, 'exact_id', 1.0)

    exact_name_matches = [
        item
        for item in actor_inventory
        if normalize_item_name(item.get('name')) == normalized_requested
    ]
    if len(exact_name_matches) == 1:
        return _resolved(exact_name_matches[0], 'exact_name', 1.0)
    if len(exact_name_matches) > 1:
        equipped_exact = [item for item in exact_name_matches if item.get('equipped')]
        if len(equipped_exact) == 1:
            return _resolved(equipped_exact[0], 'equipped_item', 0.97)
        return _clarification(f"Multiple items exactly match '{requested_name}'.", exact_name_matches, requested_name)

    requested_type = normalize_item_name(requested_type) or None
    requested_subtype = normalize_item_name(requested_subtype) or None
    candidates: list[dict[str, Any]] = []
    for item in actor_inventory:
        if requested_type and normalize_item_name(item.get('type')) != requested_type:
            continue
        item_labels = _item_labels(item)
        item_subtype = normalize_item_name(item.get('subtype'))
        if requested_subtype and item_subtype != requested_subtype:
            continue
        item_name = normalize_item_name(item.get('name'))
        if (
            normalized_requested in item_labels
            or item_subtype == normalized_requested
            or normalized_requested in item_name
        ):
            candidates.append(item)

    if not candidates:
        fuzzy = _fuzzy_candidates(actor_inventory, requested_name)
        if len(fuzzy) == 1 and fuzzy[0][1] >= 0.88:
            return _resolved(fuzzy[0][0], 'fuzzy', fuzzy[0][1])
        if len(fuzzy) > 1 and fuzzy[0][1] >= 0.88 and fuzzy[0][1] - fuzzy[1][1] >= 0.12:
            return _resolved(fuzzy[0][0], 'fuzzy', fuzzy[0][1])
        if fuzzy:
            return _clarification(f"Multiple fuzzy matches found for '{requested_name}'.", [item for item, _score in fuzzy], requested_name)
        return {
            'status': 'missing',
            'reason': f"No inventory item matches '{requested_name}'.",
            'searchedName': requested_name,
        }

    equipped_candidates = [item for item in candidates if item.get('equipped')]
    if len(equipped_candidates) == 1:
        return _resolved(equipped_candidates[0], 'equipped_item', 0.95)
    if len(equipped_candidates) > 1:
        return _clarification(f"Multiple equipped items match '{requested_name}'.", equipped_candidates, requested_name)

    if len(candidates) == 1:
        return _resolved(candidates[0], 'single_candidate', 0.9)

    context_scores = sorted(
        ((item, _context_score(item, recent_context or [])) for item in candidates),
        key=lambda entry: entry[1],
        reverse=True,
    )
    if context_scores and context_scores[0][1] >= AUTO_RESOLVE_CONFIDENCE_THRESHOLD:
        second_score = context_scores[1][1] if len(context_scores) > 1 else 0.0
        if context_scores[0][1] - second_score >= 0.1:
            return _resolved(context_scores[0][0], 'recent_context', context_scores[0][1])

    recently_used = sorted(
        (
            (item, _recency_score(item.get('lastUsedAtTurn'), current_turn))
            for item in candidates
            if item.get('lastUsedAtTurn') is not None
        ),
        key=lambda entry: entry[1],
        reverse=True,
    )
    if recently_used and recently_used[0][1] >= AUTO_RESOLVE_CONFIDENCE_THRESHOLD:
        second_score = recently_used[1][1] if len(recently_used) > 1 else 0.0
        if recently_used[0][1] - second_score >= 0.15:
            return _resolved(recently_used[0][0], 'recent_context', recently_used[0][1])

    if default_item_id:
        default_item = next((item for item in candidates if str(item.get('id')) == str(default_item_id)), None)
        if default_item:
            return _resolved(default_item, 'default_item', 0.8)

    favorite_candidates = [item for item in candidates if item.get('favorite')]
    if len(favorite_candidates) == 1:
        return _resolved(favorite_candidates[0], 'default_item', 0.78)

    return _clarification(f"Multiple matching items found for '{requested_name}'.", candidates, requested_name)
