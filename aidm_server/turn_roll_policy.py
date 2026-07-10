from __future__ import annotations

import re
from typing import Protocol

from aidm_server.models import safe_json_loads


_NO_ROLL_NEEDED_RE = re.compile(
    r"\bno\s+(?:a\s+)?(?:roll|check)s?\s+(?:(?:is|are|was|were)\s+)?(?:needed|required|necessary)\b|"
    r"\b(?:roll|check)s?\s+(?:is|are|was|were)\s+not\s+(?:needed|required|necessary)\b|"
    r"\bwithout\s+(?:requiring\s+)?(?:a\s+)?(?:roll|check)\b|"
    r"\b(?:doesn't|does not|don't|do not)\s+(?:need|require)\s+(?:a\s+)?(?:roll|check)\b|"
    r"\b(?:don't|do not)\s+roll\b",
    re.IGNORECASE,
)
_GROUP_ROLL_MARKER_RE = re.compile(
    r'\b(?:both of you|you both|all of you|everyone|each of you|every player|all players|the party)\b',
    re.IGNORECASE,
)
_GROUP_ROLL_REQUEST_RE = re.compile(
    r'\b(?:please\s+)?roll\b|'
    r'\b(?:must|should|need(?:s)? to|have to|has to)\s+(?:all\s+)?(?:roll|make)\b|'
    r'\bmake\s+(?:an?\s+)?[a-z][a-z \'-]{0,60}\s+(?:check|saving\s+throw|save)\b|'
    r'\bsaving\s+throw\b|'
    r'\binitiative\b',
    re.IGNORECASE,
)


class RollGateTurn(Protocol):
    requires_roll: bool
    outcome_status: str | None
    roll_value: int | None
    rule_type: str | None
    rules_hint: str | None
    player_id: int | None


class TurnRollPolicy:
    """Pure DM-response parsing and roll-gate construction policy."""

    @staticmethod
    def response_sentences(text: str) -> list[str]:
        return [
            chunk.strip()
            for chunk in re.split(r'(?<=[.!?;])\s+|\n+', text or '')
            if chunk.strip()
        ]

    @classmethod
    def response_explains_no_roll_needed(cls, text: str) -> bool:
        return any(_NO_ROLL_NEEDED_RE.search(sentence) for sentence in cls.response_sentences(text))

    @classmethod
    def response_requests_group_roll(cls, text: str) -> bool:
        for sentence in cls.response_sentences(text):
            if _NO_ROLL_NEEDED_RE.search(sentence):
                continue
            if _GROUP_ROLL_MARKER_RE.search(sentence) and _GROUP_ROLL_REQUEST_RE.search(sentence):
                return True
        return False

    @staticmethod
    def roll_type_from_response(text: str, fallback: str | None = None) -> str:
        candidate = (text or '').lower()
        if re.search(r'\binitiative\b', candidate):
            return 'initiative'
        if re.search(
            r'\bspell\b|\bmagic\b|\bcast\b|\bconjure\b|\bsummon\b|\bsorcery\b|'
            r'\bsorcerous\b|\btelekinesis\b|\blevitat\w*\b|\bwild magic\b',
            candidate,
        ):
            return 'spell'
        if re.search(r'\battack\b|\bweapon\b', candidate):
            return 'attack'
        if re.search(r'\bstealth\b|\bsneak\b|\bhide\b', candidate):
            return 'stealth'
        if re.search(r'\bpersuasion\b|\bdeception\b|\bintimidation\b|\bcharisma\b', candidate):
            return 'social'
        if re.search(r'\binvestigation\b|\barcana\b|\bhistory\b|\bintelligence\b', candidate):
            return 'lore'
        if re.search(r'\bathletics\b|\bstrength\b', candidate):
            return 'athletics'
        if re.search(r'\bacrobatics\b|\bdexterity\b', candidate):
            return 'mobility'
        return fallback or 'check'

    @classmethod
    def build_roll_gate(
        cls,
        *,
        turn: RollGateTurn,
        dm_response_text: str,
        response_requests_roll: bool,
        group_player_ids: list[int],
    ) -> dict | None:
        if not ((turn.requires_roll and turn.outcome_status == 'deferred') or response_requests_roll):
            return None
        if turn.roll_value is not None:
            return None

        roll_type = cls.roll_type_from_response(dm_response_text, turn.rule_type)
        rules_hint = safe_json_loads(turn.rules_hint, {})
        rules_hint = rules_hint if isinstance(rules_hint, dict) else {}
        pvp_payload = rules_hint.get('pvp') if isinstance(rules_hint.get('pvp'), dict) else {}
        try:
            pvp_target_player_id = int(pvp_payload.get('target_player_id')) if pvp_payload else None
        except (TypeError, ValueError):
            pvp_target_player_id = None
        if pvp_target_player_id is not None and pvp_target_player_id <= 0:
            pvp_target_player_id = None

        if pvp_target_player_id:
            required_player_ids = list(
                dict.fromkeys(
                    player_id
                    for player_id in (turn.player_id, pvp_target_player_id)
                    if player_id
                )
            )
            resolved_player_ids = [turn.player_id] if turn.roll_value is not None and turn.player_id else []
            remaining_player_ids = [
                player_id
                for player_id in required_player_ids
                if player_id not in set(resolved_player_ids)
            ]
            return {
                'scope': 'pvp_contest',
                'rule_type': roll_type,
                'required_player_ids': required_player_ids,
                'resolved_player_ids': resolved_player_ids,
                'remaining_player_ids': remaining_player_ids,
                'target_player_id': pvp_target_player_id,
            }

        required_player_ids = [turn.player_id] if turn.player_id else []
        scope = 'single_player'
        if cls.response_requests_group_roll(dm_response_text) and len(group_player_ids) > 1:
            required_player_ids = list(dict.fromkeys(group_player_ids))
            scope = 'group'
        if not required_player_ids:
            return None
        return {
            'scope': scope,
            'rule_type': roll_type,
            'required_player_ids': required_player_ids,
            'resolved_player_ids': [],
            'remaining_player_ids': required_player_ids,
        }
