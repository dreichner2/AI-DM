from __future__ import annotations

import json
import secrets

import pytest

from aidm_server.action_intent import validate_action_intent
from aidm_server.combat.legal_actions import legal_combat_actions_for_player, resolve_combat_legal_action
from aidm_server.combat.modifiers import save_context
from aidm_server.combat.pipeline import apply_turn_boundaries
from aidm_server.combat.state import player_combat_participant
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.extraction.pre_dm_action_extractor import extract_pre_dm_actions
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.item_effects import item_effect_descriptor, item_effect_id
from aidm_server.models import Player
from aidm_server.starting_inventory import starting_inventory_for_class


POTION = {
    'id': 'potion_1',
    'name': 'Potion of Healing',
    'quantity': 2,
    'type': 'potion',
    'aliases': ['Healing Potion'],
    'metadata': {'effectId': 'potion_healing'},
}


def _dice(monkeypatch: pytest.MonkeyPatch, *values: int) -> None:
    """Server dice come from ``secrets``; feed them in order."""

    rolls = iter(values)
    monkeypatch.setattr(secrets, 'randbelow', lambda sides: next(rolls) - 1)


def _actor(actor_id: str, name: str, *, class_name: str, hp: int, items: list[dict], conditions: list[str] | None = None) -> dict:
    return {
        'id': actor_id,
        'playerId': int(actor_id.removeprefix('player_')),
        'name': name,
        'class': class_name,
        'level': 3,
        'stats': {'strength': 10, 'dexterity': 14, 'constitution': 14, 'intelligence': 10, 'wisdom': 16, 'charisma': 10},
        'health': {'currentHp': hp, 'maxHp': 20, 'tempHp': 0, 'conditions': list(conditions or [])},
        'inventory': {'items': [dict(item) for item in items], 'currency': {'gp': 0}},
        'xp': {'current': 0},
    }


def _state(*items: dict, class_name: str = 'Cleric', ally_hp: int = 20, ally_conditions: list[str] | None = None) -> dict:
    return {
        'currentScene': {'locationId': 'camp', 'name': 'Camp', 'combatState': 'none', 'activeNpcIds': []},
        'activePlayerIds': [1, 2],
        'playerCharacters': [
            _actor('player_1', 'Aria', class_name=class_name, hp=5, items=list(items) or [POTION]),
            _actor('player_2', 'Bram', class_name='Fighter', hp=ally_hp, items=[], conditions=ally_conditions),
        ],
        'flags': {},
        'stateChangeLedger': [],
    }


def _participant(participant_id: str, name: str, team: str, *, hp: int, band: str = 'near', **extra) -> dict:
    return {
        'id': participant_id,
        'name': name,
        'team': team,
        'kind': 'player_character' if team == 'player' else 'creature',
        'level': 3,
        'stats': {'strength': 10, 'dexterity': 14, 'constitution': 14, 'wisdom': 16},
        'hp': {'current': hp, 'max': 20, 'temp': 0},
        'armorClass': 12,
        'conditions': list(extra.pop('conditions', [])),
        'position': {'rangeBand': band},
        'isAlive': True,
        'isConscious': hp > 0,
        **extra,
    }


def _combat_state(*items: dict, class_name: str = 'Cleric', ally_hp: int = 20, ally_conditions: list[str] | None = None, ally_band: str = 'near') -> dict:
    state = _state(*items, class_name=class_name, ally_hp=ally_hp, ally_conditions=ally_conditions)
    state['currentScene']['combatState'] = 'active'
    state['combat'] = {
        'status': 'active',
        'round': 1,
        'turnIndex': 0,
        'participants': [
            _participant('player_1', 'Aria', 'player', hp=5, playerId=1),
            _participant('player_2', 'Bram', 'player', hp=ally_hp, band=ally_band, playerId=2, conditions=list(ally_conditions or [])),
            _participant('enemy_skeleton', 'Skeleton', 'enemy', hp=13, creatureType='undead'),
            _participant('enemy_goblin', 'Goblin', 'enemy', hp=7, creatureType='humanoid'),
        ],
        'initiative': [
            {'participantId': 'player_1', 'total': 20, 'order': 0},
            {'participantId': 'player_2', 'total': 15, 'order': 1},
            {'participantId': 'enemy_skeleton', 'total': 10, 'order': 2},
            {'participantId': 'enemy_goblin', 'total': 5, 'order': 3},
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


def _player(state: dict) -> Player:
    actor = state['playerCharacters'][0]
    return Player(
        player_id=1,
        character_name=actor['name'],
        class_=actor['class'],
        level=3,
        stats=json.dumps({'dexterity': 14}),
        inventory=json.dumps(actor['inventory']['items']),
    )


def _run(state: dict, intent: dict, *, turn_id: int, message: str = 'Do it.') -> tuple[dict, dict, dict]:
    extraction = extract_pre_dm_actions(
        current_state=state,
        player_message=message,
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
    validation = validate_state_changes(state=state, changes=declared['immediateChanges'], expected_actor_id='player_1')
    applied = apply_state_changes(state, validated_changes_for_application(validation))
    return declared, validation, applied


def _use_item(state: dict, item_id: str, *, turn_id: int, target_id: str | None = None) -> tuple[dict, dict, dict]:
    intent, error = validate_action_intent(
        {
            'kind': 'item',
            'inventory_action': 'use',
            'item': {'id': item_id, 'name': 'item', **({'target_id': target_id} if target_id else {})},
        }
    )
    assert error is None
    item = next(item for item in state['playerCharacters'][0]['inventory']['items'] if item['id'] == item_id)
    intent['item']['name'] = item['name']
    return _run(state, intent, turn_id=turn_id)


def _hud_use(state: dict, item_id: str, *, turn_id: int, target_id: str) -> tuple[dict, dict, dict]:
    intent, error = validate_action_intent(
        {'kind': 'combat', 'combat': {'action_id': f'item.use.{item_id}', 'target_id': target_id}}
    )
    assert error is None
    canonical, code, message = resolve_combat_legal_action(
        state,
        _player(state),
        action_id=intent['combat']['action_id'],
        target_id=target_id,
    )
    assert code is None, message
    intent['combat'] = canonical
    return _run(state, intent, turn_id=turn_id, message=canonical['message'])


def _items(state: dict, actor_index: int = 0) -> dict[str, dict]:
    return {item['id']: item for item in state['playerCharacters'][actor_index]['inventory']['items']}


def _participant_by_id(state: dict, participant_id: str) -> dict:
    return next(entry for entry in state['combat']['participants'] if entry['id'] == participant_id)


def test_catalog_matches_effect_ids_names_aliases_and_scrolls() -> None:
    assert item_effect_id({'name': 'Healing Potion'}) == 'potion_healing'
    assert item_effect_id({'name': 'Red Vial', 'metadata': {'effectId': 'potion_greater_healing'}}) == 'potion_greater_healing'
    assert item_effect_id({'name': "healer's kit"}) == 'healers_kit'
    assert item_effect_id({'name': 'Vial of Acid'}) == 'acid_vial'
    assert item_effect_id({'name': 'Scroll of Cure Wounds'}) == 'spell_scroll'
    assert item_effect_id({'name': 'Odd Paper', 'metadata': {'spellId': 'spell_fire_bolt'}}) == 'spell_scroll'
    assert item_effect_id({'name': 'Rope'}) is None
    assert item_effect_descriptor({'name': 'Potion of Healing'})['cost'] == 'bonus_action'


def test_potion_heals_out_of_combat_removes_one_and_is_replay_safe(monkeypatch) -> None:
    _dice(monkeypatch, 3, 2)
    state = _state()
    declared, validation, applied = _use_item(state, 'potion_1', turn_id=4)

    change = declared['immediateChanges'][0]
    assert change['type'] == 'item.use'
    assert change['resolution']['rolls'] == [3, 2]
    assert change['resolution']['healing']['total'] == 7
    assert change['resolution']['cost'] is None
    assert validation['rejected'] == []
    actor = applied['nextState']['playerCharacters'][0]
    assert actor['health']['currentHp'] == 12
    assert _items(applied['nextState'])['potion_1']['quantity'] == 1
    assert applied['appliedChanges'][0]['actualAmount'] == 7

    replayed = apply_state_changes(applied['nextState'], validated_changes_for_application(validation))
    assert replayed['appliedChanges'] == []
    assert replayed['nextState']['playerCharacters'][0]['health']['currentHp'] == 12
    assert _items(replayed['nextState'])['potion_1']['quantity'] == 1
    revalidated = validate_state_changes(state=applied['nextState'], changes=declared['immediateChanges'])
    assert revalidated['rejected'][0]['reason'] == 'State change was already applied.'


def test_item_use_accepts_an_exact_id_without_a_client_name(monkeypatch) -> None:
    _dice(monkeypatch, 3, 2)
    intent, error = validate_action_intent(
        {'kind': 'item', 'inventory_action': 'use', 'item': {'id': 'potion_1'}}
    )
    assert error is None
    declared, validation, applied = _run(_state(), intent, turn_id=5)
    assert declared['validatedActions'][0]['status'] == 'valid'
    assert validation['rejected'] == []
    assert applied['nextState']['playerCharacters'][0]['health']['currentHp'] == 12
    assert _items(applied['nextState'])['potion_1']['quantity'] == 1


def test_tampered_item_result_is_rejected(monkeypatch) -> None:
    _dice(monkeypatch, 1, 1)
    state = _state()
    declared, _validation, _applied = _use_item(state, 'potion_1', turn_id=4)
    forged = json.loads(json.dumps(declared['immediateChanges'][0]))
    forged['resolution']['healing']['total'] = 99
    assert validate_state_changes(state=state, changes=[forged])['rejected'][0]['reason'] == (
        'Item result does not match the authoritative roll replay.'
    )
    narrated = {**declared['immediateChanges'][0], 'source': 'post_dm'}
    assert 'before narration' in validate_state_changes(state=state, changes=[narrated])['rejected'][0]['reason']


def test_potion_costs_a_bonus_action_on_self_and_an_action_for_an_ally(monkeypatch) -> None:
    state = _combat_state(ally_hp=0, ally_conditions=['unconscious', 'dying'])
    bundle = legal_combat_actions_for_player(state, _player(state))
    potion = next(action for action in bundle['actions'] if action['id'] == 'item.use.potion_1')
    assert potion['type'] == 'item'
    assert potion['available'] is True
    assert potion['cost'] == {'action': 0, 'bonusAction': 1, 'movement': 0, 'reaction': 0}
    targets = {target['id']: target for target in potion['targets']}
    assert targets['player_1']['cost'] == 'bonus_action'
    assert targets['player_2']['cost'] == 'action'
    assert targets['player_2']['available'] is True
    assert 'enemy_goblin' not in targets

    _dice(monkeypatch, 2, 2, 4, 4)
    _declared, validation, drank = _hud_use(state, 'potion_1', turn_id=5, target_id='player_1')
    assert validation['rejected'] == []
    economy = drank['nextState']['combat']['flags']['turnEconomy']
    assert (economy['bonusActionRemaining'], economy['actionRemaining']) == (0, 1)
    assert _participant_by_id(drank['nextState'], 'player_1')['hp']['current'] == 11

    _declared, validation, gave = _hud_use(drank['nextState'], 'potion_1', turn_id=6, target_id='player_2')
    assert validation['rejected'] == []
    ally = _participant_by_id(gave['nextState'], 'player_2')
    assert ally['hp']['current'] == 10
    assert 'dying' not in ally['conditions'] and ally['isConscious'] is True
    assert gave['nextState']['playerCharacters'][1]['health']['currentHp'] == 10
    assert gave['nextState']['combat']['flags']['turnEconomy']['actionRemaining'] == 0
    assert 'potion_1' not in _items(gave['nextState'])

    bundle = legal_combat_actions_for_player(gave['nextState'], _player(gave['nextState']))
    assert all(not action['id'].startswith('item.use.') for action in bundle['actions'])


def test_item_use_is_offered_but_unavailable_off_turn_and_out_of_reach() -> None:
    state = _combat_state(ally_hp=0, ally_conditions=['unconscious', 'dying'], ally_band='distant')
    state['combat']['flags']['turnEconomy']['bonusActionRemaining'] = 0
    bundle = legal_combat_actions_for_player(state, _player(state))
    potion = next(action for action in bundle['actions'] if action['id'] == 'item.use.potion_1')
    targets = {target['id']: target for target in potion['targets']}
    assert targets['player_1']['reason'] == "This turn's bonus action is already spent."
    assert 'same or an adjacent range band' in targets['player_2']['reason']
    assert potion['available'] is False

    state = _combat_state()
    state['combat']['turnIndex'] = 1
    state['combat']['flags']['activeActorId'] = 'player_2'
    bundle = legal_combat_actions_for_player(state, _player(state))
    potion = next(action for action in bundle['actions'] if action['id'] == 'item.use.potion_1')
    assert potion['available'] is False
    assert potion['reason'] == 'Bram is acting now.'


def test_dead_target_cannot_be_given_a_potion() -> None:
    state = _state(ally_hp=0, ally_conditions=['dead'])
    declared, _validation, applied = _use_item(state, 'potion_1', turn_id=4, target_id='player_2')
    assert declared['validatedActions'][0]['status'] == 'invalid'
    assert declared['validatedActions'][0]['reason'] == 'Healing cannot restore the dead.'
    assert _items(applied['nextState'])['potion_1']['quantity'] == 2


def test_antitoxin_ends_poison_and_grants_con_save_advantage_for_the_encounter() -> None:
    antitoxin = {'id': 'antitoxin_1', 'name': 'Antitoxin', 'quantity': 1, 'type': 'consumable'}
    state = _combat_state(antitoxin)
    _participant_by_id(state, 'player_1')['conditions'] = ['poisoned']

    _declared, validation, applied = _hud_use(state, 'antitoxin_1', turn_id=5, target_id='player_1')
    assert validation['rejected'] == []
    participant = _participant_by_id(applied['nextState'], 'player_1')
    assert 'poisoned' not in participant['conditions']
    assert save_context(participant, 'constitution')['mode'] == 'advantage'
    assert save_context(participant, 'dexterity')['mode'] == 'normal'
    assert applied['nextState']['combat']['flags']['turnEconomy']['actionRemaining'] == 0
    assert 'antitoxin_1' not in _items(applied['nextState'])


def test_antitoxin_out_of_combat_lasts_until_the_next_rest() -> None:
    state = _state({'id': 'antitoxin_1', 'name': 'Antitoxin', 'quantity': 1, 'type': 'consumable'})
    state['playerCharacters'][0]['health']['conditions'] = ['poisoned']
    _declared, _validation, applied = _use_item(state, 'antitoxin_1', turn_id=4)
    actor = applied['nextState']['playerCharacters'][0]
    assert actor['health']['conditions'] == []
    assert actor['activeEffects'][0]['expiresOn'] == 'rest'
    assert save_context(actor, 'constitution')['mode'] == 'advantage'
    participant = player_combat_participant(actor)
    assert save_context(participant, 'constitution')['mode'] == 'advantage'
    participant['activeEffects'].clear()
    assert save_context(actor, 'constitution')['mode'] == 'advantage'

    rest_intent, _error = validate_action_intent({'kind': 'rest', 'rest_type': 'long_rest'})
    _declared, _validation, rested = _run(applied['nextState'], rest_intent, turn_id=5)
    assert rested['nextState']['playerCharacters'][0]['activeEffects'] == []


def test_healers_kit_stabilises_a_dying_ally_one_use_at_a_time() -> None:
    kit = {'id': 'kit_1', 'name': "Healer's Kit", 'quantity': 1, 'type': 'tool', 'metadata': {'usesRemaining': 2}}
    state = _combat_state(kit, ally_hp=0, ally_conditions=['unconscious', 'dying'])

    _declared, validation, applied = _hud_use(state, 'kit_1', turn_id=5, target_id='player_2')
    assert validation['rejected'] == []
    ally = _participant_by_id(applied['nextState'], 'player_2')
    assert 'stable' in ally['conditions'] and 'dying' not in ally['conditions']
    assert ally['hp']['current'] == 0
    assert _items(applied['nextState'])['kit_1']['metadata']['usesRemaining'] == 1
    assert applied['nextState']['combat']['flags']['turnEconomy']['actionRemaining'] == 0

    not_dying = legal_combat_actions_for_player(applied['nextState'], _player(applied['nextState']))
    kit_action = next(action for action in not_dying['actions'] if action['id'] == 'item.use.kit_1')
    assert {target['id']: target['reason'] for target in kit_action['targets']}['player_2'] in {
        'Not dying.',
        "This turn's action is already spent.",
    }

    last_use = _state(dict(kit, metadata={'usesRemaining': 1}), ally_hp=0, ally_conditions=['unconscious', 'dying'])
    _declared, _validation, spent = _use_item(last_use, 'kit_1', turn_id=6, target_id='player_2')
    assert 'kit_1' not in _items(spent['nextState'])
    assert 'stable' in spent['nextState']['playerCharacters'][1]['health']['conditions']


def test_holy_water_harms_only_undead_and_fiends(monkeypatch) -> None:
    holy_water = {'id': 'holy_1', 'name': 'Holy Water', 'quantity': 2, 'type': 'consumable'}
    state = _combat_state(holy_water)

    _dice(monkeypatch, 15, 4, 5)
    declared, validation, applied = _hud_use(state, 'holy_1', turn_id=5, target_id='enemy_skeleton')
    resolution = declared['immediateChanges'][0]['resolution']
    assert resolution['attack']['total'] == 15 + 2 + 2
    assert resolution['attack']['hit'] is True
    assert resolution['damage']['total'] == 9
    assert validation['rejected'] == []
    assert _participant_by_id(applied['nextState'], 'enemy_skeleton')['hp']['current'] == 4
    assert applied['nextState']['combat']['flags']['turnEconomy']['actionRemaining'] == 0

    _dice(monkeypatch, 18)
    fresh = _combat_state(holy_water)
    declared, _validation, applied = _hud_use(fresh, 'holy_1', turn_id=6, target_id='enemy_goblin')
    resolution = declared['immediateChanges'][0]['resolution']
    assert resolution['attack']['hit'] is True
    assert resolution['damage'] == {'type': 'radiant', 'total': 0, 'unaffected': True}
    assert _participant_by_id(applied['nextState'], 'enemy_goblin')['hp']['current'] == 7
    assert _items(applied['nextState'])['holy_1']['quantity'] == 1


def test_alchemists_fire_burns_and_acid_corrodes(monkeypatch) -> None:
    fire = {'id': 'fire_1', 'name': "Alchemist's Fire", 'quantity': 1, 'type': 'consumable'}
    acid = {'id': 'acid_1', 'name': 'Acid', 'quantity': 1, 'type': 'consumable'}
    state = _combat_state(fire, acid)

    _dice(monkeypatch, 12, 3)
    declared, _validation, applied = _hud_use(state, 'fire_1', turn_id=5, target_id='enemy_skeleton')
    assert declared['immediateChanges'][0]['resolution']['damage']['total'] == 3
    skeleton = _participant_by_id(applied['nextState'], 'enemy_skeleton')
    assert skeleton['hp']['current'] == 10
    burning = skeleton['activeEffects'][0]
    assert burning['modifiers']['damagePerTurn'] == {'dice': '1d4', 'type': 'fire'}
    assert burning['duration'] == {'rounds': 2, 'tickOn': 'target_turn_end'}

    monkeypatch.setattr('aidm_server.combat.pipeline.deterministic_roller', lambda _seed: lambda _sides: 2)
    combat = applied['nextState']['combat']
    for round_number in (1, 2):
        events = apply_turn_boundaries(combat, [
            {'actorId': 'enemy_skeleton', 'phase': 'start', 'round': round_number},
            {'actorId': 'enemy_skeleton', 'phase': 'end', 'round': round_number},
        ], turn_id=round_number + 5)
        assert events[0]['ongoingDamage'][0]['damage'] == 2
    assert skeleton['hp']['current'] == 6
    assert skeleton['activeEffects'] == []
    events = apply_turn_boundaries(combat, [{'actorId': 'enemy_skeleton', 'phase': 'start', 'round': 3}], turn_id=8)
    assert 'ongoingDamage' not in events[0]

    _dice(monkeypatch, 1)
    declared, _validation, missed = _hud_use(_combat_state(fire, acid), 'acid_1', turn_id=6, target_id='enemy_goblin')
    assert declared['immediateChanges'][0]['resolution']['attack']['hit'] is False
    assert _participant_by_id(missed['nextState'], 'enemy_goblin')['hp']['current'] == 7
    assert 'acid_1' not in _items(missed['nextState'])

    _dice(monkeypatch, 20, 6, 6, 1, 1)
    declared, _validation, crit = _hud_use(_combat_state(fire, acid), 'acid_1', turn_id=7, target_id='enemy_goblin')
    resolution = declared['immediateChanges'][0]['resolution']
    assert resolution['attack']['critical'] is True
    assert resolution['damage']['rolls'] == [6, 6, 1, 1]
    goblin = _participant_by_id(crit['nextState'], 'enemy_goblin')
    assert goblin['hp']['current'] == 0 and goblin['isAlive'] is False


def test_thrown_items_need_an_encounter() -> None:
    state = _state({'id': 'acid_1', 'name': 'Acid', 'quantity': 1, 'type': 'consumable'})
    declared, _validation, _applied = _use_item(state, 'acid_1', turn_id=4)
    assert declared['validatedActions'][0]['reason'] == 'Requires an active encounter.'


def test_spell_scroll_casts_for_a_class_that_has_the_spell_and_is_consumed(monkeypatch) -> None:
    scroll = {'id': 'scroll_1', 'name': 'Scroll of Cure Wounds', 'quantity': 1, 'type': 'scroll'}
    state = _combat_state(scroll, ally_hp=4, ally_band='melee')
    _participant_by_id(state, 'player_1')['position'] = {'rangeBand': 'melee'}

    _dice(monkeypatch, 6)
    declared, validation, applied = _hud_use(state, 'scroll_1', turn_id=5, target_id='player_2')
    assert validation['rejected'] == []
    resolution = declared['immediateChanges'][0]['resolution']
    assert resolution['rolls'] == [6]
    assert resolution['spell']['name'] == 'Cure Wounds'
    assert _participant_by_id(applied['nextState'], 'player_2')['hp']['current'] == 10
    assert 'scroll_1' not in _items(applied['nextState'])
    assert applied['nextState']['combat']['flags']['turnEconomy']['actionRemaining'] == 0
    assert applied['nextState']['playerCharacters'][0].get('spellResources') is None

    wizard = _combat_state(scroll, class_name='Wizard')
    bundle = legal_combat_actions_for_player(wizard, _player(wizard))
    action = next(action for action in bundle['actions'] if action['id'] == 'item.use.scroll_1')
    assert action['available'] is False
    assert action['reason'] == "Cure Wounds is not on your class's spell list."


def test_free_text_drink_resolves_a_single_catalog_item(app, monkeypatch) -> None:
    _dice(monkeypatch, 4, 4)
    state = _state()
    with app.app_context():
        declared, validation, applied = _run(state, None, turn_id=4, message='I drink my healing potion.')
    assert declared['validatedActions'][0]['status'] == 'valid'
    assert declared['immediateChanges'][0]['type'] == 'item.use'
    assert validation['rejected'] == []
    assert applied['nextState']['playerCharacters'][0]['health']['currentHp'] == 15

    greater = {'id': 'potion_2', 'name': 'Potion of Greater Healing', 'quantity': 1, 'type': 'potion'}
    with app.app_context():
        declared, _validation, _applied = _run(_state(POTION, greater), None, turn_id=5, message='I drink my potion.')
    assert declared['validatedActions'][0]['status'] == 'needs_clarification'
    assert declared['immediateChanges'] == []


def test_starter_kits_carry_a_potion_and_healers_carry_a_kit() -> None:
    fighter = {item['name']: item for item in starting_inventory_for_class('Fighter')}
    cleric = {item['name']: item for item in starting_inventory_for_class('Cleric')}
    druid = {item['name']: item for item in starting_inventory_for_class('Druid')}
    assert fighter['Potion of Healing']['metadata'] == {'effectId': 'potion_healing'}
    assert "Healer's Kit" not in fighter
    assert cleric["Healer's Kit"]['metadata'] == {'effectId': 'healers_kit', 'usesRemaining': 10}
    assert item_effect_id(druid["Healer's Kit"]) == 'healers_kit'
    assert item_effect_id(druid['Potion of Healing']) == 'potion_healing'
