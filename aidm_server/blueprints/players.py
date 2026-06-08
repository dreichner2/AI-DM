from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from aidm_server.canon_inventory import inventory_payload
from aidm_server.character_state import serialize_stats_payload
from aidm_server.database import db
from aidm_server.errors import error_response
from aidm_server.models import DmTurn, Player, PlayerAction, TurnEvent, safe_json_dumps
from aidm_server.pagination import jsonify_page, limited_page
from aidm_server.response_dtos import player_detail_payload, player_summary_payload
from aidm_server.validation import (
    coerce_int,
    missing_fields,
    optional_text as _optional_text,
    parse_json_body,
    required_text as _required_text,
)
from aidm_server.workspace_access import (
    current_workspace_id,
    get_campaign as workspace_campaign,
    get_player as workspace_player,
)


logger = logging.getLogger(__name__)
players_bp = Blueprint('players', __name__)


def _structured_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return safe_json_dumps(value, {})


@players_bp.route('/campaigns/<int:campaign_id>/players', methods=['GET', 'POST'])
def handle_players(campaign_id):
    if request.method == 'POST':
        return add_player(campaign_id)
    return get_players(campaign_id)


def add_player(campaign_id):
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    required = missing_fields(payload, ['name', 'character_name'])
    if required:
        return error_response('validation_error', 'Missing required fields.', 400, {'missing_fields': required})

    campaign = workspace_campaign(campaign_id)
    if not campaign:
        return error_response('campaign_not_found', 'Campaign not found.', 404)

    name, name_error = _required_text(payload.get('name'), max_length=80, field='name')
    if name_error:
        return error_response('validation_error', name_error, 400)
    character_name, character_name_error = _required_text(
        payload.get('character_name'),
        max_length=80,
        field='character_name',
    )
    if character_name_error:
        return error_response('validation_error', character_name_error, 400)
    race, race_error = _optional_text(payload.get('race', ''), max_length=80, field='race')
    if race_error:
        return error_response('validation_error', race_error, 400)
    sex, sex_error = _optional_text(payload.get('sex', ''), max_length=40, field='sex')
    if sex_error:
        return error_response('validation_error', sex_error, 400)
    sex = sex or 'male'
    class_name, class_error = _optional_text(
        payload.get('char_class', payload.get('class_', '')),
        max_length=80,
        field='class',
    )
    if class_error:
        return error_response('validation_error', class_error, 400)
    level = coerce_int(payload.get('level'), 1)
    if level is None or level < 1 or level > 20:
        return error_response('validation_error', 'level must be an integer from 1 to 20.', 400)

    try:
        stats_payload, stats_error = serialize_stats_payload(payload.get('stats'), level=level)
        if stats_error:
            return error_response('validation_error', stats_error, 400)

        raw_inventory = payload.get('inventory')
        new_player = Player(
            workspace_id=current_workspace_id(),
            campaign_id=campaign_id,
            name=name,
            character_name=character_name,
            race=race,
            sex=sex,
            class_=class_name,
            level=level,
            stats=stats_payload,
            inventory=(safe_json_dumps(inventory_payload(raw_inventory), []) if raw_inventory is not None else None),
            character_sheet=_structured_text(payload.get('character_sheet')),
        )
        db.session.add(new_player)
        db.session.commit()
        return jsonify({'player_id': new_player.player_id, 'message': 'Player successfully created'}), 201
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to create player: %s', str(exc))
        return error_response('player_create_failed', 'Failed to create player.', 400)


def get_players(campaign_id):
    campaign = workspace_campaign(campaign_id)
    if not campaign:
        return error_response('campaign_not_found', 'Campaign not found.', 404)

    before_id = coerce_int(request.args.get('before_id'))
    limit = coerce_int(request.args.get('limit'))
    query = Player.query.filter_by(workspace_id=current_workspace_id())
    if before_id is not None:
        query = query.filter(Player.player_id < before_id)
    query = query.order_by(Player.created_at.asc(), Player.player_id.asc())
    players = limited_page(query, limit=limit)
    return jsonify_page(players, payload_for=player_summary_payload, cursor_for=lambda player: player.player_id)


@players_bp.route('/<int:player_id>', methods=['GET'])
def get_player_by_id(player_id):
    player = workspace_player(player_id)
    if not player:
        return error_response('player_not_found', 'Player not found.', 404)

    return jsonify(player_detail_payload(player))


@players_bp.route('/<int:player_id>', methods=['PATCH'])
def update_player(player_id):
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    player = workspace_player(player_id)
    if not player:
        return error_response('player_not_found', 'Player not found.', 404)

    text_fields = {
        'name': (80, True),
        'character_name': (80, True),
        'race': (80, False),
        'sex': (40, False),
    }
    try:
        for field, (max_length, required) in text_fields.items():
            if field not in payload:
                continue
            if required:
                value, error = _required_text(payload.get(field), max_length=max_length, field=field)
            else:
                value, error = _optional_text(payload.get(field), max_length=max_length, field=field)
            if error:
                return error_response('validation_error', error, 400)
            if field == 'sex' and not value:
                value = 'male'
            setattr(player, field, value)

        if 'class_' in payload or 'char_class' in payload:
            value, error = _optional_text(
                payload.get('char_class', payload.get('class_')),
                max_length=80,
                field='class',
            )
            if error:
                return error_response('validation_error', error, 400)
            player.class_ = value

        if 'level' in payload:
            level = coerce_int(payload.get('level'))
            if level is None or level < 1 or level > 20:
                return error_response('validation_error', 'level must be an integer from 1 to 20.', 400)
            player.level = level

        if 'stats' in payload:
            stats_payload, stats_error = serialize_stats_payload(payload.get('stats'), level=player.level or 1)
            if stats_error:
                return error_response('validation_error', stats_error, 400)
            player.stats = stats_payload
        if 'character_sheet' in payload:
            player.character_sheet = _structured_text(payload.get('character_sheet'))
        if 'inventory' in payload:
            player.inventory = safe_json_dumps(inventory_payload(payload.get('inventory')), [])

        db.session.commit()
        return jsonify(player_detail_payload(player))
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to update player: %s', str(exc))
        return error_response('player_update_failed', 'Failed to update player.', 400)


@players_bp.route('/<int:player_id>', methods=['DELETE'])
def delete_player(player_id):
    player = workspace_player(player_id)
    if not player:
        return error_response('player_not_found', 'Player not found.', 404)

    campaign_id = player.campaign_id
    try:
        PlayerAction.query.filter_by(player_id=player_id).delete(synchronize_session=False)
        DmTurn.query.filter_by(player_id=player_id).update({DmTurn.player_id: None}, synchronize_session=False)
        TurnEvent.query.filter_by(player_id=player_id).update({TurnEvent.player_id: None}, synchronize_session=False)
        db.session.delete(player)
        db.session.commit()
        return jsonify({'deleted': True, 'player_id': player_id, 'campaign_id': campaign_id})
    except Exception as exc:
        db.session.rollback()
        logger.error('Failed to delete player: %s', str(exc))
        return error_response('player_delete_failed', 'Failed to delete player.', 400)
