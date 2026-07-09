from __future__ import annotations

from aidm_server.services.session_state_mutation import _state_revision, expected_state_revision_from_payload


def test_expected_state_revision_preserves_explicit_zero():
    assert expected_state_revision_from_payload(
        {
            'expectedStateRevision': 0,
            'stateRevision': 9,
        }
    ) == 0
    assert expected_state_revision_from_payload(
        {
            'expectedStateRevision': None,
            'stateRevision': 9,
        }
    ) == 9


def test_snapshot_revision_prefers_explicit_camel_case_zero():
    assert _state_revision(
        {
            'stateRevision': 0,
            'state_revision': 9,
        }
    ) == 0
