"""The last pass from the full replay, played through the real socket turn pipeline: enemy conditions wear off,
a wound penalty is named, bosses stand their ground, refusals and fight lines read right."""

from __future__ import annotations

from aidm_server.models import DmTurn, safe_json_loads

from tests.test_flagship_adventure import _hero, table  # noqa: F401 - the Play Now table fixture

THREAT_CHANGE_TYPES = frozenset({'health.damage', 'combat.condition.add'})


def _turns_since(table, turn_id: int) -> list[dict]:  # noqa: F811
    with table.app.app_context():
        turns = (
            DmTurn.query.filter(DmTurn.session_id == table.ids['session_id'], DmTurn.turn_id > turn_id)
            .order_by(DmTurn.turn_id.asc())
            .all()
        )
        return [{'id': turn.turn_id, 'narration': turn.dm_output or '', 'metadata': safe_json_loads(turn.metadata_json, {})} for turn in turns]


def _last_turn_id(table) -> int:  # noqa: F811
    with table.app.app_context():
        turn = DmTurn.query.filter_by(session_id=table.ids['session_id']).order_by(DmTurn.turn_id.desc()).first()
        return turn.turn_id if turn else 0


def _send(table, message: str, intent: dict) -> list[dict]:  # noqa: F811
    """One typed turn, returning every socket event it produced."""

    table.turn += 1
    message_id = f'last-pass-{table.turn}'
    table.socket.emit(
        'send_message',
        {
            'session_id': table.ids['session_id'],
            'campaign_id': table.ids['campaign_id'],
            'player_id': table.ids['player_id'],
            'message': message,
            'client_message_id': message_id,
            'action_intent': {**intent, 'text': message, 'client_message_id': message_id},
        },
    )
    received = table.socket.get_received()
    assert [event['args'] for event in received if event['name'] == 'error'] == []
    table.last = table.snapshot()
    return received


def _threatened(turns: list[dict], hero_id: str) -> bool:
    """An enemy rolled against the hero, or laid damage or a condition on them, in these turns."""

    for turn in turns:
        pipeline = turn['metadata'].get('state_pipeline') or {}
        actions = ((pipeline.get('dmContextPacket') or {}).get('combatState') or {}).get('enemyResolvedActions') or []
        if any(action.get('targetId') == hero_id and (action.get('attackTotal') is not None or action.get('saveTotal') is not None) for action in actions):
            return True
        applied = [change for key in ('immediateAppliedChanges', 'combatAppliedChanges', 'postAppliedChanges') for change in pipeline.get(key) or []]
        if any(
            change.get('type') in THREAT_CHANGE_TYPES and change.get('sourceEnemyId') and hero_id in {change.get('actorId'), change.get('participantId')}
            for change in applied
        ):
            return True
    return False


def _beat_texts(turns: list[dict]) -> list[str]:
    return [beat['text'] for turn in turns for beat in turn['metadata'].get('storyBeats') or []]


def test_the_lantern_lure_wears_off_and_a_lone_hero_is_never_shut_out_of_the_fight(table, monkeypatch):  # noqa: F811
    # The lure's first save fails (enemies roll 1), and so does every repeat save at the end of the hero's turns.
    monkeypatch.setattr('aidm_server.combat.pipeline.deterministic_roller', lambda _seed: lambda _sides: 1)
    start = _last_turn_id(table)
    road = table.travel('loc_old_north_road', 'The Old North Road')
    assert road.encounter == 'enc_pale_lantern_detour'
    hero_id = _hero(road.snapshot)['id']
    window_start = start
    shut_out = 0
    for _round in range(10):
        snapshot = table.snapshot()
        if snapshot['combat']['status'] != 'active':
            break
        attack = table._hud_action('combat.attack.')
        target = next((target for target in (attack or {}).get('targets') or [] if target['available']), None)
        threatened = _threatened(_turns_since(table, window_start), hero_id)
        shut_out = 0 if target or threatened else shut_out + 1
        assert shut_out < 3, f"round {snapshot['combat']['round']}: the hero could neither act against an enemy nor was threatened"
        window_start = _last_turn_id(table)
        if target:
            table._hud('Arden Vale attacks.', attack['id'], target['id'])
        if table.snapshot()['combat']['status'] == 'active':
            table._hud('Arden Vale ends the turn.', 'combat.end_turn')
    turns = _turns_since(table, start)

    assert table.snapshot()['combat']['status'] == 'ended'
    # The lure lasts its two turns, then the hero shakes it off: each condition starts and ends exactly once.
    beats = _beat_texts(turns)
    assert (beats.count('You are charmed'), beats.count('No longer charmed')) == (1, 1)
    assert (beats.count('You are drawn aside'), beats.count('No longer drawn aside')) == (1, 1)
    assert sum(turn['narration'].count('You shake off Lantern-Lure.') for turn in turns) == 1
    ended = next(turn for turn in turns if 'You shake off Lantern-Lure.' in turn['narration'])
    assert 'No longer charmed' in _beat_texts([ended])


def test_a_repeated_save_that_succeeds_ends_the_lure_after_one_turn(table, monkeypatch):  # noqa: F811
    monkeypatch.setattr('aidm_server.combat.pipeline.deterministic_roller', lambda _seed: lambda _sides: 20)
    table.travel('loc_old_north_road', 'The Old North Road')
    hero = next(participant for participant in table.snapshot()['combat']['participants'] if participant['team'] == 'player')
    assert {'charmed', 'drawn_aside'} <= set(hero['conditions'])

    table._hud('Arden Vale ends the turn.', 'combat.end_turn')

    hero = next(participant for participant in table.snapshot()['combat']['participants'] if participant['team'] == 'player')
    assert not {'charmed', 'drawn_aside'} & set(hero['conditions'])
    assert 'You shake off Lantern-Lure.' in table.narration()


def _scribe_fight(table) -> dict:  # noqa: F811
    table.operator(
        [
            {'type': 'location.discover', 'locationId': 'loc_valley_last_road_stone'},
            {'type': 'scene.move_location', 'locationId': 'loc_valley_last_road_stone', 'name': 'Valley of the Last Road-Stone'},
        ]
    )
    assert table.act('valley_last_road_stone', 'inspect', 'The Last Road-Stone').encounter == 'enc_final_binding'
    return next(participant for participant in table.snapshot()['combat']['participants'] if participant.get('campaignPackEnemyId') == 'enemy_cinder_scribe')


def test_a_wound_penalty_is_named_on_the_roll_and_said_once(table, monkeypatch):  # noqa: F811
    _scribe_fight(table)
    hero = _hero(table.snapshot())
    table.operator([{'type': 'health.damage', 'actorId': hero['id'], 'amount': hero['health']['maxHp'] // 2}])
    # Every swing is a natural 1, so the Scribe stands for each of them.
    monkeypatch.setattr('aidm_server.player_rolls.secrets.randbelow', lambda _sides: 0)
    narrations, rolls = [], []
    for _swing in range(2):
        attack = table._hud_action('combat.attack.')
        target = next(target for target in attack['targets'] if target['available'])
        events = _send(table, 'Arden Vale attacks.', {'kind': 'combat', 'source': 'combat_hud', 'combat': {'action_id': attack['id'], 'target_id': target['id']}})
        rolls.extend(event['args'][0] for event in events if event['name'] == 'roll_resolved')
        narrations.append(table.narration())
        _send(table, 'Arden Vale ends the turn.', {'kind': 'combat', 'source': 'combat_hud', 'combat': {'action_id': 'combat.end_turn'}})

    assert [(roll['modifier'], roll['modifiers']) for roll in rolls] == [(2, [{'label': 'Wounded', 'value': -2}])] * 2
    # Closing in is told in the hero's own voice, then the attack names the penalty the first time only.
    assert narrations[0].startswith('DM: You close in on the Cinder Scribe. ')
    assert '— your wounds cost you 2.' in narrations[0]
    assert 'wounds' not in narrations[1]
    entries = table.client.get(f"/api/sessions/{table.ids['session_id']}/log?view=player&player_id={hero['playerId']}").get_json()['entries']
    persisted = [
        entry['metadata']['action_intent']['roll']
        for entry in entries
        if entry['entry_type'] == 'player' and ((entry.get('metadata') or {}).get('action_intent') or {}).get('roll')
    ]
    assert [roll['modifiers'] for roll in persisted] == [[{'label': 'Wounded', 'value': -2}]] * 2


def _make_flighty(table, scribe: dict, *, hp: int) -> None:  # noqa: F811
    """The Scribe as an enemy that would run below 90% HP, hurt to ``hp``, in round 4."""

    survival = {**scribe['behavior']['survivalRules'], 'fleeBelowHpPercent': 90}
    hit_points = {'current': hp, 'max': scribe['hp']['max'], 'temp': 0}
    table.operator(
        [
            {
                'type': 'combat.participant.update',
                'participantId': scribe['id'],
                'hp': hit_points,
                'participant': {**scribe, 'behavior': {**scribe['behavior'], 'survivalRules': survival}, 'hp': hit_points},
            },
            {'type': 'combat.update', 'round': 4},
        ]
    )


def _scribe(table) -> dict:  # noqa: F811
    return next(participant for participant in table.snapshot()['combat']['participants'] if participant.get('campaignPackEnemyId') == 'enemy_cinder_scribe')


def test_a_boss_stands_its_ground_until_a_quarter_of_its_hp(table):  # noqa: F811
    scribe = _scribe_fight(table)
    assert scribe['challengeTier'] == 'boss'
    _make_flighty(table, scribe, hp=scribe['hp']['max'] * 2 // 3)

    table._hud('Arden Vale ends the turn.', 'combat.end_turn')

    stood = [action['intentType'] for action in table.pipeline()['dmContextPacket']['combatState']['enemyResolvedActions']]
    assert stood and 'retreat' not in stood
    assert table.snapshot()['combat']['status'] == 'active'

    _make_flighty(table, _scribe(table), hp=scribe['hp']['max'] // 4)
    table._hud('Arden Vale ends the turn.', 'combat.end_turn')

    assert [action['intentType'] for action in table.pipeline()['dmContextPacket']['combatState']['enemyResolvedActions']] == ['retreat']
    assert table.snapshot()['combat']['flags']['endReason'] == 'enemies_fled'


def test_a_fate_needing_a_tool_the_hero_lacks_says_so_plainly(table):  # noqa: F811
    table.operator(
        [
            {'type': 'flag.set', 'flagKey': 'cinder_scribe_resolved', 'flagValue': True},
            {'type': 'location.discover', 'locationId': 'loc_valley_last_road_stone'},
            {'type': 'scene.move_location', 'locationId': 'loc_valley_last_road_stone', 'name': 'Valley of the Last Road-Stone'},
        ]
    )

    assert table.refused('valley_last_road_stone', 'break', 'The Last Road-Stone') == [
        'You have nothing that will break the Last Road-Stone.'
    ]


def test_losing_the_last_fight_is_not_an_ending():
    """The pack no longer lets a lost final fight claim the ward and finish the story."""

    import json
    from pathlib import Path

    pack = json.loads(Path('docs/examples/the_road_of_unremembered_kings_campaign.json').read_text())
    final = next(encounter for encounter in pack['encounters'] if encounter['id'] == 'enc_final_binding')
    fate_flags = {'ward_renewed', 'ward_broken', 'ward_hidden', 'ward_claimed', 'quest_road_complete'}

    for encounter in pack['encounters']:
        for outcome, payload in (encounter.get('outcomes') or {}).items():
            for consequence in payload.get('consequences') or []:
                assert consequence.get('flagKey') not in fate_flags, (encounter['id'], outcome)
    assert 'defeat' not in final['outcomes']
