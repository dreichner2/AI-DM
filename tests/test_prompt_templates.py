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
    PROMPT_TEMPLATE_VERSION,
    build_canon_extraction_request,
    build_dm_generate_request,
    build_dm_system_message,
    build_dm_stream_request,
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
