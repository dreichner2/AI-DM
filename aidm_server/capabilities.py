from __future__ import annotations

from typing import Literal

from flask import g, has_request_context

from aidm_server.errors import error_response
from aidm_server.workspace_access import current_account_id, current_account_is_workspace_admin


Capability = Literal[
    'player_read',
    'player_action',
    'dm_authoring',
    'dm_runtime_control',
    'debug_read',
    'admin_workspace',
    'local_operator_only',
    'server_internal',
]

CAPABILITY_DESCRIPTIONS: dict[Capability, str] = {
    'player_read': 'Read player-visible game state.',
    'player_action': 'Submit normal player actions.',
    'dm_authoring': 'Create or save DM-authored campaign content.',
    'dm_runtime_control': 'Directly control mutable runtime session state.',
    'debug_read': 'Read operator/debug state.',
    'admin_workspace': 'Manage workspace-level resources.',
    'local_operator_only': 'Use local unauthenticated operator tooling.',
    'server_internal': 'Call server-internal hooks only.',
}

PLAYER_CAPABILITIES: set[Capability] = {'player_read', 'player_action'}
WORKSPACE_ADMIN_CAPABILITIES: set[Capability] = {
    *PLAYER_CAPABILITIES,
    'dm_authoring',
    'dm_runtime_control',
    'debug_read',
    'admin_workspace',
}
LOCAL_OPERATOR_CAPABILITIES: set[Capability] = {*WORKSPACE_ADMIN_CAPABILITIES, 'local_operator_only'}


# This is the enforcement inventory for externally reachable operator actions.
# Keys use Flask's ``<blueprint>.<view function>`` endpoint names so route paths
# can evolve without silently dropping authorization checks.
HTTP_ENDPOINT_CAPABILITIES: dict[tuple[str, str], Capability] = {
    # World and campaign authoring.
    ('worlds.create_world', 'POST'): 'dm_authoring',
    ('worlds.update_world', 'PATCH'): 'dm_authoring',
    ('worlds.delete_world', 'DELETE'): 'dm_authoring',
    ('campaigns.create_campaign', 'POST'): 'dm_authoring',
    ('campaigns.update_campaign', 'PATCH'): 'dm_authoring',
    ('campaigns.archive_campaign', 'POST'): 'dm_authoring',
    ('campaigns.restore_campaign', 'POST'): 'dm_authoring',
    ('campaigns.delete_campaign', 'DELETE'): 'dm_authoring',
    # Campaign-pack authoring and hidden installed-pack data.
    ('campaigns.lint_campaign_pack_manifest_endpoint', 'POST'): 'dm_authoring',
    ('campaigns.forge_campaign_pack_manifest_endpoint', 'POST'): 'dm_authoring',
    ('campaigns.list_installed_campaign_packs', 'GET'): 'dm_authoring',
    ('campaigns.import_example_campaign_pack', 'POST'): 'dm_authoring',
    ('campaigns.get_installed_campaign_pack', 'GET'): 'dm_authoring',
    ('campaigns.import_installed_campaign_pack', 'POST'): 'dm_authoring',
    ('campaigns.import_campaign_pack_manifest', 'POST'): 'dm_authoring',
    # Session lifecycle and mutable runtime controls.
    ('sessions.start_new_session', 'POST'): 'dm_runtime_control',
    ('sessions.end_game_session', 'POST'): 'dm_runtime_control',
    # Player-owned save imports remain allowed, while the importer strips
    # operator-only campaign-pack state for non-operators.
    ('sessions.import_session', 'POST'): 'player_action',
    ('sessions.update_session', 'PATCH'): 'dm_runtime_control',
    ('sessions.archive_session', 'POST'): 'dm_runtime_control',
    ('sessions.restore_session', 'POST'): 'dm_runtime_control',
    ('sessions.delete_session', 'DELETE'): 'dm_runtime_control',
    ('sessions.update_session_content_settings', 'PATCH'): 'dm_runtime_control',
    ('sessions.update_session_content_settings', 'POST'): 'dm_runtime_control',
    ('sessions.get_session_campaign_pack_commentary', 'GET'): 'debug_read',
    ('sessions.update_session_campaign_pack_progress', 'POST'): 'dm_runtime_control',
    # Map and segment authoring. Activating a segment changes live runtime state.
    ('maps.create_map', 'POST'): 'dm_authoring',
    ('maps.update_map', 'PUT'): 'dm_authoring',
    ('maps.update_map', 'PATCH'): 'dm_authoring',
    ('segments.create_segment', 'POST'): 'dm_authoring',
    ('segments.activate_segment', 'POST'): 'dm_runtime_control',
    ('segments.update_segment', 'PUT'): 'dm_authoring',
    ('segments.update_segment', 'PATCH'): 'dm_authoring',
    ('segments.delete_segment', 'DELETE'): 'dm_authoring',
    # Existing bestiary and combat operator boundaries share the same guard.
    ('creatures.create_campaign_bestiary_entry', 'POST'): 'dm_authoring',
    ('creatures.generate_campaign_bestiary_pack', 'POST'): 'dm_authoring',
    ('creatures.start_session_combat', 'POST'): 'dm_runtime_control',
    ('creatures.plan_session_enemy_intents', 'POST'): 'dm_runtime_control',
    ('creatures.apply_session_combat_morale_event', 'POST'): 'dm_runtime_control',
    ('creatures.check_session_combat_end', 'POST'): 'dm_runtime_control',
    ('creatures.apply_session_combat_changes', 'POST'): 'dm_runtime_control',
    ('creatures.get_session_combat_debug', 'GET'): 'dm_runtime_control',
    # Global provider configuration exposes operational diagnostics and can
    # mutate process-wide runtime state.
    ('runtime_config.llm_config', 'GET'): 'debug_read',
    ('runtime_config.update_llm_config', 'PATCH'): 'admin_workspace',
    ('runtime_config.update_llm_config', 'POST'): 'admin_workspace',
    # Operational telemetry can expose provider, failure, and usage details.
    ('system.metrics_snapshot', 'GET'): 'debug_read',
    ('system.metrics_prometheus', 'GET'): 'debug_read',
    ('system.beta_summary', 'GET'): 'debug_read',
    ('system.beta_slo_summary', 'GET'): 'debug_read',
    ('system.beta_incidents', 'GET'): 'debug_read',
    ('system.beta_session_quality', 'GET'): 'debug_read',
    ('system.beta_audits', 'GET'): 'debug_read',
    ('system.beta_support_bundle', 'GET'): 'debug_read',
}


SOCKET_EVENT_CAPABILITIES: dict[str, Capability] = {
    'join_session': 'player_read',
    'set_turn_control': 'dm_runtime_control',
    'music_control': 'player_action',
    'leave_session': 'player_action',
    'typing_status': 'player_action',
    'send_message': 'player_action',
    'resolve_clarification': 'player_action',
}


def actor_capabilities(
    *,
    account_id: int | None,
    is_workspace_admin: bool,
    credential_present: bool,
    global_operator: bool,
) -> set[Capability]:
    """Resolve capabilities for REST or Socket.IO actors from the same policy."""
    if account_id is None:
        if global_operator:
            return set(WORKSPACE_ADMIN_CAPABILITIES)
        if credential_present:
            return set(PLAYER_CAPABILITIES)
        return set(LOCAL_OPERATOR_CAPABILITIES)
    if is_workspace_admin:
        return set(WORKSPACE_ADMIN_CAPABILITIES)
    return set(PLAYER_CAPABILITIES)


def _request_has_auth_token() -> bool:
    return has_request_context() and bool(getattr(g, 'aidm_auth_token_present', False))


def _request_has_global_operator_token() -> bool:
    return has_request_context() and bool(getattr(g, 'aidm_global_operator_token', False))


def current_actor_capabilities() -> set[Capability]:
    """Return request-scoped capabilities without treating unauthenticated local mode as a player."""
    return actor_capabilities(
        account_id=current_account_id(),
        is_workspace_admin=current_account_is_workspace_admin(),
        credential_present=_request_has_auth_token(),
        global_operator=_request_has_global_operator_token(),
    )


def required_http_capability(endpoint: str | None, method: str | None) -> Capability | None:
    if not endpoint or not method:
        return None
    return HTTP_ENDPOINT_CAPABILITIES.get((str(endpoint), str(method).upper()))


def required_socket_capability(event_name: str) -> Capability | None:
    return SOCKET_EVENT_CAPABILITIES.get(str(event_name))


def current_actor_has_capability(capability: Capability) -> bool:
    if capability == 'server_internal':
        return False
    return capability in current_actor_capabilities()


def capability_forbidden_response(capability: Capability, message: str | None = None):
    if current_actor_has_capability(capability):
        return None
    return error_response(
        'forbidden',
        message or f'Missing required capability: {capability}.',
        403,
        {'required_capability': capability},
    )
