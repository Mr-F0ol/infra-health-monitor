"""FastAPI entrypoint — health checks, scheduling, metrics."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session
from starlette.middleware.base import RequestResponseEndpoint

from .alerts import DiscordProvider, Notifier, TelegramProvider
from .alerts.base import AlertProvider
from .auth import require_auth
from .checks import BaseCheck, HttpCheck, SystemCheck, TcpCheck
from .config import settings
from .database import SessionLocal, get_session, init_db
from .leader import LeaderLock
from .logging_config import configure_logging, request_id_var
from .metrics import record_http_request, record_outcome
from .models import CheckResult
from .scheduler import reconcile_jobs, setup_scheduler
from .service_config import ServiceConfig, load_services
from .tracing import configure_tracing

# Configure logging once, at entrypoint import, before anything emits a record.
configure_logging(settings.log_level, settings.log_format)

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
    cert_expiry_days: float | None = Field(default=None, gt=0)


class CheckResponse(BaseModel):
    name: str
    check_type: str
    target: str
    status: str
    latency_ms: float | None = None
    detail: str | None = None
    checked_at: datetime | None = None


class ReloadResponse(BaseModel):
    services: int
    added: list[str]
    removed: list[str]
    updated: list[str]


class UptimeResponse(BaseModel):
    service: str
    window: str
    # Percentage of non-DOWN checks in the window; None when there's no data.
    uptime_pct: float | None = None
    total_checks: int = 0
    up_checks: int = 0


# Window label → lookback duration for the uptime aggregation.
_UPTIME_WINDOWS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


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


async def _leadership_loop(scheduler: object, leader: LeaderLock, ttl: int) -> None:
    """Resume the scheduler while this instance is leader, pause it otherwise.

    Runs forever (until cancelled at shutdown), re-acquiring or renewing the
    lock every ``ttl/2`` seconds so a dead leader is replaced within one TTL.
    """
    was_leader = False
    interval = max(1, ttl // 2)
    errors = 0
    while True:
        try:
            is_leader = await leader.acquire_or_renew()
            errors = 0
        except Exception:
            errors += 1
            # While Redis is unreachable no peer can acquire the lock either, so
            # hold leadership through a brief blip (up to one TTL) rather than
            # pausing the whole fleet on the first transient error.
            if was_leader and errors * interval < ttl:
                logger.warning("leader renew failed (attempt %d); holding leadership", errors)
                await asyncio.sleep(interval)
                continue
            logger.exception("leader election check failed; standing by")
            is_leader = False
        if is_leader and not was_leader:
            scheduler.resume()  # type: ignore[attr-defined]
            logger.info("acquired leadership (%s) — scheduler running", leader.instance_id)
        elif not is_leader and was_leader:
            scheduler.pause()  # type: ignore[attr-defined]
            logger.info("lost leadership — scheduler paused, standing by")
        was_leader = is_leader
        await asyncio.sleep(interval)


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

    # Redis is needed for alert dedup and/or HA leader election.
    redis_client: Redis[bytes] | None = None
    notifier: Notifier | None = None
    if providers or settings.ha_enabled:
        redis_client = Redis.from_url(settings.redis_url, decode_responses=False)
    if providers and redis_client is not None:
        notifier = Notifier(redis_client, providers, settings.failure_threshold)

    # Exposed to the readiness probe so it can verify Redis when in use.
    app.state.redis = redis_client

    scheduler = setup_scheduler(
        services, SessionLocal, notifier, retention_days=settings.retention_days
    )

    # With HA on, start paused and let the leadership loop resume us once this
    # instance wins the election — so standbys never run checks. Otherwise start
    # immediately so /reload can add the first jobs at runtime.
    ha_active = settings.ha_enabled and redis_client is not None
    scheduler.start(paused=ha_active)

    leader_task: asyncio.Task[None] | None = None
    leader: LeaderLock | None = None
    if ha_active and redis_client is not None:
        leader = LeaderLock(redis_client, ttl=settings.leader_ttl)
        leader_task = asyncio.create_task(
            _leadership_loop(scheduler, leader, settings.leader_ttl)
        )
        logger.info("HA enabled — instance %s competing for leadership", leader.instance_id)

    # Exposed to /reload so it can reconcile jobs without a restart.
    app.state.scheduler = scheduler
    app.state.notifier = notifier

    yield

    if leader_task is not None:
        leader_task.cancel()
        try:
            await leader_task
        except asyncio.CancelledError:
            pass
    if leader is not None:
        await leader.release()
    scheduler.shutdown(wait=False)
    if redis_client is not None:
        await redis_client.aclose()  # type: ignore[attr-defined]


_STATIC = Path(__file__).parent / "static"

app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


def _route_path(request: Request) -> str:
    """Matched route template (e.g. ``/history``), or a fixed label if unmatched.

    Using the template instead of ``request.url.path`` keeps metric cardinality
    bounded — query strings and path params never become distinct series.
    """
    route = request.scope.get("route")
    return str(getattr(route, "path", "") or "__unmatched__")


@app.middleware("http")
async def metrics_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Record request count + latency per route, exposed at ``/metrics``.

    The scrape endpoint itself is excluded so Prometheus polling doesn't inflate
    the API's own request metrics.
    """
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration = time.perf_counter() - start
        path = _route_path(request)
        if path != "/metrics":
            record_http_request(request.method, path, 500, duration)
        raise
    duration = time.perf_counter() - start
    path = _route_path(request)
    if path != "/metrics":
        record_http_request(request.method, path, response.status_code, duration)
    return response


_REQUEST_ID_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_request_id(value: str) -> str:
    """Strip anything but safe id chars (and cap length) before it hits logs."""
    cleaned = _REQUEST_ID_RE.sub("", value)[:64]
    return cleaned or uuid.uuid4().hex


@app.middleware("http")
async def correlation_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Attach a correlation id to every request — reused if the caller sent one.

    Stored in a context var so all logs emitted while handling the request carry
    it, and echoed back in the ``X-Request-ID`` response header for tracing. A
    caller-supplied id is sanitized to prevent log injection.
    """
    incoming = request.headers.get("x-request-id")
    rid = _sanitize_request_id(incoming) if incoming else uuid.uuid4().hex
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        request_id_var.reset(token)


_RATE_LIMIT_EXEMPT_PATHS = {"/health", "/ready", "/metrics"}
_rate_limit_window = 0
_rate_limit_counts: dict[str, int] = {}


@app.middleware("http")
async def rate_limit_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Per-IP fixed-window rate limit — basic defense against abuse/scraping.

    In-memory and per-instance: under ``MONITOR_HA_ENABLED`` each replica
    counts independently rather than sharing one global count. All counters
    reset together on each window boundary, so memory never grows unbounded.
    """
    if not settings.rate_limit_enabled or request.url.path in _RATE_LIMIT_EXEMPT_PATHS:
        return await call_next(request)

    global _rate_limit_window
    now = time.time()
    window = int(now // settings.rate_limit_window_seconds)
    if window != _rate_limit_window:
        _rate_limit_counts.clear()
        _rate_limit_window = window

    client_ip = request.client.host if request.client else "unknown"
    count = _rate_limit_counts.get(client_ip, 0) + 1
    _rate_limit_counts[client_ip] = count

    if count > settings.rate_limit_per_window:
        logger.warning("rate limit exceeded for %s on %s", client_ip, request.url.path)
        window_s = settings.rate_limit_window_seconds
        retry_after = window_s - int(now % window_s)
        return JSONResponse(
            {"detail": "rate limit exceeded, try again later"},
            status_code=429,
            headers={"Retry-After": str(max(retry_after, 1))},
        )

    return await call_next(request)


# Distributed tracing — no-op unless MONITOR_OTEL_ENABLED and the `otel` extra.
configure_tracing(app)

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
            cert_expiry_days=req.cert_expiry_days,
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


@app.get("/uptime", response_model=list[UptimeResponse], dependencies=[AuthDep])
async def get_uptime(
    request: Request,
    session: SessionDep,
    window: Literal["24h", "7d", "30d"] = Query(default="24h"),
    service: str | None = Query(default=None, description="Filter to one service"),
) -> list[UptimeResponse]:
    """Availability per service over a rolling window (non-DOWN ratio)."""
    services = request.app.state.services
    names = [svc.name for svc in services if service is None or svc.name == service]
    if not names:
        return []

    cutoff = datetime.now(UTC) - _UPTIME_WINDOWS[window]
    up_expr = func.sum(case((CheckResult.status != "down", 1), else_=0))
    stmt = (
        select(CheckResult.name, func.count().label("total"), up_expr.label("up"))
        .where(CheckResult.name.in_(names), CheckResult.created_at >= cutoff)
        .group_by(CheckResult.name)
    )
    stats = {name: (int(total), int(up or 0)) for name, total, up in session.execute(stmt)}

    result = []
    for name in names:
        total, up = stats.get(name, (0, 0))
        pct = round(100 * up / total, 3) if total else None
        result.append(
            UptimeResponse(
                service=name,
                window=window,
                uptime_pct=pct,
                total_checks=total,
                up_checks=up,
            )
        )
    return result


@app.post("/reload", response_model=ReloadResponse, dependencies=[AuthDep])
async def reload_services(request: Request) -> ReloadResponse:
    """Re-read services.yaml and reconcile scheduler jobs without a restart.

    On an invalid file we return 400 and leave the running jobs untouched.
    """
    try:
        services = load_services(settings.services_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f"services file not found: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid services file: {exc}") from exc

    diff = reconcile_jobs(
        request.app.state.scheduler,
        services,
        SessionLocal,
        request.app.state.notifier,
    )
    request.app.state.services = services
    logger.info("reloaded services: %s", diff)
    return ReloadResponse(services=len(services), **diff)


@app.get("/history", response_model=list[CheckResponse], dependencies=[AuthDep])
async def get_history(
    session: SessionDep,
    service: str = Query(..., description="Service name"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[CheckResponse]:
    stmt = (
        select(CheckResult)
        .where(CheckResult.name == service)
        .order_by(CheckResult.created_at.desc(), CheckResult.id.desc())
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
async def run_check(req: CheckRequest, request: Request, session: SessionDep) -> CheckResponse:
    # SSRF guard: only run a check that matches a service declared in
    # services.yaml, so a caller can never point the monitor at an arbitrary
    # host/port (the scheduled checks are operator-controlled; this endpoint is
    # the one place a target could otherwise be attacker-supplied).
    svc = next((s for s in request.app.state.services if s.name == req.name), None)
    if (
        svc is None
        or svc.type != req.type
        or svc.target != req.target
        or (svc.port or None) != (req.port or None)
    ):
        raise HTTPException(
            status_code=403, detail="checks can only be run for a configured service"
        )

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

    record_outcome(
        outcome.name,
        outcome.check_type,
        outcome.state,
        outcome.latency_ms,
        outcome.cert_days_remaining,
    )

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
