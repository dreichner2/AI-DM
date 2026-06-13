from __future__ import annotations

from copy import deepcopy
from typing import Any

from flask import Blueprint, jsonify, request

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
from aidm_server.creatures.resolver import resolve_creature_for_encounter, resolve_creatures_for_encounter
from aidm_server.creatures.schemas import normalize_creature_definition
from aidm_server.creatures.variants import create_creature_variant
from aidm_server.database import db
from aidm_server.errors import error_response
from aidm_server.game_state.application.applier import apply_state_changes, persist_state_to_database
from aidm_server.game_state.models import state_snapshot_for_session, stable_change_id
from aidm_server.game_state.validation.validator import validate_state_changes, validated_changes_for_application
from aidm_server.models import Campaign, CombatDebugEvent, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.services.campaign_pack_progress import update_campaign_pack_progress
from aidm_server.validation import parse_json_body
from aidm_server.workspace_access import current_workspace_id, get_campaign, get_session


creatures_bp = Blueprint('creatures', __name__)


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


def _persist_session_state(session_obj: Session, state: dict[str, Any]) -> None:
    players = _campaign_players(session_obj.campaign)
    persist_state_to_database(
        session_obj=session_obj,
        state=state,
        players_by_id={player.player_id: player for player in players},
    )


def _refresh_campaign_pack_progress(session_obj: Session) -> dict[str, Any] | None:
    result = update_campaign_pack_progress(
        session_id=session_obj.session_id,
        campaign_id=session_obj.campaign_id,
        triggered_segments=[],
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
    for index, enemy_id in enumerate(_list(encounter.get('enemyIds') or encounter.get('enemy_ids') or encounter.get('enemies'))):
        enemy = enemy_by_id.get(enemy_id)
        if not enemy:
            continue
        enemy_groups.append(
            {
                'id': f"pack_{enemy_id}",
                'label': enemy.get('name') or enemy_id,
                'count': 1,
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
    db.session.commit()
    return jsonify({'entry': entry}), 201


@creatures_bp.post('/campaigns/<int:campaign_id>/bestiary/generate-pack')
def generate_campaign_bestiary_pack(campaign_id: int):
    campaign = get_campaign(campaign_id)
    if not campaign:
        return error_response('not_found', 'Campaign not found.', 404)
    payload = parse_json_body(request) or {}
    creatures = generate_campaign_pack_bestiary(
        {
            **payload,
            'title': payload.get('title') or campaign.title,
            'campaignThemes': payload.get('campaignThemes') or payload.get('themes') or [campaign.title],
        }
    )
    entries = []
    if payload.get('save', True) is not False:
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
        db.session.commit()
    return jsonify({'campaign_id': campaign_id, 'creatures': creatures, 'entries': entries})


@creatures_bp.post('/creatures/resolve')
def resolve_creature():
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    campaign_id = payload.get('campaignId') or payload.get('campaign_id')
    workspace_id = current_workspace_id()
    if campaign_id:
        campaign = get_campaign(int(campaign_id))
        if not campaign:
            return error_response('not_found', 'Campaign not found.', 404)
        workspace_id = campaign.workspace_id
    result = resolve_creature_for_encounter(payload, workspace_id=workspace_id)
    db.session.commit()
    return jsonify(result)


@creatures_bp.post('/creatures/generate')
def generate_creature():
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    creature, model_name = generate_new_creature(payload)
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
        party_level=int(request_payload.get('partyLevel') or request_payload.get('party_level') or 1),
        party_size=int(request_payload.get('partySize') or request_payload.get('party_size') or 4),
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
    context = payload.get('eventContext') if isinstance(payload.get('eventContext'), dict) else payload.get('event_context') if isinstance(payload.get('event_context'), dict) else payload
    evolved = evolve_creature(
        base,
        context,
        party_level=int(payload.get('partyLevel') or payload.get('party_level') or base.get('level') or 1),
        party_size=int(payload.get('partySize') or payload.get('party_size') or 4),
    )
    entry = None
    campaign_id = payload.get('campaignId') or payload.get('campaign_id')
    if campaign_id and payload.get('saveGenerated', payload.get('save_generated', True)) is not False:
        campaign = get_campaign(int(campaign_id))
        if not campaign:
            return error_response('not_found', 'Campaign not found.', 404)
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
    payload = parse_json_body(request) or {}
    state = _session_state(session_obj)
    campaign = session_obj.campaign
    pack_request_payload = _campaign_pack_encounter_request(state, payload, campaign=campaign, session_id=session_id)
    if pack_request_payload:
        encounter_resolution = resolve_creatures_for_encounter(pack_request_payload, workspace_id=campaign.workspace_id)
        encounter_resolution['campaignPackEncounter'] = pack_request_payload.get('campaignPackEncounter')
    elif isinstance(payload.get('creature'), dict):
        creature = normalize_creature_definition(payload['creature'], source=payload['creature'].get('source') or 'user_custom')
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
        request_payload = {
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
        encounter_resolution = resolve_creatures_for_encounter(request_payload, workspace_id=campaign.workspace_id)
    participants = [player_combat_participant(actor) for actor in (state.get('playerCharacters') or []) if isinstance(actor, dict)]
    participants.extend(_instantiate_groups_for_api(encounter_resolution))
    encounter_flags = _encounter_flag_summary(encounter_resolution)
    combat = {
        'status': 'active',
        'round': 1,
        'turnIndex': 0,
        'participants': participants,
        'battlefield': payload.get('battlefield') if isinstance(payload.get('battlefield'), dict) else default_battlefield(state.get('currentScene')),
        'encounterGoal': payload.get('encounterGoal') if isinstance(payload.get('encounterGoal'), dict) else encounter_resolution.get('encounterGoal'),
        'initiative': [],
        'flags': encounter_flags,
    }
    intent_plan = plan_enemy_intents(combat)
    combat = attach_intents_to_combat(combat, intent_plan)
    change = {
        'id': stable_change_id(session_id, 'api.combat.start', encounter_flags.get('resolverMethod'), encounter_flags.get('enemyCount')),
        'type': 'combat.start',
        'combat': combat,
        'reason': 'Combat started from API.',
        'visible': False,
    }
    validation = validate_state_changes(state=state, changes=[change])
    applied = validated_changes_for_application(validation)
    apply_result = apply_state_changes(state, applied)
    _persist_session_state(session_obj, apply_result['nextState'])
    record_combat_debug_event(
        session_id=session_id,
        campaign_id=campaign.campaign_id,
        event_type='api_combat_start',
        payload={'resolution': encounter_resolution, 'intentPlan': intent_plan},
    )
    db.session.commit()
    return jsonify({'combat': apply_result['nextState'].get('combat'), 'validation': validation})


@creatures_bp.post('/sessions/<int:session_id>/combat/plan-enemy-intents')
def plan_session_enemy_intents(session_id: int):
    session_obj = get_session(session_id)
    if not session_obj:
        return error_response('not_found', 'Session not found.', 404)
    state = _session_state(session_obj)
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    intent_plan = plan_enemy_intents(combat)
    combat_with_intents = attach_intents_to_combat(combat, intent_plan)
    return jsonify({'intentPlan': intent_plan, 'combat': combat_with_intents})


@creatures_bp.post('/sessions/<int:session_id>/combat/apply-morale-event')
def apply_session_combat_morale_event(session_id: int):
    session_obj = get_session(session_id)
    if not session_obj:
        return error_response('not_found', 'Session not found.', 404)
    payload = parse_json_body(request) or {}
    participant_id = payload.get('participantId') or payload.get('participant_id') or payload.get('enemyId') or payload.get('enemy_id')
    event = payload.get('event') or payload.get('moraleEvent') or payload.get('morale_event')
    change = {
        'id': stable_change_id(session_id, 'api.combat.morale_event', participant_id, event),
        'type': 'combat.morale.event',
        'participantId': participant_id,
        'event': event,
        'reason': payload.get('reason') or 'Combat morale event applied from API.',
        'visible': False,
    }
    state = _session_state(session_obj)
    validation = validate_state_changes(state=state, changes=[change])
    applied = validated_changes_for_application(validation)
    apply_result = apply_state_changes(state, applied)
    _persist_session_state(session_obj, apply_result['nextState'])
    db.session.commit()
    return jsonify({'validation': validation, 'appliedChanges': apply_result['appliedChanges'], 'combat': apply_result['nextState'].get('combat')})


@creatures_bp.post('/sessions/<int:session_id>/combat/check-end')
def check_session_combat_end(session_id: int):
    session_obj = get_session(session_id)
    if not session_obj:
        return error_response('not_found', 'Session not found.', 404)
    payload = parse_json_body(request) or {}
    state = _session_state(session_obj)
    combat = state.get('combat') if isinstance(state.get('combat'), dict) else {}
    reason = check_combat_end(combat)
    response: dict[str, Any] = {'endReason': reason, 'combat': combat}
    if reason and payload.get('apply'):
        change = combat_end_change(session_id, reason)
        validation = validate_state_changes(state=state, changes=[change])
        applied = validated_changes_for_application(validation)
        apply_result = apply_state_changes(state, applied)
        _persist_session_state(session_obj, apply_result['nextState'])
        progress = _refresh_campaign_pack_progress(session_obj)
        db.session.commit()
        response.update(
            {
                'validation': validation,
                'appliedChanges': apply_result['appliedChanges'],
                'combat': safe_json_loads(session_obj.state_snapshot, {}).get('combat'),
                'campaignPackProgress': progress,
            }
        )
    return jsonify(response)


@creatures_bp.post('/sessions/<int:session_id>/combat/apply-state-changes')
def apply_session_combat_changes(session_id: int):
    session_obj = get_session(session_id)
    if not session_obj:
        return error_response('not_found', 'Session not found.', 404)
    payload = parse_json_body(request) or {}
    changes = payload.get('changes') if isinstance(payload.get('changes'), list) else []
    state = _session_state(session_obj)
    validation = validate_state_changes(state=state, changes=changes)
    applied = validated_changes_for_application(validation)
    apply_result = apply_state_changes(state, applied)
    _persist_session_state(session_obj, apply_result['nextState'])
    progress = _refresh_campaign_pack_progress(session_obj)
    db.session.commit()
    return jsonify(
        {
            'validation': validation,
            'appliedChanges': apply_result['appliedChanges'],
            'combat': safe_json_loads(session_obj.state_snapshot, {}).get('combat'),
            'campaignPackProgress': progress,
        }
    )


@creatures_bp.get('/sessions/<int:session_id>/combat/debug')
def get_session_combat_debug(session_id: int):
    session_obj = get_session(session_id)
    if not session_obj:
        return error_response('not_found', 'Session not found.', 404)
    limit = max(1, min(100, int(request.args.get('limit') or 50)))
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
