"""Common protocol that every alert provider must satisfy."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AlertProvider(Protocol):
    async def send(self, message: str) -> None: ...
