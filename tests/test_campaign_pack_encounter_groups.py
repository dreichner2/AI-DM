from __future__ import annotations

from types import SimpleNamespace

from aidm_server.blueprints.creatures import _campaign_pack_encounter_request
from aidm_server.game_state.campaign_pack_encounters import (
    MAX_CAMPAIGN_PACK_ENEMIES_PER_GROUP,
    MAX_CAMPAIGN_PACK_ENEMY_PARTICIPANTS,
    materialize_campaign_pack_combat_start,
)


def _pack_state() -> dict:
    return {
        'playerCharacters': [
            {'id': 'player_1', 'name': 'Varin', 'level': 1, 'health': {'currentHp': 10, 'maxHp': 10}},
        ],
        'campaignPack': {
            'packId': 'test_pack',
            'activeCheckpointId': 'cp_start',
            'checkpoints': [
                {'id': 'cp_start', 'title': 'Start', 'encounterIds': ['enc_wagons']},
            ],
            'catalog': {
                'enemies': [
                    {
                        'id': 'enemy_cutthroats',
                        'name': 'Cutthroats',
                        'source': 'campaign_pack',
                        'stats': {'maxHp': 12, 'armorClass': 13},
                    },
                ],
                'encounters': [
                    {
                        'id': 'enc_wagons',
                        'title': 'Wagon Trouble',
                        'enemyIds': ['enemy_cutthroats'],
                        'enemyGroups': [
                            {'enemyId': 'enemy_cutthroats', 'count': 2},
                        ],
                    },
                ],
            },
        },
    }


def test_campaign_pack_materializer_uses_enemy_group_count_over_enemy_ids():
    change = materialize_campaign_pack_combat_start(_pack_state(), {'type': 'combat.start', 'turnId': 10, 'combat': {}})

    enemies = [participant for participant in change['combat']['participants'] if participant['team'] == 'enemy']

    assert [enemy['id'] for enemy in enemies] == ['enemy_enemy_cutthroats_1', 'enemy_enemy_cutthroats_2']
    assert change['combat']['flags']['campaignPackEnemyIds'] == ['enemy_cutthroats', 'enemy_cutthroats']


def test_campaign_pack_combat_api_uses_enemy_group_count_over_enemy_ids():
    request = _campaign_pack_encounter_request(
        _pack_state(),
        {},
        campaign=SimpleNamespace(campaign_id=123),
        session_id=456,
    )

    assert request is not None
    assert request['enemyGroups'][0]['count'] == 2


def test_campaign_pack_materializer_caps_enemy_group_counts():
    state = _pack_state()
    catalog = state['campaignPack']['catalog']
    catalog['enemies'] = [
        {
            'id': f'enemy_wave_{index}',
            'name': f'Wave Enemy {index}',
            'source': 'campaign_pack',
            'stats': {'maxHp': 5, 'armorClass': 11},
        }
        for index in range(4)
    ]
    catalog['encounters'][0]['enemyIds'] = []
    catalog['encounters'][0]['enemyGroups'] = [
        {'enemyId': f'enemy_wave_{index}', 'count': 20_000}
        for index in range(4)
    ]

    change = materialize_campaign_pack_combat_start(state, {'type': 'combat.start', 'turnId': 10, 'combat': {}})
    enemies = [participant for participant in change['combat']['participants'] if participant['team'] == 'enemy']

    assert len(enemies) == MAX_CAMPAIGN_PACK_ENEMY_PARTICIPANTS
    counts_by_pack_id = {}
    for enemy in enemies:
        counts_by_pack_id[enemy['campaignPackEnemyId']] = counts_by_pack_id.get(enemy['campaignPackEnemyId'], 0) + 1
    assert max(counts_by_pack_id.values()) <= MAX_CAMPAIGN_PACK_ENEMIES_PER_GROUP


def test_campaign_pack_combat_api_caps_enemy_group_counts():
    state = _pack_state()
    catalog = state['campaignPack']['catalog']
    catalog['enemies'] = [
        {
            'id': f'enemy_wave_{index}',
            'name': f'Wave Enemy {index}',
            'source': 'campaign_pack',
            'stats': {'maxHp': 5, 'armorClass': 11},
        }
        for index in range(4)
    ]
    catalog['encounters'][0]['enemyIds'] = []
    catalog['encounters'][0]['enemyGroups'] = [
        {'enemyId': f'enemy_wave_{index}', 'count': 20_000}
        for index in range(4)
    ]

    request = _campaign_pack_encounter_request(
        state,
        {},
        campaign=SimpleNamespace(campaign_id=123),
        session_id=456,
    )

    assert request is not None
    assert sum(group['count'] for group in request['enemyGroups']) == MAX_CAMPAIGN_PACK_ENEMY_PARTICIPANTS
    assert max(group['count'] for group in request['enemyGroups']) <= MAX_CAMPAIGN_PACK_ENEMIES_PER_GROUP
