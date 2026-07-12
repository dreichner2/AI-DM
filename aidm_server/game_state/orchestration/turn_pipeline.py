from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Callable

from aidm_server.canon_inventory import append_drop_all_inventory_changes_from_text, inventory_change_from_intent_outcome
from aidm_server.canon_text import int_or_default
from aidm_server.combat.pipeline import (
    combat_turn_advance_change,
    finalize_combat_prepare,
    prepare_combat_for_turn,
    prepare_combat_from_dm_response,
    record_combat_debug_from_outcome,
    record_combat_debug_from_prepare,
    sync_combat_encounter_record,
)
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
from aidm_server.game_state.orchestration.combat_resolution import (
    build_dm_combat_context,
    combat_participant_update_signature,
    derive_trusted_damage_changes,
    without_trusted_damage_overlaps,
)
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
MANAGED_STATE_DOMAINS = ['inventory', 'currency', 'health', 'xp', 'spells', 'scene', 'quests', 'locations', 'npcs', 'flags', 'combat']
SAFE_PRE_DM_IMMEDIATE_CHANGE_TYPES = {'inventory.mark_used', 'inventory.equip', 'inventory.unequip'}
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
    players = (
        Player.query.filter_by(workspace_id=campaign.workspace_id, campaign_id=campaign.campaign_id)
        .order_by(Player.player_id.asc())
        .all()
    )
    available = [
        player
        for player in players
        if player.workspace_id == campaign.workspace_id and player.campaign_id == campaign.campaign_id
    ]
    if not any(player.player_id == fallback_player.player_id for player in available):
        available.append(fallback_player)
    return available


def _pipeline_orm_token(
    *,
    turn: DmTurn,
    session_obj: Session,
    campaign: Campaign,
    player: Player,
    players: list[Player],
) -> dict[str, Any]:
    """Capture the rows whose state a helper response is allowed to mutate.

    The models do not currently expose SQLAlchemy version columns. This token
    therefore provides an optimistic version boundary from their mutable
    columns so a helper result cannot overwrite edits committed while its
    external provider call was in flight.
    """

    return {
        'turn_id': turn.turn_id,
        'session_id': session_obj.session_id,
        'campaign_id': campaign.campaign_id,
        'player_id': player.player_id,
        'turn': (
            turn.session_id,
            turn.campaign_id,
            turn.player_id,
            turn.status,
            turn.dm_output,
            turn.requires_roll,
            turn.roll_value,
            turn.rule_type,
            turn.outcome_status,
            turn.rules_hint,
            turn.metadata_json,
            turn.completed_at,
        ),
        'session': (
            session_obj.campaign_id,
            session_obj.status,
            session_obj.state_snapshot,
            session_obj.updated_at,
            session_obj.deleted_at,
        ),
        'campaign': (
            campaign.workspace_id,
            campaign.world_id,
            campaign.status,
            campaign.current_quest,
            campaign.plot_points,
            campaign.active_npcs,
            campaign.location,
            campaign.updated_at,
        ),
        'players': tuple(
            (
                player_obj.player_id,
                player_obj.workspace_id,
                player_obj.campaign_id,
                player_obj.level,
                player_obj.stats,
                player_obj.inventory,
                player_obj.character_sheet,
                player_obj.updated_at,
            )
            for player_obj in players
        ),
    }


def _reload_pipeline_orm(token: dict[str, Any]) -> tuple[DmTurn, Session, Campaign, Player, list[Player]]:
    turn = db.session.get(DmTurn, token['turn_id'])
    session_obj = db.session.get(Session, token['session_id'])
    campaign = db.session.get(Campaign, token['campaign_id'])
    player = db.session.get(Player, token['player_id'])
    if not all((turn, session_obj, campaign, player)):
        raise RuntimeError('Turn state changed while the helper provider was running.')

    players = _players_for_campaign(campaign, player)
    current = _pipeline_orm_token(
        turn=turn,
        session_obj=session_obj,
        campaign=campaign,
        player=player,
        players=players,
    )
    if current != token:
        raise RuntimeError('Turn state changed while the helper provider was running.')
    return turn, session_obj, campaign, player, players


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


def _merge_validation_results(*validations: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {'accepted': [], 'modified': [], 'rejected': []}
    for validation in validations:
        if not isinstance(validation, dict):
            continue
        for key in ('accepted', 'modified', 'rejected'):
            merged[key].extend([item for item in (validation.get(key) or []) if isinstance(item, dict)])
    return merged


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
        if action.get('untrackedTarget') and not action.get('toActorId'):
            continue

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


def _turn_resolves_player_roll(turn: DmTurn) -> bool:
    if getattr(turn, 'roll_value', None) is not None:
        return True
    rules_hint = safe_json_loads(turn.rules_hint, {})
    if not isinstance(rules_hint, dict):
        return False
    return rules_hint.get('roll_value') is not None and not bool(rules_hint.get('outcome_deferred'))


def _turn_awaits_player_roll(turn: DmTurn) -> bool:
    return bool(turn.requires_roll and getattr(turn, 'roll_value', None) is None)


def _turn_level_pending_roll(turn: DmTurn, *, actor_id: str) -> dict[str, Any]:
    rules_hint = safe_json_loads(turn.rules_hint, {})
    rules_hint = rules_hint if isinstance(rules_hint, dict) else {}
    roll_type = str(turn.rule_type or rules_hint.get('roll_type') or 'check').strip() or 'check'
    return {
        'type': f'{roll_type}_roll',
        'actorId': actor_id,
        'source': 'turn_rules',
        'dcHint': rules_hint.get('dc_hint'),
        'reason': rules_hint.get('reason') or 'Player roll required to resolve the current action.',
    }


def _resolved_player_roll_should_defer_enemy(turn: DmTurn) -> bool:
    if not _turn_resolves_player_roll(turn):
        return False
    rules_hint = safe_json_loads(turn.rules_hint, {})
    rules_hint = rules_hint if isinstance(rules_hint, dict) else {}
    roll_type = str(turn.rule_type or rules_hint.get('roll_type') or '').strip().lower()
    if roll_type == 'attack':
        return False
    return True


def _state_change_signature(change: dict[str, Any]) -> tuple[Any, ...] | None:
    change_type = str(change.get('type') or '').strip()
    actor_id = str(change.get('actorId') or change.get('actor_id') or '')
    if change_type in {'inventory.add', 'inventory.remove', 'inventory.equip', 'inventory.unequip'}:
        item = change.get('item') if isinstance(change.get('item'), dict) else {}
        item_id = change.get('itemId') or change.get('item_id') or item.get('id') or item.get('itemId')
        item_name = change.get('itemName') or change.get('item_name') or item.get('name')
        return (
            change_type,
            actor_id,
            str(item_id or ''),
            normalize_item_name(item_name),
            int_or_default(change.get('quantity', item.get('quantity')), default=1),
            normalize_item_name(change.get('slot')),
        )
    if change_type == 'inventory.transfer':
        item = change.get('item') if isinstance(change.get('item'), dict) else {}
        item_id = change.get('itemId') or change.get('item_id') or item.get('id') or item.get('itemId')
        item_name = change.get('itemName') or change.get('item_name')
        to_actor = str(change.get('toActorId') or change.get('to_actor_id') or change.get('toActorName') or change.get('to_actor_name') or '')
        return (
            change_type,
            actor_id,
            to_actor.lower(),
            str(item_id or ''),
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
    if change_type == 'health.max.set':
        return (change_type, actor_id, int_or_default(change.get('maxHp', change.get('amount')), default=0))
    if change_type in {'xp.add', 'xp.remove'}:
        return (change_type, actor_id, int_or_default(change.get('amount'), default=0))
    if change_type == 'spell.learn':
        spell = change.get('spell') if isinstance(change.get('spell'), dict) else {}
        return (change_type, actor_id, normalize_item_name(change.get('spellName') or spell.get('name')))
    if change_type in {'scene.update', 'scene.move_location'}:
        return (
            change_type,
            normalize_item_name(change.get('locationId') or change.get('name')),
            normalize_item_name(change.get('sceneType') or change.get('mood') or change.get('combatState')),
        )
    if change_type == 'combat.end':
        return (
            change_type,
            normalize_item_name(change.get('status') or 'ended'),
            normalize_item_name(change.get('endReason') or change.get('end_reason')),
        )
    if change_type == 'combat.participant.update':
        return (
            change_type,
            normalize_item_name(change.get('participantId') or change.get('enemyId')),
            combat_participant_update_signature(change),
        )
    if change_type == 'combat.round.advance':
        return (change_type, int_or_default(change.get('round'), default=0))
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
        return (
            change_type,
            normalize_item_name(change.get('participantId') or change.get('enemyId') or change.get('combatId')),
            normalize_item_name(change.get('intentType') or change.get('status') or change.get('round')),
        )
    return None


def _inventory_change_quantity(change: dict[str, Any]) -> int:
    item = change.get('item') if isinstance(change.get('item'), dict) else {}
    return int_or_default(change.get('quantity', item.get('quantity')), default=1)


def _inventory_changes_semantically_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_type = str(left.get('type') or '').strip()
    right_type = str(right.get('type') or '').strip()
    inventory_types = {
        'inventory.add',
        'inventory.remove',
        'inventory.equip',
        'inventory.unequip',
        'inventory.transfer',
    }
    if left_type != right_type or left_type not in inventory_types:
        return False
    if _change_actor_id(left) != _change_actor_id(right):
        return False
    if _inventory_change_quantity(left) != _inventory_change_quantity(right):
        return False
    if normalize_item_name(left.get('slot')) != normalize_item_name(right.get('slot')):
        return False
    if left_type == 'inventory.transfer' and (
        _transfer_target_actor_id(left) != _transfer_target_actor_id(right)
    ):
        return False
    left_item = left.get('item') if isinstance(left.get('item'), dict) else {}
    right_item = right.get('item') if isinstance(right.get('item'), dict) else {}
    left_id = str(left.get('itemId') or left.get('item_id') or left_item.get('id') or '').strip()
    right_id = str(right.get('itemId') or right.get('item_id') or right_item.get('id') or '').strip()
    if bool(left_id) != bool(right_id):
        left_name = normalize_item_name(left.get('itemName') or left.get('item_name') or left_item.get('name'))
        right_name = normalize_item_name(right.get('itemName') or right.get('item_name') or right_item.get('name'))
        if not left_name or left_name != right_name:
            return False
        correlation_id = str(left.get('_semanticCorrelationId') or '').strip()
        return bool(correlation_id and correlation_id == str(right.get('_semanticCorrelationId') or '').strip())
    return _item_reference_matches(left, right)


def _correlate_inventory_intent_changes(
    proposed_changes: list[dict[str, Any]],
    intent_changes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    proposed = [deepcopy(change) for change in proposed_changes if isinstance(change, dict)]
    intents = [deepcopy(change) for change in intent_changes if isinstance(change, dict)]
    for intent_index, intent in enumerate(intents):
        intent_type = str(intent.get('type') or '').strip()
        if intent_type not in {'inventory.add', 'inventory.remove', 'inventory.equip', 'inventory.unequip'}:
            continue
        candidates = [
            index
            for index, proposed_change in enumerate(proposed)
            if _inventory_changes_semantically_match(
                {**intent, '_semanticCorrelationId': 'candidate'},
                {**proposed_change, '_semanticCorrelationId': 'candidate'},
            )
        ]
        if len(candidates) != 1:
            continue
        proposed_index = candidates[0]
        intent_item = intent.get('item') if isinstance(intent.get('item'), dict) else {}
        correlation_id = stable_change_id(
            intent.get('turnId'),
            'confirmed_inventory_action',
            intent_type,
            _change_actor_id(intent),
            normalize_item_name(intent.get('itemName') or intent_item.get('name')),
            _inventory_change_quantity(intent),
        )
        intents[intent_index]['_semanticCorrelationId'] = correlation_id
        proposed[proposed_index]['_semanticCorrelationId'] = correlation_id
    return proposed, intents


def _merge_state_changes(
    *change_lists: list[dict[str, Any]],
    seed_changes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    semantically_seen: list[dict[str, Any]] = []
    for change in seed_changes or []:
        if isinstance(change, dict):
            semantically_seen.append(change)
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
            if any(
                _inventory_changes_semantically_match(change, existing)
                for existing in semantically_seen
            ):
                continue
            if signature:
                seen.add(signature)
            merged.append({key: value for key, value in change.items() if key != '_semanticCorrelationId'})
            semantically_seen.append(change)
    return merged


def _change_actor_id(change: dict[str, Any]) -> str:
    return str(change.get('actorId') or change.get('actor_id') or '').strip()


def _transfer_source_actor_id(change: dict[str, Any]) -> str:
    return str(change.get('fromActorId') or change.get('from_actor_id') or change.get('actorId') or change.get('actor_id') or '').strip()


def _transfer_target_actor_id(change: dict[str, Any]) -> str:
    return str(change.get('toActorId') or change.get('to_actor_id') or '').strip()


def _item_reference_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_item = left.get('item') if isinstance(left.get('item'), dict) else {}
    right_item = right.get('item') if isinstance(right.get('item'), dict) else {}
    left_id = str(left.get('itemId') or left.get('item_id') or left_item.get('id') or left_item.get('itemId') or '').strip()
    right_id = str(right.get('itemId') or right.get('item_id') or right_item.get('id') or right_item.get('itemId') or '').strip()
    if left_id and right_id:
        return left_id == right_id
    if left_id or right_id:
        return False
    left_name = normalize_item_name(left.get('itemName') or left.get('item_name') or left_item.get('name'))
    right_name = normalize_item_name(right.get('itemName') or right.get('item_name') or right_item.get('name'))
    return bool(left_name and right_name and left_name == right_name)


def _confirmed_item_reference_matches(change: dict[str, Any], confirmed: dict[str, Any]) -> bool:
    change_item = change.get('item') if isinstance(change.get('item'), dict) else {}
    confirmed_item = confirmed.get('item') if isinstance(confirmed.get('item'), dict) else {}
    change_id = str(change.get('itemId') or change.get('item_id') or change_item.get('id') or '').strip()
    confirmed_id = str(confirmed.get('itemId') or confirmed.get('item_id') or confirmed_item.get('id') or '').strip()
    if change_id and confirmed_id:
        return change_id == confirmed_id
    change_name = normalize_item_name(change.get('itemName') or change.get('item_name') or change_item.get('name'))
    confirmed_name = normalize_item_name(
        confirmed.get('itemName') or confirmed.get('item_name') or confirmed_item.get('name')
    )
    return bool(change_name and change_name == confirmed_name)


def _same_positive_amount(left: dict[str, Any], right: dict[str, Any], key: str) -> bool:
    return int_or_default(left.get(key), default=0) > 0 and int_or_default(left.get(key), default=0) == int_or_default(right.get(key), default=0)


def _change_overlaps_confirmed_transfer(change: dict[str, Any], confirmed_transfers: list[dict[str, Any]]) -> bool:
    change_type = str(change.get('type') or '').strip()
    actor_id = _change_actor_id(change)
    for transfer in confirmed_transfers:
        transfer_type = str(transfer.get('type') or '').strip()
        if transfer_type == 'inventory.transfer':
            if change_type == 'inventory.remove' and actor_id == _transfer_source_actor_id(transfer):
                if _same_positive_amount(change, transfer, 'quantity') and _confirmed_item_reference_matches(change, transfer):
                    return True
            if change_type == 'inventory.add' and actor_id == _transfer_target_actor_id(transfer):
                if _same_positive_amount(change, transfer, 'quantity') and _confirmed_item_reference_matches(change, transfer):
                    return True
        elif transfer_type == 'currency.transfer':
            currency = str(change.get('currency') or '').strip().lower()
            transfer_currency = str(transfer.get('currency') or '').strip().lower()
            if currency != transfer_currency:
                continue
            if change_type == 'currency.remove' and actor_id == _transfer_source_actor_id(transfer):
                if _same_positive_amount(change, transfer, 'amount'):
                    return True
            if change_type == 'currency.add' and actor_id == _transfer_target_actor_id(transfer):
                if _same_positive_amount(change, transfer, 'amount'):
                    return True
    return False


def _without_confirmed_transfer_overlaps(
    changes: list[dict[str, Any]],
    confirmed_transfers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not confirmed_transfers:
        return changes
    return [
        change
        for change in changes or []
        if isinstance(change, dict) and not _change_overlaps_confirmed_transfer(change, confirmed_transfers)
    ]


def _without_confirmed_inventory_overlaps(
    changes: list[dict[str, Any]],
    confirmed_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    confirmed_inventory = [
        change
        for change in confirmed_changes
        if isinstance(change, dict)
        and str(change.get('type') or '').strip()
        in {'inventory.add', 'inventory.remove', 'inventory.equip', 'inventory.unequip'}
    ]
    if not confirmed_inventory:
        return changes
    filtered: list[dict[str, Any]] = []
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        overlaps = any(
            str(change.get('type') or '').strip() == str(confirmed.get('type') or '').strip()
            and _change_actor_id(change) == _change_actor_id(confirmed)
            and _inventory_change_quantity(change) == _inventory_change_quantity(confirmed)
            and _confirmed_item_reference_matches(change, confirmed)
            for confirmed in confirmed_inventory
        )
        if not overlaps:
            filtered.append(change)
    return filtered


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
        item_id = str(inventory_change.get('item_id') or '').strip()
        quantity = max(1, int_or_default(inventory_change.get('quantity'), default=1))
        if change_type and item_name:
            change: dict[str, Any] = {
                'id': stable_change_id(turn.turn_id, 'post_dm_intent', change_type, actor_id, item_id or item_name, quantity),
                'turnId': turn.turn_id,
                'type': change_type,
                'source': 'post_dm',
                'actorId': actor_id,
                'itemName': item_name,
                **({'itemId': item_id} if item_id else {}),
                'quantity': quantity,
                'reason': f"DM confirmed requested inventory action for {item_name}.",
                'visible': True,
            }
            if change_type == 'inventory.add':
                change['item'] = {
                    **({'id': item_id} if item_id else {}),
                    'name': item_name,
                    'quantity': quantity,
                    'type': 'misc',
                }
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
    combat_context: dict[str, Any] | None = None,
    resolved_player_roll: bool = False,
    enemy_roller: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    compact = compact_state_for_extraction(state)
    raw_pending_rolls = pre_validation.get('pendingRolls')
    pending_rolls = [roll for roll in raw_pending_rolls if isinstance(roll, dict)] if isinstance(raw_pending_rolls, list) else []
    dm_combat = build_dm_combat_context(
        state=state,
        combat_context=combat_context,
        pending_rolls=pending_rolls,
        resolved_player_roll=resolved_player_roll,
        enemy_roller=enemy_roller,
    )
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
    instructions = [
        'Narrate valid actions as possible.',
        'Anchor narration to the latest playerMessage and validatedActions.',
        'Do not substitute a different known object for the object named or described in the latest playerMessage.',
        'Do not narrate invalid actions as successful.',
        'If an action is invalid, explain it naturally in-world.',
        'Do not output JSON.',
        'Do not claim state changes that contradict validatedActions.',
    ]
    if combat_context:
        instructions.extend(
            [
                'Enemy rolls are engine-owned. Never ask the player to roll enemy attacks, enemy saving throws, enemy checks, or enemy damage.',
                'If combatState.enemyResolvedActions is present, narrate those exact enemy results, including attack totals, hit or miss, damage totals, and targets.',
                'If combatState.enemyResolvedActions is absent, do not invent enemy roll results and do not request enemy rolls from the player.',
                'Only ask the player for rolls listed in pendingRolls.',
                'Do not make fleeing, surrendering, or negotiating enemies fight to the death unless blocked or forced.',
                'Use enemy morale, survival instincts, and objectives when describing choices.',
                'Do not directly mutate game state; narrate concrete outcomes clearly for extraction and validation.',
            ]
        )
    return {
        'currentStateSummary': compact,
        'playerMessage': player_message,
        'validatedActions': validated_actions,
        'validActions': valid_actions,
        'invalidActions': invalid_actions,
        'pendingActions': pending_actions,
        'needsClarification': needs_clarification,
        'pendingRolls': pending_rolls,
        'stateChangesAlreadyApplied': [
            {
                'type': change.get('type'),
                'locationId': change.get('locationId'),
                'locationName': change.get('locationName') or change.get('name'),
                'questId': change.get('questId'),
                'questTitle': change.get('questTitle') or change.get('title'),
                'npcId': change.get('npcId'),
                'npcName': change.get('npcName') or change.get('name'),
                'flagKey': change.get('flagKey'),
                'itemName': change.get('itemName'),
                'slot': change.get('slot'),
                'amount': change.get('actualAmount', change.get('amount')),
                'currency': change.get('currency'),
                'xp': change.get('actualAmount', change.get('amount')) if str(change.get('type') or '').startswith('xp.') else None,
                'combatStatus': change.get('combatStatus'),
                'participantName': change.get('participantName'),
                'intentType': change.get('intentType'),
            }
            for change in applied_changes
            if isinstance(change, dict) and change.get('visible', True)
        ],
        'combatState': dm_combat,
        'dmInstructions': instructions,
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
    active_player_ids: list[int] | None = None,
    before_helper_call: Callable[[], None] | None = None,
) -> dict[str, Any]:
    players = _players_for_campaign(campaign, player)
    players_by_id = {player_obj.player_id: player_obj for player_obj in players}
    orm_token = _pipeline_orm_token(
        turn=turn,
        session_obj=session_obj,
        campaign=campaign,
        player=player,
        players=players,
    )
    state = state_snapshot_for_session(
        session_obj=session_obj,
        campaign=campaign,
        players=players,
        active_player_ids=active_player_ids,
    )
    recent_timeline = recent_timeline_for_session(session_obj.session_id, limit=5)
    actor_id = display_actor_id(player.player_id)

    if declared_actions_override:
        pre_extraction = {
            'declaredActions': declared_actions_override,
            'notes': ['clarification_resume'],
        }
    else:
        helper_session_released = False

        def _before_provider_call() -> None:
            nonlocal helper_session_released
            if before_helper_call:
                before_helper_call()
                helper_session_released = True

        pre_extraction = extract_pre_dm_actions(
            current_state=compact_state_for_extraction(state),
            player_message=player_message,
            recent_timeline=recent_timeline,
            actor_id=actor_id,
            action_intent=action_intent,
            force_helper=_turn_awaits_player_roll(turn),
            before_provider_call=_before_provider_call if before_helper_call else None,
        )
        if helper_session_released:
            turn, session_obj, campaign, player, players = _reload_pipeline_orm(orm_token)
            players_by_id = {player_obj.player_id: player_obj for player_obj in players}
    pre_validation = validate_declared_actions(
        state=state,
        declared_actions=pre_extraction.get('declaredActions') or [],
        current_turn=turn.turn_id,
        recent_context=_recent_context_strings(recent_timeline),
        selected_item_ids=selected_item_ids,
        expected_actor_id=actor_id,
    )
    pre_immediate_changes = [
        change
        for change in (pre_validation.get('immediateChanges') or [])
        if isinstance(change, dict)
    ]
    safe_immediate_changes = [change for change in pre_immediate_changes if _safe_pre_dm_immediate_change(change)]
    pending_immediate_changes = [change for change in pre_immediate_changes if not _safe_pre_dm_immediate_change(change)]
    immediate_validation = validate_state_changes(state=state, changes=safe_immediate_changes, expected_actor_id=actor_id)
    pending_immediate_validation = validate_state_changes(state=state, changes=pending_immediate_changes, expected_actor_id=actor_id)
    immediate_changes = validated_changes_for_application(immediate_validation)
    apply_result = apply_state_changes(state, immediate_changes)
    state_after_immediate = apply_result['nextState']
    applied_immediate = apply_result['appliedChanges']
    combat_session_released = False

    def _before_combat_provider_call() -> None:
        nonlocal combat_session_released
        if before_helper_call:
            before_helper_call()
            combat_session_released = True

    combat_prepare = prepare_combat_for_turn(
        state=state_after_immediate,
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        player_message=player_message,
        workspace_id=campaign.workspace_id,
        before_intent_provider_call=(
            _before_combat_provider_call if before_helper_call else None
        ),
        before_creature_provider_call=(
            _before_combat_provider_call if before_helper_call else None
        ),
    )
    if combat_session_released:
        turn, session_obj, campaign, player, players = _reload_pipeline_orm(orm_token)
        players_by_id = {player_obj.player_id: player_obj for player_obj in players}
    combat_changes = [
        change
        for change in (combat_prepare.get('changes') or [])
        if isinstance(change, dict)
    ]
    combat_validation = validate_state_changes(state=state_after_immediate, changes=combat_changes)
    applied_combat_changes = validated_changes_for_application(combat_validation)
    combat_apply = apply_state_changes(state_after_immediate, applied_combat_changes)
    state_before_dm = combat_apply['nextState']
    applied_combat = combat_apply['appliedChanges']
    finalize_combat_prepare(
        session_obj=session_obj,
        campaign=campaign,
        prepare_result=combat_prepare,
        applied_changes=applied_combat,
        final_state=state_before_dm,
    )
    if applied_immediate or applied_combat:
        persist_state_to_database(session_obj=session_obj, state=state_before_dm, players_by_id=players_by_id)
    else:
        session_obj.state_snapshot = safe_json_dumps(state_before_dm, {})
    record_combat_debug_from_prepare(
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        prepare_result=combat_prepare,
    )

    dm_context = _dm_context_packet(
        state=state_before_dm,
        player_message=player_message,
        pre_validation=(
            {
                **pre_validation,
                'pendingRolls': [_turn_level_pending_roll(turn, actor_id=actor_id)],
            }
            if _turn_awaits_player_roll(turn) and not (pre_validation.get('pendingRolls') or [])
            else pre_validation
        ),
        applied_changes=[*applied_immediate, *applied_combat],
        combat_context=combat_prepare.get('combatContext') if isinstance(combat_prepare.get('combatContext'), dict) else None,
        resolved_player_roll=_turn_awaits_player_roll(turn) or _resolved_player_roll_should_defer_enemy(turn),
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
        'stateBeforeDm': state_before_dm,
        'preDmExtraction': pre_extraction,
        'preDmValidation': pre_validation,
        'immediateValidation': immediate_validation,
        'pendingImmediateChanges': validated_changes_for_application(pending_immediate_validation),
        'pendingImmediateValidation': pending_immediate_validation,
        'immediateAppliedChanges': applied_immediate,
        'combatPreDmChanges': combat_changes,
        'combatPreDmValidation': combat_validation,
        'combatAppliedChanges': applied_combat,
        'combatDebug': combat_prepare.get('debug') if isinstance(combat_prepare.get('debug'), dict) else {},
        'dmContextPacket': dm_context,
        'stateLog': state_log,
        'managedDomains': MANAGED_STATE_DOMAINS,
    }
    _set_metadata(turn, metadata)
    db.session.flush()

    return {
        'stateBeforeDm': state_before_dm,
        'playersById': players_by_id,
        'preExtraction': pre_extraction,
        'preValidation': pre_validation,
        'immediateValidation': immediate_validation,
        'pendingImmediateValidation': pending_immediate_validation,
        'pendingImmediateChanges': validated_changes_for_application(pending_immediate_validation),
        'immediateAppliedChanges': applied_immediate,
        'combatValidation': combat_validation,
        'combatAppliedChanges': applied_combat,
        'combatDebug': combat_prepare.get('debug') if isinstance(combat_prepare.get('debug'), dict) else {},
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
    active_player_ids: list[int] | None = None,
    before_helper_call: Callable[[], None] | None = None,
) -> dict[str, Any]:
    metadata = _metadata(turn)
    pipeline = metadata.get(STATE_PIPELINE_METADATA_KEY) if isinstance(metadata.get(STATE_PIPELINE_METADATA_KEY), dict) else {}
    players = _players_for_campaign(campaign, player)
    players_by_id = {player_obj.player_id: player_obj for player_obj in players}
    orm_token = _pipeline_orm_token(
        turn=turn,
        session_obj=session_obj,
        campaign=campaign,
        player=player,
        players=players,
    )
    state_before_dm = pipeline.get('stateBeforeDm')
    if not isinstance(state_before_dm, dict):
        state_before_dm = state_snapshot_for_session(
            session_obj=session_obj,
            campaign=campaign,
            players=players,
            active_player_ids=active_player_ids,
        )
    elif active_player_ids is not None:
        state_before_dm = deepcopy(state_before_dm)
        active_ids: list[int] = []
        for raw_id in active_player_ids:
            parsed = int_or_default(raw_id, default=0)
            if parsed > 0 and parsed not in active_ids:
                active_ids.append(parsed)
        state_before_dm['activePlayerIds'] = active_ids

    actor_id = str(pipeline.get('actorId') or display_actor_id(player.player_id))
    recent_timeline = recent_timeline_for_session(session_obj.session_id, limit=5)
    already_applied = [*(pipeline.get('immediateAppliedChanges') or []), *(pipeline.get('combatAppliedChanges') or [])]
    pending_immediate_changes = [
        change
        for change in (pipeline.get('pendingImmediateChanges') or [])
        if isinstance(change, dict)
    ]
    post_combat_prepare: dict[str, Any] = {'debug': {}}
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
        helper_session_released = False

        def _before_provider_call() -> None:
            nonlocal helper_session_released
            if before_helper_call:
                before_helper_call()
                helper_session_released = True

        post_extraction = extract_post_dm_outcomes(
            state_before_dm=compact_state_for_extraction(state_before_dm),
            player_message=turn.player_input,
            validated_actions=pipeline.get('preDmValidation') if isinstance(pipeline.get('preDmValidation'), dict) else {},
            already_applied_changes=already_applied,
            dm_response=dm_response_text,
            recent_timeline=recent_timeline,
            actor_id=actor_id,
            turn_id=turn.turn_id,
            before_provider_call=_before_provider_call if before_helper_call else None,
        )
        if helper_session_released:
            turn, session_obj, campaign, player, players = _reload_pipeline_orm(orm_token)
            players_by_id = {player_obj.player_id: player_obj for player_obj in players}
        combat_session_released = False

        def _before_combat_provider_call() -> None:
            nonlocal combat_session_released
            if before_helper_call:
                before_helper_call()
                combat_session_released = True

        post_combat_prepare = prepare_combat_from_dm_response(
            state=state_before_dm,
            session_obj=session_obj,
            campaign=campaign,
            turn=turn,
            player_message=turn.player_input or '',
            dm_response=dm_response_text,
            workspace_id=campaign.workspace_id,
            before_intent_provider_call=(
                _before_combat_provider_call if before_helper_call else None
            ),
            before_creature_provider_call=(
                _before_combat_provider_call if before_helper_call else None
            ),
        )
        if combat_session_released:
            turn, session_obj, campaign, player, players = _reload_pipeline_orm(orm_token)
            players_by_id = {player_obj.player_id: player_obj for player_obj in players}
        post_combat_changes = [
            change
            for change in (post_combat_prepare.get('changes') or [])
            if isinstance(change, dict)
        ]
        if post_combat_changes:
            post_extraction = deepcopy(post_extraction)
            post_extraction['proposedChanges'] = _merge_state_changes(
                post_combat_changes,
                post_extraction.get('proposedChanges') or [],
                seed_changes=already_applied,
            )
            notes = list(post_extraction.get('notes') or [])
            if 'post_dm_combat_adjudicator' not in notes:
                notes.append('post_dm_combat_adjudicator')
            post_extraction['notes'] = notes
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
            confirmed_transfers = [
                change
                for change in confirmed_pre_dm_changes
                if isinstance(change, dict) and str(change.get('type') or '').strip() in {'inventory.transfer', 'currency.transfer'}
            ]
            proposed_inventory_changes, correlated_intent_changes = _correlate_inventory_intent_changes(
                _without_confirmed_inventory_overlaps(
                    _without_confirmed_transfer_overlaps(
                        post_extraction.get('proposedChanges') or [],
                        confirmed_transfers,
                    ),
                    confirmed_pre_dm_changes,
                ),
                _without_confirmed_inventory_overlaps(
                    _without_confirmed_transfer_overlaps(intent_changes, confirmed_transfers),
                    confirmed_pre_dm_changes,
                ),
            )
            post_extraction['proposedChanges'] = _merge_state_changes(
                proposed_inventory_changes,
                correlated_intent_changes,
                confirmed_pre_dm_changes,
                seed_changes=already_applied,
            )
            notes = list(post_extraction.get('notes') or [])
            if intent_changes and 'intent_confirmed_post_dm' not in notes:
                notes.append('intent_confirmed_post_dm')
            if confirmed_pre_dm_changes and 'pre_dm_confirmed_post_dm' not in notes:
                notes.append('pre_dm_confirmed_post_dm')
            post_extraction['notes'] = notes
        proposed_before_dedupe = [
            change
            for change in (post_extraction.get('proposedChanges') or [])
            if isinstance(change, dict)
        ]
        proposed_after_dedupe = _merge_state_changes(proposed_before_dedupe, seed_changes=already_applied)
        if len(proposed_after_dedupe) != len(proposed_before_dedupe):
            post_extraction = deepcopy(post_extraction)
            post_extraction['proposedChanges'] = proposed_after_dedupe
            notes = list(post_extraction.get('notes') or [])
            if 'post_dm_semantic_dedupe' not in notes:
                notes.append('post_dm_semantic_dedupe')
            post_extraction['notes'] = notes
        dm_context_packet = pipeline.get('dmContextPacket') if isinstance(pipeline.get('dmContextPacket'), dict) else {}
        trusted_damage = derive_trusted_damage_changes(
            state=state_before_dm,
            dm_context_packet=dm_context_packet,
            actor_id=actor_id,
            turn_id=turn.turn_id,
            already_applied_changes=already_applied,
        )
        trusted_enemy_damage_changes = trusted_damage.enemy
        trusted_resolved_damage_changes = trusted_damage.resolved
        trusted_damage_changes = trusted_damage.all_changes
        if trusted_damage_changes:
            post_extraction = deepcopy(post_extraction)
            post_extraction['proposedChanges'] = [
                *trusted_damage_changes,
                *without_trusted_damage_overlaps(
                    post_extraction.get('proposedChanges') or [],
                    trusted_damage_changes,
                ),
            ]
            notes = list(post_extraction.get('notes') or [])
            if trusted_enemy_damage_changes and 'trusted_enemy_resolved_damage' not in notes:
                notes.append('trusted_enemy_resolved_damage')
            if trusted_resolved_damage_changes and 'trusted_resolved_damage' not in notes:
                notes.append('trusted_resolved_damage')
            post_extraction['notes'] = notes

        trusted_damage_ids = {
            str(change.get('id') or '').strip()
            for change in trusted_damage_changes
            if isinstance(change, dict) and str(change.get('id') or '').strip()
        }
        proposed_changes = [
            change
            for change in (post_extraction.get('proposedChanges') or [])
            if isinstance(change, dict)
        ]
        trusted_changes = [
            change
            for change in proposed_changes
            if str(change.get('id') or '').strip() in trusted_damage_ids
        ]
        untrusted_changes = [
            change
            for change in proposed_changes
            if str(change.get('id') or '').strip() not in trusted_damage_ids
        ]
        if trusted_changes:
            trusted_validation = validate_state_changes(state=state_before_dm, changes=trusted_changes)
            untrusted_validation = validate_state_changes(
                state=state_before_dm,
                changes=untrusted_changes,
                expected_actor_id=actor_id,
                authorized_cross_actor_change_ids=post_extraction.get('authorizedCrossActorChangeIds') or [],
            )
            post_validation = _merge_validation_results(trusted_validation, untrusted_validation)
        else:
            post_validation = validate_state_changes(
                state=state_before_dm,
                changes=untrusted_changes,
                expected_actor_id=actor_id,
                authorized_cross_actor_change_ids=post_extraction.get('authorizedCrossActorChangeIds') or [],
            )
        post_changes = validated_changes_for_application(post_validation)
        post_apply = apply_state_changes(state_before_dm, post_changes)
        final_state = post_apply['nextState']
        applied_post = post_apply['appliedChanges']
        turn_advance_change = combat_turn_advance_change(state=final_state, turn=turn, actor_id=actor_id)
        if turn_advance_change:
            advance_validation = validate_state_changes(state=final_state, changes=[turn_advance_change], expected_actor_id=actor_id)
            advance_changes = validated_changes_for_application(advance_validation)
            advance_apply = apply_state_changes(final_state, advance_changes)
            final_state = advance_apply['nextState']
            applied_post = [*applied_post, *advance_apply['appliedChanges']]
            post_validation = _merge_validation_results(post_validation, advance_validation)
            post_extraction = deepcopy(post_extraction)
            post_extraction['proposedChanges'] = [
                *(post_extraction.get('proposedChanges') or []),
                turn_advance_change,
            ]
            notes = list(post_extraction.get('notes') or [])
            if 'combat_turn_roster_advanced' not in notes:
                notes.append('combat_turn_roster_advanced')
            post_extraction['notes'] = notes
        finalized_encounter = finalize_combat_prepare(
            session_obj=session_obj,
            campaign=campaign,
            prepare_result=post_combat_prepare,
            applied_changes=applied_post,
            final_state=final_state,
        )
        if applied_post:
            persist_state_to_database(session_obj=session_obj, state=final_state, players_by_id=players_by_id)
        else:
            session_obj.state_snapshot = safe_json_dumps(final_state, {})
        if finalized_encounter is None and any(
            str(change.get('type') or '').startswith('combat.')
            for change in applied_post
            if isinstance(change, dict)
        ):
            sync_combat_encounter_record(
                session_obj=session_obj,
                campaign=campaign,
                combat=(
                    final_state.get('combat')
                    if isinstance(final_state.get('combat'), dict)
                    else {}
                ),
            )

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
    record_combat_debug_from_outcome(
        session_obj=session_obj,
        campaign=campaign,
        turn=turn,
        prepare_result=post_combat_prepare,
        post_validation=post_validation,
        applied_changes=applied_post,
        state_log=state_log,
    )

    pipeline.update(
        {
            'stateBeforeDm': state_before_dm,
            'postDmExtraction': post_extraction,
            'postDmCombatDebug': post_combat_prepare.get('debug') if isinstance(post_combat_prepare.get('debug'), dict) else {},
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
