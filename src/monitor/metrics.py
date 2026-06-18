"""Prometheus metrics for the health monitor."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from .checks.base import CheckState

service_status = Gauge(
    "monitor_service_status",
    "Current service status: 1=up, 0=degraded, -1=down",
    ["service", "type"],
)
check_latency_ms = Histogram(
    "monitor_check_latency_ms",
    "Check latency in milliseconds",
    ["service", "type"],
    buckets=[10, 50, 100, 250, 500, 1000, 2500, 5000],
)
checks_total = Counter(
    "monitor_checks_total",
    "Total checks executed",
    ["service", "type"],
)
checks_failed_total = Counter(
    "monitor_checks_failed_total",
    "Total failed checks (down or degraded)",
    ["service", "type"],
)
cert_expiry_days = Gauge(
    "monitor_cert_expiry_days",
    "Days until the TLS certificate expires (HTTPS checks with a cert threshold)",
    ["service"],
)

_STATE_VALUES: dict[CheckState, float] = {
    CheckState.UP: 1.0,
    CheckState.DEGRADED: 0.0,
    CheckState.DOWN: -1.0,
    CheckState.UNKNOWN: 0.0,
}


def record_outcome(
    name: str,
    check_type: str,
    state: CheckState,
    latency_ms: float | None,
    cert_days_remaining: float | None = None,
) -> None:
    labels = {"service": name, "type": check_type}
    service_status.labels(**labels).set(_STATE_VALUES.get(state, 0.0))
    checks_total.labels(**labels).inc()
    if state in (CheckState.DOWN, CheckState.DEGRADED):
        checks_failed_total.labels(**labels).inc()
    if latency_ms is not None:
        check_latency_ms.labels(**labels).observe(latency_ms)
    if cert_days_remaining is not None:
        cert_expiry_days.labels(service=name).set(cert_days_remaining)
