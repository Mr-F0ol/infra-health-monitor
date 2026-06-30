"""Discord webhook alert provider."""

from __future__ import annotations

import httpx


class DiscordProvider:
    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url

    async def send(self, message: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(self._url, json={"content": message})
            # Raise on 4xx/5xx so the Notifier logs a failed delivery instead of
            # silently dropping the alert (e.g. a wrong/expired webhook URL).
            response.raise_for_status()
