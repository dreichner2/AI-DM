from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from scripts.migration_chain_drill import (
    MigrationChainDrillError,
    REQUIRED_HEAD_COLUMNS,
    REQUIRED_HEAD_TABLES,
    _verify_base_schema,
    _verify_head_schema,
    migration_env,
)


def test_migration_env_forces_isolated_test_runtime():
    env = migration_env('sqlite:////tmp/aidm-migration-drill.sqlite', base_env={'AIDM_ENV': 'production'})

    assert env['FLASK_APP'] == 'aidm_server.main:create_app'
    assert env['PYTHON_DOTENV_DISABLED'] == '1'
    assert env['AIDM_ENV'] == 'test'
    assert env['AIDM_AUTO_CREATE_SCHEMA'] == 'false'
    assert env['AIDM_DATABASE_URI'] == 'sqlite:////tmp/aidm-migration-drill.sqlite'


def _create_minimal_head_schema(db_uri: str) -> None:
    engine = create_engine(db_uri)
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)'))
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0028_session_turn_lock_fencing')"))
            for table in sorted(REQUIRED_HEAD_TABLES):
                if table in REQUIRED_HEAD_COLUMNS:
                    columns = ', '.join(f'{column} TEXT' for column in sorted(REQUIRED_HEAD_COLUMNS[table]))
                    conn.execute(text(f'CREATE TABLE {table} ({columns})'))
                elif table != 'alembic_version':
                    conn.execute(text(f'CREATE TABLE {table} (id INTEGER)'))
    finally:
        engine.dispose()


def test_verify_head_schema_accepts_required_tables_and_columns(tmp_path: Path):
    db_uri = f'sqlite:///{tmp_path / "head.sqlite"}'
    _create_minimal_head_schema(db_uri)

    snapshot = _verify_head_schema(db_uri)

    assert snapshot.revision == '0028_session_turn_lock_fencing'
    assert REQUIRED_HEAD_TABLES.issubset(snapshot.tables)


def test_verify_head_schema_rejects_missing_required_table(tmp_path: Path):
    db_uri = f'sqlite:///{tmp_path / "missing.sqlite"}'
    engine = create_engine(db_uri)
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)'))
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0028_session_turn_lock_fencing')"))
    finally:
        engine.dispose()

    with pytest.raises(MigrationChainDrillError, match='missing required tables'):
        _verify_head_schema(db_uri)


def test_verify_base_schema_rejects_leftover_application_table(tmp_path: Path):
    db_uri = f'sqlite:///{tmp_path / "base.sqlite"}'
    engine = create_engine(db_uri)
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32))'))
            conn.execute(text('CREATE TABLE sessions (session_id INTEGER)'))
    finally:
        engine.dispose()

    with pytest.raises(MigrationChainDrillError, match='left application tables behind'):
        _verify_base_schema(db_uri)
