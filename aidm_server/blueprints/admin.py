from __future__ import annotations

from flask import current_app
from flask_admin import Admin
from flask_admin import AdminIndexView
from flask_admin.contrib.sqla import ModelView
from flask_admin.helpers import is_form_submitted

from aidm_server.auth import (
    DEFAULT_WORKSPACE_ID,
    account_workspace_membership,
    is_global_operator_token,
    request_account,
    request_workspace_id,
    request_workspace_token,
    workspace_role_is_admin,
)

from aidm_server.models import (
    Campaign,
    CampaignSegment,
    DmTurn,
    Map,
    Npc,
    Player,
    PlayerAction,
    Session,
    SessionLogEntry,
    SessionState,
    StoryEntity,
    StoryFact,
    StoryThread,
    StoryEvent,
    TurnCanonUpdate,
    World,
)


def _admin_request_authorized() -> bool:
    workspace_id = request_workspace_id()
    if workspace_id != DEFAULT_WORKSPACE_ID:
        return False

    account = request_account()
    if account is None:
        return is_global_operator_token(request_workspace_token())

    membership = account_workspace_membership(account, workspace_id)
    return bool(membership and workspace_role_is_admin(membership.role))


class ProtectedAdminMixin:
    def is_accessible(self):
        if not bool(current_app.config.get('AIDM_ADMIN_ENABLED', False)):
            return False

        auth_required = bool(current_app.config.get('AIDM_AUTH_REQUIRED', False))
        if not auth_required:
            return False
        return _admin_request_authorized()

    def inaccessible_callback(self, name, **kwargs):
        if bool(current_app.config.get('AIDM_AUTH_REQUIRED', False)):
            return ('Unauthorized', 401)
        return ('Forbidden', 403)


class ProtectedAdminIndexView(ProtectedAdminMixin, AdminIndexView):
    pass


class ProtectedModelView(ProtectedAdminMixin, ModelView):
    def is_action_allowed(self, name):
        if not self.is_accessible():
            return False
        return super().is_action_allowed(name)

    def validate_form(self, form):
        if not self.is_accessible() and is_form_submitted():
            return False
        return super().validate_form(form)


class CampaignModelView(ProtectedModelView):
    pass


class PlayerModelView(ProtectedModelView):
    pass


class NpcModelView(ProtectedModelView):
    pass


class SessionLogEntryModelView(ProtectedModelView):
    pass


class StoryEventModelView(ProtectedModelView):
    pass


def configure_admin(app, db):
    try:
        admin = Admin(app, name='AI-DM Admin', index_view=ProtectedAdminIndexView(), template_mode='bootstrap3')
    except TypeError:
        # Flask-Admin 2.x removed `template_mode`.
        admin = Admin(app, name='AI-DM Admin', index_view=ProtectedAdminIndexView())
    admin.add_view(ProtectedModelView(World, db))
    admin.add_view(CampaignModelView(Campaign, db))
    admin.add_view(PlayerModelView(Player, db))
    admin.add_view(ProtectedModelView(Session, db))
    admin.add_view(ProtectedModelView(SessionState, db))
    admin.add_view(ProtectedModelView(DmTurn, db))
    admin.add_view(NpcModelView(Npc, db))
    admin.add_view(ProtectedModelView(PlayerAction, db))
    admin.add_view(ProtectedModelView(Map, db))
    admin.add_view(SessionLogEntryModelView(SessionLogEntry, db))
    admin.add_view(ProtectedModelView(CampaignSegment, db))
    admin.add_view(ProtectedModelView(StoryEntity, db))
    admin.add_view(ProtectedModelView(StoryFact, db))
    admin.add_view(ProtectedModelView(StoryThread, db))
    admin.add_view(ProtectedModelView(TurnCanonUpdate, db))
    admin.add_view(StoryEventModelView(StoryEvent, db))
    return admin
