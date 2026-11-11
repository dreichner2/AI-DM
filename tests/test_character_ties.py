"""A hero's own threads on the character, and a pack's tie lines: the API, the ready-made heroes, schema, normaliser,
linter."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest
from aidm_server.services.pregen_characters import PREGENERATED_CHARACTER_PRESETS, pregenerated_character_payload
from aidm_server.services.tie_lines import ending_lines, normalize_tie_lines
from tests.helpers import seed_world_campaign_player_session
from tests.test_party_play import CLERIC

PACK = json.loads((Path(__file__).resolve().parents[1] / 'docs' / 'examples' / 'the_road_of_unremembered_kings_campaign.json').read_text())


def _create(client, campaign_id: int, ties: Any):
    return client.post(f'/api/players/campaigns/{campaign_id}/players', json={**CLERIC, 'ties': ties})


def _sheet(client, player_id: int) -> dict[str, Any]:
    return client.get(f'/api/players/{player_id}').get_json()['character_sheet']


def test_a_hero_is_created_and_edited_with_ties(app, client):
    ids = seed_world_campaign_player_session(app)
    created = _create(
        client,
        ids['campaign_id'],
        {
            'desire': {'kind': 'be_recognised', 'words': 'ignored for a listed desire'},
            'attachment': {'name': '  Tamsin   Ashgrove ', 'relation': 'Mother'},
            'burden': 'A temple bell you rang the night the fire spread.',
        },
    )
    assert created.status_code == 201
    player_id = created.get_json()['player_id']
    assert _sheet(client, player_id)['ties'] == {
        'desire': {'kind': 'be_recognised', 'words': 'to be recognised'},
        'attachment': {'name': 'Tamsin Ashgrove', 'relation': 'mother'},
        'burden': 'A temple bell you rang the night the fire spread.',
    }

    edited = client.patch(
        f'/api/players/{player_id}',
        json={'ties': {'desire': {'kind': 'own', 'words': 'To hear the bell ring once more'}, 'attachment': {'name': 'The bell tower', 'relation': 'a place'}}},
    )
    assert edited.status_code == 200
    assert edited.get_json()['character_sheet']['ties'] == {
        'desire': {'kind': 'own', 'words': 'To hear the bell ring once more'},
        'attachment': {'name': 'The bell tower', 'relation': 'a place'},
    }
    # The spellbook and background the sheet already held are kept.
    assert edited.get_json()['character_sheet']['spellbook'] == _sheet(client, player_id)['spellbook']

    cleared = client.patch(f'/api/players/{player_id}', json={'ties': None})
    assert 'ties' not in cleared.get_json()['character_sheet']


def test_a_name_without_a_relation_is_a_friend(app, client):
    ids = seed_world_campaign_player_session(app)
    player_id = _create(client, ids['campaign_id'], {'attachment': {'name': 'Pell'}}).get_json()['player_id']

    assert _sheet(client, player_id)['ties'] == {'attachment': {'name': 'Pell', 'relation': 'friend'}}


@pytest.mark.parametrize(
    ('ties', 'message'),
    [
        ({'attachment': {'name': 'P', 'relation': 'friend'}}, 'The name must be 2 to 40 characters.'),
        ({'attachment': {'name': 'P' * 41, 'relation': 'friend'}}, 'The name must be 2 to 40 characters.'),
        ({'attachment': {'name': 'Pell', 'relation': 'cousin'}}, 'Choose the relation from the list.'),
        ({'burden': 'x' * 121}, 'What you carry must be at most 120 characters.'),
        ({'desire': {'kind': 'own', 'words': 'x' * 81}}, 'What you want must be at most 80 characters.'),
        ({'desire': {'kind': 'to_be_king'}}, 'What you want must be one of the choices or a line of your own.'),
        ('a sister', 'ties must be an object.'),
    ],
)
def test_ties_outside_the_limits_are_refused(app, client, ties, message):
    ids = seed_world_campaign_player_session(app)

    response = _create(client, ids['campaign_id'], ties)

    assert response.status_code == 400
    assert response.get_json()['error'] == message


def test_the_ready_made_heroes_carry_their_authored_ties():
    ties = {preset.character_name: pregenerated_character_payload(preset)['character_sheet']['ties'] for preset in PREGENERATED_CHARACTER_PRESETS}

    assert {name: (tie['desire']['kind'], tie['attachment']['name'], tie['attachment']['relation'], tie['burden']) for name, tie in ties.items()} == {
        'Arden Vale': ('keep_duty', 'Edda Vale', 'sister', "The road-warden's oath you swore and never finished."),
        'Liora Quill': ('learn_forbidden', 'Master Ansel Brey', 'mentor', 'A page torn from a book you were forbidden to open.'),
        'Mara Fen': ('clear_debt', 'Pip Fen', 'brother', 'A debt to people who do not forget.'),
        'Tovan Ember': ('prove_wrong', 'Hilda Ember', 'daughter', 'The hammer you put down the day the shrine burned.'),
    }


# Pack tie lines -----------------------------------------------------------------------------------------------------


def _pack_with(tie_lines: list[dict[str, Any]]) -> dict[str, Any]:
    pack = deepcopy(PACK)
    pack['tieLines'] = tie_lines
    return pack


def _tie_issues(app, pack: dict[str, Any]) -> set[tuple[str, str, str]]:
    with app.app_context():
        issues = lint_campaign_pack_manifest(pack, workspace_id='owner', strict=True)['issues']
    return {(issue['severity'], issue['code'], issue['path']) for issue in issues if issue['path'].startswith('tieLines') or issue['code'] == 'invalid_campaign_pack_schema'}


def test_the_play_now_pack_lints_clean_with_its_tie_lines(app):
    with app.app_context():
        assert lint_campaign_pack_manifest(PACK, workspace_id='owner', strict=True)['issues'] == []
    assert [line['moment'] for line in normalize_tie_lines(PACK['tieLines'])] == [
        'object', 'object', 'encounter', 'long_rest', 'ending', 'ending', 'ending', 'ending', 'encounter',
    ]


@pytest.mark.parametrize(
    ('line', 'path'),
    [
        ({'id': 'a', 'kind': 'seeking', 'at': {'objectId': 'no_such_thing', 'action': 'inspect'}, 'text': '{name}'}, 'tieLines[0].at.objectId'),
        ({'id': 'a', 'kind': 'seeking', 'at': {'objectId': 'inn_caravan_ledger', 'action': 'break'}, 'text': '{name}'}, 'tieLines[0].at.action'),
        ({'id': 'a', 'kind': 'seeking', 'at': {'locationId': 'loc_nowhere'}, 'text': '{name}'}, 'tieLines[0].at.locationId'),
        ({'id': 'a', 'kind': 'seeking', 'at': {'encounterId': 'enc_nothing'}, 'text': '{name}'}, 'tieLines[0].at.encounterId'),
        ({'id': 'a', 'kind': 'seeking', 'at': {'ending': 'ward_forgotten'}, 'text': '{name}'}, 'tieLines[0].at.ending'),
        ({'id': 'a', 'kind': 'burden', 'at': {'ending': 'ward_renewed'}, 'text': '{burden}'}, 'tieLines[0].kind'),
        ({'id': 'a', 'kind': 'seeking', 'at': {'longRest': True}, 'text': 'You remember: {burden}'}, 'tieLines[0].text'),
        ({'id': 'a', 'kind': 'burden', 'at': {'longRest': True}, 'text': 'You think of {name}.'}, 'tieLines[0].text'),
        ({'id': 'a', 'kind': 'desire', 'desire': 'keep_duty', 'at': {'longRest': True}, 'text': '{relation}'}, 'tieLines[0].text'),
    ],
)
def test_the_linter_errors_on_a_line_that_could_never_be_told_as_written(app, line, path):
    assert ('error', 'invalid_tie_line', path) in _tie_issues(app, _pack_with([line]))


def test_the_linter_errors_on_a_repeated_id(app):
    line = {'id': 'a', 'kind': 'burden', 'at': {'longRest': True}, 'text': '{burden}'}

    assert ('error', 'invalid_tie_line', 'tieLines[1].id') in _tie_issues(app, _pack_with([line, line]))


def test_the_schema_refuses_a_line_with_no_moment_or_an_unknown_kind(app):
    for line in (
        {'id': 'a', 'kind': 'seeking', 'at': {'somewhere': 'x'}, 'text': '{name}'},
        {'id': 'a', 'kind': 'rival', 'at': {'longRest': True}, 'text': '{name}'},
        {'id': 'a', 'kind': 'desire', 'desire': 'to_be_king', 'at': {'longRest': True}, 'text': '{hero}'},
    ):
        assert any(code == 'invalid_campaign_pack_schema' for _severity, code, _path in _tie_issues(app, _pack_with([line]))), line


def test_the_normaliser_keeps_well_formed_lines_only():
    lines = normalize_tie_lines(
        [
            {'id': 'Ledger Name', 'kind': 'seeking', 'at': {'objectId': 'inn_caravan_ledger', 'action': 'Inspect'}, 'text': '{name}'},
            {'id': 'ledger_name', 'kind': 'seeking', 'at': {'longRest': True}, 'text': 'again'},
            {'id': 'no_moment', 'kind': 'seeking', 'at': {}, 'text': 'x'},
            {'id': 'wish', 'kind': 'desire', 'desire': 'clear_debt', 'at': {'longRest': True}, 'text': '{hero} counts the debt.'},
            'not a line',
        ]
    )

    assert lines == [
        {'id': 'ledger_name', 'kind': 'seeking', 'at': {'objectId': 'inn_caravan_ledger', 'action': 'inspect'}, 'moment': 'object', 'text': '{name}'},
        {'id': 'wish', 'kind': 'desire', 'at': {'longRest': True}, 'moment': 'long_rest', 'text': '{hero} counts the debt.', 'desire': 'clear_debt'},
    ]


def test_ending_lines_are_told_once_per_hero_whose_seeking_line_fired():
    pack = {
        'tieLines': normalize_tie_lines(PACK['tieLines']),
        'threads': [
            {'lineId': 'tie_burden_first_rest', 'heroId': 'player_1', 'kind': 'burden', 'values': {'hero': 'Arden Vale', 'burden': 'x'}},
            {'lineId': 'tie_ledger_name', 'heroId': 'player_1', 'kind': 'seeking', 'values': {'hero': 'Arden Vale', 'name': 'Edda Vale'}},
            {'lineId': 'tie_memorial_name', 'heroId': 'player_1', 'kind': 'seeking', 'values': {'hero': 'Arden Vale', 'name': 'Edda Vale'}},
            {'lineId': 'tie_scribe_binding', 'heroId': 'player_2', 'kind': 'seeking', 'values': {'hero': 'Seren', 'name': 'Tamsin Ashgrove'}},
        ],
    }
    claimed = {'when': {'flagKey': 'ward_claimed', 'equals': True}}

    assert ending_lines(pack, claimed) == [
        'Edda Vale kneels with the rest as you pass. You tell yourself that is not why you did it.',
        'Tamsin Ashgrove kneels with the rest as you pass. You tell yourself that is not why you did it.',
    ]
    assert ending_lines({**pack, 'threads': pack['threads'][:1]}, claimed) == []


@pytest.mark.parametrize(
    ('inspires', 'issue'),
    [
        ({'break': 'a secret uncovered'}, ('warning', 'inspires_unsupported_action')),
        ({'inspect': ''}, ('error', 'invalid_inspires')),
    ],
)
def test_the_linter_checks_what_an_object_inspires(app, inspires, issue):
    pack = deepcopy(PACK)
    pack['locations'][0]['interactables'][0]['inspires'] = inspires

    with app.app_context():
        issues = lint_campaign_pack_manifest(pack, workspace_id='owner')['issues']

    assert issue in {(entry['severity'], entry['code']) for entry in issues}
