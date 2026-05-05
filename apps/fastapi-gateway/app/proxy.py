import os
from typing import AsyncIterator

import httpx
from fastapi import Request
from fastapi.responses import StreamingResponse

ROUTER_URL = os.environ.get("ROUTER_URL", "http://vllm-router.vllm.svc.cluster.local:80")
REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0)


async def proxy_to_router(request: Request, path: str) -> StreamingResponse:
    body = await request.body()
    target = f"{ROUTER_URL}{path}"
    forwarded_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() in {"content-type", "accept"}
    }

    client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    upstream = await client.send(
        client.build_request("POST", target, content=body, headers=forwarded_headers),
        stream=True,
    )

    async def iterator() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    response_headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    media_type = upstream.headers.get("content-type", "application/json")
    return StreamingResponse(
        iterator(),
        status_code=upstream.status_code,
        media_type=media_type,
        headers=response_headers,
    )
