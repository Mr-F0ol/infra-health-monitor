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
    """Sends alerts only on state transitions using Redis for deduplication.

    To suppress flapping, an unhealthy state must be observed
    ``failure_threshold`` times in a row before it is confirmed and alerted.
    A single healthy result confirms recovery immediately. The persisted state
    key tracks the last *confirmed* (alerted) state, so repeated failures never
    spam once an alert has fired.
    """

    def __init__(
        self,
        redis: Redis[bytes],
        providers: list[AlertProvider],
        failure_threshold: int = 3,
    ) -> None:
        self._redis = redis
        self._providers = providers
        self._failure_threshold = max(1, failure_threshold)

    async def _broadcast(self, message: str) -> None:
        for provider in self._providers:
            try:
                await provider.send(message)
            except Exception as exc:
                logger.warning("alert provider %s failed: %s", type(provider).__name__, exc)

    async def notify(self, outcome: CheckOutcome) -> None:
        state_key = f"monitor:state:{outcome.name}"
        fail_key = f"monitor:fails:{outcome.name}"

        raw = await self._redis.get(state_key)
        confirmed_state: str | None = raw.decode() if raw else None
        current_state = outcome.state.value

        if outcome.state not in _UNHEALTHY:
            # Healthy: clear the failure streak; alert if recovering from a
            # previously confirmed unhealthy state.
            await self._redis.delete(fail_key)
            if confirmed_state in ("down", "degraded"):
                await self._broadcast(_format_message(outcome, confirmed_state))
            await self._redis.set(state_key, current_state)
            return

        # Unhealthy: only confirm + alert once the streak reaches the threshold
        # and the confirmed state actually changes.
        fails = await self._redis.incr(fail_key)
        if fails >= self._failure_threshold and confirmed_state != current_state:
            await self._broadcast(_format_message(outcome, confirmed_state))
            await self._redis.set(state_key, current_state)
