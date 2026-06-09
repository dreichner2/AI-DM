"""Context assembly for DM prompt requests."""

from __future__ import annotations

import json

from sqlalchemy import func

from aidm_server.canon_inventory import inventory_payload
from aidm_server.character_state import character_state_for_player
from aidm_server.database import db
from aidm_server.emergent_memory import build_emergent_context
from aidm_server.models import (
    Campaign,
    CampaignSegment,
    DmTurn,
    Player,
    PlayerAction,
    SessionLogEntry,
    SessionState,
    World,
    safe_json_loads,
)
from aidm_server.race_system import build_race_context_summary
from aidm_server.time_utils import utc_now


CONTEXT_VERSION = 'v2'


def _truncate_text(value: str | None, max_length: int) -> str:
    text = str(value or '').strip()
    if len(text) <= max_length:
        return text
    return f'{text[: max(0, max_length - 1)].rstrip()}…'


def _recent_actions_by_player(player_ids: list[int], limit_per_player: int = 3) -> dict[int, list[str]]:
    if not player_ids:
        return {}

    ranked_actions = (
        db.session.query(
            PlayerAction.player_id.label('player_id'),
            PlayerAction.action_text.label('action_text'),
            func.row_number()
            .over(
                partition_by=PlayerAction.player_id,
                order_by=(PlayerAction.timestamp.desc(), PlayerAction.action_id.desc()),
            )
            .label('row_number'),
        )
        .filter(PlayerAction.player_id.in_(player_ids))
        .subquery()
    )
    rows = (
        db.session.query(ranked_actions.c.player_id, ranked_actions.c.action_text)
        .filter(ranked_actions.c.row_number <= limit_per_player)
        .order_by(ranked_actions.c.player_id.asc(), ranked_actions.c.row_number.desc())
        .all()
    )

    recent_actions: dict[int, list[str]] = {}
    for row in rows:
        recent_actions.setdefault(int(row.player_id), []).append(str(row.action_text))
    return recent_actions


def build_dm_context(world_id, campaign_id, session_id=None, max_turns: int = 8, query_text: str | None = None):
    """Build deterministic bounded context for DM responses."""
    world = db.session.get(World, world_id)
    campaign = db.session.get(Campaign, campaign_id)

    world_summary = {
        'world_id': world_id,
        'name': world.name if world else 'Unknown',
        'description': world.description if world else 'No world data available.',
    }

    campaign_summary = {
        'campaign_id': campaign_id,
        'title': campaign.title if campaign else 'Unknown',
        'description': campaign.description if campaign else 'No campaign data available.',
        'current_quest': (campaign.current_quest if campaign else None) or 'None',
        'location': (campaign.location if campaign else None) or 'Unknown',
    }

    players = (
        Player.query.filter_by(workspace_id=campaign.workspace_id, campaign_id=campaign.campaign_id)
        .order_by(Player.player_id.asc())
        .all()
        if campaign
        else []
    )
    recent_actions_map = _recent_actions_by_player([player.player_id for player in players])
    active_players = []
    for player in players:
        active_players.append(
            {
                'player_id': player.player_id,
                'character_name': player.character_name,
                'race': player.race,
                'race_summary': build_race_context_summary(player.race_selection, player.race),
                'class': player.class_,
                'level': player.level,
                'state': character_state_for_player(player),
                'inventory': inventory_payload(player.inventory),
                'recent_actions': recent_actions_map.get(player.player_id, []),
            }
        )

    recent_turns = []
    if session_id:
        turns = (
            DmTurn.query.filter_by(session_id=session_id)
            .order_by(DmTurn.turn_id.desc())
            .limit(max_turns)
            .all()
        )
        for turn in reversed(turns):
            recent_turns.append(
                {
                    'turn_id': turn.turn_id,
                    'player_id': turn.player_id,
                    'player_input': _truncate_text(turn.player_input, 240),
                    'dm_output': _truncate_text(turn.dm_output, 600),
                    'requires_roll': turn.requires_roll,
                    'rule_type': turn.rule_type,
                    'confidence': turn.confidence,
                    'roll_value': turn.roll_value,
                    'outcome_status': turn.outcome_status,
                }
            )

    recent_log = []
    if session_id and not recent_turns:
        entries = (
            SessionLogEntry.query.filter_by(session_id=session_id)
            .order_by(SessionLogEntry.timestamp.desc(), SessionLogEntry.id.desc())
            .limit(max_turns)
            .all()
        )
        recent_log = [entry.message for entry in reversed(entries)]

    pending_checks = []
    if session_id:
        pending_turns = (
            DmTurn.query.filter_by(session_id=session_id, outcome_status='deferred')
            .order_by(DmTurn.turn_id.asc())
            .limit(5)
            .all()
        )
        for turn in pending_turns:
            turn_hint = safe_json_loads(turn.rules_hint, {})
            turn_metadata = safe_json_loads(turn.metadata_json, {})
            turn_metadata = turn_metadata if isinstance(turn_metadata, dict) else {}
            pending_checks.append(
                {
                    'turn_id': turn.turn_id,
                    'player_input': turn.player_input,
                    'rule_type': turn.rule_type,
                    'dc_hint': turn_hint.get('dc_hint') if isinstance(turn_hint, dict) else None,
                    'turn_number': turn_hint.get('turn_number') if isinstance(turn_hint, dict) else None,
                    'roll_gate': turn_metadata.get('roll_gate'),
                }
            )

    segments = CampaignSegment.query.filter_by(campaign_id=campaign_id, is_triggered=True).all()
    triggered_segments = [
        {
            'segment_id': seg.segment_id,
            'title': seg.title,
            'description': seg.description,
            'tags': seg.tags,
        }
        for seg in segments
    ]

    session_state_payload = {
        'rolling_summary': '',
        'current_location': campaign_summary['location'],
        'current_quest': campaign_summary['current_quest'],
        'active_segments': [],
        'memory_snippets': [],
    }

    if session_id:
        state = SessionState.query.filter_by(session_id=session_id).first()
        if state:
            memory_snippets = safe_json_loads(state.memory_snippets, [])
            memory_snippets = memory_snippets if isinstance(memory_snippets, list) else []
            session_state_payload = {
                'rolling_summary': _truncate_text(state.rolling_summary, 4000),
                'current_location': state.current_location or campaign_summary['location'],
                'current_quest': state.current_quest or campaign_summary['current_quest'],
                'active_segments': safe_json_loads(state.active_segments, []),
                'memory_snippets': [
                    {
                        **snippet,
                        'player_input': _truncate_text(snippet.get('player_input'), 180),
                        'dm_output': _truncate_text(snippet.get('dm_output'), 260),
                    }
                    for snippet in memory_snippets[-8:]
                    if isinstance(snippet, dict)
                ],
            }

    emergent_memory = build_emergent_context(
        campaign_id=campaign_id,
        session_id=session_id,
        query_text=query_text,
        current_location=session_state_payload['current_location'],
        current_quest=session_state_payload['current_quest'],
        recent_turns=recent_turns,
    )

    context_payload = {
        'context_version': CONTEXT_VERSION,
        'generated_at': utc_now().isoformat(),
        'world': world_summary,
        'campaign': campaign_summary,
        'session_state': session_state_payload,
        'active_players': active_players,
        'triggered_segments': triggered_segments,
        'authored_segments': triggered_segments,
        'story_threads': emergent_memory.get('threads', []),
        'emergent_memory': emergent_memory,
        'recent_turns': recent_turns,
        'recent_log': recent_log,
        'pending_checks': pending_checks,
    }
    return json.dumps(context_payload, separators=(',', ':'), ensure_ascii=False)
