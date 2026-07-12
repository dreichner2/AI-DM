from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
from threading import Event, Lock, RLock, Thread
import time
from typing import Callable, Protocol
from uuid import uuid4

from flask import current_app, has_app_context
from sqlalchemy import event, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as SqlAlchemySession

from aidm_server.database import db
from aidm_server.time_utils import utc_now


TURN_COORDINATOR_STORE_MEMORY = 'memory'
TURN_COORDINATOR_STORE_DATABASE = 'database'
_HELD_SESSION_IDS: ContextVar[tuple[int, ...]] = ContextVar('aidm_held_turn_coordinator_session_ids', default=())
_HELD_DATABASE_LEASES: ContextVar[tuple['DatabaseTurnLease', ...]] = ContextVar(
    'aidm_held_database_turn_leases',
    default=(),
)


@dataclass
class _SessionLockEntry:
    lock: Lock = field(default_factory=Lock)
    active: int = 0
    last_used: float = field(default_factory=time.monotonic)


class _HeartbeatStopSignal(Protocol):
    def set(self) -> None: ...

    def wait(self, timeout: float | None = None) -> bool: ...


@dataclass(frozen=True)
class _LeaseHeartbeat:
    stop_signal: _HeartbeatStopSignal
    thread: Thread

    def stop_and_join(self) -> None:
        self.stop_signal.set()
        self.thread.join()


class TurnLeaseLostError(RuntimeError):
    """Raised when a database-backed turn lease can no longer fence a commit."""


class SessionTurnTargetMissingError(RuntimeError):
    """Raised when a database lease cannot exist because its session is gone."""

    def __init__(self, session_id: int):
        super().__init__(f'Session {session_id} no longer exists.')
        self.session_id = session_id


@dataclass(frozen=True)
class DatabaseTurnLease:
    coordinator: 'DatabaseSessionTurnCoordinator'
    session_id: int
    owner_token: str
    fencing_token: int


class SessionTurnCoordinator:
    def __init__(self, *, max_idle_seconds: float = 600.0, clock=time.monotonic):
        self._guard = RLock()
        self._locks: dict[int, _SessionLockEntry] = {}
        self._max_idle_seconds = max_idle_seconds
        self._clock = clock

    def _cleanup_idle_locked(self, now: float):
        cutoff = now - self._max_idle_seconds
        for tracked_session_id, entry in list(self._locks.items()):
            if entry.active == 0 and entry.last_used <= cutoff:
                self._locks.pop(tracked_session_id, None)

    def _entry_for_session(self, session_id: int) -> _SessionLockEntry:
        with self._guard:
            now = self._clock()
            self._cleanup_idle_locked(now)
            entry = self._locks.setdefault(session_id, _SessionLockEntry(last_used=now))
            entry.active += 1
            return entry

    @contextmanager
    def serialized(self, session_id: int):
        entry = self._entry_for_session(session_id)
        lock = entry.lock
        wait_started = time.perf_counter()
        lock.acquire()
        wait_ms = (time.perf_counter() - wait_started) * 1000.0
        try:
            yield wait_ms
        finally:
            lock.release()
            with self._guard:
                current = self._locks.get(session_id)
                if current is entry:
                    entry.active = max(0, entry.active - 1)
                    entry.last_used = self._clock()
                    self._cleanup_idle_locked(entry.last_used)

    def discard_session(self, session_id: int) -> bool:
        with self._guard:
            entry = self._locks.get(session_id)
            if entry is None:
                return False
            if entry.active > 0:
                return False
            self._locks.pop(session_id, None)
            return True

    def lock_count(self) -> int:
        with self._guard:
            return len(self._locks)


class DatabaseSessionTurnCoordinator:
    def __init__(
        self,
        *,
        lease_seconds: int = 900,
        poll_interval_seconds: float = 0.05,
        clock=utc_now,
        renewal_interval_seconds: float | None = None,
        heartbeat_signal_factory: Callable[[], _HeartbeatStopSignal] = Event,
    ):
        self.lease_seconds = max(30, int(lease_seconds))
        self.poll_interval_seconds = max(0.01, float(poll_interval_seconds))
        self._clock = clock
        requested_renewal_interval = (
            self.lease_seconds / 3.0
            if renewal_interval_seconds is None
            else max(0.01, float(renewal_interval_seconds))
        )
        self.renewal_interval_seconds = min(requested_renewal_interval, self.lease_seconds / 3.0)
        self._heartbeat_signal_factory = heartbeat_signal_factory

    def _try_acquire(self, session_id: int, owner_token: str) -> int | None:
        from aidm_server.models import Session, SessionTurnLock

        table = SessionTurnLock.__table__
        now = self._clock()
        expires_at = now + timedelta(seconds=self.lease_seconds)
        with db.engine.begin() as connection:
            result = connection.execute(
                update(table)
                .where(table.c.session_id == session_id)
                .where(table.c.expires_at <= now)
                .values(
                    owner_token=owner_token,
                    fencing_token=table.c.fencing_token + 1,
                    acquired_at=now,
                    expires_at=expires_at,
                    updated_at=now,
                )
            )
            if result.rowcount:
                return int(
                    connection.execute(
                        select(table.c.fencing_token).where(table.c.session_id == session_id)
                    ).scalar_one()
                )

        try:
            with db.engine.begin() as connection:
                connection.execute(
                    insert(table).values(
                        session_id=session_id,
                        owner_token=owner_token,
                        fencing_token=1,
                        acquired_at=now,
                        expires_at=expires_at,
                        updated_at=now,
                    )
                )
            return 1
        except IntegrityError:
            # A concurrent hard delete removes the lease through the session
            # foreign-key cascade. Distinguish that terminal state from normal
            # lease contention so waiters do not spin forever trying to insert
            # a lock row whose parent can no longer exist.
            with db.engine.connect() as connection:
                session_exists = connection.execute(
                    select(Session.__table__.c.session_id).where(
                        Session.__table__.c.session_id == session_id,
                    )
                ).first()
            if session_exists is None:
                raise SessionTurnTargetMissingError(session_id) from None
            return None

    def _renew(self, session_id: int, owner_token: str, fencing_token: int) -> bool:
        from aidm_server.models import SessionTurnLock

        table = SessionTurnLock.__table__
        now = self._clock()
        expires_at = now + timedelta(seconds=self.lease_seconds)
        with db.engine.begin() as connection:
            result = connection.execute(
                update(table)
                .where(
                    table.c.session_id == session_id,
                    table.c.owner_token == owner_token,
                    table.c.fencing_token == fencing_token,
                    table.c.expires_at > now,
                )
                .values(expires_at=expires_at, updated_at=now)
            )
            return bool(result.rowcount)

    def _fence_commit(self, session: SqlAlchemySession, lease: DatabaseTurnLease) -> None:
        """Renew and lock the owned lease row in the transaction being committed.

        The conditional update is the fence: once another owner has advanced the
        monotonically increasing token, a stale transaction cannot reach commit.
        A successful update also takes the row lock through transaction commit,
        so a contender cannot take ownership between validation and persistence.
        """

        from aidm_server.models import SessionTurnLock

        table = SessionTurnLock.__table__
        now = self._clock()
        expires_at = now + timedelta(seconds=self.lease_seconds)
        result = session.connection().execute(
            update(table)
            .where(
                table.c.session_id == lease.session_id,
                table.c.owner_token == lease.owner_token,
                table.c.fencing_token == lease.fencing_token,
                table.c.expires_at > now,
            )
            .values(expires_at=expires_at, updated_at=now)
        )
        if result.rowcount != 1:
            raise TurnLeaseLostError(
                f'Turn lease lost for session {lease.session_id}; refusing stale database commit.'
            )

    def _heartbeat_loop(
        self,
        app,
        session_id: int,
        owner_token: str,
        fencing_token: int,
        stop_signal: _HeartbeatStopSignal,
    ) -> None:
        while not stop_signal.wait(self.renewal_interval_seconds):
            try:
                with app.app_context():
                    renewed = self._renew(session_id, owner_token, fencing_token)
            except Exception:
                app.logger.error(
                    'Database turn-lock lease heartbeat renewal failed; retrying while the lease remains valid.'
                )
                continue
            if not renewed:
                app.logger.warning(
                    'Database turn-lock lease heartbeat stopped because ownership changed or the lease expired.'
                )
                return

    def _start_heartbeat(self, session_id: int, owner_token: str, fencing_token: int) -> _LeaseHeartbeat:
        app = current_app._get_current_object()
        stop_signal = self._heartbeat_signal_factory()
        thread = Thread(
            target=self._heartbeat_loop,
            args=(app, session_id, owner_token, fencing_token, stop_signal),
            name=f'aidm-turn-lease-{session_id}',
            daemon=True,
        )
        thread.start()
        return _LeaseHeartbeat(stop_signal=stop_signal, thread=thread)

    def _release(self, session_id: int, owner_token: str, fencing_token: int) -> None:
        from aidm_server.models import SessionTurnLock

        table = SessionTurnLock.__table__
        now = self._clock()
        with db.engine.begin() as connection:
            connection.execute(
                update(table)
                .where(
                    table.c.session_id == session_id,
                    table.c.owner_token == owner_token,
                    table.c.fencing_token == fencing_token,
                )
                .values(expires_at=now, updated_at=now)
            )

    @contextmanager
    def serialized(self, session_id: int):
        owner_token = uuid4().hex
        wait_started = time.perf_counter()
        fencing_token = None
        while fencing_token is None:
            fencing_token = self._try_acquire(session_id, owner_token)
            if fencing_token is not None:
                break
            time.sleep(self.poll_interval_seconds)
        wait_ms = (time.perf_counter() - wait_started) * 1000.0
        lease = DatabaseTurnLease(
            coordinator=self,
            session_id=session_id,
            owner_token=owner_token,
            fencing_token=fencing_token,
        )
        try:
            heartbeat = self._start_heartbeat(session_id, owner_token, fencing_token)
        except Exception:
            self._release(session_id, owner_token, fencing_token)
            raise RuntimeError('Unable to start the database turn-lock lease heartbeat.') from None
        held_leases = _HELD_DATABASE_LEASES.get()
        lease_context_token = _HELD_DATABASE_LEASES.set((*held_leases, lease))
        try:
            yield wait_ms
        finally:
            _HELD_DATABASE_LEASES.reset(lease_context_token)
            try:
                heartbeat.stop_and_join()
            finally:
                self._release(session_id, owner_token, fencing_token)

    def discard_session(self, session_id: int) -> bool:
        from aidm_server.models import SessionTurnLock

        table = SessionTurnLock.__table__
        now = self._clock()
        with db.engine.begin() as connection:
            inactive_lock = connection.execute(
                select(table.c.session_id).where(
                    table.c.session_id == session_id,
                    table.c.expires_at <= now,
                )
            ).first()
            # Keep the inactive row as a generation tombstone. Session deletion
            # removes it through the foreign-key cascade; ordinary archive and
            # restore cycles must not reset the fencing sequence.
            return inactive_lock is not None

    def lock_count(self) -> int:
        from aidm_server.models import SessionTurnLock

        table = SessionTurnLock.__table__
        now = self._clock()
        with db.engine.begin() as connection:
            return int(
                connection.execute(
                    select(func.count()).select_from(table).where(table.c.expires_at > now)
                ).scalar_one()
            )


@event.listens_for(SqlAlchemySession, 'before_commit')
def _fence_held_database_turn_leases(session: SqlAlchemySession) -> None:
    for lease in _HELD_DATABASE_LEASES.get():
        lease.coordinator._fence_commit(session, lease)


class ConfiguredSessionTurnCoordinator:
    def __init__(self):
        self._memory = SessionTurnCoordinator()

    def _active_coordinator(self):
        if has_app_context() and current_app.config.get('AIDM_TURN_COORDINATOR_STORE') == TURN_COORDINATOR_STORE_DATABASE:
            return DatabaseSessionTurnCoordinator(
                lease_seconds=current_app.config.get('AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS', 900),
                poll_interval_seconds=current_app.config.get('AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS', 50) / 1000.0,
            )
        return self._memory

    @contextmanager
    def serialized(self, session_id: int):
        held_session_ids = _HELD_SESSION_IDS.get()
        if session_id in held_session_ids:
            yield 0.0
            return
        with self._active_coordinator().serialized(session_id) as wait_ms:
            token = _HELD_SESSION_IDS.set((*held_session_ids, session_id))
            try:
                yield wait_ms
            finally:
                _HELD_SESSION_IDS.reset(token)

    @contextmanager
    def serialized_many(self, session_ids):
        normalized_ids = sorted({int(session_id) for session_id in session_ids if session_id is not None})
        waits: dict[int, float] = {}
        with ExitStack() as stack:
            for session_id in normalized_ids:
                waits[session_id] = stack.enter_context(self.serialized(session_id))
            yield waits

    def discard_session(self, session_id: int) -> bool:
        return self._active_coordinator().discard_session(session_id)

    def held_session_ids(self) -> frozenset[int]:
        return frozenset(_HELD_SESSION_IDS.get())

    def holds_all(self, session_ids) -> bool:
        held = self.held_session_ids()
        return all(int(session_id) in held for session_id in session_ids)

    def lock_count(self) -> int:
        return self._active_coordinator().lock_count()


session_turn_coordinator = ConfiguredSessionTurnCoordinator()
