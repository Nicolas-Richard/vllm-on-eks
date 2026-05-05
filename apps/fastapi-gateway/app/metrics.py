from prometheus_client import Counter, Gauge, Histogram

requests_total = Counter(
    "gateway_requests_total",
    "Total HTTP requests handled by the gateway",
    labelnames=("route", "status"),
)

request_duration_seconds = Histogram(
    "gateway_request_duration_seconds",
    "End-to-end request duration as observed by the gateway",
    labelnames=("route",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

inflight_requests = Gauge(
    "gateway_inflight_requests",
    "Number of in-flight requests currently being proxied",
)
