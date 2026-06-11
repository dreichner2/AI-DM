from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from aidm_server.auth import (
    DEFAULT_WORKSPACE_ID,
    WORKSPACE_ID_HEADER,
    account_display_name,
    account_for_token,
    account_requires_password_setup,
    account_workspace_membership,
    claim_legacy_players_for_account,
    ensure_account_workspace_membership,
    generate_account_token,
    generate_workspace_token,
    hash_secret,
    normalize_workspace_name,
    normalize_workspace_name_key,
    normalize_workspace_id,
    normalize_username,
    password_hash_for,
    password_hash_matches,
    password_matches,
    request_account_token,
    request_workspace_id,
    workspace_id_from_name,
    workspace_id_for_workspace_token,
    workspace_role_is_admin,
)
from aidm_server.database import db
from aidm_server.errors import error_response
from aidm_server.models import (
    Account,
    AccountWorkspaceMembership,
    BestiaryEntry,
    Campaign,
    CampaignSegment,
    CanonJob,
    CombatDebugEvent,
    CombatEncounter,
    CustomRace,
    DmCoherenceFeedback,
    DmTurn,
    Map,
    Npc,
    Player,
    PlayerAction,
    Session,
    SessionLogEntry,
    SessionState,
    SessionTurnLock,
    StoryEntity,
    StoryEvent,
    StoryFact,
    StoryThread,
    TurnCanonUpdate,
    TurnEvent,
    Workspace,
    World,
)
from aidm_server.validation import optional_text as _optional_text, parse_json_body, required_text as _required_text


logger = logging.getLogger(__name__)
accounts_bp = Blueprint('accounts', __name__)
LEGACY_PASSWORD_SETUP_MESSAGE = 'Passwords are required now. Please set one now.'
WORKSPACE_NAME_TAKEN_MESSAGE = 'table/ workspace name already in use'


def _legacy_password_setup_required_response():
    return error_response('legacy_password_setup_required', LEGACY_PASSWORD_SETUP_MESSAGE, 401)


def _workspace_access_mode(workspace: Workspace | None) -> str:
    if workspace is None:
        return 'configured'
    if workspace.password_hash:
        return 'password'
    if workspace.token_hash:
        return 'token'
    return 'unknown'


def workspace_membership_payload(
    membership: AccountWorkspaceMembership,
    workspace: Workspace | None = None,
) -> dict:
    workspace_name = workspace.name if workspace else membership.workspace_id
    return {
        'workspace_id': membership.workspace_id,
        'workspace_name': workspace_name,
        'table_name': workspace_name,
        'access_mode': _workspace_access_mode(workspace),
        'workspace_role': membership.role,
        'is_workspace_admin': workspace_role_is_admin(membership.role),
        'created_at': membership.created_at.isoformat() if membership.created_at else None,
        'updated_at': membership.updated_at.isoformat() if membership.updated_at else None,
    }


def account_workspaces_payload(account: Account) -> list[dict]:
    memberships = sorted(
        account.workspace_memberships,
        key=lambda membership: (
            membership.updated_at or membership.created_at,
            membership.workspace_id,
        ),
        reverse=True,
    )
    workspace_ids = [membership.workspace_id for membership in memberships]
    workspace_records = (
        {
            workspace.workspace_id: workspace
            for workspace in Workspace.query.filter(Workspace.workspace_id.in_(workspace_ids)).all()
        }
        if workspace_ids
        else {}
    )
    return [
        workspace_membership_payload(membership, workspace_records.get(membership.workspace_id))
        for membership in memberships
    ]


def account_payload(account: Account, *, workspace_id: str | None = None, role: str | None = None) -> dict:
    return {
        'account_id': account.account_id,
        'username': account.username,
        'first_name': account.first_name,
        'last_name': account.last_name,
        'display_name': account_display_name(account),
        'workspace_id': workspace_id,
        'workspace_role': role,
        'is_workspace_admin': workspace_role_is_admin(role),
        'requires_password_setup': account_requires_password_setup(account),
        'workspaces': account_workspaces_payload(account),
    }


def _workspace_token_from_payload(payload: dict) -> str | None:
    token = payload.get('workspace_token') or payload.get('workspaceToken')
    return str(token).strip() if token else None


def _workspace_name_from_payload(payload: dict) -> tuple[str | None, str | None]:
    value = (
        payload.get('table_name')
        or payload.get('tableName')
        or payload.get('workspace_name')
        or payload.get('workspaceName')
        or payload.get('name')
    )
    return _required_text(value, max_length=120, field='table_name')


def _workspace_password_from_payload(payload: dict) -> str:
    return str(
        payload.get('table_password')
        or payload.get('tablePassword')
        or payload.get('workspace_password')
        or payload.get('workspacePassword')
        or payload.get('password')
        or ''
    )


def _workspace_access_mode_from_payload(payload: dict) -> tuple[str, str | None]:
    raw_mode = payload.get('access_mode') or payload.get('accessMode') or 'password'
    mode = str(raw_mode or '').strip().casefold()
    if mode in {'password', 'passcode'}:
        return 'password', None
    if mode in {'token', 'generated_token', 'generated-token'}:
        return 'token', None
    return 'password', 'access_mode must be password or token.'


def _truthy_payload_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _legacy_claim_requested(payload: dict) -> bool:
    return _truthy_payload_flag(payload.get('legacy_claim') or payload.get('legacyClaim'))


def _legacy_claim_identity_matches(account: Account, first_name: str | None, last_name: str | None) -> bool:
    existing_first = str(account.first_name or '').strip().casefold()
    existing_last = str(account.last_name or '').strip().casefold()
    supplied_first = str(first_name or '').strip().casefold()
    supplied_last = str(last_name or '').strip().casefold()
    if not supplied_first and not supplied_last:
        return True
    return bool(existing_first and existing_last and supplied_first and supplied_last) and (
        existing_first,
        existing_last,
    ) == (
        supplied_first,
        supplied_last,
    )


def _account_intent_from_payload(payload: dict) -> tuple[str | None, str | None]:
    raw_intent = payload.get('intent') or payload.get('account_intent') or payload.get('accountIntent')
    if raw_intent is None or str(raw_intent).strip() == '':
        return None, None
    intent = str(raw_intent).strip().casefold()
    if intent in {'login', 'sign_in', 'signin'}:
        return 'login', None
    if intent in {'signup', 'sign_up', 'register'}:
        return 'signup', None
    return None, 'intent must be login or signup.'


def _validate_workspace_token(payload: dict) -> str | None:
    return workspace_id_for_workspace_token(_workspace_token_from_payload(payload))


def _configured_workspace_ids() -> set[str]:
    workspace_ids = {DEFAULT_WORKSPACE_ID}
    configured_map = current_app.config.get('AIDM_API_AUTH_TOKEN_WORKSPACES', {})
    if isinstance(configured_map, dict):
        workspace_ids.update(str(workspace_id) for workspace_id in configured_map.values())
    if current_app.config.get('AIDM_API_AUTH_TOKENS'):
        workspace_ids.add(DEFAULT_WORKSPACE_ID)
    return {normalize_workspace_id(workspace_id) for workspace_id in workspace_ids if str(workspace_id).strip()}


def _workspace_id_in_use(workspace_id: str) -> bool:
    if workspace_id in _configured_workspace_ids():
        return True
    if Workspace.query.filter_by(workspace_id=workspace_id).first():
        return True
    if AccountWorkspaceMembership.query.filter_by(workspace_id=workspace_id).first():
        return True
    if Campaign.query.filter_by(workspace_id=workspace_id).first():
        return True
    if World.query.filter_by(workspace_id=workspace_id).first():
        return True
    return bool(Player.query.filter_by(workspace_id=workspace_id).first())


def _workspace_name_in_use(name: str, workspace_id: str) -> bool:
    name_key = normalize_workspace_name_key(name)
    if Workspace.query.filter_by(name_key=name_key).first():
        return True
    return _workspace_id_in_use(workspace_id)


def _ids(query) -> list[int]:
    return [row[0] for row in query.all()]


def _delete_workspace_rows(workspace_id: str) -> None:
    world_ids = _ids(World.query.with_entities(World.world_id).filter_by(workspace_id=workspace_id))
    campaign_ids = _ids(Campaign.query.with_entities(Campaign.campaign_id).filter_by(workspace_id=workspace_id))
    session_ids = (
        _ids(Session.query.with_entities(Session.session_id).filter(Session.campaign_id.in_(campaign_ids)))
        if campaign_ids
        else []
    )
    player_ids = _ids(Player.query.with_entities(Player.player_id).filter_by(workspace_id=workspace_id))
    turn_ids = (
        _ids(DmTurn.query.with_entities(DmTurn.turn_id).filter(DmTurn.campaign_id.in_(campaign_ids)))
        if campaign_ids
        else []
    )
    story_entity_ids = (
        _ids(StoryEntity.query.with_entities(StoryEntity.entity_id).filter(StoryEntity.campaign_id.in_(campaign_ids)))
        if campaign_ids
        else []
    )

    CustomRace.query.filter_by(workspace_id=workspace_id).delete(synchronize_session=False)
    BestiaryEntry.query.filter_by(workspace_id=workspace_id).delete(synchronize_session=False)

    if story_entity_ids:
        StoryFact.query.filter(
            or_(
                StoryFact.subject_entity_id.in_(story_entity_ids),
                StoryFact.object_entity_id.in_(story_entity_ids),
            )
        ).delete(synchronize_session=False)
    if campaign_ids:
        StoryFact.query.filter(StoryFact.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
        StoryThread.query.filter(StoryThread.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
        StoryEntity.query.filter(StoryEntity.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
        TurnCanonUpdate.query.filter(TurnCanonUpdate.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
        CanonJob.query.filter(CanonJob.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
        TurnEvent.query.filter(TurnEvent.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
        CombatDebugEvent.query.filter(CombatDebugEvent.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
        CombatEncounter.query.filter(CombatEncounter.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
        CampaignSegment.query.filter(CampaignSegment.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
        StoryEvent.query.filter(StoryEvent.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
        Map.query.filter(Map.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
    if session_ids:
        DmCoherenceFeedback.query.filter(DmCoherenceFeedback.session_id.in_(session_ids)).delete(synchronize_session=False)
        SessionTurnLock.query.filter(SessionTurnLock.session_id.in_(session_ids)).delete(synchronize_session=False)
        SessionState.query.filter(SessionState.session_id.in_(session_ids)).delete(synchronize_session=False)
        SessionLogEntry.query.filter(SessionLogEntry.session_id.in_(session_ids)).delete(synchronize_session=False)
        PlayerAction.query.filter(PlayerAction.session_id.in_(session_ids)).delete(synchronize_session=False)
    if player_ids:
        PlayerAction.query.filter(PlayerAction.player_id.in_(player_ids)).delete(synchronize_session=False)
        TurnEvent.query.filter(TurnEvent.player_id.in_(player_ids)).delete(synchronize_session=False)
        DmTurn.query.filter(DmTurn.player_id.in_(player_ids)).update(
            {DmTurn.player_id: None},
            synchronize_session=False,
        )
    if turn_ids:
        DmCoherenceFeedback.query.filter(DmCoherenceFeedback.turn_id.in_(turn_ids)).delete(synchronize_session=False)

    if campaign_ids:
        DmTurn.query.filter(DmTurn.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
    if session_ids:
        Session.query.filter(Session.session_id.in_(session_ids)).delete(synchronize_session=False)
    Player.query.filter_by(workspace_id=workspace_id).delete(synchronize_session=False)
    if campaign_ids:
        Campaign.query.filter(Campaign.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
    if world_ids:
        Npc.query.filter(Npc.world_id.in_(world_ids)).delete(synchronize_session=False)
        Map.query.filter(Map.world_id.in_(world_ids)).delete(synchronize_session=False)
    World.query.filter_by(workspace_id=workspace_id).delete(synchronize_session=False)
    AccountWorkspaceMembership.query.filter_by(workspace_id=workspace_id).delete(synchronize_session=False)
    Workspace.query.filter_by(workspace_id=workspace_id).delete(synchronize_session=False)


def account_session_payload(
    account: Account,
    *,
    account_token: str,
    workspace_id: str | None = None,
    role: str | None = None,
    claimed_player_ids: list[int] | None = None,
    workspace_token: str | None = None,
) -> dict:
    payload = {
        'account': account_payload(account, workspace_id=workspace_id, role=role),
        'account_token': account_token,
        'workspace_id': workspace_id,
        'workspace_role': role,
        'is_workspace_admin': workspace_role_is_admin(role),
        'claimed_player_ids': claimed_player_ids or [],
        'workspaces': account_workspaces_payload(account),
    }
    if workspace_token:
        payload['workspace_token'] = workspace_token
    return payload


@accounts_bp.route('/login', methods=['POST'])
def login_or_create_account():
    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    raw_username, username_error = _required_text(payload.get('username'), max_length=80, field='username')
    if username_error:
        return error_response('validation_error', username_error, 400)
    username = normalize_username(raw_username)
    if not username:
        return error_response('validation_error', 'username is required.', 400)
    account_intent, account_intent_error = _account_intent_from_payload(payload)
    if account_intent_error:
        return error_response('validation_error', account_intent_error, 400)

    workspace_token = _workspace_token_from_payload(payload)
    workspace_id = _validate_workspace_token(payload) if workspace_token else None
    if workspace_token and not workspace_id:
        return error_response('unauthorized', 'Missing or invalid workspace token.', 401)

    first_name, first_name_error = _optional_text(payload.get('first_name'), max_length=80, field='first_name')
    if first_name_error:
        return error_response('validation_error', first_name_error, 400)
    last_name, last_name_error = _optional_text(payload.get('last_name'), max_length=80, field='last_name')
    if last_name_error:
        return error_response('validation_error', last_name_error, 400)
    password = str(payload.get('password') or '')

    try:
        account = Account.query.filter_by(username=username).first()
        token = request_account_token() or ''
        token_account = account_for_token(token)

        created = False
        if account is None:
            if account_intent == 'login':
                return error_response('username_not_found', 'Username not found. Please sign up.', 404)
            if not first_name or not last_name:
                return error_response(
                    'validation_error',
                    'First and last name are required for a new account.',
                    400,
                    {'missing_fields': [field for field, value in {'first_name': first_name, 'last_name': last_name}.items() if not value]},
                )
            if not password.strip():
                return error_response('validation_error', 'Password is required.', 400)
            token = generate_account_token()
            account = Account(
                username=username,
                first_name=first_name,
                last_name=last_name,
                password_hash=password_hash_for(password),
                account_token_hash=hash_secret(token),
            )
            db.session.add(account)
            db.session.flush()
            created = True
        else:
            if account_intent == 'signup':
                return error_response('username_taken', 'Username is already taken. Please sign in.', 409)
            token_is_valid_for_account = bool(token_account and token_account.account_id == account.account_id)
            password_is_valid = password_matches(account, password)
            account_has_password = bool(account.password_hash)
            if not account_has_password:
                password_setup_allowed = bool(password) and (
                    token_is_valid_for_account
                    or (
                        _legacy_claim_requested(payload)
                        and _legacy_claim_identity_matches(account, first_name, last_name)
                    )
                )
                if password_setup_allowed:
                    if not token_is_valid_for_account:
                        token = generate_account_token()
                    account.account_token_hash = hash_secret(token)
                    account.password_hash = password_hash_for(password)
                else:
                    return _legacy_password_setup_required_response()
            else:
                if not password_is_valid:
                    return error_response('unauthorized', 'Invalid account password.', 401)
                if not token_is_valid_for_account:
                    token = generate_account_token()
                    account.account_token_hash = hash_secret(token)
            if first_name:
                account.first_name = first_name
            if last_name:
                account.last_name = last_name

        membership = ensure_account_workspace_membership(account, workspace_id) if workspace_id else None
        claimed_player_ids = claim_legacy_players_for_account(account, workspace_id) if workspace_id else []
        db.session.commit()
        status = 201 if created else 200
        return jsonify(account_session_payload(
            account,
            account_token=token,
            workspace_id=workspace_id,
            role=membership.role if membership else None,
            claimed_player_ids=claimed_player_ids,
        )), status
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to login or create account: %s', str(exc))
        return error_response('account_login_failed', 'Failed to login or create account.', 400)


@accounts_bp.route('/workspace', methods=['POST'])
def join_account_workspace():
    account_token = request_account_token() or ''
    account = account_for_token(account_token)
    if not account:
        return error_response('unauthorized', 'Missing or invalid account session.', 401)
    if account_requires_password_setup(account):
        return _legacy_password_setup_required_response()

    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    workspace_token = _workspace_token_from_payload(payload)
    workspace_id = _validate_workspace_token(payload) if workspace_token else None
    if workspace_token and not workspace_id:
        return error_response('unauthorized', 'Missing or invalid workspace token.', 401)

    if not workspace_token:
        raw_workspace_name, workspace_name_error = _workspace_name_from_payload(payload)
        if workspace_name_error:
            return error_response('validation_error', workspace_name_error, 400)
        workspace_name = normalize_workspace_name(raw_workspace_name)
        workspace_password = _workspace_password_from_payload(payload)
        if not workspace_password.strip():
            return error_response('validation_error', 'Table password is required.', 400)
        workspace = Workspace.query.filter_by(name_key=normalize_workspace_name_key(workspace_name)).first()
        if workspace is None:
            workspace = Workspace.query.filter_by(workspace_id=normalize_workspace_id(workspace_name)).first()
        if not workspace or not password_hash_matches(workspace.password_hash, workspace_password):
            return error_response('unauthorized', 'Missing or invalid table password.', 401)
        workspace_id = workspace.workspace_id

    if not workspace_id:
        return error_response('unauthorized', 'Missing or invalid workspace token.', 401)

    try:
        membership = ensure_account_workspace_membership(account, workspace_id)
        claimed_player_ids = claim_legacy_players_for_account(account, workspace_id)
        db.session.commit()
        return jsonify(account_session_payload(
            account,
            account_token=account_token,
            workspace_id=workspace_id,
            role=membership.role if membership else None,
            claimed_player_ids=claimed_player_ids,
        ))
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to join account workspace: %s', str(exc))
        return error_response('workspace_join_failed', 'Failed to join workspace.', 400)


@accounts_bp.route('/workspaces', methods=['POST'])
def create_account_workspace():
    account_token = request_account_token() or ''
    account = account_for_token(account_token)
    if not account:
        return error_response('unauthorized', 'Missing or invalid account session.', 401)
    if account_requires_password_setup(account):
        return _legacy_password_setup_required_response()

    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    raw_workspace_name, workspace_name_error = _workspace_name_from_payload(payload)
    if workspace_name_error:
        return error_response('validation_error', workspace_name_error, 400)
    workspace_name = normalize_workspace_name(raw_workspace_name)
    workspace_id = workspace_id_from_name(workspace_name)
    if not workspace_id:
        return error_response('validation_error', 'Table name must include letters or numbers.', 400)
    if _workspace_name_in_use(workspace_name, workspace_id):
        return error_response('workspace_name_taken', WORKSPACE_NAME_TAKEN_MESSAGE, 409)

    access_mode, access_mode_error = _workspace_access_mode_from_payload(payload)
    if access_mode_error:
        return error_response('validation_error', access_mode_error, 400)

    workspace_password_hash = None
    workspace_token = None
    workspace_token_hash = None
    if access_mode == 'password':
        workspace_password = _workspace_password_from_payload(payload)
        if not workspace_password.strip():
            return error_response('validation_error', 'Table password is required.', 400)
        workspace_password_hash = password_hash_for(workspace_password)
    else:
        workspace_token = generate_workspace_token()
        workspace_token_hash = hash_secret(workspace_token)

    try:
        workspace = Workspace(
            workspace_id=workspace_id,
            name=workspace_name,
            name_key=normalize_workspace_name_key(workspace_name),
            password_hash=workspace_password_hash,
            token_hash=workspace_token_hash,
            created_by_account_id=account.account_id,
        )
        membership = AccountWorkspaceMembership(
            account_id=account.account_id,
            workspace_id=workspace_id,
            role='admin',
        )
        db.session.add(workspace)
        db.session.add(membership)
        db.session.flush()
        db.session.commit()
        return jsonify(account_session_payload(
            account,
            account_token=account_token,
            workspace_id=workspace_id,
            role=membership.role,
            workspace_token=workspace_token,
        )), 201
    except IntegrityError:
        db.session.rollback()
        return error_response('workspace_name_taken', WORKSPACE_NAME_TAKEN_MESSAGE, 409)
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to create account workspace: %s', str(exc))
        return error_response('workspace_create_failed', 'Failed to create table.', 400)


@accounts_bp.route('/workspace/select', methods=['POST'])
def select_account_workspace():
    account_token = request_account_token() or ''
    account = account_for_token(account_token)
    if not account:
        return error_response('unauthorized', 'Missing or invalid account session.', 401)
    if account_requires_password_setup(account):
        return _legacy_password_setup_required_response()

    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    raw_workspace_id = payload.get('workspace_id') or payload.get('workspaceId')
    workspace_id = normalize_workspace_id(str(raw_workspace_id or ''))
    if not raw_workspace_id or not workspace_id:
        return error_response('validation_error', 'workspace_id is required.', 400)

    try:
        membership = account_workspace_membership(account, workspace_id)
        if not membership:
            return error_response('workspace_not_saved', 'Workspace is not saved to this account.', 403)
        claimed_player_ids = claim_legacy_players_for_account(account, workspace_id)
        db.session.commit()
        return jsonify(account_session_payload(
            account,
            account_token=account_token,
            workspace_id=workspace_id,
            role=membership.role if membership else None,
            claimed_player_ids=claimed_player_ids,
        ))
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to select account workspace: %s', str(exc))
        return error_response('workspace_select_failed', 'Failed to select workspace.', 400)


@accounts_bp.route('/workspaces', methods=['GET'])
def account_workspaces():
    account = account_for_token(request_account_token())
    if not account:
        return error_response('unauthorized', 'Missing or invalid account session.', 401)
    if account_requires_password_setup(account):
        return _legacy_password_setup_required_response()
    return jsonify({'workspaces': account_workspaces_payload(account)})


@accounts_bp.route('/workspaces/<workspace_id>', methods=['DELETE'])
def delete_or_remove_account_workspace(workspace_id: str):
    account_token = request_account_token() or ''
    account = account_for_token(account_token)
    if not account:
        return error_response('unauthorized', 'Missing or invalid account session.', 401)
    if account_requires_password_setup(account):
        return _legacy_password_setup_required_response()

    clean_workspace_id = normalize_workspace_id(workspace_id)
    membership = account_workspace_membership(account, clean_workspace_id)
    if not membership:
        return error_response('workspace_not_saved', 'Workspace is not saved to this account.', 403)

    workspace = Workspace.query.filter_by(workspace_id=clean_workspace_id).first()
    should_delete_workspace = (
        workspace is not None
        and clean_workspace_id not in _configured_workspace_ids()
        and workspace_role_is_admin(membership.role)
    )
    try:
        action = 'deleted' if should_delete_workspace else 'removed'
        if should_delete_workspace:
            _delete_workspace_rows(clean_workspace_id)
        else:
            db.session.delete(membership)
        db.session.commit()
        payload = account_session_payload(account, account_token=account_token)
        payload['workspace_action'] = action
        payload['workspace_id_removed'] = clean_workspace_id
        return jsonify(payload)
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to delete or remove account workspace: %s', str(exc))
        return error_response('workspace_delete_failed', 'Failed to delete table.', 400)


@accounts_bp.route('/me', methods=['GET'])
def account_me():
    account = account_for_token(request_account_token())
    if not account:
        return error_response('unauthorized', 'Missing or invalid account session.', 401)
    workspace_id = request_workspace_id()
    membership = account_workspace_membership(account, workspace_id) if workspace_id else None
    if workspace_id and not membership and str(request.headers.get(WORKSPACE_ID_HEADER) or '').strip():
        return error_response('workspace_not_saved', 'Workspace is not saved to this account.', 403)
    if workspace_id and not membership:
        membership = ensure_account_workspace_membership(account, workspace_id)
        db.session.commit()
    return jsonify(account_payload(account, workspace_id=workspace_id, role=membership.role if membership else None))
