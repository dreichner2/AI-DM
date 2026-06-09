from __future__ import annotations

import json

from aidm_server.database import db
from aidm_server.llm import build_dm_context
from aidm_server.models import Campaign, Player, PlayerAction, Session, SessionState, World, safe_json_dumps
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
