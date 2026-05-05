import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.auth import require_bearer_token
from app.metrics import inflight_requests, request_duration_seconds, requests_total
from app.proxy import proxy_to_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


class ObservabilityMiddleware:
    # Pure ASGI middleware: BaseHTTPMiddleware (i.e. @app.middleware("http"))
    # returns from call_next as soon as the response object is constructed,
    # not when the body is fully streamed — which makes inflight_requests
    # and request_duration_seconds wrong for StreamingResponse.
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/metrics":
            await self.app(scope, receive, send)
            return

        status = {"code": "exc"}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = str(message["status"])
            await send(message)

        inflight_requests.inc()
        started = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_duration_seconds.labels(route=scope["path"]).observe(time.perf_counter() - started)
            requests_total.labels(route=scope["path"], status=status["code"]).inc()
            inflight_requests.dec()


app.add_middleware(ObservabilityMiddleware)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/v1/chat/completions", dependencies=[Depends(require_bearer_token)])
async def chat_completions(request: Request):
    return await proxy_to_router(request, "/v1/chat/completions")


@app.post("/v1/completions", dependencies=[Depends(require_bearer_token)])
async def completions(request: Request):
    return await proxy_to_router(request, "/v1/completions")
