"""Scheduler package: tenant-aware DRR + adaptive budget controllers.

Public surface re-exported from submodules so callers can keep using
``from app.scheduler import TenantScheduler, AIMDController, ...`` without
caring about the internal split.
"""

from app.scheduler.aimd import AIMDController
from app.scheduler.core import LimiterTimeout, TenantScheduler
from app.scheduler.watcher import (
    WatcherAuthError,
    WorkerCapacityWatcher,
    _count_ready_endpoints,
)

__all__ = [
    "AIMDController",
    "LimiterTimeout",
    "TenantScheduler",
    "WatcherAuthError",
    "WorkerCapacityWatcher",
    "_count_ready_endpoints",
]
