"""
security_event.py - Security event ORM model (Phase 6, Task 1).

One SecurityEvent row is stored per door, tamper or maintenance event.

* Door and tamper events come from "event" packets sent by a node.
* Maintenance events are created by an operator through the API and have
  no device.
* acknowledged_at is empty until an operator acknowledges the event. An
  "active alert" is a non-info event that has not been acknowledged.
* device_id is set to NULL when the device is deleted: the security
  history is kept even if the device record is removed.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.base import Base, UTCDateTime
from backend.models.device import Device


class SecurityEventType(StrEnum):
    """What happened."""

    DOOR_OPEN = "door_open"
    DOOR_CLOSED = "door_closed"
    TAMPER_DETECTED = "tamper_detected"
    TAMPER_CLEARED = "tamper_cleared"
    MAINTENANCE_STARTED = "maintenance_started"
    MAINTENANCE_ENDED = "maintenance_ended"


class EventSeverity(StrEnum):
    """How much attention an event deserves."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


DEFAULT_SEVERITY: dict[SecurityEventType, EventSeverity] = {
    SecurityEventType.DOOR_OPEN: EventSeverity.WARNING,
    SecurityEventType.DOOR_CLOSED: EventSeverity.INFO,
    SecurityEventType.TAMPER_DETECTED: EventSeverity.CRITICAL,
    SecurityEventType.TAMPER_CLEARED: EventSeverity.INFO,
    SecurityEventType.MAINTENANCE_STARTED: EventSeverity.INFO,
    SecurityEventType.MAINTENANCE_ENDED: EventSeverity.INFO,
}


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    """Store enum values ('door_open'), not member names ('DOOR_OPEN')."""
    return [member.value for member in enum_class]


class SecurityEvent(Base):
    """A door, tamper or maintenance event."""

    __tablename__ = "security_events"
    __table_args__ = (
        Index("ix_security_events_recorded_at", "recorded_at"),
        Index("ix_security_events_device_id_recorded_at", "device_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )
    recorded_at: Mapped[datetime]
    event_type: Mapped[SecurityEventType] = mapped_column(
        Enum(
            SecurityEventType,
            native_enum=False,
            length=30,
            values_callable=_enum_values,
            validate_strings=True,
        )
    )
    severity: Mapped[EventSeverity] = mapped_column(
        Enum(
            EventSeverity,
            native_enum=False,
            length=20,
            values_callable=_enum_values,
            validate_strings=True,
        )
    )
    description: Mapped[str] = mapped_column(String(200))
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    device: Mapped[Device | None] = relationship(lazy="joined")

    @property
    def node_id(self) -> str | None:
        """The node_id of the device that raised the event, if it still exists."""
        return self.device.node_id if self.device is not None else None

    @property
    def acknowledged(self) -> bool:
        """True once an operator has acknowledged the event."""
        return self.acknowledged_at is not None

    @property
    def is_active_alert(self) -> bool:
        """True for a warning or critical event that nobody has acknowledged."""
        return self.severity != EventSeverity.INFO and not self.acknowledged

    def __repr__(self) -> str:
        return f"<SecurityEvent {self.event_type} severity={self.severity} at={self.recorded_at}>"
