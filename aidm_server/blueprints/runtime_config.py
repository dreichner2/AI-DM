from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from aidm_server.auth import DEFAULT_WORKSPACE_ID
from aidm_server.errors import error_response
from aidm_server.services.runtime_config import (
    RuntimeConfigError,
    apply_llm_runtime,
    llm_config_payload,
    llm_config_persistence_allowed,
    provider_configured,
    validate_provider_model,
)
from aidm_server.telemetry import telemetry_metric
from aidm_server.validation import coerce_bool, parse_json_body
from aidm_server.workspace_access import current_workspace_id

runtime_config_bp = Blueprint('runtime_config', __name__)


def _llm_config_update_authorized() -> bool:
    if not bool(current_app.config.get('AIDM_AUTH_REQUIRED', False)):
        return True
    if str(getattr(g, 'aidm_workspace_id', '') or '') != DEFAULT_WORKSPACE_ID:
        return False
    if getattr(g, 'aidm_account_id', None):
        return bool(getattr(g, 'aidm_workspace_admin', False))
    return True


@runtime_config_bp.route('/llm/config', methods=['GET'])
def llm_config():
    telemetry_metric('runtime_config.llm_config.requests_total', 1)
    return jsonify(llm_config_payload(workspace_id=current_workspace_id()))


@runtime_config_bp.route('/llm/config', methods=['PATCH', 'POST'])
def update_llm_config():
    telemetry_metric('runtime_config.llm_config_updates.requests_total', 1)
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    if not _llm_config_update_authorized():
        telemetry_metric('runtime_config.llm_config_updates.denied_total', 1)
        return error_response(
            'runtime_config_admin_required',
            'Only the owner table admin can change global LLM runtime config.',
            403,
        )

    persist = coerce_bool(payload.get('persist'), True)
    if persist is None:
        return error_response('validation_error', 'persist must be a boolean value.', 400)
    if persist and not llm_config_persistence_allowed():
        return error_response(
            'llm_config_persist_disabled',
            'Persisting LLM config from the API is disabled outside local/test environments.',
            403,
        )

    try:
        provider, model = validate_provider_model(payload.get('provider'), payload.get('model'))
    except RuntimeConfigError as exc:
        return error_response(exc.error_code, exc.message, exc.status_code, exc.details)

    if not provider_configured(provider):
        return error_response(
            'provider_not_configured',
            f'Provider "{provider}" is missing its API key.',
            400,
        )

    runtime_changed = apply_llm_runtime(provider, model, persist=persist)
    response = llm_config_payload(
        workspace_id=current_workspace_id(),
        runtime_change_applied=runtime_changed,
    )
    response['persisted'] = persist
    return jsonify(response)
