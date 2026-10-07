"""
device_service.py - Business logic for the device registry (Phase 2, Task 3).

Every function takes a SQLAlchemy Session, so the same code serves API
routes (Depends(get_db)) and background threads (session_scope()).
Functions commit their own changes and roll back on failure.
"""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.device import Device, DeviceStatus, DeviceType
from backend.schemas.device import DeviceCreate, DeviceUpdate

logger = logging.getLogger(__name__)


class DeviceError(Exception):
    """Base class for device service errors."""


class DeviceNotFoundError(DeviceError):
    """Raised when no device exists with the requested node_id."""

    def __init__(self, node_id: str) -> None:
        super().__init__(f"Device '{node_id}' not found")
        self.node_id = node_id


class DeviceAlreadyExistsError(DeviceError):
    """Raised when registering a node_id that is already taken."""

    def __init__(self, node_id: str) -> None:
        super().__init__(f"Device '{node_id}' already exists")
        self.node_id = node_id


def normalise_node_id(node_id: str) -> str:
    """Trim and uppercase a node_id so lookups are case-insensitive."""
    return node_id.strip().upper()


def find_device(db: Session, node_id: str) -> Device | None:
    """Return the device with this node_id, or None."""
    statement = select(Device).where(Device.node_id == normalise_node_id(node_id))
    return db.scalars(statement).first()


def get_device(db: Session, node_id: str) -> Device:
    """Return the device with this node_id or raise DeviceNotFoundError."""
    device = find_device(db, node_id)
    if device is None:
        raise DeviceNotFoundError(normalise_node_id(node_id))
    return device


def list_devices(
    db: Session,
    *,
    node_type: DeviceType | None = None,
    status: DeviceStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Device]:
    """Return devices ordered by id, optionally filtered by type and status."""
    statement = select(Device).order_by(Device.id)
    if node_type is not None:
        statement = statement.where(Device.node_type == node_type)
    if status is not None:
        statement = statement.where(Device.status == status)
    statement = statement.limit(limit).offset(offset)
    return list(db.scalars(statement).all())


def register_device(db: Session, data: DeviceCreate) -> Device:
    """Register a new device. Raises DeviceAlreadyExistsError on a duplicate node_id."""
    if find_device(db, data.node_id) is not None:
        raise DeviceAlreadyExistsError(data.node_id)

    device = Device(
        node_id=data.node_id,
        node_name=data.node_name,
        node_type=data.node_type,
    )
    db.add(device)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DeviceAlreadyExistsError(data.node_id) from exc

    db.refresh(device)
    logger.info("Device registered: %s (%s)", device.node_id, device.node_type)
    return device


def update_device(db: Session, node_id: str, data: DeviceUpdate) -> Device:
    """Apply the fields set on `data` (node_name, status) to an existing device."""
    device = get_device(db, node_id)

    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(device, field, value)

    db.commit()
    db.refresh(device)
    logger.info("Device updated: %s (%s)", device.node_id, ", ".join(sorted(changes)))
    return device


def delete_device(db: Session, node_id: str) -> None:
    """Delete a device. Raises DeviceNotFoundError if it does not exist."""
    device = get_device(db, node_id)
    db.delete(device)
    db.commit()
    logger.info("Device deleted: %s", device.node_id)