"""Bring a hero you already have: the heroes a caller may bring into an adventure, and the copy that joins it."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from aidm_server.auth import hash_secret
from aidm_server.database import db
from aidm_server.models import Account, AccountWorkspaceMembership, Campaign, DmTurn, Player, Session, World, safe_json_dumps, safe_json_loads
from aidm_server.time_utils import utc_now
from tests.test_auth import _build_auth_runtime
from tests.test_party_play import CLERIC

QUEST_ITEM = {'id': 'sealed_letter', 'name': 'Sealed Letter', 'type': 'quest', 'quantity': 1}
TIES = {
    'desire': {'kind': 'find_someone'},
    'attachment': {'name': 'Tamsin Ashgrove', 'relation': 'mother'},
    'burden': 'A temple bell you rang the night the fire spread.',
}


def _adventure(app, title: str, *, status: str = 'active') -> int:
    with app.app_context():
        world = World(name=f'{title} World')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title=title, world_id=world.world_id, status=status)
        db.session.add(campaign)
        db.session.flush()
        db.session.add(Session(campaign_id=campaign.campaign_id))
        db.session.commit()
        return campaign.campaign_id


def _hero(client, campaign_id: int, name: str, headers: dict | None = None, **extra) -> int:
    response = client.post(
        f'/api/players/campaigns/{campaign_id}/players',
        json={**CLERIC, 'character_name': name, 'ties': TIES, **extra},
        headers=headers or {},
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()['player_id']


def _played(app, player_id: int, *, minutes_ago: int) -> None:
    with app.app_context():
        player = db.session.get(Player, player_id)
        session_obj = Session.query.filter_by(campaign_id=player.campaign_id).first()
        db.session.add(
            DmTurn(
                session_id=session_obj.session_id,
                campaign_id=player.campaign_id,
                player_id=player_id,
                player_input='I look around.',
                dm_output='The road runs on.',
                status='completed',
                created_at=utc_now() - timedelta(minutes=minutes_ago),
            )
        )
        db.session.commit()


def _record(app, player_id: int) -> dict[str, Any]:
    with app.app_context():
        player = db.session.get(Player, player_id)
        return {
            'campaign_id': player.campaign_id,
            'character_name': player.character_name,
            'race': player.race,
            'race_selection': player.race_selection,
            'sex': player.sex,
            'class_': player.class_,
            'level': player.level,
            'stats': safe_json_loads(player.stats, {}),
            'inventory': safe_json_loads(player.inventory, []),
            'weapon_proficiencies': player.weapon_proficiencies,
            'character_sheet': safe_json_loads(player.character_sheet, {}),
        }


def _wear_down(app, player_id: int) -> None:
    """What a hero carries out of a hard adventure: wounds, a condition, spent magic, a quest item, Inspiration."""

    with app.app_context():
        player = db.session.get(Player, player_id)
        stats = safe_json_loads(player.stats, {})
        stats.update(
            current_hp=3,
            hp_current=3,
            temp_hp=4,
            xp=450,
            experience=450,
            gold=37,
            conditions=['poisoned'],
            death_saves={'successes': 1, 'failures': 1},
            inspiration=True,
            active_effects=[{'id': 'inspired', 'sourceName': 'Inspiration', 'modifiers': {'checkMode': 'advantage'}}],
            hit_dice=0,
        )
        player.stats = safe_json_dumps(stats, {})
        sheet = safe_json_loads(player.character_sheet, {})
        sheet['spellResources']['slots']['1']['current'] = 0
        player.character_sheet = safe_json_dumps(sheet, {})
        player.inventory = safe_json_dumps([*safe_json_loads(player.inventory, []), QUEST_ITEM], [])
        db.session.commit()


def test_the_joinable_heroes_are_the_tables_heroes_of_other_adventures_newest_played_first(app, client):
    here = _adventure(app, 'The Greenway')
    road = _adventure(app, 'The Road of Unremembered Kings')
    archived = _adventure(app, 'The Old Barrows', status='archived')
    deleted = _adventure(app, 'The Lost Mere', status='deleted')
    _hero(client, here, 'Already Here')
    seren = _hero(client, road, 'Seren Ashgrove')
    corin = _hero(client, archived, 'Corin Ashby')
    _hero(client, deleted, 'Gone Hero')
    _hero(client, road, 'already here')
    _played(app, seren, minutes_ago=30)
    _played(app, corin, minutes_ago=5)

    response = client.get(f'/api/players/campaigns/{here}/joinable-heroes')

    assert response.status_code == 200
    heroes = response.get_json()['heroes']
    assert [hero['character_name'] for hero in heroes] == ['Corin Ashby', 'Seren Ashgrove']
    seren_row = heroes[1]
    assert {key: seren_row[key] for key in ('player_id', 'level', 'race', 'class', 'adventure_title')} == {
        'player_id': seren,
        'level': 1,
        'race': 'Human',
        'class': 'Cleric',
        'adventure_title': 'The Road of Unremembered Kings',
    }
    assert seren_row['portrait'].startswith('/') or seren_row['portrait'].startswith('data:')
    assert seren_row['last_played_at']


def test_at_most_twenty_four_heroes_are_offered(app, client):
    here = _adventure(app, 'The Greenway')
    road = _adventure(app, 'The Road of Unremembered Kings')
    for index in range(26):
        _hero(client, road, f'Hero {index:02d}')

    heroes = client.get(f'/api/players/campaigns/{here}/joinable-heroes').get_json()['heroes']

    assert len(heroes) == 24


def test_a_brought_hero_is_a_copy_at_full_strength_and_the_original_is_untouched(app, client):
    road = _adventure(app, 'The Road of Unremembered Kings')
    here = _adventure(app, 'The Greenway')
    seren = _hero(client, road, 'Seren Ashgrove')
    _wear_down(app, seren)
    original = _record(app, seren)

    response = client.post(f'/api/players/campaigns/{here}/players/bring', json={'player_id': seren})

    assert response.status_code == 201
    created = response.get_json()
    assert set(created) == {'player_id', 'message'} and created['message'] == 'Player successfully created'
    assert _record(app, seren) == original
    copy = _record(app, created['player_id'])
    assert copy['campaign_id'] == here
    for key in ('character_name', 'race', 'race_selection', 'sex', 'class_', 'level', 'weapon_proficiencies'):
        assert copy[key] == original[key], key
    stats = copy['stats']
    assert (stats['xp'], stats['gold'], stats['ability_scores']) == (450, 37, original['stats']['ability_scores'])
    assert stats['current_hp'] == stats['max_hp'] == original['stats']['max_hp']
    for left_behind in ('conditions', 'death_saves', 'temp_hp', 'inspiration', 'active_effects', 'hit_dice'):
        assert left_behind not in stats, left_behind
    assert QUEST_ITEM['id'] not in {item['id'] for item in copy['inventory']}
    assert [item['id'] for item in copy['inventory']] == [item['id'] for item in original['inventory'] if item['type'] != 'quest']
    sheet = copy['character_sheet']
    assert sheet['ties'] == {**TIES, 'desire': {'kind': 'find_someone', 'words': 'to find someone'}}
    assert sheet['spellbook'] == original['character_sheet']['spellbook']
    assert sheet['spellResources']['slots']['1'] == {'current': 2, 'max': 2}
    detail = client.get(f"/api/players/{created['player_id']}").get_json()
    assert detail['profile_image'] == client.get(f'/api/players/{seren}').get_json()['profile_image']


def test_bringing_refuses_the_same_adventure_a_name_already_there_and_another_tables_hero(app, client):
    road = _adventure(app, 'The Road of Unremembered Kings')
    here = _adventure(app, 'The Greenway')
    seren = _hero(client, road, 'Seren Ashgrove')
    _hero(client, here, 'seren ashgrove')

    same = client.post(f'/api/players/campaigns/{road}/players/bring', json={'player_id': seren})
    taken = client.post(f'/api/players/campaigns/{here}/players/bring', json={'player_id': seren})
    missing = client.post(f'/api/players/campaigns/{here}/players/bring', json={'player_id': 9999})
    with app.app_context():
        elsewhere = db.session.get(Player, seren)
        elsewhere.workspace_id = 'another-table'
        db.session.commit()
    other_table = client.post(f'/api/players/campaigns/{here}/players/bring', json={'player_id': seren})

    assert same.status_code == 400
    assert taken.status_code == 409
    assert taken.get_json()['error'] == 'A hero named Seren Ashgrove is already in this adventure.'
    assert missing.status_code == 403
    assert other_table.status_code == 403
    assert client.post(f'/api/players/campaigns/{here}/players/bring', json={}).status_code == 400


@pytest.fixture()
def signed_in(tmp_path, monkeypatch):
    app, _socketio = _build_auth_runtime(tmp_path, monkeypatch)
    with app.app_context():
        accounts = []
        for username in ('mira', 'tobin'):
            account = Account(
                username=username,
                first_name=username.title(),
                last_name='Player',
                password_hash='configured',
                account_token_hash=hash_secret(f'{username}-token'),
            )
            db.session.add(account)
            db.session.flush()
            db.session.add(AccountWorkspaceMembership(account_id=account.account_id, workspace_id='owner', role='player'))
            accounts.append(account.account_id)
        db.session.commit()
    headers = {name: {'Authorization': f'Bearer {name}-token', 'X-AIDM-Workspace-Id': 'owner'} for name in ('mira', 'tobin')}
    return app, headers


def test_with_sign_in_on_a_caller_lists_and_brings_only_their_own_heroes(signed_in):
    app, headers = signed_in
    client = app.test_client()
    road = _adventure(app, 'The Road of Unremembered Kings')
    here = _adventure(app, 'The Greenway')
    mine = _hero(client, road, 'Seren Ashgrove', headers['mira'])
    theirs = _hero(client, road, 'Corin Ashby', headers['tobin'])

    listed = client.get(f'/api/players/campaigns/{here}/joinable-heroes', headers=headers['mira'])
    refused = client.post(f'/api/players/campaigns/{here}/players/bring', json={'player_id': theirs}, headers=headers['mira'])
    brought = client.post(f'/api/players/campaigns/{here}/players/bring', json={'player_id': mine}, headers=headers['mira'])

    assert [hero['player_id'] for hero in listed.get_json()['heroes']] == [mine]
    assert refused.status_code == 403
    assert brought.status_code == 201
    with app.app_context():
        assert db.session.get(Player, brought.get_json()['player_id']).account_id == db.session.get(Player, mine).account_id
