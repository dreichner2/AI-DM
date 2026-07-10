"""Compatibility helpers for Alembic's migration bookkeeping table."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import Column, MetaData, PrimaryKeyConstraint, String, Table, inspect
from sqlalchemy.engine import Connection


ALEMBIC_VERSION_TABLE = 'alembic_version'
ALEMBIC_VERSION_COLUMN_LENGTH = 255

VersionTableAction = Literal['created', 'widened', 'compatible']


def _version_table(
    *,
    table_name: str,
    schema: str | None,
    primary_key: bool,
) -> Table:
    table = Table(
        table_name,
        MetaData(),
        Column('version_num', String(ALEMBIC_VERSION_COLUMN_LENGTH), nullable=False),
        schema=schema,
    )
    if primary_key:
        table.append_constraint(
            PrimaryKeyConstraint('version_num', name=f'{table_name}_pkc')
        )
    return table


def ensure_alembic_version_table_capacity(
    connection: Connection,
    *,
    table_name: str = ALEMBIC_VERSION_TABLE,
    schema: str | None = None,
    primary_key: bool = True,
) -> VersionTableAction:
    """Ensure Alembic can persist every revision identifier used by AIDM.

    Alembic creates ``version_num`` as ``VARCHAR(32)`` by default. Several
    established AIDM revision identifiers are longer than that. SQLite does
    not enforce the declared width, which allowed the mismatch to remain
    hidden until the migration chain ran against PostgreSQL.

    Creating the bookkeeping table before Alembic configures its migration
    context gives fresh databases a safe width. Existing PostgreSQL databases
    are widened in place before Alembic attempts to advance their revision.
    """

    inspector = inspect(connection)
    if not inspector.has_table(table_name, schema=schema):
        _version_table(
            table_name=table_name,
            schema=schema,
            primary_key=primary_key,
        ).create(connection)
        return 'created'

    version_column = next(
        (
            column
            for column in inspector.get_columns(table_name, schema=schema)
            if column.get('name') == 'version_num'
        ),
        None,
    )
    if version_column is None:
        raise RuntimeError('Alembic version table is missing its version_num column.')

    declared_length = getattr(version_column.get('type'), 'length', None)
    if declared_length is None or declared_length >= ALEMBIC_VERSION_COLUMN_LENGTH:
        return 'compatible'

    dialect_name = connection.dialect.name
    if dialect_name == 'sqlite':
        # SQLite treats VARCHAR lengths as advisory and stores the full value.
        return 'compatible'
    if dialect_name != 'postgresql':
        raise RuntimeError(
            f'Alembic version_num is only {declared_length} characters wide on '
            f'unsupported migration dialect {dialect_name!r}.'
        )

    preparer = connection.dialect.identifier_preparer
    qualified_table_name = preparer.quote(table_name)
    if schema:
        qualified_table_name = (
            f'{preparer.quote_schema(schema)}.{qualified_table_name}'
        )
    version_column_name = preparer.quote('version_num')
    connection.exec_driver_sql(
        f'ALTER TABLE {qualified_table_name} '
        f'ALTER COLUMN {version_column_name} TYPE VARCHAR({ALEMBIC_VERSION_COLUMN_LENGTH})'
    )
    return 'widened'
