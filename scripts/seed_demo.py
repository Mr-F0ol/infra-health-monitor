"""Seed a demo database with a realistic fleet for the README screenshot.

Run with MONITOR_DATABASE_URL pointed at a throwaway sqlite file and
MONITOR_SERVICES_FILE pointed at the demo services file this script writes.
Not part of the app — a one-off tool for generating docs/dashboard.png.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete

from monitor.database import SessionLocal, init_db
from monitor.models import CheckResult

random.seed(7)

# (name, type, target, port, base_latency_ms, profile)
FLEET = [
    ("api-gateway", "http", "https://api.example.com/health", None, 46, "up"),
    ("web-frontend", "http", "https://app.example.com", None, 82, "up"),
    ("auth-service", "http", "https://auth.example.com/health", None, 118, "blip"),
    ("payments-api", "http", "https://pay.example.com/health", None, 910, "degraded"),
    ("postgres-primary", "tcp", "db.internal", 5432, 3, "up"),
    ("redis-cache", "tcp", "cache.internal", 6379, 1, "up"),
    ("worker-node", "system", "/", None, None, "up"),
]


def write_services_file(path: Path) -> None:
    lines = ["services:"]
    for name, typ, target, port, *_ in FLEET:
        lines.append(f"  - name: {name}")
        lines.append(f"    type: {typ}")
        lines.append(f"    target: {target}")
        if port:
            lines.append(f"    port: {port}")
        # Huge interval: the live scheduler must not fire and overwrite the
        # seeded demo data while we capture the screenshot.
        lines.append("    interval: 999999")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def seed() -> None:
    init_db()
    now = datetime.now(UTC)
    with SessionLocal() as session:
        session.execute(delete(CheckResult))
        for name, typ, _target, _port, base, profile in FLEET:
            for i in range(49, -1, -1):  # 50 points, oldest first
                ts = now - timedelta(minutes=i)
                status = "up"
                latency = None
                if base is not None:
                    latency = round(base * random.uniform(0.8, 1.25), 1)
                if profile == "degraded" and i < 12:
                    status = "degraded"
                    latency = round(base * random.uniform(1.05, 1.4), 1) if base else None
                elif profile == "blip" and i in (33, 34):
                    status = "down"
                    latency = None
                session.add(
                    CheckResult(
                        name=name,
                        check_type=typ,
                        target=_target,
                        status=status,
                        latency_ms=latency,
                        detail="HTTP 200" if typ == "http" and status != "down" else None,
                        created_at=ts,
                    )
                )
        session.commit()
    print("seeded", len(FLEET), "services")


if __name__ == "__main__":
    import sys

    write_services_file(Path(sys.argv[1]))
    seed()
