"""Server-owned legal combat action descriptors for player clients.

Every submitted HUD action is resolved again against persisted initiative, actor
state, equipment, targeting, and the active actor's remaining turn economy.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable

from aidm_server.canon_inventory import inventory_payload
from aidm_server.character_state import server_attack_roll_context
from aidm_server.combat.state import (
    combat_turn_context,
    combat_turn_economy,
    default_turn_economy,
    normalize_combat_state,
)
from aidm_server.models import Player, safe_json_loads


LEGAL_ACTIONS_SCHEMA_VERSION = 1
ACTIVE_COMBAT_STATUSES = {'starting', 'active'}
MELEE_RANGE_BANDS = {'melee', 'near'}
RANGED_RANGE_BANDS = {'melee', 'near', 'far', 'distant'}
TURN_EXCLUDING_CONDITIONS = {
    'dead',
    'fled',
    'escaped',
    'retreated',
    'withdrawn',
    'surrendered',
    'yielded',
    'unconscious',
    'incapacitated',
    'paralyzed',
    'stunned',
    'absent',
}
WEAPON_DAMAGE_PROFILES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (('greatsword',), '2d6', 'slashing'),
    (('greataxe',), '1d12', 'slashing'),
    (('lance',), '1d12', 'piercing'),
    (('longsword', 'battleaxe'), '1d8', 'slashing'),
    (('warhammer',), '1d8', 'bludgeoning'),
    (('rapier', 'longbow', 'crossbow'), '1d8', 'piercing'),
    (('shortsword', 'spear', 'javelin'), '1d6', 'piercing'),
    (('scimitar', 'handaxe'), '1d6', 'slashing'),
    (('mace', 'quarterstaff'), '1d6', 'bludgeoning'),
    (('dagger', 'knife', 'dart'), '1d4', 'piercing'),
    (('club', 'hammer', 'baton', 'wrench'), '1d6', 'bludgeoning'),
    (('bow', 'firearm', 'pistol', 'rifle', 'sidearm'), '1d8', 'piercing'),
)


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
    conditions = {
        _text(condition).lower().replace(' ', '_').replace('-', '_')
        for condition in participant.get('conditions') or []
        if _text(condition)
    }
    return (
        participant.get('isAlive') is not False
        and participant.get('isConscious') is not False
        and participant.get('isPresent', participant.get('present', True)) is not False
        and (current_hp is None or current_hp > 0)
        and not conditions.intersection(TURN_EXCLUDING_CONDITIONS)
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


def _economy(
    *,
    action: int = 0,
    bonus_action: int = 0,
    reaction: int = 0,
    movement: str = 'unused',
    movement_cost: int = 0,
    ends_turn: bool = False,
) -> dict[str, Any]:
    return {
        'action': action,
        'bonusAction': bonus_action,
        'reaction': reaction,
        'movement': movement,
        'movementCost': movement_cost,
        'endsTurn': ends_turn,
        'tracking': 'persisted_turn_economy',
        'reactionTracked': True,
        'subTurnCountersTracked': True,
    }


def _resource_availability(
    *,
    available: bool,
    reason: str,
    economy: dict[str, Any],
    cost: dict[str, Any],
) -> tuple[bool, str]:
    if not available:
        return False, reason
    checks = (
        ('actionRemaining', 'action', int(cost.get('action') or 0)),
        ('bonusActionRemaining', 'bonus action', int(cost.get('bonusAction') or 0)),
        ('reactionRemaining', 'reaction', int(cost.get('reaction') or 0)),
        ('movementRemaining', 'movement', int(cost.get('movementCost') or 0)),
    )
    for key, label, amount in checks:
        if amount and int(economy.get(key) or 0) < amount:
            return False, f"This turn's {label} is already spent."
    return True, ''


def _weapon_damage_profile(weapon: dict[str, Any], classification: str) -> dict[str, str]:
    explicit = weapon.get('damage') if isinstance(weapon.get('damage'), dict) else {}
    explicit_dice = _text(explicit.get('dice') or weapon.get('damageDice') or weapon.get('damage_dice'))
    if explicit_dice:
        return {
            'dice': explicit_dice,
            'type': _text(explicit.get('type') or weapon.get('damageType') or weapon.get('damage_type')).lower()
            or ('piercing' if classification == 'ranged' else 'slashing'),
        }
    labels = ' '.join(
        _text(value).lower()
        for value in (weapon.get('subtype'), weapon.get('name'), *(weapon.get('tags') or []))
        if _text(value)
    )
    for markers, dice, damage_type in WEAPON_DAMAGE_PROFILES:
        if any(marker in labels for marker in markers):
            if classification == 'ranged' and damage_type == 'slashing':
                damage_type = 'piercing'
            return {'dice': dice, 'type': damage_type}
    if _text(weapon.get('id')) == 'unarmed' or 'unarmed' in labels:
        return {'dice': '1', 'type': 'bludgeoning'}
    return {'dice': '1d6', 'type': 'piercing' if classification == 'ranged' else 'slashing'}


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
    turn_economy: dict[str, Any],
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
        damage = _weapon_damage_profile(weapon, classification)
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
        economy_cost = _economy(action=1, movement='optional')
        resource_available, resource_reason = _resource_availability(
            available=available,
            reason=reason,
            economy=turn_economy,
            cost=economy_cost,
        )
        action_reason = resource_reason if not resource_available else ('' if has_target else 'No target is currently in range.')
        action = _base_action(
            action_id,
            'attack',
            f'Attack with {weapon_name}',
            'Make one server-rolled weapon attack against a legal target.',
            '',
            available=resource_available and has_target,
            reason=action_reason,
            economy=economy_cost,
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
                    'damage': damage,
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
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    current_actor = (
        turn_context.get('currentActor')
        if (
            not flags.get('turnAuthorityRedacted')
            and isinstance(turn_context.get('currentActor'), dict)
        )
        else None
    )
    available, reason = _availability(actor=actor, current_actor=current_actor)
    turn_economy = combat_turn_economy(combat, actor_id=actor.get('id')) or default_turn_economy(
        actor.get('id'),
        combat.get('round'),
    )
    player_name = _text(player.character_name) or _text(actor.get('name')) or 'I'
    actions = _weapon_actions(
        combat=combat,
        actor=actor,
        player=player,
        available=available,
        reason=reason,
        turn_economy=turn_economy,
    )
    generic_actions = [
        (
            _base_action(
                'combat.disengage',
                'disengage',
                'Disengage',
                'Withdraw carefully and move to a safer position.',
                f'{player_name} disengages and moves to a safer position.',
                available=True,
                reason='',
                economy=_economy(action=1, movement='used', movement_cost=1),
            ),
            _economy(action=1, movement='used', movement_cost=1),
        ),
        (
            _base_action(
                'combat.reposition',
                'reposition',
                'Reposition',
                'Move within the battlefield and ready for the next opening.',
                f'{player_name} repositions and readies for the next opening.',
                available=True,
                reason='',
                economy=_economy(movement='used', movement_cost=1),
            ),
            _economy(movement='used', movement_cost=1),
        ),
        (
            _base_action(
                'combat.end_turn',
                'end_turn',
                'End turn',
                'Take no further action and pass the combat turn.',
                f'{player_name} ends their turn.',
                available=True,
                reason='',
                economy=_economy(movement='unused', ends_turn=True),
            ),
            _economy(movement='unused', ends_turn=True),
        ),
    ]
    for action, cost in generic_actions:
        action_available, action_reason = _resource_availability(
            available=available,
            reason=reason,
            economy=turn_economy,
            cost=cost,
        )
        action['available'] = action_available
        action['reason'] = action_reason
        actions.append(action)
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
            'tracking': 'persisted_turn_economy',
            'actionAvailable': available and int(turn_economy.get('actionRemaining') or 0) > 0,
            'bonusActionAvailable': available and int(turn_economy.get('bonusActionRemaining') or 0) > 0,
            'movementAvailable': available and int(turn_economy.get('movementRemaining') or 0) > 0,
            'reactionAvailable': available and int(turn_economy.get('reactionRemaining') or 0) > 0,
            'actionRemaining': int(turn_economy.get('actionRemaining') or 0),
            'bonusActionRemaining': int(turn_economy.get('bonusActionRemaining') or 0),
            'reactionRemaining': int(turn_economy.get('reactionRemaining') or 0),
            'movementRemaining': int(turn_economy.get('movementRemaining') or 0),
            'reactionTracked': True,
            'subTurnCountersTracked': True,
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
                'damage_dice': _text((weapon.get('damage') or {}).get('dice')),
                'damage_type': _text((weapon.get('damage') or {}).get('type')),
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
