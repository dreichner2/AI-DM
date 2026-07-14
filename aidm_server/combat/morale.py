from __future__ import annotations

from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.combat.state import participant_can_take_turn, participant_is_targetable


MORALE_EVENTS = {
    'took_heavy_damage',
    'leader_died',
    'ally_died',
    'outnumbered',
    'bloodied_player',
    'enemy_reinforcements_arrived',
    'party_used_fire',
    'boss_entered',
    'escape_route_blocked',
}


def clamp_morale(value: int) -> int:
    return max(0, min(100, int(value)))


def _behavior(enemy: dict[str, Any]) -> dict[str, Any]:
    return enemy.get('behavior') if isinstance(enemy.get('behavior'), dict) else {}


def _hp_percent(participant: dict[str, Any]) -> int:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    current = max(0, int_or_default(hp.get('current'), default=0))
    maximum = max(1, int_or_default(hp.get('max'), default=1))
    return round((current / maximum) * 100)


def _base_morale(enemy: dict[str, Any]) -> int:
    behavior = _behavior(enemy)
    return clamp_morale(int_or_default(enemy.get('morale'), default=int_or_default(behavior.get('morale'), default=50)))


def apply_morale_event(enemy: dict[str, Any], event: str, *, current_morale: int | None = None) -> int:
    behavior = _behavior(enemy)
    morale = _base_morale(enemy) if current_morale is None else clamp_morale(current_morale)
    event = str(event or '').strip().lower()
    if event not in MORALE_EVENTS:
        return morale

    self_preservation = int_or_default(behavior.get('selfPreservation'), default=50)
    loyalty = int_or_default(behavior.get('loyalty'), default=40)
    aggression = int_or_default(behavior.get('aggression'), default=50)
    discipline = int_or_default(behavior.get('discipline'), default=50)
    tags = {str(tag or '').strip().lower() for tag in behavior.get('personalityTags') or []}
    intelligence = str(behavior.get('intelligenceProfile') or 'average')
    if intelligence == 'mindless':
        return 100

    delta = 0
    if event == 'took_heavy_damage':
        delta -= 15 if self_preservation > 60 else 5
    elif event == 'leader_died':
        delta -= 20 if loyalty > 70 else 30
    elif event == 'ally_died':
        delta -= max(4, 12 - discipline // 10)
    elif event == 'outnumbered':
        delta -= 10 if self_preservation >= 40 else 4
    elif event == 'bloodied_player':
        delta += 10 if aggression > 50 else 5
    elif event == 'enemy_reinforcements_arrived':
        delta += 20
    elif event == 'party_used_fire':
        delta -= 15 if 'afraid_of_fire' in tags or 'cowardly' in tags else 5
    elif event == 'boss_entered':
        delta += 12 if loyalty >= 50 else 6
    elif event == 'escape_route_blocked':
        delta -= 20 if self_preservation > 60 else 10

    if 'fanatical' in tags and delta < 0:
        delta = round(delta * 0.45)
    if 'cowardly' in tags and delta < 0:
        delta = round(delta * 1.25)
    return clamp_morale(morale + delta)


def living_participants(combat: dict[str, Any], team: str | None = None) -> list[dict[str, Any]]:
    """Return physically present, conscious participants that remain valid targets."""

    participants = combat.get('participants') if isinstance(combat.get('participants'), list) else []
    result = []
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        if team and participant.get('team') != team:
            continue
        if not participant_is_targetable(participant):
            continue
        result.append(participant)
    return result


def actionable_participants(combat: dict[str, Any], team: str | None = None) -> list[dict[str, Any]]:
    return [
        participant
        for participant in living_participants(combat, team)
        if participant_can_take_turn(participant)
    ]


def morale_context_events(enemy: dict[str, Any], combat: dict[str, Any]) -> list[str]:
    events: list[str] = []
    hp = _hp_percent(enemy)
    if hp <= 25:
        events.append('took_heavy_damage')
    enemies = living_participants(combat, 'enemy')
    players = living_participants(combat, 'player')
    if enemies and len(enemies) < len(players):
        events.append('outnumbered')
    for participant in combat.get('participants') or []:
        if not isinstance(participant, dict) or participant.get('team') != 'enemy':
            continue
        behavior = _behavior(participant)
        if behavior.get('combatRole') == 'leader' and (participant.get('isAlive') is False or _hp_percent(participant) <= 0):
            if participant.get('id') != enemy.get('id'):
                events.append('leader_died')
            break
    return events


def recalculate_morale(enemy: dict[str, Any], combat: dict[str, Any]) -> tuple[int, list[str]]:
    morale = _base_morale(enemy)
    events = morale_context_events(enemy, combat)
    for event in events:
        morale = apply_morale_event(enemy, event, current_morale=morale)
    return morale, events
