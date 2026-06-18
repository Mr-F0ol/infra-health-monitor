"""HTTP(S) endpoint check."""

from __future__ import annotations

import asyncio
import socket
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from .base import BaseCheck, CheckOutcome, CheckState


def _parse_not_after(value: str) -> datetime:
    # OpenSSL format, e.g. "Jun 18 12:00:00 2026 GMT".
    return datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)


async def _cert_days_remaining(host: str, port: int, timeout: float) -> float:
    """Open a TLS connection and return days until the peer cert expires.

    Runs the blocking socket handshake in a thread so it never stalls the event
    loop. Raises on connection/handshake failure or an unreadable certificate.
    """

    def _probe() -> float:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        if not cert or "notAfter" not in cert:
            raise ValueError("no certificate returned")
        expires = _parse_not_after(str(cert["notAfter"]))
        return (expires - datetime.now(UTC)).total_seconds() / 86400

    return await asyncio.to_thread(_probe)


@dataclass
class HttpCheck(BaseCheck):
    """Probe an HTTP endpoint and classify by status code.

    A response with a status code in ``expected_statuses`` is ``UP``. Any other
    response is ``DEGRADED`` (reachable but unexpected), and a transport error
    or timeout is ``DOWN``. An otherwise-healthy response whose latency exceeds
    ``latency_threshold_ms`` is also ``DEGRADED`` (reachable but slow).

    For HTTPS targets, set ``cert_expiry_days`` to also verify the TLS
    certificate: an expired cert is ``DOWN`` and one expiring within the
    threshold is ``DEGRADED``. The measured days-remaining is carried on the
    outcome for metrics regardless of state.
    """

    method: str = "GET"
    expected_statuses: tuple[int, ...] = (200,)
    latency_threshold_ms: float | None = None
    cert_expiry_days: float | None = None

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

        cert_days = await self._check_certificate()
        if cert_days is not None and cert_days <= 0:
            return self._outcome(
                CheckState.DOWN, latency, f"{detail} — cert expired", cert_days
            )

        if response.status_code not in self.expected_statuses:
            return self._outcome(CheckState.DEGRADED, latency, detail, cert_days)

        if cert_days is not None and cert_days < (self.cert_expiry_days or 0):
            return self._outcome(
                CheckState.DEGRADED,
                latency,
                f"{detail} — cert expires in {cert_days:.0f}d",
                cert_days,
            )

        if self.latency_threshold_ms is not None and latency > self.latency_threshold_ms:
            return self._outcome(
                CheckState.DEGRADED,
                latency,
                f"{detail} — slow ({latency:.0f}ms > {self.latency_threshold_ms:.0f}ms)",
                cert_days,
            )
        return self._outcome(CheckState.UP, latency, detail, cert_days)

    async def _check_certificate(self) -> float | None:
        """Return cert days-remaining, or ``None`` when cert checks are off.

        A negative value means the certificate has already expired. A probe
        failure surfaces as ``0.0`` (treated as expired) so a broken cert never
        passes silently.
        """
        if self.cert_expiry_days is None or not self.target.lower().startswith("https"):
            return None
        parsed = urlparse(self.target)
        host = parsed.hostname
        if host is None:
            return None
        try:
            return await _cert_days_remaining(host, parsed.port or 443, self.timeout)
        except Exception:
            return 0.0
