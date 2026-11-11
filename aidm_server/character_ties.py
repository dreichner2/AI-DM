"""A hero's own threads: what they want, who or what they would not abandon, and what they carry.

Stored on the character sheet as ``ties``::

    {"desire": {"kind", "words"}, "attachment": {"name", "relation"}, "burden": "…"}

Every part is optional. ``desire.kind`` is one of :data:`DESIRES` (``words`` is then its phrase) or
``own`` with the player's own line. An attachment whose relation is not :data:`NOT_PEOPLE` is a person.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from aidm_server.models import safe_json_loads

TIES_KEY = 'ties'
DESIRES = {
    'find_someone': 'to find someone',
    'be_recognised': 'to be recognised',
    'clear_debt': 'to clear a debt',
    'keep_duty': 'to keep a duty',
    'learn_forbidden': 'to learn what is forbidden',
    'prove_wrong': 'to prove someone wrong',
}
OWN_DESIRE = 'own'
PEOPLE = (
    'sister',
    'brother',
    'mother',
    'father',
    'daughter',
    'son',
    'friend',
    'mentor',
    'old comrade',
    'the one who wronged you',
)
NOT_PEOPLE = ('a place', 'an order', 'a keepsake')
RELATIONS = (*PEOPLE, *NOT_PEOPLE)
DEFAULT_RELATION = 'friend'
NAME_LENGTH = (2, 40)
OWN_DESIRE_MAX = 80
BURDEN_MAX = 120


class TiesValidationError(ValueError):
    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


def _line(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _desire(value: Any) -> dict[str, str] | None:
    if value in (None, '', {}):
        return None
    if not isinstance(value, dict):
        raise TiesValidationError('What you want must be one of the choices or a line of your own.')
    kind = _line(value.get('kind')).lower()
    if kind in DESIRES:
        return {'kind': kind, 'words': DESIRES[kind]}
    words = _line(value.get('words'))
    if kind != OWN_DESIRE or not words:
        raise TiesValidationError('What you want must be one of the choices or a line of your own.')
    if len(words) > OWN_DESIRE_MAX:
        raise TiesValidationError(f'What you want must be at most {OWN_DESIRE_MAX} characters.')
    return {'kind': OWN_DESIRE, 'words': words}


def _attachment(value: Any) -> dict[str, str] | None:
    if value in (None, '', {}):
        return None
    if not isinstance(value, dict):
        raise TiesValidationError('Who or what you would not abandon needs a name and a relation.')
    name = _line(value.get('name'))
    if not name:
        return None
    if not NAME_LENGTH[0] <= len(name) <= NAME_LENGTH[1]:
        raise TiesValidationError(f'The name must be {NAME_LENGTH[0]} to {NAME_LENGTH[1]} characters.')
    relation = _line(value.get('relation')).lower() or DEFAULT_RELATION
    if relation not in RELATIONS:
        raise TiesValidationError('Choose the relation from the list.')
    return {'name': name, 'relation': relation}


def _burden(value: Any) -> str | None:
    if value is not None and not isinstance(value, str):
        raise TiesValidationError('What you carry must be one sentence.')
    burden = _line(value)
    if len(burden) > BURDEN_MAX:
        raise TiesValidationError(f'What you carry must be at most {BURDEN_MAX} characters.')
    return burden or None


def normalize_character_ties(value: Any) -> dict[str, Any]:
    """The ties a player sent, checked and tidied; ``{}`` when there are none."""

    if value in (None, ''):
        return {}
    if not isinstance(value, dict):
        raise TiesValidationError('ties must be an object.')
    ties: dict[str, Any] = {}
    desire = _desire(value.get('desire'))
    attachment = _attachment(value.get('attachment'))
    burden = _burden(value.get('burden'))
    if desire:
        ties['desire'] = desire
    if attachment:
        ties['attachment'] = attachment
    if burden:
        ties['burden'] = burden
    return ties


def _sheet(raw_sheet: Any) -> dict[str, Any]:
    if isinstance(raw_sheet, dict):
        return raw_sheet
    loaded = safe_json_loads(raw_sheet, {})
    return loaded if isinstance(loaded, dict) else {}


def ties_from_character_sheet(raw_sheet: Any) -> dict[str, Any]:
    """The sheet's ties as stored (already normalised); ``{}`` for a sheet without them."""

    try:
        return normalize_character_ties(_sheet(raw_sheet).get(TIES_KEY))
    except TiesValidationError:
        return {}


def character_sheet_with_ties(raw_sheet: Any, ties: dict[str, Any]) -> dict[str, Any]:
    """A copy of the sheet carrying ``ties`` (none: the key is removed)."""

    sheet = deepcopy(_sheet(raw_sheet))
    if ties:
        sheet[TIES_KEY] = deepcopy(ties)
    else:
        sheet.pop(TIES_KEY, None)
    return sheet


def attachment_is_person(ties: dict[str, Any]) -> bool:
    attachment = ties.get('attachment') if isinstance(ties.get('attachment'), dict) else {}
    return bool(_line(attachment.get('name'))) and _line(attachment.get('relation')).lower() not in NOT_PEOPLE
