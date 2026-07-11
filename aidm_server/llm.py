"""LLM interactions with provider abstraction and deterministic fallback behavior."""

from __future__ import annotations

import json
import logging
import math
import os
import time

from flask import current_app, has_app_context

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
from aidm_server.provider_registry import provider_capabilities
from aidm_server.services.content_settings import content_settings_from_snapshot
from aidm_server.telemetry import telemetry_event, telemetry_metric


__all__ = [
    'CONTEXT_VERSION',
    'DEFAULT_DEEPSEEK_MODEL',
    'DEFAULT_GEMINI_MODEL',
    'DEFAULT_NVIDIA_MODEL',
    'DeepSeekChatProvider',
    'DeterministicFallbackProvider',
    'EmergencyFallbackChunk',
    'GeminiProvider',
    'LLM_RATE_LIMIT_COOLDOWN_SECONDS',
    'LLM_RATE_LIMIT_THRESHOLD',
    'NvidiaChatProvider',
    'ProviderHTTPError',
    'ProviderNotConfiguredError',
    'ProviderResponse',
    'build_dm_context',
    'estimate_text_tokens',
    'get_provider',
    'query_dm_function',
    'query_dm_function_stream',
    'query_gpt',
    'query_gpt_stream',
]


logger = logging.getLogger(__name__)

CONTINUITY_FALLBACK_PROVIDER = 'fallback'
CONTINUITY_FALLBACK_MODEL = 'continuity-safe-v1'
DISPLAY_STREAM_CHUNK_CHARS = 96
BUFFERED_STREAM_CHUNK_DELAY_ENV = 'AIDM_BUFFERED_STREAM_CHUNK_DELAY_MS'
DEFAULT_BUFFERED_STREAM_CHUNK_DELAY_MS = 50.0
MAX_BUFFERED_STREAM_CHUNK_DELAY_MS = 100.0
MAX_BUFFERED_STREAM_PACING_SECONDS = 1.0


class EmergencyFallbackChunk(str):
    """Narration text that was produced only because the selected provider failed.

    The type remains string-compatible for Socket.IO streaming while carrying
    enough provenance for the turn engine to avoid treating emergency narration
    as a successful response from the configured provider.
    """

    def __new__(
        cls,
        value: str,
        *,
        error: Exception | str,
        failed_provider: str,
        failed_model: str | None,
        reason: str = 'provider_exception',
    ):
        instance = super().__new__(cls, value)
        instance.error_type = error.__class__.__name__ if isinstance(error, Exception) else 'ProviderError'
        instance.public_message = 'The configured DM provider failed; continuity-safe narration was used.'
        instance.failed_provider = str(failed_provider or 'unknown')
        instance.failed_model = str(failed_model) if failed_model else None
        instance.reason = reason
        instance.provider = CONTINUITY_FALLBACK_PROVIDER
        instance.model = CONTINUITY_FALLBACK_MODEL
        return instance


def _provider_identity(provider: BaseLLMProvider | None) -> tuple[str, str | None]:
    if provider is None:
        if has_app_context():
            provider_name = str(current_app.config.get('AIDM_LLM_PROVIDER') or 'unknown')
            model_name = current_app.config.get('AIDM_LLM_MODEL')
            return provider_name, str(model_name) if model_name else None
        return 'unknown', None
    provider_name = str(getattr(provider, 'provider_name', provider.__class__.__name__) or 'unknown')
    model_name = getattr(provider, 'display_model_name', None) or getattr(provider, 'model_name', None)
    return provider_name, str(model_name) if model_name else None


def _provider_failure_payload(provider: BaseLLMProvider | None, error: Exception, **extra) -> dict:
    """Return operational failure metadata without copying upstream error text."""
    provider_name, model_name = _provider_identity(provider)
    return {
        'provider': provider_name,
        'model': model_name,
        'error_type': error.__class__.__name__,
        **extra,
    }


def _log_provider_failure(message: str, provider: BaseLLMProvider | None, error: Exception) -> None:
    provider_name, model_name = _provider_identity(provider)
    logger.warning(
        '%s provider=%s model=%s error_type=%s',
        message,
        provider_name,
        model_name or 'unknown',
        error.__class__.__name__,
    )


def _emergency_fallback_chunk(
    user_input: str,
    *,
    provider: BaseLLMProvider | None,
    error: Exception | str,
    reason: str = 'provider_exception',
) -> EmergencyFallbackChunk:
    provider_name, model_name = _provider_identity(provider)
    return EmergencyFallbackChunk(
        _fallback_dm_response(user_input),
        error=error,
        failed_provider=provider_name,
        failed_model=model_name,
        reason=reason,
    )


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


def _chunk_text_for_stream(text: str, max_chunk_size: int = DISPLAY_STREAM_CHUNK_CHARS):
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


def _buffered_stream_chunk_delay_seconds() -> float:
    runtime_env = (
        str(current_app.config.get('AIDM_ENV') or '').strip().lower()
        if has_app_context()
        else str(os.getenv('AIDM_ENV') or '').strip().lower()
    )
    default_delay_ms = 0.0 if runtime_env == 'test' else DEFAULT_BUFFERED_STREAM_CHUNK_DELAY_MS
    raw_delay = os.getenv(BUFFERED_STREAM_CHUNK_DELAY_ENV)
    if has_app_context() and current_app.config.get(BUFFERED_STREAM_CHUNK_DELAY_ENV) is not None:
        raw_delay = current_app.config.get(BUFFERED_STREAM_CHUNK_DELAY_ENV)
    try:
        delay_ms = default_delay_ms if raw_delay in (None, '') else float(raw_delay)
    except (TypeError, ValueError):
        delay_ms = default_delay_ms
    if not math.isfinite(delay_ms):
        delay_ms = default_delay_ms
    return min(MAX_BUFFERED_STREAM_CHUNK_DELAY_MS, max(0.0, delay_ms)) / 1000.0


def _provider_supports_progressive_streaming(provider: BaseLLMProvider) -> bool:
    provider_name = str(getattr(provider, 'provider_name', '') or '').strip().lower()
    capabilities = provider_capabilities(provider_name)
    if capabilities.get('streaming') is False:
        return False
    return capabilities.get('progressive_streaming') is not False


def _completion_chunks_for_stream(provider: BaseLLMProvider, request: ProviderRequest, _user_input: str):
    response = provider.generate(request)
    text = response.text.strip()
    if text:
        delay_seconds = _buffered_stream_chunk_delay_seconds()
        paced_seconds = 0.0
        for index, chunk in enumerate(_chunk_text_for_stream(text)):
            if (
                index > 0
                and delay_seconds > 0
                and paced_seconds + delay_seconds <= MAX_BUFFERED_STREAM_PACING_SECONDS
            ):
                time.sleep(delay_seconds)
                paced_seconds += delay_seconds
            yield chunk
        return
    raise RuntimeError('Provider returned an empty DM response')


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

    provider = None
    try:
        provider = get_provider()
        response = provider.generate(request)
        text = response.text.strip()
        if not text:
            raise RuntimeError('Provider returned an empty DM response')
        return text
    except Exception as exc:
        _log_provider_failure('DM provider failure in query_dm_function', provider, exc)
        telemetry_event(
            'llm.query_dm_function.failed',
            payload=_provider_failure_payload(provider, exc),
            severity='warning',
        )
        return _emergency_fallback_chunk(user_input, provider=provider, error=exc)


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

    provider = None
    try:
        provider = get_provider()
        if not _provider_supports_progressive_streaming(provider):
            # Some providers return only a completed response, even though the
            # gameplay transport supports incremental display. Preserve that
            # distinction and emit ordered application-sized chunks without
            # claiming provider token streaming.
            yield from _completion_chunks_for_stream(provider, request, str(user_input))
            return

        yielded = False
        yielded_meaningful_text = False
        try:
            for chunk in provider.stream(request):
                yielded = True
                if chunk:
                    if str(chunk).strip():
                        yielded_meaningful_text = True
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
        if not yielded_meaningful_text:
            if not yielded and isinstance(provider, NvidiaChatProvider):
                yield from _completion_chunks_for_stream(provider, request, str(user_input))
                return
            empty_error = RuntimeError('Provider returned an empty DM stream')
            telemetry_event(
                'llm.query_dm_stream.failed',
                payload={'error': str(empty_error), 'reason': 'empty_response'},
                severity='warning',
            )
            yield _emergency_fallback_chunk(
                str(user_input),
                provider=provider,
                error=empty_error,
                reason='empty_response',
            )
    except Exception as exc:
        _log_provider_failure('DM provider failure in stream', provider, exc)
        telemetry_event(
            'llm.query_dm_stream.failed',
            payload=_provider_failure_payload(provider, exc),
            severity='warning',
        )
        yield _emergency_fallback_chunk(str(user_input), provider=provider, error=exc)


def query_gpt(prompt, system_message=None):
    request = ProviderRequest(prompt=prompt, system_message=system_message)
    _record_prompt_context_estimate('text_generate', request)
    provider = get_provider()
    try:
        response = provider.generate(request)
        return response.text.strip() or 'No summary available.'
    except Exception as exc:
        _log_provider_failure('Provider failure in query_gpt', provider, exc)
        telemetry_event(
            'llm.query_gpt.failed',
            payload=_provider_failure_payload(provider, exc),
            severity='warning',
        )
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
        _log_provider_failure('Provider failure in query_gpt_stream', provider, exc)
        telemetry_event(
            'llm.query_gpt_stream.failed',
            payload=_provider_failure_payload(provider, exc),
            severity='warning',
        )
        yield 'Session summary is temporarily unavailable due to AI provider unavailability.'
