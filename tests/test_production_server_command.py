from __future__ import annotations

import os
from pathlib import Path
import subprocess


def _production_env(**overrides: str) -> dict[str, str]:
    return {**os.environ, 'AIDM_ENV': 'production', **overrides}


def test_production_server_command_prints_single_worker_eventlet_command():
    env = _production_env(
        PORT='6060',
        GUNICORN_BIN='gunicorn-test',
        AIDM_GUNICORN_TIMEOUT='90',
    )

    result = subprocess.run(
        ['bash', 'scripts/run_production_server.sh', '--print'],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    command = result.stdout.strip()
    assert command.startswith('gunicorn-test ')
    assert '--worker-class eventlet' in command
    assert '--workers 1' in command
    assert '--bind 0.0.0.0:6060' in command
    assert '--timeout 90' in command
    assert command.endswith('aidm_server.wsgi:app')


def test_production_server_command_rejects_multi_worker_single_model():
    env = _production_env(AIDM_SOCKETIO_WORKER_MODEL='single', WEB_CONCURRENCY='2')

    result = subprocess.run(
        ['bash', 'scripts/run_production_server.sh', '--print'],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert 'AIDM_SOCKETIO_WORKER_MODEL=single requires WEB_CONCURRENCY=1.' in result.stderr


def test_production_server_command_runs_preflight_before_gunicorn(tmp_path: Path):
    log_path = tmp_path / 'commands.log'
    python_bin = tmp_path / 'python-test'
    gunicorn_bin = tmp_path / 'gunicorn-test'
    python_bin.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "${1:-}" == "-c" ]]; then exit 0; fi\n'
        'printf \'python %s\\n\' "$*" >> "$AIDM_TEST_COMMAND_LOG"\n',
        encoding='utf-8',
    )
    gunicorn_bin.write_text(
        '#!/usr/bin/env bash\nprintf \'gunicorn %s\\n\' "$*" >> "$AIDM_TEST_COMMAND_LOG"\n',
        encoding='utf-8',
    )
    python_bin.chmod(0o755)
    gunicorn_bin.chmod(0o755)
    env = _production_env(
        PYTHON_BIN=str(python_bin),
        GUNICORN_BIN=str(gunicorn_bin),
        AIDM_TEST_COMMAND_LOG=str(log_path),
    )

    result = subprocess.run(
        ['bash', 'scripts/run_production_server.sh'],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    commands = log_path.read_text(encoding='utf-8').splitlines()
    assert commands[0] == 'python scripts/deploy_bootstrap.py --check-only --host 0.0.0.0'
    assert commands[1].startswith('gunicorn --worker-class eventlet --workers 1 ')
    assert commands[1].endswith('aidm_server.wsgi:app')


def test_production_server_command_rejects_non_production_environment():
    env = {**os.environ, 'AIDM_ENV': 'development'}

    result = subprocess.run(
        ['bash', 'scripts/run_production_server.sh', '--print'],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert 'AIDM_ENV must be explicitly set to production' in result.stderr
