"""Searching takes what it finds.

A successful ``search`` (or ``open``) of a container outside a fight reveals its contents as scene items, as
always, and the hero who searched then takes each of them in the same turn through the ordinary exact-id scene pickup
(coins into their purse), so the turn's beats read ``Taken — …`` and ``+6 gp``. An item its pack marks
``"leave": true`` (heavy, dangerous, or left for the players to choose) stays in the scene with its ``Take``
suggestion, and so does anything found while a fight is on. The pickups carry :data:`TAKEN_BY_SEARCH` (the
container's id) so the narration tells them with the search: ``You find and take 6 gp and a Black-Feathered Arrow.``
"""

from __future__ import annotations

from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.combat.state import normalize_combat_state

TAKEN_BY_SEARCH = 'takenBySearch'
TAKING_ACTIONS = frozenset({'search', 'open'})


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _fighting(state: dict[str, Any]) -> bool:
    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    return _text(combat.get('status')).lower() in {'starting', 'active'}


def search_take_actions(state: dict[str, Any], applied_changes: list[dict[str, Any]], *, actor_id: str) -> list[dict[str, Any]]:
    """The declared pickups of what the hero's successful searches and openings this turn revealed, outside a fight.

    ``state`` is the state once those object actions are applied (their contents are scene items).
    """

    if _fighting(state):
        return []
    scene_item_ids = {_text(item.get('id')) for item in _dicts((state.get('currentScene') or {}).get('items'))}
    actions: list[dict[str, Any]] = []
    for change in applied_changes:
        event = change.get('event') if isinstance(change.get('event'), dict) else {}
        if (
            change.get('type') != 'scene.interactable.action'
            or _text(event.get('actorId')) != actor_id
            or _text(change.get('objectAction') or event.get('action')).lower() not in TAKING_ACTIONS
            or _text(event.get('type')).endswith('.check_failed')
        ):
            continue
        for item in _dicts(event.get('revealedItems')):
            item_id = _text(item.get('id'))
            if not item_id or item.get('leave') is True or item_id not in scene_item_ids:
                continue
            actions.append(
                {
                    'id': f'search_take_{len(actions) + 1:03d}',
                    'type': 'scene.item.pickup',
                    'actorId': actor_id,
                    'confidence': 1.0,
                    'sourceText': f"Take what the search of {_text(event.get('targetName')) or 'it'} found.",
                    'requiresDMResolution': False,
                    'itemId': item_id,
                    'itemName': _text(item.get('name')),
                    'quantity': max(1, int_or_default(item.get('quantity'), default=1)),
                    TAKEN_BY_SEARCH: _text(change.get('targetId') or event.get('targetId')),
                }
            )
    return actions


def taken_by_search(changes: list[dict[str, Any]], actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The pickup changes of ``actions``, each marked with the container its search emptied."""

    containers = {_text(action.get('itemId')): action[TAKEN_BY_SEARCH] for action in actions}
    return [
        {**change, TAKEN_BY_SEARCH: containers[_text(change.get('itemId'))]}
        if _text(change.get('itemId')) in containers
        else change
        for change in changes
    ]
