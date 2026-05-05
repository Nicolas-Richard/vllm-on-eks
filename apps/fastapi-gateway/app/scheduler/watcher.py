from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.metrics import gateway_worker_watcher_events_total

if TYPE_CHECKING:
    from app.scheduler.core import TenantScheduler

logger = logging.getLogger(__name__)


class WatcherAuthError(Exception):
    """Raised inside ``_watch_endpointslices`` when the K8s API returns 401/403.

    Caught by ``_loop`` to abort the watcher (RBAC misconfig is not transient).
    The scheduler holds the last-known ``num_workers``; pod restart is recovery.
    """


def _translate_api_exception(exc: Exception) -> Exception:
    """Translate a ``kubernetes_asyncio`` ``ApiException`` with status 401/403
    into ``WatcherAuthError``; pass everything else through unchanged.

    Imported lazily so the module can be loaded without
    ``kubernetes_asyncio`` installed (helps tests). When the import fails we
    can't possibly have an ``ApiException`` to translate, so passthrough is
    safe.
    """
    try:
        from kubernetes_asyncio.client.exceptions import ApiException
    except ImportError:
        return exc
    if isinstance(exc, ApiException) and exc.status in (401, 403):
        return WatcherAuthError(f"k8s API returned {exc.status}")
    return exc


class WorkerCapacityWatcher:
    """Watches K8s EndpointSlices for the upstream vLLM engine Service and
    forwards Ready endpoint count changes to ``TenantScheduler.set_num_workers``.

    Hold-last-value on disconnect with exponential backoff (1s → 30s cap). On
    auth errors (401/403) the loop aborts loudly rather than spin: stale-but-
    serving is preferable to a tight retry loop on an RBAC misconfig.
    """

    _BACKOFF_INITIAL = 1.0
    _BACKOFF_MAX = 30.0

    def __init__(
        self,
        scheduler: "TenantScheduler",
        namespace: str,
        service_name: str,
        *,
        backoff_initial: float | None = None,
        backoff_max: float | None = None,
        stream_factory=None,
    ) -> None:
        self._scheduler = scheduler
        self._namespace = namespace
        self._service_name = service_name
        self._backoff_initial = (
            backoff_initial if backoff_initial is not None else self._BACKOFF_INITIAL
        )
        self._backoff_max = (
            backoff_max if backoff_max is not None else self._BACKOFF_MAX
        )
        # Optional injection point for tests: a callable that returns an async
        # iterator of watch events. May raise ``ApiException`` synchronously
        # (translated to WatcherAuthError) or from inside the iterator. When
        # None, the real kubernetes_asyncio stack is used.
        self._stream_factory = stream_factory
        self._slice_ready_counts: dict[str, int] = {}
        self._last_observed: int | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        try:
            await self._loop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker-watcher loop crashed")

    async def _loop(self) -> None:
        backoff = self._backoff_initial
        first_iteration = True
        while True:
            if not first_iteration:
                gateway_worker_watcher_events_total.labels(event="reconnect").inc()
            first_iteration = False
            try:
                await self._watch_endpointslices()
                # Stream closed normally — reconnect immediately, fresh backoff.
                backoff = self._backoff_initial
            except WatcherAuthError:
                # 403/401: not transient. Abort; scheduler holds last value.
                logger.error(
                    "worker-watcher auth failed; holding num_workers at %d",
                    self._scheduler.num_workers,
                )
                gateway_worker_watcher_events_total.labels(event="error").inc()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "worker-watcher disconnected; reconnect in %.1fs", backoff
                )
                gateway_worker_watcher_events_total.labels(event="error").inc()
                await asyncio.sleep(backoff)
                backoff = min(self._backoff_max, backoff * 2)

    async def _watch_endpointslices(self) -> None:
        # Both real and injected paths funnel through the same ApiException
        # translation. The factory may raise synchronously when constructing
        # the stream, or the async iterator may raise during consumption —
        # either way a 401/403 surfaces as WatcherAuthError to the loop.
        try:
            stream = await self._make_stream()
            try:
                await self._consume_stream(stream)
            finally:
                await self._close_stream_resources()
        except Exception as e:
            translated = _translate_api_exception(e)
            if translated is not e:
                raise translated from e
            raise

    async def _make_stream(self):
        """Build the watch stream. Test factories short-circuit the K8s stack."""
        if self._stream_factory is not None:
            # Test-injected stream — bypass the real K8s client. The factory
            # may raise (e.g., ApiException(403)) synchronously; the caller
            # translates it into WatcherAuthError.
            return self._stream_factory(self._namespace, self._service_name)

        # Imported lazily so importing this module doesn't require
        # kubernetes_asyncio to be installed at module-load time (helps tests).
        from kubernetes_asyncio import client, config, watch

        try:
            config.load_incluster_config()
        except config.ConfigException:
            await config.load_kube_config()

        api_client = client.ApiClient()
        api = client.DiscoveryV1Api(api_client)
        w = watch.Watch()
        stream = w.stream(
            api.list_namespaced_endpoint_slice,
            namespace=self._namespace,
            label_selector=f"kubernetes.io/service-name={self._service_name}",
        )
        # Stash so _close_stream_resources can clean up.
        self._active_api_client = api_client
        self._active_watch = w
        return stream

    async def _close_stream_resources(self) -> None:
        """Tear down resources opened by ``_make_stream`` for the real path."""
        w = getattr(self, "_active_watch", None)
        if w is not None:
            try:
                w.stop()
            except Exception:
                pass
            self._active_watch = None
        api_client = getattr(self, "_active_api_client", None)
        if api_client is not None:
            try:
                await api_client.close()
            except Exception:
                pass
            self._active_api_client = None

    async def _consume_stream(self, stream) -> None:
        async for event in stream:
            ev_type = event["type"]
            slice_obj = event["object"]
            label = ev_type.lower()
            if label in ("added", "modified", "deleted"):
                gateway_worker_watcher_events_total.labels(event=label).inc()
            name = getattr(slice_obj.metadata, "name", None)
            if name is None:
                continue
            if ev_type == "DELETED":
                self._slice_ready_counts.pop(name, None)
            else:
                self._slice_ready_counts[name] = _count_ready_endpoints(slice_obj)
            total = sum(self._slice_ready_counts.values())
            if total != self._last_observed:
                self._last_observed = total
                await self._scheduler.set_num_workers(total)


def _count_ready_endpoints(slice_obj) -> int:
    """Count endpoints in an EndpointSlice whose ``conditions.ready`` is True.

    Per K8s convention, ``ready`` of None or missing is treated as not-ready
    (unknown == not-ready). Only an explicit True counts.
    """
    endpoints = getattr(slice_obj, "endpoints", None) or []
    count = 0
    for ep in endpoints:
        conds = getattr(ep, "conditions", None)
        if conds is None:
            continue
        if getattr(conds, "ready", None) is True:
            count += 1
    return count
