"""Tests for the /uptime endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from monitor.config import settings
from monitor.database import SessionLocal, init_db
from monitor.main import app
from monitor.models import CheckResult

# Unique names so the aggregation never collides with pre-existing dev data.
_SVC_A = "uptime-test-a"
_SVC_B = "uptime-test-b"

_SERVICES = f"""
services:
  - name: {_SVC_A}
    type: http
    target: https://example.com
    interval: 3600
  - name: {_SVC_B}
    type: http
    target: https://example.org
    interval: 3600
"""


@pytest.fixture(autouse=True)
def _clean_test_rows():
    """The dev SQLite DB persists between runs — isolate the test service rows."""
    init_db()  # CI starts with a fresh DB — ensure tables exist before cleanup.
    with SessionLocal() as session:
        session.execute(delete(CheckResult).where(CheckResult.name.in_([_SVC_A, _SVC_B])))
        session.commit()
    yield
    with SessionLocal() as session:
        session.execute(delete(CheckResult).where(CheckResult.name.in_([_SVC_A, _SVC_B])))
        session.commit()


def _seed(name: str, statuses: list[str]) -> None:
    with SessionLocal() as session:
        for status in statuses:
            session.add(
                CheckResult(name=name, check_type="http", target="x", status=status)
            )
        session.commit()


def _client(tmp_path) -> tuple[TestClient, str]:
    services_file = tmp_path / "services.yaml"
    services_file.write_text(_SERVICES)
    saved = settings.services_file
    settings.services_file = str(services_file)
    return TestClient(app), saved


def test_uptime_counts_non_down_as_available(tmp_path):
    # 2 up + 1 degraded (available) + 1 down → 3/4 = 75%.
    _seed(_SVC_A, ["up", "up", "degraded", "down"])
    client, saved = _client(tmp_path)
    try:
        with client:
            resp = client.get("/uptime", params={"window": "24h", "service": _SVC_A})
    finally:
        settings.services_file = saved

    assert resp.status_code == 200
    body = resp.json()[0]
    assert body["service"] == _SVC_A
    assert body["total_checks"] == 4
    assert body["up_checks"] == 3
    assert body["uptime_pct"] == 75.0


def test_uptime_null_when_no_data(tmp_path):
    client, saved = _client(tmp_path)
    try:
        with client:
            resp = client.get("/uptime", params={"service": _SVC_B})
    finally:
        settings.services_file = saved

    assert resp.status_code == 200
    body = resp.json()[0]
    assert body["uptime_pct"] is None
    assert body["total_checks"] == 0


def test_uptime_rejects_invalid_window(tmp_path):
    client, saved = _client(tmp_path)
    try:
        with client:
            resp = client.get("/uptime", params={"window": "1y"})
    finally:
        settings.services_file = saved
    assert resp.status_code == 422
