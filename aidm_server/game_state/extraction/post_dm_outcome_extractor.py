from __future__ import annotations

import re
from typing import Any

from flask import current_app, has_app_context

from aidm_server.canon_text import int_or_default
from aidm_server.contracts import ProviderRequest
from aidm_server.game_state.extraction.prompts import POST_DM_SYSTEM_MESSAGE, build_post_dm_prompt
from aidm_server.game_state.extraction.schemas import extract_json_object, normalize_post_extraction
from aidm_server.game_state.models import normalize_item_name, stable_change_id
from aidm_server.llm_providers import get_helper_provider
from aidm_server.telemetry import telemetry_event, telemetry_metric


HELPER_RAW_PREVIEW_LIMIT = 2000
SMALL_NUMBER_WORDS = {
    'zero': 0,
    'one': 1,
    'two': 2,
    'three': 3,
    'four': 4,
    'five': 5,
    'six': 6,
    'seven': 7,
    'eight': 8,
    'nine': 9,
    'ten': 10,
    'eleven': 11,
    'twelve': 12,
}
SMALL_NUMBER_PATTERN = r'\d{1,4}|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve'
HEAL_PATTERN = re.compile(
    r'\b(?:restore|restores|restored|heal|heals|healed|regain|regains|regained|recover|recovers|recovered)\s+'
    rf'(?P<amount>{SMALL_NUMBER_PATTERN})\s*(?:hp|hit points?)\b',
    re.IGNORECASE,
)
DAMAGE_PATTERN = re.compile(
    r'\b(?:take|takes|took|suffer|suffers|suffered)\s+'
    rf'(?P<amount>{SMALL_NUMBER_PATTERN})\s*(?:points?\s+of\s+)?(?:[a-z]+\s+)?(?:damage|hp)\b',
    re.IGNORECASE,
)
XP_GAIN_PATTERN = re.compile(
    r'\b(?:gain|gains|gained|earn|earns|earned|award(?:ed)?|receive|receives|received)\s+'
    r'(?P<amount>\d{1,6})\s*(?:xp|experience)\b',
    re.IGNORECASE,
)
XP_LOSS_PATTERN = re.compile(
    r'\b(?:lose|loses|lost|spend|spends|spent)\s+'
    r'(?P<amount>\d{1,6})\s*(?:xp|experience)\b',
    re.IGNORECASE,
)
SPELL_NAME_PATTERN = r'[A-Z][A-Za-z0-9\' -]{1,50}|[a-z][a-z0-9\' -]{2,50}'
SPELL_LEARN_PATTERNS = [
    re.compile(
        rf'\b(?:you\s+)?(?:learn|learns|learned|master|masters|mastered|unlock|unlocks|unlocked)\s+'
        rf'(?:the\s+)?(?:spell|cantrip|ritual|magic|magical technique|form)?\s*(?P<spell>{SPELL_NAME_PATTERN})'
        r'(?=\s*(?:[.!?,;]|$))',
        re.IGNORECASE,
    ),
    re.compile(
        rf'\b(?:teaches|taught|teaching)\s+(?:you\s+)?(?:the\s+)?(?:spell|cantrip|ritual|magic|magical technique|form)?\s*'
        rf'(?P<spell>{SPELL_NAME_PATTERN})(?=\s*(?:[.!?,;]|$))',
        re.IGNORECASE,
    ),
    re.compile(
        rf'\b(?:book|tome|scroll|teacher|mentor|lesson)\b[^.!?\n]{{0,80}}?\b(?:teaches|reveals|unlocks)\s+'
        rf'(?:the\s+)?(?:spell|cantrip|ritual|magic|magical technique|form)?\s*(?P<spell>{SPELL_NAME_PATTERN})'
        r'(?=\s*(?:[.!?,;]|$))',
        re.IGNORECASE,
    ),
    re.compile(
        rf'\b(?:copy|copies|copied|read|reads|studies|studied)\b[^.!?\n]{{0,80}}?\b'
        rf'(?P<spell>{SPELL_NAME_PATTERN})\s+(?:spell|cantrip|ritual|incantation)\b',
        re.IGNORECASE,
    ),
]
CURRENCY_PATTERN = re.compile(
    r'\b(?:gain|gains|gained|receive|receives|received|loot|loots|looted|find|finds|found|take|takes|took|collect|collects|collected)\b'
    r'[^.!?\n]{0,80}?\b(?P<amount>\d{1,5})\s+'
    r'(?P<currency>pp|gp|ep|sp|cp|platinum|gold|electrum|silver|copper)'
    r'(?:\s+(?:pieces?|coins?))?\b',
    re.IGNORECASE,
)
CURRENCY_LOSS_PATTERN = re.compile(
    r'\b(?:spend|spends|spent|pay|pays|paid|lose|loses|lost|give|gives|gave|hand over|hands over)\b'
    r'[^.!?\n]{0,80}?\b(?P<amount>\d{1,5})\s+'
    r'(?P<currency>pp|gp|ep|sp|cp|platinum|gold|electrum|silver|copper)'
    r'(?:\s+(?:pieces?|coins?))?\b',
    re.IGNORECASE,
)
ITEM_GAIN_PATTERN = re.compile(
    r'\b(?:you\s+)?(?P<verb>find|finds|found|take|takes|took|pick up|picks up|picked up|receive|receives|received|loot|loots|looted|buy|buys|bought|purchase|purchases|purchased|add|adds|added)\s+'
    r'(?:the|a|an|some)?\s*(?P<item>[a-z][a-z0-9\' -]{1,60}?)(?=\s+(?:and|from|to|into|under|onto|on|beside|before|after|with|without)\b|[.!?,;]|$)',
    re.IGNORECASE,
)
ITEM_LOSS_PATTERN = re.compile(
    r'\b(?:you\s+)?(?:drop|drops|dropped|consume|consumes|consumed|use up|uses up|used up|give|gives|gave|sell|sells|sold)\s+'
    r'(?:the|a|an|some|your)?\s*(?P<item>[a-z][a-z0-9\' -]{1,60}?)(?=\s+(?:and|from|to|into|under|onto|on|beside|before|after|with|without)\b|[.!?,;]|$)',
    re.IGNORECASE,
)
ITEM_EQUIP_PATTERN = re.compile(
    r'\b(?:you\s+)?(?:equip|equips|equipped|wield|wields|wielded|ready|readies|readied|wear|wears|wore|don|dons|donned|put on|puts on|strap on|straps on|strapped on|draw|draws|drew)\s+'
    r'(?:the|a|an|some|your)?\s*(?P<item>[a-z][a-z0-9\' -]{1,60}?)(?=\s+(?:and|from|to|into|under|onto|on|beside|before|after|with|without)\b|[.!?,;]|$)',
    re.IGNORECASE,
)
ITEM_UNEQUIP_PATTERN = re.compile(
    r'\b(?:you\s+)?(?:unequip|unequips|unequipped|doff|doffs|doffed|stow|stows|stowed|sheathe|sheathes|sheathed|put away|puts away|take off|takes off|took off|remove|removes|removed)\s+'
    r'(?:the|a|an|some|your)?\s*(?P<item>[a-z][a-z0-9\' -]{1,60}?)(?=\s+(?:and|from|to|into|under|onto|on|beside|before|after|with|without)\b|[.!?,;]|$)',
    re.IGNORECASE,
)
EXPLICIT_INVENTORY_STATE_PATTERN = re.compile(
    r'\bstate change:\s*[^.\n]*?\*{0,2}'
    r'(?P<verb>gain|gains|add|adds|receive|receives|take|takes|pick up|picks up|'
    r'lose|loses|drop|drops|remove|removes|spend|spends|consume|consumes)\*{0,2}\s+'
    r'(?P<quantity>\d{1,4})\s+'
    r'(?P<item>[a-z][a-z0-9\' -]{0,80}?)'
    r'(?:\s*\((?P<alias>[^)]{1,100})\))?'
    r'(?=\s*(?:to|into|in|from|out of)\s+(?:their|your|his|her)?\s*inventory\b|[.)\n]|$)',
    re.IGNORECASE,
)

CURRENCY_WORDS = {
    'platinum': 'pp',
    'gold': 'gp',
    'electrum': 'ep',
    'silver': 'sp',
    'copper': 'cp',
}
NON_ITEM_PHRASES = {
    'breath',
    'confidence',
    'courage',
    'cover',
    'focus',
    'guard',
    'hp',
    'hit points',
    'damage',
    'pieces',
    'coins',
    'it',
    'moment',
    'them',
}
NON_SPELL_NAMES = {
    'a spell',
    'the spell',
    'new magic',
    'magic',
    'magical technique',
    'ritual',
    'cantrip',
    'form',
}
CURRENCY_ONLY_ITEM_PHRASES = {'gold', 'silver', 'copper', 'platinum', 'electrum'}
CONDITIONAL_ITEM_CONTEXT_PATTERN = re.compile(
    r'\b(?:would|could|might|may|needs?|needed|requires?|represents|attempt|trying|try|precision)\b|'
    r'\b(?:please\s+roll|make\s+a\s+[^.!?\n]{0,80}?\bcheck|dc\s+of\s+\d+|against\s+a\s+dc)\b',
    re.IGNORECASE,
)
OBSERVED_ONLY_ITEM_CONTEXT_PATTERN = re.compile(
    r'\b(?:see|sees|saw|spot|spots|spotted|notice|notices|noticed|glimpse|glimpses|glimpsed|visible|'
    r'lying|lies|resting|rests|sitting|sits|on display|before you|from the doorway|'
    r'on the altar|on the table|on the floor|on a shelf|on the shelf|on the pedestal|in the room)\b',
    re.IGNORECASE,
)
ACQUIRED_ITEM_VERB_PATTERN = re.compile(
    r'\b(?:take|takes|took|pick up|picks up|picked up|receive|receives|received|loot|loots|looted|'
    r'buy|buys|bought|purchase|purchases|purchased|pocket|pockets|pocketed|claim|claims|claimed|'
    r'collect|collects|collected|add|adds|added)\b',
    re.IGNORECASE,
)
ROLL_PROMPT_PATTERN = re.compile(
    r'\b(?:make|roll)\s+(?:a|an)?\s*[^.!?\n]{0,80}?\bcheck\b|'
    r'\bdc\s*(?:of\s*)?\d{1,2}\b',
    re.IGNORECASE,
)
EXPLICIT_DANGER_LEVEL_PATTERN = re.compile(
    r'\bdanger(?:\s+level)?\s*(?:is|=|:|to|rises?\s+to|climbs?\s+to|jumps?\s+to|'
    r'drops?\s+to|falls?\s+to|decreases?\s+to|increases?\s+to)\s*(?P<level>10|[0-9])\b',
    re.IGNORECASE,
)
COMBAT_DANGER_PATTERN = re.compile(
    r'\b(?:roll initiative|initiative|combat begins|battle begins|fight begins|ambush(?:es|ed)?|'
    r'attacks?|charges?|lunges?|strikes?|arrows?\s+fly|blades?\s+drawn|weapons?\s+drawn|'
    r'hostile|surround(?:s|ed)?|enemy|enemies)\b',
    re.IGNORECASE,
)
HIGH_DANGER_PATTERN = re.compile(
    r'\b(?:trap\s+(?:springs?|triggers?|erupts?)|alarm\s+(?:sounds?|rings?)|collaps(?:e|es|ing)|'
    r'poison(?:ed|ous)?|toxic|fire\s+spreads?|flames?\s+spread|lethal|deadly|'
    r'about\s+to\s+collapse|floor\s+gives\s+way)\b',
    re.IGNORECASE,
)
MODERATE_DANGER_PATTERN = re.compile(
    r'\b(?:dangerous|threat(?:en(?:s|ed|ing)?)?|hazard(?:ous)?|trap|stalk(?:s|ed|ing)?|'
    r'watch(?:es|ing)\s+you|growl(?:s|ing)?|snarl(?:s|ing)?|unstable|narrow\s+ledge|'
    r'fresh\s+blood|ominous|peril(?:ous)?)\b',
    re.IGNORECASE,
)
LOWER_DANGER_PATTERN = re.compile(
    r'\b(?:danger\s+(?:passes|fades|recedes)|no\s+immediate\s+threat|safe\s+for\s+now|'
    r'fight\s+is\s+over|combat\s+ends?|battle\s+ends?|enemy\s+(?:falls|flees)|enemies\s+(?:fall|flee)|'
    r'defeated|surrenders?|calm(?:s|ed)?|quiet(?:s|ed)?|secure|harmless|inert|disabled)\b',
    re.IGNORECASE,
)
ENEMY_DEFEATED_PATTERN = re.compile(
    r'\b(?:falls?|drops?|dies?|dead|defeated|destroyed|collapses?|goes down|is slain|are slain|last enemy falls)\b',
    re.IGNORECASE,
)
ENEMY_FLEE_PATTERN = re.compile(r'\b(?:flees?|runs? away|retreats?|escapes?|withdraws?)\b', re.IGNORECASE)
ENEMY_SURRENDER_PATTERN = re.compile(r'\b(?:surrenders?|yields?|drops? (?:its|their|his|her) weapon|begs? for mercy)\b', re.IGNORECASE)
COMBAT_CONDITIONS = {
    'blinded',
    'charmed',
    'deafened',
    'exhausted',
    'frightened',
    'grappled',
    'incapacitated',
    'invisible',
    'paralyzed',
    'petrified',
    'poisoned',
    'prone',
    'restrained',
    'stunned',
    'unconscious',
}
COMBAT_END_PATTERN = re.compile(
    r'\b(?:combat ends?|fight is over|battle ends?|no immediate threat|last enemy falls|enemies (?:fall|flee|surrender)|'
    r'all enemies (?:are )?(?:defeated|gone|fled|surrendered))\b',
    re.IGNORECASE,
)
LARGE_THREAT_PATTERN = re.compile(
    r'\b(?:massive|huge|giant|colossal|enormous|boss|dragon|demon|horror|monster|whale)\b',
    re.IGNORECASE,
)
COMBAT_XP_FALLBACK_BY_TIER = {
    'trivial': 10,
    'easy': 25,
    'standard': 50,
    'hard': 100,
    'deadly': 200,
    'boss': 500,
}
QUEST_XP_FALLBACK_BY_SCALE = {
    'trivial': 10,
    'minor': 25,
    'easy': 25,
    'side': 50,
    'standard': 50,
    'normal': 50,
    'main': 100,
    'major': 100,
    'hard': 100,
    'deadly': 200,
    'boss': 500,
}
DEFAULT_QUEST_XP_REWARD = 50
REWARD_AMOUNT_KEYS = ('xpReward', 'rewardXp', 'experienceReward', 'xp_reward', 'reward_xp', 'experience_reward')


def _helper_enabled() -> bool:
    if has_app_context() and current_app.config.get('AIDM_ENV') == 'test':
        return bool(current_app.config.get('AIDM_STATE_PIPELINE_HELPER_IN_TESTS', False))
    if has_app_context():
        return bool(current_app.config.get('AIDM_STATE_PIPELINE_HELPER_ENABLED', True))
    return True


def _post_payload_schema_valid(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    known_keys = {'proposedChanges', 'proposed_changes', 'uncertainChanges', 'uncertain_changes', 'notes'}
    if not any(key in payload for key in known_keys):
        return False
    for key in ('proposedChanges', 'proposed_changes', 'uncertainChanges', 'uncertain_changes'):
        if key in payload and not isinstance(payload.get(key), list):
            return False
    if 'notes' in payload and not isinstance(payload.get('notes'), (list, str)):
        return False
    return True


def _attach_debug(payload: dict[str, Any], debug: dict[str, Any]) -> dict[str, Any]:
    payload['debug'] = debug
    return payload


def _amount_text(value: Any) -> int:
    text = str(value or '').strip().lower()
    if text in SMALL_NUMBER_WORDS:
        return SMALL_NUMBER_WORDS[text]
    try:
        return int(text)
    except (TypeError, ValueError):
        return 0


def _change_identity_value(change: dict[str, Any]) -> Any:
    item = change.get('item') if isinstance(change.get('item'), dict) else {}
    spell = change.get('spell') if isinstance(change.get('spell'), dict) else {}
    return (
        change.get('locationId')
        or change.get('locationName')
        or change.get('questId')
        or change.get('questTitle')
        or change.get('title')
        or change.get('npcId')
        or change.get('npcName')
        or change.get('flagKey')
        or change.get('name')
        or change.get('objectiveId')
        or change.get('connectedLocationId')
        or change.get('itemId')
        or change.get('itemName')
        or change.get('participantId')
        or change.get('enemyId')
        or item.get('id')
        or item.get('name')
        or change.get('currency')
        or change.get('spellName')
        or spell.get('id')
        or spell.get('name')
        or change.get('amount')
        or change.get('quantity')
    )


def _assign_turn_scoped_change_ids(changes: list[dict[str, Any]], *, turn_id: int) -> None:
    for index, change in enumerate(changes, start=1):
        if not isinstance(change, dict):
            continue
        change_id = str(change.get('id') or '').strip()
        if not change_id or change_id.startswith('post_chg_'):
            change['id'] = stable_change_id(
                turn_id,
                'post_dm',
                index,
                change.get('type'),
                change.get('actorId') or change.get('actor_id'),
                _change_identity_value(change),
                change.get('quantity'),
                change.get('amount'),
            )
        change['turnId'] = turn_id


def _already_applied_signature(change: dict[str, Any]) -> tuple[Any, ...] | None:
    change_type = str(change.get('type') or '').strip()
    if change_type in {'inventory.add', 'inventory.remove', 'inventory.equip', 'inventory.unequip'}:
        item = change.get('item') if isinstance(change.get('item'), dict) else {}
        return (
            change_type,
            str(change.get('actorId') or ''),
            normalize_item_name(change.get('itemName') or item.get('name')),
        )
    if change_type in {'currency.add', 'currency.remove'}:
        return (change_type, str(change.get('actorId') or ''), str(change.get('currency') or '').lower(), int(change.get('amount') or 0))
    if change_type in {'health.heal', 'health.damage'}:
        return (change_type, str(change.get('actorId') or ''), int(change.get('amount') or 0))
    if change_type in {'xp.add', 'xp.remove'}:
        return (change_type, str(change.get('actorId') or ''), int(change.get('amount') or 0))
    if change_type == 'spell.learn':
        spell = change.get('spell') if isinstance(change.get('spell'), dict) else {}
        return (change_type, str(change.get('actorId') or ''), normalize_item_name(change.get('spellName') or spell.get('name')))
    if change_type in {'scene.update', 'scene.move_location'}:
        return (
            change_type,
            normalize_item_name(change.get('locationId') or change.get('name')),
            normalize_item_name(change.get('sceneType') or change.get('mood') or change.get('combatState')),
            str(change.get('dangerLevel')) if change.get('dangerLevel') is not None else '',
        )
    if change_type.startswith('location.'):
        return (
            change_type,
            normalize_item_name(change.get('locationId') or change.get('name')),
            normalize_item_name(change.get('connectedLocationId') or change.get('connectedLocationName')),
        )
    if change_type.startswith('quest.'):
        return (
            change_type,
            normalize_item_name(change.get('questId') or change.get('title') or change.get('name')),
            normalize_item_name(change.get('objectiveId') or change.get('stage')),
        )
    if change_type.startswith('npc.'):
        return (
            change_type,
            normalize_item_name(change.get('npcId') or change.get('name')),
            normalize_item_name(change.get('locationId') or change.get('disposition') or change.get('status')),
        )
    if change_type.startswith('flag.'):
        return (change_type, normalize_item_name(change.get('flagKey')))
    if change_type.startswith('combat.'):
        participant_id = normalize_item_name(change.get('participantId') or change.get('enemyId'))
        if change_type == 'combat.participant.update':
            hp = change.get('hp') if isinstance(change.get('hp'), dict) else {}
            conditions = tuple(sorted(str(item or '').strip().lower() for item in change.get('conditions') or []))
            return (
                change_type,
                participant_id,
                str(hp.get('current')) if hp.get('current') is not None else '',
                str(hp.get('max')) if hp.get('max') is not None else '',
                conditions,
                str(change.get('isAlive')) if change.get('isAlive') is not None else '',
                str(change.get('isConscious')) if change.get('isConscious') is not None else '',
            )
        if change_type in {'combat.condition.add', 'combat.condition.remove'}:
            return (change_type, participant_id, normalize_item_name(change.get('condition')))
        if change_type == 'combat.move':
            return (change_type, participant_id, normalize_item_name(change.get('toRangeBand') or change.get('rangeBand')))
        if change_type == 'combat.ability.mark_used':
            return (change_type, participant_id, normalize_item_name(change.get('abilityId')))
        if change_type == 'combat.morale.event':
            return (change_type, participant_id, normalize_item_name(change.get('event')))
        if change_type == 'combat.morale.update':
            return (change_type, participant_id, str(change.get('morale')))
        if change_type == 'combat.end':
            return (change_type, normalize_item_name(change.get('status') or change.get('endReason') or change.get('summary')))
        return (
            change_type,
            participant_id,
            normalize_item_name(change.get('intentType') or change.get('status') or change.get('morale')),
        )
    return None


def _already_applied(changes: list[dict[str, Any]]) -> set[tuple[Any, ...]]:
    signatures = set()
    for change in changes:
        if isinstance(change, dict):
            signature = _already_applied_signature(change)
            if signature:
                signatures.add(signature)
    return signatures


def _inventory_change_already_applied(
    *,
    change_type: str,
    actor_id: str,
    item_name: str,
    already_applied_changes: list[dict[str, Any]],
) -> bool:
    requested = normalize_item_name(item_name)
    if not requested:
        return False
    for change in already_applied_changes:
        if not isinstance(change, dict) or str(change.get('type')) != change_type:
            continue
        if str(change.get('actorId') or '') != str(actor_id):
            continue
        existing = normalize_item_name(change.get('itemName') or change.get('item_name'))
        if existing and (requested in existing or existing in requested):
            return True
    return False


def _clean_item(value: str) -> str:
    text = normalize_item_name(value)
    text = re.sub(r'\b(?:the|a|an|some|your|their|his|her|my)\b', '', text).strip()
    text = re.sub(r'\s+', ' ', text)
    return text


def _clean_item_label(value: str) -> str:
    text = str(value or '').strip()
    text = re.sub(r'\b(?:the|a|an|some|your|their|his|her|my)\b', '', text, flags=re.IGNORECASE).strip()
    return re.sub(r'\s+', ' ', text)


def _clean_spell_name(value: str) -> str:
    text = str(value or '').strip(' .,:;!?')
    text = re.sub(r'\b(?:into|from|to|with|and|as|while|when)\b.*$', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'^(?:you|the|a|an|spell|cantrip|ritual|magic|magical technique|form)\s+', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'\s+', ' ', text)
    normalized = normalize_item_name(text)
    if not normalized or normalized in NON_SPELL_NAMES:
        return ''
    if len(text.split()) > 5:
        return ''
    return text


def _heuristic_spell_learn_changes(
    *,
    text: str,
    changes: list[dict[str, Any]],
    turn_id: int,
    actor_id: str,
    already: set[tuple[Any, ...]],
) -> None:
    for sentence in _item_extraction_sentences(text):
        for pattern in SPELL_LEARN_PATTERNS:
            for match in pattern.finditer(sentence):
                spell_name = _clean_spell_name(match.group('spell'))
                if not spell_name:
                    continue
                _add_change(
                    changes,
                    turn_id=turn_id,
                    actor_id=actor_id,
                    change_type='spell.learn',
                    spellName=spell_name,
                    learnedFrom=sentence[:180],
                    reason=f'DM stated learned magic: {spell_name}.',
                    already=already,
                )


def _looks_like_item(value: str) -> bool:
    text = _clean_item(value)
    if not text:
        return False
    tokens = text.split()
    if len(tokens) > 6:
        return False
    if text in CURRENCY_ONLY_ITEM_PHRASES:
        return False
    return not any(token in NON_ITEM_PHRASES for token in tokens)


def _item_extraction_sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in re.split(r'(?<=[.!?])\s+|\n+', text or '') if sentence.strip()]


def _is_conditional_item_context(sentence: str) -> bool:
    normalized = normalize_item_name(sentence)
    if not normalized:
        return True
    if CONDITIONAL_ITEM_CONTEXT_PATTERN.search(sentence):
        return True
    if 'without' in normalized and not re.search(r'\b(?:you\s+)?(?:pick(?:s)? up|take(?:s)?|took|grab(?:s)?|collect(?:s)?)\b', normalized):
        return True
    return False


def _is_observed_only_item_context(sentence: str, verb: str) -> bool:
    normalized_verb = normalize_item_name(verb)
    if normalized_verb not in {'find', 'finds', 'found'}:
        return False
    return bool(
        OBSERVED_ONLY_ITEM_CONTEXT_PATTERN.search(sentence)
        and not ACQUIRED_ITEM_VERB_PATTERN.search(sentence)
    )


def _add_change(
    changes: list[dict[str, Any]],
    *,
    turn_id: int,
    actor_id: str,
    change_type: str,
    reason: str,
    already: set[tuple[Any, ...]],
    **payload,
) -> None:
    change = {
        'id': stable_change_id(
            turn_id,
            'post_dm',
            change_type,
            actor_id,
            payload.get('itemName'),
            payload.get('slot'),
        payload.get('currency'),
        payload.get('spellName'),
        payload.get('amount'),
    ),
        'turnId': turn_id,
        'type': change_type,
        'source': 'post_dm',
        'actorId': actor_id,
        'reason': reason,
        'visible': True,
        **payload,
    }
    if change_type == 'inventory.add':
        change['item'] = {
            'name': payload.get('itemName'),
            'quantity': payload.get('quantity', 1),
            'type': payload.get('itemType') or 'misc',
        }
    if change_type == 'spell.learn' and payload.get('spellName'):
        change['spell'] = {
            'name': payload.get('spellName'),
            'level': payload.get('spellLevel', 0),
        }
    signature = _already_applied_signature(change)
    if signature and signature in already:
        return
    if signature and any(_already_applied_signature(existing) == signature for existing in changes):
        return
    changes.append(change)


def _change_ids(changes: list[dict[str, Any]]) -> set[str]:
    return {
        str(change.get('id') or '').strip()
        for change in changes
        if isinstance(change, dict) and str(change.get('id') or '').strip()
    }


def _positive_reward_amount(value: Any) -> int:
    amount = int_or_default(value, default=0)
    return amount if amount > 0 else 0


def _reward_amount_from_record(record: dict[str, Any]) -> int:
    for key in REWARD_AMOUNT_KEYS:
        amount = _positive_reward_amount(record.get(key))
        if amount > 0:
            return amount
    for container_key in ('reward', 'rewards', 'metadata'):
        container = record.get(container_key) if isinstance(record.get(container_key), dict) else {}
        for key in ('xp', 'experience', *REWARD_AMOUNT_KEYS):
            amount = _positive_reward_amount(container.get(key))
            if amount > 0:
                return amount
    return 0


def _combat_xp_reward(enemy: dict[str, Any]) -> int:
    explicit_amount = _reward_amount_from_record(enemy)
    if explicit_amount > 0:
        return explicit_amount
    balance = enemy.get('balance') if isinstance(enemy.get('balance'), dict) else {}
    tier = normalize_item_name(enemy.get('challengeTier') or balance.get('targetTier'))
    if not tier and normalize_item_name(enemy.get('kind')) == 'boss':
        tier = 'boss'
    base_amount = COMBAT_XP_FALLBACK_BY_TIER.get(tier or 'standard', COMBAT_XP_FALLBACK_BY_TIER['standard'])
    level = max(1, int_or_default(enemy.get('level'), default=1))
    return base_amount * level


def _quest_xp_reward(quest: dict[str, Any]) -> int:
    explicit_amount = _reward_amount_from_record(quest)
    if explicit_amount > 0:
        return explicit_amount
    for key in ('difficulty', 'priority', 'type', 'questType', 'quest_type', 'scale'):
        normalized = normalize_item_name(quest.get(key))
        if normalized in QUEST_XP_FALLBACK_BY_SCALE:
            return QUEST_XP_FALLBACK_BY_SCALE[normalized]
    return DEFAULT_QUEST_XP_REWARD


def _combat_participant_by_id(state_before_dm: dict[str, Any], participant_id: str) -> dict[str, Any] | None:
    combat = state_before_dm.get('combat') if isinstance(state_before_dm, dict) else {}
    participants = combat.get('participants') if isinstance(combat, dict) else []
    for participant in participants or []:
        if isinstance(participant, dict) and str(participant.get('id') or '').strip() == participant_id:
            return participant
    return None


def _combat_participant_was_active_enemy(participant: dict[str, Any] | None) -> bool:
    if not isinstance(participant, dict) or participant.get('team') != 'enemy':
        return False
    if participant.get('isAlive') is False:
        return False
    hp = participant.get('hp') if isinstance(participant.get('hp'), dict) else {}
    current = hp.get('current')
    if current is None:
        return True
    return int_or_default(current, default=1) > 0


def _combat_participant_defeated_by_change(change: dict[str, Any]) -> bool:
    if str(change.get('type') or '') != 'combat.participant.update':
        return False
    conditions = {normalize_item_name(item) for item in change.get('conditions') or []}
    if conditions & {'fled', 'surrendered'}:
        return False
    if conditions & {'defeated', 'dead', 'slain', 'unconscious'}:
        return True
    hp = change.get('hp') if isinstance(change.get('hp'), dict) else {}
    if hp and int_or_default(hp.get('current'), default=1) <= 0:
        return True
    return change.get('isAlive') is False and change.get('isConscious') is False


def _quest_by_change(state_before_dm: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    requested_id = str(change.get('questId') or '').strip()
    requested_title = normalize_item_name(change.get('title') or change.get('name') or change.get('questTitle'))
    for quest in state_before_dm.get('quests') or []:
        if not isinstance(quest, dict):
            continue
        if requested_id and str(quest.get('id') or '').strip() == requested_id:
            return quest
        if requested_title and normalize_item_name(quest.get('title') or quest.get('name')) == requested_title:
            return quest
    return None


def _npc_by_change(state_before_dm: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    requested_id = str(change.get('npcId') or '').strip()
    requested_name = normalize_item_name(change.get('name') or change.get('npcName'))
    for npc in [*(state_before_dm.get('knownNpcs') or []), *(state_before_dm.get('partyNpcs') or []), *(state_before_dm.get('npcs') or [])]:
        if not isinstance(npc, dict):
            continue
        if requested_id and str(npc.get('id') or '').strip() == requested_id:
            return npc
        if requested_name and normalize_item_name(npc.get('name') or npc.get('npcName')) == requested_name:
            return npc
    return None


def _scene_allows_npc_defeat_xp(state_before_dm: dict[str, Any]) -> bool:
    scene = _current_scene(state_before_dm)
    return (
        normalize_item_name(scene.get('sceneType')) == 'combat'
        or normalize_item_name(scene.get('combatState')) in {'active', 'resolved'}
        or _scene_danger_level(scene) >= 5
    )


def _npc_defeated_by_change(change: dict[str, Any]) -> bool:
    if str(change.get('type') or '') != 'npc.update':
        return False
    npc_payload = change.get('npc') if isinstance(change.get('npc'), dict) else {}
    status = normalize_item_name(change.get('status') or npc_payload.get('status'))
    return status == 'dead'


def _npc_was_rewardable_enemy(npc: dict[str, Any] | None) -> bool:
    if not isinstance(npc, dict):
        return False
    if normalize_item_name(npc.get('status')) == 'dead':
        return False
    disposition = normalize_item_name(npc.get('disposition'))
    if disposition in {'friendly', 'allied', 'loyal'}:
        return False
    if normalize_item_name(npc.get('status')) in {'met', 'allied'} and disposition not in {'hostile', 'suspicious', 'unknown'}:
        return False
    return True


def _npc_xp_reward(npc: dict[str, Any]) -> int:
    explicit_amount = _reward_amount_from_record(npc)
    if explicit_amount > 0:
        return explicit_amount
    text = ' '.join(
        str(value or '')
        for value in (
            npc.get('name'),
            npc.get('role'),
            npc.get('description'),
            npc.get('memory'),
        )
    )
    if LARGE_THREAT_PATTERN.search(text):
        return COMBAT_XP_FALLBACK_BY_TIER['hard']
    return COMBAT_XP_FALLBACK_BY_TIER['standard']


def _has_existing_xp_add(changes: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(change, dict)
        and str(change.get('type') or '') == 'xp.add'
        for change in changes
    )


def _player_actor_ids(state_before_dm: dict[str, Any]) -> list[str]:
    actor_ids: list[str] = []
    for actor in state_before_dm.get('playerCharacters') or []:
        if not isinstance(actor, dict):
            continue
        actor_id = str(actor.get('id') or '').strip()
        if actor_id and actor_id not in actor_ids:
            actor_ids.append(actor_id)
    return actor_ids


def _active_session_actor_ids(state_before_dm: dict[str, Any]) -> list[str]:
    raw_ids = state_before_dm.get('activePlayerIds') if isinstance(state_before_dm.get('activePlayerIds'), list) else []
    actor_ids: list[str] = []
    for raw_id in raw_ids:
        text = str(raw_id or '').strip()
        if not text:
            continue
        actor_id = text if text.startswith('player_') else f'player_{text}'
        if actor_id not in actor_ids:
            actor_ids.append(actor_id)
    valid_actor_ids = set(_player_actor_ids(state_before_dm))
    return [actor_id for actor_id in actor_ids if not valid_actor_ids or actor_id in valid_actor_ids]


def _turn_control_actor_ids(state_before_dm: dict[str, Any]) -> list[str]:
    turn_control = state_before_dm.get('turnControl') if isinstance(state_before_dm.get('turnControl'), dict) else {}
    participant_ids = turn_control.get('participantPlayerIds') if isinstance(turn_control.get('participantPlayerIds'), list) else []
    raw_ids = [*participant_ids]
    if not raw_ids and turn_control.get('activePlayerId') is not None:
        raw_ids.append(turn_control.get('activePlayerId'))
    actor_ids: list[str] = []
    for raw_id in raw_ids:
        text = str(raw_id or '').strip()
        if not text:
            continue
        actor_id = text if text.startswith('player_') else f'player_{text}'
        if actor_id not in actor_ids:
            actor_ids.append(actor_id)
    valid_actor_ids = set(_player_actor_ids(state_before_dm))
    return [actor_id for actor_id in actor_ids if not valid_actor_ids or actor_id in valid_actor_ids]


def _combat_player_actor_ids(state_before_dm: dict[str, Any]) -> list[str]:
    combat = state_before_dm.get('combat') if isinstance(state_before_dm, dict) else {}
    participants = combat.get('participants') if isinstance(combat, dict) else []
    actor_ids: list[str] = []
    for participant in participants or []:
        if not isinstance(participant, dict) or participant.get('team') != 'player':
            continue
        actor_id = str(participant.get('id') or '').strip()
        if actor_id and actor_id not in actor_ids:
            actor_ids.append(actor_id)
    return actor_ids


def _automatic_xp_reward_changes(
    *,
    state_before_dm: dict[str, Any],
    proposed_changes: list[dict[str, Any]],
    actor_id: str,
    turn_id: int,
    already_applied_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if _has_existing_xp_add([*already_applied_changes, *proposed_changes]):
        return []

    rewards: list[dict[str, Any]] = []
    rewarded_keys: set[str] = set()
    for change in proposed_changes:
        if not isinstance(change, dict) or not _combat_participant_defeated_by_change(change):
            continue
        participant_id = str(change.get('participantId') or change.get('enemyId') or '').strip()
        if not participant_id or participant_id in rewarded_keys:
            continue
        enemy = _combat_participant_by_id(state_before_dm, participant_id)
        if not _combat_participant_was_active_enemy(enemy):
            continue
        amount = _combat_xp_reward(enemy or {})
        if amount <= 0:
            continue
        rewarded_keys.add(participant_id)
        rewards.append(
            {
                'key': f'combat:{participant_id}',
                'amount': amount,
                'label': (enemy or {}).get('name') or participant_id,
            }
        )

    if _scene_allows_npc_defeat_xp(state_before_dm):
        for change in proposed_changes:
            if not isinstance(change, dict) or not _npc_defeated_by_change(change):
                continue
            npc = _npc_by_change(state_before_dm, change)
            if not _npc_was_rewardable_enemy(npc):
                continue
            npc_id = str((npc or {}).get('id') or change.get('npcId') or change.get('name') or '').strip()
            reward_key = f'npc:{npc_id}'
            if not npc_id or reward_key in rewarded_keys:
                continue
            amount = _npc_xp_reward(npc or {})
            if amount <= 0:
                continue
            rewarded_keys.add(reward_key)
            rewards.append(
                {
                    'key': reward_key,
                    'amount': amount,
                    'label': (npc or {}).get('name') or change.get('name') or npc_id,
                }
            )

    for change in proposed_changes:
        if not isinstance(change, dict) or str(change.get('type') or '') != 'quest.complete':
            continue
        quest = _quest_by_change(state_before_dm, change)
        if not quest or normalize_item_name(quest.get('status')) == 'completed':
            continue
        quest_id = str(quest.get('id') or change.get('questId') or change.get('title') or '').strip()
        reward_key = f'quest:{quest_id}'
        if not quest_id or reward_key in rewarded_keys:
            continue
        amount = _quest_xp_reward(quest)
        if amount <= 0:
            continue
        rewarded_keys.add(reward_key)
        rewards.append(
            {
                'key': reward_key,
                'amount': amount,
                'label': quest.get('title') or quest.get('name') or quest_id,
            }
        )

    if not rewards:
        return []

    reward_keys = [reward['key'] for reward in rewards]
    existing_ids = _change_ids([*already_applied_changes, *proposed_changes])
    total_amount = sum(int(reward['amount']) for reward in rewards)
    reward_labels = ', '.join(str(reward['label']) for reward in rewards if reward.get('label'))
    combat_rewarded = any(str(reward.get('key') or '').startswith(('combat:', 'npc:')) for reward in rewards)
    actor_ids = _active_session_actor_ids(state_before_dm)
    if not actor_ids:
        actor_ids = _turn_control_actor_ids(state_before_dm)
    if not actor_ids and combat_rewarded:
        actor_ids = _combat_player_actor_ids(state_before_dm)
    if not actor_ids:
        actor_ids = _player_actor_ids(state_before_dm)
    if not actor_ids and actor_id:
        actor_ids = [actor_id]

    changes: list[dict[str, Any]] = []
    for target_actor_id in actor_ids:
        change_id = stable_change_id(turn_id, 'post_dm', 'xp.award', target_actor_id, *reward_keys)
        if change_id in existing_ids:
            continue
        changes.append(
            {
                'id': change_id,
                'turnId': turn_id,
                'type': 'xp.add',
                'source': 'post_dm',
                'actorId': target_actor_id,
                'amount': total_amount,
                'reason': f"Automatic XP reward for {reward_labels or 'completed encounter objectives'}.",
                'visible': True,
                'rewardKeys': reward_keys,
            }
        )
    return changes


def _current_scene(state_before_dm: dict[str, Any]) -> dict[str, Any]:
    scene = state_before_dm.get('currentScene') if isinstance(state_before_dm, dict) else None
    return scene if isinstance(scene, dict) else {}


def _scene_danger_level(scene: dict[str, Any]) -> int:
    try:
        return max(0, min(10, int(scene.get('dangerLevel') or 0)))
    except (TypeError, ValueError):
        return 0


def _scene_update_signature(change: dict[str, Any]) -> tuple[Any, ...] | None:
    signature = _already_applied_signature(change)
    return signature if signature else None


def _target_label_regex(label: str) -> str:
    words = [re.escape(part) for part in re.findall(r'[a-z0-9]+', str(label or '').lower())]
    if not words:
        return ''
    return r'\b' + r'[\W_]+'.join(words) + r"(?:'s|s)?\b"


def _add_scene_danger_change(
    changes: list[dict[str, Any]],
    *,
    turn_id: int,
    scene: dict[str, Any],
    danger_level: int,
    reason: str,
    already: set[tuple[Any, ...]],
    scene_type: str | None = None,
    mood: str | None = None,
    combat_state: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        'id': stable_change_id(turn_id, 'post_dm', 'scene.update', 'current_scene', danger_level, scene_type, mood, combat_state),
        'turnId': turn_id,
        'type': 'scene.update',
        'source': 'post_dm',
        'reason': reason,
        'visible': True,
        'dangerLevel': max(0, min(10, danger_level)),
    }
    if scene_type and scene.get('sceneType') != scene_type:
        payload['sceneType'] = scene_type
    if mood and scene.get('mood') != mood:
        payload['mood'] = mood
    if combat_state and scene.get('combatState') != combat_state:
        payload['combatState'] = combat_state

    current_danger = _scene_danger_level(scene)
    if payload['dangerLevel'] == current_danger and not any(key in payload for key in ('sceneType', 'mood', 'combatState')):
        return
    signature = _scene_update_signature(payload)
    if signature and signature in already:
        return
    if signature and any(_scene_update_signature(existing) == signature for existing in changes):
        return
    changes.append(payload)


def _heuristic_scene_danger_changes(
    *,
    state_before_dm: dict[str, Any],
    dm_response: str,
    turn_id: int,
    already_applied_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    text = re.sub(r'\*+', '', dm_response or '')
    if not text.strip():
        return []
    if ROLL_PROMPT_PATTERN.search(text) and not COMBAT_DANGER_PATTERN.search(text) and not HIGH_DANGER_PATTERN.search(text):
        return []

    scene = _current_scene(state_before_dm)
    current_danger = _scene_danger_level(scene)
    changes: list[dict[str, Any]] = []
    already = _already_applied(already_applied_changes)

    explicit_match = EXPLICIT_DANGER_LEVEL_PATTERN.search(text)
    if explicit_match:
        _add_scene_danger_change(
            changes,
            turn_id=turn_id,
            scene=scene,
            danger_level=int(explicit_match.group('level')),
            reason='DM explicitly changed the scene danger level.',
            already=already,
        )
        return changes

    if LOWER_DANGER_PATTERN.search(text):
        next_combat_state = 'resolved' if scene.get('combatState') == 'active' else 'none'
        _add_scene_danger_change(
            changes,
            turn_id=turn_id,
            scene=scene,
            danger_level=0 if current_danger <= 3 else 1,
            mood='calm',
            combat_state=next_combat_state,
            reason='DM indicated the immediate scene danger has passed.',
            already=already,
        )
        return changes

    if COMBAT_DANGER_PATTERN.search(text):
        _add_scene_danger_change(
            changes,
            turn_id=turn_id,
            scene=scene,
            danger_level=max(current_danger, 8),
            scene_type='combat',
            mood='dangerous',
            combat_state='active',
            reason='DM indicated active combat or immediate hostile danger.',
            already=already,
        )
        return changes

    if HIGH_DANGER_PATTERN.search(text):
        _add_scene_danger_change(
            changes,
            turn_id=turn_id,
            scene=scene,
            danger_level=max(current_danger, 7),
            mood='dangerous',
            reason='DM indicated a severe scene hazard.',
            already=already,
        )
        return changes

    if MODERATE_DANGER_PATTERN.search(text):
        _add_scene_danger_change(
            changes,
            turn_id=turn_id,
            scene=scene,
            danger_level=max(current_danger, 5),
            mood='tense',
            reason='DM indicated a meaningful scene threat.',
            already=already,
        )
    return changes


def _heuristic_active_npc_changes(
    *,
    state_before_dm: dict[str, Any],
    dm_response: str,
    turn_id: int,
    already_applied_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scene = _current_scene(state_before_dm)
    text = str(dm_response or '').strip()
    if not text:
        return []
    normalized_text = normalize_item_name(text)
    scene_location_id = str(scene.get('locationId') or '').strip()
    current_active_ids = [
        str(value).strip()
        for value in scene.get('activeNpcIds', [])
        if str(value or '').strip()
    ] if isinstance(scene.get('activeNpcIds'), list) else []
    records: list[dict[str, Any]] = []
    for key in ('knownNpcs', 'partyNpcs', 'npcs'):
        values = state_before_dm.get(key)
        if isinstance(values, list):
            records.extend([record for record in values if isinstance(record, dict)])
    mentioned_ids: list[str] = []
    for npc in records:
        npc_id = str(npc.get('id') or npc.get('npcId') or '').strip()
        npc_name = str(npc.get('name') or npc.get('npcName') or '').strip()
        if not npc_id or not npc_name:
            continue
        npc_location_id = str(npc.get('locationId') or '').strip()
        if scene_location_id and npc_location_id and npc_location_id != scene_location_id and npc_id not in current_active_ids:
            continue
        labels = [npc_name, npc_id.replace('_', ' ')]
        aliases = npc.get('aliases') if isinstance(npc.get('aliases'), list) else []
        labels.extend(str(alias) for alias in aliases if str(alias or '').strip())
        if any((pattern := _target_label_regex(label)) and re.search(pattern, normalized_text, re.IGNORECASE) for label in labels):
            if npc_id not in mentioned_ids:
                mentioned_ids.append(npc_id)
    merged_ids = list(dict.fromkeys([*current_active_ids, *mentioned_ids]))
    if not mentioned_ids or merged_ids == current_active_ids:
        return []
    change = {
        'id': stable_change_id(turn_id, 'post_dm', 'scene.update', 'active_npcs', *merged_ids),
        'turnId': turn_id,
        'type': 'scene.update',
        'source': 'post_dm',
        'activeNpcIds': merged_ids[:8],
        'reason': 'DM response identified active scene NPCs.',
        'visible': False,
    }
    signature = _scene_update_signature(change)
    already = _already_applied(already_applied_changes)
    if signature and signature in already:
        return []
    return [change]


def _combat_enemy_match(sentence: str, enemy: dict[str, Any], enemy_count: int) -> bool:
    normalized = normalize_item_name(sentence)
    name = normalize_item_name(enemy.get('name'))
    enemy_id = normalize_item_name(enemy.get('id'))
    if name and name in normalized:
        return True
    if enemy_id and enemy_id in normalized:
        return True
    return enemy_count == 1 and re.search(r'\b(?:enemy|creature|monster|foe|it)\b', normalized)


def _combat_enemy_refs(enemy: dict[str, Any], enemy_count: int) -> list[str]:
    refs = []
    for value in (enemy.get('name'), enemy.get('id')):
        normalized = normalize_item_name(value)
        if normalized and normalized not in refs:
            refs.append(normalized)
    if enemy_count == 1:
        refs.extend(['enemy', 'creature', 'monster', 'foe', 'it'])
    return refs


def _regex_ref(ref: str) -> str:
    return re.escape(normalize_item_name(ref)).replace(r'\ ', r'\s+')


def _combat_damage_amount(sentence: str, enemy: dict[str, Any], enemy_count: int) -> int | None:
    normalized = normalize_item_name(sentence)
    for ref in _combat_enemy_refs(enemy, enemy_count):
        ref_pattern = _regex_ref(ref)
        patterns = [
            rf'\b(?:the\s+)?{ref_pattern}\b\s+(?:takes?|suffers?|loses?)\s+(?P<amount>{SMALL_NUMBER_PATTERN})\s*(?:points?\s+of\s+)?(?:[a-z]+\s+)?(?:damage|hp)\b',
            rf'\b(?:deal|deals|dealt|do|does|did|inflict|inflicts|inflicted)\s+(?P<amount>{SMALL_NUMBER_PATTERN})\s*(?:points?\s+of\s+)?(?:[a-z]+\s+)?(?:damage|hp)\b[^.!?\n]{{0,80}}\b(?:to|against|on)\s+(?:the\s+)?{ref_pattern}\b',
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match:
                return _amount_text(match.group('amount'))
    return None


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind('.', 0, start), text.rfind('!', 0, start), text.rfind('?', 0, start), text.rfind('\n', 0, start))
    right_candidates = [pos for pos in (text.find('.', end), text.find('!', end), text.find('?', end), text.find('\n', end)) if pos != -1]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1 : right + 1].strip()


def _sentence_describes_enemy_damage(sentence: str, state_before_dm: dict[str, Any], amount: int) -> bool:
    enemies = _combat_enemies(state_before_dm)
    return any(_combat_damage_amount(sentence, enemy, len(enemies)) == amount for enemy in enemies)


def _combat_condition_delta(sentence: str, enemy: dict[str, Any], enemy_count: int) -> tuple[str, str] | None:
    normalized = normalize_item_name(sentence)
    condition_pattern = '|'.join(sorted(COMBAT_CONDITIONS))
    for ref in _combat_enemy_refs(enemy, enemy_count):
        ref_pattern = _regex_ref(ref)
        remove_patterns = [
            rf'\b(?:the\s+)?{ref_pattern}\b\s+(?:is|are)\s+no\s+longer\s+(?P<condition>{condition_pattern})\b',
            rf'\b(?:the\s+)?{ref_pattern}\b\s+shakes?\s+off\s+(?:the\s+)?(?P<condition>{condition_pattern})\b',
            rf'\b(?:the\s+)?{ref_pattern}\b\s+recovers?\s+from\s+(?:being\s+)?(?P<condition>{condition_pattern})\b',
        ]
        for pattern in remove_patterns:
            match = re.search(pattern, normalized)
            if match:
                return 'combat.condition.remove', match.group('condition')
        add_patterns = [
            rf'\b(?:the\s+)?{ref_pattern}\b\s+(?:is|are|becomes?|gets?|remains?)\s+(?:now\s+)?(?P<condition>{condition_pattern})\b',
            rf'\b(?:the\s+)?{ref_pattern}\b\s+is\s+knocked\s+(?P<condition>prone|unconscious)\b',
        ]
        for pattern in add_patterns:
            match = re.search(pattern, normalized)
            if match:
                return 'combat.condition.add', match.group('condition')
    return None


def _combat_enemies(state_before_dm: dict[str, Any]) -> list[dict[str, Any]]:
    combat = state_before_dm.get('combat') if isinstance(state_before_dm, dict) else {}
    participants = combat.get('participants') if isinstance(combat, dict) else []
    enemies = []
    for participant in participants or []:
        if (
            isinstance(participant, dict)
            and participant.get('team') == 'enemy'
            and participant.get('isAlive') is not False
            and ((participant.get('hp') or {}).get('current') is None or (participant.get('hp') or {}).get('current', 1) > 0)
        ):
            enemies.append(participant)
    return enemies


def _combat_participant_ids(state_before_dm: dict[str, Any], *, team: str | None = None) -> set[str]:
    combat = state_before_dm.get('combat') if isinstance(state_before_dm, dict) else {}
    participants = combat.get('participants') if isinstance(combat, dict) else []
    ids: set[str] = set()
    for participant in participants or []:
        if not isinstance(participant, dict):
            continue
        if team and participant.get('team') != team:
            continue
        participant_id = str(participant.get('id') or '').strip()
        if participant_id:
            ids.add(participant_id)
    return ids


def _filter_misrouted_combat_health_changes(
    state_before_dm: dict[str, Any],
    changes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    enemy_ids = _combat_participant_ids(state_before_dm, team='enemy')
    if not enemy_ids:
        return changes, 0
    filtered: list[dict[str, Any]] = []
    removed = 0
    for change in changes:
        change_type = str(change.get('type') or '').strip()
        if change_type not in {'health.damage', 'health.heal'}:
            filtered.append(change)
            continue
        participant_id = str(change.get('participantId') or change.get('enemyId') or '').strip()
        actor_id = str(change.get('actorId') or change.get('actor_id') or '').strip()
        if participant_id or actor_id in enemy_ids:
            removed += 1
            continue
        filtered.append(change)
    return filtered, removed


def _add_combat_change(
    changes: list[dict[str, Any]],
    *,
    turn_id: int,
    change_type: str,
    participant_id: str | None = None,
    reason: str,
    already: set[tuple[Any, ...]],
    **payload,
) -> None:
    change = {
        'id': stable_change_id(
            turn_id,
            'post_dm',
            change_type,
            participant_id,
            payload.get('status'),
            payload.get('morale'),
            payload.get('condition'),
            payload.get('abilityId'),
            payload.get('toRangeBand'),
            (payload.get('hp') or {}).get('current') if isinstance(payload.get('hp'), dict) else None,
            payload.get('event'),
        ),
        'turnId': turn_id,
        'type': change_type,
        'source': 'post_dm',
        'reason': reason,
        'visible': True,
        **payload,
    }
    if participant_id:
        change['participantId'] = participant_id
    signature = _already_applied_signature(change)
    if signature and signature in already:
        return
    if signature and any(_already_applied_signature(existing) == signature for existing in changes):
        return
    changes.append(change)


def _heuristic_combat_changes(
    *,
    state_before_dm: dict[str, Any],
    dm_response: str,
    turn_id: int,
    already_applied_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    combat = state_before_dm.get('combat') if isinstance(state_before_dm, dict) else {}
    if not isinstance(combat, dict) or str(combat.get('status') or '') not in {'starting', 'active'}:
        return []
    text = re.sub(r'\*+', '', dm_response or '')
    if not text.strip():
        return []
    enemies = _combat_enemies(state_before_dm)
    if not enemies:
        return []
    changes: list[dict[str, Any]] = []
    already = _already_applied(already_applied_changes)
    resolved_enemy_ids: set[str] = set()
    for sentence in _item_extraction_sentences(text):
        for enemy in enemies:
            enemy_id = str(enemy.get('id') or '').strip()
            if not enemy_id or not _combat_enemy_match(sentence, enemy, len(enemies)):
                continue
            hp = enemy.get('hp') if isinstance(enemy.get('hp'), dict) else {}
            if ENEMY_DEFEATED_PATTERN.search(sentence):
                resolved_enemy_ids.add(enemy_id)
                _add_combat_change(
                    changes,
                    turn_id=turn_id,
                    change_type='combat.participant.update',
                    participant_id=enemy_id,
                    hp={'current': 0, 'max': hp.get('max'), 'temp': 0},
                    conditions=['defeated'],
                    isAlive=False,
                    isConscious=False,
                    reason=f"DM narration defeated {enemy.get('name') or enemy_id}.",
                    already=already,
                )
            elif ENEMY_FLEE_PATTERN.search(sentence):
                resolved_enemy_ids.add(enemy_id)
                _add_combat_change(
                    changes,
                    turn_id=turn_id,
                    change_type='combat.participant.update',
                    participant_id=enemy_id,
                    conditions=['fled'],
                    isAlive=False,
                    isConscious=True,
                    reason=f"DM narration made {enemy.get('name') or enemy_id} flee.",
                    already=already,
                )
            elif ENEMY_SURRENDER_PATTERN.search(sentence):
                resolved_enemy_ids.add(enemy_id)
                _add_combat_change(
                    changes,
                    turn_id=turn_id,
                    change_type='combat.participant.update',
                    participant_id=enemy_id,
                    conditions=['surrendered'],
                    isAlive=True,
                    isConscious=True,
                    reason=f"DM narration made {enemy.get('name') or enemy_id} surrender.",
                    already=already,
                )
            else:
                damage_amount = _combat_damage_amount(sentence, enemy, len(enemies))
                if damage_amount is not None:
                    raw_current_hp = (hp or {}).get('current')
                    if raw_current_hp is None:
                        raw_current_hp = (hp or {}).get('max')
                    try:
                        current_hp = max(0, int(raw_current_hp))
                    except (TypeError, ValueError):
                        continue
                    max_hp = (hp or {}).get('max')
                    next_hp = max(0, current_hp - damage_amount)
                    defeated = next_hp <= 0
                    _add_combat_change(
                        changes,
                        turn_id=turn_id,
                        change_type='combat.participant.update',
                        participant_id=enemy_id,
                        hp={'current': next_hp, 'max': max_hp, 'temp': (hp or {}).get('temp', 0)},
                        conditions=[*list(enemy.get('conditions') or []), *(['defeated'] if defeated else [])],
                        isAlive=not defeated,
                        isConscious=not defeated,
                        reason=f"DM narration dealt {damage_amount} damage to {enemy.get('name') or enemy_id}.",
                        already=already,
                    )
                    if defeated:
                        resolved_enemy_ids.add(enemy_id)
                    continue
                condition_delta = _combat_condition_delta(sentence, enemy, len(enemies))
                if condition_delta:
                    change_type, condition = condition_delta
                    _add_combat_change(
                        changes,
                        turn_id=turn_id,
                        change_type=change_type,
                        participant_id=enemy_id,
                        condition=condition,
                        reason=f"DM narration changed {enemy.get('name') or enemy_id} condition: {condition}.",
                        already=already,
                    )
    if COMBAT_END_PATTERN.search(text) or (resolved_enemy_ids and resolved_enemy_ids.issuperset({str(enemy.get('id')) for enemy in enemies if enemy.get('id')})):
        _add_combat_change(
            changes,
            turn_id=turn_id,
            change_type='combat.end',
            status='ended',
            summary='Combat ended from DM narration.',
            reason='DM indicated combat is over.',
            already=already,
        )
    return changes


def _change_resolves_combat(change: dict[str, Any]) -> bool:
    change_type = str(change.get('type') or '').strip()
    if change_type == 'combat.end':
        return True
    if change_type != 'scene.update':
        return False
    combat_state = normalize_item_name(change.get('combatState'))
    if combat_state in {'resolved', 'none'}:
        return True
    reason = normalize_item_name(change.get('reason'))
    return 'danger has passed' in reason or 'combat is over' in reason or 'threat dissolved' in reason


def _change_starts_or_reactivates_combat(change: dict[str, Any]) -> bool:
    change_type = str(change.get('type') or '').strip()
    if change_type == 'combat.start':
        return True
    if change_type != 'scene.update':
        return False
    combat_state = normalize_item_name(change.get('combatState'))
    scene_type = normalize_item_name(change.get('sceneType'))
    mood = normalize_item_name(change.get('mood'))
    danger = None
    if 'dangerLevel' in change:
        try:
            danger = max(0, min(10, int(change.get('dangerLevel'))))
        except (TypeError, ValueError):
            danger = None
    reason = normalize_item_name(change.get('reason'))
    return (
        combat_state == 'active'
        or scene_type == 'combat'
        or (mood == 'dangerous' and danger is not None and danger >= 8)
        or 'active combat' in reason
        or 'hostile danger' in reason
    )


def _resolve_combat_scene_conflicts(changes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    if not any(isinstance(change, dict) and _change_resolves_combat(change) for change in changes):
        return changes, False
    filtered: list[dict[str, Any]] = []
    removed = False
    for change in changes:
        if not isinstance(change, dict):
            continue
        if _change_starts_or_reactivates_combat(change) and not _change_resolves_combat(change):
            removed = True
            continue
        filtered.append(change)
    return filtered, removed


def _heuristic_extract(
    *,
    state_before_dm: dict[str, Any],
    dm_response: str,
    actor_id: str,
    turn_id: int,
    already_applied_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    already = _already_applied(already_applied_changes)
    text = re.sub(r'\*+', '', dm_response or '')

    for match in HEAL_PATTERN.finditer(text):
        amount = _amount_text(match.group('amount'))
        _add_change(
            changes,
            turn_id=turn_id,
            actor_id=actor_id,
            change_type='health.heal',
            amount=amount,
            reason=f'DM stated healing of {amount} HP.',
            already=already,
        )
    for match in DAMAGE_PATTERN.finditer(text):
        amount = _amount_text(match.group('amount'))
        sentence = _sentence_around(text, match.start(), match.end())
        if _sentence_describes_enemy_damage(sentence, state_before_dm, amount):
            continue
        _add_change(
            changes,
            turn_id=turn_id,
            actor_id=actor_id,
            change_type='health.damage',
            amount=amount,
            reason=f'DM stated damage of {amount}.',
            already=already,
        )
    for pattern, change_type in ((XP_GAIN_PATTERN, 'xp.add'), (XP_LOSS_PATTERN, 'xp.remove')):
        for match in pattern.finditer(text):
            amount = int(match.group('amount'))
            _add_change(
                changes,
                turn_id=turn_id,
                actor_id=actor_id,
                change_type=change_type,
                amount=amount,
                reason=f'DM stated XP change of {amount}.',
                already=already,
            )
    _heuristic_spell_learn_changes(
        text=text,
        changes=changes,
        turn_id=turn_id,
        actor_id=actor_id,
        already=already,
    )
    for pattern, change_type in ((CURRENCY_PATTERN, 'currency.add'), (CURRENCY_LOSS_PATTERN, 'currency.remove')):
        for match in pattern.finditer(text):
            currency = match.group('currency').lower()
            amount = int(match.group('amount'))
            _add_change(
                changes,
                turn_id=turn_id,
                actor_id=actor_id,
                change_type=change_type,
                amount=amount,
                currency=CURRENCY_WORDS.get(currency, currency),
                reason=f'DM stated {amount} {currency}.',
                already=already,
            )
    for match in EXPLICIT_INVENTORY_STATE_PATTERN.finditer(text):
        verb = normalize_item_name(match.group('verb'))
        change_type = 'inventory.remove' if verb in {'lose', 'loses', 'drop', 'drops', 'remove', 'removes', 'spend', 'spends', 'consume', 'consumes'} else 'inventory.add'
        item_name = _clean_item_label(match.group('alias')) if match.group('alias') else _clean_item(match.group('item'))
        if not item_name:
            continue
        _add_change(
            changes,
            turn_id=turn_id,
            actor_id=actor_id,
            change_type=change_type,
            itemName=item_name,
            quantity=int(match.group('quantity')),
            reason=f'DM explicit state change for {item_name}.',
            already=already,
        )
    for sentence in _item_extraction_sentences(text):
        if _is_conditional_item_context(sentence):
            continue
        for pattern, change_type in ((ITEM_EQUIP_PATTERN, 'inventory.equip'), (ITEM_UNEQUIP_PATTERN, 'inventory.unequip')):
            for match in pattern.finditer(sentence):
                item_name = _clean_item(match.group('item'))
                if not _looks_like_item(item_name):
                    continue
                if _inventory_change_already_applied(
                    change_type=change_type,
                    actor_id=actor_id,
                    item_name=item_name,
                    already_applied_changes=already_applied_changes,
                ):
                    continue
                _add_change(
                    changes,
                    turn_id=turn_id,
                    actor_id=actor_id,
                    change_type=change_type,
                    itemName=item_name,
                    reason=f'DM stated equipment {change_type.split(".")[-1]} for {item_name}.',
                    already=already,
                )
        for pattern, change_type in ((ITEM_GAIN_PATTERN, 'inventory.add'), (ITEM_LOSS_PATTERN, 'inventory.remove')):
            for match in pattern.finditer(sentence):
                if change_type == 'inventory.add' and _is_observed_only_item_context(sentence, match.group('verb')):
                    continue
                item_name = _clean_item(match.group('item'))
                if not _looks_like_item(item_name):
                    continue
                if _inventory_change_already_applied(
                    change_type=change_type,
                    actor_id=actor_id,
                    item_name=item_name,
                    already_applied_changes=already_applied_changes,
                ):
                    continue
                _add_change(
                    changes,
                    turn_id=turn_id,
                    actor_id=actor_id,
                    change_type=change_type,
                    itemName=item_name,
                    quantity=1,
                    reason=f'DM stated inventory {change_type.split(".")[-1]} for {item_name}.',
                    already=already,
                )

    scene_changes = _heuristic_scene_danger_changes(
        state_before_dm=state_before_dm,
        dm_response=dm_response,
        turn_id=turn_id,
        already_applied_changes=[*already_applied_changes, *changes],
    )
    changes.extend(scene_changes)
    active_npc_changes = _heuristic_active_npc_changes(
        state_before_dm=state_before_dm,
        dm_response=dm_response,
        turn_id=turn_id,
        already_applied_changes=[*already_applied_changes, *changes],
    )
    changes.extend(active_npc_changes)
    combat_changes = _heuristic_combat_changes(
        state_before_dm=state_before_dm,
        dm_response=dm_response,
        turn_id=turn_id,
        already_applied_changes=[*already_applied_changes, *changes],
    )
    changes.extend(combat_changes)
    xp_reward_changes = _automatic_xp_reward_changes(
        state_before_dm=state_before_dm,
        proposed_changes=changes,
        actor_id=actor_id,
        turn_id=turn_id,
        already_applied_changes=already_applied_changes,
    )
    changes.extend(xp_reward_changes)
    changes, conflict_resolved = _resolve_combat_scene_conflicts(changes)
    notes = ['heuristic_post_dm'] if changes else []
    if scene_changes and 'heuristic_scene_danger' not in notes:
        notes.append('heuristic_scene_danger')
    if active_npc_changes and 'heuristic_active_npcs' not in notes:
        notes.append('heuristic_active_npcs')
    if combat_changes and 'heuristic_combat_outcomes' not in notes:
        notes.append('heuristic_combat_outcomes')
    if xp_reward_changes and 'automatic_xp_award' not in notes:
        notes.append('automatic_xp_award')
    if conflict_resolved and 'resolved_combat_scene_conflict' not in notes:
        notes.append('resolved_combat_scene_conflict')
    return {'proposedChanges': changes, 'uncertainChanges': [], 'notes': notes}


def extract_post_dm_outcomes(
    *,
    state_before_dm: dict[str, Any],
    player_message: str,
    validated_actions: dict[str, Any],
    already_applied_changes: list[dict[str, Any]],
    dm_response: str,
    recent_timeline: list[dict[str, Any]],
    actor_id: str,
    turn_id: int,
) -> dict[str, Any]:
    helper_payload: dict[str, Any] | None = None
    helper_attempted = False
    helper_schema_valid = False
    helper_model: str | None = None
    helper_raw_text: str | None = None
    helper_raw_preview: str | None = None
    helper_error: str | None = None
    helper_enabled = _helper_enabled()
    fallback_reason = 'helper_disabled' if not helper_enabled else 'empty_dm_response'

    if helper_enabled and dm_response.strip():
        helper_attempted = True
        fallback_reason = 'helper_not_attempted'
        prompt = build_post_dm_prompt(
            state_before_dm=state_before_dm,
            player_message=player_message,
            validated_actions=validated_actions,
            already_applied_changes=already_applied_changes,
            dm_response=dm_response,
            recent_timeline=recent_timeline,
        )
        try:
            response = get_helper_provider().generate(
                ProviderRequest(prompt=prompt, system_message=POST_DM_SYSTEM_MESSAGE)
            )
            helper_model = response.model
            helper_raw_text = str(response.text or '')
            helper_raw_preview = helper_raw_text[:HELPER_RAW_PREVIEW_LIMIT]
            helper_payload = extract_json_object(response.text)
            helper_schema_valid = _post_payload_schema_valid(helper_payload)
            if helper_schema_valid:
                telemetry_metric('state_pipeline.post_dm_helper.success_total', 1, tags={'model': response.model})
            else:
                fallback_reason = 'helper_json_invalid' if helper_payload is None else 'helper_schema_invalid'
                telemetry_event(
                    'state_pipeline.post_dm_helper.invalid_json',
                    payload={'model': response.model, 'reason': fallback_reason},
                    severity='warning',
                )
        except Exception as exc:
            fallback_reason = 'helper_error'
            helper_error = str(exc)[:300]
            telemetry_event(
                'state_pipeline.post_dm_helper.failed',
                payload={'error': helper_error},
                severity='warning',
            )

    helper_debug = {
        'source': 'helper' if helper_schema_valid else 'heuristic',
        'helperAttempted': helper_attempted,
        'helperSchemaValid': helper_schema_valid,
        'helperModel': helper_model,
        'helperRawText': helper_raw_text,
        'helperRawPreview': helper_raw_preview,
        'helperParsed': helper_payload if helper_schema_valid else None,
        'helperError': helper_error,
        'fallbackRan': False,
        'fallbackReason': None,
    }

    if helper_schema_valid:
        normalized = normalize_post_extraction(helper_payload, fallback_actor_id=actor_id)
        _assign_turn_scoped_change_ids(normalized['proposedChanges'], turn_id=turn_id)
        filtered_changes, filtered_count = _filter_misrouted_combat_health_changes(
            state_before_dm,
            normalized.get('proposedChanges') or [],
        )
        if filtered_count:
            normalized['proposedChanges'] = filtered_changes
        scene_changes = _heuristic_scene_danger_changes(
            state_before_dm=state_before_dm,
            dm_response=dm_response,
            turn_id=turn_id,
            already_applied_changes=[*already_applied_changes, *(normalized.get('proposedChanges') or [])],
        )
        active_npc_changes = _heuristic_active_npc_changes(
            state_before_dm=state_before_dm,
            dm_response=dm_response,
            turn_id=turn_id,
            already_applied_changes=[*already_applied_changes, *(normalized.get('proposedChanges') or []), *scene_changes],
        )
        combat_changes = _heuristic_combat_changes(
            state_before_dm=state_before_dm,
            dm_response=dm_response,
            turn_id=turn_id,
            already_applied_changes=[*already_applied_changes, *(normalized.get('proposedChanges') or []), *scene_changes, *active_npc_changes],
        )
        if scene_changes or active_npc_changes or combat_changes:
            normalized['proposedChanges'] = [
                *(normalized.get('proposedChanges') or []),
                *scene_changes,
                *active_npc_changes,
                *combat_changes,
            ]
        xp_reward_changes = _automatic_xp_reward_changes(
            state_before_dm=state_before_dm,
            proposed_changes=normalized.get('proposedChanges') or [],
            actor_id=actor_id,
            turn_id=turn_id,
            already_applied_changes=already_applied_changes,
        )
        if xp_reward_changes:
            normalized['proposedChanges'] = [*(normalized.get('proposedChanges') or []), *xp_reward_changes]
        resolved_changes, conflict_resolved = _resolve_combat_scene_conflicts(normalized.get('proposedChanges') or [])
        if conflict_resolved:
            normalized['proposedChanges'] = resolved_changes
        notes = list(normalized.get('notes') or [])
        if 'helper_post_dm' not in notes:
            notes.append('helper_post_dm')
        if scene_changes and 'heuristic_scene_danger' not in notes:
            notes.append('heuristic_scene_danger')
        if active_npc_changes and 'heuristic_active_npcs' not in notes:
            notes.append('heuristic_active_npcs')
        if combat_changes and 'heuristic_combat_outcomes' not in notes:
            notes.append('heuristic_combat_outcomes')
        if xp_reward_changes and 'automatic_xp_award' not in notes:
            notes.append('automatic_xp_award')
        if filtered_count and 'filtered_misrouted_combat_health' not in notes:
            notes.append('filtered_misrouted_combat_health')
        if conflict_resolved and 'resolved_combat_scene_conflict' not in notes:
            notes.append('resolved_combat_scene_conflict')
        normalized['notes'] = notes
        return _attach_debug(normalized, helper_debug)

    fallback = _heuristic_extract(
        state_before_dm=state_before_dm,
        dm_response=dm_response,
        actor_id=actor_id,
        turn_id=turn_id,
        already_applied_changes=already_applied_changes,
    )
    normalized = normalize_post_extraction(fallback, fallback_actor_id=actor_id)
    helper_debug['fallbackRan'] = True
    helper_debug['fallbackReason'] = fallback_reason
    return _attach_debug(normalized, helper_debug)
