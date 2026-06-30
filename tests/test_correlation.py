"""Tests for request correlation ids (X-Request-ID + log propagation)."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from monitor.logging_config import RequestIdFilter, request_id_var
from monitor.main import _sanitize_request_id, app

client = TestClient(app)


def test_request_id_header_is_generated() -> None:
    rid = client.get("/health").headers.get("X-Request-ID")
    assert rid is not None
    assert len(rid) == 32  # uuid4().hex


def test_request_id_header_is_reused_when_supplied() -> None:
    r = client.get("/health", headers={"X-Request-ID": "trace-abc"})
    assert r.headers["X-Request-ID"] == "trace-abc"


def test_filter_stamps_record_with_current_id() -> None:
    token = request_id_var.set("xyz")
    try:
        record = logging.makeLogRecord({})
        assert RequestIdFilter().filter(record) is True
        assert record.request_id == "xyz"
    finally:
        request_id_var.reset(token)


def test_filter_defaults_outside_request() -> None:
    record = logging.makeLogRecord({})
    RequestIdFilter().filter(record)
    assert record.request_id == "-"


def test_sanitize_request_id_strips_unsafe_chars() -> None:
    assert _sanitize_request_id("ab c;d\te") == "abcde"
    assert _sanitize_request_id("keep-this_1.2") == "keep-this_1.2"
    # All-unsafe input falls back to a generated id rather than an empty string.
    assert _sanitize_request_id("@@@") != ""
