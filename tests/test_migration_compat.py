from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import String, create_engine, inspect
from sqlalchemy.dialects import postgresql

import aidm_server.migration_compat as migration_compat
from aidm_server.migration_compat import (
    ALEMBIC_VERSION_COLUMN_LENGTH,
    ALEMBIC_VERSION_TABLE,
    ensure_alembic_version_table_capacity,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_fresh_version_table_has_capacity_for_long_revision_identifiers():
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            assert ensure_alembic_version_table_capacity(connection) == 'created'
            assert ensure_alembic_version_table_capacity(connection) == 'compatible'

        inspector = inspect(engine)
        version_column = next(
            column
            for column in inspector.get_columns(ALEMBIC_VERSION_TABLE)
            if column['name'] == 'version_num'
        )
        assert version_column['type'].length == ALEMBIC_VERSION_COLUMN_LENGTH
        assert inspector.get_pk_constraint(ALEMBIC_VERSION_TABLE)['constrained_columns'] == [
            'version_num'
        ]
    finally:
        engine.dispose()


def test_existing_postgres_version_table_is_widened(monkeypatch):
    executed: list[str] = []
    connection = SimpleNamespace(
        dialect=postgresql.dialect(),
        exec_driver_sql=executed.append,
    )
    inspector = SimpleNamespace(
        has_table=lambda _table_name, schema=None: True,
        get_columns=lambda _table_name, schema=None: [
            {'name': 'version_num', 'type': String(32)},
        ],
    )
    monkeypatch.setattr(migration_compat, 'inspect', lambda _connection: inspector)

    assert ensure_alembic_version_table_capacity(connection) == 'widened'
    assert executed == [
        'ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)'
    ]


def test_version_table_capacity_covers_every_migration_revision():
    revisions: dict[str, str | None] = {}
    for migration_path in sorted((REPO_ROOT / 'migrations' / 'versions').glob('*.py')):
        module = ast.parse(migration_path.read_text(encoding='utf-8'))
        revision: str | None = None
        down_revision: str | None = None
        for statement in module.body:
            if not isinstance(statement, ast.Assign):
                continue
            target_names = {
                target.id for target in statement.targets if isinstance(target, ast.Name)
            }
            if 'revision' in target_names:
                revision = ast.literal_eval(statement.value)
            if 'down_revision' in target_names:
                down_revision = ast.literal_eval(statement.value)
        assert isinstance(revision, str), migration_path
        assert revision not in revisions
        revisions[revision] = down_revision

    assert revisions
    assert max(map(len, revisions)) <= ALEMBIC_VERSION_COLUMN_LENGTH
    assert all(parent is None or parent in revisions for parent in revisions.values())
    assert [revision for revision, parent in revisions.items() if parent is None] == [
        '0001_initial_core'
    ]
    referenced_revisions = {parent for parent in revisions.values() if parent is not None}
    assert set(revisions) - referenced_revisions == {'0029_players_account_fk'}
