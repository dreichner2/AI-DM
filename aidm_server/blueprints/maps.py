from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request
from sqlalchemy import and_, or_

from aidm_server.capabilities import current_actor_has_capability
from aidm_server.database import db
from aidm_server.errors import error_response
from aidm_server.map_visibility import (
    MAP_VISIBILITY_PLAYER,
    MAP_VISIBILITY_VALUES,
    normalize_map_visibility,
    visible_maps_query,
)
from aidm_server.models import Campaign, Map, World, safe_json_dumps
from aidm_server.pagination import jsonify_page, limited_page
from aidm_server.response_dtos import map_payload
from aidm_server.validation import coerce_int, json_object, optional_text, parse_json_body, positive_int, required_text
from aidm_server.workspace_access import (
    current_workspace_id,
    get_campaign as workspace_campaign,
    get_campaign_map,
    get_world as workspace_world,
)


logger = logging.getLogger(__name__)
maps_bp = Blueprint('maps', __name__)
MAP_TITLE_MAX_LENGTH = 120
MAP_TEXT_MAX_LENGTH = 2000


def _include_dm_only_maps() -> bool:
    return current_actor_has_capability('dm_authoring')


def _map_visibility(value, *, default: str | None = None):
    visibility = normalize_map_visibility(value, default=default)
    if visibility is None:
        allowed = ', '.join(sorted(MAP_VISIBILITY_VALUES))
        return None, f'visibility must be one of: {allowed}.'
    return visibility, None


@maps_bp.route('', methods=['POST'])
def create_map():
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    title, title_error = required_text(payload.get('title'), max_length=MAP_TITLE_MAX_LENGTH, field='title')
    if title_error:
        return error_response('validation_error', title_error, 400)
    description, description_error = optional_text(
        payload.get('description', ''),
        max_length=MAP_TEXT_MAX_LENGTH,
        field='description',
    )
    if description_error:
        return error_response('validation_error', description_error, 400)
    world_id, world_id_error = positive_int(payload.get('world_id'), field='world_id')
    if world_id_error:
        return error_response('validation_error', world_id_error, 400)
    campaign_id, campaign_id_error = positive_int(payload.get('campaign_id'), field='campaign_id')
    if campaign_id_error:
        return error_response('validation_error', campaign_id_error, 400)
    map_data, map_data_error = json_object(payload.get('map_data'), field='map_data', default={})
    if map_data_error:
        return error_response('validation_error', map_data_error, 400)
    visibility, visibility_error = _map_visibility(
        payload.get('visibility'),
        default=MAP_VISIBILITY_PLAYER,
    )
    if visibility_error:
        return error_response('validation_error', visibility_error, 400)
    if world_id is None and campaign_id is None:
        return error_response('validation_error', 'Map requires either world_id or campaign_id.', 400)

    world = workspace_world(world_id) if world_id is not None else None
    campaign = workspace_campaign(campaign_id) if campaign_id is not None else None

    if world_id is not None and not world:
        return error_response('world_not_found', 'World not found.', 404)
    if campaign_id is not None and not campaign:
        return error_response('campaign_not_found', 'Campaign not found.', 404)
    if world and campaign and campaign.world_id != world.world_id:
        return error_response(
            'campaign_world_mismatch',
            'Campaign does not belong to the provided world.',
            400,
        )
    if campaign and world_id is None:
        world_id = campaign.world_id

    try:
        new_map = Map(
            world_id=world_id,
            campaign_id=campaign_id,
            title=title,
            description=description,
            map_data=safe_json_dumps(map_data, {}),
            visibility=visibility,
        )
        db.session.add(new_map)
        db.session.commit()
        return jsonify({'map_id': new_map.map_id}), 201
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to create map: %s', str(exc))
        return error_response('map_create_failed', 'Failed to create map.', 400)


@maps_bp.route('', methods=['GET'])
def list_maps():
    world_id = request.args.get('world_id', type=int)
    campaign_id = request.args.get('campaign_id', type=int)
    before_id = coerce_int(request.args.get('before_id'))
    limit = coerce_int(request.args.get('limit'))

    query = Map.query.outerjoin(Campaign, Map.campaign_id == Campaign.campaign_id).outerjoin(
        World,
        Map.world_id == World.world_id,
    )
    workspace_id = current_workspace_id()
    query = query.filter(
        or_(
            Campaign.workspace_id == workspace_id,
            and_(Map.campaign_id.is_(None), World.workspace_id == workspace_id),
        )
    )
    query = visible_maps_query(query, include_dm_only=_include_dm_only_maps())
    if world_id is not None:
        world = workspace_world(world_id)
        if not world:
            return error_response('world_not_found', 'World not found.', 404)
        query = query.filter(Map.world_id == world_id)
    if campaign_id is not None:
        campaign = workspace_campaign(campaign_id)
        if not campaign:
            return error_response('campaign_not_found', 'Campaign not found.', 404)
        query = query.filter(Map.campaign_id == campaign_id)
    if before_id is not None:
        query = query.filter(Map.map_id < before_id)
    query = query.order_by(Map.created_at.desc(), Map.map_id.desc())
    maps = limited_page(query, limit=limit)
    return jsonify_page(maps, payload_for=map_payload, cursor_for=lambda map_obj: map_obj.map_id)


@maps_bp.route('/<int:map_id>', methods=['GET'])
def get_map(map_id):
    map_obj = get_campaign_map(map_id, include_dm_only=_include_dm_only_maps())
    if not map_obj:
        return error_response('map_not_found', 'Map not found.', 404)

    return jsonify(map_payload(map_obj))


@maps_bp.route('/<int:map_id>', methods=['PUT', 'PATCH'])
def update_map(map_id):
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    map_obj = get_campaign_map(map_id)
    if not map_obj:
        return error_response('map_not_found', 'Map not found.', 404)

    try:
        if 'title' in payload:
            title, title_error = required_text(payload.get('title'), max_length=MAP_TITLE_MAX_LENGTH, field='title')
            if title_error:
                return error_response('validation_error', title_error, 400)
            map_obj.title = title
        if 'description' in payload:
            description, description_error = optional_text(
                payload.get('description'),
                max_length=MAP_TEXT_MAX_LENGTH,
                field='description',
            )
            if description_error:
                return error_response('validation_error', description_error, 400)
            map_obj.description = description
        if 'map_data' in payload:
            map_data, map_data_error = json_object(payload['map_data'], field='map_data', default={})
            if map_data_error:
                return error_response('validation_error', map_data_error, 400)
            map_obj.map_data = safe_json_dumps(map_data, {})
        if 'visibility' in payload:
            visibility, visibility_error = _map_visibility(payload.get('visibility'))
            if visibility_error:
                return error_response('validation_error', visibility_error, 400)
            map_obj.visibility = visibility
        db.session.commit()
        return jsonify({'message': 'Map updated successfully'}), 200
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to update map: %s', str(exc))
        return error_response('map_update_failed', 'Failed to update map.', 400)
