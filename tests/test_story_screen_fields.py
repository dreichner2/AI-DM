"""Server fields the Story screen reads: revealed handout and lore text, optional
objectives, saving throws, class pool names, item prices and party positions."""

from __future__ import annotations

from aidm_server.class_capabilities import class_combat_actions
from aidm_server.class_features import class_feature_descriptors
from aidm_server.combat.state import normalize_combat_state, player_combat_participant
from aidm_server.game_state.models import player_character_from_model
from aidm_server.models import Player, safe_json_dumps
from aidm_server.response_dtos import player_derived_payload
from aidm_server.services.campaign_pack_visibility import filter_session_snapshot_for_player


def test_revealed_handouts_and_lore_keep_their_text_for_players():
    snapshot = {
        'handouts': [
            {'id': 'handout_page', 'title': 'Ledger Page', 'status': 'revealed', 'text': 'Drivers: [scratched out].'},
            {'id': 'handout_sealed', 'title': 'Sealed Letter', 'status': 'hidden', 'text': 'Not yet.'},
        ],
        'lore': [{'id': 'lore_oath', 'title': 'The Roadward Oath', 'status': 'unlocked', 'text': 'Ask no name.'}],
        'clues': [{'id': 'clue_ash', 'title': 'Ash Marks', 'status': 'discovered', 'text': 'GM-only clue text.'}],
    }

    filtered = filter_session_snapshot_for_player(snapshot)

    assert [record.get('text') for record in filtered['handouts']] == ['Drivers: [scratched out].', None]
    assert filtered['lore'][0]['text'] == 'Ask no name.'
    assert 'text' not in filtered['clues'][0]


def test_quest_objectives_keep_optional_for_players():
    snapshot = {
        'quests': [
            {
                'id': 'quest_road',
                'title': 'The Road',
                'status': 'active',
                'objectives': [
                    {'id': 'obj_ledger', 'description': 'Read the ledger.', 'status': 'open', 'optional': True},
                    {'id': 'obj_valley', 'description': 'Reach the valley.', 'status': 'open'},
                ],
            }
        ]
    }

    objectives = filter_session_snapshot_for_player(snapshot)['quests'][0]['objectives']

    assert [(objective['id'], objective.get('optional')) for objective in objectives] == [
        ('obj_ledger', True),
        ('obj_valley', None),
    ]


def _player(class_name: str, level: int, *, inventory: list | None = None) -> Player:
    return Player(
        player_id=7,
        character_name='Arden',
        class_=class_name,
        level=level,
        stats=safe_json_dumps(
            {
                'ability_scores': {
                    'strength': 15, 'dexterity': 14, 'constitution': 13,
                    'intelligence': 10, 'wisdom': 12, 'charisma': 8,
                },
            },
            {},
        ),
        inventory=safe_json_dumps(inventory or [], []),
    )


def test_the_derived_payload_carries_saving_throw_proficiencies():
    assert player_derived_payload(_player('Fighter', 1))['savingThrowProficiencies'] == ['constitution', 'strength']


def test_class_pools_carry_a_display_name():
    def pool_names(class_name: str, level: int) -> set[tuple[str, str]]:
        return {
            (descriptor['pool'], descriptor['poolName'])
            for descriptor in class_feature_descriptors(class_name, level)
            if descriptor.get('pool')
        }

    assert pool_names('Monk', 2) == {('ki', 'Ki')}
    assert pool_names('Barbarian', 1) == {('rage', 'Rage')}
    assert pool_names('Sorcerer', 3) == {('sorcery_points', 'Sorcery points')}
    assert pool_names('Cleric', 2) == {('channel_divinity', 'Channel Divinity')}
    assert pool_names('Bard', 1) == {('bardic_inspiration', 'Bardic Inspiration')}

    actor = player_character_from_model(_player('Barbarian', 1))
    assert {(feature['pool'], feature['poolName']) for feature in actor['classFeatures'] if feature.get('pool')} == {
        ('rage', 'Rage'),
    }
    participant = player_combat_participant(actor)
    combat = normalize_combat_state(
        {'status': 'active', 'round': 1, 'participants': [participant], 'flags': {'activeActorId': actor['id']}}
    )
    rage = next(action for action in class_combat_actions({'playerCharacters': [actor]}, participant, combat) if action['id'].endswith('rage'))
    assert (rage['capability']['pool'], rage['capability']['poolName']) == ('rage', 'Rage')


def test_inventory_items_carry_the_listed_price():
    actor = player_character_from_model(
        _player(
            'Fighter',
            1,
            inventory=[
                {'name': 'Longsword', 'quantity': 1, 'type': 'weapon'},
                {'name': 'Potion of Healing', 'quantity': 2, 'type': 'potion'},
                {'name': 'Strange Idol', 'quantity': 1},
            ],
        )
    )

    assert {item['name']: item.get('price') for item in actor['inventory']['items']} == {
        'Longsword': {'gp': 15},
        'Potion of Healing': {'gp': 50},
        'Strange Idol': None,
    }


def test_every_player_sees_where_the_other_heroes_stand():
    snapshot = {
        'combat': {
            'status': 'active',
            'round': 1,
            'participants': [
                {'id': 'player_1', 'playerId': 1, 'name': 'Arden', 'team': 'player',
                 'position': {'rangeBand': 'near'}},
                {'id': 'player_2', 'playerId': 2, 'name': 'Liora', 'team': 'player',
                 'position': {'rangeBand': 'melee', 'engagedWith': ['enemy_wolf', 'enemy_hidden'], 'zoneId': 'secret_ledge'}},
                {'id': 'enemy_wolf', 'name': 'Wolf', 'team': 'enemy', 'position': {'rangeBand': 'melee', 'engagedWith': ['player_2']}},
                {'id': 'enemy_hidden', 'name': 'Lurker', 'team': 'enemy', 'hiddenToPlayers': True,
                 'position': {'rangeBand': 'melee', 'engagedWith': ['player_2']}},
            ],
            'flags': {},
        }
    }

    participants = {
        participant['id']: participant
        for participant in filter_session_snapshot_for_player(snapshot, private_player_ids={1})['combat']['participants']
    }

    assert participants['player_2']['position'] == {'rangeBand': 'melee', 'engagedWith': ['enemy_wolf']}
    assert 'enemy_hidden' not in participants
