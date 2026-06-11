from __future__ import annotations

from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.game_state.models import stable_change_id


COMBAT_END_REASONS = {
    'all_enemies_defeated',
    'enemies_fled',
    'enemies_surrendered',
    'players_fled',
    'negotiated_resolution',
    'objective_completed',
    'objective_failed',
    'interrupted',
}


def _conditions(participant: dict[str, Any]) -> set[str]:
    return {str(item or '').strip().lower().replace(' ', '_') for item in participant.get('conditions') or []}


def _hp_current(participant: dict[str, Any]) -> int:
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    return max(0, int_or_default(hp.get('current'), default=0))


def is_defeated(participant: dict[str, Any]) -> bool:
    return participant.get('isAlive') is False or participant.get('isConscious') is False or _hp_current(participant) <= 0


def has_fled(participant: dict[str, Any]) -> bool:
    return bool(_conditions(participant) & {'fled', 'escaped', 'retreated', 'withdrawn'})


def has_surrendered(participant: dict[str, Any]) -> bool:
    return bool(_conditions(participant) & {'surrendered', 'yielded', 'disarmed'})


def negotiated(participant: dict[str, Any]) -> bool:
    return bool(_conditions(participant) & {'negotiated', 'parley', 'parleying'})


def _team(combat_state: dict[str, Any], team: str) -> list[dict[str, Any]]:
    return [
        participant
        for participant in (combat_state.get('participants') or [])
        if isinstance(participant, dict) and participant.get('team') == team
    ]


def check_combat_end(combat_state: dict[str, Any]) -> str | None:
    status = str(combat_state.get('status') or '')
    if status not in {'starting', 'active'}:
        return None
    enemies = _team(combat_state, 'enemy')
    players = _team(combat_state, 'player')
    if enemies and all(has_fled(enemy) or is_defeated(enemy) for enemy in enemies) and any(has_fled(enemy) for enemy in enemies):
        return 'enemies_fled'
    if enemies and all(has_surrendered(enemy) or is_defeated(enemy) for enemy in enemies) and any(has_surrendered(enemy) for enemy in enemies):
        return 'enemies_surrendered'
    if enemies and all(negotiated(enemy) or has_surrendered(enemy) or is_defeated(enemy) for enemy in enemies) and any(negotiated(enemy) for enemy in enemies):
        return 'negotiated_resolution'
    if enemies and all(is_defeated(enemy) for enemy in enemies):
        return 'all_enemies_defeated'
    if players and all(is_defeated(player) or has_fled(player) for player in players):
        return 'objective_failed'
    flags = combat_state.get('flags') if isinstance(combat_state.get('flags'), dict) else {}
    objective_status = str(flags.get('objectiveStatus') or flags.get('objective_status') or '').strip().lower()
    if objective_status in {'completed', 'success', 'succeeded'}:
        return 'objective_completed'
    if objective_status in {'failed', 'failure'}:
        return 'objective_failed'
    return None


def combat_end_summary(reason: str) -> str:
    return {
        'all_enemies_defeated': 'Combat ends because all enemies are defeated.',
        'enemies_fled': 'Combat ends because the remaining enemies fled.',
        'enemies_surrendered': 'Combat ends because the remaining enemies surrendered.',
        'players_fled': 'Combat ends because the players fled.',
        'negotiated_resolution': 'Combat ends through negotiation.',
        'objective_completed': 'Combat ends because the encounter objective was completed.',
        'objective_failed': 'Combat ends because the encounter objective failed.',
        'interrupted': 'Combat was interrupted.',
    }.get(reason, 'Combat ends.')


def combat_end_change(turn_id: int | str, reason: str) -> dict[str, Any]:
    normalized = reason if reason in COMBAT_END_REASONS else 'interrupted'
    return {
        'id': stable_change_id(turn_id, 'combat.end', normalized),
        'turnId': turn_id if isinstance(turn_id, int) else None,
        'type': 'combat.end',
        'status': 'ended',
        'endReason': normalized,
        'summary': combat_end_summary(normalized),
        'reason': combat_end_summary(normalized),
        'visible': False,
    }
