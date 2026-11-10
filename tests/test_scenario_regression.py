from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCENARIO_SCRIPT = REPO_ROOT / 'scripts' / 'scenario_regression.py'


def test_scenario_regression_defaults_to_isolated_stubbed_runtime(tmp_path):
    local_db = tmp_path / 'should_not_be_created.db'
    report_path = tmp_path / 'scenario-report.json'
    env = os.environ.copy()
    env.update(
        {
            'PYTHONPATH': str(REPO_ROOT),
            'AIDM_DATABASE_URI': f'sqlite:///{local_db}',
            'AIDM_LLM_PROVIDER': 'deepseek',
            'AIDM_LLM_MODEL': 'deepseek-v4-pro',
            'AIDM_DEEPSEEK_API_KEY': 'should-not-be-used',
        }
    )

    result = subprocess.run(
        [sys.executable, str(SCENARIO_SCRIPT), '--json-output', str(report_path)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )

    assert result.returncode == 0, f'STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}'
    assert 'Scenario regression passed' in result.stdout
    assert not local_db.exists()

    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['provider'] == 'fallback'
    assert report['model'] == 'scenario-regression-v1'
    assert report['scenario_count'] == 7
    scenarios = {scenario['scenario']: scenario for scenario in report['scenarios']}
    assert {
        'opening_scene_plumbing',
        'impossible_action_boundary',
        'combat_requires_roll',
        'inventory_item_use',
        'campaign_checkpoint_trigger',
        'npc_continuity',
        'canon_memory_recall',
    } == set(scenarios)
    assert scenarios['combat_requires_roll']['requires_roll'] is True
    assert 'streamed opening text was persisted unchanged' in scenarios['opening_scene_plumbing']['assertions']
    assert 'checkpoint segment triggered' in scenarios['campaign_checkpoint_trigger']['assertions']
    assert all(scenario['provider'] == 'fallback' for scenario in report['scenarios'])
    assert all(scenario['model'] == 'scenario-regression-v1' for scenario in report['scenarios'])
