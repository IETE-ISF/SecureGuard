"""
water.py - Pydantic schemas for water monitoring (Phase 4, Task 2).

WaterPayload      - validates the "d" object of a water-node data packet
WaterReadingRead  - API response for one stored reading

Units: flow in L/min, temperatures in degrees C, humidity in % and
pressure in hPa (BME280 ambient pressure).
"""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

# Plausibility limits, set by what the sensors can actually report.
FLOW_MAX_LPM = 1000.0
TEMP_MIN_C = -55.0  # DS18B20 operating range
TEMP_MAX_C = 125.0
HUMIDITY_MAX_PERCENT = 100.0
PRESSURE_MIN_HPA = 300.0  # BME280 range
PRESSURE_MAX_HPA = 1100.0

Flow = Annotated[float, Field(ge=0, le=FLOW_MAX_LPM, allow_inf_nan=False)]
Temperature = Annotated[float, Field(ge=TEMP_MIN_C, le=TEMP_MAX_C, allow_inf_nan=False)]
Humidity = Annotated[float, Field(ge=0, le=HUMIDITY_MAX_PERCENT, allow_inf_nan=False)]
Pressure = Annotated[
    float, Field(ge=PRESSURE_MIN_HPA, le=PRESSURE_MAX_HPA, allow_inf_nan=False)
]


class WaterPayload(BaseModel):
    """
    The payload of a water-node "data" packet.

    Strict: numbers must be real numbers (not text, not booleans) and
    unknown keys are rejected, so a firmware typo fails loudly.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    flow_in: Flow
    flow_out: Flow
    temp_in: Temperature
    temp_out: Temperature
    humidity: Humidity | None = None
    pressure: Pressure | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _reject_booleans(cls, value: Any) -> Any:
        """JSON true/false must never be accepted as the numbers 1 and 0."""
        if isinstance(value, bool):
            raise ValueError("boolean values are not allowed")
        return value


class WaterReadingRead(BaseModel):
    """A stored water reading as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    recorded_at: datetime
    flow_in: float
    flow_out: float
    temp_in: float
    temp_out: float
    humidity: float | None
    pressure: float | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def consumption(self) -> float:
        """Water consumed in L/min (flow_in minus flow_out)."""
        return round(self.flow_in - self.flow_out, 3)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def delta_temp(self) -> float:
        """Temperature rise across the cooling loop in degrees C (outlet minus inlet)."""
        return round(self.temp_out - self.temp_in, 3)

class MetricStats(BaseModel):
    """Average, minimum and maximum of one metric over a time window."""

    avg: float | None
    min: float | None
    max: float | None


class WaterSummary(BaseModel):
    """Aggregates over a window of stored water readings."""

    node_id: str | None
    start: datetime
    end: datetime
    count: int
    first_recorded_at: datetime | None
    last_recorded_at: datetime | None
    flow_in: MetricStats
    flow_out: MetricStats
    consumption: MetricStats
    temp_in: MetricStats
    temp_out: MetricStats
    delta_temp: MetricStats
    humidity: MetricStats
    pressure: MetricStats
