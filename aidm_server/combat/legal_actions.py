"""Server-owned legal combat action descriptors for player clients.

The combat engine currently persists turn order, participant health/position, and
player inventory, but it does not persist sub-turn action or reaction counters.
This module deliberately exposes only the legality that can be proven from that
state.  Every submitted HUD action is resolved again here before turn processing.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable

from aidm_server.canon_inventory import inventory_payload
from aidm_server.character_state import server_attack_roll_context
from aidm_server.combat.state import combat_turn_context, normalize_combat_state
from aidm_server.models import Player, safe_json_loads


LEGAL_ACTIONS_SCHEMA_VERSION = 1
ACTIVE_COMBAT_STATUSES = {'starting', 'active'}
MELEE_RANGE_BANDS = {'melee', 'near'}
RANGED_RANGE_BANDS = {'melee', 'near', 'far', 'distant'}


def _text(value: Any) -> str:
    return str(value or '').strip()


def _stable_key(value: Any, *, fallback: str) -> str:
    normalized = re.sub(r'[^a-z0-9]+', '_', _text(value).lower()).strip('_')
    return (normalized or fallback)[:48]


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _participant_player_id(participant: dict[str, Any]) -> int | None:
    direct = _positive_int(participant.get('playerId', participant.get('player_id')))
    if direct is not None:
        return direct
    actor_id = _text(participant.get('id')).lower()
    for prefix in ('player_', 'player-'):
        if actor_id.startswith(prefix):
            return _positive_int(actor_id[len(prefix):])
    return None


def _actor_for_player(combat: dict[str, Any], player: Player) -> dict[str, Any] | None:
    participants = combat.get('participants') if isinstance(combat.get('participants'), list) else []
    player_id = int(player.player_id)
    for participant in participants:
        if isinstance(participant, dict) and _participant_player_id(participant) == player_id:
            return participant
    player_name = _text(player.character_name).casefold()
    if not player_name:
        return None
    legacy_matches = [
        participant
        for participant in participants
        if isinstance(participant, dict)
        # A legacy name fallback may recover one identity-less participant,
        # but it must never override a different persisted player ID or pick
        # arbitrarily between ambiguous duplicate names.
        and _participant_player_id(participant) is None
        and _text(participant.get('team')).lower() == 'player'
        and _text(participant.get('name')).casefold() == player_name
    ]
    return legacy_matches[0] if len(legacy_matches) == 1 else None


def _participant_available(participant: dict[str, Any]) -> bool:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    current_hp = _positive_int(hp.get('current', hp.get('currentHp')))
    return (
        participant.get('isAlive') is not False
        and participant.get('isConscious') is not False
        and (current_hp is None or current_hp > 0)
    )


def _position(participant: dict[str, Any]) -> dict[str, Any]:
    value = participant.get('position')
    return value if isinstance(value, dict) else {}


def _full_cover_ids(combat: dict[str, Any]) -> frozenset[str]:
    battlefield = combat.get('battlefield') if isinstance(combat.get('battlefield'), dict) else {}
    cover = battlefield.get('cover') if isinstance(battlefield.get('cover'), list) else []
    return frozenset(
        _text(entry.get('id'))
        for entry in cover
        if isinstance(entry, dict)
        and _text(entry.get('coverType', entry.get('cover_type'))).lower() == 'full'
        and _text(entry.get('id'))
    )


def _target_option(
    participant: dict[str, Any],
    *,
    actor: dict[str, Any],
    allowed_bands: set[str],
    full_cover_ids: frozenset[str],
) -> dict[str, Any]:
    position = _position(participant)
    actor_position = _position(actor)
    range_band = _text(position.get('rangeBand', position.get('range_band'))).lower() or 'near'
    target_zone = _text(position.get('zoneId', position.get('zone_id')))
    actor_zone = _text(actor_position.get('zoneId', actor_position.get('zone_id')))
    reason = ''
    if not _participant_available(participant):
        reason = 'Target is already down.'
    elif position.get('isHidden', position.get('is_hidden')) is True:
        reason = 'Target is hidden.'
    elif _text(position.get('coverId', position.get('cover_id'))) in full_cover_ids:
        reason = 'Target has full cover.'
    elif range_band not in allowed_bands:
        reason = f'Target is at {range_band} range.'
    elif allowed_bands == MELEE_RANGE_BANDS and actor_zone and target_zone and actor_zone != target_zone:
        reason = 'Target is in another battlefield zone.'
    return {
        'id': _text(participant.get('id')),
        'name': _text(participant.get('name')) or 'Unknown target',
        'rangeBand': range_band,
        'available': not reason,
        'reason': reason,
    }


def _availability(
    *,
    actor: dict[str, Any],
    current_actor: dict[str, Any] | None,
) -> tuple[bool, str]:
    if not _participant_available(actor):
        return False, 'This character is unable to act.'
    if current_actor is None:
        return False, 'Combat turn order is not established.'
    if current_actor and _text(current_actor.get('id')) != _text(actor.get('id')):
        current_name = _text(current_actor.get('name')) or 'Another combatant'
        return False, f'{current_name} is acting now.'
    return True, ''


def _economy(*, action: int, movement: str, ends_turn: bool = True) -> dict[str, Any]:
    return {
        'action': action,
        'movement': movement,
        'endsTurn': ends_turn,
        'tracking': 'turn_order_derived',
        'reactionTracked': False,
        'subTurnCountersTracked': False,
    }


def _base_action(
    action_id: str,
    action_type: str,
    label: str,
    description: str,
    message: str,
    *,
    available: bool,
    reason: str,
    economy: dict[str, Any],
) -> dict[str, Any]:
    return {
        'id': action_id,
        'type': action_type,
        'label': label,
        'description': description,
        'message': message,
        'available': available,
        'reason': reason,
        'economy': economy,
        'authoritative': True,
    }


def _weapon_actions(
    *,
    combat: dict[str, Any],
    actor: dict[str, Any],
    player: Player,
    available: bool,
    reason: str,
) -> list[dict[str, Any]]:
    weapons = [
        item
        for item in inventory_payload(player.inventory)
        if _text(item.get('type')).lower() == 'weapon'
        and int(item.get('quantity') or 1) > 0
    ]
    equipped = [item for item in weapons if item.get('equipped')]
    candidates = equipped or weapons
    if not candidates:
        candidates = [{'id': 'unarmed', 'name': 'Unarmed strike', 'type': 'weapon'}]

    full_cover_ids = _full_cover_ids(combat)
    enemies = [
        participant
        for participant in (combat.get('participants') or [])
        if isinstance(participant, dict) and _text(participant.get('team')).lower() == 'enemy'
    ]
    actions: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, weapon in enumerate(candidates):
        weapon_name = _text(weapon.get('name')) or 'Unarmed strike'
        weapon_key = _stable_key(weapon.get('id') or weapon_name, fallback=f'weapon_{index + 1}')
        action_id = f'combat.attack.{weapon_key}'
        if action_id in used_ids:
            continue
        used_ids.add(action_id)
        attack_context = server_attack_roll_context(player, f'I attack with my {weapon_name}.')
        weapon_payload = attack_context.get('weapon') if isinstance(attack_context.get('weapon'), dict) else {}
        classification = _text(weapon_payload.get('classification')).lower() or 'melee'
        allowed_bands = RANGED_RANGE_BANDS if classification == 'ranged' else MELEE_RANGE_BANDS
        targets = [
            _target_option(
                target,
                actor=actor,
                allowed_bands=allowed_bands,
                full_cover_ids=full_cover_ids,
            )
            for target in enemies
        ]
        has_target = any(target['available'] for target in targets)
        action_reason = reason if not available else ('' if has_target else 'No target is currently in range.')
        action = _base_action(
            action_id,
            'attack',
            f'Attack with {weapon_name}',
            'Make one server-rolled weapon attack against a legal target.',
            '',
            available=available and has_target,
            reason=action_reason,
            economy=_economy(action=1, movement='optional'),
        )
        action.update(
            {
                'requiresTarget': True,
                'targets': targets,
                'range': {
                    'classification': classification,
                    'allowedBands': sorted(allowed_bands, key=('melee', 'near', 'far', 'distant').index),
                    'assessment': 'persisted_range_band_only',
                },
                'roll': {'die': 'd20', 'outcome': 'server_authoritative'},
                'weapon': {
                    'id': _text(weapon.get('id')) or weapon_key,
                    'name': weapon_name,
                    'classification': classification,
                },
            }
        )
        actions.append(action)
    return actions


def legal_combat_actions_for_player(snapshot: Any, player: Player) -> dict[str, Any] | None:
    """Return the server-issued combat HUD bundle for one persisted player."""

    if not isinstance(snapshot, dict):
        return None
    raw_combat = snapshot.get('combat')
    if not isinstance(raw_combat, dict):
        return None
    combat = normalize_combat_state(raw_combat, snapshot.get('currentScene'))
    if _text(combat.get('status')).lower() not in ACTIVE_COMBAT_STATUSES:
        return None
    actor = _actor_for_player(combat, player)
    if actor is None:
        return None

    turn_context = combat_turn_context(combat)
    current_actor = (
        turn_context.get('currentActor')
        if combat.get('turnIndex') is not None and isinstance(turn_context.get('currentActor'), dict)
        else None
    )
    available, reason = _availability(actor=actor, current_actor=current_actor)
    player_name = _text(player.character_name) or _text(actor.get('name')) or 'I'
    actions = _weapon_actions(
        combat=combat,
        actor=actor,
        player=player,
        available=available,
        reason=reason,
    )
    actions.extend(
        [
            _base_action(
                'combat.defend',
                'defend',
                'Defend',
                'Take a defensive stance for this turn.',
                f'{player_name} takes a defensive stance and watches for the next threat.',
                available=available,
                reason=reason,
                economy=_economy(action=1, movement='optional'),
            ),
            _base_action(
                'combat.disengage',
                'disengage',
                'Disengage',
                'Withdraw carefully and move to a safer position.',
                f'{player_name} disengages and moves to a safer position.',
                available=available,
                reason=reason,
                economy=_economy(action=1, movement='used'),
            ),
            _base_action(
                'combat.reposition',
                'reposition',
                'Reposition',
                'Move within the battlefield and ready for the next opening.',
                f'{player_name} repositions and readies for the next opening.',
                available=available,
                reason=reason,
                economy=_economy(action=1, movement='used'),
            ),
            _base_action(
                'combat.end_turn',
                'end_turn',
                'End turn',
                'Take no further action and pass the combat turn.',
                f'{player_name} ends their turn.',
                available=available,
                reason=reason,
                economy=_economy(action=0, movement='unused'),
            ),
        ]
    )
    return {
        'schemaVersion': LEGAL_ACTIONS_SCHEMA_VERSION,
        'playerId': int(player.player_id),
        'actorId': _text(actor.get('id')),
        'actorName': _text(actor.get('name')) or player_name,
        'round': int(combat.get('round') or 1),
        'currentActorId': _text((current_actor or {}).get('id')) or None,
        'currentActorName': _text((current_actor or {}).get('name')) or None,
        'isCurrentActor': bool(
            current_actor and _text(current_actor.get('id')) == _text(actor.get('id'))
        ),
        'economy': {
            'tracking': 'turn_order_derived',
            'actionAvailable': available,
            'movementAvailable': available,
            'reactionTracked': False,
            'subTurnCountersTracked': False,
        },
        'actions': actions,
    }


def with_combat_legal_actions(snapshot: Any, players: Iterable[Player]) -> Any:
    """Attach viewer-scoped legal-action bundles without mutating persisted state."""

    if not isinstance(snapshot, dict):
        return snapshot
    result = deepcopy(snapshot)
    combat = result.get('combat')
    if not isinstance(combat, dict):
        return result
    bundles = [
        bundle
        for player in players
        if (bundle := legal_combat_actions_for_player(result, player)) is not None
    ]
    if bundles:
        combat['legalActions'] = bundles
        combat['legalActionsSchemaVersion'] = LEGAL_ACTIONS_SCHEMA_VERSION
    else:
        combat.pop('legalActions', None)
        combat.pop('legalActionsSchemaVersion', None)
    return result


def resolve_combat_legal_action(
    snapshot: Any,
    player: Player,
    *,
    action_id: Any,
    target_id: Any = None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Resolve and canonicalize a client-selected action against current state."""

    bundle = legal_combat_actions_for_player(snapshot, player)
    if bundle is None:
        return None, 'combat_not_active', 'No active combat action is available for this character.'
    normalized_action_id = _text(action_id)
    action = next(
        (candidate for candidate in bundle['actions'] if candidate.get('id') == normalized_action_id),
        None,
    )
    if action is None:
        return None, 'combat_action_invalid', 'That combat action is not available in the current state.'
    if not action.get('available'):
        return None, 'combat_action_unavailable', _text(action.get('reason')) or 'That combat action is not available now.'

    normalized_target_id = _text(target_id)
    selected_target = None
    if action.get('requiresTarget'):
        selected_target = next(
            (target for target in action.get('targets') or [] if target.get('id') == normalized_target_id),
            None,
        )
        if selected_target is None:
            return None, 'combat_target_invalid', 'Choose a current combat target for that action.'
        if not selected_target.get('available'):
            return None, 'combat_target_unavailable', _text(selected_target.get('reason')) or 'That target is not available.'
    elif normalized_target_id:
        return None, 'combat_target_invalid', 'That combat action does not accept a target.'

    canonical = {
        'action_id': action['id'],
        'action_type': action['type'],
        'economy': deepcopy(action['economy']),
        'authoritative': True,
    }
    if selected_target is not None:
        weapon = action.get('weapon') if isinstance(action.get('weapon'), dict) else {}
        target_name = _text(selected_target.get('name')) or 'the target'
        weapon_name = _text(weapon.get('name')) or 'weapon'
        canonical.update(
            {
                'target_id': selected_target['id'],
                'target_name': target_name,
                'weapon_id': _text(weapon.get('id')),
                'weapon_name': weapon_name,
                'range_band': _text(selected_target.get('rangeBand')),
            }
        )
        message = f'{player.character_name} attacks {target_name} with {weapon_name}.'
    else:
        message = _text(action.get('message'))
    canonical['message'] = message
    return canonical, None, None


def combat_snapshot_from_session(raw_snapshot: Any) -> dict[str, Any]:
    """Normalize an ORM session snapshot for action validation."""

    snapshot = safe_json_loads(raw_snapshot, {})
    return snapshot if isinstance(snapshot, dict) else {}
