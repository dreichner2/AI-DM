from __future__ import annotations

import json
from types import SimpleNamespace

import aidm_server.game_state.extraction.post_dm_outcome_extractor as extractor_module
from aidm_server.database import db
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.extraction.post_dm_outcome_extractor import extract_post_dm_outcomes
from aidm_server.game_state.models import compact_state_for_extraction
from aidm_server.game_state.validation.validator import validate_state_changes, validated_changes_for_application
from aidm_server.llm import build_dm_context
from aidm_server.models import Campaign, Session, safe_json_dumps, safe_json_loads
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player

TRUST_WHEN = 'Mara decides she can trust the party with what she saw.'


def _pack(**overrides) -> dict:
    pack = {
        'packId': 'story_pack',
        'title': 'Story Pack',
        'startingState': {'locationId': 'inn'},
        'locations': [{'id': 'inn', 'name': 'Inn'}],
        'storyFlags': [
            {'key': 'mara_trusts_party', 'when': TRUST_WHEN},
            {'key': 'bridge_fate', 'when': 'The party decides what happens to the bridge.', 'values': ['spared', 'burned']},
        ],
        'checkpoints': [
            {'id': 'cp_inn', 'title': 'The Inn', 'locationIds': ['inn'], 'nextCheckpointIds': ['cp_trust', 'cp_alone']},
            {'id': 'cp_trust', 'title': 'Trusted', 'branchWhen': {'flagKey': 'mara_trusts_party', 'equals': True}, 'terminal': True},
            {'id': 'cp_alone', 'title': 'Alone', 'terminal': True},
        ],
    }
    pack.update(overrides)
    return pack


def _import(client, app, pack: dict | None = None) -> tuple[dict, dict]:
    response = client.post('/api/campaigns/import-pack', json={'pack': pack or _pack()})
    assert response.status_code == 201, response.get_json()
    payload = response.get_json()
    with app.app_context():
        snapshot = safe_json_loads(db.session.get(Session, payload['session_id']).state_snapshot, {})
    return payload, snapshot


def _narrated_flag(key: str, value, change_id: str = 'narrated_flag') -> dict:
    return {'id': change_id, 'type': 'flag.set', 'source': 'post_dm', 'flagKey': key, 'flagValue': value}


def test_import_stores_declared_story_flags_with_default_values(client, app):
    _payload, snapshot = _import(client, app)

    assert snapshot['campaignPack']['storyFlags'] == [
        {'key': 'mara_trusts_party', 'when': TRUST_WHEN, 'values': [True]},
        {'key': 'bridge_fate', 'when': 'The party decides what happens to the bridge.', 'values': ['spared', 'burned']},
    ]


def test_narration_may_set_only_a_declared_story_flag_to_a_declared_value(client, app):
    _payload, snapshot = _import(client, app)

    accepted = validate_state_changes(
        state=snapshot,
        changes=[_narrated_flag('mara_trusts_party', True), _narrated_flag('bridge_fate', 'burned', 'fate')],
    )
    assert accepted['rejected'] == []
    applied = apply_state_changes(snapshot, validated_changes_for_application(accepted))['nextState']
    assert applied['flags']['mara_trusts_party'] is True
    assert applied['flags']['bridge_fate'] == 'burned'

    rejected = validate_state_changes(
        state=snapshot,
        changes=[
            _narrated_flag('secret_door_open', True, 'undeclared'),
            _narrated_flag('mara_trusts_party', False, 'wrong_value'),
            _narrated_flag('mara_trusts_party', 'yes', 'wrong_type'),
            _narrated_flag('bridge_fate', 'ignored', 'not_listed'),
        ],
    )
    assert validated_changes_for_application(rejected) == []
    assert {entry['reason'] for entry in rejected['rejected']} == {'Narration cannot set authoritative gameplay flags.'}
    assert len(rejected['rejected']) == 4


def test_extraction_helper_prompt_receives_the_declared_flags(client, app, monkeypatch):
    _payload, snapshot = _import(client, app)
    prompts: list[dict] = []

    def capture_prompt(**kwargs):
        prompts.append(kwargs['state_before_dm'])
        return 'prompt'

    class FakeHelper:
        def generate(self, request):
            return SimpleNamespace(
                model='fake-helper',
                text=json.dumps(
                    {
                        'proposedChanges': [{'type': 'flag.set', 'flagKey': 'mara_trusts_party', 'flagValue': True}],
                        'uncertainChanges': [],
                        'notes': [],
                    }
                ),
            )

    monkeypatch.setattr(extractor_module, '_helper_enabled', lambda: True)
    monkeypatch.setattr(extractor_module, 'build_post_dm_prompt', capture_prompt)
    monkeypatch.setattr(extractor_module, 'get_helper_provider', lambda *args, **kwargs: FakeHelper())
    with app.app_context():
        result = extract_post_dm_outcomes(
            state_before_dm=snapshot,
            player_message='I tell Mara what we found.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='Mara exhales and finally tells you everything she saw.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=12,
        )

    story_flags = prompts[0]['storyFlags']
    assert 'flag.set' in story_flags['instruction']
    assert story_flags['flags'] == [
        {'key': 'mara_trusts_party', 'when': TRUST_WHEN, 'values': [True], 'value': None},
        {'key': 'bridge_fate', 'when': 'The party decides what happens to the bridge.', 'values': ['spared', 'burned'], 'value': None},
    ]
    proposed = [change for change in result['proposedChanges'] if change['type'] == 'flag.set']
    assert proposed[0]['flagKey'] == 'mara_trusts_party'
    assert proposed[0]['source'] == 'post_dm'


def test_heuristic_fallback_proposes_a_story_flag_the_narration_makes_true(client, app):
    _payload, snapshot = _import(client, app)
    compact = compact_state_for_extraction(snapshot)
    assert 'campaignPack' not in compact

    with app.app_context():
        made_true = extract_post_dm_outcomes(
            state_before_dm=compact,
            player_message='I show Mara the ledger.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='Mara studies the ledger for a long moment. At last she trusts your party with the truth.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=13,
        )
        negated = extract_post_dm_outcomes(
            state_before_dm=compact,
            player_message='I show Mara the ledger.',
            validated_actions={},
            already_applied_changes=[],
            dm_response='Mara does not trust your party yet.',
            recent_timeline=[],
            actor_id='player_1',
            turn_id=14,
        )

    flags = [change for change in made_true['proposedChanges'] if change['type'] == 'flag.set']
    assert [(change['flagKey'], change['flagValue'], change['source']) for change in flags] == [
        ('mara_trusts_party', True, 'post_dm')
    ]
    assert validated_changes_for_application(validate_state_changes(state=snapshot, changes=flags))
    assert not [change for change in negated['proposedChanges'] if change['type'] == 'flag.set']


def test_dm_context_lists_story_flags_with_their_conditions_and_values(client, app):
    payload, snapshot = _import(client, app)
    snapshot['flags']['bridge_fate'] = 'spared'
    with app.app_context():
        session = db.session.get(Session, payload['session_id'])
        session.state_snapshot = safe_json_dumps(snapshot, {})
        campaign = db.session.get(Campaign, payload['campaign_id'])
        db.session.commit()
        context = json.loads(build_dm_context(campaign.world_id, campaign.campaign_id, session.session_id))

    assert context['campaign_pack_director']['storyFlags'] == {
        'instruction': 'When one of these conditions becomes true in the fiction, make it unmistakable in the narration.',
        'flags': [
            {'key': 'mara_trusts_party', 'when': TRUST_WHEN, 'value': None},
            {'key': 'bridge_fate', 'when': 'The party decides what happens to the bridge.', 'value': 'spared'},
        ],
    }


def test_player_projection_never_exposes_story_flags(client, app):
    _payload, snapshot = _import(client, app)

    projected = json.dumps(filter_session_snapshot_for_player(snapshot))

    assert 'storyFlags' not in projected
    assert TRUST_WHEN not in projected


def test_linter_checks_story_flag_keys_and_counts_them_as_set_flags():
    clean = lint_campaign_pack_manifest(_pack())['issues']
    assert not any(issue['code'] == 'branch_flag_never_set' for issue in clean)

    undeclared = _pack(storyFlags=[])
    assert any(issue['code'] == 'branch_flag_never_set' for issue in lint_campaign_pack_manifest(undeclared)['issues'])

    bad_key = _pack(storyFlags=[{'key': 'Mara Trusts', 'when': TRUST_WHEN}])
    errors = [issue for issue in lint_campaign_pack_manifest(bad_key)['issues'] if issue['severity'] == 'error']
    assert [(issue['code'], issue['path']) for issue in errors] == [('invalid_story_flag_key', 'storyFlags[0].key')]
