from __future__ import annotations

from aidm_server.database import db
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY
from aidm_server.game_state.orchestration import turn_pipeline
from aidm_server.models import Campaign, DmTurn, Player, Session, safe_json_dumps, safe_json_loads
from tests.helpers import seed_world_campaign_player_session


def test_lethal_ongoing_damage_finishes_the_encounter_on_turn_advance(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    actor_id = f"player_{ids['player_id']}"
    monkeypatch.setattr('aidm_server.combat.pipeline.deterministic_roller', lambda _seed: lambda _sides: 4)
    monkeypatch.setattr(turn_pipeline, 'recent_timeline_for_session', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(turn_pipeline, 'prepare_combat_from_dm_response', lambda **_kwargs: {'changes': [], 'debug': {}})
    monkeypatch.setattr(turn_pipeline, 'extract_post_dm_outcomes', lambda **_kwargs: {
        'proposedChanges': [], 'uncertainChanges': [], 'notes': [], 'debug': {},
    })
    with app.app_context():
        campaign = db.session.get(Campaign, ids['campaign_id'])
        session = db.session.get(Session, ids['session_id'])
        player = db.session.get(Player, ids['player_id'])
        player.stats = safe_json_dumps({'current_hp': 10, 'max_hp': 10, 'gold': 1}, {})
        state = {
            'sessionId': ids['session_id'], 'campaignId': ids['campaign_id'],
            'currentScene': {'name': 'Road', 'combatState': 'active'},
            'playerCharacters': [{
                'id': actor_id, 'playerId': ids['player_id'], 'name': 'Seraphina',
                'health': {'currentHp': 10, 'maxHp': 10, 'tempHp': 0, 'conditions': []},
                'inventory': {'items': [], 'currency': {}}, 'xp': {'current': 0},
            }],
            'combat': {
                'status': 'active', 'round': 1, 'turnIndex': 0,
                'participants': [
                    {'id': actor_id, 'name': 'Seraphina', 'team': 'player', 'kind': 'player_character',
                     'hp': {'current': 10, 'max': 10, 'temp': 0}, 'conditions': []},
                    {'id': 'enemy_1', 'name': 'Bandit', 'team': 'enemy', 'kind': 'creature',
                     'hp': {'current': 3, 'max': 10, 'temp': 0}, 'conditions': [],
                     'activeEffects': [{'id': 'burn', 'modifiers': {'damagePerTurn': {'dice': '1d4', 'type': 'fire'}},
                                        'duration': {'rounds': 2, 'tickOn': 'target_turn_end'}}]},
                ],
                'initiative': [{'participantId': actor_id, 'order': 0}, {'participantId': 'enemy_1', 'order': 1}],
                'flags': {'activeActorId': actor_id, 'campaignPackEncounterId': 'enc_bandit'},
            },
            'campaignPack': {'packId': 'road', 'catalog': {
                'encounters': [{'id': 'enc_bandit', 'title': 'Bandit', 'rewards': {'gp': 4}}],
            }},
            'flags': {}, 'stateChangeLedger': [],
        }
        session.state_snapshot = safe_json_dumps(state, {})
        turn = DmTurn(
            session_id=session.session_id, campaign_id=campaign.campaign_id, player_id=player.player_id,
            player_input='I end my turn.', dm_output='The bandit burns.', status='completed',
            metadata_json=safe_json_dumps({
                'action_intent': {'kind': 'combat', 'combat': {
                    'action_id': 'combat.end_turn', 'authoritative': True, 'economy': {'endsTurn': True},
                }},
                STATE_PIPELINE_METADATA_KEY: {
                    'actorId': actor_id, 'stateBeforeDm': state, 'preDmValidation': {},
                    'immediateValidation': {'rejected': []}, 'immediateAppliedChanges': [], 'combatAppliedChanges': [],
                },
            }, {}),
        )
        db.session.add(turn)
        db.session.flush()
        turn_pipeline.post_dm_pipeline(
            turn=turn, session_obj=session, campaign=campaign, player=player, dm_response_text=turn.dm_output,
        )
        snapshot = safe_json_loads(session.state_snapshot, {})
        assert snapshot['combat']['participants'][1]['hp']['current'] == 0
        assert snapshot['combat']['status'] == 'ended'
        assert snapshot['combat']['flags']['endReason'] == 'all_enemies_defeated'
        assert safe_json_loads(player.stats, {})['gold'] == 5
        assert len(snapshot['combatRewardLedger']) == 1
