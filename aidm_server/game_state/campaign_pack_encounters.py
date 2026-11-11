"""Campaign-pack world rules: encounter materialisation, scaling and triggers.

Besides building pack combat, this module derives the trusted follow-up
changes the applier queues after an applied change: an encounter whose
authored trigger is due starts, an object action that settles an encounter
(``resolvesEncounter``) completes it, a location's ``onEnter`` consequences
run on first arrival, and authored consequences that name catalog records are
materialised from the pack catalog.

An encounter's ``adjustments`` apply when its combat is created, before party
scaling: what the party knows (a flag) can weaken one enemy group's hit points
or keep whole groups away.
"""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import math
from typing import Any

from aidm_server.combat.attacks import ability_attack_bonus, ability_damage, ability_save
from aidm_server.combat.difficulty import pack_combat_difficulty_ai
from aidm_server.combat.end_conditions import combat_end_change
from aidm_server.combat.state import (
    combat_ability_resolution_mode,
    default_battlefield,
    instantiate_creature,
    player_combat_participant,
)
from aidm_server.game_state.models import stable_change_id, stable_slug
from aidm_server.game_state.quest_engine import CONSEQUENCE_CHANGE_TYPES

MAX_CAMPAIGN_PACK_ENEMIES_PER_GROUP = 12
MAX_CAMPAIGN_PACK_ENEMY_PARTICIPANTS = 24
AUTHORED_PARTY_SIZE = 4
# Present heroes -> (enemy HP x, enemy damage x, enemy attack bonus and save DC, most enemies).
# The lone-hero row is tuned by scripts/solo_balance.py now that enemies fight back every turn.
PARTY_SCALING = {
    1: (Fraction(3, 10), Fraction(2, 5), -3, 1),
    2: (Fraction(3, 5), Fraction(7, 10), -1, 3),
    3: (Fraction(4, 5), Fraction(17, 20), 0, 4),
}
ENCOUNTER_TRIGGER_TYPES = ('on_enter', 'on_checkpoint', 'on_flag', 'on_interactable')
# Applied change types that can make an event trigger due.
TRIGGER_EVENT_CHANGE_TYPES = frozenset({'scene.move_location', 'scene.interactable.action', 'flag.set'})
TRIGGERED_ENCOUNTERS_FLAG = 'campaignPackTriggeredEncounterIds'
# Encounters an object action settled: their triggers never fire again.
RESOLVED_ENCOUNTERS_FLAG = 'campaignPackResolvedEncounterIds'
COMPLETED_ENCOUNTERS_FLAG = 'campaignPackCompletedEncounterIds'
ENCOUNTER_RESOLVE_CHANGE = 'encounter.resolve'
# The outcomes an object action can settle an encounter with, and the combat end reason each stands for.
RESOLUTION_END_REASONS = {
    'victory': 'all_enemies_defeated',
    'surrender': 'enemies_surrendered',
    'negotiation': 'negotiated_resolution',
    'objective_completion': 'objective_completed',
}
ADJUSTMENT_HP_MULTIPLIER_RANGE = (0.25, 1.5)
LOCATION_ENTERED_FLAG = 'locationEnteredIds'
TRIGGER_STARTED_BY = 'campaign_pack_trigger'
WORLD_CHANGE_SOURCE = 'campaign_pack_world'
REST_POLICIES = ('safe', 'risky', 'none')
# Change type -> (catalog collection, id key, embedded record key).
CATALOG_CONSEQUENCES = {
    'clue.discover': ('clues', 'clueId', 'clue'),
    'quest.add': ('quests', 'questId', 'quest'),
    'location.discover': ('locations', 'locationId', 'location'),
    'location.update': ('locations', 'locationId', 'location'),
}


def materialize_campaign_pack_combat_start(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(change, dict) or str(change.get('type') or '') != 'combat.start':
        return change
    pack = state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}
    pack_id = _text(pack.get('packId') or pack.get('pack_id'))
    if not pack_id:
        return change

    checkpoint = _active_checkpoint(state, pack)
    requested_encounter_id = _requested_encounter_id(change, checkpoint)
    if not requested_encounter_id:
        return change
    combat = change.get('combat') if isinstance(change.get('combat'), dict) else {}
    if _combat_already_materialized_for_encounter(combat, requested_encounter_id):
        return change
    encounter = _pack_record(pack, 'encounters', requested_encounter_id)
    if not encounter:
        return change

    players = _player_participants(state, combat)
    scaling = party_scaling(
        encounter,
        party_size=sum(1 for player in players if _present_player(player)),
        adjustments=applied_adjustments(state, encounter),
    )
    enemies = _instantiate_pack_enemies(
        pack,
        encounter,
        turn_id=_positive_int(change.get('turnId') or change.get('turn_id')),
        scaling=scaling,
    )
    if not enemies:
        return change

    updated = deepcopy(change)
    combat = updated.get('combat') if isinstance(updated.get('combat'), dict) else {}
    participants = [*players, *enemies]
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    flags.update(
        {
            'source': 'campaign_pack',
            'packId': pack_id,
            'campaignPackId': pack_id,
            'campaignPackEncounterId': _record_id(encounter),
            'campaignPackCheckpointIds': _checkpoint_ids_for_encounter(checkpoint, encounter),
            'campaignPackEnemyIds': [_text(enemy.get('campaignPackEnemyId')) for enemy in enemies if enemy.get('campaignPackEnemyId')],
            'partyScaling': scaling,
        }
    )
    completion = encounter.get('completion') if isinstance(encounter.get('completion'), dict) else {}
    allowed_outcomes = _string_list(
        completion.get('anyOf')
        or completion.get('any_of')
        or completion.get('outcomes')
        or completion.get('allowedOutcomes')
        or completion.get('allowed_outcomes')
    )
    if allowed_outcomes:
        flags['campaignPackAllowedOutcomes'] = allowed_outcomes
    if encounter.get('allowParley') is True:
        # Its enemies may parley or give ground from the first blow.
        flags['allowParley'] = True

    combat.update(
        {
            'status': combat.get('status') or 'active',
            'round': combat.get('round') or 1,
            'participants': participants,
            'flags': flags,
        }
    )
    if not isinstance(combat.get('encounterGoal'), dict):
        combat['encounterGoal'] = {
            'type': 'campaign_pack',
            'encounterId': _record_id(encounter),
            'title': _text(encounter.get('title') or encounter.get('name')),
            'summary': _text(encounter.get('summary') or encounter.get('description')),
            'allowedOutcomes': allowed_outcomes,
        }
    updated['combat'] = combat
    updated['source'] = 'campaign_pack'
    updated['packId'] = pack_id
    updated.setdefault('campaignPackEncounterId', _record_id(encounter))
    updated.setdefault('encounterId', _record_id(encounter))
    return updated


def party_scaling(
    encounter: dict[str, Any],
    *,
    party_size: int,
    adjustments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """How an encounter authored for four PCs scales to the present party.

    ``PARTY_SCALING`` gives, by present heroes, the enemy HP multiplier (HP
    rounds up), the damage multiplier (every enemy damage roll, rounded down,
    at least 1), the change to enemy attack bonuses and save DCs, and the most
    enemies. Each group keeps ``max(1, round(count * party / 4))`` enemies
    (halves round up); while the total exceeds the most enemies, whole groups
    are dropped from the end (never the first), then the last remaining group
    loses enemies. A party of four or more, an empty one, or
    ``scaling: false`` changes nothing.

    The encounter's applied ``adjustments`` (:func:`applied_adjustments`) come
    first: their removed groups are gone before the most enemies is applied,
    and an ``hpMultiplier`` multiplies with the party's HP multiplier. They are
    recorded here as ``adjustments``.
    """

    adjustments = adjustments or []
    removed = {enemy_id for adjustment in adjustments for enemy_id in adjustment.get('removeEnemyIds') or []}
    applied = encounter.get('scaling') is not False and 1 <= party_size < AUTHORED_PARTY_SIZE
    hp_multiplier, damage_multiplier, attack_and_dc, max_enemies = (
        PARTY_SCALING[party_size] if applied else (Fraction(1), Fraction(1), 0, None)
    )
    groups = [
        {
            'enemyId': enemy_id,
            'authoredCount': count,
            'count': _scaled_count(count, party_size) if applied else count,
        }
        for enemy_id, count in _encounter_enemy_specs(encounter)
        if enemy_id not in removed
    ]
    dropped: list[dict[str, Any]] = []
    if max_enemies is not None:
        while len(groups) > 1 and sum(group['count'] for group in groups) > max_enemies:
            dropped.insert(0, groups.pop())
        if groups:
            groups[-1]['count'] = max(1, groups[-1]['count'] - max(0, sum(group['count'] for group in groups) - max_enemies))
    hp_adjustments: dict[str, float] = {}
    for adjustment in adjustments:
        if adjustment.get('enemyId') and adjustment.get('hpMultiplier') is not None:
            enemy_id = adjustment['enemyId']
            hp_adjustments[enemy_id] = float(Fraction(str(hp_adjustments.get(enemy_id, 1))) * Fraction(str(adjustment['hpMultiplier'])))
    scaling = {
        'partySize': party_size,
        'applied': applied,
        'hpMultiplier': float(hp_multiplier),
        'damageMultiplier': float(damage_multiplier),
        'attackAndDcAdjustment': attack_and_dc,
        'maxEnemies': max_enemies,
        'groups': groups,
        'droppedGroups': dropped,
    }
    if adjustments:
        scaling['adjustments'] = deepcopy(adjustments)
        scaling['hpAdjustments'] = hp_adjustments
    return scaling


def applied_adjustments(state: dict[str, Any], encounter: dict[str, Any]) -> list[dict[str, Any]]:
    """The encounter's ``adjustments`` whose ``when`` holds against the state's flags, in authored order."""

    flags = state.get('flags') if isinstance(state.get('flags'), dict) else {}
    applied = []
    for adjustment in encounter.get('adjustments') if isinstance(encounter.get('adjustments'), list) else []:
        if not isinstance(adjustment, dict) or not flag_condition_holds(flags, adjustment.get('when')):
            continue
        record: dict[str, Any] = {'when': deepcopy(adjustment.get('when')), 'text': _text(adjustment.get('text'))}
        multiplier = adjustment.get('hpMultiplier')
        if _text(adjustment.get('enemyId')) and isinstance(multiplier, (int, float)) and not isinstance(multiplier, bool):
            record.update(enemyId=_text(adjustment['enemyId']), hpMultiplier=multiplier)
        removed = _string_list(adjustment.get('removeEnemyIds'))
        if removed:
            record['removeEnemyIds'] = removed
        applied.append(record)
    return applied


def _scaled_count(count: int, party_size: int) -> int:
    return max(1, math.floor(Fraction(count * party_size, AUTHORED_PARTY_SIZE) + Fraction(1, 2)))


def _scaled_hp(max_hp: Any, scaling: dict[str, Any], enemy_id: str) -> int:
    hp = max(0, _positive_int(max_hp) or 0)
    multiplier = PARTY_SCALING[int(scaling['partySize'])][0] if scaling.get('applied') else Fraction(1)
    adjustment = (scaling.get('hpAdjustments') or {}).get(enemy_id)
    if adjustment is not None:
        multiplier *= Fraction(str(adjustment))
    return hp if multiplier == 1 else math.ceil(Fraction(hp) * multiplier)


def _scaled_abilities(abilities: Any, scaling: dict[str, Any], participant: dict[str, Any]) -> list[Any]:
    """The enemy's abilities with the party's attack-bonus and save-DC adjustment folded in."""

    adjustment = int(scaling.get('attackAndDcAdjustment') or 0)
    scaled = deepcopy(abilities) if isinstance(abilities, list) else []
    if not adjustment:
        return scaled
    for ability in scaled:
        if not isinstance(ability, dict):
            continue
        if ability_damage(ability).get('dice') and combat_ability_resolution_mode(ability) in {'attack', 'attack_with_save'}:
            ability['attackBonus'] = ability_attack_bonus(participant, ability) + adjustment
        if isinstance(ability.get('save'), dict):
            ability['save'] = {**ability['save'], 'dc': max(1, ability_save(ability)['dc'] + adjustment)}
    return scaled


def _present_player(participant: dict[str, Any]) -> bool:
    conditions = {_text(value).lower() for value in (participant.get('conditions') or [])}
    return (
        participant.get('team') == 'player'
        and participant.get('isPresent', participant.get('present', True)) is not False
        and 'absent' not in conditions
    )


def _combat_already_materialized_for_encounter(combat: dict[str, Any], encounter_id: str) -> bool:
    encounter_key = _text(encounter_id)
    if not encounter_key:
        return False
    enemies = [
        participant
        for participant in (combat.get('participants') or [])
        if isinstance(participant, dict) and participant.get('team') == 'enemy'
    ]
    if not enemies:
        return False
    return all(
        _text(enemy.get('source')) == 'campaign_pack'
        and _text(enemy.get('campaignPackEncounterId')) == encounter_key
        and _text(enemy.get('campaignPackEnemyId'))
        for enemy in enemies
    )


def _active_checkpoint_id(state: dict[str, Any], pack: dict[str, Any]) -> str:
    flags = state.get('flags') if isinstance(state.get('flags'), dict) else {}
    return _text(
        pack.get('activeCheckpointId')
        or pack.get('active_checkpoint_id')
        or pack.get('currentCheckpointId')
        or flags.get('campaignPackActiveCheckpointId')
    )


def _active_checkpoint(state: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    active_id = _active_checkpoint_id(state, pack)
    checkpoints = [checkpoint for checkpoint in (pack.get('checkpoints') or []) if isinstance(checkpoint, dict)]
    return next((checkpoint for checkpoint in checkpoints if _record_id(checkpoint) == active_id), {}) if active_id else {}


def _requested_encounter_id(change: dict[str, Any], checkpoint: dict[str, Any]) -> str:
    combat = change.get('combat') if isinstance(change.get('combat'), dict) else {}
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    for value in (
        change.get('campaignPackEncounterId'),
        change.get('campaign_pack_encounter_id'),
        change.get('encounterId'),
        change.get('encounter_id'),
        combat.get('campaignPackEncounterId'),
        combat.get('encounterId'),
        flags.get('campaignPackEncounterId'),
        flags.get('encounterId'),
    ):
        if _text(value):
            return _text(value)
    has_explicit_enemy = any(
        isinstance(participant, dict) and participant.get('team') == 'enemy'
        for participant in (combat.get('participants') or change.get('participants') or [])
    )
    if has_explicit_enemy:
        return ''
    encounter_ids = _string_list(
        checkpoint.get('encounterIds')
        or checkpoint.get('encounter_ids')
        or checkpoint.get('encounters')
        or checkpoint.get('encounterId')
    )
    return encounter_ids[0] if encounter_ids else ''


def _pack_record(pack: dict[str, Any], key: str, record_id: str) -> dict[str, Any]:
    catalog = pack.get('catalog') if isinstance(pack.get('catalog'), dict) else {}
    records = catalog.get(key)
    if not isinstance(records, list):
        records = pack.get(key) if isinstance(pack.get(key), list) else []
    record_key = _text(record_id)
    for record in records:
        if isinstance(record, dict) and _record_id(record) == record_key:
            return record
    return {}


def catalog_record(state: dict[str, Any], key: str, record_id: Any) -> dict[str, Any]:
    """The pack catalog record of ``key`` with this id (exact or slugged)."""

    pack = state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}
    return _pack_record(pack, key, _text(record_id)) or _pack_record(pack, key, stable_slug(record_id) if _text(record_id) else '')


def _instantiate_pack_enemies(
    pack: dict[str, Any],
    encounter: dict[str, Any],
    *,
    turn_id: int | None,
    scaling: dict[str, Any],
) -> list[dict[str, Any]]:
    participants: list[dict[str, Any]] = []
    sequence = 1
    remaining = MAX_CAMPAIGN_PACK_ENEMY_PARTICIPANTS
    for group in scaling['groups']:
        enemy = _pack_record(pack, 'enemies', group['enemyId'])
        if not enemy:
            continue
        count = min(group['count'], remaining)
        # One creature of a group is named by its singular name; several are numbered.
        singular = _text(enemy.get('singularName')) or _text(enemy.get('name'))
        for index in range(count):
            participant = instantiate_creature(
                enemy,
                instance_id=f"enemy_{stable_slug(group['enemyId'])}_{sequence}",
                team='enemy',
                current_turn=turn_id,
            )
            if singular:
                participant['name'] = singular if count == 1 else f'{singular} {index + 1}'
            if enemy.get('proper') is True:
                participant['proper'] = True
            max_hp = _scaled_hp(participant['hp']['max'], scaling, group['enemyId'])
            participant['hp'] = {**participant['hp'], 'current': max_hp, 'max': max_hp}
            participant['stats'] = {**participant['stats'], 'maxHp': max_hp}
            participant['abilities'] = _scaled_abilities(participant.get('abilities'), scaling, participant)
            if scaling.get('applied') and float(scaling.get('damageMultiplier') or 1) != 1:
                participant['partyDamageMultiplier'] = scaling['damageMultiplier']
            participant['source'] = 'campaign_pack'
            participant['campaignPackEnemyId'] = _record_id(enemy)
            participant['campaignPackEncounterId'] = _record_id(encounter)
            participants.append(participant)
            sequence += 1
            remaining -= 1
    return participants


def _encounter_enemy_specs(encounter: dict[str, Any]) -> list[tuple[str, int]]:
    specs_by_id: dict[str, int] = {}
    ordered_ids: list[str] = []

    def add_spec(enemy_id: Any, count: Any, *, override: bool = False) -> None:
        key = _text(enemy_id)
        if not key:
            return
        if key not in specs_by_id:
            ordered_ids.append(key)
        if override or key not in specs_by_id:
            specs_by_id[key] = _bounded_enemy_count(count)

    for enemy_id in _string_list(encounter.get('enemyIds') or encounter.get('enemy_ids')):
        add_spec(enemy_id, 1)
    groups = encounter.get('enemyGroups') or encounter.get('enemy_groups') or encounter.get('enemies')
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, str):
                add_spec(group, 1, override=True)
            elif isinstance(group, dict):
                enemy_id = _text(group.get('enemyId') or group.get('enemy_id') or group.get('id') or group.get('creatureId'))
                if enemy_id:
                    add_spec(enemy_id, group.get('count'), override=True)
    return [(enemy_id, specs_by_id[enemy_id]) for enemy_id in ordered_ids]


def _bounded_enemy_count(value: Any) -> int:
    return min(max(1, _positive_int(value) or 1), MAX_CAMPAIGN_PACK_ENEMIES_PER_GROUP)


def _player_participants(state: dict[str, Any], combat: dict[str, Any]) -> list[dict[str, Any]]:
    existing_players = [
        participant
        for participant in (combat.get('participants') or [])
        if isinstance(participant, dict) and participant.get('team') in {'player', 'ally'}
    ]
    if existing_players:
        return existing_players
    return [
        player_combat_participant(actor)
        for actor in (state.get('playerCharacters') or [])
        if isinstance(actor, dict)
    ]


def _checkpoint_ids_for_encounter(checkpoint: dict[str, Any], encounter: dict[str, Any]) -> list[str]:
    ids = _string_list(encounter.get('checkpointIds') or encounter.get('checkpoint_ids'))
    checkpoint_id = _record_id(checkpoint)
    if checkpoint_id and checkpoint_id not in ids:
        ids.insert(0, checkpoint_id)
    return ids


def flag_matches(flags: dict[str, Any], flag_key: Any, expected: Any) -> bool:
    """``branchWhen`` comparison: the flag exists and equals ``expected``
    with the same scalar type (numbers compare numerically)."""

    key = _text(flag_key)
    if not key:
        return False
    for candidate in (key, stable_slug(key)):
        if candidate in flags:
            return values_match(flags[candidate], expected)
    return False


def values_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and actual == expected
    if isinstance(expected, (int, float)) or isinstance(actual, (int, float)):
        return isinstance(expected, (int, float)) and isinstance(actual, (int, float)) and float(actual) == float(expected)
    return type(actual) is type(expected) and actual == expected


def _same_id(first: Any, second: Any) -> bool:
    return bool(_text(first)) and bool(_text(second)) and (
        _text(first) == _text(second) or stable_slug(first) == stable_slug(second)
    )


def flag_condition_holds(flags: dict[str, Any], condition: Any) -> bool:
    """A ``{"flagKey", "equals"}`` predicate, or ``{"anyOf": [predicate, ...]}`` holding when any one does."""

    if not isinstance(condition, dict):
        return False
    if isinstance(condition.get('anyOf'), list):
        return any(flag_condition_holds(flags, child) for child in condition['anyOf'])
    return flag_matches(flags, condition.get('flagKey'), condition.get('equals', True))


def _trigger_matches(state: dict[str, Any], trigger: dict[str, Any], changes: list[dict[str, Any]]) -> bool:
    trigger_type = _text(trigger.get('type')).lower()
    if trigger_type == 'on_checkpoint':
        pack = state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}
        return _same_id(_active_checkpoint_id(state, pack), trigger.get('checkpointId'))
    for change in changes:
        change_type = _text(change.get('type'))
        if trigger_type == 'on_enter' and change_type == 'scene.move_location':
            if _same_id(change.get('locationId'), trigger.get('locationId')):
                return True
        elif trigger_type == 'on_flag' and change_type == 'flag.set':
            if _same_id(change.get('flagKey'), trigger.get('flagKey')) and values_match(
                change.get('flagValue'),
                trigger.get('equals', True),
            ):
                return True
        elif trigger_type == 'on_interactable' and change_type == 'scene.interactable.action':
            event = change.get('event') if isinstance(change.get('event'), dict) else {}
            succeeded = not _text(event.get('type')).endswith('.check_failed')
            action = _text(trigger.get('action')).lower()
            if (
                succeeded
                and _same_id(change.get('targetId'), trigger.get('targetId'))
                and (not action or action == _text(change.get('objectAction')).lower())
            ):
                return True
    return False


def due_encounter(state: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first authored encounter whose ``trigger`` is due after ``changes``.

    ``on_checkpoint`` holds while its checkpoint is active; the other trigger
    types match one of the given applied changes. ``once`` (default true)
    skips encounters already triggered, ``when`` must hold and ``unless`` must
    not; an encounter an object action settled never fires again.
    """

    pack = state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}
    if not _text(pack.get('packId')):
        return None
    catalog = pack.get('catalog') if isinstance(pack.get('catalog'), dict) else {}
    flags = state.get('flags') if isinstance(state.get('flags'), dict) else {}
    triggered = set(_string_list(flags.get(TRIGGERED_ENCOUNTERS_FLAG)))
    resolved = set(_string_list(flags.get(RESOLVED_ENCOUNTERS_FLAG)))
    applied_changes = [change for change in changes if isinstance(change, dict)]
    for encounter in catalog.get('encounters') or []:
        if not isinstance(encounter, dict) or not isinstance(encounter.get('trigger'), dict):
            continue
        if encounter.get('once', True) is not False and _record_id(encounter) in triggered:
            continue
        if _record_id(encounter) in resolved:
            continue
        if 'when' in encounter and not flag_condition_holds(flags, encounter.get('when')):
            continue
        if flag_condition_holds(flags, encounter.get('unless')):
            continue
        if _trigger_matches(state, encounter['trigger'], applied_changes):
            return encounter
    return None


def combat_is_active(state: dict[str, Any]) -> bool:
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    return _text(combat.get('status')).lower() in {'starting', 'active'}


def triggered_encounter_start(
    state: dict[str, Any],
    encounter: dict[str, Any],
    *,
    turn_id: Any,
    cause_id: Any,
) -> dict[str, Any] | None:
    """A materialised ``combat.start`` for a due encounter, or None when the
    encounter has no enemies or no player character is present. ``cause_id``
    (the change that made it due) keeps a repeatable encounter's starts apart."""

    encounter_id = _record_id(encounter)
    trigger_type = _text((encounter.get('trigger') or {}).get('type')).lower()
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    settings = state.get('settings') if isinstance(state.get('settings'), dict) else {}
    change = materialize_campaign_pack_combat_start(
        state,
        {
            'id': stable_change_id(turn_id, 'combat.start', TRIGGER_STARTED_BY, encounter_id, cause_id),
            'turnId': turn_id,
            'type': 'combat.start',
            'campaignPackEncounterId': encounter_id,
            'campaignPackTrigger': {'encounterId': encounter_id, 'type': trigger_type},
            'combat': {
                'status': 'active',
                'round': 1,
                'turnIndex': 0,
                'participants': [],
                'battlefield': default_battlefield(scene),
                'encounterGoal': None,
                'initiative': [],
                'flags': {
                    'combatStartedBy': TRIGGER_STARTED_BY,
                    'initiativeRequired': True,
                    'initiativeSeed': f"combat:{state.get('sessionId')}:{turn_id}:{TRIGGER_STARTED_BY}:{encounter_id}",
                    'combatDifficultyAI': pack_combat_difficulty_ai(settings.get('combatDifficultyAI')),
                    'resolverMethod': 'campaign_pack',
                    'creatureSource': 'campaign_pack',
                },
            },
            'reason': f"{_text(encounter.get('title') or encounter.get('name')) or 'An authored encounter'} begins.",
            'visible': True,
        },
    )
    participants = (change.get('combat') or {}).get('participants') or []
    enemy_count = sum(1 for participant in participants if participant.get('team') == 'enemy')
    if not enemy_count or not any(_present_player(participant) for participant in participants):
        return None
    change['combat']['flags']['enemyCount'] = enemy_count
    return change


def record_triggered_encounter(state: dict[str, Any], applied_change: dict[str, Any]) -> None:
    """Remember an applied trigger start so a ``once`` encounter never repeats."""

    trigger = applied_change.get('campaignPackTrigger')
    if _text(applied_change.get('type')) != 'combat.start' or not isinstance(trigger, dict):
        return
    flags = state.setdefault('flags', {})
    triggered = _string_list(flags.get(TRIGGERED_ENCOUNTERS_FLAG))
    if _text(trigger.get('encounterId')) and _text(trigger.get('encounterId')) not in triggered:
        flags[TRIGGERED_ENCOUNTERS_FLAG] = [*triggered, _text(trigger.get('encounterId'))]


def location_entry_changes(state: dict[str, Any], location_id: Any, *, turn_id: Any) -> list[dict[str, Any]]:
    """Record a first arrival and return the location's ``onEnter`` consequences."""

    location_key = _text(location_id)
    flags = state.setdefault('flags', {})
    entered = _string_list(flags.get(LOCATION_ENTERED_FLAG))
    if not location_key or location_key in entered:
        return []
    flags[LOCATION_ENTERED_FLAG] = [*entered, location_key]
    location = catalog_record(state, 'locations', location_key) or next(
        (
            record
            for record in (state.get('locations') or [])
            if isinstance(record, dict) and _text(record.get('id')) == location_key
        ),
        {},
    )
    consequences = location.get('onEnter') if isinstance(location.get('onEnter'), list) else []
    return [
        {
            **deepcopy(consequence),
            'id': stable_change_id('location_enter', location_key, index),
            'turnId': turn_id,
            'source': WORLD_CHANGE_SOURCE,
            'reason': consequence.get('reason') or f"Arrival at {location.get('name') or location_key}.",
        }
        for index, consequence in enumerate(consequences)
        if isinstance(consequence, dict) and _text(consequence.get('type')) in CONSEQUENCE_CHANGE_TYPES
    ]


def campaign_pack_followup_changes(
    state: dict[str, Any],
    applied_change: dict[str, Any],
    *,
    combat_start_pending: bool,
) -> list[dict[str, Any]]:
    """Trusted changes that follow one applied change in a pack campaign."""

    pack = state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}
    if not _text(pack.get('packId')):
        return []
    record_triggered_encounter(state, applied_change)
    change_type = _text(applied_change.get('type'))
    turn_id = applied_change.get('turnId') or applied_change.get('turn_id')
    followups: list[dict[str, Any]] = encounter_resolution_changes(state, applied_change)
    if change_type == 'scene.move_location':
        followups.extend(location_entry_changes(state, applied_change.get('locationId'), turn_id=turn_id))
    if change_type in TRIGGER_EVENT_CHANGE_TYPES and not combat_start_pending and not combat_is_active(state):
        encounter = due_encounter(state, [applied_change])
        start = (
            triggered_encounter_start(state, encounter, turn_id=turn_id, cause_id=applied_change.get('id'))
            if encounter
            else None
        )
        if start:
            followups.append(start)
    return followups


def _scene_object(state: dict[str, Any], target_id: Any) -> dict[str, Any]:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    for collection in ('interactables', 'hazards'):
        for entry in scene.get(collection) or []:
            if isinstance(entry, dict) and _text(entry.get('id')) == _text(target_id):
                return entry
    return {}


def encounter_resolution_changes(state: dict[str, Any], applied_change: dict[str, Any]) -> list[dict[str, Any]]:
    """The change that settles an encounter when an object action authored to do so succeeds.

    ``resolvesEncounter: {<action>: {"encounterId", "outcome"}}`` on the object.
    While that encounter's combat is active it ends at once with the outcome;
    otherwise an ``encounter.resolve`` completes it without a fight. Either way
    the ordinary outcome transaction pays its rewards once, and the encounter
    is marked resolved here so its trigger never fires afterwards. An
    encounter already completed is not settled again.
    """

    if _text(applied_change.get('type')) != 'scene.interactable.action':
        return []
    event = applied_change.get('event') if isinstance(applied_change.get('event'), dict) else {}
    if _text(event.get('type')).endswith('.check_failed'):
        return []
    action = _text(applied_change.get('objectAction') or event.get('action')).lower()
    target = _scene_object(state, applied_change.get('targetId') or event.get('targetId'))
    spec = (target.get('resolvesEncounter') or {}).get(action) if isinstance(target.get('resolvesEncounter'), dict) else None
    if not isinstance(spec, dict):
        return []
    encounter = catalog_record(state, 'encounters', spec.get('encounterId'))
    end_reason = RESOLUTION_END_REASONS.get(_text(spec.get('outcome')).lower())
    if not encounter or not end_reason:
        return []
    encounter_id = _record_id(encounter)
    flags = state.setdefault('flags', {})
    if encounter_id in _string_list(flags.get(COMPLETED_ENCOUNTERS_FLAG)) or encounter_id in _string_list(flags.get(RESOLVED_ENCOUNTERS_FLAG)):
        return []
    flags[RESOLVED_ENCOUNTERS_FLAG] = [*_string_list(flags.get(RESOLVED_ENCOUNTERS_FLAG)), encounter_id]
    turn_id = applied_change.get('turnId') or applied_change.get('turn_id')
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    combat_flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    if combat_is_active(state) and _same_id(combat_flags.get('campaignPackEncounterId'), encounter_id):
        return [{**combat_end_change(turn_id, end_reason), 'visible': True, 'resolvedByTargetId': _text(target.get('id'))}]
    return [
        {
            'id': stable_change_id(event.get('id') or applied_change.get('id'), ENCOUNTER_RESOLVE_CHANGE, encounter_id),
            'turnId': turn_id,
            'type': ENCOUNTER_RESOLVE_CHANGE,
            'source': WORLD_CHANGE_SOURCE,
            'encounterId': encounter_id,
            'outcome': _text(spec.get('outcome')).lower(),
            'endReason': end_reason,
            'status': 'resolved',
            'resolvedByTargetId': _text(target.get('id')),
            'reason': f"{_text(encounter.get('title')) or encounter_id} is settled without a fight.",
            'visible': True,
        }
    ]


def materialize_catalog_consequence(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """Embed the catalog record a bare authored consequence names.

    ``clue.discover``, ``quest.add`` and ``location.discover`` with only an id
    become the authored record; a ``location.update`` naming a catalog
    location that is not yet known first discovers it.
    """

    change_type = _text(change.get('type'))
    if change_type not in CATALOG_CONSEQUENCES:
        return change
    collection, id_key, embedded_key = CATALOG_CONSEQUENCES[change_type]
    if isinstance(change.get(embedded_key), dict):
        return change
    record = catalog_record(state, collection, change.get(id_key))
    if not record:
        return change
    if change_type == 'location.update':
        known = any(
            isinstance(location, dict) and _text(location.get('id')) == _record_id(record)
            for location in (state.get('locations') or [])
        )
        if known:
            return change
    pack = state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}
    updated = deepcopy(change)
    updated[embedded_key] = {**deepcopy(record), 'source': 'campaign_pack', 'packId': _text(pack.get('packId'))}
    updated[id_key] = _record_id(record)
    label_key = 'title' if embedded_key in {'clue', 'quest'} else 'name'
    updated.setdefault(label_key, record.get(label_key) or record.get('name') or record.get('title'))
    if change_type == 'location.update':
        updated['type'] = 'location.discover'
    return updated


def declared_story_flags(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The pack's story flags with each one's current value."""

    pack = state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}
    flags = state.get('flags') if isinstance(state.get('flags'), dict) else {}
    return [
        {
            'key': _text(story_flag.get('key')),
            'when': _text(story_flag.get('when')),
            'values': deepcopy(story_flag.get('values')) if isinstance(story_flag.get('values'), list) else [True],
            'value': deepcopy(flags.get(_text(story_flag.get('key')))),
        }
        for story_flag in (pack.get('storyFlags') if isinstance(pack.get('storyFlags'), list) else [])
        if isinstance(story_flag, dict) and _text(story_flag.get('key'))
    ]


def story_flag_allows(state: dict[str, Any], flag_key: Any, value: Any) -> bool:
    """Whether narration may set this flag: it is a declared story flag and
    the value is one of its declared values."""

    return any(
        _same_id(story_flag['key'], flag_key) and any(values_match(value, allowed) for allowed in story_flag['values'])
        for story_flag in declared_story_flags(state)
    )


def _record_id(record: dict[str, Any]) -> str:
    return _text(record.get('id') or record.get('encounterId') or record.get('checkpointId') or record.get('creatureId'))


def _text(value: Any) -> str:
    return str(value or '').strip()


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, str):
        raw_values = [item.strip() for item in value.replace(';', ',').split(',')]
    elif value in (None, ''):
        raw_values = []
    else:
        raw_values = [value]
    result: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        text = _text(raw_value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
