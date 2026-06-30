"""Application logging setup — human-readable text or structured JSON.

Why: this is an *observability* product, so its own logs should be ready for an
aggregator (Loki / ELK / Datadog). ``MONITOR_LOG_FORMAT=json`` emits one JSON
object per line — the Docker image enables it — while the default ``text`` keeps
local development readable. No third-party dependency: the formatter is stdlib.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from contextvars import ContextVar
from typing import Any

# Per-request correlation id, set by the HTTP middleware and read back into every
# log record via the filter below. Defaults to "-" outside a request (e.g. the
# scheduler's background jobs), so the field is always present.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Attributes already present on a bare LogRecord. Anything *else* on a record was
# supplied by the caller via ``logger.info(..., extra={...})`` and is promoted to
# a top-level field in the JSON output.
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

_TEXT_FORMAT = "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s %(message)s"


class RequestIdFilter(logging.Filter):
    """Stamp every record with the current request id so it reaches the output."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """Render a log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        # Promote caller-supplied ``extra`` fields.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Install a single root handler in the requested format.

    Idempotent: existing root handlers are replaced, so repeated calls (or an
    import after uvicorn set up its own logging) leave exactly one handler.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter() if fmt.lower() == "json" else logging.Formatter(_TEXT_FORMAT)
    )
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
