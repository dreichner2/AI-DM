from __future__ import annotations

import json

from aidm_server.database import db
from aidm_server.models import Campaign, Player, Session, SessionState, Workspace, World
from aidm_server.services.campaign_pack import CampaignPackImportResult


def test_pregenerated_character_library_exposes_four_playable_presets(client):
    response = client.get('/api/onboarding/pregenerated-characters')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['count'] == 4
    assert payload['default_character_id'] == 'arden-vale'
    assert {character['character_id'] for character in payload['characters']} == {
        'arden-vale',
        'liora-quill',
        'mara-fen',
        'tovan-ember',
    }
    assert all(character['profile_image'].startswith('/profile-icons/') for character in payload['characters'])
    assert all(character['inventory'] for character in payload['characters'])


def test_play_now_is_forbidden_when_auth_required(client, app):
    app.config['AIDM_AUTH_REQUIRED'] = True
    app.config['AIDM_API_AUTH_TOKENS'] = ['owner-token']

    response = client.post(
        '/api/onboarding/play-now',
        headers={'Authorization': 'Bearer owner-token'},
        json={},
    )

    assert response.status_code == 403
    assert response.get_json()['error_code'] == 'play_now_auth_required'


def test_play_now_creates_local_workspace_and_replays_idempotently(client, app, monkeypatch):
    import aidm_server.services.play_now as play_now_service

    import_calls: list[dict] = []

    def fake_get_example_campaign_pack(pack_id: str):
        assert pack_id == 'example.featured'
        return {
            'manifest': {
                'schemaVersion': '1',
                'packId': 'example.featured',
                'title': 'Example Featured Pack',
            },
            'source_filename': 'example_featured.json',
        }

    def fake_import_campaign_pack(payload, *, workspace_id, dry_run=False, imported_by_account_id=None):
        assert dry_run is False
        import_calls.append(payload)
        world = World(
            workspace_id=workspace_id,
            name='Featured World',
            description='A small world for Play Now tests.',
        )
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(
            workspace_id=workspace_id,
            title='Example Featured Pack',
            description='Imported through the existing campaign-pack service.',
            world_id=world.world_id,
            current_quest='Find the first clue',
            location='Lantern Post',
        )
        db.session.add(campaign)
        db.session.flush()
        session = Session(
            campaign_id=campaign.campaign_id,
            name='Play Now',
            state_snapshot=json.dumps(
                {
                    'schemaVersion': 1,
                    'sessionId': None,
                    'campaignId': campaign.campaign_id,
                    'currentScene': {
                        'locationId': 'lantern_post',
                        'name': 'Lantern Post',
                        'sceneType': 'exploration',
                        'dangerLevel': 1,
                        'combatState': 'none',
                        'description': 'Rain taps on the shutters.',
                        'activeNpcIds': [],
                        'activeQuestIds': [],
                    },
                    'playerCharacters': [],
                    'activePlayerIds': [],
                    'knownNpcs': [],
                    'quests': [],
                    'locations': [],
                    'combat': {'status': 'none', 'round': 1, 'participants': [], 'battlefield': {}, 'flags': {}},
                    'flags': {},
                    'campaignPack': {'packId': 'example_featured', 'title': 'Example Featured Pack'},
                    'stateChangeLedger': [],
                }
            ),
        )
        db.session.add(session)
        db.session.flush()
        db.session.add(
            SessionState(
                session_id=session.session_id,
                current_location='Lantern Post',
                current_quest='Find the first clue',
                rolling_summary='Imported pack opening.',
                active_segments=json.dumps([]),
                memory_snippets=json.dumps([]),
            )
        )
        return CampaignPackImportResult(
            payload={
                'imported': True,
                'pack_id': 'example_featured',
                'campaign_id': campaign.campaign_id,
                'session_id': session.session_id,
            }
        )

    monkeypatch.setattr(play_now_service, 'get_example_campaign_pack', fake_get_example_campaign_pack)
    monkeypatch.setattr(play_now_service, 'import_campaign_pack', fake_import_campaign_pack)

    first = client.post(
        '/api/onboarding/play-now',
        json={'example_pack_id': 'example.featured', 'character_id': 'mara-fen'},
    )
    second = client.post(
        '/api/onboarding/play-now',
        json={'example_pack_id': 'example.featured', 'character_id': 'mara-fen'},
    )

    assert first.status_code == 201
    assert second.status_code == 200
    first_payload = first.get_json()
    second_payload = second.get_json()
    assert first_payload['idempotent_replay'] is False
    assert second_payload['idempotent_replay'] is True
    assert first_payload['campaign_id'] == second_payload['campaign_id']
    assert first_payload['session_id'] == second_payload['session_id']
    assert first_payload['player_id'] == second_payload['player_id']
    assert first_payload['join_context']['socket']['payload'] == {
        'workspace_id': 'owner',
        'session_id': first_payload['session_id'],
        'player_id': first_payload['player_id'],
    }
    assert first_payload['join_context']['send_message']['payload']['campaign_id'] == first_payload['campaign_id']
    assert len(import_calls) == 1

    with app.app_context():
        assert db.session.get(Workspace, 'owner') is not None
        assert Campaign.query.count() == 1
        assert Session.query.count() == 1
        assert Player.query.count() == 1
        player = db.session.get(Player, first_payload['player_id'])
        assert player.character_name == 'Mara Fen'
        assert player.inventory
        stats = json.loads(player.stats)
        assert stats['metadata']['source'] == 'play_now'
        assert stats['metadata']['pregenId'] == 'mara-fen'

        session = db.session.get(Session, first_payload['session_id'])
        snapshot = json.loads(session.state_snapshot)
        assert snapshot['playNow']['source'] == 'play_now'
        assert snapshot['playNow']['examplePackId'] == 'example.featured'
        assert snapshot['flags']['playNow'] is True
        assert snapshot['activePlayerIds'] == [first_payload['player_id']]
        assert [actor['playerId'] for actor in snapshot['playerCharacters']] == [first_payload['player_id']]
