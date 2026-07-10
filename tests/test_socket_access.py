from aidm_server.rate_limiter import RateLimitResult
from aidm_server.socket_access import (
    ADMIN_NOT_CONFIGURED_MESSAGE,
    ADMIN_UNAUTHORIZED_MESSAGE,
    admin_attempt_bucket_key,
    admin_passcode_is_valid,
    authorize_admin_socket_action,
    socket_message_rate_key,
)


def test_socket_rate_keys_keep_player_messages_separate_but_share_admin_attempts():
    assert socket_message_rate_key('workspace-1', 7, 11) == 'workspace-1:7:11'
    assert socket_message_rate_key('workspace-1', 7, 12) == 'workspace-1:7:12'
    assert admin_attempt_bucket_key('workspace-1', '127.0.0.1') == 'admin-passcode:workspace-1:127.0.0.1'
    assert admin_attempt_bucket_key('workspace-1', None) == 'admin-passcode:workspace-1:unknown'


def test_admin_passcode_validation_requires_both_values_and_uses_trimmed_strings():
    assert admin_passcode_is_valid(' letmein ', {'admin_passcode': ' letmein '}) is True
    assert admin_passcode_is_valid('letmein', {'admin_passcode': 'wrong'}) is False
    assert admin_passcode_is_valid('', {'admin_passcode': 'letmein'}) is False
    assert admin_passcode_is_valid('letmein', {}) is False


def test_admin_authorization_rejects_unconfigured_mode_without_consuming_rate_limit():
    calls = []

    result = authorize_admin_socket_action(
        configured_passcode=None,
        data={'admin_passcode': 'letmein'},
        workspace_id='workspace-1',
        remote_address='127.0.0.1',
        allow_rate_key=lambda key: calls.append(('rate', key)),
        passcode_validator=lambda data: calls.append(('passcode', data)),
    )

    assert result.allowed is False
    assert result.error_code == 'admin_not_configured'
    assert result.message == ADMIN_NOT_CONFIGURED_MESSAGE
    assert calls == []


def test_admin_authorization_rate_limits_before_verifying_passcode():
    passcode_checks = []

    result = authorize_admin_socket_action(
        configured_passcode='letmein',
        data={'admin_passcode': 'wrong'},
        workspace_id='workspace-1',
        remote_address='127.0.0.1',
        allow_rate_key=lambda key: RateLimitResult(allowed=False, remaining=0, reset_in_seconds=17),
        passcode_validator=lambda data: passcode_checks.append(data) or False,
    )

    assert result.allowed is False
    assert result.error_code == 'rate_limited'
    assert result.reset_in_seconds == 17
    assert passcode_checks == []


def test_admin_authorization_rejects_invalid_passcode_after_allowed_attempt():
    rate_keys = []

    result = authorize_admin_socket_action(
        configured_passcode='letmein',
        data={'admin_passcode': 'wrong'},
        workspace_id='workspace-1',
        remote_address='127.0.0.1',
        allow_rate_key=lambda key: rate_keys.append(key)
        or RateLimitResult(allowed=True, remaining=4, reset_in_seconds=30),
        passcode_validator=lambda data: False,
    )

    assert result.allowed is False
    assert result.error_code == 'admin_unauthorized'
    assert result.message == ADMIN_UNAUTHORIZED_MESSAGE
    assert rate_keys == ['admin-passcode:workspace-1:127.0.0.1']


def test_admin_attempt_bucket_never_contains_configured_or_supplied_passcodes():
    rate_keys = []

    for supplied_passcode in ('wrong-one', 'wrong-two'):
        authorize_admin_socket_action(
            configured_passcode='expected-secret',
            data={'admin_passcode': supplied_passcode},
            workspace_id='workspace-1',
            remote_address='127.0.0.1',
            allow_rate_key=lambda key: rate_keys.append(key)
            or RateLimitResult(allowed=True, remaining=4, reset_in_seconds=30),
            passcode_validator=lambda data: False,
        )

    assert rate_keys == [
        'admin-passcode:workspace-1:127.0.0.1',
        'admin-passcode:workspace-1:127.0.0.1',
    ]
    assert all('wrong-' not in key and 'expected-secret' not in key for key in rate_keys)


def test_admin_authorization_accepts_valid_passcode_after_allowed_attempt():
    result = authorize_admin_socket_action(
        configured_passcode='letmein',
        data={'admin_passcode': 'letmein'},
        workspace_id='workspace-1',
        remote_address='127.0.0.1',
        allow_rate_key=lambda key: RateLimitResult(allowed=True, remaining=4, reset_in_seconds=30),
        passcode_validator=lambda data: True,
    )

    assert result.allowed is True
    assert result.error_code is None
