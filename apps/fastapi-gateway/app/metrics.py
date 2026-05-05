from prometheus_client import Counter, Gauge, Histogram

requests_received_total = Counter(
    "gateway_requests_received_total",
    "Total HTTP requests received by the gateway (incremented at middleware entry, before any processing or queueing)",
    labelnames=("tenant_id", "route"),
)

requests_total = Counter(
    "gateway_requests_total",
    "Total HTTP requests completed by the gateway, by terminal status",
    labelnames=("tenant_id", "route", "status"),
)

request_duration_seconds = Histogram(
    "gateway_request_duration_seconds",
    "End-to-end request duration as observed by the gateway (request receipt to last byte sent)",
    labelnames=("tenant_id", "route", "status"),
    # Wide range with finer granularity than TTFT — E2E includes the entire
    # streaming response, so values can stretch into minutes under overload.
    buckets=(
        0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 7.5,
        10.0, 15.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0,
        240.0, 300.0, 450.0, 600.0,
    ),
)

inflight_requests = Gauge(
    "gateway_inflight_requests",
    "Number of in-flight requests currently being proxied",
    labelnames=("tenant_id", "route"),
)

ttft_seconds = Histogram(
    "gateway_ttft_seconds",
    "Time from gateway request receipt to first response chunk",
    labelnames=("tenant_id", "status"),
    # Fine-grained buckets — sub-100ms resolution near the bottom for healthy
    # serving, long tail to 600s for queue saturation under overload. Wider
    # than strictly needed; cardinality is bounded (~5 statuses × 3 tenants).
    buckets=(
        0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75,
        1.0, 1.5, 2.0, 3.0, 5.0, 7.5, 10.0, 15.0, 20.0, 30.0,
        45.0, 60.0, 90.0, 120.0, 180.0, 240.0, 300.0, 450.0, 600.0,
    ),
)

# Streaming responses can fail without changing their status code: upstream
# sends "200 OK" headers, then the body truncates mid-stream. The status-code
# counter undercounts these failures. This counter records every streaming
# response by its actual completion outcome.
stream_completed_total = Counter(
    "gateway_stream_completed_total",
    "Streaming response completion outcome (true=ended cleanly, false=truncated/exception)",
    labelnames=("tenant_id", "completed"),
)


gateway_queue_depth = Gauge(
    "gateway_queue_depth",
    "Current depth of the per-tenant scheduler queue",
    labelnames=("tenant_id",),
)

gateway_global_budget = Gauge(
    "gateway_global_budget",
    "Configured total dispatch budget (num_workers * cap_per_worker)",
)

gateway_global_inflight = Gauge(
    "gateway_global_inflight",
    "Number of slots currently held against the global budget",
)

gateway_cap_per_worker = Gauge(
    "gateway_cap_per_worker",
    "Current per-worker dispatch cap mutated by the AIMD controller; mirrors the cap_per_worker config bootstrap value at startup",
)

gateway_num_workers = Gauge(
    "gateway_num_workers",
    "Current K8s EndpointSlice-observed Ready endpoint count for the upstream vLLM engine Service; mirrors the num_workers bootstrap value when the watcher is disabled or has not yet received a reading",
)

gateway_aimd_action_total = Counter(
    "gateway_aimd_action_total",
    "AIMD controller decisions, incremented once per tick by action taken (increase, decrease, hold)",
    labelnames=("action",),
)

gateway_worker_watcher_events_total = Counter(
    "gateway_worker_watcher_events_total",
    "Per-event counter for the K8s EndpointSlice watcher, by event type (added, modified, deleted, error, reconnect)",
    labelnames=("event",),
)
