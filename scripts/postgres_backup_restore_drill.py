#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / 'tmp' / 'postgres_backup_restore_drills'

POSTGRES_DRIVERS = {'postgresql', 'postgresql+psycopg'}
DEFAULT_POSTGRES_PORT = 5432
ARCHIVE_MAGIC = b'PGDMP'
POSTGRES_TOOL_VERSION_PATTERN = re.compile(
    r'\b(?P<tool>pg_dump|pg_restore)\s+\(PostgreSQL\)\s+'
    r'(?P<version>\d+(?:\.\d+)*(?:(?:beta|rc)\d+|devel)?)',
    flags=re.IGNORECASE,
)

# These SQLAlchemy URL query keys map directly to libpq environment variables.
# Unknown keys fail closed so pg_dump/pg_restore and psycopg cannot silently use
# different connection settings.
LIBPQ_QUERY_ENV = {
    'application_name': 'PGAPPNAME',
    'channel_binding': 'PGCHANNELBINDING',
    'connect_timeout': 'PGCONNECT_TIMEOUT',
    'gssencmode': 'PGGSSENCMODE',
    'keepalives': 'PGKEEPALIVES',
    'keepalives_count': 'PGKEEPALIVESCOUNT',
    'keepalives_idle': 'PGKEEPALIVESIDLE',
    'keepalives_interval': 'PGKEEPALIVESINTERVAL',
    'load_balance_hosts': 'PGLOADBALANCEHOSTS',
    'options': 'PGOPTIONS',
    'passfile': 'PGPASSFILE',
    'sslcert': 'PGSSLCERT',
    'sslcrl': 'PGSSLCRL',
    'sslcrldir': 'PGSSLCRLDIR',
    'sslkey': 'PGSSLKEY',
    'sslmode': 'PGSSLMODE',
    'sslrootcert': 'PGSSLROOTCERT',
    'target_session_attrs': 'PGTARGETSESSIONATTRS',
    'tcp_user_timeout': 'PGTCPUSER_TIMEOUT',
}


class PostgresBackupRestoreDrillError(RuntimeError):
    pass


@dataclass(frozen=True)
class PostgresConnectionSpec:
    raw_uri: str
    host: str
    port: int
    database: str
    username: str
    password: str | None
    query: dict[str, str]

    @property
    def endpoint_key(self) -> tuple[str, int, str]:
        return (self.host.casefold(), self.port, self.database)

    @property
    def evidence_label(self) -> str:
        # Usernames, passwords, and query parameters are deliberately omitted.
        return f'postgresql://{self.host}:{self.port}/{self.database}'

    def psycopg_kwargs(self) -> dict[str, str | int]:
        kwargs: dict[str, str | int] = {
            'host': self.host,
            'port': self.port,
            'dbname': self.database,
            'user': self.username,
        }
        if self.password is not None:
            kwargs['password'] = self.password
        kwargs.update(self.query)
        return kwargs

    def pg_environment(
        self,
        *,
        base_env: Mapping[str, str] | None = None,
        read_only: bool = False,
    ) -> dict[str, str]:
        env = dict(base_env or os.environ)
        for key in tuple(env):
            if key.startswith('PG'):
                env.pop(key)
        env.update(
            {
                'PGHOST': self.host,
                'PGPORT': str(self.port),
                'PGDATABASE': self.database,
                'PGUSER': self.username,
            }
        )
        if self.password is not None:
            env['PGPASSWORD'] = self.password
        for key, value in self.query.items():
            env[LIBPQ_QUERY_ENV[key]] = value
        if read_only:
            current_options = env.get('PGOPTIONS', '').strip()
            read_only_option = '-c default_transaction_read_only=on'
            env['PGOPTIONS'] = f'{current_options} {read_only_option}'.strip()
        return env


@dataclass(frozen=True)
class SequenceSnapshot:
    data_type: str
    start_value: int
    minimum_value: int
    maximum_value: int
    increment_by: int
    cache_size: int
    cycles: bool
    last_value: int
    is_called: bool


@dataclass(frozen=True)
class DatabaseSnapshot:
    server_version: str
    server_version_num: int
    tables: dict[str, int]
    sequences: dict[str, SequenceSnapshot]
    alembic_revisions: tuple[str, ...]
    invalid_indexes: tuple[str, ...]
    unvalidated_constraints: tuple[str, ...]
    public_objects: tuple[str, ...]
    non_default_extensions: tuple[str, ...]

    @property
    def server_major(self) -> int:
        return self.server_version_num // 10_000


@dataclass(frozen=True)
class PostgresToolVersion:
    tool: str
    version: str
    major: int


@dataclass(frozen=True)
class PostgresBackupRestoreDrillResult:
    archive_path: Path
    markdown_evidence_path: Path
    json_evidence_path: Path
    archive_sha256: str
    archive_size_bytes: int
    archive_list_entries: int
    table_count: int
    row_count: int
    alembic_revisions: tuple[str, ...]


def parse_postgres_uri(uri: str, *, label: str) -> PostgresConnectionSpec:
    raw_uri = str(uri or '').strip()
    if not raw_uri:
        raise PostgresBackupRestoreDrillError(f'{label} PostgreSQL URI is required.')
    try:
        url = make_url(raw_uri)
    except Exception as exc:
        raise PostgresBackupRestoreDrillError(f'{label} PostgreSQL URI is invalid.') from exc

    if url.drivername not in POSTGRES_DRIVERS:
        raise PostgresBackupRestoreDrillError(
            f'{label} URI must use postgresql or postgresql+psycopg.'
        )
    if not url.host:
        raise PostgresBackupRestoreDrillError(f'{label} URI must include an explicit host.')
    if not url.database:
        raise PostgresBackupRestoreDrillError(f'{label} URI must include a database name.')
    if not url.username:
        raise PostgresBackupRestoreDrillError(f'{label} URI must include a username.')

    query: dict[str, str] = {}
    unsupported_keys: list[str] = []
    for key, value in url.query.items():
        if key not in LIBPQ_QUERY_ENV or not isinstance(value, str):
            unsupported_keys.append(str(key))
            continue
        query[str(key)] = value
    if unsupported_keys:
        keys = ', '.join(sorted(unsupported_keys))
        raise PostgresBackupRestoreDrillError(
            f'{label} URI contains unsupported connection option(s): {keys}.'
        )

    return PostgresConnectionSpec(
        raw_uri=raw_uri,
        host=str(url.host),
        port=int(url.port or DEFAULT_POSTGRES_PORT),
        database=str(url.database),
        username=str(url.username),
        password=str(url.password) if url.password is not None else None,
        query=query,
    )


def validate_distinct_endpoints(
    source: PostgresConnectionSpec,
    target: PostgresConnectionSpec,
) -> None:
    if source.endpoint_key == target.endpoint_key:
        raise PostgresBackupRestoreDrillError(
            'Source and empty target must identify distinct PostgreSQL host/port/database endpoints.'
        )


def _redact(text: str, specs: Sequence[PostgresConnectionSpec]) -> str:
    redacted = str(text or '')
    redacted = re.sub(
        r'postgres(?:ql)?(?:\+psycopg)?://[^\s/@]+(?::[^\s/@]*)?@',
        'postgresql://<redacted>@',
        redacted,
        flags=re.IGNORECASE,
    )
    secrets: set[str] = set()
    for spec in specs:
        secrets.add(spec.raw_uri)
        if spec.password:
            secrets.add(spec.password)
        if spec.username:
            secrets.add(spec.username)
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            redacted = redacted.replace(secret, '<redacted>')
    return redacted


def _resolve_tool(tool: str) -> str:
    resolved = shutil.which(tool)
    if not resolved:
        raise PostgresBackupRestoreDrillError(f'Required PostgreSQL tool was not found: {tool}.')
    return resolved


def _run_tool(
    executable: str,
    args: Sequence[str],
    *,
    label: str,
    specs: Sequence[PostgresConnectionSpec],
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [executable, *args],
        cwd=str(REPO_ROOT),
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return result
    detail = _redact(result.stderr or result.stdout, specs).strip()
    suffix = f' Detail: {detail}' if detail else ''
    raise PostgresBackupRestoreDrillError(f'{label} failed with exit code {result.returncode}.{suffix}')


def _parse_postgres_tool_version(output: str, *, expected_tool: str) -> PostgresToolVersion:
    match = POSTGRES_TOOL_VERSION_PATTERN.search(str(output or '').strip())
    if match is None or match.group('tool').casefold() != expected_tool.casefold():
        raise PostgresBackupRestoreDrillError(
            f'Could not parse {expected_tool} --version output as a PostgreSQL client version.'
        )
    version = match.group('version')
    return PostgresToolVersion(
        tool=expected_tool,
        version=version,
        major=int(re.match(r'\d+', version).group()),
    )


def inspect_postgres_tool_version(
    executable: str,
    *,
    expected_tool: str,
    specs: Sequence[PostgresConnectionSpec],
) -> PostgresToolVersion:
    result = _run_tool(
        executable,
        ['--version'],
        label=f'{expected_tool} version preflight',
        specs=specs,
    )
    return _parse_postgres_tool_version(
        f'{result.stdout}\n{result.stderr}',
        expected_tool=expected_tool,
    )


def _validate_expected_major(expected_major: int | None, *, label: str) -> None:
    if expected_major is None:
        return
    if isinstance(expected_major, bool) or not isinstance(expected_major, int) or expected_major < 1:
        raise PostgresBackupRestoreDrillError(
            f'{label} expected major must be a positive integer when supplied.'
        )


def _assert_expected_major(
    *,
    label: str,
    actual_major: int,
    expected_major: int | None,
) -> None:
    if expected_major is not None and actual_major != expected_major:
        raise PostgresBackupRestoreDrillError(
            f'{label} major version mismatch: expected {expected_major}, found {actual_major}. '
            'Backup and restore were not started.'
        )


def _query_rows(connection: psycopg.Connection[Any], query: Any) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(query)
        return list(cursor.fetchall())


def _inspect_connection(connection: psycopg.Connection[Any]) -> DatabaseSnapshot:
    server_version, server_version_num = _query_rows(
        connection,
        """
                SELECT
                    current_setting('server_version'),
                    current_setting('server_version_num')::integer
        """,
    )[0]
    table_names = [
                str(row[0])
                for row in _query_rows(
                    connection,
                    """
                    SELECT c.relname
                    FROM pg_catalog.pg_class AS c
                    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind IN ('r', 'p')
                    ORDER BY c.relname
                    """,
                )
    ]
    tables: dict[str, int] = {}
    for table_name in table_names:
        count_query = sql.SQL('SELECT count(*) FROM {}.{}').format(
            sql.Identifier('public'),
            sql.Identifier(table_name),
        )
        tables[table_name] = int(_query_rows(connection, count_query)[0][0])

    sequence_rows = _query_rows(
        connection,
        """
                SELECT
                    c.relname,
                    pg_catalog.format_type(s.seqtypid, NULL),
                    s.seqstart,
                    s.seqmin,
                    s.seqmax,
                    s.seqincrement,
                    s.seqcache,
                    s.seqcycle
                FROM pg_catalog.pg_sequence AS s
                JOIN pg_catalog.pg_class AS c ON c.oid = s.seqrelid
                JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                ORDER BY c.relname
        """,
    )
    sequences: dict[str, SequenceSnapshot] = {}
    for row in sequence_rows:
        sequence_name = str(row[0])
        state_query = sql.SQL('SELECT last_value, is_called FROM {}.{}').format(
            sql.Identifier('public'),
            sql.Identifier(sequence_name),
        )
        last_value, is_called = _query_rows(connection, state_query)[0]
        sequences[sequence_name] = SequenceSnapshot(
            data_type=str(row[1]),
            start_value=int(row[2]),
            minimum_value=int(row[3]),
            maximum_value=int(row[4]),
            increment_by=int(row[5]),
            cache_size=int(row[6]),
            cycles=bool(row[7]),
            last_value=int(last_value),
            is_called=bool(is_called),
        )

    alembic_revisions: tuple[str, ...] = ()
    if 'alembic_version' in tables:
        alembic_revisions = tuple(
            str(row[0])
            for row in _query_rows(
                connection,
                'SELECT version_num FROM public.alembic_version ORDER BY version_num',
            )
        )

    invalid_indexes = tuple(
        str(row[0])
        for row in _query_rows(
            connection,
            """
                    SELECT quote_ident(n.nspname) || '.' || quote_ident(c.relname)
                    FROM pg_catalog.pg_index AS i
                    JOIN pg_catalog.pg_class AS c ON c.oid = i.indexrelid
                    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND NOT i.indisvalid
                    ORDER BY c.relname
            """,
        )
    )
    unvalidated_constraints = tuple(
        str(row[0])
        for row in _query_rows(
            connection,
            """
                    SELECT
                        quote_ident(n.nspname) || '.' || quote_ident(c.relname)
                        || '.' || quote_ident(con.conname)
                    FROM pg_catalog.pg_constraint AS con
                    JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
                    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND NOT con.convalidated
                    ORDER BY c.relname, con.conname
            """,
        )
    )
    public_objects = tuple(
        str(row[0])
        for row in _query_rows(
            connection,
            """
                    SELECT object_name
                    FROM (
                        SELECT 'relation:' || c.relkind::text || ':' || c.relname AS object_name
                        FROM pg_catalog.pg_class AS c
                        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public'
                        UNION ALL
                        SELECT 'function:' || p.oid::regprocedure::text AS object_name
                        FROM pg_catalog.pg_proc AS p
                        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
                        WHERE n.nspname = 'public'
                        UNION ALL
                        SELECT 'standalone-type:' || t.typname AS object_name
                        FROM pg_catalog.pg_type AS t
                        JOIN pg_catalog.pg_namespace AS n ON n.oid = t.typnamespace
                        LEFT JOIN pg_catalog.pg_class AS c ON c.reltype = t.oid
                        WHERE n.nspname = 'public'
                          AND c.oid IS NULL
                          AND t.typelem = 0
                    ) AS public_inventory
                    ORDER BY object_name
            """,
        )
    )
    non_default_extensions = tuple(
        str(row[0])
        for row in _query_rows(
            connection,
            """
                    SELECT extname
                    FROM pg_catalog.pg_extension
                    WHERE extname <> 'plpgsql'
                    ORDER BY extname
            """,
        )
    )
    user_schemas = tuple(
        str(row[0])
        for row in _query_rows(
            connection,
            """
                    SELECT nspname
                    FROM pg_catalog.pg_namespace
                    WHERE nspname NOT LIKE 'pg_%'
                      AND nspname NOT IN ('information_schema', 'public')
                    ORDER BY nspname
            """,
        )
    )
    if user_schemas:
        public_objects += tuple(f'user-schema:{schema}' for schema in user_schemas)

    return DatabaseSnapshot(
        server_version=str(server_version),
        server_version_num=int(server_version_num),
        tables=tables,
        sequences=sequences,
        alembic_revisions=alembic_revisions,
        invalid_indexes=invalid_indexes,
        unvalidated_constraints=unvalidated_constraints,
        public_objects=public_objects,
        non_default_extensions=non_default_extensions,
    )


def inspect_database(
    spec: PostgresConnectionSpec,
    *,
    read_only: bool,
) -> DatabaseSnapshot:
    try:
        with psycopg.connect(**spec.psycopg_kwargs()) as connection:
            if read_only:
                connection.execute('SET TRANSACTION READ ONLY')
            return _inspect_connection(connection)
    except PostgresBackupRestoreDrillError:
        raise
    except Exception as exc:
        detail = _redact(str(exc), [spec]).strip()
        suffix = f' Detail: {detail}' if detail else ''
        raise PostgresBackupRestoreDrillError(
            f'Could not inspect PostgreSQL endpoint {spec.evidence_label}.{suffix}'
        ) from exc


@contextmanager
def exported_source_snapshot(
    spec: PostgresConnectionSpec,
) -> Iterable[tuple[DatabaseSnapshot, str]]:
    """Hold one read-only snapshot for both source inspection and pg_dump."""
    connection: psycopg.Connection[Any] | None = None
    try:
        connection = psycopg.connect(**spec.psycopg_kwargs())
        connection.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        snapshot_id = str(connection.execute('SELECT pg_export_snapshot()').fetchone()[0])
        yield _inspect_connection(connection), snapshot_id
    except PostgresBackupRestoreDrillError:
        raise
    except Exception as exc:
        detail = _redact(str(exc), [spec]).strip()
        suffix = f' Detail: {detail}' if detail else ''
        raise PostgresBackupRestoreDrillError(
            f'Could not export a read-only PostgreSQL source snapshot.{suffix}'
        ) from exc
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()


def _assert_source_is_drillable(snapshot: DatabaseSnapshot) -> None:
    if not snapshot.tables:
        raise PostgresBackupRestoreDrillError(
            'Source PostgreSQL database has no public tables; refusing an ambiguous empty-source drill.'
        )
    if 'alembic_version' not in snapshot.tables or not snapshot.alembic_revisions:
        raise PostgresBackupRestoreDrillError(
            'Source PostgreSQL database does not contain a recorded Alembic revision.'
        )


def _assert_target_is_empty(snapshot: DatabaseSnapshot) -> None:
    if snapshot.public_objects or snapshot.non_default_extensions:
        raise PostgresBackupRestoreDrillError(
            'The separately supplied target is not empty; restore was not started.'
        )


def _snapshot_payload(snapshot: DatabaseSnapshot) -> dict[str, Any]:
    return {
        'server_version': snapshot.server_version,
        'server_version_num': snapshot.server_version_num,
        'server_major': snapshot.server_major,
        'tables': dict(sorted(snapshot.tables.items())),
        'sequences': {
            name: asdict(sequence)
            for name, sequence in sorted(snapshot.sequences.items())
        },
        'alembic_revisions': list(snapshot.alembic_revisions),
        'invalid_indexes': list(snapshot.invalid_indexes),
        'unvalidated_constraints': list(snapshot.unvalidated_constraints),
        'public_object_count': len(snapshot.public_objects),
        'non_default_extensions': list(snapshot.non_default_extensions),
    }


def compare_snapshots(
    source: DatabaseSnapshot,
    restored: DatabaseSnapshot,
) -> dict[str, bool]:
    return {
        'public_table_set_matches': set(source.tables) == set(restored.tables),
        'public_table_row_counts_match': source.tables == restored.tables,
        'public_sequences_match': source.sequences == restored.sequences,
        'alembic_revision_matches': source.alembic_revisions == restored.alembic_revisions,
        'invalid_indexes_match': source.invalid_indexes == restored.invalid_indexes,
        'unvalidated_constraints_match': (
            source.unvalidated_constraints == restored.unvalidated_constraints
        ),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _secure_create(path: Path) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    path.chmod(0o600)


def _write_secure(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(content)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    path.chmod(0o600)


def _render_markdown(evidence: Mapping[str, Any]) -> str:
    checks = evidence['checks']
    rows = ['| Validation | Passed |', '| --- | --- |']
    for name, passed in checks.items():
        rows.append(f'| `{name}` | {"yes" if passed else "no"} |')
    source_snapshot = evidence['source']['snapshot']
    restored_snapshot = evidence['target']['restored_snapshot']
    revisions = source_snapshot['alembic_revisions'] or ['none']
    return '\n'.join(
        [
            '# PostgreSQL Backup/Restore Drill Evidence',
            '',
            f'- Status: **{evidence["status"]}**',
            f'- Generated at: `{evidence["generated_at"]}`',
            f'- Source endpoint: `{evidence["source"]["endpoint"]}`',
            (
                '- Source PostgreSQL: '
                f'`{source_snapshot["server_version"]}` '
                f'(`server_version_num={source_snapshot["server_version_num"]}`)'
            ),
            f'- Empty target endpoint: `{evidence["target"]["endpoint"]}`',
            (
                '- Target PostgreSQL: '
                f'`{restored_snapshot["server_version"]}` '
                f'(`server_version_num={restored_snapshot["server_version_num"]}`)'
            ),
            f'- pg_dump: `{evidence["client_tools"]["pg_dump"]["version"]}`',
            f'- pg_restore: `{evidence["client_tools"]["pg_restore"]["version"]}`',
            f'- Archive: `{evidence["archive"]["path"]}`',
            f'- Archive SHA-256: `{evidence["archive"]["sha256"]}`',
            f'- Archive bytes: {evidence["archive"]["size_bytes"]}',
            f'- Archive mode: `{evidence["archive"]["mode"]}`',
            f'- Archive list entries: {evidence["archive"]["list_entries"]}',
            f'- Alembic revision(s): `{", ".join(revisions)}`',
            '',
            '## Validation',
            '',
            *rows,
            '',
            '## Snapshot summary',
            '',
            f'- Source public tables: {len(source_snapshot["tables"])}',
            f'- Source rows across public tables: {sum(source_snapshot["tables"].values())}',
            f'- Restored public tables: {len(restored_snapshot["tables"])}',
            f'- Restored rows across public tables: {sum(restored_snapshot["tables"].values())}',
            f'- Source/restored sequences: {len(source_snapshot["sequences"])}',
            f'- Invalid indexes: {len(source_snapshot["invalid_indexes"])}',
            f'- Unvalidated constraints: {len(source_snapshot["unvalidated_constraints"])}',
            '',
            'The source connection was inspected read-only. `pg_restore` was invoked only against the',
            'separately supplied, twice-verified empty target and used a single transaction.',
            '',
        ]
    )


def _write_evidence(
    *,
    evidence: dict[str, Any],
    markdown_path: Path,
    json_path: Path,
) -> None:
    _write_secure(json_path, json.dumps(evidence, indent=2, sort_keys=True) + '\n')
    _write_secure(markdown_path, _render_markdown(evidence))


def run_postgres_backup_restore_drill(
    *,
    source_uri: str,
    empty_target_uri: str,
    output_dir: Path | None = None,
    pg_dump_tool: str = 'pg_dump',
    pg_restore_tool: str = 'pg_restore',
    expected_source_major: int | None = None,
    expected_target_major: int | None = None,
    expected_pg_dump_major: int | None = None,
    expected_pg_restore_major: int | None = None,
) -> PostgresBackupRestoreDrillResult:
    source = parse_postgres_uri(source_uri, label='Source')
    target = parse_postgres_uri(empty_target_uri, label='Empty target')
    validate_distinct_endpoints(source, target)
    specs = [source, target]

    expected_majors = {
        'source_server': expected_source_major,
        'target_server': expected_target_major,
        'pg_dump': expected_pg_dump_major,
        'pg_restore': expected_pg_restore_major,
    }
    for label, expected_major in expected_majors.items():
        _validate_expected_major(expected_major, label=label)

    pg_dump = _resolve_tool(pg_dump_tool)
    pg_restore = _resolve_tool(pg_restore_tool)
    pg_dump_version = inspect_postgres_tool_version(
        pg_dump,
        expected_tool='pg_dump',
        specs=specs,
    )
    _assert_expected_major(
        label='pg_dump',
        actual_major=pg_dump_version.major,
        expected_major=expected_pg_dump_major,
    )
    pg_restore_version = inspect_postgres_tool_version(
        pg_restore,
        expected_tool='pg_restore',
        specs=specs,
    )
    _assert_expected_major(
        label='pg_restore',
        actual_major=pg_restore_version.major,
        expected_major=expected_pg_restore_major,
    )

    drill_dir = (output_dir or DEFAULT_OUTPUT_DIR).expanduser().resolve()
    timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    archive_path = drill_dir / f'postgres-backup-{timestamp}.dump'
    markdown_path = drill_dir / f'postgres-backup-restore-evidence-{timestamp}.md'
    json_path = drill_dir / f'postgres-backup-restore-evidence-{timestamp}.json'

    with exported_source_snapshot(source) as (source_snapshot, source_snapshot_id):
        _assert_source_is_drillable(source_snapshot)
        _assert_expected_major(
            label='Source PostgreSQL server',
            actual_major=source_snapshot.server_major,
            expected_major=expected_source_major,
        )
        first_target_snapshot = inspect_database(target, read_only=True)
        _assert_expected_major(
            label='Target PostgreSQL server',
            actual_major=first_target_snapshot.server_major,
            expected_major=expected_target_major,
        )
        _assert_target_is_empty(first_target_snapshot)

        drill_dir.mkdir(parents=True, exist_ok=True)
        drill_dir.chmod(0o700)
        _secure_create(archive_path)
        _run_tool(
            pg_dump,
            [
                '--format=custom',
                '--no-owner',
                '--no-privileges',
                '--snapshot',
                source_snapshot_id,
                '--file',
                str(archive_path),
                '--dbname',
                source.database,
            ],
            label='PostgreSQL custom-format backup',
            specs=specs,
            env=source.pg_environment(read_only=True),
        )
    archive_path.chmod(0o600)
    archive_mode = stat.S_IMODE(archive_path.stat().st_mode)
    if archive_mode != 0o600:
        raise PostgresBackupRestoreDrillError('Backup archive mode is not 0600.')
    with archive_path.open('rb') as handle:
        if handle.read(len(ARCHIVE_MAGIC)) != ARCHIVE_MAGIC:
            raise PostgresBackupRestoreDrillError('Backup archive is not PostgreSQL custom format.')
    archive_sha256 = _sha256(archive_path)
    archive_size = archive_path.stat().st_size

    archive_list = _run_tool(
        pg_restore,
        ['--list', str(archive_path)],
        label='PostgreSQL archive list validation',
        specs=specs,
    )
    archive_list_entries = sum(
        1
        for line in archive_list.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith(';')
    )
    if archive_list_entries < 1:
        raise PostgresBackupRestoreDrillError('PostgreSQL archive list contains no restore entries.')

    # Re-check immediately before pg_restore so the restore never starts after a
    # target was populated between the initial preflight and the dump.
    second_target_snapshot = inspect_database(target, read_only=True)
    if second_target_snapshot.server_version_num != first_target_snapshot.server_version_num:
        raise PostgresBackupRestoreDrillError(
            'Target PostgreSQL server version changed after preflight; restore was not started.'
        )
    _assert_expected_major(
        label='Target PostgreSQL server',
        actual_major=second_target_snapshot.server_major,
        expected_major=expected_target_major,
    )
    _assert_target_is_empty(second_target_snapshot)
    _run_tool(
        pg_restore,
        [
            '--exit-on-error',
            '--single-transaction',
            '--no-owner',
            '--no-privileges',
            '--dbname',
            target.database,
            str(archive_path),
        ],
        label='PostgreSQL restore into empty target',
        specs=specs,
        env=target.pg_environment(),
    )
    if _sha256(archive_path) != archive_sha256:
        raise PostgresBackupRestoreDrillError('Backup archive checksum changed during restore.')

    restored_snapshot = inspect_database(target, read_only=True)
    checks = {
        'source_and_target_endpoints_are_distinct': True,
        'source_contains_public_tables': bool(source_snapshot.tables),
        'target_was_empty_before_dump': not (
            first_target_snapshot.public_objects
            or first_target_snapshot.non_default_extensions
        ),
        'target_was_empty_before_restore': not (
            second_target_snapshot.public_objects
            or second_target_snapshot.non_default_extensions
        ),
        'archive_is_custom_format': True,
        'archive_mode_is_0600': archive_mode == 0o600,
        'archive_list_is_valid': archive_list_entries > 0,
        'archive_checksum_is_stable': True,
        'restore_was_single_transaction': True,
        'source_server_major_guard_matches': (
            expected_source_major is None
            or source_snapshot.server_major == expected_source_major
        ),
        'target_server_major_guard_matches': (
            expected_target_major is None
            or restored_snapshot.server_major == expected_target_major
        ),
        'target_server_version_stayed_stable': (
            first_target_snapshot.server_version_num
            == second_target_snapshot.server_version_num
            == restored_snapshot.server_version_num
        ),
        'pg_dump_major_guard_matches': (
            expected_pg_dump_major is None
            or pg_dump_version.major == expected_pg_dump_major
        ),
        'pg_restore_major_guard_matches': (
            expected_pg_restore_major is None
            or pg_restore_version.major == expected_pg_restore_major
        ),
        **compare_snapshots(source_snapshot, restored_snapshot),
    }
    status = 'passed' if all(checks.values()) else 'failed'
    evidence = {
        'schema_version': 2,
        'generated_at': datetime.now(UTC).isoformat(),
        'status': status,
        'source': {
            'endpoint': source.evidence_label,
            'inspection_mode': 'read-only',
            'snapshot': _snapshot_payload(source_snapshot),
        },
        'target': {
            'endpoint': target.evidence_label,
            'preflight_checks': 2,
            'restore_mode': 'single-transaction, exit-on-error, no-owner, no-privileges',
            'restored_snapshot': _snapshot_payload(restored_snapshot),
        },
        'client_tools': {
            'pg_dump': asdict(pg_dump_version),
            'pg_restore': asdict(pg_restore_version),
        },
        'expected_majors': expected_majors,
        'archive': {
            'path': str(archive_path),
            'format': 'PostgreSQL custom',
            'mode': '0600',
            'sha256': archive_sha256,
            'size_bytes': archive_size,
            'list_entries': archive_list_entries,
        },
        'checks': checks,
    }
    _write_evidence(evidence=evidence, markdown_path=markdown_path, json_path=json_path)
    if status != 'passed':
        raise PostgresBackupRestoreDrillError(
            f'Restored target did not match the source; review {markdown_path}.'
        )

    return PostgresBackupRestoreDrillResult(
        archive_path=archive_path,
        markdown_evidence_path=markdown_path,
        json_evidence_path=json_path,
        archive_sha256=archive_sha256,
        archive_size_bytes=archive_size,
        archive_list_entries=archive_list_entries,
        table_count=len(source_snapshot.tables),
        row_count=sum(source_snapshot.tables.values()),
        alembic_revisions=source_snapshot.alembic_revisions,
    )


def _uri_from_args(direct_uri: str | None, uri_file: Path | None, *, label: str) -> str:
    if direct_uri:
        return direct_uri
    if uri_file is None:
        raise PostgresBackupRestoreDrillError(f'{label} PostgreSQL URI is required.')
    path = uri_file.expanduser()
    try:
        initial_stat = path.lstat()
    except OSError as exc:
        raise PostgresBackupRestoreDrillError(f'Could not read {label.lower()} URI file.') from exc
    if not stat.S_ISREG(initial_stat.st_mode):
        raise PostgresBackupRestoreDrillError(
            f'{label} URI file must be a regular file; symlinks are not accepted.'
        )
    if stat.S_IMODE(initial_stat.st_mode) != 0o600:
        raise PostgresBackupRestoreDrillError(f'{label} URI file must have mode 0600 exactly.')

    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise PostgresBackupRestoreDrillError(
                f'{label} URI file must be a regular file; symlinks are not accepted.'
            )
        if stat.S_IMODE(opened_stat.st_mode) != 0o600:
            raise PostgresBackupRestoreDrillError(f'{label} URI file must have mode 0600 exactly.')
        if (initial_stat.st_dev, initial_stat.st_ino) != (opened_stat.st_dev, opened_stat.st_ino):
            raise PostgresBackupRestoreDrillError(
                f'{label} URI file changed during safety validation.'
            )
        with os.fdopen(descriptor, encoding='utf-8') as handle:
            descriptor = None
            return handle.read().strip()
    except PostgresBackupRestoreDrillError:
        raise
    except (OSError, UnicodeError) as exc:
        raise PostgresBackupRestoreDrillError(
            f'Could not safely read {label.lower()} URI file.'
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _positive_major(value: str) -> int:
    try:
        major = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('expected major must be a positive integer') from exc
    if major < 1:
        raise argparse.ArgumentTypeError('expected major must be a positive integer')
    return major


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Create a PostgreSQL custom-format backup, restore it only into a separately supplied '
            'empty database, and emit comparison evidence.'
        )
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument('--source-uri', help='PostgreSQL source URI. Prefer --source-uri-file.')
    source_group.add_argument(
        '--source-uri-file',
        type=Path,
        help='Mode-0600 file containing only the PostgreSQL source URI.',
    )
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument(
        '--empty-target-uri',
        help='Distinct, empty PostgreSQL target URI. Prefer --empty-target-uri-file.',
    )
    target_group.add_argument(
        '--empty-target-uri-file',
        type=Path,
        help='Mode-0600 file containing only the distinct, empty PostgreSQL target URI.',
    )
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument('--pg-dump', default='pg_dump', help='pg_dump executable name or path.')
    parser.add_argument('--pg-restore', default='pg_restore', help='pg_restore executable name or path.')
    parser.add_argument(
        '--expected-source-major',
        type=_positive_major,
        help='Fail before backup unless the source PostgreSQL server has this major version.',
    )
    parser.add_argument(
        '--expected-target-major',
        type=_positive_major,
        help='Fail before backup/restore unless the target server has this major version.',
    )
    parser.add_argument(
        '--expected-pg-dump-major',
        type=_positive_major,
        help='Fail before database access unless pg_dump has this major version.',
    )
    parser.add_argument(
        '--expected-pg-restore-major',
        type=_positive_major,
        help='Fail before database access unless pg_restore has this major version.',
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        source_uri = _uri_from_args(args.source_uri, args.source_uri_file, label='Source')
        target_uri = _uri_from_args(
            args.empty_target_uri,
            args.empty_target_uri_file,
            label='Empty target',
        )
        result = run_postgres_backup_restore_drill(
            source_uri=source_uri,
            empty_target_uri=target_uri,
            output_dir=args.output_dir,
            pg_dump_tool=args.pg_dump,
            pg_restore_tool=args.pg_restore,
            expected_source_major=args.expected_source_major,
            expected_target_major=args.expected_target_major,
            expected_pg_dump_major=args.expected_pg_dump_major,
            expected_pg_restore_major=args.expected_pg_restore_major,
        )
    except PostgresBackupRestoreDrillError as exc:
        # All lower-level exceptions are sanitized before reaching this boundary.
        print(f'[postgres-backup-restore-drill][error] {exc}', file=sys.stderr)
        return 1

    print('[postgres-backup-restore-drill] PostgreSQL backup/restore drill passed.')
    print(f'[postgres-backup-restore-drill] Archive: {result.archive_path}')
    print(f'[postgres-backup-restore-drill] Markdown evidence: {result.markdown_evidence_path}')
    print(f'[postgres-backup-restore-drill] JSON evidence: {result.json_evidence_path}')
    print(
        '[postgres-backup-restore-drill] '
        f'tables={result.table_count} rows={result.row_count} '
        f'archive_sha256={result.archive_sha256}'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
