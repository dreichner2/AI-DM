from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterable, Mapping

from sqlalchemy import create_engine, inspect, text


REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_HEAD_TABLES = {
    'accounts',
    'campaign_packs',
    'campaign_segments',
    'campaigns',
    'canon_jobs',
    'dm_coherence_feedback',
    'dm_turns',
    'operator_action_audits',
    'rate_limit_events',
    'session_log_entries',
    'session_state_mutation_audits',
    'session_states',
    'session_turn_locks',
    'sessions',
    'story_entities',
    'story_facts',
    'story_threads',
    'turn_canon_updates',
    'turn_events',
    'workspaces',
    'worlds',
}
REQUIRED_HEAD_COLUMNS = {
    'dm_coherence_feedback': {'feedback_type', 'category', 'provider', 'model', 'metadata_json'},
    'dm_turns': {'confidence', 'roll_value', 'outcome_status', 'client_message_id'},
    'operator_action_audits': {'workspace_id', 'action', 'resource_type', 'status', 'details_json'},
    'session_state_mutation_audits': {'previous_revision', 'state_revision', 'diff_json', 'metadata_json'},
    'session_turn_locks': {'owner_token', 'fencing_token', 'acquired_at', 'expires_at'},
    'sessions': {'name', 'status', 'updated_at', 'deleted_at', 'client_session_id', 'archived_by_campaign_id'},
}


class MigrationChainDrillError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationSchemaSnapshot:
    revision: str | None
    tables: set[str]
    columns_by_table: dict[str, set[str]]


@dataclass(frozen=True)
class MigrationChainDrillResult:
    database_path: Path
    initial_revision: str | None
    downgraded_revision: str | None
    reupgraded_revision: str | None
    table_count: int


def migration_env(database_uri: str, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ)
    env.update(
        {
            'FLASK_APP': 'aidm_server.main:create_app',
            'PYTHONPATH': str(REPO_ROOT),
            'PYTHON_DOTENV_DISABLED': '1',
            'AIDM_DATABASE_URI': database_uri,
            'AIDM_AUTO_CREATE_SCHEMA': 'false',
            'AIDM_ENV': 'test',
            'AIDM_DEBUG': 'false',
            'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
            'AIDM_TELEMETRY_ENABLED': 'false',
        }
    )
    return env


def _run_flask_db(args: Iterable[str], *, env: Mapping[str, str], python_executable: str) -> None:
    cmd = [python_executable, '-m', 'flask', 'db', *args]
    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=dict(env),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    raise MigrationChainDrillError(
        f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def _snapshot_database(database_uri: str) -> MigrationSchemaSnapshot:
    engine = create_engine(database_uri)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        columns_by_table = {
            table: {column['name'] for column in inspector.get_columns(table)}
            for table in tables
        }
        revision = None
        if 'alembic_version' in tables:
            with engine.connect() as conn:
                revision = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
        return MigrationSchemaSnapshot(
            revision=str(revision) if revision else None,
            tables=tables,
            columns_by_table=columns_by_table,
        )
    finally:
        engine.dispose()


def _verify_head_schema(database_uri: str) -> MigrationSchemaSnapshot:
    snapshot = _snapshot_database(database_uri)
    missing_tables = sorted(REQUIRED_HEAD_TABLES - snapshot.tables)
    if missing_tables:
        raise MigrationChainDrillError(f'Head schema is missing required tables: {", ".join(missing_tables)}.')
    missing_columns: list[str] = []
    for table, required_columns in REQUIRED_HEAD_COLUMNS.items():
        actual_columns = snapshot.columns_by_table.get(table, set())
        for column in sorted(required_columns - actual_columns):
            missing_columns.append(f'{table}.{column}')
    if missing_columns:
        raise MigrationChainDrillError(f'Head schema is missing required columns: {", ".join(missing_columns)}.')
    if not snapshot.revision:
        raise MigrationChainDrillError('Head schema did not record an Alembic revision.')
    return snapshot


def _verify_base_schema(database_uri: str) -> MigrationSchemaSnapshot:
    snapshot = _snapshot_database(database_uri)
    remaining_application_tables = sorted(table for table in snapshot.tables if table != 'alembic_version')
    if remaining_application_tables:
        raise MigrationChainDrillError(
            f'Downgrade to base left application tables behind: {", ".join(remaining_application_tables)}.'
        )
    return snapshot


def run_migration_chain_drill(
    *,
    output_dir: Path | None = None,
    python_executable: str = sys.executable,
) -> MigrationChainDrillResult:
    drill_dir = (output_dir or (REPO_ROOT / 'tmp' / 'migration_chain_drills')).expanduser().resolve()
    drill_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    database_path = drill_dir / f'migration-chain-drill-{timestamp}.sqlite'
    database_uri = f'sqlite:///{database_path}'
    env = migration_env(database_uri)

    _run_flask_db(['upgrade', 'head'], env=env, python_executable=python_executable)
    head_snapshot = _verify_head_schema(database_uri)

    _run_flask_db(['downgrade', 'base'], env=env, python_executable=python_executable)
    base_snapshot = _verify_base_schema(database_uri)

    _run_flask_db(['upgrade', 'head'], env=env, python_executable=python_executable)
    reupgraded_snapshot = _verify_head_schema(database_uri)

    if head_snapshot.revision != reupgraded_snapshot.revision:
        raise MigrationChainDrillError(
            f'Re-upgrade ended at {reupgraded_snapshot.revision}, expected {head_snapshot.revision}.'
        )

    return MigrationChainDrillResult(
        database_path=database_path,
        initial_revision=head_snapshot.revision,
        downgraded_revision=base_snapshot.revision,
        reupgraded_revision=reupgraded_snapshot.revision,
        table_count=len(reupgraded_snapshot.tables),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run a non-destructive Alembic upgrade/downgrade/upgrade drill.')
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=REPO_ROOT / 'tmp' / 'migration_chain_drills',
        help='Directory for the isolated SQLite drill database.',
    )
    parser.add_argument(
        '--python',
        default=sys.executable,
        help='Python executable used to invoke `flask db`. Defaults to the current interpreter.',
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = run_migration_chain_drill(
            output_dir=args.output_dir,
            python_executable=args.python,
        )
    except MigrationChainDrillError as exc:
        print(f'[migration-chain-drill][error] {exc}', file=sys.stderr)
        return 1

    print('[migration-chain-drill] Migration chain drill passed.')
    print(f'[migration-chain-drill] Database: {result.database_path}')
    print(f'[migration-chain-drill] Initial head revision: {result.initial_revision}')
    print(f'[migration-chain-drill] Downgraded revision: {result.downgraded_revision or "base"}')
    print(f'[migration-chain-drill] Re-upgraded revision: {result.reupgraded_revision}')
    print(f'[migration-chain-drill] Tables after re-upgrade: {result.table_count}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
