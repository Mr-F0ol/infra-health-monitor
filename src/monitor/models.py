"""SQLAlchemy ORM models."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CheckResult(Base):
    """A single recorded outcome of running a check."""

    __tablename__ = "check_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    check_type: Mapped[str] = mapped_column(String(40), index=True)
    target: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), index=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<CheckResult {self.name} {self.check_type} "
            f"{self.status} {self.latency_ms}ms>"
        )
