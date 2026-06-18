"""Tests for the /reload endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from monitor.config import settings
from monitor.main import app

_SINGLE_SERVICE = """
services:
  - name: web
    type: http
    target: https://example.com
    interval: 60
"""

_TWO_SERVICES = _SINGLE_SERVICE + """  - name: api
    type: http
    target: https://api.example.com
    interval: 30
"""


def test_reload_invalid_file_returns_400_without_lifespan(tmp_path):
    """A missing file is rejected before touching the running scheduler."""
    saved = settings.services_file
    settings.services_file = str(tmp_path / "nope.yaml")
    try:
        resp = TestClient(app).post("/reload")
    finally:
        settings.services_file = saved
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"]


def test_reload_applies_added_service(tmp_path):
    services_file = tmp_path / "services.yaml"
    services_file.write_text(_SINGLE_SERVICE)

    saved = settings.services_file
    settings.services_file = str(services_file)
    try:
        with TestClient(app) as client:
            # Add a second service on disk, then reload.
            services_file.write_text(_TWO_SERVICES)
            resp = client.post("/reload")
    finally:
        settings.services_file = saved

    assert resp.status_code == 200
    body = resp.json()
    assert body["services"] == 2
    assert body["added"] == ["api"]
    assert body["removed"] == []
