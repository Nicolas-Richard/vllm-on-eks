import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.auth import require_tenant
from app.scheduler import (
    AIMDController,
    LimiterTimeout,
    TenantScheduler,
    WorkerCapacityWatcher,
)
from app.metrics import (
    gateway_aimd_action_total,
    gateway_worker_watcher_events_total,
    inflight_requests,
    request_duration_seconds,
    requests_received_total,
    requests_total,
    stream_completed_total,
    ttft_seconds,
)
from app.proxy import proxy_to_router
from app.tenants import Tenant, TenantRegistry, load_registry

TENANTS_PATH = os.environ.get("TENANTS_PATH", "/etc/gateway/tenants.yaml")
ACQUIRE_TIMEOUT_S = float(os.environ.get("ACQUIRE_TIMEOUT_S", "30"))

# Routes the gateway proxies. Used to pre-create labeled series so that
# rate()/increase() in Prometheus have a baseline value of 0 from startup
# rather than from the first observation. Without this, brief bursts of a
# rare status (e.g., 504 from cap timeouts) inside a longer query window
# evaluate to ~0 because the series didn't exist before the first event.
_PROXIED_ROUTES = ("/v1/chat/completions", "/v1/completions")
_REQUEST_STATUSES = ("200", "401", "500", "502", "503", "504")
# TTFT only fires on requests that reached the upstream and got a response;
# so the relevant statuses are the ones vLLM/router can return — no 401/504.
_TTFT_STATUSES = ("200", "500", "502", "503")


def _prewarm_metrics(registry: TenantRegistry) -> None:
    tenant_ids = [t.id for t in registry.all_tenants()] + ["unknown"]
    for tenant_id in tenant_ids:
        for completed in ("true", "false"):
            stream_completed_total.labels(tenant_id=tenant_id, completed=completed)
        for s in _TTFT_STATUSES:
            ttft_seconds.labels(tenant_id=tenant_id, status=s)
        for route in _PROXIED_ROUTES:
            inflight_requests.labels(tenant_id=tenant_id, route=route)
            requests_received_total.labels(tenant_id=tenant_id, route=route)
            for s in _REQUEST_STATUSES:
                requests_total.labels(tenant_id=tenant_id, route=route, status=s)
                request_duration_seconds.labels(tenant_id=tenant_id, route=route, status=s)
    if registry.aimd_config().enabled:
        for action in ("increase", "decrease", "hold"):
            gateway_aimd_action_total.labels(action=action)
    if registry.worker_watcher_config().enabled:
        for event in ("added", "modified", "deleted", "error", "reconnect"):
            gateway_worker_watcher_events_total.labels(event=event)


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry = load_registry(Path(TENANTS_PATH))
    app.state.registry = registry
    scheduler = TenantScheduler(registry, acquire_timeout_s=ACQUIRE_TIMEOUT_S)
    await scheduler.start()
    app.state.scheduler = scheduler

    # aimd/watcher pre-assigned to None so the finally block is safe even if
    # construction or start() raises before yield.
    aimd = None
    aimd_cfg = registry.aimd_config()
    if aimd_cfg.enabled:
        aimd = AIMDController(
            scheduler,
            ttft_seconds,
            target_p99_s=aimd_cfg.target_p99_ttft_s,
            tick_s=aimd_cfg.tick_s,
            window_s=aimd_cfg.window_s,
            cap_min=aimd_cfg.cap_per_worker_min,
            cap_max=aimd_cfg.cap_per_worker_max,
            target_band_pct=aimd_cfg.target_band_pct,
            cooldown_ticks=aimd_cfg.cooldown_ticks,
            decrease_step=aimd_cfg.decrease_step,
        )
        await aimd.start()
    app.state.aimd = aimd

    watcher = None
    watcher_cfg = registry.worker_watcher_config()
    if watcher_cfg.enabled:
        watcher = WorkerCapacityWatcher(
            scheduler,
            namespace=watcher_cfg.namespace,
            service_name=watcher_cfg.service_name,
        )
        await watcher.start()
    app.state.watcher = watcher

    _prewarm_metrics(registry)
    try:
        yield
    finally:
        if watcher is not None:
            await watcher.stop()
        if aimd is not None:
            await aimd.stop()
        await scheduler.stop()


app = FastAPI(lifespan=lifespan)


def _resolve_tenant_id_from_scope(scope) -> str:
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return "unknown"
    token = auth[len("Bearer "):]
    tenant = app.state.registry.resolve(token) if hasattr(app.state, "registry") else None
    return tenant.id if tenant else "unknown"


class ObservabilityMiddleware:
    # Pure-ASGI: see same justification as the prior implementation — BaseHTTPMiddleware
    # finishes too early on StreamingResponse.
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in {"/metrics", "/healthz"}:
            await self.app(scope, receive, send)
            return

        tenant_id = _resolve_tenant_id_from_scope(scope)
        route = scope["path"]
        status_box = {"code": "exc"}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_box["code"] = str(message["status"])
            await send(message)

        # Received counter is incremented before any processing — represents incoming
        # load even when requests block at the semaphore or eventually time out.
        requests_received_total.labels(tenant_id=tenant_id, route=route).inc()
        inflight_requests.labels(tenant_id=tenant_id, route=route).inc()
        # Stash the entry timestamp in the scope so proxy.py can record TTFT
        # from middleware entry (includes scheduler queue wait), not just from
        # upstream response.
        scope["app.t_ttft_start"] = time.monotonic()
        started = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - started
            request_duration_seconds.labels(
                tenant_id=tenant_id, route=route, status=status_box["code"]
            ).observe(elapsed)
            requests_total.labels(
                tenant_id=tenant_id, route=route, status=status_box["code"]
            ).inc()
            inflight_requests.labels(tenant_id=tenant_id, route=route).dec()


app.add_middleware(ObservabilityMiddleware)


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


async def _proxied(request: Request, path: str, tenant: Tenant):
    scheduler = request.app.state.scheduler
    try:
        await scheduler.acquire(tenant.id)
    except LimiterTimeout as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Tenant queue overflow or wait timeout",
        ) from e

    try:
        response = await proxy_to_router(request, path, tenant_id=tenant.id)
    except BaseException:
        scheduler.release(tenant.id)
        raise

    original_body_iterator = response.body_iterator

    async def _guarded_iterator():
        try:
            async for chunk in original_body_iterator:
                yield chunk
        finally:
            scheduler.release(tenant.id)

    response.body_iterator = _guarded_iterator()
    return response


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, tenant: Tenant = Depends(require_tenant)):
    return await _proxied(request, "/v1/chat/completions", tenant)


@app.post("/v1/completions")
async def completions(request: Request, tenant: Tenant = Depends(require_tenant)):
    return await _proxied(request, "/v1/completions", tenant)
