"""
device.py - Pydantic schemas for the device registry (Phase 2, Task 2).

DeviceCreate  - body of POST /devices (register a device)
DeviceUpdate  - body of PATCH /devices/{node_id} (rename or change status)
DeviceRead    - response model, built directly from the Device ORM object
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.models.device import DeviceStatus, DeviceType

NODE_ID_PATTERN = r"^[A-Z0-9_]{3,50}$"


class DeviceCreate(BaseModel):
    """Payload for registering a new device."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    node_id: str = Field(
        pattern=NODE_ID_PATTERN,
        description="Unique identifier, uppercase letters, digits and underscores (e.g. WATER_01).",
        examples=["WATER_01"],
    )
    node_name: str = Field(min_length=1, max_length=100, examples=["Water Monitoring Node"])
    node_type: DeviceType

    @field_validator("node_id", mode="before")
    @classmethod
    def _normalise_node_id(cls, value: object) -> object:
        """Trim whitespace and uppercase so 'water_01' and 'WATER_01' are the same device."""
        if isinstance(value, str):
            return value.strip().upper()
        return value


class DeviceUpdate(BaseModel):
    """Payload for editing a device. Only the fields provided are changed."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    node_name: str | None = Field(default=None, min_length=1, max_length=100)
    status: DeviceStatus | None = None

    @model_validator(mode="after")
    def _require_real_changes(self) -> "DeviceUpdate":
        """Reject empty updates and explicit nulls."""
        provided = {name: getattr(self, name) for name in self.model_fields_set}
        if not provided:
            raise ValueError("At least one field (node_name, status) must be provided")
        if any(value is None for value in provided.values()):
            raise ValueError("Fields cannot be set to null")
        return self


class DeviceRead(BaseModel):
    """A device as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    node_id: str
    node_name: str
    node_type: DeviceType
    status: DeviceStatus
    last_seen: datetime | None
    created_at: datetime
    updated_at: datetime