from __future__ import annotations

import threading

import aidm_server.blueprints.system as system_blueprint
import requests


def test_tts_config_reports_missing_key(client):
    response = client.get('/api/tts/config')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['provider'] == 'deepgram'
    assert payload['configured'] is False
    assert payload['model'] == 'aura-2-draco-en'
    assert payload['connect_timeout_seconds'] == 3.0
    assert payload['read_timeout_seconds'] == 60.0


def test_tts_speak_requires_api_key(client):
    response = client.post('/api/tts/speak', json={'text': 'The torches flicker.'})

    assert response.status_code == 503
    assert response.get_json()['error_code'] == 'tts_not_configured'


def test_tts_speak_rejects_oversized_raw_text_before_sanitizing(client, monkeypatch):
    def unexpected_sanitizer_call(value):
        raise AssertionError(f'oversized text reached sanitizer: {len(value)} characters')

    monkeypatch.setattr(system_blueprint, 'normalize_tts_text', unexpected_sanitizer_call)

    response = client.post(
        '/api/tts/speak',
        json={'text': '[x' * ((system_blueprint.TTS_MAX_INPUT_CHARS // 2) + 1)},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        'error': f'Text input must be {system_blueprint.TTS_MAX_INPUT_CHARS} characters or fewer.',
        'error_code': 'validation_error',
        'details': {'max_input_chars': system_blueprint.TTS_MAX_INPUT_CHARS},
    }


def test_tts_speak_does_not_expose_transport_exception_details(client, app, monkeypatch):
    app.config['AIDM_DEEPGRAM_API_KEY'] = 'test-key'
    internal_detail = 'proxy=corp.internal token=secret-upstream-token'

    def fail_request(*args, **kwargs):
        del args, kwargs
        raise requests.ConnectionError(internal_detail)

    monkeypatch.setattr(system_blueprint, '_deepgram_tts_request', fail_request)

    response = client.post('/api/tts/speak', json={'text': 'The torches flicker.'})
    payload = response.get_json()

    assert response.status_code == 502
    assert payload['error_code'] == 'tts_request_failed'
    assert payload['error'] == system_blueprint.TTS_REQUEST_FAILED_MESSAGE
    assert internal_detail not in str(payload)


def test_tts_speak_does_not_expose_upstream_error_body(client, app, monkeypatch):
    app.config['AIDM_DEEPGRAM_API_KEY'] = 'test-key'
    internal_detail = 'upstream credential=secret-upstream-token'

    class FakeFailureResponse:
        ok = False
        status_code = 503
        text = internal_detail

        def close(self):
            pass

    monkeypatch.setattr(
        system_blueprint,
        '_deepgram_tts_request',
        lambda *args, **kwargs: FakeFailureResponse(),
    )

    response = client.post('/api/tts/speak', json={'text': 'The torches flicker.'})
    payload = response.get_json()

    assert response.status_code == 502
    assert payload == {
        'error': system_blueprint.TTS_REQUEST_FAILED_MESSAGE,
        'error_code': 'tts_request_failed',
        'details': {'status_code': 503},
    }
    assert internal_detail not in str(payload)


def test_deepgram_tts_request_uses_shared_http_client_and_phase_timeout(app, monkeypatch):
    app.config['AIDM_DEEPGRAM_TTS_CONNECT_TIMEOUT_SECONDS'] = 1.5
    app.config['AIDM_DEEPGRAM_TTS_READ_TIMEOUT_SECONDS'] = 12
    captured = {}

    class FakeDeepgramResponse:
        ok = True
        status_code = 200
        text = ''
        headers = {'Content-Type': 'audio/mpeg'}

    def fake_post(client_name, url, **kwargs):
        captured['client_name'] = client_name
        captured['url'] = url
        captured['kwargs'] = kwargs
        return FakeDeepgramResponse()

    monkeypatch.setattr(system_blueprint, 'http_post', fake_post)

    with app.app_context():
        response = system_blueprint._deepgram_tts_request('key', 'aura-2-draco-en', 'Speak now.', stream=True)

    assert response.ok is True
    assert captured['client_name'] == 'deepgram_tts'
    assert captured['url'] == system_blueprint.DEEPGRAM_SPEAK_URL
    assert captured['kwargs']['timeout'] == (1.5, 12.0)
    assert captured['kwargs']['stream'] is True
    assert captured['kwargs']['json'] == {'text': 'Speak now.'}


def test_tts_speak_proxies_deepgram_audio(client, app, monkeypatch):
    app.config['AIDM_DEEPGRAM_API_KEY'] = 'test-key'
    app.config['AIDM_DEEPGRAM_TTS_MODEL'] = 'aura-2-draco-en'
    captured = {}

    class FakeDeepgramResponse:
        ok = True
        content = b'mp3-bytes'
        status_code = 200
        text = ''
        headers = {'Content-Type': 'audio/mpeg'}

        def iter_content(self, chunk_size=1024):
            del chunk_size
            yield self.content

        def close(self):
            pass

    def fake_tts_request(api_key, model, text, *, stream=False):
        captured['api_key'] = api_key
        captured['model'] = model
        captured['text'] = text
        captured['stream'] = stream
        return FakeDeepgramResponse()

    monkeypatch.setattr(system_blueprint, '_deepgram_tts_request', fake_tts_request)

    response = client.post('/api/tts/speak', json={'text': 'The torches flicker.'})

    assert response.status_code == 200
    assert response.data == b'mp3-bytes'
    assert response.mimetype == 'audio/mpeg'
    assert captured['api_key'] == 'test-key'
    assert captured['model'] == 'aura-2-draco-en'
    assert captured['text'] == 'The torches flicker.'
    assert captured['stream'] is True

    metrics = client.get('/api/metrics').get_json()
    phase_keys = [
        key
        for key in metrics['timings']
        if key.startswith('system.tts_phase_latency_ms|')
    ]
    assert any('phase=request' in key for key in phase_keys)
    assert any('phase=first_audio_byte' in key for key in phase_keys)


def test_tts_speak_strips_reasoning_tags_before_proxying(client, app, monkeypatch):
    app.config['AIDM_DEEPGRAM_API_KEY'] = 'test-key'
    captured = {}

    class FakeDeepgramResponse:
        ok = True
        content = b'audio'
        status_code = 200
        text = ''
        headers = {'Content-Type': 'audio/mpeg'}

        def iter_content(self, chunk_size=1024):
            del chunk_size
            yield self.content

        def close(self):
            pass

    def fake_tts_request(api_key, model, text, *, stream=False):
        del api_key, model, stream
        captured['text'] = text
        return FakeDeepgramResponse()

    monkeypatch.setattr(system_blueprint, '_deepgram_tts_request', fake_tts_request)

    response = client.post('/api/tts/speak', json={'text': 'Speak. <thought>hidden</thought> Continue.'})

    assert response.status_code == 200
    assert captured['text'] == 'Speak. Continue.'


def test_tts_speak_normalizes_markdown_before_proxying(client, app, monkeypatch):
    app.config['AIDM_DEEPGRAM_API_KEY'] = 'test-key'
    captured = {}

    class FakeDeepgramResponse:
        ok = True
        content = b'audio'
        status_code = 200
        text = ''
        headers = {'Content-Type': 'audio/mpeg'}

        def iter_content(self, chunk_size=1024):
            del chunk_size
            yield self.content

        def close(self):
            pass

    def fake_tts_request(api_key, model, text, *, stream=False):
        del api_key, model, stream
        captured['text'] = text
        return FakeDeepgramResponse()

    monkeypatch.setattr(system_blueprint, '_deepgram_tts_request', fake_tts_request)

    response = client.post(
        '/api/tts/speak',
        json={'text': '## Arrival\n- **Open** the [gate](https://example.test).\n`Carefully`.'},
    )

    assert response.status_code == 200
    assert captured['text'] == 'Arrival Open the gate. Carefully.'


def test_tts_speak_chunks_long_text(client, app, monkeypatch):
    app.config['AIDM_DEEPGRAM_API_KEY'] = 'test-key'
    app.config['AIDM_DEEPGRAM_TTS_MODEL'] = 'aura-2-draco-en'
    chunks_received = []

    class FakeDeepgramResponse:
        ok = True
        content = b'audio'
        status_code = 200
        text = ''
        headers = {'Content-Type': 'audio/mpeg'}

        def iter_content(self, chunk_size=1024):
            del chunk_size
            yield self.content

        def close(self):
            pass

    def fake_tts_request(api_key, model, text, *, stream=False):
        assert stream is True
        chunks_received.append(text)
        return FakeDeepgramResponse()

    monkeypatch.setattr(system_blueprint, '_deepgram_tts_request', fake_tts_request)

    # Build text longer than DEEPGRAM_CHUNK_LIMIT (2000 chars)
    long_text = 'The torches flicker in the ancient hall. ' * 60  # ~2400 chars
    response = client.post('/api/tts/speak', json={'text': long_text})

    assert response.status_code == 200
    assert response.data == b'audio' * len(system_blueprint._chunk_text_for_tts(long_text))
    assert int(response.headers['X-AIDM-TTS-Chunk-Count']) == len(chunks_received)
    assert int(response.headers['X-AIDM-TTS-First-Chunk-Chars']) <= system_blueprint.DEEPGRAM_FIRST_CHUNK_LIMIT
    assert len(chunks_received) >= 2
    for chunk in chunks_received:
        assert len(chunk) <= system_blueprint.DEEPGRAM_CHUNK_LIMIT
    assert response.data == b'audio' * len(chunks_received)


def test_tts_speak_prefetches_next_chunk_while_current_chunk_streams(client, app, monkeypatch):
    app.config['AIDM_DEEPGRAM_API_KEY'] = 'test-key'

    first_iter_started = threading.Event()
    second_request_started = threading.Event()
    lock = threading.Lock()
    calls = []

    class FakeDeepgramResponse:
        ok = True
        status_code = 200
        text = ''
        headers = {'Content-Type': 'audio/mpeg'}

        def __init__(self, label):
            self.label = label

        def iter_content(self, chunk_size=1024):
            del chunk_size
            if self.label == 'chunk-1':
                first_iter_started.set()
                assert second_request_started.wait(timeout=1)
            yield self.label.encode()

        def close(self):
            pass

    def fake_tts_request(api_key, model, text, *, stream=False):
        del api_key, model, text
        assert stream is True
        with lock:
            calls.append(stream)
            label = f'chunk-{len(calls)}'
            if len(calls) == 2:
                second_request_started.set()
        return FakeDeepgramResponse(label)

    monkeypatch.setattr(system_blueprint, '_deepgram_tts_request', fake_tts_request)
    long_text = 'The torches flicker in the ancient hall. ' * 60
    expected_chunks = system_blueprint._chunk_text_for_tts(long_text)

    response = client.post('/api/tts/speak', json={'text': long_text})

    assert response.status_code == 200
    assert first_iter_started.is_set()
    assert response.headers['X-AIDM-TTS-Prefetch'] == 'enabled'
    assert response.data == b''.join(f'chunk-{index}'.encode() for index in range(1, len(expected_chunks) + 1))
    assert len(calls) == len(expected_chunks)


def test_chunk_text_for_tts_short_text():
    result = system_blueprint._chunk_text_for_tts('Hello world.', max_chars=2000)
    assert result == ['Hello world.']


def test_chunk_text_for_tts_splits_on_sentence():
    text = 'First sentence. Second sentence. Third sentence.'
    result = system_blueprint._chunk_text_for_tts(text, max_chars=35)
    assert all(len(c) <= 35 for c in result)
    assert len(result) >= 2


def test_chunk_text_for_tts_uses_smaller_first_chunk_for_fast_start():
    text = ('First clause is intentionally short. ' * 20).strip()
    result = system_blueprint._chunk_text_for_tts(text)

    assert len(result) > 1
    assert len(result[0]) <= system_blueprint.DEEPGRAM_FIRST_CHUNK_LIMIT
    assert ''.join(chunk.replace(' ', '') for chunk in result) == text.replace(' ', '')


def test_tts_stream_alias_proxies_audio(client, app, monkeypatch):
    app.config['AIDM_DEEPGRAM_API_KEY'] = 'test-key'

    class FakeDeepgramResponse:
        ok = True
        content = b'stream-audio'
        status_code = 200
        text = ''
        headers = {'Content-Type': 'audio/mpeg'}

        def iter_content(self, chunk_size=1024):
            del chunk_size
            yield self.content

        def close(self):
            pass

    monkeypatch.setattr(system_blueprint, '_deepgram_tts_request', lambda *args, **kwargs: FakeDeepgramResponse())

    response = client.post('/api/tts/stream', json={'text': 'Fast narration.'})

    assert response.status_code == 200
    assert response.data == b'stream-audio'
    assert response.headers['X-AIDM-TTS-Provider'] == 'deepgram'


def test_tts_chunk_failure_after_first_chunk_is_observable(client, app, monkeypatch):
    app.config['AIDM_DEEPGRAM_API_KEY'] = 'test-key'

    class FakeOkResponse:
        ok = True
        status_code = 200
        text = ''
        headers = {'Content-Type': 'audio/mpeg'}

        def iter_content(self, chunk_size=1024):
            del chunk_size
            yield b'first'

        def close(self):
            pass

    class FakeFailureResponse:
        ok = False
        status_code = 503
        text = 'upstream busy'
        headers = {'Content-Type': 'audio/mpeg'}

        def iter_content(self, chunk_size=1024):
            del chunk_size
            yield b''

        def close(self):
            pass

    calls = {'count': 0}

    def fake_tts_request(api_key, model, text, *, stream=False):
        del api_key, model, text, stream
        calls['count'] += 1
        return FakeOkResponse() if calls['count'] == 1 else FakeFailureResponse()

    monkeypatch.setattr(system_blueprint, '_deepgram_tts_request', fake_tts_request)
    long_text = 'First sentence. ' * 80

    response = client.post('/api/tts/speak', json={'text': long_text})
    body = response.data
    metrics = client.get('/api/metrics').get_json()

    assert response.status_code == 200
    assert body == b'first'
    assert metrics['counters']['system.tts_speak.chunk_failures_total'] == 1


def test_llm_config_rejects_ambiguous_persist_boolean(client):
    response = client.patch(
        '/api/llm/config',
        json={'provider': 'fallback', 'model': 'deterministic-v1', 'persist': 'not-sure'},
    )

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'


def test_llm_config_rejects_persistent_writes_outside_local_env(client, app):
    app.config['AIDM_ENV'] = 'production'

    response = client.patch(
        '/api/llm/config',
        json={'provider': 'fallback', 'model': 'deterministic-v1', 'persist': True},
    )

    assert response.status_code == 403
    assert response.get_json()['error_code'] == 'llm_config_persist_disabled'
