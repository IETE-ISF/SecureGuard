"""
power.py - Pydantic schemas for power monitoring (Phase 5, Task 1).

PowerPayload      - validates the "d" object of a power-node data packet
PowerReadingRead  - API response for one stored reading

Units: V, A, W, kWh (cumulative meter reading) and power factor 0 to 1.
Limits follow the PZEM-004T meter.
"""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

VOLTAGE_MAX_V = 300.0
CURRENT_MAX_A = 100.0
POWER_MAX_W = 25_000.0
ENERGY_MAX_KWH = 9999.99

Voltage = Annotated[float, Field(ge=0, le=VOLTAGE_MAX_V, allow_inf_nan=False)]
Current = Annotated[float, Field(ge=0, le=CURRENT_MAX_A, allow_inf_nan=False)]
ActivePower = Annotated[float, Field(ge=0, le=POWER_MAX_W, allow_inf_nan=False)]
Energy = Annotated[float, Field(ge=0, le=ENERGY_MAX_KWH, allow_inf_nan=False)]
PowerFactor = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class PowerPayload(BaseModel):
    """
    The payload of a power-node "data" packet.

    Strict: numbers must be real numbers (not text, not booleans) and
    unknown keys are rejected, so a firmware typo fails loudly. Door and
    tamper changes are "event" packets, not part of this payload.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    voltage: Voltage
    current: Current
    power: ActivePower
    energy: Energy
    power_factor: PowerFactor | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _reject_booleans(cls, value: Any) -> Any:
        """JSON true/false must never be accepted as the numbers 1 and 0."""
        if isinstance(value, bool):
            raise ValueError("boolean values are not allowed")
        return value


class PowerReadingRead(BaseModel):
    """A stored power reading as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    recorded_at: datetime
    voltage: float
    current: float
    power: float
    energy: float
    power_factor: float | None