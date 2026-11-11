"""The engine brief: what the engine did this turn, for an AI narrator that resolves nothing itself.

Built before narration from the turn's real results (never from the model): the
narration facts, the combat and spell results, the refused actions and the
story beats so far, and the player-visible scene after them. The prompt
renders it above the player's words as ``ENGINE THIS TURN`` (see
``prompt_templates.render_engine_brief``); the narration hygiene pass reads its
``gains`` and ``engineOwned`` names.

Fields: ``did`` (each executed result in plain words; in a fight the hero's
resolved action with its numbers, each enemy turn, yields, captures, breaking
free and opportunity attacks, or the line that no enemy has acted), ``notDone``
(the message's deferred actions, then each engine-owned thing the player's
words name with no result this turn, by its labels), ``here`` (objects with
their available labels, loose items, ways onward, shops, captives held here
with their enemy record and ``captiveKnows``), ``place``, ``actingHero``,
``heroes``, ``gains`` (the acting hero's ``xp``, ``coins`` by code and
``items`` gained so far), ``offered`` (the labels of the actions offered to
the acting hero this turn) and ``engineOwned`` (every name above, and the
things acted on).
"""

from __future__ import annotations

from collections import Counter
import re
from typing import Any, Iterable

from aidm_server.combat.legal_actions import maneuver_actions, universal_combat_actions
from aidm_server.combat.maneuvers import enemy_record
from aidm_server.combat.state import normalize_combat_state
from aidm_server.game_state.extraction.free_text_actions import FreeTextAction, mentions, ways_onward
from aidm_server.game_state.orchestration.narration_facts import PICKUP_SOURCE
from aidm_server.game_state.orchestration.story_beats import COIN_ORDER, beat_basis, object_check_text, story_beats
from aidm_server.price_list import normalize_price
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player

# Beats the brief leaves to its own lines: checks sit with their object, a pickup with "Took".
OWN_LINE_BEATS = frozenset({'check', 'hint'})
# A fight turn on which no enemy acted says so, so the narrator has nothing to invent for them.
NO_ENEMY_ACTED = 'No enemy has acted since the last narration.'
SPOKE = 'The hero spoke: free, nothing is spent and nothing changes. Answer in the scene in at most three sentences.'
# A coin or XP beat: ``+15 gp``, ``Arden Vale −6 gp``, ``+75 XP each``.
GAIN_BEAT = re.compile(r'^(?:(?P<hero>.+) )?(?P<sign>[+−-])(?P<amount>\d+) (?P<unit>\S+)(?P<each> each)?$')


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _join(words: list[str]) -> str:
    if len(words) <= 2:
        return ' and '.join(words)
    return f"{', '.join(words[:-1])} and {words[-1]}"


def _object_line(fact: dict[str, Any], check: str | None) -> str:
    head = f"{fact.get('label')} ({check})" if check else _text(fact.get('label'))
    if fact.get('failed') is True:
        return f'{head}: failed; nothing is found, opened or learned this time.'
    parts = [f'{head}: done.']
    steps = [step for step in _dicts(fact.get('steps')) if _text(step.get('action')) != _text(fact.get('action'))]
    if steps:
        key = f" with the {fact['keyName']}" if _text(fact.get('keyName')) else ''
        parts.append(f"On the way: {', '.join(_text(step.get('label')) for step in steps)}{key}.")
    if fact.get('nothingNew') is True:
        parts.append('Nothing new beyond what was already seen.')
    elif _text(fact.get('description')):
        parts.append(f'What it shows: "{_text(fact.get("description"))}"')
    if _text(fact.get('actionText')):
        parts.append(f'What happens: "{_text(fact.get("actionText"))}"')
    found = [_text(item) for item in fact.get('found') or [] if _text(item)]
    if found:
        parts.append(f'Found, and still lying there until taken: {_join(found)}.')
    uncovered = [_text(name) for name in fact.get('uncovered') or [] if _text(name)]
    if uncovered:
        parts.append(f'Uncovered: {_join(uncovered)}.')
    return ' '.join(parts)


def _attack_line(attack: dict[str, Any]) -> str:
    actor = _text(attack.get('sourceActorName') or attack.get('enemyName')) or 'The hero'
    weapon = _text(attack.get('weaponName') or attack.get('abilityName'))
    target = _text(attack.get('targetName')) or 'the target'
    total, armor = attack.get('attackTotal'), attack.get('targetArmorClass')
    roll = f' ({total} against AC {armor})' if total is not None and armor is not None else ''
    with_weapon = f' with {weapon}' if weapon else ''
    if attack.get('hit') is True:
        damage = attack.get('damageTotal')
        damage_text = f" for {damage} {_text(attack.get('damageType')) or 'weapon'} damage" if damage is not None else ''
        return f'{actor} attacks {target}{with_weapon}{roll}: hit{damage_text}.'
    return f'{actor} attacks {target}{with_weapon}{roll}: miss.'


def _reaction_lines(action: dict[str, Any]) -> list[str]:
    """The opportunity attacks an enemy breaking away from melee took."""

    enemy = _text(action.get('enemyName')) or 'an enemy'
    lines = []
    for reaction in _dicts(action.get('reactions')):
        attack = reaction.get('attack') if isinstance(reaction.get('attack'), dict) else {}
        if attack.get('attackTotal') is None:
            continue
        attacker = _text(attack.get('attackerName') or reaction.get('reactorName')) or 'A hero'
        weapon = f" with {_text(attack.get('weaponName'))}" if _text(attack.get('weaponName')) else ''
        roll = f"{attack.get('attackTotal')} against AC {attack.get('targetArmorClass')}"
        if attack.get('hit') is True:
            outcome = f"hit for {attack.get('damageTotal')} {_text(attack.get('damageType')) or 'weapon'} damage"
        else:
            outcome = 'miss'
        lines.append(f'{attacker} makes an opportunity attack on {enemy} as it breaks away{weapon} ({roll}): {outcome}.')
    return lines


def _enemy_line(action: dict[str, Any]) -> str | None:
    enemy = _text(action.get('enemyName')) or 'An enemy'
    grappler = _text(action.get('grapplerName')) or 'its captor'
    escape = action.get('breakFree') if isinstance(action.get('breakFree'), dict) else {}
    if escape:
        result = 'breaks free' if escape.get('success') is True else 'fails; still held'
        return f"{enemy} tries to break {grappler}'s hold ({escape.get('total')} against DC {escape.get('dc')}): {result}."
    if action.get('held') is True and _text(action.get('intentType')) == 'surrender':
        return f'{enemy}, held by {grappler}, yields: it goes slack and gives up the fight, no roll.'
    if action.get('droppedByReaction') is True:
        return f'{enemy} falls where it stands before it can get away.'
    if action.get('resolvedWithoutRoll') is True:
        intent = _text(action.get('intentType')).replace('_', ' ')
        return f'{enemy}: {intent or "acts"}, no roll.'
    if action.get('saveDC') is not None and action.get('attackTotal') is None:
        outcome = 'success' if action.get('saveSucceeded') is True else 'failure'
        damage = action.get('damageTotal')
        damage_text = f", {damage} {_text(action.get('damageType')) or 'weapon'} damage" if damage else ''
        return (
            f"{enemy} uses {_text(action.get('abilityName')) or 'an ability'} on {_text(action.get('targetName')) or 'the target'}: "
            f"save {action.get('saveTotal')} against DC {action.get('saveDC')}, {outcome}{damage_text}."
        )
    if action.get('attackTotal') is None:
        return None
    return _attack_line(action)


def _combat_lines(combat_state: dict[str, Any], applied_changes: list[dict[str, Any]], enemies: list[str]) -> list[str]:
    against = f' against {_join(enemies)}' if enemies else ''
    lines = [f'A fight begins{against}.' for change in applied_changes if change.get('type') == 'combat.start'][:1]
    tactical = combat_state.get('playerResolvedCombatAction') if isinstance(combat_state.get('playerResolvedCombatAction'), dict) else {}
    if _text(tactical.get('outcome')):
        lines.append(_text(tactical['outcome']))
    attacks = []
    for attack in [combat_state.get('playerResolvedAction'), *_dicts(combat_state.get('trustedDamageEvents'))]:
        if isinstance(attack, dict) and attack not in attacks:
            attacks.append(attack)
    lines.extend(_attack_line(attack) for attack in attacks)
    enemy_actions = _dicts(combat_state.get('enemyResolvedActions'))
    for action in enemy_actions:
        if action.get('grappleEnded') is True:
            lines.append(f"{_text(action.get('enemyName')) or 'An enemy'} is no longer held.")
        lines.extend(_reaction_lines(action))
        if line := _enemy_line(action):
            lines.append(line)
    if combat_state and not enemy_actions:
        lines.append(NO_ENEMY_ACTED)
    return lines


def _spell_lines(applied_changes: list[dict[str, Any]]) -> list[str]:
    lines = []
    for change in applied_changes:
        if change.get('type') == 'class_feature.use':
            lines.append(f"Used {_text(change.get('capabilityName')) or 'a class feature'}.")
        resolution = change.get('spellResolution') if isinstance(change.get('spellResolution'), dict) else {}
        if change.get('type') != 'spell.cast' or not resolution:
            continue
        spell = _text(resolution.get('spellName') or change.get('spellName')) or 'a spell'
        results = []
        for target in _dicts(resolution.get('targets')):
            delivery = target.get('delivery') if isinstance(target.get('delivery'), dict) else {}
            name = _text(target.get('targetName')) or 'the target'
            if _text(delivery.get('type')) == 'attack':
                outcome = 'hit' if delivery.get('delivered') is True else 'miss'
                result = f"{name}: {outcome} ({delivery.get('total')} against AC {delivery.get('targetArmorClass')})"
            elif _text(delivery.get('type')) == 'save':
                outcome = 'saves' if delivery.get('saveSucceeded') is True else 'fails the save'
                result = f"{name}: {outcome} ({delivery.get('total')} against DC {delivery.get('dc')})"
            else:
                result = name
            effects = [
                f"{effect.get('amountApplied')} {_text(effect.get('kind')).replace('_', ' ')}"
                if _text(effect.get('kind')) != 'condition'
                else _text(effect.get('condition'))
                for effect in _dicts(target.get('effects'))
                if effect.get('applied') is not False
            ]
            results.append(f"{result}{', ' + ', '.join(effects) if effects else ''}")
        lines.append(f"Cast {spell}{': ' + '; '.join(results) if results else ''}.")
    return lines


def _taken_coins(applied_changes: list[dict[str, Any]]) -> dict[str, int]:
    coins: dict[str, int] = {}
    for change in applied_changes:
        item = change.get('item') if isinstance(change.get('item'), dict) else {}
        if change.get('type') == 'scene.item.remove' and change.get('source') == PICKUP_SOURCE and isinstance(item.get('currency'), dict):
            for code, amount in normalize_price(item['currency']).items():
                coins[code] = coins.get(code, 0) + int(amount)
    return coins


def _beat_line(beat: dict[str, Any], taken_coins: dict[str, int], *, acting_hero: str) -> str | None:
    kind, text = _text(beat.get('kind')), _text(beat.get('text'))
    picked_up = text.startswith(('Taken', f'{acting_hero} took '))
    if kind in OWN_LINE_BEATS or (kind == 'item_gained' and picked_up):
        return None
    gain = GAIN_BEAT.match(text) if kind in {'coins', 'xp'} else None
    if not gain:
        return f'{text}.'
    amount = int(gain['amount']) * (1 if gain['sign'] == '+' else -1)
    hero = gain['hero'] or ''
    if kind == 'coins' and not gain['each'] and hero in {'', acting_hero}:
        # Coins picked up have their own "Took" line.
        amount -= taken_coins.get(gain['unit'], 0)
    if not amount:
        return None
    verb = 'gained' if amount > 0 else 'lost'
    who = f'Each hero {verb}' if gain['each'] else f'{hero} {verb}' if hero else verb.capitalize()
    return f"{who} {abs(amount)} {gain['unit']}."


def _gains(before: dict[str, Any], after: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
    """What the acting hero gained this turn: ``xp``, ``coins`` by code and the names of ``items``."""

    old = (before.get('heroes') or {}).get(actor_id) or {}
    new = (after.get('heroes') or {}).get(actor_id) or {}
    if not old or not new:
        return {'xp': 0, 'coins': {}, 'items': []}
    coins = {
        code: delta
        for code in COIN_ORDER
        if (delta := int(new['currency'].get(code) or 0) - int(old['currency'].get(code) or 0)) > 0
    }
    return {
        'xp': max(0, new['xp'] - old['xp']),
        'coins': coins,
        'items': list((Counter(new['items']) - Counter(old['items'])).keys()),
    }


def _available_labels(entry: dict[str, Any]) -> list[str]:
    return [
        _text(offer.get('label'))
        for offer in _dicts(entry.get('offeredActions'))
        if offer.get('legal') is True and offer.get('done') is not True and _text(offer.get('label'))
    ]


def _place(state: dict[str, Any]) -> str:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    location = next(
        (record for record in _dicts(state.get('locations')) if _text(record.get('id')) == _text(scene.get('locationId'))),
        {},
    )
    return _text(location.get('name')) or _text(scene.get('name'))


def _captives(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The captives held here, as people present: their enemy record and what they know when questioned."""

    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    held = []
    for captive in _dicts(scene.get('captives')):
        record = enemy_record(state, {'campaignPackEnemyId': captive.get('enemyId')})
        held.append(
            {
                'name': _text(captive.get('name')),
                'description': _text(record.get('descriptionShort') or record.get('description')),
                'speech': _text((record.get('behavior') or {}).get('speechStyle')) if isinstance(record.get('behavior'), dict) else '',
                'knows': [_text(line) for line in record.get('captiveKnows') or [] if _text(line)],
            }
        )
    return [captive for captive in held if captive['name']]


def _offered_labels(
    state: dict[str, Any],
    *,
    actor_id: str,
    objects: list[dict[str, Any]],
    items: list[dict[str, Any]],
    ways: list[dict[str, Any]],
    captives: list[dict[str, Any]],
) -> list[str]:
    """The labels of the actions offered to the acting hero now, in a fight its fight actions."""

    labels = [label for entry in objects for label in _available_labels(entry)]
    labels.extend(_text(item.get('takeLabel')) or f"Take {_text(item.get('name'))}" for item in items)
    labels.extend(f"Go to {_text(way.get('name'))}" for way in ways)
    labels.extend(f"Question {captive['name']}" for captive in captives)
    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    actor = next((participant for participant in combat['participants'] if _text(participant.get('id')) == actor_id), None)
    if combat['status'] in {'starting', 'active'} and actor is not None:
        hero = next((hero for hero in _dicts(state.get('playerCharacters')) if _text(hero.get('id')) == actor_id), {})
        weapons = [item for item in _dicts((hero.get('inventory') or {}).get('items')) if _text(item.get('type')).lower() == 'weapon']
        labels.extend(f"Attack with {_text(weapon.get('name'))}" for weapon in weapons if weapon.get('equipped') and _text(weapon.get('name')))
        labels.extend(_text(action.get('label')) for action in [*universal_combat_actions(state, actor, combat), *maneuver_actions(state, actor, combat)])
        labels.append('End turn')
    return list(dict.fromkeys(label for label in labels if label))


def _not_done(
    *,
    player_message: str,
    free_text: FreeTextAction | None,
    objects: list[dict[str, Any]],
    items: list[dict[str, Any]],
    ways: list[dict[str, Any]],
    shops: list[dict[str, Any]],
    acted: set[str],
) -> list[str]:
    """The deferred actions, then each engine-owned thing the words name that has no result this turn."""

    labels = [_text(entry.get('label')) for entry in (free_text.deferred if free_text else ())]
    candidates = [
        *(('object', entry) for entry in objects),
        *(('item', item) for item in items),
        *(('place', way) for way in ways),
        *(('shop', shop) for shop in shops),
    ]
    for kind, record in mentions(player_message, candidates, lambda candidate: _text(candidate[1].get('name'))):
        name = _text(record.get('name'))
        if _text(record.get('id')) in acted:
            continue
        if kind == 'object':
            offered = _available_labels(record)
            if not offered or any(label in labels for label in offered):
                continue
            labels.append(' or '.join(offered))
        elif kind == 'item':
            labels.append(_text(record.get('takeLabel')) or f'Take {name}')
        elif kind == 'place':
            labels.append(f'Go to {name}')
        else:
            labels.append(f'Trade with {name}')
    return list(dict.fromkeys(label for label in labels if label))


def engine_brief(
    *,
    dm_context: dict[str, Any],
    state_before: dict[str, Any],
    state_after: dict[str, Any],
    applied_changes: Iterable[dict[str, Any]],
    free_text: FreeTextAction | None,
    player_message: str,
    actor_id: str,
    player_id: int | None,
) -> dict[str, Any]:
    """The turn's engine brief (fields in the module docstring)."""

    changes = [change for change in applied_changes if isinstance(change, dict)]
    narration = dm_context.get('narration') if isinstance(dm_context.get('narration'), dict) else {}
    basis_before = beat_basis(state_before, actor_id=actor_id)
    basis_after = beat_basis(state_after, actor_id=actor_id)
    beats = story_beats(basis_before, basis_after, actor_id=actor_id, state_before=state_before, applied_changes=changes)
    acting_hero = _text(((basis_after.get('heroes') or {}).get(actor_id) or {}).get('name'))
    checks = {
        _text(change.get('targetId')): text
        for change in changes
        if (text := object_check_text(change, state_before))
    }
    taken_coins = _taken_coins(changes)
    did: list[str] = []
    if _text(narration.get('captivesLeft')):
        did.append(_text(narration['captivesLeft']))
    if _text(narration.get('arrival')):
        did.append(f"Arrived at {narration['arrival']}.")
    if (narration.get('freeText') or {}).get('kind') == 'speech':
        did.append(SPOKE)
    did.extend(_object_line(fact, checks.get(_text(fact.get('targetId')))) for fact in _dicts(narration.get('objects')))
    taken = [_text(phrase) for phrase in narration.get('taken') or [] if _text(phrase)]
    if taken:
        did.append(f'Took {_join(taken)}.')
    rest = narration.get('rest') if isinstance(narration.get('rest'), dict) else {}
    if rest:
        hp = f" (+{rest['hp']} HP)" if isinstance(rest.get('hp'), int) and rest['hp'] > 0 else ''
        did.append(f"Finished a {_text(rest.get('restType')).replace('_', ' ') or 'rest'}{hp}.")
    did.extend(_spell_lines(changes))
    combat_state = dm_context.get('combatState') if isinstance(dm_context.get('combatState'), dict) else {}
    enemies = [_text(enemy.get('name')) for enemy in _dicts(narration.get('enemies')) if _text(enemy.get('name'))]
    did.extend(_combat_lines(combat_state, changes, enemies))
    did.extend(line for beat in beats if (line := _beat_line(beat, taken_coins, acting_hero=acting_hero)))
    did.extend(
        f"Refused: {_text(action.get('reason'))}"
        for action in _dicts(dm_context.get('invalidActions'))
        if _text(action.get('reason'))
    )

    projected = filter_session_snapshot_for_player(
        state_after,
        private_player_ids=frozenset({player_id} if player_id else ()),
        acting_player_id=player_id,
    )
    scene = projected.get('currentScene') if isinstance(projected, dict) and isinstance(projected.get('currentScene'), dict) else {}
    objects = [*_dicts(scene.get('interactables')), *_dicts(scene.get('hazards'))]
    items = [item for item in _dicts(scene.get('items')) if _text(item.get('name'))]
    ways = ways_onward(state_after)
    shops = [shop for shop in _dicts(scene.get('merchants')) if _text(shop.get('name'))]
    acted = {_text(fact.get('targetId')) for fact in _dicts(narration.get('objects'))}
    acted.update(
        _text((change.get('item') or {}).get('id'))
        for change in changes
        if change.get('type') == 'scene.item.remove' and change.get('source') == PICKUP_SOURCE and isinstance(change.get('item'), dict)
    )
    if _text(narration.get('arrival')):
        acted.update(_text(change.get('locationId')) for change in changes if change.get('type') == 'scene.move_location')
    acted_names = [_text(fact.get('name')) for fact in _dicts(narration.get('objects'))]
    gains = _gains(basis_before, basis_after, actor_id=actor_id)
    captives = _captives(state_after)
    here = {
        'objects': [{'name': _text(entry.get('name')), 'labels': _available_labels(entry)} for entry in objects if _text(entry.get('name'))],
        'items': [_text(item.get('name')) for item in items],
        'waysOnward': [_text(way.get('name')) for way in ways],
        'shops': [_text(shop.get('name')) for shop in shops],
        **({'captives': captives} if captives else {}),
    }
    return {
        'did': did,
        'notDone': _not_done(
            player_message=player_message,
            free_text=free_text,
            objects=objects,
            items=items,
            ways=ways,
            shops=shops,
            acted=acted,
        ),
        'here': here,
        'place': _place(state_after),
        'actingHero': acting_hero,
        'heroes': [_text(hero.get('name')) for hero in _dicts(state_after.get('playerCharacters')) if _text(hero.get('name'))],
        'gains': gains,
        'offered': _offered_labels(state_after, actor_id=actor_id, objects=objects, items=items, ways=ways, captives=captives),
        'engineOwned': list(
            dict.fromkeys(
                name
                for name in [
                    *(entry['name'] for entry in here['objects']),
                    *here['items'],
                    *here['waysOnward'],
                    *here['shops'],
                    *acted_names,
                    *gains['items'],
                ]
                if name
            )
        ),
    }
