"""Output hygiene for AI narration, applied after the model answers and before the reply is stored and sent.

Deterministic, from the turn's rules hint and its engine brief
(``state_pipeline.engineBrief``):

1. Outside the opening, a final sentence that is only a prompt to act ("What do
   you do?", "What do you do next?", "What now?", quoted or not) is dropped; so
   is the narrator's own unquoted closing question to the player ("What do you
   do with it, and do you answer the reeve?").
2. Markdown the Story view does not render is removed, keeping the words:
   asterisks, underscores used as emphasis, backticks and a leading ``#``.
3. When the turn's results already report coins, XP or items, a sentence that
   only restates them ("Arden Vale gains 100 XP, 25 gp, and one Ash-Marked
   Token.") is dropped; so is a "You gain/take/receive …" sentence about coins
   or items the results do not contain ("You gain 15 gp." before they are taken).
4. A roll request for something the engine rolls itself — a scene object, a
   loose item, travel, a purchase, an attack or a spell — is dropped, unless the
   engine is itself waiting on a roll this turn. The request is for an object,
   item, place or shop when it or the sentence before it names one; requests
   for sneaking or for dealing with people never are. ``engine_rolls_removed``
   tells the caller, which then asks for no roll and opens no roll gate. Roll
   requests for improvised actions the engine does not own stay.
5. In a fight, a sentence that says whose turn it is or that a turn is over
   ("Corin Ashby, it is your turn.", "Thalion is up next.", "Your turn is
   spent.", "… weighs the next move.") is dropped, unless it is spoken (has a
   quotation mark); the narration service ends the reply with the engine's own
   turn line instead (:func:`aidm_server.scripted_narrator.engine_turn_line`).
6. In and out of a fight, a final sentence that is only the label of an action
   offered this turn ("Search the strongbox.") is dropped, compared without case
   or final punctuation.
7. On a turn that sets a finishing blow, the engine ends the narration with the
   one sanctioned closing question (``The hollow marshal is finished. How do
   you end it?``, see :func:`aidm_server.scripted_narrator.engine_turn_line`), so
   that question always reaches the players and is never cut; the model's own
   copies of its sentences are dropped so it is asked once.
8. Brisk: outside an opening, an arrival and the adventure's ending, a
   narration longer than twice its limit (60 words in a fight, 90 outside one)
   is cut at the last sentence end at or before twice the limit, never
   mid-sentence and never inside a quotation (telemetry
   ``narration.hygiene.trimmed_total``).

A line nothing is taken from is kept exactly as the model wrote it.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from aidm_server.game_state.extraction.free_text_actions import mentions, name_words, phrase_names
from aidm_server.prompt_templates import ENGINE_BRIEF_KEY, is_opening_rules_hint
from aidm_server.telemetry import telemetry_metric
from aidm_server.turn_roll_policy import TurnRollPolicy
from aidm_server.turn_rules import response_mentions_roll_request

SENTENCE = re.compile(r'\S.*?(?:[.!?]+["”’)\]]*(?=\s|$)|$)')
PROMPT_TO_ACT = re.compile(
    r'^["“‘\']?\s*(?:(?:so|now|well),?\s+)?'
    r'(?:what\s+(?:do|will|would|shall)\s+you\s+do(?:\s+(?:next|now))?|what\s+now|what\s+next)'
    r'(?:,\s*[^?]{1,40})?\s*\?\s*["”’\']?$',
    re.IGNORECASE,
)
# The narrator's own closing question to the player ("What do you do with it, and do you answer
# the reeve?"). Never a spoken line: a sentence with a quotation mark in it is left alone.
PLAYER_QUESTION = re.compile(
    r'^(?:(?:so|now|well|and|but),?\s+)?'
    r'(?:(?:what|how|where|who|whom|which(?:\s+\w+){0,2})\s+(?:do|will|would|shall)\s+you\b|(?:do|will|would)\s+you\b)'
    r'[^"“”‘]{0,200}\?$',
    re.IGNORECASE,
)
HEADING = re.compile(r'^\s*#{1,6}\s*')
UNDERSCORE_EMPHASIS = re.compile(r'(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])')
NUMBER_WORDS = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17,
    'eighteen': 18, 'nineteen': 19, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'hundred': 100,
}
NUMBER = rf"(?:\d+|(?:(?:a|one)\s+)?hundred|{'|'.join(NUMBER_WORDS)})"
COIN_NAMES = {'gold': 'gp', 'silver': 'sp', 'copper': 'cp', 'platinum': 'pp', 'electrum': 'ep'}
XP_ENTRY = re.compile(rf'^(?P<amount>{NUMBER})\s*(?:xp|experience(?:\s+points)?)$', re.IGNORECASE)
COIN_ENTRY = re.compile(
    rf"^(?P<amount>{NUMBER})\s*(?P<code>gp|sp|cp|pp|ep|{'|'.join(COIN_NAMES)})(?:\s+(?:pieces?|coins?))?$",
    re.IGNORECASE,
)
ITEM_ENTRY = re.compile(rf'^(?:(?:a|an|the|{NUMBER})\s+)?(?P<name>.+)$', re.IGNORECASE)
ENTRY_SPLIT = re.compile(r',\s*(?:and\s+)?|\s+and\s+|\s+plus\s+', re.IGNORECASE)
GAIN_SENTENCE = re.compile(
    r'^(?P<subject>[^,;:!?"“]+?)\s+(?P<verb>gains?|receives?|earns?|takes?|(?:is|are)\s+awarded)\s+(?P<list>[^!?]+?)[.!]?$',
    re.IGNORECASE,
)
RESTATING_VERBS = frozenset({'gain', 'gains', 'receive', 'receives', 'earn', 'earns', 'is awarded', 'are awarded'})
YOU_VERBS = frozenset({'gain', 'take', 'receive'})
PARTY_SUBJECTS = frozenset({'you', 'you each', 'you both', 'each of you', 'all of you', 'everyone', 'the party', 'your party'})
# Rolls the engine makes itself: a model's request for one is always the engine's business.
ENGINE_ROLL_TYPES = frozenset({'attack', 'spell'})
# Turn roll types the engine itself waits on when the turn requires them.
ENGINE_PENDING_ROLL_TYPES = ENGINE_ROLL_TYPES | {'spell_attack', 'initiative'}
# Rolls about sneaking or people, which never concern an object, item, place or shop.
PEOPLE_ROLL_TYPES = frozenset(
    {'stealth', 'persuasion', 'deception', 'intimidation', 'performance', 'insight', 'animal_handling', 'social'}
)
# The source of the pending roll that only mirrors a roll the turn's wording suggested.
TURN_RULES_ROLL_SOURCE = 'turn_rules'
PURCHASE = re.compile(r'\b(?:buy|buying|purchase|purchasing|haggle|haggling|bargain|bargaining|price|discount)\b', re.IGNORECASE)
# The model calling a turn: whose it is, or that one is over.
TURN_CALL = re.compile(
    r"\bit(?:'s|’s| is)(?: (?:still|now))? (?:your|[\w-]+(?: [\w-]+)?['’]s) turn\b"
    r"|\byour turn\b|\bis up next\b|\bturn is (?:spent|over|done)\b|\bthe next move\b",
    re.IGNORECASE,
)
QUOTE_MARKS = frozenset('"“”')
# Word limits (rule 8): a narration over twice its limit is trimmed.
FIGHT_WORD_LIMIT = 60
WORD_LIMIT = 90
TRIMMED_METRIC = 'narration.hygiene.trimmed_total'


@dataclass(frozen=True)
class CleanNarration:
    text: str
    engine_rolls_removed: bool = False


def _text(value: Any) -> str:
    return str(value or '').strip()


def _amount(text: str) -> int:
    words = text.lower().split()
    return int(words[-1]) if words[-1].isdigit() else NUMBER_WORDS[words[-1]]


def _gain_entries(listed: str, known_items: list[str]) -> list[tuple[str, Any]] | None:
    """``[("xp", 100), ("coins", ("gp", 25)), ("item", "Ash-Marked Token")]``; None unless every part is one."""

    entries: list[tuple[str, Any]] = []
    for part in ENTRY_SPLIT.split(listed.strip()):
        part = part.strip()
        if not part:
            continue
        if xp := XP_ENTRY.match(part):
            entries.append(('xp', _amount(xp.group('amount'))))
        elif coins := COIN_ENTRY.match(part):
            code = coins.group('code').lower()
            entries.append(('coins', (COIN_NAMES.get(code, code), _amount(coins.group('amount')))))
        else:
            item = ITEM_ENTRY.match(part)
            words = name_words(item.group('name')) if item else []
            known = next((name for name in known_items if phrase_names(words, name_words(name))), None)
            if known is None:
                return None
            entries.append(('item', known))
    return entries or None


def _drops_gain(sentence: str, brief: dict[str, Any]) -> bool:
    gain = GAIN_SENTENCE.match(sentence)
    if not gain:
        return False
    gains = brief.get('gains') if isinstance(brief.get('gains'), dict) else {}
    coins = gains.get('coins') if isinstance(gains.get('coins'), dict) else {}
    gained_items = [_text(name) for name in gains.get('items') or [] if _text(name)]
    here = brief.get('here') if isinstance(brief.get('here'), dict) else {}
    entries = _gain_entries(gain.group('list'), [*gained_items, *(_text(name) for name in here.get('items') or [])])
    if entries is None:
        return False
    subject = ' '.join(gain.group('subject').lower().split())
    verb = ' '.join(gain.group('verb').lower().split())
    heroes = {name for hero in brief.get('heroes') or [] if (name := _text(hero).lower())}
    party = subject in PARTY_SUBJECTS or subject in heroes or subject in {hero.split()[0] for hero in heroes}

    def reported(kind: str, value: Any) -> bool:
        if kind == 'xp':
            return int(gains.get('xp') or 0) > 0
        if kind == 'coins':
            return int(coins.get(value[0]) or 0) > 0
        return value in gained_items

    if party and verb in RESTATING_VERBS and all(reported(kind, value) for kind, value in entries):
        return True

    def contained(kind: str, value: Any) -> bool:
        if kind == 'coins':
            return int(coins.get(value[0]) or 0) == value[1]
        return kind == 'xp' or value in gained_items

    return subject == 'you' and verb in YOU_VERBS and not all(contained(kind, value) for kind, value in entries)


def _requests_roll(sentence: str) -> bool:
    return response_mentions_roll_request(sentence) or TurnRollPolicy.response_requests_group_roll(sentence)


def _engine_owns_roll(sentence: str, previous: str, brief: dict[str, Any]) -> bool:
    roll_type = TurnRollPolicy.roll_type_from_response(sentence)
    if roll_type in ENGINE_ROLL_TYPES:
        return True
    nearby = f'{previous} {sentence}'
    here = brief.get('here') if isinstance(brief.get('here'), dict) else {}
    if here.get('shops') and PURCHASE.search(nearby):
        return True
    if roll_type in PEOPLE_ROLL_TYPES:
        return False
    owned = [_text(name) for name in brief.get('engineOwned') or [] if _text(name)]
    return bool(mentions(nearby, owned, lambda name: name, guarded=False))


def _word_limit(rules_hint: dict[str, Any], state_pipeline: dict[str, Any], brief: dict[str, Any], *, in_fight: bool) -> int | None:
    """The turn's word limit, or None for an opening, an arrival or the ending, which may run long."""

    narration = state_pipeline.get('narration') if isinstance(state_pipeline.get('narration'), dict) else {}
    arrived = bool(narration.get('arrival')) or any(
        isinstance(change, dict) and change.get('type') == 'scene.move_location'
        for change in state_pipeline.get('stateChangesAlreadyApplied') or []
    )
    if is_opening_rules_hint(rules_hint) or arrived or brief.get('ending') is True:
        return None
    return FIGHT_WORD_LIMIT if in_fight else WORD_LIMIT


def _trim(lines: list[dict[str, Any]], words_allowed: int) -> bool:
    """Cut the narration at the last sentence end within ``words_allowed`` words that is outside any
    quotation; False when it already fits or no sentence end qualifies."""

    sentences = [(line, index, sentence) for line in lines for index, sentence in enumerate(line['sentences'])]
    if sum(len(sentence.split()) for _line, _index, sentence in sentences) <= words_allowed:
        return False
    words = straight = curly = 0
    cut = None
    for line, index, sentence in sentences:
        words += len(sentence.split())
        if words > words_allowed:
            break
        straight += sentence.count('"')
        curly += sentence.count('“') - sentence.count('”')
        if straight % 2 == 0 and curly <= 0:
            cut = (line, index)
    if cut is None:
        return False
    cut_line, cut_index = cut
    after = False
    for line in lines:
        if after:
            if line['sentences']:
                line['sentences'] = []
                line['changed'] = True
            continue
        if line is cut_line:
            if len(line['sentences']) > cut_index + 1:
                line['sentences'] = line['sentences'][: cut_index + 1]
                line['changed'] = True
            after = True
    return True


def _closing_sentences(state_pipeline: dict[str, Any]) -> set[str]:
    """The sentences of the finishing blow's closing the engine adds this turn, compared without case."""

    narration = state_pipeline.get('narration') if isinstance(state_pipeline.get('narration'), dict) else {}
    blow = narration.get('finishingBlow') if isinstance(narration.get('finishingBlow'), dict) else {}
    return {_label_key(match.group(0)) for match in SENTENCE.finditer(_text(blow.get('closing')))}


def engine_roll_pending(rules_hint: dict[str, Any]) -> bool:
    """The engine itself waits on a player roll this turn: an attack, a spell, initiative, a roll its rules
    require, or a contest. A roll the turn's wording only suggested (``source: turn_rules``) is not one."""

    if rules_hint.get('requires_roll') is not True or rules_hint.get('roll_value') is not None:
        return False
    state_pipeline = rules_hint.get('state_pipeline') if isinstance(rules_hint.get('state_pipeline'), dict) else {}
    return (
        _text(rules_hint.get('roll_type')).lower() in ENGINE_PENDING_ROLL_TYPES
        or any(
            isinstance(roll, dict) and roll.get('source') != TURN_RULES_ROLL_SOURCE
            for roll in state_pipeline.get('pendingRolls') or []
        )
        or isinstance(rules_hint.get('pvp'), dict)
    )


def _without_markdown(line: str) -> str:
    return UNDERSCORE_EMPHASIS.sub('', HEADING.sub('', line).replace('`', '').replace('*', ''))


def clean_ai_narration(text: str, rules_hint: dict[str, Any]) -> CleanNarration:
    """The narration with the hygiene rules of this module applied."""

    state_pipeline = rules_hint.get('state_pipeline') if isinstance(rules_hint.get('state_pipeline'), dict) else {}
    brief = state_pipeline.get(ENGINE_BRIEF_KEY) if isinstance(state_pipeline.get(ENGINE_BRIEF_KEY), dict) else {}
    engine_waits_on_roll = engine_roll_pending(rules_hint)
    lines = []
    for raw_line in str(text or '').split('\n'):
        line = _without_markdown(raw_line)
        lines.append({'raw': raw_line, 'changed': line != raw_line, 'sentences': [match.group(0) for match in SENTENCE.finditer(line)]})

    in_fight = isinstance(state_pipeline.get('combatState'), dict) and bool(state_pipeline['combatState'])
    closing = _closing_sentences(state_pipeline)
    removed_rolls = False
    previous = ''
    for line in lines:
        kept = []
        for sentence in line['sentences']:
            if closing and _label_key(sentence) in closing:
                line['changed'] = True
                continue
            if brief and _drops_gain(sentence, brief):
                line['changed'] = True
                continue
            if not engine_waits_on_roll and _requests_roll(sentence) and _engine_owns_roll(sentence, previous, brief):
                line['changed'] = removed_rolls = True
                continue
            if in_fight and TURN_CALL.search(sentence) and not QUOTE_MARKS & set(sentence):
                line['changed'] = True
                continue
            kept.append(sentence)
            previous = sentence
        line['sentences'] = kept

    limit = _word_limit(rules_hint, state_pipeline, brief, in_fight=in_fight)
    if limit is not None and _trim(lines, 2 * limit):
        telemetry_metric(TRIMMED_METRIC, 1)

    if not is_opening_rules_hint(rules_hint):
        last = next((line for line in reversed(lines) if line['sentences']), None)
        if last is not None and (PROMPT_TO_ACT.match(last['sentences'][-1]) or PLAYER_QUESTION.match(last['sentences'][-1].strip())):
            last['sentences'] = last['sentences'][:-1]
            last['changed'] = True
        last = next((line for line in reversed(lines) if line['sentences']), None)
        offered = {_label_key(label) for label in brief.get('offered') or [] if _text(label)}
        if last is not None and _label_key(last['sentences'][-1]) in offered:
            last['sentences'] = last['sentences'][:-1]
            last['changed'] = True

    rendered = '\n'.join(' '.join(line['sentences']) if line['changed'] else line['raw'] for line in lines)
    return CleanNarration(text=re.sub(r'\n{3,}', '\n\n', rendered).strip(), engine_rolls_removed=removed_rolls)


def _label_key(text: Any) -> str:
    return ' '.join(_text(text).rstrip('.!?…').casefold().split())
