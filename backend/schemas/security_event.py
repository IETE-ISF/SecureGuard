"""
security_event.py - Pydantic schemas for security events (Phase 6, Task 1).

SecurityEventPayload  - validates the "d" object of a door/tamper "event" packet
SecurityEventRead     - API response for one stored event

Event packet contract (d object, at least one key):
    door    "open" | "closed"
    tamper  "triggered" | "safe"
"""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from backend.models.security_event import EventSeverity, SecurityEventType


class SecurityEventPayload(BaseModel):
    """
    The payload of a door/tamper "event" packet.

    Strict: values must be exactly the allowed words (lowercase text) and
    unknown keys are rejected, so a firmware typo fails loudly.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    door: Literal["open", "closed"] | None = None
    tamper: Literal["triggered", "safe"] | None = None

    @model_validator(mode="after")
    def _require_a_state(self) -> Self:
        if self.door is None and self.tamper is None:
            raise ValueError("an event payload must contain 'door' and/or 'tamper'")
        return self

    @property
    def event_types(self) -> list[SecurityEventType]:
        """The events this payload reports: door first, then tamper."""
        events: list[SecurityEventType] = []
        if self.door == "open":
            events.append(SecurityEventType.DOOR_OPEN)
        elif self.door == "closed":
            events.append(SecurityEventType.DOOR_CLOSED)
        if self.tamper == "triggered":
            events.append(SecurityEventType.TAMPER_DETECTED)
        elif self.tamper == "safe":
            events.append(SecurityEventType.TAMPER_CLEARED)
        return events


class SecurityEventRead(BaseModel):
    """A stored security event as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int | None
    node_id: str | None
    recorded_at: datetime
    event_type: SecurityEventType
    severity: EventSeverity
    description: str
    acknowledged_at: datetime | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def acknowledged(self) -> bool:
        """True once an operator has acknowledged the event."""
        return self.acknowledged_at is not None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        """True for a warning or critical event that has not been acknowledged."""
        return self.severity != EventSeverity.INFO and self.acknowledged_at is None
