"""Engine resolution of the non-attack combat actions a player submits.

The HUD action was legal when the server issued it; :func:`resolve_combat_action`
re-resolves it against the current combat under the turn lock and returns the
trusted state changes it makes plus a ``playerResolvedCombatAction`` block the
DM must narrate as decided.  Dispatch is by action type, so class features that
grant Dash, Disengage, Dodge or Hide as another action type cost reuse it.
Attacks and end turn are resolved elsewhere and return ``None`` here.

The maneuvers (grapple, shove down, bind, demand surrender) and the improvised
action roll the hero's check as hide does (rules in
:mod:`aidm_server.combat.maneuvers`); their block carries ``success``, and a
maneuver that closes in first carries the advance's ``movement`` as well.
Falling back out of melee lets go of every enemy the hero held.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from aidm_server.canon_text import int_or_default
from aidm_server.combat.attacks import ability_modifier, d20_with_mode, roll_die, rolled_bonus_dice
from aidm_server.combat.maneuvers import (
    BIND_DC,
    DEMAND_DC,
    DEMAND_DC_BROKEN,
    IMPROVISE_DC,
    MANEUVER_TYPES,
    YIELD_CONDITIONS,
    at_most_quarter,
    contest_dc,
    grapple_effect,
    grapple_effect_payload,
    grapple_holds,
    grappler_id,
    off_balance_effect_payload,
    shove_effect_payload,
    weapon_noun,
)
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
    BAND_PHRASES,
    CLOSABLE_BANDS,
    RANGE_BANDS,
    adjacent_or_same_band,
    advances_toward,
    band_inward,
    band_outward,
    close_block_reason,
    has_cover,
    in_melee_reach,
    participant_by_id,
)
from aidm_server.combat.reactions import collect_reactions, reaction_changes
from aidm_server.combat.state import combat_turn_economy, participant_is_targetable
from aidm_server.combat.vitality import is_dying
from aidm_server.combat.working_state import apply_working_changes
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
    target = participant_by_id(combat, context.get('targetId'))
    # Off the press, a move goes one band inward, unless it closes on an enemy standing off there too.
    if band not in CLOSABLE_BANDS and (target is None or close_block_reason(actor, target, combat)):
        to_band = band_inward(band)
        return {
            'changes': [_move_change(context, band, to_band, [])],
            'outcome': f"{context['actorName']} closes to {BAND_PHRASES[to_band]}.",
            'movement': {'fromRangeBand': band, 'toRangeBand': to_band, 'engagedWith': []},
        }
    if target is None:
        return _blocked('Choose an enemy to close on.')
    if advances_toward(actor, target, combat):
        # An enemy standing off from the press: the hero goes out to it, a move short of reaching it.
        return {
            'changes': [_move_change(context, band, range_band(target), [])],
            'outcome': f"{context['actorName']} moves out toward {_name(target)}.",
            'movement': {'fromRangeBand': band, 'toRangeBand': range_band(target), 'engagedWith': [], 'towardId': context['targetId']},
        }
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
        if band == 'melee':
            changes.extend(_release_changes(context, combat))
        outcome = f"{context['actorName']} falls back to {BAND_PHRASES[to_band]}."
    else:
        outcome = f"{context['actorName']} is struck down while trying to fall back."
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


def _release_changes(context: Mapping[str, Any], combat: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The hero leaves melee: every enemy they held is let go."""

    return [
        _grapple_end_change(context, enemy)
        for enemy in combat.get('participants') or []
        if isinstance(enemy, dict) and grappler_id(enemy) == context['actorId']
    ]


def _grapple_end_change(context: Mapping[str, Any], enemy: Mapping[str, Any]) -> dict[str, Any]:
    effect = grapple_effect(enemy) or {}
    change = _change(
        context,
        f"grapple_end:{_text(enemy.get('id'))}",
        'combat.effect.consume',
        consume=[{'participantId': _text(enemy.get('id')), 'effectId': effect.get('id')}],
        reason=f"{_name(enemy)} is no longer held.",
    )
    change['source'] = MODIFIER_ENGINE_SOURCE
    return change


def _skill_name(check: Mapping[str, Any]) -> str:
    return ' '.join(word if word == 'of' else word.capitalize() for word in _text(check.get('skill')).split('_')) or 'Check'


def _check_text(check: Mapping[str, Any]) -> str:
    """``Athletics 17 against DC 12, a success``."""

    return f"{_skill_name(check)} {check['total']} against DC {check['dc']}, {'a success' if check['success'] else 'a failure'}"


def _maneuver_target(context: dict[str, Any], combat: dict[str, Any]) -> dict[str, Any] | None:
    target = participant_by_id(combat, context.get('targetId'))
    if target is None or _text(target.get('team')).lower() != 'enemy' or target.get('isAlive') is False:
        return None
    return target


def _rolled(context: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any], *, dc: int) -> dict[str, Any]:
    check = context['check']
    return _roll_check(
        actor,
        combat=combat,
        check=check,
        ability=_text(check.get('ability')) or 'strength',
        dc=dc,
        seed=f"{context['actionType']}:{context['turnId']}:{context['actorId']}:{context.get('targetId')}",
    )


def _resolve_contest(context: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    """Grapple or shove down: Athletics against 10 + the enemy's better Strength or Dexterity modifier."""

    target = _maneuver_target(context, combat)
    if target is None or not participant_is_targetable(target):
        return _blocked('Choose an enemy who is still fighting.')
    if not in_melee_reach(actor, target):
        return _blocked('Advance into melee first.')
    grapple = context['actionType'] == 'grapple'
    if grapple and grapple_holds(target, combat):
        return _blocked('Already held.')
    if not grapple and 'prone' in condition_keys(target):
        return _blocked('Already down.')
    check = _rolled(context, actor, combat, dc=contest_dc(target))
    changes = _consume_change(context, check.pop('consume'))
    name, verb = _name(target), 'grapples' if grapple else 'shoves'
    if check['success'] and grapple:
        effect = grapple_effect_payload(hero_id=context['actorId'], hero_name=context['actorName'], escape=int(context.get('escapeDc') or 10))
        changes.append(_change(context, 'grappled', 'combat.effect.add', participantId=context['targetId'], effect=effect, reason=f"{context['actorName']} grapples {name}."))
        result = f'{name} is held'
    elif check['success']:
        effect = shove_effect_payload(hero_id=context['actorId'], turn_id=context['turnId'])
        changes.append(_change(context, 'prone', 'combat.effect.add', participantId=context['targetId'], effect=effect, reason=f"{context['actorName']} shoves {name} down."))
        result = f'{name} is prone until the end of its next turn'
    else:
        result = 'nothing changes'
    attempt = verb if check['success'] else f"tries to {'grapple' if grapple else 'shove'}"
    down = '' if grapple or not check['success'] else ' down'
    outcome = f"{context['actorName']} {attempt} {name}{down}: {_check_text(check)} — {result}."
    return {'changes': changes, 'outcome': outcome, 'check': check, 'success': check['success']}


def _resolve_subdue(context: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    """Bind: no roll against a yielded enemy, else the better of Athletics or Sleight of Hand against DC 10."""

    target = _maneuver_target(context, combat)
    if target is None or 'captured' in condition_keys(target):
        return _blocked('Choose an enemy to bind.')
    if not in_melee_reach(actor, target):
        return _blocked('Advance into melee first.')
    keys = condition_keys(target)
    name = _name(target)
    if keys & YIELD_CONDITIONS:
        check = None
        changes: list[dict[str, Any]] = []
        success = True
        how = f'{name}, who has yielded: no roll'
    else:
        if not (grapple_holds(target, combat) or 'prone' in keys or at_most_quarter(target)):
            return _blocked('It is still on its feet and fighting.')
        check = _rolled(context, actor, combat, dc=BIND_DC)
        changes = _consume_change(context, check.pop('consume'))
        success = check['success']
        how = f'{name}: {_check_text(check)}'
    if success:
        changes.append(_change(context, 'captured', 'combat.condition.add', participantId=context['targetId'], condition='captured', reason=f"{context['actorName']} binds {name}."))
        if grapple_effect(target):
            changes.append(_grapple_end_change(context, target))
        outcome = f"{context['actorName']} binds {how} — {name} is captured, out of the fight and alive."
    else:
        outcome = f"{context['actorName']} tries to bind {how} — {name} fights free of it."
    resolved: dict[str, Any] = {'changes': changes, 'outcome': outcome, 'success': success}
    if check is not None:
        resolved['check'] = check
    return resolved


def _resolve_demand_surrender(context: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    """Intimidation against DC 12, DC 8 against an enemy at a quarter of its hit points or less or held."""

    target = _maneuver_target(context, combat)
    if target is None or not participant_is_targetable(target):
        return _blocked('Choose an enemy who is still fighting.')
    if not in_melee_reach(actor, target):
        return _blocked('Advance into melee first.')
    name = _name(target)
    broken = at_most_quarter(target) or grapple_holds(target, combat)
    check = _rolled(context, actor, combat, dc=DEMAND_DC_BROKEN if broken else DEMAND_DC)
    changes = _consume_change(context, check.pop('consume'))
    if check['success']:
        changes.append(_change(context, 'surrendered', 'combat.condition.add', participantId=context['targetId'], condition='surrendered', reason=f'{name} yields to {context["actorName"]}.'))
    result = f'{name} yields' if check['success'] else f'{name} fights on'
    outcome = f"{context['actorName']} demands {name}'s surrender: {_check_text(check)} — {result}."
    return {'changes': changes, 'outcome': outcome, 'check': check, 'success': check['success'], 'targetWeapon': weapon_noun(target)}


def _resolve_improvise(context: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    """Anything typed that is not a listed action: the better of Athletics or Acrobatics against DC 12;
    on a success a named enemy is off balance until the end of its next turn."""

    target = _maneuver_target(context, combat) if context.get('targetId') else None
    check = _rolled(context, actor, combat, dc=IMPROVISE_DC)
    changes = _consume_change(context, check.pop('consume'))
    if check['success'] and target is not None and participant_is_targetable(target):
        effect = off_balance_effect_payload(hero_id=context['actorId'], turn_id=context['turnId'])
        changes.append(_change(context, 'off_balance', 'combat.effect.add', participantId=context['targetId'], effect=effect, reason=f'{_name(target)} is off balance.'))
        result = f'{_name(target)} is off balance until the end of its next turn'
    else:
        result = 'it works' if check['success'] else 'it does not come off'
    attempt = _text(context.get('attempt'))
    tried = f' ("{attempt}")' if attempt else ''
    outcome = f"{context['actorName']} tries something of their own{tried}: {_check_text(check)} — {result}."
    return {'changes': changes, 'outcome': outcome, 'check': check, 'success': check['success'], 'attempt': attempt}


ACTION_RESOLVERS: dict[str, Callable[..., dict[str, Any]]] = {
    'dodge': _resolve_dodge,
    'dash': _resolve_economy_only('{name} dashes, gaining another movement this turn.'),
    'disengage': _resolve_economy_only('{name} disengages: moving away provokes no opportunity attacks this turn.'),
    'advance': _resolve_advance,
    'reposition': _resolve_fall_back,
    'help': _resolve_help,
    'hide': _resolve_hide,
    'stabilize': _resolve_stabilize,
    'grapple': _resolve_contest,
    'shove': _resolve_contest,
    'subdue': _resolve_subdue,
    'demand_surrender': _resolve_demand_surrender,
    'improvise': _resolve_improvise,
}


def resolve_combat_action(
    *,
    state: Mapping[str, Any],
    combat_action: Mapping[str, Any],
    actor_id: str,
    turn_id: Any,
) -> dict[str, Any] | None:
    """Re-resolve one server-issued non-attack action (or the advance of an attack that moves first);
    ``None`` for other attacks and end turn.

    Returns ``{"ok", "reason", "changes", "result"}`` where ``result`` is the
    narration block.  Nothing in ``state`` is mutated.
    """

    action_type = _text(combat_action.get('action_type'))
    moves_first = combat_action.get('move_first') is True
    if action_type == 'attack' and moves_first:
        # An attack that needs the move first advances on its target here; the attack itself is resolved elsewhere.
        action_type = 'advance'
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
        'escapeDc': combat_action.get('escape_dc'),
        'attempt': _text(combat_action.get('attempt')),
    }
    advanced: dict[str, Any] = {}
    if moves_first and action_type in MANEUVER_TYPES:
        # A maneuver that needs the move first closes on its target, then is rolled from there.
        advanced = _resolve_advance({**context, 'actionType': 'advance'}, actor, combat, state)
        if advanced.get('ok') is False:
            return advanced
        apply_working_changes(combat, advanced['changes'])
        actor = participant_by_id(combat, actor_id) or actor
    resolved = resolver(context, actor, combat, state)
    if resolved.get('ok') is False:
        return resolved
    if advanced:
        resolved = {
            **resolved,
            'changes': [*advanced['changes'], *resolved['changes']],
            'outcome': f"{advanced['outcome']} {resolved['outcome']}",
            'movement': advanced['movement'],
        }
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
    for key in ('movement', 'check', 'reactions', 'attempt', 'targetWeapon'):
        if resolved.get(key):
            result[key] = resolved[key]
    if 'success' in resolved:
        result['success'] = resolved['success']
    return {'ok': True, 'reason': '', 'changes': resolved['changes'], 'result': result}
