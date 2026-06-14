from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EXAMPLE_PACKS_DIR = Path(__file__).resolve().parents[2] / 'docs' / 'examples'
EXAMPLE_PACK_GLOB = '*.json'
SHORT_DESCRIPTION_LENGTH = 180
SESSION_HOURS_MIN = 3
SESSION_HOURS_MAX = 4


def list_example_campaign_pack_summaries() -> list[dict[str, Any]]:
    return [
        _summary_payload(entry)
        for entry in _example_campaign_packs()
        if _player_visible_manifest(entry['manifest'])
    ]


def get_example_campaign_pack(pack_id: str) -> dict[str, Any] | None:
    normalized_pack_id = str(pack_id or '').strip()
    if not normalized_pack_id:
        return None
    for entry in _example_campaign_packs():
        if entry['pack_id'] == normalized_pack_id:
            return entry
    return None


def _example_campaign_packs() -> tuple[dict[str, Any], ...]:
    entries: list[dict[str, Any]] = []
    for pack_path in sorted(EXAMPLE_PACKS_DIR.glob(EXAMPLE_PACK_GLOB)):
        try:
            with pack_path.open('r', encoding='utf-8') as pack_file:
                manifest = json.load(pack_file)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue

        pack_id = _text(_first(manifest, 'packId', 'pack_id'))
        title = _text(_first(manifest, 'title', 'name'))
        if not pack_id or not title:
            continue
        description = _text(_first(manifest, 'description', 'summary'))
        world = _record(_first(manifest, 'world', 'worldSettings', 'world_settings'))
        length_estimate = _length_estimate(manifest)
        entries.append(
            {
                'pack_id': pack_id,
                'title': title,
                'description': description,
                'short_description': _short_description(description),
                'version': _text(_first(manifest, 'version')),
                'schema_version': _text(_first(manifest, 'schemaVersion', 'schema_version')) or '1',
                'source_filename': pack_path.name,
                'world_name': _text(_first(world, 'name', 'title')),
                'length_estimate': length_estimate,
                'manifest': manifest,
            }
        )
    return tuple(entries)


def _summary_payload(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        'pack_id': entry['pack_id'],
        'title': entry['title'],
        'description': entry['description'],
        'short_description': entry['short_description'],
        'version': entry['version'],
        'schema_version': entry['schema_version'],
        'source_filename': entry['source_filename'],
        'world_name': entry['world_name'],
        'length_estimate': entry['length_estimate'],
        'source': 'bundled_example',
    }


def _player_visible_manifest(manifest: dict[str, Any]) -> bool:
    metadata = _record(_first(manifest, 'metadata'))
    visibility = _text(
        _first(manifest, 'visibility', 'catalogVisibility', 'catalog_visibility')
        or _first(metadata, 'visibility', 'catalogVisibility', 'catalog_visibility')
    ).lower()
    if visibility in {'hidden', 'internal', 'private', 'test'}:
        return False

    hidden = _first(manifest, 'hidden', 'hiddenToPlayers', 'hidden_to_players')
    if hidden is None:
        hidden = _first(metadata, 'hidden', 'hiddenToPlayers', 'hidden_to_players')
    if _truthy(hidden):
        return False

    test_pack = _first(manifest, 'testPack', 'test_pack')
    if test_pack is None:
        test_pack = _first(metadata, 'testPack', 'test_pack')
    if _truthy(test_pack):
        return False

    player_visible = _first(manifest, 'playerVisible', 'player_visible')
    if player_visible is None:
        player_visible = _first(metadata, 'playerVisible', 'player_visible')
    if player_visible is not None and not _truthy(player_visible):
        return False

    return True


def _length_estimate(manifest: dict[str, Any]) -> dict[str, Any]:
    metadata = _record(_first(manifest, 'metadata'))
    raw = _record(
        _first(
            manifest,
            'lengthEstimate',
            'length_estimate',
            'estimatedLength',
            'estimated_length',
            'duration',
        )
    ) or _record(
        _first(
            metadata,
            'lengthEstimate',
            'length_estimate',
            'estimatedLength',
            'estimated_length',
            'duration',
        )
    )
    checkpoint_count = len(_records(_first(manifest, 'checkpoints')))
    encounter_count = len(_records(_first(manifest, 'encounters')))
    fallback = _fallback_length_estimate(checkpoint_count, encounter_count)
    sessions_min = _positive_int(
        _first(raw, 'sessionsMin', 'sessions_min', 'minSessions', 'min_sessions', 'sessions')
    ) or fallback['sessions_min']
    sessions_max = _positive_int(
        _first(raw, 'sessionsMax', 'sessions_max', 'maxSessions', 'max_sessions', 'sessions')
    ) or fallback['sessions_max']
    hours_min = _positive_int(_first(raw, 'hoursMin', 'hours_min', 'minHours', 'min_hours', 'hours')) or fallback[
        'hours_min'
    ]
    hours_max = _positive_int(_first(raw, 'hoursMax', 'hours_max', 'maxHours', 'max_hours', 'hours')) or fallback[
        'hours_max'
    ]
    if sessions_max < sessions_min:
        sessions_max = sessions_min
    if hours_max < hours_min:
        hours_max = hours_min
    label = _text(_first(raw, 'label', 'name', 'title')) or fallback['label']
    pacing = _text(_first(raw, 'pacing', 'notes', 'description', 'summary')) or fallback['pacing']
    return {
        'label': label,
        'sessions_min': sessions_min,
        'sessions_max': sessions_max,
        'hours_min': hours_min,
        'hours_max': hours_max,
        'checkpoint_count': checkpoint_count,
        'encounter_count': encounter_count,
        'pacing': pacing,
    }


def _fallback_length_estimate(checkpoint_count: int, encounter_count: int) -> dict[str, Any]:
    if checkpoint_count <= 3:
        label = 'Short campaign'
        sessions_min, sessions_max = 1, 2
    elif checkpoint_count <= 6:
        label = 'Medium campaign'
        sessions_min, sessions_max = 3, 5
    elif checkpoint_count <= 8:
        label = 'Medium-long campaign'
        sessions_min, sessions_max = 4, 7
    else:
        label = 'Long campaign'
        sessions_min, sessions_max = 6, 10
    return {
        'label': label,
        'sessions_min': sessions_min,
        'sessions_max': sessions_max,
        'hours_min': sessions_min * SESSION_HOURS_MIN,
        'hours_max': sessions_max * SESSION_HOURS_MAX,
        'pacing': (
            f'Estimated from {checkpoint_count} {_plural("checkpoint", checkpoint_count)} '
            f'and {encounter_count} authored {_plural("encounter", encounter_count)}.'
        ),
    }


def _short_description(description: str) -> str:
    cleaned = ' '.join(str(description or '').split())
    if len(cleaned) <= SHORT_DESCRIPTION_LENGTH:
        return cleaned
    clipped = cleaned[:SHORT_DESCRIPTION_LENGTH].rsplit(' ', 1)[0].rstrip('.,;:')
    return f'{clipped}...'


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record.get(key) not in (None, ''):
            return record.get(key)
    return None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ''


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'y', 'on'}
    return False


def _plural(noun: str, count: int) -> str:
    return noun if count == 1 else f'{noun}s'
