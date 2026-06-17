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

USER monitor

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "monitor.main:app", "--host", "0.0.0.0", "--port", "8000"]
