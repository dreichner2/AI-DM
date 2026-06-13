from __future__ import annotations

import json

from aidm_server.database import db
from aidm_server.llm import build_dm_context
from aidm_server.models import (
    BestiaryEntry,
    Campaign,
    CampaignSegment,
    DmTurn,
    Player,
    PlayerAction,
    Session,
    SessionState,
    World,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from tests.helpers import seed_world_campaign_player_session


def test_build_dm_context_collects_recent_actions_for_multiple_players(app):
    with app.app_context():
        world = World(name='Context World', description='world')
        db.session.add(world)
        db.session.flush()

        campaign = Campaign(title='Context Campaign', world_id=world.world_id)
        db.session.add(campaign)
        db.session.flush()

        player_one = Player(campaign_id=campaign.campaign_id, name='Alice', character_name='Alice')
        player_two = Player(campaign_id=campaign.campaign_id, name='Borin', character_name='Borin')
        db.session.add_all([player_one, player_two])
        db.session.flush()

        session = Session(campaign_id=campaign.campaign_id)
        db.session.add(session)
        db.session.flush()

        db.session.add_all(
            [
                PlayerAction(player_id=player_one.player_id, session_id=session.session_id, action_text='scout'),
                PlayerAction(player_id=player_one.player_id, session_id=session.session_id, action_text='hide'),
                PlayerAction(player_id=player_one.player_id, session_id=session.session_id, action_text='strike'),
                PlayerAction(player_id=player_one.player_id, session_id=session.session_id, action_text='retreat'),
                PlayerAction(player_id=player_two.player_id, session_id=session.session_id, action_text='chant'),
                PlayerAction(player_id=player_two.player_id, session_id=session.session_id, action_text='guard'),
            ]
        )
        db.session.commit()

        payload = json.loads(build_dm_context(world.world_id, campaign.campaign_id, session.session_id))

    players = {entry['character_name']: entry for entry in payload['active_players']}
    assert players['Alice']['recent_actions'] == ['hide', 'strike', 'retreat']
    assert players['Borin']['recent_actions'] == ['chant', 'guard']


def test_build_dm_context_skips_unfinished_recent_turns(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        stable_first = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I ask Larin to steady himself.',
            dm_output='Larin plants a hand against the wall and catches his breath.',
            status='completed',
            outcome_status='resolved',
        )
        stable_second = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I scan the next ledge before jumping.',
            dm_output='The next ledge looks slick but reachable if Larin commits.',
            status='completed',
            outcome_status='resolved',
        )
        unfinished_roll = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I roll a d20-1 for STR check: 14 = 13',
            dm_output=None,
            status='processing',
            requires_roll=True,
            rule_type='strength_check',
            roll_value=13,
            outcome_status='resolved',
        )
        db.session.add_all([stable_first, stable_second, unfinished_roll])
        db.session.commit()

        stable_turn_ids = [stable_first.turn_id, stable_second.turn_id]
        unfinished_turn_id = unfinished_roll.turn_id
        payload = json.loads(
            build_dm_context(
                ids['world_id'],
                ids['campaign_id'],
                ids['session_id'],
                max_turns=2,
            )
        )

    recent_turns = payload['recent_turns']
    assert [turn['turn_id'] for turn in recent_turns] == stable_turn_ids
    assert unfinished_turn_id not in [turn['turn_id'] for turn in recent_turns]
    assert all(turn['dm_output'] for turn in recent_turns)
    assert 'I roll a d20-1' not in json.dumps(recent_turns)


def test_build_dm_context_skips_resolved_roll_request_turns(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        stable_first = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I ask the watchman to pause.',
            dm_output='The watchman hesitates, spear still held between you.',
            status='completed',
            outcome_status='resolved',
        )
        stable_second = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I step back from the stall.',
            dm_output='You give ground, keeping the spice counter at your side.',
            status='completed',
            outcome_status='resolved',
        )
        roll_request = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I leap over the guard and try to wrench his neck.',
            dm_output='Larin, roll d20 - 1 for Athletics (DC 16).',
            status='completed',
            requires_roll=True,
            rule_type='athletics',
            roll_value=None,
            outcome_status='deferred',
            rules_hint=safe_json_dumps({'requires_roll': True, 'outcome_deferred': True}, {}),
        )
        db.session.add_all([stable_first, stable_second, roll_request])
        db.session.flush()

        resolver = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I roll a d20-1 for STR check: 14 = 13',
            dm_output=None,
            status='processing',
            requires_roll=True,
            rule_type='athletics',
            roll_value=13,
            outcome_status='resolved',
            rules_hint=safe_json_dumps(
                {
                    'requires_roll': True,
                    'roll_type': 'athletics',
                    'roll_value': 13,
                    'resolved_turn_id': roll_request.turn_id,
                    'outcome_deferred': False,
                },
                {},
            ),
        )
        db.session.add(resolver)
        db.session.flush()
        roll_request.outcome_status = 'resolved'
        roll_request.metadata_json = safe_json_dumps({'resolved_by_turn_id': resolver.turn_id}, {})
        db.session.commit()

        stable_turn_ids = [stable_first.turn_id, stable_second.turn_id]
        roll_request_turn_id = roll_request.turn_id
        resolver_turn_id = resolver.turn_id
        payload = json.loads(
            build_dm_context(
                ids['world_id'],
                ids['campaign_id'],
                ids['session_id'],
                max_turns=2,
            )
        )

    recent_turns = payload['recent_turns']
    assert [turn['turn_id'] for turn in recent_turns] == stable_turn_ids
    assert {turn['context_role'] for turn in recent_turns} == {'completed_narration'}
    assert roll_request_turn_id not in [turn['turn_id'] for turn in recent_turns]
    assert resolver_turn_id not in [turn['turn_id'] for turn in recent_turns]
    encoded_recent_turns = json.dumps(recent_turns)
    assert 'roll d20' not in encoded_recent_turns
    assert 'I roll a d20-1' not in encoded_recent_turns


def test_build_dm_context_keeps_final_no_roll_rulings(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        no_roll_ruling = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input="I throw a dagger at the watchman's throat.",
            dm_output='Your hand finds no dagger. No attack reaches the watchman.',
            status='completed',
            requires_roll=True,
            rule_type='attack',
            roll_value=None,
            outcome_status='resolved',
            rules_hint=safe_json_dumps({'requires_roll': True, 'outcome_deferred': True}, {}),
        )
        db.session.add(no_roll_ruling)
        db.session.commit()

        payload = json.loads(
            build_dm_context(
                ids['world_id'],
                ids['campaign_id'],
                ids['session_id'],
                max_turns=2,
            )
        )
        no_roll_turn_id = no_roll_ruling.turn_id

    recent_turns = payload['recent_turns']
    assert [turn['turn_id'] for turn in recent_turns] == [no_roll_turn_id]
    assert recent_turns[0]['context_role'] == 'completed_narration'
    assert 'No attack reaches the watchman.' in recent_turns[0]['dm_output']


def test_build_dm_context_scopes_players_to_current_campaign(app):
    with app.app_context():
        world = World(name='Shared World', description='world')
        db.session.add(world)
        db.session.flush()

        old_campaign = Campaign(title='Old Campaign', world_id=world.world_id, workspace_id='owner')
        current_campaign = Campaign(title='Current Campaign', world_id=world.world_id, workspace_id='owner')
        db.session.add_all([old_campaign, current_campaign])
        db.session.flush()

        old_player = Player(
            workspace_id='owner',
            campaign_id=old_campaign.campaign_id,
            name='Friend',
            character_name='Oden',
        )
        current_player = Player(
            workspace_id='owner',
            campaign_id=current_campaign.campaign_id,
            name='Danny',
            character_name='Kozuki',
        )
        db.session.add_all([old_player, current_player])
        db.session.flush()

        session = Session(campaign_id=current_campaign.campaign_id)
        db.session.add(session)
        db.session.flush()

        db.session.add_all(
            [
                PlayerAction(player_id=old_player.player_id, session_id=session.session_id, action_text='mentions A'),
                PlayerAction(player_id=current_player.player_id, session_id=session.session_id, action_text='wakes in town'),
            ]
        )
        db.session.commit()

        payload = json.loads(build_dm_context(world.world_id, current_campaign.campaign_id, session.session_id))

    players = {entry['character_name']: entry for entry in payload['active_players']}
    assert list(players) == ['Kozuki']
    assert players['Kozuki']['recent_actions'] == ['wakes in town']
    assert 'Oden' not in json.dumps(payload)
    assert 'mentions A' not in json.dumps(payload)


def test_build_dm_context_truncates_large_session_payloads(app):
    with app.app_context():
        world = World(name='Compact World', description='world')
        db.session.add(world)
        db.session.flush()

        campaign = Campaign(title='Compact Campaign', world_id=world.world_id)
        db.session.add(campaign)
        db.session.flush()

        player = Player(campaign_id=campaign.campaign_id, name='Alice', character_name='Alice')
        db.session.add(player)
        db.session.flush()

        session = Session(campaign_id=campaign.campaign_id)
        db.session.add(session)
        db.session.flush()

        state = SessionState(
            session_id=session.session_id,
            rolling_summary='R' * 6000,
            current_location='Long Hall',
            current_quest='Find the relic',
            active_segments=safe_json_dumps([], []),
            memory_snippets=safe_json_dumps(
                [
                    {
                        'turn_id': 1,
                        'player_input': 'P' * 500,
                        'dm_output': 'D' * 800,
                    }
                ]
                * 10,
                [],
            ),
        )
        db.session.add(state)

        db.session.add(
            PlayerAction(
                player_id=player.player_id,
                session_id=session.session_id,
                action_text='search',
            )
        )
        db.session.commit()

        payload = json.loads(build_dm_context(world.world_id, campaign.campaign_id, session.session_id))

    assert len(payload['session_state']['rolling_summary']) <= 4000
    assert len(payload['session_state']['memory_snippets']) == 8
    assert len(payload['session_state']['memory_snippets'][0]['player_input']) <= 180
    assert len(payload['session_state']['memory_snippets'][0]['dm_output']) <= 260


def test_build_dm_context_includes_compact_live_world_state_from_snapshot(app):
    with app.app_context():
        world = World(name='Live World', description='world')
        db.session.add(world)
        db.session.flush()

        campaign = Campaign(
            title='Live Campaign',
            world_id=world.world_id,
            location='Campaign Seed Location',
            current_quest='Campaign Seed Quest',
        )
        db.session.add(campaign)
        db.session.flush()

        player = Player(campaign_id=campaign.campaign_id, name='Alice', character_name='Alice')
        db.session.add(player)
        db.session.flush()

        session = Session(
            campaign_id=campaign.campaign_id,
            state_snapshot=safe_json_dumps(
                {
                    'currentScene': {
                        'locationId': 'blackwake_tavern',
                        'name': 'Blackwake Tavern',
                        'sceneType': 'social',
                        'dangerLevel': 2,
                        'mood': 'tense',
                        'combatState': 'none',
                        'description': 'A busy tavern full of dockside rumors.',
                        'activeNpcIds': ['captain_velra'],
                        'activeQuestIds': ['find_missing_sailor'],
                        'items': [
                            {
                                'id': 'loose_map',
                                'name': 'Loose Map',
                                'quantity': 1,
                                'type': 'misc',
                                'sourceActorId': 'player_2',
                                'privateNote': 'Hidden item note should not enter context.',
                            }
                        ],
                    },
                    'quests': [
                        {
                            'id': 'find_missing_sailor',
                            'title': 'Find the Missing Sailor',
                            'status': 'active',
                            'stage': 'Investigate the docks',
                            'summary': 'Find what happened to the missing sailor.',
                            'objectives': [
                                {
                                    'id': 'talk_to_velra',
                                    'description': 'Talk to Captain Velra.',
                                    'status': 'open',
                                }
                            ],
                        },
                        {
                            'id': 'old_finished_quest',
                            'title': 'Old Finished Quest',
                            'status': 'completed',
                        },
                    ],
                    'locations': [
                        {
                            'id': 'blackwake_tavern',
                            'name': 'Blackwake Tavern',
                            'type': 'tavern',
                            'status': 'visited',
                            'description': 'A noisy tavern near the harbor.',
                            'connectedLocationIds': ['north_docks'],
                            'lastVisitedTurn': 12,
                        }
                    ],
                    'knownNpcs': [
                        {
                            'id': 'captain_velra',
                            'name': 'Captain Velra',
                            'race': 'Human',
                            'role': 'dock captain',
                            'disposition': 'friendly',
                            'status': 'met',
                            'locationId': 'blackwake_tavern',
                            'questIds': ['find_missing_sailor'],
                            'memory': ['Private NPC memory should not enter the compact payload.'],
                            'lastSeenTurn': 12,
                        },
                        {
                            'id': 'marta_fenwick',
                            'name': 'Marta Fenwick',
                            'race': 'Halfling',
                            'role': 'shopkeeper',
                            'disposition': 'curious',
                            'status': 'known',
                            'locationId': 'north_docks',
                            'lastSeenTurn': 11,
                        },
                    ],
                    'flags': {'velra_met': True},
                    'playerCharacters': [{'id': 'player_1', 'inventory': {'items': [{'name': 'Rope'}]}}],
                    'stateChangeLedger': [{'id': 'secret_change'}],
                },
                {},
            ),
        )
        db.session.add(session)
        db.session.commit()

        payload = json.loads(build_dm_context(world.world_id, campaign.campaign_id, session.session_id))

    live_state = payload['live_world_state']
    assert payload['campaign']['location'] == 'Campaign Seed Location'
    assert live_state['currentScene']['name'] == 'Blackwake Tavern'
    assert live_state['currentScene']['locationId'] == 'blackwake_tavern'
    assert live_state['currentScene']['items'] == [
        {
            'id': 'loose_map',
            'name': 'Loose Map',
            'quantity': 1,
            'type': 'misc',
            'sourceActorId': 'player_2',
        }
    ]
    assert live_state['activeQuests'][0]['id'] == 'find_missing_sailor'
    assert live_state['activeQuests'][0]['objectives'][0]['id'] == 'talk_to_velra'
    assert [quest['id'] for quest in live_state['activeQuests']] == ['find_missing_sailor']
    assert live_state['recentLocations'][0]['id'] == 'blackwake_tavern'
    assert live_state['activeNpcs'][0]['id'] == 'captain_velra'
    assert live_state['recentKnownNpcs'][0]['id'] == 'marta_fenwick'
    assert live_state['flags'] == {'velra_met': True}

    encoded_live_state = json.dumps(live_state)
    assert 'stateChangeLedger' not in encoded_live_state
    assert 'playerCharacters' not in encoded_live_state
    assert 'Private NPC memory' not in encoded_live_state
    assert 'Hidden item note' not in encoded_live_state


def test_build_dm_context_includes_campaign_pack_director_packet(app):
    with app.app_context():
        world = World(name='Pack World', description='world')
        db.session.add(world)
        db.session.flush()

        campaign = Campaign(
            title='Pack Campaign',
            world_id=world.world_id,
            location='Bleakmoor Gate',
            current_quest='Find the Missing Caravan',
        )
        db.session.add(campaign)
        db.session.flush()

        session = Session(
            campaign_id=campaign.campaign_id,
            state_snapshot=safe_json_dumps(
                {
                    'currentScene': {
                        'locationId': 'bleakmoor_gate',
                        'name': 'Bleakmoor Gate',
                        'activeNpcIds': ['npc_captain_veyra'],
                        'activeQuestIds': ['q_missing_caravan'],
                    },
                    'locations': [
                        {
                            'id': 'bleakmoor_gate',
                            'name': 'Bleakmoor Gate',
                            'type': 'town',
                            'description': 'A rain-darkened gatehouse.',
                            'connectedLocationIds': ['old_road'],
                            'source': 'campaign_pack',
                            'packId': 'bleakmoor_intro',
                        },
                    ],
                    'knownNpcs': [
                        {
                            'id': 'npc_captain_veyra',
                            'name': 'Captain Veyra',
                            'role': 'Gate captain',
                            'disposition': 'suspicious',
                            'locationId': 'bleakmoor_gate',
                            'questIds': ['q_missing_caravan'],
                            'source': 'campaign_pack',
                            'packId': 'bleakmoor_intro',
                        }
                    ],
                    'quests': [
                        {
                            'id': 'q_missing_caravan',
                            'title': 'Find the Missing Caravan',
                            'status': 'in_progress',
                            'stage': 'Follow the Old Road',
                            'summary': 'A supply caravan vanished.',
                            'source': 'campaign_pack',
                            'packId': 'bleakmoor_intro',
                        }
                    ],
                    'flags': {'campaignPackActiveCheckpointId': 'cp_gate'},
                    'campaignPack': {
                        'packId': 'bleakmoor_intro',
                        'title': 'The Lanterns of Bleakmoor',
                        'version': '1.0.0',
                        'directorRules': {
                            'mainQuestGeneration': 'pack_only',
                            'sideQuestGeneration': 'allowed_tagged',
                            'offTrackPolicy': 'improvise_and_reconnect',
                        },
                        'checkpoints': [
                            {
                                'id': 'cp_gate',
                                'title': 'Question the gate captain',
                                'summary': 'Learn where the caravan went.',
                                'locationIds': ['bleakmoor_gate'],
                                'questIds': ['q_missing_caravan'],
                                'npcIds': ['npc_captain_veyra'],
                                'nextCheckpointIds': ['cp_wreck'],
                                'directorRules': {'checkpointStyle': 'soft_clue_forward'},
                            },
                            {
                                'id': 'cp_wreck',
                                'title': 'Find the caravan wreck',
                                'playerTitle': 'Follow the old road',
                                'chapter': 'Act I',
                                'act': 'I',
                                'priority': 7,
                                'gate': 'soft',
                                'canCompleteOutOfOrder': True,
                                'locationIds': ['old_road'],
                                'questIds': ['q_missing_caravan'],
                            },
                        ],
                        'encounters': [
                            {
                                'id': 'enc_lantern_wraith',
                                'title': 'Lantern Wraith',
                                'checkpointIds': ['cp_wreck'],
                                'locationIds': ['old_road'],
                                'enemyIds': ['lantern_wraith'],
                            }
                        ],
                        'catalog': {
                            'locations': [
                                {
                                    'id': 'bleakmoor_gate',
                                    'name': 'Bleakmoor Gate',
                                    'type': 'town',
                                    'description': 'A rain-darkened gatehouse.',
                                    'connectedLocationIds': ['old_road'],
                                    'source': 'campaign_pack',
                                    'packId': 'bleakmoor_intro',
                                },
                                {
                                    'id': 'old_road',
                                    'name': 'Old Road',
                                    'type': 'road',
                                    'description': 'A drowned road into the marsh.',
                                    'source': 'campaign_pack',
                                    'packId': 'bleakmoor_intro',
                                },
                            ],
                            'npcs': [
                                {
                                    'id': 'npc_captain_veyra',
                                    'name': 'Captain Veyra',
                                    'role': 'Gate captain',
                                    'disposition': 'suspicious',
                                    'locationId': 'bleakmoor_gate',
                                    'questIds': ['q_missing_caravan'],
                                    'source': 'campaign_pack',
                                    'packId': 'bleakmoor_intro',
                                }
                            ],
                            'quests': [
                                {
                                    'id': 'q_missing_caravan',
                                    'title': 'Find the Missing Caravan',
                                    'status': 'active',
                                    'stage': 'Ask at Bleakmoor Gate',
                                    'summary': 'A supply caravan vanished.',
                                    'source': 'campaign_pack',
                                    'packId': 'bleakmoor_intro',
                                }
                            ],
                            'encounters': [
                                {
                                    'id': 'enc_lantern_wraith',
                                    'title': 'Lantern Wraith',
                                    'checkpointIds': ['cp_wreck'],
                                    'locationIds': ['old_road'],
                                    'enemyIds': ['lantern_wraith'],
                                }
                            ],
                        },
                    },
                },
                {},
            ),
        )
        db.session.add(session)
        db.session.flush()

        db.session.add(
            CampaignSegment(
                campaign_id=campaign.campaign_id,
                title='Question Captain Veyra',
                description='Veyra points toward the old road.',
                trigger_condition=safe_json_dumps(
                    {
                        'type': 'state',
                        'location_contains': 'bleakmoor',
                        'quest_contains': 'missing caravan',
                    },
                    {},
                ),
                tags='campaign_pack,pack:bleakmoor_intro,mainline',
                is_triggered=False,
            )
        )
        db.session.add(
            BestiaryEntry(
                workspace_id='owner',
                campaign_id=campaign.campaign_id,
                scope='campaign',
                creature_id='lantern_wraith',
                version=1,
                name='Lantern Wraith',
                source='campaign_pack',
                persistence='campaign',
                location_ids_json=safe_json_dumps(['old_road'], []),
                faction_ids_json=safe_json_dumps([], []),
                tags_json=safe_json_dumps(['campaign_pack', 'pack:bleakmoor_intro', 'wraith'], []),
                creature_json=safe_json_dumps(
                    {
                        'id': 'lantern_wraith',
                        'name': 'Lantern Wraith',
                        'source': 'campaign_pack',
                        'creatureType': 'undead',
                        'challengeTier': 'hard',
                        'descriptionShort': 'A marsh undead bound to lost lantern light.',
                        'behavior': {'combatRole': 'assassin'},
                    },
                    {},
                ),
                balance_json=safe_json_dumps({}, {}),
            )
        )
        db.session.commit()

        payload = json.loads(build_dm_context(world.world_id, campaign.campaign_id, session.session_id))

    director = payload['campaign_pack_director']
    assert director['enabled'] is True
    assert director['pack']['packId'] == 'bleakmoor_intro'
    assert director['policy']['mainQuestGeneration'] == 'pack_only'
    assert director['policy']['checkpointStyle'] == 'soft_clue_forward'
    assert 'Do not invent replacement main quests' in director['policy']['instructions'][-2]
    assert director['activeCheckpoint']['id'] == 'cp_gate'
    assert director['activeCheckpoint']['status'] == 'active'
    assert director['nextCheckpoints'][0]['id'] == 'cp_wreck'
    assert director['nextCheckpoints'][0]['playerTitle'] == 'Follow the old road'
    assert director['nextCheckpoints'][0]['chapter'] == 'Act I'
    assert director['nextCheckpoints'][0]['priority'] == 7
    assert director['nextCheckpoints'][0]['gate'] == 'soft'
    assert director['nextCheckpoints'][0]['canCompleteOutOfOrder'] is True
    assert director['progress']['offTrack'] is False
    assert director['progress']['rejoinTargetCheckpointId'] == 'cp_gate'
    assert director['progress']['checkpointStatuses'] == {'cp_gate': 'active', 'cp_wreck': 'open'}
    assert director['relevantRecords']['quests'][0]['id'] == 'q_missing_caravan'
    assert director['relevantRecords']['quests'][0]['stage'] == 'Follow the Old Road'
    assert director['relevantRecords']['quests'][0]['status'] == 'in_progress'
    assert [location['id'] for location in director['relevantRecords']['locations']] == ['bleakmoor_gate', 'old_road']
    assert [location['knownToPlayers'] for location in director['relevantRecords']['locations']] == [True, False]
    assert director['relevantRecords']['npcs'][0]['id'] == 'npc_captain_veyra'
    assert director['relevantRecords']['npcs'][0]['knownToPlayers'] is True
    assert director['relevantRecords']['encounters'][0]['id'] == 'enc_lantern_wraith'
    assert director['relevantRecords']['enemies'][0]['id'] == 'lantern_wraith'
    assert director['relevantRecords']['segments'][0]['triggerType'] == 'state'
    assert director['relevantRecords']['segments'][0]['isTriggered'] is False


def test_large_imported_campaign_pack_keeps_dm_context_and_player_snapshot_bounded(client, app):
    locations = [
        {
            'id': f'loc_{index}',
            'name': f'Location {index}',
            'description': f'Pack location {index}.',
            'connectedLocationIds': [f'loc_{index + 1}'] if index < 249 else [],
            'visibleAtStart': index == 0,
        }
        for index in range(250)
    ]
    quests = [
        {
            'id': f'q_{index}',
            'title': f'Quest {index}',
            'status': 'active' if index == 0 else 'available',
            'summary': f'Pack quest {index}.',
            'visibleAtStart': index == 0,
        }
        for index in range(250)
    ]
    npcs = [
        {
            'id': f'npc_{index}',
            'name': f'NPC {index}',
            'role': 'Witness',
            'locationId': f'loc_{index % 250}',
            'questIds': [f'q_{index % 250}'],
            'visibleAtStart': index == 0,
        }
        for index in range(250)
    ]
    enemies = [
        {
            'id': f'enemy_{index}',
            'name': f'Enemy {index}',
            'creatureType': 'undead',
            'challengeTier': 'easy',
            'locationIds': [f'loc_{index % 250}'],
        }
        for index in range(150)
    ]
    encounters = [
        {
            'id': f'enc_{index}',
            'title': f'Encounter {index}',
            'locationIds': [f'loc_{index % 250}'],
            'questIds': [f'q_{index % 250}'],
            'checkpointIds': [f'cp_{index % 250}'],
            'enemyIds': [f'enemy_{index % 150}'],
        }
        for index in range(250)
    ]
    checkpoints = [
        {
            'id': f'cp_{index}',
            'title': f'Checkpoint {index}',
            'locationIds': [f'loc_{index}'],
            'questIds': [f'q_{index}'],
            'npcIds': [f'npc_{index}'],
            'encounterIds': [f'enc_{index}'],
            **({'nextCheckpointIds': [f'cp_{index + 1}']} if index < 249 else {}),
        }
        for index in range(250)
    ]
    segments = [
        {
            'id': f'seg_{index}',
            'title': f'Segment {index}',
            'description': f'Pack segment {index}.',
            'trigger': {'type': 'manual'},
        }
        for index in range(250)
    ]

    response = client.post(
        '/api/campaigns/import-pack',
        json={
            'packId': 'large_pack',
            'title': 'Large Pack',
            'startingState': {
                'locationId': 'loc_0',
                'questId': 'q_0',
                'currentScene': {
                    'locationId': 'loc_0',
                    'activeQuestIds': ['q_0'],
                    'activeNpcIds': ['npc_0'],
                },
            },
            'locations': locations,
            'quests': quests,
            'npcs': npcs,
            'enemies': enemies,
            'encounters': encounters,
            'checkpoints': checkpoints,
            'segments': segments,
            'directorRules': {'mainQuestGeneration': 'pack_only'},
        },
    )

    assert response.status_code == 201
    import_payload = response.get_json()
    assert import_payload['counts'] == {
        'locations': 250,
        'npcs': 250,
        'quests': 250,
        'segments': 250,
        'checkpoints': 250,
        'encounters': 250,
        'enemies': 150,
        'bestiary_entries': 150,
    }

    with app.app_context():
        campaign = db.session.get(Campaign, import_payload['campaign_id'])
        session = db.session.get(Session, import_payload['session_id'])
        raw_snapshot = safe_json_loads(session.state_snapshot, {})
        context = json.loads(build_dm_context(campaign.world_id, campaign.campaign_id, session.session_id))

    director = context['campaign_pack_director']
    assert director['enabled'] is True
    assert director['pack']['packId'] == 'large_pack'
    assert len(director['nextCheckpoints']) <= 4
    assert len(director['relevantRecords']['locations']) <= 6
    assert len(director['relevantRecords']['quests']) <= 4
    assert len(director['relevantRecords']['npcs']) <= 6
    assert len(director['relevantRecords']['encounters']) <= 4
    assert len(director['relevantRecords']['enemies']) <= 6
    assert len(director['relevantRecords']['segments']) <= 6
    assert len(director['progress']['checkpointStatuses']) <= 13
    assert 'loc_249' not in json.dumps(director['relevantRecords'])

    player_snapshot = filter_session_snapshot_for_player(raw_snapshot)
    assert 'catalog' not in player_snapshot['campaignPack']
    assert len(json.dumps(player_snapshot)) < len(json.dumps(raw_snapshot)) // 3


def test_build_dm_context_campaign_pack_director_flags_off_track_rejoin(app):
    with app.app_context():
        world = World(name='Off Track Pack World', description='world')
        db.session.add(world)
        db.session.flush()

        campaign = Campaign(title='Off Track Pack Campaign', world_id=world.world_id)
        db.session.add(campaign)
        db.session.flush()

        session = Session(
            campaign_id=campaign.campaign_id,
            state_snapshot=safe_json_dumps(
                {
                    'currentScene': {
                        'locationId': 'marsh_detour',
                        'name': 'Marsh Detour',
                        'activeQuestIds': ['q_missing_caravan', 'q_player_faction_war'],
                    },
                    'locations': [
                        {
                            'id': 'bleakmoor_gate',
                            'name': 'Bleakmoor Gate',
                            'source': 'campaign_pack',
                            'packId': 'bleakmoor_intro',
                        }
                    ],
                    'quests': [
                        {
                            'id': 'q_missing_caravan',
                            'title': 'Find the Missing Caravan',
                            'status': 'active',
                            'source': 'campaign_pack',
                            'packId': 'bleakmoor_intro',
                        }
                    ],
                    'knownNpcs': [
                        {
                            'id': 'npc_captain_veyra',
                            'name': 'Captain Veyra',
                            'status': 'dead',
                            'source': 'campaign_pack',
                            'packId': 'bleakmoor_intro',
                        }
                    ],
                    'clues': [
                        {
                            'id': 'clue_lantern_wax',
                            'title': 'Lantern Wax',
                            'status': 'destroyed',
                            'source': 'campaign_pack',
                            'packId': 'bleakmoor_intro',
                        }
                    ],
                    'combat': {
                        'status': 'ended',
                        'participants': [],
                        'flags': {
                            'campaignPackEncounterId': 'enc_lantern_wraith',
                            'campaignPackAllowedOutcomes': ['defeat', 'negotiate'],
                            'endReason': 'players_fled',
                        },
                    },
                    'campaignPack': {
                        'packId': 'bleakmoor_intro',
                        'title': 'The Lanterns of Bleakmoor',
                        'checkpoints': [
                            {
                                'id': 'cp_wreck',
                                'title': 'Find the caravan wreck',
                                'npcIds': ['npc_captain_veyra'],
                                'clueIds': ['clue_lantern_wax'],
                                'rejoinTargetCheckpointId': 'cp_wreck',
                            }
                        ],
                        'catalog': {
                            'locations': [{'id': 'bleakmoor_gate', 'name': 'Bleakmoor Gate'}],
                            'quests': [{'id': 'q_missing_caravan', 'title': 'Find the Missing Caravan'}],
                            'npcs': [{'id': 'npc_captain_veyra', 'name': 'Captain Veyra'}],
                            'clues': [{'id': 'clue_lantern_wax', 'title': 'Lantern Wax'}],
                        },
                        'directorRules': {'offTrackPolicy': 'improvise_and_reconnect'},
                    },
                },
                {},
            ),
        )
        db.session.add(session)
        db.session.commit()

        payload = json.loads(build_dm_context(world.world_id, campaign.campaign_id, session.session_id))

    progress = payload['campaign_pack_director']['progress']
    assert progress['offTrack'] is True
    assert progress['currentLocationId'] == 'marsh_detour'
    assert progress['rejoinTargetCheckpointId'] == 'cp_wreck'
    assert progress['offTrackScore'] >= 5
    assert set(progress['offTrackReasons']) >= {
        'locationOffTrack',
        'questOffTrack',
        'npcDependencyBroken',
        'requiredClueDestroyed',
        'combatOutcomeDiverged',
    }
    assert progress['offTrackDetails']['brokenNpcIds'] == ['npc_captain_veyra']
    assert progress['offTrackDetails']['brokenClueIds'] == ['clue_lantern_wax']
    assert progress['offTrackDetails']['combatOutcome'] == 'players_fled'


def test_build_dm_context_campaign_pack_director_honors_terminal_checkpoint_state(app):
    with app.app_context():
        world = World(name='Branched Pack World', description='world')
        db.session.add(world)
        db.session.flush()

        campaign = Campaign(title='Branched Pack Campaign', world_id=world.world_id)
        db.session.add(campaign)
        db.session.flush()

        session = Session(
            campaign_id=campaign.campaign_id,
            state_snapshot=safe_json_dumps(
                {
                    'currentScene': {
                        'locationId': 'watchtower_ruins',
                        'name': 'Watchtower Ruins',
                        'activeQuestIds': ['q_missing_caravan'],
                    },
                    'locations': [
                        {
                            'id': 'watchtower_ruins',
                            'name': 'Watchtower Ruins',
                            'source': 'campaign_pack',
                            'packId': 'bleakmoor_intro',
                        }
                    ],
                    'quests': [
                        {
                            'id': 'q_missing_caravan',
                            'title': 'Find the Missing Caravan',
                            'status': 'active',
                            'source': 'campaign_pack',
                            'packId': 'bleakmoor_intro',
                        }
                    ],
                    'flags': {
                        'campaignPackActiveCheckpointId': 'cp_gate',
                        'campaignPackCompletedCheckpointIds': ['cp_gate'],
                        'campaignPackSkippedCheckpointIds': ['cp_gate'],
                        'campaignPackFailedCheckpointIds': ['cp_old_road'],
                    },
                    'campaignPack': {
                        'packId': 'bleakmoor_intro',
                        'title': 'The Lanterns of Bleakmoor',
                        'activeCheckpointId': 'cp_gate',
                        'completedCheckpointIds': ['cp_gate'],
                        'skippedCheckpointIds': ['cp_gate'],
                        'failedCheckpointIds': ['cp_old_road'],
                        'directorRules': {'checkpointStyle': 'soft'},
                        'checkpoints': [
                            {'id': 'cp_gate', 'title': 'Question the gate captain'},
                            {'id': 'cp_chapel', 'title': 'Search the chapel', 'optional': True},
                            {'id': 'cp_old_road', 'title': 'Find the old road'},
                            {
                                'id': 'cp_watchtower',
                                'title': 'Enter the old watchtower',
                                'locationIds': ['watchtower_ruins'],
                                'questIds': ['q_missing_caravan'],
                                'prerequisiteCheckpointIds': ['cp_gate'],
                                'directorRules': {'checkpointStyle': 'firm_checkpoint'},
                            },
                        ],
                        'catalog': {
                            'locations': [
                                {
                                    'id': 'watchtower_ruins',
                                    'name': 'Watchtower Ruins',
                                    'source': 'campaign_pack',
                                    'packId': 'bleakmoor_intro',
                                }
                            ],
                            'quests': [
                                {
                                    'id': 'q_missing_caravan',
                                    'title': 'Find the Missing Caravan',
                                    'status': 'active',
                                    'source': 'campaign_pack',
                                    'packId': 'bleakmoor_intro',
                                }
                            ],
                        },
                    },
                },
                {},
            ),
        )
        db.session.add(session)
        db.session.commit()

        payload = json.loads(build_dm_context(world.world_id, campaign.campaign_id, session.session_id))

    director = payload['campaign_pack_director']
    assert director['activeCheckpoint']['id'] == 'cp_watchtower'
    assert director['policy']['checkpointStyle'] == 'firm_checkpoint'
    assert director['progress']['completedCheckpointIds'] == ['cp_gate']
    assert director['progress']['skippedCheckpointIds'] == ['cp_gate']
    assert director['progress']['failedCheckpointIds'] == ['cp_old_road']
    assert director['progress']['checkpointStatuses'] == {
        'cp_gate': 'skipped',
        'cp_old_road': 'failed',
        'cp_watchtower': 'active',
    }


def test_build_dm_context_live_world_state_is_bounded(app):
    with app.app_context():
        world = World(name='Bounded World', description='world')
        db.session.add(world)
        db.session.flush()

        campaign = Campaign(title='Bounded Campaign', world_id=world.world_id)
        db.session.add(campaign)
        db.session.flush()

        session = Session(
            campaign_id=campaign.campaign_id,
            state_snapshot=safe_json_dumps(
                {
                    'currentScene': {
                        'locationId': 'loc_0',
                        'name': 'Location 0',
                        'activeQuestIds': [f'quest_{index}' for index in range(10)],
                        'activeNpcIds': [f'npc_{index}' for index in range(10)],
                        'items': [
                            {'id': f'item_{index}', 'name': f'Item {index}', 'quantity': index + 1}
                            for index in range(20)
                        ],
                    },
                    'quests': [
                        {
                            'id': f'quest_{index}',
                            'title': f'Quest {index}',
                            'status': 'active',
                            'objectives': [
                                {'id': f'quest_{index}_objective_{objective}', 'description': 'Objective', 'status': 'open'}
                                for objective in range(6)
                            ],
                        }
                        for index in range(10)
                    ],
                    'locations': [
                        {
                            'id': f'loc_{index}',
                            'name': f'Location {index}',
                            'status': 'visited',
                            'lastVisitedTurn': index,
                        }
                        for index in range(12)
                    ],
                    'knownNpcs': [
                        {
                            'id': f'npc_{index}',
                            'name': f'NPC {index}',
                            'status': 'known',
                            'lastSeenTurn': index,
                        }
                        for index in range(12)
                    ],
                    'flags': {f'flag_{index:02d}': index for index in range(25)},
                },
                {},
            ),
        )
        db.session.add(session)
        db.session.commit()

        payload = json.loads(build_dm_context(world.world_id, campaign.campaign_id, session.session_id))

    live_state = payload['live_world_state']
    assert len(live_state['activeQuests']) == 5
    assert all(len(quest['objectives']) == 5 for quest in live_state['activeQuests'])
    assert len(live_state['recentLocations']) == 8
    assert live_state['recentLocations'][0]['id'] == 'loc_0'
    assert len(live_state['activeNpcs']) == 8
    assert len(live_state['recentKnownNpcs']) <= 8
    assert len(live_state['flags']) == 20
    assert len(live_state['currentScene']['items']) == 12


def test_build_dm_context_invalid_snapshot_keeps_existing_context_fields(app):
    with app.app_context():
        world = World(name='Invalid Snapshot World', description='world')
        db.session.add(world)
        db.session.flush()

        campaign = Campaign(title='Invalid Snapshot Campaign', world_id=world.world_id)
        db.session.add(campaign)
        db.session.flush()

        session = Session(campaign_id=campaign.campaign_id, state_snapshot='not-json')
        db.session.add(session)
        db.session.commit()

        payload = json.loads(build_dm_context(world.world_id, campaign.campaign_id, session.session_id))

    assert payload['live_world_state'] == {}
    for key in ['world', 'campaign', 'session_state', 'active_players', 'emergent_memory', 'recent_turns', 'pending_checks']:
        assert key in payload


def test_build_dm_context_shape_snapshot(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        payload = json.loads(
            build_dm_context(
                ids['world_id'],
                ids['campaign_id'],
                ids['session_id'],
                query_text='search the old ruins',
            )
        )

    payload['generated_at'] = '<generated-at>'
    payload['world']['world_id'] = '<world-id>'
    payload['campaign']['campaign_id'] = '<campaign-id>'
    payload['active_players'][0]['player_id'] = '<player-id>'
    spellbook = payload['active_players'][0]['state']['spellbook']
    known_spell_names = [spell['name'] for spell in spellbook['knownSpells']]
    assert {"Hunter's Mark", 'Pass without Trace', 'Minor Illusion', 'Detect Magic'} <= set(known_spell_names)
    assert any(name not in {"Hunter's Mark", 'Cure Wounds', 'Speak with Animals', 'Entangle', 'Goodberry'} for name in known_spell_names)
    spellbook['knownSpells'] = '<known-spells>'
    payload['active_players'][0]['state']['spells'] = '<spells>'

    assert payload == {
        'context_version': 'v2',
        'generated_at': '<generated-at>',
        'world': {
            'world_id': '<world-id>',
            'name': 'Test World',
            'description': 'A realm for tests',
        },
        'campaign': {
            'campaign_id': '<campaign-id>',
            'title': 'Test Campaign',
            'description': 'Campaign for tests',
            'current_quest': 'Find the relic',
            'location': 'Old Ruins',
        },
        'session_state': {
            'rolling_summary': '',
            'current_location': 'Old Ruins',
            'current_quest': 'Find the relic',
            'active_segments': [],
            'memory_snippets': [],
        },
        'live_world_state': {},
        'campaign_pack_director': {},
        'player_identity_rules': [
            'character_name is the in-world player character identity.',
            'Account/profile names are out-of-character labels and are not characters in the scene.',
            'Only active_players are currently active in this session unless recent narration explicitly says otherwise.',
        ],
        'active_players': [
                {
                    'player_id': '<player-id>',
                    'character_name': 'Seraphina',
                    'race': 'Elf',
                    'race_summary': {
                        'name': 'Elf',
                        'source': 'curated',
                        'summary': 'Long-lived, perceptive people shaped by magic, memory, beauty, and old grief.',
                        'traits': ['Darkvision', 'Keen Senses', 'Fey Ancestry', 'Trance'],
                        'aiNarrationHints': [
                            'Describe precise movement, old references, watchful stillness, and beauty that feels slightly unreal.'
                        ],
                        'originStory': (
                            'An Elf may remember a border before it was a kingdom, a tree before it was sacred, '
                            'or a lover whose grandchildren are now old. That long memory can be a gift, but it '
                            'can also make the present feel fragile and brief. An Elf adventurer often leaves home '
                            'when beauty becomes stillness, when grief becomes too familiar, or when the younger '
                            'world does something surprising enough to deserve attention.'
                        ),
                        'physical': {'averageHeight': '5 to 6.5 feet', 'averageWeight': '90 to 170 lb'},
                        'languages': ['Common', 'Elvish'],
                        'commonProficiencies': ['Perception', 'Arcana', 'Stealth'],
                        'balanceTier': 'standard',
                    },
                    'class': 'Ranger',
                'level': 3,
                'state': {
                    'ability_scores': {},
                    'ability_modifiers': {},
                    'point_buy': {'budget': 27, 'spent': None, 'remaining': None},
                    'hp': {'current': 0, 'max': 0, 'bloodied': False, 'critical': False},
                    'gold': 0,
                    'copper': 0,
                    'silver': 0,
                    'electrum': 0,
                    'platinum': 0,
                    'xp': 0,
                    'level': 3,
                    'proficiency_bonus': 2,
                    'spellbook': {
                        'knownSpells': '<known-spells>',
                        'preparedSpells': [],
                        'sources': [
                            'class:ranger',
                            'level:ranger:3',
                            'class_catalog:ranger:original:1',
                            'class_catalog:ranger:original:2',
                            'class_catalog:ranger:original:3',
                            'race:elf',
                            'race_catalog:elf',
                        ],
                    },
                    'spells': '<spells>',
                },
                'inventory': [],
                'recent_actions': [],
            }
        ],
        'triggered_segments': [],
        'authored_segments': [],
        'story_threads': [],
        'emergent_memory': {
            'entities': [],
            'facts': [],
            'threads': [],
            'projection': {
                'current_location': None,
                'current_quest': None,
                'rolling_summary': '',
            },
        },
        'recent_turns': [],
        'recent_log': [],
        'pending_checks': [],
    }
