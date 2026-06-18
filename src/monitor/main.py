"""FastAPI entrypoint — health checks, scheduling, metrics."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .alerts import DiscordProvider, Notifier, TelegramProvider
from .alerts.base import AlertProvider
from .auth import require_auth
from .checks import BaseCheck, HttpCheck, SystemCheck, TcpCheck
from .config import settings
from .database import SessionLocal, get_session, init_db
from .metrics import record_outcome
from .models import CheckResult
from .scheduler import setup_scheduler
from .service_config import ServiceConfig, load_services

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class CheckRequest(BaseModel):
    name: str
    type: Literal["http", "tcp", "system"]
    target: str
    port: int | None = Field(default=None, ge=1, le=65535)
    timeout: float = Field(default_factory=lambda: settings.default_timeout, gt=0)
    latency_threshold_ms: float | None = Field(default=None, gt=0)


class CheckResponse(BaseModel):
    name: str
    check_type: str
    target: str
    status: str
    latency_ms: float | None = None
    detail: str | None = None
    checked_at: datetime | None = None


class ServiceStatusResponse(BaseModel):
    name: str
    type: str
    target: str
    port: int | None = None
    interval: int
    status: str
    latency_ms: float | None = None
    last_checked: datetime | None = None


# ---------------------------------------------------------------------------
# Lifespan — scheduler + alert providers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()

    services: list[ServiceConfig] = []
    try:
        services = load_services(settings.services_file)
        logger.info("loaded %d services from %s", len(services), settings.services_file)
    except FileNotFoundError:
        logger.warning("services file not found: %s — scheduler disabled", settings.services_file)

    app.state.services = services

    # Build alert providers from env config
    providers: list[AlertProvider] = []
    if settings.discord_webhook_url:
        providers.append(DiscordProvider(settings.discord_webhook_url))
    if settings.telegram_bot_token and settings.telegram_chat_id:
        providers.append(TelegramProvider(settings.telegram_bot_token, settings.telegram_chat_id))

    redis_client: Redis[bytes] | None = None
    notifier: Notifier | None = None
    if providers:
        redis_client = Redis.from_url(settings.redis_url, decode_responses=False)
        notifier = Notifier(redis_client, providers, settings.failure_threshold)

    # Exposed to the readiness probe so it can verify Redis when alerting is on.
    app.state.redis = redis_client

    scheduler = setup_scheduler(
        services, SessionLocal, notifier, retention_days=settings.retention_days
    )
    if services:
        scheduler.start()

    yield

    if services:
        scheduler.shutdown(wait=False)
    if redis_client is not None:
        await redis_client.aclose()  # type: ignore[attr-defined]


_STATIC = Path(__file__).parent / "static"

app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_STATIC), name="static")

SessionDep = Annotated[Session, Depends(get_session)]

# Applied to endpoints that expose monitoring data or trigger work. Liveness,
# readiness and the metrics scrape target stay open for orchestrators/Prometheus.
AuthDep = Depends(require_auth)


# ---------------------------------------------------------------------------
# Helpers shared between manual and scheduled checks
# ---------------------------------------------------------------------------


def _build_check(req: CheckRequest) -> BaseCheck:
    if req.type == "http":
        return HttpCheck(
            name=req.name,
            target=req.target,
            timeout=req.timeout,
            latency_threshold_ms=req.latency_threshold_ms,
        )
    if req.type == "tcp":
        if req.port is None:
            raise ValueError("port is required for tcp checks")
        return TcpCheck(
            name=req.name,
            target=req.target,
            timeout=req.timeout,
            port=req.port,
            latency_threshold_ms=req.latency_threshold_ms,
        )
    return SystemCheck(
        name=req.name,
        target=req.target,
        timeout=req.timeout,
        cpu_threshold=settings.cpu_threshold,
        memory_threshold=settings.memory_threshold,
        disk_threshold=settings.disk_threshold,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/", dependencies=[AuthDep])
async def frontend() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness — the process is up and serving. Cheap and dependency-free."""
    return {"status": "ok"}


@app.get("/ready")
async def ready(request: Request, session: SessionDep) -> JSONResponse:
    """Readiness — verifies the backing stores the monitor actually needs.

    Returns 503 if the database is unreachable (or Redis, when alerting is
    enabled), so a load balancer / orchestrator can stop routing to an instance
    that is up but unable to persist results.
    """
    checks: dict[str, str] = {}

    try:
        session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    redis_client: Redis[bytes] | None = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        try:
            await redis_client.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {exc}"

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"ready": healthy, "checks": checks},
        status_code=200 if healthy else 503,
    )


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/services", response_model=list[ServiceStatusResponse], dependencies=[AuthDep])
async def list_services(request: Request, session: SessionDep) -> list[ServiceStatusResponse]:
    services = request.app.state.services
    names = [svc.name for svc in services]

    # Latest result per service in a single round-trip: find the max timestamp
    # per name, then join back to fetch the corresponding rows.
    latest: dict[str, CheckResult] = {}
    if names:
        newest = (
            select(
                CheckResult.name,
                func.max(CheckResult.created_at).label("created_at"),
            )
            .where(CheckResult.name.in_(names))
            .group_by(CheckResult.name)
            .subquery()
        )
        stmt = select(CheckResult).join(
            newest,
            (CheckResult.name == newest.c.name)
            & (CheckResult.created_at == newest.c.created_at),
        )
        for row in session.execute(stmt).scalars():
            latest[row.name] = row

    result = []
    for svc in services:
        last = latest.get(svc.name)
        result.append(
            ServiceStatusResponse(
                name=svc.name,
                type=svc.type,
                target=svc.target,
                port=svc.port,
                interval=svc.interval,
                status=last.status if last else "unknown",
                latency_ms=last.latency_ms if last else None,
                last_checked=last.created_at if last else None,
            )
        )
    return result


@app.get("/history", response_model=list[CheckResponse], dependencies=[AuthDep])
async def get_history(
    session: SessionDep,
    service: str = Query(..., description="Service name"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[CheckResponse]:
    stmt = (
        select(CheckResult)
        .where(CheckResult.name == service)
        .order_by(CheckResult.created_at.desc())
        .limit(limit)
    )
    rows = session.execute(stmt).scalars().all()
    return [
        CheckResponse(
            name=r.name,
            check_type=r.check_type,
            target=r.target,
            status=r.status,
            latency_ms=r.latency_ms,
            detail=r.detail,
            checked_at=r.created_at,
        )
        for r in rows
    ]


@app.post("/checks/run", response_model=CheckResponse, dependencies=[AuthDep])
async def run_check(req: CheckRequest, session: SessionDep) -> CheckResponse:
    check = _build_check(req)
    outcome = await check.run()

    row = CheckResult(
        name=outcome.name,
        check_type=outcome.check_type,
        target=outcome.target,
        status=outcome.state.value,
        latency_ms=outcome.latency_ms,
        detail=outcome.detail,
    )
    session.add(row)
    session.commit()

    record_outcome(outcome.name, outcome.check_type, outcome.state, outcome.latency_ms)

    return CheckResponse(
        name=outcome.name,
        check_type=outcome.check_type,
        target=outcome.target,
        status=outcome.state.value,
        latency_ms=outcome.latency_ms,
        detail=outcome.detail,
    )


@app.get("/checks/results", response_model=list[CheckResponse], dependencies=[AuthDep])
async def list_results(session: SessionDep, limit: int = 50) -> list[CheckResponse]:
    stmt = select(CheckResult).order_by(CheckResult.created_at.desc()).limit(limit)
    rows = session.execute(stmt).scalars().all()
    return [
        CheckResponse(
            name=r.name,
            check_type=r.check_type,
            target=r.target,
            status=r.status,
            latency_ms=r.latency_ms,
            detail=r.detail,
            checked_at=r.created_at,
        )
        for r in rows
    ]
