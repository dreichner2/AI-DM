from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from aidm_server.auth import (
    WORKSPACE_ID_HEADER,
    account_display_name,
    account_for_token,
    account_workspace_membership,
    claim_legacy_players_for_account,
    ensure_account_workspace_membership,
    generate_account_token,
    hash_secret,
    normalize_workspace_id,
    normalize_username,
    password_hash_for,
    password_matches,
    request_account_token,
    request_workspace_id,
    workspace_id_for_workspace_token,
    workspace_role_is_admin,
)
from aidm_server.database import db
from aidm_server.errors import error_response
from aidm_server.models import Account, AccountWorkspaceMembership
from aidm_server.validation import optional_text as _optional_text, parse_json_body, required_text as _required_text


logger = logging.getLogger(__name__)
accounts_bp = Blueprint('accounts', __name__)


def workspace_membership_payload(membership: AccountWorkspaceMembership) -> dict:
    return {
        'workspace_id': membership.workspace_id,
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
    return [workspace_membership_payload(membership) for membership in memberships]


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
        'workspaces': account_workspaces_payload(account),
    }


def _workspace_token_from_payload(payload: dict) -> str | None:
    token = payload.get('workspace_token') or payload.get('workspaceToken')
    return str(token).strip() if token else None


def _validate_workspace_token(payload: dict) -> str | None:
    return workspace_id_for_workspace_token(_workspace_token_from_payload(payload))


def account_session_payload(
    account: Account,
    *,
    account_token: str,
    workspace_id: str | None = None,
    role: str | None = None,
    claimed_player_ids: list[int] | None = None,
) -> dict:
    return {
        'account': account_payload(account, workspace_id=workspace_id, role=role),
        'account_token': account_token,
        'workspace_id': workspace_id,
        'workspace_role': role,
        'is_workspace_admin': workspace_role_is_admin(role),
        'claimed_player_ids': claimed_player_ids or [],
        'workspaces': account_workspaces_payload(account),
    }


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
            if not first_name or not last_name:
                return error_response(
                    'validation_error',
                    'First and last name are required for a new account.',
                    400,
                    {'missing_fields': [field for field, value in {'first_name': first_name, 'last_name': last_name}.items() if not value]},
                )
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
            token_is_valid_for_account = bool(token_account and token_account.account_id == account.account_id)
            if not token_is_valid_for_account and not password_matches(account, password):
                return error_response('unauthorized', 'Invalid account password.', 401)
            if not token:
                token = generate_account_token()
                account.account_token_hash = hash_secret(token)
            if first_name:
                account.first_name = first_name
            if last_name:
                account.last_name = last_name
            if password and not account.password_hash:
                account.password_hash = password_hash_for(password)

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

    payload = parse_json_body(request)
    if payload is None:
        return error_response('validation_error', 'Expected JSON request body.', 400)

    workspace_id = _validate_workspace_token(payload)
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


@accounts_bp.route('/workspace/select', methods=['POST'])
def select_account_workspace():
    account_token = request_account_token() or ''
    account = account_for_token(account_token)
    if not account:
        return error_response('unauthorized', 'Missing or invalid account session.', 401)

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
    return jsonify({'workspaces': account_workspaces_payload(account)})


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
