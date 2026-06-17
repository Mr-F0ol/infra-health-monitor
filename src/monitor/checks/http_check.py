"""HTTP(S) endpoint check."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .base import BaseCheck, CheckOutcome, CheckState


@dataclass
class HttpCheck(BaseCheck):
    """Probe an HTTP endpoint and classify by status code.

    A response with a status code in ``expected_statuses`` is ``UP``. Any other
    response is ``DEGRADED`` (reachable but unexpected), and a transport error
    or timeout is ``DOWN``. An otherwise-healthy response whose latency exceeds
    ``latency_threshold_ms`` is also ``DEGRADED`` (reachable but slow).
    """

    method: str = "GET"
    expected_statuses: tuple[int, ...] = (200,)
    latency_threshold_ms: float | None = None

    check_type: str = field(default="http", init=False)

    async def run(self) -> CheckOutcome:
        start = self._now()
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True
            ) as client:
                response = await client.request(self.method, self.target)
        except httpx.TimeoutException:
            return self._outcome(CheckState.DOWN, detail="request timed out")
        except httpx.HTTPError as exc:
            return self._outcome(CheckState.DOWN, detail=f"request failed: {exc}")

        latency = self._elapsed_ms(start)
        detail = f"HTTP {response.status_code}"
        if response.status_code not in self.expected_statuses:
            return self._outcome(CheckState.DEGRADED, latency, detail)
        if self.latency_threshold_ms is not None and latency > self.latency_threshold_ms:
            return self._outcome(
                CheckState.DEGRADED,
                latency,
                f"{detail} — slow ({latency:.0f}ms > {self.latency_threshold_ms:.0f}ms)",
            )
        return self._outcome(CheckState.UP, latency, detail)
