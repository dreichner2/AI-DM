from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, or_

from aidm_server.database import db
from aidm_server.map_visibility import visible_maps_query
from aidm_server.models import (
    Campaign,
    CampaignSegment,
    DmTurn,
    Map,
    Player,
    Session,
    SessionLogEntry,
    SessionState,
)
from aidm_server.pagination import InvalidCursorError, decode_cursor, encode_cursor, limited_page
from aidm_server.response_dtos import (
    ARCHIVED_STATUS,
    campaign_payload,
    current_chapter_session_id,
    isoformat,
    map_payload,
    player_summary_payload,
    public_segment_payload,
    segment_payload,
    session_activity_at,
    session_payloads,
)
from aidm_server.workspace_access import visible_players_query

SESSION_PAGE_DEFAULT_LIMIT = 30
SESSION_PAGE_MAX_LIMIT = 100

# (latest_activity_at or '', session_id): the session list's sort key, newest first.
SessionSortKey = tuple[str, int]


@dataclass(frozen=True)
class SessionCursor:
    boundary: SessionSortKey
    remaining_ids: tuple[int, ...] | None = None


@dataclass(frozen=True)
class SessionPage:
    sessions: list[Session]
    next_cursor: str | None
    # Every listed session's sort key, newest first, across all pages.
    order: list[SessionSortKey]


def _listed_sessions_filter(campaign_id: int, include_archived: bool):
    filters = [Session.campaign_id == campaign_id]
    if not include_archived:
        filters.append(or_(Session.status.is_(None), Session.status != ARCHIVED_STATUS))
    return filters


def session_activity_order(campaign_id: int, *, include_archived: bool) -> list[SessionSortKey]:
    """Sort keys of every listed session, read from columns and aggregates only.

    Snapshots are never loaded here, so ordering a large campaign stays cheap.
    """

    filters = _listed_sessions_filter(campaign_id, include_archived)
    aggregates: dict[int, dict] = {}
    columns: dict[int, tuple] = {}
    for session_id, created_at, updated_at in (
        db.session.query(Session.session_id, Session.created_at, Session.updated_at).filter(*filters).all()
    ):
        columns[session_id] = (created_at, updated_at)
        aggregates[session_id] = {}
    for session_id, state_updated_at in (
        db.session.query(SessionState.session_id, func.max(SessionState.updated_at))
        .join(Session, Session.session_id == SessionState.session_id)
        .filter(*filters)
        .group_by(SessionState.session_id)
        .all()
    ):
        aggregates[session_id]['state_updated_at'] = state_updated_at
    for session_id, latest_log_at in (
        db.session.query(SessionLogEntry.session_id, func.max(SessionLogEntry.timestamp))
        .join(Session, Session.session_id == SessionLogEntry.session_id)
        .filter(*filters)
        .group_by(SessionLogEntry.session_id)
        .all()
    ):
        aggregates[session_id]['latest_log_at'] = latest_log_at
    for session_id, latest_created_at, latest_completed_at in (
        db.session.query(DmTurn.session_id, func.max(DmTurn.created_at), func.max(DmTurn.completed_at))
        .join(Session, Session.session_id == DmTurn.session_id)
        .filter(*filters)
        .group_by(DmTurn.session_id)
        .all()
    ):
        aggregates[session_id]['latest_turn_created_at'] = latest_created_at
        aggregates[session_id]['latest_turn_completed_at'] = latest_completed_at
    return sorted(
        (
            (session_activity_at(*columns[session_id], aggregates[session_id]) or '', session_id)
            for session_id in columns
        ),
        reverse=True,
    )


def decode_session_cursor(token: str | None) -> SessionCursor | None:
    if not token:
        return None
    try:
        activity, session_id, remaining = decode_cursor(token, size=3)
    except InvalidCursorError:
        activity, session_id = decode_cursor(token, size=2)
        remaining = None
    if not isinstance(activity, str) or isinstance(session_id, bool) or not isinstance(session_id, int):
        raise InvalidCursorError('cursor is invalid.')
    if remaining is not None and (
        not isinstance(remaining, list)
        or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in remaining)
        or len(set(remaining)) != len(remaining)
        or session_id in remaining
    ):
        raise InvalidCursorError('cursor is invalid.')
    return SessionCursor((activity, session_id), tuple(remaining) if remaining is not None else None)


def campaign_session_page(
    campaign_id: int,
    *,
    include_archived: bool = False,
    limit: int = SESSION_PAGE_DEFAULT_LIMIT,
    cursor: SessionCursor | None = None,
) -> SessionPage:
    """One page of a campaign's sessions, newest activity first.

    The cursor retains the last sort key and the remaining session IDs from the
    first page. This preserves that page's order if an unseen chapter is edited
    and moves above the boundary before it is read.
    """

    order = session_activity_order(campaign_id, include_archived=include_archived)
    if cursor and cursor.remaining_ids is not None:
        current_keys = {session_id: key for key in order for session_id in [key[1]]}
        remaining = [current_keys[session_id] for session_id in cursor.remaining_ids if session_id in current_keys]
    else:
        remaining = [key for key in order if cursor is None or key < cursor.boundary]
    page_keys = remaining[:limit]
    rows = {
        session_obj.session_id: session_obj
        for session_obj in Session.query.filter(
            Session.session_id.in_([session_id for _activity, session_id in page_keys])
        ).all()
    }
    return SessionPage(
        sessions=[rows[session_id] for _activity, session_id in page_keys if session_id in rows],
        next_cursor=(
            encode_cursor(*page_keys[-1], [session_id for _activity, session_id in remaining[limit:]])
            if len(remaining) > limit else None
        ),
        order=order,
    )


def list_campaign_session_payloads(
    campaign_id: int,
    *,
    include_archived: bool = False,
    limit: int = SESSION_PAGE_DEFAULT_LIMIT,
    cursor: SessionCursor | None = None,
) -> dict:
    page = campaign_session_page(campaign_id, include_archived=include_archived, limit=limit, cursor=cursor)
    return {
        'campaign_id': campaign_id,
        'sessions': session_payloads(page.sessions),
        'limit': limit,
        'has_more': page.next_cursor is not None,
        'next_cursor': page.next_cursor,
    }


def _with_current_chapter(campaign_id: int, page: SessionPage) -> list[Session]:
    """The page plus the campaign's current chapter, in list order."""

    current_id = current_chapter_session_id(campaign_id)
    if current_id is None or any(session_obj.session_id == current_id for session_obj in page.sessions):
        return page.sessions
    current = db.session.get(Session, current_id)
    if current is None:
        return page.sessions
    rank = {session_id: index for index, (_activity, session_id) in enumerate(page.order)}
    return sorted([*page.sessions, current], key=lambda session_obj: rank.get(session_obj.session_id, len(rank)))


def campaign_workspace_payload(
    campaign: Campaign,
    *,
    include_archived: bool = False,
    sessions_limit: int = SESSION_PAGE_DEFAULT_LIMIT,
    sessions_cursor: SessionCursor | None = None,
    player_limit: int | None = None,
    map_limit: int | None = None,
    segment_limit: int | None = None,
    include_hidden_state: bool = True,
) -> dict:
    campaign_id = campaign.campaign_id
    campaign_data = campaign_payload(campaign)
    session_page = campaign_session_page(
        campaign_id,
        include_archived=include_archived,
        limit=sessions_limit,
        cursor=sessions_cursor,
    )
    session_items = session_payloads(_with_current_chapter(campaign_id, session_page))
    players_query = visible_players_query(campaign.workspace_id, campaign_id=campaign_id)
    player_count = players_query.count()
    players = limited_page(
        players_query.order_by(Player.created_at.asc(), Player.player_id.asc()),
        limit=player_limit,
    )
    maps_query = visible_maps_query(
        Map.query.filter_by(campaign_id=campaign_id),
        include_dm_only=include_hidden_state,
    )
    map_count = maps_query.count()
    maps = limited_page(
        maps_query.order_by(Map.created_at.desc(), Map.map_id.desc()),
        limit=map_limit,
    )
    segments_query = CampaignSegment.query.filter_by(campaign_id=campaign_id)
    if not include_hidden_state:
        segments_query = segments_query.filter(CampaignSegment.is_triggered.is_(True))
    segment_count = segments_query.count()
    segments = limited_page(
        segments_query.order_by(
            CampaignSegment.created_at.desc(),
            CampaignSegment.segment_id.desc(),
        ),
        limit=segment_limit,
    )
    latest_activity, latest_session_id = session_page.order[0] if session_page.order else (None, None)

    return {
        'campaign': campaign_data,
        'sessions': session_items,
        'players': [player_summary_payload(player) for player in players],
        'maps': [map_payload(map_obj) for map_obj in maps],
        'segments': [
            segment_payload(segment) if include_hidden_state else public_segment_payload(segment)
            for segment in segments
        ],
        'summary': {
            'session_count': len(session_page.order),
            'player_count': player_count,
            'map_count': map_count,
            'segment_count': segment_count,
            'latest_session_id': (
                latest_session_id if latest_session_id is not None else campaign_data['latest_session_id']
            ),
            'latest_activity_at': (
                latest_activity
                or campaign_data['latest_activity_at']
                or isoformat(campaign.created_at)
            ),
        },
        'has_more': {
            'sessions': session_page.next_cursor is not None,
            'players': players._has_more,
            'maps': maps._has_more,
            'segments': segments._has_more,
        },
        'next_cursor': {
            'sessions': session_page.next_cursor,
            'players': players[-1].player_id if players._has_more and players else None,
            'maps': maps[-1].map_id if maps._has_more and maps else None,
            'segments': segments[-1].segment_id if segments._has_more and segments else None,
        },
        'limits': {
            'sessions': sessions_limit,
            'players': player_limit,
            'maps': map_limit,
            'segments': segment_limit,
        },
    }
