"""Endpoint tests for the FastAPI app (happy paths + edge cases)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from monitor.config import settings
from monitor.database import SessionLocal, init_db
from monitor.main import app
from monitor.models import CheckResult

_SVC = "api-test-sys"

_SERVICES = f"""
services:
  - name: {_SVC}
    type: system
    target: "."
    interval: 3600
"""


@pytest.fixture
def client(tmp_path):
    """A TestClient with the lifespan running against a controlled services file."""
    services_file = tmp_path / "services.yaml"
    services_file.write_text(_SERVICES)
    saved = settings.services_file
    settings.services_file = str(services_file)
    init_db()  # CI starts with a fresh DB — ensure tables exist before cleanup.
    _clear()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        _clear()
        settings.services_file = saved


def _clear() -> None:
    with SessionLocal() as session:
        session.execute(delete(CheckResult).where(CheckResult.name == _SVC))
        session.commit()


def _seed(status: str, **extra) -> None:
    with SessionLocal() as session:
        session.add(
            CheckResult(name=_SVC, check_type="system", target=".", status=status, **extra)
        )
        session.commit()


# ---------------------------------------------------------------------------
# Open endpoints
# ---------------------------------------------------------------------------


def test_frontend_serves_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_health_ok(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_ready_ok_with_sqlite(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["checks"]["database"] == "ok"


def test_metrics_exposition(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "monitor_checks_total" in resp.text


# ---------------------------------------------------------------------------
# /services
# ---------------------------------------------------------------------------


def test_services_unknown_without_data(client):
    resp = client.get("/services")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == _SVC
    assert body[0]["status"] == "unknown"
    assert body[0]["last_checked"] is None


def test_services_reflects_latest_result(client):
    _seed("down", latency_ms=10.0)
    _seed("up", latency_ms=42.0)
    resp = client.get("/services")
    row = resp.json()[0]
    assert row["status"] == "up"
    assert row["latency_ms"] == 42.0
    assert row["last_checked"] is not None


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------


def test_history_returns_rows_newest_first(client):
    _seed("up", latency_ms=1.0)
    _seed("down", latency_ms=2.0)
    resp = client.get("/history", params={"service": _SVC})
    body = resp.json()
    assert [r["status"] for r in body] == ["down", "up"]


def test_history_respects_limit(client):
    for _ in range(5):
        _seed("up")
    resp = client.get("/history", params={"service": _SVC, "limit": 2})
    assert len(resp.json()) == 2


def test_history_empty_for_unknown_service(client):
    assert client.get("/history", params={"service": "nope"}).json() == []


def test_history_rejects_out_of_range_limit(client):
    assert client.get("/history", params={"service": _SVC, "limit": 0}).status_code == 422
    assert client.get("/history", params={"service": _SVC, "limit": 999}).status_code == 422


# ---------------------------------------------------------------------------
# /checks/run + /checks/results
# ---------------------------------------------------------------------------


def test_run_check_system_persists_and_returns(client):
    resp = client.post(
        "/checks/run",
        json={"name": _SVC, "type": "system", "target": "."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == _SVC
    assert body["check_type"] == "system"
    assert body["status"] in ("up", "degraded")

    # The result was persisted and is visible via /checks/results.
    results = client.get("/checks/results", params={"limit": 10}).json()
    assert any(r["name"] == _SVC for r in results)
