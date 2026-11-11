"""A pack's tie lines (``tieLines``): authored lines that bring a hero's own threads into the adventure.

Each line is ``{"id", "kind", "at", "text"}`` (a ``desire`` line also names ``"desire": "<kind>"``).
``kind`` is ``seeking`` (fires for a hero whose attachment is a person), ``burden`` or ``desire``.
``at`` is one of ``{"objectId", "action"}``, ``{"locationId"}`` (first arrival), ``{"encounterId"}``
(with the encounter's setup), ``{"longRest": true}`` or ``{"ending": "<variant flag>"}`` (a
``seeking`` line appended to that ending's text). ``text`` may hold the tokens the kind can fill
(:data:`TOKENS_BY_KIND`).
"""

from __future__ import annotations

import re
from typing import Any

from aidm_server.character_ties import DESIRES
from aidm_server.game_state.models import stable_slug

TIE_LINES_KEY = 'tieLines'
# The lines fired in this adventure, kept in the session's ``campaignPack``.
THREADS_KEY = 'threads'
TIE_LINE_KINDS = ('seeking', 'burden', 'desire')
TOKENS_BY_KIND = {
    'seeking': ('name', 'relation', 'hero'),
    'burden': ('burden', 'hero'),
    'desire': ('hero',),
}
TOKEN_RE = re.compile(r'\{([A-Za-z_]+)\}')
TEXT_MAX = 600
AT_OBJECT = 'object'
AT_LOCATION = 'location'
AT_ENCOUNTER = 'encounter'
AT_LONG_REST = 'long_rest'
AT_ENDING = 'ending'


def _text(value: Any) -> str:
    return str(value or '').strip()


def tie_line_moment(at: Any) -> str | None:
    """Which moment ``at`` names (``object``, ``location``, ``encounter``, ``long_rest``, ``ending``); None if none."""

    if not isinstance(at, dict):
        return None
    if _text(at.get('objectId')) and _text(at.get('action')):
        return AT_OBJECT
    if _text(at.get('locationId')):
        return AT_LOCATION
    if _text(at.get('encounterId')):
        return AT_ENCOUNTER
    if at.get('longRest') is True:
        return AT_LONG_REST
    if _text(at.get('ending')):
        return AT_ENDING
    return None


def _at(at: dict[str, Any], moment: str) -> dict[str, Any]:
    if moment == AT_OBJECT:
        return {'objectId': stable_slug(_text(at['objectId'])), 'action': _text(at['action']).lower()}
    if moment == AT_LOCATION:
        return {'locationId': stable_slug(_text(at['locationId']))}
    if moment == AT_ENCOUNTER:
        return {'encounterId': stable_slug(_text(at['encounterId']))}
    if moment == AT_LONG_REST:
        return {'longRest': True}
    return {'ending': _text(at['ending'])}


def normalize_tie_lines(value: Any) -> list[dict[str, Any]]:
    """The pack's well-formed tie lines in runtime shape (``moment`` added); the linter reports the rest."""

    lines: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        line_id = stable_slug(_text(raw.get('id')))
        kind = _text(raw.get('kind')).lower()
        moment = tie_line_moment(raw.get('at'))
        text = _text(raw.get('text'))[:TEXT_MAX]
        if not line_id or line_id in seen or kind not in TIE_LINE_KINDS or moment is None or not text:
            continue
        if kind == 'desire' and _text(raw.get('desire')) not in DESIRES:
            continue
        seen.add(line_id)
        line = {'id': line_id, 'kind': kind, 'at': _at(raw['at'], moment), 'moment': moment, 'text': text}
        if kind == 'desire':
            line['desire'] = _text(raw['desire'])
        lines.append(line)
    return lines


def fill_tie_line(text: str, values: dict[str, str]) -> str:
    """``text`` with each token replaced by its value; a token without one is left as written."""

    return TOKEN_RE.sub(lambda match: values.get(match.group(1), match.group(0)), text)


def variant_flag_keys(variant: Any) -> set[str]:
    """The flag keys an ending variant's ``when`` reads (each of an ``anyOf``)."""

    when = variant.get('when') if isinstance(variant, dict) and isinstance(variant.get('when'), dict) else {}
    predicates = when.get('anyOf') if isinstance(when.get('anyOf'), list) else [when]
    return {_text(predicate.get('flagKey')) for predicate in predicates if isinstance(predicate, dict) and _text(predicate.get('flagKey'))}


def ending_flags(checkpoint: dict[str, Any]) -> set[str]:
    """The flag keys a terminal checkpoint's ending variants are chosen by."""

    variants = checkpoint.get('endingVariants') if isinstance(checkpoint.get('endingVariants'), list) else []
    return {flag for variant in variants for flag in variant_flag_keys(variant)}


def tie_line_problems(
    value: Any,
    *,
    object_actions: dict[str, set[str]],
    location_ids: set[str],
    encounter_ids: set[str],
    ending_flag_keys: set[str],
) -> list[tuple[str, str]]:
    """Every (path suffix, message) that makes a tie line an error: unknown ids, an action the object does not
    support, an unknown ending flag or desire, a token the kind cannot fill, a repeated id."""

    problems: list[tuple[str, str]] = []
    if value is None:
        return problems
    if not isinstance(value, list):
        return [('', 'tieLines must be a list of lines.')]
    seen: set[str] = set()
    for index, raw in enumerate(value):
        path = f'[{index}]'
        if not isinstance(raw, dict):
            problems.append((path, 'A tie line must be an object.'))
            continue
        line_id = _text(raw.get('id'))
        if not line_id:
            problems.append((f'{path}.id', 'A tie line needs an id.'))
        elif line_id in seen:
            problems.append((f'{path}.id', f'"{line_id}" is used by another tie line.'))
        seen.add(line_id)
        kind = _text(raw.get('kind')).lower()
        if kind not in TIE_LINE_KINDS:
            problems.append((f'{path}.kind', f'kind must be one of {", ".join(TIE_LINE_KINDS)}.'))
            continue
        if kind == 'desire' and _text(raw.get('desire')) not in DESIRES:
            problems.append((f'{path}.desire', f'A desire line names one of: {", ".join(DESIRES)}.'))
        if not _text(raw.get('text')):
            problems.append((f'{path}.text', 'A tie line needs its text.'))
        for token in TOKEN_RE.findall(_text(raw.get('text'))):
            if token not in TOKENS_BY_KIND[kind]:
                problems.append((f'{path}.text', f'A {kind} line cannot fill {{{token}}}.'))
        at = raw.get('at') if isinstance(raw.get('at'), dict) else {}
        moment = tie_line_moment(at)
        if moment is None:
            problems.append((f'{path}.at', 'at must name an object action, a location, an encounter, a long rest or an ending.'))
        elif moment == AT_OBJECT:
            object_id = _text(at.get('objectId'))
            if object_id not in object_actions:
                problems.append((f'{path}.at.objectId', f'"{object_id}" is not one of the pack\'s objects.'))
            elif _text(at.get('action')).lower() not in object_actions[object_id]:
                problems.append((f'{path}.at.action', f'"{object_id}" does not support "{_text(at.get("action"))}".'))
        elif moment == AT_LOCATION and _text(at.get('locationId')) not in location_ids:
            problems.append((f'{path}.at.locationId', f'"{_text(at.get("locationId"))}" is not one of the pack\'s locations.'))
        elif moment == AT_ENCOUNTER and _text(at.get('encounterId')) not in encounter_ids:
            problems.append((f'{path}.at.encounterId', f'"{_text(at.get("encounterId"))}" is not one of the pack\'s encounters.'))
        elif moment == AT_ENDING:
            if _text(at.get('ending')) not in ending_flag_keys:
                problems.append((f'{path}.at.ending', f'"{_text(at.get("ending"))}" chooses none of the pack\'s ending variants.'))
            if kind != 'seeking':
                problems.append((f'{path}.kind', 'An ending line is told for a hero whose seeking line fired, so its kind is seeking.'))
    return problems


def ending_lines(pack: dict[str, Any], variant: dict[str, Any]) -> list[str]:
    """The ending lines told with this ending variant: one per hero whose seeking line fired, in firing order."""

    flags = variant_flag_keys(variant)
    lines = [
        line
        for line in pack.get(TIE_LINES_KEY) or []
        if isinstance(line, dict) and line.get('moment') == AT_ENDING and _text((line.get('at') or {}).get('ending')) in flags
    ]
    if not lines:
        return []
    told: list[str] = []
    heroes: set[str] = set()
    for record in pack.get(THREADS_KEY) or []:
        hero_id = _text(record.get('heroId')) if isinstance(record, dict) else ''
        if not hero_id or record.get('kind') != 'seeking' or hero_id in heroes:
            continue
        heroes.add(hero_id)
        values = record.get('values') if isinstance(record.get('values'), dict) else {}
        told.extend(fill_tie_line(_text(line.get('text')), {key: _text(value) for key, value in values.items()}) for line in lines)
    return told
