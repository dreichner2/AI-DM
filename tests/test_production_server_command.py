from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _production_env(**overrides: str) -> dict[str, str]:
    return {**os.environ, 'AIDM_ENV': 'production', 'PYTHON_BIN': sys.executable, **overrides}


def test_production_server_command_prints_single_worker_threaded_command():
    env = _production_env(
        PORT='6060',
        GUNICORN_BIN='gunicorn-test',
        AIDM_GUNICORN_THREADS='64',
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
    assert '--worker-class gthread' in command
    assert '--workers 1' in command
    assert '--threads 64' in command
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


def test_production_server_command_rejects_invalid_thread_count():
    env = _production_env(AIDM_GUNICORN_THREADS='1')

    result = subprocess.run(
        ['bash', 'scripts/run_production_server.sh', '--print'],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert 'AIDM_GUNICORN_THREADS must be an integer >= 16.' in result.stderr


def test_production_server_command_rejects_codex_without_dedicated_auth(tmp_path: Path):
    codex_executable = tmp_path / 'codex'
    codex_executable.write_text('#!/bin/sh\n', encoding='utf-8')
    codex_executable.chmod(0o755)
    env = _production_env(
        AIDM_LLM_PROVIDER='codex_cli',
        AIDM_CODEX_EXECUTABLE=str(codex_executable),
        AIDM_CODEX_HOME='',
        AIDM_CODEX_ACCESS_TOKEN='',
        CODEX_ACCESS_TOKEN='',
    )

    result = subprocess.run(
        ['bash', 'scripts/run_production_server.sh', '--print'],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert 'dedicated signed-in AIDM_CODEX_HOME' in result.stderr


def test_production_server_command_rejects_noncanonical_or_deferred_worker_models():
    for worker_model in ('SINGLE', 'single ', 'sticky', 'message_queue'):
        result = subprocess.run(
            ['bash', 'scripts/run_production_server.sh', '--print'],
            cwd=os.getcwd(),
            env=_production_env(AIDM_SOCKETIO_WORKER_MODEL=worker_model),
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 2
        assert 'currently supports only AIDM_SOCKETIO_WORKER_MODEL=single' in result.stderr


def test_production_server_command_rejects_malformed_web_concurrency():
    for web_concurrency in ('0', '02', '+2', 'two'):
        result = subprocess.run(
            ['bash', 'scripts/run_production_server.sh', '--print'],
            cwd=os.getcwd(),
            env=_production_env(WEB_CONCURRENCY=web_concurrency),
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 2
        assert 'WEB_CONCURRENCY must be a positive integer.' in result.stderr


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
    assert commands[1].startswith('gunicorn --worker-class gthread --workers 1 --threads 100 ')
    assert commands[1].endswith('aidm_server.wsgi:app')


def test_production_server_command_propagates_render_codex_runtime(tmp_path: Path):
    log_path = tmp_path / 'commands.log'
    node_bin = tmp_path / 'nodes' / 'node-24.18.0' / 'bin'
    node_bin.mkdir(parents=True)
    codex_bin = node_bin / 'codex'
    codex_bin.write_text('#!/usr/bin/env node\n', encoding='utf-8')
    codex_bin.chmod(0o755)
    node_bin.joinpath('node').write_text(
        '#!/usr/bin/env bash\nprintf \'node-ready\\n\' >> "$AIDM_TEST_COMMAND_LOG"\n',
        encoding='utf-8',
    )
    node_bin.joinpath('node').chmod(0o755)

    python_bin = tmp_path / 'python-test'
    gunicorn_bin = tmp_path / 'gunicorn-test'
    python_bin.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "${1:-}" == "-c" && "${2:-}" == *"sys.version_info"* ]]; then exit 0; fi\n'
        'if [[ "${1:-}" == "-c" && "${2:-}" == *"resolve_codex_executable"* ]]; then\n'
        '  printf \'%s\\n\' "$AIDM_TEST_CODEX_EXECUTABLE"\n'
        '  exit 0\n'
        'fi\n'
        'printf \'python %s\\n\' "$*" >> "$AIDM_TEST_COMMAND_LOG"\n'
        'printf \'preflight-codex=%s\\n\' "$AIDM_CODEX_EXECUTABLE" >> "$AIDM_TEST_COMMAND_LOG"\n'
        'printf \'preflight-path=%s\\n\' "$PATH" >> "$AIDM_TEST_COMMAND_LOG"\n',
        encoding='utf-8',
    )
    gunicorn_bin.write_text(
        '#!/usr/bin/env bash\n'
        'printf \'codex=%s\\n\' "$AIDM_CODEX_EXECUTABLE" >> "$AIDM_TEST_COMMAND_LOG"\n'
        'printf \'path=%s\\n\' "$PATH" >> "$AIDM_TEST_COMMAND_LOG"\n'
        '"$AIDM_CODEX_EXECUTABLE" --version\n',
        encoding='utf-8',
    )
    python_bin.chmod(0o755)
    gunicorn_bin.chmod(0o755)
    codex_home = tmp_path / 'aidm-codex-home'
    codex_home.mkdir()
    (codex_home / 'auth.json').write_text('{"auth":"test"}', encoding='utf-8')
    env = _production_env(
        PYTHON_BIN=str(python_bin),
        GUNICORN_BIN=str(gunicorn_bin),
        AIDM_LLM_PROVIDER='codex_cli',
        AIDM_CODEX_EXECUTABLE='codex',
        AIDM_CODEX_HOME=str(codex_home),
        AIDM_TEST_CODEX_EXECUTABLE=str(codex_bin),
        AIDM_TEST_COMMAND_LOG=str(log_path),
        PATH='/usr/bin:/bin',
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
    assert commands[1] == f'preflight-codex={codex_bin}'
    assert commands[2].split('=', 1)[1].split(':', 1)[0] == str(node_bin)
    assert commands[3] == f'codex={codex_bin}'
    assert commands[4].split('=', 1)[1].split(':', 1)[0] == str(node_bin)
    assert commands[5] == 'node-ready'


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
