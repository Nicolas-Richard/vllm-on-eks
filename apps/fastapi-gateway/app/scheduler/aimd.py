from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from typing import TYPE_CHECKING

from app.metrics import gateway_aimd_action_total

if TYPE_CHECKING:
    from app.scheduler.core import TenantScheduler

logger = logging.getLogger(__name__)


class AIMDController:
    """Strict TCP-style AIMD controller for ``TenantScheduler.cap_per_worker``.

    On each tick: snapshot the bucket counters of the supplied
    ``gateway_ttft_seconds`` Histogram, accumulate snapshots in a rolling
    ``window_s`` deque, compute p99 over the window's bucket-deltas, and
    adjust the cap:

    - p99 > target * (1 + band)   → cap -= decrease_step (subtractive decrease).
    - p99 < target * (1 - band) *and* there is demand
                                  → cap += 1 (additive increase, demand-gated).
    - otherwise (deadband or no demand) → hold.

    Subtractive (rather than multiplicative) decrease matches the cost
    asymmetry of batched LLM serving: overshoot is graceful (tight queue
    sheds 504s, KV preemption is bounded) but losing concurrency is
    expensive (batching efficiency drops). A gentle -N step lets the
    controller walk back toward the operating point without collapsing
    throughput in one tick. Set ``decrease_step=cur//2`` to recover the
    classical AIMD halving form; default 1 is the gentlest AISD posture.

    After a *decrease* action, the next ``cooldown_ticks`` ticks are forced
    to "hold" so the previous step's effect can flush through the rolling
    window before the next decision. AI (increase) ticks are NOT followed by a
    cooldown — TCP slow-start spirit: be cautious about backing off, eager
    to climb when conditions allow. With ``cooldown_ticks=0`` the controller
    can act every tick.

    The deadband prevents 1-tick oscillation when p99 jitters across the
    target line. With ``target_band_pct=0.0`` the controller behaves
    identically to the strict spec form (no deadband).

    p99 below the minimum sample threshold (10 within the window) yields NaN
    and is treated as "hold" — same posture as during a NaN p99 reading.
    """

    def __init__(
        self,
        scheduler: "TenantScheduler",
        ttft_histogram,
        target_p99_s: float = 2.0,
        tick_s: float = 5.0,
        window_s: float = 30.0,
        cap_min: int = 4,
        cap_max: int = 64,
        target_band_pct: float = 0.0,
        cooldown_ticks: int = 0,
        decrease_step: int = 1,
    ) -> None:
        if tick_s <= 0:
            raise ValueError(f"tick_s must be > 0, got {tick_s}")
        if window_s <= 0:
            raise ValueError(f"window_s must be > 0, got {window_s}")
        if target_p99_s <= 0:
            raise ValueError(f"target_p99_s must be > 0, got {target_p99_s}")
        if cap_min < 0:
            raise ValueError(f"cap_min must be >= 0, got {cap_min}")
        if cap_max < cap_min:
            raise ValueError(f"cap_max ({cap_max}) must be >= cap_min ({cap_min})")
        if not 0.0 <= target_band_pct < 1.0:
            raise ValueError(
                f"target_band_pct must be in [0, 1), got {target_band_pct}"
            )
        if cooldown_ticks < 0:
            raise ValueError(f"cooldown_ticks must be >= 0, got {cooldown_ticks}")
        if decrease_step < 1:
            raise ValueError(f"decrease_step must be >= 1, got {decrease_step}")
        self._scheduler = scheduler
        self._hist = ttft_histogram
        self._target = target_p99_s
        self._tick_s = tick_s
        self._window_s = window_s
        self._cap_min = cap_min
        self._cap_max = cap_max
        self._upper = target_p99_s * (1.0 + target_band_pct)
        self._lower = target_p99_s * (1.0 - target_band_pct)
        self._cooldown_ticks = cooldown_ticks
        self._decrease_step = decrease_step
        # Force-hold counter: ticks of "hold" remaining after the last
        # non-hold action. Decremented each tick.
        self._cooldown_remaining = 0
        # deque of (monotonic_time, snapshot) where snapshot is dict[le -> count]
        self._snapshots: deque[tuple[float, dict[float, float]]] = deque()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        try:
            await self._loop()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Per spec: log loudly; let the task end. Scheduler keeps running
            # with the last cap_per_worker. Pod restart is the recovery path.
            logger.exception("AIMD loop crashed")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._tick_s)
            p99 = self._observe_p99()
            cur = self._scheduler.cap_per_worker
            if math.isnan(p99):
                self._record_action("hold")
                continue
            if self._cooldown_remaining > 0:
                # Force-hold while the previous action's effect propagates
                # through the rolling window. Avoids 1-tick AI/MD oscillation.
                self._cooldown_remaining -= 1
                self._record_action("hold")
                continue
            if p99 > self._upper:
                new = max(self._cap_min, cur - self._decrease_step)
                action = "decrease"
            elif self._scheduler.has_demand() and p99 < self._lower:
                new = min(self._cap_max, cur + 1)
                action = "increase"
            else:
                new = cur
                action = "hold"
            self._record_action(action)
            if new != cur:
                await self._scheduler.set_cap_per_worker(new)
            if action == "decrease":
                self._cooldown_remaining = self._cooldown_ticks

    def _record_action(self, action: str) -> None:
        gateway_aimd_action_total.labels(action=action).inc()

    def _snapshot_buckets(self) -> dict[float, float]:
        """Sum bucket counts across all label combinations of the histogram.

        Returns ``{le: cumulative_count}`` for every finite ``le`` plus
        ``math.inf`` for the implicit ``+Inf`` bucket. Uses the public
        ``collect()`` API rather than poking at private fields.
        """
        totals: dict[float, float] = {}
        for metric_family in self._hist.collect():
            for sample in metric_family.samples:
                if not sample.name.endswith("_bucket"):
                    continue
                le_str = sample.labels.get("le")
                if le_str is None:
                    continue
                # prometheus_client encodes +Inf as the literal string "+Inf"
                if le_str == "+Inf":
                    le = math.inf
                else:
                    try:
                        le = float(le_str)
                    except ValueError:
                        continue
                totals[le] = totals.get(le, 0.0) + sample.value
        return totals

    def _observe_p99(self) -> float:
        """Push a fresh snapshot, trim the window, and interpolate p99."""
        now = time.monotonic()
        snapshot = self._snapshot_buckets()
        self._snapshots.append((now, snapshot))

        # Trim entries older than window_s unconditionally. After a long-idle
        # period this can leave us with <2 snapshots; in that case return NaN
        # rather than computing a delta against a stale snapshot far outside
        # the window.
        cutoff = now - self._window_s
        while self._snapshots and self._snapshots[0][0] < cutoff:
            self._snapshots.popleft()

        if len(self._snapshots) < 2:
            return math.nan

        oldest = self._snapshots[0][1]
        newest = self._snapshots[-1][1]
        if not newest:
            return math.nan

        # Bucket-deltas across the window. Missing keys default to 0 — the
        # histogram label-set is stable so this only matters on a cold start.
        edges = sorted(newest.keys())
        deltas: list[tuple[float, float]] = []
        for le in edges:
            d = newest.get(le, 0.0) - oldest.get(le, 0.0)
            if d < 0:
                # Counters can only grow; negative implies a registry reset.
                # Treat as zero rather than poisoning the interpolation.
                d = 0.0
            deltas.append((le, d))

        # The +Inf bucket holds the running total; it's also the largest le.
        total = deltas[-1][1] if deltas else 0.0
        if total < 10:
            return math.nan

        target_rank = 0.99 * total
        prev_edge = 0.0
        prev_cum = 0.0
        for le, cum in deltas:
            if cum >= target_rank:
                if math.isinf(le):
                    # p99 falls in the open-ended tail. Clamp to the largest
                    # finite edge — the spec leaves this to implementer's
                    # judgment; clamping keeps the controller from acting on
                    # an undefined value.
                    finite_edges = [e for e, _ in deltas if not math.isinf(e)]
                    return finite_edges[-1] if finite_edges else math.nan
                span_count = cum - prev_cum
                if span_count <= 0:
                    return le
                fraction = (target_rank - prev_cum) / span_count
                return prev_edge + (le - prev_edge) * fraction
            prev_edge = le if not math.isinf(le) else prev_edge
            prev_cum = cum
        # All deltas below target_rank — only possible if total<10, already
        # guarded. Fall back to the last finite edge.
        return prev_edge
