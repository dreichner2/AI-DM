"""LLM provider adapters and runtime provider selection."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
from threading import Lock, Thread
import time
from typing import Any, Generator
from uuid import uuid4

from flask import current_app, has_app_context
import requests

from aidm_server.contracts import ProviderRequest, ProviderResponse
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

    def __init__(
        self,
        model_name: str = 'gpt-5.5',
        executable: str = 'codex',
        workdir: str | None = None,
        timeout_seconds: int = 180,
        reasoning_effort: str = 'low',
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
        self.ignore_rules = bool(ignore_rules)
        self.prompt_role = str(prompt_role or 'helper').strip().lower()

    def _resolved_executable(self) -> str:
        if not self.executable:
            raise ProviderNotConfiguredError('AIDM_CODEX_EXECUTABLE is empty')
        if os.path.sep in self.executable:
            if Path(self.executable).is_file():
                return self.executable
            raise ProviderNotConfiguredError(f'Codex executable not found: {self.executable}')
        resolved = shutil.which(self.executable)
        if not resolved:
            telemetry_event('llm.provider_not_configured', payload={'provider': self.provider_name}, severity='warning')
            raise ProviderNotConfiguredError(f'Codex executable "{self.executable}" is not on PATH')
        return resolved

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

    def _command(self, output_path: str | None = None, *, json_output: bool = False) -> list[str]:
        command = [
            self._resolved_executable(),
            'exec',
        ]
        if json_output:
            command.append('--json')
        command.extend(
            [
                '--ephemeral',
                '--sandbox',
                'read-only',
                '-C',
                self.workdir,
                '--model',
                self.model_name,
                '-c',
                f'model_reasoning_effort="{self.reasoning_effort}"',
            ]
        )
        if output_path:
            command.extend(['-o', output_path])
        if self.ignore_rules:
            command.insert(3, '--ignore-rules')
        command.append('-')
        return command

    @staticmethod
    def _toml_string(value: str) -> str:
        return json.dumps(str(value or ''))

    def _app_server_command(self) -> list[str]:
        return [
            self._resolved_executable(),
            'app-server',
            '--stdio',
            '-c',
            f'model={self._toml_string(self.model_name)}',
            '-c',
            f'model_reasoning_effort={self._toml_string(self.reasoning_effort)}',
            '-c',
            'approval_policy="never"',
            '-c',
            'sandbox_mode="read-only"',
        ]

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        access_token = _cfg('AIDM_CODEX_ACCESS_TOKEN', os.getenv('CODEX_ACCESS_TOKEN'))
        if access_token and not env.get('CODEX_ACCESS_TOKEN'):
            env['CODEX_ACCESS_TOKEN'] = str(access_token)
        return env

    @staticmethod
    def _error_preview(value: str) -> str:
        return str(value or '').strip()[-1200:]

    @staticmethod
    def _send_app_server_message(process: subprocess.Popen, message: dict[str, Any]):
        if process.stdin is None:
            raise RuntimeError('Codex app-server stdin is unavailable')
        process.stdin.write(json.dumps(message, separators=(',', ':')) + '\n')
        process.stdin.flush()

    @staticmethod
    def _pipe_reader(name: str, pipe, output_queue: queue.Queue):
        try:
            for line in pipe:
                output_queue.put((name, line))
        finally:
            output_queue.put((name, None))

    @staticmethod
    def _event_message_text(event: dict[str, Any]) -> tuple[str | None, bool]:
        event_type = str(event.get('type') or event.get('method') or '')
        if event_type in {'item.delta', 'item.agent_message.delta', 'item/agentMessage/delta'}:
            delta = event.get('delta')
            if isinstance(delta, str):
                return delta, True
            if isinstance(delta, dict):
                text = delta.get('text') or delta.get('content')
                return (str(text), True) if text else (None, True)
            params = event.get('params')
            if isinstance(params, dict):
                text = params.get('delta') or params.get('text') or params.get('content')
                if isinstance(text, dict):
                    text = text.get('text') or text.get('content')
                return (str(text), True) if text else (None, True)
            return None, True
        if event_type in {'item.completed', 'item/completed'}:
            params = event.get('params') if isinstance(event.get('params'), dict) else {}
            item = event.get('item') or params.get('item')
            if isinstance(item, dict) and item.get('type') in {'agent_message', 'agentMessage'}:
                text = item.get('text')
                return (str(text), False) if text else (None, False)
        return None, False

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        prompt = self._build_prompt(request)
        output_file = tempfile.NamedTemporaryFile(prefix='aidm-codex-', suffix='.txt', delete=False)
        output_path = output_file.name
        output_file.close()
        try:
            completed = subprocess.run(
                self._command(output_path),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=self.workdir,
                env=self._env(),
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    'Codex CLI provider failed '
                    f'(exit {completed.returncode}): {self._error_preview(completed.stderr or completed.stdout)}'
                )
            text = Path(output_path).read_text(encoding='utf-8').strip()
            if not text:
                text = str(completed.stdout or '').strip()
            if not text:
                raise RuntimeError('Codex CLI provider returned an empty response')
            telemetry_metric(
                'llm.generate.success_total',
                1,
                tags={'provider': self.provider_name, 'model': self.display_model_name},
            )
            return ProviderResponse(text=text, provider=self.provider_name, model=self.display_model_name)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f'Codex CLI provider timed out after {self.timeout_seconds} seconds') from exc
        finally:
            try:
                Path(output_path).unlink(missing_ok=True)
            except OSError:
                pass

    def stream(self, request: ProviderRequest) -> Generator[str, None, None]:
        prompt = self._build_prompt(request)
        command = self._app_server_command()
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.workdir,
                env=self._env(),
            )
        except OSError as exc:
            raise RuntimeError(f'Codex app-server provider failed to start: {self._error_preview(str(exc))}') from exc

        output_queue: queue.Queue = queue.Queue()
        for name, pipe in (('stdout', process.stdout), ('stderr', process.stderr)):
            if pipe is not None:
                Thread(target=self._pipe_reader, args=(name, pipe, output_queue), daemon=True).start()

        stderr_parts: list[str] = []
        stdout_parts: list[str] = []
        accumulated_text = ''
        yielded_text = False
        deadline = time.monotonic() + self.timeout_seconds
        initialize_id = uuid4().hex
        thread_start_id = uuid4().hex
        turn_start_id = uuid4().hex
        thread_id: str | None = None
        initialized = False
        turn_started = False
        turn_completed = False

        try:
            self._send_app_server_message(
                process,
                {
                    'id': initialize_id,
                    'method': 'initialize',
                    'params': {
                        'clientInfo': {
                            'name': 'aidm-codex-provider',
                            'title': 'AIDM Codex Provider',
                            'version': '0.1.0',
                        },
                        'capabilities': {
                            'experimentalApi': True,
                            'optOutNotificationMethods': [
                                'command/exec/outputDelta',
                                'item/fileChange/outputDelta',
                                'item/plan/delta',
                                'item/reasoning/summaryTextDelta',
                                'item/reasoning/textDelta',
                            ],
                        },
                    },
                },
            )

            while not turn_completed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    raise RuntimeError(f'Codex app-server provider timed out after {self.timeout_seconds} seconds')
                try:
                    source, line = output_queue.get(timeout=min(0.25, remaining))
                except queue.Empty:
                    if process.poll() is not None:
                        preview = self._error_preview(''.join(stderr_parts) or '\n'.join(stdout_parts))
                        raise RuntimeError(f'Codex app-server provider exited before turn completion: {preview}')
                    continue
                if line is None:
                    if source == 'stdout' and not turn_completed:
                        preview = self._error_preview(''.join(stderr_parts) or '\n'.join(stdout_parts))
                        raise RuntimeError(f'Codex app-server provider closed stdout before turn completion: {preview}')
                    continue
                if source == 'stderr':
                    stderr_parts.append(str(line))
                    continue

                raw_line = str(line).strip()
                if not raw_line:
                    continue
                stdout_parts.append(raw_line)
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event.get('error'), dict):
                    error = event['error']
                    message = error.get('message') or error
                    raise RuntimeError(f'Codex app-server provider error: {message}')

                event_id = str(event.get('id') or '')
                result = event.get('result') if isinstance(event.get('result'), dict) else {}
                method = str(event.get('method') or '')

                if event_id == initialize_id and not initialized:
                    initialized = True
                    self._send_app_server_message(process, {'method': 'initialized', 'params': {}})
                    self._send_app_server_message(
                        process,
                        {
                            'id': thread_start_id,
                            'method': 'thread/start',
                            'params': {
                                'model': self.model_name,
                                'cwd': self.workdir,
                                'ephemeral': True,
                                'approvalPolicy': 'never',
                                'sandbox': 'read-only',
                            },
                        },
                    )
                    continue

                if event_id == thread_start_id and not thread_id:
                    thread = result.get('thread') if isinstance(result.get('thread'), dict) else {}
                    thread_id = str(thread.get('id') or '')
                    if not thread_id:
                        raise RuntimeError('Codex app-server provider did not return a thread id')
                    self._send_app_server_message(
                        process,
                        {
                            'id': turn_start_id,
                            'method': 'turn/start',
                            'params': {
                                'threadId': thread_id,
                                'input': [{'type': 'text', 'text': prompt}],
                                'model': self.model_name,
                                'effort': self.reasoning_effort,
                                'cwd': self.workdir,
                                'approvalPolicy': 'never',
                                'sandboxPolicy': {'type': 'readOnly', 'networkAccess': False},
                            },
                        },
                    )
                    continue

                if event_id == turn_start_id:
                    turn_started = True

                text, is_delta = self._event_message_text(event)
                if not text:
                    if method == 'turn/completed':
                        turn_completed = True
                    continue
                if is_delta:
                    accumulated_text += text
                    yielded_text = True
                    yield text
                elif not yielded_text:
                    accumulated_text = text
                    yielded_text = True
                    yield text
                elif text.startswith(accumulated_text):
                    suffix = text[len(accumulated_text) :]
                    if suffix:
                        accumulated_text = text
                        yield suffix

                if method == 'turn/completed':
                    turn_completed = True

            if not turn_started:
                preview = self._error_preview(''.join(stderr_parts) or '\n'.join(stdout_parts))
                raise RuntimeError(f'Codex app-server provider did not start a turn: {preview}')
            if not yielded_text:
                preview = self._error_preview(''.join(stderr_parts) or '\n'.join(stdout_parts))
                raise RuntimeError(f'Codex app-server provider returned no agent message events: {preview}')
            telemetry_metric(
                'llm.stream.success_total',
                1,
                tags={'provider': self.provider_name, 'model': self.display_model_name},
            )
        finally:
            if process.poll() is None:
                process.kill()


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
    'custom_race': 'codex_medium',
    'sentient_enemy_brain': 'codex_medium',
    'enemy_tactics_planner': 'codex_medium',
    'enemy_tactics_compiler': 'fast',
    'boss_tactics': 'codex_medium',
    'boss_tactics_planner': 'codex_medium',
    'creature_generation': 'codex_medium',
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
        if os.path.sep in executable:
            return Path(executable).is_file()
        return shutil.which(executable) is not None
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
            ignore_rules=_helper_bool(task, 'CODEX_IGNORE_RULES', True),
        )

    return DeterministicFallbackProvider(model_name='state-helper-fallback-v1')
