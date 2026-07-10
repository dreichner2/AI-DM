"""Scene-state projection for player-facing atmosphere and music."""

from __future__ import annotations

from typing import Any

from aidm_server.database import db
from aidm_server.models import Session, safe_json_loads


SCENE_MUSIC_TAGS = {
    'calm',
    'combat',
    'discovery',
    'dungeon',
    'forest',
    'mystery',
    'tension',
    'town',
    'travel',
}


def _text(value: Any, default: str = '') -> str:
    text = str(value or '').strip()
    return text or default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _status_key(value: Any) -> str:
    return _text(value).lower().replace('-', '_').replace(' ', '_')


def _music_tag_for_scene(scene: dict[str, Any], combat: dict[str, Any], *, in_combat: bool) -> str:
    authored_tag = _status_key(scene.get('musicTag') or scene.get('music_tag'))
    if authored_tag in SCENE_MUSIC_TAGS:
        return authored_tag
    if in_combat:
        return 'combat'

    scene_type = _status_key(scene.get('sceneType') or scene.get('scene_type'))
    mood = _status_key(scene.get('mood'))
    combat_state = _status_key(scene.get('combatState') or scene.get('combat_state') or combat.get('status'))
    danger_level = max(0, min(10, _int(scene.get('dangerLevel') or scene.get('danger_level'), 0)))

    if combat_state in {'starting', 'pending'} or danger_level >= 7:
        return 'tension'
    if mood in {'mystery', 'ominous', 'eerie', 'secretive'}:
        return 'mystery'
    if mood in {'tense', 'dangerous', 'threatening'} or danger_level >= 5:
        return 'tension'
    if scene_type in {'forest', 'woods', 'wilderness'}:
        return 'forest'
    if scene_type in {'town', 'city', 'village', 'social', 'tavern'}:
        return 'town'
    if scene_type in {'travel', 'road', 'journey'}:
        return 'travel'
    if scene_type in {'dungeon', 'cavern', 'crypt', 'ruins'}:
        return 'dungeon'
    if scene_type in {'mystery', 'investigation'}:
        return 'mystery'
    if scene_type in {'discovery', 'exploration'}:
        return 'discovery'
    return 'calm'


def scene_state_for_session(session_id: int, *, acting_player_id: int | None = None) -> dict[str, Any] | None:
    session_obj = db.session.get(Session, session_id)
    if session_obj is None:
        return None

    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    combat = snapshot.get('combat') if isinstance(snapshot.get('combat'), dict) else {}

    combat_status = _status_key(combat.get('status'))
    combat_state = _status_key(scene.get('combatState') or scene.get('combat_state') or combat_status or 'none')
    in_combat = combat_status in {'starting', 'active'} or combat_state in {'starting', 'pending', 'active'}
    danger_level = max(0, min(10, _int(scene.get('dangerLevel') or scene.get('danger_level'), 0)))

    return {
        'session_id': session_obj.session_id,
        'location_id': _text(scene.get('locationId') or scene.get('location_id')) or None,
        'location_name': _text(scene.get('name'), 'Unknown location'),
        'scene_type': _text(scene.get('sceneType') or scene.get('scene_type'), 'scene'),
        'mood': _text(scene.get('mood')) or None,
        'danger_level': danger_level,
        'combat_state': combat_state or 'none',
        'in_combat': in_combat,
        'music_tag': _music_tag_for_scene(scene, combat, in_combat=in_combat),
        'acting_player_id': acting_player_id,
    }
