"""APScheduler setup — one job per configured service."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import sessionmaker

from .alerts.notifier import Notifier
from .checks import HttpCheck, SystemCheck, TcpCheck
from .checks.base import BaseCheck
from .metrics import record_outcome
from .models import CheckResult
from .service_config import ServiceConfig

logger = logging.getLogger(__name__)


def _build_check(svc: ServiceConfig) -> BaseCheck:
    if svc.type == "http":
        return HttpCheck(name=svc.name, target=svc.target, timeout=svc.timeout)
    if svc.type == "tcp":
        return TcpCheck(
            name=svc.name,
            target=svc.target,
            timeout=svc.timeout,
            port=svc.port or 80,
        )
    return SystemCheck(
        name=svc.name,
        target=svc.target,
        timeout=svc.timeout,
        cpu_threshold=svc.thresholds.cpu,
        memory_threshold=svc.thresholds.memory,
        disk_threshold=svc.thresholds.disk,
    )


async def _run_service_job(
    svc: ServiceConfig,
    session_factory: sessionmaker,  # type: ignore[type-arg]
    notifier: Notifier | None,
) -> None:
    check = _build_check(svc)
    outcome = await check.run()

    with session_factory() as session:
        session.add(
            CheckResult(
                name=outcome.name,
                check_type=outcome.check_type,
                target=outcome.target,
                status=outcome.state.value,
                latency_ms=outcome.latency_ms,
                detail=outcome.detail,
            )
        )
        session.commit()

    record_outcome(outcome.name, outcome.check_type, outcome.state, outcome.latency_ms)

    if notifier is not None:
        await notifier.notify(outcome)

    logger.info(
        "checked %s → %s (%.1fms)",
        svc.name,
        outcome.state.value,
        outcome.latency_ms or 0.0,
    )


def setup_scheduler(
    services: list[ServiceConfig],
    session_factory: sessionmaker,  # type: ignore[type-arg]
    notifier: Notifier | None = None,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    for svc in services:
        scheduler.add_job(
            _run_service_job,
            trigger="interval",
            seconds=svc.interval,
            args=[svc, session_factory, notifier],
            id=f"check_{svc.name}",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=30,
        )
    return scheduler
