from __future__ import annotations

from flask import current_app
from flask_admin import Admin
from flask_admin import AdminIndexView
from flask_admin.contrib.sqla import ModelView
from flask_admin.helpers import is_form_submitted

from aidm_server.auth import DEFAULT_WORKSPACE_ID, request_is_authorized, request_workspace_id

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
    return request_is_authorized() and request_workspace_id() == DEFAULT_WORKSPACE_ID


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
    admin.add_view(ProtectedModelView(World, db.session))
    admin.add_view(CampaignModelView(Campaign, db.session))
    admin.add_view(PlayerModelView(Player, db.session))
    admin.add_view(ProtectedModelView(Session, db.session))
    admin.add_view(ProtectedModelView(SessionState, db.session))
    admin.add_view(ProtectedModelView(DmTurn, db.session))
    admin.add_view(NpcModelView(Npc, db.session))
    admin.add_view(ProtectedModelView(PlayerAction, db.session))
    admin.add_view(ProtectedModelView(Map, db.session))
    admin.add_view(SessionLogEntryModelView(SessionLogEntry, db.session))
    admin.add_view(ProtectedModelView(CampaignSegment, db.session))
    admin.add_view(ProtectedModelView(StoryEntity, db.session))
    admin.add_view(ProtectedModelView(StoryFact, db.session))
    admin.add_view(ProtectedModelView(StoryThread, db.session))
    admin.add_view(ProtectedModelView(TurnCanonUpdate, db.session))
    admin.add_view(StoryEventModelView(StoryEvent, db.session))
    return admin
