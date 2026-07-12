"""Canonical, persisted weapon-proficiency selectors.

The rules engine consumes only this normalized character profile. Legacy
per-item assertions are converted by the schema migration; roll and inventory
requests never supply or alter live weapon proficiency.
"""

from __future__ import annotations

import json
import re
from typing import Any

from aidm_server.canon_text import normalized_name


WEAPON_PROFICIENCY_SCHEMA_VERSION = 1
MAX_WEAPON_PROFICIENCIES = 64
MAX_SELECTOR_LENGTH = 96

WEAPON_CATEGORIES = frozenset({'all', 'firearm', 'martial', 'simple'})

SIMPLE_WEAPONS = frozenset(
    {
        'club',
        'dagger',
        'dart',
        'greatclub',
        'handaxe',
        'javelin',
        'light crossbow',
        'light hammer',
        'mace',
        'quarterstaff',
        'shortbow',
        'sickle',
        'sling',
        'spear',
    }
)
MARTIAL_WEAPONS = frozenset(
    {
        'battleaxe',
        'blowgun',
        'flail',
        'glaive',
        'greataxe',
        'greatsword',
        'halberd',
        'hand crossbow',
        'heavy crossbow',
        'lance',
        'longbow',
        'longsword',
        'maul',
        'morningstar',
        'net',
        'pike',
        'rapier',
        'scimitar',
        'shortsword',
        'trident',
        'war pick',
        'warhammer',
        'whip',
    }
)
FIREARM_MARKERS = frozenset(
    {
        'firearm',
        'gun',
        'musket',
        'pistol',
        'revolver',
        'rifle',
        'shotgun',
        'sidearm',
    }
)


_CLASS_SELECTORS: dict[str, tuple[str, ...]] = {
    'artificer': ('category:simple',),
    'barbarian': ('category:simple', 'category:martial'),
    'bard': (
        'category:simple',
        'weapon:hand crossbow',
        'weapon:longsword',
        'weapon:rapier',
        'weapon:shortsword',
    ),
    'cleric': ('category:simple',),
    'druid': tuple(
        f'weapon:{name}'
        for name in (
            'club',
            'dagger',
            'dart',
            'javelin',
            'mace',
            'quarterstaff',
            'scimitar',
            'sickle',
            'sling',
            'spear',
        )
    ),
    'fighter': ('category:simple', 'category:martial'),
    'monk': ('category:simple', 'weapon:shortsword'),
    'paladin': ('category:simple', 'category:martial'),
    'ranger': ('category:simple', 'category:martial'),
    'rogue': (
        'category:simple',
        'weapon:hand crossbow',
        'weapon:longsword',
        'weapon:rapier',
        'weapon:shortsword',
    ),
    'sorcerer': tuple(
        f'weapon:{name}'
        for name in ('dagger', 'dart', 'light crossbow', 'quarterstaff', 'sling')
    ),
    'warlock': ('category:simple',),
    'wizard': tuple(
        f'weapon:{name}'
        for name in ('dagger', 'dart', 'light crossbow', 'quarterstaff', 'sling')
    ),
    'gunslinger': ('category:firearm', 'weapon:dagger'),
    'operative': ('category:firearm', 'weapon:knife'),
    'public safety officer': ('category:firearm',),
}


def _selector_text(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip().lower())[:MAX_SELECTOR_LENGTH]


def _canonical_selector(value: Any, *, default_kind: str = 'weapon') -> str | None:
    raw = _selector_text(value)
    if not raw:
        return None

    kind = default_kind
    label = raw
    if ':' in raw:
        raw_kind, raw_label = raw.split(':', 1)
        if raw_kind.strip() in {'category', 'id', 'weapon'}:
            kind = raw_kind.strip()
            label = raw_label
    elif raw in WEAPON_CATEGORIES or raw.removesuffix(' weapons').removesuffix(' weapon') in WEAPON_CATEGORIES:
        kind = 'category'
        label = raw.removesuffix(' weapons').removesuffix(' weapon')

    normalized = normalized_name(label)
    if not normalized:
        return None
    if kind == 'category' and normalized not in WEAPON_CATEGORIES:
        return None
    return f'{kind}:{normalized}'


def _loaded_profile(raw_value: Any) -> Any:
    if not isinstance(raw_value, str):
        return raw_value
    text = raw_value.strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return [part.strip() for part in text.split(',') if part.strip()]


def normalize_weapon_proficiencies(raw_value: Any) -> list[str]:
    """Normalize current and legacy profile shapes into bounded selectors."""

    loaded = _loaded_profile(raw_value)
    candidates: list[tuple[Any, str]] = []

    def add_values(values: Any, default_kind: str) -> None:
        if values in (None, ''):
            return
        items = values if isinstance(values, (list, tuple, set)) else [values]
        for value in items:
            if isinstance(value, dict):
                if default_kind == 'category':
                    value = value.get('category')
                elif default_kind == 'id':
                    value = value.get('id') or value.get('weapon_id')
                else:
                    value = value.get('name') or value.get('weapon')
            if value not in (None, ''):
                candidates.append((value, default_kind))

    if isinstance(loaded, dict):
        add_values(loaded.get('selectors'), 'weapon')
        add_values(loaded.get('categories'), 'category')
        add_values(loaded.get('weapons') or loaded.get('weapon_names'), 'weapon')
        add_values(loaded.get('weapon_ids') or loaded.get('ids'), 'id')
    elif isinstance(loaded, (list, tuple, set)):
        for value in loaded:
            if isinstance(value, dict):
                if value.get('category'):
                    candidates.append((value.get('category'), 'category'))
                if value.get('name') or value.get('weapon'):
                    candidates.append((value.get('name') or value.get('weapon'), 'weapon'))
                if value.get('id') or value.get('weapon_id'):
                    candidates.append((value.get('id') or value.get('weapon_id'), 'id'))
            else:
                candidates.append((value, 'weapon'))
    elif loaded not in (None, ''):
        candidates.append((loaded, 'weapon'))

    normalized: list[str] = []
    for value, default_kind in candidates:
        selector = _canonical_selector(value, default_kind=default_kind)
        if selector and selector not in normalized:
            normalized.append(selector)
        if len(normalized) >= MAX_WEAPON_PROFICIENCIES:
            break
    return sorted(normalized)


def serialize_weapon_proficiencies(raw_value: Any) -> str:
    return json.dumps(normalize_weapon_proficiencies(raw_value), separators=(',', ':'))


def _base_class_name(class_name: str | None) -> str:
    base = str(class_name or '').split('-', 1)[0]
    return normalized_name(base)


def default_weapon_proficiencies_for_class(class_name: str | None) -> list[str]:
    """Build a server-authored profile for a newly created character."""

    base_class = _base_class_name(class_name)
    selectors = list(_CLASS_SELECTORS.get(base_class, ()))

    # Every supported class/subclass has a server-owned starter kit. Persisting
    # its weapon names closes gaps for custom classes without granting a broad
    # category that the rules catalog has not explicitly defined.
    from aidm_server.starting_inventory import starting_inventory_for_class

    for item in starting_inventory_for_class(class_name):
        if normalized_name(item.get('type')) != 'weapon':
            continue
        for label in (item.get('name'), item.get('subtype')):
            selector = _canonical_selector(label, default_kind='weapon')
            if selector:
                selectors.append(selector)
    return normalize_weapon_proficiencies(selectors)


def weapon_categories(item: dict[str, Any]) -> set[str]:
    metadata = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
    labels = {
        normalized_name(item.get('name')),
        normalized_name(item.get('subtype')),
        *[normalized_name(alias) for alias in item.get('aliases') or []],
        *[normalized_name(tag) for tag in item.get('tags') or []],
    }
    labels.discard('')
    categories = {
        normalized_name(value)
        for value in (
            item.get('weapon_category'),
            item.get('weaponCategory'),
            metadata.get('weapon_category'),
            metadata.get('weaponCategory'),
            metadata.get('category'),
        )
        if normalized_name(value) in WEAPON_CATEGORIES
    }
    if any(any(marker in label for marker in FIREARM_MARKERS) for label in labels):
        categories.add('firearm')
    if labels.intersection(SIMPLE_WEAPONS):
        categories.add('simple')
    if labels.intersection(MARTIAL_WEAPONS):
        categories.add('martial')
    return categories


def match_weapon_proficiency(raw_profile: Any, item: dict[str, Any]) -> tuple[bool, str | None]:
    """Return whether a persisted profile covers an owned persisted weapon."""

    selectors = set(normalize_weapon_proficiencies(raw_profile))
    if not selectors:
        return False, None
    if 'category:all' in selectors:
        return True, 'category:all'

    item_id = normalized_name(item.get('id') or item.get('itemId'))
    if item_id and f'id:{item_id}' in selectors:
        return True, f'id:{item_id}'

    labels = {
        normalized_name(item.get('name')),
        normalized_name(item.get('subtype')),
        *[normalized_name(alias) for alias in item.get('aliases') or []],
    }
    labels.discard('')
    for label in sorted(labels):
        selector = f'weapon:{label}'
        if selector in selectors:
            return True, selector

    for category in sorted(weapon_categories(item)):
        selector = f'category:{category}'
        if selector in selectors:
            return True, selector
    return False, None
