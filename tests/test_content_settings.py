from __future__ import annotations

import json

from aidm_server.database import db
from aidm_server.llm import query_dm_function_stream
from aidm_server.llm_context import build_dm_context
from aidm_server.models import Session, safe_json_loads
from tests.helpers import seed_world_campaign_player_session


def test_session_content_settings_endpoint_persists_normalized_snapshot(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.patch(
        f"/api/sessions/{ids['session_id']}/content-settings",
        json={
            'contentRating': 'mature',
            'toneTags': ['grim', 'unknown-tone', 'hopeful'],
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['settings']['content_rating'] == 'mature'
    assert payload['settings']['tone_tags'] == ['grim', 'hopeful']
    assert payload['state']['state_snapshot']['contentSettings']['contentRating'] == 'mature'

    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        snapshot = safe_json_loads(session.state_snapshot, {})
        assert snapshot['contentSettings']['contentRating'] == 'mature'
        assert snapshot['contentSettings']['toneTags'] == ['grim', 'hopeful']


def test_dm_context_includes_session_content_settings(client, app):
    ids = seed_world_campaign_player_session(app)
    client.patch(
        f"/api/sessions/{ids['session_id']}/content-settings",
        json={'content_rating': 'unrestricted', 'tone_tags': ['noir']},
    )

    with app.app_context():
        context = json.loads(
            build_dm_context(
                ids['world_id'],
                ids['campaign_id'],
                ids['session_id'],
                query_text='open the door',
            )
        )

    assert context['content_settings']['content_rating'] == 'unrestricted'
    assert context['content_settings']['tone_tags'] == ['noir']


def test_query_dm_stream_uses_content_settings_from_context(app, monkeypatch):
    captured = {}

    class _FakeProvider:
        def stream(self, request):
            captured['system_message'] = request.system_message
            yield 'The door opens.'

    monkeypatch.setattr('aidm_server.llm.get_provider', lambda: _FakeProvider())
    context = json.dumps(
        {
            'campaign': {'title': 'Smoke'},
            'content_settings': {
                'content_rating': 'mature',
                'tone_tags': ['grim'],
            },
        }
    )

    with app.app_context():
        chunks = list(query_dm_function_stream('open the door', context))

    assert chunks == ['The door opens.']
    assert 'Use mature adventure-fantasy boundaries' in captured['system_message']
    assert 'TONE TAGS' in captured['system_message']
    assert 'grim' in captured['system_message']
