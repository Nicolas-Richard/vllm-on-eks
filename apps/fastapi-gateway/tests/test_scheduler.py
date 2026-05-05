import asyncio

import pytest

from app.scheduler import LimiterTimeout, TenantScheduler
from app.tenants import GlobalBudgetConfig, Tenant, TenantRegistry


def _registry(
    *,
    caps_enabled: bool,
    num_workers: int = 1,
    cap_per_worker: int = 4,
    tenants: list[Tenant] | None = None,
) -> TenantRegistry:
    if tenants is None:
        tenants = [Tenant(id="tenant-a", name="A", weight=1, queue_max=4)]
    token_to_id = {f"key-{t.id}": t.id for t in tenants}
    return TenantRegistry(
        tenants=tenants,
        token_to_id=token_to_id,
        caps_enabled=caps_enabled,
        global_budget=GlobalBudgetConfig(
            num_workers=num_workers, cap_per_worker=cap_per_worker
        ),
    )


async def test_caps_disabled_admits_many_concurrent():
    sched = TenantScheduler(_registry(caps_enabled=False), acquire_timeout_s=1.0)
    await sched.start()
    try:
        counter = {"in": 0, "max": 0}
        release = asyncio.Event()

        async def hold():
            async with sched.slot("tenant-a"):
                counter["in"] += 1
                counter["max"] = max(counter["max"], counter["in"])
                await release.wait()
                counter["in"] -= 1

        tasks = [asyncio.create_task(hold()) for _ in range(100)]
        await asyncio.sleep(0.1)
        assert counter["max"] >= 100  # all 100 in flight at once

        release.set()
        await asyncio.gather(*tasks)
    finally:
        await sched.stop()


async def test_unknown_tenant_raises_key_error():
    sched = TenantScheduler(_registry(caps_enabled=True), acquire_timeout_s=1.0)
    await sched.start()
    try:
        with pytest.raises(KeyError):
            async with sched.slot("tenant-z"):
                pass
    finally:
        await sched.stop()


async def test_stop_cancels_drain_task_cleanly():
    sched = TenantScheduler(_registry(caps_enabled=True), acquire_timeout_s=1.0)
    await sched.start()
    await sched.stop()
    # second stop is a no-op
    await sched.stop()


async def test_single_tenant_uses_full_global_budget():
    # 1 worker × 4 cap_per_worker = 4 slots; one tenant should hold all 4 at once.
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=4),
        acquire_timeout_s=2.0,
    )
    await sched.start()
    try:
        counter = {"in": 0, "max": 0}
        release = asyncio.Event()

        async def hold():
            async with sched.slot("tenant-a"):
                counter["in"] += 1
                counter["max"] = max(counter["max"], counter["in"])
                await release.wait()
                counter["in"] -= 1

        tasks = [asyncio.create_task(hold()) for _ in range(4)]
        await asyncio.sleep(0.1)
        assert counter["max"] == 4  # full budget used by single tenant

        release.set()
        await asyncio.gather(*tasks)
    finally:
        await sched.stop()


async def test_global_budget_caps_total_inflight():
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=2),
        acquire_timeout_s=2.0,
    )
    await sched.start()
    try:
        in_slot = {"count": 0, "max": 0}
        release = asyncio.Event()

        async def hold():
            async with sched.slot("tenant-a"):
                in_slot["count"] += 1
                in_slot["max"] = max(in_slot["max"], in_slot["count"])
                await release.wait()
                in_slot["count"] -= 1

        # Submit 4; only 2 should be in flight at once.
        tasks = [asyncio.create_task(hold()) for _ in range(4)]
        await asyncio.sleep(0.1)
        assert in_slot["max"] == 2

        release.set()
        await asyncio.gather(*tasks)
    finally:
        await sched.stop()


async def test_equal_weight_contention_is_fair():
    a = Tenant(id="tenant-a", name="A", weight=1, queue_max=64)
    b = Tenant(id="tenant-b", name="B", weight=1, queue_max=64)
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=1, tenants=[a, b]),
        acquire_timeout_s=5.0,
    )
    await sched.start()
    try:
        order: list[str] = []
        gate = asyncio.Event()

        async def submit(tid: str):
            async with sched.slot(tid):
                order.append(tid)
                await gate.wait()

        # Pre-fill both queues with 10 each *before* the drain starts dispatching.
        # Use a held slot to force the drain to wait, so all 20 are queued.
        async with sched.slot("tenant-a"):
            tasks = []
            for _ in range(10):
                tasks.append(asyncio.create_task(submit("tenant-a")))
                tasks.append(asyncio.create_task(submit("tenant-b")))
            await asyncio.sleep(0.05)
        # Slot released; drain dispatches one-at-a-time (cap_per_worker=1).
        # Let them all serialize through the dispatch path:
        gate.set()
        await asyncio.gather(*tasks)

        # Equal weights → ±1 of 50/50 split across the 20 dispatches.
        a_count = sum(1 for x in order if x == "tenant-a")
        b_count = sum(1 for x in order if x == "tenant-b")
        assert abs(a_count - b_count) <= 2
        assert a_count + b_count == 20
    finally:
        await sched.stop()


async def test_unequal_weights_are_proportional():
    a = Tenant(id="tenant-a", name="A", weight=1, queue_max=128)
    b = Tenant(id="tenant-b", name="B", weight=3, queue_max=128)
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=1, tenants=[a, b]),
        acquire_timeout_s=10.0,
    )
    await sched.start()
    try:
        order: list[str] = []
        gate = asyncio.Event()

        async def submit(tid: str):
            async with sched.slot(tid):
                order.append(tid)
                await gate.wait()

        async with sched.slot("tenant-a"):
            tasks = []
            # Match queue lengths to the weight ratio so both queues drain
            # together — otherwise work conservation gives the rest to whoever
            # runs out first, and the dispatch ratio collapses.
            for _ in range(20):
                tasks.append(asyncio.create_task(submit("tenant-a")))
            for _ in range(60):
                tasks.append(asyncio.create_task(submit("tenant-b")))
            await asyncio.sleep(0.05)
        gate.set()
        await asyncio.gather(*tasks)

        a_count = sum(1 for x in order if x == "tenant-a")
        b_count = sum(1 for x in order if x == "tenant-b")
        # weights 1:3 → 80 dispatches → A≈20, B≈60. Allow ±5.
        assert 15 <= a_count <= 25
        assert 55 <= b_count <= 65


    finally:
        await sched.stop()


async def test_idle_tenant_slots_reallocated_to_busy_tenant():
    a = Tenant(id="tenant-a", name="A", weight=1, queue_max=64)
    b = Tenant(id="tenant-b", name="B", weight=1, queue_max=64)
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=4, tenants=[a, b]),
        acquire_timeout_s=2.0,
    )
    await sched.start()
    try:
        # Only A submits; budget=4 should all go to A.
        in_slot = {"count": 0, "max": 0}
        release = asyncio.Event()

        async def hold():
            async with sched.slot("tenant-a"):
                in_slot["count"] += 1
                in_slot["max"] = max(in_slot["max"], in_slot["count"])
                await release.wait()
                in_slot["count"] -= 1

        tasks = [asyncio.create_task(hold()) for _ in range(4)]
        await asyncio.sleep(0.1)
        assert in_slot["max"] == 4

        release.set()
        await asyncio.gather(*tasks)
    finally:
        await sched.stop()


async def test_release_admits_waiter():
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=1),
        acquire_timeout_s=5.0,
    )
    await sched.start()
    try:
        holder_in = asyncio.Event()
        holder_release = asyncio.Event()
        waiter_in = asyncio.Event()

        async def holder():
            async with sched.slot("tenant-a"):
                holder_in.set()
                await holder_release.wait()

        async def waiter():
            async with sched.slot("tenant-a"):
                waiter_in.set()

        h = asyncio.create_task(holder())
        await holder_in.wait()
        w = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
        assert not waiter_in.is_set()

        holder_release.set()
        await asyncio.gather(h, w)
        assert waiter_in.is_set()
    finally:
        await sched.stop()


async def test_caller_cancellation_after_dispatch_releases_slot():
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=1),
        acquire_timeout_s=5.0,
    )
    await sched.start()
    try:
        # Use slot() so a successful acquire is followed by a release in the
        # cancellation path. Cancelling the task while it's between the
        # `await acquire()` and the body should still release the slot.
        cancelled_task = asyncio.create_task(_acquire_and_park(sched, "tenant-a"))
        await asyncio.sleep(0.05)  # let drain dispatch the slot
        cancelled_task.cancel()
        try:
            await cancelled_task
        except asyncio.CancelledError:
            pass

        # Slot should be free again — a fresh acquire must complete promptly.
        await asyncio.wait_for(sched.acquire("tenant-a"), timeout=1.0)
        sched.release("tenant-a")
        assert sched.global_inflight() == 0
    finally:
        await sched.stop()


async def _acquire_and_park(sched, tid):
    await sched.acquire(tid)
    try:
        await asyncio.sleep(60)  # park; the test will cancel this task
    finally:
        sched.release(tid)


async def test_stop_resolves_pending_acquirers():
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=1),
        acquire_timeout_s=30.0,
    )
    await sched.start()

    # Hold the only slot — do not release before stop().
    await sched.acquire("tenant-a")

    async def waiter():
        with pytest.raises(LimiterTimeout):
            await sched.acquire("tenant-a")

    w = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)  # let waiter enqueue
    assert sched.queue_depth("tenant-a") == 1

    # Stop must wake the queued waiter with LimiterTimeout immediately, not
    # leave it hanging until acquire_timeout_s expires.
    await asyncio.wait_for(sched.stop(), timeout=2.0)
    await asyncio.wait_for(w, timeout=2.0)
    sched.release("tenant-a")


async def test_queue_overflow_raises_limiter_timeout():
    a = Tenant(id="tenant-a", name="A", weight=1, queue_max=2)
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=1, tenants=[a]),
        acquire_timeout_s=5.0,
    )
    await sched.start()
    try:
        held_in = asyncio.Event()
        held_release = asyncio.Event()

        async def hold():
            async with sched.slot("tenant-a"):
                held_in.set()
                await held_release.wait()

        h = asyncio.create_task(hold())
        await held_in.wait()

        # Fill the queue to queue_max=2.
        waiters = []
        for _ in range(2):
            waiters.append(asyncio.create_task(sched.acquire("tenant-a")))
        await asyncio.sleep(0.05)
        assert sched.queue_depth("tenant-a") == 2

        # Next acquire must overflow.
        with pytest.raises(LimiterTimeout):
            await sched.acquire("tenant-a")

        held_release.set()
        await h
        for w in waiters:
            try:
                await asyncio.wait_for(w, timeout=2.0)
            except (LimiterTimeout, asyncio.TimeoutError):
                pass
    finally:
        await sched.stop()


async def test_queue_wait_timeout_raises_limiter_timeout():
    a = Tenant(id="tenant-a", name="A", weight=1, queue_max=8)
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=1, tenants=[a]),
        acquire_timeout_s=0.1,
    )
    await sched.start()
    try:
        held_in = asyncio.Event()
        held_release = asyncio.Event()

        async def hold():
            async with sched.slot("tenant-a"):
                held_in.set()
                await held_release.wait()

        h = asyncio.create_task(hold())
        await held_in.wait()

        with pytest.raises(LimiterTimeout):
            await sched.acquire("tenant-a")

        held_release.set()
        await h
    finally:
        await sched.stop()


async def test_cancelled_queued_entry_does_not_eat_capacity():
    """Regression: a cancelled-but-not-yet-popped request used to occupy a
    queue slot until the drain lazily reaped it, causing spurious 504s under
    cancellation pressure."""
    a = Tenant(id="tenant-a", name="A", weight=1, queue_max=2)
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=1, tenants=[a]),
        acquire_timeout_s=0.1,
    )
    await sched.start()
    try:
        held_in = asyncio.Event()
        held_release = asyncio.Event()

        async def hold():
            async with sched.slot("tenant-a"):
                held_in.set()
                await held_release.wait()

        h = asyncio.create_task(hold())
        await held_in.wait()

        # Two queue-wait timeouts fill A's logical queue (cap=2) with corpses
        # that the drain has not yet popped (it is blocked on the held slot).
        for _ in range(2):
            with pytest.raises(LimiterTimeout):
                await sched.acquire("tenant-a")

        # With the bug, this third acquire would 504 immediately because the
        # queue was full of dead entries. With the fix, live_count is 0, so it
        # enqueues — and once the slot frees, dispatches normally.
        async def waiter():
            async with sched.slot("tenant-a"):
                pass

        w = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
        assert sched.queue_depth("tenant-a") == 1  # one live request

        held_release.set()
        await asyncio.gather(h, w)
    finally:
        await sched.stop()


async def test_queue_depth_reflects_live_count_not_qsize():
    """Regression: gateway_queue_depth used to count cancelled corpses, which
    could overstate real pending demand and mask DRR fairness."""
    a = Tenant(id="tenant-a", name="A", weight=1, queue_max=4)
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=1, tenants=[a]),
        acquire_timeout_s=0.1,
    )
    await sched.start()
    try:
        held_in = asyncio.Event()
        held_release = asyncio.Event()

        async def hold():
            async with sched.slot("tenant-a"):
                held_in.set()
                await held_release.wait()

        h = asyncio.create_task(hold())
        await held_in.wait()

        # Generate three corpses by timing out three acquires.
        for _ in range(3):
            with pytest.raises(LimiterTimeout):
                await sched.acquire("tenant-a")

        # The reported queue depth should be 0 even though the underlying
        # asyncio.Queue still holds three not-yet-popped corpses.
        assert sched.queue_depth("tenant-a") == 0

        held_release.set()
        await h
    finally:
        await sched.stop()


# --- runtime budget resize ------------------------------------------------


async def test_set_cap_per_worker_grows_semaphore():
    # Start at budget 2 (1 worker × 2 cap); grow cap_per_worker to 4 → budget 4.
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=2,
                  tenants=[Tenant(id="tenant-a", name="A", weight=1, queue_max=64)]),
        acquire_timeout_s=2.0,
    )
    await sched.start()
    try:
        in_slot = {"count": 0, "max": 0}
        release = asyncio.Event()

        async def hold():
            async with sched.slot("tenant-a"):
                in_slot["count"] += 1
                in_slot["max"] = max(in_slot["max"], in_slot["count"])
                await release.wait()
                in_slot["count"] -= 1

        # Spawn 4; only 2 should be in flight before the resize.
        tasks = [asyncio.create_task(hold()) for _ in range(4)]
        await asyncio.sleep(0.05)
        assert in_slot["max"] == 2

        # Grow the budget; the two waiting acquires should now dispatch.
        await sched.set_cap_per_worker(4)
        await asyncio.sleep(0.05)
        assert in_slot["max"] == 4
        assert sched.cap_per_worker == 4
        assert sched.global_budget() == 4

        release.set()
        await asyncio.gather(*tasks)
    finally:
        await sched.stop()


async def test_set_num_workers_resizes_atomically():
    # cap=4; bump num_workers from 1 → 2 → budget 8.
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=4,
                  tenants=[Tenant(id="tenant-a", name="A", weight=1, queue_max=64)]),
        acquire_timeout_s=2.0,
    )
    await sched.start()
    try:
        assert sched.global_budget() == 4
        assert sched.num_workers == 1
        assert sched.cap_per_worker == 4

        await sched.set_num_workers(2)

        assert sched.num_workers == 2
        assert sched.cap_per_worker == 4  # unchanged
        assert sched.global_budget() == 8

        # Empirical check: 8 concurrent acquires fit at once.
        in_slot = {"count": 0, "max": 0}
        release = asyncio.Event()

        async def hold():
            async with sched.slot("tenant-a"):
                in_slot["count"] += 1
                in_slot["max"] = max(in_slot["max"], in_slot["count"])
                await release.wait()
                in_slot["count"] -= 1

        tasks = [asyncio.create_task(hold()) for _ in range(8)]
        await asyncio.sleep(0.1)
        assert in_slot["max"] == 8

        release.set()
        await asyncio.gather(*tasks)
    finally:
        await sched.stop()


async def test_resize_during_inflight_does_not_lose_slots():
    # Start budget=8 and hold all 8 slots in flight. Shrink to budget=2
    # (delta=-6). The next 6 release()s should be absorbed by pending_shrink;
    # the final 2 release()s should hand slots back to the semaphore.
    #
    # Holding the full budget before the shrink means we can pin the
    # convergence-via-releases behavior precisely: once all 8 release, the
    # semaphore has exactly 2 available slots — matching the new target.
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=8,
                  tenants=[Tenant(id="tenant-a", name="A", weight=1, queue_max=64)]),
        acquire_timeout_s=5.0,
    )
    await sched.start()
    try:
        held_release = asyncio.Event()
        held_in = [asyncio.Event() for _ in range(8)]

        async def hold(idx):
            async with sched.slot("tenant-a"):
                held_in[idx].set()
                await held_release.wait()

        held_tasks = [asyncio.create_task(hold(i)) for i in range(8)]
        for ev in held_in:
            await ev.wait()
        assert sched.global_inflight() == 8

        # Shrink budget by 6 (8 → 2). Sem has 0 available; pending_shrink=6.
        await sched.set_cap_per_worker(2)
        assert sched.global_budget() == 2
        assert sched.pending_shrink == 6

        # Release all 8: first 6 are absorbed (no .release() on the sem),
        # last 2 hand slots back to the sem.
        held_release.set()
        await asyncio.gather(*held_tasks)
        assert sched.global_inflight() == 0
        assert sched.pending_shrink == 0

        # Now the sem should hold exactly 2 slots — the new target. Try 3
        # concurrent acquires; only 2 should run at a time.
        in_slot = {"count": 0, "max": 0}
        release2 = asyncio.Event()

        async def hold2():
            async with sched.slot("tenant-a"):
                in_slot["count"] += 1
                in_slot["max"] = max(in_slot["max"], in_slot["count"])
                await release2.wait()
                in_slot["count"] -= 1

        tasks2 = [asyncio.create_task(hold2()) for _ in range(3)]
        await asyncio.sleep(0.05)
        assert in_slot["max"] == 2  # budget converged

        release2.set()
        await asyncio.gather(*tasks2)
    finally:
        await sched.stop()


async def test_resize_to_zero_blocks_new_dispatches():
    # Hold the full budget in flight, then resize to 0. Once the in-flight
    # slots release (all absorbed), the semaphore has 0 available slots and
    # new acquires should LimiterTimeout.
    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=1, cap_per_worker=2,
                  tenants=[Tenant(id="tenant-a", name="A", weight=1, queue_max=64)]),
        acquire_timeout_s=0.1,
    )
    await sched.start()
    try:
        held_release = asyncio.Event()
        held_in = [asyncio.Event() for _ in range(2)]

        async def hold(idx):
            async with sched.slot("tenant-a"):
                held_in[idx].set()
                await held_release.wait()

        held_tasks = [asyncio.create_task(hold(i)) for i in range(2)]
        for ev in held_in:
            await ev.wait()

        await sched.set_cap_per_worker(0)
        assert sched.global_budget() == 0
        assert sched.pending_shrink == 2

        held_release.set()
        await asyncio.gather(*held_tasks)
        assert sched.pending_shrink == 0

        # Sem is now drained to 0; new acquires queue and time out.
        with pytest.raises(LimiterTimeout):
            await sched.acquire("tenant-a")
    finally:
        await sched.stop()


async def test_set_cap_per_worker_updates_gauges():
    from app.metrics import (
        gateway_cap_per_worker,
        gateway_global_budget,
        gateway_num_workers,
    )

    sched = TenantScheduler(
        _registry(caps_enabled=True, num_workers=2, cap_per_worker=4,
                  tenants=[Tenant(id="tenant-a", name="A", weight=1, queue_max=64)]),
        acquire_timeout_s=1.0,
    )
    await sched.start()
    try:
        # Initial values reflect the bootstrap.
        assert gateway_global_budget._value.get() == 8
        assert gateway_cap_per_worker._value.get() == 4
        assert gateway_num_workers._value.get() == 2

        await sched.set_cap_per_worker(6)
        assert gateway_cap_per_worker._value.get() == 6
        assert gateway_global_budget._value.get() == 12

        await sched.set_num_workers(3)
        assert gateway_num_workers._value.get() == 3
        assert gateway_global_budget._value.get() == 18
    finally:
        await sched.stop()


async def test_set_cap_per_worker_rejects_negative():
    sched = TenantScheduler(_registry(caps_enabled=True), acquire_timeout_s=1.0)
    with pytest.raises(ValueError):
        await sched.set_cap_per_worker(-1)


async def test_set_num_workers_rejects_negative():
    sched = TenantScheduler(_registry(caps_enabled=True), acquire_timeout_s=1.0)
    with pytest.raises(ValueError):
        await sched.set_num_workers(-3)
