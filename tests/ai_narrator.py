"""An AI narrator for socket-pipeline tests: recorded replies, the prompts it was sent, no network."""

from __future__ import annotations

import re

import requests

from aidm_server.contracts import ProviderResponse
from aidm_server.database import db
from aidm_server.models import DmTurn, Session, safe_json_loads

ENGINE_BRIEF_RE = re.compile(r'\nENGINE THIS TURN\n(?P<brief>.*?)\n\nRULES FOR THIS TURN', re.DOTALL)


class RecordedNarrator:
    """Answers each narration with the next recorded reply (``None``: a plain line) and keeps every prompt."""

    provider_name = 'deepseek'
    model_name = 'deepseek-v4-flash'

    def __init__(self) -> None:
        self.replies: list[str | None] = []
        self.prompts: list[str] = []

    def stream(self, request):
        self.prompts.append(request.prompt)
        reply = self.replies.pop(0) if self.replies else None
        yield reply or 'The rain keeps falling on the yard.'

    def generate(self, request):
        # Canon extraction after the turn; it finds nothing new.
        return ProviderResponse(text='{}', provider=self.provider_name, model=self.model_name)


class AiTable:
    """The Play Now adventure at the socket, narrated by a ``RecordedNarrator``."""

    def __init__(self, app, client, socketio, narrator: RecordedNarrator):
        self.app = app
        self.narrator = narrator
        response = client.post('/api/onboarding/play-now', json={})
        assert response.status_code == 201, response.get_data(as_text=True)
        self.ids = {key: response.get_json()[key] for key in ('session_id', 'campaign_id', 'player_id', 'workspace_id')}
        self.socket = socketio.test_client(app, flask_test_client=app.test_client())
        self.socket.emit(
            'join_session',
            {'session_id': self.ids['session_id'], 'player_id': self.ids['player_id'], 'workspace_id': self.ids['workspace_id']},
        )
        self.socket.get_received()

    def send(self, message: str, reply: str | None = None, **extra) -> list[dict]:
        self.narrator.replies.append(reply)
        self.socket.emit(
            'send_message',
            {
                'session_id': self.ids['session_id'],
                'campaign_id': self.ids['campaign_id'],
                'player_id': self.ids['player_id'],
                'message': message,
                **extra,
            },
        )
        received = self.socket.get_received()
        errors = [event['args'][0] for event in received if event['name'] == 'error']
        assert errors == [], errors
        return received

    def reply(self, message: str, reply: str | None = None, **extra) -> str:
        received = self.send(message, reply, **extra)
        return next(event['args'][0]['text'] for event in received if event['name'] == 'dm_response_end')

    def brief(self) -> str:
        match = ENGINE_BRIEF_RE.search(self.narrator.prompts[-1])
        assert match, self.narrator.prompts[-1][-3000:]
        return match.group('brief')

    def turn(self) -> DmTurn:
        with self.app.app_context():
            turn = DmTurn.query.filter_by(session_id=self.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
            db.session.expunge(turn)
            return turn

    def snapshot(self) -> dict:
        with self.app.app_context():
            return safe_json_loads(db.session.get(Session, self.ids['session_id']).state_snapshot, {})


def ai_narration(app, monkeypatch) -> RecordedNarrator:
    """Narration by a recorded AI narrator; every network or CLI model call fails the test."""

    def fail_network(*_args, **_kwargs):
        raise AssertionError('Tests must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    narrator = RecordedNarrator()
    monkeypatch.setattr('aidm_server.llm.get_provider', lambda: narrator)
    app.config.update(AIDM_LLM_PROVIDER=narrator.provider_name, AIDM_LLM_MODEL=narrator.model_name)
    # Object checks roll 15.
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 15)
    return narrator
