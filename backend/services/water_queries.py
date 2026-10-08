"""
water_queries.py - Read-side queries for water readings (Phase 4, Task 4).

All functions take a Session. node_id is optional: leave it out to query
across every water node. A node_id must belong to a registered device of
type "water".
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from backend.models.device import DeviceType
from backend.models.water import WaterReading
from backend.schemas.water import MetricStats, WaterSummary
from backend.services.device_service import DeviceError, get_device


class NotAWaterNodeError(DeviceError):
    """Raised when a water query names a device that is not a water node."""

    def __init__(self, node_id: str) -> None:
        super().__init__(f"Device '{node_id}' is not a water node")
        self.node_id = node_id


# Metrics summarised by summarise_readings(); consumption and delta_temp are derived.
_METRICS: dict[str, ColumnElement[Any]] = {
    "flow_in": WaterReading.flow_in,
    "flow_out": WaterReading.flow_out,
    "consumption": WaterReading.flow_in - WaterReading.flow_out,
    "temp_in": WaterReading.temp_in,
    "temp_out": WaterReading.temp_out,
    "delta_temp": WaterReading.temp_out - WaterReading.temp_in,
    "humidity": WaterReading.humidity,
    "pressure": WaterReading.pressure,
}


def _resolve(db: Session, node_id: str | None) -> tuple[int | None, str | None]:
    """Return (device id, canonical node_id) for a water node, or (None, None) for all nodes."""
    if node_id is None:
        return None, None
    device = get_device(db, node_id)  # raises DeviceNotFoundError
    if device.node_type != DeviceType.WATER:
        raise NotAWaterNodeError(device.node_id)
    return device.id, device.node_id


def _scope(
    statement: Select[Any],
    device_id: int | None,
    start: datetime | None,
    end: datetime | None,
) -> Select[Any]:
    """Restrict a query to one device and/or a time range (both ends inclusive)."""
    if device_id is not None:
        statement = statement.where(WaterReading.device_id == device_id)
    if start is not None:
        statement = statement.where(WaterReading.recorded_at >= start)
    if end is not None:
        statement = statement.where(WaterReading.recorded_at <= end)
    return statement


def get_latest_reading(db: Session, node_id: str | None = None) -> WaterReading | None:
    """Return the most recent reading, or None if nothing is stored."""
    device_id, _ = _resolve(db, node_id)
    statement = _scope(select(WaterReading), device_id, None, None).order_by(
        WaterReading.recorded_at.desc(), WaterReading.id.desc()
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
) -> list[WaterReading]:
    """Return stored readings, filtered and paginated."""
    device_id, _ = _resolve(db, node_id)
    statement = _scope(select(WaterReading), device_id, start, end)
    if newest_first:
        statement = statement.order_by(WaterReading.recorded_at.desc(), WaterReading.id.desc())
    else:
        statement = statement.order_by(WaterReading.recorded_at.asc(), WaterReading.id.asc())
    return list(db.scalars(statement.limit(limit).offset(offset)).all())


def _round(value: Any) -> float | None:
    return None if value is None else round(float(value), 3)


def summarise_readings(
    db: Session,
    *,
    start: datetime,
    end: datetime,
    node_id: str | None = None,
) -> WaterSummary:
    """Return count, first/last timestamps and avg/min/max of every metric in the window."""
    device_id, canonical_id = _resolve(db, node_id)

    columns: list[Any] = [
        func.count(WaterReading.id),
        func.min(WaterReading.recorded_at),
        func.max(WaterReading.recorded_at),
    ]
    for expression in _METRICS.values():
        columns += [func.avg(expression), func.min(expression), func.max(expression)]

    row = db.execute(_scope(select(*columns), device_id, start, end)).one()

    stats: dict[str, MetricStats] = {}
    for position, name in enumerate(_METRICS):
        avg, low, high = row[3 + position * 3 : 6 + position * 3]
        stats[name] = MetricStats(avg=_round(avg), min=_round(low), max=_round(high))

    return WaterSummary(
        node_id=canonical_id,
        start=start,
        end=end,
        count=row[0],
        first_recorded_at=row[1],
        last_recorded_at=row[2],
        **stats,
    )