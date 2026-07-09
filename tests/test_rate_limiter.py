from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

import aidm_server.rate_limiter as rate_limiter_module


class _FrozenDateTime:
    current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    min = datetime.min
    fromisoformat = staticmethod(datetime.fromisoformat)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.current.replace(tzinfo=None)
        return cls.current.astimezone(tz)


def test_rate_limiter_sweeps_expired_keys(monkeypatch):
    _FrozenDateTime.current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(rate_limiter_module, 'datetime', _FrozenDateTime)
    limiter = rate_limiter_module.FixedWindowRateLimiter(limit=1, window_seconds=10)

    for index in range(25):
        assert limiter.allow(f'key-{index}').allowed is True
    assert len(limiter._events) == 25

    _FrozenDateTime.current = _FrozenDateTime.current + timedelta(seconds=11)

    result = limiter.allow('fresh-key')
    assert result.allowed is True
    assert set(limiter._events) == {'fresh-key'}


def test_rate_limiter_factory_rejects_unknown_store():
    with pytest.raises(ValueError, match='Unsupported rate-limit store'):
        rate_limiter_module.build_rate_limiter(
            limit=1,
            window_seconds=10,
            store_name='sidecar',
        )


def test_postgres_advisory_lock_key_is_stable_and_bucket_specific():
    first = rate_limiter_module.postgres_advisory_lock_key('workspace:login')

    assert first == rate_limiter_module.postgres_advisory_lock_key('workspace:login')
    assert first != rate_limiter_module.postgres_advisory_lock_key('workspace:other')
    assert -(2**63) <= first < 2**63


def test_database_rate_limiter_store_is_shared_across_instances(app, monkeypatch):
    _FrozenDateTime.current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(rate_limiter_module, 'datetime', _FrozenDateTime)

    with app.app_context():
        first_process = rate_limiter_module.FixedWindowRateLimiter(
            limit=1,
            window_seconds=10,
            store=rate_limiter_module.DatabaseRateLimitStore(),
        )
        second_process = rate_limiter_module.FixedWindowRateLimiter(
            limit=1,
            window_seconds=10,
            store=rate_limiter_module.DatabaseRateLimitStore(),
        )

        assert first_process.allow('shared-key').allowed is True

        blocked = second_process.allow('shared-key')
        assert blocked.allowed is False
        assert blocked.remaining == 0

        _FrozenDateTime.current = _FrozenDateTime.current + timedelta(seconds=11)
        allowed_after_window = second_process.allow('shared-key')
        assert allowed_after_window.allowed is True


def test_database_rate_limiter_serializes_concurrent_hits(app, monkeypatch):
    _FrozenDateTime.current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(rate_limiter_module, 'datetime', _FrozenDateTime)

    store = rate_limiter_module.DatabaseRateLimitStore()
    limiter = rate_limiter_module.FixedWindowRateLimiter(
        limit=1,
        window_seconds=10,
        store=store,
    )

    def hit_once():
        with app.app_context():
            return limiter.allow('concurrent-key').allowed

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda _index: hit_once(), range(5)))

    assert results.count(True) == 1
    assert results.count(False) == 4
