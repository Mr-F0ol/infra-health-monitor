"""Service definitions loaded from a YAML config file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class ServiceThresholds(BaseModel):
    latency_ms: float | None = None
    # Days-remaining below which an HTTPS cert is reported DEGRADED (None = off).
    cert_expiry_days: float | None = None
    cpu: float = 90.0
    memory: float = 90.0
    disk: float = 90.0


class ServiceConfig(BaseModel):
    name: str
    type: Literal["http", "tcp", "system"]
    target: str
    port: int | None = None
    interval: int = Field(default=60, ge=1)
    timeout: float = Field(default=5.0, gt=0)
    thresholds: ServiceThresholds = Field(default_factory=ServiceThresholds)

    @model_validator(mode="after")
    def _tcp_requires_port(self) -> ServiceConfig:
        if self.type == "tcp" and self.port is None:
            raise ValueError(f"service '{self.name}': tcp checks require a port")
        return self


class _ServicesFile(BaseModel):
    services: list[ServiceConfig]

    @model_validator(mode="after")
    def _unique_names(self) -> _ServicesFile:
        names = [s.name for s in self.services]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate service names: {', '.join(dupes)}")
        return self


def load_services(path: str | Path) -> list[ServiceConfig]:
    """Parse a services YAML file and return validated service configs."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return _ServicesFile.model_validate(raw).services
