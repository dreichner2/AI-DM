from __future__ import annotations

from datetime import timedelta
import logging
import threading
import time

from flask import has_app_context, has_request_context
import pytest
from sqlalchemy import select, update

from aidm_server.database import db
from aidm_server.models import SessionLogEntry, SessionTurnLock
from aidm_server.time_utils import utc_now
from aidm_server.turn_coordinator import (
    ConfiguredSessionTurnCoordinator,
    DatabaseSessionTurnCoordinator,
    SessionTurnCoordinator,
    SessionTurnTargetMissingError,
    TurnLeaseLostError,
)
from tests.helpers import seed_world_campaign_player_session


class _ControlledHeartbeatSignal:
    def __init__(self):
        self._condition = threading.Condition()
        self._stopped = False
        self._renewal_permits = 0
        self.wait_calls = 0

    def set(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()

    def wait(self, _timeout: float | None = None) -> bool:
        with self._condition:
            self.wait_calls += 1
            self._condition.notify_all()
            awakened = self._condition.wait_for(
                lambda: self._stopped or self._renewal_permits > 0,
                timeout=1.0,
            )
            if not awakened or self._stopped:
                return True
            self._renewal_permits -= 1
            return False

    def allow_renewal(self) -> None:
        with self._condition:
            self._renewal_permits += 1
            self._condition.notify_all()

    def wait_until_waiting(self, expected_calls: int = 1) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: self.wait_calls >= expected_calls, timeout=1.0)

    @property
    def stopped(self) -> bool:
        with self._condition:
            return self._stopped


def _heartbeat_signal_factory():
    signals: list[_ControlledHeartbeatSignal] = []

    def factory() -> _ControlledHeartbeatSignal:
        signal = _ControlledHeartbeatSignal()
        signals.append(signal)
        return signal

    return signals, factory


def test_session_turn_coordinator_discards_idle_session_lock():
    coordinator = SessionTurnCoordinator()

    with coordinator.serialized(7):
        pass

    assert coordinator.lock_count() == 1
    assert coordinator.discard_session(7) is True
    assert coordinator.lock_count() == 0


def test_session_turn_coordinator_keeps_active_session_lock():
    coordinator = SessionTurnCoordinator()

    with coordinator.serialized(7):
        assert coordinator.discard_session(7) is False
        assert coordinator.lock_count() == 1

    assert coordinator.discard_session(7) is True


def test_session_turn_coordinator_prunes_idle_locks():
    now = 1000.0
    coordinator = SessionTurnCoordinator(max_idle_seconds=10.0, clock=lambda: now)

    with coordinator.serialized(1):
        pass
    assert coordinator.lock_count() == 1

    now = 1011.0
    with coordinator.serialized(2):
        pass

    assert coordinator.lock_count() == 1


def test_database_session_turn_coordinator_serializes_across_instances(app):
    ids = seed_world_campaign_player_session(app)
    session_id = ids['session_id']
    first = DatabaseSessionTurnCoordinator(poll_interval_seconds=0.01)
    second = DatabaseSessionTurnCoordinator(poll_interval_seconds=0.01)
    entered: list[float] = []

    def contender():
        with app.app_context():
            with second.serialized(session_id) as wait_ms:
                entered.append(wait_ms)

    with app.app_context():
        with first.serialized(session_id):
            thread = threading.Thread(target=contender)
            thread.start()
            time.sleep(0.05)
            assert entered == []

        thread.join(timeout=2)
        assert not thread.is_alive()
        assert entered and entered[0] >= 40
        assert first.lock_count() == 0


def test_database_session_turn_coordinator_reclaims_expired_lock(app):
    ids = seed_world_campaign_player_session(app)
    session_id = ids['session_id']
    now = utc_now()
    with app.app_context():
        db.session.add(
            SessionTurnLock(
                session_id=session_id,
                owner_token='stale-owner',
                acquired_at=now - timedelta(minutes=20),
                updated_at=now - timedelta(minutes=20),
                expires_at=now - timedelta(minutes=1),
            )
        )
        db.session.commit()

        coordinator = DatabaseSessionTurnCoordinator(lease_seconds=30, poll_interval_seconds=0.01)
        with coordinator.serialized(session_id):
            lock = db.session.get(SessionTurnLock, session_id)
            assert lock is not None
            assert lock.owner_token != 'stale-owner'


def test_database_session_turn_coordinator_fails_fast_when_session_was_deleted(app):
    coordinator = DatabaseSessionTurnCoordinator(poll_interval_seconds=0.01)

    with app.app_context(), pytest.raises(SessionTurnTargetMissingError) as exc_info:
        with coordinator.serialized(999_999):
            raise AssertionError('A missing session must not acquire a database lease.')

    assert exc_info.value.session_id == 999_999


def test_database_session_turn_coordinator_advances_fencing_token_on_each_acquisition(app):
    ids = seed_world_campaign_player_session(app)
    session_id = ids['session_id']
    coordinator = DatabaseSessionTurnCoordinator(poll_interval_seconds=0.01)

    with app.app_context():
        with coordinator.serialized(session_id):
            with db.engine.connect() as connection:
                first_token = connection.execute(
                    select(SessionTurnLock.fencing_token).where(SessionTurnLock.session_id == session_id)
                ).scalar_one()

        assert coordinator.lock_count() == 0
        assert coordinator.discard_session(session_id) is True
        with db.engine.connect() as connection:
            released_token = connection.execute(
                select(SessionTurnLock.fencing_token).where(SessionTurnLock.session_id == session_id)
            ).scalar_one()
        assert released_token == first_token

        with coordinator.serialized(session_id):
            with db.engine.connect() as connection:
                second_token = connection.execute(
                    select(SessionTurnLock.fencing_token).where(SessionTurnLock.session_id == session_id)
                ).scalar_one()

        assert first_token == 1
        assert second_token == first_token + 1


def test_database_session_turn_coordinator_fences_each_owned_commit(app):
    ids = seed_world_campaign_player_session(app)
    session_id = ids['session_id']
    coordinator = DatabaseSessionTurnCoordinator(lease_seconds=30, poll_interval_seconds=0.01)

    with app.app_context():
        with coordinator.serialized(session_id):
            db.session.add(
                SessionLogEntry(
                    session_id=session_id,
                    message='fenced owned commit',
                    entry_type='system',
                )
            )
            db.session.commit()

            with db.engine.connect() as connection:
                lock_row = connection.execute(
                    select(
                        SessionTurnLock.fencing_token,
                        SessionTurnLock.expires_at,
                    ).where(SessionTurnLock.session_id == session_id)
                ).one()
            assert lock_row.fencing_token == 1
            assert lock_row.expires_at > utc_now().replace(tzinfo=None)

        assert SessionLogEntry.query.filter_by(
            session_id=session_id,
            message='fenced owned commit',
        ).count() == 1


def test_database_session_turn_coordinator_rejects_stale_commit_after_generation_advances(app):
    ids = seed_world_campaign_player_session(app)
    session_id = ids['session_id']
    coordinator = DatabaseSessionTurnCoordinator(lease_seconds=30, poll_interval_seconds=0.01)
    replacement_expiry = utc_now() + timedelta(minutes=1)

    with app.app_context():
        with coordinator.serialized(session_id):
            with db.engine.begin() as connection:
                original_owner = connection.execute(
                    select(SessionTurnLock.owner_token).where(SessionTurnLock.session_id == session_id)
                ).scalar_one()
                connection.execute(
                    update(SessionTurnLock.__table__)
                    .where(SessionTurnLock.session_id == session_id)
                    .values(
                        fencing_token=SessionTurnLock.fencing_token + 1,
                        expires_at=replacement_expiry,
                        updated_at=utc_now(),
                    )
                )

            db.session.add(
                SessionLogEntry(
                    session_id=session_id,
                    message='must not survive stale lease',
                    entry_type='system',
                )
            )
            with pytest.raises(TurnLeaseLostError, match='refusing stale database commit'):
                db.session.commit()
            db.session.rollback()

        assert SessionLogEntry.query.filter_by(
            session_id=session_id,
            message='must not survive stale lease',
        ).count() == 0
        db.session.expire_all()
        lock = db.session.get(SessionTurnLock, session_id)
        assert lock is not None
        assert lock.owner_token == original_owner
        assert lock.fencing_token == 2


def test_database_session_turn_coordinator_renews_owned_lease_in_isolated_app_context(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    session_id = ids['session_id']
    now = [utc_now()]
    signals, signal_factory = _heartbeat_signal_factory()
    coordinator = DatabaseSessionTurnCoordinator(
        lease_seconds=30,
        clock=lambda: now[0],
        heartbeat_signal_factory=signal_factory,
    )
    original_renew = coordinator._renew
    original_heartbeat_loop = coordinator._heartbeat_loop
    renewed = threading.Event()
    renewal_contexts: list[tuple[bool, bool]] = []
    heartbeat_exit_contexts: list[tuple[bool, bool]] = []

    def tracked_renew(tracked_session_id, owner_token, fencing_token):
        renewal_contexts.append((has_app_context(), has_request_context()))
        result = original_renew(tracked_session_id, owner_token, fencing_token)
        renewed.set()
        return result

    def tracked_heartbeat_loop(*args):
        original_heartbeat_loop(*args)
        heartbeat_exit_contexts.append((has_app_context(), has_request_context()))

    monkeypatch.setattr(coordinator, '_renew', tracked_renew)
    monkeypatch.setattr(coordinator, '_heartbeat_loop', tracked_heartbeat_loop)

    with app.app_context():
        with coordinator.serialized(session_id):
            signal = signals[0]
            assert signal.wait_until_waiting()
            lock = db.session.get(SessionTurnLock, session_id)
            assert lock is not None
            initial_expiry = lock.expires_at
            initial_owner = lock.owner_token

            now[0] += timedelta(seconds=5)
            signal.allow_renewal()
            assert renewed.wait(timeout=1.0)

            db.session.expire_all()
            lock = db.session.get(SessionTurnLock, session_id)
            assert lock is not None
            assert lock.owner_token == initial_owner
            assert lock.expires_at > initial_expiry
            assert lock.expires_at == (now[0] + timedelta(seconds=30)).replace(tzinfo=None)

        assert signal.stopped is True
        assert renewal_contexts == [(True, False)]
        assert heartbeat_exit_contexts == [(False, False)]
        assert coordinator.lock_count() == 0


def test_database_session_turn_coordinator_stops_after_ownership_loss(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    session_id = ids['session_id']
    now = utc_now()
    signals, signal_factory = _heartbeat_signal_factory()
    coordinator = DatabaseSessionTurnCoordinator(
        lease_seconds=30,
        clock=lambda: now,
        heartbeat_signal_factory=signal_factory,
    )
    original_renew = coordinator._renew
    original_heartbeat_loop = coordinator._heartbeat_loop
    renewal_results: list[bool] = []
    heartbeat_done = threading.Event()

    def tracked_renew(tracked_session_id, owner_token, fencing_token):
        result = original_renew(tracked_session_id, owner_token, fencing_token)
        renewal_results.append(result)
        return result

    def tracked_heartbeat_loop(*args):
        try:
            original_heartbeat_loop(*args)
        finally:
            heartbeat_done.set()

    monkeypatch.setattr(coordinator, '_renew', tracked_renew)
    monkeypatch.setattr(coordinator, '_heartbeat_loop', tracked_heartbeat_loop)

    with app.app_context():
        with coordinator.serialized(session_id):
            signal = signals[0]
            assert signal.wait_until_waiting()
            with db.engine.begin() as connection:
                connection.execute(
                    update(SessionTurnLock.__table__)
                    .where(SessionTurnLock.session_id == session_id)
                    .values(
                        owner_token='replacement-owner',
                        fencing_token=SessionTurnLock.fencing_token + 1,
                        expires_at=now + timedelta(minutes=1),
                        updated_at=now,
                    )
                )

            signal.allow_renewal()
            assert heartbeat_done.wait(timeout=1.0)
            assert renewal_results == [False]
            assert signal.wait_calls == 1

        db.session.expire_all()
        lock = db.session.get(SessionTurnLock, session_id)
        assert lock is not None
        assert lock.owner_token == 'replacement-owner'


def test_database_session_turn_coordinator_joins_heartbeat_before_release(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    session_id = ids['session_id']
    signals, signal_factory = _heartbeat_signal_factory()
    coordinator = DatabaseSessionTurnCoordinator(
        heartbeat_signal_factory=signal_factory,
    )
    original_heartbeat_loop = coordinator._heartbeat_loop
    original_release = coordinator._release
    heartbeat_done = threading.Event()
    release_observations: list[bool] = []

    def tracked_heartbeat_loop(*args):
        try:
            original_heartbeat_loop(*args)
        finally:
            heartbeat_done.set()

    def tracked_release(tracked_session_id, owner_token, fencing_token):
        release_observations.append(heartbeat_done.is_set())
        return original_release(tracked_session_id, owner_token, fencing_token)

    monkeypatch.setattr(coordinator, '_heartbeat_loop', tracked_heartbeat_loop)
    monkeypatch.setattr(coordinator, '_release', tracked_release)

    with app.app_context():
        with coordinator.serialized(session_id):
            signal = signals[0]
            assert signal.wait_until_waiting()

        assert signal.stopped is True
        assert release_observations == [True]
        assert coordinator.lock_count() == 0


def test_database_session_turn_coordinator_sanitizes_heartbeat_failures(app, monkeypatch, caplog):
    ids = seed_world_campaign_player_session(app)
    session_id = ids['session_id']
    signals, signal_factory = _heartbeat_signal_factory()
    coordinator = DatabaseSessionTurnCoordinator(
        heartbeat_signal_factory=signal_factory,
    )
    original_heartbeat_loop = coordinator._heartbeat_loop
    heartbeat_done = threading.Event()
    failed_attempt = threading.Event()

    def failed_renew(_session_id, _owner_token, _fencing_token):
        try:
            raise RuntimeError('raw-database-secret')
        finally:
            failed_attempt.set()

    def tracked_heartbeat_loop(*args):
        try:
            original_heartbeat_loop(*args)
        finally:
            heartbeat_done.set()

    monkeypatch.setattr(coordinator, '_renew', failed_renew)
    monkeypatch.setattr(coordinator, '_heartbeat_loop', tracked_heartbeat_loop)

    with caplog.at_level(logging.ERROR):
        with app.app_context():
            with coordinator.serialized(session_id):
                signal = signals[0]
                assert signal.wait_until_waiting()
                signal.allow_renewal()
                assert failed_attempt.wait(timeout=1.0)

            assert coordinator.lock_count() == 0
            assert heartbeat_done.is_set()

    assert 'Database turn-lock lease heartbeat renewal failed; retrying while the lease remains valid.' in caplog.text
    assert 'raw-database-secret' not in caplog.text


def test_configured_turn_coordinator_uses_database_store(app):
    ids = seed_world_campaign_player_session(app)
    session_id = ids['session_id']

    with app.app_context():
        app.config['AIDM_TURN_COORDINATOR_STORE'] = 'database'
        app.config['AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS'] = 30
        app.config['AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS'] = 10
        coordinator = ConfiguredSessionTurnCoordinator()

        with coordinator.serialized(session_id):
            assert coordinator.lock_count() == 1

        assert coordinator.lock_count() == 0


def test_configured_turn_coordinator_allows_nested_same_session_lock(app):
    ids = seed_world_campaign_player_session(app)
    session_id = ids['session_id']

    with app.app_context():
        coordinator = ConfiguredSessionTurnCoordinator()

        with coordinator.serialized(session_id) as outer_wait_ms:
            with coordinator.serialized(session_id) as inner_wait_ms:
                assert outer_wait_ms >= 0
                assert inner_wait_ms == 0.0
                assert coordinator.lock_count() == 1

        assert coordinator.discard_session(session_id) is True


def test_configured_turn_coordinator_serializes_many_in_deduped_order(app):
    first = seed_world_campaign_player_session(app)['session_id']
    second = seed_world_campaign_player_session(app)['session_id']
    coordinator = ConfiguredSessionTurnCoordinator()

    with app.app_context():
        with coordinator.serialized_many([second, first, second]) as waits:
            assert list(waits) == sorted({first, second})
            assert coordinator.lock_count() == 2
            with coordinator.serialized(first) as inner_wait_ms:
                assert inner_wait_ms == 0.0
                assert coordinator.lock_count() == 2

        assert coordinator.discard_session(first) is True
        assert coordinator.discard_session(second) is True
