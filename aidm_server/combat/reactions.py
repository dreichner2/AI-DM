"""Reactions: who may react to a combat event, the provider registry, and opportunity attacks.

A participant has one reaction per round: ``reactionRemaining`` (default 1)
resets to 1 at the start of its own turn and every reaction spends it.  A
participant that cannot act or carries the ``cannotReact`` modifier takes no
reactions.

Providers registered with :func:`register_reaction_provider` are called as
``fn(event, combat, state)`` and return reactions.  Events are::

    {"type": "attack_hit", "attackerId", "targetId", "attackTotal", "targetAc", "damage", "critical"}
    {"type": "leaves_melee", "moverId", "fromEngagedWith": [...]}
    {"type": "damage_taken", "targetId", "sourceId", "amount", "damageType"}

and also carry ``eventId`` (stable for the turn, for deriving change ids) and
``turnId``.  Each reaction is ``{"reactorId", "kind", "label", "cancelsHit",
"damageMultiplier", "changes", "consumes"}``.  Providers receive a copy of the
combat; :func:`collect_reactions` keeps at most one reaction per reactor per
event (the first provider wins), spends the reaction and applies the kept
changes to the caller's working combat.  This module imports nothing heavy so
any module may register a provider at import time.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from aidm_server.canon_text import int_or_default
from aidm_server.combat.modifiers import can_act, cannot_react, condition_key
from aidm_server.combat.working_state import apply_working_changes
from aidm_server.game_state.models import stable_change_id


ReactionProvider = Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], list[dict[str, Any]] | None]

REACTION_SOURCE = 'combat_reaction_engine'
REACTION_EVENT_TYPES = frozenset({'attack_hit', 'leaves_melee', 'damage_taken'})
_REACTION_PROVIDERS: list[ReactionProvider] = []


def _text(value: Any) -> str:
    return str(value or '').strip()


def register_reaction_provider(fn: ReactionProvider) -> ReactionProvider:
    if fn not in _REACTION_PROVIDERS:
        _REACTION_PROVIDERS.append(fn)
    return fn


def unregister_reaction_provider(fn: ReactionProvider) -> None:
    if fn in _REACTION_PROVIDERS:
        _REACTION_PROVIDERS.remove(fn)


def reaction_remaining(participant: Mapping[str, Any] | None) -> int:
    if not isinstance(participant, Mapping):
        return 0
    return max(0, min(1, int_or_default(participant.get('reactionRemaining'), default=1)))


def can_react(participant: Mapping[str, Any] | None) -> bool:
    return (
        isinstance(participant, Mapping)
        and can_act(participant)
        and not cannot_react(participant)
        and reaction_remaining(participant) > 0
    )


def auto_reactions_enabled(participant: Mapping[str, Any] | None, state: Mapping[str, Any] | None) -> bool:
    """A player character's automatic reactions run unless its sheet turned them off."""

    participant_id = _text((participant or {}).get('id'))
    for actor in (state or {}).get('playerCharacters') or []:
        if isinstance(actor, Mapping) and _text(actor.get('id')) == participant_id and 'autoReactions' in actor:
            return actor.get('autoReactions') is not False
    return (participant or {}).get('autoReactions') is not False


def _participant(combat: Mapping[str, Any], participant_id: Any) -> dict[str, Any] | None:
    wanted = _text(participant_id)
    for participant in combat.get('participants') or []:
        if isinstance(participant, dict) and _text(participant.get('id')) == wanted:
            return participant
    return None


def reaction_use_change(*, event: Mapping[str, Any], reactor_id: str, kind: str, label: str) -> dict[str, Any]:
    return {
        'id': stable_change_id('combat_reaction', event.get('turnId'), event.get('eventId'), reactor_id, kind),
        'turnId': event.get('turnId'),
        'type': 'combat.reaction.use',
        'participantId': reactor_id,
        'reactionKind': kind,
        'source': REACTION_SOURCE,
        'reason': label,
        'visible': False,
    }


def _normalized_reaction(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping) or not _text(raw.get('reactorId')):
        return None
    multiplier = raw.get('damageMultiplier')
    consumes = raw.get('consumes') if isinstance(raw.get('consumes'), Mapping) else {}
    return {
        **deepcopy(dict(raw)),
        'reactorId': _text(raw.get('reactorId')),
        'kind': condition_key(raw.get('kind')) or 'reaction',
        'label': _text(raw.get('label')) or 'Reaction',
        'cancelsHit': raw.get('cancelsHit') is True,
        'damageMultiplier': float(multiplier) if isinstance(multiplier, (int, float)) and not isinstance(multiplier, bool) else None,
        'changes': [deepcopy(change) for change in raw.get('changes') or [] if isinstance(change, Mapping)],
        'consumes': {'reaction': 1, **{key: value for key, value in consumes.items() if key != 'reaction'}},
    }


def collect_reactions(event: Mapping[str, Any], combat: dict[str, Any], state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Ask every provider about one event and keep at most one reaction per reactor.

    Kept reactions spend the reactor's reaction and their changes (led by the
    ``combat.reaction.use`` change) are applied to ``combat`` so later rolls in
    the same resolution see them.
    """

    if _text(event.get('type')) not in REACTION_EVENT_TYPES:
        return []
    kept: list[dict[str, Any]] = []
    reacted: set[str] = set()
    for provider in list(_REACTION_PROVIDERS):
        for raw in provider(event, deepcopy(combat), state) or []:
            reaction = _normalized_reaction(raw)
            if reaction is None or reaction['reactorId'] in reacted:
                continue
            reactor = _participant(combat, reaction['reactorId'])
            if not can_react(reactor):
                continue
            reacted.add(reaction['reactorId'])
            reaction['event'] = _text(event.get('type'))
            reaction['reactorName'] = _text((reactor or {}).get('name')) or reaction['reactorId']
            reaction['changes'] = [
                reaction_use_change(event=event, reactor_id=reaction['reactorId'], kind=reaction['kind'], label=reaction['label']),
                *reaction['changes'],
            ]
            apply_working_changes(combat, reaction['changes'])
            kept.append(reaction)
    return kept


def reaction_changes(reactions: Any) -> list[dict[str, Any]]:
    """Every state change the recorded reactions carry, in order."""

    return [
        deepcopy(change)
        for reaction in (reactions if isinstance(reactions, list) else [])
        if isinstance(reaction, Mapping)
        for change in reaction.get('changes') or []
        if isinstance(change, Mapping)
    ]


def opportunity_attack_provider(event: Mapping[str, Any], combat: Mapping[str, Any], state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Built-in: each enemy engaged with a participant leaving melee strikes once.

    Disengaged movers never provoke (the caller does not raise the event).
    Player characters strike automatically unless their sheet turned
    ``autoReactions`` off.
    """

    if _text(event.get('type')) != 'leaves_melee':
        return []
    from aidm_server.combat.attacks import resolve_opportunity_attack

    working = deepcopy(dict(combat))
    reactions: list[dict[str, Any]] = []
    for reactor_id in event.get('fromEngagedWith') or []:
        reactor = _participant(working, reactor_id)
        mover = _participant(working, event.get('moverId'))
        if reactor is None or mover is None or not can_react(reactor) or not can_act(mover):
            continue
        if condition_key(reactor.get('team')) == condition_key(mover.get('team')):
            continue
        if condition_key(reactor.get('team')) != 'enemy' and not auto_reactions_enabled(reactor, state):
            continue
        attack = resolve_opportunity_attack(reactor, mover, combat=working, state=state, event=event)
        if attack is None:
            continue
        reactions.append(
            {
                'reactorId': _text(reactor.get('id')),
                'kind': 'opportunity_attack',
                'label': f"{_text(reactor.get('name')) or 'An enemy'} makes an opportunity attack against {_text(mover.get('name')) or 'the mover'}.",
                'cancelsHit': False,
                'damageMultiplier': None,
                'changes': attack['changes'],
                'consumes': {'reaction': 1},
                'attack': attack['result'],
            }
        )
    return reactions


register_reaction_provider(opportunity_attack_provider)
