"""Local system resource check (CPU / memory / disk)."""

from __future__ import annotations

from dataclasses import dataclass, field

import psutil

from .base import BaseCheck, CheckOutcome, CheckState


@dataclass
class SystemCheck(BaseCheck):
    """Sample local CPU, memory and disk usage and compare to thresholds.

    ``target`` is the filesystem path inspected for disk usage. The check is
    ``DEGRADED`` when any metric exceeds its threshold, ``UP`` otherwise.
    """

    cpu_threshold: float = 90.0
    memory_threshold: float = 90.0
    disk_threshold: float = 90.0

    check_type: str = field(default="system", init=False)

    async def run(self) -> CheckOutcome:
        # A short interval forces psutil to sample over a real window; with
        # ``interval=None`` the first call after process start always returns 0.0.
        cpu = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory().percent
        disk = psutil.disk_usage(self.target or "/").percent

        detail = f"cpu={cpu}% mem={memory}% disk={disk}%"
        breached = (
            cpu > self.cpu_threshold
            or memory > self.memory_threshold
            or disk > self.disk_threshold
        )
        state = CheckState.DEGRADED if breached else CheckState.UP
        return self._outcome(state, detail=detail)
