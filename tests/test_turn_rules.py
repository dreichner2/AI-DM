from aidm_server.database import db
from aidm_server.models import DmTurn, safe_json_dumps
from aidm_server.rules import RuleHint
from aidm_server.turn_rules import (
    apply_pending_resolution_hint,
    build_roll_prompt,
    latest_pending_turn,
    pending_turn_by_id,
    response_mentions_roll_request,
)
from tests.helpers import seed_world_campaign_player_session


def test_turn_rules_find_and_resolve_latest_pending_turn(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        older = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I inspect the first seal.',
            outcome_status='deferred',
            rule_type='lore',
            confidence=0.7,
            rules_hint=safe_json_dumps({'dc_hint': '14'}, {}),
        )
        newer = DmTurn(
            session_id=ids['session_id'],
            campaign_id=ids['campaign_id'],
            player_id=ids['player_id'],
            player_input='I inspect the second seal.',
            outcome_status='deferred',
            rule_type='stealth',
            confidence=0.8,
            rules_hint=safe_json_dumps({'dc_hint': '16'}, {}),
        )
        db.session.add_all([older, newer])
        db.session.commit()

        pending = latest_pending_turn(ids['session_id'], ids['player_id'])
        assert pending is not None
        assert pending.turn_id == newer.turn_id
        assert pending_turn_by_id(ids['session_id'], ids['player_id'], older.turn_id).turn_id == older.turn_id

        hint = RuleHint(
            requires_roll=True,
            roll_type='check',
            dc_hint=None,
            reason='Typed roll',
            confidence=0.2,
            roll_value=17,
            outcome_deferred=False,
        )
        resolved_turn, resolved_turn_id = apply_pending_resolution_hint(
            ids['session_id'],
            ids['player_id'],
            hint,
            older.turn_id,
        )

        assert resolved_turn is not None
        assert resolved_turn_id == older.turn_id
        assert hint.roll_type == 'lore'
        assert hint.dc_hint == '14'
        assert hint.confidence == 0.7
        assert hint.reason == f'Resolved pending lore from turn {older.turn_id}'


def test_turn_rules_build_roll_prompt_and_detect_roll_requests():
    prompt = build_roll_prompt(
        RuleHint(
            requires_roll=True,
            roll_type='lore',
            dc_hint='15 (base 13, INT 8 mod -1)',
            reason='Needs lore',
            confidence=1.0,
            roll_value=None,
            outcome_deferred=True,
        ),
        pending_turn_id=22,
    )

    assert prompt.startswith('Resolve pending turn 22:')
    assert 'Intelligence (Investigation/Arcana) check' in prompt
    assert 'DC 15 (base 13, INT 8 mod -1)' in prompt
    assert 'Include the -1 modifier' in prompt
    assert 'I roll a d20-1: 14 = 13' in prompt
    assert response_mentions_roll_request('Please roll a d20 for the ward.')
    assert response_mentions_roll_request(
        'Roll a Longbow attack for Legoless: `d20 + 4` against AC 13.'
    )
    assert not response_mentions_roll_request(
        "The Right-Flank Raider's attack roll of 17 hits Legoless's AC 13."
    )
    assert not response_mentions_roll_request('Right-Flank Raider rolls a 17 and hits Legoless.')
    assert not response_mentions_roll_request("On a hit, you'll roll `1d8 + 2` piercing damage.")
    assert not response_mentions_roll_request('The ward glows silently.')
