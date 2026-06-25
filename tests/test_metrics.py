"""Tests for HTTP API Prometheus instrumentation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from monitor.main import app

# No lifespan context manager: these tests exercise dependency-free endpoints
# (/health, /metrics) without starting the scheduler.
client = TestClient(app)


def test_http_request_metrics_exposed() -> None:
    client.get("/health")
    body = client.get("/metrics").text
    assert "monitor_http_requests_total" in body
    assert "monitor_http_request_duration_seconds" in body
    # Labelled by route template + status, not the raw path.
    assert 'path="/health"' in body
    assert 'status="200"' in body


def test_metrics_endpoint_not_self_counted() -> None:
    # Hitting /metrics must not create a series for the scrape endpoint itself.
    client.get("/metrics")
    body = client.get("/metrics").text
    assert 'path="/metrics"' not in body
