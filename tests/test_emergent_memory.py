from __future__ import annotations

from sqlalchemy import event

from aidm_server.database import db
from aidm_server.emergent_memory import (
    apply_canon_patch,
    build_emergent_context,
    extract_canon_patch,
    refresh_session_projection,
    validate_canon_patch,
)
from aidm_server.models import (
    DmTurn,
    Player,
    Session,
    SessionState,
    StoryEntity,
    StoryFact,
    StoryThread,
    TurnCanonUpdate,
    get_or_create_session_state,
    safe_json_dumps,
    safe_json_loads,
)
from tests.helpers import seed_world_campaign_player_session


def _create_turn(app, ids, *, player_input: str, dm_output: str) -> DmTurn:
    turn = DmTurn(
        session_id=ids['session_id'],
        campaign_id=ids['campaign_id'],
        player_id=ids['player_id'],
        player_input=player_input,
        dm_output=dm_output,
        status='completed',
        outcome_status='resolved',
    )
    db.session.add(turn)
    db.session.commit()
    return db.session.get(DmTurn, turn.turn_id)


def test_alias_resolution_reuses_existing_entity(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        first_turn = _create_turn(
            app,
            ids,
            player_input='I ask Captain Liora Vale for guidance.',
            dm_output='Captain Liora Vale studies the fire and nods.',
        )
        campaign = first_turn.campaign

        patch_1 = {
            'entities': [
                {
                    'entity_type': 'npc',
                    'name': 'Captain Liora Vale',
                    'aliases': ['Liora'],
                    'summary': 'A grim captain carrying ash-soaked authority.',
                }
            ]
        }
        validated_1, rejections_1 = validate_canon_patch(first_turn, campaign, patch_1)
        assert rejections_1 == []
        apply_canon_patch(first_turn, campaign, validated_1, 'test-extractor', rejections_1)
        db.session.commit()

        second_turn = _create_turn(
            app,
            ids,
            player_input='I follow Liora into the chapel.',
            dm_output='Liora motions for silence as the bells settle.',
        )

        patch_2 = {'entities': [{'entity_type': 'npc', 'name': 'Liora', 'summary': 'Referred to by shorthand.'}]}
        validated_2, rejections_2 = validate_canon_patch(second_turn, campaign, patch_2)
        assert rejections_2 == []
        apply_canon_patch(second_turn, campaign, validated_2, 'test-extractor', rejections_2)
        db.session.commit()

        entities = StoryEntity.query.filter_by(campaign_id=ids['campaign_id'], entity_type='npc').all()
        assert len(entities) == 1
        entity = entities[0]
        aliases = safe_json_loads(entity.aliases_json, [])
        assert entity.name == 'Captain Liora Vale'
        assert 'Liora' in aliases


def test_canon_patch_does_not_create_story_entity_for_player_character(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I ask whether Seraphina can keep watch.',
            dm_output='Seraphina keeps her bow ready and listens at the archway.',
        )
        campaign = turn.campaign
        patch = {
            'entities': [
                {
                    'entity_type': 'npc',
                    'name': 'Seraphina',
                    'summary': 'The player character keeping watch.',
                }
            ],
            'facts': [
                {
                    'subject': {'entity_type': 'npc', 'name': 'Seraphina'},
                    'predicate': 'status',
                    'value_text': 'keeping watch',
                }
            ],
        }

        validated, rejections = validate_canon_patch(turn, campaign, patch)
        summary = apply_canon_patch(turn, campaign, validated, 'test-extractor', rejections)
        db.session.commit()

        facts = StoryFact.query.filter_by(campaign_id=ids['campaign_id'], predicate='status').all()

        assert validated['entities'] == []
        assert rejections[0]['reason'] == 'Player character is tracked by player state, not story NPC canon.'
        assert summary['entities_created_or_updated'] == []
        assert StoryEntity.query.filter_by(campaign_id=ids['campaign_id'], name='Seraphina').count() == 0
        assert len(facts) == 1
        assert facts[0].subject_entity_id is None


def test_conflicting_fact_requires_explicit_change_type(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        first_turn = _create_turn(
            app,
            ids,
            player_input='I enter the chapel.',
            dm_output='You enter the chapel in silence.',
        )
        campaign = first_turn.campaign

        accepted_patch = {
            'facts': [
                {
                    'predicate': 'current_location',
                    'value_text': 'Chapel',
                    'replace_existing': True,
                }
            ]
        }
        validated_1, rejections_1 = validate_canon_patch(first_turn, campaign, accepted_patch)
        assert rejections_1 == []
        apply_canon_patch(first_turn, campaign, validated_1, 'test-extractor', rejections_1)
        refresh_session_projection(ids['session_id'], campaign)
        db.session.commit()

        second_turn = _create_turn(
            app,
            ids,
            player_input='I am suddenly at the harbor.',
            dm_output='The story now says harbor without transition.',
        )
        conflicting_patch = {'facts': [{'predicate': 'current_location', 'value_text': 'Harbor'}]}
        validated_2, rejections_2 = validate_canon_patch(second_turn, campaign, conflicting_patch)
        assert validated_2['facts'] == []
        assert len(rejections_2) == 1

        apply_canon_patch(second_turn, campaign, validated_2, 'test-extractor', rejections_2)
        refresh_session_projection(ids['session_id'], campaign)
        db.session.commit()

        accepted_facts = StoryFact.query.filter_by(campaign_id=ids['campaign_id'], predicate='current_location', fact_status='accepted').all()
        assert len(accepted_facts) == 1
        assert accepted_facts[0].value_text == 'Chapel'

        latest_update = TurnCanonUpdate.query.order_by(TurnCanonUpdate.update_id.desc()).first()
        assert latest_update is not None
        assert latest_update.status == 'applied_with_rejections'


def test_subject_singleton_fact_replacement_only_supersedes_matching_subject(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        first_turn = _create_turn(
            app,
            ids,
            player_input='I study both survivors.',
            dm_output='Mira and Bob both survive the first ordeal.',
        )
        campaign = first_turn.campaign

        setup_patch = {
            'entities': [
                {'entity_type': 'npc', 'name': 'Mira'},
                {'entity_type': 'npc', 'name': 'Bob'},
            ],
            'facts': [
                {'subject': 'Mira', 'predicate': 'status', 'value_text': 'alive'},
                {'subject': 'Bob', 'predicate': 'status', 'value_text': 'wounded'},
            ],
        }
        validated_setup, setup_rejections = validate_canon_patch(first_turn, campaign, setup_patch)
        assert setup_rejections == []
        apply_canon_patch(first_turn, campaign, validated_setup, 'test-extractor', setup_rejections)
        db.session.commit()

        second_turn = _create_turn(
            app,
            ids,
            player_input='I check Mira again.',
            dm_output='Mira does not rise.',
        )
        replacement_patch = {
            'facts': [
                {
                    'subject': 'Mira',
                    'predicate': 'status',
                    'value_text': 'dead',
                    'replace_existing': True,
                }
            ]
        }
        validated_replacement, replacement_rejections = validate_canon_patch(second_turn, campaign, replacement_patch)
        assert replacement_rejections == []
        apply_canon_patch(second_turn, campaign, validated_replacement, 'test-extractor', replacement_rejections)
        db.session.commit()

        mira_facts = (
            StoryFact.query.filter_by(campaign_id=ids['campaign_id'], predicate='status')
            .join(StoryEntity, StoryEntity.entity_id == StoryFact.subject_entity_id)
            .filter(StoryEntity.name == 'Mira')
            .order_by(StoryFact.fact_id.asc())
            .all()
        )
        bob_facts = (
            StoryFact.query.filter_by(campaign_id=ids['campaign_id'], predicate='status')
            .join(StoryEntity, StoryEntity.entity_id == StoryFact.subject_entity_id)
            .filter(StoryEntity.name == 'Bob')
            .order_by(StoryFact.fact_id.asc())
            .all()
        )

        assert [fact.fact_status for fact in mira_facts] == ['superseded', 'accepted']
        assert [fact.value_text for fact in mira_facts] == ['alive', 'dead']
        assert [fact.fact_status for fact in bob_facts] == ['accepted']
        assert bob_facts[0].value_text == 'wounded'
        assert mira_facts[1].supersedes_fact_id == mira_facts[0].fact_id


def test_validate_canon_patch_batches_existing_fact_lookup(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        first_turn = _create_turn(
            app,
            ids,
            player_input='I study the witnesses.',
            dm_output='Mira is alive and the party is in the chapel.',
        )
        campaign = first_turn.campaign
        setup_patch = {
            'entities': [{'entity_type': 'npc', 'name': 'Mira'}],
            'facts': [
                {'subject': 'Mira', 'predicate': 'status', 'value_text': 'alive'},
                {'predicate': 'current_location', 'value_text': 'Chapel'},
            ],
        }
        validated_setup, setup_rejections = validate_canon_patch(first_turn, campaign, setup_patch)
        assert setup_rejections == []
        apply_canon_patch(first_turn, campaign, validated_setup, 'test-extractor', setup_rejections)
        db.session.commit()

        second_turn = _create_turn(
            app,
            ids,
            player_input='I check the witnesses again.',
            dm_output='Mira is wounded and the party reaches the harbor.',
        )
        replacement_patch = {
            'facts': [
                {'subject': 'Mira', 'predicate': 'status', 'value_text': 'wounded', 'replace_existing': True},
                {'predicate': 'current_location', 'value_text': 'Harbor', 'replace_existing': True},
            ],
        }
        story_fact_selects = []

        def count_story_fact_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
            normalized = ' '.join(statement.lower().split())
            if normalized.startswith('select') and ' from story_facts' in normalized:
                story_fact_selects.append(statement)

        event.listen(db.engine, 'before_cursor_execute', count_story_fact_selects)
        try:
            validated_replacement, replacement_rejections = validate_canon_patch(
                second_turn,
                campaign,
                replacement_patch,
            )
        finally:
            event.remove(db.engine, 'before_cursor_execute', count_story_fact_selects)

        assert replacement_rejections == []
        assert len(validated_replacement['facts']) == 2
        assert len(story_fact_selects) == 1


def test_apply_canon_patch_batches_thread_title_lookup(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I review active threads.',
            dm_output='The bell and gate threads both move forward.',
        )
        campaign = turn.campaign
        db.session.add_all(
            [
                StoryThread(campaign_id=ids['campaign_id'], title='Bell Tower', summary='Old summary.'),
                StoryThread(campaign_id=ids['campaign_id'], title='Ash Gate', summary='Old summary.'),
            ]
        )
        db.session.commit()
        patch = {
            'threads': [
                {'title': 'Bell Tower', 'summary': 'The bell tolls again.', 'priority': 3},
                {'title': 'Ash Gate', 'summary': 'The gate opens.', 'priority': 2},
            ]
        }
        story_thread_selects = []

        def count_story_thread_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
            normalized = ' '.join(statement.lower().split())
            if normalized.startswith('select') and ' from story_threads' in normalized:
                story_thread_selects.append(statement)

        event.listen(db.engine, 'before_cursor_execute', count_story_thread_selects)
        try:
            summary = apply_canon_patch(turn, campaign, patch, 'test-extractor', [])
        finally:
            event.remove(db.engine, 'before_cursor_execute', count_story_thread_selects)

        assert len(summary['threads_created_or_updated']) == 2
        assert len(story_thread_selects) == 1
        assert StoryThread.query.filter_by(campaign_id=ids['campaign_id']).count() == 2


def test_inventory_validation_rejects_abstract_or_directional_phrases(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I keep moving and listen carefully.',
            dm_output='You take the path deeper into the ruins as Liora gives you a warning.',
        )

        patch = {
            'inventory_changes': [
                {'action': 'acquire', 'item_name': 'path deeper into the ruins'},
                {'action': 'acquire', 'item_name': 'warning'},
                {'action': 'acquire', 'item_name': 'silver key'},
            ]
        }
        validated_patch, rejections = validate_canon_patch(turn, turn.campaign, patch)

        assert validated_patch['inventory_changes'] == [
            {'action': 'acquire', 'item_name': 'silver key', 'quantity': 1}
        ]
        assert len(rejections) == 2
        assert {rejection['item_name'] for rejection in rejections} == {
            'path deeper into ruins',
            'warning',
        }


def test_inventory_validation_accepts_improvised_physical_objects(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I gather whatever is useful.',
            dm_output='You pick up a stick, a rock, a rag, and a cup.',
        )

        patch = {
            'inventory_changes': [
                {'action': 'acquire', 'item_name': 'stick'},
                {'action': 'acquire', 'item_name': 'rock'},
                {'action': 'acquire', 'item_name': 'rag'},
                {'action': 'acquire', 'item_name': 'cup'},
                {'action': 'acquire', 'item_name': 'hope'},
                {'action': 'acquire', 'item_name': 'permission'},
                {'action': 'acquire', 'item_name': 'a look'},
            ]
        }
        validated_patch, rejections = validate_canon_patch(turn, turn.campaign, patch)

        assert validated_patch['inventory_changes'] == [
            {'action': 'acquire', 'item_name': 'stick', 'quantity': 1},
            {'action': 'acquire', 'item_name': 'rock', 'quantity': 1},
            {'action': 'acquire', 'item_name': 'rag', 'quantity': 1},
            {'action': 'acquire', 'item_name': 'cup', 'quantity': 1},
        ]
        assert {rejection['item_name'] for rejection in rejections} == {'hope', 'permission', 'look'}


def test_extract_canon_patch_applies_explicit_inventory_state_change(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr('aidm_server.emergent_memory._extract_with_provider', lambda *args, **kwargs: (None, None))

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I roll a d20:16',
            dm_output=(
                'You now hold a sturdy, palm-worn stick in your hand.\n\n'
                '*(State change: Player 16 **gains** 1 Stick to inventory.)*'
            ),
        )

        patch, extractor_model = extract_canon_patch(
            turn=turn,
            campaign=turn.campaign,
            dm_output=turn.dm_output,
            speaking_player_name='test',
            triggered_segments=[],
        )

        assert extractor_model in {'heuristic-v1', 'provider', 'fallback'}
        assert patch['inventory_changes'] == [
            {'action': 'acquire', 'item_name': 'Stick', 'quantity': 1}
        ]

        validated_patch, rejections = validate_canon_patch(turn, turn.campaign, patch)
        assert rejections == []
        apply_canon_patch(turn, turn.campaign, validated_patch, 'test-extractor', rejections)
        db.session.commit()

        player = db.session.get(Player, ids['player_id'])
        assert safe_json_loads(player.inventory, []) == [{'name': 'Stick', 'quantity': 1, 'weight': 0.5}]


def test_explicit_currency_state_change_adds_weighted_coin_item_and_copper(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr('aidm_server.emergent_memory._extract_with_provider', lambda *args, **kwargs: (None, None))

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I roll a d20:18',
            dm_output=(
                'With a careful, practiced motion, you sweep them into a neat palmful. '
                'Ten cold, weighty disks clink gently as they settle in your grip.\n\n'
                f'*(State change: Player {ids["player_id"]} **gains** 10 copper pieces (Ancient Copper Coins).)*'
            ),
        )

        patch, _extractor_model = extract_canon_patch(
            turn=turn,
            campaign=turn.campaign,
            dm_output=turn.dm_output,
            speaking_player_name='test',
            triggered_segments=[],
        )

        assert patch['inventory_changes'] == [
            {'action': 'acquire', 'item_name': 'Ancient Copper Coins', 'quantity': 10}
        ]

        validated_patch, rejections = validate_canon_patch(turn, turn.campaign, patch)
        assert rejections == []
        applied_summary = apply_canon_patch(turn, turn.campaign, validated_patch, 'test-extractor', rejections)
        db.session.commit()

        player = db.session.get(Player, ids['player_id'])
        assert safe_json_loads(player.inventory, []) == [
            {'name': 'Ancient Copper Coins', 'quantity': 10, 'weight': 0.02}
        ]
        stats = safe_json_loads(player.stats, {})
        assert stats['copper'] == 10
        assert applied_summary['character_state_changes_applied'][0]['currency_delta'] == {'copper': 10}


def test_provider_inventory_fact_can_verify_missed_inventory_change(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)

    def fake_provider_patch(*args, **kwargs):
        return (
            {
                'facts': [
                    {
                        'predicate': 'inventory_status',
                        'value_text': 'The character now carries 10 ancient copper coins.',
                    }
                ],
                'inventory_changes': [],
            },
            'inventory-verifier-test',
        )

    monkeypatch.setattr('aidm_server.emergent_memory._extract_with_provider', fake_provider_patch)

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I collect the coins.',
            dm_output='You collect ten ancient copper coins and close your fist around them.',
        )

        patch, extractor_model = extract_canon_patch(
            turn=turn,
            campaign=turn.campaign,
            dm_output=turn.dm_output,
            speaking_player_name='test',
            triggered_segments=[],
        )

        assert extractor_model == 'inventory-verifier-test'
        assert patch['inventory_changes'] == [
            {'action': 'acquire', 'item_name': 'ancient copper coins', 'quantity': 10}
        ]


def test_provider_inventory_fact_without_text_evidence_is_ignored(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)

    def fake_provider_patch(*args, **kwargs):
        return (
            {
                'facts': [
                    {
                        'predicate': 'inventory_status',
                        'value_text': 'The character now carries 1 flawless diamond.',
                    }
                ],
                'inventory_changes': [{'action': 'acquire', 'item_name': 'flawless diamond', 'quantity': 1}],
            },
            'inventory-verifier-test',
        )

    monkeypatch.setattr('aidm_server.emergent_memory._extract_with_provider', fake_provider_patch)

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I check the empty hollow.',
            dm_output='You find nothing new in the hollow.',
        )

        patch, _extractor_model = extract_canon_patch(
            turn=turn,
            campaign=turn.campaign,
            dm_output=turn.dm_output,
            speaking_player_name='test',
            triggered_segments=[],
        )

        assert patch['inventory_changes'] == []


def test_extract_canon_patch_uses_resolved_pending_item_intent(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr('aidm_server.emergent_memory._extract_with_provider', lambda *args, **kwargs: (None, None))

    with app.app_context():
        pending_turn = _create_turn(
            app,
            ids,
            player_input='test tries to pick up stick',
            dm_output='The stick is wedged tight. Roll to work it free.',
        )
        pending_turn.metadata_json = safe_json_dumps(
            {
                'action_intent': {
                    'kind': 'item',
                    'inventory_action': 'pick_up',
                    'item': {'name': 'stick', 'quantity': 1},
                    'cost_gold': 0,
                }
            },
            {},
        )
        db.session.commit()

        roll_turn = _create_turn(
            app,
            ids,
            player_input='I roll a d20:16',
            dm_output='You now hold a sturdy stick in your hand.',
        )
        roll_turn.metadata_json = safe_json_dumps(
            {
                'resolved_turn_id': pending_turn.turn_id,
                'action_intent': {'kind': 'roll'},
            },
            {},
        )
        db.session.commit()

        patch, _extractor_model = extract_canon_patch(
            turn=roll_turn,
            campaign=roll_turn.campaign,
            dm_output=roll_turn.dm_output,
            speaking_player_name='test',
            triggered_segments=[],
        )

        assert patch['inventory_changes'] == [
            {'action': 'acquire', 'item_name': 'stick', 'quantity': 1}
        ]


def test_item_intent_success_allows_before_phrasing(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr('aidm_server.emergent_memory._extract_with_provider', lambda *args, **kwargs: (None, None))

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I pick up a feather before leaving.',
            dm_output='You pick up the feather before leaving the room.',
        )
        turn.metadata_json = safe_json_dumps(
            {
                'action_intent': {
                    'kind': 'item',
                    'inventory_action': 'pick_up',
                    'item': {'name': 'feather', 'quantity': 1},
                    'cost_gold': 0,
                }
            },
            {},
        )
        db.session.commit()

        patch, _extractor_model = extract_canon_patch(
            turn=turn,
            campaign=turn.campaign,
            dm_output=turn.dm_output,
            speaking_player_name='test',
            triggered_segments=[],
        )

        assert patch['inventory_changes'] == [
            {'action': 'acquire', 'item_name': 'feather', 'quantity': 1}
        ]


def test_explicit_inventory_gain_loss_variants_update_quantities(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr('aidm_server.emergent_memory._extract_with_provider', lambda *args, **kwargs: (None, None))

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps(
            [{'name': 'Bone Shard', 'quantity': 2}, {'name': 'Torch', 'quantity': 1}],
            [],
        )
        db.session.commit()

        turn = _create_turn(
            app,
            ids,
            player_input='Apply the inventory state changes.',
            dm_output=(
                f'*(State change: Player {ids["player_id"]} **gains** 3 Bone Shard to inventory.)*\n'
                f'*(State change: Player {ids["player_id"]} **loses** 1 Torch from inventory.)*'
            ),
        )

        patch, _extractor_model = extract_canon_patch(
            turn=turn,
            campaign=turn.campaign,
            dm_output=turn.dm_output,
            speaking_player_name='test',
            triggered_segments=[],
        )

        assert patch['inventory_changes'] == [
            {'action': 'acquire', 'item_name': 'Bone Shard', 'quantity': 3},
            {'action': 'lose', 'item_name': 'Torch', 'quantity': 1},
        ]

        validated_patch, rejections = validate_canon_patch(turn, turn.campaign, patch)
        assert rejections == []
        apply_canon_patch(turn, turn.campaign, validated_patch, 'test-extractor', rejections)
        db.session.commit()
        db.session.refresh(player)

        assert safe_json_loads(player.inventory, []) == [{'name': 'Bone Shard', 'quantity': 5, 'weight': 0.1}]


def test_drop_everything_expands_to_current_inventory(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr('aidm_server.emergent_memory._extract_with_provider', lambda *args, **kwargs: (None, None))

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps(
            [
                {'name': 'Wedged Stone', 'quantity': 1},
                {'name': 'Sword', 'quantity': 1},
                {'name': 'Book', 'quantity': 1},
            ],
            [],
        )
        db.session.commit()

        turn = _create_turn(
            app,
            ids,
            player_input='I drop all my items.',
            dm_output='You open your hands and let everything fall at once.',
        )

        patch, _extractor_model = extract_canon_patch(
            turn=turn,
            campaign=turn.campaign,
            dm_output=turn.dm_output,
            speaking_player_name='test',
            triggered_segments=[],
        )

        assert patch['inventory_changes'] == [
            {'action': 'lose', 'item_name': 'Wedged Stone', 'quantity': 1},
            {'action': 'lose', 'item_name': 'Sword', 'quantity': 1},
            {'action': 'lose', 'item_name': 'Book', 'quantity': 1},
        ]

        validated_patch, rejections = validate_canon_patch(turn, turn.campaign, patch)
        assert rejections == []
        apply_canon_patch(turn, turn.campaign, validated_patch, 'test-extractor', rejections)
        db.session.commit()
        db.session.refresh(player)

        assert safe_json_loads(player.inventory, []) == []


def test_narrative_drop_maps_described_item_to_current_inventory(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr('aidm_server.emergent_memory._extract_with_provider', lambda *args, **kwargs: (None, None))

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps([{'name': 'Book', 'quantity': 1}], [])
        db.session.commit()

        turn = _create_turn(
            app,
            ids,
            player_input='i drop the bookx',
            dm_output=(
                'With a casual flick of your fingers, you release the small leather-bound book. '
                'It flops spine-up onto the cold flagstones.\n\n'
                f'*(State change: Player {ids["player_id"]} **drops** 1 Book. Inventory is now empty.)*'
            ),
        )

        patch, _extractor_model = extract_canon_patch(
            turn=turn,
            campaign=turn.campaign,
            dm_output=turn.dm_output,
            speaking_player_name='test',
            triggered_segments=[],
        )

        validated_patch, rejections = validate_canon_patch(turn, turn.campaign, patch)
        assert rejections == []
        applied_summary = apply_canon_patch(turn, turn.campaign, validated_patch, 'test-extractor', rejections)
        db.session.commit()
        db.session.refresh(player)

        assert applied_summary['inventory_changes_applied'] == [
            {'action': 'lose', 'item_name': 'Book', 'quantity': 1}
        ]
        assert safe_json_loads(player.inventory, []) == []


def test_denied_item_drop_intent_does_not_remove_inventory(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr('aidm_server.emergent_memory._extract_with_provider', lambda *args, **kwargs: (None, None))

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = safe_json_dumps([{'name': 'Book', 'quantity': 1}], [])
        db.session.commit()

        turn = _create_turn(
            app,
            ids,
            player_input='test drops Book: Book',
            dm_output=(
                'You reach to let the book fall, but your fingers close on nothing but air. '
                "The book is already on the floor; there's nothing to drop.\n\n"
                '*(No inventory change. The book remains on the ground.)*'
            ),
        )
        turn.metadata_json = safe_json_dumps(
            {
                'action_intent': {
                    'kind': 'item',
                    'inventory_action': 'drop',
                    'item': {'name': 'Book', 'quantity': 1},
                    'cost_gold': 0,
                }
            },
            {},
        )
        db.session.commit()

        patch, _extractor_model = extract_canon_patch(
            turn=turn,
            campaign=turn.campaign,
            dm_output=turn.dm_output,
            speaking_player_name='test',
            triggered_segments=[],
        )

        validated_patch, rejections = validate_canon_patch(turn, turn.campaign, patch)
        assert rejections == []
        applied_summary = apply_canon_patch(turn, turn.campaign, validated_patch, 'test-extractor', rejections)
        db.session.commit()
        db.session.refresh(player)

        assert applied_summary['inventory_changes_applied'] == []
        assert safe_json_loads(player.inventory, []) == [{'name': 'Book', 'quantity': 1}]


def test_canon_patch_coerces_malformed_model_numbers(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I take the silver key and hurry to the chapel.',
            dm_output='You take the silver key and arrive at the chapel.',
        )

        patch = {
            'facts': [
                {
                    'predicate': 'current_location',
                    'value_text': 'Chapel',
                    'confidence': 'certain',
                }
            ],
            'threads': [
                {
                    'title': 'Find the Chapel',
                    'summary': 'The party has reached the chapel.',
                    'priority': 'urgent',
                }
            ],
            'inventory_changes': [
                {
                    'action': 'acquire',
                    'item_name': 'silver key',
                    'quantity': 'two',
                }
            ],
        }

        validated_patch, rejections = validate_canon_patch(turn, turn.campaign, patch)
        assert rejections == []
        assert validated_patch['inventory_changes'] == [
            {'action': 'acquire', 'item_name': 'silver key', 'quantity': 1}
        ]

        apply_canon_patch(turn, turn.campaign, validated_patch, 'test-extractor', rejections)
        db.session.commit()

        fact = StoryFact.query.filter_by(campaign_id=ids['campaign_id'], predicate='current_location').one()
        thread = StoryThread.query.filter_by(campaign_id=ids['campaign_id'], title='Find the Chapel').one()
        player = db.session.get(Player, ids['player_id'])

        assert fact.confidence is None
        assert thread.priority == 1
        assert safe_json_loads(player.inventory, []) == [{'name': 'silver key', 'quantity': 1, 'weight': 0.1}]


def test_extract_canon_patch_ignores_movement_and_warning_phrases(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr('aidm_server.emergent_memory._extract_with_provider', lambda *args, **kwargs: (None, None))

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I keep moving deeper into the ruins.',
            dm_output='You take the path deeper into the ruins as Liora gives you a warning.',
        )

        patch, extractor_model = extract_canon_patch(
            turn=turn,
            campaign=turn.campaign,
            dm_output=turn.dm_output,
            speaking_player_name='Seraphina',
            triggered_segments=[],
        )

        assert extractor_model in {'heuristic-v1', 'provider', 'fallback'}
        assert patch['inventory_changes'] == []


def test_refresh_session_projection_resets_current_quest_when_open_threads_are_resolved(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I uncover an old vow.',
            dm_output='A new vow hangs over the party.',
        )
        campaign = turn.campaign

        patch = {
            'threads': [
                {
                    'title': 'Open Hook',
                    'summary': 'An unresolved vow.',
                    'status': 'open',
                }
            ]
        }
        validated_patch, rejections = validate_canon_patch(turn, campaign, patch)
        assert rejections == []
        apply_canon_patch(turn, campaign, validated_patch, 'test-extractor', rejections)
        refresh_session_projection(ids['session_id'], campaign)
        db.session.commit()

        state = SessionState.query.filter_by(session_id=ids['session_id']).first()
        assert state is not None
        assert state.current_quest == 'Open Hook'

        resolve_turn = _create_turn(
            app,
            ids,
            player_input='I fulfill the vow.',
            dm_output='The vow is fulfilled.',
        )
        resolve_patch = {
            'threads': [
                {
                    'title': 'Open Hook',
                    'status': 'resolved',
                }
            ]
        }
        validated_resolve, resolve_rejections = validate_canon_patch(resolve_turn, campaign, resolve_patch)
        assert resolve_rejections == []
        apply_canon_patch(resolve_turn, campaign, validated_resolve, 'test-extractor', resolve_rejections)
        refresh_session_projection(ids['session_id'], campaign)
        db.session.commit()

        db.session.refresh(state)
        assert state.current_quest == campaign.current_quest


def test_projection_syncs_session_state_and_snapshot_quests(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='We ask Master Roshi to teach us to fly.',
            dm_output='Master Roshi points toward Kame House and says breakfast comes before flying lessons.',
        )
        campaign = turn.campaign
        session_obj = db.session.get(Session, ids['session_id'])
        session_obj.state_snapshot = safe_json_dumps(
            {
                'currentScene': {
                    'locationId': 'south_dock',
                    'name': 'south dock',
                    'sceneType': 'combat',
                    'dangerLevel': 8,
                    'mood': 'dangerous',
                    'combatState': 'active',
                    'description': 'A stale combat scene on the south dock.',
                    'activeNpcIds': ['unknown_segmented_creature'],
                    'activeQuestIds': ['investigate_underwater_disturbance'],
                },
                'quests': [
                    {
                        'id': 'investigate_underwater_disturbance',
                        'title': 'Investigate the Underwater Disturbance',
                        'status': 'active',
                        'summary': 'Find what is disturbing the fishermen beneath the waves.',
                        'stage': '2',
                        'objectives': [
                            {'id': 'obj_find_clues', 'description': 'Find the creature.', 'status': 'completed'}
                        ],
                        'updatedAtTurn': 42,
                    }
                ],
                'locations': [],
            },
            {},
        )

        patch = {
            'threads': [
                {
                    'title': 'Flying Lessons from Master Roshi',
                    'summary': 'Master Roshi agreed to begin training after breakfast.',
                    'status': 'open',
                    'priority': 2,
                },
                {
                    'title': 'Mysterious Stirring Beneath the Waves',
                    'summary': 'The creature beneath the waves has been slain.',
                    'status': 'closed',
                    'priority': 2,
                },
            ],
            'projection': {
                'current_location': 'Dock outside Kame House',
                'current_quest': 'Flying Lessons from Master Roshi',
            },
        }
        validated_patch, rejections = validate_canon_patch(turn, campaign, patch)
        assert rejections == []
        apply_canon_patch(turn, campaign, validated_patch, 'test-extractor', rejections)
        refresh_session_projection(ids['session_id'], campaign)
        db.session.commit()

        state = get_or_create_session_state(ids['session_id'], campaign)
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
        scene = snapshot['currentScene']
        quests = {quest['id']: quest for quest in snapshot['quests']}

        assert state.current_location == 'Dock outside Kame House'
        assert state.current_quest == 'Flying Lessons from Master Roshi'
        assert scene['locationId'] == 'dock_outside_kame_house'
        assert scene['name'] == 'Dock outside Kame House'
        assert scene['sceneType'] == 'exploration'
        assert scene['dangerLevel'] == 0
        assert scene['combatState'] == 'none'
        assert scene['activeNpcIds'] == []
        assert scene['activeQuestIds'] == ['flying_lessons_from_master_roshi']
        assert 'mood' not in scene
        assert quests['investigate_underwater_disturbance']['status'] == 'completed'
        assert quests['flying_lessons_from_master_roshi']['status'] == 'active'


def test_projection_does_not_promote_story_threads_to_active_quests_in_pack_only_mode(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I question the gate captain.',
            dm_output='Captain Veyra gives directions toward the old road.',
        )
        campaign = turn.campaign
        campaign.current_quest = 'Find the Missing Caravan'
        session_obj = db.session.get(Session, ids['session_id'])
        session_obj.state_snapshot = safe_json_dumps(
            {
                'currentScene': {
                    'locationId': 'bleakmoor_gate',
                    'name': 'Bleakmoor Gate',
                    'activeQuestIds': ['q_missing_caravan', 'question_captain_veyra'],
                },
                'quests': [
                    {
                        'id': 'q_missing_caravan',
                        'title': 'Find the Missing Caravan',
                        'status': 'active',
                        'summary': 'A supply caravan vanished.',
                        'stage': 'Follow the Old Road',
                        'objectives': [],
                        'source': 'campaign_pack',
                        'packId': 'bleakmoor_intro',
                    },
                    {
                        'id': 'question_captain_veyra',
                        'title': 'Question Captain Veyra',
                        'status': 'active',
                        'summary': 'A story thread should not become the main quest.',
                        'stage': '',
                        'objectives': [],
                        'metadata': {'source': 'canon_projection'},
                    },
                ],
                'campaignPack': {
                    'packId': 'bleakmoor_intro',
                    'startingQuestId': 'q_missing_caravan',
                    'directorRules': {'mainQuestGeneration': 'pack_only'},
                    'catalog': {
                        'quests': [
                            {
                                'id': 'q_missing_caravan',
                                'title': 'Find the Missing Caravan',
                                'source': 'campaign_pack',
                                'packId': 'bleakmoor_intro',
                            }
                        ]
                    },
                },
            },
            {},
        )

        patch = {
            'threads': [
                {
                    'title': 'Question Captain Veyra',
                    'summary': 'Veyra pointed toward the old road.',
                    'status': 'open',
                    'priority': 2,
                }
            ]
        }
        validated_patch, rejections = validate_canon_patch(turn, campaign, patch)
        assert rejections == []
        apply_canon_patch(turn, campaign, validated_patch, 'test-extractor', rejections)
        refresh_session_projection(ids['session_id'], campaign)
        db.session.commit()

        state = get_or_create_session_state(ids['session_id'], campaign)
        snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})
        scene = snapshot['currentScene']
        quests = {quest['id']: quest for quest in snapshot['quests']}

        assert state.current_quest == 'Find the Missing Caravan'
        assert scene['activeQuestIds'] == ['q_missing_caravan']
        assert quests['q_missing_caravan']['status'] == 'active'
        assert quests['question_captain_veyra']['status'] == 'noted'
        assert quests['question_captain_veyra']['metadata']['questType'] == 'story_thread'


def test_refresh_session_projection_ignores_prose_current_location_fact(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        first_turn = _create_turn(
            app,
            ids,
            player_input='We arrive in Rensira.',
            dm_output='The party reaches Rensira.',
        )
        second_turn = _create_turn(
            app,
            ids,
            player_input='Joe stands in the moonlight.',
            dm_output='Joe basks in Moonbeam while the party spreads across the stairs and street.',
        )
        campaign = first_turn.campaign
        db.session.add_all(
            [
                StoryFact(
                    campaign_id=ids['campaign_id'],
                    predicate='current_location',
                    value_text='Rensira',
                    fact_status='accepted',
                    source_turn_id=first_turn.turn_id,
                ),
                StoryFact(
                    campaign_id=ids['campaign_id'],
                    predicate='current_location',
                    value_text='Joe basks in Moonbeam while Alfred watches the stairs and Koryl hovers near the door',
                    fact_status='accepted',
                    source_turn_id=second_turn.turn_id,
                ),
            ]
        )
        refresh_session_projection(ids['session_id'], campaign)
        db.session.flush()
        state = get_or_create_session_state(ids['session_id'], campaign)
        state.current_location = 'Joe basks in Moonbeam while Alfred watches the stairs and Koryl hovers near the door'
        session = db.session.get(Session, ids['session_id'])
        snapshot = safe_json_loads(session.state_snapshot, {})
        snapshot['currentScene'] = {
            'locationId': 'joe_basks_in_moonbeam_while_alfred_watches_the_stairs_and_koryl_hovers_near_the_door',
            'name': 'Joe basks in Moonbeam while Alfred watches the stairs and Koryl hovers near the door',
        }
        session.state_snapshot = safe_json_dumps(snapshot, {})
        db.session.commit()

        refresh_session_projection(ids['session_id'], campaign)
        db.session.commit()

        refreshed_state = SessionState.query.filter_by(session_id=ids['session_id']).one()
        refreshed_snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})

        assert refreshed_state.current_location == 'Rensira'
        assert refreshed_snapshot['currentScene']['name'] == 'Rensira'
        assert refreshed_snapshot['currentScene']['locationId'] == 'rensira'


def test_refresh_session_projection_repairs_prose_scene_from_snapshot_location(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='Joe stands in moonlight.',
            dm_output='Joe basks in Moonbeam near the carved stone.',
        )
        campaign = turn.campaign
        campaign.location = ''
        db.session.add(
            StoryFact(
                campaign_id=ids['campaign_id'],
                predicate='current_location',
                value_text='Joe basks in Moonbeam while Alfred watches the stairs and Koryl hovers near the door',
                fact_status='accepted',
                source_turn_id=turn.turn_id,
            )
        )
        state = get_or_create_session_state(ids['session_id'], campaign)
        state.current_location = ''
        session = db.session.get(Session, ids['session_id'])
        snapshot = safe_json_loads(session.state_snapshot, {})
        snapshot['currentScene'] = {
            'locationId': 'joe_basks_in_moonbeam_while_alfred_watches_the_stairs_and_koryl_hovers_near_the_door',
            'name': 'Joe basks in Moonbeam while Alfred watches the stairs and Koryl hovers near the door',
        }
        snapshot['locations'] = [
            {'id': 'rensira', 'name': 'Rensira'},
            {'id': 'dark_hollow_beneath_the_carved_stone', 'name': 'Dark hollow beneath the carved stone'},
        ]
        session.state_snapshot = safe_json_dumps(snapshot, {})
        db.session.commit()

        refresh_session_projection(ids['session_id'], campaign)
        db.session.commit()

        refreshed_state = SessionState.query.filter_by(session_id=ids['session_id']).one()
        refreshed_snapshot = safe_json_loads(db.session.get(Session, ids['session_id']).state_snapshot, {})

        assert refreshed_state.current_location == 'Dark hollow beneath the carved stone'
        assert refreshed_snapshot['currentScene']['name'] == 'Dark hollow beneath the carved stone'
        assert refreshed_snapshot['currentScene']['locationId'] == 'dark_hollow_beneath_the_carved_stone'


def test_extract_canon_patch_does_not_reuse_shared_patch_state(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr('aidm_server.emergent_memory._extract_with_provider', lambda *args, **kwargs: (None, None))

    with app.app_context():
        first_turn = _create_turn(
            app,
            ids,
            player_input='I grab the silver key.',
            dm_output='You take the silver key from the altar.',
        )
        first_patch, _ = extract_canon_patch(
            turn=first_turn,
            campaign=first_turn.campaign,
            dm_output=first_turn.dm_output,
            speaking_player_name='Seraphina',
            triggered_segments=[],
        )
        assert first_patch['inventory_changes'] == [
            {'action': 'acquire', 'item_name': 'silver key', 'quantity': 1}
        ]

        second_turn = _create_turn(
            app,
            ids,
            player_input='I keep moving deeper into the ruins.',
            dm_output='You take the path deeper into the ruins as Liora gives you a warning.',
        )
        second_patch, _ = extract_canon_patch(
            turn=second_turn,
            campaign=second_turn.campaign,
            dm_output=second_turn.dm_output,
            speaking_player_name='Seraphina',
            triggered_segments=[],
        )
        assert second_patch['inventory_changes'] == []


def test_inventory_loss_and_quantity_merging_are_deterministic(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    monkeypatch.setattr('aidm_server.emergent_memory._extract_with_provider', lambda *args, **kwargs: (None, None))

    with app.app_context():
        player = db.session.get(Player, ids['player_id'])
        player.inventory = '[{"name":"silver key","quantity":1},{"name":"torch","quantity":1}]'
        db.session.commit()

        turn = _create_turn(
            app,
            ids,
            player_input='I gather what I can and spend the torch.',
            dm_output='You take a silver key. You pick up a silver key. You spend the torch.',
        )

        patch, extractor_model = extract_canon_patch(
            turn=turn,
            campaign=turn.campaign,
            dm_output=turn.dm_output,
            speaking_player_name='Seraphina',
            triggered_segments=[],
        )

        assert extractor_model in {'heuristic-v1', 'provider', 'fallback'}
        assert patch['inventory_changes'] == [
            {'action': 'acquire', 'item_name': 'silver key', 'quantity': 2},
            {'action': 'lose', 'item_name': 'torch', 'quantity': 1},
        ]

        validated_patch, rejections = validate_canon_patch(turn, turn.campaign, patch)
        assert rejections == []

        apply_canon_patch(turn, turn.campaign, validated_patch, 'test-extractor', rejections)
        db.session.commit()
        db.session.refresh(player)

        assert safe_json_loads(player.inventory, []) == [{'name': 'silver key', 'quantity': 3, 'weight': 0.1}]


def test_build_emergent_context_prioritizes_relevant_older_canon(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        first_turn = _create_turn(
            app,
            ids,
            player_input='I first meet Captain Liora Vale in the bell tower.',
            dm_output='Captain Liora Vale warns you about the ash bells.',
        )
        campaign = first_turn.campaign
        early_patch = {
            'entities': [
                {
                    'entity_type': 'npc',
                    'name': 'Captain Liora Vale',
                    'aliases': ['Liora'],
                    'summary': 'Captain of the soot watch and keeper of the bell tower.',
                }
            ],
            'facts': [
                {
                    'subject': 'Captain Liora Vale',
                    'predicate': 'role',
                    'value_text': 'keeper of the bell tower',
                }
            ],
        }
        validated_early, early_rejections = validate_canon_patch(first_turn, campaign, early_patch)
        assert early_rejections == []
        apply_canon_patch(first_turn, campaign, validated_early, 'test-extractor', early_rejections)

        for idx in range(6):
            noise_turn = _create_turn(
                app,
                ids,
                player_input=f'I encounter wanderer {idx}.',
                dm_output=f'Wanderer {idx} trades rumors in the market square.',
            )
            noise_patch = {
                'entities': [{'entity_type': 'npc', 'name': f'Wanderer {idx}', 'summary': 'A recent but irrelevant extra.'}],
                'threads': [{'title': f'Recent Noise {idx}', 'summary': 'A low-signal side beat.', 'status': 'open', 'priority': 1}],
            }
            validated_noise, noise_rejections = validate_canon_patch(noise_turn, campaign, noise_patch)
            assert noise_rejections == []
            apply_canon_patch(noise_turn, campaign, validated_noise, 'test-extractor', noise_rejections)

        db.session.commit()

        context = build_emergent_context(
            campaign_id=ids['campaign_id'],
            session_id=ids['session_id'],
            query_text='I ask Liora about the bell tower.',
            current_location='Market Square',
            current_quest='Find the relic',
            recent_turns=[{'player_input': 'I ask Liora about the bell tower.', 'dm_output': ''}],
            entity_limit=4,
            fact_limit=4,
            thread_limit=4,
        )

    entity_names = {entity['name'] for entity in context['entities']}
    fact_subjects = {fact['subject'] for fact in context['facts']}
    assert 'Captain Liora Vale' in entity_names
    assert 'Captain Liora Vale' in fact_subjects


def test_build_emergent_context_caps_candidate_pools(app, monkeypatch):
    ids = seed_world_campaign_player_session(app)
    import aidm_server.emergent_memory as emergent_memory

    monkeypatch.setattr(emergent_memory, 'EMERGENT_ENTITY_CANDIDATE_LIMIT', 3)
    monkeypatch.setattr(emergent_memory, 'EMERGENT_FACT_CANDIDATE_LIMIT', 3)
    monkeypatch.setattr(emergent_memory, 'EMERGENT_THREAD_CANDIDATE_LIMIT', 3)

    with app.app_context():
        turn = _create_turn(
            app,
            ids,
            player_input='I scan the archive.',
            dm_output='The archive is full of names.',
        )
        entities = [
            StoryEntity(
                campaign_id=ids['campaign_id'],
                session_id=ids['session_id'],
                entity_type='npc',
                name=f'Archivist {index}',
                canonical_name=f'archivist-{index}',
                summary='Candidate cap test.',
                last_seen_turn_id=turn.turn_id,
            )
            for index in range(8)
        ]
        db.session.add_all(entities)
        db.session.flush()
        facts = [
            StoryFact(
                campaign_id=ids['campaign_id'],
                subject_entity_id=entities[index].entity_id,
                predicate='role',
                value_text=f'Role {index}',
                fact_status='accepted',
            )
            for index in range(8)
        ]
        threads = [
            StoryThread(
                campaign_id=ids['campaign_id'],
                title=f'Archive Thread {index}',
                summary='Candidate cap test.',
                status='open',
            )
            for index in range(8)
        ]
        db.session.add_all(facts + threads)
        db.session.commit()

        context = build_emergent_context(
            campaign_id=ids['campaign_id'],
            entity_limit=8,
            fact_limit=8,
            thread_limit=8,
        )

    assert len(context['entities']) == 3
    assert len(context['facts']) == 3
    assert len(context['threads']) == 3

    with app.app_context():
        capped_context = build_emergent_context(
            campaign_id=ids['campaign_id'],
            entity_limit=2,
            fact_limit=2,
            thread_limit=2,
        )

    assert len(capped_context['entities']) == 2
    assert len(capped_context['facts']) == 2
    assert len(capped_context['threads']) == 2
