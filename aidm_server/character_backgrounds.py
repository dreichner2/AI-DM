"""Small authoritative background catalog and legacy-safe normalization."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from aidm_server.models import safe_json_loads


BACKGROUND_SCHEMA_VERSION = 1


class BackgroundValidationError(ValueError):
    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


BACKGROUND_CATALOG: tuple[dict[str, Any], ...] = (
    {
        'id': 'acolyte',
        'name': 'Acolyte',
        'skillProficiencies': ['insight', 'religion'],
        'toolProficiencies': ['herbalism_kit'],
        'languages': ['Celestial', 'Infernal'],
    },
    {
        'id': 'criminal',
        'name': 'Criminal',
        'skillProficiencies': ['deception', 'stealth'],
        'toolProficiencies': ['thieves_tools', 'gaming_set'],
        'languages': ["Thieves' Cant"],
    },
    {
        'id': 'folk_hero',
        'name': 'Folk Hero',
        'skillProficiencies': ['animal_handling', 'survival'],
        'toolProficiencies': ['artisan_tools', 'land_vehicles'],
        'languages': [],
    },
    {
        'id': 'guild_artisan',
        'name': 'Guild Artisan',
        'skillProficiencies': ['insight', 'persuasion'],
        'toolProficiencies': ['artisan_tools'],
        'languages': ['Dwarvish'],
    },
    {
        'id': 'sage',
        'name': 'Sage',
        'skillProficiencies': ['arcana', 'history'],
        'toolProficiencies': ['calligraphers_supplies'],
        'languages': ['Draconic', 'Elvish'],
    },
    {
        'id': 'soldier',
        'name': 'Soldier',
        'skillProficiencies': ['athletics', 'intimidation'],
        'toolProficiencies': ['gaming_set', 'land_vehicles'],
        'languages': [],
    },
)


def _key(value: Any) -> str:
    text = str(value or '').strip().lower()
    text = re.sub(r"['’]", '', text)
    return re.sub(r'[^a-z0-9]+', '_', text).strip('_')


def _catalog_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        'schemaVersion': BACKGROUND_SCHEMA_VERSION,
        'id': record['id'],
        'name': record['name'],
        'source': 'catalog',
        'skillProficiencies': list(record['skillProficiencies']),
        'toolProficiencies': list(record['toolProficiencies']),
        'languages': list(record['languages']),
    }


def background_catalog() -> list[dict[str, Any]]:
    return [_catalog_record(record) for record in BACKGROUND_CATALOG]


def _find_catalog_background(value: Any) -> dict[str, Any] | None:
    lookup = _key(value)
    if not lookup:
        return None
    for record in BACKGROUND_CATALOG:
        if lookup in {_key(record['id']), _key(record['name'])}:
            return _catalog_record(record)
    return None


def normalize_character_background(
    raw_background: Any,
    *,
    allow_legacy: bool = False,
) -> dict[str, Any] | None:
    if raw_background in (None, ''):
        return None
    if isinstance(raw_background, dict):
        catalog_match = _find_catalog_background(
            raw_background.get('id')
            or raw_background.get('backgroundId')
            or raw_background.get('background_id')
            or raw_background.get('name')
        )
        legacy_name = str(raw_background.get('name') or raw_background.get('id') or '').strip()
    else:
        catalog_match = _find_catalog_background(raw_background)
        legacy_name = str(raw_background).strip()
    if catalog_match:
        return catalog_match
    if not allow_legacy:
        raise BackgroundValidationError(
            'background must be one of: '
            + ', '.join(record['name'] for record in BACKGROUND_CATALOG)
            + '.',
        )
    if not legacy_name:
        return None
    # Legacy free text remains visible, but cannot grant arbitrary mechanics.
    return {
        'schemaVersion': BACKGROUND_SCHEMA_VERSION,
        'id': _key(legacy_name) or 'legacy_background',
        'name': legacy_name[:80],
        'source': 'legacy',
        'skillProficiencies': [],
        'toolProficiencies': [],
        'languages': [],
    }


def background_from_character_sheet(raw_sheet: Any) -> dict[str, Any] | None:
    if isinstance(raw_sheet, dict):
        sheet = raw_sheet
    else:
        loaded = safe_json_loads(raw_sheet, {})
        sheet = loaded if isinstance(loaded, dict) else {}
    return normalize_character_background(sheet.get('background'), allow_legacy=True)


def character_sheet_with_background(
    raw_sheet: Any,
    raw_background: Any,
    *,
    background_provided: bool,
) -> dict[str, Any]:
    if isinstance(raw_sheet, dict):
        sheet = deepcopy(raw_sheet)
    else:
        loaded = safe_json_loads(raw_sheet, {})
        if isinstance(loaded, dict):
            sheet = deepcopy(loaded)
        elif isinstance(raw_sheet, str) and raw_sheet.strip():
            sheet = {'notes': raw_sheet.strip()}
        else:
            sheet = {}

    if background_provided:
        normalized = normalize_character_background(raw_background, allow_legacy=False)
        if normalized is None:
            sheet.pop('background', None)
        else:
            sheet['background'] = normalized
    elif 'background' in sheet:
        normalized = normalize_character_background(sheet.get('background'), allow_legacy=True)
        if normalized is None:
            sheet.pop('background', None)
        else:
            sheet['background'] = normalized
    return sheet
