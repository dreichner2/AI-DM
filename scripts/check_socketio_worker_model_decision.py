#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DecisionPaths:
    decision_doc: Path = REPO_ROOT / 'docs' / 'socketio_worker_model.md'
    env_example: Path = REPO_ROOT / '.env.production.example'
    production_server_script: Path = REPO_ROOT / 'scripts' / 'run_production_server.sh'
    production_readiness_doc: Path = REPO_ROOT / 'docs' / 'production-readiness.md'
    beta_runbook: Path = REPO_ROOT / 'docs' / 'beta_runbook.md'


def _read(path: Path, errors: list[str]) -> str:
    if not path.exists():
        errors.append(f'{path} is missing.')
        return ''
    return path.read_text(encoding='utf-8')


def _require(text: str, needle: str, label: str, errors: list[str]) -> None:
    if needle not in text:
        errors.append(f'{label} must include `{needle}`.')


def _active_env_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export ') :].strip()
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _require_env_value(values: dict[str, str], key: str, expected: str, label: str, errors: list[str]) -> None:
    actual = values.get(key)
    if actual != expected:
        errors.append(f'{label} must set `{key}={expected}` (found {actual!r}).')


def validate_decision(paths: DecisionPaths = DecisionPaths()) -> list[str]:
    errors: list[str] = []
    decision = _read(paths.decision_doc, errors)
    env_example = _read(paths.env_example, errors)
    production_server = _read(paths.production_server_script, errors)
    production_readiness = _read(paths.production_readiness_doc, errors)
    beta_runbook = _read(paths.beta_runbook, errors)
    env_values = _active_env_values(env_example)

    _require(decision, 'Decision: single-worker hosted closed beta.', str(paths.decision_doc), errors)
    _require(decision, 'Production policy: single-worker only.', str(paths.decision_doc), errors)
    _require(decision, 'AIDM_SOCKETIO_WORKER_MODEL=single', str(paths.decision_doc), errors)
    _require(decision, 'AIDM_SOCKETIO_ASYNC_MODE=threading', str(paths.decision_doc), errors)
    _require(decision, 'AIDM_GUNICORN_THREADS=100', str(paths.decision_doc), errors)
    _require(decision, 'WEB_CONCURRENCY=1', str(paths.decision_doc), errors)
    _require(decision, 'scripts/run_production_server.sh --print', str(paths.decision_doc), errors)
    _require(decision, 'shared presence/music state', str(paths.decision_doc), errors)
    _require(decision, 'Both affinity and queueing are required', str(paths.decision_doc), errors)

    _require_env_value(env_values, 'AIDM_SOCKETIO_WORKER_MODEL', 'single', str(paths.env_example), errors)
    _require_env_value(env_values, 'AIDM_SOCKETIO_ASYNC_MODE', 'threading', str(paths.env_example), errors)
    _require_env_value(env_values, 'AIDM_GUNICORN_THREADS', '100', str(paths.env_example), errors)
    _require_env_value(env_values, 'WEB_CONCURRENCY', '1', str(paths.env_example), errors)
    _require_env_value(env_values, 'AIDM_RATE_LIMIT_STORE', 'database', str(paths.env_example), errors)
    _require_env_value(env_values, 'AIDM_TURN_COORDINATOR_STORE', 'database', str(paths.env_example), errors)

    _require(
        production_server,
        'export AIDM_SOCKETIO_WORKER_MODEL="${AIDM_SOCKETIO_WORKER_MODEL:-single}"',
        str(paths.production_server_script),
        errors,
    )
    _require(
        production_server,
        'export AIDM_SOCKETIO_ASYNC_MODE="${AIDM_SOCKETIO_ASYNC_MODE:-threading}"',
        str(paths.production_server_script),
        errors,
    )
    _require(production_server, 'WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"', str(paths.production_server_script), errors)
    _require(production_server, 'GUNICORN_THREADS="${AIDM_GUNICORN_THREADS:-100}"', str(paths.production_server_script), errors)
    _require(production_server, '--worker-class gthread', str(paths.production_server_script), errors)
    _require(production_server, '--threads "${GUNICORN_THREADS}"', str(paths.production_server_script), errors)
    _require(
        production_server,
        'currently supports only AIDM_SOCKETIO_WORKER_MODEL=single',
        str(paths.production_server_script),
        errors,
    )
    _require(
        production_server,
        'AIDM_GUNICORN_THREADS must be an integer >= 16.',
        str(paths.production_server_script),
        errors,
    )
    _require(
        production_server,
        'AIDM_SOCKETIO_WORKER_MODEL=single requires WEB_CONCURRENCY=1.',
        str(paths.production_server_script),
        errors,
    )

    for path, text in (
        (paths.production_readiness_doc, production_readiness),
        (paths.beta_runbook, beta_runbook),
    ):
        _require(text, 'AIDM_SOCKETIO_WORKER_MODEL=single', str(path), errors)
        _require(text, 'AIDM_SOCKETIO_ASYNC_MODE=threading', str(path), errors)
        _require(text, 'WEB_CONCURRENCY=1', str(path), errors)
        _require(text, 'scripts/run_production_server.sh', str(path), errors)

    return errors


def main() -> int:
    errors = validate_decision()
    if errors:
        print('[socketio-worker-model][error] Decision check failed:', file=sys.stderr)
        for error in errors:
            print(f'- {error}', file=sys.stderr)
        return 1
    print('[socketio-worker-model] Decision verified: single worker, threading/gthread, WEB_CONCURRENCY=1.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
