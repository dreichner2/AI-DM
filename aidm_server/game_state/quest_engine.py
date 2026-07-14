"""Deterministic quest progression driven by applied gameplay changes.

The narration/extraction layer may propose quest changes, but objectives that
declare ``completeWhen``/``failWhen`` rules are owned by this module.  Rules
are deliberately small and data-oriented so campaign packs and emergent quests
share the same persisted representation without introducing a second quest
store.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.game_state.models import actor_items, find_actor, normalize_item_name, stable_change_id


TERMINAL_QUEST_STATUSES = {'completed', 'failed', 'abandoned'}
TERMINAL_OBJECTIVE_STATUSES = {'completed', 'failed'}
CONSEQUENCE_CHANGE_TYPES = {
    'flag.set',
    'flag.unset',
    'npc.update',
    'npc.relationship.update',
    'location.update',
    'scene.update',
}


def _text(value: Any) -> str:
    return str(value or '').strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _quest_id(quest: dict[str, Any]) -> str:
    return _text(quest.get('id') or quest.get('questId'))


def _objective_id(objective: dict[str, Any]) -> str:
    return _text(objective.get('id') or objective.get('objectiveId'))


def _objective_status(objective: dict[str, Any]) -> str:
    return _text(objective.get('status') or 'open').lower()


def _quest_by_id(state: dict[str, Any], quest_id: Any) -> dict[str, Any] | None:
    requested = _text(quest_id)
    return next(
        (
            quest
            for quest in _list(state.get('quests'))
            if isinstance(quest, dict) and _quest_id(quest) == requested
        ),
        None,
    )


def _objective_by_id(quest: dict[str, Any], objective_id: Any) -> dict[str, Any] | None:
    requested = _text(objective_id)
    return next(
        (
            objective
            for objective in _list(quest.get('objectives'))
            if isinstance(objective, dict) and _objective_id(objective) == requested
        ),
        None,
    )


def _rule(objective: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = objective.get(key)
    if isinstance(value, dict):
        return value
    rules = objective.get('rules') if isinstance(objective.get('rules'), dict) else {}
    value = rules.get(key)
    return value if isinstance(value, dict) else None


def objective_is_mechanical(objective: dict[str, Any]) -> bool:
    return bool(
        _rule(objective, 'completeWhen')
        or _rule(objective, 'failWhen')
        or _list(objective.get('prerequisiteObjectiveIds'))
        or _list(objective.get('prerequisites'))
    )


def quest_is_mechanical(quest: dict[str, Any]) -> bool:
    return any(
        objective_is_mechanical(objective)
        for objective in _list(quest.get('objectives'))
        if isinstance(objective, dict)
    )


def _prerequisite_ids(objective: dict[str, Any]) -> list[str]:
    values = objective.get('prerequisiteObjectiveIds')
    if not isinstance(values, list):
        values = objective.get('prerequisites')
    return [_text(value) for value in _list(values) if _text(value)]


def prerequisites_satisfied(quest: dict[str, Any], objective: dict[str, Any]) -> bool:
    prerequisite_ids = _prerequisite_ids(objective)
    if not prerequisite_ids:
        return True
    return all(
        (match := _objective_by_id(quest, prerequisite_id)) is not None
        and _objective_status(match) == 'completed'
        for prerequisite_id in prerequisite_ids
    )


def _same(expected: Any, actual: Any) -> bool:
    if isinstance(expected, str) or isinstance(actual, str):
        return _text(expected).casefold() == _text(actual).casefold()
    return expected == actual


def _change_value(change: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in change and change.get(key) is not None:
            return change.get(key)
    return None


def _event_matches(rule: dict[str, Any], change: dict[str, Any]) -> bool:
    event_type = _text(rule.get('eventType') or rule.get('event') or rule.get('changeType'))
    if event_type and event_type != _text(change.get('type')):
        return False
    field_aliases = {
        'actorId': ('actorId', 'actor_id'),
        'targetActorId': ('targetActorId', 'target_actor_id', 'toActorId'),
        'targetId': ('targetId', 'target_id', 'objectId', 'object_id'),
        'objectId': ('objectId', 'object_id', 'targetId', 'target_id'),
        'itemId': ('itemId', 'item_id'),
        'itemName': ('itemName', 'item_name'),
        'npcId': ('npcId', 'npc_id'),
        'locationId': ('locationId', 'location_id'),
        'encounterId': ('encounterId', 'encounter_id'),
        'participantId': ('participantId', 'participant_id'),
        'endReason': ('endReason', 'end_reason'),
        'flagKey': ('flagKey', 'flag_key'),
        'checkpointId': ('checkpointId', 'checkpoint_id'),
        'objectiveId': ('objectiveId', 'objective_id'),
        'questId': ('questId', 'quest_id'),
    }
    for rule_key, aliases in field_aliases.items():
        if rule.get(rule_key) is None:
            continue
        actual = _change_value(change, *aliases)
        if actual is None or not _same(rule.get(rule_key), actual):
            return False
    minimum = rule.get('quantityAtLeast', rule.get('amountAtLeast'))
    if minimum is not None:
        actual_amount = int_or_default(
            _change_value(change, 'actualAmount', 'quantity', 'amount'),
            default=0,
        )
        if actual_amount < max(0, int_or_default(minimum, default=0)):
            return False
    return bool(event_type or any(rule.get(key) is not None for key in field_aliases))


def _state_matches(
    rule: dict[str, Any],
    state: dict[str, Any],
    *,
    quest: dict[str, Any] | None = None,
) -> bool:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    if rule.get('atLocationId') is not None and not _same(rule.get('atLocationId'), scene.get('locationId')):
        return False
    if rule.get('locationId') is not None and not any(
        rule.get(key) is not None for key in ('eventType', 'event', 'changeType')
    ):
        if not _same(rule.get('locationId'), scene.get('locationId')):
            return False

    if rule.get('flagKey') is not None and not any(
        rule.get(key) is not None for key in ('eventType', 'event', 'changeType')
    ):
        flags = state.get('flags') if isinstance(state.get('flags'), dict) else {}
        flag_key = _text(rule.get('flagKey'))
        if flag_key not in flags:
            return False
        expected = rule.get('flagValue', rule.get('equals', True))
        if not _same(expected, flags.get(flag_key)):
            return False

    item_id = _text(rule.get('possessesItemId'))
    item_name = _text(rule.get('possessesItemName'))
    if item_id or item_name:
        actor_id = _text(rule.get('actorId'))
        actor = find_actor(state, actor_id) if actor_id else None
        if actor_id and not actor:
            # An authored identity is authoritative.  Falling back to every
            # player would let a stale actor reference complete the objective
            # for the wrong character.
            return False
        actors = [actor] if actor else [item for item in _list(state.get('playerCharacters')) if isinstance(item, dict)]
        quantity = max(1, int_or_default(rule.get('quantityAtLeast'), default=1))
        found = False
        for candidate in actors:
            if not isinstance(candidate, dict):
                continue
            for item in actor_items(candidate):
                identity_matches = item_id and _text(item.get('id')) == item_id
                name_matches = item_name and normalize_item_name(item.get('name')) == normalize_item_name(item_name)
                if (identity_matches or name_matches) and int_or_default(item.get('quantity'), default=1) >= quantity:
                    found = True
                    break
            if found:
                break
        if not found:
            return False

    objective_ids = [_text(value) for value in _list(rule.get('objectiveIds')) if _text(value)]
    if objective_ids:
        quests = [quest] if isinstance(quest, dict) else [
            candidate for candidate in _list(state.get('quests')) if isinstance(candidate, dict)
        ]
        completed = {
            _objective_id(objective)
            for candidate_quest in quests
            for objective in _list(candidate_quest.get('objectives'))
            if isinstance(objective, dict) and _objective_status(objective) == 'completed'
        }
        policy = _text(rule.get('objectivePolicy') or 'all').lower()
        if policy == 'any':
            if not any(objective_id in completed for objective_id in objective_ids):
                return False
        elif not all(objective_id in completed for objective_id in objective_ids):
            return False

    return bool(
        rule.get('atLocationId') is not None
        or (rule.get('locationId') is not None and not _text(rule.get('eventType') or rule.get('event') or rule.get('changeType')))
        or (rule.get('flagKey') is not None and not _text(rule.get('eventType') or rule.get('event') or rule.get('changeType')))
        or item_id
        or item_name
        or objective_ids
    )


def _has_event_predicate(rule: dict[str, Any]) -> bool:
    if any(rule.get(key) is not None for key in ('eventType', 'event', 'changeType')):
        return True
    event_only_keys = {
        'targetActorId',
        'itemId',
        'itemName',
        'npcId',
        'encounterId',
        'participantId',
        'endReason',
        'checkpointId',
        'objectiveId',
        'questId',
    }
    if any(rule.get(key) is not None for key in event_only_keys):
        return True
    return rule.get('actorId') is not None and not (
        rule.get('possessesItemId') is not None or rule.get('possessesItemName') is not None
    )


def _has_state_predicate(rule: dict[str, Any]) -> bool:
    if any(
        rule.get(key) is not None
        for key in ('atLocationId', 'possessesItemId', 'possessesItemName', 'objectiveIds')
    ):
        return True
    has_event_type = any(rule.get(key) is not None for key in ('eventType', 'event', 'changeType'))
    return not has_event_type and any(rule.get(key) is not None for key in ('locationId', 'flagKey'))


def rule_satisfied(
    rule: dict[str, Any] | None,
    state: dict[str, Any],
    change: dict[str, Any],
    *,
    quest: dict[str, Any] | None = None,
) -> bool:
    if not isinstance(rule, dict) or not rule:
        return False
    all_rules = rule.get('all') if isinstance(rule.get('all'), list) else rule.get('allOf')
    if isinstance(all_rules, list):
        return bool(all_rules) and all(
            rule_satisfied(candidate, state, change, quest=quest)
            for candidate in all_rules
            if isinstance(candidate, dict)
        )
    any_rules = rule.get('any') if isinstance(rule.get('any'), list) else rule.get('anyOf')
    if isinstance(any_rules, list):
        return any(
            rule_satisfied(candidate, state, change, quest=quest)
            for candidate in any_rules
            if isinstance(candidate, dict)
        )
    has_event = _has_event_predicate(rule)
    has_state = _has_state_predicate(rule)
    event_matches = _event_matches(rule, change) if has_event else False
    state_matches = _state_matches(rule, state, quest=quest) if has_state else False
    if has_event and has_state:
        # A compound authored rule is one predicate, not two alternative ways
        # to complete it.  The validated event and the required world state
        # must both hold.
        return event_matches and state_matches
    return event_matches if has_event else state_matches


def _required_objectives(quest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        objective
        for objective in _list(quest.get('objectives'))
        if isinstance(objective, dict)
        and objective.get('optional') is not True
        and _objective_status(objective) != 'optional'
    ]


def quest_completion_blocker(state: dict[str, Any], quest_id: Any) -> str | None:
    """Return why a mechanically-authored quest cannot complete yet."""

    quest = _quest_by_id(state, quest_id)
    if not quest or not quest_is_mechanical(quest):
        return None
    required = _required_objectives(quest)
    if not required:
        return None
    policy = _text(quest.get('completionPolicy') or 'all').lower()
    completed = [objective for objective in required if _objective_status(objective) == 'completed']
    satisfied = bool(completed) if policy == 'any' else len(completed) == len(required)
    if satisfied:
        return None
    open_labels = [
        _text(objective.get('description') or _objective_id(objective))
        for objective in required
        if _objective_status(objective) != 'completed'
    ]
    return 'Required mechanical objectives remain incomplete: ' + ', '.join(open_labels[:4])


def quest_failure_blocker(state: dict[str, Any], quest_id: Any) -> str | None:
    quest = _quest_by_id(state, quest_id)
    if not quest or not quest_is_mechanical(quest):
        return None
    if any(_objective_status(objective) == 'failed' for objective in _required_objectives(quest)):
        return None
    return 'No required mechanical objective has failed.'


def _objective_update(
    *,
    quest: dict[str, Any],
    objective: dict[str, Any],
    status: str,
    trigger: dict[str, Any],
) -> dict[str, Any]:
    quest_id = _quest_id(quest)
    objective_id = _objective_id(objective)
    turn_id = trigger.get('turnId') or trigger.get('turn_id')
    return {
        'id': stable_change_id('quest_engine', quest_id, objective_id, status),
        'turnId': turn_id,
        'type': 'quest.objective.update',
        'questId': quest_id,
        'objectiveId': objective_id,
        'objectiveStatus': status,
        'status': status,
        'source': 'quest_engine',
        'reason': f"Validated gameplay changed objective '{objective.get('description') or objective_id}' to {status}.",
        'visible': True,
    }


def _terminal_change(quest: dict[str, Any], *, completed: bool, trigger: dict[str, Any]) -> dict[str, Any]:
    quest_id = _quest_id(quest)
    change_type = 'quest.complete' if completed else 'quest.fail'
    return {
        'id': stable_change_id('quest_engine', quest_id, change_type),
        'turnId': trigger.get('turnId') or trigger.get('turn_id'),
        'type': change_type,
        'questId': quest_id,
        'source': 'quest_engine',
        'reason': (
            'All required validated objectives are complete.'
            if completed
            else 'A required validated objective failed.'
        ),
        'visible': True,
    }


def _reward_actor_id(state: dict[str, Any], quest: dict[str, Any], trigger: dict[str, Any]) -> str | None:
    requested = _text(quest.get('rewardActorId') or trigger.get('actorId') or trigger.get('actor_id'))
    if requested and find_actor(state, requested):
        return requested
    active_ids = {
        int_or_default(value, default=0)
        for value in _list(state.get('activePlayerIds'))
        if int_or_default(value, default=0) > 0
    }
    for actor in _list(state.get('playerCharacters')):
        if not isinstance(actor, dict):
            continue
        player_id = int_or_default(actor.get('playerId'), default=0)
        if not active_ids or player_id in active_ids:
            return _text(actor.get('id')) or None
    return None


def _reward_records(quest: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    raw = quest.get('rewards', quest.get('reward'))
    if isinstance(raw, list):
        records.extend(deepcopy(item) for item in raw if isinstance(item, dict))
    elif isinstance(raw, dict):
        xp = raw.get('xp', raw.get('experience'))
        if int_or_default(xp, default=0) > 0:
            records.append({'type': 'xp', 'amount': int_or_default(xp, default=0)})
        for currency in ('pp', 'gp', 'ep', 'sp', 'cp'):
            amount = int_or_default(raw.get(currency), default=0)
            if amount > 0:
                records.append({'type': 'currency', 'currency': currency, 'amount': amount})
        for item in _list(raw.get('items')):
            if isinstance(item, dict):
                records.append({'type': 'item', 'item': deepcopy(item)})
        for flag in _list(raw.get('flags')):
            if isinstance(flag, dict):
                records.append({'type': 'flag', **deepcopy(flag)})
    explicit_xp = int_or_default(
        quest.get('xpReward', quest.get('rewardXp', quest.get('experienceReward'))),
        default=0,
    )
    if explicit_xp > 0 and not any(_text(record.get('type')).lower() == 'xp' for record in records):
        records.append({'type': 'xp', 'amount': explicit_xp})
    return records


def _has_actor_rewards(quest: dict[str, Any]) -> bool:
    return any(
        _text(reward.get('type')).lower() in {'xp', 'currency', 'item'}
        for reward in _reward_records(quest)
    )


def _terminal_effect_changes(
    state: dict[str, Any],
    quest: dict[str, Any],
    trigger: dict[str, Any],
    *,
    completed: bool,
) -> list[dict[str, Any]]:
    quest_id = _quest_id(quest)
    actor_id = _reward_actor_id(state, quest, trigger)
    changes: list[dict[str, Any]] = []
    if completed and actor_id:
        for index, reward in enumerate(_reward_records(quest)):
            reward_type = _text(reward.get('type')).lower()
            requested_reward_actor_id = _text(reward.get('actorId'))
            reward_actor_id = (
                requested_reward_actor_id
                if requested_reward_actor_id and find_actor(state, requested_reward_actor_id)
                else actor_id
            )
            base = {
                'id': stable_change_id('quest_engine', quest_id, 'reward', index, reward_type),
                'turnId': trigger.get('turnId') or trigger.get('turn_id'),
                'actorId': reward_actor_id,
                'source': 'quest_engine',
                'reason': f"One-time validated reward for quest '{quest.get('title') or quest_id}'.",
                'visible': True,
            }
            if reward_type == 'xp' and int_or_default(reward.get('amount'), default=0) > 0:
                changes.append({**base, 'type': 'xp.add', 'amount': int_or_default(reward.get('amount'), default=0)})
            elif reward_type == 'currency' and int_or_default(reward.get('amount'), default=0) > 0:
                changes.append(
                    {
                        **base,
                        'type': 'currency.add',
                        'currency': _text(reward.get('currency')).lower() or 'gp',
                        'amount': int_or_default(reward.get('amount'), default=0),
                    }
                )
            elif reward_type == 'item' and isinstance(reward.get('item'), dict):
                item = deepcopy(reward['item'])
                changes.append(
                    {
                        **base,
                        'type': 'inventory.add',
                        'item': item,
                        'itemId': item.get('id'),
                        'itemName': item.get('name'),
                        'quantity': max(1, int_or_default(item.get('quantity'), default=1)),
                    }
                )
            elif reward_type == 'flag' and _text(reward.get('flagKey') or reward.get('key')):
                changes.append(
                    {
                        **base,
                        'type': 'flag.set',
                        'flagKey': _text(reward.get('flagKey') or reward.get('key')),
                        'flagValue': reward.get('flagValue', reward.get('value', True)),
                    }
                )

    consequence_key = 'onComplete' if completed else 'onFail'
    consequences = quest.get(consequence_key)
    if not isinstance(consequences, list):
        consequences = quest.get('completionConsequences' if completed else 'failureConsequences')
    for index, consequence in enumerate(_list(consequences)):
        if not isinstance(consequence, dict) or _text(consequence.get('type')) not in CONSEQUENCE_CHANGE_TYPES:
            continue
        changes.append(
            {
                **deepcopy(consequence),
                'id': stable_change_id('quest_engine', quest_id, consequence_key, index),
                'turnId': trigger.get('turnId') or trigger.get('turn_id'),
                'source': 'quest_engine',
                'reason': consequence.get('reason') or f"Validated consequence of quest '{quest.get('title') or quest_id}'.",
            }
        )
    return changes


def derive_quest_changes(state: dict[str, Any], applied_change: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive trusted, ledger-stable quest changes after one applied change."""

    if not isinstance(applied_change, dict):
        return []
    change_type = _text(applied_change.get('type'))
    quest_id = _text(applied_change.get('questId') or applied_change.get('quest_id'))
    if change_type in {'quest.complete', 'quest.fail'} and applied_change.get('source') == 'quest_engine':
        quest = _quest_by_id(state, quest_id)
        if not quest:
            return []
        return _terminal_effect_changes(
            state,
            quest,
            applied_change,
            completed=change_type == 'quest.complete',
        )

    derived: list[dict[str, Any]] = []
    for quest in _list(state.get('quests')):
        if not isinstance(quest, dict) or _text(quest.get('status')).lower() in TERMINAL_QUEST_STATUSES:
            continue
        mechanical_objectives = [
            objective
            for objective in _list(quest.get('objectives'))
            if isinstance(objective, dict) and objective_is_mechanical(objective)
        ]
        if not mechanical_objectives:
            continue
        for objective in mechanical_objectives:
            status = _objective_status(objective)
            if status in TERMINAL_OBJECTIVE_STATUSES:
                continue
            prerequisites_met = prerequisites_satisfied(quest, objective)
            desired_status = 'open' if prerequisites_met else 'blocked'
            if not prerequisites_met:
                if status != 'blocked':
                    derived.append(
                        _objective_update(
                            quest=quest,
                            objective=objective,
                            status='blocked',
                            trigger=applied_change,
                        )
                    )
                continue
            if rule_satisfied(_rule(objective, 'failWhen'), state, applied_change, quest=quest):
                desired_status = 'failed'
            elif rule_satisfied(_rule(objective, 'completeWhen'), state, applied_change, quest=quest):
                desired_status = 'completed'
            if desired_status != status:
                derived.append(
                    _objective_update(
                        quest=quest,
                        objective=objective,
                        status=desired_status,
                        trigger=applied_change,
                    )
                )

        # Terminal transitions are evaluated after objective updates apply.  A
        # quest.objective.update derivation will re-enter this function with the
        # authoritative new status.
        required = _required_objectives(quest)
        if not required:
            continue
        policy = _text(quest.get('completionPolicy') or 'all').lower()
        completed_count = sum(_objective_status(objective) == 'completed' for objective in required)
        completion_met = completed_count > 0 if policy == 'any' else completed_count == len(required)
        if completion_met:
            # Do not write the terminal ledger entry until actor-bound rewards
            # have a valid recipient.  A later validated change can retry this
            # transition once a player actor is present.
            if _has_actor_rewards(quest) and not _reward_actor_id(state, quest, applied_change):
                continue
            derived.append(_terminal_change(quest, completed=True, trigger=applied_change))
        elif quest.get('failOnObjectiveFailure') is True and any(
            _objective_status(objective) == 'failed' for objective in required
        ):
            derived.append(_terminal_change(quest, completed=False, trigger=applied_change))
    return derived
