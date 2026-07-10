from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys

from aidm_server.auth import hash_secret, is_legacy_recovery_token, normalize_username, password_hash_for
from aidm_server.database import db
from aidm_server.models import Account
from scripts import issue_legacy_recovery_code


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _recovery_runtime(tmp_path, monkeypatch):
    db_path = tmp_path / 'legacy-recovery.db'
    monkeypatch.setenv('AIDM_DATABASE_URI', f'sqlite:///{db_path}')
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'true')
    monkeypatch.setenv('AIDM_AUTH_REQUIRED', 'true')
    monkeypatch.setenv('AIDM_API_AUTH_TOKENS', 'operator-token')
    monkeypatch.setenv('AIDM_ENV', 'test')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('AIDM_SOCKETIO_ASYNC_MODE', 'threading')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')
    monkeypatch.setenv('AIDM_SKIP_REPO_ENV_LOCAL', '1')

    import aidm_server.main as main_module

    main_module = importlib.reload(main_module)
    app = main_module.create_app()
    with app.app_context():
        db.create_all()
    return app


def test_operator_script_rotates_only_passwordless_account_token(tmp_path, monkeypatch, capsys):
    app = _recovery_runtime(tmp_path, monkeypatch)
    with app.app_context():
        account = Account(
            username=normalize_username('Maya'),
            first_name='Maya',
            last_name='Stone',
            password_hash=None,
            account_token_hash=hash_secret('stale-token'),
        )
        db.session.add(account)
        db.session.commit()

    assert issue_legacy_recovery_code.main(['--username', ' Maya ']) == 0
    captured = capsys.readouterr()
    recovery_code = captured.out.strip().splitlines()[-1]
    assert recovery_code
    assert is_legacy_recovery_token(recovery_code)
    assert 'stale-token' not in captured.out
    assert captured.err == ''

    with app.app_context():
        account = Account.query.filter_by(username='maya').one()
        assert account.password_hash is None
        assert account.account_token_hash == hash_secret(recovery_code)
        assert recovery_code not in account.account_token_hash


def test_operator_script_refuses_missing_or_passworded_accounts(tmp_path, monkeypatch, capsys):
    app = _recovery_runtime(tmp_path, monkeypatch)
    with app.app_context():
        db.session.add(
            Account(
                username='maya',
                first_name='Maya',
                last_name='Stone',
                password_hash=password_hash_for('already-secure'),
                account_token_hash=hash_secret('existing-token'),
            )
        )
        db.session.commit()

    assert issue_legacy_recovery_code.main(['--username', 'missing']) == 1
    assert issue_legacy_recovery_code.main(['--username', 'maya']) == 1
    captured = capsys.readouterr()
    assert 'Legacy account not found.' in captured.err
    assert 'passwordless legacy accounts' in captured.err
    assert 'existing-token' not in captured.out + captured.err


def test_operator_script_help_does_not_load_runtime_environment(tmp_path):
    env = os.environ.copy()
    env['AIDM_ENV_FILE'] = str(tmp_path / 'does-not-exist.env')
    result = subprocess.run(
        [sys.executable, 'scripts/issue_legacy_recovery_code.py', '--help'],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert '--confirm-production' in result.stdout
    assert 'does-not-exist.env' not in result.stderr
