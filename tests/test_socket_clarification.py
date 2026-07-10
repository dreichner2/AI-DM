from unittest.mock import Mock

from aidm_server.database import db
from aidm_server.models import DmTurn, safe_json_dumps
from aidm_server.socket_clarification import (
    SocketClarificationDependencies,
    clarification_action_and_option_ids,
    register_socket_clarification_events,
)
from tests.helpers import seed_world_campaign_player_session


class _SocketRegistry:
    def __init__(self):
        self.handlers = {}

    def on(self, event_name):
        def register(handler):
            self.handlers[event_name] = handler
            return handler

        return register


def _event_payload(received, name):
    for event in received:
        if event['name'] == name:
            return event['args'][0] if event['args'] else {}
    return None


def test_clarification_registration_owns_only_resolution_event():
    registry = _SocketRegistry()
    dependencies = SocketClarificationDependencies(
        state=Mock(),
        set_socket_context=Mock(),
        socket_workspace_id=Mock(),
        socket_capability_forbidden=Mock(),
        workspace_session=Mock(),
        workspace_player=Mock(),
        get_turn=Mock(),
        process_turn=Mock(),
    )

    register_socket_clarification_events(registry, dependencies)

    assert set(registry.handlers) == {'resolve_clarification'}


def test_clarification_metadata_extracts_typed_action_and_valid_options():
    action = {'id': 'act_001', 'kind': 'attack'}
    metadata = {
        'state_pipeline': {
            'clarificationRequest': {
                'originalAction': action,
                'options': [
                    {'itemId': 'great', 'label': 'Greatsword'},
                    {'itemId': 'long', 'label': 'Longsword'},
                    {'label': 'Missing identifier'},
                    'invalid option',
                ],
            }
        }
    }

    original_action, option_ids = clarification_action_and_option_ids(metadata)

    assert original_action == action
    assert option_ids == {'great', 'long'}
    assert clarification_action_and_option_ids({}) == (None, set())


def test_clarification_rejects_selection_outside_persisted_options_without_resuming_turn(
    app,
    socketio,
):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        turn = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I attack with my sword.',
            status='awaiting_clarification',
            metadata_json=safe_json_dumps(
                {
                    'action_intent': {'kind': 'interaction'},
                    'state_pipeline': {
                        'clarificationRequest': {
                            'originalAction': {'id': 'act_001', 'kind': 'attack'},
                            'options': [{'itemId': 'great', 'label': 'Greatsword'}],
                        }
                    },
                },
                {},
            ),
        )
        db.session.add(turn)
        db.session.commit()
        turn_id = turn.turn_id

    client = socketio.test_client(app, flask_test_client=app.test_client())
    client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
    client.get_received()
    client.emit(
        'resolve_clarification',
        {
            'session_id': ids['session_id'],
            'player_id': ids['player_id'],
            'turn_id': turn_id,
            'selected_item_id': 'forged-option',
        },
    )
    error = _event_payload(client.get_received(), 'error')

    assert error['error_code'] == 'clarification_invalid_selection'
    with app.app_context():
        persisted_turn = db.session.get(DmTurn, turn_id)
        assert persisted_turn.status == 'awaiting_clarification'
        assert DmTurn.query.count() == 1
