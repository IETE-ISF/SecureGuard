"""
water.py - Water monitoring ORM model (Phase 4, Task 1).

One WaterReading row is stored per "data" packet from a water node.

Units:
    flow_in, flow_out   litres per minute (L/min)
    temp_in, temp_out   degrees Celsius
    humidity            percent relative humidity
    pressure            hectopascals (hPa), ambient air pressure from the BME280

recorded_at is the Raspberry Pi's arrival time in UTC; the ESP32 has no
real clock. Values are stored exactly as measured: flow_out can briefly
exceed flow_in through sensor noise, and cleaning that up is left to the
analytics layer.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base


class WaterReading(Base):
    """One sample from a water monitoring node."""

    __tablename__ = "water_readings"
    __table_args__ = (
        Index("ix_water_readings_device_id_recorded_at", "device_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    recorded_at: Mapped[datetime]

    flow_in: Mapped[float]
    flow_out: Mapped[float]
    temp_in: Mapped[float]
    temp_out: Mapped[float]
    humidity: Mapped[float | None]
    pressure: Mapped[float | None]

    @property
    def consumption(self) -> float:
        """Water consumed in L/min: withdrawal minus discharge."""
        return self.flow_in - self.flow_out

    @property
    def delta_temp(self) -> float:
        """Temperature rise across the cooling loop in degrees C: outlet minus inlet."""
        return self.temp_out - self.temp_in

    def __repr__(self) -> str:
        return f"<WaterReading device_id={self.device_id} at={self.recorded_at}>"