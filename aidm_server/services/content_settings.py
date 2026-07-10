from __future__ import annotations

import re
from typing import Any

from aidm_server.models import Session, safe_json_loads
from aidm_server.services.session_state_mutation import mutate_session_snapshot_metadata
from aidm_server.time_utils import utc_now


CONTENT_RATINGS = ('standard', 'mature', 'unrestricted')
DEFAULT_CONTENT_RATING = 'standard'
ALLOWED_TONE_TAGS = (
    'heroic',
    'hopeful',
    'grim',
    'horror',
    'whimsical',
    'comedic',
    'noir',
    'mystery',
    'political',
    'pulpy',
    'tragic',
    'romantic',
)
MAX_TONE_TAGS = 4
_TONE_TAG_RE = re.compile(r'^[a-z][a-z0-9_-]{1,28}$')


def normalize_content_rating(value: Any) -> str:
    rating = str(value or DEFAULT_CONTENT_RATING).strip().lower()
    if rating in {'pg', 'pg-13', 'teen'}:
        return 'standard'
    if rating in {'adult', 'dark'}:
        return 'mature'
    if rating in CONTENT_RATINGS:
        return rating
    return DEFAULT_CONTENT_RATING


def normalize_tone_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_values = [item.strip() for item in value.replace(';', ',').split(',')]
    elif isinstance(value, (list, tuple)):
        raw_values = [str(item or '').strip() for item in value]
    else:
        raw_values = []

    tags: list[str] = []
    allowed = set(ALLOWED_TONE_TAGS)
    for raw_value in raw_values:
        tag = raw_value.lower().replace(' ', '-')
        if not tag or tag in tags:
            continue
        if tag not in allowed or not _TONE_TAG_RE.fullmatch(tag):
            continue
        tags.append(tag)
        if len(tags) >= MAX_TONE_TAGS:
            break
    return tags


def content_settings_from_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        snapshot = {}
    settings = snapshot.get('contentSettings')
    if not isinstance(settings, dict):
        settings = snapshot.get('content_settings')
    if not isinstance(settings, dict):
        settings = {}
    rating = normalize_content_rating(
        settings.get('contentRating')
        or settings.get('content_rating')
        or settings.get('rating')
        or snapshot.get('contentRating')
        or snapshot.get('content_rating')
    )
    tone_tags = normalize_tone_tags(
        settings.get('toneTags')
        or settings.get('tone_tags')
        or snapshot.get('toneTags')
        or snapshot.get('tone_tags')
    )
    return {
        'content_rating': rating,
        'tone_tags': tone_tags,
        'updated_at': str(settings.get('updatedAt') or settings.get('updated_at') or '') or None,
    }


def session_content_settings(session_obj: Session | None) -> dict[str, Any]:
    if not session_obj:
        return {
            'content_rating': DEFAULT_CONTENT_RATING,
            'tone_tags': [],
            'updated_at': None,
        }
    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    return content_settings_from_snapshot(snapshot)


def content_settings_payload(settings: dict[str, Any]) -> dict[str, Any]:
    rating = normalize_content_rating(settings.get('content_rating') or settings.get('contentRating'))
    tone_tags = normalize_tone_tags(settings.get('tone_tags') or settings.get('toneTags'))
    updated_at = settings.get('updated_at') or settings.get('updatedAt')
    return {
        'content_rating': rating,
        'contentRating': rating,
        'tone_tags': tone_tags,
        'toneTags': tone_tags,
        'updated_at': updated_at,
        'updatedAt': updated_at,
        'ratings': list(CONTENT_RATINGS),
        'available_tone_tags': list(ALLOWED_TONE_TAGS),
        'availableToneTags': list(ALLOWED_TONE_TAGS),
    }


def apply_session_content_settings(
    session_obj: Session,
    *,
    content_rating: Any = None,
    tone_tags: Any = None,
) -> dict[str, Any]:
    def update_snapshot(_session_obj: Session, snapshot: dict[str, Any]) -> dict[str, Any]:
        existing = content_settings_from_snapshot(snapshot)
        next_settings = {
            'content_rating': normalize_content_rating(
                content_rating if content_rating is not None else existing.get('content_rating')
            ),
            'tone_tags': normalize_tone_tags(
                tone_tags if tone_tags is not None else existing.get('tone_tags')
            ),
            'updated_at': utc_now().isoformat(),
        }
        snapshot['contentSettings'] = {
            'contentRating': next_settings['content_rating'],
            'toneTags': next_settings['tone_tags'],
            'updatedAt': next_settings['updated_at'],
        }
        return {
            'contentRating': next_settings['content_rating'],
            'toneTags': next_settings['tone_tags'],
        }

    result = mutate_session_snapshot_metadata(
        session_obj.session_id,
        mutate_snapshot=update_snapshot,
        source='api.session.content_settings',
        change_type='session.content_settings.update',
    )
    if result.session_obj is None:
        raise ValueError('Session no longer exists.')
    return content_settings_from_snapshot(result.state)
