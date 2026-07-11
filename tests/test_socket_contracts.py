from aidm_server.socket_contracts import (
    SEND_MESSAGE_REQUIRED_FIELDS,
    dm_chunk_payload,
    dm_response_end_payload,
    dm_response_start_payload,
    new_message_payload,
    roll_required_payload,
    segment_triggered_payload,
    session_log_update_payload,
    socket_error_payload,
    turn_duplicate_payload,
    turn_status_payload,
    validate_send_message_payload,
)


def test_validate_send_message_payload_normalizes_valid_message():
    payload, error = validate_send_message_payload(
        {
            'session_id': '12',
            'campaign_id': '34',
            'world_id': '',
            'player_id': '56',
            'message': '  I inspect the door.  ',
            'client_message_id': 'client-1',
        }
    )

    assert error is None
    assert payload is not None
    assert payload.session_id == 12
    assert payload.campaign_id == 34
    assert payload.world_id == 0
    assert payload.player_id == 56
    assert payload.user_input == 'I inspect the door.'
    assert payload.client_message_id == 'client-1'
    assert payload.manual_segment_ids == set()


def test_validate_send_message_payload_reports_missing_fields():
    payload, error = validate_send_message_payload({'session_id': 1})

    assert payload is None
    assert error is not None
    assert error.error_code == 'validation_error'
    assert error.message == 'Missing required data.'
    assert error.details == {
        'required_fields': SEND_MESSAGE_REQUIRED_FIELDS,
        'missing_fields': ['campaign_id', 'message', 'player_id'],
    }
    assert error.telemetry_payload == {'missing_fields': ['campaign_id', 'message', 'player_id']}


def test_validate_send_message_payload_rejects_invalid_action_intent():
    payload, error = validate_send_message_payload(
        {
            'session_id': 1,
            'campaign_id': 2,
            'player_id': 3,
            'message': 'Bad roll.',
            'action_intent': {
                'kind': 'roll',
                'roll': {
                    'die': 'd20',
                    'mode': 'normal',
                    'modifier': 1,
                    'rolls': [8],
                    'kept': 8,
                    'total': 99,
                },
            },
        }
    )

    assert payload is None
    assert error is not None
    assert error.error_code == 'validation_error'
    assert 'roll.total' in error.message
    assert error.telemetry_payload['field'] == 'action_intent'


def test_validate_send_message_payload_rejects_manual_segment_override():
    payload, error = validate_send_message_payload(
        {
            'session_id': 1,
            'campaign_id': 2,
            'player_id': 3,
            'message': 'Trigger the hidden segment.',
            'manual_trigger_segment_ids': ['9', 'not-an-id'],
        }
    )

    assert payload is None
    assert error is not None
    assert error.error_code == 'manual_segment_override_disabled'
    assert error.telemetry_suffix == 'manual_segment_override_disabled'
    assert error.telemetry_payload == {'session_id': 1, 'player_id': 3}


def test_socket_error_payload_uses_shared_error_shape():
    payload = socket_error_payload('validation_error', 'Bad socket payload.', {'field': 'message'})

    assert payload == {
        'error': 'Bad socket payload.',
        'error_code': 'validation_error',
        'details': {'field': 'message'},
    }


def test_outgoing_turn_payload_contracts_are_stable():
    rules_hint = {'requires_roll': True, 'roll_type': 'attack'}

    assert dm_response_start_payload(
        session_id=1,
        turn_id=2,
        requires_roll=True,
        rules_hint=rules_hint,
        context_version='v2',
    ) == {
        'session_id': 1,
        'turn_id': 2,
        'requires_roll': True,
        'rules_hint': rules_hint,
        'context_version': 'v2',
    }
    assert dm_chunk_payload(
        chunk='A blade flashes.',
        session_id=1,
        turn_id=2,
        requires_roll=True,
        rules_hint=rules_hint,
        context_version='v2',
    ) == {
        'chunk': 'A blade flashes.',
        'session_id': 1,
        'turn_id': 2,
        'requires_roll': True,
        'rules_hint': rules_hint,
        'context_version': 'v2',
    }
    assert dm_response_end_payload(
        session_id=1,
        turn_id=2,
        requires_roll=True,
        rules_hint=rules_hint,
        context_version='v2',
        ok=False,
        text='A partial answer.',
        error='stream failed',
    ) == {
        'session_id': 1,
        'turn_id': 2,
        'requires_roll': True,
        'rules_hint': rules_hint,
        'context_version': 'v2',
        'ok': False,
        'text': 'A partial answer.',
        'error': 'stream failed',
    }


def test_outgoing_status_and_side_effect_payload_contracts_are_stable():
    assert session_log_update_payload(4, 9) == {'session_id': 4, 'turn_id': 9}
    assert turn_status_payload(4, 9, 'saved', {'stage': 'dm_response'}) == {
        'session_id': 4,
        'turn_id': 9,
        'status': 'saved',
        'details': {'stage': 'dm_response'},
    }
    assert turn_duplicate_payload(4, 9, 'client-1') == {
        'session_id': 4,
        'turn_id': 9,
        'client_message_id': 'client-1',
    }
    assert roll_required_payload(
        session_id=4,
        pending_turn_id=9,
        rule_type='attack',
        dc_hint='DC 15',
        prompt='Please roll.',
    ) == {
        'session_id': 4,
        'pending_turn_id': 9,
        'rule_type': 'attack',
        'dc_hint': 'DC 15',
        'prompt': 'Please roll.',
    }
    assert segment_triggered_payload(
        segment_id=7,
        title='Ash Gate',
        description='The gate opens.',
        reason='keyword',
        trigger_spec={'trigger_type': 'keywords'},
    ) == {
        'segment_id': 7,
        'title': 'Ash Gate',
        'description': 'The gate opens.',
        'reason': 'keyword',
        'trigger_spec': {'trigger_type': 'keywords'},
    }


def test_new_message_payload_contract_is_stable():
    payload = new_message_payload(
        message='I inspect the door.',
        speaker='Ember',
        turn_id=12,
        requires_roll=False,
        rules_hint={'requires_roll': False},
        context_version='v2',
        action_intent={'kind': 'action'},
        client_message_id='client-12',
    )

    assert payload == {
        'message': 'I inspect the door.',
        'speaker': 'Ember',
        'turn_id': 12,
        'requires_roll': False,
        'rules_hint': {'requires_roll': False},
        'context_version': 'v2',
        'action_intent': {'kind': 'action'},
        'client_message_id': 'client-12',
    }
