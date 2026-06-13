from __future__ import annotations

from aidm_server.canon_jobs import _evaluate_state_segments_after_turn
from aidm_server.database import db
from aidm_server.models import Campaign, CampaignSegment, DmTurn, Session, World, safe_json_dumps, safe_json_loads
from aidm_server.services.campaign_pack_progress import update_campaign_pack_progress


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

    assert result.changed is True
    assert result.reason == 'checkpoint_location_reached'
    assert result.completed_checkpoint_ids == ['cp_old_road']
    assert result.active_checkpoint_id == 'cp_watchtower'
    assert snapshot['flags']['campaignPackCompletedCheckpointIds'] == ['cp_old_road']
    assert snapshot['flags']['campaignPackActiveCheckpointId'] == 'cp_watchtower'
    assert snapshot['campaignPack']['activeCheckpointId'] == 'cp_watchtower'


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

    assert result.changed is True
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
    assert override.status_code == 200
    assert override.get_json()['active_checkpoint_id'] == 'cp_watchtower'
    with app.app_context():
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
    assert snapshot['flags']['campaignPackActiveCheckpointId'] == 'cp_watchtower'
    assert snapshot['flags']['campaignPackLastManualControl']['reason'] == 'Table correction'


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
