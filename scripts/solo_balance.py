"""How often one ready-made hero, alone, wins each Play Now encounter.

Each fight runs on the real combat engine (the pack's encounter materialised
with party scaling, the validator and applier for every action, the enemy turn
resolver for every enemy block) with a seeded roller; no narrator, no
database. The hero is at the level the careful route's XP gives at that
point, at full HP, with one Potion of Healing, and follows one policy: drink
the potion at 40% HP or less; a caster casts its best damage spell while slots
last, then its best damage cantrip; anyone else attacks with their best
weapon; always the enemy with the least HP. The enemies plan as the engine
does, quiet-turn and parley limits included. A fight counts as the careful
route meets it: the final binding with the Scribe's true name known and the
Roadwardens appeased, so both of its adjustments apply.

Usage: python scripts/solo_balance.py [--seeds 40]
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace
from typing import Any
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aidm_server.game_state.leveling import level_for_xp  # noqa: E402
from aidm_server.combat.actions import resolve_combat_action  # noqa: E402
from aidm_server.combat.attacks import pc_weapon_attack_profile  # noqa: E402
from aidm_server.combat.modifiers import engaged_with, range_band  # noqa: E402
from aidm_server.combat.end_conditions import check_combat_end, combat_end_change  # noqa: E402
from aidm_server.combat.intent_planner import attach_intents_to_combat  # noqa: E402
from aidm_server.combat.pipeline import (  # noqa: E402
    combat_turn_advance_change,
    deterministic_enemy_intent_plan,
    engine_controlled_turn_is_up,
)
from aidm_server.combat.state import combat_summary_for_dm, participant_is_targetable  # noqa: E402
from aidm_server.damage_dice import normalize_damage_dice_expression  # noqa: E402
from aidm_server.game_state.application.applier import apply_state_changes  # noqa: E402
from aidm_server.game_state.campaign_pack_encounters import triggered_encounter_start  # noqa: E402
from aidm_server.game_state.models import safe_json_dumps  # noqa: E402
from aidm_server.game_state.orchestration.combat_resolution import (  # noqa: E402
    build_dm_combat_context,
    derive_trusted_damage_changes,
    resolve_authoritative_player_attack,
    settle_engine_controlled_turns,
)
from aidm_server.game_state.validation.validator import (  # noqa: E402
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.services.pregen_characters import PREGENERATED_CHARACTER_PRESETS, build_player_from_preset  # noqa: E402
from aidm_server.game_state.models import player_character_from_model  # noqa: E402
from aidm_server.spellbook import authoritative_spell_effect, spell_is_prepared  # noqa: E402

PACK_PATH = REPO_ROOT / 'docs' / 'examples' / 'the_road_of_unremembered_kings_campaign.json'
# Encounters in the order the careful route meets them, with the XP the hero holds on arrival.
ROUTE_XP = (
    ('enc_empty_caravan_pressure', 0),
    ('enc_pale_lantern_detour', 100),
    ('enc_waystation_ambush', 250),
    ('enc_roadwarden_judgment', 500),
    ('enc_hollow_marshal_pursuit', 950),
    ('enc_final_binding', 950),
)
# What the careful route knows when it reaches each encounter (the flags its adjustments read).
ROUTE_FLAGS = {'enc_final_binding': {'true_name_known': True, 'roadwardens_appeased': True}}
POTION_THRESHOLD = 0.4
MAX_ROUNDS = 60
ACTOR_ID = 'player_1'


def _pack() -> dict[str, Any]:
    return json.loads(PACK_PATH.read_text(encoding='utf-8'))


def _hero(preset_id: str, level: int) -> dict[str, Any]:
    preset = next(preset for preset in PREGENERATED_CHARACTER_PRESETS if preset.character_id == preset_id)
    player = build_player_from_preset(replace(preset, level=level), workspace_id='solo_balance', campaign_id=1)
    player.player_id = 1
    return player_character_from_model(player)


def _state(pack: dict[str, Any], hero: dict[str, Any], seed: int, *, flags: dict[str, Any]) -> dict[str, Any]:
    return {
        'sessionId': seed,
        'playerCharacters': [hero],
        'currentScene': {'locationId': 'solo_balance', 'name': 'The road', 'items': [], 'interactables': [], 'hazards': []},
        'locations': [],
        'flags': deepcopy(flags),
        'combat': {'status': 'none', 'round': 1, 'participants': [], 'flags': {}},
        'campaignPack': {
            'packId': pack['packId'],
            'catalog': {'enemies': deepcopy(pack['enemies']), 'encounters': deepcopy(pack['encounters'])},
        },
    }


def _apply(state: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    validation = validate_state_changes(state=state, changes=changes)
    return apply_state_changes(state, validated_changes_for_application(validation))['nextState']


def _end_if_decided(state: dict[str, Any], turn_id: int) -> tuple[dict[str, Any], bool]:
    reason = check_combat_end(state.get('combat') or {})
    if reason:
        state = _apply(state, [combat_end_change(turn_id, reason)])
    return state, bool(reason) or (state.get('combat') or {}).get('status') not in {'starting', 'active'}


def _participants(state: dict[str, Any], team: str) -> list[dict[str, Any]]:
    return [
        participant
        for participant in (state.get('combat') or {}).get('participants') or []
        if participant.get('team') == team
    ]


def _weakest_enemy(state: dict[str, Any]) -> dict[str, Any] | None:
    enemies = [enemy for enemy in _participants(state, 'enemy') if participant_is_targetable(enemy)]
    return min(enemies, key=lambda enemy: (enemy['hp']['current'], enemy['id'])) if enemies else None


def _average(dice: Any) -> float:
    expression = normalize_damage_dice_expression(dice) or ''
    total = 0.0
    for term in expression.replace('-', '+-').split('+'):
        if 'd' in term:
            count, sides = term.split('d')
            total += int(count or 1) * (int(sides) + 1) / 2
        elif term.strip('-').isdigit():
            total += int(term)
    return total


def _spell_average(effect: dict[str, Any]) -> float:
    delivery = effect.get('delivery') or {}
    darts = delivery.get('darts') or {}
    per_hit = sum(_average(item.get('dice')) for item in effect.get('effects') or [] if item.get('kind') == 'damage')
    return per_hit * max(int(darts.get('count') or 1), int(delivery.get('attacks') or 1))


def _damage_spells(hero: dict[str, Any]) -> list[tuple[float, str, int]]:
    """Single-target damage spells the hero has prepared: (average damage, name, level), best first."""

    spellbook = hero.get('spellbook') or {}
    spells = []
    for spell in spellbook.get('knownSpells') or []:
        effect = authoritative_spell_effect(spell.get('name'))
        if not effect or not spell_is_prepared(spellbook, spell['name']):
            continue
        if (effect.get('target') or {}).get('relation') != 'enemy' or (effect.get('target') or {}).get('area'):
            continue
        average = _spell_average(effect)
        if average > 0 and effect.get('castingTime', 'action') == 'action':
            spells.append((average, spell['name'], int(effect['level'])))
    return sorted(spells, reverse=True)


def _slot_for(hero: dict[str, Any], level: int) -> int | None:
    slots = (hero.get('spellResources') or {}).get('slots') or {}
    open_levels = sorted(int(key) for key, slot in slots.items() if int(key) >= level and int(slot.get('current') or 0) > 0)
    return open_levels[0] if open_levels else None


def _actor(state: dict[str, Any]) -> dict[str, Any]:
    return next(actor for actor in state['playerCharacters'] if actor['id'] == ACTOR_ID)


def _declared(state: dict[str, Any], action: dict[str, Any], turn_id: int) -> dict[str, Any] | None:
    """The state after one declared action, or None when the validator refuses it."""

    result = validate_declared_actions(
        state=state,
        declared_actions=[{'id': 'act_001', 'actorId': ACTOR_ID, 'confidence': 1.0, 'requiresDMResolution': False, **action}],
        current_turn=turn_id,
        expected_actor_id=ACTOR_ID,
    )
    changes = [change for change in result['immediateChanges'] if isinstance(change, dict)]
    validation = validate_state_changes(state=state, changes=changes)
    applied = apply_state_changes(state, validated_changes_for_application(validation))
    return applied['nextState'] if applied['appliedChanges'] else None


def _drink_potion(state: dict[str, Any], turn_id: int) -> dict[str, Any] | None:
    hero = _actor(state)
    health = hero['health']
    if health['currentHp'] > POTION_THRESHOLD * health['maxHp']:
        return None
    potion = next((item for item in hero['inventory']['items'] if item.get('name') == 'Potion of Healing'), None)
    if potion is None:
        return None
    return _declared(
        state,
        {'type': 'inventory.use', 'itemId': potion['id'], 'itemName': potion['name'], 'targetId': ACTOR_ID, 'quantity': 1},
        turn_id,
    )


def _close_in(state: dict[str, Any], target: dict[str, Any], turn_id: int) -> dict[str, Any]:
    """The hero's move: close into melee with the target, as the HUD's advance does."""

    hero = next(participant for participant in _participants(state, 'player') if participant['id'] == ACTOR_ID)
    if range_band(hero) == 'melee' and target['id'] in engaged_with(hero):
        return state
    advance = resolve_combat_action(
        state=state,
        combat_action={'action_type': 'advance', 'action_id': 'combat.advance', 'target_id': target['id']},
        actor_id=ACTOR_ID,
        turn_id=turn_id,
    )
    return _apply(state, advance['changes']) if advance and advance.get('ok') is not False else state


def _cast(state: dict[str, Any], target: dict[str, Any], turn_id: int) -> dict[str, Any] | None:
    hero = _actor(state)
    for _average_damage, name, level in _damage_spells(hero):
        cast_level = 0 if level == 0 else _slot_for(hero, level)
        if cast_level is None:
            continue
        bands = (authoritative_spell_effect(name).get('target') or {}).get('rangeBands') or []
        after = _declared(
            _close_in(state, target, turn_id) if bands == ['melee'] else state,
            {'type': 'spell.cast', 'spellName': name, 'castLevel': cast_level, 'targetIds': [target['id']], 'resourcePool': 'auto'},
            turn_id,
        )
        if after is not None:
            return after
    return None


def _best_weapon(hero: dict[str, Any], participant: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    weapons = [item for item in hero['inventory']['items'] if item.get('type') == 'weapon' or item.get('damage')]
    profiles = [(weapon, pc_weapon_attack_profile(hero, participant, weapon)) for weapon in weapons]
    profiles = [(weapon, profile) for weapon, profile in profiles if profile['damage'].get('dice')]
    return max(
        profiles,
        key=lambda pair: (_average(pair[1]['damage']['dice']) + pair[1]['damage'].get('bonus', 0), pair[1]['attackBonus']),
    )


def _attack(state: dict[str, Any], target: dict[str, Any], turn_id: int, rng: random.Random) -> dict[str, Any]:
    state = _close_in(state, target, turn_id)
    hero = _actor(state)
    participant = next(participant for participant in _participants(state, 'player') if participant['id'] == ACTOR_ID)
    weapon, profile = _best_weapon(hero, participant)
    d20 = rng.randint(1, 20)
    resolved = resolve_authoritative_player_attack(
        state=state,
        actor_id=ACTOR_ID,
        turn_id=turn_id,
        action_intent={
            'kind': 'combat',
            'combat': {
                'authoritative': True,
                'action_type': 'attack',
                'target_id': target['id'],
                'weapon_id': weapon['id'],
                'weapon_name': weapon['name'],
                'damage_dice': profile['damage']['dice'],
                'damage_type': profile['damage'].get('type') or 'slashing',
                'ranged': False,
            },
        },
        authoritative_roll={
            'authoritative': True,
            'kept': d20,
            'total': d20 + profile['attackBonus'],
            'ability': {'key': profile['ability']},
            'modifier_breakdown': {'ability_modifier': profile['damage'].get('bonus', 0)},
        },
    )
    if resolved is None:
        return state
    packet = {'combatState': {'playerResolvedAction': resolved, 'trustedDamageEvents': [resolved]}}
    trusted = derive_trusted_damage_changes(
        state=state, dm_context_packet=packet, actor_id=ACTOR_ID, turn_id=turn_id, already_applied_changes=[]
    )
    return _apply(state, trusted.resolved)


def _end_turn(state: dict[str, Any], turn_id: int) -> dict[str, Any]:
    """End the hero's turn as the pipeline does: the enemy block after the hero acts, then initiative moves on."""

    combat = state['combat']
    block = build_dm_combat_context(
        state=state,
        combat_context=combat_summary_for_dm(attach_intents_to_combat(combat, deterministic_enemy_intent_plan(combat))),
        pending_rolls=[],
        resolved_player_roll=False,
        turn_id=turn_id,
    ) or {}
    state = _apply(
        state,
        derive_trusted_damage_changes(
            state=state, dm_context_packet={'combatState': block}, actor_id='', turn_id=turn_id, already_applied_changes=[]
        ).all_changes,
    )
    state, over = _end_if_decided(state, turn_id)
    if over:
        return state
    turn = SimpleNamespace(
        turn_id=turn_id,
        player_id=1,
        metadata_json=safe_json_dumps(
            {'action_intent': {'kind': 'combat', 'combat': {'authoritative': True, 'economy': {'endsTurn': True}}}}, {}
        ),
    )
    advance = combat_turn_advance_change(state=state, turn=turn, actor_id=ACTOR_ID)
    return _apply(state, [advance]) if advance else state


@contextmanager
def _seeded_dice(rng: random.Random) -> Iterator[None]:
    """Every engine roll that would draw fresh randomness draws from ``rng`` instead."""

    with mock.patch('secrets.randbelow', lambda bound: rng.randrange(bound)), mock.patch(
        'aidm_server.game_state.orchestration.combat_resolution._random_roll', lambda sides: rng.randint(1, max(1, int(sides)))
    ):
        yield


def fight(preset_id: str, encounter_id: str, seed: int, *, level: int, pack: dict[str, Any] | None = None) -> bool:
    """True when the hero, alone, wins (the enemies are defeated, flee, surrender or bargain)."""

    pack = pack or _pack()
    rng = random.Random(f'{preset_id}:{encounter_id}:{seed}')
    encounter = next(record for record in pack['encounters'] if record['id'] == encounter_id)
    state = _state(pack, _hero(preset_id, level), seed, flags=ROUTE_FLAGS.get(encounter_id, {}))
    with _seeded_dice(rng):
        start = triggered_encounter_start(state, encounter, turn_id=seed * 1000, cause_id='solo_balance')
        state = _apply(state, [start])
        for round_number in range(1, MAX_ROUNDS + 1):
            turn_id = seed * 1000 + round_number
            if engine_controlled_turn_is_up(state['combat']):
                state = settle_engine_controlled_turns(state, turn_id=turn_id, already_applied_changes=[]).state
            state, over = _end_if_decided(state, turn_id)
            if over:
                break
            target = _weakest_enemy(state)
            if target is None:
                break
            after = _drink_potion(state, turn_id)
            if after is None and _actor(state).get('spellResources'):
                after = _cast(state, target, turn_id)
            state = after if after is not None else _attack(state, target, turn_id, rng)
            state, over = _end_if_decided(state, turn_id)
            if over:
                break
            state = _end_turn(state, turn_id)
            state, over = _end_if_decided(state, turn_id)
            if over:
                break
    end_reason = str(((state.get('combat') or {}).get('flags') or {}).get('endReason') or '')
    return end_reason in {'all_enemies_defeated', 'enemies_fled', 'enemies_surrendered', 'negotiated_resolution'}


def win_rates(
    preset_ids: list[str] | None = None,
    *,
    seeds: int = 40,
    encounters: tuple[tuple[str, int], ...] = ROUTE_XP,
) -> dict[str, dict[str, float]]:
    """Win rate by hero and encounter over ``seeds`` seeded fights each."""

    pack = _pack()
    ids = preset_ids or [preset.character_id for preset in PREGENERATED_CHARACTER_PRESETS]
    return {
        preset_id: {
            encounter_id: sum(
                fight(preset_id, encounter_id, seed, level=level_for_xp(xp), pack=pack) for seed in range(1, seeds + 1)
            ) / seeds
            for encounter_id, xp in encounters
        }
        for preset_id in ids
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--seeds', type=int, default=40)
    args = parser.parse_args(argv)
    rates = win_rates(seeds=args.seeds)
    encounter_ids = [encounter_id for encounter_id, _xp in ROUTE_XP]
    width = max(len(encounter_id) for encounter_id in encounter_ids)
    print(f"{'encounter':<{width}}  level  " + '  '.join(f'{preset_id:>11}' for preset_id in rates))
    for encounter_id, xp in ROUTE_XP:
        cells = '  '.join(f'{rates[preset_id][encounter_id]:>10.0%} ' for preset_id in rates)
        print(f'{encounter_id:<{width}}  {level_for_xp(xp):>5}  {cells}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
