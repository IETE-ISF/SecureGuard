"""
water.py - REST routes for water monitoring (Phase 4, Task 4).

    GET /water/latest     most recent reading (optionally for one node)
    GET /water/readings   stored readings, filter by node and time range
    GET /water/summary    avg/min/max over a window (default: the last 24 hours)

Times are ISO 8601 and must include a timezone, e.g. 2026-10-08T10:00:00Z.
Routes only validate input and translate errors; queries live in
backend.services.water_queries.
"""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.database.base import utcnow
from backend.database.session import get_db
from backend.models.water import WaterReading
from backend.schemas.water import WaterReadingRead, WaterSummary
from backend.services import water_queries
from backend.services.device_service import DeviceError, DeviceNotFoundError

router = APIRouter(prefix="/water", tags=["water"])

DbSession = Annotated[Session, Depends(get_db)]
NodeId = Annotated[str | None, Query(description="Only this water node, e.g. WATER_01")]
StartTime = Annotated[
    datetime | None, Query(description="Window start, with timezone, e.g. 2026-10-08T10:00:00Z")
]
EndTime = Annotated[
    datetime | None, Query(description="Window end, with timezone, e.g. 2026-10-08T12:00:00Z")
]

DEFAULT_SUMMARY_WINDOW = timedelta(hours=24)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _validate_times(start: datetime | None, end: datetime | None) -> None:
    """Both times must carry a timezone, and start must not be after end."""
    for name, value in (("start", start), ("end", end)):
        if value is not None and value.tzinfo is None:
            raise _unprocessable(
                f"'{name}' must include a timezone, e.g. 2026-10-08T10:00:00Z"
            )
    if start is not None and end is not None and start > end:
        raise _unprocessable("'start' must not be after 'end'")


def _device_error(exc: DeviceError) -> HTTPException:
    if isinstance(exc, DeviceNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _unprocessable(str(exc))


@router.get("/latest", response_model=WaterReadingRead, summary="Latest water reading")
def latest_reading(db: DbSession, node_id: NodeId = None) -> WaterReading:
    """Return the most recent reading. Returns 404 if nothing has been stored yet."""
    try:
        reading = water_queries.get_latest_reading(db, node_id)
    except DeviceError as exc:
        raise _device_error(exc) from exc
    if reading is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No water readings stored yet"
        )
    return reading


@router.get("/readings", response_model=list[WaterReadingRead], summary="List water readings")
def list_water_readings(
    db: DbSession,
    node_id: NodeId = None,
    start: StartTime = None,
    end: EndTime = None,
    order: Annotated[Literal["desc", "asc"], Query(description="desc = newest first")] = "desc",
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[WaterReading]:
    """Return stored readings, newest first by default."""
    _validate_times(start, end)
    try:
        return water_queries.list_readings(
            db,
            node_id=node_id,
            start=start,
            end=end,
            newest_first=order == "desc",
            limit=limit,
            offset=offset,
        )
    except DeviceError as exc:
        raise _device_error(exc) from exc


@router.get("/summary", response_model=WaterSummary, summary="Summary over a time window")
def water_summary(
    db: DbSession,
    node_id: NodeId = None,
    start: StartTime = None,
    end: EndTime = None,
) -> WaterSummary:
    """Average, minimum and maximum of every metric. Defaults to the last 24 hours."""
    _validate_times(start, end)
    end = end or utcnow()
    start = start or end - DEFAULT_SUMMARY_WINDOW
    _validate_times(start, end)
    try:
        return water_queries.summarise_readings(db, start=start, end=end, node_id=node_id)
    except DeviceError as exc:
        raise _device_error(exc) from exc