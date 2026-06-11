from aidm_server.socket_state import SocketState


def test_socket_state_tracks_player_until_last_socket_leaves():
    state = SocketState()
    player = {'id': 7, 'character_name': 'Ember', 'name': 'Danny'}

    assert state.track_active_player(3, player, 'sid-a') is True
    assert state.track_active_player(3, player, 'sid-b') is False
    assert state.active_player_payloads(3) == [player]

    assert state.release_active_player(3, 7, 'sid-a') is False
    assert state.active_player_payloads(3) == [player]

    assert state.release_active_player(3, 7, 'sid-b') is True
    assert state.active_player_payloads(3) == []


def test_socket_state_tracks_typing_by_socket():
    state = SocketState()
    player = {'id': 7, 'character_name': 'Ember', 'name': 'Danny'}

    state.track_active_player(3, player, 'sid-a')
    state.track_active_player(3, player, 'sid-b')

    assert state.set_player_typing(3, 7, 'sid-a', True) is True
    assert state.active_player_payloads(3) == [{**player, 'is_typing': True}]

    assert state.set_player_typing(3, 7, 'sid-b', True) is False
    assert state.set_player_typing(3, 7, 'sid-a', False) is False
    assert state.active_player_payloads(3) == [{**player, 'is_typing': True}]

    assert state.release_active_player(3, 7, 'sid-b') is False
    assert state.active_player_payloads(3) == [player]


def test_socket_state_stores_session_music_independently_from_presence():
    state = SocketState()
    music_state = state.set_music_state(
        3,
        {
            'track_id': 'forest-road',
            'status': 'playing',
            'position': 42.5,
            'updated_at_ms': 1000,
            'updated_by_player_id': 7,
        },
    )

    assert music_state['session_id'] == 3
    assert state.music_state(3)['track_id'] == 'forest-road'

    state.clear()
    assert state.music_state(3) is None
