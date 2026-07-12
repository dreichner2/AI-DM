from aidm_server.socket_contracts import (
    SEND_MESSAGE_REQUIRED_FIELDS,
    dm_chunk_payload,
    dm_response_end_payload,
    dm_response_start_payload,
    new_message_payload,
    roll_resolved_payload,
    roll_required_payload,
    segment_triggered_payload,
    session_log_update_payload,
    session_recovery_resolved_payload,
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


def test_validate_send_message_payload_strips_legacy_roll_outcome_fields():
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

    assert error is None
    assert payload is not None
    assert payload.action_intent['roll'] == {
        'die': 'd20',
        'mode': 'normal',
        'result_visibility': 'hidden_until_landed',
        'reason': '',
    }


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
    assert session_recovery_resolved_payload(
        session_id=4,
        turn_id=9,
        state_revision=12,
    ) == {
        'session_id': 4,
        'turn_id': 9,
        'state_revision': 12,
        'recovery_required': False,
    }
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
        remaining_player_ids=[3],
        roll_spec={
            'die': 'd20',
            'mode': 'advantage',
            'rule_type': 'attack',
            'reason': 'Longsword attack',
            'result_visibility': 'hidden_until_landed',
            'ability': {'key': 'strength', 'label': 'STR', 'score': 18, 'modifier': 4},
            'attack': {'weapon': 'private'},
        },
    ) == {
        'session_id': 4,
        'pending_turn_id': 9,
        'rule_type': 'attack',
        'dc_hint': 'DC 15',
        'prompt': 'Please roll.',
        'remaining_player_ids': [3],
        'roll_spec': {
            'die': 'd20',
            'mode': 'advantage',
            'rule_type': 'attack',
            'reason': 'Longsword attack',
            'result_visibility': 'hidden_until_landed',
            'ability': {'key': 'strength', 'label': 'STR'},
        },
    }
    assert roll_resolved_payload(
        session_id=4,
        turn_id=10,
        player_id=3,
        client_message_id='roll-10',
        pending_turn_id=9,
        roll={
            'rule_type': 'social',
            'die': 'd20',
            'mode': 'advantage',
            'rolls': [8, 17],
            'kept': 17,
            'modifier': 4,
            'total': 21,
            'reason': 'persuade the guard',
            'result_visibility': 'hidden_until_landed',
            'ability': {'key': 'charisma', 'label': 'CHA', 'score': 14, 'modifier': 2},
            'proficiency': {'bonus': 2, 'skills': ['persuasion']},
            'modifier_breakdown': {
                'ability_modifier': 2,
                'proficiency_bonus': 2,
                'wound_penalty': 0,
                'total': 4,
            },
        },
        include_private_provenance=True,
    ) == {
        'session_id': 4,
        'turn_id': 10,
        'player_id': 3,
        'client_message_id': 'roll-10',
        'pending_turn_id': 9,
        'rule_type': 'social',
        'die': 'd20',
        'mode': 'advantage',
        'rolls': [8, 17],
        'kept': 17,
        'modifier': 4,
        'total': 21,
        'reason': 'persuade the guard',
        'result_visibility': 'hidden_until_landed',
        'ability': {'key': 'charisma', 'label': 'CHA', 'score': 14, 'modifier': 2},
        'proficiency': {'bonus': 2, 'skills': ['persuasion']},
        'modifier_breakdown': {
            'ability_modifier': 2,
            'proficiency_bonus': 2,
            'wound_penalty': 0,
            'total': 4,
        },
        'authoritative': True,
    }
    public_roll = roll_resolved_payload(
        session_id=4,
        turn_id=10,
        player_id=3,
        client_message_id='roll-10',
        pending_turn_id=9,
        roll={
            'rule_type': 'social',
            'die': 'd20',
            'mode': 'advantage',
            'rolls': [8, 17],
            'kept': 17,
            'modifier': 4,
            'total': 21,
            'reason': 'persuade the guard',
            'result_visibility': 'hidden_until_landed',
            'ability': {'key': 'charisma', 'label': 'CHA', 'score': 14, 'modifier': 2},
            'proficiency': {'bonus': 2, 'skills': ['persuasion']},
            'modifier_breakdown': {
                'ability_modifier': 2,
                'proficiency_bonus': 2,
                'wound_penalty': 0,
                'total': 4,
            },
        },
        include_private_provenance=False,
    )
    assert public_roll['total'] == 21
    assert 'ability' not in public_roll
    assert 'proficiency' not in public_roll
    assert 'modifier_breakdown' not in public_roll
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
        include_private_provenance=False,
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
