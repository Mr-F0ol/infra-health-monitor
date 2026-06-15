"""Tests for the check implementations."""

import asyncio
import socket

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
