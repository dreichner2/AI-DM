"""Parse legacy, text-only player roll result claims.

The parsed numbers are presentation data only. Callers use a match as a
submission signal and must replace every claimed outcome with a server roll.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


_DIE_PATTERN = r'd(?:100|20|12|10|8|6|4)'
_TRAILING_TOTAL_PATTERN = (
    r'(?:\s*(?:(?P<post_modifier_sign>[+-])\s*(?P<post_modifier_value>\d{1,3}))?'
    r'\s*=\s*(?P<total>[+-]?\s*\d{1,4})\b)?'
)

_CLAIM_PATTERNS = (
    re.compile(
        rf'\b(?:(?:i|we)\s+)?roll(?:ed|ing)?\s+(?:a\s+)?'
        rf'(?P<die>{_DIE_PATTERN})\b'
        r'\s*(?:(?P<modifier_sign>[+-])\s*(?P<modifier_value>\d{1,3}))?'
        r'(?:\s+with\s+(?:advantage|disadvantage))?'
        r'(?:\s+for\s+(?P<reason>[^:;.!?\n]{1,240}?))?'
        r'(?:\s*(?:=|:)\s*|\s+is\s+|\s+)'
        r'(?P<face>\d{1,4})\b'
        + _TRAILING_TOTAL_PATTERN,
        re.IGNORECASE,
    ),
    re.compile(
        rf'\b(?P<label>initiative|check|{_DIE_PATTERN})\b'
        r'\s*(?:(?P<modifier_sign>[+-])\s*(?P<modifier_value>\d{1,3}))?'
        r'\s*(?:=|:|\bis\b)\s*'
        r'(?P<face>\d{1,4})\b'
        + _TRAILING_TOTAL_PATTERN,
        re.IGNORECASE,
    ),
    re.compile(
        r'\b(?:(?:(?:i|we)\s+)?roll(?:ed|ing)?\s+(?:a\s+)?)?'
        r'(?:(?:with\s+)?(?:a\s+)?)?(?:natural|nat)\s+(?P<face>20|1)\b'
        + _TRAILING_TOTAL_PATTERN,
        re.IGNORECASE,
    ),
    re.compile(
        r'\b(?:(?:i|we)\s+)?roll(?:ed|ing)?'
        r'(?:\s+(?:a\s+)?|\s*(?:=|:)\s*|\s+is\s+)'
        r'(?P<face>\d{1,4})\b'
        + _TRAILING_TOTAL_PATTERN,
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class LegacyRollClaim:
    """One syntactic result claim and its location in the original text."""

    start: int
    end: int
    die: str
    face: int
    modifier: int | None
    total: int | None
    reason: str | None


def _signed_value(sign: str | None, value: str | None) -> int | None:
    if not value:
        return None
    parsed = int(value)
    return -parsed if sign == '-' else parsed


def _integer(value: str | None) -> int | None:
    if not value:
        return None
    return int(value.replace(' ', ''))


def find_legacy_roll_claim(message: str) -> LegacyRollClaim | None:
    """Find the earliest supported result claim in legacy player text.

    Explicit dice cover d4, d6, d8, d10, d12, d20, and d100. A face outside
    the named die's range still counts as a claim so malformed or dishonest
    client text cannot bypass server-authoritative rolling.
    """

    text = str(message or '')
    candidates = []
    for priority, pattern in enumerate(_CLAIM_PATTERNS):
        match = pattern.search(text)
        if match is not None:
            candidates.append((match.start(), priority, match))
    if not candidates:
        return None

    _, _, match = min(candidates, key=lambda candidate: (candidate[0], candidate[1]))
    groups = match.groupdict()
    label = str(groups.get('die') or groups.get('label') or '').lower()
    die = label if label.startswith('d') else 'd20'
    modifier = _signed_value(groups.get('modifier_sign'), groups.get('modifier_value'))
    if modifier is None:
        modifier = _signed_value(groups.get('post_modifier_sign'), groups.get('post_modifier_value'))

    return LegacyRollClaim(
        start=match.start(),
        end=match.end(),
        die=die,
        face=int(groups['face']),
        modifier=modifier,
        total=_integer(groups.get('total')),
        reason=str(groups.get('reason') or '').strip() or None,
    )
