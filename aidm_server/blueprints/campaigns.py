from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from aidm_server.canon_jobs import canon_job_status_counts
from aidm_server.creatures.campaign_pack import generate_campaign_pack_bestiary
from aidm_server.creatures.repository import save_bestiary_entry
from aidm_server.database import db
from aidm_server.errors import error_response
from aidm_server.models import (
    Campaign,
    CampaignSegment,
    CanonJob,
    BestiaryEntry,
    CombatDebugEvent,
    CombatEncounter,
    DmCoherenceFeedback,
    DmTurn,
    Map,
    Player,
    Session,
    StoryEntity,
    StoryEvent,
    StoryFact,
    StoryThread,
    TurnCanonUpdate,
    TurnEvent,
    safe_json_loads,
)
from aidm_server.pagination import limited_page
from aidm_server.response_dtos import (
    campaign_is_archived,
    campaign_payload,
    campaign_payloads,
    isoformat,
)
from aidm_server.services.campaign_pack import CampaignPackImportError, import_campaign_pack
from aidm_server.services.workspace import campaign_workspace_payload
from aidm_server.services.session_lifecycle import delete_session_record
from aidm_server.time_utils import utc_now
from aidm_server.validation import (
    coerce_int,
    missing_fields,
    optional_text as _optional_text,
    parse_json_body,
    required_text as _required_text,
)
from aidm_server.workspace_access import (
    campaign_query,
    current_workspace_id,
    get_campaign as workspace_campaign,
    get_world as workspace_world,
)


logger = logging.getLogger(__name__)
campaigns_bp = Blueprint('campaigns', __name__)
CAMPAIGN_TITLE_MAX_LENGTH = 120
CAMPAIGN_TEXT_MAX_LENGTH = 2000
ACTIVE_STATUS = 'active'
ARCHIVED_STATUS = 'archived'


def _truthy_enabled(value, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {'0', 'false', 'no', 'off', 'disabled'}


def _theme_list(value) -> list[str]:
    if isinstance(value, str):
        raw_values = value.replace(';', ',').split(',')
    elif isinstance(value, list):
        raw_values = value
    else:
        raw_values = []
    result: list[str] = []
    for item in raw_values:
        text = str(item or '').strip().lower().replace(' ', '_').replace('-', '_')
        if text and text not in result:
            result.append(text)
        if len(result) >= 8:
            break
    return result


def _campaign_bestiary_themes(payload: dict, *, title: str, description: str, location: str | None, world) -> list[str]:
    explicit = _theme_list(payload.get('bestiary_themes') or payload.get('bestiaryThemes') or payload.get('themes'))
    if explicit:
        return explicit
    values = [title, description, location, getattr(world, 'name', None), getattr(world, 'description', None)]
    themes: list[str] = []
    for value in values:
        for token in str(value or '').lower().replace('-', ' ').split():
            if len(token) < 4:
                continue
            normalized = token.strip(".,:;!?()[]{}'\"").replace(' ', '_')
            if normalized and normalized not in themes:
                themes.append(normalized)
            if len(themes) >= 6:
                return themes
    return themes or ['campaign']


def _seed_campaign_bestiary(campaign: Campaign, payload: dict, *, world) -> int:
    if not _truthy_enabled(payload.get('seed_bestiary', payload.get('seedBestiary')), default=True):
        return 0
    requested_count = coerce_int(payload.get('bestiary_count') or payload.get('bestiaryCount'))
    count = max(3, min(18, requested_count or 8))
    themes = _campaign_bestiary_themes(
        payload,
        title=campaign.title,
        description=campaign.description or '',
        location=campaign.location,
        world=world,
    )
    creatures = generate_campaign_pack_bestiary(
        {
            'title': campaign.title,
            'themes': themes,
            'count': count,
        }
    )
    for creature in creatures:
        save_bestiary_entry(
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.campaign_id,
            scope='campaign',
            source='campaign_pack',
            persistence='campaign',
            creature=creature,
            tags=creature.get('visualTags') or [],
            created_because='Seeded during campaign creation.',
        )
    return len(creatures)


def _stale_update_error(payload: dict, current_updated_at, *, label: str) -> tuple[dict, int] | None:
    expected = payload.get('expected_updated_at')
    if expected in (None, ''):
        return None
    actual = isoformat(current_updated_at)
    if str(expected) == str(actual):
        return None
    return error_response(
        'stale_update',
        f'{label} was updated by another request. Refresh before saving changes.',
        409,
        {'expected_updated_at': expected, 'actual_updated_at': actual},
    )


def _include_archived() -> bool:
    return str(request.args.get('include_archived', '')).strip().lower() in {'1', 'true', 'yes', 'on'}


def _pagination_limit(default: int = 100, maximum: int = 500) -> int:
    return max(1, min(maximum, coerce_int(request.args.get('limit'), default) or default))


def _optional_limit_arg(name: str, maximum: int = 500) -> int | None:
    if name not in request.args:
        return None
    return max(1, min(maximum, coerce_int(request.args.get(name), maximum) or maximum))


def _active_campaigns_query():
    return campaign_query().filter(or_(Campaign.status.is_(None), Campaign.status != ARCHIVED_STATUS))


def _force_delete_campaign(campaign: Campaign) -> dict:
    campaign_id = campaign.campaign_id
    session_rows = Session.query.filter_by(campaign_id=campaign_id).all()
    session_ids = [session.session_id for session in session_rows]
    detached_player_ids = [
        player.player_id for player in Player.query.filter_by(campaign_id=campaign_id).all()
    ]
    for session_obj in session_rows:
        delete_session_record(session_obj, hard_delete=True)

    Player.query.filter_by(campaign_id=campaign_id).update(
        {Player.campaign_id: None},
        synchronize_session=False,
    )
    CanonJob.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    TurnCanonUpdate.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    TurnEvent.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    CombatDebugEvent.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    CombatEncounter.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    BestiaryEntry.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    DmCoherenceFeedback.query.filter(
        DmCoherenceFeedback.turn_id.in_(
            db.session.query(DmTurn.turn_id).filter_by(campaign_id=campaign_id),
        )
    ).delete(synchronize_session=False)
    DmTurn.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    StoryFact.query.filter_by(campaign_id=campaign_id).update(
        {StoryFact.supersedes_fact_id: None},
        synchronize_session=False,
    )
    StoryFact.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    StoryThread.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    StoryEntity.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    StoryEvent.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    CampaignSegment.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    Map.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    Session.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    db.session.delete(campaign)
    return {
        'deleted': True,
        'campaign_id': campaign_id,
        'archived': False,
        'hard_deleted': True,
        'deleted_session_ids': session_ids,
        'detached_player_ids': detached_player_ids,
    }


def _entity_payload(entity: StoryEntity) -> dict:
    return {
        'entity_id': entity.entity_id,
        'campaign_id': entity.campaign_id,
        'session_id': entity.session_id,
        'entity_type': entity.entity_type,
        'name': entity.name,
        'canonical_name': entity.canonical_name,
        'summary': entity.summary,
        'status': entity.status,
        'aliases': safe_json_loads(entity.aliases_json, []),
        'metadata': safe_json_loads(entity.metadata_json, {}),
        'first_seen_turn_id': entity.first_seen_turn_id,
        'last_seen_turn_id': entity.last_seen_turn_id,
        'created_at': isoformat(entity.created_at),
        'updated_at': isoformat(entity.updated_at),
    }


def _fact_payload(fact: StoryFact) -> dict:
    return {
        'fact_id': fact.fact_id,
        'campaign_id': fact.campaign_id,
        'subject_entity_id': fact.subject_entity_id,
        'subject_name': fact.subject_entity.name if fact.subject_entity else None,
        'predicate': fact.predicate,
        'object_entity_id': fact.object_entity_id,
        'object_name': fact.object_entity.name if fact.object_entity else None,
        'value_text': fact.value_text,
        'value_json': safe_json_loads(fact.value_json, None),
        'fact_status': fact.fact_status,
        'confidence': fact.confidence,
        'source_turn_id': fact.source_turn_id,
        'supersedes_fact_id': fact.supersedes_fact_id,
        'created_at': isoformat(fact.created_at),
    }


def _thread_payload(thread: StoryThread) -> dict:
    return {
        'thread_id': thread.thread_id,
        'campaign_id': thread.campaign_id,
        'title': thread.title,
        'summary': thread.summary,
        'status': thread.status,
        'priority': thread.priority,
        'origin_turn_id': thread.origin_turn_id,
        'last_touched_turn_id': thread.last_touched_turn_id,
        'resolved_turn_id': thread.resolved_turn_id,
        'source': thread.source,
        'metadata': safe_json_loads(thread.metadata_json, {}),
        'created_at': isoformat(thread.created_at),
        'updated_at': isoformat(thread.updated_at),
    }


def _canon_update_payload(update: TurnCanonUpdate) -> dict:
    return {
        'update_id': update.update_id,
        'turn_id': update.turn_id,
        'campaign_id': update.campaign_id,
        'raw_patch': safe_json_loads(update.raw_patch_json, None),
        'applied_patch': safe_json_loads(update.applied_patch_json, None),
        'status': update.status,
        'extractor_model': update.extractor_model,
        'error_text': update.error_text,
        'created_at': isoformat(update.created_at),
    }


@campaigns_bp.route('/import-pack', methods=['POST'])
def import_campaign_pack_manifest():
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    dry_run = _truthy_enabled(
        request.args.get('dry_run')
        or request.args.get('dryRun')
        or payload.get('dry_run')
        or payload.get('dryRun'),
        default=False,
    )
    try:
        result = import_campaign_pack(payload, workspace_id=current_workspace_id(), dry_run=dry_run)
        if dry_run:
            db.session.rollback()
            return jsonify(result.payload), 200
        db.session.commit()
        return jsonify(result.payload), 201
    except CampaignPackImportError as exc:
        db.session.rollback()
        return error_response(exc.error_code, str(exc), exc.status_code)
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to import campaign pack: %s', str(exc))
        return error_response('campaign_pack_import_failed', 'Failed to import campaign pack.', 400)


@campaigns_bp.route('', methods=['POST'])
def create_campaign():
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    required = missing_fields(payload, ['title', 'world_id'])
    if required:
        return error_response('validation_error', 'Missing required fields.', 400, {'missing_fields': required})

    title, title_error = _required_text(
        payload.get('title'),
        max_length=CAMPAIGN_TITLE_MAX_LENGTH,
        field='title',
    )
    if title_error:
        return error_response('validation_error', title_error, 400)

    world_id = coerce_int(payload.get('world_id'))
    if world_id is None or world_id < 1:
        return error_response('validation_error', 'Campaign title and a valid world ID are required.', 400)
    description, description_error = _optional_text(
        payload.get('description', ''),
        max_length=CAMPAIGN_TEXT_MAX_LENGTH,
        field='description',
    )
    if description_error:
        return error_response('validation_error', description_error, 400)
    current_quest, current_quest_error = _optional_text(
        payload.get('current_quest'),
        max_length=CAMPAIGN_TEXT_MAX_LENGTH,
        field='current_quest',
        default=None,
    )
    if current_quest_error:
        return error_response('validation_error', current_quest_error, 400)
    location, location_error = _optional_text(
        payload.get('location'),
        max_length=CAMPAIGN_TEXT_MAX_LENGTH,
        field='location',
        default=None,
    )
    if location_error:
        return error_response('validation_error', location_error, 400)

    world = workspace_world(world_id)
    if not world:
        return error_response('world_not_found', 'World not found.', 404)

    try:
        new_campaign = Campaign(
            workspace_id=current_workspace_id(),
            title=title,
            description=description,
            world_id=world_id,
            current_quest=current_quest,
            location=location,
        )
        db.session.add(new_campaign)
        db.session.flush()
        seeded_bestiary_count = _seed_campaign_bestiary(new_campaign, payload, world=world)
        db.session.commit()
        response_payload = campaign_payload(new_campaign)
        response_payload['bestiary_seeded_count'] = seeded_bestiary_count
        return jsonify(response_payload), 201
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to create campaign: %s', str(exc))
        return error_response('campaign_create_failed', 'Failed to create campaign.', 400)


@campaigns_bp.route('', methods=['GET'])
def list_campaigns():
    query = campaign_query() if _include_archived() else _active_campaigns_query()
    before_id = coerce_int(request.args.get('before_id'))
    if before_id is not None:
        query = query.filter(Campaign.campaign_id < before_id)
    limit = _optional_limit_arg('limit')
    query = query.order_by(Campaign.updated_at.desc(), Campaign.created_at.desc(), Campaign.campaign_id.desc())
    campaigns = limited_page(query, limit=limit)
    payloads = campaign_payloads(list(campaigns))
    response = jsonify(payloads)
    response.headers['X-AIDM-Has-More'] = 'true' if campaigns._has_more else 'false'
    if campaigns._has_more and campaigns:
        response.headers['X-AIDM-Next-Cursor'] = str(campaigns[-1].campaign_id)
    return response


@campaigns_bp.route('/<int:campaign_id>', methods=['GET'])
def get_campaign(campaign_id):
    campaign = workspace_campaign(campaign_id)
    if not campaign:
        return error_response('campaign_not_found', 'Campaign not found.', 404)

    return jsonify(campaign_payload(campaign))


@campaigns_bp.route('/<int:campaign_id>', methods=['PATCH'])
def update_campaign(campaign_id):
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    campaign = workspace_campaign(campaign_id)
    if not campaign:
        return error_response('campaign_not_found', 'Campaign not found.', 404)

    stale_response = _stale_update_error(payload, campaign.updated_at, label='Campaign')
    if stale_response:
        return stale_response

    allowed_fields = {'title', 'description', 'current_quest', 'location', 'world_id'}
    if not any(field in payload for field in allowed_fields):
        return error_response('validation_error', 'No supported campaign fields were provided.', 400)

    try:
        if 'title' in payload:
            title, title_error = _required_text(
                payload.get('title'),
                max_length=CAMPAIGN_TITLE_MAX_LENGTH,
                field='title',
            )
            if title_error:
                return error_response('validation_error', title_error, 400)
            campaign.title = title

        if 'description' in payload:
            description, description_error = _optional_text(
                payload.get('description'),
                max_length=CAMPAIGN_TEXT_MAX_LENGTH,
                field='description',
            )
            if description_error:
                return error_response('validation_error', description_error, 400)
            campaign.description = description

        if 'current_quest' in payload:
            current_quest, current_quest_error = _optional_text(
                payload.get('current_quest'),
                max_length=CAMPAIGN_TEXT_MAX_LENGTH,
                field='current_quest',
                default=None,
            )
            if current_quest_error:
                return error_response('validation_error', current_quest_error, 400)
            campaign.current_quest = current_quest

        if 'location' in payload:
            location, location_error = _optional_text(
                payload.get('location'),
                max_length=CAMPAIGN_TEXT_MAX_LENGTH,
                field='location',
                default=None,
            )
            if location_error:
                return error_response('validation_error', location_error, 400)
            campaign.location = location

        if 'world_id' in payload:
            world_id = coerce_int(payload.get('world_id'))
            if world_id is None or world_id < 1:
                return error_response('validation_error', 'A valid world ID is required.', 400)
            world = workspace_world(world_id)
            if not world:
                return error_response('world_not_found', 'World not found.', 404)
            campaign.world_id = world_id

        campaign.updated_at = utc_now()
        db.session.commit()
        return jsonify(campaign_payload(campaign))
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to update campaign: %s', str(exc))
        return error_response('campaign_update_failed', 'Failed to update campaign.', 400)


@campaigns_bp.route('/<int:campaign_id>/archive', methods=['POST'])
def archive_campaign(campaign_id):
    campaign = workspace_campaign(campaign_id)
    if not campaign:
        return error_response('campaign_not_found', 'Campaign not found.', 404)

    try:
        campaign.status = ARCHIVED_STATUS
        campaign.updated_at = utc_now()
        Session.query.filter(
            Session.campaign_id == campaign_id,
            or_(Session.status.is_(None), Session.status != ARCHIVED_STATUS),
        ).update(
            {
                Session.status: ARCHIVED_STATUS,
                Session.deleted_at: campaign.updated_at,
                Session.updated_at: campaign.updated_at,
                Session.archived_by_campaign_id: campaign_id,
            },
            synchronize_session=False,
        )
        db.session.commit()
        return jsonify({'archived': True, 'campaign': campaign_payload(campaign)})
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to archive campaign: %s', str(exc))
        return error_response('campaign_archive_failed', 'Failed to archive campaign.', 400)


@campaigns_bp.route('/<int:campaign_id>/restore', methods=['POST'])
def restore_campaign(campaign_id):
    campaign = workspace_campaign(campaign_id)
    if not campaign:
        return error_response('campaign_not_found', 'Campaign not found.', 404)

    try:
        campaign.status = ACTIVE_STATUS
        campaign.updated_at = utc_now()
        Session.query.filter_by(campaign_id=campaign_id, archived_by_campaign_id=campaign_id).update(
            {
                Session.status: ACTIVE_STATUS,
                Session.deleted_at: None,
                Session.updated_at: campaign.updated_at,
                Session.archived_by_campaign_id: None,
            },
            synchronize_session=False,
        )
        db.session.commit()
        return jsonify({'restored': True, 'campaign': campaign_payload(campaign)})
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to restore campaign: %s', str(exc))
        return error_response('campaign_restore_failed', 'Failed to restore campaign.', 400)


@campaigns_bp.route('/<int:campaign_id>', methods=['DELETE'])
def delete_campaign(campaign_id):
    hard_delete = str(request.args.get('hard', '')).strip().lower() in {'1', 'true', 'yes', 'on'}
    force_delete = str(request.args.get('force', '')).strip().lower() in {'1', 'true', 'yes', 'on'}
    campaign = workspace_campaign(campaign_id)
    if not campaign:
        return error_response('campaign_not_found', 'Campaign not found.', 404)

    if hard_delete:
        session_count = db.session.query(func.count(Session.session_id)).filter_by(campaign_id=campaign_id).scalar() or 0
        if session_count and not force_delete:
            return error_response(
                'campaign_has_sessions',
                'Hard deleting a campaign with sessions is not supported. Archive it instead.',
                409,
                {'session_count': int(session_count)},
            )
        try:
            if force_delete:
                payload = _force_delete_campaign(campaign)
            else:
                detached_player_ids = [
                    player.player_id for player in Player.query.filter_by(campaign_id=campaign_id).all()
                ]
                Player.query.filter_by(campaign_id=campaign_id).update(
                    {Player.campaign_id: None},
                    synchronize_session=False,
                )
                CombatDebugEvent.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
                CombatEncounter.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
                BestiaryEntry.query.filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
                payload = {
                    'deleted': True,
                    'campaign_id': campaign_id,
                    'archived': False,
                    'hard_deleted': True,
                    'deleted_session_ids': [],
                    'detached_player_ids': detached_player_ids,
                }
            if not force_delete:
                db.session.delete(campaign)
            db.session.commit()
            return jsonify(payload)
        except Exception as exc:
            db.session.rollback()
            logger.error('Failed to hard delete campaign: %s', str(exc))
            return error_response('campaign_delete_failed', 'Failed to delete campaign.', 400)

    return archive_campaign(campaign_id)


@campaigns_bp.route('/<int:campaign_id>/workspace', methods=['GET'])
def get_campaign_workspace(campaign_id):
    campaign = workspace_campaign(campaign_id)
    if not campaign:
        return error_response('campaign_not_found', 'Campaign not found.', 404)
    if campaign_is_archived(campaign) and not _include_archived():
        return error_response('campaign_not_found', 'Campaign not found.', 404)

    return jsonify(
        campaign_workspace_payload(
            campaign,
            include_archived=_include_archived(),
            session_limit=_optional_limit_arg('session_limit'),
            player_limit=_optional_limit_arg('player_limit'),
            map_limit=_optional_limit_arg('map_limit'),
            segment_limit=_optional_limit_arg('segment_limit'),
        )
    )


@campaigns_bp.route('/<int:campaign_id>/canon', methods=['GET'])
def get_campaign_canon(campaign_id):
    campaign = workspace_campaign(campaign_id)
    if not campaign:
        return error_response('campaign_not_found', 'Campaign not found.', 404)

    limit = _pagination_limit(default=100, maximum=500)
    entity_before = coerce_int(request.args.get('entity_before_id'))
    fact_before = coerce_int(request.args.get('fact_before_id'))
    thread_before = coerce_int(request.args.get('thread_before_id'))
    update_before = coerce_int(request.args.get('update_before_id'))

    entities_query = StoryEntity.query.filter_by(campaign_id=campaign_id)
    if entity_before is not None:
        entities_query = entities_query.filter(StoryEntity.entity_id < entity_before)
    entities = entities_query.order_by(StoryEntity.entity_id.desc()).limit(limit + 1).all()

    facts_query = StoryFact.query.options(
        joinedload(StoryFact.subject_entity),
        joinedload(StoryFact.object_entity),
    ).filter_by(campaign_id=campaign_id)
    if fact_before is not None:
        facts_query = facts_query.filter(StoryFact.fact_id < fact_before)
    facts = facts_query.order_by(StoryFact.fact_id.desc()).limit(limit + 1).all()

    threads_query = StoryThread.query.filter_by(campaign_id=campaign_id)
    if thread_before is not None:
        threads_query = threads_query.filter(StoryThread.thread_id < thread_before)
    threads = threads_query.order_by(StoryThread.thread_id.desc()).limit(limit + 1).all()

    updates_query = TurnCanonUpdate.query.filter_by(campaign_id=campaign_id)
    if update_before is not None:
        updates_query = updates_query.filter(TurnCanonUpdate.update_id < update_before)
    updates = updates_query.order_by(TurnCanonUpdate.update_id.desc()).limit(limit + 1).all()

    has_more = {
        'entities': len(entities) > limit,
        'facts': len(facts) > limit,
        'threads': len(threads) > limit,
        'updates': len(updates) > limit,
    }
    entities = entities[:limit]
    facts = facts[:limit]
    threads = threads[:limit]
    updates = updates[:limit]

    return jsonify(
        {
            'campaign_id': campaign_id,
            'entities': [_entity_payload(entity) for entity in entities],
            'facts': [_fact_payload(fact) for fact in facts],
            'threads': [_thread_payload(thread) for thread in threads],
            'updates': [_canon_update_payload(update) for update in updates],
            'limit': limit,
            'has_more': has_more,
            'next_cursor': {
                'entities': entities[-1].entity_id if has_more['entities'] and entities else None,
                'facts': facts[-1].fact_id if has_more['facts'] and facts else None,
                'threads': threads[-1].thread_id if has_more['threads'] and threads else None,
                'updates': updates[-1].update_id if has_more['updates'] and updates else None,
            },
            'summary': {
                'entity_count': StoryEntity.query.filter_by(campaign_id=campaign_id).count(),
                'fact_count': StoryFact.query.filter_by(campaign_id=campaign_id).count(),
                'thread_count': StoryThread.query.filter_by(campaign_id=campaign_id).count(),
                'update_count': TurnCanonUpdate.query.filter_by(campaign_id=campaign_id).count(),
                'canon_job_counts': canon_job_status_counts(campaign_id),
            },
        }
    )
