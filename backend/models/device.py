"""
device.py - Device ORM model (Phase 2, Task 1).

A device is any node in the system: the master ESP32, the water node or
the power/security node.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base, TimestampMixin, UTCDateTime


class DeviceType(StrEnum):
    """Kinds of nodes in the SecureRack Guardian network."""

    MASTER = "master"
    WATER = "water"
    POWER = "power"


class DeviceStatus(StrEnum):
    """Lifecycle state of a device."""

    ONLINE = "online"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    """Store enum values ('water'), not member names ('WATER')."""
    return [member.value for member in enum_class]


class Device(TimestampMixin, Base):
    """A registered node, identified by a unique node_id such as WATER_01."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[str] = mapped_column(String(50), unique=True)
    node_name: Mapped[str] = mapped_column(String(100))
    node_type: Mapped[DeviceType] = mapped_column(
        Enum(
            DeviceType,
            native_enum=False,
            length=20,
            values_callable=_enum_values,
            validate_strings=True,
        )
    )
    status: Mapped[DeviceStatus] = mapped_column(
        Enum(
            DeviceStatus,
            native_enum=False,
            length=20,
            values_callable=_enum_values,
            validate_strings=True,
        ),
        default=DeviceStatus.OFFLINE,
    )
    last_seen: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<Device {self.node_id} type={self.node_type} status={self.status}>"