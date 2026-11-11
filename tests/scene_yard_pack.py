"""The coaching yard: a small pack for plans that change the scene and fights about something (W9-SCENE).

One yard with a gate (barrier, exit), a lantern (light, fragile), a tethered horse (animal, rope), a huddle of
drovers (crowd), a feed crate (cover, container) and the mail wagon whose search starts the fight; Wenna Pike, the
witness; two cutthroats whose runner takes the road-stone fragment and runs for the gate while the other goes for
Wenna. Next door, the stable; beyond it, the loft. :class:`YardTable` plays it through the real socket pipeline as
the Play Now adventure (Arden Vale, and Seren Ashgrove in party play).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.database import db
from aidm_server.models import Session, safe_json_dumps, safe_json_loads
from aidm_server.services import play_now
from tests.test_party_play import CLERIC
from tests.test_where_everyone_stands import Table

YARD_ID = 'test.coaching_yard'
FRAGMENT = 'road_stone_fragment'

GATE_AFFORDANCE = {
    'id': 'bar_gate',
    'effect': 'obstruct',
    'label': 'Shut and bar the yard gate',
    'keywords': ['gate', 'bar', 'shut', 'close'],
    'check': {'skill': 'athletics', 'dc': 10},
    'cost': 'action',
    'inFight': True,
    'outsideFight': True,
    'once': True,
    'result': {'fact': 'The yard gate is barred.', 'stakes': {'way_out': 'blocked'}},
    'failResult': {'text': 'The bar jumps out of its brackets and lands in the mud.'},
}

YARD: dict[str, Any] = {
    'schemaVersion': '1',
    'packId': YARD_ID,
    'title': 'The Coaching Yard',
    'description': 'A walled coaching yard, one lantern, and a wagon nobody has unloaded.',
    'startingState': {'locationId': 'loc_yard', 'questId': 'quest_yard'},
    'locations': [
        {
            'id': 'loc_yard',
            'name': 'The Coaching Yard',
            'visibleAtStart': True,
            'sceneType': 'exploration',
            'description': 'A walled coaching yard lit by a single lantern on a post.',
            'connectedLocationIds': ['loc_lane'],
            'npcIds': ['npc_wenna'],
            'restPolicy': 'safe',
            'zones': [
                {'id': 'yard', 'name': 'The Yard', 'adjacentTo': ['stable'], 'exit': True, 'description': 'Mud, straw and lamplight.'},
                {'id': 'stable', 'name': 'The Stable', 'adjacentTo': ['yard', 'loft'], 'description': 'Warm dark and the smell of hay.'},
                {'id': 'loft', 'name': 'The Loft', 'adjacentTo': ['stable'], 'description': 'Dust and old harness.'},
            ],
            'startZoneId': 'yard',
            'interactables': [
                {
                    'id': 'yard_gate',
                    'name': 'Yard Gate',
                    'kind': 'object',
                    'tags': ['barrier', 'exit'],
                    'allowedActions': ['inspect'],
                    'zoneId': 'yard',
                    'affordances': [GATE_AFFORDANCE],
                },
                {'id': 'yard_lantern', 'name': 'Yard Lantern', 'kind': 'object', 'tags': ['light', 'fragile'], 'allowedActions': ['inspect'], 'zoneId': 'yard'},
                {
                    'id': 'tethered_horse',
                    'name': 'Tethered Horse',
                    'kind': 'object',
                    'tags': ['animal', 'rope'],
                    'allowedActions': ['inspect'],
                    'zoneId': 'yard',
                    'affordances': [
                        {
                            'id': 'slap_horse',
                            'effect': 'separate',
                            'label': 'Slash the tether and slap the horse',
                            'keywords': ['tether', 'slap', 'horse'],
                            'target': 'enemy',
                            'inFight': True,
                            'outsideFight': False,
                            'once': True,
                            'result': {'text': 'The horse bolts across the yard and the cutthroats scatter from its hooves.', 'fact': 'The horse is loose in the yard.'},
                        }
                    ],
                },
                {'id': 'drovers', 'name': 'Huddle of Drovers', 'kind': 'object', 'tags': ['crowd'], 'allowedActions': ['inspect'], 'zoneId': 'yard'},
                {'id': 'feed_crate', 'name': 'Feed Crate', 'kind': 'object', 'tags': ['cover', 'container'], 'allowedActions': ['inspect'], 'zoneId': 'yard'},
                {
                    'id': 'mail_wagon',
                    'name': 'Mail Wagon',
                    'kind': 'container',
                    'open': True,
                    'allowedActions': ['search'],
                    'zoneId': 'yard',
                    'contents': [{'id': FRAGMENT, 'name': 'Road-Stone Fragment', 'type': 'quest', 'quantity': 1}],
                },
                {
                    'id': 'hay_pile',
                    'name': 'Hay Pile',
                    'kind': 'container',
                    'tags': ['cover'],
                    'open': True,
                    'allowedActions': ['search'],
                    'zoneId': 'stable',
                    'reveals': {'search': ['wagon_strongbox']},
                },
                {'id': 'loft_hatch', 'name': 'Loft Hatch', 'kind': 'object', 'tags': ['barrier'], 'allowedActions': ['inspect'], 'zoneId': 'loft'},
                {
                    'id': 'loose_board',
                    'name': 'Loose Board',
                    'kind': 'container',
                    'open': True,
                    'allowedActions': ['search'],
                    'zoneId': 'yard',
                    'requirements': {'search': {'check': {'id': 'search_board', 'dc': 18, 'ability': 'intelligence', 'skill': 'investigation'}}},
                    'onFail': {
                        'search': {
                            'text': 'The board snaps under your boot: only rats, and a scrap of ash-marked cloth.',
                            'setsFlags': {'ash_cloth_found': True},
                            'fact': 'The loose board is splintered.',
                            'noRetry': True,
                        }
                    },
                },
                {
                    'id': 'rain_barrel',
                    'name': 'Rain Barrel',
                    'kind': 'container',
                    'open': True,
                    'allowedActions': ['search'],
                    'zoneId': 'yard',
                    'requirements': {'search': {'check': {'id': 'search_barrel', 'dc': 18, 'ability': 'intelligence', 'skill': 'investigation'}}},
                    'onFail': {'search': {'text': 'Cold water to the elbow, and a drowned glove that is not yours.'}},
                },
                {'id': 'wagon_strongbox', 'name': 'Wagon Strongbox', 'kind': 'container', 'hidden': True, 'open': False, 'allowedActions': ['open', 'search'], 'zoneId': 'stable'},
                {'id': 'saddle_cache', 'name': 'Saddle Cache', 'kind': 'container', 'hidden': True, 'open': True, 'allowedActions': ['search'], 'zoneId': 'loft'},
            ],
        },
        {'id': 'loc_lane', 'name': 'Mill Lane', 'visibleAtStart': True, 'sceneType': 'exploration', 'description': 'A muddy lane to the mill.', 'connectedLocationIds': ['loc_yard']},
    ],
    'npcs': [
        {
            'id': 'npc_wenna',
            'name': 'Wenna Pike',
            'role': 'witness',
            'disposition': 'wary',
            'zoneId': 'yard',
            'playerSummary': 'A drover who saw the wagon come in.',
            'knows': ['The wagon came in without its driver.'],
            'topics': [
                {'id': 'wenna_wagon', 'label': 'the wagon', 'line': '"It came in on its own," Wenna says.'},
                {'id': 'wenna_secret', 'label': 'who paid her', 'private': True, 'line': '"A man with ash on his hands paid me to look away," Wenna whispers.'},
            ],
        }
    ],
    'enemies': [
        {
            'id': 'enemy_cutthroat',
            'name': 'Cutthroats',
            'singularName': 'Cutthroat',
            'type': 'human',
            'level': 2,
            'stats': {'maxHp': 18, 'armorClass': 13, 'strength': 10, 'dexterity': 15, 'constitution': 12, 'intelligence': 11, 'wisdom': 10, 'charisma': 10},
            'abilities': [
                {'id': 'cutthroat_knife', 'name': 'Hooked Knife', 'type': 'attack', 'actionCost': 'action', 'range': 'melee', 'targetType': 'single', 'attackBonus': 4, 'damage': {'dice': '1d6+2', 'type': 'slashing'}}
            ],
            'behavior': {'combatRole': 'skirmisher', 'aggression': 50, 'selfPreservation': 50, 'morale': 60, 'fleeThreshold': 0, 'surrenderThreshold': 0},
        }
    ],
    'encounters': [
        {
            'id': 'enc_yard_theft',
            'title': 'Hands in the Yard',
            'locationIds': ['loc_yard'],
            'zoneId': 'yard',
            'enemyIds': ['enemy_cutthroat'],
            'enemyGroups': [{'enemyId': 'enemy_cutthroat', 'count': 2, 'startBand': 'near'}],
            'trigger': {'type': 'on_interactable', 'targetId': 'mail_wagon', 'action': 'search'},
            'stakes': [
                {'id': 'fragment', 'name': 'the road-stone fragment', 'kind': 'item', 'itemId': FRAGMENT},
                {'id': 'witness', 'name': 'Wenna Pike', 'kind': 'person', 'npcId': 'npc_wenna'},
                {'id': 'way_out', 'name': 'the yard gate', 'kind': 'route', 'objectId': 'yard_gate'},
            ],
            'enemyGoal': {'type': 'take_and_flee', 'stakeId': 'fragment', 'routeStakeId': 'way_out'},
            'secondaryGoal': {'type': 'silence', 'stakeId': 'witness'},
            'rewards': {'xp': 100},
            'results': [
                {
                    'when': {'stakes': {'fragment': 'secured'}, 'captives': True},
                    'setsFlags': {'cutthroat_taken': True},
                    'lead': 'The bound cutthroat names a buyer at the mill.',
                    'text': '"Ask the miller," the bound cutthroat spits.',
                },
                {
                    'when': {'stakes': {'fragment': 'secured'}, 'captives': False},
                    'setsFlags': {'fragment_kept_in_the_dark': True},
                    'fact': 'Mud and blood mark where the cutthroats fell.',
                    'lead': 'Whoever sent them will come again by daylight.',
                },
                {
                    'when': {'stakes': {'fragment': 'taken'}},
                    'setsFlags': {'fragment_lost': True},
                    'lead': 'The fragment went north with the cutthroat.',
                    'text': 'Hoofbeats fade up the lane.',
                },
            ],
        }
    ],
    'quests': [
        {
            'id': 'quest_yard',
            'title': 'The Mail Wagon',
            'objectives': [{'id': 'obj_lane', 'description': 'Reach Mill Lane.', 'completeWhen': {'atLocationId': 'loc_lane'}}],
        }
    ],
    'checkpoints': [{'id': 'cp_yard', 'title': 'The Yard', 'playerTitle': 'The Yard', 'terminal': True}],
}


def yard_pack() -> dict[str, Any]:
    return deepcopy(YARD)


def install(monkeypatch, pack: dict[str, Any] | None = None) -> None:
    """Make the yard (or ``pack``) the Play Now adventure."""

    manifest = deepcopy(pack or YARD)
    original = play_now.get_example_campaign_pack
    entry = {'pack_id': YARD_ID, 'title': manifest['title'], 'source_filename': 'coaching_yard.json', 'manifest': manifest}
    monkeypatch.setattr(play_now, 'DEFAULT_PLAY_NOW_EXAMPLE_PACK_ID', YARD_ID)
    monkeypatch.setattr(play_now, 'get_example_campaign_pack', lambda pack_id: deepcopy(entry) if pack_id == YARD_ID else original(pack_id))


class YardTable(Table):
    """The yard at the socket, with what plans and stakes need; ``fresh`` starts another adventure beside one already
    played."""

    def __init__(self, app, client, socketio, *, party: bool, fresh: bool = False):
        self.app = app
        self.client = client
        response = client.post('/api/onboarding/play-now', json={'fresh': True} if fresh else {})
        assert response.status_code == 201, response.get_data(as_text=True)
        self.ids = {key: response.get_json()[key] for key in ('session_id', 'campaign_id', 'player_id', 'workspace_id')}
        self.players = {'Arden Vale': self.ids['player_id']}
        if party:
            created = client.post(f"/api/players/campaigns/{self.ids['campaign_id']}/players", json=CLERIC)
            assert created.status_code == 201, created.get_json()
            self.players['Seren Ashgrove'] = created.get_json()['player_id']
        self.sockets = {}
        for name, player_id in self.players.items():
            socket_client = socketio.test_client(app, flask_test_client=app.test_client())
            socket_client.emit('join_session', {'session_id': self.ids['session_id'], 'player_id': player_id, 'workspace_id': self.ids['workspace_id']})
            self.sockets[name] = socket_client
        self.drain()
        self.sent = 0

    def plan(self, hero: str, label: str, plan: dict[str, Any]) -> list[dict]:
        return self.send(hero, label, {'kind': 'plan', 'source': 'scene_panel', 'plan': plan})

    def affordance(self, hero: str, object_id: str, affordance_id: str, label: str) -> list[dict]:
        return self.plan(hero, label, {'objectId': object_id, 'affordanceId': affordance_id})

    def facts(self) -> list[str]:
        return list((self.snapshot().get('currentScene') or {}).get('facts') or [])

    def stakes(self) -> dict[str, str]:
        flags = (self.snapshot().get('combat') or {}).get('flags') or {}
        return {stake['id']: stake['state'] for stake in flags.get('stakes') or []}

    def final_stakes(self) -> dict[str, str]:
        return dict((self.snapshot().get('stakes') or {}).get('enc_yard_theft') or {})

    def leads(self) -> list[str]:
        return [lead['text'] for lead in self.snapshot().get('leads') or []]

    def captives(self) -> list[str]:
        return [captive['name'] for captive in (self.snapshot().get('currentScene') or {}).get('captives') or []]

    def combat(self) -> dict[str, Any]:
        return self.snapshot().get('combat') or {}

    def edit(self, change) -> None:
        """Change the saved session directly (what the test stands up rather than plays)."""

        with self.app.app_context():
            session = db.session.get(Session, self.ids['session_id'])
            snapshot = safe_json_loads(session.state_snapshot, {})
            change(snapshot)
            session.state_snapshot = safe_json_dumps(snapshot, {})
            db.session.commit()

    def enemy(self, name: str) -> dict[str, Any]:
        return next(participant for participant in self.combat().get('participants') or [] if participant.get('name') == name)
