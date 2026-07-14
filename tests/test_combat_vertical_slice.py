from __future__ import annotations

from copy import deepcopy
import json

from aidm_server.database import db
import aidm_server.game_state.orchestration.turn_pipeline as turn_pipeline_module
from aidm_server.combat.legal_actions import (
    legal_combat_actions_for_player,
    resolve_combat_legal_action,
)
from aidm_server.combat.pipeline import (
    _consume_turn_combat_action,
    combat_turn_advance_change,
)
from aidm_server.combat.end_conditions import check_combat_end
from aidm_server.combat.state import (
    combat_summary_for_dm,
    combat_turn_context,
    combat_turn_order,
    default_turn_economy,
    normalize_combat_state,
)
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.orchestration.combat_resolution import (
    derive_trusted_damage_changes,
    resolve_authoritative_player_attack,
    without_trusted_damage_overlaps,
)
from aidm_server.models import Campaign, DmTurn, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from aidm_server.turn_engine import TurnCommand, TurnEngine
from tests.helpers import seed_world_campaign_player_session


def _participant(
    participant_id: str,
    team: str,
    *,
    name: str | None = None,
    hp: int = 20,
    dexterity: int = 10,
    player_id: int | None = None,
    conditions: list[str] | None = None,
    present: bool = True,
) -> dict:
    return {
        'id': participant_id,
        **({'playerId': player_id} if player_id is not None else {}),
        'name': name or participant_id,
        'team': team,
        'kind': 'player_character' if team == 'player' else 'creature',
        'hp': {'current': hp, 'max': hp, 'temp': 0},
        'armorClass': 12,
        'stats': {'dexterity': dexterity},
        'conditions': conditions or [],
        'position': {'rangeBand': 'near'},
        'isAlive': hp > 0,
        'isConscious': hp > 0,
        'isPresent': present,
    }


def _initiative(participant_ids: list[str]) -> list[dict]:
    return [
        {
            'participantId': participant_id,
            'name': participant_id,
            'roll': 20 - order,
            'modifier': 0,
            'total': 20 - order,
            'order': order,
            'source': 'test',
        }
        for order, participant_id in enumerate(participant_ids)
    ]


def _player(player_id: int, name: str) -> Player:
    return Player(
        player_id=player_id,
        name=f'Account {player_id}',
        character_name=name,
        class_='Fighter',
        level=3,
        stats=json.dumps({'strength': 16, 'dexterity': 14}),
        weapon_proficiencies=json.dumps([{'kind': 'weapon', 'value': 'longsword'}]),
        inventory=json.dumps(
            [
                {
                    'id': f'blade_{player_id}',
                    'name': 'Longsword',
                    'type': 'weapon',
                    'subtype': 'longsword',
                    'equipped': True,
                    'slot': 'main_hand',
                }
            ]
        ),
    )


def _combat_turn(turn_id: int, player_id: int, combat_action: dict) -> DmTurn:
    return DmTurn(
        turn_id=turn_id,
        player_id=player_id,
        metadata_json=json.dumps(
            {
                'action_intent': {
                    'kind': 'combat',
                    'combat': combat_action,
                }
            }
        ),
    )


def _apply_advance(state: dict, change: dict) -> dict:
    return apply_state_changes(state, [change])['nextState']


def test_server_initiative_is_explicit_deterministic_and_reload_stable():
    participants = [
        _participant('player_1', 'player', dexterity=14, player_id=1),
        _participant('enemy_wolf', 'enemy', dexterity=16),
        _participant('player_2', 'player', dexterity=12, player_id=2),
    ]
    raw = {
        'status': 'active',
        'round': 1,
        'turnIndex': 0,
        'participants': participants,
        'initiative': [],
        'flags': {'initiativeSeed': 'session-8:turn-21'},
    }

    first = normalize_combat_state(raw)
    reloaded = normalize_combat_state(json.loads(json.dumps(first)))

    assert first['initiative'] == reloaded['initiative']
    assert len(first['initiative']) == len(participants)
    assert all(entry['source'] == 'server_deterministic' for entry in first['initiative'])
    assert all(1 <= entry['roll'] <= 20 for entry in first['initiative'])
    assert [entry['participantId'] for entry in first['initiative']] == [
        entry['id'] for entry in combat_turn_order(first)
    ]


def test_legacy_combat_upgrades_to_explicit_initiative_without_reordering():
    raw = {
        'status': 'active',
        'round': 3,
        'turnIndex': 0,
        'participants': [
            _participant('player_1', 'player', player_id=1),
            _participant('enemy_1', 'enemy'),
        ],
    }

    normalized = normalize_combat_state(raw)

    assert [entry['participantId'] for entry in normalized['initiative']] == ['player_1', 'enemy_1']
    assert [entry['source'] for entry in normalized['initiative']] == [
        'legacy_roster_order',
        'legacy_roster_order',
    ]
    assert [entry['id'] for entry in combat_turn_order(normalized)] == ['player_1', 'enemy_1']


def test_turn_order_excludes_defeated_fled_surrendered_and_absent_actors():
    combat = normalize_combat_state(
        {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [
                _participant('enemy_fled', 'enemy', conditions=['fled']),
                _participant('enemy_surrendered', 'enemy', conditions=['surrendered']),
                _participant('enemy_absent', 'enemy', present=False),
                _participant('enemy_down', 'enemy', hp=0),
                _participant('player_1', 'player', player_id=1),
            ],
            'initiative': _initiative(
                ['enemy_fled', 'enemy_surrendered', 'enemy_absent', 'enemy_down', 'player_1']
            ),
        }
    )

    assert [entry['id'] for entry in combat_turn_order(combat)] == ['player_1']


def test_persisted_turn_economy_supports_action_then_move_then_handoff_and_round_reset():
    player_one = _player(1, 'Aric')
    player_two = _player(2, 'Borin')
    combat = normalize_combat_state(
        {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [
                _participant('player_1', 'player', name='Aric', player_id=1),
                _participant('enemy_wolf', 'enemy', name='Wolf'),
                _participant('player_2', 'player', name='Borin', player_id=2),
            ],
            'initiative': _initiative(['player_1', 'enemy_wolf', 'player_2']),
            'flags': {
                'activeActorId': 'player_1',
                'turnEconomy': default_turn_economy('player_1', 1),
            },
        }
    )
    snapshot = {'combat': combat}
    attack_action = next(
        action
        for action in legal_combat_actions_for_player(snapshot, player_one)['actions']
        if action['type'] == 'attack'
    )
    attack, error, _message = resolve_combat_legal_action(
        snapshot,
        player_one,
        action_id=attack_action['id'],
        target_id='enemy_wolf',
    )
    assert error is None and attack is not None
    attack_turn = _combat_turn(10, 1, attack)

    consumed, reason = _consume_turn_combat_action(combat, turn=attack_turn, actor_id='player_1')

    assert consumed is True and reason == ''
    assert combat['flags']['turnEconomy']['actionRemaining'] == 0
    assert combat_turn_advance_change(state={'combat': combat}, turn=attack_turn, actor_id='player_1') is None
    reloaded = json.loads(json.dumps({'combat': combat}))
    reloaded_bundle = legal_combat_actions_for_player(reloaded, player_one)
    assert reloaded_bundle is not None
    assert all(action['available'] is False for action in reloaded_bundle['actions'] if action['type'] == 'attack')
    assert all(action['type'] != 'defend' for action in reloaded_bundle['actions'])
    assert next(action for action in reloaded_bundle['actions'] if action['type'] == 'reposition')['available'] is True

    reposition, error, _message = resolve_combat_legal_action(
        reloaded,
        player_one,
        action_id='combat.reposition',
    )
    assert error is None and reposition is not None
    reposition_turn = _combat_turn(11, 1, reposition)
    consumed, reason = _consume_turn_combat_action(combat, turn=reposition_turn, actor_id='player_1')
    assert consumed is True and reason == ''
    assert combat['flags']['turnEconomy']['movementRemaining'] == 0
    second_reposition, error, message = resolve_combat_legal_action(
        {'combat': combat},
        player_one,
        action_id='combat.reposition',
    )
    assert second_reposition is None
    assert error == 'combat_action_unavailable'
    assert message == "This turn's movement is already spent."

    end_turn, error, _message = resolve_combat_legal_action(
        {'combat': combat},
        player_one,
        action_id='combat.end_turn',
    )
    assert error is None and end_turn is not None
    first_end_turn = _combat_turn(12, 1, end_turn)
    advance = combat_turn_advance_change(state={'combat': combat}, turn=first_end_turn, actor_id='player_1')
    assert advance is not None
    assert advance['turnIndex'] == 2
    assert advance['flags']['lastEnemyTurnBlock'] == ['enemy_wolf']
    assert advance['flags']['turnEconomy'] == default_turn_economy('player_2', 1)

    advanced_state = _apply_advance({'combat': combat}, advance)
    player_two_bundle = legal_combat_actions_for_player(advanced_state, player_two)
    assert player_two_bundle is not None and player_two_bundle['isCurrentActor'] is True
    player_two_end, error, _message = resolve_combat_legal_action(
        advanced_state,
        player_two,
        action_id='combat.end_turn',
    )
    assert error is None and player_two_end is not None
    second_advance = combat_turn_advance_change(
        state=advanced_state,
        turn=_combat_turn(13, 2, player_two_end),
        actor_id='player_2',
    )
    assert second_advance is not None
    assert second_advance['round'] == 2
    assert second_advance['flags']['turnEconomy'] == default_turn_economy('player_1', 2)


def _player_attack_state() -> dict:
    return {
        'playerCharacters': [{'id': 'player_1', 'name': 'Aric'}],
        'combat': {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [
                _participant('player_1', 'player', name='Aric', player_id=1),
                {
                    **_participant('enemy_wolf', 'enemy', name='Wolf', hp=12),
                    'armorClass': 13,
                },
            ],
            'initiative': _initiative(['player_1', 'enemy_wolf']),
            'flags': {'activeActorId': 'player_1'},
        },
    }


def _attack_intent() -> dict:
    return {
        'kind': 'combat',
        'combat': {
            'action_id': 'combat.attack.blade_1',
            'action_type': 'attack',
            'authoritative': True,
            'target_id': 'enemy_wolf',
            'target_name': 'Wolf',
            'weapon_id': 'blade_1',
            'weapon_name': 'Longsword',
            'damage_dice': '1d8',
            'damage_type': 'slashing',
            'economy': {'action': 1, 'endsTurn': False},
        },
    }


def _authoritative_roll(*, kept: int = 14, total: int = 19) -> dict:
    return {
        'authoritative': True,
        'kept': kept,
        'total': total,
        'modifier': total - kept,
        'modifier_breakdown': {'ability_modifier': 3, 'proficiency_bonus': 2, 'total': 5},
    }


def test_engine_owned_player_attack_applies_once_and_blocks_narrated_damage_override():
    state = _player_attack_state()
    resolved = resolve_authoritative_player_attack(
        state=state,
        actor_id='player_1',
        turn_id=44,
        action_intent=_attack_intent(),
        authoritative_roll=_authoritative_roll(),
        damage_roller=lambda _sides: 4,
    )

    assert resolved is not None
    assert resolved['hit'] is True
    assert resolved['attackTotal'] == 19
    assert resolved['damageRolls'] == [4]
    assert resolved['damageTotal'] == 7
    packet = {
        'combatState': {
            'playerResolvedAction': resolved,
            'trustedDamageEvents': [resolved],
        }
    }
    trusted = derive_trusted_damage_changes(
        state=state,
        dm_context_packet=packet,
        actor_id='player_1',
        turn_id=44,
        already_applied_changes=[],
    ).resolved

    assert len(trusted) == 1
    assert trusted[0]['type'] == 'combat.participant.update'
    assert trusted[0]['participantId'] == 'enemy_wolf'
    assert trusted[0]['hp'] == {'current': 5, 'max': 12, 'temp': 0}
    applied = apply_state_changes(state, trusted)['nextState']
    wolf = next(participant for participant in applied['combat']['participants'] if participant['id'] == 'enemy_wolf')
    assert wolf['hp']['current'] == 5

    narrated_override = [
        {
            'id': 'narration_invented_damage',
            'type': 'combat.participant.update',
            'participantId': 'enemy_wolf',
            'hp': {'current': 0, 'max': 12},
        }
    ]
    assert without_trusted_damage_overlaps(
        narrated_override,
        trusted,
        dm_context_packet=packet,
    ) == []
    assert derive_trusted_damage_changes(
        state=state,
        dm_context_packet=packet,
        actor_id='player_1',
        turn_id=44,
        already_applied_changes=trusted,
    ).resolved == []


def test_engine_owned_player_miss_cannot_be_rewritten_as_narrated_damage():
    state = _player_attack_state()
    resolved = resolve_authoritative_player_attack(
        state=state,
        actor_id='player_1',
        turn_id=45,
        action_intent=_attack_intent(),
        authoritative_roll=_authoritative_roll(kept=2, total=7),
        damage_roller=lambda _sides: 8,
    )
    assert resolved is not None and resolved['hit'] is False and resolved['damageTotal'] == 0
    packet = {'combatState': {'playerResolvedAction': resolved, 'trustedDamageEvents': [resolved]}}
    assert derive_trusted_damage_changes(
        state=state,
        dm_context_packet=packet,
        actor_id='player_1',
        turn_id=45,
        already_applied_changes=[],
    ).resolved == []
    narrated_damage = [
        {
            'id': 'narrated_hit_after_miss',
            'type': 'combat.participant.update',
            'participantId': 'enemy_wolf',
            'hp': {'current': 1, 'max': 12},
        }
    ]
    assert without_trusted_damage_overlaps(
        narrated_damage,
        [],
        dm_context_packet=packet,
    ) == []


def test_engine_owned_combat_end_cannot_be_rewritten_by_narration():
    packet = {
        'combatState': {'playerResolvedAction': {'authoritative': True, 'targetId': 'enemy_wolf'}},
        'trustedStateChanges': [
            {
                'id': 'engine_end',
                'type': 'combat.end',
                'status': 'ended',
                'endReason': 'all_enemies_defeated',
            }
        ],
    }

    assert without_trusted_damage_overlaps(
        [
            {
                'id': 'narrated_end',
                'type': 'combat.end',
                'status': 'ended',
                'endReason': 'negotiated_resolution',
            }
        ],
        [],
        dm_context_packet=packet,
    ) == []


def test_pre_dm_lethal_player_attack_persists_damage_and_combat_end_before_narration(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    actor_id = f"player_{ids['player_id']}"
    state = _player_attack_state()
    state['sessionId'] = ids['session_id']
    state['campaignId'] = ids['campaign_id']
    state['playerCharacters'][0]['id'] = actor_id
    state['combat']['participants'][0]['id'] = actor_id
    state['combat']['participants'][0]['playerId'] = ids['player_id']
    state['combat']['participants'][1]['hp'] = {'current': 1, 'max': 12, 'temp': 0}
    state['combat']['initiative'][0]['participantId'] = actor_id
    state['combat']['flags']['activeActorId'] = actor_id

    monkeypatch.setattr(
        turn_pipeline_module,
        'extract_pre_dm_actions',
        lambda **_kwargs: {'declaredActions': [], 'notes': ['combat_vertical_slice_test']},
    )
    monkeypatch.setattr(
        turn_pipeline_module,
        'prepare_combat_for_turn',
        lambda **_kwargs: {
            'changes': [],
            'debug': {},
            'combatContext': combat_turn_context(state['combat']),
        },
    )

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        session_obj = db.session.get(Session, ids['session_id'])
        campaign = db.session.get(Campaign, ids['campaign_id'])
        assert player is not None and session_obj is not None and campaign is not None
        player.stats = safe_json_dumps({'strength': 16, 'dexterity': 14}, {})
        player.inventory = safe_json_dumps(
            [{'id': 'blade_1', 'name': 'Longsword', 'type': 'weapon', 'equipped': True}],
            [],
        )
        session_obj.state_snapshot = safe_json_dumps(state, {})
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='Aric attacks Wolf with Longsword.',
            rule_type='attack',
            metadata_json=safe_json_dumps(
                {
                    'action_intent': _attack_intent(),
                    'authoritative_roll': _authoritative_roll(kept=18, total=23),
                },
                {},
            ),
        )
        db.session.add(turn)
        db.session.flush()

        result = turn_pipeline_module.pre_dm_pipeline(
            turn=turn,
            session_obj=session_obj,
            campaign=campaign,
            player=player,
            player_message=turn.player_input,
        )

        wolf = next(
            participant
            for participant in result['stateBeforeDm']['combat']['participants']
            if participant['id'] == 'enemy_wolf'
        )
        assert wolf['hp']['current'] == 0
        assert result['stateBeforeDm']['combat']['status'] == 'ended'
        assert result['stateBeforeDm']['combat']['flags']['endReason'] == 'all_enemies_defeated'
        assert [change['type'] for change in result['combatAppliedChanges']][-2:] == [
            'combat.participant.update',
            'combat.end',
        ]
        assert [change['type'] for change in result['dmContextPacket']['trustedStateChanges']] == [
            'combat.participant.update',
            'combat.end',
        ]
        persisted = safe_json_loads(session_obj.state_snapshot, {})
        assert persisted['combat']['status'] == 'ended'
        assert next(
            participant
            for participant in persisted['combat']['participants']
            if participant['id'] == 'enemy_wolf'
        )['hp']['current'] == 0


def test_player_projection_exposes_only_sanitized_turn_economy():
    snapshot = {
        'knownNpcs': [
            {
                'id': 'npc_guide',
                'name': 'Guide',
                'disposition': 'friendly',
                'relationship': {'score': 73, 'label': 'secretly compromised'},
            }
        ],
        'combat': {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [
                _participant('player_1', 'player', player_id=1),
                _participant('enemy_wolf', 'enemy'),
            ],
            'initiative': _initiative(['player_1', 'enemy_wolf']),
            'flags': {
                'activeActorId': 'player_1',
                'turnEconomy': {
                    **default_turn_economy('player_1', 1),
                    'actionRemaining': 0,
                    'spentActionIds': ['combat.attack.secret_weapon_identity'],
                    'enemyPlannerBudget': 99,
                },
                'combatDifficultyAI': {
                    'tacticalLevel': 'brutal',
                    'focusFireThreshold': 0.75,
                },
            },
        }
    }

    projected = filter_session_snapshot_for_player(deepcopy(snapshot), private_player_ids={1})
    economy = projected['combat']['flags']['turnEconomy']

    assert economy == {
        'version': 1,
        'round': 1,
        'actionRemaining': 0,
        'bonusActionRemaining': 1,
        'reactionRemaining': 1,
        'movementRemaining': 1,
        'actorId': 'player_1',
    }
    assert 'spentActionIds' not in economy
    assert 'enemyPlannerBudget' not in economy
    assert projected['knownNpcs'][0]['disposition'] == 'friendly'
    assert 'relationship' not in projected['knownNpcs'][0]
    assert 'combatDifficultyAI' not in projected['combat']['flags']


def test_active_actor_identity_survives_eligible_roster_compaction():
    combat = normalize_combat_state(
        {
            'status': 'active',
            'round': 1,
            'turnIndex': 1,
            'participants': [
                _participant('enemy_first', 'enemy', hp=0),
                _participant('player_1', 'player', player_id=1),
                _participant('enemy_later', 'enemy'),
            ],
            'initiative': _initiative(['enemy_first', 'player_1', 'enemy_later']),
            'flags': {
                'activeActorId': 'player_1',
                'turnEconomy': default_turn_economy('player_1', 1),
            },
        }
    )

    context = combat_turn_context(combat)

    assert context['turnOrderIds'] == ['player_1', 'enemy_later']
    assert context['turnIndex'] == 0
    assert context['currentActor']['id'] == 'player_1'


def test_chat_turn_does_not_resolve_enemy_actions_or_advance_combat():
    combat = normalize_combat_state(
        {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [
                _participant('player_1', 'player', player_id=1),
                {
                    **_participant('enemy_wolf', 'enemy'),
                    'abilities': [
                        {
                            'id': 'bite',
                            'name': 'Bite',
                            'type': 'attack',
                            'attackBonus': 4,
                            'damage': {'dice': '1d6+2', 'type': 'piercing'},
                        }
                    ],
                    'currentIntent': {
                        'intentType': 'attack',
                        'abilityId': 'bite',
                        'targetId': 'player_1',
                        'reason': 'Bite the hero.',
                    },
                },
            ],
            'initiative': _initiative(['player_1', 'enemy_wolf']),
            'flags': {
                'activeActorId': 'player_1',
                'submittedActorId': 'player_1',
                'turnEconomy': default_turn_economy('player_1', 1),
            },
        }
    )
    state = {'combat': combat, 'currentScene': {'combatState': 'active'}}
    turn = DmTurn(
        turn_id=81,
        session_id=1,
        campaign_id=1,
        player_id=1,
        player_input='What do I see?',
        metadata_json='{}',
    )
    deferred_reason = turn_pipeline_module._combat_enemy_deferred_reason(turn)
    packet = turn_pipeline_module._dm_context_packet(
        state=state,
        player_message=turn.player_input,
        pre_validation={},
        applied_changes=[],
        combat_context=combat_summary_for_dm(combat),
        enemy_deferred_reason=deferred_reason,
    )

    assert deferred_reason == 'player_turn_in_progress'
    assert packet['combatState']['enemyResolvedActions'] == []
    assert combat_turn_advance_change(state=state, turn=turn, actor_id='player_1') is None


def test_noncombat_gameplay_intent_fails_closed_during_active_combat(app):
    ids = seed_world_campaign_player_session(app)
    emitted = []
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        session = db.session.get(Session, ids['session_id'])
        assert player is not None and session is not None
        actor_id = f"player_{ids['player_id']}"
        session.state_snapshot = safe_json_dumps(
            {
                'combat': {
                    'status': 'active',
                    'round': 1,
                    'turnIndex': 0,
                    'participants': [
                        _participant(actor_id, 'player', player_id=ids['player_id']),
                        _participant('enemy_wolf', 'enemy'),
                    ],
                    'initiative': _initiative([actor_id, 'enemy_wolf']),
                    'flags': {'activeActorId': actor_id},
                }
            },
            {},
        )
        engine = object.__new__(TurnEngine)
        engine.emit = lambda event, payload: emitted.append((event, payload))
        command = TurnCommand(
            sid='test-sid',
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            world_id=ids['world_id'],
            player_id=ids['player_id'],
            user_input='I use the potion.',
            manual_segment_ids=set(),
            action_intent={
                'kind': 'item',
                'item': {'id': 'potion', 'name': 'Potion', 'quantity': 1},
                'inventory_action': 'use',
            },
        )

        assert engine._prepare_combat_action(command, player, session) is False

    assert emitted[0][0] == 'error'
    assert emitted[0][1]['error_code'] == 'combat_action_required'


def test_all_players_fled_has_distinct_combat_end_reason():
    fled = {
        'status': 'active',
        'participants': [
            _participant('player_1', 'player', conditions=['fled']),
            _participant('enemy_wolf', 'enemy'),
        ],
    }
    defeated = {
        'status': 'active',
        'participants': [
            _participant('player_1', 'player', hp=0),
            _participant('enemy_wolf', 'enemy'),
        ],
    }

    assert check_combat_end(fled) == 'players_fled'
    assert check_combat_end(defeated) == 'objective_failed'


def test_player_projection_filters_hidden_battlefield_records():
    snapshot = {
        'combat': {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [_participant('player_1', 'player', player_id=1)],
            'battlefield': {
                'hazards': [
                    {'id': 'public_fire', 'name': 'Fire', 'effect': 'burning'},
                    {'id': 'secret_pit', 'name': 'Secret Pit', 'hiddenToPlayers': True},
                ],
                'exits': [
                    {'id': 'public_door', 'name': 'Door'},
                    {'id': 'secret_door', 'name': 'Secret Door', 'visibility': 'dm_only'},
                ],
                'interactables': [
                    {'id': 'public_crank', 'name': 'Crank'},
                    {'id': 'secret_lever', 'name': 'Secret Lever', 'dmOnly': True},
                ],
            },
        }
    }

    battlefield = filter_session_snapshot_for_player(snapshot, private_player_ids={1})['combat']['battlefield']

    assert [record['id'] for record in battlefield['hazards']] == ['public_fire']
    assert [record['id'] for record in battlefield['exits']] == ['public_door']
    assert [record['id'] for record in battlefield['interactables']] == ['public_crank']


def test_enemy_block_follows_explicit_initiative_instead_of_team_sorting():
    combat = normalize_combat_state(
        {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': [
                _participant('player_1', 'player', player_id=1),
                _participant('player_2', 'player', player_id=2),
                _participant('enemy_fast', 'enemy'),
                _participant('enemy_slow', 'enemy'),
            ],
            'initiative': _initiative(['enemy_fast', 'player_1', 'enemy_slow', 'player_2']),
        }
    )

    context = combat_turn_context(combat)

    assert [entry['id'] for entry in context['turnOrder']] == [
        'enemy_fast',
        'player_1',
        'enemy_slow',
        'player_2',
    ]
    assert [entry['id'] for entry in context['enemyTurnBlock']] == ['enemy_fast']
    assert context['handoffActor']['id'] == 'player_1'


def _spell_change(change_id: str = 'cast_magic_missile', *, actor_id: str = 'player_1') -> dict:
    return {
        'id': change_id,
        'turnId': 80,
        'type': 'spell.cast',
        'actorId': actor_id,
        'spellId': 'spell_magic_missile',
        'spellName': 'Magic Missile',
        'castLevel': 1,
        'resourcePool': 'slots',
        'visible': False,
    }


def test_active_combat_spell_reserves_one_action_and_does_not_advance_the_turn():
    state = _player_attack_state()
    allowed, rejected = turn_pipeline_module._gate_pre_dm_combat_spells(
        state=state,
        changes=[_spell_change()],
        actor_id='player_1',
        turn_id=80,
    )

    assert rejected == []
    assert [change['type'] for change in allowed] == ['spell.cast', 'combat.update']
    assert allowed[1]['flags']['turnEconomy']['actionRemaining'] == 0
    assert allowed[1]['flags']['turnEconomy']['spentActionIds'] == ['cast_magic_missile']
    applied = apply_state_changes(state, [allowed[1]])['nextState']
    assert applied['combat']['flags']['turnEconomy']['actionRemaining'] == 0

    spell_turn = DmTurn(
        turn_id=80,
        player_id=1,
        metadata_json=safe_json_dumps({'action_intent': {'kind': 'spell'}}, {}),
    )
    assert combat_turn_advance_change(state=applied, turn=spell_turn, actor_id='player_1') is None
    assert turn_pipeline_module._combat_enemy_deferred_reason(spell_turn) == 'player_turn_in_progress'


def test_active_combat_spell_is_rejected_off_turn_or_after_action_is_spent():
    state = _player_attack_state()
    off_turn_allowed, off_turn_rejected = turn_pipeline_module._gate_pre_dm_combat_spells(
        state=state,
        changes=[_spell_change(actor_id='player_2')],
        actor_id='player_2',
        turn_id=81,
    )
    assert off_turn_allowed == []
    assert 'cannot be cast off-turn' in off_turn_rejected[0]['reason']

    state['combat']['flags']['turnEconomy'] = {
        **default_turn_economy('player_1', 1),
        'actionRemaining': 0,
    }
    spent_allowed, spent_rejected = turn_pipeline_module._gate_pre_dm_combat_spells(
        state=state,
        changes=[_spell_change()],
        actor_id='player_1',
        turn_id=82,
    )
    assert spent_allowed == []
    assert spent_rejected[0]['reason'] == "This turn's action is already spent."

    fresh_state = _player_attack_state()
    duplicate_allowed, duplicate_rejected = turn_pipeline_module._gate_pre_dm_combat_spells(
        state=fresh_state,
        changes=[_spell_change('cast_one'), _spell_change('cast_two')],
        actor_id='player_1',
        turn_id=83,
    )
    assert [change['type'] for change in duplicate_allowed] == ['spell.cast', 'combat.update']
    assert duplicate_rejected[0]['reason'] == "This turn's action is already spent."


def test_post_dm_cannot_replace_authoritative_travel_or_duplicate_immediate_transactions():
    applied = [
        {
            'id': 'travel_ruins',
            'type': 'scene.move_location',
            'actorId': 'player_1',
            'locationId': 'ruins',
        },
        _spell_change(),
        {
            'id': 'rest_short',
            'type': 'rest.complete',
            'actorId': 'player_1',
            'restType': 'short_rest',
        },
        {
            'id': 'scene_remove_torch',
            'type': 'scene.item.remove',
            'actorId': 'player_1',
            'itemId': 'torch_1',
            'itemName': 'Torch',
            'quantity': 1,
        },
    ]
    proposed = [
        {
            'id': 'stale_travel',
            'type': 'scene.move_location',
            'actorId': 'player_1',
            'locationId': 'old_ruins',
        },
        {**_spell_change('narrated_second_spell'), 'spellName': 'Shield'},
        {
            'id': 'narrated_long_rest',
            'type': 'rest.complete',
            'actorId': 'player_1',
            'restType': 'long_rest',
        },
        {
            'id': 'duplicate_scene_remove',
            'type': 'scene.item.remove',
            'actorId': 'player_1',
            'itemId': 'torch_1',
            'itemName': 'Torch',
            'quantity': 1,
        },
        {
            'id': 'different_scene_remove',
            'type': 'scene.item.remove',
            'actorId': 'player_1',
            'itemId': 'rope_1',
            'itemName': 'Rope',
            'quantity': 1,
        },
    ]

    assert turn_pipeline_module._without_applied_immediate_overlaps(proposed, applied) == [
        proposed[-1]
    ]
