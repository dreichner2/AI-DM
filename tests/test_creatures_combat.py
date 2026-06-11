from __future__ import annotations

from aidm_server.combat.end_conditions import check_combat_end
import aidm_server.combat.enemy_brain as enemy_brain_module
from aidm_server.combat.intent_planner import plan_enemy_intents
from aidm_server.combat.pipeline import prepare_combat_for_turn
from aidm_server.combat.morale import apply_morale_event
from aidm_server.combat.state import combat_summary_for_dm, instantiate_creature, player_combat_participant
from aidm_server.creatures.balance import analyze_creature_balance, auto_scale_creature
from aidm_server.creatures.campaign_pack import generate_campaign_pack_bestiary
from aidm_server.creatures.core_bestiary import core_bestiary, core_creature
from aidm_server.creatures.evolution import evolve_creature
from aidm_server.creatures.generator import generate_new_creature
from aidm_server.creatures.repository import save_bestiary_entry, should_save_generated_creature
from aidm_server.creatures.resolver import resolve_creature_for_encounter
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.validation.validator import validate_state_changes, validated_changes_for_application
from aidm_server.database import db
from aidm_server.models import BestiaryEntry, Campaign, DmTurn, Session, safe_json_dumps
from tests.helpers import seed_world_campaign_player_session


def _player(name='Kael', hp=20):
    return {
        'id': 'player_1',
        'playerId': 1,
        'name': name,
        'level': 2,
        'health': {'currentHp': hp, 'maxHp': 24, 'tempHp': 0, 'conditions': []},
        'stats': {'armorClass': 13},
    }


def _combat_with(enemy):
    return {
        'status': 'active',
        'round': 1,
        'participants': [player_combat_participant(_player()), enemy],
        'battlefield': {'environmentType': 'forest', 'lighting': 'bright', 'visibility': 'clear'},
        'flags': {},
    }


def test_core_bestiary_entries_validate_and_balance():
    entries = core_bestiary()

    assert {'wolf', 'goblin_skirmisher', 'zombie', 'bandit_thug', 'cult_leader'}.issubset(
        {creature['id'] for creature in entries}
    )
    for creature in entries:
        assert creature['stats']['maxHp'] > 0
        assert creature['abilities']
        assert creature['behavior']['intelligenceProfile']
        assert creature['balance']['targetTier'] == creature['challengeTier']


def test_balance_analyzer_flags_and_autoscales_overpowered_creature():
    creature = {
        'id': 'too_much',
        'name': 'Too Much',
        'source': 'generated',
        'level': 1,
        'challengeTier': 'standard',
        'creatureType': 'custom',
        'stats': {'maxHp': 250, 'armorClass': 25},
        'abilities': [
            {
                'id': 'instant',
                'name': 'Instant End',
                'type': 'attack',
                'description': 'This can instantly kill outright.',
                'damage': {'dice': '8d12+20', 'type': 'necrotic'},
                'conditionsApplied': ['paralyzed'],
            }
        ],
    }

    balance = analyze_creature_balance(creature, party_level=1, party_size=4, target_difficulty='standard')
    scaled = auto_scale_creature(creature, balance, party_level=1, party_size=4, target_difficulty='standard')

    assert balance['estimatedTier'] == 'overpowered'
    assert balance['warnings']
    assert scaled['stats']['maxHp'] < 250
    assert scaled['stats']['armorClass'] < 25
    assert scaled['balance']['balanceAdjustments']


def test_resolver_uses_campaign_bestiary_before_core(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        campaign_id = ids['campaign_id']
        save_bestiary_entry(
            workspace_id='owner',
            campaign_id=campaign_id,
            scope='campaign',
            source='campaign_pack',
            persistence='campaign',
            creature={
                **core_creature('goblin_skirmisher'),
                'id': 'ashen_goblin',
                'name': 'Ashen Goblin',
                'visualTags': ['ash', 'goblin', 'skirmisher'],
            },
            tags=['ash', 'goblin', 'skirmisher'],
        )
        db.session.commit()

        result = resolve_creature_for_encounter(
            {
                'campaignId': campaign_id,
                'themeTags': ['ash', 'goblin'],
                'desiredRole': 'skirmisher',
                'desiredCreatureType': 'humanoid',
                'encounterPurpose': 'ambush',
                'partyLevel': 1,
                'partySize': 4,
                'difficulty': 'easy',
                'allowGeneration': False,
                'allowVariants': False,
            },
            workspace_id='owner',
        )

    assert result['resolutionMethod'] == 'campaign_bestiary_match'
    assert result['creature']['id'] == 'ashen_goblin'


def test_resolver_creates_variant_and_generation_fallback(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        variant = resolve_creature_for_encounter(
            {
                'campaignId': ids['campaign_id'],
                'themeTags': ['ash'],
                'desiredRole': 'beast',
                'desiredCreatureType': 'beast',
                'encounterPurpose': 'predator',
                'partyLevel': 1,
                'partySize': 4,
                'difficulty': 'easy',
                'allowGeneration': False,
                'allowVariants': True,
            },
            workspace_id='owner',
        )
        generated = resolve_creature_for_encounter(
            {
                'campaignId': ids['campaign_id'],
                'themeTags': ['crystal', 'clockwork', 'gravity'],
                'desiredRole': 'controller',
                'desiredCreatureType': 'aberration',
                'encounterPurpose': 'custom',
                'partyLevel': 3,
                'partySize': 4,
                'difficulty': 'standard',
                'descriptionHint': 'crystal gravity jailer',
                'allowGeneration': True,
                'allowVariants': False,
            },
            workspace_id='owner',
        )

    assert variant['resolutionMethod'] == 'generated_variant'
    assert variant['creature']['source'] == 'generated_variant'
    assert generated['resolutionMethod'] == 'generated_new'
    assert generated['creature']['source'] == 'generated'


def test_resolver_uses_region_then_core_before_generation_and_saves_meaningful_generated(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        save_bestiary_entry(
            workspace_id='owner',
            campaign_id=ids['campaign_id'],
            region_id='moonfen',
            scope='region',
            source='region_bestiary',
            persistence='region',
            creature={
                **core_creature('wolf'),
                'id': 'bogmaw_wolf',
                'name': 'Bogmaw Wolf',
                'visualTags': ['bog', 'wolf', 'predator'],
            },
            tags=['bog', 'wolf', 'predator'],
        )
        db.session.commit()

        region = resolve_creature_for_encounter(
            {
                'campaignId': ids['campaign_id'],
                'regionId': 'moonfen',
                'themeTags': ['bog', 'wolf'],
                'desiredRole': 'beast',
                'desiredCreatureType': 'beast',
                'encounterPurpose': 'predator',
                'partyLevel': 1,
                'partySize': 4,
                'difficulty': 'easy',
                'allowGeneration': True,
                'allowVariants': True,
            },
            workspace_id='owner',
        )
        core = resolve_creature_for_encounter(
            {
                'campaignId': ids['campaign_id'],
                'themeTags': ['goblin'],
                'desiredRole': 'skirmisher',
                'desiredCreatureType': 'humanoid',
                'encounterPurpose': 'ambush',
                'partyLevel': 1,
                'partySize': 4,
                'difficulty': 'easy',
                'allowGeneration': True,
                'allowVariants': True,
            },
            workspace_id='owner',
        )
        generated = resolve_creature_for_encounter(
            {
                'campaignId': ids['campaign_id'],
                'regionId': 'clockwork_vault',
                'themeTags': ['crystal', 'clockwork', 'gravity'],
                'desiredRole': 'controller',
                'desiredCreatureType': 'aberration',
                'encounterPurpose': 'ritual',
                'partyLevel': 3,
                'partySize': 4,
                'difficulty': 'standard',
                'descriptionHint': 'crystal gravity jailer',
                'allowGeneration': True,
                'allowVariants': False,
            },
            workspace_id='owner',
        )

        saved_generated = BestiaryEntry.query.filter_by(campaign_id=ids['campaign_id'], source='generated').count()

    assert region['resolutionMethod'] == 'region_bestiary_match'
    assert region['creature']['id'] == 'bogmaw_wolf'
    assert core['resolutionMethod'] == 'core_bestiary_match'
    assert core['generated'] is False
    assert generated['savedToBestiary'] is True
    assert saved_generated == 1


def test_disposable_generated_creature_is_not_saved_by_policy():
    assert should_save_generated_creature({'name': 'illusion', 'source': 'generated'}, {'temporary': True}) is False
    assert should_save_generated_creature({'name': 'Named Ember Hound', 'source': 'generated_variant', 'visualTags': ['ember']}, {}) is True


def test_intent_planner_goblin_flees_zombie_attacks_bandit_negotiates_and_wolf_hunts_weak_prey():
    goblin = instantiate_creature(core_creature('goblin_skirmisher'), instance_id='goblin_1')
    goblin['hp']['current'] = 1
    goblin_plan = plan_enemy_intents(_combat_with(goblin))

    zombie = instantiate_creature(core_creature('zombie'), instance_id='zombie_1')
    zombie['hp']['current'] = 1
    zombie_plan = plan_enemy_intents(_combat_with(zombie))

    bandit = instantiate_creature(core_creature('bandit_thug'), instance_id='bandit_1')
    bandit['morale'] = 24
    bandit_plan = plan_enemy_intents(_combat_with(bandit))

    wolf = instantiate_creature(core_creature('wolf'), instance_id='wolf_1')
    combat = _combat_with(wolf)
    combat['participants'][0]['hp'] = {'current': 5, 'max': 24}
    combat['participants'][0]['position'] = {'rangeBand': 'far'}
    combat['participants'].append(player_combat_participant(_player(name='Shield', hp=24)))
    wolf_plan = plan_enemy_intents(combat)

    leader = instantiate_creature(core_creature('bandit_captain'), instance_id='leader_1')
    leader['hp']['current'] = 0
    leader['isAlive'] = False
    goblin_with_dead_leader = _combat_with(goblin)
    goblin_with_dead_leader['participants'].append(leader)
    dead_leader_plan = plan_enemy_intents(goblin_with_dead_leader)

    assert goblin_plan['intents'][0]['intentType'] == 'retreat'
    assert goblin_plan['intents'][0]['selectionMethod'] == 'deterministic_scoring'
    assert isinstance(goblin_plan['intents'][0]['selectionScore'], int)
    assert goblin_plan['intentCandidates']['goblin_1'][0]['intentType'] == 'retreat'
    assert any(candidate['intentType'] == 'attack' for candidate in goblin_plan['intentCandidates']['goblin_1'])
    assert zombie_plan['intents'][0]['intentType'] == 'attack'
    assert bandit_plan['intents'][0]['intentType'] == 'negotiate'
    assert wolf_plan['intents'][0]['targetId'] == 'player_1'
    assert wolf_plan['combatFacts']['outnumbered'] is True
    assert dead_leader_plan['combatFacts']['leaderDead'] is True
    assert dead_leader_plan['intents'][0]['intentType'] == 'retreat'


def test_intent_planner_prefers_active_actor_when_targets_are_otherwise_equal():
    wolf = instantiate_creature(core_creature('wolf'), instance_id='wolf_1')
    combat = _combat_with(wolf)
    second_player = player_combat_participant(_player(name='Larin', hp=24))
    second_player['id'] = 'player_22'
    second_player['playerId'] = 22
    combat['participants'].append(second_player)
    combat['flags'] = {'activeActorId': 'player_22'}

    plan = plan_enemy_intents(combat)

    assert plan['intents'][0]['targetId'] == 'player_22'
    assert plan['combatFacts']['activeActorId'] == 'player_22'
    combat['participants'][-2]['currentIntent'] = plan['intents'][0]
    summary = combat_summary_for_dm(combat)
    assert 'targeting Larin' in summary['enemyIntentSummary']
    assert summary['enemyRequiredActions'][0]['targetName'] == 'Larin'


def test_intent_planner_repositions_when_players_are_in_different_explicit_zone():
    trickster = instantiate_creature(core_creature('bandit_thug'), instance_id='trickster_1')
    trickster['position'] = {'rangeBand': 'near', 'zoneId': 'inside_colosseum'}
    combat = _combat_with(trickster)
    combat['flags'] = {'combatDifficultyAI': {'allowSentientEnemyBrain': False}}
    combat['participants'][0]['position'] = {'rangeBand': 'near', 'zoneId': 'outside_colosseum'}

    plan = plan_enemy_intents(combat)

    assert plan['intents'][0]['intentType'] == 'reposition'
    assert plan['intents'][0].get('targetId') is None


def test_sentient_enemy_brain_uses_helper_contract_when_enabled(app, monkeypatch):
    requests = []

    class FakeBrainProvider:
        def generate(self, request):
            requests.append(request)
            return type(
                'Response',
                (),
                {
                    'text': (
                        '{"enemy_id":"bandit_1","goal":"survive and pressure the caster",'
                        '"current_emotion":"calculating","morale":61,'
                        '"target":{"id":"player_1","reason":"reachable wounded target"},'
                        '"movement_intent":{"goal":"hold near cover","rangeBand":"near"},'
                        '"action_intent":{"intentType":"use_ability","abilityId":"bandit_thug_club",'
                        '"requiresRoll":true,"preferredRoll":"attack vs AC"},'
                        '"reasoning_summary":"Use pressure without overextending.",'
                        '"requires_roll":true,"preferred_roll":"attack vs AC",'
                        '"fallback_if_blocked":"reposition"}'
                    ),
                    'provider': 'fake',
                    'model': 'deepseek-v4-pro',
                },
            )()

    monkeypatch.setattr(enemy_brain_module, 'get_helper_provider', lambda task=None: FakeBrainProvider())
    bandit = instantiate_creature(core_creature('bandit_thug'), instance_id='bandit_1')
    combat = _combat_with(bandit)

    with app.app_context():
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_IN_TESTS'] = True
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_ENABLED'] = 'true'
        plan = plan_enemy_intents(combat)

    assert requests
    assert 'Return one JSON object' in requests[0].prompt
    assert plan['intents'][0]['selectionMethod'] == 'sentient_enemy_brain'
    assert plan['intents'][0]['brainSource'] == 'deepseek-v4-pro'
    assert plan['intents'][0]['targetId'] == 'player_1'
    assert plan['intents'][0]['brain']['preferred_roll'] == 'attack vs AC'


def test_combat_state_changes_validate_and_apply():
    state = {
        'currentScene': {'sceneType': 'exploration', 'dangerLevel': 0, 'combatState': 'none'},
        'playerCharacters': [_player()],
        'stateChangeLedger': [],
    }
    wolf = instantiate_creature(core_creature('wolf'), instance_id='enemy_wolf_1')
    change = {
        'id': 'combat_start_1',
        'type': 'combat.start',
        'combat': {
            'status': 'active',
            'round': 1,
            'participants': [player_combat_participant(_player()), wolf],
            'battlefield': {'environmentType': 'forest'},
            'flags': {},
        },
    }

    validation = validate_state_changes(state=state, changes=[change])
    applied = apply_state_changes(state, validated_changes_for_application(validation))
    damage_validation = validate_state_changes(
        state=applied['nextState'],
        changes=[
            {
                'id': 'wolf_down',
                'type': 'combat.participant.update',
                'participantId': 'enemy_wolf_1',
                'hp': {'current': 0, 'max': wolf['hp']['max']},
                'isAlive': False,
                'isConscious': False,
            },
            {'id': 'combat_end_1', 'type': 'combat.end', 'status': 'ended'},
        ],
    )
    final = apply_state_changes(applied['nextState'], validated_changes_for_application(damage_validation))

    assert not validation['rejected']
    assert applied['nextState']['combat']['status'] == 'active'
    assert applied['nextState']['currentScene']['combatState'] == 'active'
    assert final['nextState']['combat']['status'] == 'ended'
    assert final['nextState']['currentScene']['combatState'] == 'resolved'
    assert final['nextState']['currentScene']['sceneType'] == 'exploration'


def test_combat_end_clears_enemy_intent_and_duplicate_resolved_restart_is_rejected():
    surrendered = instantiate_creature(core_creature('bandit_thug'), instance_id='enemy_mirror_trickster_1')
    surrendered['conditions'] = ['surrendered']
    surrendered['currentIntent'] = {'intentType': 'attack', 'targetId': 'player_1'}
    state = {
        'currentScene': {'sceneType': 'combat', 'dangerLevel': 8, 'combatState': 'active'},
        'combat': _combat_with(surrendered),
        'stateChangeLedger': [],
    }
    final = apply_state_changes(
        state,
        [
            {
                'id': 'combat_end_surrender',
                'type': 'combat.end',
                'status': 'ended',
                'summary': 'The Trickster surrenders.',
            }
        ],
    )
    enemy = next(participant for participant in final['nextState']['combat']['participants'] if participant['team'] == 'enemy')

    assert enemy['currentIntent'] is None
    assert final['nextState']['combat']['status'] == 'ended'
    assert final['nextState']['currentScene']['combatState'] == 'resolved'

    duplicate = instantiate_creature(core_creature('bandit_thug'), instance_id='enemy_mirror_trickster_2')
    validation = validate_state_changes(
        state=final['nextState'],
        changes=[
            {
                'id': 'combat_restart_duplicate',
                'type': 'combat.start',
                'combat': {
                    'status': 'active',
                    'round': 1,
                    'participants': [player_combat_participant(_player()), duplicate],
                },
            }
        ],
    )

    assert validation['rejected']
    assert 'reopen resolved enemy' in validation['rejected'][0]['reason']


def test_prepare_combat_does_not_restart_resolved_combat_from_non_hostile_fight_reference(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        assert session_obj is not None
        assert campaign is not None
        session_obj.state_snapshot = safe_json_dumps(
            {
                'currentScene': {'sceneType': 'combat', 'combatState': 'resolved', 'dangerLevel': 1},
                'playerCharacters': [_player()],
                'combat': {'status': 'ended', 'round': 1, 'participants': []},
            },
            {},
        )
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='Loki says to Himeros: we cannot win in a fair fight.',
        )
        db.session.add(turn)
        db.session.flush()

        result = prepare_combat_for_turn(
            state={
                'currentScene': {'sceneType': 'combat', 'combatState': 'resolved', 'dangerLevel': 1},
                'playerCharacters': [_player()],
                'combat': {'status': 'ended', 'round': 1, 'participants': []},
            },
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            player_message='Loki says to Himeros: we cannot win in a fair fight.',
            workspace_id=campaign.workspace_id,
        )

    assert result['changes'] == []
    assert result['debug']['triggered'] is False


def test_fine_combat_changes_morale_events_and_round_advance_apply():
    wolf = instantiate_creature(core_creature('wolf'), instance_id='enemy_wolf_1')
    state = {
        'currentScene': {'sceneType': 'combat', 'dangerLevel': 8, 'combatState': 'active'},
        'combat': _combat_with(wolf),
        'stateChangeLedger': [],
    }

    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'move_wolf',
                'type': 'combat.move',
                'participantId': 'enemy_wolf_1',
                'toRangeBand': 'far',
            },
            {
                'id': 'frighten_wolf',
                'type': 'combat.condition.add',
                'participantId': 'enemy_wolf_1',
                'condition': 'frightened',
            },
            {
                'id': 'wolf_bite_used',
                'type': 'combat.ability.mark_used',
                'participantId': 'enemy_wolf_1',
                'abilityId': 'wolf_bite',
            },
            {
                'id': 'wolf_heavy_damage',
                'type': 'combat.morale.event',
                'participantId': 'enemy_wolf_1',
                'event': 'took_heavy_damage',
            },
            {
                'id': 'round_two',
                'type': 'combat.round.advance',
                'round': 2,
                'summary': 'The wolf breaks from melee.',
            },
        ],
    )
    applied = apply_state_changes(state, validated_changes_for_application(validation))
    enemy = next(participant for participant in applied['nextState']['combat']['participants'] if participant['id'] == 'enemy_wolf_1')

    assert not validation['rejected']
    assert enemy['position']['rangeBand'] == 'far'
    assert 'frightened' in enemy['conditions']
    assert enemy['morale'] == apply_morale_event(wolf, 'took_heavy_damage')
    assert enemy['abilities'][0]['lastUsedRound'] == 1
    assert applied['nextState']['combat']['round'] == 2


def test_combat_end_conditions_cover_defeat_flee_surrender_and_objective():
    wolf = instantiate_creature(core_creature('wolf'), instance_id='enemy_wolf_1')
    defeated = _combat_with({**wolf, 'hp': {'current': 0, 'max': wolf['hp']['max']}, 'isAlive': False})
    fled = _combat_with({**wolf, 'conditions': ['fled'], 'isConscious': False})
    surrendered = _combat_with({**wolf, 'conditions': ['surrendered']})
    objective = _combat_with(wolf)
    objective['flags'] = {'objectiveStatus': 'completed'}

    assert check_combat_end(defeated) == 'all_enemies_defeated'
    assert check_combat_end(fled) == 'enemies_fled'
    assert check_combat_end(surrendered) == 'enemies_surrendered'
    assert check_combat_end(objective) == 'objective_completed'


def test_boss_tactics_and_difficulty_settings_drive_intent_choice():
    boss = instantiate_creature(core_creature('cult_leader'), instance_id='enemy_cult_leader_1')
    combat = _combat_with(boss)
    combat['battlefield']['hazards'] = [{'id': 'ritual_fire', 'name': 'Ritual Fire'}]
    combat['flags'] = {'combatDifficultyAI': {'tacticalLevel': 'smart', 'allowBossTacticsHelper': True}}

    plan = plan_enemy_intents(combat)

    assert plan['difficultyAI']['tacticalLevel'] == 'smart'
    assert plan['intents'][0]['intentType'] == 'use_environment'
    assert plan['intents'][0]['tacticSource'] == 'deterministic'
    assert plan['intents'][0]['selectionScore'] == 86
    assert plan['intentCandidates']['enemy_cult_leader_1'][0]['intentType'] == 'use_environment'


def test_campaign_pack_generation_and_evolution_create_persistent_creatures():
    pack = generate_campaign_pack_bestiary({'title': 'Ashen Crown', 'themes': ['ash', 'choir'], 'count': 6})
    evolved = evolve_creature(
        core_creature('goblin_skirmisher'),
        {'eventTags': ['fire', 'scarred'], 'grudgeTargetId': 'player_1', 'reason': 'Survived the burning camp.'},
    )

    assert len(pack) == 6
    assert all(creature['source'] == 'campaign_pack' for creature in pack)
    assert any(creature['behavior']['combatRole'] == 'boss' for creature in pack)
    assert evolved['source'] == 'evolved'
    assert evolved['baseCreatureId'] == 'goblin_skirmisher'
    assert evolved['combatMemorySeed']['personalGrudgeTargetId'] == 'player_1'


def test_creature_api_endpoints(client, app):
    ids = seed_world_campaign_player_session(app)

    core = client.get('/api/bestiary/core').get_json()
    resolve = client.post(
        '/api/creatures/resolve',
        json={
            'campaignId': ids['campaign_id'],
            'themeTags': ['goblin'],
            'desiredRole': 'skirmisher',
            'desiredCreatureType': 'humanoid',
            'encounterPurpose': 'ambush',
            'partyLevel': 1,
            'partySize': 4,
            'difficulty': 'easy',
            'allowGeneration': False,
            'allowVariants': False,
        },
    ).get_json()
    combat = client.post(
        f"/api/sessions/{ids['session_id']}/combat/start",
        json={'creature': core_creature('wolf'), 'enemyCount': 1},
    ).get_json()

    assert core['entries']
    assert resolve['creature']['id'] == 'goblin_skirmisher'
    assert combat['combat']['status'] == 'active'


def test_creature_deep_api_endpoints_for_pack_evolution_morale_and_debug(client, app):
    ids = seed_world_campaign_player_session(app)

    pack = client.post(
        f"/api/campaigns/{ids['campaign_id']}/bestiary/generate-pack",
        json={'themes': ['ash', 'crown'], 'count': 4},
    ).get_json()
    evolved = client.post(
        '/api/creatures/evolve',
        json={
            'campaignId': ids['campaign_id'],
            'sessionId': ids['session_id'],
            'baseCreature': core_creature('goblin_skirmisher'),
            'eventContext': {'eventTags': ['fire'], 'grudgeTargetId': 'player_1'},
        },
    ).get_json()
    combat = client.post(
        f"/api/sessions/{ids['session_id']}/combat/start",
        json={'creature': core_creature('wolf'), 'enemyCount': 1},
    ).get_json()
    enemy_id = next(participant['id'] for participant in combat['combat']['participants'] if participant['team'] == 'enemy')
    morale = client.post(
        f"/api/sessions/{ids['session_id']}/combat/apply-morale-event",
        json={'participantId': enemy_id, 'event': 'took_heavy_damage'},
    ).get_json()
    debug = client.get(f"/api/sessions/{ids['session_id']}/combat/debug").get_json()

    assert len(pack['entries']) == 4
    assert evolved['entry']['source'] == 'evolved'
    assert not morale['validation']['rejected']
    assert debug['events']
