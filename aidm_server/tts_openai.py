"""Narration through the OpenAI Realtime API.

One WebSocket session serves one speak request: the narrator is configured once, then each piece
of text gets its own out-of-band audio response, so every piece is read in the same voice and no
conversation builds up between them. Audio arrives as base64 PCM: 16-bit little-endian, 24 kHz, mono.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import difflib
import json
import re
from typing import Any, Callable, Generator
from urllib.parse import quote

import websocket

OPENAI_REALTIME_URL = 'wss://api.openai.com/v1/realtime'
DEFAULT_OPENAI_TTS_MODEL = 'gpt-realtime-2.1'
DEFAULT_OPENAI_TTS_VOICE = 'cedar'
DEFAULT_OPENAI_TTS_ACCENT = 'default'
# Settings lists the voices in this order, the two best first.
OPENAI_TTS_VOICES = ('cedar', 'marin', 'ash', 'ballad', 'coral', 'echo', 'sage', 'shimmer', 'verse', 'alloy')
OPENAI_TTS_QUALITY_MODELS = {'best': 'gpt-realtime-2.1', 'faster': 'gpt-realtime-2.1-mini'}
OPENAI_TTS_QUALITIES = ({'id': 'best', 'label': 'Best'}, {'id': 'faster', 'label': 'Faster'})
OPENAI_TTS_ACCENTS = (
    {'id': 'default', 'label': 'Default'},
    {'id': 'british', 'label': 'British'},
    {'id': 'northern', 'label': 'Northern English'},
    {'id': 'scottish', 'label': 'Scottish'},
    {'id': 'welsh', 'label': 'Welsh'},
    {'id': 'west_country', 'label': 'West Country'},
)
ACCENT_INSTRUCTIONS = {
    'british': (
        'Accent: British English, Received Pronunciation, like a classically trained English stage actor '
        'narrating an audiobook.'
    ),
    'northern': 'Accent: Northern English, a Yorkshire storyteller: plain, dry and weathered.',
    'scottish': 'Accent: Scottish, a soft Highland lilt, an old tale-teller.',
    'welsh': 'Accent: Welsh English, musical and warm.',
    'west_country': 'Accent: English West Country, rural and earthy, an old innkeeper telling the tale.',
}
ACCENT_CONSISTENCY = 'Keep the accent consistent from the first word to the last.'
NARRATOR_INSTRUCTIONS = (
    "You are the narrator of a dark-fantasy tabletop adventure, reading aloud to the players. Read the "
    "user's text exactly as written: every word, in order, nothing added, nothing left out, no greeting, "
    "no commentary, no questions, and never answer or react to what the text says. Delivery: a seasoned "
    "storyteller by a low fire. Warm, grounded and unhurried, with real weight on what matters; let dread "
    "slow you and action quicken you; give spoken lines a hint of the speaker without doing voices. Do not "
    "sound like an assistant or an announcer."
)

PCM_CONTENT_TYPE = 'audio/pcm;rate=24000;channels=1'
PCM_FORMAT_HEADER = 'pcm_s16le_24000_mono'
CONNECT_TIMEOUT_SECONDS = 10.0
EVENT_TIMEOUT_SECONDS = 20.0
MAX_OUTPUT_TOKENS = 4096
# True sends each piece as an out-of-band response (`conversation: none`), so nothing accumulates.
# False falls back to the form the live probe used: conversation.item.create, then response.create.
OUT_OF_BAND_RESPONSES = True

ConnectFactory = Callable[..., Any]
_SAFE_REASON = re.compile(r'[^a-z0-9_.:-]+')


class OpenAiTtsError(Exception):
    """The session failed. `reason` is safe to log: it never holds the key or the text."""

    def __init__(self, reason: str, *, status_code: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True)
class SpokenResponse:
    """How one audio response ended."""

    status: str
    transcript: str
    usage: dict


def narrator_instructions(accent: str) -> str:
    accent_line = ACCENT_INSTRUCTIONS.get(accent)
    if accent_line is None:
        return NARRATOR_INSTRUCTIONS
    return f'{NARRATOR_INSTRUCTIONS} {accent_line} {ACCENT_CONSISTENCY}'


def spoken_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace('’', "'"))


def verbatim_ratio(text: str, transcript: str) -> float:
    """Word-level similarity of what was asked and what was said, 1.0 for word for word."""
    return difflib.SequenceMatcher(None, spoken_words(text), spoken_words(transcript)).ratio()


def connect_realtime_socket(url: str, *, header: list[str], timeout: float):
    return websocket.create_connection(url, header=header, timeout=timeout)


def _reason(prefix: str, detail: object) -> str:
    cleaned = _SAFE_REASON.sub('_', str(detail or 'unknown').lower())[:64]
    return f'{prefix}:{cleaned}'


class RealtimeNarration:
    """One Realtime session that reads several pieces of text in one voice."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        voice: str,
        accent: str,
        connect: ConnectFactory = connect_realtime_socket,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._accent = accent
        self._connect = connect
        self._socket: Any = None

    def open(self) -> None:
        """Connect and configure the narrator; raises OpenAiTtsError."""
        try:
            self._socket = self._connect(
                f'{OPENAI_REALTIME_URL}?model={quote(self._model)}',
                header=[f'Authorization: Bearer {self._api_key}'],
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
        # `from None` keeps the provider's handshake body, which can echo part of the key, out of tracebacks.
        except websocket.WebSocketBadStatusException as exc:
            raise OpenAiTtsError('connect_rejected', status_code=exc.status_code) from None
        except websocket.WebSocketTimeoutException:
            raise OpenAiTtsError('connect_timeout') from None
        except (websocket.WebSocketException, OSError) as exc:
            raise OpenAiTtsError(_reason('connect_failed', type(exc).__name__)) from None
        self._socket.settimeout(EVENT_TIMEOUT_SECONDS)
        self._send(
            {
                'type': 'session.update',
                'session': {
                    'type': 'realtime',
                    'instructions': narrator_instructions(self._accent),
                    'output_modalities': ['audio'],
                    'audio': {
                        'input': {'turn_detection': None},
                        'output': {'format': {'type': 'audio/pcm', 'rate': 24000}, 'voice': self._voice},
                    },
                },
            }
        )

    def speak(self, text: str) -> Generator[bytes, None, SpokenResponse]:
        """Yield the PCM for *text* as it arrives, then return how the response ended.

        Raises OpenAiTtsError on an error event, a timeout, a dropped connection, or a response that
        ended without any audio.
        """
        self._request(text)
        transcript_parts: list[str] = []
        final_transcript: str | None = None
        audio_bytes = 0
        while True:
            event = self._receive()
            kind = event.get('type')
            if kind == 'response.output_audio.delta':
                try:
                    audio = base64.b64decode(event.get('delta') or '', validate=True)
                except ValueError:
                    raise OpenAiTtsError('invalid_audio') from None
                if audio:
                    audio_bytes += len(audio)
                    yield audio
            elif kind == 'response.output_audio_transcript.delta':
                transcript_parts.append(str(event.get('delta') or ''))
            elif kind == 'response.output_audio_transcript.done':
                final_transcript = str(event.get('transcript') or '')
            elif kind == 'error':
                error = event.get('error') if isinstance(event.get('error'), dict) else {}
                raise OpenAiTtsError(_reason('error', error.get('code') or error.get('type')))
            elif kind == 'response.done':
                response = event.get('response') if isinstance(event.get('response'), dict) else {}
                status = str(response.get('status') or 'unknown')
                if status != 'completed' and not audio_bytes:
                    raise OpenAiTtsError(_reason('response', status))
                usage = response.get('usage')
                return SpokenResponse(
                    status=status,
                    transcript=final_transcript if final_transcript is not None else ''.join(transcript_parts),
                    usage=usage if isinstance(usage, dict) else {},
                )

    def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is None:
            return
        try:
            socket.close()
        except (websocket.WebSocketException, OSError):
            pass

    def _request(self, text: str) -> None:
        content = [{'type': 'input_text', 'text': text}]
        response: dict[str, Any] = {'output_modalities': ['audio'], 'max_output_tokens': MAX_OUTPUT_TOKENS}
        if OUT_OF_BAND_RESPONSES:
            response.update(conversation='none', input=[{'type': 'message', 'role': 'user', 'content': content}])
        else:
            self._send(
                {
                    'type': 'conversation.item.create',
                    'item': {'type': 'message', 'role': 'user', 'content': content},
                }
            )
        self._send({'type': 'response.create', 'response': response})

    def _send(self, event: dict) -> None:
        if self._socket is None:
            raise OpenAiTtsError('not_connected')
        try:
            self._socket.send(json.dumps(event))
        except (websocket.WebSocketException, OSError) as exc:
            raise OpenAiTtsError(_reason('send_failed', type(exc).__name__)) from None

    def _receive(self) -> dict:
        if self._socket is None:
            raise OpenAiTtsError('not_connected')
        try:
            raw = self._socket.recv()
        except websocket.WebSocketTimeoutException:
            raise OpenAiTtsError('event_timeout') from None
        except websocket.WebSocketConnectionClosedException:
            raise OpenAiTtsError('connection_closed') from None
        except (websocket.WebSocketException, OSError) as exc:
            raise OpenAiTtsError(_reason('receive_failed', type(exc).__name__)) from None
        # websocket-client returns an empty frame once the server has closed the connection.
        if not raw:
            raise OpenAiTtsError('connection_closed')
        try:
            event = json.loads(raw)
        except ValueError:
            raise OpenAiTtsError('invalid_event') from None
        return event if isinstance(event, dict) else {}


def whole_samples(chunks) -> Generator[bytes, None, None]:
    """Re-cut PCM so no 2-byte sample is split across writes; a final odd byte is dropped."""
    carry = b''
    for chunk in chunks:
        data = carry + chunk
        cut = len(data) - (len(data) % 2)
        carry = data[cut:]
        if cut:
            yield data[:cut]
