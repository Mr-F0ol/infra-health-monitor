"""Discord webhook alert provider."""

from __future__ import annotations

import httpx


class DiscordProvider:
    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url

    async def send(self, message: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(self._url, json={"content": message})
