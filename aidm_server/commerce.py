"""Shops: server prices, stock, change, and selling.

Runtime shape (written by the campaign-pack importer)::

    snapshot.merchants = {"<merchantId>": {"id", "name", "npcId", "locationId",
        "buyRate": 0.5, "restock": "long_rest" | "never",
        "stock": [{"stockId", "name", "type", "effectId"?, "metadata"?,
                   "price": {"gp": 50}, "quantity": 3 | None}],
        "initialStock": [...]}}

A stock entry may instead be ``{"stockId", "catalog": "<effectId or item key>",
"quantity"}``; it is priced and described from :mod:`aidm_server.price_list`.
``quantity: None`` means unlimited.  A merchant is open when its NPC is present
in the current scene (or, without an NPC, when the party is at its location)
and no combat is running.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from aidm_server.canon_text import int_or_default
from aidm_server.combat.state import normalize_combat_state
from aidm_server.game_state.models import CURRENCY_CODES, actor_currency, actor_items, actor_name, stable_item_id
from aidm_server.item_effects import item_effect_id, scroll_spell
from aidm_server.price_list import (
    COIN_VALUES_CP,
    catalog_item,
    coins_for_copper,
    copper_label,
    normalize_price,
    price_in_copper,
    price_key,
    price_label,
    scroll_price,
)


COMMERCE_SOURCE = 'pre_dm_shop_action'
COMMERCE_AUTHORITY = 'commerce_engine'
DEFAULT_BUY_RATE = 0.5
RESTOCK_POLICIES = ('long_rest', 'never')


def _text(value: Any) -> str:
    return str(value or '').strip()


def _quantity(value: Any) -> int | None:
    return None if value is None else max(0, int_or_default(value, default=0))


def stock_entry(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """One stock entry with name, type and price resolved (catalog entries priced here)."""

    entry = dict(raw)
    if _text(entry.get('catalog')):
        template = catalog_item(entry['catalog'])
        if template is None:
            return None
        entry = {**template, **{key: value for key, value in entry.items() if key != 'catalog'}}
        entry.pop('key', None)
    price = normalize_price(entry.get('price'))
    if not _text(entry.get('stockId')) or not _text(entry.get('name')) or not price:
        return None
    normalized = {
        'stockId': _text(entry['stockId']),
        'name': _text(entry['name']),
        'type': _text(entry.get('type')) or 'gear',
        'price': price,
        'quantity': _quantity(entry.get('quantity')),
    }
    if _text(entry.get('effectId')):
        normalized['effectId'] = _text(entry['effectId'])
    if isinstance(entry.get('metadata'), dict) and entry['metadata']:
        normalized['metadata'] = deepcopy(entry['metadata'])
    if _text(entry.get('itemId')):
        normalized['itemId'] = _text(entry['itemId'])
    return normalized


def normalize_merchant(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping) or not _text(raw.get('id')):
        return None
    restock = _text(raw.get('restock')).lower()
    buy_rate = raw.get('buyRate')
    return {
        'id': _text(raw['id']),
        'name': _text(raw.get('name')) or _text(raw['id']),
        'npcId': _text(raw.get('npcId')) or None,
        'locationId': _text(raw.get('locationId')) or None,
        'buyRate': max(0.0, min(1.0, float(buy_rate))) if isinstance(buy_rate, (int, float)) else DEFAULT_BUY_RATE,
        'restock': restock if restock in RESTOCK_POLICIES else 'long_rest',
        'stock': [entry for raw_entry in raw.get('stock') or [] if isinstance(raw_entry, Mapping) and (entry := stock_entry(raw_entry))],
    }


def merchants(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = state.get('merchants') if isinstance(state.get('merchants'), Mapping) else {}
    return [merchant for value in raw.values() if (merchant := normalize_merchant(value)) is not None]


def merchant_is_open(state: Mapping[str, Any], merchant: Mapping[str, Any]) -> bool:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), Mapping) else {}
    combat = normalize_combat_state(state.get('combat'), dict(scene))
    if _text(combat.get('status')).lower() in {'starting', 'active'}:
        return False
    if merchant.get('npcId'):
        return merchant['npcId'] in {_text(value) for value in scene.get('activeNpcIds') or []}
    return bool(merchant.get('locationId')) and merchant['locationId'] == _text(scene.get('locationId'))


def open_merchants(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [merchant for merchant in merchants(state) if merchant_is_open(state, merchant)]


def merchant_projection(merchant: Mapping[str, Any]) -> dict[str, Any]:
    """The public view of an open merchant for players and the DM."""

    return {
        'id': merchant['id'],
        'name': merchant['name'],
        'npcId': merchant.get('npcId'),
        'buyRate': merchant['buyRate'],
        'stock': [
            {key: deepcopy(entry[key]) for key in ('stockId', 'name', 'type', 'price', 'quantity')}
            for entry in merchant['stock']
        ],
    }


def open_merchant_projections(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [merchant_projection(merchant) for merchant in open_merchants(state)]


def _find_merchant(state: Mapping[str, Any], merchant_id: Any) -> dict[str, Any] | None:
    return next((merchant for merchant in merchants(state) if merchant['id'] == _text(merchant_id)), None)


def _stock_matches_name(entry: Mapping[str, Any], name: str) -> bool:
    wanted = price_key(name)
    if not wanted:
        return False
    if price_key(entry['name']) == wanted:
        return True
    template = catalog_item(name)
    if template is None:
        return False
    if template.get('effectId') and template['effectId'] == entry.get('effectId'):
        return True
    return price_key(template['name']) == price_key(entry['name'])


def route_purchase(state: Mapping[str, Any], item_name: Any) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The open merchant's stock entry a free-text purchase names (cheapest first)."""

    candidates = [
        (price_in_copper(entry['price']), merchant['id'], merchant, entry)
        for merchant in open_merchants(state)
        for entry in merchant['stock']
        if _stock_matches_name(entry, _text(item_name))
    ]
    if not candidates:
        return None
    _price, _merchant_id, merchant, entry = min(candidates, key=lambda candidate: candidate[:2])
    return merchant, entry


def route_sale(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """The open merchant a free-text sale goes to (first by id)."""

    candidates = sorted(open_merchants(state), key=lambda merchant: merchant['id'])
    return candidates[0] if candidates else None


def funds_in_copper(currency: Mapping[str, Any]) -> int:
    return sum(COIN_VALUES_CP[code] * max(0, int_or_default(currency.get(code), default=0)) for code in CURRENCY_CODES)


def payment_deltas(currency: Mapping[str, Any], cost_cp: int) -> dict[str, int] | None:
    """Coins to hand over (negative) and change received (positive) to pay ``cost_cp``.

    Pays largest coins first without overpaying; if smaller coins cannot cover
    the rest, breaks the smallest remaining coin and takes change in gp/sp/cp.
    Returns ``None`` when the purse cannot cover the cost.
    """

    purse = {code: max(0, int_or_default(currency.get(code), default=0)) for code in CURRENCY_CODES}
    if cost_cp <= 0:
        return {}
    if funds_in_copper(purse) < cost_cp:
        return None
    spent = {code: 0 for code in CURRENCY_CODES}
    remaining = cost_cp
    for code in sorted(CURRENCY_CODES, key=lambda value: -COIN_VALUES_CP[value]):
        take = min(purse[code], remaining // COIN_VALUES_CP[code])
        spent[code] += take
        remaining -= take * COIN_VALUES_CP[code]
    change = 0
    if remaining > 0:
        breaker = min(
            (code for code in CURRENCY_CODES if purse[code] - spent[code] > 0),
            key=lambda value: COIN_VALUES_CP[value],
        )
        spent[breaker] += 1
        change = COIN_VALUES_CP[breaker] - remaining
    deltas = {code: -amount for code, amount in spent.items() if amount}
    for code, amount in coins_for_copper(change).items():
        deltas[code] = deltas.get(code, 0) + amount
    return {code: amount for code, amount in deltas.items() if amount}


def _purchased_item(actor: Mapping[str, Any], entry: Mapping[str, Any], quantity: int) -> dict[str, Any]:
    metadata = deepcopy(entry.get('metadata') or {})
    if entry.get('effectId'):
        metadata['effectId'] = entry['effectId']
    stack = next(
        (
            item
            for item in actor_items(dict(actor))
            if price_key(item.get('name')) == price_key(entry['name'])
            and not item.get('equipped')
            and item_effect_id(item) == (entry.get('effectId') or item_effect_id({'name': entry['name']}))
            and (not entry.get('itemId') or _text(item.get('id')) == entry['itemId'])
        ),
        None,
    )
    item = {
        'id': _text(stack.get('id')) if stack else entry.get('itemId') or stable_item_id(entry['name']),
        'name': entry['name'],
        'type': entry['type'],
        'quantity': quantity,
    }
    if metadata:
        item['metadata'] = metadata
    return item


def resolve_purchase(
    state: Mapping[str, Any],
    actor: Mapping[str, Any],
    *,
    merchant_id: Any,
    stock_id: Any,
    quantity: Any,
) -> dict[str, Any]:
    """``{"ok", "reason", "transaction"}`` for buying ``quantity`` of one stock entry."""

    merchant = _find_merchant(state, merchant_id)
    if merchant is None:
        return {'ok': False, 'reason': 'That merchant is not known here.'}
    if not merchant_is_open(state, merchant):
        return {'ok': False, 'reason': f"{merchant['name']} is not open for trade right now."}
    entry = next((candidate for candidate in merchant['stock'] if candidate['stockId'] == _text(stock_id)), None)
    if entry is None:
        return {'ok': False, 'reason': f"{merchant['name']} does not sell that."}
    count = max(1, int_or_default(quantity, default=1))
    if entry['quantity'] is not None and entry['quantity'] < count:
        if entry['quantity'] == 0:
            return {'ok': False, 'reason': f"{merchant['name']} is out of {entry['name']}."}
        return {'ok': False, 'reason': f"{merchant['name']} has only {entry['quantity']} {entry['name']} left."}
    unit_cp = price_in_copper(entry['price'])
    total_cp = unit_cp * count
    currency = actor_currency(dict(actor))
    deltas = payment_deltas(currency, total_cp)
    if deltas is None:
        return {
            'ok': False,
            'reason': (
                f"{actor_name(dict(actor))} cannot afford {count} {entry['name']} for {copper_label(total_cp)}; "
                f"they have {copper_label(funds_in_copper(currency))}."
            ),
        }
    item = _purchased_item(actor, entry, count)
    return {
        'ok': True,
        'reason': f"{actor_name(dict(actor))} buys {count} {entry['name']} from {merchant['name']} for {copper_label(total_cp)}.",
        'transaction': {
            'merchantId': merchant['id'],
            'merchantName': merchant['name'],
            'stockId': entry['stockId'],
            'itemName': entry['name'],
            'quantity': count,
            'unitPrice': entry['price'],
            'totalCopper': total_cp,
            'currencyDeltas': deltas,
            'item': item,
        },
    }


def item_price(item: Mapping[str, Any]) -> dict[str, int] | None:
    """The listed price of an inventory item (``None`` when it has none)."""

    effect_id = item_effect_id(item)
    if effect_id == 'spell_scroll':
        spell = scroll_spell(item) or {}
        return scroll_price(spell['level']) if spell.get('level') is not None else None
    for key in [effect_id, item.get('name'), *(item.get('aliases') or [])]:
        template = catalog_item(key) if _text(key) else None
        if template:
            return template['price']
    return None


def resolve_sale(
    state: Mapping[str, Any],
    actor: Mapping[str, Any],
    *,
    merchant_id: Any,
    item_id: Any,
    quantity: Any,
) -> dict[str, Any]:
    """``{"ok", "reason", "transaction"}`` for selling ``quantity`` of one inventory item."""

    merchant = _find_merchant(state, merchant_id)
    if merchant is None:
        return {'ok': False, 'reason': 'That merchant is not known here.'}
    if not merchant_is_open(state, merchant):
        return {'ok': False, 'reason': f"{merchant['name']} is not open for trade right now."}
    item = next((candidate for candidate in actor_items(dict(actor)) if _text(candidate.get('id')) == _text(item_id)), None)
    if item is None:
        return {'ok': False, 'reason': f'{actor_name(dict(actor))} does not have that item.'}
    if item.get('equipped'):
        return {'ok': False, 'reason': f"Unequip {item.get('name')} before selling it."}
    count = max(1, int_or_default(quantity, default=1))
    held = max(0, int_or_default(item.get('quantity'), default=1))
    if held < count:
        return {'ok': False, 'reason': f"Not enough {item.get('name')}. Available: {held}."}
    price = item_price(item)
    if not price:
        return {'ok': False, 'reason': f"{merchant['name']} has no use for that."}
    value_cp = int(price_in_copper(price) * count * merchant['buyRate'])
    return {
        'ok': True,
        'reason': f"{actor_name(dict(actor))} sells {count} {item.get('name')} to {merchant['name']} for {copper_label(value_cp)}.",
        'transaction': {
            'merchantId': merchant['id'],
            'merchantName': merchant['name'],
            'itemId': _text(item.get('id')),
            'itemName': _text(item.get('name')),
            'quantity': count,
            'unitPrice': price,
            'totalCopper': value_cp,
            'currencyDeltas': coins_for_copper(value_cp),
        },
    }


def take_stock(state: dict[str, Any], merchant_id: str, stock_id: str, quantity: int) -> bool:
    """Decrement a merchant's stored stock entry; ``False`` when it can no longer cover it."""

    record = (state.get('merchants') or {}).get(merchant_id) if isinstance(state.get('merchants'), dict) else None
    if not isinstance(record, dict):
        return False
    entry = next(
        (candidate for candidate in record.get('stock') or [] if isinstance(candidate, dict) and _text(candidate.get('stockId')) == stock_id),
        None,
    )
    if entry is None:
        return False
    available = _quantity(entry.get('quantity'))
    if available is None:
        return True
    if available < quantity:
        return False
    entry['quantity'] = available - quantity
    return True


def restock_merchants(state: dict[str, Any]) -> list[str]:
    """A long rest restores ``initialStock`` for merchants that restock; returns their ids."""

    restocked: list[str] = []
    for merchant_id, record in (state.get('merchants') or {}).items() if isinstance(state.get('merchants'), dict) else []:
        if not isinstance(record, dict) or _text(record.get('restock')).lower() == 'never':
            continue
        if isinstance(record.get('initialStock'), list):
            record['stock'] = deepcopy(record['initialStock'])
            restocked.append(_text(merchant_id))
    return restocked


def merchant_context(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Open merchants for the DM: names and stock with readable prices."""

    return [
        {
            'id': merchant['id'],
            'name': merchant['name'],
            'npcId': merchant.get('npcId'),
            'stock': [
                {
                    'name': entry['name'],
                    'price': price_label(entry['price']),
                    'quantity': 'unlimited' if entry['quantity'] is None else entry['quantity'],
                }
                for entry in merchant['stock']
            ],
        }
        for merchant in open_merchants(state)
    ]
