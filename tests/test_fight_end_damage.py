"""The turn a hero's spell or attack ends a fight hurts only the enemy, whatever the narration says.

Seen live: a lone wizard's Fire Bolt dropped the last pale lantern, the fight ended, and the narrated
"The pale lantern takes 7 fire damage." was read as damage to the wizard herself.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
import requests

from aidm_server.database import db
from aidm_server.models import Session, safe_json_dumps, safe_json_loads

LLM_STYLE_NARRATION = 'Your strike lands true. The pale lantern takes 7 fire damage and gutters out.'


@pytest.fixture()
def road_fight(app, client, socketio, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr(requests.Session, 'request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    # The hero's d20s show 15 and damage dice their highest face; enemies roll 1 and miss.
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 14 if sides == 20 else sides - 1)
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda _sides: 1)
    clients = []

    def start(character_id: str, *, others: str) -> dict:
        response = client.post('/api/onboarding/play-now', json={'character_id': character_id})
        ids = {key: response.get_json()[key] for key in ('session_id', 'campaign_id', 'player_id', 'workspace_id')}
        socket_client = socketio.test_client(app, flask_test_client=app.test_client())
        socket_client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id'], 'workspace_id': ids['workspace_id']})
        socket_client.get_received()
        clients.append(socket_client)
        fight = {'ids': ids, 'socket': socket_client}
        send(fight, 'The party travels to The Old North Road.', {'kind': 'travel', 'source': 'scene_panel', 'location': {'id': 'loc_old_north_road', 'name': 'The Old North Road'}})
        with app.app_context():
            session_obj = db.session.get(Session, ids['session_id'])
            snapshot = safe_json_loads(session_obj.state_snapshot, {})
            combat = snapshot['combat']
            # The lone hero's one enemy is one hit from falling; a second one already fled or fell.
            last = next(participant for participant in combat['participants'] if participant['team'] == 'enemy')
            last.update(hp={'current': 1, 'max': last['hp']['max'], 'temp': 0}, armorClass=5)
            other = {**deepcopy(last), 'id': f"{last['id']}_other", 'name': f"{last['name']} 2"}
            if others == 'fled':
                other['conditions'] = ['fled']
            else:
                other.update(hp={'current': 0, 'max': other['hp']['max'], 'temp': 0}, isAlive=False, isConscious=False)
            combat['participants'].append(other)
            # Whatever the enemy's opening turn did to the hero, they can act and target it now.
            hero = next(participant for participant in combat['participants'] if participant['team'] == 'player')
            hero['conditions'] = []
            snapshot['playerCharacters'][0]['health']['conditions'] = []
            session_obj.state_snapshot = safe_json_dumps(snapshot, {})
            db.session.commit()
        fight['target'] = last['id']
        fight['hero_hp'] = snapshot['playerCharacters'][0]['health']['currentHp']
        assert snapshot['combat']['flags']['activeActorId'] == snapshot['playerCharacters'][0]['id']
        return fight

    def send(fight: dict, message: str, intent: dict) -> list[dict]:
        fight['socket'].emit(
            'send_message',
            {
                'session_id': fight['ids']['session_id'],
                'campaign_id': fight['ids']['campaign_id'],
                'player_id': fight['ids']['player_id'],
                'message': message,
                'action_intent': {**intent, 'text': message},
            },
        )
        received = fight['socket'].get_received()
        assert [event['args'][0] for event in received if event['name'] == 'error'] == []
        return received

    def snapshot(fight: dict) -> dict:
        with app.app_context():
            return safe_json_loads(db.session.get(Session, fight['ids']['session_id']).state_snapshot, {})

    yield start, send, snapshot
    for socket_client in clients:
        socket_client.disconnect()


def _fire_bolt(target_id: str) -> dict:
    return {
        'kind': 'spell',
        'source': 'composer',
        'spell': {'name': 'Fire Bolt', 'effect': 'A mote of fire streaks at the target.', 'target_ids': [target_id]},
    }


def _attack(send, fight: dict, app) -> None:
    """The HUD's attack on the enemy, closing in first when it must (one action)."""

    from aidm_server.combat.legal_actions import legal_combat_actions_for_player
    from aidm_server.models import Player

    with app.app_context():
        state = safe_json_loads(db.session.get(Session, fight['ids']['session_id']).state_snapshot, {})
        bundle = legal_combat_actions_for_player(state, db.session.get(Player, fight['ids']['player_id']))
    attack = next(action for action in bundle['actions'] if action['id'].startswith('combat.attack.') and action['available'])
    send(fight, 'Arden Vale attacks.', {'kind': 'combat', 'source': 'combat_hud', 'combat': {'action_id': attack['id'], 'target_id': fight['target']}})


@pytest.mark.parametrize('others', ['fell', 'fled'])
@pytest.mark.parametrize('narration', ['scripted', 'llm_style'])
def test_a_spell_that_ends_the_fight_leaves_the_caster_unhurt(road_fight, monkeypatch, others, narration):
    start, send, snapshot = road_fight
    fight = start('liora-quill', others=others)
    if narration == 'llm_style':
        monkeypatch.setattr('aidm_server.scripted_narrator.narrate', lambda *_args, **_kwargs: LLM_STYLE_NARRATION)

    send(fight, 'Liora casts Fire Bolt.', _fire_bolt(fight['target']))
    after = snapshot(fight)

    assert after['combat']['status'] == 'ended'
    assert after['combat']['flags']['endReason'] == ('enemies_fled' if others == 'fled' else 'all_enemies_defeated')
    assert after['playerCharacters'][0]['health']['currentHp'] == fight['hero_hp']
    assert after['playerCharacters'][0]['health']['conditions'] == []


@pytest.mark.parametrize('others', ['fell', 'fled'])
@pytest.mark.parametrize('narration', ['scripted', 'llm_style'])
def test_a_weapon_attack_that_ends_the_fight_leaves_the_attacker_unhurt(app, road_fight, monkeypatch, others, narration):
    start, send, snapshot = road_fight
    fight = start('arden-vale', others=others)
    if narration == 'llm_style':
        monkeypatch.setattr('aidm_server.scripted_narrator.narrate', lambda *_args, **_kwargs: LLM_STYLE_NARRATION)

    _attack(send, fight, app)
    after = snapshot(fight)

    assert after['combat']['status'] == 'ended'
    assert after['combat']['flags']['endReason'] == ('enemies_fled' if others == 'fled' else 'all_enemies_defeated')
    assert after['playerCharacters'][0]['health']['currentHp'] == fight['hero_hp']
    assert after['playerCharacters'][0]['health']['conditions'] == []


def test_a_fight_that_ends_on_the_enemies_turn_hands_no_turn_on(app, road_fight):
    start, send, snapshot = road_fight
    fight = start('arden-vale', others='fled')

    send(fight, 'Arden Vale ends the turn.', {'kind': 'combat', 'source': 'combat_hud', 'combat': {'action_id': 'combat.end_turn'}})
    after = snapshot(fight)
    with app.app_context():
        from aidm_server.models import SessionLogEntry

        narration = (
            SessionLogEntry.query.filter_by(session_id=fight['ids']['session_id'], entry_type='dm')
            .filter(SessionLogEntry.message.startswith('DM: '))
            .order_by(SessionLogEntry.id.desc())
            .first()
            .message
        )

    # Badly hurt and alone, the lantern flees and the fight is over: nobody is told it is their turn.
    assert after['combat']['flags']['endReason'] == 'enemies_fled'
    assert 'The pale lantern breaks and runs.' in narration
    assert 'It is your turn.' not in narration and 'is up next' not in narration


def test_a_typed_pickup_in_a_fight_is_refused_plainly(road_fight):
    start, _send, snapshot = road_fight
    fight = start('arden-vale', others='fell')

    fight['socket'].emit(
        'send_message',
        {
            'session_id': fight['ids']['session_id'],
            'campaign_id': fight['ids']['campaign_id'],
            'player_id': fight['ids']['player_id'],
            'message': 'Arden Vale picks up the torch.',
            'action_intent': {
                'kind': 'item',
                'source': 'scene_panel',
                'text': 'Arden Vale picks up the torch.',
                'inventory_action': 'pick_up',
                'item': {'id': 'road_cart_torch', 'name': 'Torch', 'quantity': 1},
            },
        },
    )

    errors = [event['args'][0]['error'] for event in fight['socket'].get_received() if event['name'] == 'error']
    assert errors == ['Not while the fight is on.']
    assert snapshot(fight)['combat']['status'] == 'active'
