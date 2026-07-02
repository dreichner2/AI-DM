"""Relevant canon retrieval for DM prompt context."""

from __future__ import annotations

import hashlib
import math

from sqlalchemy.orm import joinedload

from aidm_server.canon_text import normalized_name
from aidm_server.models import SessionState, StoryEntity, StoryFact, StoryThread, safe_json_loads


EMERGENT_ENTITY_CANDIDATE_LIMIT = 240
EMERGENT_FACT_CANDIDATE_LIMIT = 480
EMERGENT_THREAD_CANDIDATE_LIMIT = 160
HYBRID_EMBEDDING_DIMENSIONS = 96

_GLOBAL_SINGLETON_FACTS = {'current_location', 'current_quest'}
_SEMANTIC_ALIAS_GROUPS = (
    ('moon', 'lunar', 'silver', 'night'),
    ('sun', 'solar', 'golden', 'dawn'),
    ('key', 'sigil', 'token', 'seal', 'rune'),
    ('gate', 'door', 'portal', 'threshold', 'entrance'),
    ('tower', 'belfry', 'spire', 'watch'),
    ('ghost', 'spirit', 'shade', 'haunting'),
    ('curse', 'hex', 'blight', 'doom'),
    ('archive', 'library', 'record', 'ledger'),
    ('captain', 'commander', 'marshal', 'warden'),
    ('thief', 'rogue', 'smuggler', 'cutpurse'),
    ('forest', 'wood', 'grove', 'wilds'),
    ('harbor', 'dock', 'port', 'waterfront'),
    ('desert', 'dune', 'sand', 'waste'),
    ('dragon', 'wyrm', 'drake', 'serpent'),
)
_SEMANTIC_ALIASES = {
    term: {alias for alias in group if alias != term}
    for group in _SEMANTIC_ALIAS_GROUPS
    for term in group
}
_RETRIEVAL_STOPWORDS = {
    'a',
    'an',
    'and',
    'at',
    'for',
    'from',
    'into',
    'is',
    'of',
    'on',
    'or',
    'the',
    'to',
    'with',
}


def dormant_threads(
    campaign_id: int,
    current_turn_id: int | None,
    min_dormancy: int = 30,
    limit: int = 3,
) -> list[dict]:
    """Return old open story threads that may be satisfying to echo back into play."""

    if not current_turn_id or current_turn_id <= 0 or limit <= 0:
        return []
    threshold_turn_id = max(0, current_turn_id - max(1, min_dormancy))
    candidates = (
        StoryThread.query.filter(
            StoryThread.campaign_id == campaign_id,
            StoryThread.status == 'open',
            StoryThread.last_touched_turn_id.isnot(None),
            StoryThread.last_touched_turn_id <= threshold_turn_id,
        )
        .order_by(StoryThread.priority.desc(), StoryThread.last_touched_turn_id.asc(), StoryThread.thread_id.desc())
        .limit(max(limit * 6, limit))
        .all()
    )
    ranked_threads = sorted(
        candidates,
        key=lambda thread: (
            thread.priority or 0,
            current_turn_id - int(thread.last_touched_turn_id or current_turn_id),
            thread.thread_id,
        ),
        reverse=True,
    )
    return [
        {
            'thread_id': thread.thread_id,
            'title': thread.title,
            'summary': thread.summary,
            'status': thread.status,
            'priority': thread.priority,
            'source': thread.source,
            'last_touched_turn_id': thread.last_touched_turn_id,
            'dormant_turns': current_turn_id - int(thread.last_touched_turn_id or current_turn_id),
        }
        for thread in ranked_threads[:limit]
    ]


def _candidate_labels(entity: StoryEntity) -> set[str]:
    labels = {
        normalized_name(entity.name),
        normalized_name(entity.canonical_name),
    }
    aliases = safe_json_loads(entity.aliases_json, [])
    aliases = aliases if isinstance(aliases, list) else []
    labels.update(normalized_name(alias) for alias in aliases)
    return {label for label in labels if label}


def _retrieval_tokens(*values: str | None) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        normalized = normalized_name(value)
        if not normalized:
            continue
        for token in normalized.split():
            if len(token) < 3 or token in _RETRIEVAL_STOPWORDS:
                continue
            tokens.add(token)
    return tokens


def _semantic_terms(*values: str | None) -> set[str]:
    terms = set(_retrieval_tokens(*values))
    expanded = set(terms)
    for term in terms:
        expanded.update(_SEMANTIC_ALIASES.get(term, set()))
        if len(term) >= 5:
            for index in range(0, max(0, len(term) - 3)):
                expanded.add(f'ngram:{term[index:index + 4]}')
    return expanded


def _hashed_embedding(*values: str | None) -> dict[int, float]:
    terms = _semantic_terms(*values)
    vector: dict[int, float] = {}
    for term in terms:
        weight = 0.35 if term.startswith('ngram:') else 1.0
        digest = hashlib.sha1(term.encode('utf-8')).digest()
        index = int.from_bytes(digest[:4], 'big') % HYBRID_EMBEDDING_DIMENSIONS
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] = vector.get(index, 0.0) + (weight * sign)
    magnitude = math.sqrt(sum(value * value for value in vector.values()))
    if magnitude <= 0:
        return {}
    return {index: value / magnitude for index, value in vector.items()}


def _cosine_similarity(left: dict[int, float], right: dict[int, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(index, 0.0) for index, value in left.items())


def _embedding_score(candidate_text: str, signal_vector: dict[int, float]) -> float:
    similarity = _cosine_similarity(_hashed_embedding(candidate_text), signal_vector)
    return max(0.0, similarity)


def _recent_signal_text(recent_turns: list[dict] | None) -> str:
    if not recent_turns:
        return ''
    fragments: list[str] = []
    for turn in recent_turns[-3:]:
        if not isinstance(turn, dict):
            continue
        player_input = str(turn.get('player_input') or '').strip()
        dm_output = str(turn.get('dm_output') or '').strip()
        if player_input:
            fragments.append(player_input)
        if dm_output:
            fragments.append(dm_output[:160])
    return '\n'.join(fragments)


def _entity_retrieval_score(
    entity: StoryEntity,
    *,
    signal_text: str,
    signal_tokens: set[str],
    signal_vector: dict[int, float],
    session_id: int | None,
) -> float:
    score = 0.0
    labels = _candidate_labels(entity)
    if signal_text:
        for label in labels:
            if label and label in signal_text:
                score += 10.0
    for label in labels:
        overlap = len(set(label.split()) & signal_tokens)
        if overlap:
            score += overlap * 3.0
    summary_tokens = _retrieval_tokens(entity.summary)
    summary_overlap = len(summary_tokens & signal_tokens)
    if summary_overlap:
        score += summary_overlap * 1.5
    if session_id is not None and entity.session_id == session_id:
        score += 1.0
    if str(entity.status or '').lower() in {'active', 'open'}:
        score += 0.5
    aliases = safe_json_loads(entity.aliases_json, [])
    alias_text = ' '.join(str(alias or '') for alias in aliases) if isinstance(aliases, list) else ''
    score += _embedding_score(
        ' '.join(
            [
                str(entity.entity_type or ''),
                str(entity.name or ''),
                str(entity.canonical_name or ''),
                alias_text,
                str(entity.summary or ''),
                str(entity.status or ''),
            ]
        ),
        signal_vector,
    ) * 4.0
    return score


def _fact_retrieval_score(
    fact: StoryFact,
    *,
    signal_text: str,
    signal_tokens: set[str],
    signal_vector: dict[int, float],
    relevant_entity_ids: set[int],
) -> float:
    score = 0.0
    if fact.subject_entity_id in relevant_entity_ids:
        score += 5.0
    if fact.object_entity_id in relevant_entity_ids:
        score += 4.0
    if fact.predicate in _GLOBAL_SINGLETON_FACTS:
        score += 2.5

    predicate_tokens = _retrieval_tokens(fact.predicate)
    value_tokens = _retrieval_tokens(fact.value_text)
    score += len(predicate_tokens & signal_tokens) * 2.0
    score += len(value_tokens & signal_tokens) * 1.0

    subject_name = fact.subject_entity.name if fact.subject_entity else None
    object_name = fact.object_entity.name if fact.object_entity else None
    for name in (subject_name, object_name):
        normalized = normalized_name(name)
        if normalized and normalized in signal_text:
            score += 3.0
    score += _embedding_score(
        ' '.join(
            [
                str(subject_name or ''),
                str(fact.predicate or ''),
                str(object_name or ''),
                str(fact.value_text or ''),
            ]
        ),
        signal_vector,
    ) * 3.0
    return score


def _thread_retrieval_score(
    thread: StoryThread,
    *,
    signal_text: str,
    signal_tokens: set[str],
    signal_vector: dict[int, float],
) -> float:
    score = float(thread.priority or 0)
    if str(thread.status or '').lower() in {'open', 'active'}:
        score += 2.0
    title_tokens = _retrieval_tokens(thread.title)
    summary_tokens = _retrieval_tokens(thread.summary)
    score += len(title_tokens & signal_tokens) * 2.5
    score += len(summary_tokens & signal_tokens) * 1.0
    normalized_title = normalized_name(thread.title)
    if normalized_title and normalized_title in signal_text:
        score += 4.0
    score += _embedding_score(
        ' '.join(
            [
                str(thread.title or ''),
                str(thread.summary or ''),
                str(thread.status or ''),
                str(thread.source or ''),
            ]
        ),
        signal_vector,
    ) * 3.5
    return score


def build_emergent_context(
    campaign_id: int,
    session_id: int | None = None,
    entity_limit: int = 12,
    fact_limit: int = 20,
    thread_limit: int = 8,
    entity_candidate_limit: int | None = None,
    fact_candidate_limit: int | None = None,
    thread_candidate_limit: int | None = None,
    query_text: str | None = None,
    current_location: str | None = None,
    current_quest: str | None = None,
    recent_turns: list[dict] | None = None,
) -> dict:
    recent_signal = _recent_signal_text(recent_turns)
    signal_text = normalized_name(
        ' '.join(
            part
            for part in [
                query_text or '',
                current_location or '',
                current_quest or '',
                recent_signal,
            ]
            if part
        )
    )
    signal_tokens = _retrieval_tokens(query_text, current_location, current_quest, recent_signal)
    signal_vector = _hashed_embedding(query_text, current_location, current_quest, recent_signal)

    entity_candidate_limit = min(
        max(entity_limit * 8, entity_limit),
        entity_candidate_limit or EMERGENT_ENTITY_CANDIDATE_LIMIT,
    )
    fact_candidate_limit = min(
        max(fact_limit * 8, fact_limit),
        fact_candidate_limit or EMERGENT_FACT_CANDIDATE_LIMIT,
    )
    thread_candidate_limit = min(
        max(thread_limit * 8, thread_limit),
        thread_candidate_limit or EMERGENT_THREAD_CANDIDATE_LIMIT,
    )

    all_entities = (
        StoryEntity.query.filter_by(campaign_id=campaign_id)
        .order_by(StoryEntity.updated_at.desc(), StoryEntity.entity_id.desc())
        .limit(entity_candidate_limit)
        .all()
    )
    ranked_entities = sorted(
        all_entities,
        key=lambda entity: (
            _entity_retrieval_score(
                entity,
                signal_text=signal_text,
                signal_tokens=signal_tokens,
                signal_vector=signal_vector,
                session_id=session_id,
            ),
            entity.updated_at or entity.created_at,
            entity.entity_id,
        ),
        reverse=True,
    )
    entities = ranked_entities[:entity_limit]
    relevant_entity_ids = {entity.entity_id for entity in entities}

    all_facts = (
        StoryFact.query.options(
            joinedload(StoryFact.subject_entity),
            joinedload(StoryFact.object_entity),
        )
        .filter(
            StoryFact.campaign_id == campaign_id,
            StoryFact.fact_status == 'accepted',
        )
        .order_by(StoryFact.fact_id.desc())
        .limit(fact_candidate_limit)
        .all()
    )
    ranked_facts = sorted(
        all_facts,
        key=lambda fact: (
            _fact_retrieval_score(
                fact,
                signal_text=signal_text,
                signal_tokens=signal_tokens,
                signal_vector=signal_vector,
                relevant_entity_ids=relevant_entity_ids,
            ),
            fact.fact_id,
        ),
        reverse=True,
    )
    if ranked_facts and _fact_retrieval_score(
        ranked_facts[0],
        signal_text=signal_text,
        signal_tokens=signal_tokens,
        signal_vector=signal_vector,
        relevant_entity_ids=relevant_entity_ids,
    ) <= 0.0:
        ranked_facts = sorted(all_facts, key=lambda fact: fact.fact_id, reverse=True)
    facts = ranked_facts[:fact_limit]

    all_threads = (
        StoryThread.query.filter_by(campaign_id=campaign_id)
        .order_by(StoryThread.updated_at.desc(), StoryThread.thread_id.desc())
        .limit(thread_candidate_limit)
        .all()
    )
    ranked_threads = sorted(
        all_threads,
        key=lambda thread: (
            _thread_retrieval_score(
                thread,
                signal_text=signal_text,
                signal_tokens=signal_tokens,
                signal_vector=signal_vector,
            ),
            thread.updated_at or thread.created_at,
            thread.thread_id,
        ),
        reverse=True,
    )
    threads = ranked_threads[:thread_limit]

    payload = {
        'entities': [
            {
                'entity_id': entity.entity_id,
                'entity_type': entity.entity_type,
                'name': entity.name,
                'canonical_name': entity.canonical_name,
                'aliases': safe_json_loads(entity.aliases_json, []),
                'summary': entity.summary,
                'status': entity.status,
            }
            for entity in entities
        ],
        'facts': [
            {
                'fact_id': fact.fact_id,
                'subject': fact.subject_entity.name if fact.subject_entity else None,
                'predicate': fact.predicate,
                'object': fact.object_entity.name if fact.object_entity else None,
                'value_text': fact.value_text,
                'confidence': fact.confidence,
            }
            for fact in facts
        ],
        'threads': [
            {
                'thread_id': thread.thread_id,
                'title': thread.title,
                'summary': thread.summary,
                'status': thread.status,
                'priority': thread.priority,
                'source': thread.source,
            }
            for thread in threads
        ],
        'retrieval': {
            'mode': 'hybrid_lexical_local_embedding',
            'embedding': {
                'provider': 'local_hash_v1',
                'dimensions': HYBRID_EMBEDDING_DIMENSIONS,
                'query_active': bool(signal_vector),
                'query_terms': sorted(signal_tokens)[:32],
                'semantic_terms': sorted(term for term in _semantic_terms(query_text, current_location, current_quest, recent_signal) if not term.startswith('ngram:'))[:32],
            },
            'candidate_limits': {
                'entities': entity_candidate_limit,
                'facts': fact_candidate_limit,
                'threads': thread_candidate_limit,
            },
        },
    }

    if session_id:
        state = SessionState.query.filter_by(session_id=session_id).first()
        payload['projection'] = {
            'current_location': state.current_location if state else None,
            'current_quest': state.current_quest if state else None,
            'rolling_summary': state.rolling_summary if state else '',
        }

    return payload
