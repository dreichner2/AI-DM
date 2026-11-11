"""The finishing blow: the hero who fells a foe that matters tells how it ends.

When the acting hero's own attack, spell or manoeuvre brings an enemy to 0 hit
points before narration, and that enemy is the last one standing in the fight
or its pack record is ``proper`` or of a ``hard``, ``deadly`` or ``boss``
``challengeTier``, the fight resolves as it always does (defeat, victory,
rewards, beats) but the death is not told. That turn's narration ends on
:func:`closing` (``The ashmarked cutthroat is finished. How do you end it?``)
and the combat's flags record ``pendingFinisher``: ``heroId``, ``heroName``,
``enemyId``, ``enemyName``, ``turnId``, ``othersActed`` and the ``waitingLine``
other players see in place of their own prompt line. There is never more than
one; a second qualifying defeat is told as any other.

While it is pending, that hero is offered :func:`finisher_actions` first, and
any words they send are the finisher (``turn_rules.finishing``): no other
action, no roll, no cost. Words that ask for mercy (:data:`MERCY`) spare the
enemy, who becomes a captive (alive, ``captured``, held in the scene like the
enemies a fight leaves captured). The finisher lapses, and the narration adds
``{Enemy} falls.``, when the hero lets the story tell it, takes any other
action, the party travels, or another hero has acted twice since.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from aidm_server.combat.maneuvers import captives_from_combat, enemy_record
from aidm_server.combat.state import participant_is_targetable
from aidm_server.game_state.campaign_pack_encounters import catalog_record
from aidm_server.game_state.models import stable_change_id
from aidm_server.game_state.orchestration.narration_facts import enemy_prose, party
from aidm_server.turn_rules import FINISHER_DESCRIBE, FINISHER_STORY, fight_speech, finishing

PENDING_KEY = 'pendingFinisher'
QUALIFYING_TIERS = frozenset({'hard', 'deadly', 'boss'})
DESCRIBE_LABEL = 'Describe how you end it'
STORY_LABEL = 'Let the story tell it'
DESCRIBE_ACTION_ID = 'finisher.describe'
STORY_ACTION_ID = 'finisher.story'
PROMPT = 'How do you end it?'
# Another hero's actions that let the moment pass.
LAPSE_AFTER_OTHERS = 2
# Words that spare the enemy, as whole words.
MERCY = re.compile(
    r"\b(?:spare|mercy|alive|let (?:him|her|it) live|knock (?:(?:him|her) )?out|stay my hand)\b",
    re.IGNORECASE,
)
FREE_KINDS = frozenset({'ooc', 'admin'})


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _combat(state: dict[str, Any]) -> dict[str, Any]:
    return state.get('combat') if isinstance(state.get('combat'), dict) else {}


def _hp(participant: dict[str, Any]) -> int:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    try:
        return int(hp.get('current'))
    except (TypeError, ValueError):
        return 1


def pending_finisher(state: dict[str, Any]) -> dict[str, Any] | None:
    """The finishing blow a hero has yet to tell, or None."""

    flags = _combat(state).get('flags') if isinstance(_combat(state).get('flags'), dict) else {}
    pending = flags.get(PENDING_KEY)
    return pending if isinstance(pending, dict) and _text(pending.get('heroId')) else None


def finisher_actions(state: dict[str, Any], actor_id: str) -> list[dict[str, Any]]:
    """The two actions offered first to the hero whose finishing blow is pending; [] for anyone else."""

    pending = pending_finisher(state)
    if pending is None or _text(pending.get('heroId')) != actor_id:
        return []
    return [
        {'id': DESCRIBE_ACTION_ID, 'kind': 'finisher', 'label': DESCRIBE_LABEL, 'prompt': PROMPT},
        {'id': STORY_ACTION_ID, 'kind': 'finisher', 'label': STORY_LABEL},
    ]


def lets_the_story_tell_it(message: str) -> bool:
    """The hero's words are the offered "Let the story tell it", typed or sent by the action."""

    return ' '.join(re.sub(r'[^\w\s]', ' ', message.lower()).split()) == STORY_LABEL.lower()


def spares(words: str) -> bool:
    return bool(MERCY.search(words or ''))


def closing(state: dict[str, Any], *, hero_id: str, enemy: dict[str, Any]) -> str:
    """``The ashmarked cutthroat is finished. How do you end it?``; in party play the hero is named."""

    prose = enemy_prose(state, enemy)
    finished = f'{prose[:1].upper()}{prose[1:]} is finished.'
    heroes = party(state, actor_id=hero_id)
    return f"{finished} {heroes['actor']}, how do you end it?" if heroes else f'{finished} {PROMPT}'


def _qualifies(state: dict[str, Any], enemy: dict[str, Any], combat: dict[str, Any]) -> bool:
    record = enemy_record(state, enemy)
    tier = _text(record.get('challengeTier') or enemy.get('challengeTier')).lower()
    if record.get('proper') is True or enemy.get('proper') is True or tier in QUALIFYING_TIERS:
        return True
    return not any(
        participant_is_targetable(other)
        for other in _dicts(combat.get('participants'))
        if _text(other.get('team')).lower() == 'enemy' and _text(other.get('id')) != _text(enemy.get('id'))
    )


def _felled(state_before: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    """Enemies standing when the turn began and at 0 hit points now, in fight order."""

    standing = {
        _text(participant.get('id'))
        for participant in _dicts(_combat(state_before).get('participants'))
        if _text(participant.get('team')).lower() == 'enemy' and _hp(participant) > 0
    }
    return [
        participant
        for participant in _dicts(_combat(state).get('participants'))
        if _text(participant.get('id')) in standing and _hp(participant) <= 0
    ]


def _flags_change(turn_id: Any, value: dict[str, Any] | None, label: str) -> dict[str, Any]:
    return {
        'id': stable_change_id(turn_id, 'finisher', label),
        'turnId': turn_id,
        'type': 'combat.update',
        'flags': {PENDING_KEY: value},
        'reason': 'The finishing blow.',
        'visible': False,
    }


def _spare_change(turn_id: Any, enemy: dict[str, Any]) -> dict[str, Any]:
    hp = enemy.get('hp') if isinstance(enemy.get('hp'), dict) else {}
    conditions = [condition for condition in enemy.get('conditions') or [] if _text(condition).lower() not in {'dead', 'defeated', 'unconscious'}]
    return {
        'id': stable_change_id(turn_id, 'finisher', 'spared', enemy.get('id')),
        'turnId': turn_id,
        'type': 'combat.participant.update',
        'participantId': _text(enemy.get('id')),
        'hp': {'current': 1, 'max': hp.get('max') or 1, 'temp': 0},
        'conditions': [*conditions, 'captured'],
        'isConscious': True,
        'reason': 'Spared at the finishing blow: alive and captured.',
        'visible': False,
    }


def finishing_blow(
    *,
    state_before: dict[str, Any],
    state: dict[str, Any],
    actor_id: str,
    turn_id: Any,
    action_intent: dict[str, Any] | None,
    applied_changes: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The turn's finishing-blow changes and its narration facts.

    ``state_before`` is the session state as the turn began, ``state`` the state after the hero's own
    action. Facts: ``finisher`` (the words told this turn: ``enemyId``, ``enemy``, ``words``, ``spared``),
    ``finisherLapsed`` (``enemyId``, ``enemy``) and ``finishingBlow`` (set this turn: ``enemyId``,
    ``enemy``, ``hero``, ``closing``).
    """

    changes: list[dict[str, Any]] = []
    facts: dict[str, Any] = {}
    pending = pending_finisher(state_before)
    choice = finishing(action_intent)
    kind = _text((action_intent or {}).get('kind')).lower() if isinstance(action_intent, dict) else ''
    if pending is not None:
        enemy = {'enemyId': _text(pending.get('enemyId')), 'enemy': _text(pending.get('enemyName'))}
        mine = _text(pending.get('heroId')) == actor_id
        travelled = any(isinstance(change, dict) and change.get('type') == 'scene.move_location' for change in applied_changes)
        free = kind in FREE_KINDS or fight_speech(action_intent)
        others = int(pending.get('othersActed') or 0) + (0 if mine or free else 1)
        if mine and choice == FINISHER_DESCRIBE:
            words = _text((action_intent or {}).get('text'))
            facts['finisher'] = {**enemy, 'words': words, 'spared': spares(words)}
            changes.append(_flags_change(turn_id, None, 'told'))
            if facts['finisher']['spared']:
                target = next(
                    (participant for participant in _dicts(_combat(state).get('participants')) if _text(participant.get('id')) == enemy['enemyId']),
                    None,
                )
                if target is not None:
                    changes.append(_spare_change(turn_id, target))
        elif (mine and (choice == FINISHER_STORY or not free)) or travelled or others >= LAPSE_AFTER_OTHERS:
            facts['finisherLapsed'] = enemy
            changes.append(_flags_change(turn_id, None, 'lapsed'))
        elif others != int(pending.get('othersActed') or 0):
            changes.append(_flags_change(turn_id, {**pending, 'othersActed': others}, f'others:{others}'))
            return changes, facts
        else:
            return changes, facts
    if choice or kind in FREE_KINDS or fight_speech(action_intent):
        return changes, facts
    combat = _combat(state)
    felled = next((enemy for enemy in _felled(state_before, state) if _qualifies(state, enemy, combat)), None)
    if felled is None:
        return changes, facts
    heroes = party(state, actor_id=actor_id)
    hero_name = heroes['actor'] if heroes else next(
        (_text(hero.get('name')) for hero in _dicts(state.get('playerCharacters')) if _text(hero.get('id')) == actor_id),
        'The hero',
    )
    record = {
        'heroId': actor_id,
        'heroName': hero_name,
        'enemyId': _text(felled.get('id')),
        'enemyName': _text(felled.get('name')),
        'turnId': turn_id,
        'othersActed': 0,
        'waitingLine': f'{hero_name} is deciding how it ends…',
    }
    changes.append(_flags_change(turn_id, record, 'set'))
    facts['finishingBlow'] = {
        'enemyId': record['enemyId'],
        'enemy': record['enemyName'],
        'hero': hero_name,
        'closing': closing(state, hero_id=actor_id, enemy=felled),
    }
    return changes, facts


def hold_spared_captive(state: dict[str, Any], enemy_id: str) -> list[dict[str, Any]]:
    """A spared enemy of a fight already over joins the scene's captives (a fight still going holds it
    when it ends). Returns the captives added."""

    combat = _combat(state)
    if _text(combat.get('status')).lower() in {'starting', 'active'}:
        return []
    added = [
        captive
        for captive in captives_from_combat(
            combat,
            has_captive_lines=lambda record_id: bool(record_id and catalog_record(state, 'enemies', record_id).get('captiveKnows')),
        )
        if captive['id'] == enemy_id
    ]
    if not added:
        return []
    scene = state.setdefault('currentScene', {})
    held = {_text(captive.get('id')) for captive in _dicts(scene.get('captives'))}
    scene['captives'] = [*_dicts(scene.get('captives')), *(captive for captive in added if captive['id'] not in held)]
    return added
