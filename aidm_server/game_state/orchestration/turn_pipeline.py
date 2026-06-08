from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from aidm_server.canon_inventory import append_drop_all_inventory_changes_from_text, inventory_change_from_intent_outcome
from aidm_server.canon_text import int_or_default
from aidm_server.database import db
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY, STATE_PIPELINE_VERSION
from aidm_server.game_state.application.applier import (
    apply_state_changes,
    legacy_immediate_summary_from_applied,
    persist_state_to_database,
)
from aidm_server.game_state.extraction.post_dm_outcome_extractor import extract_post_dm_outcomes
from aidm_server.game_state.extraction.pre_dm_action_extractor import extract_pre_dm_actions
from aidm_server.game_state.logging.state_log_builder import build_state_log, state_log_message
from aidm_server.game_state.models import (
    compact_state_for_extraction,
    display_actor_id,
    normalize_item_name,
    recent_timeline_for_session,
    stable_change_id,
    state_snapshot_for_session,
)
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.models import Campaign, DmTurn, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.turn_events import record_turn_event


STATE_UPDATE_EVENT = 'state_update'
MANAGED_STATE_DOMAINS = ['inventory', 'currency', 'health', 'xp']
SAFE_PRE_DM_IMMEDIATE_CHANGE_TYPES = {'inventory.mark_used'}
CONFIRMATION_DENIAL_PATTERN = re.compile(
    r"\b(?:do not|don't|does not|doesn't|did not|cannot|can't|fail|fails|failed|before you can|instead)\b",
    re.IGNORECASE,
)
INVENTORY_REMOVE_CONFIRMATION_PATTERN = re.compile(
    r'\b(?:drink|drinks|drank|consume|consumes|consumed|quaff|quaffs|quaffed|swallow|swallows|swallowed|'
    r'eat|eats|ate|use up|uses up|used up|drop|drops|dropped|give|gives|gave|hand over|hands over|'
    r'sell|sells|sold|remove|removes|removed)\b',
    re.IGNORECASE,
)
INVENTORY_TRANSFER_CONFIRMATION_PATTERN = re.compile(
    r'\b(?:give|gives|gave|hand|hands|handed|pass|passes|passed|offer|offers|offered)\b',
    re.IGNORECASE,
)
CURRENCY_TRANSFER_CONFIRMATION_PATTERN = re.compile(
    r'\b(?:give|gives|gave|pay|pays|paid|hand over|hands over|handed over)\b',
    re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in re.split(r'(?<=[.!?])\s+|\n+', text or '') if sentence.strip()]


def _players_for_campaign(campaign: Campaign, fallback_player: Player) -> list[Player]:
    players = Player.query.filter_by(workspace_id=campaign.workspace_id).order_by(Player.player_id.asc()).all()
    available = [
        player
        for player in players
        if player.workspace_id == campaign.workspace_id and (player.campaign_id in {None, campaign.campaign_id})
    ]
    if not any(player.player_id == fallback_player.player_id for player in available):
        available.append(fallback_player)
    return available


def _metadata(turn: DmTurn) -> dict[str, Any]:
    payload = safe_json_loads(turn.metadata_json, {})
    return payload if isinstance(payload, dict) else {}


def _set_metadata(turn: DmTurn, payload: dict[str, Any]) -> None:
    turn.metadata_json = safe_json_dumps(payload, {})


def _recent_context_strings(recent_timeline: list[dict[str, Any]]) -> list[str]:
    values = []
    for entry in recent_timeline:
        if not isinstance(entry, dict):
            continue
        if entry.get('playerMessage'):
            values.append(str(entry.get('playerMessage')))
        if entry.get('dmResponse'):
            values.append(str(entry.get('dmResponse')))
    return values


def _safe_pre_dm_immediate_change(change: dict[str, Any]) -> bool:
    change_type = str(change.get('type') or '').strip()
    return change_type in SAFE_PRE_DM_IMMEDIATE_CHANGE_TYPES and not bool(change.get('visible', True))


def _item_reference_terms(item_name: Any) -> set[str]:
    normalized = normalize_item_name(item_name)
    terms = {normalized} if normalized else set()
    tokens = {token for token in normalized.split() if len(token) > 2}
    if tokens:
        terms.add(normalized.split()[-1])
        terms.update(token for token in tokens if token in {'potion', 'ration', 'food', 'elixir', 'vial', 'flask'})
    return {term for term in terms if term}


def _sentence_mentions_item(sentence: str, item_name: Any) -> bool:
    normalized_sentence = normalize_item_name(sentence)
    if not normalized_sentence:
        return False
    for term in _item_reference_terms(item_name):
        if ' ' in term and term in normalized_sentence:
            return True
        if re.search(rf'\b{re.escape(term)}\b', normalized_sentence):
            return True
    return False


def _dm_confirms_inventory_remove(change: dict[str, Any], dm_response_text: str) -> bool:
    item = change.get('item') if isinstance(change.get('item'), dict) else {}
    item_name = change.get('itemName') or change.get('item_name') or item.get('name')
    if not item_name:
        return False
    for sentence in _sentences(dm_response_text):
        if CONFIRMATION_DENIAL_PATTERN.search(sentence):
            continue
        if INVENTORY_REMOVE_CONFIRMATION_PATTERN.search(sentence) and _sentence_mentions_item(sentence, item_name):
            return True
    return False


def _dm_confirms_inventory_transfer(action: dict[str, Any], dm_response_text: str) -> bool:
    item_name = action.get('itemName') or action.get('item_name')
    target_name = action.get('toActorName') or action.get('to_actor_name')
    for sentence in _sentences(dm_response_text):
        if CONFIRMATION_DENIAL_PATTERN.search(sentence):
            continue
        if not INVENTORY_TRANSFER_CONFIRMATION_PATTERN.search(sentence):
            continue
        if item_name and not _sentence_mentions_item(sentence, item_name):
            continue
        if target_name and normalize_item_name(target_name) not in normalize_item_name(sentence):
            continue
        return True
    return False


def _dm_confirms_currency_transfer(action: dict[str, Any], dm_response_text: str) -> bool:
    amount = int_or_default(action.get('amount'), default=0)
    currency = str(action.get('currency') or '').strip().lower()
    target_name = action.get('toActorName') or action.get('to_actor_name')
    if amount <= 0 or not currency:
        return False
    for sentence in _sentences(dm_response_text):
        normalized = normalize_item_name(sentence)
        if CONFIRMATION_DENIAL_PATTERN.search(sentence):
            continue
        if not CURRENCY_TRANSFER_CONFIRMATION_PATTERN.search(sentence):
            continue
        if str(amount) not in normalized:
            continue
        if currency not in normalized and {
            'pp': 'platinum',
            'gp': 'gold',
            'ep': 'electrum',
            'sp': 'silver',
            'cp': 'copper',
        }.get(currency, currency) not in normalized:
            continue
        if target_name and normalize_item_name(target_name) not in normalized:
            continue
        return True
    return False


def _confirmed_pre_dm_changes(
    *,
    turn: DmTurn,
    pre_validation: dict[str, Any],
    pending_immediate_changes: list[dict[str, Any]],
    dm_response_text: str,
) -> list[dict[str, Any]]:
    confirmed: list[dict[str, Any]] = []
    for change in pending_immediate_changes:
        if not isinstance(change, dict):
            continue
        if str(change.get('type') or '') == 'inventory.remove' and _dm_confirms_inventory_remove(change, dm_response_text):
            next_change = deepcopy(change)
            next_change['source'] = 'post_dm_confirmed'
            next_change['reason'] = next_change.get('reason') or 'DM confirmed the pre-validated inventory removal.'
            confirmed.append(next_change)

    for result in pre_validation.get('validatedActions') or []:
        if not isinstance(result, dict) or result.get('status') not in {'valid', 'pending'}:
            continue
        original = result.get('originalAction') if isinstance(result.get('originalAction'), dict) else {}
        normalized = result.get('normalizedAction') if isinstance(result.get('normalizedAction'), dict) else {}
        action = {**original, **normalized}
        action_type = str(original.get('type') or normalized.get('type') or '').strip()
        action_id = str(original.get('id') or normalized.get('id') or result.get('actionId') or '').strip()
        actor_id = str(action.get('fromActorId') or action.get('actorId') or '').strip()

        if action_type == 'inventory.transfer' and _dm_confirms_inventory_transfer(action, dm_response_text):
            confirmed.append(
                {
                    'id': stable_change_id(turn.turn_id, 'post_dm_confirmed', action_id, 'inventory.transfer'),
                    'turnId': turn.turn_id,
                    'type': 'inventory.transfer',
                    'source': 'post_dm_confirmed',
                    'actorId': actor_id,
                    'fromActorId': actor_id,
                    'toActorId': action.get('toActorId'),
                    'toActorName': action.get('toActorName'),
                    'itemId': action.get('itemId'),
                    'itemName': action.get('itemName'),
                    'quantity': max(1, int_or_default(action.get('quantity'), default=1)),
                    'reason': f"DM confirmed transfer of {action.get('itemName') or 'item'}.",
                    'visible': True,
                }
            )
        elif action_type == 'currency.transfer' and _dm_confirms_currency_transfer(action, dm_response_text):
            confirmed.append(
                {
                    'id': stable_change_id(turn.turn_id, 'post_dm_confirmed', action_id, 'currency.transfer'),
                    'turnId': turn.turn_id,
                    'type': 'currency.transfer',
                    'source': 'post_dm_confirmed',
                    'actorId': actor_id,
                    'fromActorId': actor_id,
                    'toActorId': action.get('toActorId'),
                    'toActorName': action.get('toActorName'),
                    'amount': max(1, int_or_default(action.get('amount'), default=1)),
                    'currency': str(action.get('currency') or '').lower(),
                    'reason': f"DM confirmed transfer of {action.get('amount')} {action.get('currency')}.",
                    'visible': True,
                }
            )
    return _merge_state_changes(confirmed)


def _state_change_signature(change: dict[str, Any]) -> tuple[Any, ...] | None:
    change_type = str(change.get('type') or '').strip()
    actor_id = str(change.get('actorId') or change.get('actor_id') or '')
    if change_type in {'inventory.add', 'inventory.remove'}:
        item = change.get('item') if isinstance(change.get('item'), dict) else {}
        item_name = change.get('itemName') or change.get('item_name') or item.get('name')
        return (change_type, actor_id, normalize_item_name(item_name))
    if change_type == 'inventory.transfer':
        item_name = change.get('itemName') or change.get('item_name')
        to_actor = str(change.get('toActorId') or change.get('to_actor_id') or change.get('toActorName') or change.get('to_actor_name') or '')
        return (
            change_type,
            actor_id,
            to_actor.lower(),
            normalize_item_name(item_name),
            int_or_default(change.get('quantity'), default=1),
        )
    if change_type in {'currency.add', 'currency.remove'}:
        return (change_type, actor_id, str(change.get('currency') or '').lower(), int_or_default(change.get('amount'), default=0))
    if change_type == 'currency.transfer':
        to_actor = str(change.get('toActorId') or change.get('to_actor_id') or change.get('toActorName') or change.get('to_actor_name') or '')
        return (
            change_type,
            actor_id,
            to_actor.lower(),
            str(change.get('currency') or '').lower(),
            int_or_default(change.get('amount'), default=0),
        )
    if change_type in {'health.heal', 'health.damage'}:
        return (change_type, actor_id, int_or_default(change.get('amount'), default=0))
    if change_type in {'xp.add', 'xp.remove'}:
        return (change_type, actor_id, int_or_default(change.get('amount'), default=0))
    return None


def _merge_state_changes(
    *change_lists: list[dict[str, Any]],
    seed_changes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for change in seed_changes or []:
        if isinstance(change, dict):
            signature = _state_change_signature(change)
            if signature:
                seen.add(signature)
    for changes in change_lists:
        for change in changes or []:
            if not isinstance(change, dict):
                continue
            signature = _state_change_signature(change)
            if signature and signature in seen:
                continue
            if signature:
                seen.add(signature)
            merged.append(change)
    return merged


def _intent_confirmed_post_changes(
    *,
    turn: DmTurn,
    dm_response_text: str,
    actor_id: str,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    inventory_change = inventory_change_from_intent_outcome(turn, dm_response_text)
    if inventory_change:
        action = str(inventory_change.get('action') or '')
        change_type = 'inventory.add' if action == 'acquire' else 'inventory.remove' if action == 'lose' else ''
        item_name = str(inventory_change.get('item_name') or '').strip()
        quantity = max(1, int_or_default(inventory_change.get('quantity'), default=1))
        if change_type and item_name:
            change: dict[str, Any] = {
                'id': stable_change_id(turn.turn_id, 'post_dm_intent', change_type, actor_id, item_name, quantity),
                'turnId': turn.turn_id,
                'type': change_type,
                'source': 'post_dm',
                'actorId': actor_id,
                'itemName': item_name,
                'quantity': quantity,
                'reason': f"DM confirmed requested inventory action for {item_name}.",
                'visible': True,
            }
            if change_type == 'inventory.add':
                change['item'] = {'name': item_name, 'quantity': quantity, 'type': 'misc'}
            changes.append(change)

    metadata = _metadata(turn)
    action_intent = metadata.get('action_intent') if isinstance(metadata.get('action_intent'), dict) else None
    if isinstance(action_intent, dict) and inventory_change:
        inventory_action = str(action_intent.get('inventory_action') or '').strip().lower()
        cost_gold = max(0, int_or_default(action_intent.get('cost_gold'), default=0))
        if cost_gold and inventory_action in {'buy', 'sell'}:
            change_type = 'currency.remove' if inventory_action == 'buy' else 'currency.add'
            changes.append(
                {
                    'id': stable_change_id(turn.turn_id, 'post_dm_intent', change_type, actor_id, 'gp', cost_gold),
                    'turnId': turn.turn_id,
                    'type': change_type,
                    'source': 'post_dm',
                    'actorId': actor_id,
                    'amount': cost_gold,
                    'currency': 'gp',
                    'reason': f"DM confirmed {inventory_action} action with known price/value.",
                    'visible': True,
                }
            )

    drop_all_patch = {'inventory_changes': []}
    append_drop_all_inventory_changes_from_text(turn, dm_response_text, drop_all_patch)
    for change in drop_all_patch.get('inventory_changes') or []:
        if not isinstance(change, dict) or change.get('action') != 'lose':
            continue
        item_name = str(change.get('item_name') or '').strip()
        quantity = max(1, int_or_default(change.get('quantity'), default=1))
        if not item_name:
            continue
        changes.append(
            {
                'id': stable_change_id(turn.turn_id, 'post_dm_drop_all', 'inventory.remove', actor_id, item_name, quantity),
                'turnId': turn.turn_id,
                'type': 'inventory.remove',
                'source': 'post_dm',
                'actorId': actor_id,
                'itemName': item_name,
                'quantity': quantity,
                'reason': f"DM confirmed dropping {item_name}.",
                'visible': True,
            }
        )
    return _merge_state_changes(changes)


def _dm_context_packet(
    *,
    state: dict[str, Any],
    player_message: str,
    pre_validation: dict[str, Any],
    applied_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    compact = compact_state_for_extraction(state)
    validated_actions = []
    valid_actions = []
    invalid_actions = []
    pending_actions = []
    needs_clarification = []
    for result in pre_validation.get('validatedActions') or []:
        if not isinstance(result, dict):
            continue
        original = result.get('originalAction') if isinstance(result.get('originalAction'), dict) else {}
        normalized = result.get('normalizedAction') if isinstance(result.get('normalizedAction'), dict) else {}
        resolution = normalized.get('resolution') if isinstance(normalized.get('resolution'), dict) else None
        action_label = normalized.get('summary') or original.get('summary') or original.get('sourceText')
        reason = result.get('reason') or result.get('status')
        summary = action_label if action_label else reason
        if action_label and reason and reason != action_label:
            summary = f"{action_label} ({reason})"
        action_entry = {
            'status': result.get('status'),
            'summary': summary,
            'type': original.get('type'),
            'resolvedItem': (
                {
                    'itemId': resolution.get('itemId'),
                    'itemName': resolution.get('itemName'),
                    'resolutionMethod': resolution.get('resolutionMethod'),
                }
                if resolution and resolution.get('status') == 'resolved'
                else None
            ),
            'reason': result.get('reason'),
        }
        validated_actions.append(action_entry)
        if result.get('status') == 'valid':
            valid_actions.append(action_entry)
        elif result.get('status') == 'pending':
            pending_actions.append(action_entry)
        elif result.get('status') == 'invalid':
            invalid_actions.append(action_entry)
        elif result.get('status') == 'needs_clarification':
            needs_clarification.append(
                {
                    **action_entry,
                    'clarificationRequest': result.get('clarificationRequest'),
                }
            )
    return {
        'currentStateSummary': compact,
        'playerMessage': player_message,
        'validatedActions': validated_actions,
        'validActions': valid_actions,
        'invalidActions': invalid_actions,
        'pendingActions': pending_actions,
        'needsClarification': needs_clarification,
        'pendingRolls': pre_validation.get('pendingRolls') or [],
        'stateChangesAlreadyApplied': [
            {
                'type': change.get('type'),
                'itemName': change.get('itemName'),
                'amount': change.get('actualAmount', change.get('amount')),
                'currency': change.get('currency'),
                'xp': change.get('actualAmount', change.get('amount')) if str(change.get('type') or '').startswith('xp.') else None,
            }
            for change in applied_changes
            if isinstance(change, dict) and change.get('visible', True)
        ],
        'dmInstructions': [
            'Narrate valid actions as possible.',
            'Anchor narration to the latest playerMessage and validatedActions.',
            'Do not substitute a different known object for the object named or described in the latest playerMessage.',
            'Do not narrate invalid actions as successful.',
            'If an action is invalid, explain it naturally in-world.',
            'Do not output JSON.',
            'Do not claim state changes that contradict validatedActions.',
        ],
    }


def augment_rules_hint_with_state_packet(rules_hint_payload: dict[str, Any], dm_context_packet: dict[str, Any]) -> dict[str, Any]:
    updated = dict(rules_hint_payload)
    updated['state_pipeline'] = dm_context_packet
    return updated


def pre_dm_pipeline(
    *,
    turn: DmTurn,
    session_obj: Session,
    campaign: Campaign,
    player: Player,
    player_message: str,
    action_intent: dict[str, Any] | None = None,
    selected_item_ids: dict[str, str] | None = None,
    declared_actions_override: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    players = _players_for_campaign(campaign, player)
    players_by_id = {player_obj.player_id: player_obj for player_obj in players}
    state = state_snapshot_for_session(session_obj=session_obj, campaign=campaign, players=players)
    recent_timeline = recent_timeline_for_session(session_obj.session_id, limit=5)
    actor_id = display_actor_id(player.player_id)

    if declared_actions_override:
        pre_extraction = {
            'declaredActions': declared_actions_override,
            'notes': ['clarification_resume'],
        }
    else:
        pre_extraction = extract_pre_dm_actions(
            current_state=compact_state_for_extraction(state),
            player_message=player_message,
            recent_timeline=recent_timeline,
            actor_id=actor_id,
            action_intent=action_intent,
        )
    pre_validation = validate_declared_actions(
        state=state,
        declared_actions=pre_extraction.get('declaredActions') or [],
        current_turn=turn.turn_id,
        recent_context=_recent_context_strings(recent_timeline),
        selected_item_ids=selected_item_ids,
    )
    pre_immediate_changes = [
        change
        for change in (pre_validation.get('immediateChanges') or [])
        if isinstance(change, dict)
    ]
    safe_immediate_changes = [change for change in pre_immediate_changes if _safe_pre_dm_immediate_change(change)]
    pending_immediate_changes = [change for change in pre_immediate_changes if not _safe_pre_dm_immediate_change(change)]
    immediate_validation = validate_state_changes(state=state, changes=safe_immediate_changes)
    pending_immediate_validation = validate_state_changes(state=state, changes=pending_immediate_changes)
    immediate_changes = validated_changes_for_application(immediate_validation)
    apply_result = apply_state_changes(state, immediate_changes)
    state_after_immediate = apply_result['nextState']
    applied_immediate = apply_result['appliedChanges']
    if applied_immediate:
        persist_state_to_database(session_obj=session_obj, state=state_after_immediate, players_by_id=players_by_id)
    else:
        session_obj.state_snapshot = safe_json_dumps(state_after_immediate, {})

    dm_context = _dm_context_packet(
        state=state_after_immediate,
        player_message=player_message,
        pre_validation=pre_validation,
        applied_changes=applied_immediate,
    )
    state_log = build_state_log(
        turn_id=turn.turn_id,
        pre_validation=pre_validation,
        immediate_validation=immediate_validation,
    )
    metadata = _metadata(turn)
    metadata[STATE_PIPELINE_METADATA_KEY] = {
        'version': STATE_PIPELINE_VERSION,
        'actorId': actor_id,
        'stateBeforePreDm': compact_state_for_extraction(state),
        'stateBeforeDm': state_after_immediate,
        'preDmExtraction': pre_extraction,
        'preDmValidation': pre_validation,
        'immediateValidation': immediate_validation,
        'pendingImmediateChanges': validated_changes_for_application(pending_immediate_validation),
        'pendingImmediateValidation': pending_immediate_validation,
        'immediateAppliedChanges': applied_immediate,
        'dmContextPacket': dm_context,
        'stateLog': state_log,
        'managedDomains': MANAGED_STATE_DOMAINS,
    }
    _set_metadata(turn, metadata)
    db.session.flush()

    return {
        'stateBeforeDm': state_after_immediate,
        'playersById': players_by_id,
        'preExtraction': pre_extraction,
        'preValidation': pre_validation,
        'immediateValidation': immediate_validation,
        'pendingImmediateValidation': pending_immediate_validation,
        'pendingImmediateChanges': validated_changes_for_application(pending_immediate_validation),
        'immediateAppliedChanges': applied_immediate,
        'dmContextPacket': dm_context,
        'stateLog': state_log,
        'clarificationRequests': pre_validation.get('clarificationRequests') or [],
    }


def post_dm_pipeline(
    *,
    turn: DmTurn,
    session_obj: Session,
    campaign: Campaign,
    player: Player,
    dm_response_text: str,
) -> dict[str, Any]:
    metadata = _metadata(turn)
    pipeline = metadata.get(STATE_PIPELINE_METADATA_KEY) if isinstance(metadata.get(STATE_PIPELINE_METADATA_KEY), dict) else {}
    players = _players_for_campaign(campaign, player)
    players_by_id = {player_obj.player_id: player_obj for player_obj in players}
    state_before_dm = pipeline.get('stateBeforeDm')
    if not isinstance(state_before_dm, dict):
        state_before_dm = state_snapshot_for_session(session_obj=session_obj, campaign=campaign, players=players)

    actor_id = str(pipeline.get('actorId') or display_actor_id(player.player_id))
    recent_timeline = recent_timeline_for_session(session_obj.session_id, limit=5)
    already_applied = list(pipeline.get('immediateAppliedChanges') or [])
    pending_immediate_changes = [
        change
        for change in (pipeline.get('pendingImmediateChanges') or [])
        if isinstance(change, dict)
    ]
    skip_post_extraction = bool(turn.requires_roll and turn.roll_value is None and str(turn.outcome_status or '').lower() == 'deferred')
    if skip_post_extraction:
        post_extraction = {
            'proposedChanges': [],
            'uncertainChanges': [],
            'notes': ['post_dm_skipped_pending_roll'],
            'debug': {
                'source': 'skipped',
                'reason': 'pending_roll',
                'helperAttempted': False,
                'helperSchemaValid': False,
                'helperModel': None,
                'helperRawText': None,
                'helperRawPreview': None,
                'helperParsed': None,
                'helperError': None,
                'fallbackRan': False,
                'fallbackReason': None,
            },
        }
        post_validation = {'accepted': [], 'rejected': [], 'modified': []}
        final_state = deepcopy(state_before_dm)
        applied_post: list[dict[str, Any]] = []
        session_obj.state_snapshot = safe_json_dumps(final_state, {})
    else:
        post_extraction = extract_post_dm_outcomes(
            state_before_dm=compact_state_for_extraction(state_before_dm),
            player_message=turn.player_input,
            validated_actions=pipeline.get('preDmValidation') if isinstance(pipeline.get('preDmValidation'), dict) else {},
            already_applied_changes=already_applied,
            dm_response=dm_response_text,
            recent_timeline=recent_timeline,
            actor_id=actor_id,
            turn_id=turn.turn_id,
        )
        intent_changes = _intent_confirmed_post_changes(
            turn=turn,
            dm_response_text=dm_response_text,
            actor_id=actor_id,
        )
        confirmed_pre_dm_changes = _confirmed_pre_dm_changes(
            turn=turn,
            pre_validation=pipeline.get('preDmValidation') if isinstance(pipeline.get('preDmValidation'), dict) else {},
            pending_immediate_changes=pending_immediate_changes,
            dm_response_text=dm_response_text,
        )
        if intent_changes or confirmed_pre_dm_changes:
            post_extraction = deepcopy(post_extraction)
            post_extraction['proposedChanges'] = _merge_state_changes(
                post_extraction.get('proposedChanges') or [],
                intent_changes,
                confirmed_pre_dm_changes,
                seed_changes=already_applied,
            )
            notes = list(post_extraction.get('notes') or [])
            if intent_changes and 'intent_confirmed_post_dm' not in notes:
                notes.append('intent_confirmed_post_dm')
            if confirmed_pre_dm_changes and 'pre_dm_confirmed_post_dm' not in notes:
                notes.append('pre_dm_confirmed_post_dm')
            post_extraction['notes'] = notes
        post_validation = validate_state_changes(state=state_before_dm, changes=post_extraction.get('proposedChanges') or [])
        post_changes = validated_changes_for_application(post_validation)
        post_apply = apply_state_changes(state_before_dm, post_changes)
        final_state = post_apply['nextState']
        applied_post = post_apply['appliedChanges']
        if applied_post:
            persist_state_to_database(session_obj=session_obj, state=final_state, players_by_id=players_by_id)
        else:
            session_obj.state_snapshot = safe_json_dumps(final_state, {})

    state_log = build_state_log(
        turn_id=turn.turn_id,
        pre_validation=pipeline.get('preDmValidation') if isinstance(pipeline.get('preDmValidation'), dict) else None,
        immediate_validation=pipeline.get('immediateValidation') if isinstance(pipeline.get('immediateValidation'), dict) else None,
        post_validation=post_validation,
    )
    all_applied = [*already_applied, *applied_post]
    legacy_summary = legacy_immediate_summary_from_applied(
        all_applied,
        rejected=[
            *(post_validation.get('rejected') or []),
            *((pipeline.get('immediateValidation') or {}).get('rejected') if isinstance(pipeline.get('immediateValidation'), dict) else []),
        ],
    )

    pipeline.update(
        {
            'stateBeforeDm': state_before_dm,
            'postDmExtraction': post_extraction,
            'postDmValidation': post_validation,
            'postAppliedChanges': applied_post,
            'finalStateSummary': compact_state_for_extraction(final_state),
            'stateLog': state_log,
            'managedDomains': MANAGED_STATE_DOMAINS,
        }
    )
    metadata[STATE_PIPELINE_METADATA_KEY] = pipeline
    metadata['immediate_state_changes_applied'] = legacy_summary
    turn.metadata_json = safe_json_dumps(metadata, {})
    db.session.flush()

    message = state_log_message(state_log)
    if message:
        record_turn_event(
            session_id=turn.session_id,
            campaign_id=campaign.campaign_id,
            turn_id=turn.turn_id,
            player_id=turn.player_id,
            event_type=STATE_UPDATE_EVENT,
            payload={
                'message': message,
                'stateLog': state_log,
                'metadata': {
                    'turn_id': turn.turn_id,
                    'state_log': state_log,
                    'state_pipeline_version': STATE_PIPELINE_VERSION,
                },
            },
        )

    return {
        'postExtraction': post_extraction,
        'postValidation': post_validation,
        'postAppliedChanges': applied_post,
        'stateLog': state_log,
        'stateLogMessage': message,
        'legacyImmediateSummary': legacy_summary,
        'finalState': deepcopy(final_state),
    }
