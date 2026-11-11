"""Story beats: the quiet lines under a Dungeon Master turn that say what changed.

Beats are computed from the turn's before and after state in the player
projection (so they are right whichever code path made the change) and from
the turn's check results. The basis of the state before the turn is stored on
the turn when its rules run; the beats are written onto the turn, its
persisted Dungeon Master log entry and its turn event once every change of
the turn, campaign progress included, has landed.

Kinds, in this order: check, hp, condition, death_save, combat_end,
item_gained / item_lost, coins, clue, objective_complete / objective_new,
place, xp, level, defeat, ending, then hint (once per session, for words the
scripted narrator could not map to an action). Beats name people and
creatures by their display names, never by participant ids.

A check beat names the skill or ability once (``Investigation check: 14 —
success``), or reads ``Check: 14 — success`` when neither is known. A roll the
player made on their own, with no check pending, is not a check: it reads
``d20: 10``, or ``Strength: 13 (d20 10 + 3)`` with the ability they picked.

Alone, the hero's beats speak to them (``Took 7 damage``, ``You are dying``,
``+10 gp``). In party play (more than one hero in the session) every beat about
a hero names them (``Seren Ashgrove took 7 damage``, ``Seren Ashgrove —
Investigation check: 15 — success``, ``Arden Vale took the Caravan Strongbox
Key``, ``Seren Ashgrove reached level 2``); a coin or XP gain every hero received
alike is one beat, ``+10 gp each``. Beats about the party or the world read the
same either way. A hero put out of the fight has the one beat ``… is out of the
fight``; a hero who wakes, ``… comes round``.

A fight maneuver or improvised action the hero took has its check beat
(``Athletics check: 17 — success (DC 12)``), and an enemy that is newly held,
captured or yielding has its own: ``Grappled — Ashmarked Cutthroat``,
``Captured — Ashmarked Cutthroat``, ``Ashmarked Cutthroat yields``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from aidm_server.character_state import ABILITY_KEYS, SKILL_ABILITY
from aidm_server.combat.defeat import WAKE_CHANGE_TYPES
from aidm_server.combat.maneuvers import IMPROVISE_TYPE, MANEUVER_TYPES, YIELD_CONDITIONS
from aidm_server.combat.vitality import OUT_OF_FIGHT, VITALITY_CONDITIONS
from aidm_server.database import db
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY
from aidm_server.game_state.models import parse_actor_player_id
from aidm_server.models import DmTurn, Session, SessionLogEntry, TurnEvent, safe_json_dumps, safe_json_loads
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player
from aidm_server.turn_events import DM_RESPONSE_EVENT

STORY_BEATS_KEY = 'storyBeats'
BEAT_BASIS_KEY = 'story_beats_basis'
HINT_SHOWN_KEY = 'storyHintShown'
SCRIPTED_NARRATOR_PROVIDER = 'fallback'
HINT_TEXT = 'Scripted narrator — use the suggestions, or connect an AI narrator in Settings for free-form play'
MINUS = '−'
COMBAT_END_TEXT = {
    'all_enemies_defeated': 'Victory',
    'enemies_surrendered': 'The fight ends — they yield',
    'negotiated_resolution': 'The fight ends — they yield',
    'enemies_fled': 'The fight ends — they flee',
    'players_fled': 'You got away',
}
COIN_ORDER = ('pp', 'gp', 'ep', 'sp', 'cp')
ACTIVE_COMBAT_STATUSES = frozenset({'starting', 'active'})
# Names that say only "a check", never which one.
GENERIC_CHECK_NAMES = frozenset({'check', 'ability'})
# Conditions a hero who wakes leaves behind; their one beat is "… comes round".
WAKING_CONDITIONS = frozenset({*VITALITY_CONDITIONS, 'prone'})
# How an item came or went, alone (``Taken — …``) and in party play (``Arden Vale took …``).
ITEM_VERBS = {'Taken': 'took', 'Received': 'received', 'Sold': 'sold', 'Given': 'gave', 'Used': 'used'}


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _humanize(value: Any) -> str:
    return _text(value).replace('_', ' ')


def beat_basis(state: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
    """The parts of the player projection of ``state`` that story beats compare.

    Every hero's own record is read, the acting hero's and, in party play, each companion's: the shared
    story tells what each of them gained.
    """

    player_ids = {parse_actor_player_id(actor_id), *(parse_actor_player_id(hero.get('id')) for hero in _dicts(state.get('playerCharacters')))}
    projected = filter_session_snapshot_for_player(state, private_player_ids=frozenset(player_id for player_id in player_ids if player_id))
    projected = projected if isinstance(projected, dict) else {}
    combat = projected.get('combat') if isinstance(projected.get('combat'), dict) else {}
    # In a fight a hero's conditions live on their combat participant; the hero record
    # is rebuilt from the character row at each turn's start and only catches up later.
    fighting = (
        {_text(participant.get('id')): participant.get('conditions') or [] for participant in _dicts(combat.get('participants'))}
        if _text(combat.get('status')).lower() in ACTIVE_COMBAT_STATUSES
        else {}
    )
    heroes = {}
    for hero in _dicts(projected.get('playerCharacters')):
        health = hero.get('health') if isinstance(hero.get('health'), dict) else {}
        conditions = fighting.get(_text(hero.get('id')), health.get('conditions') or [])
        inventory = hero.get('inventory') if isinstance(hero.get('inventory'), dict) else {}
        items: Counter = Counter()
        for item in _dicts(inventory.get('items')):
            if _text(item.get('name')):
                items[_text(item.get('name'))] += _int(item.get('quantity')) or 1
        xp = hero.get('xp') if isinstance(hero.get('xp'), dict) else {}
        heroes[_text(hero.get('id'))] = {
            'name': _text(hero.get('name')),
            'hp': _int(health.get('currentHp')),
            'conditions': sorted({_text(condition).lower() for condition in conditions if _text(condition)}),
            'items': dict(items),
            'currency': {code: _int(amount) for code, amount in (inventory.get('currency') or {}).items()}
            if isinstance(inventory.get('currency'), dict)
            else {},
            'xp': _int(xp.get('current')),
            'level': _int(hero.get('level')),
        }
    scene = projected.get('currentScene') if isinstance(projected.get('currentScene'), dict) else {}
    journal = projected.get('journal') if isinstance(projected.get('journal'), dict) else {}
    objectives = journal.get('objectives') if isinstance(journal.get('objectives'), list) else [
        {'id': objective.get('id'), 'title': objective.get('title'), 'status': objective.get('status')}
        for quest in _dicts(projected.get('quests'))
        for objective in _dicts(quest.get('objectives'))
    ]
    places = journal.get('places') if isinstance(journal.get('places'), list) else _dicts(projected.get('locations'))
    clues = journal.get('clues') if isinstance(journal.get('clues'), list) else _dicts(projected.get('clues'))
    pack = projected.get('campaignPack') if isinstance(projected.get('campaignPack'), dict) else {}
    ending = pack.get('ending') if isinstance(pack.get('ending'), dict) else {}
    enemies = {
        _text(participant.get('id')): {
            'name': _text(participant.get('name')),
            'conditions': sorted({_text(condition).lower() for condition in participant.get('conditions') or [] if _text(condition)}),
        }
        for participant in _dicts(combat.get('participants'))
        if _text(participant.get('team')).lower() == 'enemy' and _text(participant.get('id'))
    }
    return {
        'heroes': heroes,
        'enemies': enemies,
        'sceneItems': sorted({_text(item.get('name')) for item in _dicts(scene.get('items')) if _text(item.get('name'))}),
        'clues': {_text(clue.get('id')): _text(clue.get('title') or clue.get('name')) for clue in _dicts(clues) if _text(clue.get('id'))},
        'objectives': {
            _text(objective.get('id')): {'title': _text(objective.get('title')), 'status': _text(objective.get('status')).lower()}
            for objective in _dicts(objectives)
            if _text(objective.get('id'))
        },
        'places': {_text(place.get('id')): _text(place.get('name')) for place in _dicts(places) if _text(place.get('id'))},
        'ending': _text(ending.get('title')),
    }


def _check_label(skill: Any, ability: Any) -> str:
    """``Investigation check``; ``Check`` when neither a skill nor an ability is known."""

    for value in (skill, ability):
        name = _humanize(value).lower()
        if name and name not in GENERIC_CHECK_NAMES:
            return f'{name.title()} check'
    return 'Check'


def _check_beat(check: dict[str, Any], *, show_dc: bool, label: str | None = None) -> dict[str, Any] | None:
    total = check.get('total')
    outcome = _text(check.get('outcome')).lower()
    if not isinstance(total, int) or outcome not in {'success', 'failure'}:
        return None
    label = label or _check_label(check.get('skill'), check.get('ability'))
    dc = check.get('dc')
    suffix = f' (DC {dc})' if show_dc and isinstance(dc, int) else ''
    if check.get('takeTime') is True:
        suffix += ' (taking your time)'
    return {'kind': 'check', 'text': f'{label}: {total} — {outcome}{suffix}'}


def object_check_text(change: dict[str, Any], state_before: dict[str, Any]) -> str | None:
    """``Investigation check: 15 — success`` for an object action's check, as its beat reads."""

    event = change.get('event') if isinstance(change.get('event'), dict) else {}
    if change.get('type') != 'scene.interactable.action' or not isinstance(event.get('check'), dict):
        return None
    beat = _check_beat(event['check'], show_dc=_shows_dc(state_before, change))
    return beat['text'] if beat else None


def _shows_dc(state: dict[str, Any], change: dict[str, Any]) -> bool:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    for collection in ('interactables', 'hazards'):
        for entry in _dicts(scene.get(collection)):
            if _text(entry.get('id')) != _text(change.get('targetId')):
                continue
            requirements = entry.get('requirements') if isinstance(entry.get('requirements'), dict) else {}
            requirement = requirements.get(_text(change.get('objectAction'))) if isinstance(requirements, dict) else None
            check = requirement.get('check') if isinstance(requirement, dict) else None
            return isinstance(check, dict) and check.get('showDc') is True
    return False


def free_roll_text(roll: dict[str, Any]) -> str:
    """``d20: 10``, or ``Strength: 13 (d20 10 + 3)`` with the ability the player picked."""

    die = _text(roll.get('die')) or 'd20'
    total = _int(roll.get('total'))
    ability = _humanize((roll.get('ability') or {}).get('key') if isinstance(roll.get('ability'), dict) else '').title()
    if not ability:
        return f'{die}: {total}'
    modifier = _int(roll.get('modifier'))
    detail = f'{die} {_int(roll.get("kept"))}' + (f' + {modifier}' if modifier > 0 else f' {MINUS} {-modifier}' if modifier < 0 else '')
    return f'{ability}: {total} ({detail})'


def _roll_beat(roll: dict[str, Any]) -> dict[str, Any] | None:
    rule_type = _text(roll.get('rule_type')).lower()
    if roll.get('free') is True:
        return {'kind': 'check', 'text': free_roll_text(roll)}
    if not rule_type or rule_type in {'attack', 'spell_attack', 'initiative'}:
        return None
    ability = roll.get('ability') if isinstance(roll.get('ability'), dict) else {}
    if rule_type.endswith('_saving_throw'):
        label = f"{_humanize(rule_type.removesuffix('_saving_throw')).title()} saving throw"
    else:
        label = _check_label(rule_type if rule_type in SKILL_ABILITY or rule_type in ABILITY_KEYS else '', ability.get('key'))
    return _check_beat(
        {'total': roll.get('total'), 'outcome': roll.get('outcome'), 'dc': roll.get('dc')},
        show_dc=isinstance(roll.get('dc'), int),
        label=label,
    )


def _fight_check_beat(combat_action: dict[str, Any]) -> dict[str, Any] | None:
    """``Athletics check: 17 — success (DC 12)`` for a maneuver or improvised action that rolled."""

    check = combat_action.get('check') if isinstance(combat_action.get('check'), dict) else {}
    if _text(combat_action.get('actionType')) not in {*MANEUVER_TYPES, IMPROVISE_TYPE} or not check:
        return None
    return _check_beat(
        {'total': check.get('total'), 'outcome': 'success' if check.get('success') is True else 'failure', 'dc': check.get('dc')},
        show_dc=True,
        label=_check_label(check.get('skill'), check.get('ability')),
    )


def _enemy_beats(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """``Grappled — Ashmarked Cutthroat``, ``Captured — …`` and ``… yields`` for what an enemy newly became."""

    beats = []
    for enemy_id, new in after.items():
        old = before.get(enemy_id)
        if not old:
            continue
        gained = set(new['conditions']) - set(old['conditions'])
        if 'grappled' in gained:
            beats.append({'kind': 'condition', 'text': f"Grappled — {new['name']}"})
        if 'captured' in gained:
            beats.append({'kind': 'condition', 'text': f"Captured — {new['name']}"})
        elif gained & YIELD_CONDITIONS and not set(old['conditions']) & YIELD_CONDITIONS:
            beats.append({'kind': 'condition', 'text': f"{new['name']} yields"})
    return beats


def _you(hero: dict[str, Any], *, party: bool) -> tuple[str, str]:
    """Subject and verb form: ``("You", "are")`` alone, ``("Mara Fen", "is")`` in party play."""

    return (hero.get('name') or 'Someone', 'is') if party else ('You', 'are')


def _coins(code: str, amount: int) -> str:
    return f'+{amount} {code}' if amount > 0 else f'{MINUS}{abs(amount)} {code}'


def _the(name: str) -> str:
    return name if name.lower().startswith('the ') else f'the {name}'


def _item_beat(kind: str, verb: str, name: str, count: int, hero: dict[str, Any], *, party: bool) -> dict[str, Any]:
    """``Taken — Torch ×2``; in party play ``Arden Vale took the Torch`` (``… took Torch ×2``)."""

    if not party:
        return {'kind': kind, 'text': f"{verb} — {name}{f' ×{count}' if count > 1 else ''}"}
    thing = f'{name} ×{count}' if count > 1 else _the(name)
    return {'kind': kind, 'text': f"{hero.get('name') or 'Someone'} {ITEM_VERBS[verb]} {thing}"}


def _shared_gains(
    kind: str,
    deltas: dict[str, int],
    heroes: dict[str, dict[str, Any]],
    text: Any,
    *,
    party: bool,
) -> list[dict[str, Any]]:
    """One beat per hero's gain (``+10 gp``, ``Arden Vale +6 gp``), or ``+10 gp each`` when every hero gained alike."""

    gained = {hero_id: delta for hero_id, delta in deltas.items() if delta}
    if not gained:
        return []
    if party and len(deltas) > 1 and len(gained) == len(deltas) and len(set(gained.values())) == 1:
        return [{'kind': kind, 'text': f'{text(next(iter(gained.values())))} each'}]
    return [
        {'kind': kind, 'text': f"{heroes[hero_id].get('name') or 'Someone'} {text(delta)}" if party else text(delta)}
        for hero_id, delta in gained.items()
    ]


def story_beats(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    actor_id: str,
    state_before: dict[str, Any],
    applied_changes: list[dict[str, Any]],
    authoritative_roll: dict[str, Any] | None = None,
    combat_action: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The turn's beats, in contract order, from the before/after bases and its check results
    (``combat_action``: the engine-resolved fight action of the turn)."""

    heroes_before = before.get('heroes') or {}
    heroes_after = after.get('heroes') or {}
    party = len(heroes_after) > 1
    acting_name = (heroes_after.get(actor_id) or {}).get('name') or 'Someone'
    beats: list[dict[str, Any]] = []
    checks = [text for change in applied_changes if (text := object_check_text(change, state_before))]
    if isinstance(authoritative_roll, dict) and (beat := _roll_beat(authoritative_roll)):
        checks.append(beat['text'])
    if isinstance(combat_action, dict) and (beat := _fight_check_beat(combat_action)):
        checks.append(beat['text'])
    beats.extend({'kind': 'check', 'text': f'{acting_name} — {text}' if party else text} for text in checks)

    ordered = [actor_id, *sorted(hero_id for hero_id in heroes_after if hero_id != actor_id)]
    pairs = [
        (hero_id, heroes_before[hero_id], heroes_after[hero_id])
        for hero_id in ordered
        if heroes_before.get(hero_id) and heroes_after.get(hero_id)
    ]
    woken = {
        str(hero_id)
        for change in applied_changes
        if change.get('type') in WAKE_CHANGE_TYPES
        for hero_id in change.get('wokenActorIds') or []
    }
    for hero_id, old, new in pairs:
        delta = new['hp'] - old['hp']
        # Waking from 0 HP is the hero coming round, not healing.
        if delta and not (hero_id in woken and old['hp'] <= 0):
            text = f'Took {-delta} damage' if delta < 0 else f'Healed {delta}'
            beats.append({'kind': 'hp', 'text': f"{new['name'] or 'Someone'} {text[0].lower()}{text[1:]}" if party else text})
    for hero_id, old, new in pairs:
        subject, verb = _you(new, party=party)
        gained = [condition for condition in new['conditions'] if condition not in old['conditions']]
        lost = [condition for condition in old['conditions'] if condition not in new['conditions']]
        if hero_id in woken:
            beats.append({'kind': 'condition', 'text': f"{subject} {'comes' if party else 'come'} round"})
            gained, lost = ([condition for condition in group if condition not in WAKING_CONDITIONS] for group in (gained, lost))
        elif OUT_OF_FIGHT in gained:
            beats.append({'kind': 'condition', 'text': f'{subject} {verb} out of the fight'})
            gained, lost = ([condition for condition in group if condition not in VITALITY_CONDITIONS] for group in (gained, lost))
        for condition in gained:
            beats.append({'kind': 'condition', 'text': f'{subject} {verb} {_humanize(condition)}'})
        for condition in lost:
            text = f'{subject} is no longer {_humanize(condition)}' if party else f'No longer {_humanize(condition)}'
            beats.append({'kind': 'condition', 'text': text})
    beats.extend(_enemy_beats(before.get('enemies') or {}, after.get('enemies') or {}))
    for change in applied_changes:
        if change.get('type') == 'combat.death_save' and isinstance(change.get('roll'), int):
            name = _text(change.get('participantName'))
            result = _humanize(change.get('result')) or 'rolled'
            who = f'{name}: death save' if party and name else 'Death save'
            beats.append({'kind': 'death_save', 'text': f"{who}: {change['roll']} — {result}"})
    end_reason = next(
        (_text(change.get('endReason')).lower() for change in applied_changes if change.get('type') == 'combat.end'),
        '',
    )
    if end_reason in COMBAT_END_TEXT:
        beats.append({'kind': 'combat_end', 'text': COMBAT_END_TEXT[end_reason]})

    picked_up = set(before.get('sceneItems') or []) - set(after.get('sceneItems') or [])
    dropped = set(after.get('sceneItems') or []) - set(before.get('sceneItems') or [])
    sold = {_text(change.get('itemName')) for change in applied_changes if change.get('type') == 'shop.sell'}
    given = {_text(change.get('itemName')) for change in applied_changes if change.get('type') == 'inventory.transfer'}
    # Alone, only the acting hero's things are told; in party play every hero's.
    heroes_told = pairs if party else [pair for pair in pairs if pair[0] == actor_id]
    for hero_id, old, new in heroes_told:
        old_items, new_items = Counter(old['items']), Counter(new['items'])
        for name, count in (new_items - old_items).items():
            verb = 'Taken' if hero_id == actor_id and name in picked_up else 'Received'
            beats.append(_item_beat('item_gained', verb, name, count, new, party=party))
        for name, count in (old_items - new_items).items():
            if name in dropped:
                continue
            verb = 'Sold' if name in sold else 'Given' if name in given else 'Used'
            beats.append(_item_beat('item_lost', verb, name, count, new, party=party))
    for code in COIN_ORDER:
        deltas = {
            hero_id: _int(new['currency'].get(code)) - _int(old['currency'].get(code)) for hero_id, old, new in heroes_told
        }
        beats.extend(_shared_gains('coins', deltas, heroes_after, lambda amount, code=code: _coins(code, amount), party=party))

    for clue_id, title in (after.get('clues') or {}).items():
        if clue_id not in (before.get('clues') or {}):
            beats.append({'kind': 'clue', 'text': f'Clue — {title}', 'refId': clue_id})
    old_objectives = before.get('objectives') or {}
    for objective_id, objective in (after.get('objectives') or {}).items():
        if objective['status'] == 'completed' and (old_objectives.get(objective_id) or {}).get('status') != 'completed':
            beats.append({'kind': 'objective_complete', 'text': f"Objective complete — {objective['title']}", 'refId': objective_id})
    for objective_id, objective in (after.get('objectives') or {}).items():
        if objective['status'] != 'completed' and objective_id not in old_objectives:
            beats.append({'kind': 'objective_new', 'text': f"New objective — {objective['title']}", 'refId': objective_id})
    for place_id, name in (after.get('places') or {}).items():
        if place_id not in (before.get('places') or {}):
            beats.append({'kind': 'place', 'text': f'New place on the map — {name}', 'refId': place_id})
    # One beat per person the turn's changes met (``metNpcIds``), by the pack catalog's name.
    pack = state_before.get('campaignPack') if isinstance(state_before.get('campaignPack'), dict) else {}
    catalog = pack.get('catalog') if isinstance(pack.get('catalog'), dict) else {}
    people = {_text(npc.get('id')): _text(npc.get('name')) for npc in _dicts(catalog.get('npcs'))}
    met = dict.fromkeys(_text(npc_id) for change in applied_changes for npc_id in change.get('metNpcIds') or [])
    beats.extend({'kind': 'met', 'text': f'Met — {people[npc_id]}', 'refId': npc_id} for npc_id in met if people.get(npc_id))
    xp = {hero_id: max(0, new['xp'] - old['xp']) for hero_id, old, new in heroes_told}
    beats.extend(_shared_gains('xp', xp, heroes_after, lambda amount: f'+{amount} XP', party=party))
    for _hero_id, old, new in heroes_told:
        if new['level'] > old['level']:
            text = f"{new['name'] or 'Someone'} reached level {new['level']}" if party else f"Level {new['level']} reached"
            beats.append({'kind': 'level', 'text': text})
    if any(change.get('type') == 'combat.defeat_recovery' for change in applied_changes):
        beats.append({'kind': 'defeat', 'text': 'Defeated — you wake later with 1 HP'})
    elif any(change.get('type') == 'party.wake' for change in applied_changes):
        beats.append({'kind': 'defeat', 'text': 'You come to — 1 HP'})
    if after.get('ending') and after.get('ending') != before.get('ending'):
        beats.append({'kind': 'ending', 'text': f"The story ends — {after['ending']}"})
    return beats


def _hint_already_shown(turn: DmTurn) -> bool:
    return (
        DmTurn.query.filter(
            DmTurn.session_id == turn.session_id,
            DmTurn.turn_id != turn.turn_id,
            DmTurn.metadata_json.contains(f'"{HINT_SHOWN_KEY}"'),
        ).first()
        is not None
    )


def record_turn_story_beats(turn: DmTurn, session_obj: Session) -> list[dict[str, Any]]:
    """Compute the turn's beats and write them on the turn, its Dungeon Master log entry and its turn event."""

    metadata = safe_json_loads(turn.metadata_json, {})
    metadata = metadata if isinstance(metadata, dict) else {}
    pipeline = metadata.get(STATE_PIPELINE_METADATA_KEY) if isinstance(metadata.get(STATE_PIPELINE_METADATA_KEY), dict) else {}
    before = metadata.get(BEAT_BASIS_KEY)
    if not isinstance(before, dict):
        return []
    actor_id = _text(pipeline.get('actorId'))
    state = safe_json_loads(session_obj.state_snapshot, {})
    state = state if isinstance(state, dict) else {}
    applied = [
        change
        for key in ('immediateAppliedChanges', 'combatAppliedChanges', 'postAppliedChanges')
        for change in _dicts(pipeline.get(key))
    ]
    beats = story_beats(
        before,
        beat_basis(state, actor_id=actor_id),
        actor_id=actor_id,
        state_before=pipeline.get('stateBeforeDm') if isinstance(pipeline.get('stateBeforeDm'), dict) else {},
        applied_changes=applied,
        authoritative_roll=metadata.get('authoritative_roll') if isinstance(metadata.get('authoritative_roll'), dict) else None,
        combat_action=(pipeline.get('combatDebug') or {}).get('combatActionResolution') if isinstance(pipeline.get('combatDebug'), dict) else None,
    )
    narration = (pipeline.get('dmContextPacket') or {}).get('narration') if isinstance(pipeline.get('dmContextPacket'), dict) else {}
    unmatched = isinstance(narration, dict) and (narration.get('freeText') or {}).get('unmatched') is True
    if unmatched and _text(turn.llm_provider) == SCRIPTED_NARRATOR_PROVIDER and not _hint_already_shown(turn):
        beats.append({'kind': 'hint', 'text': HINT_TEXT})
        metadata[HINT_SHOWN_KEY] = True
    metadata[STORY_BEATS_KEY] = beats
    turn.metadata_json = safe_json_dumps(metadata, {})
    event = TurnEvent.query.filter_by(turn_id=turn.turn_id, event_type=DM_RESPONSE_EVENT).order_by(TurnEvent.event_id.desc()).first()
    if event is not None:
        payload = safe_json_loads(event.payload_json, {})
        if isinstance(payload, dict) and isinstance(payload.get('metadata'), dict):
            payload['metadata'][STORY_BEATS_KEY] = beats
            event.payload_json = safe_json_dumps(payload, {})
    for entry in (
        SessionLogEntry.query.filter_by(session_id=turn.session_id, entry_type='dm')
        .order_by(SessionLogEntry.id.desc())
        .limit(20)
    ):
        entry_metadata = safe_json_loads(entry.metadata_json, {})
        if isinstance(entry_metadata, dict) and entry_metadata.get('turn_id') == turn.turn_id and entry.message.startswith('DM: '):
            entry_metadata[STORY_BEATS_KEY] = beats
            entry.metadata_json = safe_json_dumps(entry_metadata, {})
            break
    db.session.flush()
    return beats
