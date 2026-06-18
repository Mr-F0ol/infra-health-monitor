"""Tests for optional API authentication."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from monitor.config import settings
from monitor.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_auth():
    """Snapshot and restore auth settings around each test."""
    saved = (settings.api_key, settings.basic_auth_user, settings.basic_auth_password)
    settings.api_key = ""
    settings.basic_auth_user = ""
    settings.basic_auth_password = ""
    yield
    settings.api_key, settings.basic_auth_user, settings.basic_auth_password = saved


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_disabled_auth_allows_protected_endpoint():
    assert client.get("/").status_code == 200


def test_health_always_open_even_with_auth_enabled():
    settings.api_key = "secret"
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code in (200, 503)


def test_missing_credentials_rejected():
    settings.api_key = "secret"
    resp = client.get("/")
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


def test_correct_api_key_accepted():
    settings.api_key = "secret"
    assert client.get("/", headers={"X-API-Key": "secret"}).status_code == 200


def test_wrong_api_key_rejected():
    settings.api_key = "secret"
    assert client.get("/", headers={"X-API-Key": "nope"}).status_code == 401


def test_basic_auth_accepted():
    settings.basic_auth_user = "admin"
    settings.basic_auth_password = "pw"
    assert client.get("/", headers=_basic("admin", "pw")).status_code == 200


def test_basic_auth_wrong_password_rejected():
    settings.basic_auth_user = "admin"
    settings.basic_auth_password = "pw"
    assert client.get("/", headers=_basic("admin", "bad")).status_code == 401


def test_malformed_basic_header_rejected():
    settings.basic_auth_user = "admin"
    settings.basic_auth_password = "pw"
    assert client.get("/", headers={"Authorization": "Basic !!!notbase64"}).status_code == 401
