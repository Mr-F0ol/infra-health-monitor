"""APScheduler setup — one job per configured service."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete
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
        return HttpCheck(
            name=svc.name,
            target=svc.target,
            timeout=svc.timeout,
            latency_threshold_ms=svc.thresholds.latency_ms,
        )
    if svc.type == "tcp":
        return TcpCheck(
            name=svc.name,
            target=svc.target,
            timeout=svc.timeout,
            port=svc.port or 80,
            latency_threshold_ms=svc.thresholds.latency_ms,
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

    # Persistence is the least critical step: if the DB is momentarily
    # unreachable we still want the metric exported and the alert sent, so the
    # write is isolated and its failure never suppresses the rest.
    try:
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
    except Exception:
        logger.exception("failed to persist check result for %s", svc.name)

    record_outcome(outcome.name, outcome.check_type, outcome.state, outcome.latency_ms)

    if notifier is not None:
        await notifier.notify(outcome)

    logger.info(
        "checked %s → %s (%.1fms)",
        svc.name,
        outcome.state.value,
        outcome.latency_ms or 0.0,
    )


def _purge_old_results(
    session_factory: sessionmaker,  # type: ignore[type-arg]
    retention_days: int,
) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    with session_factory() as session:
        result = session.execute(
            delete(CheckResult).where(CheckResult.created_at < cutoff)
        )
        session.commit()
    logger.info("purged %d check results older than %d days", result.rowcount, retention_days)


def setup_scheduler(
    services: list[ServiceConfig],
    session_factory: sessionmaker,  # type: ignore[type-arg]
    notifier: Notifier | None = None,
    retention_days: int = 0,
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
    if retention_days > 0:
        # Fixed daily time rather than 24h-from-boot, so frequent restarts
        # (deploys, OOM) can't keep postponing the purge indefinitely.
        scheduler.add_job(
            _purge_old_results,
            trigger="cron",
            hour=3,
            args=[session_factory, retention_days],
            id="purge_old_results",
            replace_existing=True,
            max_instances=1,
        )
    return scheduler
