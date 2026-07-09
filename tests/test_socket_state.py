from concurrent.futures import ThreadPoolExecutor
import threading

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


def test_socket_state_tracks_simultaneous_player_sockets_atomically():
    state = SocketState()
    player = {'id': 7, 'character_name': 'Ember', 'name': 'Danny'}
    barrier = threading.Barrier(2)

    def track(sid: str) -> bool:
        barrier.wait(timeout=2)
        return state.track_active_player(3, player, sid)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(track, ('sid-a', 'sid-b')))

    assert results.count(True) == 1
    assert results.count(False) == 1
    assert state.active_players[3][7]['_sids'] == {'sid-a', 'sid-b'}
    assert state.active_player_payloads(3) == [player]


def test_socket_state_connection_reads_are_snapshots_and_binding_is_atomic():
    state = SocketState()
    snapshot = state.ensure_connection(
        'sid-a',
        {'authorized': True, 'session_id': 3, 'player_id': 7},
    )
    snapshot['session_id'] = 99

    assert state.connection('sid-a')['session_id'] == 3
    assert state.unbind_connection(
        'sid-a',
        expected_session_id=99,
        expected_player_id=7,
    ) is None

    previous = state.unbind_connection(
        'sid-a',
        expected_session_id=3,
        expected_player_id=7,
    )
    assert previous['session_id'] == 3
    assert state.connection('sid-a') == {
        'authorized': True,
        'session_id': None,
        'player_id': None,
    }


def test_socket_state_serializes_leave_and_rejoin_for_one_sid():
    state = SocketState()
    player = {'id': 7, 'character_name': 'Ember', 'name': 'Danny'}
    state.set_connection('sid-a', {'authorized': True, 'session_id': 3, 'player_id': 7})
    state.track_active_player(3, player, 'sid-a')

    leave_unbound = threading.Event()
    allow_leave_to_finish = threading.Event()
    rejoin_attempted = threading.Event()
    rejoin_entered = threading.Event()

    def leave() -> None:
        with state.connection_lifecycle('sid-a'):
            state.unbind_connection('sid-a', expected_session_id=3, expected_player_id=7)
            leave_unbound.set()
            assert allow_leave_to_finish.wait(timeout=2)
            state.release_active_player(3, 7, 'sid-a')

    def rejoin() -> None:
        assert leave_unbound.wait(timeout=2)
        rejoin_attempted.set()
        with state.connection_lifecycle('sid-a'):
            rejoin_entered.set()
            state.update_connection_if_present('sid-a', session_id=3, player_id=7)
            state.track_active_player(3, player, 'sid-a')

    with ThreadPoolExecutor(max_workers=2) as executor:
        leave_future = executor.submit(leave)
        rejoin_future = executor.submit(rejoin)
        assert rejoin_attempted.wait(timeout=2)
        assert not rejoin_entered.wait(timeout=0.05)
        allow_leave_to_finish.set()
        leave_future.result(timeout=2)
        rejoin_future.result(timeout=2)

    assert state.connection('sid-a')['session_id'] == 3
    assert state.active_player_payloads(3) == [player]
