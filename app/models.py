"""SQLAlchemy ORM models: targets, check_results and events."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base


def utc_now() -> datetime:
    """Current time in UTC (timezone-aware)."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """A datetime that is always stored as UTC and always comes back timezone-aware.

    SQLite has no timezone support and returns naive datetimes, which the API would
    then show without a timezone and clients would read as local time.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:  # treat naive values as UTC
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class Target(Base):
    """A website or server that the user has chosen to monitor."""

    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    host_or_url: Mapped[str] = mapped_column(String(2048))
    check_type: Mapped[str] = mapped_column(String(10), default="http")  # "http" or "ping"
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    warning_ms: Mapped[int] = mapped_column(Integer, default=500)
    critical_ms: Mapped[int] = mapped_column(Integer, default=1000)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    # Deleting a target also deletes its history and events.
    check_results: Mapped[list["CheckResult"]] = relationship(
        back_populates="target", cascade="all, delete-orphan", passive_deletes=True
    )
    events: Mapped[list["Event"]] = relationship(
        back_populates="target", cascade="all, delete-orphan", passive_deletes=True
    )


class CheckResult(Base):
    """The outcome of one check against a target."""

    __tablename__ = "check_results"
    # History and uptime queries always filter by target and sort or range by time.
    __table_args__ = (Index("ix_check_results_target_checked", "target_id", "checked_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id", ondelete="CASCADE"))
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    is_up: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)  # None if the check failed
    http_status: Mapped[int | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    target: Mapped["Target"] = relationship(back_populates="check_results")


class Event(Base):
    """A state change for a target (warning, critical or recovered)."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_target_occurred", "target_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id", ondelete="CASCADE"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    severity: Mapped[str] = mapped_column(String(10))  # "warning", "critical" or "recovered"
    message: Mapped[str] = mapped_column(String(500))
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)

    target: Mapped["Target"] = relationship(back_populates="events")
