import os

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("BEARER_TOKEN", "secret-token")
    # Force re-import so the module reads the patched env var.
    import importlib
    from app import auth as auth_module
    importlib.reload(auth_module)

    app = FastAPI()

    @app.get("/protected")
    def protected(_: None = Depends(auth_module.require_bearer_token)):
        return {"ok": True}

    return TestClient(app)


def test_missing_authorization_header_returns_401(client):
    response = client.get("/protected")
    assert response.status_code == 401


def test_wrong_token_returns_401(client):
    response = client.get("/protected", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_missing_bearer_prefix_returns_401(client):
    response = client.get("/protected", headers={"Authorization": "secret-token"})
    assert response.status_code == 401


def test_correct_token_returns_200(client):
    response = client.get("/protected", headers={"Authorization": "Bearer secret-token"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
