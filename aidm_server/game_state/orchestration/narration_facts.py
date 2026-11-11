"""What the narrator needs to tell exactly what happened this turn.

Built before narration from the turn's applied changes and the states around
them, and sent in the narration packet as ``narration``: the objects acted on
(their label, authored text, description when it has not been told yet, what a
search turned up, the unlock/open steps taken on the way), whom the hero talked
to and the next thing that person knows, how the free-text message mapped,
the enemies' names in prose (with the pronoun each takes), the authored setup
and adjustments of a fight that starts, the enemy abilities whose conditions the
hero shakes off as their turn ends, who acts next in a fight, how a fight ends
when the engine-resolved enemy turns end it (a party defeat among them), the
captives the party leaves behind when it travels on, where everyone stands for
a hero who looks in a fight, the lines the enemies speak or show (their
records' ``barks``) and the finishing blow (``finishing_blow``).

In party play (more than one hero in the session) the packet's ``party`` names
the acting hero and the heroes, and every line written here names the hero
instead of saying "you".
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

from aidm_server.combat.maneuvers import enemy_record, pronoun_key
from aidm_server.combat.modifiers import range_band
from aidm_server.combat.positioning import in_melee_reach
from aidm_server.combat.state import combat_turn_context, normalize_combat_state, participant_is_targetable
from aidm_server.game_state.campaign_pack_encounters import catalog_record
from aidm_server.game_state.orchestration.search_take import TAKEN_BY_SEARCH
from aidm_server.price_list import normalize_price

DEFAULT_LABEL_VERBS = {
    'inspect': 'Examine',
    'search': 'Search',
    'open': 'Open',
    'close': 'Close',
    'unlock': 'Unlock',
    'lock': 'Lock',
    'use': 'Use',
    'break': 'Break',
    'trigger': 'Trigger',
    'disarm': 'Disarm',
    'reset': 'Reset',
}
COIN_ORDER = ('pp', 'gp', 'ep', 'sp', 'cp')
# Said before the result of a failed check taken again slowly; the party-play form names the hero.
TAKE_TIME_LINE = 'You take your time over it; the light has changed by the time you are done.'
PARTY_TAKE_TIME_LINE = '{name} takes their time over it; the light has changed by the time they are done.'
# The validator's source for the changes of a typed pickup.
PICKUP_SOURCE = 'pre_dm_world_action'
CAPTIVES_LEFT = {False: 'You leave {captives} bound for the reeve.', True: 'The party leaves {captives} bound for the reeve.'}


def _text(value: Any) -> str:
    return str(value or '').strip()


def _heroes(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [hero for hero in state.get('playerCharacters') or [] if isinstance(hero, dict)]


def _hero_name(state: dict[str, Any], actor_id: str) -> str:
    return next((_text(hero.get('name')) for hero in _heroes(state) if _text(hero.get('id')) == actor_id), '') or 'The hero'


def party(state: dict[str, Any], *, actor_id: str) -> dict[str, Any] | None:
    """``{"actor", "heroes": [{"id", "name"}]}`` when the session holds more than one hero; None alone."""

    heroes = _heroes(state)
    if len(heroes) < 2:
        return None
    return {
        'actor': _hero_name(state, actor_id),
        'heroes': [{'id': _text(hero.get('id')), 'name': _text(hero.get('name'))} for hero in heroes],
    }


def take_time_line(state: dict[str, Any], *, actor_id: str) -> str:
    """The line said before a check taken again slowly, naming the hero in party play."""

    return PARTY_TAKE_TIME_LINE.format(name=_hero_name(state, actor_id)) if party(state, actor_id=actor_id) else TAKE_TIME_LINE


def _scene_object(state: dict[str, Any], target_id: Any) -> dict[str, Any]:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    for collection in ('interactables', 'hazards'):
        for entry in scene.get(collection) or []:
            if isinstance(entry, dict) and _text(entry.get('id')) == _text(target_id):
                return entry
    return {}


def object_label(entry: dict[str, Any], action: str) -> tuple[str, bool]:
    """The offered label of an object action and whether the pack authored it."""

    labels = entry.get('actionLabels') if isinstance(entry.get('actionLabels'), dict) else {}
    authored = _text(labels.get(action))
    if authored:
        return authored, True
    return f"{DEFAULT_LABEL_VERBS.get(action, action.title())} {_text(entry.get('name'))}".strip(), False


def item_phrase(item: dict[str, Any]) -> str:
    """``a Caravan Strongbox Key``, ``15 gp``, ``The Last Road-Stone``."""

    coins = normalize_price(item.get('currency')) if isinstance(item.get('currency'), dict) else {}
    if coins:
        return ' and '.join(f'{coins[code]} {code}' for code in COIN_ORDER if code in coins)
    name = _text(item.get('name')) or _text(item.get('id')).replace('_', ' ')
    if not name or name.lower().startswith('the ') or item.get('proper') is True:
        return name
    quantity = int(item.get('quantity') or 1)
    if quantity > 1:
        return f'{quantity} {name}'
    return f"{'an' if name[:1].lower() in 'aeiou' else 'a'} {name}"


def _key_name(state: dict[str, Any], entry: dict[str, Any], actor_id: str) -> str:
    requirements = entry.get('requirements') if isinstance(entry.get('requirements'), dict) else {}
    unlock = requirements.get('unlock') if isinstance(requirements.get('unlock'), dict) else {}
    key_ids = {_text(value) for key in ('anyItemIds', 'allItemIds') for value in unlock.get(key) or []}
    actor = next(
        (
            candidate
            for candidate in state.get('playerCharacters') or []
            if isinstance(candidate, dict) and _text(candidate.get('id')) == actor_id
        ),
        {},
    )
    items = ((actor.get('inventory') or {}).get('items') or []) if isinstance(actor.get('inventory'), dict) else []
    return next((_text(item.get('name')) for item in items if isinstance(item, dict) and _text(item.get('id')) in key_ids), '')


def object_facts(
    *,
    state_before: dict[str, Any],
    state_after: dict[str, Any],
    applied_changes: Iterable[dict[str, Any]],
    actor_id: str,
) -> list[dict[str, Any]]:
    """One record per object action the hero took this turn; what a search or an opening revealed and the hero
    took there and then is its ``taken``."""

    applied_changes = [change for change in applied_changes if isinstance(change, dict)]
    taken_ids = {
        _text(change.get('itemId'))
        for change in applied_changes
        if change.get('type') == 'scene.item.remove' and change.get(TAKEN_BY_SEARCH)
    }
    facts = []
    for change in applied_changes:
        if change.get('type') != 'scene.interactable.action':
            continue
        event = change.get('event') if isinstance(change.get('event'), dict) else {}
        action = _text(change.get('objectAction') or event.get('action')).lower()
        target_id = change.get('targetId') or event.get('targetId')
        before = _scene_object(state_before, target_id)
        entry = before or _scene_object(state_after, target_id)
        failed = _text(event.get('type')).endswith('.check_failed')
        label, authored = object_label(entry, action)
        action_text = entry.get('actionText') if isinstance(entry.get('actionText'), dict) else {}
        told = before.get('inspected') is True or before.get('searched') is True
        description = _text(entry.get('description')) or _text(entry.get('playerDescription'))
        fact: dict[str, Any] = {
            'targetId': _text(target_id),
            'name': _text(entry.get('name')) or _text(event.get('targetName')),
            'action': action,
            'label': label,
            'labelAuthored': authored,
            'failed': failed,
        }
        if event.get('takeTime') is True:
            fact['takeTime'] = True
            fact['takeTimeLine'] = take_time_line(state_before, actor_id=actor_id)
        if _text(action_text.get(action)):
            fact['actionText'] = _text(action_text.get(action))
        if not failed and action in {'inspect', 'search'}:
            fact['description'] = '' if told else description
            fact['nothingNew'] = told
        revealed = [item for item in event.get('revealedItems') or [] if isinstance(item, dict)]
        taken = [item_phrase(item) for item in revealed if _text(item.get('id')) in taken_ids]
        if taken and not failed:
            fact['taken'] = taken
        if not failed and action == 'search':
            fact['found'] = [item_phrase(item) for item in revealed if _text(item.get('id')) not in taken_ids]
            fact['uncovered'] = [
                name
                for revealed_id in event.get('revealedTargetIds') or []
                if (name := _text(_scene_object(state_after, revealed_id).get('name')))
            ]
            if _search_discovers(entry, event):
                fact['discovered'] = True
        steps = [_text(step).lower() for step in event.get('steps') or [] if _text(step)]
        if steps:
            fact['steps'] = [{'action': step, 'label': object_label(entry, step)[0]} for step in steps]
            if 'unlock' in steps:
                fact['keyName'] = _key_name(state_before, entry, actor_id)
        facts.append(fact)
    return facts


def _search_discovers(entry: dict[str, Any], event: dict[str, Any]) -> bool:
    """The search (or a step taken on the way) turns up a clue, a place or a story flag."""

    actions = ['search', *(_text(step).lower() for step in event.get('steps') or [] if _text(step))]
    return any(
        isinstance(entry.get(field), dict) and entry[field].get(action)
        for field in ('discoversClueIds', 'discoversLocationIds', 'setsFlags')
        for action in actions
    )


def _taken_phrase(item: dict[str, Any]) -> str:
    """``the Caravan Strongbox Key``; ``15 gp`` for coins."""

    if isinstance(item.get('currency'), dict) and normalize_price(item.get('currency')):
        return item_phrase(item)
    name = _text(item.get('name'))
    return name if name.lower().startswith('the ') or item.get('proper') is True else f'the {name}'


def plain_results(state_after: dict[str, Any], applied_changes: Iterable[dict[str, Any]], *, actor_id: str) -> dict[str, Any]:
    """Results the narration packet's change list leaves out: arrival, things picked up, a rest; and in
    party play who the party is (:func:`party`)."""

    results: dict[str, Any] = {}
    heroes = party(state_after, actor_id=actor_id)
    if heroes:
        results['party'] = heroes
    taken = []
    for change in applied_changes:
        if not isinstance(change, dict):
            continue
        change_type = change.get('type')
        if change_type == 'scene.move_location':
            location = next(
                (
                    record
                    for record in state_after.get('locations') or []
                    if isinstance(record, dict) and _text(record.get('id')) == _text(change.get('locationId'))
                ),
                {},
            )
            results['arrival'] = _text(location.get('name')) or _text(change.get('locationName')) or _text(
                (state_after.get('currentScene') or {}).get('name')
            )
        elif (
            change_type == 'scene.item.remove'
            and change.get('source') == PICKUP_SOURCE
            and isinstance(change.get('item'), dict)
            and not change.get(TAKEN_BY_SEARCH)
        ):
            # What a search took there and then is told with the search.
            taken.append(_taken_phrase(change['item']))
        elif change_type == 'rest.complete' and _text(change.get('actorId')) == actor_id:
            results['rest'] = {'restType': _text(change.get('restType')), 'hp': int(change.get('actualAmount') or 0)}
    if taken:
        results['taken'] = taken
    return results


def fight_start(state: dict[str, Any], applied_changes: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """The read-aloud ``setup`` of a pack encounter whose fight started this turn, and its applied adjustments' lines."""

    start = next(
        (change for change in applied_changes if isinstance(change, dict) and change.get('type') == 'combat.start'),
        None,
    )
    combat = start.get('combat') if isinstance((start or {}).get('combat'), dict) else {}
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    encounter_id = (start or {}).get('campaignPackEncounterId') or flags.get('campaignPackEncounterId')
    if not encounter_id:
        return None
    encounter = catalog_record(state, 'encounters', encounter_id)
    scaling = flags.get('partyScaling') if isinstance(flags.get('partyScaling'), dict) else {}
    adjustments = [
        _text(adjustment.get('text'))
        for adjustment in scaling.get('adjustments') or []
        if isinstance(adjustment, dict) and _text(adjustment.get('text'))
    ]
    setup = _text(encounter.get('setup'))
    if not setup and not adjustments:
        return None
    return {'setup': setup, 'adjustments': adjustments}


def enemy_names(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The fight's enemies by name, with ``proper`` for names that take no article."""

    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    names = []
    for participant in combat.get('participants') or []:
        if not isinstance(participant, dict) or participant.get('team') != 'enemy':
            continue
        record = catalog_record(state, 'enemies', participant.get('campaignPackEnemyId')) if participant.get('campaignPackEnemyId') else {}
        names.append(
            {
                'name': _text(participant.get('name')),
                'proper': participant.get('proper') is True or record.get('proper') is True,
                'pronoun': pronoun_key(state, participant),
            }
        )
    return names


def enemy_prose(state: dict[str, Any], participant: dict[str, Any]) -> str:
    """An enemy's name in prose: ``the ashmarked cutthroat``; a proper name keeps its capitals (``the Hollow Marshal``)."""

    name = _text(participant.get('name')) or 'the enemy'
    proper = participant.get('proper') is True or enemy_record(state, participant).get('proper') is True
    if proper:
        return f'the {name[4:]}' if name.lower().startswith('the ') else name
    return name.lower() if name.lower().startswith('the ') else f'the {name.lower()}'


def _health(participant: dict[str, Any]) -> str:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    current, maximum = int(hp.get('current') or 0), int(hp.get('max') or 0)
    if not maximum or current >= maximum:
        return 'unhurt'
    if current * 4 <= maximum:
        return 'barely standing'
    return 'wounded' if current * 5 <= maximum * 3 else 'hurt'


def fight_positions(state: dict[str, Any], *, actor_id: str) -> list[dict[str, Any]]:
    """Where everyone else in the fight stands from the acting hero, enemies first:
    ``[{"name", "enemy", "where", "health"}]``. ``where`` is ``on`` (in melee reach of the hero), else
    the participant's band (``near``, ``far``, ``distant``; ``close`` for melee elsewhere)."""

    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    participants = [participant for participant in combat.get('participants') or [] if isinstance(participant, dict)]
    hero = next((participant for participant in participants if _text(participant.get('id')) == actor_id), None)
    if hero is None:
        return []
    placed = []
    for participant in participants:
        if participant is hero or not participant_is_targetable(participant):
            continue
        band = range_band(participant)
        where = 'on' if in_melee_reach(hero, participant) else 'close' if band == 'melee' else band
        enemy = _text(participant.get('team')) == 'enemy'
        placed.append(
            {
                'name': enemy_prose(state, participant) if enemy else _text(participant.get('name')),
                'enemy': enemy,
                'where': where,
                **({'health': _health(participant)} if enemy else {}),
            }
        )
    return sorted(placed, key=lambda entry: not entry['enemy'])


# Barks ------------------------------------------------------------------------------------------------------------

BARK_KINDS = ('start', 'hurt', 'turning', 'taunt')
# Enemy turns that do something in the world (never replaced by a taunt); any other turn without a roll that
# deals no damage and applies no condition only fills time ("turns back to its work", "hangs back, watching").
PURPOSEFUL_INTENTS = frozenset(
    {
        'protect_ally', 'use_environment', 'negotiate', 'surrender', 'yield', 'reposition', 'defend', 'hide',
        'call_reinforcements', 'retreat', 'flee', 'break_free',
    }
)
GIVING_UP_INTENTS = frozenset({'negotiate', 'surrender', 'yield'})
FLEEING_INTENTS = frozenset({'retreat', 'flee'})
TURNING_CONDITIONS = frozenset({'captured', 'surrendered', 'yielded'})


def _barks_of(state: dict[str, Any], participant: dict[str, Any]) -> dict[str, list[str]]:
    record = enemy_record(state, participant)
    barks = record.get('barks') if isinstance(record.get('barks'), dict) else {}
    return {kind: [_text(line) for line in barks.get(kind) or [] if _text(line)] for kind in BARK_KINDS}


def _conditions(participant: dict[str, Any]) -> set[str]:
    return {_text(condition).lower() for condition in participant.get('conditions') or [] if _text(condition)}


def _hp_now(participant: dict[str, Any]) -> tuple[int, int]:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    return int(hp.get('current') or 0), int(hp.get('max') or 0)


def _turns(action: dict[str, Any]) -> bool:
    """An enemy turn that yields or flees."""

    intent = _text(action.get('intentType')).lower()
    if action.get('held') is True and intent == 'surrender':
        return True
    if action.get('resolvedWithoutRoll') is not True or action.get('droppedByReaction') is True:
        return False
    return intent in GIVING_UP_INTENTS or (intent in FLEEING_INTENTS and action.get('moveOnly') is not True)


def filler_enemy_turn(action: dict[str, Any]) -> bool:
    """An enemy turn that rolled nothing, dealt no damage, applied no condition and did nothing in the world."""

    return (
        action.get('resolvedWithoutRoll') is True
        and not action.get('breakFree')
        and not any(action.get(key) is True for key in ('turnLost', 'held', 'droppedByReaction', 'moveOnly'))
        and _text(action.get('intentType')).lower() not in PURPOSEFUL_INTENTS
        and not action.get('damageTotal')
        and not action.get('conditionsApplied')
    )


def _chosen(lines: list[str], used: set[str], key: str) -> str:
    """One of the lines, chosen from the turn; a line already used in the fight only once all have been."""

    pool = [line for line in lines if line not in used] or lines
    return pool[int(hashlib.sha1(key.encode('utf-8')).hexdigest(), 16) % len(pool)]


def enemy_barks(
    *,
    state_before: dict[str, Any],
    state_after: dict[str, Any],
    enemy_actions: Iterable[dict[str, Any]],
    applied_changes: Iterable[dict[str, Any]],
    told: Iterable[dict[str, Any]],
    turn_id: Any,
) -> list[dict[str, Any]]:
    """The lines the fight's enemies speak or show this turn, from their records' ``barks``.

    Each is ``{"kind", "enemyId", "recordId", "name", "line", "speech", "round", "after"}``: ``after`` is
    ``start`` (the fight's first narration, after its setup), ``hero`` (after the hero's own action) or the
    ``enemyId`` of the enemy turn it follows (a ``taunt`` replaces that turn's line). ``told`` holds the
    barks of this fight's earlier turns: ``start`` and ``hurt`` come once per record, a taunt at most once
    per enemy every two rounds, and no line repeats while another is unused.
    """

    combat = state_after.get('combat') if isinstance(state_after.get('combat'), dict) else {}
    round_number = int(combat.get('round') or 1)
    enemies = [participant for participant in combat.get('participants') or [] if isinstance(participant, dict) and participant.get('team') == 'enemy']
    by_id = {_text(enemy.get('id')): enemy for enemy in enemies}
    before = {
        _text(participant.get('id')): participant
        for participant in ((state_before.get('combat') or {}).get('participants') or [])
        if isinstance(participant, dict)
    }
    told = [bark for bark in told if isinstance(bark, dict)]
    used = {_text(bark.get('line')) for bark in told}
    once = {(_text(bark.get('kind')), _text(bark.get('recordId'))) for bark in told if bark.get('kind') in {'start', 'hurt'}}
    taunted = {}
    for bark in told:
        if bark.get('kind') == 'taunt':
            taunted[_text(bark.get('enemyId'))] = max(int(bark.get('round') or 0), taunted.get(_text(bark.get('enemyId')), 0))
    barks: list[dict[str, Any]] = []

    def bark(kind: str, enemy: dict[str, Any], after: str) -> None:
        record_id = _text(enemy.get('campaignPackEnemyId'))
        lines = _barks_of(state_after, enemy)[kind]
        if not lines or (kind, record_id) in once or any(entry['kind'] == kind and entry['recordId'] == record_id for entry in barks):
            return
        line = _chosen(lines, used, f'{turn_id}:{kind}:{record_id}')
        used.add(line)
        barks.append(
            {
                'kind': kind,
                'enemyId': _text(enemy.get('id')),
                'recordId': record_id,
                'name': _text(enemy.get('name')),
                'line': line,
                'speech': line[:1] in '"“',
                'round': round_number,
                'after': after,
            }
        )

    # A record's start line is told in the first narration of the fight it is in (``once`` holds those told).
    if _text(combat.get('status')).lower() in {'starting', 'active'} or any(
        isinstance(change, dict) and change.get('type') == 'combat.start' for change in applied_changes
    ):
        for enemy in enemies:
            bark('start', enemy, 'start')
    for enemy in enemies:
        gained = _conditions(enemy) - _conditions(before.get(_text(enemy.get('id')), {}))
        if gained & TURNING_CONDITIONS and _text(enemy.get('id')) in before:
            bark('turning', enemy, 'hero')
    for enemy in enemies:
        current, maximum = _hp_now(enemy)
        if participant_is_targetable(enemy) and maximum and current * 2 <= maximum:
            bark('hurt', enemy, 'hero')
    for action in enemy_actions:
        enemy = by_id.get(_text(action.get('enemyId')))
        if enemy is None:
            continue
        if _turns(action):
            bark('turning', enemy, _text(enemy.get('id')))
        elif filler_enemy_turn(action) and round_number - taunted.get(_text(enemy.get('id')), -1) >= 2:
            before_count = len(barks)
            bark('taunt', enemy, _text(enemy.get('id')))
            if len(barks) > before_count:
                taunted[_text(enemy.get('id'))] = round_number
    return barks


def captive_phrase(state: dict[str, Any], captive: dict[str, Any]) -> str:
    """``the cutthroat`` for an "Ashmarked Cutthroat"; a proper name as it is (``the Cinder Scribe``)."""

    name = _text(captive.get('name'))
    if enemy_record(state, {'campaignPackEnemyId': captive.get('enemyId')}).get('proper') is True:
        return f'the {name[4:]}' if name.lower().startswith('the ') else name
    words = name.split()
    return f'the {words[-1].lower()}' if words else 'the captive'


def captives_left_behind(state_before: dict[str, Any], applied_changes: Iterable[dict[str, Any]], *, actor_id: str) -> str | None:
    """``You leave the cutthroat bound for the reeve.`` (``The party leaves …`` in party play) when the party
    travels on from captives it held; None otherwise."""

    if not any(isinstance(change, dict) and change.get('type') == 'scene.move_location' for change in applied_changes):
        return None
    scene = state_before.get('currentScene') if isinstance(state_before.get('currentScene'), dict) else {}
    captives = [captive_phrase(state_before, captive) for captive in scene.get('captives') or [] if isinstance(captive, dict)]
    if not captives:
        return None
    listed = captives[0] if len(captives) == 1 else f"{', '.join(captives[:-1])} and {captives[-1]}"
    return CAPTIVES_LEFT[party(state_before, actor_id=actor_id) is not None].format(captives=listed)


def next_actor_line(state: dict[str, Any], *, actor_id: str, turn_ends: bool) -> str | None:
    """``It is your turn.`` or ``{Name} is up next.`` for a fight still going after this turn. In party
    play the hero acting again reads ``It is still {Name}'s turn.`` while nobody else has acted since,
    and ``It is {Name}'s turn again.`` once the round has come back to them (their turn ended)."""

    combat = normalize_combat_state(state.get('combat'), state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {})
    if _text(combat.get('status')).lower() not in {'starting', 'active'}:
        return None
    context = combat_turn_context(combat)
    current = context.get('currentActor') if isinstance(context.get('currentActor'), dict) else {}
    handoff = context.get('handoffActor') if isinstance(context.get('handoffActor'), dict) else {}
    next_actor = handoff if turn_ends or current.get('team') != 'player' else current
    if not next_actor:
        return None
    if _text(next_actor.get('id')) == actor_id:
        if not party(state, actor_id=actor_id):
            return 'It is your turn.'
        name = _hero_name(state, actor_id)
        return f"It is {name}'s turn again." if turn_ends else f"It is still {name}'s turn."
    return f"{_text(next_actor.get('name')) or 'Someone else'} is up next."
