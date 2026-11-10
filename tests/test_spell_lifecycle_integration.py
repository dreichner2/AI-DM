from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

from aidm_server.character_resources import normalize_spell_resources
from aidm_server.combat import modifiers
from aidm_server.combat.pipeline import apply_turn_boundaries
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.orchestration.combat_resolution import build_dm_combat_context, derive_trusted_damage_changes
from aidm_server.game_state.validation.validator import validate_state_changes, validated_changes_for_application
from aidm_server.spell_effects import resolve_targeted_spell, resolve_turn_start_spell_effects
from aidm_server.spellbook import normalize_spellbook, spell_payload


def _state_with_concentration() -> dict:
    concentration = {
        'active': True,
        'spellId': 'spell_entangle',
        'spellName': 'Entangle',
        'casterActorId': 'player_1',
        'targetIds': ['enemy_1'],
    }
    return {
        'currentScene': {
            'locationId': 'glade',
            'sceneType': 'combat',
            'combatState': 'active',
        },
        'playerCharacters': [
            {
                'id': 'player_1',
                'playerId': 1,
                'name': 'Rowan',
                'stats': {'constitution': 10},
                'health': {
                    'currentHp': 18,
                    'maxHp': 18,
                    'tempHp': 0,
                    'conditions': [],
                },
                'spellResources': {
                    'revision': 4,
                    'slots': {'1': {'current': 0, 'max': 2}},
                    'concentration': dict(concentration),
                },
            }
        ],
        'combat': {
            'status': 'active',
            'round': 2,
            'turnIndex': 0,
            'participants': [
                {
                    'id': 'player_1',
                    'name': 'Rowan',
                    'team': 'player',
                    'kind': 'player_character',
                    'stats': {'constitution': 10},
                    'hp': {'current': 18, 'max': 18, 'temp': 0},
                    'conditions': [],
                    'isAlive': True,
                    'isConscious': True,
                    'isPresent': True,
                },
                {
                    'id': 'enemy_1',
                    'name': 'Mire Wolf',
                    'team': 'enemy',
                    'kind': 'creature',
                    'hp': {'current': 12, 'max': 12, 'temp': 0},
                    'conditions': ['restrained'],
                    'activeEffects': [
                        {
                            'id': 'entangle_restrained_enemy_1',
                            'kind': 'condition',
                            'operation': 'add',
                            'condition': 'restrained',
                            'sourceActorId': 'player_1',
                            'sourceSpellId': 'spell_entangle',
                            'concentration': True,
                            'duration': {'kind': 'concentration'},
                        }
                    ],
                    'isAlive': True,
                    'isConscious': True,
                    'isPresent': True,
                },
            ],
            'flags': {'activeActorId': 'player_1'},
        },
        'stateChangeLedger': [],
    }


def test_authoritative_damage_resolves_one_concentration_check_and_retry_is_idempotent() -> None:
    state = _state_with_concentration()
    damage = {
        'id': 'damage_player_1_round_2',
        'type': 'health.damage',
        'actorId': 'player_1',
        'amount': 7,
        'turnId': 22,
        'source': 'combat_resolution',
    }

    # A natural 1 fails the DC 10 Constitution save.
    with patch('aidm_server.game_state.application.applier.secrets.randbelow', return_value=0) as roller:
        applied = apply_state_changes(state, [damage])

    assert roller.call_count == 1
    next_state = applied['nextState']
    assert next_state['playerCharacters'][0]['health']['currentHp'] == 11
    assert next_state['playerCharacters'][0]['spellResources']['concentration'] is None
    enemy = next_state['combat']['participants'][1]
    assert enemy['conditions'] == []
    assert enemy['activeEffects'] == []
    check = applied['appliedChanges'][0]['concentrationCheck']
    assert check['required'] is True
    assert check['maintained'] is False
    assert check['check']['dc'] == 10

    with patch('aidm_server.game_state.application.applier.secrets.randbelow') as retry_roller:
        retried = apply_state_changes(next_state, [damage])

    assert retry_roller.call_count == 0
    assert retried['nextState']['playerCharacters'][0]['health']['currentHp'] == 11
    assert retried['nextState']['combat']['participants'][1]['conditions'] == []


def test_turn_advance_expires_target_timed_effect_and_syncs_player_condition() -> None:
    state = _state_with_concentration()
    actor = state['playerCharacters'][0]
    actor['health']['conditions'] = ['slowed']
    player = state['combat']['participants'][0]
    player['conditions'] = ['slowed']
    player['activeEffects'] = [
        {
            'id': 'ray_of_frost_slow_player_1',
            'kind': 'condition',
            'operation': 'add',
            'condition': 'slowed',
            'sourceActorId': 'enemy_1',
            'sourceSpellId': 'spell_ray_of_frost',
            'duration': {'remaining': 1, 'tick': 'target_turn_end'},
        }
    ]
    update = {
        'id': 'advance_from_player_1_round_2',
        'type': 'combat.update',
        'round': 2,
        'turnIndex': 1,
        'flags': {'activeActorId': 'enemy_1'},
        'turnId': 22,
        'source': 'combat_turn_engine',
    }

    applied = apply_state_changes(state, [update])

    next_state = applied['nextState']
    player_after = next_state['combat']['participants'][0]
    assert player_after['activeEffects'] == []
    assert player_after['conditions'] == []
    assert next_state['playerCharacters'][0]['health']['conditions'] == []
    assert next_state['combat']['flags']['activeActorId'] == 'enemy_1'


CLUB = {'id': 'club', 'name': 'Club', 'type': 'attack', 'range': 'melee', 'attackBonus': 3, 'damage': {'dice': '1d4+1', 'type': 'bludgeoning'}}
NO_LLM = {'tacticalLevel': 'simple', 'allowSentientEnemyBrain': False, 'allowFreeformEnemyTactics': False, 'maxLlmCallsPerRound': 0}


def _fighter(participant_id: str, team: str, **extra: object) -> dict:
    participant = {
        'id': participant_id, 'name': participant_id.replace('_', ' ').title(), 'team': team,
        'kind': 'player_character' if team == 'player' else 'creature', 'level': 3,
        'hp': {'current': 20, 'max': 20, 'temp': 0}, 'armorClass': 12,
        'stats': {'strength': 14, 'dexterity': 12, 'constitution': 10, 'intelligence': 16, 'wisdom': 10, 'charisma': 10},
        'conditions': [], 'position': {'rangeBand': 'melee'}, 'isAlive': True, 'isConscious': True, 'isPresent': True,
        **extra,
    }
    if team == 'enemy':
        participant['abilities'] = [deepcopy(CLUB)]
    return participant


def _shield_state(*, auto_reactions: bool = True) -> dict:
    participants = [_fighter('player_1', 'player'), _fighter('enemy_1', 'enemy'), _fighter('enemy_2', 'enemy')]
    actor = {
        'id': 'player_1', 'playerId': 1, 'name': 'Player 1', 'class': 'Wizard', 'level': 3,
        'health': {'currentHp': 20, 'maxHp': 20, 'tempHp': 0, 'conditions': []},
        'stats': dict(participants[0]['stats']),
        'spellbook': normalize_spellbook({'knownSpells': [{'name': 'Shield', 'level': 1}]}, class_name='Wizard'),
        'spellResources': normalize_spell_resources(None, class_name='Wizard', level=3),
    }
    if not auto_reactions:
        actor['autoReactions'] = False
    return {
        'playerCharacters': [actor],
        'combat': {
            'status': 'active', 'round': 1, 'turnIndex': 0, 'participants': participants,
            'initiative': [
                {'participantId': participant['id'], 'roll': 20 - index, 'modifier': 0, 'total': 20 - index, 'order': index}
                for index, participant in enumerate(participants)
            ],
            'flags': {'activeActorId': 'player_1', 'combatDifficultyAI': NO_LLM},
        },
    }


def _enemy_block_changes(state: dict) -> tuple[list[dict], list[dict]]:
    attacks = [{'enemyId': enemy, 'targetId': 'player_1', 'intentType': 'attack', 'abilityId': 'club'} for enemy in ('enemy_1', 'enemy_2')]
    context = build_dm_combat_context(
        state=state, combat_context={'enemyRequiredActions': attacks}, pending_rolls=[], resolved_player_roll=False,
        enemy_roller=lambda sides: 11 if sides == 20 else 2, turn_id=60,
    )
    changes = derive_trusted_damage_changes(
        state=state, dm_context_packet={'combatState': context}, actor_id='player_1', turn_id=60, already_applied_changes=[],
    ).enemy
    return context['enemyResolvedActions'], changes


def test_shield_is_cast_automatically_when_it_turns_a_hit_into_a_miss() -> None:
    state = _shield_state()
    (first, second), changes = _enemy_block_changes(state)
    assert first['hit'] is False and first['hitCancelledBy'].startswith('Player 1 casts Shield')
    assert (second['hit'], second['targetArmorClass']) == (False, 17)
    validation = validate_state_changes(state=state, changes=changes)
    assert validation['rejected'] == []
    applied = apply_state_changes(state, validated_changes_for_application(validation))
    cast = next(change for change in applied['appliedChanges'] if change['type'] == 'spell.cast')
    assert (cast['spellName'], cast['castingTime'], cast['resourceConsumed']) == ('Shield', 'reaction', True)
    after = applied['nextState']
    assert after['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 3
    wizard = after['combat']['participants'][0]
    assert wizard['reactionRemaining'] == 0 and wizard['hp']['current'] == 20
    apply_turn_boundaries(after['combat'], [{'actorId': 'player_1', 'phase': 'start'}], turn_id=61)
    assert modifiers.active_effects(after['combat']['participants'][0]) == []


def test_shield_respects_the_auto_reaction_opt_out_and_a_missing_slot() -> None:
    (first, _second), changes = _enemy_block_changes(_shield_state(auto_reactions=False))
    assert first['hit'] is True and first['reactions'] == []
    assert not any(change['type'] == 'spell.cast' for change in changes)

    exhausted = _shield_state()
    exhausted['playerCharacters'][0]['spellResources']['slots']['1']['current'] = 0
    exhausted['playerCharacters'][0]['spellResources']['slots']['2']['current'] = 0
    (first, _second), _changes = _enemy_block_changes(exhausted)
    assert first['hit'] is True and first['reactions'] == []


def _hold_person_combat() -> dict:
    caster = _fighter('player_1', 'player', position={'rangeBand': 'near'}, stats={
        'strength': 10, 'dexterity': 12, 'constitution': 10, 'intelligence': 10, 'wisdom': 16, 'charisma': 10,
    })
    enemies = [_fighter(f'enemy_{index}', 'enemy', position={'rangeBand': 'near'}) for index in (1, 2)]
    combat = {'status': 'active', 'round': 1, 'turnIndex': 0, 'participants': [caster, *enemies],
              'flags': {'activeActorId': 'player_1'}}
    spell = spell_payload('Hold Person')
    spell['delivery']['castingAbility'] = 'wisdom'
    held = resolve_targeted_spell(combat, caster_id='player_1', spell=spell, target_ids=['enemy_1', 'enemy_2'],
                                  cast_id='hold', roller=lambda sides: 2, cast_level=3, caster_resources={})
    assert held['ok'] is True
    return held['combat']


def test_repeat_saves_across_an_enemy_turn_block_end_only_the_saver_s_paralysis() -> None:
    combat = _hold_person_combat()
    rolls = {'enemy_1': 18, 'enemy_2': 3}
    for enemy_id in ('enemy_1', 'enemy_2'):
        (event,) = resolve_turn_start_spell_effects(combat, actor_id=enemy_id, turn_id=7, round_number=1,
                                                    roller=lambda sides, value=rolls[enemy_id]: value)
        assert event['kind'] == 'repeat_save' and event['save']['dc'] == 13
    by_id = {participant['id']: participant for participant in combat['participants']}
    assert 'paralyzed' not in by_id['enemy_1']['conditions'] and modifiers.active_effects(by_id['enemy_1']) == []
    assert 'paralyzed' in by_id['enemy_2']['conditions'] and not modifiers.can_act(by_id['enemy_2'])

    # Without an injected roller a working copy and the persisted advance agree.
    working, persisted = deepcopy(combat), deepcopy(combat)
    assert resolve_turn_start_spell_effects(working, actor_id='enemy_2', turn_id=8, round_number=2) == \
        resolve_turn_start_spell_effects(persisted, actor_id='enemy_2', turn_id=8, round_number=2)
    assert working == persisted


def test_combat_end_keeps_long_rest_effects_and_drops_encounter_effects() -> None:
    state = _shield_state()
    state['currentScene'] = {'locationId': 'hall', 'sceneType': 'combat', 'combatState': 'active'}
    wizard = state['combat']['participants'][0]
    mage_armor = {'id': 'mage_armor', 'sourceId': 'spell_mage_armor', 'sourceName': 'Mage Armor', 'casterId': 'player_1',
                  'castId': 'cast_a', 'modifiers': {'acBase': 13}, 'duration': None, 'until': 'long_rest', 'spellLevel': 1}
    barkskin = {'id': 'barkskin', 'sourceId': 'spell_barkskin', 'sourceName': 'Barkskin', 'casterId': 'player_1',
                'castId': 'cast_b', 'modifiers': {'acMinimum': 16}, 'duration': None, 'until': 'encounter', 'spellLevel': 2,
                'concentration': True}
    wizard['activeEffects'] = [mage_armor, barkskin]
    state['playerCharacters'][0]['activeEffects'] = [deepcopy(mage_armor), deepcopy(barkskin)]
    state['playerCharacters'][0]['spellResources']['concentration'] = {
        'active': True, 'spellId': 'spell_barkskin', 'spellName': 'Barkskin', 'casterActorId': 'player_1',
    }
    end = {'id': 'end', 'turnId': 70, 'type': 'combat.end', 'status': 'ended', 'endReason': 'all_enemies_defeated'}
    applied = apply_state_changes(state, [end])
    actor = applied['nextState']['playerCharacters'][0]
    assert [effect['id'] for effect in actor['activeEffects']] == ['mage_armor']
    assert actor['spellResources']['concentration'] is None
    assert applied['appliedChanges'][0]['endedSpellEffects'] == ['barkskin']
