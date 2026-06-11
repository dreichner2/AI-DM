"""Persistent character state helpers for rules, resources, and rewards."""

from __future__ import annotations

import re
from typing import Any

from aidm_server.canon_text import int_or_default, normalized_name
from aidm_server.database import db
from aidm_server.models import DmTurn, Player, safe_json_dumps, safe_json_loads
from aidm_server.spellbook import (
    known_spell_names,
    merge_spellbooks,
    spellbook_for_character,
    spellbook_from_character_sheet,
)


ABILITY_KEYS = ('strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma')
ABILITY_LABELS = {
    'strength': 'STR',
    'dexterity': 'DEX',
    'constitution': 'CON',
    'intelligence': 'INT',
    'wisdom': 'WIS',
    'charisma': 'CHA',
}
POINT_BUY_BUDGET = 27
POINT_BUY_COSTS = {
    8: 0,
    9: 1,
    10: 2,
    11: 3,
    12: 4,
    13: 5,
    14: 7,
    15: 9,
}

ROLL_TYPE_ABILITY = {
    'athletics': 'strength',
    'mobility': 'dexterity',
    'stealth': 'dexterity',
    'thieves_tools': 'dexterity',
    'lore': 'intelligence',
    'social': 'charisma',
    'attack': 'strength',
    'strength': 'strength',
    'dexterity': 'dexterity',
    'constitution': 'constitution',
    'intelligence': 'intelligence',
    'wisdom': 'wisdom',
    'charisma': 'charisma',
}
ROLL_TYPE_BASE_DC = {
    'attack': 14,
    'stealth': 14,
    'social': 15,
    'lore': 13,
    'athletics': 14,
    'thieves_tools': 15,
    'mobility': 14,
    'check': 14,
}

GOLD_SPEND_PATTERNS = [
    re.compile(r'\b(?:spend|pay|paid|buy|bought|purchase|purchased|hand over|give)\s+(?:[^.!?\n]{0,80}?\s+)?(\d{1,5})\s+(?:gp|gold)\b', re.IGNORECASE),
    re.compile(r'\b(?:for|costs?|price(?: is)?)\s+(\d{1,5})\s+(?:gp|gold)\b', re.IGNORECASE),
]
GOLD_GAIN_PATTERNS = [
    re.compile(r'\b(?:gain|gains|receive|receives|loot|loots|find|finds|earn|earns|take|takes)\s+(\d{1,5})\s+(?:gp|gold)\b', re.IGNORECASE),
]
GOLD_LOSS_PATTERNS = [
    re.compile(r'\b(?:spend|spends|pay|pays|paid|lose|loses|hand over|hands over|give|gives)\s+(\d{1,5})\s+(?:gp|gold)\b', re.IGNORECASE),
    re.compile(r'\b(?:buy|buys|bought|purchase|purchases|purchased)\b[^.!?\n]{0,80}?\b(?:for|costs?)\s+(\d{1,5})\s+(?:gp|gold)\b', re.IGNORECASE),
]
CURRENCY_ALIASES = {
    'copper': 'copper',
    'coppers': 'copper',
    'cp': 'copper',
    'silver': 'silver',
    'silvers': 'silver',
    'sp': 'silver',
    'electrum': 'electrum',
    'electrums': 'electrum',
    'ep': 'electrum',
    'platinum': 'platinum',
    'platinums': 'platinum',
    'pp': 'platinum',
}
CURRENCY_GAIN_PATTERNS = [
    re.compile(
        r'\b(?:gain|gains|receive|receives|loot|loots|find|finds|earn|earns|take|takes|collect|collects|pick up|picks up)\s+'
        r'(\d{1,5})\s+(copper|coppers|cp|silver|silvers|sp|electrum|electrums|ep|platinum|platinums|pp)'
        r'(?:\s+(?:pieces?|coins?))?\b',
        re.IGNORECASE,
    ),
]
CURRENCY_LOSS_PATTERNS = [
    re.compile(
        r'\b(?:spend|spends|pay|pays|paid|lose|loses|hand over|hands over|give|gives)\s+'
        r'(\d{1,5})\s+(copper|coppers|cp|silver|silvers|sp|electrum|electrums|ep|platinum|platinums|pp)'
        r'(?:\s+(?:pieces?|coins?))?\b',
        re.IGNORECASE,
    ),
]
DAMAGE_PATTERNS = [
    re.compile(r'\b(?:take|takes|suffer|suffers)\s+(\d{1,4})\s+(?:points?\s+of\s+)?damage\b', re.IGNORECASE),
    re.compile(r'\b(?:hit|hits|struck|wounded)\b[^.!?\n]{0,80}?\bfor\s+(\d{1,4})\s+(?:points?\s+of\s+)?damage\b', re.IGNORECASE),
]
HEAL_PATTERNS = [
    re.compile(r'\b(?:heal|heals|regain|regains|recover|recovers)\s+(\d{1,4})\s+(?:hp|hit points?)\b', re.IGNORECASE),
]
XP_GAIN_PATTERNS = [
    re.compile(r'\b(?:gain|gains|earn|earns|award(?:ed)?|receive|receives)\s+(\d{1,6})\s+(?:xp|experience)\b', re.IGNORECASE),
]


def _as_record(value: Any) -> dict[str, Any]:
    loaded = safe_json_loads(value, {})
    return loaded if isinstance(loaded, dict) else {}


def _coerce_score(value: Any) -> int | None:
    parsed = int_or_default(value, default=-1)
    return parsed if parsed >= 1 else None


def ability_modifier(score: int | None) -> int:
    if score is None:
        return 0
    return (int(score) - 10) // 2


def _extract_ability_scores(stats: dict[str, Any]) -> dict[str, int]:
    source = stats.get('ability_scores') if isinstance(stats.get('ability_scores'), dict) else stats
    scores: dict[str, int] = {}
    for key in ABILITY_KEYS:
        raw_value = source.get(key)
        if raw_value is None:
            raw_value = source.get(ABILITY_LABELS[key].lower())
        score = _coerce_score(raw_value)
        if score is not None:
            scores[key] = score
    return scores


def point_buy_spent(scores: dict[str, int]) -> int:
    spent = 0
    for key in ABILITY_KEYS:
        score = int(scores.get(key, 8))
        spent += POINT_BUY_COSTS.get(score, POINT_BUY_BUDGET + 1)
    return spent


def validate_point_buy_payload(raw_stats: Any, *, level: int) -> tuple[dict[str, Any] | None, str | None]:
    stats = _as_record(raw_stats)
    has_point_buy_shape = isinstance(stats.get('ability_scores'), dict) or isinstance(stats.get('point_buy'), dict)
    if not has_point_buy_shape:
        return stats, None

    scores = _extract_ability_scores(stats)
    missing = [key for key in ABILITY_KEYS if key not in scores]
    if missing:
        return None, f"stats.ability_scores is missing: {', '.join(missing)}."

    for key, score in scores.items():
        if score not in POINT_BUY_COSTS:
            return None, f'stats.ability_scores.{key} must be between 8 and 15 for point buy.'

    spent = point_buy_spent(scores)
    if spent > POINT_BUY_BUDGET:
        return None, f'stats point buy exceeds {POINT_BUY_BUDGET} points.'

    con_mod = ability_modifier(scores['constitution'])
    max_hp = int_or_default(
        stats.get('max_hp', stats.get('hp_max', stats.get('max_hit_points'))),
        default=max(1, 8 + con_mod + max(0, int(level) - 1) * max(1, 5 + con_mod)),
    )
    current_hp = int_or_default(
        stats.get('current_hp', stats.get('hp_current', stats.get('hp'))),
        default=max_hp,
    )
    current_hp = max(0, min(current_hp, max_hp))
    gold = max(0, int_or_default(stats.get('gold'), default=0))
    xp = max(0, int_or_default(stats.get('xp', stats.get('experience')), default=0))

    normalized: dict[str, Any] = {
        **stats,
        'ability_scores': scores,
        'point_buy': {
            'budget': POINT_BUY_BUDGET,
            'spent': spent,
            'remaining': POINT_BUY_BUDGET - spent,
        },
        'current_hp': current_hp,
        'hp_current': current_hp,
        'max_hp': max_hp,
        'hp_max': max_hp,
        'gold': gold,
        'xp': xp,
        'experience': xp,
        'proficiency_bonus': 2 + max(0, int(level) - 1) // 4,
        'armor_class': int_or_default(stats.get('armor_class', stats.get('ac')), default=10 + ability_modifier(scores['dexterity'])),
        'initiative': int_or_default(stats.get('initiative'), default=ability_modifier(scores['dexterity'])),
        'speed': int_or_default(stats.get('speed'), default=30),
        'carrying_capacity': int_or_default(stats.get('carrying_capacity'), default=scores['strength'] * 15),
    }
    for key, score in scores.items():
        normalized[key] = score
        normalized[ABILITY_LABELS[key].lower()] = score
    return normalized, None


def serialize_stats_payload(raw_stats: Any, *, level: int) -> tuple[str | None, str | None]:
    if raw_stats is None:
        return None, None
    normalized, error = validate_point_buy_payload(raw_stats, level=level)
    if error:
        return None, error
    return safe_json_dumps(normalized, {}), None


def character_state_for_player(player: Player | None) -> dict[str, Any]:
    if not player:
        return {}

    stats = _as_record(player.stats)
    scores = _extract_ability_scores(stats)
    con_mod = ability_modifier(scores.get('constitution'))
    max_hp = int_or_default(
        stats.get('max_hp', stats.get('hp_max', stats.get('max_hit_points'))),
        default=(max(1, 8 + con_mod + max(0, int(player.level or 1) - 1) * max(1, 5 + con_mod)) if scores else 0),
    )
    current_hp = int_or_default(
        stats.get('current_hp', stats.get('hp_current', stats.get('hp'))),
        default=max_hp,
    )
    gold = max(0, int_or_default(stats.get('gold'), default=0))
    copper = max(0, int_or_default(stats.get('copper'), default=0))
    silver = max(0, int_or_default(stats.get('silver'), default=0))
    electrum = max(0, int_or_default(stats.get('electrum'), default=0))
    platinum = max(0, int_or_default(stats.get('platinum'), default=0))
    xp = max(0, int_or_default(stats.get('xp', stats.get('experience')), default=0))
    spent = point_buy_spent(scores) if scores and all(key in scores for key in ABILITY_KEYS) else None
    spellbook = merge_spellbooks(
        spellbook_from_character_sheet(player.character_sheet),
        spellbook_for_character(
            class_name=player.class_,
            race_name=player.race,
            race_selection=player.race_selection,
            level=player.level or 1,
        ),
    )

    state = {
        'ability_scores': scores,
        'ability_modifiers': {key: ability_modifier(scores.get(key)) for key in ABILITY_KEYS if key in scores},
        'point_buy': {
            'budget': POINT_BUY_BUDGET,
            'spent': spent,
            'remaining': (POINT_BUY_BUDGET - spent if spent is not None else None),
        },
        'hp': {
            'current': max(0, current_hp),
            'max': max_hp,
            'bloodied': bool(max_hp and current_hp <= max_hp // 2),
            'critical': bool(max_hp and current_hp <= max(1, max_hp // 4)),
        },
        'gold': gold,
        'copper': copper,
        'silver': silver,
        'electrum': electrum,
        'platinum': platinum,
        'xp': xp,
        'level': int(player.level or 1),
        'proficiency_bonus': int_or_default(stats.get('proficiency_bonus'), default=2 + max(0, int(player.level or 1) - 1) // 4),
    }
    if spellbook.get('knownSpells'):
        state['spellbook'] = spellbook
        state['spells'] = known_spell_names(spellbook)
    return state


def apply_character_dc_adjustment(rule_hint, player: Player | None):
    if not getattr(rule_hint, 'requires_roll', False) or getattr(rule_hint, 'roll_value', None) is not None:
        return rule_hint
    state = character_state_for_player(player)
    ability_key = ROLL_TYPE_ABILITY.get(rule_hint.roll_type or 'check')
    scores = state.get('ability_scores') if isinstance(state.get('ability_scores'), dict) else {}
    modifiers = state.get('ability_modifiers') if isinstance(state.get('ability_modifiers'), dict) else {}
    ability_mod = int_or_default(modifiers.get(ability_key), default=0) if ability_key else 0
    hp = state.get('hp') if isinstance(state.get('hp'), dict) else {}
    current_hp = int_or_default(hp.get('current'), default=0)
    max_hp = int_or_default(hp.get('max'), default=0)
    hp_penalty = 0
    if max_hp > 0:
        ratio = current_hp / max_hp
        if ratio <= 0.25:
            hp_penalty = 4
        elif ratio <= 0.5:
            hp_penalty = 2
        elif ratio <= 0.75:
            hp_penalty = 1

    base_dc = ROLL_TYPE_BASE_DC.get(rule_hint.roll_type or 'check', ROLL_TYPE_BASE_DC['check'])
    adjusted_dc = max(5, min(30, base_dc - ability_mod + hp_penalty))
    ability_label = ABILITY_LABELS.get(ability_key or '', 'ability')
    score = scores.get(ability_key) if ability_key else None
    details = [f'base {base_dc}', f'{ability_label} {score if score is not None else "unknown"} mod {ability_mod:+d}']
    if hp_penalty:
        details.append(f'wounded +{hp_penalty}')
    rule_hint.dc_hint = f'{adjusted_dc} ({", ".join(details)})'
    return rule_hint


def inventory_contains(player: Player | None, item_name: str | None, quantity: int = 1) -> bool:
    if not player or not item_name:
        return False
    from aidm_server.canon_inventory import load_inventory

    wanted = normalized_name(item_name)
    for item in load_inventory(player.inventory):
        if normalized_name(item.get('name')) == wanted and int_or_default(item.get('quantity'), default=1) >= quantity:
            return True
    return False


def requested_gold_spend(text: str | None) -> int:
    total = 0
    for pattern in GOLD_SPEND_PATTERNS:
        for match in pattern.finditer(text or ''):
            total += max(0, int_or_default(match.group(1), default=0))
    return total


def _sum_patterns(text: str, patterns: list[re.Pattern]) -> int:
    total = 0
    for pattern in patterns:
        for match in pattern.finditer(text or ''):
            total += max(0, int_or_default(match.group(1), default=0))
    return total


def _sum_currency_patterns(text: str, patterns: list[re.Pattern]) -> dict[str, int]:
    totals = {denomination: 0 for denomination in {'copper', 'silver', 'electrum', 'platinum'}}
    for pattern in patterns:
        for match in pattern.finditer(text or ''):
            denomination = CURRENCY_ALIASES.get(str(match.group(2) or '').strip().lower())
            if denomination:
                totals[denomination] += max(0, int_or_default(match.group(1), default=0))
    return totals


def _intent_confirmed_gold_delta(turn: DmTurn, dm_output: str, *, gold_gain: int, gold_loss: int) -> int:
    metadata = safe_json_loads(turn.metadata_json, {})
    action_intent = metadata.get('action_intent') if isinstance(metadata, dict) else None
    if not isinstance(action_intent, dict) or action_intent.get('kind') != 'item':
        return 0

    inventory_action = str(action_intent.get('inventory_action') or '').strip().lower()
    if inventory_action not in {'buy', 'sell'}:
        return 0

    cost_gold = max(0, int_or_default(action_intent.get('cost_gold'), default=0))
    if not cost_gold:
        return 0

    from aidm_server.canon_inventory import inventory_change_from_intent_outcome

    if not inventory_change_from_intent_outcome(turn, dm_output):
        return 0
    if inventory_action == 'buy' and gold_loss <= 0:
        return -cost_gold
    if inventory_action == 'sell' and gold_gain <= 0:
        return cost_gold
    return 0


def _stats_with_state(player: Player) -> dict[str, Any]:
    stats = _as_record(player.stats)
    state = character_state_for_player(player)
    hp = state.get('hp') if isinstance(state.get('hp'), dict) else {}
    stats.setdefault('gold', state.get('gold', 0))
    stats.setdefault('copper', state.get('copper', 0))
    stats.setdefault('silver', state.get('silver', 0))
    stats.setdefault('electrum', state.get('electrum', 0))
    stats.setdefault('platinum', state.get('platinum', 0))
    stats.setdefault('xp', state.get('xp', 0))
    stats.setdefault('experience', state.get('xp', 0))
    if hp.get('max'):
        stats.setdefault('max_hp', hp.get('max'))
        stats.setdefault('hp_max', hp.get('max'))
        stats.setdefault('current_hp', hp.get('current'))
        stats.setdefault('hp_current', hp.get('current'))
    return stats


def _apply_delta(
    player: Player,
    *,
    gold_delta: int = 0,
    hp_delta: int = 0,
    xp_delta: int = 0,
    currency_delta: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    currency_delta = currency_delta or {}
    if not any((gold_delta, hp_delta, xp_delta, *currency_delta.values())):
        return None

    stats = _stats_with_state(player)
    current_gold = max(0, int_or_default(stats.get('gold'), default=0))
    current_xp = max(0, int_or_default(stats.get('xp', stats.get('experience')), default=0))
    max_hp = max(0, int_or_default(stats.get('max_hp', stats.get('hp_max')), default=0))
    current_hp = int_or_default(stats.get('current_hp', stats.get('hp_current', stats.get('hp'))), default=max_hp)

    next_gold = max(0, current_gold + gold_delta)
    next_xp = max(0, current_xp + xp_delta)
    next_hp = max(0, min(max_hp, current_hp + hp_delta)) if max_hp else max(0, current_hp + hp_delta)
    next_currency = {
        denomination: max(0, int_or_default(stats.get(denomination), default=0) + int(delta))
        for denomination, delta in currency_delta.items()
    }

    stats['gold'] = next_gold
    for denomination, amount in next_currency.items():
        stats[denomination] = amount
    stats['xp'] = next_xp
    stats['experience'] = next_xp
    if max_hp:
        stats['current_hp'] = next_hp
        stats['hp_current'] = next_hp
        stats['max_hp'] = max_hp
        stats['hp_max'] = max_hp
    player.stats = safe_json_dumps(stats, {})

    return {
        'player_id': player.player_id,
        'character_name': player.character_name,
        'gold_delta': gold_delta,
        'gold': next_gold,
        'currency_delta': {key: value for key, value in currency_delta.items() if value},
        **next_currency,
        'hp_delta': hp_delta,
        'hp_current': next_hp,
        'hp_max': max_hp,
        'xp_delta': xp_delta,
        'xp': next_xp,
    }


def apply_character_state_changes(turn: DmTurn, dm_output: str) -> list[dict[str, Any]]:
    if not turn.player_id:
        return []

    player = db.session.get(Player, turn.player_id)
    if not player:
        return []

    text = re.sub(r'\*+', '', dm_output or '')
    gold_gain = _sum_patterns(text, GOLD_GAIN_PATTERNS)
    gold_loss = _sum_patterns(text, GOLD_LOSS_PATTERNS)
    gold_delta = gold_gain - gold_loss + _intent_confirmed_gold_delta(turn, text, gold_gain=gold_gain, gold_loss=gold_loss)
    currency_gain = _sum_currency_patterns(text, CURRENCY_GAIN_PATTERNS)
    currency_loss = _sum_currency_patterns(text, CURRENCY_LOSS_PATTERNS)
    currency_delta = {
        denomination: currency_gain.get(denomination, 0) - currency_loss.get(denomination, 0)
        for denomination in {'copper', 'silver', 'electrum', 'platinum'}
        if currency_gain.get(denomination, 0) - currency_loss.get(denomination, 0)
    }
    hp_delta = _sum_patterns(text, HEAL_PATTERNS) - _sum_patterns(text, DAMAGE_PATTERNS)
    xp_delta = _sum_patterns(text, XP_GAIN_PATTERNS)
    applied = _apply_delta(
        player,
        gold_delta=gold_delta,
        hp_delta=hp_delta,
        xp_delta=xp_delta,
        currency_delta=currency_delta,
    )
    return [applied] if applied else []
