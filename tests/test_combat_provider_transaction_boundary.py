from __future__ import annotations

import pytest
from sqlalchemy import text

import aidm_server.combat.pipeline as combat_pipeline
import aidm_server.game_state.orchestration.turn_pipeline as turn_pipeline_module
from aidm_server.combat.state import instantiate_creature, player_combat_participant
from aidm_server.creatures.core_bestiary import core_creature
from aidm_server.creatures.resolver import CreatureResolutionPlan
from aidm_server.database import db
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY, STATE_PIPELINE_VERSION
from aidm_server.models import (
    BestiaryEntry,
    Campaign,
    CombatEncounter,
    DmTurn,
    Player,
    Session,
    safe_json_dumps,
)
from aidm_server.provider_priority import provider_priority_gate
from tests.helpers import seed_world_campaign_player_session


def _player(player_id: int) -> dict:
    return {
        'id': f'player_{player_id}',
        'playerId': player_id,
        'name': 'Seraphina',
        'level': 3,
        'health': {'currentHp': 24, 'maxHp': 24, 'tempHp': 0, 'conditions': []},
        'stats': {'armorClass': 14},
    }


def _active_combat_state(player_id: int) -> dict:
    enemy = instantiate_creature(
        core_creature('bandit_thug'),
        instance_id='enemy_boundary_bandit',
    )
    return {
        'currentScene': {'sceneType': 'combat', 'combatState': 'active'},
        'playerCharacters': [_player(player_id)],
        'combat': {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [player_combat_participant(_player(player_id)), enemy],
            'flags': {'activeActorId': f'player_{player_id}'},
        },
    }


def _resolved_combat_state(player_id: int) -> dict:
    enemy = instantiate_creature(
        core_creature('bandit_thug'),
        instance_id='enemy_resolved_boundary_bandit',
    )
    enemy['hp']['current'] = 0
    enemy['isAlive'] = False
    enemy['isConscious'] = False
    enemy['conditions'] = ['defeated']
    return {
        'currentScene': {'sceneType': 'combat', 'combatState': 'resolved'},
        'playerCharacters': [_player(player_id)],
        'combat': {
            'status': 'ended',
            'round': 1,
            'participants': [player_combat_participant(_player(player_id)), enemy],
            'flags': {},
        },
    }


def _campaign_pack_state(player_id: int) -> dict:
    enemy = {
        **core_creature('bandit_thug'),
        'id': 'pack_orc_scout',
        'name': 'Pack Orc Scout',
        'source': 'campaign_pack',
        'packId': 'boundary_pack',
    }
    return {
        'currentScene': {'locationId': 'reed_bank', 'name': 'Reed Bank'},
        'playerCharacters': [_player(player_id)],
        'flags': {'campaignPackActiveCheckpointId': 'checkpoint_one'},
        'campaignPack': {
            'packId': 'boundary_pack',
            'checkpoints': [
                {
                    'id': 'checkpoint_one',
                    'encounterIds': ['pack_ambush'],
                }
            ],
            'catalog': {
                'enemies': [enemy],
                'encounters': [
                    {
                        'id': 'pack_ambush',
                        'title': 'Pack Ambush',
                        'checkpointIds': ['checkpoint_one'],
                        'enemyIds': ['pack_orc_scout'],
                    }
                ],
            },
        },
        'combat': {'status': 'none', 'round': 1, 'participants': [], 'flags': {}},
    }


def _seed_turn(app) -> dict[str, int]:
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I attack the nearest enemy.',
            status='pending',
        )
        db.session.add(turn)
        db.session.commit()
        return {**ids, 'turn_id': turn.turn_id}


def _intent_plan(combat: dict) -> dict:
    enemy = next(
        participant
        for participant in combat.get('participants') or []
        if participant.get('team') == 'enemy'
    )
    return {
        'round': combat.get('round') or 1,
        'intents': [
            {
                'enemyId': enemy['id'],
                'intentType': 'attack',
                'reason': 'Deterministic test intent.',
                'confidence': 0.8,
            }
        ],
        'summaryForDm': f"{enemy['name']}: attack",
    }


def _creature_resolution() -> dict:
    creature = core_creature('bandit_thug')
    return {
        'groups': [
            {
                'id': 'boundary_dynamic_group',
                'label': 'Boundary Bandit',
                'count': 1,
                'creature': creature,
                'source': creature.get('source') or 'core',
                'resolutionMethod': 'generated',
            }
        ],
        'totalEnemies': 1,
        'resolutionMethod': 'generated',
        'resolutionMethods': ['generated'],
        'sources': ['generated'],
        'generated': True,
        'savedToBestiary': True,
        'encounterGoal': {'type': 'defeat'},
        'debug': {},
    }


def _boundary_callbacks(events: list[str]):
    def release() -> None:
        events.append('release')
        scoped_session = db.session()
        assert not scoped_session.new
        assert not scoped_session.dirty
        assert not scoped_session.deleted
        db.session.remove()

    def reload_orm(session_id: int, campaign_id: int, turn_id: int):
        events.append('reload')
        return (
            db.session.get(Session, session_id),
            db.session.get(Campaign, campaign_id),
            db.session.get(DmTurn, turn_id),
        )

    return release, reload_orm


def test_active_combat_releases_before_foreground_planning_and_reloads_before_write(
    app,
    monkeypatch,
):
    ids = _seed_turn(app)
    events: list[str] = []

    def fake_plan(combat: dict) -> dict:
        events.append('plan')
        assert db.session().in_transaction() is False
        priority = provider_priority_gate.snapshot()
        assert priority.active_foreground == 1
        assert priority.background_active is False
        return _intent_plan(combat)

    monkeypatch.setattr(combat_pipeline, 'plan_enemy_intents', fake_plan)

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        turn = db.session.get(DmTurn, ids['turn_id'])
        assert session_obj is not None
        assert campaign is not None
        assert turn is not None
        release, reload_orm = _boundary_callbacks(events)

        result = combat_pipeline.prepare_combat_for_turn(
            state=_active_combat_state(ids['player_id']),
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            player_message=turn.player_input,
            workspace_id=campaign.workspace_id,
            before_intent_provider_call=release,
            reload_orm_after_intent_provider_call=reload_orm,
        )

        assert CombatEncounter.query.count() == 0

    assert not result['debug'].get('intentPlanningFallbackUsed'), result['debug']
    assert events == ['release', 'plan', 'reload'], result['debug']
    assert result['changes']
    assert result['debug']['ormSessionReleased'] is True
    assert result['debug']['intentPlanningStale'] is False


def test_active_combat_discards_plan_when_session_changes_during_provider(
    app,
    monkeypatch,
):
    ids = _seed_turn(app)
    events: list[str] = []

    def stale_plan(combat: dict) -> dict:
        events.append('plan')
        assert db.session().in_transaction() is False
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    'UPDATE sessions '
                    'SET state_snapshot = :snapshot '
                    'WHERE session_id = :session_id'
                ),
                {
                    'snapshot': '{"revision":2}',
                    'session_id': ids['session_id'],
                },
            )
        return _intent_plan(combat)

    monkeypatch.setattr(combat_pipeline, 'plan_enemy_intents', stale_plan)

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        turn = db.session.get(DmTurn, ids['turn_id'])
        assert session_obj is not None
        assert campaign is not None
        assert turn is not None
        release, reload_orm = _boundary_callbacks(events)

        result = combat_pipeline.prepare_combat_for_turn(
            state=_active_combat_state(ids['player_id']),
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            player_message=turn.player_input,
            workspace_id=campaign.workspace_id,
            before_intent_provider_call=release,
            reload_orm_after_intent_provider_call=reload_orm,
        )

        assert CombatEncounter.query.count() == 0

    assert not result['debug'].get('intentPlanningFallbackUsed'), result['debug']
    assert events == ['release', 'plan', 'reload'], result['debug']
    assert result['changes'] == []
    assert result['debug']['intentPlanningStale'] is True
    assert 'changed during enemy intent planning' in result['debug']['intentPlanningError']


def test_unexpected_planner_failure_uses_provider_free_deterministic_fallback(
    app,
    monkeypatch,
):
    ids = _seed_turn(app)
    events: list[str] = []
    planner_calls = 0

    def flaky_plan(combat: dict) -> dict:
        nonlocal planner_calls
        planner_calls += 1
        events.append(f'plan_{planner_calls}')
        if planner_calls == 1:
            raise RuntimeError('selector orchestration failed')
        settings = combat['flags']['combatDifficultyAI']
        assert settings['allowBossTacticsHelper'] is False
        assert settings['allowSentientEnemyBrain'] is False
        assert settings['allowFreeformEnemyTactics'] is False
        assert settings['maxLlmCallsPerRound'] == 0
        return _intent_plan(combat)

    monkeypatch.setattr(combat_pipeline, 'plan_enemy_intents', flaky_plan)

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        turn = db.session.get(DmTurn, ids['turn_id'])
        assert session_obj is not None
        assert campaign is not None
        assert turn is not None
        release, reload_orm = _boundary_callbacks(events)

        result = combat_pipeline.prepare_combat_for_turn(
            state=_active_combat_state(ids['player_id']),
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            player_message=turn.player_input,
            workspace_id=campaign.workspace_id,
            before_intent_provider_call=release,
            reload_orm_after_intent_provider_call=reload_orm,
        )

        assert CombatEncounter.query.count() == 0

    assert events == ['release', 'plan_1', 'plan_2', 'reload']
    assert result['changes']
    assert result['debug']['intentPlanningFallbackUsed'] is True
    assert 'selector orchestration failed' in result['debug']['intentPlanningError']


def test_campaign_pack_planning_uses_same_release_and_revalidation_boundary(
    app,
    monkeypatch,
):
    ids = _seed_turn(app)
    events: list[str] = []

    def fake_plan(combat: dict) -> dict:
        events.append('plan')
        assert db.session().in_transaction() is False
        assert provider_priority_gate.snapshot().active_foreground == 1
        return _intent_plan(combat)

    monkeypatch.setattr(combat_pipeline, 'plan_enemy_intents', fake_plan)
    monkeypatch.setattr(
        combat_pipeline,
        'resolve_creatures_for_encounter',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('campaign-pack combat should not use the generic resolver')
        ),
    )

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        turn = db.session.get(DmTurn, ids['turn_id'])
        assert session_obj is not None
        assert campaign is not None
        assert turn is not None
        release, reload_orm = _boundary_callbacks(events)

        result = combat_pipeline.prepare_combat_for_turn(
            state=_campaign_pack_state(ids['player_id']),
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            player_message=turn.player_input,
            workspace_id=campaign.workspace_id,
            before_intent_provider_call=release,
            reload_orm_after_intent_provider_call=reload_orm,
        )

        assert CombatEncounter.query.count() == 0

    combat_start = result['changes'][0]
    assert events == ['release', 'plan', 'reload']
    assert combat_start['source'] == 'campaign_pack'
    assert result['debug']['ormSessionReleased'] is True
    assert result['debug']['intentPlanningStale'] is False


@pytest.mark.parametrize('post_dm_response', [False, True])
def test_dynamic_combat_defers_creature_persistence_until_after_both_provider_boundaries(
    app,
    monkeypatch,
    post_dm_response,
):
    ids = _seed_turn(app)
    events: list[str] = []
    resolution = _creature_resolution()

    def fake_creature_plan(
        _request,
        *,
        workspace_id,
        before_provider_call=None,
        after_provider_call=None,
    ):
        assert workspace_id == 'owner'
        assert before_provider_call is not None
        assert after_provider_call is not None
        events.append('creature_plan')
        before_provider_call()
        events.append('creature_provider')
        assert db.session().in_transaction() is False
        after_provider_call()
        events.append('creature_plan_ready')
        return CreatureResolutionPlan(result=resolution, pending_saves=())

    def fake_intent_plan(combat: dict) -> dict:
        events.append('intent_plan')
        assert db.session().in_transaction() is False
        assert provider_priority_gate.snapshot().active_foreground == 1
        return _intent_plan(combat)

    def fake_persist(plan: CreatureResolutionPlan) -> dict:
        events.append('persist_creature_plan')
        assert plan.result is resolution
        return plan.result

    monkeypatch.setattr(combat_pipeline, 'plan_creatures_for_encounter', fake_creature_plan)
    monkeypatch.setattr(combat_pipeline, 'plan_enemy_intents', fake_intent_plan)
    monkeypatch.setattr(
        combat_pipeline,
        'persist_creature_resolution_plan',
        fake_persist,
    )

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        turn = db.session.get(DmTurn, ids['turn_id'])
        assert session_obj is not None
        assert campaign is not None
        assert turn is not None
        release, reload_orm = _boundary_callbacks(events)
        kwargs = {
            'state': {
                'currentScene': {'sceneType': 'exploration', 'combatState': 'none'},
                'playerCharacters': [_player(ids['player_id'])],
                'combat': {'status': 'none', 'participants': [], 'flags': {}},
            },
            'session_obj': session_obj,
            'campaign': campaign,
            'turn': turn,
            'player_message': (
                'I step into the road.'
                if post_dm_response
                else 'I attack the bandit in the road.'
            ),
            'workspace_id': campaign.workspace_id,
            'before_creature_provider_call': release,
            'reload_orm_after_creature_provider_call': reload_orm,
            'before_intent_provider_call': release,
            'reload_orm_after_intent_provider_call': reload_orm,
        }
        if post_dm_response:
            result = combat_pipeline.prepare_combat_from_dm_response(
                **kwargs,
                dm_response='A bandit attacks you from the roadside. Roll initiative.',
            )
        else:
            result = combat_pipeline.prepare_combat_for_turn(**kwargs)

        assert CombatEncounter.query.count() == 0

    assert events == [
        'creature_plan',
        'release',
        'creature_provider',
        'reload',
        'creature_plan_ready',
        'release',
        'intent_plan',
        'reload',
    ]
    assert result['changes'][0]['type'] == 'combat.start'
    assert result['debug']['creatureOrmSessionReleased'] is True
    assert result['debug']['intentOrmSessionReleased'] is True
    assert result['debug']['ormSessionReleased'] is True


def test_pre_dm_validator_rejection_discards_deferred_bestiary_and_encounter(
    app,
    monkeypatch,
):
    ids = _seed_turn(app)
    resolution = _creature_resolution()
    persist_calls = 0

    def fake_persist(plan: CreatureResolutionPlan) -> dict:
        nonlocal persist_calls
        persist_calls += 1
        db.session.add(
            BestiaryEntry(
                workspace_id='owner',
                campaign_id=ids['campaign_id'],
                session_id=ids['session_id'],
                scope='session',
                creature_id='boundary_bandit',
                name='Boundary Bandit',
                source='generated',
                persistence='session',
                creature_json=safe_json_dumps(plan.result, {}),
            )
        )
        db.session.flush()
        return plan.result

    monkeypatch.setattr(
        combat_pipeline,
        'plan_creatures_for_encounter',
        lambda *_args, **_kwargs: CreatureResolutionPlan(
            result=resolution,
            pending_saves=(),
        ),
    )
    monkeypatch.setattr(combat_pipeline, 'plan_enemy_intents', _intent_plan)
    monkeypatch.setattr(
        combat_pipeline,
        'persist_creature_resolution_plan',
        fake_persist,
    )
    monkeypatch.setattr(
        turn_pipeline_module,
        'extract_pre_dm_actions',
        lambda **_kwargs: {'declaredActions': [], 'notes': ['test']},
    )

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        player = db.session.get(Player, ids['player_id'])
        turn = db.session.get(DmTurn, ids['turn_id'])
        assert session_obj is not None
        assert campaign is not None
        assert player is not None
        assert turn is not None
        session_obj.state_snapshot = safe_json_dumps(
            _resolved_combat_state(ids['player_id']),
            {},
        )
        db.session.flush()

        result = turn_pipeline_module.pre_dm_pipeline(
            turn=turn,
            session_obj=session_obj,
            campaign=campaign,
            player=player,
            player_message='I attack the bandit again.',
        )

        assert result['combatValidation']['rejected']
        assert 'reopen resolved enemy' in result['combatValidation']['rejected'][0]['reason']
        assert result['combatAppliedChanges'] == []
        assert result['combatDebug']['combatEncounterId'] is None
        assert persist_calls == 0
        assert BestiaryEntry.query.count() == 0
        assert CombatEncounter.query.count() == 0


def test_post_dm_validator_rejection_discards_deferred_bestiary_and_encounter(
    app,
    monkeypatch,
):
    ids = _seed_turn(app)
    resolution = _creature_resolution()
    persist_calls = 0

    def fake_persist(plan: CreatureResolutionPlan) -> dict:
        nonlocal persist_calls
        persist_calls += 1
        db.session.add(
            BestiaryEntry(
                workspace_id='owner',
                campaign_id=ids['campaign_id'],
                session_id=ids['session_id'],
                scope='session',
                creature_id='post_boundary_bandit',
                name='Post Boundary Bandit',
                source='generated',
                persistence='session',
                creature_json=safe_json_dumps(plan.result, {}),
            )
        )
        db.session.flush()
        return plan.result

    monkeypatch.setattr(
        combat_pipeline,
        'plan_creatures_for_encounter',
        lambda *_args, **_kwargs: CreatureResolutionPlan(
            result=resolution,
            pending_saves=(),
        ),
    )
    monkeypatch.setattr(combat_pipeline, 'plan_enemy_intents', _intent_plan)
    monkeypatch.setattr(
        combat_pipeline,
        'persist_creature_resolution_plan',
        fake_persist,
    )
    monkeypatch.setattr(
        turn_pipeline_module,
        'extract_post_dm_outcomes',
        lambda **_kwargs: {
            'proposedChanges': [],
            'uncertainChanges': [],
            'notes': ['test'],
            'debug': {'source': 'test'},
        },
    )

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        player = db.session.get(Player, ids['player_id'])
        turn = db.session.get(DmTurn, ids['turn_id'])
        assert session_obj is not None
        assert campaign is not None
        assert player is not None
        assert turn is not None
        resolved_state = _resolved_combat_state(ids['player_id'])
        session_obj.state_snapshot = safe_json_dumps(resolved_state, {})
        turn.dm_output = 'The bandit attacks again. Roll initiative.'
        turn.metadata_json = safe_json_dumps(
            {
                STATE_PIPELINE_METADATA_KEY: {
                    'version': STATE_PIPELINE_VERSION,
                    'actorId': f"player_{ids['player_id']}",
                    'stateBeforeDm': resolved_state,
                    'preDmValidation': {
                        'validatedActions': [],
                        'immediateChanges': [],
                    },
                    'immediateValidation': {
                        'accepted': [],
                        'rejected': [],
                        'modified': [],
                    },
                    'immediateAppliedChanges': [],
                    'combatAppliedChanges': [],
                    'pendingImmediateChanges': [],
                    'dmContextPacket': {},
                }
            },
            {},
        )
        db.session.flush()

        result = turn_pipeline_module.post_dm_pipeline(
            turn=turn,
            session_obj=session_obj,
            campaign=campaign,
            player=player,
            dm_response_text=turn.dm_output,
        )

        combat_rejections = [
            item
            for item in result['postValidation']['rejected']
            if isinstance(item.get('change'), dict)
            and item['change'].get('type') == 'combat.start'
        ]
        assert combat_rejections
        assert 'reopen resolved enemy' in combat_rejections[0]['reason']
        assert all(
            change.get('type') != 'combat.start'
            for change in result['postAppliedChanges']
        )
        assert persist_calls == 0
        assert BestiaryEntry.query.count() == 0
        assert CombatEncounter.query.count() == 0


def test_combat_finalization_requires_exact_prepared_change_id(app, monkeypatch):
    ids = _seed_turn(app)
    resolution_plan = CreatureResolutionPlan(
        result=_creature_resolution(),
        pending_saves=(),
    )
    prepare_result = combat_pipeline._defer_combat_prepare_finalization(
        {
            'changes': [
                {
                    'id': 'prepared_combat_start',
                    'type': 'combat.start',
                }
            ],
            'debug': {'combatEncounterId': None},
        },
        resolution_plan=resolution_plan,
    )
    persist_calls = 0

    def fake_persist(_plan):
        nonlocal persist_calls
        persist_calls += 1
        return resolution_plan.result

    monkeypatch.setattr(
        combat_pipeline,
        'persist_creature_resolution_plan',
        fake_persist,
    )

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        assert session_obj is not None
        assert campaign is not None

        encounter = combat_pipeline.finalize_combat_prepare(
            session_obj=session_obj,
            campaign=campaign,
            prepare_result=prepare_result,
            applied_changes=[
                {
                    'id': 'helper_combat_start',
                    'type': 'combat.start',
                }
            ],
            final_state=_active_combat_state(ids['player_id']),
        )

        assert encounter is None
        assert persist_calls == 0
        assert CombatEncounter.query.count() == 0
        assert prepare_result['debug']['combatEncounterId'] is None


def test_dynamic_combat_discards_creature_plan_before_intents_and_persistence_on_drift(
    app,
    monkeypatch,
):
    ids = _seed_turn(app)
    events: list[str] = []

    def stale_creature_plan(
        _request,
        *,
        workspace_id,
        before_provider_call=None,
        after_provider_call=None,
    ):
        assert workspace_id == 'owner'
        assert before_provider_call is not None
        assert after_provider_call is not None
        events.append('creature_plan')
        before_provider_call()
        events.append('creature_provider')
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    'UPDATE sessions '
                    'SET state_snapshot = :snapshot '
                    'WHERE session_id = :session_id'
                ),
                {
                    'snapshot': '{"revision":3}',
                    'session_id': ids['session_id'],
                },
            )
        after_provider_call()
        raise AssertionError('stale callback must abort creature planning')

    monkeypatch.setattr(
        combat_pipeline,
        'plan_creatures_for_encounter',
        stale_creature_plan,
    )
    monkeypatch.setattr(
        combat_pipeline,
        'plan_enemy_intents',
        lambda _combat: (_ for _ in ()).throw(
            AssertionError('intent planning must not run after creature drift')
        ),
    )
    monkeypatch.setattr(
        combat_pipeline,
        'persist_creature_resolution_plan',
        lambda _plan: (_ for _ in ()).throw(
            AssertionError('stale creature plans must never persist')
        ),
    )

    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        turn = db.session.get(DmTurn, ids['turn_id'])
        assert session_obj is not None
        assert campaign is not None
        assert turn is not None
        release, reload_orm = _boundary_callbacks(events)

        result = combat_pipeline.prepare_combat_for_turn(
            state={
                'currentScene': {'sceneType': 'exploration', 'combatState': 'none'},
                'playerCharacters': [_player(ids['player_id'])],
                'combat': {'status': 'none', 'participants': [], 'flags': {}},
            },
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            player_message='I attack the shape in the road.',
            workspace_id=campaign.workspace_id,
            before_creature_provider_call=release,
            reload_orm_after_creature_provider_call=reload_orm,
            before_intent_provider_call=release,
            reload_orm_after_intent_provider_call=reload_orm,
        )

        assert CombatEncounter.query.count() == 0

    assert events == ['creature_plan', 'release', 'creature_provider', 'reload']
    assert result['changes'] == []
    assert result['debug']['creaturePlanningStale'] is True
    assert 'changed during creature encounter planning' in result['debug']['creaturePlanningError']
