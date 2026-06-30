"""Tests for structured (JSON) and text logging configuration."""

from __future__ import annotations

import json
import logging

from monitor.logging_config import JsonFormatter, configure_logging


def _record(**kwargs: object) -> logging.LogRecord:
    defaults: dict[str, object] = {
        "name": "monitor.test",
        "level": logging.INFO,
        "pathname": __file__,
        "lineno": 1,
        "msg": "hello %s",
        "args": ("world",),
        "exc_info": None,
    }
    defaults.update(kwargs)
    return logging.LogRecord(**defaults)  # type: ignore[arg-type]


def test_json_formatter_emits_core_fields() -> None:
    payload = json.loads(JsonFormatter().format(_record()))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "monitor.test"
    assert payload["message"] == "hello world"  # %-args interpolated
    assert "timestamp" in payload


def test_json_formatter_promotes_extra_fields() -> None:
    record = _record()
    record.service = "api"  # type: ignore[attr-defined]  # mimics extra={"service": "api"}
    payload = json.loads(JsonFormatter().format(record))
    assert payload["service"] == "api"


def test_json_formatter_includes_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record(exc_info=sys.exc_info())
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exc_info"]


def test_configure_logging_installs_single_handler() -> None:
    try:
        configure_logging(level="DEBUG", fmt="json")
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        assert root.level == logging.DEBUG

        # Idempotent: a second call replaces rather than stacks handlers.
        configure_logging(level="WARNING", fmt="text")
        assert len(root.handlers) == 1
        assert not isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        # Restore a sane default so later tests aren't affected by WARNING level.
        configure_logging(level="INFO", fmt="text")
