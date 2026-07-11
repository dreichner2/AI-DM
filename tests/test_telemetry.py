from __future__ import annotations

import importlib
import json
from queue import Queue

import pytest
import requests

from aidm_server.database import ensure_schema


class _FakeResponse:
    def __init__(self, status_code: int = 202, text: str = ''):
        self.status_code = status_code
        self.text = text


def _valid_preauth_payload(**extra):
    return {
        'action': 'account-legacy-claim',
        'dimension': 'target',
        'reset_in_seconds': 60,
        **extra,
    }


def _external_client(telemetry_module, *, api_key=None, environment='production', max_queue_size=8):
    return telemetry_module.TelemetryClient(
        enabled=True,
        endpoint='https://example.telemetry.test/ingest',
        api_key=api_key,
        timeout_seconds=1,
        max_queue_size=max_queue_size,
        environment=environment,
    )


def test_metrics_endpoint_exposes_counters(client):
    client.get('/api/health')
    response = client.get('/api/metrics')
    assert response.status_code == 200

    payload = response.get_json()
    counters = payload['counters']
    assert counters.get('system.health.requests_total', 0) >= 1
    assert payload['enabled'] is False
    assert payload['canon_queue'] == {
        'failed_count': 0,
        'oldest_queued_age_seconds': 0.0,
        'queued_count': 0,
        'running_count': 0,
    }


def test_prometheus_metrics_endpoint_exposes_counters_and_beta_gauges(client):
    client.get('/api/health')
    response = client.get('/api/metrics/prometheus')
    assert response.status_code == 200
    assert response.headers['Content-Type'].startswith('text/plain; version=0.0.4')

    body = response.get_data(as_text=True)
    assert '# TYPE aidm_telemetry_enabled gauge' in body
    assert 'aidm_system_health_requests_total' in body
    assert 'aidm_system_metrics_prometheus_requests_total 1' in body
    assert 'aidm_api_requests_total{method="GET",path="/api/health"}' in body
    assert 'aidm_beta_ai_failure_rate 0' in body
    assert 'aidm_canon_job_queue_depth 0' in body
    assert 'aidm_canon_job_oldest_queued_age_seconds 0' in body


def test_prometheus_text_sanitizes_metric_and_label_names():
    import aidm_server.telemetry as telemetry_module

    client = telemetry_module.TelemetryClient(
        enabled=False,
        endpoint=None,
        api_key=None,
        timeout_seconds=1,
        max_queue_size=8,
    )
    client.record_metric(
        'custom.metric-total',
        2,
        tags={'odd label': 'needs"escape\n'},
    )
    client.record_timing('provider.phase_ms', 12.5, tags={'provider': 'test'})

    body = client.prometheus_text()
    assert 'aidm_custom_metric_total{odd_label="needs\\"escape\\n"} 2' in body
    assert 'aidm_provider_phase_milliseconds_count{provider="test"} 1' in body
    assert 'aidm_provider_phase_milliseconds_sum{provider="test"} 12.5' in body


def test_external_telemetry_accepts_202_and_preserves_contract(tmp_path, monkeypatch):
    db_path = tmp_path / 'telemetry.db'

    monkeypatch.setenv('AIDM_DATABASE_URI', f'sqlite:///{db_path}')
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'true')
    monkeypatch.setenv('AIDM_ENV', 'test')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('AIDM_SOCKETIO_ASYNC_MODE', 'threading')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'true')
    monkeypatch.setenv('AIDM_TELEMETRY_ENDPOINT', 'https://example.telemetry.test/ingest')
    monkeypatch.setenv('AIDM_TELEMETRY_API_KEY', 'telemetry-secret')

    captured = {}

    def fake_post(url, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return _FakeResponse(202)

    import aidm_server.telemetry as telemetry_module
    monkeypatch.setattr(telemetry_module.requests, 'post', fake_post)

    import aidm_server.main as main_module
    main_module = importlib.reload(main_module)

    app = main_module.create_app()
    ensure_schema(app)
    with app.app_context():
        telemetry_client = telemetry_module.get_telemetry()
        telemetry_module.telemetry_event(
            'auth.preauth_rate_limited',
            payload=_valid_preauth_payload(),
            severity='warning',
        )
        telemetry_module.telemetry_metric('integration.metric', 3)
        assert telemetry_client is not None
        assert telemetry_client.flush(timeout_seconds=1.0) is True
        metrics = app.test_client().get('/api/metrics').get_json()
        telemetry_client.shutdown(timeout_seconds=1.0)

    assert captured['url'] == 'https://example.telemetry.test/ingest'
    assert captured['json'] == {
        'event': 'auth.preauth_rate_limited',
        'severity': 'warning',
        'payload': _valid_preauth_payload(),
        'ts': captured['json']['ts'],
        'service': 'ai-dm',
        'env': 'test',
    }
    assert captured['headers']['Authorization'] == 'Bearer telemetry-secret'
    assert metrics['enabled'] is True
    assert metrics['counters'].get('integration.metric', 0) == 3
    assert metrics['counters'].get('telemetry.external.sent', 0) == 1


def test_external_telemetry_strips_sensitive_extra_fields(monkeypatch):
    import aidm_server.telemetry as telemetry_module

    captured = {}

    def fake_post(url, json, headers, timeout):
        captured['json'] = json
        return _FakeResponse()

    monkeypatch.setattr(telemetry_module.requests, 'post', fake_post)
    client = _external_client(telemetry_module)
    sensitive_values = {
        'remote_addr': '203.0.113.42',
        'sid': 'socket-session-secret',
        'session_id': 'session-secret',
        'player_id': 'player-secret',
        'campaign_id': 'campaign-secret',
        'workspace_id': 'workspace-secret',
        'account_id': 'account-secret',
        'preview': 'private story preview',
        'error': 'provider error containing a secret',
        'request_id': 'caller-controlled-request-secret',
        'arbitrary': 'unreviewed-payload-value',
    }

    client.record_event(
        'auth.preauth_rate_limited',
        payload=_valid_preauth_payload(action='workspace-password', **sensitive_values),
        severity='warning',
    )
    assert client.flush(timeout_seconds=1.0) is True
    client.shutdown(timeout_seconds=1.0)

    assert captured['json']['payload'] == {
        'action': 'workspace-password',
        'dimension': 'target',
        'reset_in_seconds': 60,
    }
    serialized = json.dumps(captured['json'], sort_keys=True)
    assert all(value not in serialized for value in sensitive_values.values())
    assert client.snapshot()['counters'].get('telemetry.external.fields_dropped', 0) == len(sensitive_values)


def test_non_preauth_events_remain_local_and_are_counted_as_filtered(monkeypatch):
    import aidm_server.telemetry as telemetry_module

    post_calls = []
    monkeypatch.setattr(
        telemetry_module.requests,
        'post',
        lambda *args, **kwargs: post_calls.append((args, kwargs)) or _FakeResponse(),
    )
    client = _external_client(telemetry_module)

    client.record_event(
        'llm.query_gpt.failed',
        payload={'error': 'provider-secret', 'preview': 'private narration', 'client_ip': '192.0.2.14'},
        severity='error',
    )
    assert client.flush(timeout_seconds=1.0) is True
    client.shutdown(timeout_seconds=1.0)

    counters = client.snapshot()['counters']
    assert post_calls == []
    assert counters.get('events.total', 0) == 1
    assert counters.get('event.llm.query_gpt.failed', 0) == 1
    assert counters.get('telemetry.external.filtered', 0) == 1


@pytest.mark.parametrize(
    ('payload', 'severity'),
    [
        ({}, 'warning'),
        ({'action': 'account-login', 'dimension': 'target'}, 'warning'),
        (_valid_preauth_payload(action=['account-login']), 'warning'),
        (_valid_preauth_payload(dimension='account-secret'), 'warning'),
        (_valid_preauth_payload(reset_in_seconds=True), 'warning'),
        (_valid_preauth_payload(reset_in_seconds=0), 'warning'),
        (_valid_preauth_payload(), 'verbose'),
    ],
)
def test_preauth_event_rejects_missing_or_invalid_required_fields(monkeypatch, payload, severity):
    import aidm_server.telemetry as telemetry_module

    post_calls = []
    monkeypatch.setattr(
        telemetry_module.requests,
        'post',
        lambda *args, **kwargs: post_calls.append((args, kwargs)) or _FakeResponse(),
    )
    client = _external_client(telemetry_module)

    client.record_event('auth.preauth_rate_limited', payload=payload, severity=severity)
    assert client.flush(timeout_seconds=1.0) is True
    client.shutdown(timeout_seconds=1.0)

    assert post_calls == []
    assert client.snapshot()['counters'].get('telemetry.external.rejected', 0) == 1


@pytest.mark.parametrize(
    ('status_code', 'expected'),
    [(408, True), (425, True), (429, True), (500, True), (599, True), (403, False), (499, False)],
)
def test_external_telemetry_retryable_status_policy(status_code, expected):
    import aidm_server.telemetry as telemetry_module

    assert telemetry_module._retryable_external_status(status_code) is expected


def test_external_telemetry_retries_exceptions_and_5xx_then_succeeds(monkeypatch, caplog):
    import aidm_server.telemetry as telemetry_module

    monkeypatch.setattr(telemetry_module, '_EXTERNAL_RETRY_BACKOFF_SECONDS', 0)
    outcomes = [
        requests.ConnectionError('exception-secret'),
        _FakeResponse(503, text='response-body-secret'),
        _FakeResponse(202),
    ]
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers, timeout))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(telemetry_module.requests, 'post', fake_post)
    client = _external_client(telemetry_module, api_key='authorization-secret')

    client.record_event('auth.preauth_rate_limited', payload=_valid_preauth_payload(), severity='warning')
    assert client.flush(timeout_seconds=1.0) is True
    client.shutdown(timeout_seconds=1.0)

    counters = client.snapshot()['counters']
    assert len(calls) == 3
    assert counters.get('telemetry.external.retries', 0) == 2
    assert counters.get('telemetry.external.sent', 0) == 1
    assert counters.get('telemetry.external.failed', 0) == 0
    assert 'exception-secret' not in caplog.text
    assert 'response-body-secret' not in caplog.text
    assert 'authorization-secret' not in caplog.text


def test_external_telemetry_exhausts_bounded_retries(monkeypatch):
    import aidm_server.telemetry as telemetry_module

    monkeypatch.setattr(telemetry_module, '_EXTERNAL_RETRY_BACKOFF_SECONDS', 0)
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeResponse(503)

    monkeypatch.setattr(telemetry_module.requests, 'post', fake_post)
    client = _external_client(telemetry_module)

    client.record_event('auth.preauth_rate_limited', payload=_valid_preauth_payload(), severity='warning')
    assert client.flush(timeout_seconds=1.0) is True
    client.shutdown(timeout_seconds=1.0)

    counters = client.snapshot()['counters']
    assert len(calls) == 3
    assert counters.get('telemetry.external.retries', 0) == 2
    assert counters.get('telemetry.external.retry_exhausted', 0) == 1
    assert counters.get('telemetry.external.failed', 0) == 1
    assert counters.get('telemetry.external.sent', 0) == 0


def test_external_telemetry_does_not_retry_403_or_log_secrets(monkeypatch, caplog):
    import aidm_server.telemetry as telemetry_module

    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeResponse(403, text='response-body-secret')

    monkeypatch.setattr(telemetry_module.requests, 'post', fake_post)
    client = _external_client(telemetry_module, api_key='authorization-secret')

    client.record_event('auth.preauth_rate_limited', payload=_valid_preauth_payload(), severity='warning')
    assert client.flush(timeout_seconds=1.0) is True
    client.shutdown(timeout_seconds=1.0)

    counters = client.snapshot()['counters']
    assert len(calls) == 1
    assert counters.get('telemetry.external.retries', 0) == 0
    assert counters.get('telemetry.external.retry_exhausted', 0) == 0
    assert counters.get('telemetry.external.failed', 0) == 1
    assert 'response-body-secret' not in caplog.text
    assert 'authorization-secret' not in caplog.text


def test_external_telemetry_retry_backoff_is_stop_aware(monkeypatch):
    import aidm_server.telemetry as telemetry_module

    calls = []
    monkeypatch.setattr(
        telemetry_module.requests,
        'post',
        lambda *args, **kwargs: calls.append((args, kwargs)) or _FakeResponse(503),
    )
    client = _external_client(telemetry_module)
    client._stop_event.set()

    client._deliver_event(
        event_name='auth.preauth_rate_limited',
        event_body={'event': 'auth.preauth_rate_limited'},
        headers={},
    )

    counters = client.snapshot()['counters']
    assert len(calls) == 1
    assert counters.get('telemetry.external.retries', 0) == 0
    assert counters.get('telemetry.external.retry_cancelled', 0) == 1
    assert counters.get('telemetry.external.failed', 0) == 1


def test_external_telemetry_drops_events_when_queue_is_full(monkeypatch):
    import aidm_server.telemetry as telemetry_module

    monkeypatch.setattr(telemetry_module.Thread, 'start', lambda self: None)
    client = _external_client(telemetry_module, max_queue_size=1)
    client._delivery_queue = Queue(maxsize=1)
    client._delivery_queue.put(('held', {'event': 'held'}, {}))

    client.record_event(
        'auth.preauth_rate_limited',
        payload=_valid_preauth_payload(),
        severity='warning',
    )

    assert client.snapshot()['counters'].get('telemetry.external.dropped', 0) == 1
