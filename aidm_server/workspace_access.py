"""Helpers for auth-token scoped campaign workspaces."""

from __future__ import annotations

from flask import g, has_request_context

from aidm_server.auth import DEFAULT_WORKSPACE_ID
from aidm_server.database import db
from aidm_server.models import Campaign, CampaignSegment, Map, Player, Session, World


def current_workspace_id() -> str:
    if not has_request_context():
        return DEFAULT_WORKSPACE_ID
    return str(getattr(g, 'aidm_workspace_id', None) or DEFAULT_WORKSPACE_ID)


def current_account_id() -> int | None:
    if not has_request_context():
        return None
    account_id = getattr(g, 'aidm_account_id', None)
    try:
        return int(account_id) if account_id is not None else None
    except (TypeError, ValueError):
        return None


def current_account_is_workspace_admin() -> bool:
    if not has_request_context():
        return False
    return bool(getattr(g, 'aidm_workspace_admin', False))


def campaign_query():
    return Campaign.query.filter_by(workspace_id=current_workspace_id())


def world_query():
    return World.query.filter_by(workspace_id=current_workspace_id())


def campaign_is_visible(campaign: Campaign | None, workspace_id: str | None = None) -> bool:
    if not campaign:
        return False
    return (campaign.workspace_id or DEFAULT_WORKSPACE_ID) == (workspace_id or current_workspace_id())


def world_is_visible(world: World | None, workspace_id: str | None = None) -> bool:
    if not world:
        return False
    return (world.workspace_id or DEFAULT_WORKSPACE_ID) == (workspace_id or current_workspace_id())


def get_world(world_id: int, workspace_id: str | None = None) -> World | None:
    return World.query.filter_by(
        world_id=world_id,
        workspace_id=workspace_id or current_workspace_id(),
    ).first()


def get_campaign(campaign_id: int, workspace_id: str | None = None) -> Campaign | None:
    return Campaign.query.filter_by(
        campaign_id=campaign_id,
        workspace_id=workspace_id or current_workspace_id(),
    ).first()


def get_session(session_id: int, workspace_id: str | None = None) -> Session | None:
    session_obj = db.session.get(Session, session_id)
    if not session_obj or not campaign_is_visible(session_obj.campaign, workspace_id):
        return None
    return session_obj


def get_player(
    player_id: int,
    workspace_id: str | None = None,
    *,
    account_id: int | None = None,
    is_admin: bool | None = None,
) -> Player | None:
    player = db.session.get(Player, player_id)
    if not player:
        return None
    target_workspace_id = workspace_id or current_workspace_id()
    if not player_is_visible(
        player,
        target_workspace_id,
        account_id=current_account_id() if account_id is None else account_id,
        is_admin=current_account_is_workspace_admin() if is_admin is None else is_admin,
    ):
        return None
    if player.workspace_id:
        return player
    if not campaign_is_visible(player.campaign, target_workspace_id):
        return None
    return player


def player_is_visible(
    player: Player | None,
    workspace_id: str | None = None,
    *,
    account_id: int | None = None,
    is_admin: bool | None = None,
) -> bool:
    if not player:
        return False
    target_workspace_id = workspace_id or current_workspace_id()
    if player.workspace_id and player.workspace_id != target_workspace_id:
        return False
    if not player.workspace_id and not campaign_is_visible(player.campaign, target_workspace_id):
        return False

    if is_admin is None:
        is_admin = current_account_is_workspace_admin()
    if is_admin:
        return True

    if account_id is None:
        account_id = current_account_id()
    if account_id is None:
        return True
    return player.account_id == account_id


def visible_players_query(workspace_id: str | None = None, *, campaign_id: int | None = None):
    target_workspace_id = workspace_id or current_workspace_id()
    query = Player.query.filter_by(workspace_id=target_workspace_id)
    if campaign_id is not None:
        query = query.filter(Player.campaign_id == campaign_id)
    account_id = current_account_id()
    if account_id is not None and not current_account_is_workspace_admin():
        query = query.filter(Player.account_id == account_id)
    return query


def get_campaign_map(map_id: int, workspace_id: str | None = None) -> Map | None:
    map_obj = db.session.get(Map, map_id)
    if not map_obj:
        return None
    if map_obj.campaign_id is None:
        return map_obj if world_is_visible(map_obj.world, workspace_id) else None
    return map_obj if campaign_is_visible(map_obj.campaign, workspace_id) else None


def get_segment(segment_id: int, workspace_id: str | None = None) -> CampaignSegment | None:
    segment = db.session.get(CampaignSegment, segment_id)
    if not segment or not campaign_is_visible(segment.campaign, workspace_id):
        return None
    return segment
