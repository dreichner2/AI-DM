from __future__ import annotations

import math
import re
from typing import Any

from aidm_server.canon_text import int_or_default


ArmorProfile = tuple[int, int | None, str]


ARMOR_PROFILES: tuple[tuple[str, ArmorProfile], ...] = (
    ('studded leather', (12, None, 'light')),
    ('leather armor', (11, None, 'light')),
    ('leather', (11, None, 'light')),
    ('padded', (11, None, 'light')),
    ('half plate', (15, 2, 'medium')),
    ('breastplate', (14, 2, 'medium')),
    ('scale mail', (14, 2, 'medium')),
    ('chain shirt', (13, 2, 'medium')),
    ('hide armor', (12, 2, 'medium')),
    ('hide', (12, 2, 'medium')),
    ('plate armor', (18, 0, 'heavy')),
    ('plate', (18, 0, 'heavy')),
    ('splint', (17, 0, 'heavy')),
    ('chain mail', (16, 0, 'heavy')),
    ('ring mail', (14, 0, 'heavy')),
    ('tactical vest', (12, None, 'light')),
    ('vest', (12, None, 'light')),
)

ARMOR_FIELD_KEYS = ('baseAc', 'baseAC', 'base_ac', 'armorClass', 'armor_class', 'ac')
MAX_DEX_FIELD_KEYS = ('maxDexBonus', 'maxDEXBonus', 'max_dex_bonus', 'dexCap', 'dex_cap')
SHIELD_BONUS_FIELD_KEYS = ('shieldBonus', 'shield_bonus', 'acBonus', 'ac_bonus', 'armorClassBonus', 'armor_class_bonus', 'bonus')
STAT_AC_FIELD_KEYS = ('armorClass', 'armor_class', 'ac')
ARMOR_METADATA_KEYS = (*ARMOR_FIELD_KEYS, *MAX_DEX_FIELD_KEYS, *SHIELD_BONUS_FIELD_KEYS)
NON_BODY_ARMOR_LABELS = {
    'helmet',
    'helm',
    'hood',
    'cowl',
    'cloak',
    'cape',
    'boots',
    'gloves',
    'gauntlets',
    'bracers',
    'belt',
    'ring',
    'amulet',
    'necklace',
}


def ability_modifier(score: Any) -> int:
    return (int_or_default(score, default=10) - 10) // 2


def normalize_armor_text(value: Any) -> str:
    text = re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()
    return re.sub(r'\s+', ' ', text)


def _coerced_int(value: Any) -> int | None:
    if value in (None, ''):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        return int(value)
    except (OverflowError, TypeError, ValueError):
        return None


def _item_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get('metadata')
    return metadata if isinstance(metadata, dict) else {}


def _field_int(record: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    metadata = _item_metadata(record)
    for source in (record, metadata):
        for key in keys:
            parsed = _coerced_int(source.get(key))
            if parsed is not None:
                return parsed
    return None


def _stat_value(stats: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in stats:
            return stats.get(key)
    ability_scores = stats.get('ability_scores')
    if isinstance(ability_scores, dict):
        for key in keys:
            if key in ability_scores:
                return ability_scores.get(key)
    return None


def _item_labels(item: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in ('name', 'type', 'subtype', 'slot', 'equipmentSlot', 'equipment_slot'):
        value = item.get(key)
        if value:
            labels.append(normalize_armor_text(value))
    for key in ('aliases', 'tags'):
        values = item.get(key)
        if isinstance(values, list):
            labels.extend(normalize_armor_text(value) for value in values if value)
    return [label for label in labels if label]


def _label_text(item: dict[str, Any]) -> str:
    return ' '.join(_item_labels(item))


def _is_equipped(item: dict[str, Any]) -> bool:
    return bool(item.get('equipped'))


def _is_shield(item: dict[str, Any]) -> bool:
    labels = _item_labels(item)
    return any(label == 'shield' or ' shield' in f' {label} ' for label in labels)


def _is_body_armor(item: dict[str, Any]) -> bool:
    labels = _item_labels(item)
    if 'body armor' in labels or 'body_armor' in labels:
        return True
    slot = normalize_armor_text(item.get('slot') or item.get('equipmentSlot') or item.get('equipment_slot'))
    if slot == 'body armor':
        return True
    if slot and slot not in {'none', 'body armor'}:
        return False
    item_type = normalize_armor_text(item.get('type'))
    if item_type not in {'armor', 'armour'}:
        return False
    if _is_shield(item):
        return False
    labels_text = _label_text(item)
    if any(label in NON_BODY_ARMOR_LABELS for label in labels):
        return False
    if 'light armor' in labels_text or 'medium armor' in labels_text or 'heavy armor' in labels_text:
        return True
    return any(phrase in labels_text for phrase, _profile in ARMOR_PROFILES)


def _armor_kind_from_labels(labels_text: str) -> str:
    if 'heavy armor' in labels_text or 'heavy armour' in labels_text:
        return 'heavy'
    if 'medium armor' in labels_text or 'medium armour' in labels_text:
        return 'medium'
    if 'light armor' in labels_text or 'light armour' in labels_text:
        return 'light'
    return 'light'


def _default_profile_for_kind(kind: str) -> ArmorProfile:
    if kind == 'heavy':
        return (14, 0, kind)
    if kind == 'medium':
        return (12, 2, kind)
    return (11, None, 'light')


def _armor_profile(item: dict[str, Any]) -> ArmorProfile:
    labels_text = _label_text(item)
    kind = _armor_kind_from_labels(labels_text)
    explicit_base = _field_int(item, ARMOR_FIELD_KEYS)
    explicit_max_dex = _field_int(item, MAX_DEX_FIELD_KEYS)
    if explicit_base is not None:
        if explicit_max_dex is not None:
            return (explicit_base, explicit_max_dex, kind)
        return (explicit_base, _default_profile_for_kind(kind)[1], kind)
    for phrase, profile in ARMOR_PROFILES:
        if phrase in labels_text:
            return profile
    return _default_profile_for_kind(kind)


def _equipped_body_armor(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in items:
        if _is_equipped(item) and _is_body_armor(item):
            return item
    return None


def _shield_bonus(item: dict[str, Any]) -> int:
    explicit_bonus = _field_int(item, SHIELD_BONUS_FIELD_KEYS)
    if explicit_bonus is not None:
        return max(0, min(10, explicit_bonus))
    return 2


def _best_equipped_shield(items: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, int]:
    best_item: dict[str, Any] | None = None
    best_bonus = 0
    for item in items:
        if not (_is_equipped(item) and _is_shield(item)):
            continue
        bonus = _shield_bonus(item)
        if bonus > best_bonus:
            best_item = item
            best_bonus = bonus
    return best_item, best_bonus


def _bounded_ac(value: int) -> int:
    return max(1, min(40, value))


def armor_class_details(stats: dict[str, Any] | None, inventory_items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    safe_stats = stats if isinstance(stats, dict) else {}
    items = [item for item in (inventory_items or []) if isinstance(item, dict)]
    dexterity = _stat_value(safe_stats, 'dexterity', 'dex')
    dex_mod = ability_modifier(dexterity)
    unarmored_ac = 10 + dex_mod
    body_armor = _equipped_body_armor(items)
    shield, shield_bonus = _best_equipped_shield(items)

    if body_armor:
        armor_base, max_dex_bonus, armor_kind = _armor_profile(body_armor)
        dex_bonus = 0 if max_dex_bonus == 0 else dex_mod if max_dex_bonus is None else min(dex_mod, max_dex_bonus)
        base_ac = armor_base + dex_bonus
        source = 'armor'
        armor_name = body_armor.get('name')
    else:
        explicit_ac = None
        for key in STAT_AC_FIELD_KEYS:
            explicit_ac = _coerced_int(safe_stats.get(key))
            if explicit_ac is not None:
                break
        armor_base = explicit_ac if explicit_ac is not None else 10
        max_dex_bonus = None
        dex_bonus = None if explicit_ac is not None else dex_mod
        base_ac = explicit_ac if explicit_ac is not None else unarmored_ac
        source = 'explicit' if explicit_ac is not None else 'unarmored'
        armor_kind = 'none'
        armor_name = None

    total = _bounded_ac(base_ac + shield_bonus)
    return {
        'armorClass': total,
        'source': source,
        'armorName': armor_name,
        'armorKind': armor_kind,
        'armorBase': armor_base,
        'dexterity': dexterity,
        'dexModifier': dex_mod,
        'dexBonusApplied': dex_bonus,
        'maxDexBonus': max_dex_bonus,
        'shieldName': shield.get('name') if shield else None,
        'shieldBonus': shield_bonus,
        'unarmoredArmorClass': _bounded_ac(unarmored_ac),
    }


def calculate_armor_class(stats: dict[str, Any] | None, inventory_items: list[dict[str, Any]] | None = None) -> int:
    return int(armor_class_details(stats, inventory_items).get('armorClass') or 10)


def sync_actor_armor_class(actor: dict[str, Any] | None) -> int:
    if not isinstance(actor, dict):
        return 10
    stats = actor.setdefault('stats', {})
    if not isinstance(stats, dict):
        stats = {}
        actor['stats'] = stats
    inventory = actor.get('inventory') if isinstance(actor.get('inventory'), dict) else {}
    items = inventory.get('items') if isinstance(inventory.get('items'), list) else []
    details = armor_class_details(stats, items)
    stats['armorClass'] = details['armorClass']
    stats['armor_class'] = details['armorClass']
    metadata = actor.setdefault('metadata', {})
    if isinstance(metadata, dict):
        metadata['armorClassBreakdown'] = details
    return int(details['armorClass'])
