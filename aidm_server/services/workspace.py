from __future__ import annotations

from aidm_server.models import Campaign, CampaignSegment, Map, Player, Session
from aidm_server.pagination import limited_page
from aidm_server.response_dtos import (
    active_sessions_query,
    campaign_payload,
    isoformat,
    map_payload,
    player_summary_payload,
    segment_payload,
    session_payloads,
)
from aidm_server.workspace_access import visible_players_query


def _limit_query(query, limit: int | None):
    if limit is None:
        return query
    return query.limit(max(1, int(limit)))


def list_campaign_session_payloads(
    campaign_id: int,
    *,
    include_archived: bool = False,
    limit: int | None = None,
    include_hidden_state: bool = True,
) -> list[dict]:
    sessions_query = (
        Session.query.filter_by(campaign_id=campaign_id)
        if include_archived
        else active_sessions_query(campaign_id)
    )
    sessions = _limit_query(
        sessions_query.order_by(Session.updated_at.desc(), Session.created_at.desc()),
        limit,
    ).all()
    payloads = session_payloads(sessions, include_hidden_state=include_hidden_state)
    payloads.sort(key=lambda item: item.get('latest_activity_at') or '', reverse=True)
    return payloads


def campaign_workspace_payload(
    campaign: Campaign,
    *,
    include_archived: bool = False,
    session_limit: int | None = None,
    player_limit: int | None = None,
    map_limit: int | None = None,
    segment_limit: int | None = None,
    include_hidden_state: bool = True,
) -> dict:
    campaign_id = campaign.campaign_id
    campaign_data = campaign_payload(campaign)
    sessions_query = (
        Session.query.filter_by(campaign_id=campaign_id)
        if include_archived
        else active_sessions_query(campaign_id)
    )
    session_count = sessions_query.count()
    session_rows = limited_page(
        sessions_query.order_by(Session.updated_at.desc(), Session.created_at.desc()),
        limit=session_limit,
    )
    session_items = session_payloads(session_rows, include_hidden_state=include_hidden_state)
    session_items.sort(key=lambda item: item.get('latest_activity_at') or '', reverse=True)
    players_query = visible_players_query(campaign.workspace_id, campaign_id=campaign_id)
    player_count = players_query.count()
    players = limited_page(
        players_query.order_by(Player.created_at.asc(), Player.player_id.asc()),
        limit=player_limit,
    )
    maps_query = Map.query.filter_by(campaign_id=campaign_id)
    map_count = maps_query.count()
    maps = limited_page(
        maps_query.order_by(Map.created_at.desc(), Map.map_id.desc()),
        limit=map_limit,
    )
    segments_query = CampaignSegment.query.filter_by(campaign_id=campaign_id)
    segment_count = segments_query.count()
    segments = limited_page(
        segments_query.order_by(
            CampaignSegment.created_at.desc(),
            CampaignSegment.segment_id.desc(),
        ),
        limit=segment_limit,
    )
    latest_session = session_items[0] if session_items else None

    return {
        'campaign': campaign_data,
        'sessions': session_items,
        'players': [player_summary_payload(player) for player in players],
        'maps': [map_payload(map_obj) for map_obj in maps],
        'segments': [segment_payload(segment) for segment in segments],
        'summary': {
            'session_count': session_count,
            'player_count': player_count,
            'map_count': map_count,
            'segment_count': segment_count,
            'latest_session_id': (
                latest_session['session_id'] if latest_session else campaign_data['latest_session_id']
            ),
            'latest_activity_at': (
                latest_session['latest_activity_at']
                if latest_session
                else campaign_data['latest_activity_at'] or isoformat(campaign.created_at)
            ),
        },
        'has_more': {
            'sessions': session_rows._has_more,
            'players': players._has_more,
            'maps': maps._has_more,
            'segments': segments._has_more,
        },
        'next_cursor': {
            'sessions': session_rows[-1].session_id if session_rows._has_more and session_rows else None,
            'players': players[-1].player_id if players._has_more and players else None,
            'maps': maps[-1].map_id if maps._has_more and maps else None,
            'segments': segments[-1].segment_id if segments._has_more and segments else None,
        },
        'limits': {
            'sessions': session_limit,
            'players': player_limit,
            'maps': map_limit,
            'segments': segment_limit,
        },
    }
