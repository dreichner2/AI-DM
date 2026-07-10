from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request

from aidm_server.database import db
from aidm_server.errors import error_response
from aidm_server.services.campaign_pack import CampaignPackImportError
from aidm_server.services.play_now import PlayNowOnboardingError, ensure_play_now_adventure
from aidm_server.services.pregen_characters import (
    default_pregenerated_character_id,
    list_pregenerated_character_payloads,
)
from aidm_server.validation import parse_optional_json_body
from aidm_server.workspace_access import current_account_id, current_workspace_id


logger = logging.getLogger(__name__)
onboarding_bp = Blueprint('onboarding', __name__)


@onboarding_bp.route('/pregenerated-characters', methods=['GET'])
def list_pregenerated_characters():
    characters = list_pregenerated_character_payloads()
    return jsonify(
        {
            'characters': characters,
            'count': len(characters),
            'default_character_id': default_pregenerated_character_id(),
        }
    )


@onboarding_bp.route('/play-now', methods=['POST'])
def play_now():
    if current_app.config.get('AIDM_AUTH_REQUIRED'):
        return error_response(
            'play_now_auth_required',
            'Play Now onboarding is only available when authentication is disabled.',
            403,
        )

    payload = parse_optional_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    character_id = (
        payload.get('character_id')
        or payload.get('characterId')
        or payload.get('pregen_id')
        or payload.get('pregenId')
    )
    example_pack_id = (
        payload.get('example_pack_id')
        or payload.get('examplePackId')
        or payload.get('pack_id')
        or payload.get('packId')
    )

    try:
        result = ensure_play_now_adventure(
            workspace_id=current_workspace_id(),
            account_id=current_account_id(),
            character_id=character_id,
            example_pack_id=example_pack_id,
        )
        db.session.commit()
        return jsonify(result.payload), result.status_code
    except PlayNowOnboardingError as exc:
        db.session.rollback()
        return error_response(exc.error_code, exc.public_message, exc.status_code)
    except CampaignPackImportError as exc:
        db.session.rollback()
        return error_response(exc.error_code, exc.public_message, exc.status_code)
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to prepare Play Now onboarding: %s', str(exc))
        return error_response('play_now_failed', 'Failed to prepare Play Now.', 400)
