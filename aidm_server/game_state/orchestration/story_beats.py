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
"""

from __future__ import annotations

from collections import Counter
from typing import Any

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
    """The parts of the player projection of ``state`` that story beats compare."""

    player_id = parse_actor_player_id(actor_id)
    projected = filter_session_snapshot_for_player(state, private_player_ids=frozenset({player_id} if player_id else ()))
    projected = projected if isinstance(projected, dict) else {}
    heroes = {}
    for hero in _dicts(projected.get('playerCharacters')):
        health = hero.get('health') if isinstance(hero.get('health'), dict) else {}
        inventory = hero.get('inventory') if isinstance(hero.get('inventory'), dict) else {}
        items: Counter = Counter()
        for item in _dicts(inventory.get('items')):
            if _text(item.get('name')):
                items[_text(item.get('name'))] += _int(item.get('quantity')) or 1
        xp = hero.get('xp') if isinstance(hero.get('xp'), dict) else {}
        heroes[_text(hero.get('id'))] = {
            'name': _text(hero.get('name')),
            'hp': _int(health.get('currentHp')),
            'conditions': sorted({_text(condition).lower() for condition in health.get('conditions') or [] if _text(condition)}),
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
    return {
        'heroes': heroes,
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


def _check_beat(check: dict[str, Any], *, show_dc: bool) -> dict[str, Any] | None:
    total = check.get('total')
    outcome = _text(check.get('outcome')).lower()
    if not isinstance(total, int) or outcome not in {'success', 'failure'}:
        return None
    label = _humanize(check.get('skill') or check.get('ability') or 'ability').title()
    dc = check.get('dc')
    suffix = f' (DC {dc})' if show_dc and isinstance(dc, int) else ''
    return {'kind': 'check', 'text': f'{label} check: {total} — {outcome}{suffix}'}


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


def _roll_beat(roll: dict[str, Any]) -> dict[str, Any] | None:
    rule_type = _text(roll.get('rule_type')).lower()
    if not rule_type or rule_type in {'attack', 'spell_attack', 'initiative'}:
        return None
    return _check_beat(
        {'skill': rule_type, 'total': roll.get('total'), 'outcome': roll.get('outcome'), 'dc': roll.get('dc')},
        show_dc=isinstance(roll.get('dc'), int),
    )


def _you(hero: dict[str, Any], *, acting: bool) -> tuple[str, str]:
    """Subject and verb form: ``("You", "are")`` for the acting hero, ``("Mara", "is")`` for others."""

    return ('You', 'are') if acting else (hero.get('name') or 'Someone', 'is')


def _coins(code: str, amount: int) -> str:
    return f'+{amount} {code}' if amount > 0 else f'{MINUS}{abs(amount)} {code}'


def story_beats(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    actor_id: str,
    state_before: dict[str, Any],
    applied_changes: list[dict[str, Any]],
    authoritative_roll: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The turn's beats, in contract order, from the before/after bases and its check results."""

    beats: list[dict[str, Any]] = []
    for change in applied_changes:
        event = change.get('event') if isinstance(change.get('event'), dict) else {}
        if change.get('type') == 'scene.interactable.action' and isinstance(event.get('check'), dict):
            beat = _check_beat(event['check'], show_dc=_shows_dc(state_before, change))
            if beat:
                beats.append(beat)
    if isinstance(authoritative_roll, dict):
        beat = _roll_beat(authoritative_roll)
        if beat:
            beats.append(beat)

    heroes_before = before.get('heroes') or {}
    heroes_after = after.get('heroes') or {}
    ordered = [actor_id, *sorted(hero_id for hero_id in heroes_after if hero_id != actor_id)]
    for hero_id in ordered:
        old, new = heroes_before.get(hero_id), heroes_after.get(hero_id)
        if not old or not new:
            continue
        acting = hero_id == actor_id
        subject, _verb = _you(new, acting=acting)
        delta = new['hp'] - old['hp']
        if delta:
            text = f'Took {-delta} damage' if delta < 0 else f'Healed {delta}'
            beats.append({'kind': 'hp', 'text': text if acting else f"{subject} {text[0].lower()}{text[1:]}"})
    for hero_id in ordered:
        old, new = heroes_before.get(hero_id), heroes_after.get(hero_id)
        if not old or not new:
            continue
        subject, verb = _you(new, acting=hero_id == actor_id)
        for condition in new['conditions']:
            if condition not in old['conditions']:
                beats.append({'kind': 'condition', 'text': f'{subject} {verb} {_humanize(condition)}'})
        for condition in old['conditions']:
            if condition not in new['conditions']:
                text = f'No longer {_humanize(condition)}' if hero_id == actor_id else f'{subject} is no longer {_humanize(condition)}'
                beats.append({'kind': 'condition', 'text': text})
    for change in applied_changes:
        if change.get('type') == 'combat.death_save' and isinstance(change.get('roll'), int):
            name = _text(change.get('participantName'))
            result = _humanize(change.get('result')) or 'rolled'
            who = 'Death save' if _text(change.get('participantId')) == actor_id or not name else f'{name}: death save'
            beats.append({'kind': 'death_save', 'text': f"{who}: {change['roll']} — {result}"})
    end_reason = next(
        (_text(change.get('endReason')).lower() for change in applied_changes if change.get('type') == 'combat.end'),
        '',
    )
    if end_reason in COMBAT_END_TEXT:
        beats.append({'kind': 'combat_end', 'text': COMBAT_END_TEXT[end_reason]})

    old_hero, new_hero = heroes_before.get(actor_id) or {}, heroes_after.get(actor_id) or {}
    if old_hero and new_hero:
        old_items, new_items = Counter(old_hero['items']), Counter(new_hero['items'])
        picked_up = set(before.get('sceneItems') or []) - set(after.get('sceneItems') or [])
        dropped = set(after.get('sceneItems') or []) - set(before.get('sceneItems') or [])
        sold = {_text(change.get('itemName')) for change in applied_changes if change.get('type') == 'shop.sell'}
        given = {_text(change.get('itemName')) for change in applied_changes if change.get('type') == 'inventory.transfer'}
        for name, count in (new_items - old_items).items():
            verb = 'Taken' if name in picked_up else 'Received'
            beats.append({'kind': 'item_gained', 'text': f"{verb} — {name}{f' ×{count}' if count > 1 else ''}"})
        for name, count in (old_items - new_items).items():
            if name in dropped:
                continue
            verb = 'Sold' if name in sold else 'Given' if name in given else 'Used'
            beats.append({'kind': 'item_lost', 'text': f"{verb} — {name}{f' ×{count}' if count > 1 else ''}"})
        for code in COIN_ORDER:
            delta = _int(new_hero['currency'].get(code)) - _int(old_hero['currency'].get(code))
            if delta:
                beats.append({'kind': 'coins', 'text': _coins(code, delta)})

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
    if old_hero and new_hero:
        if new_hero['xp'] > old_hero['xp']:
            beats.append({'kind': 'xp', 'text': f"+{new_hero['xp'] - old_hero['xp']} XP"})
        if new_hero['level'] > old_hero['level']:
            beats.append({'kind': 'level', 'text': f"Level {new_hero['level']} reached"})
    if any(change.get('type') == 'combat.defeat_recovery' for change in applied_changes):
        beats.append({'kind': 'defeat', 'text': 'Defeated — you wake later with 1 HP'})
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
