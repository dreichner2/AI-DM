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
from aidm_server.creatures.resolver import resolve_creature_for_encounter
from aidm_server.creatures.schemas import normalize_creature_definition
from aidm_server.creatures.variants import create_creature_variant
from aidm_server.database import db
from aidm_server.errors import error_response
from aidm_server.game_state.application.applier import apply_state_changes, persist_state_to_database
from aidm_server.game_state.models import state_snapshot_for_session, stable_change_id
from aidm_server.game_state.validation.validator import validate_state_changes, validated_changes_for_application
from aidm_server.models import Campaign, CombatDebugEvent, Player, Session, safe_json_dumps, safe_json_loads
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
    if isinstance(payload.get('creature'), dict):
        creature = normalize_creature_definition(payload['creature'], source=payload['creature'].get('source') or 'user_custom')
        resolution = {'creature': creature, 'resolutionMethod': 'encounter_defined', 'source': creature['source'], 'debug': {}}
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
        }
        resolution = resolve_creature_for_encounter(request_payload, workspace_id=campaign.workspace_id)
        creature = resolution['creature']
    enemy_count = max(1, int(payload.get('enemyCount') or payload.get('enemy_count') or 1))
    participants = [player_combat_participant(actor) for actor in (state.get('playerCharacters') or []) if isinstance(actor, dict)]
    for index in range(enemy_count):
        participants.append(
            instantiate_creature(
                creature,
                instance_id=f"enemy_{creature['id']}_{index + 1}",
                team='enemy',
            )
        )
    combat = {
        'status': 'active',
        'round': 1,
        'turnIndex': 0,
        'participants': participants,
        'battlefield': payload.get('battlefield') if isinstance(payload.get('battlefield'), dict) else default_battlefield(state.get('currentScene')),
        'encounterGoal': payload.get('encounterGoal') if isinstance(payload.get('encounterGoal'), dict) else None,
        'initiative': [],
        'flags': {'resolverMethod': resolution.get('resolutionMethod'), 'creatureSource': resolution.get('source')},
    }
    intent_plan = plan_enemy_intents(combat)
    combat = attach_intents_to_combat(combat, intent_plan)
    change = {
        'id': stable_change_id(session_id, 'api.combat.start', creature['id'], enemy_count),
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
        payload={'resolution': resolution, 'intentPlan': intent_plan},
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
        db.session.commit()
        response.update({'validation': validation, 'appliedChanges': apply_result['appliedChanges'], 'combat': apply_result['nextState'].get('combat')})
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
    db.session.commit()
    return jsonify({'validation': validation, 'appliedChanges': apply_result['appliedChanges'], 'combat': apply_result['nextState'].get('combat')})


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
