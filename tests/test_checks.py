"""Tests for the check implementations."""

import asyncio
import socket
from unittest.mock import AsyncMock, patch

from monitor.checks import CheckState, HttpCheck, SystemCheck, TcpCheck


def _free_port_with_listener() -> tuple[socket.socket, int]:
    """Start a listening socket on an ephemeral port and return it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


async def test_tcp_check_up_when_port_open():
    sock, port = _free_port_with_listener()
    try:
        check = TcpCheck(name="local", target="127.0.0.1", port=port, timeout=2.0)
        outcome = await check.run()
    finally:
        sock.close()

    assert outcome.state is CheckState.UP
    assert outcome.check_type == "tcp"
    assert outcome.latency_ms is not None


async def test_tcp_check_down_when_port_closed():
    # Bind then close to obtain a port that is (almost certainly) unused.
    sock, port = _free_port_with_listener()
    sock.close()

    check = TcpCheck(name="dead", target="127.0.0.1", port=port, timeout=1.0)
    outcome = await check.run()

    assert outcome.state is CheckState.DOWN


async def test_http_check_down_on_connection_error():
    # Port 1 is privileged and not serving HTTP -> connection refused.
    check = HttpCheck(name="bad", target="http://127.0.0.1:1", timeout=1.0)
    outcome = await check.run()

    assert outcome.state is CheckState.DOWN
    assert outcome.check_type == "http"


async def test_http_check_up_against_local_server():
    server = await asyncio.start_server(_http_ok_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async with server:
        await server.start_serving()
        check = HttpCheck(name="ok", target=f"http://127.0.0.1:{port}", timeout=2.0)
        outcome = await check.run()

    assert outcome.state is CheckState.UP
    assert outcome.detail == "HTTP 200"


async def _http_ok_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    await reader.read(1024)
    body = b"ok"
    writer.write(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Length: %d\r\n"
        b"Connection: close\r\n\r\n%s" % (len(body), body)
    )
    await writer.drain()
    writer.close()


async def test_http_check_degraded_when_latency_exceeds_threshold():
    server = await asyncio.start_server(_http_ok_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async with server:
        await server.start_serving()
        check = HttpCheck(
            name="slow",
            target=f"http://127.0.0.1:{port}",
            timeout=2.0,
            latency_threshold_ms=0.0001,
        )
        outcome = await check.run()

    assert outcome.state is CheckState.DEGRADED
    assert "slow" in (outcome.detail or "")


async def test_tcp_check_degraded_when_latency_exceeds_threshold():
    sock, port = _free_port_with_listener()
    try:
        check = TcpCheck(
            name="slow",
            target="127.0.0.1",
            port=port,
            timeout=2.0,
            latency_threshold_ms=0.0001,
        )
        outcome = await check.run()
    finally:
        sock.close()

    assert outcome.state is CheckState.DEGRADED


async def _run_http_with_cert_days(cert_days, *, threshold=14.0, latency_threshold=None):
    """Run an HttpCheck against a local 200 server with a stubbed cert probe."""
    server = await asyncio.start_server(_http_ok_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        await server.start_serving()
        check = HttpCheck(
            name="cert",
            target=f"http://127.0.0.1:{port}",
            timeout=2.0,
            cert_expiry_days=threshold,
            latency_threshold_ms=latency_threshold,
        )
        with patch.object(check, "_check_certificate", AsyncMock(return_value=cert_days)):
            return await check.run()


async def test_http_cert_far_from_expiry_is_up():
    outcome = await _run_http_with_cert_days(90.0)
    assert outcome.state is CheckState.UP
    assert outcome.cert_days_remaining == 90.0


async def test_http_cert_near_expiry_is_degraded():
    outcome = await _run_http_with_cert_days(3.0, threshold=14.0)
    assert outcome.state is CheckState.DEGRADED
    assert "cert expires in" in (outcome.detail or "")


async def test_http_cert_expired_is_down():
    outcome = await _run_http_with_cert_days(-1.0)
    assert outcome.state is CheckState.DOWN
    assert "cert expired" in (outcome.detail or "")


async def test_http_cert_check_skipped_for_plain_http():
    """A plain-HTTP target never triggers a cert probe even with a threshold."""
    server = await asyncio.start_server(_http_ok_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        await server.start_serving()
        check = HttpCheck(
            name="nocert",
            target=f"http://127.0.0.1:{port}",
            timeout=2.0,
            cert_expiry_days=14.0,
        )
        outcome = await check.run()
    assert outcome.state is CheckState.UP
    assert outcome.cert_days_remaining is None


async def test_http_cert_probe_failure_reported_as_expired():
    """If the TLS handshake fails, the check treats the cert as expired."""
    check = HttpCheck(
        name="bad",
        target="https://127.0.0.1:1",
        timeout=1.0,
        cert_expiry_days=14.0,
    )
    days = await check._check_certificate()
    assert days == 0.0


async def test_system_check_up_with_high_thresholds():
    check = SystemCheck(
        name="sys",
        target=".",
        cpu_threshold=100.0,
        memory_threshold=100.0,
        disk_threshold=100.0,
    )
    outcome = await check.run()

    assert outcome.state is CheckState.UP
    assert "cpu=" in (outcome.detail or "")


async def test_system_check_degraded_when_threshold_breached():
    check = SystemCheck(
        name="sys",
        target=".",
        cpu_threshold=-1.0,  # any usage exceeds this
        memory_threshold=100.0,
        disk_threshold=100.0,
    )
    outcome = await check.run()

    assert outcome.state is CheckState.DEGRADED
