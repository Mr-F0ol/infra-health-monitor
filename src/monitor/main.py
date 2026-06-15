"""FastAPI entrypoint — health checks, scheduling, metrics."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from .alerts import DiscordProvider, Notifier, TelegramProvider
from .alerts.base import AlertProvider
from .checks import BaseCheck, HttpCheck, SystemCheck, TcpCheck
from .config import settings
from .database import SessionLocal, get_session, init_db
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
        notifier = Notifier(redis_client, providers)

    scheduler = setup_scheduler(services, SessionLocal, notifier)
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


# ---------------------------------------------------------------------------
# Helpers shared between manual and scheduled checks
# ---------------------------------------------------------------------------


def _build_check(req: CheckRequest) -> BaseCheck:
    if req.type == "http":
        return HttpCheck(name=req.name, target=req.target, timeout=req.timeout)
    if req.type == "tcp":
        if req.port is None:
            raise ValueError("port is required for tcp checks")
        return TcpCheck(
            name=req.name, target=req.target, timeout=req.timeout, port=req.port
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


@app.get("/")
async def frontend() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/services", response_model=list[ServiceStatusResponse])
async def list_services(request: Request, session: SessionDep) -> list[ServiceStatusResponse]:
    result = []
    for svc in request.app.state.services:
        stmt = (
            select(CheckResult)
            .where(CheckResult.name == svc.name)
            .order_by(CheckResult.created_at.desc())
            .limit(1)
        )
        row = session.execute(stmt).scalar_one_or_none()
        result.append(
            ServiceStatusResponse(
                name=svc.name,
                type=svc.type,
                target=svc.target,
                port=svc.port,
                interval=svc.interval,
                status=row.status if row else "unknown",
                latency_ms=row.latency_ms if row else None,
                last_checked=row.created_at if row else None,
            )
        )
    return result


@app.get("/history", response_model=list[CheckResponse])
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


@app.post("/checks/run", response_model=CheckResponse)
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

    return CheckResponse(
        name=outcome.name,
        check_type=outcome.check_type,
        target=outcome.target,
        status=outcome.state.value,
        latency_ms=outcome.latency_ms,
        detail=outcome.detail,
    )


@app.get("/checks/results", response_model=list[CheckResponse])
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
