"""Engine resolution of the non-attack combat actions a player submits.

The HUD action was legal when the server issued it; :func:`resolve_combat_action`
re-resolves it against the current combat under the turn lock and returns the
trusted state changes it makes plus a ``playerResolvedCombatAction`` block the
DM must narrate as decided.  Dispatch is by action type, so class features that
grant Dash, Disengage, Dodge or Hide as another action type cost reuse it.
Attacks and end turn are resolved elsewhere and return ``None`` here.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from aidm_server.canon_text import int_or_default
from aidm_server.combat.attacks import ability_modifier, d20_with_mode, roll_die, rolled_bonus_dice
from aidm_server.combat.modifiers import (
    MODIFIER_ENGINE_SOURCE,
    can_act,
    check_context,
    condition_keys,
    engaged_with,
    is_hidden,
    range_band,
    speed_zero_reasons,
)
from aidm_server.combat.positioning import (
    CLOSABLE_BANDS,
    RANGE_BANDS,
    adjacent_or_same_band,
    band_inward,
    band_outward,
    close_block_reason,
    has_cover,
    participant_by_id,
)
from aidm_server.combat.reactions import collect_reactions, reaction_changes
from aidm_server.combat.state import combat_turn_economy, participant_is_targetable
from aidm_server.combat.vitality import is_dying
from aidm_server.damage_dice import deterministic_roller
from aidm_server.game_state.models import stable_change_id


COMBAT_ACTION_SOURCE = 'combat_action_engine'
STABILIZE_DC = 10
NARRATION_INSTRUCTION = 'Narrate this combat action exactly as the engine resolved it; do not change its outcome, rolls or movement.'


def _text(value: Any) -> str:
    return str(value or '').strip()


def _name(participant: Mapping[str, Any] | None, fallback: str = 'someone') -> str:
    return _text((participant or {}).get('name')) or _text((participant or {}).get('id')) or fallback


def _blocked(reason: str) -> dict[str, Any]:
    return {'ok': False, 'reason': reason, 'changes': [], 'result': None}


def _change(context: Mapping[str, Any], suffix: str, change_type: str, **fields: Any) -> dict[str, Any]:
    return {
        'id': stable_change_id(context['turnId'], 'combat.action', context['actionType'], context['actorId'], context.get('targetId'), suffix),
        'turnId': context['turnId'],
        'type': change_type,
        'source': COMBAT_ACTION_SOURCE,
        'visible': True,
        **fields,
    }


def _move_change(context: Mapping[str, Any], from_band: str, to_band: str, engage_with: list[str]) -> dict[str, Any]:
    change = _change(
        context,
        f'move:{from_band}:{to_band}',
        'combat.move',
        participantId=context['actorId'],
        fromRangeBand=from_band,
        toRangeBand=to_band,
        reason=f"{context['actorName']} moved from {from_band} to {to_band} range.",
    )
    if engage_with:
        change['engageWith'] = engage_with
    return change


def _reaction_summaries(reactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for reaction in reactions:
        attack = reaction.get('attack') if isinstance(reaction.get('attack'), Mapping) else {}
        summary = {
            'reactorId': reaction['reactorId'],
            'reactorName': reaction.get('reactorName'),
            'kind': reaction['kind'],
            'label': reaction['label'],
        }
        for key in ('hit', 'critical', 'attackTotal', 'targetArmorClass', 'damageTotal', 'damageType', 'abilityName', 'weaponName', 'conditionsApplied'):
            if key in attack:
                summary[key] = deepcopy(attack[key])
        summaries.append(summary)
    return summaries


def _roll_check(
    actor: Mapping[str, Any],
    *,
    combat: Mapping[str, Any],
    check: Mapping[str, Any],
    ability: str,
    dc: int,
    seed: str,
) -> dict[str, Any]:
    context = check_context(actor, ability=ability, skill=check.get('skill'), combat=combat)
    roller = deterministic_roller(seed)
    natural, rolls = d20_with_mode(roll_die(20, roller), context['mode'], roller)
    bonus_dice = rolled_bonus_dice(context['bonusDice'], roller)
    modifier = int_or_default(check.get('modifier'), default=0)
    total = natural + modifier + sum(int(item['total']) for item in bonus_dice)
    return {
        'skill': _text(check.get('skill')),
        'ability': ability,
        'roll': natural,
        'rolls': rolls,
        'mode': context['mode'],
        'modeReasons': context['reasons'],
        'modifier': modifier,
        'bonusDice': bonus_dice,
        'total': total,
        'dc': dc,
        'success': total >= dc,
        'consume': context['consume'],
    }


def _consume_change(context: Mapping[str, Any], consume: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not consume:
        return []
    change = _change(context, 'consume', 'combat.effect.consume', consume=deepcopy(consume), reason='Effects spent by an engine-resolved check.')
    change.update({'source': MODIFIER_ENGINE_SOURCE, 'visible': False})
    return [change]


def _passive_perception(participant: Mapping[str, Any]) -> int:
    stats = participant.get('stats') if isinstance(participant.get('stats'), Mapping) else {}
    return 10 + ability_modifier(stats.get('wisdom', participant.get('wisdom')))


def _resolve_dodge(context: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    if 'dodging' in condition_keys(actor):
        return _blocked('Already dodging.')
    change = _change(context, 'dodging', 'combat.condition.add', participantId=context['actorId'], condition='dodging', reason=f"{context['actorName']} takes the Dodge action.")
    return {'changes': [change], 'outcome': f"{context['actorName']} is dodging until the start of their next turn."}


def _resolve_economy_only(outcome: str) -> Callable[..., dict[str, Any]]:
    def resolve(context: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
        return {'changes': [], 'outcome': outcome.format(name=context['actorName'])}

    return resolve


def _resolve_advance(context: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    blocked = ' '.join(speed_zero_reasons(actor))
    if blocked:
        return _blocked(blocked)
    band = range_band(actor)
    if band not in CLOSABLE_BANDS:
        to_band = band_inward(band)
        return {
            'changes': [_move_change(context, band, to_band, [])],
            'outcome': f"{context['actorName']} advances from {band} to {to_band} range.",
            'movement': {'fromRangeBand': band, 'toRangeBand': to_band, 'engagedWith': []},
        }
    target = participant_by_id(combat, context.get('targetId'))
    if target is None:
        return _blocked('Choose an enemy to close on.')
    reason = close_block_reason(actor, target, combat)
    if reason:
        return _blocked(reason)
    return {
        'changes': [_move_change(context, band, 'melee', [context['targetId']])],
        'outcome': f"{context['actorName']} closes into melee and engages {_name(target)}.",
        'movement': {'fromRangeBand': band, 'toRangeBand': 'melee', 'engagedWith': [context['targetId']]},
    }


def _resolve_fall_back(context: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    blocked = ' '.join(speed_zero_reasons(actor))
    if blocked:
        return _blocked(blocked)
    band = range_band(actor)
    if band == RANGE_BANDS[-1]:
        return _blocked('Already as far back as you can go.')
    to_band = band_outward(band)
    economy = combat_turn_economy(combat, actor_id=context['actorId']) or {}
    reactions: list[dict[str, Any]] = []
    engaged = engaged_with(actor)
    if band == 'melee' and engaged and not economy.get('disengaged'):
        reactions = collect_reactions(
            {
                'type': 'leaves_melee',
                'moverId': context['actorId'],
                'fromEngagedWith': engaged,
                'eventId': f"turn:{context['turnId']}:{context['actorId']}:leaves_melee",
                'turnId': context['turnId'],
            },
            combat,
            {**state, 'combat': combat},
        )
    changes = reaction_changes(reactions)
    still_standing = can_act(participant_by_id(combat, context['actorId']))
    if still_standing:
        changes.append(_move_change(context, band, to_band, []))
        outcome = f"{context['actorName']} falls back from {band} to {to_band} range."
    else:
        outcome = f"{context['actorName']} is struck down while trying to fall back and stays at {band} range."
    return {
        'changes': changes,
        'outcome': outcome,
        'movement': {'fromRangeBand': band, 'toRangeBand': to_band if still_standing else band, 'leftEngagementWith': engaged if band == 'melee' else []},
        'reactions': _reaction_summaries(reactions),
    }


def _resolve_help(context: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    target = participant_by_id(combat, context.get('targetId'))
    if (
        target is None
        or target is actor
        or _text(target.get('team')).lower() not in {'player', 'ally'}
        or not participant_is_targetable(target)
    ):
        return _blocked('Choose an ally who can still fight.')
    if not adjacent_or_same_band(actor, target):
        return _blocked('Too far away to help.')
    effect = {
        'id': f"help_{context['turnId']}_{context['actorId']}_{context['targetId']}",
        'sourceId': 'combat_help',
        'sourceName': f"Help from {context['actorName']}",
        'casterId': context['actorId'],
        'modifiers': {'attackMode': 'advantage'},
        'duration': {'rounds': 1, 'tickOn': 'caster_turn_start'},
        'concentration': False,
        'consumeOn': 'attack',
    }
    change = _change(context, 'help', 'combat.effect.add', participantId=context['targetId'], effect=effect, reason=f"{context['actorName']} helps {_name(target)}.")
    return {'changes': [change], 'outcome': f"{_name(target)} has advantage on their next attack before {context['actorName']}'s next turn."}


def _resolve_hide(context: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    if is_hidden(actor):
        return _blocked('Already hidden.')
    if range_band(actor) == 'melee' and not has_cover(combat, actor):
        return _blocked('You cannot hide in melee without cover.')
    watchers = [
        participant
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and _text(participant.get('team')).lower() == 'enemy' and participant_is_targetable(participant)
    ]
    dc = max((_passive_perception(watcher) for watcher in watchers), default=10)
    check = _roll_check(actor, combat=combat, check=context['check'], ability='dexterity', dc=dc, seed=f"hide:{context['turnId']}:{context['actorId']}")
    changes = _consume_change(context, check.pop('consume'))
    if check['success']:
        changes.append(_change(context, 'hidden', 'combat.condition.add', participantId=context['actorId'], condition='hidden', reason=f"{context['actorName']} is hidden."))
        outcome = f"{context['actorName']} slips out of sight (Stealth {check['total']} against passive Perception {dc})."
    else:
        outcome = f"{context['actorName']} fails to hide (Stealth {check['total']} against passive Perception {dc})."
    return {'changes': changes, 'outcome': outcome, 'check': check}


def _resolve_stabilize(context: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    target = participant_by_id(combat, context.get('targetId'))
    if target is None or not is_dying(target) or _text(target.get('team')).lower() not in {'player', 'ally'}:
        return _blocked('That ally is not dying.')
    if not adjacent_or_same_band(actor, target):
        return _blocked('Too far away to reach.')
    check = _roll_check(
        actor,
        combat=combat,
        check=context['check'],
        ability='wisdom',
        dc=STABILIZE_DC,
        seed=f"stabilize:{context['turnId']}:{context['actorId']}:{context['targetId']}",
    )
    changes = _consume_change(context, check.pop('consume'))
    if check['success']:
        changes.append(_change(context, 'stable', 'combat.stabilize', participantId=context['targetId'], reason=f"{context['actorName']} stabilized {_name(target)}."))
        outcome = f"{context['actorName']} stabilizes {_name(target)} (Medicine {check['total']} against DC {STABILIZE_DC})."
    else:
        outcome = f"{context['actorName']} cannot stop {_name(target)}'s bleeding (Medicine {check['total']} against DC {STABILIZE_DC})."
    return {'changes': changes, 'outcome': outcome, 'check': check}


ACTION_RESOLVERS: dict[str, Callable[..., dict[str, Any]]] = {
    'dodge': _resolve_dodge,
    'dash': _resolve_economy_only('{name} dashes, gaining another movement this turn.'),
    'disengage': _resolve_economy_only('{name} disengages: moving away provokes no opportunity attacks this turn.'),
    'advance': _resolve_advance,
    'reposition': _resolve_fall_back,
    'help': _resolve_help,
    'hide': _resolve_hide,
    'stabilize': _resolve_stabilize,
}


def resolve_combat_action(
    *,
    state: Mapping[str, Any],
    combat_action: Mapping[str, Any],
    actor_id: str,
    turn_id: Any,
) -> dict[str, Any] | None:
    """Re-resolve one server-issued non-attack action; ``None`` for attacks and end turn.

    Returns ``{"ok", "reason", "changes", "result"}`` where ``result`` is the
    narration block.  Nothing in ``state`` is mutated.
    """

    action_type = _text(combat_action.get('action_type'))
    resolver = ACTION_RESOLVERS.get(action_type)
    if resolver is None:
        return None
    combat = deepcopy(state.get('combat')) if isinstance(state.get('combat'), dict) else {}
    actor = participant_by_id(combat, actor_id)
    if actor is None or not can_act(actor):
        return _blocked('This character cannot act.')
    target = participant_by_id(combat, combat_action.get('target_id'))
    context = {
        'turnId': turn_id,
        'actionType': action_type,
        'actorId': _text(actor_id),
        'actorName': _name(actor),
        'targetId': _text(combat_action.get('target_id')) or None,
        'check': combat_action.get('check') if isinstance(combat_action.get('check'), Mapping) else {},
    }
    resolved = resolver(context, actor, combat, state)
    if resolved.get('ok') is False:
        return resolved
    result = {
        'actionId': _text(combat_action.get('action_id')),
        'actionType': action_type,
        'actorId': context['actorId'],
        'actorName': context['actorName'],
        'outcome': resolved['outcome'],
        'authoritative': True,
        'instruction': NARRATION_INSTRUCTION,
    }
    if target is not None:
        result.update({'targetId': context['targetId'], 'targetName': _name(target)})
    for key in ('movement', 'check', 'reactions'):
        if resolved.get(key):
            result[key] = resolved[key]
    return {'ok': True, 'reason': '', 'changes': resolved['changes'], 'result': result}
