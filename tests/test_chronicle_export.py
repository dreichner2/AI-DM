from __future__ import annotations

from aidm_server.database import db
from aidm_server.models import (
    CampaignPackCheckpointProgress,
    CampaignPackSession,
    DmTurn,
    Session,
    TurnEvent,
    safe_json_dumps,
)
from aidm_server.services.campaign_pack_progress import PROGRESS_CHANGED_EVENT
from tests.helpers import seed_world_campaign_player_session


def _add_turn(session_id: int, campaign_id: int, player_id: int, player_input: str, dm_output: str) -> DmTurn:
    turn = DmTurn(
        session_id=session_id,
        campaign_id=campaign_id,
        player_id=player_id,
        player_input=player_input,
        dm_output=dm_output,
        status='completed',
        outcome_status='resolved',
    )
    db.session.add(turn)
    db.session.flush()
    return turn


def test_session_chronicle_returns_self_contained_escaped_html(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        _add_turn(
            ids['session_id'],
            ids['campaign_id'],
            ids['player_id'],
            'I test the lantern.',
            'The lantern answers <script>alert("x")</script> & keeps glowing.',
        )
        db.session.commit()

    response = client.get(f"/api/sessions/{ids['session_id']}/chronicle")

    assert response.status_code == 200
    assert response.content_type == 'text/html; charset=utf-8'
    assert response.headers['Content-Disposition'].endswith('-session-1-chronicle.html"')
    html = response.get_data(as_text=True)
    lowered = html.lower()
    assert html.startswith('<!doctype html>')
    assert 'Test Campaign Chronicle' in html
    assert "Director's Commentary" in html
    assert 'Coverage: 1 turn across Session 1.' in html
    assert 'The lantern answers &lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt; &amp; keeps glowing.' in html
    assert '<script' not in lowered
    assert '<link' not in lowered
    assert ' src=' not in lowered
    assert ' href=' not in lowered
    assert '@import' not in lowered
    assert 'url(' not in lowered


def test_campaign_chronicle_uses_progress_turn_events_without_pack_catalog_fields(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        session.state_snapshot = safe_json_dumps(
            {
                'campaignPack': {
                    'packId': 'bleakmoor_intro',
                    'catalog': {
                        'npcs': [
                            {
                                'id': 'npc_secret_keeper',
                                'name': 'SECRET_CATALOG_FIELD',
                                'hiddenToPlayers': True,
                            }
                        ]
                    },
                }
            },
            {},
        )
        first_turn = _add_turn(
            ids['session_id'],
            ids['campaign_id'],
            ids['player_id'],
            'I approach the gate.',
            'The gate lanterns gutter in the rain.',
        )
        second_turn = _add_turn(
            ids['session_id'],
            ids['campaign_id'],
            ids['player_id'],
            'I follow the old road.',
            'The old road fog parts around the party.',
        )
        _add_turn(
            ids['session_id'],
            ids['campaign_id'],
            ids['player_id'],
            'I look toward the tower.',
            'A ruined watchtower rises past the reeds.',
        )
        pack_session = CampaignPackSession(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            workspace_id='owner',
            pack_id='bleakmoor_intro',
            pack_title='The Lanterns of Bleakmoor',
            active_checkpoint_id='cp_old_road',
            progress_revision=1,
        )
        db.session.add(pack_session)
        db.session.flush()
        db.session.add_all(
            [
                CampaignPackCheckpointProgress(
                    campaign_pack_session_id=pack_session.campaign_pack_session_id,
                    checkpoint_id='cp_gate',
                    title='Bleakmoor Gate',
                    status='completed',
                    sort_order=1,
                ),
                CampaignPackCheckpointProgress(
                    campaign_pack_session_id=pack_session.campaign_pack_session_id,
                    checkpoint_id='cp_old_road',
                    title='Old Road',
                    status='active',
                    sort_order=2,
                ),
            ]
        )
        db.session.add(
            TurnEvent(
                session_id=ids['session_id'],
                campaign_id=ids['campaign_id'],
                turn_id=second_turn.turn_id,
                event_type=PROGRESS_CHANGED_EVENT,
                payload_json=safe_json_dumps(
                    {
                        'type': PROGRESS_CHANGED_EVENT,
                        'action': 'advance',
                        'fromCheckpointId': 'cp_gate',
                        'toCheckpointId': 'cp_old_road',
                        'progressRevision': 1,
                        'reason': 'checkpoint_location_reached',
                    },
                    {},
                ),
            )
        )
        assert first_turn.turn_id < second_turn.turn_id
        db.session.commit()

    response = client.get(f"/api/campaigns/{ids['campaign_id']}/chronicle")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-chapter-source="campaign-pack-progress"' in html
    assert 'Chapter 1: Opening' in html
    assert 'Chapter 2: Old Road' in html
    assert 'Director track: this chapter begins at a campaign-pack progress marker' in html
    assert 'The old road fog parts around the party.' in html
    assert 'SECRET_CATALOG_FIELD' not in html
    assert 'hiddenToPlayers' not in html
    assert 'campaignPack' not in html


def test_campaign_chronicle_falls_back_to_session_boundary_chapters(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        first_session = db.session.get(Session, ids['session_id'])
        first_session.name = 'First Watch'
        second_session = Session(campaign_id=ids['campaign_id'], name='Second Watch')
        db.session.add(second_session)
        db.session.flush()
        _add_turn(
            first_session.session_id,
            ids['campaign_id'],
            ids['player_id'],
            'I light the beacon.',
            'The first beacon catches.',
        )
        _add_turn(
            second_session.session_id,
            ids['campaign_id'],
            ids['player_id'],
            'I guard the road.',
            'The second watch hears wheels in the fog.',
        )
        db.session.commit()

    response = client.get(f"/api/campaigns/{ids['campaign_id']}/chronicle")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert html.count('data-chapter-source="session-boundary"') == 2
    assert 'data-chapter-source="campaign-pack-progress"' not in html
    assert 'Chapter 1: First Watch' in html
    assert 'Chapter 2: Second Watch' in html
    assert 'The second watch hears wheels in the fog.' in html
