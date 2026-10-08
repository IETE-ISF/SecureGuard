"""
power_queries.py - Read-side queries for power readings (Phase 5, Task 2).

All functions take a Session. node_id is optional: leave it out to query
across every power node. A node_id must belong to a registered device of
type "power".
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from backend.models.device import DeviceType
from backend.models.power import PowerReading
from backend.schemas.power import PowerSummary
from backend.schemas.water import MetricStats
from backend.services.device_service import DeviceError, get_device


class NotAPowerNodeError(DeviceError):
    """Raised when a power query names a device that is not a power node."""

    def __init__(self, node_id: str) -> None:
        super().__init__(f"Device '{node_id}' is not a power node")
        self.node_id = node_id


_METRICS: dict[str, ColumnElement[Any]] = {
    "voltage": PowerReading.voltage,
    "current": PowerReading.current,
    "power": PowerReading.power,
    "energy": PowerReading.energy,
    "power_factor": PowerReading.power_factor,
}


def _resolve(db: Session, node_id: str | None) -> tuple[int | None, str | None]:
    """Return (device id, canonical node_id) for a power node, or (None, None) for all nodes."""
    if node_id is None:
        return None, None
    device = get_device(db, node_id)  # raises DeviceNotFoundError
    if device.node_type != DeviceType.POWER:
        raise NotAPowerNodeError(device.node_id)
    return device.id, device.node_id


def _scope(
    statement: Select[Any],
    device_id: int | None,
    start: datetime | None,
    end: datetime | None,
) -> Select[Any]:
    """Restrict a query to one device and/or a time range (both ends inclusive)."""
    if device_id is not None:
        statement = statement.where(PowerReading.device_id == device_id)
    if start is not None:
        statement = statement.where(PowerReading.recorded_at >= start)
    if end is not None:
        statement = statement.where(PowerReading.recorded_at <= end)
    return statement


def get_latest_reading(db: Session, node_id: str | None = None) -> PowerReading | None:
    """Return the most recent reading, or None if nothing is stored."""
    device_id, _ = _resolve(db, node_id)
    statement = _scope(select(PowerReading), device_id, None, None).order_by(
        PowerReading.recorded_at.desc(), PowerReading.id.desc()
    )
    return db.scalars(statement.limit(1)).first()


def list_readings(
    db: Session,
    *,
    node_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    newest_first: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[PowerReading]:
    """Return stored readings, filtered and paginated."""
    device_id, _ = _resolve(db, node_id)
    statement = _scope(select(PowerReading), device_id, start, end)
    if newest_first:
        statement = statement.order_by(PowerReading.recorded_at.desc(), PowerReading.id.desc())
    else:
        statement = statement.order_by(PowerReading.recorded_at.asc(), PowerReading.id.asc())
    return list(db.scalars(statement.limit(limit).offset(offset)).all())


def _round(value: Any) -> float | None:
    return None if value is None else round(float(value), 3)


def summarise_readings(
    db: Session,
    *,
    start: datetime,
    end: datetime,
    node_id: str | None = None,
) -> PowerSummary:
    """Return count, first/last timestamps and avg/min/max of every metric in the window."""
    device_id, canonical_id = _resolve(db, node_id)

    columns: list[Any] = [
        func.count(PowerReading.id),
        func.min(PowerReading.recorded_at),
        func.max(PowerReading.recorded_at),
    ]
    for expression in _METRICS.values():
        columns += [func.avg(expression), func.min(expression), func.max(expression)]

    row = db.execute(_scope(select(*columns), device_id, start, end)).one()

    stats: dict[str, MetricStats] = {}
    for position, name in enumerate(_METRICS):
        avg, low, high = row[3 + position * 3 : 6 + position * 3]
        stats[name] = MetricStats(avg=_round(avg), min=_round(low), max=_round(high))

    return PowerSummary(
        node_id=canonical_id,
        start=start,
        end=end,
        count=row[0],
        first_recorded_at=row[1],
        last_recorded_at=row[2],
        **stats,
    )
