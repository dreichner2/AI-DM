from __future__ import annotations

import os

import pytest

from aidm_server.env_loader import load_runtime_env


def test_load_runtime_env_prefers_env_local(tmp_path, monkeypatch):
    root = tmp_path
    (root / '.env').write_text('AIDM_LLM_PROVIDER=gemini\nAIDM_LLM_MODEL=models/gemini-3-flash-preview\n', encoding='utf-8')
    (root / '.env.local').write_text(
        'AIDM_LLM_PROVIDER=nvidia\nAIDM_LLM_MODEL=moonshotai/kimi-k2.5\nAIDM_LLM_FALLBACK_MODELS=\n',
        encoding='utf-8',
    )

    monkeypatch.delenv('AIDM_LLM_PROVIDER', raising=False)
    monkeypatch.delenv('AIDM_LLM_MODEL', raising=False)
    monkeypatch.delenv('AIDM_LLM_FALLBACK_MODELS', raising=False)
    monkeypatch.delenv('AIDM_ENV', raising=False)
    monkeypatch.delenv('AIDM_ENV_FILE', raising=False)
    monkeypatch.delenv('AIDM_SKIP_REPO_ENV_LOCAL', raising=False)

    load_runtime_env(root)

    assert os.getenv('AIDM_LLM_PROVIDER') == 'nvidia'
    assert os.getenv('AIDM_LLM_MODEL') == 'moonshotai/kimi-k2.5'
    assert os.getenv('AIDM_LLM_FALLBACK_MODELS') == ''


def test_load_runtime_env_does_not_override_production_with_repo_env_local(tmp_path, monkeypatch):
    root = tmp_path
    (root / '.env').write_text('AIDM_LLM_PROVIDER=gemini\n', encoding='utf-8')
    (root / '.env.local').write_text(
        'AIDM_ENV=development\n'
        'AIDM_AUTH_REQUIRED=false\n'
        'AIDM_RATE_LIMIT_STORE=memory\n'
        'AIDM_LLM_PROVIDER=fallback\n',
        encoding='utf-8',
    )
    monkeypatch.setenv('AIDM_ENV', 'production')
    monkeypatch.setenv('AIDM_AUTH_REQUIRED', 'true')
    monkeypatch.setenv('AIDM_RATE_LIMIT_STORE', 'database')
    monkeypatch.setenv('AIDM_LLM_PROVIDER', 'gemini')
    monkeypatch.delenv('AIDM_ENV_FILE', raising=False)
    monkeypatch.delenv('AIDM_SKIP_REPO_ENV_LOCAL', raising=False)

    load_runtime_env(root)

    assert os.environ['AIDM_ENV'] == 'production'
    assert os.environ['AIDM_AUTH_REQUIRED'] == 'true'
    assert os.environ['AIDM_RATE_LIMIT_STORE'] == 'database'
    assert os.environ['AIDM_LLM_PROVIDER'] == 'gemini'


def test_load_runtime_env_rejects_explicit_file_that_downgrades_production(tmp_path, monkeypatch):
    env_file = tmp_path / 'production.env'
    env_file.write_text('AIDM_ENV=development\nAIDM_AUTH_REQUIRED=false\n', encoding='utf-8')
    monkeypatch.setenv('AIDM_ENV', 'production')
    monkeypatch.setenv('AIDM_AUTH_REQUIRED', 'true')
    monkeypatch.setenv('AIDM_ENV_FILE', str(env_file))

    with pytest.raises(RuntimeError, match='cannot override a production process'):
        load_runtime_env(tmp_path)

    assert os.environ['AIDM_ENV'] == 'production'
    assert os.environ['AIDM_AUTH_REQUIRED'] == 'true'


@pytest.mark.parametrize('environment_line', ['AIDM_ENV=\n', 'AIDM_ENV\n'])
def test_load_runtime_env_rejects_explicit_file_that_clears_production(
    tmp_path,
    monkeypatch,
    environment_line,
):
    env_file = tmp_path / 'production.env'
    env_file.write_text(environment_line, encoding='utf-8')
    monkeypatch.setenv('AIDM_ENV', 'production')
    monkeypatch.setenv('AIDM_ENV_FILE', str(env_file))

    with pytest.raises(RuntimeError, match='cannot override a production process'):
        load_runtime_env(tmp_path)

    assert os.environ['AIDM_ENV'] == 'production'
