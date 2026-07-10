from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import stat
import subprocess

import pytest

from scripts import postgres_backup_restore_drill as drill


SOURCE_URI = (
    'postgresql+psycopg://source_user:source-secret@source.example.test:5432/aidm'
    '?sslmode=require'
)
TARGET_URI = (
    'postgresql+psycopg://target_user:target-secret@target.example.test:5433/aidm_restore'
    '?sslmode=require'
)


def _sequence() -> drill.SequenceSnapshot:
    return drill.SequenceSnapshot(
        data_type='bigint',
        start_value=1,
        minimum_value=1,
        maximum_value=9223372036854775807,
        increment_by=1,
        cache_size=1,
        cycles=False,
        last_value=7,
        is_called=True,
    )


def _source_snapshot(
    *,
    session_rows: int = 3,
    server_version: str = '17.10',
    server_version_num: int = 170010,
) -> drill.DatabaseSnapshot:
    return drill.DatabaseSnapshot(
        server_version=server_version,
        server_version_num=server_version_num,
        tables={'alembic_version': 1, 'sessions': session_rows},
        sequences={'sessions_session_id_seq': _sequence()},
        alembic_revisions=('0030_example_head',),
        invalid_indexes=(),
        unvalidated_constraints=('public.sessions.sessions_future_check',),
        public_objects=(
            'relation:r:alembic_version',
            'relation:r:sessions',
            'relation:S:sessions_session_id_seq',
        ),
        non_default_extensions=(),
    )


def _restored_snapshot(*, session_rows: int = 3) -> drill.DatabaseSnapshot:
    return _source_snapshot(
        session_rows=session_rows,
        server_version='18.4',
        server_version_num=180004,
    )


def _empty_snapshot(
    *,
    object_name: str | None = None,
    server_version: str = '18.4',
    server_version_num: int = 180004,
) -> drill.DatabaseSnapshot:
    return drill.DatabaseSnapshot(
        server_version=server_version,
        server_version_num=server_version_num,
        tables={},
        sequences={},
        alembic_revisions=(),
        invalid_indexes=(),
        unvalidated_constraints=(),
        public_objects=(object_name,) if object_name else (),
        non_default_extensions=(),
    )


def _install_snapshot_fakes(monkeypatch: pytest.MonkeyPatch, snapshots: list[drill.DatabaseSnapshot]):
    source_snapshot = snapshots.pop(0)

    @contextmanager
    def fake_exported_source_snapshot(_spec: drill.PostgresConnectionSpec):
        yield source_snapshot, '00000003-0000001A-1'

    target_snapshots = iter(snapshots)
    monkeypatch.setattr(drill, 'exported_source_snapshot', fake_exported_source_snapshot)
    monkeypatch.setattr(
        drill,
        'inspect_database',
        lambda _spec, *, read_only: next(target_snapshots),
    )


def _install_tool_fakes(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(drill, '_resolve_tool', lambda tool: f'/tools/{tool}')

    def fake_run_tool(
        executable: str,
        args: list[str],
        *,
        label: str,
        specs: list[drill.PostgresConnectionSpec],
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(
            {
                'executable': executable,
                'args': list(args),
                'label': label,
                'specs': specs,
                'env': dict(env) if env is not None else None,
            }
        )
        if args == ['--version']:
            tool = Path(executable).name
            stdout = f'{tool} (PostgreSQL) 18.4 (Homebrew)\n'
            return subprocess.CompletedProcess([executable, *args], 0, stdout, '')
        if executable.endswith('/pg_dump') and '--file' in args:
            archive = Path(args[args.index('--file') + 1])
            archive.write_bytes(b'PGDMPfixture-archive-payload')
        stdout = ''
        if '--list' in args:
            stdout = '; PostgreSQL database dump\n1; 0 0 TABLE public sessions owner\n'
        return subprocess.CompletedProcess([executable, *args], 0, stdout, '')

    monkeypatch.setattr(drill, '_run_tool', fake_run_tool)
    return calls


def test_parse_postgres_uri_rejects_non_postgres_and_unknown_options():
    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='must use postgresql'):
        drill.parse_postgres_uri('sqlite:///tmp/aidm.db', label='Source')

    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='unsupported connection'):
        drill.parse_postgres_uri(
            'postgresql://user:secret@db.example.test/aidm?unknown=value',
            label='Source',
        )


def test_distinct_check_rejects_same_endpoint_even_with_different_credentials():
    source = drill.parse_postgres_uri(
        'postgresql://source:first-secret@db.example.test:5432/aidm',
        label='Source',
    )
    target = drill.parse_postgres_uri(
        'postgresql://target:second-secret@DB.EXAMPLE.TEST/aidm',
        label='Empty target',
    )

    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='distinct PostgreSQL'):
        drill.validate_distinct_endpoints(source, target)


def test_pg_environment_keeps_credentials_out_of_connection_arguments():
    spec = drill.parse_postgres_uri(SOURCE_URI, label='Source')

    env = spec.pg_environment(
        base_env={'PGPASSWORD': 'inherited-wrong-secret', 'PATH': '/usr/bin'},
        read_only=True,
    )

    assert env['PGHOST'] == 'source.example.test'
    assert env['PGPORT'] == '5432'
    assert env['PGDATABASE'] == 'aidm'
    assert env['PGUSER'] == 'source_user'
    assert env['PGPASSWORD'] == 'source-secret'
    assert env['PGSSLMODE'] == 'require'
    assert 'default_transaction_read_only=on' in env['PGOPTIONS']
    assert 'inherited-wrong-secret' not in env.values()


def test_uri_file_accepts_only_an_exact_0600_regular_file(tmp_path: Path):
    uri_file = tmp_path / 'source-uri'
    uri_file.write_text(f'{SOURCE_URI}\n', encoding='utf-8')
    uri_file.chmod(0o600)

    assert drill._uri_from_args(None, uri_file, label='Source') == SOURCE_URI


def test_uri_file_rejects_permissive_mode(tmp_path: Path):
    uri_file = tmp_path / 'source-uri'
    uri_file.write_text(SOURCE_URI, encoding='utf-8')
    uri_file.chmod(0o640)

    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='mode 0600 exactly'):
        drill._uri_from_args(None, uri_file, label='Source')


def test_uri_file_rejects_non_regular_files_and_symlinks(tmp_path: Path):
    directory = tmp_path / 'uri-directory'
    directory.mkdir(mode=0o700)
    target = tmp_path / 'real-uri'
    target.write_text(SOURCE_URI, encoding='utf-8')
    target.chmod(0o600)
    symlink = tmp_path / 'uri-symlink'
    symlink.symlink_to(target)

    for unsafe_path in (directory, symlink):
        with pytest.raises(drill.PostgresBackupRestoreDrillError, match='regular file'):
            drill._uri_from_args(None, unsafe_path, label='Source')


def test_source_requires_recorded_alembic_revision():
    snapshot = drill.DatabaseSnapshot(
        server_version='17.10',
        server_version_num=170010,
        tables={'sessions': 1},
        sequences={},
        alembic_revisions=(),
        invalid_indexes=(),
        unvalidated_constraints=(),
        public_objects=('relation:r:sessions',),
        non_default_extensions=(),
    )

    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='Alembic revision'):
        drill._assert_source_is_drillable(snapshot)


def test_postgres_catalog_inventory_casts_internal_char_type_for_postgres_18():
    source = Path(drill.__file__).read_text(encoding='utf-8')

    assert "'relation:' || c.relkind::text || ':' || c.relname" in source


@pytest.mark.parametrize(
    ('output', 'expected_tool', 'expected_version', 'expected_major'),
    [
        ('pg_dump (PostgreSQL) 18.4 (Homebrew)\n', 'pg_dump', '18.4', 18),
        ('pg_restore (PostgreSQL) 17.10 (Ubuntu 17.10-1)\n', 'pg_restore', '17.10', 17),
        ('pg_dump (PostgreSQL) 19beta2\n', 'pg_dump', '19beta2', 19),
    ],
)
def test_postgres_client_version_parser_records_version_and_major(
    output: str,
    expected_tool: str,
    expected_version: str,
    expected_major: int,
):
    parsed = drill._parse_postgres_tool_version(output, expected_tool=expected_tool)

    assert parsed.tool == expected_tool
    assert parsed.version == expected_version
    assert parsed.major == expected_major


def test_postgres_client_version_parser_rejects_wrong_or_unparseable_tool():
    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='Could not parse pg_dump'):
        drill._parse_postgres_tool_version(
            'pg_restore (PostgreSQL) 18.4',
            expected_tool='pg_dump',
        )

    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='Could not parse pg_restore'):
        drill._parse_postgres_tool_version('unknown client', expected_tool='pg_restore')


def test_drill_uses_custom_archive_empty_target_and_writes_redacted_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source_snapshot = _source_snapshot()
    _install_snapshot_fakes(
        monkeypatch,
        [source_snapshot, _empty_snapshot(), _empty_snapshot(), _restored_snapshot()],
    )
    calls = _install_tool_fakes(monkeypatch)

    result = drill.run_postgres_backup_restore_drill(
        source_uri=SOURCE_URI,
        empty_target_uri=TARGET_URI,
        output_dir=tmp_path / 'evidence',
        expected_source_major=17,
        expected_target_major=18,
        expected_pg_dump_major=18,
        expected_pg_restore_major=18,
    )

    dump_version_call, restore_version_call, dump_call, list_call, restore_call = calls
    assert dump_version_call['args'] == ['--version']
    assert restore_version_call['args'] == ['--version']
    dump_args = dump_call['args']
    restore_args = restore_call['args']
    assert '--format=custom' in dump_args
    assert '--snapshot' in dump_args
    assert '--file' in dump_args
    assert '--list' in list_call['args']
    assert '--exit-on-error' in restore_args
    assert '--single-transaction' in restore_args
    assert '--no-owner' in restore_args
    assert '--no-privileges' in restore_args
    assert dump_call['env']['PGPASSWORD'] == 'source-secret'
    assert restore_call['env']['PGPASSWORD'] == 'target-secret'
    assert dump_call['env']['PGHOST'] == 'source.example.test'
    assert restore_call['env']['PGHOST'] == 'target.example.test'

    all_arguments = json.dumps([call['args'] for call in calls])
    assert 'source-secret' not in all_arguments
    assert 'target-secret' not in all_arguments
    assert SOURCE_URI not in all_arguments
    assert TARGET_URI not in all_arguments

    assert stat.S_IMODE(result.archive_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(result.markdown_evidence_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(result.json_evidence_path.stat().st_mode) == 0o600
    evidence_text = result.markdown_evidence_path.read_text(encoding='utf-8')
    evidence_text += result.json_evidence_path.read_text(encoding='utf-8')
    assert 'source-secret' not in evidence_text
    assert 'target-secret' not in evidence_text
    assert 'source_user' not in evidence_text
    assert 'target_user' not in evidence_text

    evidence = json.loads(result.json_evidence_path.read_text(encoding='utf-8'))
    assert evidence['status'] == 'passed'
    assert evidence['checks']['public_table_row_counts_match'] is True
    assert evidence['checks']['public_sequences_match'] is True
    assert evidence['checks']['alembic_revision_matches'] is True
    assert evidence['checks']['invalid_indexes_match'] is True
    assert evidence['checks']['unvalidated_constraints_match'] is True
    assert evidence['archive']['format'] == 'PostgreSQL custom'
    assert evidence['source']['snapshot']['server_version'] == '17.10'
    assert evidence['source']['snapshot']['server_version_num'] == 170010
    assert evidence['target']['restored_snapshot']['server_version'] == '18.4'
    assert evidence['target']['restored_snapshot']['server_version_num'] == 180004
    assert evidence['client_tools']['pg_dump']['version'] == '18.4'
    assert evidence['client_tools']['pg_restore']['version'] == '18.4'
    assert evidence['expected_majors'] == {
        'pg_dump': 18,
        'pg_restore': 18,
        'source_server': 17,
        'target_server': 18,
    }
    assert evidence['checks']['source_server_major_guard_matches'] is True
    assert evidence['checks']['target_server_major_guard_matches'] is True
    assert evidence['checks']['target_server_version_stayed_stable'] is True
    assert result.table_count == 2
    assert result.row_count == 4


def test_drill_refuses_nonempty_target_before_running_pg_dump(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_snapshot_fakes(
        monkeypatch,
        [_source_snapshot(), _empty_snapshot(object_name='relation:r:existing_table')],
    )
    calls = _install_tool_fakes(monkeypatch)

    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='target is not empty'):
        drill.run_postgres_backup_restore_drill(
            source_uri=SOURCE_URI,
            empty_target_uri=TARGET_URI,
            output_dir=tmp_path,
        )

    assert [call['args'] for call in calls] == [['--version'], ['--version']]


def test_invalid_function_major_guard_fails_before_tools_or_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        drill,
        '_resolve_tool',
        lambda _tool: pytest.fail('tool resolution must not run for an invalid guard'),
    )

    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='positive integer'):
        drill.run_postgres_backup_restore_drill(
            source_uri=SOURCE_URI,
            empty_target_uri=TARGET_URI,
            output_dir=tmp_path,
            expected_source_major=0,
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ('guard_name', 'guard_value', 'expected_version_calls'),
    [
        ('expected_pg_dump_major', 17, [['--version']]),
        ('expected_pg_restore_major', 17, [['--version'], ['--version']]),
    ],
)
def test_client_major_guards_fail_before_database_access_dump_or_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guard_name: str,
    guard_value: int,
    expected_version_calls: list[list[str]],
):
    monkeypatch.setattr(
        drill,
        'exported_source_snapshot',
        lambda _spec: pytest.fail('database access must not run after a client guard mismatch'),
    )
    calls = _install_tool_fakes(monkeypatch)

    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='major version mismatch'):
        drill.run_postgres_backup_restore_drill(
            source_uri=SOURCE_URI,
            empty_target_uri=TARGET_URI,
            output_dir=tmp_path,
            **{guard_name: guard_value},
        )

    assert [call['args'] for call in calls] == expected_version_calls
    assert list(tmp_path.iterdir()) == []


def test_source_server_major_guard_fails_before_target_access_dump_or_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_snapshot_fakes(monkeypatch, [_source_snapshot()])
    calls = _install_tool_fakes(monkeypatch)

    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='Source PostgreSQL server'):
        drill.run_postgres_backup_restore_drill(
            source_uri=SOURCE_URI,
            empty_target_uri=TARGET_URI,
            output_dir=tmp_path,
            expected_source_major=18,
        )

    assert [call['args'] for call in calls] == [['--version'], ['--version']]
    assert list(tmp_path.iterdir()) == []


def test_target_server_major_guard_fails_before_dump_or_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_snapshot_fakes(monkeypatch, [_source_snapshot(), _empty_snapshot()])
    calls = _install_tool_fakes(monkeypatch)

    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='Target PostgreSQL server'):
        drill.run_postgres_backup_restore_drill(
            source_uri=SOURCE_URI,
            empty_target_uri=TARGET_URI,
            output_dir=tmp_path,
            expected_source_major=17,
            expected_target_major=17,
        )

    assert [call['args'] for call in calls] == [['--version'], ['--version']]
    assert list(tmp_path.iterdir()) == []


def test_target_server_guard_is_rechecked_before_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_snapshot_fakes(
        monkeypatch,
        [
            _source_snapshot(),
            _empty_snapshot(),
            _empty_snapshot(server_version='19.0', server_version_num=190000),
        ],
    )
    calls = _install_tool_fakes(monkeypatch)

    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='version changed'):
        drill.run_postgres_backup_restore_drill(
            source_uri=SOURCE_URI,
            empty_target_uri=TARGET_URI,
            output_dir=tmp_path,
            expected_target_major=18,
        )

    assert any('--format=custom' in call['args'] for call in calls)
    assert any('--list' in call['args'] for call in calls)
    assert not any('--exit-on-error' in call['args'] for call in calls)


def test_cli_accepts_all_optional_expected_major_guards():
    args = drill.build_parser().parse_args(
        [
            '--source-uri',
            SOURCE_URI,
            '--empty-target-uri',
            TARGET_URI,
            '--expected-source-major',
            '17',
            '--expected-target-major',
            '18',
            '--expected-pg-dump-major',
            '18',
            '--expected-pg-restore-major',
            '18',
        ]
    )

    assert args.expected_source_major == 17
    assert args.expected_target_major == 18
    assert args.expected_pg_dump_major == 18
    assert args.expected_pg_restore_major == 18


def test_drill_emits_failed_evidence_when_restored_counts_do_not_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_snapshot_fakes(
        monkeypatch,
        [_source_snapshot(), _empty_snapshot(), _empty_snapshot(), _restored_snapshot(session_rows=2)],
    )
    _install_tool_fakes(monkeypatch)

    with pytest.raises(drill.PostgresBackupRestoreDrillError, match='did not match'):
        drill.run_postgres_backup_restore_drill(
            source_uri=SOURCE_URI,
            empty_target_uri=TARGET_URI,
            output_dir=tmp_path,
        )

    evidence_path = next(tmp_path.glob('postgres-backup-restore-evidence-*.json'))
    evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
    assert evidence['status'] == 'failed'
    assert evidence['checks']['public_table_set_matches'] is True
    assert evidence['checks']['public_table_row_counts_match'] is False


def test_tool_failures_are_redacted(monkeypatch: pytest.MonkeyPatch):
    source = drill.parse_postgres_uri(SOURCE_URI, label='Source')
    target = drill.parse_postgres_uri(TARGET_URI, label='Empty target')
    monkeypatch.setattr(
        drill.subprocess,
        'run',
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            '',
            f'connection failed for {SOURCE_URI}; password=source-secret user=source_user',
        ),
    )

    with pytest.raises(drill.PostgresBackupRestoreDrillError) as captured:
        drill._run_tool(
            '/tools/pg_dump',
            ['--dbname', 'aidm'],
            label='backup',
            specs=[source, target],
            env=source.pg_environment(base_env={}),
        )

    message = str(captured.value)
    assert SOURCE_URI not in message
    assert 'source-secret' not in message
    assert 'source_user' not in message
    assert '<redacted>' in message


def test_cli_does_not_log_credentials_when_endpoints_are_not_distinct(capsys):
    returncode = drill.main(
        [
            '--source-uri',
            'postgresql://first_user:first-secret@same.example.test/aidm',
            '--empty-target-uri',
            'postgresql://second_user:second-secret@same.example.test/aidm',
        ]
    )

    captured = capsys.readouterr()
    assert returncode == 1
    assert 'distinct PostgreSQL' in captured.err
    assert 'first-secret' not in captured.err
    assert 'second-secret' not in captured.err
    assert 'first_user' not in captured.err
    assert 'second_user' not in captured.err
