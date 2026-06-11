from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.creatures.core_bestiary import core_bestiary, core_creature
from aidm_server.creatures.schemas import normalize_creature_definition
from aidm_server.database import db
from aidm_server.models import BestiaryEntry, Campaign, CombatDebugEvent, safe_json_dumps, safe_json_loads
from aidm_server.time_utils import utc_now


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or '').strip()]
    return []


def bestiary_entry_payload(row: BestiaryEntry) -> dict[str, Any]:
    creature = safe_json_loads(row.creature_json, {})
    creature = normalize_creature_definition(creature, source=row.source)
    return {
        'bestiary_entry_id': row.bestiary_entry_id,
        'workspace_id': row.workspace_id,
        'campaign_id': row.campaign_id,
        'session_id': row.session_id,
        'scope': row.scope,
        'creature_id': row.creature_id,
        'version': row.version,
        'name': row.name,
        'source': row.source,
        'persistence': row.persistence,
        'region_id': row.region_id,
        'location_ids': safe_json_loads(row.location_ids_json, []),
        'faction_ids': safe_json_loads(row.faction_ids_json, []),
        'tags': safe_json_loads(row.tags_json, []),
        'creature': creature,
        'balance': safe_json_loads(row.balance_json, creature.get('balance') if isinstance(creature, dict) else {}),
        'created_because': row.created_because,
        'base_creature_id': row.base_creature_id,
        'variant_reason': row.variant_reason,
        'created_at_turn': row.created_at_turn,
        'created_by_model': row.created_by_model,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }


def core_entries() -> list[dict[str, Any]]:
    entries = []
    for creature in core_bestiary():
        entries.append(
            {
                'bestiary_entry_id': None,
                'workspace_id': 'core',
                'campaign_id': None,
                'session_id': None,
                'scope': 'core',
                'creature_id': creature['id'],
                'version': creature.get('version', 1),
                'name': creature['name'],
                'source': 'core_bestiary',
                'persistence': 'global',
                'region_id': None,
                'location_ids': [],
                'faction_ids': [],
                'tags': creature.get('visualTags') or [],
                'creature': creature,
                'balance': creature.get('balance') or {},
                'created_because': 'core fallback creature',
                'base_creature_id': None,
                'variant_reason': None,
                'created_at_turn': None,
                'created_by_model': None,
                'created_at': None,
                'updated_at': None,
            }
        )
    return entries


def list_bestiary_entries(
    *,
    workspace_id: str,
    campaign_id: int | None = None,
    session_id: int | None = None,
    scope: str | None = None,
    region_id: str | None = None,
    include_core: bool = False,
) -> list[dict[str, Any]]:
    query = BestiaryEntry.query.filter_by(workspace_id=workspace_id)
    if campaign_id is not None:
        query = query.filter(BestiaryEntry.campaign_id == campaign_id)
    if session_id is not None:
        query = query.filter(BestiaryEntry.session_id == session_id)
    if scope:
        query = query.filter(BestiaryEntry.scope == scope)
    if region_id:
        query = query.filter(BestiaryEntry.region_id == region_id)
    rows = query.order_by(BestiaryEntry.scope.asc(), BestiaryEntry.name.asc(), BestiaryEntry.version.desc()).all()
    entries = [bestiary_entry_payload(row) for row in rows]
    if include_core:
        entries.extend(core_entries())
    return entries


def bestiary_entry_for_creature(
    *,
    workspace_id: str,
    creature_id: str,
    campaign_id: int | None = None,
    session_id: int | None = None,
) -> dict[str, Any] | None:
    if creature_id in {entry['creature_id'] for entry in core_entries()}:
        creature = core_creature(creature_id)
        if creature:
            return next((entry for entry in core_entries() if entry['creature_id'] == creature_id), None)
    query = BestiaryEntry.query.filter_by(workspace_id=workspace_id, creature_id=creature_id)
    if campaign_id is not None:
        query = query.filter((BestiaryEntry.campaign_id == campaign_id) | (BestiaryEntry.campaign_id.is_(None)))
    if session_id is not None:
        query = query.filter((BestiaryEntry.session_id == session_id) | (BestiaryEntry.session_id.is_(None)))
    row = query.order_by(BestiaryEntry.version.desc(), BestiaryEntry.updated_at.desc()).first()
    return bestiary_entry_payload(row) if row else None


def save_bestiary_entry(
    *,
    workspace_id: str,
    creature: dict[str, Any],
    scope: str,
    source: str,
    persistence: str,
    campaign_id: int | None = None,
    session_id: int | None = None,
    region_id: str | None = None,
    location_ids: list[str] | None = None,
    faction_ids: list[str] | None = None,
    tags: list[str] | None = None,
    created_because: str | None = None,
    base_creature_id: str | None = None,
    variant_reason: str | None = None,
    created_at_turn: int | None = None,
    created_by_model: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_creature_definition(creature, source=source)
    normalized['source'] = source
    row = BestiaryEntry(
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        session_id=session_id,
        scope=scope,
        creature_id=normalized['id'],
        version=int(normalized.get('version') or 1),
        name=normalized['name'],
        source=source,
        persistence=persistence,
        region_id=region_id,
        location_ids_json=safe_json_dumps(_json_list(location_ids), []),
        faction_ids_json=safe_json_dumps(_json_list(faction_ids), []),
        tags_json=safe_json_dumps(_json_list(tags) or normalized.get('visualTags') or [], []),
        creature_json=safe_json_dumps(normalized, {}),
        balance_json=safe_json_dumps(normalized.get('balance') or {}, {}),
        created_because=created_because,
        base_creature_id=base_creature_id,
        variant_reason=variant_reason,
        created_at_turn=created_at_turn,
        created_by_model=created_by_model,
    )
    db.session.add(row)
    db.session.flush()
    return bestiary_entry_payload(row)


def campaign_workspace_id(campaign_id: int | None, fallback: str) -> str:
    if campaign_id is None:
        return fallback
    campaign = db.session.get(Campaign, campaign_id)
    return campaign.workspace_id if campaign else fallback


def should_save_generated_creature(creature: dict[str, Any], context: dict[str, Any] | None = None) -> bool:
    context = context if isinstance(context, dict) else {}
    name = str(creature.get('name') or '').strip().lower()
    if not name or name in {'creature', 'monster', 'enemy', 'swarm', 'illusion'}:
        return False
    if context.get('temporary') or context.get('disposable'):
        return False
    if context.get('survived') or context.get('player_interacted') or context.get('region_id') or context.get('faction_ids'):
        return True
    if context.get('encounter_purpose') in {'boss', 'ritual', 'patrol', 'guard'}:
        return True
    if creature.get('source') in {'generated_variant', 'evolved'}:
        return True
    abilities = creature.get('abilities') if isinstance(creature.get('abilities'), list) else []
    return len(abilities) > 1 and bool(creature.get('visualTags'))


def record_combat_debug_event(
    *,
    session_id: int,
    campaign_id: int,
    event_type: str,
    payload: dict[str, Any],
    turn_id: int | None = None,
    combat_encounter_id: int | None = None,
) -> CombatDebugEvent:
    event = CombatDebugEvent(
        session_id=session_id,
        campaign_id=campaign_id,
        turn_id=turn_id,
        combat_encounter_id=combat_encounter_id,
        event_type=event_type,
        payload_json=safe_json_dumps(deepcopy(payload), {}),
        created_at=utc_now(),
    )
    db.session.add(event)
    db.session.flush()
    return event
