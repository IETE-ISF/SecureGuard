"""
power.py - Power monitoring ORM model (Phase 5, Task 1).

One PowerReading row is stored per "data" packet from a power node.

Units:
    voltage        volts (V)
    current        amperes (A)
    power          watts (W), active power
    energy         kilowatt-hours (kWh), cumulative meter reading
    power_factor   0 to 1 (dimensionless), optional

recorded_at is the Raspberry Pi's arrival time in UTC; the ESP32 has no
real clock. energy is the meter's running total, so energy used over a
period is the difference between readings, which the analytics layer
computes (the meter itself wraps at 9999.99 kWh).
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base


class PowerReading(Base):
    """One sample from a power monitoring node."""

    __tablename__ = "power_readings"
    __table_args__ = (
        Index("ix_power_readings_device_id_recorded_at", "device_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    recorded_at: Mapped[datetime]

    voltage: Mapped[float]
    current: Mapped[float]
    power: Mapped[float]
    energy: Mapped[float]
    power_factor: Mapped[float | None]

    def __repr__(self) -> str:
        return f"<PowerReading device_id={self.device_id} at={self.recorded_at}>"