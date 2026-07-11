from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from aidm_server.provider_priority import ProviderPriorityGate


def test_foreground_provider_calls_may_overlap():
    gate = ProviderPriorityGate()
    both_entered = Barrier(2)

    def enter_foreground():
        with gate.foreground():
            both_entered.wait(timeout=2)
            active_foreground = gate.snapshot().active_foreground
            both_entered.wait(timeout=2)
            return active_foreground

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: enter_foreground(), range(2)))

    assert results == [2, 2]
    assert gate.snapshot().active_foreground == 0


def test_waiting_foreground_runs_before_next_background_call():
    gate = ProviderPriorityGate()
    first_background_entered = Event()
    release_first_background = Event()
    foreground_entered = Event()
    release_foreground = Event()
    second_background_entered = Event()
    order: list[str] = []

    def first_background():
        with gate.background():
            order.append('background-1')
            first_background_entered.set()
            assert release_first_background.wait(timeout=2)

    def foreground():
        with gate.foreground():
            order.append('foreground')
            foreground_entered.set()
            assert release_foreground.wait(timeout=2)

    def second_background():
        with gate.background():
            order.append('background-2')
            second_background_entered.set()

    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(first_background)
        assert first_background_entered.wait(timeout=2)
        second = executor.submit(second_background)
        foreground_future = executor.submit(foreground)
        assert gate.wait_until(
            lambda snapshot: snapshot.waiting_foreground == 1,
            timeout_seconds=2,
        )

        release_first_background.set()
        assert foreground_entered.wait(timeout=2)
        assert not second_background_entered.is_set()
        release_foreground.set()

        first.result(timeout=2)
        foreground_future.result(timeout=2)
        second.result(timeout=2)

    assert order == ['background-1', 'foreground', 'background-2']


def test_background_waits_for_all_active_foreground_calls():
    gate = ProviderPriorityGate()
    release_foreground = Event()
    first_entered = Event()
    second_entered = Event()
    background_entered = Event()

    def foreground(entered: Event):
        with gate.foreground():
            entered.set()
            assert release_foreground.wait(timeout=2)

    def background():
        with gate.background():
            background_entered.set()

    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(foreground, first_entered)
        second = executor.submit(foreground, second_entered)
        assert first_entered.wait(timeout=2)
        assert second_entered.wait(timeout=2)
        background_future = executor.submit(background)
        assert not background_entered.wait(timeout=0.05)
        release_foreground.set()
        first.result(timeout=2)
        second.result(timeout=2)
        background_future.result(timeout=2)

    assert background_entered.is_set()


def test_foreground_reservation_releases_resources_before_waiting_on_background():
    gate = ProviderPriorityGate()
    background_entered = Event()
    release_background = Event()
    resources_released = Event()
    foreground_entered = Event()

    def background():
        with gate.background():
            background_entered.set()
            assert release_background.wait(timeout=2)

    def foreground():
        with gate.foreground_reservation() as activate:
            resources_released.set()
            activate()
            foreground_entered.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        background_future = executor.submit(background)
        assert background_entered.wait(timeout=2)
        foreground_future = executor.submit(foreground)

        assert resources_released.wait(timeout=2)
        assert not foreground_entered.is_set()
        assert gate.snapshot().waiting_foreground == 1

        release_background.set()
        background_future.result(timeout=2)
        foreground_future.result(timeout=2)

    assert foreground_entered.is_set()
    assert gate.snapshot().active_foreground == 0


def test_abandoned_foreground_reservation_does_not_block_background():
    gate = ProviderPriorityGate()

    with gate.foreground_reservation():
        assert gate.snapshot().waiting_foreground == 1

    with gate.background():
        assert gate.snapshot().background_active is True


def test_provider_gate_releases_slots_after_exceptions():
    gate = ProviderPriorityGate()

    try:
        with gate.background():
            raise RuntimeError('provider failed')
    except RuntimeError:
        pass

    with gate.foreground():
        assert gate.snapshot().active_foreground == 1

    assert gate.snapshot().background_active is False
