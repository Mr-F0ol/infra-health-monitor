"""State-transition notifier with Redis-backed deduplication."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from redis.asyncio import Redis

from ..checks.base import CheckOutcome, CheckState
from .base import AlertProvider

logger = logging.getLogger(__name__)

_UNHEALTHY = {CheckState.DOWN, CheckState.DEGRADED}


def _format_message(outcome: CheckOutcome, previous_state: str | None) -> str:
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    latency = f"{outcome.latency_ms:.1f}ms" if outcome.latency_ms is not None else "N/A"

    if outcome.state == CheckState.UP and previous_state in ("down", "degraded"):
        icon, kind = "✅", "RECOVERED"
    else:
        icon, kind = "🔴", "ALERT"

    return (
        f"{icon} [{kind}] {outcome.name}\n"
        f"Status: {outcome.state.value.upper()} | Latency: {latency}\n"
        f"Target: {outcome.target}\n"
        f"Time: {ts}"
    )


class Notifier:
    """Sends alerts only on state transitions using Redis for deduplication."""

    def __init__(self, redis: Redis[bytes], providers: list[AlertProvider]) -> None:
        self._redis = redis
        self._providers = providers

    async def notify(self, outcome: CheckOutcome) -> None:
        key = f"monitor:state:{outcome.name}"
        raw = await self._redis.get(key)
        previous_state: str | None = raw.decode() if raw else None
        current_state = outcome.state.value

        state_changed = previous_state != current_state
        is_actionable = outcome.state in _UNHEALTHY or (
            outcome.state == CheckState.UP and previous_state in ("down", "degraded")
        )

        if state_changed and is_actionable:
            message = _format_message(outcome, previous_state)
            for provider in self._providers:
                try:
                    await provider.send(message)
                except Exception as exc:
                    logger.warning("alert provider %s failed: %s", type(provider).__name__, exc)

        await self._redis.set(key, current_state)
