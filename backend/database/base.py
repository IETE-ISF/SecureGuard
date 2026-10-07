"""
base.py - SQLAlchemy declarative base and shared model building blocks
(Phase 1, Task 2).

Provides:
    Base            - declarative base class for every ORM model
    TimestampMixin  - created_at / updated_at columns
    UTCDateTime     - column type that stores UTC and returns aware datetimes
    utcnow()        - timezone-aware "now" in UTC

Usage:
    from backend.database.base import Base, TimestampMixin

    class Device(TimestampMixin, Base):
        __tablename__ = "devices"
        id: Mapped[int] = mapped_column(primary_key=True)
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, MetaData
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

# Deterministic constraint and index names, e.g. pk_devices, uq_devices_node_id.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator[datetime]):
    """
    DateTime column that always stores UTC and always returns aware datetimes.

    SQLite drops timezone information, so this type normalises values on the
    way in (converted to UTC) and restores tzinfo=UTC on the way out. Naive
    datetimes are rejected to prevent silent timezone bugs.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "Naive datetime is not allowed. Use a timezone-aware value, "
                "for example backend.database.base.utcnow()."
            )
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)

    def process_literal_param(self, value: Any, dialect: Dialect) -> str:
        return repr(value)


class Base(DeclarativeBase):
    """Declarative base class shared by every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {datetime: UTCDateTime}


class TimestampMixin:
    """Adds created_at and updated_at columns (both UTC, timezone-aware)."""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )