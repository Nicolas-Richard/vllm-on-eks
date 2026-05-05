import asyncio
import textwrap
import time

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def gateway_app(tmp_path, monkeypatch):
    cfg = tmp_path / "tenants.yaml"
    cfg.write_text(textwrap.dedent("""
        caps_enabled: true
        num_workers: 1
        cap_per_worker: 1
        tenants:
          - id: tenant-a
            name: "A"
            weight: 1
            queue_max: 1
            key_env: TENANT_A_KEY
    """).strip())
    monkeypatch.setenv("TENANT_A_KEY", "key-a")
    monkeypatch.setenv("TENANTS_PATH", str(cfg))
    monkeypatch.setenv("ACQUIRE_TIMEOUT_S", "0.2")

    # Force a fresh module load so lifespan picks up the new env.
    import importlib

    import app.main as main_module
    importlib.reload(main_module)

    # Inject a mock upstream that streams two chunks slowly.
    from app import proxy as proxy_module

    async def upstream(_request):
        async def body():
            await asyncio.sleep(0.05)
            yield b"data: hello\n\n"
            await asyncio.sleep(0.05)
            yield b"data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    monkeypatch.setattr(
        proxy_module,
        "_build_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream), timeout=10.0),
    )

    return main_module.app


def test_missing_auth_returns_401(gateway_app):
    with TestClient(gateway_app) as client:
        r = client.post("/v1/chat/completions", json={})
        assert r.status_code == 401


def test_known_token_proxies_stream(gateway_app):
    with TestClient(gateway_app) as client:
        r = client.post(
            "/v1/chat/completions",
            json={"model": "x"},
            headers={"Authorization": "Bearer key-a"},
        )
        assert r.status_code == 200
        assert b"data: hello" in r.content


def test_acquire_timeout_returns_504(gateway_app):
    # cap=1, acquire_timeout=0.2s. Hold the only slot with a slow request,
    # then a second request should 504.
    with TestClient(gateway_app) as client:
        import threading

        results: list[int] = []

        def fire():
            r = client.post(
                "/v1/chat/completions",
                json={"model": "x"},
                headers={"Authorization": "Bearer key-a"},
            )
            results.append(r.status_code)

        from app import proxy as proxy_module

        # Patch the upstream to be very slow for both requests.
        async def slow_upstream(_request):
            async def body():
                await asyncio.sleep(2.0)
                yield b"data: hi\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

        proxy_module._build_client = (
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(slow_upstream), timeout=10.0)
        )

        threads = [threading.Thread(target=fire) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert 504 in results
