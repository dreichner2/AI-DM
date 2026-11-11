"""Location inference helpers for emergent canon projection.

Narration is never a place: a line moves the party only when the party or one
of its characters is the subject of a movement verb, and the place it names is
a location the session already knows or the campaign catalog defines.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


PARTY_SUBJECTS = ('you', 'we', 'i', 'the party', 'your party', 'the group', 'the company')
MOVEMENT_VERBS = (
    r'enter(?:s|ed)?|arrive(?:s|d)? (?:at|in)|reach(?:es|ed)?|head(?:s|ed)? (?:to|into)|go(?:es)? to|went to|'
    r'travel(?:s|led|ed)? to|move(?:s|d)? (?:to|into)|make(?:s)? (?:your|their|our|his|her) way to|'
    r'made (?:your|their|our|his|her) way to|return(?:s|ed)? to|sprint(?:s|ed)? (?:for|to)|run(?:s)? (?:for|to)|'
    r'ran (?:for|to)|escape(?:s|d)? (?:to|through)|slip(?:s|ped)? into|step(?:s|ped)? into|burst(?:s)? into|'
    r'leap(?:s|t|ed)? into|jump(?:s|ed)? into|vault(?:s|ed)? onto|climb(?:s|ed)? (?:into|to)'
)
SUBJECT_ADVERBS = r'(?:(?:then|finally|quickly|carefully|quietly|slowly|at last|soon)\s+)?'


def normalize_location_candidate(raw_text: str | None) -> str | None:
    text = (raw_text or '').strip(" \t\r\n'\"`")
    if not text:
        return None

    lower = text.lower()
    split_tokens = [
        ' and ',
        ' while ',
        ' before ',
        ' after ',
        ' as ',
        ' with ',
        ' but ',
        ' where ',
        ' to ',
        ' through ',
        ' across ',
        ' above ',
        ' unhindered',
    ]
    cut = len(text)
    for token in split_tokens:
        idx = lower.find(token)
        if idx != -1 and idx < cut:
            cut = idx

    cleaned = text[:cut].strip(' -')
    words = cleaned.split()
    trailing_noise = {
        'quietly',
        'carefully',
        'quickly',
        'silently',
        'safely',
        'unseen',
        'unhindered',
        'immediately',
    }
    while words and words[-1].lower().strip('.,;:!?') in trailing_noise:
        words.pop()
    if not words:
        return None
    return ' '.join(words[:8])


def _location_key(value: Any) -> str:
    text = re.sub(r"['’]", '', str(value or '').lower().replace('_', ' '))
    words = re.findall(r'[a-z0-9]+', text)
    if words and words[0] == 'the':
        words = words[1:]
    return ' '.join(words)


def _location_keys(location: Mapping[str, Any]) -> set[str]:
    aliases = location.get('aliases') if isinstance(location.get('aliases'), list) else []
    return {
        key
        for key in (_location_key(value) for value in (location.get('id'), location.get('name'), *aliases))
        if key
    }


def _movement_pattern(subjects: Iterable[str]) -> re.Pattern[str]:
    names = sorted({str(name).strip() for name in subjects if str(name or '').strip()}, key=len, reverse=True)
    subject = '|'.join([*(re.escape(name) for name in names), *PARTY_SUBJECTS])
    return re.compile(
        rf'\b(?:{subject})\s+{SUBJECT_ADVERBS}(?:{MOVEMENT_VERBS})\s+([^.,;!?]+)',
        re.IGNORECASE,
    )


def known_location_name(candidate: str | None, locations: Iterable[Mapping[str, Any]]) -> str | None:
    """The name of the location ``candidate`` names by id, name or alias.

    A candidate that runs on past a known name ("the Old Mill's yard") names
    the longest known place it starts with.
    """

    key = _location_key(candidate)
    if not key:
        return None
    best: tuple[int, str] | None = None
    for location in locations:
        if not isinstance(location, Mapping):
            continue
        name = str(location.get('name') or location.get('id') or '').strip()
        for location_key in _location_keys(location):
            if key == location_key or key.startswith(f'{location_key} '):
                if best is None or len(location_key) > best[0]:
                    best = (len(location_key), name)
    return best[1] if best else None


def infer_location_update(
    player_input: str,
    dm_output: str | None,
    *,
    locations: Iterable[Mapping[str, Any]],
    subjects: Iterable[str] = (),
) -> str | None:
    """The known place the party (or a named character) moves to, or None."""

    known = [location for location in locations if isinstance(location, Mapping)]
    if not known:
        return None
    pattern = _movement_pattern(subjects)
    for source in (player_input or '', dm_output or ''):
        for match in pattern.finditer(source):
            location_name = known_location_name(normalize_location_candidate(match.group(1)), known)
            if location_name:
                return location_name
    return None
