"""SRD prices for the gear starter kits and the item-effect catalog use.

Prices are keyed by normalised item name (see :func:`price_key`); a merchant
stock entry written as ``{"catalog": "<effectId or item key>"}`` is priced and
described from here, and selling uses the same table.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping

from aidm_server.canon_text import int_or_default


COIN_VALUES_CP = {'pp': 1000, 'gp': 100, 'ep': 50, 'sp': 10, 'cp': 1}
CHANGE_DENOMINATIONS = ('gp', 'sp', 'cp')


def _item(name: str, item_type: str, price: dict[str, int], **extra: Any) -> dict[str, Any]:
    return {'name': name, 'type': item_type, 'price': price, **extra}


# Keyed by ``price_key(name)``; ``aliases`` are other names that price the same item.
PRICE_LIST: dict[str, dict[str, Any]] = {
    # Weapons
    'club': _item('Club', 'weapon', {'sp': 1}),
    'dagger': _item('Dagger', 'weapon', {'gp': 2}),
    'dart': _item('Dart', 'weapon', {'cp': 5}),
    'greataxe': _item('Greataxe', 'weapon', {'gp': 30}),
    'greatsword': _item('Greatsword', 'weapon', {'gp': 50}),
    'handaxe': _item('Handaxe', 'weapon', {'gp': 5}),
    'javelin': _item('Javelin', 'weapon', {'sp': 5}),
    'light crossbow': _item('Light Crossbow', 'weapon', {'gp': 25}),
    'light hammer': _item('Light Hammer', 'weapon', {'gp': 2}),
    'longbow': _item('Longbow', 'weapon', {'gp': 50}),
    'longsword': _item('Longsword', 'weapon', {'gp': 15}),
    'mace': _item('Mace', 'weapon', {'gp': 5}),
    'quarterstaff': _item('Quarterstaff', 'weapon', {'sp': 2}),
    'rapier': _item('Rapier', 'weapon', {'gp': 25}),
    'scimitar': _item('Scimitar', 'weapon', {'gp': 25}),
    'shortbow': _item('Shortbow', 'weapon', {'gp': 25}),
    'shortsword': _item('Shortsword', 'weapon', {'gp': 10}),
    'spear': _item('Spear', 'weapon', {'gp': 1}),
    'battleaxe': _item('Battleaxe', 'weapon', {'gp': 10}),
    'warhammer': _item('Warhammer', 'weapon', {'gp': 15}),
    'arrow': _item('Arrow', 'ammo', {'cp': 5}),
    'crossbow bolt': _item('Crossbow Bolt', 'ammo', {'cp': 5}),
    # Armour
    'leather armor': _item('Leather Armor', 'armor', {'gp': 10}, aliases=('leather armour',)),
    'studded leather armor': _item('Studded Leather Armor', 'armor', {'gp': 45}, aliases=('studded leather armour',)),
    'scale mail': _item('Scale Mail', 'armor', {'gp': 50}),
    'chain shirt': _item('Chain Shirt', 'armor', {'gp': 50}),
    'chain mail': _item('Chain Mail', 'armor', {'gp': 75}),
    'shield': _item('Shield', 'armor', {'gp': 10}, aliases=('wooden shield',)),
    # Adventuring gear
    'backpack': _item('Backpack', 'gear', {'gp': 2}),
    'bedroll': _item('Bedroll', 'gear', {'gp': 1}),
    'burglars pack': _item("Burglar's Pack", 'gear', {'gp': 16}),
    'candle': _item('Candle', 'gear', {'cp': 1}),
    'chalk': _item('Chalk', 'gear', {'cp': 1}),
    'component pouch': _item('Component Pouch', 'focus', {'gp': 25}),
    'crowbar': _item('Crowbar', 'gear', {'gp': 2}),
    'diplomats pack': _item("Diplomat's Pack", 'gear', {'gp': 39}),
    'explorers pack': _item("Explorer's Pack", 'gear', {'gp': 10}),
    'holy symbol': _item('Holy Symbol', 'focus', {'gp': 5}),
    'arcane focus': _item('Arcane Focus', 'focus', {'gp': 10}),
    'druidic focus': _item('Druidic Focus', 'focus', {'gp': 1}),
    'lantern': _item('Lantern', 'gear', {'gp': 5}),
    'lute': _item('Lute', 'tool', {'gp': 35}),
    'oil': _item('Oil', 'gear', {'sp': 1}, aliases=('flask of oil',)),
    'priests pack': _item("Priest's Pack", 'gear', {'gp': 19}),
    'ration': _item('Ration', 'consumable', {'sp': 5}, aliases=('rations',)),
    'rope': _item('Rope', 'gear', {'gp': 1}, aliases=('hempen rope',)),
    'scholars pack': _item("Scholar's Pack", 'gear', {'gp': 40}),
    'spellbook': _item('Spellbook', 'focus', {'gp': 50}),
    'thieves tools': _item("Thieves' Tools", 'tool', {'gp': 25}),
    'tinderbox': _item('Tinderbox', 'gear', {'sp': 5}),
    'tinkers tools': _item("Tinker's Tools", 'tool', {'gp': 50}),
    'torch': _item('Torch', 'gear', {'cp': 1}),
    'waterskin': _item('Waterskin', 'gear', {'sp': 2}),
    # Item-effect catalog (ids match ``item_effects.ITEM_EFFECTS``)
    'potion of healing': _item('Potion of Healing', 'potion', {'gp': 50}, effectId='potion_healing', aliases=('healing potion',)),
    'potion of greater healing': _item('Potion of Greater Healing', 'potion', {'gp': 150}, effectId='potion_greater_healing'),
    'potion of superior healing': _item('Potion of Superior Healing', 'potion', {'gp': 450}, effectId='potion_superior_healing'),
    'antitoxin': _item('Antitoxin', 'consumable', {'gp': 50}, effectId='antitoxin'),
    'healers kit': _item("Healer's Kit", 'tool', {'gp': 5}, effectId='healers_kit', metadata={'usesRemaining': 10}),
    'holy water': _item('Holy Water', 'consumable', {'gp': 25}, effectId='holy_water'),
    'alchemists fire': _item("Alchemist's Fire", 'consumable', {'gp': 50}, effectId='alchemists_fire'),
    'acid': _item('Acid', 'consumable', {'gp': 25}, effectId='acid_vial', aliases=('vial of acid',)),
}

SCROLL_PRICES_BY_SPELL_LEVEL = {0: {'gp': 25}, 1: {'gp': 75}, 2: {'gp': 150}, 3: {'gp': 300}}


def price_key(value: Any) -> str:
    """Lower-case words without punctuation: ``Healer's Kit`` -> ``healers kit``."""

    text = re.sub(r"['’]", '', str(value or '').lower().replace('_', ' ').replace('-', ' '))
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', text)).strip()


def _index() -> dict[str, str]:
    index: dict[str, str] = {}
    for key, entry in PRICE_LIST.items():
        index[key] = key
        for alias in entry.get('aliases', ()):
            index[price_key(alias)] = key
        if entry.get('effectId'):
            index[price_key(entry['effectId'])] = key
    return index


_INDEX = _index()


def catalog_item(key: Any) -> dict[str, Any] | None:
    """The priced template for an item name, alias, or effect id (``None`` when unlisted)."""

    entry_key = _INDEX.get(price_key(key))
    if entry_key is None:
        return None
    entry = PRICE_LIST[entry_key]
    item = {'key': entry_key, 'name': entry['name'], 'type': entry['type'], 'price': dict(entry['price'])}
    if entry.get('effectId'):
        item['effectId'] = entry['effectId']
    if entry.get('metadata'):
        item['metadata'] = deepcopy(entry['metadata'])
    return item


def scroll_price(spell_level: int) -> dict[str, int] | None:
    price = SCROLL_PRICES_BY_SPELL_LEVEL.get(int(spell_level))
    return dict(price) if price else None


def normalize_price(value: Any) -> dict[str, int]:
    """Keep positive whole-coin amounts of known denominations."""

    raw = value if isinstance(value, Mapping) else {}
    return {
        code: amount
        for code in COIN_VALUES_CP
        if (amount := max(0, int_or_default(raw.get(code), default=0))) > 0
    }


def price_in_copper(value: Any) -> int:
    return sum(COIN_VALUES_CP[code] * amount for code, amount in normalize_price(value).items())


def coins_for_copper(amount_cp: int) -> dict[str, int]:
    """Express a copper amount in gold, silver and copper (largest coins first)."""

    remaining = max(0, int(amount_cp))
    coins: dict[str, int] = {}
    for code in CHANGE_DENOMINATIONS:
        count, remaining = divmod(remaining, COIN_VALUES_CP[code])
        if count:
            coins[code] = count
    return coins


def price_label(value: Any) -> str:
    price = normalize_price(value)
    return ', '.join(f'{amount} {code}' for code, amount in price.items()) or '0 cp'


def copper_label(amount_cp: int) -> str:
    return price_label(coins_for_copper(amount_cp))
