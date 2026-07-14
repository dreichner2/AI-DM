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


def test_database_rate_limiter_requires_sufficient_retention():
    with pytest.raises(ValueError, match='require a retention window'):
        rate_limiter_module.build_rate_limiter(
            limit=1,
            window_seconds=60,
            store_name='database',
        )

    with pytest.raises(ValueError, match='exceeds database retention horizon'):
        rate_limiter_module.build_rate_limiter(
            limit=1,
            window_seconds=60,
            store_name='database',
            retention_window_seconds=30,
        )

    with pytest.raises(ValueError, match='positive number of seconds'):
        rate_limiter_module.DatabaseRateLimitStore(retention_window_seconds=0)

    undersized_store = rate_limiter_module.DatabaseRateLimitStore(retention_window_seconds=30)
    with pytest.raises(ValueError, match='exceeds database retention horizon'):
        rate_limiter_module.FixedWindowRateLimiter(
            limit=1,
            window_seconds=60,
            store=undersized_store,
        )
    with pytest.raises(ValueError, match='exceeds database retention horizon'):
        undersized_store.hit(
            'preauth:direct-hit',
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
            limit=1,
            window_seconds=60,
        )


def test_rate_limit_key_hashing_only_compacts_oversized_bucket_identifiers():
    assert rate_limiter_module.normalize_rate_limit_key('workspace:login') == 'workspace:login'

    first = rate_limiter_module.normalize_rate_limit_key('workspace:' + ('a' * 600))
    repeated = rate_limiter_module.normalize_rate_limit_key('workspace:' + ('a' * 600))
    different = rate_limiter_module.normalize_rate_limit_key('workspace:' + ('a' * 599) + 'b')

    assert len(first) == rate_limiter_module.MAX_BUCKET_KEY_LENGTH
    assert first == repeated
    assert first != different


def test_preauth_bucket_is_stable_opaque_and_bound_to_ip_target_and_secret():
    values = {
        'secret_key': 'shared-flask-secret',
        'dimension': 'ip-target',
        'action': 'account-login',
        'client_ip': '198.51.100.10',
        'normalized_target': 'sensitive_username',
    }

    bucket = rate_limiter_module.privacy_safe_preauth_bucket(**values)

    assert bucket == rate_limiter_module.privacy_safe_preauth_bucket(**values)
    assert '198.51.100.10' not in bucket
    assert 'sensitive_username' not in bucket
    assert bucket != rate_limiter_module.privacy_safe_preauth_bucket(
        **{**values, 'client_ip': '203.0.113.20'},
    )
    assert bucket != rate_limiter_module.privacy_safe_preauth_bucket(
        **{**values, 'normalized_target': 'other_username'},
    )
    assert bucket != rate_limiter_module.privacy_safe_preauth_bucket(
        **{**values, 'secret_key': 'rotated-flask-secret'},
    )


def test_preauth_bucket_requires_secret_key():
    with pytest.raises(ValueError, match='non-empty secret key'):
        rate_limiter_module.privacy_safe_preauth_bucket(
            secret_key='',
            dimension='ip-target',
            action='account-login',
            client_ip='127.0.0.1',
            normalized_target='account',
        )


def test_preauth_rate_limit_defaults_are_stricter_than_general_api(monkeypatch):
    from aidm_server.config import load_config

    monkeypatch.setenv('AIDM_ENV', 'test')
    for name in (
        'AIDM_RATE_LIMIT_WINDOW_SECONDS',
        'AIDM_RATE_LIMIT_MAX_API_REQUESTS',
        'AIDM_PREAUTH_RATE_LIMIT_WINDOW_SECONDS',
        'AIDM_PREAUTH_RATE_LIMIT_MAX_IP_TARGET_ATTEMPTS',
        'AIDM_PREAUTH_RATE_LIMIT_MAX_IP_ATTEMPTS',
        'AIDM_PREAUTH_RATE_LIMIT_MAX_TARGET_ATTEMPTS',
    ):
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config.rate_limit_window_seconds == 30
    assert config.rate_limit_max_api_requests == 120
    assert config.preauth_rate_limit_window_seconds == 60
    assert config.preauth_rate_limit_max_ip_target_attempts == 5
    assert config.preauth_rate_limit_max_ip_attempts == 20
    assert config.preauth_rate_limit_max_target_attempts == 20


def test_app_database_limiters_share_maximum_configured_retention(tmp_path, monkeypatch):
    from aidm_server.main import create_app

    monkeypatch.setenv('AIDM_ENV', 'test')
    monkeypatch.setenv('AIDM_DATABASE_URI', f'sqlite:///{tmp_path / "retention.db"}')
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'false')
    monkeypatch.setenv('AIDM_RATE_LIMIT_STORE', 'database')
    monkeypatch.setenv('AIDM_RATE_LIMIT_WINDOW_SECONDS', '30')
    monkeypatch.setenv('AIDM_PREAUTH_RATE_LIMIT_WINDOW_SECONDS', '60')

    app = create_app()

    assert app.config['AIDM_RATE_LIMIT_RETENTION_WINDOW_SECONDS'] == 60
    for extension_name in (
        'aidm_api_limiter',
        'aidm_preauth_ip_target_limiter',
        'aidm_preauth_ip_limiter',
        'aidm_preauth_target_limiter',
    ):
        limiter = app.extensions[extension_name]
        assert limiter.store.retention_window_seconds == 60


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
            store=rate_limiter_module.DatabaseRateLimitStore(retention_window_seconds=10),
        )
        second_process = rate_limiter_module.FixedWindowRateLimiter(
            limit=1,
            window_seconds=10,
            store=rate_limiter_module.DatabaseRateLimitStore(retention_window_seconds=10),
        )

        assert first_process.allow('shared-key').allowed is True

        blocked = second_process.allow('shared-key')
        assert blocked.allowed is False
        assert blocked.remaining == 0

        _FrozenDateTime.current = _FrozenDateTime.current + timedelta(seconds=11)
        allowed_after_window = second_process.allow('shared-key')
        assert allowed_after_window.allowed is True


def test_database_gc_preserves_long_window_until_shared_retention_expires(app, monkeypatch):
    _FrozenDateTime.current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(rate_limiter_module, 'datetime', _FrozenDateTime)

    with app.app_context():
        long_limiter = rate_limiter_module.FixedWindowRateLimiter(
            limit=1,
            window_seconds=60,
            store=rate_limiter_module.DatabaseRateLimitStore(retention_window_seconds=60),
        )
        short_limiter = rate_limiter_module.FixedWindowRateLimiter(
            limit=1,
            window_seconds=30,
            store=rate_limiter_module.DatabaseRateLimitStore(retention_window_seconds=60),
        )

        assert long_limiter.allow('preauth:target:example').allowed is True

        _FrozenDateTime.current += timedelta(seconds=31)
        assert short_limiter.allow('api:noise').allowed is True
        blocked = long_limiter.allow('preauth:target:example')
        assert blocked.allowed is False
        assert blocked.reset_in_seconds == 29

        _FrozenDateTime.current += timedelta(seconds=30)
        assert long_limiter.allow('preauth:target:example').allowed is True


@pytest.mark.parametrize('offset_seconds', [30, 60])
def test_database_gc_preserves_long_window_at_exact_boundaries(app, monkeypatch, offset_seconds):
    _FrozenDateTime.current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(rate_limiter_module, 'datetime', _FrozenDateTime)

    with app.app_context():
        long_limiter = rate_limiter_module.FixedWindowRateLimiter(
            limit=1,
            window_seconds=60,
            store=rate_limiter_module.DatabaseRateLimitStore(retention_window_seconds=60),
        )
        short_limiter = rate_limiter_module.FixedWindowRateLimiter(
            limit=1,
            window_seconds=30,
            store=rate_limiter_module.DatabaseRateLimitStore(retention_window_seconds=60),
        )

        assert long_limiter.allow('preauth:boundary').allowed is True
        _FrozenDateTime.current += timedelta(seconds=offset_seconds)
        assert short_limiter.allow(f'api:boundary:{offset_seconds}').allowed is True
        assert long_limiter.allow('preauth:boundary').allowed is False


def test_database_rate_limiter_serializes_concurrent_hits(app, monkeypatch):
    _FrozenDateTime.current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(rate_limiter_module, 'datetime', _FrozenDateTime)

    store = rate_limiter_module.DatabaseRateLimitStore(retention_window_seconds=10)
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


def test_database_rate_limiter_acquires_process_lock_before_postgres_transaction(app, monkeypatch):
    class RecordingLock:
        def __init__(self):
            self.entries = 0
            self.entered = False

        def __enter__(self):
            self.entries += 1
            self.entered = True

        def __exit__(self, _exc_type, _exc, _traceback):
            self.entered = False
            return False

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = rate_limiter_module.DatabaseRateLimitStore(retention_window_seconds=10)
    recording_lock = RecordingLock()
    monkeypatch.setattr(store, '_hit_lock', recording_lock)
    original_hit_in_transaction = store._hit_in_transaction

    def hit_in_transaction(*args, **kwargs):
        assert recording_lock.entered is True
        return original_hit_in_transaction(*args, **kwargs)

    monkeypatch.setattr(store, '_hit_in_transaction', hit_in_transaction)

    with app.app_context():
        from aidm_server.database import db

        monkeypatch.setattr(db.engine.dialect, 'name', 'postgresql')
        monkeypatch.setattr(store, '_lock_bucket_for_transaction', lambda _connection, _key: None)
        result = store.hit('postgres-bucket', now=now, limit=2, window_seconds=10)

    assert result.allowed is True
    assert recording_lock.entries == 1
    assert recording_lock.entered is False


def test_database_rate_limiter_releases_process_lock_after_postgres_error(app, monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = rate_limiter_module.DatabaseRateLimitStore(retention_window_seconds=10)

    with app.app_context():
        from aidm_server.database import db

        monkeypatch.setattr(db.engine.dialect, 'name', 'postgresql')
        monkeypatch.setattr(
            store,
            '_hit_in_transaction',
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('database unavailable')),
        )

        with pytest.raises(RuntimeError, match='database unavailable'):
            store.hit('postgres-bucket', now=now, limit=2, window_seconds=10)

    assert store._hit_lock.acquire(blocking=False) is True
    store._hit_lock.release()
