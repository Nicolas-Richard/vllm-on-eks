import textwrap

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import require_tenant
from app.tenants import Tenant, load_registry


@pytest.fixture
def client(tmp_path, monkeypatch):
    p = tmp_path / "tenants.yaml"
    p.write_text(textwrap.dedent("""
        caps_enabled: false
        num_workers: 1
        cap_per_worker: 4
        tenants:
          - id: tenant-a
            name: "A"
            weight: 1
            queue_max: 4
            key_env: TENANT_A_KEY
          - id: tenant-b
            name: "B"
            weight: 1
            queue_max: 4
            key_env: TENANT_B_KEY
    """).strip())
    monkeypatch.setenv("TENANT_A_KEY", "key-a")
    monkeypatch.setenv("TENANT_B_KEY", "key-b")

    app = FastAPI()
    app.state.registry = load_registry(p)

    @app.get("/protected")
    def protected(t: Tenant = Depends(require_tenant)):
        return {"tenant_id": t.id}

    return TestClient(app)


def test_missing_authorization_returns_401(client):
    assert client.get("/protected").status_code == 401


def test_wrong_token_returns_401(client):
    r = client.get("/protected", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_missing_bearer_prefix_returns_401(client):
    r = client.get("/protected", headers={"Authorization": "key-a"})
    assert r.status_code == 401


def test_known_token_returns_tenant_id(client):
    r = client.get("/protected", headers={"Authorization": "Bearer key-a"})
    assert r.status_code == 200
    assert r.json() == {"tenant_id": "tenant-a"}


def test_second_token_resolves_separately(client):
    r = client.get("/protected", headers={"Authorization": "Bearer key-b"})
    assert r.status_code == 200
    assert r.json() == {"tenant_id": "tenant-b"}
