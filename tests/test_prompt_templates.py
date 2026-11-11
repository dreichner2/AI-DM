from __future__ import annotations

from aidm_server.prompt_templates import (
    ACTIVE_DM_SYSTEM_PROMPT_VERSION,
    CANON_EXTRACTION_RESPONSE_SCHEMA,
    CANON_EXTRACTION_SYSTEM_MESSAGE,
    DM_SYSTEM_MESSAGE,
    DM_SYSTEM_MESSAGE_V1,
    DM_SYSTEM_MESSAGE_V2,
    DM_SYSTEM_MESSAGE_V3,
    DM_SYSTEM_PROMPTS,
    ENGINE_BRIEF_RULES,
    PROMPT_TEMPLATE_VERSION,
    build_canon_extraction_request,
    build_dm_generate_request,
    build_dm_system_message,
    build_dm_stream_request,
    parse_dm_user_prompt,
    render_engine_brief,
)


def test_dm_stream_request_snapshot():
    request = build_dm_stream_request(
        user_input='open the iron gate',
        context='{"campaign":"Smoke"}',
        speaking_player={'character_name': 'Ember', 'player_id': 7},
        rules_hint={'requires_roll': False},
    )

    assert PROMPT_TEMPLATE_VERSION == 'v3'
    assert request.system_message == DM_SYSTEM_MESSAGE
    assert 'brief character color' in request.system_message
    assert 'campaign_pack_director' in request.system_message
    assert 'mainQuestGeneration is "pack_only"' in request.system_message
    assert 'Do not make a player character choose goals' in request.system_message
    assert 'exact d20 modifier' in request.system_message
    assert 'CONTEXT.dormant_threads' in request.system_message
    assert 'Do not narrate explicit sexual content' in request.system_message
    assert request.prompt == (
        '\nCurrent speaker: Ember (character ID: 7; this is the character, not the account profile).\n'
        'CONTEXT:\n{"campaign":"Smoke"}\n\n\n'
        'RULES_HINT:\n{"requires_roll": false}\n'
        'PLAYER INPUT:\nopen the iron gate\n'
    )


def test_live_system_prompt_has_response_shape_after_narrative_style():
    message = build_dm_system_message()

    assert message.index('NARRATIVE STYLE') < message.index('RESPONSE SHAPE') < message.index('CONTENT BOUNDARIES')
    assert 'Write like a novelist with a stopwatch.' in message
    assert 'Ordinary turns: 60 to 140 words in one to three short paragraphs.' in message
    assert 'Never exceed 260 words.' in message
    assert '"Make a Wisdom (Perception) check, DC 13."' in message
    assert 'RULES_HINT.authoritative_roll has an outcome' in message
    assert 'When RULES_HINT.message_kind is "opening", open the adventure from the OPENING BRIEF' in message
    assert 'When asking what the player does next' not in message
    assert 'story_threads' not in message
    assert 'authored_segments' not in message
    assert '5. emergent_memory as canon that arose through play.' in message
    assert '6. triggered_segments as optional inspiration, not rails.' in message


def test_opening_stream_request_adds_brief_and_replaces_player_input():
    brief = {'scene': {'name': 'The Lantern Post', 'visibleDetails': ['A bell rings.']}}
    request = build_dm_stream_request(
        user_input='Please narrate the opening scene for this campaign.',
        context='{"campaign":"Smoke"}',
        speaking_player={'character_name': 'Ember', 'player_id': 7},
        rules_hint={'requires_roll': False, 'message_kind': 'opening', 'opening_brief': brief},
    )

    assert request.prompt == (
        '\nCurrent speaker: Ember (character ID: 7; this is the character, not the account profile).\n'
        'CONTEXT:\n{"campaign":"Smoke"}\n\n'
        'OPENING BRIEF:\n{"scene":{"name":"The Lantern Post","visibleDetails":["A bell rings."]}}\n\n'
        '\nRULES_HINT:\n{"requires_roll": false, "message_kind": "opening"}\n'
        'PLAYER INPUT:\n(The adventure begins.)\n'
    )
    assert parse_dm_user_prompt(request.prompt) == {
        'speaker': 'Ember',
        'context': {'campaign': 'Smoke'},
        'opening_brief': brief,
        'rules_hint': {'requires_roll': False, 'message_kind': 'opening'},
        'player_input': '(The adventure begins.)',
    }


def test_stream_request_omits_state_summary_from_rendered_rules_hint():
    rules_hint = {
        'requires_roll': False,
        'state_pipeline': {'currentStateSummary': {'hp': 10}, 'playerMessage': 'I wait.'},
    }

    request = build_dm_stream_request(user_input='I wait.', context='{}', rules_hint=rules_hint)
    parsed = parse_dm_user_prompt(request.prompt)

    assert parsed['rules_hint'] == {'requires_roll': False, 'state_pipeline': {'playerMessage': 'I wait.'}}
    assert rules_hint['state_pipeline']['currentStateSummary'] == {'hp': 10}
    assert parsed['speaker'] is None
    assert parsed['opening_brief'] is None


BRIEF = {
    'did': ['Took the Caravan Strongbox Key.'],
    'notDone': ['Search the lead wagon'],
    'here': {
        'objects': [
            {'name': 'Lead Wagon', 'labels': ['Search the lead wagon']},
            {'name': 'Caravan Ledger', 'labels': []},
        ],
        'items': [],
        'waysOnward': ['The Old North Road'],
        'shops': ["Mara's Counter"],
    },
    'place': 'The Lantern Post Inn',
}


def test_stream_request_renders_the_engine_brief_directly_above_player_input():
    rules_hint = {'requires_roll': False, 'state_pipeline': {'playerMessage': 'x', 'engineBrief': BRIEF}}

    request = build_dm_stream_request(user_input='I pocket the key and search the wagon.', context='{}', rules_hint=rules_hint)

    assert request.prompt.endswith(
        '\nRULES_HINT:\n{"requires_roll": false, "state_pipeline": {"playerMessage": "x"}}\n\n'
        'ENGINE THIS TURN\n'
        'Did: Took the Caravan Strongbox Key.\n'
        'Not done: Search the lead wagon\n'
        'Here (only the engine decides these): objects Lead Wagon: Search the lead wagon | Caravan Ledger: nothing offered; '
        "loose items none; ways onward The Old North Road; shops Mara's Counter\n"
        'The party is at The Lantern Post Inn.\n\n'
        f'{ENGINE_BRIEF_RULES}\n\n'
        'PLAYER INPUT:\nI pocket the key and search the wagon.\n'
    )
    assert parse_dm_user_prompt(request.prompt) == {
        'speaker': None,
        'context': {},
        'opening_brief': None,
        'rules_hint': {'requires_roll': False, 'state_pipeline': {'playerMessage': 'x'}},
        'player_input': 'I pocket the key and search the wagon.',
    }
    assert rules_hint['state_pipeline']['engineBrief'] == BRIEF


def test_engine_brief_names_the_roll_this_turn_resolves_and_is_absent_from_the_opening():
    pipeline = {'engineBrief': {**BRIEF, 'did': [], 'notDone': []}}
    resolved = {
        'requires_roll': True,
        'resolved_turn_id': 4,
        'authoritative_roll': {'rule_type': 'stealth', 'total': 14, 'dc': 13, 'outcome': 'success'},
        'state_pipeline': pipeline,
    }
    free = {'requires_roll': True, 'authoritative_roll': {'die': 'd20', 'total': 10, 'free': True}, 'state_pipeline': pipeline}

    assert render_engine_brief(resolved).startswith('ENGINE THIS TURN\nDid: Stealth roll: 14 against DC 13 — success.\nNot done: nothing\n')
    assert 'Did: Rolled d20 on their own: 10. No check was asked for; nothing depends on it.\n' in render_engine_brief(free)
    assert render_engine_brief({'message_kind': 'opening', 'state_pipeline': pipeline}) is None
    assert render_engine_brief({'requires_roll': False}) is None


def test_parse_dm_user_prompt_round_trips_multiline_input_and_rejects_other_layouts():
    request = build_dm_stream_request(
        user_input='I say:\n"Open up."',
        context='{"a":1}',
        speaking_player={'character_name': 'Sir Ember (the Bold)', 'player_id': 3},
    )

    parsed = parse_dm_user_prompt(request.prompt)

    assert parsed['speaker'] == 'Sir Ember (the Bold)'
    assert parsed['rules_hint'] == {}
    assert parsed['player_input'] == 'I say:\n"Open up."'
    assert parse_dm_user_prompt('hello') is None
    assert parse_dm_user_prompt('\nCONTEXT:\nnot json\n\nPLAYER INPUT:\nx\n') is None
    assert parse_dm_user_prompt(build_dm_generate_request('x', '{}').prompt) is None


def test_active_dm_system_prompt_version():
    assert ACTIVE_DM_SYSTEM_PROMPT_VERSION == 'v3'
    assert DM_SYSTEM_PROMPTS['v1'] == DM_SYSTEM_MESSAGE_V1
    assert DM_SYSTEM_PROMPTS['v2'] == DM_SYSTEM_MESSAGE_V2
    assert DM_SYSTEM_PROMPTS['v3'] == DM_SYSTEM_MESSAGE_V3
    assert DM_SYSTEM_MESSAGE_V2 != DM_SYSTEM_MESSAGE_V1
    assert DM_SYSTEM_MESSAGE_V3 != DM_SYSTEM_MESSAGE_V2
    assert "You are AIDM's live Dungeon Master" in DM_SYSTEM_MESSAGE_V2
    assert 'campaign_pack_director' in DM_SYSTEM_MESSAGE_V3
    assert 'mainQuestGeneration is "pack_only"' in DM_SYSTEM_MESSAGE_V3
    assert 'Do not make a player character choose goals' in DM_SYSTEM_MESSAGE_V3
    assert 'CONTEXT.dormant_threads' in DM_SYSTEM_MESSAGE_V3
    assert DM_SYSTEM_MESSAGE == DM_SYSTEM_MESSAGE_V3


def test_build_dm_system_message_adds_content_rating_and_tone_tags():
    message = build_dm_system_message(content_rating='mature', tone_tags=['noir', 'court intrigue'])

    assert 'Use mature adventure-fantasy boundaries' in message
    assert 'TONE TAGS' in message
    assert 'noir, court intrigue' in message
    assert 'CONTEXT.dormant_threads' in message


def test_dm_generate_request_snapshot():
    request = build_dm_generate_request(
        user_input='listen at the chapel door',
        context='{"location":"Ash Chapel"}',
        rules_hint={'resolved_turn_id': 42, 'roll_value': 18},
    )

    assert request.system_message == DM_SYSTEM_MESSAGE
    assert request.prompt == (
        'CONTEXT:\n{"location":"Ash Chapel"}\n'
        '\n\nRULES_HINT:\n{"resolved_turn_id": 42, "roll_value": 18}\n'
        '\nPLAYER ACTION:\nlisten at the chapel door\n'
    )


def test_canon_extraction_request_snapshot():
    request = build_canon_extraction_request(
        context={'entities': [{'name': 'Liora'}], 'facts': []},
        campaign_title='Smoke Campaign',
        player_input='I enter the chapel.',
        dm_output='Liora reveals the ash gate.',
        speaking_player_name=None,
        triggered_segments=[{'title': 'Ash Gate'}],
    )

    assert request.system_message == CANON_EXTRACTION_SYSTEM_MESSAGE
    assert request.prompt == (
        'CURRENT CANON:\n'
        '{\n'
        '  "entities": [\n'
        '    {\n'
        '      "name": "Liora"\n'
        '    }\n'
        '  ],\n'
        '  "facts": []\n'
        '}\n\n'
        'PLAYER CHARACTER: Unknown\n'
        'CAMPAIGN TITLE: Smoke Campaign\n'
        'TURN INPUT:\nI enter the chapel.\n\n'
        'DM OUTPUT:\nLiora reveals the ash gate.\n\n'
        'TRIGGERED SEGMENTS:\n'
        '[\n'
        '  {\n'
        '    "title": "Ash Gate"\n'
        '  }\n'
        ']\n\n'
        'Return JSON of the form:\n'
        f'{CANON_EXTRACTION_RESPONSE_SCHEMA}'
    )


def test_roll_card_records_stay_out_of_the_narration_prompt():
    from aidm_server.prompt_templates import prompt_rules_hint, rolls_shown

    hint = {
        'requires_roll': False,
        'state_pipeline': {
            'storyRolls': [{'id': 't1-1', 'kind': 'attack', 'total': 19}],
            'managedDomains': ['combat'],
        },
    }

    rendered = prompt_rules_hint(hint)

    # The narrator still knows cards are shown, but sees no number from them.
    assert rendered['state_pipeline']['storyRolls'] == [{'kind': 'attack'}]
    assert rolls_shown(rendered) is True
    assert rendered['state_pipeline']['managedDomains'] == ['combat']
    # The stored hint is untouched.
    assert hint['state_pipeline']['storyRolls'][0]['total'] == 19
