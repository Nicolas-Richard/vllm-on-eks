import asyncio
import textwrap
import threading

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def gateway_app(tmp_path, monkeypatch):
    cfg = tmp_path / "tenants.yaml"
    cfg.write_text(textwrap.dedent("""
        caps_enabled: true
        num_workers: 2
        cap_per_worker: 2
        tenants:
          - id: tenant-a
            name: "A"
            weight: 1
            queue_max: 4
            key_env: TENANT_A_KEY
          - id: tenant-b
            name: "B"
            weight: 1
            queue_max: 2
            key_env: TENANT_B_KEY
    """).strip())
    monkeypatch.setenv("TENANT_A_KEY", "key-a")
    monkeypatch.setenv("TENANT_B_KEY", "key-b")
    monkeypatch.setenv("TENANTS_PATH", str(cfg))
    monkeypatch.setenv("ACQUIRE_TIMEOUT_S", "5.0")

    import importlib

    import app.main as main_module
    importlib.reload(main_module)

    from app import proxy as proxy_module

    async def slow_upstream(_request):
        async def body():
            await asyncio.sleep(0.5)
            yield b"data: hello\n\n"
            yield b"data: [DONE]\n\n"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body(),
        )

    monkeypatch.setattr(
        proxy_module,
        "_build_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(slow_upstream), timeout=10.0),
    )

    return main_module.app


def test_single_tenant_uses_full_global_budget(gateway_app):
    """Tenant A submits 4 concurrent requests; budget is 2*2=4. All return 200."""
    with TestClient(gateway_app) as client:
        results: list[int] = []
        lock = threading.Lock()

        def fire():
            r = client.post(
                "/v1/chat/completions",
                json={"model": "x"},
                headers={"Authorization": "Bearer key-a"},
            )
            with lock:
                results.append(r.status_code)

        threads = [threading.Thread(target=fire) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert results.count(200) == 4, f"expected 4×200, got {results}"


def test_burst_isolates_attacker_from_victim(gateway_app):
    """Tenant B bursts past queue_max=2; A continues at 200. B sees ≥1 504."""
    with TestClient(gateway_app) as client:
        a_results: list[int] = []
        b_results: list[int] = []
        lock = threading.Lock()

        def fire(token: str, bucket: list[int]):
            r = client.post(
                "/v1/chat/completions",
                json={"model": "x"},
                headers={"Authorization": f"Bearer {token}"},
            )
            with lock:
                bucket.append(r.status_code)

        threads = [threading.Thread(target=fire, args=("key-a", a_results)) for _ in range(2)]
        threads += [threading.Thread(target=fire, args=("key-b", b_results)) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        assert all(s == 200 for s in a_results), f"victim A took collateral: {a_results}"
        assert 504 in b_results, f"attacker B did not shed: {b_results}"
        assert b_results.count(200) >= 1, f"some B requests should still succeed: {b_results}"
