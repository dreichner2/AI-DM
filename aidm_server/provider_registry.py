"""LLM provider catalog and capability metadata."""

from __future__ import annotations

from copy import deepcopy


PROVIDER_CATALOG: dict[str, dict] = {
    'deepseek': {
        'id': 'deepseek',
        'label': 'DeepSeek',
        'default_model': 'deepseek-v4-pro',
        'base_url': 'https://api.deepseek.com',
        'models': [
            {'id': 'deepseek-v4-pro', 'label': 'DeepSeek V4 Pro'},
            {'id': 'deepseek-v4-flash', 'label': 'DeepSeek V4 Flash'},
            {'id': 'deepseek-chat', 'label': 'DeepSeek Chat (legacy)'},
            {'id': 'deepseek-reasoner', 'label': 'DeepSeek Reasoner (legacy)'},
        ],
        'capabilities': {
            'streaming': True,
            'openai_compatible': True,
            'thinking_control': True,
            'default_timeout_seconds': 180,
            'default_temperature': 0.7,
        },
    },
    'codex_cli': {
        'id': 'codex_cli',
        'label': 'Codex',
        'default_model': 'gpt-5.6-sol-medium',
        'models': [
            {
                'id': 'gpt-5.6-sol-medium',
                'label': 'GPT-5.6 Sol Medium',
                'runtime_model': 'gpt-5.6-sol',
                'reasoning_effort': 'medium',
            },
            {'id': 'gpt-5.5-low', 'label': 'GPT-5.5 Low', 'runtime_model': 'gpt-5.5', 'reasoning_effort': 'low'},
            {'id': 'gpt-5.5-medium', 'label': 'GPT-5.5 Medium', 'runtime_model': 'gpt-5.5', 'reasoning_effort': 'medium'},
            {'id': 'gpt-5.5-high', 'label': 'GPT-5.5 High', 'runtime_model': 'gpt-5.5', 'reasoning_effort': 'high'},
            {'id': 'gpt-5.5-xhigh', 'label': 'GPT-5.5 xHigh', 'runtime_model': 'gpt-5.5', 'reasoning_effort': 'xhigh'},
        ],
        'capabilities': {
            'streaming': True,
            'progressive_streaming': False,
            'oauth_cli': True,
            'isolated_runtime': True,
            'host_tool_access': False,
            'tool_event_policy': 'fail_closed',
            'thinking_control': True,
            'default_timeout_seconds': 240,
            'default_temperature': 0.0,
            'default_reasoning_effort': 'medium',
        },
    },
    'gemini': {
        'id': 'gemini',
        'label': 'Gemini',
        'default_model': 'models/gemini-3-flash-preview',
        'models': [
            {'id': 'models/gemini-3-flash-preview', 'label': 'Gemini 3 Flash Preview'},
            {'id': 'models/gemini-2.5-flash', 'label': 'Gemini 2.5 Flash'},
        ],
        'capabilities': {
            'streaming': True,
            'fallback_cooldown': True,
            'thinking_control': False,
            'default_timeout_seconds': 60,
            'default_temperature': 0.7,
        },
    },
    'nvidia': {
        'id': 'nvidia',
        'label': 'NVIDIA',
        'default_model': 'moonshotai/kimi-k2.5',
        'base_url': 'https://integrate.api.nvidia.com/v1',
        'models': [
            {'id': 'moonshotai/kimi-k2.5', 'label': 'Kimi K2.5'},
            {'id': 'deepseek-v4-pro', 'label': 'DeepSeek V4 Pro via NVIDIA'},
        ],
        'capabilities': {
            'streaming': True,
            'progressive_streaming': False,
            'openai_compatible': True,
            'thinking_control': True,
            'default_timeout_seconds': 60,
            'default_temperature': 1.0,
        },
    },
    'kimi': {
        'id': 'kimi',
        'label': 'Kimi',
        'default_model': 'moonshotai/kimi-k2.5',
        'base_url': 'https://integrate.api.nvidia.com/v1',
        'models': [{'id': 'moonshotai/kimi-k2.5', 'label': 'Kimi K2.5'}],
        'capabilities': {
            'streaming': True,
            'progressive_streaming': False,
            'openai_compatible': True,
            'thinking_control': True,
            'default_timeout_seconds': 60,
            'default_temperature': 1.0,
        },
    },
    'fallback': {
        'id': 'fallback',
        'label': 'Fallback',
        'default_model': 'deterministic-v1',
        'models': [{'id': 'deterministic-v1', 'label': 'Deterministic Local Fallback'}],
        'capabilities': {
            'streaming': False,
            'thinking_control': False,
            'default_timeout_seconds': 1,
            'default_temperature': 0.0,
        },
    },
}

SUPPORTED_LLM_PROVIDERS = set(PROVIDER_CATALOG)
MODEL_ID_ALIASES: dict[tuple[str, str], str] = {
    ('codex_cli', 'gpt-5.5'): 'gpt-5.5-medium',
    ('codex', 'gpt-5.5'): 'gpt-5.5-medium',
    ('codex_cli', 'gpt-5.6-sol'): 'gpt-5.6-sol-medium',
    ('codex', 'gpt-5.6-sol'): 'gpt-5.6-sol-medium',
}


def normalize_provider_model_id(provider_id: str, model_id: str) -> str:
    provider = str(provider_id or '').strip().lower()
    model = str(model_id or '').strip()
    return MODEL_ID_ALIASES.get((provider, model), model)


def provider_option(provider_id: str) -> dict | None:
    option = PROVIDER_CATALOG.get(provider_id)
    return deepcopy(option) if option else None


def provider_model_option(provider_id: str, model_id: str) -> dict | None:
    option = PROVIDER_CATALOG.get(str(provider_id or '').strip().lower())
    if not option:
        return None
    selected_model = normalize_provider_model_id(provider_id, model_id)
    for item in option.get('models', []):
        if str(item.get('id')) == selected_model:
            return deepcopy(item)
    return None


def provider_runtime_model(provider_id: str, model_id: str) -> str:
    selected_model = normalize_provider_model_id(provider_id, model_id)
    option = provider_model_option(provider_id, selected_model)
    if option:
        return str(option.get('runtime_model') or selected_model)
    return selected_model


def provider_model_reasoning_effort(provider_id: str, model_id: str) -> str | None:
    option = provider_model_option(provider_id, model_id)
    if not option:
        return None
    effort = option.get('reasoning_effort')
    return str(effort).strip().lower() if effort else None


def provider_default_model(provider_id: str) -> str:
    option = PROVIDER_CATALOG.get(provider_id) or PROVIDER_CATALOG['gemini']
    return str(option['default_model'])


def provider_capabilities(provider_id: str) -> dict:
    option = PROVIDER_CATALOG.get(provider_id) or {}
    return deepcopy(option.get('capabilities', {}))


def provider_catalog_payload() -> list[dict]:
    return [deepcopy(PROVIDER_CATALOG[key]) for key in ('deepseek', 'codex_cli', 'gemini', 'nvidia', 'kimi', 'fallback')]
