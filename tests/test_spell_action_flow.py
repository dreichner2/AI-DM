from __future__ import annotations

from aidm_server.action_intent import validate_action_intent
from aidm_server.character_resources import normalize_spell_resources
from aidm_server.database import db
from aidm_server.game_state.application.applier import apply_state_changes, persist_state_to_database
from aidm_server.game_state.extraction.pre_dm_action_extractor import extract_pre_dm_actions
from aidm_server.game_state.models import display_actor_id, player_character_from_model
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.models import Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.spellbook import normalize_spellbook
from tests.helpers import seed_world_campaign_player_session


def _spell(name: str, level: int, **extra: object) -> dict[str, object]:
    return {
        'id': f'spell_{name.lower().replace(" ", "_")}',
        'name': name,
        'level': level,
        **extra,
    }


def _caster_state(*, class_name: str = 'Wizard', level: int = 1) -> dict:
    spellbook = normalize_spellbook(
        {
            'knownSpells': [
                _spell('Magic Missile', 1),
                _spell('Entangle', 1),
                _spell('Dancing Lights', 0, concentration=True),
            ]
        },
        class_name=class_name,
    )
    return {
        'currentScene': {
            'locationId': 'camp',
            'name': 'Camp',
            'sceneType': 'exploration',
            'combatState': 'none',
            'activeNpcIds': [],
            'items': [],
        },
        'activePlayerIds': [1],
        'playerCharacters': [
            {
                'id': 'player_1',
                'playerId': 1,
                'name': 'Ilyra',
                'class': class_name,
                'level': level,
                'health': {
                    'currentHp': 3,
                    'maxHp': 10,
                    'tempHp': 2,
                    'conditions': ['poisoned'],
                },
                'inventory': {'items': [], 'currency': {'gp': 0}},
                'spellbook': spellbook,
                'spellResources': normalize_spell_resources(None, class_name=class_name, level=level),
                'raceAbilityState': {
                    'fey_step': {
                        'available': False,
                        'usedAtTurn': 1,
                        'refreshesOn': 'short_rest',
                    },
                    'daily_ward': {
                        'available': False,
                        'usedAtTurn': 1,
                        'refreshesOn': 'long_rest',
                    },
                },
                'xp': {'current': 0},
            }
        ],
        'stateChangeLedger': [],
    }


def _run_intent(state: dict, raw_intent: dict, *, turn_id: int) -> tuple[dict, dict, dict]:
    intent, error = validate_action_intent(raw_intent)
    assert error is None
    extraction = extract_pre_dm_actions(
        current_state=state,
        player_message=raw_intent.get('text') or 'Do it.',
        recent_timeline=[],
        actor_id='player_1',
        action_intent=intent,
    )
    declared = validate_declared_actions(
        state=state,
        declared_actions=extraction['declaredActions'],
        current_turn=turn_id,
        expected_actor_id='player_1',
    )
    immediate = validate_state_changes(
        state=state,
        changes=declared['immediateChanges'],
        expected_actor_id='player_1',
    )
    applied = apply_state_changes(state, validated_changes_for_application(immediate))
    return declared, immediate, applied


def _cast_magic_missile(state: dict, turn_id: int) -> tuple[dict, dict, dict]:
    return _run_intent(
        state,
        {
            'kind': 'spell',
            'source': 'composer',
            'text': 'I cast Magic Missile.',
            'spell': {'name': 'Magic Missile', 'effect': 'I send force darts at the target.'},
        },
        turn_id=turn_id,
    )


def _combat_state() -> dict:
    state = _caster_state()
    actor = state['playerCharacters'][0]
    actor['stats'] = {
        'strength': 8,
        'dexterity': 14,
        'constitution': 12,
        'intelligence': 16,
        'wisdom': 10,
        'charisma': 8,
    }
    state['currentScene']['sceneType'] = 'combat'
    state['currentScene']['combatState'] = 'active'
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'turnIndex': 0,
        'participants': [
            {
                'id': 'player_1',
                'name': 'Ilyra',
                'team': 'player',
                'kind': 'player_character',
                'level': 1,
                'stats': dict(actor['stats']),
                'hp': {'current': 3, 'max': 10, 'temp': 2},
                'armorClass': 12,
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'isAlive': True,
                'isConscious': True,
            },
            {
                'id': 'enemy_goblin_1',
                'name': 'Goblin',
                'team': 'enemy',
                'kind': 'creature',
                'level': 1,
                'stats': {'strength': 8, 'dexterity': 14, 'constitution': 10},
                'hp': {'current': 20, 'max': 20, 'temp': 0},
                'armorClass': 13,
                'conditions': [],
                'position': {'rangeBand': 'near'},
                'isAlive': True,
                'isConscious': True,
            },
        ],
        'initiative': [
            {'participantId': 'player_1', 'roll': 15, 'modifier': 2, 'total': 17, 'order': 0},
            {'participantId': 'enemy_goblin_1', 'roll': 10, 'modifier': 2, 'total': 12, 'order': 1},
        ],
        'battlefield': {},
        'flags': {
            'activeActorId': 'player_1',
            'turnEconomy': {
                'actorId': 'player_1',
                'round': 1,
                'actionRemaining': 1,
                'bonusActionRemaining': 1,
                'reactionRemaining': 1,
                'movementRemaining': 1,
                'spentActionIds': [],
            },
        },
    }
    return state


def test_targeted_spell_resolves_exact_effect_before_narration_and_retries_once() -> None:
    state = _combat_state()
    declared, validation, applied = _run_intent(
        state,
        {
            'kind': 'spell',
            'text': 'I cast Magic Missile at the goblin.',
            'spell': {
                'name': 'Magic Missile',
                'effect': 'Force darts strike the selected goblin.',
                'target_ids': ['enemy_goblin_1'],
            },
        },
        turn_id=21,
    )

    assert declared['validatedActions'][0]['status'] == 'valid'
    assert validation['rejected'] == []
    cast_change = applied['appliedChanges'][0]
    assert cast_change['resolutionAuthority'] == 'spell_effect_engine'
    assert cast_change['spellResolution']['targetIds'] == ['enemy_goblin_1']
    enemy = applied['nextState']['combat']['participants'][1]
    assert 5 <= enemy['hp']['current'] <= 14
    assert applied['nextState']['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 1

    retried = apply_state_changes(
        applied['nextState'],
        validated_changes_for_application(validation),
    )
    assert retried['nextState']['combat']['participants'][1]['hp'] == enemy['hp']
    assert retried['nextState']['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 1


def test_upcast_healing_replays_exact_rolls_and_consumes_the_chosen_slot(monkeypatch) -> None:
    state = _combat_state()
    actor = state['playerCharacters'][0]
    actor['class'] = 'Cleric'
    actor['level'] = 5
    actor['stats']['wisdom'] = 16
    actor['health']['maxHp'] = 30
    actor['spellbook'] = normalize_spellbook({'knownSpells': [_spell('Healing Word', 1)]}, class_name='Cleric')
    actor['spellResources'] = normalize_spell_resources(None, class_name='Cleric', level=5)
    participant = state['combat']['participants'][0]
    participant['level'] = 5
    participant['stats']['wisdom'] = 16
    participant['hp']['max'] = 30
    monkeypatch.setattr('aidm_server.game_state.validation.validator.secrets.randbelow', lambda _sides: 2)
    declared, validation, applied = _run_intent(
        state,
        {'kind': 'spell', 'text': 'I cast Healing Word on myself.', 'spell': {
            'name': 'Healing Word', 'effect': 'Heal myself.', 'target_ids': ['player_1'], 'cast_level': 3,
        }},
        turn_id=24,
    )
    assert declared['validatedActions'][0]['status'] == 'valid'
    assert validation['rejected'] == []
    resolution = applied['appliedChanges'][0]['spellResolution']
    assert resolution['castLevel'] == 3
    healing = resolution['targets'][0]['effects'][0]
    assert healing['roll']['rolls'] == [3, 3, 3]
    assert healing['roll']['castingModifier'] == 3
    assert healing['amountApplied'] == 12
    assert applied['nextState']['playerCharacters'][0]['spellResources']['slots']['3']['current'] == 1
    assert applied['nextState']['playerCharacters'][0]['health']['currentHp'] == 15


def test_out_of_combat_healing_targets_active_party_and_preserves_combat_state(monkeypatch) -> None:
    state = _caster_state(class_name='Cleric', level=1)
    actor = state['playerCharacters'][0]
    actor['stats'] = {'wisdom': 16}
    actor['spellbook'] = normalize_spellbook({'knownSpells': [_spell('Cure Wounds', 1)]}, class_name='Cleric')
    state['activePlayerIds'] = [1, 2]
    state['playerCharacters'].append({
        'id': 'player_2', 'playerId': 2, 'name': 'Borin', 'class': 'Fighter', 'level': 1,
        'stats': {}, 'health': {'currentHp': 0, 'maxHp': 10, 'conditions': ['dying']},
        'inventory': {'items': []},
    })
    state['playerCharacters'].append({
        'id': 'player_3', 'playerId': 3, 'name': 'Absent', 'class': 'Fighter', 'level': 1,
        'stats': {}, 'health': {'currentHp': 1, 'maxHp': 10, 'conditions': []},
        'inventory': {'items': []},
    })
    monkeypatch.setattr('aidm_server.game_state.validation.validator.secrets.randbelow', lambda _sides: 0)
    intent = {'kind': 'spell', 'text': 'I heal Borin.', 'spell': {
        'name': 'Cure Wounds', 'effect': 'Heal my companion.', 'target_ids': ['player_2'],
    }}
    declared, validation, applied = _run_intent(state, intent, turn_id=25)
    assert declared['validatedActions'][0]['status'] == 'valid'
    assert validation['rejected'] == []
    healed = applied['nextState']['playerCharacters'][1]
    assert healed['health']['currentHp'] == 4
    assert 'dying' not in healed['health']['conditions']
    assert applied['nextState']['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 1
    assert applied['nextState'].get('combat', {}).get('status') not in {'active', 'starting'}
    intent['spell']['target_ids'] = ['player_3']
    rejected, _, unchanged = _run_intent(state, intent, turn_id=26)
    assert rejected['validatedActions'][0]['status'] == 'invalid'
    assert unchanged['nextState']['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 2


def test_spell_damage_concentration_check_is_replayed_and_applied_once(monkeypatch) -> None:
    state = _combat_state()
    caster, enemy = state['combat']['participants']
    caster['activeEffects'] = [{
        'id': 'enemy_buff', 'sourceId': 'spell_hex', 'sourceName': 'Hex',
        'casterId': 'enemy_goblin_1', 'concentration': True, 'modifiers': {'acBonus': 1},
    }]
    enemy['spellResources'] = {'concentration': {
        'active': True, 'spellId': 'spell_hex', 'spellName': 'Hex',
        'casterActorId': 'enemy_goblin_1', 'targetIds': ['player_1'],
    }}
    monkeypatch.setattr('aidm_server.game_state.validation.validator.secrets.randbelow', lambda _sides: 0)
    declared, validation, applied = _run_intent(state, {
        'kind': 'spell', 'text': 'I cast Magic Missile at the goblin.', 'spell': {
            'name': 'Magic Missile', 'effect': 'Force darts strike.', 'target_ids': ['enemy_goblin_1'],
        },
    }, turn_id=27)
    assert declared['validatedActions'][0]['status'] == 'valid'
    assert validation['rejected'] == []
    damage = applied['appliedChanges'][0]['spellResolution']['targets'][0]['effects'][0]
    assert damage['concentrationCheck']['check']['rolls'] == [1]
    assert damage['concentrationCheck']['maintained'] is False
    participants = applied['nextState']['combat']['participants']
    assert participants[0]['activeEffects'] == []
    assert participants[1]['spellResources']['concentration'] is None
    assert 'effectTargetResources' not in applied['appliedChanges'][0]
    retried = apply_state_changes(applied['nextState'], validated_changes_for_application(validation))
    assert retried['nextState']['combat']['participants'][1]['hp'] == participants[1]['hp']


def test_area_spell_expansion_and_shared_damage_replay_in_a_turn(monkeypatch) -> None:
    state = _combat_state()
    actor = state['playerCharacters'][0]
    actor['level'] = 5
    actor['spellbook'] = normalize_spellbook({'knownSpells': [_spell('Fireball', 3)]}, class_name='Wizard')
    actor['spellResources'] = normalize_spell_resources(None, class_name='Wizard', level=5)
    state['combat']['participants'][0]['level'] = 5
    state['combat']['participants'][1]['spellResources'] = {'concentration': {
        'active': True, 'spellId': 'spell_hex', 'spellName': 'Hex',
        'casterActorId': 'enemy_goblin_1', 'targetIds': ['player_1'],
    }}
    extra = {**state['combat']['participants'][1], 'id': 'enemy_goblin_2', 'name': 'Second Goblin',
             'hp': {'current': 20, 'max': 20, 'temp': 0}, 'spellResources': {}}
    state['combat']['participants'].append(extra)
    monkeypatch.setattr('aidm_server.game_state.validation.validator.secrets.randbelow', lambda _sides: 0)
    declared, validation, applied = _run_intent(state, {
        'kind': 'spell', 'text': 'I cast Fireball at the goblin.', 'spell': {
            'name': 'Fireball', 'effect': 'A burst of fire.', 'target_ids': ['enemy_goblin_1'], 'cast_level': 3,
        },
    }, turn_id=28)
    assert declared['validatedActions'][0]['status'] == 'valid'
    assert validation['rejected'] == []
    resolution = applied['appliedChanges'][0]['spellResolution']
    assert resolution['targetIds'] == ['enemy_goblin_1', 'enemy_goblin_2']
    assert [target['effects'][0]['amountApplied'] for target in resolution['targets']] == [8, 8]
    assert resolution['targets'][0]['effects'][0]['concentrationCheck']['maintained'] is False
    assert resolution['targets'][1]['effects'][0]['sharedRollReused'] is True
    assert [participant['hp']['current'] for participant in applied['nextState']['combat']['participants'][1:]] == [12, 12]
    assert applied['nextState']['playerCharacters'][0]['spellResources']['slots']['3']['current'] == 1
    retried = apply_state_changes(applied['nextState'], validated_changes_for_application(validation))
    assert [participant['hp']['current'] for participant in retried['nextState']['combat']['participants'][1:]] == [12, 12]


def test_upcast_magic_missile_replays_each_dart_and_spends_selected_slot(monkeypatch) -> None:
    state = _combat_state()
    actor = state['playerCharacters'][0]
    actor['level'] = 3
    actor['spellResources'] = normalize_spell_resources(None, class_name='Wizard', level=3)
    state['combat']['participants'][0]['level'] = 3
    enemy = state['combat']['participants'][1]
    for number in range(2, 5):
        state['combat']['participants'].append({**enemy, 'id': f'enemy_goblin_{number}',
                                                'hp': {'current': 20, 'max': 20, 'temp': 0}})
    monkeypatch.setattr('aidm_server.game_state.validation.validator.secrets.randbelow', lambda _sides: 0)
    targets = [f'enemy_goblin_{number}' for number in range(1, 5)]
    declared, validation, applied = _run_intent(state, {
        'kind': 'spell', 'text': 'I cast Magic Missile at the goblins.', 'spell': {
            'name': 'Magic Missile', 'effect': 'Four force darts.', 'target_ids': targets, 'cast_level': 2,
        },
    }, turn_id=29)
    assert declared['validatedActions'][0]['status'] == 'valid'
    assert validation['rejected'] == []
    resolution = applied['appliedChanges'][0]['spellResolution']
    assert [target['targetId'] for target in resolution['targets']] == targets
    assert [target['effects'][0]['amountApplied'] for target in resolution['targets']] == [2, 2, 2, 2]
    assert applied['nextState']['playerCharacters'][0]['spellResources']['slots']['2']['current'] == 1


def test_upcast_scorching_ray_replays_all_attack_and_damage_rolls(monkeypatch) -> None:
    state = _combat_state()
    actor = state['playerCharacters'][0]
    actor['level'] = 5
    actor['spellbook'] = normalize_spellbook({'knownSpells': [_spell('Scorching Ray', 2)]}, class_name='Wizard')
    actor['spellResources'] = normalize_spell_resources(None, class_name='Wizard', level=5)
    state['combat']['participants'][0]['level'] = 5
    enemy = state['combat']['participants'][1]
    state['combat']['participants'].append({**enemy, 'id': 'enemy_goblin_2',
                                            'hp': {'current': 20, 'max': 20, 'temp': 0}})
    monkeypatch.setattr('aidm_server.game_state.validation.validator.secrets.randbelow', lambda sides: 9 if sides == 20 else 1)
    targets = ['enemy_goblin_1', 'enemy_goblin_2']
    declared, validation, applied = _run_intent(state, {
        'kind': 'spell', 'text': 'I cast Scorching Ray at the goblins.', 'spell': {
            'name': 'Scorching Ray', 'effect': 'Four rays of fire.', 'target_ids': targets, 'cast_level': 3,
        },
    }, turn_id=30)
    assert declared['validatedActions'][0]['status'] == 'valid'
    assert validation['rejected'] == []
    resolution = applied['appliedChanges'][0]['spellResolution']
    assert [target['targetId'] for target in resolution['targets']] == targets * 2
    assert [target['effects'][0]['amountApplied'] for target in resolution['targets']] == [4, 4, 4, 4]
    assert [participant['hp']['current'] for participant in applied['nextState']['combat']['participants'][1:]] == [12, 12]
    assert applied['nextState']['playerCharacters'][0]['spellResources']['slots']['3']['current'] == 1


def test_targeted_spell_stale_target_fails_without_spending_slot() -> None:
    state = _combat_state()
    declared, validation, applied = _run_intent(
        state,
        {
            'kind': 'spell',
            'text': 'I cast Magic Missile.',
            'spell': {
                'name': 'Magic Missile',
                'effect': 'Force darts strike.',
                'target_ids': ['enemy_missing'],
            },
        },
        turn_id=22,
    )

    assert declared['validatedActions'][0]['status'] == 'invalid'
    assert 'no longer part of this encounter' in declared['validatedActions'][0]['reason']
    assert validation['accepted'] == []
    assert applied['nextState']['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 2


def test_typed_spell_cast_consumes_slots_and_exhaustion_blocks_before_narration() -> None:
    state = _caster_state()

    first_declared, first_validation, first = _cast_magic_missile(state, 1)
    assert first_declared['validatedActions'][0]['status'] == 'valid'
    assert first_validation['rejected'] == []
    actor = first['nextState']['playerCharacters'][0]
    assert actor['spellResources']['slots']['1'] == {'current': 1, 'max': 2}

    _, second_validation, second = _cast_magic_missile(first['nextState'], 2)
    assert second_validation['rejected'] == []
    assert second['nextState']['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 0

    exhausted_declared, exhausted_validation, exhausted = _cast_magic_missile(second['nextState'], 3)
    assert exhausted_declared['validatedActions'][0]['status'] == 'invalid'
    assert 'No legal level 1 or higher spell resource remains' in exhausted_declared['validatedActions'][0]['reason']
    assert exhausted_validation['accepted'] == []
    assert exhausted['nextState']['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 0


def test_cast_change_is_idempotent_and_cantrip_tracks_concentration_without_a_slot() -> None:
    state = _caster_state()
    _, validation, cast = _cast_magic_missile(state, 8)
    accepted = validated_changes_for_application(validation)
    retried = apply_state_changes(cast['nextState'], accepted)

    assert cast['nextState']['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 1
    assert retried['nextState']['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 1
    assert retried['appliedChanges'] == []
    assert retried['skippedChanges'][0]['reason'] == 'State change was already applied.'

    _, cantrip_validation, cantrip = _run_intent(
        retried['nextState'],
        {
            'kind': 'spell',
            'text': 'I cast Dancing Lights.',
            'spell': {'name': 'Dancing Lights', 'effect': 'Four lights appear.'},
        },
        turn_id=9,
    )
    actor = cantrip['nextState']['playerCharacters'][0]
    assert cantrip_validation['rejected'] == []
    assert actor['spellResources']['slots']['1']['current'] == 1
    assert actor['spellResources']['concentration']['spellName'] == 'Dancing Lights'


def test_short_and_long_rest_restore_only_their_resources_and_preserve_consequences() -> None:
    state = _caster_state()
    _, _, first = _cast_magic_missile(state, 1)
    _, _, exhausted = _cast_magic_missile(first['nextState'], 2)

    _, short_validation, short_rest = _run_intent(
        exhausted['nextState'],
        {'kind': 'rest', 'text': 'I take a short rest.', 'rest_type': 'short_rest'},
        turn_id=3,
    )
    short_actor = short_rest['nextState']['playerCharacters'][0]
    assert short_validation['rejected'] == []
    assert short_actor['spellResources']['slots']['1']['current'] == 0
    # A short rest spends the wizard's one hit die (d6, CON +0) towards full hit points.
    hit_dice = short_validation['accepted'][0]['change']['hitDiceResolution']
    assert hit_dice['spent'] == 1 and 1 <= hit_dice['healing'] <= 6
    assert short_actor['hitDice'] == {'current': 0, 'max': 1, 'die': 'd6'}
    assert short_actor['health'] == {
        'currentHp': 3 + hit_dice['healing'],
        'maxHp': 10,
        'tempHp': 2,
        'conditions': ['poisoned'],
    }
    assert short_actor['raceAbilityState']['fey_step']['available'] is True
    assert short_actor['raceAbilityState']['daily_ward']['available'] is False

    _, long_validation, long_rest = _run_intent(
        short_rest['nextState'],
        {'kind': 'rest', 'text': 'I take a long rest.', 'rest_type': 'long_rest'},
        turn_id=4,
    )
    long_actor = long_rest['nextState']['playerCharacters'][0]
    assert long_validation['rejected'] == []
    assert long_actor['spellResources']['slots']['1'] == {'current': 2, 'max': 2}
    assert long_actor['health'] == {
        'currentHp': 10,
        'maxHp': 10,
        'tempHp': 0,
        'conditions': ['poisoned'],
    }
    assert long_actor['hitDice']['current'] == 1
    assert long_actor['raceAbilityState']['daily_ward']['available'] is True


def test_consumed_spell_resource_and_race_ability_state_survive_database_reload(app) -> None:
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        session_obj = db.session.get(Session, ids['session_id'])
        player.class_ = 'Wizard'
        player.level = 1
        state = _caster_state()
        actor = state['playerCharacters'][0]
        actor['id'] = display_actor_id(player.player_id)
        actor['playerId'] = player.player_id
        actor['name'] = player.character_name
        player.character_sheet = safe_json_dumps(
            {
                'spellbook': actor['spellbook'],
                'spellResources': actor['spellResources'],
            },
            {},
        )
        db.session.commit()

        state['sessionId'] = session_obj.session_id
        state['campaignId'] = ids['campaign_id']
        _, validation, cast = _run_intent(
            state,
            {
                'kind': 'spell',
                'text': 'I cast Magic Missile.',
                'spell': {'name': 'Magic Missile', 'effect': 'Force darts strike.'},
            },
            turn_id=11,
        )
        assert validation['rejected'] == []
        persisted_actor = cast['nextState']['playerCharacters'][0]
        persisted_actor['raceAbilityState']['fey_step']['available'] = False
        persist_state_to_database(
            session_obj=session_obj,
            state=cast['nextState'],
            players_by_id={player.player_id: player},
        )
        db.session.commit()
        db.session.expire_all()

        reloaded_player = db.session.get(Player, player.player_id)
        sheet = safe_json_loads(reloaded_player.character_sheet, {})
        stats = safe_json_loads(reloaded_player.stats, {})
        reloaded_actor = player_character_from_model(reloaded_player)
        snapshot = safe_json_loads(db.session.get(Session, session_obj.session_id).state_snapshot, {})

        assert sheet['spellResources']['slots']['1'] == {'current': 1, 'max': 2}
        assert reloaded_actor['spellResources']['slots']['1'] == {'current': 1, 'max': 2}
        assert reloaded_actor['raceAbilityState']['fey_step']['available'] is False
        assert snapshot['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 1
        assert stats['race_ability_state']['fey_step']['available'] is False


def test_warlock_pact_slot_returns_on_short_rest() -> None:
    state = _caster_state(class_name='Warlock')
    state['playerCharacters'][0]['spellbook'] = normalize_spellbook(
        {'knownSpells': [_spell('Hex', 1, concentration=True)]},
        class_name='Warlock',
    )
    _, _, cast = _run_intent(
        state,
        {
            'kind': 'spell',
            'text': 'I cast Hex.',
            'spell': {'name': 'Hex', 'effect': 'I curse the target.'},
        },
        turn_id=1,
    )
    assert cast['nextState']['playerCharacters'][0]['spellResources']['pactSlots']['current'] == 0

    _, _, rested = _run_intent(
        cast['nextState'],
        {'kind': 'rest', 'text': 'I take a short rest.', 'rest_type': 'short_rest'},
        turn_id=2,
    )
    assert rested['nextState']['playerCharacters'][0]['spellResources']['pactSlots'] == {
        'current': 1,
        'max': 1,
        'slotLevel': 1,
    }
