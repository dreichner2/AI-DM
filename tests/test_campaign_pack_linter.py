from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from aidm_server.creatures.schemas import normalize_creature_definition
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_file, lint_campaign_pack_manifest


TEST_REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = TEST_REPO_ROOT / 'docs' / 'examples'


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
            {'id': 'cp_start', 'title': 'Start', 'next_checkpoint_ids': ['cp_end'], 'encounter_ids': ['enc_bandits']},
            {'id': 'cp_end', 'title': 'End', 'terminal': True},
            {'id': 'cp_orphan', 'title': 'Orphan', 'terminal': True},
        ],
        'encounters': [
            {'id': 'enc_bandits', 'title': 'Bandit Toll', 'checkpointIds': ['cp_start'], 'enemyIds': ['enemy_bandit']},
            {'id': 'enc_unlinked', 'title': 'Unplaced Trouble', 'enemyGroups': [{'enemyId': 'enemy_bandit', 'count': 2}]},
        ],
        'enemies': [{'id': 'enemy_bandit', 'name': 'Bandit'}],
        'npcs': [{'id': 'npc_guide', 'name': 'Guide', 'visibleAtStart': True}],
        'lore': [{'id': 'lore_secret', 'title': 'Secret Lore', 'visibility': 'hidden'}],
        'dependencies': [{'packId': 'shared_rules', 'versionRange': '^1'}],
    }

    with app.app_context():
        result = lint_campaign_pack_manifest(pack, workspace_id='owner')

    assert result['ok'] is True
    assert result['graph']['reachable'] == ['cp_end', 'cp_start']
    assert any(issue['code'] == 'unreachable_checkpoint' for issue in result['issues'])
    assert any(issue['code'] == 'pack_dependencies_require_library_resolution' for issue in result['issues'])
    report = result['authoring_report']
    assert report['starting']['locationId'] == 'start'
    assert report['checkpoints']['total'] == 3
    assert report['checkpoints']['reachable'] == 2
    assert report['checkpoints']['unreachableIds'] == ['cp_orphan']
    assert report['checkpoints']['items'][0]['encounterIds'] == ['enc_bandits']
    assert report['encounters']['total'] == 2
    assert report['encounters']['linkedToCheckpoint'] == 1
    assert report['encounters']['unlinkedIds'] == ['enc_unlinked']
    assert report['visibility']['visibleAtStart']['npcs'] == ['npc_guide']
    assert report['visibility']['hiddenToPlayers']['lore'] == ['lore_secret']


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
        cwd=str(TEST_REPO_ROOT),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload['ok'] is True
    assert payload['summary']['packId'] == 'lint_pack'


def test_campaign_pack_report_cli_prints_authoring_report_json(tmp_path, app):
    pack_path = tmp_path / 'lint-pack.json'
    pack_path.write_text(json.dumps(_valid_pack()), encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            'scripts/aidm_pack.py',
            'report',
            str(pack_path),
            '--json',
        ],
        check=False,
        cwd=str(TEST_REPO_ROOT),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload['starting']['locationId'] == 'start'
    assert payload['checkpoints']['reachable'] == 1
    assert payload['collections'][0] == {
        'collection': 'locations',
        'count': 1,
        'visibleAtStartCount': 0,
        'hiddenToPlayersCount': 0,
        'visibleAtStartIds': [],
        'hiddenToPlayersIds': [],
    }


def test_example_campaign_packs_lint_and_have_reachable_checkpoints(app):
    example_paths = sorted(EXAMPLES_DIR.glob('*.json'))
    assert example_paths

    results = {}
    with app.app_context():
        for pack_path in example_paths:
            results[pack_path.name] = lint_campaign_pack_file(pack_path, workspace_id='owner')

    blocking_issues = {
        name: result['issues']
        for name, result in results.items()
        if not result['ok']
    }
    assert blocking_issues == {}

    unreachable = {}
    for name, result in results.items():
        graph = result['graph']
        missing = sorted(set(graph['nodes']) - set(graph['reachable']))
        if missing:
            unreachable[name] = missing
    assert unreachable == {}


def test_road_of_unremembered_kings_example_has_clean_authoring_surface(app):
    pack_path = EXAMPLES_DIR / 'the_road_of_unremembered_kings_campaign.json'
    pack = json.loads(pack_path.read_text(encoding='utf-8'))

    with app.app_context():
        result = lint_campaign_pack_file(pack_path, workspace_id='owner')

    assert result['ok'] is True
    assert [issue for issue in result['issues'] if issue['severity'] == 'error'] == []
    assert result['summary']['packId'] == 'original_fantasy.road_of_unremembered_kings'
    assert result['summary']['counts']['checkpoints'] == 7
    assert result['summary']['counts']['encounters'] == 6
    assert result['preview']['preview']['starting_location_id'] == 'loc_lantern_post_inn'
    assert result['preview']['preview']['starting_quest_id'] == 'quest_road_unremembered_kings'

    opening_encounter = next(encounter for encounter in pack['encounters'] if encounter['id'] == 'enc_empty_caravan_pressure')
    assert opening_encounter['enemyGroups'] == [
        {
            'enemyId': 'enemy_ashmarked_cutthroats',
            'count': 2,
            'role': 'opening_theft_cell',
        }
    ]

    enemies = pack['enemies']
    assert len(enemies) == 6
    for enemy in enemies:
        normalized = normalize_creature_definition(enemy, source='campaign_pack')
        assert normalized['stats']['maxHp'] > 10
        assert normalized['stats']['armorClass'] > 11
        assert len(normalized['abilities']) >= 3
        assert normalized['abilities'][0]['name'] != 'Strike'
        assert normalized['behavior']['tactics'] != ['Use the strongest available attack against the best target.']
        assert normalized['balance']['reviewed'] is True

    cinder_scribe = next(enemy for enemy in enemies if enemy['id'] == 'enemy_cinder_scribe')
    normalized_scribe = normalize_creature_definition(cinder_scribe, source='campaign_pack')
    assert normalized_scribe['challengeTier'] == 'boss'
    assert normalized_scribe['behavior']['primaryGoal'] == 'complete_ritual'


def _engine_clean_pack() -> dict:
    return {
        'packId': 'lint_pack',
        'title': 'Lint Pack',
        'locations': [
            {'id': 'start', 'name': 'Start', 'connectedLocationIds': ['road']},
            {'id': 'road', 'name': 'Road', 'connectedLocationIds': ['start']},
        ],
        'startingState': {'locationId': 'start', 'questId': 'quest_main'},
        'quests': [
            {
                'id': 'quest_main',
                'title': 'Main',
                'objectives': [
                    {'id': 'obj_reach_road', 'description': 'Reach the road.', 'completeWhen': {'atLocationId': 'road'}}
                ],
            }
        ],
        'checkpoints': [{'id': 'cp_start', 'title': 'Start', 'terminal': True, 'playerTitle': 'Start'}],
    }


def _objective(pack: dict) -> dict:
    return pack['quests'][0]['objectives'][0]


def _add_branch(pack: dict) -> None:
    pack['checkpoints'] = [
        {'id': 'cp_start', 'title': 'Start', 'nextCheckpointIds': ['cp_branch'], 'locationIds': ['start']},
        {'id': 'cp_branch', 'title': 'Branch', 'terminal': True, 'branchWhen': {'flagKey': 'ally_found', 'equals': True}},
    ]


ENGINE_CONTRACT_CASES = [
    (
        lambda pack: pack.update(npcs=[{'id': 'Npc-Guide', 'name': 'Guide'}]),
        'error',
        'invalid_id',
        'npcs[0].id',
    ),
    (
        lambda pack: pack['locations'][0].update(connectedLocationIds=['road', 'nowhere']),
        'error',
        'unknown_reference',
        'locations[0].connectedLocationIds',
    ),
    (
        lambda pack: _objective(pack).update(completeWhen={'atLocationId': 'castle'}),
        'error',
        'unknown_reference',
        'quests[0].objectives[0].completeWhen.atLocationId',
    ),
    (
        lambda pack: pack['checkpoints'][0].update(completeWhen={'segmentIds': ['seg_missing']}),
        'error',
        'unknown_reference',
        'checkpoints[0].completeWhen.segmentIds',
    ),
    (
        lambda pack: pack['locations'][0].update(interactables=[{'id': 'door', 'kind': 'portal'}]),
        'error',
        'invalid_interactable_catalog',
        'locations[0].sceneState.interactables[0].kind',
    ),
    (
        lambda pack: _objective(pack).update(completeWhen={'atLocationId': 'road', 'targetID': 'door'}),
        'error',
        'unknown_predicate_key',
        'quests[0].objectives[0].completeWhen.targetID',
    ),
    (
        lambda pack: _objective(pack).update(completeWhen={'allOf': [None]}),
        'error',
        'invalid_predicate_entry',
        'quests[0].objectives[0].completeWhen.allOf',
    ),
    (
        lambda pack: pack.update(segments=[{'id': 'seg_timer', 'title': 'Timer', 'trigger': {'type': 'timer'}}]),
        'error',
        'invalid_segment_trigger',
        'segments[0].trigger.type',
    ),
    (
        lambda pack: pack.update(npcs=[{'id': 'npc_guide', 'name': 'Guide', 'knows': 'x' * 4_001}]),
        'error',
        'pack_content_out_of_bounds',
        'npcs[0].knows',
    ),
    (
        lambda pack: _objective(pack).pop('completeWhen'),
        'warning',
        'objective_without_mechanics',
        'quests[0].objectives[0]',
    ),
    (
        lambda pack: pack['locations'][1].pop('connectedLocationIds'),
        'warning',
        'location_without_connections',
        'locations[1]',
    ),
    (
        _add_branch,
        'warning',
        'branch_flag_never_set',
        'checkpoints[1].branchWhen.flagKey',
    ),
    (
        lambda pack: (
            pack.update(encounters=[{'id': 'enc_toll', 'title': 'Toll', 'rewards': {'items': [{'id': 'badge', 'name': 'Badge'}]}}]),
            _objective(pack).update(completeWhen={'possessesItemId': 'badge'}),
        ),
        'warning',
        'possesses_encounter_reward_item_id',
        'quests[0].objectives[0].completeWhen.possessesItemId',
    ),
    (
        lambda pack: pack.update(directorRules={'mainQuestGeneration': 'pack_only', 'tone': 'grim'}),
        'warning',
        'unread_director_rule',
        'directorRules.tone',
    ),
    (
        lambda pack: pack['checkpoints'][0].update(completeWhen={'anyOf': ['defeat', 'The party wins the day.']}),
        'warning',
        'prose_in_complete_when',
        'checkpoints[0].completeWhen.anyOf',
    ),
    (
        lambda pack: pack['checkpoints'][0].update(completeWhen={'locationIds': ['road'], 'summary': 'Reach the road.'}),
        'warning',
        'unread_checkpoint_condition_key',
        'checkpoints[0].completeWhen.summary',
    ),
    (
        lambda pack: pack['startingState'].update(campaignFlags={'ally_found': False}),
        'warning',
        'starting_campaign_flags_alias',
        'startingState.campaignFlags',
    ),
]


def test_engine_clean_pack_has_no_lint_issues(app):
    with app.app_context():
        result = lint_campaign_pack_manifest(_engine_clean_pack(), workspace_id='owner')

    assert result['ok'] is True
    assert result['issues'] == []


@pytest.mark.parametrize(('mutate', 'severity', 'code', 'path'), ENGINE_CONTRACT_CASES)
def test_linter_reports_what_the_engine_would_ignore(app, mutate, severity, code, path):
    pack = _engine_clean_pack()
    mutate(pack)

    with app.app_context():
        result = lint_campaign_pack_manifest(pack, workspace_id='owner')

    matching = [issue for issue in result['issues'] if issue['code'] == code and issue['path'] == path]
    assert [issue['severity'] for issue in matching] == [severity], result['issues']
    assert result['ok'] is (severity == 'warning')


def test_branch_flag_set_by_an_encounter_reward_is_not_reported(app):
    pack = _engine_clean_pack()
    _add_branch(pack)
    pack['encounters'] = [
        {'id': 'enc_ally', 'title': 'Ally', 'outcomes': {'negotiation': {'flags': {'Ally Found': True}}}}
    ]

    with app.app_context():
        result = lint_campaign_pack_manifest(pack, workspace_id='owner')

    assert not any(issue['code'] == 'branch_flag_never_set' for issue in result['issues'])


def test_out_of_bounds_content_is_reported_once_per_path(app):
    pack = _engine_clean_pack()
    pack['npcs'] = [{'id': 'npc_guide', 'name': 'Guide', 'knows': 'x' * 4_001, 'secret': 'y' * 4_001}]

    with app.app_context():
        result = lint_campaign_pack_manifest(pack, workspace_id='owner')

    errors = [issue for issue in result['issues'] if issue['severity'] == 'error']
    assert [(issue['code'], issue['path']) for issue in errors] == [
        ('pack_content_out_of_bounds', 'npcs[0].knows'),
        ('pack_content_out_of_bounds', 'npcs[0].secret'),
    ]


@pytest.mark.parametrize(
    ('mutate', 'arguments', 'expected_returncode'),
    [
        (lambda pack: None, [], 0),
        (lambda pack: pack['locations'][1].pop('connectedLocationIds'), [], 0),
        (lambda pack: pack['locations'][1].pop('connectedLocationIds'), ['--strict'], 1),
        (lambda pack: pack['locations'][0].update(connectedLocationIds=['nowhere']), [], 1),
    ],
)
def test_campaign_pack_lint_cli_exit_codes(tmp_path, app, mutate, arguments, expected_returncode):
    pack = _engine_clean_pack()
    mutate(pack)
    pack_path = tmp_path / 'lint-pack.json'
    pack_path.write_text(json.dumps(pack), encoding='utf-8')

    result = subprocess.run(
        [sys.executable, 'scripts/aidm_pack.py', 'lint', str(pack_path), *arguments],
        check=False,
        cwd=str(TEST_REPO_ROOT),
        capture_output=True,
        text=True,
    )

    assert result.returncode == expected_returncode, result.stdout


def test_wording_for_an_action_the_object_does_not_support_is_a_warning(app):
    pack = _engine_clean_pack()
    pack['locations'][0]['interactables'] = [
        {
            'id': 'ledger',
            'name': 'Ledger',
            'kind': 'object',
            'actionLabels': {'inspect': 'Read the ledger', 'use': 'Use the ledger'},
            'actionText': {'inspect': 'Every name is struck through.', 'search': 'Nothing hides in it.'},
        },
        {
            'id': 'chest',
            'name': 'Chest',
            'kind': 'container',
            'allowedActions': ['search'],
            'actionLabels': {'search': 'Search the chest', 'open': 'Open the chest'},
        },
    ]

    with app.app_context():
        result = lint_campaign_pack_manifest(pack, workspace_id='owner')

    wording = [
        (issue['severity'], issue['code'], issue['path'])
        for issue in result['issues']
        if issue['code'].startswith('action_')
    ]
    assert wording == [
        ('warning', 'action_label_for_unsupported_action', 'locations[0].interactables[0].actionLabels.use'),
        ('warning', 'action_text_for_unsupported_action', 'locations[0].interactables[0].actionText.search'),
        ('warning', 'action_label_for_unsupported_action', 'locations[0].interactables[1].actionLabels.open'),
    ]
    assert result['ok'] is True
