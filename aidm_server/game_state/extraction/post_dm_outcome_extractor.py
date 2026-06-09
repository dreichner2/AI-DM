from __future__ import annotations

import re
from typing import Any

from flask import current_app, has_app_context

from aidm_server.contracts import ProviderRequest
from aidm_server.game_state.extraction.prompts import POST_DM_SYSTEM_MESSAGE, build_post_dm_prompt
from aidm_server.game_state.extraction.schemas import extract_json_object, normalize_post_extraction
from aidm_server.game_state.models import normalize_item_name, stable_change_id
from aidm_server.llm_providers import get_helper_provider
from aidm_server.telemetry import telemetry_event, telemetry_metric


HELPER_RAW_PREVIEW_LIMIT = 2000
HEAL_PATTERN = re.compile(
    r'\b(?:restore|restores|restored|heal|heals|healed|regain|regains|regained|recover|recovers|recovered)\s+'
    r'(?P<amount>\d{1,4})\s*(?:hp|hit points?)\b',
    re.IGNORECASE,
)
DAMAGE_PATTERN = re.compile(
    r'\b(?:take|takes|took|suffer|suffers|suffered)\s+'
    r'(?P<amount>\d{1,4})\s*(?:points?\s+of\s+)?(?:damage|hp)\b',
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


def _change_identity_value(change: dict[str, Any]) -> Any:
    item = change.get('item') if isinstance(change.get('item'), dict) else {}
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
        or item.get('id')
        or item.get('name')
        or change.get('currency')
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
    if change_type in {'inventory.add', 'inventory.remove'}:
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
        'id': stable_change_id(turn_id, 'post_dm', change_type, actor_id, payload.get('itemName'), payload.get('currency'), payload.get('amount')),
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
    signature = _already_applied_signature(change)
    if signature and signature in already:
        return
    if signature and any(_already_applied_signature(existing) == signature for existing in changes):
        return
    changes.append(change)


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
        amount = int(match.group('amount'))
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
        amount = int(match.group('amount'))
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
    return {'proposedChanges': changes, 'uncertainChanges': [], 'notes': ['heuristic_post_dm'] if changes else []}


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
        scene_changes = _heuristic_scene_danger_changes(
            state_before_dm=state_before_dm,
            dm_response=dm_response,
            turn_id=turn_id,
            already_applied_changes=[*already_applied_changes, *(normalized.get('proposedChanges') or [])],
        )
        if scene_changes:
            normalized['proposedChanges'] = [*(normalized.get('proposedChanges') or []), *scene_changes]
        notes = list(normalized.get('notes') or [])
        if 'helper_post_dm' not in notes:
            notes.append('helper_post_dm')
        if scene_changes and 'heuristic_scene_danger' not in notes:
            notes.append('heuristic_scene_danger')
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
