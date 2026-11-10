"""The connections between spells, class abilities, rolls and the combat turn (W2-INT2)."""

from __future__ import annotations

from copy import deepcopy
import json

import pytest

from aidm_server.character_resources import normalize_spell_resources
from aidm_server.character_state import character_roll_spec
from aidm_server.class_capabilities import rage_effect, turned_effect
from aidm_server.combat.attacks import resolve_attack, resolve_save
from aidm_server.combat.intent_planner import plan_enemy_intents
from aidm_server.combat.legal_actions import legal_combat_actions_for_player, resolve_combat_legal_action
from aidm_server.combat.pipeline import (
    hostile_action_targets_creature,
    prepare_combat_for_turn,
    turn_boundaries,
)
from aidm_server.combat.state import combat_turn_context, default_turn_economy, player_combat_participant
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.extraction.pre_dm_action_extractor import extract_pre_dm_actions
from aidm_server.game_state.models import player_character_from_model
from aidm_server.game_state.orchestration.combat_resolution import (
    build_dm_combat_context,
    derive_trusted_damage_changes,
    resolve_authoritative_player_attack,
)
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.models import Campaign, DmTurn, Player, Session, safe_json_dumps
from aidm_server.player_rolls import canonical_roll_sentence, resolve_authoritative_player_roll
from aidm_server.spell_effects import resolve_targeted_spell
from aidm_server.spellbook import normalize_spellbook, spell_payload
from aidm_server.turn_engine import TurnEngine


ACTOR = 'player_1'
NO_LLM = {'tacticalLevel': 'simple', 'allowSentientEnemyBrain': False, 'allowFreeformEnemyTactics': False, 'maxLlmCallsPerRound': 0}
CLUB = {'id': 'club', 'name': 'Club', 'type': 'attack', 'range': 'melee', 'attackBonus': 3, 'damage': {'dice': '1d4+1', 'type': 'bludgeoning'}}
STATS = {'strength': 16, 'dexterity': 16, 'constitution': 12, 'intelligence': 10, 'wisdom': 16, 'charisma': 16}


def _participant(pid: str, team: str, *, band: str = 'near', hp: int = 20, **extra) -> dict:
    participant = {
        'id': pid, 'name': pid.replace('_', ' ').title(), 'team': team,
        'kind': 'player_character' if team == 'player' else 'creature', 'level': 3,
        'hp': {'current': hp, 'max': 20, 'temp': 0}, 'armorClass': 12, 'stats': dict(STATS),
        'conditions': [], 'activeEffects': [], 'position': {'rangeBand': band},
        'isAlive': True, 'isConscious': True, 'isPresent': True,
        **({'playerId': int(pid.split('_')[1])} if team == 'player' else {'abilities': [deepcopy(CLUB)], 'creatureType': 'humanoid'}),
    }
    participant.update(extra)
    return participant


def _actor(participant: dict, class_name: str = 'Fighter', level: int = 3, **extra) -> dict:
    return {
        'id': participant['id'], 'playerId': participant['playerId'], 'name': participant['name'],
        'class': class_name, 'level': level, 'stats': dict(participant['stats']),
        'health': {'currentHp': participant['hp']['current'], 'maxHp': 20, 'tempHp': 0, 'conditions': []},
        'inventory': {'items': [], 'currency': {}},
        **extra,
    }


def _state(actors: list[dict], *participants: dict, active: str = ACTOR, economy: dict | None = None) -> dict:
    return {
        'currentScene': {'locationId': 'field', 'sceneType': 'combat', 'combatState': 'active'},
        'playerCharacters': actors,
        'combat': {
            'status': 'active', 'round': 1, 'turnIndex': 0, 'participants': list(participants), 'battlefield': {},
            'initiative': [
                {'participantId': participant['id'], 'roll': 20 - index, 'modifier': 0, 'total': 20 - index, 'order': index}
                for index, participant in enumerate(participants)
            ],
            'flags': {
                'activeActorId': active, 'combatDifficultyAI': NO_LLM,
                'turnEconomy': {**default_turn_economy(active, 1), **(economy or {})},
            },
        },
        'stateChangeLedger': [],
    }


def _combatant(state: dict, participant_id: str) -> dict:
    return next(participant for participant in state['combat']['participants'] if participant['id'] == participant_id)


def _apply(state: dict, changes: list[dict]) -> dict:
    validation = validate_state_changes(state=state, changes=changes)
    assert validation['rejected'] == [], validation['rejected']
    return apply_state_changes(state, validated_changes_for_application(validation))


def _player_attack(state: dict, *, weapon: dict, kept: int, target_id: str = 'enemy_1', ability: str = 'strength',
                   modifier: int = 3, ranged: bool = False, rider: dict | None = None, damage_roll: int = 3, turn_id: int = 11) -> dict:
    combat_action = {
        'authoritative': True, 'action_type': 'attack', 'target_id': target_id, 'weapon_id': weapon['id'],
        'weapon_name': weapon['name'], 'damage_dice': weapon['dice'], 'damage_type': weapon['type'], 'ranged': ranged,
    }
    if rider:
        combat_action['rider'] = rider
    return resolve_authoritative_player_attack(
        state=state, actor_id=ACTOR, turn_id=turn_id, action_intent={'combat': combat_action},
        authoritative_roll={
            'authoritative': True, 'kept': kept, 'total': kept + modifier, 'ability': {'key': ability},
            'modifier_breakdown': {'ability_modifier': modifier},
        },
        damage_roller=lambda _sides: damage_roll,
    )


def _attack_followups(state: dict, resolved: dict, turn_id: int = 11) -> list[dict]:
    return derive_trusted_damage_changes(
        state=state, dm_context_packet={'combatState': {'playerResolvedAction': resolved, 'trustedDamageEvents': [resolved]}},
        actor_id=ACTOR, turn_id=turn_id, already_applied_changes=[],
    ).resolved


def _sources(resolved: dict) -> list[str]:
    return [component['source'] for component in resolved['damageComponents']]


def _weapon(name: str, item_id: str, dice: str, damage_type: str, **extra) -> dict:
    return {'id': item_id, 'name': name, 'type': 'weapon', 'equipped': True, 'slot': 'main_hand', 'dice': dice, 'damageType': damage_type,
            **extra}


def _item(weapon: dict) -> dict:
    item = {key: value for key, value in weapon.items() if key not in {'dice', 'damageType'}}
    return {**item, 'damage': {'dice': weapon['dice'], 'type': weapon['damageType']}}


def _hud(weapon: dict) -> dict:
    return {'id': weapon['id'], 'name': weapon['name'], 'dice': weapon['dice'], 'type': weapon['damageType']}


# S1 — turn-start spell effects run in live combat ---------------------------------------------------------------

TURN_START_SPELLS = {
    'Entangle': 'repeat_save',
    'Hold Person': 'repeat_save',
    'Web': 'repeat_save',
    'Blindness/Deafness': 'repeat_save',
    'Fear': 'repeat_save',
    "Tasha's Hideous Laughter": 'repeat_save',
    'Moonbeam': 'recurring_damage',
    'Spirit Guardians': 'aura_damage',
}


def _cast_on(combat: dict, spell_name: str, *, caster_id: str, target_id: str) -> dict:
    spell = spell_payload(spell_name)
    spell['delivery']['castingAbility'] = 'wisdom'
    targets = [caster_id] if spell_name == 'Spirit Guardians' else [target_id]
    casting = {**deepcopy(combat), 'flags': {**combat['flags'], 'activeActorId': caster_id}}
    result = resolve_targeted_spell(casting, caster_id=caster_id, spell=spell, target_ids=targets, cast_id=f'cast:{spell_name}',
                                    roller=lambda _sides: 1, caster_resources={})
    assert result['ok'], result['reason']
    return {**result['combat'], 'flags': combat['flags']}


@pytest.mark.parametrize('spell_name', sorted(TURN_START_SPELLS))
def test_turn_start_spell_effects_run_in_an_enemy_turn_block_and_the_persisted_advance(spell_name):
    player = _participant(ACTOR, 'player')
    state = _state([_actor(player, 'Cleric', 5)], player, _participant('enemy_1', 'enemy'))
    state['combat'] = _cast_on(state['combat'], spell_name, caster_id=ACTOR, target_id='enemy_1')

    attack = {'enemyId': 'enemy_1', 'targetId': ACTOR, 'intentType': 'attack', 'abilityId': 'club'}
    context = build_dm_combat_context(state=state, combat_context={'enemyRequiredActions': [attack]}, pending_rolls=[],
                                      resolved_player_roll=False, enemy_roller=lambda _sides: 1, turn_id=90)
    (enemy_turn,) = context['enemyResolvedActions']
    (event,) = enemy_turn['turnStartEffects']
    assert (event['participantId'], event['kind'], event['sourceName']) == ('enemy_1', TURN_START_SPELLS[spell_name], spell_payload(spell_name)['name'])

    # The persisted turn advance rolls the same results and records them for the state log.
    advance = {
        'id': 'advance', 'turnId': 90, 'type': 'combat.update', 'round': 2, 'turnIndex': 0, 'flags': {'activeActorId': ACTOR},
        'turnBoundaries': turn_boundaries(state['combat'], combat_turn_context(state['combat'])),
    }
    applied = _apply(state, [advance])
    started = next(item for item in applied['appliedChanges'][0]['turnBoundaryEvents'] if item['actorId'] == 'enemy_1' and item['phase'] == 'start')
    assert started['turnStartEffects'] == enemy_turn['turnStartEffects']


@pytest.mark.parametrize('spell_name', sorted(TURN_START_SPELLS))
def test_turn_start_spell_effects_run_as_a_player_turn_starts(spell_name):
    player = _participant(ACTOR, 'player')
    state = _state([_actor(player)], player, _participant('enemy_1', 'enemy'), active='enemy_1')
    state['combat'] = _cast_on(state['combat'], spell_name, caster_id='enemy_1', target_id=ACTOR)

    advance = {
        'id': 'advance', 'turnId': 91, 'type': 'combat.update', 'round': 2, 'turnIndex': 0, 'flags': {'activeActorId': ACTOR},
        'turnBoundaries': [{'actorId': 'enemy_1', 'phase': 'end', 'round': 1}, {'actorId': ACTOR, 'phase': 'start', 'round': 2}],
    }
    applied = _apply(state, [advance])
    started = applied['appliedChanges'][0]['turnBoundaryEvents'][1]
    (event,) = started['turnStartEffects']
    assert (event['participantId'], event['kind'], event['sourceName']) == (ACTOR, TURN_START_SPELLS[spell_name], spell_payload(spell_name)['name'])
    after = applied['nextState']
    # Damage and ended conditions reach the character sheet too.
    assert after['playerCharacters'][0]['health']['currentHp'] == _combatant(after, ACTOR)['hp']['current']
    assert after['playerCharacters'][0]['health']['conditions'] == _combatant(after, ACTOR)['conditions']


# S2 — weapon damage ends what damage should end -------------------------------------------------------------------

SWORD = _weapon('Longsword', 'sword', '1d8', 'slashing')


def _spell_struck(spell_name: str, *, roller=lambda _sides: 1) -> dict:
    player = _participant(ACTOR, 'player')
    state = _state([_actor(player, 'Bard', 5, inventory={'items': [_item(SWORD)], 'currency': {}})], player, _participant('enemy_1', 'enemy'))
    spell = spell_payload(spell_name)
    spell['delivery']['castingAbility'] = 'charisma'
    result = resolve_targeted_spell(state['combat'], caster_id=ACTOR, spell=spell, target_ids=['enemy_1'],
                                    cast_id=f'cast:{spell_name}', roller=roller, caster_resources={})
    assert result['ok'], result['reason']
    state['combat'] = result['combat']
    return state


@pytest.mark.parametrize(('spell_name', 'condition', 'roller'), [
    ('Sleep', 'unconscious', lambda _sides: 8),
    ('Hypnotic Pattern', 'incapacitated', lambda _sides: 1),
    ('Charm Person', 'charmed', lambda _sides: 1),
])
def test_a_weapon_hit_ends_effects_that_end_on_damage(spell_name, condition, roller):
    state = _spell_struck(spell_name, roller=roller)
    assert condition in _combatant(state, 'enemy_1')['conditions']
    resolved = _player_attack(state, weapon=_hud(SWORD), kept=15)
    assert resolved['hit'] is True
    applied = _apply(state, _attack_followups(state, resolved))
    enemy = _combatant(applied['nextState'], 'enemy_1')
    assert condition not in enemy['conditions']
    damage = next(change for change in applied['appliedChanges'] if change['type'] == 'combat.participant.update')
    assert damage['afterDamage'][0]['kind'] == 'effects_ended'


def test_a_weapon_hit_gives_tashas_hideous_laughter_a_save_with_advantage():
    state = _spell_struck("Tasha's Hideous Laughter")
    resolved = _player_attack(state, weapon=_hud(SWORD), kept=15)
    applied = _apply(state, _attack_followups(state, resolved))
    damage = next(change for change in applied['appliedChanges'] if change['type'] == 'combat.participant.update')
    (save,) = [event for event in damage['afterDamage'] if event['kind'] == 'repeat_save']
    assert (save['reason'], save['save']['mode'], len(save['save']['rolls'])) == ('took_damage', 'advantage', 2)
    held = 'incapacitated' in _combatant(applied['nextState'], 'enemy_1')['conditions']
    assert held is not save['save']['succeeded']


# S3 — Chromatic Orb keeps its chosen damage type -----------------------------------------------------------------


def _caster_state(class_name: str, level: int, spells: list[str], *, prepared: list[str] | None = None, enemies: int = 1) -> dict:
    player = _participant(ACTOR, 'player', level=level, **{'class': class_name})
    spellbook = {'knownSpells': [{'name': name, 'level': spell_payload(name)['level']} for name in spells]}
    if prepared is not None:
        spellbook['preparedSpells'] = prepared
    actor = _actor(player, class_name, level, spellbook=normalize_spellbook(spellbook, class_name=class_name),
                   spellResources=normalize_spell_resources(None, class_name=class_name, level=level))
    return _state([actor], player, *[_participant(f'enemy_{index}', 'enemy') for index in range(1, enemies + 1)])


def test_a_typed_chromatic_orb_cast_keeps_the_chosen_damage_type():
    state = _caster_state('Sorcerer', 3, ['Chromatic Orb'])
    intent = {'kind': 'spell', 'spell': {'name': 'Chromatic Orb', 'damage_type': 'cold', 'target_ids': ['enemy_1']}}
    (action,) = extract_pre_dm_actions(current_state=state, player_message='I hurl an orb of frost.', recent_timeline=[],
                                       actor_id=ACTOR, action_intent=intent)['declaredActions']
    assert action['damageType'] == 'cold'
    declared = validate_declared_actions(state=state, declared_actions=[action], current_turn=30, expected_actor_id=ACTOR)
    (cast,) = declared['immediateChanges']
    assert cast['damageType'] == 'cold' and cast['resolution']['damageType'] == 'cold'


# S4 — stored effects apply to out-of-combat checks ----------------------------------------------------------------

GUIDANCE = {
    'id': 'cast_guidance:player_7:0', 'sourceId': 'spell_guidance', 'sourceName': 'Guidance', 'casterId': 'player_8',
    'castId': 'cast_guidance', 'modifiers': {'checkBonusDice': '1d4'}, 'duration': {'rounds': 10, 'tickOn': 'target_turn_end'},
    'concentration': True, 'consumeOn': 'check',
}
MAGE_ARMOR = {'id': 'mage_armor', 'sourceId': 'spell_mage_armor', 'sourceName': 'Mage Armor', 'casterId': 'player_7',
              'castId': 'cast_armor', 'modifiers': {'acBase': 13}, 'duration': None, 'until': 'long_rest'}


def _roller_player(class_name: str = 'Fighter', level: int = 3, effects: list[dict] | None = None) -> Player:
    return Player(
        player_id=7, name='Danny', character_name='Seraphina', class_=class_name, level=level,
        stats=safe_json_dumps({
            'ability_scores': {'strength': 10, 'dexterity': 14, 'constitution': 10, 'intelligence': 10, 'wisdom': 12, 'charisma': 10},
            'current_hp': 20, 'max_hp': 20, 'active_effects': effects or [],
        }, {}),
    )


def test_guidance_applies_to_an_out_of_combat_check_and_is_spent():
    player = _roller_player(effects=[deepcopy(GUIDANCE), deepcopy(MAGE_ARMOR)])
    roll = resolve_authoritative_player_roll(player=player, rule_type='perception', dc_hint='DC 10', action_intent=None,
                                             roller=lambda _sides: 3)
    assert [(bonus['dice'], bonus['total']) for bonus in roll['bonus_dice']] == [('+1d4', 3)]
    assert roll['total'] == 3 + 1 + 3
    assert [effect['id'] for effect in json.loads(player.stats)['active_effects']] == ['mage_armor']
    again = resolve_authoritative_player_roll(player=player, rule_type='perception', dc_hint='DC 10', action_intent=None,
                                              roller=lambda _sides: 3)
    assert again['bonus_dice'] == []


# S5 — Shield's opt-out is honoured ---------------------------------------------------------------------------------


def _wizard_player(sheet: dict) -> Player:
    return Player(
        player_id=1, character_name='Ilsa', class_='Wizard', level=3, race='Human', inventory='[]',
        stats=safe_json_dumps({
            'ability_scores': {'strength': 10, 'dexterity': 14, 'constitution': 12, 'intelligence': 16, 'wisdom': 10, 'charisma': 10},
            'current_hp': 20, 'max_hp': 20,
        }, {}),
        character_sheet=json.dumps(sheet),
    )


@pytest.mark.parametrize(('sheet', 'shielded'), [({}, True), ({'autoReactions': True}, True), ({'autoReactions': False}, False)])
def test_the_character_sheet_auto_reaction_setting_decides_whether_shield_is_cast(sheet, shielded):
    actor = player_character_from_model(_wizard_player(sheet))
    assert actor['autoReactions'] is shielded
    wizard = {**player_combat_participant(actor), 'position': {'rangeBand': 'melee'}}
    state = _state([actor], wizard, _participant('enemy_1', 'enemy', band='melee'), active='enemy_1')
    attack = {'enemyId': 'enemy_1', 'targetId': ACTOR, 'intentType': 'attack', 'abilityId': 'club'}
    context = build_dm_combat_context(state=state, combat_context={'enemyRequiredActions': [attack]}, pending_rolls=[],
                                      resolved_player_roll=False, enemy_roller=lambda sides: 11 if sides == 20 else 2, turn_id=60)
    (result,) = context['enemyResolvedActions']
    assert result['hit'] is not shielded
    assert [reaction['kind'] for reaction in result['reactions'] if reaction['event'] == 'attack_hit'] == (['shield'] if shielded else [])


# S7 — free-form casts match only what the caster can cast ----------------------------------------------------------


@pytest.fixture()
def no_extraction_helper(monkeypatch):
    monkeypatch.setattr('aidm_server.game_state.extraction.pre_dm_action_extractor._helper_enabled', lambda: False)


def _freeform_spell_casts(state: dict, message: str) -> list[str]:
    extraction = extract_pre_dm_actions(current_state=state, player_message=message, recent_timeline=[], actor_id=ACTOR)
    return [action.get('spellName') for action in extraction['declaredActions'] if action['type'] == 'spell.cast']


@pytest.mark.parametrize('spell_name', ['Aid', 'Web', 'Fear', 'Hex'])
def test_a_freeform_cast_of_a_spell_the_character_lacks_is_ordinary_narration(no_extraction_helper, spell_name):
    state = _caster_state('Sorcerer', 5, ['Chromatic Orb'])
    assert _freeform_spell_casts(state, f'I cast {spell_name} on the cultists.') == []
    assert _freeform_spell_casts(state, 'I cast Chromatic Orb at the cultist.') == ['Chromatic Orb']


def test_a_freeform_cast_needs_the_spell_prepared_by_a_preparing_class(no_extraction_helper):
    unprepared = _caster_state('Cleric', 3, ['Bless', 'Guiding Bolt'], prepared=['Guiding Bolt'])
    assert _freeform_spell_casts(unprepared, 'I cast Bless on my friends.') == []
    assert _freeform_spell_casts(unprepared, 'I cast Guiding Bolt at the cultist.') == ['Guiding Bolt']


# K1 — class actions clicked in the combat HUD take effect (socket path) ---------------------------------------------


def _socket_combat(app, socketio, app_runtime, monkeypatch, *, class_name: str, level: int) -> tuple:
    from aidm_server.creatures.core_bestiary import core_creature
    from aidm_server.database import db
    from tests.helpers import seed_world_campaign_player_session

    def fake_stream(user_input, context, speaking_player=None, rules_hint=None):
        del user_input, context, speaking_player, rules_hint
        yield 'The rules-engine result is narrated exactly as resolved.'

    monkeypatch.setattr(app_runtime['modules']['socketio_events'], 'query_dm_function_stream', fake_stream)
    ids = seed_world_campaign_player_session(app)
    actor_id = f"player_{ids['player_id']}"
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        session = db.session.get(Session, ids['session_id'])
        player.class_, player.level = class_name, level
        player.stats = safe_json_dumps({'ability_scores': dict(STATS), 'current_hp': 30, 'max_hp': 30}, {})
        player.inventory = safe_json_dumps([], [])
        session.state_snapshot = safe_json_dumps(
            {'currentScene': {'locationId': 'den', 'name': 'Den', 'sceneType': 'exploration', 'combatState': 'none'}}, {},
        )
        db.session.commit()
    started = app.test_client().post(f"/api/sessions/{ids['session_id']}/combat/start", json={'creature': core_creature('wolf'), 'enemyCount': 1})
    assert started.status_code == 200
    with app.app_context():
        session = db.session.get(Session, ids['session_id'])
        state = json.loads(session.state_snapshot)
        combat = state['combat']
        enemy_id = next(participant['id'] for participant in combat['participants'] if participant['team'] == 'enemy')
        for participant in combat['participants']:
            participant['position'] = {'rangeBand': 'near'}
        combat['initiative'] = [
            {'participantId': participant_id, 'roll': 20 - order, 'modifier': 0, 'total': 20 - order, 'order': order, 'source': 'test'}
            for order, participant_id in enumerate([actor_id, enemy_id])
        ]
        combat.update({'round': 1, 'turnIndex': 0})
        combat['flags'].update({'activeActorId': actor_id, 'turnEconomy': default_turn_economy(actor_id, 1), 'combatDifficultyAI': NO_LLM})
        state['playerCharacters'] = [player_character_from_model(db.session.get(Player, ids['player_id']))]
        session.state_snapshot = safe_json_dumps(state, {})
        db.session.commit()
    client = socketio.test_client(app, flask_test_client=app.test_client())
    client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    client.get_received()
    return client, ids, actor_id


def _hud_action(client, ids: dict, action_id: str, message_id: str) -> list[dict]:
    message = 'I act.'
    client.emit('send_message', {
        'session_id': ids['session_id'], 'campaign_id': ids['campaign_id'], 'world_id': ids['world_id'],
        'player_id': ids['player_id'], 'message': message, 'client_message_id': message_id,
        'action_intent': {'kind': 'combat', 'source': 'combat_hud', 'text': message, 'client_message_id': message_id,
                          'combat': {'action_id': action_id}},
    })
    events = client.get_received()
    assert not [event for event in events if event['name'] == 'error'], events
    return events


def _turn_pipeline(app, message_id: str) -> tuple[dict, dict]:
    from aidm_server.database import db
    from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY

    with app.app_context():
        turn = DmTurn.query.filter_by(client_message_id=message_id).one()
        session = db.session.get(Session, turn.session_id)
        return json.loads(turn.metadata_json)[STATE_PIPELINE_METADATA_KEY], json.loads(session.state_snapshot)


def test_rage_clicked_in_the_combat_hud_is_a_class_feature_use(app, socketio, app_runtime, monkeypatch):
    client, ids, actor_id = _socket_combat(app, socketio, app_runtime, monkeypatch, class_name='Barbarian', level=1)
    _hud_action(client, ids, 'class.use.rage', 'hud-rage')
    pipeline, state = _turn_pipeline(app, 'hud-rage')
    assert [change['type'] for change in pipeline['immediateAppliedChanges']] == ['class_feature.use']
    barbarian = _combatant(state, actor_id)
    assert f'rage:{actor_id}' in {effect['id'] for effect in barbarian['activeEffects']}
    assert state['playerCharacters'][0]['classFeatureState']['rage']['current'] == 1
    economy = state['combat']['flags']['turnEconomy']
    assert (economy['actionRemaining'], economy['bonusActionRemaining']) == (1, 0)
    client.disconnect()


def test_cunning_action_clicked_in_the_combat_hud_is_resolved_by_the_engine(app, socketio, app_runtime, monkeypatch):
    client, ids, actor_id = _socket_combat(app, socketio, app_runtime, monkeypatch, class_name='Rogue', level=2)
    _hud_action(client, ids, 'class.cunning_action.dash', 'hud-dash')
    pipeline, state = _turn_pipeline(app, 'hud-dash')
    assert pipeline['preDmExtraction']['declaredActions'] == []
    assert pipeline['preDmExtraction']['notes'] == ['combat_action_engine_resolved']
    assert pipeline['dmContextPacket']['combatState']['playerResolvedCombatAction']['actionType'] == 'dash'
    economy = state['combat']['flags']['turnEconomy']
    assert (economy['actionRemaining'], economy['bonusActionRemaining'], economy['movementRemaining']) == (1, 0, 2)
    client.disconnect()


# K2 — bonus-action strikes are not the Attack action ------------------------------------------------------------------


def _monk_state(level: int) -> tuple[dict, Player]:
    monk = _participant(ACTOR, 'player', band='melee', level=level, **{'class': 'Monk'})
    enemy = _participant('enemy_1', 'enemy', band='melee', armorClass=1)
    monk['position']['engagedWith'] = ['enemy_1']
    enemy['position']['engagedWith'] = [ACTOR]
    state = _state([_actor(monk, 'Monk', level)], monk, enemy)
    player = Player(player_id=1, character_name='Kai', class_='Monk', level=level, inventory='[]',
                    stats=json.dumps({'ability_scores': dict(STATS)}))
    return state, player


def _take(state: dict, player: Player, action_id: str, turn_id: int, target_id: str | None = 'enemy_1') -> dict:
    canonical, code, message = resolve_combat_legal_action(state, player, action_id=action_id, target_id=target_id)
    assert code is None, message
    turn = DmTurn(turn_id=turn_id, player_id=1, metadata_json=json.dumps({'action_intent': {'kind': 'combat', 'combat': canonical}}))
    result = prepare_combat_for_turn(
        state=state, session_obj=Session(session_id=1, campaign_id=1), campaign=Campaign(campaign_id=1, workspace_id='w'),
        turn=turn, player_message='', workspace_id='w',
    )
    assert 'combatActionBlockedReason' not in result['debug'], result['debug'].get('combatActionBlockedReason')
    return _apply(state, result['changes'])['nextState']


def _use_capability(state: dict, capability_id: str, turn_id: int) -> dict:
    action = {'id': 'act_1', 'type': 'class_feature.use', 'actorId': ACTOR, 'capabilityId': capability_id}
    declared = validate_declared_actions(state=state, declared_actions=[action], current_turn=turn_id, expected_actor_id=ACTOR)
    assert declared['validatedActions'][0]['status'] == 'valid', declared['validatedActions'][0]['reason']
    return _apply(state, declared['immediateChanges'])['nextState']


def _economy(state: dict) -> tuple[int, int, int]:
    economy = state['combat']['flags']['turnEconomy']
    return economy['actionRemaining'], economy['bonusActionRemaining'], economy.get('attacksRemaining', 0)


def test_a_martial_arts_strike_after_the_attack_action_spends_only_the_bonus_action():
    state, player = _monk_state(5)
    state = _take(state, player, 'class.attack.unarmed', 40)
    assert _economy(state) == (0, 1, 1)
    state = _take(state, player, 'class.strike.martial_arts_strike', 41)
    assert _economy(state) == (0, 0, 1)  # Extra Attack's second attack is still there.
    state = _take(state, player, 'class.attack.unarmed', 42)
    assert _economy(state) == (0, 0, 0)


def test_flurry_of_blows_grants_exactly_two_strikes():
    state, player = _monk_state(2)
    state = _take(state, player, 'class.attack.unarmed', 40)
    state = _use_capability(state, 'flurry_of_blows', 41)
    assert _economy(state) == (0, 0, 0)
    state = _take(state, player, 'class.strike.flurry_of_blows', 42)
    state = _take(state, player, 'class.strike.flurry_of_blows', 43)
    assert _economy(state) == (0, 0, 0)
    strike = next(action for action in legal_combat_actions_for_player(state, player)['actions'] if action['id'] == 'class.strike.flurry_of_blows')
    assert (strike['available'], strike['reason']) == (False, 'Both Flurry of Blows strikes are spent.')


# K3 — attacks carry what class features need --------------------------------------------------------------------------

RAPIER = _weapon('Rapier', 'rapier', '1d8', 'piercing')
LONGBOW = _weapon('Longbow', 'bow', '1d8', 'piercing', slot='two_hands')
GREATSWORD = _weapon('Greatsword', 'greatsword', '2d6', 'slashing', slot='two_hands')
DAGGER = _weapon('Dagger', 'dagger', '1d4', 'piercing', slot='off_hand')


def _armed_state(class_name: str, level: int, weapons: list[dict], *, enemy: dict | None = None, attacker: dict | None = None,
                 allies: list[dict] = (), **actor_fields) -> dict:
    attacker = attacker or _participant(ACTOR, 'player', band='melee', **{'class': class_name})
    enemy = enemy or _participant('enemy_1', 'enemy', band='melee')
    actor = _actor(attacker, class_name, level, inventory={'items': [_item(weapon) for weapon in weapons], 'currency': {}}, **actor_fields)
    return _state([actor], attacker, enemy, *allies)


def test_sneak_attack_applies_with_advantage_once_per_turn():
    state = _armed_state('Rogue', 3, [RAPIER], enemy=_participant('enemy_1', 'enemy', band='melee', conditions=['restrained']))
    first = _player_attack(state, weapon=_hud(RAPIER), kept=15, ability='dexterity')
    assert first['attackMode'] == 'advantage' and first['hit'] is True
    assert 'Sneak Attack' in _sources(first) and first['oncePerTurn'] == ['sneak_attack']
    sneak = next(component for component in first['damageComponents'] if component['source'] == 'Sneak Attack')
    assert sneak['dice'] == '2d6'
    after = _apply(state, _attack_followups(state, first))['nextState']
    assert after['combat']['flags']['turnEconomy']['oncePerTurnUsed'] == ['sneak_attack']
    second = _player_attack(after, weapon=_hud(RAPIER), kept=15, ability='dexterity', turn_id=12)
    assert second['hit'] is True and 'Sneak Attack' not in _sources(second)
    after['combat']['flags']['turnEconomy'] = default_turn_economy(ACTOR, 2)
    assert 'Sneak Attack' in _sources(_player_attack(after, weapon=_hud(RAPIER), kept=15, ability='dexterity', turn_id=13))


def test_sneak_attack_applies_with_an_engaged_ally_but_never_with_disadvantage():
    ally = _participant('player_2', 'player', band='melee')
    enemy = _participant('enemy_1', 'enemy', band='melee', armorClass=1)
    ally['position']['engagedWith'] = ['enemy_1']
    enemy['position']['engagedWith'] = ['player_2']
    state = _armed_state('Rogue', 3, [RAPIER], enemy=enemy, allies=[ally])
    flanked = _player_attack(state, weapon=_hud(RAPIER), kept=15, ability='dexterity')
    assert flanked['attackMode'] == 'normal' and 'Sneak Attack' in _sources(flanked)
    _combatant(state, ACTOR)['conditions'] = ['poisoned']
    hampered = _player_attack(state, weapon=_hud(RAPIER), kept=15, ability='dexterity')
    assert hampered['attackMode'] == 'disadvantage' and hampered['hit'] is True
    assert 'Sneak Attack' not in _sources(hampered)
    alone = _armed_state('Rogue', 3, [RAPIER], enemy=_participant('enemy_1', 'enemy', band='melee'))
    assert 'Sneak Attack' not in _sources(_player_attack(alone, weapon=_hud(RAPIER), kept=15, ability='dexterity'))


def test_colossus_slayer_hits_a_wounded_target():
    hunter = _participant(ACTOR, 'player', band='far', **{'class': 'Ranger - Hunter'})
    wounded = _participant('enemy_1', 'enemy', band='far', hp=15)
    state = _armed_state('Ranger - Hunter', 3, [LONGBOW], attacker=hunter, enemy=wounded)
    resolved = _player_attack(state, weapon=_hud(LONGBOW), kept=15, ability='dexterity', ranged=True)
    assert 'Colossus Slayer' in _sources(resolved) and resolved['oncePerTurn'] == ['colossus_slayer']
    _combatant(state, 'enemy_1')['hp']['current'] = 20
    assert 'Colossus Slayer' not in _sources(_player_attack(state, weapon=_hud(LONGBOW), kept=15, ability='dexterity', ranged=True))


def test_dueling_adds_two_damage_with_a_single_one_handed_weapon():
    duelist = _participant(ACTOR, 'player', band='melee', fightingStyle='dueling', **{'class': 'Fighter'})
    state = _armed_state('Fighter', 1, [SWORD], attacker=duelist)
    resolved = _player_attack(state, weapon=_hud(SWORD), kept=15)
    dueling = next(component for component in resolved['damageComponents'] if component['source'] == 'Dueling fighting style')
    assert dueling['total'] == 2
    two_weapons = _armed_state('Fighter', 1, [SWORD, DAGGER], attacker=deepcopy(duelist))
    assert 'Dueling fighting style' not in _sources(_player_attack(two_weapons, weapon=_hud(SWORD), kept=15))


def test_great_weapon_fighting_rerolls_ones_and_twos_once():
    fighter = _participant(ACTOR, 'player', band='melee', fightingStyle='great_weapon', **{'class': 'Fighter'})
    state = _armed_state('Fighter', 1, [GREATSWORD], attacker=fighter)
    faces = iter([1, 6, 2, 2])
    combat_action = {
        'authoritative': True, 'action_type': 'attack', 'target_id': 'enemy_1', 'weapon_id': 'greatsword',
        'weapon_name': 'Greatsword', 'damage_dice': '2d6', 'damage_type': 'slashing',
    }
    resolved = resolve_authoritative_player_attack(
        state=state, actor_id=ACTOR, turn_id=11, action_intent={'combat': combat_action},
        authoritative_roll={'authoritative': True, 'kept': 15, 'total': 18, 'ability': {'key': 'strength'}, 'modifier_breakdown': {'ability_modifier': 3}},
        damage_roller=lambda _sides: next(faces),
    )
    (weapon,) = resolved['damageComponents']
    assert (weapon['rolls'], weapon['rerolled']) == ([6, 2], [1, 2])


def test_improved_critical_scores_a_critical_on_nineteen():
    champion = _participant(ACTOR, 'player', band='melee', **{'class': 'Fighter - Champion'})
    state = _armed_state('Fighter - Champion', 3, [SWORD], attacker=champion, enemy=_participant('enemy_1', 'enemy', band='melee', armorClass=30))
    resolved = _player_attack(state, weapon=_hud(SWORD), kept=19)
    assert (resolved['hit'], resolved['critical'], resolved['damageComponents'][0]['dice']) == (True, True, '2d8')
    _combatant(state, ACTOR)['class'] = 'Fighter - Battle Master'
    assert _player_attack(state, weapon=_hud(SWORD), kept=19)['hit'] is False


def _paladin_state(enemy: dict) -> dict:
    paladin = _participant(ACTOR, 'player', band='melee', **{'class': 'Paladin'})
    return _armed_state('Paladin', 2, [SWORD], attacker=paladin, enemy=enemy,
                        spellResources={'slots': {'1': {'current': 2, 'max': 2}}})


SMITE = {'capabilityId': 'divine_smite', 'option': '1', 'slotLevel': 1}


def test_divine_smite_spends_its_slot_only_on_a_hit_and_adds_a_die_against_undead():
    missed_state = _paladin_state(_participant('enemy_1', 'enemy', band='melee', armorClass=30))
    missed = _player_attack(missed_state, weapon=_hud(SWORD), kept=5, rider=SMITE)
    assert missed['hit'] is False and _attack_followups(missed_state, missed) == []

    state = _paladin_state(_participant('enemy_1', 'enemy', band='melee', creatureType='undead'))
    resolved = _player_attack(state, weapon=_hud(SWORD), kept=15, rider=SMITE)
    smite = next(component for component in resolved['damageComponents'] if component['source'] == 'Divine Smite')
    assert (smite['dice'], smite['type']) == ('3d8', 'radiant')
    followups = _attack_followups(state, resolved)
    assert [change['type'] for change in followups] == ['combat.participant.update', 'class_feature.use']
    after = _apply(state, followups)['nextState']
    assert after['playerCharacters'][0]['spellResources']['slots']['1']['current'] == 1

    critical = _player_attack(state, weapon=_hud(SWORD), kept=20, rider=SMITE)
    assert next(component for component in critical['damageComponents'] if component['source'] == 'Divine Smite')['dice'] == '6d8'


def test_stunning_strike_spends_ki_on_a_hit_and_rolls_the_targets_save():
    monk = _participant(ACTOR, 'player', band='melee', **{'class': 'Monk'})
    state = _armed_state('Monk', 5, [], attacker=monk)
    unarmed = {'id': 'unarmed', 'name': 'Unarmed strike', 'dice': '1d6', 'type': 'bludgeoning'}
    resolved = _player_attack(state, weapon=unarmed, kept=15, ability='dexterity', rider={'capabilityId': 'stunning_strike', 'option': ''})
    rider = next(change for change in _attack_followups(state, resolved) if change['type'] == 'class_feature.use')
    save = rider['resolution']['save']
    assert (save['saveAbility'], save['saveDC']) == ('constitution', 14)
    after = _apply(state, _attack_followups(state, resolved))['nextState']
    assert after['playerCharacters'][0]['classFeatureState']['ki']['current'] == 4
    assert ('stunned' in _combatant(after, 'enemy_1')['conditions']) is not save['saveSucceeded']


def test_rage_damage_and_reckless_advantage_apply_only_to_strength_melee_attacks():
    barbarian = _participant(ACTOR, 'player', band='melee', **{'class': 'Barbarian'})
    barbarian['activeEffects'] = [rage_effect(ACTOR)]
    state = _armed_state('Barbarian', 2, [SWORD, LONGBOW], attacker=barbarian)
    assert 'Rage' in _sources(_player_attack(state, weapon=_hud(SWORD), kept=15))
    assert 'Rage' not in _sources(_player_attack(state, weapon=_hud(SWORD), kept=15, ability='dexterity'))
    _combatant(state, 'enemy_1')['position'] = {'rangeBand': 'far'}
    _combatant(state, ACTOR)['position'] = {'rangeBand': 'far'}
    assert 'Rage' not in _sources(_player_attack(state, weapon=_hud(LONGBOW), kept=15, ranged=True))


# K4 — fighting style reaches combat --------------------------------------------------------------------------------


def test_fighting_style_is_carried_onto_the_actor_and_combat_participant():
    player = Player(player_id=1, character_name='Bran', class_='Fighter', level=1, race='Human',
                    stats=safe_json_dumps({'ability_scores': dict(STATS), 'current_hp': 12, 'max_hp': 12}, {}),
                    character_sheet=json.dumps({'fightingStyle': 'archery'}),
                    inventory=json.dumps([_item(LONGBOW)]))
    actor = player_character_from_model(player)
    assert actor['fightingStyle'] == 'archery'
    assert player_combat_participant(actor)['fightingStyle'] == 'archery'


def test_archery_adds_two_to_ranged_weapon_attacks():
    archer = _participant(ACTOR, 'player', band='far', fightingStyle='archery', **{'class': 'Fighter'})
    state = _armed_state('Fighter', 1, [LONGBOW], attacker=archer, enemy=_participant('enemy_1', 'enemy', band='far'))
    resolved = _player_attack(state, weapon=_hud(LONGBOW), kept=10, ability='dexterity', ranged=True)
    assert resolved['attackTotal'] == 10 + 3 + 2


def test_defense_adds_one_armour_class_while_armoured():
    defender = _participant(ACTOR, 'player', band='melee', fightingStyle='defense', armorClass=16,
                            armorClassBreakdown={'source': 'armor'}, **{'class': 'Fighter'})
    enemy = _participant('enemy_1', 'enemy', band='melee')
    combat = _state([_actor(defender)], defender, enemy)['combat']

    def club(target: dict) -> dict:
        return resolve_attack(attacker=enemy, target=target, combat=combat, state={'combat': combat}, kind='enemy_attack', ranged=False,
                              first_d20=10, attack_bonus=3, attack_roller=lambda _sides: 10,
                              damage={'dice': '1d4', 'type': 'bludgeoning'}, damage_roller=lambda _sides: 1, event_id='club', turn_id=1)

    assert club(defender)['targetArmorClass'] == 17
    assert club({**defender, 'armorClassBreakdown': {'source': 'unarmored'}})['targetArmorClass'] == 16


# K5 — spells respect class features --------------------------------------------------------------------------------


def test_each_eldritch_blast_beam_adds_agonizing_blast():
    warlock = _participant(ACTOR, 'player', level=5, **{'class': 'Warlock'})
    state = _state([_actor(warlock, 'Warlock', 5)], warlock, _participant('enemy_1', 'enemy', hp=40))
    spell = spell_payload('Eldritch Blast')
    spell['delivery']['castingAbility'] = 'charisma'
    result = resolve_targeted_spell(state['combat'], caster_id=ACTOR, spell=spell, target_ids=['enemy_1'], cast_id='blast',
                                    roller=lambda sides: 15 if sides == 20 else 1)
    assert result['ok'], result['reason']
    beams = result['resolution']['targets']
    assert len(beams) == 2
    for beam in beams:
        agonizing = [effect for effect in beam['effects'] if effect.get('source') == 'Agonizing Blast']
        assert [(effect['damageType'], effect['amountApplied']) for effect in agonizing] == [('force', 3)]
    assert _combatant(result, 'enemy_1')['hp']['current'] == 40 - 2 * (1 + 3)


def _declare_cast(state: dict, spell_name: str, target_ids: list[str], turn_id: int = 50) -> dict:
    action = {'id': 'act_1', 'type': 'spell.cast', 'actorId': ACTOR, 'spellName': spell_name, 'targetIds': target_ids,
              'sourceText': f'I cast {spell_name}.'}
    return validate_declared_actions(state=state, declared_actions=[action], current_turn=turn_id, expected_actor_id=ACTOR)


@pytest.mark.parametrize('spell_name', ['Fire Bolt', 'Detect Magic'])
def test_a_raging_caster_cannot_cast(spell_name):
    state = _caster_state('Wizard', 3, ['Fire Bolt', 'Detect Magic'])
    _combatant(state, ACTOR)['activeEffects'] = [rage_effect(ACTOR)]
    (verdict,) = _declare_cast(state, spell_name, ['enemy_1'] if spell_name == 'Fire Bolt' else [])['validatedActions']
    assert (verdict['status'], verdict['reason']) == ('invalid', 'You cannot cast while raging.')
    spell = spell_payload('Fire Bolt')
    spell['delivery']['castingAbility'] = 'intelligence'
    refused = resolve_targeted_spell(state['combat'], caster_id=ACTOR, spell=spell, target_ids=['enemy_1'], cast_id='bolt', roller=lambda _sides: 10)
    assert (refused['ok'], refused['reason']) == (False, 'You cannot cast while raging.')


def test_quickened_spell_makes_the_next_action_spell_cost_a_bonus_action_once():
    from aidm_server.game_state.orchestration.turn_pipeline import _gate_pre_dm_combat_spells

    state = _use_capability(_caster_state('Sorcerer', 3, ['Fire Bolt']), 'quickened_spell', 49)
    assert state['playerCharacters'][0]['classFeatureState']['sorcery_points']['current'] == 1
    (cast,) = _declare_cast(state, 'Fire Bolt', ['enemy_1'])['immediateChanges']
    assert (cast['castingTime'], cast['resolution']['castingTime'], cast['resolution']['metamagic']) == ('bonus_action', 'bonus_action', ['quickened'])
    allowed, rejected = _gate_pre_dm_combat_spells(state=state, changes=[cast], actor_id=ACTOR, turn_id=50)
    assert rejected == []
    after = _apply(state, allowed)['nextState']
    assert _economy(after)[:2] == (1, 0)
    assert 'quickened_spell:player_1' not in {effect['id'] for effect in _combatant(after, ACTOR)['activeEffects']}
    (second,) = _declare_cast(after, 'Fire Bolt', ['enemy_1'], turn_id=51)['immediateChanges']
    assert second['castingTime'] == 'action' and 'metamagic' not in second['resolution']


def test_twinned_spell_lets_the_next_single_target_spell_take_a_second_target_once():
    state = _caster_state('Sorcerer', 3, ['Fire Bolt'], enemies=2)
    (refused,) = _declare_cast(state, 'Fire Bolt', ['enemy_1', 'enemy_2'])['validatedActions']
    assert refused['status'] == 'invalid'

    state = _use_capability(state, 'twinned_spell:0', 49)
    assert state['playerCharacters'][0]['classFeatureState']['sorcery_points']['current'] == 2
    (cast,) = _declare_cast(state, 'Fire Bolt', ['enemy_1', 'enemy_2'])['immediateChanges']
    resolution = cast['resolution']
    assert resolution['metamagic'] == ['twinned']
    assert [target['targetId'] for target in resolution['targets']] == ['enemy_1', 'enemy_2']
    assert all(target['delivery']['rolls'] for target in resolution['targets'])
    after = _apply(state, [cast])['nextState']
    assert 'twinned_spell:player_1' not in {effect['id'] for effect in _combatant(after, ACTOR)['activeEffects']}
    after['combat']['flags']['turnEconomy'] = default_turn_economy(ACTOR, 1)
    (again,) = _declare_cast(after, 'Fire Bolt', ['enemy_1', 'enemy_2'], turn_id=51)['validatedActions']
    assert again['status'] == 'invalid'


# K6 — effects that end on events end -------------------------------------------------------------------------------


def _raging_state(**economy) -> dict:
    barbarian = _participant(ACTOR, 'player', band='melee', **{'class': 'Barbarian'})
    barbarian['activeEffects'] = [rage_effect(ACTOR)]
    return _state([_actor(barbarian, 'Barbarian', 2)], barbarian, _participant('enemy_1', 'enemy', band='melee'), economy=economy)


def _raging(state: dict) -> bool:
    return f'rage:{ACTOR}' in {effect['id'] for effect in _combatant(state, ACTOR)['activeEffects']}


def _start_turn(state: dict, *, attacked: bool = False, turn_id: int = 70) -> dict:
    ending = {'actorId': ACTOR, 'phase': 'end', 'round': 1, **({'attacked': True} if attacked else {})}
    change = {'id': f'advance:{turn_id}', 'turnId': turn_id, 'type': 'combat.update', 'round': 2, 'turnIndex': 0,
              'flags': {'activeActorId': ACTOR}, 'turnBoundaries': [ending, {'actorId': ACTOR, 'phase': 'start', 'round': 2}]}
    return _apply(state, [change])


def test_rage_ends_when_its_owner_neither_attacked_nor_took_damage_since_their_last_turn():
    applied = _start_turn(_raging_state())
    assert not _raging(applied['nextState'])
    assert applied['appliedChanges'][0]['turnBoundaryEvents'][1]['effectsEnded'] == [f'rage:{ACTOR}']


def test_rage_lasts_while_its_owner_attacks_or_takes_damage():
    state = _raging_state(attackWeaponIds=['axe'])
    assert turn_boundaries(state['combat'], combat_turn_context(state['combat']))[0]['attacked'] is True
    attacked = _start_turn(state, attacked=True)['nextState']
    assert _raging(attacked)
    # The next turn needs a new attack or new damage.
    assert not _raging(_start_turn(attacked, turn_id=71)['nextState'])

    hurt = _apply(_raging_state(), [{'id': 'bite', 'turnId': 69, 'type': 'health.damage', 'actorId': ACTOR, 'amount': 3,
                                      'source': 'enemy_resolved_action', 'sourceEnemyId': 'enemy_1'}])['nextState']
    assert _raging(_start_turn(hurt)['nextState'])


def test_rage_ends_when_its_owner_falls_unconscious():
    downed = _apply(_raging_state(), [{'id': 'maul', 'turnId': 69, 'type': 'health.damage', 'actorId': ACTOR, 'amount': 25,
                                        'source': 'enemy_resolved_action', 'sourceEnemyId': 'enemy_1'}])['nextState']
    assert 'unconscious' in _combatant(downed, ACTOR)['conditions']
    assert not _raging(downed)


def _turned_state() -> dict:
    cleric = _participant(ACTOR, 'player', band='melee', **{'class': 'Fighter'})
    zombie = _participant('enemy_1', 'enemy', band='melee', creatureType='undead', armorClass=10)
    zombie['activeEffects'] = [turned_effect('enemy_1', ACTOR)]
    zombie['conditions'] = ['turned']
    return _armed_state('Fighter', 3, [SWORD], attacker=cleric, enemy=zombie)


def test_turning_ends_when_the_creature_takes_damage_not_when_it_is_attacked():
    state = _turned_state()
    missed = _player_attack(state, weapon=_hud(SWORD), kept=2)
    assert missed['hit'] is False
    # Being attacked spends nothing on the turned creature.
    assert missed['consume'] == [] and _attack_followups(state, missed) == []
    hit = _player_attack(state, weapon=_hud(SWORD), kept=15, turn_id=12)
    after = _apply(state, _attack_followups(state, hit, turn_id=12))['nextState']
    assert 'turned' not in _combatant(after, 'enemy_1')['conditions']
    assert _combatant(after, 'enemy_1')['activeEffects'] == []


def test_a_turned_creature_plans_to_move_away_and_stays_in_the_fight():
    state = _turned_state()
    state['combat']['participants'][1]['behavior'] = {'survivalRules': {'fightToDeath': True}}
    (intent,) = plan_enemy_intents(state['combat'])['intents']
    assert (intent['enemyId'], intent['intentType'], intent['selectionMethod']) == ('enemy_1', 'retreat', 'turned')
    action = {'enemyId': 'enemy_1', 'targetId': ACTOR, 'intentType': 'retreat'}
    context = build_dm_combat_context(state=state, combat_context={'enemyRequiredActions': [action]}, pending_rolls=[],
                                      resolved_player_roll=False, enemy_roller=lambda _sides: 10, turn_id=80)
    (resolved,) = context['enemyResolvedActions']
    assert resolved['movement'][0]['toRangeBand'] == 'near'
    changes = derive_trusted_damage_changes(state=state, dm_context_packet={'combatState': context}, actor_id=ACTOR, turn_id=80,
                                            already_applied_changes=[]).enemy
    assert [(change['type'], change.get('toRangeBand')) for change in changes if change['participantId'] == 'enemy_1'] == [('combat.move', 'near')]


# K7 — Bardic Inspiration is used -------------------------------------------------------------------------------------


def _inspiration(bearer_id: str = ACTOR) -> dict:
    return {
        'id': f'bardic_inspiration:{bearer_id}', 'sourceId': 'bardic_inspiration', 'sourceName': 'Bardic Inspiration',
        'casterId': 'player_9', 'modifiers': {}, 'duration': {'rounds': 100, 'tickOn': 'target_turn_end'},
        'concentration': False, 'consumeOn': None, 'inspirationDie': 'd6', 'expiresOn': 'rest',
    }


def test_bardic_inspiration_turns_a_missed_attack_into_a_hit_and_is_spent(monkeypatch):
    import aidm_server.game_state.orchestration.combat_resolution as combat_resolution_module

    monkeypatch.setattr(combat_resolution_module, 'deterministic_roller', lambda _seed: (lambda _sides: 3))
    fighter = _participant(ACTOR, 'player', band='melee', **{'class': 'Fighter'})
    fighter['activeEffects'] = [_inspiration()]
    state = _armed_state('Fighter', 3, [SWORD], attacker=fighter,
                         enemy=_participant('enemy_1', 'enemy', band='melee', armorClass=14), activeEffects=[_inspiration()])
    resolved = _player_attack(state, weapon=_hud(SWORD), kept=9)
    assert (resolved['hit'], resolved['attackTotal'], resolved['inspiration']) == (True, 15, {'die': 'd6', 'roll': 3})
    after = _apply(state, _attack_followups(state, resolved))['nextState']
    assert _combatant(after, ACTOR)['activeEffects'] == []
    assert after['playerCharacters'][0]['activeEffects'] == []


def test_bardic_inspiration_is_added_to_a_failed_check_and_shown_with_the_roll():
    player = _roller_player(effects=[_inspiration('player_7'), deepcopy(MAGE_ARMOR)])
    roll = resolve_authoritative_player_roll(player=player, rule_type='perception', dc_hint='DC 10', action_intent=None,
                                             roller=lambda _sides: 5)
    assert (roll['total'], roll['outcome'], roll['inspiration']) == (5 + 1 + 5, 'success', {'die': 'd6', 'roll': 5})
    assert 'Bardic Inspiration d6 (5)' in canonical_roll_sentence(roll)
    assert [effect['id'] for effect in json.loads(player.stats)['active_effects']] == ['mage_armor']
    passed = resolve_authoritative_player_roll(player=_roller_player(effects=[_inspiration('player_7')]), rule_type='perception',
                                               dc_hint='DC 5', action_intent=None, roller=lambda _sides: 5)
    assert 'inspiration' not in passed


def test_a_combat_roll_spends_its_effects_through_the_turn_pipeline():
    from aidm_server.game_state.orchestration.turn_pipeline import _player_roll_consume_change

    fighter = _participant(ACTOR, 'player', **{'class': 'Fighter'})
    fighter['activeEffects'] = [_inspiration()]
    state = _state([_actor(fighter, activeEffects=[_inspiration()])], fighter, _participant('enemy_1', 'enemy'))
    roll = {'authoritative': True, 'total': 12, 'consume': [{'participantId': ACTOR, 'effectId': f'bardic_inspiration:{ACTOR}'}]}
    turn = DmTurn(turn_id=77, player_id=1, metadata_json=json.dumps({'authoritative_roll': roll}))
    change = _player_roll_consume_change(turn, state)
    after = _apply(state, [change])['nextState']
    assert _combatant(after, ACTOR)['activeEffects'] == [] and after['playerCharacters'][0]['activeEffects'] == []
    assert _player_roll_consume_change(turn, {**state, 'combat': {**state['combat'], 'status': 'ended'}}) is None


def test_bardic_inspiration_is_added_to_a_failed_save_against_an_enemy_ability_or_a_spell():
    hero = _participant(ACTOR, 'player', **{'class': 'Fighter'})
    hero['activeEffects'] = [_inspiration()]
    combat = _state([_actor(hero)], hero, _participant('enemy_1', 'enemy'))['combat']
    save = resolve_save(hero, {'ability': 'wisdom', 'dc': 15, 'effectOnSuccess': 'none'}, combat, lambda sides: 10 if sides == 20 else 4)
    assert (save['saveTotal'], save['saveSucceeded'], save['inspiration']) == (10 + 3 + 4, True, {'die': 'd6', 'roll': 4})
    assert {'participantId': ACTOR, 'effectId': f'bardic_inspiration:{ACTOR}'} in save['consume']

    spell = spell_payload('Hold Person')
    spell['delivery']['castingAbility'] = 'wisdom'
    held = resolve_targeted_spell({**combat, 'flags': {**combat['flags'], 'activeActorId': 'enemy_1'}}, caster_id='enemy_1', spell=spell,
                                  target_ids=[ACTOR], cast_id='hold', roller=lambda _sides: 5, caster_resources={})
    (target,) = held['resolution']['targets']
    assert (target['delivery']['saveSucceeded'], target['delivery']['inspiration']) == (True, {'die': 'd6', 'roll': 5})
    assert _combatant(held, ACTOR)['activeEffects'] == [] and 'paralyzed' not in _combatant(held, ACTOR)['conditions']


# P1 — striking an object is not a fight -----------------------------------------------------------------------------


@pytest.mark.parametrize(('message', 'creature'), [
    ('I attack the goblin.', True),
    ('I draw my longsword and attack the nearest cutthroat.', True),
    ('I attack the goblin guarding the door.', True),
    ('I kick the door.', False),
    ('I smash the old wooden crate.', False),
    ('I swing my axe at the rope.', False),
    ('I do not attack anyone.', False),
])
def test_a_hostile_action_aimed_at_a_creature_is_told_apart_from_a_strike_at_an_object(message, creature):
    assert hostile_action_targets_creature(message) is creature


def test_the_no_opponent_answer_is_reserved_for_attacks_on_creatures():
    pre_pipeline = {'stateBeforeDm': {'combat': {'status': 'none'}}}
    hint = {'requires_roll': True, 'roll_type': 'attack', 'reason': 'Attack roll'}
    hostile = TurnEngine._no_opponent_roll_veto(pre_pipeline, dict(hint), 'I attack the bandit.')
    assert (hostile['no_target'], hostile['reason']) == (True, 'No opponent is present.')
    assert 'no one here to fight' in hostile['instruction']
    door = TurnEngine._no_opponent_roll_veto(pre_pipeline, dict(hint), 'I kick the door.')
    assert (door['no_target'], door['reason']) == (False, 'No opponent is present.')
    assert 'do not request a roll' in door['instruction'] and 'ordinary action' in door['instruction']


# P2 — Jack of All Trades ------------------------------------------------------------------------------------------------


def test_jack_of_all_trades_adds_half_proficiency_to_unproficient_ability_checks_only():
    bard = _roller_player('Bard', 2)
    assert character_roll_spec(bard, roll_type='stealth')['proficiency'] == {'bonus': 1, 'skills': ['jack_of_all_trades'], 'multiplier': 0}
    assert character_roll_spec(bard, roll_type='initiative')['proficiency']['skills'] == ['jack_of_all_trades']
    for roll_type in ('attack', 'strength_saving_throw', 'death_save'):
        assert 'jack_of_all_trades' not in character_roll_spec(bard, roll_type=roll_type)['proficiency']['skills']
    assert character_roll_spec(_roller_player('Bard', 1), roll_type='stealth')['proficiency']['bonus'] == 0
    assert character_roll_spec(_roller_player('Fighter', 5), roll_type='stealth')['proficiency']['bonus'] == 0
