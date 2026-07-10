"""Telemetry client for local metrics and optional external event delivery."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
from queue import Empty, Full, Queue
import re
from threading import Event, Lock, Thread
import time
import logging

import requests
from flask import current_app, has_app_context


logger = logging.getLogger(__name__)
_PROMETHEUS_NAME_RE = re.compile(r'[^a-zA-Z0-9_:]')
_PROMETHEUS_LABEL_RE = re.compile(r'[^a-zA-Z0-9_]')
_EXTERNAL_EVENT_NAME = 'auth.preauth_rate_limited'
_EXTERNAL_EVENT_SEVERITIES = frozenset({'info', 'warning', 'error'})
_EXTERNAL_ENVIRONMENTS = frozenset({'local', 'development', 'test', 'staging', 'production'})
_EXTERNAL_PAYLOAD_FIELDS = frozenset({'action', 'dimension', 'reset_in_seconds'})
_PREAUTH_ACTIONS = frozenset(
    {
        'account-legacy-claim',
        'account-login',
        'workspace-password',
        'workspace-token',
    }
)
_PREAUTH_DIMENSIONS = frozenset({'ip-target', 'ip', 'target'})
_MAX_EXTERNAL_RESET_SECONDS = 24 * 60 * 60
_EXTERNAL_MAX_ATTEMPTS = 3
_EXTERNAL_RETRY_BACKOFF_SECONDS = 0.1
_EXTERNAL_RETRY_STATUS_CODES = frozenset({408, 425, 429})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _privacy_safe_preauth_payload(payload: object) -> tuple[dict[str, object] | None, int]:
    """Return the complete allowlist-only pre-auth event payload or reject it."""
    if not isinstance(payload, dict):
        return None, 0
    dropped_fields = len(set(payload).difference(_EXTERNAL_PAYLOAD_FIELDS))
    action = payload.get('action')
    dimension = payload.get('dimension')
    reset_in_seconds = payload.get('reset_in_seconds')
    if not (isinstance(action, str) and action in _PREAUTH_ACTIONS):
        return None, dropped_fields
    if not (isinstance(dimension, str) and dimension in _PREAUTH_DIMENSIONS):
        return None, dropped_fields
    if not (
        isinstance(reset_in_seconds, int)
        and not isinstance(reset_in_seconds, bool)
        and 1 <= reset_in_seconds <= _MAX_EXTERNAL_RESET_SECONDS
    ):
        return None, dropped_fields
    return {
        'action': action,
        'dimension': dimension,
        'reset_in_seconds': reset_in_seconds,
    }, dropped_fields


def _retryable_external_status(status_code: int) -> bool:
    return status_code in _EXTERNAL_RETRY_STATUS_CODES or 500 <= status_code < 600


def _prometheus_name(value: str) -> str:
    name = _PROMETHEUS_NAME_RE.sub('_', str(value or 'metric')).strip('_') or 'metric'
    if name[0].isdigit():
        name = f'_{name}'
    return name


def _prometheus_label_name(value: str) -> str:
    name = _PROMETHEUS_LABEL_RE.sub('_', str(value or 'label')).strip('_') or 'label'
    if name[0].isdigit():
        name = f'_{name}'
    return name


def _escape_prometheus_label_value(value: object) -> str:
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')


def _prometheus_value(value: object) -> str | None:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric_value):
        return None
    if numeric_value.is_integer():
        return str(int(numeric_value))
    return f'{numeric_value:.12g}'


def _split_metric_key(metric_key: str) -> tuple[str, dict[str, str]]:
    name, separator, raw_tags = str(metric_key).partition('|')
    labels: dict[str, str] = {}
    if separator:
        for raw_tag in raw_tags.split(','):
            label, tag_separator, value = raw_tag.partition('=')
            if tag_separator and label:
                labels[label] = value
    return name, labels


def _format_prometheus_labels(labels: dict[str, str] | None = None) -> str:
    if not labels:
        return ''
    formatted = [
        f'{_prometheus_label_name(label)}="{_escape_prometheus_label_value(value)}"'
        for label, value in sorted(labels.items())
    ]
    return '{' + ','.join(formatted) + '}'


def _timing_metric_base(name: str) -> str:
    normalized = _prometheus_name(name)
    if normalized.endswith('_ms'):
        return f'{normalized[:-3]}_milliseconds'
    return f'{normalized}_milliseconds'


def prometheus_text_from_snapshot(snapshot: dict, extra_gauges: dict[str, object] | None = None) -> str:
    """Render a telemetry snapshot using the Prometheus text exposition format."""
    lines: list[str] = []
    emitted_metadata: set[str] = set()

    def emit_metric(
        metric_name: str,
        value: object,
        *,
        labels: dict[str, str] | None = None,
        metric_type: str = 'gauge',
        help_text: str | None = None,
    ) -> None:
        rendered_value = _prometheus_value(value)
        if rendered_value is None:
            return
        full_name = f'aidm_{_prometheus_name(metric_name)}'
        if full_name not in emitted_metadata:
            if help_text:
                lines.append(f'# HELP {full_name} {help_text}')
            lines.append(f'# TYPE {full_name} {metric_type}')
            emitted_metadata.add(full_name)
        lines.append(f'{full_name}{_format_prometheus_labels(labels)} {rendered_value}')

    emit_metric(
        'telemetry_enabled',
        1 if snapshot.get('enabled') else 0,
        help_text='Whether outbound AIDM telemetry is enabled.',
    )
    emit_metric(
        'telemetry_external_endpoint_configured',
        1 if snapshot.get('external_endpoint_configured') else 0,
        help_text='Whether an external AIDM telemetry endpoint is configured.',
    )

    for raw_key, value in sorted((snapshot.get('counters') or {}).items()):
        metric_name, labels = _split_metric_key(raw_key)
        emit_metric(metric_name, value, labels=labels, metric_type='counter')

    for raw_key, timing in sorted((snapshot.get('timings') or {}).items()):
        metric_name, labels = _split_metric_key(raw_key)
        base_name = _timing_metric_base(metric_name)
        emit_metric(f'{base_name}_count', timing.get('count'), labels=labels, metric_type='counter')
        emit_metric(f'{base_name}_sum', timing.get('sum_ms'), labels=labels, metric_type='counter')
        emit_metric(f'{base_name}_avg', timing.get('avg_ms'), labels=labels)
        emit_metric(f'{base_name}_min', timing.get('min_ms'), labels=labels)
        emit_metric(f'{base_name}_max', timing.get('max_ms'), labels=labels)

    for metric_name, value in sorted((extra_gauges or {}).items()):
        emit_metric(metric_name, value)

    return '\n'.join(lines) + '\n'


class TelemetryClient:
    def __init__(
        self,
        enabled: bool,
        endpoint: str | None,
        api_key: str | None,
        timeout_seconds: int,
        max_queue_size: int,
        environment: str | None = None,
    ):
        self.enabled = bool(enabled)
        self.endpoint = (endpoint or '').strip()
        self.api_key = (api_key or '').strip() or None
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.max_queue_size = max(1, int(max_queue_size))
        self.environment = (environment or '').strip().lower()

        self._lock = Lock()
        self._counters = Counter()
        self._timings = defaultdict(lambda: {'count': 0, 'sum_ms': 0.0, 'min_ms': None, 'max_ms': None})
        self._stop_event = Event()
        self._delivery_queue: Queue | None = None
        self._delivery_thread: Thread | None = None
        if self.enabled and self.endpoint:
            self._delivery_queue = Queue(maxsize=self.max_queue_size)
            self._delivery_thread = Thread(target=self._delivery_loop, name='aidm-telemetry', daemon=True)
            self._delivery_thread.start()

    def record_metric(self, name: str, value: int = 1, tags: dict | None = None):
        key = self._metric_key(name, tags)
        with self._lock:
            self._counters[key] += value
            self._counters['metrics.total'] += value

    def record_timing(self, name: str, value_ms: float, tags: dict | None = None):
        key = self._metric_key(name, tags)
        value = float(value_ms)
        with self._lock:
            bucket = self._timings[key]
            bucket['count'] += 1
            bucket['sum_ms'] += value
            bucket['min_ms'] = value if bucket['min_ms'] is None else min(bucket['min_ms'], value)
            bucket['max_ms'] = value if bucket['max_ms'] is None else max(bucket['max_ms'], value)
            self._counters['timings.total'] += 1

    def record_event(self, event_name: str, payload: dict | None = None, severity: str = 'info'):
        payload = payload or {}
        self.record_metric('events.total', 1)
        self.record_metric(f'event.{event_name}', 1)

        if not (self.enabled and self.endpoint):
            return

        if event_name != _EXTERNAL_EVENT_NAME:
            self.record_metric('telemetry.external.filtered', 1)
            return

        safe_payload, dropped_fields = _privacy_safe_preauth_payload(payload)
        if dropped_fields:
            self.record_metric('telemetry.external.fields_dropped', dropped_fields)
        if (
            safe_payload is None
            or not isinstance(severity, str)
            or severity not in _EXTERNAL_EVENT_SEVERITIES
        ):
            self.record_metric('telemetry.external.rejected', 1)
            logger.warning('Telemetry event rejected by outbound privacy policy')
            return

        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

        event_body = {
            'event': event_name,
            'severity': severity,
            'payload': safe_payload,
            'ts': _utc_now_iso(),
            'service': 'ai-dm',
            'env': self.environment if self.environment in _EXTERNAL_ENVIRONMENTS else 'unknown',
        }

        if self._delivery_queue is None:
            self._deliver_event(event_name=event_name, event_body=event_body, headers=headers)
            return

        try:
            self._delivery_queue.put_nowait((event_name, event_body, headers))
        except Full:
            self.record_metric('telemetry.external.dropped', 1)
            logger.warning('Telemetry queue full; dropping event=%s', event_name)

    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            timings = {
                key: {
                    'count': value['count'],
                    'avg_ms': (value['sum_ms'] / value['count']) if value['count'] else 0.0,
                    'min_ms': value['min_ms'],
                    'max_ms': value['max_ms'],
                    'sum_ms': value['sum_ms'],
                }
                for key, value in self._timings.items()
            }

        return {
            'enabled': self.enabled,
            'external_endpoint_configured': bool(self.endpoint),
            'counters': counters,
            'timings': timings,
        }

    def prometheus_text(self, extra_gauges: dict[str, object] | None = None) -> str:
        return prometheus_text_from_snapshot(self.snapshot(), extra_gauges=extra_gauges)

    def flush(self, timeout_seconds: float | None = None) -> bool:
        if self._delivery_queue is None:
            return True

        deadline = None if timeout_seconds is None else (time.monotonic() + max(0.0, timeout_seconds))
        while self._delivery_queue.unfinished_tasks:
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True

    def shutdown(self, timeout_seconds: float | None = None):
        if self._delivery_queue is None or self._delivery_thread is None:
            return
        self._stop_event.set()
        self.flush(timeout_seconds=timeout_seconds)
        self._delivery_thread.join(timeout=timeout_seconds)

    @staticmethod
    def _metric_key(name: str, tags: dict | None = None) -> str:
        tags = tags or {}
        if not tags:
            return name
        ordered = ','.join(f'{k}={tags[k]}' for k in sorted(tags))
        return f'{name}|{ordered}'

    def _delivery_loop(self):
        assert self._delivery_queue is not None
        while not self._stop_event.is_set() or self._delivery_queue.unfinished_tasks:
            try:
                event_name, event_body, headers = self._delivery_queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                self._deliver_event(event_name=event_name, event_body=event_body, headers=headers)
            finally:
                self._delivery_queue.task_done()

    def _deliver_event(self, *, event_name: str, event_body: dict, headers: dict[str, str]):
        for attempt in range(1, _EXTERNAL_MAX_ATTEMPTS + 1):
            if attempt > 1:
                self.record_metric('telemetry.external.retries', 1)
            retryable = False
            try:
                response = requests.post(
                    self.endpoint,
                    json=event_body,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                status_code = int(getattr(response, 'status_code', 0) or 0)
                if 200 <= status_code < 300:
                    self.record_metric('telemetry.external.sent', 1)
                    return
                retryable = _retryable_external_status(status_code)
                logger.warning(
                    'Telemetry delivery failed for event=%s: status=%s attempt=%s/%s',
                    event_name,
                    status_code,
                    attempt,
                    _EXTERNAL_MAX_ATTEMPTS,
                )
            except Exception:
                retryable = True
                logger.warning(
                    'Telemetry delivery failed for event=%s: request exception attempt=%s/%s',
                    event_name,
                    attempt,
                    _EXTERNAL_MAX_ATTEMPTS,
                )

            if not retryable or attempt == _EXTERNAL_MAX_ATTEMPTS:
                self.record_metric('telemetry.external.failed', 1)
                if retryable:
                    self.record_metric('telemetry.external.retry_exhausted', 1)
                return

            backoff_seconds = _EXTERNAL_RETRY_BACKOFF_SECONDS * attempt
            if self._stop_event.wait(backoff_seconds):
                self.record_metric('telemetry.external.retry_cancelled', 1)
                self.record_metric('telemetry.external.failed', 1)
                return


def init_telemetry(app):
    app.extensions['aidm_telemetry'] = TelemetryClient(
        enabled=app.config.get('AIDM_TELEMETRY_ENABLED', False),
        endpoint=app.config.get('AIDM_TELEMETRY_ENDPOINT'),
        api_key=app.config.get('AIDM_TELEMETRY_API_KEY'),
        timeout_seconds=app.config.get('AIDM_TELEMETRY_TIMEOUT_SECONDS', 2),
        max_queue_size=app.config.get('AIDM_TELEMETRY_MAX_QUEUE_SIZE', 1000),
        environment=app.config.get('AIDM_ENV'),
    )


def get_telemetry() -> TelemetryClient | None:
    if not has_app_context():
        return None
    return current_app.extensions.get('aidm_telemetry')


def telemetry_metric(name: str, value: int = 1, tags: dict | None = None):
    client = get_telemetry()
    if client:
        client.record_metric(name, value=value, tags=tags)


def telemetry_timing(name: str, value_ms: float, tags: dict | None = None):
    client = get_telemetry()
    if client:
        client.record_timing(name, value_ms=value_ms, tags=tags)


def telemetry_event(event_name: str, payload: dict | None = None, severity: str = 'info'):
    client = get_telemetry()
    if client:
        client.record_event(event_name=event_name, payload=payload, severity=severity)
