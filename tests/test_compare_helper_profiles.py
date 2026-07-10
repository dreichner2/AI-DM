from __future__ import annotations

import os

from scripts.compare_helper_profiles import _dm_contract_checks, _dm_profile_environment


def _passed(case_name: str, text: str) -> set[str]:
    return {
        check['id']
        for check in _dm_contract_checks(case_name, text)
        if check['passed']
    }


def test_dm_contract_checks_accept_inventory_boundary_narration():
    passed = _passed(
        'missing_inventory_item',
        (
            'Your hand finds no healing potion; your inventory is empty. '
            'You could search the chart room instead. What do you do?'
        ),
    )

    assert passed == {
        'acknowledges_missing_potion',
        'does_not_invent_consumption_or_healing',
        'does_not_claim_an_inventory_or_hp_change',
    }


def test_dm_contract_checks_reject_invented_inventory_use():
    passed = _passed(
        'missing_inventory_item',
        'You drink the healing potion and regain 7 HP.',
    )

    assert 'acknowledges_missing_potion' not in passed
    assert 'does_not_invent_consumption_or_healing' not in passed


def test_dm_contract_checks_accept_pending_group_roll_gate():
    passed = _passed(
        'pending_group_roll_gate',
        (
            "The rune pattern remains incomplete and the stone does not move. Ember's result stands, "
            'but Mira still needs to make the Arcana check.'
        ),
    )

    assert passed == {
        'asks_only_for_miras_missing_check',
        'keeps_gate_unresolved',
        'does_not_request_embers_roll_again',
    }


def test_dm_contract_checks_accept_spatial_and_resolved_roll_contracts():
    spatial = _passed(
        'spatial_player_agency',
        'You cannot reach Mira through the locked solid door. Do you call to her or try to unlock it?',
    )
    resolved = _passed(
        'resolved_roll_progression',
        'With an 18, you notice a hair-thin silver wire and trace it to the western astrolabe.',
    )

    assert len(spatial) == 3
    assert len(resolved) == 4


def test_dm_contract_checks_accept_narrative_scene_progression():
    passed = _passed(
        'narrative_scene_progression',
        (
            'Cold blue light flickers beneath the dusty glass. Without touching the lantern, you notice '
            'a crescent sigil, and its narrow glow is pointing due west toward the shadowed arch. No roll is needed.'
        ),
    )

    assert len(passed) == 5


def test_dm_profile_environment_routes_gpt_56_without_persisting(monkeypatch):
    monkeypatch.setenv('AIDM_LLM_PROVIDER', 'codex_cli')
    monkeypatch.setenv('AIDM_LLM_MODEL', 'gpt-5.5-medium')
    monkeypatch.setenv('AIDM_CODEX_REASONING_EFFORT', 'high')

    with _dm_profile_environment('codex_56_sol_medium') as supported:
        assert supported is True
        assert os.environ['AIDM_LLM_MODEL'] == 'gpt-5.6-sol'
        assert os.environ['AIDM_CODEX_REASONING_EFFORT'] == 'medium'

    assert os.environ['AIDM_LLM_MODEL'] == 'gpt-5.5-medium'
    assert os.environ['AIDM_CODEX_REASONING_EFFORT'] == 'high'


def test_dm_profile_environment_routes_gpt_56_high(monkeypatch):
    monkeypatch.setenv('AIDM_LLM_MODEL', 'gpt-5.5-medium')
    monkeypatch.setenv('AIDM_CODEX_REASONING_EFFORT', 'medium')

    with _dm_profile_environment('codex_56_sol_high') as supported:
        assert supported is True
        assert os.environ['AIDM_LLM_MODEL'] == 'gpt-5.6-sol'
        assert os.environ['AIDM_CODEX_REASONING_EFFORT'] == 'high'
        assert os.environ['AIDM_CODEX_TIMEOUT_SECONDS'] == '300'


def test_dm_profile_environment_routes_luna_medium(monkeypatch):
    monkeypatch.setenv('AIDM_LLM_MODEL', 'gpt-5.5-medium')
    monkeypatch.setenv('AIDM_CODEX_REASONING_EFFORT', 'high')

    with _dm_profile_environment('codex_56_luna_medium') as supported:
        assert supported is True
        assert os.environ['AIDM_LLM_MODEL'] == 'gpt-5.6-luna'
        assert os.environ['AIDM_CODEX_REASONING_EFFORT'] == 'medium'
        assert os.environ['AIDM_CODEX_TIMEOUT_SECONDS'] == '240'


def test_dm_profile_environment_routes_terra_light_fast(monkeypatch):
    monkeypatch.setenv('AIDM_CODEX_REASONING_EFFORT', 'high')
    monkeypatch.setenv('AIDM_CODEX_SERVICE_TIER', 'default')

    with _dm_profile_environment('codex_56_terra_light_fast') as supported:
        assert supported is True
        assert os.environ['AIDM_LLM_MODEL'] == 'gpt-5.6-terra'
        assert os.environ['AIDM_CODEX_REASONING_EFFORT'] == 'low'
        assert os.environ['AIDM_CODEX_SERVICE_TIER'] == 'priority'

    assert os.environ['AIDM_CODEX_REASONING_EFFORT'] == 'high'
    assert os.environ['AIDM_CODEX_SERVICE_TIER'] == 'default'
