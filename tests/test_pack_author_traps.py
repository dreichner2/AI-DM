"""Pack-author traps the linter reports or the engine now tolerates: story flags the pack also sets,
people nobody can meet, places without a supported scene type, speech about "the players", and item
names that lost an inner "the"."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from aidm_server.canon_inventory import apply_inventory_changes, clean_inventory_item_name, load_inventory
from aidm_server.database import db
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.models import DmTurn, Player, Session, safe_json_dumps, safe_json_loads
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest
from tests.helpers import seed_world_campaign_player_session

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pack() -> dict:
    """A clean two-place pack: the lint issues each test looks for come only from what it adds."""

    return {
        'packId': 'trap_pack',
        'title': 'Trap Pack',
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
        'storyFlags': [{'key': 'gate_open', 'when': 'The party talks the gatekeeper round.'}],
    }


def _issues(pack: dict, *, strict: bool = False) -> list[tuple[str, str, str]]:
    return [
        (issue['severity'], issue['code'], issue['path'])
        for issue in lint_campaign_pack_manifest(pack, workspace_id='owner', strict=strict)['issues']
    ]


def _issue(pack: dict, code: str, *, strict: bool = False) -> list[dict]:
    return [issue for issue in lint_campaign_pack_manifest(pack, workspace_id='owner', strict=strict)['issues'] if issue['code'] == code]


def test_the_base_pack_is_clean(app):
    with app.app_context():
        assert _issues(_pack()) == []


def _gate_lever(pack: dict) -> None:
    pack['locations'][0]['interactables'] = [
        {'id': 'gate_lever', 'name': 'Gate Lever', 'kind': 'object', 'allowedActions': ['inspect'],
         'setsFlags': {'inspect': {'gate_open': True}}}
    ]


def _gate_encounter(pack: dict) -> None:
    pack['enemies'] = [{'id': 'enemy_guard', 'name': 'Gate Guard'}]
    pack['encounters'] = [
        {'id': 'enc_gate', 'title': 'The Gate', 'enemyGroups': [{'enemyId': 'enemy_guard', 'count': 1}],
         'outcomes': {'negotiation': {'consequences': [{'type': 'flag.set', 'flagKey': 'gate_open', 'flagValue': True}]}}}
    ]


def _gate_quest(pack: dict) -> None:
    pack['quests'][0]['onComplete'] = [{'type': 'flag.set', 'flagKey': 'gate_open', 'flagValue': True}]


@pytest.mark.parametrize(
    ('add_setters', 'setter'),
    [
        (_gate_lever, 'locations[0].interactables[0].setsFlags.inspect'),
        (_gate_encounter, 'encounters[0].outcomes.negotiation.consequences[0]'),
        (_gate_quest, 'quests[0].onComplete[0]'),
        # The first place that sets it is named: objects come before encounters and quests.
        (lambda pack: [add(pack) for add in (_gate_quest, _gate_encounter, _gate_lever)], 'locations[0].interactables[0].setsFlags.inspect'),
    ],
)
def test_a_story_flag_the_pack_also_sets_is_a_warning_and_an_error_under_strict(app, add_setters, setter):
    pack = _pack()
    add_setters(pack)

    with app.app_context():
        [warning] = _issue(pack, 'story_flag_set_by_pack')
        [error] = _issue(pack, 'story_flag_set_by_pack', strict=True)
        strict_ok = lint_campaign_pack_manifest(pack, workspace_id='owner', strict=True)['ok']

    assert (warning['severity'], warning['path']) == ('warning', 'storyFlags[0].key')
    assert warning['message'] == (
        f'Story flag "gate_open" is also set by {setter}; narration may set a declared flag, '
        'so talk can set it before that does.'
    )
    assert (error['severity'], error['message']) == ('error', warning['message'])
    assert strict_ok is False


def test_lint_strict_on_the_command_line_reports_the_story_flag_as_an_error(tmp_path, app):
    pack = _pack()
    _gate_lever(pack)
    pack_path = tmp_path / 'trap-pack.json'
    pack_path.write_text(json.dumps(pack), encoding='utf-8')

    def lint(*arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, 'scripts/aidm_pack.py', 'lint', str(pack_path), *arguments],
            check=False,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )

    loose, strict = lint(), lint('--strict')

    assert loose.returncode == 0, loose.stdout
    assert 'WARNING story_flag_set_by_pack at storyFlags[0].key' in loose.stdout
    assert strict.returncode == 1, strict.stdout
    assert 'ERROR story_flag_set_by_pack at storyFlags[0].key' in strict.stdout


def _person(**fields) -> dict:
    return {'id': 'npc_hermit', 'name': 'The Hermit', 'knows': ['The road forks twice before the ford.'], **fields}


@pytest.mark.parametrize(
    ('person', 'listed_at_road', 'starting_ids', 'warned'),
    [
        (_person(), False, {}, True),
        (_person(), True, {}, False),
        (_person(visibleAtStart=True), False, {}, False),
        (_person(), False, {'knownNpcIds': ['npc_hermit']}, False),
        # Nobody can talk to a person who knows nothing, so they are not reported.
        (_person(knows=[]), False, {}, False),
        # A person held back is still met once their place lists them.
        (_person(hidden=True), True, {}, False),
        # ``hidden: true`` with no place is a deliberate never-met person (a villain kept for the finale).
        (_person(hidden=True), False, {}, False),
    ],
)
def test_a_person_with_knows_lines_whom_no_place_lists_is_reported(app, person, listed_at_road, starting_ids, warned):
    pack = _pack()
    pack['npcs'] = [person]
    if listed_at_road:
        pack['locations'][1]['npcIds'] = ['npc_hermit']
    pack['startingState'].update(starting_ids)

    with app.app_context():
        issues = _issue(pack, 'npc_listed_nowhere')

    assert issues == (
        [
            {
                'severity': 'warning',
                'code': 'npc_listed_nowhere',
                'path': 'npcs[0]',
                'message': (
                    '"The Hermit" has knows lines but is neither visibleAtStart nor in any '
                    "location's npcIds, so the party can never talk to them."
                ),
            }
        ]
        if warned
        else []
    )


@pytest.mark.parametrize(
    ('field', 'text', 'warned'),
    [
        ('description', 'The players see a gate.', True),
        ('playerDescription', 'As THE PLAYERS arrive the gate creaks.', True),
        ('description', 'The player-built cairn leans east.', False),
    ],
)
def test_a_location_text_about_the_players_is_reported(app, field, text, warned):
    pack = _pack()
    pack['locations'][1][field] = text

    with app.app_context():
        issues = _issues(pack)

    assert issues == ([('warning', 'location_description_addresses_players', f'locations[1].{field}')] if warned else [])


@pytest.mark.parametrize(
    ('location_fields', 'path'),
    [
        ({'sceneType': 'barn'}, 'locations[1].sceneType'),
        ({'sceneState': {'sceneType': 'other'}}, 'locations[1].sceneState.sceneType'),
    ],
)
def test_a_location_scene_type_outside_the_supported_set_is_a_warning(app, location_fields, path):
    pack = _pack()
    pack['locations'][1].update(location_fields)

    with app.app_context():
        [issue] = _issue(pack, 'unsupported_scene_type')

    assert (issue['severity'], issue['path']) == ('warning', path)
    assert issue['message'].endswith('the place is entered as exploration.')


def _travel_state(client, app, scene_types: dict[str, str | None]) -> dict:
    """The imported pack's snapshot with a hero seated, its places given these scene types (``None``: none).

    The road is discovered on arriving at the start, so its known record has the default type ``other``.
    """

    pack = _pack()
    pack['locations'][0]['onEnter'] = [{'type': 'location.discover', 'locationId': 'road'}]
    for location in pack['locations']:
        if scene_types.get(location['id']):
            location['sceneType'] = scene_types[location['id']]
    response = client.post('/api/campaigns/import-pack', json={'pack': pack})
    assert response.status_code == 201, response.get_json()
    with app.app_context():
        snapshot = safe_json_loads(db.session.get(Session, response.get_json()['session_id']).state_snapshot, {})
    snapshot['playerCharacters'] = [{'id': 'player_1', 'playerId': 1, 'name': 'Arden Vale', 'inventory': {'items': []}}]
    return snapshot


@pytest.mark.parametrize('scene_type', [None, 'barn'])
def test_a_place_without_a_supported_scene_type_is_entered_as_exploration(client, app, scene_type):
    state = _travel_state(client, app, {'road': scene_type})
    assert next(location['type'] for location in state['locations'] if location['id'] == 'road') == 'other'

    travel = validate_declared_actions(
        state=state,
        declared_actions=[{'id': 'go_road', 'type': 'world.travel', 'actorId': 'player_1', 'locationId': 'road'}],
        current_turn=1,
    )
    [move] = travel['immediateChanges']
    validation = validate_state_changes(state=state, changes=[move])
    applied = apply_state_changes(state, validated_changes_for_application(validation))

    assert move['sceneType'] == 'exploration'
    assert validation['rejected'] == []
    assert applied['nextState']['currentScene']['locationId'] == 'road'
    assert applied['nextState']['currentScene']['sceneType'] == 'exploration'


def test_a_move_naming_an_unsupported_scene_type_is_entered_as_exploration(client, app):
    state = _travel_state(client, app, {'road': None})
    move = {'id': 'narrated_move', 'type': 'scene.move_location', 'locationId': 'road', 'name': 'Road', 'sceneType': 'other'}

    validation = validate_state_changes(state=state, changes=[move])
    applied = apply_state_changes(state, validated_changes_for_application(validation))

    assert validation['rejected'] == []
    assert applied['nextState']['currentScene']['sceneType'] == 'exploration'


def test_a_place_keeps_its_supported_scene_type_on_arrival(client, app):
    state = _travel_state(client, app, {'road': 'mystery'})
    move = {'id': 'go_road', 'type': 'scene.move_location', 'locationId': 'road', 'name': 'Road'}

    applied = apply_state_changes(state, validated_changes_for_application(validate_state_changes(state=state, changes=[move])))

    assert applied['nextState']['currentScene']['sceneType'] == 'mystery'


@pytest.mark.parametrize(
    ('raw', 'clean'),
    [
        ('The Lantern', 'Lantern'),
        ('a Potion of Healing', 'Potion of Healing'),
        ('your Rusted Key', 'Rusted Key'),
        ('Broken Seal of the North', 'Broken Seal of the North'),
        ('The Ash-Ink Binding Quill of the Scribe', 'Ash-Ink Binding Quill of the Scribe'),
        ('Mara’s Counter Ledger', 'Mara’s Counter Ledger'),
    ],
)
def test_only_a_leading_article_is_removed_from_an_item_name(raw, clean):
    assert clean_inventory_item_name(raw) == clean


def test_an_item_saved_under_its_old_name_still_stacks_and_is_lost_by_its_full_name(app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        # Saved before the cleaning kept an inner "the".
        player.inventory = safe_json_dumps([{'name': 'Seal of North', 'quantity': 1}], [])
        turn = DmTurn(session_id=ids['session_id'], campaign_id=ids['campaign_id'], player_id=ids['player_id'], player_input='x')
        db.session.add(turn)
        db.session.flush()

        gained = apply_inventory_changes(turn, [{'action': 'acquire', 'item_name': 'Seal of the North', 'quantity': 1}])
        held = [(item['name'], item['quantity']) for item in load_inventory(player.inventory)]
        lost = apply_inventory_changes(turn, [{'action': 'lose', 'item_name': 'Seal of the North', 'quantity': 2}])
        left = load_inventory(player.inventory)

    assert gained == [{'action': 'acquire', 'item_name': 'Seal of the North', 'quantity': 1}]
    assert held == [('Seal of North', 2)]
    assert lost == [{'action': 'lose', 'item_name': 'Seal of North', 'quantity': 2}]
    assert left == []
