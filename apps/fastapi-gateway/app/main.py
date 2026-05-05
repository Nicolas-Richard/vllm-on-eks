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


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)
    inflight_requests.inc()
    started = time.perf_counter()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        elapsed = time.perf_counter() - started
        route = request.url.path
        requests_total.labels(route=route, status=str(getattr(response, "status_code", "exc"))).inc()
        request_duration_seconds.labels(route=route).observe(elapsed)
        inflight_requests.dec()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/v1/chat/completions", dependencies=[Depends(require_bearer_token)])
async def chat_completions(request: Request):
    return await proxy_to_router(request, "/v1/chat/completions")


@app.post("/v1/completions", dependencies=[Depends(require_bearer_token)])
async def completions(request: Request):
    return await proxy_to_router(request, "/v1/completions")
