from __future__ import annotations

import json
import subprocess
import sys

from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest


def _valid_pack() -> dict:
    return {
        'packId': 'lint_pack',
        'title': 'Lint Pack',
        'locations': [{'id': 'start', 'name': 'Start'}],
        'startingState': {'locationId': 'start'},
        'checkpoints': [
            {
                'id': 'cp_start',
                'title': 'Start',
                'terminal': True,
                'playerTitle': 'Start',
            }
        ],
    }


def test_campaign_pack_linter_reports_hidden_record_visible_at_start(app):
    pack = {
        **_valid_pack(),
        'clues': [
            {
                'id': 'clue_secret',
                'title': 'Secret Clue',
                'visibleAtStart': True,
                'hiddenToPlayers': True,
            }
        ],
    }

    with app.app_context():
        result = lint_campaign_pack_manifest(pack, workspace_id='owner')

    assert result['ok'] is False
    assert any(issue['code'] == 'hidden_record_visible_at_start' for issue in result['issues'])


def test_campaign_pack_linter_reports_graph_and_dependency_warnings(app):
    pack = {
        **_valid_pack(),
        'checkpoints': [
            {'id': 'cp_start', 'title': 'Start', 'nextCheckpointIds': ['cp_end']},
            {'id': 'cp_end', 'title': 'End', 'terminal': True},
            {'id': 'cp_orphan', 'title': 'Orphan', 'terminal': True},
        ],
        'dependencies': [{'packId': 'shared_rules', 'versionRange': '^1'}],
    }

    with app.app_context():
        result = lint_campaign_pack_manifest(pack, workspace_id='owner')

    assert result['ok'] is True
    assert result['graph']['reachable'] == ['cp_end', 'cp_start']
    assert any(issue['code'] == 'unreachable_checkpoint' for issue in result['issues'])
    assert any(issue['code'] == 'pack_dependencies_require_library_resolution' for issue in result['issues'])


def test_campaign_pack_linter_cli_prints_json(tmp_path, app):
    pack_path = tmp_path / 'lint-pack.json'
    pack_path.write_text(json.dumps(_valid_pack()), encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            'scripts/aidm_pack.py',
            'lint',
            str(pack_path),
            '--json',
        ],
        check=False,
        cwd='/Users/danny/Developer/AIDM-main',
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload['ok'] is True
    assert payload['summary']['packId'] == 'lint_pack'
