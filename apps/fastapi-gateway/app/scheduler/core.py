from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from app.metrics import (
    gateway_cap_per_worker,
    gateway_global_budget,
    gateway_global_inflight,
    gateway_num_workers,
    gateway_queue_depth,
)
from app.tenants import TenantRegistry

logger = logging.getLogger(__name__)


class LimiterTimeout(Exception):
    """Raised when a tenant slot cannot be acquired within the configured timeout."""


@dataclass
class _QueuedRequest:
    tenant_id: str
    future: asyncio.Future
    cancelled: bool = False


@dataclass
class _TenantState:
    queue: asyncio.Queue
    weight: int
    quantum: int
    cap: int                # logical queue capacity (was queue_max)
    live_count: int = 0     # live (not-cancelled, not-dispatched) entries in queue
    deficit: int = 0


class TenantScheduler:
    def __init__(self, registry: TenantRegistry, acquire_timeout_s: float = 30.0):
        self._timeout = acquire_timeout_s
        self._caps_on = registry.caps_enabled()
        self._tenants = {t.id: t for t in registry.all_tenants()}
        cfg = registry.global_budget_config()
        self._num_workers = cfg.num_workers
        self._cap_per_worker = cfg.cap_per_worker
        self._budget = self._num_workers * self._cap_per_worker

        self._global_sem: asyncio.Semaphore | None = None
        self._states: dict[str, _TenantState] = {}
        self._wakeup: asyncio.Event | None = None
        self._drain_task: asyncio.Task | None = None
        self._inflight: int = 0
        self._pending_shrink: int = 0
        self._resize_lock: asyncio.Lock = asyncio.Lock()

    async def start(self) -> None:
        if not self._caps_on:
            return
        self._global_sem = asyncio.Semaphore(self._budget)
        self._wakeup = asyncio.Event()
        self._states = {
            tid: _TenantState(
                # Queue is unbounded; logical capacity is enforced via
                # _TenantState.cap + live_count so that cancelled-but-not-yet-
                # popped entries do not eat real queue slots.
                queue=asyncio.Queue(),
                weight=t.weight,
                quantum=t.weight,  # direct mapping; spec open-question 1
                cap=t.queue_max,
            )
            for tid, t in self._tenants.items()
        }
        gateway_global_budget.set(self._budget)
        gateway_cap_per_worker.set(self._cap_per_worker)
        gateway_num_workers.set(self._num_workers)
        gateway_global_inflight.set(0)
        for tid in self._tenants:
            gateway_queue_depth.labels(tenant_id=tid).set(0)
        self._drain_task = asyncio.create_task(self._drain_loop())

    async def stop(self) -> None:
        if self._drain_task is not None:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            self._drain_task = None
        for state in self._states.values():
            while not state.queue.empty():
                try:
                    req = state.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if req.cancelled or req.future.done():
                    continue
                req.future.set_exception(LimiterTimeout(req.tenant_id))
                state.live_count -= 1

    async def acquire(self, tenant_id: str) -> None:
        if tenant_id not in self._tenants:
            raise KeyError(tenant_id)
        if not self._caps_on:
            return

        loop = asyncio.get_running_loop()
        req = _QueuedRequest(tenant_id=tenant_id, future=loop.create_future())
        state = self._states[tenant_id]
        if state.live_count >= state.cap:
            raise LimiterTimeout(tenant_id)
        state.queue.put_nowait(req)
        state.live_count += 1
        gateway_queue_depth.labels(tenant_id=tenant_id).set(state.live_count)
        assert self._wakeup is not None
        self._wakeup.set()

        try:
            await asyncio.wait_for(req.future, timeout=self._timeout)
        except asyncio.TimeoutError as e:
            # Still in queue waiting; mark dead and release the logical slot.
            # The corpse is reaped lazily by the drain via _next_live_request.
            req.cancelled = True
            state.live_count -= 1
            gateway_queue_depth.labels(tenant_id=tenant_id).set(state.live_count)
            raise LimiterTimeout(tenant_id) from e
        except asyncio.CancelledError:
            req.cancelled = True
            if req.future.done() and not req.future.cancelled():
                # Drain already popped + dispatched (live_count was decremented
                # at pop). Slot is held with no caller — return it.
                self.release(tenant_id)
            else:
                # Still pending in queue; release the logical slot.
                state.live_count -= 1
                gateway_queue_depth.labels(tenant_id=tenant_id).set(state.live_count)
            raise

    def release(self, tenant_id: str) -> None:
        if tenant_id not in self._tenants:
            raise KeyError(tenant_id)
        if not self._caps_on:
            return
        assert self._global_sem is not None
        if self._pending_shrink > 0:
            # Budget contracted while this slot was in flight; absorb the
            # release rather than handing the slot back to the semaphore.
            # Safe under single-threaded asyncio: no await between resize
            # and this check, so _pending_shrink can't race.
            self._pending_shrink -= 1
        else:
            self._global_sem.release()
        self._inflight -= 1
        gateway_global_inflight.set(self._inflight)

    @asynccontextmanager
    async def slot(self, tenant_id: str) -> AsyncIterator[None]:
        await self.acquire(tenant_id)
        try:
            yield
        finally:
            self.release(tenant_id)

    # --- introspection ---------------------------------------------------

    def queue_depth(self, tenant_id: str) -> int:
        if not self._caps_on:
            return 0
        return self._states[tenant_id].live_count

    def global_budget(self) -> int:
        return self._budget

    def global_inflight(self) -> int:
        return self._inflight

    @property
    def cap_per_worker(self) -> int:
        return self._cap_per_worker

    @property
    def num_workers(self) -> int:
        return self._num_workers

    @property
    def pending_shrink(self) -> int:
        """Live count of semaphore slot absorptions; decrements toward zero as in-flight slots drain after a shrink."""
        return self._pending_shrink

    def any_queue_nonempty(self) -> bool:
        """True iff any tenant's per-tenant queue has live (non-cancelled) entries; used by AIMDController to gate additive-increase."""
        return any(s.live_count > 0 for s in self._states.values())

    def has_demand(self) -> bool:
        """True iff there's any pending OR in-flight work — queued requests
        awaiting dispatch, or requests currently holding a semaphore slot.

        AIMDController gates additive-increase on this rather than queue depth
        alone, so a healthy steady-state stream (each request finishing before
        the next arrives → queue empty but in-flight > 0) still counts as
        demand and lets cap climb.
        """
        return self._inflight > 0 or self.any_queue_nonempty()

    # --- runtime resize --------------------------------------------------

    async def set_cap_per_worker(self, value: int) -> None:
        if value < 0:
            raise ValueError(f"cap_per_worker must be non-negative, got {value}")
        async with self._resize_lock:
            self._cap_per_worker = value
            self._resize_budget_locked()

    async def set_num_workers(self, value: int) -> None:
        if value < 0:
            raise ValueError(f"num_workers must be non-negative, got {value}")
        async with self._resize_lock:
            self._num_workers = value
            self._resize_budget_locked()

    def _resize_budget_locked(self) -> None:
        """Atomically re-target the global budget from the cached
        _num_workers / _cap_per_worker fields.

        Caller must hold _resize_lock. Growth releases extra slots into the
        semaphore; contraction is deferred via _pending_shrink so in-flight
        requests stay intact and the available count converges to the new
        target as they release.
        """
        target = self._num_workers * self._cap_per_worker
        delta = target - self._budget
        self._budget = target
        gateway_global_budget.set(target)
        gateway_cap_per_worker.set(self._cap_per_worker)
        gateway_num_workers.set(self._num_workers)

        if not self._caps_on or self._global_sem is None:
            # Caps off → semaphore is unused. Scheduler not started → no
            # semaphore yet; start() will size it from self._budget.
            return

        if delta > 0:
            for _ in range(delta):
                self._global_sem.release()
        elif delta < 0:
            self._pending_shrink += -delta

    # --- DRR drain loop --------------------------------------------------

    async def _drain_loop(self) -> None:
        """Deficit round-robin across tenant queues. Dispatches one request per
        iteration, blocking on the global semaphore when the budget is full."""
        assert self._wakeup is not None
        assert self._global_sem is not None
        order = list(self._states.keys())
        idx = 0
        while True:
            if not any(s.live_count > 0 for s in self._states.values()):
                self._wakeup.clear()
                await self._wakeup.wait()
                continue

            tid = order[idx]
            state = self._states[tid]
            idx = (idx + 1) % len(order)

            if state.live_count == 0:
                state.deficit = 0
                continue

            state.deficit += state.quantum
            while state.deficit > 0 and state.live_count > 0:
                # Acquire the global slot BEFORE popping, so the queue stays
                # the canonical record of pending requests. This lets stop()
                # iterate queues to wake every blocked caller.
                await self._global_sem.acquire()
                req = self._next_live_request(state)
                if req is None:
                    # All remaining entries were cancelled while we waited.
                    self._global_sem.release()
                    state.deficit = 0
                    break
                state.live_count -= 1
                self._inflight += 1
                gateway_global_inflight.set(self._inflight)
                gateway_queue_depth.labels(tenant_id=tid).set(state.live_count)
                # _next_live_request filtered out done/cancelled entries and
                # there's no await since, so req.future is guaranteed undone.
                req.future.set_result(None)
                state.deficit -= 1

    def _next_live_request(self, state: _TenantState) -> _QueuedRequest | None:
        while not state.queue.empty():
            req = state.queue.get_nowait()
            if not req.cancelled and not req.future.done():
                return req
            # Drop cancelled/timed-out queue entry.
        return None
