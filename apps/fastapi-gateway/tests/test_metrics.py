from prometheus_client import REGISTRY


def test_metrics_module_exposes_tenant_labeled_metrics():
    # Importing the module registers metrics in the default REGISTRY.
    from app import metrics  # noqa: F401

    families = {m.name: m for m in REGISTRY.collect()}

    counter = families["gateway_requests"]  # _total suffix is stripped on .name
    assert {"tenant_id", "route", "status"} <= set(counter.samples[0].labels.keys()) if counter.samples else True
    # Sample-set may be empty until first observation; assert via the labelnames API instead.
    from app.metrics import requests_total, request_duration_seconds, inflight_requests, ttft_seconds

    assert set(requests_total._labelnames) == {"tenant_id", "route", "status"}
    assert set(request_duration_seconds._labelnames) == {"tenant_id", "route", "status"}
    assert set(inflight_requests._labelnames) == {"tenant_id", "route"}
    assert set(ttft_seconds._labelnames) == {"tenant_id", "status"}


def test_ttft_histogram_buckets_cover_subsecond_to_30s():
    from app.metrics import ttft_seconds

    # Internal attribute set by prometheus_client when buckets= is passed.
    upper_bounds = list(ttft_seconds._upper_bounds)
    # +Inf is always appended; ignore it for the check.
    finite = [b for b in upper_bounds if b != float("inf")]
    assert finite[0] <= 0.05
    assert finite[-1] >= 30.0


def test_unknown_tenant_id_is_a_valid_label_value():
    from app.metrics import requests_total

    requests_total.labels(tenant_id="unknown", route="/v1/chat/completions", status="401").inc()
    # If labels() didn't raise, the cardinality is acceptable. Confirm sample exists.
    samples = [s for s in requests_total.collect()[0].samples if s.labels.get("tenant_id") == "unknown"]
    assert samples


import asyncio
import time

import httpx
import pytest


async def _drive_proxy(monkeypatch, chunks, status_code=200):
    """Drive proxy_to_router against a MockTransport that yields `chunks`."""
    from app import proxy as proxy_module
    from app.metrics import ttft_seconds

    async def stream(_request):
        async def body():
            for c in chunks:
                await asyncio.sleep(0.01)
                yield c
        return httpx.Response(status_code, headers={"content-type": "text/event-stream"}, content=body())

    transport = httpx.MockTransport(stream)
    monkeypatch.setattr(proxy_module, "_build_client", lambda: httpx.AsyncClient(transport=transport, timeout=10.0))

    # Build a minimal Request-like object: only `.body()`, `.headers`, and
    # `.scope` are used by proxy_to_router.
    class FakeRequest:
        headers = {"content-type": "application/json"}
        scope = {}
        async def body(self):
            return b"{}"

    response = await proxy_module.proxy_to_router(
        FakeRequest(), "/v1/chat/completions", tenant_id="tenant-a"
    )

    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    return body, ttft_seconds


def _ttft_count_for_tenant(hist, tenant_id: str) -> float:
    return sum(
        s.value for s in hist.collect()[0].samples
        if s.name == "gateway_ttft_seconds_count" and s.labels.get("tenant_id") == tenant_id
    )


async def test_ttft_recorded_once_on_first_chunk(monkeypatch):
    from app.metrics import ttft_seconds
    before = _ttft_count_for_tenant(ttft_seconds, "tenant-a")
    body, hist = await _drive_proxy(monkeypatch, [b"data: a\n\n", b"data: b\n\n", b"data: [DONE]\n\n"])
    assert b"data: a" in body
    after = _ttft_count_for_tenant(hist, "tenant-a")
    assert after - before == 1


async def test_ttft_recorded_for_single_chunk_response(monkeypatch):
    _, hist = await _drive_proxy(monkeypatch, [b'{"ok":true}'])
    samples = [s for s in hist.collect()[0].samples
               if s.name == "gateway_ttft_seconds_count"
               and s.labels.get("tenant_id") == "tenant-a"]
    assert sum(s.value for s in samples) >= 1


def _stream_completed(metric, tenant_id: str, completed: str) -> float:
    return sum(
        s.value for s in metric.collect()[0].samples
        if s.name == "gateway_stream_completed_total"
        and s.labels.get("tenant_id") == tenant_id
        and s.labels.get("completed") == completed
    )


async def test_stream_completed_true_on_clean_finish(monkeypatch):
    from app.metrics import stream_completed_total
    before_t = _stream_completed(stream_completed_total, "tenant-a", "true")
    before_f = _stream_completed(stream_completed_total, "tenant-a", "false")
    await _drive_proxy(monkeypatch, [b"data: a\n\n", b"data: [DONE]\n\n"])
    assert _stream_completed(stream_completed_total, "tenant-a", "true") - before_t == 1
    assert _stream_completed(stream_completed_total, "tenant-a", "false") - before_f == 0


async def test_stream_completed_false_on_iterator_exception(monkeypatch):
    """If the upstream stream raises mid-iteration, completed=false is recorded."""
    from app import proxy as proxy_module
    from app.metrics import stream_completed_total

    before_f = _stream_completed(stream_completed_total, "tenant-a", "false")

    async def stream(_request):
        async def body():
            yield b"data: partial\n\n"
            raise RuntimeError("upstream died mid-stream")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    transport = httpx.MockTransport(stream)
    monkeypatch.setattr(proxy_module, "_build_client",
                        lambda: httpx.AsyncClient(transport=transport, timeout=10.0))

    class FakeRequest:
        headers = {"content-type": "application/json"}
        scope = {}
        async def body(self):
            return b"{}"

    response = await proxy_module.proxy_to_router(FakeRequest(), "/v1/chat/completions", tenant_id="tenant-a")
    with pytest.raises(Exception):
        async for _ in response.body_iterator:
            pass

    assert _stream_completed(stream_completed_total, "tenant-a", "false") - before_f == 1


def test_prewarm_creates_all_expected_series():
    """Verify that _prewarm_metrics creates the (tenant_id, route, status) etc. series at value 0.

    Without prewarm, brand-new series only exist after their first .inc() — which means a
    Prometheus rate() over a query window that starts before the first event returns nothing
    instead of a real rate. With prewarm, the series exist at 0 from startup so rate() works.
    """
    from app import main as main_module
    from app.metrics import (
        inflight_requests,
        request_duration_seconds,
        requests_received_total,
        requests_total,
        stream_completed_total,
        ttft_seconds,
    )
    from app.tenants import GlobalBudgetConfig, Tenant, TenantRegistry

    registry = TenantRegistry(
        tenants=[Tenant(id="tenant-x", name="X", weight=1, queue_max=4)],
        token_to_id={"k": "tenant-x"},
        caps_enabled=False,
        global_budget=GlobalBudgetConfig(num_workers=1, cap_per_worker=4),
    )
    main_module._prewarm_metrics(registry)

    def has_series(metric, labels):
        return any(
            all(s.labels.get(k) == v for k, v in labels.items())
            for fam in metric.collect()
            for s in fam.samples
        )

    # Expected labels exist for tenant-x and unknown.
    for tid in ("tenant-x", "unknown"):
        assert has_series(stream_completed_total, {"tenant_id": tid, "completed": "true"})
        assert has_series(stream_completed_total, {"tenant_id": tid, "completed": "false"})
        for s in ("200", "500", "502", "503"):
            assert has_series(ttft_seconds, {"tenant_id": tid, "status": s})
        for route in ("/v1/chat/completions", "/v1/completions"):
            assert has_series(inflight_requests, {"tenant_id": tid, "route": route})
            assert has_series(requests_received_total, {"tenant_id": tid, "route": route})
            for s in ("200", "401", "500", "502", "503", "504"):
                assert has_series(requests_total, {"tenant_id": tid, "route": route, "status": s})
                assert has_series(request_duration_seconds, {"tenant_id": tid, "route": route, "status": s})
