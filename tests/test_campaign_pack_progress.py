from __future__ import annotations

import importlib

from aidm_server.auth import generate_account_token, hash_secret
from aidm_server.canon_jobs import _evaluate_state_segments_after_turn
from aidm_server.database import db
from aidm_server.models import (
    Account,
    AccountWorkspaceMembership,
    Campaign,
    CampaignPackCheckpointProgress,
    CampaignPackProgressEvent,
    CampaignPackSession,
    CampaignSegment,
    DmTurn,
    Session,
    TurnEvent,
    World,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.services.campaign_pack_progress import PROGRESS_CHANGED_EVENT
from aidm_server.services.campaign_pack_progress import control_campaign_pack_progress, update_campaign_pack_progress
from aidm_server.services.campaign_pack_storage import sync_campaign_pack_progress


def _build_auth_app(tmp_path, monkeypatch):
    db_path = tmp_path / 'campaign_pack_auth.db'
    monkeypatch.setenv('AIDM_DATABASE_URI', f'sqlite:///{db_path}')
    monkeypatch.setenv('AIDM_AUTO_CREATE_SCHEMA', 'true')
    monkeypatch.setenv('AIDM_AUTH_REQUIRED', 'true')
    monkeypatch.setenv('AIDM_ENV', 'test')
    monkeypatch.setenv('AIDM_DEBUG', 'false')
    monkeypatch.setenv('AIDM_SOCKETIO_ASYNC_MODE', 'threading')
    monkeypatch.setenv('AIDM_TELEMETRY_ENABLED', 'false')
    monkeypatch.setenv('AIDM_RATE_LIMIT_MAX_API_REQUESTS', '1000')
    monkeypatch.setenv('AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES', '1000')

    import aidm_server.main as main_module
    main_module = importlib.reload(main_module)
    app = main_module.create_app()
    with app.app_context():
        db.create_all()
    return app


def _account_headers(app, *, username: str, role: str):
    token = generate_account_token()
    with app.app_context():
        account = Account(
            username=username,
            first_name=username.title(),
            last_name='Tester',
            password_hash='test-password-hash',
            account_token_hash=hash_secret(token),
        )
        db.session.add(account)
        db.session.flush()
        db.session.add(AccountWorkspaceMembership(account_id=account.account_id, workspace_id='owner', role=role))
        db.session.commit()
    return {
        'Authorization': f'Bearer {token}',
        'X-AIDM-Workspace-Id': 'owner',
    }


def _seed_pack_session(app, snapshot: dict):
    with app.app_context():
        world = World(name='Pack Progress World', description='world')
        db.session.add(world)
        db.session.flush()

        campaign = Campaign(title='Pack Progress Campaign', world_id=world.world_id)
        db.session.add(campaign)
        db.session.flush()

        session = Session(campaign_id=campaign.campaign_id, state_snapshot=safe_json_dumps(snapshot, {}))
        db.session.add(session)
        db.session.commit()
        return {
            'world_id': world.world_id,
            'campaign_id': campaign.campaign_id,
            'session_id': session.session_id,
        }


def _pack_snapshot(
    *,
    location_id: str,
    checkpoints: list[dict],
    quests: list[dict] | None = None,
    flags: dict | None = None,
    combat: dict | None = None,
    pack_extra: dict | None = None,
):
    pack = {
        'packId': 'bleakmoor_intro',
        'title': 'The Lanterns of Bleakmoor',
        'checkpoints': checkpoints,
        'directorRules': {'offTrackPolicy': 'improvise_and_reconnect'},
    }
    if pack_extra:
        pack.update(pack_extra)
    return {
        'currentScene': {
            'locationId': location_id,
            'name': location_id.replace('_', ' ').title(),
            'activeQuestIds': ['q_missing_caravan'],
            'activeNpcIds': [],
        },
        'quests': quests or [],
        'locations': [
            {'id': 'bleakmoor_gate', 'name': 'Bleakmoor Gate', 'source': 'campaign_pack', 'packId': 'bleakmoor_intro'},
            {'id': 'old_road', 'name': 'Old Road', 'source': 'campaign_pack', 'packId': 'bleakmoor_intro'},
            {'id': 'watchtower_ruins', 'name': 'Watchtower Ruins', 'source': 'campaign_pack', 'packId': 'bleakmoor_intro'},
        ],
        'knownNpcs': [],
        'partyNpcs': [],
        'combat': combat or {'status': 'none', 'participants': [], 'flags': {}},
        'flags': flags or {},
        'campaignPack': pack,
    }


def test_pack_progress_completes_active_checkpoint_when_location_reached(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='old_road',
            flags={'campaignPackActiveCheckpointId': 'cp_old_road'},
            checkpoints=[
                {'id': 'cp_old_road', 'title': 'Reach the old road', 'locationIds': ['old_road'], 'nextCheckpointIds': ['cp_watchtower']},
                {'id': 'cp_watchtower', 'title': 'Reach the watchtower', 'locationIds': ['watchtower_ruins']},
            ],
        ),
    )

    with app.app_context():
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
        event = TurnEvent.query.filter_by(session_id=ids['session_id'], event_type=PROGRESS_CHANGED_EVENT).one()
        event_payload = safe_json_loads(event.payload_json, {})
        pack_session = CampaignPackSession.query.filter_by(session_id=ids['session_id']).one()
        durable_event = CampaignPackProgressEvent.query.filter_by(
            campaign_pack_session_id=pack_session.campaign_pack_session_id
        ).one()
        durable_progress = {
            row.checkpoint_id: row.status
            for row in CampaignPackCheckpointProgress.query.filter_by(
                campaign_pack_session_id=pack_session.campaign_pack_session_id
            ).all()
        }

    assert result.changed is True
    assert result.reason == 'checkpoint_location_reached'
    assert result.completed_checkpoint_ids == ['cp_old_road']
    assert result.active_checkpoint_id == 'cp_watchtower'
    assert snapshot['flags']['campaignPackCompletedCheckpointIds'] == ['cp_old_road']
    assert snapshot['flags']['campaignPackActiveCheckpointId'] == 'cp_watchtower'
    assert snapshot['campaignPack']['activeCheckpointId'] == 'cp_watchtower'
    assert snapshot['campaignPack']['progressRevision'] == 1
    assert event_payload['action'] == 'auto_progress'
    assert event_payload['fromCheckpointId'] == 'cp_old_road'
    assert event_payload['toCheckpointId'] == 'cp_watchtower'
    assert event_payload['progressRevision'] == 1
    assert pack_session.active_checkpoint_id == 'cp_watchtower'
    assert pack_session.progress_revision == 1
    assert durable_event.turn_event_id == event.event_id
    assert durable_event.action == 'auto_progress'
    assert durable_event.to_checkpoint_id == 'cp_watchtower'
    assert durable_progress == {'cp_old_road': 'completed', 'cp_watchtower': 'active'}


def test_pack_progress_migrates_legacy_campaign_pack_snapshot_without_advancing(app):
    ids = _seed_pack_session(
        app,
        {
            'currentScene': {
                'locationId': 'bleakmoor_gate',
                'name': 'Bleakmoor Gate',
                'activeQuestIds': [],
                'activeNpcIds': [],
            },
            'quests': [],
            'locations': [
                {'id': 'bleakmoor_gate', 'name': 'Bleakmoor Gate', 'source': 'campaign_pack', 'packId': 'legacy_pack'}
            ],
            'knownNpcs': [],
            'partyNpcs': [],
            'combat': {'status': 'none', 'participants': [], 'flags': {}},
            'flags': {'campaignPackActiveCheckpointId': 'cp_gate'},
            'campaignPack': {
                'packId': 'legacy_pack',
                'title': 'Legacy Pack',
                'currentCheckpointId': 'cp_gate',
                'checkpoints': [
                    {
                        'id': 'cp_gate',
                        'title': 'Question the gate captain',
                        'objectiveIds': ['obj_question_gate'],
                    }
                ],
                'encounters': [{'id': 'enc_gate', 'title': 'Gate Trouble'}],
            },
        },
    )

    with app.app_context():
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
        progress_event_count = TurnEvent.query.filter_by(
            session_id=ids['session_id'],
            event_type=PROGRESS_CHANGED_EVENT,
        ).count()

    assert result.changed is False
    assert result.progress_revision == 0
    assert snapshot['campaignPack']['snapshotSchemaVersion'] == 1
    assert snapshot['campaignPack']['progressSchemaVersion'] == 1
    assert snapshot['campaignPack']['progressEventsVersion'] == 1
    assert snapshot['campaignPack']['progressRevision'] == 0
    assert snapshot['campaignPack']['activeCheckpointId'] == 'cp_gate'
    assert 'currentCheckpointId' not in snapshot['campaignPack']
    assert snapshot['campaignPack']['completedCheckpointIds'] == []
    assert snapshot['campaignPack']['skippedCheckpointIds'] == []
    assert snapshot['campaignPack']['failedCheckpointIds'] == []
    assert snapshot['campaignPack']['catalog']['encounters'][0]['id'] == 'enc_gate'
    assert snapshot['flags']['campaignPackProgressRevision'] == 0
    assert 'campaign_pack.snapshot_schema_v1' in snapshot['campaignPack']['migrationsApplied']
    assert progress_event_count == 0


def test_pack_progress_propagates_across_shared_multi_session_group(app):
    checkpoints = [
        {'id': 'cp_old_road', 'title': 'Reach the old road', 'nextCheckpointIds': ['cp_watchtower']},
        {'id': 'cp_watchtower', 'title': 'Reach the watchtower', 'terminal': True},
    ]
    first_ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='bleakmoor_gate',
            flags={'campaignPackActiveCheckpointId': 'cp_old_road'},
            checkpoints=checkpoints,
            pack_extra={'multiSessionGroupKey': 'shared-west-marches'},
        ),
    )
    second_ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='bleakmoor_gate',
            flags={'campaignPackActiveCheckpointId': 'cp_old_road'},
            checkpoints=checkpoints,
            pack_extra={'multiSessionGroupKey': 'shared-west-marches'},
        ),
    )

    with app.app_context():
        for session_id in (first_ids['session_id'], second_ids['session_id']):
            session_obj = db.session.get(Session, session_id)
            snapshot = safe_json_loads(session_obj.state_snapshot, {})
            pack = snapshot['campaignPack']
            sync_campaign_pack_progress(
                session=session_obj,
                pack=pack,
                checkpoints=pack['checkpoints'],
                active_checkpoint_id='cp_old_road',
                completed_ids=[],
                skipped_ids=[],
                failed_ids=[],
                progress_revision=0,
            )
        db.session.commit()

        result = control_campaign_pack_progress(
            session_id=first_ids['session_id'],
            action='advance',
            actor='test-operator',
        )
        db.session.commit()
        second_snapshot = safe_json_loads(db.session.get(Session, second_ids['session_id']).state_snapshot, {})
        second_progress = CampaignPackSession.query.filter_by(session_id=second_ids['session_id']).one()

    assert result.changed is True
    assert result.active_checkpoint_id == 'cp_watchtower'
    assert second_snapshot['campaignPack']['activeCheckpointId'] == 'cp_watchtower'
    assert second_snapshot['campaignPack']['completedCheckpointIds'] == ['cp_old_road']
    assert second_snapshot['flags']['campaignPackSharedProgressSourceSessionId'] == first_ids['session_id']
    assert second_progress.active_checkpoint_id == 'cp_watchtower'
    assert second_progress.progress_revision == 1


def test_pack_progress_explicit_complete_when_does_not_fall_back_to_context_fields(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='bleakmoor_gate',
            flags={'campaignPackActiveCheckpointId': 'cp_question_veyra'},
            quests=[
                {
                    'id': 'q_missing_caravan',
                    'title': 'Find the Missing Caravan',
                    'status': 'active',
                    'objectives': [
                        {'id': 'obj_question_veyra', 'description': 'Question Captain Veyra.', 'status': 'open'}
                    ],
                    'source': 'campaign_pack',
                    'packId': 'bleakmoor_intro',
                }
            ],
            checkpoints=[
                {
                    'id': 'cp_question_veyra',
                    'title': 'Question Captain Veyra',
                    'locationIds': ['bleakmoor_gate'],
                    'questIds': ['q_missing_caravan'],
                    'objectiveIds': ['obj_question_veyra'],
                    'segmentIds': ['seg_question_veyra'],
                    'completeWhen': {'objectiveIds': ['obj_question_veyra']},
                    'nextCheckpointIds': ['cp_old_road'],
                },
                {'id': 'cp_old_road', 'title': 'Reach the old road', 'locationIds': ['old_road']},
            ],
        ),
    )

    with app.app_context():
        db.session.add(
            CampaignSegment(
                campaign_id=ids['campaign_id'],
                title='Question Captain Veyra',
                trigger_condition=safe_json_dumps({'type': 'manual', 'packSegmentId': 'seg_question_veyra'}, {}),
                tags='campaign_pack,pack:bleakmoor_intro,pack_segment:seg_question_veyra',
                is_triggered=True,
            )
        )
        db.session.commit()
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})

    assert result.reason is None
    assert result.completed_checkpoint_ids == []
    assert result.active_checkpoint_id == 'cp_question_veyra'
    assert snapshot['flags']['campaignPackCompletedCheckpointIds'] == []
    assert snapshot['flags']['campaignPackActiveCheckpointId'] == 'cp_question_veyra'


def test_pack_progress_location_context_does_not_complete_objective_checkpoint(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='old_road',
            flags={'campaignPackActiveCheckpointId': 'cp_old_road'},
            quests=[
                {
                    'id': 'q_missing_caravan',
                    'title': 'Find the Missing Caravan',
                    'status': 'active',
                    'objectives': [
                        {'id': 'obj_find_wreck', 'description': 'Find the caravan wreck.', 'status': 'open'}
                    ],
                    'source': 'campaign_pack',
                    'packId': 'bleakmoor_intro',
                }
            ],
            checkpoints=[
                {
                    'id': 'cp_old_road',
                    'title': 'Find the caravan wreck',
                    'locationIds': ['old_road'],
                    'questIds': ['q_missing_caravan'],
                    'objectiveIds': ['obj_find_wreck'],
                    'nextCheckpointIds': ['cp_watchtower'],
                },
                {'id': 'cp_watchtower', 'title': 'Reach the watchtower', 'locationIds': ['watchtower_ruins']},
            ],
        ),
    )

    with app.app_context():
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})

    assert result.reason is None
    assert result.completed_checkpoint_ids == []
    assert result.active_checkpoint_id == 'cp_old_road'
    assert snapshot['flags']['campaignPackActiveCheckpointId'] == 'cp_old_road'


def test_pack_progress_promotes_downstream_checkpoint_when_party_reaches_its_location(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='old_road',
            flags={'campaignPackActiveCheckpointId': 'cp_gate'},
            checkpoints=[
                {'id': 'cp_gate', 'title': 'Question the gate captain', 'nextCheckpointIds': ['cp_old_road']},
                {'id': 'cp_old_road', 'title': 'Reach the old road', 'locationIds': ['old_road']},
            ],
        ),
    )

    with app.app_context():
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])

    assert result.changed is True
    assert result.reason == 'reached_downstream_checkpoint_location'
    assert result.completed_checkpoint_ids == ['cp_gate']
    assert result.active_checkpoint_id == 'cp_old_road'


def test_pack_progress_can_complete_out_of_order_checkpoint_without_changing_active_spine(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='watchtower_ruins',
            flags={'campaignPackActiveCheckpointId': 'cp_gate'},
            checkpoints=[
                {'id': 'cp_gate', 'title': 'Question the gate captain', 'objectiveIds': ['obj_question_gate']},
                {
                    'id': 'cp_watchtower',
                    'title': 'Reach the watchtower early',
                    'locationIds': ['watchtower_ruins'],
                    'canCompleteOutOfOrder': True,
                },
            ],
        ),
    )

    with app.app_context():
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})

    assert result.changed is True
    assert result.reason == 'checkpoint_out_of_order_completed'
    assert result.completed_checkpoint_ids == ['cp_watchtower']
    assert result.active_checkpoint_id == 'cp_gate'
    assert snapshot['flags']['campaignPackActiveCheckpointId'] == 'cp_gate'


def test_pack_progress_completes_checkpoint_when_objective_is_completed(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='bleakmoor_gate',
            flags={'campaignPackActiveCheckpointId': 'cp_question_veyra'},
            quests=[
                {
                    'id': 'q_missing_caravan',
                    'title': 'Find the Missing Caravan',
                    'status': 'active',
                    'objectives': [
                        {'id': 'obj_question_veyra', 'description': 'Question Captain Veyra.', 'status': 'completed'}
                    ],
                    'source': 'campaign_pack',
                    'packId': 'bleakmoor_intro',
                }
            ],
            checkpoints=[
                {
                    'id': 'cp_question_veyra',
                    'title': 'Question Captain Veyra',
                    'questIds': ['q_missing_caravan'],
                    'objectiveIds': ['obj_question_veyra'],
                    'nextCheckpointIds': ['cp_old_road'],
                },
                {'id': 'cp_old_road', 'title': 'Reach the old road', 'locationIds': ['old_road']},
            ],
        ),
    )

    with app.app_context():
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])

    assert result.changed is True
    assert result.reason == 'checkpoint_objective_completed'
    assert result.completed_checkpoint_ids == ['cp_question_veyra']
    assert result.active_checkpoint_id == 'cp_old_road'


def test_pack_progress_completes_encounter_checkpoint_after_alternate_resolution(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='watchtower_ruins',
            flags={'campaignPackActiveCheckpointId': 'cp_watchtower'},
            combat={
                'status': 'ended',
                'participants': [],
                'flags': {
                    'campaignPackEncounterId': 'enc_lantern_wraith',
                    'campaignPackId': 'bleakmoor_intro',
                    'endReason': 'negotiated_resolution',
                },
            },
            pack_extra={
                'catalog': {
                    'encounters': [
                        {
                            'id': 'enc_lantern_wraith',
                            'completion': {'anyOf': ['defeat', 'bargain']},
                        }
                    ]
                }
            },
            checkpoints=[
                {
                    'id': 'cp_watchtower',
                    'title': 'Confront the lantern wraith',
                    'encounterIds': ['enc_lantern_wraith'],
                    'nextCheckpointIds': ['cp_aftermath'],
                },
                {'id': 'cp_aftermath', 'title': 'Aftermath'},
            ],
        ),
    )

    with app.app_context():
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])

    assert result.changed is True
    assert result.reason == 'checkpoint_encounter_completed'
    assert result.completed_checkpoint_ids == ['cp_watchtower']
    assert result.active_checkpoint_id == 'cp_aftermath'


def test_pack_progress_does_not_jump_backward_after_terminal_checkpoint_completion(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='watchtower_ruins',
            flags={'campaignPackActiveCheckpointId': 'cp_watchtower'},
            combat={
                'status': 'ended',
                'participants': [],
                'flags': {
                    'campaignPackEncounterId': 'enc_lantern_wraith',
                    'campaignPackId': 'bleakmoor_intro',
                    'endReason': 'all_enemies_defeated',
                },
            },
            checkpoints=[
                {'id': 'cp_gate', 'title': 'Question the gate captain'},
                {
                    'id': 'cp_watchtower',
                    'title': 'Confront the lantern wraith',
                    'encounterIds': ['enc_lantern_wraith'],
                },
            ],
        ),
    )

    with app.app_context():
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])

    assert result.changed is True
    assert result.reason == 'checkpoint_encounter_completed'
    assert result.completed_checkpoint_ids == ['cp_watchtower']
    assert result.active_checkpoint_id is None


def test_pack_progress_marks_failed_checkpoint_and_uses_failure_route(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='watchtower_ruins',
            flags={'campaignPackActiveCheckpointId': 'cp_watchtower'},
            combat={
                'status': 'ended',
                'participants': [],
                'flags': {
                    'campaignPackEncounterId': 'enc_lantern_wraith',
                    'campaignPackId': 'bleakmoor_intro',
                    'endReason': 'objective_failed',
                },
            },
            checkpoints=[
                {
                    'id': 'cp_watchtower',
                    'title': 'Confront the lantern wraith',
                    'encounterIds': ['enc_lantern_wraith'],
                    'failureCheckpointIds': ['cp_fallback'],
                    'nextCheckpointIds': ['cp_aftermath'],
                },
                {'id': 'cp_aftermath', 'title': 'Aftermath'},
                {'id': 'cp_fallback', 'title': 'Recover the trail'},
            ],
        ),
    )

    with app.app_context():
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})

    assert result.changed is True
    assert result.reason == 'checkpoint_encounter_failed'
    assert result.failed_checkpoint_ids == ['cp_watchtower']
    assert result.active_checkpoint_id == 'cp_fallback'
    assert snapshot['flags']['campaignPackFailedCheckpointIds'] == ['cp_watchtower']


def test_pack_progress_skips_optional_linear_beat_when_required_beat_is_available(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='bleakmoor_gate',
            flags={'campaignPackActiveCheckpointId': 'cp_gate'},
            checkpoints=[
                {'id': 'cp_gate', 'title': 'Question the gate captain', 'segmentIds': ['seg_gate']},
                {'id': 'cp_optional_rumor', 'title': 'Hear a rumor', 'optional': True},
                {'id': 'cp_old_road', 'title': 'Reach the old road'},
            ],
        ),
    )

    with app.app_context():
        db.session.add(
            CampaignSegment(
                campaign_id=ids['campaign_id'],
                title='Question the gate captain',
                trigger_condition=safe_json_dumps({'type': 'manual', 'packSegmentId': 'seg_gate'}, {}),
                tags='campaign_pack,pack:bleakmoor_intro,pack_segment:seg_gate',
                is_triggered=True,
            )
        )
        db.session.commit()
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])

    assert result.completed_checkpoint_ids == ['cp_gate']
    assert result.active_checkpoint_id == 'cp_old_road'


def test_pack_progress_respects_checkpoint_prerequisites_when_branching(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='bleakmoor_gate',
            flags={'campaignPackActiveCheckpointId': 'cp_gate'},
            checkpoints=[
                {
                    'id': 'cp_gate',
                    'title': 'Question the gate captain',
                    'segmentIds': ['seg_gate'],
                    'nextCheckpointIds': ['cp_locked', 'cp_open'],
                },
                {
                    'id': 'cp_locked',
                    'title': 'Secret route',
                    'prerequisiteCheckpointIds': ['cp_secret_clue'],
                },
                {'id': 'cp_open', 'title': 'Old road'},
            ],
        ),
    )

    with app.app_context():
        db.session.add(
            CampaignSegment(
                campaign_id=ids['campaign_id'],
                title='Question the gate captain',
                trigger_condition=safe_json_dumps({'type': 'manual', 'packSegmentId': 'seg_gate'}, {}),
                tags='campaign_pack,pack:bleakmoor_intro,pack_segment:seg_gate',
                is_triggered=True,
            )
        )
        db.session.commit()
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])

    assert result.completed_checkpoint_ids == ['cp_gate']
    assert result.active_checkpoint_id == 'cp_open'


def test_pack_progress_triggered_pack_segment_advances_to_downstream_checkpoint(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='bleakmoor_gate',
            flags={'campaignPackActiveCheckpointId': 'cp_question_veyra'},
            checkpoints=[
                {
                    'id': 'cp_question_veyra',
                    'title': 'Question Captain Veyra',
                    'segmentIds': ['seg_question_veyra'],
                    'nextCheckpointIds': ['cp_old_road'],
                },
                {'id': 'cp_old_road', 'title': 'Reach the old road', 'locationIds': ['old_road']},
            ],
        ),
    )

    with app.app_context():
        db.session.add(
            CampaignSegment(
                campaign_id=ids['campaign_id'],
                title='Question Captain Veyra',
                trigger_condition=safe_json_dumps({'type': 'manual', 'packSegmentId': 'seg_question_veyra'}, {}),
                tags='campaign_pack,pack:bleakmoor_intro,pack_segment:seg_question_veyra',
                is_triggered=True,
            )
        )
        db.session.commit()
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])

    assert result.changed is True
    assert result.reason == 'checkpoint_segment_triggered'
    assert result.completed_checkpoint_ids == ['cp_question_veyra']
    assert result.active_checkpoint_id == 'cp_old_road'


def test_pack_progress_uses_explicit_pack_segment_external_id(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='bleakmoor_gate',
            flags={'campaignPackActiveCheckpointId': 'cp_gate'},
            checkpoints=[
                {'id': 'cp_gate', 'title': 'Question the gate captain', 'segmentIds': ['seg_gate'], 'nextCheckpointIds': ['cp_old_road']},
                {'id': 'cp_old_road', 'title': 'Reach the old road', 'locationIds': ['old_road']},
            ],
        ),
    )

    with app.app_context():
        db.session.add(
            CampaignSegment(
                campaign_id=ids['campaign_id'],
                title='Question the gate captain',
                external_id='seg_gate',
                source='campaign_pack',
                source_pack_id='bleakmoor_intro',
                is_triggered=True,
            )
        )
        db.session.commit()
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])

    assert result.changed is True
    assert result.reason == 'checkpoint_segment_triggered'
    assert result.completed_checkpoint_ids == ['cp_gate']
    assert result.active_checkpoint_id == 'cp_old_road'


def test_pack_progress_off_track_location_does_not_complete_checkpoint(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='marsh_detour',
            flags={'campaignPackActiveCheckpointId': 'cp_old_road'},
            checkpoints=[
                {'id': 'cp_old_road', 'title': 'Reach the old road', 'locationIds': ['old_road'], 'rejoinTargetCheckpointId': 'cp_old_road'},
                {'id': 'cp_watchtower', 'title': 'Reach the watchtower', 'locationIds': ['watchtower_ruins']},
            ],
        ),
    )

    with app.app_context():
        result = update_campaign_pack_progress(session_id=ids['session_id'], campaign_id=ids['campaign_id'])
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})

    assert result.changed is False
    assert result.completed_checkpoint_ids == []
    assert result.active_checkpoint_id == 'cp_old_road'
    assert snapshot['flags']['campaignPackActiveCheckpointId'] == 'cp_old_road'
    assert snapshot['flags']['campaignPackCompletedCheckpointIds'] == []


def test_state_segment_evaluation_updates_pack_checkpoint_progress(app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='soot_stained_chapel',
            flags={'campaignPackActiveCheckpointId': 'cp_chapel'},
            checkpoints=[
                {
                    'id': 'cp_chapel',
                    'title': 'Enter the chapel',
                    'segmentIds': ['seg_enter_chapel'],
                    'nextCheckpointIds': ['cp_watchtower'],
                },
                {'id': 'cp_watchtower', 'title': 'Reach the watchtower'},
            ],
        ),
    )

    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=None,
            player_input='I enter the chapel.',
            dm_output='You enter the soot-stained chapel.',
            status='completed',
        )
        db.session.add_all(
            [
                turn,
                CampaignSegment(
                    campaign_id=ids['campaign_id'],
                    title='Enter the chapel',
                    description='The chapel beat activates.',
                    trigger_condition=safe_json_dumps(
                        {
                            'type': 'state',
                            'location_contains': 'chapel',
                            'packSegmentId': 'seg_enter_chapel',
                        },
                        {},
                    ),
                    tags='campaign_pack,pack:bleakmoor_intro,pack_segment:seg_enter_chapel',
                    is_triggered=False,
                ),
            ]
        )
        db.session.commit()

        triggered = _evaluate_state_segments_after_turn(turn, campaign)
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})

    assert triggered
    assert snapshot['flags']['campaignPackCompletedCheckpointIds'] == ['cp_chapel']
    assert snapshot['flags']['campaignPackActiveCheckpointId'] == 'cp_watchtower'


def test_campaign_pack_progress_endpoint_reports_pack_state(client, app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='bleakmoor_gate',
            flags={'campaignPackActiveCheckpointId': 'cp_gate'},
            checkpoints=[
                {'id': 'cp_gate', 'title': 'Question the gate captain', 'nextCheckpointIds': ['cp_old_road']},
                {'id': 'cp_old_road', 'title': 'Reach the old road', 'locationIds': ['old_road']},
            ],
        ),
    )

    response = client.get(f"/api/sessions/{ids['session_id']}/campaign-pack/progress")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['enabled'] is True
    assert payload['pack']['packId'] == 'bleakmoor_intro'
    assert payload['activeCheckpointId'] == 'cp_gate'
    assert [checkpoint['id'] for checkpoint in payload['checkpoints']] == ['cp_gate', 'cp_old_road']


def test_campaign_pack_progress_endpoint_advances_and_overrides_checkpoint(client, app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='bleakmoor_gate',
            flags={'campaignPackActiveCheckpointId': 'cp_gate'},
            checkpoints=[
                {'id': 'cp_gate', 'title': 'Question the gate captain', 'nextCheckpointIds': ['cp_old_road']},
                {'id': 'cp_old_road', 'title': 'Reach the old road', 'locationIds': ['old_road']},
                {'id': 'cp_watchtower', 'title': 'Reach the watchtower', 'locationIds': ['watchtower_ruins']},
            ],
        ),
    )

    advance = client.post(
        f"/api/sessions/{ids['session_id']}/campaign-pack/progress",
        json={'action': 'advance'},
    )
    override = client.post(
        f"/api/sessions/{ids['session_id']}/campaign-pack/progress",
        json={'action': 'override', 'checkpointId': 'cp_watchtower', 'reason': 'Table correction'},
    )

    assert advance.status_code == 200
    assert advance.get_json()['active_checkpoint_id'] == 'cp_old_road'
    assert advance.get_json()['completed_checkpoint_ids'] == ['cp_gate']
    assert advance.get_json()['progress_revision'] == 1
    assert advance.get_json()['event_id']
    assert override.status_code == 200
    assert override.get_json()['active_checkpoint_id'] == 'cp_watchtower'
    assert override.get_json()['progress_revision'] == 2
    with app.app_context():
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
        events = (
            TurnEvent.query.filter_by(session_id=ids['session_id'], event_type=PROGRESS_CHANGED_EVENT)
            .order_by(TurnEvent.event_id.asc())
            .all()
        )
        pack_session = CampaignPackSession.query.filter_by(session_id=ids['session_id']).one()
        durable_events = (
            CampaignPackProgressEvent.query.filter_by(campaign_pack_session_id=pack_session.campaign_pack_session_id)
            .order_by(CampaignPackProgressEvent.progress_event_id.asc())
            .all()
        )
        durable_progress = {
            row.checkpoint_id: row.status
            for row in CampaignPackCheckpointProgress.query.filter_by(
                campaign_pack_session_id=pack_session.campaign_pack_session_id
            ).all()
        }
    assert snapshot['flags']['campaignPackActiveCheckpointId'] == 'cp_watchtower'
    assert snapshot['flags']['campaignPackProgressRevision'] == 2
    assert snapshot['flags']['campaignPackLastManualControl']['reason'] == 'Table correction'
    assert [safe_json_loads(event.payload_json, {})['action'] for event in events] == ['advance', 'override']
    assert safe_json_loads(events[-1].payload_json, {})['actor'] == 'operator'
    assert [event.action for event in durable_events] == ['advance', 'override']
    assert durable_events[-1].reason == 'Table correction'
    assert durable_progress['cp_gate'] == 'completed'
    assert durable_progress['cp_watchtower'] == 'active'


def test_campaign_pack_progress_endpoint_rejects_stale_expected_revision(client, app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='bleakmoor_gate',
            flags={'campaignPackActiveCheckpointId': 'cp_gate', 'campaignPackProgressRevision': 1},
            pack_extra={'progressRevision': 1},
            checkpoints=[
                {'id': 'cp_gate', 'title': 'Question the gate captain', 'nextCheckpointIds': ['cp_old_road']},
                {'id': 'cp_old_road', 'title': 'Reach the old road', 'locationIds': ['old_road']},
            ],
        ),
    )

    response = client.post(
        f"/api/sessions/{ids['session_id']}/campaign-pack/progress",
        json={'action': 'advance', 'expectedRevision': 0},
    )

    assert response.status_code == 409
    assert response.get_json()['error_code'] == 'stale_campaign_pack_progress'
    with app.app_context():
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
        assert snapshot['flags']['campaignPackActiveCheckpointId'] == 'cp_gate'
        assert TurnEvent.query.filter_by(session_id=ids['session_id'], event_type=PROGRESS_CHANGED_EVENT).count() == 0


def test_campaign_pack_progress_filters_player_reads_and_blocks_player_controls(tmp_path, monkeypatch):
    auth_app = _build_auth_app(tmp_path, monkeypatch)
    client = auth_app.test_client()
    player_headers = _account_headers(auth_app, username='player_user', role='player')
    admin_headers = _account_headers(auth_app, username='admin_user', role='admin')
    ids = _seed_pack_session(
        auth_app,
        _pack_snapshot(
            location_id='bleakmoor_gate',
            flags={
                'campaignPackActiveCheckpointId': 'cp_gate',
                'campaignPackLastManualControl': {'reason': 'dm note'},
                'secretRuntimeFlag': 'dm-only',
            },
            pack_extra={
                'catalog': {
                    'locations': [{'id': 'old_road', 'name': 'Old Road', 'hiddenToPlayers': True}],
                    'npcs': [{'id': 'npc_lantern_keeper', 'name': 'Lantern Keeper', 'hiddenToPlayers': True}],
                },
                'activeDirectorRules': {'checkpointStyle': 'strict'},
            },
            checkpoints=[
                {
                    'id': 'cp_gate',
                    'title': 'Question the gate captain',
                    'summary': 'Ask about the missing caravan.',
                    'nextCheckpointIds': ['cp_old_road'],
                    'alternateCheckpointIds': ['cp_secret_tunnel'],
                    'npcIds': ['npc_captain_veyra'],
                    'directorRules': {'checkpointStyle': 'guided'},
                },
                {
                    'id': 'cp_old_road',
                    'title': 'Find the hidden old road',
                    'locationIds': ['old_road'],
                    'npcIds': ['npc_lantern_keeper'],
                },
                {
                    'id': 'cp_public_rumor',
                    'title': 'Hear a public rumor',
                    'visibleToPlayers': True,
                    'nextCheckpointIds': ['cp_old_road'],
                },
            ],
        ),
    )

    player_read = client.get(f"/api/sessions/{ids['session_id']}/campaign-pack/progress", headers=player_headers)
    admin_read = client.get(f"/api/sessions/{ids['session_id']}/campaign-pack/progress", headers=admin_headers)
    player_control = client.post(
        f"/api/sessions/{ids['session_id']}/campaign-pack/progress",
        headers=player_headers,
        json={'action': 'advance'},
    )
    admin_control = client.post(
        f"/api/sessions/{ids['session_id']}/campaign-pack/progress",
        headers=admin_headers,
        json={'action': 'advance', 'expectedRevision': 0},
    )
    player_state = client.get(f"/api/sessions/{ids['session_id']}/state", headers=player_headers)
    admin_state = client.get(f"/api/sessions/{ids['session_id']}/state", headers=admin_headers)
    player_workspace = client.get(f"/api/campaigns/{ids['campaign_id']}/workspace", headers=player_headers)
    player_sessions = client.get(f"/api/sessions/campaigns/{ids['campaign_id']}/sessions", headers=player_headers)
    player_events = client.get(f"/api/sessions/{ids['session_id']}/events", headers=player_headers)
    admin_events = client.get(f"/api/sessions/{ids['session_id']}/events", headers=admin_headers)
    player_import = client.post(
        '/api/campaigns/import-pack',
        headers=player_headers,
        json={'packId': 'player_import_attempt', 'title': 'Player Import Attempt'},
    )

    assert player_read.status_code == 200
    player_payload = player_read.get_json()
    assert player_payload['visibility'] == 'player'
    assert [checkpoint['id'] for checkpoint in player_payload['checkpoints']] == ['cp_gate', 'cp_public_rumor']
    assert 'nextCheckpointIds' not in player_payload['checkpoints'][0]
    assert 'npcIds' not in player_payload['checkpoints'][0]
    assert player_payload['checkpointStatuses'] == {'cp_gate': 'active', 'cp_public_rumor': 'open'}
    assert player_payload['directorRules'] == {}
    assert 'campaignPackLastManualControl' not in player_payload['flags']

    assert admin_read.status_code == 200
    admin_payload = admin_read.get_json()
    assert admin_payload['visibility'] == 'dm'
    assert [checkpoint['id'] for checkpoint in admin_payload['checkpoints']] == [
        'cp_gate',
        'cp_old_road',
        'cp_public_rumor',
    ]
    assert admin_payload['checkpoints'][0]['nextCheckpointIds'] == ['cp_old_road']
    assert player_control.status_code == 403
    assert player_control.get_json()['error_code'] == 'forbidden'
    assert admin_control.status_code == 200
    assert admin_control.get_json()['active_checkpoint_id'] == 'cp_old_road'

    assert player_state.status_code == 200
    player_snapshot = player_state.get_json()['state_snapshot']
    assert player_snapshot['campaignPack']['visibility'] == 'player'
    assert 'catalog' not in player_snapshot['campaignPack']
    assert 'directorRules' not in player_snapshot['campaignPack']
    assert 'activeDirectorRules' not in player_snapshot['campaignPack']
    assert [checkpoint['id'] for checkpoint in player_snapshot['campaignPack']['checkpoints']] == [
        'cp_gate',
        'cp_old_road',
        'cp_public_rumor',
    ]
    assert 'nextCheckpointIds' not in player_snapshot['campaignPack']['checkpoints'][0]
    assert 'npcIds' not in player_snapshot['campaignPack']['checkpoints'][0]
    assert 'directorRules' not in player_snapshot['campaignPack']['checkpoints'][0]
    assert player_snapshot['flags'] == {
        'campaignPackActiveCheckpointId': 'cp_old_road',
        'campaignPackCompletedCheckpointIds': ['cp_gate'],
        'campaignPackSkippedCheckpointIds': [],
        'campaignPackFailedCheckpointIds': [],
        'campaignPackProgressRevision': 1,
    }

    assert admin_state.status_code == 200
    admin_snapshot = admin_state.get_json()['state_snapshot']
    assert admin_snapshot['campaignPack']['catalog']['locations'][0]['id'] == 'old_road'
    assert admin_snapshot['campaignPack']['directorRules']['offTrackPolicy'] == 'improvise_and_reconnect'

    assert player_workspace.status_code == 200
    workspace_snapshot = player_workspace.get_json()['sessions'][0]['state_snapshot']
    assert workspace_snapshot['campaignPack']['visibility'] == 'player'
    assert 'catalog' not in workspace_snapshot['campaignPack']

    assert player_sessions.status_code == 200
    session_list_snapshot = player_sessions.get_json()[0]['state_snapshot']
    assert session_list_snapshot['campaignPack']['visibility'] == 'player'
    assert 'catalog' not in session_list_snapshot['campaignPack']

    assert player_events.status_code == 200
    assert [event['event_type'] for event in player_events.get_json()['events']] == []
    assert admin_events.status_code == 200
    assert [event['event_type'] for event in admin_events.get_json()['events']] == [PROGRESS_CHANGED_EVENT]
    assert player_import.status_code == 403
    assert player_import.get_json()['error_code'] == 'forbidden'

    with auth_app.app_context():
        event = TurnEvent.query.filter_by(session_id=ids['session_id'], event_type=PROGRESS_CHANGED_EVENT).one()
        event_payload = safe_json_loads(event.payload_json, {})
    assert event_payload['action'] == 'advance'
    assert event_payload['actor'].endswith(':admin')


def test_campaign_pack_progress_endpoint_can_mark_checkpoint_failed(client, app):
    ids = _seed_pack_session(
        app,
        _pack_snapshot(
            location_id='bleakmoor_gate',
            flags={'campaignPackActiveCheckpointId': 'cp_gate'},
            checkpoints=[
                {
                    'id': 'cp_gate',
                    'title': 'Question the gate captain',
                    'failureCheckpointIds': ['cp_fallback'],
                    'nextCheckpointIds': ['cp_old_road'],
                },
                {'id': 'cp_old_road', 'title': 'Reach the old road', 'locationIds': ['old_road']},
                {'id': 'cp_fallback', 'title': 'Find another lead'},
            ],
        ),
    )

    response = client.post(
        f"/api/sessions/{ids['session_id']}/campaign-pack/progress",
        json={'action': 'fail', 'reason': 'The clue was destroyed.'},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['active_checkpoint_id'] == 'cp_fallback'
    assert payload['failed_checkpoint_ids'] == ['cp_gate']
    with app.app_context():
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
    assert snapshot['flags']['campaignPackFailedCheckpointIds'] == ['cp_gate']
