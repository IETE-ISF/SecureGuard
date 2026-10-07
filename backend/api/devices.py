"""
devices.py - REST routes for the device registry (Phase 2, Task 4).

    POST   /devices              register a device
    GET    /devices              list devices (filter by type/status, paginate)
    GET    /devices/{node_id}    read one device
    PATCH  /devices/{node_id}    rename a device or change its status
    DELETE /devices/{node_id}    delete a device

Routes only validate input and translate service errors into HTTP
responses. All logic lives in backend.services.device_service.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.database.session import get_db
from backend.models.device import Device, DeviceStatus, DeviceType
from backend.schemas.device import DeviceCreate, DeviceRead, DeviceUpdate
from backend.services import device_service
from backend.services.device_service import (
    DeviceAlreadyExistsError,
    DeviceNotFoundError,
)

router = APIRouter(prefix="/devices", tags=["devices"])

DbSession = Annotated[Session, Depends(get_db)]


def _not_found(exc: DeviceNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "",
    response_model=DeviceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a device",
)
def create_device(payload: DeviceCreate, db: DbSession) -> Device:
    """Register a new device. Returns 409 if the node_id is already taken."""
    try:
        return device_service.register_device(db, payload)
    except DeviceAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "",
    response_model=list[DeviceRead],
    summary="List devices",
)
def list_all_devices(
    db: DbSession,
    node_type: DeviceType | None = None,
    device_status: Annotated[DeviceStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Device]:
    """List devices ordered by id, optionally filtered by type and status."""
    return device_service.list_devices(
        db,
        node_type=node_type,
        status=device_status,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{node_id}",
    response_model=DeviceRead,
    summary="Get one device",
)
def read_device(node_id: str, db: DbSession) -> Device:
    """Return one device by node_id (case-insensitive). Returns 404 if unknown."""
    try:
        return device_service.get_device(db, node_id)
    except DeviceNotFoundError as exc:
        raise _not_found(exc) from exc


@router.patch(
    "/{node_id}",
    response_model=DeviceRead,
    summary="Update a device",
)
def edit_device(node_id: str, payload: DeviceUpdate, db: DbSession) -> Device:
    """Change a device's name and/or status. node_id and node_type are immutable."""
    try:
        return device_service.update_device(db, node_id, payload)
    except DeviceNotFoundError as exc:
        raise _not_found(exc) from exc


@router.delete(
    "/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a device",
)
def remove_device(node_id: str, db: DbSession) -> None:
    """Delete a device. Returns 404 if unknown."""
    try:
        device_service.delete_device(db, node_id)
    except DeviceNotFoundError as exc:
        raise _not_found(exc) from exc