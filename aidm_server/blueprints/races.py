from __future__ import annotations

import json
import logging
import os
from typing import Any

from flask import Blueprint, current_app, g, jsonify, request

from aidm_server.auth import account_display_name
from aidm_server.contracts import ProviderRequest
from aidm_server.database import db
from aidm_server.errors import error_response
from aidm_server.game_state.extraction.schemas import extract_json_object
from aidm_server.llm_providers import get_helper_provider, helper_provider_name
from aidm_server.models import CustomRace, safe_json_dumps, safe_json_loads
from aidm_server.race_system import (
    CUSTOM_RACE_APPROVAL_STATUSES,
    CURATED_RACE_BY_ID,
    DAMAGE_TYPES,
    RACE_TAGS,
    RACE_TRAIT_CATEGORIES,
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
from aidm_server.workspace_access import current_account_id, current_workspace_id


logger = logging.getLogger(__name__)
races_bp = Blueprint('races', __name__)
CUSTOM_RACE_HELPER_TASK = 'custom_race'
CUSTOM_RACE_HELPER_PREFIX = 'AIDM_CUSTOM_RACE_HELPER'
CUSTOM_RACE_GENERATION_MODES = {'canon', 'balanced'}
CUSTOM_RACE_SYSTEM_MESSAGE = (
    'You are a race metadata generator for an AI tabletop RPG. '
    'Convert custom race ideas into balanced structured metadata. Return JSON only.'
)


def _helper_provider_name() -> str:
    return helper_provider_name(CUSTOM_RACE_HELPER_TASK)


def _custom_race_helper_configured(provider_name: str) -> bool:
    if provider_name == 'deepseek':
        return bool(
            current_app.config.get(f'{CUSTOM_RACE_HELPER_PREFIX}_DEEPSEEK_API_KEY')
            or os.getenv(f'{CUSTOM_RACE_HELPER_PREFIX}_DEEPSEEK_API_KEY')
            or current_app.config.get('AIDM_HELPER_DEEPSEEK_API_KEY')
            or os.getenv('AIDM_HELPER_DEEPSEEK_API_KEY')
            or provider_configured('deepseek')
        )
    if provider_name in {'nvidia', 'kimi'}:
        return bool(
            current_app.config.get(f'{CUSTOM_RACE_HELPER_PREFIX}_NVIDIA_API_KEY')
            or os.getenv(f'{CUSTOM_RACE_HELPER_PREFIX}_NVIDIA_API_KEY')
            or current_app.config.get('AIDM_HELPER_NVIDIA_API_KEY')
            or os.getenv('AIDM_HELPER_NVIDIA_API_KEY')
            or provider_configured(provider_name)
        )
    if provider_name == 'gemini':
        return provider_configured('gemini')
    if provider_name == 'fallback':
        return True
    if provider_name in {'codex', 'codex_cli'}:
        return provider_configured(provider_name)
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


def _custom_race_generation_mode(value: Any) -> str:
    mode = str(value or 'canon').strip().lower()
    return mode if mode in CUSTOM_RACE_GENERATION_MODES else 'canon'


def _custom_race_priority_rules(generation_mode: str) -> str:
    if generation_mode == 'balanced':
        return (
            'Generation priority: create a balanced playable variant of the described race. '
            'Preserve the race identity, signature imagery, roleplay hooks, and core fantasy, but this mode may deliberately downscale, restrict, or convert extreme powers into once-per-rest abilities, drawbacks, or narrative-only traits. '
            'Aim for about 5 total balanceCost and avoid overpowered_unreviewed unless the concept cannot be represented honestly within that budget.\n\n'
            'Rules:\n'
            '- Prefer 2-4 meaningful traits.\n'
            '- Fit the standard race balance budget of 5 when possible.\n'
            '- Use restrictions or narrative-only traits to preserve flavor without giving every signature detail combat power.\n'
            '- Do not create immunity unless explicitly approved.\n'
            '- Prefer resistance over immunity.\n'
            '- Prefer once-per-rest abilities over unlimited abilities.\n'
            '- Prefer short-range utility over unrestricted teleportation.\n'
        )
    return (
        'Generation priority: create the truest playable metadata for the race the user described. '
        'Concept fidelity and source/canon faithfulness come before balance. Do not silently nerf, delete, or flatten signature abilities merely to fit a 5-point budget. '
        'If the race is strong, keep the strong traits and assign honest balanceCost values so the app can warn that it is strong or overpowered. '
        'If a signature power is campaign-warping, represent it with explicit mechanics, limits, drawbacks, or high balanceCost rather than pretending it is harmless flavor. '
        'Only produce a toned-down version when generation_mode is balanced.\n\n'
        'Rules:\n'
        '- Prefer 3-6 traits when needed to represent the race faithfully.\n'
        '- Use the balance budget as an analysis tool, not as a cap.\n'
        '- Overpowered traits are allowed in canon mode when they are central to the race; mark them with high balanceCost and concrete warnings through mechanics/aiHint.\n'
        '- Do not hide mechanical combat, movement, resistance, magic, or transformation power inside narrative traits.\n'
        '- Do not remove signature drawbacks; model them as restriction traits with negative balanceCost when useful.\n'
    )


def _build_custom_race_prompt(
    user_prompt: str,
    strictness: str,
    generation_mode: str = 'canon',
    source_race: dict[str, Any] | None = None,
) -> str:
    allowed_tags = ', '.join(sorted(RACE_TAGS))
    allowed_trait_categories = ', '.join(sorted(RACE_TRAIT_CATEGORIES))
    damage_types = ', '.join(sorted(DAMAGE_TYPES))
    source_race_section = ''
    if source_race:
        source_race_section = (
            'Current canon-first draft to revise:\n'
            f'{json.dumps(source_race, separators=(",", ":"))}\n\n'
        )
    return (
        'Convert the user custom race idea into a RaceDefinition JSON draft.\n\n'
        'Return a JSON object with keys name, aliases, tags, size, baseSpeed, visual, physical, languages, '
        'commonProficiencies, traits, roleplayHooks, recommendedClasses, difficulty, and aiNarrationHints. '
        'Do not wrap it in draftRace unless needed for compatibility.\n\n'
        f'Generation mode: {generation_mode}.\n'
        f'Allowed tags: {allowed_tags}.\n'
        f'Allowed trait categories: {allowed_trait_categories}.\n'
        f'Allowed damage types: {damage_types}.\n\n'
        'Every trait must include id, name, description, category, balanceCost, mechanics, and aiHint. '
        'Use balanceCost exactly; do not use cost. Use mechanics exactly; do not use mechanicalEffect. '
        'For once-per-rest powers, use category active_ability with mechanics.activeAbility containing actionType, cooldown, effectType, and optional scaling. '
        'For always-on boosts, use passive_ability. For natural weapons, use active_ability or passive_ability with balanceCost 1. '
        'For drawbacks, use restriction with negative balanceCost. For pure flavor only, use narrative with balanceCost 0. '
        'Do not mark mechanical combat traits as narrative. '
        'Do not return null values; omit optional fields instead. '
        'Descriptions must be complete sentences, not cut off fragments.\n\n'
        'For signature transformations or campaign-defining active abilities, mechanics.activeAbility must include trigger, duration, effects, limits, drawbacks, and endCondition when applicable. '
        'For a Great Ape, werewolf, super mode, or similar transformation, include controlCheck or lossOfControl details, concrete stat/combat effects, how it starts, how it ends, and the cost or risk to the character. '
        'Do not leave transformation mechanics as scaling:null.\n\n'
        'Visual metadata must include portraitKey, iconKey, bodyType, and commonFeatures. '
        'Physical metadata must include averageHeight and averageWeight.\n\n'
        '- Use only supported tags, trait categories, cooldowns, action types, and damage types from the schema.\n'
        '- Do not add the flying tag unless the race has innate wings or a true racial flight trait.\n'
        '- Return JSON only, with no markdown fences.\n\n'
        f'{_custom_race_priority_rules(generation_mode)}\n'
        f'Strictness: {strictness}\n\n'
        f'Trait cost guide:\n{json.dumps(TRAIT_COST_GUIDE, separators=(",", ":"))}\n\n'
        f'{source_race_section}'
        f'User custom race idea:\n{user_prompt}\n\n'
        'Return a RaceDefinition draft.'
    )


def _race_payload_from_helper(payload: dict[str, Any], prompt: str) -> dict[str, Any]:
    candidate = payload.get('draftRace') if isinstance(payload.get('draftRace'), dict) else payload
    candidate = _strip_null_metadata(candidate)
    merged = {
        'source': 'custom',
        'descriptionLong': prompt,
        **candidate,
    }
    race = normalize_race_definition(merged, source='custom')
    race['balance'] = analyze_race_balance(race)
    race['approvalStatus'] = approval_status_for_balance(race['balance'])
    return race


def _generate_custom_race_draft(
    prompt: str,
    *,
    strictness: str,
    generation_mode: str = 'canon',
    source_race: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    fallback = generate_custom_race_draft(prompt, strictness=strictness)
    if not _custom_race_helper_enabled():
        return fallback, 'deterministic'

    try:
        response = get_helper_provider(task=CUSTOM_RACE_HELPER_TASK).generate(
            ProviderRequest(
                prompt=_build_custom_race_prompt(prompt, strictness, generation_mode, source_race),
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


def _strip_null_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_null_metadata(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_strip_null_metadata(item) for item in value if item is not None]
    return value


def _creator_display_name_from_row(row: CustomRace) -> str | None:
    if row.creator_display_name:
        return row.creator_display_name
    if row.account:
        return account_display_name(row.account)
    return row.creator_username


def _attach_custom_race_metadata(race: dict[str, Any], row: CustomRace) -> dict[str, Any]:
    creator_username = row.creator_username or (row.account.username if row.account else None)
    race['workspaceId'] = row.workspace_id
    race['createdByAccountId'] = row.account_id
    race['createdByUsername'] = creator_username
    race['createdByDisplayName'] = _creator_display_name_from_row(row)
    race['createdAt'] = row.created_at.isoformat() if row.created_at else None
    race['updatedAt'] = row.updated_at.isoformat() if row.updated_at else None
    return race


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
    return _attach_custom_race_metadata(normalized, row)


def _latest_custom_race_rows(*, all_workspaces: bool = False) -> list[CustomRace]:
    query = CustomRace.query
    if not all_workspaces:
        query = query.filter_by(workspace_id=current_workspace_id())
    rows = (
        query.order_by(
            CustomRace.workspace_id.asc(),
            CustomRace.race_id.asc(),
            CustomRace.version.desc(),
            CustomRace.custom_race_id.desc(),
        )
        .all()
    )
    seen: set[tuple[str, str]] = set()
    latest_rows: list[CustomRace] = []
    for row in rows:
        key = (row.workspace_id, row.race_id)
        if key in seen:
            continue
        seen.add(key)
        latest_rows.append(row)
    return latest_rows


def _latest_custom_races(*, all_workspaces: bool = False) -> list[dict]:
    return [_definition_from_custom(row) for row in _latest_custom_race_rows(all_workspaces=all_workspaces)]


def _latest_workspace_custom_row(race_id: str) -> CustomRace | None:
    return (
        CustomRace.query.filter_by(workspace_id=current_workspace_id(), race_id=race_id)
        .order_by(CustomRace.version.desc(), CustomRace.custom_race_id.desc())
        .first()
    )


def _latest_visible_custom_row(race_id: str, workspace_id: str | None = None) -> CustomRace | None:
    if workspace_id:
        return (
            CustomRace.query.filter_by(workspace_id=workspace_id, race_id=race_id)
            .order_by(CustomRace.version.desc(), CustomRace.custom_race_id.desc())
            .first()
        )
    workspace_row = _latest_workspace_custom_row(race_id)
    if workspace_row:
        return workspace_row
    return (
        CustomRace.query.filter_by(race_id=race_id)
        .order_by(CustomRace.version.desc(), CustomRace.custom_race_id.desc())
        .first()
    )


@races_bp.get('/races')
def list_races():
    all_races = curated_races() + _latest_custom_races(all_workspaces=True)
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

    workspace_id = str(request.args.get('workspaceId') or request.args.get('workspace_id') or '').strip() or None
    row = _latest_visible_custom_row(race_key, workspace_id=workspace_id)
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
    generation_mode = _custom_race_generation_mode(payload.get('generationMode') or payload.get('generation_mode'))
    source_race = payload.get('currentDraft') if isinstance(payload.get('currentDraft'), dict) else None
    try:
        draft, generation_source = _generate_custom_race_draft(
            str(prompt or ''),
            strictness=strictness,
            generation_mode=generation_mode,
            source_race=source_race,
        )
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
            'generationMode': generation_mode,
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

    existing = _latest_workspace_custom_row(race['id'])
    if existing:
        return error_response('race_exists', 'A custom race with that id already exists.', 409)

    account = getattr(g, 'aidm_account', None)
    row = CustomRace(
        workspace_id=current_workspace_id(),
        account_id=current_account_id(),
        creator_username=account.username if account else None,
        creator_display_name=account_display_name(account) if account else None,
        race_id=race['id'],
        version=race['version'],
        name=race['name'],
        approval_status=approval_status,
        race_definition=safe_json_dumps(race, {}),
    )
    db.session.add(row)
    db.session.commit()
    saved_race = _definition_from_custom(row)
    return jsonify({'race': saved_race, 'summary': race_summary(saved_race)}), 201


@races_bp.patch('/custom-races/<race_id>')
def update_custom_race(race_id):
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)
    row = _latest_workspace_custom_row(str(race_id or '').strip().lower())
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
        account_id=row.account_id,
        creator_username=row.creator_username,
        creator_display_name=row.creator_display_name,
        race_id=race['id'],
        version=race['version'],
        name=race['name'],
        approval_status=approval_status,
        race_definition=safe_json_dumps(race, {}),
    )
    db.session.add(new_row)
    db.session.commit()
    saved_race = _definition_from_custom(new_row)
    return jsonify({'race': saved_race, 'summary': race_summary(saved_race)})


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
