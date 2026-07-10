from __future__ import annotations

from pathlib import Path

from scripts.check_socketio_worker_model_decision import DecisionPaths, main, validate_decision


def _write_minimal_valid_files(root: Path) -> DecisionPaths:
    docs = root / 'docs'
    scripts = root / 'scripts'
    docs.mkdir()
    scripts.mkdir()
    paths = DecisionPaths(
        decision_doc=docs / 'socketio_worker_model.md',
        env_example=root / '.env.production.example',
        production_server_script=scripts / 'run_production_server.sh',
        production_readiness_doc=docs / 'production-readiness.md',
        beta_runbook=docs / 'beta_runbook.md',
    )
    paths.decision_doc.write_text(
        '\n'.join(
            [
                'Decision: single-worker hosted closed beta.',
                'Production policy: single-worker only.',
                'AIDM_SOCKETIO_WORKER_MODEL=single',
                'AIDM_SOCKETIO_ASYNC_MODE=threading',
                'AIDM_GUNICORN_THREADS=100',
                'WEB_CONCURRENCY=1',
                'scripts/run_production_server.sh --print',
                'shared presence/music state',
                'Both affinity and queueing are required',
            ]
        ),
        encoding='utf-8',
    )
    paths.env_example.write_text(
        '\n'.join(
            [
                'AIDM_SOCKETIO_WORKER_MODEL=single',
                'AIDM_SOCKETIO_ASYNC_MODE=threading',
                'AIDM_GUNICORN_THREADS=100',
                'WEB_CONCURRENCY=1',
                'AIDM_RATE_LIMIT_STORE=database',
                'AIDM_TURN_COORDINATOR_STORE=database',
            ]
        ),
        encoding='utf-8',
    )
    paths.production_server_script.write_text(
        '\n'.join(
            [
                'WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"',
                'export AIDM_SOCKETIO_WORKER_MODEL="${AIDM_SOCKETIO_WORKER_MODEL:-single}"',
                'export AIDM_SOCKETIO_ASYNC_MODE="${AIDM_SOCKETIO_ASYNC_MODE:-threading}"',
                'GUNICORN_THREADS="${AIDM_GUNICORN_THREADS:-100}"',
                '--worker-class gthread',
                '--threads "${GUNICORN_THREADS}"',
                'currently supports only AIDM_SOCKETIO_WORKER_MODEL=single',
                'AIDM_GUNICORN_THREADS must be an integer >= 16.',
                'AIDM_SOCKETIO_WORKER_MODEL=single requires WEB_CONCURRENCY=1.',
            ]
        ),
        encoding='utf-8',
    )
    for path in (paths.production_readiness_doc, paths.beta_runbook):
        path.write_text(
            'AIDM_SOCKETIO_WORKER_MODEL=single\n'
            'AIDM_SOCKETIO_ASYNC_MODE=threading\n'
            'WEB_CONCURRENCY=1\n'
            'scripts/run_production_server.sh\n',
            encoding='utf-8',
        )
    return paths


def test_validate_socketio_worker_model_decision_accepts_current_repo():
    assert validate_decision() == []


def test_validate_socketio_worker_model_decision_reports_mismatched_env_example(tmp_path):
    paths = _write_minimal_valid_files(tmp_path)
    paths.env_example.write_text(
        'AIDM_SOCKETIO_WORKER_MODEL=single\nAIDM_SOCKETIO_ASYNC_MODE=eventlet\n',
        encoding='utf-8',
    )

    errors = validate_decision(paths)

    assert any('.env.production.example must set `AIDM_SOCKETIO_ASYNC_MODE=threading`' in error for error in errors)
    assert any('.env.production.example must set `AIDM_RATE_LIMIT_STORE=database`' in error for error in errors)


def test_validate_socketio_worker_model_decision_ignores_commented_env_assignments(tmp_path):
    paths = _write_minimal_valid_files(tmp_path)
    paths.env_example.write_text(
        '# AIDM_SOCKETIO_ASYNC_MODE=threading\n'
        'AIDM_SOCKETIO_ASYNC_MODE=eventlet\n'
        'AIDM_SOCKETIO_WORKER_MODEL=single\n'
        'AIDM_GUNICORN_THREADS=100\n'
        'WEB_CONCURRENCY=1\n'
        'AIDM_RATE_LIMIT_STORE=database\n'
        'AIDM_TURN_COORDINATOR_STORE=database\n',
        encoding='utf-8',
    )

    errors = validate_decision(paths)

    assert any('must set `AIDM_SOCKETIO_ASYNC_MODE=threading`' in error for error in errors)


def test_socketio_worker_model_decision_main_passes_for_current_repo(capsys):
    assert main() == 0
    assert 'Decision verified' in capsys.readouterr().out
