from __future__ import annotations

import json
import uuid
from typing import Any

from flask import Blueprint, jsonify, request

from aidm_server.capabilities import capability_forbidden_response, current_actor_has_capability
from aidm_server.combat.end_conditions import check_combat_end, combat_end_change
from aidm_server.combat.intent_planner import attach_intents_to_combat, plan_enemy_intents
from aidm_server.combat.state import default_battlefield, instantiate_creature, player_combat_participant
from aidm_server.creatures.balance import analyze_creature_balance, auto_scale_creature
from aidm_server.creatures.campaign_pack import generate_campaign_pack_bestiary
from aidm_server.creatures.core_bestiary import core_bestiary
from aidm_server.creatures.evolution import evolve_creature
from aidm_server.creatures.generator import generate_new_creature
from aidm_server.creatures.repository import (
    list_bestiary_entries,
    record_combat_debug_event,
    save_bestiary_entry,
)
from aidm_server.creatures.resolver import (
    persist_creature_resolution_plan,
    plan_creatures_for_encounter,
    resolve_creature_for_encounter,
)
from aidm_server.creatures.schemas import normalize_creature_definition
from aidm_server.creatures.variants import create_creature_variant
from aidm_server.database import db, release_clean_scoped_session
from aidm_server.errors import error_response
from aidm_server.game_state.campaign_pack_encounters import (
    MAX_CAMPAIGN_PACK_ENEMIES_PER_GROUP,
    MAX_CAMPAIGN_PACK_ENEMY_PARTICIPANTS,
)
from aidm_server.game_state.models import state_snapshot_for_session, stable_change_id
from aidm_server.models import (
    Campaign,
    CombatDebugEvent,
    CombatEncounter,
    Player,
    Session,
    safe_json_loads,
)
from aidm_server.operator_audit import record_operator_action
from aidm_server.provider_priority import foreground_provider_reservation
from aidm_server.services.campaign_pack_progress import update_campaign_pack_progress
from aidm_server.services.session_state_mutation import (
    SessionStateBeforePersistContext,
    SessionStateMutationPlan,
    expected_state_revision_from_payload,
    mutate_session_state,
    state_conflict_response,
)
from aidm_server.turn_coordinator import session_turn_coordinator
from aidm_server.validation import coerce_bool, coerce_int, parse_json_body
from aidm_server.workspace_access import (
    current_workspace_id,
    get_campaign,
    get_session,
)


creatures_bp = Blueprint('creatures', __name__)

_PUBLIC_CREATURE_RESOLUTION_FIELDS = frozenset(
    {
        'creature',
        'source',
        'resolutionMethod',
        'matchScore',
        'generated',
        'savedToBestiary',
        'notes',
    }
)


def _combat_operator_forbidden_response():
    return capability_forbidden_response(
        'dm_runtime_control',
        'Only workspace admins can manage combat state and debug logs.',
    )


def _bestiary_authoring_forbidden_response():
    return capability_forbidden_response(
        'dm_authoring',
        'Only workspace admins can author or save campaign bestiary content.',
    )


def _save_generated_enabled(payload: dict[str, Any]) -> bool:
    raw_value = payload.get('saveGenerated') if 'saveGenerated' in payload else payload.get('save_generated')
    parsed = coerce_bool(raw_value, True)
    return True if parsed is None else parsed


def _creature_resolution_response(result: dict[str, Any]) -> dict[str, Any]:
    if current_actor_has_capability('debug_read'):
        return result
    return {key: value for key, value in result.items() if key in _PUBLIC_CREATURE_RESOLUTION_FIELDS}


def _campaign_players(campaign: Campaign) -> list[Player]:
    return (
        Player.query.filter_by(workspace_id=campaign.workspace_id, campaign_id=campaign.campaign_id)
        .order_by(Player.player_id.asc())
        .all()
    )


def _session_state(session_obj: Session) -> dict[str, Any]:
    campaign = session_obj.campaign
    players = _campaign_players(campaign)
    return state_snapshot_for_session(session_obj=session_obj, campaign=campaign, players=players)


def _combat_start_context(session_id: int) -> tuple[Session, Campaign, list[Player], dict[str, Any]]:
    """Reload every ORM input needed to prepare or apply a combat start."""

    db.session.expire_all()
    session_obj = db.session.get(Session, session_id)
    if session_obj is None:
        raise LookupError(f'Session {session_id} is no longer available.')
    db.session.refresh(session_obj)
    campaign = db.session.get(Campaign, session_obj.campaign_id)
    if campaign is None:
        raise LookupError(f'Campaign {session_obj.campaign_id} is no longer available.')
    db.session.refresh(campaign)
    players = _campaign_players(campaign)
    state = state_snapshot_for_session(session_obj=session_obj, campaign=campaign, players=players)
    return session_obj, campaign, players, state


def _combat_start_context_signature(
    session_obj: Session,
    campaign: Campaign,
    players: list[Player],
    state: dict[str, Any],
) -> tuple[Any, ...]:
    """Identify non-revisioned campaign/player context used by provider plans."""

    player_characters = state.get('playerCharacters') if isinstance(state.get('playerCharacters'), list) else []
    return (
        session_obj.session_id,
        session_obj.campaign_id,
        campaign.campaign_id,
        campaign.workspace_id,
        campaign.title,
        campaign.location,
        tuple(player.player_id for player in players),
        json.dumps(player_characters, sort_keys=True, separators=(',', ':'), default=str),
    )


def _state_revision(state: dict[str, Any]) -> int:
    return expected_state_revision_from_payload(state) or 0


def _combat_start_context_conflict(*, expected_revision: int, actual_revision: int):
    return error_response(
        'state_conflict',
        'Session context changed while combat was being prepared. Refresh and retry.',
        409,
        {
            'expected_state_revision': expected_revision,
            'actual_state_revision': actual_revision,
        },
    )


def _refresh_campaign_pack_progress(session_obj: Session) -> dict[str, Any] | None:
    result = update_campaign_pack_progress(
        session_id=session_obj.session_id,
        campaign_id=session_obj.campaign_id,
        triggered_segments=[],
        commit=False,
    )
    if not result.changed:
        return None
    return {
        'active_checkpoint_id': result.active_checkpoint_id,
        'completed_checkpoint_ids': result.completed_checkpoint_ids,
        'skipped_checkpoint_ids': result.skipped_checkpoint_ids or [],
        'failed_checkpoint_ids': result.failed_checkpoint_ids or [],
        'reason': result.reason,
    }


def _encounter_flag_summary(encounter_resolution: dict[str, Any]) -> dict[str, Any]:
    groups = [
        {
            'label': group.get('label'),
            'count': group.get('count'),
            'creatureId': (group.get('creature') or {}).get('id') if isinstance(group.get('creature'), dict) else None,
            'name': (group.get('creature') or {}).get('name') if isinstance(group.get('creature'), dict) else None,
            'source': group.get('source'),
            'resolutionMethod': group.get('resolutionMethod'),
        }
        for group in (encounter_resolution.get('groups') or [])
        if isinstance(group, dict)
    ]
    flags = {
        'resolverMethod': encounter_resolution.get('resolutionMethod'),
        'creatureSource': ', '.join(encounter_resolution.get('sources') or []),
        'enemyCount': encounter_resolution.get('totalEnemies'),
        'enemyGroups': groups,
    }
    pack_encounter = encounter_resolution.get('campaignPackEncounter')
    if isinstance(pack_encounter, dict):
        flags['campaignPackEncounterId'] = pack_encounter.get('id')
        flags['campaignPackId'] = pack_encounter.get('packId')
        flags['campaignPackCheckpointIds'] = pack_encounter.get('checkpointIds') or []
    return flags


def _instantiate_groups_for_api(encounter_resolution: dict[str, Any]) -> list[dict[str, Any]]:
    participants: list[dict[str, Any]] = []
    sequence = 1
    for group in encounter_resolution.get('groups') or []:
        if not isinstance(group, dict) or not isinstance(group.get('creature'), dict):
            continue
        count = max(1, int(group.get('count') or 1))
        creature = group['creature']
        for _index in range(count):
            participants.append(
                instantiate_creature(
                    creature,
                    instance_id=f"enemy_{creature['id']}_{sequence}",
                    team='enemy',
                )
            )
            sequence += 1
    return participants


def _text(value: Any) -> str:
    return str(value or '').strip()


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [item.strip() for item in value.replace(';', ',').split(',') if item.strip()]
    return []


def _canonical_change_payload(change: dict[str, Any]) -> str:
    fingerprint = {key: value for key, value in change.items() if key not in {'id', 'changeId', 'change_id'}}
    return json.dumps(fingerprint, sort_keys=True, separators=(',', ':'), default=str)


def _request_idempotency_key(payload: dict[str, Any]) -> str:
    return _text(
        payload.get('idempotencyKey')
        or payload.get('idempotency_key')
        or payload.get('requestId')
        or payload.get('request_id')
        or payload.get('clientRequestId')
        or payload.get('client_request_id')
        or payload.get('clientMutationId')
        or payload.get('client_mutation_id')
    )


def _combat_api_changes_with_ids(session_id: int, changes: list[Any], *, idempotency_key: str | None = None) -> list[Any]:
    normalized: list[Any] = []
    request_scope = idempotency_key or uuid.uuid4().hex
    payload_occurrences: dict[str, int] = {}
    for change in changes:
        if not isinstance(change, dict):
            normalized.append(change)
            continue
        change_id = _text(change.get('id') or change.get('changeId') or change.get('change_id'))
        if change_id:
            normalized.append({**change, 'id': change_id})
            continue
        canonical_payload = _canonical_change_payload(change)
        payload_occurrence = payload_occurrences.get(canonical_payload, 0)
        payload_occurrences[canonical_payload] = payload_occurrence + 1
        normalized.append(
            {
                **change,
                'id': stable_change_id(
                    session_id,
                    'api.combat.apply_state_changes',
                    request_scope,
                    payload_occurrence,
                    canonical_payload,
                ),
            }
        )
    return normalized


def _pack_record_id(record: dict[str, Any]) -> str:
    return _text(record.get('id') or record.get('encounterId') or record.get('encounter_id'))


def _pack_record_by_id(records: list[dict[str, Any]], record_id: str | None) -> dict[str, Any] | None:
    if not record_id:
        return None
    key = record_id.lower()
    return next((record for record in records if _pack_record_id(record).lower() == key), None)


def _pack_catalog(pack: dict[str, Any], key: str) -> list[dict[str, Any]]:
    catalog = pack.get('catalog') if isinstance(pack.get('catalog'), dict) else {}
    records = catalog.get(key)
    if not isinstance(records, list):
        records = pack.get(key)
    return [record for record in (records or []) if isinstance(record, dict)]


def _positive_int(value: Any, default: int = 1) -> int:
    try:
        amount = int(value)
    except (TypeError, ValueError):
        amount = default
    return max(1, amount)


def _encounter_enemy_specs(encounter: dict[str, Any]) -> list[tuple[str, int]]:
    specs_by_id: dict[str, int] = {}
    ordered_ids: list[str] = []

    def add_spec(enemy_id: Any, count: Any, *, override: bool = False) -> None:
        key = _text(enemy_id)
        if not key:
            return
        if key not in specs_by_id:
            ordered_ids.append(key)
        if override or key not in specs_by_id:
            specs_by_id[key] = min(_positive_int(count), MAX_CAMPAIGN_PACK_ENEMIES_PER_GROUP)

    for enemy_id in _list(encounter.get('enemyIds') or encounter.get('enemy_ids')):
        add_spec(enemy_id, 1)
    groups = encounter.get('enemyGroups') or encounter.get('enemy_groups') or encounter.get('enemies')
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, str):
                add_spec(group, 1, override=True)
            elif isinstance(group, dict):
                enemy_id = group.get('enemyId') or group.get('enemy_id') or group.get('id') or group.get('creatureId')
                add_spec(enemy_id, group.get('count'), override=True)
    bounded_specs: list[tuple[str, int]] = []
    remaining = MAX_CAMPAIGN_PACK_ENEMY_PARTICIPANTS
    for enemy_id in ordered_ids:
        if remaining <= 0:
            break
        count = min(specs_by_id[enemy_id], remaining)
        bounded_specs.append((enemy_id, count))
        remaining -= count
    return bounded_specs


def _pack_active_checkpoint(pack: dict[str, Any], flags: dict[str, Any], checkpoints: list[dict[str, Any]]) -> dict[str, Any] | None:
    active_id = _text(
        pack.get('activeCheckpointId')
        or pack.get('active_checkpoint_id')
        or flags.get('campaignPackActiveCheckpointId')
        or flags.get('activeCheckpointId')
    )
    completed_ids = {str(value).strip().lower() for value in _list(pack.get('completedCheckpointIds') or flags.get('campaignPackCompletedCheckpointIds'))}
    checkpoint = _pack_record_by_id(checkpoints, active_id)
    if checkpoint and _pack_record_id(checkpoint).lower() not in completed_ids:
        return checkpoint
    return next((item for item in checkpoints if _pack_record_id(item).lower() not in completed_ids), None)


def _campaign_pack_encounter_request(
    state: dict[str, Any],
    payload: dict[str, Any],
    *,
    campaign: Campaign,
    session_id: int,
) -> dict[str, Any] | None:
    pack = state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}
    pack_id = _text(pack.get('packId') or pack.get('pack_id'))
    if not pack_id:
        return None

    flags = state.get('flags') if isinstance(state.get('flags'), dict) else {}
    encounters = _pack_catalog(pack, 'encounters')
    enemies = _pack_catalog(pack, 'enemies')
    checkpoints = [checkpoint for checkpoint in (pack.get('checkpoints') or []) if isinstance(checkpoint, dict)]
    requested_encounter_id = _text(payload.get('encounterId') or payload.get('encounter_id'))
    checkpoint = _pack_active_checkpoint(pack, flags, checkpoints)
    encounter = _pack_record_by_id(encounters, requested_encounter_id)
    if not encounter and checkpoint:
        encounter_ids = _list(
            checkpoint.get('encounterIds')
            or checkpoint.get('encounter_ids')
            or checkpoint.get('encounters')
        )
        encounter = next((_pack_record_by_id(encounters, encounter_id) for encounter_id in encounter_ids), None)
    if not encounter:
        return None

    enemy_by_id = {_pack_record_id(enemy): enemy for enemy in enemies}
    enemy_groups = []
    for index, (enemy_id, count) in enumerate(_encounter_enemy_specs(encounter)):
        enemy = enemy_by_id.get(enemy_id)
        if not enemy:
            continue
        enemy_groups.append(
            {
                'id': f"pack_{enemy_id}",
                'label': enemy.get('name') or enemy_id,
                'count': count,
                'creature': enemy,
                'themeTags': ['campaign_pack', f'pack:{pack_id}', *_list(enemy.get('tags') or enemy.get('visualTags'))],
                'encounterPurpose': 'campaign_pack',
            }
        )
        if index >= 11:
            break
    if not enemy_groups:
        return None

    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    checkpoint_ids = _list(encounter.get('checkpointIds') or encounter.get('checkpoint_ids'))
    if checkpoint and _pack_record_id(checkpoint) and _pack_record_id(checkpoint) not in checkpoint_ids:
        checkpoint_ids.append(_pack_record_id(checkpoint))
    return {
        'campaignId': campaign.campaign_id,
        'sessionId': session_id,
        'regionId': payload.get('regionId') or scene.get('locationId'),
        'locationId': payload.get('locationId') or scene.get('locationId'),
        'encounterPurpose': payload.get('encounterPurpose') or 'campaign_pack',
        'themeTags': ['campaign_pack', f'pack:{pack_id}', *_list(encounter.get('tags'))],
        'partyLevel': payload.get('partyLevel') or 1,
        'partySize': max(1, len(state.get('playerCharacters') or [])),
        'difficulty': payload.get('difficulty') or encounter.get('difficulty') or 'standard',
        'descriptionHint': payload.get('descriptionHint') or encounter.get('summary') or encounter.get('description') or encounter.get('title') or 'Campaign pack encounter.',
        'allowGeneration': False,
        'allowVariants': payload.get('allowVariants', True),
        'enemyGroups': enemy_groups,
        'campaignPackEncounter': {
            'id': _pack_record_id(encounter),
            'title': encounter.get('title') or encounter.get('name'),
            'packId': pack_id,
            'checkpointIds': checkpoint_ids,
        },
    }


@creatures_bp.get('/bestiary/core')
def get_core_bestiary():
    return jsonify({'entries': core_bestiary()})


@creatures_bp.get('/campaigns/<int:campaign_id>/bestiary')
def get_campaign_bestiary(campaign_id: int):
    campaign = get_campaign(campaign_id)
    if not campaign:
        return error_response('not_found', 'Campaign not found.', 404)
    return jsonify(
        {
            'campaign_id': campaign_id,
            'entries': list_bestiary_entries(
                workspace_id=campaign.workspace_id,
                campaign_id=campaign_id,
                include_core=request.args.get('include_core') in {'1', 'true', 'yes'},
            ),
        }
    )


@creatures_bp.get('/campaigns/<int:campaign_id>/regions/<region_id>/bestiary')
def get_region_bestiary(campaign_id: int, region_id: str):
    campaign = get_campaign(campaign_id)
    if not campaign:
        return error_response('not_found', 'Campaign not found.', 404)
    return jsonify(
        {
            'campaign_id': campaign_id,
            'region_id': region_id,
            'entries': list_bestiary_entries(
                workspace_id=campaign.workspace_id,
                campaign_id=campaign_id,
                scope='region',
                region_id=region_id,
            ),
        }
    )


@creatures_bp.post('/campaigns/<int:campaign_id>/bestiary')
def create_campaign_bestiary_entry(campaign_id: int):
    campaign = get_campaign(campaign_id)
    if not campaign:
        return error_response('not_found', 'Campaign not found.', 404)
    forbidden = _bestiary_authoring_forbidden_response()
    if forbidden:
        return forbidden
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    raw_creature = payload.get('creature') if isinstance(payload.get('creature'), dict) else payload
    source = str(payload.get('source') or raw_creature.get('source') or 'user_custom')
    scope = str(payload.get('scope') or ('region' if payload.get('region_id') or payload.get('regionId') else 'campaign'))
    entry = save_bestiary_entry(
        workspace_id=campaign.workspace_id,
        campaign_id=campaign_id,
        region_id=payload.get('region_id') or payload.get('regionId'),
        scope=scope,
        source=source,
        persistence=str(payload.get('persistence') or scope),
        creature=normalize_creature_definition(raw_creature, source=source),
        tags=payload.get('tags') if isinstance(payload.get('tags'), list) else None,
        location_ids=payload.get('location_ids') or payload.get('locationIds'),
        faction_ids=payload.get('faction_ids') or payload.get('factionIds'),
        created_because=payload.get('created_because') or payload.get('createdBecause'),
        base_creature_id=payload.get('base_creature_id') or payload.get('baseCreatureId'),
        variant_reason=payload.get('variant_reason') or payload.get('variantReason'),
    )
    record_operator_action(
        action='bestiary.create',
        resource_type='bestiary_entry',
        workspace_id=campaign.workspace_id,
        campaign_id=campaign_id,
        resource_id=entry.get('bestiary_entry_id') if isinstance(entry, dict) else None,
        details={
            'scope': scope,
            'source': source,
            'creatureId': entry.get('creature', {}).get('id') if isinstance(entry, dict) else None,
            'creatureName': entry.get('creature', {}).get('name') if isinstance(entry, dict) else None,
        },
    )
    db.session.commit()
    return jsonify({'entry': entry}), 201


@creatures_bp.post('/campaigns/<int:campaign_id>/bestiary/generate-pack')
def generate_campaign_bestiary_pack(campaign_id: int):
    campaign = get_campaign(campaign_id)
    if not campaign:
        return error_response('not_found', 'Campaign not found.', 404)
    forbidden = _bestiary_authoring_forbidden_response()
    if forbidden:
        return forbidden
    payload = parse_json_body(request) or {}
    creatures = generate_campaign_pack_bestiary(
        {
            **payload,
            'title': payload.get('title') or campaign.title,
            'campaignThemes': payload.get('campaignThemes') or payload.get('themes') or [campaign.title],
        }
    )
    entries = []
    save_entries = coerce_bool(payload.get('save'), True)
    if save_entries is None:
        save_entries = True
    if save_entries:
        for creature in creatures:
            entries.append(
                save_bestiary_entry(
                    workspace_id=campaign.workspace_id,
                    campaign_id=campaign_id,
                    scope='campaign',
                    source='campaign_pack',
                    persistence='campaign',
                    creature=creature,
                    tags=creature.get('visualTags') or [],
                    created_because=payload.get('createdBecause') or 'Generated campaign pack bestiary seed.',
                )
            )
    record_operator_action(
        action='bestiary.generate_pack',
        resource_type='bestiary_entry',
        workspace_id=campaign.workspace_id,
        campaign_id=campaign_id,
        details={
            'generatedCount': len(creatures),
            'savedCount': len(entries),
            'themes': payload.get('campaignThemes') or payload.get('themes') or [campaign.title],
        },
    )
    db.session.commit()
    return jsonify({'campaign_id': campaign_id, 'creatures': creatures, 'entries': entries})


@creatures_bp.post('/creatures/resolve')
def resolve_creature():
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    campaign_id = payload.get('campaignId') or payload.get('campaign_id')
    if campaign_id:
        # Even a non-persisting preview can resolve against campaign-scoped
        # bestiary entries. Keep those authored and potentially unrevealed
        # creatures behind the DM boundary.
        forbidden = _bestiary_authoring_forbidden_response()
        if forbidden:
            return forbidden
    workspace_id = current_workspace_id()
    if campaign_id:
        campaign = get_campaign(int(campaign_id))
        if not campaign:
            return error_response('not_found', 'Campaign not found.', 404)
        workspace_id = campaign.workspace_id
    result = resolve_creature_for_encounter(
        payload,
        workspace_id=workspace_id,
        before_provider_call=lambda: release_clean_scoped_session(
            boundary='creature resolver provider'
        ),
    )
    db.session.commit()
    return jsonify(_creature_resolution_response(result))


@creatures_bp.post('/creatures/generate')
def generate_creature():
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    creature, model_name = generate_new_creature(
        payload,
        before_provider_call=lambda: release_clean_scoped_session(
            boundary='creature generation provider'
        ),
    )
    return jsonify({'creature': creature, 'generationSource': model_name, 'balance': creature.get('balance') or {}})


@creatures_bp.post('/creatures/variant')
def create_creature_variant_endpoint():
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    base = payload.get('baseCreature') or payload.get('base_creature')
    if not isinstance(base, dict):
        return error_response('validation_error', 'baseCreature is required.', 400)
    request_payload = payload.get('request') if isinstance(payload.get('request'), dict) else payload
    variant = create_creature_variant(
        base,
        request_payload,
        party_level=_positive_int(request_payload.get('partyLevel') or request_payload.get('party_level'), 1),
        party_size=_positive_int(request_payload.get('partySize') or request_payload.get('party_size'), 4),
    )
    return jsonify({'creature': variant, 'balance': variant.get('balance') or {}})


@creatures_bp.post('/creatures/evolve')
def evolve_creature_endpoint():
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    base = payload.get('baseCreature') or payload.get('base_creature')
    if not isinstance(base, dict):
        return error_response('validation_error', 'baseCreature is required.', 400)
    campaign_id = payload.get('campaignId') or payload.get('campaign_id')
    save_generated = _save_generated_enabled(payload)
    campaign = None
    if campaign_id and save_generated:
        forbidden = _bestiary_authoring_forbidden_response()
        if forbidden:
            return forbidden
        campaign = get_campaign(int(campaign_id))
        if not campaign:
            return error_response('not_found', 'Campaign not found.', 404)
    context = payload.get('eventContext') if isinstance(payload.get('eventContext'), dict) else payload.get('event_context') if isinstance(payload.get('event_context'), dict) else payload
    evolved = evolve_creature(
        base,
        context,
        party_level=int(payload.get('partyLevel') or payload.get('party_level') or base.get('level') or 1),
        party_size=int(payload.get('partySize') or payload.get('party_size') or 4),
    )
    entry = None
    if campaign_id and save_generated and campaign:
        entry = save_bestiary_entry(
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.campaign_id,
            session_id=payload.get('sessionId') or payload.get('session_id'),
            scope='session' if payload.get('sessionId') or payload.get('session_id') else 'campaign',
            source='evolved',
            persistence='session' if payload.get('sessionId') or payload.get('session_id') else 'campaign',
            creature=evolved,
            tags=evolved.get('visualTags') or [],
            created_because=evolved.get('evolutionReason'),
            base_creature_id=evolved.get('baseCreatureId'),
            variant_reason=evolved.get('evolutionReason'),
        )
        record_operator_action(
            action='bestiary.evolve_save',
            resource_type='bestiary_entry',
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.campaign_id,
            session_id=payload.get('sessionId') or payload.get('session_id'),
            resource_id=entry.get('bestiary_entry_id') if isinstance(entry, dict) else None,
            details={
                'creatureId': evolved.get('id'),
                'creatureName': evolved.get('name'),
                'baseCreatureId': evolved.get('baseCreatureId'),
                'scope': 'session' if payload.get('sessionId') or payload.get('session_id') else 'campaign',
            },
        )
        db.session.commit()
    return jsonify({'creature': evolved, 'entry': entry})


@creatures_bp.post('/creatures/analyze-balance')
def analyze_balance():
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    creature = payload.get('creature') if isinstance(payload.get('creature'), dict) else payload
    balance = analyze_creature_balance(
        creature,
        party_level=int(payload.get('partyLevel') or payload.get('party_level') or creature.get('level') or 1),
        party_size=int(payload.get('partySize') or payload.get('party_size') or 4),
        target_difficulty=payload.get('difficulty') or payload.get('targetDifficulty') or creature.get('challengeTier'),
    )
    scaled = auto_scale_creature(
        creature,
        balance,
        party_level=int(payload.get('partyLevel') or payload.get('party_level') or creature.get('level') or 1),
        party_size=int(payload.get('partySize') or payload.get('party_size') or 4),
        target_difficulty=payload.get('difficulty') or payload.get('targetDifficulty') or creature.get('challengeTier'),
    )
    return jsonify({'balance': balance, 'scaledCreature': scaled})


@creatures_bp.post('/sessions/<int:session_id>/combat/start')
def start_session_combat(session_id: int):
    session_obj = get_session(session_id)
    if not session_obj:
        return error_response('not_found', 'Session not found.', 404)
    forbidden = _combat_operator_forbidden_response()
    if forbidden:
        return forbidden
    payload = parse_json_body(request) or {}
    release_clean_scoped_session(boundary='combat start coordinator')

    start_change_id: str | None = None

    def record_start_debug(result):
        applied_ids = {
            str(change.get('id') or '').strip()
            for change in result.applied_changes
            if isinstance(change, dict)
        }
        if not start_change_id or start_change_id not in applied_ids:
            return
        encounter = (
            CombatEncounter.query.filter_by(session_id=session_id)
            .filter(CombatEncounter.status.in_(['starting', 'active']))
            .order_by(CombatEncounter.updated_at.desc())
            .first()
        )
        record_combat_debug_event(
            session_id=session_id,
            campaign_id=result.session_obj.campaign_id if result.session_obj else None,
            combat_encounter_id=(
                encounter.combat_encounter_id if encounter is not None else None
            ),
            event_type='api_combat_start',
            payload={
                'resolution': result.metadata.get('encounterResolution'),
                'intentPlan': result.metadata.get('intentPlan'),
                'stateRevision': result.state_revision,
            },
        )

    with session_turn_coordinator.serialized(session_id):
        try:
            session_obj, campaign, players, state = _combat_start_context(session_id)
        except LookupError:
            return error_response('not_found', 'Session or campaign not found.', 404)

        captured_revision = _state_revision(state)
        requested_revision = expected_state_revision_from_payload(payload)
        if requested_revision is not None and requested_revision != captured_revision:
            result = mutate_session_state(
                session_id,
                build_changes=lambda _locked_session, _state: [],
                source='api.combat.start',
                expected_revision=requested_revision,
            )
            return state_conflict_response(result)

        expected_context = _combat_start_context_signature(session_obj, campaign, players, state)
        pack_request_payload = _campaign_pack_encounter_request(
            state,
            payload,
            campaign=campaign,
            session_id=session_id,
        )
        resolution_plan = None
        if isinstance(payload.get('creature'), dict) and not pack_request_payload:
            creature = normalize_creature_definition(
                payload['creature'],
                source=payload['creature'].get('source') or 'user_custom',
            )
            enemy_count = max(1, int(payload.get('enemyCount') or payload.get('enemy_count') or 1))
            encounter_resolution = {
                'groups': [
                    {
                        'id': 'manual_creature',
                        'label': creature['name'],
                        'count': enemy_count,
                        'creature': creature,
                        'source': creature['source'],
                        'resolutionMethod': 'encounter_defined',
                        'matchScore': 1.0,
                        'generated': False,
                        'savedToBestiary': False,
                        'notes': ['Manual combat start supplied a creature.'],
                    }
                ],
                'totalEnemies': enemy_count,
                'resolutionMethod': 'encounter_defined' if enemy_count == 1 else 'encounter_composed',
                'resolutionMethods': ['encounter_defined'],
                'sources': [creature['source']],
                'generated': False,
                'savedToBestiary': False,
                'encounterGoal': payload.get('encounterGoal') if isinstance(payload.get('encounterGoal'), dict) else None,
                'notes': ['Manual combat start supplied a creature.'],
                'debug': {'manualCreature': creature['id'], 'totalEnemies': enemy_count},
            }
        else:
            request_payload = pack_request_payload or {
                'campaignId': campaign.campaign_id,
                'sessionId': session_id,
                'regionId': payload.get('regionId') or (state.get('currentScene') or {}).get('locationId'),
                'locationId': payload.get('locationId') or (state.get('currentScene') or {}).get('locationId'),
                'encounterPurpose': payload.get('encounterPurpose') or 'custom',
                'themeTags': payload.get('themeTags') or [],
                'partyLevel': payload.get('partyLevel') or 1,
                'partySize': max(1, len(state.get('playerCharacters') or [])),
                'difficulty': payload.get('difficulty') or 'standard',
                'descriptionHint': payload.get('descriptionHint') or 'Manual combat start.',
                'allowGeneration': payload.get('allowGeneration', True),
                'allowVariants': payload.get('allowVariants', True),
                'enemyCount': payload.get('enemyCount') or payload.get('enemy_count') or 1,
                'enemyGroups': payload.get('enemyGroups') or payload.get('enemy_groups') or [],
            }
            resolution_plan = plan_creatures_for_encounter(
                request_payload,
                workspace_id=campaign.workspace_id,
                before_provider_call=lambda: release_clean_scoped_session(
                    boundary='combat creature provider'
                ),
            )
            encounter_resolution = resolution_plan.result
            if pack_request_payload:
                encounter_resolution['campaignPackEncounter'] = pack_request_payload.get('campaignPackEncounter')

        try:
            reloaded_session, reloaded_campaign, reloaded_players, reloaded_state = _combat_start_context(session_id)
        except LookupError:
            return _combat_start_context_conflict(
                expected_revision=captured_revision,
                actual_revision=captured_revision,
            )
        actual_revision = _state_revision(reloaded_state)
        if _combat_start_context_signature(
            reloaded_session,
            reloaded_campaign,
            reloaded_players,
            reloaded_state,
        ) != expected_context:
            return _combat_start_context_conflict(
                expected_revision=captured_revision,
                actual_revision=actual_revision,
            )
        if actual_revision != captured_revision:
            result = mutate_session_state(
                session_id,
                build_changes=lambda _locked_session, _state: [],
                source='api.combat.start',
                expected_revision=captured_revision,
            )
            return state_conflict_response(result)

        participants = [
            player_combat_participant(actor)
            for actor in (reloaded_state.get('playerCharacters') or [])
            if isinstance(actor, dict)
        ]
        participants.extend(_instantiate_groups_for_api(encounter_resolution))
        encounter_flags = _encounter_flag_summary(encounter_resolution)
        combat = {
            'status': 'active',
            'round': 1,
            'turnIndex': 0,
            'participants': participants,
            'battlefield': (
                payload.get('battlefield')
                if isinstance(payload.get('battlefield'), dict)
                else default_battlefield(reloaded_state.get('currentScene'))
            ),
            'encounterGoal': (
                payload.get('encounterGoal')
                if isinstance(payload.get('encounterGoal'), dict)
                else encounter_resolution.get('encounterGoal')
            ),
            'initiative': [],
            'flags': encounter_flags,
        }
        with foreground_provider_reservation() as activate_provider:
            release_clean_scoped_session(boundary='combat intent provider')
            activate_provider()
            intent_plan = plan_enemy_intents(combat)
        combat = attach_intents_to_combat(combat, intent_plan)

        try:
            final_session, final_campaign, final_players, final_state = _combat_start_context(session_id)
        except LookupError:
            return _combat_start_context_conflict(
                expected_revision=captured_revision,
                actual_revision=captured_revision,
            )
        final_revision = _state_revision(final_state)
        if _combat_start_context_signature(
            final_session,
            final_campaign,
            final_players,
            final_state,
        ) != expected_context:
            return _combat_start_context_conflict(
                expected_revision=captured_revision,
                actual_revision=final_revision,
            )

        start_change_id = stable_change_id(
            session_id,
            'api.combat.start',
            encounter_flags.get('resolverMethod'),
            encounter_flags.get('enemyCount'),
        )

        def build_start_changes(
            _locked_session: Session,
            _state: dict[str, Any],
        ) -> SessionStateMutationPlan:
            change = {
                'id': start_change_id,
                'type': 'combat.start',
                'combat': combat,
                'reason': 'Combat started from API.',
                'visible': False,
            }
            return SessionStateMutationPlan(
                changes=[change],
                metadata={'encounterResolution': encounter_resolution, 'intentPlan': intent_plan},
            )

        def persist_accepted_resolution(
            context: SessionStateBeforePersistContext,
        ) -> None:
            applied_ids = {
                str(change.get('id') or '').strip()
                for change in context.applied_changes
                if isinstance(change, dict)
            }
            if (
                resolution_plan is not None
                and start_change_id in applied_ids
            ):
                persist_creature_resolution_plan(resolution_plan)

        result = mutate_session_state(
            session_id,
            build_changes=build_start_changes,
            source='api.combat.start',
            expected_revision=captured_revision,
            sync_combat=True,
            before_persist=persist_accepted_resolution,
            after_persist=record_start_debug,
        )
        if result.conflict:
            return state_conflict_response(result)
        return jsonify(
            {
                'combat': result.state.get('combat'),
                'validation': result.validation,
                'stateRevision': result.state_revision,
            }
        )


@creatures_bp.post('/sessions/<int:session_id>/combat/plan-enemy-intents')
def plan_session_enemy_intents(session_id: int):
    session_obj = get_session(session_id)
    if not session_obj:
        return error_response('not_found', 'Session not found.', 404)
    forbidden = _combat_operator_forbidden_response()
    if forbidden:
        return forbidden
    state = _session_state(session_obj)
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    with foreground_provider_reservation() as activate_provider:
        release_clean_scoped_session(boundary='combat intent provider')
        activate_provider()
        intent_plan = plan_enemy_intents(combat)
    combat_with_intents = attach_intents_to_combat(combat, intent_plan)
    return jsonify({'intentPlan': intent_plan, 'combat': combat_with_intents})


@creatures_bp.post('/sessions/<int:session_id>/combat/apply-morale-event')
def apply_session_combat_morale_event(session_id: int):
    session_obj = get_session(session_id)
    if not session_obj:
        return error_response('not_found', 'Session not found.', 404)
    forbidden = _combat_operator_forbidden_response()
    if forbidden:
        return forbidden
    payload = parse_json_body(request) or {}
    participant_id = payload.get('participantId') or payload.get('participant_id') or payload.get('enemyId') or payload.get('enemy_id')
    event = payload.get('event') or payload.get('moraleEvent') or payload.get('morale_event')

    def build_morale_change(_locked_session: Session, _state: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                'id': stable_change_id(session_id, 'api.combat.morale_event', participant_id, event),
                'type': 'combat.morale.event',
                'participantId': participant_id,
                'event': event,
                'reason': payload.get('reason') or 'Combat morale event applied from API.',
                'visible': False,
            }
        ]

    result = mutate_session_state(
        session_id,
        build_changes=build_morale_change,
        source='api.combat.morale_event',
        expected_revision=expected_state_revision_from_payload(payload),
        sync_combat=True,
    )
    if result.conflict:
        return state_conflict_response(result)
    return jsonify(
        {
            'validation': result.validation,
            'appliedChanges': result.applied_changes,
            'combat': result.state.get('combat'),
            'stateRevision': result.state_revision,
        }
    )


@creatures_bp.post('/sessions/<int:session_id>/combat/check-end')
def check_session_combat_end(session_id: int):
    session_obj = get_session(session_id)
    if not session_obj:
        return error_response('not_found', 'Session not found.', 404)
    payload = parse_json_body(request) or {}
    if payload.get('apply'):
        forbidden = _combat_operator_forbidden_response()
        if forbidden:
            return forbidden

        def build_end_change(_locked_session: Session, state: dict[str, Any]) -> SessionStateMutationPlan:
            combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
            reason = check_combat_end(combat)
            if not reason:
                return SessionStateMutationPlan(changes=[], metadata={'endReason': None})
            return SessionStateMutationPlan(
                changes=[combat_end_change(session_id, reason)],
                metadata={'endReason': reason},
            )

        result = mutate_session_state(
            session_id,
            build_changes=build_end_change,
            source='api.combat.check_end',
            expected_revision=expected_state_revision_from_payload(payload),
            sync_combat=True,
            refresh_progress=_refresh_campaign_pack_progress,
        )
        if result.conflict:
            return state_conflict_response(result)
        response: dict[str, Any] = {
            'endReason': result.metadata.get('endReason'),
            'combat': result.state.get('combat'),
            'stateRevision': result.state_revision,
        }
        if result.metadata.get('endReason'):
            response.update(
                {
                    'validation': result.validation,
                    'appliedChanges': result.applied_changes,
                    'campaignPackProgress': result.metadata.get('campaignPackProgress'),
                }
            )
        return jsonify(response)

    state = _session_state(session_obj)
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    reason = check_combat_end(combat)
    return jsonify({'endReason': reason, 'combat': combat})


@creatures_bp.post('/sessions/<int:session_id>/combat/apply-state-changes')
def apply_session_combat_changes(session_id: int):
    session_obj = get_session(session_id)
    if not session_obj:
        return error_response('not_found', 'Session not found.', 404)
    forbidden = _combat_operator_forbidden_response()
    if forbidden:
        return forbidden
    payload = parse_json_body(request) or {}
    changes = payload.get('changes') if isinstance(payload.get('changes'), list) else []
    changes = _combat_api_changes_with_ids(session_id, changes, idempotency_key=_request_idempotency_key(payload))

    def build_combat_changes(_locked_session: Session, _state: dict[str, Any]) -> list[Any]:
        return changes

    result = mutate_session_state(
        session_id,
        build_changes=build_combat_changes,
        source='api.combat.apply_state_changes',
        expected_revision=expected_state_revision_from_payload(payload),
        sync_combat=True,
        refresh_progress=_refresh_campaign_pack_progress,
    )
    if result.conflict:
        return state_conflict_response(result)
    return jsonify(
        {
            'validation': result.validation,
            'appliedChanges': result.applied_changes,
            'combat': result.state.get('combat'),
            'campaignPackProgress': result.metadata.get('campaignPackProgress'),
            'stateRevision': result.state_revision,
        }
    )


@creatures_bp.get('/sessions/<int:session_id>/combat/debug')
def get_session_combat_debug(session_id: int):
    session_obj = get_session(session_id)
    if not session_obj:
        return error_response('not_found', 'Session not found.', 404)
    forbidden = _combat_operator_forbidden_response()
    if forbidden:
        return forbidden
    limit = max(1, min(100, coerce_int(request.args.get('limit'), 50)))
    rows = (
        CombatDebugEvent.query.filter_by(session_id=session_id)
        .order_by(CombatDebugEvent.created_at.desc(), CombatDebugEvent.debug_event_id.desc())
        .limit(limit)
        .all()
    )
    events = [
        {
            'debug_event_id': row.debug_event_id,
            'session_id': row.session_id,
            'campaign_id': row.campaign_id,
            'turn_id': row.turn_id,
            'combat_encounter_id': row.combat_encounter_id,
            'event_type': row.event_type,
            'payload': safe_json_loads(row.payload_json, {}),
            'created_at': row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return jsonify({'events': events})
