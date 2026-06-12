from __future__ import annotations

from copy import deepcopy
import json
import re
import threading
import time

from flask import has_app_context

import aidm_server.combat.boss_tactics as boss_tactics_module
import aidm_server.combat.intent_planner as intent_planner_module
from aidm_server.combat.end_conditions import check_combat_end
import aidm_server.combat.enemy_brain as enemy_brain_module
from aidm_server.combat.evaluation import run_combat_helper_evaluation, summarize_combat_helper_plan
from aidm_server.combat.intent_planner import plan_enemy_intents
from aidm_server.combat.pipeline import DIRECT_HOSTILE_ACTION_PATTERN, combat_turn_advance_change, prepare_combat_for_turn
from aidm_server.combat.morale import apply_morale_event
from aidm_server.combat.state import combat_summary_for_dm, combat_turn_context, instantiate_creature, normalize_battlefield, player_combat_participant
from aidm_server.creatures.balance import analyze_creature_balance, auto_scale_creature
from aidm_server.creatures.campaign_pack import generate_campaign_pack_bestiary
from aidm_server.creatures.core_bestiary import core_bestiary, core_creature
from aidm_server.creatures.evolution import evolve_creature
from aidm_server.creatures.repository import save_bestiary_entry, should_save_generated_creature
from aidm_server.creatures.resolver import default_request_from_session, normalize_creature_request, resolve_creature_for_encounter, resolve_creatures_for_encounter
from aidm_server.creatures.schemas import normalize_creature_definition
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


def _player_participant(player_id: int, name: str, hp: int = 20) -> dict:
    actor = _player(name=name, hp=hp)
    actor['id'] = f'player_{player_id}'
    actor['playerId'] = player_id
    return player_combat_participant(actor)


def test_combat_turn_context_orders_players_then_enemy_block():
    enemy_1 = instantiate_creature(core_creature('bandit_thug'), instance_id='enemy_bandit_1')
    enemy_2 = instantiate_creature(core_creature('wolf'), instance_id='enemy_wolf_1')
    combat = {
        'status': 'active',
        'round': 1,
        'turnIndex': 0,
        'participants': [
            _player_participant(1, 'Loki'),
            _player_participant(2, 'Himeros'),
            enemy_1,
            enemy_2,
        ],
    }

    first_turn = combat_turn_context(combat)

    assert first_turn['turnOrderIds'] == ['player_1', 'player_2', 'enemy_bandit_1', 'enemy_wolf_1']
    assert first_turn['currentActor']['id'] == 'player_1'
    assert first_turn['immediateNextActor']['id'] == 'player_2'
    assert first_turn['enemyTurnBlock'] == []
    assert first_turn['nextTurnIndex'] == 1
    assert first_turn['nextRound'] == 1

    combat['turnIndex'] = 1
    last_player_turn = combat_turn_context(combat)

    assert last_player_turn['currentActor']['id'] == 'player_2'
    assert [actor['id'] for actor in last_player_turn['enemyTurnBlock']] == ['enemy_bandit_1', 'enemy_wolf_1']
    assert last_player_turn['handoffActor']['id'] == 'player_1'
    assert last_player_turn['nextTurnIndex'] == 0
    assert last_player_turn['nextRound'] == 2
    assert 'enemy turns in order' in last_player_turn['turnInstruction']

    summary = combat_summary_for_dm(combat)
    assert summary['currentTurn']['id'] == 'player_2'
    assert summary['handoffActor']['id'] == 'player_1'


def test_prepare_combat_uses_roster_turn_not_submitting_player_for_active_combat(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        assert session_obj is not None
        assert campaign is not None
        submitted_player_id = ids['player_id']
        active_player_id = submitted_player_id + 1000
        enemy = instantiate_creature(core_creature('bandit_thug'), instance_id='enemy_bandit_1')
        state = {
            'currentScene': {'sceneType': 'combat', 'combatState': 'active', 'dangerLevel': 8},
            'playerCharacters': [
                {'id': f'player_{active_player_id}', 'playerId': active_player_id, 'name': 'Loki', 'health': {'currentHp': 20, 'maxHp': 20}, 'stats': {'armorClass': 13}},
                {'id': f'player_{submitted_player_id}', 'playerId': submitted_player_id, 'name': 'Goliath', 'health': {'currentHp': 17, 'maxHp': 17}, 'stats': {'armorClass': 14}},
            ],
            'combat': {
                'status': 'active',
                'round': 1,
                'turnIndex': 0,
                'participants': [
                    _player_participant(active_player_id, 'Loki'),
                    _player_participant(submitted_player_id, 'Goliath', hp=17),
                    enemy,
                ],
                'flags': {'activeActorId': f'player_{submitted_player_id}'},
            },
        }
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=submitted_player_id,
            player_input='Goliath swings again.',
        )
        db.session.add(turn)
        db.session.flush()

        result = prepare_combat_for_turn(
            state=state,
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            player_message='Goliath swings again.',
            workspace_id=campaign.workspace_id,
        )

    update = next(change for change in result['changes'] if change['type'] == 'combat.update')
    assert update['turnIndex'] == 0
    assert update['flags']['activeActorId'] == f'player_{active_player_id}'
    assert update['flags']['submittedActorId'] == f'player_{submitted_player_id}'
    assert update['flags']['offTurnSubmission'] is True
    assert result['combatContext']['currentTurn']['id'] == f'player_{active_player_id}'
    assert result['combatContext']['nextActor']['id'] == f'player_{submitted_player_id}'


def test_combat_turn_advance_skips_enemy_block_to_next_player_round():
    enemy_1 = instantiate_creature(core_creature('bandit_thug'), instance_id='enemy_bandit_1')
    enemy_2 = instantiate_creature(core_creature('wolf'), instance_id='enemy_wolf_1')
    state = {
        'currentScene': {'sceneType': 'combat', 'combatState': 'active'},
        'combat': {
            'status': 'active',
            'round': 1,
            'turnIndex': 1,
            'participants': [
                _player_participant(1, 'Loki'),
                _player_participant(2, 'Himeros'),
                enemy_1,
                enemy_2,
            ],
            'flags': {
                'activeActorId': 'player_2',
                'submittedActorId': 'player_2',
                'offTurnSubmission': False,
            },
        },
    }
    turn = DmTurn(turn_id=44, session_id=1, campaign_id=1, player_id=2, player_input='Himeros attacks.')

    change = combat_turn_advance_change(state=state, turn=turn, actor_id='player_2')

    assert change is not None
    assert change['type'] == 'combat.update'
    assert change['turnIndex'] == 0
    assert change['round'] == 2
    assert change['flags']['activeActorId'] == 'player_1'
    assert change['flags']['lastResolvedActorId'] == 'player_2'
    assert change['flags']['lastEnemyTurnBlock'] == ['enemy_bandit_1', 'enemy_wolf_1']


def test_direct_hostile_action_pattern_covers_thrown_or_smashing_attacks():
    assert DIRECT_HOSTILE_ACTION_PATTERN.search('I throw a javelin at The Thunderer now')
    assert DIRECT_HOSTILE_ACTION_PATTERN.search('I smash the champion with both fists')
    assert DIRECT_HOSTILE_ACTION_PATTERN.search('I leap and try to smack Thor')
    assert DIRECT_HOSTILE_ACTION_PATTERN.search('I grab his head and try to crush it')
    assert DIRECT_HOSTILE_ACTION_PATTERN.search('I stomp the crawling ghoul')


def test_default_encounter_request_prefers_hostile_scene_npc_over_weapon_phrase(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        assert session_obj is not None
        assert campaign is not None
        state = {
            'currentScene': {
                'name': 'Colosseum arena',
                'sceneType': 'combat',
                'combatState': 'active',
                'characterPositions': {'thunderer': 'near'},
            },
            'playerCharacters': [_player()],
            'npcs': [
                {
                    'id': 'thunderer',
                    'name': 'The Thunderer',
                    'disposition': 'hostile',
                    'status': 'met',
                    'locationId': 'colosseum',
                }
            ],
        }

        request = default_request_from_session(
            session_obj=session_obj,
            campaign=campaign,
            state=state,
            player_message='I throw another javelin at the thunderes head',
        )

    assert request['allowGeneration'] is False
    creature = request['encounterDefinedCreatures'][0]
    assert creature['id'] == 'thunderer'
    assert creature['name'] == 'The Thunderer'
    assert 'thunderer' in creature['aliases']


def test_prepare_combat_starts_known_thunderer_npc_instead_of_generated_thrower(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        assert session_obj is not None
        assert campaign is not None
        state = {
            'currentScene': {
                'name': 'Colosseum arena',
                'sceneType': 'combat',
                'combatState': 'active',
                'characterPositions': {'thunderer': 'near'},
            },
            'playerCharacters': [_player()],
            'npcs': [
                {
                    'id': 'thunderer',
                    'name': 'The Thunderer',
                    'disposition': 'hostile',
                    'status': 'met',
                    'locationId': 'colosseum',
                }
            ],
            'combat': {
                'status': 'ended',
                'round': 1,
                'participants': [
                    _player_participant(1, 'Kael'),
                    {
                        **instantiate_creature(core_creature('bandit_thug'), instance_id='enemy_old_thrower_1'),
                        'name': 'Thunder Javelin Thrower',
                        'definitionId': 'thunder_javelin_thrower',
                        'conditions': ['fled'],
                        'isAlive': False,
                    },
                ],
            },
        }
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I grab his head and try to crush it',
        )
        db.session.add(turn)
        db.session.flush()

        result = prepare_combat_for_turn(
            state=state,
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            player_message='I grab his head and try to crush it',
            workspace_id=campaign.workspace_id,
        )

    start = next(change for change in result['changes'] if change['type'] == 'combat.start')
    enemies = [participant for participant in start['combat']['participants'] if participant.get('team') == 'enemy']
    assert len(enemies) == 1
    assert enemies[0]['definitionId'] == 'thunderer'
    assert enemies[0]['name'] == 'The Thunderer'
    assert 'thunderer' in enemies[0]['aliases']
    assert 'Thunder Javelin Thrower' not in result['combatContext']['turnOrderText']


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


def test_encounter_resolver_composes_explicit_groups(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        result = resolve_creatures_for_encounter(
            {
                'campaignId': ids['campaign_id'],
                'sessionId': ids['session_id'],
                'encounterPurpose': 'guard',
                'partyLevel': 2,
                'partySize': 4,
                'difficulty': 'standard',
                'allowGeneration': False,
                'allowVariants': False,
                'enemyGroups': [
                    {'label': 'wolf screen', 'count': 2, 'creature': core_creature('wolf')},
                    {
                        'label': 'goblin handler',
                        'count': 1,
                        'themeTags': ['goblin'],
                        'desiredRole': 'skirmisher',
                        'desiredCreatureType': 'humanoid',
                        'encounterPurpose': 'ambush',
                        'difficulty': 'easy',
                    },
                ],
            },
            workspace_id='owner',
        )

    assert result['resolutionMethod'] == 'encounter_composed'
    assert result['totalEnemies'] == 3
    assert [group['count'] for group in result['groups']] == [2, 1]
    assert result['groups'][0]['resolutionMethod'] == 'encounter_defined'
    assert result['groups'][1]['creature']['id'] == 'goblin_skirmisher'
    assert result['encounterGoal']['type'] == 'defend_location'


def test_disposable_generated_creature_is_not_saved_by_policy():
    assert should_save_generated_creature({'name': 'illusion', 'source': 'generated'}, {'temporary': True}) is False
    assert should_save_generated_creature({'name': 'Named Ember Hound', 'source': 'generated_variant', 'visualTags': ['ember']}, {}) is True


def test_creature_request_normalizes_string_booleans_and_enemy_count_bounds():
    request = normalize_creature_request(
        {
            'allowGeneration': 'false',
            'allowVariants': '0',
            'saveGenerated': 'off',
            'enemyCount': 99,
        }
    )

    assert request['allowGeneration'] is False
    assert request['allowVariants'] is False
    assert request['saveGenerated'] is False
    assert request['enemyCount'] == 24


def test_battlefield_normalization_shapes_hazards_cover_exits_and_interactables():
    battlefield = normalize_battlefield(
        {
            'environmentType': 'Dungeon Room',
            'lighting': 'dark',
            'visibility': 'smoke',
            'zones': [{'name': 'Upper Gallery', 'description': 'A balcony above the ritual floor.'}],
            'hazards': [
                {
                    'name': 'Open Fire Pit',
                    'description': 'A cracked pit of coals.',
                    'effect': 'fire_damage_if_entered',
                    'damage': {'dice': '1d6', 'type': 'fire'},
                }
            ],
            'cover': [{'name': 'Stone Pillar', 'cover_type': 'three quarters', 'zone_id': 'upper_gallery'}],
            'exits': [{'name': 'North Tunnel', 'blocked': 'false', 'destination_location_id': 'moonfen'}],
            'interactables': [{'name': 'Loose Chandelier', 'possible_uses': ['drop_on_target']}],
        }
    )

    assert battlefield['environmentType'] == 'dungeon_room'
    assert battlefield['hazards'][0]['damage'] == {'dice': '1d6', 'type': 'fire'}
    assert battlefield['cover'][0]['coverType'] == 'three_quarters'
    assert battlefield['exits'][0]['blocked'] is False
    assert battlefield['interactables'][0]['possibleUses'] == ['drop_on_target']


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
    assert goblin_plan['intents'][0]['candidateId'].startswith('turn_1.goblin_1.cand_')
    assert goblin_plan['intents'][0]['resolver']['resolverType'] == 'engine_intent_bundle_v1'
    assert goblin_plan['intents'][0]['dryRun']['canResolveNow'] is True
    assert goblin_plan['intentCandidates']['goblin_1'][0]['intentType'] == 'retreat'
    assert goblin_plan['intentCandidates']['goblin_1'][0]['isFallbackCandidate'] is True
    assert goblin_plan['intentCandidates']['goblin_1'][0]['candidateId'] == goblin_plan['intents'][0]['candidateId']
    assert goblin_plan['intentCandidates']['goblin_1'][0]['resolverType'] == 'engine_intent_bundle_v1'
    assert 'resolver' not in goblin_plan['intentCandidates']['goblin_1'][0]
    assert isinstance(goblin_plan['intentCandidates']['goblin_1'][0]['matcherScore'], float)
    assert 'base_deterministic_score' in goblin_plan['intentCandidates']['goblin_1'][0]['matcherSignals']
    assert any(candidate['intentType'] == 'attack' for candidate in goblin_plan['intentCandidates']['goblin_1'])
    assert zombie_plan['intents'][0]['intentType'] == 'attack'
    assert bandit_plan['intents'][0]['intentType'] == 'negotiate'
    assert wolf_plan['intents'][0]['targetId'] == 'player_1'
    assert wolf_plan['combatFacts']['outnumbered'] is True
    assert dead_leader_plan['combatFacts']['leaderDead'] is True
    assert dead_leader_plan['intents'][0]['intentType'] == 'retreat'


def test_schema_aliases_and_survival_rules_drive_planner_behavior():
    creature = normalize_creature_definition(
        {
            'id': 'schematic_bandit',
            'name': 'Schematic Bandit',
            'source': 'campaign_pack',
            'creatureType': 'humanoid',
            'challengeTier': 'standard',
            'stats': {'maxHp': 18, 'armorClass': 12},
            'abilities': [{'id': 'club', 'name': 'Club', 'damage': {'dice': '1d6+1', 'type': 'bludgeoning'}}],
            'behavior': {
                'intelligenceProfile': 'average',
                'combatRole': 'brute',
                'primaryGoal': 'steal_item',
                'aggression': 45,
                'selfPreservation': 70,
                'morale': 55,
                'targetPriority': ['lowest_hp', 'nearest_target', 'last_attacker'],
                'survivalRules': {
                    'fightToDeath': False,
                    'surrenderBelowHpPercent': 15,
                    'callForHelpBelowHpPercent': 60,
                },
            },
        },
        source='campaign_pack',
    )
    enemy = instantiate_creature(creature, instance_id='schematic_bandit_1')
    enemy['hp']['current'] = 9
    combat = _combat_with(enemy)
    combat['flags'] = {'combatDifficultyAI': {'allowFocusFire': False, 'allowSentientEnemyBrain': False}}
    combat['participants'][0]['hp'] = {'current': 6, 'max': 24}
    second_player = player_combat_participant(_player(name='Shield', hp=24))
    second_player['id'] = 'player_2'
    combat['participants'].append(second_player)

    plan = plan_enemy_intents(combat)

    assert creature['behavior']['targetPriority'] == ['wounded', 'nearest', 'last_damaged_by']
    assert creature['behavior']['survivalRules']['surrenderBelowHpPercent'] == 15
    assert plan['intents'][0]['intentType'] == 'call_reinforcements'
    assert any(candidate['targetId'] == 'player_1' for candidate in plan['intentCandidates']['schematic_bandit_1'])


def test_non_boss_enemy_uses_battlefield_hazard_when_objective_depends_on_terrain():
    cultist = instantiate_creature(core_creature('cultist'), instance_id='cultist_1')
    combat = _combat_with(cultist)
    combat['battlefield']['hazards'] = [{'id': 'ritual_fire', 'name': 'Ritual Fire'}]
    combat['battlefield']['interactables'] = [{'id': 'hanging_brazier', 'name': 'Hanging Brazier'}]
    combat['flags'] = {
        'combatDifficultyAI': {
            'allowSentientEnemyBrain': False,
            'allowEnvironmentalHazards': True,
        }
    }

    plan = plan_enemy_intents(combat)

    assert plan['intents'][0]['intentType'] == 'use_environment'
    assert plan['intents'][0]['targetId'] == 'player_1'
    assert plan['intents'][0]['selectionMethod'] == 'deterministic_scoring'
    assert 'tacticSource' not in plan['intents'][0]
    assert plan['combatFacts']['battlefieldHazards'] == 1
    assert plan['combatFactsByEnemy']['cultist_1']['hazardIds'] == ['ritual_fire']
    assert plan['intentCandidates']['cultist_1'][0]['intentType'] == 'use_environment'


def test_trained_enemy_uses_cover_when_smart_tactics_make_exposure_costly():
    mercenary = instantiate_creature(core_creature('mercenary'), instance_id='mercenary_1')
    mercenary['hp']['current'] = 20
    combat = _combat_with(mercenary)
    combat['battlefield']['cover'] = [{'id': 'stone_pillar', 'name': 'Stone Pillar', 'coverType': 'half'}]
    combat['flags'] = {
        'combatDifficultyAI': {
            'tacticalLevel': 'smart',
            'allowSentientEnemyBrain': False,
        }
    }

    plan = plan_enemy_intents(combat)

    assert plan['intents'][0]['intentType'] == 'defend'
    assert plan['intents'][0]['movementGoal'] == 'move to Stone Pillar'
    assert plan['combatFactsByEnemy']['mercenary_1']['coverIds'] == ['stone_pillar']


def test_guard_interposes_to_protect_wounded_leader():
    guard = instantiate_creature(core_creature('guard'), instance_id='guard_1')
    guard['behavior'] = {
        **guard['behavior'],
        'primaryGoal': 'protect_leader',
        'loyalty': 90,
        'discipline': 70,
    }
    leader = instantiate_creature(core_creature('bandit_captain'), instance_id='leader_1')
    leader['hp']['current'] = 8
    combat = _combat_with(guard)
    combat['participants'].append(leader)
    combat['flags'] = {'combatDifficultyAI': {'allowSentientEnemyBrain': False}}

    plan = plan_enemy_intents(combat)
    guard_intent = next(intent for intent in plan['intents'] if intent['enemyId'] == 'guard_1')

    assert guard_intent['intentType'] == 'protect_ally'
    assert guard_intent['targetId'] == 'leader_1'
    assert plan['combatFactsByEnemy']['guard_1']['allyIds'] == ['leader_1']


def test_evolved_creature_instance_seeds_grudge_memory_for_targeting():
    evolved = evolve_creature(
        core_creature('goblin_skirmisher'),
        {'eventTags': ['fire'], 'grudgeTargetId': 'player_2', 'reason': 'Survived a humiliating defeat.'},
    )
    enemy = instantiate_creature(evolved, instance_id='scarred_goblin_1')
    combat = _combat_with(enemy)
    second_player = player_combat_participant(_player(name='Mira', hp=24))
    second_player['id'] = 'player_2'
    combat['participants'].append(second_player)
    combat['flags'] = {'combatDifficultyAI': {'allowSentientEnemyBrain': False}}

    plan = plan_enemy_intents(combat)

    assert enemy['memory']['personalGrudgeTargetId'] == 'player_2'
    assert plan['intents'][0]['targetId'] == 'player_2'
    assert plan['combatFactsByEnemy']['scarred_goblin_1']['visibleTargetIds'] == ['player_1', 'player_2']


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


def test_sentient_enemy_brain_gate_includes_humanlike_and_intelligent_enemies_but_skips_animals():
    settings = {'allowSentientEnemyBrain': True}
    bandit = instantiate_creature(core_creature('bandit_thug'), instance_id='bandit_1')
    goblin = instantiate_creature(core_creature('goblin_skirmisher'), instance_id='goblin_1')
    wolf = instantiate_creature(core_creature('wolf'), instance_id='wolf_1')
    zombie = instantiate_creature(core_creature('zombie'), instance_id='zombie_1')
    awakened_wolf = instantiate_creature(core_creature('wolf'), instance_id='awakened_wolf_1')
    awakened_wolf['behavior'] = {**awakened_wolf['behavior'], 'intelligenceProfile': 'average'}

    assert enemy_brain_module.should_use_sentient_enemy_brain(bandit, settings) is True
    assert enemy_brain_module.should_use_sentient_enemy_brain(goblin, settings) is True
    assert enemy_brain_module.should_use_sentient_enemy_brain(awakened_wolf, settings) is True
    assert enemy_brain_module.should_use_sentient_enemy_brain(wolf, settings) is False
    assert enemy_brain_module.should_use_sentient_enemy_brain(zombie, settings) is False
    assert enemy_brain_module.should_use_sentient_enemy_brain(bandit, {'allowSentientEnemyBrain': False}) is False


def test_sentient_enemy_brain_uses_helper_contract_when_enabled(app, monkeypatch):
    requests = []

    class FakeBrainProvider:
        def generate(self, request):
            requests.append(request)
            match = re.search(r'LEGAL_CANDIDATE_SELECTION_INPUT:\n(\{.*\})', request.prompt, re.S)
            assert match
            selector_input = json.loads(match.group(1))
            candidate_id = selector_input['legal_candidates'][0]['candidate_id']
            return type(
                'Response',
                (),
                {
                    'text': json.dumps(
                        {
                            'selected_candidate_id': candidate_id,
                            'backup_candidate_ids': [],
                            'reasoning_summary': 'Use the best legal engine-authored candidate.',
                            'confidence': 0.82,
                        }
                    ),
                    'provider': 'fake',
                    'model': 'deepseek-v4-pro',
                },
            )()

    monkeypatch.setattr(enemy_brain_module, 'get_helper_provider', lambda task=None: FakeBrainProvider())
    bandit = instantiate_creature(core_creature('bandit_thug'), instance_id='bandit_1')
    combat = _combat_with(bandit)
    combat['flags'] = {'combatDifficultyAI': {'forceSentientEnemyBrain': True}}

    with app.app_context():
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_IN_TESTS'] = True
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_ENABLED'] = 'true'
        plan = plan_enemy_intents(combat)

    assert requests
    assert 'Select exactly one already-legal candidate' in requests[0].prompt
    assert 'target_id' in requests[0].prompt
    assert plan['intents'][0]['selectionMethod'] == 'sentient_enemy_brain_candidate_selector'
    assert plan['intents'][0]['brainSource'] == 'deepseek-v4-pro'
    assert plan['intents'][0]['candidateSelection']['selectedCandidateId'] == plan['intents'][0]['candidateId']
    assert plan['intents'][0]['resolver']['resolverType'] == 'engine_intent_bundle_v1'
    assert plan['intentCandidates']['bandit_1'][0]['candidateId'] == plan['intents'][0]['candidateId']
    assert 'resolver' not in plan['intentCandidates']['bandit_1'][0]


def test_sentient_enemy_brain_selector_can_choose_non_baseline_engine_candidate(app, monkeypatch):
    requests = []

    class FakeBrainProvider:
        def generate(self, request):
            requests.append(request)
            match = re.search(r'LEGAL_CANDIDATE_SELECTION_INPUT:\n(\{.*\})', request.prompt, re.S)
            assert match
            selector_input = json.loads(match.group(1))
            pressure_candidate = next(
                candidate
                for candidate in selector_input['legal_candidates']
                if 'pressure_target' in candidate['intent_tags']
            )
            fallback_id = selector_input['deterministic_baseline']['fallback_candidate_id']
            return type(
                'Response',
                (),
                {
                    'text': json.dumps(
                        {
                            'selected_candidate_id': pressure_candidate['candidate_id'],
                            'backup_candidate_ids': [fallback_id],
                            'reasoning_summary': 'Breaking repetition with a legal pressure attack.',
                            'confidence': 0.77,
                        }
                    ),
                    'provider': 'fake',
                    'model': 'deepseek-v4-pro',
                },
            )()

    monkeypatch.setattr(enemy_brain_module, 'get_helper_provider', lambda task=None: FakeBrainProvider())
    mercenary = instantiate_creature(core_creature('mercenary'), instance_id='mercenary_1')
    combat = _combat_with(mercenary)
    combat['battlefield']['cover'] = [{'id': 'stone_pillar', 'name': 'Stone Pillar', 'coverType': 'half'}]
    combat['flags'] = {
        'combatDifficultyAI': {
            'tacticalLevel': 'smart',
            'allowSentientEnemyBrain': True,
            'forceSentientEnemyBrain': True,
        }
    }

    with app.app_context():
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_IN_TESTS'] = True
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_ENABLED'] = 'true'
        plan = plan_enemy_intents(combat)

    assert requests
    intent = plan['intents'][0]
    assert intent['selectionMethod'] == 'sentient_enemy_brain_candidate_selector'
    assert intent['intentType'] == 'attack'
    assert intent['targetId'] == 'player_1'
    assert intent['candidateSelection']['changedDeterministicBaseline'] is True
    assert intent['candidateSelection']['backupCandidateIds'] == [intent['candidateSelection']['fallbackCandidateId']]
    assert intent['resolver']['actionBundle'][-1]['target_id'] == 'player_1'


def test_sentient_enemy_brain_rejects_executable_fields_and_falls_back(app, monkeypatch):
    class FakeBrainProvider:
        def generate(self, request):
            match = re.search(r'LEGAL_CANDIDATE_SELECTION_INPUT:\n(\{.*\})', request.prompt, re.S)
            assert match
            selector_input = json.loads(match.group(1))
            candidate_id = selector_input['legal_candidates'][0]['candidate_id']
            return type(
                'Response',
                (),
                {
                    'text': json.dumps(
                        {
                            'selected_candidate_id': candidate_id,
                            'backup_candidate_ids': [],
                            'reasoning_summary': 'Maliciously attempts to override the target.',
                            'confidence': 0.9,
                            'target_id': 'invented_player',
                        }
                    ),
                    'provider': 'fake',
                    'model': 'deepseek-v4-pro',
                },
            )()

    monkeypatch.setattr(enemy_brain_module, 'get_helper_provider', lambda task=None: FakeBrainProvider())
    bandit = instantiate_creature(core_creature('bandit_thug'), instance_id='bandit_1')
    combat = _combat_with(bandit)
    combat['flags'] = {'combatDifficultyAI': {'forceSentientEnemyBrain': True}}

    with app.app_context():
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_IN_TESTS'] = True
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_ENABLED'] = 'true'
        plan = plan_enemy_intents(combat)

    intent = plan['intents'][0]
    assert intent['selectionMethod'] == 'deterministic_scoring'
    assert intent['brainSource'] == 'deterministic_fallback'
    assert intent['targetId'] == 'player_1'


def test_clear_deterministic_candidate_skips_sentient_selector(app, monkeypatch):
    calls = []

    class FakeBrainProvider:
        def generate(self, request):
            calls.append(request)
            raise AssertionError('clear deterministic winner should not call sentient selector')

    monkeypatch.setattr(enemy_brain_module, 'get_helper_provider', lambda task=None: FakeBrainProvider())
    goblin = instantiate_creature(core_creature('goblin_skirmisher'), instance_id='goblin_1')
    goblin['hp']['current'] = 1
    combat = _combat_with(goblin)
    combat['flags'] = {
        'combatDifficultyAI': {
            'allowSentientEnemyBrain': True,
            'skipLlmWhenTopCandidateMarginExceeds': 0.05,
        }
    }

    with app.app_context():
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_IN_TESTS'] = True
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_ENABLED'] = 'true'
        plan = plan_enemy_intents(combat)

    assert calls == []
    assert plan['intents'][0]['intentType'] == 'retreat'
    assert plan['intents'][0]['selectorSkippedReason'] == 'deterministic_top_candidate_clear'


def test_sentient_selector_respects_round_call_budget(app, monkeypatch):
    calls = []

    class FakeBrainProvider:
        def generate(self, request):
            calls.append(request)
            match = re.search(r'LEGAL_CANDIDATE_SELECTION_INPUT:\n(\{.*\})', request.prompt, re.S)
            assert match
            selector_input = json.loads(match.group(1))
            candidate_id = selector_input['legal_candidates'][0]['candidate_id']
            return type(
                'Response',
                (),
                {
                    'text': json.dumps(
                        {
                            'selected_candidate_id': candidate_id,
                            'backup_candidate_ids': [],
                            'reasoning_summary': 'Budgeted selector call.',
                            'confidence': 0.8,
                        }
                    ),
                    'provider': 'fake',
                    'model': 'deepseek-v4-pro',
                },
            )()

    monkeypatch.setattr(enemy_brain_module, 'get_helper_provider', lambda task=None: FakeBrainProvider())
    enemy_ids = ['bandit_1', 'bandit_2', 'bandit_3', 'bandit_4']
    enemies = [instantiate_creature(core_creature('bandit_thug'), instance_id=enemy_id) for enemy_id in enemy_ids]
    combat = {
        'status': 'active',
        'round': 1,
        'participants': [player_combat_participant(_player()), *enemies],
        'battlefield': {'environmentType': 'forest', 'lighting': 'bright', 'visibility': 'clear'},
        'flags': {
            'combatDifficultyAI': {
                'allowSentientEnemyBrain': True,
                'forceSentientEnemyBrain': True,
                'maxLlmCallsPerRound': 2,
            }
        },
    }

    with app.app_context():
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_IN_TESTS'] = True
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_ENABLED'] = 'true'
        plan = intent_planner_module.plan_enemy_intents(combat)

    assert len(calls) == 2
    assert [intent['selectionMethod'] for intent in plan['intents'][:2]] == [
        'sentient_enemy_brain_candidate_selector',
        'sentient_enemy_brain_candidate_selector',
    ]
    assert [intent['selectorSkippedReason'] for intent in plan['intents'][2:]] == [
        'llm_round_budget_reserved_elsewhere',
        'llm_round_budget_reserved_elsewhere',
    ]


def test_resolution_revalidation_uses_helper_backup_when_selected_candidate_goes_stale():
    bandit = instantiate_creature(core_creature('bandit_thug'), instance_id='bandit_1')
    combat = _combat_with(bandit)
    target = combat['participants'][0]
    candidates = [
        intent_planner_module._candidate(
            intent_planner_module._intent(
                bandit,
                'attack',
                target=target,
                ability=bandit['abilities'][0],
                reason='Attack the visible target.',
                confidence=0.7,
            ),
            70,
        ),
        intent_planner_module._candidate(
            intent_planner_module._intent(
                bandit,
                'retreat',
                reason='Back away when the target is no longer available.',
                confidence=0.8,
                movement_goal='nearest safe exit or cover',
            ),
            55,
        ),
    ]
    intent_planner_module._attach_candidate_contracts(candidates, enemy=bandit, combat=combat)
    selected = deepcopy(candidates[0]['intent'])
    selected['selectionMethod'] = 'sentient_enemy_brain_candidate_selector'
    selected['candidateSelection'] = {
        'selectedCandidateId': candidates[0]['candidateId'],
        'backupCandidateIds': [candidates[1]['candidateId']],
        'fallbackCandidateId': candidates[0]['candidateId'],
        'reasoningSummary': 'Attack unless the target disappears.',
        'confidence': 0.8,
        'changedDeterministicBaseline': False,
    }
    stale_combat = deepcopy(combat)
    stale_combat['stateVersion'] = 'combat_after_player_removed'
    stale_combat['participants'] = [participant for participant in stale_combat['participants'] if participant.get('team') != 'player']

    resolved = intent_planner_module.resolve_selected_candidate_for_current_state(selected, candidates, bandit, stale_combat)

    assert resolved['candidateId'] == candidates[1]['candidateId']
    assert resolved['intentType'] == 'retreat'
    assert resolved['resolutionSource'] == 'backup_candidate'
    assert resolved['candidateSelection']['resolvedCandidateId'] == candidates[1]['candidateId']
    assert resolved['candidateSelection']['resolutionFallbackUsed'] is True
    assert resolved['resolutionValidation']['staleCandidateVersion'] is True


def test_resolution_revalidation_uses_deterministic_fallback_when_no_backup_is_valid():
    bandit = instantiate_creature(core_creature('bandit_thug'), instance_id='bandit_1')
    combat = _combat_with(bandit)
    target = combat['participants'][0]
    candidates = [
        intent_planner_module._candidate(
            intent_planner_module._intent(
                bandit,
                'attack',
                target=target,
                ability=bandit['abilities'][0],
                reason='Attack the visible target.',
                confidence=0.7,
            ),
            70,
        ),
        intent_planner_module._candidate(
            intent_planner_module._intent(
                bandit,
                'retreat',
                reason='Use deterministic fallback when target vanishes.',
                confidence=0.8,
                movement_goal='nearest safe exit or cover',
            ),
            55,
        ),
    ]
    intent_planner_module._attach_candidate_contracts(candidates, enemy=bandit, combat=combat)
    selected = deepcopy(candidates[0]['intent'])
    selected['selectionMethod'] = 'sentient_enemy_brain_candidate_selector'
    selected['candidateSelection'] = {
        'selectedCandidateId': candidates[0]['candidateId'],
        'backupCandidateIds': [],
        'fallbackCandidateId': candidates[0]['candidateId'],
        'reasoningSummary': 'No backup was provided.',
        'confidence': 0.8,
        'changedDeterministicBaseline': False,
    }
    stale_combat = deepcopy(combat)
    stale_combat['stateVersion'] = 'combat_after_player_removed'
    stale_combat['participants'] = [participant for participant in stale_combat['participants'] if participant.get('team') != 'player']

    resolved = intent_planner_module.resolve_selected_candidate_for_current_state(selected, candidates, bandit, stale_combat)

    assert resolved['candidateId'] == candidates[1]['candidateId']
    assert resolved['intentType'] == 'retreat'
    assert resolved['resolutionSource'] == 'deterministic_resolution_fallback'
    assert resolved['candidateSelection']['resolvedCandidateId'] == candidates[1]['candidateId']
    assert resolved['candidateSelection']['resolutionFallbackUsed'] is True
    assert candidates[0]['candidateId'] in resolved['resolutionRejectedCandidates']


def test_combat_helper_evaluation_summarizes_candidate_decisions():
    bandit = instantiate_creature(core_creature('bandit_thug'), instance_id='bandit_1')
    combat = _combat_with(bandit)
    combat['flags'] = {'combatDifficultyAI': {'allowSentientEnemyBrain': False}}
    plan = plan_enemy_intents(combat)

    summary = summarize_combat_helper_plan(plan)

    assert summary['metrics']['total_decisions'] == 1
    assert summary['metrics']['helper_assisted'] == 0
    assert summary['metrics']['average_candidate_count'] >= 1
    assert summary['records'][0]['actor_id'] == 'bandit_1'
    assert summary['records'][0]['fallback_candidate_id'] == plan['intentCandidates']['bandit_1'][0]['candidateId']
    assert summary['records'][0]['executed_candidate_id'] == plan['intents'][0]['candidateId']


def test_combat_helper_evaluation_runs_fixed_snapshots():
    bandit = instantiate_creature(core_creature('bandit_thug'), instance_id='bandit_1')
    wolf = instantiate_creature(core_creature('wolf'), instance_id='wolf_1')
    bandit_snapshot = _combat_with(bandit)
    wolf_snapshot = _combat_with(wolf)
    bandit_snapshot['flags'] = {'combatDifficultyAI': {'allowSentientEnemyBrain': False}}
    wolf_snapshot['flags'] = {'combatDifficultyAI': {'allowSentientEnemyBrain': False}}

    result = run_combat_helper_evaluation([bandit_snapshot, wolf_snapshot])

    assert result['snapshot_count'] == 2
    assert len(result['runs']) == 2
    assert result['metrics']['total_decisions'] == 2
    assert result['metrics']['average_candidate_count'] >= 1


def test_parallel_enemy_intents_preserve_order_and_worker_app_context(app, monkeypatch):
    enemy_ids = ['bandit_1', 'bandit_2', 'bandit_3']
    enemies = [instantiate_creature(core_creature('bandit_thug'), instance_id=enemy_id) for enemy_id in enemy_ids]
    combat = {
        'status': 'active',
        'round': 1,
        'participants': [player_combat_participant(_player()), *enemies],
        'battlefield': {'environmentType': 'forest', 'lighting': 'bright', 'visibility': 'clear'},
        'flags': {'combatDifficultyAI': {'allowBossTacticsHelper': False, 'allowSentientEnemyBrain': True, 'forceSentientEnemyBrain': True}},
    }
    calls = []
    active = {'current': 0, 'max': 0}
    lock = threading.Lock()

    class FakeBrainProvider:
        def generate(self, request):
            enemy_id = next(candidate for candidate in enemy_ids if candidate in request.prompt)
            with lock:
                active['current'] += 1
                active['max'] = max(active['max'], active['current'])
                calls.append({'enemy_id': enemy_id, 'has_app_context': has_app_context()})
            try:
                time.sleep(0.05)
                return type(
                    'Response',
                    (),
                    {
                        'text': json.dumps(
                            {
                                'enemy_id': enemy_id,
                                'target': {'id': 'player_1', 'reason': 'reachable target'},
                                'action_intent': {'intentType': 'attack', 'confidence': 0.8},
                                'reasoning_summary': f'{enemy_id} attacks in parallel.',
                            }
                        ),
                        'provider': 'fake',
                        'model': 'deepseek-v4-pro',
                    },
                )()
            finally:
                with lock:
                    active['current'] -= 1

    provider = FakeBrainProvider()
    monkeypatch.setattr(enemy_brain_module, 'get_helper_provider', lambda task=None: provider)

    with app.app_context():
        app.config['AIDM_ENEMY_INTENT_PARALLEL'] = True
        app.config['AIDM_ENEMY_INTENT_MAX_WORKERS'] = 3
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_IN_TESTS'] = True
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_ENABLED'] = 'true'
        plan = intent_planner_module.plan_enemy_intents(combat)

    assert [intent['enemyId'] for intent in plan['intents']] == enemy_ids
    assert list(plan['intentCandidates'].keys()) == enemy_ids
    assert list(plan['combatFactsByEnemy'].keys()) == enemy_ids
    assert all(call['has_app_context'] for call in calls)
    assert active['max'] > 1


def test_parallel_enemy_intents_respects_single_worker_fast_path(app, monkeypatch):
    enemies = [
        instantiate_creature(core_creature('bandit_thug'), instance_id='bandit_1'),
        instantiate_creature(core_creature('bandit_thug'), instance_id='bandit_2'),
    ]
    combat = {
        'status': 'active',
        'round': 1,
        'participants': [player_combat_participant(_player()), *enemies],
        'battlefield': {'environmentType': 'forest', 'lighting': 'bright', 'visibility': 'clear'},
        'flags': {'combatDifficultyAI': {'allowBossTacticsHelper': False, 'allowSentientEnemyBrain': False}},
    }

    def fail_thread_pool(*args, **kwargs):
        raise AssertionError('ThreadPoolExecutor should not be used for a single-worker plan.')

    monkeypatch.setattr(intent_planner_module, 'ThreadPoolExecutor', fail_thread_pool)

    with app.app_context():
        app.config['AIDM_ENEMY_INTENT_PARALLEL'] = True
        app.config['AIDM_ENEMY_INTENT_MAX_WORKERS'] = 1
        plan = intent_planner_module.plan_enemy_intents(combat)

    assert [intent['enemyId'] for intent in plan['intents']] == ['bandit_1', 'bandit_2']


def test_boss_tactics_helper_success_skips_second_sentient_brain_call(app, monkeypatch):
    boss_requests = []
    brain_requests = []

    class FakeBossProvider:
        def generate(self, request):
            boss_requests.append(request)
            match = re.search(r'BOSS_CANDIDATE_SELECTION_INPUT:\n(\{.*\})', request.prompt, re.S)
            assert match
            selector_input = json.loads(match.group(1))
            environment_candidate = next(
                candidate
                for candidate in selector_input['legal_candidates']
                if 'use_environment' in candidate['intent_tags']
            )
            fallback_id = selector_input['deterministic_baseline']['fallback_candidate_id']
            return type(
                'Response',
                (),
                {
                    'text': json.dumps(
                        {
                            'selected_candidate_id': environment_candidate['candidate_id'],
                            'backup_candidate_ids': [fallback_id],
                            'reasoning_summary': 'The boss uses the battlefield as a weapon.',
                            'confidence': 0.88,
                        }
                    ),
                    'provider': 'fake',
                    'model': 'deepseek-v4-pro',
                },
            )()

    class FakeBrainProvider:
        def generate(self, request):
            brain_requests.append(request)
            return type(
                'Response',
                (),
                {
                    'text': json.dumps(
                        {
                            'enemy_id': 'enemy_cult_leader_1',
                            'target': {'id': 'player_1'},
                            'action_intent': {'intentType': 'attack'},
                            'reasoning_summary': 'This should not be called after boss tactics succeeds.',
                        }
                    ),
                    'provider': 'fake',
                    'model': 'deepseek-v4-pro',
                },
            )()

    monkeypatch.setattr(boss_tactics_module, 'get_helper_provider', lambda task=None: FakeBossProvider())
    monkeypatch.setattr(enemy_brain_module, 'get_helper_provider', lambda task=None: FakeBrainProvider())

    boss = instantiate_creature(core_creature('cult_leader'), instance_id='enemy_cult_leader_1')
    combat = _combat_with(boss)
    combat['battlefield']['hazards'] = [{'id': 'ritual_fire', 'name': 'Ritual Fire'}]
    combat['flags'] = {
        'combatDifficultyAI': {
            'allowBossTacticsHelper': True,
            'allowSentientEnemyBrain': True,
        }
    }

    with app.app_context():
        app.config['AIDM_BOSS_TACTICS_HELPER_IN_TESTS'] = True
        app.config['AIDM_BOSS_TACTICS_HELPER_ENABLED'] = 'true'
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_IN_TESTS'] = True
        app.config['AIDM_SENTIENT_ENEMY_BRAIN_HELPER_ENABLED'] = 'true'
        plan = intent_planner_module.plan_enemy_intents(combat)

    assert len(boss_requests) == 1
    assert brain_requests == []
    assert plan['intents'][0]['tacticSource'] == 'deepseek-v4-pro'
    assert plan['intents'][0]['intentType'] == 'use_environment'
    assert plan['intents'][0]['selectionMethod'] == 'boss_tactics_candidate_selector'
    assert plan['intents'][0]['bossTacticsSelection']['selectedCandidateId'] == plan['intents'][0]['candidateId']


def test_boss_tactics_rejects_executable_fields_and_falls_back(app, monkeypatch):
    class FakeBossProvider:
        def generate(self, request):
            match = re.search(r'BOSS_CANDIDATE_SELECTION_INPUT:\n(\{.*\})', request.prompt, re.S)
            assert match
            selector_input = json.loads(match.group(1))
            candidate_id = selector_input['legal_candidates'][0]['candidate_id']
            return type(
                'Response',
                (),
                {
                    'text': json.dumps(
                        {
                            'selected_candidate_id': candidate_id,
                            'backup_candidate_ids': [],
                            'reasoning_summary': 'Attempts to smuggle an ability override.',
                            'confidence': 0.9,
                            'ability_id': 'invented_boss_power',
                        }
                    ),
                    'provider': 'fake',
                    'model': 'deepseek-v4-pro',
                },
            )()

    monkeypatch.setattr(boss_tactics_module, 'get_helper_provider', lambda task=None: FakeBossProvider())
    boss = instantiate_creature(core_creature('cult_leader'), instance_id='enemy_cult_leader_1')
    combat = _combat_with(boss)
    combat['battlefield']['hazards'] = [{'id': 'ritual_fire', 'name': 'Ritual Fire'}]
    combat['flags'] = {'combatDifficultyAI': {'allowBossTacticsHelper': True}}

    with app.app_context():
        app.config['AIDM_BOSS_TACTICS_HELPER_IN_TESTS'] = True
        app.config['AIDM_BOSS_TACTICS_HELPER_ENABLED'] = 'true'
        plan = intent_planner_module.plan_enemy_intents(combat)

    assert plan['intents'][0]['tacticSource'] == 'deterministic_fallback'
    assert plan['intents'][0]['selectionMethod'] == 'deterministic_scoring'
    assert plan['intents'][0]['intentType'] == 'use_environment'


def test_warm_boss_planner_biases_candidate_matching_without_authoring_actions(app, monkeypatch):
    planner_requests = []

    class FakePlannerProvider:
        def generate(self, request):
            planner_requests.append(request)
            assert 'BOSS_ADVISORY_PLANNER_INPUT' in request.prompt
            assert 'Do not choose a candidate ID' in request.prompt
            return type(
                'Response',
                (),
                {
                    'text': json.dumps(
                        {
                            'tactical_goal': 'Protect the ritual instead of chasing damage.',
                            'desired_intent_type': 'complete_objective',
                            'preferred_tags': ['objective_support', 'complete_ritual'],
                            'risk_posture': 'balanced',
                            'objective_priority': 'protect_objective',
                            'reasoning_summary': 'The ritual is the boss objective this turn.',
                        }
                    ),
                    'provider': 'fake',
                    'model': 'deepseek-v4-pro',
                },
            )()

    monkeypatch.setattr(boss_tactics_module, 'get_helper_provider', lambda task=None: FakePlannerProvider())
    boss = instantiate_creature(core_creature('cult_leader'), instance_id='enemy_cult_leader_1')
    combat = _combat_with(boss)
    combat['flags'] = {
        'combatDifficultyAI': {
            'allowBossTacticsHelper': True,
            'allowBossWarmPlanner': True,
            'allowDeterministicCandidateMatcher': True,
        }
    }

    with app.app_context():
        app.config['AIDM_BOSS_TACTICS_PLANNER_IN_TESTS'] = True
        app.config['AIDM_BOSS_TACTICS_PLANNER_ENABLED'] = 'true'
        app.config['AIDM_BOSS_TACTICS_HELPER_ENABLED'] = 'false'
        plan = intent_planner_module.plan_enemy_intents(combat)

    assert planner_requests
    assert plan['intents'][0]['intentType'] == 'complete_objective'
    assert plan['intents'][0]['selectionMethod'] == 'deterministic_matcher'
    assert plan['intents'][0]['bossPlanner']['source'] == 'deepseek-v4-pro'
    assert 'objective_support' in plan['intents'][0]['bossPlanner']['matchedTags']


def test_cached_boss_planner_biases_next_turn_without_provider_call(app, monkeypatch):
    class FailingPlannerProvider:
        def generate(self, request):
            raise AssertionError('cached boss planner should avoid provider call')

    monkeypatch.setattr(boss_tactics_module, 'get_helper_provider', lambda task=None: FailingPlannerProvider())
    boss = instantiate_creature(core_creature('cult_leader'), instance_id='enemy_cult_leader_1')
    boss['currentIntent'] = {
        'intentType': 'complete_objective',
        'bossPlanner': {
            'source': 'deepseek-v4-pro',
            'preferredTags': ['objective_support', 'complete_ritual'],
            'desiredIntentType': 'complete_objective',
            'riskPosture': 'balanced',
            'reasoningSummary': 'Continue defending the ritual.',
            'expiresAfterTurns': 2,
        },
    }
    combat = _combat_with(boss)
    combat['flags'] = {
        'combatDifficultyAI': {
            'allowBossTacticsHelper': True,
            'allowBossWarmPlanner': True,
            'allowDeterministicCandidateMatcher': True,
        }
    }

    with app.app_context():
        app.config['AIDM_BOSS_TACTICS_PLANNER_IN_TESTS'] = True
        app.config['AIDM_BOSS_TACTICS_PLANNER_ENABLED'] = 'true'
        app.config['AIDM_BOSS_TACTICS_HELPER_ENABLED'] = 'false'
        plan = intent_planner_module.plan_enemy_intents(combat)

    assert plan['intents'][0]['intentType'] == 'complete_objective'
    assert plan['intents'][0]['bossPlanner']['source'] == 'deepseek-v4-pro:cached'
    assert plan['intents'][0]['bossPlanner']['expiresAfterTurns'] == 1


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


def test_combat_participant_alias_resolves_generated_enemy_id():
    thunderer = instantiate_creature(core_creature('bandit_thug'), instance_id='enemy_thor_99_1')
    thunderer['name'] = 'The Thunderer'
    thunderer['aliases'] = ['Thor', 'Thunderer']
    state = {
        'currentScene': {'sceneType': 'combat', 'dangerLevel': 8, 'combatState': 'active'},
        'combat': _combat_with(thunderer),
        'stateChangeLedger': [],
    }

    validation = validate_state_changes(
        state=state,
        changes=[
            {
                'id': 'thunderer_hit',
                'type': 'combat.participant.update',
                'participantId': 'thunderer',
                'hp': {'current': 4, 'max': thunderer['hp']['max']},
            }
        ],
    )
    applied_changes = validated_changes_for_application(validation)
    applied = apply_state_changes(state, applied_changes)
    enemy = next(participant for participant in applied['nextState']['combat']['participants'] if participant['id'] == 'enemy_thor_99_1')

    assert not validation['rejected']
    assert applied_changes[0]['participantId'] == 'enemy_thor_99_1'
    assert enemy['hp']['current'] == 4


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


def test_prepare_combat_does_not_start_from_cast_or_stale_scene_combat_state(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        assert session_obj is not None
        assert campaign is not None
        state = {
            'currentScene': {'sceneType': 'combat', 'combatState': 'active', 'dangerLevel': 8},
            'playerCharacters': [_player()],
            'combat': {'status': 'ended', 'round': 1, 'participants': []},
        }
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='Loki casts a ward over Himeros.',
        )
        db.session.add(turn)
        db.session.flush()

        result = prepare_combat_for_turn(
            state=state,
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            player_message='Loki casts a ward over Himeros.',
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
    assert plan['intents'][0]['selectionScore'] == 90
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
    composed = client.post(
        f"/api/sessions/{ids['session_id']}/combat/start",
        json={
            'encounterPurpose': 'guard',
            'allowGeneration': False,
            'allowVariants': False,
            'enemyGroups': [
                {'label': 'wolves', 'count': 2, 'creature': core_creature('wolf')},
                {
                    'label': 'goblin',
                    'count': 1,
                    'themeTags': ['goblin'],
                    'desiredRole': 'skirmisher',
                    'desiredCreatureType': 'humanoid',
                    'encounterPurpose': 'ambush',
                    'difficulty': 'easy',
                },
            ],
        },
    ).get_json()

    assert core['entries']
    assert resolve['creature']['id'] == 'goblin_skirmisher'
    assert combat['combat']['status'] == 'active'
    assert composed['combat']['flags']['resolverMethod'] == 'encounter_composed'
    assert composed['combat']['flags']['enemyCount'] == 3
    assert len([participant for participant in composed['combat']['participants'] if participant['team'] == 'enemy']) == 3


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
