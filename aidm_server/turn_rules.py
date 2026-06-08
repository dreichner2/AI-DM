"""Turn rule helpers shared by socket and non-socket turn processors."""

from __future__ import annotations

import re

from aidm_server.models import DmTurn, safe_json_loads
from aidm_server.rules import RuleHint


ROLL_TYPE_LABELS = {
    'attack': 'an Attack roll',
    'stealth': 'a Dexterity (Stealth) check',
    'social': 'a Charisma (Persuasion/Deception) check',
    'lore': 'an Intelligence (Investigation/Arcana) check',
    'athletics': 'a Strength (Athletics) check',
    'thieves_tools': "a Dexterity (Thieves' Tools) check",
    'mobility': 'a Dexterity (Acrobatics) or Strength (Athletics) check',
    'check': 'an appropriate ability check',
}


ROLL_REQUEST_PATTERNS = [
    re.compile(r'\bplease\s+roll\b', re.IGNORECASE),
    re.compile(r'\broll\s+(?:a\s+)?d20\b', re.IGNORECASE),
    re.compile(r'\bmake\s+(?:an?\s+)?[a-z][a-z \'-]{0,40}\s+check\b', re.IGNORECASE),
    re.compile(r'\bwhat\s+did\s+you\s+roll\b', re.IGNORECASE),
    re.compile(r'\broll\s+for\b', re.IGNORECASE),
]


def _metadata(turn: DmTurn | None) -> dict:
    if not turn:
        return {}
    metadata = safe_json_loads(turn.metadata_json, {})
    return metadata if isinstance(metadata, dict) else {}


def roll_gate(turn: DmTurn | None) -> dict:
    gate = _metadata(turn).get('roll_gate')
    return gate if isinstance(gate, dict) else {}


def _int_ids(value) -> list[int]:
    if not isinstance(value, list):
        return []
    ids: list[int] = []
    for item in value:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in ids:
            ids.append(parsed)
    return ids


def pending_turn_required_player_ids(turn: DmTurn | None) -> list[int]:
    if not turn:
        return []
    gate_ids = _int_ids(roll_gate(turn).get('required_player_ids'))
    if gate_ids:
        return gate_ids
    return [turn.player_id] if turn.player_id else []


def pending_turn_resolved_player_ids(turn: DmTurn | None) -> list[int]:
    return _int_ids(roll_gate(turn).get('resolved_player_ids'))


def pending_turn_remaining_player_ids(turn: DmTurn | None) -> list[int]:
    resolved = set(pending_turn_resolved_player_ids(turn))
    return [player_id for player_id in pending_turn_required_player_ids(turn) if player_id not in resolved]


def pending_turn_waits_for_player(turn: DmTurn | None, player_id: int | None) -> bool:
    if not turn or player_id is None or turn.outcome_status != 'deferred':
        return False
    return int(player_id) in pending_turn_remaining_player_ids(turn)


def latest_pending_turn(session_id: int, player_id: int | None = None) -> DmTurn | None:
    query = DmTurn.query.filter_by(session_id=session_id, outcome_status='deferred').order_by(DmTurn.turn_id.desc())
    if player_id is None:
        return query.first()
    for turn in query.limit(20).all():
        if pending_turn_waits_for_player(turn, player_id):
            return turn
    return None


def pending_turn_by_id(session_id: int, player_id: int, pending_turn_id: int | None) -> DmTurn | None:
    if pending_turn_id is None:
        return None
    turn = DmTurn.query.filter_by(
        session_id=session_id,
        turn_id=pending_turn_id,
        outcome_status='deferred',
    ).first()
    if not pending_turn_waits_for_player(turn, player_id):
        return None
    return turn


def dc_hint_from_turn(turn: DmTurn | None) -> str | None:
    if not turn:
        return None
    rules_hint = safe_json_loads(turn.rules_hint, {})
    if not isinstance(rules_hint, dict):
        return None
    dc_hint = rules_hint.get('dc_hint')
    if not dc_hint:
        return None
    return str(dc_hint)


def apply_pending_resolution_hint(
    session_id: int,
    player_id: int,
    rule_hint: RuleHint,
    target_pending_turn_id: int | None = None,
) -> tuple[DmTurn | None, int | None]:
    if rule_hint.roll_value is None:
        return None, None

    pending_turn = (
        pending_turn_by_id(session_id, player_id, target_pending_turn_id)
        if target_pending_turn_id is not None
        else latest_pending_turn(session_id, player_id)
    )
    if not pending_turn:
        return None, None

    pending_rule_type = pending_turn.rule_type or 'check'
    pending_dc_hint = dc_hint_from_turn(pending_turn)

    rule_hint.requires_roll = True
    rule_hint.outcome_deferred = False
    if rule_hint.roll_type in (None, 'check'):
        rule_hint.roll_type = pending_rule_type
    if not rule_hint.dc_hint and pending_dc_hint:
        rule_hint.dc_hint = pending_dc_hint
    rule_hint.reason = f'Resolved pending {pending_rule_type} from turn {pending_turn.turn_id}'
    pending_confidence = pending_turn.confidence if pending_turn.confidence is not None else 0.8
    rule_hint.confidence = max(rule_hint.confidence, pending_confidence)

    return pending_turn, pending_turn.turn_id


def build_roll_prompt(rule_hint: RuleHint, pending_turn_id: int | None = None) -> str:
    roll_label = ROLL_TYPE_LABELS.get(rule_hint.roll_type or 'check', 'an appropriate ability check')
    dc_hint = f" (DC {rule_hint.dc_hint})" if rule_hint.dc_hint else ''
    pending_prefix = f'Resolve pending turn {pending_turn_id}: ' if pending_turn_id else ''
    return (
        f'{pending_prefix}Please roll {roll_label}{dc_hint} and send the result '
        '(example: "I roll a d20: 14").'
    )


def response_mentions_roll_request(text: str) -> bool:
    candidate = text or ''
    return any(pattern.search(candidate) for pattern in ROLL_REQUEST_PATTERNS)
