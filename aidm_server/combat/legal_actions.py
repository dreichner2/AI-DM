"""Server-owned legal combat action descriptors for player clients.

Every submitted HUD action is resolved again against persisted initiative, actor
state, equipment, targeting, and the active actor's remaining turn economy.

Weapon attacks (and the off-hand attack of two-weapon fighting) come from the
player's inventory; the universal actions every character has — advance, fall
back, dash, disengage, dodge, help, hide, stabilize — come from the built-in
action provider; class features add more providers.  End turn is always last.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Callable, Iterable

from aidm_server.canon_inventory import inventory_payload
from aidm_server.canon_text import normalized_name
from aidm_server.character_state import character_roll_spec, server_attack_roll_context
from aidm_server.combat.modifiers import (
    attack_context,
    can_act,
    cannot_act_reasons,
    condition_keys,
    engaged_with,
    is_hidden,
    range_band,
    speed_zero_reasons,
)
from aidm_server.combat.positioning import (
    CLOSABLE_BANDS,
    REACH_WEAPON_BANDS,
    RANGE_BANDS,
    adjacent_or_same_band,
    band_inward,
    band_outward,
    close_block_reason,
    has_cover,
    reach_block_reason,
    targeting_block_reason,
)
from aidm_server.combat.reactions import reaction_remaining
from aidm_server.combat.state import (
    MAX_MOVEMENT_PER_TURN,
    combat_turn_context,
    combat_turn_economy,
    default_turn_economy,
    normalize_combat_state,
    participant_is_targetable,
)
from aidm_server.combat.vitality import is_dying
from aidm_server.models import Player, safe_json_loads


# ``fn(snapshot, actor, combat)`` returns extra actions in the bundle action shape.
ActionProvider = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], list[dict[str, Any]]]
# ``fn(snapshot, player, action, target_id)`` returns ``(canonical, error_code, error_message)``.
ActionResolver = Callable[
    [dict[str, Any], Player, dict[str, Any], str],
    tuple[dict[str, Any] | None, str | None, str | None],
]

LEGAL_ACTIONS_SCHEMA_VERSION = 1
ACTIVE_COMBAT_STATUSES = {'starting', 'active'}
CHECK_ACTIONS = {'hide': 'stealth', 'stabilize': 'medicine'}
LIGHT_WEAPON_MARKERS = ('dagger', 'handaxe', 'light hammer', 'scimitar', 'shortsword', 'sickle', 'club')
_ACTION_PROVIDERS: list[ActionProvider] = []
_ACTION_RESOLVERS: dict[str, ActionResolver] = {}
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


def register_action_provider(fn: ActionProvider) -> ActionProvider:
    if fn not in _ACTION_PROVIDERS:
        _ACTION_PROVIDERS.append(fn)
    return fn


def unregister_action_provider(fn: ActionProvider) -> None:
    if fn in _ACTION_PROVIDERS:
        _ACTION_PROVIDERS.remove(fn)


def register_action_resolver(prefix: str, fn: ActionResolver) -> ActionResolver:
    _ACTION_RESOLVERS[_text(prefix)] = fn
    return fn


def unregister_action_resolver(prefix: str) -> None:
    _ACTION_RESOLVERS.pop(_text(prefix), None)


def _resolver_for(action_id: str) -> ActionResolver | None:
    matches = [prefix for prefix in _ACTION_RESOLVERS if prefix and action_id.startswith(prefix)]
    return _ACTION_RESOLVERS[max(matches, key=len)] if matches else None


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


def _target(participant: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        'id': _text(participant.get('id')),
        'name': _text(participant.get('name')) or 'Unknown target',
        'rangeBand': range_band(participant),
        'available': not reason,
        'reason': reason,
    }


def _attack_target_option(
    participant: dict[str, Any],
    *,
    actor: dict[str, Any],
    combat: dict[str, Any],
    classification: str,
    allowed_bands: Iterable[str],
    reach: bool,
    ability: str | None,
) -> dict[str, Any]:
    ranged = classification == 'ranged'
    reason = targeting_block_reason(actor, participant, combat) or reach_block_reason(
        actor,
        participant,
        melee=not ranged,
        allowed_bands=allowed_bands,
        reach=reach,
    )
    roll = attack_context(actor, participant, kind='weapon_attack', ranged=ranged, combat=combat, ability=ability)
    return {**_target(participant, reason), 'rollMode': roll['mode'], 'rollReasons': roll['reasons']}


def _availability(
    *,
    actor: dict[str, Any],
    current_actor: dict[str, Any] | None,
) -> tuple[bool, str]:
    if not can_act(actor):
        reasons = cannot_act_reasons(actor)
        return False, ' '.join(reasons) if reasons else 'This character is unable to act.'
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


def action_cost(economy: dict[str, Any]) -> dict[str, int]:
    """The plain per-resource cost of one action, as shown to players."""

    return {
        'action': 1 if int(economy.get('action') or 0) else 0,
        'bonusAction': 1 if int(economy.get('bonusAction') or 0) else 0,
        'movement': 1 if int(economy.get('movementCost') or 0) else 0,
        'reaction': 1 if int(economy.get('reaction') or 0) else 0,
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


def weapon_damage_profile(weapon: dict[str, Any], classification: str) -> dict[str, str]:
    explicit = weapon.get('damage') if isinstance(weapon.get('damage'), dict) else {}
    explicit_dice = _text(explicit.get('dice') or weapon.get('damageDice') or weapon.get('damage_dice'))
    if explicit_dice:
        return {
            'dice': explicit_dice,
            'type': _text(explicit.get('type') or weapon.get('damageType') or weapon.get('damage_type')).lower()
            or ('piercing' if classification == 'ranged' else 'slashing'),
        }
    labels = _weapon_labels(weapon)
    for markers, dice, damage_type in WEAPON_DAMAGE_PROFILES:
        if any(marker in labels for marker in markers):
            if classification == 'ranged' and damage_type == 'slashing':
                damage_type = 'piercing'
            return {'dice': dice, 'type': damage_type}
    if _text(weapon.get('id')) == 'unarmed' or 'unarmed' in labels:
        return {'dice': '1', 'type': 'bludgeoning'}
    return {'dice': '1d6', 'type': 'piercing' if classification == 'ranged' else 'slashing'}


def _weapon_labels(weapon: dict[str, Any]) -> str:
    return ' '.join(
        _text(value).lower()
        for value in (weapon.get('subtype'), weapon.get('name'), *(weapon.get('tags') or []))
        if _text(value)
    )


def _has_tag(weapon: dict[str, Any], tag: str) -> bool:
    return any(normalized_name(value) == tag for value in weapon.get('tags') or [])


def _is_light(weapon: dict[str, Any]) -> bool:
    labels = _weapon_labels(weapon)
    return _has_tag(weapon, 'light') or any(marker in labels for marker in LIGHT_WEAPON_MARKERS)


def _slot(weapon: dict[str, Any]) -> str:
    return normalized_name(weapon.get('slot') or weapon.get('equipmentSlot'))


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
        'cost': action_cost(economy),
        'authoritative': True,
    }


def _weapon_key(weapon: dict[str, Any], index: int) -> str:
    weapon_name = _text(weapon.get('name')) or 'Unarmed strike'
    return _stable_key(weapon.get('id') or weapon_name, fallback=f'weapon_{index + 1}')


def _weapon_attack_action(
    *,
    action_id: str,
    action_type: str,
    label: str,
    description: str,
    weapon: dict[str, Any],
    weapon_key: str,
    player: Player,
    combat: dict[str, Any],
    actor: dict[str, Any],
    enemies: list[dict[str, Any]],
    available: bool,
    reason: str,
    turn_economy: dict[str, Any],
    economy_cost: dict[str, Any],
) -> dict[str, Any]:
    weapon_name = _text(weapon.get('name')) or 'Unarmed strike'
    attack_roll_context = server_attack_roll_context(player, f'I attack with my {weapon_name}.')
    weapon_payload = attack_roll_context.get('weapon') if isinstance(attack_roll_context.get('weapon'), dict) else {}
    classification = _text(weapon_payload.get('classification')).lower() or 'melee'
    damage = weapon_damage_profile(weapon, classification)
    reach = classification != 'ranged' and _has_tag(weapon, 'reach')
    allowed_bands = RANGE_BANDS if classification == 'ranged' else (tuple(sorted(REACH_WEAPON_BANDS, key=RANGE_BANDS.index)) if reach else ('melee',))
    targets = [
        _attack_target_option(
            target,
            actor=actor,
            combat=combat,
            classification=classification,
            allowed_bands=allowed_bands,
            reach=reach,
            ability=attack_roll_context.get('ability_key'),
        )
        for target in enemies
    ]
    has_target = any(target['available'] for target in targets)
    resource_available, resource_reason = _resource_availability(
        available=available,
        reason=reason,
        economy=turn_economy,
        cost=economy_cost,
    )
    action = _base_action(
        action_id,
        action_type,
        label,
        description,
        '',
        available=resource_available and has_target,
        reason=resource_reason if not resource_available else ('' if has_target else 'No target is currently in range.'),
        economy=economy_cost,
    )
    action.update(
        {
            'requiresTarget': True,
            'targets': targets,
            'range': {
                'classification': classification,
                'allowedBands': list(allowed_bands),
                'assessment': 'range_band_and_engagement',
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
    return action


def _two_weapon_pair(weapons: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    equipped = [weapon for weapon in weapons if weapon.get('equipped')]
    main = [weapon for weapon in equipped if _slot(weapon) == 'main hand']
    off = [weapon for weapon in equipped if _slot(weapon) == 'off hand']
    if len(main) != 1 or len(off) != 1:
        return None
    pair = (main[0], off[0])
    ranged_markers = ('bow', 'crossbow', 'firearm', 'pistol', 'rifle', 'sidearm', 'sling')
    if all(_is_light(weapon) and not any(marker in _weapon_labels(weapon) for marker in ranged_markers) for weapon in pair):
        return pair
    return None


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

    enemies = [
        participant
        for participant in (combat.get('participants') or [])
        if isinstance(participant, dict) and _text(participant.get('team')).lower() == 'enemy'
    ]
    # Extra Attack: once the Attack action is taken, further attacks spend
    # ``attacksRemaining`` instead of another action.
    attacks_remaining = int(turn_economy.get('attacksRemaining') or 0)
    attack_cost = _economy(action=0 if attacks_remaining > 0 else 1, movement='optional')
    actions: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, weapon in enumerate(candidates):
        weapon_key = _weapon_key(weapon, index)
        action_id = f'combat.attack.{weapon_key}'
        if action_id in used_ids:
            continue
        used_ids.add(action_id)
        weapon_name = _text(weapon.get('name')) or 'Unarmed strike'
        actions.append(
            _weapon_attack_action(
                action_id=action_id,
                action_type='attack',
                label=f'Attack with {weapon_name}',
                description='Make one server-rolled weapon attack against a legal target.',
                weapon=weapon,
                weapon_key=weapon_key,
                player=player,
                combat=combat,
                actor=actor,
                enemies=enemies,
                available=available,
                reason=reason,
                turn_economy=turn_economy,
                economy_cost=attack_cost,
            )
        )
    pair = _two_weapon_pair(weapons)
    if pair is not None:
        main, off = pair
        attacked = set(turn_economy.get('attackWeaponIds') or [])
        main_id, off_id = (_text(weapon.get('id')) or _weapon_key(weapon, candidates.index(weapon)) for weapon in pair)
        offhand, offhand_id = (main, main_id) if off_id in attacked and main_id not in attacked else (off, off_id)
        offhand_reason = '' if attacked & {main_id, off_id} else 'Attack with one of your light weapons first.'
        offhand_name = _text(offhand.get('name')) or 'off-hand weapon'
        actions.append(
            _weapon_attack_action(
                action_id='combat.offhand_attack',
                action_type='attack',
                label=f'Off-hand attack with {offhand_name}',
                description='Strike with your other light weapon as a bonus action; your ability modifier is not added to its damage unless negative.',
                weapon=offhand,
                weapon_key=offhand_id,
                player=player,
                combat=combat,
                actor=actor,
                enemies=enemies,
                available=available and not offhand_reason,
                reason=reason or offhand_reason,
                turn_economy=turn_economy,
                economy_cost=_economy(bonus_action=1, movement='optional'),
            )
        )
        actions[-1]['offhand'] = True
    return actions


def _ally_targets(actor: dict[str, Any], combat: dict[str, Any], *, dying: bool) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for participant in combat.get('participants') or []:
        if (
            not isinstance(participant, dict)
            or _text(participant.get('id')) == _text(actor.get('id'))
            or _text(participant.get('team')).lower() not in {'player', 'ally'}
        ):
            continue
        if dying:
            if not is_dying(participant) or participant.get('isPresent', True) is False:
                continue
            reason = '' if adjacent_or_same_band(actor, participant) else 'Too far away to reach.'
        else:
            if not participant_is_targetable(participant):
                continue
            reason = '' if adjacent_or_same_band(actor, participant) else 'Too far away to help.'
        targets.append(_target(participant, reason))
    return targets


def _targeted(action: dict[str, Any], targets: list[dict[str, Any]], *, empty_reason: str) -> dict[str, Any]:
    has_target = any(target['available'] for target in targets)
    action.update({'requiresTarget': True, 'targets': targets})
    if action['available'] and not has_target:
        action.update({'available': False, 'reason': empty_reason})
    return action


def _gate(action: dict[str, Any], reason: str) -> dict[str, Any]:
    if reason and action['available']:
        action.update({'available': False, 'reason': reason})
    return action


def universal_combat_actions(_snapshot: dict[str, Any], actor: dict[str, Any], combat: dict[str, Any]) -> list[dict[str, Any]]:
    """The tactical actions every character has, with plain unavailability reasons."""

    name = _text(actor.get('name')) or 'The character'
    economy = combat_turn_economy(combat, actor_id=actor.get('id')) or default_turn_economy(actor.get('id'), combat.get('round'))
    movement_blocked = ' '.join(speed_zero_reasons(actor))
    band = range_band(actor)
    move = _economy(movement='used', movement_cost=1)
    action_only = _economy(action=1)
    enemies = [
        participant
        for participant in combat.get('participants') or []
        if isinstance(participant, dict) and _text(participant.get('team')).lower() == 'enemy'
    ]

    def action(action_id: str, action_type: str, label: str, description: str, message: str, cost: dict[str, Any]) -> dict[str, Any]:
        return _base_action(action_id, action_type, label, description, message, available=True, reason='', economy=cost)

    if band in CLOSABLE_BANDS:
        advance = action(
            'combat.advance',
            'advance',
            'Advance',
            'Close into melee with an enemy at melee or near range; you become engaged with it.',
            f'{name} closes in.',
            move,
        )
    else:
        advance = action(
            'combat.advance',
            'advance',
            'Advance',
            f'Move one band closer to the fight, to {band_inward(band)} range.',
            f'{name} advances to {band_inward(band)} range.',
            move,
        )
    fall_back = action(
        'combat.reposition',
        'reposition',
        'Fall back',
        'Move one band away from the fight. Leaving melee while engaged provokes opportunity attacks unless you disengaged this turn.',
        f'{name} falls back to {band_outward(band)} range.',
        move,
    )
    dash = action('combat.dash', 'dash', 'Dash', 'Gain one more movement this turn.', f'{name} dashes.', action_only)
    disengage = action(
        'combat.disengage',
        'disengage',
        'Disengage',
        'Your movement provokes no opportunity attacks for the rest of this turn.',
        f'{name} disengages, careful to give no opening.',
        action_only,
    )
    dodge = action(
        'combat.dodge',
        'dodge',
        'Dodge',
        'Until your next turn, attacks against you have disadvantage and you have advantage on Dexterity saves.',
        f'{name} takes the Dodge action.',
        action_only,
    )
    help_action = action(
        'combat.help',
        'help',
        'Help',
        "Give an ally in your band or the next one advantage on their next attack before your next turn.",
        f'{name} helps an ally.',
        action_only,
    )
    hide = action(
        'combat.hide',
        'hide',
        'Hide',
        'Make a server-rolled Dexterity (Stealth) check against the sharpest enemy passive Perception.',
        f'{name} tries to hide.',
        action_only,
    )
    stabilize = action(
        'combat.stabilize',
        'stabilize',
        'Stabilize',
        'Make a server-rolled Wisdom (Medicine) check, DC 10, to stop a dying ally from dying.',
        f'{name} tries to stabilize a dying ally.',
        action_only,
    )
    _gate(advance, movement_blocked)
    if band in CLOSABLE_BANDS:
        close_targets = [
            _target(enemy, close_block_reason(actor, enemy, combat))
            for enemy in enemies
            if participant_is_targetable(enemy)
        ]
        _targeted(advance, close_targets, empty_reason='Already in melee.' if band == 'melee' else 'No enemy is close enough to engage.')
    _gate(fall_back, movement_blocked or ('Already as far back as you can go.' if band == RANGE_BANDS[-1] else ''))
    _gate(dash, movement_blocked or ('You cannot move any farther this turn.' if int(economy.get('movementRemaining') or 0) >= MAX_MOVEMENT_PER_TURN else ''))
    _gate(disengage, 'Already disengaged this turn.' if economy.get('disengaged') else '')
    _gate(dodge, 'Already dodging.' if 'dodging' in condition_keys(actor) else '')
    _targeted(help_action, _ally_targets(actor, combat, dying=False), empty_reason='No ally is close enough to help.')
    _gate(
        hide,
        'Already hidden.'
        if is_hidden(actor)
        else 'You cannot hide in melee without cover.'
        if band == 'melee' and not has_cover(combat, actor)
        else '',
    )
    _targeted(stabilize, _ally_targets(actor, combat, dying=True), empty_reason='No dying ally is within reach.')
    return [advance, fall_back, dash, disengage, dodge, help_action, hide, stabilize]


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
    turn_economy['reactionRemaining'] = min(int(turn_economy.get('reactionRemaining') or 0), reaction_remaining(actor))
    player_name = _text(player.character_name) or _text(actor.get('name')) or 'I'
    actions = _weapon_actions(
        combat=combat,
        actor=actor,
        player=player,
        available=available,
        reason=reason,
        turn_economy=turn_economy,
    )
    for provider in list(_ACTION_PROVIDERS):
        for provided in provider(snapshot, actor, combat) or []:
            if not isinstance(provided, dict) or not _text(provided.get('id')):
                continue
            if any(existing['id'] == _text(provided.get('id')) for existing in actions):
                continue
            action = deepcopy(provided)
            economy = action.get('economy') if isinstance(action.get('economy'), dict) else _economy()
            action_available, action_reason = _resource_availability(
                available=available and action.get('available', True) is not False,
                reason=reason or _text(action.get('reason')),
                economy=turn_economy,
                cost=economy,
            )
            action.update(
                {
                    'economy': economy,
                    'cost': action_cost(economy),
                    'available': action_available,
                    'reason': action_reason,
                    'authoritative': True,
                }
            )
            actions.append(action)
    end_turn_cost = _economy(movement='unused', ends_turn=True)
    actions.append(
        _base_action(
            'combat.end_turn',
            'end_turn',
            'End turn',
            'Take no further action and pass the combat turn.',
            f'{player_name} ends their turn.',
            available=available,
            reason=reason,
            economy=end_turn_cost,
        )
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
        'attacksRemaining': int(turn_economy.get('attacksRemaining') or 0),
        'reactionRemaining': int(turn_economy.get('reactionRemaining') or 0),
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


def _turn_order(combat: dict[str, Any]) -> list[dict[str, Any]]:
    context = combat_turn_context(combat)
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    current_id = (
        _text((context.get('currentActor') or {}).get('id'))
        if not flags.get('turnAuthorityRedacted') and isinstance(context.get('currentActor'), dict)
        else ''
    )
    return [
        {
            'id': _text(entry.get('id')),
            'name': _text(entry.get('name')) or _text(entry.get('id')),
            'team': _text(entry.get('team')),
            'isCurrent': bool(current_id) and _text(entry.get('id')) == current_id,
        }
        for entry in context.get('turnOrder') or []
        if isinstance(entry, dict)
    ]


def _visible_engagements(combat: dict[str, Any]) -> None:
    visible_ids = {
        _text(participant.get('id'))
        for participant in combat.get('participants') or []
        if isinstance(participant, dict)
    }
    for participant in combat.get('participants') or []:
        position = participant.get('position') if isinstance(participant, dict) else None
        if not isinstance(position, dict) or 'engagedWith' not in position:
            continue
        engaged = [value for value in engaged_with(participant) if value in visible_ids]
        if engaged:
            position['engagedWith'] = engaged
        else:
            position.pop('engagedWith')


def with_combat_legal_actions(snapshot: Any, players: Iterable[Player]) -> Any:
    """Attach viewer-scoped legal-action bundles and the initiative ribbon without
    mutating persisted state; engagements name visible participants only."""

    if not isinstance(snapshot, dict):
        return snapshot
    result = deepcopy(snapshot)
    combat = result.get('combat')
    if not isinstance(combat, dict):
        return result
    _visible_engagements(combat)
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
    if _text(combat.get('status')).lower() in ACTIVE_COMBAT_STATUSES:
        combat['turnOrder'] = _turn_order(normalize_combat_state(combat, result.get('currentScene')))
    return result


def _check_payload(player: Player, action_type: str) -> dict[str, Any]:
    skill = CHECK_ACTIONS[action_type]
    spec = character_roll_spec(player, roll_type=skill)
    return {
        'skill': skill,
        'ability': (spec.get('ability') or {}).get('key'),
        'modifier': int(spec.get('modifier') or 0),
        'modifierBreakdown': deepcopy(spec.get('modifier_breakdown') or {}),
    }


def _targeted_message(player: Player, action: dict[str, Any], target_name: str) -> str:
    name = _text(player.character_name) or 'The character'
    action_type = _text(action.get('type'))
    if action_type == 'advance':
        return f'{name} closes in on {target_name}.'
    if action_type == 'help':
        return f'{name} helps {target_name}.'
    if action_type == 'stabilize':
        return f'{name} tries to stabilize {target_name}.'
    return _text(action.get('message')) or f'{name} acts toward {target_name}.'


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
    resolver = _resolver_for(normalized_action_id)
    if resolver is not None:
        return resolver(snapshot, player, action, normalized_target_id)
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
    target_name = _text((selected_target or {}).get('name')) or 'the target'
    if selected_target is not None and action['type'] == 'attack':
        weapon = action.get('weapon') if isinstance(action.get('weapon'), dict) else {}
        weapon_name = _text(weapon.get('name')) or 'weapon'
        canonical.update(
            {
                'target_id': selected_target['id'],
                'target_name': target_name,
                'weapon_id': _text(weapon.get('id')),
                'weapon_name': weapon_name,
                'damage_dice': _text((weapon.get('damage') or {}).get('dice')),
                'damage_type': _text((weapon.get('damage') or {}).get('type')),
                'ranged': _text(weapon.get('classification')) == 'ranged',
                'range_band': _text(selected_target.get('rangeBand')),
            }
        )
        if action.get('offhand'):
            canonical['offhand'] = True
            message = f'{player.character_name} attacks {target_name} with their off-hand {weapon_name}.'
        else:
            message = f'{player.character_name} attacks {target_name} with {weapon_name}.'
    elif selected_target is not None:
        canonical.update({'target_id': selected_target['id'], 'target_name': target_name})
        message = _targeted_message(player, action, target_name)
    else:
        message = _text(action.get('message'))
    if action['type'] in CHECK_ACTIONS:
        canonical['check'] = _check_payload(player, action['type'])
    canonical['message'] = message
    return canonical, None, None


def combat_snapshot_from_session(raw_snapshot: Any) -> dict[str, Any]:
    """Normalize an ORM session snapshot for action validation."""

    snapshot = safe_json_loads(raw_snapshot, {})
    return snapshot if isinstance(snapshot, dict) else {}


register_action_provider(universal_combat_actions)
