from __future__ import annotations

from copy import deepcopy

import pytest

import aidm_server.blueprints.creatures as creatures_blueprint
import aidm_server.combat.pipeline as combat_pipeline
from aidm_server.combat import modifiers, vitality
from aidm_server.combat.legal_actions import (
    legal_combat_actions_for_player,
    register_action_provider,
    register_action_resolver,
    resolve_combat_legal_action,
    unregister_action_provider,
    unregister_action_resolver,
)
from aidm_server.combat.pipeline import apply_turn_boundaries, combat_turn_advance_change
from aidm_server.combat.state import combat_ability_is_available, normalize_combat_state
from aidm_server.database import db
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.models import state_snapshot_for_session
from aidm_server.game_state.orchestration.combat_resolution import (
    build_dm_combat_context,
    resolve_authoritative_player_attack,
)
from aidm_server.game_state.orchestration.turn_pipeline import _gate_pre_dm_combat_spells
from aidm_server.models import Campaign, DmTurn, Player, Session, safe_json_dumps
from aidm_server.player_rolls import canonical_roll_sentence, resolve_authoritative_player_roll
from aidm_server.roll_visibility import public_roll_payload
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from aidm_server.spell_effects import resolve_targeted_spell, spell_target_legality
from tests.helpers import seed_world_campaign_player_session


def _p(pid: str, *, team: str = 'enemy', conditions=(), effects=(), band: str = 'near', **extra) -> dict:
    return {
        'id': pid,
        'name': pid.replace('_', ' ').title(),
        'team': team,
        'kind': 'player_character' if team == 'player' else 'creature',
        'hp': {'current': 10, 'max': 10, 'temp': 0},
        'conditions': list(conditions),
        'activeEffects': list(effects),
        'position': {'rangeBand': band},
        **extra,
    }


def _effect(effect_id: str, **mods) -> dict:
    consume_on = mods.pop('consumeOn', None)
    return {
        'id': effect_id,
        'sourceId': f'src_{effect_id}',
        'sourceName': effect_id.title(),
        'casterId': mods.pop('casterId', 'caster'),
        'modifiers': mods,
        'duration': None,
        'concentration': False,
        'consumeOn': consume_on,
    }


def _attack(attacker: dict, target: dict | None, *, ranged: bool = False, combat: dict | None = None) -> dict:
    return modifiers.attack_context(attacker, target, kind='weapon_attack', ranged=ranged, combat=combat)


# --- one test per condition row -------------------------------------------------


def test_blinded_disadvantages_own_attacks_and_advantages_attacks_against_it():
    assert _attack(_p('a', conditions=['blinded']), _p('t'))['mode'] == 'disadvantage'
    result = _attack(_p('a'), _p('t', conditions=['blinded']))
    assert result['mode'] == 'advantage'
    assert 'Target is blinded' in result['reasons']


def test_charmed_creature_cannot_target_its_charmer():
    charmed = _p('a', team='player', conditions=['charmed'], charmedBy=['enemy_witch'])
    witch = _p('enemy_witch')
    assert modifiers.attack_block_reason(charmed, witch) == 'A is charmed by Enemy Witch.'
    assert modifiers.attack_block_reason(charmed, _p('enemy_ogre')) == ''


def test_frightened_disadvantages_attacks_and_checks_only_while_source_is_present():
    scared = _p('a', conditions=['frightened'], frightenedBy=['enemy_dragon'])
    dragon = _p('enemy_dragon')
    combat = {'participants': [scared, dragon]}
    assert _attack(scared, _p('t'), combat=combat)['mode'] == 'disadvantage'
    assert modifiers.check_context(scared, ability='strength', combat=combat)['mode'] == 'disadvantage'
    dragon['conditions'] = ['dead']
    dragon['isAlive'] = False
    assert _attack(scared, _p('t'), combat=combat)['mode'] == 'normal'
    assert modifiers.check_context(scared, ability='strength', combat=combat)['mode'] == 'normal'


def test_grappled_and_slowed_set_speed_to_zero():
    assert modifiers.speed_is_zero(_p('a', conditions=['grappled']))
    assert modifiers.speed_is_zero(_p('a', conditions=['slowed']))
    assert not modifiers.speed_is_zero(_p('a'))


def test_incapacitated_cannot_act():
    assert modifiers.can_act(_p('a'))
    assert not modifiers.can_act(_p('a', conditions=['incapacitated']))


def test_invisible_advantages_own_attacks_and_disadvantages_attacks_against_it():
    assert _attack(_p('a', conditions=['invisible']), _p('t'))['mode'] == 'advantage'
    assert _attack(_p('a'), _p('t', conditions=['invisible']))['mode'] == 'disadvantage'


def test_paralyzed_cannot_act_fails_str_dex_saves_and_melee_hits_are_critical():
    target = _p('t', conditions=['paralyzed'])
    assert not modifiers.can_act(target)
    assert modifiers.speed_is_zero(target)
    assert modifiers.save_context(target, 'dex')['autoFail'] is True
    assert modifiers.save_context(target, 'wisdom')['autoFail'] is False
    melee = _attack(_p('a'), target)
    assert melee['mode'] == 'advantage'
    assert melee['autoCrit'] is True
    assert _attack(_p('a'), target, ranged=True)['autoCrit'] is False


def test_petrified_cannot_act_fails_saves_is_attacked_with_advantage_and_resists_all_damage():
    target = _p('t', conditions=['petrified'])
    assert not modifiers.can_act(target)
    assert modifiers.save_context(target, 'strength')['autoFail'] is True
    assert _attack(_p('a'), target)['mode'] == 'advantage'
    assert modifiers.damage_multiplier(target, 'fire') == 0.5


def test_poisoned_disadvantages_attacks_and_checks():
    poisoned = _p('a', conditions=['poisoned'])
    assert _attack(poisoned, _p('t'))['mode'] == 'disadvantage'
    assert modifiers.check_context(poisoned, ability='dexterity')['mode'] == 'disadvantage'


def test_prone_disadvantages_own_attacks_and_depends_on_the_attackers_band():
    assert _attack(_p('a', conditions=['prone']), _p('t'))['mode'] == 'disadvantage'
    prone = _p('t', conditions=['prone'])
    assert _attack(_p('a'), prone)['mode'] == 'advantage'
    assert _attack(_p('a', band='far'), prone, ranged=True)['mode'] == 'disadvantage'
    assert _attack(_p('a', band='melee'), prone, ranged=True)['mode'] == 'advantage'


def test_prone_creature_that_can_act_stands_at_turn_start_spending_movement():
    combat = {
        'round': 1,
        'participants': [_p('a', team='player', conditions=['prone']), _p('b', team='player', conditions=['prone', 'stunned'])],
        'flags': {'turnEconomy': {'actorId': 'a', 'movementRemaining': 1}},
    }
    events = apply_turn_boundaries(
        combat,
        [{'actorId': 'a', 'phase': 'start', 'round': 1}, {'actorId': 'b', 'phase': 'start', 'round': 1}],
        turn_id=5,
    )
    assert combat['participants'][0]['conditions'] == []
    assert combat['flags']['turnEconomy']['movementRemaining'] == 0
    assert events[0]['stoodUp'] is True
    assert combat['participants'][1]['conditions'] == ['prone', 'stunned']


def test_ongoing_damage_uses_defenses_temporary_hp_and_player_vitality(monkeypatch):
    monkeypatch.setattr(combat_pipeline, 'deterministic_roller', lambda _seed: lambda _sides: 4)
    effect = _effect('burn', damagePerTurn={'dice': '1d4', 'type': 'fire'})
    player = _p('player_1', team='player', effects=[effect], resistances=['fire'],
                hp={'current': 1, 'max': 10, 'temp': 1})
    combat = {'participants': [player], 'flags': {}}
    events = apply_turn_boundaries(combat, [{'actorId': 'player_1', 'phase': 'start', 'round': 2}], turn_id=7)
    result = events[0]['ongoingDamage'][0]
    assert (result['rolls'], result['defense'], result['damage']) == ([4], 'resistant', 2)
    assert (result['tempHpDamage'], result['hpDamage'], result['vitality']) == (1, 1, 'dying')
    assert player['hp']['current'] == 0
    assert 'dying' in player['conditions'] and player['isAlive']
    apply_turn_boundaries(combat, [{'actorId': 'player_1', 'phase': 'start', 'round': 3}], turn_id=8)
    assert player['deathSaves']['failures'] == 1


def test_restrained_stops_movement_and_shifts_attacks_and_dex_saves():
    restrained = _p('t', conditions=['restrained'])
    assert modifiers.speed_is_zero(restrained)
    assert _attack(restrained, _p('x'))['mode'] == 'disadvantage'
    assert _attack(_p('a'), restrained)['mode'] == 'advantage'
    assert modifiers.save_context(restrained, 'dexterity')['mode'] == 'disadvantage'
    assert modifiers.save_context(restrained, 'wisdom')['mode'] == 'normal'


def test_stunned_cannot_act_fails_saves_and_is_attacked_with_advantage():
    stunned = _p('t', conditions=['stunned'])
    assert not modifiers.can_act(stunned)
    assert modifiers.save_context(stunned, 'strength')['autoFail'] is True
    assert _attack(_p('a'), stunned)['mode'] == 'advantage'
    assert _attack(_p('a'), stunned)['autoCrit'] is False


def test_unconscious_counts_as_prone_fails_saves_and_melee_hits_are_critical():
    target = _p('t', conditions=['unconscious'])
    assert not modifiers.can_act(target)
    assert modifiers.save_context(target, 'dexterity')['autoFail'] is True
    melee = _attack(_p('a'), target)
    assert (melee['mode'], melee['autoCrit']) == ('advantage', True)
    ranged = _attack(_p('a', band='far'), target, ranged=True)
    assert (ranged['mode'], ranged['autoCrit']) == ('normal', False)


def test_dodging_defends_and_ends_at_the_start_of_its_turn():
    dodger = _p('t', conditions=['dodging'])
    assert _attack(_p('a'), dodger)['mode'] == 'disadvantage'
    assert modifiers.save_context(dodger, 'dexterity')['mode'] == 'advantage'
    combat = {'round': 1, 'participants': [dodger], 'flags': {}}
    apply_turn_boundaries(combat, [{'actorId': 't', 'phase': 'start', 'round': 2}], turn_id=1)
    assert combat['participants'][0]['conditions'] == []


def test_hidden_attacker_gains_advantage_once_then_is_revealed():
    hidden = _p('a', team='player', conditions=['hidden'], position={'rangeBand': 'near', 'isHidden': True})
    combat = {'participants': [hidden, _p('t')]}
    result = _attack(hidden, _p('t'), combat=combat)
    assert result['mode'] == 'advantage'
    assert modifiers.is_hidden(hidden)
    modifiers.consume_modifiers(combat, result['consume'])
    assert combat['participants'][0]['conditions'] == []
    assert combat['participants'][0]['position']['isHidden'] is False
    assert _attack(combat['participants'][0], _p('t'))['mode'] == 'normal'


def test_exhausted_disadvantages_ability_checks():
    assert modifiers.check_context(_p('a', conditions=['exhausted']), ability='wisdom')['mode'] == 'disadvantage'


@pytest.mark.parametrize('condition', ['deafened', 'dying', 'stable', 'surrendered'])
def test_conditions_without_roll_effects_leave_rolls_normal(condition):
    participant = _p('a', conditions=[condition])
    assert _attack(participant, _p('t'))['mode'] == 'normal'
    assert _attack(_p('x'), participant)['mode'] == 'normal'
    assert modifiers.check_context(participant, ability='strength')['mode'] == 'normal'


def test_advantage_and_disadvantage_from_any_number_of_sources_cancel():
    result = _attack(_p('a', conditions=['poisoned', 'prone', 'invisible']), _p('t'))
    assert result['mode'] == 'normal'
    assert len(result['reasons']) == 3


# --- active effects ------------------------------------------------------------


def test_attack_effect_keys_shape_the_attack():
    attacker = _p('a', effects=[_effect('bless', attackBonusDice=['+1d4']), _effect('focus', attackMode='advantage')])
    target = _p('t', effects=[_effect('shield', acBonus=5), _effect('bark', acMinimum=16), _effect('blur', attackedMode='disadvantage')])
    result = _attack(attacker, target)
    assert result['attackBonusDice'] == ['+1d4']
    assert result['mode'] == 'normal'
    assert result['targetAcBonus'] == 5
    assert modifiers.armor_class_with_context(12, result) == 17
    assert modifiers.armor_class_with_context(8, {**result, 'targetAcBonus': 0}) == 16


def test_save_and_check_effect_keys():
    blessed = _p(
        'a',
        effects=[
            _effect('bless', saveBonusDice='+1d4'),
            _effect('resolve', saveMode={'abilities': ['wisdom'], 'mode': 'advantage'}),
            _effect('guidance', checkBonusDice=['+1d4']),
            _effect('bane_check', checkMode='disadvantage'),
        ],
    )
    wisdom = modifiers.save_context(blessed, 'wisdom')
    assert (wisdom['mode'], wisdom['bonusDice']) == ('advantage', ['+1d4'])
    assert modifiers.save_context(blessed, 'strength')['mode'] == 'normal'
    check = modifiers.check_context(blessed, ability='wisdom')
    assert check['bonusDice'] == ['+1d4']
    assert check['mode'] == 'disadvantage'


def test_damage_effect_keys_and_defenses():
    hunter = _p('a', effects=[
        _effect('mark', bonusDamageVs={'targetId': 't', 'dice': '1d6', 'type': 'force'}),
        _effect('smite', bonusWeaponDamage={'dice': '2d8', 'type': 'radiant'}),
        _effect('oil', bonusWeaponDamage={'flat': 2}),
    ])
    assert modifiers.extra_damage(hunter, _p('t'), kind='weapon_attack', weapon={'damageType': 'slashing'}) == [
        {'dice': '1d6', 'type': 'force', 'source': 'Mark'},
        {'type': 'radiant', 'source': 'Smite', 'dice': '2d8'},
        {'type': 'slashing', 'source': 'Oil', 'flat': 2},
    ]
    assert modifiers.extra_damage(hunter, _p('other'), kind='spell_attack') == []
    stoneskin = _p('t', effects=[_effect('stoneskin', resistances=['bludgeoning'])], vulnerabilities=['fire'])
    assert modifiers.damage_multiplier(stoneskin, 'bludgeoning') == 0.5
    assert modifiers.damage_multiplier(stoneskin, 'fire') == 2.0
    warded = _p('t', effects=[_effect('ward', resistances=['all'])], vulnerabilities=['fire'], immunities=['poison'])
    assert modifiers.damage_multiplier(warded, 'fire') == 1.0
    assert modifiers.damage_multiplier(warded, 'poison') == 0.0


def test_state_effect_keys_stop_movement_action_and_healing_and_grant_temp_hp():
    held = _p('a', effects=[_effect('hold', speedZero=True), _effect('daze', cannotAct=True)])
    assert modifiers.speed_is_zero(held)
    assert not modifiers.can_act(held)
    assert modifiers.cannot_act_reasons(held) == ['Held by Daze.']
    rotting = _p('b', team='player', effects=[_effect('rot', noHealing=True)])
    rotting['hp']['current'] = 4
    assert modifiers.heals_nothing(rotting)
    assert vitality.heal(rotting, 5) == 0
    assert rotting['hp']['current'] == 4
    combat = {'round': 1, 'participants': [_p('c', effects=[_effect('armor', tempHpPerTurn=5)])], 'flags': {}}
    apply_turn_boundaries(combat, [{'actorId': 'c', 'phase': 'start', 'round': 1}], turn_id=3)
    assert combat['participants'][0]['hp']['temp'] == 5


def test_consume_on_effects_are_spent_by_their_event():
    attacker = _p('a', effects=[_effect('surge', attackMode='advantage', consumeOn='attack')])
    target = _p('t', effects=[_effect('parry', acBonus=3, consumeOn='attacked'), _effect('luck', saveMode='advantage', consumeOn='save')])
    combat = {'participants': [attacker, target]}
    result = _attack(attacker, target, combat=combat)
    assert {entry['effectId'] for entry in result['consume']} == {'surge', 'parry'}
    removed = modifiers.consume_modifiers(combat, result['consume'])
    assert {effect['id'] for effect in removed} == {'surge', 'parry'}
    assert [effect['id'] for effect in combat['participants'][1]['activeEffects']] == ['luck']
    save = modifiers.save_context(combat['participants'][1], 'wisdom')
    assert save['consume'] == [{'participantId': 't', 'effectId': 'luck'}]
    checker = _p('c', effects=[_effect('tip', checkBonusDice='+1d4', consumeOn='check')])
    assert modifiers.check_context(checker, ability='wisdom')['consume'] == [{'participantId': 'c', 'effectId': 'tip'}]


def test_concentration_end_removes_only_that_casters_concentration_effects_and_their_conditions():
    held = _effect('held', casterId='mage', concentration=True)
    held.update({'sourceId': 'spell_hold', 'concentration': True, 'condition': 'restrained'})
    other = _effect('other', casterId='bard')
    other.update({'sourceId': 'spell_hold', 'concentration': True, 'condition': 'frightened'})
    combat = {'participants': [_p('t', conditions=['restrained', 'frightened'], effects=[held, other])]}
    removed = modifiers.end_concentration_effects(combat, caster_id='mage', source_id='spell_hold')
    assert [effect['id'] for effect in removed] == ['held']
    assert combat['participants'][0]['conditions'] == ['frightened']


def test_legacy_spell_effect_records_read_through_the_standard_shape():
    legacy = {
        'id': 'sfx_1',
        'kind': 'condition',
        'operation': 'add',
        'condition': 'slowed',
        'sourceActorId': 'player_mage',
        'sourceSpellId': 'spell_ray_of_frost',
        'sourceSpellName': 'Ray of Frost',
        'duration': {'remaining': 1, 'tick': 'source_turn_end'},
    }
    assert modifiers.normalize_active_effect(legacy) == {
        'id': 'sfx_1',
        'condition': 'slowed',
        'sourceId': 'spell_ray_of_frost',
        'sourceName': 'Ray of Frost',
        'casterId': 'player_mage',
        'modifiers': {},
        'duration': {'rounds': 1, 'tickOn': 'caster_turn_end'},
        'concentration': False,
        'consumeOn': None,
    }


# --- registries ----------------------------------------------------------------


def test_modifier_and_extra_damage_registries_merge_into_every_context():
    def rage(participant, role, context):
        if role == 'attacker' and participant.get('id') == 'barbarian':
            return {'mode': 'advantage', 'attackBonusDice': ['+1d4'], 'reasons': ['Raging']}
        if role == 'saver':
            return {'flatBonus': 2}
        return None

    def rage_damage(attacker, target, context):
        return [{'flat': 2, 'type': 'slashing', 'source': 'Rage'}] if attacker.get('id') == 'barbarian' else []

    modifiers.register_modifier_provider(rage)
    modifiers.register_extra_damage_provider(rage_damage)
    try:
        result = _attack(_p('barbarian'), _p('t'))
        assert result['mode'] == 'advantage'
        assert result['attackBonusDice'] == ['+1d4']
        assert 'Raging' in result['reasons']
        assert modifiers.save_context(_p('x'), 'con')['flatBonus'] == 2
        assert modifiers.extra_damage(_p('barbarian'), _p('t'), kind='weapon_attack') == [
            {'flat': 2, 'type': 'slashing', 'source': 'Rage'}
        ]
    finally:
        modifiers.unregister_modifier_provider(rage)
        modifiers.unregister_extra_damage_provider(rage_damage)
    assert _attack(_p('barbarian'), _p('t'))['mode'] == 'normal'


def _legal_combat(player_id: int = 7) -> dict:
    return {
        'status': 'active',
        'round': 1,
        'turnIndex': 0,
        'participants': [
            {'id': f'player_{player_id}', 'playerId': player_id, 'name': 'Seraphina', 'team': 'player', 'kind': 'player_character', 'hp': {'current': 10, 'max': 10}},
            {'id': 'enemy_1', 'name': 'Bandit', 'team': 'enemy', 'hp': {'current': 8, 'max': 8}},
        ],
        'initiative': [
            {'participantId': f'player_{player_id}', 'roll': 15, 'modifier': 0, 'total': 15, 'order': 0},
            {'participantId': 'enemy_1', 'roll': 10, 'modifier': 0, 'total': 10, 'order': 1},
        ],
        'flags': {},
    }


def _player(player_id: int = 7) -> Player:
    return Player(
        player_id=player_id,
        character_name='Seraphina',
        class_='Fighter',
        level=1,
        stats=safe_json_dumps({'ability_scores': {'strength': 14, 'dexterity': 12}}, {}),
        inventory=safe_json_dumps([{'name': 'Longsword', 'type': 'weapon', 'equipped': True}], []),
    )


def test_action_provider_and_resolver_registries_extend_the_hud_bundle():
    def feint_provider(snapshot, actor, combat):
        return [{'id': 'combat.feint', 'type': 'feint', 'label': 'Feint', 'economy': {'action': 1}}]

    def feint_resolver(snapshot, player, action, target_id):
        return {'action_id': action['id'], 'action_type': 'feint', 'authoritative': True}, None, None

    register_action_provider(feint_provider)
    register_action_resolver('combat.feint', feint_resolver)
    try:
        bundle = legal_combat_actions_for_player({'combat': _legal_combat()}, _player())
        feint = next(action for action in bundle['actions'] if action['id'] == 'combat.feint')
        assert feint['available'] is True
        assert feint['cost'] == {'action': 1, 'bonusAction': 0, 'movement': 0, 'reaction': 0}
        resolved, code, _message = resolve_combat_legal_action({'combat': _legal_combat()}, _player(), action_id='combat.feint')
        assert code is None
        assert resolved == {'action_id': 'combat.feint', 'action_type': 'feint', 'authoritative': True}
    finally:
        unregister_action_provider(feint_provider)
        unregister_action_resolver('combat.feint')
    bundle = legal_combat_actions_for_player({'combat': _legal_combat()}, _player())
    assert all(action['id'] != 'combat.feint' for action in bundle['actions'])


def test_hud_reasons_come_from_the_modifier_engine():
    combat = _legal_combat()
    combat['participants'][0]['conditions'] = ['restrained']
    bundle = legal_combat_actions_for_player({'combat': combat}, _player())
    reposition = next(action for action in bundle['actions'] if action['id'] == 'combat.reposition')
    assert reposition['available'] is False
    assert reposition['reason'] == 'Restrained: no movement.'
    attack = next(action for action in bundle['actions'] if action['type'] == 'attack')
    assert attack['targets'][0]['rollMode'] == 'disadvantage'
    combat['participants'][0]['conditions'] = ['stunned']
    stunned_bundle = legal_combat_actions_for_player({'combat': combat}, _player())
    assert stunned_bundle['actions'][0]['reason'] == 'Stunned.'


# --- wiring into attacks, saves and turns ---------------------------------------


def _attack_state(target: dict) -> dict:
    return {
        'combat': {
            'status': 'active',
            'participants': [_p('player_1', team='player'), target],
            'battlefield': {'cover': [{'id': 'wall', 'coverType': 'half'}]},
            'flags': {'activeActorId': 'player_1'},
        }
    }


def _player_attack(state: dict, *, kept: int, total: int, damage_roller=None) -> dict:
    return resolve_authoritative_player_attack(
        state=state,
        actor_id='player_1',
        turn_id=9,
        action_intent={
            'combat': {
                'authoritative': True,
                'action_type': 'attack',
                'target_id': 'enemy_1',
                'weapon_id': 'mace',
                'weapon_name': 'Mace',
                'damage_dice': '1d6',
                'damage_type': 'bludgeoning',
            }
        },
        authoritative_roll={'authoritative': True, 'kept': kept, 'total': total, 'modifier_breakdown': {'ability_modifier': 2}},
        damage_roller=damage_roller or (lambda _sides: 4),
    )


def test_cover_raises_armor_class_against_weapon_attacks():
    target = _p('enemy_1', armorClass=13, position={'rangeBand': 'near', 'coverId': 'wall'})
    result = _player_attack(_attack_state(target), kept=10, total=14)
    assert result['targetArmorClass'] == 15
    assert result['coverBonus'] == 2
    assert result['hit'] is False


@pytest.mark.parametrize(
    ('defense', 'expected'),
    [({'resistances': ['bludgeoning']}, 3), ({'vulnerabilities': ['bludgeoning']}, 12), ({'immunities': ['bludgeoning']}, 0), ({}, 6)],
)
def test_weapon_damage_applies_resistance_vulnerability_and_immunity(defense, expected):
    target = _p('enemy_1', armorClass=10, **defense)
    result = _player_attack(_attack_state(target), kept=15, total=19)
    assert result['hit'] is True
    assert result['damageTotal'] == expected


def test_weapon_attack_rolls_a_second_engine_die_with_advantage_and_crits_paralyzed_targets():
    target = _p('enemy_1', armorClass=30, conditions=['paralyzed'])
    result = _player_attack(_attack_state(target), kept=4, total=8)
    assert result['attackMode'] == 'advantage'
    assert len(result['attackRolls']) == 2
    assert result['attackRolls'][0] == 4
    assert result['attackRoll'] == max(result['attackRolls'])


def _enemy_state(target_extra: dict | None = None) -> dict:
    return {
        'playerCharacters': [{'id': 'player_1', 'name': 'Kael'}],
        'combat': {
            'status': 'active',
            'participants': [
                {'id': 'player_1', 'name': 'Kael', 'team': 'player', 'kind': 'player_character', 'level': 1, 'armorClass': 12, 'stats': {'dexterity': 10}, **(target_extra or {})},
                {
                    'id': 'enemy_wolf',
                    'name': 'Wolf',
                    'team': 'enemy',
                    'level': 1,
                    'abilities': [
                        {'id': 'bite', 'name': 'Bite', 'type': 'attack', 'attackBonus': 4, 'damage': {'dice': '1d6+2', 'type': 'piercing'}},
                        {
                            'id': 'howl',
                            'name': 'Howl',
                            'type': 'special',
                            'save': {'ability': 'dexterity', 'dc': 13},
                            'damage': {'dice': '1d4', 'type': 'thunder'},
                            'cooldown': 'recharge_5_6',
                        },
                    ],
                },
            ],
        },
    }


def _enemy_action(state: dict, ability_id: str, rolls: list[int]) -> dict:
    sequence = iter(rolls)
    context = build_dm_combat_context(
        state=state,
        combat_context={'enemyRequiredActions': [{'enemyId': 'enemy_wolf', 'targetId': 'player_1', 'intentType': 'attack', 'abilityId': ability_id}]},
        pending_rolls=[],
        resolved_player_roll=False,
        enemy_roller=lambda _sides: next(sequence),
    )
    return context['enemyResolvedActions'][0]


def test_enemy_critical_hit_doubles_damage_dice_but_not_the_bonus():
    action = _enemy_action(_enemy_state(), 'bite', [20, 3, 4])
    assert action['critical'] is True
    assert action['damageRolls'] == [3, 4]
    assert action['damageTotal'] == 9


def test_pc_saving_throw_proficiency_applies_against_enemy_saves():
    without = _enemy_action(_enemy_state(), 'howl', [11, 2])
    proficient = _enemy_action(_enemy_state({'savingThrowProficiencies': ['dexterity']}), 'howl', [11, 2])
    assert without['saveModifier'] == 0
    assert without['saveSucceeded'] is False
    assert proficient['saveModifier'] == 2
    assert proficient['saveSucceeded'] is True


def _turn(turn_id: int, *, ends_turn: bool = True) -> DmTurn:
    return DmTurn(
        turn_id=turn_id,
        player_id=1,
        metadata_json=safe_json_dumps(
            {'action_intent': {'combat': {'authoritative': True, 'action_type': 'end_turn', 'action_id': 'combat.end_turn', 'economy': {'endsTurn': ends_turn}}}},
            {},
        ),
    )


def _timed(effect_id: str, caster: str = 'player_1') -> dict:
    return {
        'id': effect_id,
        'sourceId': 'spell_ray_of_frost',
        'sourceName': 'Ray of Frost',
        'casterId': caster,
        'condition': 'slowed',
        'modifiers': {},
        'duration': {'rounds': 1, 'tickOn': 'target_turn_end'},
        'concentration': False,
        'consumeOn': None,
    }


def test_turn_advance_ticks_every_enemy_inside_a_grouped_block():
    state = {
        'playerCharacters': [{'id': 'player_1', 'name': 'Kael'}, {'id': 'player_2', 'name': 'Mira'}],
        'combat': {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [
                _p('player_1', team='player'),
                _p('enemy_a', conditions=['slowed'], effects=[_timed('fx_a')]),
                _p('enemy_b', conditions=['slowed'], effects=[_timed('fx_b')]),
                _p('player_2', team='player'),
            ],
            'initiative': [
                {'participantId': pid, 'roll': 20 - index, 'modifier': 0, 'total': 20 - index, 'order': index}
                for index, pid in enumerate(['player_1', 'enemy_a', 'enemy_b', 'player_2'])
            ],
            'flags': {'activeActorId': 'player_1'},
        },
    }
    change = combat_turn_advance_change(state=state, turn=_turn(30))
    assert [(item['actorId'], item['phase']) for item in change['turnBoundaries']] == [
        ('player_1', 'end'),
        ('enemy_a', 'start'),
        ('enemy_a', 'end'),
        ('enemy_b', 'start'),
        ('enemy_b', 'end'),
        ('player_2', 'start'),
    ]
    applied = apply_state_changes(state, [change])['nextState']
    enemies = {participant['id']: participant for participant in applied['combat']['participants']}
    assert enemies['enemy_a']['activeEffects'] == [] and enemies['enemy_a']['conditions'] == []
    assert enemies['enemy_b']['activeEffects'] == [] and enemies['enemy_b']['conditions'] == []
    assert applied['combat']['flags']['activeActorId'] == 'player_2'


def test_recharge_ability_is_spent_then_recharges_on_five_or_six(monkeypatch):
    state = _enemy_state()
    used = apply_state_changes(
        state,
        [{'id': 'mark_howl', 'type': 'combat.ability.mark_used', 'participantId': 'enemy_wolf', 'abilityId': 'howl'}],
    )['nextState']
    howl = used['combat']['participants'][1]['abilities'][1]
    assert howl['needsRecharge'] is True
    assert combat_ability_is_available(howl, round_number=1) is False

    combat = deepcopy(used['combat'])
    monkeypatch.setattr(combat_pipeline, 'deterministic_roller', lambda _seed: (lambda _sides: 4))
    events = apply_turn_boundaries(combat, [{'actorId': 'enemy_wolf', 'phase': 'start', 'round': 2}], turn_id=7)
    assert events[0]['rechargeRolls'] == [{'abilityId': 'howl', 'roll': 4, 'recharged': False}]
    assert combat['participants'][1]['abilities'][1]['needsRecharge'] is True
    monkeypatch.setattr(combat_pipeline, 'deterministic_roller', lambda _seed: (lambda _sides: 5))
    apply_turn_boundaries(combat, [{'actorId': 'enemy_wolf', 'phase': 'start', 'round': 3}], turn_id=8)
    assert combat['participants'][1]['abilities'][1]['needsRecharge'] is False
    assert combat_ability_is_available(combat['participants'][1]['abilities'][1], round_number=3) is True


def _casting_state(casting_time: str | None) -> dict:
    spell = {'id': 'spell_quick_ward', 'name': 'Quick Ward'}
    if casting_time:
        spell['castingTime'] = casting_time
    return {
        'playerCharacters': [{'id': 'player_1', 'name': 'Kael', 'spellbook': {'knownSpells': [spell]}}],
        'combat': {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [_p('player_1', team='player'), _p('enemy_1')],
            'initiative': [
                {'participantId': 'player_1', 'roll': 20, 'modifier': 0, 'total': 20, 'order': 0},
                {'participantId': 'enemy_1', 'roll': 5, 'modifier': 0, 'total': 5, 'order': 1},
            ],
            'flags': {'activeActorId': 'player_1'},
        },
    }


def _cast(change_id: str) -> dict:
    return {'id': change_id, 'type': 'spell.cast', 'actorId': 'player_1', 'spellId': 'spell_quick_ward', 'spellName': 'Quick Ward'}


def test_casting_cost_follows_the_spell_definition_casting_time():
    allowed, rejected = _gate_pre_dm_combat_spells(
        state=_casting_state('bonus_action'),
        changes=[_cast('cast_1'), _cast('cast_2')],
        actor_id='player_1',
        turn_id=4,
    )
    economy = allowed[1]['flags']['turnEconomy']
    assert (economy['actionRemaining'], economy['bonusActionRemaining']) == (1, 0)
    assert rejected == [{'change': _cast('cast_2'), 'reason': "This turn's bonus action is already spent."}]

    allowed, _rejected = _gate_pre_dm_combat_spells(
        state=_casting_state(None),
        changes=[_cast('cast_3')],
        actor_id='player_1',
        turn_id=4,
    )
    economy = allowed[1]['flags']['turnEconomy']
    assert (economy['actionRemaining'], economy['bonusActionRemaining']) == (0, 1)


def test_only_one_levelled_spell_per_turn_but_cantrips_remain_legal():
    state = _casting_state('bonus_action')
    state['playerCharacters'][0]['spellbook']['knownSpells'].extend([
        {'id': 'spell_action', 'name': 'Action Spell', 'level': 1},
        {'id': 'spell_cantrip', 'name': 'Cantrip', 'level': 0},
    ])
    bonus = {**_cast('bonus'), 'castLevel': 1}
    action = {**_cast('action'), 'spellId': 'spell_action', 'spellName': 'Action Spell', 'castLevel': 1}
    cantrip = {**_cast('cantrip'), 'spellId': 'spell_cantrip', 'spellName': 'Cantrip', 'castLevel': 0}

    allowed, rejected = _gate_pre_dm_combat_spells(state=state, changes=[bonus, action], actor_id='player_1', turn_id=4)
    assert [change['id'] for change in allowed if change['type'] == 'spell.cast'] == ['bonus']
    assert rejected[0]['reason'] == 'Only one levelled spell can be cast per turn.'
    state['combat']['flags']['turnEconomy'] = allowed[1]['flags']['turnEconomy']
    assert state['combat']['flags']['turnEconomy']['leveledSpellCast'] is True
    projected = filter_session_snapshot_for_player(state, private_player_ids={1})
    assert projected['combat']['flags']['turnEconomy']['leveledSpellCast'] is True
    allowed, rejected = _gate_pre_dm_combat_spells(state=state, changes=[cantrip], actor_id='player_1', turn_id=4)
    assert not rejected
    assert allowed[0]['id'] == 'cantrip'

    reverse = _casting_state('bonus_action')
    reverse['playerCharacters'][0]['spellbook']['knownSpells'].append({'id': 'spell_action', 'name': 'Action Spell', 'level': 1})
    allowed, rejected = _gate_pre_dm_combat_spells(state=reverse, changes=[action, bonus], actor_id='player_1', turn_id=5)
    assert allowed[0]['id'] == 'action'
    assert rejected[0]['reason'] == 'Only one levelled spell can be cast per turn.'


def test_operator_api_combat_start_uses_seeded_initiative_with_modifiers(client, app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr(creatures_blueprint, 'plan_enemy_intents', lambda _combat: {'intents': [], 'summaryForDm': ''})
    response = client.post(
        f"/api/sessions/{ids['session_id']}/combat/start",
        json={'creature': {'name': 'Bandit', 'creatureType': 'humanoid', 'stats': {'dexterity': 16}}, 'enemyCount': 2},
    )
    assert response.status_code == 200
    initiative = response.get_json()['combat']['initiative']
    assert {entry['source'] for entry in initiative} == {'server_deterministic'}
    bandit = next(entry for entry in initiative if entry['participantId'].startswith('enemy_'))
    assert bandit['modifier'] == 3
    assert [entry['order'] for entry in initiative] == list(range(len(initiative)))
    assert sorted(initiative, key=lambda entry: entry['order']) == sorted(
        initiative, key=lambda entry: (-entry['total'], -entry['modifier'], entry['participantId'])
    )


def test_stored_legacy_combat_without_seed_keeps_roster_initiative():
    combat = normalize_combat_state({'participants': [_p('player_1', team='player'), _p('enemy_1')]})
    assert [entry['source'] for entry in combat['initiative']] == ['legacy_roster_order', 'legacy_roster_order']


# --- consistency fixes -----------------------------------------------------------


def _snapshot_for(scene: dict, locations: list | None = None) -> dict:
    session_obj = Session(session_id=3, campaign_id=2, state_snapshot=safe_json_dumps({'currentScene': scene, 'locations': locations or []}, {}))
    campaign = Campaign(campaign_id=2, location='Old Ruins')
    return state_snapshot_for_session(session_obj=session_obj, campaign=campaign, players=[])


def test_snapshot_rebuild_keeps_stored_scene_collections_and_unknown_keys():
    scene = {
        'locationId': 'gate',
        'interactables': [{'id': 'door', 'name': 'Door', 'locked': True}],
        'hazards': [{'id': 'pit', 'name': 'Pit'}],
        'items': [{'id': 'coin', 'name': 'Coin'}],
        'summary': 'A wind-scoured gate.',
        'visibleDetails': ['rusted chain'],
        'hiddenSceneNotes': 'The chain is trapped.',
    }
    rebuilt = _snapshot_for(scene, [{'id': 'gate', 'interactables': [{'id': 'other'}]}])['currentScene']
    assert rebuilt['interactables'] == scene['interactables']
    assert rebuilt['hazards'] == scene['hazards']
    assert rebuilt['items'] == scene['items']
    assert rebuilt['summary'] == 'A wind-scoured gate.'
    assert rebuilt['visibleDetails'] == ['rusted chain']
    assert rebuilt['hiddenSceneNotes'] == 'The chain is trapped.'


def test_snapshot_rebuild_defaults_absent_collections_from_the_authored_location():
    locations = [{'id': 'gate', 'sceneState': {'interactables': [{'id': 'lever', 'name': 'Lever'}]}, 'hazards': [{'id': 'spikes'}]}]
    rebuilt = _snapshot_for({'locationId': 'gate'}, locations)['currentScene']
    assert rebuilt['interactables'] == [{'id': 'lever', 'name': 'Lever'}]
    assert rebuilt['hazards'] == [{'id': 'spikes'}]
    assert rebuilt['items'] == []


def _roll_player(conditions: list[str] | None = None) -> Player:
    stats = {'ability_scores': {'dexterity': 14, 'wisdom': 10}, 'current_hp': 20, 'max_hp': 20}
    if conditions:
        stats['conditions'] = conditions
    return Player(player_id=7, character_name='Seraphina', class_='Rogue', level=1, stats=safe_json_dumps(stats, {}))


def test_roll_result_reports_dc_source_and_outcome_and_exposes_them_once_landed():
    stated = resolve_authoritative_player_roll(
        player=_roll_player(), rule_type='check', dc_hint='DC 12', action_intent={'kind': 'roll'}, roller=lambda _sides: 11,
    )
    assert (stated['dc'], stated['dcSource'], stated['outcome']) == (12, 'stated', 'failure')
    default = resolve_authoritative_player_roll(
        player=_roll_player(), rule_type='check', dc_hint='10-18', action_intent={'kind': 'roll'}, roller=lambda _sides: 16,
    )
    assert (default['dc'], default['dcSource'], default['outcome']) == (14, 'default', 'success')
    adjusted_default = resolve_authoritative_player_roll(
        player=_roll_player(), rule_type='check', dc_hint='14 (roll mod +0)', action_intent={'kind': 'roll'}, roller=lambda _sides: 16,
    )
    assert adjusted_default['dcSource'] == 'default'
    initiative = resolve_authoritative_player_roll(
        player=_roll_player(), rule_type='initiative', dc_hint=None, action_intent={'kind': 'roll'}, roller=lambda _sides: 9,
    )
    assert initiative['outcome'] is None
    public = public_roll_payload(stated)
    assert (public['dc'], public['dcSource'], public['outcome']) == (12, 'stated', 'failure')


def test_pending_roll_mode_comes_from_conditions_unless_the_gate_forces_one():
    pending = DmTurn(turn_id=1, rules_hint=safe_json_dumps({'roll_spec': {'die': 'd20', 'mode': 'normal'}}, {}), metadata_json='{}')
    values = iter([15, 6])
    poisoned = resolve_authoritative_player_roll(
        player=_roll_player(['poisoned']), rule_type='perception', dc_hint=None, action_intent={'kind': 'roll'},
        pending_turn=pending, roller=lambda _sides: next(values),
    )
    assert poisoned['mode'] == 'disadvantage'
    assert poisoned['kept'] == 6
    assert poisoned['mode_reasons'] == ['Character is poisoned']
    forced = DmTurn(turn_id=2, rules_hint=safe_json_dumps({'roll_spec': {'die': 'd20', 'mode': 'advantage'}}, {}), metadata_json='{}')
    values = iter([3, 17])
    gated = resolve_authoritative_player_roll(
        player=_roll_player(['poisoned']), rule_type='perception', dc_hint=None, action_intent={'kind': 'roll'},
        pending_turn=forced, roller=lambda _sides: next(values),
    )
    assert gated['mode'] == 'advantage'
    assert gated['kept'] == 17


def test_pending_save_rolls_bonus_dice_from_active_effects(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        session_obj.state_snapshot = safe_json_dumps(
            {
                'combat': {
                    'status': 'active',
                    'participants': [
                        _p(f"player_{ids['player_id']}", team='player', effects=[_effect('bless', saveBonusDice='+1d4')]),
                    ],
                }
            },
            {},
        )
        db.session.commit()
        pending = DmTurn(turn_id=3, session_id=ids['session_id'], rules_hint='{}', metadata_json='{}')
        player = db.session.get(Player, ids['player_id'])
        values = iter([12, 3])
        roll = resolve_authoritative_player_roll(
            player=player, rule_type='wisdom_saving_throw', dc_hint='DC 15', action_intent={'kind': 'roll'},
            pending_turn=pending, roller=lambda _sides: next(values),
        )
    assert roll['bonus_dice'] == [{'dice': '+1d4', 'rolls': [3], 'total': 3}]
    assert roll['total'] == 12 + roll['modifier'] + 3
    assert canonical_roll_sentence(roll).endswith(f"+1d4 (3) = {roll['total']}")


def test_harmful_spell_cannot_target_the_casters_charmer():
    caster = _p('player_1', team='player', conditions=['charmed'], charmedBy='enemy_witch', stats={'intelligence': 16})
    combat = {'status': 'active', 'participants': [caster, _p('enemy_witch')], 'flags': {'activeActorId': 'player_1'}}
    spell = {
        'id': 'spell_fire_bolt',
        'name': 'Fire Bolt',
        'delivery': {'type': 'attack', 'attackBonus': 5},
        'target': {'relation': 'enemy', 'rangeBands': ['near']},
        'effects': [{'kind': 'damage', 'dice': '1d10', 'damageType': 'fire'}],
    }
    legality = spell_target_legality(combat, caster_id='player_1', spell=spell, target_ids=['enemy_witch'])
    assert legality['code'] == 'spell_target_charmer'


def test_spell_attack_uses_the_engine_and_spends_consumed_effects():
    caster = _p('player_1', team='player', effects=[_effect('true_strike', attackMode='advantage', consumeOn='attack')])
    combat = {'status': 'active', 'participants': [caster, _p('enemy_1', armorClass=12)], 'flags': {'activeActorId': 'player_1'}}
    spell = {
        'id': 'spell_fire_bolt',
        'name': 'Fire Bolt',
        'delivery': {'type': 'attack', 'attackBonus': 5},
        'target': {'relation': 'enemy', 'rangeBands': ['near']},
        'effects': [{'kind': 'damage', 'dice': '1d10', 'damageType': 'fire'}],
    }
    rolls = iter([3, 15, 6])
    result = resolve_targeted_spell(
        combat, caster_id='player_1', spell=spell, target_ids=['enemy_1'], cast_id='cast_x', roller=lambda _sides: next(rolls),
    )
    delivery = result['resolution']['targets'][0]['delivery']
    assert (delivery['mode'], delivery['rolls'], delivery['naturalRoll']) == ('advantage', [3, 15], 15)
    assert delivery['consumedEffects'] == ['true_strike']
    assert result['combat']['participants'][0]['activeEffects'] == []


def test_pending_attack_roll_reads_the_attackers_own_conditions():
    pending = DmTurn(turn_id=4, rules_hint=safe_json_dumps({'roll_spec': {'die': 'd20', 'mode': 'normal'}}, {}), metadata_json='{}')
    values = iter([18, 7])
    roll = resolve_authoritative_player_roll(
        player=_roll_player(['blinded']), rule_type='attack', dc_hint=None, action_intent={'kind': 'roll'},
        pending_turn=pending, roller=lambda _sides: next(values),
    )
    assert (roll['mode'], roll['kept']) == ('disadvantage', 7)
