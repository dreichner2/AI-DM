"""Authentication helpers for API and Socket.IO requests."""

from __future__ import annotations

import hashlib
import re
import secrets

from flask import current_app, request
from werkzeug.security import check_password_hash, generate_password_hash


DEFAULT_WORKSPACE_ID = "owner"
WORKSPACE_TOKEN_HEADER = "X-AIDM-Workspace-Token"
WORKSPACE_ID_HEADER = "X-AIDM-Workspace-Id"
ACCOUNT_TOKEN_BYTES = 32
_WORKSPACE_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")
_USERNAME_RE = re.compile(r"[^a-z0-9_.-]+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_workspace_id(value: str | None) -> str:
    workspace_id = _WORKSPACE_ID_RE.sub("_", str(value or "").strip()).strip("_")
    return (workspace_id or DEFAULT_WORKSPACE_ID)[:80]


def workspace_id_from_name(value: str | None) -> str:
    workspace_id = _WORKSPACE_ID_RE.sub("_", str(value or "").strip()).strip("_")
    return workspace_id[:80]


def normalize_workspace_name(value: str | None) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "").strip())[:120]


def normalize_workspace_name_key(value: str | None) -> str:
    return normalize_workspace_name(value).casefold()


def normalize_username(value: str | None) -> str:
    username = _USERNAME_RE.sub("_", str(value or "").strip().lower()).strip("_.-")
    return username[:80]


def account_display_name(account) -> str:
    first_name = str(getattr(account, "first_name", "") or "").strip()
    last_name = str(getattr(account, "last_name", "") or "").strip()
    display_name = " ".join(part for part in (first_name, last_name) if part).strip()
    return display_name or str(getattr(account, "username", "") or "").strip() or "Local Player"


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_account_token() -> str:
    return secrets.token_urlsafe(ACCOUNT_TOKEN_BYTES)


def generate_workspace_token() -> str:
    return secrets.token_urlsafe(ACCOUNT_TOKEN_BYTES)


def password_hash_for(value: str | None) -> str | None:
    password = str(value or "").strip()
    return generate_password_hash(password) if password else None


def password_hash_matches(password_hash: str | None, password: str | None) -> bool:
    stored_hash = str(password_hash or "")
    if not stored_hash:
        return False
    supplied = str(password or "")
    return bool(supplied) and check_password_hash(stored_hash, supplied)


def password_matches(account, password: str | None) -> bool:
    return password_hash_matches(str(getattr(account, "password_hash", "") or ""), password)


def account_requires_password_setup(account) -> bool:
    return bool(account) and not bool(str(getattr(account, "password_hash", "") or "").strip())


def account_for_token(token: str | None):
    raw_token = str(token or "").strip()
    if not raw_token:
        return None
    from aidm_server.models import Account

    return Account.query.filter_by(account_token_hash=hash_secret(raw_token)).first()


def _configured_token_workspaces() -> dict[str, str]:
    raw_mapping = current_app.config.get("AIDM_API_AUTH_TOKEN_WORKSPACES", {})
    if not isinstance(raw_mapping, dict):
        return {}
    return {
        str(token).strip(): normalize_workspace_id(str(workspace_id))
        for token, workspace_id in raw_mapping.items()
        if str(token).strip()
    }


def _configured_tokens() -> set[str]:
    tokens = current_app.config.get("AIDM_API_AUTH_TOKENS", [])
    configured = {token.strip() for token in tokens if isinstance(token, str) and token.strip()}
    configured.update(_configured_token_workspaces().keys())
    return configured


def auth_required() -> bool:
    return bool(current_app.config.get("AIDM_AUTH_REQUIRED", False))


def extract_bearer_token(auth_header: str | None) -> str | None:
    if not auth_header:
        return None
    header = auth_header.strip()
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    return token or None


def is_token_authorized(token: str | None) -> bool:
    if not token:
        return not auth_required()
    valid_tokens = _configured_tokens()
    if token in valid_tokens:
        return True
    return not auth_required()


def workspace_id_for_workspace_token(token: str | None) -> str | None:
    raw_token = str(token or "").strip()
    workspace_mapping = _configured_token_workspaces()
    if raw_token and raw_token in workspace_mapping:
        return workspace_mapping[raw_token]
    valid_tokens = _configured_tokens()
    if raw_token and raw_token in valid_tokens:
        return DEFAULT_WORKSPACE_ID
    if raw_token:
        from aidm_server.models import Workspace

        workspace = Workspace.query.filter_by(token_hash=hash_secret(raw_token)).first()
        if workspace:
            return workspace.workspace_id
    if auth_required():
        return None
    return DEFAULT_WORKSPACE_ID


def workspace_id_for_token(token: str | None) -> str | None:
    return workspace_id_for_workspace_token(token)


def request_account_token() -> str | None:
    return extract_bearer_token(request.headers.get("Authorization"))


def request_account():
    return account_for_token(request_account_token())


def request_workspace_token(account_token: str | None = None) -> str | None:
    header_token = str(request.headers.get(WORKSPACE_TOKEN_HEADER) or "").strip()
    if header_token:
        return header_token

    bearer_token = request_account_token()
    if not bearer_token:
        return None

    checked_account_token = account_token if account_token is not None else bearer_token
    if checked_account_token and account_for_token(checked_account_token):
        return None
    return bearer_token


def _request_workspace_id_header() -> str | None:
    raw_workspace_id = str(request.headers.get(WORKSPACE_ID_HEADER) or "").strip()
    return normalize_workspace_id(raw_workspace_id) if raw_workspace_id else None


def request_workspace_id() -> str | None:
    workspace_token = request_workspace_token()
    if workspace_token:
        return workspace_id_for_workspace_token(workspace_token)

    selected_workspace_id = _request_workspace_id_header()
    if selected_workspace_id:
        account = request_account()
        if account_workspace_membership(account, selected_workspace_id):
            return selected_workspace_id
        return None

    if auth_required():
        return None
    return DEFAULT_WORKSPACE_ID


def account_workspace_membership(account, workspace_id: str | None):
    if account is None or not workspace_id:
        return None
    from aidm_server.models import AccountWorkspaceMembership

    return AccountWorkspaceMembership.query.filter_by(
        account_id=account.account_id,
        workspace_id=workspace_id,
    ).first()


def ensure_account_workspace_membership(account, workspace_id: str | None):
    if account is None or not workspace_id:
        return None
    from aidm_server.database import db
    from aidm_server.models import AccountWorkspaceMembership

    membership = account_workspace_membership(account, workspace_id)
    if membership:
        return membership

    membership = AccountWorkspaceMembership(
        account_id=account.account_id,
        workspace_id=workspace_id,
        role='player',
    )
    db.session.add(membership)
    db.session.flush()
    return membership


def claim_legacy_players_for_account(account, workspace_id: str | None) -> list[int]:
    if account is None or not workspace_id:
        return []
    from aidm_server.database import db
    from aidm_server.models import Player

    match_names = {
        account_display_name(account).strip().lower(),
        str(getattr(account, "username", "") or "").strip().lower(),
        str(getattr(account, "first_name", "") or "").strip().lower(),
    }
    match_names = {name for name in match_names if name}
    if not match_names:
        return []

    claimed: list[int] = []
    legacy_players = Player.query.filter_by(workspace_id=workspace_id, account_id=None).all()
    for player in legacy_players:
        if str(player.name or "").strip().lower() not in match_names:
            continue
        player.account_id = account.account_id
        player.name = account_display_name(account)
        claimed.append(player.player_id)
    if claimed:
        db.session.flush()
    return claimed


def workspace_role_is_admin(role: str | None) -> bool:
    return str(role or "").strip().lower() == "admin"


def extract_socket_account_token(auth_payload: dict | None = None) -> str | None:
    if isinstance(auth_payload, dict):
        token = auth_payload.get("account_token") or auth_payload.get("accountToken")
        if token:
            return str(token)
        legacy_token = auth_payload.get("token") or auth_payload.get("auth_token")
        if legacy_token and account_for_token(str(legacy_token)):
            return str(legacy_token)
    header_token = extract_bearer_token(request.headers.get("Authorization"))
    if header_token and account_for_token(header_token):
        return header_token
    return None


def extract_socket_workspace_token(auth_payload: dict | None = None, data_payload: dict | None = None) -> str | None:
    del data_payload
    if isinstance(auth_payload, dict):
        token = auth_payload.get("workspace_token") or auth_payload.get("workspaceToken")
        if token:
            return str(token)
        legacy_token = auth_payload.get("token") or auth_payload.get("auth_token")
        if legacy_token and not account_for_token(str(legacy_token)):
            return str(legacy_token)
    header_token = str(request.headers.get(WORKSPACE_TOKEN_HEADER) or "").strip()
    if header_token:
        return header_token
    bearer_token = extract_bearer_token(request.headers.get("Authorization"))
    if bearer_token and not account_for_token(bearer_token):
        return bearer_token
    return None


def extract_socket_workspace_id(auth_payload: dict | None = None, data_payload: dict | None = None) -> str | None:
    for payload in (data_payload, auth_payload):
        if not isinstance(payload, dict):
            continue
        workspace_id = payload.get("workspace_id") or payload.get("workspaceId")
        if workspace_id:
            return normalize_workspace_id(str(workspace_id))
    header_workspace_id = str(request.headers.get(WORKSPACE_ID_HEADER) or "").strip()
    return normalize_workspace_id(header_workspace_id) if header_workspace_id else None


def request_is_authorized() -> bool:
    return request_workspace_id() is not None


def extract_socket_token(auth_payload: dict | None = None, data_payload: dict | None = None) -> str | None:
    """Legacy token extractor kept for tests and diagnostics."""
    workspace_token = extract_socket_workspace_token(auth_payload=auth_payload, data_payload=data_payload)
    if workspace_token:
        return workspace_token
    return extract_socket_account_token(auth_payload=auth_payload)


def is_account_token_authorized_for_workspace(account_token: str | None, workspace_token: str | None) -> bool:
    account = account_for_token(account_token)
    workspace_id = workspace_id_for_workspace_token(workspace_token)
    return bool(account and workspace_id)


def _legacy_configured_tokens() -> set[str]:
    valid_tokens = _configured_tokens()
    if not valid_tokens:
        return set()
    return valid_tokens


def request_auth_token() -> str | None:
    return extract_bearer_token(request.headers.get("Authorization"))
