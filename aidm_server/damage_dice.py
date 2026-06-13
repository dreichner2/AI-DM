"""Bounded parsing for creature and combat damage dice expressions."""

from __future__ import annotations

import re
from typing import Any


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
