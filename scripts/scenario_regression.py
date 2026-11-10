from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from dataclasses import dataclass
from typing import Any, Callable


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


SetupFn = Callable[[Any, dict[str, int]], None]
AssertFn = Callable[[Any, dict[str, int], list[dict[str, Any]], dict[str, Any]], list[str]]


@dataclass(frozen=True)
class Scenario:
    name: str
    message: str
    response: str
    expectations: tuple[str, ...]
    setup: SetupFn | None = None
    action_intent: dict[str, Any] | None = None
    helper_response: str | None = None
    assertions: AssertFn | None = None


@dataclass
class ScenarioCapture:
    context: str = ''
    user_input: str = ''
    rules_hint: dict[str, Any] | None = None


def _configure_runtime() -> None:
    os.environ.update(
        {
            'PYTHON_DOTENV_DISABLED': '1',
            'AIDM_ENV': 'test',
            'AIDM_DATABASE_URI': 'sqlite:///:memory:',
            'AIDM_AUTO_CREATE_SCHEMA': 'true',
            'AIDM_LLM_PROVIDER': 'fallback',
            'AIDM_LLM_MODEL': 'scenario-regression-v1',
            'AIDM_LLM_FALLBACK_MODELS': '',
            'AIDM_AUTH_REQUIRED': 'false',
            'AIDM_TELEMETRY_ENABLED': 'false',
            'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
            'AIDM_CODEX_EXECUTABLE': '/nonexistent/aidm-scenario-codex',
        }
    )


def _seed_world_campaign_player_session() -> dict[str, int]:
    from aidm_server.database import db
    from aidm_server.models import Campaign, Player, Session, World

    world = World(name='Scenario World', description='A deterministic quality-regression realm.')
    db.session.add(world)
    db.session.flush()

    campaign = Campaign(
        title='Scenario Regression Campaign',
        description='Scenario fixture for beta AI quality checks.',
        world_id=world.world_id,
        current_quest='Follow the silver road',
        location='Ruined Observatory',
    )
    db.session.add(campaign)
    db.session.flush()

    player = Player(
        campaign_id=campaign.campaign_id,
        name='Scenario Player',
        character_name='Ember',
        race='Elf',
        class_='Ranger',
        level=3,
    )
    db.session.add(player)
    db.session.flush()

    session = Session(campaign_id=campaign.campaign_id)
    db.session.add(session)
    db.session.commit()

    return {
        'world_id': world.world_id,
        'campaign_id': campaign.campaign_id,
        'player_id': player.player_id,
        'session_id': session.session_id,
    }


def _event_payload(events: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for event in events:
        if event.get('name') == name:
            args = event.get('args') or []
            return args[0] if args else {}
    return None


def _event_payloads(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    payloads = []
    for event in events:
        if event.get('name') != name:
            continue
        args = event.get('args') or []
        payloads.append(args[0] if args else {})
    return payloads


def _turn_statuses(events: list[dict[str, Any]]) -> list[str]:
    return [
        str(payload.get('status') or '')
        for payload in _event_payloads(events, 'turn_status')
        if payload.get('status')
    ]


def _latest_turn(session_id: int):
    from aidm_server.models import DmTurn

    return (
        DmTurn.query.filter_by(session_id=session_id)
        .order_by(DmTurn.turn_id.desc())
        .first()
    )


def _assert_text_contains(text: str, *needles: str) -> list[str]:
    lowered = text.lower()
    missing = [needle for needle in needles if needle.lower() not in lowered]
    if missing:
        raise AssertionError(f'Missing expected text fragments: {missing}; text={text!r}')
    return [f'text contains {needle}' for needle in needles]


def _setup_potion(app, ids: dict[str, int]) -> None:
    from aidm_server.database import db
    from aidm_server.models import Player, safe_json_dumps

    player = db.session.get(Player, ids['player_id'])
    assert player is not None
    player.inventory = safe_json_dumps(
        [
            {
                'id': 'minor_healing_potion_1',
                'name': 'Minor Healing Potion',
                'quantity': 1,
                'type': 'consumable',
                'subtype': 'potion',
            }
        ],
        [],
    )
    player.stats = safe_json_dumps({'current_hp': 10, 'hp_current': 10, 'max_hp': 20, 'hp_max': 20}, {})
    db.session.commit()


def _assert_potion(app, ids: dict[str, int], events: list[dict[str, Any]], capture: dict[str, Any]) -> list[str]:
    from aidm_server.database import db
    from aidm_server.models import Player, safe_json_loads

    assert 'state_applied' in _turn_statuses(events)
    player = db.session.get(Player, ids['player_id'])
    assert player is not None
    inventory = safe_json_loads(player.inventory, [])
    stats = safe_json_loads(player.stats, {})
    assert inventory == [], inventory
    assert stats.get('current_hp') == 17, stats
    return ['potion consumed', 'hp restored to 17']


def _setup_checkpoint(app, ids: dict[str, int]) -> None:
    from aidm_server.database import db
    from aidm_server.models import CampaignSegment

    db.session.add(
        CampaignSegment(
            campaign_id=ids['campaign_id'],
            title='Chapel Checkpoint Reached',
            description='The chapel checkpoint is active.',
            trigger_condition='{"type":"state","location_contains":"chapel"}',
            tags='checkpoint,chapel',
            is_triggered=False,
        )
    )
    db.session.commit()


def _assert_checkpoint(app, ids: dict[str, int], events: list[dict[str, Any]], capture: dict[str, Any]) -> list[str]:
    from aidm_server.models import CampaignSegment

    segment_payload = _event_payload(events, 'segment_triggered')
    assert segment_payload is not None, events
    assert segment_payload.get('title') == 'Chapel Checkpoint Reached'
    segment = CampaignSegment.query.filter_by(campaign_id=ids['campaign_id']).first()
    assert segment is not None and segment.is_triggered is True
    return ['checkpoint segment triggered']


def _setup_npc(app, ids: dict[str, int]) -> None:
    from aidm_server.database import db
    from aidm_server.models import Session, safe_json_dumps

    session = db.session.get(Session, ids['session_id'])
    assert session is not None
    session.state_snapshot = safe_json_dumps(
        {
            'schemaVersion': 1,
            'currentScene': {
                'locationId': 'ruined_observatory',
                'name': 'Ruined Observatory',
                'activeNpcIds': ['npc_lysa'],
                'activeQuestIds': [],
            },
            'knownNpcs': [
                {
                    'id': 'npc_lysa',
                    'name': 'Lysa',
                    'role': 'Star chart keeper',
                    'disposition': 'wary ally',
                    'summary': 'Lysa remembers the safe route through the observatory dome.',
                    'locationId': 'ruined_observatory',
                }
            ],
            'quests': [],
            'locations': [],
            'partyNpcs': [],
            'flags': {},
            'stateChangeLedger': [],
        },
        {},
    )
    db.session.commit()


def _assert_npc_context(app, ids: dict[str, int], events: list[dict[str, Any]], capture: dict[str, Any]) -> list[str]:
    context = str(capture.get('context') or '')
    _assert_text_contains(context, 'Lysa')
    turn = _latest_turn(ids['session_id'])
    assert turn is not None
    return _assert_text_contains(turn.dm_output or '', 'Lysa')


def _setup_canon_fact(app, ids: dict[str, int]) -> None:
    from aidm_server.database import db
    from aidm_server.models import StoryEntity, StoryFact

    entity = StoryEntity(
        campaign_id=ids['campaign_id'],
        session_id=ids['session_id'],
        entity_type='npc',
        name='Mara',
        canonical_name='Mara',
        summary='Mara is a caravan scout who owes the party a silver key.',
    )
    db.session.add(entity)
    db.session.flush()
    db.session.add(
        StoryFact(
            campaign_id=ids['campaign_id'],
            subject_entity_id=entity.entity_id,
            predicate='owes_party',
            value_text='Mara owes the party a silver key',
            fact_status='accepted',
            confidence=0.95,
        )
    )
    db.session.commit()


def _assert_canon_context(app, ids: dict[str, int], events: list[dict[str, Any]], capture: dict[str, Any]) -> list[str]:
    context = str(capture.get('context') or '')
    _assert_text_contains(context, 'Mara', 'silver key')
    turn = _latest_turn(ids['session_id'])
    assert turn is not None
    return _assert_text_contains(turn.dm_output or '', 'Mara', 'silver key')


def _assert_opening_plumbing(app, ids: dict[str, int], events: list[dict[str, Any]], capture: dict[str, Any]) -> list[str]:
    """The response is canned, so this checks delivery and persistence, not narration quality."""
    turn = _latest_turn(ids['session_id'])
    assert turn is not None
    assert turn.status == 'completed'
    assert turn.llm_provider == 'fallback'
    assert turn.llm_model == 'scenario-regression-v1'
    end_payload = _event_payload(events, 'dm_response_end') or {}
    streamed_text = ''.join(str(payload.get('chunk') or '') for payload in _event_payloads(events, 'dm_chunk'))
    assert turn.dm_output and turn.dm_output == end_payload.get('text') == streamed_text
    return ['streamed opening text was persisted unchanged', 'turn records the scenario provider and model']


def _assert_impossible(app, ids: dict[str, int], events: list[dict[str, Any]], capture: dict[str, Any]) -> list[str]:
    turn = _latest_turn(ids['session_id'])
    assert turn is not None
    return _assert_text_contains(turn.dm_output or '', 'cannot', 'moon', 'instead')


def _assert_combat_roll(app, ids: dict[str, int], events: list[dict[str, Any]], capture: dict[str, Any]) -> list[str]:
    turn = _latest_turn(ids['session_id'])
    assert turn is not None
    assert turn.requires_roll is True
    assert 'dm_response_end' in [event.get('name') for event in events]
    return _assert_text_contains(turn.dm_output or '', 'roll')


def _default_scenarios() -> list[Scenario]:
    return [
        Scenario(
            name='opening_scene_plumbing',
            message='I step into the ruined observatory and ask what I see.',
            response=(
                'The ruined observatory opens into a circular hall where star-silver dust hangs in the air. '
                'Broken brass lenses point toward a cracked dome, and the first real choice is whether Ember '
                'studies the chart table, follows the cold wind downstairs, or calls out to whoever lit the blue lantern.'
            ),
            expectations=(
                'canned opening streams and persists unchanged (plumbing only; narration quality is not evaluated)',
            ),
            assertions=_assert_opening_plumbing,
        ),
        Scenario(
            name='impossible_action_boundary',
            message='I leap to the moon in one jump.',
            response=(
                'You cannot leap to the moon from the observatory floor; the world holds to its own weight. '
                'Instead, Ember turns the impossible jump into a desperate vault toward the balcony rail, '
                'where a moonlit lens might still answer.'
            ),
            expectations=('impossible action is bounded', 'DM offers a grounded alternative'),
            assertions=_assert_impossible,
        ),
        Scenario(
            name='combat_requires_roll',
            message='I attack the goblin sentry with my sword.',
            response='The goblin sentry jerks back as Ember lunges across the cracked tiles.',
            expectations=('combat action requires a roll', 'roll prompt is present before outcome'),
            assertions=_assert_combat_roll,
        ),
        Scenario(
            name='inventory_item_use',
            message='I drink my healing potion.',
            response='You drink the Minor Healing Potion. Restore 7 HP as warmth spreads through your ribs.',
            expectations=('inventory item is consumed', 'healing is applied'),
            setup=_setup_potion,
            assertions=_assert_potion,
        ),
        Scenario(
            name='campaign_checkpoint_trigger',
            message='I enter the chapel under the observatory.',
            response='You enter the chapel, and the soot-stained bells settle into a checkpoint hush.',
            expectations=('state checkpoint triggers after scene movement',),
            setup=_setup_checkpoint,
            helper_response=(
                '{"proposedChanges":['
                '{"id":"scenario_move_chapel","type":"scene.move_location",'
                '"locationId":"soot_stained_chapel","name":"Soot-Stained Chapel"}'
                '],"uncertainChanges":[]}'
            ),
            assertions=_assert_checkpoint,
        ),
        Scenario(
            name='npc_continuity',
            message='I ask Lysa which stair is safe.',
            response='Lysa taps the star chart and warns Ember that the eastern stair remembers every false step.',
            expectations=('active NPC appears in DM context', 'response preserves NPC identity'),
            setup=_setup_npc,
            assertions=_assert_npc_context,
        ),
        Scenario(
            name='canon_memory_recall',
            message='I ask what Mara still owes us.',
            response='Mara still owes the party a silver key, and that debt may open the locked toll gate.',
            expectations=('durable canon fact appears in DM context', 'response uses the remembered fact'),
            setup=_setup_canon_fact,
            assertions=_assert_canon_context,
        ),
    ]


class _FakePostDmProvider:
    def __init__(self, response: str):
        self.response = response

    def generate(self, _request):
        from aidm_server.contracts import ProviderResponse

        return ProviderResponse(text=self.response, provider='scenario-helper', model='scenario-helper-v1')


def _run_scenario(app, socketio, scenario: Scenario) -> dict[str, Any]:
    from aidm_server.database import db
    from aidm_server.models import safe_json_loads
    import aidm_server.blueprints.socketio_events as socketio_events_module
    import aidm_server.game_state.extraction.post_dm_outcome_extractor as post_extractor_module

    capture = ScenarioCapture()

    ids = _seed_world_campaign_player_session()
    if scenario.setup:
        scenario.setup(app, ids)

    def fake_stream(user_input, context, speaking_player=None, rules_hint=None):
        del speaking_player
        capture.user_input = str(user_input or '')
        capture.context = str(context or '')
        capture.rules_hint = dict(rules_hint or {})
        yield scenario.response

    original_stream = socketio_events_module.query_dm_function_stream
    original_post_provider = post_extractor_module.get_helper_provider
    original_helper_flag = app.config.get('AIDM_STATE_PIPELINE_HELPER_IN_TESTS')
    socketio_events_module.query_dm_function_stream = fake_stream
    if scenario.helper_response:
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = True
        post_extractor_module.get_helper_provider = lambda: _FakePostDmProvider(scenario.helper_response or '')

    try:
        client = socketio.test_client(app, flask_test_client=app.test_client())
        assert client.is_connected()
        client.emit('join_session', {'session_id': ids['session_id'], 'player_id': ids['player_id']})
        client.get_received()
        payload = {
            'session_id': ids['session_id'],
            'campaign_id': ids['campaign_id'],
            'world_id': ids['world_id'],
            'player_id': ids['player_id'],
            'message': scenario.message,
        }
        if scenario.action_intent:
            payload['action_intent'] = scenario.action_intent
        client.emit('send_message', payload)
        events = client.get_received()
    finally:
        socketio_events_module.query_dm_function_stream = original_stream
        post_extractor_module.get_helper_provider = original_post_provider
        app.config['AIDM_STATE_PIPELINE_HELPER_IN_TESTS'] = original_helper_flag

    errors = [event for event in events if event.get('name') == 'error']
    if errors:
        raise AssertionError(f'{scenario.name} emitted errors: {errors}')
    if _event_payload(events, 'dm_response_start') is None:
        raise AssertionError(f'{scenario.name} did not emit dm_response_start')
    if _event_payload(events, 'dm_response_end') is None:
        raise AssertionError(f'{scenario.name} did not emit dm_response_end')

    assertions = []
    if scenario.assertions:
        assertions = scenario.assertions(app, ids, events, capture.__dict__)

    turn = _latest_turn(ids['session_id'])
    if turn is None:
        raise AssertionError(f'{scenario.name} did not persist a DmTurn')
    metadata = safe_json_loads(turn.metadata_json, {})
    result = {
        'scenario': scenario.name,
        'turn_id': turn.turn_id,
        'session_id': ids['session_id'],
        'status': turn.status,
        'provider': turn.llm_provider,
        'model': turn.llm_model,
        'requires_roll': turn.requires_roll,
        'outcome_status': turn.outcome_status,
        'turn_number': metadata.get('turn_number'),
        'expectations': list(scenario.expectations),
        'assertions': assertions,
        'events': [event.get('name') for event in events],
    }
    assert result['provider'] == app.config['AIDM_LLM_PROVIDER']
    assert result['model'] == app.config['AIDM_LLM_MODEL']
    assert result['status'] == 'completed'
    db.session.commit()
    return result


def run_scenarios() -> list[dict[str, Any]]:
    _configure_runtime()

    from aidm_server.blueprints.socketio_events import register_socketio_events
    from aidm_server.database import ensure_schema
    from aidm_server.main import create_app, create_socketio

    app = create_app()
    ensure_schema(app)
    socketio = create_socketio(app)
    register_socketio_events(socketio)

    results: list[dict[str, Any]] = []
    with app.app_context():
        for scenario in _default_scenarios():
            results.append(_run_scenario(app, socketio, scenario))
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run deterministic AI-DM scenario quality regressions.')
    parser.add_argument(
        '--json-output',
        type=pathlib.Path,
        help='Optional path to write the structured scenario regression report.',
    )
    parser.add_argument(
        '--print-json',
        action='store_true',
        help='Print the full structured report to stdout instead of the concise summary.',
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    results = run_scenarios()
    report = {
        'provider': results[0]['provider'] if results else None,
        'model': results[0]['model'] if results else None,
        'scenario_count': len(results),
        'scenarios': results,
    }
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    if args.print_json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"Scenario regression provider={report['provider']} model={report['model']}")
        for result in results:
            assertions = ', '.join(result['assertions']) or 'assertions passed'
            print(f"- {result['scenario']}: turn {result['turn_id']}, {assertions}")
    print(f"Scenario regression passed: {len(results)} scenarios")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
