from __future__ import annotations

import re
from typing import Protocol

from aidm_server.models import safe_json_dumps, safe_json_loads


MIN_STATED_DC = 5
MAX_STATED_DC = 30

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
# "Make a Wisdom (Perception) check, DC 13." is the roll-request form the DM
# prompt asks for, so ability/skill labels may carry parentheses and slashes.
_GROUP_ROLL_REQUEST_RE = re.compile(
    r'\b(?:please\s+)?roll\b|'
    r'\b(?:must|should|need(?:s)? to|have to|has to)\s+(?:all\s+)?(?:roll|make)\b|'
    r'\bmake\s+(?:an?\s+)?[a-z][a-z \'()/-]{0,60}\s+(?:check|saving\s+throw|save)\b|'
    r'\bsaving\s+throw\b|'
    r'\binitiative\b',
    re.IGNORECASE,
)
_STATED_DC_RE = re.compile(r'\bDC\s*(\d{1,2})\b', re.IGNORECASE)
_ROLL_REQUEST_CLAUSE_RE = re.compile(
    r'\b(?:please\s+)?roll\b|'
    r'\b(?:must|should|need(?:s)? to|have to|has to)\s+(?:all\s+)?(?:roll|make)\b|'
    r'\bmake\b',
    re.IGNORECASE,
)
_ABILITY_PATTERN = r'(strength|dexterity|constitution|intelligence|wisdom|charisma)'
_SAVING_THROW_RE = re.compile(
    rf'\b{_ABILITY_PATTERN}\s+(?:saving\s+throw|save)\b',
    re.IGNORECASE,
)
_ABILITY_CHECK_RE = re.compile(
    rf'\b{_ABILITY_PATTERN}\s+(?:ability\s+)?check\b',
    re.IGNORECASE,
)
_SKILL_PHRASES = {
    'acrobatics': 'acrobatics',
    'animal handling': 'animal_handling',
    'arcana': 'arcana',
    'athletics': 'athletics',
    'deception': 'deception',
    'history': 'history',
    'insight': 'insight',
    'intimidation': 'intimidation',
    'investigation': 'investigation',
    'medicine': 'medicine',
    'nature': 'nature',
    'perception': 'perception',
    'performance': 'performance',
    'persuasion': 'persuasion',
    'religion': 'religion',
    'sleight of hand': 'sleight_of_hand',
    'stealth': 'stealth',
    'survival': 'survival',
    "thieves' tools": 'thieves_tools',
    'thieves tools': 'thieves_tools',
}
_SKILL_RE = re.compile(
    r"\b(" + '|'.join(sorted((re.escape(value) for value in _SKILL_PHRASES), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _text_roll_type(value: object) -> str:
    return str(value or '').strip().lower()


class RollGateTurn(Protocol):
    requires_roll: bool
    outcome_status: str | None
    roll_value: int | None
    rule_type: str | None
    rules_hint: str | None
    player_id: int | None


class TurnRollPolicy:
    """DM-response parsing and roll-gate construction policy."""

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

    @classmethod
    def _roll_request_sentences(cls, text: str) -> list[str]:
        return [
            sentence
            for sentence in cls.response_sentences(text)
            if _GROUP_ROLL_REQUEST_RE.search(sentence) and not _NO_ROLL_NEEDED_RE.search(sentence)
        ]

    @classmethod
    def stated_dc_from_response(cls, text: str) -> int | None:
        """Return the DC named in the final roll-request sentence, clamped to 5..30."""
        request_sentences = cls._roll_request_sentences(text)
        if not request_sentences:
            return None
        match = _STATED_DC_RE.search(request_sentences[-1])
        if not match:
            return None
        return max(MIN_STATED_DC, min(MAX_STATED_DC, int(match.group(1))))

    @classmethod
    def roll_type_from_response(cls, text: str, fallback: str | None = None) -> str:
        requested_clauses: list[str] = []
        for sentence in cls._roll_request_sentences(text):
            request_start = _ROLL_REQUEST_CLAUSE_RE.search(sentence)
            requested_clauses.append(sentence[request_start.start():] if request_start else sentence)
        # A response may mention a prior check while narrating its consequence
        # before requesting the next one. Classify the final explicit request,
        # not the first skill name anywhere in the response.
        candidate = (requested_clauses[-1] if requested_clauses else (text or '')).lower()
        if re.search(r'\binitiative\b', candidate):
            return 'initiative'
        saving_throw = _SAVING_THROW_RE.search(candidate)
        if saving_throw:
            return f'{saving_throw.group(1).lower()}_saving_throw'
        skill = _SKILL_RE.search(candidate)
        if skill:
            return _SKILL_PHRASES[skill.group(1).lower()]
        ability_check = _ABILITY_CHECK_RE.search(candidate)
        if ability_check:
            return ability_check.group(1).lower()
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
        """Build the gate for a roll the DM requested or the turn already requires.

        A DC stated in the final roll-request sentence becomes the gate's
        ``dc_hint`` and is also written into ``turn.rules_hint``, where the
        pending-roll resolution reads the task DC.
        """
        if not ((turn.requires_roll and turn.outcome_status == 'deferred') or response_requests_roll):
            return None

        roll_type = cls.roll_type_from_response(dm_response_text, turn.rule_type)
        rules_hint = safe_json_loads(turn.rules_hint, {})
        rules_hint = rules_hint if isinstance(rules_hint, dict) else {}
        persisted_roll_spec = rules_hint.get('roll_spec') if isinstance(rules_hint.get('roll_spec'), dict) else None
        persisted_rule_type = (
            _text_roll_type((persisted_roll_spec or {}).get('rule_type'))
            or _text_roll_type(turn.rule_type)
        )
        if persisted_roll_spec and persisted_rule_type and persisted_rule_type != roll_type:
            # The DM can refine an initial heuristic (for example, a generic
            # magic action) into a specific skill or saving throw. Keep only
            # presentation settings across that refinement; ability and
            # proficiency provenance must be recomputed from the persisted
            # character when the authoritative roll is resolved.
            persisted_roll_spec = {
                key: persisted_roll_spec[key]
                for key in ('die', 'mode', 'reason', 'result_visibility')
                if key in persisted_roll_spec
            }
        roll_spec = {
            'die': 'd20',
            'mode': 'normal',
            'result_visibility': 'hidden_until_landed',
            **(persisted_roll_spec or {}),
            'rule_type': roll_type,
        }
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
            gate = {
                'scope': 'pvp_contest',
                'rule_type': roll_type,
                'required_player_ids': required_player_ids,
                'resolved_player_ids': resolved_player_ids,
                'remaining_player_ids': remaining_player_ids,
                'target_player_id': pvp_target_player_id,
            }
            gate['roll_spec'] = roll_spec
            return cls._apply_stated_dc(gate, turn=turn, rules_hint=rules_hint, dm_response_text=dm_response_text)

        if turn.roll_value is not None:
            return None

        required_player_ids = [turn.player_id] if turn.player_id else []
        scope = 'single_player'
        if cls.response_requests_group_roll(dm_response_text) and len(group_player_ids) > 1:
            required_player_ids = list(dict.fromkeys(group_player_ids))
            scope = 'group'
        if not required_player_ids:
            return None
        gate = {
            'scope': scope,
            'rule_type': roll_type,
            'required_player_ids': required_player_ids,
            'resolved_player_ids': [],
            'remaining_player_ids': required_player_ids,
        }
        gate['roll_spec'] = roll_spec
        return cls._apply_stated_dc(gate, turn=turn, rules_hint=rules_hint, dm_response_text=dm_response_text)

    @classmethod
    def _apply_stated_dc(
        cls,
        gate: dict,
        *,
        turn: RollGateTurn,
        rules_hint: dict,
        dm_response_text: str,
    ) -> dict:
        stated_dc = cls.stated_dc_from_response(dm_response_text)
        if stated_dc is None:
            return gate
        dc_hint = f'DC {stated_dc}'
        gate['dc_hint'] = dc_hint
        turn.rules_hint = safe_json_dumps({**rules_hint, 'dc_hint': dc_hint}, {})
        return gate
