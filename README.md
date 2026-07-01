# infra-health-monitor

[![CI](https://github.com/Mr-F0ol/infra-health-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/Mr-F0ol/infra-health-monitor/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Mr-F0ol/infra-health-monitor/branch/main/graph/badge.svg)](https://codecov.io/gh/Mr-F0ol/infra-health-monitor)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Watches your websites, APIs and servers, and tells you the moment one goes down.

![Dashboard](docs/dashboard.png)

## Run it

```bash
cp .env.example .env
docker compose up -d
```

Open **http://localhost:8000** — that's your dashboard.

## Add a site to watch

Open [`services.yaml`](services.yaml) and add a block like this:

```yaml
services:
  - name: my-site
    type: http
    target: https://example.com
    interval: 60      # check every 60 seconds
```

Save the file and restart (`docker compose restart`) — your site now shows up on the dashboard.

You can also watch a port (`type: tcp`, e.g. a database) or the machine's own CPU/memory/disk (`type: system`) — see [Advanced](#advanced) for those examples.

## Get notified when something breaks

Add a Discord or Telegram target to `.env`:

```env
MONITOR_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
MONITOR_TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
MONITOR_TELEGRAM_CHAT_ID=-100123456789
```

Restart, and you'll get a message the moment a site goes down (and again when it recovers).

---

<details>
<summary id="advanced"><strong>Advanced / reference</strong> — API, auth, alerts detail, metrics, tracing, HA, production notes</summary>

### More check types

```yaml
services:
  - name: my-db
    type: tcp
    target: db.example.com
    port: 5432
    interval: 30

  - name: local-system            # monitors the machine the monitor runs on
    type: system
    target: /                     # filesystem path to check disk usage
    interval: 120
    thresholds:
      cpu: 85.0
      memory: 85.0
      disk: 90.0
```

Optional `thresholds` turn a healthy-but-slow or soon-to-expire service into `DEGRADED` instead of `UP`/`DOWN`:

```yaml
services:
  - name: my-api
    type: http
    target: https://api.example.com
    interval: 60
    thresholds:
      latency_ms: 800          # 200 OK but slower than 800ms → DEGRADED
      cert_expiry_days: 14     # DEGRADED when the TLS cert expires in < 14 days, DOWN if already expired
```

### API

Everything the dashboard shows is also available as JSON:

| Method | Endpoint | What it does |
|---|---|---|
| GET | `/services` | Current status of every service |
| GET | `/history?service=name` | Check history for one service |
| GET | `/uptime?window=24h\|7d\|30d` | Availability % per service |
| POST | `/checks/run` | Run a check right now |
| GET | `/checks/results` | Recent check results |
| POST | `/reload` | Re-read `services.yaml` live, no restart needed |
| GET | `/health` / `/ready` | Liveness / readiness (for orchestrators) |
| GET | `/metrics` | Prometheus metrics |

```bash
curl -H "X-API-Key: $MONITOR_API_KEY" http://localhost:8000/services
```

`/reload` diffs `services.yaml` against the running scheduler — new services get a job, removed ones are unscheduled, changed ones are replaced, everything else keeps running undisturbed.

### Authentication

The API is open by default. To lock it down, set either or both in `.env`:

```env
MONITOR_API_KEY=long-random-string          # for scripts: header X-API-Key
MONITOR_BASIC_AUTH_USER=admin               # for the browser dashboard
MONITOR_BASIC_AUTH_PASSWORD=change-me
```

`/health`, `/ready` and `/metrics` always stay open so orchestrators and Prometheus can reach them.

### Rate limiting

On by default — each client IP gets 100 requests per 60-second window before getting a `429`. `/health`, `/ready` and `/metrics` are always exempt. Tune or disable it in `.env`:

```env
MONITOR_RATE_LIMIT_ENABLED=true
MONITOR_RATE_LIMIT_PER_WINDOW=100
MONITOR_RATE_LIMIT_WINDOW_SECONDS=60
```

It's in-memory and per-instance — under `MONITOR_HA_ENABLED` each replica counts independently rather than sharing one global limit.

### How alerting works

Alerts fire only when a service **changes state** (e.g. UP → DOWN), so it won't spam you on every failed check. A service also needs to fail `MONITOR_FAILURE_THRESHOLD` checks in a row (default 3) before an alert fires — one slow blip won't page you. Redis stores the last known state per service for this.

### Architecture

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

**Stack:** FastAPI (API + metrics endpoint) · APScheduler 3.x (in-process, one job per service) · SQLAlchemy 2.0 (SQLite by default, Postgres via Docker) · Redis (alert dedup + optional HA lock) · prometheus-client · httpx / psutil for probing.

### Project layout

```
src/monitor/
├── main.py              # FastAPI app, routes, lifespan, middleware
├── config.py            # settings (pydantic-settings + .env)
├── auth.py              # optional API key / Basic auth
├── models.py            # SQLAlchemy ORM
├── database.py          # engine / session
├── service_config.py    # services.yaml parsing
├── scheduler.py         # APScheduler setup
├── metrics.py           # Prometheus gauges / histograms / counters
├── leader.py            # Redis leader lock (opt-in HA)
├── logging_config.py    # structured (JSON) logging + correlation ids
├── tracing.py           # OpenTelemetry setup (opt-in)
├── static/
│   └── index.html       # built-in dashboard (single file, no build step)
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

migrations/               # Alembic schema migrations
monitoring/
├── prometheus.yml
├── alert_rules.yml
├── alertmanager.yml
└── grafana/
    ├── provisioning/    # auto-wired datasource + dashboard
    └── dashboards/      # infra-monitor.json
tests/                    # pytest, one file per module roughly
scripts/
└── seed_demo.py          # one-off: seeds demo data for the README screenshot
docs/                     # README image + how it's regenerated
.github/workflows/ci.yml  # lint, type-check, test, audit, docker build+scan
```

### Logging

```env
MONITOR_LOG_LEVEL=INFO     # DEBUG | INFO | WARNING | ERROR
MONITOR_LOG_FORMAT=json    # text (default, local dev) | json (structured, Docker default)
```

Each JSON line carries `timestamp`, `level`, `logger`, `message`, `request_id`, and any structured `extra=` fields:

```json
{"timestamp":"2026-06-25T14:32:01.123+00:00","level":"INFO","logger":"monitor.scheduler","message":"checked my-api → up (42.0ms)","request_id":"-"}
```

Every HTTP request gets a correlation id (from an inbound `X-Request-ID` header, or generated) that's echoed in the response header and attached to every log line for that request (`request_id` field) — so one request can be traced end-to-end. Background scheduler logs use `-`. Uvicorn's own access logs keep their default format.

### Tracing

Opt-in distributed tracing via OpenTelemetry:

```bash
pip install '.[otel]'
```

```env
MONITOR_OTEL_ENABLED=true
MONITOR_OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
MONITOR_OTEL_SERVICE_NAME=infra-health-monitor
```

FastAPI requests get auto-instrumented and spans export via OTLP/gRPC to Tempo/Jaeger/etc. The OTel packages stay out of the default install so a standard deployment remains lean.

### Prometheus alerting (watches the watcher)

Discord/Telegram alerts can't fire if the monitor process itself dies — Prometheus + Alertmanager close that gap independently:

- **`MonitorDown`** — `up == 0` for 1m (a deadman's switch: fires when Prometheus can't scrape the monitor at all).
- **`ServiceDown`** — a service reports `DOWN` for 2m.
- **`ServiceDegraded`** — a service reports `DEGRADED` for 5m.

Rules live in `monitoring/alert_rules.yml`, routing in `monitoring/alertmanager.yml`. The default receiver has no integrations, so alerts show up in the Alertmanager UI (http://localhost:9093) out of the box — add a Slack/webhook/PagerDuty config there to route them out.

### Uptime / SLA

```bash
curl -H "X-API-Key: $MONITOR_API_KEY" "http://localhost:8000/uptime?window=7d"
# [{"service":"my-api","window":"7d","uptime_pct":99.8,"total_checks":10080,"up_checks":10060}]
```

Every non-`DOWN` check counts as available (a `DEGRADED` service was still reachable), matching `ServiceDown`'s outage semantics. The dashboard's "Overall uptime" indicator reads this endpoint (24h). No checks in the window → `uptime_pct: null`.

### Exposed metrics

`GET /metrics` covers both the monitored services and the API itself:

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `monitor_service_status` | gauge | `service`, `type` | 1=up, 0=degraded, -1=down |
| `monitor_check_latency_ms` | histogram | `service`, `type` | Check latency |
| `monitor_checks_total` | counter | `service`, `type` | Checks executed |
| `monitor_checks_failed_total` | counter | `service`, `type` | Down/degraded checks |
| `monitor_cert_expiry_days` | gauge | `service` | Days until TLS cert expiry |
| `monitor_http_requests_total` | counter | `method`, `path`, `status` | API requests handled |
| `monitor_http_request_duration_seconds` | histogram | `method`, `path` | API request latency |

HTTP metrics are labelled by **route template** (e.g. `/history`), never the raw path, so cardinality stays bounded. `/metrics` is excluded from its own counters.

### Grafana dashboard

Auto-provisioned from `monitoring/grafana/dashboards/infra-monitor.json`:

- **Service Status** — current UP / DEGRADED / DOWN per service (colour-coded)
- **Latency p50 / p95** — time series of check latency
- **Checks per second** / **Failures per second**
- **Uptime (last 1h)** — gauge

### Development

```bash
pip install -e ".[dev]"

pytest                                        # run all tests
pytest --cov=monitor --cov-report=term-missing  # with coverage
ruff check src tests                          # lint
mypy src                                      # type-check
```

### Database migrations

Schema is managed with **Alembic**. The SQLite quick-start auto-creates tables on boot; for Postgres/production, run migrations explicitly:

```bash
alembic upgrade head
alembic revision --autogenerate -m "describe change"   # after editing models
```

The Docker image runs `alembic upgrade head` automatically before starting the API.

### Production notes

- **Hardening:** Postgres and Redis are **not published to the host** — reachable only over the internal compose network, and Redis runs with `--requirepass`. Default credentials are demo values, fully overridable via `.env` (`POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`, `REDIS_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`) — **change them for anything public.** Put the API behind a reverse proxy with auth/TLS: `/services`, `/history`, `/metrics` are unauthenticated by design (meant for a private network or scrape target). Per-IP rate limiting is on by default (see [Rate limiting](#rate-limiting)).
- **Container:** non-root user, multi-stage image, `HEALTHCHECK`, CPU/memory limits.
- **Data retention:** check history is purged after `MONITOR_RETENTION_DAYS` (default 30; `0` keeps everything). Prometheus keeps 7 days of metrics.
- **High availability:** the scheduler runs in-process — by default, run a *single* instance (two replicas would double every check and alert). Set `MONITOR_HA_ENABLED=true` (requires Redis) to run multiple replicas safely: they elect one leader via a Redis lock, only the leader runs checks, and standbys take over within one `MONITOR_LEADER_TTL` (default 30s) if the leader dies.
- **Supply chain:** CI runs `pip-audit` on dependencies and Trivy on the built image.

### Technical decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Scheduler | APScheduler 3.x (in-process) | No broker needed; single process is enough at this scale. Celery would require a separate worker + broker. |
| ORM | SQLAlchemy sync | Check jobs are short-lived inserts; async ORM adds complexity with no throughput benefit here. |
| Alert dedup | Redis key per service | Simplest durable store for a single state value; avoids a message queue entirely. |
| HA model | Redis leader lock (opt-in) | One active replica + warm standbys without a broker or shared jobstore; the lock alone prevents double-execution. |
| Metrics | prometheus-client | Standard exposition format; the Prometheus + Grafana pair is the industry default for this use case. |
| Config split | YAML for services, `.env` for secrets | Services are structural config (version-controlled); credentials belong in the environment. |

</details>
