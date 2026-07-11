"""In-process foreground preference for AI provider calls.

Foreground calls may overlap. Background work is exclusive and may begin only
when no foreground call is active or waiting. The gate is intentionally
process-local; AIDM's supported production worker model currently uses one
Gunicorn worker.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Condition
import time


@dataclass(frozen=True)
class ProviderPrioritySnapshot:
    active_foreground: int
    waiting_foreground: int
    background_active: bool


class ProviderPriorityGate:
    def __init__(self):
        self._condition = Condition()
        self._active_foreground = 0
        self._waiting_foreground = 0
        self._background_active = False

    @contextmanager
    def foreground(self):
        with self.foreground_reservation() as activate:
            activate()
            yield

    @contextmanager
    def foreground_reservation(self):
        """Reserve foreground priority without waiting for an active background call.

        The returned callback performs the blocking transition into an active
        foreground slot. Callers can therefore register foreground demand,
        release database resources, and only then wait for provider access.
        """

        active = False
        with self._condition:
            self._waiting_foreground += 1
            self._condition.notify_all()

        def activate() -> None:
            nonlocal active
            with self._condition:
                if active:
                    return
                self._condition.wait_for(lambda: not self._background_active)
                self._waiting_foreground -= 1
                self._active_foreground += 1
                active = True
                self._condition.notify_all()

        try:
            yield activate
        finally:
            with self._condition:
                if active:
                    self._active_foreground = max(0, self._active_foreground - 1)
                else:
                    self._waiting_foreground = max(0, self._waiting_foreground - 1)
                self._condition.notify_all()

    @contextmanager
    def background(self):
        with self._condition:
            self._condition.wait_for(
                lambda: (
                    not self._background_active
                    and self._active_foreground == 0
                    and self._waiting_foreground == 0
                )
            )
            self._background_active = True
        try:
            yield
        finally:
            with self._condition:
                self._background_active = False
                self._condition.notify_all()

    def snapshot(self) -> ProviderPrioritySnapshot:
        with self._condition:
            return ProviderPrioritySnapshot(
                active_foreground=self._active_foreground,
                waiting_foreground=self._waiting_foreground,
                background_active=self._background_active,
            )

    def wait_until(
        self,
        predicate,
        *,
        timeout_seconds: float,
    ) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        with self._condition:
            while not predicate(self.snapshot_unlocked()):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def snapshot_unlocked(self) -> ProviderPrioritySnapshot:
        return ProviderPrioritySnapshot(
            active_foreground=self._active_foreground,
            waiting_foreground=self._waiting_foreground,
            background_active=self._background_active,
        )


provider_priority_gate = ProviderPriorityGate()


def foreground_provider_slot():
    return provider_priority_gate.foreground()


def foreground_provider_reservation():
    return provider_priority_gate.foreground_reservation()


def background_provider_slot():
    return provider_priority_gate.background()
