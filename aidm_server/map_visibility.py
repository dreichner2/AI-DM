"""Authoritative visibility policy for DM-authored maps."""

from __future__ import annotations

from typing import Any


MAP_VISIBILITY_PLAYER = 'player'
MAP_VISIBILITY_DM = 'dm'
MAP_VISIBILITY_VALUES = frozenset({MAP_VISIBILITY_PLAYER, MAP_VISIBILITY_DM})

_VISIBILITY_ALIASES = {
    'player': MAP_VISIBILITY_PLAYER,
    'players': MAP_VISIBILITY_PLAYER,
    'public': MAP_VISIBILITY_PLAYER,
    'revealed': MAP_VISIBILITY_PLAYER,
    'dm': MAP_VISIBILITY_DM,
    'dm_only': MAP_VISIBILITY_DM,
    'hidden': MAP_VISIBILITY_DM,
    'private': MAP_VISIBILITY_DM,
}


def normalize_map_visibility(value: Any, *, default: str | None = None) -> str | None:
    """Return a canonical map visibility, rejecting unknown values.

    Public API aliases are accepted for ergonomics, but persisted and emitted
    values are always ``player`` or ``dm``. Unknown stored values therefore
    fail closed instead of accidentally exposing an authored map.
    """

    if value is None:
        return default
    if not isinstance(value, str):
        return None
    return _VISIBILITY_ALIASES.get(value.strip().lower().replace('-', '_').replace(' ', '_'))


def map_is_player_visible(map_obj: Any) -> bool:
    return normalize_map_visibility(getattr(map_obj, 'visibility', None)) == MAP_VISIBILITY_PLAYER


def visible_maps_query(query, *, include_dm_only: bool):
    if include_dm_only:
        return query

    # Local import avoids making this policy helper part of the model import
    # cycle while still keeping every collection query on one predicate.
    from aidm_server.models import Map

    return query.filter(Map.visibility == MAP_VISIBILITY_PLAYER)
