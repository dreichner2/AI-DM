from aidm_server.game_state.models import stable_change_id


def test_stable_change_id_preserves_persisted_compatibility():
    parts = (
        'trusted_resolved_damage',
        'trusted_player_attack',
        42,
        0,
        'player_1',
        'player_2',
        7,
        'slashing',
    )

    assert stable_change_id(*parts) == 'chg_9550b21453030169'
    assert stable_change_id(*parts) == stable_change_id(*parts)
    assert stable_change_id(*parts[:-2], 8, 'slashing') != stable_change_id(*parts)
