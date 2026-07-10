"""Socket.IO runtime helpers outside the transport registration module."""

from __future__ import annotations

from threading import RLock
from typing import Any, Callable

from flask import current_app, request

from aidm_server.capabilities import Capability, actor_capabilities
from aidm_server.auth import (
    DEFAULT_WORKSPACE_ID,
    account_for_token,
    account_requires_password_setup,
    account_workspace_membership,
    ensure_account_workspace_membership,
    extract_socket_account_token,
    extract_socket_token,
    extract_socket_workspace_id,
    extract_socket_workspace_token,
    is_global_operator_token,
    is_token_authorized,
    workspace_id_for_workspace_token,
    workspace_role_is_admin,
)
from aidm_server.logging_context import new_correlation_id, set_logging_context
from aidm_server.rate_limiter import FixedWindowRateLimiter, build_rate_limiter
from aidm_server.socket_state import SocketState
from aidm_server.validation import coerce_int

EmitFn = Callable[..., Any]
LeaveRoomFn = Callable[[str], Any]


class SocketRuntime:
    def __init__(self, state: SocketState):
        self.state = state
        self._limiter: FixedWindowRateLimiter | None = None
        self._limiter_config: tuple[int, int, str, int] | None = None
        self._limiter_lock = RLock()

    def set_context(self, event_name: str, data: dict | None = None, turn_id: int | None = None) -> str:
        sid = getattr(request, 'sid', 'unknown')
        connection = self.state.connection(sid) or {}
        correlation_id = (
            (data or {}).get('correlation_id')
            or connection.get('correlation_id')
            or new_correlation_id(prefix=f'socket-{event_name}')
        )
        session_id = (data or {}).get('session_id') or connection.get('session_id')

        set_logging_context(correlation_id=correlation_id, session_id=session_id, turn_id=turn_id)
        if sid:
            self.state.update_connection_if_present(sid, correlation_id=correlation_id)
        return correlation_id

    def rate_limiter(self) -> FixedWindowRateLimiter:
        limit = int(current_app.config.get('AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES', 40))
        window_seconds = int(current_app.config.get('AIDM_RATE_LIMIT_WINDOW_SECONDS', 30))
        preauth_window_seconds = int(current_app.config.get('AIDM_PREAUTH_RATE_LIMIT_WINDOW_SECONDS', 60))
        configured_retention_seconds = int(
            current_app.config.get(
                'AIDM_RATE_LIMIT_RETENTION_WINDOW_SECONDS',
                max(1, window_seconds, preauth_window_seconds),
            )
        )
        store_name = str(current_app.config.get('AIDM_RATE_LIMIT_STORE', 'memory')).strip().lower()
        minimum_retention_seconds = max(
            1,
            window_seconds,
            preauth_window_seconds,
        )
        if store_name == 'database' and configured_retention_seconds < minimum_retention_seconds:
            raise ValueError(
                'Configured rate-limit retention is shorter than an active Socket.IO or '
                'pre-authentication window.'
            )
        retention_window_seconds = configured_retention_seconds
        limiter_config = (limit, window_seconds, store_name, retention_window_seconds)

        with self._limiter_lock:
            if self._limiter is None or self._limiter_config != limiter_config:
                self._limiter = build_rate_limiter(
                    limit=limit,
                    window_seconds=window_seconds,
                    store_name=store_name,
                    retention_window_seconds=retention_window_seconds,
                )
                self._limiter_config = limiter_config
            return self._limiter

    def auth_required(self) -> bool:
        try:
            return bool(current_app.config.get('AIDM_AUTH_REQUIRED', False))
        except Exception:
            return False

    def is_authorized(self, auth_payload: dict | None = None, data_payload: dict | None = None) -> bool:
        if not self.auth_required():
            return True
        sid = getattr(request, 'sid', None)
        existing = self.state.connection(sid)
        if existing and existing.get('authorized'):
            return True
        if self.workspace_id_for_auth(auth_payload=auth_payload, data_payload=data_payload):
            return True
        token = extract_socket_token(auth_payload=auth_payload, data_payload=data_payload)
        return is_token_authorized(token)

    def workspace_id_for_auth(self, auth_payload: dict | None = None, data_payload: dict | None = None) -> str | None:
        if not self.auth_required():
            workspace_token = extract_socket_workspace_token(auth_payload=auth_payload, data_payload=data_payload)
            return workspace_id_for_workspace_token(workspace_token) or DEFAULT_WORKSPACE_ID
        sid = getattr(request, 'sid', None)
        existing = self.state.connection(sid)
        if existing and existing.get('authorized'):
            return str(existing.get('workspace_id') or DEFAULT_WORKSPACE_ID)
        workspace_token = extract_socket_workspace_token(auth_payload=auth_payload, data_payload=data_payload)
        if workspace_token:
            return workspace_id_for_workspace_token(workspace_token)
        selected_workspace_id = extract_socket_workspace_id(auth_payload=auth_payload, data_payload=data_payload)
        if selected_workspace_id:
            account = self.account_for_auth(auth_payload=auth_payload)
            if account_requires_password_setup(account):
                return None
            if account_workspace_membership(account, selected_workspace_id):
                return selected_workspace_id
            return None
        return None

    def account_for_auth(self, auth_payload: dict | None = None):
        sid = getattr(request, 'sid', None)
        existing = self.state.connection(sid)
        if existing and existing.get('account_id'):
            from aidm_server.database import db
            from aidm_server.models import Account

            return db.session.get(Account, int(existing['account_id']))
        account_token = extract_socket_account_token(auth_payload=auth_payload)
        return account_for_token(account_token)

    def membership_for_auth(self, auth_payload: dict | None = None, workspace_id: str | None = None):
        account = self.account_for_auth(auth_payload=auth_payload)
        if account is None or not workspace_id:
            return None
        if account_requires_password_setup(account):
            return None
        return ensure_account_workspace_membership(account, workspace_id)

    def credential_present_for_auth(
        self,
        auth_payload: dict | None = None,
        data_payload: dict | None = None,
    ) -> bool:
        """Report credential presence without retaining a raw token in socket state."""
        return bool(extract_socket_token(auth_payload=auth_payload, data_payload=data_payload))

    def global_operator_for_auth(
        self,
        auth_payload: dict | None = None,
        data_payload: dict | None = None,
    ) -> bool:
        token = extract_socket_token(auth_payload=auth_payload, data_payload=data_payload)
        return is_global_operator_token(token)

    def connection_has_capability(self, sid: str, capability: Capability) -> bool:
        existing = self.state.connection(sid) or {}
        account_id = coerce_int(existing.get('account_id'))
        capabilities = actor_capabilities(
            account_id=account_id,
            is_workspace_admin=workspace_role_is_admin(str(existing.get('workspace_role') or '')),
            credential_present=bool(existing.get('credential_present')),
            global_operator=bool(existing.get('global_operator')),
        )
        return capability in capabilities

    def connection_account_context(self, sid: str) -> tuple[int | None, bool]:
        existing = self.state.connection(sid) or {}
        account_id = coerce_int(existing.get('account_id'))
        return account_id, workspace_role_is_admin(str(existing.get('workspace_role') or ''))

    def active_player_payloads(self, session_id: int) -> list[dict]:
        return self.state.active_player_payloads(session_id)

    def track_active_player(self, session_id: int, player_data: dict, sid: str) -> bool:
        return self.state.track_active_player(session_id, player_data, sid)

    def release_active_player(self, session_id: int, player_id: int, sid: str) -> bool:
        return self.state.release_active_player(session_id, player_id, sid)

    def player_is_typing(self, session_id: int, player_id: int) -> bool:
        return self.state.player_is_typing(session_id, player_id)

    def set_player_typing(self, session_id: int, player_id: int, sid: str, is_typing: bool) -> bool:
        return self.state.set_player_typing(session_id, player_id, sid, is_typing)

    def clear_connection_binding(
        self,
        sid: str,
        *,
        leave_bound_room: bool,
        leave_room_fn: LeaveRoomFn,
        emit_fn: EmitFn,
    ) -> dict[str, Any] | None:
        connection_record = self.state.unbind_connection(sid)
        if not connection_record:
            return None

        session_id = coerce_int(connection_record.get('session_id'))
        player_id = coerce_int(connection_record.get('player_id'))
        if leave_bound_room and session_id:
            leave_room_fn(str(session_id))
        if session_id and player_id:
            was_typing = self.player_is_typing(session_id, player_id)
            removed_player = self.release_active_player(session_id, player_id, sid)
            if removed_player:
                emit_fn('player_left', {'id': player_id}, room=str(session_id))
                emit_fn('active_players', self.active_player_payloads(session_id), room=str(session_id))
            elif was_typing != self.player_is_typing(session_id, player_id):
                emit_fn('active_players', self.active_player_payloads(session_id), room=str(session_id))
        return self.state.connection(sid)

    def release_disconnect(self, sid: str, *, emit_fn: EmitFn) -> dict[str, Any] | None:
        connection_info = self.state.pop_connection(sid)
        if not connection_info:
            return None

        session_id = coerce_int(connection_info.get('session_id'))
        player_id = coerce_int(connection_info.get('player_id'))
        set_logging_context(session_id=session_id)

        if session_id and player_id:
            was_typing = self.player_is_typing(session_id, player_id)
            removed_player = self.release_active_player(session_id, player_id, sid)
            if removed_player:
                emit_fn('player_left', {'id': player_id}, room=str(session_id))
                emit_fn('active_players', self.active_player_payloads(session_id), room=str(session_id))
            elif was_typing != self.player_is_typing(session_id, player_id):
                emit_fn('active_players', self.active_player_payloads(session_id), room=str(session_id))
        return connection_info
