"""Integration tests for Layer-2 adaptive budget wiring.

Pins the end-to-end path that the Layer-1 unit tests don't cover:

    lifespan → real TenantScheduler → real ttft_seconds histogram
             → real AIMDController / WorkerCapacityWatcher
             → real set_cap_per_worker / set_num_workers

The unit tests in ``tests/test_aimd_controller.py`` and
``tests/test_worker_watcher.py`` already pin the controllers' decision
logic against stubs (bucket math, demand gating, auth-error handling,
etc.). These tests deliberately skip the bucket-math edge cases and
focus on "the wiring works": observations made into the global
``ttft_seconds`` histogram move ``app.state.scheduler.cap_per_worker``,
and watcher events move ``app.state.scheduler.num_workers``.

Tick durations are aggressive (``tick_s=0.05``, ``window_s=0.5``) to
keep wall-clock under a few seconds per test. Asserts use a poll-with-
timeout pattern (``_eventually``) to tolerate one-tick scheduling
jitter.
"""

from __future__ import annotations

import asyncio
import importlib
import textwrap
import time
from typing import Awaitable, Callable

from fastapi.testclient import TestClient

from app.scheduler import WorkerCapacityWatcher as RealWorkerCapacityWatcher


def _write_cfg(tmp_path, body: str) -> str:
    cfg = tmp_path / "tenants.yaml"
    cfg.write_text(body)
    return str(cfg)


def _reload_main(monkeypatch, cfg_path: str):
    monkeypatch.setenv("TENANT_A_KEY", "key-a")
    monkeypatch.setenv("TENANTS_PATH", cfg_path)
    monkeypatch.setenv("ACQUIRE_TIMEOUT_S", "5.0")
    import app.main as main_module
    importlib.reload(main_module)
    return main_module


_BASE_TENANTS = textwrap.dedent("""\
    tenants:
      - id: tenant-a
        name: "A"
        weight: 1
        queue_max: 4
        key_env: TENANT_A_KEY
    """)


def _eventually(
    predicate: Callable[[], bool],
    *,
    timeout_s: float = 2.0,
    interval_s: float = 0.02,
) -> bool:
    """Poll ``predicate`` until True or ``timeout_s`` elapses.

    Returns the final truth value so callers can assert on it. Wall-clock
    based (not asyncio.sleep) because the TestClient context drives the
    event loop on a background thread.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


def _aimd_cfg(
    *,
    cap_per_worker: int,
    target: float = 2.0,
    tick_s: float = 0.05,
    window_s: float = 0.5,
    cap_min: int = 1,
    cap_max: int = 64,
) -> str:
    return (
        "caps_enabled: true\n"
        "num_workers: 1\n"
        f"cap_per_worker: {cap_per_worker}\n"
        "aimd:\n"
        "  enabled: true\n"
        f"  target_p99_ttft_s: {target}\n"
        f"  tick_s: {tick_s}\n"
        f"  window_s: {window_s}\n"
        f"  cap_per_worker_min: {cap_min}\n"
        f"  cap_per_worker_max: {cap_max}\n"
        + _BASE_TENANTS
    )


def _flood_ttft(main_module, latency_s: float, *, samples: int = 80) -> None:
    """Push ``samples`` observations into the real ``ttft_seconds`` histogram.

    The AIMD controller polls this histogram on its tick schedule. We feed
    well above the 10-sample threshold so window-deltas can't fall below
    the minimum-sample floor.
    """
    hist = main_module.ttft_seconds.labels(tenant_id="tenant-a", status="200")
    for _ in range(samples):
        hist.observe(latency_s)


# ---------------------------------------------------------------------------
# Test 1: AIMD contracts under burst.
# ---------------------------------------------------------------------------


def test_aimd_contracts_under_synthetic_burst(tmp_path, monkeypatch):
    """High p99 → cap halves within ~3 ticks via the lifespan-wired path.

    Bypasses the upstream proxy by observing latencies directly into the
    real ``ttft_seconds`` histogram that the lifespan handed to AIMD.
    Verifies the controller is actually polling that histogram and
    flushing decisions through to ``scheduler.cap_per_worker``.
    """
    cfg = _write_cfg(tmp_path, _aimd_cfg(cap_per_worker=16))
    main_module = _reload_main(monkeypatch, cfg)

    with TestClient(main_module.app) as client:
        assert client.get("/healthz").status_code == 200
        scheduler = main_module.app.state.scheduler
        assert scheduler.cap_per_worker == 16

        # Continuously feed high latencies so each tick's window has
        # fresh samples (a single front-loaded burst would age out of the
        # 0.5s rolling window). Cap halving fires the moment p99 lands
        # over target with a non-empty queue assertion or NaN guard
        # already passed — so demand gating doesn't matter for the
        # decrease branch.
        deadline = time.monotonic() + 1.5
        contracted = False
        while time.monotonic() < deadline:
            _flood_ttft(main_module, latency_s=5.0, samples=20)
            if scheduler.cap_per_worker <= 8:
                contracted = True
                break
            time.sleep(0.02)

        assert contracted, (
            f"cap did not contract within deadline; "
            f"final cap={scheduler.cap_per_worker}"
        )
        # First halve must take cap to exactly cur//2 == 8 (cap_min=1
        # doesn't intervene). Subsequent ticks may drive it lower.
        assert scheduler.cap_per_worker <= 8


# ---------------------------------------------------------------------------
# Test 2: AIMD recovers after burst ends.
# ---------------------------------------------------------------------------


def test_aimd_recovers_after_burst_ends(tmp_path, monkeypatch):
    """Low p99 + non-empty queue → cap climbs by +1.

    Drives demand-gating by poking the scheduler's tenant state directly:
    we make ``any_queue_nonempty()`` return True without actually
    queueing requests through the dispatch loop (which would consume
    them). This is acceptable for an integration test of our own gateway
    — the scheduler internals are part of the same package under test.
    """
    cfg = _write_cfg(
        tmp_path,
        _aimd_cfg(cap_per_worker=8, cap_min=1, cap_max=64),
    )
    main_module = _reload_main(monkeypatch, cfg)

    with TestClient(main_module.app) as client:
        assert client.get("/healthz").status_code == 200
        scheduler = main_module.app.state.scheduler
        start_cap = scheduler.cap_per_worker
        assert start_cap == 8

        # Force the demand-gate True by stamping live_count > 0 directly.
        # The drain loop won't consume anything because there are no
        # actual _QueuedRequest entries — live_count is just a counter.
        # This avoids a fixture race where real requests get dispatched
        # mid-test.
        state = scheduler._states["tenant-a"]
        state.live_count = 1

        # Feed sustained low latencies so p99 stays well under 2.0s.
        deadline = time.monotonic() + 1.5
        climbed = False
        while time.monotonic() < deadline:
            _flood_ttft(main_module, latency_s=0.05, samples=20)
            if scheduler.cap_per_worker > start_cap:
                climbed = True
                break
            time.sleep(0.02)

        # Restore live_count before scheduler.stop() runs in lifespan
        # shutdown — otherwise the shutdown's "drain remaining queue
        # entries" loop will under-count.
        state.live_count = 0

        assert climbed, (
            f"cap did not climb within deadline; "
            f"final cap={scheduler.cap_per_worker}, start={start_cap}"
        )
        # Each tick is +1 (additive increase); the test passes if we got
        # at least one increment.
        assert scheduler.cap_per_worker >= start_cap + 1


# ---------------------------------------------------------------------------
# Test 3: WorkerCapacityWatcher → scheduler resize.
# ---------------------------------------------------------------------------


def test_watcher_responds_to_endpoint_change(tmp_path, monkeypatch):
    """K8s EndpointSlice events flow through to ``scheduler.global_budget()``.

    Mocks the K8s watch stream by feeding events through an
    ``asyncio.Queue`` that the test puts into. The watcher's
    ``_consume_stream`` drains the iterator and forwards Ready-endpoint
    counts to ``scheduler.set_num_workers`` — same code path as in
    production.
    """
    # The queue lives on the test side; the stream factory returns an
    # async iterator that drains it. We wire the queue lazily because it
    # must be created on the lifespan loop (the FastAPI test client
    # spawns one); creating it on the test loop causes a "got Future
    # attached to a different loop" error.
    state: dict = {"queue": None, "loop": None}

    async def _stream_iter():
        # Capture the loop the watcher is running on so the test can
        # cross-thread put_nowait via call_soon_threadsafe.
        if state["queue"] is None:
            state["loop"] = asyncio.get_running_loop()
            state["queue"] = asyncio.Queue()
        while True:
            evt = await state["queue"].get()
            if evt is None:
                return
            yield evt

    def _stream_factory(_namespace, _service_name):
        return _stream_iter()

    class WatcherWithStub(RealWorkerCapacityWatcher):
        def __init__(self, scheduler, *, namespace, service_name):
            super().__init__(
                scheduler,
                namespace=namespace,
                service_name=service_name,
                stream_factory=_stream_factory,
            )

    cfg = _write_cfg(
        tmp_path,
        "caps_enabled: true\n"
        "num_workers: 1\n"
        "cap_per_worker: 4\n"
        "worker_watcher:\n"
        "  enabled: true\n"
        "  namespace: vllm\n"
        "  service_name: vllm-engine\n"
        + _BASE_TENANTS,
    )
    main_module = _reload_main(monkeypatch, cfg)
    assert main_module.WorkerCapacityWatcher is RealWorkerCapacityWatcher
    monkeypatch.setattr(main_module, "WorkerCapacityWatcher", WatcherWithStub)

    # Build fake EndpointSlice objects with the surface the watcher reads.
    from types import SimpleNamespace

    def _slice(name: str, ready_count: int):
        return SimpleNamespace(
            metadata=SimpleNamespace(name=name),
            endpoints=[
                SimpleNamespace(conditions=SimpleNamespace(ready=True))
                for _ in range(ready_count)
            ],
        )

    def _put(evt):
        # Wait for the watcher to have entered _stream_iter (which
        # creates the queue on the lifespan loop).
        assert _eventually(lambda: state["queue"] is not None, timeout_s=2.0), (
            "watcher never entered stream_iter"
        )
        loop = state["loop"]
        queue = state["queue"]
        loop.call_soon_threadsafe(queue.put_nowait, evt)

    with TestClient(main_module.app) as client:
        assert client.get("/healthz").status_code == 200
        scheduler = main_module.app.state.scheduler
        # Bootstrap value from config: num_workers=1, cap=4 → budget 4.
        assert scheduler.num_workers == 1
        assert scheduler.global_budget() == 4

        # Event 1: ADDED slice with 2 ready endpoints.
        _put({"type": "ADDED", "object": _slice("a", ready_count=2)})
        assert _eventually(
            lambda: scheduler.num_workers == 2, timeout_s=2.0
        ), f"num_workers did not reach 2; got {scheduler.num_workers}"
        assert scheduler.global_budget() == 2 * scheduler.cap_per_worker

        # Event 2: MODIFIED slice with 4 ready endpoints (scale-up).
        _put({"type": "MODIFIED", "object": _slice("a", ready_count=4)})
        assert _eventually(
            lambda: scheduler.num_workers == 4, timeout_s=2.0
        ), f"num_workers did not reach 4; got {scheduler.num_workers}"
        assert scheduler.global_budget() == 4 * scheduler.cap_per_worker
