from __future__ import annotations

import base64
from collections import deque
import json
import logging

from flask import request
import pytest
from sqlalchemy import text
import websocket

import aidm_server.blueprints.system as system_blueprint
from aidm_server import tts_openai
from aidm_server.api_type_contract import API_TYPE_CONTRACT_BY_NAME
from aidm_server.config import load_config
from aidm_server.database import db

TEST_KEY = 'sk-test-narrator-key'


def audio_delta(data: bytes) -> dict:
    return {'type': 'response.output_audio.delta', 'delta': base64.b64encode(data).decode()}


def transcript_done(transcript: str) -> dict:
    return {'type': 'response.output_audio_transcript.done', 'transcript': transcript}


def response_done(status: str = 'completed', usage: dict | None = None) -> dict:
    return {
        'type': 'response.done',
        'response': {
            'status': status,
            'usage': usage
            or {
                'input_tokens': 260,
                'output_tokens': 1080,
                'output_token_details': {'audio_tokens': 820, 'text_tokens': 220, 'reasoning_tokens': 40},
            },
        },
    }


def spoken(text: str, *audio: bytes) -> list:
    """A response that reads *text* word for word."""
    return [*(audio_delta(chunk) for chunk in audio), transcript_done(text), response_done()]


class FakeRealtimeSocket:
    """Answers each response.create with the events `respond(text)` returns, in order."""

    def __init__(self, respond):
        self.respond = respond
        self.sent: list[dict] = []
        self.pending: deque = deque()
        self.timeout = None
        self.recv_calls = 0
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def send(self, raw):
        event = json.loads(raw)
        self.sent.append(event)
        if event['type'] == 'response.create':
            content = (event['response'].get('input') or [self._last_item()])[0]['content'][0]
            self.pending.extend(self.respond(content['text']))

    def recv(self):
        self.recv_calls += 1
        if not self.pending:
            raise websocket.WebSocketTimeoutException('no event')
        event = self.pending.popleft()
        if isinstance(event, BaseException):
            raise event
        return json.dumps(event)

    def close(self):
        self.closed = True

    def requested_texts(self) -> list[str]:
        return [
            event['response']['input'][0]['content'][0]['text']
            for event in self.sent
            if event['type'] == 'response.create'
        ]

    def _last_item(self):
        return next(event['item'] for event in reversed(self.sent) if event['type'] == 'conversation.item.create')


@pytest.fixture()
def realtime(app, monkeypatch):
    """OpenAI as the narrator voice, with every WebSocket connection served by a fake."""
    app.config['AIDM_OPENAI_API_KEY'] = TEST_KEY
    state = {'respond': lambda text: spoken(text, b'\x01\x02\x03\x04'), 'connections': [], 'sockets': []}

    def connect(url, *, header, timeout):
        state['connections'].append({'url': url, 'header': header, 'timeout': timeout})
        socket = FakeRealtimeSocket(state['respond'])
        state['sockets'].append(socket)
        return socket

    monkeypatch.setattr(tts_openai, 'connect_realtime_socket', connect)
    return state


def _assert_contract_payload(type_name: str, payload: dict) -> None:
    contract = API_TYPE_CONTRACT_BY_NAME[type_name]
    required = {field.name for field in contract.fields if not field.optional}
    assert required <= set(payload) <= {field.name for field in contract.fields}


def test_openai_is_the_voice_when_its_key_is_set_and_reports_its_choices(client, app, realtime):
    app.config['AIDM_DEEPGRAM_API_KEY'] = 'deepgram-key'

    payload = client.get('/api/tts/config').get_json()

    assert payload == {
        'provider': 'openai',
        'configured': True,
        'model': 'gpt-realtime-2.1',
        'voice': 'cedar',
        'accent': 'default',
        'audio_format': 'audio/pcm;rate=24000;channels=1',
        'voices': ['cedar', 'marin', 'ash', 'ballad', 'coral', 'echo', 'sage', 'shimmer', 'verse', 'alloy'],
        'qualities': [{'id': 'best', 'label': 'Best'}, {'id': 'faster', 'label': 'Faster'}],
        'accents': [
            {'id': 'default', 'label': 'Default'},
            {'id': 'british', 'label': 'British'},
            {'id': 'northern', 'label': 'Northern English'},
            {'id': 'scottish', 'label': 'Scottish'},
            {'id': 'welsh', 'label': 'Welsh'},
            {'id': 'west_country', 'label': 'West Country'},
        ],
        'connect_timeout_seconds': 10.0,
        'read_timeout_seconds': 20.0,
    }
    assert TEST_KEY not in json.dumps(payload)


def test_tts_config_payload_matches_its_contract_for_every_provider(client, app):
    _assert_contract_payload('TtsConfigResponse', client.get('/api/tts/config').get_json())
    app.config['AIDM_DEEPGRAM_API_KEY'] = 'deepgram-key'
    _assert_contract_payload('TtsConfigResponse', client.get('/api/tts/config').get_json())
    app.config['AIDM_OPENAI_API_KEY'] = TEST_KEY
    payload = client.get('/api/tts/config').get_json()
    assert payload['provider'] == 'openai'
    _assert_contract_payload('TtsConfigResponse', payload)
    for choice in payload['qualities'] + payload['accents']:
        _assert_contract_payload('TtsChoice', choice)


def test_server_defaults_for_model_voice_and_accent_are_reported(client, app, realtime):
    app.config.update(
        AIDM_OPENAI_TTS_MODEL='gpt-realtime-2.1-mini',
        AIDM_OPENAI_TTS_VOICE='marin',
        AIDM_OPENAI_TTS_ACCENT='welsh',
    )

    payload = client.get('/api/tts/config').get_json()

    assert (payload['model'], payload['voice'], payload['accent']) == ('gpt-realtime-2.1-mini', 'marin', 'welsh')


def test_openai_speak_streams_raw_pcm_cut_at_whole_samples(client, realtime):
    pcm = bytes(range(16))
    realtime['respond'] = lambda text: spoken(text, pcm[:3], pcm[3:8], pcm[8:9], pcm[9:16])

    response = client.post('/api/tts/speak', json={'text': 'The torches flicker.'}, buffered=False)
    writes = list(response.response)
    response.close()

    assert response.status_code == 200
    assert response.headers['Content-Type'] == 'audio/pcm;rate=24000;channels=1'
    assert response.headers['X-AIDM-Audio-Format'] == 'pcm_s16le_24000_mono'
    assert response.headers['X-AIDM-TTS-Provider'] == 'openai'
    assert response.headers['X-AIDM-TTS-Model'] == 'gpt-realtime-2.1'
    assert response.headers['X-AIDM-TTS-Voice'] == 'cedar'
    assert response.headers['Cache-Control'] == 'no-store'
    # 3 + 5 + 1 + 7 bytes arrive; every write holds whole 2-byte samples and nothing is lost.
    assert [len(write) for write in writes] == [2, 6, 8]
    assert b''.join(writes) == pcm

    [connection] = realtime['connections']
    assert connection == {
        'url': 'wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1',
        'header': [f'Authorization: Bearer {TEST_KEY}'],
        'timeout': 10.0,
    }
    [socket] = realtime['sockets']
    assert socket.timeout == 20.0
    assert socket.closed is True
    session_update, response_create = socket.sent
    assert session_update == {
        'type': 'session.update',
        'session': {
            'type': 'realtime',
            'instructions': tts_openai.NARRATOR_INSTRUCTIONS,
            'output_modalities': ['audio'],
            'audio': {
                'input': {'turn_detection': None},
                'output': {'format': {'type': 'audio/pcm', 'rate': 24000}, 'voice': 'cedar'},
            },
        },
    }
    assert response_create == {
        'type': 'response.create',
        'response': {
            'conversation': 'none',
            'output_modalities': ['audio'],
            'max_output_tokens': 4096,
            'input': [
                {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'The torches flicker.'}]}
            ],
        },
    }


def test_openai_speak_records_first_audio_latency_and_token_usage(client, realtime):
    assert client.post('/api/tts/speak', json={'text': 'The torches flicker.'}).data

    metrics = client.get('/api/metrics').get_json()
    first_audio = [
        key
        for key in metrics['timings']
        if key.startswith('system.tts_phase_latency_ms|') and 'phase=first_audio_byte' in key
    ]
    assert first_audio and all('model=gpt-realtime-2.1' in key for key in first_audio)
    tokens = {
        key.split('kind=')[1].split('|')[0].split(',')[0]: value
        for key, value in metrics['counters'].items()
        if key.startswith('system.tts_speak.usage_tokens_total|')
    }
    assert tokens == {'input': 260, 'output': 1080, 'audio': 820, 'text': 220, 'reasoning': 40}
    assert not any(key.startswith('system.tts_speak.verbatim_mismatch_total') for key in metrics['counters'])


def test_openai_speak_counts_a_transcript_that_strays_from_the_text_and_never_logs_it(client, realtime, caplog):
    text_to_read = 'The bell sounded at the third mile, though no tower stood there.'
    realtime['respond'] = lambda text: [
        audio_delta(b'\x00\x01'),
        transcript_done('Greetings, traveller! What would you like to do next?'),
        response_done(),
    ]

    with caplog.at_level(logging.WARNING):
        response = client.post('/api/tts/speak', json={'text': text_to_read})

    assert response.status_code == 200
    assert response.data == b'\x00\x01'
    counters = client.get('/api/metrics').get_json()['counters']
    assert counters['system.tts_speak.verbatim_mismatch_total|model=gpt-realtime-2.1'] == 1
    assert 'did not read the text verbatim' in caplog.text
    assert 'bell' not in caplog.text and 'Greetings' not in caplog.text and TEST_KEY not in caplog.text


def test_openai_speak_tolerates_small_differences_in_the_transcript(client, realtime):
    text_to_read = 'Mara steps closer, her lantern trembling just slightly in the cold rain tonight.'
    realtime['respond'] = lambda text: [
        audio_delta(b'\x00\x01'),
        transcript_done('Mara steps closer, her lantern trembling slightly in the cold rain tonight.'),
        response_done(),
    ]

    assert client.post('/api/tts/speak', json={'text': text_to_read}).data == b'\x00\x01'

    counters = client.get('/api/metrics').get_json()['counters']
    assert not any(key.startswith('system.tts_speak.verbatim_mismatch_total') for key in counters)


@pytest.mark.parametrize(
    'events',
    [
        [{'type': 'error', 'error': {'type': 'invalid_request_error', 'code': 'invalid_value', 'message': 'secret'}}],
        [response_done(status='failed')],
        [websocket.WebSocketTimeoutException('quiet')],
        [transcript_done(''), response_done()],
    ],
    ids=['error-event', 'refused', 'no-event-for-20s', 'no-audio'],
)
def test_openai_failure_before_audio_is_a_502_and_closes_the_session(client, realtime, events, caplog):
    realtime['respond'] = lambda text: list(events)

    with caplog.at_level(logging.WARNING):
        response = client.post('/api/tts/speak', json={'text': 'The torches flicker.'})

    assert response.status_code == 502
    assert response.get_json() == {
        'error': 'The narrator voice is temporarily unavailable. Please retry.',
        'error_code': 'tts_request_failed',
        'details': {},
    }
    assert realtime['sockets'][0].closed is True
    assert 'torches' not in caplog.text and 'secret' not in caplog.text


def test_openai_rejected_handshake_is_a_502_with_the_status_and_no_key(client, realtime, monkeypatch, caplog):
    def reject(url, *, header, timeout):
        del url, header, timeout
        raise websocket.WebSocketBadStatusException(
            'Handshake status %d %s -+-+- %s -+-+- %s', 401, 'Unauthorized', {}, f'Incorrect API key {TEST_KEY}'
        )

    monkeypatch.setattr(tts_openai, 'connect_realtime_socket', reject)

    with caplog.at_level(logging.WARNING):
        response = client.post('/api/tts/speak', json={'text': 'The torches flicker.'})

    assert response.status_code == 502
    assert response.get_json()['details'] == {'status_code': 401}
    assert TEST_KEY not in caplog.text
    assert 'connect_rejected' in caplog.text


def test_openai_failure_after_audio_ends_the_stream_and_is_logged(client, realtime, caplog):
    realtime['respond'] = lambda text: [
        audio_delta(b'\x01\x02\x03'),
        {'type': 'error', 'error': {'type': 'server_error'}},
    ]

    with caplog.at_level(logging.WARNING):
        response = client.post('/api/tts/speak', json={'text': 'The torches flicker.'})

    assert response.status_code == 200
    assert response.data == b'\x01\x02'
    assert realtime['sockets'][0].closed is True
    counters = client.get('/api/metrics').get_json()['counters']
    assert counters['system.tts_speak.chunk_failures_total'] == 1
    assert 'Narrator voice stopped mid-stream: reason=error:server_error' in caplog.text


def test_openai_reads_long_text_in_one_session_one_response_per_chunk(client, realtime):
    long_text = 'The torches flicker in the ancient hall. ' * 60
    expected_chunks = system_blueprint._chunk_text_for_tts(long_text.strip())
    realtime['respond'] = lambda text: spoken(text, f'<{len(text)}>'.encode())

    response = client.post('/api/tts/speak', json={'text': long_text})
    body = response.data

    assert response.status_code == 200
    assert len(expected_chunks) >= 2
    assert len(realtime['connections']) == 1
    [socket] = realtime['sockets']
    assert [event['type'] for event in socket.sent].count('session.update') == 1
    assert socket.requested_texts() == expected_chunks
    assert response.headers['X-AIDM-TTS-Chunk-Count'] == str(len(expected_chunks))
    assert body == b''.join(f'<{len(chunk)}>'.encode() for chunk in expected_chunks)


def test_client_disconnect_closes_the_realtime_session(client, realtime):
    realtime['respond'] = lambda text: [audio_delta(b'\x00\x01' * 512) for _ in range(50)] + spoken(text)

    response = client.post('/api/tts/speak', json={'text': 'The torches flicker.'}, buffered=False)
    stream = iter(response.response)
    first = next(stream)
    [socket] = realtime['sockets']
    assert first and socket.closed is False

    response.close()

    assert socket.closed is True
    assert socket.recv_calls < 50


def test_voice_quality_and_accent_choose_the_session(client, realtime):
    response = client.post(
        '/api/tts/stream',
        json={'text': 'The torches flicker.', 'voice': 'marin', 'quality': 'faster', 'accent': 'british'},
    )

    assert response.status_code == 200
    assert response.headers['X-AIDM-TTS-Model'] == 'gpt-realtime-2.1-mini'
    assert realtime['connections'][0]['url'].endswith('?model=gpt-realtime-2.1-mini')
    session = realtime['sockets'][0].sent[0]['session']
    assert session['audio']['output']['voice'] == 'marin'
    assert session['instructions'] == tts_openai.narrator_instructions('british')


def test_server_defaults_apply_when_a_request_names_no_choice(client, app, realtime):
    app.config.update(
        AIDM_OPENAI_TTS_MODEL='gpt-realtime-2.1-mini',
        AIDM_OPENAI_TTS_VOICE='sage',
        AIDM_OPENAI_TTS_ACCENT='scottish',
    )

    client.post('/api/tts/speak', json={'text': 'The torches flicker.'})

    assert realtime['connections'][0]['url'].endswith('?model=gpt-realtime-2.1-mini')
    session = realtime['sockets'][0].sent[0]['session']
    assert session['audio']['output']['voice'] == 'sage'
    assert session['instructions'] == tts_openai.narrator_instructions('scottish')


@pytest.mark.parametrize(
    ('field', 'value'),
    [('voice', 'darth'), ('quality', 'ultra'), ('accent', 'texan'), ('voice', 'Cedar'), ('accent', ['british'])],
)
def test_unknown_voice_quality_or_accent_is_a_400(client, realtime, field, value):
    response = client.post('/api/tts/speak', json={'text': 'The torches flicker.', field: value})

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'
    assert field in response.get_json()['error']
    assert realtime['connections'] == []


def test_openai_without_a_key_is_a_503(client, app, realtime):
    app.config['AIDM_OPENAI_API_KEY'] = None
    app.config['AIDM_TTS_PROVIDER'] = 'openai'

    config = client.get('/api/tts/config').get_json()
    response = client.post('/api/tts/speak', json={'text': 'The torches flicker.'})

    assert (config['provider'], config['configured']) == ('openai', False)
    assert response.status_code == 503
    assert response.get_json()['error'] == 'No narrator voice is set up on this server.'
    assert realtime['connections'] == []


def test_deepgram_still_narrates_when_chosen_over_an_openai_key(client, app, realtime, monkeypatch):
    app.config['AIDM_TTS_PROVIDER'] = 'deepgram'
    app.config['AIDM_DEEPGRAM_API_KEY'] = 'deepgram-key'

    class FakeDeepgramResponse:
        ok = True
        status_code = 200
        headers = {'Content-Type': 'audio/mpeg'}

        def iter_content(self, chunk_size=1024):
            del chunk_size
            yield b'mp3-bytes'

        def close(self):
            pass

    monkeypatch.setattr(system_blueprint, '_deepgram_tts_request', lambda *args, **kwargs: FakeDeepgramResponse())

    config = client.get('/api/tts/config').get_json()
    response = client.post('/api/tts/speak', json={'text': 'The torches flicker.', 'voice': 'marin'})

    assert config['provider'] == 'deepgram'
    assert response.status_code == 200
    assert response.mimetype == 'audio/mpeg'
    assert response.data == b'mp3-bytes'
    assert response.headers['X-AIDM-TTS-Provider'] == 'deepgram'
    assert realtime['connections'] == []


def test_openai_speak_releases_the_request_database_connection_before_connecting(client, app, realtime, monkeypatch):
    @app.before_request
    def open_read_transaction_before_tts():
        if request.path == '/api/tts/speak':
            db.session.execute(text('SELECT 1'))
            assert db.session().in_transaction() is True

    def connect(url, *, header, timeout):
        del url, header, timeout
        assert db.session.registry.has() is False
        socket = FakeRealtimeSocket(realtime['respond'])
        realtime['sockets'].append(socket)
        return socket

    monkeypatch.setattr(tts_openai, 'connect_realtime_socket', connect)

    response = client.post('/api/tts/speak', json={'text': 'Release the connection.'})

    assert response.status_code == 200
    assert response.data == b'\x01\x02\x03\x04'


@pytest.mark.parametrize(
    ('accent', 'line'),
    [
        (
            'british',
            'Accent: British English, Received Pronunciation, like a classically trained English stage actor '
            'narrating an audiobook.',
        ),
        ('northern', 'Accent: Northern English, a Yorkshire storyteller: plain, dry and weathered.'),
        ('scottish', 'Accent: Scottish, a soft Highland lilt, an old tale-teller.'),
        ('welsh', 'Accent: Welsh English, musical and warm.'),
        ('west_country', 'Accent: English West Country, rural and earthy, an old innkeeper telling the tale.'),
    ],
)
def test_an_accent_appends_one_sentence_and_the_consistency_line(accent, line):
    assert tts_openai.narrator_instructions(accent) == (
        f'{tts_openai.NARRATOR_INSTRUCTIONS} {line} Keep the accent consistent from the first word to the last.'
    )


def test_the_default_accent_appends_nothing():
    assert tts_openai.narrator_instructions('default') == tts_openai.NARRATOR_INSTRUCTIONS
    assert tts_openai.NARRATOR_INSTRUCTIONS.startswith('You are the narrator of a dark-fantasy tabletop adventure')
    assert tts_openai.NARRATOR_INSTRUCTIONS.endswith('Do not sound like an assistant or an announcer.')


def test_the_probe_request_form_stays_available(monkeypatch):
    monkeypatch.setattr(tts_openai, 'OUT_OF_BAND_RESPONSES', False)
    socket = FakeRealtimeSocket(lambda text: spoken(text, b'\x05\x06'))
    narration = tts_openai.RealtimeNarration(
        api_key=TEST_KEY, model='gpt-realtime-2.1', voice='cedar', accent='default', connect=lambda *a, **k: socket
    )

    narration.open()
    audio = list(narration.speak('Hold the line.'))
    narration.close()

    assert audio == [b'\x05\x06']
    assert [event['type'] for event in socket.sent] == ['session.update', 'conversation.item.create', 'response.create']
    assert socket.sent[1]['item']['content'] == [{'type': 'input_text', 'text': 'Hold the line.'}]
    assert socket.sent[2]['response'] == {'output_modalities': ['audio'], 'max_output_tokens': 4096}


def test_a_closed_connection_mid_response_is_a_typed_error():
    socket = FakeRealtimeSocket(lambda text: [audio_delta(b'\x00\x01'), websocket.WebSocketConnectionClosedException()])
    narration = tts_openai.RealtimeNarration(
        api_key=TEST_KEY, model='gpt-realtime-2.1', voice='cedar', accent='default', connect=lambda *a, **k: socket
    )
    narration.open()

    speech = narration.speak('Hold the line.')
    assert next(speech) == b'\x00\x01'
    with pytest.raises(tts_openai.OpenAiTtsError) as error:
        next(speech)

    assert error.value.reason == 'connection_closed'


def test_whole_samples_carries_odd_bytes_and_drops_a_final_half_sample():
    assert list(tts_openai.whole_samples([b'\x01', b'\x02\x03', b'', b'\x04\x05\x06', b'\x07'])) == [
        b'\x01\x02',
        b'\x03\x04\x05\x06',
    ]


def test_verbatim_ratio_compares_words_not_punctuation():
    spoken_text = "That wasn't Craner's writing, she says"
    assert tts_openai.verbatim_ratio('“That wasn’t Craner’s writing,” she says.', spoken_text) == 1.0
    assert tts_openai.verbatim_ratio('one two three four five six seven eight nine ten', 'one two three') < 0.9


NARRATOR_VOICE_ENV = (
    'AIDM_TTS_PROVIDER',
    'AIDM_OPENAI_API_KEY',
    'OPENAI_API_KEY',
    'AIDM_OPENAI_TTS_MODEL',
    'AIDM_OPENAI_TTS_VOICE',
    'AIDM_OPENAI_TTS_ACCENT',
)


def test_narrator_voice_settings_default_when_unset(monkeypatch):
    for name in NARRATOR_VOICE_ENV:
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert (config.tts_provider, config.openai_api_key) == (None, None)
    assert (config.openai_tts_model, config.openai_tts_voice, config.openai_tts_accent) == (
        'gpt-realtime-2.1',
        'cedar',
        'default',
    )


def test_narrator_voice_settings_load_from_the_environment(monkeypatch):
    monkeypatch.delenv('AIDM_OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', TEST_KEY)
    monkeypatch.setenv('AIDM_TTS_PROVIDER', 'Deepgram')
    monkeypatch.setenv('AIDM_OPENAI_TTS_MODEL', 'gpt-realtime-2.1-mini')
    monkeypatch.setenv('AIDM_OPENAI_TTS_VOICE', 'Marin')
    monkeypatch.setenv('AIDM_OPENAI_TTS_ACCENT', 'west_country')

    config = load_config()

    assert (config.tts_provider, config.openai_api_key) == ('deepgram', TEST_KEY)
    assert (config.openai_tts_model, config.openai_tts_voice, config.openai_tts_accent) == (
        'gpt-realtime-2.1-mini',
        'marin',
        'west_country',
    )


@pytest.mark.parametrize(
    ('name', 'value'),
    [('AIDM_TTS_PROVIDER', 'elevenlabs'), ('AIDM_OPENAI_TTS_VOICE', 'darth'), ('AIDM_OPENAI_TTS_ACCENT', 'texan')],
)
def test_an_unknown_narrator_voice_setting_stops_startup(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        load_config()


def test_the_app_carries_the_narrator_voice_settings(app_runtime, monkeypatch):
    monkeypatch.setenv('AIDM_OPENAI_API_KEY', TEST_KEY)
    monkeypatch.setenv('AIDM_TTS_PROVIDER', 'openai')
    monkeypatch.setenv('AIDM_OPENAI_TTS_MODEL', 'gpt-realtime-2.1-mini')
    monkeypatch.setenv('AIDM_OPENAI_TTS_VOICE', 'ash')
    monkeypatch.setenv('AIDM_OPENAI_TTS_ACCENT', 'northern')

    app = app_runtime['modules']['main'].create_app()

    assert {name: app.config[name] for name in NARRATOR_VOICE_ENV if name != 'OPENAI_API_KEY'} == {
        'AIDM_TTS_PROVIDER': 'openai',
        'AIDM_OPENAI_API_KEY': TEST_KEY,
        'AIDM_OPENAI_TTS_MODEL': 'gpt-realtime-2.1-mini',
        'AIDM_OPENAI_TTS_VOICE': 'ash',
        'AIDM_OPENAI_TTS_ACCENT': 'northern',
    }
