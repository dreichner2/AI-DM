"""Fixed-window rate limiting with pluggable storage backends."""

from __future__ import annotations

import hashlib
import hmac
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Protocol

from sqlalchemy import delete, func, insert, select


RATE_LIMIT_STORE_MEMORY = 'memory'
RATE_LIMIT_STORE_DATABASE = 'database'
SUPPORTED_RATE_LIMIT_STORES = {RATE_LIMIT_STORE_MEMORY, RATE_LIMIT_STORE_DATABASE}
MAX_BUCKET_KEY_LENGTH = 512
POSTGRES_ADVISORY_LOCK_NAMESPACE = 'aidm-rate-limit:'
PREAUTH_RATE_LIMIT_NAMESPACE = 'aidm-preauth-rate-limit:v1'


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_in_seconds: int


class RateLimitStore(Protocol):
    def hit(self, key: str, *, now: datetime, limit: int, window_seconds: int) -> RateLimitResult:
        """Record or reject one request for *key* in the current fixed window."""


def normalize_rate_limit_key(key: str) -> str:
    normalized = str(key or 'unknown').strip() or 'unknown'
    if len(normalized) <= MAX_BUCKET_KEY_LENGTH:
        return normalized

    digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    prefix_length = MAX_BUCKET_KEY_LENGTH - len(digest) - 1
    return f'{normalized[:prefix_length]}:{digest}'


def privacy_safe_preauth_bucket(
    *,
    secret_key: str | bytes,
    dimension: str,
    action: str,
    client_ip: str,
    normalized_target: str,
) -> str:
    """Build a stable opaque pre-auth bucket without persisting credentials.

    ``FLASK_SECRET_KEY`` is shared by production workers, so the HMAC remains
    stable across processes while usernames, workspace names, tokens, and IP
    addresses never appear in the database-backed bucket key.
    """
    if isinstance(secret_key, bytes):
        key_bytes = secret_key
    else:
        key_bytes = str(secret_key or '').encode('utf-8')
    if not key_bytes:
        raise ValueError('A non-empty secret key is required for pre-auth rate limiting.')

    normalized_dimension = str(dimension or 'compound').strip().casefold() or 'compound'
    normalized_action = str(action or 'credential').strip().casefold() or 'credential'
    message = '\x00'.join(
        (
            PREAUTH_RATE_LIMIT_NAMESPACE,
            normalized_dimension,
            normalized_action,
            str(client_ip or 'unknown').strip().casefold() or 'unknown',
            str(normalized_target or 'unknown').strip().casefold() or 'unknown',
        )
    ).encode('utf-8')
    digest = hmac.new(key_bytes, message, hashlib.sha256).hexdigest()
    return f'preauth:{normalized_dimension}:{normalized_action}:{digest}'


def postgres_advisory_lock_key(key: str) -> int:
    """Return a stable signed 64-bit key for PostgreSQL advisory locking."""
    digest = hashlib.sha256(f'{POSTGRES_ADVISORY_LOCK_NAMESPACE}{key}'.encode('utf-8')).digest()
    return int.from_bytes(digest[:8], byteorder='big', signed=True)


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reset_seconds_from_oldest(oldest: datetime | str | None, *, now: datetime, window_seconds: int) -> int:
    oldest = _coerce_datetime(oldest)
    if oldest is None:
        return window_seconds
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    reset_at = oldest + timedelta(seconds=window_seconds)
    return max(0, int((reset_at - now).total_seconds()))


class InMemoryRateLimitStore:
    def __init__(self):
        self._events: dict[str, deque[datetime]] = {}
        self._lock = Lock()
        self._next_gc_at = datetime.min.replace(tzinfo=timezone.utc)

    @staticmethod
    def _prune_queue(queue: deque[datetime], window_start: datetime):
        while queue and queue[0] < window_start:
            queue.popleft()

    def _collect_garbage(self, window_start: datetime):
        stale_keys: list[str] = []
        for key, queue in self._events.items():
            self._prune_queue(queue, window_start)
            if not queue:
                stale_keys.append(key)
        for key in stale_keys:
            self._events.pop(key, None)

    def hit(self, key: str, *, now: datetime, limit: int, window_seconds: int) -> RateLimitResult:
        window_start = now - timedelta(seconds=window_seconds)

        with self._lock:
            if now >= self._next_gc_at:
                self._collect_garbage(window_start)
                self._next_gc_at = now + timedelta(seconds=window_seconds)

            queue = self._events.get(key)
            if queue is None:
                queue = deque()
                self._events[key] = queue
            else:
                self._prune_queue(queue, window_start)

            if len(queue) >= limit:
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    reset_in_seconds=_reset_seconds_from_oldest(queue[0], now=now, window_seconds=window_seconds),
                )

            queue.append(now)
            return RateLimitResult(
                allowed=True,
                remaining=max(0, limit - len(queue)),
                reset_in_seconds=window_seconds,
            )


class DatabaseRateLimitStore:
    """Store rate-limit events in the configured SQLAlchemy database."""

    def __init__(
        self,
        *,
        retention_window_seconds: int,
        gc_interval_seconds: int | None = None,
    ):
        try:
            retention_window_seconds = int(retention_window_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError('Database rate-limit retention must be a positive number of seconds.') from exc
        if retention_window_seconds < 1:
            raise ValueError('Database rate-limit retention must be a positive number of seconds.')

        self.retention_window_seconds = retention_window_seconds
        self._gc_interval_seconds = gc_interval_seconds
        self._next_gc_at = datetime.min.replace(tzinfo=timezone.utc)
        self._gc_lock = Lock()
        self._hit_lock = Lock()

    def _validate_window_seconds(self, window_seconds: int) -> int:
        normalized_window_seconds = max(1, int(window_seconds))
        if normalized_window_seconds > self.retention_window_seconds:
            raise ValueError(
                f'Rate-limit window ({normalized_window_seconds} seconds) exceeds database '
                f'retention horizon ({self.retention_window_seconds} seconds).'
            )
        return normalized_window_seconds

    def _collect_garbage(self, connection, table, *, now: datetime, window_seconds: int):
        if now < self._next_gc_at:
            return
        with self._gc_lock:
            if now < self._next_gc_at:
                return
            retention_start = now - timedelta(seconds=self.retention_window_seconds)
            connection.execute(delete(table).where(table.c.created_at < retention_start))
            interval = self._gc_interval_seconds or window_seconds
            self._next_gc_at = now + timedelta(seconds=max(1, int(interval)))

    @staticmethod
    def _lock_bucket_for_transaction(connection, key: str) -> None:
        if connection.dialect.name != 'postgresql':
            return
        connection.execute(select(func.pg_advisory_xact_lock(postgres_advisory_lock_key(key))))

    def hit(self, key: str, *, now: datetime, limit: int, window_seconds: int) -> RateLimitResult:
        from aidm_server.database import db
        from aidm_server.models import RateLimitEvent

        window_seconds = self._validate_window_seconds(window_seconds)
        window_start = now - timedelta(seconds=window_seconds)
        table = RateLimitEvent.__table__

        with self._hit_lock:
            with db.engine.begin() as connection:
                self._lock_bucket_for_transaction(connection, key)
                self._collect_garbage(connection, table, now=now, window_seconds=window_seconds)

                matching_events = table.c.bucket_key == key
                within_window = table.c.created_at >= window_start
                current_count = connection.execute(
                    select(func.count()).select_from(table).where(matching_events, within_window)
                ).scalar_one()

                if current_count >= limit:
                    oldest = connection.execute(
                        select(func.min(table.c.created_at)).where(matching_events, within_window)
                    ).scalar_one()
                    return RateLimitResult(
                        allowed=False,
                        remaining=0,
                        reset_in_seconds=_reset_seconds_from_oldest(oldest, now=now, window_seconds=window_seconds),
                    )

                connection.execute(insert(table).values(bucket_key=key, created_at=now))
                return RateLimitResult(
                    allowed=True,
                    remaining=max(0, limit - int(current_count) - 1),
                    reset_in_seconds=window_seconds,
                )


def build_rate_limiter(
    *,
    limit: int,
    window_seconds: int,
    store_name: str = RATE_LIMIT_STORE_MEMORY,
    retention_window_seconds: int | None = None,
) -> FixedWindowRateLimiter:
    normalized_store = str(store_name or RATE_LIMIT_STORE_MEMORY).strip().lower()
    if normalized_store == RATE_LIMIT_STORE_MEMORY:
        store: RateLimitStore = InMemoryRateLimitStore()
    elif normalized_store == RATE_LIMIT_STORE_DATABASE:
        if retention_window_seconds is None:
            raise ValueError('Database rate-limit stores require a retention window.')
        store = DatabaseRateLimitStore(retention_window_seconds=retention_window_seconds)
    else:
        expected = ', '.join(sorted(SUPPORTED_RATE_LIMIT_STORES))
        raise ValueError(f'Unsupported rate-limit store "{normalized_store}". Expected one of: {expected}.')

    return FixedWindowRateLimiter(limit=limit, window_seconds=window_seconds, store=store)


class FixedWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int, store: RateLimitStore | None = None):
        self.limit = max(1, int(limit))
        self.window_seconds = max(1, int(window_seconds))
        self.store = store or InMemoryRateLimitStore()
        if isinstance(self.store, DatabaseRateLimitStore):
            self.store._validate_window_seconds(self.window_seconds)
        if isinstance(self.store, InMemoryRateLimitStore):
            self._events = self.store._events

    def allow(self, key: str) -> RateLimitResult:
        now = datetime.now(timezone.utc)
        return self.store.hit(
            normalize_rate_limit_key(key),
            now=now,
            limit=self.limit,
            window_seconds=self.window_seconds,
        )
