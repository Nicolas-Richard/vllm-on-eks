"""Unit tests for WorkerCapacityWatcher.

Mocks the K8s watch stream by injecting a ``stream_factory`` into the
watcher. Auth-error behavior is exercised via a factory that raises
``ApiException(status=403)``; the watcher's real ``_watch_endpointslices``
maps that to ``WatcherAuthError`` — so the tests construct that exception
directly and rely on a small wrapper to mirror the real branch.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.scheduler import (
    WatcherAuthError,
    WorkerCapacityWatcher,
    _count_ready_endpoints,
)
from app.metrics import gateway_worker_watcher_events_total


# ---------- fakes -----------------------------------------------------------


class FakeEndpoint:
    def __init__(self, ready: bool | None):
        self.conditions = SimpleNamespace(ready=ready)


class FakeSlice:
    def __init__(
        self,
        name: str,
        ready_count: int,
        total: int | None = None,
    ):
        self.metadata = SimpleNamespace(name=name)
        n = total if total is not None else ready_count
        self.endpoints = [FakeEndpoint(i < ready_count) for i in range(n)]


class StubScheduler:
    """Minimal surface WorkerCapacityWatcher consumes."""

    def __init__(self, initial_num_workers: int = 2):
        self.num_workers = initial_num_workers
        self.set_calls: list[int] = []

    async def set_num_workers(self, value: int) -> None:
        self.set_calls.append(value)
        self.num_workers = value


def _make_async_iter(events):
    """Return a callable producing a fresh async iterator over ``events``."""

    async def _iter():
        for ev in events:
            yield ev

    return _iter


def _counter_value(event: str) -> float:
    return gateway_worker_watcher_events_total.labels(event=event)._value.get()


# ---------- tests -----------------------------------------------------------


async def test_calls_set_num_workers_on_event():
    """Initial endpoint read updates field."""
    scheduler = StubScheduler(initial_num_workers=0)
    events = [
        {"type": "ADDED", "object": FakeSlice("slice-a", ready_count=2)},
    ]

    def factory(namespace, service_name):
        return _make_async_iter(events)()

    watcher = WorkerCapacityWatcher(
        scheduler,
        namespace="vllm",
        service_name="engine-svc",
        stream_factory=factory,
    )
    # Drive the stream once directly — no backoff needed.
    await watcher._watch_endpointslices()

    assert scheduler.set_calls == [2]
    assert scheduler.num_workers == 2


async def test_reconnects_after_stream_error():
    """Disconnect handling: reconnect with backoff, eventually succeed.

    First stream raises ConnectionError; second stream yields an event and
    then awaits a sentinel ``Event`` that's never set, parking the loop in
    a steady state we can assert against without spinning.
    """
    scheduler = StubScheduler(initial_num_workers=0)
    calls = {"n": 0}
    park = asyncio.Event()  # never set; second stream awaits this forever

    def factory(namespace, service_name):
        calls["n"] += 1
        if calls["n"] == 1:

            async def _broken():
                raise ConnectionError("boom")
                yield  # pragma: no cover — make this an async generator

            return _broken()

        async def _good():
            yield {"type": "ADDED", "object": FakeSlice("slice-a", ready_count=3)}
            # Park; on stop() the cancel propagates and the watcher exits.
            await park.wait()

        return _good()

    reconnect_before = _counter_value("reconnect")
    error_before = _counter_value("error")

    watcher = WorkerCapacityWatcher(
        scheduler,
        namespace="vllm",
        service_name="engine-svc",
        stream_factory=factory,
        backoff_initial=0.01,
        backoff_max=0.01,
    )
    await watcher.start()
    for _ in range(200):
        if scheduler.set_calls:
            break
        await asyncio.sleep(0.01)
    await watcher.stop()

    assert scheduler.set_calls == [3]
    assert calls["n"] >= 2
    assert _counter_value("reconnect") - reconnect_before >= 1
    assert _counter_value("error") - error_before >= 1


async def test_holds_value_during_disconnect():
    """Stale-but-serving guarantee: between events the scheduler value sticks.

    First stream lands a value of 4. All subsequent reconnects raise so
    the loop sleeps in backoff. Across the wait the scheduler must hold
    its last value (no `set_num_workers(0)` injected on disconnect).
    """
    scheduler = StubScheduler(initial_num_workers=0)
    calls = {"n": 0}

    def factory(namespace, service_name):
        calls["n"] += 1
        if calls["n"] == 1:

            async def _good():
                yield {"type": "ADDED", "object": FakeSlice("slice-a", ready_count=4)}

            return _good()

        async def _broken():
            raise ConnectionError("disconnect")
            yield  # pragma: no cover

        return _broken()

    watcher = WorkerCapacityWatcher(
        scheduler,
        namespace="vllm",
        service_name="engine-svc",
        stream_factory=factory,
        # Use a relatively long backoff so subsequent reconnects don't
        # produce log spam during the assertion window.
        backoff_initial=0.5,
        backoff_max=0.5,
    )
    await watcher.start()
    for _ in range(200):
        if scheduler.set_calls:
            break
        await asyncio.sleep(0.01)
    # Sit through enough wall-clock for several disconnect/backoff cycles to
    # *not* mutate the value.
    await asyncio.sleep(0.2)
    assert scheduler.set_calls == [4]
    assert scheduler.num_workers == 4
    await watcher.stop()


async def test_reads_zero_when_endpoints_empty():
    """Zero ready endpoints → set_num_workers(0)."""
    scheduler = StubScheduler(initial_num_workers=2)
    events = [
        # Slice present but no Ready endpoints.
        {
            "type": "ADDED",
            "object": FakeSlice("slice-a", ready_count=0, total=3),
        },
    ]

    def factory(namespace, service_name):
        return _make_async_iter(events)()

    watcher = WorkerCapacityWatcher(
        scheduler,
        namespace="vllm",
        service_name="engine-svc",
        stream_factory=factory,
    )
    await watcher._watch_endpointslices()

    assert scheduler.set_calls == [0]


async def test_aborts_on_auth_error():
    """403/401 → loop returns, no further reconnect attempts."""
    scheduler = StubScheduler(initial_num_workers=2)

    def factory(namespace, service_name):
        async def _gen():
            raise WatcherAuthError("simulated 403")
            yield  # pragma: no cover

        return _gen()

    watcher = WorkerCapacityWatcher(
        scheduler,
        namespace="vllm",
        service_name="engine-svc",
        stream_factory=factory,
        backoff_initial=0.01,
        backoff_max=0.01,
    )
    await watcher.start()
    # Loop should complete quickly — auth errors abort.
    for _ in range(200):
        if watcher._task is not None and watcher._task.done():
            break
        await asyncio.sleep(0.01)
    assert watcher._task is not None
    assert watcher._task.done()
    assert scheduler.set_calls == []
    await watcher.stop()


async def test_translates_api_exception_403_to_auth_error():
    """A real ``ApiException(status=403)`` from the stream factory must
    propagate as ``WatcherAuthError`` — exercising the production
    ``_translate_api_exception`` branch, not a pre-translated stub."""
    from kubernetes_asyncio.client.exceptions import ApiException

    scheduler = StubScheduler(initial_num_workers=2)

    def factory(namespace, service_name):
        raise ApiException(status=403, reason="Forbidden")

    watcher = WorkerCapacityWatcher(
        scheduler,
        namespace="vllm",
        service_name="engine-svc",
        stream_factory=factory,
    )
    with pytest.raises(WatcherAuthError):
        await watcher._watch_endpointslices()


async def test_translates_api_exception_401_to_auth_error():
    """Same coverage as the 403 case for the 401 branch."""
    from kubernetes_asyncio.client.exceptions import ApiException

    scheduler = StubScheduler(initial_num_workers=2)

    def factory(namespace, service_name):
        raise ApiException(status=401, reason="Unauthorized")

    watcher = WorkerCapacityWatcher(
        scheduler,
        namespace="vllm",
        service_name="engine-svc",
        stream_factory=factory,
    )
    with pytest.raises(WatcherAuthError):
        await watcher._watch_endpointslices()


async def test_does_not_translate_api_exception_500():
    """Non-auth ApiException statuses must NOT become ``WatcherAuthError`` —
    they go down the regular reconnect-with-backoff path."""
    from kubernetes_asyncio.client.exceptions import ApiException

    scheduler = StubScheduler(initial_num_workers=2)

    def factory(namespace, service_name):
        raise ApiException(status=500, reason="Server Error")

    watcher = WorkerCapacityWatcher(
        scheduler,
        namespace="vllm",
        service_name="engine-svc",
        stream_factory=factory,
    )
    with pytest.raises(ApiException):
        await watcher._watch_endpointslices()


async def test_increments_event_counters():
    """ADDED/MODIFIED/DELETED events bump the corresponding labels."""
    scheduler = StubScheduler(initial_num_workers=0)
    events = [
        {"type": "ADDED", "object": FakeSlice("a", ready_count=1)},
        {"type": "MODIFIED", "object": FakeSlice("a", ready_count=2)},
        {"type": "ADDED", "object": FakeSlice("b", ready_count=1)},
        {"type": "DELETED", "object": FakeSlice("a", ready_count=0)},
    ]

    def factory(namespace, service_name):
        return _make_async_iter(events)()

    watcher = WorkerCapacityWatcher(
        scheduler,
        namespace="vllm",
        service_name="engine-svc",
        stream_factory=factory,
    )

    added_before = _counter_value("added")
    modified_before = _counter_value("modified")
    deleted_before = _counter_value("deleted")

    await watcher._watch_endpointslices()

    assert _counter_value("added") - added_before == 2
    assert _counter_value("modified") - modified_before == 1
    assert _counter_value("deleted") - deleted_before == 1
    # Final state: slice-a deleted, slice-b has 1 ready → total 1.
    assert scheduler.set_calls[-1] == 1


# ---------- helper coverage -------------------------------------------------


def test_count_ready_endpoints_handles_unknown_as_not_ready():
    s = FakeSlice("x", ready_count=0, total=0)
    s.endpoints = [
        FakeEndpoint(True),
        FakeEndpoint(False),
        FakeEndpoint(None),  # K8s "unknown" — treat as not-ready
        FakeEndpoint(True),
    ]
    assert _count_ready_endpoints(s) == 2
