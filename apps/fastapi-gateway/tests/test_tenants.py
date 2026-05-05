import textwrap

import pytest

from app.tenants import (
    AIMDConfig,
    GlobalBudgetConfig,
    Tenant,
    TenantRegistry,
    WorkerWatcherConfig,
    load_registry,
)


@pytest.fixture
def tenants_yaml(tmp_path):
    p = tmp_path / "tenants.yaml"
    p.write_text(textwrap.dedent("""
        caps_enabled: true
        num_workers: 2
        cap_per_worker: 16
        tenants:
          - id: tenant-a
            name: "Tenant A"
            weight: 1
            queue_max: 8
            key_env: TENANT_A_KEY
          - id: tenant-b
            name: "Tenant B"
            weight: 3
            queue_max: 16
            key_env: TENANT_B_KEY
    """).strip())
    return p


def test_load_registry_reads_weight_and_queue_max(tenants_yaml, monkeypatch):
    monkeypatch.setenv("TENANT_A_KEY", "key-a")
    monkeypatch.setenv("TENANT_B_KEY", "key-b")

    registry = load_registry(tenants_yaml)

    tenants = registry.all_tenants()
    assert [t.id for t in tenants] == ["tenant-a", "tenant-b"]
    assert tenants[0].weight == 1
    assert tenants[0].queue_max == 8
    assert tenants[1].weight == 3
    assert tenants[1].queue_max == 16


def test_load_registry_reads_global_budget_config(tenants_yaml, monkeypatch):
    monkeypatch.setenv("TENANT_A_KEY", "key-a")
    monkeypatch.setenv("TENANT_B_KEY", "key-b")

    registry = load_registry(tenants_yaml)
    cfg = registry.global_budget_config()

    assert isinstance(cfg, GlobalBudgetConfig)
    assert cfg.num_workers == 2
    assert cfg.cap_per_worker == 16


def test_resolve_known_token_returns_tenant(tenants_yaml, monkeypatch):
    monkeypatch.setenv("TENANT_A_KEY", "key-a")
    monkeypatch.setenv("TENANT_B_KEY", "key-b")
    registry = load_registry(tenants_yaml)

    t = registry.resolve("key-a")

    assert isinstance(t, Tenant)
    assert t.id == "tenant-a"


def test_resolve_unknown_token_returns_none(tenants_yaml, monkeypatch):
    monkeypatch.setenv("TENANT_A_KEY", "key-a")
    monkeypatch.setenv("TENANT_B_KEY", "key-b")
    registry = load_registry(tenants_yaml)

    assert registry.resolve("not-a-real-key") is None


def test_resolve_empty_token_returns_none(tenants_yaml, monkeypatch):
    monkeypatch.setenv("TENANT_A_KEY", "key-a")
    monkeypatch.setenv("TENANT_B_KEY", "key-b")
    registry = load_registry(tenants_yaml)

    assert registry.resolve("") is None


def test_missing_env_var_raises_at_load(tenants_yaml, monkeypatch):
    monkeypatch.delenv("TENANT_A_KEY", raising=False)
    monkeypatch.setenv("TENANT_B_KEY", "key-b")

    with pytest.raises(RuntimeError, match="TENANT_A_KEY"):
        load_registry(tenants_yaml)


def test_missing_global_budget_fields_raises(tmp_path, monkeypatch):
    p = tmp_path / "tenants.yaml"
    p.write_text(textwrap.dedent("""
        caps_enabled: true
        tenants:
          - id: tenant-a
            name: "A"
            weight: 1
            queue_max: 4
            key_env: TENANT_A_KEY
    """).strip())
    monkeypatch.setenv("TENANT_A_KEY", "k")

    with pytest.raises(RuntimeError, match="num_workers"):
        load_registry(p)


def test_caps_enabled_false_round_trips(tmp_path, monkeypatch):
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
    """).strip())
    monkeypatch.setenv("TENANT_A_KEY", "k")

    assert load_registry(p).caps_enabled() is False


def test_aimd_and_watcher_default_to_disabled_when_blocks_absent(tenants_yaml, monkeypatch):
    monkeypatch.setenv("TENANT_A_KEY", "key-a")
    monkeypatch.setenv("TENANT_B_KEY", "key-b")

    registry = load_registry(tenants_yaml)

    aimd = registry.aimd_config()
    assert isinstance(aimd, AIMDConfig)
    assert aimd.enabled is False

    watcher = registry.worker_watcher_config()
    assert isinstance(watcher, WorkerWatcherConfig)
    assert watcher.enabled is False
    assert watcher.namespace == ""
    assert watcher.service_name == ""


def test_load_registry_parses_aimd_and_watcher_blocks(tmp_path, monkeypatch):
    p = tmp_path / "tenants.yaml"
    p.write_text(textwrap.dedent("""
        caps_enabled: true
        num_workers: 2
        cap_per_worker: 16
        aimd:
          enabled: true
          target_p99_ttft_s: 2.0
          tick_s: 5.0
          window_s: 30.0
          cap_per_worker_min: 4
          cap_per_worker_max: 64
        worker_watcher:
          enabled: true
          namespace: vllm
          service_name: vllm-stack-qwen25-7b-engine-service
        tenants:
          - id: tenant-a
            name: "A"
            weight: 1
            queue_max: 4
            key_env: TENANT_A_KEY
    """).strip())
    monkeypatch.setenv("TENANT_A_KEY", "k")

    registry = load_registry(p)

    aimd = registry.aimd_config()
    assert aimd.enabled is True
    assert aimd.target_p99_ttft_s == 2.0
    assert aimd.tick_s == 5.0
    assert aimd.window_s == 30.0
    assert aimd.cap_per_worker_min == 4
    assert aimd.cap_per_worker_max == 64

    watcher = registry.worker_watcher_config()
    assert watcher.enabled is True
    assert watcher.namespace == "vllm"
    assert watcher.service_name == "vllm-stack-qwen25-7b-engine-service"


def test_aimd_and_watcher_enabled_false_round_trips(tmp_path, monkeypatch):
    p = tmp_path / "tenants.yaml"
    p.write_text(textwrap.dedent("""
        caps_enabled: true
        num_workers: 2
        cap_per_worker: 16
        aimd:
          enabled: false
        worker_watcher:
          enabled: false
        tenants:
          - id: tenant-a
            name: "A"
            weight: 1
            queue_max: 4
            key_env: TENANT_A_KEY
    """).strip())
    monkeypatch.setenv("TENANT_A_KEY", "k")

    registry = load_registry(p)
    assert registry.aimd_config().enabled is False
    assert registry.worker_watcher_config().enabled is False


def test_worker_watcher_enabled_without_namespace_raises(tmp_path, monkeypatch):
    p = tmp_path / "tenants.yaml"
    p.write_text(textwrap.dedent("""
        caps_enabled: true
        num_workers: 2
        cap_per_worker: 16
        worker_watcher:
          enabled: true
          service_name: vllm-stack-qwen25-7b-engine-service
        tenants:
          - id: tenant-a
            name: "A"
            weight: 1
            queue_max: 4
            key_env: TENANT_A_KEY
    """).strip())
    monkeypatch.setenv("TENANT_A_KEY", "k")

    with pytest.raises(RuntimeError, match="namespace"):
        load_registry(p)


def test_worker_watcher_enabled_without_service_name_raises(tmp_path, monkeypatch):
    p = tmp_path / "tenants.yaml"
    p.write_text(textwrap.dedent("""
        caps_enabled: true
        num_workers: 2
        cap_per_worker: 16
        worker_watcher:
          enabled: true
          namespace: vllm
        tenants:
          - id: tenant-a
            name: "A"
            weight: 1
            queue_max: 4
            key_env: TENANT_A_KEY
    """).strip())
    monkeypatch.setenv("TENANT_A_KEY", "k")

    with pytest.raises(RuntimeError, match="service_name"):
        load_registry(p)


def test_aimd_enabled_without_required_fields_raises(tmp_path, monkeypatch):
    p = tmp_path / "tenants.yaml"
    p.write_text(textwrap.dedent("""
        caps_enabled: true
        num_workers: 2
        cap_per_worker: 16
        aimd:
          enabled: true
          target_p99_ttft_s: 2.0
        tenants:
          - id: tenant-a
            name: "A"
            weight: 1
            queue_max: 4
            key_env: TENANT_A_KEY
    """).strip())
    monkeypatch.setenv("TENANT_A_KEY", "k")

    with pytest.raises(RuntimeError, match="aimd"):
        load_registry(p)
