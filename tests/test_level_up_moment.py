"""Levelling up is a moment: the level beat carries what the hero gained, from their sheet before the turn to after it.

The socket tests play the Play Now adventure's road fight in Safe Mode; its victory's encounter reward carries the
heroes over a level threshold through the real applier.
"""

from __future__ import annotations

import pytest

from aidm_server.api_type_contract import API_TYPE_CONTRACT_BY_NAME
from aidm_server.character_state import character_state_for_player
from aidm_server.database import db
from aidm_server.game_state.orchestration.story_beats import story_beats
from aidm_server.models import Player, Session, SessionLogEntry, safe_json_dumps, safe_json_loads
from aidm_server.response_dtos import session_log_entry_payload
from tests.test_party_play import PartyTable


ACTOR = 'player_1'
ACTION_SURGE = {'name': 'Action Surge', 'text': 'Regain the action for the current combat turn.'}


@pytest.fixture()
def quiet_road(app, monkeypatch):
    """Safe Mode with seeded dice: the heroes hit with 15 and their highest damage, and every enemy attack misses."""

    def fail_network(*_args, **_kwargs):
        raise AssertionError('Safe Mode must not call a network or CLI model')

    monkeypatch.setattr('requests.Session.request', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.http_post', fail_network)
    monkeypatch.setattr('aidm_server.llm_providers.subprocess.Popen', fail_network)
    app.config.update(AIDM_LLM_PROVIDER='fallback', AIDM_LLM_MODEL='deterministic-v1')
    monkeypatch.setattr('aidm_server.interactables.deterministic_roller', lambda _seed: lambda _sides: 15)
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda sides: 14 if sides == 20 else sides - 1)
    monkeypatch.setattr('aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda _sides: 1)


def _sheet(table: PartyTable, hero: str) -> dict:
    """The hero's character sheet as the server reads it from their row."""

    with table.app.app_context():
        state = character_state_for_player(db.session.get(Player, table.players[hero]))
    resources = state.get('spell_resources') or {}
    slots = {int(level): slot['max'] for level, slot in (resources.get('slots') or {}).items() if slot['max']}
    return {
        'level': state['level'],
        'maxHp': state['hp']['max'],
        'proficiencyBonus': state['proficiency_bonus'],
        'slots': slots,
        'spells': state.get('spells') or [],
        'features': {capability['baseId']: capability['name'] for capability in state.get('class_capabilities') or []},
    }


def _slot_changes(before: dict, after: dict) -> list[dict]:
    return [
        {'level': level, 'from': before['slots'].get(level, 0), 'to': after['slots'].get(level, 0)}
        for level in sorted({*before['slots'], *after['slots']})
        if before['slots'].get(level, 0) != after['slots'].get(level, 0)
    ]


def _xp(table: PartyTable, hero: str, amount: int) -> None:
    table.operator([{'type': 'xp.add', 'actorId': table.hero_id(hero), 'amount': amount}])


def _road_fight_beats(table: PartyTable) -> list[dict]:
    """Travel to the Old North Road and win its fight; the beats of the turn that won it."""

    start = table.last_turn_id()
    table.travel('Arden Vale', 'loc_old_north_road', 'The Old North Road')
    table.fight()
    assert table.snapshot()['combat']['flags']['endReason'] == 'all_enemies_defeated'
    return table.turns_since(start)[-1]['beats']


def _level_beats(beats: list[dict]) -> list[dict]:
    return [beat for beat in beats if beat['kind'] == 'level']


def _fits(type_name: str, payload: dict) -> bool:
    """``payload`` has the required fields of the API contract's ``type_name`` and no field it does not declare."""

    fields = API_TYPE_CONTRACT_BY_NAME[type_name].fields
    return {field.name for field in fields if not field.optional} <= set(payload) <= {field.name for field in fields}


def _declared(beats: list[dict]) -> bool:
    """Every beat, and every part of a level beat's object, is declared in the API contract."""

    levels = [beat['level'] for beat in _level_beats(beats)]
    return (
        all(_fits('StoryBeatRecord', beat) for beat in beats)
        and all(_fits('StoryBeatLevel', level) and _fits('StoryBeatLevelChange', level['maxHp']) for level in levels)
        and all(_fits('StoryBeatLevelChange', level['proficiencyBonus']) for level in levels if 'proficiencyBonus' in level)
        and all(_fits('StoryBeatLevelFeature', feature) for level in levels for feature in level.get('features', []))
        and all(_fits('StoryBeatLevelSlots', slot) for level in levels for slot in level.get('spellSlots', []))
    )


def test_a_fighter_reaching_level_two_alone_gains_what_their_sheet_gained(app, client, socketio, quiet_road):
    table = PartyTable(app, client, socketio, party=False)
    try:
        _xp(table, 'Arden Vale', 299)
        before = _sheet(table, 'Arden Vale')
        beats = _road_fight_beats(table)
        after = _sheet(table, 'Arden Vale')
        logged = table.client.get(
            f"/api/sessions/{table.ids['session_id']}/log?view=player&player_id={table.ids['player_id']}"
        ).get_json()['entries'][-1]['metadata']['storyBeats']
    finally:
        table.close()

    assert (before['level'], after['level']) == (1, 2)
    assert after['maxHp'] > before['maxHp']
    assert set(after['features']) - set(before['features']) == {'action_surge'}
    assert _level_beats(beats) == [
        {
            'kind': 'level',
            'text': 'Level 2 reached',
            'level': {'hero': 'Arden Vale', 'level': 2, 'maxHp': {'from': before['maxHp'], 'to': after['maxHp']}, 'features': [ACTION_SURGE]},
        }
    ]
    # The maximum rising is told by the level beat, not as healing.
    assert [beat for beat in beats if beat['kind'] == 'hp'] == []
    # Persisted with the turn's beats, and in the player view after a reload.
    assert logged == beats


def test_a_cleric_reaching_level_three_beside_a_fighter_reaching_level_two_in_party_play(app, client, socketio, quiet_road):
    table = PartyTable(app, client, socketio, party=True)
    try:
        _xp(table, 'Arden Vale', 299)
        _xp(table, 'Seren Ashgrove', 899)
        before = {hero: _sheet(table, hero) for hero in ('Arden Vale', 'Seren Ashgrove')}
        beats = _road_fight_beats(table)
        after = {hero: _sheet(table, hero) for hero in ('Arden Vale', 'Seren Ashgrove')}
    finally:
        table.close()

    arden, seren = (before['Arden Vale'], after['Arden Vale']), (before['Seren Ashgrove'], after['Seren Ashgrove'])
    assert (arden[0]['level'], arden[1]['level'], seren[0]['level'], seren[1]['level']) == (1, 2, 2, 3)
    new_spells = [spell for spell in seren[1]['spells'] if spell not in seren[0]['spells']]
    assert set(new_spells) == {'Aid', 'Blindness/Deafness', 'Hold Person', 'Lesser Restoration', 'Prayer of Healing', 'Spiritual Weapon'}
    assert set(seren[1]['features']) == set(seren[0]['features'])
    assert _slot_changes(*seren) == [{'level': 1, 'from': 3, 'to': 4}, {'level': 2, 'from': 0, 'to': 2}]
    # The hero who won the fight is told first.
    assert sorted(_level_beats(beats), key=lambda beat: beat['text']) == [
        {
            'kind': 'level',
            'text': 'Arden Vale reached level 2',
            'level': {'hero': 'Arden Vale', 'level': 2, 'maxHp': {'from': arden[0]['maxHp'], 'to': arden[1]['maxHp']}, 'features': [ACTION_SURGE]},
        },
        {
            'kind': 'level',
            'text': 'Seren Ashgrove reached level 3',
            'level': {
                'hero': 'Seren Ashgrove',
                'level': 3,
                'maxHp': {'from': seren[0]['maxHp'], 'to': seren[1]['maxHp']},
                'spellSlots': _slot_changes(*seren),
                'spells': new_spells,
            },
        },
    ]
    assert [beat for beat in beats if beat['kind'] == 'hp'] == []
    assert _declared(beats)
    assert 'storyBeats?: StoryBeatRecord[]' in (API_TYPE_CONTRACT_BY_NAME['SessionLogEntryMetadata'].alias or '')


def test_two_levels_in_one_turn_are_one_beat_from_the_sheet_before_the_turn(app, client, socketio, quiet_road):
    table = PartyTable(app, client, socketio, party=False)
    try:
        # The road fight's reward is raised to carry a level 1 hero past level 2 into level 3.
        with app.app_context():
            session_obj = db.session.get(Session, table.ids['session_id'])
            snapshot = safe_json_loads(session_obj.state_snapshot, {})
            road = next(encounter for encounter in snapshot['campaignPack']['catalog']['encounters'] if encounter['id'] == 'enc_pale_lantern_detour')
            road['rewards']['xp'] = 900
            session_obj.state_snapshot = safe_json_dumps(snapshot, {})
            db.session.commit()
        before = _sheet(table, 'Arden Vale')
        beats = _road_fight_beats(table)
        after = _sheet(table, 'Arden Vale')
    finally:
        table.close()

    assert (before['level'], after['level']) == (1, 3)
    assert _level_beats(beats) == [
        {
            'kind': 'level',
            'text': 'Level 3 reached',
            'level': {'hero': 'Arden Vale', 'level': 3, 'maxHp': {'from': before['maxHp'], 'to': after['maxHp']}, 'features': [ACTION_SURGE]},
        }
    ]


# The beat on its own -----------------------------------------------------------------------------------------------


def _hero(class_name: str, **fields) -> dict:
    """A hero's record in the beats basis."""

    return {'name': 'Liora Quill', 'hp': 9, 'conditions': [], 'items': {}, 'currency': {}, 'xp': 0, 'class': class_name, **fields}


def _basis(hero: dict) -> dict:
    return {'heroes': {ACTOR: hero}}


def test_a_level_that_raises_the_proficiency_bonus_and_grants_many_spells_names_eight():
    before = _basis(_hero('Wizard', level=1, maxHp=8, spellSlots={'1': 2}))
    after = _basis(_hero('Wizard', level=5, maxHp=27, xp=6500, spellSlots={'1': 4, '2': 3, '3': 2}))

    beat = story_beats(before, after, actor_id=ACTOR, state_before={}, applied_changes=[])[-1]

    assert beat['kind'] == 'level'
    level = beat['level']
    assert (level['hero'], level['level'], level['maxHp'], level['proficiencyBonus']) == (
        'Liora Quill', 5, {'from': 8, 'to': 27}, {'from': 2, 'to': 3}
    )
    assert level['spellSlots'] == [{'level': 1, 'from': 2, 'to': 4}, {'level': 2, 'from': 0, 'to': 3}, {'level': 3, 'from': 0, 'to': 2}]
    # Fourteen spells come with levels 3 and 5: eight are named, six counted.
    assert level['spells'] == [
        'Blindness/Deafness', 'Flaming Sphere', 'Hold Person', 'Invisibility', 'Misty Step', 'Scorching Ray', 'Shatter', 'Web'
    ]
    assert level['more'] == 6
    assert 'features' not in level
    assert _declared([beat])


def test_a_level_with_only_more_hit_points_carries_max_hp_alone():
    beats = story_beats(
        _basis(_hero('Fighter', level=3, maxHp=28, spellSlots={}, xp=2600)),
        _basis(_hero('Fighter', level=4, maxHp=36, spellSlots={}, xp=2700)),
        actor_id=ACTOR,
        state_before={},
        applied_changes=[],
    )

    assert beats[-1] == {
        'kind': 'level',
        'text': 'Level 4 reached',
        'level': {'hero': 'Liora Quill', 'level': 4, 'maxHp': {'from': 28, 'to': 36}},
    }


@pytest.mark.parametrize(
    ('hp_after', 'hp_beats'),
    [
        # Healed by the level alone: no hp beat.
        (17, []),
        # 3 damage taken in the turn, then the level's 8: the damage is the beat.
        (14, [{'kind': 'hp', 'text': 'Took 3 damage'}]),
        # A hero who is down levels up in maximum only.
        (0, [{'kind': 'hp', 'text': 'Took 9 damage'}]),
    ],
)
def test_the_hit_points_a_level_added_are_not_also_an_hp_beat(hp_after, hp_beats):
    before = _basis(_hero('Fighter', level=1, hp=9, maxHp=12, spellSlots={}, xp=250))
    after = _basis(_hero('Fighter', level=2, hp=hp_after, maxHp=20, spellSlots={}, xp=350))

    beats = story_beats(before, after, actor_id=ACTOR, state_before={}, applied_changes=[])

    assert [beat for beat in beats if beat['kind'] == 'hp'] == hp_beats
    assert [beat['kind'] for beat in beats if beat['kind'] != 'hp'] == ['xp', 'level']


def test_the_player_view_keeps_the_level_object_and_still_accepts_an_old_one_line_beat(app):
    old = {'kind': 'level', 'text': 'Level 2 reached'}
    new = {**old, 'level': {'hero': 'Arden Vale', 'level': 2, 'maxHp': {'from': 12, 'to': 20}, 'features': [ACTION_SURGE]}}
    with app.app_context():
        entries = [
            SessionLogEntry(session_id=1, message='DM: The road is quiet.', entry_type='dm', metadata_json=safe_json_dumps({'turn_id': 7, 'storyBeats': [beat]}, {}))
            for beat in (old, new)
        ]
        payloads = [
            session_log_entry_payload(entry, include_private=False, private_player_ids=frozenset(), turn_player_ids={7: 2})
            for entry in entries
        ]

    assert [payload['metadata']['storyBeats'] for payload in payloads] == [[old], [new]]
