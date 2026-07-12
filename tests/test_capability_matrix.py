from __future__ import annotations

import pytest

from aidm_server.capabilities import (
    HTTP_ENDPOINT_CAPABILITIES,
    HTTP_EXPLICIT_ACCESS,
    SOCKET_EVENT_CAPABILITIES,
    explicit_http_access,
    required_http_capability,
    validate_http_capability_inventory,
)


def test_every_api_route_has_an_explicit_capability(app):
    route_keys: set[tuple[str, str]] = set()
    missing: list[tuple[str, str, str]] = []

    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith('/api'):
            continue
        methods = set(rule.methods or ()) - {'HEAD'}
        if rule.provide_automatic_options:
            methods.discard('OPTIONS')
        for method in methods:
            key = (rule.endpoint, method)
            route_keys.add(key)
            if key not in HTTP_ENDPOINT_CAPABILITIES and key not in HTTP_EXPLICIT_ACCESS:
                missing.append((method, rule.rule, rule.endpoint))

    assert missing == []
    stale_entries = sorted((set(HTTP_ENDPOINT_CAPABILITIES) | set(HTTP_EXPLICIT_ACCESS)) - route_keys)
    assert stale_entries == []
    validate_http_capability_inventory(app)


def test_unclassified_http_endpoint_requires_ungrantable_internal_capability():
    assert required_http_capability('new_blueprint.unreviewed_route', 'POST') == 'server_internal'
    assert required_http_capability(None, 'GET') is None


def test_public_and_account_self_service_routes_are_explicitly_allowlisted():
    assert HTTP_EXPLICIT_ACCESS[('system.health_check', 'GET')] == 'public'
    assert HTTP_EXPLICIT_ACCESS[('accounts.login_or_create_account', 'POST')] == 'self_service'
    assert explicit_http_access('system.health_check', 'GET') == 'public'
    assert explicit_http_access('accounts.login_or_create_account', 'POST') == 'self_service'
    assert required_http_capability('system.health_check', 'GET') is None
    assert required_http_capability('system.health_check', 'HEAD') is None
    assert required_http_capability('accounts.login_or_create_account', 'POST') is None


def test_unclassified_api_route_fails_closed_at_runtime(app):
    app.add_url_rule(
        '/api/unclassified-test',
        endpoint='unclassified.test_route',
        view_func=lambda: {'unsafe': True},
        methods=['GET'],
    )

    response = app.test_client().get('/api/unclassified-test')

    assert response.status_code == 403
    assert response.get_json()['details']['required_capability'] == 'server_internal'


def test_unmatched_api_path_preserves_normal_not_found_response(app):
    response = app.test_client().get('/api/not-a-real-route')

    assert response.status_code == 404


def test_startup_inventory_validation_rejects_unclassified_api_route(app):
    app.add_url_rule(
        '/api/startup-unclassified-test',
        endpoint='unclassified.startup_test_route',
        view_func=lambda: {'unsafe': True},
        methods=['PATCH'],
    )

    with pytest.raises(RuntimeError, match='unclassified routes=.*startup-unclassified-test'):
        validate_http_capability_inventory(app)


def test_explicit_options_handler_is_not_excluded_from_startup_inventory(app):
    app.add_url_rule(
        '/api/explicit-options-test',
        endpoint='unclassified.explicit_options_test_route',
        view_func=lambda: {'unsafe': True},
        methods=['OPTIONS'],
        provide_automatic_options=False,
    )

    with pytest.raises(RuntimeError, match='unclassified routes=.*explicit-options-test'):
        validate_http_capability_inventory(app)


def test_unclassified_explicit_options_handler_fails_closed_at_runtime(app):
    app.add_url_rule(
        '/api/runtime-explicit-options-test',
        endpoint='unclassified.runtime_explicit_options_test_route',
        view_func=lambda: {'unsafe': True},
        methods=['OPTIONS'],
        provide_automatic_options=False,
    )

    response = app.test_client().open('/api/runtime-explicit-options-test', method='OPTIONS')

    assert response.status_code == 403
    assert response.get_json()['details']['required_capability'] == 'server_internal'


def test_unclassified_account_route_does_not_bypass_runtime_guard(app):
    app.add_url_rule(
        '/api/accounts/unclassified-test',
        endpoint='accounts.unclassified_test_route',
        view_func=lambda: {'unsafe': True},
        methods=['GET'],
    )

    response = app.test_client().get('/api/accounts/unclassified-test')

    assert response.status_code == 403
    assert response.get_json()['details']['required_capability'] == 'server_internal'


def test_health_bypass_fails_closed_when_its_public_classification_is_missing(app, monkeypatch):
    monkeypatch.delitem(HTTP_EXPLICIT_ACCESS, ('system.health_check', 'GET'))

    response = app.test_client().get('/api/health')

    assert response.status_code == 403
    assert response.get_json()['details']['required_capability'] == 'server_internal'


def test_sensitive_read_routes_are_explicitly_operator_scoped():
    assert HTTP_ENDPOINT_CAPABILITIES[('segments.list_segments', 'GET')] == 'dm_authoring'
    assert HTTP_ENDPOINT_CAPABILITIES[('segments.get_segment', 'GET')] == 'dm_authoring'
    assert HTTP_ENDPOINT_CAPABILITIES[('campaigns.get_campaign_canon', 'GET')] == 'debug_read'
    assert HTTP_ENDPOINT_CAPABILITIES[('creatures.get_campaign_bestiary', 'GET')] == 'debug_read'
    assert HTTP_ENDPOINT_CAPABILITIES[('creatures.get_region_bestiary', 'GET')] == 'debug_read'
    assert HTTP_ENDPOINT_CAPABILITIES[('creatures.get_core_bestiary', 'GET')] == 'player_read'
    assert HTTP_ENDPOINT_CAPABILITIES[('campaigns.list_installed_campaign_packs', 'GET')] == 'dm_authoring'
    assert HTTP_ENDPOINT_CAPABILITIES[('campaigns.get_installed_campaign_pack', 'GET')] == 'dm_authoring'
    assert HTTP_ENDPOINT_CAPABILITIES[('sessions.get_session_campaign_pack_commentary', 'GET')] == 'debug_read'
    assert HTTP_ENDPOINT_CAPABILITIES[('runtime_config.llm_config', 'GET')] == 'debug_read'
    assert HTTP_ENDPOINT_CAPABILITIES[('system.metrics_snapshot', 'GET')] == 'debug_read'
    assert HTTP_ENDPOINT_CAPABILITIES[('system.metrics_prometheus', 'GET')] == 'debug_read'
    assert HTTP_ENDPOINT_CAPABILITIES[('system.beta_summary', 'GET')] == 'debug_read'
    assert HTTP_ENDPOINT_CAPABILITIES[('system.beta_slo_summary', 'GET')] == 'debug_read'
    assert HTTP_ENDPOINT_CAPABILITIES[('system.beta_incidents', 'GET')] == 'debug_read'
    assert HTTP_ENDPOINT_CAPABILITIES[('system.beta_session_quality', 'GET')] == 'debug_read'
    assert HTTP_ENDPOINT_CAPABILITIES[('system.beta_audits', 'GET')] == 'debug_read'
    assert HTTP_ENDPOINT_CAPABILITIES[('system.beta_support_bundle', 'GET')] == 'debug_read'


def test_runtime_config_mutations_require_workspace_admin_capability():
    assert HTTP_ENDPOINT_CAPABILITIES[('runtime_config.update_llm_config', 'PATCH')] == 'admin_workspace'
    assert HTTP_ENDPOINT_CAPABILITIES[('runtime_config.update_llm_config', 'POST')] == 'admin_workspace'


def test_every_application_socket_event_has_an_explicit_capability(socketio):
    registered_events = set(socketio.server.handlers.get('/', {}))
    application_events = registered_events - {'connect', 'disconnect'}

    assert application_events == set(SOCKET_EVENT_CAPABILITIES)
    assert SOCKET_EVENT_CAPABILITIES['set_turn_control'] == 'dm_runtime_control'
    assert SOCKET_EVENT_CAPABILITIES['send_message'] == 'player_action'
