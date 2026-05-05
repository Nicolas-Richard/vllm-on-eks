"""Integration tests for lifespan() wiring of Layer-2 controllers.

These pin the contract that:

- When ``aimd.enabled`` and/or ``worker_watcher.enabled`` are true, the
  matching controller is constructed, started, and exposed on
  ``app.state``.
- When both are disabled (or absent), no controllers are constructed —
  Layer-1 behavior is preserved.
- Configured AIMD knobs flow through to the constructed controller.

The watcher path uses a stream factory that yields no events to keep the
test isolated from the real K8s client. We inject the factory by
monkey-patching ``WorkerCapacityWatcher`` as imported by ``app.main`` so
the lifespan picks up our wrapper without needing an extra config seam.
"""

from __future__ import annotations

import asyncio
import importlib
import textwrap

from fastapi.testclient import TestClient

from app.scheduler import WorkerCapacityWatcher as RealWorkerCapacityWatcher


def _write_cfg(tmp_path, body: str) -> str:
    cfg = tmp_path / "tenants.yaml"
    cfg.write_text(body)
    return str(cfg)


def _reload_main(monkeypatch, cfg_path: str):
    monkeypatch.setenv("TENANT_A_KEY", "key-a")
    monkeypatch.setenv("TENANTS_PATH", cfg_path)
    monkeypatch.setenv("ACQUIRE_TIMEOUT_S", "5.0")
    import app.main as main_module
    importlib.reload(main_module)
    return main_module


_BASE_TENANTS = textwrap.dedent("""\
    tenants:
      - id: tenant-a
        name: "A"
        weight: 1
        queue_max: 4
        key_env: TENANT_A_KEY
    """)


def test_lifespan_skips_disabled_controllers(tmp_path, monkeypatch):
    cfg = _write_cfg(
        tmp_path,
        "caps_enabled: true\n"
        "num_workers: 1\n"
        "cap_per_worker: 2\n"
        + _BASE_TENANTS,
    )
    main_module = _reload_main(monkeypatch, cfg)
    with TestClient(main_module.app) as client:
        # Lifespan ran; healthz proves startup completed.
        assert client.get("/healthz").status_code == 200
        assert main_module.app.state.aimd is None
        assert main_module.app.state.watcher is None
    # Exiting the TestClient context drives lifespan shutdown; if it
    # didn't complete cleanly we'd hang or raise here.


def test_lifespan_starts_aimd_when_enabled(tmp_path, monkeypatch):
    cfg = _write_cfg(
        tmp_path,
        "caps_enabled: true\n"
        "num_workers: 1\n"
        "cap_per_worker: 8\n"
        "aimd:\n"
        "  enabled: true\n"
        "  target_p99_ttft_s: 2.5\n"
        "  tick_s: 60.0\n"
        "  window_s: 300.0\n"
        "  cap_per_worker_min: 2\n"
        "  cap_per_worker_max: 32\n"
        + _BASE_TENANTS,
    )
    main_module = _reload_main(monkeypatch, cfg)
    with TestClient(main_module.app) as client:
        assert client.get("/healthz").status_code == 200
        aimd = main_module.app.state.aimd
        assert aimd is not None
        # Pin config plumbing through to the constructed controller.
        assert aimd._target == 2.5
        assert aimd._tick_s == 60.0
        assert aimd._window_s == 300.0
        assert aimd._cap_min == 2
        assert aimd._cap_max == 32
        assert aimd._task is not None and not aimd._task.done()
        # Watcher block absent → no watcher.
        assert main_module.app.state.watcher is None
    # After shutdown the AIMD task should be cleared.
    assert main_module.app.state.aimd._task is None


def test_lifespan_starts_both_controllers_when_enabled(
    tmp_path, monkeypatch
):
    """Watcher path: monkeypatch ``app.main.WorkerCapacityWatcher`` to a
    wrapper that injects a no-event ``stream_factory``. The real watcher
    class is still exercised; only the K8s stack is short-circuited.
    """

    # Real K8s watch streams block indefinitely between events. To match
    # that, the stub awaits an Event that's never set; cancellation during
    # lifespan shutdown unwinds it. A naive empty async-generator would
    # return immediately and put the watcher into a tight reconnect loop.
    async def _blocking_iter():
        await asyncio.Event().wait()
        if False:
            yield  # pragma: no cover

    def _stream_factory(_namespace, _service_name):
        return _blocking_iter()

    class WatcherWithStub(RealWorkerCapacityWatcher):
        def __init__(self, scheduler, *, namespace, service_name):
            super().__init__(
                scheduler,
                namespace=namespace,
                service_name=service_name,
                stream_factory=_stream_factory,
            )

    cfg = _write_cfg(
        tmp_path,
        "caps_enabled: true\n"
        "num_workers: 1\n"
        "cap_per_worker: 8\n"
        "aimd:\n"
        "  enabled: true\n"
        "  target_p99_ttft_s: 2.0\n"
        "  tick_s: 60.0\n"
        "  window_s: 300.0\n"
        "  cap_per_worker_min: 4\n"
        "  cap_per_worker_max: 64\n"
        "worker_watcher:\n"
        "  enabled: true\n"
        "  namespace: vllm\n"
        "  service_name: vllm-engine\n"
        + _BASE_TENANTS,
    )
    main_module = _reload_main(monkeypatch, cfg)
    # Defensive: if a future refactor changes how main.py imports the watcher,
    # the monkeypatch silently no-ops and the test would hang on real K8s I/O.
    assert main_module.WorkerCapacityWatcher is RealWorkerCapacityWatcher
    monkeypatch.setattr(main_module, "WorkerCapacityWatcher", WatcherWithStub)

    with TestClient(main_module.app) as client:
        assert client.get("/healthz").status_code == 200
        assert main_module.app.state.aimd is not None
        watcher = main_module.app.state.watcher
        assert watcher is not None
        assert isinstance(watcher, RealWorkerCapacityWatcher)
        assert watcher._namespace == "vllm"
        assert watcher._service_name == "vllm-engine"
        assert watcher._task is not None
    # Shutdown ran in reverse order without raising.
    assert main_module.app.state.aimd._task is None
    assert main_module.app.state.watcher._task is None
