"""Tests for opt-in OpenTelemetry tracing setup."""

from __future__ import annotations

import importlib.util

import pytest
from fastapi import FastAPI

from monitor.config import settings
from monitor.tracing import configure_tracing

_OTEL_INSTALLED = importlib.util.find_spec("opentelemetry") is not None


def test_tracing_is_noop_when_disabled() -> None:
    saved = settings.otel_enabled
    settings.otel_enabled = False
    try:
        configure_tracing(FastAPI())  # must not raise or instrument
    finally:
        settings.otel_enabled = saved


def test_tracing_enabled_never_raises() -> None:
    saved = settings.otel_enabled
    settings.otel_enabled = True
    try:
        configure_tracing(FastAPI())  # instruments if installed, warns if not
    finally:
        settings.otel_enabled = saved


@pytest.mark.skipif(_OTEL_INSTALLED, reason="otel extra installed; warning path not hit")
def test_tracing_warns_when_extra_missing(caplog: pytest.LogCaptureFixture) -> None:
    saved = settings.otel_enabled
    settings.otel_enabled = True
    try:
        with caplog.at_level("WARNING"):
            configure_tracing(FastAPI())
        assert any("OpenTelemetry" in r.message for r in caplog.records)
    finally:
        settings.otel_enabled = saved
