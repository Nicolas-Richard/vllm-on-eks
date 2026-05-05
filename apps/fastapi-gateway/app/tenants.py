from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    weight: int
    queue_max: int


@dataclass(frozen=True)
class GlobalBudgetConfig:
    num_workers: int
    cap_per_worker: int


@dataclass(frozen=True)
class AIMDConfig:
    enabled: bool
    target_p99_ttft_s: float
    tick_s: float
    window_s: float
    cap_per_worker_min: int
    cap_per_worker_max: int
    # Optional deadband around target_p99_ttft_s. 0.0 = strict spec form.
    # 0.2 means halve only when p99 > 1.2*target, climb only when p99 < 0.8*target.
    target_band_pct: float = 0.0
    # After any non-hold action, force "hold" for this many ticks before the
    # next decision. 0 = no cooldown (act every tick).
    cooldown_ticks: int = 0
    # Subtractive decrease step. cap -= decrease_step on each MD tick (subject
    # to cap_per_worker_min floor). 1 = gentlest AISD; larger values approach
    # classical halving for higher caps.
    decrease_step: int = 1


@dataclass(frozen=True)
class WorkerWatcherConfig:
    enabled: bool
    namespace: str
    service_name: str


_AIMD_DEFAULTS = AIMDConfig(
    enabled=False,
    target_p99_ttft_s=2.0,
    tick_s=5.0,
    window_s=30.0,
    cap_per_worker_min=4,
    cap_per_worker_max=64,
    target_band_pct=0.0,
    cooldown_ticks=0,
    decrease_step=1,
)

_WATCHER_DEFAULTS = WorkerWatcherConfig(
    enabled=False,
    namespace="",
    service_name="",
)


class TenantRegistry:
    def __init__(
        self,
        tenants: list[Tenant],
        token_to_id: dict[str, str],
        caps_enabled: bool,
        global_budget: GlobalBudgetConfig,
        aimd: AIMDConfig = _AIMD_DEFAULTS,
        worker_watcher: WorkerWatcherConfig = _WATCHER_DEFAULTS,
    ):
        self._tenants = tenants
        self._by_id = {t.id: t for t in tenants}
        self._token_to_id = token_to_id
        self._caps_enabled = caps_enabled
        self._global_budget = global_budget
        self._aimd = aimd
        self._worker_watcher = worker_watcher

    def resolve(self, bearer_token: str) -> Tenant | None:
        if not bearer_token:
            return None
        tid = self._token_to_id.get(bearer_token)
        return self._by_id.get(tid) if tid else None

    def caps_enabled(self) -> bool:
        return self._caps_enabled

    def all_tenants(self) -> list[Tenant]:
        return list(self._tenants)

    def global_budget_config(self) -> GlobalBudgetConfig:
        return self._global_budget

    def aimd_config(self) -> AIMDConfig:
        return self._aimd

    def worker_watcher_config(self) -> WorkerWatcherConfig:
        return self._worker_watcher


def _parse_aimd(raw: dict | None) -> AIMDConfig:
    if not raw:
        return _AIMD_DEFAULTS
    enabled = bool(raw.get("enabled", False))
    if not enabled:
        return AIMDConfig(
            enabled=False,
            target_p99_ttft_s=float(raw.get("target_p99_ttft_s", _AIMD_DEFAULTS.target_p99_ttft_s)),
            tick_s=float(raw.get("tick_s", _AIMD_DEFAULTS.tick_s)),
            window_s=float(raw.get("window_s", _AIMD_DEFAULTS.window_s)),
            cap_per_worker_min=int(raw.get("cap_per_worker_min", _AIMD_DEFAULTS.cap_per_worker_min)),
            cap_per_worker_max=int(raw.get("cap_per_worker_max", _AIMD_DEFAULTS.cap_per_worker_max)),
            target_band_pct=float(raw.get("target_band_pct", _AIMD_DEFAULTS.target_band_pct)),
            cooldown_ticks=int(raw.get("cooldown_ticks", _AIMD_DEFAULTS.cooldown_ticks)),
            decrease_step=int(raw.get("decrease_step", _AIMD_DEFAULTS.decrease_step)),
        )
    # target_band_pct, cooldown_ticks, decrease_step are optional even when enabled.
    optional = ("enabled", "target_band_pct", "cooldown_ticks", "decrease_step")
    required = tuple(f.name for f in fields(AIMDConfig) if f.name not in optional)
    missing = [k for k in required if k not in raw]
    if missing:
        raise RuntimeError(
            f"aimd config enabled but missing required fields: {', '.join(missing)}"
        )
    return AIMDConfig(
        enabled=True,
        target_p99_ttft_s=float(raw["target_p99_ttft_s"]),
        tick_s=float(raw["tick_s"]),
        window_s=float(raw["window_s"]),
        cap_per_worker_min=int(raw["cap_per_worker_min"]),
        cap_per_worker_max=int(raw["cap_per_worker_max"]),
        target_band_pct=float(raw.get("target_band_pct", _AIMD_DEFAULTS.target_band_pct)),
        cooldown_ticks=int(raw.get("cooldown_ticks", _AIMD_DEFAULTS.cooldown_ticks)),
        decrease_step=int(raw.get("decrease_step", _AIMD_DEFAULTS.decrease_step)),
    )


def _parse_worker_watcher(raw: dict | None) -> WorkerWatcherConfig:
    if not raw:
        return _WATCHER_DEFAULTS
    enabled = bool(raw.get("enabled", False))
    required = tuple(f.name for f in fields(WorkerWatcherConfig) if f.name != "enabled")
    values = {k: str(raw.get(k, "") or "") for k in required}
    if enabled:
        missing = [k for k in required if not values[k]]
        if missing:
            raise RuntimeError(
                f"worker_watcher config enabled but missing required fields: {', '.join(missing)}"
            )
    return WorkerWatcherConfig(enabled=enabled, **values)


def load_registry(path: str | Path) -> TenantRegistry:
    raw = yaml.safe_load(Path(path).read_text())
    caps_enabled = bool(raw.get("caps_enabled", False))

    if "num_workers" not in raw or "cap_per_worker" not in raw:
        raise RuntimeError(
            "tenants config missing required top-level fields: num_workers, cap_per_worker"
        )
    global_budget = GlobalBudgetConfig(
        num_workers=int(raw["num_workers"]),
        cap_per_worker=int(raw["cap_per_worker"]),
    )

    aimd = _parse_aimd(raw.get("aimd"))
    worker_watcher = _parse_worker_watcher(raw.get("worker_watcher"))

    tenants: list[Tenant] = []
    token_to_id: dict[str, str] = {}
    for entry in raw["tenants"]:
        env_name = entry["key_env"]
        key = os.environ.get(env_name)
        if not key:
            raise RuntimeError(f"missing tenant key env var: {env_name}")
        tenant = Tenant(
            id=entry["id"],
            name=entry["name"],
            weight=int(entry["weight"]),
            queue_max=int(entry["queue_max"]),
        )
        tenants.append(tenant)
        token_to_id[key] = tenant.id

    return TenantRegistry(
        tenants=tenants,
        token_to_id=token_to_id,
        caps_enabled=caps_enabled,
        global_budget=global_budget,
        aimd=aimd,
        worker_watcher=worker_watcher,
    )
