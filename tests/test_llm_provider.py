from __future__ import annotations

import json

import pytest

from aidm_server.contracts import ProviderRequest
from aidm_server.llm import (
    DeepSeekChatProvider,
    GeminiProvider,
    NvidiaChatProvider,
    ProviderNotConfiguredError,
    ProviderResponse,
    _chunk_text_for_stream,
    estimate_text_tokens,
    get_provider,
    query_dm_function_stream,
)
from aidm_server.llm_providers import CodexCliProvider, get_helper_provider
from aidm_server.provider_registry import provider_capabilities, provider_default_model, provider_runtime_model


def _clear_helper_env(monkeypatch):
    for key in (
        'AIDM_HELPER_LLM_PROVIDER',
        'AIDM_HELPER_LLM_MODEL',
        'AIDM_HELPER_LLM_FALLBACK_MODELS',
        'AIDM_HELPER_LLM_MAX_TOKENS',
        'AIDM_HELPER_LLM_TEMPERATURE',
        'AIDM_HELPER_LLM_TOP_P',
        'AIDM_HELPER_PROFILE_DEFAULT',
        'AIDM_HELPER_DEEPSEEK_TIMEOUT_SECONDS',
        'AIDM_HELPER_DEEPSEEK_THINKING',
        'AIDM_HELPER_DEEPSEEK_REASONING_EFFORT',
        'AIDM_CODEX_EXECUTABLE',
        'AIDM_CODEX_WORKDIR',
        'AIDM_CODEX_TIMEOUT_SECONDS',
        'AIDM_CODEX_REASONING_EFFORT',
        'AIDM_CODEX_IGNORE_RULES',
        'AIDM_CODEX_ACCESS_TOKEN',
        'AIDM_CUSTOM_RACE_HELPER_LLM_PROVIDER',
        'AIDM_CUSTOM_RACE_HELPER_LLM_MODEL',
        'AIDM_CUSTOM_RACE_HELPER_LLM_FALLBACK_MODELS',
        'AIDM_CUSTOM_RACE_HELPER_LLM_MAX_TOKENS',
        'AIDM_CUSTOM_RACE_HELPER_LLM_TEMPERATURE',
        'AIDM_CUSTOM_RACE_HELPER_LLM_TOP_P',
        'AIDM_CUSTOM_RACE_HELPER_PROFILE',
        'AIDM_CUSTOM_RACE_HELPER_DEEPSEEK_TIMEOUT_SECONDS',
        'AIDM_CUSTOM_RACE_HELPER_DEEPSEEK_CONNECT_TIMEOUT_SECONDS',
        'AIDM_CUSTOM_RACE_HELPER_DEEPSEEK_READ_TIMEOUT_SECONDS',
        'AIDM_CUSTOM_RACE_HELPER_DEEPSEEK_THINKING',
        'AIDM_CUSTOM_RACE_HELPER_DEEPSEEK_REASONING_EFFORT',
        'AIDM_HELPER_PROFILE_CUSTOM_RACE',
        'AIDM_SENTIENT_ENEMY_BRAIN_HELPER_LLM_PROVIDER',
        'AIDM_SENTIENT_ENEMY_BRAIN_HELPER_LLM_MODEL',
        'AIDM_SENTIENT_ENEMY_BRAIN_HELPER_LLM_FALLBACK_MODELS',
        'AIDM_SENTIENT_ENEMY_BRAIN_HELPER_LLM_MAX_TOKENS',
        'AIDM_SENTIENT_ENEMY_BRAIN_HELPER_LLM_TEMPERATURE',
        'AIDM_SENTIENT_ENEMY_BRAIN_HELPER_LLM_TOP_P',
        'AIDM_SENTIENT_ENEMY_BRAIN_HELPER_PROFILE',
        'AIDM_SENTIENT_ENEMY_BRAIN_HELPER_DEEPSEEK_TIMEOUT_SECONDS',
        'AIDM_SENTIENT_ENEMY_BRAIN_HELPER_DEEPSEEK_CONNECT_TIMEOUT_SECONDS',
        'AIDM_SENTIENT_ENEMY_BRAIN_HELPER_DEEPSEEK_READ_TIMEOUT_SECONDS',
        'AIDM_SENTIENT_ENEMY_BRAIN_HELPER_DEEPSEEK_THINKING',
        'AIDM_SENTIENT_ENEMY_BRAIN_HELPER_DEEPSEEK_REASONING_EFFORT',
        'AIDM_HELPER_PROFILE_SENTIENT_ENEMY_BRAIN',
        'AIDM_BOSS_TACTICS_HELPER_LLM_PROVIDER',
        'AIDM_BOSS_TACTICS_HELPER_LLM_MODEL',
        'AIDM_BOSS_TACTICS_HELPER_LLM_FALLBACK_MODELS',
        'AIDM_BOSS_TACTICS_HELPER_LLM_MAX_TOKENS',
        'AIDM_BOSS_TACTICS_HELPER_LLM_TEMPERATURE',
        'AIDM_BOSS_TACTICS_HELPER_LLM_TOP_P',
        'AIDM_BOSS_TACTICS_HELPER_PROFILE',
        'AIDM_BOSS_TACTICS_HELPER_DEEPSEEK_TIMEOUT_SECONDS',
        'AIDM_BOSS_TACTICS_HELPER_DEEPSEEK_CONNECT_TIMEOUT_SECONDS',
        'AIDM_BOSS_TACTICS_HELPER_DEEPSEEK_READ_TIMEOUT_SECONDS',
        'AIDM_BOSS_TACTICS_HELPER_DEEPSEEK_THINKING',
        'AIDM_BOSS_TACTICS_HELPER_DEEPSEEK_REASONING_EFFORT',
        'AIDM_HELPER_PROFILE_BOSS_TACTICS',
        'AIDM_BOSS_TACTICS_PLANNER_HELPER_LLM_PROVIDER',
        'AIDM_BOSS_TACTICS_PLANNER_HELPER_LLM_MODEL',
        'AIDM_BOSS_TACTICS_PLANNER_HELPER_LLM_FALLBACK_MODELS',
        'AIDM_BOSS_TACTICS_PLANNER_HELPER_LLM_MAX_TOKENS',
        'AIDM_BOSS_TACTICS_PLANNER_HELPER_LLM_TEMPERATURE',
        'AIDM_BOSS_TACTICS_PLANNER_HELPER_LLM_TOP_P',
        'AIDM_BOSS_TACTICS_PLANNER_HELPER_PROFILE',
        'AIDM_BOSS_TACTICS_PLANNER_HELPER_DEEPSEEK_TIMEOUT_SECONDS',
        'AIDM_BOSS_TACTICS_PLANNER_HELPER_DEEPSEEK_CONNECT_TIMEOUT_SECONDS',
        'AIDM_BOSS_TACTICS_PLANNER_HELPER_DEEPSEEK_READ_TIMEOUT_SECONDS',
        'AIDM_BOSS_TACTICS_PLANNER_HELPER_DEEPSEEK_THINKING',
        'AIDM_BOSS_TACTICS_PLANNER_HELPER_DEEPSEEK_REASONING_EFFORT',
        'AIDM_HELPER_PROFILE_BOSS_TACTICS_PLANNER',
        'AIDM_CREATURE_HELPER_LLM_PROVIDER',
        'AIDM_CREATURE_HELPER_LLM_MODEL',
        'AIDM_CREATURE_HELPER_LLM_FALLBACK_MODELS',
        'AIDM_CREATURE_HELPER_LLM_MAX_TOKENS',
        'AIDM_CREATURE_HELPER_LLM_TEMPERATURE',
        'AIDM_CREATURE_HELPER_LLM_TOP_P',
        'AIDM_CREATURE_HELPER_PROFILE',
        'AIDM_CREATURE_HELPER_DEEPSEEK_TIMEOUT_SECONDS',
        'AIDM_CREATURE_HELPER_DEEPSEEK_CONNECT_TIMEOUT_SECONDS',
        'AIDM_CREATURE_HELPER_DEEPSEEK_READ_TIMEOUT_SECONDS',
        'AIDM_CREATURE_HELPER_DEEPSEEK_THINKING',
        'AIDM_CREATURE_HELPER_DEEPSEEK_REASONING_EFFORT',
        'AIDM_HELPER_PROFILE_CREATURE_GENERATION',
    ):
        monkeypatch.delenv(key, raising=False)


def test_provider_registry_defines_defaults_and_capabilities():
    assert provider_default_model('deepseek') == 'deepseek-v4-pro'
    assert provider_default_model('nvidia') == 'moonshotai/kimi-k2.5'
    assert provider_default_model('codex_cli') == 'gpt-5.5-medium'
    assert provider_runtime_model('codex_cli', 'gpt-5.5-xhigh') == 'gpt-5.5'
    deepseek_capabilities = provider_capabilities('deepseek')
    nvidia_capabilities = provider_capabilities('nvidia')
    codex_capabilities = provider_capabilities('codex_cli')
    assert deepseek_capabilities['openai_compatible'] is True
    assert deepseek_capabilities['thinking_control'] is True
    assert nvidia_capabilities['thinking_control'] is True
    assert codex_capabilities['streaming'] is True


def test_get_provider_reads_fallback_models_from_env(monkeypatch):
    monkeypatch.setenv('AIDM_LLM_PROVIDER', 'gemini')
    monkeypatch.setenv('AIDM_LLM_MODEL', 'models/gemini-3-flash-preview')
    monkeypatch.setenv('AIDM_LLM_FALLBACK_MODELS', 'models/gemini-2.5-flash, models/gemini-flash-lite-latest')

    provider = get_provider()

    assert isinstance(provider, GeminiProvider)
    assert provider.model_name == 'models/gemini-3-flash-preview'
    assert provider.fallback_models == ['models/gemini-2.5-flash', 'models/gemini-flash-lite-latest']


def test_gemini_provider_generate_uses_fallback_model_when_primary_fails(monkeypatch):
    provider = GeminiProvider(
        model_name='models/gemini-3-flash-preview',
        api_key='fake-key',
        fallback_models=['models/gemini-2.5-flash'],
    )
    attempts: list[str] = []

    def fake_generate(model_name: str, full_prompt: str):
        attempts.append(model_name)
        if model_name == 'models/gemini-3-flash-preview':
            raise RuntimeError('model unavailable')
        return 'Fallback model response'

    monkeypatch.setattr(provider, '_generate_with_model', fake_generate)

    response = provider.generate(ProviderRequest(prompt='hello'))

    assert attempts == ['models/gemini-3-flash-preview', 'models/gemini-2.5-flash']
    assert response.text == 'Fallback model response'
    assert response.model == 'models/gemini-2.5-flash'


def test_gemini_provider_stream_uses_fallback_when_primary_fails_before_output(monkeypatch):
    provider = GeminiProvider(
        model_name='models/gemini-3-flash-preview',
        api_key='fake-key',
        fallback_models=['models/gemini-2.5-flash'],
    )
    attempts: list[str] = []

    def fake_stream(model_name: str, full_prompt: str):
        attempts.append(model_name)
        if model_name == 'models/gemini-3-flash-preview':
            raise RuntimeError('model unavailable')
        yield 'fallback chunk'

    monkeypatch.setattr(provider, '_stream_with_model', fake_stream)

    chunks = list(provider.stream(ProviderRequest(prompt='hello')))

    assert attempts == ['models/gemini-3-flash-preview', 'models/gemini-2.5-flash']
    assert chunks == ['fallback chunk']


def test_gemini_provider_stream_does_not_mix_models_after_partial_output(monkeypatch):
    provider = GeminiProvider(
        model_name='models/gemini-3-flash-preview',
        api_key='fake-key',
        fallback_models=['models/gemini-2.5-flash'],
    )
    attempts: list[str] = []

    def fake_stream(model_name: str, full_prompt: str):
        attempts.append(model_name)
        if model_name == 'models/gemini-3-flash-preview':
            yield 'partial chunk'
            raise RuntimeError('stream interrupted')
        yield 'fallback chunk'

    monkeypatch.setattr(provider, '_stream_with_model', fake_stream)

    stream_iter = provider.stream(ProviderRequest(prompt='hello'))
    assert next(stream_iter) == 'partial chunk'
    with pytest.raises(RuntimeError):
        next(stream_iter)

    assert attempts == ['models/gemini-3-flash-preview']


def test_extract_text_preserves_stream_whitespace():
    class _Chunk:
        text = ' leading-space'

    text = GeminiProvider._extract_text(_Chunk(), preserve_whitespace=True)
    assert text == ' leading-space'


def test_gemini_provider_skips_primary_when_rate_limited_cooldown_active(monkeypatch):
    import aidm_server.llm_providers as provider_module

    monkeypatch.setattr(provider_module, 'LLM_RATE_LIMIT_THRESHOLD', 1)
    monkeypatch.setattr(provider_module, 'LLM_RATE_LIMIT_COOLDOWN_SECONDS', 120)
    GeminiProvider._rate_limit_state.clear()

    provider = GeminiProvider(
        model_name='models/gemini-3-flash-preview',
        api_key='fake-key',
        fallback_models=['models/gemini-2.5-flash'],
    )
    attempts: list[str] = []

    def fake_generate(model_name: str, full_prompt: str):
        attempts.append(model_name)
        if model_name == 'models/gemini-3-flash-preview':
            raise RuntimeError('429 Too Many Requests')
        return 'Fallback works'

    monkeypatch.setattr(provider, '_generate_with_model', fake_generate)

    first = provider.generate(ProviderRequest(prompt='hello one'))
    second = provider.generate(ProviderRequest(prompt='hello two'))

    assert first.model == 'models/gemini-2.5-flash'
    assert second.model == 'models/gemini-2.5-flash'
    assert attempts == [
        'models/gemini-3-flash-preview',
        'models/gemini-2.5-flash',
        'models/gemini-2.5-flash',
    ]


def test_get_provider_supports_nvidia(monkeypatch):
    monkeypatch.setenv('AIDM_LLM_PROVIDER', 'nvidia')
    monkeypatch.setenv('AIDM_LLM_MODEL', 'moonshotai/kimi-k2.5')
    monkeypatch.setenv('AIDM_NVIDIA_API_KEY', 'nvapi-test')
    monkeypatch.setenv('AIDM_NVIDIA_INVOKE_URL', 'https://integrate.api.nvidia.com/v1/chat/completions')

    provider = get_provider()

    assert isinstance(provider, NvidiaChatProvider)
    assert provider.model_name == 'moonshotai/kimi-k2.5'


def test_get_provider_supports_codex_cli_medium(monkeypatch):
    monkeypatch.setenv('AIDM_LLM_PROVIDER', 'codex_cli')
    monkeypatch.setenv('AIDM_LLM_MODEL', 'gpt-5.5-medium')
    monkeypatch.setenv('AIDM_CODEX_EXECUTABLE', '/usr/local/bin/codex')
    monkeypatch.delenv('AIDM_CODEX_REASONING_EFFORT', raising=False)
    monkeypatch.delenv('AIDM_CODEX_TIMEOUT_SECONDS', raising=False)

    provider = get_provider()

    assert isinstance(provider, CodexCliProvider)
    assert provider.model_name == 'gpt-5.5'
    assert provider.display_model_name == 'gpt-5.5-medium'
    assert provider.reasoning_effort == 'medium'
    assert provider.timeout_seconds == 240
    assert provider.prompt_role == 'dm'


def test_get_provider_supports_codex_cli_xhigh(monkeypatch):
    monkeypatch.setenv('AIDM_LLM_PROVIDER', 'codex_cli')
    monkeypatch.setenv('AIDM_LLM_MODEL', 'gpt-5.5-xhigh')
    monkeypatch.setenv('AIDM_CODEX_EXECUTABLE', '/usr/local/bin/codex')
    monkeypatch.setenv('AIDM_CODEX_REASONING_EFFORT', 'medium')

    provider = get_provider()

    assert isinstance(provider, CodexCliProvider)
    assert provider.model_name == 'gpt-5.5'
    assert provider.display_model_name == 'gpt-5.5-xhigh'
    assert provider.reasoning_effort == 'xhigh'


def test_get_provider_keeps_legacy_codex_model_as_medium(monkeypatch):
    monkeypatch.setenv('AIDM_LLM_PROVIDER', 'codex_cli')
    monkeypatch.setenv('AIDM_LLM_MODEL', 'gpt-5.5')
    monkeypatch.setenv('AIDM_CODEX_EXECUTABLE', '/usr/local/bin/codex')

    provider = get_provider()

    assert isinstance(provider, CodexCliProvider)
    assert provider.model_name == 'gpt-5.5'
    assert provider.display_model_name == 'gpt-5.5-medium'
    assert provider.reasoning_effort == 'medium'


def test_get_provider_does_not_reuse_nvidia_key_for_official_deepseek(monkeypatch):
    monkeypatch.setenv('AIDM_LLM_PROVIDER', 'deepseek')
    monkeypatch.setenv('AIDM_LLM_MODEL', 'deepseek-v4-pro')
    monkeypatch.setenv('AIDM_NVIDIA_API_KEY', 'nvapi-test')
    monkeypatch.delenv('AIDM_DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)

    provider = get_provider()

    assert isinstance(provider, DeepSeekChatProvider)
    assert provider.api_key is None


def test_get_provider_uses_nvidia_key_for_deepseek_model_via_nvidia(monkeypatch):
    monkeypatch.setenv('AIDM_LLM_PROVIDER', 'nvidia')
    monkeypatch.setenv('AIDM_LLM_MODEL', 'deepseek-v4-pro')
    monkeypatch.setenv('AIDM_NVIDIA_API_KEY', 'nvapi-test')
    monkeypatch.setenv('AIDM_NVIDIA_INVOKE_URL', 'https://integrate.api.nvidia.com/v1')

    provider = get_provider()

    assert isinstance(provider, NvidiaChatProvider)
    assert provider.model_name == 'deepseek-v4-pro'
    assert provider.api_key == 'nvapi-test'


def test_get_provider_rejects_unknown_provider(monkeypatch):
    monkeypatch.setenv('AIDM_LLM_PROVIDER', 'typo-provider')

    with pytest.raises(ProviderNotConfiguredError):
        get_provider()


def test_get_helper_provider_defaults_to_fast_state_helper(monkeypatch):
    _clear_helper_env(monkeypatch)
    monkeypatch.setenv('AIDM_DEEPSEEK_API_KEY', 'deepseek-test')

    provider = get_helper_provider()

    assert isinstance(provider, DeepSeekChatProvider)
    assert provider.api_key == 'deepseek-test'
    assert provider.model_name == 'deepseek-v4-flash'
    assert provider.max_tokens == 2048
    assert provider.temperature == 0.1
    assert provider.top_p == 0.9
    assert provider.thinking_enabled is False
    assert provider.reasoning_effort == 'low'
    assert provider.read_timeout_seconds == 30.0


def _assert_codex_medium_helper(provider):
    assert isinstance(provider, CodexCliProvider)
    assert provider.model_name == 'gpt-5.5'
    assert provider.timeout_seconds == 240
    assert provider.reasoning_effort == 'medium'
    assert provider.ignore_rules is True


def test_get_helper_provider_uses_codex_medium_for_custom_races(monkeypatch):
    _clear_helper_env(monkeypatch)

    provider = get_helper_provider(task='custom_race')

    _assert_codex_medium_helper(provider)


def test_get_helper_provider_uses_codex_medium_for_sentient_enemy_brain(monkeypatch):
    _clear_helper_env(monkeypatch)

    provider = get_helper_provider(task='sentient_enemy_brain')

    _assert_codex_medium_helper(provider)


def test_get_helper_provider_uses_codex_medium_for_boss_tactics(monkeypatch):
    _clear_helper_env(monkeypatch)

    provider = get_helper_provider(task='boss_tactics')

    _assert_codex_medium_helper(provider)


def test_get_helper_provider_uses_codex_medium_for_boss_tactics_planner(monkeypatch):
    _clear_helper_env(monkeypatch)

    provider = get_helper_provider(task='boss_tactics_planner')

    _assert_codex_medium_helper(provider)


def test_get_helper_provider_can_route_task_back_to_deepseek_pro_profile(monkeypatch):
    _clear_helper_env(monkeypatch)
    monkeypatch.setenv('AIDM_HELPER_PROFILE_BOSS_TACTICS', 'deepseek_pro')
    monkeypatch.setenv('AIDM_DEEPSEEK_API_KEY', 'deepseek-test')

    provider = get_helper_provider(task='boss_tactics')

    assert isinstance(provider, DeepSeekChatProvider)
    assert provider.api_key == 'deepseek-test'
    assert provider.model_name == 'deepseek-v4-pro'
    assert provider.max_tokens == 3072
    assert provider.temperature == 0.55
    assert provider.reasoning_effort == 'medium'


def test_get_helper_provider_uses_codex_medium_for_creature_generation(monkeypatch):
    _clear_helper_env(monkeypatch)

    provider = get_helper_provider(task='creature_generation')

    _assert_codex_medium_helper(provider)


def test_get_helper_provider_can_route_creature_generation_to_fast_profile(monkeypatch):
    _clear_helper_env(monkeypatch)
    monkeypatch.setenv('AIDM_HELPER_PROFILE_CREATURE_GENERATION', 'fast')
    monkeypatch.setenv('AIDM_DEEPSEEK_API_KEY', 'deepseek-test')

    provider = get_helper_provider(task='creature_generation')

    assert isinstance(provider, DeepSeekChatProvider)
    assert provider.api_key == 'deepseek-test'
    assert provider.model_name == 'deepseek-v4-flash'
    assert provider.max_tokens == 2048
    assert provider.temperature == 0.1
    assert provider.top_p == 0.9
    assert provider.thinking_enabled is False
    assert provider.reasoning_effort == 'low'
    assert provider.read_timeout_seconds == 30.0


def test_get_helper_provider_routes_task_profile_to_codex(monkeypatch):
    _clear_helper_env(monkeypatch)
    monkeypatch.setenv('AIDM_HELPER_PROFILE_SENTIENT_ENEMY_BRAIN', 'codex_medium')
    monkeypatch.setenv('AIDM_CODEX_EXECUTABLE', '/usr/local/bin/codex')
    monkeypatch.setenv('AIDM_CODEX_WORKDIR', '/tmp/aidm-codex-workdir')

    provider = get_helper_provider(task='sentient_enemy_brain')

    assert isinstance(provider, CodexCliProvider)
    assert provider.model_name == 'gpt-5.5'
    assert provider.executable == '/usr/local/bin/codex'
    assert provider.workdir == '/tmp/aidm-codex-workdir'
    assert provider.timeout_seconds == 240
    assert provider.reasoning_effort == 'medium'


def test_task_specific_provider_override_beats_profile(monkeypatch):
    _clear_helper_env(monkeypatch)
    monkeypatch.setenv('AIDM_HELPER_PROFILE_BOSS_TACTICS', 'codex')
    monkeypatch.setenv('AIDM_BOSS_TACTICS_HELPER_LLM_PROVIDER', 'deepseek')
    monkeypatch.setenv('AIDM_BOSS_TACTICS_HELPER_LLM_MODEL', 'deepseek-v4-pro')
    monkeypatch.setenv('AIDM_DEEPSEEK_API_KEY', 'deepseek-test')

    provider = get_helper_provider(task='boss_tactics')

    assert isinstance(provider, DeepSeekChatProvider)
    assert provider.model_name == 'deepseek-v4-pro'


def test_codex_cli_provider_generate_uses_readonly_exec_and_output_file(monkeypatch, tmp_path):
    import aidm_server.llm_providers as provider_module

    calls = []

    def fake_which(executable):
        assert executable == 'codex'
        return '/usr/local/bin/codex'

    def fake_run(command, input, capture_output, text, timeout, cwd, env, check):
        del capture_output, text, env, check
        calls.append(
            {
                'command': command,
                'input': input,
                'timeout': timeout,
                'cwd': cwd,
            }
        )
        output_path = command[command.index('-o') + 1]
        with open(output_path, 'w', encoding='utf-8') as handle:
            handle.write('{"selected_candidate_id":"candidate_2","confidence":0.8}')
        return type('Completed', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

    monkeypatch.setattr(provider_module.shutil, 'which', fake_which)
    monkeypatch.setattr(provider_module.subprocess, 'run', fake_run)

    provider = CodexCliProvider(
        model_name='gpt-5.5',
        executable='codex',
        workdir=str(tmp_path),
        timeout_seconds=12,
        reasoning_effort='low',
    )
    response = provider.generate(ProviderRequest(prompt='Return selector JSON.', system_message='Return JSON only.'))

    assert response.provider == 'codex_cli'
    assert response.model == 'gpt-5.5'
    assert response.text == '{"selected_candidate_id":"candidate_2","confidence":0.8}'
    assert len(calls) == 1
    command = calls[0]['command']
    assert command[:2] == ['/usr/local/bin/codex', 'exec']
    assert '--ephemeral' in command
    assert '--ignore-rules' in command
    assert command[command.index('--sandbox') + 1] == 'read-only'
    assert command[command.index('--model') + 1] == 'gpt-5.5'
    assert 'model_reasoning_effort="low"' in command
    assert command[-1] == '-'
    assert calls[0]['timeout'] == 12
    assert calls[0]['cwd'] == str(tmp_path)
    assert 'SYSTEM CONTRACT:\nReturn JSON only.' in calls[0]['input']
    assert 'TASK INPUT:\nReturn selector JSON.' in calls[0]['input']


def test_codex_cli_provider_stream_reads_app_server_deltas(monkeypatch, tmp_path):
    import aidm_server.llm_providers as provider_module

    calls = []
    fake_processes = []

    class FakeUuid:
        def __init__(self, value):
            self.hex = value

    class FakeStdin:
        def __init__(self):
            self.values = []

        def write(self, value):
            self.values.append(value)

        def close(self):
            pass

        def flush(self):
            pass

    class FakeProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = iter(
                [
                    '{"id":"init_id","result":{"userAgent":"test"}}\n',
                    '{"id":"thread_id","result":{"thread":{"id":"thread_test"}}}\n',
                    '{"id":"turn_id","result":{"turn":{"id":"turn_test"}}}\n',
                    '{"method":"item/agentMessage/delta","params":{"threadId":"thread_test","turnId":"turn_test","itemId":"msg_test","delta":"Streamed "}}\n',
                    '{"method":"item/agentMessage/delta","params":{"threadId":"thread_test","turnId":"turn_test","itemId":"msg_test","delta":"final."}}\n',
                    '{"method":"item/completed","params":{"item":{"id":"msg_test","type":"agentMessage","text":"Streamed final."},"threadId":"thread_test","turnId":"turn_test"}}\n',
                    '{"method":"turn/completed","params":{"threadId":"thread_test","turn":{"id":"turn_test","status":"completed"}}}\n',
                ]
            )
            self.stderr = iter([])
            self.killed = False

        def wait(self, timeout=None):
            del timeout
            return 0

        def poll(self):
            return 0

        def kill(self):
            self.killed = True

    def fake_which(executable):
        assert executable == 'codex'
        return '/usr/local/bin/codex'

    def fake_popen(command, stdin, stdout, stderr, text, cwd, env):
        del stdin, stdout, stderr, text, env
        process = FakeProcess()
        calls.append({'command': command, 'cwd': cwd, 'process': process})
        fake_processes.append(process)
        return process

    monkeypatch.setattr(provider_module.shutil, 'which', fake_which)
    monkeypatch.setattr(provider_module.subprocess, 'Popen', fake_popen)
    fake_ids = iter([FakeUuid('init_id'), FakeUuid('thread_id'), FakeUuid('turn_id')])
    monkeypatch.setattr(provider_module, 'uuid4', lambda: next(fake_ids))

    provider = CodexCliProvider(
        model_name='gpt-5.5',
        executable='codex',
        workdir=str(tmp_path),
        timeout_seconds=12,
        reasoning_effort='medium',
    )

    chunks = list(provider.stream(ProviderRequest(prompt='Return a short answer.')))

    assert chunks == ['Streamed ', 'final.']
    assert len(calls) == 1
    command = calls[0]['command']
    assert command[:3] == ['/usr/local/bin/codex', 'app-server', '--stdio']
    assert '--json' not in command
    assert '-o' not in command
    assert 'model="gpt-5.5"' in command
    assert 'model_reasoning_effort="medium"' in command
    assert calls[0]['cwd'] == str(tmp_path)
    written_messages = [
        json.loads(line)
        for line in ''.join(fake_processes[0].stdin.values).splitlines()
        if line.strip()
    ]
    assert [message['method'] for message in written_messages] == [
        'initialize',
        'initialized',
        'thread/start',
        'turn/start',
    ]
    assert written_messages[2]['params']['ephemeral'] is True
    assert written_messages[2]['params']['sandbox'] == 'read-only'
    turn_params = written_messages[3]['params']
    assert turn_params['threadId'] == 'thread_test'
    assert turn_params['effort'] == 'medium'
    assert turn_params['sandboxPolicy'] == {'type': 'readOnly', 'networkAccess': False}
    assert 'TASK INPUT:\nReturn a short answer.' in turn_params['input'][0]['text']


def test_codex_cli_provider_uses_dm_prompt_role():
    provider = CodexCliProvider(prompt_role='dm')

    prompt = provider._build_prompt(ProviderRequest(prompt='The player opens the vault.', system_message='Narrate.'))

    assert 'main AIDM Dungeon Master narration model' in prompt
    assert 'Return only the in-world DM response' in prompt
    assert 'AIDM helper model' not in prompt
    assert 'SYSTEM CONTRACT:\nNarrate.' in prompt
    assert 'TASK INPUT:\nThe player opens the vault.' in prompt


def test_nvidia_provider_generate_parses_openai_shape(monkeypatch):
    import aidm_server.llm_providers as provider_module

    class _FakeResponse:
        status_code = 200
        text = ''

        def json(self):
            return {'choices': [{'message': {'content': 'Kimi is online.'}}]}

        def close(self):
            return None

    def fake_post(client_name, url, headers, json, timeout, stream):
        assert client_name == 'llm'
        assert stream is False
        assert url == 'https://integrate.api.nvidia.com/v1/chat/completions'
        assert json['model'] == 'moonshotai/kimi-k2.5'
        assert json['thinking'] == {'type': 'enabled'}
        assert 'chat_template_kwargs' not in json
        assert timeout == (10.0, 60.0)
        return _FakeResponse()

    monkeypatch.setattr(provider_module, 'http_post', fake_post)

    provider = NvidiaChatProvider(
        model_name='moonshotai/kimi-k2.5',
        api_key='nvapi-test',
        invoke_url='https://integrate.api.nvidia.com/v1/chat/completions',
    )
    response = provider.generate(ProviderRequest(prompt='hello'))

    assert response.provider == 'nvidia'
    assert response.model == 'moonshotai/kimi-k2.5'
    assert response.text == 'Kimi is online.'


def test_nvidia_provider_normalizes_base_v1_endpoint():
    provider = NvidiaChatProvider(
        model_name='moonshotai/kimi-k2.5',
        api_key='nvapi-test',
        invoke_url='https://integrate.api.nvidia.com/v1',
    )
    assert provider.invoke_url == 'https://integrate.api.nvidia.com/v1/chat/completions'


def test_nvidia_provider_stream_parses_sse_chunks(monkeypatch):
    import aidm_server.llm_providers as provider_module

    class _FakeStreamResponse:
        status_code = 200
        text = ''

        def iter_lines(self, decode_unicode=True):
            yield 'data: {"choices":[{"delta":{"content":"Hello "}}]}'
            yield 'data: {"choices":[{"delta":{"content":"world"}}]}'
            yield 'data: [DONE]'

        def close(self):
            return None

    def fake_post(client_name, url, headers, json, timeout, stream):
        assert client_name == 'llm'
        assert stream is True
        assert json['thinking'] == {'type': 'enabled'}
        assert timeout == (10.0, 60.0)
        return _FakeStreamResponse()

    monkeypatch.setattr(provider_module, 'http_post', fake_post)

    provider = NvidiaChatProvider(
        model_name='moonshotai/kimi-k2.5',
        api_key='nvapi-test',
        invoke_url='https://integrate.api.nvidia.com/v1/chat/completions',
    )
    chunks = list(provider.stream(ProviderRequest(prompt='hello')))
    assert chunks == ['Hello ', 'world']


def test_nvidia_provider_instant_mode_sets_disabled_thinking(monkeypatch):
    import aidm_server.llm_providers as provider_module

    class _FakeResponse:
        status_code = 200
        text = ''

        def json(self):
            return {'choices': [{'message': {'content': 'Instant mode response'}}]}

        def close(self):
            return None

    def fake_post(client_name, url, headers, json, timeout, stream):
        assert client_name == 'llm'
        assert json['thinking'] == {'type': 'disabled'}
        return _FakeResponse()

    monkeypatch.setattr(provider_module, 'http_post', fake_post)

    provider = NvidiaChatProvider(
        model_name='moonshotai/kimi-k2.5',
        api_key='nvapi-test',
        invoke_url='https://integrate.api.nvidia.com/v1',
        thinking_enabled=False,
    )
    response = provider.generate(ProviderRequest(prompt='hello'))
    assert response.text == 'Instant mode response'


def test_nvidia_provider_skips_primary_when_rate_limited_cooldown_active(monkeypatch):
    import aidm_server.llm_providers as provider_module

    monkeypatch.setattr(provider_module, 'LLM_RATE_LIMIT_THRESHOLD', 1)
    monkeypatch.setattr(provider_module, 'LLM_RATE_LIMIT_COOLDOWN_SECONDS', 120)
    NvidiaChatProvider._rate_limit_state.clear()
    attempts: list[str] = []
    closed_rate_limit_responses = []

    class _RateLimitedResponse:
        status_code = 429
        text = 'too many requests'

        def close(self):
            closed_rate_limit_responses.append(True)

    class _OkResponse:
        status_code = 200
        text = ''

        def json(self):
            return {'choices': [{'message': {'content': 'Fallback model response'}}]}

        def close(self):
            return None

    def fake_post(client_name, url, headers, json, timeout, stream):
        del client_name, url, headers, timeout
        assert stream is False
        attempts.append(json['model'])
        if json['model'] == 'moonshotai/kimi-k2.5':
            return _RateLimitedResponse()
        return _OkResponse()

    monkeypatch.setattr(provider_module, 'http_post', fake_post)

    provider = NvidiaChatProvider(
        model_name='moonshotai/kimi-k2.5',
        api_key='nvapi-test',
        invoke_url='https://integrate.api.nvidia.com/v1',
        fallback_models=['meta/llama-3.1-70b-instruct'],
    )

    first = provider.generate(ProviderRequest(prompt='hello one'))
    second = provider.generate(ProviderRequest(prompt='hello two'))

    assert first.model == 'meta/llama-3.1-70b-instruct'
    assert second.model == 'meta/llama-3.1-70b-instruct'
    assert attempts == [
        'moonshotai/kimi-k2.5',
        'meta/llama-3.1-70b-instruct',
        'meta/llama-3.1-70b-instruct',
    ]
    assert closed_rate_limit_responses == [True]


def test_nvidia_provider_stream_skips_primary_when_rate_limited_cooldown_active(monkeypatch):
    import aidm_server.llm_providers as provider_module

    monkeypatch.setattr(provider_module, 'LLM_RATE_LIMIT_THRESHOLD', 1)
    monkeypatch.setattr(provider_module, 'LLM_RATE_LIMIT_COOLDOWN_SECONDS', 120)
    NvidiaChatProvider._rate_limit_state.clear()
    attempts: list[str] = []

    class _RateLimitedResponse:
        status_code = 429
        text = 'too many requests'

        def close(self):
            return None

    class _OkStreamResponse:
        status_code = 200
        text = ''

        def iter_lines(self, decode_unicode=True):
            del decode_unicode
            yield 'data: {"choices":[{"delta":{"content":"fallback chunk"}}]}'
            yield 'data: [DONE]'

        def close(self):
            return None

    def fake_post(client_name, url, headers, json, timeout, stream):
        del client_name, url, headers, timeout
        assert stream is True
        attempts.append(json['model'])
        if json['model'] == 'moonshotai/kimi-k2.5':
            return _RateLimitedResponse()
        return _OkStreamResponse()

    monkeypatch.setattr(provider_module, 'http_post', fake_post)

    provider = NvidiaChatProvider(
        model_name='moonshotai/kimi-k2.5',
        api_key='nvapi-test',
        invoke_url='https://integrate.api.nvidia.com/v1',
        fallback_models=['meta/llama-3.1-70b-instruct'],
    )

    first = list(provider.stream(ProviderRequest(prompt='hello one')))
    second = list(provider.stream(ProviderRequest(prompt='hello two')))

    assert first == ['fallback chunk']
    assert second == ['fallback chunk']
    assert attempts == [
        'moonshotai/kimi-k2.5',
        'meta/llama-3.1-70b-instruct',
        'meta/llama-3.1-70b-instruct',
    ]


def test_deepseek_provider_uses_openai_compatible_cooldown(monkeypatch):
    import aidm_server.llm_providers as provider_module

    monkeypatch.setattr(provider_module, 'LLM_RATE_LIMIT_THRESHOLD', 1)
    monkeypatch.setattr(provider_module, 'LLM_RATE_LIMIT_COOLDOWN_SECONDS', 120)
    NvidiaChatProvider._rate_limit_state.clear()
    attempts: list[str] = []

    class _RateLimitedResponse:
        status_code = 429
        text = 'too many requests'

        def close(self):
            return None

    class _OkResponse:
        status_code = 200
        text = ''

        def json(self):
            return {'choices': [{'message': {'content': 'DeepSeek fallback response'}}]}

        def close(self):
            return None

    def fake_post(client_name, url, headers, json, timeout, stream):
        del client_name, url, headers, timeout, stream
        attempts.append(json['model'])
        if json['model'] == 'deepseek-v4-pro':
            return _RateLimitedResponse()
        return _OkResponse()

    monkeypatch.setattr(provider_module, 'http_post', fake_post)

    provider = DeepSeekChatProvider(
        model_name='deepseek-v4-pro',
        api_key='deepseek-test',
        fallback_models=['deepseek-v4-flash'],
    )

    first = provider.generate(ProviderRequest(prompt='hello one'))
    second = provider.generate(ProviderRequest(prompt='hello two'))

    assert first.provider == 'deepseek'
    assert first.model == 'deepseek-v4-flash'
    assert second.model == 'deepseek-v4-flash'
    assert attempts == ['deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-v4-flash']


def test_query_dm_function_stream_uses_generate_chunking_for_nvidia(monkeypatch):
    provider = NvidiaChatProvider(
        model_name='moonshotai/kimi-k2.5',
        api_key='nvapi-test',
        invoke_url='https://integrate.api.nvidia.com/v1',
    )

    def fail_stream(_request):
        raise AssertionError('provider.stream should not be used for NVIDIA query_dm_function_stream')

    def fake_generate(_request):
        return ProviderResponse(
            text='First sentence. Second sentence. Third sentence.',
            provider='nvidia',
            model='moonshotai/kimi-k2.5',
        )

    monkeypatch.setattr(provider, 'stream', fail_stream)
    monkeypatch.setattr(provider, 'generate', fake_generate)
    monkeypatch.setattr('aidm_server.llm.get_provider', lambda: provider)

    chunks = list(query_dm_function_stream('hello', '{"campaign":"test"}'))

    assert ''.join(chunk if chunk.endswith(' ') else f'{chunk} ' for chunk in chunks).strip().startswith('First sentence.')
    assert len(chunks) >= 1


def test_query_dm_function_stream_uses_codex_provider_stream(monkeypatch, tmp_path):
    provider = CodexCliProvider(
        model_name='gpt-5.5',
        executable='codex',
        workdir=str(tmp_path),
        reasoning_effort='medium',
        prompt_role='dm',
    )

    def fake_stream(_request):
        yield 'First '
        yield 'streamed '
        yield 'sentence.'

    def fail_generate(_request):
        raise AssertionError('provider.generate should not be used for Codex query_dm_function_stream')

    monkeypatch.setattr(provider, 'stream', fake_stream)
    monkeypatch.setattr(provider, 'generate', fail_generate)
    monkeypatch.setattr('aidm_server.llm.get_provider', lambda: provider)

    chunks = list(query_dm_function_stream('hello', '{"campaign":"test"}'))

    assert chunks == ['First ', 'streamed ', 'sentence.']


def test_query_dm_function_stream_uses_real_streaming_for_deepseek(monkeypatch):
    provider = DeepSeekChatProvider(
        model_name='deepseek-v4-pro',
        api_key='deepseek-test',
    )

    def fake_stream(_request):
        yield 'First '
        yield 'streamed '
        yield 'sentence.'

    def fail_generate(_request):
        raise AssertionError('provider.generate should not be used for DeepSeek query_dm_function_stream')

    monkeypatch.setattr(provider, 'stream', fake_stream)
    monkeypatch.setattr(provider, 'generate', fail_generate)
    monkeypatch.setattr('aidm_server.llm.get_provider', lambda: provider)

    chunks = list(query_dm_function_stream('hello', '{"campaign":"test"}'))

    assert chunks == ['First ', 'streamed ', 'sentence.']


def test_query_dm_function_stream_falls_back_to_completion_for_deepseek_stream_failure(monkeypatch):
    provider = DeepSeekChatProvider(
        model_name='deepseek-v4-pro',
        api_key='deepseek-test',
    )

    def fake_stream(_request):
        raise RuntimeError('stream unavailable')

    def fake_generate(_request):
        return ProviderResponse(
            text='Completion fallback sentence. Another fallback sentence.',
            provider='deepseek',
            model='deepseek-v4-pro',
        )

    monkeypatch.setattr(provider, 'stream', fake_stream)
    monkeypatch.setattr(provider, 'generate', fake_generate)
    monkeypatch.setattr('aidm_server.llm.get_provider', lambda: provider)

    chunks = list(query_dm_function_stream('hello', '{"campaign":"test"}'))

    assert ''.join(chunks) == 'Completion fallback sentence. Another fallback sentence.'


def test_get_provider_uses_phase_timeout_env(monkeypatch):
    monkeypatch.setenv('AIDM_LLM_PROVIDER', 'nvidia')
    monkeypatch.setenv('AIDM_LLM_MODEL', 'moonshotai/kimi-k2.5')
    monkeypatch.setenv('AIDM_NVIDIA_API_KEY', 'nvapi-test')
    monkeypatch.setenv('AIDM_NVIDIA_CONNECT_TIMEOUT_SECONDS', '2.5')
    monkeypatch.setenv('AIDM_NVIDIA_READ_TIMEOUT_SECONDS', '45')

    provider = get_provider()

    assert isinstance(provider, NvidiaChatProvider)
    assert provider.connect_timeout_seconds == 2.5
    assert provider.read_timeout_seconds == 45.0


def test_query_dm_function_stream_records_prompt_context_estimates(app, monkeypatch):
    class _FakeProvider:
        def stream(self, request):
            del request
            yield 'The gate opens.'

    monkeypatch.setattr('aidm_server.llm.get_provider', lambda: _FakeProvider())

    with app.app_context():
        chunks = list(query_dm_function_stream('open the gate', '{"campaign":"Smoke","facts":["embers"]}'))
        metrics = app.extensions['aidm_telemetry'].snapshot()

    assert chunks == ['The gate opens.']
    assert metrics['counters']['llm.prompt.estimated_tokens|operation=dm_stream'] > 0
    assert metrics['counters']['llm.context.estimated_tokens|operation=dm_stream'] == estimate_text_tokens(
        '{"campaign":"Smoke","facts":["embers"]}'
    )
    assert metrics['counters']['llm.request.estimated_tokens|operation=dm_stream'] > metrics['counters'][
        'llm.context.estimated_tokens|operation=dm_stream'
    ]


def test_chunk_text_for_stream_preserves_boundary_whitespace():
    text = 'The ash settles. Liora stands beside you.\n\nYou ask what comes next.'
    chunks = list(_chunk_text_for_stream(text, max_chunk_size=24))

    assert ''.join(chunks) == text
