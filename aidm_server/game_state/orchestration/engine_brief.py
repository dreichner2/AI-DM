"""The engine brief: what the engine did this turn, for an AI narrator that resolves nothing itself.

Built before narration from the turn's real results (never from the model): the
narration facts, the combat and spell results, the refused actions and the
story beats so far, and the player-visible scene after them. The prompt
renders it above the player's words as ``ENGINE THIS TURN`` (see
``prompt_templates.render_engine_brief``); the narration hygiene pass reads its
``gains`` and ``engineOwned`` names.

Fields: ``did`` (each executed result in plain words), ``notDone`` (the
message's deferred actions, then each engine-owned thing the player's words
name with no result this turn, by its labels), ``here`` (objects with their
available labels, loose items, ways onward, shops), ``place``, ``heroes``,
``gains`` (``xp``, ``coins`` by code and ``items`` the story beats report so
far) and ``engineOwned`` (every name above, and the things acted on).
"""

from __future__ import annotations

from typing import Any, Iterable

from aidm_server.game_state.extraction.free_text_actions import FreeTextAction, mentions, ways_onward
from aidm_server.game_state.orchestration.narration_facts import PICKUP_SOURCE
from aidm_server.game_state.orchestration.story_beats import beat_basis, object_check_text, story_beats
from aidm_server.price_list import normalize_price
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player

# Beats the brief leaves to its own lines: checks sit with their object, a pickup with "Took".
OWN_LINE_BEATS = frozenset({'check', 'hint'})


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


def _enemy_line(action: dict[str, Any]) -> str | None:
    enemy = _text(action.get('enemyName')) or 'An enemy'
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
    attacks = []
    for attack in [combat_state.get('playerResolvedAction'), *_dicts(combat_state.get('trustedDamageEvents'))]:
        if isinstance(attack, dict) and attack not in attacks:
            attacks.append(attack)
    lines.extend(_attack_line(attack) for attack in attacks)
    lines.extend(line for action in _dicts(combat_state.get('enemyResolvedActions')) if (line := _enemy_line(action)))
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


def _beat_line(beat: dict[str, Any], taken_coins: dict[str, int]) -> str | None:
    kind, text = _text(beat.get('kind')), _text(beat.get('text'))
    if kind in OWN_LINE_BEATS or (kind == 'item_gained' and text.startswith('Taken')):
        return None
    if kind == 'coins':
        amount_text, _space, code = text.partition(' ')
        amount = int(amount_text.lstrip('+−-') or 0) * (-1 if amount_text.startswith(('−', '-')) else 1)
        amount -= taken_coins.get(code, 0)
        if not amount:
            return None
        return f'Gained {amount} {code}.' if amount > 0 else f'Lost {-amount} {code}.'
    if kind == 'xp':
        return f"Gained {text.lstrip('+')}."
    return f'{text}.'


def _gains(beats: list[dict[str, Any]]) -> dict[str, Any]:
    gains: dict[str, Any] = {'xp': 0, 'coins': {}, 'items': []}
    for beat in beats:
        kind, text = _text(beat.get('kind')), _text(beat.get('text'))
        if kind == 'xp':
            gains['xp'] += int(text.lstrip('+').split()[0])
        elif kind == 'coins' and text.startswith('+'):
            amount, _space, code = text[1:].partition(' ')
            gains['coins'][code] = gains['coins'].get(code, 0) + int(amount)
        elif kind == 'item_gained':
            name = text.split(' — ', 1)[-1].rsplit(' ×', 1)[0]
            gains['items'].append(name)
    return gains


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
    beats = story_beats(
        beat_basis(state_before, actor_id=actor_id),
        beat_basis(state_after, actor_id=actor_id),
        actor_id=actor_id,
        state_before=state_before,
        applied_changes=changes,
    )
    checks = {
        _text(change.get('targetId')): text
        for change in changes
        if (text := object_check_text(change, state_before))
    }
    taken_coins = _taken_coins(changes)
    did: list[str] = []
    if _text(narration.get('arrival')):
        did.append(f"Arrived at {narration['arrival']}.")
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
    did.extend(line for beat in beats if (line := _beat_line(beat, taken_coins)))
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
    gains = _gains(beats)
    here = {
        'objects': [{'name': _text(entry.get('name')), 'labels': _available_labels(entry)} for entry in objects if _text(entry.get('name'))],
        'items': [_text(item.get('name')) for item in items],
        'waysOnward': [_text(way.get('name')) for way in ways],
        'shops': [_text(shop.get('name')) for shop in shops],
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
        'heroes': [_text(hero.get('name')) for hero in _dicts(state_after.get('playerCharacters')) if _text(hero.get('name'))],
        'gains': gains,
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
