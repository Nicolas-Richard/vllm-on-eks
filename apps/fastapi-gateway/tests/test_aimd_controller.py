"""Unit tests for AIMDController.

These exercise the additive-increase / multiplicative-decrease loop against a
real prometheus_client.Histogram (with the same buckets as
gateway_ttft_seconds) and a stub scheduler. Using a real histogram keeps the
bucket-snapshot + delta + interpolation path actually executed, not mocked.
"""

import asyncio
import math
import time

from prometheus_client import CollectorRegistry, Histogram

from app.metrics import gateway_aimd_action_total
from app.scheduler import AIMDController

# Same buckets as app.metrics.ttft_seconds.
TTFT_BUCKETS = (
    0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75,
    1.0, 1.5, 2.0, 3.0, 5.0, 7.5, 10.0, 15.0, 20.0, 30.0,
    45.0, 60.0, 90.0, 120.0, 180.0, 240.0, 300.0, 450.0, 600.0,
)

TICK_S = 0.05
# Wait long enough to span ≥3 ticks so the rolling-window delta has data.
TICK_WAIT = 0.25


def _fresh_histogram() -> Histogram:
    """A private-registry copy of gateway_ttft_seconds.

    Avoids polluting the global default registry across tests and keeps each
    test's bucket snapshot independent of the rest of the suite.
    """
    reg = CollectorRegistry()
    return Histogram(
        "test_ttft_seconds",
        "test ttft histogram",
        labelnames=("tenant_id", "status"),
        buckets=TTFT_BUCKETS,
        registry=reg,
    )


class _StubScheduler:
    """Minimal surface AIMDController consumes."""

    def __init__(self, cap: int = 16, queue_nonempty: bool = True):
        self.cap_per_worker = cap
        self._queue_nonempty = queue_nonempty
        self.set_calls: list[int] = []

    def any_queue_nonempty(self) -> bool:
        return self._queue_nonempty

    def has_demand(self) -> bool:
        # Tests that flip queue_nonempty also represent overall demand.
        return self._queue_nonempty

    async def set_cap_per_worker(self, value: int) -> None:
        self.set_calls.append(value)
        self.cap_per_worker = value


def _action_count(action: str) -> float:
    return gateway_aimd_action_total.labels(action=action)._value.get()


async def _run_with_load(
    controller: AIMDController,
    hist: Histogram,
    latency_s: float | None,
    *,
    wait: float = TICK_WAIT,
) -> None:
    """Run the controller while continuously feeding observations.

    The controller's window-delta only sees samples that arrive between
    snapshots. Continuously observing during the wait keeps each tick's
    delta non-empty and avoids flakiness from "did samples land before or
    after this tick's snapshot."

    Pass ``latency_s=None`` to run with no observations (insufficient-samples
    test).
    """
    await controller.start()
    try:
        deadline = asyncio.get_running_loop().time() + wait
        while asyncio.get_running_loop().time() < deadline:
            if latency_s is not None:
                # 5 samples per mini-step; with TICK_S=0.05 and step=0.01,
                # ~25 samples land between any two snapshots → well above
                # the 10-sample threshold.
                for _ in range(5):
                    hist.labels(tenant_id="test", status="ok").observe(latency_s)
            await asyncio.sleep(0.01)
    finally:
        await controller.stop()


async def test_increases_cap_when_p99_below_target_with_demand():
    hist = _fresh_histogram()
    sched = _StubScheduler(cap=16, queue_nonempty=True)
    controller = AIMDController(
        sched, hist,
        target_p99_s=2.0, tick_s=TICK_S, window_s=2.0,
        cap_min=4, cap_max=64,
    )
    await _run_with_load(controller, hist, latency_s=0.1)
    # Each AI tick is +1; with multiple ticks we expect cap > 16.
    assert sched.cap_per_worker > 16
    assert all(v == 16 + i + 1 for i, v in enumerate(sched.set_calls))


async def test_holds_when_p99_below_target_but_queues_empty():
    hist = _fresh_histogram()
    sched = _StubScheduler(cap=16, queue_nonempty=False)
    before_hold = _action_count("hold")
    controller = AIMDController(
        sched, hist,
        target_p99_s=2.0, tick_s=TICK_S, window_s=2.0,
        cap_min=4, cap_max=64,
    )
    await _run_with_load(controller, hist, latency_s=0.1)
    assert sched.cap_per_worker == 16
    assert sched.set_calls == []
    assert _action_count("hold") > before_hold


async def test_decreases_cap_when_p99_above_target():
    hist = _fresh_histogram()
    sched = _StubScheduler(cap=16, queue_nonempty=True)
    controller = AIMDController(
        sched, hist,
        target_p99_s=2.0, tick_s=TICK_S, window_s=2.0,
        cap_min=4, cap_max=64,
        decrease_step=1,
    )
    await _run_with_load(controller, hist, latency_s=5.0)
    # Subtractive: 16 → 15 → 14 → ... over multiple ticks.
    assert sched.cap_per_worker < 16
    assert sched.set_calls and sched.set_calls[0] == 15


async def test_decrease_step_configurable():
    """A larger decrease_step shrinks cap by the configured amount per tick."""
    hist = _fresh_histogram()
    sched = _StubScheduler(cap=20, queue_nonempty=True)
    controller = AIMDController(
        sched, hist,
        target_p99_s=2.0, tick_s=TICK_S, window_s=2.0,
        cap_min=4, cap_max=64,
        decrease_step=4,
    )
    await _run_with_load(controller, hist, latency_s=5.0)
    # First decrease: 20 → 16 (subtract 4).
    assert sched.set_calls and sched.set_calls[0] == 16


async def test_clamps_to_min():
    hist = _fresh_histogram()
    sched = _StubScheduler(cap=5, queue_nonempty=True)
    controller = AIMDController(
        sched, hist,
        target_p99_s=2.0, tick_s=TICK_S, window_s=2.0,
        cap_min=4, cap_max=64,
        decrease_step=2,
    )
    await _run_with_load(controller, hist, latency_s=5.0)
    # 5 - 2 = 3, clamped to cap_min=4. Subsequent ticks stay at 4.
    assert sched.cap_per_worker == 4
    assert all(v == 4 for v in sched.set_calls)


async def test_clamps_to_max():
    hist = _fresh_histogram()
    sched = _StubScheduler(cap=64, queue_nonempty=True)
    controller = AIMDController(
        sched, hist,
        target_p99_s=2.0, tick_s=TICK_S, window_s=2.0,
        cap_min=4, cap_max=64,
    )
    await _run_with_load(controller, hist, latency_s=0.1)
    # cap is already at max; +1 clamps. cap stays at 64; no setter calls
    # because new == cur.
    assert sched.cap_per_worker == 64
    assert sched.set_calls == []


async def test_holds_with_insufficient_samples():
    hist = _fresh_histogram()
    # No observations at all during the run → p99 NaN → hold.
    sched = _StubScheduler(cap=16, queue_nonempty=True)
    before_hold = _action_count("hold")
    controller = AIMDController(
        sched, hist,
        target_p99_s=2.0, tick_s=TICK_S, window_s=2.0,
        cap_min=4, cap_max=64,
    )
    await _run_with_load(controller, hist, latency_s=None)
    assert sched.cap_per_worker == 16
    assert sched.set_calls == []
    assert _action_count("hold") > before_hold


async def test_p99_clamps_when_samples_in_overflow_bucket():
    # Latencies of 1000.0 exceed the largest finite edge (600.0), landing in
    # the +Inf bucket. The controller should clamp p99 to the largest finite
    # edge (600.0) and treat that as "p99 finite, > target" → decrease.
    hist = _fresh_histogram()
    sched = _StubScheduler(cap=16, queue_nonempty=True)
    controller = AIMDController(
        sched, hist,
        target_p99_s=2.0, tick_s=TICK_S, window_s=2.0,
        cap_min=4, cap_max=64,
        decrease_step=1,
    )
    await _run_with_load(controller, hist, latency_s=1000.0)
    # Decrease path fired → p99 was finite (clamped to 600.0) and > target.
    assert sched.set_calls and sched.set_calls[0] == 15
    assert sched.cap_per_worker < 16


def test_observe_p99_returns_nan_after_long_idle():
    # Pin the I-2 fix: after a period longer than window_s with no new
    # observations, _observe_p99 should drop all stale snapshots and return
    # NaN rather than computing a delta against a snapshot far outside the
    # window.
    hist = _fresh_histogram()
    sched = _StubScheduler(cap=16, queue_nonempty=True)
    window_s = 1.0
    controller = AIMDController(
        sched, hist,
        target_p99_s=2.0, tick_s=TICK_S, window_s=window_s,
        cap_min=4, cap_max=64,
    )

    # Manually populate _snapshots with stale entries — well outside the
    # window. The empty-bucket dicts don't matter; the trim should evict
    # them all before bucket math runs.
    stale_t = time.monotonic() - (window_s + 5.0)
    for i in range(20):
        controller._snapshots.append((stale_t + i * 0.001, {}))

    result = controller._observe_p99()
    assert math.isnan(result)


async def test_emits_action_metrics():
    # Drive one tick of each action and check the corresponding counter
    # incremented. Three short controllers (one per intended action) avoids
    # racing transitions in a single run.
    before_inc = _action_count("increase")
    before_dec = _action_count("decrease")
    before_hold = _action_count("hold")

    cases = [
        (_fresh_histogram(), _StubScheduler(cap=16, queue_nonempty=True), 0.1),
        (_fresh_histogram(), _StubScheduler(cap=16, queue_nonempty=True), 5.0),
        (_fresh_histogram(), _StubScheduler(cap=16, queue_nonempty=False), 0.1),
    ]
    for hist, sched, latency in cases:
        controller = AIMDController(
            sched, hist,
            target_p99_s=2.0, tick_s=TICK_S, window_s=2.0,
            cap_min=4, cap_max=64,
        )
        await _run_with_load(controller, hist, latency_s=latency)

    assert _action_count("increase") > before_inc
    assert _action_count("decrease") > before_dec
    assert _action_count("hold") > before_hold
