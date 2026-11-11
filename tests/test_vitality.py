from __future__ import annotations

from copy import deepcopy

import pytest

import aidm_server.combat.pipeline as combat_pipeline
from aidm_server.class_capabilities import resolve_capability_use
from aidm_server.combat import vitality
from aidm_server.combat.defeat import party_defeat_recovery, party_wake
from aidm_server.combat.end_conditions import check_combat_end, combat_end_change
from aidm_server.combat.pipeline import death_saves_due
from aidm_server.combat.state import normalize_participant, participant_can_take_turn
from aidm_server.database import db
from aidm_server.game_state.application.applier import apply_state_changes, persist_state_to_database
from aidm_server.game_state.logging.state_log_builder import build_state_log
from aidm_server.game_state.leveling import sync_stats_for_level
from aidm_server.game_state.models import state_snapshot_for_session
from aidm_server.game_state.orchestration.combat_resolution import build_dm_combat_context, derive_trusted_damage_changes
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.models import Campaign, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from aidm_server.spell_effects import resolve_targeted_spell, spell_target_legality
from aidm_server.spellbook import AUTHORITATIVE_SPELL_EFFECTS
from tests.helpers import seed_world_campaign_player_session


def _pc(pid: str = 'player_1', *, hp: int = 12, maximum: int = 12, conditions=(), saves=None, temp: int = 0) -> dict:
    participant = {
        'id': pid,
        'name': pid.replace('_', ' ').title(),
        'team': 'player',
        'kind': 'player_character',
        'hp': {'current': hp, 'max': maximum, 'temp': temp},
        'armorClass': 12,
        'conditions': list(conditions),
        'position': {'rangeBand': 'near'},
        'isAlive': 'dead' not in conditions,
        'isConscious': hp > 0,
        'stats': {'wisdom': 14, 'charisma': 14},
        'level': 1,
    }
    if saves is not None:
        participant['deathSaves'] = dict(saves)
    return participant


def _actor(pid: str, participant: dict, **extra) -> dict:
    return {
        'id': pid,
        'name': participant['name'],
        'playerId': int(pid.split('_')[1]),
        'level': 1,
        'health': {
            'currentHp': participant['hp']['current'],
            'maxHp': participant['hp']['max'],
            'tempHp': 0,
            'conditions': list(participant['conditions']),
            **({'deathSaves': participant['deathSaves']} if 'deathSaves' in participant else {}),
        },
        **extra,
    }


def _state(*pcs: dict, active: bool = True, flags: dict | None = None) -> dict:
    enemy = {'id': 'enemy_1', 'name': 'Ogre', 'team': 'enemy', 'kind': 'creature', 'hp': {'current': 30, 'max': 30}}
    order = [*[pc['id'] for pc in pcs], 'enemy_1']
    return {
        'playerCharacters': [_actor(pc['id'], pc) for pc in pcs],
        'combat': {
            'status': 'active' if active else 'ended',
            'round': 1,
            'turnIndex': 0,
            'participants': [*deepcopy(list(pcs)), enemy],
            'initiative': [
                {'participantId': pid, 'roll': 20 - index, 'modifier': 0, 'total': 20 - index, 'order': index}
                for index, pid in enumerate(order)
            ],
            'flags': {'activeActorId': pcs[0]['id'], **(flags or {})},
        },
    }


def _participant(state: dict, pid: str) -> dict:
    return next(item for item in state['combat']['participants'] if item['id'] == pid)


def _apply(state: dict, changes: list[dict]) -> dict:
    validation = validate_state_changes(state=state, changes=changes)
    assert not validation['rejected'], validation['rejected']
    return apply_state_changes(state, validated_changes_for_application(validation))['nextState']


def _damage(pid: str, amount: int, change_id: str = 'dmg', **extra) -> dict:
    return {'id': change_id, 'type': 'health.damage', 'actorId': pid, 'amount': amount, **extra}


# --- A1-A3: going down, massive damage, damage at 0 HP ---------------------------


def test_player_character_at_zero_hp_is_dying_not_dead():
    state = _apply(_state(_pc('player_1', hp=5)), [_damage('player_1', 5)])
    participant = _participant(state, 'player_1')
    assert set(participant['conditions']) == {'unconscious', 'dying'}
    assert participant['isAlive'] is True
    assert participant['isConscious'] is False
    assert participant['deathSaves'] == {'successes': 0, 'failures': 0}
    health = state['playerCharacters'][0]['health']
    assert set(health['conditions']) == {'unconscious', 'dying'}
    assert health['deathSaves'] == {'successes': 0, 'failures': 0}
    assert participant_can_take_turn(participant) is False


OUT = {'unconscious', 'stable', 'out_of_fight'}


def _is_out(record: dict) -> bool:
    return set(record['conditions']) == OUT and record['isAlive'] is True and record['deathSaves'] == {'successes': 0, 'failures': 0}


def test_normalize_participant_reads_an_old_saves_dead_player_as_out_of_the_fight():
    downed = normalize_participant({**_pc('player_1', hp=0, conditions=['unconscious', 'dying']), 'isAlive': False})
    dead = normalize_participant(_pc('player_1', hp=0, conditions=['dead'], saves={'successes': 0, 'failures': 3}))
    assert downed['isAlive'] is True
    assert _is_out(dead)


def test_massive_damage_puts_a_hero_out_of_the_fight():
    state = _state(_pc('player_1', hp=5, maximum=12))
    validation = validate_state_changes(state=state, changes=[_damage('player_1', 17)])
    result = apply_state_changes(state, validated_changes_for_application(validation))
    assert _is_out(_participant(result['nextState'], 'player_1'))
    assert set(result['nextState']['playerCharacters'][0]['health']['conditions']) == OUT
    assert result['appliedChanges'][0]['vitality'] == 'out_of_fight'


@pytest.mark.parametrize(('critical', 'failures'), [(False, 1), (True, 2)])
def test_damage_at_zero_hp_adds_death_save_failures(critical, failures):
    downed = _pc('player_1', hp=0, conditions=['unconscious', 'dying'], saves={'successes': 1, 'failures': 0})
    state = _apply(_state(downed), [_damage('player_1', 3, critical=critical)])
    assert _participant(state, 'player_1')['deathSaves'] == {'successes': 1, 'failures': failures}


def test_third_failure_from_damage_puts_a_hero_out_and_stable_characters_resume_dying():
    near_death = _pc('player_1', hp=0, conditions=['unconscious', 'dying'], saves={'successes': 0, 'failures': 2})
    state = _apply(_state(near_death), [_damage('player_1', 1)])
    assert _is_out(_participant(state, 'player_1'))

    stable = _pc('player_2', hp=0, conditions=['unconscious', 'stable'], saves={'successes': 0, 'failures': 0})
    state = _apply(_state(_pc('player_1'), stable), [_damage('player_2', 1)])
    participant = _participant(state, 'player_2')
    assert set(participant['conditions']) == {'unconscious', 'dying'}
    assert participant['deathSaves'] == {'successes': 0, 'failures': 1}


# --- A4: death saving throws ------------------------------------------------------


@pytest.mark.parametrize(
    ('roll', 'result', 'tally', 'outcome'),
    [
        (10, 'success', {'successes': 1, 'failures': 0}, 'dying'),
        (9, 'failure', {'successes': 0, 'failures': 1}, 'dying'),
        (1, 'critical_failure', {'successes': 0, 'failures': 2}, 'dying'),
        (20, 'critical_success', {'successes': 0, 'failures': 0}, 'revived'),
    ],
)
def test_death_save_outcomes(roll, result, tally, outcome):
    record = _pc(hp=0, conditions=['unconscious', 'dying'], saves={'successes': 0, 'failures': 0})
    save = vitality.apply_death_save(record, roll)
    assert (save['result'], save['outcome']) == (result, outcome)
    assert vitality.death_saves(record) == tally
    if roll == 20:
        assert record['hp']['current'] == 1
        assert record['conditions'] == []
        assert record['isConscious'] is True


def test_three_successes_stabilize_and_three_failures_put_a_hero_out_of_the_fight():
    record = _pc(hp=0, conditions=['unconscious', 'dying'], saves={'successes': 2, 'failures': 1})
    assert vitality.apply_death_save(record, 15)['outcome'] == 'stable'
    assert set(record['conditions']) == {'unconscious', 'stable'}
    assert record['deathSaves'] == {'successes': 0, 'failures': 0}
    assert vitality.apply_death_save(record, 3)['applied'] is False

    record = _pc(hp=0, conditions=['unconscious', 'dying'], saves={'successes': 2, 'failures': 2})
    assert vitality.apply_death_save(record, 4)['outcome'] == 'out_of_fight'
    assert _is_out(record)
    assert vitality.apply_death_save(record, 2)['applied'] is False


def test_ending_turn_rolls_and_records_the_death_save_of_a_dying_pc_it_passes(monkeypatch):
    monkeypatch.setattr(combat_pipeline, 'death_save_roll', lambda turn_id, participant_id, round_number: 1)
    dying = _pc('player_2', hp=0, conditions=['unconscious', 'dying'], saves={'successes': 0, 'failures': 0})
    state = _state(_pc('player_1'), dying)
    context = build_dm_combat_context(
        state=state,
        combat_context={'enemyRequiredActions': []},
        pending_rolls=[],
        resolved_player_roll=False,
        turn_id=40,
    )
    assert context['deathSaves'] == [
        {
            'participantId': 'player_2',
            'participantName': 'Player 2',
            'round': 1,
            'roll': 1,
            'result': 'critical_failure',
            'successes': 0,
            'failures': 2,
            'outcome': 'dying',
            'authoritative': True,
        }
    ]
    changes = derive_trusted_damage_changes(
        state=state,
        dm_context_packet={'combatState': context},
        actor_id='player_1',
        turn_id=40,
        already_applied_changes=[],
    ).death_saves
    assert [change['type'] for change in changes] == ['combat.death_save']
    assert changes[0]['visible'] is True
    # The validator replays the engine's own roll, so the patched roll is rejected.
    validation = validate_state_changes(state=state, changes=changes)
    assert validation['rejected'][0]['reason'] == 'Death save does not match the engine roll.'

    monkeypatch.undo()
    context = build_dm_combat_context(
        state=state, combat_context={'enemyRequiredActions': []}, pending_rolls=[], resolved_player_roll=False, turn_id=40,
    )
    changes = derive_trusted_damage_changes(
        state=state, dm_context_packet={'combatState': context}, actor_id='player_1', turn_id=40, already_applied_changes=[],
    ).death_saves
    assert changes[0]['roll'] == vitality.death_save_roll(40, 'player_2', 1)
    expected = deepcopy(dying)
    vitality.apply_death_save(expected, changes[0]['roll'])
    applied = _apply(state, changes)
    participant = _participant(applied, 'player_2')
    assert vitality.vitality_snapshot(participant) == vitality.vitality_snapshot(expected)
    assert vitality.vitality_snapshot(applied['playerCharacters'][1]) == vitality.vitality_snapshot(expected)


def test_death_save_reads_as_its_reason_in_the_turn_state_log():
    dying = _pc('player_2', hp=0, conditions=['unconscious', 'dying'], saves={'successes': 0, 'failures': 0})
    state = _state(_pc('player_1'), dying)
    context = build_dm_combat_context(
        state=state, combat_context={'enemyRequiredActions': []}, pending_rolls=[], resolved_player_roll=False, turn_id=41,
    )
    changes = derive_trusted_damage_changes(
        state=state, dm_context_packet={'combatState': context}, actor_id='player_1', turn_id=41, already_applied_changes=[],
    ).death_saves
    validation = validate_state_changes(state=state, changes=changes)

    state_log = build_state_log(turn_id=41, post_validation=validation)

    assert changes[0]['reason'].startswith('Death saving throw: Player 2 rolled ')
    assert [line['message'] for line in state_log['lines']] == [changes[0]['reason']]
    assert state_log['lines'][0]['visibleToPlayer'] is True


def test_stable_characters_make_no_death_saves():
    stable = _pc('player_2', hp=0, conditions=['unconscious', 'stable'])
    assert death_saves_due(_state(_pc('player_1'), stable)['combat'], turn_id=1) == []


def test_forged_death_save_is_rejected():
    dying = _pc('player_2', hp=0, conditions=['unconscious', 'dying'])
    forged = {
        'id': vitality.death_save_change_id(5, 'player_2', 1),
        'turnId': 5,
        'type': 'combat.death_save',
        'participantId': 'player_2',
        'round': 1,
        'roll': 20,
        'source': 'post_dm',
    }
    validation = validate_state_changes(state=_state(_pc('player_1'), dying), changes=[forged])
    assert validation['rejected'][0]['reason'] == 'Death saving throws are rolled only by the rules engine.'


# --- A5 / D5: healing a downed character; nothing raises a hero out of the fight ----


def _spell(name: str) -> dict:
    return {'id': f"spell_{name.replace(' ', '_')}", 'name': name.title(), **deepcopy(AUTHORITATIVE_SPELL_EFFECTS[name])}


@pytest.mark.parametrize(('name', 'band'), [('healing word', 'far'), ('cure wounds', 'melee')])
def test_healing_spells_revive_a_dying_player_character(name, band):
    downed = _pc('player_2', hp=0, conditions=['unconscious', 'dying'], saves={'successes': 1, 'failures': 2})
    downed['position'] = {'rangeBand': band}
    combat = _state(_pc('player_1'), downed)['combat']
    result = resolve_targeted_spell(
        combat, caster_id='player_1', spell=_spell(name), target_ids=['player_2'], cast_id=f'cast_{name}', roller=lambda _sides: 3,
    )
    healed = next(item for item in result['combat']['participants'] if item['id'] == 'player_2')
    assert healed['hp']['current'] == 3
    assert healed['conditions'] == []
    assert healed['deathSaves'] == {'successes': 0, 'failures': 0}
    assert healed['isConscious'] is True and participant_can_take_turn(healed)


def test_out_of_the_fight_player_characters_and_downed_damage_targets_are_rejected():
    out = _pc('player_2', hp=0, conditions=sorted(OUT))
    combat = _state(_pc('player_1'), out)['combat']
    for name in ('healing word', 'spare the dying'):
        legality = spell_target_legality(combat, caster_id='player_1', spell=_spell(name), target_ids=['player_2'])
        assert (legality['code'], legality['reason']) == ('spell_target_out_of_fight', 'They are out of this fight.')
    downed_enemy = deepcopy(combat)
    downed_enemy['participants'][2]['hp']['current'] = 0
    fire_bolt = {'id': 'spell_fire_bolt', 'name': 'Fire Bolt', **deepcopy(AUTHORITATIVE_SPELL_EFFECTS['fire bolt'])}
    fire_bolt['delivery'] = {**fire_bolt['delivery'], 'attackBonus': 5}
    assert spell_target_legality(downed_enemy, caster_id='player_1', spell=fire_bolt, target_ids=['enemy_1'])['code'] == 'spell_target_defeated'


def _paladin(pid: str = 'player_1') -> dict:
    return {
        'id': pid,
        'name': 'Aldric',
        'playerId': 1,
        'class': 'Paladin',
        'level': 1,
        'team': 'player',
        'health': {'currentHp': 12, 'maxHp': 12, 'conditions': []},
    }


def test_lay_on_hands_restores_a_downed_ally_but_not_one_out_of_the_fight():
    downed = {'id': 'player_2', 'team': 'player', 'health': {'currentHp': 0, 'maxHp': 10, 'conditions': ['unconscious', 'stable']}}
    assert resolve_capability_use(actor=_paladin(), capability_id='lay_on_hands', target=downed, requested_amount=4)['amount'] == 4
    out = {'id': 'player_2', 'team': 'player', 'health': {'currentHp': 0, 'maxHp': 10, 'conditions': sorted(OUT)}}
    assert resolve_capability_use(actor=_paladin(), capability_id='lay_on_hands', target=out, requested_amount=4) == {
        'ok': False,
        'reason': 'They are out of this fight.',
    }

    state = {
        'playerCharacters': [
            _paladin(),
            {'id': 'player_2', 'name': 'Mira', 'playerId': 2, 'health': {'currentHp': 0, 'maxHp': 10, 'conditions': ['unconscious', 'stable']}},
        ],
    }
    declared = validate_declared_actions(
        state=state,
        declared_actions=[{'id': 'act_1', 'type': 'class_feature.use', 'actorId': 'player_1', 'capabilityId': 'lay_on_hands', 'targetId': 'player_2', 'amount': 4}],
        current_turn=3,
        expected_actor_id='player_1',
    )
    healed = _apply(state, declared['immediateChanges'])['playerCharacters'][1]['health']
    assert healed['currentHp'] == 4
    assert healed['conditions'] == []


def test_narrated_heal_wakes_a_dying_character_and_is_refused_for_one_out_of_the_fight():
    dying = _pc('player_2', hp=0, conditions=['unconscious', 'dying'], saves={'successes': 0, 'failures': 1})
    state = _apply(_state(_pc('player_1'), dying), [{'id': 'heal', 'type': 'health.heal', 'actorId': 'player_2', 'amount': 4}])
    participant = _participant(state, 'player_2')
    assert participant['hp']['current'] == 4
    assert participant['conditions'] == []
    assert participant['isConscious'] is True
    assert state['playerCharacters'][1]['health']['conditions'] == []

    out = _pc('player_2', hp=0, conditions=sorted(OUT))
    validation = validate_state_changes(
        state=_state(_pc('player_1'), out),
        changes=[{'id': 'heal', 'type': 'health.heal', 'actorId': 'player_2', 'amount': 4}],
    )
    assert validation['rejected'][0]['reason'] == 'They are out of this fight.'
    # Stabilising, an explicit HP update and damage change nothing either.
    record = deepcopy(out)
    assert vitality.heal(record, 5) == 0 and vitality.stabilize(record) is False
    vitality.set_hit_points(record, 6, in_combat=True)
    assert vitality.take_damage(record, 3)['amount'] == 0
    assert record['hp']['current'] == 0 and _is_out({**record, 'deathSaves': vitality.death_saves(record)})


def test_revive_wakes_a_hero_out_of_the_fight_with_one_hit_point():
    record = _pc(hp=0, conditions=sorted(OUT), saves={'successes': 0, 'failures': 0})
    vitality.revive(record)
    assert (record['hp']['current'], record['conditions'], record['isAlive'], record['isConscious']) == (1, [], True, True)
    assert record['deathSaves'] == {'successes': 0, 'failures': 0}


# --- A7: everyone down ------------------------------------------------------------


def _all_down_state(flags: dict | None = None) -> dict:
    dying = _pc('player_1', hp=0, conditions=['unconscious', 'dying'], saves={'successes': 1, 'failures': 1})
    out = _pc('player_2', hp=0, conditions=sorted(OUT))
    return _state(dying, out, flags=flags)


def test_all_player_characters_down_ends_combat_and_stabilizes_the_dying():
    state = _all_down_state()
    reason = check_combat_end(state['combat'])
    assert reason == 'objective_failed'
    ended = _apply(state, [combat_end_change(50, reason)])
    assert ended['combat']['status'] == 'ended'
    assert set(_participant(ended, 'player_1')['conditions']) == {'unconscious', 'stable'}
    assert set(ended['playerCharacters'][0]['health']['conditions']) == {'unconscious', 'stable'}
    assert set(_participant(ended, 'player_2')['conditions']) == OUT


def test_a_lethal_defeat_stabilizes_the_dying_too():
    # lethalDefeat only routes the story; the beaten party then wakes like any other.
    ended = _apply(_all_down_state({'lethalDefeat': True}), [combat_end_change(51, 'objective_failed')])
    assert set(_participant(ended, 'player_1')['conditions']) == {'unconscious', 'stable'}


# --- A8, A9, A10 -------------------------------------------------------------------


def test_out_of_combat_zero_hp_is_stable_and_massive_damage_puts_a_hero_out():
    state = {'playerCharacters': [{'id': 'player_1', 'name': 'Kael', 'playerId': 1, 'health': {'currentHp': 4, 'maxHp': 12, 'conditions': []}}]}
    stable = _apply(state, [_damage('player_1', 6)])['playerCharacters'][0]['health']
    assert set(stable['conditions']) == {'unconscious', 'stable'}
    out = _apply(state, [_damage('player_1', 16, 'dmg_massive')])['playerCharacters'][0]['health']
    assert set(out['conditions']) == OUT


def _rest_state(conditions: list[str], hp: int = 0) -> dict:
    return {'playerCharacters': [{'id': 'player_1', 'name': 'Kael', 'playerId': 1, 'class': 'Fighter', 'level': 1, 'health': {'currentHp': hp, 'maxHp': 12, 'conditions': conditions}}]}


def _rest(state: dict, rest_type: str) -> dict:
    # No hit dice are spent, so a short rest shows only the wake-up to 1 HP.
    return validate_declared_actions(
        state=state,
        declared_actions=[{'id': 'act_rest', 'type': 'rest.complete', 'actorId': 'player_1', 'restType': rest_type, 'hitDice': 0}],
        current_turn=8,
        expected_actor_id='player_1',
    )


@pytest.mark.parametrize(('rest_type', 'hp'), [('short_rest', 1), ('long_rest', 12)])
def test_stable_character_can_rest_and_wakes(rest_type, hp):
    state = _rest_state(['unconscious', 'stable'])
    declared = _rest(state, rest_type)
    assert declared['validatedActions'][0]['status'] == 'valid'
    health = _apply(state, declared['immediateChanges'])['playerCharacters'][0]['health']
    assert health['currentHp'] == hp
    assert health['conditions'] == []


@pytest.mark.parametrize('conditions', [['unconscious', 'dying'], ['dead']])
def test_dying_or_dead_character_cannot_rest(conditions):
    declared = _rest(_rest_state(conditions), 'long_rest')
    assert declared['validatedActions'][0]['reason'] == 'A dead or dying character cannot complete a rest.'


def test_level_up_at_zero_hp_raises_maximum_only():
    stats = {'ability_scores': {'constitution': 14}, 'hit_die': 10, 'max_hp': 12, 'current_hp': 0}
    sync_stats_for_level(stats, 2)
    assert stats['max_hp'] > 12
    assert stats['current_hp'] == 0


# --- A11: persistence -------------------------------------------------------------


def test_vitality_round_trips_through_the_player_row(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        player = db.session.get(Player, ids['player_id'])
        player.stats = safe_json_dumps({'current_hp': 6, 'max_hp': 20, 'conditions': ['poisoned']}, {})
        db.session.commit()
        state = state_snapshot_for_session(session_obj=session_obj, campaign=campaign, players=[player])
        actor_id = state['playerCharacters'][0]['id']
        state = _apply(state, [_damage(actor_id, 6)])
        persist_state_to_database(session_obj=session_obj, state=state, players_by_id={player.player_id: player})
        db.session.commit()

        stats = safe_json_loads(db.session.get(Player, ids['player_id']).stats, {})
        rebuilt = state_snapshot_for_session(session_obj=session_obj, campaign=campaign, players=[db.session.get(Player, ids['player_id'])])
    assert stats['conditions'] == ['poisoned', 'unconscious', 'stable']
    assert stats['death_saves'] == {'successes': 0, 'failures': 0}
    health = rebuilt['playerCharacters'][0]['health']
    assert health['currentHp'] == 0
    assert health['conditions'] == ['poisoned', 'unconscious', 'stable']
    assert health['deathSaves'] == {'successes': 0, 'failures': 0}


# --- A12: enemies and NPC allies keep 0 HP = defeated ------------------------------


def test_enemies_and_npc_allies_are_simply_defeated_at_zero_hp():
    state = _state(_pc('player_1'))
    state['combat']['participants'].append({'id': 'ally_guard', 'name': 'Guard', 'team': 'ally', 'kind': 'npc', 'hp': {'current': 3, 'max': 9}})
    state = _apply(
        state,
        [
            {'id': 'hit_enemy', 'type': 'combat.participant.update', 'participantId': 'enemy_1', 'hp': {'current': 0, 'max': 30}},
            {'id': 'hit_ally', 'type': 'combat.participant.update', 'participantId': 'ally_guard', 'hp': {'current': 0, 'max': 9}},
        ],
    )
    for pid in ('enemy_1', 'ally_guard'):
        participant = _participant(state, pid)
        assert participant['isAlive'] is False
        assert 'dying' not in participant['conditions']


# --- A13: the table sees a downed character's death saves --------------------------


@pytest.mark.parametrize('viewer_player_ids', [{1}, None])
def test_non_admin_viewers_see_another_player_characters_death_saves(viewer_player_ids):
    healthy = _pc('player_1')
    dying = _pc('player_2', hp=0, conditions=['unconscious', 'dying'], saves={'successes': 1, 'failures': 2})
    state = _state(healthy, dying)
    for participant in state['combat']['participants'][:2]:
        participant['playerId'] = int(participant['id'].split('_')[1])

    projected = filter_session_snapshot_for_player(state, private_player_ids=viewer_player_ids)

    downed = next(item for item in projected['combat']['participants'] if item['id'] == 'player_2')
    assert downed['deathSaves'] == {'successes': 1, 'failures': 2}
    assert set(downed['conditions']) == {'unconscious', 'dying'}
    standing = next(item for item in projected['combat']['participants'] if item['id'] == 'player_1')
    assert 'deathSaves' not in standing


def test_victory_also_leaves_a_dying_character_stable():
    dying = _pc('player_2', hp=0, conditions=['unconscious', 'dying'])
    state = _state(_pc('player_1'), dying)
    state['combat']['participants'][2]['hp']['current'] = 0
    reason = check_combat_end(state['combat'])
    assert reason == 'all_enemies_defeated'
    ended = _apply(state, [combat_end_change(52, reason)])
    assert set(_participant(ended, 'player_2')['conditions']) == {'unconscious', 'stable'}


# --- W6: beaten, never killed: the fight's end wakes the downed -------------------


def _won_fight(*pcs: dict) -> dict:
    state = _state(*pcs, active=False, flags={'endReason': 'all_enemies_defeated'})
    state['combat']['participants'][-1]['hp']['current'] = 0
    state['clock'] = {'day': 1, 'phase': 'evening', 'shortRestsSinceLong': 0}
    return state


def test_a_fight_won_with_a_hero_standing_wakes_every_downed_hero_once_with_no_time_passing():
    out = _pc('player_2', hp=0, conditions=[*sorted(OUT), 'prone'])
    dying = _pc('player_3', hp=0, conditions=['unconscious', 'dying'], saves={'successes': 1, 'failures': 2})
    state = _won_fight(_pc('player_1'), out, dying)
    clock = deepcopy(state['clock'])

    change = party_wake(state, turn_id=60)

    assert (change['type'], change['wokenActorIds']) == ('party.come_round', ['player_2', 'player_3'])
    assert 'clock' not in change and state['clock'] == clock
    for pid in ('player_2', 'player_3'):
        participant = _participant(state, pid)
        actor = next(hero for hero in state['playerCharacters'] if hero['id'] == pid)
        assert (participant['hp']['current'], participant['conditions'], participant['deathSaves']) == (1, [], {'successes': 0, 'failures': 0})
        assert (actor['health']['currentHp'], actor['health']['conditions']) == (1, [])
    assert state['stateChangeLedger'][-1]['type'] == 'party.come_round'
    # One wake per fight end: a hero who goes down after it waits for a rest, a heal or the whole party falling.
    assert party_wake(state, turn_id=61) is None
    state['playerCharacters'][1]['health'].update(currentHp=0, conditions=['unconscious', 'stable'])
    assert party_wake(state, turn_id=62) is None


def test_with_every_hero_down_the_out_of_the_fight_hero_wakes_with_the_beaten_party():
    state = _all_down_state()
    ended = _apply(state, [combat_end_change(53, 'objective_failed')])

    change = party_defeat_recovery(ended, turn_id=53)

    assert change['wokenActorIds'] == ['player_1', 'player_2']
    for pid in ('player_1', 'player_2'):
        assert (_participant(ended, pid)['hp']['current'], _participant(ended, pid)['conditions']) == (1, [])
    assert party_wake(ended, turn_id=54) is None


def test_outside_a_fight_a_hero_left_out_of_it_comes_round_while_a_companion_stands():
    state = _state(_pc('player_1'), _pc('player_2', hp=0, conditions=['dead']), active=False)
    state['combat'] = {'status': 'none', 'participants': [], 'flags': {}}
    state['playerCharacters'][1]['health'] = {'currentHp': 0, 'maxHp': 12, 'conditions': sorted(OUT)}

    change = party_wake(state, turn_id=70)

    assert change['type'] == 'party.come_round'
    assert state['playerCharacters'][1]['health']['currentHp'] == 1
