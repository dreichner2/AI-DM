from __future__ import annotations

import math
import re
from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.damage_dice import MAX_DAMAGE_DICE_COUNT, MAX_DAMAGE_DIE_SIDES, normalize_damage_dice_expression
from aidm_server.creatures.schemas import CHALLENGE_TIERS, normalize_creature_definition


TIER_MULTIPLIERS = {
    'trivial': 0.45,
    'easy': 0.7,
    'standard': 1.0,
    'hard': 1.35,
    'deadly': 1.75,
    'boss': 3.0,
}
TIER_ORDER = ['trivial', 'easy', 'standard', 'hard', 'deadly', 'boss', 'overpowered']
DISABLING_CONDITIONS = {'stunned', 'paralyzed', 'incapacitated', 'unconscious', 'petrified'}
HARD_CONTROL_WORDS = {'stun', 'stunned', 'paralyze', 'paralyzed', 'incapacitate', 'incapacitated', 'dominate'}


def _party_level(party_level: int | None) -> int:
    return max(1, min(30, int_or_default(party_level, default=1)))


def _party_size(party_size: int | None) -> int:
    return max(1, min(10, int_or_default(party_size, default=4)))


def expected_hp_for_level(party_level: int, target_difficulty: str) -> int:
    level = _party_level(party_level)
    multiplier = TIER_MULTIPLIERS.get(target_difficulty, 1.0)
    return max(3, round((7 + level * 8) * multiplier))


def expected_dpr_for_level(party_level: int, party_size: int, target_difficulty: str) -> int:
    level = _party_level(party_level)
    size = _party_size(party_size)
    multiplier = TIER_MULTIPLIERS.get(target_difficulty, 1.0)
    return max(1, round((3 + level * 2.4) * multiplier * min(1.5, 0.75 + size * 0.08)))


def expected_ac_for_level(party_level: int, target_difficulty: str) -> int:
    level = _party_level(party_level)
    bonus = {'trivial': -2, 'easy': -1, 'standard': 0, 'hard': 1, 'deadly': 2, 'boss': 3}.get(target_difficulty, 0)
    return max(10, min(24, 11 + math.ceil(level / 4) + bonus))


def _average_die(die_count: int, die_size: int) -> float:
    return die_count * ((die_size + 1) / 2)


def average_damage_from_dice(dice: str | None) -> float:
    text = normalize_damage_dice_expression(dice)
    if not text:
        return 0.0
    total = 0.0
    for count, sides in re.findall(r'(\d*)d(\d+)', text):
        total += _average_die(int(count or 1), int(sides))
    flat_values = re.findall(r'(?<!d)([+-]\s*\d+)', text)
    for value in flat_values:
        try:
            total += int(value.replace(' ', ''))
        except ValueError:
            continue
    return max(0.0, total)


def estimate_average_damage_per_round(creature: dict[str, Any]) -> int:
    abilities = creature.get('abilities') if isinstance(creature.get('abilities'), list) else []
    damages = []
    for ability in abilities:
        if not isinstance(ability, dict):
            continue
        damage = ability.get('damage') if isinstance(ability.get('damage'), dict) else {}
        average = average_damage_from_dice(damage.get('dice'))
        if average <= 0:
            continue
        cooldown = str(ability.get('cooldown') or 'none')
        weight = 1.0
        if cooldown == 'recharge_5_6':
            weight = 0.33
        elif cooldown in {'once_per_combat', 'short_rest', 'long_rest'}:
            weight = 0.25
        damages.append(average * weight)
    if not damages:
        return 1
    damages.sort(reverse=True)
    return max(1, round(sum(damages[:2]) if str(creature.get('challengeTier')) == 'boss' else damages[0]))


def estimate_control_strength(creature: dict[str, Any]) -> int:
    score = 0
    for ability in creature.get('abilities') or []:
        if not isinstance(ability, dict):
            continue
        conditions = {str(item).strip().lower() for item in ability.get('conditionsApplied') or []}
        description = str(ability.get('description') or '').lower()
        cooldown = str(ability.get('cooldown') or 'none')
        if conditions & DISABLING_CONDITIONS or any(word in description for word in HARD_CONTROL_WORDS):
            score += 4
        elif conditions:
            score += 2
        if ability.get('save'):
            score = max(0, score - 1)
        if cooldown not in {'none', 'turn'}:
            score = max(0, score - 1)
    return min(20, score)


def classify_creature_tier(*, hp_ratio: float, dpr_ratio: float, ac_delta: int, control_score: int, target_tier: str) -> str:
    pressure = max(hp_ratio, dpr_ratio)
    if ac_delta >= 5:
        pressure += 0.4
    elif ac_delta >= 3:
        pressure += 0.2
    if control_score >= 8:
        pressure += 0.5
    elif control_score >= 4:
        pressure += 0.25
    target_index = TIER_ORDER.index(target_tier) if target_tier in TIER_ORDER else 2
    if pressure >= 2.25:
        return 'overpowered'
    if pressure >= 1.65:
        return TIER_ORDER[min(target_index + 2, len(TIER_ORDER) - 2)]
    if pressure >= 1.25:
        return TIER_ORDER[min(target_index + 1, len(TIER_ORDER) - 2)]
    if pressure <= 0.55:
        return TIER_ORDER[max(0, target_index - 2)]
    if pressure <= 0.8:
        return TIER_ORDER[max(0, target_index - 1)]
    return target_tier


def analyze_creature_balance(
    creature: dict[str, Any],
    *,
    party_level: int = 1,
    party_size: int = 4,
    target_difficulty: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_creature_definition(creature, source=creature.get('source') if isinstance(creature, dict) else None)
    difficulty = target_difficulty if target_difficulty in CHALLENGE_TIERS else normalized['challengeTier']
    expected_hp = expected_hp_for_level(party_level, difficulty)
    expected_dpr = expected_dpr_for_level(party_level, party_size, difficulty)
    expected_ac = expected_ac_for_level(party_level, difficulty)
    stats = normalized['stats']
    dpr = estimate_average_damage_per_round(normalized)
    control_score = estimate_control_strength(normalized)
    hp_ratio = stats['maxHp'] / max(1, expected_hp)
    dpr_ratio = dpr / max(1, expected_dpr)
    ac_delta = stats['armorClass'] - expected_ac
    warnings = []
    if hp_ratio > 1.45:
        warnings.append('HP is high for the requested party level and difficulty.')
    if dpr_ratio > 1.45:
        warnings.append('Damage per round is high for the requested party level and difficulty.')
    if ac_delta >= 4:
        warnings.append('Armor Class is high for the requested party level and difficulty.')
    if control_score >= 6:
        warnings.append('Creature has strong control effects.')
    if normalized['movement'].get('fly') and party_level <= 3:
        warnings.append('Flight can be encounter-warping at low levels.')
    if len(normalized.get('immunities') or []) >= 2 and normalized['challengeTier'] != 'boss':
        warnings.append('Multiple immunities on a non-boss creature may overconstrain players.')
    if normalized['challengeTier'] != 'boss':
        if any(str(ability.get('type')) in {'legendary', 'lair'} for ability in normalized.get('abilities') or [] if isinstance(ability, dict)):
            warnings.append('Boss mechanics appear on a non-boss creature.')
    for ability in normalized.get('abilities') or []:
        description = str((ability or {}).get('description') or '').lower()
        if 'instant death' in description or 'instantly dies' in description or 'kill outright' in description:
            warnings.append('Instant-death language must be replaced with bounded damage or a save-based effect.')
            break
    return {
        'estimatedTier': classify_creature_tier(
            hp_ratio=hp_ratio,
            dpr_ratio=dpr_ratio,
            ac_delta=ac_delta,
            control_score=control_score,
            target_tier=difficulty,
        ),
        'targetTier': difficulty,
        'estimatedDamagePerRound': dpr,
        'estimatedDurability': stats['maxHp'],
        'estimatedControlStrength': control_score,
        'warnings': warnings,
        'balanceAdjustments': [],
        'reviewed': False,
        'expected': {
            'hp': expected_hp,
            'damagePerRound': expected_dpr,
            'armorClass': expected_ac,
        },
    }


def _scale_damage_dice(dice: str, ratio: float) -> str:
    normalized = normalize_damage_dice_expression(dice)
    match = re.search(r'(\d*)d(\d+)(.*)', normalized or '')
    if not match:
        return '1d4'
    count = max(1, min(MAX_DAMAGE_DICE_COUNT, int(match.group(1) or 1)))
    sides = max(4, min(MAX_DAMAGE_DIE_SIDES, int(match.group(2))))
    suffix = match.group(3) or ''
    new_count = max(1, math.floor(count * ratio))
    if ratio < 0.75 and sides > 6:
        sides = max(4, sides // 2)
    return f'{new_count}d{sides}{suffix}'


def auto_scale_creature(
    creature: dict[str, Any],
    balance: dict[str, Any] | None = None,
    *,
    target_difficulty: str | None = None,
    party_level: int = 1,
    party_size: int = 4,
) -> dict[str, Any]:
    scaled = normalize_creature_definition(creature, source=creature.get('source') if isinstance(creature, dict) else None)
    difficulty = target_difficulty if target_difficulty in CHALLENGE_TIERS else scaled['challengeTier']
    analysis = balance or analyze_creature_balance(scaled, party_level=party_level, party_size=party_size, target_difficulty=difficulty)
    adjustments = list(analysis.get('balanceAdjustments') or [])
    expected_hp = int((analysis.get('expected') or {}).get('hp') or expected_hp_for_level(party_level, difficulty))
    expected_dpr = int((analysis.get('expected') or {}).get('damagePerRound') or expected_dpr_for_level(party_level, party_size, difficulty))
    expected_ac = int((analysis.get('expected') or {}).get('armorClass') or expected_ac_for_level(party_level, difficulty))

    max_hp_ceiling = round(expected_hp * 1.25)
    if scaled['stats']['maxHp'] > max_hp_ceiling and difficulty != 'boss':
        adjustments.append(f"Reduced HP from {scaled['stats']['maxHp']} to {max_hp_ceiling}.")
        scaled['stats']['maxHp'] = max_hp_ceiling

    ac_ceiling = expected_ac + (3 if difficulty == 'boss' else 2)
    if scaled['stats']['armorClass'] > ac_ceiling:
        adjustments.append(f"Reduced AC from {scaled['stats']['armorClass']} to {ac_ceiling}.")
        scaled['stats']['armorClass'] = ac_ceiling

    current_dpr = estimate_average_damage_per_round(scaled)
    if current_dpr > expected_dpr * 1.35:
        ratio = max(0.35, (expected_dpr * 1.1) / max(1, current_dpr))
        for ability in scaled.get('abilities') or []:
            if not isinstance(ability, dict):
                continue
            damage = ability.get('damage') if isinstance(ability.get('damage'), dict) else None
            if not damage or not damage.get('dice'):
                continue
            old = damage['dice']
            damage['dice'] = _scale_damage_dice(str(old), ratio)
            if ability.get('attackBonus') is not None:
                ability['attackBonus'] = max(-2, min(int_or_default(ability.get('attackBonus'), default=0), 2 + party_level // 2 + 3))
            adjustments.append(f"Reduced {ability.get('name') or 'ability'} damage from {old} to {damage['dice']}.")

    if analysis.get('estimatedControlStrength', 0) >= 6:
        for ability in scaled.get('abilities') or []:
            if not isinstance(ability, dict):
                continue
            conditions = [str(item).strip().lower() for item in ability.get('conditionsApplied') or []]
            replaced = ['frightened' if item in DISABLING_CONDITIONS else item for item in conditions]
            if replaced != conditions:
                ability['conditionsApplied'] = replaced
                ability.setdefault('save', {'ability': 'wis', 'dc': min(12 + party_level // 2, 18), 'effectOnSuccess': 'none'})
                ability['cooldown'] = 'once_per_combat'
                adjustments.append(f"Softened hard control on {ability.get('name') or 'ability'} and added a save/cooldown.")

    if len(scaled.get('immunities') or []) >= 2 and difficulty != 'boss':
        moved = scaled['immunities'][1:]
        scaled['immunities'] = scaled['immunities'][:1]
        scaled['resistances'] = sorted(set([*(scaled.get('resistances') or []), *moved]))
        adjustments.append('Downgraded excess immunities to resistances.')

    if difficulty != 'boss' and len(scaled.get('abilities') or []) > 3:
        kept = []
        for ability in scaled.get('abilities') or []:
            if isinstance(ability, dict) and ability.get('type') in {'legendary', 'lair'}:
                adjustments.append(f"Removed boss-only ability {ability.get('name') or ability.get('id')}.")
                continue
            kept.append(ability)
            if len(kept) >= 3:
                break
        if len(kept) < len(scaled.get('abilities') or []):
            adjustments.append('Trimmed non-boss creature to three core abilities.')
            scaled['abilities'] = kept

    scaled_balance = analyze_creature_balance(scaled, party_level=party_level, party_size=party_size, target_difficulty=difficulty)
    scaled_balance['balanceAdjustments'] = adjustments
    scaled['balance'] = scaled_balance
    return scaled
