"""Transport-agnostic access checks shared by Socket.IO event handlers."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Callable

from aidm_server.rate_limiter import RateLimitResult


ADMIN_NOT_CONFIGURED_MESSAGE = 'Admin mode is not configured on this backend.'
ADMIN_UNAUTHORIZED_MESSAGE = 'Invalid admin passcode.'


@dataclass(frozen=True)
class AdminSocketAuthorization:
    allowed: bool
    error_code: str | None = None
    message: str | None = None
    reset_in_seconds: int | None = None


def socket_message_rate_key(workspace_id: str, session_id: int, player_id: int) -> str:
    """Return the stable per-player bucket used by normal socket messages."""
    return f'{workspace_id}:{session_id}:{player_id}'


def admin_attempt_bucket_key(workspace_id: str, remote_address: str | None) -> str:
    """Bucket privileged attempts by workspace and address without including credentials."""
    normalized_remote_address = str(remote_address or 'unknown').strip()
    return f'admin-passcode:{workspace_id}:{normalized_remote_address}'


def admin_passcode_is_valid(configured_passcode: str | None, data: dict | None) -> bool:
    """Compare a supplied admin passcode without leaking timing information."""
    configured = str(configured_passcode or '').strip()
    supplied = str((data or {}).get('admin_passcode') or '').strip()
    if not configured or not supplied:
        return False
    return secrets.compare_digest(supplied, configured)


def authorize_admin_socket_action(
    *,
    configured_passcode: str | None,
    data: dict | None,
    workspace_id: str,
    remote_address: str | None,
    allow_rate_key: Callable[[str], RateLimitResult],
    passcode_validator: Callable[[dict | None], bool],
) -> AdminSocketAuthorization:
    """Apply the admin configuration, rate-limit, and passcode checks in order."""
    if not configured_passcode:
        return AdminSocketAuthorization(
            allowed=False,
            error_code='admin_not_configured',
            message=ADMIN_NOT_CONFIGURED_MESSAGE,
        )

    limit_result = allow_rate_key(admin_attempt_bucket_key(workspace_id, remote_address))
    if not limit_result.allowed:
        return AdminSocketAuthorization(
            allowed=False,
            error_code='rate_limited',
            reset_in_seconds=limit_result.reset_in_seconds,
        )

    if not passcode_validator(data):
        return AdminSocketAuthorization(
            allowed=False,
            error_code='admin_unauthorized',
            message=ADMIN_UNAUTHORIZED_MESSAGE,
        )

    return AdminSocketAuthorization(allowed=True)
