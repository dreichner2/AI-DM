"""Pure, ledger-stable encounter outcome and reward derivation.

This module deliberately does not mutate state.  It is intended to run after
an authoritative ``combat.end`` change has been applied and persisted in the
session snapshot.  The caller may send ``changes`` through the normal
validation/application pipeline and feed ``questEvents`` to the mechanical
quest engine.  Persisting the returned ``outcomeLedgerId`` after every output
has committed provides an inexpensive all-or-nothing replay marker; individual
output IDs are stable and are also safe to replay through the state ledger.

Encounter authoring contract
----------------------------

Common rewards live in ``encounter.rewards`` and apply only to successful
outcomes.  Outcome-specific records can be authored under either
``encounter.outcomes.<outcome>.rewards`` or
``encounter.outcomeRewards.<outcome>``.  Supported canonical outcome keys are
``victory``, ``defeat``, ``retreat``, ``surrender``, ``negotiation``, and
``objective_completion``.

Reward containers accept ``xp``/``experience``, currency codes, ``items``,
``flags``, ``questEvents``, or an explicit list of typed reward records.
Actor-bound rewards support ``split``, ``each``, ``first``, and ``actor``
allocation.  XP and currency default to ``split``; items default to ``first``.
An explicit ``actorId`` selects ``actor`` allocation.  Only player actors who
were physically present combat participants are eligible; defeated or
unconscious participants remain eligible, while absent participants do not.

Consequences may be common, outcome-specific, or named ``onVictory`` / etc.
They are limited to existing world-state consequence types.  Quest progression
is represented by stable gameplay events rather than direct terminal quest
mutation, preserving the quest engine as the source of truth.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.game_state.models import stable_change_id, stable_item_instance_id


SOURCE = 'combat_reward_engine'

END_REASON_OUTCOMES = {
    'all_enemies_defeated': 'victory',
    'enemies_fled': 'victory',
    'victory': 'victory',
    'objective_failed': 'defeat',
    'party_defeated': 'defeat',
    'players_defeated': 'defeat',
    'defeat': 'defeat',
    'players_fled': 'retreat',
    'party_retreated': 'retreat',
    'retreat': 'retreat',
    'enemies_surrendered': 'surrender',
    'surrender': 'surrender',
    'negotiated_resolution': 'negotiation',
    'negotiation': 'negotiation',
    'objective_completed': 'objective_completion',
    'objective_completion': 'objective_completion',
}

SUPPORTED_OUTCOMES = frozenset(END_REASON_OUTCOMES.values())
SUCCESSFUL_OUTCOMES = frozenset({'victory', 'surrender', 'negotiation', 'objective_completion'})
CURRENCY_CODES = frozenset({'pp', 'gp', 'ep', 'sp', 'cp'})
ALLOCATION_MODES = frozenset({'split', 'each', 'first', 'actor'})

CONSEQUENCE_TYPES = frozenset(
    {
        'flag.set',
        'flag.unset',
        'npc.update',
        'npc.relationship.update',
        'npc.move',
        'location.update',
        'scene.update',
        'scene.item.add',
        'scene.item.remove',
    }
)

OUTCOME_HOOKS = {
    'victory': 'onVictory',
    'defeat': 'onDefeat',
    'retreat': 'onRetreat',
    'surrender': 'onSurrender',
    'negotiation': 'onNegotiation',
    'objective_completion': 'onObjectiveCompletion',
}


def _text(value: Any) -> str:
    return str(value or '').strip()


def _key(value: Any) -> str:
    snake_case = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', _text(value))
    return re.sub(r'[^a-z0-9]+', '_', snake_case.lower()).strip('_')


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _record_id(record: dict[str, Any]) -> str:
    return _text(record.get('id') or record.get('encounterId') or record.get('encounter_id'))


def canonical_combat_outcome(end_reason: Any) -> str | None:
    """Return the supported gameplay outcome for an authoritative end reason."""

    return END_REASON_OUTCOMES.get(_key(end_reason))


def _result(
    *,
    valid: bool,
    reason: str,
    outcome: str | None = None,
    end_reason: str = '',
    encounter_id: str = '',
    outcome_ledger_id: str = '',
    eligible_actor_ids: list[str] | None = None,
    changes: list[dict[str, Any]] | None = None,
    quest_events: list[dict[str, Any]] | None = None,
    skipped: list[dict[str, Any]] | None = None,
    ledger_ids: list[str] | None = None,
    pending_ledger_ids: list[str] | None = None,
    already_applied: bool = False,
) -> dict[str, Any]:
    return {
        'valid': valid,
        'reason': reason,
        'outcome': outcome,
        'endReason': end_reason,
        'encounterId': encounter_id,
        'outcomeLedgerId': outcome_ledger_id,
        'eligibleActorIds': eligible_actor_ids or [],
        'changes': changes or [],
        'questEvents': quest_events or [],
        'skipped': skipped or [],
        'ledgerIds': ledger_ids or [],
        'pendingLedgerIds': pending_ledger_ids or [],
        'alreadyApplied': already_applied,
    }


def _combat(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get('combat') if isinstance(state, dict) else None
    return value if isinstance(value, dict) else {}


def _combat_flags(combat: dict[str, Any]) -> dict[str, Any]:
    flags = combat.get('flags')
    return flags if isinstance(flags, dict) else {}


def _pack_encounter(state: dict[str, Any], encounter_id: str) -> dict[str, Any]:
    pack = state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}
    catalog = pack.get('catalog') if isinstance(pack.get('catalog'), dict) else {}
    records = catalog.get('encounters')
    if not isinstance(records, list):
        records = pack.get('encounters') if isinstance(pack.get('encounters'), list) else []
    requested = _key(encounter_id)
    return next(
        (
            deepcopy(record)
            for record in records
            if isinstance(record, dict) and _key(_record_id(record)) == requested
        ),
        {},
    )


def _resolve_encounter(
    state: dict[str, Any],
    combat: dict[str, Any],
    outcome_change: dict[str, Any],
    encounter: dict[str, Any] | None,
) -> tuple[dict[str, Any], str, str | None]:
    flags = _combat_flags(combat)
    goal = combat.get('encounterGoal') if isinstance(combat.get('encounterGoal'), dict) else {}
    combat_id = _text(
        flags.get('campaignPackEncounterId')
        or flags.get('campaign_pack_encounter_id')
        or flags.get('encounterId')
        or flags.get('encounter_id')
        or combat.get('encounterId')
        or combat.get('encounter_id')
        or goal.get('encounterId')
        or goal.get('encounter_id')
    )
    change_id = _text(outcome_change.get('encounterId') or outcome_change.get('encounter_id'))
    supplied = deepcopy(encounter) if isinstance(encounter, dict) else {}
    supplied_id = _record_id(supplied)
    known_ids = [value for value in (combat_id, change_id, supplied_id) if value]
    if known_ids and any(_key(value) != _key(known_ids[0]) for value in known_ids[1:]):
        return {}, '', 'Combat, outcome change, and supplied encounter IDs disagree.'
    encounter_id = known_ids[0] if known_ids else ''
    if not encounter_id:
        return {}, '', 'A stable encounter ID is required for outcome rewards.'
    if supplied:
        supplied.setdefault('id', encounter_id)
        return supplied, encounter_id, None
    resolved = _pack_encounter(state, encounter_id)
    if resolved:
        return resolved, encounter_id, None
    return {'id': encounter_id}, encounter_id, None


def _ledger_ids(state: dict[str, Any], applied_ledger_ids: Any) -> set[str]:
    seen: set[str] = set()
    for collection_key in ('stateChangeLedger', 'gameplayEventLedger', 'combatRewardLedger'):
        for entry in _list(state.get(collection_key)):
            value = entry.get('id') if isinstance(entry, dict) else entry
            if _text(value):
                seen.add(_text(value))
    for entry in _list(applied_ledger_ids):
        value = entry.get('id') if isinstance(entry, dict) else entry
        if _text(value):
            seen.add(_text(value))
    return seen


def _conditions(participant: dict[str, Any]) -> set[str]:
    return {_key(value) for value in _list(participant.get('conditions')) if _key(value)}


def _eligible_actor_ids(state: dict[str, Any], combat: dict[str, Any]) -> list[str]:
    actor_ids = {
        _text(actor.get('id'))
        for actor in _list(state.get('playerCharacters'))
        if isinstance(actor, dict) and _text(actor.get('id'))
    }
    eligible: set[str] = set()
    for participant in _list(combat.get('participants')):
        if not isinstance(participant, dict) or _key(participant.get('team')) != 'player':
            continue
        participant_id = _text(participant.get('id') or participant.get('actorId'))
        if participant_id not in actor_ids:
            continue
        if participant.get('isPresent', participant.get('present', True)) is False:
            continue
        if 'absent' in _conditions(participant):
            continue
        eligible.add(participant_id)
    return sorted(eligible)


def _mapping_value(mapping: Any, outcome: str, raw_end_reason: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    keyed = {_key(key): value for key, value in mapping.items()}
    for candidate in (raw_end_reason, outcome):
        if _key(candidate) in keyed:
            return keyed[_key(candidate)]
    return None


def _outcome_spec(encounter: dict[str, Any], outcome: str, raw_end_reason: str) -> dict[str, Any]:
    value = _mapping_value(encounter.get('outcomes'), outcome, raw_end_reason)
    return deepcopy(value) if isinstance(value, dict) else {}


def _apply_container_allocation(record: dict[str, Any], container: dict[str, Any]) -> dict[str, Any]:
    copied = deepcopy(record)
    if copied.get('allocation') is None and isinstance(container.get('allocation'), str):
        copied['allocation'] = container.get('allocation')
    return copied


def _reward_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [deepcopy(item) for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    if _text(value.get('type')):
        return [deepcopy(value)]

    records: list[dict[str, Any]] = []
    for item in _list(value.get('records')):
        if isinstance(item, dict):
            records.append(_apply_container_allocation(item, value))

    xp = value.get('xp', value.get('experience'))
    if xp is not None:
        records.append(_apply_container_allocation({'type': 'xp', 'amount': xp}, value))

    currency_container = value.get('currency')
    if isinstance(currency_container, dict):
        for code, amount in currency_container.items():
            if _key(code) in CURRENCY_CODES:
                records.append(
                    _apply_container_allocation(
                        {'type': 'currency', 'currency': _key(code), 'amount': amount},
                        value,
                    )
                )
    for code in sorted(CURRENCY_CODES):
        if value.get(code) is not None:
            records.append(
                _apply_container_allocation(
                    {'type': 'currency', 'currency': code, 'amount': value.get(code)},
                    value,
                )
            )

    items = value.get('items')
    if isinstance(items, dict):
        items = [items]
    for item in _list(items):
        if isinstance(item, dict):
            records.append(_apply_container_allocation({'type': 'item', 'item': deepcopy(item)}, value))

    flags = value.get('flags')
    if isinstance(flags, dict):
        if _text(flags.get('flagKey') or flags.get('key')):
            flags = [flags]
        else:
            flags = [{'flagKey': key, 'flagValue': flag_value} for key, flag_value in flags.items()]
    for flag in _list(flags):
        if isinstance(flag, dict):
            records.append({'type': 'flag', **deepcopy(flag)})

    for event in _list(value.get('questEvents')):
        if isinstance(event, dict):
            records.append({'type': 'quest_event', 'event': deepcopy(event)})
    return records


def _reward_sources(
    encounter: dict[str, Any],
    outcome_spec: dict[str, Any],
    outcome: str,
    raw_end_reason: str,
) -> list[Any]:
    sources: list[Any] = []
    if outcome in SUCCESSFUL_OUTCOMES:
        base = encounter.get('rewards', encounter.get('reward'))
        if base is not None:
            sources.append(base)
    if outcome_spec:
        specific = outcome_spec.get('rewards', outcome_spec.get('reward'))
        if specific is None and any(
            key in outcome_spec
            for key in ('xp', 'experience', 'pp', 'gp', 'ep', 'sp', 'cp', 'currency', 'items', 'flags', 'records')
        ):
            specific = outcome_spec
        if specific is not None:
            sources.append(specific)
    mapped = _mapping_value(encounter.get('outcomeRewards'), outcome, raw_end_reason)
    if mapped is None:
        mapped = _mapping_value(encounter.get('outcome_rewards'), outcome, raw_end_reason)
    if mapped is not None:
        sources.append(mapped)
    return sources


def _allocation_setting(value: Any, reward_type: str) -> str:
    if isinstance(value, str):
        return _key(value)
    if not isinstance(value, dict):
        return ''
    aliases = {
        'xp': ('xp', 'experience'),
        'currency': ('currency', 'money', 'coins'),
        'item': ('item', 'items', 'loot'),
    }
    for key in (*aliases.get(reward_type, (reward_type,)), 'default'):
        if isinstance(value.get(key), str):
            return _key(value.get(key))
    return ''


def _allocation_mode(
    reward: dict[str, Any],
    reward_type: str,
    encounter: dict[str, Any],
    outcome_spec: dict[str, Any],
) -> str:
    if _text(reward.get('actorId') or reward.get('actor_id')):
        return 'actor'
    mode = _allocation_setting(reward.get('allocation'), reward_type)
    if not mode:
        mode = _allocation_setting(outcome_spec.get('partyAllocation'), reward_type)
    if not mode:
        mode = _allocation_setting(encounter.get('partyAllocation'), reward_type)
    mode = {'all': 'each', 'party': 'split', 'single': 'first'}.get(mode, mode)
    if mode in ALLOCATION_MODES:
        return mode
    return 'first' if reward_type == 'item' else 'split'


def _reward_actor_ids(reward: dict[str, Any], eligible_actor_ids: list[str]) -> list[str]:
    requested = reward.get('actorIds') if isinstance(reward.get('actorIds'), list) else reward.get('actor_ids')
    if isinstance(requested, list):
        wanted = {_text(value) for value in requested if _text(value)}
        return [actor_id for actor_id in eligible_actor_ids if actor_id in wanted]
    return list(eligible_actor_ids)


def _allocate_amount(amount: int, mode: str, actor_ids: list[str], actor_id: str) -> list[tuple[str, int]]:
    if amount <= 0:
        return []
    if mode == 'actor':
        return [(actor_id, amount)] if actor_id in actor_ids else []
    if not actor_ids:
        return []
    if mode == 'each':
        return [(candidate, amount) for candidate in actor_ids]
    if mode == 'first':
        return [(actor_ids[0], amount)]
    quotient, remainder = divmod(amount, len(actor_ids))
    return [
        (candidate, quotient + (1 if index < remainder else 0))
        for index, candidate in enumerate(actor_ids)
        if quotient + (1 if index < remainder else 0) > 0
    ]


def _base_change(
    *,
    outcome_change: dict[str, Any],
    encounter_id: str,
    outcome: str,
    change_id: str,
    reason: str,
) -> dict[str, Any]:
    return {
        'id': change_id,
        'turnId': outcome_change.get('turnId') or outcome_change.get('turn_id'),
        'source': SOURCE,
        'encounterId': encounter_id,
        'combatOutcome': outcome,
        'endReason': _key(outcome_change.get('endReason') or outcome_change.get('end_reason')),
        'reason': reason,
        'visible': True,
    }


def _derive_reward_changes(
    *,
    reward_records: list[dict[str, Any]],
    encounter: dict[str, Any],
    outcome_spec: dict[str, Any],
    outcome_change: dict[str, Any],
    encounter_id: str,
    outcome: str,
    scope_id: str,
    eligible_actor_ids: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    changes: list[dict[str, Any]] = []
    event_records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    encounter_label = _text(encounter.get('title') or encounter.get('name') or encounter_id)

    for index, reward in enumerate(reward_records):
        reward_type = _key(reward.get('type'))
        if reward_type == 'quest_event':
            event = reward.get('event') if isinstance(reward.get('event'), dict) else reward
            event_records.append(deepcopy(event))
            continue
        if reward_type == 'flag':
            flag_key = _text(reward.get('flagKey') or reward.get('key'))
            if not flag_key:
                skipped.append({'category': 'reward', 'index': index, 'reason': 'Flag reward has no key.'})
                continue
            change_id = stable_change_id(scope_id, 'reward', index, 'flag', flag_key)
            changes.append(
                {
                    **_base_change(
                        outcome_change=outcome_change,
                        encounter_id=encounter_id,
                        outcome=outcome,
                        change_id=change_id,
                        reason=f"One-time {outcome} reward for encounter '{encounter_label}'.",
                    ),
                    'type': 'flag.set',
                    'flagKey': flag_key,
                    'flagValue': reward.get('flagValue', reward.get('value', True)),
                }
            )
            continue
        if reward_type not in {'xp', 'currency', 'item'}:
            skipped.append(
                {'category': 'reward', 'index': index, 'reason': f"Unsupported reward type '{reward_type or 'missing'}'."}
            )
            continue

        actor_ids = _reward_actor_ids(reward, eligible_actor_ids)
        mode = _allocation_mode(reward, reward_type, encounter, outcome_spec)
        explicit_actor = _text(reward.get('actorId') or reward.get('actor_id'))
        if explicit_actor and explicit_actor not in actor_ids:
            skipped.append({'category': 'reward', 'index': index, 'reason': 'Explicit reward actor is not an eligible participant.'})
            continue
        if not actor_ids:
            skipped.append({'category': 'reward', 'index': index, 'reason': 'No eligible player participant can receive this reward.'})
            continue

        if reward_type in {'xp', 'currency'}:
            amount = int_or_default(reward.get('amount'), default=0)
            if amount <= 0:
                skipped.append({'category': 'reward', 'index': index, 'reason': 'Reward amount must be positive.'})
                continue
            currency = _key(reward.get('currency')) if reward_type == 'currency' else ''
            if reward_type == 'currency' and currency not in CURRENCY_CODES:
                skipped.append({'category': 'reward', 'index': index, 'reason': 'Currency reward uses an unsupported code.'})
                continue
            allocations = _allocate_amount(amount, mode, actor_ids, explicit_actor)
            if not allocations:
                skipped.append({'category': 'reward', 'index': index, 'reason': 'Reward allocation produced no valid recipient.'})
                continue
            for actor_id, share in allocations:
                change_id = stable_change_id(scope_id, 'reward', index, reward_type, actor_id)
                change = {
                    **_base_change(
                        outcome_change=outcome_change,
                        encounter_id=encounter_id,
                        outcome=outcome,
                        change_id=change_id,
                        reason=f"One-time {outcome} reward for encounter '{encounter_label}'.",
                    ),
                    'type': 'xp.add' if reward_type == 'xp' else 'currency.add',
                    'actorId': actor_id,
                    'amount': share,
                    'partyAllocation': mode,
                    'partyRewardAmount': amount,
                }
                if currency:
                    change['currency'] = currency
                changes.append(change)
            continue

        item = reward.get('item') if isinstance(reward.get('item'), dict) else {}
        if not item:
            item = {key: deepcopy(value) for key, value in reward.items() if key not in {'type', 'allocation', 'actorId', 'actorIds'}}
        if not _text(item.get('id') or item.get('name')):
            skipped.append({'category': 'reward', 'index': index, 'reason': 'Item reward requires an id or name.'})
            continue
        quantity = max(0, int_or_default(item.get('quantity', reward.get('quantity')), default=1))
        if quantity <= 0:
            skipped.append({'category': 'reward', 'index': index, 'reason': 'Item reward quantity must be positive.'})
            continue
        allocations = _allocate_amount(quantity, mode, actor_ids, explicit_actor)
        if not allocations:
            skipped.append({'category': 'reward', 'index': index, 'reason': 'Item allocation produced no valid recipient.'})
            continue
        authored_item_id = _text(item.get('id'))
        for actor_id, share in allocations:
            instance_id = stable_item_instance_id(SOURCE, scope_id, 'reward', index, actor_id)
            instance = {**deepcopy(item), 'id': instance_id, 'quantity': share}
            if authored_item_id:
                instance['sourceItemId'] = authored_item_id
            change_id = stable_change_id(scope_id, 'reward', index, 'item', actor_id)
            changes.append(
                {
                    **_base_change(
                        outcome_change=outcome_change,
                        encounter_id=encounter_id,
                        outcome=outcome,
                        change_id=change_id,
                        reason=f"One-time {outcome} loot for encounter '{encounter_label}'.",
                    ),
                    'type': 'inventory.add',
                    'actorId': actor_id,
                    'item': instance,
                    'itemId': instance_id,
                    'itemName': instance.get('name'),
                    'quantity': share,
                    'partyAllocation': mode,
                    'partyRewardQuantity': quantity,
                }
            )
    return changes, event_records, skipped


def _consequence_records(
    encounter: dict[str, Any],
    outcome_spec: dict[str, Any],
    outcome: str,
    raw_end_reason: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    common = encounter.get('consequences')
    if isinstance(common, list):
        records.extend(deepcopy(item) for item in common if isinstance(item, dict))
    elif isinstance(common, dict):
        mapped = _mapping_value(common, outcome, raw_end_reason)
        records.extend(deepcopy(item) for item in _list(mapped) if isinstance(item, dict))
    records.extend(
        deepcopy(item)
        for item in _list(outcome_spec.get('consequences'))
        if isinstance(item, dict)
    )
    mapped = _mapping_value(encounter.get('outcomeConsequences'), outcome, raw_end_reason)
    if mapped is None:
        mapped = _mapping_value(encounter.get('outcome_consequences'), outcome, raw_end_reason)
    records.extend(deepcopy(item) for item in _list(mapped) if isinstance(item, dict))
    hook = encounter.get(OUTCOME_HOOKS[outcome])
    records.extend(deepcopy(item) for item in _list(hook) if isinstance(item, dict))
    return records


def _derive_consequence_changes(
    *,
    consequences: list[dict[str, Any]],
    outcome_change: dict[str, Any],
    encounter_id: str,
    outcome: str,
    scope_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    changes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for index, consequence in enumerate(consequences):
        change_type = _text(consequence.get('type'))
        if change_type not in CONSEQUENCE_TYPES:
            skipped.append(
                {
                    'category': 'consequence',
                    'index': index,
                    'reason': f"Unsupported consequence type '{change_type or 'missing'}'.",
                }
            )
            continue
        if change_type in {'flag.set', 'flag.unset'} and not _text(consequence.get('flagKey')):
            skipped.append({'category': 'consequence', 'index': index, 'reason': 'Flag consequence has no key.'})
            continue
        change_id = stable_change_id(scope_id, 'consequence', index, change_type)
        changes.append(
            {
                **deepcopy(consequence),
                **_base_change(
                    outcome_change=outcome_change,
                    encounter_id=encounter_id,
                    outcome=outcome,
                    change_id=change_id,
                    reason=consequence.get('reason') or f"One-time {outcome} consequence for encounter '{encounter_id}'.",
                ),
                'type': change_type,
            }
        )
    return changes, skipped


def _quest_event_records(encounter: dict[str, Any], outcome_spec: dict[str, Any], reward_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = [deepcopy(item) for item in _list(encounter.get('questEvents')) if isinstance(item, dict)]
    records.extend(deepcopy(item) for item in _list(outcome_spec.get('questEvents')) if isinstance(item, dict))
    records.extend(deepcopy(item) for item in reward_events if isinstance(item, dict))
    return records


def _linked_quest_ids(encounter: dict[str, Any]) -> list[str]:
    values = encounter.get('questIds')
    if not isinstance(values, list):
        values = encounter.get('quest_ids')
    return sorted({_text(value) for value in _list(values) if _text(value)})


def _derive_quest_events(
    *,
    encounter: dict[str, Any],
    authored_events: list[dict[str, Any]],
    outcome_change: dict[str, Any],
    encounter_id: str,
    outcome: str,
    scope_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    linked_quest_ids = _linked_quest_ids(encounter)

    for quest_id in linked_quest_ids:
        event_id = stable_change_id(scope_id, 'quest_event', 'combat.outcome', quest_id)
        events.append(
            {
                **_base_change(
                    outcome_change=outcome_change,
                    encounter_id=encounter_id,
                    outcome=outcome,
                    change_id=event_id,
                    reason=f"Authoritative encounter outcome event for quest '{quest_id}'.",
                ),
                'type': 'combat.outcome',
                'eventType': 'combat.outcome',
                'questId': quest_id,
                'objectiveId': None,
            }
        )

    for index, authored in enumerate(authored_events):
        event_type = _text(authored.get('eventType') or authored.get('event') or authored.get('changeType'))
        if not event_type and _key(authored.get('type')) not in {'', 'quest_event'}:
            event_type = _text(authored.get('type'))
        if not event_type:
            event_type = 'combat.outcome'
        explicit_quest_id = _text(authored.get('questId') or authored.get('quest_id'))
        quest_ids = [explicit_quest_id] if explicit_quest_id else linked_quest_ids
        if not quest_ids:
            skipped.append({'category': 'quest_event', 'index': index, 'reason': 'Quest event has no exact quest ID.'})
            continue
        for quest_id in quest_ids:
            event_id = stable_change_id(scope_id, 'quest_event', index, event_type, quest_id)
            event = {
                **deepcopy(authored),
                **_base_change(
                    outcome_change=outcome_change,
                    encounter_id=encounter_id,
                    outcome=outcome,
                    change_id=event_id,
                    reason=authored.get('reason') or f"Authoritative encounter event for quest '{quest_id}'.",
                ),
                'type': event_type,
                'eventType': event_type,
                'questId': quest_id,
                'objectiveId': authored.get('objectiveId') or authored.get('objective_id'),
            }
            events.append(event)

    unique: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for event in events:
        event_id = _text(event.get('id'))
        if event_id and event_id not in seen_ids:
            unique.append(event)
            seen_ids.add(event_id)
    return unique, skipped


def derive_combat_outcome_rewards(
    state: dict[str, Any],
    outcome_change: dict[str, Any],
    *,
    encounter: dict[str, Any] | None = None,
    applied_ledger_ids: list[Any] | None = None,
) -> dict[str, Any]:
    """Derive exact-once changes and quest events for an ended encounter.

    The function fails closed unless ``outcome_change`` is a stable, ended
    ``combat.end`` event whose reason agrees with the persisted ended combat.
    It never mutates ``state``, ``outcome_change``, or ``encounter``.
    """

    if not isinstance(state, dict) or not isinstance(outcome_change, dict):
        return _result(valid=False, reason='State and outcome change must be records.')
    if _text(outcome_change.get('type')) != 'combat.end':
        return _result(valid=False, reason='Rewards require an authoritative combat.end change.')
    outcome_change_id = _text(outcome_change.get('id'))
    if not outcome_change_id:
        return _result(valid=False, reason='Combat end change requires a stable ledger ID.')
    if _key(outcome_change.get('status')) not in {'ended', 'resolved'}:
        return _result(valid=False, reason='Combat end change is not terminal.')

    combat = _combat(state)
    if _key(combat.get('status')) not in {'ended', 'resolved'}:
        return _result(valid=False, reason='Persisted combat is not ended.')
    flags = _combat_flags(combat)
    persisted_reason = _key(flags.get('endReason') or flags.get('end_reason'))
    change_reason = _key(outcome_change.get('endReason') or outcome_change.get('end_reason'))
    if not persisted_reason or persisted_reason != change_reason:
        return _result(
            valid=False,
            reason='Combat end reason does not match persisted authoritative state.',
            end_reason=change_reason,
        )
    outcome = canonical_combat_outcome(change_reason)
    if outcome not in SUPPORTED_OUTCOMES:
        return _result(
            valid=False,
            reason=f"Combat end reason '{change_reason or 'missing'}' is not rewardable.",
            end_reason=change_reason,
        )

    resolved_encounter, encounter_id, encounter_error = _resolve_encounter(
        state,
        combat,
        outcome_change,
        encounter,
    )
    if encounter_error:
        return _result(
            valid=False,
            reason=encounter_error,
            outcome=outcome,
            end_reason=change_reason,
        )

    scope_id = stable_change_id(SOURCE, outcome_change_id, encounter_id, outcome)
    known_ledger_ids = _ledger_ids(state, applied_ledger_ids)
    eligible_actor_ids = _eligible_actor_ids(state, combat)
    if scope_id in known_ledger_ids:
        return _result(
            valid=True,
            reason='Encounter outcome rewards were already finalized.',
            outcome=outcome,
            end_reason=change_reason,
            encounter_id=encounter_id,
            outcome_ledger_id=scope_id,
            eligible_actor_ids=eligible_actor_ids,
            ledger_ids=[scope_id],
            already_applied=True,
        )

    specific = _outcome_spec(resolved_encounter, outcome, change_reason)
    rewards = [
        reward
        for source in _reward_sources(resolved_encounter, specific, outcome, change_reason)
        for reward in _reward_records(source)
    ]
    reward_changes, reward_events, reward_skips = _derive_reward_changes(
        reward_records=rewards,
        encounter=resolved_encounter,
        outcome_spec=specific,
        outcome_change=outcome_change,
        encounter_id=encounter_id,
        outcome=outcome,
        scope_id=scope_id,
        eligible_actor_ids=eligible_actor_ids,
    )
    consequence_changes, consequence_skips = _derive_consequence_changes(
        consequences=_consequence_records(resolved_encounter, specific, outcome, change_reason),
        outcome_change=outcome_change,
        encounter_id=encounter_id,
        outcome=outcome,
        scope_id=scope_id,
    )
    quest_events, quest_event_skips = _derive_quest_events(
        encounter=resolved_encounter,
        authored_events=_quest_event_records(resolved_encounter, specific, reward_events),
        outcome_change=outcome_change,
        encounter_id=encounter_id,
        outcome=outcome,
        scope_id=scope_id,
    )

    all_changes = [*reward_changes, *consequence_changes]
    all_ids = [
        scope_id,
        *[_text(change.get('id')) for change in all_changes if _text(change.get('id'))],
        *[_text(event.get('id')) for event in quest_events if _text(event.get('id'))],
    ]
    pending_changes = [change for change in all_changes if _text(change.get('id')) not in known_ledger_ids]
    pending_events = [event for event in quest_events if _text(event.get('id')) not in known_ledger_ids]
    pending_ids = [
        *[_text(change.get('id')) for change in pending_changes],
        *[_text(event.get('id')) for event in pending_events],
    ]
    already_applied = not pending_ids and len(all_ids) > 1
    return _result(
        valid=True,
        reason='Derived authoritative encounter outcome rewards and consequences.',
        outcome=outcome,
        end_reason=change_reason,
        encounter_id=encounter_id,
        outcome_ledger_id=scope_id,
        eligible_actor_ids=eligible_actor_ids,
        changes=pending_changes,
        quest_events=pending_events,
        skipped=[*reward_skips, *consequence_skips, *quest_event_skips],
        ledger_ids=all_ids,
        pending_ledger_ids=pending_ids,
        already_applied=already_applied,
    )


__all__ = [
    'END_REASON_OUTCOMES',
    'SUCCESSFUL_OUTCOMES',
    'SUPPORTED_OUTCOMES',
    'canonical_combat_outcome',
    'derive_combat_outcome_rewards',
]
