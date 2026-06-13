from __future__ import annotations

from aidm_server.turn_engine import _state_application_event_details


def _details_for(applied_changes: list[dict]):
    return _state_application_event_details(
        stage='dm_response',
        player_id=30,
        affected_player_ids=[30],
        inventory_changes_applied=[],
        character_state_changes_applied=[],
        state_log={'lines': []},
        applied_changes=applied_changes,
    )


def test_state_application_details_flag_world_state_changes():
    details = _details_for([{'id': 'add_quest', 'type': 'quest.add', 'questId': 'find_missing_sailor'}])

    assert details['world_state_changed'] is True
    assert details['snapshot_changed'] is True

    canon_details = _state_application_event_details(
        stage='state_applied',
        player_id=30,
        affected_player_ids=[30],
        inventory_changes_applied=[],
        character_state_changes_applied=[],
        state_log={'lines': []},
        applied_changes=[{'id': 'add_quest', 'type': 'quest.add', 'questId': 'find_missing_sailor'}],
        state_applied=True,
    )
    assert canon_details['state_applied'] is True
    assert canon_details['world_state_changed'] is True
    assert canon_details['snapshot_changed'] is True


def test_state_application_details_do_not_flag_mechanical_only_changes():
    details = _details_for([{'id': 'add_item', 'type': 'inventory.add', 'actorId': 'player_30'}])

    assert 'world_state_changed' not in details
    assert 'snapshot_changed' not in details


def test_state_application_details_flag_campaign_pack_progress_changes():
    details = _state_application_event_details(
        stage='state_applied',
        player_id=30,
        affected_player_ids=[30],
        inventory_changes_applied=[],
        character_state_changes_applied=[],
        state_log={'lines': []},
        applied_changes=[],
        campaign_pack_progress_changed=True,
    )

    assert details['campaign_pack_progress_changed'] is True
    assert details['snapshot_changed'] is True
