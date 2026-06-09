from __future__ import annotations

import json
import logging
import os
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from aidm_server.contracts import ProviderRequest
from aidm_server.database import db
from aidm_server.errors import error_response
from aidm_server.game_state.extraction.schemas import extract_json_object
from aidm_server.llm_providers import get_helper_provider
from aidm_server.models import CustomRace, safe_json_dumps, safe_json_loads
from aidm_server.race_system import (
    CUSTOM_RACE_APPROVAL_STATUSES,
    CURATED_RACE_BY_ID,
    TRAIT_COST_GUIDE,
    analyze_race_balance,
    approval_status_for_balance,
    curated_races,
    generate_custom_race_draft,
    normalize_race_definition,
    race_summary,
)
from aidm_server.services.runtime_config import provider_configured
from aidm_server.telemetry import telemetry_event, telemetry_metric
from aidm_server.validation import parse_json_body
from aidm_server.workspace_access import current_workspace_id


logger = logging.getLogger(__name__)
races_bp = Blueprint('races', __name__)
CUSTOM_RACE_SYSTEM_MESSAGE = (
    'You are a race metadata generator for an AI tabletop RPG. '
    'Convert custom race ideas into balanced structured metadata. Return JSON only.'
)


def _helper_provider_name() -> str:
    return str(
        current_app.config.get('AIDM_HELPER_LLM_PROVIDER')
        or os.getenv('AIDM_HELPER_LLM_PROVIDER')
        or 'deepseek'
    ).strip().lower()


def _custom_race_helper_configured(provider_name: str) -> bool:
    if provider_name == 'deepseek':
        return bool(
            current_app.config.get('AIDM_HELPER_DEEPSEEK_API_KEY')
            or os.getenv('AIDM_HELPER_DEEPSEEK_API_KEY')
            or provider_configured('deepseek')
        )
    if provider_name in {'nvidia', 'kimi'}:
        return bool(
            current_app.config.get('AIDM_HELPER_NVIDIA_API_KEY')
            or os.getenv('AIDM_HELPER_NVIDIA_API_KEY')
            or provider_configured(provider_name)
        )
    if provider_name == 'gemini':
        return provider_configured('gemini')
    if provider_name == 'fallback':
        return True
    return False


def _custom_race_helper_enabled() -> bool:
    if current_app.config.get('TESTING'):
        return False
    setting = str(
        current_app.config.get('AIDM_CUSTOM_RACE_HELPER_ENABLED')
        or os.getenv('AIDM_CUSTOM_RACE_HELPER_ENABLED')
        or 'auto'
    ).strip().lower()
    if setting in {'0', 'false', 'no', 'off', 'disabled'}:
        return False
    if setting in {'1', 'true', 'yes', 'on', 'enabled'}:
        return True
    return _custom_race_helper_configured(_helper_provider_name())


def _build_custom_race_prompt(user_prompt: str, strictness: str) -> str:
    return (
        'Convert the user custom race idea into a RaceDefinition JSON draft.\n\n'
        'Rules:\n'
        '- Do not create overpowered races.\n'
        '- Prefer 2-3 meaningful traits.\n'
        '- Use a standard race balance budget of 5 points.\n'
        '- Include at least one narrative hook.\n'
        '- Include visual metadata.\n'
        '- Include AI narration hints.\n'
        '- If the user requests extreme powers, scale them down into balanced versions.\n'
        '- Do not create immunity unless explicitly approved.\n'
        '- Prefer resistance over immunity.\n'
        '- Prefer once-per-rest abilities over unlimited abilities.\n'
        '- Prefer short-range utility over unrestricted teleportation.\n'
        '- Use only supported tags, trait categories, cooldowns, action types, and damage types from the schema.\n'
        '- Return JSON only, with no markdown fences.\n\n'
        f'Strictness: {strictness}\n\n'
        f'Trait cost guide:\n{json.dumps(TRAIT_COST_GUIDE, separators=(",", ":"))}\n\n'
        f'User custom race idea:\n{user_prompt}\n\n'
        'Return a RaceDefinition draft.'
    )


def _race_payload_from_helper(payload: dict[str, Any], prompt: str) -> dict[str, Any]:
    candidate = payload.get('draftRace') if isinstance(payload.get('draftRace'), dict) else payload
    merged = {
        'source': 'custom',
        'descriptionLong': prompt,
        **candidate,
    }
    race = normalize_race_definition(merged, source='custom')
    race['balance'] = analyze_race_balance(race)
    race['approvalStatus'] = approval_status_for_balance(race['balance'])
    return race


def _generate_custom_race_draft(prompt: str, *, strictness: str) -> tuple[dict[str, Any], str]:
    fallback = generate_custom_race_draft(prompt, strictness=strictness)
    if not _custom_race_helper_enabled():
        return fallback, 'deterministic'

    try:
        response = get_helper_provider().generate(
            ProviderRequest(
                prompt=_build_custom_race_prompt(prompt, strictness),
                system_message=CUSTOM_RACE_SYSTEM_MESSAGE,
            )
        )
        payload = extract_json_object(response.text)
        if not payload:
            raise ValueError('helper returned invalid JSON')
        draft = _race_payload_from_helper(payload, prompt)
        telemetry_metric('race.custom_helper.success_total', 1, tags={'model': response.model})
        return draft, response.model
    except Exception as exc:
        telemetry_event(
            'race.custom_helper.failed',
            payload={'error': str(exc)[:300]},
            severity='warning',
        )
        return fallback, 'deterministic_fallback'


def _definition_from_custom(row: CustomRace) -> dict:
    definition = safe_json_loads(row.race_definition, {})
    if not isinstance(definition, dict):
        definition = {}
    try:
        normalized = normalize_race_definition(definition, source='custom')
    except ValueError:
        normalized = {
            'id': row.race_id,
            'version': row.version,
            'name': row.name,
            'source': 'custom',
            'descriptionShort': 'Custom race metadata could not be parsed.',
            'descriptionLong': 'This custom race should be edited before use.',
            'aliases': [row.name.lower()],
            'tags': ['exotic'],
            'size': 'medium',
            'baseSpeed': 30,
            'visual': {
                'portraitKey': 'human',
                'iconKey': 'custom',
                'bodyType': 'custom',
                'commonFeatures': [row.name],
            },
            'traits': [],
            'aiNarrationHints': ['Do not assume mechanics for this custom race until it is repaired.'],
            'roleplayHooks': [],
            'recommendedClasses': [],
            'difficulty': 'medium',
            'balance': {'budget': 5, 'spent': 0, 'tier': 'weak'},
        }
    normalized['id'] = row.race_id
    normalized['version'] = row.version
    normalized['approvalStatus'] = row.approval_status
    return normalized


def _latest_custom_races() -> list[dict]:
    rows = (
        CustomRace.query.filter_by(workspace_id=current_workspace_id())
        .order_by(CustomRace.race_id.asc(), CustomRace.version.desc())
        .all()
    )
    seen: set[str] = set()
    races: list[dict] = []
    for row in rows:
        if row.race_id in seen:
            continue
        seen.add(row.race_id)
        races.append(_definition_from_custom(row))
    return races


def _latest_custom_row(race_id: str) -> CustomRace | None:
    return (
        CustomRace.query.filter_by(workspace_id=current_workspace_id(), race_id=race_id)
        .order_by(CustomRace.version.desc())
        .first()
    )


@races_bp.get('/races')
def list_races():
    all_races = curated_races() + _latest_custom_races()
    source = (request.args.get('source') or '').strip().lower()
    if source:
        all_races = [race for race in all_races if race.get('source') == source]
    return jsonify({'races': [race_summary(race) for race in all_races]})


@races_bp.get('/races/<race_id>')
def get_race(race_id):
    race_key = str(race_id or '').strip().lower()
    curated = CURATED_RACE_BY_ID.get(race_key)
    if curated:
        return jsonify(curated)

    row = _latest_custom_row(race_key)
    if not row:
        return error_response('race_not_found', 'Race not found.', 404)
    return jsonify(_definition_from_custom(row))


@races_bp.post('/custom-races/generate')
def generate_custom_race():
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    prompt = payload.get('prompt')
    strictness = str(payload.get('strictness') or 'standard').strip().lower()
    try:
        draft, generation_source = _generate_custom_race_draft(str(prompt or ''), strictness=strictness)
    except ValueError as exc:
        return error_response('validation_error', str(exc), 400)
    balance = analyze_race_balance(draft)
    draft['balance'] = balance
    draft['approvalStatus'] = approval_status_for_balance(balance)
    return jsonify(
        {
            'draftRace': draft,
            'balanceAnalysis': balance,
            'warnings': balance.get('warnings', []),
            'generationSource': generation_source,
        }
    )


@races_bp.post('/custom-races')
def create_custom_race():
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    try:
        race = normalize_race_definition(payload.get('raceDefinition'), source='custom')
    except ValueError as exc:
        return error_response('validation_error', str(exc), 400)
    approval_status = payload.get('approvalStatus') or race.get('approvalStatus') or approval_status_for_balance(race['balance'])
    if approval_status not in CUSTOM_RACE_APPROVAL_STATUSES:
        return error_response('validation_error', 'approvalStatus is not supported.', 400)
    race['approvalStatus'] = approval_status
    race['version'] = 1

    existing = _latest_custom_row(race['id'])
    if existing:
        return error_response('race_exists', 'A custom race with that id already exists.', 409)

    row = CustomRace(
        workspace_id=current_workspace_id(),
        race_id=race['id'],
        version=race['version'],
        name=race['name'],
        approval_status=approval_status,
        race_definition=safe_json_dumps(race, {}),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({'race': _definition_from_custom(row), 'summary': race_summary(race)}), 201


@races_bp.patch('/custom-races/<race_id>')
def update_custom_race(race_id):
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    row = _latest_custom_row(str(race_id or '').strip().lower())
    if not row:
        return error_response('race_not_found', 'Custom race not found.', 404)

    current = _definition_from_custom(row)
    incoming = payload.get('raceDefinition')
    if isinstance(incoming, dict):
        merged = {**current, **incoming}
    else:
        editable_keys = {
            'name',
            'descriptionShort',
            'descriptionLong',
            'aliases',
            'tags',
            'size',
            'baseSpeed',
            'visual',
            'traits',
            'aiNarrationHints',
            'roleplayHooks',
            'recommendedClasses',
            'difficulty',
            'parentRaceId',
        }
        merged = {**current, **{key: value for key, value in payload.items() if key in editable_keys}}
    try:
        race = normalize_race_definition(merged, source='custom')
    except ValueError as exc:
        return error_response('validation_error', str(exc), 400)
    race['id'] = row.race_id
    race['version'] = row.version + 1
    approval_status = payload.get('approvalStatus') or race.get('approvalStatus') or approval_status_for_balance(race['balance'])
    if approval_status not in CUSTOM_RACE_APPROVAL_STATUSES:
        return error_response('validation_error', 'approvalStatus is not supported.', 400)
    race['approvalStatus'] = approval_status

    new_row = CustomRace(
        workspace_id=current_workspace_id(),
        race_id=race['id'],
        version=race['version'],
        name=race['name'],
        approval_status=approval_status,
        race_definition=safe_json_dumps(race, {}),
    )
    db.session.add(new_row)
    db.session.commit()
    return jsonify({'race': _definition_from_custom(new_row), 'summary': race_summary(race)})


@races_bp.delete('/custom-races/<race_id>')
def delete_custom_race(race_id):
    race_key = str(race_id or '').strip().lower()
    deleted = CustomRace.query.filter_by(workspace_id=current_workspace_id(), race_id=race_key).delete(
        synchronize_session=False
    )
    if not deleted:
        return error_response('race_not_found', 'Custom race not found.', 404)
    db.session.commit()
    return jsonify({'deleted': True, 'race_id': race_key})
