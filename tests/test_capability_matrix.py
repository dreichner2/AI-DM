from __future__ import annotations

from aidm_server.capabilities import HTTP_ENDPOINT_CAPABILITIES, SOCKET_EVENT_CAPABILITIES


UNSAFE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
CAPABILITY_MANAGED_BLUEPRINTS = {'worlds', 'campaigns', 'sessions', 'maps', 'segments'}


def test_every_unsafe_managed_route_has_an_explicit_capability(app):
    route_keys: set[tuple[str, str]] = set()
    missing: list[tuple[str, str, str]] = []

    for rule in app.url_map.iter_rules():
        methods = set(rule.methods or ()) - {'HEAD', 'OPTIONS'}
        for method in methods:
            key = (rule.endpoint, method)
            route_keys.add(key)
            blueprint = rule.endpoint.partition('.')[0]
            if blueprint in CAPABILITY_MANAGED_BLUEPRINTS and method in UNSAFE_METHODS:
                if key not in HTTP_ENDPOINT_CAPABILITIES:
                    missing.append((method, rule.rule, rule.endpoint))

    assert missing == []
    stale_entries = sorted(set(HTTP_ENDPOINT_CAPABILITIES) - route_keys)
    assert stale_entries == []


def test_sensitive_read_routes_are_explicitly_operator_scoped():
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
