"""LLM interactions with provider abstraction and deterministic fallback behavior."""

from __future__ import annotations

import logging
import math
import json

from aidm_server.contracts import ProviderRequest, ProviderResponse
from aidm_server.llm_context import CONTEXT_VERSION, build_dm_context
from aidm_server.llm_providers import (
    BaseLLMProvider,
    DeepSeekChatProvider,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_NVIDIA_MODEL,
    DeterministicFallbackProvider,
    GeminiProvider,
    LLM_RATE_LIMIT_COOLDOWN_SECONDS,
    LLM_RATE_LIMIT_THRESHOLD,
    NvidiaChatProvider,
    ProviderHTTPError,
    ProviderNotConfiguredError,
    get_provider,
)
from aidm_server.prompt_templates import DM_SYSTEM_MESSAGE, build_dm_generate_request, build_dm_stream_request
from aidm_server.services.content_settings import content_settings_from_snapshot
from aidm_server.telemetry import telemetry_event, telemetry_metric


logger = logging.getLogger(__name__)


def estimate_text_tokens(value: str | None) -> int:
    """Cheap server-side token estimate for prompt/context budgeting."""
    text = str(value or '')
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def _record_prompt_context_estimate(operation: str, request: ProviderRequest, context: str | None = None):
    prompt_tokens = estimate_text_tokens(request.prompt)
    context_tokens = estimate_text_tokens(context)
    system_tokens = estimate_text_tokens(request.system_message)
    total_tokens = prompt_tokens + system_tokens
    tags = {'operation': operation}
    telemetry_metric('llm.prompt.estimated_tokens', prompt_tokens, tags=tags)
    telemetry_metric('llm.context.estimated_tokens', context_tokens, tags=tags)
    telemetry_metric('llm.system.estimated_tokens', system_tokens, tags=tags)
    telemetry_metric('llm.request.estimated_tokens', total_tokens, tags=tags)
    telemetry_event(
        'llm.prompt_context_estimated',
        payload={
            'operation': operation,
            'prompt_tokens_estimate': prompt_tokens,
            'context_tokens_estimate': context_tokens,
            'system_tokens_estimate': system_tokens,
            'total_tokens_estimate': total_tokens,
        },
    )


def _system_message_for_dm():
    return DM_SYSTEM_MESSAGE


def _content_settings_for_context(context: str | None) -> dict:
    try:
        payload = json.loads(str(context or '{}'))
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    settings = payload.get('content_settings')
    return content_settings_from_snapshot(settings if isinstance(settings, dict) else {})


def _fallback_dm_response(user_input: str) -> str:
    return (
        'The torchlight flickers as your action reshapes the moment. '
        'I can continue with continuity-safe narration while the primary model reconnects. '
        f'You attempt: "{user_input.strip()}". '
        'Tell me your next detail, or roll if this action requires one.'
    )


def _chunk_text_for_stream(text: str, max_chunk_size: int = 260):
    full_text = str(text or '')
    if not full_text:
        return

    start = 0
    length = len(full_text)
    while start < length:
        if length - start <= max_chunk_size:
            yield full_text[start:]
            return

        window_end = min(length, start + max_chunk_size)
        split_at = -1
        split_width = 0
        for marker in ('\n\n', '. ', '! ', '? ', '\n', ' '):
            idx = full_text.rfind(marker, start + 1, window_end + 1)
            if idx > split_at:
                split_at = idx
                split_width = len(marker)

        if split_at <= start:
            split_at = window_end
            split_width = 0
        else:
            split_at += split_width

        yield full_text[start:split_at]
        start = split_at


def _completion_chunks_for_stream(provider: BaseLLMProvider, request: ProviderRequest, user_input: str):
    response = provider.generate(request)
    text = response.text.strip()
    if text:
        yield from _chunk_text_for_stream(text)
        return
    yield _fallback_dm_response(user_input)


def query_dm_function(user_input, context, speaking_player_id=None, rules_hint: dict | None = None):
    content_settings = _content_settings_for_context(str(context))
    request = build_dm_generate_request(
        user_input=str(user_input),
        context=str(context),
        rules_hint=rules_hint,
        content_rating=content_settings.get('content_rating', 'standard'),
        tone_tags=content_settings.get('tone_tags', []),
    )
    _record_prompt_context_estimate('dm_generate', request, context)

    provider = get_provider()
    try:
        response = provider.generate(request)
        text = response.text.strip()
        return text if text else _fallback_dm_response(user_input)
    except Exception as exc:
        logger.warning('DM provider failure in query_dm_function: %s', str(exc))
        telemetry_event('llm.query_dm_function.failed', payload={'error': str(exc)}, severity='warning')
        return _fallback_dm_response(user_input)


def query_dm_function_stream(user_input, context, speaking_player=None, rules_hint: dict | None = None):
    content_settings = _content_settings_for_context(str(context))
    request = build_dm_stream_request(
        user_input=str(user_input),
        context=str(context),
        speaking_player=speaking_player,
        rules_hint=rules_hint,
        content_rating=content_settings.get('content_rating', 'standard'),
        tone_tags=content_settings.get('tone_tags', []),
    )
    _record_prompt_context_estimate('dm_stream', request, context)

    provider = get_provider()
    try:
        if type(provider) is NvidiaChatProvider:
            # NVIDIA/Kimi's SSE path has been materially less reliable than
            # single-shot generation for large campaign prompts. For gameplay,
            # prefer the stable completion path and stream it to the client in
            # application-sized chunks.
            yield from _completion_chunks_for_stream(provider, request, str(user_input))
            return

        yielded = False
        try:
            for chunk in provider.stream(request):
                yielded = True
                if chunk:
                    yield chunk
        except Exception:
            if yielded or not isinstance(provider, NvidiaChatProvider):
                raise
            telemetry_event(
                'llm.query_dm_stream.completion_fallback',
                payload={'provider': getattr(provider, 'provider_name', provider.__class__.__name__)},
                severity='warning',
            )
            yield from _completion_chunks_for_stream(provider, request, str(user_input))
            return
        if not yielded:
            if isinstance(provider, NvidiaChatProvider):
                yield from _completion_chunks_for_stream(provider, request, str(user_input))
                return
            yield _fallback_dm_response(user_input)
    except Exception as exc:
        logger.warning('DM provider failure in stream: %s', str(exc))
        telemetry_event('llm.query_dm_stream.failed', payload={'error': str(exc)}, severity='warning')
        yield _fallback_dm_response(user_input)


def query_gpt(prompt, system_message=None):
    request = ProviderRequest(prompt=prompt, system_message=system_message)
    _record_prompt_context_estimate('text_generate', request)
    provider = get_provider()
    try:
        response = provider.generate(request)
        return response.text.strip() or 'No summary available.'
    except Exception as exc:
        logger.warning('Provider failure in query_gpt: %s', str(exc))
        telemetry_event('llm.query_gpt.failed', payload={'error': str(exc)}, severity='warning')
        return 'Session summary is temporarily unavailable due to AI provider unavailability.'


def query_gpt_stream(prompt, system_message=None):
    request = ProviderRequest(prompt=prompt, system_message=system_message)
    _record_prompt_context_estimate('text_stream', request)
    provider = get_provider()

    try:
        yielded = False
        for chunk in provider.stream(request):
            yielded = True
            if chunk:
                yield chunk
        if not yielded:
            yield 'No summary available.'
    except Exception as exc:
        logger.warning('Provider failure in query_gpt_stream: %s', str(exc))
        telemetry_event('llm.query_gpt_stream.failed', payload={'error': str(exc)}, severity='warning')
        yield 'Session summary is temporarily unavailable due to AI provider unavailability.'
