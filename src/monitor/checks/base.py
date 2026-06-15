"""Base check abstraction and shared result types."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum


class CheckState(StrEnum):
    """Health state of a single check."""

    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


@dataclass
class CheckOutcome:
    """The result of running a check once."""

    name: str
    check_type: str
    target: str
    state: CheckState
    latency_ms: float | None = None
    detail: str | None = None


@dataclass
class BaseCheck(ABC):
    """A named check against a target.

    Subclasses implement :meth:`run` and may use :meth:`_timed` to measure
    latency around the actual probe.
    """

    name: str
    target: str
    timeout: float = 5.0

    #: short identifier persisted with each result (e.g. "http", "tcp")
    check_type: str = field(default="base", init=False)

    @abstractmethod
    async def run(self) -> CheckOutcome:
        """Execute the check and return its outcome."""
        raise NotImplementedError

    def _outcome(
        self,
        state: CheckState,
        latency_ms: float | None = None,
        detail: str | None = None,
    ) -> CheckOutcome:
        return CheckOutcome(
            name=self.name,
            check_type=self.check_type,
            target=self.target,
            state=state,
            latency_ms=latency_ms,
            detail=detail,
        )

    @staticmethod
    def _now() -> float:
        return time.perf_counter()

    @staticmethod
    def _elapsed_ms(start: float) -> float:
        return round((time.perf_counter() - start) * 1000, 2)
