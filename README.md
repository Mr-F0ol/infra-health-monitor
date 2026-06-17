# infra-health-monitor

[![CI](https://github.com/Mr-F0ol/infra-health-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/Mr-F0ol/infra-health-monitor/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Mr-F0ol/infra-health-monitor/branch/main/graph/badge.svg)](https://codecov.io/gh/Mr-F0ol/infra-health-monitor)

A lightweight infrastructure health monitor that checks HTTP endpoints, TCP ports, and local system resources automatically — with scheduled monitoring, state-transition alerting, and Prometheus metrics.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  FastAPI  :8000                          │
│                                                         │
│  GET /health    GET /services    GET /history           │
│  GET /metrics   POST /checks/run                        │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  APScheduler — one job per service              │   │
│  │  run check → persist → metrics → alert          │   │
│  └─────────────────────────────────────────────────┘   │
└──────────┬───────────────┬───────────────┬─────────────┘
           │               │               │
      ┌────▼───┐     ┌─────▼────┐   ┌──────▼──────┐
      │Postgres│     │  Redis   │   │  /metrics   │
      │ results│     │  state   │   │  endpoint   │
      └────────┘     └──────────┘   └──────┬──────┘
                                           │  scrape
                                    ┌──────▼──────┐
                                    │ Prometheus  │
                                    └──────┬──────┘
                                           │
                                    ┌──────▼──────┐
                                    │   Grafana   │
                                    │   :3000     │
                                    └─────────────┘
```

## Stack

- **FastAPI** — HTTP API and metrics endpoint
- **APScheduler 3.x** — in-process job scheduler (one job per service)
- **SQLAlchemy 2.0** — persistence (SQLite by default, Postgres via Docker)
- **Redis** — last-known state per service for alert deduplication
- **prometheus-client** — Prometheus metrics exposition
- **httpx / psutil** — HTTP probing and system metrics

## Project layout

```
src/monitor/
├── config.py            # settings (pydantic-settings + .env)
├── models.py            # SQLAlchemy ORM
├── database.py          # engine / session
├── service_config.py    # services.yaml parsing
├── scheduler.py         # APScheduler setup
├── metrics.py           # Prometheus gauges / histograms / counters
├── alerts/
│   ├── base.py          # AlertProvider protocol
│   ├── discord.py       # Discord webhook
│   ├── telegram.py      # Telegram bot
│   └── notifier.py      # state-transition + Redis dedup
└── checks/
    ├── base.py          # BaseCheck + CheckState enum
    ├── http_check.py
    ├── tcp_check.py
    └── system_check.py
monitoring/
├── prometheus.yml
└── grafana/
    ├── provisioning/    # auto-wired datasource + dashboard
    └── dashboards/      # infra-monitor.json
```

## Quick start

```bash
cp .env.example .env
docker compose up -d
```

| Service | URL |
|---------|-----|
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Grafana | http://localhost:3000  (admin / admin) |
| Prometheus | http://localhost:9090 |

## Configure services

Edit `services.yaml` before starting:

```yaml
services:
  - name: my-api
    type: http
    target: https://api.example.com/health
    interval: 60          # seconds between checks

  - name: my-db
    type: tcp
    target: db.example.com
    port: 5432
    interval: 30

  - name: local-system
    type: system
    target: /             # filesystem path for disk check
    interval: 120
    thresholds:
      cpu: 85.0
      memory: 85.0
      disk: 90.0
```

## API reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Liveness probe |
| GET | `/services` | Current status of every configured service |
| GET | `/history?service=name` | Check history for one service |
| GET | `/metrics` | Prometheus exposition format |
| POST | `/checks/run` | Run a one-off check immediately |
| GET | `/checks/results` | Recent check results |

## Alerting

Set any of these in `.env` to enable that channel:

```env
MONITOR_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
MONITOR_TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
MONITOR_TELEGRAM_CHAT_ID=-100123456789
```

Alerts fire **only on state transitions** (UP → DOWN or DOWN → UP). Redis stores the last known state so repeated failures never spam you. Example:

```
🔴 [ALERT] my-api
Status: DOWN | Latency: N/A
Target: https://api.example.com/health
Time: 2026-06-15 14:32:01 UTC
```

### Anti-flapping

A service must fail `MONITOR_FAILURE_THRESHOLD` checks **in a row** (default: 3)
before a `DOWN`/`DEGRADED` alert fires, so a single transient blip never pages
you. Recovery (`UP`) is confirmed immediately. Raw results are always persisted
and exported to Prometheus — the threshold only gates *alerting*.

### Latency thresholds

Give an HTTP or TCP service a `latency_ms` threshold and a reachable-but-slow
response is reported as `DEGRADED`:

```yaml
services:
  - name: my-api
    type: http
    target: https://api.example.com/health
    interval: 60
    thresholds:
      latency_ms: 800     # 200 OK but slower than 800ms → DEGRADED
```

## Grafana dashboard

The dashboard is provisioned automatically from `monitoring/grafana/dashboards/infra-monitor.json` and includes:

- **Service Status** — current UP / DEGRADED / DOWN per service (colour-coded)
- **Latency p50 / p95** — time series of check latency in milliseconds
- **Checks per second** — rate of scheduled executions
- **Failures per second** — rate of failed checks
- **Uptime (last 1h)** — gauge showing availability percentage

## Development

```bash
pip install -e ".[dev]"

# run all tests
pytest

# with coverage
pytest --cov=monitor --cov-report=term-missing

# lint
ruff check src tests

# type-check
mypy src
```

## Database migrations

Schema is managed with **Alembic**. The default SQLite quick-start auto-creates
tables on boot; for Postgres / production, run migrations explicitly:

```bash
alembic upgrade head      # apply all migrations
alembic revision --autogenerate -m "describe change"   # after editing models
```

The Docker image runs `alembic upgrade head` automatically before starting the
API (see `docker-compose.yml`).

## Production notes

- **Hardening:** the bundled `docker-compose.yml` uses demo credentials
  (`monitor`/`monitor` for Postgres, `admin` for Grafana). Override
  `GRAFANA_ADMIN_PASSWORD` and the Postgres secrets, and put the API behind a
  reverse proxy with auth/TLS — `/services`, `/history` and `/metrics` are
  unauthenticated by design (intended for a private network or scrape target).
- **Container:** runs as a non-root user from a multi-stage image, with a
  `HEALTHCHECK` and CPU/memory limits.
- **Data retention:** check history is purged after `MONITOR_RETENTION_DAYS`
  (default 30; `0` keeps everything). Prometheus keeps 7 days of metrics.
- **High availability:** the scheduler runs **in-process**, so run a *single*
  instance. Two replicas would double every check and alert — there is no
  leader election. For HA, externalise the scheduler (e.g. APScheduler with a
  shared jobstore, or a dedicated worker) before scaling out.
- **Supply chain:** CI runs `pip-audit` on dependencies and Trivy on the built
  image.

## Technical decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Scheduler | APScheduler 3.x (in-process) | No broker needed; single process is enough at this scale. Celery would require a separate worker + broker. |
| ORM | SQLAlchemy sync | Check jobs are short-lived inserts; async ORM adds complexity with no throughput benefit here. |
| Alert dedup | Redis key per service | Simplest durable store for a single state value; avoids a message queue entirely. |
| Metrics | prometheus-client | Standard exposition format; the Prometheus + Grafana pair is the industry default for this use case. |
| Config split | YAML for services, `.env` for secrets | Services are structural config (version-controlled); credentials belong in the environment. |
