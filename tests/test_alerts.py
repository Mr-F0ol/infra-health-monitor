"""Tests for alert deduplication and provider mocking."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from monitor.alerts.discord import DiscordProvider
from monitor.alerts.notifier import Notifier
from monitor.alerts.telegram import TelegramProvider
from monitor.checks.base import CheckOutcome, CheckState


def _outcome(state: CheckState, latency_ms: float | None = None) -> CheckOutcome:
    return CheckOutcome(
        name="my-service",
        check_type="http",
        target="https://example.com",
        state=state,
        latency_ms=latency_ms,
    )


def _make_notifier(
    previous_raw: bytes | None,
    *providers,
    failure_threshold: int = 1,
    fail_count: int = 1,
):
    redis = AsyncMock()
    redis.get.return_value = previous_raw
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    redis.incr = AsyncMock(return_value=fail_count)
    notifier = Notifier(redis, list(providers), failure_threshold=failure_threshold)
    return notifier, redis


# ---------------------------------------------------------------------------
# Deduplication logic
# ---------------------------------------------------------------------------


async def test_notifier_alerts_on_healthy_to_down():
    provider = AsyncMock()
    notifier, redis = _make_notifier(b"up", provider)

    await notifier.notify(_outcome(CheckState.DOWN))

    provider.send.assert_awaited_once()
    redis.set.assert_awaited_once_with("monitor:state:my-service", "down")


async def test_notifier_no_alert_when_already_down():
    provider = AsyncMock()
    notifier, redis = _make_notifier(b"down", provider)

    await notifier.notify(_outcome(CheckState.DOWN))

    provider.send.assert_not_awaited()


async def test_notifier_alerts_on_recovery():
    provider = AsyncMock()
    notifier, redis = _make_notifier(b"down", provider)

    await notifier.notify(_outcome(CheckState.UP, latency_ms=55.0))

    provider.send.assert_awaited_once()
    msg = provider.send.call_args[0][0]
    assert "RECOVERED" in msg
    assert "my-service" in msg


async def test_notifier_no_alert_healthy_stays_healthy():
    provider = AsyncMock()
    notifier, redis = _make_notifier(b"up", provider)

    await notifier.notify(_outcome(CheckState.UP))

    provider.send.assert_not_awaited()


async def test_notifier_alerts_when_no_previous_state_and_down():
    """First check ever — no previous state — and service is down."""
    provider = AsyncMock()
    notifier, redis = _make_notifier(None, provider)

    await notifier.notify(_outcome(CheckState.DOWN))

    provider.send.assert_awaited_once()


async def test_notifier_no_alert_when_no_previous_state_and_up():
    """First check ever — service is healthy — no alert needed."""
    provider = AsyncMock()
    notifier, redis = _make_notifier(None, provider)

    await notifier.notify(_outcome(CheckState.UP))

    provider.send.assert_not_awaited()


async def test_notifier_silences_failing_provider():
    """A provider that raises must not crash the notifier."""
    bad_provider = AsyncMock()
    bad_provider.send.side_effect = Exception("network error")
    notifier, _ = _make_notifier(b"up", bad_provider)

    # Should not raise
    await notifier.notify(_outcome(CheckState.DOWN))


async def test_notifier_notifies_all_providers():
    p1, p2 = AsyncMock(), AsyncMock()
    notifier, _ = _make_notifier(b"up", p1, p2)

    await notifier.notify(_outcome(CheckState.DOWN))

    p1.send.assert_awaited_once()
    p2.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# Anti-flapping
# ---------------------------------------------------------------------------


async def test_notifier_suppresses_flap_below_threshold():
    """A single failure with threshold=3 must not alert."""
    provider = AsyncMock()
    notifier, redis = _make_notifier(None, provider, failure_threshold=3, fail_count=1)

    await notifier.notify(_outcome(CheckState.DOWN))

    provider.send.assert_not_awaited()
    redis.set.assert_not_awaited()  # state not confirmed yet


async def test_notifier_alerts_once_threshold_reached():
    """The third consecutive failure with threshold=3 confirms and alerts."""
    provider = AsyncMock()
    notifier, redis = _make_notifier(None, provider, failure_threshold=3, fail_count=3)

    await notifier.notify(_outcome(CheckState.DOWN))

    provider.send.assert_awaited_once()
    redis.set.assert_awaited_once_with("monitor:state:my-service", "down")


async def test_notifier_recovery_clears_failure_streak():
    provider = AsyncMock()
    notifier, redis = _make_notifier(b"down", provider, failure_threshold=3)

    await notifier.notify(_outcome(CheckState.UP, latency_ms=12.0))

    redis.delete.assert_awaited_once_with("monitor:fails:my-service")
    provider.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# Discord provider
# ---------------------------------------------------------------------------


async def test_discord_provider_posts_to_webhook():
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client

        provider = DiscordProvider("https://discord.com/api/webhooks/123/abc")
        await provider.send("Test alert")

    mock_client.post.assert_awaited_once()
    call_kwargs = mock_client.post.call_args
    assert "https://discord.com/api/webhooks/123/abc" in str(call_kwargs)
    assert "Test alert" in str(call_kwargs)


# ---------------------------------------------------------------------------
# Telegram provider
# ---------------------------------------------------------------------------


async def test_telegram_provider_posts_to_api():
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client

        provider = TelegramProvider("TOKEN123", "-100999")
        await provider.send("Test alert")

    mock_client.post.assert_awaited_once()
    call_kwargs = mock_client.post.call_args
    assert "TOKEN123" in str(call_kwargs)
    assert "-100999" in str(call_kwargs)
    assert "Test alert" in str(call_kwargs)
