"""Fights about something: an encounter's stakes, its enemies' goals, and what the end of the fight decides.

Stakes (``encounters[].stakes`` when it is a list; a string there stays the operator's note). Each is
``{"id", "name", "kind": "item" | "person" | "route", "itemId" | "npcId" | "objectId", "when"?}``; a person stake whose
``when`` flag condition does not hold when the fight starts is not part of it. The fight keeps them in its flags
(``combat.flags.stakes``) with their state:

- item: ``in_place`` (where it was found), ``held_by_hero``, ``on_ground``, ``held_by_enemy``, ``taken`` (left the
  fight in enemy hands) and, once the fight ends with it not in enemy hands, ``secured``; a ``concealed`` item reads
  ``hidden`` to the players;
- person: ``safe``, ``threatened`` and ``hurt`` (one and two successful enemy actions against them; out of play until
  the next long rest, never killed) or ``taken``; a hero ``guarding`` them stops both;
- route: ``open`` or ``blocked`` (its object's ``blocked`` state).

Items and routes are read from the world (:func:`sync_stakes`): who holds the item, whether the way is blocked. A
route or item stake's ``startWhen`` (``[{"flags": {...}, "state": "<state>"}]``) sets its state when the fight is
created, the first entry whose flags hold (an unset flag counting as false) deciding; a route started ``blocked`` is
``held`` blocked until an enemy clears it, whatever its object's state.

Enemy goals (engine behaviour, never a model's). ``enemyGoal: {"type": "take_and_flee", "stakeId",
"routeStakeId"}`` makes the first enemy of the first group the runner (re-chosen if it falls). Its turn, in order:
holding the item it moves one band outward, and from ``far`` escapes with it when the route is ``open`` or spends
its action clearing it (Strength or Dexterity against DC 12) when it is ``blocked``; a concealed item it searches out
(an action, no roll); an item on the ground in its reach it takes (a hero guarding it is first beaten: Strength
against their Athletics); a hero holding it it attacks, and on a hit against a hero at half their hit points or less
wrests it instead of hurting them (Strength against Athletics); otherwise it closes on the item. The other enemies
attack whoever is engaged with or nearest the runner. A hero who drops holding the item drops it; so does an enemy
that falls, yields or is bound. When the item is ``taken`` the others withdraw and the fight ends
(``enemy_goal_met``). ``secondaryGoal: {"type": "silence", "stakeId"}``: one enemy that is not the runner goes for
the person each turn it is not engaged.

The end of the fight. The final state of every stake is kept at ``stakes[encounterId][stakeId]`` in the session
state; every ``results`` entry whose ``when`` holds (``stakes``, ``end``, ``flags``, ``captives``) applies in order:
``setsFlags``, a scene ``fact``, ``affects`` (dispositions), ``givesItems`` (to the hero whose turn ended the fight,
else the first hero in it; :func:`given_items_changes`), a ``lead`` (``leads``, journal ``LEADS``, beat ``A lead —
…``) and its ``text``, told as written. ``onDefeat`` (see :mod:`aidm_server.combat.defeat`) replaces the re-arm of a
beaten party.

Floors (:func:`encounter_floors`): ``minEnemies`` and ``enemyGroups[].minHp`` hold after party scaling for a party
under four; a ``take_and_flee`` encounter without them gets a runner worth stopping.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from aidm_server.game_state.models import stable_change_id

STAKE_KINDS = ('item', 'person', 'route')
ITEM_STATES = ('in_place', 'held_by_hero', 'on_ground', 'held_by_enemy', 'taken', 'secured')
PERSON_STATES = ('safe', 'threatened', 'hurt', 'taken')
ROUTE_STATES = ('open', 'blocked')
STATES_BY_KIND = {'item': ITEM_STATES, 'person': PERSON_STATES, 'route': ROUTE_STATES}
GOAL_TYPES = ('take_and_flee',)
SECONDARY_GOAL_TYPES = ('silence',)
STAKE_CHANGE = 'scene.stake'
STAKE_SOURCE = 'fight_stakes'
STAKES_KEY = 'stakes'
LEADS_KEY = 'leads'
GOAL_INTENT = 'stake_goal'
ENEMY_GOAL_MET = 'enemy_goal_met'
CLEAR_DC = 12
# The fight's end reasons a ``results`` entry's ``end`` may name, besides the raw reason.
END_ALIASES = {
    'all_enemies_defeated': {'victory', 'defeated'},
    'enemies_fled': {'victory', 'fled'},
    'enemies_surrendered': {'surrender', 'surrendered'},
    'negotiated_resolution': {'negotiation'},
    'objective_failed': {'defeat'},
    'players_fled': {'retreat', 'fled'},
    ENEMY_GOAL_MET: {'enemy_goal_met'},
}
DEFAULT_FLOORS = {'withSilence': 2, 'alone': 1}
RUNNER_MIN_HP = {1: 12, 2: 16, 3: 16}
ACTIVE_COMBAT_STATUSES = frozenset({'starting', 'active'})


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    return [_text(item) for item in value if _text(item)] if isinstance(value, (list, tuple)) else []


def _combat(state: Mapping[str, Any]) -> dict[str, Any]:
    combat = state.get('combat') if isinstance(state, Mapping) else None
    return combat if isinstance(combat, dict) else {}


def _scene(state: Mapping[str, Any]) -> dict[str, Any]:
    scene = state.get('currentScene') if isinstance(state, Mapping) else None
    return scene if isinstance(scene, dict) else {}


def _flags(state: Mapping[str, Any]) -> dict[str, Any]:
    return state.get('flags') if isinstance(state.get('flags'), dict) else {}


# Authoring --------------------------------------------------------------------------------------------------------


def encounter_stakes(encounter: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The encounter's stake records (a list under ``stakes``); [] for a string note or none."""

    stakes = encounter.get('stakes')
    found = []
    for raw in stakes if isinstance(stakes, list) else []:
        if not isinstance(raw, dict) or not _text(raw.get('id')) or _text(raw.get('kind')) not in STAKE_KINDS:
            continue
        found.append(
            {
                'id': _text(raw['id']),
                'name': _text(raw.get('name')) or _text(raw['id']).replace('_', ' '),
                'kind': _text(raw['kind']),
                **({'itemId': _text(raw.get('itemId'))} if _text(raw.get('itemId')) else {}),
                **({'npcId': _text(raw.get('npcId'))} if _text(raw.get('npcId')) else {}),
                **({'objectId': _text(raw.get('objectId'))} if _text(raw.get('objectId')) else {}),
                **({'when': deepcopy(raw['when'])} if isinstance(raw.get('when'), dict) else {}),
                **({'startWhen': _dicts(raw['startWhen'])} if _dicts(raw.get('startWhen')) else {}),
            }
        )
    return found


def enemy_goal(encounter: Mapping[str, Any]) -> dict[str, Any] | None:
    goal = encounter.get('enemyGoal')
    if not isinstance(goal, dict) or _text(goal.get('type')) not in GOAL_TYPES or not _text(goal.get('stakeId')):
        return None
    return {key: _text(goal.get(key)) for key in ('type', 'stakeId', 'routeStakeId') if _text(goal.get(key))}


def secondary_goal(encounter: Mapping[str, Any]) -> dict[str, Any] | None:
    goal = encounter.get('secondaryGoal')
    if not isinstance(goal, dict) or _text(goal.get('type')) not in SECONDARY_GOAL_TYPES or not _text(goal.get('stakeId')):
        return None
    return {'type': _text(goal['type']), 'stakeId': _text(goal['stakeId'])}


def encounter_results(encounter: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [deepcopy(entry) for entry in encounter.get('results') or [] if isinstance(entry, dict)]


def on_defeat(encounter: Mapping[str, Any]) -> dict[str, Any] | None:
    """The encounter's ``onDefeat`` object (W9); an older consequence list is not one."""

    value = encounter.get('onDefeat')
    return deepcopy(value) if isinstance(value, dict) else None


def encounter_floors(encounter: Mapping[str, Any], *, party_size: int) -> dict[str, Any]:
    """The floors that hold for this party: ``minEnemies``, ``minHp`` by enemy id and ``runnerMinHp``, with their
    ``source`` (``pack`` or ``default``); {} for a party of four or more or an encounter without any."""

    if party_size >= 4 or party_size < 1:
        return {}
    groups = [group for group in encounter.get('enemyGroups') or [] if isinstance(group, dict)]
    min_hp = {
        _text(group.get('enemyId')): int(group['minHp'])
        for group in groups
        if _text(group.get('enemyId')) and isinstance(group.get('minHp'), int) and not isinstance(group.get('minHp'), bool)
    }
    min_enemies = encounter.get('minEnemies')
    if (isinstance(min_enemies, int) and not isinstance(min_enemies, bool)) or min_hp:
        floors: dict[str, Any] = {'source': 'pack'}
        if isinstance(min_enemies, int) and not isinstance(min_enemies, bool):
            floors['minEnemies'] = max(1, min(6, min_enemies))
        if min_hp:
            floors['minHp'] = min_hp
        return floors
    if enemy_goal(encounter) is None:
        return {}
    return {
        'source': 'default',
        'minEnemies': DEFAULT_FLOORS['withSilence'] if secondary_goal(encounter) else DEFAULT_FLOORS['alone'],
        'runnerMinHp': RUNNER_MIN_HP[party_size],
    }


# Stakes in a fight ------------------------------------------------------------------------------------------------


def _scene_item(state: Mapping[str, Any], item_id: str) -> dict[str, Any] | None:
    return next((item for item in _dicts(_scene(state).get('items')) if item_id in {_text(item.get('id')), _text(item.get('sourceItemId'))}), None)


def _holder(state: Mapping[str, Any], item_id: str) -> dict[str, Any] | None:
    return next(
        (
            hero
            for hero in _dicts(state.get('playerCharacters'))
            if any(item_id in {_text(item.get('id')), _text(item.get('sourceItemId'))} for item in _dicts(_dict(hero.get('inventory')).get('items')))
        ),
        None,
    )


def _carrier(combat: Mapping[str, Any], item_id: str) -> dict[str, Any] | None:
    return next(
        (participant for participant in _dicts(combat.get('participants')) if item_id in {_text(item.get('id')) for item in _dicts(participant.get('carrying'))}),
        None,
    )


def _object(state: Mapping[str, Any], object_id: str) -> dict[str, Any]:
    scene = _scene(state)
    return next((entry for key in ('interactables', 'hazards') for entry in _dicts(scene.get(key)) if _text(entry.get('id')) == object_id), {})


def _world_state(state: Mapping[str, Any], stake: Mapping[str, Any]) -> str | None:
    """The state an item or route stake reads from the world now; None for a person (kept as it is)."""

    kind = stake.get('kind')
    if kind == 'route':
        return 'blocked' if stake.get('held') is True or _object(state, _text(stake.get('objectId'))).get('blocked') is True else 'open'
    if kind != 'item' or stake.get('state') in {'taken', 'secured'}:
        return None
    item_id = _text(stake.get('itemId'))
    if _carrier(_combat(state), item_id) is not None:
        return 'held_by_enemy'
    if _holder(state, item_id) is not None:
        return 'held_by_hero'
    if _scene_item(state, item_id) is not None:
        return 'on_ground' if stake.get('moved') is True else 'in_place'
    return _text(stake.get('state')) or 'in_place'


def start_state(state: Mapping[str, Any], stake: Mapping[str, Any]) -> str | None:
    """The state a route or item stake's ``startWhen`` gives it as the fight is created: the first entry whose
    ``flags`` hold (an unset flag counting as false) and whose ``state`` the stake's kind has; None."""

    from aidm_server.game_state.campaign_pack_encounters import values_match

    if stake.get('kind') not in {'item', 'route'}:
        return None
    flags = _flags(state)
    for entry in _dicts(stake.get('startWhen')):
        wanted = _text(entry.get('state'))
        if wanted not in STATES_BY_KIND[_text(stake.get('kind'))]:
            continue
        if all(values_match(flags[key], value) if key in flags else value is False for key, value in _dict(entry.get('flags')).items()):
            return wanted
    return None


def initial_stakes(state: Mapping[str, Any], encounter: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The fight's ``combat.stakes`` as it starts (person stakes whose ``when`` does not hold are left out; a
    ``startWhen`` that holds sets the state, and a route it blocks is ``held``)."""

    from aidm_server.game_state.campaign_pack_encounters import flag_condition_holds

    stakes = []
    for stake in encounter_stakes(encounter):
        if stake['kind'] == 'person' and isinstance(stake.get('when'), dict) and not flag_condition_holds(_flags(state), stake['when']):
            continue
        record = {key: value for key, value in stake.items() if key not in {'when', 'startWhen'}}
        started = start_state(state, stake)
        if started == 'blocked':
            record['held'] = True
        record['state'] = started or ('safe' if stake['kind'] == 'person' else _world_state(state, {**record, 'state': 'in_place'}) or 'in_place')
        stakes.append(record)
    return stakes


def combat_goal_flags(state: Mapping[str, Any], encounter: Mapping[str, Any], stakes: list[dict[str, Any]], enemies: list[dict[str, Any]]) -> dict[str, Any]:
    """The combat flags that carry what the fight is about: its ``stakes``, the encounter's ``enemyGoal`` and
    ``secondaryGoal``, and the ``runnerId``."""

    flags: dict[str, Any] = {STAKES_KEY: stakes} if stakes else {}
    stake_ids = {stake['id'] for stake in stakes}
    goal = enemy_goal(encounter)
    if goal and goal['stakeId'] in stake_ids:
        flags['enemyGoal'] = goal
        if enemies:
            flags['runnerId'] = _text(enemies[0].get('id'))
    secondary = secondary_goal(encounter)
    if secondary and secondary['stakeId'] in stake_ids:
        flags['secondaryGoal'] = secondary
    return flags


def _stakes_of(combat: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The fight's stakes, kept in its flags (the combat record's own keys are normalised away)."""

    return _dicts(_dict(combat.get('flags')).get(STAKES_KEY))


def fight_stakes(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _stakes_of(_combat(state))


def stake_by_id(combat: Mapping[str, Any], stake_id: Any) -> dict[str, Any] | None:
    return next((stake for stake in _stakes_of(combat) if stake.get('id') == _text(stake_id)), None)


def sync_stakes(state: dict[str, Any], *, turn_id: Any, forced: Mapping[str, Any] | None = None) -> None:
    """Read each item and route stake from the world again; ``forced`` states (an affordance's ``stakes``) win.

    A stake whose state changes records the ``changedTurn``."""

    combat = _combat(state)
    if _text(combat.get('status')).lower() not in ACTIVE_COMBAT_STATUSES:
        return
    forced = forced or {}
    for stake in _stakes_of(combat):
        before = stake.get('state')
        wanted = _text(forced.get(stake.get('id')))
        now = wanted if wanted in STATES_BY_KIND.get(_text(stake.get('kind')), ()) else _world_state(state, stake)
        if stake.get('kind') == 'item':
            item = _scene_item(state, _text(stake.get('itemId')))
            stake['concealed'] = bool(item and item.get('concealed') is True)
            if now in {'held_by_hero', 'held_by_enemy'}:
                stake['moved'] = True
            holder = _holder(state, _text(stake.get('itemId'))) if now == 'held_by_hero' else None
            if holder is not None:
                stake['holderId'] = _text(holder.get('id'))
            else:
                stake.pop('holderId', None)
        if now and now != before:
            stake['state'] = now
            stake['changedTurn'] = turn_id


STATE_WORDS = {
    'in_place': 'where it lies',
    'on_ground': 'on the ground',
    'held_by_hero': '{hero} has it',
    'held_by_enemy': '{enemy} has it',
    'taken': 'taken',
    'secured': 'secured',
    'safe': 'safe',
    'threatened': 'threatened',
    'hurt': 'hurt',
    'open': 'open',
    'blocked': 'barred',
}


def capitalized(text: str) -> str:
    return f'{text[:1].upper()}{text[1:]}'


def _enemy_phrase(participant: Mapping[str, Any]) -> str:
    """``the cutthroat`` for "Ashmarked Cutthroat 2": the last word of its name; a proper name stands alone."""

    name = _text(participant.get('name'))
    if participant.get('proper') is True:
        return name
    words = [word for word in name.split() if not word.isdigit()]
    return f'the {words[-1].lower()}' if words else 'an enemy'


def stake_words(state: Mapping[str, Any], stake: Mapping[str, Any]) -> str:
    """The stake's state in words: ``the cutthroat has it``, ``on the ground``, ``safe``, ``barred``."""

    stake_state = _text(stake.get('state'))
    if stake.get('kind') == 'item' and stake.get('concealed') is True and stake_state in {'in_place', 'on_ground'}:
        return 'hidden'
    if stake_state == 'held_by_enemy':
        carrier = _carrier(_combat(state), _text(stake.get('itemId'))) or {}
        return f"{_enemy_phrase(carrier) if carrier else 'an enemy'} has it"
    if stake_state == 'held_by_hero':
        holder = _holder(state, _text(stake.get('itemId'))) or {}
        return f"{_text(holder.get('name')) or 'a hero'} has it"
    return STATE_WORDS.get(stake_state, stake_state.replace('_', ' '))


def stakes_view(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The player view of a fight's stakes: ``{"id", "name", "kind", "state", "words"}``."""

    return [
        {
            'id': _text(stake.get('id')),
            'name': _text(stake.get('name')),
            'kind': _text(stake.get('kind')),
            'state': _text(stake.get('state')),
            'words': stake_words(state, stake),
        }
        for stake in fight_stakes(state)
    ]


def at_stake_line(state: Mapping[str, Any], enemy_actions: list[dict[str, Any]] | None = None) -> str:
    """``the road-stone fragment (the cutthroat has it); Tovin Bell (safe); the yard gate (open)``; '' without stakes.

    ``enemy_actions`` are enemy turns resolved this turn whose changes apply after narration: the line says where the
    stakes stand once they have."""

    changes = [change for index, action in enumerate(enemy_actions or []) for change in stake_changes(action, turn_id=None, index=index)]
    if changes and fight_stakes(state):
        projected = deepcopy(dict(state))
        for change in changes:
            apply_stake_change(projected, change)
        state = projected
    return '; '.join(f"{entry['name']} ({entry['words']})" for entry in stakes_view(state))


# Guarding ---------------------------------------------------------------------------------------------------------


def guard_of(combat: Mapping[str, Any], target_id: str) -> dict[str, Any] | None:
    """The hero standing guard over this person or item in the fight, able to act; None when nobody does."""

    from aidm_server.combat.modifiers import active_effects, can_act
    from aidm_server.game_state.orchestration.scene_plans import GUARDING_SOURCE

    for participant in _dicts(combat.get('participants')):
        if _text(participant.get('team')).lower() != 'player' or not can_act(participant):
            continue
        if any(effect['sourceId'] == GUARDING_SOURCE and _text(effect.get('guardingId')) == target_id for effect in active_effects(participant)):
            return participant
    return None


# Enemy goals: planning --------------------------------------------------------------------------------------------


def _standing(participant: Mapping[str, Any]) -> bool:
    from aidm_server.combat.modifiers import can_act
    from aidm_server.combat.state import participant_is_targetable

    return participant_is_targetable(participant) and can_act(participant)


def runner(combat: Mapping[str, Any]) -> dict[str, Any] | None:
    """The enemy carrying out ``take_and_flee``: the one named, else (it fell) the first enemy still standing."""

    flags = _dict(combat.get('flags'))
    if not isinstance(flags.get('enemyGoal'), dict):
        return None
    enemies = [participant for participant in _dicts(combat.get('participants')) if _text(participant.get('team')).lower() == 'enemy']
    named = next((enemy for enemy in enemies if _text(enemy.get('id')) == _text(flags.get('runnerId'))), None)
    if named is not None and _standing(named):
        return named
    carrying = next((enemy for enemy in enemies if _dicts(enemy.get('carrying')) and _standing(enemy)), None)
    return carrying or next((enemy for enemy in enemies if _standing(enemy)), None)


def silencer(combat: Mapping[str, Any]) -> dict[str, Any] | None:
    flags = _dict(combat.get('flags'))
    if not isinstance(flags.get('secondaryGoal'), dict):
        return None
    run = runner(combat)
    return next(
        (
            enemy
            for enemy in _dicts(combat.get('participants'))
            if _text(enemy.get('team')).lower() == 'enemy' and _standing(enemy) and enemy is not run
        ),
        None,
    )


def _band(participant: Mapping[str, Any]) -> str:
    from aidm_server.combat.modifiers import range_band

    return range_band(participant)


def _best_attack(enemy: Mapping[str, Any]) -> str:
    from aidm_server.combat.attacks import ability_damage
    from aidm_server.combat.state import combat_ability_resolution_mode

    for ability in enemy.get('abilities') or []:
        if isinstance(ability, dict) and combat_ability_resolution_mode(ability) in {'attack', 'attack_with_save'} and ability_damage(ability).get('dice'):
            return _text(ability.get('id'))
    return ''


def _nearest_hero(combat: Mapping[str, Any], to: Mapping[str, Any]) -> dict[str, Any] | None:
    from aidm_server.combat.modifiers import engaged_with
    from aidm_server.combat.positioning import RANGE_BANDS

    heroes = [participant for participant in _dicts(combat.get('participants')) if _text(participant.get('team')).lower() == 'player' and _standing(participant)]
    engaged = set(engaged_with(to))
    on_it = [hero for hero in heroes if _text(hero.get('id')) in engaged]
    if on_it:
        return on_it[0]
    order = {band: index for index, band in enumerate(RANGE_BANDS)}
    return min(heroes, key=lambda hero: abs(order.get(_band(hero), 1) - order.get(_band(to), 1)), default=None)


def _goal(enemy: Mapping[str, Any], goal: str, reason: str, **fields: Any) -> dict[str, Any]:
    return {
        'enemyId': _text(enemy.get('id')),
        'intentType': GOAL_INTENT,
        'goal': goal,
        'reason': reason,
        'brainSource': 'stake_goal',
        'selectionMethod': 'stake_goal',
        **fields,
    }


def _attack(enemy: Mapping[str, Any], target: Mapping[str, Any], reason: str, **fields: Any) -> dict[str, Any] | None:
    ability_id = _best_attack(enemy)
    if not ability_id:
        return None
    return {
        'enemyId': _text(enemy.get('id')),
        'intentType': 'attack',
        'targetId': _text(target.get('id')),
        'abilityId': ability_id,
        'reason': reason,
        'brainSource': 'stake_goal',
        'selectionMethod': 'stake_goal',
        **fields,
    }


def runner_intent(combat: Mapping[str, Any], enemy: Mapping[str, Any]) -> dict[str, Any] | None:
    """The runner's move this turn (the module docstring's order)."""

    flags = _dict(combat.get('flags'))
    goal = _dict(flags.get('enemyGoal'))
    stake = stake_by_id(combat, goal.get('stakeId'))
    if stake is None or stake.get('state') in {'taken', 'secured'}:
        return None
    item_id = _text(stake.get('itemId'))
    route = stake_by_id(combat, goal.get('routeStakeId')) if goal.get('routeStakeId') else None
    if _carrier(combat, item_id) is enemy:
        if _band(enemy) in {'far', 'distant'}:
            if route is not None and route.get('state') == 'blocked':
                return _goal(enemy, 'clear', f"Clear {route.get('name')} to get away.", stakeId=route['id'])
            return _goal(enemy, 'escape', f"Escape with {stake.get('name')}.", stakeId=stake['id'])
        return _goal(enemy, 'flee', f"Run with {stake.get('name')}.", stakeId=stake['id'])
    if stake.get('state') == 'held_by_hero':
        holder = next(
            (
                participant
                for participant in _dicts(combat.get('participants'))
                if _text(participant.get('id')) == _text(stake.get('holderId'))
            ),
            None,
        )
        if holder is not None and _standing(holder):
            return _attack(enemy, holder, f"Get {stake.get('name')} back.", wrestStakeId=stake['id'])
    if stake.get('state') in {'in_place', 'on_ground'}:
        if stake.get('concealed') is True:
            return _goal(enemy, 'search', f"Find where {stake.get('name')} went.", stakeId=stake['id'])
        if _band(enemy) in {'melee', 'near'}:
            return _goal(enemy, 'take', f"Take {stake.get('name')}.", stakeId=stake['id'])
        return _goal(enemy, 'close', f"Go for {stake.get('name')}.", stakeId=stake['id'])
    return None


def silence_intent(combat: Mapping[str, Any], enemy: Mapping[str, Any]) -> dict[str, Any] | None:
    from aidm_server.combat.modifiers import engaged_with

    flags = _dict(combat.get('flags'))
    stake = stake_by_id(combat, _dict(flags.get('secondaryGoal')).get('stakeId'))
    if stake is None or stake.get('state') in {'hurt', 'taken'} or engaged_with(enemy):
        return None
    guard = guard_of(combat, _text(stake.get('npcId')))
    if guard is not None:
        return _attack(enemy, guard, f"Get past {_text(guard.get('name'))} to {stake.get('name')}.", guardedStakeId=stake['id'])
    return _goal(enemy, 'silence', f"Silence {stake.get('name')}.", stakeId=stake['id'])


def goal_intents(combat: Mapping[str, Any], intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The planner's intents with the encounter's goals laid over them: the runner's, the silencer's, and the others
    attacking whoever is engaged with or nearest the runner."""

    flags = _dict(combat.get('flags'))
    if not isinstance(flags.get('enemyGoal'), dict) and not isinstance(flags.get('secondaryGoal'), dict):
        return intents
    run = runner(combat)
    quiet = silencer(combat)
    planned = []
    for intent in intents:
        enemy = next((participant for participant in _dicts(combat.get('participants')) if _text(participant.get('id')) == _text(intent.get('enemyId'))), None)
        replaced = None
        if enemy is not None and run is not None and enemy is run:
            replaced = runner_intent(combat, enemy)
        elif enemy is not None and quiet is not None and enemy is quiet:
            replaced = silence_intent(combat, enemy)
        if replaced is None and enemy is not None and run is not None and enemy is not run and _text(intent.get('intentType')) in {'attack', 'use_ability'}:
            target = _nearest_hero(combat, run)
            if target is not None and _text(target.get('id')) != _text(intent.get('targetId')):
                replaced = _attack(enemy, target, f"Cover {_text(run.get('name'))}.")
        planned.append({**intent, **replaced} if replaced else intent)
    return planned


# Enemy goals: what a turn does ------------------------------------------------------------------------------------


def _stake_change(turn_id: Any, index: int, operation: str, **fields: Any) -> dict[str, Any]:
    return {
        'id': stable_change_id('stake', turn_id, index, operation, *(fields.get(key) for key in ('stakeId', 'enemyId', 'itemId'))),
        'turnId': turn_id,
        'type': STAKE_CHANGE,
        'source': STAKE_SOURCE,
        'operation': operation,
        **fields,
        'visible': True,
    }


def stake_changes(action: Mapping[str, Any], *, turn_id: Any, index: int) -> list[dict[str, Any]]:
    """The ``scene.stake`` changes one resolved enemy turn carries (``stakeEvents``)."""

    return [
        _stake_change(turn_id, index * 10 + position, _text(event.get('operation')), **{key: value for key, value in event.items() if key != 'operation'})
        for position, event in enumerate(_dicts(action.get('stakeEvents')))
    ]


def contest(enemy: Mapping[str, Any], hero: Mapping[str, Any], *, roll, label: str) -> dict[str, Any]:
    """The enemy's Strength against the hero's Athletics; both rolled by the engine."""

    from aidm_server.combat.maneuvers import ability_mod

    enemy_roll = int(roll(20))
    hero_roll = int(roll(20))
    enemy_total = enemy_roll + ability_mod(enemy, 'strength')
    hero_total = hero_roll + ability_mod(hero, 'strength') + _proficiency(hero, 'athletics')
    return {
        'label': label,
        'roll': enemy_roll,
        'modifier': ability_mod(enemy, 'strength'),
        'total': enemy_total,
        'against': {'kind': 'Athletics', 'value': hero_total, 'heroId': _text(hero.get('id')), 'heroName': _text(hero.get('name'))},
        'success': enemy_total > hero_total,
    }


def _proficiency(participant: Mapping[str, Any], skill: str) -> int:
    proficient = {_text(value).lower() for value in participant.get('skillProficiencies') or participant.get('skills') or []}
    if skill not in proficient:
        return 0
    level = int(participant.get('level') or 1)
    return 2 + (max(1, level) - 1) // 4


def clear_check(enemy: Mapping[str, Any], *, roll) -> dict[str, Any]:
    from aidm_server.combat.maneuvers import ability_mod

    natural = int(roll(20))
    modifier = max(ability_mod(enemy, 'strength'), ability_mod(enemy, 'dexterity'))
    return {'label': 'Clear the way', 'roll': natural, 'modifier': modifier, 'total': natural + modifier, 'dc': CLEAR_DC, 'success': natural + modifier >= CLEAR_DC}


def resolve_goal_turn(*, enemy: dict[str, Any], combat: dict[str, Any], state: dict[str, Any], roll, retreat) -> dict[str, Any]:
    """What a ``stake_goal`` intent does on the enemy block's working copy (mutated so the enemies after it see it);
    ``retreat`` moves an enemy one band outward (with its opportunity attacks) and returns that outcome."""

    from aidm_server.combat.positioning import band_inward

    intent = _dict(enemy.get('currentIntent'))
    goal = _text(intent.get('goal'))
    stake = stake_by_id(combat, intent.get('stakeId')) or {}
    name = capitalized(_enemy_phrase(enemy))
    enemy_id = _text(enemy.get('id'))
    entry: dict[str, Any] = {'goal': goal, 'stakeId': _text(stake.get('id')), 'resolvedWithoutRoll': True}
    events: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    if goal == 'take':
        guard = guard_of(combat, _text(stake.get('itemId')))
        won = True
        if guard is not None:
            check = contest(enemy, guard, roll=roll, label='Grab past a guard')
            checks.append(check)
            won = check['success']
        if won and _scene_item(state, _text(stake.get('itemId'))) is not None:
            _take_into(state, combat, enemy, _text(stake.get('itemId')))
            events.append({'operation': 'take', 'stakeId': stake.get('id'), 'enemyId': enemy_id, 'itemId': stake.get('itemId')})
            entry['stakeLine'] = f"{name} snatches up {stake.get('name')}."
        else:
            entry['stakeLine'] = f"{name} goes for {stake.get('name')}, but {_text((guard or {}).get('name')) or 'it'} keeps it out of reach."
    elif goal == 'search':
        item = _scene_item(state, _text(stake.get('itemId')))
        if item is not None:
            item.pop('concealed', None)
        stake['concealed'] = False
        events.append({'operation': 'search', 'stakeId': stake.get('id'), 'enemyId': enemy_id, 'itemId': stake.get('itemId')})
        entry['stakeLine'] = f"{name} hunts about and finds where {stake.get('name')} was hidden."
    elif goal == 'close':
        band = _band(enemy)
        from aidm_server.combat.positioning import move_participant

        to_band = band_inward(band)
        move_participant(combat, enemy_id, to_band, engage_with=[])
        entry['movement'] = [{'fromRangeBand': band, 'toRangeBand': to_band, 'engageWith': []}]
        entry['stakeLine'] = f"{name} makes for {stake.get('name')}."
    elif goal == 'flee':
        moved = retreat(enemy)
        entry.update(moved)
        entry['stakeLine'] = f"{name} runs with {stake.get('name')}."
    elif goal == 'escape':
        events.append({'operation': 'escape', 'stakeId': stake.get('id'), 'enemyId': enemy_id, 'itemId': stake.get('itemId')})
        for other in _dicts(combat.get('participants')):
            if _text(other.get('team')).lower() == 'enemy':
                other['conditions'] = list(dict.fromkeys([*(other.get('conditions') or []), 'escaped' if other is enemy else 'withdrawn']))
        combat.setdefault('flags', {})['enemyGoalMet'] = True
        entry['stakeLine'] = f"{name} gets clear with {stake.get('name')}; the others melt away after it."
    elif goal == 'clear':
        check = clear_check(enemy, roll=roll)
        checks.append({**check, 'targetName': stake.get('name')})
        if check['success']:
            way = _object(state, _text(stake.get('objectId')))
            if way:
                way['blocked'] = False
            stake.pop('held', None)
            stake['state'] = 'open'
            events.append({'operation': 'clear', 'stakeId': stake.get('id'), 'enemyId': enemy_id, 'objectId': stake.get('objectId')})
            entry['stakeLine'] = f"{name} wrenches {stake.get('name')} open."
        else:
            entry['stakeLine'] = f"{name} heaves at {stake.get('name')}, and it holds."
    elif goal == 'silence':
        band = _band(enemy)
        if band in {'melee', 'near'}:
            now = 'hurt' if stake.get('state') == 'threatened' else 'threatened'
            stake['state'] = now
            events.append({'operation': 'person', 'stakeId': stake.get('id'), 'enemyId': enemy_id, 'npcId': stake.get('npcId'), 'state': now})
            entry['stakeLine'] = f"{name} gets to {stake.get('name')}: {stake.get('name')} is {now}."
        else:
            from aidm_server.combat.positioning import move_participant

            to_band = band_inward(band)
            move_participant(combat, enemy_id, to_band, engage_with=[])
            entry['movement'] = [{'fromRangeBand': band, 'toRangeBand': to_band, 'engageWith': []}]
            entry['stakeLine'] = f"{name} makes for {stake.get('name')}."
    if events:
        entry['stakeEvents'] = events
    if checks:
        entry['stakeChecks'] = checks
    return entry


def _take_into(state: dict[str, Any], combat: dict[str, Any], enemy: dict[str, Any], item_id: str) -> None:
    scene = _scene(state)
    items = _dicts(scene.get('items'))
    found = next((item for item in items if item_id in {_text(item.get('id')), _text(item.get('sourceItemId'))}), None)
    if found is None:
        return
    scene['items'] = [item for item in items if item is not found]
    enemy['carrying'] = [*_dicts(enemy.get('carrying')), {key: value for key, value in found.items() if key not in {'containerId', 'concealed'}}]
    stake = next((stake for stake in _stakes_of(combat) if _text(stake.get('itemId')) == item_id), None)
    if stake is not None:
        stake['state'] = 'held_by_enemy'
        stake['moved'] = True


def wrest_after_hit(*, enemy: dict[str, Any], target: dict[str, Any], combat: dict[str, Any], state: dict[str, Any], result: dict[str, Any], roll) -> None:
    """A runner's hit on a hero at half their hit points or less, who holds the item, may wrest it instead of hurting
    them (Strength against Athletics); ``result`` (the attack) is changed in place."""

    from aidm_server.combat.maneuvers import at_most_half

    stake = stake_by_id(combat, _dict(enemy.get('currentIntent')).get('wrestStakeId'))
    if stake is None or result.get('hit') is not True or not at_most_half(target):
        return
    check = contest(enemy, target, roll=roll, label='Wrest it away')
    result['stakeChecks'] = [check]
    who = capitalized(_enemy_phrase(enemy))
    if not check['success']:
        result['stakeLine'] = f"{who} grabs for {stake.get('name')}, but {_text(target.get('name'))} holds on."
        return
    from aidm_server.game_state.orchestration.scene_plans import move_item

    # The blow did no harm: the working copy the enemies after it see gets the hit points back, and the item moves.
    hp = target.get('hp') if isinstance(target.get('hp'), dict) else {}
    if hp:
        hp['current'] = min(int(hp.get('max') or 0), int(hp.get('current') or 0) + int(result.get('damageTotal') or 0))
    move_item(state, {'itemId': _text(stake.get('itemId')), 'to': 'enemy', 'enemyId': _text(enemy.get('id'))})
    result['damageTotal'] = 0
    result['wrested'] = True
    result['stakeEvents'] = [{'operation': 'wrest', 'stakeId': stake.get('id'), 'enemyId': _text(enemy.get('id')), 'heroId': _text(target.get('id')), 'itemId': stake.get('itemId')}]
    result['stakeLine'] = f"{who} tears {stake.get('name')} out of {_text(target.get('name'))}'s hands."
    stake['state'] = 'held_by_enemy'


# Applying ---------------------------------------------------------------------------------------------------------


def stake_change_problem(state: Mapping[str, Any], change: Mapping[str, Any]) -> str:
    if _text(change.get('source')) != STAKE_SOURCE:
        return 'Only the engine moves what a fight is about.'
    if _text(change.get('operation')) not in {'take', 'search', 'escape', 'clear', 'person', 'wrest', 'drop', 'final', 'rested'}:
        return 'Unknown stake operation.'
    return ''


def _drop(state: dict[str, Any], item_id: str) -> bool:
    from aidm_server.game_state.orchestration.scene_plans import move_item

    zone = _text(_dict(_combat(state).get('flags')).get('zoneId'))
    return move_item(state, {'itemId': item_id, 'to': 'scene', **({'zoneId': zone} if zone else {})})


def apply_stake_change(state: dict[str, Any], change: Mapping[str, Any]) -> dict[str, Any] | None:
    """Apply a ``scene.stake`` change; the applied change's extra fields, or None when it no longer applies."""

    if stake_change_problem(state, change):
        return None
    operation = _text(change.get('operation'))
    if operation == 'final':
        return _apply_final(state, change)
    if operation == 'rested':
        for npc in _dicts(state.get('knownNpcs')):
            npc.pop('outOfPlay', None)
        return {}
    combat = _combat(state)
    stake = stake_by_id(combat, change.get('stakeId'))
    enemy = next((participant for participant in _dicts(combat.get('participants')) if _text(participant.get('id')) == _text(change.get('enemyId'))), None)
    item_id = _text(change.get('itemId'))
    if operation in {'take', 'search', 'wrest'}:
        from aidm_server.game_state.orchestration.scene_plans import set_fact

        # Where the heroes had put it is no longer true.
        set_fact(_scene(state), f'item:{item_id}', '')
    if operation == 'take':
        if enemy is None:
            return None
        _take_into(state, combat, enemy, item_id)
    elif operation == 'wrest':
        from aidm_server.game_state.orchestration.scene_plans import move_item

        if enemy is None or not move_item(state, {'itemId': item_id, 'to': 'enemy', 'enemyId': _text(enemy.get('id'))}):
            return None
    elif operation == 'search':
        item = _scene_item(state, item_id)
        if item is not None:
            item.pop('concealed', None)
    elif operation == 'escape':
        if enemy is None:
            return None
        if stake is not None:
            stake['state'] = 'taken'
            stake['changedTurn'] = change.get('turnId')
        enemy['carrying'] = [item for item in _dicts(enemy.get('carrying')) if _text(item.get('id')) != item_id]
        for other in _dicts(combat.get('participants')):
            if _text(other.get('team')).lower() == 'enemy' and _standing(other):
                other['conditions'] = list(dict.fromkeys([*(other.get('conditions') or []), 'escaped' if other is enemy else 'withdrawn']))
        combat.setdefault('flags', {})['enemyGoalMet'] = True
    elif operation == 'clear':
        if stake is not None:
            stake.pop('held', None)
        way = _object(state, _text(change.get('objectId')))
        if way:
            from aidm_server.game_state.orchestration.scene_plans import set_fact, sync_location

            way['blocked'] = False
            set_fact(_scene(state), f"object:{_text(change.get('objectId'))}", '')
            sync_location(state)
    elif operation == 'person':
        if stake is None:
            return None
        stake['state'] = _text(change.get('state'))
        stake['changedTurn'] = change.get('turnId')
        if stake['state'] == 'hurt':
            for npc in _dicts(state.get('knownNpcs')):
                if _text(npc.get('id')) == _text(stake.get('npcId')):
                    npc['outOfPlay'] = True
    elif operation == 'drop':
        if not _drop(state, item_id):
            return None
    sync_stakes(state, turn_id=change.get('turnId'))
    return {}


def _final_states(state: Mapping[str, Any]) -> dict[str, str]:
    finals = {}
    for stake in fight_stakes(state):
        now = _text(stake.get('state'))
        if stake.get('kind') == 'item':
            now = 'taken' if now in {'held_by_enemy', 'taken'} else 'secured'
        finals[_text(stake.get('id'))] = now
    return finals


def _end_matches(wanted: Any, end_reason: str) -> bool:
    names = {end_reason, *END_ALIASES.get(end_reason, set())}
    if isinstance(wanted, list):
        return any(_text(value) in names for value in wanted)
    return _text(wanted) in names


def result_holds(state: Mapping[str, Any], when: Any, *, finals: Mapping[str, str], end_reason: str, captives: bool) -> bool:
    from aidm_server.game_state.campaign_pack_encounters import values_match

    if not isinstance(when, dict):
        return True
    flags = _flags(state)
    return (
        all(finals.get(_text(stake_id)) == _text(wanted) for stake_id, wanted in _dict(when.get('stakes')).items())
        and ('end' not in when or _end_matches(when.get('end'), end_reason))
        and all(values_match(flags.get(key, False), value) for key, value in _dict(when.get('flags')).items())
        and ('captives' not in when or bool(when.get('captives')) == captives)
    )


def finalize_changes(state: Mapping[str, Any], applied_end: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The changes the end of a fight with stakes makes: the final ``scene.stake`` (final states, results that hold,
    their facts, leads and texts) and their flags and dispositions."""

    from aidm_server.game_state.campaign_pack_encounters import catalog_record
    from aidm_server.game_state.orchestration.conversations import affect_change

    combat = _combat(state)
    flags = _dict(combat.get('flags'))
    encounter_id = _text(flags.get('campaignPackEncounterId'))
    encounter = catalog_record(dict(state), 'encounters', encounter_id) if encounter_id else {}
    if not (fight_stakes(state) or encounter_results(encounter)):
        # A fight without stakes still ends the passing effects laid in it (distracted, cut off, guarding).
        passing = [key for key in _dict(_scene(state).get('factKeys')) if key.startswith('t:')]
        if not passing:
            return []
        encounter_id = ''
    end_reason = _text(applied_end.get('endReason') or flags.get('endReason'))
    finals = _final_states(state)
    captives = bool(_dicts(applied_end.get('captives')))
    held = [
        (index, entry)
        for index, entry in enumerate(encounter_results(encounter))
        if result_holds(state, entry.get('when'), finals=finals, end_reason=end_reason, captives=captives)
    ]
    final = _stake_change(
        applied_end.get('turnId'),
        0,
        'final',
        encounterId=encounter_id,
        endReason=end_reason,
        finals=finals,
        results=[
            {
                'index': index,
                **{key: deepcopy(entry[key]) for key in ('fact', 'lead', 'text') if _text(entry.get(key))},
            }
            for index, entry in held
        ],
    )
    final['id'] = stable_change_id(applied_end.get('id'), STAKE_CHANGE, 'final', encounter_id)
    changes = [final]
    for index, entry in held:
        for key, value in _dict(entry.get('setsFlags')).items():
            changes.append(
                {
                    'id': stable_change_id(final['id'], 'flag', index, key),
                    'turnId': applied_end.get('turnId'),
                    'type': 'flag.set',
                    'source': STAKE_SOURCE,
                    'flagKey': _text(key),
                    'flagValue': deepcopy(value),
                    'visible': False,
                    'reason': f"The fight's outcome ({end_reason}).",
                }
            )
        for position, affect in enumerate(_dicts(entry.get('affects'))):
            change = affect_change(dict(state), parent={'id': f"{final['id']}:{index}", 'turnId': applied_end.get('turnId')}, entry=affect, index=position, hero_id='')
            if change:
                changes.append(change)
        changes.extend(
            given_items_changes(
                state,
                parent_id=stable_change_id(final['id'], 'items', index),
                turn_id=applied_end.get('turnId'),
                items=entry.get('givesItems'),
                hero_id=_fight_recipient(combat),
                source=STAKE_SOURCE,
                reason=f"The fight's outcome ({end_reason}).",
            )
        )
    return changes


def _fight_recipient(combat: Mapping[str, Any]) -> str:
    """Who a fight's result gives to: the hero whose turn ended it, else the first hero in it (present); ''."""

    heroes = [
        participant
        for participant in _dicts(combat.get('participants'))
        if _text(participant.get('team')).lower() == 'player' and participant.get('isPresent', participant.get('present', True)) is not False
    ]
    acting = _text(_dict(combat.get('flags')).get('activeActorId'))
    chosen = next((hero for hero in heroes if _text(hero.get('id')) == acting), heroes[0] if heroes else {})
    return _text(chosen.get('id'))


def given_items_changes(
    state: Mapping[str, Any],
    *,
    parent_id: str,
    turn_id: Any,
    items: Any,
    hero_id: str,
    source: str,
    reason: str,
) -> list[dict[str, Any]]:
    """The ``inventory.add`` changes of authored ``givesItems`` (a fight's result, a plan's result), in the shape a
    topic's gift takes: each item keeps its id and comes from the pack; [] without a hero."""

    if not _text(hero_id):
        return []
    pack_id = _text(_dict(state.get('campaignPack')).get('packId'))
    changes = []
    for item in _dicts(items):
        if not _text(item.get('id')) or not _text(item.get('name')):
            continue
        given = {**deepcopy(item), 'quantity': max(1, int(item.get('quantity') or 1)), 'source': 'campaign_pack', 'packId': pack_id}
        changes.append(
            {
                'id': stable_change_id(parent_id, 'item', item['id']),
                'turnId': turn_id,
                'type': 'inventory.add',
                'source': source,
                'actorId': _text(hero_id),
                'item': given,
                'quantity': given['quantity'],
                'visible': True,
                'reason': reason,
            }
        )
    return changes


def _apply_final(state: dict[str, Any], change: Mapping[str, Any]) -> dict[str, Any]:
    from aidm_server.game_state.orchestration.scene_plans import clear_transient_facts, set_fact

    encounter_id = _text(change.get('encounterId'))
    if encounter_id:
        stored = _dict(state.get(STAKES_KEY))
        stored[encounter_id] = {**_dict(stored.get(encounter_id)), **{key: _text(value) for key, value in _dict(change.get('finals')).items()}}
        state[STAKES_KEY] = stored
    for stake in fight_stakes(state):
        final = _dict(change.get('finals')).get(stake.get('id'))
        if final:
            stake['state'] = final
    scene = _scene(state)
    clear_transient_facts(scene)
    leads = _dicts(state.get(LEADS_KEY))
    for result in _dicts(change.get('results')):
        if _text(result.get('fact')):
            set_fact(scene, f"result:{encounter_id}:{result.get('index')}", _text(result['fact']))
        if _text(result.get('lead')) and all(lead.get('text') != _text(result['lead']) for lead in leads):
            leads.append({'text': _text(result['lead'])})
    state[LEADS_KEY] = leads
    return {}


def told_results(changes: Any) -> list[str]:
    """The texts of the results a fight's end applied, as written (amendment 9)."""

    return [
        _text(result.get('text'))
        for change in _dicts(changes)
        if change.get('type') == STAKE_CHANGE and change.get('operation') == 'final'
        for result in _dicts(change.get('results'))
        if _text(result.get('text'))
    ]


def foreseen_results(state: dict[str, Any], dm_context: Mapping[str, Any], *, actor_id: str, turn_id: Any, already_applied: list[dict[str, Any]]) -> list[str]:
    """The result texts of a fight the engine-resolved enemy turns in ``dm_context`` will end, so the narration of
    this turn can tell them (the end itself is applied after narration)."""

    from aidm_server.combat.end_conditions import check_combat_end, combat_end_change
    from aidm_server.game_state.application.applier import apply_state_changes
    from aidm_server.game_state.orchestration.combat_resolution import derive_trusted_damage_changes
    from aidm_server.game_state.validation.validator import validate_state_changes, validated_changes_for_application

    def apply(current: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
        return apply_state_changes(current, validated_changes_for_application(validate_state_changes(state=current, changes=changes)))

    trusted = derive_trusted_damage_changes(
        state=state, dm_context_packet=dict(dm_context), actor_id=actor_id, turn_id=turn_id, already_applied_changes=already_applied
    ).all_changes
    projected = apply(state, trusted)['nextState']
    reason = check_combat_end(_combat(projected))
    if not reason:
        return []
    return told_results(apply(projected, [combat_end_change(turn_id, reason)])['appliedChanges'])


def defeat_text(state: Mapping[str, Any]) -> list[str]:
    """The ``onDefeat`` text of the encounter a beaten party lost, told as written; [] without one."""

    from aidm_server.game_state.campaign_pack_encounters import catalog_record

    encounter_id = _text(_dict(_combat(state).get('flags')).get('campaignPackEncounterId'))
    authored = on_defeat(catalog_record(dict(state), 'encounters', encounter_id)) if encounter_id else None
    return [_text(authored.get('text'))] if authored and _text(authored.get('text')) else []


def leads_of(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The journal's ``LEADS``: ``{"text", "followed"?}``."""

    return [{'text': _text(lead.get('text')), **({'followed': True} if lead.get('followed') is True else {})} for lead in _dicts(state.get(LEADS_KEY)) if _text(lead.get('text'))]


# Follow-ups -------------------------------------------------------------------------------------------------------


def _drops(state: Mapping[str, Any], applied: Mapping[str, Any]) -> list[dict[str, Any]]:
    """An item drops when whoever holds it in the fight goes down, yields or is bound."""

    from aidm_server.combat.end_conditions import has_surrendered, is_defeated

    combat = _combat(state)
    if _text(combat.get('status')).lower() not in ACTIVE_COMBAT_STATUSES:
        return []
    changes = []
    for stake in fight_stakes(state):
        if stake.get('kind') != 'item':
            continue
        item_id = _text(stake.get('itemId'))
        carrier = _carrier(combat, item_id)
        if carrier is not None and (is_defeated(carrier) or has_surrendered(carrier)):
            changes.append(_stake_change(applied.get('turnId'), 0, 'drop', stakeId=stake.get('id'), itemId=item_id, enemyId=_text(carrier.get('id'))))
            continue
        holder = _holder(state, item_id)
        participant = next((record for record in _dicts(combat.get('participants')) if holder and _text(record.get('id')) == _text(holder.get('id'))), None)
        if holder is not None and participant is not None and is_defeated(participant):
            changes.append(_stake_change(applied.get('turnId'), 0, 'drop', stakeId=stake.get('id'), itemId=item_id, heroId=_text(holder.get('id'))))
    for change in changes:
        change['id'] = stable_change_id(applied.get('id'), STAKE_CHANGE, 'drop', change.get('itemId'))
    return changes


def stake_followup_changes(state: dict[str, Any], applied: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What follows one applied change: an item dropped by whoever fell, the end of a fight with stakes, and the
    people a fight hurt coming back after a long rest."""

    change_type = _text(applied.get('type'))
    if change_type == 'combat.end':
        return finalize_changes(state, applied)
    if change_type in {'health.damage', 'combat.participant.update', 'combat.condition.add'}:
        return _drops(state, applied)
    if change_type == 'rest.complete' and _text(applied.get('restType')) == 'long_rest' and any(npc.get('outOfPlay') for npc in _dicts(state.get('knownNpcs'))):
        change = _stake_change(applied.get('turnId'), 0, 'rested')
        change['id'] = stable_change_id(applied.get('id'), STAKE_CHANGE, 'rested')
        return [change]
    return []
