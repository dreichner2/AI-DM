"""Bounded parsing for creature and combat damage dice expressions."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable


MAX_DAMAGE_DICE_COUNT = 32
MAX_DAMAGE_DIE_SIDES = 100
MAX_DAMAGE_BONUS_ABS = 1000
MAX_DAMAGE_DICE_EXPRESSION_LENGTH = 24

DAMAGE_DICE_PATTERN = re.compile(r'^(?:(?P<count>\d{0,2})d(?P<sides>\d{1,3}))?(?P<bonus>[+-]\d{1,4})?$')
FLAT_DAMAGE_PATTERN = re.compile(r'^[+-]?\d{1,4}$')


def _format_bonus(bonus: int) -> str:
    if bonus > 0:
        return f'+{bonus}'
    if bonus < 0:
        return str(bonus)
    return ''


def parse_damage_dice_expression(value: Any) -> dict[str, int | str] | None:
    text = str(value or '').strip().lower().replace(' ', '')
    if not text or len(text) > MAX_DAMAGE_DICE_EXPRESSION_LENGTH:
        return None

    if FLAT_DAMAGE_PATTERN.fullmatch(text):
        bonus = int(text)
        if abs(bonus) > MAX_DAMAGE_BONUS_ABS:
            return None
        return {'dice': str(bonus), 'count': 0, 'sides': 0, 'bonus': bonus}

    match = DAMAGE_DICE_PATTERN.fullmatch(text)
    if not match or not match.group('sides'):
        return None

    count = int(match.group('count') or 1)
    sides = int(match.group('sides'))
    bonus = int(match.group('bonus') or 0)
    if (
        count < 1
        or count > MAX_DAMAGE_DICE_COUNT
        or sides < 1
        or sides > MAX_DAMAGE_DIE_SIDES
        or abs(bonus) > MAX_DAMAGE_BONUS_ABS
    ):
        return None

    return {
        'dice': f'{count}d{sides}{_format_bonus(bonus)}',
        'count': count,
        'sides': sides,
        'bonus': bonus,
    }


def normalize_damage_dice_expression(value: Any) -> str | None:
    parsed = parse_damage_dice_expression(value)
    if not parsed:
        return None
    return str(parsed['dice'])


def deterministic_roller(seed: str) -> Callable[[int], int]:
    """Return the engine's reproducible roller: roll n of a die hashes (seed, n, sides)."""

    counter = 0

    def roll(sides: int) -> int:
        nonlocal counter
        counter += 1
        digest = hashlib.sha256(f'{seed}:{counter}:{sides}'.encode('utf-8')).digest()
        return int.from_bytes(digest[:4], 'big') % max(1, int(sides)) + 1

    return roll


def parse_signed_dice(value: Any) -> dict[str, Any] | None:
    """Parse a bonus-dice string such as ``+1d4`` or ``-1d4`` (sign defaults to plus)."""

    text = str(value or '').strip().lower().replace(' ', '')
    sign = -1 if text.startswith('-') else 1
    parsed = parse_damage_dice_expression(text.lstrip('+-'))
    if not parsed:
        return None
    return {**parsed, 'sign': sign, 'dice': f"{'-' if sign < 0 else '+'}{parsed['dice']}"}


def roll_signed_dice(value: Any, roller: Callable[[int], int]) -> dict[str, Any] | None:
    parsed = parse_signed_dice(value)
    if not parsed:
        return None
    sides = int(parsed['sides'])
    rolls = [max(1, min(sides, int(roller(sides)))) for _ in range(int(parsed['count']))] if sides else []
    return {
        'dice': parsed['dice'],
        'rolls': rolls,
        'total': int(parsed['sign']) * (sum(rolls) + int(parsed['bonus'])),
    }
