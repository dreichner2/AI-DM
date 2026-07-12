"""Inventory extraction, validation, and mutation for emergent canon."""

from __future__ import annotations

import re

from aidm_server.canon_text import int_or_default, normalized_name, positive_int
from aidm_server.database import db
from aidm_server.models import DmTurn, Player, safe_json_dumps, safe_json_loads


ITEM_WEIGHT_LOOKUP = {
    'arrow': 0.05,
    'bone': 0.2,
    'bone shard': 0.1,
    'book': 5,
    'chain mail': 55,
    'club': 2,
    'coin': 0.02,
    'coins': 0.02,
    'copper coin': 0.02,
    'copper coins': 0.02,
    'copper piece': 0.02,
    'copper pieces': 0.02,
    'dagger': 1,
    'feather': 0.1,
    'gem': 0.1,
    'healing potion': 0.5,
    'key': 0.1,
    'leather armor': 10,
    'longsword': 3,
    'potion': 0.5,
    'quarterstaff': 4,
    'ration': 2,
    'rock': 1,
    'rope': 10,
    'shield': 6,
    'shortsword': 2,
    'silver key': 0.1,
    'silver piece': 0.02,
    'silver pieces': 0.02,
    'stick': 0.5,
    'sword': 3,
    'torch': 1,
}
ITEM_WEIGHT_RULES = (
    (re.compile(r'\b(?:book|journal|tome)\b'), 5),
    (re.compile(r'\b(?:chain mail|mail)\b'), 55),
    (re.compile(r'\b(?:armor|armour)\b'), 10),
    (re.compile(r'\b(?:shield|buckler)\b'), 6),
    (re.compile(r'\b(?:greatsword|maul)\b'), 6),
    (re.compile(r'\b(?:sword|blade)\b'), 3),
    (re.compile(r'\b(?:dagger|knife)\b'), 1),
    (re.compile(r'\b(?:staff|quarterstaff)\b'), 4),
    (re.compile(r'\b(?:bow|crossbow)\b'), 2),
    (re.compile(r'\b(?:rope|chain)\b'), 10),
    (re.compile(r'\b(?:potion|vial|flask)\b'), 0.5),
    (re.compile(r'\b(?:torch|lantern|candle)\b'), 1),
    (re.compile(r'\b(?:ration|food|meal)\b'), 2),
    (re.compile(r'\b(?:coin|coins|copper|silver|electrum|platinum|piece|pieces)\b'), 0.02),
    (re.compile(r'\b(?:key|gem|ring|feather|shard)\b'), 0.1),
    (re.compile(r'\b(?:rock|stone)\b'), 1),
    (re.compile(r'\b(?:stick|twig)\b'), 0.5),
    (re.compile(r'\b(?:bone)\b'), 0.2),
)
NUMBER_WORDS = {
    1: 'one',
    2: 'two',
    3: 'three',
    4: 'four',
    5: 'five',
    6: 'six',
    7: 'seven',
    8: 'eight',
    9: 'nine',
    10: 'ten',
    11: 'eleven',
    12: 'twelve',
    20: 'twenty',
}
ITEM_NAME_CONNECTORS = {'of'}
NON_ITEM_HEADWORDS = {
    'advice',
    'all',
    'answer',
    'attention',
    'belongings',
    'chance',
    'choice',
    'equipment',
    'everything',
    'fear',
    'gear',
    'glance',
    'hope',
    'inventory',
    'items',
    'look',
    'news',
    'nod',
    'path',
    'permission',
    'promise',
    'rumor',
    'silence',
    'smile',
    'stare',
    'story',
    'time',
    'trouble',
    'truth',
    'warning',
    'way',
    'word',
}
NON_ITEM_TOKENS = {
    'across',
    'along',
    'around',
    'away',
    'before',
    'behind',
    'deeper',
    'down',
    'further',
    'immediately',
    'inside',
    'into',
    'onto',
    'outside',
    'through',
    'toward',
    'towards',
    'under',
    'within',
}
INVENTORY_ACTIONS = {'pick_up', 'buy', 'use', 'drop', 'give', 'sell', 'equip', 'unequip'}
OWNED_ITEM_ACTIONS = {'use', 'drop', 'give', 'sell', 'equip', 'unequip'}

INVENTORY_GAIN_PATTERNS = [
    re.compile(
        r'\byou\s+(?:take|pick up|pocket|claim|receive|accept|gather|loot|carry away)\s+'
        r'(?:the|a|an|some|your)?\s*([a-z][a-z0-9\' -]{1,40}?)(?:\s+and\b|\s+from\b|[.,;!?]|$)',
        re.IGNORECASE,
    ),
    re.compile(
        r'\b[A-Z][a-z]{2,}\s+(?:hands|gives|passes|offers)\s+you\s+'
        r'(?:the|a|an|some)?\s*([a-z][a-z0-9\' -]{1,40}?)(?:\s+and\b|\s+with\b|[.,;!?]|$)',
        re.IGNORECASE,
    ),
]
INVENTORY_LOSS_PATTERNS = [
    re.compile(
        r'\byou\s+(?:drop|dropped|give|gave|hand over|leave behind|left behind|discard|discarded|consume|consumed|use up|spend|spent|release|released|set down|put down|toss|tossed)\s+'
        r'(?:the|a|an|some|your)?\s*([a-z][a-z0-9\' -]{1,40}?)(?:\s+and\b|\s+to\b|\s+on\b|[.,;!?]|$)',
        re.IGNORECASE,
    ),
    re.compile(
        r'\byou\s+(?:open\s+your\s+hands\s+and\s+)?let\s+'
        r'(?:the|a|an|some|your)?\s*([a-z][a-z0-9\' -]{1,40}?)\s+fall\b',
        re.IGNORECASE,
    ),
]
EXPLICIT_INVENTORY_GAIN_PATTERNS = [
    re.compile(
        r'\b(?:state change:\s*)?(?:player\s+\d+|you|[A-Z][a-z0-9_-]{1,40})\s+'
        r'\*{0,2}(?:gain|gains|add|adds|receive|receives)\*{0,2}\s+'
        r'(?P<quantity>\d{1,3})\s+'
        r'(?P<item>[a-z][a-z0-9\' -]{0,60}?)\s+'
        r'(?:to|into|in)\s+(?:their|your|his|her)?\s*inventory\b',
        re.IGNORECASE,
    ),
]
DROP_ALL_INVENTORY_PATTERNS = [
    re.compile(
        r'\byou\s+(?:drop|dropped|discard|discarded|leave behind|left behind|set down|put down)\s+'
        r'(?:everything|all(?:\s+(?:of\s+)?(?:your|their|his|her|my)?\s*)?'
        r'(?:items|inventory|gear|belongings|possessions|equipment|pack))\b',
        re.IGNORECASE,
    ),
    re.compile(
        r'\byou\s+empty\s+(?:your|their|his|her|my)?\s*(?:pack|bag|inventory|pockets)\b',
        re.IGNORECASE,
    ),
    re.compile(
        r'\byou\s+(?:open\s+your\s+hands\s+and\s+)?let\s+everything\s+fall\b',
        re.IGNORECASE,
    ),
]
EXPLICIT_INVENTORY_LOSS_PATTERNS = [
    re.compile(
        r'\b(?:state change:\s*)?(?:player\s+\d+|you|[A-Z][a-z0-9_-]{1,40})\s+'
        r'\*{0,2}(?:lose|loses|drop|drops|remove|removes|spend|spends|consume|consumes)\*{0,2}\s+'
        r'(?P<quantity>\d{1,3})\s+'
        r'(?P<item>[a-z][a-z0-9\' -]{0,60}?)\s+'
        r'(?:from|out of)\s+(?:their|your|his|her)?\s*inventory\b',
        re.IGNORECASE,
    ),
    re.compile(
        r'\bstate change:\s*(?:player\s+\d+|you|[A-Z][a-z0-9_-]{1,40})\s+'
        r'\*{0,2}(?:lose|loses|drop|drops|remove|removes|spend|spends|consume|consumes)\*{0,2}\s+'
        r'(?P<quantity>\d{1,3})\s+'
        r'(?P<item>[a-z][a-z0-9\' -]{0,60}?)(?:[.)]|$)',
        re.IGNORECASE,
    ),
]
EXPLICIT_STATE_CHANGE_PATTERN = re.compile(
    r'\bstate change:\s*(?:player\s+\d+|you|[A-Z][a-z0-9_-]{1,40})\s+'
    r'\*{0,2}(?P<verb>gain|gains|add|adds|receive|receives|take|takes|pick up|picks up|'
    r'lose|loses|drop|drops|remove|removes|spend|spends|consume|consumes)\*{0,2}\s+'
    r'(?P<items>[^.\n]+)',
    re.IGNORECASE,
)
EXPLICIT_STATE_ITEM_PATTERN = re.compile(
    r'(?:^|,|\band\b)\s*(?P<quantity>\d{1,4})\s+'
    r'(?P<item>[a-z][a-z0-9\' -]{0,80}?)'
    r'(?:\s*\((?P<alias>[^)]{1,100})\))?'
    r'(?=\s*(?:,|\band\b|\bto\b|\binto\b|\bin\b|\bfrom\b|\bout of\b|$))',
    re.IGNORECASE,
)
PROVIDER_INVENTORY_FACT_PATTERN = re.compile(
    r'\b(?:inventory|pack|pouch|character|player|you|their inventory|your inventory)\b'
    r'[^.!?\n]{0,80}?\b(?:holds?|has|carries|contains|now carries|now holds)\s+'
    r'(?P<quantity>\d{1,4})\s+'
    r'(?P<item>[a-z][a-z0-9\' -]{0,80}?)'
    r'(?:\s*\((?P<alias>[^)]{1,100})\))?(?:[.!?]|$)',
    re.IGNORECASE,
)
PROVIDER_INVENTORY_LOSS_FACT_PATTERN = re.compile(
    r'\b(?:inventory|pack|pouch|character|player|you|their inventory|your inventory)\b'
    r'[^.!?\n]{0,80}?\b(?:no longer|does not|do not|doesnt|dont|lost|dropped|removed|spent|consumed)\s+'
    r'(?:have|hold|carry|contains?)?\s*'
    r'(?P<quantity>\d{1,4})\s+'
    r'(?P<item>[a-z][a-z0-9\' -]{0,80}?)'
    r'(?:\s*\((?P<alias>[^)]{1,100})\))?(?:[.!?]|$)',
    re.IGNORECASE,
)


def clean_inventory_item_name(item_name: str | None) -> str | None:
    candidate = str(item_name or '').strip(" \t\r\n'\"`")
    if not candidate:
        return None
    candidate = re.sub(r'\b(?:the|a|an|some|your|their|his|her)\b\s+', '', candidate, flags=re.IGNORECASE).strip()
    candidate = re.sub(r'\s+', ' ', candidate).strip(' -')
    if not candidate:
        return None
    return candidate[:80]


def looks_like_inventory_item(item_name: str | None) -> bool:
    candidate = clean_inventory_item_name(item_name)
    if not candidate:
        return False

    normalized = normalized_name(candidate)
    tokens = normalized.split()
    if not tokens or len(tokens) > 6:
        return False

    if any(token in NON_ITEM_TOKENS for token in tokens):
        return False

    if any(token not in ITEM_NAME_CONNECTORS and len(token) <= 1 for token in tokens):
        return False

    head = tokens[-1]
    if head in NON_ITEM_HEADWORDS:
        return False

    return any(token not in ITEM_NAME_CONNECTORS for token in tokens)


def _compact_weight(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    rounded = round(max(0.0, float(value)), 2)
    return int(rounded) if rounded.is_integer() else rounded


def _coerce_item_weight(value) -> float | int | None:
    if isinstance(value, int | float):
        return _compact_weight(value)
    if isinstance(value, str):
        match = re.search(r'\d+(?:\.\d+)?', value)
        if match:
            return _compact_weight(float(match.group(0)))
    return None


def item_weight_for_name(item_name: str | None) -> float | int | None:
    clean_name = clean_inventory_item_name(item_name)
    if not clean_name or not looks_like_inventory_item(clean_name):
        return None

    item_key = normalized_name(clean_name)
    if item_key in ITEM_WEIGHT_LOOKUP:
        return ITEM_WEIGHT_LOOKUP[item_key]

    for pattern, weight in ITEM_WEIGHT_RULES:
        if pattern.search(item_key):
            return weight

    return 1


def _normalized_inventory_item(name: str, quantity: int = 1, weight=None, extra: dict | None = None) -> dict:
    item = {
        'name': name,
        'quantity': positive_int(quantity),
    }
    item_weight = _coerce_item_weight(weight)
    if item_weight is None:
        item_weight = item_weight_for_name(name)
    if item_weight is not None:
        item['weight'] = item_weight
    for key in (
        'id',
        'type',
        'subtype',
        'equipped',
        'slot',
        'equipmentSlot',
        'aliases',
        'tags',
        'lastUsedAtTurn',
        'lastEquippedAtTurn',
        'favorite',
        'metadata',
    ):
        if extra and key in extra and extra[key] not in (None, '', [], {}):
            item[key] = extra[key]
    return item


def append_inventory_change(patch: dict, action: str, item_name: str | None, quantity: int = 1):
    clean_name = clean_inventory_item_name(item_name)
    if not clean_name or not looks_like_inventory_item(clean_name):
        return
    normalized_item = normalized_name(clean_name)
    existing = next(
        (
            change
            for change in patch['inventory_changes']
            if normalized_name(change.get('item_name')) == normalized_item and change.get('action') == action
        ),
        None,
    )
    if existing:
        existing['quantity'] = positive_int(existing.get('quantity', 1)) + positive_int(quantity)
        return

    patch['inventory_changes'].append(
        {
            'action': action,
            'item_name': clean_name,
            'quantity': positive_int(quantity),
        }
    )

    if not any(
        entity.get('entity_type') == 'item' and normalized_name(entity.get('name')) == normalized_item
        for entity in patch['entities']
    ):
        patch['entities'].append(
            {
                'entity_type': 'item',
                'name': clean_name,
                'summary': 'Explicitly involved in a deterministic inventory consequence.',
                'status': 'active',
            }
        )


def _provider_item_name(match: re.Match) -> str | None:
    alias = clean_inventory_item_name(match.groupdict().get('alias'))
    item_name = clean_inventory_item_name(match.groupdict().get('item'))
    if alias and looks_like_inventory_item(alias):
        return alias
    return item_name


def _inventory_change_exists(patch: dict, action: str, item_name: str | None, quantity: int) -> bool:
    item_key = normalized_name(item_name)
    if not item_key:
        return False
    return any(
        isinstance(change, dict)
        and change.get('action') == action
        and normalized_name(change.get('item_name')) == item_key
        and positive_int(change.get('quantity', 1)) >= positive_int(quantity)
        for change in patch.get('inventory_changes', [])
    )


def extract_inventory_changes_from_text(text: str, patterns: list[re.Pattern], action: str, patch: dict):
    for pattern in patterns:
        for match in pattern.finditer(text or ''):
            append_inventory_change(patch, action=action, item_name=match.group(1))


def extract_explicit_inventory_state_changes_from_text(text: str, patch: dict):
    for action, patterns in (
        ('acquire', EXPLICIT_INVENTORY_GAIN_PATTERNS),
        ('lose', EXPLICIT_INVENTORY_LOSS_PATTERNS),
    ):
        for pattern in patterns:
            for match in pattern.finditer(text or ''):
                append_inventory_change(
                    patch,
                    action=action,
                    item_name=_provider_item_name(match) or match.group('item'),
                    quantity=positive_int(match.group('quantity')),
                )

    for state_match in EXPLICIT_STATE_CHANGE_PATTERN.finditer(text or ''):
        verb = normalized_name(state_match.group('verb'))
        action = 'lose' if verb in {'lose', 'loses', 'drop', 'drops', 'remove', 'removes', 'spend', 'spends', 'consume', 'consumes'} else 'acquire'
        items_text = re.sub(
            r'\b(?:to|into|in|from|out of)\s+(?:their|your|his|her)?\s*inventory\b.*$',
            '',
            state_match.group('items') or '',
            flags=re.IGNORECASE,
        )
        for item_match in EXPLICIT_STATE_ITEM_PATTERN.finditer(items_text):
            item_name = _provider_item_name(item_match)
            quantity = positive_int(item_match.group('quantity'))
            if _inventory_change_exists(patch, action, item_name, quantity):
                continue
            append_inventory_change(
                patch,
                action=action,
                item_name=item_name,
                quantity=quantity,
            )


def _quantity_has_text_evidence(quantity: int, text: str) -> bool:
    normalized = normalized_name(text)
    if re.search(rf'\b{int(quantity)}\b', text or ''):
        return True
    quantity_word = NUMBER_WORDS.get(int(quantity))
    return bool(quantity_word and re.search(rf'\b{re.escape(quantity_word)}\b', normalized))


def _item_has_text_evidence(item_name: str | None, text: str) -> bool:
    item_key = normalized_name(item_name)
    text_key = normalized_name(text)
    if not item_key or not text_key:
        return False
    if item_key in text_key:
        return True
    tokens = [
        token
        for token in item_key.split()
        if token not in {'ancient', 'old', 'small', 'large', 'piece', 'pieces'}
    ]
    return bool(tokens) and all(token in text_key for token in tokens)


def _action_has_text_evidence(action: str, text: str) -> bool:
    normalized = normalized_name(text)
    if action == 'acquire':
        return bool(
            re.search(
                r'\b(?:gain|gains|take|takes|took|pick|picks|picked|collect|collects|collected|'
                r'gather|gathers|gathered|receive|receives|received|sweep|sweeps|swept|'
                r'hold|holds|held|carries|carry|pocket|pockets|pocketed)\b',
                normalized,
            )
        )
    return bool(
        re.search(
            r'\b(?:lose|loses|lost|drop|drops|dropped|remove|removes|removed|spend|spends|spent|'
            r'consume|consumes|consumed|use|uses|used|give|gives|gave|sell|sells|sold)\b',
            normalized,
        )
    )


def _append_verified_provider_change(
    patch: dict,
    change: dict,
    dm_text: str,
    verifier_text: str = '',
):
    action = str(change.get('action') or '').strip().lower()
    if action not in {'acquire', 'lose'}:
        return
    item_name = clean_inventory_item_name(change.get('item_name'))
    if not item_name or not looks_like_inventory_item(item_name):
        return
    quantity = positive_int(change.get('quantity', 1))
    if (
        not _item_has_text_evidence(item_name, dm_text)
        or not _quantity_has_text_evidence(quantity, dm_text)
        or not _action_has_text_evidence(action, f'{dm_text}\n{verifier_text}')
    ):
        return
    append_inventory_change(patch, action=action, item_name=item_name, quantity=quantity)


def append_verified_provider_inventory_changes(provider_patch: dict, text: str, patch: dict):
    """Accept model-suggested inventory changes only when the DM text proves them."""

    for change in provider_patch.get('inventory_changes') or []:
        if isinstance(change, dict):
            _append_verified_provider_change(patch, change, text or '')

    for fact in provider_patch.get('facts') or []:
        if not isinstance(fact, dict):
            continue
        predicate = normalized_name(fact.get('predicate'))
        value_text = str(fact.get('value_text') or '')
        if 'inventory' not in predicate and 'possession' not in predicate and 'carr' not in value_text.lower():
            continue
        for pattern, action in (
            (PROVIDER_INVENTORY_FACT_PATTERN, 'acquire'),
            (PROVIDER_INVENTORY_LOSS_FACT_PATTERN, 'lose'),
        ):
            for match in pattern.finditer(value_text):
                item_name = _provider_item_name(match)
                _append_verified_provider_change(
                    patch,
                    {
                        'action': action,
                        'item_name': item_name,
                        'quantity': positive_int(match.group('quantity')),
                    },
                    text or '',
                    value_text,
                )


def _sentences_with_item(text: str, item_name: str) -> list[str]:
    item_key = normalized_name(item_name)
    if not item_key:
        return []
    sentences = re.split(r'(?<=[.!?])\s+|\n+', text or '')
    return [sentence for sentence in sentences if item_key in normalized_name(sentence)]


def _has_negative_outcome(sentence: str) -> bool:
    normalized = normalized_name(sentence)
    negative_markers = {
        'cannot',
        'cant',
        'did not',
        'do not',
        'does not',
        'dont',
        'fails',
        'failed',
        'failure',
        'misses',
        'missed',
        'no inventory change',
        'nothing but air',
        'nothing to',
        'refuses',
        'refused',
        'denies',
        'denied',
        'crumbles',
        'crumbled',
        'breaks',
        'broken',
        'shatters',
        'shattered',
        'vanishes',
        'vanished',
        'out of reach',
        'not enough',
        'too expensive',
        'unable',
        'already lying',
        'already on',
        'already there',
        'already dropped',
        'already on the ground',
        'remains on the ground',
    }
    return any(marker in normalized for marker in negative_markers)


def _drop_all_inventory_confirmed(text: str) -> bool:
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', text or ''):
        if _has_negative_outcome(sentence):
            continue
        if any(pattern.search(sentence) for pattern in DROP_ALL_INVENTORY_PATTERNS):
            return True
    return False


def append_drop_all_inventory_changes_from_text(turn: DmTurn, text: str, patch: dict):
    if not _drop_all_inventory_confirmed(text):
        return

    player = db.session.get(Player, turn.player_id) if turn.player_id else None
    if not player:
        return

    for item in load_inventory(player.inventory):
        item_name = item.get('name')
        quantity = positive_int(item.get('quantity', 1))
        normalized_item = normalized_name(item_name)
        existing = next(
            (
                change
                for change in patch.get('inventory_changes', [])
                if change.get('action') == 'lose'
                and normalized_name(change.get('item_name')) == normalized_item
            ),
            None,
        )
        if existing:
            existing['quantity'] = max(positive_int(existing.get('quantity', 1)), quantity)
            continue
        append_inventory_change(patch, action='lose', item_name=item_name, quantity=quantity)


def _sentence_confirms_action(sentence: str, inventory_action: str) -> bool:
    normalized = normalized_name(sentence)
    if _has_negative_outcome(sentence):
        return False
    if inventory_action == 'pick_up':
        return bool(
            re.search(
                r'\b(?:pick(?:s)? up|take(?:s)?|grab(?:s)?|claim(?:s)?|collect(?:s)?|gather(?:s)?|pocket(?:s)?|retrieve(?:s)?|lift(?:s)?|hold(?:s)?|have|has|carry|carries)\b',
                normalized,
            )
        )
    if inventory_action == 'buy':
        return bool(
            re.search(
                r'\b(?:buy(?:s)?|bought|purchase(?:s)?|purchased|pay(?:s)?|paid|sells?|sold|hands?|gives?|passes?)\b',
                normalized,
            )
        )
    if inventory_action == 'use':
        return bool(re.search(r'\b(?:consume(?:s)?|consumed|use(?:s)? up|drink(?:s)?|ate|eat(?:s)?)\b', normalized))
    if inventory_action == 'drop':
        return bool(re.search(r'\b(?:drop(?:s)?|dropped|leave(?:s)? behind|discard(?:s)?|discarded)\b', normalized))
    if inventory_action == 'give':
        return bool(re.search(r'\b(?:give(?:s)?|gave|hand(?:s)? over|handed over|pass(?:es)?|passed)\b', normalized))
    if inventory_action == 'sell':
        return bool(re.search(r'\b(?:sell(?:s)?|sold|trade(?:s)?|traded|barter(?:s)?|bartered)\b', normalized))
    return False


def inventory_change_from_intent_outcome(turn: DmTurn, dm_output: str) -> dict | None:
    metadata = safe_json_loads(turn.metadata_json, {})
    action_intent = metadata.get('action_intent') if isinstance(metadata, dict) else None
    if (
        (not isinstance(action_intent, dict) or action_intent.get('kind') != 'item')
        and isinstance(metadata, dict)
        and metadata.get('resolved_turn_id')
    ):
        try:
            resolved_turn_id = int(metadata.get('resolved_turn_id'))
        except (TypeError, ValueError):
            resolved_turn_id = 0
        resolved_turn = db.session.get(DmTurn, resolved_turn_id) if resolved_turn_id > 0 else None
        resolved_metadata = safe_json_loads(resolved_turn.metadata_json, {}) if resolved_turn else {}
        action_intent = (
            resolved_metadata.get('action_intent')
            if isinstance(resolved_metadata, dict)
            else None
        )
    if not isinstance(action_intent, dict) or action_intent.get('kind') != 'item':
        return None

    inventory_action = str(action_intent.get('inventory_action') or 'use').strip().lower()
    if inventory_action not in INVENTORY_ACTIONS:
        return None

    item_payload = action_intent.get('item') if isinstance(action_intent.get('item'), dict) else {}
    item_name = clean_inventory_item_name(item_payload.get('name'))
    item_id = str(item_payload.get('id') or item_payload.get('itemId') or '').strip()
    if not item_name or not looks_like_inventory_item(item_name):
        return None

    sentences = _sentences_with_item(dm_output or '', item_name)
    if not any(_sentence_confirms_action(sentence, inventory_action) for sentence in sentences):
        return None

    if inventory_action in {'pick_up', 'buy'}:
        action = 'acquire'
    elif inventory_action in {'drop', 'give', 'sell', 'use'}:
        action = 'lose'
    else:
        return None

    return {
        'action': action,
        'item_name': item_name,
        **({'item_id': item_id} if item_id else {}),
        'quantity': positive_int(item_payload.get('quantity', 1)),
        'source': 'item_intent_outcome',
        'inventory_action': inventory_action,
    }


def append_inventory_change_from_intent_outcome(turn: DmTurn, dm_output: str, patch: dict):
    change = inventory_change_from_intent_outcome(turn, dm_output)
    if not change:
        return
    normalized_item = normalized_name(change['item_name'])
    if any(
        normalized_name(existing.get('item_name')) == normalized_item
        and existing.get('action') == change['action']
        for existing in patch.get('inventory_changes', [])
        if isinstance(existing, dict)
    ):
        return
    append_inventory_change(
        patch,
        action=change['action'],
        item_name=change['item_name'],
        quantity=change.get('quantity', 1),
    )


def load_inventory(raw_value: str | None) -> list[dict]:
    if not raw_value:
        return []

    payload = safe_json_loads(raw_value, None)
    if isinstance(payload, dict):
        payload = payload.get('items', [])
    if isinstance(payload, list):
        normalized_items: list[dict] = []
        for item in payload:
            if isinstance(item, dict):
                name = clean_inventory_item_name(item.get('name'))
                if not name:
                    continue
                normalized_items.append(
                    _normalized_inventory_item(
                        name,
                        quantity=positive_int(item.get('quantity', 1)),
                        weight=item.get('weight'),
                        extra=item,
                    )
                )
            elif isinstance(item, str):
                name = clean_inventory_item_name(item)
                if name:
                    normalized_items.append(_normalized_inventory_item(name))
        return normalized_items

    if isinstance(raw_value, str):
        parts = [part.strip() for part in raw_value.split(',') if part.strip()]
        return [_normalized_inventory_item(part) for part in parts]
    return []


def dump_inventory(items: list[dict]) -> str:
    compacted = [
            _normalized_inventory_item(
                clean_inventory_item_name(item.get('name')) or item['name'],
                quantity=positive_int(item.get('quantity', 1)),
                weight=item.get('weight'),
                extra=item,
            )
        for item in items
        if item.get('name') and int_or_default(item.get('quantity', 1), default=1) > 0
    ]
    return safe_json_dumps(compacted, [])


def inventory_payload(raw_value: str | None) -> list[dict]:
    return load_inventory(raw_value)


def _inventory_contains_item_name(held_key: str, change_key: str) -> bool:
    if not held_key or not change_key:
        return False
    return bool(
        re.search(
            rf'(?:^|\s){re.escape(held_key)}(?:\s|$)',
            change_key,
        )
    )


def _resolve_inventory_loss_item(index: dict[str, dict], item_name: str) -> tuple[str, dict | None]:
    key = normalized_name(item_name)
    item_entry = index.get(key)
    if item_entry:
        return key, item_entry

    candidates = [
        (held_key, held_item)
        for held_key, held_item in index.items()
        if _inventory_contains_item_name(held_key, key)
    ]
    if len(candidates) == 1:
        return candidates[0]

    return key, None


def apply_inventory_changes(turn: DmTurn, changes: list[dict]) -> list[dict]:
    if not changes:
        return []

    player = db.session.get(Player, turn.player_id)
    if not player:
        return []

    inventory = load_inventory(player.inventory)
    index = {normalized_name(item['name']): item for item in inventory}
    applied_changes: list[dict] = []
    applied_loss_keys: set[str] = set()

    for change in changes:
        action = change['action']
        item_name = change['item_name']
        quantity = positive_int(change.get('quantity', 1))
        key = normalized_name(item_name)
        item_entry = index.get(key)

        if action == 'acquire':
            if item_entry:
                item_entry['quantity'] += quantity
                if item_entry.get('weight') is None:
                    item_entry['weight'] = item_weight_for_name(item_entry['name'])
            else:
                item_entry = _normalized_inventory_item(item_name, quantity=quantity)
                inventory.append(item_entry)
                index[key] = item_entry
            applied_changes.append({'action': action, 'item_name': item_name, 'quantity': quantity})
            continue

        if action == 'lose':
            key, item_entry = _resolve_inventory_loss_item(index, item_name)
            if not item_entry or key in applied_loss_keys:
                continue
            applied_quantity = min(quantity, positive_int(item_entry.get('quantity', 1)))
            item_entry['quantity'] -= applied_quantity
            applied_loss_keys.add(key)
            applied_changes.append({'action': action, 'item_name': item_entry['name'], 'quantity': applied_quantity})

    player.inventory = dump_inventory([item for item in inventory if item.get('quantity', 0) > 0])
    return applied_changes
