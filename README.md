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

## Technical decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Scheduler | APScheduler 3.x (in-process) | No broker needed; single process is enough at this scale. Celery would require a separate worker + broker. |
| ORM | SQLAlchemy sync | Check jobs are short-lived inserts; async ORM adds complexity with no throughput benefit here. |
| Alert dedup | Redis key per service | Simplest durable store for a single state value; avoids a message queue entirely. |
| Metrics | prometheus-client | Standard exposition format; the Prometheus + Grafana pair is the industry default for this use case. |
| Config split | YAML for services, `.env` for secrets | Services are structural config (version-controlled); credentials belong in the environment. |
