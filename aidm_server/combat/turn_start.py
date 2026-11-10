"""Turn-start hooks: rules that fire as a participant begins its turn.

Hooks registered with :func:`register_turn_start_hook` are called as
``fn(combat, actor_id=..., turn_id=..., round_number=...)``.  They mutate the
combat they are given (the persisted turn advance or an enemy block's working
copy) and return the events they resolved, which the caller records with the
turn's other trusted combat results.  A hook must roll deterministically from
its arguments so the enemy block and the persisted advance reach the same
result.  This module imports nothing heavy so any module may register a hook
at import time.
"""

from __future__ import annotations

from typing import Any, Callable


TurnStartHook = Callable[..., list[dict[str, Any]] | None]

_TURN_START_HOOKS: list[TurnStartHook] = []


def register_turn_start_hook(fn: TurnStartHook) -> TurnStartHook:
    if fn not in _TURN_START_HOOKS:
        _TURN_START_HOOKS.append(fn)
    return fn


def unregister_turn_start_hook(fn: TurnStartHook) -> None:
    if fn in _TURN_START_HOOKS:
        _TURN_START_HOOKS.remove(fn)


def run_turn_start_hooks(combat: dict[str, Any], *, actor_id: str, turn_id: Any, round_number: int) -> list[dict[str, Any]]:
    """Every registered hook's events for ``actor_id`` starting its turn, in registration order."""

    events: list[dict[str, Any]] = []
    for hook in list(_TURN_START_HOOKS):
        events.extend(
            event
            for event in hook(combat, actor_id=actor_id, turn_id=turn_id, round_number=round_number) or []
            if isinstance(event, dict)
        )
    return events
