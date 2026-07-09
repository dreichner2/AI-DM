"""In-process Socket.IO presence and connection state."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, ClassVar


@dataclass
class SocketState:
    LIFECYCLE_LOCK_STRIPES: ClassVar[int] = 64

    active_players: dict[int, dict[int, dict[str, Any]]] = field(default_factory=dict)
    connections: dict[str, dict[str, Any]] = field(default_factory=dict)
    session_music: dict[int, dict[str, Any]] = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)
    _lifecycle_locks: tuple[RLock, ...] = field(
        default_factory=lambda: tuple(RLock() for _ in range(SocketState.LIFECYCLE_LOCK_STRIPES)),
        init=False,
        repr=False,
        compare=False,
    )

    def connection_lifecycle(self, sid: str):
        """Serialize connection, room, and presence transitions for one socket."""

        return self._lifecycle_locks[hash(sid) % len(self._lifecycle_locks)]

    def connection(self, sid: str | None) -> dict[str, Any] | None:
        if not sid:
            return None
        with self._lock:
            connection = self.connections.get(sid)
            return dict(connection) if connection is not None else None

    def ensure_connection(self, sid: str, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            connection = self.connections.setdefault(sid, dict(defaults or {}))
            return dict(connection)

    def set_connection(self, sid: str, data: dict[str, Any]) -> None:
        with self._lock:
            self.connections[sid] = dict(data)

    def update_connection(self, sid: str, **updates: Any) -> dict[str, Any]:
        with self._lock:
            connection = self.connections.setdefault(sid, {})
            connection.update(updates)
            return dict(connection)

    def update_connection_if_present(self, sid: str, **updates: Any) -> dict[str, Any] | None:
        with self._lock:
            connection = self.connections.get(sid)
            if connection is None:
                return None
            connection.update(updates)
            return dict(connection)

    def unbind_connection(
        self,
        sid: str,
        *,
        expected_session_id: int | None = None,
        expected_player_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Clear a socket's player/session binding and return its prior snapshot."""

        with self._lock:
            connection = self.connections.get(sid)
            if connection is None:
                return None
            if (
                expected_session_id is not None
                and connection.get('session_id') != expected_session_id
            ):
                return None
            if (
                expected_player_id is not None
                and connection.get('player_id') != expected_player_id
            ):
                return None
            previous = dict(connection)
            connection['session_id'] = None
            connection['player_id'] = None
            return previous

    def pop_connection(self, sid: str) -> dict[str, Any] | None:
        with self._lock:
            return self.connections.pop(sid, None)

    def active_player_payloads(self, session_id: int) -> list[dict[str, Any]]:
        with self._lock:
            payloads = []
            for player_data in self.active_players.get(session_id, {}).values():
                payload = {key: value for key, value in player_data.items() if not key.startswith('_')}
                payload.pop('is_typing', None)
                if self._typing_sids_for(player_data):
                    payload['is_typing'] = True
                payloads.append(payload)
            return payloads

    def ensure_session(self, session_id: int) -> None:
        with self._lock:
            self.active_players.setdefault(session_id, {})

    def track_active_player(self, session_id: int, player_data: dict[str, Any], sid: str) -> bool:
        with self._lock:
            session_players = self.active_players.setdefault(session_id, {})
            player_id = player_data['id']
            existing = session_players.get(player_id)
            if existing:
                existing.update({key: value for key, value in player_data.items() if not key.startswith('_')})
                sids = existing.setdefault('_sids', set())
                sids.add(sid)
                return False

            session_players[player_id] = {
                **player_data,
                '_sids': {sid},
            }
            return True

    def _typing_sids_for(self, player_data: dict[str, Any]) -> set[str]:
        typing_sids = player_data.get('_typing_sids')
        return typing_sids if isinstance(typing_sids, set) else set()

    def player_is_typing(self, session_id: int, player_id: int) -> bool:
        with self._lock:
            session_players = self.active_players.get(session_id)
            if not session_players:
                return False
            player_data = session_players.get(player_id)
            return bool(self._typing_sids_for(player_data)) if player_data else False

    def set_player_typing(self, session_id: int, player_id: int, sid: str, is_typing: bool) -> bool:
        with self._lock:
            session_players = self.active_players.get(session_id)
            if not session_players:
                return False

            player_data = session_players.get(player_id)
            if not player_data:
                return False

            typing_sids = self._typing_sids_for(player_data)
            was_typing = bool(typing_sids)
            if is_typing:
                typing_sids.add(sid)
            else:
                typing_sids.discard(sid)

            if typing_sids:
                player_data['_typing_sids'] = typing_sids
            else:
                player_data.pop('_typing_sids', None)

            return was_typing != bool(typing_sids)

    def music_state(self, session_id: int) -> dict[str, Any] | None:
        with self._lock:
            state = self.session_music.get(session_id)
            return dict(state) if state else None

    def set_music_state(self, session_id: int, state: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            next_state = {**state, 'session_id': session_id}
            self.session_music[session_id] = next_state
            return dict(next_state)

    def release_active_player(self, session_id: int, player_id: int, sid: str) -> bool:
        with self._lock:
            session_players = self.active_players.get(session_id)
            if not session_players:
                return False

            existing = session_players.get(player_id)
            if not existing:
                return False

            sids = existing.get('_sids')
            if not isinstance(sids, set):
                sids = set()
            sids.discard(sid)
            if sids:
                typing_sids = self._typing_sids_for(existing)
                typing_sids.discard(sid)
                if typing_sids:
                    existing['_typing_sids'] = typing_sids
                else:
                    existing.pop('_typing_sids', None)
                existing['_sids'] = sids
                return False

            del session_players[player_id]
            if not session_players:
                del self.active_players[session_id]
            return True

    def clear(self) -> None:
        with self._lock:
            self.active_players.clear()
            self.connections.clear()
            self.session_music.clear()
