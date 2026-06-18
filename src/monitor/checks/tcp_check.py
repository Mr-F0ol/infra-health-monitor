"""TCP port connectivity check."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .base import BaseCheck, CheckOutcome, CheckState


@dataclass
class TcpCheck(BaseCheck):
    """Open a TCP connection to ``host:port`` to verify reachability.

    ``target`` is the host; the port is supplied separately.
    """

    port: int = 0
    latency_threshold_ms: float | None = None

    check_type: str = field(default="tcp", init=False)

    async def run(self) -> CheckOutcome:
        start = self._now()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.target, self.port),
                timeout=self.timeout,
            )
        except TimeoutError:
            return self._outcome(CheckState.DOWN, detail="connection timed out")
        except OSError as exc:
            return self._outcome(CheckState.DOWN, detail=f"connection failed: {exc}")

        latency = self._elapsed_ms(start)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

        detail = f"connected to {self.target}:{self.port}"
        if self.latency_threshold_ms is not None and latency > self.latency_threshold_ms:
            return self._outcome(
                CheckState.DEGRADED,
                latency,
                f"{detail} — slow ({latency:.0f}ms > {self.latency_threshold_ms:.0f}ms)",
            )
        return self._outcome(CheckState.UP, latency, detail)
