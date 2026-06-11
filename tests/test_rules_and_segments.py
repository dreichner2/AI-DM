from __future__ import annotations

from aidm_server.database import db
from aidm_server.models import CampaignSegment
from aidm_server.rules import classify_player_action
from aidm_server.segment_triggers import evaluate_segment_trigger
from tests.helpers import seed_world_campaign_player_session


def test_rules_classifier_detects_roll_requirement():
    hint = classify_player_action('I attack the goblin with my longsword')
    assert hint.requires_roll is True
    assert hint.roll_type == 'attack'
    assert hint.outcome_deferred is True
    assert hint.confidence > 0.8


def test_rules_classifier_detects_decisive_attack_wording():
    hint = classify_player_action("I slice the orc's head off")
    assert hint.requires_roll is True
    assert hint.roll_type == 'attack'
    assert hint.outcome_deferred is True


def test_rules_classifier_detects_unarmed_attack_wording():
    hint = classify_player_action('I punch a hole through the machine.')
    assert hint.requires_roll is True
    assert hint.roll_type == 'attack'
    assert hint.outcome_deferred is True


def test_rules_classifier_detects_spell_actions_as_spell_checks():
    hint = classify_player_action('I use my magic to make the dagger huge.')
    assert hint.requires_roll is True
    assert hint.roll_type == 'spell'
    assert hint.outcome_deferred is True


def test_rules_classifier_ignores_retrospective_attack_references():
    hint = classify_player_action('I was the one who killed it, so please teach me to fly first.')
    assert hint.requires_roll is False
    assert hint.roll_type is None


def test_rules_classifier_marks_resolved_when_roll_is_provided():
    hint = classify_player_action('I attack and rolled a d20: 17')
    assert hint.requires_roll is True
    assert hint.roll_value == 17
    assert hint.outcome_deferred is False


def test_rules_classifier_does_not_treat_food_roll_as_dice_check():
    hint = classify_player_action('I give Danny one roll and one gold')
    assert hint.requires_roll is False
    assert hint.roll_type is None


def test_rules_classifier_still_detects_explicit_generic_roll():
    hint = classify_player_action('I roll a d20: 19')
    assert hint.requires_roll is True
    assert hint.roll_type == 'check'
    assert hint.roll_value == 19
    assert hint.outcome_deferred is False


def test_rules_classifier_detects_thieves_tools_context():
    hint = classify_player_action("I use thieves' tools to disable the ward sigil quietly.")
    assert hint.requires_roll is True
    assert hint.roll_type == 'thieves_tools'
    assert hint.outcome_deferred is True


def test_rules_classifier_detects_bluff_social_context():
    hint = classify_player_action('I bluff the guard and impersonate a city inspector.')
    assert hint.requires_roll is True
    assert hint.roll_type == 'social'


def test_rules_classifier_detects_mobility_escape_context():
    hint = classify_player_action('I sprint to the side door and leap across the rain gutter.')
    assert hint.requires_roll is True
    assert hint.roll_type == 'mobility'


def test_segment_keywords_trigger():
    trigger_condition = '{"type":"keywords","keywords":["goblin","altar"],"match":"any"}'
    matched, reason, spec = evaluate_segment_trigger(
        trigger_condition=trigger_condition,
        player_message='I search the goblin altar for clues.',
        session_state={'current_location': 'Shrine'},
        campaign_state={'location': 'Shrine', 'current_quest': 'Find relic'},
    )
    assert matched is True
    assert reason.startswith('keywords:')
    assert spec['trigger_type'] == 'keywords'


def test_segment_trigger_non_object_json_falls_back_to_keywords():
    matched, reason, spec = evaluate_segment_trigger(
        trigger_condition='altar, gate',
        player_message='I inspect the gate.',
        session_state={},
        campaign_state={},
    )
    assert matched is True
    assert reason == 'keywords:altar,gate'
    assert spec['trigger_type'] == 'keywords'

    matched, reason, spec = evaluate_segment_trigger(
        trigger_condition='["altar"]',
        player_message='I inspect the altar.',
        session_state={},
        campaign_state={},
    )
    assert matched is False
    assert reason == 'keywords:["altar"]'
    assert spec['trigger_type'] == 'keywords'


def test_create_segment_coerces_string_false_to_false(client, app):
    ids = seed_world_campaign_player_session(app)

    response = client.post(
        '/api/segments',
        json={
            'campaign_id': ids['campaign_id'],
            'title': 'Quiet Door',
            'is_triggered': 'false',
        },
    )

    assert response.status_code == 201
    segment_id = response.get_json()['segment_id']
    with app.app_context():
        segment = db.session.get(CampaignSegment, segment_id)
        assert segment is not None
        assert segment.is_triggered is False


def test_create_segment_validates_request_body_and_fields(client, app):
    ids = seed_world_campaign_player_session(app)

    non_json_response = client.post('/api/segments', data='not-json', content_type='text/plain')
    assert non_json_response.status_code == 400
    assert non_json_response.get_json()['error_code'] == 'validation_error'

    invalid_campaign_response = client.post(
        '/api/segments',
        json={'campaign_id': 'not-an-id', 'title': 'Quiet Door'},
    )
    assert invalid_campaign_response.status_code == 400
    assert invalid_campaign_response.get_json()['error_code'] == 'validation_error'

    empty_title_response = client.post(
        '/api/segments',
        json={'campaign_id': ids['campaign_id'], 'title': '   '},
    )
    assert empty_title_response.status_code == 400
    assert empty_title_response.get_json()['error_code'] == 'validation_error'

    numeric_title_response = client.post(
        '/api/segments',
        json={'campaign_id': ids['campaign_id'], 'title': 123},
    )
    assert numeric_title_response.status_code == 400
    assert numeric_title_response.get_json()['error_code'] == 'validation_error'

    overlong_tags_response = client.post(
        '/api/segments',
        json={'campaign_id': ids['campaign_id'], 'title': 'Quiet Door', 'tags': 'x' * 501},
    )
    assert overlong_tags_response.status_code == 400
    assert overlong_tags_response.get_json()['error_code'] == 'validation_error'


def test_update_segment_rejects_ambiguous_boolean(client, app):
    ids = seed_world_campaign_player_session(app)
    create_response = client.post('/api/segments', json={'campaign_id': ids['campaign_id'], 'title': 'Quiet Door'})
    assert create_response.status_code == 201
    segment_id = create_response.get_json()['segment_id']

    response = client.patch(f'/api/segments/{segment_id}', json={'is_triggered': 'definitely'})

    assert response.status_code == 400
    assert response.get_json()['error_code'] == 'validation_error'


def test_update_segment_validates_text_fields(client, app):
    ids = seed_world_campaign_player_session(app)
    create_response = client.post('/api/segments', json={'campaign_id': ids['campaign_id'], 'title': 'Quiet Door'})
    assert create_response.status_code == 201
    segment_id = create_response.get_json()['segment_id']

    non_json_response = client.patch(f'/api/segments/{segment_id}', data='not-json', content_type='text/plain')
    assert non_json_response.status_code == 400
    assert non_json_response.get_json()['error_code'] == 'validation_error'

    empty_title_response = client.patch(f'/api/segments/{segment_id}', json={'title': '   '})
    assert empty_title_response.status_code == 400
    assert empty_title_response.get_json()['error_code'] == 'validation_error'

    numeric_description_response = client.patch(f'/api/segments/{segment_id}', json={'description': 123})
    assert numeric_description_response.status_code == 400
    assert numeric_description_response.get_json()['error_code'] == 'validation_error'


def test_list_segments_returns_404_for_missing_campaign(client):
    response = client.get('/api/segments?campaign_id=99999')

    assert response.status_code == 404
    assert response.get_json()['error_code'] == 'campaign_not_found'


def test_activate_segment_exclusive_updates_campaign_segments_in_one_request(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        first = CampaignSegment(campaign_id=ids['campaign_id'], title='First', is_triggered=True)
        second = CampaignSegment(campaign_id=ids['campaign_id'], title='Second', is_triggered=False)
        db.session.add_all([first, second])
        db.session.commit()
        first_id = first.segment_id
        second_id = second.segment_id

    response = client.post(
        '/api/segments/activate',
        json={'campaign_id': ids['campaign_id'], 'segment_id': second_id, 'exclusive': True},
    )

    assert response.status_code == 200
    payload = response.get_json()
    states = {item['segment_id']: item['is_triggered'] for item in payload['segments']}
    assert states[first_id] is False
    assert states[second_id] is True

    with app.app_context():
        assert db.session.get(CampaignSegment, first_id).is_triggered is False
        assert db.session.get(CampaignSegment, second_id).is_triggered is True
