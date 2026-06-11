"""Shared state projection for CampaignSegment state triggers."""

from __future__ import annotations

from typing import Any

from aidm_server.canon_projection import refresh_session_projection
from aidm_server.database import db
from aidm_server.models import Campaign, Session, safe_json_loads


ACTIVE_QUEST_STATUSES = {'active', 'open', 'available', 'in_progress', 'in-progress', 'started'}


def _text(value: Any) -> str:
    return str(value or '').strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _dicts(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _quest_is_active(quest: dict, active_quest_ids: set[str]) -> bool:
    quest_id = _text(quest.get('id') or quest.get('questId'))
    if quest_id and quest_id.lower() in active_quest_ids:
        return True
    status = _text(quest.get('status')).lower()
    return status in ACTIVE_QUEST_STATUSES


def _quest_search_text(quest: dict) -> list[str]:
    values = [
        _text(quest.get('id') or quest.get('questId')),
        _text(quest.get('title') or quest.get('name')),
        _text(quest.get('stage')),
        _text(quest.get('summary')),
    ]
    for objective in _dicts(quest.get('objectives')):
        values.extend(
            [
                _text(objective.get('id') or objective.get('objectiveId')),
                _text(objective.get('description') or objective.get('title') or objective.get('name')),
                _text(objective.get('status')),
            ]
        )
    return _unique(values)


def _projection_payload(session_id: int, campaign: Campaign) -> dict:
    session_state = refresh_session_projection(session_id, campaign)
    return {
        'current_location': session_state.current_location or campaign.location,
        'current_quest': session_state.current_quest or campaign.current_quest,
    }


def _snapshot_has_usable_state(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict):
        return False
    return any(
        [
            isinstance(snapshot.get('currentScene'), dict),
            isinstance(snapshot.get('quests'), list),
            isinstance(snapshot.get('locations'), list),
            isinstance(snapshot.get('knownNpcs'), list),
            isinstance(snapshot.get('partyNpcs'), list),
            isinstance(snapshot.get('flags'), dict),
        ]
    )


def build_segment_state_payload(session_id: int, campaign: Campaign) -> tuple[dict, dict]:
    """Build the compact state payload consumed by segment trigger evaluation.

    Live runtime values from Session.state_snapshot win. SessionState and Campaign
    are retained only as fallback for older sessions without live snapshots.
    """

    campaign_state = {
        'location': campaign.location,
        'current_quest': campaign.current_quest,
    }
    payload: dict[str, Any] = {
        'current_location': campaign.location,
        'current_quest': campaign.current_quest,
    }

    session_obj = db.session.get(Session, session_id)
    snapshot = safe_json_loads(session_obj.state_snapshot if session_obj else None, {})
    if not _snapshot_has_usable_state(snapshot):
        return _projection_payload(session_id, campaign), campaign_state

    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    live_location = _text(scene.get('name'))
    live_location_id = _text(scene.get('locationId'))
    if live_location or live_location_id:
        payload['current_location'] = live_location or live_location_id
        payload['current_location_id'] = live_location_id
    if scene:
        payload.update(
            {
                'current_scene_type': _text(scene.get('sceneType')),
                'current_scene_mood': _text(scene.get('mood')),
                'danger_level': scene.get('dangerLevel'),
                'combat_state': _text(scene.get('combatState')),
            }
        )

    active_quest_ids = _string_list(scene.get('activeQuestIds'))
    active_quest_id_set = {quest_id.lower() for quest_id in active_quest_ids}
    quests = _dicts(snapshot.get('quests'))
    active_quests = [quest for quest in quests if _quest_is_active(quest, active_quest_id_set)]
    if active_quest_ids:
        order = {quest_id: index for index, quest_id in enumerate(active_quest_ids)}
        active_quests.sort(key=lambda quest: order.get(_text(quest.get('id') or quest.get('questId')), len(order)))

    if active_quests or active_quest_ids:
        quest_ids = _unique(
            [
                *active_quest_ids,
                *[_text(quest.get('id') or quest.get('questId')) for quest in active_quests],
            ]
        )
        quest_titles = _unique([_text(quest.get('title') or quest.get('name')) for quest in active_quests])
        quest_stages = _unique([_text(quest.get('stage')) for quest in active_quests])
        quest_summaries = _unique([_text(quest.get('summary')) for quest in active_quests])
        quest_objectives: list[str] = []
        quest_texts: list[str] = []
        for quest in active_quests:
            quest_texts.extend(_quest_search_text(quest))
            for objective in _dicts(quest.get('objectives')):
                quest_objectives.extend(
                    [
                        _text(objective.get('id') or objective.get('objectiveId')),
                        _text(objective.get('description') or objective.get('title') or objective.get('name')),
                    ]
                )
        payload.update(
            {
                'active_quest_ids': quest_ids,
                'active_quest_titles': quest_titles,
                'active_quest_stages': quest_stages,
                'active_quest_summaries': quest_summaries,
                'active_quest_objectives': _unique(quest_objectives),
                'active_quest_texts': _unique([*quest_texts, *quest_ids]),
            }
        )
        if quest_titles:
            payload['current_quest'] = (
                f'{quest_titles[0]} - {quest_stages[0]}' if quest_stages else quest_titles[0]
            )

    locations = _dicts(snapshot.get('locations'))
    payload['known_location_ids'] = _unique(
        [_text(location.get('id') or location.get('locationId')) for location in locations]
    )
    payload['known_location_names'] = _unique([_text(location.get('name')) for location in locations])

    active_npc_ids = _string_list(scene.get('activeNpcIds'))
    known_npcs = _dicts(snapshot.get('knownNpcs'))
    party_npcs = _dicts(snapshot.get('partyNpcs'))
    npcs = [*known_npcs, *party_npcs]
    active_npc_id_set = {npc_id.lower() for npc_id in active_npc_ids}
    active_npcs = [
        npc
        for npc in npcs
        if _text(npc.get('id') or npc.get('npcId')).lower() in active_npc_id_set
    ]
    payload['active_npc_ids'] = _unique(
        [
            *active_npc_ids,
            *[_text(npc.get('id') or npc.get('npcId')) for npc in active_npcs],
        ]
    )
    payload['active_npc_names'] = _unique([_text(npc.get('name')) for npc in active_npcs])
    payload['known_npc_ids'] = _unique([_text(npc.get('id') or npc.get('npcId')) for npc in npcs])
    payload['known_npc_names'] = _unique([_text(npc.get('name')) for npc in npcs])
    payload['flags'] = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}

    return payload, campaign_state
