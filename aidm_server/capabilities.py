from __future__ import annotations

from typing import Any, Literal

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

ExplicitHTTPAccess = Literal['public', 'self_service']

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
    # Player-visible world and campaign reads.
    ('worlds.list_worlds', 'GET'): 'player_read',
    ('worlds.get_world', 'GET'): 'player_read',
    ('campaigns.list_campaigns', 'GET'): 'player_read',
    ('campaigns.get_campaign', 'GET'): 'player_read',
    ('campaigns.get_campaign_workspace', 'GET'): 'player_read',
    ('campaigns.export_campaign_chronicle', 'GET'): 'player_read',
    ('campaigns.get_campaign_canon', 'GET'): 'debug_read',
    ('campaigns.list_example_campaign_packs', 'GET'): 'player_read',
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
    ('sessions.get_session_recap', 'GET'): 'player_read',
    ('sessions.list_campaign_sessions', 'GET'): 'player_read',
    ('sessions.export_session', 'GET'): 'player_read',
    ('sessions.export_session_chronicle', 'GET'): 'player_read',
    ('sessions.get_session_log', 'GET'): 'player_read',
    ('sessions.get_session_events', 'GET'): 'player_read',
    ('sessions.get_session_state', 'GET'): 'player_read',
    ('sessions.get_session_content_settings', 'GET'): 'player_read',
    ('sessions.get_session_campaign_pack_progress', 'GET'): 'player_read',
    ('sessions.update_session', 'PATCH'): 'dm_runtime_control',
    ('sessions.archive_session', 'POST'): 'dm_runtime_control',
    ('sessions.restore_session', 'POST'): 'dm_runtime_control',
    ('sessions.delete_session', 'DELETE'): 'dm_runtime_control',
    ('sessions.resolve_session_recovery', 'POST'): 'dm_runtime_control',
    ('sessions.update_session_content_settings', 'PATCH'): 'dm_runtime_control',
    ('sessions.update_session_content_settings', 'POST'): 'dm_runtime_control',
    ('sessions.get_session_campaign_pack_commentary', 'GET'): 'debug_read',
    ('sessions.update_session_campaign_pack_progress', 'POST'): 'dm_runtime_control',
    # Map and segment authoring. Activating a segment changes live runtime state.
    ('maps.create_map', 'POST'): 'dm_authoring',
    ('maps.list_maps', 'GET'): 'player_read',
    ('maps.get_map', 'GET'): 'player_read',
    ('maps.update_map', 'PUT'): 'dm_authoring',
    ('maps.update_map', 'PATCH'): 'dm_authoring',
    ('segments.create_segment', 'POST'): 'dm_authoring',
    ('segments.list_segments', 'GET'): 'dm_authoring',
    ('segments.get_segment', 'GET'): 'dm_authoring',
    ('segments.activate_segment', 'POST'): 'dm_runtime_control',
    ('segments.update_segment', 'PUT'): 'dm_authoring',
    ('segments.update_segment', 'PATCH'): 'dm_authoring',
    ('segments.delete_segment', 'DELETE'): 'dm_authoring',
    # Existing bestiary and combat operator boundaries share the same guard.
    ('creatures.create_campaign_bestiary_entry', 'POST'): 'dm_authoring',
    ('creatures.generate_campaign_bestiary_pack', 'POST'): 'dm_authoring',
    ('creatures.get_core_bestiary', 'GET'): 'player_read',
    ('creatures.get_campaign_bestiary', 'GET'): 'debug_read',
    ('creatures.get_region_bestiary', 'GET'): 'debug_read',
    ('creatures.resolve_creature', 'POST'): 'player_action',
    ('creatures.generate_creature', 'POST'): 'player_action',
    ('creatures.create_creature_variant_endpoint', 'POST'): 'player_action',
    ('creatures.evolve_creature_endpoint', 'POST'): 'player_action',
    ('creatures.analyze_balance', 'POST'): 'player_action',
    ('creatures.start_session_combat', 'POST'): 'dm_runtime_control',
    ('creatures.plan_session_enemy_intents', 'POST'): 'dm_runtime_control',
    ('creatures.apply_session_combat_morale_event', 'POST'): 'dm_runtime_control',
    ('creatures.check_session_combat_end', 'POST'): 'dm_runtime_control',
    ('creatures.apply_session_combat_changes', 'POST'): 'dm_runtime_control',
    ('creatures.get_session_combat_debug', 'GET'): 'dm_runtime_control',
    # Player-owned character and custom-race operations.
    ('players.handle_players', 'GET'): 'player_read',
    ('players.handle_players', 'POST'): 'player_action',
    ('players.get_player_by_id', 'GET'): 'player_read',
    ('players.repair_player_starting_loadout', 'POST'): 'player_action',
    ('players.update_player', 'PATCH'): 'player_action',
    ('players.update_player_equipment', 'PATCH'): 'player_action',
    ('players.delete_player', 'DELETE'): 'player_action',
    ('races.list_races', 'GET'): 'player_read',
    ('races.get_race', 'GET'): 'player_read',
    ('races.generate_custom_race', 'POST'): 'player_action',
    ('races.create_custom_race', 'POST'): 'player_action',
    ('races.update_custom_race', 'PATCH'): 'player_action',
    ('races.delete_custom_race', 'DELETE'): 'player_action',
    # Player onboarding, narration, and feedback.
    ('onboarding.list_pregenerated_characters', 'GET'): 'player_read',
    ('onboarding.play_now', 'POST'): 'player_action',
    ('system.actor_capabilities', 'GET'): 'player_read',
    ('system.tts_config', 'GET'): 'player_read',
    ('system.speak_text', 'POST'): 'player_action',
    ('system.submit_coherence_feedback', 'POST'): 'player_action',
    ('system.submit_bad_turn_feedback', 'POST'): 'player_action',
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


# These handlers intentionally run before normal workspace capability resolution.
# Keeping them in a separate, narrowly named inventory makes public/self-service
# access reviewable without treating a missing capability entry as permission.
HTTP_EXPLICIT_ACCESS: dict[tuple[str, str], ExplicitHTTPAccess] = {
    ('system.health_check', 'GET'): 'public',
    ('accounts.login_or_create_account', 'POST'): 'self_service',
    ('accounts.play_now_account', 'POST'): 'self_service',
    ('accounts.join_account_workspace', 'POST'): 'self_service',
    ('accounts.create_account_workspace', 'POST'): 'self_service',
    ('accounts.select_account_workspace', 'POST'): 'self_service',
    ('accounts.account_workspaces', 'GET'): 'self_service',
    ('accounts.delete_or_remove_account_workspace', 'DELETE'): 'self_service',
    ('accounts.account_me', 'GET'): 'self_service',
    ('accounts.logout_account_session', 'DELETE'): 'self_service',
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


def _http_policy_key(endpoint: str | None, method: str | None) -> tuple[str, str]:
    normalized_method = str(method or '').upper()
    # Flask supplies HEAD automatically for GET routes; it inherits the GET
    # policy instead of requiring a duplicate inventory entry.
    if normalized_method == 'HEAD':
        normalized_method = 'GET'
    return (str(endpoint or ''), normalized_method)


def explicit_http_access(endpoint: str | None, method: str | None) -> ExplicitHTTPAccess | None:
    return HTTP_EXPLICIT_ACCESS.get(_http_policy_key(endpoint, method))


def required_http_capability(endpoint: str | None, method: str | None) -> Capability | None:
    # No endpoint means Flask did not match a route, so there is no handler to
    # authorize and the normal 404 response should remain intact.
    if not endpoint:
        return None
    key = _http_policy_key(endpoint, method)
    if key in HTTP_ENDPOINT_CAPABILITIES:
        return HTTP_ENDPOINT_CAPABILITIES[key]
    if explicit_http_access(endpoint, method) is not None:
        return None
    # ``server_internal`` is never granted to a request actor. Returning it for
    # missing classifications makes runtime enforcement fail closed.
    return 'server_internal'


def validate_http_capability_inventory(app: Any) -> None:
    """Reject API route drift before the application begins serving requests."""
    route_keys: set[tuple[str, str]] = set()
    route_labels: dict[tuple[str, str], str] = {}
    invalid_early_access: list[str] = []
    for rule in app.url_map.iter_rules():
        if not str(rule.rule).startswith('/api'):
            continue
        methods = set(rule.methods or ()) - {'HEAD'}
        # Flask-generated OPTIONS responses do not invoke application handler
        # logic. An explicit OPTIONS handler does, so it needs the same
        # fail-closed policy classification as every other concrete method.
        if bool(getattr(rule, 'provide_automatic_options', False)):
            methods.discard('OPTIONS')
        for method in methods:
            key = (str(rule.endpoint), str(method).upper())
            route_keys.add(key)
            route_labels[key] = f'{method.upper()} {rule.rule} ({rule.endpoint})'
            access = explicit_http_access(*key)
            if str(rule.rule) == '/api/health' and access != 'public':
                invalid_early_access.append(f'{route_labels[key]} must be public')
            elif str(rule.rule).startswith('/api/accounts') and access != 'self_service':
                invalid_early_access.append(f'{route_labels[key]} must be self_service')

    capability_keys = set(HTTP_ENDPOINT_CAPABILITIES)
    explicit_access_keys = set(HTTP_EXPLICIT_ACCESS)
    invalid_capabilities = sorted(
        (key, value)
        for key, value in HTTP_ENDPOINT_CAPABILITIES.items()
        if value not in CAPABILITY_DESCRIPTIONS
    )
    invalid_explicit_access = sorted(
        (key, value)
        for key, value in HTTP_EXPLICIT_ACCESS.items()
        if value not in {'public', 'self_service'}
    )
    overlap = sorted(capability_keys & explicit_access_keys)
    classified_keys = capability_keys | explicit_access_keys
    missing = sorted(route_keys - classified_keys)
    stale = sorted(classified_keys - route_keys)
    if (
        not invalid_capabilities
        and not invalid_explicit_access
        and not invalid_early_access
        and not overlap
        and not missing
        and not stale
    ):
        return

    details: list[str] = []
    if invalid_capabilities:
        details.append(f'invalid capabilities={invalid_capabilities!r}')
    if invalid_explicit_access:
        details.append(f'invalid explicit access={invalid_explicit_access!r}')
    if invalid_early_access:
        details.append(f'invalid early access={sorted(invalid_early_access)!r}')
    if overlap:
        details.append(f'duplicate classifications={overlap!r}')
    if missing:
        details.append(
            'unclassified routes='
            + repr([route_labels.get(key, f'{key[1]} {key[0]}') for key in missing])
        )
    if stale:
        details.append(f'stale classifications={stale!r}')
    raise RuntimeError('Invalid HTTP capability inventory: ' + '; '.join(details))


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
