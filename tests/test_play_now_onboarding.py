from __future__ import annotations

from contextlib import contextmanager
import json
from unittest.mock import Mock

from aidm_server.database import db
from aidm_server.auth import generate_account_token, hash_secret, password_hash_for
from aidm_server.models import (
    Account,
    AccountWorkspaceMembership,
    Campaign,
    Player,
    Session,
    SessionState,
    Workspace,
    World,
)
from aidm_server.rate_limiter import RateLimitResult
from aidm_server.services.campaign_pack import CampaignPackImportResult
from aidm_server.services.play_now import PlayNowOnboardingError, PlayNowOnboardingResult


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


def test_play_now_uses_existing_workspace_auth_when_auth_required(client, app, monkeypatch):
    import aidm_server.blueprints.onboarding as onboarding_blueprint

    app.config['AIDM_AUTH_REQUIRED'] = True
    app.config['AIDM_API_AUTH_TOKENS'] = ['owner-token']
    calls: list[dict] = []

    def fake_play_now(**kwargs):
        calls.append(kwargs)
        return PlayNowOnboardingResult(payload={'mode': 'play_now'}, status_code=201)

    monkeypatch.setattr(onboarding_blueprint, 'ensure_play_now_adventure', fake_play_now)

    unauthorized = client.post('/api/onboarding/play-now', json={})
    assert unauthorized.status_code == 401
    assert calls == []

    response = client.post(
        '/api/onboarding/play-now',
        headers={'Authorization': 'Bearer owner-token'},
        json={},
    )

    assert response.status_code == 201
    assert response.get_json()['mode'] == 'play_now'
    assert calls == [
        {
            'workspace_id': 'owner',
            'account_id': None,
            'character_id': None,
            'example_pack_id': None,
        }
    ]


def _enable_hosted_cookie_auth(app):
    app.config.update(
        AIDM_AUTH_REQUIRED=True,
        AIDM_ACCOUNT_COOKIE_AUTH_ENABLED=True,
        AIDM_ACCOUNT_COOKIE_SECURE=False,
        AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=False,
        AIDM_CORS_ALLOWLIST=['http://localhost'],
    )


def test_account_play_now_provisions_isolated_nonrecoverable_guests(client, app, monkeypatch):
    import aidm_server.blueprints.accounts as accounts_blueprint

    _enable_hosted_cookie_auth(app)
    calls: list[dict] = []

    def fake_play_now(**kwargs):
        calls.append(kwargs)
        return PlayNowOnboardingResult(
            payload={'mode': 'play_now', 'workspace_id': kwargs['workspace_id']},
            status_code=201,
        )

    monkeypatch.setattr(accounts_blueprint, 'ensure_play_now_adventure', fake_play_now)
    second_client = app.test_client()

    first = client.post('/api/accounts/play-now', json={})
    second = second_client.post('/api/accounts/play-now', json={})

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.headers['Cache-Control'] == 'no-store'
    assert second.headers['Cache-Control'] == 'no-store'
    first_payload = first.get_json()
    second_payload = second.get_json()
    assert first_payload['guest_account'] is True
    assert second_payload['guest_account'] is True
    assert first_payload['account_session']['account_token'] == ''
    assert first_payload['account_session']['account_token_transport'] == 'http_only_cookie'
    assert first_payload['account_session']['workspace_role'] == 'player'
    assert first_payload['account_session']['is_workspace_admin'] is False

    first_account_id = first_payload['account_session']['account']['account_id']
    second_account_id = second_payload['account_session']['account']['account_id']
    first_workspace_id = first_payload['workspace_id']
    second_workspace_id = second_payload['workspace_id']
    assert first_account_id != second_account_id
    assert first_workspace_id != second_workspace_id
    assert first_workspace_id == first_workspace_id.lower()
    assert second_workspace_id == second_workspace_id.lower()
    second_csrf_cookie = second_client.get_cookie('aidm_csrf_token')
    assert second_csrf_cookie is not None
    csrf_header_name = 'X-AIDM-CSRF-Token'
    cross_guest_join = second_client.post(
        '/api/accounts/workspace',
        headers={csrf_header_name: second_csrf_cookie.value},
        json={
            'table_name': first_payload['account_session']['workspaces'][0]['workspace_name'],
            'table_password': 'not-a-real-private-password',
        },
    )
    assert cross_guest_join.status_code == 401
    assert cross_guest_join.get_json()['error_code'] == 'unauthorized'
    assert calls == [
        {
            'workspace_id': first_workspace_id,
            'account_id': first_account_id,
            'character_id': None,
            'example_pack_id': None,
        },
        {
            'workspace_id': second_workspace_id,
            'account_id': second_account_id,
            'character_id': None,
            'example_pack_id': None,
        },
    ]

    with app.app_context():
        first_account = db.session.get(Account, first_account_id)
        second_account = db.session.get(Account, second_account_id)
        assert first_account.username.startswith(accounts_blueprint.GUEST_USERNAME_PREFIX)
        assert second_account.username.startswith(accounts_blueprint.GUEST_USERNAME_PREFIX)
        assert first_account.password_hash
        assert second_account.password_hash
        first_workspace = db.session.get(Workspace, first_workspace_id)
        second_workspace = db.session.get(Workspace, second_workspace_id)
        assert first_workspace.created_by_account_id == first_account_id
        assert second_workspace.created_by_account_id == second_account_id
        assert first_workspace.password_hash is None
        assert first_workspace.token_hash is None
        assert second_workspace.password_hash is None
        assert second_workspace.token_hash is None
        assert AccountWorkspaceMembership.query.filter_by(
            account_id=first_account_id,
            workspace_id=first_workspace_id,
            role='player',
        ).one()
        assert AccountWorkspaceMembership.query.filter_by(
            account_id=second_account_id,
            workspace_id=second_workspace_id,
            role='player',
        ).one()


def test_account_play_now_cookie_csrf_replay_preserves_guest_account_and_scope(client, app, monkeypatch):
    import aidm_server.blueprints.accounts as accounts_blueprint

    _enable_hosted_cookie_auth(app)
    calls: list[dict] = []

    def fake_play_now(**kwargs):
        calls.append(kwargs)
        return PlayNowOnboardingResult(
            payload={'mode': 'play_now', 'workspace_id': kwargs['workspace_id']},
            status_code=201 if len(calls) == 1 else 200,
        )

    monkeypatch.setattr(accounts_blueprint, 'ensure_play_now_adventure', fake_play_now)

    first = client.post(
        '/api/accounts/play-now',
        json={'character_id': 'liora-quill', 'example_pack_id': 'example.featured'},
    )
    first_payload = first.get_json()
    account_id = first_payload['account_session']['account']['account_id']
    workspace_id = first_payload['workspace_id']
    csrf_cookie = client.get_cookie('aidm_csrf_token')
    assert csrf_cookie is not None

    missing_csrf = client.post(
        '/api/accounts/play-now',
        headers={'X-AIDM-Workspace-Id': workspace_id},
        json={},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.get_json()['error_code'] == 'csrf_required'

    replay = client.post(
        '/api/accounts/play-now',
        headers={
            'X-AIDM-CSRF-Token': csrf_cookie.value,
            'X-AIDM-Workspace-Id': workspace_id,
        },
        json={'characterId': 'liora-quill', 'examplePackId': 'example.featured'},
    )

    assert replay.status_code == 200, replay.get_json()
    replay_payload = replay.get_json()
    assert replay_payload['guest_account'] is True
    assert replay_payload['workspace_id'] == workspace_id
    assert replay_payload['account_session']['account']['account_id'] == account_id
    assert replay_payload['account_session']['workspace_id'] == workspace_id
    assert calls[-1] == {
        'workspace_id': workspace_id,
        'account_id': account_id,
        'character_id': 'liora-quill',
        'example_pack_id': 'example.featured',
    }


def test_account_play_now_real_service_assigns_player_to_guest_and_replays(client, app):
    _enable_hosted_cookie_auth(app)

    first = client.post('/api/accounts/play-now', json={'character_id': 'tovan-ember'})

    assert first.status_code == 201
    first_payload = first.get_json()
    account_id = first_payload['account_session']['account']['account_id']
    workspace_id = first_payload['workspace_id']
    player_id = first_payload['player_id']
    csrf_cookie = client.get_cookie('aidm_csrf_token')
    assert csrf_cookie is not None

    with app.app_context():
        player = db.session.get(Player, player_id)
        assert player.account_id == account_id
        assert player.workspace_id == workspace_id
        assert player.character_name == 'Tovan Ember'

    replay = client.post(
        '/api/accounts/play-now',
        headers={
            'X-AIDM-CSRF-Token': csrf_cookie.value,
            'X-AIDM-Workspace-Id': workspace_id,
        },
        json={'character_id': 'tovan-ember'},
    )

    assert replay.status_code == 200
    replay_payload = replay.get_json()
    assert replay_payload['idempotent_replay'] is True
    assert replay_payload['account_session']['account']['account_id'] == account_id
    assert replay_payload['workspace_id'] == workspace_id
    assert replay_payload['player_id'] == player_id


def test_account_play_now_uses_only_an_authenticated_accounts_saved_workspace(client, app, monkeypatch):
    import aidm_server.blueprints.accounts as accounts_blueprint

    _enable_hosted_cookie_auth(app)
    account_token = generate_account_token()
    with app.app_context():
        account = Account(
            username='registered-player',
            first_name='Registered',
            last_name='Player',
            password_hash=password_hash_for('secret'),
            account_token_hash=hash_secret(account_token),
        )
        saved_workspace = Workspace(workspace_id='saved-table', name='Saved Table', name_key='saved table')
        other_workspace = Workspace(workspace_id='other-table', name='Other Table', name_key='other table')
        db.session.add_all((account, saved_workspace, other_workspace))
        db.session.flush()
        db.session.add(
            AccountWorkspaceMembership(
                account_id=account.account_id,
                workspace_id=saved_workspace.workspace_id,
                role='player',
            )
        )
        db.session.commit()
        account_id = account.account_id

    calls: list[dict] = []

    def fake_play_now(**kwargs):
        calls.append(kwargs)
        return PlayNowOnboardingResult(payload={'mode': 'play_now', 'workspace_id': kwargs['workspace_id']}, status_code=201)

    monkeypatch.setattr(accounts_blueprint, 'ensure_play_now_adventure', fake_play_now)
    headers = {'Authorization': f'Bearer {account_token}'}

    forbidden = client.post(
        '/api/accounts/play-now',
        headers={**headers, 'X-AIDM-Workspace-Id': 'other-table'},
        json={},
    )
    allowed = client.post(
        '/api/accounts/play-now',
        headers={**headers, 'X-AIDM-Workspace-Id': 'saved-table'},
        json={},
    )
    private_fallback = client.post('/api/accounts/play-now', headers=headers, json={})

    assert forbidden.status_code == 403
    assert forbidden.get_json()['error_code'] == 'workspace_not_saved'
    assert allowed.status_code == 201
    assert allowed.get_json()['guest_account'] is False
    assert allowed.get_json()['account_session']['account']['account_id'] == account_id
    assert private_fallback.status_code == 201
    private_payload = private_fallback.get_json()
    assert private_payload['workspace_id'] not in {'saved-table', 'other-table'}
    assert private_payload['account_session']['workspace_role'] == 'admin'
    assert private_payload['account_session']['is_workspace_admin'] is True
    assert calls == [
        {
            'workspace_id': 'saved-table',
            'account_id': account_id,
            'character_id': None,
            'example_pack_id': None,
        },
        {
            'workspace_id': private_payload['workspace_id'],
            'account_id': account_id,
            'character_id': None,
            'example_pack_id': None,
        },
    ]

    with app.app_context():
        private_workspace = db.session.get(Workspace, private_payload['workspace_id'])
        assert private_workspace.password_hash is None
        assert private_workspace.token_hash is None


def test_account_play_now_guest_bootstrap_rejects_cross_origin_and_skips_global_target_bucket(
    client,
    app,
    monkeypatch,
):
    import aidm_server.blueprints.accounts as accounts_blueprint

    _enable_hosted_cookie_auth(app)
    calls: list[dict] = []

    def fake_play_now(**kwargs):
        calls.append(kwargs)
        return PlayNowOnboardingResult(payload={'mode': 'play_now', 'workspace_id': kwargs['workspace_id']}, status_code=201)

    monkeypatch.setattr(accounts_blueprint, 'ensure_play_now_adventure', fake_play_now)
    target_allow = Mock(return_value=RateLimitResult(allowed=True, remaining=1, reset_in_seconds=60))
    monkeypatch.setattr(app.extensions['aidm_preauth_target_limiter'], 'allow', target_allow)

    rejected = client.post(
        '/api/accounts/play-now',
        headers={'Origin': 'https://attacker.example', 'Sec-Fetch-Site': 'cross-site'},
        json={},
    )
    allowed = client.post(
        '/api/accounts/play-now',
        headers={'Origin': 'http://localhost', 'Sec-Fetch-Site': 'same-origin'},
        json={},
    )

    assert rejected.status_code == 403
    assert rejected.get_json()['error_code'] == 'origin_forbidden'
    assert allowed.status_code == 201
    assert len(calls) == 1
    target_allow.assert_not_called()


def test_play_now_preset_reuse_is_scoped_to_authenticated_account(app):
    import aidm_server.services.play_now as play_now_service

    with app.app_context():
        workspace = Workspace(workspace_id='hosted', name='Hosted Table', name_key='hosted table')
        first_account = Account(username='first', first_name='First', last_name='Player')
        second_account = Account(username='second', first_name='Second', last_name='Player')
        db.session.add_all([workspace, first_account, second_account])
        db.session.flush()
        world = World(workspace_id=workspace.workspace_id, name='Hosted World')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(
            workspace_id=workspace.workspace_id,
            title='Hosted Adventure',
            world_id=world.world_id,
        )
        db.session.add(campaign)
        db.session.flush()
        preset = play_now_service.pregenerated_character_preset('arden-vale')
        assert preset is not None

        first_player, first_created = play_now_service._ensure_preset_player(
            workspace_id=workspace.workspace_id,
            campaign_id=campaign.campaign_id,
            account_id=first_account.account_id,
            preset=preset,
        )
        second_player, second_created = play_now_service._ensure_preset_player(
            workspace_id=workspace.workspace_id,
            campaign_id=campaign.campaign_id,
            account_id=second_account.account_id,
            preset=preset,
        )
        first_replay, replay_created = play_now_service._ensure_preset_player(
            workspace_id=workspace.workspace_id,
            campaign_id=campaign.campaign_id,
            account_id=first_account.account_id,
            preset=preset,
        )

        assert first_created is True
        assert second_created is True
        assert replay_created is False
        assert first_player.player_id != second_player.player_id
        assert first_player.account_id == first_account.account_id
        assert second_player.account_id == second_account.account_id
        assert first_replay.player_id == first_player.player_id


def test_play_now_returns_only_explicit_public_validation_message(client, monkeypatch):
    import aidm_server.blueprints.onboarding as onboarding_blueprint

    internal_detail = 'postgresql://internal-user:secret@database.internal/aidm'

    class HostilePlayNowError(PlayNowOnboardingError):
        def __str__(self):
            return internal_detail

    def fail_play_now(**kwargs):
        del kwargs
        raise HostilePlayNowError('The selected adventure is unavailable.', error_code='play_now_unavailable')

    monkeypatch.setattr(onboarding_blueprint, 'ensure_play_now_adventure', fail_play_now)

    response = client.post('/api/onboarding/play-now', json={})
    payload = response.get_json()

    assert response.status_code == 400
    assert payload['error_code'] == 'play_now_unavailable'
    assert payload['error'] == 'The selected adventure is unavailable.'
    assert internal_detail not in str(payload)


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
    coordinated_session_ids: list[int] = []
    real_serialized = play_now_service.session_turn_coordinator.serialized

    @contextmanager
    def tracking_serialized(session_id: int):
        coordinated_session_ids.append(session_id)
        with real_serialized(session_id) as wait_ms:
            yield wait_ms

    monkeypatch.setattr(play_now_service.session_turn_coordinator, 'serialized', tracking_serialized)

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
    # Initial creation is one uncommitted initialization transaction. Only the
    # idempotent replay targets a visible session and therefore takes its turn lock.
    assert coordinated_session_ids == [first_payload['session_id']]

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
