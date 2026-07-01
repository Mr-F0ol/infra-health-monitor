"""Tests for per-IP rate limiting."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from monitor import main
from monitor.config import settings
from monitor.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _configure():
    """Enable rate limiting with a small, deterministic window for these tests."""
    saved = (
        settings.rate_limit_enabled,
        settings.rate_limit_per_window,
        settings.rate_limit_window_seconds,
    )
    settings.rate_limit_enabled = True
    settings.rate_limit_per_window = 3
    settings.rate_limit_window_seconds = 60
    main._rate_limit_counts.clear()
    main._rate_limit_window = 0
    yield
    (
        settings.rate_limit_enabled,
        settings.rate_limit_per_window,
        settings.rate_limit_window_seconds,
    ) = saved
    main._rate_limit_counts.clear()
    main._rate_limit_window = 0


def test_requests_within_limit_pass():
    for _ in range(3):
        assert client.get("/").status_code == 200


def test_requests_beyond_limit_get_429_with_retry_after():
    for _ in range(3):
        assert client.get("/").status_code == 200
    resp = client.get("/")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_health_ready_metrics_are_exempt():
    for _ in range(10):
        assert client.get("/health").status_code == 200
        assert client.get("/metrics").status_code == 200


def test_disabled_by_default_setting_allows_unlimited_requests():
    settings.rate_limit_enabled = False
    for _ in range(10):
        assert client.get("/").status_code == 200
