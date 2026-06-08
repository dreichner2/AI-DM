from __future__ import annotations

from collections import Counter
from collections import defaultdict
import json
import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from aidm_server import canon_retrieval as _canon_retrieval
from aidm_server.canon_inventory import (
    INVENTORY_GAIN_PATTERNS as _INVENTORY_GAIN_PATTERNS,
    INVENTORY_LOSS_PATTERNS as _INVENTORY_LOSS_PATTERNS,
    apply_inventory_changes as _apply_inventory_changes,
    append_drop_all_inventory_changes_from_text as _append_drop_all_inventory_changes_from_text,
    append_inventory_change_from_intent_outcome as _append_inventory_change_from_intent_outcome,
    append_verified_provider_inventory_changes as _append_verified_provider_inventory_changes,
    clean_inventory_item_name as _clean_inventory_item_name,
    extract_explicit_inventory_state_changes_from_text as _extract_explicit_inventory_state_changes_from_text,
    extract_inventory_changes_from_text as _extract_inventory_changes_from_text,
    inventory_payload,
    looks_like_inventory_item as _looks_like_inventory_item,
)
from aidm_server.canon_location import infer_location_update
from aidm_server.canon_projection import append_session_memory, refresh_session_projection
from aidm_server.canon_text import (
    normalized_name as _normalized_name,
    optional_float as _optional_float,
    positive_int as _positive_int,
)
from aidm_server.character_state import apply_character_state_changes as _apply_character_state_changes
from aidm_server.database import db
from aidm_server.models import (
    Campaign,
    DmTurn,
    StoryEntity,
    StoryFact,
    StoryThread,
    TurnCanonUpdate,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.prompt_templates import build_canon_extraction_request
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.time_utils import utc_now


_ENTITY_RE = re.compile(r'\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\b')
IMMEDIATE_STATE_METADATA_KEY = 'immediate_state_changes_applied'
EMERGENT_ENTITY_CANDIDATE_LIMIT = _canon_retrieval.EMERGENT_ENTITY_CANDIDATE_LIMIT
EMERGENT_FACT_CANDIDATE_LIMIT = _canon_retrieval.EMERGENT_FACT_CANDIDATE_LIMIT
EMERGENT_THREAD_CANDIDATE_LIMIT = _canon_retrieval.EMERGENT_THREAD_CANDIDATE_LIMIT
_ENTITY_STOPWORDS = {
    'The',
    'This',
    'That',
    'Your',
    'You',
    'And',
    'But',
    'For',
    'With',
    'Describe',
    'Roll',
    'Smoke',
    'World',
}

_EMPTY_PATCH = {
    'entities': [],
    'facts': [],
    'threads': [],
    'inventory_changes': [],
    'projection': {},
}

_ALLOWED_FACT_CHANGE_TYPES = {'reveal', 'retcon', 'misconception', 'correction'}
_GLOBAL_SINGLETON_FACTS = {'current_location', 'current_quest'}
_SUBJECT_SINGLETON_FACTS = {'status', 'role', 'current_holder'}


def _empty_patch() -> dict:
    return {
        'entities': [],
        'facts': [],
        'threads': [],
        'inventory_changes': [],
        'projection': {},
    }


def _extract_json_object(text: str) -> dict | None:
    candidate = (text or '').strip()
    if not candidate:
        return None
    try:
        loaded = json.loads(candidate)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r'\{.*\}', candidate, re.DOTALL)
    if not match:
        return None

    try:
        loaded = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _normalize_patch(raw_patch: dict | None) -> dict:
    patch = _empty_patch()
    if not isinstance(raw_patch, dict):
        return patch

    for key in ('entities', 'facts', 'threads', 'inventory_changes'):
        value = raw_patch.get(key, [])
        patch[key] = value if isinstance(value, list) else []

    projection = raw_patch.get('projection', {})
    patch['projection'] = projection if isinstance(projection, dict) else {}
    return patch


def _candidate_labels(entity: StoryEntity) -> set[str]:
    labels = {
        _normalized_name(entity.name),
        _normalized_name(entity.canonical_name),
    }
    aliases = safe_json_loads(entity.aliases_json, [])
    aliases = aliases if isinstance(aliases, list) else []
    labels.update(_normalized_name(alias) for alias in aliases)
    return {label for label in labels if label}


def _token_aliases(entity: StoryEntity) -> set[str]:
    aliases: set[str] = set()
    for label in _candidate_labels(entity):
        parts = label.split()
        if len(parts) > 1:
            aliases.add(parts[0])
            aliases.add(parts[-1])
    return aliases


class _EntityLookupIndex:
    def __init__(self, campaign_id: int):
        self.campaign_id = campaign_id
        self.entities_by_id: dict[int, StoryEntity] = {}
        self._exact: dict[str, set[int]] = defaultdict(set)
        self._token: dict[str, set[int]] = defaultdict(set)
        self._memberships: dict[int, tuple[set[str], set[str]]] = {}

        for entity in StoryEntity.query.filter_by(campaign_id=campaign_id).all():
            self.refresh(entity)

    def refresh(self, entity: StoryEntity):
        previous = self._memberships.pop(entity.entity_id, (set(), set()))
        previous_exact, previous_token = previous
        for label in previous_exact:
            ids = self._exact.get(label)
            if ids is not None:
                ids.discard(entity.entity_id)
                if not ids:
                    del self._exact[label]
        for token in previous_token:
            ids = self._token.get(token)
            if ids is not None:
                ids.discard(entity.entity_id)
                if not ids:
                    del self._token[token]

        exact_labels = _candidate_labels(entity)
        token_labels = _token_aliases(entity)
        self.entities_by_id[entity.entity_id] = entity
        self._memberships[entity.entity_id] = (exact_labels, token_labels)

        for label in exact_labels:
            self._exact[label].add(entity.entity_id)
        for token in token_labels:
            self._token[token].add(entity.entity_id)

    def find(self, incoming_type: str, incoming_name: str) -> StoryEntity | None:
        normalized_incoming = _normalized_name(incoming_name)
        if not normalized_incoming:
            return None

        exact_matches = [
            self.entities_by_id[entity_id]
            for entity_id in sorted(self._exact.get(normalized_incoming, set()))
            if _entity_type_matches(self.entities_by_id[entity_id].entity_type, incoming_type)
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]

        if ' ' not in normalized_incoming:
            token_matches = [
                self.entities_by_id[entity_id]
                for entity_id in sorted(self._token.get(normalized_incoming, set()))
                if _entity_type_matches(self.entities_by_id[entity_id].entity_type, incoming_type)
            ]
            if len(token_matches) == 1:
                return token_matches[0]

        return exact_matches[0] if exact_matches else None


def _entity_type_matches(existing_type: str, incoming_type: str) -> bool:
    if not incoming_type or incoming_type == 'entity':
        return True
    if existing_type == incoming_type:
        return True
    return existing_type == 'entity'


def _find_existing_entity(
    campaign_id: int,
    incoming_type: str,
    incoming_name: str,
    lookup_index: _EntityLookupIndex | None = None,
) -> StoryEntity | None:
    if lookup_index is not None:
        return lookup_index.find(incoming_type, incoming_name)
    return _EntityLookupIndex(campaign_id).find(incoming_type, incoming_name)


def _heuristic_patch(
    turn: DmTurn,
    campaign: Campaign,
    dm_output: str,
    speaking_player_name: str | None,
    triggered_segments: list[dict],
) -> dict:
    patch = _normalize_patch(None)
    location = infer_location_update(turn.player_input, dm_output)
    if location:
        patch['entities'].append(
            {
                'entity_type': 'location',
                'name': location,
                'summary': 'Active scene location mentioned during play.',
                'status': 'active',
            }
        )
        patch['facts'].append(
            {
                'predicate': 'current_location',
                'value_text': location,
                'confidence': 0.72,
                'replace_existing': True,
            }
        )
        patch['projection']['current_location'] = location

    candidate_names = Counter()
    source_text = f'{turn.player_input}\n{dm_output}'
    for match in _ENTITY_RE.findall(source_text):
        if match in _ENTITY_STOPWORDS:
            continue
        if speaking_player_name and match.lower() == speaking_player_name.lower():
            continue
        if match.lower() in {
            (campaign.title or '').lower(),
            (campaign.description or '').lower(),
        }:
            continue
        candidate_names[match] += 1

    for name, _count in candidate_names.most_common(6):
        patch['entities'].append(
            {
                'entity_type': 'npc',
                'name': name,
                'summary': 'Named in improvised narration.',
                'status': 'active',
            }
        )

    for segment_payload in triggered_segments:
        title = str(segment_payload.get('title') or '').strip()
        if not title:
            continue
        patch['threads'].append(
            {
                'title': title,
                'summary': f'Authored story thread activated: {title}.',
                'status': 'open',
                'priority': 2,
                'source': 'segment',
                'metadata': {
                    'segment_id': segment_payload.get('segment_id'),
                    'reason': segment_payload.get('reason'),
                },
            }
        )

    _extract_inventory_changes_from_text(dm_output or '', _INVENTORY_GAIN_PATTERNS, 'acquire', patch)
    _extract_inventory_changes_from_text(dm_output or '', _INVENTORY_LOSS_PATTERNS, 'lose', patch)
    _extract_explicit_inventory_state_changes_from_text(dm_output or '', patch)
    _append_drop_all_inventory_changes_from_text(turn, dm_output or '', patch)
    _append_inventory_change_from_intent_outcome(turn, dm_output or '', patch)

    return patch


def _extract_with_provider(
    turn: DmTurn,
    campaign: Campaign,
    dm_output: str,
    speaking_player_name: str | None,
    triggered_segments: list[dict],
) -> tuple[dict | None, str | None]:
    from aidm_server.llm import get_provider

    context = build_emergent_context(campaign.campaign_id, session_id=turn.session_id, entity_limit=8, fact_limit=12, thread_limit=8)
    provider = get_provider()

    request = build_canon_extraction_request(
        context=context,
        campaign_title=campaign.title,
        player_input=turn.player_input,
        dm_output=dm_output,
        speaking_player_name=speaking_player_name,
        triggered_segments=triggered_segments,
    )

    try:
        response = provider.generate(request)
    except Exception as exc:
        telemetry_event(
            'memory.extract.provider_failed',
            payload={'campaign_id': campaign.campaign_id, 'turn_id': turn.turn_id, 'error': str(exc)},
            severity='warning',
        )
        return None, None

    parsed = _extract_json_object(response.text)
    if not parsed:
        return None, response.model
    return _normalize_patch(parsed), response.model


def extract_canon_patch(
    turn: DmTurn,
    campaign: Campaign,
    dm_output: str,
    speaking_player_name: str | None,
    triggered_segments: list[dict] | None = None,
) -> tuple[dict, str]:
    triggered_segments = triggered_segments or []
    provider_patch, extractor_model = _extract_with_provider(
        turn=turn,
        campaign=campaign,
        dm_output=dm_output,
        speaking_player_name=speaking_player_name,
        triggered_segments=triggered_segments,
    )
    if provider_patch is not None:
        provider_inventory_patch = _empty_patch()
        _append_verified_provider_inventory_changes(provider_patch, dm_output or '', provider_inventory_patch)

        # Inventory consequences are fairness state. A provider can point out a
        # missed item, but the mutation layer accepts it only with textual proof.
        provider_patch['inventory_changes'] = []
        for change in provider_inventory_patch['inventory_changes']:
            provider_patch['inventory_changes'].append(change)
        _extract_inventory_changes_from_text(dm_output or '', _INVENTORY_GAIN_PATTERNS, 'acquire', provider_patch)
        _extract_inventory_changes_from_text(dm_output or '', _INVENTORY_LOSS_PATTERNS, 'lose', provider_patch)
        _extract_explicit_inventory_state_changes_from_text(dm_output or '', provider_patch)
        _append_drop_all_inventory_changes_from_text(turn, dm_output or '', provider_patch)
        _append_inventory_change_from_intent_outcome(turn, dm_output or '', provider_patch)
        telemetry_metric('memory.extract.provider_success_total', 1)
        return provider_patch, extractor_model or 'provider'

    telemetry_metric('memory.extract.heuristic_fallback_total', 1)
    return (
        _heuristic_patch(
            turn=turn,
            campaign=campaign,
            dm_output=dm_output,
            speaking_player_name=speaking_player_name,
            triggered_segments=triggered_segments,
        ),
        'heuristic-v1',
    )


def extract_deterministic_state_patch(turn: DmTurn, dm_output: str) -> dict:
    patch = _normalize_patch({})
    _extract_inventory_changes_from_text(dm_output or '', _INVENTORY_GAIN_PATTERNS, 'acquire', patch)
    _extract_inventory_changes_from_text(dm_output or '', _INVENTORY_LOSS_PATTERNS, 'lose', patch)
    _extract_explicit_inventory_state_changes_from_text(dm_output or '', patch)
    _append_drop_all_inventory_changes_from_text(turn, dm_output or '', patch)
    _append_inventory_change_from_intent_outcome(turn, dm_output or '', patch)
    return patch


def apply_immediate_state_changes(turn: DmTurn, campaign: Campaign, dm_output: str) -> dict:
    metadata = safe_json_loads(turn.metadata_json, {})
    metadata = metadata if isinstance(metadata, dict) else {}
    existing_summary = metadata.get(IMMEDIATE_STATE_METADATA_KEY)
    if isinstance(existing_summary, dict):
        return existing_summary

    patch = extract_deterministic_state_patch(turn, dm_output)
    validated_patch, rejections = validate_canon_patch(turn=turn, campaign=campaign, patch=patch)
    inventory_changes = _apply_inventory_changes(turn, validated_patch['inventory_changes'])
    character_state_changes = _apply_character_state_changes(turn, dm_output or '')
    summary = {
        'inventory_changes_applied': inventory_changes,
        'character_state_changes_applied': character_state_changes,
        'rejections': rejections,
    }
    if not inventory_changes and not character_state_changes:
        return summary

    metadata[IMMEDIATE_STATE_METADATA_KEY] = summary
    metadata['immediate_state_changes_applied_at'] = utc_now().isoformat()
    turn.metadata_json = safe_json_dumps(metadata, {})
    return summary


def _merge_aliases(existing_raw: str | None, incoming: list[str] | None) -> str:
    aliases = safe_json_loads(existing_raw, [])
    aliases = aliases if isinstance(aliases, list) else []
    for alias in incoming or []:
        alias_text = str(alias or '').strip()
        if alias_text and alias_text not in aliases:
            aliases.append(alias_text)
    return safe_json_dumps(aliases, [])


def _merge_metadata(existing_raw: str | None, incoming: dict | None) -> str:
    payload = safe_json_loads(existing_raw, {})
    payload = payload if isinstance(payload, dict) else {}
    if isinstance(incoming, dict):
        payload.update(incoming)
    return safe_json_dumps(payload, {})


def _get_or_create_entity(
    campaign_id: int,
    session_id: int | None,
    turn_id: int,
    payload: dict,
    lookup_index: _EntityLookupIndex | None = None,
) -> StoryEntity | None:
    name = str(payload.get('name') or '').strip()
    if not name:
        return None

    entity_type = str(payload.get('entity_type') or 'entity').strip().lower()
    entity = _find_existing_entity(
        campaign_id=campaign_id,
        incoming_type=entity_type,
        incoming_name=name,
        lookup_index=lookup_index,
    )

    if not entity:
        entity = StoryEntity(
            campaign_id=campaign_id,
            session_id=session_id,
            entity_type=entity_type,
            name=name,
            canonical_name=payload.get('canonical_name') or name,
            summary=payload.get('summary'),
            status=payload.get('status') or 'active',
            aliases_json=safe_json_dumps(payload.get('aliases', []), []),
            metadata_json=safe_json_dumps(payload.get('metadata', {}), {}),
            first_seen_turn_id=turn_id,
            last_seen_turn_id=turn_id,
        )
        db.session.add(entity)
        db.session.flush()
        if lookup_index is not None:
            lookup_index.refresh(entity)
        return entity

    entity.session_id = entity.session_id or session_id
    entity.last_seen_turn_id = turn_id
    if entity.entity_type == 'entity' and entity_type != 'entity':
        entity.entity_type = entity_type
    if payload.get('canonical_name') and not entity.canonical_name:
        entity.canonical_name = payload.get('canonical_name')
    if payload.get('summary'):
        entity.summary = payload.get('summary')
    if payload.get('status'):
        entity.status = payload.get('status')
    aliases = list(payload.get('aliases', []) or [])
    if _normalized_name(name) not in {_normalized_name(entity.name), _normalized_name(entity.canonical_name)}:
        aliases.append(name)
    entity.aliases_json = _merge_aliases(entity.aliases_json, aliases)
    entity.metadata_json = _merge_metadata(entity.metadata_json, payload.get('metadata'))
    if lookup_index is not None:
        lookup_index.refresh(entity)
    return entity


def _resolve_entity_ref(
    campaign_id: int,
    session_id: int | None,
    turn_id: int,
    payload: Any,
    lookup_index: _EntityLookupIndex | None = None,
) -> StoryEntity | None:
    if isinstance(payload, dict):
        return _get_or_create_entity(
            campaign_id=campaign_id,
            session_id=session_id,
            turn_id=turn_id,
            payload=payload,
            lookup_index=lookup_index,
        )
    if isinstance(payload, str):
        return _get_or_create_entity(
            campaign_id=campaign_id,
            session_id=session_id,
            turn_id=turn_id,
            payload={'entity_type': 'entity', 'name': payload},
            lookup_index=lookup_index,
        )
    return None


def _mark_superseded_fact(
    campaign_id: int,
    *,
    fact_id: int | None = None,
    predicate: str | None = None,
    subject_entity_id: int | None = None,
) -> StoryFact | None:
    target_fact = None

    if fact_id is not None:
        try:
            fact_id = int(fact_id)
        except (TypeError, ValueError):
            fact_id = None
        if fact_id is not None:
            candidate = db.session.get(StoryFact, fact_id)
            if candidate and candidate.campaign_id == campaign_id and candidate.fact_status == 'accepted':
                target_fact = candidate

    if target_fact is None and predicate:
        query = StoryFact.query.filter(
            StoryFact.campaign_id == campaign_id,
            StoryFact.predicate == predicate,
            StoryFact.fact_status == 'accepted',
        )
        if predicate in _SUBJECT_SINGLETON_FACTS:
            if subject_entity_id is None:
                return None
            query = query.filter(StoryFact.subject_entity_id == subject_entity_id)
        elif predicate not in _GLOBAL_SINGLETON_FACTS:
            return None
        target_fact = query.order_by(StoryFact.fact_id.desc()).first()

    if target_fact:
        target_fact.fact_status = 'superseded'
    return target_fact


def _fact_value_signature(fact_payload: dict) -> tuple[str | None, str | None]:
    value_text = fact_payload.get('value_text')
    object_name = fact_payload.get('object')
    if isinstance(object_name, dict):
        object_name = object_name.get('name')
    if value_text is None and fact_payload.get('value_json') is not None:
        value_text = safe_json_dumps(fact_payload.get('value_json'), {})
    return (
        _normalized_name(object_name) or None,
        _normalized_name(value_text) or None,
    )


def _existing_fact_signature(fact: StoryFact) -> tuple[str | None, str | None]:
    value_text = fact.value_text
    if value_text is None and fact.value_json is not None:
        value_text = fact.value_json
    return (
        _normalized_name(fact.object_entity.name if fact.object_entity else None) or None,
        _normalized_name(value_text) or None,
    )


def _fact_subject_key(payload: dict) -> str | None:
    subject = payload.get('subject')
    if isinstance(subject, dict):
        return _normalized_name(subject.get('name'))
    if isinstance(subject, str):
        return _normalized_name(subject)
    return None


def _existing_fact_subject_key(fact: StoryFact) -> str | None:
    if fact.subject_entity is None:
        return None
    return _normalized_name(fact.subject_entity.name)


def validate_canon_patch(turn: DmTurn, campaign: Campaign, patch: dict) -> tuple[dict, list[dict]]:
    patch = _normalize_patch(patch)
    rejections: list[dict] = []

    deduped_entities: list[dict] = []
    seen_entities: set[tuple[str, str]] = set()
    for entity_payload in patch['entities']:
        name = _clean_inventory_item_name(entity_payload.get('name'))
        if not name:
            continue
        entity_type = str(entity_payload.get('entity_type') or 'entity').strip().lower()
        dedupe_key = (entity_type, _normalized_name(name))
        if dedupe_key in seen_entities:
            continue
        seen_entities.add(dedupe_key)
        normalized_payload = dict(entity_payload)
        normalized_payload['name'] = name
        normalized_payload['entity_type'] = entity_type
        deduped_entities.append(normalized_payload)
    patch['entities'] = deduped_entities

    fact_predicates = {
        predicate
        for predicate in (
            str(fact_payload.get('predicate') or '').strip()
            for fact_payload in patch['facts']
        )
        if predicate
    }
    accepted_facts_by_predicate: dict[str, list[StoryFact]] = defaultdict(list)
    if fact_predicates:
        accepted_facts = (
            StoryFact.query.options(
                joinedload(StoryFact.subject_entity),
                joinedload(StoryFact.object_entity),
            )
            .filter(
                StoryFact.campaign_id == campaign.campaign_id,
                StoryFact.predicate.in_(fact_predicates),
                StoryFact.fact_status == 'accepted',
            )
            .order_by(StoryFact.fact_id.desc())
            .all()
        )
        for existing_fact in accepted_facts:
            accepted_facts_by_predicate[existing_fact.predicate].append(existing_fact)

    validated_facts: list[dict] = []
    for fact_payload in patch['facts']:
        predicate = str(fact_payload.get('predicate') or '').strip()
        if not predicate:
            continue

        fact_copy = dict(fact_payload)
        fact_copy['predicate'] = predicate

        change_type = str(fact_copy.get('change_type') or '').strip().lower()
        replace_existing = bool(fact_copy.get('replace_existing'))
        incoming_signature = _fact_value_signature(fact_copy)
        subject_key = _fact_subject_key(fact_copy)

        accepted_facts = accepted_facts_by_predicate.get(predicate, [])

        conflicting_fact: StoryFact | None = None
        duplicate = False
        for existing in accepted_facts:
            existing_signature = _existing_fact_signature(existing)
            if incoming_signature == existing_signature:
                if predicate in _GLOBAL_SINGLETON_FACTS or (
                    predicate in _SUBJECT_SINGLETON_FACTS and subject_key == _existing_fact_subject_key(existing)
                ):
                    duplicate = True
                    break
                continue

            if predicate in _GLOBAL_SINGLETON_FACTS:
                conflicting_fact = existing
                break

            if predicate in _SUBJECT_SINGLETON_FACTS and subject_key and subject_key == _existing_fact_subject_key(existing):
                conflicting_fact = existing
                break

        if duplicate:
            continue

        if conflicting_fact and not replace_existing and change_type not in _ALLOWED_FACT_CHANGE_TYPES:
            rejections.append(
                {
                    'type': 'fact_conflict',
                    'predicate': predicate,
                    'subject': subject_key,
                    'existing_fact_id': conflicting_fact.fact_id,
                    'reason': 'conflicting accepted fact without explicit change_type or replace_existing',
                }
            )
            continue

        if conflicting_fact and (replace_existing or change_type in _ALLOWED_FACT_CHANGE_TYPES):
            fact_copy['replace_existing'] = True
            fact_copy['supersedes_fact_id'] = conflicting_fact.fact_id

        validated_facts.append(fact_copy)

    patch['facts'] = validated_facts

    validated_inventory_changes: list[dict] = []
    for change in patch['inventory_changes']:
        action = str(change.get('action') or '').strip().lower()
        if action not in {'acquire', 'lose'}:
            continue
        item_name = _clean_inventory_item_name(change.get('item_name'))
        if not item_name:
            continue
        if not _looks_like_inventory_item(item_name):
            rejections.append(
                {
                    'type': 'inventory_change_rejected',
                    'action': action,
                    'item_name': item_name,
                    'reason': 'inventory changes must reference a tangible item',
                }
            )
            continue
        validated_inventory_changes.append(
            {
                'action': action,
                'item_name': item_name,
                'quantity': _positive_int(change.get('quantity', 1)),
            }
        )
    patch['inventory_changes'] = validated_inventory_changes

    return patch, rejections


def _inventory_change_signature(change: dict) -> tuple[str, str] | None:
    action = str(change.get('action') or '').strip().lower()
    item_name = _clean_inventory_item_name(change.get('item_name'))
    if action not in {'acquire', 'lose'} or not item_name:
        return None
    return action, _normalized_name(item_name)


def _immediate_state_summary(turn: DmTurn) -> dict:
    metadata = safe_json_loads(turn.metadata_json, {})
    metadata = metadata if isinstance(metadata, dict) else {}
    summary = metadata.get(IMMEDIATE_STATE_METADATA_KEY)
    return summary if isinstance(summary, dict) else {}


def _inventory_changes_after_immediate_state(changes: list[dict], immediate_summary: dict) -> tuple[list[dict], list[dict]]:
    already_applied = Counter()
    for change in immediate_summary.get('inventory_changes_applied') or []:
        if not isinstance(change, dict):
            continue
        signature = _inventory_change_signature(change)
        if signature:
            already_applied[signature] += _positive_int(change.get('quantity', 1))

    if not already_applied:
        return changes, []

    remaining_changes: list[dict] = []
    credited_changes: list[dict] = []
    for change in changes:
        signature = _inventory_change_signature(change)
        quantity = _positive_int(change.get('quantity', 1))
        if not signature or already_applied[signature] <= 0:
            remaining_changes.append(change)
            continue

        credited_quantity = min(quantity, already_applied[signature])
        credited_changes.append(
            {
                'action': change.get('action'),
                'item_name': change.get('item_name'),
                'quantity': credited_quantity,
                'already_applied': True,
            }
        )
        already_applied[signature] -= credited_quantity
        remaining_quantity = quantity - credited_quantity
        if remaining_quantity > 0:
            remaining_change = dict(change)
            remaining_change['quantity'] = remaining_quantity
            remaining_changes.append(remaining_change)

    return remaining_changes, credited_changes


def apply_canon_patch(
    turn: DmTurn,
    campaign: Campaign,
    patch: dict,
    extractor_model: str,
    rejections: list[dict] | None = None,
) -> dict:
    patch = _normalize_patch(patch)
    rejections = rejections or []
    update_record = TurnCanonUpdate(
        turn_id=turn.turn_id,
        campaign_id=campaign.campaign_id,
        raw_patch_json=safe_json_dumps(patch, _EMPTY_PATCH),
        status='applied_with_rejections' if rejections else 'applied',
        extractor_model=extractor_model,
        error_text=(safe_json_dumps(rejections, []) if rejections else None),
    )
    db.session.add(update_record)
    db.session.flush()

    applied_summary = {
        'entities_created_or_updated': [],
        'facts_created': 0,
        'threads_created_or_updated': [],
        'inventory_changes_applied': [],
        'character_state_changes_applied': [],
        'rejections': rejections,
    }
    lookup_index = _EntityLookupIndex(campaign.campaign_id)

    for entity_payload in patch['entities']:
        entity = _get_or_create_entity(
            campaign_id=campaign.campaign_id,
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            payload=entity_payload,
            lookup_index=lookup_index,
        )
        if not entity:
            continue
        applied_summary['entities_created_or_updated'].append(
            {
                'entity_id': entity.entity_id,
                'entity_type': entity.entity_type,
                'name': entity.name,
            }
        )

    for fact_payload in patch['facts']:
        predicate = str(fact_payload.get('predicate') or '').strip()
        if not predicate:
            continue

        subject = _resolve_entity_ref(
            campaign.campaign_id,
            turn.session_id,
            turn.turn_id,
            fact_payload.get('subject'),
            lookup_index=lookup_index,
        )
        object_entity = _resolve_entity_ref(
            campaign.campaign_id,
            turn.session_id,
            turn.turn_id,
            fact_payload.get('object'),
            lookup_index=lookup_index,
        )

        superseded_fact = None
        if fact_payload.get('replace_existing'):
            superseded_fact = _mark_superseded_fact(
                campaign.campaign_id,
                fact_id=fact_payload.get('supersedes_fact_id'),
                predicate=predicate,
                subject_entity_id=subject.entity_id if subject else None,
            )

        fact = StoryFact(
            campaign_id=campaign.campaign_id,
            subject_entity_id=subject.entity_id if subject else None,
            predicate=predicate,
            object_entity_id=object_entity.entity_id if object_entity else None,
            value_text=fact_payload.get('value_text'),
            value_json=safe_json_dumps(fact_payload.get('value_json'), {}) if fact_payload.get('value_json') is not None else None,
            fact_status=fact_payload.get('fact_status') or 'accepted',
            confidence=_optional_float(fact_payload.get('confidence')),
            source_turn_id=turn.turn_id,
            supersedes_fact_id=superseded_fact.fact_id if superseded_fact else None,
        )
        db.session.add(fact)
        applied_summary['facts_created'] += 1

    thread_title_keys = {
        str(thread_payload.get('title') or '').strip().lower()
        for thread_payload in patch['threads']
        if str(thread_payload.get('title') or '').strip()
    }
    existing_threads_by_title: dict[str, StoryThread] = {}
    if thread_title_keys:
        existing_threads = (
            StoryThread.query.filter(
                StoryThread.campaign_id == campaign.campaign_id,
                func.lower(StoryThread.title).in_(thread_title_keys),
            )
            .order_by(StoryThread.thread_id.asc())
            .all()
        )
        for existing_thread in existing_threads:
            existing_threads_by_title.setdefault(existing_thread.title.lower(), existing_thread)

    for thread_payload in patch['threads']:
        title = str(thread_payload.get('title') or '').strip()
        if not title:
            continue

        title_key = title.lower()
        thread = existing_threads_by_title.get(title_key)
        if not thread:
            thread = StoryThread(
                campaign_id=campaign.campaign_id,
                title=title,
                summary=thread_payload.get('summary'),
                status=thread_payload.get('status') or 'open',
                priority=_positive_int(thread_payload.get('priority', 1)),
                origin_turn_id=turn.turn_id,
                last_touched_turn_id=turn.turn_id,
                resolved_turn_id=turn.turn_id if thread_payload.get('status') == 'resolved' else None,
                source=thread_payload.get('source') or 'emergent',
                metadata_json=safe_json_dumps(thread_payload.get('metadata', {}), {}),
            )
            db.session.add(thread)
            db.session.flush()
            existing_threads_by_title[title_key] = thread
        else:
            if thread_payload.get('summary'):
                thread.summary = thread_payload.get('summary')
            if thread_payload.get('status'):
                thread.status = thread_payload.get('status')
            if thread_payload.get('priority') is not None:
                thread.priority = _positive_int(thread_payload.get('priority'))
            thread.last_touched_turn_id = turn.turn_id
            if thread_payload.get('status') == 'resolved':
                thread.resolved_turn_id = turn.turn_id
            if thread_payload.get('source'):
                thread.source = thread_payload.get('source')
            thread.metadata_json = _merge_metadata(thread.metadata_json, thread_payload.get('metadata'))

        applied_summary['threads_created_or_updated'].append(
            {
                'thread_id': thread.thread_id,
                'title': thread.title,
                'status': thread.status,
                'source': thread.source,
            }
        )

    immediate_summary = _immediate_state_summary(turn)
    remaining_inventory_changes, credited_inventory_changes = _inventory_changes_after_immediate_state(
        patch['inventory_changes'],
        immediate_summary,
    )
    applied_summary['inventory_changes_applied'] = credited_inventory_changes + _apply_inventory_changes(
        turn,
        remaining_inventory_changes,
    )
    immediate_character_changes = [
        {**change, 'already_applied': True}
        for change in (immediate_summary.get('character_state_changes_applied') or [])
        if isinstance(change, dict)
    ]
    applied_summary['character_state_changes_applied'] = immediate_character_changes or _apply_character_state_changes(
        turn,
        turn.dm_output or '',
    )

    update_record.applied_patch_json = safe_json_dumps(applied_summary, {})
    return applied_summary


def build_emergent_context(
    campaign_id: int,
    session_id: int | None = None,
    entity_limit: int = 12,
    fact_limit: int = 20,
    thread_limit: int = 8,
    query_text: str | None = None,
    current_location: str | None = None,
    current_quest: str | None = None,
    recent_turns: list[dict] | None = None,
) -> dict:
    return _canon_retrieval.build_emergent_context(
        campaign_id=campaign_id,
        session_id=session_id,
        entity_limit=entity_limit,
        fact_limit=fact_limit,
        thread_limit=thread_limit,
        entity_candidate_limit=EMERGENT_ENTITY_CANDIDATE_LIMIT,
        fact_candidate_limit=EMERGENT_FACT_CANDIDATE_LIMIT,
        thread_candidate_limit=EMERGENT_THREAD_CANDIDATE_LIMIT,
        query_text=query_text,
        current_location=current_location,
        current_quest=current_quest,
        recent_turns=recent_turns,
    )
