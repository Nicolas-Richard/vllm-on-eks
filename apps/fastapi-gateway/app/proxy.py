import os
import time
from typing import AsyncIterator

import httpx
from fastapi import Request
from fastapi.responses import StreamingResponse

from app.metrics import stream_completed_total, ttft_seconds

ROUTER_URL = os.environ.get("ROUTER_URL", "http://vllm-router.vllm.svc.cluster.local:80")
REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0)


def _build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT)


async def proxy_to_router(request: Request, path: str, tenant_id: str) -> StreamingResponse:
    body = await request.body()
    target = f"{ROUTER_URL}{path}"
    forwarded_headers = {
        k: v for k, v in request.headers.items() if k.lower() in {"content-type", "accept"}
    }

    # Capture the request-entry timestamp set by ObservabilityMiddleware so
    # TTFT measures user-visible time from middleware entry → first chunk
    # (queue wait + dispatch + upstream TTFT). Falls back to dispatch-time
    # if the middleware was bypassed (shouldn't happen in production).
    t0 = request.scope.get("app.t_ttft_start", time.monotonic())

    client = _build_client()
    upstream = await client.send(
        client.build_request("POST", target, content=body, headers=forwarded_headers),
        stream=True,
    )

    status_for_metric = str(upstream.status_code)

    async def iterator() -> AsyncIterator[bytes]:
        first = True
        completed = False
        try:
            async for chunk in upstream.aiter_raw():
                if first:
                    ttft_seconds.labels(tenant_id=tenant_id, status=status_for_metric).observe(
                        time.monotonic() - t0
                    )
                    first = False
                yield chunk
            if first:
                # Empty upstream body: still emit one TTFT sample so the histogram has coverage.
                ttft_seconds.labels(tenant_id=tenant_id, status=status_for_metric).observe(
                    time.monotonic() - t0
                )
            completed = True
        finally:
            stream_completed_total.labels(
                tenant_id=tenant_id, completed=str(completed).lower()
            ).inc()
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        iterator(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
