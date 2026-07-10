"""LLM provider adapters and runtime provider selection."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from threading import Lock
import time
from typing import Any, Generator

from flask import current_app, has_app_context
import requests

from aidm_server.contracts import ProviderRequest, ProviderResponse
from aidm_server.codex_runtime import codex_executable_configured, resolve_codex_executable
from aidm_server.http_client import post as http_post
from aidm_server.http_client import timeout_from_config
from aidm_server.provider_registry import (
    SUPPORTED_LLM_PROVIDERS,
    normalize_provider_model_id,
    provider_default_model,
    provider_model_reasoning_effort,
    provider_runtime_model,
)
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.time_utils import utc_now


DEFAULT_GEMINI_MODEL = provider_default_model('gemini')
DEFAULT_NVIDIA_MODEL = provider_default_model('nvidia')
DEFAULT_DEEPSEEK_MODEL = provider_default_model('deepseek')
DEFAULT_CODEX_MODEL = provider_default_model('codex_cli')
REPO_ROOT = Path(__file__).resolve().parents[1]


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


LLM_RATE_LIMIT_THRESHOLD = _int_env('AIDM_LLM_RATE_LIMIT_THRESHOLD', 2)
LLM_RATE_LIMIT_COOLDOWN_SECONDS = _int_env('AIDM_LLM_RATE_LIMIT_COOLDOWN_SECONDS', 120)


class ProviderNotConfiguredError(RuntimeError):
    pass


class ProviderHTTPError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class BaseLLMProvider:
    provider_name = 'base'

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        raise NotImplementedError

    def stream(self, request: ProviderRequest) -> Generator[str, None, None]:
        response = self.generate(request)
        if response.text:
            yield response.text


class GeminiProvider(BaseLLMProvider):
    provider_name = 'gemini'
    _rate_limit_state: dict[str, dict[str, Any]] = {}
    _rate_limit_lock = Lock()

    def __init__(self, model_name: str, api_key: str | None, fallback_models: list[str] | None = None):
        self.model_name = model_name
        self.api_key = api_key
        self.fallback_models = self._normalize_models(fallback_models or [])
        self._client = None

    @staticmethod
    def _normalize_models(model_names: list[str]) -> list[str]:
        normalized = []
        seen = set()
        for raw_name in model_names:
            model_name = str(raw_name or '').strip()
            if not model_name or model_name in seen:
                continue
            normalized.append(model_name)
            seen.add(model_name)
        return normalized

    def _candidate_models(self) -> list[str]:
        return self._normalize_models([self.model_name, *self.fallback_models])

    def _build_prompt(self, request: ProviderRequest) -> str:
        if request.system_message:
            return f"{request.system_message}\n\n{request.prompt}"
        return request.prompt

    def _ensure_sdk(self):
        if self._client is not None:
            return

        if not self.api_key:
            telemetry_event('llm.provider_not_configured', payload={'provider': self.provider_name}, severity='warning')
            raise ProviderNotConfiguredError('GOOGLE_GENAI_API_KEY is not configured')

        try:
            from google import genai
        except Exception as exc:
            telemetry_event(
                'llm.provider_import_failed',
                payload={'provider': self.provider_name, 'error': str(exc)},
                severity='error',
            )
            raise ProviderNotConfiguredError(f'google.genai SDK import failed: {str(exc)}') from exc

        self._client = genai.Client(api_key=self.api_key)

    @staticmethod
    def _extract_text(response: Any, preserve_whitespace: bool = False) -> str:
        text = getattr(response, 'text', None)
        if isinstance(text, str):
            return text if preserve_whitespace else text.strip()

        # Handle response variants where text is nested in candidates/parts.
        fragments = []
        candidates = getattr(response, 'candidates', None) or []
        for candidate in candidates:
            content = getattr(candidate, 'content', None)
            parts = getattr(content, 'parts', None) or []
            for part in parts:
                part_text = getattr(part, 'text', None)
                if isinstance(part_text, str) and part_text:
                    fragments.append(part_text)
        joined = ''.join(fragments)
        return joined if preserve_whitespace else joined.strip()

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        status_code = getattr(exc, 'status_code', None)
        if status_code == 429:
            return True
        message = str(exc).lower()
        return '429' in message or 'too many requests' in message or 'resource_exhausted' in message

    @classmethod
    def _is_model_in_cooldown(cls, model_name: str) -> tuple[bool, int]:
        now = utc_now()
        with cls._rate_limit_lock:
            state = cls._rate_limit_state.get(model_name)
            if not state:
                return False, 0
            cooldown_until = state.get('cooldown_until')
            if not isinstance(cooldown_until, datetime):
                return False, 0
            if cooldown_until <= now:
                state['cooldown_until'] = None
                return False, 0
            remaining = max(0, int((cooldown_until - now).total_seconds()))
            return True, remaining

    @classmethod
    def _record_model_success(cls, model_name: str):
        with cls._rate_limit_lock:
            state = cls._rate_limit_state.setdefault(model_name, {})
            state['consecutive_429'] = 0
            state['cooldown_until'] = None

    @classmethod
    def _record_model_rate_limit(cls, model_name: str) -> datetime | None:
        now = utc_now()
        with cls._rate_limit_lock:
            state = cls._rate_limit_state.setdefault(model_name, {})
            consecutive = int(state.get('consecutive_429', 0)) + 1
            state['consecutive_429'] = consecutive
            if consecutive < LLM_RATE_LIMIT_THRESHOLD:
                return None

            cooldown_until = now + timedelta(seconds=LLM_RATE_LIMIT_COOLDOWN_SECONDS)
            state['cooldown_until'] = cooldown_until
            state['consecutive_429'] = 0
            return cooldown_until

    def _generate_with_model(self, model_name: str, full_prompt: str) -> str:
        self._ensure_sdk()
        response = self._client.models.generate_content(
            model=model_name,
            contents=full_prompt,
        )
        return self._extract_text(response, preserve_whitespace=False)

    def _stream_with_model(self, model_name: str, full_prompt: str) -> Generator[str, None, None]:
        self._ensure_sdk()
        response = self._client.models.generate_content_stream(
            model=model_name,
            contents=full_prompt,
        )
        for chunk in response:
            text = self._extract_text(chunk, preserve_whitespace=True)
            if text != '':
                yield text

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        full_prompt = self._build_prompt(request)
        last_error: Exception | None = None

        for index, model_name in enumerate(self._candidate_models()):
            in_cooldown, remaining_seconds = self._is_model_in_cooldown(model_name)
            if in_cooldown:
                telemetry_event(
                    'llm.model_skipped_cooldown',
                    payload={
                        'provider': self.provider_name,
                        'model': model_name,
                        'remaining_seconds': remaining_seconds,
                    },
                    severity='warning',
                )
                last_error = RuntimeError(f'Model in cooldown for {remaining_seconds} seconds: {model_name}')
                continue

            try:
                text = self._generate_with_model(model_name, full_prompt).strip()
                if not text:
                    raise RuntimeError('Model returned an empty response')
                if index > 0:
                    telemetry_event(
                        'llm.model_fallback_used',
                        payload={'provider': self.provider_name, 'selected_model': model_name, 'primary_model': self.model_name},
                        severity='warning',
                    )
                telemetry_metric('llm.generate.success_total', 1, tags={'provider': self.provider_name, 'model': model_name})
                self._record_model_success(model_name)
                return ProviderResponse(text=text, provider=self.provider_name, model=model_name)
            except Exception as exc:
                last_error = exc
                if self._is_rate_limit_error(exc):
                    cooldown_until = self._record_model_rate_limit(model_name)
                    telemetry_event(
                        'llm.model_rate_limited',
                        payload={
                            'provider': self.provider_name,
                            'model': model_name,
                            'error': str(exc),
                            'cooldown_until': cooldown_until.isoformat() if cooldown_until else None,
                            'cooldown_seconds': LLM_RATE_LIMIT_COOLDOWN_SECONDS if cooldown_until else 0,
                        },
                        severity='warning',
                    )
                telemetry_event(
                    'llm.model_attempt_failed',
                    payload={'provider': self.provider_name, 'model': model_name, 'error': str(exc)},
                    severity='warning',
                )
                continue

        configured_models = ', '.join(self._candidate_models()) or self.model_name
        raise RuntimeError(f'All Gemini models failed: {configured_models}') from last_error

    def stream(self, request: ProviderRequest) -> Generator[str, None, None]:
        full_prompt = self._build_prompt(request)
        last_error: Exception | None = None

        for index, model_name in enumerate(self._candidate_models()):
            in_cooldown, remaining_seconds = self._is_model_in_cooldown(model_name)
            if in_cooldown:
                telemetry_event(
                    'llm.model_skipped_cooldown',
                    payload={
                        'provider': self.provider_name,
                        'model': model_name,
                        'remaining_seconds': remaining_seconds,
                    },
                    severity='warning',
                )
                last_error = RuntimeError(f'Model in cooldown for {remaining_seconds} seconds: {model_name}')
                continue

            yielded = False
            try:
                for chunk in self._stream_with_model(model_name, full_prompt):
                    yielded = True
                    yield chunk

                if yielded:
                    if index > 0:
                        telemetry_event(
                            'llm.model_fallback_used',
                            payload={'provider': self.provider_name, 'selected_model': model_name, 'primary_model': self.model_name},
                            severity='warning',
                        )
                    telemetry_metric('llm.stream.start_total', 1, tags={'provider': self.provider_name, 'model': model_name})
                    self._record_model_success(model_name)
                    return

                raise RuntimeError('Model returned an empty streaming response')
            except Exception as exc:
                # If streaming already began from this model, preserve continuity and let caller fall back
                # to deterministic handling instead of mixing chunks from two model outputs.
                if yielded:
                    raise
                last_error = exc
                if self._is_rate_limit_error(exc):
                    cooldown_until = self._record_model_rate_limit(model_name)
                    telemetry_event(
                        'llm.model_rate_limited',
                        payload={
                            'provider': self.provider_name,
                            'model': model_name,
                            'error': str(exc),
                            'cooldown_until': cooldown_until.isoformat() if cooldown_until else None,
                            'cooldown_seconds': LLM_RATE_LIMIT_COOLDOWN_SECONDS if cooldown_until else 0,
                        },
                        severity='warning',
                    )
                telemetry_event(
                    'llm.model_attempt_failed',
                    payload={'provider': self.provider_name, 'model': model_name, 'error': str(exc)},
                    severity='warning',
                )
                continue

        configured_models = ', '.join(self._candidate_models()) or self.model_name
        raise RuntimeError(f'All Gemini streaming models failed: {configured_models}') from last_error


class DeterministicFallbackProvider(BaseLLMProvider):
    provider_name = 'fallback'

    def __init__(self, model_name: str = 'deterministic-v1'):
        self.model_name = model_name

    def _make_text(self, request: ProviderRequest) -> str:
        prompt = request.prompt.strip()
        opening = (
            'The scene advances with deliberate tension as the world reacts to the party\'s intent. '
            'Describe your next move and I will keep continuity while we reconnect full AI narration.'
        )
        if 'roll a d20' in prompt.lower() or 'requires_roll' in prompt.lower():
            return f"{opening}\n\nThis moment likely calls for a roll. Roll a d20 and tell me the result."
        return opening

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            text=self._make_text(request),
            provider=self.provider_name,
            model=self.model_name,
        )


class NvidiaChatProvider(BaseLLMProvider):
    provider_name = 'nvidia'
    display_name = 'NVIDIA'
    _rate_limit_state: dict[str, dict[str, Any]] = {}
    _rate_limit_lock = Lock()

    def __init__(
        self,
        model_name: str,
        api_key: str | None,
        invoke_url: str,
        fallback_models: list[str] | None = None,
        max_tokens: int = 16384,
        temperature: float = 1.0,
        top_p: float = 1.0,
        thinking_enabled: bool = True,
        timeout_seconds: int = 60,
        connect_timeout_seconds: float = 10.0,
        read_timeout_seconds: float | None = None,
    ):
        self.model_name = model_name
        self.api_key = (api_key or '').strip() or None
        self.invoke_url = self._normalize_invoke_url(invoke_url)
        self.fallback_models = self._normalize_models(fallback_models or [])
        self.max_tokens = max(1, int(max_tokens))
        self.temperature = float(temperature)
        self.top_p = float(top_p)
        self.thinking_enabled = bool(thinking_enabled)
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.connect_timeout_seconds = max(0.1, float(connect_timeout_seconds))
        self.read_timeout_seconds = max(0.1, float(read_timeout_seconds or timeout_seconds))

    @staticmethod
    def _normalize_models(model_names: list[str]) -> list[str]:
        normalized = []
        seen = set()
        for raw_name in model_names:
            model_name = str(raw_name or '').strip()
            if not model_name or model_name in seen:
                continue
            normalized.append(model_name)
            seen.add(model_name)
        return normalized

    def _candidate_models(self) -> list[str]:
        return self._normalize_models([self.model_name, *self.fallback_models])

    def _rate_limit_key(self, model_name: str) -> str:
        return f'{self.provider_name}:{model_name}'

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        status_code = getattr(exc, 'status_code', None)
        if status_code == 429:
            return True
        message = str(exc).lower()
        return '429' in message or 'too many requests' in message or 'rate limit' in message or 'resource_exhausted' in message

    def _is_model_in_cooldown(self, model_name: str) -> tuple[bool, int]:
        now = utc_now()
        key = self._rate_limit_key(model_name)
        with self._rate_limit_lock:
            state = self._rate_limit_state.get(key)
            if not state:
                return False, 0
            cooldown_until = state.get('cooldown_until')
            if not isinstance(cooldown_until, datetime):
                return False, 0
            if cooldown_until <= now:
                state['cooldown_until'] = None
                return False, 0
            remaining = max(0, int((cooldown_until - now).total_seconds()))
            return True, remaining

    def _record_model_success(self, model_name: str):
        key = self._rate_limit_key(model_name)
        with self._rate_limit_lock:
            state = self._rate_limit_state.setdefault(key, {})
            state['consecutive_429'] = 0
            state['cooldown_until'] = None

    def _record_model_rate_limit(self, model_name: str) -> datetime | None:
        now = utc_now()
        key = self._rate_limit_key(model_name)
        with self._rate_limit_lock:
            state = self._rate_limit_state.setdefault(key, {})
            consecutive = int(state.get('consecutive_429', 0)) + 1
            state['consecutive_429'] = consecutive
            if consecutive < LLM_RATE_LIMIT_THRESHOLD:
                return None

            cooldown_until = now + timedelta(seconds=LLM_RATE_LIMIT_COOLDOWN_SECONDS)
            state['cooldown_until'] = cooldown_until
            state['consecutive_429'] = 0
            return cooldown_until

    def _record_model_attempt_failed(self, model_name: str, exc: Exception):
        if self._is_rate_limit_error(exc):
            cooldown_until = self._record_model_rate_limit(model_name)
            telemetry_event(
                'llm.model_rate_limited',
                payload={
                    'provider': self.provider_name,
                    'model': model_name,
                    'error': str(exc),
                    'cooldown_until': cooldown_until.isoformat() if cooldown_until else None,
                    'cooldown_seconds': LLM_RATE_LIMIT_COOLDOWN_SECONDS if cooldown_until else 0,
                },
                severity='warning',
            )
        telemetry_event(
            'llm.model_attempt_failed',
            payload={'provider': self.provider_name, 'model': model_name, 'error': str(exc)},
            severity='warning',
        )

    @staticmethod
    def _normalize_invoke_url(invoke_url: str | None) -> str:
        url = (invoke_url or '').strip().rstrip('/')
        if not url:
            return ''
        if url.endswith('/v1'):
            return f'{url}/chat/completions'
        return url

    def _ensure_configured(self):
        if not self.api_key:
            telemetry_event('llm.provider_not_configured', payload={'provider': self.provider_name}, severity='warning')
            raise ProviderNotConfiguredError('AIDM_NVIDIA_API_KEY (or NVIDIA_API_KEY) is not configured')
        if not self.invoke_url:
            raise ProviderNotConfiguredError('AIDM_NVIDIA_INVOKE_URL is not configured')

    @staticmethod
    def _as_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            fragments = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get('text')
                    if isinstance(text, str):
                        fragments.append(text)
                elif isinstance(item, str):
                    fragments.append(item)
            return ''.join(fragments)
        if isinstance(content, dict):
            text = content.get('text')
            return text if isinstance(text, str) else ''
        return ''

    @staticmethod
    def _extract_completion_text(payload: dict) -> str:
        choices = payload.get('choices') or []
        if not choices:
            return ''
        message = (choices[0] or {}).get('message') or {}
        text = NvidiaChatProvider._as_text(message.get('content'))
        if text:
            return text.strip()

        # Fallback for alternate response shapes.
        delta = (choices[0] or {}).get('delta') or {}
        return NvidiaChatProvider._as_text(delta.get('content')).strip()

    @staticmethod
    def _extract_stream_chunk(payload: dict) -> str:
        choices = payload.get('choices') or []
        fragments = []
        for choice in choices:
            delta = (choice or {}).get('delta') or {}
            value = delta.get('content')
            text = NvidiaChatProvider._as_text(value)
            if text:
                fragments.append(text)
        return ''.join(fragments)

    def _build_messages(self, request: ProviderRequest) -> list[dict]:
        messages = []
        if request.system_message:
            messages.append({'role': 'system', 'content': request.system_message})
        messages.append({'role': 'user', 'content': request.prompt})
        return messages

    def _headers(self, stream: bool) -> dict[str, str]:
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Accept': 'text/event-stream' if stream else 'application/json',
            'Content-Type': 'application/json',
        }

    def _thinking_payload(self) -> dict[str, str]:
        if self.thinking_enabled:
            return {'type': 'enabled'}
        return {'type': 'disabled'}

    def _payload_for_model(self, model_name: str, request: ProviderRequest, stream: bool) -> dict:
        return {
            'model': model_name,
            'messages': self._build_messages(request),
            'max_tokens': self.max_tokens,
            'temperature': self.temperature,
            'top_p': self.top_p,
            'stream': stream,
            # Official NVIDIA Kimi API expects a top-level `thinking` control object.
            'thinking': self._thinking_payload(),
        }

    def _post(self, payload: dict, stream: bool) -> requests.Response:
        self._ensure_configured()
        response = http_post(
            'llm',
            self.invoke_url,
            headers=self._headers(stream=stream),
            json=payload,
            timeout=(self.connect_timeout_seconds, self.read_timeout_seconds),
            stream=stream,
        )
        if response.status_code >= 400:
            status_code = int(response.status_code)
            detail = response.text[:300]
            response.close()
            raise ProviderHTTPError(
                f'{self.display_name} provider error {status_code}: {detail}',
                status_code=status_code,
            )
        return response

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        last_error: Exception | None = None
        for index, model_name in enumerate(self._candidate_models()):
            in_cooldown, remaining_seconds = self._is_model_in_cooldown(model_name)
            if in_cooldown:
                telemetry_event(
                    'llm.model_skipped_cooldown',
                    payload={
                        'provider': self.provider_name,
                        'model': model_name,
                        'remaining_seconds': remaining_seconds,
                    },
                    severity='warning',
                )
                last_error = RuntimeError(f'Model in cooldown for {remaining_seconds} seconds: {model_name}')
                continue

            try:
                payload = self._payload_for_model(model_name, request=request, stream=False)
                response = self._post(payload, stream=False)
                try:
                    data = response.json()
                finally:
                    response.close()

                text = self._extract_completion_text(data)
                if not text:
                    raise RuntimeError(f'{self.display_name} provider returned an empty completion')

                if index > 0:
                    telemetry_event(
                        'llm.model_fallback_used',
                        payload={'provider': self.provider_name, 'selected_model': model_name, 'primary_model': self.model_name},
                        severity='warning',
                    )
                telemetry_metric('llm.generate.success_total', 1, tags={'provider': self.provider_name, 'model': model_name})
                self._record_model_success(model_name)
                return ProviderResponse(text=text, provider=self.provider_name, model=model_name)
            except Exception as exc:
                last_error = exc
                self._record_model_attempt_failed(model_name, exc)
                continue

        configured = ', '.join(self._candidate_models()) or self.model_name
        raise RuntimeError(f'All {self.display_name} models failed: {configured}') from last_error

    def stream(self, request: ProviderRequest) -> Generator[str, None, None]:
        last_error: Exception | None = None
        for index, model_name in enumerate(self._candidate_models()):
            in_cooldown, remaining_seconds = self._is_model_in_cooldown(model_name)
            if in_cooldown:
                telemetry_event(
                    'llm.model_skipped_cooldown',
                    payload={
                        'provider': self.provider_name,
                        'model': model_name,
                        'remaining_seconds': remaining_seconds,
                    },
                    severity='warning',
                )
                last_error = RuntimeError(f'Model in cooldown for {remaining_seconds} seconds: {model_name}')
                continue

            yielded = False
            response = None
            try:
                payload = self._payload_for_model(model_name, request=request, stream=True)
                response = self._post(payload, stream=True)
                for raw_line in response.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue
                    line = str(raw_line).strip()
                    if not line.startswith('data:'):
                        continue
                    data_part = line[5:].strip()
                    if data_part == '[DONE]':
                        break
                    try:
                        event = json.loads(data_part)
                    except json.JSONDecodeError:
                        continue
                    chunk = self._extract_stream_chunk(event)
                    if chunk:
                        yielded = True
                        yield chunk

                if yielded:
                    if index > 0:
                        telemetry_event(
                            'llm.model_fallback_used',
                            payload={'provider': self.provider_name, 'selected_model': model_name, 'primary_model': self.model_name},
                            severity='warning',
                    )
                    telemetry_metric('llm.stream.start_total', 1, tags={'provider': self.provider_name, 'model': model_name})
                    self._record_model_success(model_name)
                    return
                raise RuntimeError(f'{self.display_name} provider returned an empty streaming response')
            except Exception as exc:
                if yielded:
                    raise
                last_error = exc
                self._record_model_attempt_failed(model_name, exc)
                continue
            finally:
                if response is not None:
                    response.close()

        configured = ', '.join(self._candidate_models()) or self.model_name
        raise RuntimeError(f'All {self.display_name} streaming models failed: {configured}') from last_error


class DeepSeekChatProvider(NvidiaChatProvider):
    provider_name = 'deepseek'
    display_name = 'DeepSeek'

    def __init__(
        self,
        model_name: str,
        api_key: str | None,
        base_url: str = 'https://api.deepseek.com',
        fallback_models: list[str] | None = None,
        max_tokens: int = 16384,
        temperature: float = 1.0,
        top_p: float = 1.0,
        thinking_enabled: bool = True,
        reasoning_effort: str = 'high',
        timeout_seconds: int = 60,
        connect_timeout_seconds: float = 10.0,
        read_timeout_seconds: float | None = None,
    ):
        super().__init__(
            model_name=model_name,
            api_key=api_key,
            invoke_url=self._chat_completion_url(base_url),
            fallback_models=fallback_models,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            thinking_enabled=thinking_enabled,
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
        )
        self.reasoning_effort = str(reasoning_effort or 'high').strip().lower()

    @staticmethod
    def _chat_completion_url(base_url: str | None) -> str:
        url = (base_url or '').strip().rstrip('/')
        if not url:
            url = 'https://api.deepseek.com'
        if url.endswith('/chat/completions'):
            return url
        return f'{url}/chat/completions'

    def _ensure_configured(self):
        if not self.api_key:
            telemetry_event('llm.provider_not_configured', payload={'provider': self.provider_name}, severity='warning')
            raise ProviderNotConfiguredError('AIDM_DEEPSEEK_API_KEY (or DEEPSEEK_API_KEY) is not configured')
        if not self.invoke_url:
            raise ProviderNotConfiguredError('AIDM_DEEPSEEK_BASE_URL is not configured')

    def _payload_for_model(self, model_name: str, request: ProviderRequest, stream: bool) -> dict:
        payload = super()._payload_for_model(model_name=model_name, request=request, stream=stream)
        if self.reasoning_effort in {'high', 'max'}:
            payload['reasoning_effort'] = self.reasoning_effort
        return payload


class CodexCliProvider(BaseLLMProvider):
    provider_name = 'codex_cli'
    # Render is deliberately single-process, but its gthread worker can invoke
    # multiple providers concurrently. Codex rotates saved OAuth refresh tokens,
    # so saved-login invocations must not race within that process.
    _saved_auth_lock = Lock()

    def __init__(
        self,
        model_name: str = 'gpt-5.5',
        executable: str = 'codex',
        workdir: str | None = None,
        timeout_seconds: int = 180,
        reasoning_effort: str = 'low',
        service_tier: str = 'default',
        ignore_rules: bool = True,
        prompt_role: str = 'helper',
        display_model_name: str | None = None,
    ):
        self.model_name = str(model_name or 'gpt-5.5').strip()
        self.display_model_name = str(display_model_name or self.model_name).strip()
        self.executable = str(executable or 'codex').strip()
        self.workdir = str(workdir or os.getcwd()).strip()
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.reasoning_effort = str(reasoning_effort or 'low').strip().lower()
        self.service_tier = str(service_tier or 'default').strip().lower()
        # Gameplay prompts are untrusted. Project and user execution rules must
        # never be allowed to expand the narrator's runtime capabilities.
        self.ignore_rules = True
        self.prompt_role = str(prompt_role or 'helper').strip().lower()

    def _resolved_executable(self) -> str:
        if not self.executable:
            raise ProviderNotConfiguredError('AIDM_CODEX_EXECUTABLE is empty')
        resolved = resolve_codex_executable(self.executable)
        if resolved:
            return resolved
        if os.path.sep in self.executable:
            raise ProviderNotConfiguredError(f'Codex executable not found: {self.executable}')
        if self.executable == 'codex':
            telemetry_event('llm.provider_not_configured', payload={'provider': self.provider_name}, severity='warning')
            raise ProviderNotConfiguredError(
                'Codex executable "codex" is not on PATH or in /Applications/Codex.app'
            )
        telemetry_event('llm.provider_not_configured', payload={'provider': self.provider_name}, severity='warning')
        raise ProviderNotConfiguredError(f'Codex executable "{self.executable}" is not on PATH')

    def _build_prompt(self, request: ProviderRequest) -> str:
        if self.prompt_role == 'dm':
            sections = [
                'You are acting as the main AIDM Dungeon Master narration model, not as a code-editing agent.',
                'Do not inspect files, run commands, modify files, or explain implementation details.',
                'Use only the campaign state, rules hint, system contract, and player action in this prompt.',
                'Return only the in-world DM response that should be shown to the player.',
            ]
        else:
            sections = [
                'You are acting as an AIDM helper model, not as a code-editing agent.',
                'Do not inspect files, run commands, modify files, or explain the codebase.',
                'Use only the task data in this prompt and return exactly the response shape requested.',
            ]
        if request.system_message:
            sections.append(f'SYSTEM CONTRACT:\n{request.system_message}')
        sections.append(f'TASK INPUT:\n{request.prompt}')
        return '\n\n'.join(sections)

    _DISABLED_FEATURES = (
        'apps',
        'browser_use',
        'browser_use_external',
        'browser_use_full_cdp_access',
        'code_mode_host',
        'computer_use',
        'hooks',
        'image_generation',
        'in_app_browser',
        'multi_agent',
        'plugins',
        'remote_plugin',
        'shell_snapshot',
        'shell_tool',
        'unified_exec',
        'workspace_dependencies',
    )
    _SAFE_ENV_COPY_KEYS = (
        'LANG',
        'LC_ALL',
        'NODE_EXTRA_CA_CERTS',
        'PATH',
        'SSL_CERT_DIR',
        'SSL_CERT_FILE',
        'SYSTEMROOT',
        'WINDIR',
    )
    _ALLOWED_EXEC_EVENT_TYPES = {
        'item.completed',
        'thread.started',
        'turn.completed',
        'turn.started',
    }
    _ALLOWED_EXEC_ITEM_TYPES = {'agent_message', 'reasoning'}
    _PERMISSION_PROFILE_OVERRIDE = (
        'permissions.aidm_narrator={'
        'description="AIDM host-isolated narrator",'
        'extends=":read-only",'
        'filesystem={":root"="deny",":minimal"="read",'
        '":workspace_roots"={"."="read"}},'
        'network={enabled=false}'
        '}'
    )

    def _command(self, runtime_workdir: str) -> list[str]:
        command = [
            self._resolved_executable(),
            'exec',
            '--json',
            '--ephemeral',
            '--ignore-user-config',
            '--ignore-rules',
            '--strict-config',
            '--skip-git-repo-check',
            '-C',
            runtime_workdir,
            '--model',
            self.model_name,
            '-c',
            'default_permissions="aidm_narrator"',
            '-c',
            'approval_policy="never"',
            '-c',
            'allow_login_shell=false',
            '-c',
            'web_search="disabled"',
            '-c',
            self._PERMISSION_PROFILE_OVERRIDE,
            '-c',
            'shell_environment_policy.inherit="none"',
            '-c',
            'shell_environment_policy.experimental_use_profile=false',
            '-c',
            'skills.bundled.enabled=false',
            '-c',
            'skills.include_instructions=false',
            '-c',
            'orchestrator.skills.enabled=false',
            '-c',
            'orchestrator.mcp.enabled=false',
            '-c',
            'include_apps_instructions=false',
            '-c',
            'include_collaboration_mode_instructions=false',
            '-c',
            'include_environment_context=false',
            '-c',
            'include_permissions_instructions=true',
            '-c',
            'tools.experimental_request_user_input.enabled=false',
            '-c',
            f'model_reasoning_effort={json.dumps(self.reasoning_effort)}',
            '-c',
            f'service_tier={json.dumps(self.service_tier)}',
        ]
        for feature in self._DISABLED_FEATURES:
            command.extend(['--disable', feature])
        command.append('-')
        return command

    @staticmethod
    def _source_codex_home() -> Path:
        configured = str(_cfg('AIDM_CODEX_HOME', os.getenv('CODEX_HOME')) or '').strip()
        return Path(configured).expanduser() if configured else Path.home() / '.codex'

    def _runtime_env(self, runtime_root: Path) -> dict[str, str]:
        runtime_home = runtime_root / 'home'
        runtime_tmp = runtime_root / 'tmp'
        for path in (runtime_home, runtime_tmp):
            path.mkdir(mode=0o700)

        env = {
            key: value
            for key in self._SAFE_ENV_COPY_KEYS
            if (value := os.getenv(key))
        }
        env.setdefault('PATH', os.defpath)
        env.update(
            {
                'HOME': str(runtime_home),
                'TEMP': str(runtime_tmp),
                'TMP': str(runtime_tmp),
                'TMPDIR': str(runtime_tmp),
            }
        )
        access_token = _cfg('AIDM_CODEX_ACCESS_TOKEN', os.getenv('CODEX_ACCESS_TOKEN'))
        if access_token:
            runtime_codex_home = runtime_root / 'codex-home'
            runtime_codex_home.mkdir(mode=0o700)
            env['CODEX_HOME'] = str(runtime_codex_home)
            env['CODEX_ACCESS_TOKEN'] = str(access_token)
        else:
            # Keep Codex pointed at its real auth store so OAuth refresh-token
            # rotation is persisted. --ignore-user-config plus explicit CLI
            # overrides prevents this directory from contributing behavior,
            # and the permissions profile denies the model access to it.
            source_codex_home = self._source_codex_home()
            if not (source_codex_home / 'auth.json').is_file():
                raise ProviderNotConfiguredError(
                    'Codex saved authentication is missing; configure AIDM_CODEX_HOME '
                    'or AIDM_CODEX_ACCESS_TOKEN'
                )
            env['CODEX_HOME'] = str(source_codex_home)
        return env

    def _parse_exec_output(self, stdout: str) -> str:
        accumulated_text = ''
        lifecycle_state = 'await_thread'
        for raw_line in str(stdout or '').splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            invalid_json = False
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                invalid_json = True
                event = None
            if invalid_json:
                raise RuntimeError('Codex CLI provider returned invalid structured output')
            if not isinstance(event, dict):
                raise RuntimeError('Codex CLI provider returned invalid structured output')

            event_type = event.get('type')
            if event_type not in self._ALLOWED_EXEC_EVENT_TYPES:
                raise RuntimeError('Codex CLI provider returned an unexpected event')

            if event_type == 'thread.started':
                thread_id = event.get('thread_id')
                if (
                    lifecycle_state != 'await_thread'
                    or not isinstance(thread_id, str)
                    or not thread_id.strip()
                ):
                    raise RuntimeError('Codex CLI provider returned invalid lifecycle output')
                lifecycle_state = 'await_turn'
                continue
            if event_type == 'turn.started':
                if lifecycle_state != 'await_turn':
                    raise RuntimeError('Codex CLI provider returned invalid lifecycle output')
                lifecycle_state = 'in_turn'
                continue
            if event_type == 'turn.completed':
                if lifecycle_state != 'in_turn':
                    raise RuntimeError('Codex CLI provider returned invalid lifecycle output')
                lifecycle_state = 'completed'
                continue
            if lifecycle_state != 'in_turn':
                raise RuntimeError('Codex CLI provider returned invalid lifecycle output')

            item = event.get('item')
            if not isinstance(item, dict):
                raise RuntimeError('Codex CLI provider returned invalid structured output')
            item_type = item.get('type')
            if not isinstance(item_type, str) or not item_type:
                raise RuntimeError('Codex CLI provider returned invalid structured output')
            if item_type not in self._ALLOWED_EXEC_ITEM_TYPES:
                raise RuntimeError('Codex CLI provider attempted a disabled tool')
            if item_type == 'agent_message':
                text = item.get('text')
                if not isinstance(text, str) or not text.strip():
                    raise RuntimeError('Codex CLI provider returned invalid structured output')
                accumulated_text += text

        if lifecycle_state != 'completed':
            raise RuntimeError('Codex CLI provider did not complete the turn')
        if not accumulated_text.strip():
            raise RuntimeError('Codex CLI provider returned an empty response')
        return accumulated_text.strip()

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen):
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                process.kill()
        else:
            process.kill()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()

    @classmethod
    def _run_process(
        cls,
        command,
        *,
        input,
        capture_output,
        text,
        timeout,
        cwd,
        env,
        check,
    ):
        if not capture_output:
            raise ValueError('Codex CLI provider requires captured output')
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            cwd=cwd,
            env=env,
            start_new_session=os.name == 'posix',
        )
        try:
            stdout, stderr = process.communicate(input=input, timeout=timeout)
        except BaseException:
            cls._terminate_process_tree(process)
            raise
        completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if check:
            completed.check_returncode()
        return completed

    def _invoke(self, prompt: str) -> str:
        try:
            with tempfile.TemporaryDirectory(prefix='aidm-codex-runtime-') as runtime_dir:
                runtime_root = Path(runtime_dir)
                runtime_workdir = runtime_root / 'workspace'
                runtime_workdir.mkdir(mode=0o700)
                runtime_env = self._runtime_env(runtime_root)
                deadline = time.monotonic() + self.timeout_seconds

                def run_codex(timeout_seconds):
                    return self._run_process(
                        self._command(str(runtime_workdir)),
                        input=prompt,
                        capture_output=True,
                        text=True,
                        timeout=timeout_seconds,
                        cwd=str(runtime_workdir),
                        env=runtime_env,
                        check=False,
                    )

                if 'CODEX_ACCESS_TOKEN' in runtime_env:
                    completed = run_codex(self.timeout_seconds)
                else:
                    acquired = self._saved_auth_lock.acquire(timeout=self.timeout_seconds)
                    if not acquired:
                        raise subprocess.TimeoutExpired(self.executable, self.timeout_seconds)
                    try:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(self.executable, self.timeout_seconds)
                        completed = run_codex(remaining)
                    finally:
                        self._saved_auth_lock.release()
                if completed.returncode != 0:
                    raise RuntimeError(f'Codex CLI provider failed (exit {completed.returncode})')
                return self._parse_exec_output(completed.stdout)
        except subprocess.TimeoutExpired as exc:
            exc.output = None
            exc.stderr = None
            raise RuntimeError(f'Codex CLI provider timed out after {self.timeout_seconds} seconds') from None
        except OSError:
            raise RuntimeError('Codex CLI provider failed to start') from None
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError('Codex CLI provider failed') from None

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        text = self._invoke(self._build_prompt(request))
        telemetry_metric(
            'llm.generate.success_total',
            1,
            tags={'provider': self.provider_name, 'model': self.display_model_name},
        )
        return ProviderResponse(text=text, provider=self.provider_name, model=self.display_model_name)

    def stream(self, request: ProviderRequest) -> Generator[str, None, None]:
        text = self._invoke(self._build_prompt(request))
        telemetry_metric(
            'llm.stream.success_total',
            1,
            tags={'provider': self.provider_name, 'model': self.display_model_name},
        )
        if text:
            yield text


def _cfg(key: str, default=None):
    if has_app_context():
        value = current_app.config.get(key, None)
        if value is not None:
            return value
    return os.getenv(key, default)


def _cfg_list(key: str) -> list[str]:
    raw_value = _cfg(key, [])
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    if isinstance(raw_value, str):
        return [item.strip() for item in raw_value.split(',') if item.strip()]
    return []


HELPER_MODEL_PROFILES: dict[str, dict[str, Any]] = {
    'fast': {
        'LLM_PROVIDER': 'deepseek',
        'LLM_MODEL': 'deepseek-v4-flash',
        'LLM_MAX_TOKENS': 2048,
        'LLM_TEMPERATURE': 0.1,
        'LLM_TOP_P': 0.9,
        'DEEPSEEK_TIMEOUT_SECONDS': 30,
        'DEEPSEEK_THINKING': 'false',
        'DEEPSEEK_REASONING_EFFORT': 'low',
    },
    'deepseek_pro': {
        'LLM_PROVIDER': 'deepseek',
        'LLM_MODEL': 'deepseek-v4-pro',
        'LLM_MAX_TOKENS': 3072,
        'LLM_TEMPERATURE': 0.55,
        'LLM_TOP_P': 0.9,
        'DEEPSEEK_TIMEOUT_SECONDS': 90,
        'DEEPSEEK_THINKING': 'false',
        'DEEPSEEK_REASONING_EFFORT': 'medium',
    },
    'codex': {
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.5',
        'CODEX_TIMEOUT_SECONDS': 180,
        'CODEX_REASONING_EFFORT': 'low',
        'CODEX_IGNORE_RULES': 'true',
    },
    'codex_low': {
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.5',
        'CODEX_TIMEOUT_SECONDS': 180,
        'CODEX_REASONING_EFFORT': 'low',
        'CODEX_IGNORE_RULES': 'true',
    },
    'codex_medium': {
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.5',
        'CODEX_TIMEOUT_SECONDS': 240,
        'CODEX_REASONING_EFFORT': 'medium',
        'CODEX_IGNORE_RULES': 'true',
    },
    'codex_56_sol_medium': {
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.6-sol',
        'CODEX_TIMEOUT_SECONDS': 240,
        'CODEX_REASONING_EFFORT': 'medium',
        'CODEX_IGNORE_RULES': 'true',
    },
    'codex_56_sol_high': {
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.6-sol',
        'CODEX_TIMEOUT_SECONDS': 300,
        'CODEX_REASONING_EFFORT': 'high',
        'CODEX_IGNORE_RULES': 'true',
    },
    'codex_56_terra_medium': {
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.6-terra',
        'CODEX_TIMEOUT_SECONDS': 240,
        'CODEX_REASONING_EFFORT': 'medium',
        'CODEX_IGNORE_RULES': 'true',
    },
    'codex_56_terra_medium_fast': {
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.6-terra',
        'CODEX_TIMEOUT_SECONDS': 240,
        'CODEX_REASONING_EFFORT': 'medium',
        'CODEX_SERVICE_TIER': 'priority',
        'CODEX_IGNORE_RULES': 'true',
    },
    'codex_56_terra_light_fast': {
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.6-terra',
        'CODEX_TIMEOUT_SECONDS': 180,
        'CODEX_REASONING_EFFORT': 'low',
        'CODEX_SERVICE_TIER': 'priority',
        'CODEX_IGNORE_RULES': 'true',
    },
    'codex_56_luna_medium': {
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.6-luna',
        'CODEX_TIMEOUT_SECONDS': 240,
        'CODEX_REASONING_EFFORT': 'medium',
        'CODEX_IGNORE_RULES': 'true',
    },
    'codex_56_luna_high': {
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.6-luna',
        'CODEX_TIMEOUT_SECONDS': 300,
        'CODEX_REASONING_EFFORT': 'high',
        'CODEX_IGNORE_RULES': 'true',
    },
    'codex_56_luna_high_fast': {
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.6-luna',
        'CODEX_TIMEOUT_SECONDS': 300,
        'CODEX_REASONING_EFFORT': 'high',
        'CODEX_SERVICE_TIER': 'priority',
        'CODEX_IGNORE_RULES': 'true',
    },
    'codex_high': {
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.5',
        'CODEX_TIMEOUT_SECONDS': 300,
        'CODEX_REASONING_EFFORT': 'high',
        'CODEX_IGNORE_RULES': 'true',
    },
    'codex_extra_high': {
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.5',
        'CODEX_TIMEOUT_SECONDS': 360,
        'CODEX_REASONING_EFFORT': 'xhigh',
        'CODEX_IGNORE_RULES': 'true',
    },
}


HELPER_TASK_PROFILE: dict[str, str] = {
    'custom_race': 'codex_56_sol_medium',
    'sentient_enemy_brain': 'codex_56_sol_medium',
    'enemy_tactics_planner': 'codex_56_sol_medium',
    'enemy_tactics_compiler': 'fast',
    'boss_tactics': 'codex_56_sol_medium',
    'boss_tactics_planner': 'codex_56_sol_medium',
    'creature_generation': 'codex_56_sol_medium',
}


HELPER_TASK_DEFAULTS: dict[str, dict[str, Any]] = {
    'custom_race': {
        'prefix': 'AIDM_CUSTOM_RACE_HELPER',
        'LLM_PROVIDER': 'deepseek',
        'LLM_MODEL': 'deepseek-v4-pro',
        'LLM_MAX_TOKENS': 4096,
        'LLM_TEMPERATURE': 0.2,
        'LLM_TOP_P': 0.9,
        'DEEPSEEK_TIMEOUT_SECONDS': 180,
        'DEEPSEEK_THINKING': 'false',
        'DEEPSEEK_REASONING_EFFORT': 'low',
    },
    'sentient_enemy_brain': {
        'prefix': 'AIDM_SENTIENT_ENEMY_BRAIN_HELPER',
        'LLM_PROVIDER': 'deepseek',
        'LLM_MODEL': 'deepseek-v4-pro',
        'LLM_MAX_TOKENS': 768,
        'LLM_TEMPERATURE': 0.1,
        'LLM_TOP_P': 0.9,
        'DEEPSEEK_TIMEOUT_SECONDS': 90,
        'DEEPSEEK_THINKING': 'false',
        'DEEPSEEK_REASONING_EFFORT': 'medium',
    },
    'enemy_tactics_planner': {
        'prefix': 'AIDM_ENEMY_TACTICS_PLANNER_HELPER',
        'LLM_PROVIDER': 'codex_cli',
        'LLM_MODEL': 'gpt-5.5',
        'CODEX_TIMEOUT_SECONDS': 240,
        'CODEX_REASONING_EFFORT': 'medium',
        'CODEX_IGNORE_RULES': 'true',
    },
    'enemy_tactics_compiler': {
        'prefix': 'AIDM_ENEMY_TACTICS_COMPILER_HELPER',
        'LLM_PROVIDER': 'deepseek',
        'LLM_MODEL': 'deepseek-v4-flash',
        'LLM_MAX_TOKENS': 1024,
        'LLM_TEMPERATURE': 0.05,
        'LLM_TOP_P': 0.9,
        'DEEPSEEK_TIMEOUT_SECONDS': 30,
        'DEEPSEEK_THINKING': 'false',
        'DEEPSEEK_REASONING_EFFORT': 'low',
    },
    'boss_tactics': {
        'prefix': 'AIDM_BOSS_TACTICS_HELPER',
        'LLM_PROVIDER': 'deepseek',
        'LLM_MODEL': 'deepseek-v4-pro',
        'LLM_MAX_TOKENS': 3072,
        'LLM_TEMPERATURE': 0.55,
        'LLM_TOP_P': 0.9,
        'DEEPSEEK_TIMEOUT_SECONDS': 90,
        'DEEPSEEK_THINKING': 'false',
        'DEEPSEEK_REASONING_EFFORT': 'medium',
    },
    'boss_tactics_planner': {
        'prefix': 'AIDM_BOSS_TACTICS_PLANNER_HELPER',
        'LLM_PROVIDER': 'deepseek',
        'LLM_MODEL': 'deepseek-v4-pro',
        'LLM_MAX_TOKENS': 2048,
        'LLM_TEMPERATURE': 0.6,
        'LLM_TOP_P': 0.9,
        'DEEPSEEK_TIMEOUT_SECONDS': 90,
        'DEEPSEEK_THINKING': 'false',
        'DEEPSEEK_REASONING_EFFORT': 'medium',
    },
    'creature_generation': {
        'prefix': 'AIDM_CREATURE_HELPER',
        'LLM_PROVIDER': 'deepseek',
        'LLM_MODEL': 'deepseek-v4-flash',
        'LLM_MAX_TOKENS': 4096,
        'LLM_TEMPERATURE': 0.2,
        'LLM_TOP_P': 0.9,
        'DEEPSEEK_TIMEOUT_SECONDS': 120,
        'DEEPSEEK_THINKING': 'false',
        'DEEPSEEK_REASONING_EFFORT': 'low',
    },
}


def _helper_task_name(task: str | None) -> str:
    return str(task or '').strip().lower().replace('-', '_')


def _helper_task_config(task: str | None) -> dict[str, Any] | None:
    return HELPER_TASK_DEFAULTS.get(_helper_task_name(task))


def _explicit_helper_profile_name(task: str | None) -> str:
    task_name = _helper_task_name(task)
    task_config = _helper_task_config(task)
    task_env_key = f"AIDM_HELPER_PROFILE_{task_name.upper()}" if task_name else ''
    for key in (task_env_key, f"{task_config['prefix']}_PROFILE" if task_config else ''):
        if not key:
            continue
        value = _cfg(key, None)
        if value not in (None, ''):
            return str(value).strip().lower()
    value = _cfg('AIDM_HELPER_PROFILE_DEFAULT', None)
    return str(value or '').strip().lower()


def _helper_profile_name(task: str | None) -> str:
    explicit = _explicit_helper_profile_name(task)
    if explicit:
        return explicit
    mapped = HELPER_TASK_PROFILE.get(_helper_task_name(task))
    if mapped:
        return mapped
    return ''


def _helper_profile_config(task: str | None) -> tuple[dict[str, Any] | None, bool]:
    explicit_name = _explicit_helper_profile_name(task)
    if explicit_name:
        return HELPER_MODEL_PROFILES.get(explicit_name), True
    profile_name = HELPER_TASK_PROFILE.get(_helper_task_name(task))
    if not profile_name:
        return None, False
    return HELPER_MODEL_PROFILES.get(profile_name), False


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _helper_cfg(task: str | None, suffix: str, default=None):
    task_config = _helper_task_config(task)
    profile_config, profile_is_explicit = _helper_profile_config(task)
    if task_config:
        value = _cfg(f"{task_config['prefix']}_{suffix}", None)
        if value is not None:
            return value
    if profile_config and suffix in profile_config and (profile_is_explicit or suffix in {'LLM_PROVIDER', 'LLM_MODEL'}):
        return profile_config[suffix]
    if task_config:
        if suffix in task_config:
            return task_config[suffix]
    if profile_config and suffix in profile_config:
        return profile_config[suffix]
    return _cfg(f'AIDM_HELPER_{suffix}', default)


def _helper_cfg_list(task: str | None, suffix: str) -> list[str]:
    raw_value = _helper_cfg(task, suffix, [])
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    if isinstance(raw_value, str):
        return [item.strip() for item in raw_value.split(',') if item.strip()]
    return []


def _helper_int(task: str | None, suffix: str, default: int) -> int:
    return _positive_int(_helper_cfg(task, suffix, default), default)


def _helper_float(task: str | None, suffix: str, default: float) -> float:
    try:
        return float(_helper_cfg(task, suffix, default))
    except (TypeError, ValueError):
        return default


def _helper_bool(task: str | None, suffix: str, default: bool) -> bool:
    raw_value = _helper_cfg(task, suffix, 'true' if default else 'false')
    if isinstance(raw_value, bool):
        return raw_value
    return str(raw_value).strip().lower() in {'1', 'true', 'yes', 'on'}


def helper_provider_configured(provider_name: str) -> bool:
    provider = str(provider_name or '').strip().lower()
    if provider == 'fallback':
        return True
    if provider == 'deepseek':
        return bool(
            _cfg('AIDM_HELPER_DEEPSEEK_API_KEY')
            or _cfg('AIDM_DEEPSEEK_API_KEY', os.getenv('DEEPSEEK_API_KEY'))
            or os.getenv('DEEPSEEK_API_KEY')
        )
    if provider in {'nvidia', 'kimi'}:
        return bool(
            _cfg('AIDM_HELPER_NVIDIA_API_KEY')
            or _cfg('AIDM_NVIDIA_API_KEY', os.getenv('NVIDIA_API_KEY'))
            or os.getenv('NVIDIA_API_KEY')
        )
    if provider == 'gemini':
        return bool(_cfg('GOOGLE_GENAI_API_KEY'))
    if provider in {'codex', 'codex_cli'}:
        executable = str(_cfg('AIDM_CODEX_EXECUTABLE', os.getenv('AIDM_CODEX_EXECUTABLE', 'codex')) or 'codex')
        return codex_executable_configured(executable)
    return False


def _helper_timeout_prefix(task: str | None, provider_suffix: str) -> str:
    task_config = _helper_task_config(task)
    if task_config:
        return f"{task_config['prefix']}_{provider_suffix}"
    return f'AIDM_HELPER_{provider_suffix}'


def helper_provider_name(task: str | None = None) -> str:
    return str(_helper_cfg(task, 'LLM_PROVIDER', 'deepseek')).strip().lower()


def get_provider() -> BaseLLMProvider:
    provider_name = str(_cfg('AIDM_LLM_PROVIDER', 'gemini')).strip().lower()
    if provider_name not in SUPPORTED_LLM_PROVIDERS:
        raise ProviderNotConfiguredError(
            'Unsupported AIDM_LLM_PROVIDER '
            f'"{provider_name}". Expected one of: {", ".join(sorted(SUPPORTED_LLM_PROVIDERS))}.'
        )
    model_name = str(_cfg('AIDM_LLM_MODEL', provider_default_model(provider_name)))
    fallback_models = _cfg_list('AIDM_LLM_FALLBACK_MODELS')

    if provider_name == 'gemini':
        return GeminiProvider(
            model_name=model_name,
            api_key=_cfg('GOOGLE_GENAI_API_KEY'),
            fallback_models=fallback_models,
        )
    if provider_name == 'deepseek':
        chosen_model = model_name or DEFAULT_DEEPSEEK_MODEL
        if chosen_model == DEFAULT_GEMINI_MODEL:
            chosen_model = DEFAULT_DEEPSEEK_MODEL
        thinking_enabled = str(_cfg('AIDM_DEEPSEEK_THINKING', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'}
        default_read_timeout = _int_env('AIDM_DEEPSEEK_TIMEOUT_SECONDS', 180)
        connect_timeout, read_timeout = timeout_from_config(
            'AIDM_DEEPSEEK',
            default_connect=10.0,
            default_read=default_read_timeout,
        )
        return DeepSeekChatProvider(
            model_name=chosen_model,
            api_key=_cfg(
                'AIDM_DEEPSEEK_API_KEY',
                os.getenv('DEEPSEEK_API_KEY'),
            ),
            base_url=str(_cfg('AIDM_DEEPSEEK_BASE_URL', 'https://api.deepseek.com')),
            fallback_models=fallback_models,
            max_tokens=_int_env('AIDM_DEEPSEEK_MAX_TOKENS', 16384),
            temperature=_float_env('AIDM_DEEPSEEK_TEMPERATURE', 1.0),
            top_p=_float_env('AIDM_DEEPSEEK_TOP_P', 0.95),
            thinking_enabled=thinking_enabled,
            reasoning_effort=str(_cfg('AIDM_DEEPSEEK_REASONING_EFFORT', 'high')),
            timeout_seconds=int(read_timeout),
            connect_timeout_seconds=connect_timeout,
            read_timeout_seconds=read_timeout,
        )
    if provider_name in {'nvidia', 'kimi'}:
        chosen_model = model_name or DEFAULT_NVIDIA_MODEL
        if chosen_model == DEFAULT_GEMINI_MODEL:
            chosen_model = DEFAULT_NVIDIA_MODEL
        thinking_enabled = str(_cfg('AIDM_NVIDIA_THINKING', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'}
        default_temperature = 1.0 if thinking_enabled else 0.6
        default_read_timeout = _int_env('AIDM_NVIDIA_TIMEOUT_SECONDS', 60)
        connect_timeout, read_timeout = timeout_from_config(
            'AIDM_NVIDIA',
            default_connect=10.0,
            default_read=default_read_timeout,
        )
        return NvidiaChatProvider(
            model_name=chosen_model,
            api_key=_cfg('AIDM_NVIDIA_API_KEY', os.getenv('NVIDIA_API_KEY')),
            invoke_url=str(_cfg('AIDM_NVIDIA_INVOKE_URL', 'https://integrate.api.nvidia.com/v1')),
            fallback_models=fallback_models,
            max_tokens=_int_env('AIDM_NVIDIA_MAX_TOKENS', 16384),
            temperature=_float_env('AIDM_NVIDIA_TEMPERATURE', default_temperature),
            top_p=_float_env('AIDM_NVIDIA_TOP_P', 0.95),
            thinking_enabled=thinking_enabled,
            timeout_seconds=int(read_timeout),
            connect_timeout_seconds=connect_timeout,
            read_timeout_seconds=read_timeout,
        )
    if provider_name in {'codex', 'codex_cli'}:
        selected_model = normalize_provider_model_id('codex_cli', model_name or DEFAULT_CODEX_MODEL)
        chosen_model = provider_runtime_model('codex_cli', selected_model)
        if chosen_model == DEFAULT_GEMINI_MODEL:
            chosen_model = 'gpt-5.5'
        reasoning_effort = (
            provider_model_reasoning_effort('codex_cli', selected_model)
            or str(_cfg('AIDM_CODEX_REASONING_EFFORT', 'medium'))
        )
        return CodexCliProvider(
            model_name=chosen_model,
            executable=str(_cfg('AIDM_CODEX_EXECUTABLE', 'codex')),
            workdir=str(_cfg('AIDM_CODEX_WORKDIR', str(REPO_ROOT))),
            timeout_seconds=_int_env('AIDM_CODEX_TIMEOUT_SECONDS', 240),
            reasoning_effort=reasoning_effort,
            service_tier=str(_cfg('AIDM_CODEX_SERVICE_TIER', 'default')),
            ignore_rules=str(_cfg('AIDM_CODEX_IGNORE_RULES', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'},
            prompt_role='dm',
            display_model_name=selected_model,
        )

    return DeterministicFallbackProvider()


def get_helper_provider(task: str | None = None) -> BaseLLMProvider:
    provider_name = str(_helper_cfg(task, 'LLM_PROVIDER', 'deepseek')).strip().lower()
    model_name = str(_helper_cfg(task, 'LLM_MODEL', 'deepseek-v4-flash')).strip()
    fallback_models = _helper_cfg_list(task, 'LLM_FALLBACK_MODELS')
    max_tokens = _helper_int(task, 'LLM_MAX_TOKENS', 2048)
    temperature = _helper_float(task, 'LLM_TEMPERATURE', 0.1)
    top_p = _helper_float(task, 'LLM_TOP_P', 0.9)

    if provider_name == 'deepseek':
        default_read_timeout = _helper_int(task, 'DEEPSEEK_TIMEOUT_SECONDS', 30)
        connect_timeout, read_timeout = timeout_from_config(
            _helper_timeout_prefix(task, 'DEEPSEEK'),
            default_connect=5.0,
            default_read=default_read_timeout,
        )
        return DeepSeekChatProvider(
            model_name=model_name or 'deepseek-v4-flash',
            api_key=_helper_cfg(
                task,
                'DEEPSEEK_API_KEY',
                _cfg('AIDM_DEEPSEEK_API_KEY', os.getenv('DEEPSEEK_API_KEY')),
            ),
            base_url=str(
                _helper_cfg(
                    task,
                    'DEEPSEEK_BASE_URL',
                    _cfg('AIDM_DEEPSEEK_BASE_URL', 'https://api.deepseek.com'),
                )
            ),
            fallback_models=fallback_models,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            thinking_enabled=_helper_bool(task, 'DEEPSEEK_THINKING', False),
            reasoning_effort=str(_helper_cfg(task, 'DEEPSEEK_REASONING_EFFORT', 'low')),
            timeout_seconds=int(read_timeout),
            connect_timeout_seconds=connect_timeout,
            read_timeout_seconds=read_timeout,
        )

    if provider_name in {'nvidia', 'kimi'}:
        default_read_timeout = _helper_int(task, 'NVIDIA_TIMEOUT_SECONDS', 30)
        connect_timeout, read_timeout = timeout_from_config(
            _helper_timeout_prefix(task, 'NVIDIA'),
            default_connect=5.0,
            default_read=default_read_timeout,
        )
        return NvidiaChatProvider(
            model_name=model_name or DEFAULT_NVIDIA_MODEL,
            api_key=_helper_cfg(
                task,
                'NVIDIA_API_KEY',
                _cfg('AIDM_NVIDIA_API_KEY', os.getenv('NVIDIA_API_KEY')),
            ),
            invoke_url=str(
                _helper_cfg(
                    task,
                    'NVIDIA_INVOKE_URL',
                    _cfg('AIDM_NVIDIA_INVOKE_URL', 'https://integrate.api.nvidia.com/v1'),
                )
            ),
            fallback_models=fallback_models,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            thinking_enabled=_helper_bool(task, 'NVIDIA_THINKING', False),
            timeout_seconds=int(read_timeout),
            connect_timeout_seconds=connect_timeout,
            read_timeout_seconds=read_timeout,
        )

    if provider_name in {'codex', 'codex_cli'}:
        return CodexCliProvider(
            model_name=model_name or 'gpt-5.5',
            executable=str(_helper_cfg(task, 'CODEX_EXECUTABLE', _cfg('AIDM_CODEX_EXECUTABLE', 'codex'))),
            workdir=str(_helper_cfg(task, 'CODEX_WORKDIR', _cfg('AIDM_CODEX_WORKDIR', str(REPO_ROOT)))),
            timeout_seconds=_helper_int(task, 'CODEX_TIMEOUT_SECONDS', 180),
            reasoning_effort=str(_helper_cfg(task, 'CODEX_REASONING_EFFORT', 'low')),
            service_tier=str(_helper_cfg(task, 'CODEX_SERVICE_TIER', 'default')),
            ignore_rules=_helper_bool(task, 'CODEX_IGNORE_RULES', True),
        )

    return DeterministicFallbackProvider(model_name='state-helper-fallback-v1')
