# ---- build stage: install the package and its deps into an isolated prefix ----
FROM python:3.12-slim AS builder

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir --prefix=/install .

# ---- runtime stage: copy only what is needed, run as a non-root user ----
FROM python:3.12-slim

# Non-root user for the application.
RUN useradd --create-home --uid 1000 monitor

WORKDIR /app

COPY --from=builder /install /usr/local
COPY services.yaml alembic.ini ./
COPY migrations ./migrations

# /app must stay writable by the app user — needed when DATABASE_URL points
# at a local SQLite file (e.g. the default). Postgres-backed deployments
# don't touch this at all.
RUN chown -R monitor:monitor /app

USER monitor

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/health' % os.environ.get('PORT', '8000'))" || exit 1

# Respects $PORT when the host platform assigns one, else 8000.
CMD ["sh", "-c", "uvicorn monitor.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
