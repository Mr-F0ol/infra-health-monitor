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
    or timeout is ``DOWN``.
    """

    method: str = "GET"
    expected_statuses: tuple[int, ...] = (200,)

    check_type: str = field(default="http", init=False)

    async def run(self) -> CheckOutcome:
        start = self._now()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(self.method, self.target)
        except httpx.TimeoutException:
            return self._outcome(CheckState.DOWN, detail="request timed out")
        except httpx.HTTPError as exc:
            return self._outcome(CheckState.DOWN, detail=f"request failed: {exc}")

        latency = self._elapsed_ms(start)
        detail = f"HTTP {response.status_code}"
        if response.status_code in self.expected_statuses:
            return self._outcome(CheckState.UP, latency, detail)
        return self._outcome(CheckState.DEGRADED, latency, detail)
