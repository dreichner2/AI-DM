from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, event, inspect, select, text, update
from sqlalchemy.engine import URL, make_url

from aidm_server.database import db
from aidm_server.migration_compat import ALEMBIC_VERSION_COLUMN_LENGTH
from aidm_server.models import RateLimitEvent, SessionLogEntry, SessionTurnLock
from aidm_server.rate_limiter import DatabaseRateLimitStore, FixedWindowRateLimiter
from aidm_server.time_utils import utc_now
from aidm_server.turn_coordinator import DatabaseSessionTurnCoordinator, TurnLeaseLostError
from tests.helpers import seed_world_campaign_player_session


POSTGRES_TEST_URI = str(os.getenv('AIDM_POSTGRES_TEST_URI') or '').strip()
REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_HEAD_REVISION = '0029_players_account_fk'
pytestmark = pytest.mark.skipif(
    not POSTGRES_TEST_URI.startswith('postgresql+psycopg://'),
    reason='AIDM_POSTGRES_TEST_URI is required for PostgreSQL integration tests.',
)


def _render_database_url(database_url: URL) -> str:
    return database_url.render_as_string(hide_password=False)


def _run_flask_db(database_url: URL, *args: str) -> None:
    env = os.environ.copy()
    env.update(
        {
            'FLASK_APP': 'aidm_server.main:create_app',
            'PYTHONPATH': str(REPO_ROOT),
            'PYTHON_DOTENV_DISABLED': '1',
            'AIDM_SKIP_REPO_ENV_LOCAL': '1',
            'AIDM_DATABASE_URI': _render_database_url(database_url),
            'AIDM_AUTO_CREATE_SCHEMA': 'false',
            'AIDM_ENV': 'test',
            'AIDM_DEBUG': 'false',
            'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
            'AIDM_TELEMETRY_ENABLED': 'false',
        }
    )
    result = subprocess.run(
        [sys.executable, '-m', 'flask', 'db', *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        output = f'{result.stdout}\n{result.stderr}'
        if database_url.password:
            output = output.replace(database_url.password, '***')
        pytest.fail(f"flask db {' '.join(args)} failed:\n{output}")


def _version_column_length(engine) -> int | None:
    version_column = next(
        column
        for column in inspect(engine).get_columns('alembic_version')
        if column['name'] == 'version_num'
    )
    return getattr(version_column['type'], 'length', None)


def test_postgres_migrations_recover_legacy_version_table_width():
    base_url = make_url(POSTGRES_TEST_URI)
    rehearsal_database = f'aidm_migration_{uuid4().hex}'
    rehearsal_url = base_url.set(database=rehearsal_database)
    maintenance_url = base_url.set(database='postgres')
    maintenance_engine = create_engine(maintenance_url, isolation_level='AUTOCOMMIT')
    rehearsal_engine = None

    try:
        with maintenance_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{rehearsal_database}"')

        _run_flask_db(rehearsal_url, 'upgrade', '0014_workspace_character_pool')
        rehearsal_engine = create_engine(rehearsal_url)
        with rehearsal_engine.begin() as connection:
            revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            assert revision == '0014_workspace_character_pool'
            connection.exec_driver_sql(
                'ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(32)'
            )

        _run_flask_db(rehearsal_url, 'upgrade', 'head')
        with rehearsal_engine.connect() as connection:
            revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        assert revision == EXPECTED_HEAD_REVISION
        assert (_version_column_length(rehearsal_engine) or 0) >= ALEMBIC_VERSION_COLUMN_LENGTH

        player_account_fk = next(
            foreign_key
            for foreign_key in inspect(rehearsal_engine).get_foreign_keys('players')
            if foreign_key.get('constrained_columns') == ['account_id']
        )
        assert player_account_fk['referred_table'] == 'accounts'
        assert (player_account_fk.get('options') or {}).get('ondelete') == 'SET NULL'

        # A database manually stamped at a short current head must also be
        # repaired even when Alembic has no application migration to run.
        with rehearsal_engine.begin() as connection:
            connection.exec_driver_sql(
                'ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(32)'
            )
        _run_flask_db(rehearsal_url, 'upgrade', 'head')
        assert (_version_column_length(rehearsal_engine) or 0) >= ALEMBIC_VERSION_COLUMN_LENGTH
        _run_flask_db(rehearsal_url, 'check')
    finally:
        if rehearsal_engine is not None:
            rehearsal_engine.dispose()
        with maintenance_engine.connect() as connection:
            connection.execute(
                text(
                    'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                    'WHERE datname = :database_name AND pid <> pg_backend_pid()'
                ),
                {'database_name': rehearsal_database},
            )
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{rehearsal_database}"')
        maintenance_engine.dispose()


@pytest.fixture()
def postgres_app(monkeypatch):
    monkeypatch.setenv('AIDM_DATABASE_URI', POSTGRES_TEST_URI)
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'false')
    monkeypatch.setenv('AIDM_ENV', 'test')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('AIDM_CORS_ALLOWLIST', 'http://localhost')
    monkeypatch.setenv('AIDM_SOCKET_CORS_ALLOWLIST', 'http://localhost')
    monkeypatch.setenv('AIDM_SOCKETIO_ASYNC_MODE', 'threading')
    monkeypatch.setenv('AIDM_LLM_PROVIDER', 'fallback')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')
    monkeypatch.setenv('AIDM_RATE_LIMIT_STORE', 'database')
    monkeypatch.setenv('AIDM_TURN_COORDINATOR_STORE', 'database')

    from aidm_server.main import create_app

    app = create_app()
    yield app

    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def test_postgres_rate_limiter_is_atomic_across_store_instances(postgres_app):
    bucket_key = f'postgres-concurrency-{uuid4().hex}'
    first = FixedWindowRateLimiter(
        limit=1,
        window_seconds=60,
        store=DatabaseRateLimitStore(retention_window_seconds=60),
    )
    second = FixedWindowRateLimiter(
        limit=1,
        window_seconds=60,
        store=DatabaseRateLimitStore(retention_window_seconds=60),
    )
    start = threading.Barrier(2)

    with postgres_app.app_context():
        engine = db.engine

    def delay_test_insert(_conn, _cursor, statement, _parameters, _context, _executemany):
        if 'insert into rate_limit_events' in statement.lower():
            # Without the PostgreSQL advisory lock both transactions count zero,
            # reach this delayed insert, and admit the same one-request bucket.
            time.sleep(0.25)

    def hit_once(limiter: FixedWindowRateLimiter) -> bool:
        start.wait(timeout=5)
        with postgres_app.app_context():
            return limiter.allow(bucket_key).allowed

    event.listen(engine, 'before_cursor_execute', delay_test_insert)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(hit_once, limiter) for limiter in (first, second)]
            results = [future.result(timeout=10) for future in futures]
    finally:
        event.remove(engine, 'before_cursor_execute', delay_test_insert)
        with engine.begin() as connection:
            connection.execute(delete(RateLimitEvent.__table__).where(RateLimitEvent.bucket_key == bucket_key))

    assert results.count(True) == 1
    assert results.count(False) == 1


def test_postgres_turn_coordinator_serializes_across_instances(postgres_app):
    ids = seed_world_campaign_player_session(postgres_app)
    session_id = ids['session_id']
    first = DatabaseSessionTurnCoordinator(poll_interval_seconds=0.01)
    second = DatabaseSessionTurnCoordinator(poll_interval_seconds=0.01)
    contender_started = threading.Event()
    contender_entered = threading.Event()

    def contend() -> None:
        with postgres_app.app_context():
            contender_started.set()
            with second.serialized(session_id):
                contender_entered.set()

    with postgres_app.app_context():
        with first.serialized(session_id):
            thread = threading.Thread(target=contend)
            thread.start()
            assert contender_started.wait(timeout=2)
            assert not contender_entered.wait(timeout=0.15)

        thread.join(timeout=5)
        assert not thread.is_alive()
        assert contender_entered.is_set()
        assert first.lock_count() == 0


def test_postgres_turn_coordinator_rejects_stale_commit_after_takeover(postgres_app):
    ids = seed_world_campaign_player_session(postgres_app)
    session_id = ids['session_id']
    first = DatabaseSessionTurnCoordinator(lease_seconds=30, poll_interval_seconds=0.01)
    second = DatabaseSessionTurnCoordinator(lease_seconds=30, poll_interval_seconds=0.01)
    contender_entered = threading.Event()
    release_contender = threading.Event()
    contender_errors: list[BaseException] = []

    def take_over() -> None:
        try:
            with postgres_app.app_context():
                with second.serialized(session_id):
                    contender_entered.set()
                    release_contender.wait(timeout=5)
        except BaseException as exc:  # pragma: no cover - asserted in the parent thread.
            contender_errors.append(exc)
            contender_entered.set()

    with postgres_app.app_context():
        with first.serialized(session_id):
            with db.engine.connect() as connection:
                first_token = connection.execute(
                    select(SessionTurnLock.fencing_token).where(SessionTurnLock.session_id == session_id)
                ).scalar_one()
            with db.engine.begin() as connection:
                connection.execute(
                    update(SessionTurnLock.__table__)
                    .where(SessionTurnLock.session_id == session_id)
                    .values(expires_at=utc_now() - timedelta(seconds=1), updated_at=utc_now())
                )

            contender = threading.Thread(target=take_over)
            contender.start()
            try:
                assert contender_entered.wait(timeout=5)
                assert contender_errors == []
                with db.engine.connect() as connection:
                    replacement_token = connection.execute(
                        select(SessionTurnLock.fencing_token).where(SessionTurnLock.session_id == session_id)
                    ).scalar_one()
                assert replacement_token == first_token + 1

                db.session.add(
                    SessionLogEntry(
                        session_id=session_id,
                        message='postgres stale lease commit must be fenced',
                        entry_type='system',
                    )
                )
                with pytest.raises(TurnLeaseLostError, match='refusing stale database commit'):
                    db.session.commit()
                db.session.rollback()
            finally:
                release_contender.set()
                contender.join(timeout=5)

            assert not contender.is_alive()
            assert contender_errors == []

        assert SessionLogEntry.query.filter_by(
            session_id=session_id,
            message='postgres stale lease commit must be fenced',
        ).count() == 0
